"""The 9:16 timeline as a time scale (2026-11-01): the years elapsed shown after each date, the entries spaced by
the time between them where there is room, and the topic's photo as a dark ground."""

import json
from itertools import pairwise
from pathlib import Path

import pytest

from src import graphics, safe_zones

needs_ffmpeg = pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")

DERINKUYU = [{"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
             {"year": 780, "approximate": True, "label": "Expanded against raiders"},
             {"year": 1923, "approximate": False, "label": "Abandoned after the population exchange"},
             {"year": 1963, "approximate": False, "label": "Rediscovered behind a wall"}]


def test_years_between_has_no_year_zero() -> None:
    assert graphics.years_between(-700, 780) == 1479
    assert graphics.years_between(1923, 1963) == 40
    assert graphics.years_between(-1, 1) == 1


def test_gap_wording() -> None:
    assert graphics.gap_text(DERINKUYU[0], DERINKUYU[1]) == "ABOUT 1,500 YEARS LATER"  # rounded: c. dates (2026-11-02)
    assert graphics.gap_text(DERINKUYU[2], DERINKUYU[3]) == "40 YEARS LATER"
    assert graphics.gap_text({"year": 1900}, {"year": 1901}) == ""


def test_spacing_follows_elapsed_time_and_keeps_the_minimum_step() -> None:
    ys = graphics.spaced_positions(DERINKUYU, 0.0, 1000.0, 150.0)
    steps = [b - a for a, b in pairwise(ys)]
    assert ys[0] == 0.0 and abs(ys[-1] - 1000.0) < 1e-6
    assert min(steps) >= 150.0 and steps[0] > steps[2]                 # 1,479 years read longer than 40
    even = graphics.spaced_positions(DERINKUYU, 0.0, 450.0, 150.0)       # no spare room: even steps
    assert [round(b - a) for a, b in pairwise(even)] == [150, 150, 150]


@needs_ffmpeg
def test_elapsed_time_is_drawn_at_contrast_inside_the_box(tmp_path: Path, monkeypatch) -> None:
    from PIL import Image
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 2.0)
    photo = tmp_path / "hero.jpg"
    Image.new("RGB", (1600, 1200), (200, 170, 110)).save(photo)
    out = graphics.render_timeline(DERINKUYU, tmp_path / "graphic_timeline.mp4", photo=photo)
    rec = json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))
    gaps = [e for e in rec["elements"] if e["name"].startswith("gap")]
    assert [e["name"] for e in gaps] == ["gap1", "gap2", "gap3"]
    assert all(e["drawn"] >= 4.5 for e in rec["elements"])
    _, _, x1, y1 = safe_zones.graphics_box()
    assert max(e["box"][3] for e in rec["elements"]) <= y1 and max(e["box"][2] for e in rec["elements"]) <= x1
    labels = [e for e in rec["elements"] if e["name"].startswith("label")]
    assert len(labels) == 5                                              # the labels themselves are unchanged
