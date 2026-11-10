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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import ASSETS_DIR, BASE_DIR
from .credits import load_credits, save_credits

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 1800
TARGET_POOL = 20  # the fetcher's pool size  # Commons pacing makes a full pool take minutes


def _load_pool(folder: Path) -> dict:
    from .photo_pool import refresh
    credits = load_credits(folder)
    refresh(folder, credits.get("images", []))
    return credits


def pool_ready(topic_id: str, assets_dir: Optional[Path] = None, beats=None) -> bool:
    """Approved, and the video could use enough of its photos now (src/image_review.py readiness: Topic +
    Kept + at most CONTEXT_SHOTS_MAX Context, distinct by the files' current hashes, >= required_photos(beats)).
    Counting every photo showed pools as ready that then failed at run start (production 2026-10-28,
    run_greek_fire_siphon_1793038718)."""
    from .image_review import readiness, required_photos
    credits = _load_pool((assets_dir or ASSETS_DIR) / topic_id)
    return readiness(credits.get("images", []), bool(credits.get("reviewed")), need=required_photos(beats))["ready"]


def not_ready_reason(topic_id: str, assets_dir: Optional[Path] = None) -> str:
    """One line for the operator: why the pool cannot make a video yet, in the video's own counting."""
    from .image_review import readiness, readiness_text
    credits = _load_pool((assets_dir or ASSETS_DIR) / topic_id)
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


def _fetched_at(credits: dict) -> datetime | None:
    try:
        fetched = datetime.fromisoformat(str(credits.get("pool_fetched_at") or ""))
    except (TypeError, ValueError):
        return None
    return fetched if fetched.tzinfo is not None else None


def refresh_due(credits: dict, now=None) -> bool:
    """An automatic pool with fewer than PHOTO_POOL_REFRESH_BELOW usable photos gets a new search once its last
    recorded fetch is PHOTO_POOL_REFRESH_DAYS old, or when no fetch was ever recorded; a human decision stands."""
    from . import config
    from .image_review import usable_photos
    if credits.get("reviewed_by") == "operator" or len(usable_photos(credits.get("images", []))) >= config.PHOTO_POOL_REFRESH_BELOW:
        return False
    fetched = _fetched_at(credits)
    return fetched is None or (now or datetime.now(UTC)) - fetched >= timedelta(days=config.PHOTO_POOL_REFRESH_DAYS)


def fetched_recently(credits: dict, now=None) -> bool:
    """A fetch is recorded within PHOTO_POOL_REFRESH_DAYS: a search again now would find what that one found."""
    from . import config
    fetched = _fetched_at(credits)
    return fetched is not None and (now or datetime.now(UTC)) - fetched < timedelta(days=config.PHOTO_POOL_REFRESH_DAYS)


def shot_quotas_met(images: list[dict]) -> bool:
    """The fetch target for shot variety: at least three verified shot classes among the usable photos and none
    over half of them. "unknown" is not a class. A target only: a pool that misses it stays ready."""
    from collections import Counter

    from .image_review import usable_photos
    from .photo_pool import SHOT_CLASSES, shot_class

    usable = usable_photos(images)
    known = Counter(c for c in (shot_class(image) for image in usable) if c in SHOT_CLASSES)
    if len(known) < 3:
        return False
    return max(known.values()) <= (len(usable) + 1) // 2


