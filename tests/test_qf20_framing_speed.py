"""QF-20 (2026-11-10): unnamed ground is still framed, polar views are framed closer for the home's name, and the
render's hot geometry paths are batched without changing a single answer."""
import json
import math
import random

import pytest
from shapely import Point

from src import geo, graphics, map_geometry
from tests import geo_truth
from tests.test_r4_graphics_2026_11_02 import _render

needs_data = pytest.mark.skipif(not (geo.available() and geo.COUNTRIES_10M_FILE.is_file() and geo.MARINE_FILE.is_file()),
                                reason="Natural Earth data not fetched")


def _ring(cx: float, cy: float, r: float, n: int, rng: random.Random, jitter: float) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(2 * math.pi * i / n) + rng.uniform(-jitter, jitter),
             cy + r * math.sin(2 * math.pi * i / n) + rng.uniform(-jitter, jitter)) for i in range(n)]


def test_point_in_polygons_answers_exactly_as_covers_on_every_probe() -> None:
    # the prepared, reused polygons give the same answer as building covers() each time, on edges and in holes
    rng = random.Random(20)
    for _ in range(200):
        cx, cy = rng.uniform(-50, 50), rng.uniform(-50, 50)
        outer = _ring(cx, cy, 20, 40, rng, 4)
        hole = _ring(cx, cy, 5, 12, rng, 1)
        polygons = [[outer, hole], [_ring(cx + 60, cy, 3, 9, rng, 0.5)]]
        probes = [(rng.uniform(cx - 30, cx + 70), rng.uniform(cy - 30, cy + 30)) for _ in range(25)]
        probes += [outer[3], hole[2], ((outer[0][0] + outer[1][0]) / 2, (outer[0][1] + outer[1][1]) / 2)]
        for x, y in probes:
            want = any(map_geometry.polygon(poly).covers(Point(x, y)) for poly in polygons)
            assert geo.point_in_polygons(x, y, polygons) == want, (x, y)


def test_point_in_polygons_never_answers_from_a_stale_list() -> None:
    polygons = [[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]]]
    assert geo.point_in_polygons(0.5, 0.5, polygons)
    polygons.append([[(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 6.0)]])      # the same list, grown
    assert geo.point_in_polygons(5.5, 5.5, polygons)


@pytest.mark.parametrize("down", [1, 8])
def test_batched_projection_equals_the_per_point_projection(down: int) -> None:
    # one PROJ call for a whole view gives the same floats as one call per point (identical frames)
    rng = random.Random(down)
    for _ in range(100):
        lon0, lat0 = rng.uniform(-180, 180), rng.uniform(-89, 89)
        proj = graphics.PolarProjection(lon0, lat0, rng.uniform(0, 2000), rng.uniform(0, 2000), rng.uniform(1, 500))
        rings = [[(lon0 + rng.uniform(-40, 40), max(-90.0, min(90.0, lat0 + rng.uniform(-30, 30))))
                  for _ in range(rng.randint(3, 40))] for _ in range(4)]
        want = [[(x / down, y / down) for x, y in (proj(lo, la) for lo, la in ring)] for ring in rings]
        assert proj.rings(rings, down) == want
        assert proj.ring(rings[0], down) == want[0]
        # the same rings again at another scale and offset (the kept PROJ coordinates of the same centre)
        closer = graphics.PolarProjection(lon0, lat0, proj.cx + 7.5, proj.cy - 3.25, proj.k * 1.05 ** 3)
        assert closer.rings(rings, down) == [[(x / down, y / down) for x, y in (closer(lo, la) for lo, la in ring)]
                                             for ring in rings]


def test_seam_edges_are_reused_only_for_the_same_ring() -> None:
    ring = [(180.0, 10.0), (180.0, 20.0), (170.0, 20.0)]
    assert geo.seam_edges_of(ring) == geo.seam_edges(list(ring))
    other = [(10.0, 10.0), (11.0, 10.0), (11.0, 11.0)]
    assert geo.seam_edges_of(other) == geo.seam_edges(list(other)) == [False, False, False]


