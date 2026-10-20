"""
src/asset_pipeline.py
=====================
Gets a topic's photo pool ready without waiting for a person: fill it from the licensed sources if
it is short, then have the vision review approve it on merit (src/image_review.py). The operator's
own approval or rejection always stands. Used before each run and nightly for the next topics.
"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .config import ASSETS_DIR, BASE_DIR, MIN_SCENE_IMAGES
from .credits import load_credits

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 1800
TARGET_POOL = 20  # the fetcher's pool size  # Commons pacing makes a full pool take minutes


def pool_ready(topic_id: str, assets_dir: Optional[Path] = None) -> bool:
    credits = load_credits((assets_dir or ASSETS_DIR) / topic_id)
    return bool(credits.get("reviewed")) and len(credits.get("images", [])) >= MIN_SCENE_IMAGES


def ensure_pool(topic: Dict[str, Any], assets_dir: Optional[Path] = None) -> bool:
    """True when the topic has an approved pool, fetching and reviewing it first if needed."""
    assets_dir = assets_dir or ASSETS_DIR
    tid = topic["id"]
    if pool_ready(tid, assets_dir):
        return True
    credits = load_credits(assets_dir / tid)
    if credits.get("reviewed_by") == "operator":
        return False  # the operator held or rejected it; that decision stands
    if len(credits.get("images", [])) < TARGET_POOL:  # empty, or thinned by a review: refill
        log.info("Fetching photos for %s.", tid)
        try:
            subprocess.run([sys.executable, str(BASE_DIR / "scripts" / "fetch_commons_images.py"), "--topic-id", tid,
                            "--out-dir", str(assets_dir)], cwd=str(BASE_DIR), timeout=FETCH_TIMEOUT,
                           capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            log.warning("Fetching photos for %s timed out.", tid)
    from .image_review import review_pool
    summary = review_pool(topic, assets_dir / tid)
    log.info("Photo pool for %s: %s (%s kept).", tid, summary["status"], summary["kept"])
    return pool_ready(tid, assets_dir)


def first_ready(topics: Iterable[Dict[str, Any]], assets_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The first topic (in production order) whose pool is, or can now be made, ready."""
    for topic in topics:
        if ensure_pool(topic, assets_dir):
            return topic
        log.info("Skipping %s: no approved photo pool.", topic["id"])
    return None
