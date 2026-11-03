"""Round-3 panel changes to the explanatory graphics (2026-11-02): number cards count in the figure's own precision
with a closed-dash range and one figure size; the timeline encodes certainty on its nodes, steps earlier events back
to stone twins, keeps every hedge word, sets 9:16 spans on their own segment and 16:9 years on one baseline per
side; the scale says which is taller and by how much, splits the metric figure out, and a depth carries a ruler and a
bracket; the cross-section registers each level on its ruler, keeps its first label clear of the ground and tags the
drawing ILLUSTRATIVE; the locator and route maps (on a synthetic Natural Earth stand-in) ease in log space, draw a
scale bar in both orientations, mark a region fallback as an area and measure every stop label."""
import math
import re
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, geo, graphics
from src.typography import fit_text

DERINKUYU = [{"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
             {"year": 780, "approximate": True, "label": "Expanded against raiders"},
             {"year": 1923, "label": "Abandoned after the population exchange"},
             {"year": 1963, "label": "Rediscovered behind a wall"}]
SECTION = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
           "levels": [{"depth_ft": 15.0, "label": "Stables and storage"}, {"depth_ft": 35.0, "label": "Living quarters"},
                      {"depth_ft": 140.0, "label": "Cruciform church"}, {"depth_ft": 280.0, "label": "Deepest wells"}]}
