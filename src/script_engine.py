"""
src/script_engine.py
====================
Produces the complete script package for one Reel:
1. 5-beat, 42-48 second narration (hook -> evidence -> hypotheses -> relevance -> loop-back takeaway),
   measured by speaking it before the real voiceover.
2. All on-screen copy (thumbnail, 3 story frames, poll, 7 carousel slides) and Meta metadata.
3. Stage 2.5 guardrail: structural checks + search-grounded claim verification.

Source of scripts, in order:
  a) The configured writer (free Gemini engine, or Claude with web search), from the topic's verified
     fact sheet, returning schema-validated JSON.
  b) A human-curated script in data/scripts/<topic_id>.json.
There is deliberately no template fallback: a topic with neither fails the run.
"""

import difflib
import json
import logging
import math
import re
import threading
import time
from typing import Callable, Dict, Any, List, NamedTuple, Optional, Tuple

from pydantic import BaseModel, ValidationError

from .config import (CURATED_SCRIPTS_DIR, HARD_DURATION_CEILING, SECOND_CHECK_ENABLED, TARGET_DURATION_MAX,
                     TARGET_DURATION_MIN, TARGET_WORD_COUNT_MIN, TARGET_WORD_COUNT_MAX,
                     WORD_COUNT_CEILING, WORD_COUNT_FLOOR)
from . import audio_engine, config
from .fact_checker import ClaimFactChecker
from . import free_llm
from .llm import LLMError, claude_available, format_sources, research, structured_call, task as llm_task
from . import fact_sheet, improve, trace_check
from .models import (CarouselSlide, DeepDivePackage, DocumentaryAct, GraphicsPlan, Poll, ScriptCore, ScriptPackage,
                     Source, StorySlide, ThumbnailCopy)


log = logging.getLogger(__name__)


class ScriptUnavailableError(RuntimeError):
    pass


HOOK_MAX_WORDS = 14  # the first sentence lands inside the two seconds viewers decide in (hook_problems)

SYSTEM_PROMPT = """You are a documentary scriptwriter producing short vertical Reels (1080x1920) for Facebook and Instagram in the "Historical Curiosities & Ancient Marvels" niche. Accuracy is the brand: viewers and Meta's integrity systems punish fabricated or exaggerated history, so every factual statement you write must be something you have confirmed with web search.

Editorial rules:
- Narration is {WORDS_MIN}-{WORDS_MAX} spoken words (about {SECONDS_MIN}-{SECONDS_MAX} seconds spoken). Hard maximum {WORDS_MAX}. Long words take longer to say: prefer plain, short words, or the script runs long even inside the word range.
- Exactly five beats, tailored to the topic archetype (Knowledge Bomb & Forensic Reality schema):
  (1) Category-Aware Cinematic Hook in the first 3 seconds (Beat 1):
      - Megastructures: Impossible Architectural Contrast or Subterranean Paradox (e.g. ancient structures enduring while modern ones crumble, or whole communities living deep underground).
      - Relic Artifacts: Precision Beyond Its Era (micro-mechanics, astronomical gear trains, or metallurgy more refined than its period is known for), stated as documented fact, never as "lost" or impossible technology.
      - Riddle Events & Cataclysms: Sudden Forensic Anomaly (stark physical devastation, colossal atmospheric shockwave, zero impact crater, or vanished expeditions).
      NEVER start Beat 1 with greetings ('Hey guys'), generic rhetorical questions ('Have you ever wondered'), or robotic hedge words ('Estimated,', 'Approximately,'). Hook the audience immediately with an arresting, tangible reality.
      Lead with the mystery: viewers decide in under two seconds. The first sentence is at most {HOOK_WORDS} words and delivers the impossible fact, or poses the riddle, within its first 8 words: a stark claim carrying a number or a scale (e.g. "They carved a 100-foot temple out of a single rock, starting from the top.") or a visual-challenge question the opening picture poses (e.g. "How do you carve a temple from the top down?"). Never open on a label that names the subject before the mystery ("This massive X was...", "This is the...", "Meet the...", "Here is...", "X is a..."). The hook is a verified fact like every other sentence.
  (2) Forensic Evidence & Underlying Mechanism (Beat 2): Concrete physical evidence, exact metrics, tree rings, seismic data, metallurgy, materials, or gears.
  (3) Epistemic Integrity & Leading Hypotheses (Beat 3): Where figures or causes are debated, present the leading scientific hypotheses with fluent, natural phrasing (e.g. 'physicists calculate that...', 'physical models suggest roughly...', 'ranging between X and Y').
  (4) Civilizational / Modern Relevance & High Stakes (Beat 4): Why this anomaly matters to modern science, engineering, or planetary resilience.
  (5) Standalone Loop Takeaway (Beat 5): An impactful concluding sentence leading back naturally into the opening hook so the video loops seamlessly.
- Hard ban on robotic sentence prefixes: NEVER start sentences with 'Estimated,' or 'Approximately,'. Integrate hedges naturally into the narrative flow.
- Hard ban on academic paper citation chaff: do NOT use phrases like "A study in [year] reported...", "Researchers drilled...", "Geologist [Name] stated...", or "According to historians...". Present verified physical, chemical, and historical facts directly and authoritatively.
- Hard ban on research meta-commentary: NEVER discuss the state of records, studies, or sources in the narration (e.g. do NOT say "popular sources give shallower figures", "there is no official figure for...", "records do not specify", or "sources disagree"). Instead, present the physical and historical realities directly with natural in-situ hedges (e.g. "carved roughly X feet (Y m) down", "an estimated N residents", or "ranging between A and B meters", using the sourced figures).
- Never end on a trailing word such as "when", "until", "because", "and", "that", "the" or "of".
- Use numbers only when sources support them. Where sources disagree, say "about", "up to", "estimated", or name the range. State the hedged physical reality directly.
- No conspiracy or pseudo-archaeology framing (aliens, lost super-civilizations, etc.).
- Topic notes supplied by the user may contain errors or exaggerations. Check them; do not repeat them unverified.
- On-screen copy is short and uppercase-friendly: thumbnail lines at most 22 characters, story and carousel headings at most 26 characters, bodies at most 90 characters.
- Audience: primarily the United States, then the UK, Canada and Australia. Use American English spelling, and give measurements in US units first with metric in parentheses, e.g. "about 280 feet (85 m)", "half a ton (about 500 kg)".
- The narration must add facts, context and interpretation a viewer could not get from the pictures alone (Meta treats voiceover that merely describes what is on screen as unoriginal). Never narrate the visuals ("here we see", "as you can see", "this photo shows").
- Write like a sharp human documentary narrator: concrete nouns, active verbs, specific numbers and names. Avoid generic AI phrasing such as "delve", "testament to", "tapestry", "unveil", "unravel", "shrouded in mystery", "realm", "embark", "intricate", "a glimpse into", "stands as", "it's important to note".
- Hard ban on dictionary etymologies (e.g. "originates from the Greek word...", "meaning single and stone"). Focus 100% on physical, engineering, and historical realities.
- Hard ban on academic historian name-dropping (e.g. "Art historian X theorized...", "Scholars debate whether..."). Narrate the factual and physical reality directly.
- Return the sources you relied on."""
# The prompt holds literal braces elsewhere, so the numbers are substituted rather than f-formatted.
SYSTEM_PROMPT = (SYSTEM_PROMPT.replace("{WORDS_MIN}", str(TARGET_WORD_COUNT_MIN))
                 .replace("{WORDS_MAX}", str(TARGET_WORD_COUNT_MAX))
                 .replace("{SECONDS_MIN}", f"{TARGET_DURATION_MIN:g}")
                 .replace("{SECONDS_MAX}", f"{TARGET_DURATION_MAX:g}")
                 .replace("{HOOK_WORDS}", str(HOOK_MAX_WORDS)))

RESEARCH_SYSTEM = """You are a research assistant for a history documentary team. Use web search to establish what reliable sources (scholarship, museums, heritage bodies, reputable encyclopedias and press) say about the topic. Report concrete, citable facts with numbers and dates, note where sources disagree and give the range, flag popular claims that are myths or exaggerations, and identify the genuine open questions. Be concise and factual."""

RESEARCH_PROMPT = """Research this topic for a 45-second documentary Reel.

Topic: {title}
Location: {location}
Claims to check (unverified, may be wrong): {key_facts}
Suggested hook (verify): {hook}"""

USER_PROMPT_TEMPLATE = """Write the complete script package for this topic.

Topic title: {title}
Sub-theme: {sub_theme}
Suggested hook (verify before using): {hook}
Location: {location}
Topic notes (unverified): {key_facts}

Requirements:
- narration: open on the verified facts' most surprising discovery or human-stakes fact (the mystery first: viewers decide in under two seconds), never on a list of measurements; the first sentence at most 14 words. Place the site for the viewer ({location}: its region or country, not only its own name) within the first 3 sentences. Never put a unit conversion or an aside in brackets: the voice does not say it. The title, the caption and the narration tell the same story: a figure, year or discovery the title or caption leads with is told in the narration too. Word budget by role: the hook (at most 14 words) carries the discovery or mystery and, ideally, the place; the place is named inside an existing sentence ("Beneath a house in <place>, <country>, ..."), never as a sentence added for it; then 3-4 fact sentences; then the closing sentence; the total inside the Length range below. The discovery-first opening replaces measurement sentences, it is not added to them; one hedge may cover two related figures.
- title: the Facebook Reel title shown under the video: at most 60 characters, specific and intriguing (a name, a number or a tension, not a label like "The History of X"); at most one relevant emoji.
- caption: written like a magazine editor, not a template. First line: a curiosity-driven line of at most 110 characters (a hard limit of 125 is shown before "more"; stay well under it) that is NOT the narration's hook sentence. Then 2-3 short sentences that add context the video doesn't say (a detail, a consequence, a name), with the place worked naturally into the prose. Then one genuine question to the audience. Then the hashtags on the last line. Use 1-3 relevant emoji where they add warmth or meaning (for example one at the end of the first line and one on the question), never as labels or bullet markers, and no "📍" lines. Under 1,600 characters.
- carousel_caption: a separate caption for the 7-slide carousel of the same topic, different wording from the Reel caption (not a rephrase of its first line): a short invitation to look through the slides ("Swipe through ..." is fine; never say which way), one fresh detail, one question, then the same hashtags. Use 1-3 relevant emoji where they add warmth or meaning, as in the caption (never a pointing hand or an arrow). Under 800 characters. It is posted as Instagram's carousel and as Facebook's photo grid, which place the caption and the photos differently.
- No direction cues in any text (captions, title, first comment, share line, slides, stories): no pointing-hand or arrow emoji or symbols (such as 👉 👇 👆 ☝️ ➡️ ⬇️ → ↓), no "swipe right/left/up/down", and no "below" or "above" for the comments, caption or photos (write "in the comments", not "in the comments below"). Each platform places these differently and may change it.
- No engagement bait in any text (Meta demotes it): never ask for likes or reactions ("like this if", "react with", "double tap if"), votes by reaction, shares to a number of people ("share with 5 friends"), tags ("tag someone who") or set replies ("comment YES", "type 1 if"). Ask a genuine question instead.
- hashtags: 3-5 (Instagram allows at most 5), each starting with "#": specific to the place, era or subject (e.g. "#<Place>", "#<Era>History"); no generic engagement tags like "#viral", "#fyp", "#ReelsViral", "#explore".
- story_slides: exactly 3 (hook, revelation, question). Slide 3 asks the poll's question; its body gives context and asks nothing else. Each body is one complete sentence of at most {story_words} words ({poll_words} on slide 3): it must fit three lines at the Story's text size, and long words and names take more room.
- poll: an either/or question (at most 60 characters) and exactly 2 options (each at most 24 characters) that are its two answers, e.g. "Planned or lucky?" with "Careful planning" / "Lucky accident". Never a yes/no question with options that are not yes/no.
- carousel_slides: exactly 7 (title slide, five fact slides, closing call-to-comment slide). Slide 7 MUST ask a humane, universally resonant, thought-provoking question that appeals to all demographics (e.g. human ingenuity, civilizational survival, what modern humans have forgotten, or enduring mysteries) rather than narrow academic trivia, compelling viewers to debate in the comments.
- first_comment: a debate-starting question, with no hashtags (Instagram's 5-hashtag cap covers the whole post).
- share_line: one warm sentence of at most 90 characters, no hashtags, at most one emoji, that asks the viewer to share this with someone (say who, for this topic: "a friend who loves ancient engineering") and to follow for the next story. Worded for this topic, never a stock slogan ("share this with a friend", "someone who loves history" are rejected). Put the share ask first and the follow invitation after it ("Send this to the codebreaker you know, and follow for the next relic."): the video's end card shows the share ask on its own, under the channel name, with its own FOLLOW line. It closes both captions and the carousel too.
- sources: the pages you used, with real URLs."""

VERIFIED_USER_PROMPT_TEMPLATE = """Write the complete script package for this topic from the verified fact sheet.

Topic title: {title}
Sub-theme: {sub_theme}
Location: {location}

Requirements:
- narration: open on the verified facts' most surprising discovery, central anomaly, or human-stakes tension (the mystery first: viewers decide in under two seconds), never on a list of measurements; the first sentence at most 14 words. Build the opening hook strictly from verified facts or genuine open debates; never invent unverified framing. Place the site for the viewer ({location}: its region or country, not only its own name) within the first 3 sentences. Never put a unit conversion or an aside in brackets: the voice does not say it. The title, the caption and the narration tell the same story: a figure, year or discovery the title or caption leads with is told in the narration too. Word budget by role: the hook (at most 14 words) carries the discovery or mystery and, ideally, the place; the place is named inside an existing sentence ("Beneath a house in <place>, <country>, ..."), never as a sentence added for it; then 3-4 fact sentences; then the closing sentence; the total inside the Length range below. The discovery-first opening replaces measurement sentences, it is not added to them; one hedge may cover two related figures.
- sentence_trace: For every single sentence in the narration_script, provide an entry in sentence_trace: the exact sentence text, the supporting fact_ids from <verified_facts> (e.g. ["F1"], ["F3"]), and trace_type ("domain_fact", "open_historical_debate", "epistemic_negation", or "rhetorical_framing"). If a sentence expresses an open debate or rhetorical question without a specific fact, set fact_ids=[] and set trace_type accordingly.
- title: the Facebook Reel title shown under the video: at most 60 characters, specific and intriguing (a name, a number or a tension, not a label like "The History of X"); at most one relevant emoji.
- caption: written like a magazine editor, not a template. First line: a curiosity-driven line of at most 110 characters (a hard limit of 125 is shown before "more"; stay well under it) that is NOT the narration's hook sentence. Then 2-3 short sentences that add context the video doesn't say (a detail, a consequence, a name), with the place worked naturally into the prose. Then one genuine question to the audience. Then the hashtags on the last line. Use 1-3 relevant emoji where they add warmth or meaning (for example one at the end of the first line and one on the question), never as labels or bullet markers, and no "📍" lines. Under 1,600 characters.
- carousel_caption: a separate caption for the 7-slide carousel of the same topic, different wording from the Reel caption (not a rephrase of its first line): a short invitation to look through the slides ("Swipe through ..." is fine; never say which way), one fresh detail, one question, then the same hashtags. Use 1-3 relevant emoji where they add warmth or meaning, as in the caption (never a pointing hand or an arrow). Under 800 characters. It is posted as Instagram's carousel and as Facebook's photo grid, which place the caption and the photos differently.
- No direction cues in any text (captions, title, first comment, share line, slides, stories): no pointing-hand or arrow emoji or symbols (such as 👉 👇 👆 ☝️ ➡️ ⬇️ → ↓), no "swipe right/left/up/down", and no "below" or "above" for the comments, caption or photos (write "in the comments", not "in the comments below"). Each platform places these differently and may change it.
- No engagement bait in any text (Meta demotes it): never ask for likes or reactions ("like this if", "react with", "double tap if"), votes by reaction, shares to a number of people ("share with 5 friends"), tags ("tag someone who") or set replies ("comment YES", "type 1 if"). Ask a genuine question instead.
- hashtags: 3-5 (Instagram allows at most 5), each starting with "#": specific to the place, era or subject (e.g. "#<Place>", "#<Era>History"); no generic engagement tags like "#viral", "#fyp", "#ReelsViral", "#explore".
- story_slides: exactly 3 (hook, revelation, question). Slide 3 asks the poll's question; its body gives context and asks nothing else. Each body is one complete sentence of at most {story_words} words ({poll_words} on slide 3): it must fit three lines at the Story's text size, and long words and names take more room.
- poll: an either/or question (at most 60 characters) and exactly 2 options (each at most 24 characters) that are its two answers, e.g. "Planned or lucky?" with "Careful planning" / "Lucky accident". Never a yes/no question with options that are not yes/no.
- carousel_slides: exactly 7 (title slide, five fact slides, closing call-to-comment slide). Slide 7 MUST ask a humane, universally resonant, thought-provoking question that appeals to all demographics (e.g. human ingenuity, civilizational survival, what modern humans have forgotten, or enduring mysteries) rather than narrow academic trivia, compelling viewers to debate in the comments.
- first_comment: a debate-starting question, with no hashtags (Instagram's 5-hashtag cap covers the whole post).
- share_line: one warm sentence of at most 90 characters, no hashtags, at most one emoji, that asks the viewer to share this with someone (say who, for this topic: "a friend who loves ancient engineering") and to follow for the next story. Worded for this topic, never a stock slogan ("share this with a friend", "someone who loves history" are rejected). Put the share ask first and the follow invitation after it ("Send this to the codebreaker you know, and follow for the next relic."): the video's end card shows the share ask on its own, under the channel name, with its own FOLLOW line. It closes both captions and the carousel too.
- sources: the pages you used, with real URLs."""

AI_ORIGINS = ("claude", "gemini-free")  # scripts written by a model (vs curated by hand)
SECOND_CHECK_ATTEMPTS = 3        # the free second check, when unavailable, is tried this many times in all
SECOND_CHECK_RETRY_SECONDS = 60  # a per-minute quota is back by then
_sleep = time.sleep


def _structured(system, prompt, schema, **claude_kwargs):
    """The free writer model when SCRIPT_ENGINE=free, otherwise Claude."""
    if free_llm.enabled():
        return free_llm.call(system, prompt, schema, role="writer", task_class="heavy", **claude_kwargs)
    return structured_call(system, prompt, schema, **claude_kwargs)


def _ready() -> bool:
    """Can scripts be written on the configured engine (free Gemini key, or Claude)?"""
    return free_llm.available() if free_llm.enabled() else claude_available()


def writer_origin() -> str:
    return "gemini-free" if free_llm.enabled() else "claude"


FACTS_ONLY_RULES = """Write from the verified facts ONLY. Every factual statement in the narration, caption, title,
on-screen text, slides, stories and first comment must come from a listed fact; for a DISPUTED fact use natural, fluent
hedging (e.g. 'physicists calculate that...', 'models suggest roughly...', 'ranging between X and Y').
CRITICAL BAN ON ROBOTIC PREFIXES: NEVER start sentences with 'Estimated,' or 'Approximately,'. Integrate hedges seamlessly into verbs or clauses.
Do not add details, names, causes, numbers or dates that are not in a fact, and do not derive new figures (no subtracting dates, no unit-free rounding).
State verified physical, chemical, and historical facts directly and authoritatively. Do not use dictionary etymologies (no "monos meaning single and lithos meaning stone", no Latin/Greek roots). Do not quote individual researcher names or study publication dates (e.g. do not say 'Geologist X has said' or 'A July 2017 study reported') unless hedging a scientific hypothesis (e.g. 'Researchers propose that...').
NARRATIVE TENSION & HOOK GROUNDING:
All dramatic curiosity, narrative hooks, and opening tension must arise strictly from the contrast between two verified facts (e.g. 'structures standing 2,000 years vs modern concrete crumbling') or from an explicit open debate/question in <verified_facts>.
HARD BAN ON SENSATIONAL DRAMATIZATION: NEVER use emotional embellishment words like 'baffled', 'mystified', 'astonished', 'stunned', or 'miraculous' unless an evidence quote explicitly uses that exact word.
CRITICAL THEMATIC UNITY & ZERO-DRIFT RULE:
Every single sentence in the narration must directly advance the central subject, engineering marvel, physical anomaly, chemical mechanism, or civilizational contrast of the topic.
HARD BAN ON TOPIC DRIFT: NEVER pivot in later beats to regional municipal history, local colonial founding dates, territorial border conquests, or unrelated town elevations (e.g. never talk about 280 BC Etruscan land treaties when discussing concrete).
GEOGRAPHIC CONCORDANCE: The narration's primary site and landmarks must align 100% with the topic's primary location. If scientific testing occurred on samples excavated from a secondary archaeological site (e.g. Pompeii or Privernum), that site must be explicitly subordinated as a material testing sample of the primary technique, NEVER treated as an unlinked narrative topic.
Do not invent unprovable negative claims unless an evidence quote explicitly states that exact negative fact. Stick strictly to what IS documented.
Do not assert factual claims about modern builders, modern technology, or modern construction practices unless explicitly listed in a verified fact; stick strictly to the ancient subject or frame contrasts as open questions.
LITERAL SCIENTIFIC FIDELITY: When citing or referencing studies, analyses, or archaeological discoveries, state ONLY what the evidence quote literally states. NEVER extrapolate, hypothesize, or invent mechanisms beyond the text (e.g. do NOT turn 'provides lessons for sustainable construction' into 'demonstrates how historical mortars resist degradation').
Use at most 8 distinct facts and reuse the same wording for a fact wherever it appears. Questions and framing are fine if they assert nothing new.
List sources only from retrieved_sources."""

REVISE_SYSTEM = """You fix a short history video package so it passes fact-checking. For each finding, replace ONLY
the rejected sentence or phrase: hedge it naturally using fluent English (e.g. 'models suggest', 'scientists calculate', 'roughly'), or replace it with a verified
fact, or remove it. CRITICAL: NEVER prepend robotic prefixes like 'Estimated,' or 'Approximately,' to fix a sentence.
CRITICAL GROUNDING IN REVISIONS:
When replacing or fixing an untraced claim, you must ONLY use vocabulary, dates, measurements, and statements that exist in <verified_facts>.
DO NOT introduce outside parametric memory or unlisted adjectives (e.g. do not introduce terms like 'alkaline', 'volcanic', or unlisted builder names unless they appear in <verified_facts>).
If a claim cannot be substantiated by an existing fact ID, delete it cleanly or replace it with a verified fact from <verified_facts>.
Titles and on-screen covers are intrigue headlines and must NEVER be hedged with 'Estimated'.
Return exact find/replace edits: "find" must be copied character-for-character from the draft
(a whole sentence or phrase), "replace" is its new text ("" to delete). Fix every place the claim appears
(narration, caption, title, on-screen text, slides, stories, graphics). Never add facts that are not verified.
If you change the narration's last sentence, apply the same edit to closing_sentence.
A LENGTH finding names a word window for the narration. Too short: add one or two sentences made only of
verified facts, placed before the closing sentence ("find" = the sentence before it, "replace" = that sentence
followed by the new ones). Too long: remove or condense sentences until the narration is inside the window.
Keep the closing sentence and every other field as they are.
A REPEAT finding names a phrase the narration says twice: reword the later sentence ("find" = that whole sentence) so
the phrase is said once, keeping its facts and its hedge (one hedge can cover two related figures). Never let an edit
say again a phrase the narration already says (the REPEATS list), and keep every disputed figure's hedge wherever the
figure is stated (MANDATORY HEDGES).
An OPENING finding is about how the narration starts: place the site (its region or country) in one of the first
sentences, open on the story the title and caption tell, or cut the first sentence to the words the finding gives,
each with verified facts only and every hedge kept. Never add a sentence for it: name the place inside an existing
sentence ("Beneath a house in <place>, <country>, ..."), and let the discovery replace a measurement sentence. If the
first sentence changes, apply the same edit to hook_sentence.
WORD BUDGET (every edit keeps it): the hook at most 14 words, 3-4 fact sentences, the closing sentence, the total
inside the word window a LENGTH finding names.
GRAPHICS (stat cards, timeline, scale, cross-section, route, photo labels, emphasis words) are listed under <graphics>,
one field per line by its path. To change one, set "target" to that path ("stat_cards[2].value") with "find" = the
text in that field and "replace" = its new text; to remove a whole entry, set "target" to the entry ("stat_cards[2]")
with "find" "" and "replace" "". Never copy JSON syntax (braces, quotes, commas) into "find". A disputed figure on a
graphic keeps its hedge in its text ("about 280 ft", "up to <N> levels"), or its entry is removed. Emphasis words
must be words the narration says: if an edit takes one out of the narration, change or remove that emphasis word too."""


class Edit(BaseModel):
    find: str
    replace: str
    target: Optional[str] = None   # a graphic field or entry by its <graphics> path ("stat_cards[2].value")


class Edits(BaseModel):
    edits: List[Edit]


