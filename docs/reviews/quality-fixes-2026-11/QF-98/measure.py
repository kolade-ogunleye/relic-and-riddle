"""QF-98 acceptance metric. Usage: python measure.py <repo root>  (runs on the base and on the fix; prints JSON).

1. Replays of the 2026-11-10 writer sequences through the repo's real src.free_llm.structured, offline: gemini.structured
   is a scripted stub (503 in 4.4 s, a 300 s read timeout, a billed draft in 91 s: the diagnosis' probe times), time is
   simulated, the billed key has the configured heavy cap (USD 80) and nothing spent, and the run record is written
   to a temporary folder. Reported per sequence: which keys were asked, the simulated seconds to a draft, the reason
   given for the billed key, and whether llm_calls.jsonl names every request.
2. The free-model request timeout (300 s): from committed call_durations.json (development run records, extracted by
   extract_durations.py), the successful single-request calls (not retried) above candidate shorter timeouts.
Writes nothing outside a temporary folder; no network.
"""
import json
import logging
import random
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from pydantic import BaseModel

from src import config, free_llm, gemini
from src.ops import RunLog

FREE, BILLED = "f" * 30, "b" * 30
SEQUENCES = {
    "runs_1_2_503_then_timeout": [("503", 4.4), ("timeout", 300.0), ("timeout", 300.0), ("timeout", 300.0)],
    "run_3_503_x4": [("503", 4.4)] * 4,
}


class Draft(BaseModel):
    narration: str


class _Reasons(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        msg = record.getMessage()
        if "using the billed key" in msg:
            self.lines.append(msg.split("Free Gemini unavailable (", 1)[1].split("); using the billed key")[0])


def replay(outcomes, tmp: Path, name: str) -> dict:
    clock = SimpleNamespace(now=1000.0)
    keys = []
    free = list(outcomes)

    def stub(system, prompt, schema, model=None, key=None, **kw):
        keys.append("billed" if key == BILLED else "free")
        if key == BILLED:
            clock.now += 91.0
            return Draft(narration="billed")
        outcome, seconds = free.pop(0)
        clock.now += seconds
        if outcome == "503":
            raise gemini.GeminiError("Gemini is temporarily overloaded (HTTP 503); try again later.")
        raise gemini.RequestTimedOut("Gemini request failed: Read timed out. (read timeout=300)")

    def sleep(s):
        clock.now += s

    real_time = free_llm.time
    free_llm.time = SimpleNamespace(perf_counter=lambda: clock.now, monotonic=lambda: clock.now, sleep=sleep)
    gemini.structured = stub
    reasons = _Reasons()
    logging.getLogger("src.free_llm").addHandler(reasons)
    logging.getLogger("src.free_llm").setLevel(logging.INFO)
    free_llm._announced.clear()
    run_dir = tmp / f"run_{name}"
    sink = RunLog(run_dir).start()
    try:
        start = clock.now
        result = free_llm.structured("s", "p", Draft)
        seconds = round(clock.now - start, 1)
    finally:
        sink.stop()
        free_llm.time = real_time
        logging.getLogger("src.free_llm").removeHandler(reasons)
    row = json.loads((run_dir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    logged = row.get("attempts") or []
    return {"keys": keys, "draft_from": result.narration, "seconds_to_draft": seconds,
            "free_requests": keys.count("free"), "billed_reason": reasons.lines,
            "attempts_logged": len(logged), "requests_made": len(keys),
            "attempt_statuses": [(a["key"], a["status"]) for a in logged]}


def durations() -> dict:
    data = json.loads((HERE / "call_durations.json").read_text(encoding="utf-8"))
    single = [c for c in data["calls"] if c["outcome"] == "ok" and not c["retried"]]
    writer = sorted(c["seconds"] for c in single if c["task"] == "script_writing")
    out = {"runs": data["runs"], "days": f"{data['days'][0]}..{data['days'][-1]}", "gemini_calls": len(data["calls"]),
           "single_request_ok_calls": len(single), "longest_single_request_ok": sorted(
               ({"task": c["task"], "seconds": c["seconds"]} for c in single), key=lambda c: -c["seconds"])[:5],
           "script_writing_single_request": {"n": len(writer), "median": writer[len(writer) // 2],
                                             "p95": writer[int(0.95 * (len(writer) - 1))], "max": writer[-1]}}
    # Calls whose first two logged retries were 503s/timeouts in a row: under QF-98 those leave the free key after the
    # second one when the billed key can take them. Split by what happened on the old rule.
    early = [c for c in data["calls"] if c.get("events", [])[:2] in (["503", "503"], ["503", "timeout"],
                                                                     ["timeout", "503"], ["timeout", "timeout"])]
    out["two_capacity_failures_in_a_row"] = {
        "calls": len(early),
        "then_billed_anyway": sum("billed" in c["events"] for c in early),
        "then_answered_by_free_key": sum("billed" not in c["events"] and c["outcome"] == "ok" for c in early),
        "then_failed": sum(c["outcome"] != "ok" for c in early),
        "tasks_answered_by_free_key": sorted(c["task"] for c in early if "billed" not in c["events"] and c["outcome"] == "ok"),
        "all_calls": len(data["calls"])}
    out["answers_a_shorter_timeout_would_cut"] = {
        str(t): {"all_tasks": sum(c["seconds"] > t for c in single),
                 "script_writing": sum(s > t for s in writer)} for t in (120, 150, 180, 240)}
    return out


def main():
    random.seed(98)
    config.SCRIPT_ENGINE, config.GEMINI_FREE_API_KEY, config.GEMINI_API_KEY = "free", FREE, BILLED
    config.PAID_FALLBACK_HEAVY_MONTHLY_USD, config.FREE_TIER_HEAVY = 80.0, True
    config.GEMINI_SPARE_KEY_PERMITTED, config.CLAUDE_FALLBACK = False, False
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        from src import db as dbmod
        test_db = dbmod.PipelineDatabase(db_path=tmp / "t.db", topic_bank_file=tmp / "none.json")
        dbmod.PipelineDatabase = lambda *a, **k: test_db
        from src import provider_status
        provider_status._file = lambda: tmp / "provider_status.json"
        result = {"replays": {name: replay(seq, tmp, name) for name, seq in SEQUENCES.items()}}
        with test_db._conn() as conn:
            result["billed_usage_rows"] = conn.execute(
                "SELECT COUNT(*) FROM llm_usage WHERE task = 'paid-fallback:heavy'").fetchone()[0]
    result["heavy_cap_usd"] = config.PAID_FALLBACK_HEAVY_MONTHLY_USD
    result["timeout_300s"] = durations()
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
