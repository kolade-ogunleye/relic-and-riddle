"""
src/geo.py
==========
Where a topic is, for the locator-map graphic.

- Coordinates come from Wikidata (CC0; property P625). A candidate is accepted only if it
  falls inside the topic's country on the Natural Earth map, so a namesake elsewhere in
  the world can't put the pin in the wrong place. Results are cached in data/geo_cache.json.
- Country outlines: Natural Earth 1:50m admin-0 countries (public domain), stored at
  tools/geo/ne_50m_admin_0_countries.geojson (fetch with scripts/fetch_geodata.py).
- Shaded relief: Natural Earth SR_HR.tif (or SR_50M.tif) in tools/geo/relief/ (fetch with scripts/fetch_relief.py).
- Marine polygons (the seas the map names) and the 1:10m countries (an island the 1:50m data leaves out), both
  Natural Earth from naciscdn.org: scripts/fetch_marine.py and scripts/fetch_geodata.py --10m.
Sources and terms: docs/sources.md.
"""

import itertools
import json
import logging
import math
import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple, NotRequired, TypedDict

import requests

from .config import BASE_DIR, DATA_DIR

log = logging.getLogger(__name__)

COUNTRIES_FILE = BASE_DIR / "tools" / "geo" / "ne_50m_admin_0_countries.geojson"
# Natural Earth shaded relief (public domain; fetch with scripts/fetch_relief.py). The 1:10m SR_HR (21600x10800,
# 60 px a degree) matches what the locator map samples (about 60 px a degree of latitude on its 9:16 close-up);
# the 1:50m SR_50M (10800x5400) is the fallback when only it is present (softer: upsampled about 2x).
RELIEF_HR_FILE = BASE_DIR / "tools" / "geo" / "relief" / "SR_HR.tif"
RELIEF_50M_FILE = BASE_DIR / "tools" / "geo" / "relief" / "SR_50M.tif"
FETCH_COUNTRIES_CMD = "python scripts/fetch_geodata.py"
FETCH_RELIEF_CMD = "python scripts/fetch_relief.py"


def _relief_file() -> Path:
    """SR_HR.tif if present, else SR_50M.tif if present, else the SR_HR path (missing: maps render flat)."""
    for p in (RELIEF_HR_FILE, RELIEF_50M_FILE):
        if p.is_file():
            return p
    return RELIEF_HR_FILE


RELIEF_FILE = _relief_file()
CACHE_FILE = DATA_DIR / "geo_cache.json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
NAME_FIELDS = ("NAME", "NAME_LONG", "ADMIN", "FORMAL_EN", "NAME_EN", "GEOUNIT")
ALIASES = {"turkiye": "turkey", "türkiye": "turkey", "usa": "united states of america", "united states": "united states of america",
           "uk": "united kingdom", "scotland": "united kingdom", "england": "united kingdom",
           "wales": "united kingdom", "czechia": "czech republic", "russia": "russian federation"}

Ring = list[tuple[float, float]]


def available() -> bool:
    return COUNTRIES_FILE.exists()


def missing_files() -> list[tuple[Path, str]]:
    """The geo files that are not on disk, each with the command that fetches it: without the country outlines
    no map is drawn; without the shaded relief maps render flat."""
    out: list[tuple[Path, str]] = []
    if not COUNTRIES_FILE.is_file():
        out.append((COUNTRIES_FILE, FETCH_COUNTRIES_CMD))
    if not (RELIEF_FILE.is_file() or _relief_file().is_file()):
        out.append((RELIEF_HR_FILE, FETCH_RELIEF_CMD))
    return out


class Country(TypedDict):
    """One Natural Earth country as countries() gives it (round 7: typed for the map code, no `Any`)."""
    names: set[str]
    sovereign: str
    polygons: list[list[list[tuple[float, float]]]]
    size: int
    display: str
    label_at: tuple[float, float] | None
    label_rank: int
    type: str
    extent: tuple[float, float, float, float]
    short_names: NotRequired[list[str]]     # round 10: shorter forms of `display` (short_names)
    full_name: NotRequired[str]             # round 14: `display` written out where Natural Earth abbreviates it
    key: NotRequired[str]                   # round 15: the entity's key (its ADMIN, or ADMIN/territory), shared by
                                            # its 1:50m and 1:10m forms (same_entity)


def short_names(props: dict[str, object]) -> list[str]:
    """Round 10 (review 3, R5): the shorter forms a country's name may be set in where its full name finds no place,
    longest first: Natural Earth's NAME_LONG where it is shorter than NAME ("United States" for "United States of
    America"), then an all-initials ABBREV without its stops ("U.S.A." -> "USA", "U.A.E." -> "UAE")."""
    name = str(props.get("NAME") or props.get("ADMIN") or "")
    out: list[str] = []
    long_ = str(props.get("NAME_LONG") or "")
    if long_ and len(long_) < len(name) and "." not in long_:
        out.append(long_)
    ab = str(props.get("ABBREV") or "")
    if re.fullmatch(r"(?:[A-Z]\.){2,4}", ab):
        out.append(ab.replace(".", ""))
    return [n for n in out if n.upper() != name.upper()]


def full_name(props: dict[str, object]) -> str:
    """Round 14 (review: "FR. S. ANTARCTIC LANDS", "MARSHALL IS."): the country's name written out, for its own
    name on a locator: Natural Earth's ADMIN (else NAME_LONG) where its NAME is abbreviated (holds a stop), else
    NAME. "Faeroe Is." -> "Faroe Islands", "Fr. S. Antarctic Lands" -> "French Southern and Antarctic Lands"."""
    name = str(props.get("NAME") or props.get("ADMIN") or "")
    if "." not in name:
        return name
    for key in ("ADMIN", "NAME_LONG"):
        v = str(props.get(key) or "")
        if v and "." not in v:
            return v
    return name


@lru_cache(maxsize=1)
def countries_50m() -> list[Country]:
    """The 1:50m countries: [{"names": {...lowercase}, "polygons": [[outer ring, hole rings...], ...]}]. Round 15:
    the globe and any small-scale view draw these; a locator's close-up draws the 1:10m entities (detail_view)."""
    data = json.loads(COUNTRIES_FILE.read_text(encoding="utf-8"))
    out: list[Country] = []
    for f in data["features"]:
        props, geom = f["properties"], f["geometry"]
        if not geom:
            continue
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        names = {str(props.get(k, "")).strip().lower() for k in NAME_FIELDS if props.get(k)}
        polygons = [[[(x, y) for x, y in ring] for ring in poly] for poly in polys]
        label_at = (props.get("LABEL_X"), props.get("LABEL_Y"))
        out.append({"names": names, "sovereign": str(props.get("SOVEREIGNT", "")).strip().lower(),
                    "polygons": polygons, "size": sum(len(poly[0]) for poly in polygons),
                    "display": props.get("NAME") or props.get("ADMIN") or "",
                    "label_at": label_at if None not in label_at else None,
                    "label_rank": props.get("LABELRANK") or 9,
                    "type": str(props.get("TYPE") or ""),
                    "short_names": clean_forms(short_names(props)),
                    "full_name": full_name(props),
                    "key": fold(str(props.get("ADMIN") or props.get("NAME") or "")).lower(),
                    "extent": (min(x for poly in polygons for x, _ in poly[0]), min(y for poly in polygons for _, y in poly[0]),
                               max(x for poly in polygons for x, _ in poly[0]), max(y for poly in polygons for _, y in poly[0]))})
    _add_ice_shelves(out)
    return out


_DETAIL: list[bool] = [False]


def countries() -> list[Country]:
    """The country list the map draws: the 1:50m countries (countries_50m), or, inside detail_view, the 1:10m
    entities (countries_detail), so every polygon a close-up draws, names and checks is one entity's own 1:10m
    ground."""
    if _DETAIL[0]:
        got = countries_detail()
        if got:
            return got
    return countries_50m()


@contextmanager
def detail_view() -> Iterator[bool]:
    """Round 15 (coordinator directive 1): countries() gives the 1:10m entities (countries_detail) while the block
    runs; yields whether it does (not where the 1:10m file is missing, or where a caller has put its own country
    list in place of countries(): a test's stand-in is drawn as given)."""
    own = globals().get("countries") is _COUNTRIES_ENTRY and COUNTRIES_10M_FILE.is_file() and bool(countries_detail())
    prev = _DETAIL[0]
    _DETAIL[0] = own or prev
    _VIEW_CACHE.clear()
    try:
        yield _DETAIL[0]
    finally:
        _DETAIL[0] = prev
        _VIEW_CACHE.clear()


