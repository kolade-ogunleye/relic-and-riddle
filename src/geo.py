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
Sources and terms: docs/sources.md.
"""

import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

Ring = List[Tuple[float, float]]


def available() -> bool:
    return COUNTRIES_FILE.exists()


def missing_files() -> List[Tuple[Path, str]]:
    """The geo files that are not on disk, each with the command that fetches it: without the country outlines
    no map is drawn; without the shaded relief maps render flat."""
    out: List[Tuple[Path, str]] = []
    if not COUNTRIES_FILE.is_file():
        out.append((COUNTRIES_FILE, FETCH_COUNTRIES_CMD))
    if not (RELIEF_FILE.is_file() or _relief_file().is_file()):
        out.append((RELIEF_HR_FILE, FETCH_RELIEF_CMD))
    return out


@lru_cache(maxsize=1)
def countries() -> List[Dict[str, Any]]:
    """[{"names": {...lowercase}, "polygons": [[outer ring, hole rings...], ...]}]"""
    data = json.loads(COUNTRIES_FILE.read_text(encoding="utf-8"))
    out = []
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
                    "extent": (min(x for poly in polygons for x, _ in poly[0]), min(y for poly in polygons for _, y in poly[0]),
                               max(x for poly in polygons for x, _ in poly[0]), max(y for poly in polygons for _, y in poly[0]))})
    return out


def find_country(name: str) -> Optional[Dict[str, Any]]:
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


def in_country(lon: float, lat: float, country: Dict[str, Any], tolerance_deg: float = 1.0) -> bool:
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
    from .commons import HEADERS  # same identifying User-Agent as the Commons client (API etiquette)
    for attempt in range(3):
        resp = requests.get(WIKIDATA_API, params={**params, "format": "json", "maxlag": 5}, headers=HEADERS, timeout=30)
        if resp.status_code in (429, 503) or "maxlag" in resp.text[:200]:
            time.sleep(5 * 2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def _load_cache() -> Dict[str, Any]:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _wikidata_point(query: str, country: Dict[str, Any], country_name: str) -> Optional[Dict[str, Any]]:
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


def locate(topic: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
    for c in countries():
        rings = []
        for poly in c["polygons"]:
            pts = [orthographic(x, y, lon0, lat0) for x, y in poly[0][::step]]
            vis = [(x, y) for x, y, v in pts if v]
            if len(vis) >= 3:
                rings.append(vis)
        if rings:
            out.append((c, rings))
    return out
