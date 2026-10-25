"""
src/image_review.py
===================
Merit-based review of a topic's photo pool, so an unavailable operator never blocks production.

A vision model sees every photo with its archive caption and the topic, and returns a fixed-format
verdict per photo: does it show this topic (not a namesake town, a different site or a later
replica presented as the original), is it technically usable, is it safe to publish, and is it a
near-duplicate of a better photo in the pool. Code, not the model, decides from those verdicts:

  - a photo is kept only if it shows the topic, is usable and safe, and isn't a duplicate;
  - the pool is approved only if enough photos are kept;
  - a human decision is never overridden (an operator's approval or rejection stands);
  - with no vision model available nothing is approved (fail closed).

The verdicts are stored per photo in credits.json, so the decision can be audited and reversed.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from . import commons, free_llm
from .config import MIN_SCENE_IMAGES
from .credits import load_credits, save_credits
from .llm import LLMError, image_block

log = logging.getLogger(__name__)

MAX_PHOTOS = 24  # one request per pool, so duplicates are judged across the whole pool


class PhotoVerdict(BaseModel):
    index: int                                   # 1-based, as labelled in the request
    shows_topic: Literal["yes", "partly", "no"]  # the topic itself, not a namesake or a different place
    usable: bool                                 # sharp enough, not a tiny crop, no heavy watermark/text overlay
    safe: bool                                   # no human remains on display, no identifiable bystanders as subject
    duplicate_of: Optional[int] = None           # a better photo of nearly the same view, if any
    people_prominent: bool = False               # visitors are a prominent part of it (their faces get blurred)
    reason: str                                  # one short sentence


class PoolReview(BaseModel):
    verdicts: List[PhotoVerdict]


SYSTEM = """You review the photo pool for one topic of a fact-checked history documentary channel. For every photo, judge it against the topic and its caption.
- shows_topic: "yes" if it shows the topic's own site, object, document or event (or an archival depiction of it); "partly" if it shows the immediate context (the surrounding landscape, the museum display of a find from the site); "no" if it shows something else: a town or person that only shares the name, a different site or object, unrelated modern scenes, souvenirs.
- usable: false for blurry, tiny, heavily watermarked, or mostly-text images that would look poor full-screen.
- safe: false for photos whose subject is human remains (other than ancient skulls or bones displayed as museum artefacts), injuries, or identifiable private people who are the main subject.
- duplicate_of: the number of a better photo showing nearly the same view, else null.
- people_prominent: true if present-day visitors or bystanders are a prominent part of the picture (their faces will be blurred, which draws the eye).
Judge from what you see; the caption tells you what the archive says it is. Be strict: a wrong picture is worse than no picture."""


def _human_decided(credits: Dict[str, Any]) -> bool:
    return credits.get("reviewed_by") == "operator"


def _verdicts(topic: Dict[str, Any], topic_dir: Path, images: List[Dict[str, Any]]) -> Optional[Dict[int, PhotoVerdict]]:
    """The vision model's verdict per photo (1-based), or None when no model could look."""
    loc = topic.get("location") or {}
    content: List[dict] = [{"type": "text", "text": (
        f"Topic: {topic.get('title', topic['id'])}\nPlace: {loc.get('name', '')}, {loc.get('country', '')}\n"
        f"Key facts: {'; '.join(topic.get('key_facts', [])[:5])}\n\nPhotos:")}]
    for i, img in enumerate(images, 1):
        content += [{"type": "text", "text": f"Photo {i}: {commons.caption(img)}"}, image_block(topic_dir / img["file"])]
    content.append({"type": "text", "text": f"Return one verdict for each of the {len(images)} photos."})
    for role in ("checker", "writer"):  # either free model will do; one is often overloaded
        try:
            review = free_llm.call(SYSTEM, content, PoolReview, role=role, task_class="light",
                                    effort="medium", max_tokens=6000)
            return {v.index: v for v in review.verdicts}
        except (LLMError, OSError) as e:
            log.warning("Photo review for %s on the %s model failed (%s).", topic["id"], role, e)
    return None


# ------------------------------------------------------------------ which approved photos a video may use
# Operator, 2026-10-25: photos that only partly showed the topic (other finds from the same wreck, the
# island) and photos never looked at reached the Antikythera video and carousel. Every photo now carries a
# tier, and a video uses the topic's own photos, the ones the operator chose to keep, and at most a couple
# of context shots; never a weak or unchecked one.
CONTEXT_SHOTS_MAX = 2
TIER_LABELS = {"topic": "Topic", "kept": "Kept by you", "context": "Context", "unchecked": "Unchecked", "weak": "Weak"}


def tier(img: Dict[str, Any]) -> str:
    """topic | kept | context | unchecked | weak, from the photo's vision verdict and the operator's keep."""
    if img.get("operator_keep"):
        return "kept"
    v = img.get("vision_review") or {}
    if "shows_topic" not in v:
        return "unchecked"
    if not (v.get("usable") and v.get("safe")) or v.get("duplicate_of"):
        return "weak"
    return {"yes": "topic", "partly": "context"}.get(v.get("shows_topic"), "weak")


def _relevance(img: Dict[str, Any]) -> float:
    try:
        return float(img.get("relevance") or 0)
    except (TypeError, ValueError):
        return 0.0


def usable_photos(images: List[Dict[str, Any]], context_max: int = CONTEXT_SHOTS_MAX) -> List[Dict[str, Any]]:
    """The photos a video may use, best first: the topic's own (most relevant first), those you kept, then at
    most `context_max` context shots. Unchecked and weak photos are left out."""
    by = {t: sorted((im for im in images if tier(im) == t), key=_relevance, reverse=True)
          for t in ("topic", "kept", "context")}
    return by["topic"] + by["kept"] + by["context"][:context_max]