def fold(text: str) -> str:
    """`text` without its accents (São Tomé -> Sao Tome, Åland -> Aland): NFKD, combining marks dropped."""
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def clean_name(text: str) -> bool:
    """Whether `text` reads as a written-out name: no abbreviation's stop, no initialism (two or more capitals
    together: CR, TL, PNG, UK, PDR, S.A.R) and no alternative after a slash."""
    t = str(text or "").strip()
    return bool(t) and "." not in t and "/" not in t and not re.search(r"\b[A-Z]{2,}\b", t)


def clean_forms(forms: Sequence[str]) -> list[str]:
    """The written-out names of `forms` (clean_name), accent-folded, in order, once each."""
    out: list[str] = []
    for f in forms:
        f_ = fold(str(f)).strip()
        if clean_name(f_) and f_.lower() not in {o.lower() for o in out}:
            out.append(f_)
    return out


# ------------------------------------------------------------------ ice shelves (round 12, 2026-11-04)
# Natural Earth's 1:50m country outlines end Antarctica at its grounding line, so its ice shelves (the Ross Ice
# Shelf, some 900 km south of Ross Island) were drawn as open sea and the Ross Sea's name was set on the ice. The
# 1:50m ice-shelf polygons (public domain, fetched with the marine polygons by scripts/fetch_marine.py) are added
# to Antarctica's ground; without the file the shelves stay unmarked (logged once).
ICE_SHELVES_FILE = BASE_DIR / "tools" / "geo" / "ne_50m_antarctic_ice_shelves_polys.geojson"
_ICE_MISSING_LOGGED: list[bool] = []


def ice_shelf_polygons() -> list[list[Ring]]:
    """The Antarctic ice shelves of ICE_SHELVES_FILE as polygons (their outer rings); [] where the file is missing
    (logged once) or unreadable."""
    if not ICE_SHELVES_FILE.is_file():
        if not _ICE_MISSING_LOGGED:
            _ICE_MISSING_LOGGED.append(True)
            log.info("Map: %s is missing (fetch with %s); Antarctica's ice shelves are drawn as sea.",
                     ICE_SHELVES_FILE, FETCH_MARINE_CMD)
        return []
    try:
        data = json.loads(ICE_SHELVES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("Map: %s is unreadable (%s); Antarctica's ice shelves are drawn as sea.", ICE_SHELVES_FILE, e)
        return []
    out: list[list[Ring]] = []
    for f in data.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
            # the outer ring alone: a shelf's holes are its ice rises (Roosevelt Island), grounded ice the 1:50m
            # outline leaves out too, so they are ground, not sea
            if poly and len(poly[0]) >= 4:
                out.append([[(float(x), float(y)) for x, y in poly[0]]])
    return out


def _add_ice_shelves(cs: list[Country]) -> None:
    """Antarctica's ice shelves (ice_shelf_polygons) added to its polygons in `cs`, its size and extent updated."""
    ant = next((c for c in cs if "antarctica" in c["names"]), None)
    if ant is None:
        return
    shelves = ice_shelf_polygons()
    if not shelves:
        return
    ant["polygons"].extend(shelves)
    ant["size"] += sum(len(p[0]) for p in shelves)
    xs = [x for poly in ant["polygons"] for x, _ in poly[0]]
    ys = [y for poly in ant["polygons"] for _, y in poly[0]]
    ant["extent"] = (min(xs), min(ys), max(xs), max(ys))


_VIEW_CACHE: list[tuple[list["Country"], tuple[tuple[tuple[float, float, float, float], ...], ...],
                        frozenset[tuple[int, int]]]] = []


def _view_index(cs: list["Country"]) -> tuple[tuple[tuple[tuple[float, float, float, float], ...], ...],
                                              frozenset[tuple[int, int]]]:
    """(each polygon's outer-ring extent (lon0, lat0, lon1, lat1), the enclaves) of the country list `cs`, kept for
    the list countries() returns (recomputed for any other, as a test's own list)."""
    if _VIEW_CACHE and _VIEW_CACHE[0][0] is cs:
        return _VIEW_CACHE[0][1], _VIEW_CACHE[0][2]
    ext = tuple(tuple((min(x for x, _ in poly[0]), min(y for _, y in poly[0]), max(x for x, _ in poly[0]),
                       max(y for _, y in poly[0])) for poly in c["polygons"]) for c in cs)
    holes: list[tuple[int, tuple[float, float, float, float]]] = []
    for ci, c in enumerate(cs):
        for poly in c["polygons"]:
            for hole in poly[1:]:
                holes.append((ci, (min(x for x, _ in hole), min(y for _, y in hole), max(x for x, _ in hole),
                                   max(y for _, y in hole))))
    encl = set()
    for ci, polys in enumerate(ext):
        for pi, e in enumerate(polys):
            for hc, h in holes:
                if hc != ci and h[0] - 1e-6 <= e[0] and e[2] <= h[2] + 1e-6 and h[1] - 1e-6 <= e[1] and e[3] <= h[3] + 1e-6:
                    encl.add((ci, pi))
                    break
    _VIEW_CACHE[:] = [(cs, ext, frozenset(encl))]
    return ext, frozenset(encl)


def enclaves() -> frozenset[tuple[int, int]]:
    """(country index, polygon index) of every polygon that lies inside another country's hole (round 8 review:
    Uzbekistan's Sokh and Shakhimardan in Kyrgyzstan's holes were cut out of the land as sea): a drawing pass
    draws them after every other polygon, so the hole is filled with its own country's ground."""
    return _view_index(countries())[1]


def view_polygons(lon0: float, lat0: float, lon_half: float, lat_half: float
                  ) -> list[tuple[Country, list[Ring]]]:
    """Every country polygon (outer ring, holes) that reaches the view lon0 +- lon_half, lat0 +- lat_half, in
    drawing order: the enclaves (enclaves()) last. Round 8 (review 6): where the view crosses the antimeridian a
    polygon is also given shifted by 360 degrees, and where it passes a pole (an equirectangular view past -90 or
    90) mirrored over it ((lon + 180, -180 - lat) is the same point of the sphere), so Antarctica no longer ends in
    a straight edge at 180 degrees and at the pole line. O(n) over the countries' extents, then the polygons near
    the view."""
    lo_x, hi_x, lo_y, hi_y = lon0 - lon_half, lon0 + lon_half, lat0 - lat_half, lat0 + lat_half
    shifts = [0.0] + ([360.0] if hi_x > 180 else []) + ([-360.0] if lo_x < -180 else [])
    mirrors = [None] + (["s"] if lo_y < -90 else []) + (["n"] if hi_y > 90 else [])
    cs = countries()
    ext, encl = _view_index(cs)
    first: list[tuple[Country, list[Ring]]] = []
    last: list[tuple[Country, list[Ring]]] = []
    for ci, c in enumerate(cs):
        for pi, poly in enumerate(c["polygons"]):
            e = ext[ci][pi]
            for mirror in mirrors:
                if mirror is None:
                    me = e
                elif mirror == "s":
                    if e[1] > -60:
                        continue
                    me = (e[0] + 180, -180 - e[3], e[2] + 180, -180 - e[1])
                else:
                    if e[3] < 60:
                        continue
                    me = (e[0] + 180, 180 - e[3], e[2] + 180, 180 - e[1])
                for base in ((0.0, -360.0) if mirror else (0.0,)):
                    for sh in shifts:
                        dx = base + sh
                        if me[2] + dx < lo_x or me[0] + dx > hi_x or me[3] < lo_y or me[1] > hi_y:
                            continue
                        if mirror is None and dx == 0:
                            rings = poly
                        elif mirror is None:
                            rings = [[(x + dx, y) for x, y in r] for r in poly]
                        elif mirror == "s":
                            rings = [[(x + 180 + dx, -180 - y) for x, y in r] for r in poly]
                        else:
                            rings = [[(x + 180 + dx, 180 - y) for x, y in r] for r in poly]
                        (last if (ci, pi) in encl else first).append((c, rings))
    return first + last


def seam_edges(ring: Ring, tol: float = 1e-6) -> list[bool]:
    """Round 9 (review 4): for each edge of `ring` (ring[i] to ring[i + 1], the last one closing it), whether it
    is a cut of the data, not a coast or a border: along a pole (both ends at latitude +-90) or along the
    antimeridian (both ends on one meridian a multiple of 180 degrees: +-180, and 0 or 360 for a copy mirrored past
    a pole by view_polygons). A view across those lines draws the polygon's copies side by side, so the cut is
    no edge of the land and is never stroked. Round 15: nor is an edge two subunits of one 1:10m entity share
    (_INTERNAL_EDGES: England and Scotland are one country's ground). O(n)."""
    n = len(ring)
    out = []
    internal = _INTERNAL_EDGES if _DETAIL[0] else set()
    for i in range(n):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % n]
        pole = abs(abs(y0) - 90.0) < 0.01 and abs(abs(y1) - 90.0) < 0.01   # Natural Earth: -89.998926
        meridian = abs(x0 - x1) < tol and abs(x0 / 180.0 - round(x0 / 180.0)) < 1e-6
        shared = False
        if internal:
            a_, b_ = (x0, y0), (x1, y1)
            shared = ((a_, b_) if a_ < b_ else (b_, a_)) in internal
        out.append(pole or meridian or shared)
    return out


