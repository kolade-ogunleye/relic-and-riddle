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
import threading
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


class ConnectionDropped(GeminiError):
    """The connection failed or closed before a response (not a timeout): sending again is safe."""


def available() -> bool:
    return is_configured(GEMINI_API_KEY)


class RequestTimedOut(GeminiError):
    """No answer within the request's time limit (the model may be slow or overloaded)."""


class CreditExhausted(GeminiError):
    """HTTP 402: the key's prepaid credit is spent (2026-10-24, the billed key); nothing works until a top-up."""


class QuotaExhausted(GeminiError):
    """The key's quota (free-tier daily limit) is used up."""


class EmptyReply(GeminiError):
    """HTTP 200 with no answer in it (run audit 2026-10-29: the billed key's "did not match the schema: 'parts'"):
    the message says why, from the reply's finishReason or blockReason. Another sample may answer."""


def _no_output_reason(body: Dict[str, Any]) -> str:
    """Why a generateContent reply carries no text: its finish or block reason and what its tokens went on."""
    cand = ((body.get("candidates") or [{}])[0]) if isinstance(body, dict) else {}
    meta = (body.get("usageMetadata") or {}) if isinstance(body, dict) else {}
    said = [f"finishReason {cand.get('finishReason')}" if cand.get("finishReason") else "",
            f"blockReason {(body.get('promptFeedback') or {}).get('blockReason')}"
            if (body.get("promptFeedback") or {}).get("blockReason") else "",
            f"{meta.get('thoughtsTokenCount')} thinking tokens" if meta.get("thoughtsTokenCount") else "",
            "no candidates" if not body.get("candidates") else ""]
    return ", ".join(s for s in said if s) or "no reason given"


def _post(body: Dict[str, Any], timeout: float = 300, key: str = None) -> Dict[str, Any]:
    key = key if key is not None else GEMINI_API_KEY
    if not is_configured(key):
        raise GeminiError("No Gemini API key is configured for this call.")
    try:
        resp = requests.post(URL, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                             json=body, timeout=timeout)
    except requests.Timeout as e:  # the caller's own limit, not a dropped line
        raise RequestTimedOut(f"Gemini request failed: {e}") from e
    except requests.ConnectionError as e:
        raise ConnectionDropped(f"Gemini request failed: {e}") from e
    except requests.RequestException as e:
        raise GeminiError(f"Gemini request failed: {e}") from e
    if resp.status_code == 429:
        hint = (" Google Search grounding is not available on the Gemini free tier for Gemini 3 models; enable "
                "billing on the key's Google Cloud project (5,000 grounded searches/month are then free)."
                if body.get("tools") else "")
        raise QuotaExhausted("Gemini quota exceeded (HTTP 429)." + hint)
    if resp.status_code == 402:  # prepaid billing: the key's credit is spent until the operator tops it up
        raise CreditExhausted("The Gemini key's prepaid credit is depleted (HTTP 402); top up the project's "
                              "billing in Google AI Studio.")
    if resp.status_code == 503:
        raise GeminiError("Gemini is temporarily overloaded (HTTP 503); try again later.")
    try:
        data = resp.json()
    except ValueError:
        raise GeminiError(f"Gemini returned HTTP {resp.status_code} with no JSON.")
    if isinstance(data, list):  # some errors arrive as a one-element list, [{"error": {...}}] (2026-10-24: the
        data = next((d for d in data if isinstance(d, dict)), {"error": data})  # billed key's failure read as AttributeError)
    if not isinstance(data, dict):
        raise GeminiError(f"Gemini error HTTP {resp.status_code}: {str(data)[:300]}")
    if not resp.ok or data.get("errors"):
        raise GeminiError(f"Gemini error HTTP {resp.status_code}: {data.get('errors') or data.get('error') or data}")
    if data.get("status") not in ("completed",):
        raise GeminiError(f"Gemini interaction ended with status '{data.get('status')}'.")
    return data


_usage = threading.local()


def _set_usage(input_tokens: int, output_tokens: int) -> None:
    _usage.value = (int(input_tokens or 0), int(output_tokens or 0))


def last_usage() -> tuple:
    """(input tokens, output tokens) of this thread's last structured call, for cost accounting."""
    return getattr(_usage, "value", (0, 0))


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
        # URLs the model wrote into its text are not sources: only citations the search
        # returned count (a claim's source must be a page the search actually found).
        if text and sources:
            break
    if not sources and is_configured(GEMINI_API_KEY):
        try:
            gc_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
            gc_body = {
                "contents": [{"parts": [{"text": f"{system}\n\n{prompt}"}]}],
                "tools": [{"googleSearch": {}}]
            }
            gc_resp = requests.post(gc_url, headers={"x-goog-api-key": GEMINI_API_KEY}, json=gc_body, timeout=60)
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


