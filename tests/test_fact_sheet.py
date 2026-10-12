import json

import pytest

from src import fact_sheet, trace_check
from src.models import DraftFact

QUOTES = [
    {"url": "https://en.wikipedia.org/wiki/Derinkuyu", "quote": "at least eight levels constructed to a depth of 85 metres (279 ft)"},
    {"url": "https://www.unesco.org/x", "quote": "The city was rediscovered in 1963."},
    {"url": "https://travelblog.example/d", "quote": "doors weighing 200 to 500 kg"},
    {"url": "https://other.example/d", "quote": "some say 60 m deep"},
]


def fact(fid, statement, kind, quote_ids, disputed=False, hedge=""):
    return DraftFact(id=fid, statement=statement, kind=kind, quote_ids=quote_ids, disputed=disputed, hedge=hedge)


def test_tiers():
    assert fact_sheet.tier("https://whc.unesco.org/en/list/357") == 1
    assert fact_sheet.tier("https://www.ktb.gov.tr/x") == 1
    assert fact_sheet.tier("https://en.wikipedia.org/wiki/X") == 2
    assert fact_sheet.tier("https://www.ancient-origins.net/x") == 3  # popular but sensational
    assert fact_sheet.tier("https://travelblog.example/x") == 3


def test_rules_keep_supported_drop_unsupported():
    kept, dropped = fact_sheet._apply_rules([
        fact("F1", "Derinkuyu is about 280 feet deep.", "number", [1, 2]),        # 85 m conversion; two sites
        fact("F2", "It was rediscovered in 1963.", "date", [2]),                    # tier-1 source
        fact("F3", "It was rediscovered in 1961.", "date", [2]),                    # number not in evidence
        fact("F4", "Doors weighed up to 500 kg.", "number", [3]),                   # one weak source, no hedge
        fact("F5", "Doors weighed up to 500 kg.", "number", [3], hedge="often said to weigh several hundred kg"),
        fact("F6", "No source.", "other", [99]),
    ], QUOTES)
    ids = {f["id"]: f for f in kept}
    assert set(ids) == {"F1", "F2", "F5"}
    assert ids["F5"]["disputed"] is True and ids["F5"]["hedge"]
    reasons = dict(dropped)
    assert "1961" in reasons["F3"] and "weak source" in reasons["F4"] and "no valid evidence" in reasons["F6"]


def test_untraced_numbers_found_in_every_surface(curated_payload):
    sheet = {"facts": [{"id": "F1", "statement": "About 85 m (279 ft) deep.", "hedge": "", "disputed": False,
                        "evidence": [{"url": "u", "tier": 1, "quote": "85 metres; rediscovered in 1963; 20,000 people; 500 kg; 55 m shaft; 8th century; 1965; 18 levels"}]}]}
    payload = dict(curated_payload, caption="Story about 1999.\n\n#Derinkuyu", stat_cards=[{"value": "4,000", "label": "people"}])
    problems = trace_check.untraced_numbers(payload, sheet)
    assert any("caption: 1999" in p for p in problems)
    assert any("graphic: 4000" in p for p in problems)
    assert not any(": 85 " in p or ": 280 " in p for p in problems)  # 280 ft is the conversion of 85 m


def test_surgical_revision_changes_only_flagged_text(monkeypatch, curated_payload):
    from src import script_engine
    from src.script_engine import Edit, Edits
    eng = script_engine.ScriptEngine()
    old = "Rolling stone doors, some weighing up to half a ton, sealed each level from the inside."
    assert old in curated_payload["narration_script"]
    new = "Rolling stone doors, often said to weigh several hundred kilograms, sealed each level from the inside."
    monkeypatch.setattr(script_engine, "structured_call", lambda *a, **k: Edits(edits=[Edit(find=old, replace=new)]))
    pkg = eng._revise_surgically(dict(curated_payload), "'half a ton' (disputed)", {"facts": []})
    assert new in pkg.narration_script and old not in pkg.narration_script
    assert pkg.caption == curated_payload["caption"]  # untouched
    monkeypatch.setattr(script_engine, "structured_call", lambda *a, **k: Edits(edits=[Edit(find="not in the draft", replace="x")]))
    assert eng._revise_surgically(dict(curated_payload), "x", {"facts": []}) is None  # falls back to a rewrite


def test_scripts_from_a_fact_sheet_are_trace_checked(monkeypatch, curated_payload, tmp_path):
    from src import script_engine
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path)
    fact_sheet.save({"topic_id": "t1", "built_at": "2099-01-01T00:00:00+00:00", "facts": [], "dropped": [], "sources": []})
    called = {}
    monkeypatch.setattr(trace_check, "check", lambda payload, sheet: called.setdefault("trace", True) and (True, 100.0, [], "ok"))
    eng = script_engine.ScriptEngine()
    monkeypatch.setattr(eng.fact_checker, "verify", lambda *a, **k: pytest.fail("web check used instead of trace"))
    monkeypatch.setattr(script_engine, "SECOND_CHECK_ENABLED", False)
    payload = dict(curated_payload, topic_id="t1", origin="claude", fact_sheet=True)
    passed, out, msg = eng.verify_claims(payload)
    assert passed and called["trace"] and "verified fact sheet" in out["fact_sheet_note"]


def test_single_tier2_source_is_not_enough_for_a_number():
    kept, dropped = fact_sheet._apply_rules([fact("F1", "Derinkuyu is about 280 feet deep.", "number", [1])], QUOTES)
    assert kept == [] and "weak source" in dropped[0][1]
