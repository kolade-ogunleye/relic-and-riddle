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
