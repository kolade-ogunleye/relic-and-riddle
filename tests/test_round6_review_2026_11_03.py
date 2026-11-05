"""Round 6 review fixes (2026-11-03): stops kept apart, seas named wherever they are in view, neighbours named on the
close-up, the country's outside name on the sea only, short-word row ends avoided, the framing's neighbour room."""
import math

import numpy as np
import pytest
from PIL import Image

from src import brand, geo, graphics


def test_separate_stops_moves_close_dots_apart_and_keeps_far_ones() -> None:
    pts = [(100.0, 100.0), (110.0, 104.0), (400.0, 400.0)]
    got = graphics.separate_stops(pts, 40.0)
    assert math.dist(got[0], got[1]) >= 40.0 - 1e-6
    assert got[2] == pts[2]                                              # a far stop is not moved
    mid = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
    assert math.dist(mid, ((got[0][0] + got[1][0]) / 2, (got[0][1] + got[1][1]) / 2)) < 0.5   # symmetric
    same = graphics.separate_stops([(50.0, 50.0), (50.0, 50.0)], 40.0)    # coincident: still parted
    assert math.dist(*same) >= 40.0 - 1e-6
    assert graphics.separate_stops([], 40.0) == []


# round 11 (deliberate, coordinator directive 1): the water is named from Natural Earth's marine polygons, not the
# nearest anchor (sea_names and WATER_SEAS are gone); the same points, each inside its own body's polygon
@pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")
def test_sea_names_take_the_nearest_anchor_within_reach() -> None:
    assert geo.marine_at(18.0, 34.0)["name"] == "Mediterranean Sea"
    assert geo.marine_at(38.0, 20.5)["name"] == "Red Sea"
    assert geo.marine_at(0.0, -60.0)["name"] == "South Atlantic Ocean"   # the far south: its own ocean (was none)


@pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")
def test_water_labels_set_a_sea_name_where_its_water_is_open() -> None:
    w, h = 1080, 1920
    land = np.zeros((h, w), bool)
    land[:, :300] = True                                                # a coast on the left

    def from_frame(x: float, y: float) -> tuple[float, float]:          # the middle of the Mediterranean
        return 18.0 + (x - w / 2) / 100, 34.0 - (y - h / 2) / 100
    got = graphics.water_labels(w, h, from_frame, land, [])
    assert got and got[0].name == "Mediterranean Sea"
    b = got[0].box
    assert b[0] >= 300 + 8 and graphics.get_text_box(w, h)[0] <= b[0]  # wholly on the sea, inside the box
    keep = [(0, 0, w, h)]                                               # everything taken: none named
    assert graphics.water_labels(w, h, from_frame, land, keep) == []
    assert got[0].mask.size == (round(b[2] - b[0]), round(b[3] - b[1]))  # the mask is its ink


def test_country_outside_place_is_on_the_sea_never_on_other_land() -> None:
    w, h = 1080, 1920
    home = np.zeros((h, w), bool)
    home[600:1000, 300:700] = True
    land = np.zeros((h, w), bool)
    land[:, 700:] = True                                                # a neighbour east
    land |= home
    got = graphics.country_outside_place("TURKEY", home, (500.0, 800.0), [], w, h, land=land)
    assert got is not None
    (_x, _y), b = got
    x0, y0, x1, y1 = (int(v) for v in b)
    assert not land[max(0, y0 - 12):y1 + 12, max(0, x0 - 12):x1 + 12].any()   # COUNTRY_OUT_AIR off any land
    # FM1: no fill at all (an island too small to draw): placed near the anchor, not dropped
    got2 = graphics.country_outside_place("MALTA", np.zeros((h, w), bool), (540.0, 960.0), [], w, h,
                                          land=np.zeros((h, w), bool))
    assert got2 is not None and math.dist(got2[0], (540, 960)) <= graphics.COUNTRY_NEAR_PX
    # the site label's clearance is COUNTRY_LABEL_AIR, not the obstacles' 24
    label = (300.0, 500.0, 780.0, 560.0)
    got3 = graphics.country_outside_place("TURKEY", home, (500.0, 800.0), [], w, h, land=land, label=label)
    assert got3 is not None and not graphics.LayoutConstraintSolver.boxes_intersect(
        got3[1], label, graphics.COUNTRY_LABEL_AIR - 0.5)


