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

Reliable depiction (operator, 2026-11-02: "Any illustration we're going to be using for no-photo must reliably
depict that thing... that's the whole point"), a hard requirement of this tier:
- A named subject (src/picture_coverage.NAMED_SUBJECT: one particular object or place) is drawn ONLY from at least
  one openly licensed or public-domain reference image of that very thing (find_references): the topic's approved
  photos (credits.json Topic/Kept), museum_sources candidates (PD/CC0/CC BY/CC BY-SA, the title naming the subject)
  or the Library of Congress (worldwide-unrestricted only). Up to REFERENCE_MAX of them go into the request as image
  parts with REFERENCE_INSTRUCTION. No reference: nothing is requested (the cascade's next tier fills the beat) and
  the reason is recorded. A kind of thing may be drawn without a reference, from its fact-sheet descriptors alone.
- Two independent vision checks must BOTH pass (src/image_quarantine.score_candidate; either one unable to run
  rejects): the reference comparison (candidate beside its references: same object, same structure, nothing added)
  and the descriptor checklist (without the reference, another prompt framing and, where configured, another model).
- Every reference is recorded in the illustration's lineage and credits ("based on: <title>, <licence>"), and every
  illustrated beat goes on the review card with its references; a run with an illustration is never auto-approved
  (auto_approve_refusal).

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

# The default image model (2026-11-02). ai.google.dev/gemini-api/docs/image-generation (fetched 2026-11-02): "Gemini 3
# Pro Image is optimized for professional asset production", the premium choice "for the most complex visual tasks";
# its reference-image table gives it "up to 6 images of objects with high-fidelity to include in the final image"
# (Nano Banana 2.1, gemini-nano-banana-2.1, is the recommended high-efficiency workhorse). config.IMAGE_GEN_MODEL
# defaults to it; any model the operator names is used as named.
DEFAULT_MODEL = "gemini-3-pro-image"


# Reference-grounded generation: settings live in src/config.py (IMAGE_GEN_REFERENCE_*, IMAGE_GEN_REMOTE_REFERENCES,
# IMAGE_GEN_IMAGE_SIZE); read here once.
REFERENCE_MAX = config.IMAGE_GEN_REFERENCE_MAX                    # reference images in one request
REFERENCE_MIN_CONFIDENCE = config.IMAGE_GEN_REFERENCE_MIN_CONFIDENCE   # the comparison's "yes"
REMOTE_REFERENCES = config.IMAGE_GEN_REMOTE_REFERENCES
IMAGE_SIZE = config.IMAGE_GEN_IMAGE_SIZE                          # generationConfig.imageConfig.imageSize
REFERENCE_SOURCES = ("met", "nasa", "smithsonian", "europeana", "loc")     # museum_sources, LoC verified live
REFERENCE_DIR = "illustration_refs"
REFERENCE_MAX_BYTES = 20 * 1024 * 1024
REFERENCE_MIN_SIDE = 256
REFERENCE_SIDE = 1536          # a reference is sent as a JPEG at most this many pixels on its long side
NAMED_SUBJECT = "named_subject"   # src/picture_coverage.NAMED_SUBJECT (not imported: that module is light)
SHOW_MIN_SIDE = config.IMAGE_GEN_SHOW_MIN_SIDE   # a reference shown on screen: its short side
# Why a named subject may be illustrated at all (operator refinement, 2026-11-02): a reference fit to show is SHOWN
# (the exact tier), never redrawn. Only (a) a line about what no photo can show (a reconstruction: the original
# appearance, an interior, a mechanism in use, how it was built), or (b) every reference unfit for the screen (too
# small, watermarked, cluttered) yet reliable as a reference.
RECONSTRUCTION_REASON = "reconstruction"
UNFIT_REASON = "references_unfit_for_screen"
ILLUSTRATION_REASONS = (RECONSTRUCTION_REASON, UNFIT_REASON)
_RECONSTRUCTION = re.compile(
    r"\b(original(ly)?|once (stood|rose|held|had|looked|was|were|housed|filled)|when (it was |they were )?(new|built|"
    r"complete|completed|finished|working)|at (its|their) (height|peak)|in (its|their) heyday|would have|used to|"
    r"inside|interior|within (its|the|a)|how (it|they) (was|were) (built|made|raised|assembled)|being (built|raised|"
    r"assembled)|under construction|(construction|building|assembly) of|assembl(ed|ing)|in use|in operation|worked|"
    r"working|turn(ed|ing|s)?|rotat\w*|"
    r"powered|operated|cranked|meshed|before (it|they) (fell|collapsed|sank|burned|was destroyed))\b", re.IGNORECASE)


def is_reconstruction_line(line: str) -> bool:
    """Whether the line is about what no photograph can show: the original appearance, an interior, a mechanism
    in use or how it was built."""
    return bool(_RECONSTRUCTION.search(str(line or "")))


def illustration_reason(line: str, unfit: Sequence[str] = (), n_refs: int = 0) -> str:
    """RECONSTRUCTION_REASON, UNFIT_REASON, or "" when a named subject may not be illustrated. `unfit`: the kind
    of unfitness each reference showed when it was considered for the screen ("screen": too small, text or a
    watermark, cluttered; "line": it does not show what the line says; "unknown": no check could run)."""
    if is_reconstruction_line(line):
        return RECONSTRUCTION_REASON
    kinds = list(unfit)
    if n_refs and len(kinds) >= n_refs and all(k == "screen" for k in kinds):
        return UNFIT_REASON
    return ""
REFERENCE_INSTRUCTION = ("The attached reference image(s) show this very object. Draw this object as a scratchboard "
                         "engraving; keep its structure exactly (the number, shape and proportions of its parts); "
                         "add nothing not in the reference or the facts above.")


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
    """The configured image model (config.IMAGE_GEN_MODEL, default DEFAULT_MODEL)."""
    return str(getattr(config, "IMAGE_GEN_MODEL", "") or DEFAULT_MODEL).strip().removeprefix("models/")


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


def _reference_part(path: Path) -> dict[str, Any] | None:
    """A reference image as a generateContent part (inlineData, a JPEG at most REFERENCE_SIDE px), or None when
    it cannot be read. https://ai.google.dev/api/generate-content (fetched 2026-11-02): Part.inlineData is a Blob
    {mimeType, data (base64)}."""
    import io

    from PIL import Image, ImageOps
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((REFERENCE_SIDE, REFERENCE_SIDE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
    except (OSError, ValueError) as e:
        log.warning("Reference %s unreadable (%s).", Path(path).name, e)
        return None
    return {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode("ascii")}}


def _sizes_image(model: str) -> bool:
    """Whether the model takes imageConfig.imageSize (the Gemini 3 / Nano Banana 2.x image models; the legacy 2.5
    Flash Image is sent no size)."""
    return model.startswith(("gemini-3", "gemini-nano-banana"))


def _spend_ledger(db: Any = None) -> Any:
    """The database holding media_usage (the media cap's ledger), or None when it cannot be opened."""
    if db is not None:
        return db
    try:
        from .db import PipelineDatabase
        return PipelineDatabase()
    except Exception as e:  # noqa: BLE001 - no ledger means no billed request (fail closed)
        log.warning("Illustration skipped: the media spend ledger could not be opened (%s).", type(e).__name__)
        return None


def generate_image(prompt: str, aspect_ratio: str = "9:16", timeout: float | None = None,
                   references: Sequence[Path] = (), db: Any = None) -> bytes | None:
    """One image from the configured model, or None. Fails closed: no key, generation off, or a configured model
    the key cannot reach (model_status) means no request is made.

    Billed spend (QF-46): every request is asked of the monthly media cap first (media_budget.allow with
    config.IMAGE_GEN_COST_USD, shared with AI motion; MEDIA_MONTHLY_BUDGET_USD = 0 means no request at all) and an
    HTTP 200 is recorded in media_usage as media_budget.AI_IMAGES, whatever the image then turns out to be. `db` is
    the ledger (the pipeline database by default); one that cannot be opened means no request.

    `references`: images of the very subject, sent after the prompt as inlineData parts of the same user turn
    (https://ai.google.dev/api/generate-content, fetched 2026-11-02: contents[].parts[] each a text or an inlineData
    Blob; ImageConfig.aspectRatio "If not specified, the model will choose a default aspect ratio based on any
    reference images provided", ImageConfig.imageSize 512/1K/2K/4K. The image-generation guide,
    ai.google.dev/gemini-api/docs/image-generation, shows the same text-then-images order). A reference that cannot
    be read, or the legacy Imagen API (no image input here), means no request (fail closed)."""
    if not is_available():
        log.info("Illustration tier unavailable (no GEMINI_API_KEY, or GENERATIVE_GAP_FILLING is off).")
        return None
    from . import media_budget
    cost = float(getattr(config, "IMAGE_GEN_COST_USD", 0.14))
    ledger = _spend_ledger(db)
    if ledger is None:
        return None
    if not media_budget.allow(ledger, cost):
        log.info("Illustration skipped: the monthly media budget (MEDIA_MONTHLY_BUDGET_USD) allows no $%.2f request.",
                 cost)
        return None
    status = model_status()
    if not status["available"]:
        log.warning("Illustration skipped: image model %s %s.", status["model"], status["reason"])
        return None
    model, style = status["model"], status["style"]
    timeout = float(timeout or getattr(config, "IMAGE_GEN_TIMEOUT", 60.0))
    ref_parts: list[dict[str, Any]] = []
    for ref in list(references or [])[:REFERENCE_MAX]:
        part = _reference_part(Path(ref))
        if part is None:
            log.warning("Illustration skipped: reference %s could not be read.", Path(ref).name)
            return None
        ref_parts.append(part)
    if style == "imagen":
        if ref_parts:
            log.warning("Illustration skipped: the Imagen API path sends no reference image (model %s).", model)
            return None
        url = f"{API_BASE}/models/{model}:predict"
        body: dict[str, Any] = {"instances": [{"prompt": prompt}],
                                "parameters": {"sampleCount": 1, "aspectRatio": aspect_ratio}}
    else:
        # Exclusions are sentences in the prompt: generateContent takes no negative prompt.
        url = f"{API_BASE}/models/{model}:generateContent"
        image_config: dict[str, Any] = {"aspectRatio": aspect_ratio}
        if IMAGE_SIZE and _sizes_image(model):
            image_config["imageSize"] = IMAGE_SIZE
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}, *ref_parts]}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": image_config}}
    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=timeout)
        if resp.status_code == 200:   # billed from here on, even if no usable image comes back
            media_budget.record(ledger, media_budget.AI_IMAGES, model, cost)
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
              prompt_version: str = PROMPT_VERSION, references: Sequence[str] = ()) -> str:
    """Hash of the line's words, the era, the prompt version, the model and the frame: never the beat number, so a
    re-plan that moves or rewords a line never reuses another line's picture. `references`: the reference images'
    digests (another reference is another picture); none leaves the key as it was."""
    parts: list[Any] = [_norm(line), era, prompt_version, model or model_id(), aspect]
    if references:
        parts.append(sorted(str(r) for r in references))
    blob = json.dumps(parts, ensure_ascii=False)
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


