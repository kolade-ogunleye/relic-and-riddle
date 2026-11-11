"""Map boundaries and projections must reflect geometry rather than bounding boxes."""

import math

import pytest

from src import geo, graphics


def test_island_leader_title_keeps_neighbouring_islets_visible():
    import numpy as np

    width, height = 1920, 1080
    part = np.zeros((height, width), dtype=bool)
    part[330:566, 910:1050] = True
    islet = np.zeros_like(part)
    islet[640:700, 910:980] = True
    land = part | islet
    label = "Kingstown, St Vincent"
    layout = graphics.leader_marker((960, 540), label, width, height, land, part)
    x0, y0, x1, y1 = map(int, layout.label_box)
    assert not land[y0:y1, x0:x1].any(), layout
    assert " ".join(row[0] for row in layout.rows) == label.upper()
    assert layout.font_px == graphics.marker_label_lines(label, width, height)[1].size
    assert graphics.MAP_LEADER_MIN <= math.dist(*layout.leader) <= graphics.MAP_LEADER_MAX
    left, top, right, bottom = graphics.get_text_box(width, height)
    assert left <= x0 < x1 <= right and top <= y0 < y1 <= bottom


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_narrow_neighbour_keeps_its_complete_name_without_lowering_the_font_floor(monkeypatch, size):
    from PIL import Image, ImageDraw

    width, height = size
    floor = graphics.map_name_px(width, height) - max(graphics.NEIGHBOUR_PX_STEPS)
    font = graphics.get_font("Montserrat", floor, weight=graphics.MAP_NAME_WEIGHT)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    compact = probe.textbbox((0, 0), "GAMBIA", font=font, anchor="mm")
    spaced = probe.textbbox((0, 0), "G A M B I A", font=font, anchor="mm")
    land_width = compact[2] - compact[0] + 20
    assert land_width < spaced[2] - spaced[0] + 12
    x0, y0 = 600, 600
    x1, y1 = x0 + land_width, y0 + compact[3] - compact[1] + 24
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    country = {"display": "Gambia", "names": {"gambia"}, "label_rank": 2,
               "type": "Sovereign country", "polygons": [[ring]]}
    monkeypatch.setattr(graphics, "_view_polygons", lambda *_args: [(country, [ring])])
    names = graphics.close_neighbours(width, height, None, lambda x, y: (x, y), [], set())
    assert len(names) == 1 and names[0].name == "Gambia"
    assert names[0].rows == ("GAMBIA",) and names[0].px >= floor
    left, top, right, bottom = names[0].box
    assert x0 + 6 <= left < right <= x1 - 6
    assert y0 + 6 <= top < bottom <= y1 - 6
    # The fallback still obeys the existing clearance and no-duplicate rules.
    assert graphics.close_neighbours(width, height, None, lambda x, y: (x, y),
                                     [(x0, y0, x1, y1)], set()) == []
    assert graphics.close_neighbours(width, height, None, lambda x, y: (x, y),
                                     [], {"GAMBIA"}) == []


def test_island_inside_mainland_bbox_is_selected_by_its_containing_ring():
    mainland = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 10), (0, 10)]
    island = [(5, 5), (6, 5), (6, 6), (5, 6)]
    home = {"polygons": [[mainland], [island]]}
    distance, selected = graphics._site_island(5.5, 5.5, home)
    assert distance == 0 and selected == island


def test_offshore_island_choice_uses_distance_to_edges_not_bbox():
    mainland = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 10), (0, 10)]
    island = [(5, 5), (6, 5), (6, 6), (5, 6)]
    distance, selected = graphics._site_island(6.1, 5.5, {"polygons": [[mainland], [island]]})
    assert distance > 0 and selected == island


def test_clipping_preserves_separate_arms_without_false_bridge():
    horseshoe = [(0, 0), (8, 0), (8, 8), (6, 8), (6, 2), (2, 2), (2, 8), (0, 8)]
    pieces = geo.clip_rings(horseshoe, (-1, 4, 9, 7))
    assert len(pieces) == 2
    assert sum(geo.ring_area(piece) for piece in pieces) == pytest.approx(12)
    assert not geo.point_in_polygons(4, 5, [[piece] for piece in pieces])


