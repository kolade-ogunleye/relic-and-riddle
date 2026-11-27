"""PLATES-12 (panel 6): inside one deck the copy band stands at no more than two positions: the content slides'
headings start at one of two cap lines (the deck frame's one-line and two-line positions, multi_format
_content_frame), and each kicker stands the one kicker gap (kicker_gap) above its heading's cap line.

Over every deck plates_draw.py drew (Instagram and Facebook): per content slide (the cover, the stat slide and the
closing debate slide are their own archetypes, excepted as the ledger's rule excepts them; a slide whose record
carries a ruled exemption, "kicker_line_exempt", is listed and excepted): the heading's top (its ink box) and the
kicker's gap (the heading's top less the kicker's ink bottom). Violations: a deck with more than two distinct heading
tops (TOLERANCE px apart), or a slide whose kicker gap differs from the deck's most common gap by more than
TOLERANCE.
"""
from __future__ import annotations

from collections import Counter

import plates_records as pr

TOLERANCE = 3
EXCEPT = ("cover", "colossal_stat", "debate")
a = pr.setup(__doc__)
rows, bad = [], []


def distinct(values: list[int]) -> list[int]:
    out: list[int] = []
    for v in sorted(values):
        if not out or v - out[-1] > TOLERANCE:
            out.append(v)
    return out


for run in pr.runs(a.drawn):
    for sub in pr.DECKS:
        slides = []
        for name, entry in pr.frames(run, sub):
            arch = pr.archetype(entry)
            meta = entry.get("meta") or {}
            els = {e["name"]: e for e in entry.get("elements") or []}
            h, k = els.get("heading"), els.get("kicker")
            if arch in EXCEPT or not h:
                continue
            gap = h["box"][1] - k["box"][3] if k else None
            slides.append({"frame": name[-6:-4], "archetype": arch, "top": h["box"][1], "gap": gap,
                           "exempt": meta.get("kicker_line_exempt")})
        if not slides:
            continue
        judged = [s for s in slides if not s["exempt"]]
        tops = distinct([s["top"] for s in judged])
        gaps = [s["gap"] for s in judged if s["gap"] is not None]
        common = Counter(gaps).most_common(1)[0][0] if gaps else None
        off = [s for s in judged if s["gap"] is not None and abs(s["gap"] - common) > TOLERANCE]
        ok = len(tops) <= 2 and not off
        row = {"run": run.name[4:], "deck": sub, "slides": slides, "tops": tops, "gap": common,
               "gap_off": [(s["frame"], s["gap"]) for s in off],
               "exempt": [s["frame"] for s in slides if s["exempt"]], "ok": ok}
        rows.append(row)
        if not ok:
            bad.append(row)
        print(f"{run.name[4:44]:40} {sub:11} tops {tops} gap {common} off {row['gap_off']} exempt {row['exempt']} "
              f"{'pass' if ok else 'FAIL'}", flush=True)

summary = {"decks": len(rows), "decks with <= 2 heading tops": sum(len(r["tops"]) <= 2 for r in rows),
           "decks with 3 or more": sum(len(r["tops"]) > 2 for r in rows),
           "slides off the deck's kicker gap": sum(len(r["gap_off"]) for r in rows),
           "slides on a ruled exemption": sum(len(r["exempt"]) for r in rows)}
pr.finish(a, "PLATES-12 two band positions per deck", rows, bad, summary)
