"""Fewer wasted rounds in the script stage, with the same gates (production 2026-10-24: a run spent 8 min
on five rounds). A length-only miss is edited to the window instead of rewritten; a draft the first
checker rejects also gets the second checker's objections, so one revision fixes both; a second check
that is unavailable is tried again before the run is lost; the improvement cycle writes where the
pipeline account may."""

import re

import pytest

from src import script_engine, trace_check
from src.models import ScriptPackage
from src.script_engine import ScriptEngine

SHEET = {"topic_id": "t1", "facts": [], "sources": [{"title": "X", "url": "https://en.wikipedia.org/wiki/X", "tier": 2}]}


def _engine(monkeypatch, curated_payload):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(engine, "_sheet_context", lambda sheet: ("notes", []))
    monkeypatch.setattr(engine, "_with_graphics", lambda core, notes, retrieved, feedback: core)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script, **k})
    monkeypatch.setattr(script_engine, "validate_structure", lambda pkg: [])
    # Length by word count, stated the way the real check states it (0.4 s a word).
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [] if len(pkg.narration_script.split()) >= 100 else [
        f"Narration runs {len(pkg.narration_script.split()) * 0.4:.1f}s when spoken (minimum 42s). This draft is "
        f"{len(pkg.narration_script.split())} words; extend the narration to 105-116 words with more verified facts "
        "from the research notes, keeping the closing sentence."])
    closing = curated_payload["closing_sentence"]
    short = ScriptPackage.model_validate(dict(curated_payload, narration_script=" ".join(["word"] * 80) + ". " + closing))
    return engine, short, closing


def test_a_length_only_miss_is_edited_to_the_window_not_rewritten(monkeypatch, curated_payload):
    engine, short, closing = _engine(monkeypatch, curated_payload)
    written, edited = [], []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(feedback) or short)

    def revise(previous, findings, sheet):
        edited.append(findings)
        return ScriptPackage.model_validate(dict(previous, narration_script=" ".join(["word"] * 108) + ". " + closing))
    monkeypatch.setattr(engine, "_revise_surgically", revise)
    out = engine.generate_script({"id": "t", "title": "T"})
    length = [e for e in edited if e.startswith("LENGTH:")]
    assert len(written) == 1 and len(length) == 1           # one draft, one length edit: no second full rewrite
    assert "105-116 words" in length[0]
    # Its 108-word first sentence then gets one opening edit (2026-10-29), which this stub cannot fix: kept as was.
    assert [e.split(":", 1)[0] for e in edited] == ["LENGTH", "OPENING"]
    assert len(out["narration_script"].split()) == 108 + len(closing.split()) and out["fact_sheet_used"] is True


def test_a_length_edit_that_still_misses_falls_back_to_the_rewrite(monkeypatch, curated_payload):
    engine, short, closing = _engine(monkeypatch, curated_payload)
    written = []

    def write(topic, feedback):
        written.append(feedback)
        words = 112 if re.search(r"extend the narration to", feedback) else 80
        return ScriptPackage.model_validate(dict(curated_payload, narration_script=" ".join(["word"] * words) + ". " + closing))
    monkeypatch.setattr(engine, "_generate_with_claude", write)
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: short)  # no better
    out = engine.generate_script({"id": "t", "title": "T"})
    assert len(written) == 2 and "<previous_narration>" in written[1]
    assert len(out["narration_script"].split()) == 112 + len(closing.split())


def _checked(monkeypatch, curated_payload, first, second):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: True)
    monkeypatch.setattr(script_engine, "SECOND_CHECK_ENABLED", True)
    monkeypatch.setattr(script_engine.fact_sheet, "load", lambda topic_id, max_age_days=0: SHEET)
    monkeypatch.setattr(trace_check, "check", lambda p, s: first)
    calls = []
    monkeypatch.setattr(trace_check, "evidence_check", lambda p, s: calls.append(1) or second[min(len(calls), len(second)) - 1])
    monkeypatch.setattr(script_engine, "_sleep", lambda s: calls.append(("slept", s)))
    payload = dict(curated_payload, topic_id="t1", origin="gemini-free", fact_sheet=True)
    passed, out, notes = engine.verify_claims(payload)
    return passed, out, notes, calls