# ------------------------------------------------------------------ references (reliable depiction, 2026-11-02)
_TERM_STOP = {"the", "a", "an", "its", "it", "this", "that", "these", "those", "in", "on", "at", "of", "and", "but",
              "for", "from", "with", "by", "to", "into", "inside", "under", "over", "when", "where", "while", "after",
              "before", "today", "yet", "there", "here", "then", "they", "their", "he", "she", "his", "her", "we",
              "our", "you", "your", "one", "two", "three", "each", "every", "some", "most", "all", "no", "not"}


_SENTENCE_START = re.compile(r"(?:^|[.!?;:]\s+|[\"\u201c(]\s*)$")


def _line_names(line: str, topic: dict[str, Any] | None = None) -> list[str]:
    """The line's own proper names: capitalised words that are not function words. A sentence's first word is
    capitalised by grammar, not because it is a name ("Divers found...", "Like the..."), so it counts only when it
    is also a word of the topic's title (follow-up review, 2026-11-02)."""
    text = str(line or "")
    title = set(_norm(str((topic or {}).get("title") or "")).split())
    names: list[str] = []
    for m in re.finditer(r"[A-Z][A-Za-z'\-]{2,}", text):
        w = m.group(0)
        if w.lower().strip("'") in _TERM_STOP:
            continue
        if _SENTENCE_START.search(text[:m.start()]) and _norm(w.removesuffix("'s")) not in title:
            continue
        names.append(w)
    return names


