"""The selectable covers preserve source photographs inside the profile crop.

Corner markers catch the earlier double crop: a landscape source was first
centre-filled to 9:16, then cropped again to a square, losing the actual subject.
These tests check delivered pixels and text placement, not semantic detection.
"""
import socket

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import still_layout
from src.visual_engine import VisualEngine


SELECTABLE_STYLES = ("depth_sandwich", "badge", "minimal")
PAYLOAD = {"thumbnail": {
    "eyebrow": "ANCIENT ENGINEERING",
    "line1": "THE 144-FOOT",
    "line2": "WHISPERING DOME",
    "tagline": "BUILT WITHOUT RINGS",
}}
MARKERS = ((216, 64, 56), (62, 190, 83), (58, 98, 218), (191, 66, 194))


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def unexpected_network(*args, **kwargs):
        raise AssertionError("Cover composition must not call an external service")

    monkeypatch.setattr(socket.socket, "connect", unexpected_network)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected_network)


def _marked_photo(tmp_path, size):
    photo = Image.new("RGB", size, (100, 112, 126))
    draw = ImageDraw.Draw(photo)
    width, height = size
    edge = 100
    centers = []
    for (left, top), colour in zip(
        ((0, 0), (width - edge, 0), (0, height - edge), (width - edge, height - edge)), MARKERS
    ):
        draw.rectangle((left, top, left + edge - 1, top + edge - 1), fill=colour)
        centers.append((left + edge / 2, top + edge / 2))
    path = tmp_path / "complete-photo.png"
    photo.save(path)
    return path, centers


def _assert_complete_photo(cover, grid, size, centers):
    record = still_layout.load(cover)
    assert record is not None and not record["problems"]
    composition = record["recipe"]["composition"]
    assert composition["mode"] == "contain"
    assert tuple(composition["source_size"]) == size
    assert composition["source_box"] == [0, 0, *size]
    x0, y0, x1, y1 = composition["image_box"]
    # Independent of the renderer's crop helpers: this exact square is delivered.
    assert 0 <= x0 < x1 <= 1080 and 420 <= y0 < y1 <= 1500
    width, height = size
    # A one-pixel rounding difference is allowed; stretching the source is not.
    assert abs((x1 - x0) / width - (y1 - y0) / height) <= max(1 / width, 1 / height)
    for element in record["elements"]:
        if element.get("font"):
            assert not still_layout.intersects(tuple(element["box"]), (x0, y0, x1, y1)), element
    with Image.open(cover) as portrait, Image.open(grid) as square:
        assert portrait.size == (1080, 1920) and square.size == (1080, 1080)
        for (sx, sy), expected in zip(centers, MARKERS):
            px = round(x0 + sx * (x1 - x0) / width)
            py = round(y0 + sy * (y1 - y0) / height)
            for image, offset in ((portrait, 0), (square, 420)):
                patch = np.asarray(image.convert("RGB").crop((px - 2, py - offset - 2, px + 3, py - offset + 3)))
                actual = np.median(patch, axis=(0, 1))
                # Markers sit far from boundaries: JPEG should only introduce a
                # few channel levels of error, not shade, mask or erase corners.
                assert np.max(np.abs(actual - expected)) < 16, (expected, actual)
    return record


@pytest.mark.parametrize("size", [(1600, 800), (800, 1600)])
@pytest.mark.parametrize("style", SELECTABLE_STYLES)
def test_source_edges_survive_without_stretch_or_type_overlap(tmp_path, size, style):
    photo, centers = _marked_photo(tmp_path, size)
    cover, grid = VisualEngine().generate_thumbnails(photo, PAYLOAD, tmp_path / "out", style=style, variants=False)
    record = _assert_complete_photo(cover, grid, size, centers)
    assert not record["shortened"]
    assert {"eyebrow", "title_1", "title_2", "tagline"} <= {e["name"] for e in record["elements"]}


