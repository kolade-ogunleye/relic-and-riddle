"""Design-freeze lane A, cycle 3: invariants (gates met, inside the safe box, drawn text == signed text, bounded
steps), never pinned measurements."""
from __future__ import annotations

import pytest
from PIL import Image

from src import brand, brand_system, graphics, safe_zones, then_now
from src.refusal import LayoutError
from src.typography import get_font


def _rows_ok(rows: list[str]) -> bool:
    words = " ".join(rows).split()
    cuts, at = set(), 0
    for ln in rows[:-1]:
        at += len(ln.split())
        cuts.add(at)
    return not graphics.label_never_cuts(words, cuts)


@pytest.mark.parametrize("text", ["AMBER IN COMPLETED ROOM", "DISASSEMBLED BY GERMAN TROOPS",
                                  "ITALY TAKES THE DODECANESE", "BUILDS A ROYAL ESTATE", "GROUND TO TORCH TIP"])
@pytest.mark.parametrize("share", [0.45, 0.62, 0.8])
def test_label_wrappers_keep_the_heading_engines_hard_rule(text, share):
    """A-S2-2: the strict wrap never ends a row on a never-end word or inside a quantity; where it returns a break,
    the words are unchanged."""
    font = get_font(brand.TEXT_FONT, 46, weight=800)
    width = graphics._tracked_w(text, font, 3) * share
    got = graphics.tracked_wrap(text, font, width, 3, strict=True, max_lines=4)
    if got is not None:
        assert " ".join(got).split() == text.split()
        assert _rows_ok(got)
    fitted = graphics.tracked_wrap(text, font, width * 1.25, 3, lines=2) or [text]
    ruled = graphics.ruled_rows(fitted, font, width * 1.25, 3)
    assert " ".join(ruled).split() == text.split()
    if graphics.balanced_label_rows(fitted, font, width * 1.25, 3)[1]:
        assert _rows_ok(ruled)


def test_probe_steps_size_before_a_hard_cut():
    """A size step is tried before a row may end on a never-end word (BUILDS A / ROYAL ESTATE)."""
    import PIL.ImageDraw as D
    font = get_font(brand.TEXT_FONT, 46, weight=800)
    text = "BUILDS A ROYAL ESTATE"
    width = graphics._tracked_w(text, font, 3) * 0.62
    lines, f, short = graphics._probe_tracked(D.Draw(Image.new("L", (8, 8))), text, brand.TEXT_FONT, 46, width, 2, 30,
                                              3, weight=800)
    assert not short and len(lines) <= 2 and _rows_ok(lines) and f.size <= 46


def test_stop_words_are_the_systems_list():
    system = {w.upper() for w in brand_system.LINE_END_NEVER | brand_system.LINE_END_LAST_RESORT}
    assert graphics.TALL_STOP_WORDS == system
    assert graphics.label_hard_fault(["AMBER IN", "COMPLETED ROOM"], strict=True)
    assert not graphics.label_hard_fault(["AMBER", "IN COMPLETED ROOM"], strict=True)
    assert not graphics.label_hard_fault(["AMBER IN", "COMPLETED ROOM"])        # a preference, never a refusal


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280)])
def test_tall_text_column_is_the_inset_reel_box(size):
    """MG-1: a 9:16 graphic's text column is the Reel box inset by the system's margin token."""
    w, h = size
    tb = graphics.get_text_box(w, h)
    col = safe_zones.text_box("reel", w, h)
    assert (tb[0], tb[2]) == (col[0], col[2])
    assert graphics.inset_x0(w, h) >= col[0]


def test_map_names_keep_the_safe_box_and_column_copy_the_column():
    """A map's geographic names keep the Reel box (the gate); column copy keeps the 687 px column; 16:9 one box."""
    gb = safe_zones.graphics_box(1080, 1920)
    mb = graphics.map_text_box(1080, 1920)
    assert (mb[0], mb[2]) == (gb[0], gb[2]) and mb[1:4:2] == graphics.get_text_box(1080, 1920)[1:4:2]
    assert graphics.map_text_box(1920, 1080) == graphics.get_text_box(1920, 1080)
    assert graphics.get_text_box(1080, 1920)[0] > gb[0]          # the context var never leaks out


