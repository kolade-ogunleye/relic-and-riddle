"""Round 14 (2026-11-04): review 13's blocking items, made structural. The home fill drawn as the union of its
polygons (a 1:10m outline over the 1:50m one cancelled it, even-odd, and SOUTH AFRICA went to sea); the home
resolved to the 1:50m entity that holds the site (the Faroes, Norfolk Island and Kerguelen were drawn grey as
neighbours of themselves); an outside home name never nearer other land than its own (GREENLAND beside Svalbard);
the island close-up decided before the polar view (Torshavn and Jan Mayen 9:16 at Europe scale) and never closer
than the data's own offset or detail reads (Rangiroa, Male, Cape Horn); every form of the home's name tried in its
fill before any outside place (Diomede); the route's names through the verifier too, with a sidecar; 16:9 spans
on one row before any lone, wide or weak label, and every unclean set logged as an error. The sweep's names are
checked by an independent inverse projection against the raw Natural Earth polygons, and its sites include
coastal points off the 1:50m ground (how the cancelled fill got through)."""
import json
import logging
import math
import random

import numpy as np
import pytest
from shapely import Point

from src import geo, graphics, map_layers
from tests import geo_truth
from tests.test_r4_graphics_2026_11_02 import _render

needs_geo = pytest.mark.skipif(not geo.available(), reason="country outlines not fetched")
needs_10m = pytest.mark.skipif(not geo.COUNTRIES_10M_FILE.is_file(), reason="1:10m map subunits not fetched")
needs_marine = pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")

P = lambda lat, lon, c, lab: {"lat": lat, "lon": lon, "country": c, "label": lab, "approximate": False}
SIZES = [(1080, 1920), (1920, 1080)]


def _square(x0: float, y0: float, s: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)]


# ---------------------------------------------------------------------------------------------- the home fill
def test_overlapping_home_polygons_fill_as_their_union() -> None:
    rings = graphics.HomeRings([_square(100, 100, 400), _square(120, 120, 400)])
    rings.groups, rings.outer = [0, 1], [True, True]
    m = graphics.rings_fill(rings, 800, 800)
    assert m.sum() > 400 * 400                         # even-odd left only the two thin L-shaped strips
    assert m[300, 300]
    plain = graphics.rings_fill([_square(100, 100, 400), _square(120, 120, 400)], 800, 800)
    assert not plain[300, 300]                         # a plain ring list stays even-odd (holes and parts alike)


def test_a_polygons_hole_stays_a_hole_in_the_union() -> None:
    rings = graphics.HomeRings([_square(100, 100, 400), _square(250, 250, 50), _square(600, 600, 100)])
    rings.groups, rings.outer = [0, 0, 1], [True, False, True]
    m = graphics.rings_fill(rings, 800, 800)
    assert not m[270, 270] and m[150, 150] and m[650, 650]


def test_the_name_fits_a_fill_drawn_twice() -> None:
    # review 13: the 1:50m South Africa and its 1:10m outline over it; even-odd, 46,807 px left 300
    rings = graphics.HomeRings([_square(200, 600, 700), _square(204, 604, 700)])
    rings.groups, rings.outer = [0, 1], [True, True]
    got = graphics.country_name_place("SOUTH AFRICA", rings, (540.0, 1500.0), (400.0, 1400.0, 680.0, 1440.0), [],
                                      1080, 1920)
    assert got is not None
    assert graphics.country_name_place("SOUTH AFRICA", list(rings), (540.0, 1500.0),
                                       (400.0, 1400.0, 680.0, 1440.0), [], 1080, 1920) is None


def test_the_name_keeps_off_a_part_smaller_than_the_sites() -> None:
    home = np.zeros((400, 400), bool)
    home[0:300, 0:200] = True                          # the mainland, the site on it
    home[350:390, 300:390] = True                      # a smaller island
    allow = graphics.allowed_home_parts(home, (100.0, 100.0))
    assert allow is not None and allow[100, 100] and not allow[370, 350]
    assert graphics.allowed_home_parts(home, (380.0, 10.0), k=0.01) is None    # no part under the site


