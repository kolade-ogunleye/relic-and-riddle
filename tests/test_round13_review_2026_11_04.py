"""Round 13 (2026-11-04): the coordinator's directives after review 12, made structural and fail closed. The name
verifier (every drawn water, home and neighbour name checked last, dropped where it fails), the marine rings
densified before any projection and the oceans split at the equator, the home named by the entity whose ground
holds the site (Ascension, never SAINT HELENA; St Kilda, UNITED KINGDOM, never SCOTLAND), the honest fallback of a
dot on a leader where the pin would float or cover the site's own ground, true land neighbours first on a route,
and 16:9 timeline labels never split a name or leave a one-word middle row. A seeded sweep of 60 sites at both
sizes runs the verifier on every name."""
import json
import logging
import math
import random
from itertools import pairwise

import numpy as np
import pytest

from src import geo, graphics
from tests import geo_truth
from tests.test_r4_graphics_2026_11_02 import _render

needs_geo = pytest.mark.skipif(not geo.available(), reason="country outlines not fetched")
needs_10m = pytest.mark.skipif(not geo.COUNTRIES_10M_FILE.is_file(), reason="1:10m map subunits not fetched")
needs_marine = pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")

P = lambda lat, lon, c, lab: {"lat": lat, "lon": lon, "country": c, "label": lab, "approximate": False}


# ---------------------------------------------------------------------------------------------- marine rings
def test_a_long_edge_is_densified_along_its_own_parallel() -> None:
    ring = [(-80.0, -60.53), (66.5, -60.53), (66.5, -70.0), (-80.0, -70.0), (-80.0, -60.53)]
    got = geo.densify_ring(ring)
    edges = list(pairwise(got))
    assert max(max(abs(b[0] - a[0]), abs(b[1] - a[1])) for a, b in edges) <= geo.MARINE_DENSIFY_DEG + 1e-9
    on_top = [p for p in got if abs(p[1] + 60.53) < 1e-9]
    assert len(on_top) >= 146                       # the 146.5-degree edge: a point every degree, on 60.53 S
    assert geo.densify_ring([(0.0, 0.0)]) == [(0.0, 0.0)]


@needs_marine
def test_no_marine_edge_is_longer_than_a_degree() -> None:
    for m in geo.marine_polygons():
        for poly in m["polygons"]:
            for ring in poly:
                for a, b in pairwise(ring):
                    assert max(abs(b[0] - a[0]), abs(b[1] - a[1])) <= geo.MARINE_DENSIFY_DEG + 1e-6, m["name"]


@needs_marine
@pytest.mark.parametrize("lon, lat, name", [
    (172.931, 1.353, "North Pacific Ocean"),        # Tarawa: north of the equator (review 12: SOUTH PACIFIC)
    (172.931, -1.0, "South Pacific Ocean"),
    (-30.0, 0.5, "North Atlantic Ocean"), (-30.0, -0.5, "South Atlantic Ocean"),
    (142.66, -62.9, "Southern Ocean"),             # Mawson 9:16 drew INDIAN OCEAN here
    (-25.0, -71.8, "Weddell Sea"),                 # Halley 16:9 drew SOUTHERN OCEAN here
    (10.5, 57.9, "Skagerrak"),                     # a "fjord" in Natural Earth: now loaded
])
def test_the_water_at_a_point_is_its_own_body(lon, lat, name) -> None:
    assert geo.marine_at(lon, lat)["name"] == name


@needs_marine
def test_the_ocean_halves_keep_to_their_hemispheres() -> None:
    by = {m["name"]: m for m in geo.marine_polygons()}
    for north, south in geo.OCEAN_HALVES:
        assert min(y for p in by[north]["polygons"] for y in (v for _x, v in p[0])) >= -1e-9
        assert max(y for p in by[south]["polygons"] for y in (v for _x, v in p[0])) <= 1e-9


@needs_marine
def test_a_water_name_is_refused_off_its_own_body_or_on_land() -> None:
    box = (100.0, 100.0, 300.0, 130.0)
    # every point of the box at Mawson's 62.9 S, 142.66 E: the Southern Ocean's water
    ff = lambda x, y: (142.0 + x / 500.0, -63.2 + y / 1000.0)
    assert "not Indian Ocean" in graphics.water_name_fault("Indian Ocean", box, ff, None)
    assert graphics.water_name_fault("Southern Ocean", box, ff, None) == ""
    land = np.zeros((400, 400), bool)
    land[120:125, 250:260] = True
    assert graphics.water_name_fault("Southern Ocean", box, ff, land).startswith("on land")


