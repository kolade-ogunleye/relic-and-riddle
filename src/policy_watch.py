"""
src/policy_watch.py
===================
Weekly watch for Meta policy changes that affect this channel.

Meta's policy pages are rendered by JavaScript, so a plain page diff sees only titles.
Instead Claude searches the web for changes since the last check, and reports only dated
items whose source is a URL the search actually returned. Each item is reported once.
"""

import hashlib
import html
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .db import PipelineDatabase
from .llm import claude_available, format_sources, research, structured_call
from .models import PolicyChange, PolicyDigest

log = logging.getLogger(__name__)

LAST_CHECK_KEY = "policy_watch_last_check"
SEEN_KEY = "policy_watch_seen"

AREAS = (
    "Meta Community Standards and Instagram Community Guidelines",
    "Facebook Content Monetization Policies and Partner Monetization Policies (including Reels and creator bonus programs)",
    "Meta rules on unoriginal, reused or mass-produced content, and recommendation eligibility",
    "Meta AI-generated content labeling and disclosure ('AI info') requirements",
    "Instagram and Facebook Reels publishing rules (length, branded content, music)",
    "Meta Graph API / Instagram Content Publishing API changes, version deprecations and rate limits",
)

RESEARCH_SYSTEM = """You monitor platform policy for a small Facebook/Instagram creator account that publishes AI-assisted, faceless, fact-checked history documentary Reels with an AI-generated voice, and posts through the Instagram/Facebook publishing APIs. Find official announcements and reputable reporting of changes. Prefer Meta's own pages (transparency.meta.com, about.fb.com, facebook.com/business/help, developers.facebook.com) and established tech/creator press. Report only changes announced or taking effect in the given window, with dates and the URL of each source. If you find nothing new, say so plainly."""

STRUCTURE_SYSTEM = "You structure policy findings faithfully. Do not add changes that are not in the findings."


def _key(change: PolicyChange) -> str:
    return hashlib.sha256(f"{change.area}|{change.source_url}|{change.summary[:120]}".encode()).hexdigest()[:16]


def check(db: PipelineDatabase, now: Optional[datetime] = None) -> List[PolicyChange]:
    """Returns new, sourced policy changes since the last check (and records them as seen)."""
    if not claude_available():
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    now = now or datetime.now(timezone.utc)
    last = db.get_setting(LAST_CHECK_KEY)
    since = datetime.fromisoformat(last) if last else now - timedelta(days=30)
    window = f"{since:%Y-%m-%d} to {now:%Y-%m-%d}"

    prompt = (f"Window: {window}.\nCheck each area for changes in that window:\n"
              + "\n".join(f"- {a}" for a in AREAS)
              + "\nFor every change: date, area, what changed, the source URL.")
    notes, retrieved = research(RESEARCH_SYSTEM, prompt, web_search_uses=8, effort="medium")
    digest = structured_call(
        STRUCTURE_SYSTEM,
        f"<findings>\n{notes}\n</findings>\n<retrieved_sources>\n{format_sources(retrieved)}\n</retrieved_sources>\n"
        "List each change. source_url must be one of the retrieved sources. impact: what it means for this "
        "channel in one sentence. action_needed: true only if the channel must change something.",
        PolicyDigest, effort="medium",
    )
    retrieved_urls = {s.url for s in retrieved}
    seen = set(json.loads(db.get_setting(SEEN_KEY, "[]")))
    fresh = []
    for c in digest.changes:
        if c.source_url not in retrieved_urls:
            log.info("Dropping policy item with unverified source: %s", c.source_url)
            continue
        k = _key(c)
        if k not in seen:
            seen.add(k)
            fresh.append(c)
    db.set_setting(SEEN_KEY, json.dumps(sorted(seen)))
    db.set_setting(LAST_CHECK_KEY, now.isoformat())
    return fresh


def digest_text(changes: List[PolicyChange], window_note: str = "") -> str:
    e = html.escape
    if not changes:
        return "📜 <b>Weekly policy watch</b>: no new Meta policy changes found" + (f" ({e(window_note)})" if window_note else "") + "."
    lines = [f"📜 <b>Weekly policy watch</b>: {len(changes)} change(s)"]
    for c in sorted(changes, key=lambda c: not c.action_needed):
        flag = "⚠️ ACTION" if c.action_needed else "ℹ️"
        lines.append(f"\n{flag} <b>{e(c.area)}</b> ({e(c.date)})\n{e(c.summary)}\n<i>Impact:</i> {e(c.impact)}")
        if c.action_needed and c.action:
            lines.append(f"<i>Do:</i> {e(c.action)}")
        lines.append(e(c.source_url))
    return "\n".join(lines)[:4000]
