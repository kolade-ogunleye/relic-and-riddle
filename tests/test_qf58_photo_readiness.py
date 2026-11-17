"""Distinct photos, bounded refill, refresh and final Reel coverage use real policies."""

import importlib
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from PIL import Image

from src import image_review, picture_coverage, scene_planner
from src.credits import load_credits, save_credits


def photos(count):
    return [{"file": f"{i}.jpg", "shot_class": ("wide", "interior", "detail")[i % 3],
             "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}}
            for i in range(count)]


def stills(folder, count=9):
    out = []
    for index in range(count):
        path = folder / f"{index}.png"
        Image.fromarray(np.random.default_rng(index).integers(0, 256, (40, 40, 3), dtype=np.uint8)).save(path)
        out.append(path)
    return out


def pipeline():
    from src import asset_pipeline
    return importlib.reload(asset_pipeline)


def save_pool(folder, credits):
    for index, image in enumerate(credits["images"]):
        pixels = np.random.default_rng(index).integers(0, 256, (40, 40, 3), dtype=np.uint8)
        Image.fromarray(pixels).save(folder / image["file"])
    save_credits(folder, credits)


def test_five_usable_photos_no_longer_make_a_ready_reel():
    result = image_review.readiness(photos(5))
    assert result["need"] == 9 and not result["ready"] and result["short"] == 4


def test_nine_records_of_one_source_count_once():
    images = photos(9)
    for image in images:
        image["source_sha256"] = "a" * 64
    assert image_review.readiness(images)["usable"] == 1


def test_near_duplicate_photos_cannot_fill_readiness():
    images = photos(9)
    for index, image in enumerate(images):
        # pHash + dHash within QF-53b's near-duplicate distance of the first photo
        image["phash"], image["dhash"] = f"{index:016x}", "0" * 16
    assert image_review.readiness(images)["usable"] == 1


def test_readiness_rechecks_actual_bytes_instead_of_stale_record_hashes(tmp_path):
    ap = pipeline()
    folder = tmp_path / "topic"
    folder.mkdir()
    save_pool(folder, {"images": photos(9), "reviewed": True})
    assert ap.pool_ready("topic", tmp_path)
    for image in photos(9)[1:]:
        (folder / image["file"]).write_bytes((folder / "0.jpg").read_bytes())
    assert not ap.pool_ready("topic", tmp_path)


def test_uncredited_folder_needs_nine_distinct_decodable_photos(tmp_path):
    from src.visual_engine import MissingAssetsError, VisualEngine
    folder = tmp_path / "topic"
    folder.mkdir()
    images = stills(folder)
    images[-1].write_bytes(images[0].read_bytes())
    with pytest.raises(MissingAssetsError):
        VisualEngine(tmp_path).get_scene_sequence({"id": "topic"})


def test_readiness_follows_the_actual_photo_beats():
    ap = pipeline()
    assert image_review.required_photos([{"media_type": "photo"}] * 14) == 14
    assert image_review.required_photos([{"media_type": "graphic"}] * 14) == 9
    assert callable(ap.ensure_pool)


def test_old_small_automatic_pool_is_refreshed_but_human_decision_stands():
    ap = pipeline()
    now = datetime(2026, 11, 6, tzinfo=UTC)
    credit = {"images": photos(9), "pool_fetched_at": (now - timedelta(days=90)).isoformat()}
    assert ap.refresh_due(credit, now)
    credit["reviewed_by"] = "operator"
    assert not ap.refresh_due(credit, now)
    credit.pop("reviewed_by")
    credit["images"] = photos(12)
    assert not ap.refresh_due(credit, now)


def test_refill_continues_after_review_until_class_quota_is_met(tmp_path, monkeypatch):
    ap = pipeline()
    folder = tmp_path / "topic"
    folder.mkdir()
    save_credits(folder, {"images": [], "reviewed": False})
    calls = []

    def fetch(tid, assets, target=None):
        calls.append(target)
        images = photos(9 if len(calls) == 1 else 12)
        if len(calls) == 1:
            for image in images:
                image["shot_class"] = "wide"
        save_pool(folder, {"images": images, "reviewed": False})
        return True

    def review(*args):
        credits = load_credits(folder)
        credits["reviewed"] = True
        save_credits(folder, credits)
        return {"status": "approved", "kept": len(credits["images"])}

    monkeypatch.setattr(ap, "_fetch", fetch)
    monkeypatch.setattr(image_review, "review_pool", review)
    assert ap.ensure_pool({"id": "topic"}, tmp_path)
    assert len(calls) == 2
    assert load_credits(folder)["pool_fetched_at"]


def test_current_pool_is_refilled_for_fourteen_actual_beats(tmp_path, monkeypatch):
    ap = pipeline()
    folder = tmp_path / "topic"
    folder.mkdir()
    save_pool(folder, {"images": photos(12), "reviewed": True})
    fetched = []

    def fetch(*args):
        fetched.append(True)
        save_pool(folder, {"images": photos(15), "reviewed": True})
        return True

    monkeypatch.setattr(ap, "_fetch", fetch)
    monkeypatch.setattr(image_review, "review_pool", lambda *args: {"status": "approved", "kept": 15})
    assert ap.ensure_pool({"id": "topic"}, tmp_path, beats=[{}] * 14)
    assert fetched == [True]


@pytest.mark.parametrize("fault", ["repeat", "rename", "reencode", "long", "short"])
def test_final_reel_gate_refuses_coverage_defects(tmp_path, fault):
    scenes = stills(tmp_path)
    durations = [4.0] * 9
    if fault == "repeat":
        scenes[-1] = scenes[0]
    elif fault == "rename":
        scenes[-1].write_bytes(scenes[0].read_bytes())
    elif fault == "reencode":
        with Image.open(scenes[0]) as image:
            image.save(scenes[-1], format="JPEG", quality=90)
    elif fault == "long":
        durations[-1] = 6.1
    else:
        scenes, durations = scenes[:8], durations[:8]
    assert not scene_planner.reel_photo_coverage(scenes, durations)["ok"]


def test_final_reel_gate_accepts_nine_distinct_photos_at_six_seconds(tmp_path):
    scenes = stills(tmp_path)
    assert scene_planner.reel_photo_coverage(scenes, [6.0] * 9)["ok"]


def test_final_reel_gate_accepts_graphics_coexistence(tmp_path):
    scenes = stills(tmp_path, count=8)
    graphic1 = tmp_path / "graphic_map.png"
    graphic2 = tmp_path / "graphic_timeline.png"
    Image.new("RGB", (40, 40)).save(graphic1)
    Image.new("RGB", (40, 40)).save(graphic2)
    plan_scenes = scenes[:4] + [graphic1] + scenes[4:] + [graphic2]
    durations = [4.0] * len(plan_scenes)
    cov = scene_planner.reel_photo_coverage(plan_scenes, durations)
    assert cov["ok"]
    assert cov["distinct"] == 8
    assert cov["photo_beats"] == 8
    plan_with_repeat = list(plan_scenes)
    plan_with_repeat[0] = plan_with_repeat[1]
    assert not scene_planner.reel_photo_coverage(plan_with_repeat, durations)["ok"]


def test_hold_rule_has_a_six_second_ceiling():
    assert scene_planner.HELD_SHOT_MAX == 6.0


# ------------------------------------------------------------------ the gate as ruled (decisions/QF-58-gate.md, Option A)
def crop_of(photo, folder, n=1):
    """A detail crop of `photo` with its lineage record, as picture_coverage.detail_crop saves one."""
    from src.lineage import record_derived
    crop = folder / "detail_crops" / str(n) / photo.name
    crop.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(photo) as image:
        image.crop((0, 0, 20, 36)).save(crop)
    record_derived(crop, [photo], picture_coverage.DETAIL_OPERATION, {"box": [0, 0, 0.5, 0.9]})
    return crop


def test_a_picture_shown_on_two_beats_is_not_two_distinct_photos(tmp_path):
    scenes = stills(tmp_path)
    held = scenes[:3] + [scenes[2]] + scenes[3:]     # beats 3-4 show one picture: 10 photo beats, 9 photos
    cov = scene_planner.reel_photo_coverage(held, [4.0, 4.0, 3.0, 3.0] + [4.0] * 6)
    assert not cov["ok"] and cov["photo_beats"] == 10 and cov["distinct"] == 9
    cut_back = scenes + [scenes[2]]                  # the same picture after another one
    assert scene_planner.reel_photo_coverage(cut_back, [4.0] * 10)["repeats"] == [{"beat": 10, "first": 3}]


def test_crops_never_stand_in_for_distinct_photos(tmp_path):
    scenes = stills(tmp_path)
    one_photo = [scenes[0]] + [crop_of(scenes[0], tmp_path, n) for n in range(1, 9)]   # the reviewer's probe
    cov = scene_planner.reel_photo_coverage(one_photo, [4.0] * 9)
    assert not cov["ok"] and cov["distinct"] == 1 and cov["detail_crops"] == 8
    eight_and_a_crop = scenes[:8] + [crop_of(scenes[0], tmp_path, 20)]                # photos only: needs 9
    assert not scene_planner.reel_photo_coverage(eight_and_a_crop, [4.0] * 9)["ok"]
    nine_and_a_crop = scenes + [crop_of(scenes[3], tmp_path, 21)]                     # 9 photos + one crop: admitted
    cov = scene_planner.reel_photo_coverage(nine_and_a_crop, [4.0] * 10)
    assert cov["ok"] and cov["distinct"] == 9 and cov["detail_crops"] == 1
    two_of_one = scenes + [crop_of(scenes[3], tmp_path, 22), crop_of(scenes[3], tmp_path, 23)]
    assert not scene_planner.reel_photo_coverage(two_of_one, [4.0] * 11)["ok"]        # one crop per photo


def test_a_photo_and_its_crops_on_consecutive_beats_are_one_hold(tmp_path):
    scenes = stills(tmp_path)
    a = scenes[0]
    probe = [a, crop_of(a, tmp_path, 1), crop_of(a, tmp_path, 2)] + scenes[1:]        # 18 s of one photograph
    cov = scene_planner.reel_photo_coverage(probe, [6.0, 6.0, 6.0] + [4.0] * 8)
    assert not cov["ok"] and cov["long_holds"][0] == {"beats": [1, 2, 3], "seconds": 18.0}
    short = [a, crop_of(a, tmp_path, 3)] + scenes[1:]                                 # 3 + 3 s: one 6 s hold
    assert scene_planner.reel_photo_coverage(short, [3.0, 3.0] + [4.0] * 8)["ok"]
    assert not scene_planner.reel_photo_coverage(short, [3.0, 3.5] + [4.0] * 8)["ok"]


def test_the_photo_beats_a_script_will_have_are_estimated_before_the_voice():
    words = " ".join(["word"] * 106)
    assert scene_planner.estimated_photo_beats(words, graphics=True) == 10       # 11 beats, one for a graphic
    assert scene_planner.estimated_photo_beats(words, graphics=False) == 11
    assert scene_planner.estimated_photo_beats("", graphics=False) == 0


def words_for(beats, pause_after=None):
    """Two words a beat, spoken in its first 3 s; `pause_after`: (0-based beat, seconds of speech) instead."""
    out = []
    for k, beat in enumerate(beats):
        talk = 3.0 if pause_after is None or pause_after[0] != k else pause_after[1]
        out += [{"word": "a", "start": beat["start"], "dur": talk / 2 - 0.05},
                {"word": "b.", "start": beat["start"] + talk / 2, "dur": talk / 2 - 0.05}]
    return out


def test_a_pause_that_keeps_a_photo_past_six_seconds_moves_its_cut(tmp_path):
    scenes = stills(tmp_path, count=8)
    graphic = tmp_path / "graphic_numbers.mp4"
    graphic.write_bytes(b"")
    plan = scenes[:6] + [graphic] + scenes[6:]
    durations = [4.0] * 5 + [6.32, 4.0, 4.0, 4.0]   # the production Amber Room case: 6.32 s on one photo
    starts = [sum(durations[:k]) for k in range(len(durations))]
    beats = [{"start": s, "end": s + d, "text": "a b."} for s, d in zip(starts, durations)]
    assert not scene_planner.reel_photo_coverage(plan, durations)["ok"]
    words = words_for(beats, pause_after=(5, 4.6))
    new_beats, new_durations, moves = scene_planner.fit_holds(beats, plan, durations, words)
    assert moves == [{"beat": 6, "cut": "earlier", "seconds": 0.32}]
    assert new_durations[5] == pytest.approx(6.0) and new_durations[6] == pytest.approx(4.32)
    assert sum(new_durations) == pytest.approx(sum(durations))
    assert new_beats[5]["end"] == new_beats[6]["start"] == pytest.approx(starts[5] + 6.0)
    assert scene_planner.reel_photo_coverage(plan, new_durations)["ok"]
    # words that run past the limit keep their cut, and the gate refuses the plan
    spoken = words_for(beats, pause_after=(5, 6.3))
    assert scene_planner.fit_holds(beats, plan, durations, spoken)[2] == []


def test_a_pool_at_the_floor_fetched_this_period_is_not_searched_again_for_more_beats(tmp_path, monkeypatch):
    ap = pipeline()
    folder = tmp_path / "topic"
    folder.mkdir()
    save_pool(folder, {"images": photos(9), "reviewed": True, "reviewed_by": "vision",
                       "pool_fetched_at": (datetime.now(UTC) - timedelta(days=3)).isoformat()})
    fetched = []
    monkeypatch.setattr(ap, "_fetch", lambda *args: fetched.append(args) or True)
    monkeypatch.setattr(image_review, "review_pool", lambda *args: {"status": "approved", "kept": 9})
    assert ap.ensure_pool({"id": "topic"}, tmp_path, beats=[{}] * 11)
    assert ap.ensure_pool({"id": "topic"}, tmp_path)
    assert fetched == []


def test_a_small_pool_with_no_recorded_fetch_is_searched_once_and_recorded(tmp_path, monkeypatch):
    ap = pipeline()
    folder = tmp_path / "topic"
    folder.mkdir()
    save_pool(folder, {"images": photos(9), "reviewed": True, "reviewed_by": "vision"})
    fetched = []
    monkeypatch.setattr(ap, "_fetch", lambda *args: fetched.append(args) or True)
    monkeypatch.setattr(image_review, "review_pool", lambda *args: {"status": "approved", "kept": 9})
    assert ap.ensure_pool({"id": "topic"}, tmp_path)        # nothing new found: ready at the floor
    assert len(fetched) == 1 and ap.fetched_recently(load_credits(folder))
    assert ap.ensure_pool({"id": "topic"}, tmp_path) and len(fetched) == 1


def test_unknown_shot_classes_never_meet_the_variety_target():
    ap = pipeline()
    images = photos(9)
    for image in images:
        image["shot_class"] = "unknown"
    assert not ap.shot_quotas_met(images)
    assert ap.shot_quotas_met(photos(9))
