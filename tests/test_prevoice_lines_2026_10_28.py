"""A line no photo shows goes back to the writer before the paid voice (audit 2026-10-28).

Production's Roman concrete x3 (2026-10-26) narrated "modern concrete / infrastructure" when no licensed photo showed
it; the voice was already recorded, so the line could only be flagged after the render. These tests reproduce it with
fixture photo descriptions (the Pantheon, a Roman harbour's concrete core): no model is called (free_llm.call is
replaced by a judge that reads the prompt), no writer runs (the revision path is recorded), no network.
"""
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from src import config, improve, prevoice_lines

HOOK = "Roman concrete has outlasted every empire that poured it."
PANTHEON = "Rome's Pantheon still carries the largest unreinforced concrete dome on Earth."
HARBOUR = "Harbour piers poured into seawater two thousand years ago still stand."
MODERN = "Today, modern bridges crack and crumble within a few decades."
QUESTION = "So why did the ancient mix heal itself?"
CLOSE = "The answer was hiding in lumps of lime."
REWORDED = "Cores drilled from those harbour piers show the lime that sealed their cracks."
ROMAN = " ".join([HOOK, PANTHEON, HARBOUR, MODERN, QUESTION, CLOSE])


class Judge:
    """free_llm.call as the light model would answer from the descriptions: the Pantheon and the harbour core are
    shown, a question or the lime's answer is abstract, anything modern is shown by no photo."""

    def __init__(self, fail=False, unshown=("modern",)):
        self.calls, self.fail, self.unshown = [], fail, unshown

    def __call__(self, system, prompt, schema, **kw):
        assert kw.get("task_class") == "light" and isinstance(prompt, str)  # light and text-only
        self.calls.append(prompt)
        if self.fail:
            from src.llm import LLMError
            raise LLMError("free model overloaded")
        lines = re.findall(r"^(\d+)\. (.+)$", prompt.split("Narration sentences:")[1], re.M)
        out = []
        for i, s in lines:
            low = s.lower()
            if any(w in low for w in self.unshown):
                out.append({"index": int(i), "kind": "unshown", "reason": "every photo is an ancient Roman building"})
            elif "?" in s or "lime" in low and "cores" not in low:
                out.append({"index": int(i), "kind": "abstract", "reason": "an idea"})
            else:
                out.append({"index": int(i), "kind": "shown", "photo": 2 if "harbour" in low else 1, "reason": "shown"})
        return schema.model_validate({"lines": out})


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.delattr(config, "PRE_VOICE_LINE_CHECK", raising=False)
    assets = tmp_path / "assets"
    topic_dir = assets / "roman_self_healing_concrete"
    topic_dir.mkdir(parents=True)
    records = [
        {"file": "01_commons.jpg", "title": "File:Pantheon dome interior.jpg",
         "description": "The coffered concrete dome of the Pantheon, Rome",
         "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": "The Pantheon's concrete dome."}},
        {"file": "02_commons.jpg", "title": "File:Roman harbour concrete core.jpg",
         "description": "A drill core of Roman marine concrete from a harbour pier at Caesarea",
         "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": "A Roman harbour concrete core."}}]
    for r in records:
        (topic_dir / r["file"]).write_bytes(b"jpg")
    (topic_dir / "credits.json").write_text(json.dumps({"reviewed": True, "images": records}), encoding="utf-8")
    judge = Judge()
    monkeypatch.setattr("src.free_llm.call", judge)
    # The picture side (2026-10-29) searches Commons first: here it finds nothing, so a line still goes to the writer.
    monkeypatch.setattr("src.beat_images.source_gaps", lambda *a, **k: ([], []))
    found = []
    monkeypatch.setattr(improve, "note", lambda klass, detail, source="pipeline": found.append((klass, detail)))
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    updates = []
    coord.visual_engine = SimpleNamespace(assets_dir=assets)
    coord.db = SimpleNamespace(update_run=lambda run_id, status, **kw: updates.append(kw.get("script_payload")))
    coord.progress_msg_id = None
    coord._check_cancel = lambda run_id: None
    revisions = []

    def revise(topic, run_id, run_dir, dry_run, initial_payload=None, initial_notes=""):
        revisions.append({"payload": initial_payload, "notes": initial_notes})
        if coord.revise_fails:
            (run_dir / "script_metadata.json").write_text(json.dumps({"guardrail_passed": False}), encoding="utf-8")
            updates.append({"guardrail_passed": False})
            raise RuntimeError("Script rejected by guardrail: the rewording claims too much")
        return dict(initial_payload, narration_script=initial_payload["narration_script"].replace(MODERN, coord.rewording),
                    guardrail_passed=True)
    coord._write_and_check_script = revise
    coord._verify_final_copy = lambda payload, topic: None
    coord.revise_fails, coord.rewording = False, REWORDED
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    from src.checkpoints import Checkpoints
    media = [topic_dir / r["file"] for r in records]
    w = SimpleNamespace(coord=coord, judge=judge, revisions=revisions, found=found, updates=updates, run_dir=run_dir,
                        media=media, topic={"id": "roman_self_healing_concrete", "title": "Roman self-healing concrete"},
                        checkpoints=Checkpoints(run_dir))

    def check(payload, kept_words=False, run=None):
        return coord._lines_against_pictures(w.topic, "run_x", run or run_dir, False, payload, media,
                                             Checkpoints(run or run_dir), kept_words)
    w.check = check
    return w


