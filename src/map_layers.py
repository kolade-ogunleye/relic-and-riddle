"""Hash-bound Natural Earth water and neutral ground for the map renderer."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from shapely import Point, STRtree, make_valid, union_all
from shapely.geometry import shape

from . import map_geometry

DATA = Path(__file__).resolve().parents[1] / "data/geo"
STEMS = {
    "water": "ne_10m_lakes",
    "neutral": "ne_10m_admin_0_disputed_areas",
    "ice": "ne_10m_antarctic_ice_shelves_polys",
}


# Operator ruling (QF-20 Q2, 2026-11-11): only ground where Natural Earth records a claim is neutral: its NOTE_BRK
# names one ("Claimed by", "Self admin.", "Between"), whatever its TYPE (Disputed, Indeterminate, Breakaway, Geo unit,
# Geo subunit), except a lease or a UN/military overlay. Taiwan, Crimea, Western Sahara, Kashmir, the Falklands,
# South Georgia and the Kurils stay neutral; Israel, Belize, Kosovo, Gaza, the West Bank, Ceuta, Melilla, the leases
# and the overlays keep their ordinary Natural Earth country.
CLAIM_NOTES = ("claimed by", "self admin", "between")
NOT_CONTESTED_TYPES = frozenset({"Lease", "Overlay"})


def is_contested(properties):
    """Whether a disputed-areas feature records a claim (its NOTE_BRK names one) and is no lease or overlay."""
    note = str(properties.get("NOTE_BRK") or "").lower()
    return (str(properties.get("TYPE") or "").strip() not in NOT_CONTESTED_TYPES
            and any(k in note for k in CLAIM_NOTES))


@lru_cache(maxsize=3)
def geometries(kind):
    stem = STEMS[kind]
    manifest = json.loads((DATA / "natural-earth-manifest.json").read_text(encoding="utf-8"))
    record = next(row for row in manifest if row["stem"] == stem)
    # Hash the LF form: a checkout that keeps CRLF line endings holds the same
    # content and must not lose its maps over a byte-level difference.
    raw = (DATA / (stem + ".geojson")).read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(raw).hexdigest() != record["geojson_sha256"]:
        raise ValueError("Natural Earth layer hash mismatch: " + stem)
    features = json.loads(raw)["features"]
    if len(features) != record["features"]:
        raise ValueError("Natural Earth layer count mismatch: " + stem)
    if kind == "neutral":
        features = [feature for feature in features if is_contested(feature["properties"])]
    return tuple(part for feature in features
                 for part in map_geometry.polygon_parts(make_valid(shape(feature["geometry"]))))


@lru_cache(maxsize=3)
def index(kind):
    return STRtree(geometries(kind))


def polygons(kind):
    return [rings for geometry in geometries(kind) for rings in map_geometry.rings_of(geometry)]


def neutral_at(lon, lat):
    return bool(len(index("neutral").query(Point((lon + 180) % 360 - 180, lat), predicate="intersects")))


def _subtract(ground, kind):
    hits = index(kind).query(ground, predicate="intersects")
    if not len(hits):
        return ground
    return ground.difference(union_all([geometries(kind)[int(i)] for i in hits]))


_GROUND_CACHE = []


def ground(countries):
    """Remove water/claimed overlap, then add unnamed neutral ground once.

    Cache by the source list's identity, retaining it to prevent id reuse. Tests
    replacing a source list receive fresh geometry; the source is never mutated.
    """
    for original, result in _GROUND_CACHE:
        if original is countries:
            return result
    result = []
    for country in countries:
        parts = []
        for rings in country["polygons"]:
            geometry = _subtract(_subtract(map_geometry.polygon(rings), "water"), "neutral")
            parts.extend(map_geometry.rings_of(geometry))
        if parts:
            result.append({**country, "polygons": parts, "size": sum(len(p[0]) for p in parts)})
    neutral = _subtract(union_all(geometries("neutral")), "water")
    result.append({"names": set(), "sovereign": "", "polygons": map_geometry.rings_of(neutral),
                   "size": 0, "display": "", "label_at": None, "label_rank": 99,
                   "type": "Indeterminate", "key": "neutral-ground", "neutral": True,
                   "extent": neutral.bounds})
    _GROUND_CACHE.append((countries, result))
    del _GROUND_CACHE[:-2]
    return result
