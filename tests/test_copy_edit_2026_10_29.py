"""Production, Roman self-healing concrete 2026-10-29 (rc69, daily run 13:01-13:12 UTC): all three writer attempts
were spent, the second on a 615-character caption first line (with a length miss), the third on a yes/no poll whose
options were two choices. A fault confined to the copy around the narration cost a whole rewrite, which re-rolled the
narration and its length. Such faults are now fixed by a deterministic repair where one exists (the caption line) or
one short edit of those fields only (the copy edit), the narration kept byte for byte; a length miss with them gets
the length edit too. Every result is checked again in full; an edit that leaves or adds a fault falls back to the
rewrite. The LLM is stubbed throughout."""
import pytest

from src import script_engine
from src.models import ScriptPackage
from src.script_engine import (ScriptEngine, _CopyEdit, copy_problem_fields, normalize_copy, split_caption_first_line,
                               validate_structure)

SHEET = {"topic_id": "t1", "sources": [{"title": "X", "url": "https://en.wikipedia.org/wiki/X", "tier": 2}],
         "facts": [{"id": "f1", "statement": "Roman concrete holds lime clasts.", "kind": "other", "disputed": False,
                    "hedge": "", "evidence": []}]}
MIXED_POLL = {"question": "Can modern builders replicate Rome's durability?",
              "options": ["Yes, with hot mixing", "Modern cement fails"]}
LONG_SENTENCE = ("Roman harbor concrete has stood in seawater for two thousand years while lime clasts inside it quietly "
                 "sealed its cracks, which modern engineers are only now beginning to measure")   # one sentence, > 125
LENGTH = ("Narration runs 41.2s when spoken (minimum 42s). This draft is {n} words; extend the narration to 105-110 "
          "words with more verified facts from the research notes, keeping the closing sentence.")


def _long_first_line(caption: str) -> str:
    """The caption with its paragraphs run together on one line (615 characters in production)."""
    return " ".join(ln.strip() for ln in caption.splitlines() if ln.strip())


def _draft(payload, words=108, **update):
    closing = payload["closing_sentence"]
    body = " ".join(f"w{i}" for i in range(words - len(closing.split())))
    return ScriptPackage.model_validate(dict(payload, narration_script=f"{body}. {closing}", **update))


@pytest.fixture
def engine(monkeypatch, curated_payload):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: False)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(engine, "_sheet_context", lambda sheet: ("notes", []))
    monkeypatch.setattr(engine, "_with_graphics", lambda core, notes, retrieved, feedback: core)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: dict(pkg.model_dump(), **k))
    monkeypatch.setattr(script_engine, "hook_problems", lambda narration: [])
    monkeypatch.setattr(script_engine.improve, "note_problems", lambda problems, source: None)
    monkeypatch.setattr(script_engine.improve, "note", lambda *a, **k: None)
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [] if 105 <= len(pkg.narration_script.split())
                        <= 110 else [LENGTH.format(n=len(pkg.narration_script.split()))])
    engine.written, engine.edits, engine.copy_calls = [], [], []
    return engine


def _writer(monkeypatch, engine, *drafts):
    """The writer returns these drafts in turn; the copy edit's model and the surgical reviser are stubbed apart."""
    def write(topic, feedback):
        engine.written.append(feedback)
        return drafts[min(len(engine.written), len(drafts)) - 1]
    monkeypatch.setattr(engine, "_generate_with_claude", write)


def _copy_model(monkeypatch, engine, reply):
    def call(system, prompt, schema, **k):
        assert schema is _CopyEdit and system == script_engine.COPY_EDIT_SYSTEM
        engine.copy_calls.append(prompt)
        return reply(prompt) if callable(reply) else reply
    monkeypatch.setattr(script_engine, "structured_call", call)


def _lengthen(monkeypatch, engine, words=108):
    def revise(previous, findings, sheet):
        engine.edits.append(findings)
        closing = previous["closing_sentence"]
        body = " ".join(f"w{i}" for i in range(words - len(closing.split())))
        return ScriptPackage.model_validate(dict(previous, narration_script=f"{body}. {closing}"))
    monkeypatch.setattr(engine, "_revise_surgically", revise)


