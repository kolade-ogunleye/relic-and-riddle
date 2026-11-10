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
Temporary overloads (HTTP 503) are retried with jittered exponential backoff; after
CAPACITY_FAILURES_BEFORE_BILLED consecutive 503s or timeouts a long task moves to the capped billed key when
that key can take it. Every request's key, status and seconds go into the call's llm_calls.jsonl line
("attempts"). Free-tier prompts may be used by Google to improve its products: only public research material
is sent.
"""

import contextvars
import logging
import random
import time
from typing import Type, TypeVar

from pydantic import BaseModel

from . import config, gemini, slowdown
from .llm import LLMError, note_served, served_count

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class FreeQuotaUnavailable(LLMError):
    """No free key, or its free quota is used up for now."""


class PaidFallbackFailed(LLMError):
    """The billed key was tried (twice when the failure was transient) and failed: asking again at once repeats
    the same wait (a picture look that failed this way cost about 250 s before its own retry, run audit 2026-10-29)."""


def enabled() -> bool:
    return config.SCRIPT_ENGINE == "free"


def available() -> bool:
    return config.is_configured(config.GEMINI_FREE_API_KEY)


# Paid-tier prices, USD per million tokens (input, output), from the Gemini pricing page (through 2026).
# A model not listed is priced as the writer model.
PAID_PRICES = {"gemini-3.8-flash": (0.75, 3.75)}
ALLOW_PAID_FALLBACK = True  # evals switch this off: a test should never spend
# Free quota only for the calls made inside free_only(), on this thread: the improvement loop's rule draft runs
# beside a production run, and switching the module-wide flag off for it would take the paid fallback away
# from that run's calls too (audit, 2026-10-28)
_free_only: contextvars.ContextVar = contextvars.ContextVar("free_llm_free_only", default=False)


def paid_fallback_allowed() -> bool:
    return ALLOW_PAID_FALLBACK and not _free_only.get()


class free_only:
    """with free_only(): the calls inside use the free quota only; other threads are not affected."""

    def __enter__(self):
        self._token = _free_only.set(True)
        return self

    def __exit__(self, *exc):
        _free_only.reset(self._token)
        return False


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


def _thinking(level: str) -> dict:
    """The thinking keyword only when a level was asked for (a stub without it keeps working)."""
    return {"thinking": level} if level else {}


_thinking_level: contextvars.ContextVar = contextvars.ContextVar("free_llm_thinking", default=None)
# A per-request time limit on the free and the billed key alike (structured(request_timeout=...)): a one-picture
# look waited 120 s per billed try (run audit 2026-10-29). None: each call's own default.
_request_timeout: contextvars.ContextVar = contextvars.ContextVar("free_llm_request_timeout", default=None)


def _limit(timeout) -> dict:
    """The timeout keyword only when one was set (a stub without it keeps working)."""
    return {"timeout": timeout} if timeout else {}


# Each request of the current structured() call, in order: {"key", "status", "seconds"} and "waited" (the
# backoff slept before it). The run record kept only a call's final outcome, so the 2026-11-10 writer failures
# had to be inferred from their total time (WRITER-TIMEOUT diagnosis, finding 4).
_attempts: contextvars.ContextVar = contextvars.ContextVar("free_llm_attempts", default=None)
_waited: contextvars.ContextVar = contextvars.ContextVar("free_llm_waited", default=0.0)


def _overloaded(e: Exception) -> bool:
    return "overloaded" in str(e).lower() or "503" in str(e)


def _status(e: Exception) -> str:
    """A failed request's status as recorded in llm_calls.jsonl."""
    if isinstance(e, gemini.QuotaExhausted):
        return "429"
    if isinstance(e, gemini.CreditExhausted):
        return "402"
    if isinstance(e, gemini.RequestTimedOut):
        return "timeout"
    if isinstance(e, gemini.ConnectionDropped):
        return "dropped"
    if isinstance(e, gemini.EmptyReply):
        return "empty"
    if _overloaded(e):
        return "503"
    return "error" if isinstance(e, gemini.GeminiError) else type(e).__name__


