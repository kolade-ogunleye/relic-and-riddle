"""Every spoken line shown by a picture that shows it (operator, 2026-10-29).

Production's underground-city runs (29 Oct, the run and its re-render) listed three lines no photo showed; the
re-render's Regenerate reason "Pictures" kept the script word for word, so nothing could reword them, and two lines
went on screen over unrelated photos ("and food supplies" over a painted stone panel). These tests reproduce it with
fixtures: no model is called (the light model and the picture look are stubs), no network (the museum sources are
stubs), no writer (the revision path is recorded).
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import main
from src import beat_images, config, image_review, improve, motion, museum_sources, prevoice_lines, scene_planner
from src.models import BeatQuery

HOOK = "Deep under the plain lies a city carved into soft rock."
SHAFT = "Ventilation shafts carried fresh air down eight levels."
STORES = "Families kept their livestock and food supplies far below."
WARS = "During the long wars, whole sections were sealed off."
CLOSE = "Why did anyone dig so deep?"
STORES_REWORDED = "Families lived far below, beside the shafts that fed them air."
SCRIPT = " ".join([HOOK, SHAFT, STORES, WARS, CLOSE])


# ------------------------------------------------------------------ 1. a pictures regeneration may reword a line
class Judge:
    """free_llm.call as the light model would answer from the descriptions: the city and the shafts are shown, a
    question is abstract, livestock, food and the wars are shown by no photo."""

    def __init__(self, unshown=("livestock", "wars")):
        self.calls, self.unshown = [], unshown

    def __call__(self, system, prompt, schema, **kw):
        self.calls.append(prompt)
        lines = re.findall(r"^(\d+)\. (.+)$", prompt.split("Narration sentences:")[1], re.M)
        out = []
        for i, s in lines:
            low = s.lower()
            if any(w in low for w in self.unshown):
                out.append({"index": int(i), "kind": "unshown", "reason": "no photo shows it"})
            elif "?" in s:
                out.append({"index": int(i), "kind": "abstract", "reason": "a question"})
            else:
                out.append({"index": int(i), "kind": "shown", "photo": 1, "reason": "shown"})
        return schema.model_validate({"lines": out})


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.delattr(config, "PRE_VOICE_LINE_CHECK", raising=False)
    assets = tmp_path / "assets"
    topic_dir = assets / "a_topic"
    topic_dir.mkdir(parents=True)
    records = [{"file": "01_commons.jpg", "title": "File:Rock-cut city levels.jpg",
                "description": "Levels of a rock-cut underground city",
                "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": "Carved rock levels."}},
               {"file": "02_commons.jpg", "title": "File:Ventilation shaft.jpg",
                "description": "A ventilation shaft running down through the levels",
                "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": "A vertical shaft."}}]
    for r in records:
        (topic_dir / r["file"]).write_bytes(b"jpg")
    (topic_dir / "credits.json").write_text(json.dumps({"reviewed": True, "images": records}), encoding="utf-8")
    judge = Judge()
    monkeypatch.setattr("src.free_llm.call", judge)
    monkeypatch.setattr("src.beat_images.source_gaps", lambda *a, **k: ([], []))   # the search finds nothing
    monkeypatch.setattr(improve, "note", lambda *a, **k: None)
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.visual_engine = SimpleNamespace(assets_dir=assets, reserve_sequence=lambda topic: [])
    coord.db = SimpleNamespace(update_run=lambda *a, **k: None)
    coord.progress_msg_id = None
    coord._check_cancel = lambda run_id: None
    coord._verify_final_copy = lambda payload, topic: None
    coord._operator_feedback = {}
    revisions = []

    def revise(topic, run_id, run_dir, dry_run, initial_payload=None, initial_notes=""):
        revisions.append(initial_notes)
        text = initial_payload["narration_script"]
        for old, new in coord.rewordings:
            text = text.replace(old, new)
        return dict(initial_payload, narration_script=text, guardrail_passed=True)
    coord._write_and_check_script = revise
    coord.rewordings = [(STORES, STORES_REWORDED), (WARS, "Some sections were later closed.")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    from src.checkpoints import Checkpoints
    media = [topic_dir / r["file"] for r in records]
    w = SimpleNamespace(coord=coord, judge=judge, revisions=revisions, run_dir=run_dir)

    def check(payload, kept_words=False):
        return coord._lines_against_pictures({"id": "a_topic", "title": "A rock-cut city"}, "run_x", run_dir, False,
                                             payload, media, Checkpoints(run_dir), kept_words)
    w.check = check
    return w


def _payload(narration=SCRIPT, **extra):
    return {"narration_script": narration, "guardrail_passed": True, "title": "A city", **extra}


def test_a_pictures_regeneration_rewords_only_the_lines_no_picture_shows(world):
    world.coord._operator_feedback = {"reason": "pictures"}
    out = world.check(_payload(), kept_words=True)                      # the kept script, word for word
    assert len(world.revisions) == 1 and "change nothing else at all" in world.revisions[0]
    assert STORES not in out["narration_script"] and STORES_REWORDED in out["narration_script"]
    for kept in (HOOK, SHAFT, CLOSE):
        assert kept in out["narration_script"]                          # every other line word for word
    stamp = out[prevoice_lines.STAGE]
    assert stamp["regeneration"] == "pictures" and set(stamp["rewritten"]) == {STORES, WARS}
    assert "sent it back for its pictures" in out["picture_lines_note"] and "voiceover is new" in out["picture_lines_note"]


def test_a_pictures_rewording_that_touches_another_line_is_refused(world):
    world.coord._operator_feedback = {"reason": "pictures"}
    world.coord.rewordings = [(STORES, STORES_REWORDED), (WARS, "Some sections were later closed."),
                              (SHAFT, "Shafts brought air down.")]
    out = world.check(_payload(), kept_words=True)
    assert out["narration_script"] == SCRIPT                            # the approved script stands
    assert "changed other lines too" in out["picture_lines_note"]


@pytest.mark.parametrize("reason", ["sound", "voice", "cover_kept"])
def test_another_kept_reason_still_keeps_the_words_and_asks_nothing(world, reason):
    world.coord._operator_feedback = {"reason": reason}
    original = _payload()
    assert world.check(original, kept_words=True) is original and world.judge.calls == []


def test_a_pictures_regeneration_does_not_trust_the_previous_runs_stamp(world):
    """The kept script carries the previous run's verdict, which left its lines unshown."""
    first = world.check(_payload())
    world.revisions.clear()
    world.judge.calls.clear()
    stale = dict(first, narration_script=SCRIPT)
    stale[prevoice_lines.STAGE] = dict(first[prevoice_lines.STAGE],
                                       **prevoice_lines.keys(SCRIPT, prevoice_lines.pool_descriptions(
                                           world.coord.visual_engine.assets_dir / "a_topic",
                                           [world.coord.visual_engine.assets_dir / "a_topic" / "01_commons.jpg",
                                            world.coord.visual_engine.assets_dir / "a_topic" / "02_commons.jpg"])),
                                       left=[STORES])
    world.coord._operator_feedback = {"reason": "pictures"}
    import shutil
    shutil.rmtree(world.run_dir)
    world.run_dir.mkdir()                                               # a new run folder: no checkpoint of its own
    out = world.check(stale, kept_words=True)
    assert world.judge.calls and len(world.revisions) == 1 and STORES_REWORDED in out["narration_script"]