GRAPHICS_SYSTEM = """You plan the on-screen graphics for a finished, fact-checked history Reel script. Use only facts stated in the narration or the research notes; never add new claims. Keep every hedge the narration uses: if it says "about", "up to", "estimated" or gives a range, the graphic must too (put the hedge in the value text, e.g. "about 280 ft", or set approximate=true). Graphics are fact-checked like the narration, and a flat figure where sources disagree is rejected: a disputed figure or range (the MANDATORY HEDGES list) on a stat card, timeline, scale or cross-section carries its hedge in its own text, or is left out of the graphics. Emphasis words are only words the narration says. Leave a field null when the facts don't support it."""

GRAPHICS_PROMPT = """Plan the graphics for this script.

- emphasis_words: 1-3 words or short names copied exactly from the narration that carry the story (a striking number, a name, a place). Each must be words the narration above says, character for character (a number as the narration writes it); never a word only the research notes or an earlier draft use. They get a gold highlight on screen, so choose sparingly.
- timeline: 2-4 dated events from the story, oldest first, only dates the research supports (approximate=true for estimates; negative years for BC; labels at most 40 characters). Omit (null) if the story has fewer than two solid dates.
- scale_fact: one striking, well-sourced size (depth, height or length) of the subject, for an on-screen comparison with a famous landmark (subject at most 28 characters); null if no reliable figure.
- stat_cards: 1-2 headline figures from the research (value as it should appear, US units first, at most 22 characters, plus a label of at most 40 characters); null if none is striking and solid.
- cross_section: only for underground or vertical structures where the research gives the total depth and the depth of 2-5 levels or features; otherwise null.
- route: only when the story follows a journey or movement between real places (2-5 stops, in order, each with a short label); otherwise null.
- photo_labels: 2-4 physical features named in the narration that a photo of the site could show (e.g. "ventilation shaft", "rolling stone door"); null if none."""

BANNED_ENDINGS = {"when", "until", "because", "and", "or", "so", "that", "while", "as", "but", "if", "the", "a", "of"}
BANNED_TROPES = [
    "alien", "aliens", "extraterrestrial", "illuminati", "reptilian", "magic portal",
    "time travel portal", "atlantis technology", "levitation ray", "levitation rays",
]
# Phrasing that reads as machine-written; a draft containing it is sent back for a rewrite.
AI_PHRASES = [
    "estimated,", "approximately,",
    "here we see", "as you can see", "this photo shows", "in this image", "in this video",
    "delve", "delves", "delving", "testament to", "tapestry", "unveil", "unveils", "unravel", "unravels",
    "shrouded in mystery", "realm of", "embark", "a glimpse into", "stands as a", "it's important to note",
    "in the annals of", "intricate dance", "beacon of",
    "a study reported", "a study published", "a study found", "researchers drilled", "researchers found that",
    "geologist marie jackson has said", "geologist marie jackson said", "according to researchers", "according to studies",
]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")  # pictographs, symbols, dingbats
GENERIC_HASHTAGS = {"#viral", "#fyp", "#foryou", "#foryoupage", "#reelsviral", "#explore", "#explorepage", "#trending",
                    "#reels", "#reelsinstagram", "#instagood"}


def _similar(a: str, b: str) -> float:
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() if a.strip() and b.strip() else 0.0


# "Extraterrestrial" is also impact science's word for something from space ("once considered an
# extraterrestrial impact crater": a Richat draft was rejected for it). Followed by one of these, within a
# word, it is the science, not the trope; anywhere else it is still rejected.
_SCIENTIFIC_EXTRATERRESTRIAL = re.compile(
    r"\bextraterrestrial\s+(?:\w+\s+)?(?:impacts?|impactors?|meteorites?|asteroids?|comets?|bolides?|bod(?:y|ies)|"
    r"objects?|material|dust|craters?)\b", re.IGNORECASE)


def find_banned_tropes(text: str) -> List[str]:
    checked = _SCIENTIFIC_EXTRATERRESTRIAL.sub("", text)
    return [t for t in BANNED_TROPES if re.search(r"\b" + re.escape(t) + r"\b", checked, re.IGNORECASE)]


# The licensed voice (Azure) may differ slightly from the edge-tts measurement of the same neural
# voice, so a script must measure a little inside the ceiling.
_MEASURE_MARGIN_SEC = 0.5


def _short_reason(problems: List[str]) -> str:
    """A few words for the progress card on why a draft was rejected."""
    text = " ".join(problems)
    if "when spoken (limit" in text:
        return "too long when spoken"
    if "when spoken (minimum" in text or re.search(r"Narration is \d+ words; must be", text):
        return "wrong length"
    return problems[0][:60] if problems else "checks"


WORD_PACE_FILE = config.BASE_DIR / "data" / "word_pace.json"


def _word_pace_entries() -> List[list]:
    """[[draft key, seconds per word], ...], oldest first; an older file of bare values reads as keyless."""
    try:
        raw = json.loads(WORD_PACE_FILE.read_text(encoding="utf-8")) if WORD_PACE_FILE.exists() else []
    except (OSError, ValueError):
        return []
    return [[str(e[0]), float(e[1])] if isinstance(e, list) and len(e) == 2 else ["", float(e)] for e in raw]


def record_word_pace(seconds_per_word: float, text: str = "") -> None:
    """Keeps the last 20 measured seconds-per-word of distinct drafts (the narrator voice, this topic's words).
    A draft measured again (a kept script, a resume, a second structural pass) is not counted again: five
    copies of one topic's pace moved the median and the writer's word window with it, and the next drafts
    came out under the minimum (2026-10-25)."""
    import hashlib
    try:
        key = hashlib.sha256(" ".join((text or "").lower().split()).encode("utf-8")).hexdigest()[:12] if text else ""
        entries = _word_pace_entries()
        if key and any(e[0] == key for e in entries):
            return
        entries = (entries + [[key, round(seconds_per_word, 4)]])[-20:]
        from .atomic_files import write_json
        write_json(WORD_PACE_FILE, entries)
    except (OSError, ValueError) as e:
        log.debug("Could not record the word pace: %s", e)


def _pace_window() -> Tuple[float, float]:
    """(fastest, slowest) pace of the production narrator against the measurement voice."""
    return audio_engine.pace_range(audio_engine.production_voice_key())


class LengthWindow(NamedTuple):
    low: int          # fewest words that reach TARGET_DURATION_MIN at the fastest take judged
    high: int         # most words that stay within the ceiling at the slowest take judged
    comfortable: int  # most words within TARGET_DURATION_MAX at the slowest take, held inside low-high
    fast: float       # the pace ratios the window was judged at (both the middle one when takes vary too much)
    slow: float


def length_window(measured_per_word: float, fast: float, slow: float) -> LengthWindow:
    """The one word window every length instruction and the prompt's note are held to: enough words for the
    minimum at the narrator's fastest take, few enough for the ceiling at its slowest. Each instruction was
    worked out on its own before, and they contradicted each other (production, Hal Saflieni 2026-10-27: 103
    words read 54.3 s and were told "85-90 words"; 86 then read 39.3 s, too short; 91 read 40.3 s and 100
    read 51.8 s, too long again; all three attempts spent)."""
    longest = HARD_DURATION_CEILING - _MEASURE_MARGIN_SEC

    def bounds(f: float, s: float) -> Tuple[int, int]:  # a tiny epsilon: 84 words at exactly 0.5 s is 42.0 s
        return (math.ceil(TARGET_DURATION_MIN / (measured_per_word * f) - 1e-9),
                math.floor(longest / (measured_per_word * s) + 1e-9))
    low, high = bounds(fast, slow)
    if low > high:  # takes vary too much for one window: judged at the middle pace, as the checks always were
        fast = slow = (fast + slow) / 2
        low, high = bounds(fast, slow)
    # WORD_COUNT_FLOOR (validate_structure's sanity bound) lifts the floor only while that still fits the
    # ceiling: a voice so slow that the floor overruns the ceiling is held to the ceiling, since a voiceover
    # past it cannot make a Reel at all, while a short one is only short.
    if WORD_COUNT_FLOOR <= high:
        low = max(low, WORD_COUNT_FLOOR)
    comfortable = max(low, min(high, math.floor(TARGET_DURATION_MAX / (measured_per_word * slow) + 1e-9)))
    return LengthWindow(low, high, comfortable, fast, slow)


def _inside(a: int, b: int, window: LengthWindow, span: int = 4) -> Tuple[int, int]:
    """An a-b word range held inside the window, at least `span` words wide where the window allows
    (told one exact count, or "at most 95", a writer overshoots: production, 2026-10-23)."""
    a, b = (max(window.low, min(x, window.high)) for x in sorted((a, b)))
    while b - a < span and (a > window.low or b < window.high):
        if b < window.high:
            b += 1
        if b - a < span and a > window.low:
            a -= 1
    return a, b


def length_target_note() -> str:
    """The word range that fits, from measured pace (the prompt's fixed 105-115 words ran 53-58 s at the
    narrator's pace for name-heavy topics). Empty until three drafts have been measured."""
    import statistics
    fast, slow = _pace_window()
    paces = _run_paces() if _history_on() else []
    if len(paces) >= 2:  # this run's drafts set the range: the same one their length instructions name
        judged = length_window(statistics.median(paces), fast, slow)
        seen = _seen_window(judged.fast, judged.slow)
        if seen is not None:
            (low, high), note = _band(seen)
            return (f"\n\nLength: write the narration in {low}-{high} words{note}, the range this run's drafts read "
                    f"at the narrator's pace allow ({TARGET_DURATION_MIN:g}-{HARD_DURATION_CEILING - _MEASURE_MARGIN_SEC:g}"
                    " seconds); this overrides any other word count.")
    values = [e[1] for e in _word_pace_entries()]
    if len(values) < 3:
        return ""
    measured = statistics.median(values)
    # The minimum for the narrator's fastest take, the comfortable maximum for its slowest (they vary for
    # ElevenLabs): the same window the length checks hold a draft to.
    window = length_window(measured, fast, slow)
    low, high = window.low, window.comfortable
    per_word = measured * window.slow
    return (f"\n\nLength: write the narration in {low}-{high} words. This narrator speaks about {per_word:.2f} s "
            f"a word, so that is {TARGET_DURATION_MIN:g}-{TARGET_DURATION_MAX:g} seconds; this overrides any other "
            "word count.")


def fit_speed(text: str, seconds: Optional[float] = None) -> Optional[float]:
    """How much faster the narration must be spoken to fit the time limit: 1.0 when it fits as written, up
    to SPEECH_SPEEDUP_MAX when a faster read is measured to fit, None when even that runs long or the voice
    can't be read faster (ElevenLabs: the narration is rewritten shorter). Each rewrite
    was a full model call that overshot both ways (production 2026-10-23: 109, 73, 108 words); a few
    percent faster is not noticed. 1.0 when it can't be measured (the post-voiceover check still guards)."""
    pace = audio_engine.pace_ratio(audio_engine.production_voice_key())  # the real voice's pace
    if seconds is None:
        measured = audio_engine.measure_spoken_seconds(text)
        if measured is None:
            return 1.0
        seconds = measured * pace
    longest = HARD_DURATION_CEILING - _MEASURE_MARGIN_SEC
    if seconds <= longest:
        return 1.0
    if not audio_engine.honours_speed(audio_engine.production_voice_key()):
        return None  # ElevenLabs can't be read faster and is never stretched: the script is shortened instead
    speed = math.ceil(seconds / longest * 100 + 1) / 100  # one point of margin
    if speed > config.SPEECH_SPEEDUP_MAX:
        return None
    faster = audio_engine.measure_spoken_seconds(text, speed=speed)
    return speed if faster is not None and faster * pace <= longest else None


# This run's drafts, as seconds a word at the measurement voice: a rewrite changes the words (names, numbers) and so
# the pace, and a window worked out from one draft's pace alone sent the next one back the other way (production,
# Derinkuyu 2026-10-29: 100 words read 41.5 s, "extend to 104-108"; 106 words read 51.5 s, "cut to 99-103"; three
# times over, the band both paces allowed being 102-105). Cleared when a run starts writing (begin_length_history).
_draft_paces: Dict[str, float] = {}  # each draft (its normalised words) -> seconds a word, in the order measured
# "on" only while a run is writing (generate_script): a lone check is judged on its own. "key": whose drafts these are
# (topic, run): another topic or run starts afresh, whatever its first call carries (a re-render's saved script, a
# regeneration, the next topic of a cycle).
_length_history: Dict[str, Any] = {"on": False, "key": None}
_PACES_KEPT = 4          # the latest drafts' paces only
_PACE_OUTLIER = 0.15     # a pace this far from the run's median (one name-heavy draft) does not set the window


def begin_length_history(key: Any = None) -> None:
    _draft_paces.clear()
    _length_history["key"] = key


def _history_on() -> bool:
    """Whether this thread is the run's writing one: a check made elsewhere (a review sweep, a review wait) is
    judged on its own and never enters the run's history."""
    return bool(_length_history["on"]) and _length_history.get("thread") == threading.get_ident()


def _run_paces(current: Optional[float] = None) -> List[float]:
    """This run's latest distinct drafts' paces, sorted; from three on, without one far from their median. The
    draft being judged (`current`) always counts: its own pace is the one thing known for certain about it."""
    import statistics
    recent = list(_draft_paces.values())[-_PACES_KEPT:]
    if len(recent) >= 3:
        middle = statistics.median(recent)
        kept = [x for x in recent if abs(x - middle) <= _PACE_OUTLIER * middle]
        if not [x for x in kept if current is None or round(x, 5) != round(current, 5)]:
            log.info("This run's draft paces are too far apart to set a window (%s); judging the draft on its own.",
                     ", ".join(f"{x:.3f}" for x in recent))
        recent = kept
    if current is not None:
        recent = recent + [current]
    return sorted(set(round(x, 5) for x in recent))


def _seen_window(fast: float, slow: float, current: Optional[float] = None) -> Optional[Tuple[LengthWindow, bool]]:
    """The word window every pace this run's drafts were read at allows: enough words for the minimum at the
    fastest per-word pace (and fastest take), few enough for the ceiling at the slowest; (window, False). When no
    count satisfies both, the window at the middle pace, as length_window does for takes, and True. None until two
    drafts read at different paces."""
    paces = _run_paces(current)
    if not _history_on() or len(paces) < 2:
        return None
    longest = HARD_DURATION_CEILING - _MEASURE_MARGIN_SEC
    low = math.ceil(TARGET_DURATION_MIN / (paces[0] * fast) - 1e-9)
    high = math.floor(longest / (paces[-1] * slow) + 1e-9)
    if WORD_COUNT_FLOOR <= high:
        low = max(low, WORD_COUNT_FLOOR)
    if low > high:
        import statistics
        return length_window(statistics.median(paces), fast, slow), True
    comfortable = max(low, min(high, math.floor(TARGET_DURATION_MAX / (paces[-1] * slow) + 1e-9)))
    return LengthWindow(low, high, comfortable, fast, slow), False


def _band(seen: Tuple[LengthWindow, bool]) -> Tuple[Tuple[int, int], str]:
    """The run's word range: the middle of the window its paces allow, and a note when that is the middle pace's."""
    w, middle_pace = seen
    mid = (w.low + w.high) // 2
    note = (" (this run's drafts read at paces too different for one window: aimed at the middle one)"
            if middle_pace else "")
    return _inside(mid - 2, mid + 2, w), note


def _secs(value: float, limit: float) -> str:
    """Seconds as said to the writer: to the hundredth near the limit, so "51.5s (limit 51.5s)" never reads as a
    pass that failed."""
    return f"{value:.2f}" if abs(value - limit) < 0.1 else f"{value:.1f}"


def length_miss_words(problems: List[str]) -> Optional[int]:
    """How many words a length problem's draft is outside the window it names ("This draft is 106 words; rewrite
    the narration in 102-105 words" -> 1), or None when a problem is not a length one."""
    misses = []
    for problem in problems:
        m = re.search(r"This draft is (\d+) words; .*?(\d+)-(\d+) words", problem)
        if not m or "when spoken" not in problem:
            return None
        words, low, high = (int(x) for x in m.groups())
        misses.append(max(low - words, words - high, 0))
    return max(misses) if misses else None


# A draft just past the plan's ceiling (51.6-53 s at the narrator's slowest take) gets one trim attempt and then
# passes, since the voice keeps a take up to VOICE_DURATION_CEILING (54.5 s): such drafts ended runs at the last
# attempt (run audit 2026-10-29, item 9). Held 1.5 s under the voice's ceiling, so a kept draft's take still fits.
NEAR_MISS_CEILING = min(HARD_DURATION_CEILING + 1.0, config.VOICE_DURATION_CEILING - 1.5)
_near_misses_kept: Dict[str, float] = {}   # narration (its words) -> seconds, for drafts kept after their trim attempt
_NEAR_MISSES_KEPT_MAX = 32


def _narration_key(text: str) -> str:
    """The narration's words, as the near-miss record knows them (punctuation and spacing tidied by _finalize)."""
    return " ".join(re.findall(r"\w+", (text or "").lower()))


def near_miss_seconds(problems: List[str]) -> Optional[float]:
    """The spoken seconds of a draft whose every problem is its length just past the plan's ceiling (at most
    NEAR_MISS_CEILING), or None (no problems, another fault, too short, or further past it)."""
    seconds = []
    for problem in problems:
        m = re.match(r"Narration runs (\d+(?:\.\d+)?)s when spoken \(limit ", problem)
        if not m:
            return None
        seconds.append(float(m.group(1)))
    return max(seconds) if seconds and max(seconds) <= NEAR_MISS_CEILING + 1e-9 else None


def keep_near_miss(pkg: ScriptPackage, seconds: float) -> None:
    """Records a draft kept after its trim attempt: spoken_length_problems passes it from then on (the run's final
    checks, a fact revision's), as long as it still reads within NEAR_MISS_CEILING."""
    for text in (pkg.narration_script, spoken_narration(pkg.narration_script)):
        _near_misses_kept.pop(_narration_key(text), None)
        _near_misses_kept[_narration_key(text)] = seconds
    while len(_near_misses_kept) > _NEAR_MISSES_KEPT_MAX:
        _near_misses_kept.pop(next(iter(_near_misses_kept)))


def spoken_length_problems(pkg: ScriptPackage) -> List[str]:
    """Measures how long the narration will run when spoken (the narrator voice is deterministic) and
    says how to fix it if the voiceover would be too long or short. Empty when fine, when a slightly faster
    read fits (fit_speed), or when unmeasurable (offline); the check after the real voiceover still guards."""
    seconds = audio_engine.measure_spoken_seconds(pkg.narration_script)
    if seconds is None:
        return []
    words = len(pkg.narration_script.split()) or 1
    record_word_pace(seconds / words, pkg.narration_script)  # at the measurement voice; the pace ratio is applied on use
    if _history_on():  # one entry a draft: measured again (a second check of it), it is not counted twice
        key = " ".join(pkg.narration_script.lower().split())
        _draft_paces.pop(key, None)
        _draft_paces[key] = seconds / words
    measured = seconds
    longest = HARD_DURATION_CEILING - _MEASURE_MARGIN_SEC
    # Judged at the paces the window was (the middle one when takes vary too much for both ends), so every
    # instruction below lands inside it: a cut that falls under the minimum at the fastest take, or an
    # extension past the ceiling at the slowest, sent the next draft straight back (Hal Saflieni, 2026-10-27).
    window = length_window(measured / words, *_pace_window())
    fast, slow = window.fast, window.slow
    seconds = measured * slow  # the ceiling, for the narrator's slowest take
    per_word = seconds / words
    if seconds > longest + 1e-9 and fit_speed(pkg.narration_script, seconds):
        return []  # spoken slightly faster (the voiceover stage applies the same speed)
    # Once drafts have been read at different paces, every instruction aims at the middle of the window all those
    # paces allow, so a rewrite at another pace lands inside it too.
    seen = _seen_window(fast, slow, current=measured / words)

    def band(default: Tuple[int, int], longer: bool) -> Tuple[Tuple[int, int], str]:
        """The run's range when there is one, else this draft's; always moving the right way: a draft too long is
        never told a count at or above its own, nor one too short at or below it."""
        (low, high), note = _band(seen) if seen is not None else (default, "")
        if (longer and low <= words) or (not longer and high >= words):
            (low, high), note = default, ""
        # Held so even when the window's own bounds disagree (a floor above a long draft): never the wrong way.
        if longer:
            low = max(low, words + 1)
            high = max(high, low)
        else:
            high = min(high, words - 1)
            low = min(low, high)
        return (low, high), note
    if (seconds > longest + 1e-9 and seconds <= NEAR_MISS_CEILING + 1e-9
            and _narration_key(pkg.narration_script) in _near_misses_kept):
        return []   # kept after its trim attempt (keep_near_miss)
    if seconds > longest + 1e-9:
        # A word count is followed far better than seconds: name this draft's count and the one to reach,
        # aiming inside the comfortable length (not just under the limit), never below the window's floor.
        # Never clamped up to the prompt's word range: for a slow voice that range itself is too long.
        target = min(words - math.ceil((seconds - longest) / per_word) - 2, window.comfortable)
        # A window, not a ceiling: told "at most 95", a writer cut 109 words to 73 (production, 2026-10-23).
        (low, target), note = band(_inside(target - 7, target, window), longer=False)
        return [f"Narration runs {_secs(seconds, longest)}s when spoken (limit {longest:g}s). This draft is {words} "
                f"words; rewrite the narration in {low}-{target} words{note} (names and numbers such as dates take "
                f"longer to say), keeping the closing sentence."]
    if measured * fast < TARGET_DURATION_MIN:
        # The minimum, for the narrator's fastest take (production 2026-10-25: 93 words read 41.1 s).
        # A window, as for a long draft: "add about 9 words" to a fresh rewrite meant nothing, and three
        # drafts in a row came out shorter (38.8, 37.3, 35.8 s; production, 2026-10-23).
        seconds = measured * fast
        fast_per_word = seconds / words
        low = words + math.ceil((TARGET_DURATION_MIN - seconds) / fast_per_word) + 2
        (low, high), note = band(_inside(low, max(low + 4, window.comfortable), window), longer=True)
        return [f"Narration runs {_secs(seconds, TARGET_DURATION_MIN)}s when spoken (minimum "
                f"{TARGET_DURATION_MIN:g}s). This draft is {words} words; extend the narration to {low}-{high} "
                f"words{note} with more verified facts from the research notes, keeping the closing sentence."]
    return []


def take_band(seconds: float) -> str:
    """A recorded take against the band its script was planned for: "inside" (TARGET_DURATION_MIN to
    HARD_DURATION_CEILING), "short" (read again), "long" (past the plan, kept up to VOICE_DURATION_CEILING) or
    "over" (past that)."""
    if seconds < TARGET_DURATION_MIN:
        return "short"
    if seconds <= HARD_DURATION_CEILING:
        return "inside"
    return "long" if seconds <= config.VOICE_DURATION_CEILING else "over"


def refit_speed(seconds: float, speed: float = 1.0) -> Optional[float]:
    """The speed for one more take when a real voiceover ran over the ceiling, or None (it fits, or even the
    fastest allowed read would not). Aims just inside the ceiling, never past SPEECH_SPEEDUP_MAX."""
    if seconds <= config.VOICE_DURATION_CEILING:
        return None
    target = config.VOICE_DURATION_CEILING - 0.6
    faster = math.ceil(speed * seconds / target * 100) / 100
    return faster if faster <= config.SPEECH_SPEEDUP_MAX else None


_PHRASE_STOPWORDS = {"the", "a", "an", "of", "to", "in", "and", "was", "were", "is", "it", "its", "that", "this", "on", "at", "for", "by", "with", "as"}


