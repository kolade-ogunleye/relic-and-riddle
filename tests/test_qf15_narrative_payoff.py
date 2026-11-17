"""QF-15: the story order is hook -> stake -> 2-3 reveals -> payoff -> loop, with no modern-relevance beat. The
penultimate sentence is the payoff: the writer returns it as payoff_sentence, word for word, and it names again a noun
of the narration's own first sentence (a noun in one sentence, a noun or an adjective in the other; a shared verb,
adjective or number never links them). The payoff's faults ride in the same one edit as the opening's faults (QF-14),
before any rewrite. Operator ruling 2026-11-10 (option C): a missing, misplaced or hook-repeating payoff then blocks;
a payoff naming none of the opening's nouns ships flagged on the review card (payoff_note), its one edit asked once
per payoff sentence. Curated and cached scripts are refused before any fact provider, and library
scripts get that edit. A kept script is never reworded into a refusal. A trim never takes the payoff out."""

import json

import pytest

import main
from src import improve, prevoice_lines, script_library
from src import script_engine as engine
from src.models import ScriptCore, ScriptPackage
from tests.test_pictures_every_line_2026_10_29 import world  # noqa: F401 - the pictures fixture

CLOSING = "That is where the story began."


def package(payload, hook, payoff, *, claimed_hook=None, middle="The evidence explains the mechanism."):
    return ScriptPackage.model_validate({**payload, "hook_sentence": claimed_hook or hook,
                                         "narration_script": f"{hook} {middle} {payoff} {CLOSING}",
                                         "payoff_sentence": payoff, "closing_sentence": CLOSING})


def draft(payload, hook, payoff):
    """The curated narration (its length and facts) with this first sentence and this payoff."""
    sentences = engine._split_sentences(payload["narration_script"])
    sentences[0], sentences[-2] = hook, payoff
    return ScriptPackage.model_validate({**payload, "narration_script": " ".join(sentences), "hook_sentence": hook,
                                         "payoff_sentence": payoff})


def faults(pkg):
    return engine.payoff_problems(pkg.narration_script, pkg.payoff_sentence)


# ----------------------------------------------------------------------------------------------- the rule
@pytest.mark.parametrize(("hook", "payoff"), [
    ("How did the stone walls survive?", "The walls transferred force into the ground."),
    ("One door sealed an entire level.", "The doors could be rolled shut from inside."),
    # review 2, finding 2 (a synthetic pair): the tagger reads "concrete" here as an adjective and "crumbles" as a
    # noun; the payoff's "concrete" is a noun, the only lemma the two sentences share
    ("Modern concrete crumbles fast, yet a sunken Roman pier still holds firm.",
     "Volcanic ash let tiny crystals grow through the cured concrete."),
])
def test_a_noun_of_the_opening_named_again_links_the_payoff(curated_payload, hook, payoff):
    pkg = package(curated_payload, hook, payoff)
    assert pkg.payoff_sentence == payoff
    assert faults(pkg) == []


@pytest.mark.parametrize(("hook", "payoff"), [
    ("Workers carve a temple.", "Visitors carve their names."),                 # a shared verb
    ("Ancient builders crossed the river.", "Ancient stones covered the hill."),  # a shared adjective
    ("200 workers crossed the river.", "200 stones covered the hill."),          # a shared number
])
def test_a_shared_verb_adjective_or_number_cannot_substitute_for_a_noun(curated_payload, hook, payoff):
    found = faults(package(curated_payload, hook, payoff))
    assert found and found[0].startswith(engine.PAYOFF_NOUN_PROBLEM)
    assert engine.blocking_problems(found) == []                    # flagged after its one edit, not blocked (C)


def test_a_missing_payoff_parses_and_blocks(curated_payload):
    for value in ("", None):
        pkg = ScriptPackage.model_validate({**curated_payload, "payoff_sentence": value})
        assert pkg.payoff_sentence == ""
        assert faults(pkg) and faults(pkg)[0].startswith(engine.PAYOFF_PROBLEM)
    legacy = {k: v for k, v in curated_payload.items() if k != "payoff_sentence"}
    assert faults(ScriptPackage.model_validate(legacy))


def test_the_payoff_is_the_penultimate_sentence_word_for_word(curated_payload):
    pkg = package(curated_payload, "One door sealed a level.", "The doors rolled shut.")
    assert faults(pkg.model_copy(update={"payoff_sentence": "The doors blocked the passage."}))
    assert faults(pkg.model_copy(update={"payoff_sentence": CLOSING}))
    assert faults(pkg.model_copy(update={"narration_script": "One door sealed a level. " + CLOSING}))