def test_a_new_run_retries_a_script_whose_stamp_left_lines_unshown(world):
    """A library script stamped elsewhere with lines left (its rewording refused) is tried again on a new run."""
    world.coord.rewordings = []                                         # the first rewording changes nothing
    first = world.check(_payload())
    assert first[prevoice_lines.STAGE]["left"]
    import shutil
    shutil.rmtree(world.run_dir)
    world.run_dir.mkdir()
    world.coord.rewordings = [(STORES, STORES_REWORDED), (WARS, "Some sections were later closed.")]
    out = world.check(dict(first))
    assert STORES_REWORDED in out["narration_script"]


def test_the_plan_card_says_honestly_what_a_pictures_regeneration_does(monkeypatch):
    plan = main.regeneration_plan({"reason": "pictures"})
    assert "Redoes the <b>pictures</b>" in plan and "Only a line that still has none is reworded" in plan
    assert "voiceover is recorded again" in plan and "Otherwise its voiceover and music bed carry over" in plan
    assert "reworded" in improve.REGENERATE_EFFECT["pictures"]
    monkeypatch.setattr(config, "PRE_VOICE_LINE_CHECK", False, raising=False)   # no rewording possible: not promised
    assert "reworded" not in main.regeneration_plan({"reason": "pictures"})


