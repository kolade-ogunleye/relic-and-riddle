"""OpenType inspection and one shaped glyph run for measured, tracked and featured text (QF-21).

fontTools reads what a bundled face really carries (its cmap coverage, layout features, variation axes, vertical
metrics and the names libass matches); uharfbuzz shapes a run once, with the face's GPOS kerning and the requested
OpenType features, so a tracked line is measured and drawn from the same glyph positions. Pillow (raqm) still
rasterises the bundled outlines.

The API takes the face per call (a FreeTypeFont from typography.get_font): a caller that sets a field in a fallback
face (QF-94) passes that face, and its coverage comes from inspect_font(face.path).coverage.
"""

import io
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from PIL import ImageFont

# Graphic text (Pillow) is set with the face's kerning, and with its case-sensitive forms when the run has no
# lower-case letter. `case` raises hyphens, dashes, brackets, the middle dot and guillemets to the capitals'
# centre (OpenType feature registry: "shifts various punctuation marks up to a position that works better with
# all-capital sequences or sets of lining figures"); on a run with lower case they belong at the x-height, so
# a mixed-case line keeps its default forms. Builder decision for QF-21 (review section 9 asks for
# ["kern","case"] by default; the caps condition is the registry's own scope for the feature).
KERN = ("kern",)
CAPS_FEATURES = ("kern", "case")
DEFAULT_FEATURES = CAPS_FEATURES          # the default of a run without lower case (see default_features)
FIGURE_FEATURES = (*CAPS_FEATURES, "tnum", "lnum")   # figures that must hold their cell (a column, a changing value)
SMALL_CAPS = ("kern", "case", "smcp")     # true small capitals: the face's own smcp glyphs, accents kept
# libass as the captions use it: the ASS header sets no "Kerning: yes", so it shapes without `kern`; it never asks
# for `case`; and it draws no optional ligatures at any Spacing: measured on libass's own pixels, an fi run and an
# fl run in the 16:9 CapText style (Montserrat Bold, Spacing 0) draw the same width, while the face's fi ligature is
# 3/1000 em wider than f + i and its fl ligature 1/1000 em narrower (tests/test_qf21_opentype.py).
LIBASS_FEATURES = ("-kern", "-liga", "-clig")
_GLYPH_CODEPOINT = 0xF0000


def default_features(text) -> tuple[str, ...]:
    """The features a graphic run is set with when its caller names none: kerning, plus `case` when `text` has
    no lower-case letter (all capitals, figures, punctuation)."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("latin-1")
    return KERN if any(ch.islower() for ch in text) else CAPS_FEATURES


def resolve_features(text, features) -> tuple[str, ...]:
    """`features` as given, or default_features(text) when None."""
    return default_features(text) if features is None else tuple(features)


class FeaturedFont(ImageFont.FreeTypeFont):
    """A FreeTypeFont whose measures and masks use default_features(text) when the caller names no features, so
    an untracked line drawn with ImageDraw.text is set, measured and boxed with the same OpenType features as a
    tracked run (draw_run) and a plate's mask. A caller's explicit `features` list wins."""

    def getlength(self, text, mode="", direction=None, features=None, language=None):
        return super().getlength(text, mode, direction, list(resolve_features(text, features)), language)

    def getbbox(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None):
        return super().getbbox(text, mode, direction, list(resolve_features(text, features)), language,
                               stroke_width, anchor)

    def getmask(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None,
                ink=0, start=None):
        return super().getmask(text, mode, direction, list(resolve_features(text, features)), language,
                               stroke_width, anchor, ink, start)

    def getmask2(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None,
                 ink=0, start=None, *args, **kwargs):
        return super().getmask2(text, mode, direction, list(resolve_features(text, features)), language,
                                stroke_width, anchor, ink, start, *args, **kwargs)


@dataclass(frozen=True)
class FontInfo:
    """What a font file carries, read from its tables."""
    coverage: frozenset[int]                             # code points the cmap maps to a real glyph (not .notdef)
    features: frozenset[str]                             # GSUB and GPOS feature tags
    axes: tuple[tuple[str, float, float, float], ...]    # (tag, min, default, max) per variation axis
    names: frozenset[str]                                # name IDs 1, 4 and 6, lower-cased (what libass matches)
    weight: int                                          # OS/2 usWeightClass of the default instance
    metrics: tuple[int, int, int]                        # (unitsPerEm, usWinAscent, usWinDescent)


@lru_cache(maxsize=32)
def inspect_font(path: str) -> FontInfo:
    """The font file's cmap coverage, layout features, axis bounds and the metadata libass reads."""
    with TTFont(path, lazy=True) as font:
        cmap = font.getBestCmap() or {}
        coverage = frozenset(cp for cp, glyph in cmap.items() if glyph != ".notdef")
        features = frozenset(
            record.FeatureTag
            for tag in ("GSUB", "GPOS") if tag in font
            for record in getattr(font[tag].table.FeatureList, "FeatureRecord", ()))
        axes = tuple((axis.axisTag, axis.minValue, axis.defaultValue, axis.maxValue)
                     for axis in font["fvar"].axes) if "fvar" in font else ()
        names = frozenset(record.toUnicode().strip().lower() for record in font["name"].names
                          if record.nameID in (1, 4, 6))
        os2 = font["OS/2"]
        return FontInfo(coverage, features, axes, names, os2.usWeightClass,
                        (font["head"].unitsPerEm, os2.usWinAscent, os2.usWinDescent))


