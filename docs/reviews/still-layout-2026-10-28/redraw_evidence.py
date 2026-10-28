"""The tighter fit, as the audit draws it when the look reports a cut-off or an overlap: redraw in place from
the kept recipe, then save a half-size copy. usage: python redraw.py <code_root> <work_dir> <out_dir>"""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from PIL import Image  # noqa: E402

from src import still_layout  # noqa: E402

work, out = Path(sys.argv[2]), Path(sys.argv[3])
rows = {}
for rel in ("richat_structure_sahara_eye/carousel/carousel_slide_01.jpg", "richat_structure_sahara_eye_cover/thumbnail_cover.jpg",
            "antikythera_mechanism/stories/story_02_reveal.jpg", "antikythera_mechanism/stories/story_03_question.jpg"):
    p = work / rel
    entry = still_layout.redraw(p)
    rows[rel] = {"problems": entry["problems"], "tight": entry["tight"], "attempts": entry["attempts"],
                 "shortened": entry["shortened"], "elements": {e["name"]: e["box"] for e in entry["elements"]}}
    im = Image.open(p).convert("RGB")
    dst = out / (rel.split("/")[0].split("_")[0] + "_" + p.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(dst, quality=85)
    print(rel, entry["problems"], entry["shortened"])
(out / "measurements.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
