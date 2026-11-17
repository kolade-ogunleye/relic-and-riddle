"""Prints caps.py's results as Markdown tables: python caps_table.py (reads caps_{parent,row}_{dev,prod}.json beside
this file; writes nothing)."""
import json
import sys
from pathlib import Path

D = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
NAMES = {"parent": "parent 1c4533d8", "row": "this row"}
MODES = {"off": "off", "on_p0.5": "on, caps, pass 0.5", "on_p1.0": "on, caps, pass 1.0"}


def dist(d):
    return ", ".join(f"{k}: {v}" for k, v in d.items()) or "-"


def cell(r):
    rs = r["reasons"]
    stops = (f"{r.get('stopped_after_the_voice', 0)} of {r['plans']} (hold {rs.get('hold over 6 s', 0)}, repeat "
             f"{rs.get('repeat', 0)}, too few distinct {rs.get('too few distinct photos', 0)})")
    return stops, f"{r['distinct_min']} / {r['distinct_median']}", dist(r["crops_per_reel"]), \
        dist(r["illustrated_per_reel"]), dist(r["filled_by"])


for scenario in ("recorded", "each_beat"):
    for env, tag in (("dev", "dev"), ("production sample", "prod")):
        print(f"\n### {scenario}, {env}\n")
        print("| variant | illustration | stops after the voice (by reason) | distinct photos per Reel min / median "
              "| Reels by crops | Reels by illustrated beats | unshown beats filled by |")
        print("|---|---|---|---|---|---|---|")
        for v, name in NAMES.items():
            res = json.loads((D / f"caps_{v}_{tag}.json").read_text(encoding="utf-8"))["results"]
            for m, mode in MODES.items():
                r = res[f"{m}/{scenario}"]
                print(f"| {name} | {mode} | " + " | ".join(cell(r)) + " |")
