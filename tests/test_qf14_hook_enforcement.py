"""QF-14: the first sentence (the first caption burst's sentence) is at most 12 spoken words and never opens on a
label: those faults block. A number, scale, contrast or question within its first 8 tokens is asked for too: its
absence gets the one opening edit and then ships, flagged on the review card (operator ruling 2026-11-10,
decisions/QF-14-hook-rule.md). A writer's draft gets the one targeted opening edit before any rewrite; a curated or
cached script with a blocking fault is refused before any fact provider; a library script with any hook fault is
edited at its opening before use, never used as it is."""

import json

import pytest

import main
from src import fact_sheet, improve, script_library
from src import script_engine as engine
from src.credits import sha256_file
from src.models import ScriptPackage


def package(payload, opening, **extra):
    body = payload["narration_script"].split(". ", 1)[1]
    # QF-15: a payoff that names a noun of every opening here (rock, stone), so these tests isolate the hook rule;
    # the payoff rule has its own tests (tests/test_qf15_narrative_payoff.py)
    payoff = "The rock itself holds the stones in place."
    body = body.replace(payload["payoff_sentence"], payoff)
    return ScriptPackage.model_validate({**payload, "narration_script": opening + " " + body,
                                         "hook_sentence": opening, "payoff_sentence": payoff, **extra})


@pytest.mark.parametrize("opening", [
    "This is a temple carved from one rock.",                                              # a label
    "Workers carefully cut this enormous temple from one solid rock starting far above.",  # 13 words
])
def test_a_label_or_an_opening_over_the_cap_blocks(opening):
    faults = engine.hook_problems(opening)
    assert faults and engine.blocking_problems(faults)


@pytest.mark.parametrize("opening", [
    "Beyond the hills along the river workers moved 200 stones.",                          # the number at word 9
    "Sound travels along a massive curved stone wall for hours.",                          # a vague size, no signal
])
def test_a_missing_signal_alone_is_a_finding_that_does_not_block(opening):
    faults = engine.hook_problems(opening)
    assert faults and all(f.startswith(engine.HOOK_SIGNAL_PROBLEM) for f in faults)
    assert engine.blocking_problems(faults) == []
    assert engine._opening_faults(faults) == faults                 # it still gets the one opening edit


@pytest.mark.parametrize("opening, kind", [
    ("In 1908, something flattened 80 million trees and left no crater.", "number"),
    ("How do you carve a temple from the top down?", "question"),
    ("No mortar holds these stones together.", "contrast"),
    ("Bridges rust within decades, yet these stone arches still stand.", "scale"),    # a span of time
    ("Iron bridges rust, yet these stone arches still stand.", "contrast"),          # an adversative
    ("Soldiers dismantled a palace hall in thirty-six hours.", "number"),            # a hyphenated number word
    ("The world's oldest map was scratched onto bone.", "scale"),
])
def test_short_concrete_opening_is_retained(opening, kind):
    assert engine.hook_problems(opening) == []
    assert engine.hook_signal(opening) == kind


def test_the_signal_must_arrive_within_the_first_eight_spoken_tokens():
    late = "Beyond the hills along the river workers moved 200 stones."
    assert any("first 8 spoken tokens" in problem for problem in engine.hook_problems(late))
    assert engine.hook_problems("Workers moved 200 stones across the river.") == []


def test_an_empty_narration_is_a_hook_fault():
    assert engine.hook_problems("")[0].startswith(engine.HOOK_PROBLEM)


def test_every_hook_fault_files_under_the_hook_class():
    for opening in ("This is a temple carved from one rock.", "Sound travels along a massive curved stone wall.",
                    "Workers carefully cut this enormous temple from one solid rock starting far above."):
        for problem in engine.hook_problems(opening):
            assert improve.classify(problem) == "script.hook", problem


def test_all_hook_faults_are_eligible_for_the_existing_one_call_edit():
    faults = [engine.HOOK_PROBLEM + "label", engine.HOOK_PROBLEM + "first 8", engine.STORY_PROBLEM + "place"]
    assert engine._opening_faults(faults) == faults


