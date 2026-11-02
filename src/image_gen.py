"""
src/image_gen.py
================
The illustration tier: a labelled, topic-specific illustration for a narrated line no authoritative photo shows
(2026-11-02, panel of archival producer, illustration art director and QA lead).

Operator's intent: a striking ("marvel") fact is not rewritten away for lack of a photo; a high-fidelity, honestly
labelled illustration fills it instead. Its rules:

- House style (HOUSE_STYLE): one scratchboard-engraving system, fine bone-white linework, hatching and stipple on a
  deep ink ground in two to three values; the era is shown only by the line idiom (ERA_IDIOM). The model is asked
  for a dark ground directly; src/substrate_compositor.composite_engraving_on_ink maps it to BONE over the brand
  INK2. Never coloured; any GOLD figure or leader is drawn by code. No named artist, no claim of accuracy.
- The prompt (build_prompt) is built from the fact sheet and its evidence (spec_from_facts), never from the
  narration's wording: STYLE, SUBJECT, MATERIAL, SCALE, SETTING/ERA, VIEWPOINT, LIGHT, COMPOSITION, EXCLUDE.
- Never generated (never_generate_reason): identifiable people, faces or remains; text, inscriptions, coins or
  seals; maps; the find itself on an evidence line; contested hypotheses; micrographs or lab imagery.
- Up to config.IMAGE_GEN_CANDIDATES candidates a beat, each scored with the rubric (src/image_quarantine.py);
  the best passing one is kept, or none. Every API request, rejected ones included, counts against a per-RUN cap
  kept in the run folder (Budget).
- The model is configurable (config.IMAGE_GEN_MODEL, IMAGE_GEN_API): the Gemini generateContent image API or the
  legacy Imagen :predict API. The key goes in the x-goog-api-key header, never the URL. model_status() lists the
  models the key can reach; a configured model it cannot reach means nothing is generated (fail closed, logged).

The legacy entry point synthesize_gap_asset (era prompts, light-paper compositing) is kept for its callers and
tests; it no longer returns an uncomposited file and its cache is keyed by the line, not the beat number.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import shutil
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import requests

from . import config, substrate_compositor
from .config import is_configured

log = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
PROMPT_VERSION = "engraving-2026-11-02"
CANDIDATE_DIR = "illustration_candidates"
BUDGET_FILE = "illustration_budget.json"
ALLOWED_SURFACES = ("beat",)   # never a hook, cover, thumbnail or story slide


# ------------------------------------------------------------------ legacy era prompts (synthesize_gap_asset)
ERA_PROMPTS: dict[str, str] = {
    "classical_antiquity": (
        "Detailed monochrome 18th-century Giovanni Battista Piranesi architectural copperplate engraving, "
        "high-contrast black ink linework with fine burin cross-hatching on pure white background, "
        "archaeological folio specimen draft style. Historical accuracy."
    ),
    "medieval_renaissance": (
        "Detailed monochrome 16th-century Albrecht Dürer woodcut print, "
        "hand-pulled printing press black ink linework on pure white background, "
        "authentic historical codex engraving style."
    ),
    "industrial_victorian": (
        "Detailed monochrome 19th-century Victorian illustrated gazette steelplate engraving, "
        "technical patent cross-section and mechanical hatched linework on pure white background."
    ),
    "modern_cold_war": (
        "Detailed monochrome technical engineering schematic and declassified orthographic cross-section "
        "on pure white background, high-contrast blueprint line art."
    ),
}

UNIVERSAL_NEGATIVE_PROMPT = (
    "photorealistic, modern, 3D render, CGI, digital painting, color, saturated, "
    "text, typography, labels, words, letters, watermarks, signature, border, frame, "
    "anime, cartoon, blur, out of focus, low quality"
)

MAX_GAP_IMAGES_PER_RUN = 2


# ------------------------------------------------------------------ the house style
HOUSE_STYLE = ("A scratchboard engraving: fine bone-white linework, cross-hatching and stipple scratched out of a deep "
               "near-black ink ground, in two to three tonal values only, with no grey wash and no colour.")
ERA_IDIOM: dict[str, str] = {
    "classical_antiquity": "The line work follows the idiom of a burin-cut line engraving.",
    "medieval_renaissance": "The line work follows the idiom of a woodcut, with bold gouged strokes.",
    "industrial_victorian": "The line work follows the idiom of a steel-plate engraving, with fine parallel hatching.",
    "modern_cold_war": "The line work follows the idiom of an orthographic technical schematic, with clean ruled lines.",
}
ERA_LABEL: dict[str, str] = {
    "classical_antiquity": "antiquity", "medieval_renaissance": "the medieval and early modern period",
    "industrial_victorian": "the nineteenth century", "modern_cold_war": "the twentieth century",
}
LIGHT = "Light rakes from the upper left and is rendered only by the density of the hatching."
COMPOSITION = ("The subject sits in the middle third of the frame, clear of the edges; the lowest fifth of the frame "
               "is quiet, plain dark ground.")
EXCLUDE = ("Do not include any text, letters, numbers, labels, signatures, watermarks, borders or frames. "
           "Do not include colour, photographic realism, a photograph's look, or 3D-render shading. "
           "Do not include faces or portraits of real people, human remains, maps, coins, seals or inscriptions. "
           "Do not include anything modern or from another period.")


def api_key() -> str:
    return str(getattr(config, "GEMINI_API_KEY", "") or "")


def is_available() -> bool:
    """Whether generative gap filling is enabled and has credentials."""
    return bool(getattr(config, "GENERATIVE_GAP_FILLING", True)) and is_configured(api_key())


def model_id() -> str:
    return str(getattr(config, "IMAGE_GEN_MODEL", "") or "").strip().removeprefix("models/")


def api_style(model: str | None = None) -> str:
    """'imagen' (legacy :predict) or 'gemini' (generateContent with image output)."""
    style = str(getattr(config, "IMAGE_GEN_API", "auto") or "auto").lower()
    if style in ("imagen", "gemini"):
        return style
    return "imagen" if (model or model_id()).startswith("imagen") else "gemini"


def _headers() -> dict[str, str]:
    return {"x-goog-api-key": api_key(), "Content-Type": "application/json"}


# ------------------------------------------------------------------ the model probe (startup / doctor)
_STATUS: dict[str, Any] = {}
_STATUS_LOCK = threading.Lock()
STATUS_TTL = 600.0


def list_models(timeout: float = 15.0) -> list[str] | None:
    """The model ids the key can reach (GET /v1beta/models, key in the header), or None when the list cannot be
    read (no key, no network, an error)."""
    if not is_configured(api_key()):
        return None
    names: list[str] = []
    token = ""
    try:
        for _ in range(10):
            params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
            resp = requests.get(f"{API_BASE}/models", headers=_headers(), params=params, timeout=timeout)
            if resp.status_code != 200:
                log.warning("Image model list could not be read (HTTP %d).", resp.status_code)
                return None
            data = resp.json()
            names += [str(m.get("name", "")).removeprefix("models/") for m in data.get("models") or [] if isinstance(m, dict)]
            token = data.get("nextPageToken") or ""
            if not token:
                break
    except (requests.RequestException, ValueError) as e:
        log.warning("Image model list could not be read (%s).", type(e).__name__)
        return None
    return names


def model_status(refresh: bool = False) -> dict[str, Any]:
    """{"model", "style", "available", "reason", "models"}: whether the configured model is one the key can reach.
    Cached for STATUS_TTL seconds; logged each time it is read afresh."""
    with _STATUS_LOCK:
        now = time.monotonic()
        if not refresh and _STATUS and now - _STATUS.get("at", 0.0) < STATUS_TTL and _STATUS.get("model") == model_id():
            return dict(_STATUS)
        model = model_id()
        models = list_models()
        if not model:
            available, reason = False, "IMAGE_GEN_MODEL is empty"
        elif models is None:
            available, reason = False, "the model list could not be read (no key, no network or an API error)"
        elif model in models:
            available, reason = True, "listed for this key"
        else:
            available, reason = False, "not among the models this key can reach"
        _STATUS.clear()
        _STATUS.update({"model": model, "style": api_style(model), "available": available, "reason": reason,
                        "models": [m for m in models or [] if "image" in m or m.startswith("imagen")], "at": now})
        (log.info if available else log.warning)("Image model %s (%s API): %s, %s.", model, api_style(model),
                                                  "available" if available else "UNAVAILABLE", reason)
        return dict(_STATUS)


def doctor() -> str:
    """One line for a startup or doctor check: the configured image model and whether the key reaches it."""
    if not getattr(config, "GENERATIVE_GAP_FILLING", True):
        return "Illustration tier: off (GENERATIVE_GAP_FILLING=false)."
    st = model_status(refresh=True)
    seen = ", ".join(st["models"][:8]) or "none listed"
    return (f"Illustration tier: model {st['model']} ({st['style']} API) "
            f"{'available' if st['available'] else 'UNAVAILABLE: ' + st['reason']}; image models the key reaches: {seen}.")


# ------------------------------------------------------------------ the request
def _decode_gemini(data: dict[str, Any]) -> bytes | None:
    for cand in data.get("candidates") or []:
        for part in ((cand or {}).get("content") or {}).get("parts") or []:
            inline = (part or {}).get("inlineData") or (part or {}).get("inline_data") or {}
            if inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


def _decode_imagen(data: dict[str, Any]) -> bytes | None:
    preds = data.get("predictions") or []
    b64 = preds[0].get("bytesBase64Encoded") if preds and isinstance(preds[0], dict) else None
    return base64.b64decode(b64) if b64 else None


def generate_image(prompt: str, aspect_ratio: str = "9:16", timeout: float | None = None) -> bytes | None:
    """One image from the configured model, or None. Fails closed: no key, generation off, or a configured model
    the key cannot reach (model_status) means no request is made."""
    if not is_available():
        log.info("Illustration tier unavailable (no GEMINI_API_KEY, or GENERATIVE_GAP_FILLING is off).")
        return None
    status = model_status()
    if not status["available"]:
        log.warning("Illustration skipped: image model %s %s.", status["model"], status["reason"])
        return None
    model, style = status["model"], status["style"]
    timeout = float(timeout or getattr(config, "IMAGE_GEN_TIMEOUT", 60.0))
    if style == "imagen":
        url = f"{API_BASE}/models/{model}:predict"
        body: dict[str, Any] = {"instances": [{"prompt": prompt}],
                                "parameters": {"sampleCount": 1, "aspectRatio": aspect_ratio}}
    else:
        # Exclusions are sentences in the prompt: generateContent takes no negative prompt.
        url = f"{API_BASE}/models/{model}:generateContent"
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect_ratio}}}
    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=timeout)
        if resp.status_code != 200:
            log.warning("Image generation failed (HTTP %d): %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Image generation request failed (%s).", type(e).__name__)
        return None
    raw = _decode_imagen(data) if style == "imagen" else _decode_gemini(data)
    if not raw:
        log.warning("Image generation returned no image (model %s).", model)
    return raw


def generate_raw_linework(prompt: str, negative_prompt: str = UNIVERSAL_NEGATIVE_PROMPT, aspect_ratio: str = "9:16",
                          timeout: float = 30.0) -> bytes | None:
    """The legacy call: the negative prompt is said as an exclusion sentence (the APIs take none)."""
    full = prompt + (f" Avoid: {negative_prompt}." if negative_prompt else "")
    return generate_image(full, aspect_ratio=aspect_ratio, timeout=timeout)


# ------------------------------------------------------------------ the per-run cap
class Budget:
    """API requests a RUN may make (config.IMAGE_GEN_MAX_CALLS_PER_RUN), every request counted before it is sent,
    rejected candidates included, kept in run_dir/BUDGET_FILE so it holds across re-plans and resumes."""

    def __init__(self, run_dir: Path, limit: int | None = None):
        self.path = Path(run_dir) / BUDGET_FILE
        self.limit = int(getattr(config, "IMAGE_GEN_MAX_CALLS_PER_RUN", 8) if limit is None else limit)

    def used(self) -> int:
        try:
            return int(json.loads(self.path.read_text(encoding="utf-8")).get("calls", 0))
        except (OSError, ValueError, AttributeError, TypeError):
            return 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used())

    def take(self, what: str = "") -> bool:
        """Counts one request; False (nothing counted) once the run's cap is reached or the count can't be kept."""
        used = self.used()
        if used >= self.limit:
            return False
        try:
            from .atomic_files import write_json
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self.path, {"calls": used + 1, "limit": self.limit, "last": what[:80]})
        except (OSError, ValueError, TypeError) as e:
            log.warning("Illustration budget not recorded (%s); no request made.", e)
            return False
        return True


# ------------------------------------------------------------------ what may be illustrated
_PERSON = re.compile(r"\b(faces?|portraits?|skeletons?|skulls?|human remains|mumm(y|ies|ified)|corpses?|bodies|bones|"
                     r"king|queen|emperor|empress|pharaoh|pope|saint|tsar|sultan|president|general)\b", re.IGNORECASE)
_TEXT = re.compile(r"\b(inscri\w*|texts?|writing|written|letters?|scripts?|glyphs?|hieroglyph\w*|runes?|coins?|seals?|"
                   r"tablets?|papyr\w*|manuscripts?|scrolls?|signatures?|maps?|charts?)\b", re.IGNORECASE)
_LAB = re.compile(r"\b(microscop\w*|micrograph\w*|nanoparticle\w*|laborator\w*|electron|spectro\w*|x-?rays?|ct scans?|"
                  r"tomograph\w*|isotop\w*|dna|radiocarbon|molecul\w*|chemical\w*|atoms?|lattice)\b", re.IGNORECASE)
_CONTESTED = re.compile(r"\b(may have|might have|could have|perhaps|possibly|probably|theor(y|ies|ised|ized)|"
                        r"hypothes\w*|some (believe|think|say|argue|suggest)|legends?|myths?|claimed|allegedly|"
                        r"supposedly|debated|disputed|contested)\b", re.IGNORECASE)
_FIND = re.compile(r"\b(found|discovered|unearthed|excavated|recovered|dug up|came to light|turned up)\b", re.IGNORECASE)


def never_generate_reason(line: str, facts_text: str = "", disputed: bool = False) -> str:
    """Why this line must never be illustrated ("" when it may be): an identifiable person, face or remains; text,
    an inscription, coin or seal; a map; the find itself on an evidence line; a contested hypothesis; a micrograph
    or lab imagery (a schematic graphic carries those instead)."""
    text = f"{line} {facts_text}"
    for pattern, why in ((_PERSON, "names a person, a face or human remains"),
                         (_TEXT, "names text, an inscription, a coin, a seal or a map"),
                         (_LAB, "names microscopy or lab imagery (a schematic graphic carries it)")):
        if pattern.search(text):
            return why
    if _FIND.search(line or ""):
        return "is an evidence line (the find itself is never drawn)"
    if disputed or _CONTESTED.search(line or ""):
        return "states a contested hypothesis"
    return ""


# ------------------------------------------------------------------ the spec, from the fact sheet
_MATERIALS = ("bronze", "copper", "iron", "steel", "gold", "silver", "lead", "tin", "brass", "marble", "limestone",
              "granite", "basalt", "sandstone", "tuff", "volcanic rock", "volcanic ash", "stone", "brick", "concrete",
              "mortar", "timber", "wood", "oak", "cedar", "clay", "terracotta", "glass", "ivory", "obsidian", "rope",
              "canvas", "leather", "plaster", "rock")
_FORMS = ("arch", "arches", "dome", "vault", "tower", "column", "columns", "pillar", "gear", "gears", "wheel", "hull",
          "mast", "sail", "chamber", "chambers", "tunnel", "tunnels", "staircase", "stairs", "shaft", "shafts", "wall",
          "walls", "pyramid", "aqueduct", "channel", "canal", "harbour", "harbor", "pier", "piers", "bridge", "furnace",
          "kiln", "mechanism", "dial", "lever", "pulley", "crane", "door", "doors", "level", "levels", "floor", "floors",
          "well", "cistern", "temple", "fortress", "ship", "vessel", "cup", "bowl", "lamp", "anchor", "statue base")
_STRUCTURES = {"chamber", "chambers", "tunnel", "tunnels", "staircase", "stairs", "shaft", "shafts", "level", "levels",
               "floor", "floors", "well", "cistern", "temple", "fortress", "pyramid", "aqueduct", "tower", "dome",
               "vault", "harbour", "harbor", "canal", "channel"}
_MECHANISMS = {"gear", "gears", "mechanism", "dial", "lever", "pulley", "crane", "wheel"}
_MEASURE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:m|metres?|meters?|ft|feet|km|kilomet(?:re|er)s?|cm|mm|tonnes?|tons?|"
                      r"kg|levels?|floors?|storeys?|stories)\b", re.IGNORECASE)
_LARGE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:m|metres?|meters?|ft|feet|km|kilomet(?:re|er)s?|levels?|floors?|"
                    r"storeys?|stories)\b", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).split())


def _clean(text: str, n: int = 180) -> str:
    """Fact-sheet text as a prompt slot: one line, no quotes or brackets, clipped (web-derived data, never orders)."""
    text = re.sub(r"[\"“”<>{}\[\]`]", "", " ".join(str(text or "").split()))
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0]


def _years(text: str) -> list[int]:
    out: list[int] = []
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)[- ]century(\s*(?:BC|BCE))?", text, re.IGNORECASE):
        c = int(m.group(1))
        out.append(-(c * 100 - 50) if m.group(2) else c * 100 - 50)
    for m in re.finditer(r"\b(\d{1,4})\s*(BC|BCE)\b", text):
        out.append(-int(m.group(1)))
    for m in re.finditer(r"\b(?:AD|CE)\s*(\d{1,4})\b|\b(\d{1,4})\s*(?:AD|CE)\b", text):
        out.append(int(m.group(1) or m.group(2)))
    for m in re.finditer(r"\b(1[0-9]{3}|20[0-2][0-9])\b(?!\s*(?:BC|BCE|m|ft|km|metres|meters|feet|people|years))", text):
        out.append(int(m.group(1)))
    return out


def era_for_year(year: int) -> str:
    if year < 500:
        return "classical_antiquity"
    if year < 1700:
        return "medieval_renaissance"
    if year < 1914:
        return "industrial_victorian"
    return "modern_cold_war"


def derive_era(topic: dict[str, Any] | None, fact_sheet: dict[str, Any] | None = None) -> str:
    """topic["era"] when it names a known era; else from the dates in the topic and its fact sheet (the earliest
    stated year: the subject's own time, not when it was studied); antiquity when nothing says."""
    era = (topic or {}).get("era")
    if era in ERA_IDIOM:
        return str(era)
    text = " ".join([str((topic or {}).get(k) or "") for k in ("title", "summary", "description", "period", "date")]
                    + [str(f.get("statement") or "") for f in (fact_sheet or {}).get("facts") or [] if isinstance(f, dict)])
    years = _years(text)
    return era_for_year(min(years)) if years else "classical_antiquity"


def _supporting_facts(line: str, fact_sheet: dict[str, Any] | None, payload: dict[str, Any] | None) -> list[dict]:
    facts = [f for f in (fact_sheet or {}).get("facts") or [] if isinstance(f, dict)]
    by_id = {str(f.get("id")): f for f in facts}
    target = _norm(line)
    for row in (payload or {}).get("sentence_trace") or []:
        if isinstance(row, dict) and target and target in _norm(row.get("sentence")):
            hits = [by_id[i] for i in row.get("fact_ids") or [] if i in by_id]
            if hits:
                return hits
    words = {w for w in target.split() if len(w) > 3}
    scored = []
    for f in facts:
        overlap = len(words & set(_norm(f.get("statement")).split()))
        if overlap >= 2:
            scored.append((overlap, f))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:2]]