def test_repeating_the_hook_is_not_a_payoff(curated_payload):
    hook = "How did the walls survive?"
    assert faults(package(curated_payload, hook, hook))


def test_the_spoken_first_sentence_is_the_hook_not_unbound_metadata(curated_payload):
    pkg = package(curated_payload, "How did the dome survive?", "The walls transferred force.",
                  claimed_hook="How did the walls survive?")
    assert faults(pkg)


def test_a_missing_noun_model_holds_instead_of_using_word_overlap(monkeypatch, curated_payload):
    from src import narrative_payoff

    def unavailable(*args):
        raise OSError("model missing")
    monkeypatch.setattr(narrative_payoff, "shared_nouns", unavailable)
    pkg = package(curated_payload, "How did the walls survive?", "The walls transferred force.")
    assert "unavailable" in " ".join(faults(pkg))


def test_payoff_findings_file_under_their_own_class():
    assert improve.classify(engine.PAYOFF_PROBLEM + "the payoff names none of the first sentence's nouns. "
                            "Name the hook's subject again, the same word.") == "script.payoff"


# --------------------------------------------------------------------------------------- the writer's ask
def test_the_writer_schema_requires_the_payoff_and_the_prompts_ask_the_new_order():
    assert "payoff_sentence" in ScriptCore.model_json_schema()["required"]
    assert "hook -> stake -> two or three concrete reveals -> payoff -> loop" in engine.SYSTEM_PROMPT
    assert "Modern Relevance" not in engine.SYSTEM_PROMPT and "(Beat 4)" not in engine.SYSTEM_PROMPT
    assert "never add a generic modern-relevance paragraph" in engine.SYSTEM_PROMPT
    for template in (engine.USER_PROMPT_TEMPLATE, engine.VERIFIED_USER_PROMPT_TEMPLATE):
        assert "payoff_sentence" in template and "3-4 fact sentences" not in template
    assert "A PAYOFF finding" in engine.REVISE_SYSTEM and "3-4 fact sentences" not in engine.REVISE_SYSTEM


# ------------------------------------------------------------------------- where the rule is enforced
def test_drafts_and_final_claim_verification_both_hold_a_missing_payoff(monkeypatch, curated_payload):
    pkg = ScriptPackage.model_validate({**curated_payload, "payoff_sentence": ""})
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    assert any(p.startswith(engine.PAYOFF_PROBLEM) for p in engine.blocking_problems(engine.draft_problems(pkg)))
    writer = engine.ScriptEngine()

    def no_provider(*args, **kwargs):
        raise AssertionError("A missing payoff reached a provider")
    monkeypatch.setattr(writer.fact_checker, "verify", no_provider)
    monkeypatch.setattr(engine.free_llm, "enabled", lambda: False)
    passed, _, reason = writer.verify_claims(pkg.model_dump())
    assert not passed and engine.PAYOFF_PROBLEM in reason


def _writer(monkeypatch, draft, edited):
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


def test_a_payoff_fault_gets_one_edit_not_a_rewrite(monkeypatch, curated_payload):
    """Review 2, finding 4: a payoff fault was not narrow, so every one cost a full redraft."""
    unlinked = draft(curated_payload, curated_payload["hook_sentence"], "Builders later added ventilation shafts.")
    linked = draft(curated_payload, curated_payload["hook_sentence"], "Builders later extended the tunnel.")
    writer, written, edits = _writer(monkeypatch, unlinked, linked)
    out = writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and len(edits) == 1 and edits[0].startswith("PAYOFF: " + engine.PAYOFF_PROBLEM)
    assert out["narration_script"] == linked.narration_script


def test_a_hook_fault_and_a_payoff_fault_share_the_one_edit(monkeypatch, curated_payload):
    """Review 2, finding 4: the payoff fault no longer takes QF-14's one-call hook edit away; both ride in it."""
    long_hook = "Workers carefully cut this enormous tunnel from one solid rock starting far above."
    both = draft(curated_payload, long_hook, "Builders later added ventilation shafts.")
    fixed = draft(curated_payload, "No mortar holds these tunnel walls together.", "Builders later extended the tunnel.")
    writer, written, edits = _writer(monkeypatch, both, fixed)
    out = writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and len(edits) == 1
    assert edits[0].startswith("OPENING: " + engine.HOOK_PROBLEM) and "PAYOFF: " + engine.PAYOFF_PROBLEM in edits[0]
    assert engine.HOOK_FORMS in edits[0]                                  # QF-14's findings are all still there
    assert out["narration_script"] == fixed.narration_script


