"""QF-74 real data: how long real runs' production took, per attempt, and per budgeted stage.

    python real_data.py <repo root> --dev <output dir> --dev-db <COPY of the development database>
                        [--prod <COPY of the production sample's output dir>]

Reads only: <output dir>/run_*/timings.json, the runs table of a database COPY opened read-only (sqlite URI
mode=ro; never through PipelineDatabase), and an optional copy of the production sample. The stage mapping is the
committed data/stage_budget.json of <repo root> (the spec's budgets; the base has no such file, so it is read from
this commit's tree via the path given).

An attempt is one production pass: a run's timings restart at script_and_fact_check each time it is made or
resumed (a resume appends to the same timings.json, often hours later). An attempt's production minutes run from
its first timed step's start (the first attempt: from the run's creation, which includes topic and photo
selection) to its last timed step's end, the review proxy before the review card; review waits are after it.

Prints JSON; writes nothing. Development rows carry no run names; production results are counts and medians only
(decisions PROD-SAMPLE-counts-only).
"""

import argparse
import json
import re
import sqlite3
import statistics
from datetime import UTC, datetime
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("root")
ap.add_argument("--dev", required=True)
ap.add_argument("--dev-db", required=True)
ap.add_argument("--prod")
args = ap.parse_args()
policy = json.loads((Path(args.root) / "data" / "stage_budget.json").read_text(encoding="utf-8"))


def ts(text):
    moment = datetime.fromisoformat(str(text))
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


def per_run(rows, created=None):
    rows = sorted(rows, key=lambda r: ts(r["at"]) - float(r["seconds"]))
    attempts, current = [], []
    for row in rows:
        if row["stage"] == "script_and_fact_check" and current:
            attempts.append(current)
            current = []
        current.append(row)
    if current:
        attempts.append(current)
    minutes = []
    for i, attempt in enumerate(attempts):
        start = min(ts(r["at"]) - float(r["seconds"]) for r in attempt)
        if i == 0 and created is not None and created <= start:
            start = created
        minutes.append(round((max(ts(r["at"]) for r in attempt) - start) / 60, 1))
    stages = {}
    for row in rows:
        group = policy["stages"].get(re.sub(r"\d+$", "", row["stage"]))
        if group:
            stages[group] = round(stages.get(group, 0) + float(row["seconds"]) / 60, 2)
    return {"attempt_minutes": minutes, "longest_step_minutes": round(max(float(r["seconds"]) for r in rows) / 60, 1),
            "stage_minutes": stages}


def collect(output, created_at=None):
    found = []
    for path in sorted(Path(output).glob("run_*/timings.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rows, list) and rows:
            created = (created_at or {}).get(path.parent.name)
            found.append(per_run(rows, ts(created) if created else None))
    return found


con = sqlite3.connect(f"file:{Path(args.dev_db).resolve().as_posix()}?mode=ro", uri=True)
created = dict(con.execute("SELECT run_id, created_at FROM runs").fetchall())
con.close()
dev = collect(args.dev, created)
dev.sort(key=lambda r: max(r["attempt_minutes"]))
out = {"dev": {"run_folders": len(list(Path(args.dev).glob("run_*"))), "runs_with_timings": len(dev),
               "runs_with_creation_time": sum(1 for p in Path(args.dev).glob("run_*/timings.json")
                                               if p.parent.name in created),
               "rows": dev}}
if args.prod:
    prod = collect(args.prod)
    attempts = [m for r in prod for m in r["attempt_minutes"]]
    limit = float(policy["warning_multiplier"])
    out["prod"] = {
        "runs_with_timings": len(prod), "attempts": len(attempts),
        "median_attempt_minutes": round(statistics.median(attempts), 1),
        "attempts_over_minutes": {str(m): sum(1 for a in attempts if a > m) for m in (90, 120, 150, 180, 240)},
        "steps_over_minutes": {str(m): sum(1 for r in prod if r["longest_step_minutes"] > m) for m in (30, 60)},
        "stages": {group: {"runs": sum(1 for r in prod if group in r["stage_minutes"]),
                           "median_minutes": round(statistics.median([r["stage_minutes"][group] for r in prod
                                                                      if group in r["stage_minutes"]]), 1),
                           "over_150_percent": sum(1 for r in prod
                                                   if r["stage_minutes"].get(group, 0) > budget * limit)}
                   for group, budget in policy["minutes"].items() if any(group in r["stage_minutes"] for r in prod)},
    }
print(json.dumps(out, indent=1))
