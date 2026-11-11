"""
src/typography.py
=================
Shared font loading and text fitting for every still-image renderer.
Copy comes from an LLM, so its length is not fixed: text is shrunk, then wrapped,
to stay inside the safe width instead of running off the canvas.
"""

import re as _re_mod
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import FONTS_DIR
from .font_engine import (
    FeaturedFont,
    begin_field,
    brand_font,
    draw_run,
    field_face,
    inspect_font,
    record_glyph_event,
    resolve_features,
    shape,
)

FONT_FILES = {"Anton": "Anton-Regular.ttf", "Montserrat": "Montserrat-Variable.ttf", "Cinzel": "Cinzel-Variable.ttf",
              "IBM Plex Mono": "IBMPlexMono-SemiBold.ttf"}
VARIABLE_WEIGHTS = {"Montserrat": 600, "Cinzel": 700}  # SemiBold labels; Bold display serif for titles (art direction, 2026-10-24)
# Static families: one file per weight. IBM Plex Mono 2.5.0 (OFL 1.1, fonts/license_evidence.json) is the data font
# from 2026-10-29 (visual critic round 4): figures, units beside a figure, scale bars, photo credits. Fixed-pitch,
# so a count-up's digits hold their place, with a plain zero (JetBrains Mono's dotted one reads as code).
STATIC_WEIGHTS = {"IBM Plex Mono": {500: "IBMPlexMono-Medium.ttf", 600: "IBMPlexMono-SemiBold.ttf"}}
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")
# Fallback faces (QF-94; operator ruling 2026-11-10): Noto subsets for Greek, Cyrillic and extended Latin (SIL OFL
# 1.1, fonts/license_evidence.json, scripts/build_fallback_fonts.py). A field its family cannot draw whole is set
# in the first of these that can (the family's own style first), matched to the family's weight, width and cap
# height by measurement (font_engine.field_face); then the other brand faces; never a dropped letter.
# FALLBACK_STYLE names a family's style when it is not sans (a brand change, QF-87, edits these two tables only).
FALLBACK_FILES = {"sans": "NotoSans-LGC.ttf", "serif": "NotoSerif-LGC.ttf"}
FALLBACK_STYLE = {"Cinzel": "serif"}


def font_file(name: str, weight: Optional[int] = None) -> str:
    """The bundled file for a family (and, for a static family, the weight nearest `weight`)."""
    if name in STATIC_WEIGHTS and weight:
        files = STATIC_WEIGHTS[name]
        return files[min(files, key=lambda w: abs(w - weight))]
    return FONT_FILES[name]


def bundled_font_files() -> List[str]:
    """Every font file a renderer may load (for checkpoints and the release's font-rights check)."""
    files = list(FONT_FILES.values()) + [f for weights in STATIC_WEIGHTS.values() for f in weights.values()]
    return list(dict.fromkeys(files + list(FALLBACK_FILES.values())))


def fallback_files(name: str) -> list[str]:
    """The faces a family's field may be set in when the family cannot draw it, in the order they are tried: the
    Noto subset of its own style (FALLBACK_STYLE), the other Noto subset, then the other brand faces (the
    ruling refuses a field only when no bundled face has its glyphs: Cinzel has no arrow, Montserrat has)."""
    style = FALLBACK_STYLE.get(name, "sans")
    own = {FONT_FILES.get(name), *STATIC_WEIGHTS.get(name, {}).values()}
    noto = [FALLBACK_FILES[style]] + [f for k, f in FALLBACK_FILES.items() if k != style]
    others = [f for f in bundled_font_files() if f not in own and f not in noto]
    # a face with a weight axis first: its weight is matched to the family's (font_engine.fallback_match)
    return noto + sorted(others, key=lambda f: not any(a[0] == "wght" for a in inspect_font(str(FONTS_DIR / f)).axes))


@lru_cache(maxsize=96)
def get_font(name: str, size: int, fonts_dir: Path = FONTS_DIR, weight: Optional[int] = None) -> ImageFont.FreeTypeFont:
    """A bundled font at `size`; `weight` picks an instance of a variable font (the type hierarchy uses
    Montserrat 500/600/700 and Cinzel 700) or a static family's file (IBM Plex Mono 500/600), else the pinned
    default."""
    path = fonts_dir / font_file(name, weight)
    if not path.exists():
        raise FileNotFoundError(f"Required font missing: {path}")
    font = FeaturedFont(str(path), size)   # default OpenType features on every measure and mask (font_engine)
    font._rr_family = name
    font._rr_fallbacks = tuple(str(fonts_dir / f) for f in fallback_files(name) if (fonts_dir / f).is_file())
    if name in VARIABLE_WEIGHTS:
        # Variable fonts default to their lightest instance; pin a legible weight.
        axes = inspect_font(str(path)).axes
        chosen = {"wght": weight or VARIABLE_WEIGHTS[name]}
        values = []
        for tag, minimum, default, maximum in axes:
            value = chosen.get(tag, default)
            if not minimum <= value <= maximum:
                raise ValueError(f"Font axis {tag} value {value} is outside {minimum}..{maximum}")
            values.append(value)
        font.set_variation_by_axes(values)
        font._rr_variations = tuple((axis[0], value) for axis, value in zip(axes, values))
    return font