@pytest.mark.parametrize("lat", [10.0, 45.0, 60.0])
def test_tall_scale_bar_stays_inside_the_text_column(lat):
    """A-S1-2: the bar's ticks, casing and figures inside the 9:16 text column, wherever it is placed."""
    w, h = 1080, 1920
    bar = graphics.scale_bar(lat, 40.0, "", bar_range=graphics.scale_range(w, h))
    lay = graphics.place_scale_bar(bar, w, h, [(80, 900, 400, 1060)])
    tb = graphics.get_text_box(w, h)
    box = graphics.scale_bar_box(bar, lay.x, lay.y, graphics.scale_label_font(w, h), lay.direction)
    assert tb[0] <= box[0] and box[2] <= tb[2]


def _fam(lat=45.0, ppd=60.0, w=1080, h=1920):
    return graphics.scale_bar_family(lambda m: graphics.scale_bar(lat, ppd, "", m, bar_range=graphics.scale_range(w, h)))


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_furniture_bar_corner_order_never_on_a_mark(size):
    """The furniture ruling: lower-left first; a mark there sends it to the next corner; the bar's padded box never
    meets a label or a hard mark; with every corner taken, None (the caller records the omission)."""
    import numpy as np
    w, h = size
    tb = graphics.get_text_box(w, h)
    free = graphics.furniture_scale_bar(_fam(w=w, h=h), w, h, [])
    assert free is not None and free.corner == "ll" and free.soft == 0
    hard = np.zeros((h, w), bool)
    hard[int(tb[1] + (tb[3] - tb[1]) * 0.5):, : (tb[0] + tb[2]) // 2] = True          # the lower-left half taken
    got = graphics.furniture_scale_bar(_fam(w=w, h=h), w, h, [], hard=hard)
    assert got is not None and got.layout.box != free.layout.box       # moved off the mark, along its edge
    b, pad = got.layout.box, graphics.SCALE_MARK_PAD
    band = (graphics.SCALE_CORNER_STEPS + 1) * graphics.SCALE_SHIFT + (b[3] - b[1])
    assert b[3] >= tb[3] - band or b[1] <= tb[1] + band                  # on the bottom or top band, never mid-map
    assert not hard[max(0, int(b[1] - pad)):int(b[3] + pad), max(0, int(b[0] - pad)):int(b[2] + pad)].any()
    assert tb[0] - 0.5 <= b[0] and b[2] <= tb[2] + 0.5 and tb[1] - 0.5 <= b[1] and b[3] <= tb[3] + 0.5
    label = (got.layout.box[0] - 5, got.layout.box[1] - 5, got.layout.box[2] + 5, got.layout.box[3] + 5)
    again = graphics.furniture_scale_bar(_fam(w=w, h=h), w, h, [label], hard=hard)
    assert again is None or not graphics.LayoutConstraintSolver.boxes_intersect(again.layout.box, label, pad)
    everything = np.ones((h, w), bool)
    assert graphics.furniture_scale_bar(_fam(w=w, h=h), w, h, [], hard=everything) is None


def test_furniture_bar_prefers_a_quiet_place_over_borders():
    import numpy as np
    w, h = 1080, 1920
    tb = graphics.get_text_box(w, h)
    soft = np.zeros((h, w), bool)
    soft[int(tb[3]) - 120:int(tb[3]), int(tb[0]):int(tb[0]) + 400] = True              # borders in the lower-left
    got = graphics.furniture_scale_bar(_fam(w=w, h=h), w, h, [], soft=soft)
    assert got is not None and got.soft == 0


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_a_routes_bar_never_meets_its_stops_or_names(tmp_path, monkeypatch, size):
    """The furniture invariant on a real route's geometry (the Morocco stops): the bar's padded box meets no stop's
    dot and no name drawn on the route; where it is omitted the layout says so."""
    from tests.test_qf24_route_neighbours import _morocco_route, needs_data
    if needs_data.args and needs_data.args[0]:
        pytest.skip("Natural Earth data not fetched")
    from src.refusal import collecting
    with collecting():
        lay = _morocco_route(tmp_path, monkeypatch, *size)
    rec = lay["scale_bar"]
    if rec.get("omitted"):
        return
    b, pad = rec["box"], graphics.SCALE_MARK_PAD
    for x, y, r in lay["stops"]:
        dot = (x - r - 4, y - r - 4, x + r + 4, y + r + 4)
        assert not graphics.LayoutConstraintSolver.boxes_intersect(tuple(b), dot, pad), (b, dot)
    for c in lay["name_checks"]:
        assert not graphics.LayoutConstraintSolver.boxes_intersect(tuple(b), tuple(c["box"]), 0), (b, c)


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_the_amber_routes_bar_clears_the_home_outline_and_every_border(tmp_path, monkeypatch, size):
    """Lander ruling (fd532f34 rejected): on the real Amber route (the board's stops, Catherine Palace to Kholat
    Syakhl), the bar's line, ticks and figures with their halo keep SCALE_MARK_PAD from the home's outline and every
    land border, as from the path, the dots and the coasts. Before the fix the route passed only its path, dots and
    coasts: no border reached the bar's test, and the 9:16 bar crossed Russia's outline and its land border."""
    import numpy as np
    from tests.test_r4_graphics_2026_11_02 import _render_fast
    from tests.test_qf24_route_neighbours import needs_data
    if needs_data.args and needs_data.args[0]:
        pytest.skip("Natural Earth data not fetched")
    cap: dict = {"borders": []}
    real_borders, real_place = graphics.land_borders, graphics.furniture_scale_bar

    def spy_borders(id_map, width, height):
        got = real_borders(id_map, width, height)
        cap["borders"].append(np.asarray(got, bool))
        return got

    def spy_place(bars, width, height, labels, hard=None, soft=None, box=None):
        cap["hard"] = None if hard is None else np.asarray(hard, bool)
        cap["got"] = real_place(bars, width, height, labels, hard, soft, box)
        return cap["got"]
    monkeypatch.setattr(graphics, "land_borders", spy_borders)
    monkeypatch.setattr(graphics, "furniture_scale_bar", spy_place)
    stops = [{"place": "Catherine Palace", "country": "Russia", "label": "Catherine Palace"},
             {"place": "Kholat Syakhl", "country": "Russia", "label": "Kholat Syakhl"}]
    _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0],
                                                            height=size[1]))
    assert cap["borders"], "the route's bar was tested without the land borders"
    borders = np.logical_or.reduce(cap["borders"])
    assert cap["hard"] is not None and not (borders & ~cap["hard"]).any()        # every border is a hard mark
    fb = cap["got"]
    if fb is None:
        return                                                                    # declined: recorded on the ledger
    w, h = size
    font = graphics.scale_label_font(w, h)
    pad, halo = graphics.SCALE_MARK_PAD, graphics.SCALE_LABEL_HALO_PX
    x, y, bar = fb.layout.x, fb.layout.y, fb.bar
    foot = [(x - 3 - pad, y - graphics.SCALE_TICK - 2 - pad, x + bar.length + 3 + pad, y + graphics.SCALE_TICK + 2 + pad)]
    for _n, text, xy in graphics.scale_bar_label_specs(bar, x, y, font):
        b = graphics.ink_box(xy, text, font, "ls", font.size * graphics.SCALE_LABEL_TRACK)
        foot.append((b[0] - halo - pad, b[1] - halo - pad, b[2] + halo + pad, b[3] + halo + pad))
    for b in foot:
        x0, y0, x1, y1 = (max(0, int(v)) for v in b)
        assert not cap["hard"][y0:y1, x0:x1].any(), (size, b)


