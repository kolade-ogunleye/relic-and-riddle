"""Scores SUBJECT-VISION answers against the labelled set with the SUBJECT stream's measure (cover < 0.85 = cut).

  python score.py                 answers_first.jsonl (+ answers_repeat.jsonl) in SV_SCRATCH
  python score.py --selftest      the scorer on stand-ins (no answers needed): the labels themselves must give zero
                                  cuts, IoU 1 and a perfect scene call; the SUBJECT stream's photo_subject records
                                  as the "vision" box must reproduce that instrument's own failure rate.

Prints: the evaluation table (boxed, IoU p25/p50/p75, cut overall and on the objects, panel-7 photographs), the
scene/object call against the labels, stability over the repeated 30 (box IoU between the two answers, scene call
agreement), the named hard cases one by one, the fusion search (vision box AND IoU with the photo_subject record above
a threshold, AND confidence above a threshold: cuts, coverage, panel-7 coverage) and the list to check by eye
(every cut or scene disagreement: the label may be the wrong one)."""
import itertools
import json
import sys

import numpy as np

from sv_common import (CUT, HARD, LABELS, SCRATCH, cover, iou, load_answers, panel7, photo_subject_records,
                       stability_ids)

BY_ID = {lab["id"]: lab for lab in LABELS}


def record(ans):
    """The validated subject record of one answer, or None (not looked at / not asked)."""
    look = (ans or {}).get("look") or {}
    return look.get("subject") if look.get("checked") else None


def table(name, preds):
    """preds: {id: (box or None, scene bool, confidence)}"""
    rows = [(BY_ID[i], p) for i, p in preds.items()]
    boxed = [(g, p) for g, p in rows if p[0]]
    ious = [iou(g["box"], p[0]) for g, p in boxed]
    cuts = [(g, p) for g, p in boxed if cover(g["box"], p[0]) < CUT]
    objs = [(g, p) for g, p in boxed if g["kind"] == "object"]
    p7 = [(g, p) for g, p in boxed if panel7(g)]
    q = np.percentile(ious, [25, 50, 75]) if ious else [0, 0, 0]
    print(f"| {name} | {len(rows)} | {len(boxed)} | {' / '.join('%.2f' % v for v in q)} | "
          f"{len(cuts)} ({len(cuts) / max(1, len(boxed)):.0%}) | {sum(1 for g, _ in cuts if g['kind'] == 'object')}"
          f" / {len(objs)} | {sum(1 for g, _ in cuts if panel7(g))} / {len(p7)} |")
    return cuts


def scene_call(preds):
    m = {(k, s): 0 for k in ("object", "scene") for s in (False, True)}
    for i, p in preds.items():
        m[(BY_ID[i]["kind"], bool(p[1]))] += 1
    print(f"scene call: label object -> said object {m[('object', False)]}, said scene {m[('object', True)]}; "
          f"label scene -> said scene {m[('scene', True)]}, said object {m[('scene', False)]}")


def stability(first, repeat):
    pairs = [(i, record(first.get(i)), record(repeat.get(i))) for i in stability_ids()]
    pairs = [(i, a, b) for i, a, b in pairs if a and b]
    if not pairs:
        print("stability: no repeated answers"); return
    both = [(i, a, b) for i, a, b in pairs if a["box"] and b["box"]]
    ious = [iou(a["box"], b["box"]) for _, a, b in both]
    flips = [i for i, a, b in pairs if a["scene"] != b["scene"]]
    cutflip = [i for i, a, b in both if (cover(BY_ID[i]["box"], a["box"]) < CUT) != (cover(BY_ID[i]["box"], b["box"]) < CUT)]
    print(f"stability ({len(pairs)} repeated): both boxed {len(both)}, IoU between the answers p10/p50 = "
          f"{np.percentile(ious, 10):.2f} / {np.median(ious):.2f}, >= 0.8: {sum(v >= 0.8 for v in ious)}; "
          f"scene call flips {len(flips)} {flips}; cut/not-cut flips {len(cutflip)} {cutflip}")


def hard_cases(preds, names):
    print("| case | label | vision box | cover | IoU | scene | said | not the subject |")
    print("|---|---|---|---|---|---|---|---|")
    for i, what in HARD.items():
        g, p = BY_ID[i], preds.get(i)
        if not p:
            print(f"| {what} ({i}) | {g['kind']} {g['box']} | no answer | | | | | |"); continue
        b = p[0]
        c = f"{cover(g['box'], b):.2f}" if b else "-"
        u = f"{iou(g['box'], b):.2f}" if b else "-"
        n = names.get(i, ("", []))
        print(f"| {what} ({i}) | {g['kind']} | {[round(v, 2) for v in b] if b else n[2] if len(n) > 2 else '-'} | {c} | {u} | "
              f"{p[1]} | {n[0]} | {'; '.join(x['name'] for x in n[1])} |")


