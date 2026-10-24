import pytest

from src import fact_sheet, free_llm, gemini, trace_check


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
    passed, _, msg = eng.verify_claims(dict(curated_payload, topic_id="none", origin="gemini-free"))
    assert not passed and "fails closed" in msg


def test_openalex_scholarly_evidence_synthesis(monkeypatch):
    from src.free_evidence import fetch_openalex_scholarly_evidence
    class MockWorkResp:
        status_code = 200
        def json(self):
            return {
                "results": [
                    {
                        "title": "Ancient Mechanical Calculator",
                        "doi": "https://doi.org/10.1038/nature05357",
                        "abstract_inverted_index": {
                            "The": [0], "mechanism": [1], "was": [2], "constructed": [3], "in": [4],
                            "ancient": [5], "Greece.": [6], "Scans": [7], "revealed": [8], "thirty": [9],
                            "bronze": [10], "gears.": [11]
                        }
                    }
                ]
            }

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: MockWorkResp())
    quotes, sources = fetch_openalex_scholarly_evidence("Antikythera mechanism")
    assert len(quotes) >= 1
    assert "https://doi.org/10.1038/nature05357" in quotes[0]["url"]
    assert "gears" in quotes[0]["quote"] or "Greece" in quotes[0]["quote"]
    assert len(sources) == 1
    assert sources[0]["url"] == "https://doi.org/10.1038/nature05357"



def test_interactive_calls_wait_briefly_and_never_bill_on_a_timeout(monkeypatch):
    from src import config, free_llm
    monkeypatch.setattr(config, "SCRIPT_ENGINE", "free", raising=False)
    monkeypatch.setattr(free_llm, "enabled", lambda: True)
    monkeypatch.setattr(free_llm, "available", lambda: True)
    seen = []
    def stalled(system, prompt, schema, model=None, key=None, timeout=None):
        seen.append(timeout)
        raise gemini.GeminiError("Gemini request failed: Read timed out.")
    monkeypatch.setattr(gemini, "structured", stalled)
    monkeypatch.setattr(free_llm, "_paid_fallback", lambda *a, **k: pytest.fail("a timeout must not bill"))
    with pytest.raises(Exception):
        free_llm.call("s", "p", object, role="checker", task_class="light", interactive=True)
    assert seen == [free_llm.INTERACTIVE_TIMEOUT]


def test_a_low_effort_vision_call_limits_thinking_and_falls_back_when_refused(monkeypatch):
    # A 10-beat scene plan spent 20,000 output tokens and 2.5 minutes thinking (production 2026-10-24).
    import json as _json
    from types import SimpleNamespace
    from src import config as cfg
    monkeypatch.setattr(cfg, "GEMINI_API_KEY", "paid-key-456")
    bodies = []

    def post(url, headers=None, json=None, timeout=None):
        import copy
        bodies.append(copy.deepcopy(json))
        if "thinkingConfig" in json["generationConfig"] and len(bodies) == 1:
            return SimpleNamespace(status_code=400, text='{"error": "Unknown name thinking_config"}', json=lambda: {})
        return SimpleNamespace(status_code=200, text="", json=lambda: {"usageMetadata": {}, "candidates": [{"content": {"parts": [{"text": _json.dumps({"claims": []})}]}}]})
    monkeypatch.setattr(gemini.requests, "post", post)
    out = gemini.structured("s", [{"type": "text", "text": "p"}], trace_check.TraceReport, model="gemini-3.8-flash", key="paid-key-456", thinking="low")
    assert out.claims == [] and len(bodies) == 2
    assert bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"} and "thinkingConfig" not in bodies[1]["generationConfig"]
    bodies.clear()
    gemini.structured("s", [{"type": "text", "text": "p"}], trace_check.TraceReport, model="gemini-3.8-flash", key="paid-key-456")
    assert len(bodies) == 1 and "thinkingConfig" not in bodies[0]["generationConfig"]   # no effort given: the default
