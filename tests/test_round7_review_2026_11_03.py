"""Round 7 (2026-11-03): the last review's fixes and the coordinator's rulings R1-R6: seas named by the water they
name, the landlocked country's outside name by its own border, the scale bar's 1-2-5 primary with a tick for the
secondary, its two-dimensional place, neighbours a step under the subject, a tiny island framed, the 9:16 spans back
in their own row (never level with a year), the 16:9 labels with no weak middle row, the route naming its country."""
import itertools
import logging
import math
import re

import numpy as np
import pytest
from PIL import Image

from src import brand, geo, graphics
from tests.test_r3_graphics_2026_11_02 import fake_geo  # noqa: F401  (fixture)
from tests.test_r4_graphics_2026_11_02 import _render, _render_fast

needs_geo = pytest.mark.skipif(not geo.available() or not geo.RELIEF_FILE.exists(),
                               reason="Natural Earth data and relief not downloaded")
def E(y: int, l: str, a: bool = False) -> dict:
    return {"year": y, "label": l, "approximate": a}
POMPEII = [E(-600, "Oscans found a town on the Sarnus", True), E(62, "Earthquake damages the forum temples"),
           E(79, "Vesuvius buries the city in ash"), E(1748, "Bourbon engineers begin the excavations")]
BABYLON = [E(-2100, "Ziggurat of the moon god raised", True), E(-605, "Nebuchadnezzar rebuilds the walls"),
           E(-323, "Alexander dies here"), E(1899, "German excavations begin at the Ishtar gate")]
STONEHENGE = [E(-3000, "The first ditch and bank are dug", True), E(-2500, "Sarsen stones raised"),
              E(1130, "Henry of Huntingdon writes of it"), E(1918, "Cecil Chubb gives it to the nation")]
LONG = [E(-2560, "Khufu's workers finish the Great Pyramid at Giza after twenty years"),
        E(-1323, "Tutankhamun is buried in the Valley of the Kings"),
        E(1798, "Napoleon's savants arrive with the army and begin measuring"),
        E(1922, "Howard Carter opens the tomb and finds wonderful things")]
SIZES = [(1080, 1920), (1920, 1080)]


def _sea(lon: float, lat: float) -> str | None:
    # round 11 (deliberate, coordinator directive 1): the body whose own marine polygon holds the point
    m = geo.marine_at(lon, lat)
    return m["name"] if m else None


# ---------------------------------------------------------------------------------------------- review 1: seas
# round 11 (deliberate, directive 1): Natural Earth's names ("South Atlantic Ocean", "South Pacific Ocean": the
# anchors' table said "Atlantic Ocean" and "Pacific Ocean"); each point on the same side as before
@pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")
def test_seas_are_named_by_the_water_round_ushuaia_and_the_horn_of_africa() -> None:
    assert _sea(-64.0, -54.5) == "South Atlantic Ocean"     # east of Tierra del Fuego (was "Pacific Ocean")
    assert _sea(-65.5, -50.0) == "South Atlantic Ocean"
    assert _sea(-76.0, -50.0) == "South Pacific Ocean"      # west of Chile
    assert _sea(-70.0, -56.0) == "South Pacific Ocean"      # south-west of the Horn
    assert _sea(45.0, 12.0) == "Gulf of Aden" and _sea(43.9, 12.2) == "Gulf of Aden"   # the western gulf
    assert _sea(40.0, 16.0) == "Red Sea" and _sea(42.6, 13.6) == "Red Sea"


@pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")
def test_a_sea_name_sits_wholly_on_its_own_sea() -> None:
    w, h = 1080, 1920
    land = np.zeros((h, w), bool)

    def from_frame(x: float, y: float) -> tuple[float, float]:          # the Red Sea's mouth into the gulf
        return 43.6 + (x - w / 2) / 120, 12.6 - (y - h / 2) / 120
    for wl in graphics.water_labels(w, h, from_frame, land, []):
        b = wl.box
        # round 11 (deliberate, directive 1): every sampled point under the name is its own body's (all, not
        # WATER_OWN_SHARE of them)
        for x in np.arange(b[0], b[2], 6):
            for y in np.arange(b[1], b[3], 6):
                assert _sea(*from_frame(float(x), float(y))) == wl.name, wl.name


# ---------------------------------------------------------------------------------------------- R5: landlocked
def _landlocked() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    w, h = 1080, 1920
    ids = np.zeros((h, w), np.uint16)
    ids[700:1100, 100:500] = 2                                           # a neighbour west
    ids[700:1100, 500:800] = 1                                           # home
    ids[700:1100, 800:1000] = 3                                          # a neighbour east
    ids[500:700, 100:1000] = 4                                           # a neighbour north (all land round home)
    return ids, ids == 1, ids > 0


