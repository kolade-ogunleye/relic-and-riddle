"""Builds the bundled fallback faces (QF-94) from pinned Noto releases and records their licensing evidence.

    python scripts/build_fallback_fonts.py

A field the brand face cannot draw whole is set in one of these faces (src/font_engine.field_face), so no letter
is ever dropped. Operator ruling 2026-11-10: Noto subsets for Greek, Cyrillic and extended Latin, SIL Open Font
License 1.1. Each upstream file is fetched from the google/fonts repository at a pinned commit, checked against
its recorded SHA-256, cut to the Latin, Greek and Cyrillic blocks (layout features, names and variation kept;
hinting removed, the renderers rasterise unhinted) and its weight axis limited to 400-900 (no design sets
lighter text). The serif's width axis is pinned to normal (Cinzel, the serif it stands in for, is a wide face).
The Noto licence reserves no font name, so the subsets keep their names. Writes fonts/<file>, the licence notice
fonts/licenses/<file>.OFL.txt (LF) and the file's record in fonts/license_evidence.json.
"""
import hashlib
import io
import json
import sys
from pathlib import Path

import requests
from fontTools import subset
from fontTools import version as fonttools_version
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import FONTS_DIR
from src.credits import sha256_file
from src.font_evidence import embedded_names

COMMIT = "8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5"     # google/fonts, 2026-03-12 (Noto Sans / Serif 2.015)
RAW = f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/ofl"
# Basic Latin, Latin-1, Latin Extended-A/B, IPA and spacing modifiers, combining diacritics, Greek and Coptic,
# Cyrillic and its supplement and extended-C, Latin Extended Additional (Vietnamese), Greek Extended (polytonic),
# general punctuation, super/subscripts, currency, letterlike symbols, number forms, arrows, minus, Latin
# Extended-C/D and the Latin ligatures.
UNICODES = ("U+0020-007E,U+00A0-024F,U+0250-02FF,U+0300-036F,U+0370-03FF,U+0400-052F,U+1C80-1C8F,U+1E00-1FFF,"
            "U+2000-206F,U+2070-20CF,U+2100-218F,U+2190-2199,U+2212,U+2C60-2C7F,U+A720-A7FF,U+FB00-FB06")
FACES = (
    {"file": "NotoSans-LGC.ttf", "upstream": "notosans/NotoSans%5Bwdth,wght%5D.ttf",
     "upstream_sha256": "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d",
     "license": "notosans/OFL.txt", "axes": {"wght": (400, 900)}},
    {"file": "NotoSerif-LGC.ttf", "upstream": "notoserif/NotoSerif%5Bwdth,wght%5D.ttf",
     "upstream_sha256": "4d8e6761424656867019081a1a01336f3cb086982682698714054fc33f782713",
     "license": "notoserif/OFL.txt", "axes": {"wght": (400, 900), "wdth": 100}},
)


def _fetch(url: str) -> bytes:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def build(face: dict) -> dict:
    data = _fetch(f"{RAW}/{face['upstream']}")
    if hashlib.sha256(data).hexdigest() != face["upstream_sha256"]:
        raise ValueError(f"{face['upstream']}: upstream bytes differ from the pinned SHA-256")
    font = TTFont(io.BytesIO(data), recalcTimestamp=False)
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.hinting = False
    options.notdef_outline = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=subset.parse_unicodes(UNICODES))
    subsetter.subset(font)
    font = instancer.instantiateVariableFont(font, face["axes"])
    out = FONTS_DIR / face["file"]
    font.save(out)
    notice_text = _fetch(f"{RAW}/{face['license']}").decode("utf-8")
    header = notice_text.split("SIL OPEN FONT LICENSE", 1)[0]
    if "SIL OPEN FONT LICENSE" not in notice_text or "Reserved Font Name" in header:
        raise ValueError("Unexpected font licence document (or a Reserved Font Name the subset would have to drop)")
    notice = FONTS_DIR / "licenses" / f"{face['file']}.OFL.txt"
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.write_text(notice_text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    axes = {tag: list(v) if isinstance(v, tuple) else v for tag, v in face["axes"].items()}
    return {"file": face["file"], "sha256": sha256_file(out), **embedded_names(out),
            "license_file": notice.relative_to(FONTS_DIR).as_posix(), "license_sha256": sha256_file(notice),
            "source_url": f"{RAW}/{face['upstream']}",
            "release": (f"google/fonts {COMMIT} (upstream sha256 {face['upstream_sha256']}); subset with fontTools "
                        f"{fonttools_version} to {UNICODES}; hinting removed; axes limited to {axes}; no Reserved "
                        "Font Name"),
            "role": "fallback face (QF-94)", "checked_on": "2026-11-10"}


def main():
    evidence_path = FONTS_DIR / "license_evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    records = [build(face) for face in FACES]
    names = {r["file"] for r in records}
    evidence["fonts"] = [r for r in evidence["fonts"] if r["file"] not in names] + records
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8",
                             newline="\n")
    for r in records:
        print(r["file"], (FONTS_DIR / r["file"]).stat().st_size, "bytes", r["sha256"])


if __name__ == "__main__":
    main()