def test_the_check_points_cover_the_corners_and_the_inside() -> None:
    pts = graphics.name_check_points((0.0, 0.0, 100.0, 20.0))
    nx, ny = graphics.NAME_CHECK_GRID
    assert len(pts) == 4 + nx * ny
    assert (2.0, 2.0) in pts and (98.0, 18.0) in pts
    assert all(0 < x < 100 and 0 < y < 20 for x, y in pts)


# ---------------------------------------------------------------------------------------------- home names
@needs_geo
@needs_10m
@pytest.mark.parametrize("country, lat, lon, missing, want", [
    ("Saint Helena", -7.9286, -14.4155, False, "Ascension"),          # inside the 1:50m Saint Helena
    ("Saint Helena", -37.0675, -12.3105, True, "Tristan da Cunha"),
    ("United Kingdom", -37.0675, -12.3105, True, "Tristan da Cunha"),
    ("United Kingdom", 57.813, -8.57, True, "United Kingdom"),        # St Kilda: Scotland shares Great Britain
    ("Italy", 38.1938, 15.554, False, "Italy"),                       # Messina: Sicily lies 3 km off the mainland
    ("Italy", 39.75, 8.9, False, "Italy"),                            # Sardinia: Italy is no island territory
    # round 15 (coordinator directive 1, deliberate): no special-case table; Diego Garcia is the British Indian
    # Ocean Territory's own atoll, named by that entity's own name
    ("British Indian Ocean Territory", -7.3133, 72.4111, False, "British Indian Ocean Territory"),
])
def test_the_home_is_named_by_the_entity_that_holds_the_site(country, lat, lon, missing, want) -> None:
    # round 15 (deliberate): geo.subunit_name is gone; the entity holding the site names it (geo.site_home), its
    # country's own name where the entity is the country (None before stood for that)
    with geo.detail_view():
        got = geo.site_home(lon, lat, hint=country)
    assert got.country is not None and got.country["display"] == want


def _islands() -> tuple[np.ndarray, np.ndarray]:
    home = np.zeros((400, 600), bool)
    home[100:140, 100:300] = True          # the site's island
    home[300:330, 100:180] = True          # another, smaller island of the home
    return home, home.copy()


def test_a_home_name_inside_must_sit_on_the_sites_island_or_a_larger_one() -> None:
    home, land = _islands()
    anchor = (150.0, 120.0)
    assert graphics.home_name_fault((150, 110, 250, 130), True, home, land, anchor) == ""
    assert "smaller island" in graphics.home_name_fault((110, 305, 170, 325), True, home, land, anchor)
    assert "off the home" in graphics.home_name_fault((150, 125, 250, 150), True, home, land, anchor)


def test_an_outside_home_name_keeps_off_and_away_from_another_island() -> None:
    home, land = _islands()
    anchor = (150.0, 120.0)
    # KIRIBATI over Maiana (review 12): just above a different island of the home, far from the site's own.
    # Round 15 (coordinator directive 2, deliberate): over open water beside the home's own ground it names the
    # home, with a leader to that ground (ground_leader); only land under it, or other land nearer, is a fault
    assert graphics.home_name_fault((100, 270, 200, 290), False, home, land, anchor) == ""
    assert graphics.ground_leader((100, 270, 200, 290), home) is not None
    assert "over the home" in graphics.home_name_fault((100, 310, 200, 330), False, home, land, anchor)
    # by the site's own island, on the sea
    assert graphics.home_name_fault((120, 60, 260, 80), False, home, land, anchor) == ""


def test_a_neighbour_name_is_refused_on_a_third_country() -> None:
    ids = np.zeros((100, 200), np.uint16)
    ids[:, :100] = 1
    ids[:, 100:] = 2
    table = (ids, {1: {"display": "Ireland", "names": ["ireland"]}, 2: {"display": "United Kingdom", "names": []}})
    assert graphics.neighbour_name_fault("IRELAND", (20, 20, 90, 40), table, down=1) == ""
    assert graphics.neighbour_name_fault("IRELAND", (20, 20, 260, 40), table, down=1).startswith("over United")


# ---------------------------------------------------------------------------------------------- the marker
def _lay(anchor, label_box) -> graphics.MarkerLayout:
    return graphics.MarkerLayout("pin", anchor, (), 64, label_box, "above", None,
                                 (anchor[0] - 30, anchor[1] - 88, anchor[0] + 30, anchor[1] + 8))