def test_a_dot_title_over_its_own_island_takes_its_two_row_form(monkeypatch) -> None:
    # Levuka 9:16: the one-row title found no clear leader place and was set over 20 % of Ovalau; its balanced
    # two-row form at the same size clears the island and is taken (and only when it covers less)
    import numpy as np
    width, height = 1080, 1920
    part = np.zeros((height, width), bool)
    part[700:900, 300:540] = True                     # the site's island, left of the dot
    land = part.copy()
    calls = []

    def fake_layout(anchor, lines, font, w, h, obstacles=(), land_=None, box=None, style="leader",
                    keep_land_visible=None):
        calls.append(list(lines))
        lb = (320.0, 720.0, 760.0, 790.0) if len(lines) == 1 else (580.0, 760.0, 800.0, 880.0)
        return graphics.MarkerLayout("leader", anchor, (), font.size, lb, "leader", (anchor, anchor),
                                     (anchor[0] - 9, anchor[1] - 9, anchor[0] + 9, anchor[1] + 9))
    monkeypatch.setattr(graphics, "leader_layout", fake_layout)
    lay = graphics.leader_marker((540.0, 760.0), "Levuka, Ovalau", width, height, land, part)
    assert calls[0] == ["LEVUKA, OVALAU"] and len(calls[1]) == 2
    assert lay.label_box == (580.0, 760.0, 800.0, 880.0)
    assert graphics.island_cover([lay.label_box], part) <= graphics.PIN_ISLAND_COVER_MAX


@pytest.mark.parametrize("length", [15.000000000001, 9.000000000001, 24.0000000001, 1234.5678])
def test_a_dashed_neutral_border_always_finishes(length) -> None:
    # Pantheon 16:9 never finished: a segment whose length ends a hair (< 1e-8 px) past a dash boundary looped
    # forever between the dash and the gap; a ring projected far off the canvas was walked dash by dash
    import threading

    from PIL import Image, ImageDraw
    im = Image.new("RGB", (100, 100))
    pts = [(0.0, 0.0), (length, 0.0), (length, 30.0), (5e7, 5e7), (0.0, 0.0)]
    done = threading.Event()

    def run() -> None:
        graphics._dashed_border(ImageDraw.Draw(im), pts, (255, 255, 255), 1, 9, 6)
        done.set()
    threading.Thread(target=run, daemon=True).start()
    assert done.wait(20), "the dashed border did not finish"
    row = [im.getpixel((x, 0)) for x in range(9)]
    assert all(p == (255, 255, 255) for p in row)         # the first dash still starts at the first vertex


def test_a_subunit_bears_a_name_as_the_independent_check_reads_it() -> None:
    assert geo.bears_name("Serbia", {"serbia", "Republic of Serbia"})
    assert not geo.bears_name("Serbia", {"vojvodina", "Republic of Serbia"})
    assert geo.bears_name("United States", {"United States of America"})
    assert geo.bears_name("Sao Tome and Principe", {"São Tomé and Príncipe"})


def test_a_neighbours_name_on_a_part_that_does_not_carry_it_is_refused() -> None:
    # Site 137 16:9 set SERBIA on Vojvodina (its subunit's names: Vojvodina, Republic of Serbia): refused there,
    # accepted on the part that carries the name
    import numpy as np
    serbia = {"display": "Serbia", "names": {"serbia"}, "label_polygons": [[[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0),
                                                                                (0.0, 5.0)]]]}
    ids = np.ones((100, 100), np.uint16)
    table = (ids, {1: serbia})
    from_frame = lambda x, y: (x / 10.0, y / 10.0)   # frame px -> degrees; label ground is y < 50 px
    assert graphics.neighbour_name_fault("SERBIA", (20.0, 10.0, 60.0, 30.0), table, down=1, from_frame=from_frame) == ""
    fault = graphics.neighbour_name_fault("SERBIA", (20.0, 60.0, 60.0, 80.0), table, down=1, from_frame=from_frame)
    assert "does not carry its name" in fault
    assert graphics.neighbour_name_fault("SERBIA", (20.0, 60.0, 60.0, 80.0), table, down=1) == ""   # old callers


