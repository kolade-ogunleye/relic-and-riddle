import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("eval_models", Path(__file__).resolve().parent.parent / "scripts" / "eval_models.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_flagged_claims_match_the_guardrail_rules():
    audits = [
        {"claim": "a", "verdict": "supported", "sources": [{"url": "u"}], "hedged_in_script": False, "note": ""},
        {"claim": "b", "verdict": "supported", "sources": [], "hedged_in_script": False, "note": ""},
        {"claim": "c", "verdict": "disputed", "sources": [], "hedged_in_script": True, "note": ""},
        {"claim": "d", "verdict": "false", "sources": [], "hedged_in_script": False, "note": "wrong year"},
    ]
    assert [f.split(" ")[0] for f in ev._flagged_claims(audits)] == ["b", "d"]


def test_spend_cap_stops_before_overrun(db):
    s = ev.Spend(1.0, db)
    s.record(model="m", input_tokens=1, output_tokens=1, web_searches=0, cost_usd=0.8)
    s.ensure(0.2, "ok")
    with pytest.raises(ev.BudgetStop):
        s.ensure(0.3, "too much")


def test_estimate_stays_within_the_agreed_budget(capsys):
    assert ev.estimate() <= 30.0