# ---------------------------------------------------------------------------------------------- the entity
@needs_geo
@needs_10m
@pytest.mark.parametrize("country, lat, lon, want", [
    ("Denmark", 62.0107, -6.7741, "Faroe Islands"), ("Australia", -29.056, 167.96, "Norfolk Island"),
    ("France", -49.35, 70.22, "French Southern and Antarctic Lands"), ("France", -14.97, -147.63, "French Polynesia"),
    ("Denmark", 56.039, 12.6213, "Denmark"), ("Norway", 70.9333, -8.6667, "Norway"),
    ("United Kingdom", 57.813, -8.57, "United Kingdom"),
])
def test_the_home_is_the_entity_that_holds_the_site(country, lat, lon, want) -> None:
    # round 15 (coordinator directive 1, deliberate): graphics.site_entity and its 1,500 km search are gone; the
    # 1:10m entity whose ground holds the site (or lies nearest, offshore) is the home, by its written-out name
    # (None stood for the given country's own: now its name)
    with geo.detail_view():
        got = geo.site_home(lon, lat, hint=country)
    assert (got.country["display"] if got.country is not None else None) == want


@needs_geo
def test_an_abbreviated_name_is_written_out() -> None:
    assert geo.find_country("Faeroe Is.")["full_name"] == "Faroe Islands"
    assert geo.find_country("Marshall Islands")["full_name"] == "Marshall Islands"
    assert geo.find_country("Fr. S. Antarctic Lands")["full_name"] == "French Southern and Antarctic Lands"
    assert geo.find_country("France")["full_name"] == "France"
    assert geo.full_name({"NAME": "W. Sahara", "ADMIN": "Western Sahara"}) == "Western Sahara"
    assert geo.full_name({"NAME": "X. Y."}) == "X. Y."


# ---------------------------------------------------------------------------------------------- outside names
def test_an_outside_name_nearer_other_land_than_its_own_fails() -> None:
    w, h = 1080, 1920
    home = np.zeros((h, w), bool)
    home[1000:1900, 0:500] = True                      # Greenland, the site on it
    other = np.zeros((h, w), bool)
    other[0:1000, 900:1080] = True                     # Svalbard, larger than 20 name boxes
    land = home | other
    box = (650.0, 795.0, 880.0, 820.0)                 # 20 px off Svalbard, 180 px off Greenland
    assert "nearer other land" in graphics.home_name_fault(box, False, home, land, (250.0, 1400.0))
    near_home = (200.0, 950.0, 430.0, 975.0)           # 25 px over Greenland
    assert graphics.home_name_fault(near_home, False, home, land, (250.0, 1400.0)) == ""


# ---------------------------------------------------------------------------------------------- framing
@needs_geo
@needs_10m
def test_a_site_off_all_drawn_ground_is_framed_where_its_offset_reads() -> None:
    lat, lon = -55.98, -67.27                          # Cape Horn: Hornos Island is not in the data
    w, h = 1920, 1080
    # round 15 (deliberate): the 1:10m entity drawn throughout (geo.detail_view), no outline added for the view
    with geo.detail_view():
        home = geo.site_home(lon, lat, hint="Chile").country
        assert home is not None and home["display"] == "Chile"
        span0 = graphics.locator_span(lat, lon, home, w, h)
        span = graphics.island_span(lat, lon, home, w, h, span0)
        d_off = graphics._site_island(lat, lon, home)[0] * 111.2
    span = span if span is not None else span0
    assert d_off / 111.2 * w / span <= graphics.ISLAND_OFF_PX + 1     # the dot within 24 px of the drawn ground


@needs_geo
def test_a_coarse_island_is_never_framed_larger_than_its_detail() -> None:
    # round 15 (coordinator directive 3, deliberate): no outline is smoothed, so none is "coarse" by that test
    # (ISLAND_COARSE_MAX_PX is gone): the outline's own mean edge holds the frame (ISLAND_VERTEX_PX_RAW)
    home = geo.find_country("Maldives")
    lat, lon = 4.1755, 73.5093
    for w, h in SIZES:
        span0 = graphics.locator_span(lat, lon, home, w, h)
        span = graphics.island_span(lat, lon, home, w, h, span0)
        ring = graphics._site_island(lat, lon, home)[1]
        c = math.cos(math.radians(lat))
        edges = [math.hypot((b[0] - a[0]) * c, b[1] - a[1]) for a, b in zip(ring, ring[1:] + ring[:1])]
        assert sum(edges) / len(edges) * w / (span or span0) <= graphics.ISLAND_VERTEX_PX_RAW * min(w, h) / 1080 + 1


