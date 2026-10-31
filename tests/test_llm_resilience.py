from types import SimpleNamespace

import anthropic
import httpx
import pytest

from src import llm
from src.models import Poll


def _err(cls, status, message="error"):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=response, body=None)


def _ok(model, parsed=None, searches=0):
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_creation_input_tokens=0,
                            cache_read_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=searches))
    return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed, content=[], model=model,
                           _request_id="req", stop_details=None, usage=usage)


class _Messages:
    def __init__(self, behaviour):
        self.behaviour = behaviour  # model -> exception or response
        self.models = []
        self.kwargs = []

    def parse(self, **kw):
        self.models.append(kw["model"])
        self.kwargs.append(kw)
        out = self.behaviour[kw["model"]]
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def wire(monkeypatch):
    def make(behaviour, fallbacks=("claude-sonnet-5-5",), budget=0.0, budget_model="", spent=0.0):
        msgs = _Messages(behaviour)
        recorded = []
        monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setattr(llm.time, "sleep", lambda s: None)  # the retry pause
        monkeypatch.setattr(llm, "CLAUDE_MODEL", "claude-opus-5-5")
        monkeypatch.setattr(llm, "CLAUDE_FALLBACK_MODELS", list(fallbacks))
        monkeypatch.setattr(llm, "CLAUDE_MONTHLY_BUDGET_USD", budget)
        monkeypatch.setattr(llm, "CLAUDE_BUDGET_MODEL", budget_model)
        monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **kw: SimpleNamespace(beta=SimpleNamespace(messages=msgs)))
        llm.configure_usage(lambda **kw: recorded.append(kw), lambda month: spent)
        return msgs, recorded
    yield make
    llm.configure_usage(None, None)


POLL = Poll(question="q", options=["a", "b"])


def test_overloaded_primary_falls_back_to_next_model(wire):
    msgs, recorded = wire({"claude-opus-5-5": _err(anthropic.InternalServerError, 529, "overloaded"),
                           "claude-sonnet-5-5": _ok("claude-sonnet-5-5", POLL)})
    assert llm.structured_call("s", "p", Poll) == POLL
    assert msgs.models == ["claude-opus-5-5", "claude-opus-5-5", "claude-sonnet-5-5"]   # Opus twice, then Sonnet (2026-10-25)
    assert recorded[0]["model"] == "claude-sonnet-5-5" and recorded[0]["cost_usd"] == pytest.approx(3.0)


def test_exhausted_credit_is_reported_not_retried(wire):
    msgs, _ = wire({"claude-opus-5-5": _err(anthropic.BadRequestError, 400, "Your credit balance is too low"),
                    "claude-sonnet-5-5": _ok("claude-sonnet-5-5", POLL)})
    with pytest.raises(llm.CreditExhausted):
        llm.structured_call("s", "p", Poll)
    assert msgs.models == ["claude-opus-5-5"]


def test_budget_reached_stops_without_budget_model(wire):
    msgs, _ = wire({"claude-opus-5-5": _ok("claude-opus-5-5", POLL)}, budget=20.0, spent=20.5)
    with pytest.raises(llm.BudgetExceeded):
        llm.structured_call("s", "p", Poll)
    assert msgs.models == []


def test_budget_reached_switches_to_budget_model(wire):
    msgs, _ = wire({"claude-haiku-5-5": _ok("claude-haiku-5-5", POLL)}, budget=20.0, spent=25.0,
                   budget_model="claude-haiku-5-5")
    assert llm.structured_call("s", "p", Poll) == POLL
    assert msgs.models == ["claude-haiku-5-5"]
    assert "fallbacks" not in msgs.kwargs[0]  # Haiku 5.5 has no server-side refusal fallback


def test_refusal_fallback_sent_only_to_supporting_models(wire):
    msgs, _ = wire({"claude-opus-5-5": _ok("claude-opus-5-5", POLL)})
    llm.structured_call("s", "p", Poll)
    assert msgs.kwargs[0]["fallbacks"] == "default"


def test_cost_includes_searches_and_cache():
    usage = SimpleNamespace(input_tokens=100_000, output_tokens=10_000, cache_creation_input_tokens=0,
                            cache_read_input_tokens=1_000_000, server_tool_use=SimpleNamespace(web_search_requests=6))
    # opus-5-5: 0.1M*$4 + 1M cached*0.1*$4 + 0.01M*$20 + 6*$0.01
    assert llm.call_cost("claude-opus-5-5", usage) == pytest.approx(0.4 + 0.4 + 0.2 + 0.06)


def test_db_month_spend(db):
    from datetime import datetime, timezone
    db.record_llm_usage("claude-opus-5-5", 1, 1, 0, 1.25)
    db.record_llm_usage("claude-opus-5-5", 1, 1, 0, 0.75)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    assert db.llm_spend(month) == pytest.approx(2.0)
    assert db.llm_spend("1999-01") == 0


def test_usage_is_labelled_by_task_and_summed(db):
    from src import llm
    recorded = []
    llm.configure_usage(lambda **kw: recorded.append(kw), lambda month: 0.0)
    try:
        class U:
            input_tokens, output_tokens = 1000, 100
        class R:
            model, usage = "claude-opus-5-5", U()
        with llm.task("second_check"):
            llm._record(R())
        llm._record(R())
    finally:
        llm.configure_usage(None, None)
    assert [r["task"] for r in recorded] == ["second_check", "other"]
    for r in recorded:
        db.record_llm_usage(**r)
    from datetime import datetime, timezone
    rows = db.llm_spend_by_task(datetime.now(timezone.utc).strftime("%Y-%m"))
    assert {r["task"] for r in rows} == {"second_check", "other"} and all(r["calls"] == 1 for r in rows)


def test_media_budget_blocks_paid_calls(db, monkeypatch):
    from src import media_budget
    monkeypatch.setattr("src.config.MEDIA_MONTHLY_BUDGET_USD", 0.0)
    assert not media_budget.allow(db, 0.10)  # 0 = paid media off
    monkeypatch.setattr("src.config.MEDIA_MONTHLY_BUDGET_USD", 1.0)
    assert media_budget.allow(db, 0.60)
    media_budget.record(db, "veo-3.1-fast", "hook", 0.60)
    assert not media_budget.allow(db, 0.60) and media_budget.allow(db, 0.40)


def test_a_structured_answer_that_does_not_parse_is_asked_again_then_fails_as_an_llm_error(monkeypatch):
    # Writer trial 2026-10-24: a Sonnet trace check degenerated into "ok. ok. ok." until max_tokens; the SDK's
    # parse raised pydantic's ValidationError and the script stage died on it instead of asking once more.
    from pydantic import BaseModel

    class Out(BaseModel):
        text: str
    answers = []

    def parse(**kw):
        answers.append(1)
        if answers.count(1) in bad:
            Out.model_validate_json('{"text": "trunc')                     # a real ValidationError (invalid JSON)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=Out(text="ok"), content=[], model="claude-opus-5-5",
                               _request_id="r", usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse))))
    monkeypatch.setattr(llm, "model_chain", lambda models=None: ["claude-opus-5-5"])
    monkeypatch.setattr(llm, "_record", lambda r: None)
    bad = {1}
    assert llm.structured_call("s", "p", Out).text == "ok" and len(answers) == 2      # one more sample parsed
    answers.clear()
    bad = {1, 2}
    with pytest.raises(llm.LLMError, match="did not parse twice"):
        llm.structured_call("s", "p", Out)
    assert len(answers) == 2
