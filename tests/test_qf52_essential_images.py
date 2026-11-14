"""Explicit exceptions preserve complete images through preparation and delivery."""

import importlib
import json
import subprocess

import numpy as np
import pytest
from PIL import Image

from src import commons, upscale


def policy():
    return importlib.import_module("src.image_presentation")


def photo(tmp_path, name="source.png", size=(200, 150)):
    path = tmp_path / name
    image = Image.new("RGB", size, "white")
    image.save(path)
    return path


def test_exact_designation_admits_small_image_without_waiving_rights():
    from tests.test_qf52_crop_floor import page
    record = page("essential", 800, 600)
    title = record["title"]
    assert not list(commons.candidates([record], ["temple"]))
    candidate = next(commons.candidates([record], ["temple"], essential_titles={title: "Only view of inscription"}))
    assert candidate["presentation"] == "full_image"
    record["imageinfo"][0]["extmetadata"]["LicenseShortName"]["value"] = "All rights reserved"
    assert not list(commons.candidates([record], ["temple"], essential_titles={title: "Key image"}))


def test_preparation_never_calls_upscaler_for_essential_source(monkeypatch, tmp_path):
    path = photo(tmp_path)
    policy().designate(path, "Only surviving source")
    monkeypatch.setattr(upscale, "backend", lambda: pytest.fail("automatic enlargement"))
    assert upscale.ensure_resolution(path) == path


def test_approved_derivative_is_exact_and_does_not_undo_privacy(tmp_path):
    from src.lineage import record_derived
    path = photo(tmp_path)
    enlarged = photo(tmp_path, "reviewed.png", (400, 300))
    private = photo(tmp_path, "private.png", (400, 300))
    p = policy()
    p.designate(path, "Essential")
    p.reviewed_enlargement(path, enlarged, "Whole image checked at delivery size")
    assert upscale.ensure_resolution(path) == enlarged
    record_derived(private, [enlarged], "face_blur")
    assert p.full_image(private)
    assert upscale.ensure_resolution(private) == private
    Image.new("RGB", (400, 300), "red").save(enlarged)
    with pytest.raises(ValueError, match="changed"):
        upscale.ensure_resolution(path)


def test_old_cropped_derivative_cannot_reenter_after_designation(tmp_path):
    from src.lineage import record_derived
    path = photo(tmp_path)
    cropped = photo(tmp_path, "crop.png", (100, 100))
    record_derived(cropped, [path], "text_crop")
    policy().designate(path, "Essential")
    with pytest.raises(ValueError, match="cached crop"):
        upscale.ensure_resolution(cropped)


def test_designation_changes_render_cache_inputs_and_rejects_changed_source(tmp_path):
    path = photo(tmp_path)
    p = policy()
    before = p.policy_inputs([path])
    p.designate(path, "Essential")
    assert p.policy_inputs([path]) != before
    Image.new("RGB", (200, 150), "red").save(path)
    with pytest.raises(ValueError, match="Stale"):
        p.full_image(path)


def test_crops_and_animation_do_not_replace_essential_photo(monkeypatch, tmp_path):
    from src import (
        beat_images,
        parallax,
        picture_coverage,
        scene_planner,
        video_composer,
    )
    path = photo(tmp_path)
    policy().designate(path, "Essential")
    assert beat_images.crop_text_band(path, "bottom", [900, 0, 1000, 1000]) is None
    assert picture_coverage.detail_crop(path, tmp_path, [], 9 / 16) is None
    assert picture_coverage.located_crop(path, tmp_path, [], 9 / 16, "inscription",
                                        locate=lambda *a: pytest.fail("locator called")) is None
    assert not scene_planner.strong_shot(path)
    monkeypatch.setattr(parallax, "available", lambda: True)
    monkeypatch.setattr(parallax, "_session", lambda: None)
    monkeypatch.setattr(parallax, "render_clip", lambda *a, **k: pytest.fail("parallax called"))
    assert parallax.render_scenes([path], [2.0], tmp_path / "clips", 0) == [path]
    assert video_composer.loop_motion([path], [2.0], 2.0, set()) == {
        "close_zoom": 1.0, "close_push": 0.0, "open_push": 0.0}


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_real_filter_preserves_native_pixels_and_all_edges(tmp_path, size):
    path = photo(tmp_path, size=(200, 150))
    p = policy()
    p.designate(path, "Essential")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", p.fit_filter(path, *size),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    arr = np.frombuffer(raw, np.uint8).reshape(size[1], size[0], 3)
    ys, xs = np.where(arr[..., 0] > 200)
    assert (xs.max() - xs.min() + 1, ys.max() - ys.min() + 1) == (200, 150)
    assert len(xs) == 200 * 150


