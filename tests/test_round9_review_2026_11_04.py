"""Round 9 (2026-11-04): the round-8 review's fixes. A landlocked country is named inside its fill wherever it fits
at a reduced size, and outside it only nearer its own country than any other, on land as on the sea; a coarse
island is drawn as its curve and framed close, the globe held as its inset; the pole's cut is never stroked; the
scale bar keeps out of the subject's space; neighbours are chosen by adjacency; the 16:9 spans sit under the
spine; the 9:16 spans keep their separation; a break never splits a name or a figure from its noun; the Antarctic
seas are named; the polar relief is soft; the outside name is STONE_LT (R7)."""
import logging
import math

import numpy as np
import pytest

from src import brand, geo, graphics
from tests.test_r4_graphics_2026_11_02 import _render

needs_geo = pytest.mark.skipif(not geo.available() or not geo.RELIEF_FILE.exists(),
                               reason="Natural Earth data and relief not downloaded")


def E(y: int, l: str, a: bool = False) -> dict:
    return {"year": y, "label": l, "approximate": a}


HAGIA = [E(537, "Justinian's great church is consecrated"), E(558, "An earthquake brings down the dome"),
         E(1453, "Mehmed II turns it into a mosque"), E(1934, "Ataturk makes it a museum")]
COLOSSEUM = [E(72, "Vespasian begins the amphitheatre"), E(80, "Titus opens it with 100 days of games"),
             E(217, "Lightning sets the upper tiers ablaze"), E(1349, "An earthquake topples the south side")]


def _box(name: str, ring: list) -> dict:
    xs, ys = [x for x, _ in ring], [y for _, y in ring]
    return {"names": {name.lower()}, "polygons": [[ring]], "display": name, "label_at": None, "label_rank": 2,
            "type": "Sovereign country", "extent": (min(xs), min(ys), max(xs), max(ys)), "size": 4}


# ---------------------------------------------------------------------------------------------- R5: inside first
def test_a_long_fill_holds_the_name_its_inscribed_circle_would_refuse() -> None:
    w, h = 1080, 1920
    tb = graphics.get_text_box(w, h)
    ring = [(tb[0] + 20, 400.0), (tb[2] - 20, 400.0), (tb[2] - 20, 470.0), (tb[0] + 20, 470.0)]   # 70 px tall
    got = graphics.country_name_place("SWITZERLAND", [ring], (540.0, 960.0), (400.0, 860.0, 680.0, 920.0), [],
                                      w, h)
    assert got is not None                                     # the inscribed radius (35) is far under half its width
    (_x, _y), b = got
    assert ring[0][0] + graphics.COUNTRY_IN_PAD <= b[0] and b[2] <= ring[1][0] - graphics.COUNTRY_IN_PAD
    assert 400 + graphics.COUNTRY_IN_PAD <= b[1] and b[3] <= 470 - graphics.COUNTRY_IN_PAD


def test_the_name_steps_down_inside_its_fill_before_it_goes_outside(caplog) -> None:
    w, h = 1080, 1920
    tb = graphics.get_text_box(w, h)
    y0, y1 = 400, 430                                          # holds 20-24 px caps with their pad, not 28
    ring = [(tb[0] + 20, y0), (tb[2] - 20, y0), (tb[2] - 20, y1), (tb[0] + 20, y1)]
    home = np.zeros((h, w), bool)
    home[y0:y1, int(tb[0]) + 20:int(tb[2]) - 20] = True
    land = np.zeros((h, w), bool)                              # the sea all round: an outside place exists too
    land |= home
    assert graphics.country_name_place("SWITZERLAND", [ring], (540.0, 960.0), (400.0, 860.0, 680.0, 920.0), [],
                                       w, h) is None
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        spot = graphics.country_spot("SWITZERLAND", [ring], home, land, (540.0, 960.0), (400.0, 860.0, 680.0, 920.0),
                                     [], [], w, h)
    assert spot is not None and spot.inside and spot.px < graphics.COUNTRY_PX
    assert "set in its fill at" in caplog.text