def test_the_pin_gives_way_where_it_would_float_or_cover_the_site() -> None:
    land = np.zeros((600, 600), bool)
    land[300:320, 400:440] = True
    assert "off the drawn land" in graphics.pin_fallback(_lay((200.0, 310.0), (100, 150, 300, 200)), land, None)
    part = land.copy()
    assert "cover" in graphics.pin_fallback(_lay((410.0, 310.0), (300, 150, 500, 200)), land, part)
    big = np.zeros((600, 600), bool)
    big[100:600, 0:600] = True
    assert graphics.pin_fallback(_lay((300.0, 400.0), (200, 250, 400, 300)), big, None) == ""


def test_the_leader_keeps_the_name_off_a_small_country() -> None:
    land = np.ones((1920, 1080), bool)
    part = np.zeros_like(land)
    part[600:680, 500:560] = True
    lay = graphics.leader_marker((530.0, 640.0), "Vaduz Castle", 1080, 1920, land, part)
    assert lay.leader is not None
    assert graphics.island_cover([lay.label_box], part) <= graphics.PIN_ISLAND_COVER_MAX


# ---------------------------------------------------------------------------------------------- timelines
@pytest.mark.parametrize("lines, source, fault", [
    (["LORD CURZON", "RESTORES", "THE GARDENS"], "Lord Curzon restores the gardens", "a one-word middle row"),
    (["NAMED A", "WORLD", "HERITAGE SITE"], "Named a World Heritage Site", "a one-word middle row"),
    (["WILLIAM THE", "CONQUEROR BEGINS", "THE WHITE TOWER"], "William the Conqueror begins the White Tower",
     "a proper name split"),
    (["WILLIAM THE CONQUEROR", "BEGINS THE WHITE TOWER"], "William the Conqueror begins the White Tower", ""),
    (["LORD CURZON RESTORES", "THE GARDENS"], "Lord Curzon restores the gardens", ""),
    (["NAMED A", "WORLD HERITAGE SITE"], "Named a World Heritage Site", ""),
])
def test_a_label_break_is_hard_only_where_it_splits_a_name_or_strands_a_word(lines, source, fault) -> None:
    assert graphics.label_hard_fault(lines, source) == fault


E = lambda y, lab, a=False: {"year": y, "label": lab, "approximate": a}
TL = {
    "taj": [E(1631, "Mumtaz Mahal dies in childbirth"), E(1632, "Work begins on the mausoleum"),
            E(1653, "The tomb complex is finished"), E(1908, "Lord Curzon restores the gardens"),
            E(1983, "Named a World Heritage Site")],
    "tower": [E(1078, "William the Conqueror begins the White Tower", True), E(1483, "The two princes vanish inside"),
              E(1536, "Anne Boleyn is beheaded on the green"), E(1941, "The last execution takes place here")],
    "pompeii": [E(-80, "Rome makes Pompeii a colony"), E(62, "An earthquake shakes the town"),
                E(79, "Vesuvius buries the city"), E(1748, "Excavations begin"), E(1997, "Named a World Heritage Site")],
    "petra": [E(-312, "The Nabataeans hold the city", True), E(106, "Rome annexes the kingdom"),
              E(363, "An earthquake wrecks half the city"), E(1812, "Burckhardt rides in disguised"),
              E(1985, "Named a World Heritage Site")],
}


@pytest.mark.parametrize("name", sorted(TL))
def test_the_reviews_16x9_timelines_have_no_hard_break(name, tmp_path, monkeypatch, caplog) -> None:
    seen: list[list[str]] = []
    real = graphics.label_hard_fault

    def spy(lines, source=None):
        seen.append(list(lines))
        return real(lines, source)
    monkeypatch.setattr(graphics, "label_hard_fault", spy)
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        _render(monkeypatch, lambda: graphics.render_timeline(TL[name], tmp_path / "t.mp4", width=1920, height=1080),
                fps=1)
    assert "hard failure" not in caplog.text
    assert seen                                   # the layout asked the rule


def test_taj_16x9_sets_every_span_on_one_row(tmp_path, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(TL["taj"], tmp_path / "t.mp4", width=1920,
                                                                   height=1080), fps=1)
    assert "set on 2 rows" not in caplog.text
    texts = [getattr(p, "text", "") for _n, p in st["plates"]]
    assert "LATER" not in texts


# ---------------------------------------------------------------------------------------------- renders
def _map(monkeypatch, tmp_path, place, size, caplog=None) -> dict:
    w, h = size
    _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=w, height=h), fps=1)
    return json.loads((tmp_path / "m.mp4.layout.json").read_text(encoding="utf-8"))


SIZES = [(1080, 1920), (1920, 1080)]