def find_country(name: str) -> Country | None:
    """The country itself by name; a sovereign-state match (e.g. a territory) only as a last resort,
    and then the largest one, so "UK" is Great Britain, not South Georgia."""
    key = ALIASES.get(name.strip().lower(), name.strip().lower())
    exact = [c for c in countries() if key in c["names"]]
    pool = exact or [c for c in countries() if c["sovereign"] == key]
    return max(pool, key=lambda c: c["size"]) if pool else None


def _in_ring(lon: float, lat: float, ring: Ring) -> bool:
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def in_country(lon: float, lat: float, country: dict[str, Any], tolerance_deg: float = 1.0) -> bool:
    """Point-in-polygon (holes respected). Offshore sites (wrecks, bays, straits) count when
    within about tolerance_deg (~100 km) of the coast; a namesake elsewhere is far further."""
    offsets = [(0.0, 0.0)] + [(r * dx, r * dy) for r in (0.15, tolerance_deg / 2, tolerance_deg)
                              for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7))]
    for poly in country["polygons"]:
        for dx, dy in offsets:
            if _in_ring(lon + dx, lat + dy, poly[0]) and not any(_in_ring(lon + dx, lat + dy, h) for h in poly[1:]):
                return True
    return False


def _get(params: dict) -> dict:
    from .commons import (
        HEADERS,  # same identifying User-Agent as the Commons client (API etiquette)
    )
    for attempt in range(3):
        resp = requests.get(WIKIDATA_API, params={**params, "format": "json", "maxlag": 5}, headers=HEADERS, timeout=30)
        if resp.status_code in (429, 503) or "maxlag" in resp.text[:200]:
            time.sleep(5 * 2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def _load_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _wikidata_point(query: str, country: dict[str, Any], country_name: str) -> dict[str, Any] | None:
    found = _get({"action": "wbsearchentities", "search": query, "language": "en", "limit": 7, "type": "item"})
    ids = [r["id"] for r in found.get("search", [])]
    if not ids:
        return None
    ents = _get({"action": "wbgetentities", "ids": "|".join(ids), "props": "claims", "languages": "en"})
    for qid in ids:  # search order = relevance
        for c in ents.get("entities", {}).get(qid, {}).get("claims", {}).get("P625", []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
            if "latitude" in v and in_country(v["longitude"], v["latitude"], country):
                return {"lat": v["latitude"], "lon": v["longitude"], "qid": qid, "country": country_name}
    return None


def locate(topic: dict[str, Any]) -> dict[str, Any] | None:
    """{"lat", "lon", "qid", "label", "country", "approximate", "matched"} for the topic's place, or None if no
    Wikidata candidate falls inside the stated country. `matched` says which of the topic's names the point is for
    ("name", "city" or "region"; 2026-11-02: the map draws a region's point as an area, not a precise pin)."""
    loc = topic.get("location", {})
    name, country_name = (loc.get("name") or "").strip(), (loc.get("country") or "").strip()
    if not (name and country_name and available()):
        return None
    cache = _load_cache()
    key = f"{name}|{country_name}".lower()
    if key in cache:
        return cache[key]
    country = find_country(country_name)
    if country is None:
        log.warning("Map: country '%s' not found in Natural Earth names.", country_name)
        return None
    # The topic bank's own coordinates (reviewed with the topic) come first, if they check out.
    stored = (loc.get("latitude"), loc.get("longitude"))
    if None not in stored and any(float(v) != 0.0 for v in stored):  # 0,0 is a placeholder ("Null Island")
        lat, lon = float(stored[0]), float(stored[1])
        if in_country(lon, lat, country):
            return {"lat": lat, "lon": lon, "qid": "", "label": name, "country": country_name, "approximate": False,
                    "matched": "name"}
        log.warning("Map: stored coordinates for '%s' are not in %s; trying Wikidata.", name, country_name)
    result = None
    # The site's own name first, then its city and region (a coarser but still correct pin).
    for kind, query in [(k, q) for k, q in (("name", name), ("city", loc.get("city")), ("region", loc.get("region")))
                        if q]:
        try:
            result = _wikidata_point(query, country, country_name)
        except requests.RequestException as e:
            log.warning("Map: Wikidata lookup failed (%s).", e)
            return None
        if result:
            # The pin says what it actually marks: the site, or the city/region used instead.
            result["label"], result["approximate"], result["matched"] = query, query != name, kind
            break
    if result is None:
        log.warning("Map: no Wikidata coordinates for '%s' inside %s; skipping the map.", name, country_name)
        return None
    cache[key] = result
    CACHE_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def is_region_fallback(place: dict[str, Any]) -> bool:
    """True when `place` stands for the topic's whole region, not the site or its city (locate fell back to the
    region's point): recorded as matched "region", or, for a point cached before `matched` was kept, approximate
    and labelled with the region's own name."""
    if place.get("matched"):
        return place["matched"] == "region"
    region = str(place.get("region") or "").strip().lower()
    return bool(place.get("approximate")) and bool(region) and str(place.get("label") or "").strip().lower() == region


def point_in_polygons(x: float, y: float, polygons: list[list[Ring]]) -> bool:
    """Point-in-polygon (holes respected) in any one plane: lon/lat or projected pixels alike (2026-11-02: the map
    checks that a country's name moved off a route still lies on that country's land)."""
    return any(_in_ring(x, y, poly[0]) and not any(_in_ring(x, y, h) for h in poly[1:]) for poly in polygons)


def orthographic(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float, bool]:
    """(x, y, visible) of a point on the unit orthographic globe centred on (lon0, lat0): x east, y north, in
    -1..1; `visible` false on the far hemisphere (round 4, 2026-11-02: the maps' context globe inset)."""
    import math
    la, lo, la0 = math.radians(lat), math.radians(lon - lon0), math.radians(lat0)
    cos_c = math.sin(la0) * math.sin(la) + math.cos(la0) * math.cos(la) * math.cos(lo)
    x = math.cos(la) * math.sin(lo)
    y = math.cos(la0) * math.sin(la) - math.sin(la0) * math.cos(la) * math.cos(lo)
    return x, y, cos_c > 0


def globe_rings(lon0: float, lat0: float, step: int = 4) -> list[tuple[dict[str, Any], list[list[tuple[float, float]]]]]:
    """Every country's outer rings on the orthographic globe centred on (lon0, lat0), as unit (x, y) points, each
    ring cut to its visible part (every `step`-th vertex: the inset is a few hundred pixels across)."""
    out = []
    for c in (countries_50m() if _DETAIL[0] else countries()):    # round 15: the globe is a small scale: 1:50m
        rings = []
        for poly in c["polygons"]:
            pts = [orthographic(x, y, lon0, lat0) for x, y in poly[0][::step]]
            vis = [(x, y) for x, y, v in pts if v]
            if len(vis) >= 3:
                rings.append(vis)
        if rings:
            out.append((c, rings))
    return out


# ------------------------------------------------------------------ pole of inaccessibility (round 6, 2026-11-03)
Point = tuple[float, float]
Box = tuple[float, float, float, float]


def clip_ring(ring: Sequence[Point], box: Box) -> Ring:
    """`ring` cut to the axis-aligned `box` (x0, y0, x1, y1), Sutherland-Hodgman (round 6, 2026-11-03: the country
    name is set at the pole of the frame-clipped home polygon, so a country running off the frame is labelled where
    the viewer sees it). O(n) per ring; an empty list where nothing of it is inside."""
    x0, y0, x1, y1 = box
    pts: Ring = [(float(x), float(y)) for x, y in ring]
    edges = ((lambda p: p[0] >= x0, lambda a, b: _cut_x(a, b, x0)), (lambda p: p[0] <= x1, lambda a, b: _cut_x(a, b, x1)),
             (lambda p: p[1] >= y0, lambda a, b: _cut_y(a, b, y0)), (lambda p: p[1] <= y1, lambda a, b: _cut_y(a, b, y1)))
    for inside, cut in edges:
        if not pts:
            break
        out: Ring = []
        prev = pts[-1]
        for cur in pts:
            if inside(cur):
                if not inside(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cut(prev, cur))
            prev = cur
        pts = out
    return pts


def _cut_x(a: Point, b: Point, x: float) -> Point:
    f = (x - a[0]) / ((b[0] - a[0]) or 1e-12)
    return x, a[1] + (b[1] - a[1]) * f


def _cut_y(a: Point, b: Point, y: float) -> Point:
    f = (y - a[1]) / ((b[1] - a[1]) or 1e-12)
    return a[0] + (b[0] - a[0]) * f, y


def ring_area(ring: Sequence[Point]) -> float:
    """The unsigned shoelace area of one ring."""
    n = len(ring)
    return abs(sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))) / 2


