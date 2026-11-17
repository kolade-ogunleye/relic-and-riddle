"""QF-94b: where libass draws a caption with characters the caption face lacks, against the plate the measure sizes.

    python docs/reviews/quality-fixes-2026-11/QF-94/caption_plate_overflow.py <repository root>

Runs on the base and on the row's commit (it calls only VideoComposer.build_subtitles and caption_phrases, which both
trees have). For each synthetic caption below it builds the tree's own ASS (9:16 and 16:9), renders one frame with
ffmpeg's libass (`subtitles=...:fontsdir=<root>/fonts`, as compose_video does; on an RGB picture, so no chroma subsampling
bleeds the colours) on a pure red ground, so ink that runs
past the plate stays visible, and reads from the frame:
- the plate's width (its dark pixels in the middle rows, clear of the rounded corners);
- the ink's extent (bone and gold pixels anywhere in the plate's rows, inside the plate or past it);
- how far the ink runs past the plate on each side (positive: past it; negative: the pad left inside).
From the same render, `ffmpeg -v debug` logs libass's font selection: each glyph the requested face lacks ("Glyph 0x...
not found") and the face libass then takes ("fontselect: (Anton, 700, 0) -> <face>"), listed per caption.

Synthetic words only (no run data). Prints JSON; writes nothing outside a temporary folder it removes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CAPTIONS = ["Αθήνα wrote", "Αγία Σοφία", "Москва 1941", "Γνώθι σαυτόν", "the Παρθενών temple", "Κωνσταντινούπολη",
            "Counterrevolutionaries", "Ibn ابن wrote"]
FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}


def esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def measure_frame(png: Path) -> dict:
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    r, g = a[..., 0], a[..., 1]
    plate = (r < 120) & (g < 60)             # the plate (INK) over the red ground; ink is bright, the ground pure red
    ys, _ = np.nonzero(plate)
    if not len(ys):
        return {"plate": None}
    top, bottom = int(ys.min()), int(ys.max())
    band = plate[top + 16:bottom - 15]
    cols = np.nonzero(band.any(0))[0]
    pl, pr = int(cols.min()), int(cols.max()) + 1
    ink = g[max(0, top - 40):bottom + 41] > 110      # bone and gold: inside the plate or past it
    icols = np.nonzero(ink.any(0))[0]
    il, ir = int(icols.min()), int(icols.max()) + 1
    return {"plate_w": pr - pl, "plate_x": [pl, pr], "ink_x": [il, ir], "ink_w": ir - il,
            "past_plate_left": pl - il, "past_plate_right": ir - pr, "frame_w": int(a.shape[1])}


def main(root: Path) -> None:
    root = root.resolve()
    os.environ.setdefault("FONT_SIZE", "80")
    sys.path.insert(0, str(root))
    from src.video_composer import VideoComposer, caption_phrases
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not on PATH")
    out: dict = {}
    tmp = Path(tempfile.mkdtemp(prefix="qf94b-captions-"))
    try:
        for fmt, (w, h) in FORMATS.items():
            rows = out.setdefault(fmt, {})
            for k, text in enumerate(CAPTIONS):
                words = [{"word": x, "start": 0.1 + 0.3 * i, "dur": 0.25} for i, x in enumerate(text.split())]
                n = len(caption_phrases(words))
                ass = VideoComposer().build_subtitles(words, tmp / f"c{k}.ass", width=w, height=h)
                png = tmp / f"f{k}.png"
                at = words[-1]["start"] + 0.2
                vf = f"format=rgb24,setpts=PTS+{at:.3f}/TB,subtitles='{esc(ass)}':fontsdir='{esc(root / 'fonts')}'"
                run = subprocess.run(["ffmpeg", "-v", "debug", "-y", "-f", "lavfi", "-i", f"color=c=0xFF0000:s={w}x{h}:r=30",
                                      "-vf", vf, "-frames:v", "1", str(png)],
                                     capture_output=True, check=False, text=True, encoding="utf-8", errors="replace", timeout=180)
                if run.returncode:
                    raise SystemExit(f"ffmpeg failed on caption {k} ({fmt})")
                log = run.stderr
                missing = sorted({m.upper() for m in re.findall(r"Glyph 0x([0-9A-Fa-f]+) not found", log)})
                chosen = sorted({f"{a} -> {b}" for a, b in re.findall(r"fontselect: \(([^)]*)\) -> ([^,\s]+)", log)})
                rows[text] = {"phrases": n, **measure_frame(png), "glyphs_not_in_face": len(missing),
                              "fontselect": chosen}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
