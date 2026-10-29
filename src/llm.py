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
import re
import time
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
    # Retries are counted here, not inside the SDK (operator, 2026-10-25: Opus twice, then Sonnet once).
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)


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


# Legacy models without the effort parameter (a budget or fallback model may be one of these).
_NO_EFFORT_PREFIXES = ("claude-3", "claude-haiku-4-5")


def _model_kwargs(model: str, effort: str) -> dict:
    """Per-model request options. Effort is sent explicitly: Claude Opus 5.5 defaults to medium, so a
    task asking for high would otherwise run at medium (and low tasks would spend more than asked)."""
    kw: dict = {}
    if not model.startswith(_NO_EFFORT_PREFIXES):
        kw["output_config"] = {"effort": effort}
    if model in _REFUSAL_FALLBACK_MODELS:
        kw.update(betas=[_FALLBACK_BETA], fallbacks="default")
    return kw


PRIMARY_TRIES = 2         # Opus (CLAUDE_MODEL): two tries
FALLBACK_TRIES = 1        # then each fallback model (Sonnet) once, as the last try
RETRY_PAUSE_SECONDS = 5


def _send_again(send: Callable[[str], object], model: str):
    """A continuation on the same model (a pause_turn): one more try if the connection or server fails."""
    try:
        return send(model)
    except (anthropic.InternalServerError, anthropic.RateLimitError, anthropic.APIConnectionError):
        time.sleep(RETRY_PAUSE_SECONDS)
        return send(model)


def _first_request(send: Callable[[str], object], models: Optional[List[str]] = None) -> Tuple[object, str]:
    """Sends the first request down the model chain. Returns (response, model used)."""
    last: Optional[Exception] = None
    from . import provider_status
    chain = model_chain(models)
    for position, model in enumerate(chain):
        tries = PRIMARY_TRIES if position == 0 else FALLBACK_TRIES
        for attempt in range(1, tries + 1):
            try:
                response = send(model)
                provider_status.clear("claude")
                return response, model
            except anthropic.BadRequestError as e:
                if "credit" in str(e).lower():
                    provider_status.note_capped("claude", "the credit balance is too low; add credit in the Claude Console")
                    raise CreditExhausted("Anthropic credit balance is too low; add credit in the Claude Console.") from e
                if "usage limit" in str(e).lower():
                    provider_status.note_capped("claude", "the API usage limit set in the Claude Console is reached; "
                                                "raise it under Limits", until=provider_status.until_from(str(e)))
                    raise CreditExhausted("The Anthropic account's API usage limit is reached (set in the Claude Console "
                                          f"under Limits); raise it to continue. Details: {e}") from e
                raise LLMError(f"Claude rejected the request: {e}") from e
            except anthropic.NotFoundError as e:  # the model itself is not available: straight to the next one
                log.warning("%s unavailable (%s); trying the next model.", model, type(e).__name__)
                last = e
                break
            except (anthropic.InternalServerError, anthropic.RateLimitError, anthropic.APIConnectionError) as e:
                last = e
                if attempt < tries:
                    log.warning("%s failed (%s); trying it once more.", model, type(e).__name__)
                    time.sleep(RETRY_PAUSE_SECONDS)
                    continue
                log.warning("%s unavailable (%s); trying the next model.", model, type(e).__name__)
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


