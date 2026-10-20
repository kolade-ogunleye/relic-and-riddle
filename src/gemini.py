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
from typing import Any, Dict, List, Tuple, Type, TypeVar, Union

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


class QuotaExhausted(GeminiError):
    """The key's quota (free-tier daily limit) is used up."""


def _post(body: Dict[str, Any], timeout: float = 300, key: str = None) -> Dict[str, Any]:
    key = key if key is not None else GEMINI_API_KEY
    if not is_configured(key):
        raise GeminiError("No Gemini API key is configured for this call.")
    try:
        resp = requests.post(URL, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                             json=body, timeout=timeout)
    except requests.RequestException as e:
        raise GeminiError(f"Gemini request failed: {e}") from e
    if resp.status_code == 429:
        hint = (" Google Search grounding is not available on the Gemini free tier for Gemini 3 models; enable "
                "billing on the key's Google Cloud project (5,000 grounded searches/month are then free)."
                if body.get("tools") else "")
        raise QuotaExhausted("Gemini quota exceeded (HTTP 429)." + hint)
    if resp.status_code == 503:
        raise GeminiError("Gemini is temporarily overloaded (HTTP 503); try again later.")
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
    """Search-grounded answer; sources come from the response's url_citation annotations or grounded text."""
    for attempt in (1, 2):  # a grounded answer occasionally comes back without citations: ask once more
        data = _post({"model": GEMINI_MODEL, "input": f"{system}\n\n{prompt}", "tools": [{"type": "google_search"}]})
        blocks = _text_blocks(data)
        text = "".join(b.get("text", "") for b in blocks).strip()
        seen, sources = set(), []
        for b in blocks:
            for a in b.get("annotations") or []:
                if a.get("type") == "url_citation" and a.get("url"):
                    url = _resolve(a["url"])
                    if url not in seen:
                        seen.add(url)
                        sources.append(Source(title=a.get("title") or url, url=url))
        if not sources:
            for step in data.get("steps", []):
                for c in step.get("content", []):
                    for a in c.get("annotations") or []:
                        if a.get("url"):
                            url = _resolve(a["url"])
                            if url not in seen:
                                seen.add(url)
                                sources.append(Source(title=a.get("title") or url, url=url))
        if not sources and text:
            import re
            for m in re.finditer(r'https?://[^\s)\]">]+', text):
                url = m.group(0).rstrip(".,;")
                if url not in seen:
                    seen.add(url)
                    sources.append(Source(title=url, url=url))
        if text and sources:
            break
    if not sources and is_configured(GEMINI_API_KEY):
        try:
            gc_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
            gc_body = {
                "contents": [{"parts": [{"text": f"{system}\n\n{prompt}"}]}],
                "tools": [{"googleSearch": {}}]
            }
            gc_resp = requests.post(gc_url, json=gc_body, timeout=60)
            if gc_resp.ok:
                gc_data = gc_resp.json()
                cands = gc_data.get("candidates", [])
                if cands:
                    cand = cands[0]
                    gc_text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
                    if gc_text:
                        text = gc_text
                    gm = cand.get("groundingMetadata", {})
                    for chunk in gm.get("groundingChunks", []):
                        w = chunk.get("web", {})
                        u = w.get("uri")
                        if u:
                            r_u = _resolve(u)
                            if r_u not in seen:
                                seen.add(r_u)
                                sources.append(Source(title=w.get("title") or r_u, url=r_u))
        except Exception as e:
            log.debug("generateContent grounding fallback error: %s", e)
    if not text:
        raise GeminiError("Gemini research returned no text.")
    return text, sources


REDIRECT_HOST = "vertexaisearch.cloud.google.com"


def _resolve(url: str) -> str:
    """Grounding citations are Google redirect links; the real page address is in the redirect's
    Location header (read without following it, so the target page is not fetched)."""
    from urllib.parse import urlparse
    if urlparse(url).hostname != REDIRECT_HOST:
        return url
    try:
        resp = requests.head(url, allow_redirects=False, timeout=10)
        target = resp.headers.get("Location", "")
        return target if target.startswith("https://") or target.startswith("http://") else url
    except requests.RequestException:
        return url


def _inline_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Gemini accepts a JSON Schema subset; inline $defs/$ref and drop unsupported keys."""
    defs = schema.get("$defs", {})

    def walk(node, field_names: bool = False):
        if isinstance(node, dict):
            if field_names:  # a "properties" map: its keys are field names (a field may be called "title")
                return {k: walk(v) for k, v in node.items()}
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v, field_names=(k == "properties")) for k, v in node.items()
                    if k not in ("$defs", "title", "default")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def structured(system: str, prompt: Union[str, list], schema: Type[T], model: str = None, key: str = None) -> T:
    if isinstance(prompt, list):
        parts = []
        for b in prompt:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append({"text": b.get("text", "")})
                elif b.get("type") == "image":
                    src = b.get("source", {})
                    parts.append({
                        "inline_data": {
                            "mime_type": src.get("media_type", "image/jpeg"),
                            "data": src.get("data", "")
                        }
                    })
            elif isinstance(b, str):
                parts.append({"text": b})
        target_key = key if key is not None else GEMINI_API_KEY
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={target_key}"
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": _inline_refs(schema.model_json_schema())
            }
        }
        try:
            resp = requests.post(url, json=body, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return schema.model_validate(json.loads(text))
            log.warning("Gemini multimodal generateContent failed (%d: %s); falling back to text.", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("Gemini multimodal call error: %s; falling back to text.", e)
        # fallback prompt string
        prompt = "\n".join(b.get("text", "") for b in prompt if isinstance(b, dict) and b.get("type") == "text") or str(prompt)

    data = _post({
        "model": model or GEMINI_MODEL,
        "input": f"{system}\n\n{prompt}",
        "response_format": {"type": "text", "mime_type": "application/json",
                            "schema": _inline_refs(schema.model_json_schema())},
    }, key=key)
    text = "".join(b.get("text", "") for b in _text_blocks(data)).strip()
    try:
        return schema.model_validate(json.loads(text))
    except (ValueError, json.JSONDecodeError) as e:
        raise GeminiError(f"Gemini structured output did not match the schema: {e}") from e