def _payload(narration=ROMAN):
    return {"narration_script": narration, "guardrail_passed": True, "title": "Roman concrete"}


def test_the_roman_case_sends_the_modern_bridges_line_back_once(world):
    out = world.check(_payload())
    assert len(world.revisions) == 1                                      # one revision, through the writer's path
    notes = world.revisions[0]["notes"]
    assert MODERN in notes and QUESTION not in notes and PANTHEON not in notes  # just the unshown line named
    assert "Pantheon" in notes and "harbour" in notes                     # toward what the photos do show
    assert world.revisions[0]["payload"]["narration_script"] == ROMAN     # the verified script, revised surgically
    assert MODERN not in out["narration_script"] and REWORDED in out["narration_script"]
    assert len(world.judge.calls) == 2                                    # the script, then its rewording
    stamp = out[prevoice_lines.STAGE]
    assert stamp["rewritten"] == [MODERN] and stamp["left"] == [] and stamp["unshown"] == []
    assert "reworded before the voice" in out["picture_lines_note"]
    assert [k for k, _ in world.found] == ["video.picture_relevance"] and "reworded" in world.found[0][1]
    assert (world.run_dir / prevoice_lines.RECORD_FILE).is_file()


def test_a_fine_script_is_one_call_and_no_revision(world):
    fine = " ".join([HOOK, PANTHEON, HARBOUR, QUESTION, CLOSE])
    out = world.check(_payload(fine))
    assert len(world.judge.calls) == 1 and world.revisions == [] and world.found == []
    assert out["narration_script"] == fine and "picture_lines_note" not in out
    assert out[prevoice_lines.STAGE]["unshown"] == []


def test_a_resume_pays_nothing(world):
    first = world.check(_payload())
    calls = len(world.judge.calls)
    again = world.check(first)                                            # the saved, stamped script
    assert len(world.judge.calls) == calls and len(world.revisions) == 1 and again is first
    bare = {k: v for k, v in first.items() if k != prevoice_lines.STAGE}  # the run's checkpoint alone also holds
    assert world.check(bare) is bare and len(world.judge.calls) == calls


def test_a_library_script_already_judged_against_these_photos_pays_nothing(world, tmp_path):
    judged = world.check(_payload(" ".join([HOOK, PANTHEON, HARBOUR, QUESTION, CLOSE])))
    other = tmp_path / "other_run"
    other.mkdir()
    assert world.check(dict(judged), run=other)[prevoice_lines.STAGE] == judged[prevoice_lines.STAGE]
    assert len(world.judge.calls) == 1


def test_a_failed_revision_keeps_the_verified_script(world):
    world.coord.revise_fails = True
    original = _payload()
    out = world.check(original)
    assert out["narration_script"] == ROMAN and out["guardrail_passed"] is True
    assert out[prevoice_lines.STAGE]["left"] == [MODERN] and out[prevoice_lines.STAGE]["unshown"] == [MODERN]
    assert "left as written" in out["picture_lines_note"] and "did not pass its checks" in out["picture_lines_note"]
    assert world.updates[-1]["narration_script"] == ROMAN                 # the run's record is the verified script again
    assert json.loads((world.run_dir / "script_metadata.json").read_text(encoding="utf-8"))["narration_script"] == ROMAN
    assert "left as written" in world.found[0][1]