def _phrase_runs(text: str, n: int = 3) -> Tuple[List[str], List[str]]:
    """The narration's sentence openings (first n words) and its runs of n words that the repeat rule counts, in
    order: within a sentence, not only small words, and holding no name or number."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text or "") if x.strip()]
    openings: List[str] = []
    for sent in sentences:
        words = re.findall(r"[A-Za-z']+", sent.lower())
        if len(words) >= n:
            openings.append(" ".join(words[:n]))
    # Within sentences, not across them; a run holding a name or a number is a deliberate repeat ("the
    # Richat Structure", "Catherine Palace"), not wording, and is left alone.
    grams: List[str] = []
    for toks in (re.findall(r"[\w']+", sent) for sent in sentences):
        for i in range(len(toks) - n + 1):
            gram = toks[i:i + n]
            if any(t[:1].isupper() and i + j > 0 for j, t in enumerate(gram)) or any(any(ch.isdigit() for ch in t) for t in gram):
                continue
            gram = [t.lower() for t in gram]
            if sum(w not in _PHRASE_STOPWORDS for w in gram) >= 2:
                grams.append(" ".join(gram))
    return openings, grams


def repeated_phrases(text: str, n: int = 3) -> List[str]:
    """Phrases of n+ words said twice in a narration: any repeated opening of a sentence (the hedge "By one
    estimate" twice in a row), and any repeated run of n words that is not only small words. Names and
    numbers repeated on purpose rarely form a three-word run that is repeated exactly."""
    openings, grams = _phrase_runs(text, n)
    found = [k for k in dict.fromkeys(openings) if openings.count(k) > 1]
    for k in dict.fromkeys(grams):  # one entry per repeated stretch: overlapping runs of it are the same repeat
        if grams.count(k) > 1 and not any(" ".join(k.split()[:n - 1]) in f or " ".join(k.split()[1:]) in f for f in found):
            found.append(k)
    return found


REPEAT_PROBLEM = "The narration repeats the phrase "   # validate_structure's wording; the editors recognise it
REPEAT_RULE = ("No phrase twice in the narration: no two sentences open with the same three words, and no run of three "
               "ordinary words (not a name or a number) is said twice. One hedge can cover two related figures (\"by one "
               "estimate, X and Y\"); otherwise word the second hedge differently (\"roughly\", \"an estimated\", \"up "
               "to\", a range).")
_MAX_LISTED_PHRASES = 80


def repeat_rules(narration: str) -> str:
    """The repeat rule for an editor, with the phrases this narration already says: an edit that adds or changes
    a sentence must not say one of them again (a length edit added "geoglyphs etched into" a second time, twice:
    development, Nazca 2026-10-28). Every sentence opening is listed; past the cap the runs are the first and the
    last ones, since a length edit adds its sentences just before the closing one."""
    openings, grams = _phrase_runs(narration)
    openings = list(dict.fromkeys(openings))
    grams = [g for g in dict.fromkeys(grams) if g not in openings]
    room = max(0, _MAX_LISTED_PHRASES - len(openings))
    if len(grams) > room:
        grams = grams[:room // 2] + grams[len(grams) - (room - room // 2):]
    used = openings + grams
    listed = "; ".join(f'"{x}"' for x in used)
    return f"\n\nREPEATS: {REPEAT_RULE}" + (f" Already said (never again in a new or changed sentence): {listed}." if used else "")


def _hedge_words(fact: Dict[str, Any]) -> str:
    """The words a disputed fact's hedge adds to its statement ("by one estimate"), when the hedge is the statement
    with one stretch of words added; "" when it rephrases the statement (the hedged wording alone is then shown)."""
    statement = re.findall(r"[\w'%.-]+", (fact.get("statement") or "").lower())
    hedge = re.findall(r"[\w'%.-]+", (fact.get("hedge") or "").lower())
    ops = [op for op in difflib.SequenceMatcher(None, statement, hedge, autojunk=False).get_opcodes() if op[0] != "equal"]
    if len(ops) != 1 or ops[0][0] != "insert":
        return ""
    _, _, _, j1, j2 = ops[0]
    return " ".join(hedge[j1:j2])


def _clip(text: str, limit: int) -> str:
    """`text` within `limit` characters, cut after a sentence or else between words (never inside a figure)."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit + 1]
    end = max(head.rfind(". "), head.rfind("; "))
    if end > limit // 2:
        return head[:end + 1]
    return head[:head.rfind(" ")].rstrip(",;:") + " ..." if " " in head else head[:limit]


def _concerns(fact: Dict[str, Any], texts: List[str]) -> bool:
    """Whether one of `texts` states the fact: a figure of its, or three of its longer words."""
    joined = " ".join(texts).lower()
    figures = set(fact_sheet.numbers_in(fact.get("statement") or "") + fact_sheet.numbers_in(fact.get("hedge") or ""))
    if figures & set(fact_sheet.numbers_in(joined)):
        return True
    words = {w for w in re.findall(r"[a-z']{5,}", (fact.get("statement") or "").lower())}
    return len(words & set(re.findall(r"[a-z']{5,}", joined))) >= 3


def mandatory_hedges_note(sheet: Optional[Dict[str, Any]], texts: Optional[List[str]] = None) -> str:
    """The disputed figures and the hedge each keeps, wherever it is stated: the checkers require it in the
    captions, slides, stories, first comment and graphics as in the narration (development, Nazca 2026-10-28: a
    carousel caption and a slide dropped "by one estimate" and the draft was rejected). Titles and covers are
    headlines, which the checkers exempt. `texts`: only the facts those texts state (an edit of a few lines)."""
    facts = [f for f in (sheet or {}).get("facts", []) if f.get("disputed") and f.get("hedge")]
    if texts is not None:
        facts = [f for f in facts if _concerns(f, texts)]
    if not facts:
        return ""
    lines = []
    for f in facts:
        words = _hedge_words(f)
        lines.append(f"- {_clip(f['statement'], 160)} -> hedged: {_clip(f['hedge'], 220)}"
                     + (f' (the hedge: "{words}")' if words else ""))
    return ("\n\nMANDATORY HEDGES: these figures are disputed. Every text that states one keeps its hedge, or an "
            "equivalent (\"about\", \"roughly\", \"an estimated\", \"up to\", a range): the narration, the caption, "
            "the carousel caption, the first comment, every story and carousel slide, and every graphic (titles and "
            "covers are exempt). A flat figure anywhere is rejected. GRAPHICS too: a stat card's value or label, a "
            "timeline entry, the scale figure, a cross-section depth, a route or photo label that states a disputed "
            "figure or range carries the hedge in its own text (\"about 280 ft\", \"up to <N> levels\") or is marked "
            "approximate; one that cannot carry it is left out of the graphics. Never the same hedge twice in one text: two "
            "figures there share one hedge (\"by one estimate, X and Y\"), or each takes a different wording.\n"
            + "\n".join(lines))


MAX_TRIMMED_SENTENCES = 2   # distinct blocked claims a trim may take out; more is a rewrite's
_TRIM_MIN_WORDS = 4         # shorter text is too likely to appear in a checker's note by chance
# Graphics are optional: an entry a checker blocked is dropped, and the whole graphic when too few entries remain
# for it to stand (validate_structure's counts); nothing is ever replaced.
_GRAPHIC_MIN_ENTRIES = {"timeline": 2, "route": 2, "stat_cards": 1}
_GRAPHIC_PREFIXES = ("Timeline:", "Scale graphic:", "Scale reference:", "Number card:", "Cross-section", "Route stop:",
                     "Photo labels")


def _norm_words(text: str) -> str:
    """Lower-case words only; an apostrophe is kept inside a word ("ship's"), not as a quote mark."""
    text = re.sub(r"[^a-z0-9' ]", " ", (text or "").lower().replace("’", "'"))
    return " ".join(re.sub(r"(?<![a-z0-9])'|'(?![a-z0-9])", " ", text).split())


# A sentence opening on one of these leans on the one before it.
_BACK_REFERENCE = re.compile(r"^\W*(?:this|that|these|those|it|its|they|their|such|the\s+same|both|he|she|his|her|but|then|yet|so"
                             r"|still|instead|later|there|here)\b",
                             re.IGNORECASE)


def _split_sentences(text: str) -> List[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if x.strip()]


def _graphic_lines(key: str, value: Any) -> List[str]:
    """A graphic's text exactly as the fact checkers are shown it (ClaimFactChecker._material)."""
    from .fact_checker import ClaimFactChecker
    bare = {"narration_script": "", "caption": "", "first_comment": "", "story_slides": [], "carousel_slides": [],
            "thumbnail": {k: "" for k in ("eyebrow", "line1", "line2", "tagline")}, key: value}
    try:
        material = ClaimFactChecker._material(bare)
    except (KeyError, TypeError, ValueError, AttributeError):
        return []
    return [ln for ln in material.splitlines() if ln.startswith(_GRAPHIC_PREFIXES)]


# ---------------------------------------------------------------- graphic edits (production, Derinkuyu 2026-10-29)
# The surgical reviser was shown the draft as indented JSON and asked for find/replace text: to drop a stat card it
# copied the card's JSON (',\n  {\n   "value": "8 to 18",\n   "label": "Subterranean level...'), which no text
# search of the draft can find, and the whole draft was rewritten. A graphic is now edited by its fields: an edit
# names the field or entry by path (target), and a JSON fragment without one is matched to the one entry it quotes.
_GRAPHIC_PATH = re.compile(r"^\s*(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*(?:\.\s*(\w+))?\s*$")
_NESTED_PATH = re.compile(r"^\s*(\w+)\s*\.\s*(\w+)\s*\[\s*(\d+)\s*\]\s*(?:\.\s*(\w+))?\s*$")
_JSON_PAIR = re.compile(r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*("?)|-?\d+(?:\.\d+)?|true|false|null)')
_JSON_SYNTAX = re.compile(r'[\s,{}\[\]":]+')


def graphic_paths(draft: Dict[str, Any]) -> List[str]:
    """Each graphic field of `draft` as 'path: value' ("stat_cards[1].value: 280 ft"), entries numbered from 1:
    what the surgical reviser targets."""
    out = []
    for field in GRAPHICS_FIELDS:
        value = draft.get(field)
        items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        for i, item in enumerate(items, 1):
            at = f"{field}[{i}]" if isinstance(value, list) else field
            if isinstance(item, dict):
                for k, v in item.items():
                    if isinstance(v, list):   # a cross-section's levels
                        out += [f"{at}.{k}[{j}].{sk}: {sv}" for j, sub in enumerate(v, 1)
                                for sk, sv in (sub.items() if isinstance(sub, dict) else [])]
                    elif v is not None:
                        out.append(f"{at}.{k}: {v}")
            elif item is not None:
                out.append(f"{at}: {item}")
    return out


def _drop_entry(draft: Dict[str, Any], field: str, index: int) -> None:
    """Removes entry `index` (0-based) of a list graphic; the whole graphic when too few entries remain for it."""
    items = list(draft.get(field) or [])
    del items[index]
    draft[field] = items if len(items) >= _GRAPHIC_MIN_ENTRIES.get(field, 1) else None


def _json_value(raw: str):
    """A JSON fragment's value: a string (a cut-off one too), a number, true/false or null."""
    if raw.startswith('"'):
        body = raw[1:-1] if len(raw) > 1 and raw.endswith('"') else raw[1:]
        try:
            return json.loads(f'"{body}"')
        except ValueError:
            return body
    return json.loads(raw)


def _replaced(old: str, find: str, replace: str) -> Optional[str]:
    """`old` with `find` replaced (the whole of it when `find` is "" or `old` itself); None when it is not there."""
    if find and find in old:
        return old.replace(find, replace)
    if not find or find == old:
        return replace
    return None


def _set_field(entry: Dict[str, Any], key: str, find: str, replace: str) -> bool:
    """entry[key] edited: text by find/replace, a number or a flag set to the value `replace` gives."""
    if key not in entry:
        return False
    old = entry[key]
    if isinstance(old, bool):
        flag = replace.strip().lower()
        if flag not in ("true", "false"):
            return False
        entry[key] = flag == "true"
    elif isinstance(old, (int, float)):
        try:
            entry[key] = float(replace) if isinstance(old, float) else int(float(replace))
        except ValueError:
            return False
    elif isinstance(old, str):
        new = _replaced(old, find, replace)
        if new is None:
            return False
        entry[key] = new
    else:
        return False
    return True


def graphic_edit(draft: Dict[str, Any], target: Optional[str], find: str, replace: str) -> bool:
    """Applies one edit to a graphic of `draft` (changed in place) by its fields, never by a text search of the
    serialized draft: by `target` (a graphic_paths path), else by the one entry a JSON fragment in `find` quotes.
    An entry removed takes its graphic with it when too few entries remain (_GRAPHIC_MIN_ENTRIES). False, with
    the draft unchanged, when the edit names no graphic it can apply to."""
    import copy
    work = copy.deepcopy(draft)
    done = (_apply_targeted(work, target, find, replace) if (target or "").strip()
            else _apply_fragment(work, find, replace))
    if done:
        draft.clear()
        draft.update(work)
    return done


def _apply_nested(draft: Dict[str, Any], m, find: str, replace: str) -> bool:
    """A field of a graphic's own list ("cross_section.levels[2].label"), or that list's entry removed
    ("cross_section.levels[2]" with replace ""); a cross-section left with fewer than 2 levels is removed."""
    field, key, j, sub = m.group(1), m.group(2), int(m.group(3)) - 1, m.group(4)
    value = draft.get(field)
    items = value.get(key) if isinstance(value, dict) else None
    if not isinstance(items, list) or not 0 <= j < len(items):
        return False
    if sub is None:
        if replace.strip():
            return False
        del items[j]
        if len(items) < 2:
            draft[field] = None
        return True
    return isinstance(items[j], dict) and _set_field(items[j], sub, find, replace)


def _apply_targeted(draft: Dict[str, Any], target: str, find: str, replace: str) -> bool:
    nested = _NESTED_PATH.match(target or "")
    if nested:
        return nested.group(1) in GRAPHICS_FIELDS and _apply_nested(draft, nested, find, replace)
    m = _GRAPHIC_PATH.match(target or "")
    if not m or m.group(1) not in GRAPHICS_FIELDS or draft.get(m.group(1)) is None:
        return False
    field, index, key = m.group(1), m.group(2), m.group(3)
    value, gone = draft[field], not replace.strip()
    if isinstance(value, dict):   # the scale fact, the cross-section: one entry, no index
        if index is not None:
            return False
        if key is None:
            if gone:
                draft[field] = None
            return gone
        return _set_field(value, key, find, replace)
    if not isinstance(value, list):
        return False
    if index is None:   # the whole graphic: only removed
        if gone and key is None:
            draft[field] = None
        return gone and key is None
    i = int(index) - 1
    if not 0 <= i < len(value):
        return False
    entry = value[i]
    if key is None:
        if gone:
            _drop_entry(draft, field, i)
            return True
        if isinstance(entry, str):   # an emphasis word, a photo label
            new = _replaced(entry, find, replace)
            if new is None:
                return False
            value[i] = new
            return True
        return _update_entry(entry, replace)
    if not isinstance(entry, dict) or not _set_field(entry, key, find, replace):
        return False
    if isinstance(entry[key], str) and not entry[key].strip():
        _drop_entry(draft, field, i)   # an entry with an empty field says nothing
    return True


def _update_entry(entry: Any, replace: str) -> bool:
    """The fields a JSON fragment in `replace` quotes, set on `entry` (only fields it already has)."""
    new = {k: _json_value(v) for k, v, _ in _JSON_PAIR.findall(replace or "")}
    if not isinstance(entry, dict) or not new or any(k not in entry for k in new):
        return False
    entry.update(new)
    return True


def _apply_fragment(draft: Dict[str, Any], find: str, replace: str) -> bool:
    """An edit whose `find` is a piece of a graphic's JSON: the one entry whose fields it quotes is changed to the
    fields `replace` quotes, or removed when `replace` holds nothing but JSON syntax. A quoted word of a word list
    (emphasis words, photo labels) is replaced or removed the same way."""
    find = find or ""
    pairs = _JSON_PAIR.findall(find)
    if not pairs:
        word = _JSON_SYNTAX.sub(" ", find).strip()
        if '"' not in find or not word:
            return False
        for field in ("emphasis_words", "photo_labels"):
            items = draft.get(field) or []
            if word in items:
                i, new = items.index(word), _JSON_SYNTAX.sub(" ", replace or "").strip()
                if new:
                    items[i] = new
                else:
                    _drop_entry(draft, field, i)
                return True
        return False
    wanted = [(k, _json_value(v), v.startswith('"') and not closed) for k, v, closed in pairs]

    def same(have: Any, want: Any, cut: bool) -> bool:
        if cut:   # the fragment's last string, cut off: a prefix of the field
            return isinstance(have, str) and have.startswith(want)
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            return isinstance(have, (int, float)) and not isinstance(have, bool) and float(have) == float(want)
        return have == want

    def matches(entry: Any) -> bool:
        return isinstance(entry, dict) and all(same(entry.get(k), v, cut) for k, v, cut in wanted)
    hits = [(field, i) for field in GRAPHICS_FIELDS if isinstance(draft.get(field), list)
            for i, entry in enumerate(draft[field]) if matches(entry)]
    hits += [(field, None) for field in GRAPHICS_FIELDS if isinstance(draft.get(field), dict) and matches(draft[field])]
    if len(hits) != 1:
        return False   # no entry, or more than one it could mean
    field, i = hits[0]
    if not _JSON_SYNTAX.sub("", replace or ""):
        if i is None:
            draft[field] = None
        else:
            _drop_entry(draft, field, i)
        return True
    return _update_entry(draft[field][i] if i is not None else draft[field], replace)


def without_blocked_sentences(payload: Dict[str, Any], notes: str) -> Optional[Dict[str, Any]]:
    """The draft with the sentences a checker quoted as blocking taken out, or None when there is nothing safe to
    take out (the reason is logged). Used once, after the last rewrite failed on a claim or two (production
    2026-10-25: three Antikythera drafts, the last blocked only on "The bronze lump was pulled specifically from the
    Aegean Sea."). A sentence goes from wherever it is read: the narration, both captions, the first comment, a
    story or carousel slide's body; a graphic's entry is dropped (a claim outside the narration still rejected the
    script: dev, Paracas, 2026-10-26). Nothing is written, so nothing new can be claimed. Headlines (title, cover,
    slide headings, poll) and set pieces (the hook, the closing sentence, each caption's first line, a body's only
    sentence) are never removed: a blocked claim there means no trim. The result must pass every check again
    (structure, spoken length and both fact checkers), so a gap that leaves it too short, or a claim repeated in
    other words, still fails."""
    quoted = f" {_norm_words(notes)} "  # whole words: a sentence must appear in the notes word for word

    def blocked(text: str) -> bool:
        words = _norm_words(text)
        return len(words.split()) >= _TRIM_MIN_WORDS and f" {words} " in quoted

    def refuse(why: str) -> None:
        log.info("The blocked claims are not trimmed (%s); the rejection stands.", why)
        return None

    def dangling(parts: List[str], hits: List[str]) -> Optional[str]:
        """The kept sentence right after a removed one that points back at it ("It sank in 60 BC. That date makes
        it the oldest..."): left on its own it says nothing, or the wrong thing."""
        for i, x in enumerate(parts):
            if x in hits:
                after = next((y for y in parts[i + 1:] if y not in hits), None)
                if after and _BACK_REFERENCE.match(after):
                    return after
        return None

    removed: Dict[str, List[str]] = {}  # each removed text (as first written) -> where it was read
    graphic_claims: Dict[str, str] = {}  # a removed graphic line -> the words it was matched on (without its label)

    def take(text: str, where: str, claim: Optional[str] = None) -> None:
        """Records `text` as removed from `where`: the same words read in two places are one entry. `claim` marks a
        graphic's line and gives the words it was matched on."""
        key = next((k for k in removed if _norm_words(k) == _norm_words(text)), text)
        places = removed.setdefault(key, [])
        if where not in places:
            places.append(where)
        if claim is not None:
            graphic_claims[key] = claim

    def claim_count() -> int:
        """How many claims the removals are: each removed sentence is one (two sentences are one only in the same
        words), and a graphic's line adds one only when no removed sentence holds its claim and it holds none, so a
        graphic never links two sentences into one claim."""
        sentences = {f" {_norm_words(k)} " for k in removed if k not in graphic_claims}
        count = len(sentences)
        for line, claim in graphic_claims.items():
            words = f" {_norm_words(claim)} "
            if not any(words in x or x in words for x in sentences):
                count += 1
        return count

    # Headlines and set pieces: a blocked claim in one of them can't be taken out.
    thumb = payload.get("thumbnail") or {}
    fixed = [("the title", payload.get("title")), ("the hook", payload.get("hook_sentence")),
             ("the closing sentence", payload.get("closing_sentence")),
             ("the cover", " ".join(str(thumb.get(k) or "") for k in ("eyebrow", "line1", "line2", "tagline"))),
             ("the poll", (payload.get("poll") or {}).get("question"))]
    # A heading as each checker reads it: "line1 line2" (the first checker), and after the kicker (the second).
    for i, s in enumerate(payload.get("story_slides") or [], 1):
        fixed += [(f"story slide {i}'s heading", f"{s.get(k) or ''} {s.get('line1') or ''} {s.get('line2') or ''}")
                  for k in ("", "kicker")]
    fixed += [(f"carousel slide {i}'s heading", c.get("heading"))
              for i, c in enumerate(payload.get("carousel_slides") or [], 1)]
    for where, text in fixed:
        if text and blocked(text):
            return refuse(f"{where} carries one")

    out = dict(payload)
    sentences = _split_sentences(payload.get("narration_script"))
    keep_ends = {_norm_words(payload.get("hook_sentence")), _norm_words(payload.get("closing_sentence"))}
    cut = [x for x in sentences if blocked(x)]
    for x in cut:
        i = sentences.index(x)
        if not 0 < i < len(sentences) - 1 or _norm_words(x) in keep_ends:
            return refuse("the narration's opening or closing sentence carries one")
        take(x, "narration")
    after = dangling(sentences, cut)
    if after:
        return refuse(f"the narration's next sentence points back at it ({after[:60]!r})")
    if cut:
        out["narration_script"] = " ".join(x for x in sentences if x not in cut)

    for field, name in (("caption", "caption"), ("carousel_caption", "carousel caption")):
        text = payload.get(field)
        if not text:
            continue
        lines, first, changed = text.split("\n"), True, False
        whole = [x for ln in lines if ln.strip() and not ln.lstrip().startswith("#") for x in _split_sentences(ln)]
        after = dangling(whole, [x for x in whole if blocked(x)])
        if after:
            return refuse(f"the {name}'s next sentence points back at it ({after[:60]!r})")
        for n, line in enumerate(lines):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = _split_sentences(line)
            hits = [x for x in parts if blocked(x)]
            if hits and first:
                return refuse(f"the {name}'s first line carries one")
            first = False
            if hits:
                for x in hits:
                    take(x, name)
                lines[n] = " ".join(x for x in parts if x not in hits) or None  # a line left empty goes
                changed = True
        if changed:
            out[field] = re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if ln is not None)).strip()

    def body_without(body: str, where: str, poll: bool = False) -> Optional[str]:
        """The body less its blocked sentences, or None when nothing would be left of it (on the poll frame, which
        drops a body's questions (multi_format), a question alone is nothing)."""
        parts = _split_sentences(body)
        hits = [x for x in parts if blocked(x)]
        if not hits:
            return body
        rest = [x for x in parts if x not in hits]
        if not [x for x in rest if not (poll and x.rstrip().endswith("?"))] or dangling(parts, hits):
            return None
        for x in hits:
            take(x, where)
        return " ".join(rest)

    comment = payload.get("first_comment")
    if comment:
        kept = body_without(comment, "first comment")
        if kept is None:
            return refuse("the first comment would be left empty or pointing back at it")
        out["first_comment"] = kept
    for field, name in (("story_slides", "story slide"), ("carousel_slides", "carousel slide")):
        slides = payload.get(field) or []
        new = []
        for i, s in enumerate(slides, 1):
            body = s.get("body") or ""
            kept = body_without(body, f"{name} {i}", poll=field == "story_slides" and i == len(slides))
            if kept is None:
                return refuse(f"{name} {i}'s body would be left empty or pointing back at it")
            new.append(dict(s, body=kept) if kept != body else s)
        if slides:
            out[field] = new

    for key in ("timeline", "route", "stat_cards"):
        entries = payload.get(key) or []
        keep = []
        for e in entries:
            hits = [ln for ln in _graphic_lines(key, [e]) if blocked(ln.split(":", 1)[-1])]
            for ln in hits:
                take(ln, key.replace("_", " "), claim=ln.split(":", 1)[-1])
            if not hits:
                keep.append(e)
        if len(keep) != len(entries):
            out[key] = keep if len(keep) >= _GRAPHIC_MIN_ENTRIES[key] else None
    for key in ("scale_fact", "cross_section", "photo_labels"):
        if payload.get(key):
            hits = [ln for ln in _graphic_lines(key, payload[key]) if blocked(ln.split(":", 1)[-1])]
            for ln in hits:
                take(ln, key.replace("_", " "), claim=ln.split(":", 1)[-1])
            if hits:
                out[key] = None

    if not removed:
        return refuse("no blocked claim is quoted word for word in a sentence that can be taken out")
    claims = claim_count()
    if claims > MAX_TRIMMED_SENTENCES:
        return refuse(f"{claims} claims to take out; more than {MAX_TRIMMED_SENTENCES} is a rewrite's")
    if out.get("emphasis_words"):  # a highlight on words the narration no longer says is dropped (not a claim)
        narration = (out.get("narration_script") or "").lower()
        out["emphasis_words"] = [w for w in out["emphasis_words"] if w.lower() in narration] or None
    out["word_count"] = len((out.get("narration_script") or "").split())
    out["trimmed_sentences"] = list(removed)
    out["trimmed_from"] = [", ".join(w) for w in removed.values()]
    return out


# ---------------------------------------------------------------- share and follow (Meta's creator tips, 2026-10-25)
SHARE_LINE_MAX = 90
SHARE_FALLBACK = "Share this with someone who loves a good mystery, and follow for the next one."
_SHARE_VERBS = re.compile(r"\b(share|send|pass|tag|forward|show)\b", re.IGNORECASE)
_HASHTAG_LINE = re.compile(r"^(?:\s*#\w+)+\s*$")


def share_line_problems(line: str) -> List[str]:
    """Why a writer's share line can't be used: it must be short, ask for a share and a follow, carry no
    hashtags (they live on the caption's last line), at most one emoji and no engagement bait ("share with 5
    friends", "tag someone": the end card would show it)."""
    line = " ".join((line or "").split())
    problems = []
    if len(line) > SHARE_LINE_MAX:
        problems.append(f"{len(line)} characters; at most {SHARE_LINE_MAX}.")
    if "#" in line:
        problems.append("no hashtags in it.")
    if len(EMOJI_RE.findall(line)) > 1:
        problems.append("at most one emoji.")
    if not re.search(r"\bfollow", line, re.IGNORECASE):
        problems.append("it must invite the viewer to follow.")
    if not _SHARE_VERBS.search(line):
        problems.append("it must ask the viewer to share (send, pass on) this.")
    elif is_stock_share(line):
        problems.append("it is stock phrasing: name who, for this story (\"the codebreaker you know\", "
                        "\"a friend who loves Roman engineering\"), not \"a friend\" or \"someone who loves history\".")
    if engagement_bait(line):
        problems.append(f"it is engagement bait ({'; '.join(engagement_bait(line))}), which Meta demotes: name who, "
                        "for this story, never a number of people or a tag.")
    return problems


# Words any share ask could use: a line made only of these names nobody in particular ("Share this with a friend",
# "Send this to someone who loves history"). The end card shows the share half on its own, so it has to be about
# this story (operator, 2026-10-25: the first version was "a wall of stock text").
_STOCK_SHARE_WORDS = set("""
share shares send sends pass passing tag forward show this it these that on to with a an the your you you'll you've
friend friends someone somebody anyone everyone who who'd whod that'd would will could should 'd and or for of in one
ones family mate mates buddy bestie people person know knows needs need must see them they their wants want also too
really loves love likes like enjoys enjoy into fan fans buff buffs lover lovers history historical mystery mysteries
mysterious good great cool amazing interesting curious fascinating story stories video reel post clip next more
follow following relic relics riddle riddles thing things stuff fact facts world ancient past
""".split())


def is_stock_share(line: str) -> bool:
    """Whether a share line's ask names nobody in particular: once its follow clause is set aside, every word is
    one any video could use."""
    from .end_card import card_text
    words = re.findall(r"[a-z']+", card_text(line).lower())
    return bool(words) and all(w.strip("'") in _STOCK_SHARE_WORDS or w in _STOCK_SHARE_WORDS for w in words)


def share_repeats_narration(line: str, narration: str) -> bool:
    """Whether the narration's last sentence already makes the share ask the card would repeat (the same ask
    twice in three seconds)."""
    from .end_card import card_text
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", (narration or "").strip()) if s]
    if not sentences or not _SHARE_VERBS.search(sentences[-1]):
        return False
    content = lambda text: {w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOCK_SHARE_WORDS}
    ask, last = content(card_text(line)), content(sentences[-1])
    return not ask or len(ask & last) / len(ask) >= 0.5


def share_prompt(payload: Dict[str, Any]) -> str:
    """The share-and-follow line a run shows: the writer's, when it passes its checks, else a plain one."""
    line = " ".join(str((payload or {}).get("share_line") or "").split())
    return line if line and not share_line_problems(line) else SHARE_FALLBACK


def end_card_share(payload: Dict[str, Any]) -> Tuple[str, bool]:
    """The end card's share line (the card says FOLLOW itself) and whether it is the plain fallback, which the
    review card notes: the writer's line when it passes its checks and does not repeat the narration's last
    sentence, else the fallback's."""
    from .end_card import card_text
    line = " ".join(str((payload or {}).get("share_line") or "").split())
    if line and not share_line_problems(line) and not share_repeats_narration(line, (payload or {}).get("narration_script", "")):
        text = card_text(line)
        if len(text.split()) >= 3:
            return text, False
    return card_text(SHARE_FALLBACK), True


def with_share_line(text: str, line: str) -> str:
    """`text` (a caption) with `line` as its own paragraph before the closing hashtag line(s); unchanged when the
    line is already there."""
    text = (text or "").rstrip()
    if not line or line in text:
        return text
    lines = text.split("\n")
    i = len(lines)
    while i > 0 and (not lines[i - 1].strip() or _HASHTAG_LINE.match(lines[i - 1])):
        i -= 1
    head = "\n".join(lines[:i]).rstrip()
    tail = [ln for ln in lines[i:] if ln.strip()]
    return "\n\n".join(x for x in (head, line, "\n".join(tail)) if x)


def duration_problem(seconds: float) -> Optional[str]:
    """Why a measured voiceover can't make a Reel (the master is exactly as long), or None."""
    if seconds < TARGET_DURATION_MIN:
        return f"Voiceover is {seconds:.1f}s; too short for the {TARGET_DURATION_MIN:g}s minimum."
    if seconds > config.VOICE_DURATION_CEILING:  # a recorded take; the script is planned under HARD_DURATION_CEILING
        return (f"Voiceover is {seconds:.1f}s; hard ceiling is {config.VOICE_DURATION_CEILING:g}s. A re-render shortens "
                "the script for this voice's measured pace.")
    return None


YES_NO_START = re.compile(r"^(is|are|was|were|do|does|did|can|could|would|will|should|has|have|had|must|might)\b", re.I)
YES_NO_ANSWERS = {"yes", "no", "maybe", "not sure", "definitely", "no way", "of course", "never", "absolutely"}
# An option that opens with one of these words answers a yes/no question ("Yes, easily", "No chance", "Nope").
YES_NO_FIRST_WORDS = {"yes", "no", "yeah", "yep", "yup", "nope", "nah", "maybe", "perhaps", "definitely", "absolutely",
                      "never", "certainly", "surely", "probably", "unlikely", "possibly"}
POLL_OPTION_MAX = 24
POLL_QUESTION_MAX = 60


def _is_yes_no_question(question: str) -> bool:
    q = question.strip()
    return bool(YES_NO_START.match(q)) and not re.search(r"\bor\b", q, re.I)


def _is_yes_no_answer(option: str) -> bool:
    o = option.strip(" .!?").lower()
    first = re.split(r"[\s,.!?;:]+", o, maxsplit=1)[0] if o else ""
    return o.split(",")[0] in YES_NO_ANSWERS or first in YES_NO_FIRST_WORDS


def poll_problems(question: str, options: List[str]) -> List[str]:
    """The poll's options must answer its question: a yes/no question gets yes/no options, otherwise
    the question has to offer the choice ("Which...?", "X or Y?"). A yes/no question with two contrary
    choices is reworded by repair_poll before this is checked; what is left here is a genuine mismatch
    (one yes/no option and one choice, or a reworded question that would not fit)."""
    problems = []
    if len(options) != 2:
        problems.append(f"The poll needs exactly 2 options, got {len(options)}.")
    if not question.strip() or len(question) > POLL_QUESTION_MAX:
        problems.append(f"The poll question must be 1-{POLL_QUESTION_MAX} characters.")
    long = [o for o in options if len(o) > POLL_OPTION_MAX]
    if long:
        problems.append(f"Poll options must be at most {POLL_OPTION_MAX} characters: {long}.")
    q = question.strip()
    if _is_yes_no_question(q) and not all(_is_yes_no_answer(o) for o in options):
        problems.append(f"The poll asks a yes/no question ('{q}') but its options ({' / '.join(options)}) are not yes/no "
                        "answers; ask an either/or question the two options answer, e.g. 'Planned or lucky?'.")
    return problems


def repair_poll(question: str, options: List[str], narration: str = "") -> Optional[str]:
    """The either/or question a yes/no poll's two choices already answer, or None when it needs none or
    can't have one. The rule: a yes/no question whose two options are both choices (neither a yes/no
    answer) becomes "{option A} or {option B}?", when that fits the question limit. The options are the
    answers the viewer taps, so asking them as the question keeps what the poll offers and adds no claim
    (their words were fact-checked with the rest of the copy). A mixed pair ("Yes" / "Hidden somewhere")
    has no question both answer and is still sent back, as is one too long to ask. A whole writer attempt
    was spent on exactly this (production, Hal Saflieni 2026-10-27: "Was the acoustic design
    intentional?" with "Deliberate design" / "Accidental resonance")."""
    if len(options) != 2 or not _is_yes_no_question(question):
        return None
    a, b = (o.strip().rstrip(".!?").strip() for o in options)
    if not a or not b or _is_yes_no_answer(a) or _is_yes_no_answer(b):
        return None
    first = b.split()[0]
    # The second option's capital is lowered mid-question unless the narration uses that word capitalised
    # mid-sentence (a name stays a name).
    proper = first[:1].isupper() and (first.isupper() or any(
        m.start() > 0 and narration[:m.start()].rstrip()[-1:] not in ".!?"
        for m in re.finditer(r"\b" + re.escape(first) + r"\b", narration)))
    reworded = f"{a} or {b if proper else b[:1].lower() + b[1:]}?"
    return reworded if len(reworded) <= POLL_QUESTION_MAX else None


# ---------------------------------------------------------------- the hook (Meta's Creator assistant, 2026-10-28)
# On the operator's Reel: "Lead with the mystery ... the initial phrase 'This Massive Hindu' doesn't immediately
# reveal the mystery"; viewers decide in under two seconds and about 71% had scrolled away by the fourth. The
# first caption burst is the first two to five words of the first sentence (video_composer's partition), so the
# sentence itself has to open on the fact or the riddle. Checked on a writer's new drafts (draft_problems), and
# sent back with whatever else a rewrite fixes, at no extra call: a draft whose only fault is its opening is
# kept and the finding recorded (HOOK_FORCES_REWRITE), and kept and library scripts written before the rule
# stay usable (validate_structure leaves the opening alone) rather than being paid for again.
HOOK_PROBLEM = "Hook: "         # every hook problem starts so; the writer loop recognises them
HOOK_FORCES_REWRITE = False     # True: a weak opening alone costs a writer rewrite
HOOK_NUMBER_WITHIN = 4          # a "This X was" opening is a stark claim when its number comes this early
_NUMBER_WORDS = set("""two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty sixty
hundred hundreds thousand thousands million millions billion billions dozen dozens half""".split())
# Openings that name or introduce before they say anything: never a hook, whatever follows.
_LABEL_OPENERS = re.compile(
    r"^(?:this\s+is|these\s+are|that\s+is|those\s+are|here(?:'s|’s|\s+is|\s+are)|meet|welcome\s+to|introducing"
    r"|let(?:'s|’s|\s+me|\s+us)|today\s+we|in\s+this\s+(?:video|reel)|we(?:'re|’re|\s+are)\s+(?:going|looking|exploring)"
    r"|the\s+(?:story|history|tale|legend)\s+of|have\s+you\s+ever|ever\s+wondered|did\s+you\s+know"
    r"|what\s+if\s+i\s+told\s+you)\b", re.I)
# "This massive temple was ...": the subject's label first, the mystery after it.
_DEMONSTRATIVE_LABEL = re.compile(
    r"^(?:this|these|that|those)\s+(?:[\w'’-]+\s+){0,4}?(?:is|was|are|were|has|had|have|stands|stood|sits|sat"
    r"|lies|lay|remains)\b", re.I)
# "The X Mechanism is a ...", "Trepanation is the ...": a definition (capitalised name, then is/was a/the).
_DEFINITION_LABEL = re.compile(r"^(?:The\s+)?(?:[A-Z][\w'’-]*\s+){1,5}(?:is|was|are|were)\s+(?:a|an|the|one)\b")


def _first_sentence(narration: str) -> str:
    parts = [s for s in _SENTENCE_END.split((narration or "").strip()) if s.strip()]
    return parts[0].strip() if parts else ""


def _spoken_words(sentence: str) -> List[str]:
    """The sentence's words as they are said and captioned (a dash between two words separates them)."""
    return [w for w in re.split(r"[\s—–]+", sentence) if re.search(r"\w", w)]


# ---------------------------------------------------------------- the story the draft tells (Derinkuyu, 2026-10-29)
# Production's Derinkuyu run passed every gate with weak content: its narration never named Cappadocia or Turkey (the
# map was rendered and placed on no beat: the planner puts it on a line naming the place), its title and caption told
# the 1963 discovery while the narration listed measurements, and "280 feet (85 m)" stood in the narration. These are
# checked on every writer draft (draft_problems, with the topic the run is writing) and fixed by one short edit of
# the narration's opening (ScriptEngine._edit_story), never a full rewrite; a draft the edit cannot fix is kept with
# the finding, as a weak hook is (blocking_problems).
STORY_PROBLEM = "Story: "      # every story problem starts so; the writer loop recognises them
PLACE_WITHIN = 3               # sentences: the site is placed (region or country) this early, where the map can go
import contextvars as _contextvars
_draft_topic: _contextvars.ContextVar = _contextvars.ContextVar("script_draft_topic", default=None)
# A bracketed aside in the narration: a unit conversion ("(85 m)") or any other. The voice never says it
# (audio_engine.normalize_speech_text), so the narration does not carry it either: its words are what is said,
# captioned and measured. The captions and graphics keep their own figures.


def spoken_narration(text: str) -> str:
    """`text` without bracketed asides ("280 feet (85 m) underground" -> "280 feet underground"), exactly as the
    voice drops them (audio_engine.strip_asides: nested ones whole)."""
    if "(" not in (text or "") and ")" not in (text or ""):
        return text
    out = audio_engine.strip_asides(text)
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", out)).strip()


def _folded(text: str) -> str:
    from .unesco import _fold
    return " " + " ".join(re.findall(r"[a-z0-9]+", _fold(str(text or "")))) + " "


def place_names(topic: Optional[Dict[str, Any]]) -> List[str]:
    """The names that place the topic's site for a viewer: its region's parts, its city and its country (from the
    topic's location), less any the title already says (the site's own name places nothing)."""
    loc = (topic or {}).get("location") or {}
    title = _folded((topic or {}).get("title", "")) + _folded(loc.get("name", ""))
    names = []
    for part in [*re.split(r"\s*[/,;]\s*", str(loc.get("region") or "")), loc.get("city"), loc.get("country")]:
        part = str(part or "").strip()
        if part and _folded(part).strip() and _folded(part) not in title and part not in names:
            names.append(part)
    return names


def place_problems(narration: str, topic: Optional[Dict[str, Any]]) -> List[str]:
    """The site is never placed in the narration's first PLACE_WITHIN sentences."""
    names = place_names(topic)
    if not names:
        return []
    opening = _folded(" ".join(_split_sentences(narration)[:PLACE_WITHIN]))
    if any(_folded(n) in opening for n in names):
        return []
    return [f"{STORY_PROBLEM}the narration never places the site in its first {PLACE_WITHIN} sentences: name "
            f"{' or '.join(repr(n) for n in names)} in one of them (the map is shown on that line), keeping it a "
            "verified fact."]


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i > 1}
_ORDINALS = {w: i for i, w in enumerate("_ first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
                                        "twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
                                        "nineteenth twentieth".split()) if i > 0}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 10 ** 6, "billion": 10 ** 9}


