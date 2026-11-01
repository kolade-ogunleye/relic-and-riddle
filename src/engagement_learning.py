"""Which share-and-follow ask and which audience question a Reel is written with, learned from what its
viewers did (2026-11-01).

Every Reel asks twice: its share line ("Send this to the codebreaker you know, and follow for the next relic.",
on the end card and closing both captions) and the caption's question to the audience. Both stay inside the
writer's rules (share_line_problems: name who, for this story; never bait or a number of people); the form is
how they are put:

share line, scored on shares plus saves per account reached:
- identity: names the person by who they are ("the codebreaker you know");
- interest: names them by what they love ("a friend who loves Roman engineering").

question, scored on comments per account reached:
- either_or: a choice between two answers ("... on foot, or from orbit?");
- would_you: puts the viewer in the story ("Would you have stayed below?");
- opinion: asks what they think or rank ("Which wonder looks most magnificent?");
- open: any other open question.

Each run is steered to one form of each by learning.choose; the forms actually written are classified from the
text, and those are what is measured.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

SHARE_FORMS = ("identity", "interest")
QUESTION_FORMS = ("either_or", "would_you", "opinion", "open")
SHARE_STEERS = {
    "identity": "In share_line, name the person by who they are for this story (\"the codebreaker you know\").",
    "interest": "In share_line, name the person by what they love, for this story (\"a friend who loves Roman "
                "engineering\").",
}
QUESTION_STEERS = {
    "either_or": "Make the caption's question a choice between two answers the story supports.",
    "would_you": "Make the caption's question put the viewer in the story (\"Would you ...?\").",
    "opinion": "Make the caption's question ask the viewer's opinion or ranking (\"Which ...?\", \"What do you "
               "think ...?\").",
    "open": "Make the caption's question an open one the viewer can answer from their own experience.",
}
SHARED = 0.03      # shares + saves per account reached that count as a full reward
COMMENTED = 0.01   # comments per account reached that count as a full reward


def classify_share(line: str) -> str | None:
    text = " ".join((line or "").split()).lower()
    if not text:
        return None
    if re.search(r"\b(who|that) (loves?|likes?|enjoys?|is into|would love)\b", text):
        return "interest"
    return "identity" if re.search(r"\byou know\b|\byour\b", text) else "interest"


def caption_question(caption: str) -> str:
    """The caption's question to the audience: its last sentence ending in "?" before the hashtags, else ""."""
    body = "\n".join(ln for ln in (caption or "").splitlines() if not ln.strip().startswith("#"))
    questions = re.findall(r"[^.!?\n]*\?", body)
    return questions[-1].strip() if questions else ""


def classify_question(question: str) -> str | None:
    text = " ".join((question or "").split())
    if not text:
        return None
    low = text.lower()
    if re.match(r"(would|could|will) you\b", low) or re.search(r"\bwould you\b", low):
        return "either_or" if re.search(r",? or\b", low) else "would_you"
    if re.search(r"\bor\b", low):
        return "either_or"
    if re.match(r"(which|what do you think|what's your|whose|who do you|how would you rank)\b", low):
        return "opinion"
    return "open"


def _num(row, key):
    v = row.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def share_reward(row: dict[str, Any]) -> float | None:
    share, save = _num(row, "share_rate"), _num(row, "save_rate")
    if share is None and save is None:
        return None
    return min(1.0, ((share or 0.0) + (save or 0.0)) / SHARED)


def question_reward(row: dict[str, Any]) -> float | None:
    comments, reach = _num(row, "comments"), _num(row, "reach")
    if comments is None or not reach:
        return None
    return min(1.0, comments / reach / COMMENTED)


def _evidence(rows, key, reward):
    from .learning import per_run_evidence
    scored = [(r, v) for r in rows if not r.get("unavailable") and r.get(key) for v in [reward(r)] if v is not None]
    return per_run_evidence(scored, key)


def choose(rows: list[dict[str, Any]], seed: str = "") -> tuple[tuple[str, str], str]:
    """((share form, question form), why) to steer this run's asks towards."""
    from . import learning
    share, why_s = learning.choose(list(SHARE_FORMS), _evidence(rows, "share_form", share_reward), seed, "share")
    question, why_q = learning.choose(list(QUESTION_FORMS), _evidence(rows, "question_form", question_reward),
                                      seed, "question")
    return (share, question), f"share line {why_s}; question {why_q}"


def steer_note(forms: tuple[str, str] | None) -> str:
    """The writer's instruction for the share line's and the question's form ("" for none)."""
    if not forms:
        return ""
    share, question = forms
    lines = [x for x in (SHARE_STEERS.get(share), QUESTION_STEERS.get(question)) if x]
    return ("\n\nENGAGEMENT FORM for this Reel (within every rule above): " + " ".join(lines)) if lines else ""


def for_run(db, run_id: str) -> tuple[tuple[str, str] | None, str]:
    from . import audience
    try:
        rows = audience.table(db)
    except Exception as e:  # noqa: BLE001 - a broken metrics table must never stop a script
        log.warning("Engagement forms not chosen (%s); the writer gets no steer.", e)
        return None, f"no steer: audience numbers unreadable ({e})"
    if not (_evidence(rows, "share_form", share_reward) or _evidence(rows, "question_form", question_reward)):
        return None, "no steer: no Reel's shares or comments measured yet"
    forms, why = choose(rows, seed=run_id)
    log.info("Engagement forms %s / %s (%s).", forms[0], forms[1], why)
    return forms, why
