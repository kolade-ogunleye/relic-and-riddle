"""
src/shot_archetypes.py
======================
Documentary Shot Decomposition Engine.

Decomposes historical wonder and mystery topics into 4 fundamental
documentary shot archetypes for archival image retrieval:
  1. Landscape: Wide geographical panorama / terrain / environmental context
  2. Interior: Subterranean vaults / internal chambers / architectural structure
  3. Artifact: Macro engineering objects / inscriptions / craftsmanship
  4. Schematic: Archaeological survey plans / blueprints / historical excavation sketches

Uses Google Gemini Flash (or Claude) when configured, with robust offline heuristics.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .config import (
    GEMINI_API_KEY,
    GEMINI_FREE_API_KEY,
    ANTHROPIC_API_KEY,
    is_configured,
)

log = logging.getLogger(__name__)


class ShotDecomposition(BaseModel):
    landscape_query: str = Field(description="Search query for wide geographical context / exterior panorama")
    interior_query: str = Field(description="Search query for internal chambers, subterranean vaults, or architectural structural details")
    artifact_query: str = Field(description="Search query for portable artifacts, carvings, tools, or physical relics")
    schematic_query: str = Field(description="Search query for archaeological survey plans, blueprints, cross-sections, or excavation maps")
    keywords: List[str] = Field(description="6-8 specific lowercase single words that MUST appear in candidate image titles")


_STOP_WORDS = {
    "the", "a", "an", "of", "and", "in", "on", "at", "to", "for", "with", "by", "from",
    "ancient", "mystery", "lost", "secret", "great", "hidden", "unsolved", "miracle", "wonder",
}


def _heuristic_decomposition(title: str) -> Dict[str, Any]:
    """Offline heuristic fallback extracting core nouns and generating archetype queries."""
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", title) if w.lower() not in _STOP_WORDS and len(w) > 2]
    core = " ".join(words[:4]) or title
    keywords = list(dict.fromkeys(words + [w[:6] for w in words]))[:8]

    return {
        "queries": [
            f"{core} panorama landscape",
            f"{core} interior chamber architecture",
            f"{core} artifact carving relic",
            f"{core} plan blueprint excavation map",
        ],
        "keywords": keywords,
        "archetypes": {
            "landscape": f"{core} panorama",
            "interior": f"{core} interior chamber",
            "artifact": f"{core} artifact",
            "schematic": f"{core} plan blueprint",
        },
    }


def decompose_topic_visuals(
    title: str,
    context_facts: Optional[List[str]] = None,
    unesco_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Decomposes a topic into 4 structured documentary shot archetypes.
    Grounded with verified historical facts and UNESCO World Heritage registry records.
    Returns:
      {
         "queries": [q1, q2, q3, q4],
         "keywords": [k1, k2, ...],
         "archetypes": {"landscape": ..., "interior": ..., "artifact": ..., "schematic": ...}
      }
    """
    system_prompt = (
        "You are an archival documentary visual researcher specializing in ancient history, architecture, and mysteries. "
        "Decompose the given topic into 4 distinct search queries for Wikimedia Commons and Openverse archives. "
        "Avoid generic tourist phrases. Use authentic archaeological site designations, excavator surnames, "
        "museum names, and specific architectural chamber terminology."
    )
    context_str = ""
    if unesco_context:
        context_str += f"\nOfficial UNESCO World Heritage Record:\n{unesco_context}\n"
    if context_facts:
        facts_preview = "\n".join(f"- {f}" for f in context_facts[:4])
        context_str += f"\nVetted Historical/Archaeological Facts:\n{facts_preview}\n"

    user_prompt = (
        f"Topic Title: {title}\n"
        f"{context_str}"
        "Return 4 search queries matching these exact archetypes (strictly grounded in verified site features):\n"
        "1. landscape_query: exterior geographical panorama, geological terrain, aerial or landscape overview.\n"
        "2. interior_query: internal passage, underground chamber, structural stonework, or subterranean vault.\n"
        "3. artifact_query: portable archaeological artifact, carved relief, inscription, tool, or engineering mechanism.\n"
        "4. schematic_query: archaeological plan, cross-section diagram, survey map, or 19th-century excavation drawing.\n"
        "Also provide 6-8 distinct lowercase keywords that are likely to appear in the filenames of authentic photos."
    )

    # 1. Try Gemini Interactions API (Free Tier or Standard Key)
    gemini_key = GEMINI_FREE_API_KEY if is_configured(GEMINI_FREE_API_KEY) else GEMINI_API_KEY
    if is_configured(gemini_key):
        try:
            from . import gemini
            res = gemini.structured(system_prompt, user_prompt, ShotDecomposition, key=gemini_key)
            queries = [
                res.landscape_query.strip(),
                res.interior_query.strip(),
                res.artifact_query.strip(),
                res.schematic_query.strip(),
            ]
            keywords = [k.lower().strip() for k in res.keywords if len(k) > 2]
            return {
                "queries": queries,
                "keywords": keywords,
                "archetypes": {
                    "landscape": res.landscape_query.strip(),
                    "interior": res.interior_query.strip(),
                    "artifact": res.artifact_query.strip(),
                    "schematic": res.schematic_query.strip(),
                },
            }
        except Exception as e:
            log.warning("Gemini visual decomposition failed (%s); trying Claude fallback.", e)

    # 2. Try Claude if configured
    if is_configured(ANTHROPIC_API_KEY):
        try:
            from . import llm
            res = llm.structured_call(system_prompt, user_prompt, ShotDecomposition, effort="low", max_tokens=800)
            queries = [
                res.landscape_query.strip(),
                res.interior_query.strip(),
                res.artifact_query.strip(),
                res.schematic_query.strip(),
            ]
            keywords = [k.lower().strip() for k in res.keywords if len(k) > 2]
            return {
                "queries": queries,
                "keywords": keywords,
                "archetypes": {
                    "landscape": res.landscape_query.strip(),
                    "interior": res.interior_query.strip(),
                    "artifact": res.artifact_query.strip(),
                    "schematic": res.schematic_query.strip(),
                },
            }
        except Exception as e:
            log.warning("Claude visual decomposition failed (%s); falling back to heuristics.", e)

    # 3. Fallback to clean heuristic decomposition
    return _heuristic_decomposition(title)