def spelled_numbers(text: str) -> List[float]:
    """Numbers written as words: "two thousand" -> 2000, "twenty-five" -> 25, "eighth" -> 8, "Five" -> 5."""
    out, total, current, seen = [], 0, 0, False
    for word in re.findall(r"[a-z]+", (text or "").lower()) + ["."]:
        if word in _UNITS or word in _TENS or word in _ORDINALS:
            current += _UNITS.get(word, _TENS.get(word, _ORDINALS.get(word, 0)))
            seen = True
        elif word in _SCALES and seen:
            if word == "hundred":
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * _SCALES[word]
                current = 0
        elif word == "and" and seen:
            continue
        else:
            if seen:
                out.append(float(total + current))
            total, current, seen = 0, 0, False
    return out


def told_story_problems(pkg: ScriptPackage) -> List[str]:
    """The title and the caption's first line tell a story the narration does not: a figure or year they state
    that the narration never says (production Derinkuyu: "An ordinary basement renovation in 1963 uncovered an
    entire hidden civilization" over a narration of measurements)."""
    first_line = next((ln for ln in (pkg.caption or "").splitlines() if ln.strip()), "")
    # Said as digits, as words ("two thousand", "eighth") or in the other unit (280 feet / 85 meters): the same figure.
    said = fact_sheet.numbers_in(pkg.narration_script) + spelled_numbers(pkg.narration_script)
    missing = []
    for text in (pkg.title, first_line):
        for n in fact_sheet.numbers_in(text or ""):   # the headline's digits; the narration may say them in words
            if not fact_sheet._number_supported(n, said) and n not in missing:
                missing.append(n)
    if not missing:
        return []
    figures = ", ".join(f"{n:g}" for n in missing)
    return [f"{STORY_PROBLEM}the title and caption tell a story the narration never says (they state {figures}): "
            "the narration tells the same story, opening on its most surprising discovery or human stakes, or the "
            "title and caption follow the narration."]


# ---------------------------------------------------------------- headline figures (review rc72, item 15)
# Titles and covers are exempt from the hedge rule, and their figures were verified by nobody: the development
# obelisk run's title and cover said "1,200-TON" where the fact sheet gives 1,090-1,168 t. Every figure in the
# title, the cover (thumbnail) and the caption's first line must now be the verified sheet's: an undisputed figure
# as it is; a disputed one only as a floor at or below its range's minimum ("1,000+ tonnes"); one only a single
# estimate gives ("by one estimate") never, since a headline cannot carry the hedge. A miss goes to the copy edit.
HEADLINE_PROBLEM = "Headline figure in the "
HEADLINE_MIN = 10          # smaller numbers ("3 secrets", "Top 5") are not checked
# A unit converts only to another of its own kind (ft/m, t/lb, mi²/km²), and a bare number (a year, a count) to
# nothing: the obelisk's "1,200-ton" once passed as the year 1922 in miles. Each unit: (kind, its size in the kind's
# base unit). "ton" is read as the tonne, as headlines use it.
_UNIT_SIZES = {
    "m": ("length", 1.0), "metre": ("length", 1.0), "meter": ("length", 1.0), "metres": ("length", 1.0),
    "meters": ("length", 1.0), "km": ("length", 1000.0), "kilometre": ("length", 1000.0),
    "kilometer": ("length", 1000.0), "kilometres": ("length", 1000.0), "kilometers": ("length", 1000.0),
    "ft": ("length", 0.3048), "foot": ("length", 0.3048), "feet": ("length", 0.3048), "yard": ("length", 0.9144),
    "yards": ("length", 0.9144), "mi": ("length", 1609.34), "mile": ("length", 1609.34),
    "miles": ("length", 1609.34),
    "t": ("mass", 1000.0), "ton": ("mass", 1000.0), "tons": ("mass", 1000.0), "tonne": ("mass", 1000.0),
    "tonnes": ("mass", 1000.0), "kg": ("mass", 1.0), "kilogram": ("mass", 1.0), "kilograms": ("mass", 1.0),
    "lb": ("mass", 0.4536), "lbs": ("mass", 0.4536), "pound": ("mass", 0.4536), "pounds": ("mass", 0.4536),
    "km²": ("area", 1.0), "km2": ("area", 1.0), "sq km": ("area", 1.0), "square kilometre": ("area", 1.0),
    "square kilometer": ("area", 1.0), "square kilometres": ("area", 1.0), "square kilometers": ("area", 1.0),
    "sq mi": ("area", 2.58999), "square mile": ("area", 2.58999), "square miles": ("area", 2.58999),
    "acre": ("area", 0.004047), "acres": ("area", 0.004047), "hectare": ("area", 0.01),
    "hectares": ("area", 0.01), "ha": ("area", 0.01),
    "year": ("years", 1.0), "years": ("years", 1.0), "yr": ("years", 1.0), "yrs": ("years", 1.0),
    "%": ("percent", 1.0), "percent": ("percent", 1.0), "per cent": ("percent", 1.0),
}
_SAME_UNIT = {"metre": "m", "meter": "m", "metres": "m", "meters": "m", "feet": "ft", "foot": "ft", "tons": "t",
              "ton": "t", "tonne": "t", "tonnes": "t"}
_MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "million": 1e6, "billion": 1e9, "bn": 1e9}
_UNIT_WORDS = "|".join(re.escape(u) for u in sorted(_UNIT_SIZES, key=len, reverse=True))
_FIGURE = re.compile(
    r"(?:\b(?P<pre>over|more than|at least|nearly|almost|upwards of)\s+)?"
    r"(?<![\w.,])(?P<num>\d[\d,]*(?:\.\d+)?)(?P<ord>st|nd|rd|th)?"
    r"(?:\s*(?P<mult>k|thousand|million|billion|bn)(?![a-z]))?"
    r"(?P<plus>\s*\+)?"
    r"(?:[\s-]*(?P<unit>" + _UNIT_WORDS + r")(?![a-z]))?", re.IGNORECASE)
_RANGE_JOIN = re.compile(r"\s*(?:to|and|or|-|–|—)\s*", re.IGNORECASE)
_DATE_ERA = re.compile(r"\s*(?:(?P<bc>BCE|BC)|(?P<ad>AD|CE))\b")
# A capitalised word glued to a bare number names something ("Apollo 11", "Route 66", "Top 10"); these do not.
_FIGURE_LEADS = {"the", "a", "an", "in", "on", "at", "by", "of", "for", "from", "since", "after", "before", "until",
                 "over", "under", "nearly", "almost", "about", "around", "all", "its", "their", "only", "just",
                 "and", "or", "to", "with", "built", "found", "lost", "buried", "carved", "made"}
_SINGLE_ESTIMATE = re.compile(r"\b(?:by one estimate|one estimate|a single estimate|one source)\b", re.IGNORECASE)
_draft_sheet: _contextvars.ContextVar = _contextvars.ContextVar("script_draft_sheet", default=None)


class _Figure(NamedTuple):
    value: float           # as written, multiplier applied ("1.2K" -> 1200)
    floor: bool            # said as a floor: "1,000+", "over 1,000", "at least 1,000"
    kind: Optional[str]    # "length", "mass", "area", "years", "percent", "ordinal" or None (a bare number)
    unit: Optional[str]    # the unit as written, folded ("tons" -> "t")
    base: float            # in the kind's base unit
    era: Optional[str]     # "bc" / "ad" after a bare number: a date
    bare_name: bool        # glued to a capitalised name ("Apollo 11")


def _headline_texts(pkg: ScriptPackage) -> Dict[str, str]:
    first_line = next((ln for ln in (pkg.caption or "").splitlines() if ln.strip()), "")
    t = pkg.thumbnail
    return {"title": pkg.title or "", "thumbnail": " · ".join((t.eyebrow, t.line1, t.line2, t.tagline)),
            "caption": first_line}


def _figures(text: str) -> List[_Figure]:
    """The numbers in a text with their units. The first number of a range ("1,090 to 1,168 tonnes") takes the
    second's unit."""
    text = text or ""
    found = []
    for m in _FIGURE.finditer(text):
        try:
            value = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        value *= _MULTIPLIERS.get((m.group("mult") or "").lower(), 1.0)
        unit = (m.group("unit") or "").lower() or None
        era = _DATE_ERA.match(text, m.end())
        lead = re.search(r"([A-Za-z][\w'’]*)\s*$", text[:m.start()])
        found.append([m, value, unit, era, bool(m.group("ord")),
                      bool(lead and lead.group(1)[0].isupper() and lead.group(1).lower() not in _FIGURE_LEADS)])
    for here, after in zip(found, found[1:]):
        if not here[2] and not here[4] and after[2] and _RANGE_JOIN.fullmatch(text[here[0].end():after[0].start()]):
            here[2] = after[2]
            if not here[0].group("mult") and after[0].group("mult"):
                here[1] *= _MULTIPLIERS[after[0].group("mult").lower()]
    out = []
    for m, value, unit, era, ordinal, glued in found:
        kind, size = _UNIT_SIZES.get(unit, (None, 1.0)) if unit else ("ordinal" if ordinal else None, 1.0)
        floor = bool(m.group("plus")) or bool(m.group("pre"))
        out.append(_Figure(value, floor, kind, _SAME_UNIT.get(unit, unit), value * size,
                           (("bc" if era.group("bc") else "ad") if era and kind is None else None),
                           glued and kind is None and not floor))
    return out


def _same(n: _Figure, v: _Figure) -> bool:
    """The same figure: equal in the same unit, or within 3% once converted between units of one kind."""
    if n.kind != v.kind or not v.base:
        return False
    if n.unit == v.unit:
        return abs(n.value - v.value) <= 1e-9 * max(1.0, abs(v.value))
    return abs(n.base - v.base) <= 0.03 * abs(v.base)


def _related(n: _Figure, v: _Figure, spread: float = 1.5) -> bool:
    return n.kind == v.kind and bool(v.base) and n.base / spread <= v.base <= n.base * spread


def _said(value: float) -> str:
    return f"{value:,.0f}" if value >= 100 else f"{value:g}"


def _ages(fact: Dict[str, Any], year: int) -> List[float]:
    """How long ago each date in a fact was: "100 BC" -> 2126 in 2026, "1901" -> 125."""
    ages = []
    for f in _figures(f"{fact.get('statement', '')}"):
        if f.kind is None and f.era == "bc":
            ages.append(year + f.value - 1)
        elif f.kind is None and (f.era == "ad" or (f.value == int(f.value) and 1000 <= f.value <= year)):
            ages.append(year - f.value)
    return ages


