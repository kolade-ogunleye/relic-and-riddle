import json, sys
import numpy as np
from evalboxes import *
GT = json.load(open(HERE / "gt.json"))
name = sys.argv[1]
D = json.load(open(HERE / "raw" / name / "dets.json"))
def det_box(sha, mode="top", rel=0.6):
    d = D.get(sha)
    if not d or not d["dets"]: return None, 0.0
    top = d["dets"][0]
    if mode == "top": return top["box"], top["score"]
    keep = [x["box"] for x in d["dets"] if x["score"] >= rel * top["score"]]
    return union(*keep), top["score"]
for mode in ("top", "union"):
    for smin in (0.0, 0.2, 0.3, 0.4, 0.5):
        rows = []
        for g in GT:
            b, s = det_box(g["sha256"], mode)
            if b is None or s < smin: continue
            rows.append((g, b, s))
        cuts = [r for r in rows if cover(r[0]["box"], r[1]) < 0.85]
        ious = [iou(g["box"], b) for g, b, s in rows]
        print(f"{name} {mode:5s} score>={smin}: boxed {len(rows)} cut {len(cuts)} IoU med {np.median(ious) if ious else 0:.2f}  "
              + " ".join(g["id"] for g, b, s in cuts)[:200])