def test_label_only_fault_gets_one_surgical_edit_and_recheck(monkeypatch, curated_payload):
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    bad = package(curated_payload, "This is a temple carved from one rock.")
    good = package(curated_payload, "No mortar holds these stones together.")
    writer = engine.ScriptEngine()
    calls = []
    monkeypatch.setattr(writer, "_revise_surgically", lambda *args: calls.append(args) or good)
    result, problems = writer._narrow_edits(bad, engine.draft_problems(bad), {"facts": []})
    assert result.narration_script == good.narration_script
    assert len(calls) == 1 and calls[0][1].startswith("OPENING: ")
    assert not engine.blocking_problems(problems)


def test_failed_targeted_edit_still_blocks_and_is_asked_once(monkeypatch, curated_payload):
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    bad = package(curated_payload, "This is a temple carved from one rock.")
    writer = engine.ScriptEngine()
    calls = []
    monkeypatch.setattr(writer, "_revise_surgically", lambda *args: calls.append(args) or bad)
    for _ in range(2):
        _, problems = writer._narrow_edits(bad, engine.draft_problems(bad), {"facts": []})
        assert any(problem.startswith(engine.HOOK_PROBLEM) for problem in engine.blocking_problems(problems))
    assert len(calls) == 1


def test_no_copy_edit_is_spent_on_a_draft_whose_hook_the_edit_could_not_fix(monkeypatch, curated_payload):
    """The draft goes back to the writer for its hook anyway; a copy edit on it would be a wasted call."""
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    bad = package(curated_payload, "This is a temple carved from one rock.", title="x" * 70)
    writer = engine.ScriptEngine()
    calls = []
    monkeypatch.setattr(writer, "_revise_surgically", lambda *args: calls.append("opening") or bad)
    monkeypatch.setattr(writer, "_edit_copy", lambda *args: calls.append("copy"))
    problems = engine.draft_problems(bad)
    assert any(engine.copy_problem_fields(p, bad) is not None for p in problems)   # the title is a copy fault
    _, after = writer._narrow_edits(bad, problems, {"facts": []})
    assert calls == ["opening"] and any(p.startswith(engine.HOOK_PROBLEM) for p in after)


def test_a_long_draft_with_a_long_hook_is_edited_not_rewritten(monkeypatch, curated_payload):
    """Length first (one edit), then the opening (one edit): no full rewrite, as before QF-14 for the length."""
    sheet = {"topic_id": "t1", "facts": [], "sources": []}
    writer = engine.ScriptEngine()
    monkeypatch.setattr(engine, "_ready", lambda: True)
    monkeypatch.setattr(writer, "_sheet", lambda topic: sheet)
    monkeypatch.setattr(writer, "_sheet_context", lambda s: ("notes", []))
    monkeypatch.setattr(writer, "_with_graphics", lambda core, notes, retrieved, feedback: core)
    monkeypatch.setattr(writer, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script})
    monkeypatch.setattr(engine, "validate_structure", lambda pkg: [])
    long_hook = "Workers carefully cut this enormous temple from one solid rock starting far above."
    good_hook = "No mortar holds these stones together."
    closing = curated_payload["closing_sentence"]

    def narration(first, words):   # QF-15: the payoff package() marks stays the penultimate sentence
        return first + " " + " ".join(["word"] * words) + ". The rock itself holds the stones in place. " + closing

    long_draft = package(curated_payload, long_hook).model_copy(update={"narration_script": narration(long_hook, 120)})
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [] if len(pkg.narration_script.split()) < 140 else [
        "Narration runs 60.0s when spoken (limit 51.5s). This draft is 150 words; cut the narration to 100-110 words."])
    written, edits = [], []
    monkeypatch.setattr(writer, "_generate_with_claude", lambda topic, feedback: written.append(feedback) or long_draft)

    def revise(previous, findings, s):
        edits.append(findings.split(":", 1)[0])
        first = good_hook if findings.startswith("OPENING:") else long_hook
        return ScriptPackage.model_validate(dict(previous, narration_script=narration(first, 90), hook_sentence=first))
    monkeypatch.setattr(writer, "_revise_surgically", revise)
    out = writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and edits == ["LENGTH", "OPENING"]
    assert out["narration_script"].startswith(good_hook)