def test_other_lines_changed_names_what_moved():
    assert prevoice_lines.other_lines_changed(SCRIPT, SCRIPT.replace(STORES, "X."), [STORES]) == []
    assert prevoice_lines.other_lines_changed(SCRIPT, SCRIPT.replace(SHAFT, "Y."), [STORES]) == [SHAFT]


# ------------------------------------------------------------------ 2. museum sources before giving up on a line
@pytest.fixture
def museums(tmp_path, monkeypatch):
    monkeypatch.setattr(museum_sources, "CACHE_FILE", tmp_path / "cache" / "museum_search.json")
    monkeypatch.setattr(museum_sources, "_down_until", {})
    monkeypatch.setattr(museum_sources, "line_sources", lambda: ("met",))
    asked = []

    def met(query, limit=12):
        asked.append(query)
        return [museum_sources._candidate(title="Storage jar for grain (The Met 1)", download_url="https://img/1.jpg",
                                          source_url="https://met/1", license="CC0", description="grain storage jar",
                                          provider="met"),
                museum_sources._candidate(title="Grain jar NC", download_url="https://img/2.jpg", license="CC BY-NC 4.0",
                                          description="grain", provider="europeana")]
    monkeypatch.setitem(museum_sources.SOURCES, "met", met)
    return asked


def test_a_museum_search_is_cached_and_licence_checked(museums):
    found = museum_sources.cached_search("grain storage jar", ["grain"])
    assert [c["title"] for c in found] == ["Storage jar for grain (The Met 1)"]     # NonCommercial never
    assert museum_sources.cached_search("Grain  storage jar", ["grain"]) == found and museums == ["grain storage jar"]
    assert museum_sources.cached_search("grain storage jar", ["chariot"]) == []      # must name the line's subject


def test_a_failed_museum_is_not_cached_and_rests(tmp_path, monkeypatch):
    monkeypatch.setattr(museum_sources, "CACHE_FILE", tmp_path / "m.json")
    monkeypatch.setattr(museum_sources, "_down_until", {})
    calls = []

    def down(query, limit=12):
        calls.append(query)
        raise RuntimeError("no network")
    monkeypatch.setitem(museum_sources.SOURCES, "met", down)
    assert museum_sources.cached_search("x jar", ["jar"], sources=("met",)) == []
    assert museum_sources.cached_search("x jar", ["jar"], sources=("met",)) == [] and len(calls) == 1
    assert not (tmp_path / "m.json").exists()


def test_line_sources_need_their_keys(monkeypatch):
    monkeypatch.setattr(config, "SMITHSONIAN_API_KEY", "")
    monkeypatch.setattr(config, "EUROPEANA_API_KEY", "k")
    # item 5 (2026-11-03): the Library of Congress is on by default (was ("met", "nasa", "europeana"))
    assert museum_sources.line_sources() == ("met", "nasa", "europeana", "loc")   # no DEMO_KEY in a pipeline


def _search_world(tmp_path, monkeypatch, commons_found):
    monkeypatch.setattr(beat_images.commons, "search_widened", lambda q, anchor, kw, limit=30, max_wait=60: ([], [(q, 0)]))
    monkeypatch.setattr(beat_images.commons, "candidates", lambda results, kws, sa: iter(list(commons_found)))

    def fetch(url, dest, **k):
        Image.new("RGB", (40, 40), (90, 80, 70)).save(dest, "JPEG")
    monkeypatch.setattr(beat_images.commons, "download_jpeg", fetch)
    looked = []

    def screen(path, query):
        looked.append(Path(path).name)
        return True, "a storage jar for grain", Path(path)
    monkeypatch.setattr(beat_images, "screened_picture", screen)
    item = BeatQuery(beat=3, query="grain storage jar", keywords=["grain"])
    return item, looked