def test_country_tolerance_uses_actual_boundary_distance():
    ground = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert not geo.in_country(10.1, 5, {"polygons": [[ground]]}, tolerance_deg=0)
    assert geo.in_country(10.1, 5, {"polygons": [[ground]]}, tolerance_deg=0.11)


def test_union_removes_internal_shelf_boundary(monkeypatch):
    land = [(0, 0), (5, 0), (5, 5), (0, 5)]
    shelf = [(4, 0), (8, 0), (8, 5), (4, 5)]
    ant = {"names": ["antarctica"], "polygons": [[land]], "size": 4, "extent": (0, 0, 5, 5)}
    monkeypatch.setattr(geo, "ice_shelf_polygons", lambda: [[shelf]])
    geo._add_ice_shelves([ant])
    assert len(ant["polygons"]) == 1
    assert geo.ring_area(ant["polygons"][0][0]) == pytest.approx(40)
    assert geo.point_in_polygons(6, 3, ant["polygons"])


def test_shelf_name_ground_keeps_detail_but_avoids_disputed_resolution_edges(monkeypatch):
    land = [(0, 0), (5, 0), (5, 5), (0, 5)]
    coarse = [(4, 0), (7, 0), (7, 5), (4, 5)]
    detail = [(4, 0), (8, 0), (8, 5), (4, 5)]
    ant = {"names": ["antarctica"], "polygons": [[land]], "size": 4, "extent": (0, 0, 5, 5)}
    monkeypatch.setattr(geo, "ice_shelf_polygons", lambda **_kwargs: [[coarse]])
    monkeypatch.setattr(geo.map_layers, "polygons", lambda _kind: [[detail]])
    geo._add_ice_shelves([ant], detail=True)
    assert geo.point_in_polygons(7.5, 3, ant["polygons"])  # detailed coast remains drawn
    assert not geo.point_in_polygons(7.5, 3, ant["name_polygons"])
    assert geo.point_in_polygons(6, 3, ant["name_polygons"])  # supported shelf is usable
    assert geo.point_in_polygons(1, 3, ant["name_polygons"])  # original mainland stays


def test_name_ground_mask_uses_source_agreement_without_erasing_the_drawn_coast():
    import numpy as np

    land = [(0, 0), (8, 0), (8, 5), (0, 5)]
    agreed = [(0, 0), (7, 0), (7, 5), (0, 5)]
    home = {"polygons": [[land]], "name_polygons": [[agreed]]}
    mask = graphics.name_ground_mask(home, lambda x, y: (x / 10, y / 10), 90, 60)
    assert mask.dtype == np.bool_ and mask.shape == (60, 90)
    assert mask[30, 60] and not mask[30, 75]
    assert home["polygons"] == [[land]]


def test_route_has_finite_antipodal_samples_and_exact_endpoints():
    start, end = (0, 0), (180, 0)
    points = graphics.great_circle(start, end, 16)
    assert len(points) == 17 and points[0] == start and points[-1] == end
    assert all(math.isfinite(value) for point in points for value in point)
    # On WGS84 the shortest antipodal path passes a pole rather than the equator.
    assert abs(points[8][1]) > 89


@pytest.mark.parametrize("point,centre", [((179, 65), (-179, 70)), ((20, 89.9), (140, 88)),
                                          ((-75, -80), (12, -75))])
def test_azimuthal_round_trip_uses_proj_on_poles_and_antimeridian(point, centre):
    from pyproj import Geod
    projected = geo.azimuthal(*point, *centre)
    restored = geo.azimuthal_inverse(*projected, *centre)
    assert Geod(ellps="WGS84").inv(*point, *restored)[2] < 0.001
    assert math.hypot(*projected) * math.pi * 6371008.8 / 180 == pytest.approx(
        Geod(ellps="WGS84").inv(*centre, *point)[2], abs=0.001)


