"""A reconstruction exception requires a structured pre-voice verdict for the sentence the beat is spoken in, placed
on the beat by word position.

The grant is structural, never prose (QF-60b): the verdict names its kind, a cause from a fixed list that the kind
allows (COMPATIBLE) and the words of its own sentence that name what is reconstructed (subject words, placed in that
sentence as whole words). The free-text reconstruction_reason is kept for the human reviewer only: it must be
present (QF-60's check, unchanged) but its wording never grants or refuses anything."""

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, field_validator

ReconstructionKind = Literal["original_appearance", "unphotographed_interior", "mechanism_in_use", "construction"]
KINDS = {"original_appearance", "unphotographed_interior", "mechanism_in_use", "construction"}

# Why no photograph can show it: one of a fixed list, never prose (QF-60b, operator decision A).
ReconstructionCause = Literal["lost_or_destroyed", "sealed_or_inaccessible", "never_photographed",
                              "before_photography", "no_longer_operating"]
CAUSES = {
    "lost_or_destroyed": "the thing, or its original state, is lost, destroyed or changed",
    "sealed_or_inaccessible": "it is sealed, buried, flooded or otherwise out of reach",
    "never_photographed": "no photograph of it was ever taken",
    "before_photography": ("the building or making happened before photography began in 1826, so no photograph "
                           "of it can exist (construction only, never a thing that still stands or works)"),
    "no_longer_operating": "it no longer works; it is broken, seized or survives only in fragments",
}
# The causes each kind allows: the cause must explain why no photograph can show that kind of view.
# - original_appearance: the original look is gone or changed (lost_or_destroyed). "Before photography" is true of
#   any ancient subject, even one whose look survives unchanged, so it is no reason; "sealed" or "never
#   photographed" says nothing about a look that may still be there.
# - unphotographed_interior: the interior is out of reach (sealed_or_inaccessible) or was never photographed
#   (never_photographed), the kind's own definition. A lost or older interior is never_photographed or
#   original_appearance; "no longer operating" is no cause for a room.
# - mechanism_in_use: it no longer works (no_longer_operating) or is lost (lost_or_destroyed). "Before
#   photography" is true of a mechanism that still runs today, so it is no reason; "sealed" or "never
#   photographed" does not say it cannot be seen working.
# - construction: the building process is transient whatever happens to the building later, so only a process
#   before photography (before_photography) lacks a photograph for certain; a later building's loss does not
#   (construction photographs of since-destroyed buildings exist). Fail closed.
COMPATIBLE = {
    "original_appearance": ("lost_or_destroyed",),
    "unphotographed_interior": ("sealed_or_inaccessible", "never_photographed"),
    "mechanism_in_use": ("lost_or_destroyed", "no_longer_operating"),
    "construction": ("before_photography",),
}
# A reviewer's reason that is only a placeholder (round 1's list, matched after _norm) has nothing to show.
PLACEHOLDER_REASONS = frozenset({
    "none", "null", "nil", "nan", "undefined", "false", "true", "unknown", "n a", "na", "tbd", "tba", "todo", "x",
    "no reason", "no reason given", "not applicable", "not available", "same as above", "see above", "as above",
    "ditto", "reason", "the reason", "placeholder", "lorem ipsum", "reconstruction", "no photo", "no photograph",
})
SUBJECT_MAX_WORDS = 8     # a noun phrase of the sentence, not the sentence copied whole


def _wordset(text: str) -> frozenset[str]:
    return frozenset(text.split())


# Words that never name a thing on their own: function words, the kinds' own words and the vocabulary of photos.
_STOP_WORDS = _wordset("""
a an the this that these those it its itself there here then than which who whom whose what when where why how
and or but nor so as if because since while though although yet also only just even still very too
of in on at to for from by with without within about into onto over under upon through across between before
after during until against among per via out off up down beside behind beneath beyond around near along toward
towards is are was were be been being am do does did done has have had having can cannot could would should may
might must will shall not no never none nothing any some all each every both either neither one ones more most
less least much many such other another same own i we you he she they me us him her them my our your his their
once ago now today long ever thing things something time way part
reconstruction reconstructed original originally appearance inside interior interiors construction use used using
photo photos photograph photographs photography picture pictures image images view scene
""")


class ReconstructionAssessment(BaseModel):
    reconstruction: StrictBool = False
    reconstruction_kind: ReconstructionKind | None = None
    reconstruction_cause: ReconstructionCause | None = None
    reconstruction_subject_words: str = ""      # copied from the sentence: the words naming what is reconstructed
    reconstruction_reason: str = Field(default="", max_length=400)    # for the reviewer only, never the grant test

    @field_validator("reconstruction_cause", mode="before")
    @classmethod
    def _cause_from_the_list(cls, value: Any) -> Any:
        """A cause outside the list is no cause (refused at the grant), not a failed answer for every sentence."""
        return value if isinstance(value, str) and value in CAUSES else None

    @field_validator("reconstruction_subject_words", mode="before")
    @classmethod
    def _subject_text(cls, value: Any) -> Any:
        """Subject words that are not text are no subject words (refused at the grant)."""
        return value if isinstance(value, str) else ""


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
    """A reason the reviewer can read: at most 400 characters with at least one word of three or more letters or
    digits (QF-60's check, unchanged), and not a bare placeholder (PLACEHOLDER_REASONS, after _norm: "none", "N/A",
    "TBD", "no reason given"). A display sanity filter only: it can refuse, never grant; the reason's wording is
    otherwise never tested (QF-60b: the prose checks of rounds 1-3 are gone, the structure decides)."""
    return (isinstance(reason, str) and 0 < len(reason.strip()) <= 400 and _norm(reason) not in PLACEHOLDER_REASONS
            and any(len(t) >= 3 and any(c.isalpha() for c in t) for t in _norm(reason).split()))


