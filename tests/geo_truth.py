"""An independent name check for the locator and route maps (round 15, coordinator directive 5).

Every name a map draws is checked against the raw Natural Earth geojson files with this module's own
point-in-polygon code: none of src.geo's or src.graphics's parsing, entity grouping, masks or verdicts is used.
The renderer's layout sidecar (<clip>.layout.json) gives each drawn name's text and the (lon, lat) of its checked
points (its box's corners and a 7 x 3 grid inside it, through the frame's own inverse projection); this module
decides on its own whether those points lie on the land, water or polygon the name says.

Rules (directive 2, LEADER OR DROP):
- the home's name inside its fill: every point on ground of a feature whose names include the text (within
  TOL_KM, the drawing's own rasterisation), never on another feature's ground only;
- the home's name outside its fill: every point on open water (no feature's ground, no ice shelf), and a leader;
- a neighbour's name: every point on ground of a feature of that name, none on another's only;
- a water name: every point off all ground, on the smallest named marine polygon there (oceans split at the
  equator, as the IHO does), and that polygon's name is the text;
- the home: the text is a name of the feature whose polygon holds the site (or, offshore, of one within
  HOME_NEAR_KM), or the map says why it named none (layout "home_drop").
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import cache
from pathlib import Path

GEO = Path(__file__).resolve().parent.parent / "tools" / "geo"
SUBUNITS = GEO / "ne_10m_admin_0_map_subunits.geojson"
MARINE = GEO / "ne_10m_geography_marine_polys.geojson"
SHELVES = GEO / "ne_50m_antarctic_ice_shelves_polys.geojson"
TOL_KM = 2.0          # km: a name's point this near its own ground is on it (the fill's rasterisation)
HOME_NEAR_KM = 50.0   # km: an offshore site's home lies within this
SNAP_KM = 5.0         # km: the data's own error, within which a site's ground may be the next polygon
NAME_KEYS = ("NAME", "NAME_LONG", "ADMIN", "GEOUNIT", "SUBUNIT", "NAME_EN", "FORMAL_EN")


def available() -> bool:
    return SUBUNITS.is_file() and MARINE.is_file()


def words(text: str) -> list[str]:
    """`text`'s words, lower case, accents folded, "saint" as "st", "and"/"the"/"of" dropped."""
    t = "".join(ch for ch in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(ch)).lower()
    t = t.replace("&", " and ").replace("saint", "st")
    return [w for w in re.findall(r"[a-z]+", t) if w not in ("and", "the", "of")]


def names_match(text: str, names: set[str], props_list: list[dict]) -> bool:
    """Whether `text` is one of `names` (normalised), or a written-out shortening of one: its first two or more
    words ("United States" of "United States of America", "Tanzania" of "United Republic of Tanzania" excepted)."""
    if norm(text) in names:
        return True
    tw = words(text)
    if len(tw) < 2:
        return False
    for props in props_list:
        for k in NAME_KEYS:
            nw = words(props.get(k) or "")
            if len(nw) > len(tw) and nw[:len(tw)] == tw:
                return True
    return False


def _title_words(text: str) -> list[str]:
    """`text`'s words as the renderer's title test reads them: accents folded, upper case, runs of A-Z, 0-9 and '."""
    t = "".join(ch for ch in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(ch))
    return re.findall(r"[A-Z0-9']+", t.upper())


def repeats_title(name: str, title: str) -> bool:
    """Whether `name` is a whole-word run of `title` (operator ruling Q1 of QF-20, 2026-11-11): "Ascension" of
    "Georgetown, Ascension", "Palmyra Atoll" of "Palmyra Atoll"; never part of a word ("George Town" is not a run
    of "Georgetown")."""
    nw, tw = _title_words(name), _title_words(title)
    return bool(nw) and any(tw[j:j + len(nw)] == nw for j in range(len(tw) - len(nw) + 1))


def _ground_repeats_title(f: Feature, title: str) -> bool:
    """Whether one of the feature's own names is the site's title (normalised, the round-16 rule) or a whole-word
    run of it."""
    return norm(title) in f.names or any(repeats_title(str(f.props.get(k)), title) for k in NAME_KEYS if f.props.get(k))


def norm(text: str) -> str:
    """Letters only, lower case, accents folded, "saint" as "st", "and"/"the" dropped."""
    t = "".join(ch for ch in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(ch)).lower()
    t = t.replace("&", " and ").replace("saint", "st")
    return "".join(w for w in re.findall(r"[a-z]+", t) if w not in ("and", "the", "of"))


