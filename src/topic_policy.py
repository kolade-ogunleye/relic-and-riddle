"""The topic bank's editorial policy (QF-73): sourced intake, hook and fact readiness, region balance, holds, and
spacing between editorially sensitive topics.

- Sources: every topic cites at least one public HTTPS page (sources_valid; syntax and host only, never fetched
  here: the fact gate still retrieves and checks every fact). A proposal or a Telegram Add without one is refused.
- Hook and facts: a topic's suggested hook is a writer seed. It is kept only when it passes QF-14's rule
  (script_engine.hook_problems, the same function the writer's opening is held to); a topic needs one concrete fact
  (a number, a scale or a contrast: script_engine.hook_signal's categories, no second word list) among its key facts
  so a compliant opening can be written. A missing concrete fact is said, never a block on intake
  (decisions/QF-73-ingest-hook-check.md).
- Sensitivity: a topic's "sensitivity" list gives the reasons for care (deaths, human remains, war, living sacred
  practice, animal harm); an empty list is an assessment of none. A sensitive topic is never published within
  config.SENSITIVE_TOPIC_GAP_DAYS audience-calendar days (config.POST_TIMEZONE) of another sensitive topic, counting
  every surface published (Reel, Stories, carousel), publications in flight or unconfirmed, and booked Reels. A
  waiting carousel is checked at its slot and waits for a later one (main._post_carousel_if_due). The check runs
  inside the caller's SQLite transaction (db.claim_publication, add/update_scheduled_post), so two runs cannot both
  pass it.
- Hold: a topic with a non-empty "hold" reason is not made until the reason is cleared from the bank.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import config, url_guard

EDITORIAL_KEYS = ("sources", "sensitivity", "region_group", "hold")
UNASSESSED = "Editorial sensitivity assessment pending"   # a topic nobody has assessed is treated as sensitive
UNCLASSIFIED = "unclassified"
VIRAL_TAG = "#reelsviral"
UNCONFIRMED = ("Another sensitive topic has a publication in flight or unconfirmed; reconcile it before a sensitive "
               "topic is published")
TOO_CLOSE = "Sensitive topics need a clear audience-calendar day between them"


class SensitiveSpacingError(ValueError):
    """No slot can be given until another sensitive topic's publication is reconciled."""


def editorial(topic: dict[str, Any]) -> dict[str, Any]:
    """The editorial fields the bank entry has, as the database stores them (db._seed_topics_from_json)."""
    return {key: topic[key] for key in EDITORIAL_KEYS if key in topic}


def sources_valid(sources: Any) -> bool:
    """At least one source with a public HTTPS URL (url_guard.checked without resolving: no network here)."""
    return isinstance(sources, list) and any(
        isinstance(source, dict) and isinstance(source.get("url"), str)
        and url_guard.checked(source["url"], lambda _host: True, resolve=False)
        for source in sources)


def concrete_signal(fact: str) -> str | None:
    """The concrete signal ("number", "scale" or "contrast") anywhere in a fact, or None: QF-14's hook_signal over
    each run of words, so the categories are the writer rule's own."""
    from .script_engine import _spoken_words, hook_signal
    words = _spoken_words(str(fact or "").replace("?", ""))
    for start in range(len(words)):
        signal = hook_signal(" ".join(words[start:start + config.HOOK_SIGNAL_WITHIN]))
        if signal in ("number", "scale", "contrast"):
            return signal
    return None


def has_concrete_fact(topic: dict[str, Any]) -> bool:
    return any(concrete_signal(fact) for fact in topic.get("key_facts") or [])


def hook_passes(hook: str) -> bool:
    from .script_engine import hook_problems
    return bool((hook or "").strip()) and not hook_problems(hook)