class _Edges:
    """Every edge of a set of rings as flat arrays, for the signed distance (even-odd inside test)."""

    def __init__(self, rings: Sequence[Sequence[Point]]) -> None:
        import numpy as np
        ax: list[float] = []
        ay: list[float] = []
        bx: list[float] = []
        by: list[float] = []
        for ring in rings:
            n = len(ring)
            for i in range(n):
                (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % n]
                ax.append(x0)
                ay.append(y0)
                bx.append(x1)
                by.append(y1)
        self.ax, self.ay = np.asarray(ax, np.float64), np.asarray(ay, np.float64)
        self.bx, self.by = np.asarray(bx, np.float64), np.asarray(by, np.float64)
        self.dx, self.dy = self.bx - self.ax, self.by - self.ay
        self.l2 = np.where(self.dx ** 2 + self.dy ** 2 > 0, self.dx ** 2 + self.dy ** 2, 1.0)

    def signed(self, x: float, y: float) -> float:
        """Distance to the nearest edge, positive inside (an odd number of rings round the point)."""
        import numpy as np
        f = np.clip(((x - self.ax) * self.dx + (y - self.ay) * self.dy) / self.l2, 0.0, 1.0)
        d = float(np.sqrt(np.min((self.ax + f * self.dx - x) ** 2 + (self.ay + f * self.dy - y) ** 2)))
        crosses = (self.ay > y) != (self.by > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = self.ax + (y - self.ay) * self.dx / np.where(self.dy != 0, self.dy, 1e-12)
        inside = int(np.count_nonzero(crosses & (x < xs))) % 2 == 1
        return d if inside else -d


def _avoid_distance(x: float, y: float, boxes: Sequence[Box], circles: Sequence[tuple[float, float, float]]) -> float:
    """The signed distance from (x, y) to the nearest region it must keep out of (negative inside one)."""
    best = math.inf
    for bx0, by0, bx1, by1 in boxes:
        ox, oy = max(bx0 - x, 0.0, x - bx1), max(by0 - y, 0.0, y - by1)
        d = math.hypot(ox, oy) if ox or oy else -min(x - bx0, bx1 - x, y - by0, by1 - y)
        best = min(best, d)
    for cx, cy, r in circles:
        best = min(best, math.hypot(x - cx, y - cy) - r)
    return best


def polylabel(rings: Sequence[Sequence[Point]], precision: float = 1.0, avoid: Sequence[Box] = (),
              avoid_circles: Sequence[tuple[float, float, float]] = ()) -> tuple[float, float, float] | None:
    """(x, y, radius): the pole of inaccessibility of the region `rings` bound (even-odd: holes and several parts
    alike), the inside point furthest from any edge, and that distance, to within `precision` (round 6, 2026-11-03:
    where the map sets the home country's name). A quadtree of cells searched best-first from a priority queue
    (Mapbox's polylabel): a cell is split only while its bound (its centre's distance plus its half-diagonal) can
    still beat the best found by `precision`, so the search is O(c log c) in the cells it opens. `avoid` boxes and
    `avoid_circles` (cx, cy, r) count as edges too (the marker and its label), so the point keeps clear of them.
    None for no ring, a degenerate one, or a region no point of which is clear."""
    import heapq
    rings = [list(r) for r in rings if len(r) >= 3 and ring_area(r) > 1e-9]
    if not rings:
        return None
    xs = [x for r in rings for x, _ in r]
    ys = [y for r in rings for _, y in r]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    w, h = x1 - x0, y1 - y0
    size = min(w, h)
    if size <= 0:
        return None
    edges = _Edges(rings)

    def dist(x: float, y: float) -> float:
        d = edges.signed(x, y)
        if avoid or avoid_circles:
            d = min(d, _avoid_distance(x, y, avoid, avoid_circles))
        return d

    heap: list[tuple[float, float, float, float, float]] = []   # (-bound, -d, x, y, half)

    def push(x: float, y: float, half: float) -> None:
        d = dist(x, y)
        heapq.heappush(heap, (-(d + half * math.sqrt(2)), -d, x, y, half))
    half = size / 2
    cx = x0 + half
    while cx < x1 + half:
        cy = y0 + half
        while cy < y1 + half:
            push(cx, cy, half)
            cy += size
        cx += size
    # the area-weighted centroid of the outer ring, and the box's centre, as the first best guesses
    best_x, best_y = x0 + w / 2, y0 + h / 2
    best_d = dist(best_x, best_y)
    ring0 = max(rings, key=ring_area)
    a = sum(ring0[i][0] * ring0[i - 1][1] - ring0[i - 1][0] * ring0[i][1] for i in range(len(ring0))) / 2
    if abs(a) > 1e-9:
        gx = sum((ring0[i][0] + ring0[i - 1][0]) * (ring0[i][0] * ring0[i - 1][1] - ring0[i - 1][0] * ring0[i][1])
                 for i in range(len(ring0))) / (6 * a)
        gy = sum((ring0[i][1] + ring0[i - 1][1]) * (ring0[i][0] * ring0[i - 1][1] - ring0[i - 1][0] * ring0[i][1])
                 for i in range(len(ring0))) / (6 * a)
        gd = dist(gx, gy)
        if gd > best_d:
            best_x, best_y, best_d = gx, gy, gd
    while heap:
        neg_bound, neg_d, x, y, half = heapq.heappop(heap)
        d = -neg_d
        if d > best_d:
            best_x, best_y, best_d = x, y, d
        if -neg_bound - best_d <= precision:
            continue
        half /= 2
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            push(x + sx * half, y + sy * half, half)
    if best_d <= 0:
        return None
    return best_x, best_y, best_d


# ------------------------------------------------------------------ shapefiles (round 11, 2026-11-04)
# Natural Earth's marine polygons and its 1:10m countries come from naciscdn.org as zipped shapefiles; the fetch
# scripts read them with read_shapefile_zip (no shapefile library is a dependency) and save GeoJSON beside the
# 1:50m countries, which is all the map reads at run time.
SHP_POLYGON_TYPES = frozenset({5, 15, 25})     # Polygon, PolygonZ, PolygonM


def _shp_rings(content: bytes) -> list[Ring]:
    """The rings of one polygon record's content (after its 8-byte record header): its parts' points."""
    import struct
    shape = struct.unpack_from("<i", content, 0)[0]
    if shape == 0:
        return []
    if shape not in SHP_POLYGON_TYPES:
        raise ValueError(f"shape type {shape} is not a polygon")
    n_parts, n_pts = struct.unpack_from("<ii", content, 36)
    if n_parts < 0 or n_pts < 0 or 44 + 4 * n_parts + 16 * n_pts > len(content):
        raise ValueError("polygon record is truncated")
    parts = list(struct.unpack_from(f"<{n_parts}i", content, 44)) + [n_pts]
    xy = struct.unpack_from(f"<{2 * n_pts}d", content, 44 + 4 * n_parts)
    rings: list[Ring] = []
    for a, b in itertools.pairwise(parts):
        if not 0 <= a <= b <= n_pts:
            raise ValueError("polygon part index out of range")
        ring = [(round(xy[2 * i], 6), round(xy[2 * i + 1], 6)) for i in range(a, b)]
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def _signed_area(ring: Sequence[Point]) -> float:
    n = len(ring)
    return sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n)) / 2


