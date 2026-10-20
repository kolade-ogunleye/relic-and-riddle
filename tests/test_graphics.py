import subprocess

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
    assert graphics.pick_reference(280)[0] == "Statue of Liberty (total)"
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


def test_text_and_shapes_really_fade():
    """Pillow ignores text alpha and RGBA-target alpha; graphics must blend for fades to exist."""
    from PIL import Image, ImageDraw
    from src.graphics import _text
    from src.typography import get_font
    font = get_font("Anton", 60)
    hidden = Image.new("RGB", (200, 100), (0, 0, 0))
    _text(ImageDraw.Draw(hidden, "RGBA"), (10, 10), "HI", font=font, fill=(255, 255, 255, 0))
    assert hidden.getextrema() == ((0, 0), (0, 0), (0, 0))
    half = Image.new("RGB", (200, 100), (0, 0, 0))
    _text(ImageDraw.Draw(half, "RGBA"), (10, 10), "HI", font=font, fill=(255, 255, 255, 128))
    assert 100 <= half.getextrema()[0][1] <= 140  # blended, not solid white


def test_then_now_first_frame_has_no_recent_year_badge(tmp_path):
    import subprocess
    from PIL import Image
    from src import then_now
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    Image.new("RGB", (1200, 2000), (40, 40, 40)).save(a)
    Image.new("RGB", (1200, 2000), (40, 40, 40)).save(b)
    clip = then_now.render(a, 1925, b, 2019, tmp_path / "graphic_thennow.mp4")
    f = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-frames:v", "1", str(f)], check=True)
    right = Image.open(f).convert("L").crop((700, 330, 1050, 430))  # where the 2019 badge ends up
    assert right.getextrema()[1] < 80  # nothing bright there yet


def test_planner_caps_graphics(tmp_path, monkeypatch):
    from PIL import Image
    from src import scene_planner
    from src.models import ScenePlan
    stills = []
    for k in range(2):
        p = tmp_path / f"s{k}.jpg"
        Image.new("RGB", (10, 10)).save(p)
        stills.append(p)
    gfx = []
    for name in ("map", "timeline", "numbers", "section", "route"):
        g = tmp_path / f"graphic_{name}.mp4"
        g.write_bytes(b"0")
        Image.new("RGB", (10, 10)).save(g.with_suffix(".jpg"))
        gfx.append(g)
    beats = [{"start": i * 3.0, "end": (i + 1) * 3.0, "text": f"b{i}"} for i in range(10)]
    monkeypatch.setattr(scene_planner, "claude_available", lambda: True)
    monkeypatch.setattr(scene_planner, "structured_call", lambda *a, **k: ScenePlan(images=[1, 3, 4, 5, 6, 7, 2, 1, 2, 1], notes="n"))
    chosen, durations, _ = scene_planner.plan_scenes(beats, stills + gfx)
    assert scene_planner._graphics_count(chosen) <= 3 and scene_planner._graphics_share(chosen, durations) <= 0.35


def test_cross_section_landscape_and_vertical(tmp_path):
    cs = {
        "subject": "Derinkuyu underground city",
        "total_depth_ft": 280.0,
        "approximate": True,
        "levels": [
            {"depth_ft": 15.0, "label": "Stables & wine presses"},
            {"depth_ft": 35.0, "label": "Living quarters & halls"},
            {"depth_ft": 140.0, "label": "Byzantine church"},
            {"depth_ft": 280.0, "label": "Deepest wells"},
        ],
    }
    # Landscape 1920x1080
    clip_ls = graphics.render_cross_section(cs, tmp_path / "section_ls.mp4", width=1920, height=1080)
    assert clip_ls.exists() and clip_ls.with_suffix(".jpg").exists()
    # Vertical 1080x1920
    clip_vert = graphics.render_cross_section(cs, tmp_path / "section_vert.mp4", width=1080, height=1920)
    assert clip_vert.exists() and clip_vert.with_suffix(".jpg").exists()