# No emoji in rendered art (the binding gates): Montserrat has none for emoji or their variation selector, so a
# writer's emoji at the end of the carousel's closing line was drawn as two empty boxes, "□□" (production carousel,
# 2026-10-29). Pictographs, dingbats, flags, the joiner and the variation selectors are removed wherever art text is
# fitted or drawn (recorded). Every other character is kept (QF-94: removing the ones a face had no glyph for drew
# "ΑΓΙΑ ΣΟΦΙΑ" as "Σ" in Anton): a field the face cannot draw whole is set in a fallback face, or refused.
ART_EMOJI = _re_mod.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u200D\uFE0E\uFE0F\u20E3]")


def drawable(text: str, font) -> str:
    """`text` as the gates allow it on art: emoji removed and the spaces they leave tidied ("marvel 🌍." ->
    "marvel."), the removal recorded. Never a letter less (QF-94): the field is checked against `font` (a
    FreeTypeFont or a bundled family name) and its fallback faces here, so a field set in a fallback face is
    recorded whole before it is fitted, and one no bundled face can draw raises GlyphRefused (the card that
    asked for it is refused with that reason). Plain ASCII is returned as it is."""
    if not text or text.isascii():
        return text or ""
    if isinstance(font, str):
        font = get_font(font, 40)
    out = ART_EMOJI.sub("", text)
    if out != text:
        out = _re_mod.sub(r"[^\S\n]{2,}", " ", out)
        out = _re_mod.sub(r"[^\S\n]+([.,!?;:…])", r"\1", out).strip()
        face = Path(str(getattr(font, "path", "") or "")).name
        record_glyph_event("emoji_removed", text, face, "".join(dict.fromkeys(ART_EMOJI.findall(text))))
    begin_field(out)      # what is fitted and drawn next are this field's parts: recorded with it, once
    field_face(out, font)
    return out


def field_font(text: str, font):
    """The face a whole field is drawn in (font_engine.field_face): `font`, or its matched fallback face when
    `font` cannot draw every character of `text`. For a renderer that draws a field's lines or words one at a
    time, so they all take the field's face. GlyphRefused when no bundled face can draw it."""
    if isinstance(font, str):
        font = get_font(font, 40)
    return field_face(drawable(text, font), font)


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


NBSP = " "


def _words(text: str) -> List[str]:
    """Words split at ordinary white space only: a no-break space (glue) holds a figure to its unit."""
    return [w for w in _re_mod.split(r"[^\S ]+", text or "") if w]


def _plain(text: str) -> str:
    return (text or "").replace(NBSP, " ")


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """Greedy word wrap. A figure glued to its unit (glue) moves to the next line with it; the lines come back
    with ordinary spaces (they are drawn and compared as the writer wrote them)."""
    lines: List[str] = []
    current = ""
    for word in _words(glue(text)):
        candidate = f"{current} {word}".strip()
        if current and text_size(draw, _plain(candidate), font)[0] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return [_plain(ln) for ln in lines]


def balanced_wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int,
                  stroke: int = 0) -> List[str]:
    """`text` on as many lines as the greedy wrap needs at `max_width`, broken so the widest line is as narrow as
    it can be (visual overhaul, 2026-10-29: the greedy wrap left one-word last lines, "rings." alone under two
    full ones). The narrowest width that still wraps into the same number of lines is found by bisection; the
    lines are the greedy wrap at that width, so a figure still never breaks from its unit."""
    lines = wrap_text(draw, text, font, max_width - 2 * stroke)
    if len(lines) < 2:
        return lines
    words = _words(glue(text))
    lo = max(line_width(draw, _plain(w), font, stroke) for w in words)
    hi = max_width
    best = lines
    while lo < hi:
        mid = (lo + hi) // 2
        trial = wrap_text(draw, text, font, mid - 2 * stroke)
        if len(trial) <= len(lines) and all(line_width(draw, ln, font, stroke) <= mid for ln in trial):
            best, hi = trial, mid
        else:
            lo = mid + 1
    return best


import logging as _logging
import re as _re

_log = _logging.getLogger(__name__)
FIT_MIN_RATIO = 0.75   # a text block may shrink to three quarters of its design size before anything else


def _font_name(font: ImageFont.FreeTypeFont) -> Optional[str]:
    if getattr(font, "_rr_family", None):
        return font._rr_family
    base = Path(getattr(font, "path", "") or "").name
    return next((n for n, f in FONT_FILES.items() if f == base), None) or next(
        (n for n, files in STATIC_WEIGHTS.items() if base in files.values()), None)