@pytest.mark.parametrize("fault", ["missing", "misplaced", "repeats_the_hook"])
def test_a_structural_payoff_fault_still_blocks_after_its_one_edit(monkeypatch, curated_payload, fault):
    """Operator ruling (option C): a missing, misplaced or hook-repeating payoff blocks after the one edit, and no
    copy edit is spent on the draft."""
    hook = curated_payload["hook_sentence"]
    bad = draft(curated_payload, hook, hook if fault == "repeats_the_hook" else curated_payload["payoff_sentence"])
    if fault != "repeats_the_hook":
        bad = bad.model_copy(update={"payoff_sentence": "" if fault == "missing" else bad.closing_sentence})
    bad = bad.model_copy(update={"title": "x" * 70})                                # a copy fault as well
    assert engine.blocking_problems(faults(bad))
    writer, written, edits = _writer(monkeypatch, bad, bad)
    copies = []
    monkeypatch.setattr(writer, "_edit_copy", lambda *a: copies.append(1))
    with pytest.raises(engine.ScriptUnavailableError):
        writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 3 and len(edits) == 1 and not copies     # three drafts, the one edit, no copy edit


UNLINKED = "Builders later added ventilation shafts."


def test_a_payoff_naming_no_opening_noun_gets_one_edit_then_ships_flagged(monkeypatch, curated_payload):
    unlinked = draft(curated_payload, curated_payload["hook_sentence"], UNLINKED)
    writer, written, edits = _writer(monkeypatch, unlinked, unlinked)          # the edit cannot add a noun
    out = writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and len(edits) == 1 and edits[0].startswith("PAYOFF: " + engine.PAYOFF_NOUN_PROBLEM)
    assert out["narration_script"] == unlinked.narration_script                # shipped, not ended without a script
    note = engine.payoff_note(out["narration_script"], UNLINKED)
    assert note.startswith("⚠️ Weak payoff: ") and UNLINKED in note


def test_the_payoff_flag_is_on_the_review_card_and_never_published(curated_payload):
    from src import probe, publishable
    from src.telegram_bot import TelegramApprovalBot
    unlinked = draft(curated_payload, curated_payload["hook_sentence"], UNLINKED)
    note = engine.payoff_note(unlinked.narration_script, unlinked.payoff_sentence)
    payload = {"title": "T", "narration_script": unlinked.narration_script, "caption": "c", "payoff_note": note}
    card = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert "<b>Weak payoff:</b>" in card and "Judge whether it answers the opening" in card
    assert "payoff_note" not in publishable.ENVELOPE_FIELDS and "payoff_note" not in publishable.PUBLISHABLE_FIELDS
    assert "payoff_note" in probe.NOTE_KEYS
    assert engine.payoff_note(curated_payload["narration_script"], curated_payload["payoff_sentence"]) == ""
    missing = engine.payoff_note(curated_payload["narration_script"], "")      # a blocking fault is never a flag
    assert missing == ""


def test_verify_claims_does_not_refuse_a_payoff_naming_no_opening_noun(monkeypatch, curated_payload):
    unlinked = draft(curated_payload, curated_payload["hook_sentence"], UNLINKED)
    monkeypatch.setattr(engine, "validate_structure", lambda pkg: [])
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [])
    writer = engine.ScriptEngine()
    reached = []
    monkeypatch.setattr(writer.fact_checker, "verify", lambda *a, **k: reached.append(1) or (False, 0.0, [], "stop"))
    monkeypatch.setattr(engine.free_llm, "enabled", lambda: False)
    writer.verify_claims(unlinked.model_dump())
    assert reached                                                  # it reached the fact checks: not refused for it


def test_an_unchanged_flagged_payoff_gets_no_second_edit(monkeypatch, curated_payload):
    """A fact-check revision that keeps the flagged payoff makes 2 surgical calls in all (the edit, the revision)."""
    unlinked = draft(curated_payload, curated_payload["hook_sentence"], UNLINKED)
    writer, _, edits = _writer(monkeypatch, unlinked, unlinked)
    writer.generate_script({"id": "t1", "title": "T"})
    closing = unlinked.closing_sentence
    revised = ScriptPackage.model_validate(dict(unlinked.model_dump(), closing_sentence=closing.replace(".", " again."),
                                                narration_script=unlinked.narration_script.replace(
                                                    closing, closing.replace(".", " again."))))
    monkeypatch.setattr(writer, "_revise_surgically", lambda prev, findings, s: edits.append(findings) or revised)
    out = writer.generate_script({"id": "t1", "title": "T"}, revision_notes="FACT: claim 3 unsupported",
                                 previous=dict(unlinked.model_dump(), fact_sheet=True))
    assert [e.split(":", 1)[0] for e in edits] == ["PAYOFF", "FACT"]           # 2 surgical calls, not 3
    assert engine.payoff_note(out["narration_script"], UNLINKED)