@pytest.fixture
def neutral_ground(monkeypatch):
    from shapely import STRtree, box

    from src import map_geometry, map_layers
    layers = {"water": (box(2, 2, 3, 3),), "neutral": (box(5, 1, 7, 9),)}
    monkeypatch.setattr(map_layers, "geometries", layers.__getitem__)
    monkeypatch.setattr(map_layers, "index", lambda kind: STRtree(layers[kind]))
    monkeypatch.setattr(map_layers, "_GROUND_CACHE", [])
    original = {"names": {"example"}, "sovereign": "example", "polygons": map_geometry.rings_of(box(0, 0, 10, 10)),
                "size": 5, "display": "Example", "label_at": None, "label_rank": 2,
                "type": "Country", "extent": (0, 0, 10, 10)}
    ground = map_layers.ground([original])
    monkeypatch.setattr(geo, "countries", lambda: ground)
    return original, ground


def test_water_and_neutral_ground_are_not_owned_or_highlighted(neutral_ground):
    original, ground = neutral_ground
    assert geo.point_in_polygons(2.5, 2.5, original["polygons"])
    assert not geo.point_in_polygons(2.5, 2.5, ground[0]["polygons"])
    assert not geo.point_in_polygons(6, 6, ground[0]["polygons"])
    assert geo.site_home(6, 6, hint="Example").country is None
    assert geo.site_home(4, 6, hint="Example").country is ground[0]
    neutral = next(c for c in ground if c.get("neutral"))
    assert not neutral["names"] and not neutral["display"] and not neutral["sovereign"]


def test_missing_enclave_does_not_name_the_wrong_country(neutral_ground):
    assert geo.site_home(4, 6, hint="Missing enclave").country is None


def test_actual_map_base_draws_neutral_fill_dashes_and_lake(neutral_ground, monkeypatch):
    import numpy as np
    _, ground = neutral_ground
    monkeypatch.setattr(graphics, "_relief", lambda: None)
    image = graphics.relief_base(5, 5, 300, 300, 30, 1, ground[0],
                                 lambda lon, lat: (lon * 30, 300 - lat * 30), (0, 0, 300, 300))
    pixels = np.asarray(image)
    assert tuple(pixels[120, 180]) == (57, 61, 64)
    assert pixels[225, 75].max() < 40  # lake is water, not highlighted land
    edge = pixels[40:260, 150]
    assert ((edge == (154, 160, 164)).all(axis=1)).sum() > 50
    assert ((edge == (57, 61, 64)).all(axis=1)).sum() > 30  # real gaps, no solid underlying claim border


def test_committed_layers_are_hash_verified_and_neutral_sites_suppress_home():
    from src import map_layers
    assert len(map_layers.geometries("water")) >= 1355
    assert len(map_layers.geometries("ice")) >= 159
    assert map_layers.neutral_at(34.1, 44.95)  # inside the retained Natural Earth disputed polygon
    assert map_layers.neutral_at(-13.2, 27.15)


def test_coast_stroke_does_not_erase_a_thin_spit(monkeypatch):
    import numpy as np
    ring = [(1, 1), (6, 1), (6, 4.98), (9, 4.98), (9, 5.02), (6, 5.02), (6, 9), (1, 9)]
    home = {"names": {"example"}, "sovereign": "example", "polygons": [[ring]],
            "display": "Example", "size": 8, "label_at": None, "label_rank": 2,
            "extent": (1, 1, 9, 9)}
    monkeypatch.setattr(geo, "countries", lambda: [home])
    monkeypatch.setattr(graphics, "_relief", lambda: None)
    image = graphics.relief_base(5, 5, 300, 300, 30, 1, home,
                                 lambda lon, lat: (lon * 30, 300 - lat * 30), (0, 0, 300, 300))
    pixels = np.asarray(image)
    assert pixels[150, 240, 0] > 80  # the actual narrow ground remains visibly gold
    assert pixels[147, 240].max() < 40  # nearby sea is not expanded into invented land


