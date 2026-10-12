import subprocess
from pathlib import Path

import pytest

from src import geo, graphics
from src.scene_planner import plan_scenes

needs_geo = pytest.mark.skipif(not geo.available(), reason="Natural Earth data not downloaded")


@needs_geo
def test_country_match_prefers_the_country_over_territories():
    uk, usa = geo.find_country("UK"), geo.find_country("USA")
    assert geo.in_country(-0.127, 51.519, uk) and geo.in_country(-72.93, 41.31, usa)
    assert not geo.in_country(-0.127, 51.519, geo.find_country("France"))
    assert geo.find_country("Scotland") is uk


@needs_geo
def test_stored_coordinates_used_only_when_inside_the_country():
    topic = {"location": {"name": "Derinkuyu", "country": "Turkey", "latitude": 38.37, "longitude": 34.73}}
    assert geo.locate(topic)["lat"] == 38.37
    wrong = {"location": {"name": "Nowhere", "country": "Turkey", "latitude": 48.85, "longitude": 2.35}}  # Paris
    import requests
    original = geo._get
    geo._get = lambda params: (_ for _ in ()).throw(requests.ConnectionError("offline"))
    try:
        assert geo.locate(wrong) is None  # Paris coordinates are not accepted for Turkey
    finally:
        geo._get = original


def test_year_text_and_reference_choice():
    assert graphics.year_text(-800, True) == "c. 800 BC"
    assert graphics.year_text(780, True) == "c. AD 780"
    assert graphics.year_text(1963, False) == "1963"
    assert graphics.pick_reference(280)[0] == "Statue of Liberty"
    assert graphics.pick_reference(20) is None  # no honest comparison at 1/15 scale


def test_timeline_clip_has_poster_and_full_length(tmp_path):
    clip = graphics.render_timeline([{"year": -800, "approximate": True, "label": "First chambers"},
                                     {"year": 1963, "approximate": False, "label": "Rediscovered"}], tmp_path / "graphic_timeline.mp4")
    assert clip.with_suffix(".jpg").exists()
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
                         capture_output=True, text=True).stdout
    assert abs(float(dur) - graphics.CLIP_SECONDS) < 0.1


def test_make_all_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(graphics, "render_timeline", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert graphics.make_all({"timeline": [{"year": 1, "label": "x"}]}, {"location": {}}, tmp_path) == []


def test_planner_uses_each_graphic_once(tmp_path, monkeypatch):
    from PIL import Image
    from src import scene_planner
    from src.models import ScenePlan
    stills = []
    for k in range(3):
        p = tmp_path / f"s{k}.jpg"
        Image.new("RGB", (10, 10)).save(p)
        stills.append(p)
    gfx = tmp_path / "graphic_map.mp4"
    gfx.write_bytes(b"0")
    Image.new("RGB", (10, 10)).save(gfx.with_suffix(".jpg"))
    beats = [{"start": i * 3.0, "end": (i + 1) * 3.0, "text": f"b{i}"} for i in range(4)]
    monkeypatch.setattr(scene_planner, "claude_available", lambda: True)
    monkeypatch.setattr(scene_planner, "structured_call", lambda *a, **k: ScenePlan(images=[1, 4, 4, 2], notes="n"))
    chosen, _, _ = plan_scenes(beats, stills + [gfx])
    assert chosen[1] == gfx and chosen.count(gfx) == 1  # the repeat became a photo
    # Without a plan, graphics are not used at all.
    monkeypatch.setattr(scene_planner, "claude_available", lambda: False)
    assert gfx not in plan_scenes(beats, stills + [gfx])[0]
