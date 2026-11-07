"""
src/topic_engine.py
===================
Topic rotation and status tracking, backed by the SQLite database (src/db.py).
"""

import logging
from typing import Optional, Dict, Any, List

from .db import PipelineDatabase

log = logging.getLogger(__name__)


class TopicEngine:
    def __init__(self, db: Optional[PipelineDatabase] = None):
        self.db = db or PipelineDatabase()

    def load_bank(self) -> List[Dict[str, Any]]:
        return self.db.list_all_topics()

    def get_published_ids(self) -> set:
        return {t["id"] for t in self.db.list_all_topics() if t["times_published"] > 0}

    def get_next_topic(self) -> Dict[str, Any]:
        return self.db.get_next_topic()

    def get_topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_topic_by_id(topic_id)

    def record_status(self, topic_id: str, status: str, run_id: Optional[str] = None):
        self.db.record_topic_status(topic_id, status, run_id=run_id)
        log.info("Topic '%s' -> '%s'.", topic_id, status)


# ------------------------------------------------------------------ topic image gate (2026-11-02)
# A topic whose approved pool holds few distinct authoritative photos leaves lines no photo shows, and renders failed
# the video check for "no photo for this beat". At selection, a topic whose reviewed pool has fewer than
# config.TOPIC_MIN_AUTHORITATIVE_IMAGES such photos is demoted behind the topics that have enough, and said with its
# reason; it is never dropped (it is still produced when nothing stronger is ready). A topic whose pool has not been
# fetched or reviewed yet is not judged (its count is unknown) and keeps its place.
AUTHORITATIVE_TIERS = ("topic", "kept")


def authoritative_images(topic_id: str, assets_dir=None) -> int | None:
    """Distinct approved authoritative photos in a topic's pool: records of a reviewed credits.json whose file is
    there, judged Topic or Kept (src/image_review.tier: usable, safe, not a duplicate, or kept by the operator), with a
    licence and a source to credit; distinct by file and by archive title. None when the pool is not reviewed yet."""
    import json
    from pathlib import Path

    from .config import ASSETS_DIR
    from .image_review import tier
    folder = Path(assets_dir or ASSETS_DIR) / topic_id
    try:
        credits = json.loads((folder / "credits.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(credits, dict) or not credits.get("reviewed"):
        return None
    files, titles = set(), set()
    for rec in credits.get("images") or []:
        if not isinstance(rec, dict) or not rec.get("file") or not (folder / str(rec["file"])).is_file():
            continue
        if tier(rec) not in AUTHORITATIVE_TIERS or not rec.get("license") or not (rec.get("source_url") or rec.get("title")):
            continue
        title = " ".join(str(rec.get("title") or "").lower().split())
        if rec["file"] in files or (title and title in titles):
            continue
        files.add(rec["file"])
        if title:
            titles.add(title)
    return len(files)


def image_gate(topic: dict[str, Any], assets_dir=None, threshold: int | None = None) -> str:
    """The reason a topic is demoted ("" when it passes or its pool is not reviewed yet)."""
    from . import config
    need = int(config.TOPIC_MIN_AUTHORITATIVE_IMAGES if threshold is None else threshold)
    have = authoritative_images(topic["id"], assets_dir)
    if have is None or have >= need:
        return ""
    return (f"only {have} distinct approved photo(s) from the archives (needs {need}); lines would go without a "
            "photo. Add photos, or fetch and review more, to bring it forward")


def demote_thin(topics: list[dict[str, Any]], assets_dir=None,
                threshold: int | None = None) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """(topics with those failing image_gate moved behind the rest, each order otherwise kept; [(topic, reason)] of
    the demoted ones). Never raises: a topic whose pool cannot be read keeps its place."""
    strong, thin, flagged = [], [], []
    for t in topics:
        try:
            why = image_gate(t, assets_dir, threshold)
        except (OSError, ValueError, TypeError, KeyError) as e:  # a ranking hint, never a way for selection to fail
            log.debug("Image gate for %s could not run: %s", t.get("id"), e)
            why = ""
        (thin if why else strong).append(t)
        if why:
            flagged.append((t, why))
    if flagged:
        log.info("Topic image gate: %d topic(s) demoted: %s", len(flagged),
                 "; ".join(f"{t['id']} ({why.split(';')[0]})" for t, why in flagged))
    return strong + thin, flagged