def headline_problems(pkg: ScriptPackage, sheet: Optional[Dict[str, Any]] = None) -> List[str]:
    """Each headline field whose figures the verified sheet does not support as stated (see above)."""
    if sheet is None:
        sheet = _draft_sheet.get()
        sheet = sheet() if callable(sheet) else sheet   # the run's sheet, as the engine has it (never built here)
    facts = (sheet or {}).get("facts") or []
    if not facts:
        return []
    year = time.gmtime().tm_year
    figures = [(f, _figures(f"{f.get('statement', '')} {f.get('hedge', '')}")) for f in facts]
    problems = []
    for field, text in _headline_texts(pkg).items():
        wrong = []
        for n in _figures(text):
            if n.value < HEADLINE_MIN or n.bare_name:
                continue
            related = [f for f, vs in figures if any(_related(n, v) for v in vs)]
            single = [f for f in related if _SINGLE_ESTIMATE.search(f.get("hedge") or "")
                      and any(_same(n, v) for v in _figures(f.get("hedge") or ""))]
            if single:
                wrong.append(f"{_said(n.value)} (only one estimate gives it: a headline cannot carry the hedge; leave it out)")
                continue
            if any(_same(n, v) for f, vs in figures if f in related and not f.get("disputed") for v in vs):
                continue   # an undisputed, verified figure, said as it is
            # An age worked out from an undisputed date ("2,000-year-old" for 100 BC), never more than it
            if n.kind == "years" and any(0.8 * age <= n.value <= age * 1.0001 for f in facts
                                         if not f.get("disputed") for age in _ages(f, year)):
                continue
            if n.floor and related:
                # The range in the headline's own unit: the related figures, converted
                size = n.base / n.value if n.value else 1.0
                lowest = min(v.base / size for f, vs in figures if f in related for v in vs if _related(n, v))
                if n.value <= lowest * 1.0001:
                    continue   # a floor at or below the sourced range's minimum
                wrong.append(f"{_said(n.value)}+ (above the sourced minimum {lowest:,.0f})")
                continue
            disputed = any(f.get("disputed") for f in related)
            wrong.append(f"{_said(n.value)} ({'disputed: say it as a floor at or below the sourced minimum, e.g. a round number with +' if disputed else 'not in the verified facts'})")
        if wrong:
            problems.append(f"{HEADLINE_PROBLEM}{field}: " + "; ".join(wrong) + ". A headline states only verified "
                            "figures: an undisputed one as it is, a disputed one as a floor at or below its range's "
                            "minimum (\"1,000+\"), none that only one estimate gives.")
    return problems


def _opening_faults(problems: List[str]) -> List[str]:
    """The faults one edit of the opening fixes: the story problems and a first sentence over HOOK_MAX_WORDS."""
    return [p for p in problems or [] if p.startswith(STORY_PROBLEM)
            or (p.startswith(HOOK_PROBLEM) and "-word sentence" in p)]


def story_problems(pkg: ScriptPackage, topic: Optional[Dict[str, Any]] = None) -> List[str]:
    topic = topic if topic is not None else _draft_topic.get()
    if topic is None:
        return []
    return place_problems(pkg.narration_script, topic) + told_story_problems(pkg)


def hook_problems(narration: str) -> List[str]:
    """Why the narration's first sentence is not a hook: it opens on a label rather than the fact or the riddle,
    or it runs past HOOK_MAX_WORDS. A question or a stark claim with an early number passes."""
    first = _first_sentence(narration)
    if not first:
        return []
    words = _spoken_words(first)
    head = " ".join(words[:5])
    early_number = any(re.search(r"\d", w) or w.strip(".,;:!?\"'“”‘’").lower() in _NUMBER_WORDS
                       for w in words[:HOOK_NUMBER_WITHIN])
    problems = []
    if _LABEL_OPENERS.match(first) or (not early_number and (_DEMONSTRATIVE_LABEL.match(first)
                                                             or _DEFINITION_LABEL.match(first))):
        problems.append(f"{HOOK_PROBLEM}the first sentence opens on a label ('{head} ...'), not the mystery. Open on "
                        "the impossible fact or the riddle itself: a stark claim with its number or scale up front, "
                        "or a question the opening picture poses; never 'This X was...', 'This is the...', "
                        "'Meet the...' or 'X is a...'. Keep it a verified fact.")
    if len(words) > HOOK_MAX_WORDS:
        # Worded without "words" or "seconds", which improve.classify files under script.length.
        problems.append(f"{HOOK_PROBLEM}the first sentence is a {len(words)}-word sentence; a hook lands in "
                        f"{HOOK_MAX_WORDS} or fewer, since viewers decide almost at once. Cut or split it, keeping "
                        "the fact or the riddle at its start.")
    return problems


# ---------------------------------------------------------------- engagement bait
# Meta demotes posts that ask for likes, reactions, votes, shares to a number of people, tags or set comment
# replies (about.fb.com/news/2017/12/news-feed-fyi-fighting-engagement-bait-on-facebook/). An ask to share this
# with someone in particular ("Send this to the codebreaker you know") and a genuine question are not bait.
# Each pattern starts a clause (the start, punctuation or an emoji before it, optionally "and"/"please"), so
# narration that reports what people did ("the Romans would vote with stones") is not read as an ask.
_CLAUSE = (r"(?:^|(?<=[.!?\n:;,()☀-➓\U0001F300-\U0001FAFF])|(?<=[☀-➓\U0001F300-\U0001FAFF]️))\s*"
           r"(?:(?i:and|then|so|now|just|please|go\s+ahead\s+and)\s+)*")
_N_PEOPLE = (r"(?:at\s+least\s+)?(?:\d+|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|a\s+hundred"
             r"|all\s+(?:of\s+)?your|every(?:one|body)?|as\s+many)\b")
# "to" after a reply: an ask before a verb ("Comment 1 To Vote", "Comment YES to get part 2"), a description before
# a name, a number or a determiner ("Reply 12 to Nelson", "Type 1 to 2 years", "Reply 12 to the Admiralty";
# rc68 reviews 9-12). One string, shared by every pattern that reads it.
_ASK_TO = (r"to(?:(?=\s+(?i:vote|agree|disagree|choose|pick|confirm|answer|guess|win|join|enter|say|get|unlock"
           r"|see|receive|claim|support|show|continue|keep)\b)"
           r"|(?!\s+(?:(?:the|a|an|his|her|their|its|this|that|these|those|my|our|your)\b|\d|(?-i:[A-Z]))))")
_BAIT_PATTERNS = [
    # react-baiting: "Like this if ...", "Hit like if", "Double tap if", "Smash that like if"
    re.compile(_CLAUSE + r"(?:like|love|heart|double[\s-]?tap|smash|hit)\s+(?:(?:this|it|that)\s+)?"
               r"(?:(?:post|video|reel|photo|button|like|the\s+like(?:\s+button)?)\s+)?if\b", re.I),
    # vote-baiting: "React with ...", "Vote with a ❤️", "Like for X, love for Y"
    re.compile(_CLAUSE + r"(?:react|vote)\s+(?:with|using|by)\b", re.I),
    # an order to vote, at a clause's start: "Vote: orbit or on foot?" (missed: prod, 2026-10-29), "Vote now",
    # "Cast your vote", "Vote for your favourite", "Vote A or B", "Vote below". A vote told ("the Romans would vote
    # with stones", "Would you vote for Rome?") is not at a clause's start, and "Vote counts ..." is a noun.
    re.compile(_CLAUSE + r"(?:cast\s+(?:your|a)\s+votes?\b|vote(?:\s*[:!]|\s+(?:now|below|here|today)\b"
               r"|\s+for\s+(?:your|which|whichever|one|a|an|the)\b|\s+in\s+the\s+(?:comments|replies|poll)\b"
               r"|\s+[A-D]\b(?=\s*(?:or\b|[/,])|\s*[.!?]*\s*$)))", re.I),
    re.compile(_CLAUSE + r"(?:like|love|haha|wow)\s+(?:this\s+)?(?:for|if)\b[^.!?\n]{1,60}?"
               r"\b(?:like|love|haha|wow|sad|angry|care)\s+(?:this\s+)?(?:for|if)\b", re.I),
    # share-baiting: "Share with 5 friends", "Send this to ten people", "Share if you agree"
    re.compile(_CLAUSE + r"(?:share|send|forward|pass)\s+(?:(?:this|it)\s+)?(?:on\s+)?(?:with|to)\s+" + _N_PEOPLE
               + r"(?:\s+(?:friends?|people|others|mates))?", re.I),
    re.compile(_CLAUSE + r"(?:share|repost|reshare)\s+(?:(?:this|it)\s+)?if\b", re.I),
    # tag-baiting: "Tag a friend who ...", "Tag someone", "Tag the explorer you know"
    re.compile(_CLAUSE + r"tag\s+(?:a|an|the|your|some|any|every|\d+|two|three|four|five|friends?|someone|somebody"
               r"|anyone|everyone|people|them|whoever|who)\b", re.I),
    # comment-baiting: "Comment YES", "Type 1 if", "Reply 'AMEN'", "Drop a 🔥 if", "Write 'X' if"
    # an ask after the reply makes it an instruction: an emoji, "in the comments", "below", "and tell us" (rc68 review 5)
    re.compile(_CLAUSE + r"(?:comment|type|reply)\s+[\"“'‘]?(?:(?:yes|no)\s?/\s?(?:yes|no)\b|yes|no|yep|nope|amen|true|false|\d{1,3}(?:\s?[/,]\s?\d{1,3}\b)*)[\"”'’]?"
               r"(?=\s*[\"”'’]?\s*(?:(?i:if|for|when|now|" + _ASK_TO + r")\b|[.!?,]?\s?[↓⇓⇩↡⤵⬇☀-➓\U0001F300-\U0001FAFF]|,?\s?(?i:in\s+the\s+(?:comments|replies)|below|and\s+(?:tell|say|share))\b|,?\s?or\s+(?:[\"“'‘]?\d+\b[\"”'’]?(?:\s+or\s+[\"“'‘]?\d+\b[\"”'’]?)*(?-i:(?!\s+(?!(?i:if|for|when|now|" + _ASK_TO + r"|in\s+the\s+(?:comments|replies)|below|and\s+(?:tell|say|share))\b)[A-Za-z]))|yes\b|no\b)|[.!?]*\s*$))", re.I),
    # an instruction only (followed by if/for/to/when/now, or the end): "Type AB is rarer" is a fact (rc68 review)
    re.compile(_CLAUSE + r"(?i:comment|type|reply)\s+[\"“'‘]?(?![IVXLC]+\b)[A-Z]{2,}\b(?=\s*[\"”'’]?\s*(?:(?i:if|for|when|now|" + _ASK_TO + r")\b|[.!?,]?\s?[↓⇓⇩↡⤵⬇☀-➓\U0001F300-\U0001FAFF]|,?\s?(?i:in\s+the\s+(?:comments|replies)|below|and\s+(?:tell|say|share))\b|,?\s*(?:\bor\b)?\s*[\"“'‘]?[A-Z]{2,}\b|[.!?]*\s*$))"),
    # choices to type: "Comment A or B", "Type A for Rome, B for Greece", "Comment B." (Meta's "comment with
    # specific answers"; rc68 review 2)
    re.compile(_CLAUSE + r"(?i:comment|type|reply)\s+(?i:with\s+)?[\"“'‘]?[A-D](?:\s?[/,]\s?[A-D]\b)*[\"”'’]?(?=\s*(?:,?\s?(?i:or)\s+[\"“'‘]?[A-D]\b(?:[\"”'’]?\s+(?i:or)\s+[\"“'‘]?[A-D]\b)*(?!\s+(?!(?i:if|for|when|now|" + _ASK_TO + r"|in\s+the\s+(?:comments|replies)|below|and\s+(?:tell|say|share))\b)[A-Za-z])|(?i:for)\s+(?!(?i:example|instance)\b)\w|(?i:if|for|when|now|" + _ASK_TO + r")\b|[.!?,]?\s?[↓⇓⇩↡⤵⬇☀-➓\U0001F300-\U0001FAFF]|,?\s?(?i:in\s+the\s+(?:comments|replies)|below|and\s+(?:tell|say|share))\b|[.!?]+(?:\s|$)|$))(?!\.[A-Z])"),
    # a choice mapped to each option: "Comment 1 to Rome, 2 to Greece", "Comment YES to Rome or NO to Greece"
    # (rc68 review 10: "to" + a name alone describes a reply, so the mapping is matched as a whole)
    re.compile(_CLAUSE + r"(?:comment|type|reply)\s+[\"“'‘]?(?:\d{1,3}|yes|no|[A-D])[\"”'’]?\s+(?:for|to)\s+"
               r"[^,.!?\n;/—–&]{1,30}?(?:\s?[,;/—–&]|\s+(?:or|and))\s*(?:(?:or|and)\s+)?(?:(?:comment|type|reply)\s+)?"
               r"[\"“'‘]?(?:\d{1,3}|yes|no|[A-D])[\"”'’]?\s+(?:for|to)\b", re.I),
    re.compile(_CLAUSE + r"(?i:comment|type|reply)\s+(?i:your\s+(?:answer|guess|pick|vote)\s*:\s*)"
               r"(?:[\"“'‘][^\"”'’]{1,20}[\"”'’]|(?![IVXLC]+\b)[A-Z]{2,}\b|[A-D]\b)"),
    re.compile(_CLAUSE + r"(?:comment|type|reply|drop|leave)\s+(?:an?\s+)?(?:emoji|[\"“'‘]?[^\w\s\"“'‘]{1,3}[\"”'’]?)"
               r"\s+if\b", re.I),
    re.compile(_CLAUSE + r"(?:comment|type|reply|write)\s+[\"“'‘][^\"”'’]{1,20}[\"”'’]\s+(?:if|for)\b", re.I),
]


def engagement_bait(text: str) -> List[str]:
    """The engagement-bait asks in `text` (as written, trimmed), or [] when it has none."""
    found = []
    # Whitespace runs collapsed first: the lookaheads stack \s* and backtracked for minutes on a caption with a long
    # blank run ("Comment YES" + 250 spaces took 17 s; rc68 review 6). Matching is the same on normal text.
    text = re.sub(r"\n\s*", "\n", re.sub(r"[^\S\n]+", " ", text or ""))
    for pattern in _BAIT_PATTERNS:
        for m in pattern.finditer(text):
            phrase = " ".join(m.group(0).split()).strip(" ,;:")
            if phrase and phrase not in found:
                found.append(phrase)
    return found


def _bait_fields(pkg: ScriptPackage) -> List[Tuple[str, str]]:
    """(field, text) for every public text a viewer reads: the asks live there. The narration is fact-checked
    speech, not an ask, and checking it risked rejecting a verified script over "Type 1 diabetes..." (rc68 review 4)."""
    closing = re.split(r"(?<=[.!?])\s+", (pkg.narration_script or "").strip())[-1:] or [""]
    fields = [("title", pkg.title), ("narration's closing sentence", closing[0]), ("caption", pkg.caption),
              ("carousel_caption", pkg.carousel_caption or ""), ("first_comment", pkg.first_comment),
              ("share_line", pkg.share_line or ""), ("poll", pkg.poll.question)]
    fields += [(f"story slide {i}", f"{s.line1} {s.line2}. {s.body}") for i, s in enumerate(pkg.story_slides, 1)]
    fields += [(f"carousel slide {i}", f"{c.heading}. {c.body}") for i, c in enumerate(pkg.carousel_slides, 1)]
    return fields


# A ruler for the Story body's word budget: a sentence of the long words (about 7 letters on average) that names,
# places and measurements make; an average sentence fits a word or two more.
_STORY_RULER = "Engineers recorded twelve separate chambers beneath the plateau during careful modern surveys".split()
_STORY_WORDS_FALLBACK = 12   # what the ruler measured (2026-10-28), if the fonts can't be read
_POLL_BODY_SLACK = 0         # the poll frame's body has the same three lines: it draws no option boxes since rc73
                             # (multi_format.POLL_STICKER_ROOM), so its context line is not shortened (2026-10-29)
_story_word_limits: Dict[bool, int] = {}


def story_body_word_limit(asks_poll: bool = False) -> int:
    """The most words the writer is asked for in a Story body, measured on the frame as it is drawn
    (multi_format.story_body_lines): one less than the ruler fits in STORY_BODY_LINES wherever its sentence starts,
    since a writer lands on the limit and names run longer than the ruler's words. The prompt said 18 words, and 18
    words take four lines; fourteen long ones did too (8 rejections, production and development, 2026-10-25/28)."""
    if asks_poll not in _story_word_limits:
        try:
            from .multi_format import STORY_BODY_LINES, story_body_lines
            ruler = _STORY_RULER * 3
            fits = 1
            while fits < 3 * len(_STORY_RULER) and all(
                    story_body_lines(" ".join(ruler[r:r + fits + 1]) + ".") <= STORY_BODY_LINES
                    for r in range(len(_STORY_RULER))):
                fits += 1
            limit = max(6, fits - 1)
        except (OSError, ImportError, ValueError) as e:
            log.debug("Story body not measured (%s); asking for %d words.", e, _STORY_WORDS_FALLBACK)
            limit = _STORY_WORDS_FALLBACK
        _story_word_limits[asks_poll] = limit - (_POLL_BODY_SLACK if asks_poll else 0)
    return _story_word_limits[asks_poll]


STORY_OVERFLOW = re.compile(r"^Story slide (\d)'s body takes \d+ lines on the frame")


def validate_structure(pkg: ScriptPackage) -> List[str]:
    """Returns a list of problems; empty means the package is structurally publishable. The opening's hook rule
    is a new draft's (draft_problems)."""
    problems = []
    for field, text in _bait_fields(pkg):
        bait = engagement_bait(text)
        if bait:
            problems.append(f"Engagement bait in the {field} ({'; '.join(repr(b) for b in bait)}): Meta demotes asks for "
                            "likes, reactions, votes, shares to a number of people, tags or set replies; ask a "
                            "genuine question instead.")
    words = pkg.narration_script.split()
    # Sanity bounds; the precise length is the measured spoken one (spoken_length_problems), so a draft
    # shortened to fit a slow voice is not then rejected for having too few words.
    if not WORD_COUNT_FLOOR <= len(words) <= WORD_COUNT_CEILING:
        problems.append(f"Narration is {len(words)} words; must be {WORD_COUNT_FLOOR}-{WORD_COUNT_CEILING}.")
    if words:
        last = words[-1].strip(".,!?:;\"'”’").lower()
        if last in BANNED_ENDINGS:
            problems.append(f"Narration ends on trailing word '{last}'.")
        if not pkg.narration_script.rstrip().endswith((".", "!", "?")):
            problems.append("Narration does not end with terminal punctuation.")
    if not pkg.narration_script.strip().endswith(pkg.closing_sentence.strip()):
        problems.append("closing_sentence is not the final sentence of the narration.")
    if len(EMOJI_RE.findall(pkg.title)) > 1:
        problems.append("Title uses more than one emoji.")
    if len(pkg.title) > 60:
        problems.append(f"Title is {len(pkg.title)} characters; max 60.")
    # The caption as written, without the credit lines the pipeline appends (a regenerated run's draft carries the
    # last render's): their 📷 and 🎬 were counted as its emoji, and the copy edit then stripped them (production,
    # Derinkuyu 2026-10-29). add_provenance_attributions keeps the credited caption within 2,200.
    from .credits import split_attribution
    written_caption = split_attribution(pkg.caption)[0]
    if len(written_caption) > 2200:
        problems.append(f"Caption is {len(written_caption)} characters; Instagram max is 2,200.")
    if not 3 <= len(pkg.hashtags) <= 5 or not all(h.startswith("#") and " " not in h for h in pkg.hashtags):
        problems.append("Hashtags must be 3-5 single-word tags starting with '#' (Instagram allows at most 5).")
    if len(re.findall(r"(?<!\w)#\w", pkg.caption + " " + pkg.first_comment)) > 5:
        problems.append("The caption and first comment carry more than 5 hashtags; Instagram allows at most 5 per post.")
    first_line = next((ln for ln in pkg.caption.splitlines() if ln.strip()), "")
    if len(first_line) > 125:
        problems.append(f"Caption's first line is {len(first_line)} characters; keep it within the 125 shown before 'more'.")
    emoji = len(EMOJI_RE.findall(written_caption))
    if emoji > 4:
        problems.append(f"Caption uses {emoji} emoji; 1-3 relevant ones read human, more reads like spam.")
    if pkg.share_line:
        problems.extend(f"share_line: {p}" for p in share_line_problems(pkg.share_line))
        if share_repeats_narration(pkg.share_line, pkg.narration_script):
            problems.append("share_line: the narration's last sentence already makes this ask; the end card shows it "
                            "right after, so word one of them differently (or end the narration on the story).")
    if len(pkg.story_slides) != 3:
        problems.append(f"Expected 3 story slides, got {len(pkg.story_slides)}.")
    else:
        # Each body fits three lines at the Story's fixed text size (it was shrunk to an unreadable 30 px
        # instead: operator, 2026-10-25). Measured as the renderer draws it.
        from .multi_format import STORY_BODY_LINES, story_body_lines
        for i, s in enumerate(pkg.story_slides, 1):
            lines = story_body_lines(s.body, asks_poll=i == 3)
            if lines > STORY_BODY_LINES:
                words = len(s.body.split())
                fit = max(6, min(story_body_word_limit(asks_poll=i == 3), words * STORY_BODY_LINES // lines - 1))
                problems.append(f"Story slide {i}'s body takes {lines} lines on the frame (at most {STORY_BODY_LINES}); "
                                f"rewrite it in about {fit} words ({words} now), keeping one complete sentence.")
    if len(pkg.carousel_slides) != 7:
        problems.append(f"Expected 7 carousel slides, got {len(pkg.carousel_slides)}.")
    problems.extend(poll_problems(pkg.poll.question, pkg.poll.options))
    public_text = " ".join([
        pkg.title, pkg.narration_script, pkg.caption, pkg.carousel_caption or "", pkg.first_comment, pkg.share_line or "",
        *(s.line1 + " " + s.line2 + " " + s.body for s in pkg.story_slides),
        *(c.heading + " " + c.body for c in pkg.carousel_slides),
    ])
    for trope in find_banned_tropes(public_text):
        problems.append(f"Contains pseudo-archaeology trope '{trope}'.")
    ai_isms = [p for p in AI_PHRASES if re.search(r"\b" + re.escape(p) + r"\b", public_text, re.IGNORECASE)]
    if ai_isms:
        problems.append(f"Uses machine-sounding phrasing ({', '.join(ai_isms)}); rewrite in plain documentary language.")
    if pkg.timeline is not None:
        if not 2 <= len(pkg.timeline) <= 4:
            problems.append("timeline needs 2-4 events (or null).")
        if any(len(e.label) > 40 for e in pkg.timeline):
            problems.append("timeline labels must be at most 40 characters.")
        if [e.year for e in pkg.timeline] != sorted(e.year for e in pkg.timeline):
            problems.append("timeline events must be in chronological order.")
    if pkg.scale_fact is not None and (pkg.scale_fact.value <= 0 or len(pkg.scale_fact.subject) > 28):
        problems.append("scale_fact needs a positive value and a subject of at most 28 characters.")
    for card in pkg.stat_cards or []:
        if len(card.label) > 40 or len(card.value) > 22:
            problems.append("stat_cards need a value of at most 22 and a label of at most 40 characters.")
    if pkg.stat_cards is not None and not 1 <= len(pkg.stat_cards) <= 2:
        problems.append("stat_cards needs 1-2 entries (or null).")
    if pkg.cross_section is not None:
        cs = pkg.cross_section
        depths = [lv.depth_ft for lv in cs.levels]
        if not 2 <= len(cs.levels) <= 5 or depths != sorted(depths) or any(d < 0 or d > cs.total_depth_ft for d in depths):
            problems.append("cross_section needs 2-5 levels ordered top to bottom, within the total depth.")
    if pkg.route is not None and not 2 <= len(pkg.route) <= 5:
        problems.append("route needs 2-5 stops (or null).")
    if pkg.photo_labels is not None and (not 2 <= len(pkg.photo_labels) <= 4 or any(len(x) > 28 for x in pkg.photo_labels)):
        problems.append("photo_labels needs 2-4 short labels (at most 28 characters each).")
    for term in pkg.emphasis_words or []:
        if term.lower() not in pkg.narration_script.lower():
            problems.append(f"emphasis word '{term}' does not appear in the narration.")
    if pkg.emphasis_words is not None and not 1 <= len(pkg.emphasis_words) <= 3:
        problems.append("emphasis_words needs 1-3 entries.")
    repeated = repeated_phrases(pkg.narration_script)
    if repeated:
        # Production 2026-10-25: two sentences in a row opened "By one estimate," (both facts disputed); the
        # viewer hears a template, and the video check flagged it after the voiceover was paid for.
        problems.append(REPEAT_PROBLEM + ", ".join(f"'{r}'" for r in repeated)
                        + "; say it once (one hedge can cover two related figures) or reword the second, keeping every "
                          "disputed fact hedged.")
    if "📍" in pkg.caption:
        problems.append("Caption uses the '📍 location' template line; work the place into the prose instead.")
    first_line = next((ln for ln in pkg.caption.splitlines() if ln.strip()), "")
    if _similar(first_line, pkg.hook_sentence) >= 0.8:
        problems.append("Caption's first line repeats the narration hook; write a different opening line.")
    if pkg.carousel_caption is not None:
        if _similar(pkg.carousel_caption, pkg.caption) >= 0.6:
            problems.append("carousel_caption is too close to the Reel caption; write it fresh.")
        if len(pkg.carousel_caption) > 2200:
            problems.append("carousel_caption exceeds 2,200 characters.")
    generic = [h for h in pkg.hashtags if h.lower() in GENERIC_HASHTAGS]
    if generic:
        problems.append(f"Generic hashtags {generic}; use specific subject tags.")
    return problems


GRAPHICS_FIELDS = ("timeline", "scale_fact", "stat_cards", "cross_section", "route", "photo_labels", "emphasis_words")


class _Shortened(BaseModel):
    texts: List[str]


SHORTEN_SYSTEM = """You shorten on-screen labels of a history documentary's graphics to fit their character limits. Keep each label's meaning and facts exactly: drop words, use a shorter phrasing, never add a fact, a number or a name, never change one. Return the labels in the same order."""
STORY_SHORTEN_SYSTEM = """You shorten the body lines of a history video's Story frames so each fits three lines on screen. Keep each body's meaning and facts exactly: drop words or use a shorter phrasing; never add a fact, a number or a name, never change one, and keep every hedge ("about", "up to", "estimated"). Each result is one complete sentence of at most the words given; the poll frame's body (slide 3) gives context and asks no question. Return the bodies in the same order."""


def shorten_graphic_text(pkg: ScriptPackage) -> ScriptPackage:
    """Graphic labels over their limits get one light "shorten only" rewrite before prune_graphics
    would drop them. The result is fact-checked later like the rest of the on-screen copy; a label
    that still doesn't fit is left for pruning."""
    items = [("stat_cards", i, "label", 40, c.label) for i, c in enumerate(pkg.stat_cards or []) if len(c.label) > 40]
    items += [("timeline", i, "label", 40, e.label) for i, e in enumerate(pkg.timeline or []) if len(e.label) > 40]
    if pkg.scale_fact is not None and len(pkg.scale_fact.subject) > 28:
        items.append(("scale_fact", None, "subject", 28, pkg.scale_fact.subject))
    if not items or not free_llm.ready():
        return pkg
    prompt = "\n".join(f"{k}. (at most {limit} characters) {text}" for k, (*_, limit, text) in enumerate(items, 1))
    try:
        out = free_llm.call(SHORTEN_SYSTEM, prompt, _Shortened, role="writer", task_class="light", effort="low", max_tokens=800)
    except (LLMError, OSError) as e:
        log.info("Could not shorten graphic labels (%s); over-long ones will be dropped.", e)
        return pkg
    if len(out.texts) != len(items):
        return pkg
    for (field, i, attr, limit, old), new in zip(items, out.texts):
        new = new.strip()
        if not new or len(new) > limit:
            continue
        if field == "scale_fact":
            pkg = pkg.model_copy(update={field: pkg.scale_fact.model_copy(update={attr: new})})
        else:
            entries = list(getattr(pkg, field))
            entries[i] = entries[i].model_copy(update={attr: new})
            pkg = pkg.model_copy(update={field: entries})
        log.info("Shortened %s label: %r -> %r", field, old, new)
        improve.note(f"graphics.{field}", f"label over {limit} characters, shortened", "shorten")
    return pkg


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”’)]*\s+")

