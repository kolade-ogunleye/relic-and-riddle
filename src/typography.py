"""
src/typography.py
=================
Shared font loading and text fitting for every still-image renderer.
Copy comes from an LLM, so its length is not fixed: text is shrunk, then wrapped,
to stay inside the safe width instead of running off the canvas.
"""

from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import FONTS_DIR

FONT_FILES = {"Anton": "Anton-Regular.ttf", "Montserrat": "Montserrat-Variable.ttf"}
VARIABLE_WEIGHTS = {"Montserrat": 600}  # SemiBold
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")


@lru_cache(maxsize=64)
def get_font(name: str, size: int, fonts_dir: Path = FONTS_DIR) -> ImageFont.FreeTypeFont:
    path = fonts_dir / FONT_FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"Required font missing: {path}")
    font = ImageFont.truetype(str(path), size)
    if name in VARIABLE_WEIGHTS:
        # Variable fonts default to their lightest instance; pin a legible weight.
        font.set_variation_by_axes([VARIABLE_WEIGHTS[name]])
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


def cover_crop(path: Path, size: Tuple[int, int]) -> Image.Image:
    """Scales and center-crops an image to fill `size` without distorting it."""
    with Image.open(path) as im:
        return ImageOps.fit(im.convert("RGBA"), size, Image.LANCZOS)


def is_video(path: Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_SUFFIXES
