"""QF-150: a script-stage lint that stops narration phrasing the captions cannot break well, before it is voiced.

The captions never end a burst or a line on a never-end word where a valid break exists (video_composer
never_end_end, _never_end_repair); where none exists under the caps and the bonds, the composer records the defect
("DEDICATED IN |", "AT / THE CATHERINE PALACE"). Lander ruling: recorded at render, fixed at source. The lint plans the
narration with the composer's own caption_phrases at nominal timing, names each sentence that keeps such a break, and
the draft gets one narrow surgical edit (no call when nothing is flagged, none when the draft is going back anyway),
kept only when nothing else changed: no other sentence, no figure, no new blocking fault. It never blocks a draft;
what stays is recorded (payload["caption_lint"]). No paid calls: the writer is stubbed."""
import itertools

import pytest

from src import improve, script_engine
from src import video_composer as vc
from src.models import ScriptPackage
from src.script_engine import (
    CAPTION_BREAK_PROBLEM,
    ScriptEngine,
    blocking_problems,
    caption_break_findings,
    caption_break_problems,
    caption_break_record,
    caption_tokens,
    nominal_caption_words,
)

FLAGGED = "Dedicated in AD 128 in Italy, it holds the world's largest unreinforced concrete dome."
FIXED = "In Italy, the temple was dedicated in AD 128, and it holds the world's largest unreinforced concrete dome."
FIGURE_CHANGED = "In Italy, the temple was dedicated in AD 125, and it holds the world's largest unreinforced concrete dome."
BASE = ("The old dome still stands after nearly two thousand years. Builders mixed hot lime into the wet mortar. "
        "Rain seeps into the cracks and the lime seals them again.")
CLEAN = BASE + " The dome still holds."
DIRTY = BASE + " " + FLAGGED + " The dome still holds."
SHEET = {"topic_id": "t1", "sources": [], "facts": [{"id": "f1", "statement": "x", "kind": "fact", "disputed": False,
                                                     "hedge": "", "evidence": []}]}


def _defects(phrases):
    """The composer's own records (reading_rates' never_end and line_defect)."""
    return vc.never_end_bursts(phrases) + vc.line_defect_bursts(phrases)


# ------------------------------------------------------------------------------------------------ the prediction
def test_nominal_words_are_the_voice_tokens_with_rising_times():
    words = nominal_caption_words("King Louis XIV built it (85 m) high. Then, it fell.")
    assert [w["word"] for w in words] == caption_tokens("King Louis XIV built it (85 m) high. Then, it fell.")
    assert "(85" not in " ".join(w["word"] for w in words)                    # the aside is not said
    starts = [w["start"] for w in words]
    assert starts == sorted(starts) and all(w["dur"] > 0 for w in words)
    assert all(w["start"] + w["dur"] <= n["start"] for w, n in itertools.pairwise(words))
    # a sentence end pauses longer than a clause mark, and both longer than a plain gap
    gap = {w["word"]: n["start"] - w["start"] for w, n in itertools.pairwise(words)}
    assert gap["high."] > gap["Then,"] > gap["built"]


@pytest.mark.parametrize("narration", [CLEAN, DIRTY, BASE + " " + FIXED + " The dome still holds."])
def test_findings_are_exactly_the_composers_records_on_the_same_words(narration):
    """The lint is the composer's own planning: on any timed words, its findings are the defects the render records."""
    words = nominal_caption_words(narration)
    found = caption_break_findings(narration, words=words)
    recorded = _defects(vc.caption_phrases([dict(w) for w in words]))
    assert sorted(f["shown"].rstrip(" |") for f in found) == sorted(recorded)


def test_a_sentence_with_no_valid_break_is_flagged_by_name_and_its_rephrase_is_not():
    found = caption_break_findings(DIRTY)
    assert found and {f["sentence"] for f in found} == {FLAGGED}
    assert found[0]["kind"] == "burst" and found[0]["shown"].endswith("in |")
    assert vc.never_end_end(found[0]["shown"].split()[-2])                   # it ends on a never-end word
    assert caption_break_findings(CLEAN) == []
    assert caption_break_findings(BASE + " " + FIXED + " The dome still holds.") == []


