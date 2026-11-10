"""Writes call_durations.json (the committed input of measure.py) from a checkout's run records.

Usage: python extract_durations.py <checkout with output/run_*/llm_calls.jsonl and logs/pipeline.log*> <out json>

Each Gemini call in output/run_*/llm_calls.jsonl keeps only its task, model, seconds and outcome. A call's line holds
the whole call (all its free attempts, waits and any billed try), so the free-model retry lines of logs/pipeline.log*
(local time) that fall inside the call's window mark it `retried` and are kept in order as `events`. A call that was not retried is one request: its
seconds are that request's time to answer. Reads only; writes the one output file (LF).
"""
import glob
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

RETRY = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \w+\s+src\.free_llm: (Free model (\S+) (busy|timed out|"
                   r"connection dropped)|Free Gemini unavailable|Billed Gemini call failed)")


def main(root: Path, out: Path) -> None:
    events = []
    for log in sorted(glob.glob(str(root / "logs" / "pipeline.log*"))):
        for line in Path(log).read_text(encoding="utf-8", errors="replace").splitlines():
            m = RETRY.match(line)
            if m:   # local wall-clock time, converted with this machine's zone rules (DST included)
                at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").astimezone(UTC)
                kind = {"busy": "503", "timed out": "timeout", "connection dropped": "dropped"}.get(m.group(4) or "",
                                                                                                "billed")
                events.append((at, m.group(3) or "", kind))
    calls, days = [], set()
    records = sorted(glob.glob(str(root / "output" / "run_*" / "llm_calls.jsonl")))
    for path in records:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            days.add(r["at"][:10])
            if not r.get("model", "").startswith("gemini"):
                continue
            end = datetime.fromisoformat(r["at"])
            start = end - timedelta(seconds=r["seconds"])
            seen = [kind for at, model, kind in events
                    if start - timedelta(seconds=1) <= at <= end + timedelta(seconds=1) and model in ("", r["model"])]
            call = {"task": r["task"], "model": r["model"], "seconds": r["seconds"], "outcome": r["outcome"],
                    "retried": bool(seen)}
            if seen:   # the retry lines in the call's window: "503", "timeout", "dropped", "billed" (the hand-over)
                call["events"] = seen
            calls.append(call)
    data = {"source": "development output/run_*/llm_calls.jsonl and logs/pipeline.log*",
            "days": sorted(days), "runs": len(records), "retry_log_lines": len(events)}
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data)[:-1] + ', "calls": [\n')   # one call per line
        f.write(",\n".join(json.dumps(c) for c in calls) + "\n]}\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
