"""
src/models.py
=============
Typed contracts shared by the script engine, fact checker, and renderers.
The same schema constrains Claude's structured output and validates curated scripts.
"""

from typing import Annotated, List, Literal

from pydantic import BaseModel, BeforeValidator


class ThumbnailCopy(BaseModel):
    eyebrow: str   # small badge above the title, e.g. "LOST ENGINEERING"
    line1: str     # white title line
    line2: str     # gold title line
    tagline: str   # badge below the title


class StorySlide(BaseModel):
    kicker: str
    line1: str
    line2: str
    body: str


class Poll(BaseModel):
    question: str
    options: List[str]


class CarouselSlide(BaseModel):
    heading: str
    body: str


class Source(BaseModel):
    title: str
    url: str


class ScriptPackage(BaseModel):
    title: str
    hook_sentence: str
    narration_script: str
    closing_sentence: str
    caption: str
    first_comment: str
    hashtags: List[str]
    thumbnail: ThumbnailCopy
    story_slides: List[StorySlide]
    poll: Poll
    carousel_slides: List[CarouselSlide]
    sources: List[Source]


# Structured outputs may vary enum casing ("Supported"), so normalize before validating.
Verdict = Annotated[Literal["supported", "disputed", "unsupported", "false"], BeforeValidator(lambda v: str(v).strip().lower())]


class ClaimAudit(BaseModel):
    claim: str
    verdict: Verdict
    hedged_in_script: bool   # does the script itself present this as uncertain/estimated?
    note: str
    sources: List[Source]


class FactCheckReport(BaseModel):
    claims: List[ClaimAudit]
    summary: str
