"""QF-16: the hedge rule. Operator ruling 2026-11-11 (decisions/QF-16-question.md, under delegation):
1 (B) the fact sheet's automatic hedge takes only a weak date or measured quantity (a number with a unit), in the
  statement's own words (range statements are deferred to proposed QF-112); a weak count or event figure is not hedged automatically. Cached sheets are
  re-checked in place on load (no RULES_VERSION bump, so no topic is researched again for it).
2 (P3) in the opening, the payoff and the final loop sentence, a disputed fact of the run's sheet blocks after the one
  shared repair edit; any other qualifier there gets that edit and then ships flagged on the review card (hedge_note).
  A question is exempt.
3 body qualifiers are neither blocked nor flagged; one quantity matcher (digits, spelled numbers, multiples) serves
  every marker."""

import json

import pytest

from src import fact_sheet, improve, narrative_hedges
from src import script_engine as engine
from src.models import DraftFact, ScriptPackage

SHEET = {"topic_id": "t1", "facts": [
    {"id": "F1", "kind": "number", "disputed": True, "statement": "The shaft is 280 feet (85 m) deep.",
     "hedge": "by one estimate, the shaft is 280 feet (85 m) deep.", "evidence": []},
    {"id": "F2", "kind": "date", "disputed": False, "statement": "The shaft was found in 1963.", "hedge": "",
     "evidence": []},
    {"id": "F3", "kind": "number", "disputed": True, "statement": "It holds 144 chambers.",
     "hedge": "an estimated 144 chambers", "evidence": []}]}
BODY = "Workers cut the rock by hand. Each level has its own air shaft."
OPEN = "A farmer found a hidden shaft in 1963."
PAY = "The shaft was found in 1963 by a farmer."
LOOP = "And it still keeps its secret."


def kinds(narration, sheet=SHEET):
    found = engine.hedge_problems(narration, sheet)
    return "blocked" if engine.blocking_problems(found) else "flagged" if found else "clean"


# ------------------------------------------------------------------------- 1: the automatic hedge's scope (B)
def _auto(statement, kind, quote):
    return fact_sheet._apply_rules(
        [DraftFact(id="F1", statement=statement, kind=kind, quote_ids=[1], disputed=False, hedge="")],
        [{"url": "https://travel-blog.example/x", "quote": quote}], auto_hedge=True)


@pytest.mark.parametrize(("statement", "kind"), [
    ("Miners carved four chapels in the rock.", "number"),        # the review's cited defect: a count
    ("Visitors left 107 inscriptions.", "number"),                 # a count in digits
])
def test_a_weak_count_gets_no_automatic_hedge_and_is_dropped(statement, kind):
    kept, dropped = _auto(statement, kind, statement)
    assert kept == [] and "no hedge" in dropped[0][1] and "measured quantity" in dropped[0][1]


@pytest.mark.parametrize(("statement", "kind", "lead"), [
    ("The city reached 85 meters deep.", "number", "by one estimate"),
    ("Its walls dip at 15 degrees.", "number", "by one estimate"),
    ("Less than 2 percent of it is open.", "number", "by one estimate"),
    ("It was rediscovered in 1963.", "date", "by one account"),
])
def test_a_weak_measured_quantity_or_date_is_still_hedged(statement, kind, lead):
    kept, _ = _auto(statement, kind, statement)
    assert kept[0]["disputed"] is True and kept[0]["hedge"].startswith(lead)


