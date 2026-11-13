"""
src/ai_label.py
===============
Whether a post carries the platforms' AI label, derived from the media it actually publishes (QF-65a).

The label was a setting (config.META_IS_AI_GENERATED, set for the synthetic narration) that no picture could change:
an AI illustration or an AI motion clip on screen did not force it, and the Stories and carousels sent none at all.
Now every post's flag is derived from the records of the files it shows, and the setting can only add to it:

  trace           one file: "ai" (an image_gen illustration, a motion clip, anything a record calls AI-generated),
                  "real" (a credited photo, real footage, our own graphic, or a copy derived from one), or
                  "unknown" (missing file, unreadable record, a clip or picture with no record of where it came from)
  assess_video    a Reel, Short or documentary: the master's scenes (its composition manifest), the picture check's
                  illustrated beats and the cover; on = the setting, or any AI or unknown media (fails closed)
  assess_photos   a Story frame or carousel slides: the photos each still's record says it shows; facebook_hold
                  when they show AI media, or when a still has no record and its run's video is not wholly real
                  (QF-65b, fail closed)
  card_line       the review card's line: whether the label is on, and why

What each platform takes (cited in docs/reviews/quality-fixes-2026-11/QF-65a/README.md): Instagram's is_ai_generated
(Reels, photos, carousels), Facebook's facebook_is_ai_generated (Reels only), YouTube's containsSyntheticMedia;
Facebook photo posts and Stories have no AI-label field, so a Facebook photo post showing AI media is not sent
(MetaPublisher.publish_photos). Topic-agnostic: only file records are read.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
MEDIA_SUFFIXES = IMAGE_SUFFIXES + VIDEO_SUFFIXES
SETTING_REASON = "synthetic narration (setting)"
ILLUSTRATION_PREFIXES = ("gap_gen_", "gen_")   # as video_composer and video_qa name image_gen files
REASONS_SHOWN = 3


def _credited(photo: Path) -> bool:
    """Whether the photo's folder credits it (credits.json, a list or {"images": [...]}): a sourced photo's record."""
    try:
        data = json.loads((photo.parent / "credits.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    images = data if isinstance(data, list) else (data.get("images") if isinstance(data, dict) else None)
    return any(isinstance(e, dict) and e.get("file") == photo.name for e in images or [])


def trace(path: Any, credits_needed: bool = True, _seen: frozenset = frozenset()) -> tuple[str, str]:
    """("ai" | "real" | "unknown", why) for one published or composited file, through its lineage records
    (src/lineage.py) down to the sources. `credits_needed`: a picture without a lineage record must be a credited
    photo (False under our own graphic, whose inputs are declared in its own record)."""
    from .lineage import sidecar
    try:
        path = Path(str(path))
    except TypeError:
        return "unknown", "a media path that cannot be read"
    name = path.name
    if path.stem.startswith(ILLUSTRATION_PREFIXES):   # the image_gen tier's files (video_composer, video_qa)
        return "ai", f"AI illustration ({name})"
    if path in _seen:
        return "unknown", f"a cyclic media record ({name})"
    if not path.is_file():
        return "unknown", f"{name} is missing"
    record_path = sidecar(path)
    if record_path.is_file():
        try:
            rec = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "unknown", f"the record of {name} cannot be read"
        if not isinstance(rec, dict):
            return "unknown", f"the record of {name} cannot be read"
        settings = rec.get("settings") if isinstance(rec.get("settings"), dict) else {}
        inputs = settings.get("inputs") if isinstance(settings.get("inputs"), dict) else {}
        if rec.get("operation") == "ai_motion" or settings.get("ai_generated") is True:
            provider = str(settings.get("provider") or "").strip()
            return "ai", f"AI motion clip ({name}{', ' + provider if provider else ''})"
        if inputs.get("origin") == "ai_illustration":
            return "ai", f"AI illustration ({name})"
        sources = rec.get("sources")
        if not isinstance(sources, list) or not sources:
            return "unknown", f"the record of {name} names no source"
        graphic = rec.get("operation") == "procedural_graphic"
        unknown = ""
        for source in sources:
            src = Path(str((source or {}).get("path") or "")) if isinstance(source, dict) else None
            if src is None or not str(src) or src.suffix.lower() not in MEDIA_SUFFIXES:
                continue   # a graphic's generator code, map data, a font: not a picture
            verdict, why = trace(src, credits_needed=credits_needed and not graphic, _seen=_seen | {path})
            if verdict == "ai":
                return verdict, why
            if verdict == "unknown" and not unknown:
                unknown = why
        return ("unknown", unknown) if unknown else ("real", "")
    if path.suffix.lower() in VIDEO_SUFFIXES:
        if path.stem.startswith("footage_") and path.with_suffix(".json").is_file():
            return "real", ""   # archive film with its own record (src/footage.py)
        return "unknown", f"a clip with no record of where it came from ({name})"
    if credits_needed and path.suffix.lower() in IMAGE_SUFFIXES and not _credited(path):
        return "unknown", f"a picture with no credit or lineage record ({name})"
    return "real", ""


def _result(ai: list[str], unknown: list[str], setting: bool) -> dict[str, Any]:
    ai, unknown = list(dict.fromkeys(ai)), list(dict.fromkeys(unknown))
    reasons = ai + ([SETTING_REASON] if setting else []) + unknown   # the decisive reasons first
    return {"on": bool(setting or ai or unknown), "ai_media": bool(ai), "unknown": bool(unknown),
            "setting": bool(setting), "reasons": reasons, "ai_reasons": ai, "unknown_reasons": unknown}


def _collect(verdicts: Iterable[tuple[str, str]], ai: list[str], unknown: list[str]) -> None:
    for verdict, why in verdicts:
        if verdict == "ai":
            ai.append(why)
        elif verdict == "unknown":
            unknown.append(why)


def still_sources(still: Any) -> list[Path] | None:
    """The pictures a drawn still shows, from its record (still_layout.json beside it): a Story frame's or carousel
    slide's photo (and a comparison's second one), a cover's hero. None when the still has no record."""
    from . import still_layout
    try:
        entry = still_layout.load(Path(str(still)))
    except (OSError, TypeError, ValueError):
        entry = None
    recipe = (entry or {}).get("recipe") if isinstance(entry, dict) else None
    if not isinstance(recipe, dict):
        return None
    found = [recipe.get(k) for k in ("photo", "hero")]
    if recipe.get("archetype") == "comparison":
        found.append(recipe.get("photo_next"))
    paths = [Path(str(p)) for p in found if p]
    return paths if paths or recipe.get("archetype") == "diagram" else None


