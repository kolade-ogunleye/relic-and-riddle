"""
src/groq.py
===========
Second free model for LIGHT, TEXT-ONLY tasks (sound plan, /ask, issue sorting, rule drafting, callouts,
image search terms) when the free Gemini quota is used up, before any paid fallback.

Groq's free plan, OpenAI-compatible chat completions with strict structured output (constrained
decoding: the reply always matches the schema) on openai/gpt-oss-120b. Docs:
https://console.groq.com/docs/structured-outputs (strict mode: every property required, nullable for
optional, additionalProperties false on every object). Free-plan limits are per model and shown in
the Groq console; a 429 is treated like an exhausted quota so the caller moves on.

It is off until measured: GROQ_LIGHT_FALLBACK=true only after scripts/compare_light_engines.py shows
its answers hold up against Gemini's on our own tasks. Photo review never comes here (no images).
"""

import copy
import json
import logging
from typing import Any, Dict, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from . import config

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqError(Exception):
    pass


class GroqQuota(GroqError):
    pass


def available() -> bool:
    return config.is_configured(config.GROQ_API_KEY)


def strict_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """Pydantic's JSON schema made strict-mode compliant: every property required (optional ones
    become nullable), no additional properties, no defaults or titles."""
    schema = copy.deepcopy(model.model_json_schema())

    def fix(node):
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if node.get("type") == "object" or "properties" in node:
                props = node.get("properties", {})
                required = set(node.get("required", []))
                for name, prop in props.items():
                    if name not in required and "anyOf" not in prop and isinstance(prop.get("type"), str):
                        prop["type"] = [prop["type"], "null"]
                node["required"] = list(props)
                node["additionalProperties"] = False
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
    fix(schema)
    return schema


def structured(system: str, prompt: str, schema: Type[T], model: str = "") -> T:
    if not available():
        raise GroqError("GROQ_API_KEY is not set")
    if not isinstance(prompt, str):
        raise GroqError("Groq is used for text-only tasks")
    body = {
        "model": model or config.GROQ_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema.__name__.strip("_") or "result", "strict": True,
                                            "schema": strict_schema(schema)}},
        "temperature": 0.2,
    }
    try:
        resp = requests.post(URL, json=body, timeout=90,
                             headers={"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"})
    except requests.RequestException as e:
        raise GroqError(f"Groq request failed: {type(e).__name__}") from None
    if resp.status_code == 429:
        raise GroqQuota("Groq free-plan limit reached for now")
    try:
        data = resp.json()
    except ValueError:
        raise GroqError(f"Groq HTTP {resp.status_code}") from None
    if not resp.ok:
        raise GroqError(f"Groq HTTP {resp.status_code}: {((data.get('error') or {}).get('message') or '')[:200]}")
    try:
        content = data["choices"][0]["message"]["content"]
        result = schema.model_validate(json.loads(content))
    except (KeyError, IndexError, ValueError, ValidationError) as e:
        raise GroqError(f"Groq returned an unusable answer: {e}") from None
    usage = data.get("usage") or {}
    try:
        from .db import PipelineDatabase
        PipelineDatabase().record_llm_usage(f"{body['model']} (groq free)", usage.get("prompt_tokens", 0),
                                            usage.get("completion_tokens", 0), 0, 0.0, task="groq-free:light")
    except Exception as e:  # usage records are bookkeeping; the answer stands
        log.debug("Could not record Groq usage: %s", e)
    return result
