"""
src/publishable.py
==================
What one Approve publishes, as the exact strings each platform receives (QF-43).

The writer's copy (title, captions, first comment, hashtags, YouTube title and description) reached publication with
shape checks only, and part of it was never shown: the review card showed the caption before the illustration
disclosure and the carousel clean-up, and not the YouTube title, description, tags or the carousel alt texts.

  ENVELOPE_FIELDS     the allowlist: every metadata field a Reel envelope may carry, each mapped to the card lines
                      that show it (RECORDED_ONLY fields are kept for the record and posted by no publisher)
  envelope_metadata   the envelope's metadata, built from the allowlisted payload fields only, with the hashtags
                      restricted to the topic's candidate list (hashtag_candidates)
  card_copy           {label: exact final string}, computed with the publishers' own field builders
  card_record         what the review card showed (stored under "review-copy" when the card is sent)
  check_envelope      raises when an envelope field is outside the allowlist or differs from what the card showed

Topic-agnostic: the hashtag candidates come from the topic's own record (its hashtag list and its names).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Envelope metadata field -> the card labels that show it, word for word. A field added to the envelope without a
# card line is refused by check_envelope (the allowlist cannot drift from the card).
ENVELOPE_FIELDS: dict[str, tuple[str, ...]] = {
    "title": ("reel.title", "short.title"),
    "caption": ("reel.caption", "short.description"),
    "hashtags": ("hashtags", "short.tags"),
    "first_comment": ("reel.first_comment",),
    "youtube_title": ("youtube.title",),
    "description": ("youtube.description",),
    "tags": ("youtube.tags",),
}
# Kept in the envelope for the record; no publisher posts them as text (the AI label is a platform flag derived from
# the post's media and the setting, src/ai_label.py, never from the writer).
RECORDED_ONLY = ("location", "meta_topic_categories", "is_ai_generated")
# The payload fields a post's text is made from (the writer's, or an operator's edit of them).
PUBLISHABLE_FIELDS = ("title", "caption", "carousel_caption", "first_comment", "hashtags", "youtube_title",
                      "description", "tags")

REVIEW_COPY = "review-copy"          # telegram_state namespace: what each run's review card showed
HASHTAGS_MIN, HASHTAGS_MAX = 3, 5    # Instagram allows at most 5 per post
NAME_TAG_MAX = 25                    # characters of a hashtag made from one of the topic's names, "#" included

_TAG = re.compile(r"(?<![\w&/#])#([^\W\d_]\w*)")
_HASHTAG_ONLY_LINE = re.compile(r"^(?:\s*#\w+)+\s*$")
_TRAILING_HASHTAGS = re.compile(r"(?:\s+#\w+)+\s*$")


def _key(tag: str) -> str:
    return str(tag or "").strip().lstrip("#").casefold()


def _camel(name: Any) -> str:
    """'Derinkuyu Underground City' -> '#DerinkuyuUndergroundCity' ("" for nothing usable)."""
    words = re.findall(r"[^\W_]+", str(name or ""))
    if words and words[0].casefold() == "the":
        words = words[1:]
    tag = "".join(w[:1].upper() + w[1:] for w in words)
    return f"#{tag}" if tag and not tag[0].isdigit() else ""


def hashtag_candidates(topic: dict[str, Any] | None) -> list[str]:
    """The hashtags a post on `topic` may carry: the topic record's own list (generic engagement tags removed), then
    one tag for each of its names (its title, place, city, region, country, sub-theme). [] without a record."""
    if not topic:
        return []
    from .script_engine import GENERIC_HASHTAGS
    loc = topic.get("location") or {}
    names: list[Any] = []
    title = str(topic.get("title") or "").split(":")[0]   # "Göbekli Tepe: The 11,000-Year-Old Sanctuary" -> its name
    for value in (title, loc.get("name"), loc.get("city"), loc.get("region"), loc.get("country"),
                  topic.get("sub_theme")):
        names += re.split(r"[,/;]", value) if isinstance(value, str) else []
    out: list[str] = []
    seen = set()
    named = [t for t in (_camel(n) for n in names) if len(t) <= NAME_TAG_MAX]   # a long title makes no hashtag
    for tag in [str(h).strip() for h in topic.get("hashtags") or []] + named:
        if not _TAG.fullmatch(tag) or tag.lower() in GENERIC_HASHTAGS or _key(tag) in seen:
            continue
        seen.add(_key(tag))
        out.append(tag)
    return out


def publishable_hashtags(written: Iterable[Any], candidates: list[str]) -> tuple[list[str], list[str]]:
    """(the hashtags posted, the written ones left out). The writer's tags that are on the candidate list, in its
    order (spelled as the list spells them), topped up from the list to HASHTAGS_MIN, at most HASHTAGS_MAX."""
    allowed = {_key(c): c for c in candidates}
    kept: list[str] = []
    dropped: list[str] = []
    for tag in [str(t).strip() for t in written or []]:
        if _key(tag) in allowed and allowed[_key(tag)] not in kept:
            kept.append(allowed[_key(tag)])
        elif tag and _key(tag) not in allowed:
            dropped.append(tag)
    for tag in candidates:
        if len(kept) >= HASHTAGS_MIN:
            break
        if tag not in kept:
            kept.append(tag)
    return kept[:HASHTAGS_MAX], dropped


def retag(text: str, final: list[str]) -> str:
    """`text` carrying no hashtag outside `final`: its hashtag line (a line of tags, or a run of them closing a
    line) becomes `final`; any other hashtag loses its "#" and stays as a word, so no sentence is cut. Credit
    lines go as they are (QF-42 sanitises them at ingest)."""
    if not text:
        return text or ""
    from .credits import is_attribution_line
    keep = {_key(t) for t in final}
    out: list[str] = []
    placed = False
    for line in text.splitlines():
        if is_attribution_line(line):
            out.append(line)
            continue
        if _HASHTAG_ONLY_LINE.match(line):
            if not placed and final:
                out.append(" ".join(final))
                placed = True
            continue
        trailing = _TRAILING_HASHTAGS.search(line)
        if trailing and trailing.start() > 0:
            out.append(_TAG.sub(lambda m: m.group(0) if _key(m.group(1)) in keep else m.group(1),
                                line[:trailing.start()]).rstrip())
            if not placed and final:
                out.append(" ".join(final))
                placed = True
            continue
        out.append(_TAG.sub(lambda m: m.group(0) if _key(m.group(1)) in keep else m.group(1), line))
    return "\n".join(out)


def topic_for(db: Any, topic_id: str) -> dict[str, Any] | None:
    getter = getattr(db, "get_topic_by_id", None)
    if not callable(getter) or not topic_id:
        return None
    try:
        return getter(topic_id)
    except (sqlite3.Error, ValueError, TypeError):  # an unreadable record gives no candidates: no writer hashtag is posted
        return None


def envelope_metadata(payload: dict[str, Any], topic: dict[str, Any] | None,
                      ai_label: dict[str, Any] | None = None) -> dict[str, Any]:
    """A Reel envelope's metadata, from the allowlisted payload fields only. `ai_label`: the post's AI label as
    derived from its media (ai_label.assess_video, QF-65a); without one, the setting alone (META_IS_AI_GENERATED)."""
    from .config import META_IS_AI_GENERATED
    from .publisher import with_disclosure
    final, _ = publishable_hashtags(payload.get("hashtags") or [], hashtag_candidates(topic))
    return {
        "title": retag(payload["title"], final),
        "caption": with_disclosure(retag(payload["caption"], final), payload),
        "location": payload.get("location", {}),
        "hashtags": final,
        "meta_topic_categories": payload.get("meta_topic_categories", []),
        "first_comment": retag(payload.get("first_comment", ""), final),
        "is_ai_generated": bool(ai_label.get("on")) if isinstance(ai_label, dict) else META_IS_AI_GENERATED,
        "youtube_title": retag(payload.get("youtube_title", ""), final),
        "description": (with_disclosure(retag(payload.get("description", ""), final), payload)
                        if payload.get("description") else ""),
        "tags": list(payload.get("tags", [])),
    }


def is_documentary(payload: dict[str, Any]) -> bool:
    """Whether the run posts as the 16:9 documentary (to YouTube) rather than as the Reel."""
    from .delivery_profiles import get_delivery_profile
    try:
        return get_delivery_profile(str(payload.get("delivery_profile") or "reel")).name == "deep_dive_16x9"
    except KeyError:
        return False


def card_copy(meta: dict[str, Any], youtube: bool = False) -> dict[str, str]:
    """{label: the exact string posted}, from the envelope metadata through the publishers' own field builders.
    `youtube`: the documentary's YouTube upload; otherwise the Reel (Facebook and Instagram) and its Short."""
    from .publisher import reel_fields, youtube_fields, youtube_short_fields
    out: dict[str, str] = {"hashtags": " ".join(meta.get("hashtags") or [])}
    if youtube:
        yt = youtube_fields(meta)
        out.update({"youtube.title": yt["title"], "youtube.description": yt["description"],
                    "youtube.tags": ", ".join(yt["tags"])})
        return out
    reel = reel_fields(meta)
    short = youtube_short_fields(meta)
    out.update({"reel.title": reel["title"], "reel.caption": reel["caption"],
                "reel.first_comment": reel["first_comment"],
                "short.title": short["title"], "short.description": short["description"],
                "short.tags": ", ".join(short["tags"])})
    return out


