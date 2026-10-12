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

import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple

from pydantic import ValidationError

from .config import CURATED_SCRIPTS_DIR, SECOND_CHECK_ENABLED, TARGET_WORD_COUNT_MIN, TARGET_WORD_COUNT_MAX
from .fact_checker import ClaimFactChecker
from .llm import LLMError, claude_available, format_sources, research, structured_call
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
- caption: line 1 hook teaser; then a 3-sentence micro-documentary; then an open-ended question; then "📍 <location>"; then the hashtags. Under 2,000 characters.
- hashtags: exactly 7, each starting with "#".
- story_slides: exactly 3 (hook, revelation, question).
- poll: one question with exactly 2 short options (posted manually as a native Story poll sticker).
- carousel_slides: exactly 7 (title slide, five fact slides, closing call-to-comment slide).
- first_comment: a debate-starting question.
- sources: the pages you used, with real URLs."""

BANNED_ENDINGS = {"when", "until", "because", "and", "or", "so", "that", "while", "as", "but", "if", "the", "a", "of"}
BANNED_TROPES = [
    "alien", "aliens", "extraterrestrial", "illuminati", "reptilian", "magic portal",
    "time travel portal", "atlantis technology", "levitation ray", "levitation rays",
]


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
    if len(pkg.title) > 60:
        problems.append(f"Title is {len(pkg.title)} characters; max 60.")
    if len(pkg.caption) > 2200:
        problems.append(f"Caption is {len(pkg.caption)} characters; Instagram max is 2,200.")
    if not 3 <= len(pkg.hashtags) <= 30 or not all(h.startswith("#") and " " not in h for h in pkg.hashtags):
        problems.append("Hashtags must be 3-30 single-word tags starting with '#'.")
    if len(pkg.story_slides) != 3:
        problems.append(f"Expected 3 story slides, got {len(pkg.story_slides)}.")
    if len(pkg.carousel_slides) != 7:
        problems.append(f"Expected 7 carousel slides, got {len(pkg.carousel_slides)}.")
    if not 2 <= len(pkg.poll.options) <= 4:
        problems.append("Poll needs 2-4 options.")
    public_text = " ".join([
        pkg.title, pkg.narration_script, pkg.caption, pkg.first_comment,
        *(s.line1 + " " + s.line2 + " " + s.body for s in pkg.story_slides),
        *(c.heading + " " + c.body for c in pkg.carousel_slides),
    ])
    for trope in find_banned_tropes(public_text):
        problems.append(f"Contains pseudo-archaeology trope '{trope}'.")
    return problems


class ScriptEngine:
    def __init__(self, fact_checker: Optional[ClaimFactChecker] = None):
        self.fact_checker = fact_checker or ClaimFactChecker()
        self._research_cache: Dict[str, Tuple[str, list]] = {}  # topic_id -> (notes, retrieved sources)

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