# Direction cues (operator, 2026-10-28): "Whenever we're using the pointer emoji we need to be sure we are
# pointing in the right direction". The posted Facebook carousel's 👉 pointed right while its caption sat above
# and the photos below; Instagram's carousel shows the photos above the caption. Each platform places the
# caption, the comments and the media its own way, and may change that, so no copy carries a cue that depends
# on where things are: no pointing hand or arrow, no "swipe right/left/up/down", no "below"/"above" for the
# comments, caption or photos. normalize_copy removes them deterministically; the writer is told as well.
_DIRECTION_MARKS = re.compile(
    "(?:[←-⇿➔-➿⤀-⥿⬀-⬍⭠-⮹☚-☟▲-►▼-◄"
    "⏩-⏬\U0001F446-\U0001F449\U0001F53C\U0001F53D\U0001F800-\U0001F8FF]"
    "[︎️\U0001F3FB-\U0001F3FF]*)+"
    r"|(?<![\w-])(?:-{1,2}>|<-{1,2}|=>)(?![\w>])")
# What a "below"/"above" may point at; a fact ("30 m below the surface", "the carvings above the door") is
# never one of these or is followed by what it is below or above, and is left alone.
# Only nouns that name a part of the post: "the Grand Gallery above it" and "the post below it marks the border"
# are facts (rc68 review, 2026-10-28), so gallery, post, video and pictures are not here.
_UI_NOUN = r"(?:photos|slides|carousel)"  # the comment-type nouns have their own pattern below (rc68 review 3)
_NOT_A_PLACE = (r"(?!\s+(?:the|a|an|its|his|her|their|our|this|that|these|those|sea|ground|water|surface|level|it|them"
                r"|him|which|who|whom|here|there|where|and|or|\d))")
# After a call to act ("comment below which...", "tell us below who...") only a determiner or a place word means a
# place: "comment below the line" is rare enough; "below which theory you back" is the prompt (rc68 review 2)
_VERB_NOT_A_PLACE = r"(?!\s+(?:the|its|his|her|their|our|sea|ground|water|surface|level|\d))"
# After a comment-type noun: also a pronoun, determiner or place preposition means the words go on about a place or
# the noun itself ("the comments below it", "a discussion below in the crypt"; rc68 review 3)
_NOUN_NOT_A_PLACE = (r"(?!\s+(?:the|its|his|her|their|our|sea|ground|water|surface|level|it|them|him|this|that|these"
                     r"|those|a|an|in|on|at|here|there|and|or|\d)\b)")
_DIRECTION_PHRASES = [
    # "swipe right", "swipe to the left", "swipe up through": the act, without the direction
    (re.compile(r"\b(swip(?:e|es|ed|ing))\s+(?:(?:to|towards?)\s+the\s+)?(?:right|left|up|down|forward|back(?:wards?)?"
                r"|across|sideways|over)\b(?:\s+through\b)?", re.I), lambda m: m.group(1) + " through"),
    (re.compile(r"\b(scroll(?:s|ed|ing)?)\s+(?:up|down)\b", re.I), lambda m: m.group(1)),
    # "in the comments below", "the photos above" -> "in the comments", "the photos"
    (re.compile(r"\b((?:the\s+)?(?:comments?|comment\s+section|caption|thread|discussion|replies|link\s+in\s+bio))"
                r"\s+(?:(?:down|up)\s+)?(?:below|above|underneath)\b" + _NOUN_NOT_A_PLACE, re.I), lambda m: m.group(1)),
    (re.compile(r"\b((?:the\s+)?" + _UI_NOUN + r")\s+(?:(?:down|up)\s+)?(?:below|above|underneath)\b" + _NOT_A_PLACE, re.I),
     lambda m: m.group(1)),
    # "comment below", "tell us below", "drop your guess below"
    (re.compile(r"\b(comment|reply|answer|vote|weigh\s+in|sound\s+off|chime\s+in|tell\s+us|let\s+us\s+know"
                r"|share\s+(?:it|yours|your\s+\w+)|drop\s+(?:it|yours|a\s+comment|your\s+\w+))\s+(?:(?:down|up)\s+)?"
                r"(?:below|above)\b" + _VERB_NOT_A_PLACE, re.I), lambda m: m.group(1)),
    (re.compile(r"\b(?:see|read|check|look)\s+(?:(?:down|up)\s+)?(?:below|above)\b" + _NOT_A_PLACE, re.I),
     lambda m: _cased(m.group(0), "take a look")),
]
# Facebook shows a multi-photo post as a grid, not a swipe: its caption says "explore" or "see", never "swipe".
_FB_SWIPE = [
    # "Swipe to see how ..." / "Swipe through to trace ..." -> "See how ..." / "Trace ..."
    (re.compile(r"\b(?:keep\s+swiping|swipe)\b(?:\s+(?:through|along|across|on))?\s+(?:to\s+(\w+)|and\s+(see|discover"
                r"|find|uncover|learn|explore|meet|read|trace|follow|watch)\b)", re.I),
     lambda m: _cased(m.group(0), m.group(1) or m.group(2))),
    # "Swipe through the evidence" -> "Explore the evidence"
    (re.compile(r"\b(?:keep\s+swiping|swipe)\s+(?:through|along|across)\s+(?=(?:the|these|this|those|our|all|each|every"
                r"|its|his|her|their|\d)\b)", re.I), lambda m: _cased(m.group(0), "explore ")),
    # any other: "Swipe." -> "See the photos.", "start swiping" -> "start looking through the photos"
    (re.compile(r"\b(keep\s+)?swip(e|es|ed|ing)\b(?:\s+(?:through|along|across|on)\b)?", re.I),
     lambda m: _cased(m.group(0), (m.group(1) or "") + {"e": "see", "es": "sees", "ed": "looked through",
                                                         "ing": "looking through"}[m.group(2).lower()] + " the photos")),
]


def _cased(source: str, text: str) -> str:
    """`text` with its first letter in the case of `source`'s (a sentence start stays capitalised)."""
    return (text[:1].upper() if source[:1].isupper() else text[:1].lower()) + text[1:]


def _tidy(text: str) -> str:
    """Spacing after words or marks were removed: one space between words, none before punctuation, no line
    left empty by a removed mark, at most one blank line in a row."""
    lines = []
    for line in text.split("\n"):
        tidy = re.sub(r"[ \t]{2,}", " ", line)
        tidy = re.sub(r"[ \t]+([.,!?;:)])", r"\1", tidy).strip()
        if tidy or not line.strip():
            lines.append(tidy)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def _mark_gap(text: str, m) -> str:
    """What a removed mark leaves: "to" between two numbers ("1902 → 1910"), a full stop where it separated two
    sentences ("the gears ➡️ What ..."), else a space."""
    before, after = text[:m.start()], text[m.end():]
    if re.search(r"\d\s*$", before) and re.match(r"\s*\d", after):
        return " to "
    if re.search(r"\w\s*$", before) and re.match(r"\s*[A-Z]", after):
        return ". "
    return " "


def neutral_cues(text: str) -> str:
    """`text` without a direction-dependent cue (pointing hands, arrows, "swipe right", "comment below"); the
    same text when it had none."""
    if not text:
        return text
    out = _DIRECTION_MARKS.sub(lambda m: _mark_gap(text, m), text)
    for pattern, rep in _DIRECTION_PHRASES:
        out = pattern.sub(rep, out)
    return text if out == text else _tidy(out)


def facebook_photo_caption(text: str) -> str:
    """The carousel caption as Facebook's multi-photo post carries it: no direction cue, and no "swipe"
    (Facebook shows the set as a grid; operator, 2026-10-28)."""
    out = neutral_cues(text or "")
    before = out
    for pattern, rep in _FB_SWIPE:
        out = pattern.sub(rep, out)
    return out if out == before else _tidy(out)


CAPTION_FIRST_LINE_MAX = 125   # what Instagram shows before "more"
# A caption sentence ends after . ! or ?, any closing quote or bracket, and any emoji that decorates it ("... cracks. 🏛️");
# or, with no full stop, after an emoji followed by a capital ("Rome's concrete heals itself 🏛️ Lime clasts ...").
_EMOJI_RUN = "(?:[\U0001F300-\U0001FAFF☀-➿][︎️‍\U0001F3FB-\U0001F3FF]*)"
_CAPTION_SENTENCE_END = re.compile(
    r"(?:(?<=[.!?])[\"'”’)]*(?:\s*" + _EMOJI_RUN + r")*|(?<=\S)\s*" + _EMOJI_RUN + r"+(?=\s+[\"“‘(]?[A-Z]))(?=\s+\S)")
_TRAILING_HASHTAGS = re.compile(r"(?:\s+#\w+)+\s*$")


def split_caption_first_line(caption: str) -> str:
    """The caption with a first line over CAPTION_FIRST_LINE_MAX characters broken after as many whole sentences as
    fit within it (at least one; the hook shown before "more" is not cut to a stub like "One renovation."): same
    words, the rest on the next line, and hashtags that ended that line on their own last line. Unchanged when the
    line fits, holds one sentence, or its first sentence alone is over the limit (that needs new wording: the copy
    edit's). Production, Roman concrete 2026-10-29: a 615-character first line cost a whole writer attempt."""
    lines = (caption or "").split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None or len(lines[first]) <= CAPTION_FIRST_LINE_MAX:
        return caption
    line = lines[first].strip()
    end = None
    for m in _CAPTION_SENTENCE_END.finditer(line):   # greedy: the last sentence end that still fits
        if len(line[:m.end()].strip()) > CAPTION_FIRST_LINE_MAX:
            break
        end = m.end()
    if end is None:
        return caption
    head, rest = line[:end].strip(), line[end:].strip()
    tags = _TRAILING_HASHTAGS.search(rest)
    tail = [rest[:tags.start()].strip(), tags.group(0).strip()] if tags and tags.start() > 0 else [rest]
    lines[first:first + 1] = [head] + tail
    return "\n".join(lines)


# ---------------------------------------------------------------- the copy edit (production, Roman concrete 2026-10-29)
# A fault confined to the copy around the narration (caption, poll, slides, title, hashtags, first comment, share
# line) cost a whole rewrite, which re-rolled the narration and its length: three attempts were spent on a length
# miss, a 615-character caption line and a yes/no poll with two choices. Such faults are now fixed by one short
# edit of those fields only; the narration, its structure and the graphics stay byte for byte.
COPY_FIELDS = ("title", "caption", "carousel_caption", "first_comment", "share_line", "hashtags", "poll",
               "story_slides", "carousel_slides", "thumbnail")
_COPY_PREFIXES = (
    ("Title ", ("title",)), ("Caption's first line repeats", ("caption",)), ("Caption", ("caption",)),
    # The captions end on the hashtags line: a tag changed in the list alone would stay in the posted caption.
    ("Hashtags must", ("hashtags", "caption", "carousel_caption")),
    ("Generic hashtags", ("hashtags", "caption", "carousel_caption")),
    ("The caption and first comment carry", ("caption", "first_comment", "hashtags", "carousel_caption")),
    ("share_line:", ("share_line",)), ("Expected 3 story slides", ("story_slides",)), ("Story slide ", ("story_slides",)),
    ("Expected 7 carousel slides", ("carousel_slides",)), ("carousel_caption", ("carousel_caption",)),
    ("The poll", ("poll",)), ("Poll options", ("poll",)),
    (HEADLINE_PROBLEM + "title", ("title",)), (HEADLINE_PROBLEM + "thumbnail", ("thumbnail",)),
    (HEADLINE_PROBLEM + "caption", ("caption",)),
)
_BAIT_FIELD = re.compile(r"^Engagement bait in the (title|caption|carousel_caption|first_comment|share_line|poll"
                         r"|story slide \d+|carousel slide \d+) \(")


def _copy_texts(pkg: ScriptPackage) -> Dict[str, str]:
    """Each copy field's public text, for finding which one holds a phrase."""
    return {"title": pkg.title, "caption": pkg.caption, "carousel_caption": pkg.carousel_caption or "",
            "first_comment": pkg.first_comment, "share_line": pkg.share_line or "",
            "story_slides": " ".join(f"{s.line1} {s.line2} {s.body}" for s in pkg.story_slides),
            "carousel_slides": " ".join(f"{c.heading} {c.body}" for c in pkg.carousel_slides)}


def copy_problem_fields(problem: str, pkg: ScriptPackage) -> Optional[Tuple[str, ...]]:
    """The copy fields a structural problem lies in, or None when it touches the narration (its words, length,
    ending, repeats, hook), the graphics, or is not recognised: only a fault confined to the copy is the copy edit's."""
    m = _BAIT_FIELD.match(problem)
    if m:
        field = m.group(1)
        return ("story_slides",) if field.startswith("story") else ("carousel_slides",) if field.startswith("carousel slide") \
            else (field,)
    for prefix, fields in _COPY_PREFIXES:
        if problem.startswith(prefix):
            return fields
    trope = re.match(r"^Contains pseudo-archaeology trope '(.+)'\.$", problem)
    phrasing = re.match(r"^Uses machine-sounding phrasing \((.+?)\); rewrite", problem)
    phrases = ([trope.group(1)] if trope else
               [p.strip() for p in phrasing.group(1).split(", ") if p.strip()] if phrasing else [])
    if not phrases:
        return None
    hit = lambda text, p: re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)  # noqa: E731  (as the detector matches)
    if any(hit(pkg.narration_script, p) for p in phrases):
        return None
    fields = tuple(f for f, text in _copy_texts(pkg).items() if any(hit(text, p) for p in phrases))
    return fields or None


def copy_only(problems: List[str], pkg: ScriptPackage) -> bool:
    return bool(problems) and all(copy_problem_fields(p, pkg) is not None for p in problems)


def _field_rules(fields: Tuple[str, ...]) -> str:
    """The writer's own rules for these fields (USER_PROMPT_TEMPLATE's), with the rules every text follows."""
    rules = {}
    for line in USER_PROMPT_TEMPLATE.split("\n- ")[1:]:
        name = line.split(":", 1)[0].strip()
        rules[name] = "- " + line.strip()
    general = [r for n, r in rules.items() if n.startswith("No ")]
    chosen = [rules[f] for f in fields if f in rules]
    return "\n".join(chosen + general).format(story_words=story_body_word_limit(),
                                              poll_words=story_body_word_limit(asks_poll=True))


COPY_EDIT_SYSTEM = """You fix the copy around a finished, fact-checked history video: only the fields you are given,
only so the listed problems are gone. Keep each field's facts, names, numbers and hedges; change as few words as
the problem needs (move, cut or reword); never add a fact that is not in the verified facts, and never a figure
without its hedge. Each field you return replaces the old one whole and must follow its rules and limits. Return
only the fields you were given; the narration is shown for context and is not yours to change. A title or caption
leads with the story the narration tells (its discovery, figures and place), never one it does not. A headline
(title, cover, the caption's first line) states only verified figures: an undisputed one as it is, a disputed one as a
floor at or below its range's minimum ("1,000+"), none that only one estimate gives (reword it without the figure)."""


class _CopyEdit(BaseModel):
    title: Optional[str] = None
    caption: Optional[str] = None
    carousel_caption: Optional[str] = None
    first_comment: Optional[str] = None
    share_line: Optional[str] = None
    hashtags: Optional[List[str]] = None
    poll: Optional[Poll] = None
    story_slides: Optional[List[StorySlide]] = None
    carousel_slides: Optional[List[CarouselSlide]] = None
    thumbnail: Optional[ThumbnailCopy] = None   # the cover's words, for a headline figure the sheet does not support


def normalize_copy(pkg: ScriptPackage) -> ScriptPackage:
    """Bookkeeping a draft gets wrong without being wrong, fixed without a rewrite (each was a full
    model call; dev, 2026-10-23): closing_sentence is set to the narration's actual last sentence, and a
    caption first line over 125 characters that holds several sentences is broken after the whole
    sentences that fit (split_caption_first_line: same words, new line). A first sentence over the limit gets the copy edit. A
    yes/no poll question over two choices is asked as the either/or they answer (repair_poll). No public
    copy keeps a direction-dependent cue (neutral_cues; operator, 2026-10-28)."""
    update = {}
    if pkg.share_line and engagement_bait(pkg.share_line):
        # The share line closes both captions and the end card: a baiting one gives way to the plain line
        # (share_prompt) rather than costing a rewrite; the review card says the fallback was used.
        log.info("Share line dropped as engagement bait (%s): %r", "; ".join(engagement_bait(pkg.share_line)), pkg.share_line)
        improve.note("script.share_bait", f"engagement bait in the share line: {pkg.share_line[:80]}", "repair")
        update["share_line"] = None
    sentences = [s for s in _SENTENCE_END.split(pkg.narration_script.strip()) if s.strip()]
    if sentences and not pkg.narration_script.strip().endswith(pkg.closing_sentence.strip()):
        update["closing_sentence"] = sentences[-1].strip()
    for key in ("title", "carousel_caption", "first_comment", "share_line"):
        value = update.get(key, getattr(pkg, key))
        if value and neutral_cues(value) != value:
            update[key] = neutral_cues(value)
    for key, fields in (("story_slides", ("kicker", "line1", "line2", "body")), ("carousel_slides", ("heading", "body")),
                        ("thumbnail", ("eyebrow", "line1", "line2", "tagline"))):
        items = getattr(pkg, key)
        many = isinstance(items, list)
        fixed = [it.model_copy(update={f: neutral_cues(getattr(it, f)) for f in fields
                                       if neutral_cues(getattr(it, f)) != getattr(it, f)}) for it in (items if many else [items])]
        if any(a != b for a, b in zip(fixed, items if many else [items])):
            update[key] = fixed if many else fixed[0]
    caption = neutral_cues(pkg.caption)
    if caption != pkg.caption:
        update["caption"] = caption
    cued = [k for k in update if k != "closing_sentence"]
    if cued:
        log.info("Direction cues (pointing hands, arrows, swipe right, below/above) removed from: %s.", ", ".join(cued))
    split = split_caption_first_line(caption)
    if split != caption:
        log.info("Caption's first line (%d characters) broken after the sentences that fit.",
                 len(next((ln for ln in caption.splitlines() if ln.strip()), "")))
        update["caption"] = split
    # A yes/no poll question over two choices is asked as the either/or they answer (repair_poll), not
    # sent back for a whole rewrite (Hal Saflieni, 2026-10-27).
    reworded = repair_poll(pkg.poll.question, pkg.poll.options, pkg.narration_script)
    if reworded:
        log.info("Poll question reworded to the choice its options offer: %r -> %r", pkg.poll.question, reworded)
        improve.note("script.poll_repaired", f"yes/no question over two choices, reworded to '{reworded}'", "repair")
        update["poll"] = pkg.poll.model_copy(update={"question": reworded})
    return pkg.model_copy(update=update) if update else pkg


def prune_graphics(pkg: ScriptPackage) -> ScriptPackage:
    """Drops each planned graphic that fails its structural checks (logged), keeping the script.
    Graphics are optional; one bad field shouldn't cost a whole script attempt. Over-long labels are
    first shortened; of the stat cards, only the ones that still don't fit are dropped."""
    pkg = normalize_copy(shorten_graphic_text(pkg))
    if pkg.stat_cards:
        fitting = [c for c in pkg.stat_cards if len(c.label) <= 40 and len(c.value) <= 22]
        if fitting and len(fitting) < len(pkg.stat_cards):
            log.warning("Dropping %d stat card(s) over the length limits.", len(pkg.stat_cards) - len(fitting))
            improve.note("graphics.stat_cards", "stat card over the length limits", "prune")
            pkg = pkg.model_copy(update={"stat_cards": fitting})
    for field in GRAPHICS_FIELDS:
        if getattr(pkg, field) is None:
            continue
        alone = pkg.model_copy(update={f: (getattr(pkg, f) if f == field else None) for f in GRAPHICS_FIELDS})
        base = pkg.model_copy(update={f: None for f in GRAPHICS_FIELDS})
        extra = [p for p in validate_structure(alone) if p not in validate_structure(base)]
        if extra:
            log.warning("Dropping planned graphic '%s': %s", field, " ".join(extra))
            improve.note(f"graphics.{field}", " ".join(extra), "prune")
            pkg = pkg.model_copy(update={field: None})
    return pkg


def draft_problems(pkg: ScriptPackage) -> List[str]:
    """A writer's draft's structural and spoken-length problems, reported together: measured only once the
    structure passed, a draft sent back for its structure came back the wrong length, and that cost the next
    attempt too (production, Hal Saflieni 2026-10-27)."""
    return (validate_structure(pkg) + spoken_length_problems(pkg) + hook_problems(pkg.narration_script)
            + story_problems(pkg) + headline_problems(pkg))


def blocking_problems(problems: List[str]) -> List[str]:
    """The problems that send a draft back: all of them, less the hook rule's unless HOOK_FORCES_REWRITE (those
    ride along with a rewrite that happens anyway)."""
    kept = (STORY_PROBLEM,) if HOOK_FORCES_REWRITE else (HOOK_PROBLEM, STORY_PROBLEM)
    return [p for p in problems if not p.startswith(kept)]


def _note_kept_hook(problems: List[str]) -> None:
    """A draft kept with a weak opening: said in the log and the findings ledger (script.hook)."""
    weak = [p for p in problems if p.startswith((HOOK_PROBLEM, STORY_PROBLEM))]
    if weak:
        log.info("Draft kept with an opening the hook rule would rewrite: %s", " ".join(weak)[:300])
        improve.note_problems(weak, "structure")


