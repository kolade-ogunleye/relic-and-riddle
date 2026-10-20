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
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import commons, free_llm, graphic_match
from .config import FOOTAGE_MAX_SHARE
from .llm import LLMError, claude_available, image_block, structured_call, task as llm_task
from .models import ScenePlan
from .typography import is_video

log = logging.getLogger(__name__)

MIN_BEAT, TARGET_BEAT, MAX_BEAT = 2.5, 3.2, 4.5

SYSTEM = """You are a documentary film editor. Choose the best picture for each line of narration in a short vertical history video. Prefer images that literally show what the line describes; for abstract lines, pick an atmospheric image that fits. Use each image at most once where possible, start strong (the first beat is the hook), and avoid placing two near-identical images back to back. Never pick an image that contradicts the narration; real footage labelled GENERIC must never stand in for the named place. A photo's label carries its archive caption (title, description, date): use it to know what and where the photo shows, and never place a photo whose caption names a different site, object or era where the narration names this one.
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


def _photo_caption(p: Path) -> str:
    """The archive caption of a sourced photo, from the credits.json beside it ("" if none)."""
    entry = next((e for e in _folder_credits(str(Path(p).parent)) if e.get("file") == Path(p).name), None)
    if not entry:
        return ""
    crowded = (entry.get("vision_review") or {}).get("people_prominent")
    return commons.caption(entry) + ("; prominent visitors, faces blurred: avoid for the hook" if crowded else "")


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
    Beat 0 is the hook and keeps its photo or video."""
    pairs = sorted(((graphic_match.score(g, b.get("text", "")), idx, gi)
                    for gi, g in enumerate(graphics) if Path(g).stem.startswith("graphic_")
                    for idx, b in enumerate(beats) if idx > 0), reverse=True)
    locked: Dict[int, Path] = {}
    placed: set = set()
    for s, idx, gi in pairs:
        if s < graphic_match.THRESHOLD:
            break
        if idx not in locked and gi not in placed:
            locked[idx] = graphics[gi]
            placed.add(gi)
    return locked


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


def storyboard_order(storyboard: List[Dict[str, Any]], beats: List[Dict[str, Any]], options: List[Path]) -> Optional[List[int]]:
    """Option index per beat from the approved storyboard, matched by what each beat says (the
    storyboard's lines are sentences; the planner's beats are timed segments, and a revision can
    reorder them). Beats sharing no words get unused options in order. None if it doesn't fit."""
    by_name = {p.name: i for i, p in enumerate(options)}
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


@llm_task("scene_plan")
def plan_scenes(beats: List[Dict[str, Any]], media: List[Path], feedback: str = "",
                use_storyboard: bool = True) -> Tuple[List[Path], List[float], str]:
    """Returns (media per beat, duration per beat, notes). A motion hook clip stays first.
    `feedback` lists problems a reviewer found with a previous plan, so they are avoided.
    `use_storyboard`: honour an approved Reel storyboard (its beats are numbered for a Reel; a
    documentary's many cuts are planned from the narration instead)."""
    durations = [b["end"] - b["start"] for b in beats]
    hook = media[0] if media and is_video(media[0]) and not _is_graphic(media[0]) else None
    stills = [p for p in media if not is_video(p)]
    graphics = [p for p in media if _is_graphic(p)]
    options = stills + graphics  # what the planner chooses from (graphics shown by their poster frame)
    n = len(beats)

    order = list(range(n))  # default: filename order of the photos, cycling if there are more beats than photos
    notes = "filename order"

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
            content: List[dict] = []
            for i, p in enumerate(options, 1):
                if p in graphics:
                    content += [{"type": "text", "text": f"Image {i} ({_clip_label(p)}):"}, image_block(p.with_suffix(".jpg"))]
                else:
                    cap = _photo_caption(p)
                    content += [{"type": "text", "text": f"Image {i}" + (f" ({cap}):" if cap else ":")}, image_block(p)]
            lines = "\n".join(f"Beat {i}: {b['text']}" for i, b in enumerate(beats, 1))
            fix = f"\n\nA reviewer found these problems with the previous choice; fix them:\n{feedback}" if feedback else ""
            content.append({"type": "text", "text": (
                f"Narration beats ({n}):\n{lines}{fix}\n\n"
                f"Return exactly {n} image numbers (1-{len(options)}), one per beat, in order.")})
            plan = (free_llm.call(SYSTEM, content, ScenePlan, effort="low", max_tokens=4000)
                    if free_llm.enabled() else structured_call(SYSTEM, content, ScenePlan, effort="low", max_tokens=4000))
            picks = [i - 1 for i in plan.images]
            if len(picks) == n and all(0 <= i < len(options) for i in picks):
                order, notes = picks, plan.notes
            else:
                log.warning("Scene plan had %d picks for %d beats or bad numbers; using filename order.", len(picks), n)
        except LLMError as e:
            log.warning("Scene planning failed (%s); using filename order.", e)

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
    locked = lock_narrative_graphics(beats, graphics)
    for idx, g in locked.items():
        if idx < len(chosen):
            chosen[idx] = g
    # A locked graphic appears only on its beat: any other copy (the planner's own pick) becomes a photo.
    if stills:
        for k, p in enumerate(chosen):
            if p in locked.values() and locked.get(k) != p:
                chosen[k] = stills[k % len(stills)]

    # A graphic the planner placed where the narration doesn't say what it shows becomes a photo.
    if stills:
        for k in range(len(chosen)):
            if k not in locked and k < len(beats):
                chosen[k] = keep_if_it_matches(Path(chosen[k]), beats[k].get("text", ""), stills[k % len(stills)])

    # Footage supports the narration, never dominates it (Meta's originality rules): over the
    # cap, the latest footage picks become photos.
    k = len(chosen) - 1
    while _footage_share(chosen, durations) > FOOTAGE_MAX_SHARE and k >= 0:
        if Path(chosen[k]).stem.startswith("footage_"):
            chosen[k] = stills[k % len(stills)]
        k -= 1
    # Our own graphics carry information, but a Reel of diagrams loses the place: at most
    # MAX_GRAPHICS of them and GRAPHICS_MAX_SHARE of the runtime; extras become photos.
    # Unlocked graphics go first; a graphic locked to the beat that names it goes only if still over.
    over = lambda: _graphics_count(chosen) > MAX_GRAPHICS or _graphics_share(chosen, durations) > GRAPHICS_MAX_SHARE  # noqa: E731
    for keep_locked in (True, False):
        k = len(chosen) - 1
        while over() and k >= 0:
            if Path(chosen[k]).stem.startswith("graphic_") and not (keep_locked and k in locked):
                chosen[k] = stills[k % len(stills)]
            k -= 1
    if hook is not None:
        chosen[0] = hook
    return chosen, durations, notes