def test_a_landlocked_name_sits_12_px_off_its_own_border_on_the_next_land() -> None:
    # round 15 (coordinator directive 2, LEADER OR DROP, deliberate): an outside name sits only over open water,
    # never on the next land; a landlocked country with no water in reach has no outside place (it is named in its
    # fill, or dropped and logged)
    ids, home, land = _landlocked()
    w, h = 1080, 1920
    assert graphics.country_outside_place("HOMELAND", home, (650.0, 900.0), [], w, h, land=land, ids=(ids, 1)) is None


def test_a_name_by_a_third_country_is_never_chosen() -> None:
    ids, home, _land = _landlocked()
    w, h = 1080, 1920
    ids[1100:1920, :] = 5                                               # every side has a third country close
    ids[0:500, :] = 6
    ids[:, 0:100] = 7
    ids[:, 1000:] = 8
    got = graphics.country_outside_place("HOMELAND", home, (650.0, 900.0), [], w, h, land=ids > 0, ids=(ids, 1))
    # round 15 (directive 2, deliberate): no open water at all, so no outside place (it was a third country's)
    assert got is None


@needs_geo
@pytest.mark.parametrize("style", ["pin", "leader"])
def test_great_zimbabwe_names_zimbabwe_on_9x16(tmp_path, monkeypatch, style) -> None:
    monkeypatch.setattr(graphics, "MAP_MARKER_STYLE", style)
    place = {"lat": -20.2675, "lon": 30.9338, "country": "Zimbabwe", "label": "Great Zimbabwe", "approximate": False}
    st = _render_fast(monkeypatch, lambda: graphics.render_map(place, tmp_path / "z.mp4", narration="11 metres"))
    country = dict(st["plates"]).get("country")
    assert country is not None and country.text == "ZIMBABWE"
    home = graphics.geo.find_country("Zimbabwe")
    assert home is not None


# ---------------------------------------------------------------------------------------------- R1, R2: scale bar
def test_the_scale_bar_order_and_values_follow_the_rulings() -> None:
    assert graphics.scale_bar(21.1, 60, "")["unit"] == "KM"             # R1: KM without a narration unit
    assert graphics.scale_bar(21.1, 60, "a wall 30 feet high")["unit"] == "MI"
    assert graphics.scale_bar(21.1, 60, "a wall 9 metres high")["unit"] == "KM"
    for px_per_deg in np.linspace(5, 3000, 90):
        for rng in (graphics.SCALE_RANGE_TALL, graphics.SCALE_RANGE_WIDE):
            bar = graphics.scale_bar(21.1, float(px_per_deg), "", bar_range=rng)
            m = bar.value / 10 ** math.floor(math.log10(bar.value) + 1e-9)
            assert round(m, 6) in (1, 2, 5), bar.label                     # R2: the primary 1-2-5
            assert bar.length == bar.px                                    # one bar, the primary's
            if bar.secondary is not None:                                  # a tick on it, never past it
                assert bar.secondary.px < bar.px and graphics.scale_ends_apart(bar.px, bar.secondary.px)
                assert bar.secondary.value != bar.value                    # never the same number


def test_place_scale_bar_rises_off_a_blocked_bottom_row() -> None:
    bar = graphics.scale_bar(16.8, 0.9 * graphics._km_per_deg(16.8), "")
    w, h = 1080, 1920
    tb = graphics.get_text_box(w, h)
    blocker = (tb[0], 980.0, tb[2], tb[3])                              # the whole bottom row is a label
    lay = graphics.place_scale_bar(bar, w, h, [blocker])
    assert not graphics.LayoutConstraintSolver.boxes_intersect(lay.box, blocker, graphics.SCALE_LABEL_CLEAR - 0.5)
    assert lay.y < graphics.SCALE_ORIGIN_TALL[1] and tb[1] <= lay.box[1]


def test_place_scale_bar_breaks_the_least_where_nothing_is_clear(caplog) -> None:
    bar = graphics.scale_bar(16.8, 0.9 * graphics._km_per_deg(16.8), "")
    w, h = 1080, 1920
    home = np.ones((h, w), bool)                                        # all home fill: no place keeps 20 %
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        lay = graphics.place_scale_bar(bar, w, h, [], home=home)
    assert "breaks the least" in caplog.text
    assert graphics.get_text_box(w, h)[1] <= lay.box[1]


