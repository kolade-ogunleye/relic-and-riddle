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
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import free_llm
from .config import FOOTAGE_MAX_SHARE
from .llm import LLMError, claude_available, image_block, structured_call, task as llm_task
from .models import ScenePlan
from .typography import is_video

log = logging.getLogger(__name__)

MIN_BEAT, TARGET_BEAT, MAX_BEAT = 2.5, 3.2, 4.5

SYSTEM = """You are a documentary film editor. Choose the best picture for each line of narration in a short vertical history video. Prefer images that literally show what the line describes; for abstract lines, pick an atmospheric image that fits. Use each image at most once where possible, start strong (the first beat is the hook), and avoid placing two near-identical images back to back. Never pick an image that contradicts the narration; real footage labelled GENERIC must never stand in for the named place.
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


def _is_graphic(p: Path) -> bool:
    """A clip the planner may place: an original graphic (src/graphics.py) or a real footage
    segment (src/footage.py). Both have a poster frame beside them."""
    return is_video(p) and Path(p).stem.startswith(("graphic_", "footage_")) and Path(p).with_suffix(".jpg").exists()


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
    """
    Directly locks channel graphics onto the exact beat where the narrator speaks about them.
    - Scale / Cross-section / Stat cards / Numbers: where depth/dimensions/capacity/levels are stated
    - Map: where location/discovery/geography is introduced (e.g. 1963, Cappadocia, Turkey, village, entrance, wall)
    - Timeline: where historical dates / eras / dynasties are stated (e.g. century, bc, ad, byzantine, hittites, phrygians, years old)
    - Rolling doors / defense / mechanisms: where stone doors / locks are stated (e.g. rolling stone, doors, sealed, tons)
    - Ventilation / shaft / airflow: where ventilation or breathing is stated
    """
    locked: Dict[int, Path] = {}
    used_g: set = set()

    for idx, b in enumerate(beats):
        txt = b.get("text", "").lower()
        # 1. Depth & Scale & Numbers graphics (skip beat 0: hook requires visual photo/video immersion)
        if idx > 0 and any(w in txt for w in ("depth", "metre", "meter", "feet", "levels", "stories", "280", "180", "85", "capacity", "thousand", "population", "people")):
            scale_g = next((g for g in graphics if g not in used_g and any(k in g.stem for k in ("section", "scale", "stat", "numbers"))), None)
            if scale_g:
                locked[idx] = scale_g
                used_g.add(scale_g)
                continue
        # 2. Map & Geographic context (skip beat 0 so hook still gets a photo/video hook)
        if idx > 0 and any(w in txt for w in ("1963", "cappadocia", "turkey", "renovating", "wall", "region", "village", "anatolian", "plains")):
            map_g = next((g for g in graphics if g not in used_g and "map" in g.stem), None)
            if map_g:
                locked[idx] = map_g
                used_g.add(map_g)
                continue
        # 3. Timeline & Historical dynasties
        if any(w in txt for w in ("years old", "century", " bc", " ad", "byzantine", "hittite", "phrygian", "dynasty")):
            time_g = next((g for g in graphics if g not in used_g and any(k in g.stem for k in ("timeline", "dates"))), None)
            if time_g:
                locked[idx] = time_g
                used_g.add(time_g)
                continue
        # 4. Mechanism & Defense (doors, rooms)
        if any(w in txt for w in ("rolling", "doors", "sealed", "choke point", "stoneware", "millstone", "defense", "defensive", "basalt")):
            callout_g = next((g for g in graphics if g not in used_g and any(k in g.stem for k in ("defense", "labels", "callout", "door"))), None)
            if callout_g:
                locked[idx] = callout_g
                used_g.add(callout_g)
                continue
        # 5. Ventilation & Airflow
        if any(w in txt for w in ("ventilation", "shaft", "air", "breathe", "convection", "airflow", "oxygen")):
            vent_g = next((g for g in graphics if g not in used_g and any(k in g.stem for k in ("ventilation", "shaft", "air"))), None)
            if vent_g:
                locked[idx] = vent_g
                used_g.add(vent_g)
                continue
    return locked


@llm_task("scene_plan")
def plan_scenes(beats: List[Dict[str, Any]], media: List[Path], feedback: str = "") -> Tuple[List[Path], List[float], str]:
    """Returns (media per beat, duration per beat, notes). A motion hook clip stays first.
    `feedback` lists problems a reviewer found with a previous plan, so they are avoided."""
    durations = [b["end"] - b["start"] for b in beats]
    hook = media[0] if media and is_video(media[0]) and not _is_graphic(media[0]) else None
    stills = [p for p in media if not is_video(p)]
    graphics = [p for p in media if _is_graphic(p)]
    options = stills + graphics  # what the planner chooses from (graphics shown by their poster frame)
    n = len(beats)

    order = list(range(n))  # default: filename order of the photos, cycling if there are more beats than photos
    notes = "filename order"
    if (free_llm.available() if free_llm.enabled() else claude_available()) and stills:
        try:
            content: List[dict] = []
            for i, p in enumerate(options, 1):
                if p in graphics:
                    content += [{"type": "text", "text": f"Image {i} ({_clip_label(p)}):"}, image_block(p.with_suffix(".jpg"))]
                else:
                    content += [{"type": "text", "text": f"Image {i}:"}, image_block(p)]
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

    # Footage supports the narration, never dominates it (Meta's originality rules): over the
    # cap, the latest footage picks become photos.
    k = len(chosen) - 1
    while _footage_share(chosen, durations) > FOOTAGE_MAX_SHARE and k >= 0:
        if Path(chosen[k]).stem.startswith("footage_"):
            chosen[k] = stills[k % len(stills)]
        k -= 1
    # Our own graphics carry information, but a Reel of diagrams loses the place: at most
    # MAX_GRAPHICS of them and GRAPHICS_MAX_SHARE of the runtime; extras become photos.
    k = len(chosen) - 1
    while (_graphics_count(chosen) > MAX_GRAPHICS or _graphics_share(chosen, durations) > GRAPHICS_MAX_SHARE) and k >= 0:
        if Path(chosen[k]).stem.startswith("graphic_"):
            chosen[k] = stills[k % len(stills)]
        k -= 1
    if hook is not None:
        chosen[0] = hook
    return chosen, durations, notes