def strong_stems(topic_dir: Path) -> Optional[set]:
    """File stems of the photos that show the topic or that you kept: the only ones a Story frame, a carousel
    slide or a cover uses (no context shots there). None for a folder without credits (photos you supplied)."""
    if not (Path(topic_dir) / "credits.json").is_file():
        return None
    return {Path(im["file"]).stem for im in load_credits(topic_dir).get("images", []) if tier(im) in ("topic", "kept")}


def only_strong(paths: List[Path], stems: Optional[set]) -> List[Path]:
    """`paths` (prepared copies keep their source's stem as a prefix) narrowed to the strong photos; the
    list as given when nothing is known or none is strong (a frame needs a picture)."""
    if stems is None:
        return list(paths)
    strong = [p for p in paths if any(Path(p).stem.startswith(s) for s in stems)]
    return strong or list(paths)


def screen_unchecked(topic: Dict[str, Any], topic_dir: Path) -> int:
    """Gives every photo without a vision verdict one, whatever was decided about the set: photos added after
    an approval were never looked at (production 2026-10-25). Only those photos are sent; nothing is dropped
    and the approval is not changed (the tier decides use). Returns how many were screened."""
    credits = load_credits(topic_dir)
    pending = [im for im in credits.get("images", []) if tier(im) == "unchecked"][:MAX_PHOTOS]
    if not pending:
        return 0
    by_index = _verdicts(topic, topic_dir, pending)
    if by_index is None:
        log.warning("Photos of %s not screened: no vision model answered; they stay unused.", topic["id"])
        return 0
    for i, img in enumerate(pending, 1):
        v = by_index.get(i)
        img["vision_review"] = (v.model_dump() if v else {"reason": "no verdict returned"}) | {"kept": tier({"vision_review": v.model_dump()}) in ("topic", "context") if v else False}
    save_credits(topic_dir, credits)
    try:  # the sheet's numbers follow the tiers: redrawn, so /drop N and /keep N mean the photo shown as N
        from .credits import write_contact_sheet
        write_contact_sheet(topic_dir, credits["images"])
    except Exception as e:
        log.debug("Contact sheet of %s not redrawn: %s", topic["id"], e)
    log.info("Screened %d new photo(s) of %s: %s.", len(pending), topic["id"],
             ", ".join(f"{im['file']}={tier(im)}" for im in pending))
    return len(pending)


def pool_fingerprint(images: List[Dict[str, Any]]) -> str:
    return ",".join(sorted(str(i.get("file", "")) for i in images))


def review_pool(topic: Dict[str, Any], topic_dir: Path, apply: bool = True, force: bool = False) -> Dict[str, Any]:
    """Reviews the pool and (with apply) records verdicts, drops failing photos from the pool record
    (files stay on disk) and approves the pool on merit. Returns a summary. A pool unchanged since its
    last review (no photo added or removed) is not sent again unless forced: the verdict would repeat,
    and each review spends free vision quota on every photo."""
    credits = load_credits(topic_dir)
    if _human_decided(credits):
        return {"topic": topic["id"], "status": "operator decision stands", "kept": len(credits["images"])}
    if not force and credits.get("review_fingerprint") and \
            credits["review_fingerprint"] == pool_fingerprint(credits.get("images", [])):
        status = "approved" if credits.get("reviewed") else "held"
        return {"topic": topic["id"], "status": f"{status} (unchanged since last review)", "kept": len(credits.get("images", []))}
    images = credits["images"][:MAX_PHOTOS]
    if not images:
        return {"topic": topic["id"], "status": "no photos", "kept": 0}
    by_index = _verdicts(topic, topic_dir, images)
    if by_index is None:
        log.warning("Photo review for %s could not run; pool stays unapproved.", topic["id"])
        return {"topic": topic["id"], "status": "review unavailable", "kept": 0}
    kept, dropped = [], []
    for i, img in enumerate(images, 1):
        v = by_index.get(i)
        # A photo you kept (/keep) stays whatever the model says: a re-review dropped kept photos and marked
        # them rejected, so a refill would never fetch them again (review, 2026-10-25).
        ok = bool(img.get("operator_keep")) or bool(v and v.shows_topic in ("yes", "partly") and v.usable and v.safe and not v.duplicate_of)
        img["vision_review"] = (v.model_dump() if v else {"reason": "no verdict returned"}) | {"kept": ok}
        (kept if ok else dropped).append(img)
    # Photos that show the topic itself come first, then context; crowded ones (blurred faces) last.
    kept.sort(key=lambda im: (im["vision_review"].get("people_prominent", False),
                              0 if im["vision_review"].get("shows_topic") == "yes" else 1))
    approved = len(kept) >= MIN_SCENE_IMAGES
    summary = {"topic": topic["id"], "status": "approved" if approved else "held", "kept": len(kept),
               "dropped": [(d["file"], d["vision_review"].get("reason", "")) for d in dropped]}
    if apply:
        credits.update(images=kept, reviewed=approved, reviewed_by="vision" if approved else None,
                       review_fingerprint=pool_fingerprint(kept),
                       review_note=(f"Vision review {datetime.now(timezone.utc):%Y-%m-%d}: kept {len(kept)} of "
                                    f"{len(images)}" + ("" if approved else f"; held: fewer than {MIN_SCENE_IMAGES}")),
                       rejected_by_review=credits.get("rejected_by_review", []) + dropped)
        save_credits(topic_dir, credits)
    return summary