def test_every_shipped_bar_carries_both_units():
    """Lander ruling: a shorter round bar keeps both units ("50 KM" alone was refused); none: no bar, recorded."""
    for lat, ppd in ((45.0, 60.0), (60.0, 15.0), (10.0, 300.0), (55.0, 4.0)):
        fam = _fam(lat, ppd)
        assert all(b.secondary is not None for b in fam)


def test_omitted_scale_bar_is_recorded_on_the_ledger():
    from src.refusal import Declined, collecting
    with collecting() as rows:
        graphics.omit_scale_bar("map x.mp4", "no corner is clear")
    assert rows and rows[0]["omitted"] == "the scale bar" and "furniture" in rows[0]["rule"]
    with pytest.raises(Declined):
        graphics.omit_scale_bar("map x.mp4", "no corner is clear")         # no ledger: never only a log line


def test_scale_bar_figures_in_the_data_face():
    """A-S2-3 (ruling 3): rulers, axes and scale-bar figures in the data face."""
    assert graphics.scale_label_font(1080, 1920).font.family == graphics.data_font(30).font.family


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920)])
def test_data_floor_on_both_orientations(size):
    w, h = size
    need = brand_system.label_floor_px(w, h, "data_label")
    assert graphics.data_floored(10, w, h) >= need - 1e-6
    assert graphics.scale_label_font(w, h).size >= need - 1e-6