def readiness(topic: dict[str, Any]) -> dict[str, Any]:
    """{"hook_passes", "concrete_fact", "note"}: what a proposal card or an Add reply says before the topic is made;
    the note is "" when both hold."""
    hook_ok, fact_ok = hook_passes(topic.get("hook", "")), has_concrete_fact(topic)
    notes = []
    if not hook_ok:
        notes.append("its suggested hook does not pass the opening rule, so the writer gets none")
    if not fact_ok:
        notes.append("no concrete fact (a number, a scale or a contrast) yet: its first run relies on research to "
                     "find one")
    return {"hook_passes": hook_ok, "concrete_fact": fact_ok, "note": "; ".join(notes)}


# A hedge a fact gives its number ("nearly 80 hectares", "some 400 km2") or its claim ("reportedly", "is disputed"):
# a hook drawn from that fact keeps one, so a writer seed never states more than the fact it came from.
_NUMBER_HEDGE = (r"nearly|almost|some|about|around|approximately|roughly|circa|c\.|over|more than|up to|at least|"
                 r"under|fewer than|less than|an estimated|estimated|perhaps|possibly|probably")
_HEDGED_NUMBER = re.compile(rf"\b(?:{_NUMBER_HEDGE})\s+[$£€]?(\d[\d,.]*\d|\d)", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,.]*\d|\d")
_CLAIM_HEDGE = re.compile(r"\b(?:reportedly|allegedly|said to|claimed|claims|disputed|believed|thought to|"
                          r"may have|might have|according to|legend)\b", re.IGNORECASE)
_SHARED_MIN = 2   # content words a hook shares with a claim-hedged fact before it is taken as drawn from it


_FUNCTION_WORDS = frozenset((
    "the", "and", "for", "was", "were", "are", "its", "but", "not", "has", "had", "have", "did", "does", "why", "how",
    "who", "what", "when", "where", "which", "that", "this", "with", "from", "into", "their", "they", "them", "there",
    "than", "then"))


def _content_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower())) - _FUNCTION_WORDS


def hook_hedge_problems(topic: dict[str, Any]) -> list[str]:
    """Where the topic's hook drops a hedge its key facts keep (QF-73 review 3): a number every fact giving it
    hedges ("nearly 80") stated bare, or a claim a fact reports ("reportedly implanted in a cat") stated as fact."""
    hook, facts = str(topic.get("hook") or ""), [str(f) for f in topic.get("key_facts") or []]
    problems = []
    hook_hedged = {n.replace(",", "") for n in _HEDGED_NUMBER.findall(hook)}
    for number in sorted({n.replace(",", "") for n in _NUMBER.findall(hook)} - hook_hedged):
        giving = [f for f in facts if number in {n.replace(",", "") for n in _NUMBER.findall(f)}]
        if giving and all(number in {n.replace(",", "") for n in _HEDGED_NUMBER.findall(f)} for f in giving):
            problems.append(f"the hook states {number} without the hedge its key fact gives it")
    if not _CLAIM_HEDGE.search(hook):
        for fact in facts:
            if _CLAIM_HEDGE.search(fact) and len(_content_words(hook) & _content_words(fact)) >= _SHARED_MIN:
                problems.append(f"the hook states as fact what a key fact reports: '{fact[:80]}'")
    return problems


def intake_problems(topic: dict[str, Any]) -> list[str]:
    """Why a topic cannot join the bank (a proposal's Add): it cites no public source."""
    return [] if sources_valid(topic.get("sources")) else ["at least one public HTTPS source is required"]