DEPTH = {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu depth", "approximate": True}
HEIGHT = {"value": 107.0, "unit": "ft", "dimension": "height", "subject": "Kailasa Temple", "approximate": True}
FORMATS = [(1080, 1920), (1920, 1080)]


def _render(monkeypatch: pytest.MonkeyPatch, job, graded: bool = False) -> dict[str, Any]:
    """One render at 4 fps: its frames and the plates it records (the graded measurement skipped unless asked)."""
    state: dict[str, Any] = {}
    real_record = graphics.record_contrast
    monkeypatch.setattr(graphics, "VIDEO_FPS", 4)
    if not graded:
        monkeypatch.setattr(graphics, "graded_frame", lambda frame, work, vignette=False: None)

    def fake_encode(frames, path, n, width=1080, height=1920):
        state["frames"] = [f.convert("RGB") for f in frames]
        return path

    def spy_record(path, frame, plates):
        state["plates"] = [(name, p) for name, p in plates if p is not None and not p.empty]
        state["record"] = real_record(path, frame, plates)
        return state["record"]
    monkeypatch.setattr(graphics, "_encode", fake_encode)
    monkeypatch.setattr(graphics, "record_contrast", spy_record)
    assert job() is not None
    return state


def _photo(path: Path, rgb=(196, 150, 64)) -> Path:
    rng = np.random.default_rng(4)
    arr = np.clip(np.array(rgb, np.float32) + rng.normal(0, 24, (1200, 1600, 3)), 0, 255)
    Image.fromarray(arr.astype(np.uint8)).save(path)
    return path


def _no_collisions(plates) -> None:
    hits = [(a, b) for (a, pa), (b, pb) in combinations(plates, 2)
            if graphics.LayoutConstraintSolver.boxes_intersect(pa.box, pb.box)]
    assert not hits, hits


def _near(arr: np.ndarray, colour, tol: int = 40) -> np.ndarray:
    return np.abs(arr.astype(np.int16) - np.array(colour, np.int16)).max(axis=-1) <= tol


# ============================================================================ number cards
def test_count_step_is_the_written_figures_precision() -> None:
    assert graphics.count_step("1,200,000") == 100_000
    assert graphics.count_step("280") == 10
    assert graphics.count_step("20,000") == 1_000
    assert graphics.count_step("85") == 1
    assert graphics.count_step("100") == 10                       # ten steps at least, never finer than units
    assert graphics.count_step("0") == 1


def test_a_count_up_never_shows_a_figure_more_precise_than_its_written_form() -> None:
    for i in range(101):
        shown = graphics._shown_value("1,200,000 TONS", i / 100)
        num = int(shown.split()[0].replace(",", ""))
        assert num % 100_000 == 0, shown                          # no 597,340 on the way to 1,200,000
        shown = graphics._shown_value("about 280 ft", i / 100)
        assert int(re.findall(r"\d+", shown)[0]) % 10 == 0, shown
    assert graphics._shown_value("1,200,000 TONS", 1.0) == "1,200,000 TONS"


def test_a_two_unit_count_keeps_both_figures_on_one_depth() -> None:
    for i in range(101):
        ft, m = (int(x) for x in re.findall(r"\d+", graphics._shown_value("280 FT (85 M)", i / 100)))
        assert abs(ft * 0.3048 - m) <= 1.0, (i, ft, m)
    assert graphics._shown_value("1404-1438", 0.3) == "1404-1438"   # years stay whole


def test_ranges_take_a_closed_en_dash() -> None:
    assert graphics.figure_text("94-104M YEARS AGO") == "94–104 MILLION YEARS AGO"
    assert graphics.figure_text("94 – 104") == "94–104"
    assert graphics.figure_parts("94–104 MILLION YEARS AGO")["num"] == "94–104"


@pytest.mark.parametrize("size", FORMATS)
def test_no_displayed_figure_has_a_spaced_or_hyphen_range(tmp_path, monkeypatch, size) -> None:
    cards = [{"value": "94-104M years ago", "label": "Carbonatite rock cooling age"},
             {"value": "1404 - 1438", "label": "Written"}]
    st = _render(monkeypatch, lambda: graphics.render_stat_cards(cards, tmp_path / "n.mp4", _photo(tmp_path / "r.jpg"),
                                                                 width=size[0], height=size[1]))
    texts = [p.text for _, p in st["plates"]]
    assert "94–104" in texts
    assert not [t for t in texts if re.search(r"\d\s+[–-]\s*\d|\d\s*[–-]\s+\d|\d-\d", t)], texts


def test_two_vertical_cards_share_one_figure_size(tmp_path, monkeypatch) -> None:
    cards = [{"value": "about 1,200,000 tons", "label": "Rock removed by hand"},
             {"value": "94-104M years ago", "label": "Carbonatite rock cooling age"}]
    seen: list[int] = []
    real = graphics._stat_clip

    def spy(cards_, plans, *a, **k):
        seen.extend(p["figure_px"] for p in plans)
        return real(cards_, plans, *a, **k)
    monkeypatch.setattr(graphics, "_stat_clip", spy)
    _render(monkeypatch, lambda: graphics.render_stat_cards(cards, tmp_path / "n.mp4", _photo(tmp_path / "r.jpg")))
    assert len(seen) == 2 and seen[0] == seen[1]


def test_the_scrim_deepens_behind_a_bright_warm_block() -> None:
    warm = Image.new("RGB", (400, 400), (205, 160, 80))
    dark = Image.new("RGB", (400, 400), (40, 44, 52))
    assert graphics.stat_scrim_target(warm, (0, 0, 400, 400)) < 0.2
    assert graphics.stat_scrim_target(dark, (0, 0, 400, 400)) == pytest.approx(0.24)


# ============================================================================ timeline
@pytest.mark.parametrize("size", FORMATS)
@pytest.mark.parametrize("events", [DERINKUYU, DERINKUYU[:2], DERINKUYU[1:]])
def test_fitted_labels_keep_every_hedge_word(tmp_path, monkeypatch, size, events) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", width=size[0], height=size[1]))
    by_k: dict[int, list[str]] = {}
    for name, p in st["plates"]:
        m = re.fullmatch(r"label(\d)", name)
        if m:
            by_k.setdefault(int(m.group(1)), []).append(p.text)
    for k, e in enumerate(sorted(events, key=lambda e: e["year"])):
        assert " ".join(by_k[k]).split() == e["label"].upper().split(), (k, by_k[k])


def test_focus_steps_an_event_back_and_restores_it_and_pulses_once() -> None:
    sch = graphics.reveal_schedule(4, 6.0)
    t = sch.at[2] + 0.6
    assert sch.focus(0, t) == pytest.approx(1.0, abs=0.01) and sch.focus(2, t) == 0.0
    assert sch.focus(3, 5.0) == 0.0                                 # the last is never stepped back
    assert all(sch.focus(k, 6.0) == pytest.approx(0.0, abs=0.01) for k in range(4))
    sizes = [sch.pulse(1, sch.at[1] + i / 100) for i in range(0, 200)]
    assert max(sizes) == pytest.approx(1.3, abs=0.01) and sizes[0] == 1.0 and sizes[-1] == 1.0


@pytest.mark.parametrize("size", FORMATS)
def test_earlier_events_step_back_to_stone_twins_at_full_contrast(tmp_path, monkeypatch, size) -> None:
    twins = []
    real = graphics._twin

    def spy(ground, plate, fill):
        tw = real(ground, plate, fill)
        twins.append(tw)
        return tw
    monkeypatch.setattr(graphics, "_twin", spy)
    _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", width=size[0], height=size[1]))
    assert twins and {tuple(t.fill) for t in twins} <= {brand.STONE, brand.STONE_LT}
    assert all(t.contrast >= brand.TEXT_CONTRAST for t in twins), [(t.text, t.contrast) for t in twins]


@pytest.mark.parametrize("size", FORMATS)
def test_nodes_encode_certainty_hollow_ring_for_circa_solid_dot_for_exact(tmp_path, monkeypatch, size) -> None:
    calls = []
    real = graphics._node
    monkeypatch.setattr(graphics, "_node", lambda d, x, y, r, approx, *a: calls.append((round(x), round(y), r, approx))
                        or real(d, x, y, r, approx, *a))
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", width=size[0], height=size[1]))
    last = {(x, y): (r, a) for x, y, r, a in calls[-4:]}
    arr = np.asarray(st["frames"][-1])
    for (x, y), (r, approx) in last.items():
        centre_gold = bool(_near(arr[y - 2:y + 3, x - 2:x + 3], brand.GOLD, 50).mean() > 0.5)
        assert centre_gold != approx, (x, y, approx)              # circa: hollow; exact: filled


def test_tall_spans_sit_on_their_own_segment_when_the_column_has_room(tmp_path, monkeypatch) -> None:
    # two entries (review fix, 2026-11-02: a span's band now holds a label line of air on each side, which three
    # Derinkuyu entries cannot afford without smaller type; they keep the year baseline)
    events = DERINKUYU[2:]
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4"))
    plates = dict(st["plates"])
    gaps = {int(n[3:]): p for n, p in st["plates"] if n.startswith("gap")}
    assert gaps
    for k, p in gaps.items():
        above = max(q.box[3] for n, q in st["plates"] if re.fullmatch(rf"(year|label){k - 1}", n))
        below = plates[f"year{k}"].box[1]
        assert above < p.box[1] and p.box[3] < below, (k, p.text)   # between the two entries, not on a baseline
        labels = [q.font.size for n, q in st["plates"] if re.fullmatch(r"label\d", n)]
        assert p.font.size < min(labels) and p.fill == brand.STONE_LT   # secondary to the events (review fix)


def test_wide_years_share_one_baseline_per_side(tmp_path, monkeypatch) -> None:
    events = [dict(DERINKUYU[0]), dict(DERINKUYU[1]),
              {"year": 1923, "label": "Abandoned after the population exchange between Greece and Turkey"},
              dict(DERINKUYU[3])]
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", width=1920, height=1080))
    years = {int(n[4:]): p for n, p in st["plates"] if re.fullmatch(r"year\d", n)}
    assert abs(years[0].box[3] - years[2].box[3]) <= 2               # above the spine
    assert abs(years[1].box[3] - years[3].box[3]) <= 2               # below it
    _no_collisions(st["plates"])


# ============================================================================ scale
def test_comparison_ratio_is_never_more_confident_than_the_figures() -> None:
    assert graphics.comparison_ratio(150, 100) == "1.5"
    assert graphics.comparison_ratio(250, 100) == "3"                # half up, not banker's 2
    assert graphics.comparison_ratio(305, 107) == "3"
    assert graphics.comparison_ratio(116, 100) == "1.2"


def test_height_meaning_says_which_is_taller() -> None:
    fact = dict(HEIGHT)
    # round 4 (2026-11-02): the subject first (scale_meaning_lines, in capitals here)
    assert graphics.scale_meaning(fact, 107.0, "Statue of Liberty (total)", 305.0, False) == [
        "KAILASA TEMPLE: ABOUT 107 FT.", "THE STATUE OF LIBERTY, 305 FT, IS ABOUT 3 TIMES AS TALL."]
    taller = dict(HEIGHT, subject="Kailasa Temple monolith height")
    assert graphics.scale_meaning(taller, 457.5, "Statue of Liberty (total)", 305.0, False) == [
        "KAILASA TEMPLE MONOLITH: ABOUT 458 FT,", "ABOUT 1.5 TIMES AS TALL AS THE STATUE OF LIBERTY, 305 FT."]
    assert graphics.scale_meaning(fact, 300.0, "Statue of Liberty (total)", 305.0, False)[1].endswith(
        "IS NEARLY THE SAME HEIGHT.")
    assert not any("TALLER" in t for t in graphics.scale_meaning(fact, 107.0, "Statue of Liberty (total)", 305.0, False))


def test_scale_figure_splits_the_metric_figure_out() -> None:
    assert graphics.scale_figure(DEPTH, 280.0, True) == ("ABOUT 280 FT DEEP", "85 M")
    assert graphics.scale_figure(dict(HEIGHT, approximate=False), 107.0, False) == ("107 FT TALL", "33 M")


def test_depth_ruler_marks() -> None:
    assert graphics._depth_ruler(280.0, "ABOUT ") == [(0.0, "0 FT", False), (100.0, "100", False),
                                                      (200.0, "200", False), (280.0, "ABOUT 280 FT", True)]
    assert [t for _, t, _ in graphics._depth_ruler(120.0, "")] == ["0 FT", "50", "100", "120 FT"]
    assert [t for _, t, _ in graphics._depth_ruler(105.0, "")] == ["0 FT", "50", "105 FT"]   # none just above


@pytest.mark.parametrize("size", FORMATS)
def test_depth_carries_a_bracket_with_the_gap_and_a_data_face_ruler(tmp_path, monkeypatch, size) -> None:
    st = _render(monkeypatch, lambda: graphics.render_scale(DEPTH, tmp_path / "s.mp4", width=size[0], height=size[1]))
    rows = dict(st["plates"])
    assert rows["bracket"].text == "ABOUT 25 FT" and rows["bracket"].fill == brand.GOLD
    assert "ABOUT 25 FT" in " ".join(graphics.scale_meaning(DEPTH, 280.0, "Statue of Liberty (total)", 305.0, True))
    rulers = [p for n, p in st["plates"] if n.startswith("ruler")]
    assert [p.text for p in rulers][0] == "0 FT" and rulers[-1].text == "ABOUT 280 FT"
    assert all(brand.DATA_FONT.replace(" ", "") in Path(p.font.path).stem for p in rulers)
    figure = " ".join(p.text for n, p in st["plates"] if re.fullmatch(r"figure\d*", n))
    assert figure == "ABOUT 280 FT DEEP" and rows["figure_metric"].text == "85 M"
    assert all(e["drawn"] >= brand.TEXT_CONTRAST for e in st["record"]["elements"]), st["record"]["elements"]
    _no_collisions(st["plates"])
    x0, y0, x1, y1 = graphics.get_text_box(*size)
    assert all(x0 <= p.box[0] and p.box[2] <= x1 and y0 <= p.box[1] and p.box[3] <= y1 for _, p in st["plates"])


@pytest.mark.parametrize("size", FORMATS)
def test_height_labels_the_landmarks_figure_at_its_top(tmp_path, monkeypatch, size) -> None:
    st = _render(monkeypatch, lambda: graphics.render_scale(HEIGHT, tmp_path / "s.mp4", width=size[0], height=size[1]))
    rows = dict(st["plates"])
    assert rows["ref_label"].text == "305 FT" and rows["block_label"].text == "ABOUT 107 FT"
    meaning = [p for n, p in st["plates"] if n.startswith("meaning")]
    assert " ".join(p.text for p in meaning).upper().endswith("IS ABOUT 3 TIMES AS TALL.")   # round 4: subject first
    if size[0] < size[1]:
        assert all(p.font.size >= graphics.SCALE_MEANING_PX[1] for p in meaning)
    _no_collisions(st["plates"])


def test_wide_ground_plane_ends_inside_title_safe(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_scale(HEIGHT, tmp_path / "s.mp4", width=1920, height=1080))
    arr = np.asarray(st["frames"][-1])
    bone = _near(arr, brand.BONE, 12)
    ground_rows = [y for y in range(arr.shape[0]) if bone[y, 300:1000].sum() > 400]
    assert ground_rows
    y = ground_rows[-1]
    assert not bone[y, :graphics.WIDE_TITLE_SAFE[0] - 2].any()        # no longer runs off x 0
    assert bone[y, graphics.WIDE_TITLE_SAFE[0] + 4:1000].mean() > 0.95


# ============================================================================ cross-section
def test_label_stack_holds_heavy_blocks_and_fans_the_crowded_pair() -> None:
    tops = graphics.label_stack([10.0, 20.0, 300.0, 500.0], [60.0] * 4, 30.0, 700.0, 10.0,
                                weights=[1.0, 1.0, 1000.0, 1000.0])
    assert tops[0] >= 30.0                                          # a hard floor under the ground
    assert tops[2] == pytest.approx(300.0, abs=0.5) and tops[3] == pytest.approx(500.0, abs=0.5)
    assert tops[1] - tops[0] >= 70.0 - 1e-6
    plain = graphics.label_stack([0.0, 0.0], [50.0, 50.0], 0.0, 1000.0, 10.0)
    assert plain[1] - plain[0] == pytest.approx(60.0)
    tight = graphics.label_stack([0.0, 0.0, 0.0], [50.0] * 3, 0.0, 160.0, 20.0)
    assert tight[0] >= 0.0 and tight[-1] + 50.0 <= 160.0 + 1e-6      # gaps shrink before the floor moves


def test_section_bands_and_profiles_differ_by_depth() -> None:
    assert graphics.section_band(15, 280, False) == "shallow"
    assert graphics.section_band(140, 280, False) == "mid"
    assert graphics.section_band(280, 280, True) == "deep"
    prof = graphics.SECTION_PROFILES
    assert prof["shallow"]["h"] < prof["mid"]["h"] < prof["deep"]["h"]
    assert prof["shallow"]["flat"] > prof["mid"]["flat"] and prof["deep"]["w"] > prof["mid"]["w"]


@pytest.mark.parametrize("size", FORMATS)
def test_section_registers_levels_and_tags_the_drawing(tmp_path, monkeypatch, size) -> None:
    st = _render(monkeypatch, lambda: graphics.render_cross_section(SECTION, tmp_path / "x.mp4", width=size[0],
                                                                    height=size[1]), graded=size[0] > size[1])
    rows = dict(st["plates"])
    assert rows["approx"].text == graphics.SECTION_CAPTION == "ILLUSTRATIVE · APPROX. DEPTHS"
    if size[0] > size[1]:
        e = next(e for e in st["record"]["elements"] if e["name"] == "approx")
        assert e["graded"] is None or e["graded"] >= 5.5, e
    rulers = {p.text: (p.box[1] + p.box[3]) / 2 for n, p in st["plates"] if n.startswith("ruler")}
    assert all(brand.DATA_FONT.replace(" ", "") in Path(p.font.path).stem for n, p in st["plates"]
               if n.startswith("ruler"))
    y0, y300 = rulers["0 FT"], rulers["300"]

    def floor(ft: float) -> float:
        return y0 + (y300 - y0) * ft / 300.0
    k = min(size[0] / 1920, size[1] / 1080) if size[0] > size[1] else graphics._Grid(*size).k
    level0 = [p for n, p in st["plates"] if n.startswith("level0_")]
    assert min(p.box[1] for p in level0) >= y0 + 24 * k - 2         # 15 FT clear of the surface line
    if size[0] < size[1]:            # room above them: on their floors (16:9 runs out of title-safe below 280)
        for name, ft in (("level2_depth", 140), ("level3_depth", 280)):
            p = rows[name]
            assert abs((p.box[1] + p.box[3]) / 2 - floor(ft)) <= 8, name
    arr = np.asarray(st["frames"][-1])
    g = graphics._Grid(*size) if size[0] < size[1] else graphics._WideGrid(*size)
    L = graphics._SECTION_TALL if size[0] < size[1] else graphics._SECTION_WIDE
    x = round(g.x(L.ruler_x + 4))
    yy = round(floor(280))
    assert _near(arr[yy - 3:yy + 4, x - 3:x + 4], brand.GOLD, 60).any()   # the deepest level's gold tick
    _no_collisions(st["plates"])


# ============================================================================ maps
@pytest.fixture
def fake_geo(monkeypatch):
    def blob(cx, cy, rx, ry):
        return [(cx + rx * math.cos(2 * math.pi * i / 48), cy + ry * math.sin(2 * math.pi * i / 48)) for i in range(48)]

    def country(name, cx, cy, rx, ry):
        ring = blob(cx, cy, rx, ry)
        return {"names": {name.lower()}, "sovereign": name.lower(), "polygons": [[ring]], "size": len(ring),
                "display": name, "label_at": (cx, cy), "label_rank": 2, "type": "Sovereign country",
                "extent": (cx - rx, cy - ry, cx + rx, cy + ry)}
    world = [country("Turkey", 35.0, 39.0, 9.0, 3.2), country("Ukraine", 31.5, 49.0, 7.0, 2.8),
             country("Egypt", 30.0, 26.5, 5.0, 4.5), country("Iran", 54.0, 32.5, 7.5, 5.0)]
    points = {"istanbul": (41.0082, 28.9784), "ankara": (39.9334, 32.8597), "derinkuyu": (38.3735, 34.7347)}
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "countries", lambda: world)
    monkeypatch.setattr(geo, "find_country", lambda name: next((c for c in world if name.lower() in c["names"]), None))
    monkeypatch.setattr(geo, "locate", lambda topic: dict(zip(("lat", "lon"), points[topic["location"]["name"].lower()]),
                                                          label=topic["location"]["name"], approximate=False,
                                                          matched="name", country=topic["location"]["country"]))
    monkeypatch.setattr(graphics, "_relief", lambda: None)
    return world


