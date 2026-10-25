"""The free model client: what it retries, and what never reaches the billed key."""
import pytest
import requests
from pydantic import BaseModel

from src import free_llm, gemini


class Out(BaseModel):
    ok: bool


class _Done:
    status_code = 200
    ok = True

    def json(self):
        return {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"ok": true}'}]}]}


@pytest.fixture
def free(monkeypatch):
    monkeypatch.setattr(free_llm, "available", lambda: True)
    monkeypatch.setattr(free_llm, "enabled", lambda: True)
    monkeypatch.setattr(gemini, "is_configured", lambda key: True)
    monkeypatch.setattr(free_llm.time, "sleep", lambda s: None)


def test_a_dropped_connection_is_sent_again_on_the_free_key_and_never_billed(free, monkeypatch):
    calls = []

    def post(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed'))")
        return _Done()
    monkeypatch.setattr(gemini.requests, "post", post)
    monkeypatch.setattr(free_llm, "_paid_fallback", lambda *a, **k: (_ for _ in ()).throw(AssertionError("billed")))
    assert free_llm.structured("s", "p", Out).ok and len(calls) == 2


def test_a_timed_out_long_task_is_sent_again_then_uses_the_capped_billed_key(free, monkeypatch):
    # Production's 2026-10-23 run lost its script to a single 300 s timeout.
    calls, billed = [], []
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(requests.ReadTimeout("read timed out")))
    monkeypatch.setattr(free_llm, "_paid_fallback", lambda *a, **k: billed.append(a[-1]) or Out(ok=True))
    assert free_llm.structured("s", "p", Out).ok
    # after two timeouts, not four: each costs about five minutes (a dev step took 18 min, 2026-10-25)
    assert len(calls) == free_llm.TIMEOUTS_BEFORE_BILLED == 2 and "did not answer in time (2 attempts)" in billed[0]


def test_an_operator_facing_call_that_times_out_gives_way_without_billing(free, monkeypatch):
    calls = []
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(requests.ReadTimeout("read timed out")))
    monkeypatch.setattr(free_llm, "_paid_fallback", lambda *a, **k: (_ for _ in ()).throw(AssertionError("billed")))
    with pytest.raises(free_llm.LLMError, match="timed out"):
        free_llm.call("s", "p", Out, interactive=True)
    assert len(calls) == 2


def test_claude_stands_in_only_when_switched_on_and_capped(free, monkeypatch):
    from src import config, llm
    monkeypatch.setattr(free_llm, "structured", lambda *a, **k: (_ for _ in ()).throw(free_llm.LLMError("all Gemini failed")))
    monkeypatch.setattr(llm, "claude_available", lambda: True)
    monkeypatch.setattr(llm, "structured_call", lambda *a, **k: Out(ok=True))
    monkeypatch.setattr(config, "CLAUDE_MONTHLY_BUDGET_USD", 50.0)
    monkeypatch.setattr(config, "CLAUDE_FALLBACK", False)
    with pytest.raises(free_llm.LLMError):  # off by default
        free_llm.call("s", "p", Out)
    monkeypatch.setattr(config, "CLAUDE_FALLBACK", True)
    assert free_llm.call("s", "p", Out).ok
    monkeypatch.setattr(config, "CLAUDE_MONTHLY_BUDGET_USD", 0.0)  # 0 means uncapped: never used as a fallback
    with pytest.raises(free_llm.LLMError):
        free_llm.call("s", "p", Out)


def test_a_failed_spare_key_is_not_tried_on_every_call(free, monkeypatch):
    # Production 2026-10-23: each call waited 5 minutes on a spare key that never answered.
    from src import config
    monkeypatch.setattr(free_llm, "_spare_down_until", 0.0)
    monkeypatch.setattr(free_llm, "_spare_key", lambda: "spare-key")
    tried = []

    def post(url, headers=None, json=None, timeout=None):
        key = headers["x-goog-api-key"]
        tried.append(key)
        if key == "spare-key":
            raise requests.ReadTimeout("read timed out")
        class R:
            status_code, ok = 429, False
            def json(self):
                return {}
        return R()
    monkeypatch.setattr(gemini.requests, "post", post)
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free-key")
    monkeypatch.setattr(free_llm, "_paid_fallback", lambda *a, **k: Out(ok=True))
    assert free_llm.structured("s", "p", Out).ok and free_llm.structured("s", "p", Out).ok
    assert tried.count("spare-key") == 1  # the second call goes straight on


def test_heavy_tasks_can_go_straight_to_the_capped_billed_key(free, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "FREE_TIER_HEAVY", False)
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("free key tried")))
    why = []
    monkeypatch.setattr(free_llm, "_paid_fallback", lambda *a, **k: why.append(a[-1]) or Out(ok=True))
    assert free_llm.structured("s", "p", Out, task_class="heavy").ok and "FREE_TIER_HEAVY" in why[0]


def test_the_billed_key_gets_a_second_try_for_a_passing_failure_only(free, monkeypatch):
    # Operator, 2026-10-25: billed Gemini twice before the task leaves for the Claude standby.
    from src import config
    monkeypatch.setattr(config, "GEMINI_API_KEY", "billed")
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free")
    monkeypatch.setattr(config, "is_configured", lambda key: True)
    monkeypatch.setattr(free_llm, "_paid_cap", lambda task_class: 80.0)
    monkeypatch.setattr(free_llm, "_paid_spend", lambda task_class: 0.0)
    monkeypatch.setattr("src.provider_status.clear", lambda *a, **k: None)
    monkeypatch.setattr("src.db.PipelineDatabase.record_llm_usage", lambda *a, **k: None)
    tries = []

    def flaky(*a, **k):
        tries.append(1)
        if len(tries) == 1:
            raise gemini.RequestTimedOut("no answer")
        return Out(ok=True)
    monkeypatch.setattr(gemini, "structured", flaky)
    assert free_llm._paid_fallback("s", "p", Out, "gemini-3.8-flash", "heavy", "why").ok and len(tries) == 2
    tries.clear()
    monkeypatch.setattr(gemini, "structured", lambda *a, **k: tries.append(1) or (_ for _ in ()).throw(gemini.RequestTimedOut("x")))
    with pytest.raises(free_llm.LLMError, match="paid fallback also failed"):
        free_llm._paid_fallback("s", "p", Out, "gemini-3.8-flash", "heavy", "why")
    assert len(tries) == 2                                                        # two, then the standby's turn
    tries.clear()
    monkeypatch.setattr(gemini, "structured", lambda *a, **k: tries.append(1) or (_ for _ in ()).throw(gemini.GeminiError("400 bad request")))
    with pytest.raises(free_llm.LLMError):
        free_llm._paid_fallback("s", "p", Out, "gemini-3.8-flash", "heavy", "why")
    assert len(tries) == 1                                                        # a real error is not repeated