def test_the_problem_names_the_sentence_and_the_break_and_asks_for_a_rephrase_keeping_facts():
    problems = caption_break_problems(DIRTY)
    assert len(problems) == 1 and problems[0].startswith(CAPTION_BREAK_PROBLEM)
    assert f"“{FLAGGED}”" in problems[0] and "“Dedicated in |”" in problems[0]
    assert "Rephrase that clause" in problems[0] and "keeping every fact, figure, name and hedge" in problems[0]
    assert script_engine._caption_break_sentences(problems) == [FLAGGED]
    assert caption_break_problems(CLEAN) == []


def test_the_lint_never_blocks_a_draft_and_is_filed_in_its_own_class():
    problems = caption_break_problems(DIRTY)
    assert problems and blocking_problems(problems) == []
    assert improve.classify(problems[0]) == "script.caption_break"


def test_a_planning_failure_is_advice_lost_not_a_stopped_draft(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no fonts")
    monkeypatch.setattr(vc, "caption_phrases", boom)
    assert caption_break_problems(DIRTY) == []
    assert caption_break_record(DIRTY) == {"checked": False, "error": "no fonts"}


def test_the_record_names_each_kept_break():
    rec = caption_break_record(DIRTY)
    assert rec == {"checked": True, "bursts": ["Dedicated in |"], "lines": [], "sentences": 1}
    assert caption_break_record(CLEAN) == {"checked": True, "bursts": [], "lines": [], "sentences": 0}


def test_the_lint_is_wired_into_every_drafts_problems():
    pkg = ScriptPackage.model_construct(narration_script=DIRTY, emphasis_words=None)
    import inspect
    src = inspect.getsource(script_engine.draft_problems)
    assert "caption_break_problems(pkg.narration_script, pkg.emphasis_words)" in src
    assert caption_break_problems(pkg.narration_script, pkg.emphasis_words)


# ------------------------------------------------------------------------------------------------ the one edit
def _engine(monkeypatch, curated_payload, narration, other=()):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine.improve, "note_problems", lambda problems, source: None)
    # the draft's other checks stubbed: only the real caption lint (and any `other` problem) is reported
    monkeypatch.setattr(script_engine, "draft_problems", lambda pkg: list(other) + caption_break_problems(
        pkg.narration_script, pkg.emphasis_words))
    pkg = ScriptPackage.model_validate(dict(curated_payload, narration_script=narration, emphasis_words=None,
                                            sentence_trace=[{"sentence": s, "fact_ids": ["f1"]}
                                                            for s in script_engine._split_sentences(narration)]))
    return engine, pkg


def _stub_writer(monkeypatch, engine, replace_with):
    calls = []

    def revise(previous, findings, sheet):
        calls.append(findings)
        return ScriptPackage.model_validate(dict(previous, narration_script=replace_with(previous["narration_script"])))
    monkeypatch.setattr(engine, "_revise_surgically", revise)
    return calls


def test_no_writer_call_when_nothing_is_flagged(monkeypatch, curated_payload):
    engine, pkg = _engine(monkeypatch, curated_payload, CLEAN)
    calls = _stub_writer(monkeypatch, engine, lambda n: n)
    out, problems = engine._narrow_edits(pkg, script_engine.draft_problems(pkg), SHEET)
    assert calls == [] and out is pkg and problems == []


def test_a_flagged_sentence_gets_one_edit_that_is_kept_when_it_fixes_the_break(monkeypatch, curated_payload):
    engine, pkg = _engine(monkeypatch, curated_payload, DIRTY)
    calls = _stub_writer(monkeypatch, engine, lambda n: n.replace(FLAGGED, FIXED))
    out, problems = engine._narrow_edits(pkg, script_engine.draft_problems(pkg), SHEET)
    assert len(calls) == 1 and calls[0].startswith("CAPTION: " + CAPTION_BREAK_PROBLEM)
    assert FLAGGED in calls[0]
    assert FIXED in out.narration_script and problems == []
    assert any(row.sentence == FIXED for row in out.sentence_trace)          # the trace follows the reworded sentence
    assert engine.last_caption_edit == {"asked": 1, "kept": True, "left": 0}
    # asked once per narration: the same narration again costs no call
    engine._narrow_edits(pkg, script_engine.draft_problems(pkg), SHEET)
    assert len(calls) == 1


