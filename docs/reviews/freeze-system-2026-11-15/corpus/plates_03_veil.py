"""PLATES-03 (panel 6): a text band over a bright, coloured ground leaves no grey or brown veil: under the band the
photograph keeps its chroma (the ledger's check: saturation >= 0.6 of the unscrimmed ground's at equal luma; no
neutral-grey plateau).

Over every photograph of every dev run's topic, each band as its surface lays it (the tree's own call, canonical
geometry), read in the band's full-strength interior on pixels whose ground is saturated (HSV >= 0.25) and lit
(luma >= 60):
- carousel cover / immersive band (multi_format.scrim, target 0.26 from y 900 of 1440 x 1800), interior y 1000-1800;
- Story band (multi_format.scrim, target 0.30, y 700-1100 of 1080 x 1920), interior y 720-1080;
- Reel cover type scrim (cover_design._scrim, box 137-824 x 1100-1500), interior x 237-724 x 1150-1450.
Per band: the 5th percentile of the saturation kept (after / before, HSV) and the share of those pixels turned to a
neutral grey (saturation under 0.08). Violation: 5th percentile under 0.6, or a grey share over 1 %.
"""
from __future__ import annotations

import plates_common as pc

a, tree, dev = pc.setup(__doc__)

import numpy as np  # noqa: E402

from src import cover_design, multi_format  # noqa: E402
from src.photo_grade import cover  # noqa: E402

KEEP, GREY = 0.6, 0.01
rows, bad = [], []


def measure(before, after, box):
    x0, y0, x1, y1 = box
    b = np.asarray(before.convert("RGB"), np.float32)[y0:y1:2, x0:x1:2]
    f = np.asarray(after.convert("RGB"), np.float32)[y0:y1:2, x0:x1:2]
    _, sb = pc._hue_sat(b)
    _, sa = pc._hue_sat(f)
    sel = (sb >= 0.25) & (b @ pc.LUMA >= 60)
    if sel.sum() < 200:
        return None
    ratio = sa[sel] / np.maximum(sb[sel], 1e-3)
    return round(float(np.percentile(ratio, 5)), 3), round(float((sa[sel] < 0.08).mean()), 4), int(sel.sum())


for topic, n, photo in pc.corpus_photos(dev, a.per_topic):
    car = cover(photo, (1440, 1800))
    tall = cover(photo, (1080, 1920))
    bands = (("carousel band", car, multi_format.scrim(car, 900, 1800, feather=400, target=0.26), (0, 1000, 1440, 1800)),
             ("story band", tall, multi_format.scrim(tall, 700, 1100, feather=320, target=0.30), (0, 720, 1080, 1080)),
             ("reel cover scrim", tall, cover_design._scrim(tall, (137, 1100, 824, 1500), cover_design.MONUMENT_SCRIM,
                                                            cover_design.MONUMENT_SCRIM_FEATHER),
              (237, 1150, 724, 1450)))
    for name, before, after, box in bands:
        got = measure(before, after, box)
        if got is None:
            continue
        ok = got[0] >= KEEP and got[1] <= GREY
        row = {"topic": topic, "photo": photo.name, "band": name, "saturation_kept_p5": got[0], "grey_share": got[1],
               "px": got[2], "ok": ok}
        rows.append(row)
        if not ok:
            bad.append(row)
        print(f"{topic[:28]:28} {photo.name:16} {name:16} kept p5 {got[0]:.3f} grey {got[1]:.4f} "
              f"{'pass' if ok else 'FAIL'}", flush=True)

summary = {}
for name in ("carousel band", "story band", "reel cover scrim"):
    sel = [r for r in rows if r["band"] == name]
    if sel:
        summary[name] = {"grounds": len(sel), "fail": sum(not r["ok"] for r in sel),
                         "kept p5 min": min(r["saturation_kept_p5"] for r in sel),
                         "kept p5 median": float(np.median([r["saturation_kept_p5"] for r in sel])),
                         "grey share max": max(r["grey_share"] for r in sel)}
pc.finish(a, "PLATES-03 no veil under a text band", rows, bad, summary)