def vision_timeout(n_images: int) -> float:
    """Seconds allowed for a vision request: 60 plus 12 per picture, between 120 and 600."""
    return float(min(600, max(120, 60 + 12 * n_images)))


THINKING_LEVELS = {"low": "low", "medium": "medium"}  # a caller's effort -> Gemini 3 thinkingLevel; "high" is the default


def structured(system: str, prompt: Union[str, list], schema: Type[T], model: str = None, key: str = None,
               timeout: float = None, thinking: str = None) -> T:
    """`thinking`: the caller's effort ("low"/"medium"); on the vision call it becomes generationConfig
    .thinkingConfig.thinkingLevel, since by default the model thinks at length (a 10-beat scene plan spent
    20,000 output tokens and 2.5 minutes thinking, 2026-10-24). A model that rejects the field is called
    again without it."""
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
        if not is_configured(target_key):
            raise GeminiError("No Gemini API key is configured for this call.")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model or GEMINI_MODEL}:generateContent"
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": _inline_refs(schema.model_json_schema())
            }
        }
        level = THINKING_LEVELS.get((thinking or "").lower())
        if level:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": level}
        # Images are the point of this call: if it fails, the caller learns it failed. A text-only
        # retry would answer questions about pictures it never saw.
        # The time allowed grows with the pictures sent: a 20-photo scene plan timed out at a flat 120 s.
        n_images = sum(1 for part in parts if "inline_data" in part)

        def post(payload):
            try:
                return requests.post(url, headers={"x-goog-api-key": target_key, "Content-Type": "application/json"},
                                     json=payload, timeout=timeout or vision_timeout(n_images))
            except requests.Timeout as e:
                raise RequestTimedOut(f"Gemini vision request failed: {e}") from e
            except requests.ConnectionError as e:  # a dropped line, not a slow answer: safe to send again
                raise ConnectionDropped(f"Gemini vision request failed: {e}") from e
            except requests.RequestException as e:
                raise GeminiError(f"Gemini vision request failed: {e}") from e
        resp = post(body)
        if resp.status_code == 400 and level and "thinking" in resp.text.lower():
            log.info("%s does not take thinkingLevel; sent again without it.", model or GEMINI_MODEL)
            del body["generationConfig"]["thinkingConfig"]
            resp = post(body)
        if resp.status_code == 429:
            raise QuotaExhausted("Gemini quota exceeded (HTTP 429).")
        if resp.status_code == 503:
            raise GeminiError("Gemini is temporarily overloaded (HTTP 503); try again later.")
        if resp.status_code != 200:
            raise GeminiError(f"Gemini vision call failed (HTTP {resp.status_code}): {resp.text[:200]}")
        try:
            body_json = resp.json()
            meta = body_json.get("usageMetadata") or {}
            _set_usage(meta.get("promptTokenCount", 0),
                       meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0))
        except (AttributeError, ValueError) as e:
            raise GeminiError(f"Gemini vision reply was not JSON: {e}") from e
        # The answer's text parts, a thought part aside; a reply with none says why (it was logged only as
        # "did not match the schema: 'parts'", run audit 2026-10-29).
        cands = body_json.get("candidates") or [{}]
        parts = ((cands[0] or {}).get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
        if not text:
            why = _no_output_reason(body_json)
            log.info("Gemini vision reply from %s had no output (%s).", model or GEMINI_MODEL, why)
            raise EmptyReply(f"Gemini vision reply had no output ({why}).")
        try:
            return schema.model_validate(json.loads(text))
        except ValueError as e:
            raise GeminiError(f"Gemini vision output did not match the schema: {e}") from e

    data = _post({
        "model": model or GEMINI_MODEL,
        "input": f"{system}\n\n{prompt}",
        "response_format": {"type": "text", "mime_type": "application/json",
                            "schema": _inline_refs(schema.model_json_schema())},
    }, key=key, timeout=timeout or 300)
    usage = data.get("usage") or {}
    _set_usage(usage.get("total_input_tokens", 0), usage.get("total_output_tokens", 0))
    text = "".join(b.get("text", "") for b in _text_blocks(data)).strip()
    if not text:   # completed, but no text step: said as such, not as a schema mismatch
        steps = ", ".join(sorted({str(s.get("type")) for s in data.get("steps", []) if isinstance(s, dict)})) or "none"
        log.info("Gemini reply from %s had no output (steps: %s).", model or GEMINI_MODEL, steps)
        raise EmptyReply(f"Gemini reply had no output (steps: {steps}).")
    try:
        return schema.model_validate(json.loads(text))
    except (ValueError, json.JSONDecodeError) as e:
        raise GeminiError(f"Gemini structured output did not match the schema: {e}") from e
