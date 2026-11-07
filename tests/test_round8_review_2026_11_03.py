"""Round 8 (2026-11-03): the round-7 review's fixes. The island zoom stops where its outline's detail holds and the
pin stands on its island; a small island country is named; an outside name is nearer its own country than any
other; the scale bar keeps off land borders and clear of the marker; neighbours are named where their land is a
narrow strip; polar views wrap across the antimeridian and the pole; enclaves are their own country's ground; the
16:9 labels lose a widow; the contrast measure's files are its own."""
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import geo, graphics
from tests.test_r4_graphics_2026_11_02 import _render

needs_geo = pytest.mark.skipif(not geo.available() or not geo.RELIEF_FILE.exists(),
                               reason="Natural Earth data and relief not downloaded")
SIZES = [(1080, 1920), (1920, 1080)]


def E(y: int, l: str, a: bool = False) -> dict:
    return {"year": y, "label": l, "approximate": a}


def _box(name: str, x0: float, y0: float, x1: float, y1: float, holes: list | None = None) -> dict:
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {"names": {name.lower()}, "polygons": [[ring, *(holes or [])]], "display": name, "label_at": None,
            "label_rank": 2, "type": "Sovereign country", "extent": (x0, y0, x1, y1), "size": 4}


# ---------------------------------------------------------------------------------------------- review 1: islands
@needs_geo
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_nan_madol_is_framed_at_its_outline_detail_with_the_pin_on_pohnpei(size) -> None:
    home = geo.find_country("Micronesia")
    lat, lon = 6.8441, 158.3350
    span0 = graphics.locator_span(lat, lon, home, *size)
    span = graphics.island_span(lat, lon, home, size[0], size[1], span0)
    assert span is not None and span < span0
    ring = graphics._site_island(lat, lon, home)[1]
    coslat = math.cos(math.radians(lat))
    edges = [math.hypot((b[0] - a[0]) * coslat, b[1] - a[1]) for a, b in zip(ring, ring[1:] + ring[:1])]
    assert sum(edges) / len(edges) * size[0] / span <= graphics.ISLAND_VERTEX_PX_RAW + 0.5   # no crude heptagon
    slat, slon = graphics.island_site(lat, lon, home, size[0], size[1], span)
    assert geo.point_in_polygons(slon, slat, [[ring]])                  # the marker stands on the island
    assert math.hypot((slon - lon) * coslat, slat - lat) * 111.2 <= graphics.ISLAND_SNAP_KM


def test_a_site_far_off_its_island_is_not_moved() -> None:
    home = _box("Isle", 0.0, 0.0, 0.1, 0.1)
    assert graphics.island_site(0.05, 0.5, home, 1080, 1920, 2.0) == (0.05, 0.5)   # 44 km off: stays
    assert graphics.island_site(0.05, 0.05, home, 1080, 1920, 2.0) == (0.05, 0.05)  # on it already


# ---------------------------------------------------------------------------------------------- review 2, 3: R5
def test_a_small_island_country_is_named_by_its_marker() -> None:
    w, h = 1080, 1920
    home = np.zeros((h, w), bool)
    home[968:976, 548:556] = True                                       # a few px of fill under the marker
    land = home.copy()
    anchor = (540.0, 960.0)
    marker = (512.0, 876.0, 568.0, 964.0)
    label = (380.0, 800.0, 700.0, 860.0)
    got = graphics.country_outside_place("MALTA", home, anchor, [marker], w, h, None, land, label)
    assert got is not None
    _xy, b = got
    assert not graphics.LayoutConstraintSolver.boxes_intersect(b, marker, graphics.MARKER_LABEL_CLEAR - 1)


