"""
src/models.py
=============
Typed contracts shared by the script engine, fact checker, and renderers.
The same schema constrains Claude's structured output and validates curated scripts.
"""

from typing import Annotated, List, Literal, Optional

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


class TimelineEvent(BaseModel):
    year: int                 # negative for BC
    approximate: bool         # shown as "c."
    label: str                # at most 40 characters


class ScaleFact(BaseModel):
    value: float
    unit: Literal["ft", "m"]
    dimension: Literal["depth", "height", "length"]
    subject: str              # e.g. "Derinkuyu's depth"; at most 28 characters


class StatCard(BaseModel):
    value: str                # as shown, e.g. "20,000" or "280 ft"
    label: str                # at most 40 characters, e.g. "people it could shelter"


class SectionLevel(BaseModel):
    depth_ft: float
    label: str                # at most 32 characters


class CrossSection(BaseModel):
    subject: str              # e.g. "Derinkuyu"; at most 28 characters
    total_depth_ft: float
    levels: List[SectionLevel]  # 2-5 sourced levels or features, top to bottom


class RouteStop(BaseModel):
    place: str                # a findable place name (city, island, site)
    country: str
    label: str                # at most 32 characters, e.g. "Built in Rhodes?" or "1902: found by divers"


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
    timeline: Optional[List[TimelineEvent]] = None   # 2-4 dated events for the timeline graphic
    scale_fact: Optional[ScaleFact] = None           # one well-sourced size for the scale graphic
    stat_cards: Optional[List[StatCard]] = None      # 1-2 headline figures for animated number cards
    cross_section: Optional[CrossSection] = None     # underground or vertical structures with sourced depths
    route: Optional[List[RouteStop]] = None          # 2-5 stops of a journey the story describes
    photo_labels: Optional[List[str]] = None         # 2-4 visible features named in the narration, to label on a photo
    emphasis_words: Optional[List[str]] = None  # 1-3 key words/names from the narration, emphasised on screen
    carousel_caption: Optional[str] = None  # its own text: Meta/Upload-Post flag duplicate captions


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


class BeatQuery(BaseModel):
    beat: int            # 1-based beat number
    query: str           # Wikimedia Commons search for the concrete subject of that beat
    keywords: List[str]  # lowercase words, one of which a relevant file title must contain


class BeatQueries(BaseModel):
    items: List[BeatQuery]