# ---------------------------------------------------------------------------------------------- an independent check
def _holds(c: geo.Country, lon: float, lat: float, tol_km: float = 0.0) -> bool:
    offs = [(0.0, 0.0)]
    if tol_km:
        d = tol_km / 111.2
        cl = max(0.05, math.cos(math.radians(lat)))
        offs += [(dx * d / cl, dy * d) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    return any(geo.point_in_polygons(lon + dx, lat + dy, c["polygons"]) for dx, dy in offs)


def _units_of(c: geo.Country) -> list[geo.Unit10m]:
    keys = set(c["names"]) | ({c["sovereign"]} if c.get("sovereign") else set())
    return [u for u in geo.countries_10m() if u.names & keys or u.sovereign in c["names"]]


def _home_ground(c: geo.Country, lon: float, lat: float, tol_km: float) -> bool:
    if _holds(c, lon, lat, tol_km):
        return True
    return any(geo.point_in_polygons(lon, lat, list(u.polygons)) for u in _units_of(c))


def independent_name_faults(lay: dict) -> list[str]:
    """Each drawn name's fault found from its checked points' (lon, lat) alone, against the raw Natural Earth
    polygons (the renderer's own verdicts unused): a water name off its body or on a smaller one; a home name
    inside its fill with a point off the home's 1:50m or 1:10m ground or on another country's; an outside one on the
    home's ground; a neighbour's with a point on a third country or under 40 % on its own."""
    out = []
    cs = geo.countries()
    home = next((c for c in cs if c["display"] == lay.get("home")), None)
    by_key = {}
    for c in cs:
        for key in graphics._country_keys(c):
            by_key.setdefault(key, c)
    drawn = {("water", w.upper()) for w in lay.get("water", [])} | {("neighbour", n) for n in lay.get("neighbours", [])}
    if lay.get("country"):
        drawn.add(("home", lay["country"]["text"]))
    for chk in lay["name_checks"]:
        if chk["fault"] or (chk["kind"], chk["text"]) not in drawn:
            continue
        pts = chk["lonlat"]
        if chk["kind"] == "water":
            for lo, la in pts:
                m = geo.marine_at(lo, la)
                if m is None or m["name"].upper() != chk["text"]:
                    out.append(f"water {chk['text']} at ({lo:.2f}, {la:.2f}) on {m['name'] if m else 'nothing'}")
                    break
        elif chk["kind"] == "home" and home is not None:
            inside = lay["country"]["inside"]
            for lo, la in pts:
                mine = _home_ground(home, lo, la, 3.0)
                if inside and not mine:
                    out.append(f"home {chk['text']} at ({lo:.2f}, {la:.2f}) off its ground")
                    break
                if inside:
                    third = [c["display"] for c in cs if c is not home and _holds(c, lo, la)
                             and not _home_ground(home, lo, la, 0.0)]
                    if third:
                        out.append(f"home {chk['text']} at ({lo:.2f}, {la:.2f}) on {third[0]}")
                        break
                # round 15 (coordinator directive 1, deliberate): the ground drawn is the 1:10m data, so an outside
                # name is checked off that ground (the 1:50m Norfolk Island blob runs out over its sea)
                if not inside and any(geo.point_in_polygons(lo, la, list(u.polygons)) for u in _units_of(home)):
                    out.append(f"outside home {chk['text']} at ({lo:.2f}, {la:.2f}) on the home")
                    break
        elif chk["kind"] == "neighbour":
            own_c = by_key.get(chk["text"].replace(" ", ""))
            # round 15 (coordinator directive 1, deliberate): a neighbour may be a 1:10m entity the 1:50m data folds
            # into its sovereign (FRENCH GUIANA, France's at 1:50m): its own 1:10m units are its ground
            own_u = [u for u in geo.countries_10m() if chk["text"].lower() in u.names or chk["text"].lower() in
                     {u.name.lower(), u.geounit.lower()}]
            own = 0
            for lo, la in pts:
                on = [c for c in cs if _holds(c, lo, la)]
                if any(geo.point_in_polygons(lo, la, list(u.polygons)) for u in own_u) \
                        or (own_c is not None and any(c is own_c for c in on)):
                    own += 1
                elif on and not (home is not None and on == [home] and own_c is None) \
                        and (own_c is None or not _holds(own_c, lo, la, 3.0)):
                    out.append(f"neighbour {chk['text']} at ({lo:.2f}, {la:.2f}) on {on[0]['display']}")
                    break
            else:
                if own < 0.3 * len(pts):
                    out.append(f"neighbour {chk['text']}: only {own} of {len(pts)} points on its own ground")
    return out


def _map(monkeypatch, tmp_path, place, size) -> dict:
    w, h = size
    _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=w, height=h), fps=1)
    return json.loads((tmp_path / "m.mp4.layout.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------- the review's places
REVIEW = {
    "agulhas": (P(-34.8333, 20.0167, "South Africa", "Cape Agulhas Lighthouse"), "SOUTH AFRICA"),
    "perim": (P(12.655, 43.415, "Yemen", "Perim Island"), "YEMEN"),
    "puntaarenas": (P(-53.1638, -70.9171, "Chile", "Punta Arenas"), "CHILE"),
    "dumont": (P(-66.6628, 140.0014, "Antarctica", "Dumont d'Urville Station"), "ANTARCTICA"),
    "llivia": (P(42.4637, 1.9814, "Spain", "Llivia"), "SPAIN"),
    # QF-20 (operator ruling Q1, 2026-11-11): FAROE ISLANDS and NORFOLK ISLAND repeat their titles, so each home is
    # named by its sovereign, which the extended independent checker accepts
    "torshavn": (P(62.0107, -6.7741, "Denmark", "Torshavn, Faroe Islands"), "DENMARK"),
    "norfolk": (P(-29.056, 167.96, "Australia", "Kingston, Norfolk Island"), "AUSTRALIA"),
}


@needs_geo
@needs_10m
@needs_marine
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
@pytest.mark.parametrize("name", sorted(REVIEW))
def test_the_reviews_homes_are_named_on_their_own_ground(name, size, tmp_path, monkeypatch, caplog) -> None:
    place, want = REVIEW[name]
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        lay = _map(monkeypatch, tmp_path, place, size)
    assert lay["country"] is not None and lay["country"]["text"] == want, (name, lay["country"])
    if name not in ("torshavn", "norfolk", "puntaarenas"):    # (a small island's name may sit beside it)
        assert lay["country"]["inside"], name                 # in its own fill, never at sea
    # round 15 (coordinator directive 2, deliberate): Punta Arenas 9:16's 1:10m mainland is cut by its fjords and
    # holds CHILE nowhere off the site's name; the name may then sit over open water beside it, with a leader
    if not lay["country"]["inside"]:
        assert lay["country"]["leader"] is not None or name in ("torshavn", "norfolk"), name
    assert not independent_name_faults(lay)
    if name in ("torshavn", "norfolk"):                    # QF-20 Q1: the extended checker
        assert not geo_truth.faults(lay), geo_truth.faults(lay)
    assert want not in lay["neighbours"] and "FAEROE IS." not in lay["neighbours"]
    assert "neighbour of itself" not in caplog.text


@needs_geo
@needs_10m
@needs_marine
def test_diomede_9x16_names_the_united_states_in_its_fill(tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(65.76, -168.95, "United States", "Little Diomede"), (1080, 1920))
    # round 15 (deliberate): Little Diomede, an island of the United States, is framed on itself (ISLAND_PART_PX),
    # so the name sits in its fill or, where that islet cannot hold it, over open water with a leader to it
    assert lay["country"] is not None and (lay["country"]["inside"] or lay["country"]["leader"] is not None)
    assert lay["country"]["text"] in ("UNITED STATES OF AMERICA", "UNITED STATES", "USA")


@needs_geo
@needs_10m
@needs_marine
@pytest.mark.parametrize("site", [(62.0107, -6.7741, "Denmark", "Torshavn"), (70.9333, -8.6667, "Norway", "Jan Mayen")],
                         ids=["torshavn", "janmayen"])
def test_a_high_latitude_island_is_framed_on_itself_at_9x16(site, tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(*site), (1080, 1920))
    assert lay["span_close"] < 3.0                            # it was 8 degrees, Europe scale
    assert lay["country"] is not None


@needs_geo
@needs_marine
def test_greenland_is_never_named_beside_svalbard(tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(83.6625, -33.37, "Greenland", "Cape Morris Jesup"), (1080, 1920))
    if lay["country"] is not None:
        chk = next(c for c in lay["name_checks"] if c["kind"] == "home")
        assert all(lo < -10.0 for lo, _la in chk["lonlat"]), chk["lonlat"]    # never out at 11 E, by Svalbard


@needs_geo
@needs_marine
@pytest.mark.parametrize("route", ["greece", "egypt"])
def test_the_routes_names_pass_the_verifier_and_are_recorded(route, tmp_path, monkeypatch) -> None:
    coords = {"greece": {"Athens": (37.9715, 23.7257), "Delphi": (38.4824, 22.5010), "Olympia": (37.6383, 21.6300),
                         "Knossos": (35.2980, 25.1631)},
              "egypt": {"Alexandria": (31.2001, 29.9187), "Giza": (29.9792, 31.1342), "Luxor": (25.7188, 32.6573),
                        "Aswan": (24.0889, 32.8998)}}[route]
    country = {"greece": "Greece", "egypt": "Egypt"}[route]

    def fake_locate(topic):
        la, lo = coords[topic["location"]["name"]]
        return {"lat": la, "lon": lo, "country": country, "label": topic["location"]["name"], "approximate": False,
                "qid": "x", "matched": "name"}
    monkeypatch.setattr(geo, "locate", fake_locate)
    if hasattr(graphics, "locate"):
        monkeypatch.setattr(graphics, "locate", fake_locate)
    stops = [{"place": n, "country": country, "label": n} for n in coords]
    for w, h in SIZES:
        _render(monkeypatch, lambda w=w, h=h: graphics.render_route(stops, tmp_path / f"r{w}.mp4", width=w, height=h),
                fps=1)
        lay = json.loads((tmp_path / f"r{w}.mp4.layout.json").read_text(encoding="utf-8"))
        assert lay["kind"] == "route"
        assert lay["name_checks"] is not None
        passed = {(c["kind"], c["text"]) for c in lay["name_checks"] if not c["fault"]}
        for wn in lay["water"]:
            assert ("water", wn.upper()) in passed
        assert not independent_name_faults(lay), independent_name_faults(lay)


# ---------------------------------------------------------------------------------------------- 16:9 timelines
E = lambda y, lab, a=False: {"year": y, "label": lab, "approximate": a}
TL = {
    "angkor": [E(1113, "Suryavarman II begins the temple"), E(1150, "The temple is finished", True),
               E(1431, "The Khmer capital moves south"), E(1860, "Henri Mouhot reaches the ruins"),
               E(1992, "Named a World Heritage Site")],
    "hagia": [E(537, "Justinian opens the great church"), E(558, "An earthquake brings down the dome"),
              E(1453, "Mehmed the Conqueror makes it a mosque"), E(1934, "It becomes a museum"),
              E(2020, "It is a mosque again")],
    "machu": [E(1450, "Pachacuti builds the royal estate", True), E(1572, "The site is abandoned", True),
              E(1911, "Hiram Bingham reaches the ruins"), E(1983, "Named a World Heritage Site")],
    "notredame": [E(1163, "Maurice de Sully lays the first stone"), E(1345, "The cathedral is finished"),
                  E(1804, "Napoleon is crowned emperor inside"), E(2019, "Fire destroys the spire"),
                  E(2024, "The cathedral reopens")],
}
# Round 15 (ruling R11, deliberate): the hard-coded list of known-unclean sets is gone; a span on two rows in the
# count-over-unit form ("72 / YEARS LATER") is clean, and every set is held to R11 (tests/test_round15_...).
KNOWN_UNCLEAN: set[str] = set()


@pytest.mark.parametrize("name", sorted(TL))
def test_16x9_timelines_are_clean_or_fail_loudly(name, tmp_path, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(TL[name], tmp_path / "t.mp4", width=1920,
                                                                   height=1080), fps=1)
    errs = [r.getMessage() for r in caplog.records if r.levelname == "ERROR" and graphics.TIMELINE_UNCLEAN in r.getMessage()]
    texts = [getattr(p, "text", "") for _n, p in st["plates"]]
    assert "hard failure" not in caplog.text                  # never a split name or a one-word middle row
    assert not errs, errs
    assert "LATER" not in texts                               # never "LATER" alone on a row (R11)
    # a two-row span never leaves "LATER" alone where the count can sit over "YEARS LATER"
    for t in texts:
        assert t not in ("YEARS",), texts
    if any(t == "LATER" for t in texts):
        assert errs


def test_a_two_row_span_keeps_years_later_together_first() -> None:
    assert graphics.wide_span_lines("ABOUT 120 YEARS LATER", 2) == ["ABOUT 120", "YEARS LATER"]
    # round 15 (ruling R11, deliberate): an exact count breaks before "YEARS", never before "LATER"
    assert graphics.wide_span_lines("72 YEARS LATER", True) == ["72", "YEARS LATER"]


# ---------------------------------------------------------------------------------------------- the sweep
CONTESTED_EDGE_DEG = 1e-6      # about 0.1 m: a site on a contested feature or on its boundary (0.00 km)


def _on_contested(lon: float, lat: float) -> bool:
    """Whether (lon, lat) lies on or at the edge of Natural Earth ground with a recorded claim (QF-20 ruling Q2),
    from the committed layer itself (map_layers.index), not from the code under test's answer."""
    return bool(len(map_layers.index("neutral").query(Point(lon, lat), predicate="dwithin",
                                                     distance=CONTESTED_EDGE_DEG)))


def _on_lake(lon: float, lat: float) -> bool:
    """Whether (lon, lat) lies on or at the edge of a Natural Earth lake (water, no country's ground)."""
    return bool(len(map_layers.index("water").query(Point(lon, lat), predicate="dwithin", distance=CONTESTED_EDGE_DEG)))


def _coastal_sites(n: int, seed: int = 1414) -> list[tuple[float, float, str, str]]:
    """`n` sites on the 1:10m coastline (a random vertex of a random 1:10m subunit polygon), seeded: the coastal
    sites off the 1:50m ground that the inland sweep never drew (how the cancelled home fill got through)."""
    rnd = random.Random(seed)
    units = [u for u in geo.countries_10m() if u.polygons]
    out: list[tuple[float, float, str, str]] = []
    tries = 0
    while len(out) < n and tries < 20000:
        tries += 1
        u = rnd.choice(units)
        poly = rnd.choice(u.polygons)
        if geo.ring_area(poly[0]) < 0.05:
            continue
        lon, lat = rnd.choice(poly[0])
        if abs(lat) > 80:
            continue
        if _on_contested(lon, lat) or _on_lake(lon, lat):
            continue                  # QF-20: ordinary ground only (contested ground: below; a lake is water)
        c = geo.find_country(u.name) or geo.find_country(u.sovereign)
        if c is None or not c.get("display"):
            continue
        g = graphics._site_island(lat, lon, c)
        if g is None or g[0] * 111.2 <= graphics.ISLAND_SNAP_KM:
            continue                                          # only sites off the 1:50m ground
        out.append((lat, lon, str(c["display"]), f"Coast {len(out) + 1}"))
    return out


COAST = _coastal_sites(16) if geo.available() and geo.COUNTRIES_10M_FILE.is_file() else []
EDGE = [(-34.8333, 20.0167, "South Africa", "Cape Agulhas"), (12.655, 43.415, "Yemen", "Perim"),
        (-53.1638, -70.9171, "Chile", "Punta Arenas"), (-66.6628, 140.0014, "Antarctica", "Dumont d'Urville"),
        (62.0107, -6.7741, "Denmark", "Torshavn"), (-29.056, 167.96, "Australia", "Kingston"),
        (-49.35, 70.22, "France", "Port-aux-Francais"), (83.6625, -33.37, "Greenland", "Cape Morris Jesup"),
        (-14.97, -147.63, "France", "Rangiroa"), (-55.98, -67.27, "Chile", "Cape Horn")]
SWEEP14 = EDGE + COAST


@needs_geo
@needs_10m
@needs_marine
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
@pytest.mark.parametrize("k", range(len(SWEEP14)))
def test_the_coastal_sweep_names_its_home_and_passes_an_independent_check(k, size, tmp_path, monkeypatch) -> None:
    lat, lon, country, label = SWEEP14[k]
    lay = _map(monkeypatch, tmp_path, P(lat, lon, country, label), size)
    assert lay["country"] is not None, (label, "home name dropped")     # never passes by dropping the name
    faults = independent_name_faults(lay)
    assert not faults, (label, faults)


# QF-20 ruling Q2 (2026-11-11): coastal sites on contested ground (inside the feature, or on its boundary) name no
# home, with the reason recorded, and pass the independent check; the coastal sweep above draws ordinary ground only
CONTESTED_COAST = [(27.009825, -10.921835, "W. Sahara", "Western Sahara coast"),
                   (35.584599, 77.017552, "Siachen Glacier", "Siachen Glacier"),
                   (45.320136, 32.591482, "Russia", "Crimea, on its boundary")]


@needs_geo
@needs_10m
@needs_marine
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
@pytest.mark.parametrize("k", range(len(CONTESTED_COAST)))
def test_a_coastal_site_on_contested_ground_names_no_home(k, size, tmp_path, monkeypatch) -> None:
    lat, lon, country, label = CONTESTED_COAST[k]
    assert _on_contested(lon, lat), label
    lay = _map(monkeypatch, tmp_path, P(lat, lon, country, label), size)
    assert lay["country"] is None and lay["home_drop"], (label, lay["country"])
    assert not geo_truth.faults(lay), (label, geo_truth.faults(lay))