def subject_terms(line: str, topic: dict[str, Any] | None = None) -> list[str]:
    """The words that name the line's particular subject: its proper names (capitalised words, a sentence's first
    word only when the topic's title has it too: _line_names), else the topic's title words. Normalised, at most
    four, in order."""
    names = _line_names(line, topic)
    if not names:
        names = [w for w in re.findall(r"[A-Za-z'\-]{4,}", str((topic or {}).get("title") or ""))
                 if w.lower() not in _TERM_STOP]
    out: list[str] = []
    for w in names:
        n = _norm(w.removesuffix("'s"))
        if n and n not in out:
            out.append(n)
    return out[:4]


def names_subject(text: str, terms: Sequence[str]) -> bool:
    """Whether `text` (a reference's title) names the subject: every term when there are one or two, else the
    first term and at least two in all. No terms: never (a reference must be of the very thing)."""
    terms = [t for t in terms if t]
    if not terms:
        return False
    have = set(_norm(text).split())
    hits = [t for t in terms if all(w in have for w in t.split())]
    if len(terms) <= 2:
        return len(hits) == len(terms)
    return terms[0] in hits and len(hits) >= 2


_ATTRIBUTION_LICENCE = re.compile(r"^cc[ -]by\b", re.IGNORECASE)   # CC BY and CC BY-SA (not CC0)


