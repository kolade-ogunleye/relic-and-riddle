"""The instrument over the whole pool: one record per photograph from the two shipped models' masks (GPU masks of
run_candidates.py; the module's CPU masks give the same boxes within a pixel), with the module's own box and
confidence functions."""
import json, sys
sys.path.insert(0, r"C:\tmp\qf-w-subject-c6")
from src import photo_subject as ps
from evalboxes import mask
ph = json.load(open("photos_all.json"))
out = {}
for o in ph:
    ms = [mask(m, o["sha256"]) for m in ("u2net", "isnet")]
    if any(m is None for m in ms): continue
    got = [ps.mask_box(m) for m in ms]
    bs = [b for b, f in got]
    if not all(bs):
        out[o["sha256"]] = {"name": "/".join(o["path"].split("\\")[-2:]), "box": None, "confidence": 0.0}; continue
    box = [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]
    conf = ps._iou(bs[0], bs[1]) * (1 - max(f["soft"] for b, f in got))
    out[o["sha256"]] = {"name": "/".join(o["path"].split("\\")[-2:]), "box": box, "confidence": round(conf, 4)}
json.dump(out, open("pool_records.json", "w"), indent=0)
n = len(out)
for t in (0.5, 0.7, 0.85, 0.9, 0.95):
    k = sum(1 for r in out.values() if r["box"] and r["confidence"] >= t)
    print(f"confidence >= {t}: {k} of {n} photographs ({k/n:.0%})")
