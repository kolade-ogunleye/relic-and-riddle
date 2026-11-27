"""
src/subject_look.py
===================
PROTOTYPE, behind config.PICTURE_SUBJECT_LOOK (off): the picture check (beat_images.picture_look) also says WHERE
the subject is, in the same call, at the same image size.

Why (design freeze cycle 6, SUBJECT-VISION evaluation): salient-object segmentation and an open-vocabulary detector
miss or cut the labelled subject in 47-62 % of photographs, and their confident failures are failures of meaning (the
Pantheon's oculus for a dome photograph, a tourist for an obelisk, a spacecraft for the Richat Eye, an aircraft
wing). The picture check already asks a vision language model whether a photograph shows the topic; given the
topic, the photograph's title/caption/credit and the claim its slide makes, the same answer can name the subject and
box it, say when the photograph is a scene with no single subject, and name what is NOT the subject.

This module only ASKS and VALIDATES. Nothing consumes the record: photo_fitness, covers, carousels and Stories are
unchanged whether the switch is on or off. Boxes are asked in the convention the check already uses for text and
intrusions ([ymin, xmin, ymax, xmax], integers 0-1000) and recorded as [x0, y0, x1, y1] in 0-1 of the picture, the
convention of photo_subject and the labelled set (docs/reviews/freeze-system-2026-11-15/corpus/subject).
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .llm import degenerate_reason

# Bumped when the question or the validation changes: an answer is only comparable with answers to the same question.
VERSION = 1
# Mechanical validation (subject_record)
MIN_SIDE = 0.01       # a box narrower or shorter than 1 % of the picture is degenerate (the smallest labelled subject is 8 x 9 %)
WHOLE_FRAME = 0.95    # an object box covering >= 95 % of the frame is the whole frame: only a scene may say so
CONTEXT_CHARS = 400   # each metadata field is cut to this many characters (a Commons description can run to pages)

SUBJECT_SYSTEM = (
    " Then say WHERE the subject is. The documentary's topic, the photograph's title, caption and credit, and the "
    "claim its slide makes are given as context; they can be wrong, so judge the picture. subject_name: the subject "
    "in a few words, as the picture shows it (\"the two seated statues\", \"the ring structure\", \"the coffered dome\"). "
    "scene: true when the whole view is the subject and no single bounded thing is (a landscape, a room interior, a "
    "pattern filling the frame, a whole map, chart or page); then subject_box is [0, 0, 1000, 1000]. subject_box: "
    "[ymin, xmin, ymax, xmax] as integers from 0 to 1000 around ALL of the subject (the whole statue from head to "
    "base, both statues of a pair, the whole ring system, the whole folio), as tight as it can be without cutting any of it. "
    "A part that catches the eye is not the subject when the subject is the whole thing (an oculus in a dome, a "
    "marker on a map, a face on a statue). not_subject: things in the picture that are NOT the subject and must not "
    "be taken for it: part of the aircraft or spacecraft the photo was taken from, tourists or a crowd, a museum label "
    "board or placard, a scale bar, colour chart or grey scale, a watermark or logo, a page margin or scan edge; each "
    "with name and box ([ymin, xmin, ymax, xmax], 0-1000); [] when there are none. subject_confidence: from 0 to 1, how "
    "sure you are that subject_box holds all of the subject and little else.")


class NotSubject(BaseModel):
    name: str
    box: List[float]


def _every_field_required(schema: Dict[str, Any]) -> None:
    schema["required"] = list(schema.get("properties", {}))


def verdict_schema(base: type) -> type:
    """The picture check's answer model with the subject fields added (every field required, as the base's are)."""

    class SubjectVerdict(base):
        model_config = ConfigDict(json_schema_extra=_every_field_required)
        subject_name: str = Field("", description="The subject in a few words, as the picture shows it.")
        scene: bool = False
        subject_box: List[float] = []   # [ymin, xmin, ymax, xmax] around all of the subject, 0-1000
        not_subject: List[NotSubject] = []
        subject_confidence: float = 0.0

    SubjectVerdict.__name__ = SubjectVerdict.__qualname__ = "SubjectVerdict"
    return SubjectVerdict


def _clip(text: Any) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= CONTEXT_CHARS else text[:CONTEXT_CHARS].rsplit(" ", 1)[0] + " ..."


def context_text(context: Optional[Dict[str, Any]]) -> str:
    """The context lines added to the question: topic, title, caption, credit, the slide's claim (those present)."""
    context = context or {}
    lines = [f"{label}: {_clip(context[key])}" for key, label in
             (("topic", "Documentary topic"), ("title", "Photograph title"), ("caption", "Caption"),
              ("credit", "Credit"), ("claim", "The slide says"))
             if _clip(context.get(key))]
    return ("\n" + "\n".join(lines)) if lines else ""


