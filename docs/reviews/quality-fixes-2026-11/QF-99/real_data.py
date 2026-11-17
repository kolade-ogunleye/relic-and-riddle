"""QF-99 real-data snapshot: the recorded model calls of the tasks whose every request asks for a light thinking level
on the text path, from the development runs' llm_calls.jsonl (counts, seconds and tokens only).

    python real_data.py <development app root>   ->  writes real_data.json beside this file

Tasks (their effort in src/): trace_check, trace_check_act and evidence_check ("medium", free_llm.call on the
checker); beat_queries, beat_gap_queries and music_mood ("low"). These requests went out with no thinking level
before QF-99, so the model thought at its default (high). Each call's latency and output tokens (thinking included)
are the "before" figures; the "after" ones need the same requests sent again (operator decision, README).
"""
import json
import statistics
import sys
from pathlib import Path

TASKS = {"trace_check": "medium", "trace_check_act": "medium", "evidence_check": "medium",
         "beat_queries": "low", "beat_gap_queries": "low", "music_mood": "low"}

root = Path(sys.argv[1])
calls = {t: [] for t in TASKS}
runs = 0
for log in sorted(root.glob("output/run_*/llm_calls.jsonl")):
    runs += 1
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("task") in calls and r.get("outcome") == "ok":
            calls[r["task"]].append(r)


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))] if values else None


out = {"runs_with_call_logs": runs, "tasks": {}}
for task, rows in calls.items():
    secs = [float(r.get("seconds") or 0) for r in rows]
    toks = [int(r.get("tokens_out") or 0) for r in rows]
    out["tasks"][task] = {"effort_asked": TASKS[task], "ok_calls": len(rows),
                          "median_seconds": round(statistics.median(secs), 2) if secs else None,
                          "p90_seconds": round(pct(secs, 0.9), 2) if secs else None,
                          "median_tokens_out": statistics.median(toks) if toks else None,
                          "total_tokens_out": sum(toks)}
out["light_calls_total"] = sum(v["ok_calls"] for v in out["tasks"].values())
out["light_seconds_total"] = round(sum(float(r.get("seconds") or 0) for rows in calls.values() for r in rows), 1)
(Path(__file__).resolve().parent / "real_data.json").write_bytes((json.dumps(out, indent=1) + "\n").encode())
print(json.dumps(out, indent=1))
