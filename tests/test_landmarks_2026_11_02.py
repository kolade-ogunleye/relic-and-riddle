"""Scale-graphic landmarks redrawn as look-alike flat illustrations (2026-11-02), and the rolling-stone door and
ventilation diagrams retired from make_all. Each drawing is exactly its reference's true height on the scale and
its drawn width is the width _silhouette_width declares (so the layout's room and the scale stay honest), in brand
colours only; an old payload carrying a retired diagram key still renders its other graphics."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, graphics
from src.reference_facts import REFERENCE_FILE, references

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
BRAND = {brand.INK, brand.INK2, brand.BONE, brand.STONE, brand.STONE_LT, brand.GOLD, brand.GOLD_DIM}
# Each reference's true width per unit of height, from its source's figures (see SILHOUETTE_WIDTH).
TRUE_WIDTH = {"R_PYRAMID": 230.33 / 146.5, "R_ELIZABETH": 12.0 / 96.3}


def _drawn(label: str, h: int) -> tuple[np.ndarray, int, int]:
    size = int(h * 1.8) + 40
    im = Image.new("RGB", (size, size), brand.INK)
    base = size - 10
    graphics._silhouette(ImageDraw.Draw(im, "RGBA"), label, size / 2, base, h, brand.STONE_LT)
    return np.asarray(im).astype(np.int16), base, size


@pytest.mark.parametrize("ref", references(), ids=lambda r: r["id"])
@pytest.mark.parametrize("h", [600, 240, 80])
def test_drawing_is_the_references_height_and_its_declared_width(ref, h):
    arr, base, _ = _drawn(ref["label"], h)
    ink = np.abs(arr - np.array(brand.INK, np.int16)).max(axis=2) > 0
    ys, xs = np.nonzero(ink)
    assert ys.max() <= base and base - ys.min() == pytest.approx(h, abs=2)        # its true height on the scale
    declared = graphics._silhouette_width(ref["label"])
    assert (xs.max() - xs.min() + 1) / h == pytest.approx(declared, abs=0.012 + 3 / h)  # its room (+ a pixel's rounding)
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    assert graphics._silhouette(d, ref["label"], 4, 8, h, brand.STONE_LT) == pytest.approx(declared * h)


@pytest.mark.parametrize("ref_id,true", sorted(TRUE_WIDTH.items()))
def test_proportions_match_the_real_structure(ref_id, true):
    label = next(r["label"] for r in references() if r["id"] == ref_id)
    arr, base, _size = _drawn(label, 600)
    ink = np.abs(arr - np.array(brand.INK, np.int16)).max(axis=2) > 0
    row = np.nonzero(ink[base - 3])[0]                     # the footprint at ground level
    assert (row.max() - row.min() + 1) / 600 == pytest.approx(true, rel=0.08)


def test_pyramid_faces_rise_at_51_8_degrees_to_the_original_apex():
    arr, base, size = _drawn("Great Pyramid (original)", 600)
    ink = np.abs(arr - np.array(brand.INK, np.int16)).max(axis=2) > 0
    left = [np.nonzero(ink[base - k])[0].min() for k in (50, 450)]
    slope = np.degrees(np.arctan2(400, left[1] - left[0]))
    assert slope == pytest.approx(51.8, abs=0.6)
    assert ink[base - 598, size // 2 - 2: size // 2 + 3].any()                   # a sharp apex, not truncated


@pytest.mark.parametrize("ref", references(), ids=lambda r: r["id"])
def test_drawings_use_brand_colours_and_shade_one_side(ref):
    arr, _, _ = _drawn(ref["label"], 600)
    colours = {tuple(c) for c in np.unique(arr.reshape(-1, 3), axis=0)}
    assert colours <= {tuple(c) for c in BRAND}, colours - {tuple(c) for c in BRAND}
    if ref["id"] != "R_US_HEIGHT":
        assert tuple(brand.STONE) in colours                                      # a lit and a shaded side


def test_every_reference_cites_its_source_and_when_it_was_checked():
    data = json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))
    for ref in data["references"]:
        assert ref["source_url"].startswith("https://") and ref["quote"] and ref["statement"], ref["id"]
        assert ref.get("checked_on") or data.get("checked_on"), ref["id"]
        assert ref["feet"] > 0


def test_make_all_no_longer_draws_the_retired_diagrams(tmp_path, monkeypatch):
    monkeypatch.setattr(graphics.geo, "locate", lambda topic: None)
    made = []
    monkeypatch.setattr(graphics, "render_rolling_stone_defense", lambda *a, **k: made.append("defense"))
    monkeypatch.setattr(graphics, "render_ventilation_airflow", lambda *a, **k: made.append("ventilation"))
    payload = {"rolling_stone_defense": {"title": "Rolling Stone Door Defense Mechanism"},
               "ventilation_airflow": {"title": "Subterranean Ventilation & Convection Dynamics"}}
    assert graphics.make_all(payload, {"id": "derinkuyu_underground_city"}, tmp_path) == []
    assert made == [] and set(graphics.RETIRED_DIAGRAM_KEYS) == set(payload)


@needs_ffmpeg
def test_an_old_payload_with_retired_keys_still_renders_its_other_graphics(tmp_path, monkeypatch):
    monkeypatch.setattr(graphics.geo, "locate", lambda topic: None)
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 1.0)
    payload = {"rolling_stone_defense": {"title": "Rolling Stone Door Defense Mechanism"},
               "ventilation_airflow": {"labels": {"summary": "Sheltered 20,000 residents"}},
               "scale_fact": {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu depth",
                              "approximate": True}}
    made = graphics.make_all(payload, {"id": "derinkuyu_underground_city"}, tmp_path)
    assert [p.name for p in made] == ["graphic_scale.mp4"]
    assert not list(Path(tmp_path).glob("graphic_defense*")) and not list(Path(tmp_path).glob("graphic_ventilation*"))