@needs_geo
@needs_10m
@needs_marine
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_ascension_reads_ascension(size, tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(-7.9286, -14.4155, "Saint Helena", "Georgetown, Ascension"), size)
    # QF-20 (operator ruling Q1, 2026-11-11): ASCENSION repeats the title, so the home is named by its sovereign;
    # the independent checker accepts that (geo_truth.repeats_title), and SAINT HELENA is still never drawn
    assert lay["country"]["text"] == "UNITED KINGDOM"
    assert not geo_truth.faults(lay)
    assert "SAINT HELENA" not in json.dumps(lay)


@needs_geo
@needs_10m
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_st_kilda_is_never_named_scotland(size, tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(57.813, -8.57, "United Kingdom", "Village Bay, Hirta"), size)
    assert lay["country"] is None or lay["country"]["text"] == "UNITED KINGDOM"


@needs_geo
@needs_10m
@needs_marine
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_tarawa_reads_north_pacific_with_a_dot_and_no_name_by_maiana(size, tmp_path, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        lay = _map(monkeypatch, tmp_path, P(1.353, 172.931, "Kiribati", "Betio, Tarawa"), size)
    assert "South Pacific Ocean" not in lay["water"]
    assert lay["marker"] == "leader"                       # Betio is not in the data: no pin floating on the sea
    assert lay["leader"] is not None
    assert all(c["fault"] == "" for c in lay["name_checks"])


@needs_geo
@needs_10m
@pytest.mark.parametrize("site", [(47.141, 9.5215, "Liechtenstein", "Vaduz Castle"),
                                  (-9.4167, 46.4167, "Seychelles", "Aldabra Atoll")], ids=["vaduz", "aldabra"])
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_a_small_ground_is_marked_by_the_dot_where_the_pin_would_cover_it(site, size, tmp_path, monkeypatch,
                                                                          caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        lay = _map(monkeypatch, tmp_path, P(*site), size)
    if lay["marker"] == "pin":
        assert "would cover" not in caplog.text
    else:
        assert lay["marker"] == "leader" and "dot on a leader" in caplog.text
    assert "of the island" not in caplog.text or site[2] != "Liechtenstein"


@needs_geo
@needs_marine
@pytest.mark.parametrize("site, size, wrong", [
    ((-67.0089, 142.6625, "Antarctica", "Mawson's Huts"), (1080, 1920), "Indian Ocean"),
    ((-75.581, -26.6642, "Antarctica", "Halley VI Research Station"), (1920, 1080), "Southern Ocean"),
], ids=["mawson-9x16", "halley-16x9"])
def test_polar_water_names_lie_on_their_own_body(site, size, wrong, tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(*site), size)
    for c in lay["name_checks"]:
        if c["kind"] == "water" and not c["fault"]:
            assert all(geo.marine_at(lo, la)["name"].upper() == c["text"] for lo, la in c["lonlat"]), c["text"]


@needs_geo
def test_the_egypt_route_names_sudan_not_iraq(tmp_path, monkeypatch, caplog) -> None:
    coords = {"alexandria": (31.2001, 29.9187), "giza": (29.9792, 31.1342), "luxor": (25.7188, 32.6573),
              "aswan": (24.0889, 32.8998), "abu simbel": (22.3372, 31.6258)}

    def fake_locate(topic):
        n = topic["location"]["name"].lower()
        la, lo = coords[n]
        return {"lat": la, "lon": lo, "country": "Egypt", "label": topic["location"]["name"], "approximate": False,
                "qid": "x", "matched": "name"}
    monkeypatch.setattr(geo, "locate", fake_locate)
    if hasattr(graphics, "locate"):
        monkeypatch.setattr(graphics, "locate", fake_locate)
    stops = [{"place": n.title(), "country": "Egypt", "label": n.title()} for n in coords]
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=1920, height=1080),
                     fps=1)
    names = " ".join(n for n, _p in st["plates"]) + caplog.text
    assert "SUDAN" in names
    assert "neighbour_IRAQ" not in names