@pytest.mark.parametrize(("statement", "kind", "quote"), [
    # Range statements are deferred to proposed QF-112: the automatic hedge never rewrites the statement's figure.
    # Round 3's own positive cases, now never rewritten:
    ("The city reached 85 meters deep.", "number", "The city reached between 60 and 85 meters deep."),
    ("The city reached 85 meters deep.", "number", "It reached 85 meters; the 60 to 70 m figure is old."),
    # review 3, blocking 1 (round 3's false or misplaced ranges):
    ("The tunnels were rediscovered in 1963.", "date", "The tunnels were rediscovered in 1963-64 by a resident."),
    ("The tunnels were rediscovered in 1963.", "date", "The tunnels were rediscovered between 1963 and 1965."),
    ("It was made in 290 AD.", "date", "It was made between 290 and 325 AD."),
    ("The shaft is 20 meters deep.", "number", "See pages 20-25: the shaft is 20 meters deep."),
    # round 4's cases:
    ("It rises 120 meters.", "number", "Its walls rise 120-25 meters above the plain."),        # abbreviated
    ("The engineer died in 1938.", "date", "The engineer Ivan Petrov (1881–1938) surveyed the site."),   # lifespan
    ("The site was built in 9500 BC.", "date", "The site was built over 9500–8000 BC."),                 # BC span
    ("It was rediscovered in 1963.", "date", "It was rediscovered between 1963 and 1965."),             # in <year>
    ("The city reached 85 meters deep.", "number", "The city reached between 60 and 85 feet deep."),     # other unit
    ("The hall is about 1500 meters long.", "number", "The hall runs from 1500 to 1600 metres long."),   # approximation
    # review 3, round 4 (blocking 1): a bound word, a hyphenated adjective, another dimension
    ("The city reached up to 85 meters deep.", "number", "The city reached between 60 and 85 meters deep."),
    ("The city reached more than 85 meters deep.", "number", "The city reached between 60 and 85 meters deep."),
    ("The city reached over 85 meters deep.", "number", "The city reached between 60 and 85 meters deep."),
    ("The city reached c. 85 meters deep.", "number", "The city reached between 60 and 85 meters deep."),
    ("It is an 85-meter shaft.", "number", "It is a 60–85-meter shaft."),
    ("The shaft is 20 meters deep.", "number", "The hall is 20 to 30 meters wide; the shaft is 20 meters deep."),
])
def test_the_automatic_hedge_never_rewrites_the_statement_into_a_range(statement, kind, quote):
    kept, _ = _auto(statement, kind, quote)
    assert kept and kept[0]["hedge"] == fact_sheet._auto_form(statement, kind)        # the statement's own wording
    assert "between" not in kept[0]["hedge"] and "1,9" not in kept[0]["hedge"]
    assert narrative_hedges.with_quoted_range(statement, [{"quote": quote}], kind) == statement


def test_maybe_is_a_qualifier():
    assert kinds(f"{OPEN} {BODY} {PAY} Maybe it was a refuge.") == "flagged"


def test_an_authored_hedge_on_a_weak_count_still_stands():
    kept, _ = fact_sheet._apply_rules(
        [DraftFact(id="F1", statement="Miners carved four chapels.", kind="number", quote_ids=[1], disputed=True,
                   hedge="records list four chapels")],
        [{"url": "https://travel-blog.example/x", "quote": "Miners carved four chapels."}], auto_hedge=True)
    assert kept and kept[0]["hedge"] == "records list four chapels"


def _cached(tmp_path, monkeypatch, facts):
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path)
    sheet = {"topic_id": "t9", "built_at": "2026-11-01T00:00:00+00:00", "facts": facts, "sources": [],
             "rules_version": fact_sheet.RULES_VERSION}
    (tmp_path / "t9.json").write_text(json.dumps(sheet), encoding="utf-8")
    return fact_sheet.load("t9", max_age_days=100000)


