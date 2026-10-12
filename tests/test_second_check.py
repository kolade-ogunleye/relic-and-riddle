import pytest

from src import fact_checker as fc
from src import gemini
from src.llm import LLMError
from src.models import ClaimAudit, FactCheckReport, Source
from src.script_engine import ScriptEngine

GOOD = FactCheckReport(claims=[ClaimAudit(claim="c", verdict="supported", hedged_in_script=False, note="",
                                          sources=[Source(title="s", url="https://s.org")])], summary="")
BAD = FactCheckReport(claims=[ClaimAudit(claim="c", verdict="false", hedged_in_script=False, note="wrong",
                                         sources=[])], summary="")


@pytest.fixture
def checker(monkeypatch):
    monkeypatch.setattr(fc, "claude_available", lambda: True)
    monkeypatch.setattr(fc, "SECOND_CHECK_MODELS", ["claude-fable-5-1", "claude-opus-5-5"])
    return fc.ClaimFactChecker()


def test_best_model_first(checker, monkeypatch):
    used = []
    monkeypatch.setattr(checker, "_check_with_claude", lambda p, models=None: used.append(models[0]) or (GOOD, {"https://s.org"}))
    status, _, _, msg = checker.second_opinion({})
    assert status == "pass" and used == ["claude-fable-5-1"] and "claude-fable-5-1" in msg


def test_falls_through_to_gemini(checker, monkeypatch):
    def claude_fails(p, models=None):
        raise LLMError("overloaded")
    monkeypatch.setattr(checker, "_check_with_claude", claude_fails)
    monkeypatch.setattr(gemini, "available", lambda: True)
    monkeypatch.setattr(checker, "_check_with_gemini", lambda p: (BAD, set()))
    status, _, _, msg = checker.second_opinion({})
    assert status == "fail" and "gemini" in msg


def test_unavailable_when_everything_fails(checker, monkeypatch):
    def fails(*a, **k):
        raise LLMError("down")
    monkeypatch.setattr(checker, "_check_with_claude", fails)
    monkeypatch.setattr(gemini, "available", lambda: False)
    assert checker.second_opinion({})[0] == "unavailable"


def test_second_check_rejection_blocks_script(monkeypatch, curated_payload):
    eng = ScriptEngine()
    curated_payload.update(origin="claude", human_reviewed=True)
    monkeypatch.setattr(eng.fact_checker, "verify", lambda *a, **k: (True, 100.0, [], "first ok"))
    monkeypatch.setattr(eng.fact_checker, "second_opinion", lambda p: ("fail", 50.0, [], "[claude-fable-5-1] 1 blocking claim"))
    passed, payload, notes = eng.verify_claims(curated_payload)
    assert not passed and "second fact checker" in notes and payload["second_check"]["status"] == "fail"


def test_gemini_parses_interactions_response(monkeypatch):
    monkeypatch.setattr(gemini, "GEMINI_API_KEY", "AIza-test")
    body = {"status": "completed", "steps": [
        {"type": "google_search_call", "arguments": {"queries": ["x"]}},
        {"type": "model_output", "content": [{"type": "text", "text": "Fact. ", "annotations": [
            {"type": "url_citation", "url": "https://a.org", "title": "A"}]}, {"type": "text", "text": "More."}]}]}

    class R:
        status_code, ok = 200, True
        def json(self):
            return body
    sent = {}
    monkeypatch.setattr(gemini.requests, "post", lambda url, headers, json, timeout: sent.update(json) or R())
    text, sources = gemini.research("sys", "p")
    assert text == "Fact. More." and [s.url for s in sources] == ["https://a.org"]
    assert sent["tools"] == [{"type": "google_search"}]


def test_gemini_schema_is_inlined():
    schema = gemini._inline_refs(FactCheckReport.model_json_schema())
    assert "$defs" not in str(schema) and "$ref" not in str(schema)


def test_disagreement_escalates_and_escalation_decides(monkeypatch):
    from src import fact_checker as fc
    checker = fc.ClaimFactChecker()
    monkeypatch.setattr(fc, "claude_available", lambda: True)
    monkeypatch.setattr(fc, "SECOND_CHECK_MODELS", ["claude-opus-5-5"])
    monkeypatch.setattr(fc, "SECOND_CHECK_ESCALATION_MODEL", "claude-fable-5-1")
    used = []

    def check(payload, models=None):
        used.append(models[0])
        return "report-" + models[0], set()
    monkeypatch.setattr(checker, "_check_with_claude", check)
    verdicts = {"report-claude-opus-5-5": False, "report-claude-fable-5-1": True}
    monkeypatch.setattr(fc.ClaimFactChecker, "evaluate", staticmethod(
        lambda report, urls=None: (verdicts[report], 100.0 if verdicts[report] else 50.0, [], "msg")))
    status, conf, _, summary = checker.second_opinion({})
    assert used == ["claude-opus-5-5", "claude-fable-5-1"]
    assert status == "pass" and conf == 100.0 and "claude-fable-5-1 decided" in summary


def test_escalation_failure_keeps_the_dispute(monkeypatch):
    from src import fact_checker as fc
    from src.llm import LLMError
    checker = fc.ClaimFactChecker()
    monkeypatch.setattr(fc, "claude_available", lambda: True)
    monkeypatch.setattr(fc, "SECOND_CHECK_MODELS", ["claude-opus-5-5"])
    monkeypatch.setattr(fc, "SECOND_CHECK_ESCALATION_MODEL", "claude-fable-5-1")

    def check(payload, models=None):
        if models[0] == "claude-fable-5-1":
            raise LLMError("overloaded")
        return "r", set()
    monkeypatch.setattr(checker, "_check_with_claude", check)
    monkeypatch.setattr(fc.ClaimFactChecker, "evaluate", staticmethod(lambda report, urls=None: (False, 50.0, [], "x")))
    status, _, _, summary = checker.second_opinion({})
    assert status == "fail" and "escalation" in summary


def test_agreement_does_not_escalate(monkeypatch):
    from src import fact_checker as fc
    checker = fc.ClaimFactChecker()
    monkeypatch.setattr(fc, "claude_available", lambda: True)
    monkeypatch.setattr(fc, "SECOND_CHECK_MODELS", ["claude-opus-5-5"])
    used = []
    monkeypatch.setattr(checker, "_check_with_claude", lambda payload, models=None: used.append(models[0]) or ("r", set()))
    monkeypatch.setattr(fc.ClaimFactChecker, "evaluate", staticmethod(lambda report, urls=None: (True, 100.0, [], "ok")))
    assert checker.second_opinion({})[0] == "pass" and used == ["claude-opus-5-5"]


def test_gemini_schema_keeps_fields_named_title():
    from src.gemini import _inline_refs
    from src.models import FactCheckReport
    s = _inline_refs(FactCheckReport.model_json_schema())
    src = s["properties"]["claims"]["items"]["properties"]["sources"]["items"]
    assert "title" in src["properties"] and set(src["required"]) <= set(src["properties"])
    assert "title" not in s  # schema annotations are still stripped


def test_gemini_redirect_citations_resolve_to_real_pages(monkeypatch):
    from src import gemini

    class H:
        headers = {"Location": "https://www.example.org/derinkuyu"}
    seen = []
    monkeypatch.setattr(gemini.requests, "head", lambda url, allow_redirects, timeout: seen.append(allow_redirects) or H())
    assert gemini._resolve("https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc") == "https://www.example.org/derinkuyu"
    assert seen == [False]  # the target page itself is never fetched
    assert gemini._resolve("https://other.example/x") == "https://other.example/x"