def test_a_new_payoff_sentence_may_get_its_own_one_edit(monkeypatch, curated_payload):
    unlinked = draft(curated_payload, curated_payload["hook_sentence"], UNLINKED)
    other = draft(curated_payload, curated_payload["hook_sentence"], "Builders later dug deep wells.")
    writer, written, edits = _writer(monkeypatch, unlinked, unlinked)
    writer.generate_script({"id": "t1", "title": "T"})
    monkeypatch.setattr(writer, "_generate_with_claude", lambda topic, feedback: written.append(feedback) or other)
    monkeypatch.setattr(writer, "_revise_surgically", lambda prev, findings, s: edits.append(findings) or other)
    writer.generate_script({"id": "t1", "title": "T"})
    assert [e.split(":", 1)[0] for e in edits] == ["PAYOFF", "PAYOFF"]             # one edit per payoff sentence


def test_a_long_draft_with_a_payoff_fault_is_edited_not_rewritten(monkeypatch, curated_payload):
    """Length first (one edit, inserted before the payoff), then the payoff (one edit): no rewrite."""
    sheet = {"topic_id": "t1", "facts": [], "sources": []}
    writer = engine.ScriptEngine()
    monkeypatch.setattr(engine, "_ready", lambda: True)
    monkeypatch.setattr(writer, "_sheet", lambda topic: sheet)
    monkeypatch.setattr(writer, "_sheet_context", lambda s: ("notes", []))
    monkeypatch.setattr(writer, "_with_graphics", lambda core, notes, retrieved, feedback: core)
    monkeypatch.setattr(writer, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script})
    monkeypatch.setattr(engine, "validate_structure", lambda pkg: [])
    hook = curated_payload["hook_sentence"]
    bad, good = "Builders later added ventilation shafts.", "Builders later extended the tunnel."

    def draft(words, payoff):
        return package(curated_payload, hook, payoff, middle=" ".join(["word"] * words) + ".")
    monkeypatch.setattr(engine, "spoken_length_problems", lambda pkg: [] if len(pkg.narration_script.split()) < 140 else [
        "Narration runs 60.0s when spoken (limit 51.5s). This draft is 150 words; cut the narration to 100-110 words."])
    written, edits = [], []
    monkeypatch.setattr(writer, "_generate_with_claude", lambda topic, feedback: written.append(feedback) or draft(140, bad))

    def revise(previous, findings, s):
        edits.append(findings.split(":", 1)[0])
        return draft(90, good if findings.startswith("PAYOFF:") else bad)
    monkeypatch.setattr(writer, "_revise_surgically", revise)
    out = writer.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and edits == ["LENGTH", "PAYOFF"]
    assert good in out["narration_script"]


def test_a_length_edit_inserts_before_the_payoff_without_expanding_it(monkeypatch, curated_payload):
    payoff = curated_payload["payoff_sentence"]
    inserted = "The passage continued beneath the houses. " + payoff
    monkeypatch.setattr(engine, "_structured", lambda *args, **kwargs: engine.Edits(edits=[
        engine.Edit(target="narration_script", find=payoff, replace=inserted)]))
    fixed = engine.ScriptEngine()._revise_surgically(dict(curated_payload), "LENGTH: extend it", {"facts": []})
    assert fixed.payoff_sentence == payoff
    assert inserted in fixed.narration_script
    assert faults(fixed) == []


def test_a_narration_edit_refuses_a_match_only_in_another_field(monkeypatch, curated_payload):
    monkeypatch.setattr(engine, "_structured", lambda *args, **kwargs: engine.Edits(edits=[
        engine.Edit(target="narration_script", find="not in the narration", replace="Extra material.")]))
    payload = {**curated_payload, "caption": "not in the narration"}
    assert engine.ScriptEngine()._revise_surgically(payload, "LENGTH: extend it", {"facts": []}) is None