CAROUSEL_PLATFORMS = ("instagram", "facebook")   # each gets its own carousel request (publisher.publish_photos)


def carousel_base(payload: dict[str, Any], final: list[str]) -> str:
    """The carousel's own caption (the Reel caption for a script without one) carrying no hashtag outside `final`:
    the text the carousel post starts from (main.PipelineCoordinator._carousel_post) and the card shows."""
    return retag((payload.get("carousel_caption") or "").strip() or payload.get("caption", ""), final)


def carousel_captions(base: str) -> dict[str, str]:
    """{"carousel.<platform>": the caption that platform's carousel request carries} (before the photo credit lines
    the post adds from the slides' records)."""
    from .publisher import _caption_for
    return {f"carousel.{p}": _caption_for("carousel", base, [p]) for p in CAROUSEL_PLATFORMS} if base else {}


def carousel_copy(payload: dict[str, Any], final: list[str], run_dir: Path | None) -> dict[str, str]:
    """The carousel's caption per platform as its request carries it (before the photo credit lines the post adds
    from the slides' records), and each slide's alt text when the slides are on disk."""
    out: dict[str, str] = carousel_captions(carousel_base(payload, final))
    if run_dir is not None:
        from .multi_format import slide_alt_text
        for folder, platform in (("carousel", "instagram"), ("carousel_fb", "facebook")):
            for n, slide in enumerate(sorted((Path(run_dir) / folder).glob("carousel_slide_*.jpg"))[:10], 1):
                try:
                    alt = slide_alt_text(slide)
                except (OSError, ValueError, KeyError, TypeError):  # an unreadable record: no alt text is sent for it either
                    alt = ""
                if alt:
                    out[f"alt.{platform}.{n}"] = alt
    return out


