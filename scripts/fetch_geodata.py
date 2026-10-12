"""
Download the Natural Earth 1:50m country outlines (public domain) used by the map graphic.

  python scripts/fetch_geodata.py

Saved to tools/geo/ne_50m_admin_0_countries.geojson (gitignored, about 3 MB). The file is
parsed as JSON only; a size cap guards the download.
"""

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.geo import COUNTRIES_FILE  # noqa: E402

URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_admin_0_countries.geojson"
MAX_BYTES = 30_000_000


def main() -> int:
    COUNTRIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(URL, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        data = b""
        for chunk in resp.iter_content(1 << 16):
            data += chunk
            if len(data) > MAX_BYTES:
                print("Download exceeded the size cap; aborting.")
                return 1
    parsed = json.loads(data)
    if parsed.get("type") != "FeatureCollection" or len(parsed.get("features", [])) < 200:
        print("Unexpected file contents; not saved.")
        return 1
    COUNTRIES_FILE.write_bytes(data)
    print(f"Saved {len(parsed['features'])} countries to {COUNTRIES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