def _ask(key_name: str, *args, **kwargs):
    """gemini.structured, with the request's key ("free", "spare", "billed"), status and seconds noted."""
    started = time.perf_counter()
    status = "ok"
    try:
        return gemini.structured(*args, **kwargs)
    except Exception as e:
        status = _status(e)
        raise
    finally:
        attempts = _attempts.get()
        if attempts is not None:
            entry = {"key": key_name, "status": status, "seconds": round(time.perf_counter() - started, 2)}
            if _waited.get():
                entry["waited"] = round(_waited.get(), 2)
                _waited.set(0.0)
            attempts.append(entry)


def _wait(seconds: float) -> None:
    """Sleep before the next request; the time is noted on that request's attempt."""
    time.sleep(seconds)
    _waited.set(_waited.get() + seconds)


def _backoff(delay: float) -> float:
    """The wait before a retry: the exponential step times a random factor in [1 - BACKOFF_JITTER,
    1 + BACKOFF_JITTER]. Google's troubleshooting guide asks for exponential backoff with jitter on 503, so
    callers that met the same overload do not all ask again at the same moment."""
    return delay * random.uniform(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER)


def _paid_fallback(system, prompt, schema: Type[T], model: str, task_class: str, why: str) -> T:
    thinking = _thinking_level.get()
    request_timeout = _request_timeout.get()
    # A light, text-only task first tries the second free model (when enabled after measurement).
    if task_class == "light" and isinstance(prompt, str) and config.GROQ_LIGHT_FALLBACK:
        from . import groq
        if groq.available():
            try:
                result = groq.structured(system, prompt, schema)
                note_served(f"groq:{config.GROQ_MODEL}")
                log.info("Free Gemini unavailable (%s); answered by the free Groq model.", why)
                return result
            except groq.GroqError as e:
                log.info("Free Groq model unavailable too (%s).", e)
    if not (paid_fallback_allowed() and config.is_configured(config.GEMINI_API_KEY) and _paid_cap(task_class) > 0):
        raise FreeQuotaUnavailable(why)
    if config.GEMINI_API_KEY == config.GEMINI_FREE_API_KEY:
        raise FreeQuotaUnavailable(why + " (GEMINI_API_KEY is the free key, so there is no paid fallback)")
    spent, cap = _paid_spend(task_class), _paid_cap(task_class)
    if spent >= cap:
        from . import provider_status
        provider_status.note_capped("gemini-billed", f"the ${cap:.2f} monthly cap for {task_class} tasks is spent",
                                    until=provider_status.next_month())
        raise FreeQuotaUnavailable(f"{why} The paid fallback for {task_class} tasks has reached its "
                                   f"${cap:.2f} monthly cap (${spent:.2f} spent).")
    # Said once per reason (a configured FREE_TIER_HEAVY=false put this line on every call: 66 of them in
    # production's log); later calls are logged at debug with the running spend.
    level = logging.DEBUG if why in _announced else logging.INFO
    _announced.add(why)
    log.log(level, "Free Gemini unavailable (%s); using the billed key for this %s task ($%.2f of $%.2f this month).",
            why, task_class, spent, cap)
    # Two tries on the billed key (operator, 2026-10-25) before the task leaves Gemini for the Claude standby:
    # a timeout, a dropped connection or an overloaded model is sent once more; spent credit or the key's own
    # quota is not, as a second try cannot change it.
    for billed_try in (1, 2):
        try:
            result = _ask("billed", system, prompt, schema, model=model, key=config.GEMINI_API_KEY,
                          **_thinking(thinking), **_limit(request_timeout))
            break
        except gemini.CreditExhausted as e:  # prepaid credit spent: the same wait as the cap, until the operator tops up
            from . import provider_status
            provider_status.note_capped("gemini-billed", "its prepaid credit is depleted (HTTP 402); top up the project's "
                                        "billing in Google AI Studio")
            raise FreeQuotaUnavailable(f"{why} The paid fallback cannot run either: {e}") from e
        except gemini.GeminiError as e:  # the billed key failed too (its own quota, an outage)
            # A reply with no output (EmptyReply) is sent once more too: another sample usually answers.
            transient = isinstance(e, (gemini.RequestTimedOut, gemini.ConnectionDropped, gemini.EmptyReply)) or \
                _overloaded(e)
            if billed_try == 2 or not transient or isinstance(e, gemini.QuotaExhausted):
                raise PaidFallbackFailed(f"{why} The paid fallback also failed: {e}") from e
            log.info("Billed Gemini call failed (%s); trying once more.", type(e).__name__)
            _wait(10)
    from . import provider_status
    provider_status.clear("gemini-billed")  # a billed call went through: a depleted-credit notice is over
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
# After the free key's 429 on a model, light calls on that model skip it for a while, as the spare key is skipped
# after it fails: every light call hit the free 429 first and then the billed key (run audit 2026-10-29). Heavy
# calls still ask the free key first: a free script or fact check is worth the wait of a refused request.
FREE_COOLDOWN_SECONDS = 600
_free_down_until: dict = {}  # (free key, model) -> monotonic time before which light calls skip the free key


