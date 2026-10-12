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


class PolicyChange(BaseModel):
    date: str            # when the change was announced or took effect (YYYY-MM-DD if known)
    area: str            # e.g. "Content monetization", "Community Standards", "AI labeling", "Graph API"
    summary: str
    impact: str          # what it means for a faceless, AI-assisted history Reels channel
    action_needed: bool
    action: str          # what to do, or "" if nothing
    source_url: str


class PolicyDigest(BaseModel):
    changes: List[PolicyChange]


class ScenePlan(BaseModel):
    images: List[int]    # one 1-based image number per narration beat, in order
    notes: str


Severity = Annotated[Literal["minor", "major", "critical"], BeforeValidator(lambda v: str(v).strip().lower())]


class QAIssue(BaseModel):
    beat: int            # 1-based beat number the frame came from
    severity: Severity
    issue: str


class QAReport(BaseModel):
    issues: List[QAIssue]
    summary: str


class ProposedLocation(BaseModel):
    name: str
    city: str
    region: str
    country: str


class TopicProposal(BaseModel):
    slug: str                 # lowercase_with_underscores, e.g. "baghdad_battery"
    title: str
    sub_theme: str
    hook: str
    location: ProposedLocation
    key_facts: List[str]
    hashtags: List[str]
    image_queries: List[str]  # Wikimedia Commons searches
    image_keywords: List[str] # words a relevant file title must contain
    why_it_works: str
    sources: List[Source]


class TopicProposals(BaseModel):
    topics: List[TopicProposal]
