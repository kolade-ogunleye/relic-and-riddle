"""Evidence renders for docs/reviews/story-credits-2026-10-29: two Story frames drawn by one version of the real
Story renderer (MultiFormatGenerator._render_story_frame) from development photos, no model calls:
  A  Derinkuyu photo 18 (Wolfgang Moroder, CC BY-SA 3.0, artist field a nine-sentence notice)
  B  Antikythera photo 15 (Gary Todd, CC0)
The copy on the frames is evidence copy written for this review, not a run's script.

usage: python render_evidence.py <code_root> <label> <out_dir> <repo_root>
Writes <label>_<frame>.jpg at half size (540x960) and <label>_measurements.json."""
import json
import sys
from pathlib import Path

code_root, label, out_dir, repo = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
sys.path.insert(0, str(code_root))
from PIL import Image  # noqa: E402

from src import multi_format  # noqa: E402
from src.multi_format import MultiFormatGenerator  # noqa: E402

FRAMES = {
    "A_derinkuyu_cc_by_sa": (repo / "assets" / "derinkuyu_underground_city" / "18_commons.jpg",
                             {"kicker": "EIGHTEEN STOREYS DOWN", "line1": "A CITY", "line2": "UNDER THE FIELDS",
                              "body": "Stone doors a metre wide rolled shut from the inside, sealing whole families below."}),
    "B_antikythera_cc0": (repo / "assets" / "antikythera_mechanism" / "15_commons.jpg",
                          {"kicker": "FROM THE WRECK", "line1": "MARBLE", "line2": "FROM THE SEA",
                           "body": "Divers raised statues from the same wreck that held the bronze gears."}),
}


def bright_end_contrast(im, box, text_rgb=(225, 225, 225)):
    import numpy as np
    region = np.asarray(im.convert("RGB").crop(tuple(box)), dtype=np.float32) / 255.0
    lin = np.where(region <= 0.03928, region / 12.92, ((region + 0.055) / 1.055) ** 2.4)
    bg = float(np.percentile(lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), 90))
    t = [v / 255.0 for v in text_rgb]
    lt = sum(w * (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4) for w, c in zip((0.2126, 0.7152, 0.0722), t))
    return round((lt + 0.05) / (bg + 0.05), 2)


out_dir.mkdir(parents=True, exist_ok=True)
report = {}
for name, (photo, slide) in FRAMES.items():
    im, lay = MultiFormatGenerator()._render_story_frame(photo, slide, 1, 3, {})
    im.resize((540, 960), Image.LANCZOS).save(out_dir / f"{label}_{name}.jpg", quality=88)
    credit = (lay.meta or {}).get("photo_credit")
    entry = multi_format.photo_credit(photo)
    report[name] = {
        "photo": str(photo.relative_to(repo)), "license": entry.get("license"),
        "artist_field_chars": len(str(entry.get("artist") or "")),
        "credit_record": credit,                         # None before: the frame recorded no credit
        "elements": lay.elements, "problems": lay.problems(), "safe_box": lay.meta.get("safe_box"),
        # the foot of the Story box, measured the same way before and after (where the credit goes)
        "contrast_at_credit_band": bright_end_contrast(im, (75, 1094, 1005, 1244)),
    }
(out_dir / f"{label}_measurements.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: {"credit": (v["credit_record"] or {}).get("drawn"), "problems": v["problems"]} for k, v in report.items()},
                 ensure_ascii=False, indent=1))
