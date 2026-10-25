"""A look at each rendered Story frame and carousel slide before it reaches the review card (quality step 5,
2026-10-25). The geometric audits catch blank frames, aspect ratios, margins and the safe-zone boxes; they
cannot see text cut off by its own box, a title across the subject's face, a kicker unreadable over bright
sand, or a photograph that has nothing to do with the words on it. One light vision call per still, with the
still's own words; when it cannot run, the still is kept and the record says it was not looked at.

Findings are informational until calibrated on real episodes: a still is deterministic, so a re-render would
not change it, and a wrong verdict must not hold a release. They appear on the review card and in the
improvement ledger, where the next step (moving the text, choosing another photo) is decided from evidence."""
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel

from . import config
from .llm import structured_call

log = logging.getLogger(__name__)

Problem = Literal["text_cut_off", "text_unreadable", "text_over_subject", "layout_overlap", "photo_unrelated", "none"]

PROBLEM_WORDS = {
    "text_cut_off": "text cut off",
    "text_unreadable": "text hard to read",
    "text_over_subject": "text over the subject",
    "layout_overlap": "elements overlapping",
    "photo_unrelated": "photo unrelated to the words",
}


class StillVerdict(BaseModel):
    problems: List[Problem]
    reason: str  # one short sentence, from the image itself


SYSTEM = ("You check a finished social-media still from a history documentary (a Story frame or a carousel slide) for "
          "defects a viewer would notice on a phone. Report only what the image itself shows: text cut off at an edge or "
          "by its own box (text_cut_off); text too small, too faint or too low in contrast against its background to "
          "read (text_unreadable); a title or label covering the main subject's face or the very detail the words are "
          "about (text_over_subject); elements overlapping or colliding (layout_overlap); a photograph that has nothing "
          "to do with the words on the frame (photo_unrelated). A dark gradient or pill behind text, a photo cropped to "
          "the frame, small credit lines and a deliberately partial view of the subject are not defects. If nothing is "
          "wrong, answer none.")


def look(path: Path, kind: str, copy: str = "") -> Tuple[List[str], str]:
    """(problems, reason) for one still; ([], "not checked" / "check unavailable") when it could not be looked at."""
    if not config.STILL_VISION_CHECK:
        return [], "not checked"
    from . import free_llm
    from .llm import image_block
    text = f"A {kind}." + (f" Its words: {copy[:400]}" if copy else "") + "\nWhat is wrong with it, if anything?"
    content = [{"type": "text", "text": text}, image_block(path, 768)]
    try:
        if free_llm.claude_engine():
            verdict = structured_call(SYSTEM, content, StillVerdict, effort="low", max_tokens=300,
                                      models=config.LIGHT_TASK_MODELS)
        else:
            verdict = free_llm.structured(SYSTEM, content, StillVerdict, role="checker", task_class="light")
    except Exception as e:  # unavailable or malformed: the still stays, the record says it was not looked at
        log.debug("Still check unavailable for %s (%s).", path.name, e)
        return [], "check unavailable"
    problems = [p for p in verdict.problems if p != "none"]
    return problems, (verdict.reason or "")[:160]


def story_copy(payload: Dict[str, Any], index: int) -> str:
    """The words on Story frame `index` (0-based), as the renderer puts them there."""
    slides = (payload or {}).get("story_slides") or []
    if index >= len(slides):
        return ""
    s = slides[index]
    words = " / ".join(str(s.get(k, "")) for k in ("kicker", "line1", "line2", "body") if s.get(k))
    poll = (payload or {}).get("poll") or {}
    if index == len(slides) - 1 and poll.get("question"):
        words += f" / {poll['question']} / A: {' / B: '.join(str(o) for o in poll.get('options', [])[:2])}"
    return words


def carousel_copy(payload: Dict[str, Any], index: int) -> str:
    """The words on carousel slide `index` (0-based)."""
    slides = (payload or {}).get("carousel_slides") or []
    if index >= len(slides):
        return ""
    s = slides[index]
    return " / ".join(str(s.get(k, "")) for k in ("heading", "body") if s.get(k))


def findings(paths: List[Path], kind: str, copies: List[str], issue_type: str, key: str) -> List[Dict[str, Any]]:
    """Informational issues, one per still with a problem; a still that could not be checked adds none."""
    out: List[Dict[str, Any]] = []
    for i, path in enumerate(paths):
        problems, reason = look(path, kind, copies[i] if i < len(copies) else "")
        if problems:
            out.append({"type": issue_type, "severity": "info", key: path.name, "problems": problems,
                        "issue": f"{kind.capitalize()} {i + 1} ({path.name}): "
                                 f"{', '.join(PROBLEM_WORDS.get(p, p) for p in problems)}: {reason}"})
    return out
