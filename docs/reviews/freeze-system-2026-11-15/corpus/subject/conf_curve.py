import json, sys
from evalboxes import *
GT = json.load(open(HERE / "gt.json"))
models = sys.argv[1:] or ["u2net", "isnet"]
rows = []
for g in GT:
    bs, soft = [], []
    for m in models:
        b, f = seg_box(mask(m, g["sha256"]))
        bs.append(b); soft.append(f.get("soft", 1))
    if not all(bs): rows.append((0, g, None)); continue
    agree = min(iou(a, b) for i, a in enumerate(bs) for b in bs[i+1:])
    rows.append((agree * (1 - max(soft)), g, union(*bs)))
print("threshold  boxed  cuts  (objects cut)  cut ids")
for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95):
    acc = [(c, g, b) for c, g, b in rows if c >= t]
    cuts = [(c, g, b) for c, g, b in acc if cover(g["box"], b) < 0.85]
    print(f"{t:9.2f} {len(acc):6d} {len(cuts):5d} {sum(1 for c,g,b in cuts if g['kind']=='object'):6d}   "
          + ", ".join(f"{g['id']} {g['name'].split('/')[0][:10]}/{g['name'].split('/')[1][:2]} ({c:.2f})" for c, g, b in cuts))