def fusion(preds):
    """Accept a vision box when its confidence >= c and its IoU with the photo_subject record's box >= t; the box
    used is the vision box (v) or the union with the record's box (u). Zero-cut rules first, by coverage."""
    rec = photo_subject_records()
    res = []
    for mode, c, t in itertools.product(("v", "u"), (0.0, 0.5, 0.7, 0.8, 0.9), (0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9)):
        acc, cuts = [], []
        for i, p in preds.items():
            g = BY_ID[i]
            r = rec.get(g["sha256"])
            if not p[0] or (p[2] or 0) < c:
                continue
            if t > 0 and not (r and r.get("box") and iou(p[0], r["box"]) >= t):
                continue
            box = p[0] if mode == "v" or not (r and r.get("box")) else \
                [min(p[0][0], r["box"][0]), min(p[0][1], r["box"][1]), max(p[0][2], r["box"][2]), max(p[0][3], r["box"][3])]
            acc.append(g)
            if cover(g["box"], box) < CUT:
                cuts.append(g["id"])
        big = sum(1 for g in acc if (g["box"][2] - g["box"][0]) * (g["box"][3] - g["box"][1]) >= 0.7)
        res.append((len(cuts), -len(acc), mode, c, t, big, sum(1 for g in acc if panel7(g)), cuts))
    print("| box | conf >= | IoU(record) >= | accepted | of them labelled >= 70 % of frame | panel-7 | cuts |")
    print("|---|---|---|---|---|---|---|")
    for r in sorted(res)[:14]:
        print(f"| {r[2]} | {r[3]} | {r[4]} | {-r[1]} | {r[5]} | {r[6]} | {r[0]} {' '.join(r[7][:8])} |")
    zero = [r for r in res if r[0] == 0 and r[1] < 0]
    best = min(zero, key=lambda r: r[1]) if zero else None
    print("zero-cut rule with the most coverage:", f"box {best[2]}, conf >= {best[3]}, IoU >= {best[4]}: "
          f"{-best[1]} of {len(preds)} accepted, panel-7 {best[6]}" if best else "none")


def to_check(preds, names):
    print("to check by eye (cut, or scene call against the label): id, photo, label kind, said, cover")
    for i, p in sorted(preds.items()):
        g = BY_ID[i]
        c = cover(g["box"], p[0]) if p[0] else None
        if (c is not None and c < CUT) or (g["kind"] == "scene") != bool(p[1]):
            print(f"  {i} {g['name']:50s} {g['kind']:6s} scene={p[1]!s:5s} cover={'-' if c is None else '%.2f' % c} "
                  f"{names.get(i, ('',))[0]}")


def from_answers(answers):
    preds, names = {}, {}
    for i, ans in answers.items():
        r = record(ans)
        if r is None:
            continue
        preds[i] = (r["box"] or None, r["scene"], r["confidence"])
        names[i] = (r["name"], r["not_subject"], r["problem"] or "-")
    return preds, names


def selftest():
    oracle = {g["id"]: (g["box"], g["kind"] == "scene", 1.0) for g in LABELS}
    cuts = table("oracle (the labels)", oracle)
    assert not cuts
    rec = photo_subject_records()
    stand = {g["id"]: ((rec.get(g["sha256"]) or {}).get("box"), False, (rec.get(g["sha256"]) or {}).get("confidence", 0))
             for g in LABELS}
    table("stand-in: photo_subject record (U2+IS union)", stand)
    scene_call(oracle)
    fusion(stand)
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    first, repeat = load_answers(SCRATCH / "answers_first.jsonl"), load_answers(SCRATCH / "answers_repeat.jsonl")
    if not first:
        raise SystemExit(f"no answers in {SCRATCH}: nothing was asked (ask.py --send first)")
    preds, names = from_answers(first)
    invalid = {i: n[2] for i, n in names.items() if n[2] != "-"}
    print(f"answers {len(first)}, looked at {len(preds)}, subject record cannot stand {len(invalid)}: {invalid}")
    print("| candidate | answered | boxed | IoU p25 / p50 / p75 | cut > 15 % (all) | cut (objects) | cut (panel-7) |")
    print("|---|---|---|---|---|---|---|")
    table("vision subject box", preds)
    for c in (0.5, 0.7, 0.9):
        table(f"vision, confidence >= {c}", {i: p for i, p in preds.items() if (p[2] or 0) >= c})
    scene_call(preds)
    stability(first, repeat)
    hard_cases(preds, names)
    fusion(preds)
    to_check(preds, names)


if __name__ == "__main__":
    main()