# ------------------------------------------------------------------ the deterministic caption repair
def test_a_long_first_line_is_broken_after_the_sentences_that_fit(curated_payload):
    caption = _long_first_line(curated_payload["caption"])
    assert len(caption.splitlines()[0]) > 125
    fixed = split_caption_first_line(caption)
    first = fixed.splitlines()[0]
    assert len(first) <= 125 and first.endswith(".")
    assert " ".join(fixed.split()) == " ".join(caption.split())            # same words, only a line break added
    pkg = normalize_copy(ScriptPackage.model_validate(dict(curated_payload, caption=caption)))
    assert pkg.caption == fixed and not [p for p in validate_structure(pkg) if p.startswith("Caption's first line is")]


def test_the_repair_keeps_a_closing_quote_an_emoji_and_puts_trailing_hashtags_on_their_own_line():
    caption = ("They called it “stone that heals.” 🏛️ " + "Lime clasts sealed cracks for centuries in harbor walls. " * 3
               + "Could we build like this? #RomanConcrete #AncientRome #Engineering")
    fixed = split_caption_first_line(caption).splitlines()
    assert len(fixed[0]) <= 125 and fixed[0].startswith("They called it “stone that heals.” 🏛️")
    assert " ".join(" ".join(fixed).split()) == " ".join(caption.split())
    assert fixed[-1] == "#RomanConcrete #AncientRome #Engineering"


def test_an_emoji_ends_a_sentence_without_a_full_stop():
    caption = "Rome's concrete heals its own cracks 🏛️ " + "Lime clasts inside it react with seawater over years. " * 3
    first = split_caption_first_line(caption).splitlines()[0]
    assert first == "Rome's concrete heals its own cracks 🏛️ Lime clasts inside it react with seawater over years."
    caption = "Rome's concrete heals its own cracks 🏛️ " + "Lime clasts inside the harbor walls react with seawater and seal their cracks over many long years. " * 3
    assert split_caption_first_line(caption).splitlines()[0] == "Rome's concrete heals its own cracks 🏛️"


def test_the_first_line_takes_as_many_whole_sentences_as_fit_not_only_the_first():
    caption = ("One renovation. It moved the gate forty metres and buried the old road under a market square. "
               "Nobody noticed for centuries. Why did it take so long? #A #B")
    lines = split_caption_first_line(caption).splitlines()
    assert lines[0] == ("One renovation. It moved the gate forty metres and buried the old road under a market square. "
                        "Nobody noticed for centuries.")
    assert len(lines[0]) <= 125 and lines[1:] == ["Why did it take so long?", "#A #B"]
    # an emoji after a sentence stays with it, and a first sentence that alone fits is still kept when the next does not
    caption = "Done here! 🏛️ " + "and on " * 20 + "end. #Tag"
    assert split_caption_first_line(caption).splitlines()[0] == "Done here! 🏛️"


def test_a_single_sentence_over_the_limit_is_left_for_the_copy_edit():
    caption = LONG_SENTENCE + ".\n\nWhat else did Rome know? #RomanConcrete #AncientRome #Engineering"
    assert split_caption_first_line(caption) == caption
    assert split_caption_first_line("Short line.\n\nMore.") == "Short line.\n\nMore."


# ------------------------------------------------------------------ which faults are the copy's
def test_copy_faults_are_told_apart_from_narration_faults(curated_payload):
    pkg = ScriptPackage.model_validate(curated_payload)
    assert copy_problem_fields("Caption's first line is 615 characters; keep it within the 125 shown before 'more'.", pkg) == ("caption",)
    assert copy_problem_fields(script_engine.poll_problems(MIXED_POLL["question"], MIXED_POLL["options"])[0], pkg) == ("poll",)
    assert copy_problem_fields("Engagement bait in the story slide 2 ('Tag a friend'): ...", pkg) == ("story_slides",)
    assert copy_problem_fields("Engagement bait in the narration's closing sentence ('x'): ...", pkg) is None
    # the captions' hashtag lines are edited with the list, so a tag changed in one is not left in the other
    assert copy_problem_fields("Generic hashtags ['#viral']; use specific subject tags.", pkg) == (
        "hashtags", "caption", "carousel_caption")
    assert copy_problem_fields(LENGTH.format(n=90), pkg) is None
    assert copy_problem_fields(script_engine.REPEAT_PROBLEM + "'a b c'; say it once", pkg) is None
    assert copy_problem_fields("Narration ends on trailing word 'the'.", pkg) is None
    word = pkg.narration_script.split()[3].strip(".,").lower()
    # a banned phrase in the narration is the narration's; only in the copy, the copy's
    assert copy_problem_fields(f"Uses machine-sounding phrasing ({word}); rewrite in plain documentary language.", pkg) is None
    with_tapestry = pkg.model_copy(update={"first_comment": "What a tapestry of stone?"})
    assert copy_problem_fields("Uses machine-sounding phrasing (tapestry); rewrite in plain documentary language.",
                               with_tapestry) == ("first_comment",)