def _pip(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def _area(ring: list) -> float:
    return abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
                   for i in range(len(ring)))) / 2


class Feature:
    def __init__(self, props: dict, geom: dict) -> None:
        self.props = props
        self.polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        self.boxes = [(min(c[0] for c in p[0]), min(c[1] for c in p[0]), max(c[0] for c in p[0]),
                       max(c[1] for c in p[0])) for p in self.polys]
        self.area = sum(_area(p[0]) for p in self.polys)
        self.names = {norm(props.get(k)) for k in NAME_KEYS if props.get(k)} - {""}

    def holds(self, lon: float, lat: float) -> bool:
        for p, b in zip(self.polys, self.boxes):
            if b[0] <= lon <= b[2] and b[1] <= lat <= b[3] and _pip(lon, lat, p[0]) \
                    and not any(_pip(lon, lat, h) for h in p[1:]):
                return True
        return False


@cache
def _load(path: Path) -> tuple[Feature, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Feature(f.get("properties") or {}, f["geometry"]) for f in data["features"]
                 if f.get("geometry") and f["geometry"]["type"] in ("Polygon", "MultiPolygon"))


def wrap(lon: float) -> float:
    return (lon + 180.0) % 360.0 - 180.0


def land_at(lon: float, lat: float) -> list[Feature]:
    """The subunit features whose ground holds (lon, lat), and an ice shelf's south of 55 S."""
    lon = wrap(lon)
    got = [f for f in _load(SUBUNITS) if f.holds(lon, lat)]
    if lat < -55 and SHELVES.is_file():
        got += [f for f in _load(SHELVES) if f.holds(lon, lat)]
    return got


def _near(fs: list[Feature], lon: float, lat: float, km: float) -> bool:
    d = km / 111.2
    c = max(0.05, math.cos(math.radians(lat)))
    offs = [(0.0, 0.0)] + [(dx * d / c, dy * d) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                                                               (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7))]
    return any(f.holds(wrap(lon + dx), lat + dy) for f in fs for dx, dy in offs)


def features_named(text: str) -> list[Feature]:
    t = norm(text)
    return [f for f in _load(SUBUNITS) if t in f.names or names_match(text, set(), [f.props])] + \
        ([f for f in _load(SHELVES) if t == "antarctica"] if SHELVES.is_file() else [])


def water_at(lon: float, lat: float) -> str | None:
    """The upper-case name of the smallest marine polygon holding (lon, lat), the oceans split at the equator."""
    lon = wrap(lon)
    best = None
    for f in _load(MARINE):
        if f.holds(lon, lat) and (best is None or f.area < best.area):
            best = f
    if best is None:
        return None
    p = best.props
    nm = str((p.get("name") if p.get("featurecla") == "ocean" else (p.get("name_en") or p.get("name"))) or "").upper()
    for ocean in ("ATLANTIC", "PACIFIC"):
        if ocean in nm and (nm.startswith(("NORTH", "SOUTH"))):
            return ("NORTH " if lat >= 0 else "SOUTH ") + ocean + " OCEAN"
    return nm


def _edge_km(ring: list, lon: float, lat: float) -> float:
    c = max(0.05, math.cos(math.radians(lat)))
    best = math.inf
    for i in range(len(ring)):
        ax, ay = (ring[i][0] - lon) * c, ring[i][1] - lat
        bx, by = (ring[(i + 1) % len(ring)][0] - lon) * c, ring[(i + 1) % len(ring)][1] - lat
        vx, vy = bx - ax, by - ay
        ll = vx * vx + vy * vy
        t = 0.0 if ll == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / ll))
        best = min(best, math.hypot(ax + t * vx, ay + t * vy))
    return best * 111.2


def site_features(lon: float, lat: float) -> tuple[list[Feature], float]:
    """The features holding the site (km 0), else the one whose ground is nearest within HOME_NEAR_KM."""
    here = land_at(lon, lat)
    if here:
        return here, 0.0
    lon = wrap(lon)
    r = HOME_NEAR_KM / 111.2
    rx = r / max(0.05, math.cos(math.radians(lat)))
    best: tuple[float, Feature] | None = None
    for f in _load(SUBUNITS):
        for p, b in zip(f.polys, f.boxes):
            for sh in (0.0, 360.0, -360.0):
                if b[0] - rx <= lon + sh <= b[2] + rx and b[1] - r <= lat <= b[3] + r:
                    d = _edge_km(p[0], lon + sh, lat)
                    if d <= HOME_NEAR_KM and (best is None or d < best[0]):
                        best = (d, f)
    return ([best[1]], best[0]) if best else ([], math.inf)