def test_a_cached_sheet_is_re_checked_in_place_not_researched_again(tmp_path, monkeypatch):
    """Review 2, finding 3: no RULES_VERSION bump (it would research all 20 real topics again)."""
    weak = [{"url": "https://travel-blog.example/x", "tier": 3, "quote": "q", "host": "travel-blog.example", "fetch": ""}]
    count = {"id": "F1", "kind": "number", "disputed": True, "statement": "Miners carved four chapels.",
             "hedge": "by one estimate, miners carved four chapels.", "evidence": weak}
    measure = {"id": "F2", "kind": "number", "disputed": True, "statement": "The city reached 85 meters deep.",
               "hedge": "by one estimate, the city reached 85 meters deep.",
               "evidence": [dict(weak[0], quote="It reached between 60 and 85 meters deep.")]}
    authored = {"id": "F3", "kind": "number", "disputed": True, "statement": "It has 18 levels.",
                "hedge": "researchers have estimated 18 levels", "evidence": weak}
    plain = {"id": "F4", "kind": "other", "disputed": False, "statement": "It is in Turkey.", "hedge": "",
             "evidence": weak}
    sheet = _cached(tmp_path, monkeypatch, [count, measure, authored, plain])
    assert fact_sheet.RULES_VERSION == 2 and sheet is not None
    assert [f["id"] for f in sheet["facts"]] == ["F2", "F3", "F4"]
    assert sheet["facts"][0]["hedge"] == "by one estimate, the city reached 85 meters deep."   # never a range (QF-112)
    assert sheet["facts"][1]["hedge"] == "researchers have estimated 18 levels"


def test_every_real_sheet_still_loads_with_enough_facts():
    """The committed sheets (production ships them): none is researched again for this rule, none falls short."""
    for path in sorted((fact_sheet.DATA_DIR / "fact_sheets").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        sheet = fact_sheet.load(raw["topic_id"], max_age_days=100000)
        if raw.get("rules_version") != fact_sheet.RULES_VERSION:
            assert sheet is None          # built under older rules before this row: unchanged
            continue
        assert sheet is not None and len(sheet["facts"]) >= fact_sheet.MIN_VERIFIED_FACTS
        assert not any(f.get("disputed") and fact_sheet._is_auto_hedge(f)
                       and not narrative_hedges.auto_hedge_allowed(f["statement"], f["kind"]) for f in sheet["facts"])


# ------------------------------------------------------------------------- 2: the opening, payoff and loop (P3)
@pytest.mark.parametrize(("narration", "want"), [
    (f"By one estimate, a shaft drops 280 feet. {BODY} {PAY} {LOOP}", "blocked"),        # the automatic lead
    (f"{OPEN} {BODY} The shaft drops 280 feet into the rock. {LOOP}", "blocked"),        # a disputed figure
    (f"{OPEN} {BODY} {PAY} It drops 85 m.", "blocked"),                                  # its metric form
    (f"{OPEN} {BODY} {PAY} One hundred forty-four chambers wait below.", "blocked"),     # spelled out
    (f"A shaft drops roughly 300 feet below a farm. {BODY} {PAY} {LOOP}", "flagged"),
    (f"{OPEN} {BODY} {PAY} It was most likely a refuge.", "flagged"),
    (f"{OPEN} {BODY} Soot on the walls suggests long use. {LOOP}", "flagged"),
    (f"{OPEN} {BODY} {PAY} Could it have held a whole town?", "clean"),                  # a question
    (f"Can a shaft drop 280 feet by hand? {BODY} {PAY} {LOOP}", "clean"),
    (f"{OPEN} {BODY} {PAY} {LOOP}", "clean"),                                            # undisputed 1963
    (f"{OPEN} By one estimate, the shaft drops 280 feet. It was probably a refuge. {PAY} {LOOP}", "clean"),  # body
    (f"Farms spread around the hidden shaft from 1963. {BODY} {PAY} Some still stand about the valley.", "clean"),
])
def test_hedges_in_the_opening_payoff_and_loop(narration, want):
    assert kinds(narration) == want


def test_without_a_sheet_only_the_automatic_lead_marks_a_disputed_fact():
    assert kinds(f"By one account, it was found in 1963. {BODY} {PAY} {LOOP}", None) == "blocked"
    assert kinds(f"{OPEN} {BODY} The shaft drops 280 feet into the rock. {LOOP}", None) == "clean"


def test_one_quantity_matcher_serves_every_marker():
    """Review 2, finding 4: "By one estimate" with a spelled-out number, "twice", and digits all qualify a quantity."""
    for sentence in ("By one estimate, scans reveal thirty-five bronze gears.",
                     "By one estimate, the dome is one hundred forty-four feet across.",
                     "Its grains measure roughly twice as wide.", "Up to 20,000 people sheltered here.",
                     "The walls are some two millennia old."):
        found = narrative_hedges.markers(sentence)
        assert found and all(q for _, q in found), sentence
    assert narrative_hedges.values("one hundred forty-four feet, thirty-five gears") == [144.0, 35.0]
    assert narrative_hedges.markers("Some still stand around the city.") == []


def test_the_draft_reads_its_own_sheet_and_hedge_findings_file_under_their_own_class(curated_payload):
    token = engine._draft_sheet.set(lambda: SHEET)
    try:
        assert engine.hedge_problems(f"{OPEN} {BODY} The shaft drops 280 feet into the rock. {LOOP}")
    finally:
        engine._draft_sheet.reset(token)
    assert improve.classify(engine.HEDGE_PROBLEM + "the payoff states a disputed fact (280).") == "script.hedge"


def draft(payload, payoff=None, loop=None, opening=None):
    s = engine._split_sentences(payload["narration_script"])
    if opening:
        s[0] = opening
    if payoff:
        s[-2] = payoff
    if loop:
        s[-1] = loop
    return ScriptPackage.model_validate({**payload, "narration_script": " ".join(s), "hook_sentence": s[0],
                                         "payoff_sentence": s[-2], "closing_sentence": s[-1]})


# the loop at the curated loop's length, stating the disputed figure
LONG_LOOP = "For decades after its last residents left, a whole city lay 280 feet beneath ordinary homes, behind a wall."
QUALIFIED_PAYOFF = ("Its earliest tunnels may be nearly three thousand years old, and Byzantine-era Christians later "
                    "expanded it to hide from raids.")   # the curated file's own payoff
CLEAN_PAYOFF = "Byzantine-era Christians later expanded its tunnels to hide from raids."
SHEET_T1 = {"topic_id": "t1", "sources": [], "facts": [
    {"id": "F1", "kind": "number", "disputed": True, "statement": "It reached 280 feet (85 m) deep.",
     "hedge": "by one estimate, it reached 280 feet (85 m) deep.", "evidence": []}]}


def _writer(monkeypatch, first, edited):
    writer = engine.ScriptEngine()
    writer._sheets["t1"] = SHEET_T1
    monkeypatch.setattr(engine, "_ready", lambda: True)
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(engine, "headline_problems", lambda pkg: [])      # this sheet holds only the hedged figure
    monkeypatch.setattr(engine, "cover_problems", lambda pkg, sheet=None: [])
    monkeypatch.setattr(writer, "_sheet", lambda topic: SHEET_T1)
    monkeypatch.setattr(writer, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script})
    monkeypatch.setattr(writer, "_load_curated", lambda topic_id: None)
    written, edits = [], []
    monkeypatch.setattr(writer, "_generate_with_claude", lambda topic, feedback: written.append(feedback) or first)
    monkeypatch.setattr(writer, "_revise_surgically", lambda prev, findings, s: edits.append(findings) or edited)
    return writer, written, edits


