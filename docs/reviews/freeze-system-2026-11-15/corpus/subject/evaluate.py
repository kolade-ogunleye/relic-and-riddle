"""Per candidate: IoU distribution and the failure that matters (predicted box misses or cuts > 15 % of the labelled
subject) on the builder's labelled set."""
import json, sys
import numpy as np
from evalboxes import *
GT = json.load(open(HERE / "gt.json"))
CUT = 0.85

def seg_pred(model, sha, t=0.5, margin=0.0):
    m = mask(model, sha)
    if m is None: return None, None
    b, f = seg_box(m, t)
    return (grow(b, margin) if b else None), f

def report(name, preds):
    """preds: {id: box or None}"""
    rows = []
    for g in GT:
        p = preds.get(g["id"], "missing")
        if p == "missing": continue
        rows.append((g, p))
    n = len(rows)
    boxed = [(g, p) for g, p in rows if p]
    ious = [iou(g["box"], p) for g, p in boxed]
    cuts = [(g, p) for g, p in boxed if cover(g["box"], p) < CUT]
    obj = [(g, p) for g, p in boxed if g["kind"] == "object"]
    q = np.percentile(ious, [10, 25, 50, 75, 90]) if ious else [0]*5
    print(f"{name:34s} n={n:3d} boxed={len(boxed):3d} IoU p10/25/50/75/90={' '.join('%.2f'%v for v in q)} "
          f"cut>15%={len(cuts):3d} ({len(cuts)/max(1,len(boxed)):.0%}) obj_cut={sum(1 for g,p in cuts if g['kind']=='object')}/{len(obj)}")
    return cuts

if __name__ == "__main__":
    for model in sys.argv[1:]:
        for t in (0.3, 0.5):
            for mg in (0.0, 0.05, 0.10):
                preds = {}
                for g in GT:
                    b, f = seg_pred(model, g["sha256"], t, mg)
                    if f is not None: preds[g["id"]] = b
                report(f"{model} t={t} margin={mg}", preds)
