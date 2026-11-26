"""PLATES-04 (panel 6): a kicker's treatment (colour, bone, INK type or the held plate: the overlay-label step) is
decided once per set: every drawn kicker of one Instagram deck, one Facebook deck, one Story's frames takes the
set's one step (cycle 0 round 8 R1, multi_format.deck_kicker_step / kicker_set); a kicker that cannot take it on its
own ground keeps its own step only on record (its record's "set_broken"), and a kicker not drawn says why.

Over every set plates_draw.py drew: the distinct steps of the set's drawn kickers (frame meta "kickers"), the set's
recorded step (meta "kicker_set"), every set_broken and every withheld kicker. A set with more than one step and no
set_broken record, or a withheld kicker with no reason, is a violation. The cover's eyebrow (the Reel cover) is one
element per set and cannot differ from itself; it is listed for the record.
"""
from __future__ import annotations

import plates_records as pr

a = pr.setup(__doc__)
rows, bad = [], []

for run in pr.runs(a.drawn):
    for sub in (*pr.DECKS, "stories"):
        fr = pr.frames(run, sub)
        if not fr:
            continue
        steps, broken, withheld, planned = {}, [], [], set()
        for name, entry in fr:
            meta = entry.get("meta") or {}
            if meta.get("kicker_set"):
                planned.add(str((meta["kicker_set"] or {}).get("step")))
            for kname, k in (meta.get("kickers") or {}).items():
                if not k.get("shown"):
                    withheld.append((name, kname, k.get("reason")))
                    continue
                st = tuple(k.get("step") or ()) or (k.get("variant"),)
                steps.setdefault(st, []).append(name[-6:-4])
                if k.get("set_broken") is not None:
                    broken.append((name, kname, k.get("set_broken")))
        distinct = len(steps)
        ok = (distinct <= 1 or len(broken) >= distinct - 1) and all(r for _, _, r in withheld)
        row = {"run": run.name[4:], "set": sub, "frames": len(fr), "steps": {str(k): v for k, v in steps.items()},
               "distinct": distinct, "planned": sorted(planned), "set_broken": broken, "withheld": withheld, "ok": ok}
        rows.append(row)
        if not ok:
            bad.append(row)
        print(f"{run.name[4:44]:40} {sub:11} frames {len(fr)} steps {row['steps']} broken {len(broken)} "
              f"withheld {len(withheld)} {'pass' if ok else 'FAIL'}", flush=True)

summary = {"sets": len(rows), "sets with one step": sum(r["distinct"] <= 1 for r in rows),
           "sets with more than one step": sum(r["distinct"] > 1 for r in rows),
           "kickers off the set's step on record (set_broken)": sum(len(r["set_broken"]) for r in rows),
           "kickers withheld": sum(len(r["withheld"]) for r in rows),
           "steps seen": sorted({s for r in rows for s in r["steps"]})}
pr.finish(a, "PLATES-04 one kicker treatment per set", rows, bad, summary)