def test_camera_spans_ease_in_log_space_and_hold() -> None:
    spans = graphics.camera_spans(180, 40.0, 18.0, 2.2, 30)
    assert spans[0] == pytest.approx(40.0) and spans[-1] == pytest.approx(18.0)
    assert all(b <= a + 1e-9 for a, b in zip(spans, spans[1:]))
    mid = spans[33]                                                  # half the travel
    assert mid == pytest.approx(math.sqrt(40.0 * 18.0), rel=0.02)    # the geometric, not arithmetic, midpoint
    assert spans[int(2.3 * 30):] == [pytest.approx(18.0)] * len(spans[int(2.3 * 30):])


def test_great_circle_bows_poleward_and_keeps_its_ends() -> None:
    arc = graphics.great_circle((-74.0, 40.7), (-0.1, 51.5))
    assert arc[0] == pytest.approx((-74.0, 40.7)) and arc[-1] == pytest.approx((-0.1, 51.5))
    assert max(lat for _, lat in arc) > 51.5                          # a chord would never pass north of London
    assert len(arc) == 33


def test_region_fallback_is_recognised() -> None:
    assert geo.is_region_fallback({"matched": "region"})
    assert not geo.is_region_fallback({"matched": "city", "approximate": True})
    assert geo.is_region_fallback({"approximate": True, "label": "Cappadocia", "region": "Cappadocia"})
    assert not geo.is_region_fallback({"approximate": False, "label": "Derinkuyu", "region": "Cappadocia"})


