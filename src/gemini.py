"""
src/gemini.py
=============
Minimal client for Google's Gemini Interactions API (v1beta/interactions), used as the
free, independent fallback for the second fact check.

  research()    Google Search grounding; returns (text, cited sources)
  structured()  JSON output constrained to a Pydantic schema

Docs: ai.google.dev/gemini-api/docs/google-search, .../structured-output, ai.google.dev/api/interactions-api
"""

import json
import logging
from typing import Any, Dict, List, Tuple, Type, TypeVar

import requests
from pydantic import BaseModel

from .config import GEMINI_API_KEY, GEMINI_MODEL, is_configured
from .models import Source

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


class GeminiError(RuntimeError):
    pass


def available() -> bool:
    return is_configured(GEMINI_API_KEY)


def _post(body: Dict[str, Any], timeout: float = 300) -> Dict[str, Any]:
    if not available():
        raise GeminiError("GEMINI_API_KEY is not configured.")
    try:
        resp = requests.post(URL, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                             json=body, timeout=timeout)
    except requests.RequestException as e:
        raise GeminiError(f"Gemini request failed: {e}") from e
    if resp.status_code == 429:
        raise GeminiError("Gemini quota exceeded (HTTP 429).")
    try:
        data = resp.json()
    except ValueError:
        raise GeminiError(f"Gemini returned HTTP {resp.status_code} with no JSON.")
    if not resp.ok or data.get("errors"):
        raise GeminiError(f"Gemini error HTTP {resp.status_code}: {data.get('errors') or data.get('error') or data}")
    if data.get("status") not in ("completed",):
        raise GeminiError(f"Gemini interaction ended with status '{data.get('status')}'.")
    return data


def _text_blocks(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for step in data.get("steps", []) if step.get("type") == "model_output"
            for c in step.get("content", []) if c.get("type") == "text"]


def research(system: str, prompt: str) -> Tuple[str, List[Source]]:
    """Search-grounded answer; sources come from the response's url_citation annotations."""
    data = _post({"model": GEMINI_MODEL, "input": f"{system}\n\n{prompt}", "tools": [{"type": "google_search"}]})
    blocks = _text_blocks(data)
    text = "".join(b.get("text", "") for b in blocks).strip()
    seen, sources = set(), []
    for b in blocks:
        for a in b.get("annotations") or []:
            if a.get("type") == "url_citation" and a.get("url") and a["url"] not in seen:
                seen.add(a["url"])
                sources.append(Source(title=a.get("title") or a["url"], url=a["url"]))
    if not text:
        raise GeminiError("Gemini research returned no text.")
    return text, sources


def _inline_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Gemini accepts a JSON Schema subset; inline $defs/$ref and drop unsupported keys."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title", "default")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def structured(system: str, prompt: str, schema: Type[T]) -> T:
    data = _post({
        "model": GEMINI_MODEL,
        "input": f"{system}\n\n{prompt}",
        "response_format": {"type": "text", "mime_type": "application/json",
                            "schema": _inline_refs(schema.model_json_schema())},
    })
    text = "".join(b.get("text", "") for b in _text_blocks(data)).strip()
    try:
        return schema.model_validate(json.loads(text))
    except (ValueError, json.JSONDecodeError) as e:
        raise GeminiError(f"Gemini structured output did not match the schema: {e}") from e