def test_curated_or_cached_weak_hook_is_rejected_before_any_fact_provider(monkeypatch, curated_payload):
    bad = package(curated_payload, "This is a temple carved from one rock.")
    monkeypatch.setattr(engine, "validate_structure", lambda pkg: [])
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    writer = engine.ScriptEngine()

    def no_provider(*args, **kwargs):
        raise AssertionError("A weak hook reached an external checker")
    monkeypatch.setattr(writer.fact_checker, "verify", no_provider)
    monkeypatch.setattr(engine.free_llm, "enabled", lambda: False)
    passed, _, reason = writer.verify_claims(bad.model_dump())
    assert not passed and engine.HOOK_PROBLEM in reason


# ------------------------------------------------------------------------------------------ the library path
def _library_entry(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(script_library, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(script_library, "FRESH_FROM", None)
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path / "sheets")
    (tmp_path / "sheets").mkdir()
    dev = tmp_path / "dev_sheet.json"
    dev.write_text(json.dumps({"topic_id": "t1", "facts": ["a"]}), encoding="utf-8")
    script = dict(payload, guardrail_passed=True, fact_review={"sheet_sha256": sha256_file(dev), "sheet_path": str(dev)})
    script_library.export("t1", dev, script, {"topic_id": "t1", "run_id": "run_dev"})
    data = tmp_path / "data"
    (data / "fact_sheets").mkdir(parents=True)
    (data / "fact_sheets" / "t1.json").write_bytes(dev.read_bytes())
    monkeypatch.setattr(main.config, "DATA_DIR", data)
    monkeypatch.setattr(main.PipelineCoordinator, "_spoken_length_now", lambda self, saved: "")
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.reuse_from = None, None
    return c


def test_a_library_script_with_a_weak_hook_gets_the_opening_edit_not_used_as_it_is(tmp_path, monkeypatch,
                                                                                   curated_payload):
    weak = package(curated_payload, "This is a temple carved from one rock.").model_dump()
    c = _library_entry(tmp_path, monkeypatch, weak)
    library = c._script_from_library({"id": "t1"})
    assert library is not None and library["_weak_hook_here"].startswith("OPENING: " + engine.HOOK_PROBLEM)
    calls = []
    c._write_and_check_script = lambda topic, run_id, run_dir, dry_run, **kw: calls.append(kw) or {"revised": True}
    assert c._library_payload({"id": "t1"}, "run_x", tmp_path, True, library) == {"revised": True}
    assert calls[0]["initial_notes"].startswith("OPENING: ")
    assert calls[0]["initial_payload"]["narration_script"] == weak["narration_script"]
    assert "_weak_hook_here" not in calls[0]["initial_payload"]                     # the marker never reaches a record


def test_a_library_script_with_a_passing_hook_is_still_used_as_it_is(tmp_path, monkeypatch, curated_payload):
    c = _library_entry(tmp_path, monkeypatch, curated_payload)
    c._write_and_check_script = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no edit"))
    library = c._script_from_library({"id": "t1"})
    out = c._library_payload({"id": "t1"}, "run_x", tmp_path, True, library)
    assert out["narration_script"] == curated_payload["narration_script"] and "_weak_hook_here" not in out


def test_every_shipped_library_entry_is_routed_by_its_real_hook(monkeypatch):
    """The shipped entries: a script whose real opening fails the rule is edited, never used as it is."""
    monkeypatch.setattr(script_library, "LIBRARY_DIR", main.config.DATA_DIR / "script_library")
    monkeypatch.setattr(script_library, "FRESH_FROM", None)
    monkeypatch.setattr(main.PipelineCoordinator, "_spoken_length_now", lambda self, saved: "")
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.reuse_from = None, None
    seen = 0
    for entry in sorted(p for p in script_library.LIBRARY_DIR.glob("*") if (p / "verified_script.json").is_file()):
        monkeypatch.setattr(script_library, "kept_script",
                            lambda tid, sheet, e=entry: json.loads((e / "verified_script.json").read_text(encoding="utf-8")))
        library = c._script_from_library({"id": entry.name})
        assert library is not None, entry.name
        assert bool(library["_weak_hook_here"]) == bool(engine.hook_problems(library["narration_script"])), entry.name
        seen += 1
    assert seen


# ------------------------------------------------------------------------------- round 4: categories and forms
@pytest.mark.parametrize("opening, kind", [
    ("Monks left Europe's most puzzling book unsigned.", "scale"),          # most + adjective
    ("The least visited chamber holds the oldest paintings.", "scale"),
    ("Builders shaped the hall from a single block.", "number"),            # single alongside one
    ("A wall older than the city still stands.", "scale"),                  # comparative + than
    ("Divers mapped more than 600 hidden entrances.", "scale"),        # the comparison comes first
    ("This road predates the empire that paved it.", "scale"),              # comparative verb
    ("Before the pharaohs, farmers built this dam.", "contrast"),           # earlier-than marker
    ("Engineers couldn't explain the tunnel's air.", "contrast"),           # any n't negation
    ("The best preserved scroll was found in a jar.", "scale"),             # irregular superlative
])
def test_each_category_covers_its_grammar(opening, kind):
    assert engine.hook_signal(opening) == kind
    assert engine.hook_problems(opening) == []


@pytest.mark.parametrize("opening", [
    "The walls grow stronger with every passing year.",    # a comparative without "than" is no comparison
    "After the war, the city was rebuilt by its people.",  # "after" orders events, it sets no contrast
    "Most of the city was rebuilt by its people.",         # "most of" is a quantity, not a superlative
])
def test_forms_outside_the_categories_are_still_refused(opening):
    assert engine.hook_signal(opening) is None


FORM_EXAMPLES = ["80 million trees", "thirty-six hours", "a single cliff", "the first", "the largest obelisk",
                 "the most precise gears", "the only map", "the world's oldest", "older than Stonehenge",
                 "more than 600 entrances", "predates writing", "no crater", "never read", "without mortar",
                 "couldn't", "yet", "but", "despite", "before farming", "still stands", "vanished", "unknown",
                 "for centuries", "two millennia", "How did...?", "Why does...?"]


def test_every_named_form_passes_and_vague_size_does_not():
    for example in FORM_EXAMPLES:
        assert example in engine.HOOK_FORMS, example
        assert engine.hook_signal(example) is not None, example
    for vague in ("massive", "immense", "colossal"):
        assert vague in engine.HOOK_FORMS and engine.hook_signal(vague) is None
    assert f"at most {engine.HOOK_MAX_WORDS}" in engine.HOOK_FORMS


def test_the_refusal_the_edit_and_the_prompts_name_what_passes(monkeypatch, curated_payload):
    from src import hook_learning
    signal = [p for p in engine.hook_problems("Sound travels along a massive curved stone wall.") if "tokens" in p]
    assert signal and engine.HOOK_FORMS in signal[0]
    long_only = engine.hook_problems("In 1963, a man renovating his house in Cappadocia knocked through a wall.")
    assert engine.HOOK_FORMS in engine.opening_edit_findings(long_only)          # named even for a length-only fault
    assert engine.opening_edit_findings(signal).count(engine.HOOK_FORMS) == 1
    assert engine.HOOK_FORMS in engine.SYSTEM_PROMPT and "{HOOK_FORMS}" not in engine.SYSTEM_PROMPT
    assert engine.HOOK_FORMS in hook_learning.revision_note("question", "A hook.")


def _heldout(part):
    from pathlib import Path
    return json.loads((Path(__file__).parent / "data" / f"qf14_heldout_{part}.json").read_text(encoding="utf-8"))


# Measured once on the round-4 lists (measure.py heldout) and never tuned on: a change that admits fewer strong or
# more weak held-out openings than this fails. Part B was written before the change (its file says when).
HELDOUT_FLOORS = {"builder": (19, 0), "reviewer": (18, 12)}


@pytest.mark.parametrize("part", sorted(HELDOUT_FLOORS))
def test_held_out_openings_do_not_regress(part):
    data, (strong_min, weak_max) = _heldout(part), HELDOUT_FLOORS[part]

    def admitted(s):
        return not engine.hook_problems(s + " The next sentence follows.")
    assert sum(map(admitted, data["strong"])) >= strong_min
    assert sum(map(admitted, data["weak"])) <= weak_max


# ------------------------------------------------------------------- operator ruling 2026-11-10: signal repairs
SIGNAL_ONLY = "Sound travels along a massive curved stone wall for hours."


def _writer(monkeypatch, curated_payload, draft, edited):
    sheet = {"topic_id": "t1", "facts": [], "sources": []}
    writer = engine.ScriptEngine()
    monkeypatch.setattr(engine, "_ready", lambda: True)
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(writer, "_sheet", lambda topic: sheet)
    monkeypatch.setattr(writer, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script})
    monkeypatch.setattr(writer, "_load_curated", lambda topic_id: None)
    written, edits = [], []
    monkeypatch.setattr(writer, "_generate_with_claude", lambda topic, feedback: written.append(feedback) or draft)
    monkeypatch.setattr(writer, "_revise_surgically", lambda prev, findings, s: edits.append(findings) or edited)
    return writer, written, edits


