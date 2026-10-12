"""
src/llm.py
==========
Thin wrapper around the Anthropic SDK.

Grounded calls run in two phases:
  1. research()        - Claude with the server-side web search tool, plain-text answer.
                         Sources are taken from the API's citation metadata (URLs the
                         search actually returned), never from model-written text.
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


def research(system: str, prompt: str, *, web_search_uses: int, effort: str = "high",
             max_tokens: int = 16000) -> Tuple[str, List[Source]]:
    """Web-search-grounded answer. Returns (text, sources actually cited by the API)."""
    client = _client()
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": web_search_uses}]
    for _ in range(_MAX_CONTINUATIONS):
        response = client.beta.messages.create(
            model=CLAUDE_MODEL, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
            output_config={"effort": effort}, betas=[_FALLBACK_BETA], fallbacks="default",
        )
        if response.stop_reason == "pause_turn":
            # Long server-tool turn: send the partial turn back unchanged so Claude can resume.
            messages.append({"role": "assistant", "content": response.content})
            continue
        _check_stop(response)

        parts, sources, seen = [], [], set()
        for block in response.content:
            if block.type != "text":
                continue
            parts.append(block.text)
            for c in block.citations or []:
                url = getattr(c, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append(Source(title=getattr(c, "title", "") or url, url=url))
        text = "".join(parts).strip()
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
