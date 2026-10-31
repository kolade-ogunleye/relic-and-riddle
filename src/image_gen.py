"""
src/image_gen.py
================
Era-Conditioned Generative Gap Art Engine.

Synthesizes high-fidelity, era-matched monochrome linework for unphotographed historical beats
(shipwrecks, ancient metallurgy, lost ruins) using Google Imagen 3 API, then composites the
extracted ink over authentic paper substrates via src/substrate_compositor.py.

Includes the 3-Tier Degradation Ladder:
- Tier 1: Era-Matched Generative Art
- Tier 2: Programmatic Analytical Schematic / Stat Card
- Tier 3: Intentional Relic Macro Drift
"""

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import requests
from PIL import Image

from . import config, substrate_compositor
from .config import GEMINI_API_KEY, is_configured

log = logging.getLogger(__name__)

# Enterprise Imagen 3 endpoint on Google Generative Language API
IMAGEN_MODEL = "imagen-3.0-generate-002"
IMAGEN_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGEN_MODEL}:predict"

# Era-conditioned style prompts (monochrome linework on white background for clean ink extraction)
ERA_PROMPTS: Dict[str, str] = {
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


def is_available() -> bool:
    """Whether generative gap filling is enabled and has credentials."""
    return getattr(config, "GENERATIVE_GAP_FILLING", True) and is_configured(GEMINI_API_KEY)


def generate_raw_linework(
    prompt: str,
    negative_prompt: str = UNIVERSAL_NEGATIVE_PROMPT,
    aspect_ratio: str = "9:16",
    timeout: float = 30.0
) -> Optional[bytes]:
    """Calls Google Imagen 3 API to generate monochrome linework."""
    if not is_available():
        log.info("Generative gap filling unavailable (missing GEMINI_API_KEY or disabled).")
        return None
        
    url = f"{IMAGEN_URL}?key={GEMINI_API_KEY}"
    payload = {
        "instances": [
            {"prompt": prompt}
        ],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": aspect_ratio,
            "outputMimeType": "image/jpeg",
            "negativePrompt": negative_prompt,
        }
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            log.warning("Imagen 3 generation failed (HTTP %d): %s", resp.status_code, resp.text[:200])
            return None
            
        data = resp.json()
        predictions = data.get("predictions", [])
        if not predictions:
            log.warning("Imagen 3 returned empty predictions: %s", data)
            return None
            
        b64_bytes = predictions[0].get("bytesBase64Encoded")
        if not b64_bytes:
            log.warning("Imagen 3 prediction missing image bytes.")
            return None
            
        return base64.b64decode(b64_bytes)
    except Exception as e:
        log.warning("Imagen 3 API call error: %s", e)
        return None


def synthesize_gap_asset(
    beat_index: int,
    line_text: str,
    era: str,
    topic: Dict[str, Any],
    work_dir: Path,
    modality: str = "historical_scene"
) -> Optional[Path]:
    """Generates an era-matched historical illustration for an unphotographed beat.
    
    1. Compiles the era-specific prompt with subject context.
    2. Requests Imagen 3 for 9:16 monochrome linework.
    3. Runs the result through substrate_compositor to multiply-blend over paper tooth.
    4. Saves master still as gap_gen_beat_{beat_index}.jpg.
    Returns the composited Path, or None on failure (triggering Tier 2/3 degradation).
    """
    if not is_available():
        return None

    work_p = Path(work_dir)
    work_p.mkdir(parents=True, exist_ok=True)
    out_path = work_p / f"gap_gen_beat_{beat_index}.jpg"
    if out_path.is_file() and out_path.stat().st_size > 0:
        log.info("Reusing existing synthesized gap asset for Beat %d -> %s", beat_index, out_path.name)
        return out_path
        
    era_key = era if era in ERA_PROMPTS else "classical_antiquity"
    style_prompt = ERA_PROMPTS[era_key]
    
    # Modern scientific override: If the modality is modern scientific or sentence describes lab analysis, route away from antique etching
    is_modern = (modality == "modern_scientific" or
                 bool(re.search(r"\b(microscop|nanoparticle|laboratory|electron|spectrom|x-ray|ct scan|modern analysis|modern test|chemical bond)\b", line_text, re.I)))
    if is_modern:
        style_prompt = ERA_PROMPTS["modern_cold_war"]
        era_key = "modern_cold_war"
        
    subject = topic.get("title") or topic.get("name") or "historical relic"
    full_prompt = f"{style_prompt} Subject: {subject}. Depicting: {line_text}. Clear focal subject, intricate linework."
    
    log.info("Generating era-matched gap art for Beat %d (%s): '%s'...", beat_index, era_key, line_text[:60])
    raw_bytes = generate_raw_linework(full_prompt)
    if not raw_bytes:
        return None
        
    raw_path = work_p / f"raw_gen_beat_{beat_index}.jpg"
    raw_path.write_bytes(raw_bytes)
    
    # Composite onto authentic physical substrate
    out_path = work_p / f"gap_gen_beat_{beat_index}.jpg"
    try:
        substrate_compositor.composite_linework_onto_substrate(
            raw_path,
            era=era_key,
            output_path=out_path
        )
        try:
            from .lineage import record_graphic
            record_graphic(
                out_path,
                Path(__file__),
                {"era": era_key, "beat": beat_index, "line": line_text, "modality": modality},
            )
        except Exception as le:
            log.warning("Lineage recording skipped for %s: %s", out_path.name, le)
        log.info("Composited era-matched gap asset -> %s", out_path.name)
        return out_path
    except Exception as e:
        log.warning("Substrate compositing failed for %s: %s", raw_path.name, e)
        try:
            from .lineage import record_graphic
            record_graphic(
                raw_path,
                Path(__file__),
                {"era": era_key, "beat": beat_index, "line": line_text, "modality": modality},
            )
        except Exception:
            pass
        return raw_path