def attribution_complete(licence: str, artist: str, license_url: str, source_url: str) -> bool:
    """Whether a reference carries what its licence needs to be credited: a CC BY or CC BY-SA one only with its
    artist, licence URL and source URL all known (a credit missing any of them is unverifiable, and the provenance
    ledger would mark it so); public domain and CC0 need none of them."""
    if not _ATTRIBUTION_LICENCE.match(str(licence or "").strip()):
        return True
    return all(str(v or "").strip() for v in (artist, license_url, source_url))


def reference_licence_ok(licence: str) -> bool:
    """Public domain, CC0, CC BY (and CC BY-SA where the project's licence policy allows it), never anything else
    (src/credits.license_allowed)."""
    from .credits import license_allowed
    return bool(licence) and license_allowed(str(licence))


def _loc_unrestricted(cand: dict[str, Any]) -> bool:
    """A Library of Congress candidate whose recorded rights say "no known restrictions" with no U.S.-only limit
    (checked again here: a cached answer may predate src/museum_sources.loc_search's U.S.-only refusal)."""
    from . import museum_sources as ms
    rights = str(cand.get("description") or "").lower()
    return any(p in rights for p in ms._NO_KNOWN_RESTRICTIONS) and not any(p in rights for p in ms._LOC_US_ONLY)


def _file_digest(path: Path) -> str:
    try:
        from .credits import sha256_file
        return sha256_file(Path(path))
    except (OSError, ValueError):
        return ""


