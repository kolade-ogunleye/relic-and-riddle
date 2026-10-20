"""
Candidate rewrites of the script writer's prompts, compared against the production ones by
scripts/eval_prompts.py. A variant is adopted only if it wins on the measured scores without a
regression; until then production keeps its prompts.

"general": the case-specific patches (Etruscan treaties, Pompeii/Privernum samples, named
studies) become general principles; shouted "CRITICAL / HARD BAN" phrasing becomes plain rules
(current models follow plain instructions and over-apply shouted ones); the system prompt no
longer says facts were "confirmed with web search", which contradicts the facts-only free engine.
"""

from src import script_engine

GENERAL_SYSTEM = script_engine.SYSTEM_PROMPT.replace(
    "so every factual statement you write must be something you have confirmed with web search.",
    "so every factual statement you write must come from the verified facts or research you are given.")

GENERAL_FACTS_ONLY = """Write from the verified facts only. Every factual statement in the narration, caption, title,
on-screen text, slides, stories and first comment must come from a listed fact. For a disputed fact, keep its hedge
and phrase it naturally (for example "physicists calculate that...", "models suggest roughly...", "ranging between X and Y").
Never begin a sentence with a bare hedge word such as "Estimated," or "Approximately,"; work the hedge into the sentence.
Do not add details, names, causes, numbers or dates that are not in a fact, and do not derive new figures (no
subtracting dates, no rounding that changes a figure).
State the facts directly. Leave out word etymologies, and name individual researchers or study dates only when the
fact itself is about who proposed or found something.
Keep every sentence on the topic's own subject. Do not drift into the surrounding region's unrelated history (other
eras, rulers, treaties, town details) unless a fact ties it to the subject.
Keep the topic's own place at the centre. When evidence comes from a different site or sample, present it as evidence
about this topic's subject (for example "samples from another site of the same period show..."), never as a separate story.
Do not claim that something is unknown, absent or impossible unless a fact says exactly that.
Do not make claims about modern builders, technology or practice unless a fact states them; a contrast with today
can be posed as a question.
When a fact reports a study or analysis, say only what the fact says; do not extend it to mechanisms or implications.
Use at most 8 distinct facts and word each the same way wherever it appears. Questions and framing are fine if they
assert nothing new.
List sources only from retrieved_sources."""

VARIANTS = {
    "current": (script_engine.SYSTEM_PROMPT, script_engine.FACTS_ONLY_RULES),
    "general": (GENERAL_SYSTEM, GENERAL_FACTS_ONLY),
}