POSTINGS = ("slot", "now", "dry_run")   # what Approve does: books a slot, posts at once, or simulates (posts nothing)


def schedule(db: Any, now: datetime | None = None, window_seconds: int | None = None,
             posting: str = "now", topic_id: str = "", run_id: str = "") -> dict[str, str | None]:
    """{"posting": what Approve does (POSTINGS; main.PipelineCoordinator._approve_posting), "slot": the Reel slot
    it books (topic_policy.next_safe_slot over the booked days, the slot main books; None unless Approve books one),
    "closes": when the review closes}, the times as ISO strings; plus "scheduling_hold" when Approve cannot post or
    book this sensitive topic yet (another sensitive publication too close or unconfirmed, QF-73)."""
    from . import config, post_schedule
    from .topic_policy import SensitiveSpacingError, next_safe_slot
    now = now or datetime.now(UTC)
    posting = posting if posting in POSTINGS else "now"
    slot, hold = None, ""
    if posting == "slot" and config.POST_SCHEDULING:
        try:
            taken = {datetime.fromisoformat(p["due_at"]).astimezone(post_schedule.tz()).date()
                     for p in db.list_scheduled_posts(("pending",)) if p.get("kind") == "reel" and p.get("due_at")}
            slot = (next_safe_slot(db, topic_id, now, taken, run_id=run_id) if hasattr(db, "topic_problem")
                    else post_schedule.next_reel_slot(now, taken)).isoformat()
        except SensitiveSpacingError as exc:   # no day is safe yet: the card says so, never "posted at once"
            hold = str(exc)
        except (KeyError, ValueError, TypeError, AttributeError):  # a schedule misconfiguration never costs the card
            slot = None
    elif posting == "now" and topic_id and hasattr(db, "topic_problem"):
        hold = db.topic_problem(topic_id, now, run_id=run_id, spacing_only=True)
    seconds = window_seconds if window_seconds is not None else config.APPROVAL_TIMEOUT_SECONDS
    return {"posting": "slot" if slot or (hold and posting == "slot") else ("now" if posting == "slot" else posting),
            "slot": slot, "closes": (now + timedelta(seconds=int(seconds))).isoformat(),
            **({"scheduling_hold": hold} if hold else {})}


def photo_posts(run_dir: Path | None) -> list[Path]:
    """The Story frames and carousel slides a run would post (Stories: multi_format.story_posting_frames; a carousel:
    at most 10 slides, Instagram's copy and Facebook's), for the card's AI-label line."""
    if run_dir is None or not Path(run_dir).is_dir():
        return []
    from .multi_format import story_posting_frames
    stories = [Path(p) for _, p in story_posting_frames(sorted(str(p) for p in (Path(run_dir) / "stories").glob("story_*.jpg")))]
    slides = [p for folder in ("carousel", "carousel_fb")
              for p in sorted((Path(run_dir) / folder).glob("carousel_slide_*.jpg"))[:10]]
    return stories + slides