@needs_data
def test_serbia_carries_its_name_only_on_its_own_subunit() -> None:
    with geo.detail_view():
        serbia = next(c for c in geo.countries() if c.get("display") == "Serbia")
        assert serbia.get("label_polygons")
        assert geo.point_in_polygons(20.46, 44.82, serbia["label_polygons"])        # Belgrade
        assert not geo.point_in_polygons(19.83, 45.27, serbia["label_polygons"])    # Novi Sad, Vojvodina
        assert geo.point_in_polygons(19.83, 45.27, serbia["polygons"])


@needs_data
def test_a_sea_name_is_never_set_over_an_islet_the_view_leaves_out() -> None:
    # the Fiji antimeridian route 16:9 (1:50m land) set SOUTH PACIFIC OCEAN over an islet at (179.413, -17.807)
    import numpy as np
    assert geo.on_detail_land(179.413, -17.807) and not geo.on_detail_land(-150.0, -30.0)
    empty = np.zeros((100, 100), bool)
    over_islet = graphics.water_name_fault("SOUTH PACIFIC OCEAN", (10.0, 10.0, 90.0, 30.0),
                                           lambda x, y: (179.413, -17.807), empty)
    assert over_islet.startswith("on land")
    open_sea = graphics.water_name_fault("SOUTH PACIFIC OCEAN", (10.0, 10.0, 90.0, 30.0),
                                         lambda x, y: (-150.0, -30.0), empty)
    assert not open_sea.startswith("on land")


class _Framed(Exception):
    pass


@pytest.mark.parametrize("reason, framed", [("neutral or unmatched ground; no country name or highlight", True),
                                            ("", False)])
def test_unnamed_ground_is_framed_on_the_ground_that_holds_the_site(monkeypatch, tmp_path, reason, framed) -> None:
    # Stanley (the contested Falklands) was framed at 200 km, its island a speck: the ground that holds a site
    # chooses the span and the marker even where no country is named; the true open sea still has none
    ground = {"names": set(), "polygons": [], "neutral": True, "display": ""}
    seen = {}
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "ground_at", lambda lon, lat: ground)

    def style(place, home, open_sea=False):
        seen["style"] = (home, open_sea)
        return "pin"

    def span(lat, lon, home, width, height, *a, **k):
        seen["span"] = home
        raise _Framed
    monkeypatch.setattr(graphics, "locator_marker_style", style)
    monkeypatch.setattr(graphics, "locator_span", span)
    with pytest.raises(_Framed):
        graphics._render_map({"lat": -51.69, "lon": -57.86, "country": "United Kingdom", "label": "Stanley"},
                             tmp_path / "m.mp4", open_sea=True, home_entity=None, resolved=True, home_reason=reason)
    if framed:
        assert seen["span"] is ground and seen["style"] == (ground, False)
    else:
        assert seen["span"] is None and seen["style"] == (None, True)


@needs_data
def test_the_neutral_entity_holds_a_disputed_island() -> None:
    with geo.detail_view():
        got = geo.ground_at(-57.86, -51.69)              # Stanley: Natural Earth's contested Falkland Islands
        assert got is not None and got.get("neutral") and not got["display"]
        assert geo.site_home(-57.86, -51.69, hint="United Kingdom").country is None
        # QF-20 ruling Q2: South Georgia's feature records a claim, so it is neutral whatever its TYPE
        got = geo.ground_at(-36.5092, -54.2811)
        assert got is not None and got.get("neutral") and not got["display"]
        assert geo.site_home(-36.5092, -54.2811, hint="United Kingdom").country is None


@needs_data
def test_a_polar_view_is_framed_closer_to_hold_its_home_name(monkeypatch, tmp_path) -> None:
    # Bamburgh 9:16 reaches 75 degrees in its wide view (drawn polar): the closer framing that holds the home's
    # name was barred on polar views, so UNITED KINGDOM was dropped from a fill one step closer holds it beside
    place = {"lat": 55.6090, "lon": -1.7099, "country": "United Kingdom", "label": "Bamburgh Castle",
             "approximate": False}
    _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=1080, height=1920), fps=1)
    lay = json.loads((tmp_path / "m.mp4.layout.json").read_text(encoding="utf-8"))
    assert lay["country"] is not None and lay["country"]["text"] == "UNITED KINGDOM", lay.get("home_drop")
    assert not geo_truth.faults(lay), geo_truth.faults(lay)