def test_locate_records_which_name_it_matched(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "_load_cache", lambda: {})
    monkeypatch.setattr(geo, "find_country", lambda name: {"names": {name.lower()}})
    monkeypatch.setattr(geo, "in_country", lambda lon, lat, c, tolerance_deg=1.0: True)
    monkeypatch.setattr(geo, "CACHE_FILE", tmp_path / "cache.json")
    stored = geo.locate({"location": {"name": "Derinkuyu", "country": "Turkey", "latitude": 38.37, "longitude": 34.73}})
    assert stored["matched"] == "name"
    answers = iter([None, {"lat": 38.6, "lon": 34.8, "qid": "Q1", "country": "Turkey"}])
    monkeypatch.setattr(geo, "_wikidata_point", lambda q, c, n: next(answers))
    got = geo.locate({"location": {"name": "X site", "country": "Turkey", "region": "Cappadocia"}})
    assert got["matched"] == "region" and got["label"] == "Cappadocia" and got["approximate"]


@pytest.mark.parametrize("size", FORMATS)
def test_locator_draws_its_scale_bar_in_both_orientations(tmp_path, monkeypatch, fake_geo, size) -> None:
    place = {"lat": 38.3735, "lon": 34.7347, "label": "Derinkuyu", "country": "Turkey"}
    st = _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=size[0], height=size[1]))
    rows = dict(st["plates"])
    assert "scale" in rows
    x0, y0, x1, y1 = graphics.get_text_box(*size)
    outside = [(n, p.box) for n, p in st["plates"] if not (x0 <= p.box[0] and p.box[2] <= x1 and y0 <= p.box[1]
                                                          and p.box[3] <= y1)]
    assert not outside, outside
    assert not graphics.LayoutConstraintSolver.boxes_intersect(rows["scale"].box, (1620, 950, 1920, 1080))
    arr = np.asarray(st["frames"][-1])
    cx = size[0] // 2
    top, pin = rows["country"].box[3] + 20, size[1] // 2 - 40
    if pin - top > 30:                                               # the leader from the label to the pin
        assert _near(arr[top:pin, cx - 1:cx + 2], brand.BONE, 30).any(axis=1).mean() > 0.8