class _CallRecord:
    """One line in the run's llm_calls.jsonl for a web-searching call, whose turn may be several requests
    (pause_turn continuations): their tokens are summed and the searches counted. Until 2026-10-24 only
    structured calls were recorded, and the second opinion's 180-300k-token search request was invisible
    in a run's evidence while it was the run's largest cost."""

    def __init__(self):
        import time as _time
        self.started, self.tokens_in, self.tokens_out, self.searches = _time.perf_counter(), 0, 0, 0

    def add(self, response) -> None:
        usage = getattr(response, "usage", None)
        self.tokens_in += int(getattr(usage, "input_tokens", 0) or 0)
        self.tokens_out += int(getattr(usage, "output_tokens", 0) or 0)
        self.searches += int(getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0)

    def done(self, model: str, outcome: str = "ok") -> None:
        import time as _time
        from .ops import record_llm_call
        record_llm_call(current_task(), model, _time.perf_counter() - self.started, tokens_in=self.tokens_in,
                        tokens_out=self.tokens_out, outcome=outcome, searches=self.searches)


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
        kw = _model_kwargs(model, effort)
        try:
            return client.beta.messages.create(
                model=model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
                **kw,
            )
        except TypeError as e:
            if "fallbacks" in str(e):
                kw.pop("fallbacks", None)
                return client.beta.messages.create(
                    model=model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
                    **kw,
                )
            raise

    record, model = _CallRecord(), "claude"
    try:
        response, model = _first_request(send, models)
        for _ in range(_MAX_CONTINUATIONS):
            _record(response)
            record.add(response)
            _collect_sources(response.content, sources, seen)
            if response.stop_reason == "pause_turn":
                # Long server-tool turn: resume on the same model with the partial turn unchanged.
                messages.append({"role": "assistant", "content": response.content})
                response = _send_again(send, model)
                continue
            _check_stop(response)

            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if not text:
                raise LLMError("Research returned no text.")
            record.done(model)
            return text, sources
        raise LLMError("Claude did not finish after repeated pause_turn continuations.")
    except Exception as e:  # the run's record says what failed and how long it took
        record.done(model, outcome=type(e).__name__)
        raise


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
        kw = _model_kwargs(model, effort)
        try:
            return client.beta.messages.create(
                model=model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
                **kw,
            )
        except TypeError as e:
            if "fallbacks" in str(e):
                kw.pop("fallbacks", None)
                return client.beta.messages.create(
                    model=model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
                    **kw,
                )
            raise

    record, model = _CallRecord(), "claude"
    try:
        response, model = _first_request(send, models)
        for _ in range(_MAX_CONTINUATIONS):
            _record(response)
            record.add(response)
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
                response = _send_again(send, model)
                continue
            _check_stop(response)
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if not text:
                raise LLMError("Research returned no text.")
            record.done(model)
            return text, sources, quotes
        raise LLMError("Claude did not finish after repeated pause_turn continuations.")
    except Exception as e:
        record.done(model, outcome=type(e).__name__)
        raise


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
    """One request whose answer must validate against `schema` (no tools).
    `prompt` may be a string or a list of content blocks (e.g. images then text)."""
    client = _client()

    parse_fn = getattr(client.beta.messages, "parse", None)
    if parse_fn is None:
        from . import gemini
        return gemini.structured(system, prompt, schema)

    def send(model: str):
        kw = _model_kwargs(model, effort)
        try:
            return parse_fn(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema, **kw,
            )
        except TypeError as e:
            if "fallbacks" in str(e):
                kw.pop("fallbacks", None)
                return parse_fn(
                    model=model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": prompt}],
                    output_format=schema, **kw,
                )
            raise

    from .ops import record_llm_call
    import time as _time
    started = _time.perf_counter()
    for attempt in (1, 2):
        try:
            response, model_used = _first_request(send, models)
            break
        except ValueError as e:
            # The SDK could not parse the answer (pydantic's ValidationError is a ValueError): a truncated or
            # degenerate body, as when a trace check repeated "ok." until max_tokens (writer trial, 2026-10-24).
            # One more sample usually parses; after that the caller gets an LLMError, not a crash.
            record_llm_call(current_task(), "claude", _time.perf_counter() - started, outcome=type(e).__name__)
            if attempt == 2:
                raise LLMError(f"Claude's structured answer did not parse twice: {str(e)[:200]}") from e
            log.warning("Claude's structured answer did not parse (%s); asking once more.", str(e)[:120])
            started = _time.perf_counter()
        except Exception as e:  # the run's record says what failed and how long it took
            record_llm_call(current_task(), "claude", _time.perf_counter() - started, outcome=type(e).__name__)
            raise
    usage = getattr(response, "usage", None)
    record_llm_call(current_task(), model_used, _time.perf_counter() - started,
                    tokens_in=int(getattr(usage, "input_tokens", 0) or 0), tokens_out=int(getattr(usage, "output_tokens", 0) or 0))
    _record(response)
    _check_stop(response)
    if response.parsed_output is None:
        raise LLMError(f"No structured output returned (stop_reason={response.stop_reason}).")
    return response.parsed_output


def current_task() -> str:
    """The task label of the model call in progress (llm.task), or ''."""
    try:
        return _task.get() or ""
    except LookupError:
        return ""


# A model's own working notes left in a verdict's reason (production Derinkuyu, 2026-10-29: "Wait, make sure no
# control tokens or formatting errors inside JSON." was logged as a picture check's reason). A sentence that talks
# to itself or about the answer's format is not a reason; the verdict it came with stands.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# Run audit 2026-10-29 (production Derinkuyu): "Well alright, let's wrap this up...", "Done well done perfectly
# done..." also came back as reasons; a sentence that opens by winding the answer up is working notes too.
_SELF_TALK = re.compile(
    r"^\W*(?:wait\b|hmm+\b|let me\b|double[- ]check|re-?check\b|make sure\b|note to self|self[- ]correction|"
    r"on second thought|(?:ok(?:ay)?|alright|so|now),?\s+(?:let me|wait)\b|"
    r"(?:well,?\s+)?(?:ok(?:ay)?|alright|all right|so|now)[,.!]?\s+let'?s\b|well,?\s+(?:alright|all right|ok(?:ay)?)\b|"
    r"let'?s\s+(?:wrap|finish|double|re-?check|make sure|output|write|format)\b|done\b[\s,.!]*(?:well|perfectly|done)\b)",
    re.IGNORECASE)