def free_cooling(model: str) -> bool:
    """Whether the free key refused `model` (HTTP 429) within FREE_COOLDOWN_SECONDS."""
    return time.monotonic() < _free_down_until.get((config.GEMINI_FREE_API_KEY, model), 0.0)


def _billed_ready(task_class: str) -> bool:
    """Whether the billed key may take a `task_class` call (its monthly spend is checked when it does): only then
    is the free key skipped while it cools down; with nothing behind it, the free key is still asked."""
    return (paid_fallback_allowed() and config.is_configured(config.GEMINI_API_KEY)
            and config.GEMINI_API_KEY != config.GEMINI_FREE_API_KEY and _paid_cap(task_class) > 0)


def _billed_can_take(task_class: str) -> bool:
    """_billed_ready and this month's spend still under the cap: only then does an overloaded free model give the
    call to the billed key early; otherwise the free key keeps its retries (a spent cap would only fail sooner)."""
    return _billed_ready(task_class) and _paid_spend(task_class) < _paid_cap(task_class)


def _tally(busy: int, timed_out: int) -> str:
    """What the free key's capacity failures were, e.g. "(2 attempts): 1 busy (HTTP 503), 1 timed out"."""
    parts = ([f"{busy} busy (HTTP 503)"] if busy else []) + ([f"{timed_out} timed out"] if timed_out else [])
    return f"({busy + timed_out} attempts): " + ", ".join(parts)


def _spare_key() -> str:
    """The spare free key, only while explicitly permitted (see config) and never the billed key."""
    key = config.GEMINI_FREE_API_KEY_SPARE
    if (config.GEMINI_SPARE_KEY_PERMITTED and config.is_configured(key)
            and key not in (config.GEMINI_FREE_API_KEY, config.GEMINI_API_KEY)):
        return key
    return ""


