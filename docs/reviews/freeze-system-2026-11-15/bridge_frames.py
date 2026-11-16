"""python bridge_frames.py <tree> <dev> <out.npz>: the Aswan loop bridge frames (no line, no follow)."""
import os
import sys
from pathlib import Path

import numpy as np

tree, dev, out = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
os.environ.update(FONT_SIZE="80", RR_FIXTURE_REPO=str(dev))
sys.path.insert(0, str(tree))
sys.path.insert(0, str(tree / "docs/reviews/freeze-panel-2026-11-04/fixtures"))
import fixtures as fx
from PIL import Image

from src import end_card

case = [c for c in fx.load("endcard")][2]
last_src = case.get("last_frame", {}).get("from") or case.get("last")
last = (Image.open(fx.frame(last_src, last=True)) if str(last_src).endswith(".mp4") else Image.open(fx.path(last_src))).convert("RGB")
first = Image.open(fx.frame(case["first_frame"]["from"], 0.0)).convert("RGB")
frames = [np.asarray(f.convert("RGB")) for f in end_card.loop_bridge(last, first, case["share_line"], {})]
np.savez_compressed(out, *frames)
print(case["topic"], len(frames))
