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
    loc = topic.get("location") or {}
    content: List[dict] = [{"type": "text", "text": (
        f"Topic: {topic.get('title', topic['id'])}\nPlace: {loc.get('name', '')}, {loc.get('country', '')}\n"
        f"Key facts: {'; '.join(topic.get('key_facts', [])[:5])}\n\nPhotos:")}]
    for i, img in enumerate(images, 1):
        content += [{"type": "text", "text": f"Photo {i}: {commons.caption(img)}"}, image_block(topic_dir / img["file"])]
    content.append({"type": "text", "text": f"Return one verdict for each of the {len(images)} photos."})
    review = None
    for role in ("checker", "writer"):  # either free model will do; one is often overloaded
        try:
            review = free_llm.call(SYSTEM, content, PoolReview, role=role, task_class="light",
                                    effort="medium", max_tokens=6000)
            break
        except (LLMError, OSError) as e:
            log.warning("Photo review for %s on the %s model failed (%s).", topic["id"], role, e)
    if review is None:
        log.warning("Photo review for %s could not run; pool stays unapproved.", topic["id"])
        return {"topic": topic["id"], "status": "review unavailable", "kept": 0}
    by_index = {v.index: v for v in review.verdicts}
    kept, dropped = [], []
    for i, img in enumerate(images, 1):
        v = by_index.get(i)
        ok = bool(v and v.shows_topic in ("yes", "partly") and v.usable and v.safe and not v.duplicate_of)
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
