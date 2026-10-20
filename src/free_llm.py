"""
src/free_llm.py
===============
Zero-cost text model for script research, writing and fact checks: Gemini on the free tier.

It uses GEMINI_FREE_API_KEY, which must come from a Google project with billing turned off. When the
free daily quota is used up (or the free model stays overloaded), a call may fall back to the billed
GEMINI_API_KEY, but only for task classes with a monthly cap above zero and only while that month's
paid spend for the class is under it: PAID_FALLBACK_LIGHT_MONTHLY_USD (photo review, sound plan) and
PAID_FALLBACK_HEAVY_MONTHLY_USD (scripts, fact sheets, fact checks). Each paid call's cost is
recorded (llm_usage, task "paid-fallback:<class>"). Otherwise calls fail with FreeQuotaUnavailable.
Temporary overloads (HTTP 503) are retried with backoff. Free-tier prompts may be used by Google to
improve its products: only public research material is sent.
"""

import logging
import time
from typing import Type, TypeVar

from pydantic import BaseModel

from . import config, gemini
from .llm import LLMError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class FreeQuotaUnavailable(LLMError):
    """No free key, or its free quota is used up for now."""


def enabled() -> bool:
    return config.SCRIPT_ENGINE == "free"


def available() -> bool:
    return config.is_configured(config.GEMINI_FREE_API_KEY)


# Paid-tier prices, USD per million tokens (input, output), from the Gemini pricing page (through 2026).
# A model not listed is priced as the writer model.
PAID_PRICES = {"gemini-3.8-flash": (0.75, 3.75)}
ALLOW_PAID_FALLBACK = True  # evals switch this off: a test should never spend


def _paid_cap(task_class: str) -> float:
    return config.PAID_FALLBACK_LIGHT_MONTHLY_USD if task_class == "light" else config.PAID_FALLBACK_HEAVY_MONTHLY_USD


def _paid_spend(task_class: str) -> float:
    from datetime import datetime, timezone
    from .db import PipelineDatabase
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with PipelineDatabase()._conn() as conn:
        row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE task = ? AND substr(created_at, 1, 7) = ?",
                           (f"paid-fallback:{task_class}", month)).fetchone()
    return float(row[0])


def _paid_fallback(system, prompt, schema: Type[T], model: str, task_class: str, why: str) -> T:
    # A light, text-only task first tries the second free model (when enabled after measurement).
    if task_class == "light" and isinstance(prompt, str) and config.GROQ_LIGHT_FALLBACK:
        from . import groq
        if groq.available():
            try:
                result = groq.structured(system, prompt, schema)
                log.info("Free Gemini unavailable (%s); answered by the free Groq model.", why)
                return result
            except groq.GroqError as e:
                log.info("Free Groq model unavailable too (%s).", e)
    if not (ALLOW_PAID_FALLBACK and config.is_configured(config.GEMINI_API_KEY) and _paid_cap(task_class) > 0):
        raise FreeQuotaUnavailable(why)
    if config.GEMINI_API_KEY == config.GEMINI_FREE_API_KEY:
        raise FreeQuotaUnavailable(why + " (GEMINI_API_KEY is the free key, so there is no paid fallback)")
    spent, cap = _paid_spend(task_class), _paid_cap(task_class)
    if spent >= cap:
        raise FreeQuotaUnavailable(f"{why} The paid fallback for {task_class} tasks has reached its "
                                   f"${cap:.2f} monthly cap (${spent:.2f} spent).")
    log.info("Free Gemini unavailable (%s); using the billed key for this %s task ($%.2f of $%.2f this month).",
             why, task_class, spent, cap)
    try:
        result = gemini.structured(system, prompt, schema, model=model, key=config.GEMINI_API_KEY)
    except gemini.GeminiError as e:  # the billed key failed too (its own quota, an outage)
        raise LLMError(f"{why} The paid fallback also failed: {e}") from e
    tokens_in, tokens_out = gemini.last_usage()
    price_in, price_out = PAID_PRICES.get(model, PAID_PRICES["gemini-3.8-flash"])
    cost = round(tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out, 6)
    from .db import PipelineDatabase
    PipelineDatabase().record_llm_usage(f"{model} (paid fallback)", tokens_in, tokens_out, 0, cost,
                                        task=f"paid-fallback:{task_class}")
    return result


def structured(system: str, prompt, schema: Type[T], role: str = "writer", attempts: int = 4,
               task_class: str = "heavy") -> T:
    """Schema-constrained output from the free model ("writer") or the different checking model ("checker").
    task_class "light" (photo review, sound plan) or "heavy" (writing, checking) picks the paid-fallback cap."""
    model = config.FREE_CHECKER_MODEL if role == "checker" else config.FREE_WRITER_MODEL
    if not available():
        return _paid_fallback(system, prompt, schema, model, task_class,
                              "GEMINI_FREE_API_KEY is not set (create it in a Google project without billing).")
    delay = 15.0
    for attempt in range(1, attempts + 1):
        try:
            return gemini.structured(system, prompt, schema, model=model, key=config.GEMINI_FREE_API_KEY)
        except gemini.QuotaExhausted as e:
            return _paid_fallback(system, prompt, schema, model, task_class,
                                  f"The free Gemini quota is used up for now ({model}): {e}")
        except gemini.GeminiError as e:
            if "overloaded" not in str(e).lower() and "503" not in str(e):
                raise LLMError(f"Free model {model} failed: {e}") from e
            if attempt == attempts:
                return _paid_fallback(system, prompt, schema, model, task_class,
                                      f"The free model {model} stayed overloaded")
            log.info("Free model %s busy (attempt %d); retrying in %.0fs.", model, attempt, delay)
            time.sleep(delay)
            delay *= 2
    raise LLMError("unreachable")


def call(system: str, prompt, schema: Type[T], role: str = "writer", task_class: str = "heavy", **claude_kwargs) -> T:
    """The free model when SCRIPT_ENGINE=free, otherwise Claude (llm.structured_call with claude_kwargs)."""
    if enabled():
        return structured(system, prompt, schema, role=role, task_class=task_class)
    from .llm import structured_call
    return structured_call(system, prompt, schema, **claude_kwargs)


def claude_engine() -> bool:
    """Claude is used only when SCRIPT_ENGINE=claude (and a key exists). Having an Anthropic key is not
    a reason to use it: on the free engine every task goes through this module and its capped fallback."""
    if enabled():
        return False
    from .llm import claude_available
    return claude_available()


def ready() -> bool:
    """Can script work run at all on the configured engine?"""
    if enabled():
        return available()
    from .llm import claude_available
    return claude_available()