def missing_glyphs(text: str, font) -> str:
    """The characters of `text` (white space aside) that `font`'s cmap does not map: what a fallback face must
    supply, or the reason a field cannot be set in this face."""
    coverage = inspect_font(str(font.path)).coverage
    return "".join(dict.fromkeys(ch for ch in text if not ch.isspace() and ord(ch) not in coverage))


@dataclass(frozen=True)
class Glyph:
    index: int        # glyph id in the face
    cluster: int      # the run's character index this glyph belongs to
    x: float          # pen position (px) of the glyph origin, offsets and tracking included
    y: float


@dataclass(frozen=True)
class GlyphRun:
    glyphs: tuple[Glyph, ...]
    advance: float    # the run's width (px): advances, kerning and the tracking between clusters


@lru_cache(maxsize=32)
def _face(path: str) -> hb.Face:
    return hb.Face(Path(path).read_bytes())


def _feature_values(features: tuple[str, ...], tracking: float) -> dict[str, bool]:
    # Tracking switches the optional ligatures off (a ligature would close the space tracking opens; libass does
    # the same); required shaping and mark attachment stay on. "-tag" switches a feature off.
    values = {"liga": not tracking, "clig": not tracking}
    values.update({feature.lstrip("-"): not feature.startswith("-") for feature in features})
    return values


@lru_cache(maxsize=16384)
def _shape(path: str, size: float, variations: tuple[tuple[str, float], ...], text: str,
           tracking: float, features: tuple[str, ...]) -> GlyphRun:
    if not text:
        return GlyphRun((), 0.0)
    font = hb.Font(_face(path))
    scale = round(size * 64)
    font.scale = (scale, scale)
    font.ppem = (round(size), round(size))
    if variations:
        font.set_variations(dict(variations))
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    hb.shape(font, buffer, _feature_values(features, tracking))
    infos, positions = buffer.glyph_infos, buffer.glyph_positions
    glyphs = []
    x = y = 0.0
    for index, (info, pos) in enumerate(zip(infos, positions)):
        glyphs.append(Glyph(info.codepoint, info.cluster, x + pos.x_offset / 64, y + pos.y_offset / 64))
        x += pos.x_advance / 64
        y += pos.y_advance / 64
        if index + 1 < len(infos) and infos[index + 1].cluster != info.cluster:
            x += tracking          # between clusters only: a base letter keeps its combining marks
    return GlyphRun(tuple(glyphs), x)


def shape(text: str, font, tracking: float = 0, features=None) -> GlyphRun:
    """`text` shaped in `font` (its size and variation instance) with `features` (None: default_features) and
    `tracking` px between clusters."""
    return _shape(str(font.path), float(font.size), getattr(font, "_rr_variations", ()),
                  text, float(tracking), resolve_features(text, features))


@lru_cache(maxsize=16)
def _encoded_glyphs(path: str) -> dict[int, str]:
    """Glyph id -> a character the source cmap maps to it (the lowest code point), for glyphs with Unicode access."""
    with TTFont(path, lazy=True) as font:
        result: dict[int, str] = {}
        for codepoint, name in sorted((font.getBestCmap() or {}).items()):
            result.setdefault(font.getGlyphID(name), chr(codepoint))
        return result