def ensure_pool(topic: Dict[str, Any], assets_dir: Optional[Path] = None, beats=None) -> bool:
    """True when the topic has a pool the video can use, screening, fetching and reviewing it first if needed.
    A pool short of usable photos is topped up, not only an empty or thinned one: an approved pool of 2 Topic
    + 4 Context counted as ready and was never refilled (production 2026-10-28).

    QF-58: the floor is required_photos(beats), at least MIN_SCENE_IMAGES distinct usable photos (one per photo
    beat when the narration's beats are given). A small automatic pool is searched again when refresh_due. Up to
    PHOTO_POOL_FETCH_PASSES fetch/review passes run while the pool is short or its shot classes miss
    shot_quotas_met, stopping at the first pass that adds nothing. Shot quotas are a fetch target only
    (decisions/QF-58-gate.md): a pool at the floor stays ready without them. A pool at the floor that was fetched
    within the refresh period is not searched again for more beats or for quotas (that search found what there is),
    so the run-start cost is at most one bounded refill per refresh period. A pool still short after a search within
    the period gets one search per call, not PHOTO_POOL_FETCH_PASSES. A human decision is never refetched."""
    from . import config
    from .image_review import (
        MAX_PHOTOS,
        readiness,
        required_photos,
        review_pool,
        screen_unchecked,
    )
    assets_dir = assets_dir or ASSETS_DIR
    tid = topic["id"]
    folder = assets_dir / tid
    credits = _load_pool(folder)
    need = required_photos(beats)

    def settled() -> bool:
        if pool_ready(tid, assets_dir, beats) and (credits.get("reviewed_by") == "operator" or not refresh_due(credits)):
            return True
        return pool_ready(tid, assets_dir) and not refresh_due(credits) and fetched_recently(credits)

    if settled():
        return True
    if readiness(credits.get("images", []), bool(credits.get("reviewed")), need=need)["pending"]:
        # Approved, with photos never looked at (added later, or approved by you before screening): the run
        # would screen them first, so they are screened here and the pool judged on the result.
        screen_unchecked(topic, folder)
        credits = _load_pool(folder)
        if settled():
            return True
    if credits.get("reviewed_by") == "operator":
        log.info("Photo pool for %s: %s.", tid, not_ready_reason(tid, assets_dir))
        return pool_ready(tid, assets_dir)  # the operator held, rejected or approved it; that decision stands
    # Extra passes only when no search is recorded within the refresh period; a pool searched in the period gets
    # one search per call, as before QF-58 (a not-ready pool is called at each run start it ranks ahead and nightly).
    passes = 1 if fetched_recently(credits) else config.PHOTO_POOL_FETCH_PASSES
    for attempt in range(passes):
        images = credits.get("images", [])
        r = readiness(images, bool(credits.get("reviewed")), need=need)
        gap = max(0, r["need"] - r["usable"] - r["unchecked"])  # missing even if every unchecked photo passes
        before = {image.get("source_sha256") or image.get("file") for image in images}
        target = min(MAX_PHOTOS, max(TARGET_POOL, need, len(images) + 2 * gap + 2 * attempt))
        if len(images) < target or refresh_due(credits) or gap or not shot_quotas_met(images):
            # Two candidates per missing photo, as about half of the fetched ones show the topic itself, within
            # what one review looks at; an empty or thinned pool is refilled to the fetcher's pool size. The
            # search is recorded whatever its result, so a failing or timed-out search is not repeated at every
            # run start for a pool that is ready.
            _fetch(tid, assets_dir, target if len(images) >= TARGET_POOL else None)
            credits = _load_pool(folder)
            credits["pool_fetched_at"] = datetime.now(UTC).isoformat()
            save_credits(folder, credits)
        summary = review_pool(topic, folder)
        log.info("Photo pool for %s: %s (%s kept; %s).", tid, summary["status"], summary["kept"],
                 not_ready_reason(tid, assets_dir))
        credits = _load_pool(folder)
        images = credits.get("images", [])
        if pool_ready(tid, assets_dir, beats) and shot_quotas_met(images):
            return True
        if {image.get("source_sha256") or image.get("file") for image in images} == before:
            log.info("The fetch/review added no photos for %s; stopping the refill.", tid)
            break
    if not pool_ready(tid, assets_dir, beats):
        return False
    if not shot_quotas_met(credits.get("images", [])):
        log.info("Photo pool for %s is ready; its shot classes miss the variety target (a fetch target only).", tid)
    return True


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
