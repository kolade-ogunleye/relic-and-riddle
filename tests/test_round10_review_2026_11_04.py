"""Round 10 (2026-11-04): the round-9 review's fixes. A small island by other land is framed as an island (the globe
its inset), and the widest span no longer wins a fill too small for the framing rule; the pin's label keeps off the
island; a coarse island of few points keeps its corners, cut, not curved; the 9:16 pin stands higher in the box, so
the home's ground south of it holds its name; a name with no place in any form ends in its shorter forms; an
outside home name keeps every neighbour's name off; the close-up tries its stepped-down names on a fine grid and
the far names give way to the adjacent ones; the view past a pole fades out; a break after a whole spelled count
weighs less than one inside it; the 9:16 spans keep their separation before the years their steps."""
import math

import numpy as np
import pytest

from src import geo, graphics

needs_geo = pytest.mark.skipif(not geo.available(), reason="Natural Earth data not downloaded")
SIZES = [(1080, 1920), (1920, 1080)]


# ---------------------------------------------------------------------------------------------- R5: shorter forms
def test_a_long_name_has_its_shorter_forms() -> None:
    usa = {"NAME": "United States of America", "NAME_LONG": "United States", "ABBREV": "U.S.A."}
    assert geo.short_names(usa) == ["United States", "USA"]
    assert geo.short_names({"NAME": "France", "NAME_LONG": "France", "ABBREV": "Fr."}) == []
    assert geo.short_names({"NAME": "Antigua and Barb.", "NAME_LONG": "Antigua and Barbuda", "ABBREV": "Ant.B."}) == []


@needs_geo
def test_the_countries_carry_their_shorter_forms() -> None:
    # round 15 (coordinator directive 1, deliberate): no initialism is a form of a name ("USA" is gone)
    assert geo.find_country("United States of America")["short_names"] == ["United States"]


# ---------------------------------------------------------------------------------------------- R6: islands
def test_a_coarse_ring_of_few_points_keeps_its_corners_cut() -> None:
    # round 15 (coordinator directive 3, deliberate): no outline is smoothed or cut any more; a coarse island is
    # drawn as its own polygon and framed no closer than its points hold (geo.chaikin_ring is gone)
    assert not hasattr(geo, "chaikin_ring")


def test_an_island_of_under_eight_points_is_cut_not_curved() -> None:
    # round 15 (directive 3, deliberate): neither cut nor curved: view_polygons gives the ring as it is
    assert not hasattr(geo, "smoothed_islands") and not hasattr(geo, "catmull_rom_ring")


@needs_geo
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
@pytest.mark.parametrize("site", [("Barbados", 13.3070, -59.6110), ("Maldives", 4.1755, 73.5093)],
                         ids=["barbados", "male"])
def test_a_small_island_by_other_land_is_framed_where_it_reads(size, site) -> None:
    name, lat, lon = site
    # round 15 (coordinator directives 1 and 3, deliberate): the close-up is drawn from the 1:10m entities, and no
    # outline is framed closer than its own vertices hold; the 1:50m outlines (7 points for Male) no longer stand in
    with geo.detail_view():
        home = geo.site_home(lon, lat, hint=name).country
        span0 = graphics.locator_span(lat, lon, home, *size)
        assert span0 < 39.0                                        # never the widest span (Barbados: 39.3)
        span = graphics.island_span(lat, lon, home, size[0], size[1], span0)
        assert span is not None and span < span0
        centre = graphics.view_centre_lat(lat, size[0], size[1], span)[0]
        _land, hm = graphics.view_masks(size[0], size[1], lon, centre, span, home, down=1)
        ring = graphics._site_island(lat, lon, home)[1]
    ys, xs = np.nonzero(hm)
    k = min(size) / 1080
    # (deliberate, round 15: or framed as close as its own outline's points hold, directive 3)
    c = math.cos(math.radians(lat))
    edges = [math.hypot((q[0] - p[0]) * c, q[1] - p[1]) for p, q in zip(ring, ring[1:] + ring[:1])]
    gate = sum(edges) / len(edges) * size[0] / span >= graphics.ISLAND_VERTEX_PX_RAW * k - 1
    assert max(xs.max() - xs.min(), ys.max() - ys.min()) >= 3 * graphics.PIN_HEAD_D * k or gate   # the pin hides little


@needs_geo
def test_a_large_country_by_its_small_island_is_not_framed_as_one() -> None:
    home = geo.find_country("Ireland")
    lat, lon = 51.7713, -10.5390                               # Skellig Michael: a rock off Ireland, which fills the box
    span0 = graphics.locator_span(lat, lon, home, 1080, 1920)
    assert graphics.island_span(lat, lon, home, 1080, 1920, span0) is None


