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

import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .llm import LLMError, claude_available, image_block, structured_call
from .models import ScenePlan
from .typography import is_video

log = logging.getLogger(__name__)

MIN_BEAT, TARGET_BEAT, MAX_BEAT = 2.5, 3.2, 4.5

SYSTEM = """You are a documentary film editor. Choose the best picture for each line of narration in a short vertical history video. Prefer images that literally show what the line describes; for abstract lines, pick an atmospheric image that fits. Use each image at most once where possible, start strong (the first beat is the hook), and avoid placing two near-identical images back to back. Never pick an image that contradicts the narration."""


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


def plan_scenes(beats: List[Dict[str, Any]], media: List[Path]) -> Tuple[List[Path], List[float], str]:
    """Returns (media per beat, duration per beat, notes). A motion hook clip stays first."""
    durations = [b["end"] - b["start"] for b in beats]
    hook = media[0] if media and is_video(media[0]) else None
    stills = [p for p in media if not is_video(p)]
    n = len(beats)

    order = list(range(n))  # default: filename order, cycling if there are more beats than images
    notes = "filename order"
    if claude_available() and stills:
        try:
            content: List[dict] = []
            for i, p in enumerate(stills, 1):
                content += [{"type": "text", "text": f"Image {i}:"}, image_block(p)]
            lines = "\n".join(f"Beat {i}: {b['text']}" for i, b in enumerate(beats, 1))
            content.append({"type": "text", "text": (
                f"Narration beats ({n}):\n{lines}\n\nReturn exactly {n} image numbers (1-{len(stills)}), one per beat, in order.")})
            plan = structured_call(SYSTEM, content, ScenePlan, effort="low", max_tokens=4000)
            picks = [i - 1 for i in plan.images]
            if len(picks) == n and all(0 <= i < len(stills) for i in picks):
                order, notes = picks, plan.notes
            else:
                log.warning("Scene plan had %d picks for %d beats or bad numbers; using filename order.", len(picks), n)
        except LLMError as e:
            log.warning("Scene planning failed (%s); using filename order.", e)

    chosen = [stills[i % len(stills)] for i in order]
    if hook is not None:
        chosen[0] = hook
    return chosen, durations, notes