def structured(system: str, prompt, schema: Type[T], role: str = "writer", attempts: int = 4,
               task_class: str = "heavy", timeout: float = None, thinking: str = None,
               request_timeout: float = None) -> T:
    """Schema-constrained output from the free model ("writer") or the different checking model ("checker").
    task_class "light" (photo review, sound plan) or "heavy" (writing, checking) picks the paid-fallback cap.
    `timeout`: an operator is waiting (a timeout gives way, never reaches the billed key). `request_timeout`: each
    request's limit on the free and the billed key, the usual fallbacks unchanged (a one-picture look: 45 s).
    Every call is recorded in the current run's llm_calls.jsonl (src/ops.py) with its length and tokens."""
    from .llm import current_task
    from .ops import record_llm_call
    model = config.FREE_CHECKER_MODEL if role == "checker" else config.FREE_WRITER_MODEL
    if task_class == "light" and isinstance(prompt, str) and config.FREE_LIGHT_MODEL:
        model = config.FREE_LIGHT_MODEL  # its own free limits; photo review (images) stays on the main models
    started = time.perf_counter()
    token = _thinking_level.set(thinking)
    limit_token = _request_timeout.set(request_timeout)
    tried = []
    attempts_token, waited_token = _attempts.set(tried), _waited.set(0.0)
    noted = served_count()
    try:
        result = _structured_on(model, system, prompt, schema, role, attempts, task_class, timeout, thinking)
        if served_count() == noted:   # a Gemini key answered (free, spare or billed: all on `model`)
            note_served(model)
    except Exception as e:
        record_llm_call(current_task(), model, time.perf_counter() - started, outcome=type(e).__name__, role=role,
                        attempts=tried)
        raise
    finally:
        _thinking_level.reset(token)
        _request_timeout.reset(limit_token)
        _attempts.reset(attempts_token)
        _waited.reset(waited_token)
    tokens_in, tokens_out = gemini.last_usage()
    record_llm_call(current_task(), model, time.perf_counter() - started, tokens_in=tokens_in or 0,
                    tokens_out=tokens_out or 0, role=role, attempts=tried)
    return result