def test_known_enclave_inside_host_hole_keeps_its_actual_identity(monkeypatch):
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    inner = [(4, 4), (6, 4), (6, 6), (4, 6)]
    host = {"names": {"host"}, "sovereign": "host", "polygons": [[outer, inner]]}
    enclave = {"names": {"enclave"}, "sovereign": "enclave", "polygons": [[inner]]}
    monkeypatch.setattr(geo, "countries", lambda: [host, enclave])
    assert geo.site_home(5, 5, hint="Host").country is enclave


def test_neutral_styling_does_not_delete_stored_site_coordinates(neutral_ground, monkeypatch):
    original, _ground = neutral_ground
    # A wholly neutral source country has no remaining named drawing geometry.
    monkeypatch.setattr(geo, "countries", lambda: [_ground[-1]])
    monkeypatch.setattr(geo, "countries_50m", lambda: [original])
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "_load_cache", dict)
    monkeypatch.setattr(geo, "_wikidata_point", lambda *_args: pytest.fail("stored coordinate must stay offline"))
    got = geo.locate({"location": {"name": "Example site", "country": "Example", "longitude": 6, "latitude": 6}})
    assert got is not None and (got["lon"], got["lat"]) == (6, 6)
    assert geo.site_home(6, 6, hint="Example").country is None


def test_offshore_neutral_ground_cannot_fall_back_to_a_claimant(neutral_ground, monkeypatch):
    _original, ground = neutral_ground
    monkeypatch.setattr(geo, "countries", lambda: [ground[-1]])
    found = geo.site_home(6, 9.01, hint="Example")
    assert found.country is None and found.km == 0  # neutral shore, rather than an unlocated open-sea site


def test_globe_applies_neutral_layer_at_its_own_scale(neutral_ground, monkeypatch):
    from src import map_layers
    original, ground = neutral_ground
    monkeypatch.setattr(geo, "countries_50m", lambda: [original])
    monkeypatch.setattr(geo, "_DETAIL", [True])
    monkeypatch.setattr(map_layers, "ground", lambda _countries: ground)
    projected = geo.globe_rings(5, 5, step=1)
    assert any(c.get("neutral") and rings for c, rings in projected)


def test_five_event_history_keeps_close_adjacent_dates():
    years = [1300, 1522, 1523, 1912, 1988]
    events = [{"year": year, "label": f"Event {year}"} for year in years]
    assert [event["year"] for event in graphics.timeline_events(events)] == years


@pytest.mark.parametrize("case", ["nazca", "amber"])
def test_wide_timeline_preserves_names_without_unnecessary_third_rows(monkeypatch, caplog, tmp_path, case):
    from test_r4_graphics_2026_11_02 import _render_checked

    samples = {
        "nazca": [(-500, "Geoglyph creation begins"), (500, "Geoglyph creation ends"),
                  (1994, "Designated UNESCO World Heritage")],
        "amber": [(1941, "Looted by German soldiers"), (1944, "Documentation ends at Königsberg"),
                  (1997, "Original stone mosaic recovered"), (2003, "Catherine Palace reconstruction")],
    }
    events = [{"year": year, "label": label, "approximate": case == "nazca" and year < 1994}
              for year, label in samples[case]]
    rendered = _render_checked(monkeypatch, caplog, tmp_path, events, (1920, 1080))
    for index, event in enumerate(events):
        rows = [plate.text for name, plate in rendered["plates"] if name == f"label{index}"]
        assert " ".join(rows) == event["label"].upper()
        assert not graphics.label_hard_fault(rows, event["label"])
        assert len(rows) <= 2


def test_sentence_verb_is_not_bound_into_the_following_proper_name():
    assert not graphics.label_hard_fault(["DESIGNATED", "UNESCO WORLD HERITAGE"],
                                        "Designated UNESCO World Heritage")
    assert graphics.label_hard_fault(["DESIGNATED UNESCO WORLD", "HERITAGE"],
                                    "Designated UNESCO World Heritage")
    assert graphics.label_hard_fault(["CATHERINE", "PALACE RECONSTRUCTION"], "Catherine Palace reconstruction")
    assert graphics.label_hard_fault(["UNITED", "KINGDOM JOINS"], "United Kingdom joins")