# ------------------------------------------------------------------ the three production faults
def test_attempt_one_a_length_miss_alone_is_edited_not_rewritten(monkeypatch, engine, curated_payload):
    _writer(monkeypatch, engine, _draft(curated_payload, 88))
    _lengthen(monkeypatch, engine)
    _copy_model(monkeypatch, engine, _CopyEdit())
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(engine.written) == 1 and len(engine.edits) == 1 and not engine.copy_calls
    assert len(out["narration_script"].split()) == 108


def test_attempt_two_a_615_character_first_line_with_a_length_miss_is_repaired_and_edited(monkeypatch, engine, curated_payload):
    caption = _long_first_line(curated_payload["caption"])
    _writer(monkeypatch, engine, _draft(curated_payload, 99, caption=caption))
    _lengthen(monkeypatch, engine)
    _copy_model(monkeypatch, engine, _CopyEdit())
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(engine.written) == 1 and len(engine.edits) == 1 and not engine.copy_calls   # no model for the caption
    assert len(out["caption"].splitlines()[0]) <= 125


def test_a_first_sentence_over_the_limit_gets_the_copy_edit(monkeypatch, engine, curated_payload):
    caption = LONG_SENTENCE + " 🏛️ What else did Rome know? #RomanConcrete #AncientRome #Engineering"
    draft = _draft(curated_payload, 108, caption=caption)
    _writer(monkeypatch, engine, draft)
    _lengthen(monkeypatch, engine)
    better = "Roman harbor concrete has sealed its own cracks for 2,000 years 🏛️\n\n" + caption[len(LONG_SENTENCE) + 4:]
    _copy_model(monkeypatch, engine, _CopyEdit(caption=better))
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(engine.written) == 1 and len(engine.copy_calls) == 1 and not engine.edits
    assert out["caption"] == better and out["narration_script"] == draft.narration_script
    prompt = engine.copy_calls[0]
    assert "Caption's first line is" in prompt and "- caption:" in prompt and "REPEATS:" in prompt
    assert "<verified_facts>" in prompt and "Return only these fields, each whole: caption." in prompt


def test_attempt_three_a_mixed_yes_and_choice_poll_gets_the_copy_edit(monkeypatch, engine, curated_payload):
    draft = _draft(curated_payload, 108, poll=MIXED_POLL)
    assert any(p.startswith("The poll asks a yes/no question") for p in validate_structure(draft))   # repair_poll declines
    _writer(monkeypatch, engine, draft)
    _lengthen(monkeypatch, engine)
    # the model also returns a title it was not asked for: only the poll is taken
    _copy_model(monkeypatch, engine, _CopyEdit(poll={"question": "Hot mixing or modern cement?",
                                                     "options": ["Hot mixing", "Modern cement"]}, title="Changed 🏛️🏛️"))
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(engine.written) == 1 and len(engine.copy_calls) == 1 and not engine.edits
    assert out["poll"]["question"] == "Hot mixing or modern cement?" and out["title"] == draft.title
    assert out["narration_script"] == draft.narration_script                # byte for byte


def test_a_length_miss_with_a_copy_fault_gets_both_edits_not_a_rewrite(monkeypatch, engine, curated_payload):
    draft = _draft(curated_payload, 88, poll=MIXED_POLL)
    _writer(monkeypatch, engine, draft)
    _lengthen(monkeypatch, engine)
    _copy_model(monkeypatch, engine, lambda prompt: _CopyEdit(poll={"question": "Hot mixing or modern cement?",
                                                                    "options": ["Hot mixing", "Modern cement"]}))
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(engine.written) == 1 and len(engine.copy_calls) == 1 and len(engine.edits) == 1
    assert engine.edits[0].startswith("LENGTH:") and "poll" not in engine.edits[0].lower()
    assert out["poll"]["question"] == "Hot mixing or modern cement?" and len(out["narration_script"].split()) == 108


