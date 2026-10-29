"""The Derinkuyu script stage (production run_derinkuyu_underground_city_1793286295, 29 Oct 15:04-16:02 UTC): 24.5
minutes, most of it spent on things a picture or a field edit settles.

- 15:09 four lines naming something no photo showed went to the writer (a writer call, both fact checks, a length
  edit, a second fact-check round) and the rewording was refused at 15:29. The picture side now comes first: the
  pool's reserve photos, then a bounded search for just those lines; only what is still unshown goes to the writer.
- 15:21 "Surgical edit not found in the draft (',\\n  {\\n   "value": "8 to 18",\\n   "label": "Subterranean level')":
  a graphic is now edited by its fields, not by a text search of the serialized draft.
- 15:20 a flat "8 to 18" levels stat card (a disputed range) and 15:14 an emphasis word the narration no longer said:
  the graphics planner and the editors are told the hedges cover graphics and emphasis words are narration words.
- The picture check's reason carried the model's own note ("Wait, make sure no control tokens ..."): stripped.

No model, writer, network or repository write: free_llm.call and the Commons search are replaced, files go to tmp_path.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from src import beat_images, config, improve, prevoice_lines as pl, script_engine
from src.llm import verdict_reason
from src.models import ScriptPackage

HOOK = "Derinkuyu reaches deep into the soft volcanic rock of Cappadocia."
SHAFT = "Ventilation shafts carried fresh air down through every level."
ENTRANCES = "More than 600 secret entrances descend approximately 280 feet (85 m) underground."
STABLES = "Stables for livestock were cut into the upper floors."
QUESTION = "Who were they hiding from?"
CLOSE = "The answer still lies in the dark."
NARRATION = " ".join([HOOK, SHAFT, ENTRANCES, STABLES, QUESTION, CLOSE])
LEAK = "Wait, make sure no control tokens or formatting errors inside JSON."


class Judge:
    """free_llm.call as the light model answers from the descriptions: a line is shown when a photo described in
    the prompt names its subject ("shaft", "entrance", "stable"), abstract for a question or the close."""

    SUBJECTS = {"shafts": "shaft", "entrances": "entrance", "stables": "stable"}

    def __init__(self):
        self.calls = []

    def __call__(self, system, prompt, schema, **kw):
        assert kw.get("task_class") == "light"
        self.calls.append(prompt)
        photos = re.findall(r"^Photo (\d+): (.+)$", prompt.split("Narration sentences:")[0], re.M)
        lines = re.findall(r"^(\d+)\. (.+)$", prompt.split("Narration sentences:")[1], re.M)
        out = []
        for i, s in lines:
            low = s.lower()
            if "?" in s or "answer" in low or "derinkuyu reaches" in low:
                out.append({"index": int(i), "kind": "abstract", "reason": "an idea"})
                continue
            want = next(v for k, v in self.SUBJECTS.items() if k in low)
            hit = next((int(n) for n, text in photos if want in text.lower()), None)
            out.append({"index": int(i), "kind": "shown", "photo": hit, "reason": "shown"} if hit else
                       {"index": int(i), "kind": "unshown", "reason": "no photo shows it. " + LEAK})
        return schema.model_validate({"lines": out})


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.delattr(config, "PRE_VOICE_LINE_CHECK", raising=False)
    assets = tmp_path / "assets"
    topic_dir = assets / "derinkuyu_underground_city"
    topic_dir.mkdir(parents=True)
    records = [
        {"file": "01_commons.jpg", "title": "File:Derinkuyu ventilation shaft.jpg",
         "description": "A ventilation shaft cut through the rock at Derinkuyu",
         "vision_review": {"shows_topic": "yes", "usable": True, "safe": True,
                           "reason": "A ventilation shaft. " + LEAK}},
        {"file": "07_commons.jpg", "title": "File:Derinkuyu stable.jpg", "description": "A rock-cut stable",
         "vision_review": {"shows_topic": "partly", "usable": True, "safe": True, "reason": "A rock-cut stable."}}]
    for r in records:
        (topic_dir / r["file"]).write_bytes(b"jpg")
    (topic_dir / "credits.json").write_text(json.dumps({"reviewed": True, "images": records}), encoding="utf-8")
    judge = Judge()
    monkeypatch.setattr("src.free_llm.call", judge)
    monkeypatch.setattr(improve, "note", lambda klass, detail, source="pipeline": None)
    searches = []

    def search(beats, gap_beats, topic, work_dir, seen, notes="", prefix="gap", deadline=None):
        searches.append({"lines": [beats[i - 1]["text"] for i in gap_beats], "prefix": prefix, "seen": set(seen),
                         "work_dir": work_dir, "deadline": deadline})
        found = []
        for i in gap_beats:
            if "entrances" in beats[i - 1]["text"] and w.commons_has_entrance:
                work_dir.mkdir(parents=True, exist_ok=True)
                f = work_dir / f"{prefix}{i:02d}_1.jpg"
                f.write_bytes(b"jpg")
                found.append({"file": str(f), "title": "File:Derinkuyu entrance passage.jpg", "beat": i,
                              "description": "A narrow entrance passage descending into Derinkuyu",
                              "looked": "An entrance passage cut in the rock.", "license": "CC BY 4.0"})
        return [Path(c["file"]) for c in found], found
    monkeypatch.setattr(beat_images, "source_gaps", search)
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    revisions = []
    reserve = topic_dir / "09_commons.jpg"
    coord.visual_engine = SimpleNamespace(assets_dir=assets, reserve_sequence=lambda topic: list(w.reserve))
    coord.db = SimpleNamespace(update_run=lambda run_id, status, **kw: None)
    coord.progress_msg_id = None
    coord._check_cancel = lambda run_id: None

    def revise(topic, run_id, run_dir, dry_run, initial_payload=None, initial_notes=""):
        revisions.append(initial_notes)
        return dict(initial_payload, narration_script=initial_payload["narration_script"].replace(ENTRANCES, ""),
                    guardrail_passed=True)
    coord._write_and_check_script = revise
    coord._verify_final_copy = lambda payload, topic: None
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    from src.checkpoints import Checkpoints
    media = [topic_dir / r["file"] for r in records]
    w = SimpleNamespace(coord=coord, judge=judge, revisions=revisions, searches=searches, run_dir=run_dir,
                        topic_dir=topic_dir, records=records, reserve=[], reserve_file=reserve,
                        commons_has_entrance=False,
                        topic={"id": "derinkuyu_underground_city", "title": "Derinkuyu underground city"})

    def add_reserve(description, title="File:Derinkuyu entrance.jpg"):
        rec = {"file": reserve.name, "title": title, "description": description,
               "vision_review": {"shows_topic": "partly", "usable": True, "safe": True, "reason": description}}
        reserve.write_bytes(b"jpg")
        (topic_dir / "credits.json").write_text(json.dumps({"reviewed": True, "images": records + [rec]}),
                                                encoding="utf-8")
        w.reserve = [reserve]
    w.add_reserve = add_reserve
    w.check = lambda payload: coord._lines_against_pictures(w.topic, "run_x", run_dir, False, payload, media,
                                                            Checkpoints(run_dir), False)
    return w


def _payload(narration=NARRATION):
    return {"narration_script": narration, "guardrail_passed": True, "title": "Derinkuyu"}


def test_a_reserve_photo_that_shows_the_line_saves_the_writer_call(world):
    world.add_reserve("One of the city's rock-cut entrances, a stone door beside it")
    out = world.check(_payload())
    assert world.revisions == [] and world.searches == []               # no writer call, no search needed
    assert out["narration_script"] == NARRATION                          # the verified words, voiced as written
    stamp = out[pl.STAGE]
    assert stamp["pictured"] == [ENTRANCES] and stamp["writer_calls_saved"] == 1 and stamp["unshown"] == []
    assert len(world.judge.calls) == 2                                   # the script, then the line with the reserve
    side = pl.load_side(world.run_dir)
    assert side["reserve"] == ["09_commons.jpg"] and [p["file"] for p in side["photos"]] == ["09_commons.jpg"]
    assert "a photo found before the voice" in out["picture_lines_note"]
    again = world.check(out)                                             # a resume judges the same pool: no call
    assert again is out and len(world.judge.calls) == 2


def test_a_search_for_just_the_unshown_line_saves_the_writer_call(world):
    world.commons_has_entrance = True
    out = world.check(_payload())
    assert world.revisions == [] and len(world.searches) == 1
    s = world.searches[0]
    assert s["lines"] == [ENTRANCES] and s["prefix"] == pl.GAP_PREFIX   # only that line, named apart from gap photos
    assert s["work_dir"] == world.run_dir / "beat_images"
    assert "File:Derinkuyu ventilation shaft.jpg" in s["seen"]           # the pool's own photos are not fetched again
    assert out[pl.STAGE]["writer_calls_saved"] == 1
    found = pl.load_side(world.run_dir)["found"]
    assert len(found) == 1 and found[0]["line"] == ENTRANCES


def test_a_line_still_unshown_after_the_picture_side_goes_to_the_writer_once(world):
    out = world.check(_payload())
    assert len(world.searches) == 1 and len(world.revisions) == 1        # searched, found nothing: the writer
    assert ENTRANCES in world.revisions[0] and SHAFT not in world.revisions[0]
    assert out[pl.STAGE]["writer_calls_saved"] == 0 and out[pl.STAGE]["pictured"] == []
    assert not (world.run_dir / pl.SIDE_FILE).exists()                   # nothing found, nothing recorded


def test_the_search_is_bounded_to_gap_lines(world):
    many = " ".join(f"Entrances number {i} descend into the rock." for i in range(1, 9))
    world.check(_payload(" ".join([HOOK, many, CLOSE])))
    assert len(world.searches) == 1 and len(world.searches[0]["lines"]) == pl.GAP_LINES


def test_a_failing_picture_side_leaves_the_lines_for_the_writer(world, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("commons down")
    monkeypatch.setattr(beat_images, "source_gaps", broken)
    out = world.check(_payload())
    assert len(world.revisions) == 1 and ENTRANCES not in out["narration_script"]


def test_the_verdict_reasons_lose_the_models_stray_notes(world):
    out = world.check(_payload())
    record = json.loads((world.run_dir / pl.RECORD_FILE).read_text(encoding="utf-8"))
    reasons = " ".join(v["reason"] for v in record["verdicts"]) + " ".join(p["text"] for p in record["photos"])
    assert "Wait" not in reasons and "control tokens" not in reasons and "JSON" not in reasons
    assert "no photo shows it." in world.revisions[0] and "Wait" not in world.revisions[0]
    assert out[pl.STAGE]["left"] == [] and "A ventilation shaft." in reasons     # the verdicts themselves stand


def test_verdict_reason_keeps_the_reason_and_drops_self_talk():
    assert verdict_reason("The photo shows the Pantheon dome. " + LEAK) == "The photo shows the Pantheon dome."
    assert verdict_reason("Let me double-check. A Roman harbour core.") == "A Roman harbour core."
    assert verdict_reason("Hmm, okay. Make sure the JSON is valid.") == ""
    # Legitimate reasons stay whole (review rc72): "OK," "I think", "the prompt".
    for fine in ("OK, the photo shows a Roman bridge.", "I think it shows the harbour core.",
                 "It does not show what the prompt describes."):
        assert verdict_reason(fine) == fine
    assert verdict_reason("  A shaft   through rock.  ") == "A shaft through rock."
    assert verdict_reason(None) == ""


def test_the_picture_look_reason_is_stripped(monkeypatch, tmp_path):
    from PIL import Image
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (64, 64)).save(photo)
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr("src.free_llm.claude_engine", lambda: False)
    monkeypatch.setattr("src.free_llm.structured", lambda system, content, schema, **k: schema(
        shows_subject=False, reason="A modern bridge. " + LEAK, text_reason="Okay, let me see. A museum label."))
    look = beat_images.picture_look(photo, "Roman harbour")
    assert look["shows"] is False and look["reason"] == "A modern bridge." and look["text_reason"] == "A museum label."


def test_the_render_offers_the_photos_found_before_the_voice_under_their_line(world, monkeypatch):
    world.commons_has_entrance = True
    world.add_reserve("A rock-cut stone door", "File:Derinkuyu door.jpg")  # shows nothing asked: not recorded
    world.check(_payload())
    monkeypatch.setattr(main, "ensure_resolution", lambda p: p)
    monkeypatch.setattr(main.privacy, "anonymize_all", lambda paths: list(paths))
    monkeypatch.setattr(beat_images, "without_burned_in_text", lambda photos, subject, minimum: list(photos))
    beats = [{"text": HOOK + " " + SHAFT}, {"text": "More than 600 secret entrances descend approximately"},
             {"text": "280 feet (85 m) underground. " + STABLES}]
    stills, credits = world.coord._picture_side_media(world.topic, world.run_dir, beats, [])
    assert stills == [] and len(credits) == 1                              # the reserve shot showed no line
    assert credits[0]["beat"] == 2 and Path(credits[0]["upscaled"]).is_file()
    assert main.beat_of_line(beats, "") is None
    assert world.coord._picture_side_media(world.topic, world.run_dir / "none", beats, []) == ([], [])


# ---------------------------------------------------------------- structured graphic edits

FRAGMENT = ',\n  {\n   "value": "8 to 18",\n   "label": "Subterranean level'   # as logged at 15:21 (cut at 60)
CARDS = [{"value": "about 280 ft", "label": "deep, by one estimate"},
         {"value": "8 to 18", "label": "Subterranean levels"}]


def _revise(monkeypatch, payload, edits, findings="'Derinkuyu has 8 to 18 subterranean levels' (overstated)"):
    seen = []
    monkeypatch.setattr(script_engine, "_structured", lambda system, prompt, schema, **kw: seen.append(prompt)
                        or script_engine.Edits(edits=[script_engine.Edit(**e) for e in edits]))
    sheet = {"facts": [{"id": "f1", "statement": "Derinkuyu has 18 levels.", "disputed": True,
                        "hedge": "Derinkuyu has up to 18 levels, by some counts.", "evidence": []}]}
    return script_engine.ScriptEngine()._revise_surgically(payload, findings, sheet), seen


@pytest.mark.parametrize("find", [FRAGMENT, FRAGMENT + 's"\n  }'])
def test_the_logged_stat_card_fragment_removes_that_card(monkeypatch, curated_payload, find):
    revised, seen = _revise(monkeypatch, dict(curated_payload, stat_cards=CARDS), [{"find": find, "replace": ""}])
    assert revised is not None and [c.value for c in revised.stat_cards] == ["about 280 ft"]
    assert revised.narration_script == curated_payload["narration_script"]          # nothing else changed
    assert "<graphics>" in seen[0] and "stat_cards[2].value: 8 to 18" in seen[0]


def test_a_targeted_field_edit_hedges_the_card(monkeypatch, curated_payload):
    revised, _ = _revise(monkeypatch, dict(curated_payload, stat_cards=CARDS),
                         [{"target": "stat_cards[2].value", "find": "8 to 18", "replace": "up to 18"}])
    assert [c.value for c in revised.stat_cards] == ["about 280 ft", "up to 18"]


def test_a_targeted_entry_removal_takes_the_graphic_when_too_few_remain(monkeypatch, curated_payload):
    timeline = [{"year": -800, "approximate": True, "label": "First rooms cut"},
                {"year": 1963, "approximate": False, "label": "Rediscovered"}]
    revised, _ = _revise(monkeypatch, dict(curated_payload, timeline=timeline, stat_cards=CARDS),
                         [{"target": "timeline[1]", "find": "", "replace": ""},
                          {"target": "stat_cards[2]", "find": "", "replace": ""}])
    assert revised.timeline is None and len(revised.stat_cards) == 1   # one event is no timeline


def test_a_fragment_replaced_by_its_hedged_fields(monkeypatch, curated_payload):
    revised, _ = _revise(monkeypatch, dict(curated_payload, stat_cards=CARDS),
                         [{"find": FRAGMENT, "replace": ',\n  {\n   "value": "up to 18",\n   "label": "Subterranean levels"\n  }'}])
    assert revised.stat_cards[1].value == "up to 18"


def test_a_fragment_that_could_mean_two_entries_is_never_guessed(monkeypatch, curated_payload):
    twins = [{"value": "8 to 18", "label": "Subterranean levels"}, {"value": "8 to 18", "label": "Subterranean levels, all"}]
    revised, _ = _revise(monkeypatch, dict(curated_payload, stat_cards=twins), [{"find": FRAGMENT, "replace": ""}])
    assert revised is None                                              # the caller rewrites, as before


def test_a_text_edit_then_a_field_edit_both_stand(monkeypatch, curated_payload):
    caption = curated_payload["caption"]
    first = caption.split(".")[0]
    revised, _ = _revise(monkeypatch, dict(curated_payload, stat_cards=CARDS),
                         [{"find": first, "replace": first + " indeed"},
                          {"target": "stat_cards[2]", "find": "", "replace": ""}])
    assert revised.caption.startswith(first + " indeed") and len(revised.stat_cards) == 1


def test_graphic_edit_leaves_the_draft_unchanged_when_it_cannot_apply():
    draft = {"stat_cards": [dict(c) for c in CARDS], "emphasis_words": ["600", "Derinkuyu"]}
    before = json.dumps(draft)
    assert not script_engine.graphic_edit(draft, "stat_cards[5].value", "x", "y")
    assert not script_engine.graphic_edit(draft, "narration_script", "x", "y")
    assert not script_engine.graphic_edit(draft, None, '"value": "nowhere"', "")
    assert json.dumps(draft) == before
    assert script_engine.graphic_edit(draft, None, '"600",', "")       # a quoted emphasis word
    assert draft["emphasis_words"] == ["Derinkuyu"]


# ---------------------------------------------------------------- hedges and emphasis words at generation

SHEET = {"topic_id": "t1", "sources": [{"title": "X", "url": "https://en.wikipedia.org/wiki/X", "tier": 2}],
         "facts": [{"id": "f1", "statement": "Derinkuyu has 8 to 18 levels.", "kind": "number", "disputed": True,
                    "hedge": "Derinkuyu has 8 to 18 levels, by differing counts.", "evidence": []}]}


def test_the_hedges_note_covers_every_graphic():
    note = script_engine.mandatory_hedges_note(SHEET)
    assert "GRAPHICS too" in note and "stat card" in note and "timeline entry" in note
    assert "left out of the graphics" in note


def test_the_graphics_planner_is_told_the_hedges_and_that_emphasis_words_are_narration_words(monkeypatch, curated_payload):
    seen = []
    monkeypatch.setattr(script_engine, "_structured", lambda system, prompt, schema, **kw: seen.append((system, prompt))
                        or schema())
    engine = script_engine.ScriptEngine()
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    core = script_engine.ScriptCore.model_validate({k: v for k, v in curated_payload.items()
                                                    if k in script_engine.ScriptCore.model_fields})
    engine._with_graphics(core, "notes", [], script_engine.mandatory_hedges_note(SHEET))
    system, prompt = seen[-1]
    assert "MANDATORY HEDGES" in prompt and "left out of the graphics" in prompt
    assert "Each must be words the narration above says" in prompt and "Emphasis words are only words" in system


def test_both_graphics_planning_calls_carry_the_hedges(monkeypatch, curated_payload):
    """The writer's own graphics (_generate_with_claude) and the re-plan after a length edit
    (_fit_length_by_editing, where the flat "8 to 18" card was planned) are both told the hedges."""
    import inspect
    for fn in (script_engine.ScriptEngine._generate_with_claude, script_engine.ScriptEngine._fit_length_by_editing):
        src = inspect.getsource(fn)
        assert "self._with_graphics(" in src and "mandatory_hedges_note(sheet) + feedback" in src
    calls = []
    engine = script_engine.ScriptEngine()
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(script_engine, "_structured", lambda system, prompt, schema, **kw: schema.model_validate(
        {k: v for k, v in curated_payload.items() if k in schema.model_fields}))
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(engine, "_with_graphics", lambda core, notes, retrieved, feedback: calls.append(feedback) or core)
    engine._generate_with_claude({"id": "t1", "title": "T"}, "")
    assert calls and "MANDATORY HEDGES" in calls[0] and "GRAPHICS too" in calls[0]


def test_the_reviser_is_told_graphics_by_field_and_emphasis_words_follow_the_narration():
    assert '"target"' in script_engine.REVISE_SYSTEM and "Never copy JSON syntax" in script_engine.REVISE_SYSTEM
    assert "Emphasis words" in script_engine.REVISE_SYSTEM and "up to <N> levels" in script_engine.REVISE_SYSTEM
