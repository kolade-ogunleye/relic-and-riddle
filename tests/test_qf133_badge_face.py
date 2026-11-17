"""QF-133: the AI-reconstruction badge draws in the bundled file it is measured in (IBM Plex Mono at 600), not in a
system fallback face.

The badge asked libass for "IBM Plex Mono", the typographic family (name ID 16) of the bundled files. libass matches
a Fontname against a file's legacy family (ID 1), full name (ID 4) and PostScript name (ID 6) only, and the bundled
files are "IBM Plex Mono SmBld" and "IBM Plex Mono Medm" there, so libass drew the badge in a system face (Arial Bold
on Windows), wider than the Plex the plate is measured in: the label ran 32-35 px past its plate.

The invariants are checked through libass's own resolution, not by name: the face libass reports for the badge is
exactly one bundled file, that file's SHA-256 is the licence record's for the file the badge is measured in, the same
badge drawn from a folder holding only that file is pixel-identical, and the drawn label's ink stays inside its plate
in both formats.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fontTools.ttLib import TTFont
from PIL import Image

from src import video_composer as vc
from src.config import FONTS_DIR

WORDS = [{"word": w, "start": 0.2 + i * 0.4, "dur": 0.35}
         for i, w in enumerate(["The", "ring", "is", "forty", "kilometres", "wide."])]


def _esc(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:")


def _draw(ass: Path, fonts: Path, png: Path, width: int, height: int, background: str) -> str:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-v", "debug", "-y", "-f", "lavfi", "-i",
                             f"color=c={background}:s={width}x{height}:r=30", "-vf",
                             f"setpts=PTS+1.0/TB,subtitles='{_esc(ass)}':fontsdir='{_esc(fonts)}'",
                             "-frames:v", "1", "-threads", "1", str(png)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                            check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stderr


def _only(text: str, style: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("Dialogue:") or f",{style}," in ln) + "\n"


def _bbox(mask: np.ndarray) -> list[int]:
    ys, xs = np.nonzero(mask)
    assert len(xs), "nothing drawn"
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def _postscript_files(fonts: Path) -> dict[str, list[Path]]:
    files: dict[str, list[Path]] = {}
    for path in sorted(fonts.glob("*.ttf")) + sorted(fonts.glob("*.otf")):
        with TTFont(path, lazy=True) as font:
            files.setdefault(font["name"].getDebugName(6), []).append(path)
    return files


@pytest.mark.parametrize("width,height", [(1080, 1920), (1920, 1080)])
def test_libass_draws_the_badge_from_the_bundled_file_it_is_measured_in_and_inside_its_plate(width, height, tmp_path):
    from src import brand
    from src.typography import font_file

    ass = vc.VideoComposer().build_subtitles(WORDS, tmp_path / "badge.ass", width=width, height=height,
                                             reconstruction_intervals=[(0.0, 3.0)])
    text = ass.read_text(encoding="utf-8")
    fontname = next(ln for ln in text.splitlines() if ln.startswith("Style: ReconBadge,")).split(",")[1]
    (tmp_path / "text.ass").write_text(_only(text, "ReconBadge"), encoding="utf-8", newline="\n")
    (tmp_path / "plate.ass").write_text(_only(text, "ReconPlate"), encoding="utf-8", newline="\n")

    # 1. the face libass resolves for the badge is the bundled file the badge is measured in (by hash)
    log = _draw(tmp_path / "text.ass", FONTS_DIR, tmp_path / "text.png", width, height, "black")
    picks = re.findall(r"fontselect: \(([^,]+), (\d+), \d+\) -> [^,]+, \d+, (\S+)", log)
    badge = [ps for name, _weight, ps in picks if name == fontname]
    assert badge, f"libass logged no font selection for the badge's {fontname!r}: {picks}"
    files = {f for ps in badge for f in _postscript_files(FONTS_DIR).get(ps, [])}
    assert len(files) == 1, f"libass drew the badge in {sorted(set(badge))}, which is no single bundled file"
    drawn = files.pop()
    measured = FONTS_DIR / font_file(brand.BADGE_FONT, 600)
    evidence = {e["file"]: e["sha256"]
                for e in json.loads((FONTS_DIR / "license_evidence.json").read_text(encoding="utf-8"))["fonts"]}
    sha = hashlib.sha256(drawn.read_bytes()).hexdigest()
    assert sha == hashlib.sha256(measured.read_bytes()).hexdigest() == evidence[measured.name]

    # ... and that file alone draws the same badge, pixel for pixel
    solo = tmp_path / "solo"
    solo.mkdir()
    shutil.copyfile(drawn, solo / drawn.name)
    _draw(tmp_path / "text.ass", solo, tmp_path / "solo.png", width, height, "black")
    assert np.array_equal(np.asarray(Image.open(tmp_path / "solo.png")), np.asarray(Image.open(tmp_path / "text.png")))

    # 2. the label's ink stays inside its plate (0 px overrun on every side)
    _draw(tmp_path / "plate.ass", FONTS_DIR, tmp_path / "plate.png", width, height, "white")
    ink = _bbox(np.asarray(Image.open(tmp_path / "text.png").convert("L")) > 40)
    plate = _bbox(np.asarray(Image.open(tmp_path / "plate.png").convert("L")) < 250)
    assert plate[0] < ink[0] and plate[1] < ink[1] and ink[2] < plate[2] and ink[3] < plate[3], (ink, plate)


def test_the_badge_fontname_names_its_measured_file_and_no_other():
    """Without a renderer: the badge's Fontname is one of the names libass matches in the bundled file the badge is
    measured in (weight 600), and in no other bundled font, so no other face can answer it."""
    from src import brand
    from src.typography import font_file

    measured = FONTS_DIR / font_file(brand.BADGE_FONT, 600)
    name = vc._badge_family().strip().lower()
    assert name in vc._libass_face(measured)[0]
    others = [p for p in sorted(FONTS_DIR.glob("*.ttf")) if p.resolve() != measured.resolve()]
    assert not [p.name for p in others if (face := vc._libass_face(p)) and name in face[0]]
    assert vc.libass_font_problems(FONTS_DIR, [(vc._badge_family(), 700)]) == []
