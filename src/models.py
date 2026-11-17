"""
src/models.py
=============
Typed contracts shared by the script engine, fact checker, and renderers.
The same schema constrains Claude's structured output and validates curated scripts.
"""

from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


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
    approximate: bool = True  # the narration hedges it ("about", "up to"): shown as "ABOUT ..."


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
    approximate: bool = True    # depths are estimates in the sources: shown with "ABOUT"


class RouteStop(BaseModel):
    place: str                # a findable place name (city, island, site)
    country: str
    label: str                # at most 32 characters, e.g. "Built in Rhodes?" or "1902: found by divers"


QuantityQualifier = Literal["exact", "approximate", "minimum", "maximum", "range", "per_unit", "comparison_set"]


class DraftCount(BaseModel):
    """How many of one physical thing the fact states (QF-61), as the fact-sheet writer proposes it. Checked by
    src/fact_quantities.py before it is stored; only an "exact" count the statement states as exact is ever drawn."""
    element: str              # the counted thing's singular noun, in the fact's own word ("mast")
    count: int                # the whole number the fact states
    qualifier: QuantityQualifier
    source: str               # the statement's own words holding the number and the noun ("three tall masts")


class DraftMeasure(BaseModel):
    """One size, distance or weight the fact states (QF-61), checked like a count (src/fact_quantities.py)."""
    element: str              # what is measured ("ship")
    measure: str              # the statement's own words: the number and its unit ("34 cm")
    qualifier: QuantityQualifier


class DraftFact(BaseModel):
    """A fact as the fact-sheet writer proposes it, citing evidence by number."""
    id: str                   # short id, e.g. "F3"
    statement: str            # one checkable fact, worded exactly as it may be said on screen
    kind: Literal["number", "date", "name", "event", "other"]
    quote_ids: List[int]      # 1-based numbers of the evidence quotes that support it
    disputed: bool            # do the quotes (or sources) disagree?
    hedge: str                # if disputed or estimated: the safe wording (e.g. "about 85 m; sources range 60-85 m"); else ""
    counts: list[DraftCount] = []      # QF-61: structured counts of physical things the statement states
    measures: list[DraftMeasure] = []  # QF-61: structured measures the statement states


CARD_TEXT_MAX = 84   # characters: a card-ready statement or a myth's correction


class DraftCardStatement(BaseModel):
    """A short card-ready statement the fact-sheet writer proposes: one of its own facts, said in at most 84 characters."""
    id: str                   # short id, e.g. "S1"
    text: str                 # at most 84 characters; no claim the fact does not make
    fact_id: str              # the draft fact it restates


class DraftCardMyth(BaseModel):
    id: str                   # e.g. "M1"
    myth: str                 # the misconception, worded as people state it
    verdict: Literal["false", "misleading"]
    refuting_fact_id: str     # the draft fact that refutes it
    correction: str           # at most 84 characters, said from the refuting fact
    quote_id: int             # 1-based number of the evidence quote that is its source


class Speaker(BaseModel):
    name: str                 # e.g. "Theodore Monod"
    role: str = ""            # e.g. "Geologist"; "" when the evidence gives none


class DraftCardQuote(BaseModel):
    id: str                   # e.g. "Q1"
    quotation: str            # the exact words between the evidence's quotation marks
    speaker: Speaker
    work: str = ""            # the work or source it was said in; "" when the evidence gives none
    date: Optional[str] = None  # as the evidence dates it (e.g. "1940"), else null
    fact_id: str              # the draft fact whose evidence holds the quotation
    quote_id: int             # 1-based number of that evidence quote


class DraftCardFields(BaseModel):
    statements: List[DraftCardStatement] = []
    myths: List[DraftCardMyth] = []
    quotes: List[DraftCardQuote] = []


class FactSheetDraft(BaseModel):
    facts: List[DraftFact]
    cards: Optional[DraftCardFields] = None   # card-ready copy (src/card_fields.py checks it before any card uses it)


# ------------------------------------------------------------------ card fields as stored on a fact sheet
# The design cards (src/design_cards.py) draw only these fields, each with a verdict from the project's own fact
# checker (trace_check.check and trace_check.evidence_check) recorded beside it (src/card_fields.py).
CardVerdict = Literal["supported", "unsupported", "contradicted", "needs_check"]


