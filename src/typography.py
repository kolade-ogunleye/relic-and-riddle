"""
src/typography.py
=================
Shared font loading and text fitting for every still-image renderer.
Copy comes from an LLM, so its length is not fixed: text is shrunk, then wrapped,
to stay inside the safe width instead of running off the canvas.
"""

from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import FONTS_DIR

FONT_FILES = {"Anton": "Anton-Regular.ttf", "Montserrat": "Montserrat-Variable.ttf", "Cinzel": "Cinzel-Variable.ttf"}
VARIABLE_WEIGHTS = {"Montserrat": 600, "Cinzel": 700}  # SemiBold labels; Bold display serif for titles (art direction, 2026-10-24)
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")


@lru_cache(maxsize=96)
def get_font(name: str, size: int, fonts_dir: Path = FONTS_DIR, weight: Optional[int] = None) -> ImageFont.FreeTypeFont:
    """A bundled font at `size`; `weight` picks an instance of a variable font (the type hierarchy uses
    Montserrat 500/600/700 and Cinzel 700), else the pinned default."""
    path = fonts_dir / FONT_FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"Required font missing: {path}")
    font = ImageFont.truetype(str(path), size)
    if name in VARIABLE_WEIGHTS:
        # Variable fonts default to their lightest instance; pin a legible weight.
        font.set_variation_by_axes([weight or VARIABLE_WEIGHTS[name]])
    return font


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    bb = draw.textbbox((0, 0), text, font=font, anchor="lt")
    return bb[2] - bb[0], bb[3] - bb[1]


def fit_font(draw: ImageDraw.ImageDraw, text: str, name: str, size: int, max_width: int, min_size: int) -> ImageFont.FreeTypeFont:
    """Largest font <= size (down to min_size) at which `text` fits on one line."""
    for s in range(size, min_size - 1, -2):
        font = get_font(name, s)
        if text_size(draw, text, font)[0] <= max_width:
            return font
    return get_font(name, min_size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and text_size(draw, candidate, font)[0] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


import logging as _logging
import re as _re

_log = _logging.getLogger(__name__)
FIT_MIN_RATIO = 0.75   # a text block may shrink to three quarters of its design size before anything else


def _font_name(font: ImageFont.FreeTypeFont) -> Optional[str]:
    base = Path(getattr(font, "path", "") or "").name
    return next((n for n, f in FONT_FILES.items() if f == base), None)


def fit_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int,
              max_lines: int) -> Tuple[List[str], ImageFont.FreeTypeFont]:
    """The lines to draw and the font to draw them with, never a cut sentence (operator, 2026-10-25: a posted
    Story frame ended mid-sentence on "the", because every renderer kept only its first N wrapped lines).
    The text shrinks (down to FIT_MIN_RATIO of its size) until it fits in max_lines; if it still does not,
    whole sentences are kept while they fit, so the frame ends on a full stop; if not even the first sentence
    fits, every line is kept (a longer block, never a fragment). Each outcome other than a plain fit is logged."""
    text = " ".join((text or "").split())
    if not text:
        return [], font
    name = _font_name(font)
    sizes = [font.size] + ([s for s in range(font.size - 2, int(font.size * FIT_MIN_RATIO) - 1, -2)] if name else [])
    for size in sizes:
        f = font if size == font.size else get_font(name, size)
        lines = wrap_text(draw, text, f, max_width)
        if len(lines) <= max_lines:
            if size != font.size:
                _log.info("Text set at %dpx instead of %dpx to fit %d lines: %.60s", size, font.size, max_lines, text)
            return lines, f
    f = font if len(sizes) == 1 else get_font(name, sizes[-1])
    sentences = _re.split(r"(?<=[.!?])\s+", text)
    kept: List[str] = []
    for sentence in sentences:
        if len(wrap_text(draw, " ".join(kept + [sentence]), f, max_width)) <= max_lines:
            kept.append(sentence)
        else:
            break
    if kept:
        _log.warning("Text kept to %d of %d sentence(s) to fit %d lines: %.80s", len(kept), len(sentences), max_lines, text)
        return wrap_text(draw, " ".join(kept), f, max_width), f
    _log.warning("Text runs past its %d-line block (kept whole, never cut): %.80s", max_lines, text)
    return wrap_text(draw, text, f, max_width), f


def cover_crop(path: Path, size: Tuple[int, int]) -> Image.Image:
    """Scales and center-crops an image to fill `size` without distorting it."""
    with Image.open(path) as im:
        return ImageOps.fit(im.convert("RGBA"), size, Image.LANCZOS)


def is_video(path: Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_SUFFIXES
