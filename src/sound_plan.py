"""
src/sound_plan.py
=================
Sound that follows the story. One LLM call reads the timed narration and marks its moments: where
something is revealed, where tension builds, where the story pauses, where an action is described.
Code then validates and times the plan before the ambient bed uses it for sound effects (from the
licensed pack only) and for the music's swells and dips. Without a model, or with an unusable plan,
the bed falls back to its word-list cues (AudioEngine.sfx_cue_times).
"""

import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from . import free_llm, sfx_palette
from .llm import LLMError

log = logging.getLogger(__name__)

# What the model may choose: every effect of the licensed palette that a word can cue (src/sfx_palette.py).
# Widened 2026-10-25 from five stone-and-gear effects to the settings and actions the topic bank narrates,
# after three episodes planned moments and chose no effect at all.
EFFECTS = tuple(sfx_palette.cues())
MAX_EFFECTS = 4
MIN_GAP_SECONDS = 2.5
Kind = Literal["reveal", "tension", "calm", "action"]


class SoundMoment(BaseModel):
    word: int                       # 1-based index of the word the moment lands on
    kind: Kind
    effect: Optional[str] = None    # one of the listed effect files, only when the narration describes that action/sound


class SoundPlan(BaseModel):
    moments: List[SoundMoment]


SYSTEM = f"""You are the sound designer of a short history documentary. Read the numbered narration and mark its key moments, at most 6:
- reveal: the moment a striking fact or discovery lands (the music swells briefly);
- tension: where suspense builds toward a reveal (the music rises slowly);
- calm: a reflective pause (the music dips);
- action: where the narration names a physical thing, place or action that has a sound of its own.
Give an effect when the words at that moment name the thing or action it depicts (a forge or blade, a battle or siege, a ship at sea, waves, a fire, a storm, a collapse, footsteps on stone, a bell, a crowd, horses, a scroll or letter, gears, a chisel, a door), so the sound makes the picture concrete; the effect's file name says what it is: {", ".join(EFFECTS)}. Place it on the word that names the thing, at its first mention. No effect for a figure of speech ("a war of ideas"), a number, or a sound the story never brings on screen. Use the number of the word the moment should land on."""


def plan(words: List[Dict[str, Any]], topic_title: str = "") -> Optional[Dict[str, Any]]:
    """A validated, timed plan {"moments": [{"t", "kind", "effect"}]}, or None to use the fallback."""
    if not words or not free_llm.ready():
        return None
    numbered = " ".join(f"[{i}]{w.get('word', '')}" for i, w in enumerate(words, 1))
    try:
        raw = free_llm.call(SYSTEM, f"Topic: {topic_title}\n\nNarration:\n{numbered}", SoundPlan, role="writer", task_class="light",
                            effort="low", max_tokens=2000)
    except (LLMError, OSError) as e:
        log.info("Sound plan unavailable (%s); using word cues.", e)
        return None
    return validate(raw, words)


def validate(raw: SoundPlan, words: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Code, not the model, decides what is used: known effects only, real word positions, at most
    MAX_EFFECTS effects at least MIN_GAP_SECONDS apart, nothing in the first half second."""
    moments, effect_times = [], []
    for m in sorted(raw.moments, key=lambda m: m.word):
        if not 1 <= m.word <= len(words):
            continue
        t = float(words[m.word - 1].get("start", 0.0))
        if t < 0.5:
            continue
        effect = m.effect if m.effect in EFFECTS else None
        if effect and (len(effect_times) >= MAX_EFFECTS or any(abs(t - e) < MIN_GAP_SECONDS for e in effect_times)):
            effect = None
        if effect:
            effect_times.append(t)
        moments.append({"t": round(t, 3), "kind": m.kind, "effect": effect})
    return {"moments": moments} if moments else None
