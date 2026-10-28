"""A look at each rendered Story frame and carousel slide before it reaches the review card (quality step 5,
2026-10-25). The geometric audits catch blank frames, aspect ratios, margins and the safe-zone boxes; they
cannot see text cut off by its own box, a title across the subject's face, a kicker unreadable over bright
sand, or a photograph that has nothing to do with the words on it. One light vision call per still, with the
still's own words; when it cannot run, the still is kept and the record says it was not looked at.

Most findings are informational until calibrated on real episodes: a wrong verdict must not hold a release.
They appear on the review card and in the improvement ledger, where the next step (moving the text, choosing
another photo) is decided from evidence.

Text cut off and elements overlapping are acted on (still-layout audit, 2026-10-28: those notes preceded 3 of
3 operator rejections, and only noted them). First, from numbers alone: each still's measured layout
(still_layout, recorded by its renderer) is re-checked, and a box past the canvas or its safe zone, or two
boxes over each other, is redrawn once with the tighter fit; a measured defect that survives it is major.
Then the look: when it reports either defect, the still is redrawn once with the tighter fit (unless the
measured check already redrew it) and looked at again; a defect it still sees, or a still that could not be
redrawn or re-checked, is raised to minor, so the operator's MINOR_FINDINGS_BLOCK_RELEASE decides. Text
the fit had to shorten (an ellipsis or dropped sentences) is minor too."""
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel

from . import config, still_layout
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
    return problems, (verdict.reason or "").strip()  # whole: the card folds it (cut mid-word at 160, 2026-10-25)


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


LAYOUT_PROBLEMS = ("text_cut_off", "layout_overlap")   # the two the layout can fix by itself


def _redraw(path: Path) -> bool:
    entry = still_layout.redraw(path)
    if entry is not None:
        log.info("Redrew %s with the tighter fit: %s", path.name, "; ".join(entry.get("problems") or []) or "its layout checks pass")
    return entry is not None


def findings(paths: List[Path], kind: str, copies: List[str], issue_type: str, key: str) -> List[Dict[str, Any]]:
    """Issues for each still with a problem: measured layout defects (major after one redraw), text the fit
    shortened (minor), and the look's findings (info; a cut-off or overlap is redrawn once and raised to minor
    if it is still seen). A still that could not be looked at adds no look finding."""
    out: List[Dict[str, Any]] = []
    for i, path in enumerate(paths):
        path = Path(path)
        where = f"{kind.capitalize()} {i + 1} ({path.name})"
        redrawn = False
        measured = still_layout.check(path)             # None: no record (a still drawn before 2026-10-28)
        if measured:
            redrawn = _redraw(path)
            measured = still_layout.check(path) if redrawn else measured
            if measured:
                out.append({"type": issue_type, "severity": "major", key: path.name, "measured": measured,
                            "problems": sorted({m.split(":")[0] for m in measured}),
                            "issue": f"{where}: its measured layout fails"
                                     f"{' after one redraw with a tighter fit' if redrawn else ''}: {'; '.join(measured[:3])}"})
        entry = still_layout.load(path) or {}
        # The Story body has its own major finding when shortened (video_qa: story_text_incomplete).
        short = [n for n in entry.get("shortened") or [] if not (entry.get("kind") == "story" and n == "body")]
        if short:
            out.append({"type": issue_type, "severity": "minor", key: path.name, "problems": ["text_shortened"],
                        "issue": f"{where}: text shortened to fit its box ({', '.join(short)}): the copy is longer than "
                                 f"the layout holds at its smallest size"})
        copy = copies[i] if i < len(copies) else ""
        problems, reason = look(path, kind, copy)
        if not problems:
            continue
        severity, note = "info", ""
        if any(p in LAYOUT_PROBLEMS for p in problems):
            if not redrawn:
                redrawn = _redraw(path)
                if redrawn:
                    again, reason_again = look(path, kind, copy)
                    persisting = [p for p in again if p in LAYOUT_PROBLEMS]
                    if persisting:
                        severity, problems, reason = "minor", again, reason_again
                        note = " (still seen after one redraw with a tighter fit)"
                    elif not again and reason_again in ("not checked", "check unavailable"):
                        severity, note = "minor", " (redrawn once with a tighter fit; the re-check could not run)"
                    else:
                        note = " (redrawn once with a tighter fit; the re-check no longer sees it)"
                else:
                    severity, note = "minor", " (no layout record to redraw it from)"
            else:   # the measured check already redrew it: this is what the redraw looks like
                severity, note = "minor", " (seen after one redraw with a tighter fit)"
        out.append({"type": issue_type, "severity": severity, key: path.name, "problems": problems,
                    **({"redrawn": True} if redrawn else {}),
                    "issue": f"{where}: {', '.join(PROBLEM_WORDS.get(p, p) for p in problems)}: {reason}{note}"})
    return out