def test_a_signal_only_opening_gets_one_edit_then_ships_flagged(monkeypatch, curated_payload):
    weak = package(curated_payload, SIGNAL_ONLY)
    writer, written, edits = _writer(monkeypatch, curated_payload, weak, weak)   # the edit cannot add a signal
    out = writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and len(edits) == 1 and edits[0].startswith("OPENING: ")
    assert engine.HOOK_FORMS in edits[0]                                       # the edit is told what passes
    assert out["narration_script"].startswith(SIGNAL_ONLY)                     # shipped, not ended without a script
    note = engine.hook_signal_note(out["narration_script"])
    assert note.startswith("⚠️ Weak hook signal: ") and SIGNAL_ONLY in note


def test_an_opening_over_the_cap_still_blocks_after_its_edit(monkeypatch, curated_payload):
    long = package(curated_payload, "Workers carefully cut this enormous temple from one solid rock starting far above.")
    writer, written, edits = _writer(monkeypatch, curated_payload, long, long)
    with pytest.raises(engine.ScriptUnavailableError):
        writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 3 and len(edits) == 1                               # three drafts, the one edit, no script


def test_verify_claims_does_not_refuse_a_signal_only_opening(monkeypatch, curated_payload):
    weak = package(curated_payload, SIGNAL_ONLY)
    monkeypatch.setattr(engine, "validate_structure", lambda pkg: [])
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    writer = engine.ScriptEngine()
    reached = []
    monkeypatch.setattr(writer.fact_checker, "verify", lambda *a, **k: reached.append(1) or (False, 0.0, [], "stop"))
    monkeypatch.setattr(engine.free_llm, "enabled", lambda: False)
    _, _, reason = writer.verify_claims(weak.model_dump())
    assert reached and engine.HOOK_PROBLEM not in reason                       # past the hook gate, to the checker