def test_a_region_fallback_is_drawn_as_an_area_and_says_so(tmp_path, monkeypatch, fake_geo) -> None:
    place = {"lat": 38.6, "lon": 34.8, "label": "Cappadocia", "country": "Turkey", "approximate": True,
             "matched": "region"}
    st = _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4"))
    rows = dict(st["plates"])
    assert rows["approx"].text == "APPROX. LOCATION"
    arr = np.asarray(st["frames"][-1])
    cy, cx = 960, 540
    assert not _near(arr[cy - 4:cy + 5, cx - 4:cx + 5], brand.INK, 12).any()    # no pin's ink centre


def test_map_names_and_borders_use_brand_stone(fake_geo, monkeypatch) -> None:
    assert graphics.MAP_NAME == brand.STONE and graphics.MAP_BORDER == brand.STONE
    fills = []
    monkeypatch.setattr(graphics, "_text", lambda d, xy, text, **k: fills.append(k["fill"]))
    lat0, lon0, bw, bh = 39.0, 35.0, 1620, 2880
    kx, coslat = bw / 40.0, math.cos(math.radians(lat0))
    project = lambda lon, lat: (bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx)  # noqa: E731
    graphics.relief_base(lat0, lon0, bw, bh, kx, coslat, fake_geo[0], project, (0, 0, bw, bh))
    assert fills and set(fills) == {brand.STONE}