def fit_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int,
              max_lines: int, min_ratio: Optional[float] = None) -> Tuple[List[str], ImageFont.FreeTypeFont]:
    """The lines to draw and the font to draw them with, never a cut sentence (operator, 2026-10-25: a posted
    Story frame ended mid-sentence on "the", because every renderer kept only its first N wrapped lines).
    The text shrinks (down to FIT_MIN_RATIO of its size) until it fits in max_lines; if it still does not,
    whole sentences are kept while they fit, so the frame ends on a full stop; if not even the first sentence
    fits, every line is kept (a longer block, never a fragment). Each outcome other than a plain fit is logged."""
    font = brand_font(font)     # a field already set in a fallback face is refitted from its own face's sizes
    text = drawable(" ".join((text or "").split()), font)
    if not text:
        return [], font
    name = _font_name(font)
    ratio = FIT_MIN_RATIO if min_ratio is None else min_ratio  # 1.0: never smaller (Story body, 2026-10-25)
    sizes = [font.size] + ([s for s in range(font.size - 2, int(font.size * ratio) - 1, -2)] if name else [])
    for size in sizes:
        f = field_face(text, font if size == font.size else get_font(name, size))   # one face for the field
        lines = wrap_text(draw, text, f, max_width)
        if len(lines) <= max_lines:
            if size != font.size:
                _log.info("Text set at %dpx instead of %dpx to fit %d lines: %.60s", size, font.size, max_lines, text)
            return (balanced_wrap(draw, text, f, max_width) if len(lines) > 1 else lines), f
    f = field_face(text, font if len(sizes) == 1 else get_font(name, sizes[-1]))
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


ELLIPSIS = "…"


def line_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, stroke: int = 0) -> int:
    """The drawn width of one line, its outline stroke included (what the canvas actually receives)."""
    bb = draw.textbbox((0, 0), text, font=font, anchor="lt", stroke_width=stroke)
    return bb[2] - bb[0]


def _wrap_fits(draw, text, font, max_width, stroke):
    lines = wrap_text(draw, text, font, max_width - 2 * stroke)
    return lines, all(line_width(draw, ln, font, stroke) <= max_width for ln in lines)


def _shorten(draw, text, font, max_width, max_lines, stroke):
    """The longest start of `text`, cut at a word boundary and ended with an ellipsis, that fits."""
    words = text.split()

    def attempt(n):
        candidate = " ".join(words[:n]).rstrip(",;:-–—") + ELLIPSIS
        lines, ok = _wrap_fits(draw, candidate, font, max_width, stroke)
        return lines if ok and len(lines) <= max_lines else None
    lo, hi, best = 1, len(words) - 1, None      # the most words that fit (a binary search: more words never fit better)
    while lo <= hi:
        mid = (lo + hi) // 2
        lines = attempt(mid)
        if lines is not None:
            best, lo = lines, mid + 1
        else:
            hi = mid - 1
    if best is not None:
        return best
    word = words[0] if words else ""
    while word and line_width(draw, word + ELLIPSIS, font, stroke) > max_width:
        word = word[:-1]
    return [word + ELLIPSIS] if word else []


def fit_text(draw: ImageDraw.ImageDraw, text: str, name: str, size: int, max_width: int, max_lines: int,
             min_size: int, stroke: int = 0, sentences: bool = True, weight: Optional[int] = None,
             quiet: bool = False, balance: bool = True) -> Tuple[List[str], ImageFont.FreeTypeFont, bool]:
    """Text fitted to a box with the font's real metrics, so it can never run past the box (still-layout
    audit, 2026-10-28: a Story body cut at "35 meshing", a cover tagline missing). In order: wrap at the design
    size; step the size down by 2 px to `min_size`; keep whole sentences (if `sentences`); last, shorten at a
    word boundary with an ellipsis. Widths include the outline stroke. Returns (lines, font, shortened);
    `quiet`: a trial fit, not logged. A text that fits is broken into balanced lines (`balance`, 2026-10-29)
    and a figure never breaks from its unit (glue)."""
    text = drawable(" ".join((text or "").split()), name)
    if not text:
        return [], get_font(name, size, weight=weight), False
    font = None
    for s in list(range(size, min_size - 1, -2)) + ([min_size] if (size - min_size) % 2 else []):
        font = field_face(text, get_font(name, s, weight=weight))      # one face for the whole field (QF-94)
        lines, ok = _wrap_fits(draw, text, font, max_width, stroke)
        if ok and len(lines) <= max_lines:
            if s != size and not quiet:
                _log.info("Text set at %dpx instead of %dpx to fit %d line(s) of %d px: %.60s", s, size, max_lines, max_width, text)
            if balance and len(lines) > 1:  # the same lines' count, the widest as narrow as it can be (2026-10-29)
                lines = balanced_wrap(draw, text, font, max_width, stroke)
            return lines, font, False
    if sentences:
        parts = _re.split(r"(?<=[.!?])\s+", text)
        kept: List[str] = []
        for part in parts:
            lines, ok = _wrap_fits(draw, " ".join(kept + [part]), font, max_width, stroke)
            if ok and len(lines) <= max_lines:
                kept.append(part)
            else:
                break
        if kept and len(kept) < len(parts):
            if not quiet:
                _log.warning("Text kept to %d of %d sentence(s) to fit %d line(s): %.80s", len(kept), len(parts), max_lines, text)
            return _wrap_fits(draw, " ".join(kept), font, max_width, stroke)[0], font, True
    if not quiet:
        _log.warning("Text shortened with an ellipsis to fit %d line(s) of %d px at %d px: %.80s", max_lines, max_width, font.size, text)
    return _shorten(draw, text, font, max_width, max_lines, stroke), font, True


def fit_line(draw: ImageDraw.ImageDraw, text: str, name: str, size: int, max_width: int, min_size: int,
             stroke: int = 0, quiet: bool = False) -> Tuple[str, ImageFont.FreeTypeFont, bool]:
    """One line that fits `max_width` (fit_font returned its floor size even when the text still overflowed):
    (text, font, shortened)."""
    lines, font, short = fit_text(draw, text, name, size, max_width, 1, min_size, stroke=stroke, sentences=False, quiet=quiet)
    return (lines[0] if lines else ""), font, short