def _structured_on(model: str, system: str, prompt, schema: Type[T], role: str, attempts: int,
                   task_class: str, timeout: float, thinking: str = None) -> T:
    if not available():
        return _paid_fallback(system, prompt, schema, model, task_class,
                              "GEMINI_FREE_API_KEY is not set (create it in a Google project without billing).")
    if task_class == "heavy" and not config.FREE_TIER_HEAVY:
        return _paid_fallback(system, prompt, schema, model, task_class,
                              "heavy tasks use the billed key here (FREE_TIER_HEAVY=false)")
    wait = _limit(timeout or _request_timeout.get())

    def refused(e) -> T:   # the free key's quota: the spare key (while it answers), then the billed key
        global _spare_down_until
        spare = _spare_key()
        if spare and time.monotonic() >= _spare_down_until:
            try:
                return _ask("spare", system, prompt, schema, model=model, key=spare, **_thinking(thinking), **wait)
            except gemini.GeminiError as e2:
                # Skipped for a while after failing: production lost 5 minutes on every call to a spare
                # key that never answered before reaching the billed key.
                _spare_down_until = time.monotonic() + SPARE_COOLDOWN_SECONDS
                log.info("Spare free key unavailable too (%s); not tried again for %d min.",
                         e2, SPARE_COOLDOWN_SECONDS // 60)
        return _paid_fallback(system, prompt, schema, model, task_class,
                              f"The free Gemini quota is used up for now ({model}): {e}")

    spare_up = bool(_spare_key()) and time.monotonic() >= _spare_down_until
    if task_class == "light" and free_cooling(model) and (spare_up or _billed_ready(task_class)):
        # Said once per reason by _paid_fallback; the free key is asked again when the cooldown is over.
        return refused(f"refused within the last {FREE_COOLDOWN_SECONDS // 60} min (HTTP 429), not asked again yet")
    delay = 15.0
    # Consecutive capacity failures (503 or timeout) and the totals of each: the hand-over to the billed key counted
    # attempts, so one 503 and then one timeout gave up as "2 attempts" after a single timeout (2026-11-10).
    streak = busy = timed_out = 0
    for attempt in range(1, attempts + 1):
        try:
            return _ask("free", system, prompt, schema, model=model, key=config.GEMINI_FREE_API_KEY,
                        **_thinking(thinking), **wait)
        except gemini.QuotaExhausted as e:
            if task_class == "light" and (spare_up or _billed_ready(task_class)):   # only with a key to go to
                log.info("Free Gemini quota refused %s (HTTP 429); light calls use the %s key for %d min.",
                         model, "spare" if spare_up else "billed", FREE_COOLDOWN_SECONDS // 60)
                _free_down_until[(config.GEMINI_FREE_API_KEY, model)] = time.monotonic() + FREE_COOLDOWN_SECONDS
            return refused(e)
        except gemini.ConnectionDropped as e:
            # A dropped connection (Richat's documentary check died on one after 8 minutes): send again.
            # It is not a quota or capacity problem, so it never moves the work to the billed key.
            streak = 0
            if attempt == attempts:
                raise LLMError(f"Free model {model} failed: {e}") from e
            pause = _backoff(delay)
            log.info("Free model %s connection dropped (attempt %d); retrying in %.0fs.", model, attempt, pause)
            slowdown.note("the free AI model's connection dropped, retrying")
            _wait(pause)
            delay *= 2
        except gemini.RequestTimedOut as e:
            # No answer in time (production's 2026-10-23 run lost its script to one 300 s timeout). Sent
            # again with backoff; an operator-facing call (its own short timeout) gives way instead, and a
            # long task that keeps timing out is treated like an overloaded model: the capped billed key.
            # After TIMEOUTS_BEFORE_BILLED timeouts, not all `attempts`: each costs about five minutes, and a
            # development run spent 18 minutes on one step waiting through them (operator, 2026-10-25). With
            # the billed key able to take it, CAPACITY_FAILURES_BEFORE_BILLED 503s or timeouts in a row are enough.
            streak, timed_out = streak + 1, timed_out + 1
            if timeout:
                if attempt == attempts:
                    raise LLMError(f"Free model {model} failed: {e}") from e
            elif (attempt == attempts or timed_out >= TIMEOUTS_BEFORE_BILLED
                  or (streak >= CAPACITY_FAILURES_BEFORE_BILLED and _billed_can_take(task_class))):
                return _paid_fallback(system, prompt, schema, model, task_class,
                                      f"The free model {model} did not answer in time {_tally(busy, timed_out)}")
            pause = _backoff(delay)
            log.info("Free model %s timed out (attempt %d); retrying in %.0fs.", model, attempt, pause)
            slowdown.note("the free AI model is slow to answer, retrying")
            _wait(pause)
            delay *= 2
        except gemini.GeminiError as e:
            if not _overloaded(e):
                raise LLMError(f"Free model {model} failed: {e}") from e
            # Overloaded (HTTP 503): free capacity is short, often for every request at once (2026-11-10: the free
            # tier answered 503 to a one-line request while the billed key served the writer's). Two in a row
            # hand the call to the capped billed key when it can take it; with nothing behind the free key, it
            # keeps its retries.
            streak, busy = streak + 1, busy + 1
            if attempt == attempts or (streak >= CAPACITY_FAILURES_BEFORE_BILLED and _billed_can_take(task_class)):
                return _paid_fallback(system, prompt, schema, model, task_class,
                                      f"The free model {model} stayed overloaded {_tally(busy, timed_out)}")
            pause = _backoff(delay)
            log.info("Free model %s busy (attempt %d); retrying in %.0fs.", model, attempt, pause)
            slowdown.note("the free AI model is busy, retrying")
            _wait(pause)
            delay *= 2
    raise LLMError("unreachable")


INTERACTIVE_TIMEOUT = 45  # seconds; someone is waiting in Telegram
TIMEOUTS_BEFORE_BILLED = 2  # a long task's free-model timeouts before the capped billed key takes it
# 503s or timeouts in a row before a long task goes to the capped billed key, when that key can take it (QF-98)
CAPACITY_FAILURES_BEFORE_BILLED = 2
BACKOFF_JITTER = 0.5  # each retry wait is its exponential step (15, 30, 60 s) times a factor in [0.5, 1.5]


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
                              timeout=INTERACTIVE_TIMEOUT, thinking=claude_kwargs.get("effort"))
        try:
            return structured(system, prompt, schema, role=role, task_class=task_class, thinking=claude_kwargs.get("effort"))
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
    if not (config.CLAUDE_FALLBACK and config.CLAUDE_MONTHLY_BUDGET_USD > 0 and paid_fallback_allowed()):
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