@pytest.mark.parametrize("size", FORMATS)
def test_route_labels_are_measured_inside_the_box_and_clear_of_each_other(tmp_path, monkeypatch, fake_geo, size) -> None:
    stops = [{"place": "Istanbul", "country": "Turkey", "label": "Constantinople"},
             {"place": "Ankara", "country": "Turkey", "label": "Ancyra"},
             {"place": "Derinkuyu", "country": "Turkey", "label": "Derinkuyu underground city"}]
    st = _render(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0], height=size[1]))
    names = [n for n, _ in st["plates"]]
    assert {"stop0", "stop1", "stop2", "scale"} <= set(names)
    words = " ".join(p.text for n, p in st["plates"] if n.startswith("stop")).split()
    assert words == "CONSTANTINOPLE ANCYRA DERINKUYU UNDERGROUND CITY".split()
    assert all(e["drawn"] >= brand.TEXT_CONTRAST for e in st["record"]["elements"]), st["record"]["elements"]
    x0, y0, x1, y1 = graphics.get_text_box(*size)
    assert all(x0 <= p.box[0] and p.box[2] <= x1 and y0 <= p.box[1] and p.box[3] <= y1 for _, p in st["plates"])
    _no_collisions(st["plates"])


# ============================================================================ round-3 review fixes (2026-11-02)
def _span_rows(st: dict[str, Any]) -> tuple[dict[int, Any], dict[str, Any]]:
    return {int(n[3:]): p for n, p in st["plates"] if n.startswith("gap")}, dict(st["plates"])


@pytest.mark.parametrize("events", [DERINKUYU[2:], DERINKUYU[1:], DERINKUYU])
def test_a_tall_span_on_its_segment_has_a_label_line_of_air_and_sits_under_the_labels(tmp_path, monkeypatch,
                                                                                     events) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4"))
    gaps, rows = _span_rows(st)
    labels = [p for n, p in st["plates"] if re.fullmatch(r"label\d", n)]
    lab_px = min(p.font.size for p in labels)
    for k, p in gaps.items():
        if abs(p.box[3] - rows[f"year{k}"].box[3]) <= 4:
            continue                                                # on the later year's baseline
        above = max(q.box[3] for n, q in st["plates"] if re.fullmatch(rf"(year|label){k - 1}", n))
        below = rows[f"year{k}"].box[1]
        air = round(lab_px * graphics.SPAN_AIR)                    # round 4: 0.6 of a label line, not 1.3
        assert p.box[1] - above >= air - 6 and below - p.box[3] >= air - 6, (k, p.text, p.box, above, below)
        assert p.font.size < lab_px                                 # secondary: smaller than every event label
    if len(events) == 2:
        assert gaps and all(abs(p.box[3] - rows[f"year{k}"].box[3]) > 4 for k, p in gaps.items())


def test_spans_never_cost_the_event_labels_their_size(tmp_path, monkeypatch) -> None:
    with_spans = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "a.mp4"))
    monkeypatch.setattr(graphics, "gap_text", lambda a, b: "")
    without = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "b.mp4"))
    # round 4: the spans are paid for by the Anton years first, never by the labels
    sizes = lambda st: sorted((n, p.font.size) for n, p in st["plates"] if re.fullmatch(r"label\d", n))
    assert sizes(with_spans) == sizes(without)


def test_the_four_entry_derinkuyu_spans_sit_above_the_later_year_clear_of_every_plate(tmp_path, monkeypatch) -> None:
    # Round 4 (2026-11-02) replaced the baseline fallback this test encoded: the spans now always sit on their
    # segments, above the later year (tests/test_r4_graphics_2026_11_02.py).
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    gaps, rows = _span_rows(st)
    assert set(gaps) == {1, 2, 3}
    for k, p in gaps.items():
        assert p.box[3] < rows[f"year{k}"].box[1], (k, p.text)
    _no_collisions(st["plates"])


def test_span_helpers() -> None:
    f = graphics.get_font("Montserrat", graphics.span_font_px(38), weight=600)
    assert graphics.span_font_px(38) < 38 and graphics.span_font_px(20) == graphics.SPAN_MIN_PX   # round 4: 24 -> 30
    band, air = graphics.span_band(38, f)
    assert air == round(38 * graphics.SPAN_AIR) and band == graphics.cap_height(f) + 2 * air     # round 4: 1.3 -> 0.6


