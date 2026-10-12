"""
src/geo.py
==========
Where a topic is, for the locator-map graphic.

- Coordinates come from Wikidata (CC0; property P625). A candidate is accepted only if it
  falls inside the topic's country on the Natural Earth map, so a namesake elsewhere in
  the world can't put the pin in the wrong place. Results are cached in data/geo_cache.json.
- Country outlines: Natural Earth 1:50m admin-0 countries (public domain), stored at
  tools/geo/ne_50m_admin_0_countries.geojson (fetch with scripts/fetch_geodata.py).
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
CACHE_FILE = DATA_DIR / "geo_cache.json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
NAME_FIELDS = ("NAME", "NAME_LONG", "ADMIN", "FORMAL_EN", "NAME_EN", "GEOUNIT")
ALIASES = {"turkiye": "turkey", "türkiye": "turkey", "usa": "united states of america", "united states": "united states of america",
           "uk": "united kingdom", "scotland": "united kingdom", "england": "united kingdom",
           "wales": "united kingdom", "czechia": "czech republic", "russia": "russian federation"}

Ring = List[Tuple[float, float]]


def available() -> bool:
    return COUNTRIES_FILE.exists()


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
        out.append({"names": names, "sovereign": str(props.get("SOVEREIGNT", "")).strip().lower(),
                    "polygons": polygons, "size": sum(len(poly[0]) for poly in polygons)})
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
    """{"lat", "lon", "qid", "label", "country"} for the topic's place, or None if no
    Wikidata candidate falls inside the stated country."""
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
            return {"lat": lat, "lon": lon, "qid": "", "label": name, "country": country_name, "approximate": False}
        log.warning("Map: stored coordinates for '%s' are not in %s; trying Wikidata.", name, country_name)
    result = None
    # The site's own name first, then its city and region (a coarser but still correct pin).
    for query in [q for q in (name, loc.get("city"), loc.get("region")) if q]:
        try:
            result = _wikidata_point(query, country, country_name)
        except requests.RequestException as e:
            log.warning("Map: Wikidata lookup failed (%s).", e)
            return None
        if result:
            # The pin says what it actually marks: the site, or the city/region used instead.
            result["label"], result["approximate"] = query, query != name
            break
    if result is None:
        log.warning("Map: no Wikidata coordinates for '%s' inside %s; skipping the map.", name, country_name)
        return None
    cache[key] = result
    CACHE_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    return result