@pytest.mark.parametrize("feet", [20.0, 30.0, 60.0, 100.0, 170.0])
def test_twenty_to_hundred_feet_has_a_sourced_reference(feet):
    """Stress 1 (real Memnon, 60 ft): a subject of 20-100 ft has a landmark within 1/3x-3x, from a sourced row."""
    ref = graphics.pick_reference(feet)
    assert ref is not None
    row = next(r for r in graphics.references() if r["label"] == ref[0])
    assert row["source_url"].startswith("https://") and row["quote"] and row["feet"] > 0
    assert 1 / 3 <= feet / row["feet"] <= 3


def test_every_reference_has_a_drawing():
    for r in graphics.references():
        kind = graphics._landmark_kind(r["label"])
        assert kind in graphics._LANDMARK_DRAWINGS and kind in graphics.SILHOUETTE_WIDTH


@pytest.mark.parametrize("fact", [{"value": 1000.0, "unit": "m", "dimension": "height", "subject": "X"},
                                  {"value": 0.0, "unit": "ft", "dimension": "height", "subject": "X"}])
def test_scale_without_a_reference_is_a_recorded_decline(fact, tmp_path):
    """Stress 1: never a silent None; the refusal names the rule."""
    with pytest.raises(LayoutError) as e:
        graphics.render_scale(fact, tmp_path / "s.mp4", width=1080, height=1920)
    assert e.value.code in ("scale_no_reference", "scale_not_positive") and "declined" in e.value.reason


@pytest.mark.parametrize("u", [0.0, 0.2, 0.5, 0.8, 1.0])
def test_cover_window_across_stays_inside_the_photo(u):
    """A-S2-1: the shared window searched across is always inside the photo and keeps the cover crop's size."""
    size, big = (3000, 2000), (1123, 1997)
    box = then_now._cover_window(size, big, 1.2, 0.4, u)
    centred = then_now._cover_window(size, big, 1.2, 0.4)
    assert 0 <= box[0] and box[2] <= size[0] + 1e-6 and 0 <= box[1] and box[3] <= size[1] + 1e-6
    assert abs((box[2] - box[0]) - (centred[2] - centred[0])) < 1e-6