@lru_cache(maxsize=16)
def _glyph_font_data(path: str) -> bytes:
    """The face with a private glyph-id cmap added in memory (the bundled file is never changed).

    HarfBuzz has already chosen and placed the glyphs; this lets Pillow rasterise a chosen glyph that has no
    Unicode mapping of its own (a small capital, a case form, a ligature) without shaping it again. The source
    mappings stay in the table: FreeType's auto-hinter classifies outlines through the Unicode cmap.
    """
    with TTFont(path, recalcTimestamp=False) as font:
        order = font.getGlyphOrder()
        if len(order) > 65534:
            raise ValueError("Font exceeds the reserved glyph-address range")
        cmap = CmapSubtable.newSubtable(12)
        cmap.platformID, cmap.platEncID, cmap.language = 3, 10, 0
        cmap.cmap = dict(font.getBestCmap() or {})
        cmap.cmap.update({_GLYPH_CODEPOINT + index: name for index, name in enumerate(order)})
        font["cmap"].tables = [cmap]
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()


@lru_cache(maxsize=128)
def _glyph_font(path: str, size: float, variations: tuple[tuple[str, float], ...]):
    font = ImageFont.truetype(io.BytesIO(_glyph_font_data(path)), size, layout_engine=ImageFont.Layout.RAQM)
    axes = inspect_font(path).axes
    if axes:
        chosen = dict(variations)
        font.set_variation_by_axes([chosen.get(tag, default) for tag, _lo, default, _hi in axes])
    return font


def draw_run(draw, xy, text: str, font, fill, anchor="la", tracking=0, stroke=0, stroke_fill=None, features=None):
    """Draws `text` as shape() measures it at `xy` (a Pillow anchor) and returns the drawn box (stroke included).

    Untracked, Pillow's raqm draws the run whole with the same features. Tracked, each glyph HarfBuzz chose is
    drawn at its shaped position, the strokes of the whole run first and the fills over them (as Pillow draws a
    stroked run), so a kerned neighbour's stroke never cuts into a letter."""
    features = resolve_features(text, features)
    if not tracking:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor, features=list(features),
                  stroke_width=stroke, stroke_fill=stroke_fill)
        return tuple(round(value) for value in draw.textbbox(
            xy, text, font=font, anchor=anchor, features=list(features), stroke_width=stroke))
    run = shape(text, font, tracking, features)
    if not run.glyphs:
        return (round(xy[0]), round(xy[1]), round(xy[0]), round(xy[1]))
    if len(anchor) != 2 or anchor[0] not in "lmr" or anchor[1] not in "atmsbd":
        raise ValueError(f"Unsupported text anchor: {anchor}")
    x = xy[0] - (run.advance / 2 if anchor[0] == "m" else run.advance if anchor[0] == "r" else 0)
    # the vertical anchor from the same run, set with the same features
    a = font.getbbox(text, anchor="l" + anchor[1], features=list(features))
    baseline = font.getbbox(text, anchor="ls", features=list(features))
    y = xy[1] + a[1] - baseline[1]
    path = str(font.path)
    encoded = _encoded_glyphs(path)
    raster_features = ["-" + tag for tag in sorted(inspect_font(path).features)]   # draw the chosen glyph as is
    variations = getattr(font, "_rr_variations", ())
    placed = []
    for glyph in run.glyphs:
        char = encoded.get(glyph.index)
        raster_font = font if char is not None else _glyph_font(path, font.size, variations)
        if char is None:
            char = chr(_GLYPH_CODEPOINT + glyph.index)
        placed.append(((x + glyph.x, y - glyph.y), char, raster_font))
    passes = ((stroke_fill if stroke_fill is not None else fill, stroke), (fill, 0)) if stroke else ((fill, 0),)
    for ink, width in passes:
        for position, char, raster_font in placed:
            draw.text(position, char, font=raster_font, fill=ink, anchor="ls", stroke_width=width,
                      features=raster_features)
    boxes = [draw.textbbox(position, char, font=raster_font, anchor="ls", stroke_width=stroke,
                           features=raster_features) for position, char, raster_font in placed]
    return (math.floor(min(box[0] for box in boxes)), math.floor(min(box[1] for box in boxes)),
            math.ceil(max(box[2] for box in boxes)), math.ceil(max(box[3] for box in boxes)))