class ScriptEngine:
    def __init__(self, fact_checker: Optional[ClaimFactChecker] = None):
        self.fact_checker = fact_checker or ClaimFactChecker()
        self._research_cache: Dict[str, Tuple[str, list]] = {}  # topic_id -> (notes, retrieved sources)
        self._sheets: Dict[str, Optional[Dict[str, Any]]] = {}    # topic_id -> verified fact sheet
        self._starved: Dict[str, fact_sheet.FactStarvedTopicError] = {}  # topics research couldn't sustain
        self.progress: Optional[Callable[[str], None]] = None  # the run's progress card, told each draft

    def _report(self, text: str) -> None:
        if self.progress:
            try:
                self.progress(text)
            except Exception as e:  # the card is a courtesy: it never stops the writing
                log.debug("Could not report script progress: %s", e)

    @llm_task("script_writing")
    def generate_script(self, topic: Dict[str, Any], max_attempts: int = 3, revision_notes: str = "",
                        previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The run's script (see _generate_script), with the length window kept over its drafts: started afresh
        by a run's first call, carried on by its revisions (`previous`)."""
        key = (topic.get("id"), getattr(self, "length_run", None))
        if previous is None or _length_history["key"] != key:
            begin_length_history(key)
        _length_history["on"], _length_history["thread"] = True, threading.get_ident()
        told = _draft_topic.set(topic)   # the story checks (place, told story) read the topic being written
        figures = _draft_sheet.set(lambda: self._sheets.get(topic.get("id")))   # the headline figures, its sheet
        # A regenerated run's payload carries the last render's credit lines: the writer, the edits and the checks
        # see the copy without them, and they are appended again to what comes back (production, Derinkuyu
        # 2026-10-29: their 📷/🎬 were counted as caption emoji, the copy edit stripped them, and the photo titles
        # and Commons ids were then checked as figures).
        from .credits import hold_attribution, restore_attribution
        held: Dict[str, List[str]] = {}
        if previous is not None:
            previous, held = hold_attribution(previous)
        try:
            return restore_attribution(self._generate_script(topic, max_attempts, revision_notes, previous), held)
        finally:
            _length_history["on"] = False
            _draft_topic.reset(told)
            _draft_sheet.reset(figures)

    def _generate_script(self, topic: Dict[str, Any], max_attempts: int = 3, revision_notes: str = "",
                         previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        revision_notes / previous: fact-checker findings on the previous draft. The previous
        draft is edited (not rewritten) so corrections don't introduce new, unchecked claims.
        """
        last_problems: List[str] = []
        writer_error = ""
        if _ready():
            sheet = self._sheet(topic) if previous is not None and previous.get("fact_sheet") else None
            if revision_notes and previous and sheet:
                try:
                    pkg = self._revise_surgically(previous, revision_notes, sheet)
                except LLMError as e:
                    log.warning("Surgical revision failed (%s); rewriting the draft.", e)
                    pkg = None
                if pkg is not None:
                    pkg = prune_graphics(pkg)
                    # A Story body that runs long, a copy fault or a repeat: edited, not the whole draft rewritten,
                    # which must pass both fact checks again (production 2026-10-28: "Story slide 3's body takes 4
                    # lines"; 2026-10-29: a caption line and a poll).
                    pkg, problems = self._narrow_edits(pkg, draft_problems(pkg), sheet)
                    problems = blocking_problems(problems)
                    if (problems and any("when spoken" in p for p in problems)
                            and all("when spoken" in p or p.startswith(REPEAT_PROBLEM) for p in problems)):
                        # The revision changed the length (production, Derinkuyu 2026-10-29: 112 words, 53.8 s):
                        # edited to the window, not the whole fact-checked draft rewritten.
                        fitted = self._fit_length_by_editing(pkg, problems, topic, "")
                        if fitted is not None:
                            return self._finalize(fitted, topic, origin=writer_origin(), fact_sheet_used=True)
                    # (A lone repeated phrase, a hedge the revision added twice (development 2026-10-28), was
                    # reworded by _narrow_edits above.)
                    if not problems:
                        _note_kept_hook(draft_problems(pkg))
                        log.info("Revised surgically: only the rejected sentences changed.")
                        return self._finalize(pkg, topic, origin=writer_origin(), fact_sheet_used=True)
                    log.warning("Surgical revision failed structural checks (%s); rewriting.", problems)
            base_feedback = ""
            if revision_notes and previous:
                draft = {k: previous.get(k) for k in ScriptPackage.model_fields}
                base_feedback = (
                    "\n\n<previous_draft>\n" + json.dumps(draft, ensure_ascii=False, indent=1) + "\n</previous_draft>\n"
                    "This draft failed fact-checking. Revise it, changing only what the findings below require. For "
                    "each finding: if disputed, hedge it with the range or wording the finding gives; if unsupported "
                    "or false, delete that sentence or replace it with a fact stated in the research notes. Do NOT "
                    "write any new sentence whose facts are not in the research notes, not even small details "
                    "(names, what people did, what is unknown): every new sentence is fact-checked again and an "
                    "unsupported one fails the script. If the narration gets too short, extend it only with facts "
                    "from the research notes. Apply the same fixes wherever the claim also appears (caption, "
                    "on-screen text, slides, graphics). Keep everything else as it is.\n<fact_check_findings>\n"
                    + revision_notes + "\n</fact_check_findings>"
                )
            feedback = base_feedback
            window = None  # the word window a too-long draft set; later drafts are held to it
            # What this call cost in drafts, for the run's record (the record said "1 round" for a run whose
            # log showed two drafts rejected here: Memnon, 2026-10-25).
            self.last_drafts, self.last_draft_problems = 0, []
            for attempt in range(1, max_attempts + 1):
                self.last_drafts = attempt
                self._report(f"Writing draft {attempt} of {max_attempts}")
                try:
                    pkg = self._generate_with_claude(topic, feedback)
                except LLMError as e:
                    log.error("Script generation failed: %s", e)
                    writer_error = str(e)
                    break
                pkg = prune_graphics(pkg)  # an invalid graphic is dropped, not allowed to sink the script
                # Structure, length and the opening together: one rewrite fixes all of them.
                problems = draft_problems(pkg)
                # Story bodies, copy faults and a lone repeat are edited in place (short calls), the narration and
                # its length kept; a length miss left with them is then edited below, as on its own (production,
                # Roman concrete 2026-10-29: a caption line, then a poll, each cost a rewrite and a new length).
                pkg, problems = self._narrow_edits(pkg, problems, self._sheet(topic))
                blocking = blocking_problems(problems)
                if not blocking:
                    _note_kept_hook(problems)
                    return self._finalize(pkg, topic, origin=writer_origin(), fact_sheet_used=self._sheet(topic) is not None)
                log.warning("Attempt %d failed structural checks: %s", attempt, problems)
                self.last_draft_problems.append(f"draft {attempt}: {_short_reason(problems)}")
                last_problems = problems
                improve.note_problems(problems, "structure")
                if (any("when spoken" in p for p in blocking)
                        and all("when spoken" in p or p.startswith(REPEAT_PROBLEM) for p in blocking)):
                    # Only the length is off (with, at most, a repeated phrase the same edit rewords): the draft is edited to the window (one short call), not written
                    # again from scratch (a rewrite took 2 min 46 s and came out at the writer's usual length
                    # again; production, 2026-10-24).
                    fitted = self._fit_length_by_editing(pkg, blocking, topic, feedback)
                    if fitted is not None:
                        return self._finalize(fitted, topic, origin=writer_origin(), fact_sheet_used=True)
                found = re.search(r"(?:in|to) (\d+)-(\d+) words", " ".join(problems))
                window = (int(found.group(1)), int(found.group(2))) if found else window
                # A too-short draft after a too-long one is reminded of the window, so it does not swing back.
                keep = (f" Keep the narration between {window[0]} and {window[1]} words: longer runs past the "
                        "time limit when spoken." if window and not found else "")
                # A length problem is fixed on this draft, not by starting over: each fresh rewrite came out at
                # the writer's usual length again (2026-10-23). The facts it has were already checked.
                length_only = all("when spoken" in p for p in blocking)
                carry = (f"\n\n<previous_narration>\n{pkg.narration_script}\n</previous_narration>\nAdjust this "
                         "narration to the length above, adding or removing sentences using only facts in the "
                         "research notes; keep the rest of it, and the other fields, as they are."
                         if length_only and pkg.narration_script else "")
                feedback = (base_feedback + "\n\nA previous draft was rejected for: " + " ".join(problems) + keep
                            + " Fix these." + carry)
                self._report(f"Draft {attempt} of {max_attempts} rejected ({_short_reason(blocking)})"
                             + ("; rewriting" if attempt < max_attempts else ""))
            log.warning("The script writer (%s) did not produce a usable script; trying a curated script.", writer_origin())

        curated = self._load_curated(topic["id"])
        if curated:
            pkg, reviewed = curated
            log.info("Using curated script for '%s' (human reviewed: %s).", topic["id"], reviewed)
            payload = self._finalize(pkg, topic, origin="curated")
            payload["human_reviewed"] = reviewed
            if last_problems or writer_error:  # why the writer's drafts were not used, for the run's record
                payload["writer_failure"] = (
                    f"The script writer ({writer_origin()}) could not be reached: {writer_error[:300]}" if writer_error
                    else f"The script writer ({writer_origin()}) made {self.last_drafts} attempt(s) and each failed its "
                         f"checks (last: {' '.join(last_problems)[:400]})")
            return payload
        if topic["id"] in self._starved:  # too few verified facts and no curated script: skip the topic
            raise self._starved[topic["id"]]

        if writer_error:  # the writer is configured but its request failed (production 2026-10-23: a timeout)
            raise ScriptUnavailableError(
                f"The script writer ({writer_origin()}) could not be reached for '{topic['id']}': {writer_error[:300]}. "
                "No video was made; run it again later."
            )
        if last_problems:  # the writer ran; say what stopped it rather than blaming configuration
            raise ScriptUnavailableError(
                f"The script writer ({writer_origin()}) made {max_attempts} attempts for '{topic['id']}' and each failed "
                f"its checks (last: {' '.join(last_problems)}). Run it again, or add a curated script at "
                f"data/scripts/{topic['id']}.json."
            )
        raise ScriptUnavailableError(
            f"No script source for topic '{topic['id']}': configure a script engine (free Gemini or ANTHROPIC_API_KEY) "
            f"or add a curated script at data/scripts/{topic['id']}.json."
        )

    def _sheet(self, topic: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The topic's verified fact sheet (cached on disk; built if missing), or None if it can't be built."""
        if topic["id"] not in self._sheets:
            try:
                self._sheets[topic["id"]] = fact_sheet.ensure(topic) if _ready() else None
            except fact_sheet.FactStarvedTopicError as e:
                log.warning("Fact sheet for %s is too thin (%s).", topic["id"], e)
                self._starved[topic["id"]] = e
                self._sheets[topic["id"]] = None
            except (LLMError, ValueError) as e:
                log.warning("No fact sheet for %s (%s); writing from research notes.", topic["id"], e)
                self._sheets[topic["id"]] = None
        return self._sheets[topic["id"]]

    def _revise_surgically(self, previous: Dict[str, Any], findings: str, sheet: Dict[str, Any]) -> Optional[ScriptPackage]:
        """Replace only the sentences the checks rejected, using the fact sheet; everything else stays.
        Returns None if the edits can't be applied cleanly (the caller then rewrites the draft)."""
        draft = {k: previous.get(k) for k in ScriptPackage.model_fields}
        result = _structured(
            REVISE_SYSTEM,
            f"<verified_facts>\n{fact_sheet.as_prompt(sheet)}\n</verified_facts>\n\n"
            f"<draft>\n{json.dumps(draft, ensure_ascii=False, indent=1)}\n</draft>\n\n"
            + (f"<graphics>\n" + "\n".join(graphic_paths(draft)) + "\n</graphics>\n\n" if graphic_paths(draft) else "")
            + f"<findings>\n{findings}\n</findings>"
            + mandatory_hedges_note(sheet) + repeat_rules(draft.get("narration_script") or ""),
            Edits, effort="medium", max_tokens=6000)
        text = json.dumps(draft, ensure_ascii=False)
        for e in result.edits:
            find_str = e.find.strip()
            replace_str = e.replace.strip()
            target = (getattr(e, "target", None) or "").strip()
            # A graphic is edited by its fields (graphic_edit): by the path the edit names, or the entry a JSON
            # fragment in "find" quotes; a text search of the serialized draft cannot find a field's JSON.
            if target or _JSON_PAIR.search(find_str):
                if graphic_edit(draft, target, find_str, replace_str):
                    text = json.dumps(draft, ensure_ascii=False)
                    continue
                if target and not find_str:
                    log.warning("Graphic edit for %r not applicable; rewriting instead.", target[:60])
                    return None
            if not find_str:
                continue
            old = json.dumps(find_str, ensure_ascii=False)[1:-1]
            new = json.dumps(replace_str, ensure_ascii=False)[1:-1]
            if old in text or find_str in text:
                text = text.replace(old, new) if old in text else text.replace(find_str, replace_str)
                try:  # the fields stay the record: a later edit, or a graphic's, works on this one's result
                    draft = json.loads(text)
                except ValueError as err:
                    log.warning("Surgical edits broke the draft (%s); rewriting instead.", err)
                    return None
            elif graphic_edit(draft, None, find_str, replace_str):   # a quoted emphasis word or label
                text = json.dumps(draft, ensure_ascii=False)
            else:
                # Scan individual draft string fields
                found = False
                for k, v in draft.items():
                    if isinstance(v, str) and find_str in v:
                        draft[k] = v.replace(find_str, replace_str)
                        found = True
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                for sub_k, sub_v in item.items():
                                    if isinstance(sub_v, str) and find_str in sub_v:
                                        item[sub_k] = sub_v.replace(find_str, replace_str)
                                        found = True
                if found:
                    text = json.dumps(draft, ensure_ascii=False)
                else:
                    # Resilient fallback: locate sentence by normalized core (ignoring quotation, whitespace & punctuation diffs)
                    core = " ".join(re.sub(r"[^\w\s]", " ", find_str).lower().split())
                    if len(core) >= 12:
                        for k, v in draft.items():
                            if isinstance(v, str):
                                norm_v = " ".join(re.sub(r"[^\w\s]", " ", v).lower().split())
                                if core in norm_v:
                                    sentences = re.split(r"(?<=[.!?])\s+", v)
                                    new_sentences = []
                                    for s in sentences:
                                        norm_s = " ".join(re.sub(r"[^\w\s]", " ", s).lower().split())
                                        if core in norm_s or norm_s in core:
                                            if replace_str:
                                                new_sentences.append(replace_str)
                                            found = True
                                        else:
                                            new_sentences.append(s)
                                    draft[k] = " ".join(new_sentences)
                            elif isinstance(v, list):
                                for item in v:
                                    if isinstance(item, dict):
                                        for sub_k, sub_v in item.items():
                                            if isinstance(sub_v, str):
                                                norm_sub = " ".join(re.sub(r"[^\w\s]", " ", sub_v).lower().split())
                                                if core in norm_sub:
                                                    sentences = re.split(r"(?<=[.!?])\s+", sub_v)
                                                    new_sentences = []
                                                    for s in sentences:
                                                        norm_s = " ".join(re.sub(r"[^\w\s]", " ", s).lower().split())
                                                        if core in norm_s or norm_s in core:
                                                            if replace_str:
                                                                new_sentences.append(replace_str)
                                                            found = True
                                                        else:
                                                            new_sentences.append(s)
                                                    item[sub_k] = " ".join(new_sentences)
                        if found:
                            text = json.dumps(draft, ensure_ascii=False)
                    if not found:
                        log.warning("Surgical edit not found in the draft (%r); rewriting instead.", e.find[:60])
                        return None
        # A revision must remove what was rejected: a quoted claim still in the draft (another field kept it, or
        # the edit missed) would be resubmitted and rejected again, as happened twice in a row on Damascus steel.
        def plain(value: str) -> str:
            return " ".join(re.sub(r"[^\w\s]", " ", value.lower()).split())
        revised = plain(text)
        # A rejected claim is a quote followed by its reason in brackets; quotes inside the reason (the required
        # hedge, the source's wording) are what the draft should say, not claims (Richat kept its hedge, 2026-10-23).
        for claim in re.findall(r"['\"`]([^'\"`]{20,})['\"`]\s*\(", findings):
            if plain(claim) in revised:
                log.warning("Surgical revision kept a rejected claim (%r); rewriting instead.", claim[:60])
                return None
        try:
            return ScriptPackage.model_validate(json.loads(text))
        except (ValueError, ValidationError) as e:
            log.warning("Surgical edits broke the draft (%s); rewriting instead.", e)
            return None

    _SMALL_LENGTH_MISS = 3  # words: a length edit this close gets one more edit, not a rewrite

    def _fit_length_by_editing(self, pkg: ScriptPackage, problems: List[str], topic: Dict[str, Any],
                               feedback: str) -> Optional[ScriptPackage]:
        """A draft whose only fault is its spoken length (and perhaps a repeated phrase), edited to the word window
        with verified facts (the surgical reviser), a repeat the edit leaves reworded by one more short edit, then
        given its graphics. None when there is no fact sheet, the edit fails
        or the result still misses: the caller rewrites as before. A draft just past the plan's ceiling
        (near_miss_seconds) is kept after this one trim attempt: the trimmed draft when only that is left of its
        faults, else the draft as it came."""
        sheet = self._sheet(topic)
        if sheet is None:
            return None
        try:
            length = [p for p in problems if not p.startswith(REPEAT_PROBLEM)]
            repeats = [p for p in problems if p.startswith(REPEAT_PROBLEM)]
            fitted = self._revise_surgically(pkg.model_dump(), "LENGTH: " + " ".join(length)
                                             + (" REPEAT: " + " ".join(repeats) if repeats else ""), sheet)
        except LLMError as e:
            log.warning("Length edit failed (%s); rewriting the draft.", e)
            return self._kept_near_miss(pkg, problems)
        if fitted is None:
            return self._kept_near_miss(pkg, problems)
        fitted = prune_graphics(fitted)
        # The edit reached the length but said a phrase twice ("geoglyphs etched into", development 2026-10-28,
        # twice, each followed by a full rewrite), or left a copy fault: reworded by one more short edit.
        fitted, still = self._narrow_edits(fitted, draft_problems(fitted), sheet)
        still = blocking_problems(still)
        miss = length_miss_words(still) if still else None
        if miss is not None and miss <= self._SMALL_LENGTH_MISS:
            # A few words out (production, Derinkuyu 2026-10-29: 106 words against 99-103, then a full rewrite):
            # one more short edit of these words, not a rewrite that starts the length over.
            try:
                again = self._revise_surgically(fitted.model_dump(), "LENGTH: " + " ".join(still), sheet)
            except LLMError as e:
                log.warning("Second length edit failed (%s).", e)
                again = None
            if again is not None:
                fitted, still = prune_graphics(again), blocking_problems(draft_problems(prune_graphics(again)))
        if still and self._kept_near_miss(fitted, still) is not None:
            still = []   # trimmed once and just past the plan's ceiling: kept (NEAR_MISS_CEILING)
        if still:
            log.warning("Length edit still misses (%s); rewriting the draft.", still)
            return self._kept_near_miss(pkg, problems)
        _note_kept_hook(draft_problems(fitted))
        log.info("Length fixed by editing the draft: %d -> %d words.",
                 len(pkg.narration_script.split()), len(fitted.narration_script.split()))
        notes, retrieved = self._sheet_context(sheet)
        # The graphics are planned again for the edited words: with the disputed figures' hedges (a flat "8 to 18"
        # levels card was planned here and rejected: production, Derinkuyu 2026-10-29).
        return self._with_graphics(fitted, notes, retrieved, mandatory_hedges_note(sheet) + feedback)

    def _kept_near_miss(self, pkg: ScriptPackage, problems: List[str]) -> Optional[ScriptPackage]:
        """`pkg`, recorded as kept (keep_near_miss), when its only blocking problem is its length just past the plan's
        ceiling and nothing else blocks once it is; else None (the caller rewrites as before)."""
        seconds = near_miss_seconds(blocking_problems(problems))
        if seconds is None:
            return None
        keep_near_miss(pkg, seconds)
        if blocking_problems(draft_problems(pkg)):   # read again, it is not inside NEAR_MISS_CEILING after all
            _near_misses_kept.pop(_narration_key(pkg.narration_script), None)
            return None
        log.info("Draft kept at %.1fs spoken after its trim attempt (plan %gs; takes are kept up to %gs).",
                 seconds, HARD_DURATION_CEILING - _MEASURE_MARGIN_SEC, config.VOICE_DURATION_CEILING)
        improve.note("script.length_near_miss", f"kept at {seconds:.1f}s after one trim attempt", "repair")
        return pkg

    def _fix_repeats(self, pkg: ScriptPackage, problems: List[str], sheet: Dict[str, Any]) -> Optional[ScriptPackage]:
        """A draft whose narration says a phrase twice, with the later sentence reworded by one short edit (the
        surgical reviser, told the phrases already said and the mandatory hedges), or None when the edit fails: the
        caller rewrites as before. The result is checked again in full by the caller, both fact checks included."""
        repeats = [p for p in problems if p.startswith(REPEAT_PROBLEM)]
        if not repeats:
            return None
        try:
            fixed = self._revise_surgically(pkg.model_dump(), "REPEAT: " + " ".join(repeats), sheet)
        except LLMError as e:
            log.warning("Repeat edit failed (%s); rewriting the draft.", e)
            return None
        if fixed is None:
            return None
        log.info("Repeated phrase reworded by a short edit: %s", " ".join(repeats)[:200])
        return prune_graphics(fixed)

    def _edit_copy(self, pkg: ScriptPackage, problems: List[str], sheet: Dict[str, Any]) -> Optional[ScriptPackage]:
        """A draft whose copy faults (caption, poll, slides, title, hashtags, first comment, share line) are fixed by
        one short writer call that edits only the fields they lie in, or None when the call fails, a fault remains or
        a new one appears: the caller goes on as before (a rewrite). The narration, hook, closing sentence, sources
        and graphics are carried over untouched, so the measured length stands; the caller checks the result again
        in full, and both fact checks see it like any other draft."""
        faults = [p for p in problems if copy_problem_fields(p, pkg) is not None]
        fields = tuple(f for f in dict.fromkeys(f for p in faults for f in copy_problem_fields(p, pkg))
                       if f != "carousel_caption" or pkg.carousel_caption)   # never one written where there was none
        if not faults or not fields:
            return None
        # The copy someone wrote, without the pipeline's credit lines: told the caption had too many emoji, the edit
        # removed the credits' 📷/🎬 (production, Derinkuyu 2026-10-29). They are appended again to its result.
        from .credits import hold_attribution, restore_attribution
        current, held = hold_attribution({f: v for f, v in pkg.model_dump().items() if f in fields})
        texts = [json.dumps(current, ensure_ascii=False), pkg.narration_script]   # the copy may restate its figures
        prompt = (f"<narration>\n{pkg.narration_script}\n</narration>\n\n"
                  f"<verified_facts>\n{fact_sheet.as_prompt(sheet)}\n</verified_facts>\n\n"
                  f"<fields>\n{json.dumps(current, ensure_ascii=False, indent=1)}\n</fields>\n\n"
                  f"<problems>\n" + "\n".join(f"- {p}" for p in faults) + "\n</problems>\n\n"
                  f"<field_rules>\n{_field_rules(fields)}\n</field_rules>"
                  + mandatory_hedges_note(sheet, texts=texts) + repeat_rules(pkg.narration_script)
                  + "\nThe copy may restate the narration's facts, but no field says one phrase twice."
                  + f"\n\nReturn only these fields, each whole: {', '.join(fields)}.")
        try:
            if free_llm.enabled():
                out = free_llm.call(COPY_EDIT_SYSTEM, prompt, _CopyEdit, role="writer", task_class="light", effort="low", max_tokens=4000)
            else:
                out = structured_call(COPY_EDIT_SYSTEM, prompt, _CopyEdit, effort="low", max_tokens=4000)
        except (LLMError, OSError, ValueError) as e:
            log.warning("Copy edit failed (%s); rewriting the draft.", e)
            return None
        update = restore_attribution({f: v for f, v in out.model_dump().items() if f in fields and v is not None}, held)
        if not update:
            log.warning("Copy edit returned none of %s; rewriting the draft.", ", ".join(fields))
            return None
        try:
            fixed = normalize_copy(ScriptPackage.model_validate({**pkg.model_dump(), **update}))
        except ValidationError as e:
            log.warning("Copy edit broke the draft (%s); rewriting.", e)
            return None
        kept = ("narration_script", "hook_sentence", "closing_sentence", "sources", *GRAPHICS_FIELDS)
        if any(getattr(fixed, k) != getattr(pkg, k) for k in kept):  # never: the edit holds none of them
            log.warning("Copy edit changed the narration or graphics; rewriting the draft.")
            return None
        before, after = validate_structure(pkg), validate_structure(fixed)
        left = [p for p in after if copy_problem_fields(p, fixed) is not None]
        new = [p for p in after if p not in before]
        if left or new:
            log.warning("Copy edit still misses (%s); rewriting the draft.", " ".join(dict.fromkeys(left + new))[:300])
            return None
        improve.note_problems(faults, "structure")  # still the writer's miss, for the findings ledger
        log.info("Copy fixed by a short edit of %s: %s", ", ".join(update), " ".join(faults)[:300])
        return fixed

    def _narrow_edits(self, pkg: ScriptPackage, problems: List[str], sheet: Optional[Dict[str, Any]]
                      ) -> Tuple[ScriptPackage, List[str]]:
        """The short edits a draft's faults allow, when every blocking one is a Story body too long, a copy fault, a
        repeated phrase or the spoken length: the Story bodies shortened, the copy edited, a lone repeat reworded,
        each checked again in full (draft_problems). The length itself is left to _fit_length_by_editing. Returns
        the draft and its problems, unchanged when an edit cannot apply (the caller rewrites as before)."""
        def blocking() -> List[str]:
            return blocking_problems(problems)

        def narrow(p: str) -> bool:
            return "when spoken" in p or p.startswith(REPEAT_PROBLEM) or copy_problem_fields(p, pkg) is not None

        if (sheet is not None and _opening_faults(problems) and all(narrow(p) for p in blocking())
                and not any("when spoken" in p for p in blocking())):   # after a length edit, on its words
            # The site unplaced, a story the narration does not tell, a hook over its length: one short edit of the
            # opening (production Derinkuyu 2026-10-29: these were only warned about), not a rewrite.
            edited = self._edit_story(pkg, problems, sheet)
            if edited is not None:
                pkg, problems = edited, draft_problems(edited)
        if not blocking() or not all(narrow(p) for p in blocking()):
            return pkg, problems
        if any(STORY_OVERFLOW.match(p) for p in blocking()):
            # A Story body over three lines is shortened in place (one short call), as on its own.
            shortened = self._shorten_story_slides(pkg, blocking(), sheet)
            if shortened is not None:
                pkg, problems = shortened, draft_problems(shortened)
        copy = [p for p in blocking() if copy_problem_fields(p, pkg) is not None]
        for p in copy:
            if p.startswith(HEADLINE_PROBLEM):   # a figure the sheet does not support: a finding, then the copy edit
                improve.note("script.headline_figure", p[:300], "structure")
        if copy and sheet is not None and all(narrow(p) for p in blocking()):
            edited = self._edit_copy(pkg, copy, sheet)
            if edited is not None:
                pkg, problems = edited, draft_problems(edited)
        if blocking() and all(p.startswith(REPEAT_PROBLEM) for p in blocking()) and sheet is not None:
            fixed = self._fix_repeats(pkg, blocking(), sheet)
            if fixed is not None:
                pkg, problems = fixed, draft_problems(fixed)
        return pkg, problems

    def _edit_story(self, pkg: ScriptPackage, problems: List[str], sheet: Dict[str, Any]) -> Optional[ScriptPackage]:
        """The opening's faults (_opening_faults: the site not placed in the first sentences, a story the title and
        caption tell that the narration does not, a first sentence over HOOK_MAX_WORDS) fixed by one surgical edit
        with the verified facts and the mandatory hedges, or None when the edit fails, fixes none of them or brings
        a new blocking fault (other than the length, which the caller edits as it would any other draft's). Asked
        once per narration. The result is fact-checked in full like any draft."""
        faults = _opening_faults(problems)
        tried = self.__dict__.setdefault("_story_tried", set())
        if not faults or pkg.narration_script in tried:
            return None
        tried.add(pkg.narration_script)
        findings = ("OPENING: " + " ".join(faults) + f" Edit only what these need: the narration's first "
                    f"{PLACE_WITHIN} sentences (a new first sentence of at most {HOOK_MAX_WORDS} words keeps its facts "
                    "and hedges), and for a story the title and caption tell, the narration's opening or else the title "
                    "and the caption's first line. Keep the length and every other sentence.")
        try:
            edited = self._revise_surgically(pkg.model_dump(), findings, sheet)
        except LLMError as e:
            log.warning("Opening edit failed (%s); the draft goes on as it was.", e)
            return None
        if edited is None:
            return None
        first_before, first_after = _first_sentence(pkg.narration_script), _first_sentence(edited.narration_script)
        if pkg.hook_sentence.strip() == first_before and first_after != first_before:
            edited = edited.model_copy(update={"hook_sentence": first_after})   # the hook is the first sentence
        edited = prune_graphics(edited)
        before, after = draft_problems(pkg), draft_problems(edited)
        # A length problem the edit brings is a regression when the draft was inside the window (review rc72: an
        # inserted place took a fitted 108-word draft to 114 and cost a rewrite); only a draft already the wrong
        # length is left to the caller's length edit.
        was_long_or_short = any("when spoken" in p for p in before)
        new = [p for p in blocking_problems(after) if p not in blocking_problems(before)
               and ("when spoken" not in p or not was_long_or_short)]
        if len(_split_sentences(edited.narration_script)) > len(_split_sentences(pkg.narration_script)):
            new.append("the edit added a sentence (the word budget: the place goes inside an existing one)")
        if new or len(_opening_faults(after)) >= len(faults):
            log.warning("Opening edit did not fix the opening (%s); the draft goes on as it was.",
                        " ".join(new or _opening_faults(after))[:200])
            return None
        log.info("Opening edited: %s", " ".join(f[:80] for f in faults))
        return edited

    def _shorten_story_slides(self, pkg: ScriptPackage, problems: List[str],
                              sheet: Optional[Dict[str, Any]] = None) -> Optional[ScriptPackage]:
        """A draft whose Story bodies run past three lines, with only those bodies shortened (one short call), or
        None when the call fails or a body still misses: the caller rewrites as before. Nothing else changes, so
        the narration, its measured length and its graphics stand; each shortened body must be one sentence,
        shorter than it was, fit the frame as drawn, and (slide 3) ask nothing; the draft is then checked again,
        both fact checks included, like any other."""
        from .multi_format import STORY_BODY_LINES, story_body_lines
        over = sorted({int(m.group(1)) for m in (STORY_OVERFLOW.match(p) for p in problems) if m})
        items = [(i, story_body_word_limit(asks_poll=i == 3), pkg.story_slides[i - 1].body)
                 for i in over if 1 <= i <= len(pkg.story_slides)]
        if not items:
            return None
        prompt = ("\n".join(f"Slide {i} (at most {limit} words): {body}" for i, limit, body in items)
                  + mandatory_hedges_note(sheet, texts=[body for _, _, body in items]))
        try:
            if free_llm.enabled():
                out = free_llm.call(STORY_SHORTEN_SYSTEM, prompt, _Shortened, role="writer", task_class="light", effort="low", max_tokens=800)
            else:
                out = structured_call(STORY_SHORTEN_SYSTEM, prompt, _Shortened, effort="low", max_tokens=800)
        except (LLMError, OSError, ValueError) as e:
            log.warning("Story slide shortening failed (%s); rewriting the draft.", e)
            return None
        if len(out.texts) != len(items):
            log.warning("Story slide shortening returned %d bodies for %d; rewriting the draft.", len(out.texts), len(items))
            return None
        slides = list(pkg.story_slides)
        for (i, limit, old), new in zip(items, out.texts):
            new = " ".join((new or "").split())
            poll = i == 3
            if (not new or len(new.split()) >= len(old.split()) or len(_split_sentences(new)) != 1
                    or not new.endswith((".", "!")) and not (new.endswith("?") and not poll)
                    or story_body_lines(new, asks_poll=poll) > STORY_BODY_LINES):
                log.warning("Story slide %d's shortened body still misses (%r); rewriting the draft.", i, new[:90])
                return None
            slides[i - 1] = slides[i - 1].model_copy(update={"body": new})
            log.info("Story slide %d's body shortened: %d -> %d words.", i, len(old.split()), len(new.split()))
        improve.note_problems([p for p in problems if STORY_OVERFLOW.match(p)], "structure")  # still the writer's miss
        return pkg.model_copy(update={"story_slides": slides})

    @staticmethod
    def _sheet_context(sheet: Dict[str, Any]) -> Tuple[str, List[Source]]:
        """The writer's view of a fact sheet: the verified facts as notes, and its sources best tier first."""
        notes = "VERIFIED FACTS (use only these; keep each DISPUTED fact's hedge wording):\n" + fact_sheet.as_prompt(sheet)
        retrieved = [Source(title=s["title"], url=s["url"]) for s in sorted(sheet["sources"], key=lambda s: s["tier"])]
        return notes, retrieved

    def _with_graphics(self, core, notes: str, retrieved: List[Source], feedback: str) -> ScriptPackage:
        """The finished script with its planned graphics (optional: a failure leaves them out) and only
        sources the research retrieved."""
        try:
            extras = _structured(
                GRAPHICS_SYSTEM,
                f"<narration>\n{core.narration_script}\n</narration>\n\n<research_notes>\n{notes}\n</research_notes>\n\n"
                + GRAPHICS_PROMPT + feedback, GraphicsPlan, effort="medium")  # revision notes and hedges cover graphics too
        except LLMError as e:
            log.warning("Graphics planning failed (%s); this script has no planned graphics.", e)
            extras = GraphicsPlan()
        core_fields = {k: v for k, v in core.model_dump().items() if k in ScriptCore.model_fields}
        pkg = ScriptPackage(**{**core_fields, **{k: v for k, v in extras.model_dump().items() if v is not None}})
        retrieved_urls = {s.url for s in retrieved}
        pkg.sources = [s for s in pkg.sources if s.url in retrieved_urls] or retrieved
        return pkg

    def _generate_with_claude(self, topic: Dict[str, Any], feedback: str) -> ScriptPackage:
        loc = topic.get("location", {})
        fields = dict(
            title=topic.get("title", ""),
            sub_theme=topic.get("sub_theme", ""),
            hook=topic.get("hook", ""),
            location=", ".join(v for v in (loc.get("name"), loc.get("city"), loc.get("country")) if v),
            key_facts="; ".join(topic.get("key_facts", [])),
            story_words=story_body_word_limit(),
            poll_words=story_body_word_limit(asks_poll=True),
        )
        sheet = self._sheet(topic)
        if sheet is not None:
            # Verify first, then write: the writer may use only the verified facts, with their hedges.
            notes, retrieved = self._sheet_context(sheet)

            # Extract distinct raw evidence quotes so the writer sees the exact academic/encyclopedic text
            evidence_listing = []
            seen_q = set()
            for f in sheet.get("facts", []):
                for e in f.get("evidence", []):
                    q_text = (e.get("quote") or "").strip()
                    if q_text and q_text not in seen_q:
                        seen_q.add(q_text)
                        evidence_listing.append(f"- \"{q_text[:400]}\"")
            evidence_block = "\n".join(evidence_listing[:35])
            raw_evidence_str = (
                f"\n\n<raw_evidence_quotes>\n{evidence_block}\n</raw_evidence_quotes>\n"
                if evidence_block else ""
            )

            prompt = (
                VERIFIED_USER_PROMPT_TEMPLATE.format(**fields)
                + f"\n\n<verified_facts>\n{fact_sheet.as_prompt(sheet)}\n</verified_facts>"
                + raw_evidence_str
                + f"\n<retrieved_sources>\n{format_sources(retrieved)}\n</retrieved_sources>\n"
                + FACTS_ONLY_RULES + mandatory_hedges_note(sheet) + f"\n\nREPEATS: {REPEAT_RULE}"
                + improve.learned_rules() + feedback
            )
        elif free_llm.enabled():
            raise LLMError("No verified fact sheet could be built; the free engine writes only from one.")
        else:
            if topic["id"] not in self._research_cache:  # research once per topic; revisions reuse it
                notes, retrieved = research(RESEARCH_SYSTEM, RESEARCH_PROMPT.format(**fields), web_search_uses=6)
                if not retrieved:
                    raise LLMError("Research cited no sources; refusing to write an ungrounded script.")
                self._research_cache[topic["id"]] = (notes, retrieved)
            notes, retrieved = self._research_cache[topic["id"]]
            prompt = (
                USER_PROMPT_TEMPLATE.format(**fields)
                + f"\n\n<research_notes>\n{notes}\n</research_notes>\n\n"
                + f"<retrieved_sources>\n{format_sources(retrieved)}\n</retrieved_sources>\n"
                + "Use only facts supported by the research notes. List sources only from retrieved_sources."
                + feedback
            )
        from .hook_learning import steer_note
        core = _structured(SYSTEM_PROMPT, prompt + steer_note(topic.get("hook_form")) + length_target_note(), ScriptCore,
                           effort="high")
        if spoken_length_problems(ScriptPackage(**core.model_dump())):
            # This draft will be sent back for its length: graphics are planned only for one that can be kept.
            return ScriptPackage(**core.model_dump())
        return self._with_graphics(core, notes, retrieved, mandatory_hedges_note(sheet) + feedback)

    @staticmethod
    def _load_curated(topic_id: str) -> Optional[Tuple[ScriptPackage, bool]]:
        path = CURATED_SCRIPTS_DIR / f"{topic_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            pkg = ScriptPackage.model_validate({k: v for k, v in data.items() if not k.startswith("_")})
            return pkg, data.get("_reviewed_by_human") is True
        except ValidationError as e:
            raise ScriptUnavailableError(f"Curated script {path.name} is invalid: {e}") from e

    @staticmethod
    def _finalize(pkg: ScriptPackage, topic: Dict[str, Any], origin: str, fact_sheet_used: bool = False) -> Dict[str, Any]:
        payload = pkg.model_dump()
        # A surgical edit can leave a space before a full stop ("a cataclysm struck ." was voiced and captioned
        # on the Tunguska development run, 2026-10-25): tidied in every public text before the checks.
        for key in ("narration_script", "hook_sentence", "closing_sentence", "caption", "carousel_caption", "first_comment"):
            if isinstance(payload.get(key), str):
                if key in ("narration_script", "hook_sentence", "closing_sentence"):
                    payload[key] = spoken_narration(payload[key])   # its words are what the voice says
                payload[key] = re.sub(r"[ \t]+([.,;:!?])", r"\1", payload[key])
        payload.update({
            "fact_sheet": fact_sheet_used,  # written from the topic's verified fact sheet: trace-checked
            "topic_id": topic["id"],
            "origin": origin,
            "word_count": len(payload["narration_script"].split()),
            "location": topic.get("location", {}),
            "meta_topic_categories": topic.get("meta_categories", []),
        })
        from .topic_proposer import origin_note
        payload["topic_origin_note"] = origin_note(topic)
        return payload

    def verify_claims(self, script_payload: Dict[str, Any], topic: Optional[Dict[str, Any]] = None,
                      allow_unreviewed_curated: bool = False) -> Tuple[bool, Dict[str, Any], str]:
        """
        Stage 2.5 guardrail. Fails closed: a script is publishable only if it passes the
        structural checks AND every factual claim is verified (or, for curated scripts,
        a human has attached sources).
        """
        try:
            pkg = ScriptPackage.model_validate(script_payload)
        except ValidationError as e:
            return False, script_payload, f"REJECTED: payload does not match schema: {e.error_count()} errors."

        problems = validate_structure(pkg) or spoken_length_problems(pkg)
        if problems:
            return False, script_payload, "REJECTED: " + " ".join(problems)

        sheet = fact_sheet.load(script_payload.get("topic_id", ""), max_age_days=3650) if script_payload.get("fact_sheet") else None
        if sheet is not None:  # written from verified facts: trace every claim to them (same evidence each time)
            passed, confidence, audits, msg = trace_check.check(script_payload, sheet)
            disputed = sum(f["disputed"] for f in sheet["facts"])
            script_payload["fact_sheet_note"] = (f"🧾 Written from a verified fact sheet: {len(sheet['facts'])} facts with quoted "
                                                 f"evidence ({disputed} disputed, used only with their hedges)")
        elif free_llm.enabled():
            # No fact sheet and no paid checks: only a human-reviewed curated script (or a dry run) may pass.
            if script_payload.get("origin") == "curated" and (script_payload.get("human_reviewed") or allow_unreviewed_curated):
                passed, confidence, audits = True, 0.0, []
                msg = "Curated script (" + ("human reviewed" if script_payload.get("human_reviewed") else "UNREVIEWED; dry run only") + ")."
            else:
                why = ("The curated script is not human-reviewed, so only a dry run may use it"
                       if script_payload.get("origin") == "curated" else "No verified fact sheet; the free engine fails closed")
                # The writer's own failure, when that is why the curated script was tried (production, Derinkuyu
                # 2026-10-29: the run said "No verified fact sheet" for three drafts the wrong length).
                failure = script_payload.get("writer_failure")
                passed, confidence, audits, msg = False, 0.0, [], f"{why}." + (f" {failure}" if failure else "")
        else:
            passed, confidence, audits, msg = self.fact_checker.verify(script_payload, topic, allow_unreviewed_curated)
        script_payload["fact_audits"] = audits
        script_payload["consensus_confidence"] = confidence
        log.info("Fact check %s: %s", "passed" if passed else "REJECTED", msg[:400])   # the run's record of the verdict
        if not passed:
            return False, script_payload, "REJECTED by fact checker: " + self._with_second_findings(script_payload, sheet, msg)

        # Independent second check (only for Claude-written scripts that passed the first).
        if SECOND_CHECK_ENABLED and script_payload.get("origin") in AI_ORIGINS:
            if free_llm.enabled() or (sheet is not None and trace_check.gemini_checker_available()):
                # A different model judges every claim against the raw evidence quotes: free on the free
                # engine, and on the Claude engine too when a verified sheet and a Gemini key exist (the
                # web-searching second opinion cost $2-4 a round for such scripts; writer trial, 2026-10-24).
                status, conf2, audits2, msg2 = self._free_second_check(script_payload, sheet)
            else:
                status, conf2, audits2, msg2 = self.fact_checker.second_opinion(script_payload)
            log.info("Second check %s: %s", status, msg2[:400])
            script_payload["second_check"] = {"status": status, "confidence": conf2, "audits": audits2, "summary": msg2}
            if status != "pass":  # fail closed: a check that could not run proves nothing
                reason = "second fact checker" if status == "fail" else "second fact check, which could not run"
                return False, script_payload, f"REJECTED by {reason}: {msg2}"
            msg = f"{msg} | 2nd check: {msg2}"
        return True, script_payload, f"Structure OK ({pkg.narration_script.count(' ') + 1} words) | {msg}"

    def _with_second_findings(self, payload: Dict[str, Any], sheet: Optional[Dict[str, Any]], msg: str) -> str:
        """The second checker's objections to a draft the first checker already rejected, so one revision
        fixes both. Before, the second checker only saw a draft that had passed the first, and its objection
        surfaced one draft later and cost a whole round (Göbekli Tepe, production 2026-10-24). One extra
        call on a draft that is being revised anyway; its verdict is not recorded (the final draft's is)."""
        if not (SECOND_CHECK_ENABLED and sheet is not None and free_llm.enabled() and payload.get("origin") in AI_ORIGINS):
            return msg
        try:
            status, _, _, msg2 = trace_check.evidence_check(payload, sheet)
        except LLMError as e:
            log.info("The second checker could not look at the rejected draft (%s).", e)
            return msg
        if status == "fail":
            return f"{msg} | ALSO REJECTED by second fact checker: {msg2}"
        return msg

    def _free_second_check(self, payload: Dict[str, Any], sheet: Optional[Dict[str, Any]]):
        """The free evidence check, tried again after a wait when it is unavailable: a per-minute quota
        comes back, and a finished script is worth two minutes more (a run was lost to one unavailable
        check, dev 2026-10-23). If Gemini quota is exhausted and Claude is available, escalates to
        Claude second_opinion fallback before failing closed."""
        if sheet is None:
            return "fail", 0.0, [], "No fact sheet: the free engine can't check this script."
        result = ("unavailable", 0.0, [], "Not attempted yet")
        for attempt in range(1, SECOND_CHECK_ATTEMPTS + 1):
            result = trace_check.evidence_check(payload, sheet)
            if result[0] != "unavailable" or attempt == SECOND_CHECK_ATTEMPTS:
                break
            log.info("Second fact check unavailable (%s); trying again in %d s (%d of %d).",
                     result[3], SECOND_CHECK_RETRY_SECONDS, attempt, SECOND_CHECK_ATTEMPTS)
            self._report(f"Second fact check unavailable; trying again in {SECOND_CHECK_RETRY_SECONDS} s")
            _sleep(SECOND_CHECK_RETRY_SECONDS)
        if result[0] == "unavailable" and claude_available():
            log.info("Free second check unavailable (%s); escalating to Claude second_opinion fallback.", result[3])
            return self.fact_checker.second_opinion(payload)
        return result

    def is_deep_dive_eligible(self, topic: Dict[str, Any], visual_count: int = 0) -> bool:
        """Determines if a topic has sufficient fact density and visual assets for a 4-minute documentary."""
        sheet = self._sheet(topic)
        facts_count = len(sheet.get("facts", [])) if sheet else len(topic.get("key_facts", []))
        return facts_count >= 8 and (visual_count == 0 or visual_count >= 6)

    def generate_deep_dive(self, topic: Dict[str, Any], feedback: str = "") -> DeepDivePackage:
        """Generates a complete 5-act long-form documentary script package (~3:30 to 5:00 min)."""
        sheet = self._sheet(topic)
        if sheet is None:
            raise ScriptUnavailableError(f"No verified fact sheet available for topic '{topic['id']}'.")

        def _build_fallback() -> DeepDivePackage:
            acts = []
            facts = sheet.get("facts", [])
            # Topic-neutral titles, and narration made only of the sheet's own statements (the old
            # titles and filler were Derinkuyu's, and every act repeated the first two facts).
            act_titles = ["Introduction", "What the Evidence Shows", "How It Came to Be",
                          "What Was Found", "Why It Matters"]
            per_act = max(1, -(-len(facts) // 5))
            for i in range(5):
                chunk = facts[i * per_act:(i + 1) * per_act] or facts[-1:]
                facts_text = " ".join(f.get("statement", "") for f in chunk)
                acts.append(DocumentaryAct(
                    act_number=i + 1,
                    act_title=act_titles[i],
                    target_duration_sec=45,
                    narration_text=facts_text,
                    visual_intent=f"Archival visuals illustrating {act_titles[i]}",
                    graphic_type="none",
                    claim_fact_ids=[f.get("id", f"F{i+1}") for f in chunk],
                ))
            return DeepDivePackage(
                topic_id=topic["id"],
                title=topic.get("title", ""),
                youtube_title=topic.get("title", "")[:100],
                description=f"A documentary about {topic.get('title', '')}, from its verified fact sheet.",
                tags=["documentary", "history"],
                acts=acts,
                closing_debate_question=f"What would you most like to know next about {topic.get('title')}?",
                sources=[],
            )

        if not _ready():
            return _build_fallback()

        loc = topic.get("location", {})
        location_str = ", ".join(v for v in (loc.get("name"), loc.get("city"), loc.get("country")) if v)
        prompt = (
            f"Topic title: {topic.get('title')}\n"
            f"Sub-theme: {topic.get('sub_theme', '')}\n"
            f"Location: {location_str}\n\n"
            f"<verified_facts>\n{fact_sheet.as_prompt(sheet)}\n</verified_facts>\n\n"
            f"Write the complete 5-act investigative documentary script for YouTube and Facebook In-Stream Video (16:9 widescreen).\n"
            f"Target duration: 3:30 to 4:45 minutes (approx. 550 to 750 spoken words across 5 acts)."
        )
        if feedback:
            prompt += f"\n\n<revision_feedback>\n{feedback}\n</revision_feedback>"

        try:
            pkg: DeepDivePackage = _structured(
                DEEP_DIVE_SYSTEM_PROMPT,
                prompt,
                DeepDivePackage,
                effort="high",
                max_tokens=8000,
            )
            return pkg
        except LLMError as e:
            log.warning("Claude deep-dive generation failed (%s); building from verified fact sheet.", e)
            return _build_fallback()

    def verify_deep_dive_act_by_act(self, package: DeepDivePackage, topic: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Verifies each act of a deep-dive script individually against the fact sheet."""
        sheet = self._sheet(topic)
        if sheet is None:
            return False, ["No fact sheet available."]

        all_findings = []
        for act in package.acts:
            passed, findings = trace_check.check_act(act.narration_text, sheet, act.act_number)
            if not passed:
                all_findings.extend(findings or [f"Act {act.act_number}: verification did not pass."])

        return len(all_findings) == 0, all_findings


DEEP_DIVE_SYSTEM_PROMPT = """Write a five-act historical documentary grounded exclusively in the supplied verified fact sheet.
Every empirical claim in narration, titles, descriptions, and chapter headings must trace to a supplied fact.
Preserve each disputed fact's required qualification, source attribution, uncertainty, and scope.
Do not add causes, construction methods, threats, dates, quantities, or discoveries to make a story more dramatic.

Use five acts organized around the strongest available facts:
1. Introduce the subject through a supported observation or a question.
2. Explain its documented physical features.
3. Explore its documented history, clearly preserving uncertainty about origins.
4. Examine the remaining documented evidence and limits of what it establishes.
5. Reflect on those facts and close with a question that asserts no new factual premise.
These are narrative roles, not requirements to invent rediscovery incidents, sieges, laser scans, tunnels, or mysteries.

Aim for 550-750 spoken words when the evidence supports that depth. Do not repeat facts or invent details to reach a runtime.
If the evidence supports less material, return a shorter complete narrative; duration validation will make the limitation visible.
Use plain language. Retain necessary source attribution and disagreement even when it interrupts a dramatic hook.
Use numbers exactly as supported. Do not convert units, round figures, or introduce comparison benchmarks without supporting facts.
For each act, include claim_fact_ids identifying the facts it uses.
Apply the same factual standard to the YouTube title, description, tags, and debate question.
Descriptions may contain chapter labels, but timestamps must be derived from actual audio later rather than presented as measured timings.
Return the requested structured documentary package."""
