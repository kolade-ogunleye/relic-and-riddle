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
import time
from typing import Callable, Dict, Any, List, Optional, Tuple

from pydantic import BaseModel, ValidationError

from .config import (CURATED_SCRIPTS_DIR, HARD_DURATION_CEILING, SECOND_CHECK_ENABLED, TARGET_DURATION_MAX,
                     TARGET_DURATION_MIN, TARGET_WORD_COUNT_MIN, TARGET_WORD_COUNT_MAX,
                     WORD_COUNT_CEILING, WORD_COUNT_FLOOR)
from . import audio_engine, config
from .fact_checker import ClaimFactChecker
from . import free_llm
from .llm import LLMError, claude_available, format_sources, research, structured_call, task as llm_task
from . import fact_sheet, improve, trace_check
from .models import DeepDivePackage, DocumentaryAct, GraphicsPlan, ScriptCore, ScriptPackage, Source


log = logging.getLogger(__name__)


class ScriptUnavailableError(RuntimeError):
    pass


SYSTEM_PROMPT = """You are a documentary scriptwriter producing short vertical Reels (1080x1920) for Facebook and Instagram in the "Historical Curiosities & Ancient Marvels" niche. Accuracy is the brand: viewers and Meta's integrity systems punish fabricated or exaggerated history, so every factual statement you write must be something you have confirmed with web search.

Editorial rules:
- Narration is {WORDS_MIN}-{WORDS_MAX} spoken words (about {SECONDS_MIN}-{SECONDS_MAX} seconds spoken). Hard maximum {WORDS_MAX}. Long words take longer to say: prefer plain, short words, or the script runs long even inside the word range.
- Exactly five beats, tailored to the topic archetype (Knowledge Bomb & Forensic Reality schema):
  (1) Category-Aware Cinematic Hook in the first 3 seconds (Beat 1):
      - Megastructures: Impossible Architectural Contrast or Subterranean Paradox (e.g. ancient structures enduring while modern ones crumble, or whole communities living deep underground).
      - Relic Artifacts: Precision Beyond Its Era (micro-mechanics, astronomical gear trains, or metallurgy more refined than its period is known for), stated as documented fact, never as "lost" or impossible technology.
      - Riddle Events & Cataclysms: Sudden Forensic Anomaly (stark physical devastation, colossal atmospheric shockwave, zero impact crater, or vanished expeditions).
      NEVER start Beat 1 with greetings ('Hey guys'), generic rhetorical questions ('Have you ever wondered'), or robotic hedge words ('Estimated,', 'Approximately,'). Hook the audience immediately with an arresting, tangible reality.
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
                 .replace("{SECONDS_MAX}", f"{TARGET_DURATION_MAX:g}"))

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
- title: the Facebook Reel title shown under the video: at most 60 characters, specific and intriguing (a name, a number or a tension, not a label like "The History of X"); at most one relevant emoji.
- caption: written like a magazine editor, not a template. First line: a curiosity-driven line of at most 110 characters (a hard limit of 125 is shown before "more"; stay well under it) that is NOT the narration's hook sentence. Then 2-3 short sentences that add context the video doesn't say (a detail, a consequence, a name), with the place worked naturally into the prose. Then one genuine question to the audience. Then the hashtags on the last line. Use 1-3 relevant emoji where they add warmth or meaning (for example one at the end of the first line and one on the question), never as labels or bullet markers, and no "📍" lines. Under 1,600 characters.
- carousel_caption: a separate caption for the 7-slide carousel of the same topic, different wording from the Reel caption (not a rephrase of its first line): a short invitation to swipe, one fresh detail, one question, then the same hashtags. Under 800 characters.
- hashtags: 3-5 (Instagram allows at most 5), each starting with "#": specific to the place, era or subject (e.g. "#Cappadocia", "#ByzantineHistory"); no generic engagement tags like "#viral", "#fyp", "#ReelsViral", "#explore".
- story_slides: exactly 3 (hook, revelation, question). Slide 3 asks the poll's question; its body gives context and asks nothing else. Each body is one complete sentence of at most 18 words: it must fit three lines at the Story's text size.
- poll: an either/or question (at most 60 characters) and exactly 2 options (each at most 24 characters) that are its two answers, e.g. "Which took more work?" with "Carving downward" / "Hauling the stone". Never a yes/no question with options that are not yes/no.
- carousel_slides: exactly 7 (title slide, five fact slides, closing call-to-comment slide). Slide 7 MUST ask a humane, universally resonant, thought-provoking question that appeals to all demographics (e.g. human ingenuity, civilizational survival, what modern humans have forgotten, or enduring mysteries) rather than narrow academic trivia, compelling viewers to debate in the comments.
- first_comment: a debate-starting question, with no hashtags (Instagram's 5-hashtag cap covers the whole post).
- sources: the pages you used, with real URLs."""