def test_a_disputed_fact_in_the_loop_gets_the_one_edit_not_a_rewrite(monkeypatch, curated_payload):
    bad = draft(curated_payload, CLEAN_PAYOFF, LONG_LOOP)
    fixed = draft(curated_payload, CLEAN_PAYOFF)
    writer, written, edits = _writer(monkeypatch, bad, fixed)
    out = writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and len(edits) == 1 and edits[0].startswith("HEDGE: " + engine.HEDGE_PROBLEM)
    assert "the closing loop" in edits[0] and out["narration_script"] == fixed.narration_script


def test_a_disputed_fact_in_the_close_still_blocks_after_its_one_edit(monkeypatch, curated_payload):
    bad = draft(curated_payload, CLEAN_PAYOFF, LONG_LOOP)
    writer, written, edits = _writer(monkeypatch, bad, bad)
    with pytest.raises(engine.ScriptUnavailableError):
        writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 3 and len(edits) == 1                     # three drafts, the one edit


def test_a_qualified_payoff_gets_one_edit_then_ships_flagged(monkeypatch, curated_payload):
    qualified = draft(curated_payload, QUALIFIED_PAYOFF)
    writer, written, edits = _writer(monkeypatch, qualified, qualified)
    out = writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and len(edits) == 1 and edits[0].startswith("HEDGE: " + engine.HEDGE_WORDING_PROBLEM)
    assert out["narration_script"] == qualified.narration_script            # shipped, not ended without a script
    note = engine.hedge_note(out["narration_script"], SHEET_T1)
    assert note.startswith("⚠️ Hedged payoff: ") and "nearly three thousand" in note