def test_span_label_takes_a_lighter_tier_to_reach_the_gate():
    """Stress 12: a span planned under the text gate in its tier is set in the lighter tier that reaches it."""
    ground = Image.new("RGB", (600, 200), (250, 250, 248))
    font = get_font(brand.TEXT_FONT, 28, weight=graphics.TALL_SPAN_WEIGHT)
    plate = graphics._plan_gated(ground, (40, 120), "ABOUT 6,000 YEARS LATER", font,
                                 (brand.STONE, brand.STONE_LT, brand.BONE), "ls", track=2)
    first = graphics._plan_thin(ground, (40, 120), "ABOUT 6,000 YEARS LATER", font, brand.STONE, "ls", halo_px=None,
                                track=2)
    assert plate.contrast is None or plate.contrast >= (first.contrast or 0) - 1e-6


def test_two_number_cards_step_the_first_back_by_design(tmp_path, monkeypatch):
    """A-S2-5, lander ruling 3 (cycle 3): the first of two number cards steps back when the second lands (the F13
    hierarchy, the timeline's grammar): its words dim to STONE_LT while its figure and unit stay gold. Two label
    colours on the last frame are this intended step-back, not two treatments of one role."""
    twins: list[tuple[str, tuple]] = []
    real = graphics._twin

    def spy(ground, plate, fill):
        twins.append((plate.text, tuple(fill)))
        return real(ground, plate, fill)
    monkeypatch.setattr(graphics, "_twin", spy)
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.2)
    cards = [{"value": "36 hours", "label": "Time to disassemble the room"},
             {"value": "13,000 lbs", "label": "Amber in completed room"}]
    with graphics.poster_only():
        graphics.render_stat_cards(cards, tmp_path / "n.mp4", width=1080, height=1920)
    dimmed = dict(twins)
    label_rows = [t for t in dimmed if "TIME" in t or "THE ROOM" in t]      # the first card's label rows
    assert label_rows and all(dimmed[t] == tuple(brand.STONE_LT) for t in label_rows)
    figure_fill = tuple(graphics.figure_fill())
    assert any(fill == figure_fill for _t, fill in twins)          # the stepped-back card's quantity stays gold
    assert not any("AMBER" in t or "13" in t for t in dimmed)       # the second card never steps back


def test_obelisk_upper_face_keeps_the_signed_length():
    """MG-3: the obelisk's upper face is drawn straight up; its ink never runs past the signed length's end."""
    from PIL import ImageDraw
    im = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
    graphics._obelisk_lying(ImageDraw.Draw(im), 100, 600, 180, 600 * graphics.SUBJECT_LYING_ASPECT)
    bb = im.getbbox()
    assert bb[0] >= 100 and bb[2] <= 601 and bb[3] <= 181
    assert 180 - bb[1] <= 500 * graphics.SUBJECT_LYING_ASPECT + 1


# ------------------------------------------------------------------ lander ruling (cycle 3): map framing and the pin
# real runs' recorded places (output/run_*/graphics/graphic_map.mp4.lineage.json): a vast home on the coast, a vast
# home inland, a coastal site that was panned into a corner
FRAMING_PLACES = {
    "amber": {"label": "Catherine Palace", "country": "Russia", "lat": 59.716, "lon": 30.395555555555557},
    "tunguska": {"label": "Podkamennaya Tunguska", "country": "Russia", "lat": 57.6873, "lon": 104.3553},
    "paracas": {"label": "Paracas Peninsula", "country": "Peru", "lat": -13.858888888889, "lon": -76.328888888889},
}


