"""Round 15 (2026-11-05): the coordinator's invariants, in place of the layered naming heuristics.

1. IDENTITY FROM GEOMETRY: the home is the 1:10m entity whose polygon holds the site (geo.site_home over
   geo.countries_detail), named by its own name field written out (geo.entity_name); its fill is that entity's.
2. LEADER OR DROP: every name inside its own polygon, or (the home's) outside only over open water with a leader
   to its ground; else dropped and logged.
3. OUTLINE QUALITY: the 1:10m outline, never smoothed or made up; framed no closer than its own vertices hold.
4. RULING R11: a 16:9 span on one row, or two in the count-over-unit form only; never "LATER" alone; a label never
   splits a name or leaves a one-word middle row.
5. An independent sweep: every drawn name checked against the raw Natural Earth geojson by tests/geo_truth.py's
   own point-in-polygon code, over 200 sites at both sizes.
"""
import json
import logging
import math
import random

import pytest

from src import geo, graphics
from tests import geo_truth
from tests.test_r4_graphics_2026_11_02 import _render

needs_data = pytest.mark.skipif(not (geo.available() and geo.COUNTRIES_10M_FILE.is_file() and geo.MARINE_FILE.is_file()),
                                reason="Natural Earth data not fetched")
P = lambda lat, lon, c, lab: {"lat": lat, "lon": lon, "country": c, "label": lab, "approximate": False}
SIZES = [(1080, 1920), (1920, 1080)]


