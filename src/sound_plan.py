"""
src/sound_plan.py
=================
Sound that follows the story. One LLM call reads the timed narration and marks its moments: where
something is revealed, where tension builds, where the story pauses, where an action is described.
Code then validates and times the plan before the ambient bed uses it for sound effects (from the
licensed pack only) and for the music's swells and dips. Without a model, or with an unusable plan,
the bed falls back to its word-list cues (AudioEngine.sfx_cue_times).

The same call divides the narration into mood sections; the music's mood is the one that covers most of the spoken
time (dominant_mood, QF-84). It is chosen before the music, never defaulted: a plan without usable sections gives no
mood, and the music step holds the library and says why on the review card.
"""

import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

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


Mood = Literal["mysterious", "epic", "melancholic", "tense"]   # music.MOODS: the library's sets


class MoodSection(BaseModel):
    word: int                       # 1-based index of the word the section starts on
    mood: Mood


class SoundPlan(BaseModel):
    moments: List[SoundMoment]
    sections: list[MoodSection] = []   # the narration's music mood, section by section (QF-84)


class PlannedSound(SoundPlan):
    """The schema the planning call answers in: its mood sections are required (1 to 4), so a model that would omit
    them is asked by the schema itself, in the same single call, instead of sending the Reel to the drone. The 34
    recorded development plans predate the field, so how often a model omits optional sections is unmeasured."""
    sections: list[MoodSection] = Field(min_length=1, max_length=4)


SYSTEM = f"""You are the sound designer of a short history documentary. Read the numbered narration and mark its key moments, at most 6:
- reveal: the moment a striking fact or discovery lands (the music swells briefly);
- tension: where suspense builds toward a reveal (the music rises slowly);
- calm: a reflective pause (the music dips);
- action: where the narration names a physical thing, place or action that has a sound of its own.
Give an effect when the words at that moment name the thing or action it depicts (a forge or blade, a battle or siege, a ship at sea, waves, a fire, a storm, a collapse, footsteps on stone, a bell, a crowd, horses, a scroll or letter, gears, a chisel, a door), so the sound makes the picture concrete; the effect's file name says what it is: {", ".join(EFFECTS)}. Place it on the word that names the thing, at its first mention. No effect for a figure of speech ("a war of ideas"), a number, or a sound the story never brings on screen. Use the number of the word the moment should land on.
Then divide the whole narration into 1 to 4 mood sections for the music, each given by the number of the word it starts on (the first starts on word 1) and its mood, judged from what the words say happens:
- mysterious: something unexplained, hidden or still unknown;
- epic: scale, achievement, ingenuity, awe;
- melancholic: loss, death, ruin, absence, something destroyed or forgotten;
- tense: danger, threat, conflict, a race against time.
A reflective pause is not by itself melancholic; choose the mood the story's events carry."""


def plan(words: List[Dict[str, Any]], topic_title: str = "") -> Optional[Dict[str, Any]]:
    """A validated, timed plan {"moments": [{"t", "kind", "effect"}], "mood": {...}}, or None to use the fallback."""
    return plan_and_mood(words, topic_title)[0]


def plan_and_mood(words: list[dict[str, Any]], topic_title: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The plan (None: the bed's word-cue fallback) and the music's mood record (dominant_mood): the mood, or None
    with the reason there is none. The music is chosen from this record, so a failed plan is never a silent default."""
    if not words:
        return None, no_mood("the narration has no timed words")
    if not free_llm.ready():
        return None, no_mood("no planning model is configured")
    numbered = " ".join(f"[{i}]{w.get('word', '')}" for i, w in enumerate(words, 1))
    try:
        raw = free_llm.call(SYSTEM, f"Topic: {topic_title}\n\nNarration:\n{numbered}", PlannedSound, role="writer", task_class="light",
                            effort="low", max_tokens=2000)
    except (LLMError, OSError) as e:
        log.info("Sound plan unavailable (%s); using word cues.", e)
        return None, no_mood(f"the sound plan failed ({str(e)[:160]})")
    return validate(raw, words), dominant_mood(raw, words)


def no_mood(reason: str) -> dict[str, Any]:
    return {"mood": None, "reason": reason, "sections": [], "seconds": {}}


def dominant_mood(raw: SoundPlan, words: list[dict[str, Any]]) -> dict[str, Any]:
    """The mood that covers most of the spoken time, from the plan's sections (code, not the model, times them).
    A section on a word that does not exist is dropped; a section runs to the next one and the last to the end of
    the narration; time before the first section is not counted. A tie goes to the mood heard first. No usable
    section: no mood, with the reason (never a default)."""
    if not words:
        return no_mood("the narration has no timed words")
    end = max(float(w.get("start", 0.0)) + float(w.get("dur", 0.0)) for w in words)
    starts: dict[int, str] = {}
    for section in sorted(raw.sections or [], key=lambda s: s.word):
        if 1 <= section.word <= len(words) and section.word not in starts:
            starts[section.word] = section.mood
    if not starts:
        return no_mood("the sound plan marked no mood sections")
    timed = [(float(words[w - 1].get("start", 0.0)), m) for w, m in sorted(starts.items())]
    sections, seconds, first = [], {}, {}
    for i, (t, m) in enumerate(timed):
        stop = timed[i + 1][0] if i + 1 < len(timed) else end
        sections.append({"t": round(t, 3), "end": round(stop, 3), "mood": m})
        seconds[m] = round(seconds.get(m, 0.0) + max(0.0, stop - t), 3)
        first.setdefault(m, i)
    total = sum(seconds.values())
    if total <= 0:
        return no_mood("the sound plan's mood sections cover no spoken time")
    mood = max(seconds, key=lambda m: (seconds[m], -first[m]))
    return {"mood": mood, "reason": "", "share": round(seconds[mood] / total, 3), "sections": sections,
            "seconds": seconds}


def validate(raw: SoundPlan, words: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Code, not the model, decides what is used: known effects only, real word positions, at most
    MAX_EFFECTS effects at least MIN_GAP_SECONDS apart, nothing in the first half second. The plan also carries
    the narration's music mood (dominant_mood)."""
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
    if not moments:
        return None
    return {"moments": moments, "mood": dominant_mood(raw, words)}