def test_an_outside_name_on_the_sea_is_nearer_its_own_country_than_any_other() -> None:
    w, h = 1080, 1920
    ids = np.zeros((h // 2, w // 2), np.uint16)
    ids[560:700, 0:270] = 1                                             # home: a block below a strait
    ids[480:542, 0:540] = 2                                             # a neighbour 18 frame px... across it
    home = np.zeros((h, w), bool)
    home[1120:1400, 0:540] = True
    land = home.copy()
    land[960:1084, :] = True
    got = graphics.country_outside_place("ESTONIA", home, (540.0, 1300.0), [], w, h, None, land, None,
                                         (ids, 1))
    if got is not None:
        b = got[1]
        sub_o = ids[int(b[1] / 2):int(b[3] / 2) + 1, int(b[0] / 2):int(b[2] / 2) + 1]
        assert not (sub_o == 2).any()                                   # never over the neighbour's coast
        d_home = max(0.0, 1120 - b[3])
        d_other = max(0.0, b[1] - 1084)
        assert d_other >= d_home + graphics.COUNTRY_OUT_AIR - 2, (b, d_home, d_other)


# ---------------------------------------------------------------------------------------------- review 4: the bar
def test_land_borders_are_found_between_two_countries_only() -> None:
    ids = np.zeros((10, 20), np.uint16)
    ids[:, 2:10] = 1
    ids[:, 10:18] = 2
    edge = graphics.land_borders(ids, 40, 20)
    cols = sorted(set(np.nonzero(edge)[1].tolist()))
    assert cols and all(18 <= c <= 21 for c in cols)                   # the 1|2 border, not the coasts


def test_the_scale_bar_keeps_off_a_border_and_away_from_the_marker() -> None:
    w, h = 1080, 1920
    border = np.zeros((h, w), bool)
    border[:, 300] = True                                               # a border down the bar's bottom row
    bar = graphics.scale_bar(40.0, 100.0, "")
    sbl = graphics.place_scale_bar(bar, w, h, [], obstacles=border)
    assert not (sbl.box[0] - graphics.SCALE_COAST_AIR <= 300 <= sbl.box[2] + graphics.SCALE_COAST_AIR)
    marker = (60.0, 960.0, 600.0, 1060.0)
    keep = (marker[0] - graphics.MARKER_SCALE_AIR, marker[1] - graphics.MARKER_SCALE_AIR,
            marker[2] + graphics.MARKER_SCALE_AIR, marker[3] + graphics.MARKER_SCALE_AIR)
    sbl = graphics.place_scale_bar(bar, w, h, [keep])
    assert not graphics.LayoutConstraintSolver.boxes_intersect(sbl.box, marker, graphics.MARKER_SCALE_AIR)


# ---------------------------------------------------------------------------------------------- review 5: neighbours
def test_a_neighbour_on_a_narrow_strip_is_named_a_step_smaller(monkeypatch) -> None:
    home, other = _box("Home", -5, -5, 0, 5), _box("Sweden", 0, -5, 5, 5)
    monkeypatch.setattr(geo, "countries", lambda: [home, other])
    tb = graphics.get_text_box(1080, 1920)

    def to_frame(lon: float, lat: float) -> tuple[float, float]:   # Sweden's strip in the box: ~140 px wide
        return tb[2] - 140 + lon * 200, 700 - lat * 100
    got = graphics.close_neighbours(1080, 1920, home, to_frame, [], set())
    assert [n.name for n in got] == ["Sweden"]
    assert got[0].px < graphics.map_name_px(1080, 1920)
    assert got[0].box[0] >= tb[2] - 140 and got[0].box[2] <= tb[2]


# ---------------------------------------------------------------------------------------------- review 6, 8: drawing
def test_view_polygons_wrap_the_antimeridian_and_mirror_past_the_pole(monkeypatch) -> None:
    ant = _box("Antarctica", -180.0, -90.0, 180.0, -65.0)
    monkeypatch.setattr(geo, "countries", lambda: [ant])
    got = geo.view_polygons(170.0, -80.0, 20.0, 15.0)
    xs = [x for _c, rings in got for x, _y in rings[0]]
    ys = [y for _c, rings in got for _x, y in rings[0]]
    assert max(xs) > 180.0                                              # shifted past the antimeridian
    assert min(ys) < -90.0                                              # mirrored past the pole


def test_an_enclave_is_drawn_after_the_hole_it_fills(monkeypatch) -> None:
    hole = [(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 3.0)]
    outer, encl = _box("Kyrgyzstan", 0, 0, 5, 5, [hole]), _box("Uzbekistan", 2.0, 2.0, 3.0, 3.0)
    monkeypatch.setattr(geo, "countries", lambda: [encl, outer])        # the enclave first in the data
    order = [c["display"] for c, _r in geo.view_polygons(2.5, 2.5, 5.0, 5.0)]
    assert order == ["Kyrgyzstan", "Uzbekistan"]
    land, home = graphics.view_masks(200, 200, 2.5, 2.5, 6.0, encl)
    assert land[100, 100] and home[100, 100]                            # gold ground, not the sea


@needs_geo
def test_the_sokh_exclave_is_uzbek_ground() -> None:
    uz = geo.find_country("Uzbekistan")
    land, home = graphics.view_masks(1080, 1920, 71.1, 39.97, 3.0, uz)  # Sokh, inside Kyrgyzstan
    assert land[960, 540] and home[960, 540]


@needs_geo
def test_the_polar_close_up_has_no_dateline_edge() -> None:
    ant = geo.find_country("Antarctica")
    land, _home = graphics.view_masks(1080, 1920, 166.64, -77.85, 18.0, ant, down=4)
    # east of 180 degrees (the frame's right) and past the pole (its foot) are land, not a cut to the sea
    assert land[-5:, :].mean() > 0.9


def test_relief_crop_wraps_and_mirrors() -> None:
    rel = Image.fromarray(np.tile(np.arange(36, dtype=np.uint8)[None, :], (18, 1)), "L")
    out = np.asarray(graphics._relief_crop(rel, (30, -2, 40, 4)))
    assert out.shape == (6, 10)
    assert out[3].tolist() == [30, 31, 32, 33, 34, 35, 0, 1, 2, 3]     # across the antimeridian
    assert out[0].tolist() == [12, 13, 14, 15, 16, 17, 18, 19, 20, 21]  # past the pole: the far side, mirrored


# ---------------------------------------------------------------------------------------------- review 7: 16:9
@pytest.mark.parametrize("events,forbidden", [
    ([E(1163, "Bishop Maurice lays the first stone"), E(1345, "The cathedral is finished"),
      E(1831, "Hugo's novel saves the building"), E(2019, "Fire brings down the spire")],
     [["BISHOP MAURICE", "LAYS THE FIRST", "STONE"], ["HUGO'S NOVEL", "SAVES", "THE BUILDING"]]),
    ([E(1450, "Pachacuti builds the royal estate", True), E(1572, "The last Inca stronghold falls"),
      E(1911, "Hiram Bingham reaches the ruins"), E(1983, "Named a World Heritage Site")],
     [["THE LAST INCA", "STRONGHOLD", "FALLS"]]),
], ids=["notre", "machu"])
def test_wide_labels_lose_their_widows(tmp_path, monkeypatch, events, forbidden) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", 1920, 1080), fps=1)
    by: dict[str, list] = {}
    for n, p in st["plates"]:
        if n.startswith("label"):
            by.setdefault(n, []).append(p)
    rows = [[p.text for p in sorted(ps, key=lambda p: p.box[1])] for ps in by.values()]
    for bad in forbidden:
        assert bad not in rows, rows
    for ps in by.values():
        ps.sort(key=lambda p: p.box[1])
        if len(ps) > 1 and len(ps[-1].text.split()) == 1:               # a one-word last row is no widow
            widths = [p.box[2] - p.box[0] for p in ps]
            assert widths[-1] >= graphics.WIDE_LONE_SHARE * max(widths[:-1]) - 2, [p.text for p in ps]


# ---------------------------------------------------------------------------------------------- shared files
def test_graded_frame_names_its_files_per_call(tmp_path, monkeypatch) -> None:
    seen: list[str] = []

    def fake_run(cmd, **_kw):
        seen.append(cmd[cmd.index("-i") + 1])
        Image.new("RGB", (4, 4)).save(cmd[-1])
    monkeypatch.setattr(graphics.subprocess, "run", fake_run)
    for _ in range(2):
        assert graphics.graded_frame(Image.new("RGB", (4, 4)), tmp_path) is not None
    assert len(set(seen)) == 2 and not list(Path(tmp_path).glob("_contrast_*.png"))
