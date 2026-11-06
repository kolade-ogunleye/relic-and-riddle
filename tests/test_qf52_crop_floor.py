"""Crop-aware sourcing: metadata, decoded pixels, ranking and partial pool quota."""

import io
import json
from typing import ClassVar

import pytest
from PIL import Image

from scripts import fetch_commons_images as fetch
from src import commons, url_guard


def page(title, width, height, **extra):
    return {"title": f"File:Temple {title}.jpg", "imageinfo": [{
        "width": width, "height": height, "mime": "image/jpeg", "size": 2_000_000,
        "url": f"https://upload.wikimedia.org/{title}.jpg", **extra,
        "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"},
                        "Artist": {"value": "Example Photographer"}}}]}


def test_panorama_megapixels_do_not_satisfy_vertical_floor():
    records = [page("panorama", 8000, 1600), page("small", 2000, 1500),
               page("portrait", 1800, 2400)]
    assert [c["title"] for c in commons.candidates(records, ["temple"])] == ["File:Temple portrait.jpg"]


def test_crop_area_and_portrait_bonus_replace_raw_pixel_ranking():
    common = {"license": "CC0", "title": "Temple", "description": "Temple"}
    panorama = {**common, "width": 9000, "height": 3000, "label": "panorama"}
    portrait = {**common, "width": 2250, "height": 3000, "label": "portrait"}
    assert [c["label"] for c in commons.best_first([panorama, portrait], ["temple"])] == ["portrait", "panorama"]


def test_original_selected_when_preview_loses_needed_crop_height():
    record = page("original", 6000, 4000, size=40_000_000,
                  thumburl="https://upload.wikimedia.org/preview.jpg", thumbwidth=1800, thumbheight=1200)
    candidate = next(commons.candidates([record], ["temple"]))
    assert candidate["download_url"].endswith("original.jpg")
    assert (candidate["width"], candidate["height"]) == (6000, 4000)


def test_oversized_original_does_not_excuse_an_undersized_preview():
    record = page("original", 6000, 4000, size=80_000_000,
                  thumburl="https://upload.wikimedia.org/preview.jpg", thumbwidth=1800, thumbheight=1200)
    assert list(commons.candidates([record], ["temple"])) == []


def _download(monkeypatch, size, orientation=None):
    image = Image.new("RGB", size, (90, 80, 70))
    data = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
    image.save(data, "JPEG", exif=exif)

    class Response:
        headers: ClassVar[dict] = {}

        def iter_content(self, chunk_size):
            yield data.getvalue()

        def close(self):
            pass

    monkeypatch.setattr(url_guard, "follow", lambda *a, **k: Response())
    return data.getvalue()


def test_download_checks_real_pixels_before_saving(monkeypatch, tmp_path):
    _download(monkeypatch, (5000, 1500))
    target = tmp_path / "small.jpg"
    with pytest.raises(ValueError, match="crop too small"):
        commons.download_jpeg("https://upload.wikimedia.org/small.jpg", target, min_crop_width=1080)
    assert not target.exists()


def test_download_records_upright_dimensions(monkeypatch, tmp_path):
    _download(monkeypatch, (3000, 2000), orientation=6)
    target = tmp_path / "upright.jpg"
    assert commons.download_jpeg("https://upload.wikimedia.org/upright.jpg", target,
                                 min_crop_width=1350) == (2000, 3000)
    with Image.open(target) as image:
        assert image.size == (2000, 3000)


def pool(monkeypatch, tmp_path):
    records = [page(f"native{i}", 4000, 3000) for i in range(9)]
    records += [page(f"low{i}", 2200, 2100) for i in range(9)]
    dimensions = {p["imageinfo"][0]["url"]: (p["imageinfo"][0]["width"], p["imageinfo"][0]["height"])
                  for p in records}

    def download(url, dest, **kwargs):
        width, height = dimensions[url]
        if min(width, height * 9 / 16) < kwargs.get("min_crop_width", 0):
            raise ValueError("decoded crop below requested floor")
        dest.write_bytes(b"mock JPEG")
        return width, height

    monkeypatch.setattr(fetch, "search", lambda *a, **k: records)
    monkeypatch.setattr(fetch, "search_graded", lambda *a, **k: [])
    monkeypatch.setattr(fetch, "find_categories", lambda *a, **k: [])
    monkeypatch.setattr(fetch, "search_openverse", lambda *a, **k: [])
    monkeypatch.setattr(fetch, "download_jpeg", download)
    monkeypatch.setattr("src.storyboard.generate_storyboard", lambda *a, **k: None)
    fetch.fetch_topic({"id": "crop-fixture", "title": "Temple"},
                      {"queries": ["Temple"], "keywords": ["temple"], "museums": False},
                      allow_sa=False, target_images=20, assets_dir=tmp_path)
    return json.loads((tmp_path / "crop-fixture" / "credits.json").read_text())["images"]


def test_incomplete_pool_still_has_at_most_ten_percent_low_crops(monkeypatch, tmp_path):
    images = pool(monkeypatch, tmp_path)
    assert len(images) == 10
    widths = [min(c["width"], c["height"] * 9 / 16) for c in images]
    assert min(widths) >= 1080
    assert sum(w < 1350 for w in widths) / len(widths) <= 0.10
    assert all(c["vertical_crop_width"] == int(w) for c, w in zip(images, widths))