def lint_bank(bank: dict[str, Any]) -> list[str]:
    """Every problem of the committed bank (scripts/lint_topic_bank.py, CI): a topic without a source, a hook that
    fails QF-14's rule or drops a hedge its key facts keep, no concrete fact, no region group or sensitivity
    assessment, the viral hashtag, a duplicate id, a region group above config.TOPIC_REGION_MAX_SHARE, or a wrong
    total."""
    from .script_engine import hook_problems
    topics = bank.get("topics") or []
    problems: list[str] = []
    regions: Counter = Counter()
    seen: set = set()
    for topic in topics:
        tid = topic.get("id") or "missing-id"
        if tid in seen:
            problems.append(f"{tid}: duplicate id")
        seen.add(tid)
        problems += [f"{tid}: {p}" for p in intake_problems(topic)]
        problems += [f"{tid}: {p}" for p in hook_problems(topic.get("hook") or "")]
        problems += [f"{tid}: {p}" for p in hook_hedge_problems(topic)]
        if not has_concrete_fact(topic):
            problems.append(f"{tid}: no key fact carries a number, a scale or a contrast")
        region = topic.get("region_group")
        if not isinstance(region, str) or not region.strip() or region == UNCLASSIFIED:
            problems.append(f"{tid}: region_group is required")
        else:
            regions[region] += 1
        sensitivity = topic.get("sensitivity")
        if not isinstance(sensitivity, list) or not all(isinstance(s, str) and s.strip() for s in sensitivity) \
                or UNASSESSED in sensitivity:
            problems.append(f"{tid}: sensitivity must be an assessed list of reasons ([] for none)")
        if not isinstance(topic.get("hold", ""), str):
            problems.append(f"{tid}: hold must be a reason")
        if any(str(tag).casefold() == VIRAL_TAG for tag in topic.get("hashtags") or []):
            problems.append(f"{tid}: remove the #ReelsViral hashtag")
    for region, count in sorted(regions.items()):
        if count > len(topics) * config.TOPIC_REGION_MAX_SHARE:
            problems.append(f"{region}: {count} of {len(topics)} topics exceeds "
                            f"{config.TOPIC_REGION_MAX_SHARE:.0%} of the bank")
    if not topics or bank.get("total") != len(topics):
        problems.append("topic total is missing or inconsistent")
    return problems


def sensitivity_note(topic: dict[str, Any]) -> str:
    """The review card's chip: "Sensitive topic · <reasons>", or "" for a topic assessed as not sensitive."""
    reasons = [str(r) for r in topic.get("sensitivity") or []]
    return "Sensitive topic · " + "; ".join(reasons) if reasons else ""


def _day(value: Any) -> date:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        raise ValueError("Sensitive-topic spacing needs a time with its timezone")
    from zoneinfo import ZoneInfo
    return moment.astimezone(ZoneInfo(config.POST_TIMEZONE)).date()


def _sensitive(editorial_json: str | None) -> bool:
    return bool(json.loads(editorial_json or "{}").get("sensitivity"))


def spacing_problem(conn: Any, topic_id: str, at: Any = None, *, run_id: str = "") -> str:
    """Why the topic cannot be published on `at`'s audience day (now by default): "" unless it is sensitive and
    another sensitive topic is published, publishing, unconfirmed or booked within SENSITIVE_TOPIC_GAP_DAYS of it.
    Runs of the same topic (a regeneration, a documentary) never space each other. Uses the caller's connection."""
    row = conn.execute("SELECT editorial_json FROM topics WHERE id = ?", (topic_id,)).fetchone()
    if not row or not _sensitive(row[0]):
        return ""
    day, gap = _day(at or datetime.now(UTC)), config.SENSITIVE_TOPIC_GAP_DAYS
    # A publication counts once any platform published it, even when its run later failed.
    for other in conn.execute(
            "SELECT r.run_id, r.status, r.updated_at, t.editorial_json FROM runs r JOIN topics t ON t.id = r.topic_id "
            "WHERE r.run_id <> ? AND r.topic_id <> ? AND (r.status IN ('published', 'publishing', 'publish_unknown') "
            "OR EXISTS (SELECT 1 FROM publications p WHERE p.run_id = r.run_id AND p.status = 'published'))",
            (run_id, topic_id)).fetchall():
        if not _sensitive(other[3]):
            continue
        if other[1] in ("publishing", "publish_unknown"):
            return UNCONFIRMED
        # Every surface the audience saw it on: the Reel, its Stories and its carousel each record a publication
        # (an uncertain one counts on the day it was tried); a run with none yet counts on its last update.
        shown = [r[0] for r in conn.execute("SELECT created_at FROM publications WHERE run_id = ? "
                                            "AND status IN ('published', 'unknown')", (other[0],)).fetchall()]
        if any(abs((day - _day(when)).days) <= gap for when in shown or [other[2]]):
            return TOO_CLOSE
    for booked in conn.execute(
            "SELECT p.due_at, p.status, t.editorial_json FROM scheduled_posts p JOIN runs r ON r.run_id = p.run_id "
            "JOIN topics t ON t.id = r.topic_id WHERE p.run_id <> ? AND r.topic_id <> ? AND p.kind = 'reel' "
            "AND p.status IN ('pending', 'waiting', 'unknown')", (run_id, topic_id)).fetchall():
        if not _sensitive(booked[2]):
            continue
        if booked[1] == "unknown" or not booked[0]:
            return UNCONFIRMED
        if abs((day - _day(booked[0])).days) <= gap:
            return TOO_CLOSE
    return ""