def _readable_image(path: Path) -> bool:
    from PIL import Image
    try:
        with Image.open(path) as im:
            return min(im.size) >= REFERENCE_MIN_SIDE
    except (OSError, ValueError):
        return False


def _download_reference(cand: dict[str, Any], work_dir: Path) -> Path | None:
    """The candidate's file in work_dir/REFERENCE_DIR (https only, at most REFERENCE_MAX_BYTES, readable, at least
    REFERENCE_MIN_SIDE px), or None. Never raises. The URL and every redirect hop must be on the candidate's provider
    hosts (footage.ALLOWED_HOSTS; any still-image provider's fixed hosts when it records none) and public (QF-44)."""
    from . import url_guard
    from .commons import HEADERS
    from .footage import url_allowed
    url = str(cand.get("download_url") or "")
    provider = str(cand.get("provider") or "") or None
    if not url_allowed(url, provider):
        log.info("Reference %s not fetched (not on %s's hosts).", url[:80], provider or "an image provider")
        return None
    import io

    from PIL import Image, ImageOps
    out = Path(work_dir) / REFERENCE_DIR / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + ".jpg")
    if out.is_file() and _readable_image(out):
        return out
    try:
        with url_guard.follow(url, requests.get, allowed=lambda u: url_allowed(u, provider),
                              headers=HEADERS, timeout=30, stream=True) as r:
            if r.status_code != 200:
                return None
            size, chunks = 0, []
            for chunk in r.iter_content(64 * 1024):
                size += len(chunk)
                if size > REFERENCE_MAX_BYTES:
                    return None
                chunks.append(chunk)
        # Saved as a JPEG whatever was served (a TIFF or PNG): the request, the checks and the review card read it.
        with Image.open(io.BytesIO(b"".join(chunks))) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            if min(im.size) < REFERENCE_MIN_SIDE:
                return None
            out.parent.mkdir(parents=True, exist_ok=True)
            im.save(out, "JPEG", quality=92)
    except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError) as e:
        log.info("Reference %s not fetched (%s).", url[:80], type(e).__name__)
        return None
    return out


def _reference(path: Path, title: str, licence: str, source: str, url: str = "", license_url: str = "",
               artist: str = "") -> dict[str, Any]:
    return {"path": str(path), "title": _clean(title, 160), "licence": str(licence), "license_url": str(license_url or ""),
            "url": str(url or ""), "source": source, "artist": _clean(artist, 120), "sha256": _file_digest(Path(path))}


