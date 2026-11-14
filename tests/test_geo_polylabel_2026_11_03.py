"""Round 6 (2026-11-03): geo.polylabel, the pole of inaccessibility the locator sets the home country's name at, and
geo.clip_ring, the frame clip it is computed on."""
import math

import pytest

from src import geo, graphics

needs_geo = pytest.mark.skipif(not geo.available(), reason="Natural Earth data not downloaded")


def test_square_pole_is_its_centre() -> None:
    x, y, r = geo.polylabel([[(0, 0), (100, 0), (100, 100), (0, 100)]], precision=1.0)
    assert geo.point_in_polygons(x, y, [[[(0, 0), (100, 0), (100, 100), (0, 100)]]])
    assert r == pytest.approx(50, abs=1.0) and math.dist((x, y), (50, 50)) <= 1.5


def test_l_shape_pole_sits_in_its_corner_square() -> None:
    ring = [(0, 0), (100, 0), (100, 30), (30, 30), (30, 100), (0, 100)]
    x, y, r = geo.polylabel([ring], precision=1.0)
    analytic = 30 * math.sqrt(2) / (1 + math.sqrt(2))     # the circle in the corner touching the inner corner
    assert geo.point_in_polygons(x, y, [[ring]]) and r == pytest.approx(analytic, abs=1.0)


def test_a_hole_is_kept_out_of() -> None:
    outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
    hole = [(40, 40), (60, 40), (60, 60), (40, 60)]
    x, y, r = geo.polylabel([outer, hole], precision=1.0)
    assert geo.point_in_polygons(x, y, [[outer, hole]])
    assert r == pytest.approx(40 * math.sqrt(2) / (1 + math.sqrt(2)), abs=1.0)


def test_avoid_regions_count_as_edges() -> None:
    sq = [(0, 0), (200, 0), (200, 100), (0, 100)]
    x, _y, r = geo.polylabel([sq], avoid_circles=[(100, 50, 30)])
    assert math.dist((x, _y), (100, 50)) - 30 >= r - 1
    x2, _y2, _r2 = geo.polylabel([sq], avoid=[(60, 0, 200, 100)])
    assert x2 < 60


@pytest.mark.parametrize("rings", [[], [[]], [[(0, 0), (1, 1)]], [[(0, 0), (1, 1), (2, 2)]], [[(5, 5)] * 4]])
def test_empty_or_degenerate_rings_give_none(rings) -> None:
    assert geo.polylabel(rings) is None


def test_no_fit_falls_back_outside(monkeypatch) -> None:
    # a sliver too thin for the name: country_name_place says no, and the caller sets it outside
    sliver = [[(100.0, 500.0), (900.0, 500.0), (900.0, 512.0), (100.0, 512.0)]]
    assert graphics.country_name_place("TURKEY", sliver, (540, 960), (300, 700, 780, 860), [], 1080, 1920) is None
    assert graphics.country_name_place("TURKEY", [], (540, 960), (300, 700, 780, 860), [], 1080, 1920) is None


def test_clip_ring_keeps_only_the_inside() -> None:
    cut = geo.clip_ring([(-10, -10), (50, -10), (50, 50), (-10, 50)], (0, 0, 20, 20))
    assert geo.ring_area(cut) == pytest.approx(400)
    assert geo.clip_ring([(100, 100), (120, 100), (120, 120)], (0, 0, 20, 20)) == []


@needs_geo
def test_frame_clipped_india_pole_is_inside_the_clip() -> None:
    india = geo.find_country("India")
    clip = (65.0, 330.0, 888.0, 1058.0)
    kf, coslat = 1080 / 12.0, math.cos(math.radians(16.8))

    def to_frame(lon, lat):
        return 540 + (lon - 75.7) * kf * coslat, 960 - (lat - 16.8) * kf
    rings = graphics.home_rings(india, to_frame, clip)
    x, y, r = geo.polylabel(rings, precision=2.0)
    assert clip[0] <= x <= clip[2] and clip[1] <= y <= clip[3] and r > 0
    assert geo.point_in_polygons(x, y, [[ring] for ring in rings])