def test_the_pin_label_slides_off_the_home_fill(caplog) -> None:
    # round 11 (deliberate, coordinator directive 4): the move is ruled by the share of the ISLAND the pin and its
    # name cover (graphics.island_cover), not the share of the label's box on the fill, and may set the name
    # beside the pin's head; the island here is Pohnpei-sized (a block west of the pin and over it)
    w, h = 1080, 1920
    anchor = (540.0, 760.0)
    plain = graphics.pin_layout(anchor, "Nan Madol", w, h)
    home = np.zeros((h, w), bool)
    lb = plain.label_box
    home[int(lb[1]) - 40:int(anchor[1]) + 40, int(anchor[0]) - 260:int(anchor[0]) + 10] = True
    before = graphics.island_cover([lb, plain.marker_box], home)
    with caplog.at_level("INFO", logger=graphics.log.name):
        moved = graphics.pin_layout(anchor, "Nan Madol", w, h, home=home)
    assert graphics.island_cover([moved.label_box, moved.marker_box], home) < before
    assert "of the island" in caplog.text
    assert graphics.pin_layout(anchor, "Nan Madol", w, h, home=np.zeros((h, w), bool)) == plain


# ---------------------------------------------------------------------------------------------- the pin's height
def test_the_tall_pin_stands_in_the_box_not_at_its_foot() -> None:
    w, h = 1080, 1920
    tb = graphics.get_text_box(w, h)
    span = 8.0
    centre, lift = graphics.view_centre_lat(47.55, w, h, span)
    assert h / 2 - lift == pytest.approx(tb[1] + graphics.MAP_PIN_Y_SHARE * (tb[3] - tb[1]))
    assert centre < 47.55 and lift == pytest.approx((47.55 - centre) * w / span)
    assert graphics.view_centre_lat(47.55, 1920, 1080, span) == (47.55, 0.0)             # 16:9: the centre
    assert graphics.view_centre_lat(47.55, w, h, span, area=True) == (47.55, 0.0)        # a region's area
    assert graphics.view_centre_lat(-89.9, w, h, 40.0)[0] >= -89.5                       # never past the pole


# ---------------------------------------------------------------------------------------------- neighbours
@needs_geo
def test_slovakia_finds_its_room_east_of_pannonhalmas_name() -> None:
    w, h, span = 1080, 1920, 8.0
    lat, lon = 47.5537, 17.7557
    home = geo.find_country("Hungary")
    centre, lift = graphics.view_centre_lat(lat, w, h, span)
    coslat = max(0.2, math.cos(math.radians(centre)))
    kf = w / span

    def to_frame(lo: float, la: float) -> tuple[float, float]:
        return w / 2 + (lo - lon) * kf * coslat, h / 2 - (la - centre) * kf
    lay = graphics.pin_layout((w / 2, h / 2 - lift), "Pannonhalma Archabbey", w, h)
    got = graphics.close_neighbours(w, h, home, to_frame, [lay.label_box, lay.marker_box], {"AUSTRIA"}, 1)
    assert [n.name for n in got] == ["Slovakia"]


def test_the_scale_bar_keeps_the_names_further_off() -> None:
    assert graphics.SCALE_NAME_AIR > graphics.MARKER_LABEL_CLEAR + 4
    assert graphics.COUNTRY_OUT_NB_AIR >= 3 * graphics.MAP_NAME_PX


# ---------------------------------------------------------------------------------------------- the poles
def test_the_view_past_the_south_pole_fades_to_the_sea() -> None:
    from PIL import Image
    base = Image.new("RGB", (40, 200), (200, 200, 200))
    kx = 2.0                                                    # px per degree; the base centred on -60

    def project(lon: float, lat: float) -> tuple[float, float]:
        return 20 + lon * kx, 100 - (lat + 60) * kx
    out = np.asarray(graphics.pole_fade(base, project, 0.0))
    y_pole = round(project(0, -90)[1])                          # 160
    assert (out[:y_pole] == 200).all()                          # the real ground to the pole, as it was
    assert (out[y_pole + round(graphics.POLE_FADE_DEG * kx) + 1:] == np.array(graphics.SEA)).all()
    assert graphics.pole_fade(base, lambda lo, la: (0.0, 100 - 2 * la), 0.0) is base    # no pole in view


# ---------------------------------------------------------------------------------------------- timeline breaks
def test_a_break_after_a_whole_spelled_count_is_the_lighter_fault() -> None:
    words = ["TWENTY", "THOUSAND", "WORKERS", "FINISH", "IT"]
    assert graphics._soft_count_breaks(words) == {2}
    assert {1, 2} <= graphics._held_breaks(words, "Twenty thousand workers finish it")
    assert graphics._soft_count_breaks(["A", "HUNDRED", "DAYS"]) == set()     # a single count stays held