@pytest.mark.parametrize("change", [
    lambda n: n.replace(FLAGGED, FIGURE_CHANGED),                                         # a figure changed
    lambda n: n.replace(FLAGGED, FIXED).replace("hot lime", "quicklime"),                 # another sentence changed
    lambda n: n.replace(FLAGGED, FIXED + " It still stands."),                            # a sentence added
    lambda n: n,                                                                          # nothing fixed
])
def test_an_edit_that_changes_facts_or_other_sentences_or_fixes_nothing_is_not_kept(monkeypatch, curated_payload,
                                                                                     change):
    engine, pkg = _engine(monkeypatch, curated_payload, DIRTY)
    calls = _stub_writer(monkeypatch, engine, change)
    out, problems = engine._narrow_edits(pkg, script_engine.draft_problems(pkg), SHEET)
    assert len(calls) == 1 and out is pkg
    assert [p for p in problems if p.startswith(CAPTION_BREAK_PROBLEM)]      # recorded, never silently dropped
    assert engine.last_caption_edit == {"asked": 1, "kept": False}


def test_an_edit_that_brings_a_blocking_fault_is_not_kept(monkeypatch, curated_payload):
    engine, pkg = _engine(monkeypatch, curated_payload, DIRTY)
    _stub_writer(monkeypatch, engine, lambda n: n.replace(FLAGGED, FIXED))
    monkeypatch.setattr(script_engine, "draft_problems", lambda p: (
        ["Narration runs 60.0s when spoken (maximum 50s)."] if FIXED in p.narration_script else [])
        + caption_break_problems(p.narration_script))
    out, _ = engine._narrow_edits(pkg, script_engine.draft_problems(pkg), SHEET)
    assert out is pkg


def test_a_draft_going_back_for_a_rewrite_spends_no_caption_edit(monkeypatch, curated_payload):
    engine, pkg = _engine(monkeypatch, curated_payload, DIRTY, other=["Missing hashtags entirely."])
    calls = _stub_writer(monkeypatch, engine, lambda n: n.replace(FLAGGED, FIXED))
    out, problems = engine._narrow_edits(pkg, script_engine.draft_problems(pkg), SHEET)
    assert calls == [] and out is pkg and any(p.startswith(CAPTION_BREAK_PROBLEM) for p in problems)


def test_no_sheet_no_edit(monkeypatch, curated_payload):
    engine, pkg = _engine(monkeypatch, curated_payload, DIRTY)
    calls = _stub_writer(monkeypatch, engine, lambda n: n.replace(FLAGGED, FIXED))
    engine._narrow_edits(pkg, script_engine.draft_problems(pkg), None)
    assert calls == []


def test_the_reviser_is_told_what_a_caption_finding_asks():
    rules = script_engine.REVISE_SYSTEM
    assert "A CAPTION finding" in rules and "keep every fact, figure, name, date" in rules
    assert "Never add or remove a sentence" in rules


# ------------------------------------------------------------------------------------------------ the record
def test_finalize_records_the_lint_and_the_stage_records_it_on_the_checked_narration(curated_payload):
    pkg = ScriptPackage.model_validate(dict(curated_payload, narration_script=DIRTY, emphasis_words=None))
    payload = ScriptEngine._finalize(pkg, {"id": "t1"}, origin="claude")
    assert payload["caption_lint"] == {"checked": True, "bursts": ["Dedicated in |"], "lines": [], "sentences": 1,
                                       "edit": None}
    from pathlib import Path
    main = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
    assert 'payload["caption_lint"] = {**caption_break_record(payload.get("narration_script")' in main


def test_the_lint_names_no_topic():
    """Generic only: the lint reads the composer's rules, never a list of places, names or topics."""
    import inspect
    src = "".join(inspect.getsource(f) for f in (
        caption_break_findings, caption_break_problems, caption_break_record, nominal_caption_words,
        ScriptEngine._fix_caption_breaks))
    for word in ("Italy", "Catherine", "Byzantine", "Podkamennaya", "Germuş", "Amber", "topic_id"):
        assert word not in src
