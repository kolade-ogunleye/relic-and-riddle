"""Which kind of opening a Reel's narration is written with, learned from how its first three seconds did
(2026-11-01).

The feed autoplays: a new viewer meets the Reel's first frame and first sentence, not its cover. Whether they
stay past three seconds is measured (Instagram's 3-second skip rate; on Facebook, the share of viewers still
watching at 3 s on its retention curve), but nothing tied it back to how the Reel opened. Every hook stays
inside the writer's rules (VERIFIED_USER_PROMPT_TEMPLATE: the most surprising verified fact, at most config.HOOK_MAX_WORDS words, QF-14);
the form is how that fact is delivered:

- discovery: what was found, stated plainly ("Beneath a house in Cappadocia, a city lay hidden.");
- question: a question the Reel then answers ("How did a city of 20,000 stay hidden for centuries?");
- figure: the most striking verified number first ("280 feet below a Turkish village, ...");
- contradiction: what seems impossible, and that it is true ("It has no foundations, yet it has stood ...").

Once any Reel's first three seconds are measured, each run is steered to one form by learning.choose (each
tried MIN_TRIES times, then Thompson sampling on the hook reward); before that the writer is not steered. The
form actually written is classified from the hook sentence, since the writer may not follow
the steer, and that is what is measured. "figure" is offered only when the topic's notes hold a number.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import config

log = logging.getLogger(__name__)

FORMS = ("discovery", "question", "figure", "contradiction")
# The Reel's own rows only: audience.table also holds carousel and Story rows, which carry the run's fields but
# did not open with its hook (PR review, 2026-11-02).
VIDEO_PLATFORMS = ("instagram", "facebook", "youtube")
STEERS = {
    "discovery": "Deliver it as a discovery: say plainly what was found and where, as a revelation.",
    "question": "Deliver it as a question the viewer cannot yet answer and the Reel then answers (a rhetorical "
                "question about a verified fact; trace_type rhetorical_framing).",
    "figure": "Lead with the single most striking verified number, with its unit, in the first words.",
    "contradiction": "Deliver it as a contradiction: what seems impossible, then that it is verifiably true.",
}
_CONTRA = re.compile(r"\b(yet|but|despite|although|impossible|shouldn'?t|couldn'?t|no one|nobody|never|without|"
                     r"still|baffl\w*|defies?|instead)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d|\b(hundred|thousand|million|billion|dozens?|twice|half)\b", re.IGNORECASE)


def classify(hook: str) -> str | None:
    """The form a hook sentence was written in, or None for an empty one. A question mark wins, then a number in
    the opening words, then a contradiction, else a discovery."""
    text = " ".join((hook or "").split())
    if not text:
        return None
    if text.rstrip().endswith("?"):
        return "question"
    if _NUMBER.search(" ".join(text.split()[:6])):
        return "figure"
    if _CONTRA.search(text):
        return "contradiction"
    return "discovery"


def opening_shot(scenes: list, motion_dir) -> str | None:
    """What the Reel opens on: "ai_motion" (a clip made from a real photo in `motion_dir`), "footage" (any other
    clip), "still" (a photo, given a camera move later), or None with no scenes."""
    from pathlib import Path

    from .typography import is_video
    if not scenes:
        return None
    first = Path(str(scenes[0]))
    if not is_video(first):
        return "still"
    try:
        return "ai_motion" if Path(motion_dir).resolve() in first.resolve().parents else "footage"
    except OSError:
        return "footage"


def fitting(topic: dict[str, Any]) -> list[str]:
    """The forms this topic can be opened with: "figure" only when its notes or hook hold a number."""
    notes = " ".join([str(topic.get("hook") or ""), *map(str, topic.get("key_facts") or [])])
    return [f for f in FORMS if f != "figure" or _NUMBER.search(notes)]


def reward(row: dict[str, Any]) -> float | None:
    """A measured video's first-three-seconds reward in [0, 1]: 1 - its skip rate (Instagram), the share of viewers
    still watching at 3 s (Facebook's retention curve), or their mean; None when neither is reported."""
    parts = []
    skip = row.get("skip_rate")
    if isinstance(skip, (int, float)) and not isinstance(skip, bool):
        parts.append(min(1.0, max(0.0, 1 - skip / 100)))
    hold = row.get("hold_3s")
    if isinstance(hold, (int, float)) and not isinstance(hold, bool):
        parts.append(min(1.0, max(0.0, float(hold))))
    return sum(parts) / len(parts) if parts else None


def evidence(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Each written hook form's rewards, one per run, from audience.table rows on every video platform."""
    from .learning import per_run_evidence
    scored = [(r, v) for r in rows if r.get("platform") in VIDEO_PLATFORMS and not r.get("unavailable")
              and r.get("hook_form") for v in [reward(r)] if v is not None]
    return per_run_evidence(scored, "hook_form")


def choose(topic: dict[str, Any], rows: list[dict[str, Any]], seed: str = "") -> tuple[str, str]:
    """(form, why) to steer this run's hook towards."""
    from . import learning
    return learning.choose(fitting(topic), evidence(rows), seed, "hook")


def steer_note(form: str | None) -> str:
    """The writer's instruction for the hook's form ("" for none): appended to the script prompt."""
    if form not in STEERS:
        return ""
    return ("\n\nHOOK FORM for this Reel (the first sentence, within every rule above; the fact itself must still be "
            f"the most surprising verified one): {STEERS[form]}")


def revision_note(form: str, hook: str) -> str:
    """The findings for the soft check's one revision: recast the first sentence in `form`, same fact, nothing
    else changed (read by the surgical reviser, which edits only the sentences named). It names what the hook rule
    accepts (script_engine.HOOK_FORMS), so the revision can pass it."""
    from .script_engine import HOOK_FORMS
    return (f"Hook form: the first sentence (also hook_sentence) \"{hook}\" is not in the asked form "
            f"({form}). {STEERS[form]} Rewrite only that sentence (and hook_sentence to match), keeping the same "
            f"verified fact, its hedge if it has one, and at most {config.HOOK_MAX_WORDS} words, with its figure, "
            f"contrast or question within the first {config.HOOK_SIGNAL_WITHIN}. {HOOK_FORMS} Keep the noun the "
            "payoff (payoff_sentence) names again, so the payoff still answers it (QF-15). Change nothing else.")


def follow_rate(rows: list[dict[str, Any]]) -> str | None:
    """A digest line on how often the writer opened in the asked form (from each run's hook_plan), or None before
    any run was steered."""
    plans = {r["run_id"]: r["hook_plan"] for r in rows if isinstance(r.get("hook_plan"), dict)
             and r["hook_plan"].get("asked")}
    if not plans:
        return None
    first = sum(1 for p in plans.values() if p.get("written") == p["asked"] and not p.get("revised"))
    after = sum(1 for p in plans.values() if p.get("revised"))
    return (f"• Hook steer: {len(plans)} run(s) asked; {first} opened in the asked form at once, {after} after one "
            "revision, the rest kept their passed draft.")


def for_run(db, run_id: str, topic: dict[str, Any]) -> tuple[str | None, str]:
    """choose() for a pipeline run, from the audience table; (None, why) when the numbers cannot be read, so the
    writer gets no steer rather than a guess."""
    from . import audience
    try:
        rows = audience.table(db)
    except Exception as e:  # noqa: BLE001 - a broken metrics table must never stop a script
        log.warning("Hook form not chosen (%s); the writer gets no steer.", e)
        return None, f"no steer: audience numbers unreadable ({e})"
    if not evidence(rows):
        # Nothing measured yet (a new channel, or no first-three-seconds numbers): the writer writes as it always
        # has, and the forms those Reels open with seed the comparison once they are measured.
        return None, "no steer: no Reel's first three seconds measured yet"
    form, why = choose(topic, rows, seed=run_id)
    log.info("Hook form %s (%s).", form, why)
    return form, why