def test_a_rejected_draft_also_carries_the_second_checkers_objections(monkeypatch, curated_payload):
    first = (False, 50.0, [], "1 untraceable claim(s): 'The temple is a 100-foot monolith' (title; untraced: no such fact)")
    second = [("fail", 50.0, [], "[evidence re-check] 1 blocking claim(s): 'Built before farming began here' (unsupported: no quote)")]
    passed, out, notes, calls = _checked(monkeypatch, curated_payload, first, second)
    assert not passed and notes.startswith("REJECTED by fact checker: 1 untraceable")
    assert "ALSO REJECTED by second fact checker: [evidence re-check] 1 blocking" in notes
    assert "second_check" not in out                       # the final draft's verdict is the one recorded
    # The surgical reviser finds both claims in the findings.
    assert re.findall(r"'([^']{25,})'\s*\(", notes) == ["The temple is a 100-foot monolith", "Built before farming began here"]


def test_a_rejected_draft_without_second_objections_reads_as_before(monkeypatch, curated_payload):
    first = (False, 50.0, [], "1 untraceable claim(s): 'A claim the sheet does not hold' (narration; untraced: x)")
    passed, _, notes, calls = _checked(monkeypatch, curated_payload, first, [("pass", 100.0, [], "all supported")])
    assert not passed and "ALSO" not in notes and calls == [1]


def test_an_unavailable_second_check_is_tried_again_before_the_run_is_lost(monkeypatch, curated_payload):
    first = (True, 100.0, [], "All 12 claims trace to the verified fact sheet.")
    second = [("unavailable", 0.0, [], "quota"), ("pass", 100.0, [], "[evidence re-check] All 12 claims supported.")]
    passed, out, notes, calls = _checked(monkeypatch, curated_payload, first, second)
    assert passed and out["second_check"]["status"] == "pass"
    assert calls == [1, ("slept", script_engine.SECOND_CHECK_RETRY_SECONDS), 1]


def test_a_second_check_that_stays_unavailable_still_fails_closed(monkeypatch, curated_payload):
    first = (True, 100.0, [], "All claims trace.")
    passed, out, notes, calls = _checked(monkeypatch, curated_payload, first, [("unavailable", 0.0, [], "quota")])
    assert not passed and "could not run" in notes and out["second_check"]["status"] == "unavailable"
    assert calls.count(1) == script_engine.SECOND_CHECK_ATTEMPTS


def test_improvement_queue_and_evals_write_where_the_pipeline_account_may(monkeypatch, tmp_path):
    from src import config, improve
    from scripts import eval_prompts
    blocker = tmp_path / "docs"
    blocker.write_text("a file where the folder should be", encoding="utf-8")  # the tree is not writable
    monkeypatch.setattr(improve, "QUEUE_FILE", blocker / "improvements" / "queue.md")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    assert improve._append_queue("\n## imp_1\n") == "data/improvements/queue.md"
    assert (tmp_path / "data" / "improvements" / "queue.md").read_text(encoding="utf-8").strip() == "## imp_1"
    assert eval_prompts.OUT == config.DATA_DIR / "evals" / "prompts" or "data" in str(eval_prompts.OUT)


def test_the_evidence_re_check_runs_on_gemini_for_a_claude_writer_too(monkeypatch, curated_payload):
    # Writer trial 2026-10-24: with SCRIPT_ENGINE=claude the second opinion went to Fable with web search
    # ($1.70 a call) for scripts that already had a verified fact sheet.
    from src import config
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: False)            # the Claude engine
    monkeypatch.setattr(script_engine, "SECOND_CHECK_ENABLED", True)
    monkeypatch.setattr(script_engine.fact_sheet, "load", lambda topic_id, max_age_days=0: SHEET)
    monkeypatch.setattr(trace_check, "check", lambda p, s: (True, 100.0, [], "All claims trace."))
    monkeypatch.setattr(config, "GEMINI_API_KEY", "paid-key-456")
    seen = []
    monkeypatch.setattr(script_engine.free_llm, "structured", lambda *a, **k: seen.append(k.get("role")) or trace_check.EvidenceReport(claims=[]))
    monkeypatch.setattr(engine.fact_checker, "second_opinion", lambda p: pytest.fail("the web-searching second opinion was used"))
    passed, out, notes = engine.verify_claims(dict(curated_payload, topic_id="t1", origin="claude", fact_sheet=True))
    assert seen == ["checker"] and out["second_check"]["status"] == "fail"   # no reviewed claims: fail closed, on Gemini
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(script_engine.free_llm, "available", lambda: False)
    monkeypatch.setattr(engine.fact_checker, "second_opinion", lambda p: ("pass", 100.0, [], "web check ok"))
    passed, out, notes = engine.verify_claims(dict(curated_payload, topic_id="t1", origin="claude", fact_sheet=True))
    assert passed and "web check ok" in notes                                   # no Gemini key: the old path