def faults(lay: dict) -> list[str]:
    """Every fault of a layout sidecar's drawn names (empty where all are right)."""
    out: list[str] = []
    drawn = {("water", w.upper()) for w in lay.get("water", [])} | {("neighbour", n) for n in lay.get("neighbours", [])}
    country = lay.get("country")
    if country:
        drawn.add(("home", country["text"]))
    site = lay.get("anchor_lonlat")
    feats: list[Feature] = []
    title = ""
    if site and lay.get("kind") != "route":
        feats, km = site_features(*site)
        names = set().union(*(f.names for f in feats)) if feats else set()
        # within the data's own error (SNAP_KM) a neighbouring ground may be the site's (the Rock's summit lies
        # 0.3 km off the seven-point Gibraltar; Busingen's and Baarle-Hertog's enclaves are not in the data)
        names |= set().union(*(f.names for f in _load(SUBUNITS) if _near([f], *site, SNAP_KM)))
        # and the names of its admin-0 country's other parts (Zanzibar's row names the United Republic of Tanzania,
        # whose mainland row is NAME "Tanzania"); which part names a territory is tested place by place
        admins = {f.props.get("ADMIN") for f in feats}
        names |= set().union(*(f.names for f in _load(SUBUNITS) if f.props.get("ADMIN") in admins))
        group = [f for f in _load(SUBUNITS) if f.props.get("ADMIN") in admins] + feats
        # round 16: a home whose own name only repeats the site's title (Palmyra Atoll) may be named by its
        # sovereign (the feature's SOVEREIGNT), and only then; QF-20 ruling Q1 (2026-11-11): "repeats" is the
        # renderer's test, the ground's name a whole-word run of the title (Ascension under "Georgetown, Ascension")
        title = str(lay.get("label") or "")
        if title and any(_ground_repeats_title(f, title) for f in feats):
            names |= {norm(f.props.get("SOVEREIGNT")) for f in feats if f.props.get("SOVEREIGNT")}
        if country:
            if not names_match(country["text"], names, [f.props for f in group]):
                out.append(f"home {country['text']!r} is not a name of the ground at the site "
                           f"({sorted(names)[:6]}, {km} km)")
        elif not lay.get("home_drop"):
            out.append(f"no home name and no reason logged (the ground there: {sorted(names)[:6]})")
    for chk in lay.get("name_checks", []):
        if chk["fault"] or (chk["kind"], chk["text"]) not in drawn:
            continue
        pts, text = chk["lonlat"], chk["text"]
        if chk["kind"] == "water":
            for lo, la in pts:
                if land_at(lo, la):
                    out.append(f"water {text} over land at ({lo:.3f}, {la:.3f})")
                    break
                w = water_at(lo, la)
                if w != text.upper():
                    out.append(f"water {text} at ({lo:.3f}, {la:.3f}) is on {w}")
                    break
        elif chk["kind"] == "home":
            own = features_named(text)
            if site and title and lay.get("kind") != "route" and norm(text) in {
                    norm(f.props.get("SOVEREIGNT")) for f in feats if f.props.get("SOVEREIGNT")}:
                own = own + [f for f in feats if _ground_repeats_title(f, title)]   # the sovereign's name there
            inside = bool(country and country.get("inside"))
            for lo, la in pts:
                on = land_at(lo, la)
                if inside:
                    if not _near(own, lo, la, TOL_KM):
                        out.append(f"home {text} at ({lo:.3f}, {la:.3f}) off its own ground, on "
                                   f"{[f.props.get('NAME') for f in on] or 'the sea'}")
                        break
                elif on:
                    out.append(f"outside home {text} over land at ({lo:.3f}, {la:.3f}): {on[0].props.get('NAME')}")
                    break
            # an outside name touching its own ground needs no leader; else it must have one
            if not inside and country and not country.get("leader") and not out \
                    and not any(_near(own, lo, la, TOL_KM * 3) for lo, la in pts):
                out.append(f"outside home {text} has no leader to its ground")
        elif chk["kind"] == "neighbour":
            own = features_named(text)
            if not own:
                out.append(f"neighbour {text}: no feature of that name")
                continue
            for lo, la in pts:
                if not _near(own, lo, la, TOL_KM):
                    on = land_at(lo, la)
                    out.append(f"neighbour {text} at ({lo:.3f}, {la:.3f}) off its own ground, on "
                               f"{[f.props.get('NAME') for f in on] or 'the sea'}")
                    break
    return out
