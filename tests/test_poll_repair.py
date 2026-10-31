"""A yes/no poll question over two contrary choices is reworded to the either/or they answer, without a
writer attempt (production, Hal Saflieni 2026-10-27: the last of three drafts failed only on "Was the
acoustic design intentional?" with "Deliberate design" / "Accidental resonance"). The topics below are
test data only."""
from src import script_engine
from src.models import ScriptPackage
from src.script_engine import ScriptEngine, poll_problems, repair_poll


def _repaired(question, options, narration=""):
    """The poll as the draft would carry it after normalize_copy: reworded when that applies."""
    return repair_poll(question, options, narration) or question


def test_yes_no_questions_over_two_choices_are_reworded_to_the_choice():
    q = _repaired("Was the acoustic design intentional?", ["Deliberate design", "Accidental resonance"])
    assert q == "Deliberate design or accidental resonance?" and not poll_problems(q, ["Deliberate design", "Accidental resonance"])
    q = _repaired("Did the Amber Room survive?", ["Burned in 1944", "Hidden somewhere"])
    assert q == "Burned in 1944 or hidden somewhere?" and not poll_problems(q, ["Burned in 1944", "Hidden somewhere"])
    # Before the repair, the validator alone still names the mismatch.
    assert poll_problems("Was the acoustic design intentional?", ["Deliberate design", "Accidental resonance"])


def test_yes_no_answers_with_more_words_pass_as_they_are():
    for options in (["Yes, easily", "No chance"], ["Yes", "Nope"]):
        assert repair_poll("Could modern tools match this speed?", options) is None  # nothing to reword
        assert not poll_problems("Could modern tools match this speed?", options), options


def test_a_name_keeps_its_capital_in_the_reworded_question():
    narration = "The panels were packed in crates. Many say the Baltic coast hid them."
    assert repair_poll("Was it moved?", ["Shipped west", "Baltic coast"], narration) == "Shipped west or Baltic coast?"
    assert repair_poll("Was it moved?", ["Shipped west", "Baltic coast"]) == "Shipped west or baltic coast?"


def test_a_genuinely_mismatched_poll_still_fails():
    # One yes/no answer and one choice: no question both answer.
    assert repair_poll("Is it old?", ["Yes", "Hidden somewhere"]) is None
    assert poll_problems("Is it old?", ["Yes", "Hidden somewhere"])
    assert poll_problems("Did it survive?", ["No", "Burned in 1944"])
    # Over-long options are not reworded into an over-long question (they fail their own limit).
    assert repair_poll("Was it planned?", ["A deliberately tuned stone chamber", "An accidental resonance of rock"]) is None
    # Either/or and "which" questions are not reworded.
    assert repair_poll("Which took more work?", ["Planning", "Labour"]) is None
    assert repair_poll("Planned or lucky?", ["Careful planning", "Lucky accident"]) is None


def test_a_poll_only_fault_costs_no_writer_attempt(monkeypatch, curated_payload):
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    engine = ScriptEngine()
    monkeypatch.setattr(engine, "_sheet", lambda topic: None)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"poll": pkg.poll.model_dump()})
    draft = dict(curated_payload, poll={"question": "Was the acoustic design intentional?",
                                        "options": ["Deliberate design", "Accidental resonance"]})
    assert not script_engine.validate_structure(ScriptPackage.model_validate(dict(curated_payload)))  # the rest is fine
    written = []
    monkeypatch.setattr(engine, "_generate_with_claude",
                        lambda topic, feedback: written.append(feedback) or ScriptPackage.model_validate(draft))
    out = engine.generate_script({"id": "t", "title": "T"})
    assert len(written) == 1 and engine.last_drafts == 1
    assert out["poll"]["question"] == "Deliberate design or accidental resonance?"


def test_structure_and_length_are_reported_together(monkeypatch, curated_payload):
    # A draft sent back for its structure alone came back the wrong length, costing the next attempt too.
    from src import audio_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 39.0)
    monkeypatch.setattr(script_engine, "record_word_pace", lambda spw, text="": None)
    bad = ScriptPackage.model_validate(dict(curated_payload, title="x" * 70))
    problems = script_engine.draft_problems(bad)
    assert any("Title is 70 characters" in p for p in problems) and any("when spoken" in p for p in problems)
