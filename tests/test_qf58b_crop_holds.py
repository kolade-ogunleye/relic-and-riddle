"""QF-58b: the cascade's detail crop never plans a hold the Reel gate refuses beside its own photograph
(queue/QF-58.review-6.md, finding 1); a held shot the gate would refuse gives way only to the beat's own photo
matched to its line (the operator's "matched photo only", 2026-11-10); the crop cap cites its scope decision
(finding 3). Finding 2's count rule was tried and withdrawn (decisions/QF-58b-question.md)."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import beat_images, config, scene_planner
from src import picture_coverage as pc

MODERN_LINE = "Today, bridges crack within decades across modern highways."


@pytest.fixture(autouse=True)
def reel(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VIDEO_WIDTH", 1080)
    monkeypatch.setattr(config, "VIDEO_HEIGHT", 1920)
    monkeypatch.setattr(beat_images, "FIT_CACHE_FILE", tmp_path / "cache" / "picture_fit.json")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", False, raising=False)
    scene_planner._folder_credits.cache_clear()
    yield
    scene_planner._folder_credits.cache_clear()


def approved_pool(folder: Path, count: int, size: tuple[int, int] = (1400, 2000)) -> list[Path]:
    """`count` approved photos (a Topic-tier credit record each), by default big enough for a 9:16 punch-in."""
    folder.mkdir(parents=True, exist_ok=True)
    paths, images = [], []
    for index in range(count):
        name = f"{index + 1:02d}_commons.jpg"
        pixels = np.random.default_rng(index).integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
        Image.fromarray(pixels).save(folder / name, "JPEG")
        paths.append(folder / name)
        images.append({"file": name, "title": f"File:{name}", "license": "CC0",
                       "source_url": f"https://example.org/{name}",
                       "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": "the arches"}})
    (folder / "credits.json").write_text(json.dumps({"reviewed": True, "images": images}), encoding="utf-8")
    scene_planner._folder_credits.cache_clear()
    return paths


def crop_of(photo: Path, folder: Path, n: int = 1) -> Path:
    from src.lineage import record_derived
    crop = folder / "detail_crops" / str(n) / photo.name
    crop.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(photo) as image:
        image.crop((0, 0, 20, 36)).save(crop)
    record_derived(crop, [photo], pc.DETAIL_OPERATION, {"box": [0, 0, 0.5, 0.9]})
    return crop


# ------------------------------------------------------------------ 1. the crop source beside its own photograph
def test_a_crop_beside_the_opening_photo_is_not_planned_past_six_seconds(tmp_path):
    """The reviewer's probe (amber_room_heist, kailasa_temple_monolith): every photo in use, 4.5 s beats; beat 2's
    crop was of the opening photo (pool[0]) and joined into a 9 s hold the gate refuses after the voice."""
    pool = approved_pool(tmp_path / "topic", 4)
    out = list(pool)                       # beat 2 (k=1) is the line no photo shows; every photo is in use
    durations = [4.5] * 4
    got = scene_planner._cascade_fill(1, MODERN_LINE, out, pool, durations, {}, tmp_path / "run", "",
                                      only=(pc.DETAIL_CROP,))
    assert got is not None and got[0] == pc.DETAIL_CROP
    source = scene_planner.picture_id(got[1])
    assert source not in {scene_planner.picture_id(out[0]), scene_planner.picture_id(out[2])}
    plan = list(out)
    plan[1] = got[1]
    assert scene_planner.reel_photo_coverage(plan, durations)["long_holds"] == []


def test_a_crop_beside_its_photograph_stays_when_the_joined_hold_is_within_six_seconds(tmp_path):
    pool = approved_pool(tmp_path / "topic", 4)
    out = list(pool)
    durations = [3.0] * 4                  # 3 + 3 s: one 6 s hold, within the limit
    got = scene_planner._cascade_fill(1, MODERN_LINE, out, pool, durations, {}, tmp_path / "run", "",
                                      only=(pc.DETAIL_CROP,))
    assert got is not None and scene_planner.picture_id(got[1]) == scene_planner.picture_id(pool[0])


def test_a_crop_of_the_next_beats_photograph_counts_its_hold_too(tmp_path):
    pool = approved_pool(tmp_path / "topic", 2)
    out = [pool[1], pool[1], pool[0]]      # beat 2 (k=1): pool[0] follows it, pool[1] precedes it
    assert not scene_planner._crop_hold_ok(out, [4.0, 4.0, 4.0], 1, pool[0])     # 4 + 4 s with beat 3
    assert scene_planner._crop_hold_ok(out, [4.0, 1.5, 4.0], 1, pool[0])         # 1.5 + 4 s
    assert scene_planner._crop_hold_ok([pool[1], pool[1], pool[1]], [4.0] * 3, 1, pool[0])   # not a neighbour
    crop = crop_of(pool[0], tmp_path)      # a crop of the photograph is the photograph
    assert not scene_planner._crop_hold_ok([pool[1], pool[1], crop], [4.0] * 3, 1, pool[0])


def test_when_every_source_is_beside_itself_past_the_limit_no_crop_is_planned(tmp_path):
    pool = approved_pool(tmp_path / "topic", 1)
    got = scene_planner._cascade_fill(1, MODERN_LINE, [pool[0], pool[0]], pool, [4.5, 4.5], {}, tmp_path / "run",
                                      "", only=(pc.DETAIL_CROP,))
    assert got is None


def test_a_landscape_video_keeps_its_crop_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VIDEO_WIDTH", 1920)
    monkeypatch.setattr(config, "VIDEO_HEIGHT", 1080)
    pool = approved_pool(tmp_path / "topic", 4)
    got = scene_planner._cascade_fill(1, MODERN_LINE, list(pool), pool, [4.5] * 4, {}, tmp_path / "run", "",
                                      only=(pc.DETAIL_CROP,))
    assert got is not None and scene_planner.picture_id(got[1]) == scene_planner.picture_id(pool[0])


def test_the_planner_fills_a_beat_after_the_opening_without_a_long_hold(tmp_path, monkeypatch):
    def look(path, query, size=512, ask=None):
        bad = "modern" in query.lower()
        return {"checked": True, "shows": not bad, "burned_in_text": False, "reason": "not that" if bad else "it"}
    monkeypatch.setattr(beat_images, "picture_look", look)
    pool = approved_pool(tmp_path / "topic", 3)
    lines = ["The great aqueduct strides across the valley.", MODERN_LINE, "Builders mixed quicklime.",
             "The dome still stands."]
    beats = [{"start": 4.5 * i, "end": 4.5 * (i + 1), "text": t} for i, t in enumerate(lines)]
    out, report = scene_planner.fit_to_lines(beats, [pool[0], pool[1], pool[2], pool[1]], pool, "Roman concrete",
                                             work_dir=tmp_path / "run")
    crops = [p for p in out if pc.is_detail_crop(Path(p))]
    assert crops, report
    assert scene_planner.reel_photo_coverage(out, [4.5] * 4)["long_holds"] == []


# ------------------------------------------------------------------ 2. a refused hold gives way only to a matched photo
def graphic_plan(tmp_path, photos: int) -> tuple[list[Path], list[Path]]:
    """`photos` approved photos too small for a punch-in (so no crop can fill a beat), a graphic on beat 4."""
    pool = approved_pool(tmp_path / "topic", photos, size=(100, 60))
    graphic = tmp_path / "graphic_map.png"
    Image.new("RGB", (40, 40)).save(graphic)
    return pool[:3] + [graphic] + pool[3:], pool


UNSHOWN = "Zorvath quillen drabbet morskel vintrope."


def unshown_look(monkeypatch):
    def look(path, query, size=512, ask=None):
        bad = UNSHOWN in str(query)
        return {"checked": True, "shows": not bad, "burned_in_text": False, "reason": "not that" if bad else "it"}
    monkeypatch.setattr(beat_images, "picture_look", look)


def plan_with_unshown_beat(plan, k, seconds=4.5):
    lines = [f"The temple part {i + 1}." for i in range(len(plan))]
    lines[k] = UNSHOWN
    return [{"start": seconds * i, "end": seconds * (i + 1), "text": t} for i, t in enumerate(lines)]


def test_with_no_illustration_a_refused_hold_gives_way_to_the_beats_matched_photo(tmp_path, monkeypatch):
    unshown_look(monkeypatch)
    plan, pool = graphic_plan(tmp_path, 8)     # 9 beats, 8 photos + a graphic; no crop, film, map or illustration
    beats = plan_with_unshown_beat(plan, 5)
    out, report = scene_planner.fit_to_lines(beats, list(plan), pool, "temple", work_dir=tmp_path / "run",
                                             line_beat={plan[5]: 6})   # the planner found this photo for beat 6
    assert out[5] == plan[5] and report["kept"] == [{"beat": 6, "picture": plan[5].name, "instead_of": plan[4].name}]
    assert report["held"] == [] and not any(pc.is_detail_crop(Path(p)) for p in out)
    ids = [scene_planner.picture_id(Path(p)) for p in out if scene_planner._shot_kind(Path(p)) == "photo"]
    assert len(ids) == len(set(ids))            # keeping the pick repeats nothing
    fits = {b - 1: True for b in report["fits"]}
    _, scenes, durations, _, _ = scene_planner.hold_for_short_pool(beats, out, [4.5] * 9, fits=fits,
                                                                   check=lambda p, line: line != UNSHOWN, available=8)
    assert scene_planner.reel_photo_coverage(scenes, durations)["ok"]


def test_a_photo_not_matched_to_its_line_is_held_as_the_2026_10_29_rule_says(tmp_path, monkeypatch):
    unshown_look(monkeypatch)
    plan, pool = graphic_plan(tmp_path, 8)
    out, report = scene_planner.fit_to_lines(plan_with_unshown_beat(plan, 5), list(plan), pool, "temple",
                                             work_dir=tmp_path / "run", line_beat={plan[5]: 2})   # found for beat 2
    assert not report.get("kept") and out[5] == plan[4] and report["held"][-1]["beat"] == 6


def test_a_pick_shown_on_another_beat_is_not_kept(tmp_path, monkeypatch):
    unshown_look(monkeypatch)
    plan, pool = graphic_plan(tmp_path, 8)
    plan[5] = plan[1]                           # the pick repeats beat 2: keeping it is no better than a hold
    _, report = scene_planner.fit_to_lines(plan_with_unshown_beat(plan, 5), list(plan), pool, "temple",
                                           work_dir=tmp_path / "run", line_beat={plan[5]: 6})
    assert not report.get("kept") and report["held"][-1]["beat"] == 6


def test_holds_the_gate_would_refuse(tmp_path):
    pool = approved_pool(tmp_path / "topic", 4)
    out = list(pool)
    assert scene_planner._hold_refused(out, [4.5] * 4, 2, pool[1], pool)      # 9 s
    assert scene_planner._hold_refused(out, [3.0] * 4, 2, pool[1], pool)      # 6 s, but not a short pool: a repeat
    assert not scene_planner._hold_refused(out, [3.0] * 4, 2, pool[1], pool[:2])  # short pool, 6 s: it may join
    assert scene_planner._hold_refused(out, [3.0] * 4, 2, pool[0], pool[:2])  # the opening again: a cut-back


def test_a_landscape_video_keeps_its_held_shot_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VIDEO_WIDTH", 1920)
    monkeypatch.setattr(config, "VIDEO_HEIGHT", 1080)
    pool = approved_pool(tmp_path / "topic", 4)
    assert not scene_planner._hold_refused(list(pool), [4.5] * 4, 2, pool[1], pool)


# ------------------------------------------------------------------ 3. the crop cap's source
def test_the_crop_cap_cites_the_scope_decision():
    import inspect
    source = inspect.getsource(scene_planner)
    line = next(x for x in source.splitlines() if x.startswith("DETAIL_CROPS_PER_PHOTO ="))
    assert scene_planner.DETAIL_CROPS_PER_PHOTO == 1 and "decisions/QF-58.md" in line and "ruling" not in line