def cover_crop(path: Path, size: Tuple[int, int]) -> Image.Image:
    """Scales and center-crops an image to fill `size` without distorting it."""
    from .photo_hygiene import without_caption_strip
    with Image.open(path) as im:
        from .image_presentation import fit_image, full_image
        if full_image(path):
            return fit_image(im, size)
        clean, _ = without_caption_strip(ImageOps.exif_transpose(im).convert("RGBA"))
        return ImageOps.fit(clean, size, Image.LANCZOS)


def is_video(path: Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


# ---------------------------------------------------------------- one unit per run (2026-10-29)
# The narration is the source of truth for a figure's unit: Richat's covers said "40+ KM" while the voice said
# "25 miles (40 km)" (production, 29 Oct). A figure drawn in the other system is redrawn in the unit the
# narration speaks, using the narration's own pair for it ("25 miles (40 km)"), so no figure is drawn that the
# fact review did not see; a figure the narration gives in one unit only is left as written. A figure drawn
# with both units puts the narration's first ("85 M (280 FT)" -> "280 FT (85 M)" when the voice says feet).
# unit token -> (dimension, system, singular, abbreviation)
_UNITS = {
    "km": ("length", "metric", "km", "km"), "kilometre": ("length", "metric", "kilometre", "km"),
    "kilometres": ("length", "metric", "kilometre", "km"), "kilometer": ("length", "metric", "kilometer", "km"),
    "kilometers": ("length", "metric", "kilometer", "km"),
    "m": ("length", "metric", "m", "m"), "metre": ("length", "metric", "metre", "m"),
    "metres": ("length", "metric", "metre", "m"), "meter": ("length", "metric", "meter", "m"),
    "meters": ("length", "metric", "meter", "m"),
    "cm": ("length", "metric", "cm", "cm"), "centimetres": ("length", "metric", "centimetre", "cm"),
    "centimeters": ("length", "metric", "centimeter", "cm"),
    "mi": ("length", "imperial", "mile", "miles"), "mile": ("length", "imperial", "mile", "miles"),
    "miles": ("length", "imperial", "mile", "miles"),
    "ft": ("length", "imperial", "ft", "ft"), "foot": ("length", "imperial", "foot", "ft"),
    "feet": ("length", "imperial", "foot", "ft"),
    "in": ("length", "imperial", "inch", "in"), "inch": ("length", "imperial", "inch", "in"),
    "inches": ("length", "imperial", "inch", "in"),
    "yd": ("length", "imperial", "yard", "yd"), "yard": ("length", "imperial", "yard", "yd"),
    "yards": ("length", "imperial", "yard", "yd"),
    "km²": ("area", "metric", "km²", "km²"), "sq km": ("area", "metric", "sq km", "sq km"),
    "square kilometres": ("area", "metric", "square kilometre", "sq km"),
    "square kilometers": ("area", "metric", "square kilometer", "sq km"),
    "sq mi": ("area", "imperial", "sq mi", "sq mi"), "square miles": ("area", "imperial", "square mile", "sq mi"),
    "kg": ("mass", "metric", "kg", "kg"), "kilograms": ("mass", "metric", "kilogram", "kg"),
    "kilogrammes": ("mass", "metric", "kilogramme", "kg"), "tonnes": ("mass", "metric", "tonne", "tonnes"),
    "lb": ("mass", "imperial", "lb", "lb"), "lbs": ("mass", "imperial", "lb", "lb"),
    "pounds": ("mass", "imperial", "pound", "lb"),
}
_UNIT_ALT = "|".join(sorted((_re_mod.escape(u) for u in _UNITS), key=len, reverse=True))
_NUM = r"\d[\d,]*(?:\.\d+)?"
# A figure: its number, an optional "+" (a floor), a space or a hyphen ("25-MILE") and its unit. "in" and "m"
# need the space or hyphen, so "5M visitors" or "in" after a number in a sentence is not read as one.
_FIGURE = _re_mod.compile(r"(?<![\w.])(?P<num>" + _NUM + r")(?P<plus>\+?)(?P<sep>[ \u00a0-]?)(?P<unit>" + _UNIT_ALT
                          + r")(?![\w²])", _re_mod.IGNORECASE)
_PAIR = _re_mod.compile(r"(?<![\w.])(?P<n1>" + _NUM + r")\s*(?P<u1>" + _UNIT_ALT + r")(?![\w²])\s*[(/]\s*(?:about\s+|or\s+"
                        r"|roughly\s+|some\s+|nearly\s+|almost\s+)?(?P<n2>" + _NUM + r")\s*(?P<u2>" + _UNIT_ALT
                        + r")(?![\w²])\s*\)?", _re_mod.IGNORECASE)


def _unit(token: str):
    info = _UNITS.get(" ".join(token.lower().split()))
    return info


def _figure_ok(m) -> bool:
    return bool(m.group("sep")) or m.group("unit").lower() not in ("m", "in")


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


def spoken_units(text: str, narration: str) -> str:
    """`text` (a cover line, a slide, a number card) with each figure in the unit the narration speaks for it."""
    if not text or not narration or not _FIGURE.search(text):
        return text or ""
    spoken: Dict[str, str] = {}                           # dimension -> the system the narration speaks first
    for m in _FIGURE.finditer(narration):
        info = _unit(m.group("unit"))
        if info and _figure_ok(m):
            spoken.setdefault(info[0], info[1])
    pairs = []                                            # ((value, unit), (value, unit)) the narration gives
    for m in _PAIR.finditer(narration):
        a, b = _unit(m.group("u1")), _unit(m.group("u2"))
        if a and b and a[0] == b[0] and a[1] != b[1]:
            pairs.append(((m.group("n1"), m.group("u1")), (m.group("n2"), m.group("u2"))))

    def styled(token: str, like: str, adjective: bool) -> str:
        """The narration's unit written as the drawn one was: abbreviated where it was ("KM" -> "MILES": miles
        has no abbreviation a viewer reads at a glance), singular before a noun ("25-MILE"), in its case."""
        _, _, singular, abbr = _unit(token)
        short = like.lower() in {u[3] for u in _UNITS.values()} | {"mi", "lbs"}
        if adjective:
            word = abbr if short and abbr not in ("miles", "tonnes") else singular
        else:
            word = abbr if short else " ".join(token.lower().split())
        return word.upper() if like.isupper() else word

    def in_spoken_unit(m) -> Optional[str]:
        info = _unit(m.group("unit"))
        if not info or not _figure_ok(m) or spoken.get(info[0], info[1]) == info[1]:
            return None
        value = _number(m.group("num"))
        for one, other in pairs:
            for (n, u), (n2, u2) in ((one, other), (other, one)):
                if _unit(u)[:2] + _unit(u)[3:] == info[:2] + info[3:] and _number(n) == value and _unit(u2)[1] == spoken[info[0]]:
                    sep = m.group("sep")
                    return f"{n2}{m.group('plus')}{sep}{styled(u2, m.group('unit'), sep == '-')}"
        return None

    # Both units drawn: the narration's first.
    def swap(m) -> str:
        a, b = _FIGURE.fullmatch(m.group("a")), _FIGURE.fullmatch(m.group("b"))
        ia, ib = (_unit(a.group("unit")) if a else None), (_unit(b.group("unit")) if b else None)
        if ia and ib and ia[0] == ib[0] and ia[1] != ib[1] and _figure_ok(a) and _figure_ok(b):
            dual.append(m.group(0))
            if spoken.get(ia[0]) == ib[1]:
                return f"{m.group('b')}{m.group('mid')}{m.group('a')}{m.group('end')}"
        return m.group(0)
    dual: List[str] = []
    fig = r"\d[\d,]*(?:\.\d+)?\+?[ \u00a0-]?(?:" + _UNIT_ALT + r")(?![\w²])"
    out = _re_mod.sub(r"(?<![\w.])(?P<a>" + fig + r")(?P<mid>\s*\(\s*)(?P<b>" + fig + r")(?P<end>\s*\))", swap, text,
                      flags=_re_mod.IGNORECASE)
    if dual:  # both units already drawn: only their order follows the narration ("280 FT (85 M)" stays whole)
        return out
    # One unit drawn, the other system's: the narration's own figure in its unit.
    return _FIGURE.sub(lambda m: in_spoken_unit(m) or m.group(0), text)


# ---------------------------------------------------------------- a figure and its unit on one line (2026-10-29)
# "180 FT" broke as "180" at a line's end and "FT" at the next one's start (visual critic round 4); a figure is
# held to its unit, to a count word after it ("20,000 PEOPLE", "104 MILLION YEARS") and to a prefix before it
# ("c. 780", "AD 1200") by no-break spaces, which every wrap here keeps together.
_GLUE_WORDS = (_UNIT_ALT + r"|million|billion|thousand|hundred|years?|centuries|people|tons?|tonnes|levels?|storeys|"
               r"stories|floors|degrees|bce?|ad|ce|percent|%")
_GLUE_AFTER = _re_mod.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?\+?)((?: (?:" + _GLUE_WORDS + r")(?![\w²]))+)",
                              _re_mod.IGNORECASE)