def shp_polygons(rings: list[Ring]) -> list[list[Ring]]:
    """A shapefile polygon's rings grouped as GeoJSON MultiPolygon coordinates: each outer ring (clockwise: a
    negative shoelace area, the shapefile rule) with the holes (anticlockwise) whose first point lies inside it,
    the smallest such outer ring. O(h o) for h holes and o outer rings, each tried by its extent first."""
    outers = [r for r in rings if _signed_area(r) < 0]
    holes = [r for r in rings if _signed_area(r) >= 0]
    if not outers:                     # a file wound the other way: every ring an outer one
        return [[r] for r in holes]
    ext = [(min(x for x, _ in r), min(y for _, y in r), max(x for x, _ in r), max(y for _, y in r)) for r in outers]
    polys: list[list[Ring]] = [[r] for r in outers]
    for h in holes:
        x, y = h[0]
        best = None
        for i, (r, e) in enumerate(zip(outers, ext)):
            if (e[0] <= x <= e[2] and e[1] <= y <= e[3] and _in_ring(x, y, r)
                    and (best is None or ring_area(r) < ring_area(outers[best]))):
                best = i
        if best is None:
            polys.append([h])          # a hole in no outer ring is kept as land of its own (never dropped)
        else:
            polys[best].append(h)
    return polys


def _dbf_records(data: bytes, encoding: str = "utf-8") -> list[dict[str, str]]:
    """Every record of a dBASE III table (a shapefile's .dbf) as {field: stripped text}."""
    import struct
    if len(data) < 32:
        raise ValueError("dbf header is truncated")
    n_rec, head_len, rec_len = struct.unpack_from("<IHH", data, 4)
    fields: list[tuple[str, int]] = []
    pos = 32
    while pos + 32 <= head_len and data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0", 1)[0].decode("ascii", "replace")
        fields.append((name, data[pos + 16]))
        pos += 32
    if head_len + n_rec * rec_len > len(data):
        raise ValueError("dbf records are truncated")
    out: list[dict[str, str]] = []
    for i in range(n_rec):
        rec = data[head_len + i * rec_len: head_len + (i + 1) * rec_len]
        row: dict[str, str] = {}
        at = 1                                   # byte 0: the deletion flag
        for name, size in fields:
            row[name] = rec[at:at + size].decode(encoding, "replace").replace("\0", "").strip()
            at += size
        out.append(row)
    return out


def read_shapefile_zip(data: bytes, stem: str, max_bytes: int = 200_000_000) -> dict[str, Any]:
    """A zipped polygon shapefile (`stem`.shp and `stem`.dbf, as Natural Earth ships them) as a GeoJSON
    FeatureCollection of MultiPolygons, properties as text. Parsed by struct alone, members bounded by `max_bytes`
    uncompressed; ValueError on anything but polygons, a member over the bound, a .cpg naming an unknown encoding
    or a .dbf whose records do not match the .shp's."""
    import io
    import struct
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        def member(ext: str) -> bytes:
            info = z.getinfo(f"{stem}.{ext}")
            if info.file_size > max_bytes:
                raise ValueError(f"{stem}.{ext} is over {max_bytes} bytes")
            return z.read(info)
        shp, dbf = member("shp"), member("dbf")
        try:
            enc = member("cpg").decode("ascii").strip() or "utf-8"
        except KeyError:
            enc = "utf-8"
    if len(shp) < 100 or struct.unpack_from(">i", shp, 0)[0] != 9994:
        raise ValueError("not a shapefile")
    import codecs
    try:
        codecs.lookup(enc)
    except LookupError:
        raise ValueError(f"{stem}.cpg names an unknown encoding {enc!r}") from None
    records = _dbf_records(dbf, "utf-8" if enc.lower().replace("-", "") in ("utf8", "") else enc)
    feats: list[dict[str, Any]] = []
    pos, i = 100, 0
    while pos + 8 <= len(shp):
        size = struct.unpack_from(">i", shp, pos + 4)[0] * 2
        content = shp[pos + 8: pos + 8 + size]
        pos += 8 + size
        if i >= len(records):
            raise ValueError("the .shp has more records than the .dbf")
        rings = _shp_rings(content)
        props = records[i]
        i += 1
        if not rings:
            continue
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "MultiPolygon", "coordinates": shp_polygons(rings)}})
    if i != len(records):
        raise ValueError("the .dbf has more records than the .shp")
    return {"type": "FeatureCollection", "features": feats}


# ------------------------------------------------------------------ marine polygons (round 11, 2026-11-04)
# Natural Earth's 1:10m marine polygons (public domain): the water bodies' own areas, so a sea is named only
# inside itself (the nearest-anchor naming put PACIFIC OCEAN on Portobelo's Caribbean). Fetched by
# scripts/fetch_marine.py; without the file the map names no water (logged), never a guess.
MARINE_FILE = BASE_DIR / "tools" / "geo" / "ne_10m_geography_marine_polys.geojson"
FETCH_MARINE_CMD = "python scripts/fetch_marine.py"
MARINE_CLASSES = frozenset({"ocean", "sea", "gulf", "bay", "strait", "channel", "sound", "fjord"})
# round 13 (review: the Skagerrak, Skagen's own water, is Natural Earth's class "fjord" and went unnamed)
MARINE_DENSIFY_DEG = 1.0     # round 13 (coordinator directive 1): no marine edge longer than this, in degrees of
                             # lon or lat or of arc, before any projection (the 60.53 S Indian/Southern boundary is
                             # one 146.5-degree edge; on a polar view it became a chord across the Southern Ocean)
OCEAN_HALVES = (("North Atlantic Ocean", "South Atlantic Ocean"), ("North Pacific Ocean", "South Pacific Ocean"))
                             # round 13 (directive 1): split at the equator, as the IHO does (Natural Earth's South
                             # Pacific reaches 3.65 N: Tarawa, 1.35 N, read SOUTH PACIFIC OCEAN)
MARINE_MAX_RANK = 7          # Natural Earth's scalerank: 0-1 oceans, 2-3 seas and big gulfs, 4-5 gulfs and bays;
                             # round 12 (review: the Sea of Marmara, 6, and the Alboran Sea, 7, went unnamed) 6-7
MARINE_MINOR_RANK = 6        # the smaller seas and straits, named only where their water fills much of the view


class Marine(TypedDict):
    name: str
    featurecla: str
    scalerank: int
    polygons: list[list[Ring]]
    extent: tuple[float, float, float, float]


_MARINE_MISSING_LOGGED: list[bool] = []


@lru_cache(maxsize=1)
def marine_polygons() -> tuple[Marine, ...]:
    """The named water bodies of MARINE_FILE of a class in MARINE_CLASSES and scalerank up to MARINE_MAX_RANK, the
    largest (lowest scalerank) first; () where the file is missing (logged once: no water is named)."""
    if not MARINE_FILE.is_file():
        if not _MARINE_MISSING_LOGGED:
            _MARINE_MISSING_LOGGED.append(True)
            log.warning("Map: %s is missing (fetch with %s); no water is named.", MARINE_FILE, FETCH_MARINE_CMD)
        return ()
    data = json.loads(MARINE_FILE.read_text(encoding="utf-8"))
    out: list[Marine] = []
    for f in data.get("features", []):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        cla = str(p.get("featurecla") or "").strip().lower()
        # the English name (Golfo de Panama: Gulf of Panama), but an ocean's own, which says North or South
        name = str((p.get("name") if cla == "ocean" else p.get("name_en")) or p.get("name") or "").strip()
        name = " ".join(w if w.lower() in ("of", "de", "the") else w[:1].upper() + w[1:].lower() if w.isupper() else w
                        for w in name.split())
        try:
            rank = int(float(p.get("scalerank") or 9))
        except ValueError:
            rank = 9
        if not name or cla not in MARINE_CLASSES or rank > MARINE_MAX_RANK or g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        polygons = [[densify_ring([(float(x), float(y)) for x, y in ring]) for ring in poly] for poly in polys]
        out.append({"name": name, "featurecla": cla, "scalerank": rank, "polygons": polygons,
                    "extent": _poly_extent(polygons)})
    _split_ocean_halves(out)
    out.sort(key=lambda m: m["scalerank"])
    return tuple(out)


