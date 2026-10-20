from src import trace_check


def test_performance_claim_cannot_pass_as_uncited_rhetoric(monkeypatch):
    report = trace_check.TraceReport(claims=[trace_check.TracedClaim(
        claim="Ancient material outlasts our best engineering", where="caption",
        fact_ids=[], claim_type="rhetorical_framing", verdict="traced", note="Rhetorical")])
    monkeypatch.setattr(trace_check.free_llm, "call", lambda *a, **k: report)
    passed, _, audits, _ = trace_check.check({"caption": report.claims[0].claim}, {"facts": []})
    assert not passed
    assert audits[0]["verdict"] == "untraced"


def test_empty_review_cannot_certify_a_documentary(monkeypatch):
    monkeypatch.setattr(trace_check.free_llm, "call", lambda *a, **k: trace_check.TraceReport(claims=[]))
    assert not trace_check.check({"narration_script": "A factual statement"}, {"facts": []})[0]


def test_hypothetical_question_can_remain_nonfactual(monkeypatch):
    report = trace_check.TraceReport(claims=[trace_check.TracedClaim(
        claim="Could this material heal?", where="comment", fact_ids=[],
        claim_type="rhetorical_framing", verdict="traced", note="Question")])
    monkeypatch.setattr(trace_check.free_llm, "call", lambda *a, **k: report)
    assert trace_check.check({"first_comment": report.claims[0].claim}, {"facts": []})[0]


def test_epistemic_negation_passes(monkeypatch):
    report = trace_check.TraceReport(claims=[trace_check.TracedClaim(
        claim="The architect's name is not recorded", where="narration", fact_ids=[],
        claim_type="epistemic_negation", verdict="traced", note="Unrecorded name")])
    monkeypatch.setattr(trace_check.free_llm, "call", lambda *a, **k: report)
    passed, _, audits, _ = trace_check.check({"narration_script": report.claims[0].claim}, {"facts": []})
    assert passed
    assert audits[0]["verdict"] == "traced"


def test_qualitative_modern_contrast_passes(monkeypatch):
    report = trace_check.TraceReport(claims=[trace_check.TracedClaim(
        claim="Modern builders erect monuments upward", where="narration", fact_ids=[],
        claim_type="rhetorical_framing", verdict="traced", note="Qualitative contrast")])
    monkeypatch.setattr(trace_check.free_llm, "call", lambda *a, **k: report)
    passed, _, audits, _ = trace_check.check({"narration_script": report.claims[0].claim}, {"facts": []})
    assert passed
    assert audits[0]["verdict"] == "traced"

