"""Fusion: open-vocabulary detection (topic + caption prompt) x salient segmentation (u2net+isnet union).
Accept when the detector's union box and the segmentation's union box agree; box = union of both."""
import json, itertools
from evalboxes import *
GT = json.load(open(HERE / "gt.json"))
D = json.load(open(HERE / "raw/gdino/dets.json"))
def dino(sha, rel=0.6):
    d = D.get(sha)
    if not d or not d["dets"]: return None, 0
    top = d["dets"][0]["score"]
    return union(*[x["box"] for x in d["dets"] if x["score"] >= rel * top]), top
rows = []
for g in GT:
    bs = [seg_box(mask(m, g["sha256"]))[0] for m in ("u2net", "isnet")]
    sb = union(*bs) if all(bs) else None
    db, sc = dino(g["sha256"])
    rows.append(dict(g=g, sb=sb, db=db, sc=sc, ag=iou(sb, db) if sb and db else 0,
                     a=area(union(sb, db)) if sb and db else 1))
print("score  agree  amax   boxed  cuts  ids")
res = []
for smin, amin, amax in itertools.product((0.3, 0.4, 0.5, 0.6), (0.5, 0.6, 0.7, 0.8, 0.9), (0.6, 0.8, 1.01)):
    acc = [r for r in rows if r["sc"] >= smin and r["ag"] >= amin and r["a"] <= amax]
    cuts = [r for r in acc if cover(r["g"]["box"], union(r["sb"], r["db"])) < 0.85]
    res.append((len(cuts), -len(acc), smin, amin, amax, [r["g"]["id"] for r in cuts]))
for x in sorted(res)[:12]:
    print(f"{x[2]:5.1f} {x[3]:6.1f} {x[4]:5.2f} {-x[1]:7d} {x[0]:5d}  {' '.join(x[5])}")
json.dump([{"id": r["g"]["id"], "sb": r["sb"], "db": r["db"], "score": r["sc"], "agree": r["ag"], "area": r["a"],
            "cover": cover(r["g"]["box"], union(r["sb"], r["db"])) if r["sb"] and r["db"] else None} for r in rows],
          open(HERE / "fusion_rows.json", "w"))
