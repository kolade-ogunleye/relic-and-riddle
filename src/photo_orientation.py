"""Photos stored turned over are put upright before a run uses them.

Before 2026-10-25 the fetcher re-encoded a photo without applying its EXIF orientation, so a portrait photo
stored sideways with a rotate flag was kept sideways (production's Antikythera 07_commons.jpg: 4000x3000 for
a 3000x4000 original, 2026-10-27). Commons records each original's width and height in credits.json; a file
whose orientation disagrees is fetched again upright (commons.download_jpeg applies the orientation), the old
file kept in .orientation_backup/. One that cannot be fetched again is left out of the run, never used sideways.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from PIL import Image

log = logging.getLogger(__name__)
BACKUP_DIR = ".orientation_backup"


def _items(topic_dir: Path) -> List[Dict[str, Any]]:
    try:
        credits = json.loads((topic_dir / "credits.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = credits if isinstance(credits, list) else (credits.get("images") or credits.get("items") or [])
    return [i for i in items if isinstance(i, dict) and i.get("file")]


def turned(path: Path, width: Any, height: Any) -> bool:
    """Whether the file's orientation disagrees with the original's recorded size (a square never does)."""
    try:
        recorded_w, recorded_h = int(width), int(height)
        with Image.open(path) as im:
            w, h = im.size
    except (OSError, ValueError, TypeError):
        return False
    return recorded_w != recorded_h and w != h and (w > h) != (recorded_w > recorded_h)


def sideways(topic_dir: Path) -> List[Dict[str, Any]]:
    """The topic's photos stored turned over against Commons' record."""
    return [i for i in _items(topic_dir)
            if (topic_dir / i["file"]).is_file() and turned(topic_dir / i["file"], i.get("width"), i.get("height"))]


def put_upright(topic_dir: Path) -> Tuple[List[str], Set[str]]:
    """Fetches each turned-over photo again, upright. Returns (what was done, in words; the files still turned
    over, which the run must not use)."""
    from . import commons
    done: List[str] = []
    still: Set[str] = set()
    for item in sideways(topic_dir):
        name, path = item["file"], topic_dir / item["file"]
        url = item.get("download_url")
        fresh = path.with_name(path.stem + ".upright.jpg")
        try:
            if not url:
                raise ValueError("no download link recorded")
            commons.download_jpeg(url, fresh)
            if turned(fresh, item.get("width"), item.get("height")):
                raise ValueError("Commons gave it turned over again")
            (topic_dir / BACKUP_DIR).mkdir(exist_ok=True)
            shutil.copy2(path, topic_dir / BACKUP_DIR / name)
            fresh.replace(path)
            done.append(f"{name}: was stored turned over; fetched again upright (the old file is in {BACKUP_DIR}/)")
            log.info("Photo %s/%s was stored turned over; fetched again upright.", topic_dir.name, name)
        except Exception as e:
            fresh.unlink(missing_ok=True)
            still.add(name)
            done.append(f"{name}: is stored turned over and could not be fetched again ({str(e)[:120]}); "
                        "this run leaves it out")
            log.warning("Photo %s/%s is turned over and was not fixed: %s", topic_dir.name, name, e)
    return done, still