def test_tall_depth_sets_the_metres_under_the_figure_and_keeps_air_over_the_bracket(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_scale(DEPTH, tmp_path / "s.mp4"))
    rows = dict(st["plates"])
    fig, met, note = rows["figure"], rows["figure_metric"], rows["bracket"]
    assert met.box[1] > fig.box[3]                                  # its own line, under the feet
    assert abs(met.box[0] - fig.box[0]) <= 8                         # on the same left edge
    # the bracket's note clear of the heading lines over its columns (round 5: of the feet figure; the short
    # metres line is a gap away sideways, and the note sits under its row)
    assert note.box[1] - fig.box[3] >= graphics.SCALE_TEXT_GAP and note.box[1] > met.box[3]
    assert note.box[0] - met.box[2] >= graphics.SCALE_TEXT_GAP
    assert graphics.SCALE_TEXT_GAP == 44


def test_tall_height_sets_the_metres_under_the_figure(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_scale(HEIGHT, tmp_path / "s.mp4"))
    rows = dict(st["plates"])
    assert rows["figure_metric"].box[1] > rows["figure"].box[3]
    assert rows["block_label"].text == "ABOUT 107 FT"
    assert rows["block_label"].box[1] - rows["figure_metric"].box[3] >= graphics.SCALE_TEXT_GAP
    _no_collisions(st["plates"])


def test_point_in_polygons_works_in_any_plane() -> None:
    square = [[[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)], [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]]]
    assert geo.point_in_polygons(2.0, 2.0, square)
    assert not geo.point_in_polygons(5.0, 5.0, square)              # in the hole
    assert not geo.point_in_polygons(12.0, 2.0, square)


def _drawn_names(monkeypatch) -> list[tuple[str, tuple[float, float]]]:
    names: list[tuple[str, tuple[float, float]]] = []
    real = graphics._text

    def spy(d, xy, text, font, fill, anchor="la"):
        if fill == graphics.MAP_NAME:
            names.append((text, xy, font))
        return real(d, xy, text, font, fill, anchor)
    monkeypatch.setattr(graphics, "_text", spy)
    return names


def test_a_name_on_a_reserved_label_point_moves_onto_its_own_land(fake_geo, monkeypatch) -> None:
    names = _drawn_names(monkeypatch)
    lat0, lon0, bw, bh = 39.0, 35.0, 1620, 2880
    kx, coslat = bw / 20.0, math.cos(math.radians(lat0))
    project = lambda lon, lat: (bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx)
    px, py = project(35.0, 39.0)                                    # Turkey's label point, taken (by a leg)
    reserved = ((px - 20, py - 20, px + 20, py + 20),)
    graphics.relief_base(lat0, lon0, bw, bh, kx, coslat, None, project, (0, 0, bw, bh), reserved=reserved)
    turkey = [(xy, f) for t, xy, f in names if t == "T U R K E Y"]
    assert turkey
    (x, y), _f = turkey[0]
    assert (x, y) != (px, py)
    ring = [[[project(lo, la) for lo, la in r] for r in poly] for poly in fake_geo[0]["polygons"]]
    assert geo.point_in_polygons(x, y, ring)


@pytest.mark.parametrize("size", FORMATS)
def test_the_route_map_names_its_country_off_the_legs_and_labels(tmp_path, monkeypatch, fake_geo, size) -> None:
    names = _drawn_names(monkeypatch)
    seen: dict[str, Any] = {}
    real_base = graphics.relief_base

    def spy_base(*a, **k):
        seen["reserved"] = k.get("reserved", ())
        return real_base(*a, **k)
    monkeypatch.setattr(graphics, "relief_base", spy_base)
    stops = [{"place": "Istanbul", "country": "Turkey", "label": "Constantinople"},
             {"place": "Ankara", "country": "Turkey", "label": "Ancyra"},
             {"place": "Derinkuyu", "country": "Turkey", "label": "Derinkuyu underground city"}]
    st = _render(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0], height=size[1]))
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    drawn = {t: probe.textbbox(xy, t, font=f, anchor="mm") for t, xy, f in names}
    assert "T U R K E Y" in drawn, list(drawn)
    for text, box in drawn.items():                                 # base px (2x): clear of every reserved box
        assert not any(graphics.LayoutConstraintSolver.boxes_intersect(box, r) for r in seen["reserved"]), text
        design = tuple(v / 2 for v in box)
        assert not any(graphics.LayoutConstraintSolver.boxes_intersect(design, p.box) for _, p in st["plates"]), text
    stop_boxes = [p.box for n, p in st["plates"] if n.startswith("stop")]
    assert stop_boxes and all(any(graphics.LayoutConstraintSolver.boxes_intersect(tuple(v * 2 for v in b), r)
                                  for r in seen["reserved"]) for b in stop_boxes)   # the stops' words reserved


# ============================================================================ round 5 review: the 9:16 scale figure
def _landmark_height(monkeypatch, fact, tmp_path) -> tuple[float, dict[str, Any]]:
    """The 9:16 scale's landmark drawn height in px (its last _silhouette call) and the render's plates."""
    seen: dict[str, float] = {}
    real = graphics._silhouette

    def spy(d, label, cx, base, hh, grey):
        seen["h"], seen["base"] = hh, base
        return real(d, label, cx, base, hh, grey)
    monkeypatch.setattr(graphics, "_silhouette", spy)
    st = _render(monkeypatch, lambda: graphics.render_scale(fact, tmp_path / "s.mp4"))
    return seen["h"], st


