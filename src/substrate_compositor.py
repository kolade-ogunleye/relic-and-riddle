"""
src/substrate_compositor.py
===========================
Physical Substrate Compositor for Era-Conditioned Historical Illustrations.

Extracts monochrome linework from generated imagery, tints it to period-authentic ink
(iron-gall black, copperplate sepia, Victorian printer's ink, or cyanotype blue), and
multiply-blends it over physical antique paper textures (laid vellum, rag paper, ledger plate).

Also separates the ink layer from the substrate to enable dual-layer Ken Burns parallax
in the video composer (ink moves at 1.05x, paper drifts at 1.01x).
"""

import math
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import config

# Standard vertical broadcast dimensions
DEFAULT_SIZE = (1080, 1920)

# Authentic historical ink colors (RGB)
ERA_INK_COLORS: Dict[str, Tuple[int, int, int]] = {
    "classical_antiquity": (42, 29, 21),       # Burnt Sepia / Copperplate ink
    "medieval_renaissance": (26, 23, 20),      # Iron-Gall Black
    "industrial_victorian": (33, 33, 36),      # Carbon Printer's Ink
    "modern_cold_war": (16, 46, 74),           # Prussian Blueprint Blue
}

# Substrate base paper color tones (RGB)
ERA_BASE_TONES: Dict[str, Tuple[int, int, int]] = {
    "classical_antiquity": (244, 238, 224),    # Aged Roman Vellum
    "medieval_renaissance": (240, 234, 218),   # Cotton Rag Laid Paper
    "industrial_victorian": (236, 232, 222),   # Victorian Ledger Paper
    "modern_cold_war": (232, 238, 245),        # Cyanotype Technical Paper
}

SUBSTRATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "substrates"


def upscale_factor(size: tuple[int, int], target_size: tuple[int, int]) -> float:
    """How much a cover-fit of `size` into `target_size` enlarges the image (1.0 or less: no upscale)."""
    w, h = size
    tw, th = target_size
    if w <= 0 or h <= 0:
        return float("inf")
    return max(tw / w, th / h)


def fit_crop(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """`image` scaled uniformly to cover `target_size` and centre-cropped to it: the shape is never distorted
    (the old resize stretched a 16:9 frame into 9:16)."""
    tw, th = target_size
    scale = upscale_factor(image.size, target_size)
    w, h = max(tw, round(image.size[0] * scale)), max(th, round(image.size[1] * scale))
    resized = image.resize((w, h), Image.Resampling.LANCZOS) if (w, h) != image.size else image
    x0, y0 = (w - tw) // 2, (h - th) // 2
    return resized.crop((x0, y0, x0 + tw, y0 + th))


# ------------------------------------------------------------------ the house style (2026-11-02)
# Generated illustrations are one scratchboard-engraving system: fine BONE linework on the brand's deep INK ground,
# two to three values, never coloured. The model is asked for a dark ground directly; here its luminance is mapped to
# BONE over INK2 (the light-paper multiply above stays for the legacy linework path only). Any GOLD figure or leader
# is drawn by code elsewhere, never by the model.
ENGRAVING_LEVELS = (0.0, 0.55, 1.0)   # ground, mid hatching, full line


def engraving_mask(raw: Image.Image) -> Image.Image:
    """The bright strokes of a dark-ground engraving as a mask (0 ground .. 255 full line), quantized to
    ENGRAVING_LEVELS so the frame keeps two to three values; colour is discarded."""
    arr = np.asarray(raw.convert("L"), dtype=np.float32) / 255.0
    lifted = np.clip((arr - 0.12) / 0.70, 0.0, 1.0)
    levels = np.asarray(ENGRAVING_LEVELS, dtype=np.float32)
    idx = np.abs(lifted[..., None] - levels[None, None, :]).argmin(axis=2)
    return Image.fromarray((levels[idx] * 255.0).astype(np.uint8), mode="L")


def composite_engraving_on_ink(
    raw_input: Path | str | Image.Image,
    output_path: Path | str | None = None,
    target_size: tuple[int, int] = DEFAULT_SIZE,
) -> Image.Image:
    """A generated dark-ground engraving in the house style: cover-fit to target_size (never stretched), its
    luminance mapped to BONE line over the INK2 ground. Raises on an unreadable input (the caller rejects it)."""
    from .brand import BONE, INK2
    raw = Image.open(raw_input) if isinstance(raw_input, (str, Path)) else raw_input
    raw = raw.convert("RGB")
    if raw.size != target_size:
        raw = fit_crop(raw, target_size)
    mask = engraving_mask(raw)
    out = Image.composite(Image.new("RGB", target_size, BONE), Image.new("RGB", target_size, INK2), mask)
    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out.save(out_p, "JPEG", quality=95)
    return out


def generate_procedural_substrate(era: str, size: Tuple[int, int] = DEFAULT_SIZE) -> Image.Image:
    """Generates an authentic physical paper substrate procedurally with natural paper tooth,
    fiber grain, and subtle edge darkening (used when no physical scan is bundled on disk)."""
    base_rgb = ERA_BASE_TONES.get(era, (242, 236, 222))
    w, h = size
    
    # 1. Base color canvas
    base = Image.new("RGB", size, base_rgb)
    
    # 2. Add organic paper tooth / fiber noise
    # We use a deterministic pseudo-random noise field blurred to simulate cotton rag tooth
    np.random.seed(42 + len(era))
    noise = np.random.normal(loc=0.0, scale=4.5, size=(h, w, 3)).astype(np.float32)
    base_arr = np.array(base, dtype=np.float32)
    paper_arr = np.clip(base_arr + noise, 0, 255).astype(np.uint8)
    paper = Image.fromarray(paper_arr)
    
    # 3. Add era-specific physical characteristics
    d = ImageDraw.Draw(paper, "RGBA")
    if era == "medieval_renaissance":
        # Subtle horizontal laid paper wire lines
        for y in range(0, h, 28):
            d.line([(0, y), (w, y)], fill=(0, 0, 0, 7), width=1)
    elif era == "modern_cold_war":
        # Subtle 1mm orthographic blueprint grid lines
        grid_step = 40
        for x in range(0, w, grid_step):
            d.line([(x, 0), (x, h)], fill=(16, 46, 74, 12), width=1)
        for y in range(0, h, grid_step):
            d.line([(0, y), (w, y)], fill=(16, 46, 74, 12), width=1)
            
    # 4. Subtle optical vignette (darker edges)
    # Simulates lens falloff and natural historical paper oxidation
    vignette = Image.new("L", size, 255)
    v_draw = ImageDraw.Draw(vignette)
    cx, cy = w / 2, h / 2
    max_radius = math.hypot(cx, cy)
    for r in range(int(max_radius * 0.65), int(max_radius), 8):
        alpha = int(255 - ((r - max_radius * 0.65) / (max_radius * 0.35)) * 35)
        v_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=alpha)
    vignette = vignette.filter(ImageFilter.GaussianBlur(radius=30))
    
    final_substrate = Image.composite(paper, Image.new("RGB", size, (base_rgb[0] - 25, base_rgb[1] - 25, base_rgb[2] - 25)), vignette)
    return final_substrate