class CheckRun(BaseModel):
    checker: Literal["trace_check", "evidence_check"]
    text: str                 # the exact text the checker was given
    model: str                # the model the checker ran on
    passed: bool              # the checker's own pass/fail for this text
    audits: List[dict]        # the checker's own claim audits, as it returned them
    summary: str
    signature: str = ""       # HMAC-SHA256 made when the checker returned (src/record_signing.py, "check_run")


class CardCheck(BaseModel):
    verdict: CardVerdict                  # "supported" is the only usable verdict
    parts: dict = {}                      # per checked text: {"text": ...} or, for a myth, {"myth": ..., "correction": ...}
    runs: List[CheckRun] = []             # the checker runs the verdict was derived from (empty when needs_check)
    findings: List[str] = []              # deterministic findings (lengths, verbatim, attribution); empty when clean
    checked_at: str = ""
    facts_sha256: str = ""                # the sheet's facts the check was made against
    field_sha256: str = ""                # the field's own content
    sha256: str = ""                      # over everything above (card_fields.check_hash)
    signature: str = ""                   # HMAC-SHA256 over the field and this record (record_signing, "card_check")


class CardStatement(BaseModel):
    id: str
    text: str = Field(max_length=CARD_TEXT_MAX)
    fact_id: str
    check: Optional[CardCheck] = None


class CardMyth(BaseModel):
    id: str
    myth: str
    verdict: Literal["false", "misleading"]
    refuting_fact_id: str
    correction: str = Field(max_length=CARD_TEXT_MAX)
    source: Source
    check: Optional[CardCheck] = None


class CardQuote(BaseModel):
    id: str
    quotation: str
    speaker: Speaker
    work: str = ""
    date: Optional[str] = None
    source_url: str
    evidence: str             # the fact's evidence string the quotation appears in verbatim
    fact_id: str              # the fact that evidence belongs to
    check: Optional[CardCheck] = None


class FactSheetCards(BaseModel):
    statements: List[CardStatement] = []
    myths: List[CardMyth] = []
    quotes: List[CardQuote] = []
    dropped: List[dict] = []  # proposed fields the deterministic rules dropped, with why


class SentenceTrace(BaseModel):
    sentence: str
    fact_ids: List[str] = []
    trace_type: Literal["domain_fact", "open_historical_debate", "epistemic_negation", "rhetorical_framing"] = "domain_fact"


def _payoff_required(schema: dict) -> None:
    """The writer's schema asks for payoff_sentence (QF-15), though a payload saved without it still parses."""
    required = schema.setdefault("required", [])
    if "payoff_sentence" not in required:
        required.append("payoff_sentence")


class ScriptCore(BaseModel):
    """What the script writer produces in one structured call."""
    model_config = ConfigDict(json_schema_extra=_payoff_required)
    title: str
    hook_sentence: str
    narration_script: str
    closing_sentence: str
    # QF-15: the narration's penultimate sentence, word for word, which answers the hook and names one of its nouns
    # (script_engine.payoff_problems). Required of the writer (_payoff_required); a payload saved before the rule
    # parses with "" (or a None) so it can be repaired, and the missing payoff is a blocking finding, never filled in.
    payoff_sentence: Annotated[str, BeforeValidator(lambda v: "" if v is None else v)] = ""
    caption: str
    first_comment: str
    hashtags: List[str]
    thumbnail: ThumbnailCopy
    story_slides: List[StorySlide]
    poll: Poll
    carousel_slides: List[CarouselSlide]
    sources: List[Source]
    carousel_caption: Optional[str] = None  # its own text: Meta/Upload-Post flag duplicate captions
    # One warm sentence asking viewers to share this with someone and to follow for the next story (Meta's
    # creator tips, 2026-10-25): shown at the end of the video, in both captions and on the last carousel
    # slide. Optional: older and curated scripts get a plain line (script_engine.share_prompt).
    share_line: Optional[str] = None
    sentence_trace: Optional[List[SentenceTrace]] = None