@pytest.mark.parametrize("unsafe_size", [(1080, 1920), (1920, 1080), None])
def test_locator_marker_policy_checks_both_formats(monkeypatch, unsafe_size):
    import numpy as np

    seen = set()
    monkeypatch.setattr(graphics, "MAP_MARKER_STYLE", "pin")
    monkeypatch.setattr(graphics, "locator_span", lambda *_a: 8)
    monkeypatch.setattr(graphics, "island_span", lambda *_a: None)
    monkeypatch.setattr(graphics, "view_masks", lambda w, h, *_a, **_k:
                        (np.ones((h, w), dtype=bool), np.ones((h, w), dtype=bool)))
    monkeypatch.setattr(graphics, "site_part", lambda home, *_a: home)
    monkeypatch.setattr(graphics, "ground_word", lambda *_a: "land")

    def layout(_style, _anchor, _label, width, height, **_kwargs):
        seen.add((width, height))
        return width, height

    monkeypatch.setattr(graphics, "marker_layout", layout)
    monkeypatch.setattr(graphics, "pin_fallback", lambda layout, *_a:
                        "pin would cover the site" if layout == unsafe_size else "")
    got = graphics.locator_marker_style({"lat": 0, "lon": 0, "label": "Example"}, None)
    assert got == ("leader" if unsafe_size else "pin")
    if unsafe_size != (1080, 1920):
        assert seen == {(1080, 1920), (1920, 1080)}


