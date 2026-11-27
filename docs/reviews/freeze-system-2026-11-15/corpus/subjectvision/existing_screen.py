"""ZERO-COST: what the picture check ALREADY says, from the pool screen's cached looks (.picture_text.json, made by
past runs; no call). It names no subject box, but it does box intrusions along an edge (edge_object / object_box) and
printed text (text_box): regions that are NOT the subject. Measured here: for each labelled photograph with a cached
look, the share of the labelled subject that those regions cover (0 = they keep off the subject, as they should).
A large share means the existing look already calls part of the subject foreign: the half of the question that the
prototype's not_subject field extends."""
from sv_common import LABELS, area, inter, screen_verdict, yxyx

rows, cached = [], 0
for lab in LABELS:
    v = screen_verdict(lab)
    if not v:
        continue
    cached += 1
    for kind, name, box in (("object", v.get("object"), v.get("object_box")), ("text", v.get("reason") if v.get("text") else None, v.get("box"))):
        if not name:
            continue
        b = yxyx(box)
        share = inter(lab["box"], b) / max(1e-9, area(lab["box"])) if b else None
        rows.append((lab["id"], lab["name"], lab["kind"], kind, str(name)[:60], b, share))
print(f"labelled photographs with a cached look: {cached} of {len(LABELS)}")
print(f"{'id':4s} {'photo':48s} {'kind':6s} {'region':6s} {'subject share under it':>22s}  what")
for r in rows:
    s = "no box" if r[6] is None else f"{r[6]:.2f}"
    print(f"{r[0]:4s} {r[1]:48s} {r[2]:6s} {r[3]:6s} {s:>22s}  {r[4]}")
boxed = [r for r in rows if r[6] is not None]
print(f"regions: {len(rows)} ({sum(r[3]=='object' for r in rows)} intrusions, {sum(r[3]=='text' for r in rows)} text); "
      f"boxed {len(boxed)}; covering > 15 % of the labelled subject: {sum(r[6] > 0.15 for r in boxed)}")