def test_the_copy_edit_never_touches_the_narration(monkeypatch, engine, curated_payload):
    draft = _draft(curated_payload, 108, poll=MIXED_POLL)
    _copy_model(monkeypatch, engine, lambda prompt: _CopyEdit.model_validate(
        {"poll": {"question": "Hot mixing or modern cement?", "options": ["Hot mixing", "Modern cement"]},
         "narration_script": "Something else entirely.", "closing_sentence": "Something else entirely."}))
    out = engine._edit_copy(draft, validate_structure(draft), SHEET)
    assert out is not None
    for key in ("narration_script", "hook_sentence", "closing_sentence", "sources", *script_engine.GRAPHICS_FIELDS):
        assert getattr(out, key) == getattr(draft, key)
    assert out.narration_script.encode("utf-8") == draft.narration_script.encode("utf-8")


# ------------------------------------------------------------------ fallbacks
def test_a_copy_edit_that_breaks_something_falls_back_to_the_rewrite(monkeypatch, engine, curated_payload):
    broken = _draft(curated_payload, 108, poll=MIXED_POLL)
    good = _draft(curated_payload, 107)
    _writer(monkeypatch, engine, broken, good)
    _lengthen(monkeypatch, engine)
    # fixes the poll, but its options run past the 24-character limit: a new fault
    _copy_model(monkeypatch, engine, _CopyEdit(poll={"question": "Hot mixing or modern cement?",
                                                     "options": ["Hot mixing at high heat, by hand", "Modern cement"]}))
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(engine.written) == 2 and len(engine.copy_calls) == 1
    assert out["narration_script"] == good.narration_script


def test_a_copy_edit_that_adds_a_fault_elsewhere_or_fails_falls_back(monkeypatch, engine, curated_payload):
    draft = _draft(curated_payload, 108, poll=MIXED_POLL)
    _copy_model(monkeypatch, engine, _CopyEdit(poll={"question": "Can it last?", "options": ["Hot mixing", "No"]}))
    assert engine._edit_copy(draft, validate_structure(draft), SHEET) is None          # still a mismatch

    def fail(*a, **k):
        raise script_engine.LLMError("quota")
    monkeypatch.setattr(script_engine, "structured_call", fail)
    assert engine._edit_copy(draft, validate_structure(draft), SHEET) is None
    _copy_model(monkeypatch, engine, _CopyEdit())
    assert engine._edit_copy(draft, validate_structure(draft), SHEET) is None          # nothing returned


def test_a_narration_fault_with_a_copy_fault_is_still_rewritten(monkeypatch, engine, curated_payload):
    draft = _draft(curated_payload, 108, poll=MIXED_POLL)
    bad = draft.model_copy(update={"narration_script": draft.narration_script.rstrip(".") + " and the"})
    good = _draft(curated_payload, 107)
    _writer(monkeypatch, engine, bad, good)
    _lengthen(monkeypatch, engine)
    _copy_model(monkeypatch, engine, _CopyEdit())
    engine.generate_script({"id": "t1", "title": "T"})
    assert len(engine.written) == 2 and not engine.copy_calls


def test_a_surgical_revision_that_leaves_a_copy_fault_gets_the_copy_edit(monkeypatch, engine, curated_payload):
    _writer(monkeypatch, engine, _draft(curated_payload, 108))

    def revise(previous, findings, sheet):
        engine.edits.append(findings)
        return ScriptPackage.model_validate(dict(previous, poll=MIXED_POLL))
    monkeypatch.setattr(engine, "_revise_surgically", revise)
    _copy_model(monkeypatch, engine, _CopyEdit(poll={"question": "Hot mixing or modern cement?",
                                                     "options": ["Hot mixing", "Modern cement"]}))
    previous = dict(_draft(curated_payload, 108).model_dump(), fact_sheet=True)
    out = engine.generate_script({"id": "t1", "title": "T"}, revision_notes="'a claim here' (unsupported)",
                                 previous=previous)
    assert not engine.written and len(engine.edits) == 1 and len(engine.copy_calls) == 1
    assert out["poll"]["question"] == "Hot mixing or modern cement?" and out["fact_sheet_used"] is True