def _xyxy(box: Any) -> List[float]:
    """[ymin, xmin, ymax, xmax] in 0-1000 -> [x0, y0, x1, y1] in 0-1, or [] when it is not a box inside the picture."""
    box = list(box or [])
    if len(box) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box):
        return []
    y0, x0, y1, x1 = (float(v) for v in box)
    if not all(0 <= v <= 1000 for v in (y0, x0, y1, x1)) or y0 >= y1 or x0 >= x1:
        return []
    return [round(x0 / 1000, 4), round(y0 / 1000, 4), round(x1 / 1000, 4), round(y1 / 1000, 4)]


def _area(b: List[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def subject_record(verdict: Any) -> Dict[str, Any]:
    """The subject part of an answer, validated mechanically: {"version", "name", "scene", "box", "confidence",
    "not_subject", "problem", "asked_box"}. `box` is [x0, y0, x1, y1] in 0-1 ([0, 0, 1, 1] for a scene), or [] with `problem`
    saying why the answer cannot stand: no box, a box outside the picture or inverted, a degenerate box (a side under
    MIN_SIDE), the whole frame for something that is not a scene, a confidence outside 0-1, a name that cannot stand.
    A not_subject entry that is not a valid box is dropped and counted ("not_subject_dropped")."""
    name = " ".join(str(getattr(verdict, "subject_name", "") or "").split())
    scene = bool(getattr(verdict, "scene", False))
    raw = getattr(verdict, "subject_box", None)
    box = _xyxy(raw)
    try:
        confidence = float(getattr(verdict, "subject_confidence", 0.0))
    except (TypeError, ValueError):
        confidence = -1.0
    kept, dropped = [], 0
    for item in getattr(verdict, "not_subject", None) or []:
        nb = _xyxy(getattr(item, "box", None) if not isinstance(item, dict) else item.get("box"))
        nm = " ".join(str(getattr(item, "name", "") if not isinstance(item, dict) else item.get("name", "")).split())
        if nb and nm:
            kept.append({"name": nm, "box": nb})
        else:
            dropped += 1
    problem, asked_box = "", box
    if scene:
        box = [0.0, 0.0, 1.0, 1.0]   # a scene's subject is the whole view, whatever box came with it
    elif not box:
        problem = "no subject box inside the picture" if raw else "no subject box"
    elif min(box[2] - box[0], box[3] - box[1]) < MIN_SIDE:
        problem = "the subject box is degenerate (a side under 1 % of the picture)"
    elif _area(box) >= WHOLE_FRAME:
        problem = "the subject box is the whole frame but the picture is not called a scene"
    if not problem and not 0.0 <= confidence <= 1.0:
        problem = "the confidence is not between 0 and 1"
    if not problem:
        problem = ("no subject named" if not name else degenerate_reason(name))
    return {"version": VERSION, "name": name, "scene": scene, "box": [] if problem else box, "asked_box": asked_box,
            "confidence": confidence if 0.0 <= confidence <= 1.0 else None, "not_subject": kept,
            **({"not_subject_dropped": dropped} if dropped else {}), "problem": problem}
