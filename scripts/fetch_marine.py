"""
Download Natural Earth's 1:10m marine polygons (public domain): the named oceans, seas, gulfs, bays and straits
the locator map names, each only inside its own polygon; and the 1:50m Antarctic ice shelves, drawn as
Antarctica's ground (the 1:50m country outline ends at the grounding line, so the Ross Ice Shelf read as sea).

  python scripts/fetch_marine.py

Saved to tools/geo/ne_10m_geography_marine_polys.geojson (gitignored, about 1.4 MB) and
tools/geo/ne_50m_antarctic_ice_shelves_polys.geojson (gitignored, about 0.2 MB), converted from the zipped
shapefiles on naciscdn.org (parsed by struct alone; a size cap guards each download). Without the first the map
names no water; without the second the ice shelves are drawn as sea; both logged.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.fetch_geodata import download
from src.geo import ICE_SHELVES_FILE, MARINE_FILE, read_shapefile_zip

URL = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_geography_marine_polys.zip"
URL_ICE = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_antarctic_ice_shelves_polys.zip"
MAX_BYTES = 10_000_000
KEEP_PROPS = ("name", "name_en", "label", "featurecla", "scalerank", "min_label")
MUST_HAVE = ("Caribbean Sea", "Red Sea", "North Pacific Ocean", "Gulf of Finland")


def _fetch(url: str, stem: str) -> dict | None:
    """The zipped shapefile at `url` as a FeatureCollection, or None (printed) where it fails a check."""
    data = download(url, MAX_BYTES)
    if data is None:
        return None
    try:
        return read_shapefile_zip(data, stem)
    except (ValueError, LookupError) as e:
        print(f"Unexpected file contents ({e}); not saved.")
        return None


def fetch_marine() -> int:
    parsed = _fetch(URL, "ne_10m_geography_marine_polys")
    if parsed is None:
        return 1
    feats = parsed["features"]
    names = {f["properties"].get("name") for f in feats}
    if len(feats) < 200 or not all(n in names for n in MUST_HAVE):
        print("Unexpected file contents; not saved.")
        return 1
    for f in feats:
        f["properties"] = {k: f["properties"].get(k, "") for k in KEEP_PROPS}
    MARINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MARINE_FILE.write_text(json.dumps(parsed, separators=(",", ":")), encoding="utf-8")
    print(f"Saved {len(feats)} marine polygons to {MARINE_FILE}")
    return 0


def fetch_ice_shelves() -> int:
    parsed = _fetch(URL_ICE, "ne_50m_antarctic_ice_shelves_polys")
    if parsed is None:
        return 1
    feats = parsed["features"]
    ys = [y for f in feats for poly in f["geometry"]["coordinates"] for _x, y in poly[0]]
    # the shelves are Antarctic: every point south of 60 S, and the Ross Ice Shelf's reach to 85 S
    if len(feats) < 20 or not ys or max(ys) > -60.0 or min(ys) > -84.0:
        print("Unexpected file contents; not saved.")
        return 1
    for f in feats:
        f["properties"] = {k: f["properties"].get(k, "") for k in ("featurecla", "scalerank")}
    ICE_SHELVES_FILE.parent.mkdir(parents=True, exist_ok=True)
    ICE_SHELVES_FILE.write_text(json.dumps(parsed, separators=(",", ":")), encoding="utf-8")
    print(f"Saved {len(feats)} ice shelves to {ICE_SHELVES_FILE}")
    return 0


def main() -> int:
    return fetch_marine() or fetch_ice_shelves()


if __name__ == "__main__":
    sys.exit(main())