def _still_verdicts(still: Any, what: str) -> list[tuple[str, str]]:
    sources = still_sources(still)
    if sources is None:
        name = Path(str(still)).name if still else what
        return [("unknown", f"the {what}'s record is missing ({name})")]
    return [trace(p) for p in sources]


def assess_video(master: Any, payload: dict[str, Any] | None, cover: Any = None,
                 setting: bool | None = None) -> dict[str, Any]:
    """The AI label of a Reel, its Short or a documentary: {"on", "ai_media", "unknown", "setting", "reasons"}.
    On when the setting is, when the picture check illustrated a beat, when any scene of the master (its
    composition manifest) or the cover traces to AI media, and when any of them cannot be traced (fails closed)."""
    from . import config
    setting = bool(config.META_IS_AI_GENERATED if setting is None else setting)
    ai: list[str] = []
    unknown: list[str] = []
    fit = (payload or {}).get("picture_fit") if isinstance(payload, dict) else None
    for g in (fit.get("generated") if isinstance(fit, dict) else None) or []:
        ai.append(f"AI illustration (beat {g.get('beat', '?')})" if isinstance(g, dict) else "AI illustration")
    scenes: Any = None
    if master:
        manifest = Path(str(master)).with_suffix(".composition.json")
        try:
            scenes = json.loads(manifest.read_text(encoding="utf-8")).get("scenes")
        except (OSError, ValueError, AttributeError):
            scenes = None
    if not isinstance(scenes, (dict, list)) or not scenes:
        unknown.append("no readable media record for the video")
    else:
        _collect((trace(p) for p in scenes), ai, unknown)
    if cover:
        _collect(_still_verdicts(cover, "cover"), ai, unknown)
    return _result(ai, unknown, setting)


def assess_photos(photos: Iterable[Any]) -> dict[str, Any]:
    """The AI label of a Story frame or a carousel: on when any still shows AI media or cannot be traced. The
    narration setting does not apply (a photo post has no narration)."""
    ai: list[str] = []
    unknown: list[str] = []
    photos = list(photos or [])
    for still in photos:
        _collect(_still_verdicts(still, "slide"), ai, unknown)
    held = _record_less_hold([s for s in photos if s and still_sources(s) is None])
    return {**_result(ai, unknown, False), "facebook_hold": bool(ai or held),
            "facebook_reasons": list(dict.fromkeys(ai)) + held}


