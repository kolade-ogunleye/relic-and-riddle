"""
src/llm.py
==========
Thin wrapper around the Anthropic SDK.

Grounded calls run in two phases:
  1. research()        - Claude with the server-side web search tool, plain-text answer.
                         Sources are the URLs the search actually returned (from
                         web_search_tool_result blocks, plus any citation metadata),
                         never from model-written text. With web_search_20260209 the
                         results pass through dynamic filtering, and the final text
                         often carries no citations, so result blocks are the main source.
  2. structured_call() - no tools; the answer is constrained to a Pydantic schema.
Keeping web search (whose citations are always on) separate from structured output
avoids combining two features in one request, and gives callers a trustworthy set of
retrieved URLs to check model-reported sources against.
"""

import logging
from typing import List, Tuple, Type, TypeVar

import anthropic
from pydantic import BaseModel

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, is_configured
from .models import Source

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Server-side refusal fallback: on a policy decline the API re-runs the request on a
# fallback model chosen by refusal category, inside the same call.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_MAX_CONTINUATIONS = 5


class LLMError(RuntimeError):
    pass


def claude_available() -> bool:
    return is_configured(ANTHROPIC_API_KEY)


def _client() -> anthropic.Anthropic:
    if not claude_available():
        raise LLMError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=3)


def _check_stop(response) -> None:
    log.info("Claude %s: stop_reason=%s, request_id=%s", response.model, response.stop_reason, response._request_id)
    if response.stop_reason == "refusal":
        raise LLMError(f"Claude declined the request ({getattr(response.stop_details, 'category', None)}).")
    if response.stop_reason == "max_tokens":
        raise LLMError("Claude hit max_tokens before finishing.")


_MUST_SEARCH = (
    "\n\nAlways use the web search tool before answering, even for well-known topics; "
    "do not answer from memory alone."
)


def _collect_sources(blocks, sources: List[Source], seen: set) -> None:
    """Adds every URL the search returned (result blocks) or the text cited."""
    for block in blocks:
        btype = getattr(block, "type", None)
        if btype == "web_search_tool_result" and isinstance(getattr(block, "content", None), list):
            items = [(getattr(r, "url", None), getattr(r, "title", "")) for r in block.content]
        elif btype == "text":
            items = [(getattr(c, "url", None), getattr(c, "title", "")) for c in (block.citations or [])]
        else:
            continue
        for url, title in items:
            if url and url not in seen:
                seen.add(url)
                sources.append(Source(title=title or url, url=url))


def research(system: str, prompt: str, *, web_search_uses: int, effort: str = "high",
             max_tokens: int = 16000) -> Tuple[str, List[Source]]:
    """Web-search-grounded answer. Returns (text, sources the search actually returned)."""
    client = _client()
    system = system + _MUST_SEARCH
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": web_search_uses}]
    sources: List[Source] = []
    seen: set = set()
    for _ in range(_MAX_CONTINUATIONS):
        response = client.beta.messages.create(
            model=CLAUDE_MODEL, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
            output_config={"effort": effort}, betas=[_FALLBACK_BETA], fallbacks="default",
        )
        _collect_sources(response.content, sources, seen)
        if response.stop_reason == "pause_turn":
            # Long server-tool turn: send the partial turn back unchanged so Claude can resume.
            messages.append({"role": "assistant", "content": response.content})
            continue
        _check_stop(response)

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMError("Research returned no text.")
        return text, sources
    raise LLMError("Claude did not finish after repeated pause_turn continuations.")


def structured_call(system: str, prompt: str, schema: Type[T], *, effort: str = "high", max_tokens: int = 16000) -> T:
    """One Claude request whose answer must validate against `schema` (no tools)."""
    response = _client().beta.messages.parse(
        model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": effort}, output_format=schema,
        betas=[_FALLBACK_BETA], fallbacks="default",
    )
    _check_stop(response)
    if response.parsed_output is None:
        raise LLMError(f"No structured output returned (stop_reason={response.stop_reason}).")
    return response.parsed_output


def format_sources(sources: List[Source]) -> str:
    return "\n".join(f"- {s.title}: {s.url}" for s in sources) or "(none)"
