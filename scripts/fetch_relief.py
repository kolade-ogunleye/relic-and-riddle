"""
Download the Natural Earth shaded relief (public domain) the map graphics draw their hillshade from.

  python scripts/fetch_relief.py            # 1:10m SR_HR.tif, 21600x10800 (the default; what the maps sample)
  python scripts/fetch_relief.py --medium   # 1:50m SR_50M.tif, 10800x5400 (smaller; maps upsample it ~2x)

Saved to tools/geo/relief/ (gitignored). src/geo.RELIEF_FILE uses SR_HR.tif when present, else SR_50M.tif.
Without either, maps render flat. Source: naturalearthdata.com, "Shaded Relief" raster downloads, served from
the Natural Earth CDN (naciscdn.org); terms in docs/sources.md.

The archive is streamed to disk under a size cap, the one .tif inside is extracted under its own cap (no other
member is read, and no archive path is used), and it is decoded and checked for the expected dimensions before
it replaces anything.
"""

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.geo import RELIEF_50M_FILE, RELIEF_HR_FILE  # noqa: E402

# Natural Earth's download pages link these; www.naturalearthdata.com/http//www.naturalearthdata.com/download/...
# redirects to the same files on naciscdn.org.
VARIANTS = {
    "high": {"urls": ("https://naciscdn.org/naturalearth/10m/raster/SR_HR.zip",
                      "https://www.naturalearthdata.com/http//www.naturalearthdata.com/download/10m/raster/SR_HR.zip"),
             "member": "SR_HR.tif", "size": (21600, 10800), "dest": RELIEF_HR_FILE},
    "medium": {"urls": ("https://naciscdn.org/naturalearth/50m/raster/SR_50M.zip",
                        "https://www.naturalearthdata.com/http//www.naturalearthdata.com/download/50m/raster/SR_50M.zip"),
               "member": "SR_50M.tif", "size": (10800, 5400), "dest": RELIEF_50M_FILE},
}
MAX_ZIP_BYTES = 250_000_000      # the archive as downloaded
MAX_TIF_BYTES = 600_000_000      # the extracted raster (21600x10800 8-bit is 233 MB uncompressed)
CHUNK = 1 << 20


def download(url: str, dest: Path, max_bytes: int = MAX_ZIP_BYTES) -> bool:
    """Streams `url` to `dest`; False (and `dest` removed) past the size cap."""
    total = 0
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        declared = int(resp.headers.get("Content-Length") or 0)
        if declared > max_bytes:
            print(f"{url} is {declared} bytes, over the {max_bytes} byte cap; not downloaded.")
            return False
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(CHUNK):
                total += len(chunk)
                if total > max_bytes:
                    break
                fh.write(chunk)
    if total > max_bytes:
        dest.unlink(missing_ok=True)
        print("Download exceeded the size cap; aborting.")
        return False
    print(f"Downloaded {total} bytes from {url}")
    return True


def extract_member(archive: Path, member: str, dest: Path, max_bytes: int = MAX_TIF_BYTES) -> bool:
    """Copies the one archive entry whose file name is `member` to `dest` (its archive path is not used)."""
    with zipfile.ZipFile(archive) as zf:
        infos = [i for i in zf.infolist() if Path(i.filename).name == member and not i.is_dir()]
        if len(infos) != 1:
            print(f"Expected one {member} in the archive, found {len(infos)}; not saved.")
            return False
        info = infos[0]
        if info.file_size > max_bytes:
            print(f"{member} is {info.file_size} bytes uncompressed, over the cap; not saved.")
            return False
        copied = 0
        with zf.open(info) as src, dest.open("wb") as out:
            while chunk := src.read(CHUNK):
                copied += len(chunk)
                if copied > max_bytes:
                    break
                out.write(chunk)
    if copied > max_bytes:
        dest.unlink(missing_ok=True)
        print(f"{member} exceeded the size cap while extracting; not saved.")
        return False
    return True


def valid_relief(path: Path, size: tuple[int, int]) -> bool:
    """Decodes the raster (our own trusted Natural Earth file once its size checks out) and checks its shape."""
    from src.trusted_image import open_trusted
    try:
        with open_trusted(path, size[0] * size[1], formats=("TIFF",)) as im:   # its own limit, not the global one
            if im.size != size:
                print(f"Unexpected raster ({im.format}, {im.size}; expected TIFF {size}); not saved.")
                return False
            lo, hi = im.convert("L").getextrema()
    except (OSError, Image.DecompressionBombError) as e:
        print(f"Could not read the raster: {e}; not saved.")
        return False
    if hi - lo < 64:   # a shaded relief spans dark slopes to bright ones; a blank raster does not
        print(f"Raster has almost no contrast ({lo}..{hi}); not saved.")
        return False
    return True


def fetch(variant: str) -> int:
    spec = VARIANTS[variant]
    dest: Path = spec["dest"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    zip_part, tif_part = dest.with_suffix(".zip.part"), dest.with_suffix(".tif.part")
    try:
        for url in spec["urls"]:
            try:
                if download(url, zip_part):
                    break
                return 1
            except requests.RequestException as e:
                print(f"{url}: {e}")
        else:
            print("No source could be reached; nothing saved.")
            return 1
        try:
            if not extract_member(zip_part, spec["member"], tif_part):
                return 1
        except zipfile.BadZipFile:
            print("The download is not a zip archive; not saved.")
            return 1
        if not valid_relief(tif_part, spec["size"]):
            return 1
        shutil.move(str(tif_part), str(dest))
        print(f"Saved {spec['member']} ({spec['size'][0]}x{spec['size'][1]}) to {dest}")
        return 0
    finally:
        zip_part.unlink(missing_ok=True)
        tif_part.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--medium", action="store_true", help="the 1:50m SR_50M.tif (10800x5400) instead of SR_HR.tif")
    args = ap.parse_args(argv)
    return fetch("medium" if args.medium else "high")


if __name__ == "__main__":
    sys.exit(main())
