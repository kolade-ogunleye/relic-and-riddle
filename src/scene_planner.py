"""
src/scene_planner.py
====================
Matches pictures to what is being said.

1. segment_beats(): splits the timed narration into beats of roughly 2.5-4.5 seconds,
   cutting at sentence or clause ends (so cuts land on natural pauses).
2. plan_scenes(): Claude looks at small versions of the candidate images and picks the
   best image for each beat. Without Claude, images keep their filename order.
The result is one (image, duration) per beat for the video composer.
"""

import json
import re
from collections import Counter
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import commons, config, free_llm, graphic_match, prompt_fence
from .config import FOOTAGE_MAX_SHARE
from .llm import LLMError, claude_available, image_block, structured_call, task as llm_task
from .models import ScenePlan
from .operator_jobs import JobCancelled
from .typography import is_video

log = logging.getLogger(__name__)

MIN_BEAT, TARGET_BEAT, MAX_BEAT = 2.5, 3.2, 4.5

SYSTEM = """You are a documentary film editor. Choose the best picture for each line of narration in a short vertical history video. Prefer images that literally show what the line describes; for abstract lines, pick an atmospheric image that fits. Use each image at most once where possible, start strong (the first beat is the hook: open on the widest shot that shows the whole subject's scale at once, an aerial, top-down, wide or establishing view, never a detail or a close-up), and avoid placing two near-identical images back to back. Never pick an image that contradicts the narration; real footage labelled GENERIC must never stand in for the named place. A photo's label carries its archive caption (title, description, date): use it to know what and where the photo shows, and never place a photo whose caption names a different site, object or era where the narration names this one.
Some candidates are the channel's own animated graphics (a locator map, a timeline, a scale comparison). Use each at most once, on the beat it fits: the map where the place is introduced, the timeline where the dates are told, the scale graphic where the size is stated. They add information the photos can't, so use the ones that fit; don't open the video with one unless no photo makes a stronger hook."""


def segment_beats(words: List[Dict[str, Any]], total: float) -> List[Dict[str, Any]]:
    """Groups timed words into beats; each beat runs until the next beat starts."""
    beats: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    for i, w in enumerate(words):
        current.append(w)
        start = current[0]["start"]
        end = w["start"] + w["dur"]
        length = end - start
        pause = w["word"].rstrip()[-1:] in ".!?;:,—–"
        last = i == len(words) - 1
        if last or (length >= MIN_BEAT and pause and length >= TARGET_BEAT * 0.8) or length >= MAX_BEAT:
            beats.append({"start": start, "text": " ".join(x["word"] for x in current)})
            current = []
    if not beats:
        return [{"start": 0.0, "end": total, "text": ""}]
    beats[0]["start"] = 0.0
    for b, nxt in zip(beats, beats[1:] + [None]):
        b["end"] = nxt["start"] if nxt else total
    # Fold a very short final beat into the previous one.
    if len(beats) > 1 and beats[-1]["end"] - beats[-1]["start"] < 1.5:
        beats[-2]["end"], beats[-2]["text"] = beats[-1]["end"], beats[-2]["text"] + " " + beats[-1]["text"]
        beats.pop()
    return beats


MIN_CUT, MAX_CUT = 5.0, 12.0  # documentary cuts: long enough to read a picture, short enough to keep pace


def documentary_cuts(words: List[Dict[str, Any]], total: float) -> List[Dict[str, Any]]:
    """Cuts for the 16:9 documentary, one picture each: the narration's sentences, with short
    ones merged until a cut lasts MIN_CUT seconds (never past MAX_CUT). Cuts are contiguous and
    cover the whole narration, so their durations add up to it exactly."""
    sentences: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    for w in words:
        current.append(w)
        if w["word"].rstrip()[-1:] in ".!?" and len(current) > 0:
            sentences.append({"start": current[0]["start"], "end": w["start"] + w.get("dur", 0.0),
                              "text": " ".join(x["word"] for x in current)})
            current = []
    if current:
        sentences.append({"start": current[0]["start"], "end": current[-1]["start"] + current[-1].get("dur", 0.0),
                          "text": " ".join(x["word"] for x in current)})
    cuts: List[Dict[str, Any]] = []
    for s in sentences:
        last = cuts[-1] if cuts else None
        if last and last["end"] - last["start"] < MIN_CUT and s["end"] - last["start"] <= MAX_CUT:
            last["end"], last["text"] = s["end"], last["text"] + " " + s["text"]
        else:
            cuts.append(dict(s))
    if not cuts:
        return [{"start": 0.0, "end": total, "text": ""}]
    if len(cuts) > 1 and cuts[-1]["end"] - cuts[-1]["start"] < MIN_CUT / 2:  # a stub at the end joins the cut before
        stub = cuts.pop()
        cuts[-1]["text"] += " " + stub["text"]
    cuts[0]["start"] = 0.0
    for a, b in zip(cuts, cuts[1:]):
        a["end"] = b["start"]
    cuts[-1]["end"] = total
    return cuts


def _is_graphic(p: Path) -> bool:
    """A clip the planner may place: an original graphic (src/graphics.py) or a real footage
    segment (src/footage.py). Both have a poster frame beside them."""
    return is_video(p) and Path(p).stem.startswith(("graphic_", "footage_")) and Path(p).with_suffix(".jpg").exists()