def test_country_spot_falls_back_inside_at_a_smaller_size_where_no_sea_is() -> None:
    w, h = 1080, 1920
    tb = graphics.get_text_box(w, h)
    ring = [(tb[0] + 10, tb[1] + 10), (tb[2] - 10, tb[1] + 10), (tb[2] - 10, tb[3] - 10), (tb[0] + 10, tb[3] - 10)]
    land = np.ones((h, w), bool)                                        # all land: no sea place
    home = np.zeros((h, w), bool)
    home[int(tb[1]) + 10:int(tb[3]) - 10, int(tb[0]) + 10:int(tb[2]) - 10] = True
    spot = graphics.country_spot("TURKEY", [ring], home, land, (540.0, 960.0), (400.0, 860.0, 680.0, 920.0), [], [],
                                 w, h)
    assert spot is not None and spot.inside


def test_close_neighbours_names_a_country_on_its_own_land(monkeypatch) -> None:
    def box(name, x0, y0, x1, y1):
        ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return {"names": {name.lower()}, "polygons": [[ring]], "display": name, "label_at": None, "label_rank": 2,
                "type": "Sovereign country", "extent": (x0, y0, x1, y1)}
    home, other = box("Home", -5, -5, 0, 5), box("Neighbour", 0, -5, 5, 5)
    monkeypatch.setattr(geo, "countries", lambda: [home, other])

    def to_frame(lon: float, lat: float) -> tuple[float, float]:
        return 540 + lon * 100, 700 - lat * 100
    got = graphics.close_neighbours(1080, 1920, home, to_frame, [], set())
    assert [n.name for n in got] == ["Neighbour"]
    b = got[0].box
    assert b[0] >= 540 and b[2] <= 1040                                 # on its own land, off the home fill
    assert graphics.close_neighbours(1080, 1920, home, to_frame, [], {"NEIGHBOUR"}) == []   # named already
    plates = graphics.neighbour_plates(Image.new("RGB", (1080, 1920), (88, 92, 100)), got)
    assert plates and all(p.contrast is None or p.contrast >= brand.TEXT_CONTRAST - 0.05 for _n, p in plates)


def test_tracked_wrap_avoids_a_row_ending_on_a_short_word() -> None:
    font = graphics.get_font("Montserrat", 38, weight=800)
    rows = graphics.tracked_wrap("PACHACUTI BUILDS A ROYAL ESTATE", font, 420, 2)
    assert rows is not None and " ".join(rows) == "PACHACUTI BUILDS A ROYAL ESTATE"
    assert all(r.split()[-1] not in graphics.TALL_STOP_WORDS for r in rows[:-1]), rows


def test_scale_bar_never_repeats_its_number_and_keeps_the_primary_in_range() -> None:
    for d in np.linspace(20, 400, 60):
        bar = graphics.scale_bar(39.0, float(d), "")
        if bar.secondary is not None:
            assert bar.secondary.value != bar.primary.value
        assert bar.length <= graphics.SCALE_RANGE_TALL[1] + 0.5
    wide = graphics.scale_bar(16.8, 241 / 250 * graphics._km_per_deg(16.8), "44 metres",
                              bar_range=graphics.SCALE_RANGE_WIDE)
    assert wide.length <= 280 + 0.5                                       # the 16:9 ceiling is 280, not 260
    # round 7 (deliberate, coordinator R2: 1-2-5 only, 250 KM is no longer a length): 200 KM at 224 px, the
    # 280 px ceiling still holding (280 px for 250 KM before)
    assert graphics.scale_bar(16.8, 280 / 250 * graphics._km_per_deg(16.8), "44 metres",
                              bar_range=graphics.SCALE_RANGE_WIDE).px == pytest.approx(224, abs=1)
    assert graphics.scale_bar(16.8, 280 / 200 * graphics._km_per_deg(16.8), "44 metres",
                              bar_range=graphics.SCALE_RANGE_WIDE).px == pytest.approx(280, abs=1)


@pytest.mark.skipif(not geo.available(), reason="Natural Earth data not downloaded")
def test_locator_span_leaves_room_for_the_neighbours_and_frames_small_islands() -> None:
    turkey = geo.find_country("Turkey")
    span = graphics.locator_span(38.37, 34.73, turkey, 1080, 1920)
    land, home = graphics.view_masks(1080, 1920, 34.73, 38.37, span, turkey, down=8)
    box = graphics.get_text_box(1080, 1920)
    assert graphics.home_share(land & ~home, box, 8) >= graphics.LOCATOR_NEIGHBOUR_MIN
    malta = geo.find_country("Malta")
    assert graphics.locator_span(35.87, 14.507, malta, 1080, 1920) <= 40.0 / graphics.LOCATOR_MAX_ZOOM + 0.5