def test_an_unchanged_qualified_close_gets_no_second_edit(monkeypatch, curated_payload):
    qualified = draft(curated_payload, QUALIFIED_PAYOFF)
    writer, _, edits = _writer(monkeypatch, qualified, qualified)
    writer.generate_script({"id": "t1", "title": "T"})
    writer.generate_script({"id": "t1", "title": "T"})
    assert [e.split(":", 1)[0] for e in edits] == ["HEDGE"]


def test_the_hedge_flag_is_on_the_review_card_and_never_published(curated_payload):
    from src import probe, publishable
    from src.telegram_bot import TelegramApprovalBot
    qualified = draft(curated_payload, QUALIFIED_PAYOFF)
    note = engine.hedge_note(qualified.narration_script, None)
    card = TelegramApprovalBot.format_card_text({"title": "T", "narration_script": qualified.narration_script,
                                                 "caption": "c", "hedge_note": note}, "run_x")
    assert "<b>Hedged payoff:</b>" in card and "Judge whether it should stand on a firmer fact" in card
    assert "hedge_note" not in publishable.ENVELOPE_FIELDS and "hedge_note" not in publishable.PUBLISHABLE_FIELDS
    assert "hedge_note" in probe.NOTE_KEYS
    blocked = draft(curated_payload, CLEAN_PAYOFF, "By one estimate, " + LONG_LOOP[0].lower() + LONG_LOOP[1:])
    assert engine.hedge_note(blocked.narration_script, None) == ""          # a blocking fault is never a flag
    assert engine.hedge_note(draft(curated_payload, CLEAN_PAYOFF).narration_script, None) == ""


def test_verify_claims_refuses_a_disputed_close_before_any_provider(monkeypatch, curated_payload):
    bad = draft(curated_payload, CLEAN_PAYOFF, "By one estimate, " + LONG_LOOP[0].lower() + LONG_LOOP[1:])
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    writer = engine.ScriptEngine()
    monkeypatch.setattr(writer.fact_checker, "verify", lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider")))
    monkeypatch.setattr(engine.free_llm, "enabled", lambda: False)
    passed, _, reason = writer.verify_claims(bad.model_dump())
    assert not passed and engine.HEDGE_PROBLEM in reason


def test_verify_claims_does_not_refuse_a_qualified_close(monkeypatch, curated_payload):
    qualified = draft(curated_payload, QUALIFIED_PAYOFF)
    monkeypatch.setattr(engine, "validate_structure", lambda pkg: [])
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    writer = engine.ScriptEngine()
    reached = []
    monkeypatch.setattr(writer.fact_checker, "verify", lambda *a, **k: reached.append(1) or (False, 0.0, [], "stop"))
    monkeypatch.setattr(engine.free_llm, "enabled", lambda: False)
    writer.verify_claims(qualified.model_dump())
    assert reached


def test_the_prompts_keep_hedges_in_the_body():
    assert "never in the first sentence, the payoff or the final loop sentence" in engine.SYSTEM_PROMPT
    assert "A hedged claim stays in the body" in engine.REVISE_SYSTEM
    note = engine.mandatory_hedges_note(SHEET)
    assert "never the first sentence, the payoff or the final loop" in note


def test_the_hedge_edit_names_its_sentences_and_keeps_the_payoff_marked():
    findings = engine.opening_edit_findings([engine.HEDGE_PROBLEM + "the payoff (\"x\") states a disputed fact."])
    assert findings.startswith("HEDGE: ") and "payoff_sentence is it word for word" in findings
    assert "Never delete a needed hedge" in findings
