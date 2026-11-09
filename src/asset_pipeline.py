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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import ASSETS_DIR, BASE_DIR
from .credits import load_credits

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 1800
TARGET_POOL = 20  # the fetcher's pool size  # Commons pacing makes a full pool take minutes


def pool_ready(topic_id: str, assets_dir: Optional[Path] = None) -> bool:
    """Approved, and the video could use enough of its photos now (src/image_review.py readiness: Topic +
    Kept + at most CONTEXT_SHOTS_MAX Context >= MIN_SCENE_IMAGES). Counting every photo showed pools as ready
    that then failed at run start (production 2026-10-28, run_greek_fire_siphon_1793038718)."""
    from .image_review import video_ready
    credits = load_credits((assets_dir or ASSETS_DIR) / topic_id)
    return video_ready(credits.get("images", []), bool(credits.get("reviewed")))


def not_ready_reason(topic_id: str, assets_dir: Optional[Path] = None) -> str:
    """One line for the operator: why the pool cannot make a video yet, in the video's own counting."""
    from .image_review import readiness, readiness_text
    credits = load_credits((assets_dir or ASSETS_DIR) / topic_id)
    images = credits.get("images", [])
    if not images and credits.get("fetch_held"):
        return "no photos: the photo search is held. " + credits["fetch_held"]
    if not images:
        rejected = len(credits.get("rejected_by_review", []))
        return ("no photos: the fetch found nothing new" + (f" ({rejected} earlier ones were rejected)" if rejected else "")
                + "; upload photos or widen its image queries")
    r = readiness(images, bool(credits.get("reviewed")))
    text = readiness_text(images)
    if r["ready"]:
        return "ready: " + text
    if r["pending"]:
        return f"approved, {text}; its unchecked photos are looked at before a run"
    if credits.get("reviewed"):
        return f"approved, but short for a video: {text}"
    if credits.get("reviewed_by") == "operator":
        return f"held by you: {text}"
    return f"not approved: {text}"


def _fetch(tid: str, assets_dir: Path, target: Optional[int] = None) -> None:
    cmd = [sys.executable, str(BASE_DIR / "scripts" / "fetch_commons_images.py"), "--topic-id", tid,
           "--out-dir", str(assets_dir)]
    if target:
        cmd += ["--target-images", str(target)]
    log.info("Fetching photos for %s%s.", tid, f" (up to {target})" if target else "")
    try:
        subprocess.run(cmd, cwd=str(BASE_DIR), timeout=FETCH_TIMEOUT, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        log.warning("Fetching photos for %s timed out.", tid)


def ensure_pool(topic: Dict[str, Any], assets_dir: Optional[Path] = None) -> bool:
    """True when the topic has a pool the video can use, screening, fetching and reviewing it first if needed.
    A pool short of usable photos is topped up, not only an empty or thinned one: an approved pool of 2 Topic
    + 4 Context counted as ready and was never refilled (production 2026-10-28)."""
    from .image_review import MAX_PHOTOS, readiness, review_pool, screen_unchecked
    assets_dir = assets_dir or ASSETS_DIR
    tid = topic["id"]
    if pool_ready(tid, assets_dir):
        return True
    credits = load_credits(assets_dir / tid)
    if readiness(credits.get("images", []), bool(credits.get("reviewed")))["pending"]:
        # Approved, with photos never looked at (added later, or approved by you before screening): the run
        # would screen them first, so they are screened here and the pool judged on the result.
        screen_unchecked(topic, assets_dir / tid)
        if pool_ready(tid, assets_dir):
            return True
        credits = load_credits(assets_dir / tid)
    if credits.get("reviewed_by") == "operator":
        log.info("Photo pool for %s: %s.", tid, not_ready_reason(tid, assets_dir))
        return False  # the operator held, rejected or approved it; that decision stands (no refetch over it)
    images = credits.get("images", [])
    r = readiness(images, bool(credits.get("reviewed")))
    gap = max(0, r["need"] - r["usable"] - r["unchecked"])  # missing even if every unchecked photo passes
    before = len(images)
    if before < TARGET_POOL:  # empty, or thinned by a review: refill to the fetcher's pool size
        _fetch(tid, assets_dir)
    elif gap and before < MAX_PHOTOS:
        # A full pool of mostly context shots: fetch a few more (two candidates per missing photo, as about
        # half of the fetched ones show the topic itself), within what one review looks at.
        _fetch(tid, assets_dir, min(MAX_PHOTOS, before + 2 * gap))
    if before < TARGET_POOL or gap:
        after = len(load_credits(assets_dir / tid).get("images", []))
        if after == before:
            log.info("The fetch added no photos for %s.", tid)
    summary = review_pool(topic, assets_dir / tid)
    log.info("Photo pool for %s: %s (%s kept; %s).", tid, summary["status"], summary["kept"],
             not_ready_reason(tid, assets_dir))
    return pool_ready(tid, assets_dir)


def first_ready(topics: Iterable[Dict[str, Any]], assets_dir: Optional[Path] = None,
                skipped: Optional[List[Tuple[Dict[str, Any], str]]] = None) -> Optional[Dict[str, Any]]:
    """The first topic (in production order) whose pool is, or can now be made, ready for the video. Each
    topic passed over is logged with its reason and, given a `skipped` list, added to it as (topic, reason)
    so the caller can say so: a run is never started on a pool the video would refuse."""
    for topic in topics:
        if ensure_pool(topic, assets_dir):
            return topic
        reason = not_ready_reason(topic["id"], assets_dir)
        log.info("Skipping %s: %s.", topic["id"], reason)
        if skipped is not None:
            skipped.append((topic, reason))
    return None