def _record_less_hold(stills: list[Any]) -> list[str]:
    """Why stills with no record (a run made before still records) are kept off Facebook, which has no AI label for
    photo posts (QF-65b; operator decision 2026-11-12: fail closed). Such a still is judged by its run's video (the
    run folder holding its stories/ or carousel/ folder): the master's scenes, from its composition manifest, and
    the picture check's illustrated beats. It goes to Facebook only when every one of them traces to real media;
    AI media, media that cannot be traced, or no readable video record keep it off. [] when none is held."""
    reasons: list[str] = []
    by_run: dict[Path, str] = {}
    for still in stills:
        by_run.setdefault(Path(str(still)).parent.parent, Path(str(still)).name)
    for run, name in by_run.items():
        try:
            payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        manifests = sorted(run.glob("*.composition.json")) if run.is_dir() else []
        if not manifests:
            reasons.append(f"a still with no record ({name}) and no video record to judge it by")
            continue
        for manifest in manifests:
            master = manifest.with_name(manifest.name[:-len(".composition.json")] + ".mp4")
            label = assess_video(master, payload if isinstance(payload, dict) else None, setting=False)
            if label["ai_media"] or label["unknown"]:
                reasons.append(f"a still with no record ({name}) from a video "
                               + ("showing AI media" if label["ai_media"] else "not wholly traceable")
                               + f": {label['reasons'][0]}")
                break
    return reasons


def _why(label: dict[str, Any]) -> str:
    """The reasons in a few words for the card: the AI items by name (at most REASONS_SHOWN),
    then the setting, then the untraceable ones counted with the first named. The record keeps every reason."""
    ai = list(label.get("ai_reasons") or [])
    unknown = list(label.get("unknown_reasons") or [])
    if not ai and not unknown and not label.get("setting"):   # an older record: its reasons as they are
        ai = list(label.get("reasons") or [])
    parts = ai[:REASONS_SHOWN] + ([f"and {len(ai) - REASONS_SHOWN} more AI item(s)"] if len(ai) > REASONS_SHOWN else [])
    if label.get("setting"):   # the decisive reasons first: AI media, then the setting, then what cannot be traced
        parts.append(SETTING_REASON)
    if unknown:
        parts.append(f"{len(unknown)} items not traceable ({unknown[0]})" if len(unknown) > 1 else unknown[0])
    return "; ".join(parts)


BRIEF_REASON_MAX = 60


def card_line(label: dict[str, Any] | None, documentary: bool = False,
              photos: dict[str, Any] | None = None, brief: bool = False, stills: str = "Stories/carousel") -> str:
    """The review card's AI-transparency text (plain; the card escapes it), or "" without an assessment. `brief`: the
    form a full card has room for (whether the label is on, and its first reason); the reply under the card then
    carries the whole line (telegram_bot._with_publish_lines). `stills`: what `photos` assessed ("Stories" when the
    carousel is drawn at its slot, QF-81, and labelled from its slides there; never longer than the default)."""
    if not isinstance(label, dict):
        return ""
    if brief:
        if not label.get("on"):
            text = "no AI label (no AI media; setting off)"
        else:
            # The decisive reason first: AI media, then the setting, then what cannot be traced (review of
            # QF-65a round 1: 28 of 35 real cards named a missing cover record although the setting decided)
            first = (list(label.get("ai_reasons") or []) + ([SETTING_REASON] if label.get("setting") else [])
                     + list(label.get("unknown_reasons") or []) + list(label.get("reasons") or []))[:1]
            reason = first[0] if first else "AI media"
            if len(reason) > BRIEF_REASON_MAX:
                reason = reason[:BRIEF_REASON_MAX - 1].rstrip() + "…"
            more = len(label.get("reasons") or []) > 1
            text = f"AI label on: {reason}" + ("; more in the reply" if more else "")
        if isinstance(photos, dict) and (photos.get("ai_media") or photos.get("facebook_hold")):
            text += f"; {stills} kept off Facebook"
        return text
    where = "YouTube" if documentary else "Instagram, Facebook, YouTube"
    if label.get("on"):
        text = f"AI label on ({where}): {_why(label)}"
    else:
        text = "no AI label: no AI picture or clip in this video, and the AI-content setting is off"
    if isinstance(photos, dict) and photos.get("ai_media"):
        text += (f". {stills}: AI label on Instagram; not posted to Facebook, which has no AI label for "
                 f"photos ({_why(dict(photos, unknown_reasons=[]))})")
    elif isinstance(photos, dict) and photos.get("facebook_hold"):   # stills with no record (QF-65b)
        text += (f". {stills}: AI label on Instagram; not posted to Facebook, which has no AI label for "
                 f"photos ({(photos.get('facebook_reasons') or ['a still that cannot be traced'])[0]})")
    elif isinstance(photos, dict) and photos.get("on"):
        text += f". {stills}: AI label on Instagram (a still cannot be traced)"
    return text
