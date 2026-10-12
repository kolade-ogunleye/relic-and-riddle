"""
src/topic_proposer.py
=====================
Keeps the topic queue full. Claude researches new topics that fit the niche and don't
duplicate the bank; each proposal waits for a human "Add" before it joins the bank.

  propose()   research + structured proposals (sources limited to URLs the search returned)
  add()       appends an approved proposal to topic_bank.json and image_queries.json,
              then syncs the database
"""

import difflib
import html
import json
import logging
import re
import unicodedata
from typing import Dict, List, Optional

from .config import TOPICS_DIR
from .db import PipelineDatabase
from .llm import claude_available, format_sources, research, structured_call, task as llm_task
from .models import TopicProposal, TopicProposals

log = logging.getLogger(__name__)

PENDING_KEY = "topic_proposals_pending"
BANK = TOPICS_DIR / "topic_bank.json"
QUERIES = TOPICS_DIR / "image_queries.json"

RESEARCH_SYSTEM = """You find topics for a channel of 45-second documentary Reels in the niche "Historical Curiosities & Ancient Marvels": real places, objects, feats of engineering and strange-but-true events. Good topics have a concrete, surprising hook that is TRUE, well-documented facts from reliable sources, an honest open question or remarkable detail, and openly licensed images likely to exist on Wikimedia Commons. Avoid pseudo-archaeology (aliens, Atlantis, lost super-civilisations), recent tragedies, and topics where the popular story is mostly myth unless the myth-busting is the story."""

STRUCTURE_SYSTEM = "You turn topic research into structured proposals. Use only facts and sources present in the research."


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50]


_STOP = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "s", "its", "how", "why", "what", "that", "with",
         "for", "from", "ancient", "mystery", "lost", "secret", "great", "old", "year"}


def _key_words(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower())
    return {w for w in words if w not in _STOP and not w.isdigit() and len(w) > 2}


def near_duplicate(title: str, existing: List[str]) -> Optional[str]:
    """Returns the existing title this one duplicates (shared key words or very similar wording), if any."""
    new = _key_words(title)
    for other in existing:
        old = _key_words(other)
        shared = new & old
        if len(shared) >= 2 and len(shared) / min(len(new), len(old)) >= 0.5:
            return other
        if difflib.SequenceMatcher(None, title.lower(), other.lower()).ratio() >= 0.75:
            return other
    return None


def _bank() -> dict:
    return json.loads(BANK.read_text(encoding="utf-8"))


def pending(db: PipelineDatabase) -> Dict[str, dict]:
    return json.loads(db.get_setting(PENDING_KEY, "{}"))


@llm_task("topic_proposals")
def propose(db: PipelineDatabase, n: int = 3) -> List[TopicProposal]:
    if not claude_available():
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    existing = [t["title"] for t in _bank()["topics"]] + [p["title"] for p in pending(db).values()]
    prompt = (f"Find {n} new topics. Do not repeat or closely overlap these existing topics:\n"
              + "\n".join(f"- {t}" for t in existing)
              + "\nFor each: the core facts with sources, a true hook, location, why it would hold viewers, "
                "and whether Wikimedia Commons is likely to have openly licensed images of it.")
    notes, retrieved = research(RESEARCH_SYSTEM, prompt, web_search_uses=8, effort="medium")
    proposals = structured_call(
        STRUCTURE_SYSTEM,
        f"<research>\n{notes}\n</research>\n<retrieved_sources>\n{format_sources(retrieved)}\n</retrieved_sources>\n"
        f"Return {n} proposals. key_facts: exactly 5, hedged where sources disagree. hashtags: exactly 7. "
        "image_queries: 2-4 Wikimedia Commons searches. image_keywords: 2-6 lowercase words, at least one of which "
        "must appear in a relevant image file title. sources: only from retrieved_sources.",
        TopicProposals, effort="medium",
    )
    retrieved_urls = {s.url for s in retrieved}
    store = pending(db)
    taken = {t["id"].split("_", 1)[1] for t in _bank()["topics"]} | set(store)
    fresh = []
    for p in proposals.topics:
        p.slug = _slugify(p.slug or p.title)
        p.sources = [s for s in p.sources if s.url in retrieved_urls]
        dup = near_duplicate(p.title, existing)
        if not p.sources or p.slug in taken or len(p.key_facts) < 3 or dup:
            log.info("Skipping proposal %s (no verified source, duplicate%s, or too thin).", p.slug,
                     f" of '{dup}'" if dup else "")
            continue
        existing.append(p.title)
        taken.add(p.slug)
        store[p.slug] = p.model_dump()
        fresh.append(p)
    db.set_setting(PENDING_KEY, json.dumps(store))
    return fresh


def add(db: PipelineDatabase, slug: str) -> str:
    """Appends an approved proposal to the bank (next id number) and its image searches."""
    store = pending(db)
    if slug not in store:
        raise ValueError(f"No pending proposal '{slug}'.")
    p = TopicProposal.model_validate(store.pop(slug))
    bank = _bank()
    number = max(int(t["id"].split("_")[0]) for t in bank["topics"]) + 1
    topic_id = f"{number:02d}_{p.slug}"
    bank["topics"].append({
        "id": topic_id, "title": p.title, "sub_theme": p.sub_theme, "hook": p.hook,
        "location": p.location.model_dump(), "key_facts": p.key_facts,
        "meta_categories": ["History & Philosophy", "Travel & Culture"], "hashtags": p.hashtags,
        "sources": [s.model_dump() for s in p.sources],
    })
    bank["total"] = len(bank["topics"])
    BANK.write_text(json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))
    queries[topic_id] = {"queries": p.image_queries, "keywords": [k.lower() for k in p.image_keywords]}
    QUERIES.write_text(json.dumps(queries, indent=2, ensure_ascii=False), encoding="utf-8")
    db.set_setting(PENDING_KEY, json.dumps(store))
    db._seed_topics_from_json()
    return topic_id


def skip(db: PipelineDatabase, slug: str) -> None:
    store = pending(db)
    store.pop(slug, None)
    db.set_setting(PENDING_KEY, json.dumps(store))


def proposal_text(p: TopicProposal) -> str:
    e = html.escape
    facts = "\n".join(f"• {e(f)}" for f in p.key_facts)
    loc = ", ".join(x for x in (p.location.name, p.location.country) if x)
    src = "\n".join(e(s.url) for s in p.sources[:3])
    return (f"💡 <b>Topic proposal: {e(p.title)}</b>\n📍 {e(loc)} · {e(p.sub_theme)}\n\n"
            f"<i>{e(p.hook)}</i>\n\n{facts}\n\nWhy: {e(p.why_it_works)}\n\n📚 {src}")[:4000]