def test_a_library_opening_edit_is_the_one_edit(monkeypatch, curated_payload):
    """A library script's signal-only opening: the surgical revision with the opening findings is its one edit;
    _narrow_edits does not ask for a second."""
    weak = package(curated_payload, SIGNAL_ONLY)
    writer, written, edits = _writer(monkeypatch, curated_payload, weak, weak)
    monkeypatch.setattr(writer, "_sheet", lambda topic: {"topic_id": "t1", "facts": [], "sources": []})
    previous = dict(weak.model_dump(), fact_sheet=True)
    out = writer.generate_script({"id": "t1", "title": "T"}, revision_notes=engine.opening_edit_findings(
        engine.hook_problems(SIGNAL_ONLY)), previous=previous)
    assert len(edits) == 1 and written == [] and out["narration_script"].startswith(SIGNAL_ONLY)


def test_the_flag_is_on_the_review_card_and_never_published():
    from src import publishable
    from src.telegram_bot import TelegramApprovalBot
    note = engine.hook_signal_note(SIGNAL_ONLY + " The next sentence follows.")
    payload = {"title": "T", "narration_script": SIGNAL_ONLY, "caption": "c", "hook_signal_note": note}
    card = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert "<b>Weak hook signal:</b>" in card and "Judge whether the opening holds" in card
    assert "hook_signal_note" not in publishable.ENVELOPE_FIELDS and "hook_signal_note" not in publishable.PUBLISHABLE_FIELDS
    assert engine.hook_signal_note("In 1908, something flattened 80 million trees.") == ""