def spec_from_facts(line: str, topic: dict[str, Any] | None, fact_sheet: dict[str, Any] | None,
                    payload: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
    """(the illustration's spec, "") from the facts that support `line` (its sentence_trace, else word overlap) and
    their evidence; (None, why) when there is no supporting fact, no physical descriptor, or it must never be drawn."""
    facts = _supporting_facts(line, fact_sheet, payload)
    if not facts:
        return None, "no fact-sheet fact supports the line"
    statements = " ".join(str(f.get("statement") or "") for f in facts)
    quotes = " ".join(str(e.get("quote") or "") for f in facts for e in f.get("evidence") or [] if isinstance(e, dict))
    why = never_generate_reason(line, statements, disputed=any(f.get("disputed") for f in facts))
    if why:
        return None, why
    text = f" {_norm(statements)} {_norm(quotes)} "
    materials = [m for m in _MATERIALS if f" {m} " in text][:3]
    forms = [m for m in _FORMS if f" {m} " in text][:3]
    required = materials + [f for f in forms if f not in materials]
    if not required:
        return None, "the facts give no physical descriptor to draw from"
    measures = list(dict.fromkeys(m.group(0) for m in _MEASURE.finditer(statements)))[:3]
    era = derive_era(topic, fact_sheet)
    if set(forms) & _MECHANISMS:
        viewpoint = "an exploded axonometric view"
    elif set(forms) & _STRUCTURES:
        viewpoint = "a cutaway section"
    else:
        viewpoint = "a three-quarter view"
    place = next((str((topic or {}).get(k)) for k in ("place", "location", "country") if isinstance((topic or {}).get(k), str)
                  and (topic or {}).get(k)), "")
    spec = {"subject": _clean(statements), "topic": _clean((topic or {}).get("title") or "", 80),
            "materials": materials, "forms": forms, "required": required[:4], "measures": measures,
            "human_scale": bool(_LARGE.search(" ".join(measures))), "era": era, "era_label": ERA_LABEL[era],
            "setting": _clean(place, 60), "viewpoint": viewpoint, "fact_ids": [str(f.get("id")) for f in facts]}
    return spec, ""


def build_prompt(spec: dict[str, Any]) -> str:
    """The illustration prompt, slot by slot, from the spec (fact-sheet text), in plain sentences."""
    era = spec.get("era") if spec.get("era") in ERA_IDIOM else "classical_antiquity"
    parts = [HOUSE_STYLE, ERA_IDIOM[era],
             f"Subject: {spec.get('subject')}" + (f" ({spec['topic']})" if spec.get("topic") else "") + "."]
    if spec.get("materials"):
        parts.append(f"Material: it is made of {', '.join(spec['materials'])}, shown by the texture of the line work.")
    if spec.get("forms"):
        parts.append(f"It clearly shows: {', '.join(spec['forms'])}.")
    if spec.get("measures"):
        parts.append(f"Scale: {', '.join(spec['measures'])}."
                     + (" One small, generic human figure with no facial detail stands beside it for scale."
                        if spec.get("human_scale") else ""))
    parts.append(f"Setting and era: {spec.get('setting') + ', ' if spec.get('setting') else ''}{ERA_LABEL[era]}.")
    parts += [f"Viewpoint: {spec.get('viewpoint') or 'a three-quarter view'}.", LIGHT, COMPOSITION, EXCLUDE]
    return " ".join(parts)


# ------------------------------------------------------------------ the cache
def cache_key(line: str, era: str, aspect: str = "9:16", model: str | None = None,
              prompt_version: str = PROMPT_VERSION) -> str:
    """Hash of the line's words, the era, the prompt version, the model and the frame: never the beat number, so a
    re-plan that moves or rewords a line never reuses another line's picture."""
    blob = json.dumps([_norm(line), era, prompt_version, model or model_id(), aspect], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def target_size(aspect: str) -> tuple[int, int]:
    return (1920, 1080) if aspect == "16:9" else (1080, 1920)


def _lineage_inputs(path: Path) -> dict[str, Any]:
    from .lineage import sidecar
    try:
        rec = json.loads(sidecar(path).read_text(encoding="utf-8"))
        return ((rec.get("settings") or {}).get("inputs") or {}) if isinstance(rec, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _record(out: Path, inputs: dict[str, Any]) -> None:
    try:
        from .lineage import record_graphic
        record_graphic(out, Path(__file__), inputs)
    except (OSError, ValueError) as e:
        log.warning("Lineage recording skipped for %s: %s", out.name, e)


# ------------------------------------------------------------------ the house-style path (the planner's)
def checkers_ready() -> tuple[bool, str]:
    """Whether the rubric's checkers can run: a candidate is never requested only to be rejected unlooked-at."""
    from . import image_quarantine as q
    if not q._vision_ready():
        return False, "the vision check is not available"
    if getattr(config, "IMAGE_GEN_REQUIRE_OCR", True):
        try:
            import pytesseract  # noqa: F401 - optional, probed only
        except ImportError:
            return False, "local OCR (pytesseract) is not installed"
    return True, ""


def illustrate_beat(line: str, spec: dict[str, Any], work_dir: Path, aspect: str = "9:16",
                    budget: Budget | None = None, others: Sequence[Path] = (), candidates: int | None = None,
                    surface: str = "beat") -> tuple[Path | None, dict[str, Any]]:
    """The best passing candidate for `line` (src/image_quarantine.score_candidate), composited in the house style
    and saved as run_dir/gap_gen_<key>.jpg with its lineage (line, era, model, prompt version, descriptors); or None.
    A file already made for this key is reused only after it passes the rubric again. Returns (path, info)."""
    info: dict[str, Any] = {"calls": 0, "rejected": [], "reused": False, "model": model_id(), "reason": ""}
    if surface not in ALLOWED_SURFACES:
        info["reason"] = f"never on a {surface}"
        return None, info
    if not is_available():
        info["reason"] = "generation off or no key"
        return None, info
    if work_dir is None:
        info["reason"] = "no run folder"   # never a shared topic folder
        return None, info
    from . import image_quarantine as q
    work = Path(work_dir)
    era = spec.get("era") if spec.get("era") in ERA_IDIOM else "classical_antiquity"
    key = cache_key(line, era, aspect)
    info["key"] = key
    size = target_size(aspect)
    out = work / f"gap_gen_{key}.jpg"
    cand_dir = work / CANDIDATE_DIR
    best_raw = cand_dir / f"{key}_best.png"
    ready, why = checkers_ready()
    if not ready:
        info["reason"] = why
        log.info("Illustration skipped for %r: %s.", line[:60], why)
        return None, info
    if out.is_file() and best_raw.is_file() and _lineage_inputs(out).get("key") == key:
        ok, reasons, _ = q.score_candidate(best_raw, out, spec, size, [o for o in others if Path(o) != out])
        if ok:
            info["reused"] = True
            return out, info
        info["rejected"].append({"reused": out.name, "why": reasons})
    prompt = build_prompt(spec)
    n = int(candidates if candidates is not None else getattr(config, "IMAGE_GEN_CANDIDATES", 4))
    cand_dir.mkdir(parents=True, exist_ok=True)
    best: tuple[float, Path, Path] | None = None
    for i in range(max(1, min(4, n))):
        if budget is not None and not budget.take(key):
            info["reason"] = "the run's illustration call cap is reached"
            break
        info["calls"] += 1
        raw_bytes = generate_image(prompt, aspect_ratio=aspect)
        if not raw_bytes:
            info["rejected"].append({"candidate": i, "why": ["no image returned"]})
            continue
        raw_path = cand_dir / f"{key}_{i}.png"
        final_path = cand_dir / f"{key}_{i}_final.jpg"
        try:
            raw_path.write_bytes(raw_bytes)
            substrate_compositor.composite_engraving_on_ink(raw_path, final_path, size)
        except (OSError, ValueError) as e:
            info["rejected"].append({"candidate": i, "why": [f"could not be composited: {e}"]})
            continue
        ok, reasons, score = q.score_candidate(raw_path, final_path, spec, size, others)
        if not ok:
            info["rejected"].append({"candidate": i, "why": reasons})
            continue
        if best is None or score > best[0]:
            best = (score, raw_path, final_path)
    if best is None:
        info["reason"] = info["reason"] or "no candidate passed the rubric"
        return None, info
    try:
        shutil.copyfile(best[1], best_raw)
        shutil.copyfile(best[2], out)
    except OSError as e:
        info["reason"] = f"not saved: {e}"
        return None, info
    _record(out, {"key": key, "line": line, "era": era, "aspect": aspect, "model": model_id(), "api": api_style(),
                  "prompt_version": PROMPT_VERSION, "descriptors": list(spec.get("required") or []),
                  "fact_ids": list(spec.get("fact_ids") or []), "origin": "ai_illustration"})
    log.info("Illustration for %r: %s (best of %d request(s)).", line[:60], out.name, info["calls"])
    return out, info


# ------------------------------------------------------------------ the legacy path
def synthesize_gap_asset(
    beat_index: int,
    line_text: str,
    era: str,
    topic: dict[str, Any],
    work_dir: Path,
    modality: str = "historical_scene",
    aspect: str = "9:16",
) -> Path | None:
    """The legacy era-matched linework for one line, composited over paper, or None.

    Fixed 2026-11-02: a compositing failure returns None (never the raw file, which would air unbadged and
    miscredited); the file is keyed by the line, era, prompt version and model (cache_key), not the beat number,
    and its lineage records the key; an older beat-numbered file is reused only when its lineage names this line
    (or it has none). The caller re-verifies every file it gets (src/image_quarantine.verify_gap_asset)."""
    if not is_available() or work_dir is None:
        return None
    work_p = Path(work_dir)
    work_p.mkdir(parents=True, exist_ok=True)

    era_key = era if era in ERA_PROMPTS else "classical_antiquity"
    style_prompt = ERA_PROMPTS[era_key]
    # Legacy only (the house-style path never draws lab imagery: never_generate_reason).
    is_modern = (modality == "modern_scientific" or bool(re.search(
        r"\b(microscop|nanoparticle|laboratory|electron|spectrom|x-ray|ct scan|modern analysis|modern test|chemical bond)\b",
        line_text, re.IGNORECASE)))
    if is_modern:
        style_prompt = ERA_PROMPTS["modern_cold_war"]
        era_key = "modern_cold_war"

    key = cache_key(line_text, era_key, aspect, prompt_version="legacy-" + PROMPT_VERSION)
    out_path = work_p / f"gap_gen_{key}.jpg"
    legacy = work_p / f"gap_gen_beat_{beat_index}.jpg"
    for cached in (out_path, legacy):
        if not (cached.is_file() and cached.stat().st_size > 0):
            continue
        recorded = _lineage_inputs(cached)
        if cached == out_path and recorded.get("key") != key:
            continue
        if cached == legacy and recorded and _norm(recorded.get("line")) != _norm(line_text):
            continue   # another line's picture under this beat number
        log.info("Reusing synthesized gap asset for %r -> %s", line_text[:60], cached.name)
        return cached

    subject = topic.get("title") or topic.get("name") or "historical relic"
    full_prompt = f"{style_prompt} Subject: {subject}. Depicting: {line_text}. Clear focal subject, intricate linework."
    log.info("Generating era-matched gap art for Beat %d (%s): '%s'...", beat_index, era_key, line_text[:60])
    raw_bytes = generate_raw_linework(full_prompt)
    if not raw_bytes:
        return None
    raw_path = work_p / CANDIDATE_DIR / f"raw_{key}.jpg"   # never a scene file name (gap_gen_ / raw_gen_beat_)
    try:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw_bytes)
        substrate_compositor.composite_linework_onto_substrate(raw_path, era=era_key, output_path=out_path,
                                                               target_size=target_size(aspect))
    except Exception as e:  # noqa: BLE001 - any compositing failure: nothing is shown
        log.warning("Substrate compositing failed for %s (%s); no gap asset.", raw_path.name, e)
        return None
    if not out_path.is_file():
        log.warning("Substrate compositing wrote no file for %r; no gap asset.", line_text[:60])
        return None
    _record(out_path, {"key": key, "era": era_key, "beat": beat_index, "line": line_text, "modality": modality,
                       "model": model_id(), "prompt_version": "legacy-" + PROMPT_VERSION, "aspect": aspect})
    log.info("Composited era-matched gap asset -> %s", out_path.name)
    return out_path