def _poly_extent(polygons: list[list[Ring]]) -> tuple[float, float, float, float]:
    xs = [x for poly in polygons for x, _ in poly[0]]
    ys = [y for poly in polygons for _, y in poly[0]]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)


def arc_deg(a: Point, b: Point) -> float:
    """The great-circle angle, degrees, between two (lon, lat) points."""
    la1, la2, dl = math.radians(a[1]), math.radians(b[1]), math.radians(b[0] - a[0])
    c = math.sin(la1) * math.sin(la2) + math.cos(la1) * math.cos(la2) * math.cos(dl)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def densify_ring(ring: Sequence[Point], step: float = MARINE_DENSIFY_DEG) -> Ring:
    """Round 13 (coordinator directive 1): `ring` (lon, lat) with points put into every edge longer than `step`
    degrees, measured as the larger of its lon and lat change and its great-circle arc, so no edge projects as a chord
    on an azimuthal view. The points are interpolated in lon/lat, so the ring is the same planar lon/lat polygon
    marine_at tests (an edge along 60.53 S stays on that parallel). O(n + added points)."""
    pts = [(float(x), float(y)) for x, y in ring]
    if len(pts) < 2:
        return pts
    out: Ring = []
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        out.append(a)
        if i == len(pts) - 1 and a == pts[0]:
            continue
        span = max(abs(b[0] - a[0]), abs(b[1] - a[1]), arc_deg(a, b))
        n = math.ceil(span / step) if step > 0 else 1
        for j in range(1, n):
            f = j / n
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def _split_ocean_halves(bodies: list["Marine"]) -> None:
    """Round 13 (directive 1): each OCEAN_HALVES pair cut at the equator (IHO): the part of the South polygon north
    of 0 joins the North body, the part of the North polygon south of it joins the South body (Sutherland-Hodgman on
    each ring; holes cut the same way). In place; a pair not both present is left as it is."""
    by = {m["name"]: m for m in bodies}
    for north, south in OCEAN_HALVES:
        n, s = by.get(north), by.get(south)
        if n is None or s is None:
            continue
        nb, sb = (-361.0, 0.0, 361.0, 91.0), (-361.0, -91.0, 361.0, 0.0)

        def cut(polys: list[list[Ring]], box: Box) -> list[list[Ring]]:
            got = []
            for poly in polys:
                outer = densify_ring(clip_ring(poly[0], box))     # the cut's own edge along 0 too
                if len(outer) < 3 or ring_area(outer) < 1e-9:
                    continue
                holes = [densify_ring(h) for h in (clip_ring(r, box) for r in poly[1:]) if len(h) >= 3]
                got.append([outer, *holes])
            return got
        n_polys = cut(n["polygons"], nb) + cut(s["polygons"], nb)
        s_polys = cut(s["polygons"], sb) + cut(n["polygons"], sb)
        n["polygons"], s["polygons"] = n_polys, s_polys
        n["extent"], s["extent"] = _poly_extent(n_polys), _poly_extent(s_polys)


def marine_at(lon: float, lat: float) -> Marine | None:
    """The smallest named water body (marine_polygons) whose own polygon holds (lon, lat), or None."""
    lon = (lon + 180.0) % 360.0 - 180.0
    best: Marine | None = None
    best_a = math.inf
    for m in marine_polygons():
        e = m["extent"]
        if not (e[0] <= lon <= e[2] and e[1] <= lat <= e[3]):
            continue
        if point_in_polygons(lon, lat, m["polygons"]):
            a = sum(ring_area(poly[0]) for poly in m["polygons"])
            if a < best_a:
                best, best_a = m, a
    return best


# ------------------------------------------------------------------ 1:10m countries (round 11, 2026-11-04)
# For a site whose own island the 1:50m outlines leave out (Pitcairn: only Henderson is there, 1.7 degrees from
# Adamstown): the 1:10m polygons at the site, for that one view. Round 12: Natural Earth's 1:10m map subunits, the
# countries split where their parts lie apart (Saint Helena into Saint Helena, Ascension and Tristan da Cunha), so
# the island drawn carries its own name (Tristan da Cunha was named SAINT HELENA, an island 2,400 km away).
# Fetched by `python scripts/fetch_geodata.py --10m`; without it the map marks the site on open sea (logged).
COUNTRIES_10M_FILE = BASE_DIR / "tools" / "geo" / "ne_10m_admin_0_map_subunits.geojson"
FETCH_COUNTRIES_10M_CMD = "python scripts/fetch_geodata.py --10m"
ISLAND_GROUP_KM = 30.0       # round 12: the 1:10m islets this near the site are drawn with its own (an atoll's
                             # ring: Bikini's Enyu, 18.8 km from the site, was left out and the frame held one islet)


class Unit10m(NamedTuple):
    """One 1:10m map subunit: its lowercase names (NAME_FIELDS), its sovereign's, its own name (SUBUNIT), its
    polygons, and (round 15) its admin-0 country's, map unit's and own names as Natural Earth gives them, its long
    name and its ISO code (entity_rows)."""
    names: frozenset[str]
    sovereign: str
    name: str
    polygons: tuple[list[Ring], ...]
    admin: str = ""
    geounit: str = ""
    name_long: str = ""
    label: str = ""
    iso: str = ""


@lru_cache(maxsize=1)
def countries_10m() -> tuple[Unit10m, ...]:
    """Each 1:10m map subunit (Unit10m); () where the file is missing."""
    if not COUNTRIES_10M_FILE.is_file():
        return ()
    data = json.loads(COUNTRIES_10M_FILE.read_text(encoding="utf-8"))
    out = []
    for f in data.get("features", []):
        props, geom = f.get("properties") or {}, f.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        names = frozenset(str(props.get(k, "")).strip().lower() for k in NAME_FIELDS + ("SUBUNIT",) if props.get(k))
        own = str(props.get("SUBUNIT") or props.get("NAME_LONG") or props.get("NAME") or "").strip()
        out.append(Unit10m(names, str(props.get("SOVEREIGNT") or "").strip().lower(), own,
                           tuple([[(float(x), float(y)) for x, y in ring] for ring in poly] for poly in polys),
                           str(props.get("ADMIN") or own).strip(), str(props.get("GEOUNIT") or "").strip(),
                           str(props.get("NAME_LONG") or "").strip(), str(props.get("NAME") or "").strip(),
                           str(props.get("ISO_A3") or "").strip()))
    return tuple(out)


def ring_km(ring: Ring, lon: float, lat: float) -> float:
    """Km from (lon, lat) to the nearest vertex of `ring` (0 inside it), lon scaled by cos(lat)."""
    if _in_ring(lon, lat, ring):
        return 0.0
    c = math.cos(math.radians(lat))
    return 111.2 * min(math.hypot((x - lon) * c, y - lat) for x, y in ring)


def edge_km(ring: Ring, lon: float, lat: float) -> float:
    """Km from (lon, lat) to the nearest point of `ring`'s edges (not 0 inside it), lon scaled by cos(lat). O(n)."""
    c = max(0.05, math.cos(math.radians(lat)))
    best = math.inf
    n = len(ring)
    for i in range(n):
        (ax, ay), (bx, by) = ring[i], ring[(i + 1) % n]
        ax_, bx_, px_ = (ax - lon) * c, (bx - lon) * c, 0.0
        ay_, by_ = ay - lat, by - lat
        vx, vy = bx_ - ax_, by_ - ay_
        ll = vx * vx + vy * vy
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px_ - ax_) * vx + (0.0 - ay_) * vy) / ll))
        d = math.hypot(ax_ + t * vx, ay_ + t * vy)
        best = min(best, d)
    return 111.2 * best


