"""
src/free_llm.py
===============
Zero-cost text model for script research, writing and fact checks: Gemini on the free tier.

It uses only GEMINI_FREE_API_KEY, which must come from a Google project with billing turned off, so
calls can never be charged. When that key is missing or its free daily quota is used up, calls fail
with FreeQuotaUnavailable and the run stops with an alert; it never falls back to a paid key.
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


def structured(system: str, prompt: str, schema: Type[T], role: str = "writer", attempts: int = 4) -> T:
    """Schema-constrained output from the free model ("writer") or the different checking model ("checker")."""
    if not available():
        raise FreeQuotaUnavailable("GEMINI_FREE_API_KEY is not set (create it in a Google project without billing).")
    model = config.FREE_CHECKER_MODEL if role == "checker" else config.FREE_WRITER_MODEL
    delay = 15.0
    for attempt in range(1, attempts + 1):
        try:
            return gemini.structured(system, prompt, schema, model=model, key=config.GEMINI_FREE_API_KEY)
        except gemini.QuotaExhausted as e:
            raise FreeQuotaUnavailable(f"The free Gemini quota is used up for now ({model}): {e}") from e
        except gemini.GeminiError as e:
            if attempt == attempts or "overloaded" not in str(e).lower() and "503" not in str(e):
                raise LLMError(f"Free model {model} failed: {e}") from e
            log.info("Free model %s busy (attempt %d); retrying in %.0fs.", model, attempt, delay)
            time.sleep(delay)
            delay *= 2
    raise LLMError("unreachable")


def call(system: str, prompt: str, schema: Type[T], role: str = "writer", **claude_kwargs) -> T:
    """The free model when SCRIPT_ENGINE=free, otherwise Claude (llm.structured_call with claude_kwargs)."""
    if enabled():
        return structured(system, prompt, schema, role=role)
    from .llm import structured_call
    return structured_call(system, prompt, schema, **claude_kwargs)


def ready() -> bool:
    """Can script work run at all on the configured engine?"""
    if enabled():
        return available()
    from .llm import claude_available
    return claude_available()