_FORMAT_TALK = re.compile(r"\b(?:json|control tokens?|formatting errors?|output format)\b|```", re.IGNORECASE)
_FIELD_NAME = re.compile(r"\b[a-z]+_[a-z_]+\b")   # "text_edge", "shows_subject": the answer's own fields named
REASON_MAX_CHARS = 300   # a one-sentence reason; longer is a model running on (Richat's cached 21,768-character one)
_ECHO_WORDS = 8          # this many words in a row from the question: the question repeated, not a reason


def _reason_words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", str(text or "").lower())


def _repeat_at(words: List[str], n: int = 3, times: int = 3) -> int:
    """Where a degenerate repetition starts: the index of the second occurrence of the first `n`-word run that
    occurs `times` times, or -1. A run said twice is ordinary prose; three times is a model looping."""
    seen: dict = {}
    for i in range(len(words) - n + 1):
        seen.setdefault(tuple(words[i:i + n]), []).append(i)
    looping = [spots for spots in seen.values() if len(spots) >= times]
    return min(spots[1] for spots in looping) if looping else -1


_COMMON = {"the", "a", "an", "of", "and", "or", "in", "on", "at", "to", "is", "it", "its", "this", "that", "with",
           "not", "no", "as", "by", "for", "from", "are", "be", "which", "photo", "picture", "image", "shows"}


def _repetitive(words: List[str]) -> bool:
    """A reason that loops: a three-word run three times, or one word making a fifth of five or more words
    ("Done well done perfectly done")."""
    if _repeat_at(words) >= 0:
        return True
    content = [w for w in words if w not in _COMMON and len(w) > 2]
    if len(words) < 5 or not content:
        return False
    top = max(content.count(w) for w in set(content))
    return top >= 3 and top / len(words) >= 0.2


def degenerate_reason(text: str, prompt: str = "") -> str:
    """Why `text`, a model's one-line reason, cannot be trusted as an answer at all, or "" when it can: it runs
    past REASON_MAX_CHARS, repeats itself, holds the model's working notes ("Wait, ...", "Well alright, let's
    ..."), names the answer's fields, or repeats `prompt` (the question it was asked) _ECHO_WORDS words at a time.
    A caller asks again rather than keep such an answer (run audit 2026-10-29: these were cached as verdicts)."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    if len(text) > REASON_MAX_CHARS:
        return f"its reason runs to {len(text)} characters"
    words = _reason_words(text)
    if _repetitive(words):
        return "its reason repeats itself"
    if any(_SELF_TALK.match(s) or _FORMAT_TALK.search(s) for s in _SENTENCE_SPLIT.split(text)):
        return "its reason holds the model's working notes"
    if _FIELD_NAME.search(text):
        return "its reason names the answer's fields"
    asked = _reason_words(prompt)
    if len(words) >= _ECHO_WORDS and len(asked) >= _ECHO_WORDS:
        runs = {tuple(asked[i:i + _ECHO_WORDS]) for i in range(len(asked) - _ECHO_WORDS + 1)}
        if any(tuple(words[i:i + _ECHO_WORDS]) in runs for i in range(len(words) - _ECHO_WORDS + 1)):
            return "its reason repeats the question"
    return ""


def verdict_reason(text: str) -> str:
    """`text` (a model's one-line reason for a verdict) without its stray reasoning: only clearly meta sentences,
    the model talking to itself ("Wait, ...", "Let me ...", "make sure ...", "Well alright, let's ...") or about
    the answer's format (JSON, control tokens), a sentence said again, and a loop (a three-word run said a third
    time: cut where it starts repeating); at most REASON_MAX_CHARS, cut at a word. A reason that starts "OK," or
    "I think" is a reason and stays. The verdict is never changed here; "" when nothing is left. Applied to cached
    reasons too, so an old run-on is not logged whole."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    kept, said = [], set()
    for s in _SENTENCE_SPLIT.split(text):
        key = " ".join(_reason_words(s))
        if s.strip() and not _SELF_TALK.match(s) and not _FORMAT_TALK.search(s) and key not in said:
            kept.append(s)
            said.add(key)
    out = " ".join(kept).strip()
    words = out.split()
    loop = _repeat_at([" ".join(_reason_words(w)) for w in words])
    if loop >= 0:
        out = " ".join(words[:loop]).rstrip(" ,;:") + "…"
    if len(out) > REASON_MAX_CHARS:
        out = out[:REASON_MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return out


def format_sources(sources: List[Source]) -> str:
    return "\n".join(f"- {s.title}: {s.url}" for s in sources) or "(none)"