# ---------------------------------------------------------------------------------------------- R5: outside, on land
def _strip() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    w, h = 1080, 1920
    ids = np.zeros((h // 2, w // 2), np.uint16)
    ids[250:520, 50:500] = 2                                  # a neighbour all round
    ids[250:520, 260:276] = 1                                 # the home: a strip 32 frame px wide
    ids[250:380, 50:150] = 3                                  # a third country in the north-west
    home = np.repeat(np.repeat(ids == 1, 2, 0), 2, 1)
    land = np.repeat(np.repeat(ids > 0, 2, 0), 2, 1)
    return ids, home, land


def test_an_outside_name_on_land_lies_along_its_home_and_nearer_it_than_any_third_country(caplog) -> None:
    ids, home, land = _strip()
    w, h = 1080, 1920
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        got = graphics.country_outside_place("HOMELAND", home, (536.0, 600.0), [], w, h, land=land, ids=(ids, 1))
    assert got is not None
    (x, y), b = got
    yy, xx = np.nonzero(home)
    d_home = float(np.min(np.hypot(xx - x, yy - y)))
    # round 15 (coordinator directive 2, deliberate): over open water only, never on the land beside its home
    assert not land[int(b[1]):int(b[3]) + 1, int(b[0]):int(b[2]) + 1].any()
    assert d_home <= graphics.COUNTRY_NEAR_PX
    # (and, deliberate, round 15: with its leader to its own ground, no other land more than NAME_NEARER_PX nearer
    # the box than the home's own; every corner nearer its home than any third country before)
    other = land & ~home
    oy, ox = np.nonzero(other)
    def box_d(ys_, xs_):
        dx = np.maximum(np.maximum(b[0] - xs_, xs_ - b[2]), 0)
        dy = np.maximum(np.maximum(b[1] - ys_, ys_ - b[3]), 0)
        return float(np.min(np.hypot(dx, dy)))
    assert box_d(oy, ox) + graphics.NAME_NEARER_PX >= box_d(yy, xx)


# ---------------------------------------------------------------------------------------------- R7
def test_the_outside_name_is_stone_lt() -> None:
    assert graphics.COUNTRY_OUT_FILL == brand.STONE_LT
    assert graphics.MAP_NAME_WEIGHT < 600


# ---------------------------------------------------------------------------------------------- R6: islands
def test_a_catmull_rom_ring_keeps_every_vertex() -> None:
    # round 15 (coordinator directive 3, deliberate): no outline is curved; every vertex is the data's own
    assert not hasattr(geo, "catmull_rom_ring")


def test_only_a_coarse_island_with_no_border_is_smoothed(monkeypatch) -> None:
    # round 15 (directive 3, deliberate): none is smoothed; view_polygons gives each polygon as it is
    hexa = [(10 + math.cos(a / 3 * math.pi), math.sin(a / 3 * math.pi)) for a in range(6)]
    cs = [_box("Isle", hexa)]
    monkeypatch.setattr(geo, "countries", lambda: cs)
    got = geo.view_polygons(10.0, 0.0, 3.0, 3.0)
    assert [list(p[0]) for _c, p in got] == [hexa]


@needs_geo
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
def test_nauru_is_framed_where_it_reads(size) -> None:
    lat, lon = -0.5228, 166.9315
    # round 15 (coordinator directives 1 and 3, deliberate): drawn from the 1:10m entities, never smoothed
    with geo.detail_view():
        home = geo.site_home(lon, lat, hint="Nauru").country
        span0 = graphics.locator_span(lat, lon, home, *size)
        span = graphics.island_span(lat, lon, home, size[0], size[1], span0)
        assert span is not None
        land, _h = graphics.view_masks(size[0], size[1], lon, lat, span, home, down=1)
        own_px = graphics.view_edge_px(lat, lon, home, size[0], size[1], span)[0]
    ys, xs = np.nonzero(land)
    k = min(size) / 1080
    # round 15 (coordinator directive 3, deliberate): framed no closer than its 1:10m outline's points hold; where
    # that leaves it under three pin heads across, the marker is the dot (pin_fallback) and the globe its inset
    ring = graphics._site_island(lat, lon, home)[1]
    c = math.cos(math.radians(lat))
    edges = [math.hypot((q[0] - p[0]) * c, q[1] - p[1]) for p, q in zip(ring, ring[1:] + ring[:1])]
    gate = sum(edges) / len(edges) * size[0] / span >= graphics.ISLAND_VERTEX_PX_RAW * k - 1
    # round 16 (review 8, deliberate): or held by the close-up's own outline gate (its median drawn source edge at
    # VIEW_EDGE_PX_OWN: Nauru's 1:10m ring reads angular any closer)
    gate = gate or own_px >= graphics.VIEW_EDGE_PX_OWN * k - 1
    assert max(xs.max() - xs.min(), ys.max() - ys.min()) >= 3 * graphics.PIN_HEAD_D * k or gate
    assert graphics.globe_alpha(10.0, math.inf) > 0.99         # the globe held at rest as its inset


# ---------------------------------------------------------------------------------------------- the pole's cut
def test_a_cut_along_the_pole_or_the_antimeridian_is_a_seam() -> None:
    ring = [(-180.0, -90.0), (-180.0, -70.0), (0.0, -66.0), (180.0, -70.0), (180.0, -90.0)]
    assert geo.seam_edges(ring) == [True, False, False, True, True]
    mirrored = [(x + 180, -180 - y) for x, y in ring]
    assert geo.seam_edges(mirrored)[-1] and geo.seam_edges(mirrored)[0]
    assert geo.seam_edges([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]) == [False, False, False]


# ---------------------------------------------------------------------------------------------- the scale bar
def test_the_scale_bar_never_rises_into_the_subject() -> None:
    w, h = 1080, 1920
    tb = graphics.get_text_box(w, h)
    bar = graphics.scale_bar(16.8, 0.9 * graphics._km_per_deg(16.8), "")
    hero = (380.0, 820.0, 700.0, 964.0)
    blocker = (tb[0], 980.0, tb[2], tb[3])                        # the bottom row taken
    lay = graphics.place_scale_bar(bar, w, h, [blocker], hero=hero)
    a = graphics.SCALE_HERO_AIR
    assert not graphics.LayoutConstraintSolver.boxes_intersect(lay.box, (hero[0] - a, hero[1] - a, hero[2] + a,
                                                                          hero[3] + a), 0)
    if lay.box[3] < hero[3]:
        assert lay.box[1] <= tb[1] + graphics.SCALE_TOP_BAND * (tb[3] - tb[1])    # a corner, not mid-map


# ---------------------------------------------------------------------------------------------- neighbours
def test_neighbours_rank_by_adjacency_not_area() -> None:
    h, w = 400, 400
    home = np.zeros((h, w), bool)
    home[100:300, 100:200] = True
    small_next = np.zeros((h, w), np.uint8)
    small_next[100:200, 200:240] = 255                             # touches the home
    big_far = np.zeros((h, w), np.uint8)
    big_far[0:400, 300:400] = 255                                  # larger, 100 px off
    a, b = {"display": "Next"}, {"display": "Far"}
    rank = graphics.neighbour_rank([(a, small_next), (b, big_far)], home)
    assert rank[id(a)] < rank[id(b)]


# ---------------------------------------------------------------------------------------------- timelines
def test_breaks_keep_a_figure_with_its_noun_and_a_name_whole() -> None:
    words = ["TITUS", "OPENS", "IT", "WITH", "100", "DAYS", "OF", "GAMES"]
    assert 5 in graphics._held_breaks(words, "Titus opens it with 100 days of games")
    words = ["WORK", "BEGINS", "ON", "THE", "CHAMP", "DE", "MARS"]
    held = graphics._held_breaks(words, "Work begins on the Champ de Mars")
    assert {5, 6} <= held and 4 not in held


@pytest.mark.parametrize("events", [HAGIA, COLOSSEUM], ids=["hagia", "colosseum"])
def test_wide_spans_are_all_under_the_spine(tmp_path, monkeypatch, caplog, events) -> None:
    caplog.set_level(logging.INFO, logger=graphics.log.name)
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", width=1920, height=1080),
                 fps=2)
    assert "over the spine" not in caplog.text
    plates = dict(st["plates"])
    years_above = [p for n, p in st["plates"] if n.startswith("year") and int(n[4:].split("_")[0]) % 2 == 0]
    floor = max(p.box[3] for p in years_above)                     # the upper row's years end over the spine
    for n, p in st["plates"]:
        if n.startswith("gap"):
            assert p.box[1] > floor, (n, p.text)
    assert plates


@pytest.mark.parametrize("events", [HAGIA, COLOSSEUM], ids=["hagia", "colosseum"])
def test_tall_spans_keep_their_air_over_the_next_year(tmp_path, monkeypatch, caplog, events) -> None:
    caplog.set_level(logging.INFO, logger=graphics.log.name)
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4"), fps=2)
    rows = dict(st["plates"])
    for k in range(1, len(events)):                                    # each span its air over the next year
        gap = rows.get(f"gap{k}")
        year = min((p.box[1] for n, p in st["plates"] if n in (f"year{k}", f"year{k}_circa")), default=None)
        if gap is not None and year is not None:
            assert year - gap.box[3] >= graphics.SPAN_AIR_BELOW_MIN, (k, year - gap.box[3])
    year_cap = graphics.cap_height(rows["year1"].font)
    label_cap = graphics.cap_height(rows["label1"].font)
    assert year_cap >= graphics.TALL_YEAR_RATIO_LAST * label_cap - 1   # R8: the 3.0 x floor


# ---------------------------------------------------------------------------------------------- polar
# round 11 (deliberate, coordinator directive 1): Natural Earth's marine polygons name the water off Port Lockroy
# the Southern Ocean (its Bellingshausen and Weddell polygons lie further south); never the Atlantic or the Pacific
@pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")
def test_the_antarctic_seas_are_named() -> None:
    def sea(lon: float, lat: float) -> str | None:
        m = geo.marine_at(lon, lat)
        return m["name"] if m else None
    assert sea(-66.5, -65.0) == "Southern Ocean"                   # west of Port Lockroy's peninsula
    assert sea(-57.0, -65.5) == "Southern Ocean"                   # east of it
    assert sea(-50.0, -72.0) == "Weddell Sea"
    assert sea(-64.0, -54.5) == "South Atlantic Ocean" and sea(-70.0, -56.0) == "South Pacific Ocean"


def test_the_polar_relief_is_soft_and_the_rest_unchanged() -> None:
    rng = np.random.default_rng(1)
    hill = rng.random((200, 300), dtype=np.float32)
    assert np.array_equal(graphics._polar_relief(hill, 50.0, 20.0), hill)           # 30-50 degrees: untouched
    soft = graphics._polar_relief(hill, -84.0, 6.0)                                 # past RELIEF_POLAR[1]
    assert float(soft.std()) < 0.5 * float(hill.std())