class GraphicsPlan(BaseModel):
    """Planned in a second call from the finished script (one schema with everything was too large
    for structured outputs: "compiled grammar is too large")."""
    timeline: Optional[List[TimelineEvent]] = None   # 2-4 dated events for the timeline graphic
    scale_fact: Optional[ScaleFact] = None           # one well-sourced size for the scale graphic
    stat_cards: Optional[List[StatCard]] = None      # 1-2 headline figures for animated number cards
    cross_section: Optional[CrossSection] = None     # underground or vertical structures with sourced depths
    route: Optional[List[RouteStop]] = None          # 2-5 stops of a journey the story describes
    photo_labels: Optional[List[str]] = None         # 2-4 visible features named in the narration, to label on a photo
    emphasis_words: Optional[List[str]] = None       # 1-3 key words/names from the narration, emphasised on screen


class ScriptPackage(ScriptCore, GraphicsPlan):
    """The full package: the script plus its graphics plan (validated and fact-checked together)."""


class DocumentaryAct(BaseModel):
    act_number: int                          # 1 to 5
    act_title: str                           # e.g. "The 1963 Basement Breach"
    target_duration_sec: int                 # 35 to 75 seconds per act
    narration_text: str                      # ~100-160 words
    visual_intent: str                       # visual description for scene selector
    graphic_type: Optional[Literal["map", "blueprint_callout", "cross_section", "timeline", "numbers", "none"]] = "none"
    claim_fact_ids: List[str] = []           # e.g. ["F1", "F3", "F5"] from fact sheet


class DeepDivePackage(BaseModel):
    """The full 5-act long-form documentary script package (~3:30 to 5:00 minutes)."""
    topic_id: str
    title: str                               # e.g. "The Underground City of Derinkuyu"
    youtube_title: str                       # e.g. "The 18-Story Subterranean Metropolis: How Derinkuyu Protected 20,000 People"
    description: str                         # Multi-paragraph YouTube / Facebook In-Stream description with chapter timestamps
    tags: List[str]                          # SEO tags
    acts: List[DocumentaryAct]               # Exactly 5 acts
    closing_debate_question: str             # High-engagement MSI closing question
    sources: List[Source]

    @property
    def total_words(self) -> int:
        return sum(len(a.narration_text.split()) for a in self.acts)

    @property
    def full_narration(self) -> str:
        return "\n\n".join(f"[Act {a.act_number}: {a.act_title}]\n{a.narration_text}" for a in self.acts)



# Structured outputs may vary enum casing ("Supported"), so normalize before validating.
Verdict = Annotated[Literal["supported", "disputed", "unsupported", "false"], BeforeValidator(lambda v: str(v).strip().lower())]
# A claim that passed, in each checker's own word: the fact checker's "supported", the trace check's
# "traced", the second check's "supported"/"hedged_ok". The card counted only "supported", so 23 traced
# claims showed as "Integrity Alert: 23 disputed/hedged" (production, 2026-10-23).
GOOD_VERDICTS = frozenset({"supported", "traced", "hedged_ok"})


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
    # QF-73: the broad geographic region, and the reasons for editorial care ([] after assessing none). A proposal
    # without an assessment counts as sensitive (topic_policy.UNASSESSED) until you edit the bank.
    region_group: str = "unclassified"
    sensitivity: list[str] = Field(default_factory=lambda: ["Editorial sensitivity assessment pending"])


class TopicProposals(BaseModel):
    topics: List[TopicProposal]


class BeatQuery(BaseModel):
    beat: int            # 1-based beat number
    query: str           # Wikimedia Commons search for the concrete subject of that beat
    keywords: List[str]  # lowercase words, one of which a relevant file title must contain
    place: Optional[str] = None  # the named place the subject is at, when it is not the documentary's own site
    # The sentence is about something that is not the documentary's subject or site (modern concrete in a Roman
    # concrete video, 2026-10-30): then `broad` is a 2-3 word general search for it, asked outside the topic's
    # category. False for anything about the subject itself, however generic its words.
    outside_topic: bool = False
    broad: Optional[str] = None


class BeatQueries(BaseModel):
    items: List[BeatQuery]