def _map(monkeypatch, tmp_path, place, size, name="m") -> dict:
    w, h = size
    _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / f"{name}.mp4", width=w, height=h), fps=1)
    return json.loads((tmp_path / f"{name}.mp4.layout.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------- 1. identity
@needs_data
@pytest.mark.parametrize("lat, lon, given, want", [
    (36.144, -5.342, "United Kingdom", "Gibraltar"),            # never Jersey, 1,476 km away; not Spain's ground
    (-9.38, -171.247, "New Zealand", "Tokelau"),                 # never NEW ZEALAND, 3,500 km away
    (0.3365, 6.7273, "Sao Tome and Principe", "Sao Tome and Principe"),   # accents folded
    (-7.926, -14.416, "United Kingdom", "Ascension"),
    (-37.067, -12.311, "United Kingdom", "Tristan da Cunha"),
    (57.813, -8.57, "United Kingdom", "United Kingdom"),        # St Kilda: Scotland is the UK's own ground
    (-7.313, 72.411, "United Kingdom", "British Indian Ocean Territory"),   # Diego Garcia, no special table
    (54.7065, 20.511, "Russia", "Russia"),                      # Kaliningrad
    (62.0107, -6.7741, "Denmark", "Faroe Islands"), (-29.056, 167.96, "Australia", "Norfolk Island"),
    (-49.35, 70.22, "France", "French Southern and Antarctic Lands"), (4.94, -52.33, "France", "French Guiana"),
    (22.2783, 114.1747, "China", "Hong Kong"), (-29.31, 27.48, "Lesotho", "Lesotho"), (41.9029, 12.4534, "Italy", "Vatican"),
    (47.696, 8.69, "Germany", "Germany"),                       # Busingen: its enclave is not in the data; 2 km
])
def test_the_home_is_the_entity_whose_ground_holds_the_site(lat, lon, given, want) -> None:
    with geo.detail_view() as on:
        assert on
        got = geo.site_home(lon, lat, hint=given)
    assert got.country is not None and got.country["display"] == want, (got.country or {}).get("display")


@needs_data
def test_the_topic_country_never_reaches_past_the_datas_own_error() -> None:
    with geo.detail_view():
        # Fakaofo 6.5 km off Tokelau's islets: given as Samoa, still Tokelau's (the hint chooses only within 5 km)
        assert geo.site_home(-171.247, -9.38, hint="Samoa").country["display"] == "Tokelau"
        # Point Nemo: no ground within 50 km, whatever the topic says
        assert geo.site_home(-123.393, -48.876, hint="Chile").country is None


def test_names_are_written_out_and_folded() -> None:
    assert geo.fold("São Tomé and Príncipe") == "Sao Tome and Principe"
    assert not geo.clean_name("Hong Kong S.A.R.") and not geo.clean_name("Lao PDR") and not geo.clean_name("UK")
    assert not geo.clean_name("Falkland Islands / Malvinas") and geo.clean_name("Cocos (Keeling) Islands")
    assert geo.clean_forms(["U.S.A.", "USA", "United States", "united states"]) == ["United States"]


@needs_data
def test_no_initialism_is_ever_an_entitys_name() -> None:
    bad = [c["display"] for c in geo.countries_detail() if not geo.clean_name(c["display"])
           and "US Naval Base" not in c["display"]]
    assert not bad, bad
    bad_alt = [(c["display"], a) for c in geo.countries_detail() for a in c.get("short_names", [])
               if not geo.clean_name(a)]
    assert not bad_alt, bad_alt
    assert not [c for c in geo.countries_50m() for a in c.get("short_names", []) if not geo.clean_name(a)]


@needs_data
def test_an_entitys_internal_borders_are_never_stroked() -> None:
    with geo.detail_view():
        uk = next(c for c in geo.countries() if c["display"] == "United Kingdom")
        seams = [any(geo.seam_edges(poly[0])) for poly in uk["polygons"]]
    assert any(seams)                          # England's and Scotland's shared edge is a seam, never a border


def test_detail_view_draws_a_stand_in_list_as_given(monkeypatch) -> None:
    fake = [{"names": {"x"}, "polygons": [], "display": "X"}]
    monkeypatch.setattr(geo, "countries", lambda: fake)
    with geo.detail_view() as on:
        assert not on and geo.countries() is fake


def test_the_globe_fills_the_homes_coarser_form(monkeypatch) -> None:
    assert geo.same_entity({"key": "france"}, {"key": "france"})
    assert not geo.same_entity({"key": "france"}, {"key": "france/french guiana"})
    assert not geo.same_entity(None, {"key": "france"})


# ---------------------------------------------------------------------------------------------- 2. leader or drop
def test_an_outside_home_name_over_any_land_fails() -> None:
    import numpy as np
    w, h = 1080, 1920
    home = np.zeros((h, w), bool)
    home[900:960, 500:560] = True                              # Kaliningrad
    land = home.copy()
    land[960:1400, 300:900] = True                             # Lithuania and Poland, large landmasses
    on_land = (520.0, 1000.0, 650.0, 1020.0)
    assert "over other land" in graphics.home_name_fault(on_land, False, home, land, (530.0, 930.0))
    at_sea = (300.0, 850.0, 430.0, 870.0)
    assert graphics.home_name_fault(at_sea, False, home, land, (530.0, 930.0)) == ""
    lead = graphics.ground_leader(at_sea, home)
    assert lead is not None and lead[1][0] >= 495 and 845 <= lead[1][1] <= 965


def test_a_neighbour_name_partly_on_the_sea_fails() -> None:
    import numpy as np
    ids = np.zeros((100, 300), np.uint16)
    ids[:, :150] = 1
    table = (ids, {1: {"display": "Ireland", "names": {"ireland"}}})
    assert graphics.neighbour_name_fault("IRELAND", (20, 20, 120, 40), table, down=1) == ""
    assert "on the sea" in graphics.neighbour_name_fault("IRELAND", (100, 20, 200, 40), table, down=1)


DIRECTIVE2 = {
    "kaliningrad": (P(54.7065, 20.511, "Russia", "Konigsberg Cathedral, Kaliningrad"), "RUSSIA"),
    "singapore": (P(1.2895, 103.852, "Singapore", "Marina Bay, Singapore"), "SINGAPORE"),
    "monaco": (P(43.731, 7.42, "Monaco", "Monte Carlo Casino"), "MONACO"),
    "lesotho": (P(-29.38, 27.68, "Lesotho", "Thaba Bosiu"), "LESOTHO"),
    "liechtenstein": (P(47.1394, 9.5246, "Liechtenstein", "Vaduz Castle"), "LIECHTENSTEIN"),
    "vatican": (P(41.9022, 12.4539, "Vatican", "St Peter's Basilica"), "VATICAN"),
    "gambia": (P(13.45, -16.58, "Gambia", "Banjul"), "GAMBIA"),
    "akrotiri": (P(34.585, 32.987, "United Kingdom", "RAF Akrotiri"), "AKROTIRI SOVEREIGN BASE AREA"),
    "gibraltar": (P(36.144, -5.342, "United Kingdom", "Rock of Gibraltar"), "GIBRALTAR"),
    "fakaofo": (P(-9.38, -171.247, "New Zealand", "Fakaofo, Tokelau"), "TOKELAU"),
    "saotome": (P(0.3365, 6.7273, "Sao Tome and Principe", "Sao Tome"), "SAO TOME AND PRINCIPE"),
    "ascension": (P(-7.926, -14.416, "United Kingdom", "Georgetown, Ascension"), "ASCENSION"),
    "tristan": (P(-37.067, -12.311, "United Kingdom", "Edinburgh of the Seven Seas"), "TRISTAN DA CUNHA"),
    "stkilda": (P(57.813, -8.57, "United Kingdom", "Hirta, St Kilda"), "UNITED KINGDOM"),
    "diego": (P(-7.313, 72.411, "United Kingdom", "Diego Garcia"), "BRITISH INDIAN OCEAN TERRITORY"),
}


@needs_data
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
@pytest.mark.parametrize("name", sorted(DIRECTIVE2))
def test_the_directives_places_name_their_home_or_drop_it_with_a_reason(name, size, tmp_path, monkeypatch) -> None:
    place, want = DIRECTIVE2[name]
    lay = _map(monkeypatch, tmp_path, place, size)
    c = lay["country"]
    if c is not None:
        forms = {want} | {s.upper() for s in (geo.site_home(place["lon"], place["lat"]).country or {}).get(
            "short_names", [])}
        with geo.detail_view():
            forms |= {s.upper() for s in (geo.site_home(place["lon"], place["lat"], hint=place["country"]).country
                                          or {}).get("short_names", [])}
        assert c["text"] in forms, (name, c["text"])
        if not c["inside"]:
            assert c["leader"] is not None or not geo_truth.faults(lay), (name, "outside without a leader")
    else:
        assert lay["home_drop"], name                          # never dropped silently
    assert not geo_truth.faults(lay), (name, geo_truth.faults(lay))


@needs_data
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_lesotho_holds_its_own_name(size, tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, DIRECTIVE2["lesotho"][0], size)
    assert lay["country"] is not None and lay["country"]["text"] == "LESOTHO" and lay["country"]["inside"]


@needs_data
def test_kaliningrads_russia_is_never_on_lithuania(tmp_path, monkeypatch) -> None:
    for size in SIZES:
        lay = _map(monkeypatch, tmp_path, DIRECTIVE2["kaliningrad"][0], size, f"k{size[0]}")
        chk = [c for c in lay["name_checks"] if c["kind"] == "home" and not c["fault"]]
        for c in chk:
            for lo, la in c["lonlat"]:
                assert not any(geo_truth.norm(f.props.get("ADMIN")) == "lithuania" for f in geo_truth.land_at(lo, la))


# ---------------------------------------------------------------------------------------------- 3. outlines
@needs_data
def test_no_outline_is_smoothed() -> None:
    assert not hasattr(geo, "smoothed_islands") and not hasattr(geo, "catmull_rom_ring")
    with geo.detail_view():
        polys = geo.view_polygons(-109.217, 10.3, 0.2, 0.2)       # Clipperton: its own 1:10m points, as they are
        raw = next(u for u in geo.countries_10m() if u.name == "Clipperton Island").polygons[0][0]
    assert any(list(p[0]) == list(raw) for _c, p in polys)


@needs_data
@pytest.mark.parametrize("lat, lon, country", [(4.1755, 73.5093, "Maldives"), (10.3, -109.217, "France"),
                                               (-0.743, -90.313, "Ecuador"), (1.2895, 103.852, "Singapore")])
def test_an_island_is_framed_no_closer_than_its_own_vertices_hold(lat, lon, country) -> None:
    with geo.detail_view():
        home = geo.site_home(lon, lat, hint=country).country
        for w, h in SIZES:
            span0 = graphics.locator_span(lat, lon, home, w, h)
            span = graphics.island_span(lat, lon, home, w, h, span0) or span0
            ring = graphics._site_island(lat, lon, home)[1]
            c = math.cos(math.radians(lat))
            edges = [math.hypot((b[0] - a[0]) * c, b[1] - a[1]) for a, b in zip(ring, ring[1:] + ring[:1])]
            mean_px = sum(edges) / len(edges) * w / span
            assert mean_px <= graphics.ISLAND_VERTEX_PX_RAW * min(w, h) / 1080 + 1, (country, w, mean_px)


@needs_data
def test_an_offshore_site_has_a_leader_to_its_land(tmp_path, monkeypatch) -> None:
    import numpy as np
    land = np.zeros((1920, 1080), bool)
    land[900:960, 700:760] = True                              # its islet, 150 px east of the marker
    lead = graphics.ground_leader((526.0, 946.0, 554.0, 974.0), land)
    assert lead is not None and lead[1][0] >= 697 and lead[0][0] <= 560
    assert graphics.ground_leader((690.0, 946.0, 702.0, 958.0), land) is None   # touching its land: none
    lay = _map(monkeypatch, tmp_path, P(-9.38, -171.247, "New Zealand", "Fakaofo, Tokelau"), (1080, 1920))
    assert lay["country"] is not None and lay["country"]["text"] == "TOKELAU"
    assert not geo_truth.faults(lay)


# ---------------------------------------------------------------------------------------------- 4. ruling R11
def test_a_two_row_span_is_the_count_over_its_unit() -> None:
    assert graphics.wide_span_lines("ABOUT 700 YEARS LATER", 2) == ["ABOUT 700", "YEARS LATER"]
    assert graphics.wide_span_lines("1,669 YEARS LATER", 2) == ["1,669", "YEARS LATER"]
    assert graphics.wide_span_lines("155 YEARS LATER", True) == ["155", "YEARS LATER"]
    assert graphics.wide_span_lines("155 YEARS LATER", False) == ["155 YEARS LATER"]


def test_ferdinand_and_isabella_stay_together() -> None:
    src = "Boabdil hands Granada to Ferdinand and Isabella"
    assert graphics.label_hard_fault(["BOABDIL HANDS GRANADA", "TO FERDINAND AND", "ISABELLA"], src)
    assert not graphics.label_hard_fault(["BOABDIL HANDS", "GRANADA TO", "FERDINAND AND ISABELLA"], src)


E = lambda y, lab, a=False: {"year": y, "label": lab, "approximate": a}
TL = {
    "alhambra": [E(1238, "Muhammad I founds the Nasrid palace"), E(1492, "Boabdil hands Granada to Ferdinand and Isabella"),
                 E(1526, "Charles V begins his own palace"), E(1829, "Washington Irving lives in the palace"),
                 E(1984, "Named a World Heritage Site")],
    "colosseum": [E(72, "Vespasian begins the amphitheatre"), E(80, "Titus opens it with 100 days of games"),
                  E(1349, "An earthquake brings down the south side"), E(1749, "Pope Benedict XIV declares it sacred"),
                  E(1980, "Named a World Heritage Site")],
    "stonehenge": [E(-3000, "The first ditch and bank are dug", True), E(-2500, "The sarsen stones are raised", True),
                   E(1918, "Cecil Chubb gives it to the nation"), E(1986, "Named a World Heritage Site")],
    "machu": [E(1450, "Pachacuti builds the royal estate", True), E(1572, "The site is abandoned", True),
              E(1911, "Hiram Bingham reaches the ruins"), E(1983, "Named a World Heritage Site")],
    "angkor": [E(1113, "Suryavarman II begins the temple"), E(1150, "The temple is finished", True),
               E(1431, "The Khmer capital moves south"), E(1860, "Henri Mouhot reaches the ruins"),
               E(1992, "Named a World Heritage Site")],
    "notredame": [E(1163, "Maurice de Sully lays the first stone"), E(1345, "The cathedral is finished"),
                  E(1804, "Napoleon is crowned emperor inside"), E(2019, "Fire destroys the spire"),
                  E(2024, "The cathedral reopens")],
    "hagia": [E(537, "Justinian opens the great church"), E(558, "An earthquake brings down the dome"),
              E(1453, "Mehmed the Conqueror makes it a mosque"), E(1934, "It becomes a museum"),
              E(2020, "It is a mosque again")],
}


@pytest.mark.parametrize("name", sorted(TL))
def test_every_review_timeline_is_clean_under_r11(name, tmp_path, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(TL[name], tmp_path / "t.mp4", width=1920,
                                                                   height=1080), fps=1)
    errs = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert not errs, errs                                      # no unclean layout, no hard failure
    plates = [(n, getattr(p, "text", "")) for n, p in st["plates"]]
    spans = [t for n, t in plates if n.startswith("gap")]
    assert "LATER" not in spans and "YEARS" not in spans, spans          # never "LATER" (or "YEARS") alone
    for t in spans:                                            # a span's row: whole, the count, or its unit
        assert t.endswith(("YEARS LATER", "YRS LATER")) or not t.endswith("LATER"), t
    labels: dict[str, list[str]] = {}
    for n, t in plates:
        if n.startswith("label"):
            labels.setdefault(n, []).append(t)
    for rows in labels.values():
        src = next((e["label"] for e in TL[name] if " ".join(rows) == e["label"].upper()), None)
        assert not graphics.label_hard_fault(rows, src), (name, rows)
        assert not any(r.endswith(("FERDINAND AND", "FERDINAND")) for r in rows[:-1]), rows


# ---------------------------------------------------------------------------------------------- 5. the sweep
SMALL_ISLANDS = [
    (-0.5228, 166.9315, "Nauru"), (1.42, 173.0, "Kiribati"), (-8.52, 179.2, "Tuvalu"), (4.1755, 73.5093, "Maldives"),
    (10.3, -109.217, "France"), (19.283, 166.647, "United States"), (-12.188, 96.829, "Australia"),
    (-10.49, 105.63, "Australia"), (-25.066, -130.1015, "United Kingdom"), (-27.125, -109.35, "Chile"),
    (-17.53, -149.57, "France"), (-14.97, -147.63, "France"), (11.6, 165.38, "Marshall Islands"),
    (7.09, 171.38, "Marshall Islands"), (-21.2, -175.2, "Tonga"), (-19.05, -169.9, "Niue"),
    (-21.23, -159.78, "Cook Islands"), (6.92, 158.16, "Micronesia"), (7.5, 134.6, "Palau"),
    (-4.62, 55.45, "Seychelles"), (-20.16, 57.5, "Mauritius"), (-11.7, 43.25, "Comoros"),
    (-15.94, -5.71, "United Kingdom"), (-54.43, 3.4, "Norway"), (-53.019, 73.392, "Australia"),
    (-54.5, 158.95, "Australia"), (-3.854, -32.424, "Brazil"), (-0.743, -90.313, "Ecuador"),
    (32.3, -64.78, "United Kingdom"), (13.1, -59.6, "Barbados"), (18.0, -63.05, "Netherlands"),
    (17.9, -62.83, "France"), (12.52, -70.03, "Netherlands"), (35.9, 14.5, "Malta"), (64.8, -23.0, "Iceland"),
    (-46.9, 37.75, "South Africa"), (-38.7, 77.55, "France"), (70.9333, -8.6667, "Norway"), (59.9, -1.3, "United Kingdom"),
    (-9.38, -171.247, "New Zealand"),
]
ENCLAVES = [
    (36.144, -5.342, "United Kingdom"), (43.731, 7.42, "Monaco"), (41.9022, 12.4539, "Vatican"),
    (43.94, 12.45, "San Marino"), (47.1394, 9.5246, "Liechtenstein"), (42.507, 1.52, "Andorra"),
    (-29.38, 27.68, "Lesotho"), (-26.32, 31.14, "eSwatini"), (13.45, -16.58, "Gambia"), (34.585, 32.987, "United Kingdom"),
    (35.0, 33.75, "United Kingdom"), (47.696, 8.69, "Germany"), (51.4386, 4.9289, "Belgium"), (42.4637, 1.9814, "Spain"),
    (54.7065, 20.511, "Russia"), (39.209, 45.412, "Azerbaijan"), (-5.55, 12.2, "Angola"), (1.2895, 103.852, "Singapore"),
    (22.2783, 114.1747, "China"), (22.19, 113.54, "China"), (26.2335, 50.573, "Bahrain"), (25.29, 51.53, "Qatar"),
    (4.89, 114.94, "Brunei"), (35.889, -5.317, "Spain"), (35.29, -2.94, "Spain"),
]
TRIPOINTS = [
    (20.354, 100.083, "Thailand"), (69.06, 20.548, "Sweden"), (47.5897, 7.5893, "Switzerland"),
    (50.754, 6.02, "Netherlands"), (-25.69, -54.59, "Brazil"), (48.0, 17.16, "Slovakia"), (46.83, 16.1, "Austria"),
    (31.6, 35.5, "Jordan"), (-22.0, 27.87, "Botswana"), (12.4, 14.3, "Chad"), (41.86, 26.36, "Greece"),
    (49.1, 22.6, "Poland"),
]
POLES = [(89.95, 0.0, ""), (-89.9, 0.0, "Antarctica"), (82.5, -62.3, "Canada"), (78.92, 11.93, "Norway"),
         (80.803, 47.65, "Russia"), (77.467, -69.23, "Greenland"), (-75.1, 123.33, "Antarctica"),
         (-77.85, 166.67, "Antarctica"), (-63.397, -56.997, "Antarctica"), (83.6625, -33.37, "Greenland")]
ANTIMERIDIAN = [(71.24, -179.4, "Russia"), (-16.8, 179.9, "Fiji"), (-16.78, -179.95, "Fiji"), (52.9, 173.2, "United States"),
                (65.0, 179.5, "Russia"), (-43.954, -176.559, "New Zealand"), (-17.9, 178.0, "Fiji"),
                (51.8, -176.6, "United States")]


def _random_sites(n: int, seed: int = 1505) -> list[tuple[float, float, str]]:
    """`n` seeded sites on land over every continent: a random point inside a random 1:10m subunit, half of them
    weighted to the large ones (by area) so the continents' interiors are in it, half uniform over the subunits
    (so small countries and territories are too)."""
    rnd = random.Random(seed)
    units = [u for u in geo.countries_10m() if u.polygons]
    area = [max(1e-6, sum(abs(geo.ring_area(p[0])) for p in u.polygons)) for u in units]
    out: list[tuple[float, float, str]] = []
    tries = 0
    while len(out) < n and tries < 50000:
        tries += 1
        u = rnd.choices(units, weights=area)[0] if len(out) % 2 == 0 else rnd.choice(units)
        poly = max(u.polygons, key=lambda p: abs(geo.ring_area(p[0]))) if rnd.random() < 0.7 else rnd.choice(u.polygons)
        xs, ys = [x for x, _ in poly[0]], [y for _, y in poly[0]]
        lon, lat = rnd.uniform(min(xs), max(xs)), rnd.uniform(min(ys), max(ys))
        if abs(lat) > 85 or not geo.point_in_polygons(lon, lat, [poly]):
            continue
        out.append((round(lat, 4), round(lon, 4), u.admin))
    return out


def _coastal_sites(n: int, seed: int = 1515) -> list[tuple[float, float, str]]:
    """`n` sites on the 1:10m coast more than 5 km off the 1:50m ground (how a cancelled fill got through)."""
    rnd = random.Random(seed)
    units = [u for u in geo.countries_10m() if u.polygons]
    out: list[tuple[float, float, str]] = []
    tries = 0
    while len(out) < n and tries < 20000:
        tries += 1
        u = rnd.choice(units)
        poly = rnd.choice(u.polygons)
        if abs(geo.ring_area(poly[0])) < 0.05:
            continue
        lon, lat = rnd.choice(poly[0])
        if abs(lat) > 80:
            continue
        near50 = min((geo.edge_km(p[0], lon, lat) for c in geo.countries_50m() for p in c["polygons"]
                      if c["extent"][0] - 1 <= lon <= c["extent"][2] + 1 and c["extent"][1] - 1 <= lat <= c["extent"][3] + 1
                      and not geo.point_in_polygons(lon, lat, [p])), default=math.inf)
        inside50 = any(geo.point_in_polygons(lon, lat, c["polygons"]) for c in geo.countries_50m()
                       if c["extent"][0] <= lon <= c["extent"][2] and c["extent"][1] <= lat <= c["extent"][3])
        if inside50 or near50 <= 5.0:
            continue
        out.append((round(lat, 4), round(lon, 4), u.admin))
    return out


SWEEP: list[tuple[float, float, str]] = []
if geo.available() and geo.COUNTRIES_10M_FILE.is_file():
    SWEEP = (SMALL_ISLANDS + ENCLAVES + TRIPOINTS + POLES + ANTIMERIDIAN + _random_sites(80) + _coastal_sites(25))


def test_the_sweep_is_large_and_spread() -> None:
    if not SWEEP:
        pytest.skip("Natural Earth data not fetched")
    assert len(SWEEP) >= 200
    lats = [s[0] for s in SWEEP]
    assert max(lats) > 89 and min(lats) < -89                  # both poles
    assert sum(abs(s[1]) > 175 for s in SWEEP) >= 6            # the antimeridian
    quads = {(s[0] > 0, s[1] > 0) for s in SWEEP}
    assert len(quads) == 4


@needs_data
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
@pytest.mark.parametrize("k", range(len(SWEEP)))
def test_the_sweep_names_every_home_and_passes_the_independent_check(k, size, tmp_path, monkeypatch) -> None:
    lat, lon, country = SWEEP[k]
    lay = _map(monkeypatch, tmp_path, P(lat, lon, country, f"Site {k + 1}"), size)
    assert lay["country"] is not None or lay["home_drop"], (lat, lon, "home dropped with no reason")
    faults = geo_truth.faults(lay)
    assert not faults, (lat, lon, country, faults)