# ---------------------------------------------------------------------------------------------- the sweep
EDGE_SITES = [
    (-89.99, 0.0, "Antarctica", "Amundsen-Scott Station"), (89.5, -30.0, "Greenland", "Near the North Pole"),
    (-0.18, -78.47, "Ecuador", "Quito"), (0.32, 32.58, "Uganda", "Kampala"), (-0.95, 100.35, "Indonesia", "Padang"),
    (-16.78, -179.98, "Fiji", "Taveuni, on the 180th meridian"), (65.73, -179.9, "Russia", "Chukotka coast"),
    (-8.52, 179.2, "Tuvalu", "Funafuti"), (7.09, 171.38, "Marshall Islands", "Majuro"),
    (11.58, 165.38, "Marshall Islands", "Bikini Atoll"), (1.353, 172.931, "Kiribati", "Betio, Tarawa"),
    (1.98, -157.47, "Kiribati", "London, Kiritimati"), (-7.313, 72.411, "British Indian Ocean Territory", "Diego Garcia"),
    (-9.4167, 46.4167, "Seychelles", "Aldabra Atoll"), (41.9022, 12.4534, "Vatican", "St Peter's Basilica"),
    (43.9356, 12.4473, "San Marino", "Guaita Tower"), (-29.35, 27.68, "Lesotho", "Thaba Bosiu"),
    (47.141, 9.5215, "Liechtenstein", "Vaduz Castle"), (36.14, -5.35, "United Kingdom", "Rock of Gibraltar"),
    (41.0086, 28.9802, "Turkey", "Hagia Sophia"), (38.1938, 15.554, "Italy", "Messina Cathedral"),
    (51.1295, 1.3089, "United Kingdom", "Dover Castle"), (12.6, 43.33, "Djibouti", "Bab-el-Mandeb"),
    (26.18, 56.25, "Oman", "Khasab"), (1.2868, 103.8545, "Singapore", "Merlion"),
    (57.813, -8.57, "United Kingdom", "Village Bay, Hirta"), (-7.9286, -14.4155, "Saint Helena", "Georgetown"),
    (-37.0675, -12.3105, "United Kingdom", "Edinburgh of the Seven Seas"), (78.925, 11.93, "Norway", "Ny-Alesund"),
    (-77.846, 166.676, "Antarctica", "McMurdo Station"), (81.6031, -16.6556, "Greenland", "Station Nord"),
    (-27.1258, -109.2769, "Chile", "Ahu Tongariki"), (21.3069, -157.8583, "United States of America", "Honolulu"),
    (71.29, -156.79, "United States of America", "Utqiagvik"), (-54.8, -68.3, "Argentina", "Ushuaia"),
    (57.7447, 10.6365, "Denmark", "Grenen, Skagen"), (30.5965, 32.2715, "Egypt", "Suez Canal"),
]


def _random_sites(n: int, seed: int = 1313) -> list[tuple[float, float, str, str]]:
    """`n` sites inside randomly chosen countries' largest polygons (rejection sampled on its extent), seeded."""
    rnd = random.Random(seed)
    cs = [c for c in geo.countries() if c.get("display") and c["polygons"]]
    out = []
    while len(out) < n:
        c = rnd.choice(cs)
        poly = max(c["polygons"], key=lambda p: geo.ring_area(p[0]))
        xs, ys = [x for x, _ in poly[0]], [y for _, y in poly[0]]
        for _ in range(200):
            lon, lat = rnd.uniform(min(xs), max(xs)), rnd.uniform(min(ys), max(ys))
            if geo.point_in_polygons(lon, lat, [poly]):
                out.append((lat, lon, str(c["display"]), f"Site {len(out) + 1}"))
                break
    return out


SWEEP = EDGE_SITES + (_random_sites(60 - len(EDGE_SITES)) if geo.available() else [])


@needs_geo
@needs_marine
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
@pytest.mark.parametrize("k", range(len(SWEEP)))
def test_the_sweep_draws_only_names_the_verifier_passes(k, size, tmp_path, monkeypatch) -> None:
    lat, lon, country, label = SWEEP[k]
    lay = _map(monkeypatch, tmp_path, P(lat, lon, country, label), size)
    checks = lay["name_checks"]
    passed = {(c["kind"], c["text"]) for c in checks if not c["fault"]}
    for w in lay["water"]:
        assert ("water", w.upper()) in passed, (label, w)
    for c in checks:          # every drawn water name's points, re-checked outside the renderer
        if c["kind"] == "water" and not c["fault"]:
            for lo, la in c["lonlat"]:
                m = geo.marine_at(lo, la)
                assert m is not None and m["name"].upper() == c["text"], (label, c["text"], lo, la)
    if lay["country"] is not None:
        assert ("home", lay["country"]["text"]) in passed, (label, lay["country"])
    for nb in lay["neighbours"]:
        assert ("neighbour", nb) in passed, (label, nb)
    # never a water name in the wrong hemisphere's half of an ocean
    for w in lay["water"]:
        if w.startswith(("North ", "South ")) and w.endswith("Ocean"):
            pts = next(c["lonlat"] for c in checks if c["kind"] == "water" and c["text"] == w.upper())
            assert all((la >= 0) == w.startswith("North") for _lo, la in pts), (label, w)
    assert math.isfinite(lay["span_close"])
