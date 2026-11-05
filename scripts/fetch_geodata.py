"""
Download the Natural Earth country outlines (public domain) used by the map graphic.

  python scripts/fetch_geodata.py          # 1:50m countries (required for any map)
  python scripts/fetch_geodata.py --10m    # 1:10m map subunits too (the locator close-up's land and the site's home)

Saved to tools/geo/ne_50m_admin_0_countries.geojson (gitignored, about 3 MB) and, with --10m,
tools/geo/ne_10m_admin_0_map_subunits.geojson (gitignored, about 12 MB, converted from the zipped shapefile on
naciscdn.org: the countries split into their map units and subunits, which src/geo.py groups into the entities a
map names, so Tristan da Cunha and Tokelau carry their own names). Each file is parsed (JSON, or the shapefile by
struct alone) and checked before it is saved; a size cap guards every download.
"""

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.geo import COUNTRIES_10M_FILE, COUNTRIES_FILE, read_shapefile_zip

URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_admin_0_countries.geojson"
URL_10M = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_map_subunits.zip"
MAX_BYTES = 30_000_000
KEEP_PROPS = ("NAME", "NAME_LONG", "ADMIN", "FORMAL_EN", "NAME_EN", "GEOUNIT", "SUBUNIT", "SOVEREIGNT", "ISO_A3")


def download(url: str, max_bytes: int = MAX_BYTES) -> bytes | None:
    """The body of `url`, streamed under `max_bytes` (None, printed, where it runs over)."""
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        chunks: list[bytes] = []
        got = 0
        for chunk in resp.iter_content(1 << 16):
            chunks.append(chunk)
            got += len(chunk)
            if got > max_bytes:
                print("Download exceeded the size cap; aborting.")
                return None
    return b"".join(chunks)


def fetch_10m() -> int:
    data = download(URL_10M)
    if data is None:
        return 1
    try:
        parsed = read_shapefile_zip(data, "ne_10m_admin_0_map_subunits")
    except (ValueError, LookupError) as e:
        print(f"Unexpected file contents ({e}); not saved.")
        return 1
    feats = parsed["features"]
    units = {f["properties"].get("SUBUNIT") for f in feats}
    if len(feats) < 200 or not all(f["properties"].get("NAME") for f in feats) \
            or not {"Tristan da Cunha", "Pitcairn Islands"} <= units:
        print("Unexpected file contents; not saved.")
        return 1
    for f in feats:
        f["properties"] = {k: f["properties"].get(k, "") for k in KEEP_PROPS}
    COUNTRIES_10M_FILE.parent.mkdir(parents=True, exist_ok=True)
    COUNTRIES_10M_FILE.write_text(json.dumps(parsed, separators=(",", ":")), encoding="utf-8")
    print(f"Saved {len(feats)} map subunits (1:10m) to {COUNTRIES_10M_FILE}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    COUNTRIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not COUNTRIES_FILE.is_file() or "--10m" not in argv:
        data = download(URL)
        if data is None:
            return 1
        parsed = json.loads(data)
        if parsed.get("type") != "FeatureCollection" or len(parsed.get("features", [])) < 200:
            print("Unexpected file contents; not saved.")
            return 1
        COUNTRIES_FILE.write_bytes(data)
        print(f"Saved {len(parsed['features'])} countries to {COUNTRIES_FILE}")
    if "--10m" in argv:
        return fetch_10m()
    return 0


if __name__ == "__main__":
    sys.exit(main())