def test_five_event_date_column_reserves_the_last_rows_descenders(monkeypatch, caplog, tmp_path):
    import test_r4_graphics_2026_11_02 as r4
    import test_timeline_hierarchy_2026_11_03 as hierarchy

    # This checks portrait cell geometry directly. The shared selection policy
    # correctly falls back to four when the same copy cannot fit widescreen;
    # that must not prevent exercising the five-cell portrait renderer here.
    events = hierarchy.SETS["lw/lw14"]
    rendered = r4._render_fast(monkeypatch, lambda: graphics._render_timeline_tall(
        events, tmp_path / "five.mp4", 1080, 1920, None))
    box = graphics.safe_zones.graphics_box(1080, 1920)
    assert len([name for name, _ in rendered["plates"] if name.startswith("year")]) == 5
    for index, event in enumerate(events):
        rows = [plate for name, plate in rendered["plates"] if name == f"label{index}"]
        assert " ".join(plate.text for plate in rows) == event["label"].upper()
        assert all(graphics.safe_zones.inside(tuple(plate.box), box) for plate in rows)
        assert rows[-1].xy[1] + graphics.span_descent(rows[-1].text, rows[-1].font) <= box[3]
    assert all(graphics.safe_zones.inside(tuple(plate.box), box) for _, plate in rendered["plates"])
    r4._assert_clear(rendered["plates"])


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_five_event_render_keeps_words_names_and_span_clearance(monkeypatch, caplog, tmp_path, size):
    from test_r4_graphics_2026_11_02 import _render_checked

    events = [
        {"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
        {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
        {"year": 1523, "label": "The knights sail for Malta"},
        {"year": 1912, "label": "Italy takes the Dodecanese"},
        {"year": 1988, "label": "The Medieval City of Rhodes is listed"},
    ]
    rendered = _render_checked(monkeypatch, caplog, tmp_path, events, size)
    for index, event in enumerate(events):
        rows = [plate.text for name, plate in rendered["plates"] if name == f"label{index}"]
        assert " ".join(rows) == event["label"].upper()
        assert not graphics.label_hard_fault(rows, event["label"])
    assert "HARD FAULT" not in caplog.text


def test_subpixel_shelf_gap_is_not_drawn_as_an_opaque_coast(monkeypatch):
    import numpy as np
    outer = [(1, 1), (9, 1), (9, 9), (1, 9)]
    lake = [(2, 2), (3, 2), (3, 3), (2, 3)]
    sliver = [(2, 4.99999), (8, 4.99999), (8, 5.00001), (2, 5.00001)]
    home = {"names": {"example"}, "sovereign": "example", "polygons": [[outer, lake, sliver]],
            "display": "Example", "size": 4, "label_at": None, "label_rank": 2,
            "extent": (1, 1, 9, 9)}
    monkeypatch.setattr(geo, "countries", lambda: [home])
    monkeypatch.setattr(graphics, "_relief", lambda: None)
    pixels = np.asarray(graphics.relief_base(5, 5, 300, 300, 30, 1, home,
                                            lambda lon, lat: (lon * 30, 300 - lat * 30), (0, 0, 300, 300)))
    assert pixels[150, 150, 0] > 80
    assert pixels[225, 75].max() < 40  # a resolvable lake remains water
    assert home["polygons"] == [[outer, lake, sliver]]  # source geometry is untouched


def test_tiny_home_island_is_not_invented_as_a_larger_disc(monkeypatch):
    import numpy as np
    island = [(4.98, 4.98), (5.02, 4.98), (5.02, 5.02), (4.98, 5.02)]
    home = {"names": {"example"}, "sovereign": "example", "polygons": [[island]],
            "display": "Example", "size": 4, "label_at": None, "label_rank": 2,
            "extent": (4.98, 4.98, 5.02, 5.02)}
    monkeypatch.setattr(geo, "countries", lambda: [home])
    monkeypatch.setattr(graphics, "_relief", lambda: None)
    pixels = np.asarray(graphics.relief_base(5, 5, 300, 300, 30, 1, home,
                                            lambda lon, lat: (lon * 30, 300 - lat * 30), (0, 0, 300, 300)))
    gold = pixels[..., 0] > 80
    assert 1 <= gold.sum() <= 4  # actual 1.2 px island footprint, not a six-pixel disc


def test_polar_relief_reads_bounded_tiles_without_changing_sampled_pixels(monkeypatch):
    import numpy as np
    from PIL import Image

    pixels = np.arange(2048 * 1024, dtype=np.uint8).reshape(2048, 1024)
    relief = Image.fromarray(pixels)
    projection = graphics.PolarProjection(0, 0, 16, 16, 0.25)
    expected = graphics._polar_hill(relief, projection, 32, 32)
    original_crop = Image.Image.crop
    crops = []
    limit = Image.MAX_IMAGE_PIXELS

    def bounded_crop(image, box=None):
        if image is relief:
            area = (box[2] - box[0]) * (box[3] - box[1])
            crops.append(area)
            assert area <= 1024 * 1024, "trusted relief must be read in bounded tiles"
        return original_crop(image, box)

    monkeypatch.setattr(Image.Image, "crop", bounded_crop)
    actual = graphics._polar_hill(relief, projection, 32, 32)
    assert crops
    np.testing.assert_array_equal(actual, expected)
    assert Image.MAX_IMAGE_PIXELS == limit


@pytest.mark.parametrize("kind,rank", [("reef", 4), ("bay", 9)])
def test_unlabelled_marine_regions_still_exclude_the_enclosing_sea(monkeypatch, tmp_path, kind, rank):
    import json

    def feature(name, kind, rank, corners):
        return {"type": "Feature", "properties": {"name": name, "name_en": name,
                "featurecla": kind, "scalerank": rank},
                "geometry": {"type": "Polygon", "coordinates": [corners]}}

    outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    inner = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]
    path = tmp_path / "marine.json"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        feature("Outer Sea", "sea", 1, outer), feature("Inner Region", kind, rank, inner),
    ]}), encoding="utf-8")
    monkeypatch.setattr(geo, "MARINE_FILE", path)
    geo.marine_polygons.cache_clear()
    try:
        assert [body["name"] for body in geo.marine_polygons()] == ["Outer Sea"]
        assert geo.marine_at(3, 3)["name"] == "Inner Region"
        assert graphics.water_name_fault("Outer Sea", (2.5, 2.5, 3.5, 3.5),
                                         lambda x, y: (x, y), None)
        assert geo.marine_at(8, 8)["name"] == "Outer Sea"
    finally:
        geo.marine_polygons.cache_clear()