def find_references(line: str, topic: dict[str, Any] | None, local: Sequence[dict[str, Any]] = (),
                    work_dir: Path | None = None, remote: bool | None = None,
                    limit: int = REFERENCE_MAX) -> tuple[list[dict[str, Any]], str]:
    """(references, "") for a named-subject line: openly licensed or public-domain images of that very thing, at
    most `limit`; ([], why) when there is none.

    `local`: the topic's approved photos as {"path", "title", "description", "license", "license_url", "source_url",
    "artist"} (src/scene_planner builds them from credits.json, Topic/Kept only). Then, when `remote` (default: the
    tier is available and REMOTE_REFERENCES), the museum sources (REFERENCE_SOURCES via the cached line search,
    src/museum_sources.cached_search), each file fetched into the run folder. A reference qualifies only when its
    licence is open (reference_licence_ok) and its TITLE names the subject (names_subject); a Library of Congress
    one only when its rights are unrestricted worldwide."""
    terms = subject_terms(line, topic)
    if not terms:
        return [], "the line names no subject a reference could be matched to"
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in local:
        path = Path(str(c.get("path") or ""))
        title = str(c.get("title") or "")
        if not (path.is_file() and reference_licence_ok(str(c.get("license") or "")) and names_subject(title, terms)):
            continue
        if not attribution_complete(str(c.get("license") or ""), str(c.get("artist") or ""),
                                    str(c.get("license_url") or ""), str(c.get("source_url") or "")):
            log.info("Reference %s skipped: its CC BY credit lacks the artist, licence URL or source URL.", path.name)
            continue
        digest = _file_digest(path)
        if not digest or digest in seen or not _readable_image(path):
            continue
        seen.add(digest)
        refs.append(_reference(path, title, str(c.get("license")), "topic_photo", c.get("source_url") or "",
                               c.get("license_url") or "", c.get("artist") or ""))
        if len(refs) >= limit:
            return refs, ""
    remote = (is_available() and REMOTE_REFERENCES) if remote is None else remote
    if remote and work_dir is not None:
        from . import museum_sources as ms
        sources = tuple(n for n in REFERENCE_SOURCES if n in ms.line_sources() or n == "loc")
        try:
            found = ms.cached_search(" ".join(terms), terms, sources=sources)
        except Exception as e:  # noqa: BLE001 - a museum is a saving, never a new way for a run to fail
            log.info("Reference search for %r failed (%s).", terms, e)
            found = []
        for cand in found:
            if not (reference_licence_ok(str(cand.get("license") or "")) and names_subject(str(cand.get("title") or ""), terms)):
                continue
            if cand.get("provider") == "loc" and not _loc_unrestricted(cand):
                continue
            if not attribution_complete(str(cand.get("license") or ""), str(cand.get("artist") or ""),
                                        str(cand.get("license_url") or ""),
                                        str(cand.get("source_url") or cand.get("download_url") or "")):
                continue   # checked before the download: an uncreditable CC BY file is never fetched
            got = _download_reference(cand, Path(work_dir))
            digest = _file_digest(got) if got else ""
            if not got or not digest or digest in seen:
                continue
            seen.add(digest)
            refs.append(_reference(got, str(cand.get("title") or ""), str(cand.get("license")),
                                   str(cand.get("provider") or "museum"), cand.get("source_url") or cand.get("download_url") or "",
                                   cand.get("license_url") or "", cand.get("artist") or ""))
            if len(refs) >= limit:
                break
    if not refs:
        return [], (f"no openly licensed reference image of {' '.join(terms)} (a named subject is never drawn "
                    "without one)")
    return refs, ""


def based_on(references: Sequence[dict[str, Any]]) -> str:
    """"based on: <title>, <licence>; ..." for the credits and the review card ("" when none)."""
    items = [f"{r.get('title') or Path(str(r.get('path') or '')).name}, {r.get('licence') or 'licence unknown'}"
             for r in references or [] if isinstance(r, dict)]
    return ("based on: " + "; ".join(items)) if items else ""


