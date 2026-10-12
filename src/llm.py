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

Resilience and cost control:
  - Model chain: CLAUDE_MODEL first, then CLAUDE_FALLBACK_MODELS when a model is
    overloaded, unavailable or rate-limited. (Credit is per account, so an exhausted
    balance fails every Claude model; that is reported as CreditExhausted.)
  - Every call's token and search usage is priced and recorded (see configure_usage).
  - Optional monthly budget: once reached, use CLAUDE_BUDGET_MODEL, or stop if unset.
"""

import contextvars
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple, Type, TypeVar, Union

import anthropic
from pydantic import BaseModel

from .config import (
    ANTHROPIC_API_KEY,
    CLAUDE_BUDGET_MODEL,
    CLAUDE_FALLBACK_MODELS,
    CLAUDE_MODEL,
    CLAUDE_MONTHLY_BUDGET_USD,
    is_configured,
)
from .models import Source

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Server-side refusal fallback: on a policy decline the API re-runs the request on a
# fallback model chosen by refusal category, inside the same call. Not every model
# supports it (Claude Haiku 5.5 has none), so it is only sent where accepted.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_REFUSAL_FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")
_MAX_CONTINUATIONS = 5

# USD per million tokens (input, output); web search is $10 per 1,000 searches.
MODEL_PRICES = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
WEB_SEARCH_PRICE = 0.01


class LLMError(RuntimeError):
    pass


class CreditExhausted(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass


# Hooks wired by the application (main.py) to the database.
_record_usage: Optional[Callable[..., None]] = None
_month_spend: Optional[Callable[[str], float]] = None


# Which pipeline task the current calls belong to (e.g. "second_check"), for cost per task.
_task: contextvars.ContextVar[str] = contextvars.ContextVar("llm_task", default="other")


@contextmanager
def task(name: str):
    """Labels every Claude call inside the block with `name` in the usage records."""
    token = _task.set(name)
    try:
        yield
    finally:
        _task.reset(token)


def configure_usage(record: Callable[..., None], month_spend: Callable[[str], float]) -> None:
    global _record_usage, _month_spend
    _record_usage, _month_spend = record, month_spend


def claude_available() -> bool:
    return is_configured(ANTHROPIC_API_KEY)


def _client() -> anthropic.Anthropic:
    if not claude_available():
        raise LLMError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=3)


def call_cost(model: str, usage) -> float:
    in_price, out_price = MODEL_PRICES.get(model, MODEL_PRICES["claude-opus-5-5"])
    inputs = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    return inputs / 1e6 * in_price + (getattr(usage, "output_tokens", 0) or 0) / 1e6 * out_price + searches * WEB_SEARCH_PRICE


def _record(response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None or _record_usage is None:
        return
    model = getattr(response, "model", CLAUDE_MODEL)
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    try:
        _record_usage(model=model, input_tokens=getattr(usage, "input_tokens", 0) or 0,
                      output_tokens=getattr(usage, "output_tokens", 0) or 0,
                      web_searches=searches, cost_usd=round(call_cost(model, usage), 6), task=_task.get())
    except Exception as e:  # accounting must never break a run
        log.warning("Could not record Claude usage: %s", e)


def model_chain(models: Optional[List[str]] = None) -> List[str]:
    """Models to try, best first (or `models` if given), honouring the monthly budget."""
    chain = list(models) if models else [CLAUDE_MODEL] + [m for m in CLAUDE_FALLBACK_MODELS if m != CLAUDE_MODEL]
    if CLAUDE_MONTHLY_BUDGET_USD > 0 and _month_spend is not None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        spent = _month_spend(month)
        if spent >= CLAUDE_MONTHLY_BUDGET_USD:
            if not CLAUDE_BUDGET_MODEL:
                raise BudgetExceeded(f"Monthly Claude budget reached (${spent:.2f} of ${CLAUDE_MONTHLY_BUDGET_USD:.2f}).")
            log.warning("Monthly budget reached ($%.2f); using %s.", spent, CLAUDE_BUDGET_MODEL)
            chain = [CLAUDE_BUDGET_MODEL]
    return chain


def _model_kwargs(model: str) -> dict:
    if model in _REFUSAL_FALLBACK_MODELS:
        return {"betas": [_FALLBACK_BETA], "fallbacks": "default"}
    return {}


def _first_request(send: Callable[[str], object], models: Optional[List[str]] = None) -> Tuple[object, str]:
    """Sends the first request down the model chain. Returns (response, model used)."""
    last: Optional[Exception] = None
    for model in model_chain(models):
        try:
            return send(model), model
        except anthropic.BadRequestError as e:
            if "credit" in str(e).lower():
                raise CreditExhausted("Anthropic credit balance is too low; add credit in the Claude Console.") from e
            if "usage limit" in str(e).lower():
                raise CreditExhausted("The Anthropic account's API usage limit is reached (set in the Claude Console "
                                      f"under Limits); raise it to continue. Details: {e}") from e
            raise LLMError(f"Claude rejected the request: {e}") from e
        except (anthropic.InternalServerError, anthropic.NotFoundError, anthropic.RateLimitError,
                anthropic.APIConnectionError) as e:
            log.warning("%s unavailable (%s); trying the next model.", model, type(e).__name__)
            last = e
        except anthropic.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY was rejected.") from e
    raise LLMError(f"All Claude models failed; last error: {last}")


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
             max_tokens: int = 16000, models: Optional[List[str]] = None) -> Tuple[str, List[Source]]:
    """Web-search-grounded answer. Returns (text, sources the search actually returned)."""
    client = _client()
    system = system + _MUST_SEARCH
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": web_search_uses}]
    sources: List[Source] = []
    seen: set = set()

    def send(model: str):
        return client.beta.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
            output_config={"effort": effort}, **_model_kwargs(model),
        )

    response, model = _first_request(send, models)
    for _ in range(_MAX_CONTINUATIONS):
        _record(response)
        _collect_sources(response.content, sources, seen)
        if response.stop_reason == "pause_turn":
            # Long server-tool turn: resume on the same model with the partial turn unchanged.
            messages.append({"role": "assistant", "content": response.content})
            response = send(model)
            continue
        _check_stop(response)

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMError("Research returned no text.")
        return text, sources
    raise LLMError("Claude did not finish after repeated pause_turn continuations.")


def research_with_evidence(system: str, prompt: str, *, web_search_uses: int, effort: str = "high",
                           max_tokens: int = 16000, models: Optional[List[str]] = None
                           ) -> Tuple[str, List[Source], List[dict]]:
    """Like research(), plus every citation's exact quoted passage: [{"url", "title", "quote"}].
    The quotes are the evidence a fact sheet is built on (text the search actually returned)."""
    client = _client()
    system = system + _MUST_SEARCH
    messages = [{"role": "user", "content": prompt}]
    # The basic search tool: its answers carry citations with the quoted passage (cited_text). The newer
    # dynamic-filtering tool (web_search_20260209) returned empty citation lists in testing (2026-10-12).
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": web_search_uses}]
    sources: List[Source] = []
    seen: set = set()
    quotes: List[dict] = []
    seen_quotes: set = set()

    def send(model: str):
        return client.beta.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
            output_config={"effort": effort}, **_model_kwargs(model),
        )

    response, model = _first_request(send, models)
    for _ in range(_MAX_CONTINUATIONS):
        _record(response)
        _collect_sources(response.content, sources, seen)
        for block in response.content:
            if getattr(block, "type", None) != "text":
                continue
            for c in block.citations or []:
                quote = (getattr(c, "cited_text", "") or "").strip()
                url = getattr(c, "url", None)
                if url and quote and (url, quote) not in seen_quotes:
                    seen_quotes.add((url, quote))
                    quotes.append({"url": url, "title": getattr(c, "title", "") or url, "quote": quote})
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            response = send(model)
            continue
        _check_stop(response)
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMError("Research returned no text.")
        return text, sources, quotes
    raise LLMError("Claude did not finish after repeated pause_turn continuations.")


def image_block(path, max_side: int = 512) -> dict:
    """A small JPEG of `path` as a base64 image content block (keeps vision calls cheap)."""
    import base64
    import io

    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(buf.getvalue()).decode("ascii")}}


def structured_call(system: str, prompt: Union[str, list], schema: Type[T], *, effort: str = "high",
                    max_tokens: int = 16000, models: Optional[List[str]] = None) -> T:
    """One Claude request whose answer must validate against `schema` (no tools).
    `prompt` may be a string or a list of content blocks (e.g. images then text)."""
    client = _client()

    def send(model: str):
        return client.beta.messages.parse(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort}, output_format=schema, **_model_kwargs(model),
        )

    response, _ = _first_request(send, models)
    _record(response)
    _check_stop(response)
    if response.parsed_output is None:
        raise LLMError(f"No structured output returned (stop_reason={response.stop_reason}).")
    return response.parsed_output


def format_sources(sources: List[Source]) -> str:
    return "\n".join(f"- {s.title}: {s.url}" for s in sources) or "(none)"