def test_a_legacy_script_is_marked_with_its_payoff_without_changing_a_word(monkeypatch, curated_payload):
    legacy = {k: v for k, v in curated_payload.items() if k != "payoff_sentence"}
    penultimate = curated_payload["payoff_sentence"]
    monkeypatch.setattr(engine, "_structured", lambda *args, **kwargs: engine.Edits(edits=[
        engine.Edit(target="payoff_sentence", find="", replace=penultimate)]))
    fixed = engine.ScriptEngine()._revise_surgically(legacy, "PAYOFF: mark it", {"facts": []})
    assert fixed.narration_script == curated_payload["narration_script"] and faults(fixed) == []


def test_the_payoff_is_never_trimmed(curated_payload):
    payoff = curated_payload["payoff_sentence"]
    notes = f"REJECTED: '{payoff}' (unsupported)"
    assert engine.without_blocked_sentences(dict(curated_payload), notes) is None


def test_the_payoff_is_a_protected_line_for_the_line_picture_check(curated_payload):
    assert curated_payload["payoff_sentence"] in prevoice_lines.protected_sentences(curated_payload)


# --------------------------------------------------------------------------- library and kept scripts
def test_a_library_script_without_a_payoff_gets_the_one_edit(tmp_path, monkeypatch, curated_payload):
    from tests.test_qf14_hook_enforcement import _library_entry
    legacy = {k: v for k, v in curated_payload.items() if k != "payoff_sentence"}
    c = _library_entry(tmp_path, monkeypatch, legacy)
    library = c._script_from_library({"id": "t1"})
    assert library is not None and library["_weak_hook_here"].startswith("PAYOFF: " + engine.PAYOFF_PROBLEM)
    calls = []
    c._write_and_check_script = lambda topic, run_id, run_dir, dry_run, **kw: calls.append(kw) or {"revised": True}
    assert c._library_payload({"id": "t1"}, "run_x", tmp_path, True, library) == {"revised": True}
    assert calls[0]["initial_notes"].startswith("PAYOFF: ")


def test_a_library_script_with_a_linked_payoff_is_still_used_as_it_is(tmp_path, monkeypatch, curated_payload):
    from tests.test_qf14_hook_enforcement import _library_entry
    c = _library_entry(tmp_path, monkeypatch, curated_payload)
    c._write_and_check_script = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no edit"))
    out = c._library_payload({"id": "t1"}, "run_x", tmp_path, True, c._script_from_library({"id": "t1"}))
    assert out["narration_script"] == curated_payload["narration_script"]


def test_every_shipped_library_entry_is_routed_to_the_edit(monkeypatch):
    """No shipped entry has a payoff_sentence: none is used as it is."""
    monkeypatch.setattr(script_library, "FRESH_FROM", None)
    monkeypatch.setattr(main.PipelineCoordinator, "_spoken_length_now", lambda self, saved: "")
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.reuse_from = None, None
    seen = 0
    for entry in sorted((main.config.DATA_DIR / "script_library").glob("*/verified_script.json")):
        saved = json.loads(entry.read_text(encoding="utf-8"))
        monkeypatch.setattr(script_library, "kept_script", lambda tid, sheet, s=saved: dict(s))
        library = c._script_from_library({"id": entry.parent.name})
        assert library is not None and "PAYOFF: " + engine.PAYOFF_PROBLEM in library["_weak_hook_here"], entry
        seen += 1
    assert seen


def test_a_kept_script_without_a_payoff_is_not_reworded_into_a_refusal(world):  # noqa: F811
    """A kept script written before the rule has no payoff_sentence: its rewording would take the payoff edit, which
    changes the penultimate sentence, and a pictures regeneration refuses any change outside the asked lines. Unless
    the penultimate sentence is itself asked, its words are kept and no call is spent."""
    from tests import test_pictures_every_line_2026_10_29 as pictures
    world.coord._operator_feedback = {"reason": "pictures"}
    world.judge.unshown = ("livestock",)                     # the penultimate line (the wars) is shown
    out = world.check(pictures._payload(), kept_words=True)
    assert world.revisions == [] and out["narration_script"] == pictures.SCRIPT
    assert set(out[prevoice_lines.STAGE]["left"]) == {pictures.STORES}


def test_a_kept_script_whose_penultimate_line_is_asked_is_still_reworded(world):  # noqa: F811
    from tests import test_pictures_every_line_2026_10_29 as pictures
    world.coord._operator_feedback = {"reason": "pictures"}
    out = world.check(pictures._payload(), kept_words=True)          # the wars line is asked, and is the penultimate
    assert len(world.revisions) == 1 and pictures.STORES_REWORDED in out["narration_script"]