def screen_unfit(path: Path) -> str:
    """Why a reference image is unfit for the screen by its pixels alone ("" when its size allows it): its short
    side under SHOW_MIN_SIDE, or it cannot be read."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            w, h = im.size
    except (OSError, ValueError) as e:
        return f"unreadable ({e})"
    return f"too small to show ({w}x{h}, short side under {SHOW_MIN_SIDE}px)" if min(w, h) < SHOW_MIN_SIDE else ""


def reference_credit(ref: dict[str, Any]) -> dict[str, Any]:
    """A credit record (the shape src/credits.py reads) for a reference shown on screen as a photo."""
    licence = str(ref.get("licence") or "")
    return {"file": str(ref.get("path") or ""), "title": ref.get("title") or "", "artist": ref.get("artist") or "Unknown",
            "license": licence, "license_url": ref.get("license_url") or "", "source_url": ref.get("url") or "",
            "attribution_required": licence.strip().lower().startswith("cc by"), "provider": ref.get("source") or "",
            "origin": "reference_shown"}


# ------------------------------------------------------------------ operator approval (2026-11-02)
def review_note(generated: Sequence[dict[str, Any]]) -> str:
    """The review card's line for the run's illustrated beats: each beat with its narrated line, the illustration
    and its reference(s), and that the run waits for the operator ("" when none)."""
    rows = [g for g in generated or [] if isinstance(g, dict)]
    if not rows:
        return ""
    parts = []
    for g in rows:
        refs = [r for r in g.get("references") or [] if isinstance(r, dict)]
        ref_text = "; ".join(f"{r.get('title') or 'untitled'} ({r.get('licence') or '?'}"
                             + (f", {r['url']}" if r.get("url") else "") + ")" for r in refs)
        why = {RECONSTRUCTION_REASON: " (a reconstruction no photo can show)",
               UNFIT_REASON: " (every reference unfit for the screen)"}.get(str(g.get("why_illustrated") or ""), "")
        parts.append(f"Beat {g.get('beat')} “{_clean(g.get('line') or '', 120)}” → {g.get('to') or 'illustration'}{why}"
                     + (f", based on: {ref_text}" if ref_text else ", no reference (kind of thing, drawn from the facts)"))
    return (f"🏛 Archival Reconstructions ({len(rows)}): " + " · ".join(parts)
            + " · Your approval is required (never auto-approved).")


def auto_approve_refusal(payload: dict[str, Any] | None, scenes: Sequence[Any] = ()) -> str:
    """Why this run may not be auto-approved ("" when it may): it put an AI illustration on screen; every
    illustrated beat waits for the operator. Read from the picture check's record (payload["picture_fit"]
    ["generated"]) and, since a path without that record (the deep dive) shows illustrations too, from the
    `scenes` themselves: a file the planner names as an illustration (scene_planner.is_illustration) or whose
    lineage records origin "ai_illustration"."""
    fit = (payload or {}).get("picture_fit") or {}
    generated = fit.get("generated") if isinstance(fit, dict) else None
    if generated:
        beats = ", ".join(str(g.get("beat")) for g in generated if isinstance(g, dict))
        return (f"auto-approve refused: the video shows an AI illustration (beat {beats}); an illustration is "
                "approved by the operator on the review card, never automatically")
    from .scene_planner import is_illustration
    drawn: list[str] = []
    for s in scenes or ():
        if not isinstance(s, (str, Path)) or not str(s):
            continue
        p = Path(s)
        if (is_illustration(p) or _lineage_inputs(p).get("origin") == "ai_illustration") and p.name not in drawn:
            drawn.append(p.name)
    if not drawn:
        return ""
    return (f"auto-approve refused: the video shows an AI illustration ({', '.join(drawn[:6])}); an illustration "
            "is approved by the operator on the review card, never automatically")


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
                    surface: str = "beat", references: Sequence[dict[str, Any]] = (),
                    visual: str | None = None, reason: str = "") -> tuple[Path | None, dict[str, Any]]:
    """The best passing candidate for `line` (src/image_quarantine.score_candidate), composited in the house style
    and saved as run_dir/gap_gen_<key>.jpg with its lineage (line, era, model, prompt version, descriptors,
    references); or None. A file already made for this key is reused only after it passes the rubric again.

    `visual` NAMED_SUBJECT: drawn only from `references` (find_references), which go into the request and into the
    reference comparison, and only for one of ILLUSTRATION_REASONS (`reason`, illustration_reason); otherwise
    nothing is requested. Returns (path, info)."""
    refs = [dict(r) for r in references or [] if isinstance(r, dict) and Path(str(r.get("path") or "")).is_file()]
    refs = refs[:REFERENCE_MAX]
    info: dict[str, Any] = {"calls": 0, "rejected": [], "reused": False, "model": model_id(), "reason": "",
                            "references": refs, "visual": visual, "why_illustrated": reason}
    if surface not in ALLOWED_SURFACES:
        info["reason"] = f"never on a {surface}"
        return None, info
    if visual == NAMED_SUBJECT and not refs:
        info["reason"] = "a named subject is drawn only from a reference image of it, and there is none"
        log.info("Illustration skipped for %r: %s.", line[:60], info["reason"])
        return None, info
    if visual == NAMED_SUBJECT and reason not in ILLUSTRATION_REASONS:
        info["reason"] = ("a named subject with a reference is illustrated only for a reconstruction or when every "
                          "reference is unfit for the screen (a reference fit to show is shown instead)")
        log.info("Illustration skipped for %r: %s.", line[:60], info["reason"])
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
    ref_paths = [Path(str(r["path"])) for r in refs]
    key = cache_key(line, era, aspect, references=[r.get("sha256") or r["path"] for r in refs])
    info["key"] = key
    size = target_size(aspect)
    # The rubric knows a named subject needs its reference comparison, and why it is drawn: a reconstruction may
    # add only what the narrated line and its descriptors describe (image_quarantine.reference_check).
    checked = dict(spec, visual=visual, why_illustrated=reason, line=spec.get("line") or line)
    out = work / f"gap_gen_{key}.jpg"
    cand_dir = work / CANDIDATE_DIR
    best_raw = cand_dir / f"{key}_best.png"
    ready, why = checkers_ready()
    if not ready:
        info["reason"] = why
        log.info("Illustration skipped for %r: %s.", line[:60], why)
        return None, info
    if out.is_file() and best_raw.is_file() and _lineage_inputs(out).get("key") == key:
        ok, reasons, _ = q.score_candidate(best_raw, out, checked, size, [o for o in others if Path(o) != out],
                                           references=ref_paths)
        if ok:
            info["reused"] = True
            return out, info
        info["rejected"].append({"reused": out.name, "why": reasons})
    prompt = build_prompt(spec) + (" " + REFERENCE_INSTRUCTION if ref_paths else "")
    n = int(candidates if candidates is not None else getattr(config, "IMAGE_GEN_CANDIDATES", 4))
    cand_dir.mkdir(parents=True, exist_ok=True)
    best: tuple[float, Path, Path] | None = None
    for i in range(max(1, min(4, n))):
        if budget is not None and not budget.take(key):
            info["reason"] = "the run's illustration call cap is reached"
            break
        info["calls"] += 1
        raw_bytes = generate_image(prompt, aspect_ratio=aspect, **({"references": ref_paths} if ref_paths else {}))
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
        ok, reasons, score = q.score_candidate(raw_path, final_path, checked, size, others, references=ref_paths)
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
                  "fact_ids": list(spec.get("fact_ids") or []), "origin": "ai_illustration", "visual": visual,
                  "why_illustrated": reason,
                  "references": [{k: r.get(k) for k in ("path", "url", "title", "licence", "license_url", "source",
                                                        "artist", "sha256")} for r in refs]})
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
    visual: str | None = None,
    references: Sequence[dict[str, Any]] = (),
) -> Path | None:
    """The legacy era-matched linework for one line, composited over paper, or None.

    Never a named subject without a reference (follow-up review, 2026-11-02): this path sends no reference to the
    model, so a line marked NAMED_SUBJECT (`visual`) or one naming a proper subject of its own (_line_names) with no
    `references` gets None, with the reason logged; the planner uses illustrate_beat, never this. The topic's title
    is no longer put in the prompt (it drew the titled thing from the model's memory).

    Fixed 2026-11-02: a compositing failure returns None (never the raw file, which would air unbadged and
    miscredited); the file is keyed by the line, era, prompt version and model (cache_key), not the beat number,
    and its lineage records the key; an older beat-numbered file is reused only when its lineage names this line
    (or it has none). The caller re-verifies every file it gets (src/image_quarantine.verify_gap_asset)."""
    if not is_available() or work_dir is None:
        return None
    if not references and (visual == NAMED_SUBJECT or _line_names(line_text, topic)):
        log.info("Legacy gap art refused for %r: a named subject is drawn only from a reference image of it "
                 "(illustrate_beat), and none was given.", line_text[:60])
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

    full_prompt = f"{style_prompt} Depicting: {line_text}. Clear focal subject, intricate linework."
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
