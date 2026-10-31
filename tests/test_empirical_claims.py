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


def test_open_historical_debate_untraced_passes_check(monkeypatch):
    # An open historical question or reflection marked untraced asserts no empirical claim to verify
    report = trace_check.TraceReport(claims=[trace_check.TracedClaim(
        claim="Whether Roman builders understood the exact chemistry or deliberately engineered the self-healing process is an open question.",
        where="slide", fact_ids=[],
        claim_type="open_historical_debate", verdict="untraced", note="Open historical debate")])
    monkeypatch.setattr(trace_check.free_llm, "call", lambda *a, **k: report)
    passed, _, audits, _ = trace_check.check({"slide": report.claims[0].claim}, {"facts": []})
    assert passed
    assert audits[0]["verdict"] == "untraced"


def test_evidence_verdict_records_surface_provenance():
    v = trace_check.EvidenceVerdict(
        claim="Did builders understand the mix?",
        where="story",
        claim_type="rhetorical_framing",
        verdict="hedged_ok",
        quote_ids=[],
        note="Rhetorical question in story slide"
    )
    assert v.where == "story"
    assert v.claim_type == "rhetorical_framing"


def test_free_second_check_escalates_to_claude_on_quota(monkeypatch):
    from src.script_engine import ScriptEngine
    engine = ScriptEngine()
    monkeypatch.setattr(trace_check, "evidence_check", lambda p, s: ("unavailable", 0.0, [], "Gemini quota 429"))
    monkeypatch.setattr("src.script_engine.claude_available", lambda: True)
    monkeypatch.setattr(engine.fact_checker, "second_opinion", lambda p: ("pass", 95.0, [{"claim": "C", "verdict": "supported"}], "[claude-opus] verified"))
    monkeypatch.setattr("src.script_engine.SECOND_CHECK_ATTEMPTS", 1)
    status, conf, audits, msg = engine._free_second_check({"title": "Test"}, {"facts": []})
    assert status == "pass"
    assert conf == 95.0
    assert "[claude-opus] verified" in msg