_GLUE_BEFORE = _re_mod.compile(r"(?<![\w.])(c\.|ca\.|ad|about|~)\s+(?=\d)", _re_mod.IGNORECASE)


def glue(text: str) -> str:
    """`text` with a no-break space between each figure and its unit (and count words, and a prefix)."""
    if not text or not any(ch.isdigit() for ch in text):
        return text or ""
    out = _GLUE_AFTER.sub(lambda m: m.group(1) + m.group(2).replace(" ", NBSP), text)
    return _GLUE_BEFORE.sub(lambda m: m.group(1) + NBSP, out)


# ---------------------------------------------------------------- text placed at a measured contrast (2026-10-29)
# Every light text element on a photograph is measured where it is read: its glyph colour against the brightest
# tenth (90th percentile of relative luminance) of the ring 2-10 px outside its glyphs, on the pixels as composed.
# While that ratio is under the target, an ink halo shaped like the glyphs (dilated, blurred) is raised under them
# 8% at a time, up to 95%: only as dark as that photo needs there, never a box (visual critic round 4: an even
# gradient left some lines at 3:1 over bright stone and darkened others that needed nothing). The glyph mask is kept,
# so the delivered pixels (after JPEG, or the video's film grade) are measured again with the same ring, and the
# ratio is recorded in the still's layout (still_layout) for video_qa to assert.
_RING_INNER, _RING_OUTER = 5, 21   # dilation kernels: the ring is 2..10 px outside the glyphs