def test_heading_clear_counts_only_the_heading_lines_over_its_columns() -> None:
    heads = [(100, 340, 450, 362), (100, 384, 640, 451), (100, 466, 183, 490)]
    gap = graphics.SCALE_TEXT_GAP
    assert graphics.heading_clear(heads, 100, 860, 330) == 490 + gap        # spans the metric line's columns
    assert graphics.heading_clear(heads, 450, 600, 330) == 451 + gap        # under the feet figure only
    assert graphics.heading_clear(heads, 660, 760, 330) == 451 + gap        # within a gap of it sideways
    assert graphics.heading_clear(heads, 700, 800, 330) == 330              # clear of every line
    assert graphics.heading_clear([], 0, 10, 330) == 330


def test_the_9x16_depth_figure_keeps_its_height(tmp_path, monkeypatch) -> None:
    """Round 5 review (2026-11-02): the metric line and the note's reserve took the well from 345 to 275 px (the
    statue from 374 to 300). Pre-fix: well 345, statue 374 (commit HEAD, with the metres inline and a two-line
    30 px meaning, both since replaced: 395 / 430)."""
    hh, st = _landmark_height(monkeypatch, DEPTH, tmp_path)
    well = hh * 280.0 / 305.0
    assert hh >= 374 and well >= 345, (hh, well)
    rows = dict(st["plates"])
    assert graphics.SCALE_TEXT_GAP == 44
    note = rows["bracket"]
    assert note.box[1] - rows["figure"].box[3] >= graphics.SCALE_TEXT_GAP   # clear of the feet figure over it
    assert note.box[1] > rows["figure_metric"].box[3]                       # under the metres' row, not on it
    assert note.box[0] - rows["figure_metric"].box[2] >= graphics.SCALE_TEXT_GAP
    _no_collisions(st["plates"])


def test_the_9x16_height_figure_keeps_its_height(tmp_path, monkeypatch) -> None:
    """Commit HEAD drew the 305 ft statue 390 px tall; the round-3 heading had taken it to 287."""
    hh, st = _landmark_height(monkeypatch, HEIGHT, tmp_path)
    assert hh >= 390, hh
    rows = dict(st["plates"])
    gap = graphics.SCALE_TEXT_GAP
    heads = [rows[k].box for k in ("subject", "figure", "figure_metric")]
    for name in ("block_label", "ref_label"):
        b = rows[name].box
        assert b[1] >= graphics.heading_clear(heads, b[0], b[2], 0) - 1, name   # air under, and beside, the heading
    assert rows["block_label"].box[1] - rows["figure_metric"].box[3] >= gap
    _no_collisions(st["plates"])


@pytest.mark.parametrize("fact", [DEPTH, HEIGHT])
def test_the_9x16_meaning_sits_on_the_box_foot_with_air_over_it(tmp_path, monkeypatch, fact) -> None:
    _hh, st = _landmark_height(monkeypatch, fact, tmp_path)
    _x0, top, _x1, bottom = graphics.get_text_box(1080, 1920)
    rows = dict(st["plates"])
    lines = sorted((n, p) for n, p in st["plates"] if n.startswith("meaning"))
    assert all(top <= p.box[1] and p.box[3] <= bottom for _, p in st["plates"])
    assert bottom - lines[-1][1].box[3] <= 8                                 # no wasted line step under it
    frame = np.asarray(st["frames"][-1])
    ink_rows = np.where((np.abs(frame[:, 100:860].astype(int) - graphics.SEA).max(axis=-1) > 30).any(axis=1))[0]
    drawing = [y for y in ink_rows if rows["figure_metric"].box[3] + 4 < y < lines[0][1].box[1] - 4
               and not any(p.box[1] - 2 <= y <= p.box[3] + 2 for n, p in st["plates"])]
    assert drawing and lines[0][1].box[1] - max(drawing) >= graphics.SCALE_TEXT_GAP - 2


# ============================================================================ round 4 review 3: the route's scale bar
@pytest.mark.parametrize("size", FORMATS)
def test_route_stop_names_keep_clear_air_from_the_scale_bar_figures(tmp_path, monkeypatch, fake_geo, size) -> None:
    # 9:16: "200 KM" ended 1 px left of "UNDERGROUND" (Derinkuyu's name hung below its pin)
    stops = [{"place": "Istanbul", "country": "Turkey", "label": "Constantinople"},
             {"place": "Ankara", "country": "Turkey", "label": "Ancyra"},
             {"place": "Derinkuyu", "country": "Turkey", "label": "Derinkuyu underground city"}]
    st = _render(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0], height=size[1]))
    bar = [p.box for n, p in st["plates"] if n.startswith("scale")]
    names = [p.box for n, p in st["plates"] if n.startswith("stop")]
    assert bar and names
    words = " ".join(p.text for n, p in st["plates"] if n.startswith("stop")).split()
    assert words == ["CONSTANTINOPLE", "ANCYRA", "DERINKUYU", "UNDERGROUND", "CITY"]    # never cut
    assert not any(graphics.LayoutConstraintSolver.boxes_intersect(a, b, graphics.ROUTE_SCALE_AIR)
                   for a in bar for b in names)


def test_scale_bar_keeps_to_its_longest_length() -> None:
    long, short = graphics.scale_bar(39.0, 120.0, ""), graphics.scale_bar(39.0, 120.0, "", max_px=120)
    assert long["px"] <= 260 and short["px"] <= 120 < long["px"]
    assert short["value"] < long["value"] and graphics.ROUTE_BAR_STEPS[0] == 260