@pytest.mark.parametrize("style", SELECTABLE_STYLES)
def test_repair_reloads_original_source_and_refreshes_composition(tmp_path, style):
    size = (1600, 800)
    photo, centers = _marked_photo(tmp_path, size)
    cover, grid = VisualEngine().generate_thumbnails(photo, PAYLOAD, tmp_path / "out", style=style, variants=False)
    before = _assert_complete_photo(cover, grid, size, centers)
    result = still_layout.redraw(cover)
    assert result is not None and result["tight"]
    assert result["attempts"] == before["attempts"] + 1
    _assert_complete_photo(cover, grid, size, centers)


@pytest.mark.parametrize("style", ["badge", "lower_third"])
def test_reviewed_feature_keeps_its_colour_and_clearance_on_redraw(tmp_path, monkeypatch, style):
    import hashlib
    import json
    from src import config

    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / "data").mkdir()
    photo = Image.new("RGB", (1600, 900), (65, 100, 130))
    subject_colour = (190, 120, 50)
    ImageDraw.Draw(photo).rectangle((480, 180, 1120, 630), fill=subject_colour)
    hero = tmp_path / "reviewed.png"
    photo.save(hero)
    focus = {"focus_box": [.3, .2, .7, .7], "sha256": hashlib.sha256(hero.read_bytes()).hexdigest(),
             "reviewed": "test review"}
    registry = tmp_path / "data/cover_art_direction.json"
    registry.write_text(json.dumps({"photos": [focus]}), encoding="utf-8")
    cover, _ = VisualEngine().generate_thumbnails(hero, PAYLOAD, tmp_path / style, style=style, variants=False)
    for repair in (False, True):
        if repair:
            # Repair the typography using the saved reviewed framing, even if
            # the registry is unavailable after the original render.
            registry.unlink()
            assert still_layout.redraw(cover) is not None
        record = still_layout.load(cover)
        assert not record["problems"]
        assert record["recipe"]["focus"] == focus
        composition = record["recipe"]["composition"]
        assert composition["mode"] == "reviewed_focus"
        x0, y0, x1, y1 = composition["protected_box"]
        assert 0 <= x0 < x1 <= 1080 and 420 <= y0 < y1 <= 1500
        for element in record["elements"]:
            assert not still_layout.intersects(tuple(element["box"]), (x0, y0, x1, y1))
        with Image.open(cover) as im:
            # Sample inside each feature corner: no black gradient or feather
            # is permitted to wash out the protected photographic area.
            for x, y in [(x0 + 12, y0 + 12), (x1 - 12, y0 + 12),
                         (x0 + 12, y1 - 12), (x1 - 12, y1 - 12)]:
                actual = np.asarray(im.crop((x - 2, y - 2, x + 3, y + 3))).mean(axis=(0, 1))
                assert np.max(np.abs(actual - subject_colour)) < 8


def test_reviewed_coordinates_never_follow_a_changed_or_invalid_photo(tmp_path, monkeypatch):
    import hashlib
    import json
    from src import config
    from src.cover_design import reviewed_focus

    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / "data").mkdir()
    hero = tmp_path / "photo.png"
    Image.new("RGB", (120, 100), (80, 90, 100)).save(hero)
    digest = hashlib.sha256(hero.read_bytes()).hexdigest()
    entry = {"focus_box": [.2, .1, .8, .9], "sha256": digest, "reviewed": "test"}
    registry = tmp_path / "data/cover_art_direction.json"
    registry.write_text(json.dumps({"photos": [entry]}), encoding="utf-8")
    assert reviewed_focus(hero) == entry
    for box in ([.8, .1, .2, .9], [-.1, 0, 1, 1], [0, 0, float("nan"), 1], [0, 0, 1]):
        assert reviewed_focus(hero, {**entry, "focus_box": box}) is None
    Image.new("RGB", (120, 100), (100, 90, 80)).save(hero)
    assert reviewed_focus(hero) is None and reviewed_focus(hero, entry) is None
