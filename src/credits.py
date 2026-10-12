"""
src/credits.py
==============
Licence and review tracking for fetched scene images.

assets/<topic_id>/credits.json:
  {"reviewed": false, "images": [{"file", "title", "artist", "license", "license_url",
                                   "source_url", "attribution_required"}, ...]}

Fetched sets start unreviewed; the pipeline refuses them until a person has checked the
contact sheet and run `python main.py assets approve <topic_id>`. Attribution-required
licences (CC BY) are credited in the post caption.
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

CREDITS_FILE = "credits.json"
CONTACT_SHEET = "contact_sheet.jpg"

_ALLOWED = re.compile(r"^(public domain|pd\b|pd-|cc0|cc by \d)", re.IGNORECASE)
_SHARE_ALIKE = re.compile(r"^cc by-sa \d", re.IGNORECASE)


def license_allowed(short_name: str, allow_share_alike: bool = False) -> bool:
    """PD / CC0 / CC BY by default. CC BY-SA only on request (it can bind the derived video)."""
    name = (short_name or "").strip()
    if _ALLOWED.match(name):
        return True
    return allow_share_alike and bool(_SHARE_ALIKE.match(name))


def normalize(text: str) -> str:
    """Lowercase, accent-free, underscores as spaces: for keyword matching on file titles."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).replace("_", " ").lower()


def load_credits(topic_dir: Path) -> Dict[str, Any]:
    """Returns {"reviewed": bool, "images": [...]}, dropping entries whose file was deleted."""
    path = topic_dir / CREDITS_FILE
    if not path.exists():
        return {"reviewed": False, "images": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):  # legacy format
        data = {"reviewed": False, "images": data}
    data["images"] = [c for c in data.get("images", []) if (topic_dir / c.get("file", "")).exists()]
    return data


def save_credits(topic_dir: Path, data: Dict[str, Any]) -> None:
    (topic_dir / CREDITS_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def caption_credit_line(credits: Dict[str, Any]) -> str:
    """One compact attribution line for images whose licence requires it."""
    seen, parts = set(), []
    for c in credits.get("images", []):
        if not c.get("attribution_required", True):
            continue
        key = (c.get("artist") or "Unknown", c.get("license") or "")
        if key not in seen:
            seen.add(key)
            parts.append(f"{key[0]} ({key[1]})")
    return ("📷 Images via Wikimedia Commons: " + "; ".join(parts)) if parts else ""


# ---------------------------------------------------------------- review helpers
def write_contact_sheet(topic_dir: Path, images: List[Dict[str, Any]]) -> Path:
    """Grid of the set's images with a number on each tile (used by /drop and the CLI)."""
    from PIL import Image, ImageDraw

    from .typography import get_font

    cell_w, cell_h, cols = 360, 270, 4
    rows = max(1, (len(images) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = get_font("Anton", 40)
    for i, c in enumerate(images):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        with Image.open(topic_dir / c["file"]) as im:
            im.thumbnail((cell_w - 8, cell_h - 8))
            sheet.paste(im, (x + 4, y + 4))
        draw.rounded_rectangle([x + 10, y + 10, x + 70, y + 66], radius=8, fill=(0, 0, 0))
        draw.text((x + 40, y + 38), str(i + 1), font=font, fill=(255, 215, 0), anchor="mm")
    path = topic_dir / CONTACT_SHEET
    sheet.save(path, quality=85)
    return path


def approve_set(topic_dir: Path, min_images: int) -> str:
    """Marks a fetched set reviewed. Raises ValueError if it can't be."""
    if not (topic_dir / CREDITS_FILE).exists():
        raise ValueError(f"No fetched images for {topic_dir.name}.")
    credits = load_credits(topic_dir)
    if len(credits["images"]) < min_images:
        raise ValueError(f"Only {len(credits['images'])} images; need {min_images}. Re-run the fetcher to refill.")
    credits["reviewed"] = True
    save_credits(topic_dir, credits)
    return f"Approved {len(credits['images'])} images for {topic_dir.name}."


def drop_images(topic_dir: Path, numbers: List[int]) -> str:
    """Deletes images by their contact-sheet number (1-based) and marks the set unreviewed."""
    credits = load_credits(topic_dir)
    images = credits["images"]
    bad = [n for n in numbers if not 1 <= n <= len(images)]
    if bad or not numbers:
        raise ValueError(f"Numbers must be between 1 and {len(images)}.")
    for n in sorted(set(numbers), reverse=True):
        (topic_dir / images[n - 1]["file"]).unlink(missing_ok=True)
        del images[n - 1]
    credits["reviewed"] = False
    save_credits(topic_dir, credits)
    if images:
        write_contact_sheet(topic_dir, images)
    return f"Removed {len(set(numbers))}; {len(images)} images remain in {topic_dir.name}."
