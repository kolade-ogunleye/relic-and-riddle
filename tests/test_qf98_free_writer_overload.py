"""QF-98: an overloaded free writer hands over to the capped billed key on consecutive 503s/timeouts, with jittered
backoff, and every request is in llm_calls.jsonl.

Offline replays of the 2026-11-10 development-run sequences (decisions WRITER-TIMEOUT diagnosis): one fast 503 then a
300 s read timeout (runs 1 and 2), and 503 on every request (run 3). The request times come from the diagnosis
(503 in 4.4 s, billed draft in 91 s). No live call: gemini.structured is a scripted stub and time is simulated.
"""
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src import config, free_llm, gemini
from src.ops import RunLog

FREE, BILLED = "f" * 30, "b" * 30
OVERLOADED = "Gemini is temporarily overloaded (HTTP 503); try again later."


class Draft(BaseModel):
    narration: str


class Clock:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def perf_counter(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def replay(monkeypatch, tmp_path):
    """A free engine with the billed heavy key behind it (cap USD 80, nothing spent yet); returns a function that
    plays a sequence of free-key outcomes and records which key each request used."""
    from src.db import PipelineDatabase
    monkeypatch.setattr(config, "SCRIPT_ENGINE", "free")
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", FREE)
    monkeypatch.setattr(config, "GEMINI_API_KEY", BILLED)
    monkeypatch.setattr(config, "PAID_FALLBACK_HEAVY_MONTHLY_USD", 80.0)
    db = PipelineDatabase(db_path=tmp_path / "t.db", topic_bank_file=tmp_path / "none.json")
    monkeypatch.setattr("src.db.PipelineDatabase", lambda *a, **k: db)
    clock = Clock()
    monkeypatch.setattr(free_llm, "time", SimpleNamespace(perf_counter=clock.perf_counter, monotonic=clock.monotonic,
                                                          sleep=clock.sleep))
    keys = []

    def play(free_outcomes, billed_seconds=91.0, **kwargs):
        free = list(free_outcomes)

        def stub(system, prompt, schema, model=None, key=None, **kw):
            keys.append("billed" if key == BILLED else "free")
            if key == BILLED:
                clock.now += billed_seconds
                return Draft(narration="billed draft")
            outcome, seconds = free.pop(0)
            clock.now += seconds
            if outcome == "503":
                raise gemini.GeminiError(OVERLOADED)
            if outcome == "timeout":
                raise gemini.RequestTimedOut("Gemini request failed: Read timed out. (read timeout=300)")
            if outcome == "dropped":
                raise gemini.ConnectionDropped("Gemini request failed: Connection aborted.")
            return Draft(narration="free draft")
        monkeypatch.setattr(gemini, "structured", stub)
        started = clock.now
        result = free_llm.structured("s", "p", Draft, **kwargs)
        return result, clock.now - started
    play.keys, play.clock, play.db = keys, clock, db
    return play


def test_one_503_then_a_timeout_reaches_the_billed_key_and_says_what_happened(replay, caplog):
    caplog.set_level("INFO", logger="src.free_llm")
    result, seconds = replay([("503", 4.4), ("timeout", 300.0)])
    assert result.narration == "billed draft" and replay.keys == ["free", "free", "billed"]
    # the reason counts the failures as they were, not "2 attempts"
    assert "did not answer in time (2 attempts): 1 busy (HTTP 503), 1 timed out" in caplog.text
    assert seconds <= 4.4 + 15 * 1.5 + 300 + 91
    with replay.db._conn() as conn:   # the billed call counts against the heavy cap
        assert conn.execute("SELECT COUNT(*) FROM llm_usage WHERE task = 'paid-fallback:heavy'").fetchone()[0] == 1


def test_503_on_every_request_hands_over_after_two_not_four(replay, caplog):
    caplog.set_level("INFO", logger="src.free_llm")
    result, seconds = replay([("503", 4.4)] * 4)
    assert result.narration == "billed draft"
    assert replay.keys == ["free"] * free_llm.CAPACITY_FAILURES_BEFORE_BILLED + ["billed"] == ["free", "free", "billed"]
    assert "stayed overloaded (2 attempts): 2 busy (HTTP 503)" in caplog.text
    assert len(replay.clock.sleeps) == 1 and 7.5 <= replay.clock.sleeps[0] <= 22.5   # one jittered 15 s wait
    assert seconds <= 2 * 4.4 + 22.5 + 91


def test_the_billed_hand_over_stays_within_the_monthly_cap(replay, monkeypatch):
    monkeypatch.setattr(free_llm, "_paid_spend", lambda task_class: 80.0)    # this month's heavy cap is spent
    result, _ = replay([("503", 4.4), ("503", 4.4), ("ok", 75.0)])
    assert result.narration == "free draft" and replay.keys == ["free", "free", "free"]   # free keeps its retries


def test_with_nothing_behind_the_free_key_it_keeps_its_retries(replay, monkeypatch):
    monkeypatch.setattr(config, "PAID_FALLBACK_HEAVY_MONTHLY_USD", 0.0)      # dev_run.py's paid-free test setting
    result, _ = replay([("503", 4.4), ("timeout", 300.0), ("ok", 75.0)])
    # one timeout is not TIMEOUTS_BEFORE_BILLED timeouts: the free key is asked again instead of giving up
    assert result.narration == "free draft" and replay.keys == ["free", "free", "free"]


def test_two_real_timeouts_still_give_up_on_the_free_key_without_a_billed_key(replay, monkeypatch):
    monkeypatch.setattr(config, "PAID_FALLBACK_HEAVY_MONTHLY_USD", 0.0)
    with pytest.raises(free_llm.FreeQuotaUnavailable, match=r"did not answer in time \(2 attempts\): 2 timed out"):
        replay([("timeout", 300.0), ("timeout", 300.0), ("ok", 75.0)])
    assert replay.keys == ["free", "free"]


def test_a_dropped_connection_breaks_the_run_of_capacity_failures(replay):
    result, _ = replay([("503", 4.4), ("dropped", 1.0), ("503", 4.4), ("ok", 75.0)])
    assert result.narration == "free draft" and replay.keys == ["free"] * 4


def test_an_operator_facing_call_never_bills_on_a_timeout(replay):
    with pytest.raises(free_llm.LLMError, match="timed out"):
        replay([("503", 4.4), ("timeout", 45.0)], attempts=2, timeout=free_llm.INTERACTIVE_TIMEOUT)
    assert replay.keys == ["free", "free"]


def test_every_request_is_in_llm_calls_jsonl_with_its_status_and_seconds(replay, tmp_path, monkeypatch):
    monkeypatch.setattr(free_llm.random, "uniform", lambda a, b: 1.2)
    sink = RunLog(tmp_path / "run_x").start()
    try:
        replay([("503", 4.4), ("timeout", 300.0)])
        monkeypatch.setattr(config, "PAID_FALLBACK_HEAVY_MONTHLY_USD", 0.0)
        with pytest.raises(free_llm.FreeQuotaUnavailable):
            replay([("503", 0.5)] * 4)
    finally:
        sink.stop()
    rows = [json.loads(ln) for ln in (tmp_path / "run_x" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["outcome"] == "ok" and rows[0]["seconds"] == 4.4 + 18 + 300 + 91
    assert rows[0]["attempts"] == [{"key": "free", "status": "503", "seconds": 4.4},
                                   {"key": "free", "status": "timeout", "seconds": 300.0, "waited": 18.0},
                                   {"key": "billed", "status": "ok", "seconds": 91.0}]
    assert rows[1]["outcome"] == "FreeQuotaUnavailable"
    assert [(a["status"], a.get("waited")) for a in rows[1]["attempts"]] == [
        ("503", None), ("503", 18.0), ("503", 36.0), ("503", 72.0)]


def test_backoff_is_exponential_with_jitter_inside_the_bounds(monkeypatch):
    seen = []
    monkeypatch.setattr(free_llm.random, "uniform", lambda a, b: seen.append((a, b)) or b)
    assert [free_llm._backoff(d) for d in (15.0, 30.0, 60.0)] == [22.5, 45.0, 90.0]
    assert seen == [(0.5, 1.5)] * 3