AI_ORIGINS = ("claude", "gemini-free")  # scripts written by a model (vs curated by hand)
SECOND_CHECK_ATTEMPTS = 3        # the free second check, when unavailable, is tried this many times in all
SECOND_CHECK_RETRY_SECONDS = 60  # a per-minute quota is back by then
_sleep = time.sleep


def _structured(system, prompt, schema, **claude_kwargs):
    """The free writer model when SCRIPT_ENGINE=free, otherwise Claude."""
    if free_llm.enabled():
        return free_llm.structured(system, prompt, schema, role="writer")
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
Titles and on-screen covers are intrigue headlines and must NEVER be hedged with 'Estimated'.
Return exact find/replace edits: "find" must be copied character-for-character from the draft
(a whole sentence or phrase), "replace" is its new text ("" to delete). Fix every place the claim appears
(narration, caption, title, on-screen text, slides, stories, graphics). Never add facts that are not verified.
If you change the narration's last sentence, apply the same edit to closing_sentence.
A LENGTH finding names a word window for the narration. Too short: add one or two sentences made only of
verified facts, placed before the closing sentence ("find" = the sentence before it, "replace" = that sentence
followed by the new ones). Too long: remove or condense sentences until the narration is inside the window.
Keep the closing sentence and every other field as they are."""


class Edit(BaseModel):
    find: str
    replace: str


class Edits(BaseModel):
    edits: List[Edit]


GRAPHICS_SYSTEM = """You plan the on-screen graphics for a finished, fact-checked history Reel script. Use only facts stated in the narration or the research notes; never add new claims. Keep every hedge the narration uses: if it says "about", "up to", "estimated" or gives a range, the graphic must too (put the hedge in the value text, e.g. "about 280 ft", or set approximate=true). Graphics are fact-checked like the narration, and a flat figure where sources disagree is rejected. Leave a field null when the facts don't support it."""