def load_substrate(era: str, size: Tuple[int, int] = DEFAULT_SIZE) -> Image.Image:
    """Loads a scanned substrate texture if available on disk; otherwise falls back to
    the procedural generator."""
    scan_file = SUBSTRATES_DIR / f"{era}.jpg"
    if scan_file.exists():
        try:
            img = Image.open(scan_file).convert("RGB")
            if img.size != size:
                img = fit_crop(img, size)
            return img
        except Exception:
            pass
    return generate_procedural_substrate(era, size)


def extract_ink_mask(linework: Image.Image) -> Image.Image:
    """Extracts linework luminance and inverts it:
    - Pure white background -> Alpha 0 (transparent)
    - Dark strokes -> Alpha 255 (opaque ink)
    Applies non-linear contrast expansion to preserve delicate burin cross-hatching."""
    gray = linework.convert("L")
    
    # Non-linear curve: expand contrast, suppress paper gray haze, enhance dark ink lines
    arr = np.array(gray, dtype=np.float32) / 255.0
    # Invert: strokes are near 1.0, background near 0.0
    inverted = 1.0 - arr
    
    # Gamma & threshold curve
    # Values below 0.12 (paper dirt/scuffs) go to 0; strokes above 0.35 become rich ink
    clipped = np.clip((inverted - 0.10) / 0.75, 0.0, 1.0)
    # Apply soft s-curve
    mask_arr = (clipped ** 1.25 * 255.0).astype(np.uint8)
    return Image.fromarray(mask_arr, mode="L")


def composite_linework_onto_substrate(
    linework_input: Union[Path, str, Image.Image],
    era: str = "classical_antiquity",
    output_path: Optional[Union[Path, str]] = None,
    target_size: Tuple[int, int] = DEFAULT_SIZE
) -> Image.Image:
    """Composites raw generated linework onto an authentic physical paper substrate.
    
    1. Resizes linework to target_size (default 1080x1920) with high-quality Lanczos.
    2. Extracts burin ink strokes as an isolated alpha mask.
    3. Tints ink layer to period-accurate historical ink.
    4. Multiply-blends tinted ink over physical paper tooth.
    5. Saves to output_path if provided, returning the PIL Image.
    """
    if isinstance(linework_input, (str, Path)):
        linework = Image.open(linework_input)
    else:
        linework = linework_input

    if linework.size != target_size:
        # Cover-fit and centre-crop: never stretched to another shape (2026-11-02)
        linework = fit_crop(linework, target_size)

    # 1. Extract clean ink mask
    ink_mask = extract_ink_mask(linework)
    
    # 2. Prepare tinted ink layer
    ink_rgb = ERA_INK_COLORS.get(era, ERA_INK_COLORS["classical_antiquity"])
    ink_layer = Image.new("RGB", target_size, ink_rgb)
    
    # 3. Load or generate paper substrate
    substrate = load_substrate(era, target_size)
    
    # 4. Multiply blend:
    # Blend(substrate, ink, alpha=0.92) for deep, rich ink integration
    blended_ink = Image.blend(substrate, ink_layer, 0.92)
    composited = Image.composite(blended_ink, substrate, ink_mask)
    
    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        composited.save(out_p, "JPEG", quality=95)
        
    return composited


def extract_parallax_layers(
    linework_input: Union[Path, str, Image.Image],
    era: str = "classical_antiquity",
    target_size: Tuple[int, int] = DEFAULT_SIZE
) -> Tuple[Image.Image, Image.Image]:
    """Returns (ink_rgba, substrate_rgb) to enable dual-layer parallax motion.
    The ink layer has transparent background and period-tinted strokes."""
    if isinstance(linework_input, (str, Path)):
        linework = Image.open(linework_input)
    else:
        linework = linework_input
        
    if linework.size != target_size:
        linework = fit_crop(linework, target_size)

    ink_mask = extract_ink_mask(linework)
    ink_rgb = ERA_INK_COLORS.get(era, ERA_INK_COLORS["classical_antiquity"])
    
    # RGBA ink layer
    ink_rgba = Image.new("RGBA", target_size, (*ink_rgb, 0))
    ink_rgba.putalpha(ink_mask)
    
    substrate_rgb = load_substrate(era, target_size)
    return ink_rgba, substrate_rgb