# ------------------------------------------------------------------ the entity at a site (round 15, 2026-11-05)
# Coordinator directive 1: a place's home is the polygon that holds it, never a name looked up. The 1:10m map
# subunits are grouped into the entities a map names (entity_rows): an admin-0 country (the United Kingdom, Scotland
# and St Kilda with it; Sao Tome and Principe), or a part of one that is a territory of its own: a map unit with its
# own ISO code (Tokelau, French Guiana, Svalbard), or a dependency's subunit lying ENTITY_AWAY_KM from all its other
# ground (Ascension and Tristan da Cunha, apart from Saint Helena; never Diego Garcia, which is the British Indian
# Ocean Territory's own atoll). Each is named by its own name field written out (entity_name), and drawn from its
# own 1:10m polygons (countries_detail), so the fill and the name are one entity by construction.
ENTITY_AWAY_KM = 50.0       # a dependency's subunit this far from all its other ground is a territory of its own
SITE_SNAP_KM = 5.0          # km: a site this near an entity's ground, off it, is on it (a harbour, a reef islet)
SITE_NEAR_KM = 50.0         # and within this, off it, offshore of it (the marker on the sea, a leader to the land)
SITE_HINT_KM = 2.0          # round 16 (review 3): km, the most the topic's country may override a polygon that holds
                            # the site (1:10m borders are generalised by about this: Gibraltar's summit 0.3 km, Vaals
                            # 1.75, Leticia 1.4, Busingen 1.9); beyond it the ground that holds the site decides
_INTERNAL_EDGES: set[tuple[Point, Point]] = set()   # edges two subunits of one entity share (never stroked)


