"""Probe: the YouTube thumbnail's masthead on the freeze-panel covers fixtures (real heroes and payloads).

    python probe_youtube.py <tree> <dev checkout> <out.json>
"""
import json
import os
import sys
from pathlib import Path

tree, dev, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3])
os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))
sys.path.insert(0, str(tree))
sys.path.insert(0, str(tree / "docs/reviews/freeze-panel-2026-11-04/fixtures"))
import fixtures as fx
from PIL import Image

from src import youtube_thumbnail as yt
from src.multi_format import platform_encode
from src.visual_engine import VisualEngine

captured = []
_orig = yt.render


def _wrapped(*a, **k):
    im, plates = _orig(*a, **k)
    captured.append((im, dict(plates).get("brand"), (k.get("meta") or {}).get("masthead")))
    return im, plates


yt.render = _wrapped
res = []
keep = out.parent / (out.stem + "_png")
keep.mkdir(parents=True, exist_ok=True)
for case in fx.load("covers"):
    hero = fx.path(case["hero"]["photo"])
    payload = fx.payload(case)
    d = out.parent / f"yt_{case['topic']}"
    p = VisualEngine().generate_youtube_thumbnail(hero, payload, d / "yt.jpg")
    im = Image.open(p).convert("RGB")
    lay = json.loads(Path(str(p) + ".layout.json").read_text(encoding="utf-8")) if Path(str(p) + ".layout.json").is_file() else {}
    im.crop((0, 0, 900, 220)).save(keep / f"{case['topic']}.png")
    im.resize((246, 138), Image.LANCZOS).save(keep / f"{case['topic']}_246.png")
    rim, plate, mrec = captured[-1]
    if plate is None:   # round 2: a masthead under its gate is not drawn (fail closed)
        res.append({"topic": case["topic"], "shown": False, "record": mrec})
    else:
        worst = None
        try:
            from src.masthead import worst_contrast
            worst = worst_contrast(platform_encode(im), plate)
        except ImportError:
            pass
        res.append({"topic": case["topic"], "fill": list(plate.fill), "contrast": plate.contrast, "halo": plate.halo,
                    "delivered_jpeg": plate.measure(im), "delivered_platform": plate.measure(platform_encode(im)),
                    "delivered_platform_worst_letter": worst, "record": mrec})
    print(json.dumps(res[-1]), flush=True)
out.write_text(json.dumps(res, indent=1), encoding="utf-8", newline="\n")
