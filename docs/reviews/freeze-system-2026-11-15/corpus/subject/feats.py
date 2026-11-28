import json, sys
import numpy as np
from evalboxes import *
GT = json.load(open(HERE / "gt.json"))
models = sys.argv[1:]
rows = []
for g in GT:
    r = {"id": g["id"], "name": g["name"], "kind": g["kind"]}
    boxes = {}
    for m in models:
        mk = mask(m, g["sha256"])
        if mk is None: break
        b, f = seg_box(mk, 0.5)
        boxes[m] = b; r[m] = f
    else:
        u = union(*boxes.values())
        ious = [iou(boxes[a], boxes[b]) for i, a in enumerate(models) for b in models[i+1:] if boxes[a] and boxes[b]]
        r["agree"] = min(ious) if ious else 0
        r["box"] = u; r["area"] = area(u) if u else 0
        r["cov"] = cover(g["box"], u) if u else 0
        r["iou"] = iou(g["box"], u) if u else 0
        r["edges"] = sum([u[0] < 0.02, u[1] < 0.02, u[2] > 0.98, u[3] > 0.98]) if u else 4
        rows.append(r)
json.dump(rows, open(HERE / "feats.json", "w"))
for r in sorted(rows, key=lambda r: -r["agree"]):
    f = r[models[0]]
    print(f"{r['id']} {r['kind'][:3]} agree={r['agree']:.2f} area={r['area']:.2f} edges={r['edges']} soft={f.get('soft',0):.2f} ncomp={f.get('ncomp',0)} "
          f"cov={r['cov']:.2f} iou={r['iou']:.2f} {'CUT' if r['cov']<0.85 else ''} {r['name'][:40]}")