def _photo_record(p: Path) -> Tuple[Optional[Dict[str, Any]], bool]:
    """(credits.json entry, whether any folder on the way had a credits.json) of a sourced photo. A derived copy
    (text-band crop, privacy blur, upscale) has no credits beside it: its lineage leads back to the original (the
    planner chose cropped photos blind, audit 2026-10-28)."""
    p = Path(p)
    entry, credited = None, False
    for _ in range(4):
        credited = credited or (p.parent / "credits.json").is_file()
        entry = next((e for e in _folder_credits(str(p.parent)) if e.get("file") == p.name), None)
        if entry:
            break
        try:
            from .lineage import sidecar
            sources = json.loads(sidecar(p).read_text(encoding="utf-8")).get("sources") or []
            p = Path(sources[0]["path"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            break
    return entry, credited


def _photo_entry(p: Path) -> Optional[Dict[str, Any]]:
    """The credits.json entry of a sourced photo, through its lineage (None if none)."""
    return _photo_record(p)[0]


def strong_shot(p: Path) -> bool:
    """A still that may open the video or be animated: a Topic or Kept photo by its record, or a photo you supplied
    (no credits.json beside it or its sources). Never a context shot, a photo found for one line, or one never looked
    at: Nazca on production (2026-10-28) opened on, and animated, a context photo of the highway through the desert."""
    from .image_presentation import full_image
    if is_video(Path(p)) or full_image(Path(p)):
        return False
    from .beat_images import intrusion_noted
    if intrusion_noted(p):  # a wing or frame the screen saw and could not crop away (dev run, 2026-10-28)
        return False
    from .image_review import tier
    entry, credited = _photo_record(p)
    if entry:
        return tier(entry) in ("topic", "kept")
    return not credited


def picture_id(p: Path) -> str:
    """What a picture is, whatever copy of it is placed: the file its lineage starts from (a text-band crop, an
    upscale or a privacy blur is the same picture as its original; coverage audit 2026-10-28). Clips are their own
    path."""
    p = Path(p)
    if not is_video(p):
        from .lineage import sidecar
        for _ in range(6):
            try:
                sources = json.loads(sidecar(p).read_text(encoding="utf-8")).get("sources") or []
                p = Path(sources[0]["path"])
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                break
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def _photo_caption(p: Path) -> str:
    """The archive caption of a sourced photo, from its credits entry ("" if none)."""
    entry = _photo_entry(p)
    if not entry:
        return ""
    return commons.caption(entry) + _CROWDED_NOTE * _crowded(entry)


_CROWDED_NOTE = "; prominent visitors, faces blurred: avoid for the hook"


def _crowded(entry: dict[str, Any]) -> bool:
    return bool((entry.get("vision_review") or {}).get("people_prominent"))


def _caption_label(p: Path, mark: str) -> str:
    """A photo's archive caption for the planner's prompt: a short JSON string inside the call's fence, with the
    pipeline's own crowd note outside it ("" if the photo has no record)."""
    entry = _photo_entry(p)
    cap = commons.caption(entry) if entry else ""
    if not cap:
        return _CROWDED_NOTE[2:] if entry and _crowded(entry) else ""
    label = f'<caption fence="{mark}">{prompt_fence.safe_caption(cap)}</caption fence="{mark}">'
    return label + _CROWDED_NOTE * _crowded(entry)


# The opening shot (Meta's Creator assistant on the operator's Reel, 2026-10-28: "move that top-down aerial shot
# to the 0:00 mark; show the scale immediately"; the Reel opened on a side shrine). The hook beat takes the
# topic's strongest scale shot, judged from what is already recorded about each photo: the vision verdict's
# reason (what the picture shows), weighted above the archive title and description; resolution breaks ties.
_SCALE_WORDS = re.compile(
    r"\b(?:aerial|drone|bird'?s[\s-]eye|top[\s-]down|overhead|from\s+above|from\s+the\s+air|satellite|panoram(?!io)\w*"
    r"|wide(?:[\s-]angle)?|broad\s+view|elevated|establishing|overview|general\s+view|full\s+view|whole|entire"
    r"|vast|skyline|landscape|scale)\b", re.I)
_DETAIL_WORDS = re.compile(
    r"\b(?:close[\s-]?up|details?|detailed|macro|reliefs?|frieze|inscriptions?|panel|carvings?|interior|inside"
    r"|niches?|fragments?|texture|small)\b", re.I)


def scale_score(entry: Optional[Dict[str, Any]]) -> Optional[float]:
    """How strongly a photo's record says it shows the subject's whole scale (higher is wider); None when it
    may not open the video (not a Topic or Kept photo by its verdict, or prominent visitors in it)."""
    from .image_review import tier
    if not entry or tier(entry) not in ("topic", "kept"):
        return None
    verdict = entry.get("vision_review") or {}
    if verdict.get("people_prominent"):
        return None
    seen = str(verdict.get("reason") or "")
    archive = " ".join(str(entry.get(k) or "") for k in ("title", "description"))
    score = 2 * (len(_SCALE_WORDS.findall(seen)) - len(_DETAIL_WORDS.findall(seen)))
    score += len(_SCALE_WORDS.findall(archive)) - len(_DETAIL_WORDS.findall(archive))
    try:
        pixels = float(entry.get("width") or 0) * float(entry.get("height") or 0)
    except (TypeError, ValueError):
        pixels = 0.0
    return score + min(pixels, 50e6) / 1e9   # at most +0.05: resolution only breaks ties


def hook_shot(stills: List[Path]) -> Optional[Path]:
    """The still with the strongest recorded scale evidence, or None when no photo's record says it shows
    the scale (the plan's own opening then stands). Ties keep pool order (best-first)."""
    best, best_score = None, 0.5   # at least one scale mention, net of detail ones
    for p in stills:
        score = scale_score(_photo_entry(p))
        if score is not None and score > best_score:
            best, best_score = p, score
    return best


def open_on_scale(chosen: List[Path], stills: List[Path]) -> List[Path]:
    """`chosen` with the hook beat on the topic's strongest scale shot (hook_shot), when the planned opening
    is a still whose record shows less scale. A shot already placed later trades places with the opening, so
    the set of pictures (and their credits) stays the same; footage and clips at the opening already move
    and are kept. Whatever the scale evidence, the opening is a strong shot (strong_shot): a context photo or one found for a line gives way to the first strong
    still in pool order (best-first), one already placed before one that is not; none at all leaves the plan
    alone."""
    if not chosen or is_video(Path(chosen[0])):
        return chosen
    best, why = hook_shot(stills), "the widest recorded view"
    if best is None:
        if strong_shot(Path(chosen[0])):
            return chosen
        strong = [s for s in stills if strong_shot(Path(s))]
        best = next((s for s in strong if s in chosen), strong[0] if strong else None)
        why = "a Topic or Kept photo; the planned one is not"
        if best is None:
            return chosen
    elif Path(best) == Path(chosen[0]):
        return chosen
    else:
        current = scale_score(_photo_entry(Path(chosen[0])))
        if current is not None and current >= scale_score(_photo_entry(best)):
            return chosen
    out = list(chosen)
    if best in out:
        k = out.index(best)
        out[0], out[k] = out[k], out[0]
    else:
        out[0] = best
    log.info("Opening shot: %s (%s) instead of %s.", Path(best).name, why, Path(chosen[0]).name)
    return out


@lru_cache(maxsize=64)
def _folder_credits(folder: str) -> tuple:
    try:
        return tuple(json.loads((Path(folder) / "credits.json").read_text(encoding="utf-8")).get("images", []))
    except (OSError, ValueError):
        return ()


def _clip_label(p: Path) -> str:
    if p.stem.startswith("graphic_"):
        return f"animated {p.stem.replace('graphic_', '')} graphic, use at most once"
    try:
        meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if meta.get("place_specific"):
        return "real video footage of the actual site, use at most once"
    return "real video footage, GENERIC (not the actual site): only for atmosphere, never where the narration names the place"


MAX_GRAPHICS, GRAPHICS_MAX_SHARE = 3, 0.35


def _graphics_count(chosen: List[Path]) -> int:
    return sum(1 for c in chosen if Path(c).stem.startswith("graphic_"))


def _graphics_share(chosen: List[Path], durations: List[float]) -> float:
    total = sum(durations) or 1.0
    return sum(d for c, d in zip(chosen, durations) if Path(c).stem.startswith("graphic_")) / total


def _footage_share(chosen: List[Path], durations: List[float]) -> float:
    total = sum(durations) or 1.0
    return sum(d for c, d in zip(chosen, durations) if Path(c).stem.startswith("footage_")) / total


def lock_narrative_graphics(beats: List[Dict[str, Any]], graphics: List[Path]) -> Dict[int, Path]:
    """Puts each original graphic on the beat that says what it shows (its declared figures, names
    and labels, as whole words; see src/graphic_match.py), best matches first, one graphic per beat.
    Beat 0 is the hook and keeps its photo or video.

    The map is placed first (map_beat): on the beat that names the place most strongly, the earliest of equals,
    or the beat after the hook when that is the hook."""
    locked: Dict[int, Path] = {}
    placed: set = set()
    # Production Derinkuyu, 2026-10-29: "in Cappadocia, Turkey" (beat 2) and the last line ("Derinkuyu ... between
    # Greece and Turkey") named the place equally; ties went to the latest beat, the graphics cap then dropped the
    # latest locked graphic (the map), and the scale graphic followed the hook onto the place's words instead.
    for gi, g in enumerate(graphics):
        if graphic_match.kind_of(g) == "map":
            at = map_beat(beats, g)
            if at is not None:
                locked[at] = g
                placed.add(gi)
            break
    pairs = sorted(((graphic_match.score(g, b.get("text", "")), idx, gi)
                    for gi, g in enumerate(graphics) if Path(g).stem.startswith("graphic_") and gi not in placed
                    for idx, b in enumerate(beats) if idx > 0), reverse=True)
    for s, idx, gi in pairs:
        if s < graphic_match.THRESHOLD:
            break
        if idx not in locked and gi not in placed:
            locked[idx] = graphics[gi]
            placed.add(gi)
    # A graphic that only the hook names (Richat's script opened with its location, so the map had
    # no beat) follows the hook on the next beat, while the narration is still on that subject.
    if len(beats) > 1 and 1 not in locked:
        for gi, g in enumerate(graphics):
            if (gi not in placed and Path(g).stem.startswith("graphic_")
                    and graphic_match.score(g, beats[0].get("text", "")) >= graphic_match.THRESHOLD):
                locked[1] = g
                break
    return locked


def map_beat(beats: List[Dict[str, Any]], graphic: Path) -> Optional[int]:
    """The beat the locator map belongs on: the one whose words name its place (the site, region or country it
    declares) most strongly, the earliest of equals, since the map belongs where the place is introduced; the
    beat after the hook when that strongest naming is the hook's (the opening shot is a picture). None when no
    beat names the place, or there is no beat after the hook."""
    if len(beats) < 2:
        return None
    scored = [(graphic_match.score(graphic, b.get("text", "")), -idx) for idx, b in enumerate(beats)]
    best, neg = max(scored)
    if best < graphic_match.THRESHOLD:
        return None
    return max(1, -neg)


def _is_map(p: Path) -> bool:
    return Path(p).stem.startswith("graphic_") and graphic_match.kind_of(p) == "map"


def repair_placement(planned: List[Path], durations: List[float], issues: List[Dict[str, Any]]) -> Optional[List[Path]]:
    """A graphic the audit found on screen away from the words that name it moves to the beat where they
    are spoken, and that beat's picture takes its place: the correction the planner makes, without a second
    planning call (two 3-minute plans for one moved map; production 2026-10-24). None when the move is not
    clean (the words fall in the hook, which keeps its photo, so the next beat is the earliest; the target
    already holds a graphic; the finding names no time): the planner is asked instead."""
    scenes = list(planned)
    starts = [sum(durations[:i]) for i in range(len(durations))]
    moved = False
    for issue in issues:
        if issue.get("type") != "temporal_desync":
            continue
        src, at = int(issue.get("scene_index", -1)), issue.get("trigger_time")
        if not (0 <= src < len(scenes)) or at is None or not starts:
            return None
        target = max((i for i, s in enumerate(starts) if s <= float(at)), default=0)
        if target == 0:
            target = 1
        if target >= len(scenes) or target == src or Path(scenes[target]).stem.startswith("graphic_"):
            return None
        scenes[src], scenes[target] = scenes[target], scenes[src]
        moved = True
    return scenes if moved else None


def placement_only(issues: List[Dict[str, Any]], picture_issues: str) -> List[Dict[str, Any]]:
    """The audit's placement findings, when they are the only thing to repair (no picture mismatch from
    the video check, no other repairable defect); else an empty list."""
    repairable = [i for i in issues or [] if i.get("type") in SCENE_FIXES_TYPES and i.get("scene_index", -1) >= 0]
    late = [i for i in repairable if i.get("type") == "temporal_desync"]
    return late if late and len(late) == len(repairable) and not picture_issues else []


SCENE_FIXES_TYPES = ("semantic_desync", "motion_loop", "blank_frame", "caption_collision", "temporal_desync")


def keep_if_it_matches(pick: Path, text: str, fallback: Path) -> Path:
    """An original graphic stays only on a beat that says what it shows; one that declares nothing
    to judge it by stays as chosen."""
    if not Path(pick).stem.startswith("graphic_") or not graphic_match.judgeable(pick):
        return pick
    return pick if graphic_match.score(pick, text) >= graphic_match.THRESHOLD else fallback


STORYBOARD_NOTES = "approved storyboard (matched by narration)"
# Each beat takes the picture of the storyboard line it shares most words with (a beat that straddles
# two sentences takes the closer one). A storyboard whose lines most beats don't clearly say was
# approved for other narration and is not used.
STORYBOARD_MIN_OVERLAP, STORYBOARD_MIN_BEATS = 0.6, 0.6


def photo_names(p: Path) -> list[str]:
    """The names a still answers to in name-keyed records (an approved storyboard, the fit cache): its own name
    first, then the names of the originals its checked lineage resolves to (a text-band or detail crop, a balanced
    or upscaled copy, saved as PNG since QF-13), then its stem as a JPEG (a copy whose lineage cannot be read)."""
    p = Path(p)
    try:
        st = p.stat()
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        stamp = None
    return list(_photo_names(str(p), stamp))


@lru_cache(maxsize=4096)
def _photo_names(path: str, stamp: tuple[int, int] | None) -> tuple[str, ...]:
    p = Path(path)
    names = [p.name]
    if stamp is not None:
        try:
            from .lineage import source_chain
            names += [Path(r["path"]).name for r in source_chain(p) if r.get("kind") != "lineage"]
        except (OSError, ValueError, KeyError):
            pass
    if p.suffix.lower() == ".png":
        names.append(p.with_suffix(".jpg").name)
    return tuple(dict.fromkeys(names))


def storyboard_order(storyboard: List[Dict[str, Any]], beats: List[Dict[str, Any]], options: List[Path]) -> Optional[List[int]]:
    """Option index per beat from the approved storyboard, matched by what each beat says (the
    storyboard's lines are sentences; the planner's beats are timed segments, and a revision can
    reorder them). Beats sharing no words get unused options in order. None if it doesn't fit.
    A storyboard file matches an option by its own name first, then through photo_names (a cropped copy)."""
    by_name: dict[str, int] = {}
    for i, p in enumerate(options):
        by_name.setdefault(Path(p).name, i)
    for i, p in enumerate(options):
        for name in photo_names(p)[1:]:
            by_name.setdefault(name, i)
    lines = [(graphic_match.tokens(item.get("text", "")), by_name[item["file"]])
             for item in storyboard if item.get("file") in by_name]
    picks: List[Optional[int]] = []
    strong = 0
    for b in beats:
        said = graphic_match.tokens(b.get("text", ""))
        overlap, pick = max(((len(said & words) / max(1, len(said)), i) for words, i in lines),
                            key=lambda c: c[0], default=(0.0, None))
        picks.append(pick if overlap > 0 else None)
        strong += overlap >= STORYBOARD_MIN_OVERLAP
    if strong < STORYBOARD_MIN_BEATS * len(beats):
        return None
    spare = [i for i in range(len(options)) if i not in picks] or list(range(len(options)))
    return [p if p is not None else spare[k % len(spare)] for k, p in enumerate(picks)]


CAPTION_NOTES = "caption match (no picture plan)"


def degraded_note(notes: str) -> str:
    """The review card's warning when pictures were not planned against the narration ("" when they were)."""
    if notes == CAPTION_NOTES:
        return ("⚠️ Pictures: matched by their archive captions, because the picture planner was unavailable "
                "after every retry. Check each picture fits its sentence, or Regenerate.")
    if notes == "filename order":
        return ("⚠️ Pictures: in filename order, not matched to the narration (no picture planner and no "
                "captions). Regenerate before approving.")
    return ""


def caption_order(beats: List[Dict[str, Any]], stills: List[Path]) -> Optional[List[int]]:
    """A photo per beat from archive captions alone: the unused photo sharing the most distinctive
    words with the beat (a word every caption has, like the subject's name, counts least), else the
    next unused photo in pool order. The hook keeps the pool's first photo. None if no caption exists."""
    caps = [graphic_match.tokens(_photo_caption(p)) for p in stills]
    if not any(caps):
        return None
    df = Counter(t for c in caps for t in c)
    used: Counter = Counter()
    order: List[int] = []
    for k, b in enumerate(beats):
        said = graphic_match.tokens(b.get("text", ""))
        score = [sum(1 / df[t] for t in caps[i] & said) for i in range(len(stills))]
        pick = 0 if k == 0 else max(range(len(stills)), key=lambda i: (-used[i], score[i], -i))
        used[pick] += 1
        order.append(pick)
    return order


@llm_task("scene_plan")
def plan_scenes(beats: List[Dict[str, Any]], media: List[Path], feedback: str = "",
                use_storyboard: bool = True, reserve: Optional[List[Path]] = None,
                pinned: Optional[Dict[int, Path]] = None) -> Tuple[List[Path], List[float], str]:
    """Returns (media per beat, duration per beat, notes). A motion hook clip stays first.
    `feedback` lists problems a reviewer found with a previous plan, so they are avoided.
    `use_storyboard`: honour an approved Reel storyboard (its beats are numbered for a Reel; a
    documentary's many cuts are planned from the narration instead). `reserve`: stills offered only where a
    picture would otherwise be shown twice (no_repeats), never to the planner or the opening.
    `pinned`: {0-based beat index: Path} beats verified as passing in a previous pass, locked against re-planning."""
    durations = [b["end"] - b["start"] for b in beats]
    hook = media[0] if media and is_video(media[0]) and not _is_graphic(media[0]) else None
    stills = [p for p in media if not is_video(p)]
    graphics = [p for p in media if _is_graphic(p)]
    options = stills + graphics  # what the planner chooses from (graphics shown by their poster frame)
    n = len(beats)

    order = list(range(n))  # default: filename order of the photos, cycling if there are more beats than photos
    notes = "filename order"
    failure = "no picture planner is available"

    # 1. Deterministic Storyboard Contract:
    # If the creator approved a storyboard in Telegram, lock those exact approved assets directly,
    # ensuring zero API cost, zero flakiness, and 100% fidelity to what was reviewed.
    storyboard = None
    if not feedback and use_storyboard:
        for p in stills:
            sb_candidate = p.parent / "storyboard.json"
            if sb_candidate.is_file():
                try:
                    storyboard = json.loads(sb_candidate.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass

    if storyboard and not feedback:
        locked_order = storyboard_order(storyboard, beats, options)
        if locked_order is not None:
            order = locked_order
            notes = STORYBOARD_NOTES
        else:
            log.info("The approved storyboard was made for different narration; planning scenes afresh.")

    # 2. LLM Multimodal Scene Planning (used when no approved storyboard exists or reviewer requested feedback)
    if notes != STORYBOARD_NOTES and (free_llm.available() if free_llm.enabled() else claude_available()) and stills:
        try:
            mark = prompt_fence.new_token()   # archive captions are quoted data, never instructions (QF-41)
            content: List[dict] = [{"type": "text", "text": prompt_fence.fence_note(mark)}]
            for i, p in enumerate(options, 1):
                if p in graphics:
                    content += [{"type": "text", "text": f"Image {i} ({_clip_label(p)}):"}, image_block(p.with_suffix(".jpg"))]
                else:
                    label = _caption_label(p, mark)
                    content += [{"type": "text", "text": f"Image {i}" + (f" ({label}):" if label else ":")}, image_block(p)]
            lines = "\n".join(f"Beat {i}: {b['text']}" for i, b in enumerate(beats, 1))
            fix = f"\n\nA reviewer found these problems with the previous choice; fix them:\n{feedback}" if feedback else ""
            pinned_txt = ""
            if pinned:
                pinned_notes = [f"Beat {k + 1} is already verified and locked to Image {options.index(p) + 1} ({p.name}); do NOT change it."
                                for k, p in pinned.items() if p in options]
                if pinned_notes:
                    pinned_txt = "\n\nLocked beats (keep these exact images):\n" + "\n".join(pinned_notes)
            content.append({"type": "text", "text": (
                f"Narration beats ({n}):\n{lines}{fix}{pinned_txt}\n\n"
                f"Return exactly {n} image numbers (1-{len(options)}), one per beat, in order.")})
            # Pictures that follow the narration are the point of the video, so a failed plan is tried
            # again within the run (the stages before it are kept), waiting longer each time, before the
            # caption fallback below is used.
            wait = config.SCENE_PLAN_RETRY_SECONDS
            for attempt in range(1, config.SCENE_PLAN_ATTEMPTS + 1):
                try:
                    plan = (free_llm.call(SYSTEM, content, ScenePlan, effort="low", max_tokens=4000)
                            if free_llm.enabled() else structured_call(SYSTEM, content, ScenePlan, effort="low", max_tokens=4000))
                    prompt_fence.check_reply(plan, mark)
                except LLMError as e:
                    failure = f"Scene planning attempt {attempt}/{config.SCENE_PLAN_ATTEMPTS} failed ({e})"
                else:
                    picks = [i - 1 for i in plan.images]
                    if len(picks) == n and all(0 <= i < len(options) for i in picks):
                        if pinned:
                            for k, p in pinned.items():
                                if 0 <= k < n and p in options:
                                    picks[k] = options.index(p)
                        order, notes = picks, plan.notes
                        break
                    failure = f"Scene plan attempt {attempt} had {len(picks)} picks for {n} beats or bad numbers"
                log.warning("%s.", failure)
                if attempt < config.SCENE_PLAN_ATTEMPTS:
                    log.info("Trying the scene plan again in %.0fs.", wait)
                    time.sleep(wait)
                    wait *= 2
        except LLMError as e:  # preparing the pictures failed
            failure = f"Scene planning failed ({e})"
            log.warning("%s.", failure)

    # 3. Without a picture plan (quota used up, a vision timeout: Richat's documentary went out in
    # filename order), each beat takes the unused photo whose archive caption shares its words.
    if notes == "filename order" and stills:
        matched = caption_order(beats, stills)
        if matched is not None:
            order, notes = matched, CAPTION_NOTES
        # Recorded for the review card and the audit ledger: a degraded plan never passes silently.
        from . import improve
        improve.note("pictures.plan_degraded", f"{notes}: {failure}", source="scene_plan")

    planned = notes != "filename order"  # without a plan, photos only, in filename order
    chosen: List[Path] = []
    used_graphics = set()
    for k, i in enumerate(order):
        pick = options[i] if planned else stills[i % len(stills)]
        if pick in graphics:
            if pick in used_graphics:  # a graphic shown twice reads as padding: use a photo instead
                pick = stills[k % len(stills)]
            used_graphics.add(pick)
        chosen.append(pick)

    # Hard-lock narrative-matching graphics to their corresponding spoken beats
    locked = lock_narrative_graphics(beats, graphics)  # never beat 0: the opening shot is a picture (below)
    for idx, g in locked.items():
        if idx < len(chosen) and (not pinned or idx not in pinned):
            chosen[idx] = g
    # A locked graphic appears only on its beat: any other copy (the planner's own pick) becomes a photo.
    if stills:
        for k, p in enumerate(chosen):
            if p in locked.values() and locked.get(k) != p and (not pinned or k not in pinned):
                chosen[k] = stills[k % len(stills)]

    # A graphic the planner placed where the narration doesn't say what it shows becomes a photo.
    if stills:
        for k in range(len(chosen)):
            if k not in locked and (not pinned or k not in pinned) and k < len(beats):
                chosen[k] = keep_if_it_matches(Path(chosen[k]), beats[k].get("text", ""), stills[k % len(stills)])

    # Verified pinned beats from a previous pass are preserved unconditionally
    if pinned:
        for k, p in pinned.items():
            if 0 <= k < len(chosen) and p in options:
                chosen[k] = p

    # The opening shot is a picture, never a graphic: the planner opened the Nazca video on the map, since the
    # first line names the place (2026-10-25). The map's own line still accepts it one cut later.
    if chosen and stills and _is_graphic(Path(chosen[0])):
        chosen[0] = next((s for s in stills if s not in chosen), stills[0])
    # The opening shows the scale at once, on a Topic or Kept photo (open_on_scale); an approved storyboard's
    # opening is the operator's and stands (a context shot there is said on the card: storyboard_opening_note).
    if notes != STORYBOARD_NOTES and stills:
        chosen = open_on_scale(chosen, stills)

    # Footage supports the narration, never dominates it (Meta's originality rules): over the
    # cap, the latest footage picks become photos.
    k = len(chosen) - 1
    while _footage_share(chosen, durations) > FOOTAGE_MAX_SHARE and k >= 0:
        if Path(chosen[k]).stem.startswith("footage_"):
            chosen[k] = stills[k % len(stills)]
        k -= 1
    # Our own graphics carry information, but a Reel of diagrams loses the place: at most
    # MAX_GRAPHICS of them and GRAPHICS_MAX_SHARE of the runtime; extras become photos.
    # Unlocked graphics go first; a graphic locked to the beat that names it goes only if still over, and the
    # map, locked where the place is named, last of all (production Derinkuyu, 2026-10-29: four locked graphics
    # for a cap of three, and the map, latest on screen, was the one dropped).
    over = lambda: _graphics_count(chosen) > MAX_GRAPHICS or _graphics_share(chosen, durations) > GRAPHICS_MAX_SHARE  # noqa: E731
    for spared in ("locked", "map", ""):
        k = len(chosen) - 1
        while over() and k >= 0:
            keep = (k in locked and (spared == "locked" or (spared == "map" and _is_map(Path(chosen[k])))))
            if Path(chosen[k]).stem.startswith("graphic_") and not keep:
                chosen[k] = stills[k % len(stills)]
            k -= 1
    if any(_is_map(Path(g)) for g in locked.values()) and not any(_is_map(Path(c)) for c in chosen):
        log.warning("The locator map was locked to the beat that names the place but is not in the plan.")
    chosen = no_repeats(chosen, stills, reserve, pinned=pinned)
    if pinned:
        for k, p in pinned.items():
            if 0 <= k < len(chosen) and p in options:
                chosen[k] = p
    if hook is not None:
        chosen[0] = hook
    return chosen, durations, notes


FIT_NOTE_LABEL = "🖼 Pictures checked against their lines before the render"
FIT_NOTE_MAX_BEATS = 4   # beats named on the card; the rest are counted


def fit_to_lines(beats: List[Dict[str, Any]], scenes: List[Path], pool: List[Path], subject: str,
                 durations: Optional[List[float]] = None, line_beat: Optional[Dict[Path, int]] = None,
                 cancel=None, max_calls: Optional[int] = None, swaps: Optional[int] = None,
                 reserve: Optional[List[Path]] = None,
                 topic: Optional[Dict[str, Any]] = None,
                 work_dir: Optional[Path] = None) -> Tuple[List[Path], Dict[str, Any]]:
    """Each photo beat's picture judged against its own line before the render (render audit 2026-10-28), with a
    bounded repair. Returns (scenes, report).

    Judged: every beat after the hook whose pick is a still. Not judged: the hook (the opening shot is the
    topic's strongest picture whatever the first line says), our own graphics and real footage (clips: a
    graphic is placed by the words it declares, src/graphic_match.py), and AI motion (made after this).
    A picture that does not show what its line says is repaired, in order:
      1. another unused still from `pool` (what the planner chose from) that does show it, the photos found
         for that line first (`line_beat`: still -> 1-based beat it was fetched for), at most `swaps` tried;
      2. one of our unplaced graphics whose declared words the line says, within the graphics caps;
      3. where the previous shot would be held (4): an unused `reserve` photo that shows the line, else one of
         our graphics not yet on screen (within the caps), so one picture is not shown over two lines;
      3.5 [Phase 2.5]: era-matched generative gap art, synthesized and quarantined pre-render;
      4. the previous beat's still, held (it was judged right for its own line, and is not itself held),
         so the shot carries on rather than a picture of something else being asserted under the line;
    otherwise the pick stays.

    With config.PICTURE_CASCADE (2026-11-02, src/picture_coverage.py) the steps follow the fixed cascade: after the
    related (same-kind) photo and the reserve, a detail crop of one of the topic's strongest approved photos on a
    region not yet shown, then archive film whose record names what the line names, then the locator map when the
    line names its place, then one of our graphics (whether or not the previous shot could be held), then the
    reconstruction, and only then a held shot. report["tiers"] records, per repaired beat, the tier that filled it. Every beat repaired by 3 or not at all is listed in report["unshown"], for the
    review card and the findings. The judge after the render stays the final gate.

    Verdicts are cached by photo hash and line (src/beat_images.py): a resumed or re-rendered run makes no new
    call. New calls are serial (the free tier's rule) and stop at `max_calls`; a check that cannot run stops the
    rest (the light model is down: each further try would wait out its retry) and the plan is kept."""
    from . import beat_images
    out = [Path(p) for p in scenes]
    report: Dict[str, Any] = {"judged": 0, "cached": 0, "swapped": [], "graphic": [], "held": [], "related": [],
                              "generated": [], "unshown": [], "stopped": "", "tiers": []}
    if not config.PICTURE_VISION_CHECK or not getattr(config, "PRE_RENDER_PICTURE_FIT", True):
        report["stopped"] = "off"
        return out, report
    max_calls = int(getattr(config, "PICTURE_FIT_MAX_CALLS", 30) if max_calls is None else max_calls)
    swaps = int(getattr(config, "PICTURE_FIT_SWAPS", 3) if swaps is None else swaps)
    line_beat = {Path(k): v for k, v in (line_beat or {}).items()}
    durations = list(durations or [b.get("end", 0) - b.get("start", 0) for b in beats])
    cache = beat_images.load_fit_cache()
    fits_at: Dict[int, bool] = {}   # beat index -> its final picture was judged right for its line
    held: set = set()
    cascade_on = bool(getattr(config, "PICTURE_CASCADE", True))
    crop_regions: dict[str, list] = {}   # picture_id -> the detail-crop regions already cut from it
    # The illustration tier and the per-line plan (2026-11-02): each beat's planned visual class and protected flag
    # from the check before the voice, the run's fact sheet and its per-run call budget.
    ictx = _illustration_context(beats, topic, work_dir, durations, pool)
    from .picture_coverage import ABSTRACT, NAMED_SUBJECT
    visual_of = [((v or {}).get("visual") or (b.get("visual") if isinstance(b, dict) else None))
                 for v, b in zip(ictx["verdicts"], beats)]

    def tiered(k: int, tier: str, to: Path, cascade: bool = True) -> None:
        report["tiers"] = [r for r in report["tiers"] if r.get("beat") != k + 1] + [
            {"beat": k + 1, "tier": tier, "to": Path(to).name, "cascade": cascade}]

    def graphic_tier(g: Path) -> str:
        from .picture_coverage import GRAPHIC, MAP
        return MAP if _is_map(Path(g)) else GRAPHIC

    def still(p: Path) -> bool:
        return not is_video(p) and Path(p).is_file()

    def verdict(p: Path, line: str) -> Optional[Dict[str, Any]]:
        if report["stopped"]:
            return None
        key_known = beat_images.fit_key(_digest(p), line) in cache
        if not key_known:
            if report["judged"] >= max_calls:
                report["stopped"] = f"call budget ({max_calls}) reached"
                log.info("Picture-line check: %s; the remaining beats keep the plan.", report["stopped"])
                return None
            if cancel is not None:
                cancel()
        v = beat_images.line_fit(p, line, subject, cache)
        if v is None:
            if not key_known:
                report["stopped"] = "the picture check could not run"
                log.warning("Picture-line check stopped: %s; the plan is kept (the video check still looks).",
                            report["stopped"])
            return None
        report["cached" if v["cached"] else "judged"] += 1
        return v

    try:
        for k in range(1, min(len(out), len(beats))):
            line = (beats[k].get("text") or "").strip()
            pick = out[k]
            if not line or not still(pick):
                continue
            first = verdict(pick, line)
            if first is None:
                if report["stopped"]:
                    break
                continue
            if first["fits"]:
                fits_at[k] = True
                continue
            log.info("Beat %d: %s does not show its line %r (%s).", k + 1, pick.name, clip(line), first["reason"])
            # 1. another unused still that shows the line: those fetched for this line first, then pool order
            in_use = {picture_id(p) for p in out}   # a cropped copy of a placed photo is that photo
            spare = [Path(p) for p in pool if still(Path(p)) and picture_id(Path(p)) not in in_use]
            spare.sort(key=lambda p: 0 if line_beat.get(p) == k + 1 else 1)  # stable: pool order otherwise
            swapped = None
            for cand in spare[:swaps]:
                v = verdict(cand, line)
                if v is None:
                    if report["stopped"]:
                        break
                    continue
                if v["fits"]:
                    swapped = cand
                    break
            if swapped is not None:
                out[k], fits_at[k] = swapped, True
                report["swapped"].append({"beat": k + 1, "from": pick.name, "to": swapped.name})
                tiered(k, "exact", swapped, cascade=False)
                log.info("Beat %d: %s swapped in, it shows the line.", k + 1, swapped.name)
                continue
            if report["stopped"]:
                break
            named = visual_of[k] == NAMED_SUBJECT
            # 1.5 (named subject, operator refinement 2026-11-02) an openly licensed reference image of that very
            # thing that is fit to show (size, no text or watermark, it shows what the line says) is SHOWN, as the
            # exact tier: a reference fit for the screen is never redrawn.
            if named and work_dir is not None:
                shown = _show_reference(k, line, out, pool, topic, ictx, report, verdict, in_use, Path(work_dir))
                if shown is not None:
                    out[k], fits_at[k] = shown, True
                    report["swapped"].append({"beat": k + 1, "from": pick.name, "to": shown.name, "reference": True})
                    tiered(k, "exact", shown, cascade=False)
                    log.info("Beat %d: reference %s shown, it shows the line.", k + 1, shown.name)
                    continue
                if report["stopped"]:
                    break
            # 2. one of our own graphics that says what the line says, if the caps allow it
            graphic = next((g for g in pool if _is_graphic(Path(g)) and Path(g).stem.startswith("graphic_")
                            and picture_id(Path(g)) not in in_use
                            and graphic_match.judgeable(Path(g))  # one that declares nothing is never "what the line says"
                            and graphic_match.score(Path(g), line) >= graphic_match.THRESHOLD), None)
            if graphic is not None:
                trial = list(out)
                trial[k] = Path(graphic)
                if _graphics_count(trial) <= MAX_GRAPHICS and _graphics_share(trial, durations) <= GRAPHICS_MAX_SHARE:
                    out[k] = Path(graphic)
                    report["graphic"].append({"beat": k + 1, "from": pick.name, "to": Path(graphic).name})
                    tiered(k, graphic_tier(Path(graphic)), Path(graphic))
                    log.info("Beat %d: our %s graphic placed, it says what the line says.", k + 1, Path(graphic).stem)
                    continue
            entry = {"beat": k + 1, "line": line, "picture": pick.name, "reason": first["reason"], "fallback": "kept"}
            # Never the unrelated pick (production Derinkuyu, 2026-10-29: "and food supplies" over a painted stone
            # panel, "individual sections" over a dark rock close-up): an unused photo whose recorded descriptions
            # (its archive caption, what the photo review and earlier looks saw) name what the line names, chosen
            # from what is already known, with no new call; one already judged wrong for this line never.
            spare_all = [Path(p) for p in list(pool) + list(reserve or []) if still(Path(p))
                         and picture_id(Path(p)) not in in_use]
            # A same-kind photo under a named subject would need an on-screen "COMPARABLE EXAMPLE" label, which the
            # composer does not draw: not used for a named subject (2026-11-02, illustration panel).
            related = related_shot(line, spare_all, cache) if visual_of[k] != NAMED_SUBJECT else None
            if related is not None:
                out[k], entry["fallback"], entry["related"] = related, "related", related.name
                report["related"].append({"beat": k + 1, "from": pick.name, "to": related.name})
                tiered(k, "same_kind", related)
                log.info("Beat %d: no photo shows its line; %s, which shows what it names, instead of %s.",
                         k + 1, related.name, pick.name)
                report["unshown"].append(entry)
                continue
            prev = out[k - 1]
            holds = fits_at.get(k - 1) and still(prev) and (k - 1) not in held
            if holds:
                # Before the same shot is held over a second line (production Derinkuyu, 2026-10-29: beats 5/6 and
                # 10/11 showed one picture each): a reserve photo that shows the line, else one of our graphics not
                # yet on screen (a map, numbers, a scale), within the graphics caps.
                spare_reserve = [Path(p) for p in reserve or [] if still(Path(p)) and picture_id(Path(p)) not in in_use]
                found = None
                for cand in spare_reserve[:swaps]:
                    v = verdict(cand, line)
                    if v is None:
                        if report["stopped"]:
                            break
                        continue
                    if v["fits"]:
                        found = cand
                        break
                if found is not None:
                    out[k], fits_at[k] = found, True
                    report["swapped"].append({"beat": k + 1, "from": pick.name, "to": found.name, "reserve": True})
                    tiered(k, "exact", found, cascade=False)
                    log.info("Beat %d: reserve photo %s swapped in, it shows the line.", k + 1, found.name)
                    continue
            if cascade_on:
                # The cascade's middle tiers (src/picture_coverage.py): a detail crop, archive film, the map. For a
                # named subject the illustration of that subject comes before the map (2026-11-02), and its crop is
                # one located on the named feature and confirmed (operator refinement): never one chosen by detail.
                filled = _cascade_fill(k, line, out, pool, durations, crop_regions, work_dir, report["stopped"],
                                       only=("located_crop", "archive_film") if named else None,
                                       subject=" ".join(_subject_terms(line, topic)))
                if filled is None and named:
                    if _illustrate(k, line, out, durations, report, ictx, pick, entry, visual_of, tiered):
                        continue
                    filled = _cascade_fill(k, line, out, pool, durations, crop_regions, work_dir, report["stopped"],
                                           only=("map",))
                if filled is not None:
                    tier, asset = filled
                    out[k], entry["fallback"] = asset, tier
                    entry["filled_by"] = asset.name
                    tiered(k, tier, asset)
                    if tier == "map":
                        report["graphic"].append({"beat": k + 1, "from": pick.name, "to": asset.name, "filler": True})
                    log.info("Beat %d: no photo shows its line; filled by the %s tier (%s).", k + 1, tier, asset.name)
                    report["unshown"].append(entry)
                    continue
            # A named subject goes map, then held shot (follow-up review, 2026-11-02): a graphic not matched to that
            # very thing is never put between them.
            if (holds or cascade_on) and not named:
                filler = None
                # Never once the checks stopped (their budget, or the model down): what the graphic says would go
                # unjudged against the line; the shot is held, as before.
                for g in ([] if report["stopped"] else pool):
                    if (_is_graphic(Path(g)) and Path(g).stem.startswith("graphic_")
                            and picture_id(Path(g)) not in in_use
                            and graphic_match.judgeable(Path(g))):   # it shows real declared content
                        trial = list(out)
                        trial[k] = Path(g)
                        if _graphics_count(trial) <= MAX_GRAPHICS and _graphics_share(trial, durations) <= GRAPHICS_MAX_SHARE:
                            filler = Path(g)
                            break
                if filler is not None:
                    out[k], entry["fallback"] = filler, "graphic"
                    report["graphic"].append({"beat": k + 1, "from": pick.name, "to": filler.name, "filler": True})
                    tiered(k, graphic_tier(filler), filler)
                    log.info("Beat %d: no photo shows its line; our %s graphic instead of holding the previous shot.",
                             k + 1, filler.stem)
                    report["unshown"].append(entry)
                    continue
            # 3.5 the illustration tier (src/image_gen.py): never on an abstract line, never once the checks stopped,
            # within the caps; a generated beat is never "judged right" (it is not held over the next beat as exact).
            if not named and visual_of[k] != ABSTRACT and _illustrate(k, line, out, durations, report, ictx, pick,
                                                                       entry, visual_of, tiered):
                continue
            # QF-58b (operator, 2026-11-10, "matched photo only"): in a Reel, a held shot the gate would refuse (a
            # repeat, or a hold over 6 s) gives way to the beat's own photo only when the planner matched that photo
            # to this very line (`line_beat`: it was found for this beat) and no other beat shows it; otherwise the
            # 2026-10-29 rule stands (the shot is held) and the gate may stop the run.
            if holds or (still(prev) and related_before(k, out, fits_at, report)):
                target = prev
            else:
                target = out[0] if still(out[0]) else None
            if (target is not None and line_beat.get(Path(pick)) == k + 1
                    and _hold_refused(out, durations, k, Path(target), pool, reserve) and _distinct_pick(out, k)):
                entry["fallback"] = "kept"
                report.setdefault("kept", []).append({"beat": k + 1, "picture": pick.name,
                                                      "instead_of": Path(target).name})
                log.info("Beat %d: no picture shows its line; %s stays (holding %s would be refused by the Reel gate).",
                         k + 1, pick.name, Path(target).name)
            # 4. the previous beat's still held, when it was right for its own line and is not itself held
            elif holds:
                out[k], entry["fallback"] = prev, "held"
                held.add(k)
                report["held"].append({"beat": k + 1, "from": pick.name, "to": prev.name})
                log.info("Beat %d: no picture shows its line; the previous shot (%s) is held.", k + 1, prev.name)
            elif still(prev) and related_before(k, out, fits_at, report) and _held_run(out, durations, k) <= HELD_SHOT_MAX:
                # 5. the previous related shot held longer (the opening, a shot judged right for its line, or one
                # chosen above as related), within HELD_SHOT_MAX on screen: a picture held on is never a claim,
                # and a photo of something else under the words is (operator, 2026-10-29).
                out[k], entry["fallback"] = prev, "held"
                held.add(k)
                report["held"].append({"beat": k + 1, "from": pick.name, "to": prev.name, "longer": True})
                log.info("Beat %d: no picture shows its line; the previous related shot (%s) is held on rather than %s.",
                         k + 1, prev.name, pick.name)
            elif still(prev) and related_before(k, out, fits_at, report):
                # 6. Pre-render asset quarantine: when the planned picture contradicts its line, holding
                # the previous related shot past HELD_SHOT_MAX is strictly preferred over asserting a contradictory
                # photograph on screen (which triggers major QA gate blocks).
                out[k], entry["fallback"] = prev, "held"
                held.add(k)
                report["held"].append({"beat": k + 1, "from": pick.name, "to": prev.name, "quarantine_hold": True})
                log.info("Beat %d: asset quarantine: planned %s contradicts line %r; holding previous related shot (%s) to prevent QA gate block.",
                         k + 1, pick.name, clip(line), prev.name)
            elif still(out[0]):
                # 7. Fallback to anchor relic/site hero shot rather than asserting a contradictory asset
                out[k], entry["fallback"] = out[0], "held"
                held.add(k)
                report["held"].append({"beat": k + 1, "from": pick.name, "to": out[0].name, "quarantine_anchor": True})
                log.info("Beat %d: asset quarantine: planned %s contradicts line %r; holding hero anchor (%s) to prevent QA gate block.",
                         k + 1, pick.name, clip(line), out[0].name)
            entry["macro_drift"] = True
            report.setdefault("macro_drifts", []).append(k)
            report["unshown"].append(entry)
    finally:
        report["fits"] = sorted(k + 1 for k, ok in fits_at.items() if ok)   # beats whose picture was judged right
        if report["judged"]:
            beat_images.save_fit_cache(cache)
    return out, report


def _hold_refused(out: list[Path], durations: list[float], k: int, target: Path, pool: list[Path],
                  reserve: list[Path] | None = None) -> bool:
    """Whether holding `target` on beat k (0-based) makes a Reel the plan gate (reel_photo_coverage) refuses: a
    picture shown again away from its own beat, a hold over REEL_PHOTO_HOLD_MAX, or a picture on two beats in a
    pool that is not short (hold_for_short_pool joins beats only when the pool is short, so the gate sees a
    repeat). Always False in 16:9 (no such gate)."""
    if config.VIDEO_WIDTH >= config.VIDEO_HEIGHT:
        return False
    if k < 1 or picture_id(Path(target)) != picture_id(Path(out[k - 1])):
        return True
    if _held_run(out, durations, k) > config.REEL_PHOTO_HOLD_MAX + 1e-6:
        return True
    stills = [p for p in out if _shot_kind(Path(p)) is not None]
    available = {picture_id(Path(p)) for p in list(pool) + list(reserve or [])
                 if not is_video(Path(p)) and _shot_kind(Path(p)) is not None}
    return len(available) >= len(stills)


def _distinct_pick(out: list[Path], k: int) -> bool:
    """Whether beat k's own picture is a photo no other beat shows (keeping it repeats nothing)."""
    if _shot_kind(Path(out[k])) != "photo":
        return False
    source = picture_id(Path(out[k]))
    return all(picture_id(Path(p)) != source for j, p in enumerate(out) if j != k)


# ------------------------------------------------------------------ the illustration tier (2026-11-02)
def _illustration_context(beats: list[dict[str, Any]], topic: dict[str, Any] | None, work_dir: Path | None,
                          durations: list[float], pool: list[Path]) -> dict[str, Any]:
    """What the illustration step needs, read once a planner call: each beat's verdict from the check before the
    voice (visual class, protected), the script record and fact sheet (the spec's source), the per-run budget (run
    folder only: never a shared topic folder), the cap on illustrated beats and the frame's shape."""
    from .picture_coverage import beat_verdicts
    verdicts = beat_verdicts(work_dir, beats)
    ctx: dict[str, Any] = {"verdicts": verdicts, "enabled": False}
    if not getattr(config, "GENERATIVE_GAP_FILLING", True) or work_dir is None:
        return ctx
    from . import image_gen
    payload: dict[str, Any] = {}
    try:
        payload = json.loads((Path(work_dir) / "script_metadata.json").read_text(encoding="utf-8"))
        payload = payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        payload = {}
    total = float(sum(durations or [])) or 0.0
    long_form = total > float(getattr(config, "IMAGE_GEN_LONGFORM_SECONDS", 180))
    cap = int(getattr(config, "IMAGE_GEN_MAX_LONGFORM", 4) if long_form else getattr(config, "MAX_GAP_IMAGES_PER_RUN", 2))
    ctx.update({"enabled": True, "topic": topic or {}, "payload": payload,
                "budget": image_gen.Budget(Path(work_dir)), "work_dir": Path(work_dir), "cap": cap,
                "long_form": long_form, "total": total, "pool": list(pool or []),
                "aspect": "16:9" if config.VIDEO_WIDTH > config.VIDEO_HEIGHT else "9:16",
                "protected": [bool((v or {}).get("protected")) for v in verdicts]})
    return ctx


def _fact_sheet_for(topic: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any] | None:
    """The topic's fact sheet (the illustration's only source of descriptors), or None."""
    tid = (topic or {}).get("id") or payload.get("topic_id")
    if not tid:
        return None
    try:
        from . import fact_sheet
        return fact_sheet.load(str(tid), max_age_days=3650)
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.info("Illustration: no fact sheet for %s (%s).", tid, e)
        return None


def is_illustration(p: Path) -> bool:
    return Path(p).stem.startswith("gap_gen_")


def illustration_allowed(k: int, out: list[Path], durations: list[float], ctx: dict[str, Any],
                         stopped: str = "") -> str:
    """"" when beat k (0-based) may take an illustration, else why not: never the hook, never once the checks
    stopped, never on a shot under config.IMAGE_GEN_MIN_SHOT_SECONDS, never next to another illustrated beat,
    within the cap on illustrated beats (Reels/Shorts MAX_GAP_IMAGES_PER_RUN and IMAGE_GEN_MAX_RUNTIME_SHARE of
    the runtime; long-form IMAGE_GEN_MAX_LONGFORM), protected lines served first (their slots are held back)."""
    if not ctx.get("enabled"):
        return "off"
    if k <= 0:
        return "never on the hook"
    if stopped:
        return f"the checks stopped ({stopped})"
    dur = durations[k] if k < len(durations) else 0.0
    if dur < float(getattr(config, "IMAGE_GEN_MIN_SHOT_SECONDS", 1.5)):
        return f"the shot is {dur:.1f}s, too short for an illustration"
    if (k > 0 and is_illustration(out[k - 1])) or (k + 1 < len(out) and is_illustration(out[k + 1])):
        return "the next or previous beat is already illustrated"
    used = [j for j, p in enumerate(out) if is_illustration(p) and j != k]
    protected = ctx.get("protected") or []
    pending = sum(1 for j in range(k + 1, len(out)) if j < len(protected) and protected[j] and not is_illustration(out[j]))
    mine = k < len(protected) and protected[k]
    if len(used) + 1 + (0 if mine else pending) > int(ctx.get("cap", 0)):
        return "the cap on illustrated beats is reached" + ("" if mine or not pending else " (held for protected lines)")
    if not ctx.get("long_form") and ctx.get("total"):
        share = (sum(durations[j] for j in used if j < len(durations)) + dur) / ctx["total"]
        if share > float(getattr(config, "IMAGE_GEN_MAX_RUNTIME_SHARE", 0.15)) + 1e-9:
            return f"illustrations would hold {share:.0%} of the runtime"
    return ""


def _illustrate(k: int, line: str, out: list[Path], durations: list[float], report: dict[str, Any],
                ctx: dict[str, Any], pick: Path, entry: dict[str, Any], visual_of: list[str | None], tiered) -> bool:
    """Fills beat k with a labelled illustration when allowed (illustration_allowed) and the fact sheet gives the
    line's subject physical descriptors (image_gen.spec_from_facts); True when filled. The beat is recorded as
    unshown (fallback "generated_art") and in report["generated"], never as judged right for its line."""
    from .picture_coverage import ABSTRACT, RECONSTRUCTION
    if visual_of[k] == ABSTRACT:
        return False
    why = illustration_allowed(k, out, durations, ctx, report.get("stopped") or "")
    if why:
        if why != "off":
            report.setdefault("illustration_skipped", []).append({"beat": k + 1, "why": why})
        return False
    from . import image_gen
    if "sheet" not in ctx:   # read once, and only when a beat may take an illustration
        ctx["sheet"] = _fact_sheet_for(ctx.get("topic"), ctx.get("payload") or {})
    spec, why = image_gen.spec_from_facts(line, ctx.get("topic"), ctx.get("sheet"), ctx.get("payload"))
    if spec is None:
        report.setdefault("illustration_skipped", []).append({"beat": k + 1, "why": why})
        log.info("Beat %d: no illustration (%s).", k + 1, why)
        return False
    others = [Path(p) for p in list(out) + list(ctx.get("pool") or [])
              if not is_video(Path(p)) and Path(p).is_file() and not is_illustration(Path(p)) and Path(p) != Path(pick)][:40]
    others += [Path(p) for p in out if is_illustration(Path(p)) and Path(p).is_file()]
    # Reliable depiction (operator, 2026-11-02): a named subject is drawn only from an openly licensed reference
    # image of that very thing (image_gen.find_references); illustrate_beat requests nothing without one, and the
    # beat falls to the next tier with the reason recorded. A kind of thing is drawn from its descriptors alone.
    from .picture_coverage import NAMED_SUBJECT
    refs: list[dict[str, Any]] = []
    why_none, reason = "", ""
    if visual_of[k] == NAMED_SUBJECT:
        known = (ctx.get("references") or {}).get(k)   # what the reference step (_show_reference) found and judged
        if known is not None:
            refs, why_none, unfit = list(known["refs"]), known["why"], list(known["unfit"])
        else:
            refs, why_none = image_gen.find_references(line, ctx.get("topic"), _reference_photos(ctx.get("pool") or []),
                                                       ctx["work_dir"])
            unfit = []
        if not refs:
            log.info("Beat %d: %s.", k + 1, why_none)
        # Why a named subject may be drawn at all: a reconstruction, or every reference unfit for the screen.
        reason = image_gen.illustration_reason(line, unfit, len(refs))
    art, info = image_gen.illustrate_beat(line, spec, ctx["work_dir"], aspect=ctx.get("aspect", "9:16"),
                                          budget=ctx.get("budget"), others=others, references=refs,
                                          visual=visual_of[k], reason=reason)
    if art is None:
        why = info.get("reason") or "rejected"
        if visual_of[k] == NAMED_SUBJECT and not refs:
            why = f"{why} ({why_none})"
        report.setdefault("illustration_skipped", []).append({"beat": k + 1, "why": why,
                                                              "calls": info.get("calls", 0)})
        log.info("Beat %d: no illustration passed (%s).", k + 1, why)
        return False
    out[k] = art
    entry["fallback"], entry["filled_by"] = "generated_art", art.name
    used_refs = [r for r in info.get("references") or refs if isinstance(r, dict)]
    report.setdefault("generated", []).append({"beat": k + 1, "from": Path(pick).name, "to": art.name, "line": line,
                                               "model": info.get("model"), "descriptors": spec.get("required"),
                                               "protected": k < len(ctx.get("protected") or []) and ctx["protected"][k],
                                               "path": str(art), "visual": visual_of[k], "why_illustrated": reason,
                                               "references": [{key: r.get(key) for key in
                                                               ("path", "url", "title", "licence", "source")}
                                                              for r in used_refs]})
    tiered(k, RECONSTRUCTION, art)
    report["unshown"].append(entry)   # an illustration is honest cover, never a photo of the line
    log.info("Beat %d: labelled illustration %s (no photo shows the line).", k + 1, art.name)
    return True


def _show_reference(k: int, line: str, out: list[Path], pool: list[Path], topic: dict[str, Any] | None,
                    ctx: dict[str, Any], report: dict[str, Any], verdict, in_use: set, work_dir: Path) -> Path | None:
    """A reference image of the named subject (image_gen.find_references) fit to show under beat k's line, or
    None. Fit to show: its short side at least image_gen.SHOW_MIN_SIDE, the picture-line check says it shows what
    the line says, and a look sees no burned-in text, watermark or intruding object. A check that cannot run fails
    closed. What was found and why each reference was not shown (its kind: "screen", "line" or "unknown") is kept
    in ctx["references"][k] for the illustration step; a shown museum reference's credit goes to
    report["reference_credits"]."""
    from . import beat_images, image_gen
    refs, why = image_gen.find_references(line, topic, _reference_photos(pool), work_dir)
    unfit: list[str] = []
    notes: list[str] = []
    for ref in refs:
        path = Path(str(ref["path"]))
        if picture_id(path) in in_use:
            unfit.append("unknown")   # shown elsewhere already: displayable, not unfit; never redrawn for that
            notes.append(f"{path.name}: already on screen")
            continue
        small = image_gen.screen_unfit(path)
        if small:
            unfit.append("screen")
            notes.append(f"{path.name}: {small}")
            continue
        v = verdict(path, line)
        if v is None:
            unfit.append("unknown")
            notes.append(f"{path.name}: the picture check could not run")
            if report["stopped"]:
                break
            continue
        if not v["fits"]:
            unfit.append("line")
            notes.append(f"{path.name}: does not show what the line says ({clip(v.get('reason', ''), 80)})")
            continue
        try:
            look = beat_images.picture_look(path, str(ref.get("title") or line))
        except (OSError, ValueError):
            look = {"checked": False}
        if not look.get("checked"):
            unfit.append("unknown")
            notes.append(f"{path.name}: the text and watermark look could not run")
            continue
        if look.get("burned_in_text") or look.get("edge_object"):
            unfit.append("screen")
            notes.append(f"{path.name}: " + ("text or a watermark burned in" if look.get("burned_in_text")
                                             else "cluttered by an intruding object"))
            continue
        if ref.get("source") != "topic_photo":
            report.setdefault("reference_credits", []).append(image_gen.reference_credit(ref))
        ctx.setdefault("references", {})[k] = {"refs": refs, "why": why, "unfit": unfit, "notes": notes, "shown": str(path)}
        return path
    ctx.setdefault("references", {})[k] = {"refs": refs, "why": why, "unfit": unfit, "notes": notes}
    if refs:
        report.setdefault("references_not_shown", []).append({"beat": k + 1, "why": notes})
    return None


def _reference_photos(pool: list[Path]) -> list[dict[str, Any]]:
    """The topic's approved photos (Topic or Kept by their own credits.json record, never a context shot, one
    found for a single line, or one never looked at) as reference candidates for image_gen.find_references, with
    their title, licence and source. Derived copies (crops, upscales) are read back to their original record."""
    from .image_review import tier
    out: list[dict[str, Any]] = []
    seen: set = set()
    for p in pool:
        p = Path(p)
        if is_video(p) or not p.is_file() or is_illustration(p) or _is_graphic(p):
            continue
        entry = _photo_entry(p)
        if not entry or tier(entry) not in ("topic", "kept"):
            continue
        pid = picture_id(p)
        if pid in seen:
            continue
        seen.add(pid)
        out.append({"path": str(p), "title": str(entry.get("title") or ""), "description": str(entry.get("description") or ""),
                    "license": str(entry.get("license") or ""), "license_url": str(entry.get("license_url") or ""),
                    "source_url": str(entry.get("source_url") or ""), "artist": str(entry.get("artist") or "")})
    return out


DETAIL_TRIES = 4   # strong photos tried for a detail crop per beat (each is a local crop, no call)


def _approved_strong(p: Path) -> bool:
    """One of the topic's strongest approved photos: a Topic or Kept photo by its own credit record (a photo with no
    record is not "approved" for a punch-in), with no intrusion the screen could not crop away."""
    from .beat_images import intrusion_noted
    from .image_review import tier
    entry = _photo_entry(p)
    return bool(entry) and tier(entry) in ("topic", "kept") and not intrusion_noted(p)


def _clip_words(p: Path) -> set:
    """What a footage clip's record says it shows, as words (its title, description and place)."""
    try:
        meta = json.loads(Path(p).with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    meta = meta if isinstance(meta, dict) else {}
    return graphic_match.tokens(" ".join(str(meta.get(k) or "") for k in ("title", "description", "place", "query")))


LOCATE_TRIES = 2   # approved photos the named feature is looked for in, per beat (two vision calls each)


def _subject_terms(line: str, topic: dict[str, Any] | None) -> list[str]:
    from .image_gen import subject_terms
    return subject_terms(line, topic)


def _crop_allowed(crop_regions: dict[str, list], photo: Path) -> bool:
    """A Reel takes at most DETAIL_CROPS_PER_PHOTO crops of one photograph (its plan gate, reel_photo_coverage)."""
    if config.VIDEO_WIDTH >= config.VIDEO_HEIGHT:
        return True
    return len(crop_regions.get(picture_id(photo), [])) < DETAIL_CROPS_PER_PHOTO


def _crop_hold_ok(out: list[Path], durations: list[float], k: int, photo: Path) -> bool:
    """Whether a crop of `photo` on beat k (0-based) keeps that photograph within config.REEL_PHOTO_HOLD_MAX on
    screen: the gate (reel_photo_coverage) joins a photo and its crops on consecutive beats into one hold, so a crop
    beside its own photograph (beat k-1 or k+1, the photo itself or another crop of it) is planned only while the
    joined hold stays within the limit (QF-58b: a beat-2 crop of the opening photo made a 9 s hold)."""
    if config.VIDEO_WIDTH >= config.VIDEO_HEIGHT:
        return True
    source = picture_id(photo)

    def seconds(i: int) -> float:
        return float(durations[i]) if i < len(durations) else MAX_BEAT

    total, lo, hi = seconds(k), k - 1, k + 1
    while lo >= 0 and _shot_key(Path(out[lo])) == source:
        total, lo = total + seconds(lo), lo - 1
    while hi < len(out) and _shot_key(Path(out[hi])) == source:
        total, hi = total + seconds(hi), hi + 1
    return (lo == k - 1 and hi == k + 1) or total <= config.REEL_PHOTO_HOLD_MAX + 1e-6


def _cascade_fill(k: int, line: str, out: list[Path], pool: list[Path], durations: list[float],
                  crop_regions: dict[str, list], work_dir: Path | None, stopped: str,
                  only: tuple[str, ...] | None = None, subject: str = "") -> tuple[str, Path] | None:
    """The cascade's middle tiers for beat k (0-based) whose line no photo shows (src/picture_coverage.py), in
    order: a detail crop of one of the topic's strongest approved photos on a region not yet shown (unused photos
    first; in a Reel never past the hold limit beside its own photograph, _crop_hold_ok), archive film whose record
    names what the line names (within FOOTAGE_MAX_SHARE), the locator map when the line names its place (within the
    graphics caps). `only`: just these tiers; "located_crop" (a named subject's): a crop around the named feature
    `subject`, located and confirmed by two vision checks (picture_coverage.located_crop), never once the checks
    stopped. (tier, asset) or None; no model call but the located crop's."""
    from . import picture_coverage as pc
    in_use = {picture_id(p) for p in out}
    tiers = set(only) if only is not None else {pc.DETAIL_CROP, pc.ARCHIVE_FILM, pc.MAP}
    if work_dir is not None and "located_crop" in tiers and subject and not stopped:
        strong = [Path(p) for p in pool if not is_video(Path(p)) and Path(p).is_file()
                  and not pc.is_detail_crop(Path(p)) and _approved_strong(Path(p))
                  and _crop_allowed(crop_regions, Path(p)) and _crop_hold_ok(out, durations, k, Path(p))]
        strong.sort(key=lambda p: (picture_id(p) in in_use, len(crop_regions.get(picture_id(p), []))))
        aspect = config.VIDEO_WIDTH / config.VIDEO_HEIGHT
        for src in strong[:LOCATE_TRIES]:
            pid = picture_id(src)
            got = pc.located_crop(src, Path(work_dir), crop_regions.get(pid, []), aspect, subject, line,
                                  min_width=int(getattr(config, "DETAIL_CROP_MIN_WIDTH", 600)))
            if got is not None:
                crop_regions.setdefault(pid, []).append(got[1])
                return pc.DETAIL_CROP, got[0]
    if work_dir is not None and pc.DETAIL_CROP in tiers:
        strong = [Path(p) for p in pool if not is_video(Path(p)) and Path(p).is_file()
                  and not pc.is_detail_crop(Path(p)) and _approved_strong(Path(p))
                  and _crop_allowed(crop_regions, Path(p)) and _crop_hold_ok(out, durations, k, Path(p))]
        strong.sort(key=lambda p: (picture_id(p) in in_use, len(crop_regions.get(picture_id(p), []))))
        aspect = config.VIDEO_WIDTH / config.VIDEO_HEIGHT
        for src in strong[:DETAIL_TRIES]:
            pid = picture_id(src)
            got = pc.detail_crop(src, Path(work_dir), crop_regions.get(pid, []), aspect,
                                 min_width=int(getattr(config, "DETAIL_CROP_MIN_WIDTH", 600)))
            if got is not None:
                crop_regions.setdefault(pid, []).append(got[1])
                return pc.DETAIL_CROP, got[0]
    words = subject_words(line) if pc.ARCHIVE_FILM in tiers else set()
    if words:
        for clip in pool:
            c = Path(clip)
            if not (c.stem.startswith("footage_") and is_video(c)) or picture_id(c) in in_use:
                continue
            if not words & _clip_words(c):
                continue
            trial = list(out)
            trial[k] = c
            if _footage_share(trial, durations) <= FOOTAGE_MAX_SHARE:
                return pc.ARCHIVE_FILM, c
    for g in (pool if pc.MAP in tiers else []):
        gp = Path(g)
        if not _is_map(gp) or picture_id(gp) in in_use or graphic_match.score(gp, line) < graphic_match.THRESHOLD:
            continue
        trial = list(out)
        trial[k] = gp
        if _graphics_count(trial) <= MAX_GRAPHICS and _graphics_share(trial, durations) <= GRAPHICS_MAX_SHARE:
            return pc.MAP, gp
    return None


def _digest(p: Path) -> str:
    from .credits import sha256_file
    return sha256_file(p)


# A related shot (operator, 2026-10-29: "never an unrelated photo"): when no photo shows a line, one whose recorded
# descriptions name what the line names is the next best, judged from what is already known about each photo. The
# words compared are the line's own (lightly singularized), less those that name nothing to see.
_RELATED_STOP = graphic_match.STOPWORDS | {
    "also", "always", "among", "another", "away", "back", "began", "came", "come", "does", "done", "even", "ever",
    "find", "first", "found", "full", "gave", "give", "going", "gone", "half", "held", "itself", "keep", "kept", "known",
    "last", "later", "long", "made", "make", "might", "must", "never", "next", "once", "onto", "own", "people",
    "perhaps", "seen", "shall", "should", "show", "shown", "shows", "since", "something", "still", "take", "taken",
    "tell", "than", "thing", "things", "today", "told", "took", "toward", "towards", "upon", "used", "using", "want",
    "well", "whole", "will", "within", "without", "work", "world", "yet", "hundred", "thousand", "million", "thousands",
    "hundreds", "millions", "century", "centuries", "photo", "photograph", "picture", "image", "shot", "view", "close",
    "individual", "entire", "single", "several", "certain", "great", "small", "large", "huge", "tiny", "real",
    "true", "whose", "they're", "there's", "it's", "people's"}


def subject_words(text: str) -> set:
    """What a line names to see, as words: its own words of four letters or more, lightly singularized, less the
    ones that name nothing in particular (graphic_match.STOPWORDS and _RELATED_STOP); no numbers."""
    return {t for t in graphic_match.tokens(text) if len(t) > 3 and t[0].isalpha() and t not in _RELATED_STOP}


def _known_text(p: Path, cache: Optional[Dict[str, Any]] = None) -> str:
    """Everything already recorded about what a photo shows: its archive caption, what the photo review saw, what the
    look that kept it saw (a photo found for a line), and the reasons of the looks that judged it right for a line
    (beat_images' fit cache, by file name). No call."""
    entry = _photo_entry(p) or {}
    parts = [_photo_caption(p), str((entry.get("vision_review") or {}).get("reason") or ""), str(entry.get("looked") or "")]
    names = set(photo_names(p))   # a crop saved as PNG keeps its original's fit reasons (QF-13)
    for hit in (cache or {}).values():
        if isinstance(hit, dict) and hit.get("fits") and hit.get("file") in names:
            parts.append(str(hit.get("reason") or ""))
    return " ".join(x for x in parts if x)


def _judged_wrong(p: Path, line: str, cache: Optional[Dict[str, Any]]) -> bool:
    """Whether a cached look already found this photo wrong for this very line."""
    from . import beat_images
    try:
        hit = (cache or {}).get(beat_images.fit_key(_digest(p), line))
    except OSError:
        return False
    return isinstance(hit, dict) and hit.get("fits") is False


def related_shot(line: str, candidates: List[Path], cache: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """The candidate still whose recorded descriptions name the most of what `line` names (subject_words), at least
    one word; a photo a cached look found wrong for this line is never one. None when none names anything the line
    does. Ties keep the candidates' order (the planner's pool order: the topic's own photos first)."""
    words = subject_words(line)
    if not words:
        return None
    best, score = None, 0
    for cand in candidates:
        if is_video(Path(cand)):
            continue
        named = len(words & graphic_match.tokens(_known_text(Path(cand), cache)))
        if named > score and not _judged_wrong(Path(cand), line, cache):
            best, score = Path(cand), named
    return best


def related_before(k: int, out: List[Path], fits_at: Dict[int, bool], report: Dict[str, Any]) -> bool:
    """Whether the picture before beat k (0-based) is a related one to hold on: the opening shot (the topic's
    strongest picture), one judged right for its own line, or one chosen as related or held on for a related one."""
    if k - 1 == 0 or fits_at.get(k - 1):
        return True
    return any(r.get("beat") == k and Path(str(r.get("to"))).name == Path(out[k - 1]).name
               for r in (report.get("related") or []) + (report.get("held") or []))


def _held_run(out: List[Path], durations: List[float], k: int) -> float:
    """Seconds the picture before beat k (0-based) would stay on screen if beat k held it too."""
    total = durations[k] if k < len(durations) else MAX_BEAT
    j = k - 1
    while j >= 0 and picture_id(out[j]) == picture_id(out[k - 1]):
        total += durations[j] if j < len(durations) else MAX_BEAT
        j -= 1
    return total


NO_PICTURE_ISSUE = "no picture shows: "


def no_picture_issues(fit: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The review card's issue rows (info: shown, never a block) for the lines no picture shows, one per line, as
    "no picture shows: <line>" (operator, 2026-10-29), from the fit check's report (payload["picture_fit"])."""
    rows, seen = [], set()
    for u in (fit or {}).get("unshown") or []:
        line = " ".join(str(u.get("line") or "").split())
        if not line or line in seen:
            continue
        seen.add(line)
        how = {"held": "the previous related shot is held", "held_over": "the previous related shot is held",
               "graphic": "one of our graphics is shown", "related": "a related photo of what it names is shown",
               "detail_crop": "a detail of one of the topic's photos is shown", "archive_film": "archive film is shown",
               "map": "the locator map is shown", "generated_art": "a labelled reconstruction is shown"}.get(
            u.get("fallback"), "its planned photo stayed")
        rows.append({"type": "picture_line", "card": "Pictures", "severity": "info", "beat": u.get("beat"),
                     "line": line, "issue": f"{NO_PICTURE_ISSUE}{line} (beat {u.get('beat', '?')}: {how})"})
    return rows


def clip(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def fit_note(report: Dict[str, Any]) -> str:
    """The review card's line for the beats no picture shows ("" when every judged beat has one)."""
    unshown = (report or {}).get("unshown") or []
    if not unshown:
        return ""
    said = []
    for u in unshown[:FIT_NOTE_MAX_BEATS]:
        how = {"held": "the previous shot is repeated, reframed", "held_over": "the previous shot is held over it",
               "graphic": "one of our graphics is shown instead",
               "related": f"a related photo of what it names is shown instead ({u.get('related', 'a photo')})",
               "detail_crop": f"a detail of one of the topic's photos is shown instead ({u.get('filled_by', 'a crop')})",
               "archive_film": f"archive film is shown instead ({u.get('filled_by', 'a clip')})",
               "map": "the locator map is shown instead",
               "generated_art": "a labelled illustration is shown instead"}.get(
            u.get("fallback"), f"{u.get('picture', 'its photo')} kept")
        said.append(f"beat {u['beat']}: “{clip(u.get('line', ''), 70)}”: no licensed photo shows it ({how})")
    more = len(unshown) - FIT_NOTE_MAX_BEATS
    return (f"{FIT_NOTE_LABEL}: " + "; ".join(said) + (f"; and {more} more" if more > 0 else "")
            + ". Reword or drop the line(s) with Regenerate, or add a photo that shows it.")


def fit_findings(report: Dict[str, Any]) -> List[str]:
    """One finding text per beat no picture shows, for improve.note (class video.picture_relevance)."""
    def how(u):
        if u.get("fallback") in ("held", "held_over"):
            return "previous shot held"
        if u.get("fallback") == "related":
            return f"related photo {u.get('related', '')} shown"
        if u.get("fallback") == "generated_art":
            return f"labelled illustration {u.get('filled_by', '')} shown"
        return u.get("picture", "") + " kept"
    return [f"beat {u['beat']}: '{clip(u.get('line', ''), 120)}': no licensed photo shows it "
            f"({how(u)}; {clip(u.get('reason', ''), 160)})" for u in (report or {}).get("unshown") or []]


def no_repeats(chosen: List[Path], stills: List[Path], reserve: Optional[List[Path]] = None,
               pinned: Optional[Dict[int, Path]] = None) -> List[Path]:
    """No picture is shown twice while the pool still has unused photos: a repeat reads as padding.
    The swaps above take stills[k % n], which can be a photo already on another beat. Later copies
    get the pool's next unused photo (the pool is ordered best-first), then the `reserve`'s (context shots
    beyond the usual cap: Nazca showed four photos twice while its fifth context photo was never used,
    2026-10-28); with fewer photos than beats, repeats stay (hold_for_short_pool holds them instead). A picture
    is what its lineage starts from (picture_id): a cropped copy and its original are one picture.
    `pinned`: {0-based beat index: Path} beats verified as passing in a previous pass, locked against swapping."""
    pid = _id_memo()
    placed = {pid(p) for p in chosen}
    unused: List[Path] = []
    for s in list(stills) + list(reserve or []):
        if pid(s) not in placed:
            placed.add(pid(s))   # each picture once in the unused list too
            unused.append(s)
    seen = set()
    out = []
    for k, p in enumerate(chosen):
        if pinned and k in pinned:
            p = pinned[k]
        elif pid(p) in seen and unused:
            p = unused.pop(0)
        seen.add(pid(p))
        out.append(p)
    if pinned:
        for k, p in pinned.items():
            if 0 <= k < len(out):
                out[k] = p
    return out


def _id_memo():
    """picture_id, looked up once per path within one pass (each lookup reads lineage sidecars)."""
    memo: Dict[str, str] = {}

    def pid(p: Path) -> str:
        key = str(p)
        if key not in memo:
            memo[key] = picture_id(Path(p))
        return memo[key]
    return pid


# ------------------------------------------------------------------ fewer pictures than beats
# Production Nazca, 2026-10-28: six stills for nine photo beats. The planner cycled photos (four shown twice) and
# the fit check's fallback held the previous shot as a second, reframed cut. A picture held over two lines reads
# as one shot; the same picture cut back in later reads as padding. So a repeated picture's beat may join a
# neighbouring photo beat, at the beat boundaries the narration already has (cuts stay on its pauses), never past
# HELD_SHOT_MAX and never into the hook (its motion clip lasts six seconds). A line keeps a picture that was
# judged right for it (fit_to_lines): only a later copy, or a copy whose line no picture was found to show, may be
# joined to a neighbour, and only to one showing the same picture or one judged to show that line too (`check`).
# Otherwise the repeat stays, and the review card says so.
HELD_SHOT_MAX = config.REEL_PHOTO_HOLD_MAX


def _shot_kind(path: Path) -> str | None:
    """"photo", "detail" (a punch-in made by src/picture_coverage.detail_crop) or None (a graphic, a film clip or a
    labelled illustration: not a photo)."""
    path = Path(path)
    if is_video(path) or is_illustration(path) or path.stem.startswith("graphic_"):
        return None
    from .picture_coverage import is_detail_crop
    return "detail" if is_detail_crop(path) else "photo"


def _shot_key(path: Path) -> str | None:
    """The photograph a still shows (a detail crop by its lineage, as picture_id resolves it); None otherwise."""
    return None if _shot_kind(path) is None else picture_id(path)


# Words per narration beat, measured on the 61 real Reels of QF-58's evidence (6,125 words, 635 beats: 9.6); the
# estimate is within two beats of segment_beats on every one of them.
WORDS_PER_BEAT = 9.6


def estimated_photo_beats(narration: str, graphics: bool) -> int:
    """The photo beats a narration will have before its voice exists: its words over WORDS_PER_BEAT, less one beat
    for a graphic when the script asks for one (every real Reel placed at least one, at most MAX_GRAPHICS)."""
    words = len(str(narration or "").split())
    beats = round(words / WORDS_PER_BEAT) if words else 0
    return max(0, beats - (1 if graphics and beats else 0))


GRAPHIC_FIELDS = ("stat_cards", "cross_section", "route", "timeline", "scale_fact", "location")


def script_asks_for_graphics(payload: dict[str, Any]) -> bool:
    """Whether the checked script carries a field one of our graphics is drawn from (src/graphics.make_all; the
    locator map from its location)."""
    return any((payload or {}).get(field) for field in GRAPHIC_FIELDS)


def reel_photo_coverage(scenes: list[Path], durations: list[float]) -> dict[str, Any]:
    """The final plan gate, as the operator ruled it (decisions/QF-58-gate.md, Option A), after fitting, holds and
    the caption re-plan and before motion and the render.

    - Every photo beat shows a different photo (distinct == photo_beats): the same photograph on a second beat,
      next to the first or later, found by lineage, by its bytes or as a near duplicate (photo_pool), is a repeat.
      A picture held over two lines is one beat only when the hold joined them (hold_for_short_pool).
    - A Reel of photos only (no graphic, film or illustration) shows at least config.MIN_SCENE_IMAGES distinct
      photos; graphics, film and illustrations take beats of their own.
    - A detail crop (src/picture_coverage.py) counts toward neither side: it is never a distinct photo, so it
      cannot fill the floor or a photo beat; at most one crop of each photograph (DETAIL_CROPS_PER_PHOTO).
    - A photograph stays on screen at most config.REEL_PHOTO_HOLD_MAX seconds at a time: consecutive beats on one
      photograph, itself or a crop of it, are one hold."""
    import math

    from .photo_pool import duplicate_of, signature
    problems, invalid = [], []
    if len(scenes) != len(durations):
        problems.append("scene and duration counts differ")
    holds: list[dict[str, Any]] = []
    photos: list[tuple[int, str]] = []      # (beat, photograph) of each photo beat
    crops: dict[str, list[int]] = {}        # photograph -> beats of its crops
    previous, other = None, 0
    for index, (scene, duration) in enumerate(zip(scenes, durations)):
        seconds = float(duration) if isinstance(duration, (int, float)) else float("nan")
        if not math.isfinite(seconds) or seconds <= 0:
            invalid.append(index + 1)
            seconds = 0.0
        kind = _shot_kind(Path(scene))
        if kind is None:
            previous, other = None, other + 1
            continue
        source = picture_id(Path(scene))
        if kind == "photo":
            photos.append((index + 1, source))
        else:
            crops.setdefault(source, []).append(index + 1)
        if source == previous:
            holds[-1]["beats"].append(index + 1)
            holds[-1]["seconds"] += seconds
        else:
            holds.append({"beats": [index + 1], "seconds": seconds})
        previous = source
    signatures: dict[str, dict] = {}
    seen, digests, placed, repeats, unreadable = {}, {}, [], [], []
    for beat, source in photos:
        try:
            if source not in signatures:
                signatures[source] = signature(Path(source))
            fingerprint = signatures[source]
        except (OSError, ValueError):
            unreadable.append(beat)
            fingerprint = {}
        digest = fingerprint.get("source_sha256", "")
        first = seen.get(source) or (digests.get(digest) if digest else None) or \
            (duplicate_of(fingerprint, placed) or {}).get("beat")
        if first:
            repeats.append({"beat": beat, "first": first})
            continue
        seen[source] = beat
        if digest:
            digests[digest] = beat
        placed.append({**fingerprint, "beat": beat})
    distinct = len(photos) - len(repeats)
    crop_beats = sum(len(b) for b in crops.values())
    extra_crops = {s: b for s, b in crops.items() if len(b) > DETAIL_CROPS_PER_PHOTO}
    long_holds = [{"beats": h["beats"], "seconds": round(h["seconds"], 2)} for h in holds
                  if h["seconds"] > config.REEL_PHOTO_HOLD_MAX + 1e-6]
    if not other and distinct < config.MIN_SCENE_IMAGES:
        problems.append(f"only {distinct} distinct photos in a Reel of photos only; it needs {config.MIN_SCENE_IMAGES}"
                        + (f" (its {crop_beats} detail crop{'s' if crop_beats != 1 else ''} do not count)"
                           if crop_beats else ""))
    if repeats:
        problems.append("a photo shown again: " + ", ".join(f"beat {r['beat']} repeats beat {r['first']}" for r in repeats))
    if extra_crops:
        problems.append("more than one detail crop of a photo: " + ", ".join(
            "beats " + ", ".join(map(str, b)) for b in extra_crops.values()))
    if long_holds:
        problems.append(f"a photograph on screen over {config.REEL_PHOTO_HOLD_MAX:g} s: " + ", ".join(
            "beat" + ("s " + f"{h['beats'][0]}-{h['beats'][-1]}" if len(h["beats"]) > 1 else f" {h['beats'][0]}")
            + f" {h['seconds']:.1f} s" for h in long_holds))
    if invalid:
        problems.append("invalid beat durations: " + ", ".join(map(str, invalid)))
    if unreadable:
        problems.append("unreadable photo sources: " + ", ".join(map(str, unreadable)))
    return {"ok": not problems, "distinct": distinct, "photo_beats": len(photos), "detail_crops": crop_beats,
            "other_beats": other, "repeats": repeats, "long_holds": long_holds, "problems": problems}


DETAIL_CROPS_PER_PHOTO = 1  # at most one crop per photograph in a Reel: the round-6 scope (decisions/QF-58.md, item 1)


HOLD_CUT_MARGIN = 0.12  # a cut moved into a pause stays this far from the words on either side (seconds)


def fit_holds(beats: list[dict[str, Any]], scenes: list[Path], durations: list[float],
              words: list[dict[str, Any]], limit: float | None = None
              ) -> tuple[list[dict[str, Any]], list[float], list[dict[str, Any]]]:
    """A narration beat runs until the next one starts, so a pause can keep a still on screen past the hold limit
    (production sample: 3 of 341 Reel beats, up to 6.32 s). Such a still's cut moves into that pause: the cut after
    it earlier, else the cut before it later, only while every word stays on its own shot at least HOLD_CUT_MARGIN
    from the cut and a still on the other side stays within the limit. Cuts still land between words. Returns
    (beats, durations, moves); a shot whose words alone run past the limit is left for reel_photo_coverage."""
    limit = config.REEL_PHOTO_HOLD_MAX if limit is None else limit
    n = len(scenes)
    if not words or len(beats) != n or len(durations) != n:
        return beats, durations, []
    beats, durations = [dict(b) for b in beats], [float(d) for d in durations]
    keys = [_shot_key(Path(s)) for s in scenes]

    def run(k: int) -> list[int]:
        lo = hi = k
        while lo > 0 and keys[lo - 1] == keys[k]:
            lo -= 1
        while hi < n - 1 and keys[hi + 1] == keys[k]:
            hi += 1
        return list(range(lo, hi + 1))

    def seconds(k: int) -> float:
        return sum(durations[i] for i in run(k)) if keys[k] is not None else 0.0

    def spoken(k: int) -> tuple[float, float] | None:
        start, end = float(beats[k].get("start", 0.0)), float(beats[k].get("end", 0.0))
        inside = [w for w in words if start - 1e-6 <= float(w["start"]) < end - 1e-6]
        if not inside:
            return None
        return float(inside[0]["start"]), max(float(w["start"]) + float(w.get("dur", 0.0)) for w in inside)

    moves, k = [], 0
    while k < n:
        if keys[k] is None:
            k += 1
            continue
        shot = run(k)
        a, b = shot[0], shot[-1]
        excess = seconds(k) - limit
        if excess > 1e-6:
            said_end, said_start = spoken(b), spoken(a)
            if (b + 1 < n and said_end and float(beats[b]["end"]) - excess >= said_end[1] + HOLD_CUT_MARGIN
                    and seconds(b + 1) + excess <= limit + 1e-6 and durations[b] - excess > 0):
                durations[b] -= excess
                durations[b + 1] += excess
                beats[b]["end"] = float(beats[b]["end"]) - excess
                beats[b + 1]["start"] = float(beats[b + 1]["start"]) - excess
                moves.append({"beat": b + 1, "cut": "earlier", "seconds": round(excess, 3)})
            elif (a > 0 and said_start and float(beats[a]["start"]) + excess <= said_start[0] - HOLD_CUT_MARGIN
                  and seconds(a - 1) + excess <= limit + 1e-6 and durations[a] - excess > 0):
                durations[a] -= excess
                durations[a - 1] += excess
                beats[a]["start"] = float(beats[a]["start"]) + excess
                beats[a - 1]["end"] = float(beats[a - 1]["end"]) + excess
                moves.append({"beat": a + 1, "cut": "later", "seconds": round(excess, 3)})
        k = b + 1
    return beats, durations, moves


def still_shortfall(n_beats: int, media: List[Path]) -> int:
    """How many beats the distinct stills in `media` cannot cover, as if every beat took a photo: the reserve made
    from it is used only where a picture would otherwise repeat, so an estimate on the high side costs nothing."""
    return max(0, n_beats - len({picture_id(p) for p in media if not is_video(Path(p))}))


def hold_for_short_pool(beats: List[Dict[str, Any]], scenes: List[Path], durations: List[float],
                        fits: Optional[Dict[int, bool]] = None, check=None, available: Optional[int] = None,
                        merge: bool = True
                        ) -> Tuple[List[Dict[str, Any]], List[Path], List[float], List[int], Dict[str, Any]]:
    """(beats, scenes, durations, where each old beat went (old index -> new index), report) with repeated stills'
    beats joined to neighbouring photo beats where the rules above allow (see above); nothing changes when no picture
    repeats. `fits`: 0-based beat -> whether fit_to_lines judged its picture right for its line (absent: not judged).
    `check(picture, line)`: True/False/None (could not look), asked before a line takes a neighbour's different
    picture; without it, only a neighbour showing the same picture is joined. `available`: the distinct stills the
    plan could choose from (with the reserve), which says whether the pool was short. report: "merged" (1-based new
    beats carrying two or more lines), "shown" (1-based old beats whose line now shows a picture judged right for
    it), "repeats" ({"beat", "first", "picture"}, new numbering), "distinct", "still_beats", "short". Nothing is
    joined when the pool is not short (the fit check's held shot stays a reframed cut, as before) or with
    merge=False (an approved storyboard: its beats are the operator's; the repeats are only reported)."""
    lines = [str(b.get("text") or "") for b in beats]
    beats, scenes, durations = [dict(b) for b in beats], [Path(p) for p in scenes], list(durations)
    n = min(len(beats), len(scenes), len(durations))
    beats, scenes, durations = beats[:n], scenes[:n], durations[:n]
    groups = [[i] for i in range(n)]
    status: Dict[int, Optional[bool]] = {i: (fits or {}).get(i) for i in range(n)}   # old beat -> its line is shown
    shown: List[int] = []
    asked: Dict[Tuple[str, str], Optional[bool]] = {}

    pid = _id_memo()

    def ids() -> List[Optional[str]]:
        return [None if is_video(p) else pid(p) for p in scenes]

    still_beats = sum(1 for i in ids() if i is not None)
    distinct = len({i for i in ids() if i is not None})
    short = (available if available is not None else distinct) < still_beats

    def shows(picture: Path, old: int) -> bool:
        if check is None or not lines[old].strip():
            return False
        key = (pid(picture), lines[old])
        if key not in asked:
            try:
                asked[key] = check(picture, lines[old])
            except JobCancelled:
                raise
            except Exception as e:  # a check that fails keeps the repeat
                log.info("Held-shot check for %s could not run (%s).", Path(picture).name, e)
                asked[key] = None
        return asked[key] is True

    while short and merge:
        now = ids()
        options = []
        for k in range(len(scenes) - 1, 0, -1):   # the hook (0) is never joined
            if now[k] is None or now.count(now[k]) < 2:
                continue
            first = now.index(now[k]) == k
            if first and not any(status[o] is False for o in groups[k]):
                continue   # the first copy keeps the line it was judged right for (or not judged against)
            for j in (k - 1, k + 1):
                if 1 <= j < len(scenes) and now[j] is not None and durations[j] + durations[k] <= HELD_SHOT_MAX:
                    options.append((0 if now[j] == now[k] else 1, durations[j] + durations[k], -k, j))
        step = None
        for same, _, negk, j in sorted(options):
            k = -negk
            if same == 0 or all(shows(scenes[j], o) for o in groups[k]):
                step = (k, j, same == 0)
                break
        if step is None:
            break
        k, j, same = step
        if not same:
            for o in groups[k]:
                status[o] = True
                if o + 1 not in shown:
                    shown.append(o + 1)
        lo, hi = sorted((k, j))
        beats[lo] = {**beats[lo], "end": beats[hi].get("end", beats[lo].get("end")),
                     "text": " ".join(t for t in (beats[lo].get("text", ""), beats[hi].get("text", "")) if t)}
        scenes[lo], durations[lo], groups[lo] = scenes[j], durations[lo] + durations[hi], groups[lo] + groups[hi]
        del beats[hi], scenes[hi], durations[hi], groups[hi]
    where = [0] * n
    for new, group in enumerate(groups):
        for old in group:
            where[old] = new
    first_at: Dict[str, int] = {}
    repeats = []
    for k, p in enumerate(scenes):
        if is_video(p):
            continue
        here = pid(p)
        if here in first_at:
            repeats.append({"beat": k + 1, "first": first_at[here] + 1, "picture": p.name})
        first_at.setdefault(here, k)
    report = {"merged": [k + 1 for k, g in enumerate(groups) if len(g) > 1], "shown": sorted(set(shown)),
              "repeats": repeats, "distinct": distinct, "still_beats": still_beats, "short": short,
              "storyboard": not merge}
    if report["merged"] or repeats:
        log.info("Pictures: %d distinct for %d photo beats; %d beat(s) now hold one picture over two or more lines, "
                 "%d repeat(s) left.", distinct, still_beats, len(report["merged"]), len(repeats))
    return beats, scenes, durations, where, report


def withdrawals(planned: List[Path], wrong_beats: List[int], candidates: List[Path], floor: int = 3) -> set:
    """The stills of `planned` to stop offering for a re-plan, so it cannot move them to another line (Nazca,
    2026-10-25): those on `wrong_beats` (1-based; the video check found they show the wrong thing,
    beat_images.wrong_subject_beats: never a finding that a picture repeats, which withdrew a right photo from a
    short pool on 2026-10-28), with every copy of the same picture, while at least `floor` distinct stills stay in
    `candidates` (counted after the gap search has added its photos). Empty when nothing may go."""
    flagged = {planned[b - 1] for b in wrong_beats if 0 < b <= len(planned)
               and not is_video(Path(planned[b - 1])) and not _is_graphic(Path(planned[b - 1]))}
    if not flagged:
        return set()
    gone = {picture_id(f) for f in flagged}
    left = {picture_id(c) for c in candidates if not is_video(Path(c)) and picture_id(c) not in gone}
    if len(left) < floor:
        log.info("Not withdrawing %s: only %d distinct still(s) would be left.",
                 ", ".join(sorted(Path(f).name for f in flagged)), len(left))
        return set()
    return {c for c in candidates if picture_id(c) in gone}


def original_beats(numbers: List[int], where: List[int]) -> Dict[int, List[int]]:
    """1-based beats after hold_for_short_pool -> the 1-based original beats each one carries."""
    out: Dict[int, List[int]] = {}
    for old, new in enumerate(where):
        if new + 1 in numbers:
            out.setdefault(new + 1, []).append(old + 1)
    return out


# A beat number, not a count after it ("beat 3 and 4 seconds later", "beat 4 - 5 people"): a number followed by a
# unit or a noun of quantity is left alone, and only an unspaced dash or "to" makes a range.
_NOT_A_COUNT = (r"(?!\d)(?!\s*(?:%|(?:seconds?|secs?|s|minutes?|mins?|hours?|days?|weeks?|months?|years?|decades?"
                r"|centuries|people|persons?|men|women|children|visitors|workers|times|metres?|meters?|m|km|cm|mm"
                r"|feet|foot|ft|miles?|percent|tons?|tonnes?|kg)\b))")
_BEAT_ITEM = rf"\d+{_NOT_A_COUNT}(?:(?:[-–]|\s+to\s+)\d+{_NOT_A_COUNT})?"
_BEAT_LIST = re.compile(rf"\b([Bb]eats?\s+)({_BEAT_ITEM}(?:(?:\s*[,&]\s*|\s+(?:and|or)\s+){_BEAT_ITEM})*)")


def renumber_feedback(text: str, where: List[int]) -> str:
    """Video-check feedback, numbered by the beats the video had, in the narration's own beats (the re-plan works
    on those): "Beat 4" of a video whose beat 4 carried lines 4 and 5 becomes "Beat 4+5"; every number of a list
    ("Beats 4 and 5", "beats 2, 3") is mapped, and a range ("beats 2-3") runs from its first beat's first line to
    its last beat's last line. A count after a beat number ("4 seconds", "5 people") is not a beat."""
    groups = original_beats(list(range(1, max(where, default=-1) + 2)), where)

    def item(m: "re.Match") -> str:
        lo = groups.get(int(m.group(1)))
        if m.group(3) is None:
            return "+".join(map(str, lo)) if lo else m.group(0)
        hi = groups.get(int(m.group(3)))
        return f"{lo[0] if lo else m.group(1)}{m.group(2)}{hi[-1] if hi else m.group(3)}"

    def numbers(m: "re.Match") -> str:
        return m.group(1) + re.sub(r"(\d+)(?:([-–]|\s+to\s+)(\d+))?", item, m.group(2))
    return _BEAT_LIST.sub(numbers, text or "")


def storyboard_opening_note(notes: str, scenes: List[Path]) -> str:
    """The card's warning when an approved storyboard opens on a still that is not a Topic or Kept photo (the
    operator's choice stands; it is not animated either), else ""."""
    if notes != STORYBOARD_NOTES or not scenes or is_video(Path(scenes[0])) or strong_shot(Path(scenes[0])):
        return ""
    return (f"⚠️ Opening: the approved storyboard opens on {Path(scenes[0]).name}, which is not one of the topic's "
            "own photos (a context shot): it stays as approved, without AI motion. Edit the storyboard to open on "
            "the subject itself.")


HOLD_NOTE_LABEL = "🔁 Pictures"


def hold_note(report: Dict[str, Any], reserve_used: Optional[List[str]] = None,
              fit_report: Optional[Dict[str, Any]] = None) -> str:
    """The review card's line when the pool had fewer pictures than photo beats ("" otherwise). A beat the fit
    check's line already names as held over (fit_note, `fit_report`) is not said again here."""
    report = report or {}
    said_there = {u.get("beat") for u in (fit_report or {}).get("unshown") or [] if u.get("fallback") == "held_over"}
    merged = [m for m in report.get("merged") or [] if m not in said_there]
    repeats = report.get("repeats") or []
    if not (report.get("short") or reserve_used) or not (merged or repeats or reserve_used):
        return ""
    said = []
    if reserve_used:
        said.append(f"{len(reserve_used)} context photo(s) beyond the usual cap used rather than repeat a picture "
                    f"({', '.join(reserve_used)})")
    if merged:
        said.append(("beats " if len(merged) > 1 else "beat ") + ", ".join(map(str, merged))
                    + (" each hold" if len(merged) > 1 else " holds") + " one picture over two or more lines")
    if repeats:
        said.append("; ".join(f"beat {r['beat']} repeats beat {r['first']}'s picture ({r['picture']})"
                              for r in repeats[:FIT_NOTE_MAX_BEATS])
                    + (f"; and {len(repeats) - FIT_NOTE_MAX_BEATS} more" if len(repeats) > FIT_NOTE_MAX_BEATS else ""))
    head = f"{HOLD_NOTE_LABEL}: {report.get('distinct', '?')} distinct for {report.get('still_beats', '?')} photo beats"
    tail = (" (the approved storyboard is kept as it is: edit it, or add photos)" if report.get("storyboard") and repeats
            else "") + ". Add photos of the topic for a picture per line."
    return head + ": " + "; ".join(said) + tail


def remap_beats(report: Dict[str, Any], where: List[int], scenes: Optional[List[Path]] = None,
                shown: list[int] | None = None,
                keys=("swapped", "graphic", "held", "related", "unshown", "tiers")) -> dict[str, Any]:
    """A fit_to_lines report in the beat numbering after hold_for_short_pool (1-based old -> new). A line that now
    shows a picture judged right for it (`shown`, old numbering) leaves "unshown"; a row names the picture its beat
    now has (`scenes`, after the hold); a held line joined to the shot it held is said as held over, not repeated."""
    out = dict(report or {})
    shown = set(shown or [])
    for key in keys:
        rows = []
        for row in out.get(key) or []:
            row = dict(row)
            old = int(row.get("beat", 0)) - 1
            if key == "unshown" and old + 1 in shown:
                continue
            if 0 <= old < len(where):
                if row.get("fallback") == "held" and old > 0 and where[old] == where[old - 1]:
                    row["fallback"] = "held_over"
                row["beat"] = where[old] + 1
                if key == "unshown" and scenes is not None and where[old] < len(scenes):
                    row["picture"] = Path(scenes[where[old]]).name
            rows.append(row)
        out[key] = rows
    if out.get("fits"):
        out["fits"] = sorted({where[b - 1] + 1 for b in out["fits"] if 0 < b <= len(where)})
    return out