def cause_allowed(kind: Any, cause: Any) -> bool:
    """Whether `cause` is one of the fixed causes (CAUSES) and one that `kind` allows (COMPATIBLE). Exact strings
    only: another spelling, case or type is no cause."""
    return (isinstance(kind, str) and isinstance(cause, str) and kind in COMPATIBLE and cause in CAUSES
            and cause in COMPATIBLE[kind])


def subject_placed(subject: Any, sentence: Any) -> bool:
    """Whether the subject words are words of `sentence` (the verdict's own sentence) in its order, whole words
    after the same normalisation the beat is placed by (_norm, as QF-60 places a beat inside its sentence), at most
    SUBJECT_MAX_WORDS of them, and at least one names something: three or more characters with a letter and not a
    stop word (_STOP_WORDS: function words, the kinds' own words, photo words)."""
    if not isinstance(subject, str):
        return False
    words = _norm(subject).split()
    own = _norm(sentence)
    return (0 < len(words) <= SUBJECT_MAX_WORDS and bool(own) and f" {' '.join(words)} " in f" {own} "
            and any(len(w) >= 3 and any(c.isalpha() for c in w) and w not in _STOP_WORDS for w in words))


def structured(verdict: Any, sentence: Any) -> bool:
    """The structural grant test of a verdict on its own `sentence`: reconstruction exactly True, a known kind, a
    cause from the fixed list that the kind allows (cause_allowed), subject words placed in the sentence
    (subject_placed) and a reason present for the reviewer (_has_reason; its wording is not tested). Anything
    missing, mistyped or invalid refuses (the strict nothing-added rule stays)."""
    if not isinstance(verdict, dict):
        return False
    kind = verdict.get("reconstruction_kind")
    return (verdict.get("reconstruction") is True and isinstance(kind, str) and kind in KINDS
            and cause_allowed(kind, verdict.get("reconstruction_cause"))
            and subject_placed(verdict.get("reconstruction_subject_words"), sentence)
            and _has_reason(verdict.get("reconstruction_reason")))


def prompt_fields() -> str:
    """The cause list, the kinds' allowed causes and the subject words as the pre-voice prompt asks for them
    (prevoice_lines.SYSTEM), written from CAUSES, COMPATIBLE and SUBJECT_MAX_WORDS so the prompt and the grant test
    never differ."""
    causes = "; ".join(f"{name} ({text})" for name, text in CAUSES.items())
    allowed = "; ".join(f"{kind}: {' or '.join(names)}" for kind, names in COMPATIBLE.items())
    return (f"reconstruction_cause: exactly one of: {causes}. Each kind allows only these causes: {allowed}. "
            "When no allowed cause is true, the sentence is not a reconstruction (false).\n"
            "reconstruction_subject_words: the words of the sentence that name the thing reconstructed, copied "
            f"exactly as they stand in the sentence, at most {SUBJECT_MAX_WORDS} words.")


def approved(line: str, verdict: Any, sentence: str | None = None) -> bool:
    """Whether the structured pre-voice verdict grants beat `line` the reconstruction exception. The verdict must
    be the one of the sentence the beat is spoken in, placed on this beat by word position
    (picture_coverage.placed_verdicts: the beat's word offsets in the voiceover's word timeline, aligned with the
    script's sentences); a verdict without that placement, or placed on another beat, grants nothing. The beat's
    words must also lie inside that sentence (normalised, whole words), and `sentence`, the sentence stored with
    the verdict (bind), must equal the verdict's own sentence exactly. The reconstruction itself is granted only
    by its structure (structured: kind, an allowed cause, subject words of the sentence), never by its prose.
    The one check of the planner, the generation gate and the nothing-added rule. Missing, stale, ambiguous or
    contradictory evidence never grants the exception."""
    if not isinstance(verdict, dict) or not _placed(line, verdict):
        return False
    own = " ".join(str(verdict.get("sentence") or "").split())
    if not own or (sentence is not None and " ".join(str(sentence or "").split()) != own):
        return False
    beat = _norm(line)
    return bool(beat and f" {beat} " in f" {_norm(own)} "
                and structured(verdict, own)
                and verdict.get("kind") in ("generable", "unshown")
                and verdict.get("visual") in ("named_subject", "kind_of_thing"))