def card_record(payload: dict[str, Any], db: Any, run_dir: Path | None = None,
                window_seconds: int | None = None, now: datetime | None = None, topic_id: str = "",
                posting: str = "now", master: Path | None = None, cover: Path | None = None,
                run_id: str = "") -> dict[str, Any]:
    """Everything the review card shows of what Approve publishes. `topic_id`: the run's, for a payload without
    one (a documentary's: publish_run fills it in the same way before build_envelope). `posting`: what Approve
    does (POSTINGS); a documentary is never booked a slot (it goes to YouTube at once). `master` and `cover`: the
    video and cover Approve posts, whose media decide the AI label (QF-65a; without a master the label is on)."""
    from . import ai_label, config
    topic = topic_for(db, payload.get("topic_id") or topic_id)
    candidates = hashtag_candidates(topic)
    final, dropped = publishable_hashtags(payload.get("hashtags") or [], candidates)
    label = ai_label.assess_video(master, payload, cover=cover)
    meta = envelope_metadata(payload, topic, ai_label=label)
    documentary = is_documentary(payload)
    photos = [] if documentary else photo_posts(run_dir)
    copy = card_copy(meta, youtube=documentary)
    slides = run_dir is not None and any((Path(run_dir) / "carousel").glob("carousel_slide_*.jpg"))
    from .carousel_lazy import planned
    at_slot = run_dir is not None and not slides and planned(run_dir)   # drawn when it takes its slot (QF-81)
    if not documentary and (slides or at_slot or payload.get("carousel_caption")):  # a run with a carousel to post
        copy.update(carousel_copy(payload, final, run_dir))
    return {"copy": copy, "hashtags": final, "dropped": dropped, "candidates": candidates,
            "documentary": documentary, "shorts": bool(config.YOUTUBE_SHORTS),
            **({"carousel_at_slot": True} if at_slot and not documentary else {}),
            "carousel_own": bool((payload.get("carousel_caption") or "").strip()),
            "ai_label": label, "ai_label_photos": ai_label.assess_photos(photos) if photos else None,
            **schedule(db, now=now, window_seconds=window_seconds,
                       posting="now" if documentary and posting == "slot" else posting,
                       topic_id=(topic or {}).get("id") or payload.get("topic_id") or topic_id,
                       run_id=run_id or payload.get("run_id") or "")}


def save_record(db: Any, run_id: str, record: dict[str, Any]) -> None:
    from .telegram_state import put
    put(db, REVIEW_COPY, run_id, record)


def load_record(db: Any, run_id: str) -> dict[str, Any] | None:
    """The card's record, or None when no card was sent for the run (the command line, no Telegram)."""
    if not hasattr(db, "_conn"):
        return None
    from .telegram_state import get
    try:
        return get(db, REVIEW_COPY, run_id)
    except sqlite3.OperationalError as e:  # no transport table: this database never sent a card
        if "no such table" in str(e):
            return None
        raise


def carousel_post_base(payload: dict[str, Any], db: Any, run_id: str, topic_id: str = "") -> tuple[str, list[str]]:
    """(the carousel's caption base, the hashtags it may carry), as the run's review card showed them. Raises
    ValueError when a review card was sent and showed another carousel caption (or none): the carousel is posted
    by its own path, days after Approve, so it is held to the card as the Reel's envelope is (check_envelope)."""
    topic = topic_for(db, payload.get("topic_id") or topic_id)
    final, _ = publishable_hashtags(payload.get("hashtags") or [], hashtag_candidates(topic))
    base = carousel_base(payload, final)
    record = load_record(db, run_id)
    if record is not None:
        shown = record.get("copy") or {}
        for label, text in carousel_captions(base).items():
            if shown.get(label) != text:
                raise ValueError(f"Not posted: the {label} to be posted is not what the review card showed.")
    return base, final


def check_envelope(metadata: dict[str, Any], record: dict[str, Any] | None, youtube: bool) -> None:
    """Raises ValueError when the envelope carries a field outside the allowlist, or (when a review card was sent)
    a string the card did not show word for word."""
    outside = sorted(set(metadata) - set(ENVELOPE_FIELDS) - set(RECORDED_ONLY))
    if outside:
        raise ValueError(f"Not posted: {', '.join(outside)} is not a publishable field (not on the review card).")
    if record is None:
        return
    shown = record.get("copy") or {}
    for label, text in card_copy(metadata, youtube=youtube).items():
        if youtube and label == "hashtags":   # the documentary's upload carries no hashtags (publisher.youtube_fields)
            continue
        if shown.get(label) != text:
            raise ValueError(f"Not posted: the {label} to be posted is not what the review card showed; "
                             "resend the card and review it again.")