def test_when_commons_has_nothing_for_a_line_the_museums_are_searched_with_the_same_look(tmp_path, monkeypatch, museums):
    item, looked = _search_world(tmp_path, monkeypatch, [])
    paths, credits = beat_images._download([item], {"id": "a_topic", "title": "A city"}, tmp_path / "found", set(), "line")
    assert museums == ["grain storage jar"] and looked == ["line03_1.jpg"]            # looked at like a Commons photo
    assert [c["provider"] for c in credits] == ["met"] and credits[0]["license"] == "CC0"
    record = json.loads((tmp_path / "found" / "credits.json").read_text(encoding="utf-8"))["images"]
    assert record[0]["source_url"] == "https://met/1" and record[0]["looked"] == "a storage jar for grain"


def test_a_line_commons_covers_asks_no_museum(tmp_path, monkeypatch, museums):
    on_commons = {"title": "File:Grain jar.jpg", "download_url": "https://c/1.jpg", "license": "CC0",
                  "description": "grain jar", "categories": [], "width": 2000, "height": 1500}
    item, _ = _search_world(tmp_path, monkeypatch, [on_commons])
    _, credits = beat_images._download([item], {"id": "a_topic", "title": "A city"}, tmp_path / "found", set(), "line")
    assert museums == [] and [c["title"] for c in credits] == ["File:Grain jar.jpg"]


# ------------------------------------------------------------------ 3. never an unrelated photo
@pytest.fixture
def fit(tmp_path, monkeypatch):
    monkeypatch.setattr(beat_images, "FIT_CACHE_FILE", tmp_path / "cache" / "picture_fit.json")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    folder = tmp_path / "pool"
    folder.mkdir()
    captions = {"hook.jpg": "Rock-cut city from above", "shaft.jpg": "A ventilation shaft",
                "panel.jpg": "A painted stone panel", "rock.jpg": "A dark rock close-up",
                "stable.jpg": "Carved troughs where livestock were kept"}
    images = []
    for i, (name, caption) in enumerate(captions.items()):
        Image.new("RGB", (48, 48), (20 * i, 90, 40)).save(folder / name, "JPEG")
        images.append({"file": name, "title": f"File:{name}", "description": caption,
                       "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": caption}})
    (folder / "credits.json").write_text(json.dumps({"images": images}), encoding="utf-8")
    scene_planner._folder_credits.cache_clear()
    looks = []

    def look(path, query, size=512, ask=None):
        name = Path(path).name
        looks.append((name, query))
        shows = (name == "shaft.jpg" and "shafts" in query)
        return {"checked": True, "shows": shows, "burned_in_text": False, "reason": f"{name} does not show it"}
    monkeypatch.setattr(beat_images, "picture_look", look)
    yield SimpleNamespace(p=lambda n: folder / n, looks=looks)
    scene_planner._folder_credits.cache_clear()


def _beats(*lines):
    return [{"start": 3.0 * i, "end": 3.0 * (i + 1), "text": t} for i, t in enumerate(lines)]


def test_an_unrelated_pick_gives_way_to_a_related_shot_from_what_is_known(fit):
    p = fit.p
    beats = _beats(HOOK, SHAFT, STORES)
    out, report = scene_planner.fit_to_lines(beats, [p("hook.jpg"), p("shaft.jpg"), p("panel.jpg")],
                                             [p(n) for n in ("hook.jpg", "shaft.jpg", "panel.jpg", "rock.jpg", "stable.jpg")],
                                             "A rock-cut city", swaps=1)
    assert out[2] == p("stable.jpg")                         # its record names the livestock; no look was asked of it
    assert ("stable.jpg", STORES) not in fit.looks and ("rock.jpg", STORES) in fit.looks
    [entry] = report["unshown"]
    assert entry["fallback"] == "related" and report["related"] == [{"beat": 3, "from": "panel.jpg", "to": "stable.jpg"}]
    assert "a related photo of what it names is shown instead" in scene_planner.fit_note(report)


def test_a_photo_judged_wrong_for_the_line_is_never_the_related_one(fit):
    p = fit.p
    beats = _beats(HOOK, SHAFT, STORES)
    out, report = scene_planner.fit_to_lines(beats, [p("hook.jpg"), p("shaft.jpg"), p("panel.jpg")],
                                             [p(n) for n in ("hook.jpg", "shaft.jpg", "panel.jpg", "stable.jpg")],
                                             "A rock-cut city", swaps=1)   # the stable was looked at: not it
    assert out[2] == p("shaft.jpg") and report["unshown"][0]["fallback"] == "held"


