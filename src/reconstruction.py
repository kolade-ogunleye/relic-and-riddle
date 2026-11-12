"""A reconstruction exception requires a structured pre-voice verdict for the sentence the beat is spoken in, placed
on the beat by word position."""

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool

ReconstructionKind = Literal["original_appearance", "unphotographed_interior", "mechanism_in_use", "construction"]
KINDS = {"original_appearance", "unphotographed_interior", "mechanism_in_use", "construction"}


class ReconstructionAssessment(BaseModel):
    reconstruction: StrictBool = False
    reconstruction_kind: ReconstructionKind | None = None
    reconstruction_reason: str = Field(default="", max_length=400)


def _norm(text: Any) -> str:
    """Lower case, punctuation as spaces, single spaces: picture_coverage._norm, the normalisation the word
    timeline is aligned with the script's sentences by (picture_coverage.beat_sentences)."""
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in str(text or "")).split())


def bind(verdict: Any) -> dict[str, Any]:
    """The spec fields that carry a verdict into generation and the reference comparison: the verdict itself and
    its own sentence (the sentence the beat is spoken in), stored separately so approved() can check them against
    each other exactly. Empty when there is no verdict to bind."""
    if not isinstance(verdict, dict):
        return {}
    return {"reconstruction_verdict": dict(verdict), "reconstruction_sentence": str(verdict.get("sentence") or "")}


def spec_approved(line: str, spec: Any) -> bool:
    """approved() for a generation spec: the verdict bound into it (bind) and its stored sentence, which must be
    present and equal the verdict's own sentence exactly (fail closed without it)."""
    if not isinstance(spec, dict):
        return False
    return approved(line, spec.get("reconstruction_verdict"), str(spec.get("reconstruction_sentence") or ""))


def _placed(line: str, verdict: dict[str, Any]) -> bool:
    """Whether the verdict was placed on this very beat by word position (picture_coverage.placed_verdicts): never
    by searching sentence text for the beat's words, which a short beat can share with another sentence."""
    from .picture_coverage import PLACEMENT_BY, PLACEMENT_KEY
    placement = verdict.get(PLACEMENT_KEY)
    if not isinstance(placement, dict) or placement.get("by") != PLACEMENT_BY:
        return False
    index = placement.get("sentence_index")
    beat = placement.get("beat")
    return (isinstance(index, int) and not isinstance(index, bool) and index >= 0 and isinstance(beat, str)
            and bool(beat.split()) and beat.split() == str(line or "").split())


def _has_reason(reason: Any) -> bool:
    """A stated reason: at most 400 characters with at least one word of three or more letters or digits."""
    return (isinstance(reason, str) and 0 < len(reason.strip()) <= 400
            and any(len(t) >= 3 and any(c.isalpha() for c in t) for t in _norm(reason).split()))


def approved(line: str, verdict: Any, sentence: str | None = None) -> bool:
    """Whether the structured pre-voice verdict grants beat `line` the reconstruction exception. The verdict must
    be the one of the sentence the beat is spoken in, placed on this beat by word position
    (picture_coverage.placed_verdicts: the beat's word offsets in the voiceover's word timeline, aligned with the
    script's sentences); a verdict without that placement, or placed on another beat, grants nothing. The beat's
    words must also lie inside that sentence (normalised, whole words), and `sentence`, the sentence stored with
    the verdict (bind), must equal the verdict's own sentence exactly. Missing, stale, ambiguous or contradictory
    evidence never grants the exception."""
    if not isinstance(verdict, dict) or not _placed(line, verdict):
        return False
    own = " ".join(str(verdict.get("sentence") or "").split())
    if not own or (sentence is not None and " ".join(str(sentence or "").split()) != own):
        return False
    beat = _norm(line)
    return bool(beat and f" {beat} " in f" {_norm(own)} "
                and verdict.get("reconstruction") is True
                and isinstance(verdict.get("reconstruction_kind"), str)
                and verdict.get("reconstruction_kind") in KINDS
                and _has_reason(verdict.get("reconstruction_reason"))
                and verdict.get("kind") in ("generable", "unshown")
                and verdict.get("visual") in ("named_subject", "kind_of_thing"))