def topic_problem(conn: Any, topic_id: str, at: Any = None, *, run_id: str = "", spacing_only: bool = False) -> str:
    """The topic's hold reason (unless `spacing_only`), else its spacing_problem, else ""."""
    if not spacing_only:
        row = conn.execute("SELECT editorial_json FROM topics WHERE id = ?", (topic_id,)).fetchone()
        hold = json.loads((row and row[0]) or "{}").get("hold") if row else ""
        if hold:
            return f"On hold: {hold}"
    return spacing_problem(conn, topic_id, at, run_id=run_id)


def next_safe_slot(db: Any, topic_id: str, now: datetime, taken_days: Any, *, run_id: str = "") -> datetime:
    """post_schedule.next_reel_slot, moved on day by day until the topic's spacing allows it. Raises
    SensitiveSpacingError while another sensitive publication is unconfirmed (no day is safe until it is known)."""
    from .post_schedule import next_reel_slot
    cursor = now
    for _ in range(400):
        slot = next_reel_slot(cursor, taken_days)
        problem = db.topic_problem(topic_id, slot, run_id=run_id, spacing_only=True)
        if not problem:
            return slot
        if problem == UNCONFIRMED:
            raise SensitiveSpacingError(problem)
        cursor = slot + timedelta(minutes=1)
    raise SensitiveSpacingError("No posting day keeps sensitive topics apart; check the booked Reels")


def queued_count(db: Any) -> int:
    """Topics production can still pick: queued and not on hold."""
    return sum(t.get("status") == "queued" and not t.get("hold") for t in db.list_all_topics())


RESEARCH_WEEK_KEY = "topic_research_nightly_week"


def research_week(now: datetime | None = None) -> str:
    """The calendar week (ISO, config.TIMEZONE) the nightly research budget counts in, e.g. "2026-W46"."""
    from zoneinfo import ZoneInfo
    year, week, _ = (now or datetime.now(UTC)).astimezone(ZoneInfo(config.TIMEZONE)).isocalendar()
    return f"{year}-W{week:02d}"


def research_runs_this_week(db: Any, now: datetime | None = None) -> int:
    try:
        record = json.loads(db.get_setting(RESEARCH_WEEK_KEY) or "{}")
    except ValueError:
        record = {}
    return int(record.get("runs") or 0) if record.get("week") == research_week(now) else 0


def nightly_proposals(db: Any, trigger: Any, now: datetime | None = None) -> Any:
    """The nightly job: research proposes config.TOPIC_NIGHTLY_PROPOSALS ideas (the trigger's own job, budgets and
    human Add) only when fewer than config.TOPIC_QUEUE_FLOOR topics are queued, and at most
    config.TOPIC_RESEARCH_RUNS_PER_WEEK times a calendar week (the weekly run it replaced: spend does not rise). A
    run counts when it is started, whatever it finds."""
    if queued_count(db) >= config.TOPIC_QUEUE_FLOOR:
        return None
    used = research_runs_this_week(db, now)
    if used >= config.TOPIC_RESEARCH_RUNS_PER_WEEK:
        return None
    db.set_setting(RESEARCH_WEEK_KEY, json.dumps({"week": research_week(now), "runs": used + 1}))
    return trigger()