GRAPHICS_PROMPT = """Plan the graphics for this script.

- emphasis_words: 1-3 words or short names copied exactly from the narration that carry the story (a striking number, a name, a place). They get a gold highlight on screen, so choose sparingly.
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


def record_word_pace(seconds_per_word: float) -> None:
    """Keeps the last 20 measured seconds-per-word of drafts (the narrator voice, this topic's words)."""
    try:
        values = json.loads(WORD_PACE_FILE.read_text(encoding="utf-8")) if WORD_PACE_FILE.exists() else []
        values = (values + [round(seconds_per_word, 4)])[-20:]
        from .atomic_files import write_json
        write_json(WORD_PACE_FILE, values)
    except (OSError, ValueError) as e:
        log.debug("Could not record the word pace: %s", e)


def _pace_window() -> Tuple[float, float]:
    """(fastest, slowest) pace of the production narrator against the measurement voice."""
    return audio_engine.pace_range(audio_engine.production_voice_key())


def length_target_note() -> str:
    """The word range that fits, from measured pace (the prompt's fixed 105-115 words ran 53-58 s at the
    narrator's pace for name-heavy topics). Empty until three drafts have been measured."""
    try:
        values = json.loads(WORD_PACE_FILE.read_text(encoding="utf-8")) if WORD_PACE_FILE.exists() else []
    except (OSError, ValueError):
        values = []
    if len(values) < 3:
        return ""
    import statistics
    fast, slow = _pace_window()
    measured = statistics.median(values)
    per_word = measured * slow
    # The minimum for the narrator's fastest take, the maximum for its slowest (they vary for ElevenLabs).
    low, high = int(TARGET_DURATION_MIN / (measured * fast)) + 1, int(TARGET_DURATION_MAX / per_word)
    if low > high:  # takes vary too much for one window: the middle pace
        per_word = measured * (fast + slow) / 2
        low, high = int(TARGET_DURATION_MIN / per_word) + 1, int(TARGET_DURATION_MAX / per_word)
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


def spoken_length_problems(pkg: ScriptPackage) -> List[str]:
    """Measures how long the narration will run when spoken (the narrator voice is deterministic) and
    says how to fix it if the voiceover would be too long or short. Empty when fine, when a slightly faster
    read fits (fit_speed), or when unmeasurable (offline); the check after the real voiceover still guards."""
    seconds = audio_engine.measure_spoken_seconds(pkg.narration_script)
    if seconds is None:
        return []
    words = len(pkg.narration_script.split()) or 1
    record_word_pace(seconds / words)  # measured at the measurement voice; the pace ratio is applied on use
    measured = seconds
    fast, slow = _pace_window()
    longest = HARD_DURATION_CEILING - _MEASURE_MARGIN_SEC
    if measured * slow > longest and measured * fast < TARGET_DURATION_MIN:
        fast = slow = (fast + slow) / 2  # takes vary too much for both ends: judged at the middle pace
    seconds = measured * slow  # the ceiling, for the narrator's slowest take
    per_word = seconds / words
    if seconds > longest and fit_speed(pkg.narration_script, seconds):
        return []  # spoken slightly faster (the voiceover stage applies the same speed)
    if seconds > longest:
        # A word count is followed far better than seconds: name this draft's count and the one to reach,
        # aiming inside the comfortable length (not just under the limit), never below the word minimum.
        # Never clamped up to the prompt's word range: for a slow voice that range itself is too long.
        target = max(WORD_COUNT_FLOOR, min(words - math.ceil((seconds - longest) / per_word) - 2,
                                           int(TARGET_DURATION_MAX / per_word)))
        # A window, not a ceiling: told "at most 95", a writer cut 109 words to 73 (production, 2026-10-23).
        low = max(WORD_COUNT_FLOOR, target - 7)
        return [f"Narration runs {seconds:.1f}s when spoken (limit {longest:g}s). This draft is {words} words; "
                f"rewrite the narration in {low}-{target} words (names and numbers such as dates take "
                f"longer to say), keeping the closing sentence."]
    if measured * fast < TARGET_DURATION_MIN:
        # The minimum, for the narrator's fastest take (production 2026-10-25: 93 words read 41.1 s).
        # A window, as for a long draft: "add about 9 words" to a fresh rewrite meant nothing, and three
        # drafts in a row came out shorter (38.8, 37.3, 35.8 s; production, 2026-10-23).
        seconds = measured * fast
        fast_per_word = seconds / words
        low = words + math.ceil((TARGET_DURATION_MIN - seconds) / fast_per_word) + 2
        high = max(low + 4, min(int(TARGET_DURATION_MAX / per_word), int(longest / per_word) - 2))
        return [f"Narration runs {seconds:.1f}s when spoken (minimum {TARGET_DURATION_MIN:g}s). This draft is "
                f"{words} words; extend the narration to {low}-{high} words with more verified facts from the "
                f"research notes, keeping the closing sentence."]
    return []


def refit_speed(seconds: float, speed: float = 1.0) -> Optional[float]:
    """The speed for one more take when a real voiceover ran over the ceiling, or None (it fits, or even the
    fastest allowed read would not). Aims just inside the ceiling, never past SPEECH_SPEEDUP_MAX."""
    if seconds <= HARD_DURATION_CEILING:
        return None
    target = HARD_DURATION_CEILING - 0.6
    faster = math.ceil(speed * seconds / target * 100) / 100
    return faster if faster <= config.SPEECH_SPEEDUP_MAX else None


_PHRASE_STOPWORDS = {"the", "a", "an", "of", "to", "in", "and", "was", "were", "is", "it", "its", "that", "this", "on", "at", "for", "by", "with", "as"}


def repeated_phrases(text: str, n: int = 3) -> List[str]:
    """Phrases of n+ words said twice in a narration: any repeated opening of a sentence (the hedge "By one
    estimate" twice in a row), and any repeated run of n words that is not only small words. Names and
    numbers repeated on purpose rarely form a three-word run that is repeated exactly."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text or "") if x.strip()]
    found: List[str] = []
    openings: Dict[str, int] = {}
    for sent in sentences:
        words = re.findall(r"[A-Za-z']+", sent.lower())
        if len(words) >= n:
            key = " ".join(words[:n])
            openings[key] = openings.get(key, 0) + 1
    found += [k for k, c in openings.items() if c > 1]
    # Within sentences, not across them; a run holding a name or a number is a deliberate repeat ("the
    # Richat Structure", "Catherine Palace"), not wording, and is left alone.
    seen: Dict[str, int] = {}
    tokens: List[List[str]] = [re.findall(r"[\w']+", sent) for sent in sentences]
    grams = []
    for toks in tokens:
        for i in range(len(toks) - n + 1):
            gram = toks[i:i + n]
            if any(t[:1].isupper() and i + j > 0 for j, t in enumerate(gram)) or any(any(ch.isdigit() for ch in t) for t in gram):
                continue
            grams.append([t.lower() for t in gram])
    for gram in grams:
        if sum(w not in _PHRASE_STOPWORDS for w in gram) < 2:
            continue
        key = " ".join(gram)
        seen[key] = seen.get(key, 0) + 1
    for k, c in seen.items():  # one entry per repeated stretch: overlapping runs of it are the same repeat
        if c > 1 and not any(" ".join(k.split()[:n - 1]) in f or " ".join(k.split()[1:]) in f for f in found):
            found.append(k)
    return found


MAX_TRIMMED_SENTENCES = 2


def _norm_words(text: str) -> str:
    """Lower-case words only; an apostrophe is kept inside a word ("ship's"), not as a quote mark."""
    text = re.sub(r"[^a-z0-9' ]", " ", (text or "").lower().replace("’", "'"))
    return " ".join(re.sub(r"(?<![a-z0-9])'|'(?![a-z0-9])", " ", text).split())


def without_blocked_sentences(payload: Dict[str, Any], notes: str) -> Optional[Dict[str, Any]]:
    """The draft with the narration sentences a checker quoted as blocking taken out, or None when there is
    nothing safe to take out. Used once, after the last rewrite failed on a claim or two (production
    2026-10-25: three Antikythera drafts, the last blocked only on "The bronze lump was pulled specifically
    from the Aegean Sea."). Nothing is written, so nothing new can be claimed; the hook and the closing
    sentence are never removed; and the result must pass every check again (structure, spoken length and
    both fact checkers), so a gap that leaves it too short, or a claim repeated elsewhere, still fails."""
    narration = payload.get("narration_script") or ""
    quoted = f" {_norm_words(notes)} "  # whole words: a sentence must appear in the notes word for word
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", narration.strip()) if x.strip()]
    keep_ends = {_norm_words(payload.get("hook_sentence")), _norm_words(payload.get("closing_sentence"))}
    blocked = [x for i, x in enumerate(sentences)
               if len(_norm_words(x).split()) >= 4 and f" {_norm_words(x)} " in quoted
               and 0 < i < len(sentences) - 1 and _norm_words(x) not in keep_ends]
    if not blocked or len(blocked) > MAX_TRIMMED_SENTENCES:
        return None
    trimmed = " ".join(x for x in sentences if x not in blocked)
    return dict(payload, narration_script=trimmed, trimmed_sentences=blocked)


def duration_problem(seconds: float) -> Optional[str]:
    """Why a measured voiceover can't make a Reel (the master is exactly as long), or None."""
    if seconds < TARGET_DURATION_MIN:
        return f"Voiceover is {seconds:.1f}s; too short for the {TARGET_DURATION_MIN:g}s minimum."
    if seconds > HARD_DURATION_CEILING:
        return (f"Voiceover is {seconds:.1f}s; hard ceiling is {HARD_DURATION_CEILING:g}s. A re-render shortens "
                "the script for this voice's measured pace.")
    return None


YES_NO_START = re.compile(r"^(is|are|was|were|do|does|did|can|could|would|will|should|has|have|had|must|might)\b", re.I)
YES_NO_ANSWERS = {"yes", "no", "maybe", "not sure", "definitely", "no way", "of course", "never", "absolutely"}
POLL_OPTION_MAX = 24


def poll_problems(question: str, options: List[str]) -> List[str]:
    """The poll's options must answer its question: a yes/no question gets yes/no options, otherwise
    the question has to offer the choice ("Which...?", "X or Y?")."""
    problems = []
    if len(options) != 2:
        problems.append(f"The poll needs exactly 2 options, got {len(options)}.")
    if not question.strip() or len(question) > 60:
        problems.append("The poll question must be 1-60 characters.")
    long = [o for o in options if len(o) > POLL_OPTION_MAX]
    if long:
        problems.append(f"Poll options must be at most {POLL_OPTION_MAX} characters: {long}.")
    q = question.strip()
    yes_no = bool(YES_NO_START.match(q)) and not re.search(r"\bor\b", q, re.I)
    if yes_no and not all(o.strip(" .!?").lower().split(",")[0] in YES_NO_ANSWERS for o in options):
        problems.append(f"The poll asks a yes/no question ('{q}') but its options ({' / '.join(options)}) are not yes/no "
                        "answers; ask an either/or question the two options answer, e.g. 'Which took more work?'.")
    return problems


def validate_structure(pkg: ScriptPackage) -> List[str]:
    """Returns a list of problems; empty means the package is structurally publishable."""
    problems = []
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
    if len(pkg.caption) > 2200:
        problems.append(f"Caption is {len(pkg.caption)} characters; Instagram max is 2,200.")
    if not 3 <= len(pkg.hashtags) <= 5 or not all(h.startswith("#") and " " not in h for h in pkg.hashtags):
        problems.append("Hashtags must be 3-5 single-word tags starting with '#' (Instagram allows at most 5).")
    if len(re.findall(r"(?<!\w)#\w", pkg.caption + " " + pkg.first_comment)) > 5:
        problems.append("The caption and first comment carry more than 5 hashtags; Instagram allows at most 5 per post.")
    first_line = next((ln for ln in pkg.caption.splitlines() if ln.strip()), "")
    if len(first_line) > 125:
        problems.append(f"Caption's first line is {len(first_line)} characters; keep it within the 125 shown before 'more'.")
    emoji = len(EMOJI_RE.findall(pkg.caption))
    if emoji > 4:
        problems.append(f"Caption uses {emoji} emoji; 1-3 relevant ones read human, more reads like spam.")
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
                fit = max(8, words * STORY_BODY_LINES // lines - 1)
                problems.append(f"Story slide {i}'s body takes {lines} lines on the frame (at most {STORY_BODY_LINES}); "
                                f"rewrite it in about {fit} words ({words} now), keeping one complete sentence.")
    if len(pkg.carousel_slides) != 7:
        problems.append(f"Expected 7 carousel slides, got {len(pkg.carousel_slides)}.")
    problems.extend(poll_problems(pkg.poll.question, pkg.poll.options))
    public_text = " ".join([
        pkg.title, pkg.narration_script, pkg.caption, pkg.carousel_caption or "", pkg.first_comment,
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
        problems.append("The narration repeats the phrase " + ", ".join(f"'{r}'" for r in repeated)
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


def normalize_copy(pkg: ScriptPackage) -> ScriptPackage:
    """Bookkeeping a draft gets wrong without being wrong, fixed without a rewrite (each was a full
    model call; dev, 2026-10-23): closing_sentence is set to the narration's actual last sentence, and a
    caption first line over 125 characters that holds several sentences is broken after the last
    sentence that fits (same words, new line). A single sentence over the limit still needs a rewrite."""
    update = {}
    sentences = [s for s in _SENTENCE_END.split(pkg.narration_script.strip()) if s.strip()]
    if sentences and not pkg.narration_script.strip().endswith(pkg.closing_sentence.strip()):
        update["closing_sentence"] = sentences[-1].strip()
    lines = pkg.caption.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is not None and len(lines[first]) > 125:
        parts = [s for s in _SENTENCE_END.split(lines[first].strip()) if s]
        keep = []
        for s in parts:
            if len(" ".join(keep + [s])) > 125:
                break
            keep.append(s)
        if keep and len(keep) < len(parts):
            lines[first:first + 1] = [" ".join(keep), " ".join(parts[len(keep):])]
            update["caption"] = "\n".join(lines)
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
                    problems = validate_structure(pkg) or spoken_length_problems(pkg)
                    if not problems:
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
            for attempt in range(1, max_attempts + 1):
                self._report(f"Writing draft {attempt} of {max_attempts}")
                try:
                    pkg = self._generate_with_claude(topic, feedback)
                except LLMError as e:
                    log.error("Script generation failed: %s", e)
                    writer_error = str(e)
                    break
                pkg = prune_graphics(pkg)  # an invalid graphic is dropped, not allowed to sink the script
                problems = validate_structure(pkg) or spoken_length_problems(pkg)
                if not problems:
                    return self._finalize(pkg, topic, origin=writer_origin(), fact_sheet_used=self._sheet(topic) is not None)
                log.warning("Attempt %d failed structural checks: %s", attempt, problems)
                last_problems = problems
                improve.note_problems(problems, "structure")
                if all("when spoken" in p for p in problems):
                    # Only the length is off: the draft is edited to the window (one short call), not written
                    # again from scratch (a rewrite took 2 min 46 s and came out at the writer's usual length
                    # again; production, 2026-10-24).
                    fitted = self._fit_length_by_editing(pkg, problems, topic, feedback)
                    if fitted is not None:
                        return self._finalize(fitted, topic, origin=writer_origin(), fact_sheet_used=True)
                found = re.search(r"(?:in|to) (\d+)-(\d+) words", " ".join(problems))
                window = (int(found.group(1)), int(found.group(2))) if found else window
                # A too-short draft after a too-long one is reminded of the window, so it does not swing back.
                keep = (f" Keep the narration between {window[0]} and {window[1]} words: longer runs past the "
                        "time limit when spoken." if window and not found else "")
                # A length problem is fixed on this draft, not by starting over: each fresh rewrite came out at
                # the writer's usual length again (2026-10-23). The facts it has were already checked.
                length_only = all("when spoken" in p for p in problems)
                carry = (f"\n\n<previous_narration>\n{pkg.narration_script}\n</previous_narration>\nAdjust this "
                         "narration to the length above, adding or removing sentences using only facts in the "
                         "research notes; keep the rest of it, and the other fields, as they are."
                         if length_only and pkg.narration_script else "")
                feedback = (base_feedback + "\n\nA previous draft was rejected for: " + " ".join(problems) + keep
                            + " Fix these." + carry)
                self._report(f"Draft {attempt} of {max_attempts} rejected ({_short_reason(problems)})"
                             + ("; rewriting" if attempt < max_attempts else ""))
            log.warning("The script writer (%s) did not produce a usable script; trying a curated script.", writer_origin())

        curated = self._load_curated(topic["id"])
        if curated:
            pkg, reviewed = curated
            log.info("Using curated script for '%s' (human reviewed: %s).", topic["id"], reviewed)
            payload = self._finalize(pkg, topic, origin="curated")
            payload["human_reviewed"] = reviewed
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
            f"<draft>\n{json.dumps(draft, ensure_ascii=False, indent=1)}\n</draft>\n\n<findings>\n{findings}\n</findings>",
            Edits, effort="medium", max_tokens=6000)
        text = json.dumps(draft, ensure_ascii=False)
        for e in result.edits:
            find_str = e.find.strip()
            replace_str = e.replace.strip()
            if not find_str:
                continue
            old = json.dumps(find_str, ensure_ascii=False)[1:-1]
            new = json.dumps(replace_str, ensure_ascii=False)[1:-1]
            if old in text:
                text = text.replace(old, new)
            elif find_str in text:
                text = text.replace(find_str, replace_str)
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
                    # Resilient fallback: locate sentence by normalized core (ignoring quotation/punctuation diffs)
                    core = re.sub(r"[^\w\s]", "", find_str).strip()
                    if len(core) >= 15:
                        for k, v in draft.items():
                            if isinstance(v, str) and core in re.sub(r"[^\w\s]", "", v):
                                sentences = re.split(r"(?<=[.!?])\s+", v)
                                new_sentences = []
                                for s in sentences:
                                    if core in re.sub(r"[^\w\s]", "", s):
                                        if replace_str:
                                            new_sentences.append(replace_str)
                                        found = True
                                    else:
                                        new_sentences.append(s)
                                draft[k] = " ".join(new_sentences)
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
        for claim in re.findall(r"'([^']{25,})'\s*\(", findings):
            if plain(claim) in revised:
                log.warning("Surgical revision kept a rejected claim (%r); rewriting instead.", claim[:60])
                return None
        try:
            return ScriptPackage.model_validate(json.loads(text))
        except (ValueError, ValidationError) as e:
            log.warning("Surgical edits broke the draft (%s); rewriting instead.", e)
            return None

    def _fit_length_by_editing(self, pkg: ScriptPackage, problems: List[str], topic: Dict[str, Any],
                               feedback: str) -> Optional[ScriptPackage]:
        """A draft whose only fault is its spoken length, edited to the word window with verified facts
        (the surgical reviser), then given its graphics. None when there is no fact sheet, the edit fails
        or the result still misses: the caller rewrites as before."""
        sheet = self._sheet(topic)
        if sheet is None:
            return None
        try:
            fitted = self._revise_surgically(pkg.model_dump(), "LENGTH: " + " ".join(problems), sheet)
        except LLMError as e:
            log.warning("Length edit failed (%s); rewriting the draft.", e)
            return None
        if fitted is None:
            return None
        fitted = prune_graphics(fitted)
        still = validate_structure(fitted) or spoken_length_problems(fitted)
        if still:
            log.warning("Length edit still misses (%s); rewriting the draft.", still)
            return None
        log.info("Length fixed by editing the draft: %d -> %d words.",
                 len(pkg.narration_script.split()), len(fitted.narration_script.split()))
        notes, retrieved = self._sheet_context(sheet)
        return self._with_graphics(fitted, notes, retrieved, feedback)

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
                + GRAPHICS_PROMPT + feedback, GraphicsPlan, effort="medium")  # revision notes cover graphics too
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
                USER_PROMPT_TEMPLATE.format(**fields)
                + f"\n\n<verified_facts>\n{fact_sheet.as_prompt(sheet)}\n</verified_facts>"
                + raw_evidence_str
                + f"\n<retrieved_sources>\n{format_sources(retrieved)}\n</retrieved_sources>\n"
                + FACTS_ONLY_RULES + improve.learned_rules() + feedback
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
        core = _structured(SYSTEM_PROMPT, prompt + length_target_note(), ScriptCore, effort="high")
        if spoken_length_problems(ScriptPackage(**core.model_dump())):
            # This draft will be sent back for its length: graphics are planned only for one that can be kept.
            return ScriptPackage(**core.model_dump())
        return self._with_graphics(core, notes, retrieved, feedback)

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
        payload.update({
            "fact_sheet": fact_sheet_used,  # written from the topic's verified fact sheet: trace-checked
            "topic_id": topic["id"],
            "origin": origin,
            "word_count": len(pkg.narration_script.split()),
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
                passed, confidence, audits, msg = False, 0.0, [], "No verified fact sheet; the free engine fails closed."
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
        check, dev 2026-10-23). Still fails closed when it stays unavailable."""
        if sheet is None:
            return "fail", 0.0, [], "No fact sheet: the free engine can't check this script."
        for attempt in range(1, SECOND_CHECK_ATTEMPTS + 1):
            result = trace_check.evidence_check(payload, sheet)
            if result[0] != "unavailable" or attempt == SECOND_CHECK_ATTEMPTS:
                return result
            log.info("Second fact check unavailable (%s); trying again in %d s (%d of %d).",
                     result[3], SECOND_CHECK_RETRY_SECONDS, attempt, SECOND_CHECK_ATTEMPTS)
            self._report(f"Second fact check unavailable; trying again in {SECOND_CHECK_RETRY_SECONDS} s")
            _sleep(SECOND_CHECK_RETRY_SECONDS)
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
