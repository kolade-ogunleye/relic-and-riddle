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


def _normalize_id(tid: str) -> str:
    parts = tid.split("_", 1)
    return parts[1] if len(parts) > 1 and parts[0].isdigit() else tid


@llm_task("topic_proposals")
def propose(db: PipelineDatabase, n: int = 3) -> List[TopicProposal]:
    from . import gemini
    if not claude_available() and not gemini.available():
        raise RuntimeError("Neither ANTHROPIC_API_KEY nor GEMINI_API_KEY is configured.")
    existing = [t["title"] for t in _bank()["topics"]] + [p["title"] for p in pending(db).values()]
    candidate_count = max(n + 2, 5)
    prompt = (f"Find {candidate_count} new topics. Do not repeat or closely overlap these existing topics:\n"
              + "\n".join(f"- {t}" for t in existing)
              + "\nFor each: the core facts with sources, a true hook, location, why it would hold viewers, "
                "and whether Wikimedia Commons is likely to have openly licensed images of it.")

    notes, retrieved = None, None
    if claude_available():
        try:
            notes, retrieved = research(RESEARCH_SYSTEM, prompt, web_search_uses=8, effort="medium")
        except Exception as e:
            log.warning("Claude research for proposals failed (%s); trying Gemini fallback.", e)
            if not gemini.available():
                raise
    if notes is None:
        if not gemini.available():
            raise RuntimeError("Gemini is not configured as fallback.")
        notes, retrieved = gemini.research(RESEARCH_SYSTEM, prompt)

    sources_instruction = (
        "sources: only from retrieved_sources."
        if retrieved
        else "sources: provide 1-3 reliable public reference URLs for each topic (e.g. Wikipedia, museum, or academic sources)."
    )

    struct_prompt = (
        f"<research>\n{notes}\n</research>\n<retrieved_sources>\n{format_sources(retrieved)}\n</retrieved_sources>\n"
        f"Return {candidate_count} proposals. key_facts: exactly 5, hedged where sources disagree. hashtags: exactly 7. "
        "image_queries: 2-4 Wikimedia Commons searches. image_keywords: 2-6 lowercase words, at least one of which "
        f"must appear in a relevant image file title. {sources_instruction}"
    )
    proposals = None
    if claude_available():
        try:
            proposals = structured_call(STRUCTURE_SYSTEM, struct_prompt, TopicProposals, effort="medium")
        except Exception as e:
            log.warning("Claude structured call failed (%s); trying Gemini fallback.", e)
            if not gemini.available():
                raise
    if proposals is None:
        proposals = gemini.structured(STRUCTURE_SYSTEM, struct_prompt, TopicProposals)
    def _norm_url(u: str) -> str:
        return (u or "").strip().rstrip("/").lower().replace("http://", "https://")

    norm_retrieved = {_norm_url(s.url): s for s in retrieved if s and s.url}
    store = pending(db)
    taken = {_normalize_id(t["id"]) for t in _bank()["topics"]} | {t["id"] for t in _bank()["topics"]} | set(store)
    fresh = []
    for p in proposals.topics:
        p.slug = _slugify(p.slug or p.title)
        valid_sources = []
        if retrieved:
            for s in (p.sources or []):
                if not s or not s.url:
                    continue
                nu = _norm_url(s.url)
                if nu in norm_retrieved:
                    valid_sources.append(norm_retrieved[nu])
                elif any(nu.split("://")[-1] in r or r.split("://")[-1] in nu for r in norm_retrieved):
                    valid_sources.append(s)
        else:
            valid_sources = [s for s in (p.sources or []) if s and s.url]
        p.sources = valid_sources

        dup = near_duplicate(p.title, existing)
        if not p.sources or p.slug in taken or len(p.key_facts) < 3 or dup:
            log.info("Skipping proposal %s (no verified source, duplicate%s, or too thin).", p.slug,
                     f" of '{dup}'" if dup else "")
            continue
        existing.append(p.title)
        taken.add(p.slug)
        store[p.slug] = p.model_dump()
        fresh.append(p)
        if len(fresh) >= n:
            break
    db.set_setting(PENDING_KEY, json.dumps(store))
    return fresh


def add(db: PipelineDatabase, slug: str) -> str:
    """Appends an approved proposal to the bank and its image searches."""
    store = pending(db)
    if slug not in store:
        raise ValueError(f"No pending proposal '{slug}'.")
    p = TopicProposal.model_validate(store.pop(slug))
    bank = _bank()
    topic_id = p.slug
    bank["topics"].append({
        "id": topic_id, "title": p.title, "sub_theme": p.sub_theme, "hook": p.hook,
        "location": p.location.model_dump(), "key_facts": p.key_facts,
        "meta_categories": ["History & Philosophy", "Travel & Culture"], "hashtags": p.hashtags,
        "sources": [s.model_dump() for s in p.sources],
    })
    bank["total"] = len(bank["topics"])
    BANK.write_text(json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))
    if p.image_queries and p.image_keywords:
        queries[topic_id] = {"queries": p.image_queries, "keywords": [k.lower() for k in p.image_keywords]}
    else:
        from .shot_archetypes import decompose_topic_visuals
        decomp = decompose_topic_visuals(p.title, context_facts=p.key_facts)
        queries[topic_id] = {
            "queries": decomp["queries"],
            "keywords": decomp["keywords"],
            "archetypes": decomp.get("archetypes", {}),
        }
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
    src = "\n".join(f"📚 {e(s.url)}" for s in p.sources[:3])
    return (f"💡 <b>Topic proposal: {e(p.title)}</b>\n📍 {e(loc)} · {e(p.sub_theme)}\n\n"
            f"<i>\"{e(p.hook)}\"</i>\n\n"
            f"<blockquote expandable><b>Key Verified Facts:</b>\n{facts}\n\n"
            f"<b>Editorial Rationale:</b> {e(p.why_it_works)}\n\n"
            f"{src}</blockquote>")[:4000]
