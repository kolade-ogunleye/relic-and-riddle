"""
src/script_engine.py
====================
Produces the complete script package for one Reel:
1. 4-beat, ~42-second circular narration (hook -> scale -> enigma -> loop-back insight).
2. All on-screen copy (thumbnail, 3 story frames, poll, 7 carousel slides) and Meta metadata.
3. Stage 2.5 guardrail: structural checks + search-grounded claim verification.

Source of scripts, in order:
  a) Claude, researching the topic with web search and returning schema-validated JSON.
  b) A human-curated script in data/scripts/<topic_id>.json.
There is deliberately no template fallback: a topic with neither fails the run.
"""

import difflib
import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple

from pydantic import ValidationError

from .config import CURATED_SCRIPTS_DIR, SECOND_CHECK_ENABLED, TARGET_WORD_COUNT_MIN, TARGET_WORD_COUNT_MAX
from .fact_checker import ClaimFactChecker
from .llm import LLMError, claude_available, format_sources, research, structured_call, task as llm_task
from .models import ScriptPackage

log = logging.getLogger(__name__)


class ScriptUnavailableError(RuntimeError):
    pass


SYSTEM_PROMPT = """You are a documentary scriptwriter producing short vertical Reels (1080x1920) for Facebook and Instagram in the "Historical Curiosities & Ancient Marvels" niche. Accuracy is the brand: viewers and Meta's integrity systems punish fabricated or exaggerated history, so every factual statement you write must be something you have confirmed with web search.

Editorial rules:
- Narration is 95-112 spoken words (about 40-45 seconds at 145 wpm). Hard maximum 115.
- Four beats: (1) a concrete, sensory hook in the first sentence - never "Hey guys" or "Have you ever wondered"; (2) verifiable scale and engineering facts; (3) the genuine open question or historical context; (4) a closing sentence that is grammatically complete on its own and leads naturally back into the hook, so the video loops.
- Never end on a trailing word such as "when", "until", "because", "and".
- Use numbers only when sources support them. Where sources disagree, say "about", "up to", "estimated", or name the range. Do not call something unexplained if historians have a well-supported explanation; present real debates as debates.
- No conspiracy or pseudo-archaeology framing (aliens, lost super-civilizations, etc.).
- Topic notes supplied by the user may contain errors or exaggerations. Check them; do not repeat them unverified.
- On-screen copy is short and uppercase-friendly: thumbnail lines at most 22 characters, story and carousel headings at most 26 characters, bodies at most 90 characters.
- Audience: primarily the United States, then the UK, Canada and Australia. Use American English spelling, and give measurements in US units first with metric in parentheses, e.g. "about 280 feet (85 m)", "half a ton (about 500 kg)".
- The narration must add facts, context and interpretation a viewer could not get from the pictures alone (Meta treats voiceover that merely describes what is on screen as unoriginal). Never narrate the visuals ("here we see", "as you can see", "this photo shows").
- Write like a sharp human documentary narrator: concrete nouns, active verbs, specific numbers and names. Avoid generic AI phrasing such as "delve", "testament to", "tapestry", "unveil", "unravel", "shrouded in mystery", "realm", "embark", "intricate", "a glimpse into", "stands as", "it's important to note".
- Return the sources you relied on."""

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
- caption: written like a magazine editor, not a template. First line: a curiosity-driven line of at most 125 characters (the part shown before "more") that is NOT the narration's hook sentence. Then 2-3 short sentences that add context the video doesn't say (a detail, a consequence, a name), with the place worked naturally into the prose. Then one genuine question to the audience. Then the hashtags on the last line. Use 1-3 relevant emoji where they add warmth or meaning (for example one at the end of the first line and one on the question), never as labels or bullet markers, and no "📍" lines. Under 1,600 characters.
- carousel_caption: a separate caption for the 7-slide carousel of the same topic, different wording from the Reel caption (not a rephrase of its first line): a short invitation to swipe, one fresh detail, one question, then the same hashtags. Under 800 characters.
- hashtags: 3-5 (Instagram allows at most 5), each starting with "#": specific to the place, era or subject (e.g. "#Cappadocia", "#ByzantineHistory"); no generic engagement tags like "#viral", "#fyp", "#ReelsViral", "#explore".
- emphasis_words: 1-3 words or short names copied exactly from the narration that carry the story (a striking number, a name, a place). They get a gold highlight on screen, so choose sparingly.
- timeline: 2-4 dated events from the story, oldest first, only dates the research supports (approximate=true for estimates; negative years for BC; labels at most 40 characters). Omit (null) if the story has fewer than two solid dates.
- scale_fact: one striking, well-sourced size (depth, height or length) of the subject, for an on-screen comparison with a famous landmark; null if no reliable figure.
- stat_cards: 1-2 headline figures from the research (value as it should appear, US units first, plus a short label); null if none is striking and solid.
- cross_section: only for underground or vertical structures where the research gives the total depth and the depth of 2-5 levels or features; otherwise null.
- route: only when the story follows a journey or movement between real places (2-5 stops, in order, each with a short label); otherwise null.
- photo_labels: 2-4 physical features named in the narration that a photo of the site could show (e.g. "ventilation shaft", "rolling stone door"); null if none.
- story_slides: exactly 3 (hook, revelation, question).
- poll: one question with exactly 2 short options (posted manually as a native Story poll sticker).
- carousel_slides: exactly 7 (title slide, five fact slides, closing call-to-comment slide).
- first_comment: a debate-starting question, with no hashtags (Instagram's 5-hashtag cap covers the whole post).
- sources: the pages you used, with real URLs."""

BANNED_ENDINGS = {"when", "until", "because", "and", "or", "so", "that", "while", "as", "but", "if", "the", "a", "of"}
BANNED_TROPES = [
    "alien", "aliens", "extraterrestrial", "illuminati", "reptilian", "magic portal",
    "time travel portal", "atlantis technology", "levitation ray", "levitation rays",
]
# Phrasing that reads as machine-written; a draft containing it is sent back for a rewrite.
AI_PHRASES = [
    "here we see", "as you can see", "this photo shows", "in this image", "in this video",
    "delve", "delves", "delving", "testament to", "tapestry", "unveil", "unveils", "unravel", "unravels",
    "shrouded in mystery", "realm of", "embark", "a glimpse into", "stands as a", "it's important to note",
    "in the annals of", "intricate dance", "beacon of",
]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")  # pictographs, symbols, dingbats
GENERIC_HASHTAGS = {"#viral", "#fyp", "#foryou", "#foryoupage", "#reelsviral", "#explore", "#explorepage", "#trending",
                    "#reels", "#reelsinstagram", "#instagood"}


def _similar(a: str, b: str) -> float:
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() if a.strip() and b.strip() else 0.0


def find_banned_tropes(text: str) -> List[str]:
    return [t for t in BANNED_TROPES if re.search(r"\b" + re.escape(t) + r"\b", text, re.IGNORECASE)]


def validate_structure(pkg: ScriptPackage) -> List[str]:
    """Returns a list of problems; empty means the package is structurally publishable."""
    problems = []
    words = pkg.narration_script.split()
    if not TARGET_WORD_COUNT_MIN <= len(words) <= TARGET_WORD_COUNT_MAX:
        problems.append(f"Narration is {len(words)} words; must be {TARGET_WORD_COUNT_MIN}-{TARGET_WORD_COUNT_MAX}.")
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
    if len(pkg.carousel_slides) != 7:
        problems.append(f"Expected 7 carousel slides, got {len(pkg.carousel_slides)}.")
    if not 2 <= len(pkg.poll.options) <= 4:
        problems.append("Poll needs 2-4 options.")
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
        if len(card.label) > 40 or len(card.value) > 14:
            problems.append("stat_cards need a value of at most 14 and a label of at most 40 characters.")
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


class ScriptEngine:
    def __init__(self, fact_checker: Optional[ClaimFactChecker] = None):
        self.fact_checker = fact_checker or ClaimFactChecker()
        self._research_cache: Dict[str, Tuple[str, list]] = {}  # topic_id -> (notes, retrieved sources)

    @llm_task("script_writing")
    def generate_script(self, topic: Dict[str, Any], max_attempts: int = 2, revision_notes: str = "",
                        previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        revision_notes / previous: fact-checker findings on the previous draft. The previous
        draft is edited (not rewritten) so corrections don't introduce new, unchecked claims.
        """
        if claude_available():
            base_feedback = ""
            if revision_notes and previous:
                draft = {k: previous.get(k) for k in ScriptPackage.model_fields}
                base_feedback = (
                    "\n\n<previous_draft>\n" + json.dumps(draft, ensure_ascii=False, indent=1) + "\n</previous_draft>\n"
                    "This draft failed fact-checking. Revise it, changing only what the findings below require: "
                    "hedge or drop disputed and weakly sourced figures, correct false ones, and add no new factual "
                    "claims. Keep everything else as it is.\n<fact_check_findings>\n" + revision_notes + "\n</fact_check_findings>"
                )
            feedback = base_feedback
            for attempt in range(1, max_attempts + 1):
                try:
                    pkg = self._generate_with_claude(topic, feedback)
                except LLMError as e:
                    log.error("Claude script generation failed: %s", e)
                    break
                problems = validate_structure(pkg)
                if not problems:
                    return self._finalize(pkg, topic, origin="claude")
                log.warning("Attempt %d failed structural checks: %s", attempt, problems)
                feedback = base_feedback + "\n\nA previous draft was rejected for: " + " ".join(problems) + " Fix these."
            log.warning("Claude did not produce a usable script; trying curated script.")

        curated = self._load_curated(topic["id"])
        if curated:
            pkg, reviewed = curated
            log.info("Using curated script for '%s' (human reviewed: %s).", topic["id"], reviewed)
            payload = self._finalize(pkg, topic, origin="curated")
            payload["human_reviewed"] = reviewed
            return payload

        raise ScriptUnavailableError(
            f"No script source for topic '{topic['id']}': configure ANTHROPIC_API_KEY "
            f"or add a curated script at data/scripts/{topic['id']}.json."
        )

    def _generate_with_claude(self, topic: Dict[str, Any], feedback: str) -> ScriptPackage:
        loc = topic.get("location", {})
        fields = dict(
            title=topic.get("title", ""),
            sub_theme=topic.get("sub_theme", ""),
            hook=topic.get("hook", ""),
            location=", ".join(v for v in (loc.get("name"), loc.get("city"), loc.get("country")) if v),
            key_facts="; ".join(topic.get("key_facts", [])),
        )
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
        pkg = structured_call(SYSTEM_PROMPT, prompt, ScriptPackage, effort="high")
        retrieved_urls = {s.url for s in retrieved}
        pkg.sources = [s for s in pkg.sources if s.url in retrieved_urls] or retrieved
        return pkg

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
    def _finalize(pkg: ScriptPackage, topic: Dict[str, Any], origin: str) -> Dict[str, Any]:
        payload = pkg.model_dump()
        payload.update({
            "topic_id": topic["id"],
            "origin": origin,
            "word_count": len(pkg.narration_script.split()),
            "location": topic.get("location", {}),
            "meta_topic_categories": topic.get("meta_categories", []),
        })
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

        problems = validate_structure(pkg)
        if problems:
            return False, script_payload, "REJECTED: " + " ".join(problems)

        passed, confidence, audits, msg = self.fact_checker.verify(script_payload, topic, allow_unreviewed_curated)
        script_payload["fact_audits"] = audits
        script_payload["consensus_confidence"] = confidence
        if not passed:
            return False, script_payload, f"REJECTED by fact checker: {msg}"

        # Independent second check (only for Claude-written scripts that passed the first).
        if SECOND_CHECK_ENABLED and script_payload.get("origin") == "claude":
            status, conf2, audits2, msg2 = self.fact_checker.second_opinion(script_payload)
            script_payload["second_check"] = {"status": status, "confidence": conf2, "audits": audits2, "summary": msg2}
            if status == "fail":
                return False, script_payload, f"REJECTED by second fact checker: {msg2}"
            msg = f"{msg} | 2nd check: {msg2}"
        return True, script_payload, f"Structure OK ({pkg.narration_script.count(' ') + 1} words) | {msg}"