def test_the_opening_or_a_held_related_shot_is_held_on_rather_than_an_unrelated_pick(fit):
    p = fit.p
    # Line 2 right after the opening: the pick was kept before (the opening is never judged).
    out, report = scene_planner.fit_to_lines(_beats(HOOK, WARS), [p("hook.jpg"), p("panel.jpg")],
                                             [p("hook.jpg"), p("panel.jpg")], "A rock-cut city")
    assert out == [p("hook.jpg"), p("hook.jpg")] and report["unshown"][0]["fallback"] == "held"
    # A second line after a held one: the related shot is held on, within HELD_SHOT_MAX.
    out, report = scene_planner.fit_to_lines(_beats(HOOK, SHAFT, WARS, "And sealed again."),
                                             [p("hook.jpg"), p("shaft.jpg"), p("panel.jpg"), p("rock.jpg")],
                                             [p(n) for n in ("hook.jpg", "shaft.jpg", "panel.jpg", "rock.jpg")],
                                             "A rock-cut city", durations=[3.0, 3.0, 3.0, 3.0])
    assert out[2:] == [p("shaft.jpg"), p("shaft.jpg")] and [u["fallback"] for u in report["unshown"]] == ["held", "held"]

    # Pre-render asset quarantine: even when held run exceeds HELD_SHOT_MAX, holding previous related
    # shot is strictly preferred over asserting a contradictory still on screen.
    out, report = scene_planner.fit_to_lines(_beats(HOOK, SHAFT, WARS, "And sealed again."),
                                             [p("hook.jpg"), p("shaft.jpg"), p("panel.jpg"), p("rock.jpg")],
                                             [p(n) for n in ("hook.jpg", "shaft.jpg", "panel.jpg", "rock.jpg")],
                                             "A rock-cut city", durations=[3.0, 10.0, 10.0, 10.0])
    assert out[2:] == [p("shaft.jpg"), p("shaft.jpg")]
    assert all(u["fallback"] == "held" for u in report["unshown"])


def test_each_line_no_picture_shows_is_an_issue_on_the_card():
    fit_report = {"unshown": [{"beat": 3, "line": STORES, "fallback": "related", "related": "stable.jpg"},
                              {"beat": 4, "line": WARS, "fallback": "held"}]}
    rows = scene_planner.no_picture_issues(fit_report)
    assert [r["issue"].split(" (")[0] for r in rows] == [f"no picture shows: {STORES}", f"no picture shows: {WARS}"]
    assert all(r["severity"] == "info" and r["card"] == "Pictures" for r in rows)   # shown, never a block
    assert scene_planner.no_picture_issues({}) == []
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert "no_picture_issues(payload.get(\"picture_fit\"))" in src                 # added where the card reads issues


# ------------------------------------------------------------------ 4. what can move, from the same photo review
def _recorded(tmp_path, title, review):
    folder = tmp_path / title.replace(" ", "_")
    folder.mkdir()
    Image.new("RGB", (32, 32)).save(folder / "p.jpg", "JPEG")
    (folder / "credits.json").write_text(json.dumps({"images": [
        {"file": "p.jpg", "title": f"File:{title}.jpg", "vision_review": review}]}), encoding="utf-8")
    scene_planner._folder_credits.cache_clear()
    return folder / "p.jpg"


def test_the_photo_review_says_what_can_move_and_can_move_reads_it_first(tmp_path):
    assert "can_move" in image_review.PhotoVerdict.model_fields and "can_move" in image_review.SYSTEM
    assert image_review.PhotoVerdict(index=1, shows_topic="yes", usable=True, safe=True, reason="r").can_move is None
    base = {"shows_topic": "yes", "usable": True, "safe": True, "reason": "a view"}
    assert motion.can_move(_recorded(tmp_path, "lake water", {**base, "can_move": False})) is False
    assert motion.can_move(_recorded(tmp_path, "stone wall", {**base, "can_move": True})) is True
    assert motion.can_move(_recorded(tmp_path, "stone hall", dict(base))) is False        # older verdict: the words
    assert motion.can_move(_recorded(tmp_path, "river bank", dict(base))) is True