def test_a_rewording_that_still_names_something_unshown_keeps_the_original(world):
    world.coord.rewording = "Modern motorway viaducts fail the same way."
    out = world.check(_payload())
    assert out["narration_script"] == ROMAN and len(world.revisions) == 1   # asked once, never twice
    assert "still names something no photo shows" in out["picture_lines_note"]


def test_abstract_sentences_are_never_sent_back(world):
    abstract = " ".join([HOOK, QUESTION, "What did they know that we forgot?", CLOSE])
    out = world.check(_payload(abstract))
    assert world.revisions == [] and "picture_lines_note" not in out
    world.judge.unshown = ()
    verdicts = prevoice_lines.judge(ROMAN, prevoice_lines.pool_descriptions(world.media[0].parent, world.media), "x")
    kinds = {v["sentence"]: v["kind"] for v in verdicts}
    assert kinds[QUESTION] == "abstract" and prevoice_lines.unshown(verdicts) == []


def test_a_sentence_the_model_skips_is_not_sent_back(world, monkeypatch):
    monkeypatch.setattr("src.free_llm.call", lambda *a, **k: prevoice_lines.LineReport(lines=[]))
    out = world.check(_payload())
    assert world.revisions == [] and out[prevoice_lines.STAGE]["unshown"] == []


def test_no_verdict_means_the_verified_script_goes_on_unstamped(world):
    world.judge.fail = True
    original = _payload()
    assert world.check(original) is original and prevoice_lines.STAGE not in original and world.revisions == []


@pytest.mark.parametrize("switch", ["vision", "flag", "kept", "voiced"])
def test_switched_off_kept_or_already_voiced_makes_no_call(world, monkeypatch, switch):
    kept = False
    if switch == "vision":
        monkeypatch.setattr(config, "PICTURE_VISION_CHECK", False)
    elif switch == "flag":
        monkeypatch.setattr(config, "PRE_VOICE_LINE_CHECK", False, raising=False)
    elif switch == "kept":
        kept = True                                                     # a regeneration that keeps the words
    else:
        (world.run_dir / "voiceover.mp3").write_bytes(b"mp3")
        (world.run_dir / "words.json").write_text("[]", encoding="utf-8")
    original = _payload()
    assert world.check(original, kept_words=kept) is original and world.judge.calls == []


def test_the_pool_descriptions_are_the_credits_captions_and_review_reasons(world):
    photos = prevoice_lines.pool_descriptions(world.media[0].parent, world.media + [Path("hook_motion.mp4")])
    assert [p["file"] for p in photos] == ["01_commons.jpg", "02_commons.jpg"]   # an uncredited clip says nothing
    assert "Pantheon" in photos[0]["text"] and "The Pantheon's concrete dome." in photos[0]["text"]


def test_the_check_runs_after_the_verified_script_and_before_the_voice():
    src = inspect.getsource(main.PipelineCoordinator._produce_and_publish)
    at = src.index("self._lines_against_pictures(")
    assert src.index("current_fact_review(payload)") < at < src.index("voice_inputs = self.voice_inputs(")


def test_a_resumed_regeneration_whose_previous_run_voiced_these_words_is_never_reworded(world, tmp_path):
    """Review 2026-10-28: kept_words lived only in memory; a resume before the voice was copied could reword the
    kept script and pay for the voice again."""
    previous = tmp_path / "run_prev"; previous.mkdir()
    (previous / "voiceover.mp3").write_bytes(b"paid take")
    (previous / "script_metadata.json").write_text(json.dumps({"narration_script": ROMAN}), encoding="utf-8")
    world.coord._previous_run_dir = lambda: previous
    original = _payload()
    assert world.check(original) is original and world.judge.calls == [] and world.revisions == []


def test_a_revision_that_falls_back_to_another_script_keeps_the_verified_one(world):
    """Review 2026-10-28: a curated fallback passed the check and the card named sentences it never changed."""
    real = world.coord._write_and_check_script
    world.coord._write_and_check_script = lambda *a, **k: dict(real(*a, **k), origin="curated")
    original = _payload()
    out = world.check(original)
    assert out["narration_script"] == ROMAN and "fell back to another script" in out["picture_lines_note"]
