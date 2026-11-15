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
                  (QF-65b, fail closed); a scene whose file or record is gone is verified against the run's
                  provenance.json, bound by sha256 (QF-65d)
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
    AI media, media that cannot be traced, or no readable video record keep it off. A scene whose own file or
    record is gone (an older run's parallax/ clips) is verified against the run's provenance.json instead
    (_ledger_scene, QF-65d), never by a lookup elsewhere. [] when none is held."""
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
            reasons.append(f"a still with no record ({name}) and no video record to judge it by"
                           + _ledger_ai_note(run))
            continue
        for manifest in manifests:
            master = manifest.with_name(manifest.name[:-len(".composition.json")] + ".mp4")
            label = assess_video(master, payload if isinstance(payload, dict) else None, setting=False)
            if label["unknown"] and not label["ai_media"]:
                label = _ledger_video(run, manifest, label)
            if label["ai_media"] or label["unknown"]:
                reasons.append(f"a still with no record ({name}) from a video "
                               + ("showing AI media" if label["ai_media"] else "not wholly traceable")
                               + f": {label['reasons'][0]}")
                break
    return reasons


LEDGER_NOTE = "provenance.json"
PHOTO_CLIP = ("parallax", "scene_")     # src/parallax.py: a camera move over one still, never a generated picture
OWN_GRAPHIC = ("graphics", "graphic_")  # src/graphics.py: our own drawing (a map, a timeline, a number card)
MOTION_DIR, MOTION_PREFIX = "motion", "motion_"   # src/motion.py: AI motion clips and their anchor frames


def _parts(path: Any) -> list[str]:
    """A recorded path's parts, whichever machine's separators it was written with."""
    return [p for p in str(path or "").replace(chr(92), "/").split("/") if p]


def _key(path: Any) -> str:
    return "/".join(_parts(path)).lower()


def _ledger_video(run: Path, manifest: Path, label: dict[str, Any]) -> dict[str, Any]:
    """`label` (assess_video's, with media that cannot be traced and no AI media) after its untraced scenes are
    verified against the run's provenance ledger (QF-65d). The ledger records each composited file with its sha256,
    the pictures it was made from with theirs, and the photo's licence record; the composition manifest's own
    checksum, recorded there, binds the scenes' checksums to it. A scene the ledger verifies counts as real; one it
    shows to be AI makes the video AI; any other stays untraceable, with the reason the ledger gives."""
    try:
        scenes = json.loads(manifest.read_text(encoding="utf-8")).get("scenes")
    except (OSError, ValueError, AttributeError):
        scenes = None
    if not isinstance(scenes, dict) or not scenes:   # a list records no checksum: nothing binds it to the ledger
        return label
    ledger = _ledger(run, manifest)
    ai: list[str] = []
    unknown: list[str] = []
    for scene, recorded in scenes.items():
        verdict, why = trace(scene)
        if verdict == "unknown":
            found, because = _ledger_scene(run, scene, str(recorded or ""), ledger)
            if found == "ai":
                verdict, why = found, because
            elif found == "unknown":
                why = f"{why}, and {LEDGER_NOTE} cannot verify it: {because}"
            else:
                verdict = found
        _collect([(verdict, why)], ai, unknown)
    return _result(ai, unknown, False)


def _ledger_ai_note(run: Path) -> str:
    """For a run with no video record: the AI media its provenance.json lists, said to the operator (the hold does
    not depend on it; with no manifest nothing binds the ledger to the video). "" when it lists none or is gone."""
    try:
        assets = json.loads((run / LEDGER_NOTE).read_text(encoding="utf-8")).get("assets")
    except (OSError, ValueError, AttributeError):
        return ""
    named = []
    for asset in assets if isinstance(assets, list) else []:
        parts = [p.lower() for p in _parts(asset.get("path") if isinstance(asset, dict) else "")]
        if parts and (parts[-1].startswith(MOTION_PREFIX) or parts[-2:-1] == [MOTION_DIR]):
            named.append(f"AI motion clip ({_parts(asset['path'])[-1]})")
        elif parts and (Path(parts[-1]).stem.startswith(ILLUSTRATION_PREFIXES)
                        or asset.get("origin") == "archaeological_reconstruction" or asset.get("illustration")):
            named.append(f"AI illustration ({_parts(asset['path'])[-1]})")
    return f"; its {LEDGER_NOTE} lists {', '.join(dict.fromkeys(named))}" if named else ""


def _ledger(run: Path, manifest: Path) -> dict[str, Any]:
    """The run's provenance ledger for this video: {"data", "root" (the app tree's path parts as the ledger
    recorded them), "problem" ("" or why it cannot verify the video: missing, unreadable, or recorded for other bytes
    of the composition manifest or the master), "hashes" (a cache)}."""
    from .credits import sha256_file
    out: dict[str, Any] = {"data": {}, "root": None, "problem": "", "hashes": {}}
    try:
        data = json.loads((run / LEDGER_NOTE).read_text(encoding="utf-8"))
    except OSError:
        return {**out, "problem": "the run has none"}
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {**out, "problem": "it cannot be read"}
    recorded = data.get("composition_manifest") if isinstance(data.get("composition_manifest"), dict) else {}
    if not recorded.get("sha256"):
        return {**out, "problem": "it records no checksum for the video's manifest"}
    if recorded["sha256"] != sha256_file(manifest):
        return {**out, "problem": f"the video's manifest ({manifest.name}) is not the one it recorded (checksum mismatch)"}
    try:
        master_sum = json.loads(manifest.read_text(encoding="utf-8")).get("master_sha256")
    except (OSError, ValueError, AttributeError):
        master_sum = None
    master = data.get("master_video") if isinstance(data.get("master_video"), dict) else {}
    if master_sum and master.get("sha256") and master_sum != master["sha256"]:
        return {**out, "problem": "it records another master video (checksum mismatch)"}
    parts = _parts(recorded.get("path"))
    names = [p.lower() for p in parts]
    root = next((parts[:i] for i in range(len(names) - 1) if names[i:i + 2] == ["output", run.name.lower()]), None)
    return {**out, "data": data, "root": root}


def _here(run: Path, ledger: dict[str, Any], recorded: Any) -> Path | None:
    """The file a ledger path names, on this machine: as recorded, else at the same place under this app tree (a
    ledger written on another machine, or before the folder moved, records that tree's root). None when neither is
    a file. Only a checksum then decides that it is the recorded file."""
    path = Path(str(recorded))
    if path.is_file():
        return path
    root, parts = ledger.get("root"), _parts(recorded)
    if not root or [p.lower() for p in parts[:len(root)]] != [p.lower() for p in root] or len(parts) <= len(root):
        return None
    local = run.parent.parent.joinpath(*parts[len(root):])
    return local if local.is_file() else None


def _ledger_scene(run: Path, scene: Any, recorded: str, ledger: dict[str, Any]) -> tuple[str, str]:
    """("ai" | "real" | "unknown", why) for one scene of the video whose file or record is gone, from the ledger
    (QF-65d). AI when the scene is an AI motion clip (src/motion.py writes them to motion/ as motion_NN) or the
    ledger records an AI illustration in it. Real only when every link is bound by sha256 and nothing in it is AI:
    - the ledger's entry for the scene has the checksum the manifest records (and the file, if still here, has it);
    - the scene is one of this run's parallax clips (a camera move over one still) or our own graphics;
    - every picture it was made from has a recorded checksum, none is an AI illustration, each one still here has
      the recorded bytes and is not AI by its own record, and at least one is here to check (the anchor);
    - its licence record names a free licence and the photo's source, and the author and title when it asks for
      credit."""
    from .credits import license_allowed, needs_attribution, sha256_file
    hashes = ledger["hashes"]

    def digest(path: Path) -> str:
        if str(path) not in hashes:
            hashes[str(path)] = sha256_file(path)
        return hashes[str(path)]

    def motion(path: Any) -> bool:
        parts = [p.lower() for p in _parts(path)]
        return bool(parts) and (parts[-1].startswith(MOTION_PREFIX) or parts[-2:-1] == [MOTION_DIR])

    parts = [p.lower() for p in _parts(scene)]
    name = _parts(scene)[-1] if parts else str(scene)
    if motion(scene):   # AI whatever else is recorded: its own record, which says so, is the file that is gone
        return "ai", f"AI motion clip ({name}, in {LEDGER_NOTE})"
    if ledger["problem"]:
        return "unknown", ledger["problem"]
    if not recorded:
        return "unknown", f"the manifest records no checksum for {name}"
    entry = next((a for a in ledger["data"].get("assets") or []
                  if isinstance(a, dict) and _key(a.get("path")) == _key(scene)), None)
    if entry is None:
        return "unknown", f"no record of {name}"
    sources = [str(s) for s in entry.get("source_paths") or [] if _parts(s)]
    pictures = [s for s in sources if Path(_parts(s)[-1]).suffix.lower() in IMAGE_SUFFIXES]
    if any(motion(s) for s in sources):
        return "ai", f"AI motion clip ({name}, made from {_parts(next(s for s in sources if motion(s)))[-1]})"
    drawn = [s for s in pictures if Path(_parts(s)[-1]).stem.startswith(ILLUSTRATION_PREFIXES)]
    if drawn or entry.get("origin") == "archaeological_reconstruction" or entry.get("illustration") \
            or Path(name).stem.startswith(ILLUSTRATION_PREFIXES):
        return "ai", f"AI illustration ({_parts(drawn[0])[-1] if drawn else name}, in {LEDGER_NOTE})"
    if str(entry.get("sha256") or "") != recorded:
        return "unknown", f"it records other bytes for {name} (checksum mismatch)"
    present = _here(run, ledger, scene)
    if present is not None and digest(present) != recorded:
        return "unknown", f"{name} here is not the recorded file (checksum mismatch)"
    in_run = len(parts) > 2 and parts[-3] == run.name.lower()
    photo_clip = in_run and parts[-2] == PHOTO_CLIP[0] and parts[-1].startswith(PHOTO_CLIP[1])
    graphic = in_run and parts[-2] == OWN_GRAPHIC[0] and parts[-1].startswith(OWN_GRAPHIC[1])
    if not (photo_clip or graphic):
        return "unknown", f"it does not show what made {name}"
    if photo_clip and not pictures:
        return "unknown", f"it records no photo for {name}"
    sums = {_key(d.get("path")): str(d.get("sha256") or "")
            for d in ledger["data"].get("derived_sources") or [] if isinstance(d, dict)}
    anchored = False
    for source in pictures:
        source_name = _parts(source)[-1]
        recorded_sum = sums.get(_key(source)) or ""
        if not recorded_sum:
            return "unknown", f"it records no checksum for {source_name}"
        local = _here(run, ledger, source)
        if local is None:
            continue   # an intermediate copy (cache/) that is gone; its checksum stays recorded
        if digest(local) != recorded_sum:
            return "unknown", f"{source_name} here is not the recorded photo (checksum mismatch)"
        if trace(local, credits_needed=False)[0] == "ai":
            return "ai", f"AI media ({source_name}, by its own record)"
        anchored = True
    if pictures and not anchored:
        return "unknown", f"none of the pictures {name} was made from is here to check"
    if pictures:
        artist = str(entry.get("artist") or "").strip()
        credited = license_allowed(str(entry.get("license") or ""), allow_share_alike=True) and bool(entry.get("source_url"))
        if credited and needs_attribution(entry):
            credited = bool(entry.get("title")) and bool(artist) and artist.lower() != "unknown"
        if not credited:
            return "unknown", f"the licence record of {name}'s photo is incomplete"
    return "real", ""


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
