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
    # Said once per reason (a configured FREE_TIER_HEAVY=false put this line on every call: 66 of them in
    # production's log); later calls are logged at debug with the running spend.
    level = logging.DEBUG if why in _announced else logging.INFO
    _announced.add(why)
    log.log(level, "Free Gemini unavailable (%s); using the billed key for this %s task ($%.2f of $%.2f this month).",
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


_announced: set = set()  # reasons for using the billed key already said in this process's log
SPARE_COOLDOWN_SECONDS = 3600
_spare_down_until = 0.0  # monotonic time before which a failed spare key is not tried again


def _spare_key() -> str:
    """The spare free key, only while explicitly permitted (see config) and never the billed key."""
    key = config.GEMINI_FREE_API_KEY_SPARE
    if (config.GEMINI_SPARE_KEY_PERMITTED and config.is_configured(key)
            and key not in (config.GEMINI_FREE_API_KEY, config.GEMINI_API_KEY)):
        return key
    return ""


def structured(system: str, prompt, schema: Type[T], role: str = "writer", attempts: int = 4,
               task_class: str = "heavy", timeout: float = None) -> T:
    """Schema-constrained output from the free model ("writer") or the different checking model ("checker").
    task_class "light" (photo review, sound plan) or "heavy" (writing, checking) picks the paid-fallback cap."""
    model = config.FREE_CHECKER_MODEL if role == "checker" else config.FREE_WRITER_MODEL
    if task_class == "light" and isinstance(prompt, str) and config.FREE_LIGHT_MODEL:
        model = config.FREE_LIGHT_MODEL  # its own free limits; photo review (images) stays on the main models
    if not available():
        return _paid_fallback(system, prompt, schema, model, task_class,
                              "GEMINI_FREE_API_KEY is not set (create it in a Google project without billing).")
    if task_class == "heavy" and not config.FREE_TIER_HEAVY:
        return _paid_fallback(system, prompt, schema, model, task_class,
                              "heavy tasks use the billed key here (FREE_TIER_HEAVY=false)")
    wait = {"timeout": timeout} if timeout else {}
    delay = 15.0
    for attempt in range(1, attempts + 1):
        try:
            return gemini.structured(system, prompt, schema, model=model, key=config.GEMINI_FREE_API_KEY, **wait)
        except gemini.QuotaExhausted as e:
            global _spare_down_until
            spare = _spare_key()
            if spare and time.monotonic() >= _spare_down_until:
                try:
                    return gemini.structured(system, prompt, schema, model=model, key=spare, **wait)
                except gemini.GeminiError as e2:
                    # Skipped for a while after failing: production lost 5 minutes on every call to a spare
                    # key that never answered before reaching the billed key.
                    _spare_down_until = time.monotonic() + SPARE_COOLDOWN_SECONDS
                    log.info("Spare free key unavailable too (%s); not tried again for %d min.",
                             e2, SPARE_COOLDOWN_SECONDS // 60)
            return _paid_fallback(system, prompt, schema, model, task_class,
                                  f"The free Gemini quota is used up for now ({model}): {e}")
        except gemini.ConnectionDropped as e:
            # A dropped connection (Richat's documentary check died on one after 8 minutes): send again.
            # It is not a quota or capacity problem, so it never moves the work to the billed key.
            if attempt == attempts:
                raise LLMError(f"Free model {model} failed: {e}") from e
            log.info("Free model %s connection dropped (attempt %d); retrying in %.0fs.", model, attempt, delay)
            time.sleep(delay)
            delay *= 2
        except gemini.RequestTimedOut as e:
            # No answer in time (production's 2026-10-23 run lost its script to one 300 s timeout). Sent
            # again with backoff; an operator-facing call (its own short timeout) gives way instead, and a
            # long task that keeps timing out is treated like an overloaded model: the capped billed key.
            if attempt == attempts:
                if timeout:
                    raise LLMError(f"Free model {model} failed: {e}") from e
                return _paid_fallback(system, prompt, schema, model, task_class,
                                      f"The free model {model} did not answer in time ({attempts} attempts)")
            log.info("Free model %s timed out (attempt %d); retrying in %.0fs.", model, attempt, delay)
            time.sleep(delay)
            delay *= 2
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


INTERACTIVE_TIMEOUT = 45  # seconds; someone is waiting in Telegram


def call(system: str, prompt, schema: Type[T], role: str = "writer", task_class: str = "heavy",
         interactive: bool = False, **claude_kwargs) -> T:
    """The free model when SCRIPT_ENGINE=free, otherwise Claude (llm.structured_call with claude_kwargs).
    interactive: an operator is waiting (/ask, issue triage): one retry and a short request timeout, so a
    stalled free model gives way to the caller's fallback in about a minute instead of five. A timeout is
    not a quota error, so it never triggers the billed fallback."""
    from .llm import structured_call
    if enabled():
        if interactive:
            return structured(system, prompt, schema, role=role, task_class=task_class, attempts=2,
                              timeout=INTERACTIVE_TIMEOUT)
        try:
            return structured(system, prompt, schema, role=role, task_class=task_class)
        except LLMError as e:
            if not claude_fallback_ready():
                raise
            # Last resort, only when switched on (CLAUDE_FALLBACK) and within CLAUDE_MONTHLY_BUDGET_USD,
            # which structured_call enforces: the free and billed Gemini paths both failed.
            log.warning("Gemini failed (%s); using Claude as the last resort for this task.", e)
            return structured_call(system, prompt, schema, **claude_kwargs)
    return structured_call(system, prompt, schema, **claude_kwargs)


def claude_fallback_ready() -> bool:
    """Claude may stand in for a failed Gemini call: switched on, a key, and a monthly cap above zero
    (a cap of 0 means "no cap" to structured_call, so it never enables an uncapped fallback)."""
    if not (config.CLAUDE_FALLBACK and config.CLAUDE_MONTHLY_BUDGET_USD > 0 and ALLOW_PAID_FALLBACK):
        return False
    from .llm import claude_available
    return claude_available()


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