# ------------------------------------------------------------- round 5: one repair edit per opening (review 4)
def test_a_flagged_opening_gets_no_second_edit_from_a_later_revision(monkeypatch, curated_payload):
    """Review 4's probe: the draft gets its one opening edit and ships flagged; a fact-check revision that changes
    only the closing sentence makes 2 surgical calls in all (the edit, the revision), not 3."""
    weak = package(curated_payload, SIGNAL_ONLY)
    writer, _, edits = _writer(monkeypatch, curated_payload, weak, weak)
    first = writer.generate_script({"id": "t1", "title": "T"})
    assert len(edits) == 1 and engine.hook_signal_note(first["narration_script"])
    closing = weak.closing_sentence
    revised = ScriptPackage.model_validate(dict(weak.model_dump(), closing_sentence=closing.replace(".", " again."),
                                                narration_script=weak.narration_script.replace(
                                                    closing, closing.replace(".", " again."))))
    monkeypatch.setattr(writer, "_revise_surgically", lambda prev, findings, s: edits.append(findings) or revised)
    out = writer.generate_script({"id": "t1", "title": "T"}, revision_notes="FACT: claim 3 unsupported",
                                 previous=dict(weak.model_dump(), fact_sheet=True))
    assert [e.split(":", 1)[0] for e in edits] == ["OPENING", "FACT"]                # 2 surgical calls, not 3
    assert out["narration_script"].startswith(SIGNAL_ONLY)


def test_a_rewording_never_changes_the_first_sentence_unasked(monkeypatch, curated_payload):
    """A line-picture rewording of a flagged script, in a fresh engine (a new run): no opening edit is asked, so the
    first sentence stays word for word."""
    weak = package(curated_payload, SIGNAL_ONLY)
    body = weak.narration_script.split(". ")
    reworded_line = body[2] if len(body) > 2 else body[-1]
    reworded = ScriptPackage.model_validate(dict(weak.model_dump(), narration_script=weak.narration_script.replace(
        reworded_line, reworded_line.replace(" ", "  ", 1).replace("  ", " the ", 1))))
    writer, _, edits = _writer(monkeypatch, curated_payload, weak, reworded)
    out = writer.generate_script({"id": "t1", "title": "T"}, revision_notes="LINES: reword line 3 for the photos",
                                 previous=dict(weak.model_dump(), fact_sheet=True))
    assert [e.split(":", 1)[0] for e in edits] == ["LINES"]
    assert out["narration_script"].startswith(SIGNAL_ONLY)


def test_a_new_opening_may_get_its_own_one_edit(monkeypatch, curated_payload):
    weak = package(curated_payload, SIGNAL_ONLY)
    other = package(curated_payload, "Water runs along a curved stone channel for hours.")   # another weak opening
    writer, written, edits = _writer(monkeypatch, curated_payload, weak, weak)
    writer.generate_script({"id": "t1", "title": "T"})
    monkeypatch.setattr(writer, "_generate_with_claude", lambda topic, feedback: written.append(feedback) or other)
    monkeypatch.setattr(writer, "_revise_surgically", lambda prev, findings, s: edits.append(findings) or other)
    writer.generate_script({"id": "t1", "title": "T"})
    assert [e.split(":", 1)[0] for e in edits] == ["OPENING", "OPENING"]             # one edit per opening