def _ring_extent(ring: Ring) -> tuple[float, float, float, float]:
    xs, ys = [x for x, _ in ring], [y for _, y in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _rings_km(a: Ring, b: Ring, cap: float) -> float:
    """Km between the nearest vertices of two rings (0 where one holds a vertex of the other), sampled to at most
    200 vertices of `a`; `cap` returned at once where their extents lie further apart than it."""
    ea, eb = _ring_extent(a), _ring_extent(b)
    lat = (ea[1] + ea[3]) / 2
    c = max(0.05, math.cos(math.radians(lat)))
    gx = max(eb[0] - ea[2], 0.0, ea[0] - eb[2]) * c * 111.2
    gy = max(eb[1] - ea[3], 0.0, ea[1] - eb[3]) * 111.2
    if math.hypot(gx, gy) > cap:
        return cap
    best = cap
    for x, y in a[:: max(1, len(a) // 200)]:
        best = min(best, ring_km(b, x, y))
        if best == 0.0:
            break
    return best


def _same_words(a: str, b: str) -> bool:
    """`a` and `b` the same name but for "and", "the" and punctuation ("French Southern Antarctic Lands")."""
    def key(t: str) -> list[str]:
        return [w for w in re.findall(r"[a-z]+", fold(t).lower()) if w not in ("and", "the", "of")]
    return key(a) == key(b)


def _apart(unit: Unit10m, others: Sequence[Unit10m], cap: float = ENTITY_AWAY_KM) -> bool:
    """Every polygon of `unit` at least `cap` km from every polygon of `others` (outer rings, sampled)."""
    for poly in unit.polygons:
        for o in others:
            for q in o.polygons:
                if _rings_km(poly[0], q[0], cap) < cap:
                    return False
    return True


def entity_rows() -> dict[str, tuple[str, str, list[Unit10m]]]:
    """{entity key: (level, own name, its 1:10m rows)} over the map subunits. Level "admin": the admin-0 country
    (key its ADMIN, lower case, accent-folded); "unit": a map unit with an ISO code of its own; "part": a
    dependency's subunit ENTITY_AWAY_KM from the rest of it, where the dependency is a grouping (no coded row of
    its own; key ADMIN/name). O(rows^2) within each dependency."""
    by_admin: dict[str, list[Unit10m]] = {}
    for u in countries_10m():
        by_admin.setdefault(u.admin, []).append(u)
    n50 = {str(c.get("key") or ""): c for c in countries_50m()} if available() else {}
    out: dict[str, tuple[str, str, list[Unit10m]]] = {}
    for admin, rows in by_admin.items():
        dependency = bool(rows[0].sovereign) and not _same_words(rows[0].sovereign, admin)
        c50 = n50.get(fold(admin).lower())
        own50 = [str(v) for v in ((c50.get("display"), c50.get("full_name"), *c50.get("short_names", []))
                                  if c50 is not None else ()) if v]
        # a grouping of territories (no row of the admin's own with a code of its own: Saint Helena, Ascension and
        # Tristan da Cunha; the US Minor Outlying Islands), not one coded territory (the British Indian Ocean
        # Territory, Diego Garcia with it)
        grouping = not any(_same_words(u.name, admin) and u.iso and u.iso != "-99" for u in rows)
        for u in rows:
            if u.geounit and not _same_words(u.geounit, admin) and u.iso and u.iso != "-99" \
                    and not any(_same_words(u.geounit, o) for o in own50):
                level, own = "unit", u.geounit
            elif dependency and grouping and len(rows) > 1 and not _same_words(u.name, admin) \
                    and _apart(u, [o for o in rows if o is not u]):
                level, own = "part", u.name
            else:
                level, own = "admin", admin
            key = fold(admin).lower() if level == "admin" else f"{fold(admin).lower()}/{fold(own).lower()}"
            out.setdefault(key, (level, own, []))[2].append(u)
    return out


def entity_name(level: str, own: str, rows: Sequence[Unit10m], n50: "Country | None" = None) -> tuple[str, list[str]]:
    """(the entity's name written out, its shorter written-out forms): an admin-0 country's 1:50m full_name
    (Natural Earth's NAME, or ADMIN where NAME is abbreviated: FAROE ISLANDS, never FAEROE IS.), else its ADMIN; a
    territory's own unit or subunit name, else its long name. Accents folded; never an initialism or an
    abbreviation (clean_name): HONG KONG for Hong Kong S.A.R., EUROPA ISLAND for Europa I."""
    if level == "admin":
        cands = ([str(n50.get("full_name") or n50.get("display") or "")] if n50 is not None else []) + [own]
        if len(rows) == 1:
            cands += [rows[0].name_long, rows[0].label]
        alts = list(n50.get("short_names", [])) if n50 is not None else []
    else:
        mine = sorted(rows, key=lambda u: not _same_words(u.name, own))
        cands = [own] + [v for u in mine for v in (u.name_long, u.label)]
        alts = []
    forms = clean_forms(cands)
    name = forms[0] if forms else fold(own)
    shorter = [a for a in clean_forms(alts + forms[1:]) if len(a) < len(name) and a.lower() != name.lower()]
    return name, sorted(shorter, key=len, reverse=True)


@lru_cache(maxsize=1)
def countries_detail() -> list[Country]:
    """The 1:10m entities (entity_rows) as countries: each one's own polygons (all its subunits'), its name
    (entity_name), its lower-case names, and, for an admin-0 country, its 1:50m label point, label rank and type;
    Antarctica's ice shelves added. The edges two subunits of one entity share are recorded (_INTERNAL_EDGES: the
    border between England and Scotland is never stroked). [] where the 1:10m file is missing."""
    groups = entity_rows()
    if not groups:
        return []
    n50 = {str(c.get("key") or ""): c for c in countries_50m()} if available() else {}
    out: list[Country] = []
    _INTERNAL_EDGES.clear()
    for key, (level, own, rows) in groups.items():
        c50 = n50.get(key) if level == "admin" else None
        name, alts = entity_name(level, own, rows, c50)
        polygons = [[list(r) for r in poly] for u in rows for poly in u.polygons]
        if not polygons:
            continue
        names = {fold(v).strip().lower() for u in rows for v in (u.name, u.name_long, u.label, u.geounit)
                 if v} | {v.strip().lower() for u in rows for v in (u.name, u.name_long, u.label) if v}
        if level == "admin":
            names |= {fold(own).lower(), own.lower()} | {n for u in rows for n in u.names}
            if c50 is not None:
                names |= set(c50["names"])
        else:
            names |= {fold(own).lower(), own.lower()}
        names.add(name.lower())
        xs = [x for poly in polygons for x, _ in poly[0]]
        ys = [y for poly in polygons for _, y in poly[0]]
        out.append({"names": names, "sovereign": rows[0].sovereign, "polygons": polygons,
                    "size": sum(len(poly[0]) for poly in polygons), "display": name,
                    "label_at": c50["label_at"] if c50 is not None else None,
                    "label_rank": int(c50["label_rank"]) if c50 is not None else 6,
                    "type": str(c50.get("type") or "") if c50 is not None else "",
                    "short_names": alts, "full_name": name, "key": key,
                    "extent": (min(xs), min(ys), max(xs), max(ys))})
        if len(rows) > 1:
            seen: dict[tuple[Point, Point], int] = {}
            for ri, u in enumerate(rows):
                for poly in u.polygons:
                    for ring in poly:
                        for i in range(len(ring) - 1):
                            a_, b_ = ring[i], ring[i + 1]
                            e_ = (a_, b_) if a_ < b_ else (b_, a_)
                            got = seen.setdefault(e_, ri)
                            if got != ri:
                                _INTERNAL_EDGES.add(e_)
    _add_ice_shelves(out)
    return out


def same_entity(a: "Country | None", b: "Country | None") -> bool:
    """`a` and `b` the same entity (the same object, or the same key: a 1:50m country and its 1:10m form)."""
    if a is None or b is None:
        return False
    return a is b or (bool(a.get("key")) and a.get("key") == b.get("key"))


class SiteHome(NamedTuple):
    """The entity at a site (site_home): the country (None on the open sea), km off its ground (0 on it);
    `overrode`: the topic's country chosen within SITE_HINT_KM although another entity's polygon holds the site (the
    caller then moves the marker onto the chosen ground, so the marker and the highlighted fill agree)."""
    country: Country | None
    km: float
    overrode: bool = False


def _hint_matches(c: Country, hint: str) -> bool:
    h = fold(str(hint or "")).strip().lower()
    h = ALIASES.get(h, h)
    return bool(h) and (h in {fold(n).lower() for n in c["names"]} or fold(str(c.get("sovereign") or "")) == h)


def site_home(lon: float, lat: float, near_km: float = SITE_NEAR_KM, hint: str = "") -> SiteHome:
    """Round 15 (coordinator directive 1): the entity whose polygon holds (lon, lat) in the drawn country list
    (countries(): the 1:10m entities inside detail_view; the smallest such polygon, so an enclave is its own
    country's), else the one whose ground is nearest within `near_km` (a harbour, a wreck off the coast), else none
    (the open sea). The site's own geometry decides. Only within SITE_HINT_KM of a polygon that holds the site
    (round 16), or SITE_SNAP_KM of a site off all ground, the data's own error, does the topic's country (`hint`)
    choose between the grounds there (the Rock of Gibraltar's summit lies 0.3 km off
    Natural Earth's seven-point Gibraltar, inside its Spain; Busingen's enclave is not in the data, 2 km off
    Germany); never beyond it (Fakaofo is Tokelau's, given as New Zealand). Across the antimeridian too.
    O(polygons near the site)."""
    cs = countries()
    c_ = max(0.05, math.cos(math.radians(lat)))
    reach_y = max(near_km, SITE_SNAP_KM) / 111.2
    reach_x = reach_y / c_
    holds: tuple[float, Country] | None = None
    near: list[tuple[float, Country]] = []
    for c in cs:
        for poly in c["polygons"]:
            ring = poly[0]
            if len(ring) < 3:
                continue
            xs = [x for x, _ in ring]
            ys = [y for _, y in ring]
            if not (min(ys) - reach_y <= lat <= max(ys) + reach_y):
                continue
            for sh in (0.0, 360.0, -360.0):
                lo = lon + sh
                if not (min(xs) - reach_x <= lo <= max(xs) + reach_x):
                    continue
                if point_in_polygons(lo, lat, [poly]):
                    a = abs(ring_area(ring))
                    if holds is None or a < holds[0]:
                        holds = (a, c)
                    continue
                d = edge_km(ring, lo, lat)
                if d <= max(near_km, SITE_SNAP_KM):
                    near.append((d, c))
    if hint:
        # round 16 (review 3): over a polygon that holds the site, only within SITE_HINT_KM (the 1:10m border's
        # own generalisation); off all ground, within SITE_SNAP_KM
        cap = SITE_HINT_KM if holds is not None else SITE_SNAP_KM
        within = sorted((d, c) for d, c in near if d <= cap and _hint_matches(c, hint)) \
            if not (holds is not None and _hint_matches(holds[1], hint)) else []
        if within:
            return SiteHome(within[0][1], within[0][0], holds is not None)
    if holds is not None:
        return SiteHome(holds[1], 0.0)
    near = [(d, c) for d, c in near if d <= near_km]
    if near:
        d, c = min(near, key=lambda t: t[0])
        return SiteHome(c, d)
    return SiteHome(None, math.inf)


# ------------------------------------------------------------------ polar views (round 11, 2026-11-04)
def azimuthal(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    """(u, v), degrees of arc east and north, of (lon, lat) on the azimuthal equidistant projection centred on
    (lon0, lat0): distances from the centre are true, there is no seam or edge short of the antipode (coordinator
    directive 2: a view near a pole is never plate carree run past it)."""
    la, la0, dl = math.radians(lat), math.radians(lat0), math.radians(lon - lon0)
    cos_c = max(-1.0, min(1.0, math.sin(la0) * math.sin(la) + math.cos(la0) * math.cos(la) * math.cos(dl)))
    c = math.acos(cos_c)
    kp = c / math.sin(c) if c > 1e-9 else 1.0
    x = kp * math.cos(la) * math.sin(dl)
    y = kp * (math.cos(la0) * math.sin(la) - math.sin(la0) * math.cos(la) * math.cos(dl))
    return math.degrees(x), math.degrees(y)


def azimuthal_inverse(u: float, v: float, lon0: float, lat0: float) -> tuple[float, float]:
    """(lon, lat) of the plane point (u, v) (degrees of arc east and north) of azimuthal(lon0, lat0)."""
    x, y = math.radians(u), math.radians(v)
    rho = math.hypot(x, y)
    la0 = math.radians(lat0)
    if rho < 1e-12:
        return lon0, lat0
    c = rho
    lat = math.asin(max(-1.0, min(1.0, math.cos(c) * math.sin(la0) + y * math.sin(c) * math.cos(la0) / rho)))
    lon = lon0 + math.degrees(math.atan2(x * math.sin(c), rho * math.cos(la0) * math.cos(c) - y * math.sin(la0) * math.sin(c)))
    return (lon + 180.0) % 360.0 - 180.0, math.degrees(lat)


def polar_polygons(lon0: float, lat0: float, radius: float) -> list[tuple[Country, list[Ring]]]:
    """Every country polygon (outer ring, holes) that reaches the cap `radius` degrees of arc round (lon0, lat0),
    enclaves last, each once at its own longitudes (an azimuthal view has no antimeridian or pole to repeat it
    across). By each polygon's extent: the cap's latitude band, and its longitude band where the cap holds no
    pole. O(n) over the extents."""
    cs = countries()
    ext, encl = _view_index(cs)
    lo_y, hi_y = lat0 - radius, lat0 + radius
    pole = lo_y <= -90.0 or hi_y >= 90.0
    if not pole:
        half = math.degrees(math.asin(min(1.0, math.sin(math.radians(radius)) / max(1e-9, math.cos(math.radians(lat0))))))
    first: list[tuple[Country, list[Ring]]] = []
    last: list[tuple[Country, list[Ring]]] = []
    for ci, c in enumerate(cs):
        for pi, poly in enumerate(c["polygons"]):
            e = ext[ci][pi]
            if e[3] < lo_y or e[1] > hi_y:
                continue
            if not pole and half < 90.0:
                d0 = (e[0] - lon0 + 180.0) % 360.0 - 180.0
                d1 = d0 + (e[2] - e[0])
                if (d1 < -half or d0 > half) and not (d0 - 360.0 <= half and d1 - 360.0 >= -half) \
                        and not (d0 + 360.0 <= half and d1 + 360.0 >= -half):
                    continue
            (last if (ci, pi) in encl else first).append((c, poly))
    return first + last


_COUNTRIES_ENTRY = countries
