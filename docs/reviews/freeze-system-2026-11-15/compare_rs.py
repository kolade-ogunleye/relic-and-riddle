"""Compare render_surfaces results (base vs head): identical / differing per surface group, with pixel stats on the
kept last frames.  python compare_rs.py <scratch>"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

S = Path(sys.argv[1])
b = json.loads((S / "rs_base.json").read_text(encoding="utf-8"))
h = json.loads((S / "rs_head.json").read_text(encoding="utf-8"))
groups = defaultdict(lambda: {"same": 0, "diff": [], "only": []})
for k in sorted(set(b["surfaces"]) | set(h["surfaces"])):
    g = k.split("__")[0]
    if k not in b["surfaces"] or k not in h["surfaces"]:
        groups[g]["only"].append(k)
        continue
    if b["surfaces"][k] == h["surfaces"][k]:
        groups[g]["same"] += 1
        continue
    stat = {}
    pb, ph = S / "rs_base_png" / f"{k}.png", S / "rs_head_png" / f"{k}.png"
    if pb.is_file() and ph.is_file():
        A = np.asarray(Image.open(pb).convert("RGB"), dtype=np.int16)
        B = np.asarray(Image.open(ph).convert("RGB"), dtype=np.int16)
        if A.shape == B.shape:
            d = np.abs(A - B).max(axis=2)
            ys, xs = np.nonzero(d > 0)                     # round 3: the exact region, every differing pixel
            y2, x2 = np.nonzero(d > 2)
            stat = {"px_changed": int((d > 0).sum()), "px_changed_gt2": int((d > 2).sum()),
                    "box_any": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None,
                    "box_gt2": [int(x2.min()), int(y2.min()), int(x2.max()), int(y2.max())] if len(x2) else None,
                    "max": int(d.max())}
    groups[g]["diff"].append((k, stat))
print("errors base", sorted(b["errors"]), "head", sorted(h["errors"]))
for g, v in sorted(groups.items()):
    print(f"{g}: identical {v['same']}, differing {len(v['diff'])}, one side only {len(v['only'])}")
    for k, st in v["diff"]:
        print("   ", k, st)