def rel_luminance(rgb) -> float:
    import numpy as np
    c = np.asarray(rgb[:3], dtype=np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(c @ np.array([0.2126, 0.7152, 0.0722]))


def contrast_ratio(a, b) -> float:
    la, lb = rel_luminance(a), rel_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _lin(pixels):
    import numpy as np
    c = pixels.astype(np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4) @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def ring_contrast(rgb, mask, origin=(0, 0), fill=None, light: bool = True) -> Optional[float]:
    """WCAG ratio of one text element on `rgb` (H x W x 3 uint8 array): the glyphs' colour (the median of their
    eroded interior on these pixels, or `fill`) against the 90th (light text) or 10th (dark text) percentile
    relative luminance of the ring 2-10 px outside the glyphs. `mask`: the element's glyph mask (uint8, 0/255)
    with its top-left at `origin`. None when the ring has too few pixels (the element is off the canvas)."""
    import cv2
    import numpy as np
    ox, oy = int(origin[0]), int(origin[1])
    h, w = mask.shape
    reg = rgb[max(0, oy):max(0, oy + h), max(0, ox):max(0, ox + w)]
    m = mask[max(0, -oy):max(0, -oy) + reg.shape[0], max(0, -ox):max(0, -ox) + reg.shape[1]]
    g = (m > 127).astype(np.uint8)
    ring = cv2.dilate(g, np.ones((_RING_OUTER, _RING_OUTER), np.uint8)) - cv2.dilate(g, np.ones((_RING_INNER, _RING_INNER), np.uint8))
    bg = reg[ring > 0]
    if len(bg) < 50:
        return None
    lb = float(np.percentile(_lin(bg), 90 if light else 10))
    inner = cv2.erode(g, np.ones((3, 3), np.uint8))
    if fill is None and inner.sum() > 30:
        lt = float(np.median(_lin(reg[inner > 0])))
    else:
        lt = rel_luminance(fill)
    return round((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05), 2)


def _draw_tracked(d: ImageDraw.ImageDraw, xy, text: str, font, fill, anchor: str = "la", track: float = 0,
                  stroke: int = 0, stroke_fill=None, features=None) -> Tuple[int, int, int, int]:
    """d.text with letter spacing `track` (px between letters, kerning kept: one shaped run, font_engine.draw_run)
    and OpenType `features` (None: font_engine.default_features); returns the drawn box (stroke included)."""
    return draw_run(d, xy, text, font, fill, anchor, track, stroke, stroke_fill, features)


def tracked_width(text: str, font, track: float = 0, features=None) -> float:
    """The advance _draw_tracked draws `text` at: the shaped run, kerned, plus `track` between its letters."""
    return shape(text, font, track, features).advance


def cap_height(font) -> int:
    """The font's cap height in px (the top of "H" above its baseline)."""
    return -font.getbbox("H", anchor="ls")[1]


def full_halo_pad(halo_px) -> int:
    """The crop pad that holds a whole halo of spread `halo_px`: the halo is a dilation by halo_px | 1, then a
    Gaussian blur of sigma 0.55 * halo_px, so its tail runs to about k / 2 + 3.5 sigma = 2.425 * halo_px past the
    glyphs (freeze panel 2026-11-04: the default pad, halo_px + 16, cut it at about 1.8 sigma, still 4-5% of full,
    and that cut read as a stepped rectangle round a label on a near-white photo). 4 px more for the rounding."""
    import math
    return math.ceil(2.425 * float(halo_px or 0)) + 4


class TextPlate:
    """One text element planned on its background: glyph masks, the ink halo it needs and the ratio measured on
    the composed pixels. `draw(im)` composites it (also at a partial `alpha` or shifted `dy`, for animation);
    `measure(im)` re-measures it on the delivered pixels. `halo_pad` (opt-in, full_halo_pad): crop the plate wide
    enough for the whole halo and round its alpha, so no rectangle shows where the crop ends; None keeps the
    original crop (halo_px + 16) and truncation, byte for byte."""

    def __init__(self, text, font, fill, xy, anchor, track, stroke, stroke_fill, halo_px, shadow, size,
                 halo_pad=None, features=None):
        import numpy as np
        font = field_face(text, font)      # a plate built straight from a brand font records the face it draws in
        self.text, self.font, self.fill, self.xy, self.anchor = text, font, tuple(fill[:3]), xy, anchor
        self.features = resolve_features(text, features)   # the run's features, resolved once for every pass
        self.track, self.stroke, self.stroke_fill, self.shadow = track, stroke, stroke_fill, shadow
        self.light = rel_luminance(self.fill) > 0.3
        self.halo, self.contrast, self.contrast_unaided, self.halo_mask = 0.0, None, None, None
        self._halo_q = round if halo_pad is not None else int    # how the halo's alpha is quantised
        m = Image.new("L", size, 0)
        _draw_tracked(ImageDraw.Draw(m), xy, text, font, 255, anchor, track, features=self.features)
        full = m
        if stroke:
            full = Image.new("L", size, 0)
            _draw_tracked(ImageDraw.Draw(full), xy, text, font, 255, anchor, track, stroke, 255, self.features)
        bb = full.getbbox()
        self.empty = bb is None
        if self.empty:
            self.origin, self.box, self.glyphs, self.full = (0, 0), (0, 0, 0, 0), None, None
            return
        pad = int(halo_pad) if halo_pad is not None else int(halo_px or 0) + 16
        x0, y0 = max(0, bb[0] - pad), max(0, bb[1] - pad)
        x1, y1 = min(size[0], bb[2] + pad), min(size[1], bb[3] + pad)
        self.origin, self.box = (x0, y0), tuple(int(v) for v in bb)
        self.glyphs = np.asarray(m.crop((x0, y0, x1, y1)))
        self.full = full.crop((x0, y0, x1, y1))
        if halo_px:
            import cv2
            from PIL import ImageFilter
            k = int(halo_px) | 1
            grown = cv2.dilate((np.asarray(self.full) > 127).astype(np.uint8) * 255, np.ones((k, k), np.uint8))
            self.halo_mask = Image.fromarray(grown).filter(ImageFilter.GaussianBlur(halo_px * 0.55))

    def _layer(self, base: Image.Image, halo: float, alpha: float = 1.0) -> Image.Image:
        """The element composed over `base` (an RGBA crop at the plate's origin)."""
        from PIL import ImageFilter
        out = base
        if halo > 0 and self.halo_mask is not None:
            from .brand import INK
            ink = Image.new("RGBA", base.size, INK + (255,))
            q = self._halo_q
            ink.putalpha(self.halo_mask.point(lambda v: q(v * halo * alpha)))
            out = Image.alpha_composite(out, ink)
        loc = (self.xy[0] - self.origin[0], self.xy[1] - self.origin[1])
        if self.shadow:
            sl = Image.new("RGBA", base.size, (0, 0, 0, 0))
            if self.light:
                shadow_col = (0, 0, 0, int(180 * alpha))
                s_dy, s_blur = 3, 4
            else:
                shadow_col = (255, 255, 255, int(140 * alpha))
                s_dy, s_blur = 1, 2
            _draw_tracked(ImageDraw.Draw(sl), (loc[0], loc[1] + s_dy), self.text, self.font, shadow_col,
                          self.anchor, self.track, self.stroke, shadow_col, self.features)
            out = Image.alpha_composite(out, sl.filter(ImageFilter.GaussianBlur(s_blur)))
        txt = Image.new("RGBA", base.size, (0, 0, 0, 0))
        sf = self.stroke_fill
        if sf is not None and len(sf) == 3:
            sf = tuple(sf) + (255,)
        _draw_tracked(ImageDraw.Draw(txt), loc, self.text, self.font, self.fill + (255,), self.anchor, self.track,
                      self.stroke, sf, self.features)
        if alpha < 1.0:
            txt.putalpha(txt.getchannel("A").point(lambda v: int(v * alpha)))
        return Image.alpha_composite(out, txt)

    def _crop(self, im: Image.Image, dy: int = 0) -> Image.Image:
        h, w = self.glyphs.shape
        return im.convert("RGBA").crop((self.origin[0], self.origin[1] + dy, self.origin[0] + w, self.origin[1] + dy + h))

    def solve(self, im: Image.Image, target: float, min_halo: float = 0.0, max_halo: float = 0.95) -> "TextPlate":
        """The least halo (in 8% steps from `min_halo`) at which the composed element measures `target`."""
        import numpy as np
        if self.empty:
            return self
        base = self._crop(im)
        self.contrast_unaided = ring_contrast(np.asarray(base.convert("RGB")), self.glyphs, (0, 0), self.fill, self.light)
        halo = min_halo if self.halo_mask is not None else 0.0
        r = ring_contrast(np.asarray(self._layer(base, halo).convert("RGB")), self.glyphs, (0, 0),
                          None if self.light else self.fill, self.light)
        while self.light and self.halo_mask is not None and r is not None and r < target and halo < max_halo:
            halo = min(max_halo, halo + 0.08)
            r = ring_contrast(np.asarray(self._layer(base, halo).convert("RGB")), self.glyphs, (0, 0), None, True)
        self.halo, self.contrast = round(halo, 2), r
        return self

    def draw(self, im: Image.Image, alpha: float = 1.0, dy: int = 0) -> Image.Image:
        """`im` with the element composited (its halo at the solved strength), faded by `alpha`, moved `dy` px."""
        if self.empty or alpha <= 0:
            return im
        mode = im.mode
        rgba = im.convert("RGBA")
        rgba.paste(self._layer(self._crop(rgba, int(dy)), self.halo, alpha), (self.origin[0], self.origin[1] + int(dy)))
        return rgba if mode == "RGBA" else rgba.convert(mode)

    def draw_halo(self, im: Image.Image, alpha: float = 1.0, dy: int = 0) -> Image.Image:
        """Only the element's ink halo (for text whose glyphs change frame to frame, as a count-up's digits: the
        halo of the final value, drawn under each frame's figure)."""
        if self.empty or alpha <= 0 or self.halo <= 0 or self.halo_mask is None:
            return im
        from .brand import INK
        mode = im.mode
        rgba = im.convert("RGBA")
        base = self._crop(rgba, int(dy))
        ink = Image.new("RGBA", base.size, INK + (255,))
        q = self._halo_q
        ink.putalpha(self.halo_mask.point(lambda v: q(v * self.halo * alpha)))
        rgba.paste(Image.alpha_composite(base, ink), (self.origin[0], self.origin[1] + int(dy)))
        return rgba if mode == "RGBA" else rgba.convert(mode)

    def measure(self, im: Image.Image) -> Optional[float]:
        """The element's ratio on the delivered pixels `im` (the same ring, the glyph colour as it came out)."""
        import numpy as np
        if self.empty:
            return None
        return ring_contrast(np.asarray(im.convert("RGB")), self.glyphs, self.origin, None if self.light else self.fill,
                             self.light)

    def record(self) -> Dict[str, Any]:
        """What the still's layout keeps about this element."""
        rec = {"contrast": self.contrast, "contrast_unaided": self.contrast_unaided, "halo": self.halo,
               "fill": list(self.fill), "font": Path(getattr(self.font, "path", "") or "").name,
               "px": round(float(self.font.size), 1)}
        if getattr(self.font, "_rr_fallback_for", None):   # set in a fallback face: the face it stands in for (QF-94)
            rec["fallback_for"] = self.font._rr_fallback_for
            rec["fallback_px"] = round(float(self.font._rr_px), 1)   # px: the size asked for; this: as drawn
        return rec


def plan_text(im: Image.Image, xy, text: str, font, fill, anchor: str = "la", track: float = 0, stroke: int = 0,
              stroke_fill=None, target: Optional[float] = None, halo_px: Optional[int] = None, min_halo: float = 0.0,
              shadow: bool = False, max_halo: float = 0.95, halo_pad: Optional[int] = None,
              features=None) -> TextPlate:
    """A TextPlate for `text` at `xy` on `im`, its halo solved for `target` (brand.PLACE_TARGET by default).
    `halo_px`: the halo's spread (default: a third of the font size, 21-31 px: under 21 the halo does not reach the
    outer edge of the 10 px ring it is measured on, and a thin face on a bright photo stayed under 3:1).
    `halo_pad`: opt-in crop pad for the whole halo (full_halo_pad; TextPlate)."""
    from .brand import INK, PLACE_TARGET
    text = drawable(text, font)
    font = field_face(text, font)      # the plate records the face it is drawn in (QF-94)
    halo_px = halo_px if halo_px is not None else int(min(31, max(21, font.size // 3)))
    plate = TextPlate(text, font, fill, xy, anchor, track, stroke, stroke_fill if stroke_fill is not None else INK,
                      halo_px, shadow, im.size, halo_pad=halo_pad, features=features)
    return plate.solve(im, PLACE_TARGET if target is None else target, min_halo, max_halo)


def place_text(im: Image.Image, xy, text: str, font, fill, anchor: str = "la", track: float = 0, stroke: int = 0,
               stroke_fill=None, target: Optional[float] = None, halo_px: Optional[int] = None, min_halo: float = 0.0,
               shadow: bool = False, layout=None, name: str = "", zone=None, record: bool = True,
               features=None):
    """Draws one text element at a measured contrast (see above): returns (image, plate). With `layout` (a
    still_layout.Layout) the element's box is recorded under `name` with its measured ratio, so the still's
    record says how legible each line is, not only where it is."""
    plate = plan_text(im, xy, text, font, fill, anchor, track, stroke, stroke_fill, target, halo_px, min_halo, shadow,
                       features=features)
    out = plate.draw(im)
    if layout is not None and record and not plate.empty:
        layout.add(name, plate.box, zone)
        layout.elements[-1].update(plate.record())
    return out, plate


def record_contrast(layout, name: str, plates: List[TextPlate], zone=None):
    """Several placed lines recorded as one block (still_layout.Layout.block) with the lowest ratio among them."""
    plates = [p for p in plates if not p.empty]
    if layout is None or not plates:
        return None
    from .still_layout import union
    box = layout.add(name, union([p.box for p in plates]), zone)
    worst = min(plates, key=lambda p: p.contrast if p.contrast is not None else 99.0)
    layout.elements[-1].update(worst.record())
    return box


def remeasure(layout, im: Image.Image, plates: Dict[str, List[TextPlate]]) -> None:
    """The delivered ratios (after JPEG, say) written over the planned ones in `layout`'s elements, by name."""
    for e in getattr(layout, "elements", []):
        ps = [p for p in plates.get(e["name"], []) if not p.empty]
        vals = [v for v in (p.measure(im) for p in ps) if v is not None]
        if vals:
            e["contrast_planned"] = e.get("contrast")
            e["contrast"] = min(vals)


def spoken_system(narration: str, dimension: str = "length") -> Optional[str]:
    """The unit system ("metric" or "imperial") the narration speaks first for a dimension, None if it gives
    none: a figure drawn without a figure to copy (a map's scale bar, 2026-10-29) is drawn in this system."""
    for m in _FIGURE.finditer(narration or ""):
        info = _unit(m.group("unit"))
        if info and _figure_ok(m) and info[0] == dimension:
            return info[1]
    return None