# ---------------------------------------------------------------------------------------------- R4: neighbours
def test_neighbour_names_are_a_step_under_the_country_name() -> None:
    assert graphics.MAP_NAME == brand.STONE_LT
    assert graphics.MAP_NAME_WEIGHT < 600                               # the country name's weight
    for size in SIZES:
        f = graphics.neighbour_font(*size)
        k = 1.0 if size[1] > size[0] else min(size[0] / 1920, size[1] / 1080)
        inside = round((graphics.COUNTRY_PX if size[1] > size[0] else graphics.COUNTRY_PX_WIDE) * k)
        assert f.size <= inside and f.size <= round(graphics.COUNTRY_OUT_PX * k) + 2, (size, f.size)
    # round 9 (deliberate, coordinator R7): the outside name is spec A.4's STONE_LT, like a neighbour's; it is told
    # from one by its weight (600 against MAP_NAME_WEIGHT) and its place by the home's border
    assert graphics.COUNTRY_OUT_FILL == brand.STONE_LT


def test_a_name_drawn_on_the_frames_carries_its_halo() -> None:
    sprite = Image.new("L", (80, 30), 0)
    sprite.paste(255, (10, 10, 70, 20))
    labels = [{"name": "Georgia", "sprite": sprite, "halo": graphics._name_halo(sprite, 24), "box": (300, 600, 380, 630)}]
    plan = graphics.map_label_plan(labels, [1.0, 1.0], 1080, 1920, 1080, 1920)
    assert plan[-1] and plan[-1][0]["halo"] is not None                 # GEORGIA had none (1.6:1, review 6)


def test_a_thin_neighbour_name_is_measured_not_dropped() -> None:
    rest = Image.new("RGB", (600, 200), (88, 92, 100))
    font = graphics.get_font("Montserrat", 24, weight=graphics.MAP_NAME_WEIGHT)
    pl = graphics._plan_thin(rest, (300, 100), "G E O R G I A", font, graphics.MAP_NAME, "mm", 21,
                             graphics.MAP_NAME_HALO)
    assert pl.contrast is not None and pl.contrast >= brand.TEXT_CONTRAST - 0.05
    assert pl.measure(pl.draw(rest)) is not None


# ---------------------------------------------------------------------------------------------- R6: a tiny island
@needs_geo
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_rapa_nui_is_framed_on_its_island(size) -> None:
    home = geo.find_country("Chile")
    lat, lon = -27.1127, -109.3497
    span0 = graphics.locator_span(lat, lon, home, *size)
    got = graphics.island_span(lat, lon, home, size[0], size[1], span0)
    assert got is not None and got < span0
    # round 8 (deliberate, review 1): the zoom stops where the 1:50m outline's detail holds (its mean edge at most
    # ISLAND_VERTEX_PX on the frame), so the island is drawn at its least visible extent, not at 3 % of the box
    land, _h = graphics.view_masks(size[0], size[1], lon, lat, got, home, down=1)
    ys, xs = np.nonzero(land)
    assert len(xs) and max(xs.max() - xs.min(), ys.max() - ys.min()) >= 48   # the island reads, not a speck
    ring = graphics._site_island(lat, lon, home)[1]
    coslat = math.cos(math.radians(lat))
    edges = [math.hypot((b[0] - a[0]) * coslat, b[1] - a[1]) for a, b in zip(ring, ring[1:] + ring[:1])]
    assert sum(edges) / len(edges) * size[0] / got <= graphics.ISLAND_VERTEX_PX_RAW + 0.5
    # a close-up with its land keeps its framing
    assert graphics.island_span(39.0, 35.0, geo.find_country("Turkey"), size[0], size[1], 18.0) is None


# ---------------------------------------------------------------------------------------------- R3: spans
def _bands_apart(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return a[3] <= b[1] or b[3] <= a[1]


@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
@pytest.mark.parametrize("events", [POMPEII, BABYLON, STONEHENGE, LONG], ids=["pompeii", "babylon", "stonehenge",
                                                                              "long"])
def test_no_span_shares_a_vertical_band_with_a_year(tmp_path, monkeypatch, events, size) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", width=size[0],
                                                               height=size[1]), fps=2)
    years = [p.box for n, p in st["plates"] if n.startswith("year")]
    spans = [(n, p) for n, p in st["plates"] if n.startswith("gap")]
    assert spans
    for n, p in spans:
        assert all(_bands_apart(p.box, y) for y in years), (n, p.text)
        # round 14 (review: "LATER" alone on its row): a count over "YEARS LATER" ("ABOUT 700 / YEARS LATER") too
        assert re.search(r"YEARS( LATER)?$|^LATER$|^(ABOUT )?[\d,]+$", p.text), p.text   # never "+17 YRS"
        assert "YRS" not in p.text


