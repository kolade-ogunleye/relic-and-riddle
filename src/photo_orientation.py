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


def sheet_name(topic_dir: Path, item: Dict[str, Any]) -> str:
    """How the operator knows the photo: its number on the /photos sheet and its title ("photo 3, The
    Antikythera Mechanism (3471976134)"), never the file name: 07_commons.jpg is photo 3 on the sheet, and
    naming the file sent the operator to photo 7 (2026-10-27)."""
    title = str(item.get("title") or "").removeprefix("File:").rsplit(".", 1)[0]
    try:
        from .credits import sheet_order
        shown = [i.get("file") for i in sheet_order(topic_dir, _items(topic_dir))]
        number = f"photo {shown.index(item['file']) + 1}"
    except Exception:
        number = "a photo"
    return f"{number}, {title}" if title else number


def put_upright(topic_dir: Path) -> Tuple[List[str], Set[str]]:
    """Fetches each turned-over photo again, upright. Returns (what was done, in words; the files still turned
    over, which the run must not use)."""
    from . import commons
    done: List[str] = []
    still: Set[str] = set()
    for item in sideways(topic_dir):
        name, path = item["file"], topic_dir / item["file"]
        shown = sheet_name(topic_dir, item)
        url = item.get("download_url")
        requested = path.with_name(path.stem + ".upright.png")
        fresh = requested
        target = path.with_suffix(".png")
        installed = False
        credit_path = topic_dir / "credits.json"
        old_credits = credit_path.read_bytes()
        credit_temp = topic_dir / "credits.upright.tmp"
        backup = topic_dir / BACKUP_DIR / name
        saved_sidecars: dict = {}
        try:
            if not url:
                raise ValueError("no download link recorded")
            commons.download_jpeg(url, requested, provider=item.get("provider") or None)  # its provider's hosts (QF-44)
            fresh = commons.stored(requested)   # the kept source JPEG, or the decoded PNG (QF-13)
            target = path.with_suffix(fresh.suffix)
            saved_sidecars = {Path(str(target) + suffix): (Path(str(target) + suffix).read_bytes()
                              if Path(str(target) + suffix).exists() else None)
                             for suffix in (".quality.json", ".presentation.json")}
            if turned(fresh, item.get("width"), item.get("height")):
                raise ValueError("Commons gave it turned over again")
            if target != path and target.exists():
                raise ValueError(f"A separate {target.suffix} already exists; preserve both for review")
            from . import image_presentation
            from .credits import sha256_file
            from .photo_pool import signature
            essential = image_presentation._record(path)
            data = json.loads(old_credits)
            records = data if isinstance(data, list) else (data.get("images") or data.get("items") or [])
            for entry in records:
                if isinstance(entry, dict) and entry.get("file") == name:
                    entry.update(file=target.name, **signature(fresh))
                    if "sha256" in entry:
                        entry["sha256"] = sha256_file(fresh)
                    entry.pop("vision_review", None)  # old review bytes cannot approve a refreshed original
            if isinstance(data, dict):
                data["reviewed"] = False
            credit_temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            (topic_dir / BACKUP_DIR).mkdir(exist_ok=True)
            if backup.exists() and sha256_file(backup) != sha256_file(path):
                backup = backup.with_name(f"{backup.stem}-{sha256_file(path)[:12]}{backup.suffix}")
            shutil.copy2(path, backup)
            fresh.replace(target)
            installed = True
            if essential:
                image_presentation.designate(target, essential["reason"])
            quality = Path(str(fresh) + ".quality.json")
            if quality.exists():
                quality.replace(Path(str(target) + ".quality.json"))
            credit_temp.replace(credit_path)
            if target != path:
                path.unlink()  # the original remains byte-for-byte in its backup, not twice in the active pool
            done.append(f"{shown}: was stored turned over; fetched again upright (the old file is kept as {BACKUP_DIR}/{backup.name})")
            log.info("Photo %s/%s was stored turned over; fetched again upright.", topic_dir.name, name)
        except Exception as e:
            for left in {fresh, requested, requested.with_suffix(".jpg")}:
                left.unlink(missing_ok=True)
            credit_temp.unlink(missing_ok=True)
            if installed:
                if target != path:
                    target.unlink(missing_ok=True)
                shutil.copy2(backup, path)
                credit_path.write_bytes(old_credits)
                for marker, previous in saved_sidecars.items():
                    if previous is None:
                        marker.unlink(missing_ok=True)
                    else:
                        marker.write_bytes(previous)
            still.add(name)
            done.append(f"{shown}: is stored turned over and could not be fetched again ({str(e)[:120]}); "
                        "this run leaves it out")
            log.warning("Photo %s/%s is turned over and was not fixed: %s", topic_dir.name, name, e)
    return done, still