def test_social_and_cover_photo_helpers_preserve_native_extent(tmp_path):
    from src import cover_design, photo_hygiene, typography, youtube_thumbnail
    path = photo(tmp_path)
    p = policy()
    p.designate(path, "Essential")
    with Image.open(path) as raw:
        source = raw.convert("RGBA")
    source.info["essential_full_image"] = True
    clean, focus = photo_hygiene.clean_hero(source, {"focus_box": [0.2, 0.2, 0.8, 0.8]})
    assert clean.size == source.size and focus is None
    for canvas in (typography.cover_crop(path, (1080, 1920)),
                   cover_design._full_bleed(source, None, 960, None),
                   youtube_thumbnail._photo(source, None)):
        arr = np.asarray(canvas)
        assert int((arr[..., 0] > 200).sum()) == 200 * 150


@pytest.mark.parametrize("already_full", [False, True])
def test_fetch_preserves_exception_bytes_and_separate_credit(monkeypatch, tmp_path, already_full):
    from scripts import fetch_commons_images as fetch
    from tests.test_qf52_crop_floor import _download, page
    source_bytes = _download(monkeypatch, (800, 600))
    record = page("essential", 800, 600)
    if already_full:
        from src.credits import save_credits
        topic = tmp_path / "essential"
        topic.mkdir()
        existing = photo(topic, "existing.png", (2250, 3000))
        save_credits(topic, {"images": [{"file": existing.name, "title": "Existing temple photo",
                                        "width": 2250, "height": 3000}]})
    monkeypatch.setattr(fetch, "search", lambda *a, **k: [record])
    monkeypatch.setattr(fetch, "search_graded", lambda *a, **k: [])
    monkeypatch.setattr(fetch, "find_categories", lambda *a, **k: [])
    monkeypatch.setattr(fetch, "search_openverse", lambda *a, **k: [])
    monkeypatch.setattr("src.storyboard.generate_storyboard", lambda *a, **k: None)
    assert fetch.fetch_topic({"id": "essential", "title": "Temple"}, {
        "queries": ["Temple"], "keywords": ["temple"], "museums": False,
        "exclude": ["unrelated fixture"],
        "essential_images": {record["title"]: "Only surviving source"}},
        False, target_images=1, assets_dir=tmp_path) == 1 + int(already_full)
    records = json.loads((tmp_path / "essential" / "credits.json").read_text())["images"]
    entry = next(r for r in records if r["title"] == record["title"])
    assert entry["vertical_crop_width"] == 337
    assert entry["presentation"] == "full_image"
    assert (tmp_path / "essential" / entry["preserved_source"]).read_bytes() == source_bytes
    assert policy().full_image(tmp_path / "essential" / entry["file"])


def test_compositor_uses_complete_static_image_before_macro_zoom(monkeypatch, tmp_path):
    from src import video_composer as vc
    path = photo(tmp_path)
    policy().designate(path, "Essential")
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=48000", "-t", "1.5", str(voice)], check=True)
    monkeypatch.setattr(vc.VideoComposer, "calibrate_gain", lambda *a, **k: 0.0)
    graphs = []

    class GraphReady(Exception):
        pass

    def capture(cmd, *args, **kwargs):
        if "-filter_complex" in cmd:
            graphs.append(cmd[cmd.index("-filter_complex") + 1])
            raise GraphReady
        pytest.fail("Unexpected compositor command")

    monkeypatch.setattr(vc, "_run_ffmpeg", capture)
    with pytest.raises(GraphReady):
        vc.VideoComposer().compose_video([path], voice, voice, [], 1.0, tmp_path / "master.mp4",
                                         durations=[1.0], macro_drifts=[0])
    source = graphs[0].split("[v0]")[0]
    assert "scale=200:150:flags=lanczos,pad=1080:1920" in source
    assert "zoompan" not in source and "crop=" not in source
