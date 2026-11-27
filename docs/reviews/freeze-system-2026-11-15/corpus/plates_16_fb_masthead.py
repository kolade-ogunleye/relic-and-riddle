"""PLATES-16 (panel 6): the Facebook carousel's interiors carry no masthead BY RULE (the collage carries the brand
on its cover tile: multi_format, recorded `fb_interior` on the frame's masthead record), while every Instagram
slide and both covers carry it; the rule is a documented format variant, not a drop.

Over every deck plates_draw.py drew: per slide, the masthead record (meta "masthead"). A Facebook interior that
draws a masthead, or carries no masthead record, or is withheld with no rule named; an Instagram slide or a cover
whose masthead is withheld with no gate record (PLATES-07's ledger): each is a violation.
"""
from __future__ import annotations

import plates_records as pr

a = pr.setup(__doc__)
rows, bad = [], []

for run in pr.runs(a.drawn):
    for sub in pr.DECKS:
        for name, entry in pr.frames(run, sub):
            m = (entry.get("meta") or {}).get("masthead")
            idx = int((entry.get("recipe") or {}).get("index", int(name[-6:-4]) - 1))
            interior = sub == "carousel_fb" and idx > 0
            by_rule = bool(m and (m.get("fb_interior") or m.get("by_rule")))
            shown = bool(m and m.get("shown"))
            if interior:
                ok = bool(m) and not shown and by_rule
                why = "by rule (fb_interior)" if ok else "FB INTERIOR: masthead drawn or not on the rule"
            else:
                ok = bool(m) and (shown or m.get("below_gate") or m.get("reason") is not None)
                why = "drawn" if shown else "withheld by the gate, recorded" if ok else "NO RECORD"
            row = {"run": run.name[4:], "deck": sub, "frame": name, "interior": interior, "shown": shown,
                   "by_rule": by_rule, "ok": ok, "why": why}
            rows.append(row)
            if not ok:
                bad.append(row)
            print(f"{run.name[4:44]:40} {sub:11} {name[-6:-4]} {'interior' if interior else 'IG/cover':8} "
                  f"{'pass' if ok else 'FAIL'} {why}", flush=True)

fb_int = [r for r in rows if r["interior"]]
summary = {"slides": len(rows), "Facebook interiors": len(fb_int),
           "Facebook interiors without a masthead, by rule": sum(r["ok"] for r in fb_int),
           "Facebook interiors drawing one": sum(r["shown"] for r in fb_int),
           "other slides drawn": sum(r["shown"] for r in rows if not r["interior"]),
           "other slides withheld (gate, recorded)": sum(r["ok"] and not r["shown"] for r in rows if not r["interior"])}
pr.finish(a, "PLATES-16 Facebook interiors carry no masthead by rule", rows, bad, summary)