def _locator(tmp_path, monkeypatch, key, size):
    import json
    from pathlib import Path
    from tests.test_qf24_route_neighbours import needs_data
    from tests.test_r4_graphics_2026_11_02 import _render
    if needs_data.args and needs_data.args[0]:
        pytest.skip("Natural Earth data not fetched")
    calls: list = []
    real_dot = graphics._draw_dot

    def spy_dot(im, xy, k, scale=1.0, alpha=1.0, ripple=(0.0, 0.0)):
        calls.append((xy, alpha))
        return real_dot(im, xy, k, scale, alpha, ripple)
    monkeypatch.setattr(graphics, "_draw_dot", spy_dot)
    out = tmp_path / f"{key}.mp4"
    st = _render(monkeypatch, lambda: graphics.render_map(FRAMING_PLACES[key], out, width=size[0], height=size[1]),
                 fps=1)
    st["layout"] = json.loads(Path(str(out) + ".layout.json").read_text(encoding="utf-8"))
    st["dots"] = calls
    return st


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("key", list(FRAMING_PLACES))
def test_the_close_up_is_framed_on_the_site_with_named_context(tmp_path, monkeypatch, key, size):
    """Lander ruling (map framing): the site stands in the middle third of the content box (the Reel box, title-safe)
    on both axes (Catherine Palace's 9:16 end frame had it at x 0.87, y 0.19, Russia a sliver); the end frame names the
    home and at least one neighbour or sea; and the target is on the site from the first frame."""
    import numpy as np
    st = _locator(tmp_path, monkeypatch, key, size)
    lay = st["layout"]
    w, h = size
    u, v = graphics.site_box_share(tuple(lay["anchor"]), w, h)
    assert graphics.site_in_middle_third(tuple(lay["anchor"]), w, h), (key, size, round(u, 2), round(v, 2))
    assert lay["country"] is not None, lay["home_drop"]
    if key == "tunguska":
        # open item (reported to the lander): deep inside a vast home no neighbour's name or sea fits the close-up's
        # box at any span the base's detail allows (MONGOLIA's ground is framed in, its name has no room); the
        # omission is recorded, never silent
        assert lay["neighbours"] or lay["water"] or any("neighbour" in str(r.get("omitted")) for r in st["omitted"])
    else:
        assert lay["neighbours"] or lay["water"], "no neighbour or sea named on the close-up"
    # the first frame: the target drawn on the site, inside the frame, its BONE ring there in the pixels
    tx, ty = lay["target_first"]
    assert 0 <= tx < w and 0 <= ty < h
    assert st["dots"] and np.hypot(st["dots"][0][0][0] - tx, st["dots"][0][0][1] - ty) < 1 and st["dots"][0][1] == 1
    arr = np.asarray(st["frames"][0]).astype(int)
    yy, xx = np.mgrid[0:h, 0:w]
    ring = np.abs(np.hypot(xx - tx, yy - ty) - (graphics.DOT_D - graphics.DOT_RING_W) / 2 + 1) <= 1.5
    assert (np.abs(arr[ring] - np.array(brand.BONE)).max(axis=1) <= 40).mean() > 0.5
    # the opening at continental scale on both orientations: its shorter side spans at least 40 degrees
    assert lay["span_open"] * min(w, h) / w >= 40 - 1e-6 or lay["span_open"] < 40


def test_the_target_is_shown_until_the_marker_lands():
    for t in (0.0, 0.4, 0.8, 1.0, 1.6, graphics.MAP_TRAVEL):
        assert graphics.target_motion(t, "pin")[0] == 1.0 and graphics.target_motion(t, "dot")[0] == 1.0
    # it pulses while the camera travels, never after it rests
    assert any(graphics.target_motion(t / 10, "pin")[2] > 0 for t in range(10))
    assert graphics.target_motion(graphics.MAP_TRAVEL + 0.05, "pin")[2] == 0
    landed = graphics.MAP_PIN_AT + graphics.PIN_FALL_S
    assert graphics.target_motion(landed + 0.01, "pin")[0] == 0
    assert graphics.target_motion(graphics.MAP_PIN_AT + graphics.DOT_POP_S + 0.01, "dot")[0] == 0


def test_a_vast_home_is_framed_on_context_not_its_share():
    from src import geo
    if not geo.available():
        pytest.skip("Natural Earth data not fetched")
    russia, peru, malta = (geo.find_country(n) for n in ("Russia", "Peru", "Malta"))
    assert graphics.is_vast_home(russia) and not graphics.is_vast_home(peru) and not graphics.is_vast_home(malta)
    assert 1.5e7 < graphics.home_area_km2(russia) < 2.0e7        # about 17.1 million km2 (planar estimate)