def test_tall_spans_are_in_their_own_row_between_the_entries(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(POMPEII, tmp_path / "t.mp4"), fps=2)
    rows = dict(st["plates"])
    gaps = {int(n[3:]): p for n, p in st["plates"] if re.fullmatch(r"gap\d", n)}
    assert set(gaps) == {1, 2, 3}
    assert gaps[1].text == "ABOUT 700 YEARS LATER" and gaps[2].text == "17 YEARS LATER"
    for k, p in gaps.items():
        above = max(q.box[3] for n, q in st["plates"] if re.fullmatch(rf"label{k - 1}", n))
        below = min(q.box[1] for n, q in st["plates"] if n in (f"year{k}", f"year{k}_circa"))
        assert above < p.box[1] and p.box[3] < below, (k, p.text)
        lines = [q for n, q in st["plates"] if n == f"label{k - 1}"]
        gap = (min(b.box[1] - a.box[3] for a, b in itertools.pairwise(lines)) if len(lines) > 1
               else round(lines[0].font.size * graphics.TALL_LEADING) - graphics.cap_height(lines[0].font))
        assert p.box[1] - above >= 1.5 * gap - 2, (k, p.box[1] - above, gap)   # 1.5 x the line gap
        assert tuple(p.fill) == brand.STONE
    year_cap = graphics.cap_height(rows["year1"].font)
    label_cap = graphics.cap_height(rows["label1"].font)
    assert year_cap >= graphics.TALL_YEAR_RATIO_MIN * label_cap          # the years stay the hero


# ---------------------------------------------------------------------------------------------- review 7: 16:9
def test_wide_labels_have_no_weak_middle_row_where_a_break_avoids_it(tmp_path, monkeypatch) -> None:
    angkor = [E(1113, "Suryavarman II begins the temple"), E(1431, "Ayutthaya sacks Angkor")]
    for events in (angkor, STONEHENGE):
        st = _render(monkeypatch, lambda ev=events: graphics.render_timeline(ev, tmp_path / "t.mp4", 1920, 1080), fps=1)
        by: dict[str, list] = {}
        for n, p in st["plates"]:
            if n.startswith("label"):
                by.setdefault(n, []).append(p)
        for ps in by.values():
            ps.sort(key=lambda p: p.box[1])
            widths = [p.box[2] - p.box[0] for p in ps]
            for j in range(1, len(ps) - 1):                             # a middle row as wide as the others
                assert widths[j] >= graphics.WIDE_LONE_SHARE * max(widths[:j] + widths[j + 1:]) - 2, \
                    [p.text for p in ps]
    rows = [p.text for n, p in st["plates"] if n == "label0"]
    assert rows != ["THE FIRST DITCH", "AND BANK", "ARE DUG"]


def test_a_wide_long_label_takes_rows_of_its_column_before_a_wider_measure(tmp_path, monkeypatch, caplog) -> None:
    # review 7: a label the 420 px column cannot hold in three rows takes more rows of it (a copy-fit error,
    # logged) where the frame holds them; a wider measure only where it does not, and then always logged
    events = [E(-2560, LONG[0]["label"]), E(-1323, "Tomb sealed"), E(1798, "Savants arrive"), E(1922, "Carter opens it")]
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", 1920, 1080), fps=1)
    rows = [p for n, p in st["plates"] if n == "label0"]
    assert " ".join(p.text for p in rows) == graphics.drawable(LONG[0]["label"].upper(), "Montserrat")
    assert max(p.box[2] - p.box[0] for p in rows) <= graphics.WIDE_LABEL_COL + 8, [p.text for p in rows]
    assert len(rows) <= graphics.WIDE_LABEL_ROWS or "copy-fit" in caplog.text
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(LONG, tmp_path / "t2.mp4", 1920, 1080), fps=1)
    for n, p in st["plates"]:
        if n.startswith("label") and p.box[2] - p.box[0] > graphics.WIDE_LABEL_COL + 8:
            assert "wider than its" in caplog.text, (n, p.text)


# ---------------------------------------------------------------------------------------------- review 8: routes
def test_the_route_globe_holds_while_the_first_leg_draws(tmp_path, monkeypatch, fake_geo) -> None:  # noqa: F811
    seen: list[float] = []
    real = graphics._draw_globe

    def spy(im, globe, box, t, out_at=None):
        if out_at is not None:
            seen.append(out_at)
        return real(im, globe, box, t, out_at)
    monkeypatch.setattr(graphics, "_draw_globe", spy)
    stops = [{"place": "Istanbul", "country": "Turkey", "label": "Constantinople"},
             {"place": "Ankara", "country": "Turkey", "label": "Ancyra"},
             {"place": "Derinkuyu", "country": "Turkey", "label": "Derinkuyu"}]
    _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4"))
    assert seen and min(seen) > 0.6 + 0.2                              # past the first leg's start (0.6 s)
    assert max(seen) <= 0.6 + graphics.ROUTE_GLOBE_HOLD + 1e-6
