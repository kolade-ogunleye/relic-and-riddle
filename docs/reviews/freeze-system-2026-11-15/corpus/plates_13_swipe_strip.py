"""PLATES-13 (panel 6): slide 1's part of the Instagram swipe strip lands on a designed line: it clears slide 1's
text by the ledger's 48 canvas px (24 half px) or is not drawn (recorded, lay.meta["swipe_strip"]["skipped"]), and
its part stays inside the profile grid's 3:4 crop (it starts left of the crop's right edge).

Over every Instagram deck plates_draw.py drew: slide 1's strip record, the gap from the strip's left end to the right
edge of each text element (ink box; the heading's box is its widest line, so the gap to any line is at least this)
whose rows come within the clearance of the strip's rows, and the strip's start against the 3:4 crop's right edge.
"""
from __future__ import annotations

import plates_records as pr

CLEAR = 48          # canvas px at 1440 (the ledger's 24 half px)
a = pr.setup(__doc__)
rows, bad = [], []

for run in pr.runs(a.drawn):
    fr = dict(pr.frames(run, "carousel"))
    e = fr.get("carousel_slide_01.jpg")
    if not e:
        continue
    w, h = e.get("size") or [1440, 1800]
    clear = round(CLEAR * w / 1440)
    crop_right = w - round((w - h * 3 / 4) / 2)
    s = (e.get("meta") or {}).get("swipe_strip") or {}
    row = {"run": run.name[4:], "strip": s}
    if s.get("box"):
        b = s["box"]
        near = [(el["name"], b[0] - el["box"][2]) for el in e.get("elements") or []
                if el["name"] != "header_brand" and el["box"][1] < b[3] + clear and el["box"][3] > b[1] - clear]
        gap = min((g for _, g in near), default=None)
        ok = (gap is None or gap >= clear) and b[0] < crop_right
        row.update(gap=gap, near=near, inside_grid=b[0] < crop_right, ok=ok)
        why = f"drawn, gap {gap} px, starts at x {b[0]} (grid crop ends {crop_right})"
    else:
        ok = bool(s.get("skipped")) or not s
        row.update(ok=ok)
        why = f"not drawn: {s.get('skipped') or 'no strip (no kicker rule on slide 2)'}"
    rows.append(row)
    if not ok:
        bad.append(row)
    print(f"{run.name[4:44]:40} {'pass' if ok else 'FAIL'} {why}", flush=True)

drawn = [r for r in rows if r["strip"].get("box")]
summary = {"Instagram covers": len(rows), "strip drawn": len(drawn),
           "smallest gap to text (px)": min((r["gap"] for r in drawn if r.get("gap") is not None), default=None),
           f"drawn under {CLEAR} px": sum(1 for r in drawn if r.get("gap") is not None and r["gap"] < CLEAR),
           "skipped (recorded)": sum(1 for r in rows if r["strip"].get("skipped"))}
pr.finish(a, "PLATES-13 the swipe strip clears slide 1's text", rows, bad, summary)
