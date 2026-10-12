import pytest

from src import fact_sheet, free_llm, gemini, trace_check
from src.models import ScriptCore


@pytest.fixture
def free(monkeypatch):
    monkeypatch.setattr("src.config.SCRIPT_ENGINE", "free")
    monkeypatch.setattr("src.config.GEMINI_FREE_API_KEY", "f" * 30)
    monkeypatch.setattr("src.config.GEMINI_API_KEY", "b" * 30)  # the billed key: must never be used here


def test_free_calls_use_only_the_free_key_and_model_roles(free, monkeypatch):
    seen = []
    monkeypatch.setattr(gemini, "structured", lambda system, prompt, schema, model=None, key=None:
                        seen.append((model, key)) or schema(claims=[]))
    free_llm.structured("s", "p", trace_check.TraceReport, role="writer")
    free_llm.structured("s", "p", trace_check.TraceReport, role="checker")
    assert seen == [("gemini-3.8-flash", "f" * 30), ("gemini-3.7-flash", "f" * 30)]


def test_no_free_key_means_no_call_at_all(monkeypatch):
    monkeypatch.setattr("src.config.SCRIPT_ENGINE", "free")
    monkeypatch.setattr(gemini, "structured", lambda *a, **k: pytest.fail("called without a free key"))
    with pytest.raises(free_llm.FreeQuotaUnavailable):
        free_llm.structured("s", "p", trace_check.TraceReport)


def test_quota_exhausted_stops_and_overload_retries(free, monkeypatch):
    monkeypatch.setattr(free_llm.time, "sleep", lambda s: None)
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise gemini.GeminiError("Gemini is temporarily overloaded (HTTP 503); try again later.")
        return trace_check.TraceReport(claims=[])
    monkeypatch.setattr(gemini, "structured", flaky)
    assert free_llm.structured("s", "p", trace_check.TraceReport).claims == [] and len(calls) == 3
    monkeypatch.setattr(gemini, "structured", lambda *a, **k: (_ for _ in ()).throw(gemini.QuotaExhausted("429")))
    with pytest.raises(free_llm.FreeQuotaUnavailable):
        free_llm.structured("s", "p", trace_check.TraceReport)


def test_free_mode_never_calls_claude_to_check(free, monkeypatch, curated_payload, tmp_path):
    from src import script_engine
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path)
    sheet = {"topic_id": "t1", "built_at": "2099-01-01T00:00:00+00:00", "dropped": [], "sources": [],
             "facts": [{"id": "F1", "statement": "x", "kind": "other", "disputed": False, "hedge": "",
                        "evidence": [{"url": "https://en.wikipedia.org/wiki/X", "tier": 2, "quote": "x"}]}]}
    fact_sheet.save(sheet)
    monkeypatch.setattr(trace_check, "check", lambda p, s: (True, 100.0, [], "traced"))
    monkeypatch.setattr(trace_check, "evidence_check", lambda p, s: ("pass", 100.0, [], "evidence ok"))
    eng = script_engine.ScriptEngine()
    monkeypatch.setattr(eng.fact_checker, "verify", lambda *a, **k: pytest.fail("paid first check"))
    monkeypatch.setattr(eng.fact_checker, "second_opinion", lambda *a, **k: pytest.fail("paid second check"))
    passed, out, msg = eng.verify_claims(dict(curated_payload, topic_id="t1", origin="gemini-free", fact_sheet=True))
    assert passed and "evidence ok" in msg
    # Without a fact sheet, a model-written script fails closed (no paid fallback).
    passed, _, msg = eng.verify_claims(dict(curated_payload, topic_id="none", origin="gemini-free"))
    assert not passed and "fails closed" in msg