def test_stat_cards_and_scale_landscape(tmp_path):
    cards = [
        {"value": "280 FT", "label": "Depth into volcanic rock"},
        {"value": "20,000", "label": "Estimated sheltered population"},
    ]
    clip_cards = graphics.render_stat_cards(cards, tmp_path / "cards_ls.mp4", width=1920, height=1080)
    assert clip_cards.exists()

    scale_fact = {
        "value": 280.0,
        "unit": "ft",
        "dimension": "depth",
        "subject": "Derinkuyu depth",
        "approximate": True,
    }
    clip_scale = graphics.render_scale(scale_fact, tmp_path / "scale_ls.mp4", width=1920, height=1080)
    assert clip_scale.exists()


def test_ancient_engineering_graphics_landscape(tmp_path):
    # Rolling stone defense graphic
    defense_spec = {"title": "Rolling Stone Door Defense Mechanism"}
    clip_def = graphics.render_rolling_stone_defense(defense_spec, tmp_path / "defense_ls.mp4", width=1920, height=1080)
    assert clip_def.exists() and clip_def.with_suffix(".jpg").exists()

    # Ventilation airflow graphic
    vent_spec = {"title": "Subterranean Ventilation & Convection Dynamics"}
    clip_vent = graphics.render_ventilation_airflow(vent_spec, tmp_path / "vent_ls.mp4", width=1920, height=1080)
    assert clip_vent.exists() and clip_vent.with_suffix(".jpg").exists()


def test_cross_section_shows_a_level_at_the_full_depth(tmp_path):
    # A label faded in over the next 8% of digging after the shaft reached it, so a level at the
    # total depth (usually the deepest, headline feature) never became visible.
    import subprocess
    from PIL import Image, ImageChops
    base = {"subject": "Derinkuyu", "total_depth_ft": 280,
            "levels": [{"depth_ft": 20, "label": "Stables"}, {"depth_ft": 150, "label": "Chapel"}]}
    wells = {**base, "levels": base["levels"] + [{"depth_ft": 280, "label": "Wells"}]}
    cisterns = {**base, "levels": base["levels"] + [{"depth_ft": 280, "label": "Cisterns"}]}
    frames = []  # same layout, different label: identical frames mean the label is invisible
    for name, spec in (("wells", wells), ("cisterns", cisterns)):
        out = graphics.render_cross_section(spec, tmp_path / f"{name}.mp4", width=540, height=960)
        png = tmp_path / f"{name}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.5", "-i", str(out), "-frames:v", "1", str(png)], check=True)
        frames.append(Image.open(png).convert("L"))
    assert ImageChops.difference(*frames).getbbox() is not None  # the deepest label is drawn


@needs_geo
def test_map_names_no_indeterminate_area_and_keeps_names_off_reserved_space(monkeypatch):
    """Siachen Glacier (Natural Earth TYPE "Indeterminate") is not a country and is never named;
    a reserved box (the pin and place label) gets no neighbour name."""
    import math
    siachen = next(c for c in geo.countries() if "siachen glacier" in c["names"])
    assert siachen["type"] == "Indeterminate"
    named = []
    monkeypatch.setattr(graphics, "_text", lambda d, xy, text, **k: named.append((text, xy)))
    lat0, lon0, bw, bh = 20.02, 75.18, 1620, 2880
    kx, coslat = bw / 40.0, math.cos(math.radians(lat0))
    project = lambda lon, lat: (bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx)  # noqa: E731
    reserved = (0.0, bh * 0.3, float(bw), bh * 0.45)
    graphics.relief_base(lat0, lon0, bw, bh, kx, coslat, geo.find_country("India"), project, (0, 0, bw, bh),
                         reserved=(reserved,))
    texts = [t.replace(" ", "") for t, _ in named]
    assert texts  # neighbours are still named
    assert "SIACHENGLACIER" not in texts
    assert not any(reserved[1] <= y <= reserved[3] for _, (_, y) in named)
