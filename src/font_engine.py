"""OpenType inspection and one shaped glyph run for measured, tracked and featured text (QF-21).

fontTools reads what a bundled face really carries (its cmap coverage, layout features, variation axes, vertical
metrics and the names libass matches); uharfbuzz shapes a run once, with the face's GPOS kerning and the requested
OpenType features, so a tracked line is measured and drawn from the same glyph positions. Pillow (raqm) still
rasterises the bundled outlines.

The API takes the face per call (a FreeTypeFont from typography.get_font): a caller that sets a field in a fallback
face (QF-94) passes that face, and its coverage comes from inspect_font(face.path).coverage.

Fallback faces (QF-94): a field (one string a design draws) that its face cannot map whole is never drawn with a
letter missing. field_face() sets the whole field in the first bundled fallback face that maps every character of
it, at the weight, width and cap height measured to match the face it stands in for; when no bundled face maps them
it raises GlyphRefused, whose reason the run's glyph ledger (glyph_faces.jsonl) carries to the review card. Every
fallback and refusal is logged and recorded once per run. Nothing here names a face: the brand face lists its
fallbacks (typography.get_font), so a changed brand face (QF-87) keeps the mechanism.
"""

import io
import json
import logging
import math
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

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
    tracked run (draw_run) and a plate's mask. A caller's explicit `features` list wins.

    A string this face cannot map whole is measured and drawn in its field_face (QF-94): never a missing-glyph box,
    never a dropped letter, and GlyphRefused when no bundled face maps it."""

    def getlength(self, text, mode="", direction=None, features=None, language=None):
        face = field_face(text, self)
        if face is not self:
            return face.getlength(text, mode, direction, features, language)
        return super().getlength(text, mode, direction, list(resolve_features(text, features)), language)

    def getbbox(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None):
        face = field_face(text, self)
        if face is not self:
            return face.getbbox(text, mode, direction, features, language, stroke_width, anchor)
        return super().getbbox(text, mode, direction, list(resolve_features(text, features)), language,
                               stroke_width, anchor)

    def getmask(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None,
                ink=0, start=None):
        face = field_face(text, self)
        if face is not self:
            return face.getmask(text, mode, direction, features, language, stroke_width, anchor, ink, start)
        return super().getmask(text, mode, direction, list(resolve_features(text, features)), language,
                               stroke_width, anchor, ink, start)

    def getmask2(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None,
                 ink=0, start=None, *args, **kwargs):
        face = field_face(text, self)
        if face is not self:
            return face.getmask2(text, mode, direction, features, language, stroke_width, anchor, ink, start,
                                 *args, **kwargs)
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


def _ignorable(ch: str) -> bool:
    """White space and the invisible format characters (soft hyphen, joiners, direction marks, variation
    selectors): HarfBuzz draws them as nothing when a face does not map them, so they need no glyph."""
    return ch.isspace() or unicodedata.category(ch) == "Cf" or 0xFE00 <= ord(ch) <= 0xFE0F


@lru_cache(maxsize=64)
def _covers_ascii(path: str) -> bool:
    coverage = inspect_font(path).coverage
    return all(cp in coverage for cp in range(0x20, 0x7F))


def _missing(path: str, text: str) -> str:
    if text.isascii() and _covers_ascii(path):     # the common case, on every measure: no lookup
        return ""
    return _missing_any(path, text)


@lru_cache(maxsize=65536)
def _missing_any(path: str, text: str) -> str:
    coverage = inspect_font(path).coverage
    # canonically composed first: HarfBuzz composes a base and its mark into the face's precomposed letter
    nfc = unicodedata.normalize("NFC", text)
    return "".join(dict.fromkeys(ch for ch in nfc if ord(ch) not in coverage and not _ignorable(ch)))


def missing_glyphs(text: str, font) -> str:
    """The characters of `text` (white space and invisible format characters aside, read in composed form) that
    `font`'s cmap does not map: what a fallback face must supply, or the reason a field cannot be set in this
    face."""
    return _missing(str(font.path), text)


# ------------------------------------------------------------------ fallback faces (QF-94)
REFERENCE = "HAMBURGEFONSTIV"     # the capitals a face's width and colour are measured on
_PROBE_PX = 200


def scripts_of(chars: str) -> list[str]:
    """The Unicode scripts (the first word of each character's name: Greek, Cyrillic, Arabic...) of `chars`."""
    names = (unicodedata.name(ch, f"U+{ord(ch):04X}").split(" ")[0] for ch in chars)
    return sorted({n if len(n) <= 3 else n.title() for n in names})


class GlyphRefused(ValueError):
    """A field no bundled face can draw whole: it is not drawn (never a dropped letter); `reason` says why."""

    def __init__(self, text: str, face: str, missing: str):
        self.text, self.face, self.missing, self.code = text, face, missing, "no_glyph"
        self.scripts = scripts_of(missing)
        self.reason = (f"no bundled face draws {' '.join(missing)} ({', '.join(self.scripts)}), so {text!r} "
                       f"is not drawn (face {face})")
        super().__init__(self.reason)


def _instance(path: str, size: float, values: dict) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    axes = inspect_font(path).axes
    if axes:
        font.set_variation_by_axes([values.get(tag, default) for tag, _lo, default, _hi in axes])
    return font


def _profile(font) -> tuple[float, float, float]:
    """(cap height px, REFERENCE's advance in cap heights, its ink density): a face's size, width and weight."""
    cap = -font.getbbox("H", anchor="ls")[1]
    advance = font.getlength(REFERENCE)
    im = Image.new("L", (math.ceil(advance) + 40, math.ceil(cap * 2) + 40), 0)
    ImageDraw.Draw(im).text((20, round(cap * 1.4) + 20), REFERENCE, font=font, fill=255, anchor="ls")
    ink = float(np.asarray(im, dtype=np.float64).sum()) / 255.0
    return float(cap), advance / cap, ink / (advance * cap)


def _solve(measure, lo: float, hi: float, target: float, steps: int = 14) -> float:
    """The axis value in [lo, hi] at which the increasing `measure` reaches `target` (a bound when it cannot)."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if measure(mid) < target else (lo, mid)
    return round((lo + hi) / 2, 1)


@lru_cache(maxsize=64)
def fallback_match(brand_path: str, brand_variations: tuple, fallback_path: str) -> tuple[tuple, float]:
    """The fallback face's axis values and size factor that match the brand face's instance: its width (REFERENCE's
    advance per cap height, on `wdth`), its colour (ink density, on `wght`) and its cap height (the factor).
    Measured on the faces themselves, so it holds for any brand face."""
    target = _profile(_instance(brand_path, _PROBE_PX, dict(brand_variations)))
    bounds = {tag: (lo, hi) for tag, lo, _default, hi in inspect_font(fallback_path).axes}
    values = {tag: default for tag, _lo, default, _hi in inspect_font(fallback_path).axes}

    def measured(tag, index):
        return lambda v: _profile(_instance(fallback_path, _PROBE_PX, {**values, tag: v}))[index]
    for _ in range(2):           # width moves colour a little and colour moves width: settle both
        for tag, index in (("wdth", 1), ("wght", 2)):
            if tag in bounds:
                values[tag] = _solve(measured(tag, index), *bounds[tag], target[index])
    factor = target[0] / _profile(_instance(fallback_path, _PROBE_PX, values))[0]
    return tuple(values.items()), round(factor, 4)


@lru_cache(maxsize=256)
def _fallback_font(fallback_path: str, brand_path: str, brand_variations: tuple, size: float):
    values, factor = fallback_match(brand_path, brand_variations, fallback_path)
    px = max(1, round(size * factor))
    font = FeaturedFont(fallback_path, px)
    # `.size` is the size the field was asked for (the brand face's), so a renderer that rebuilds a family's font
    # from a fitted font's size, or spaces lines by it, gets the design size; the face is rasterised at `_rr_px`,
    # its cap height matched to the brand face's (QF-94 review 1).
    font.size, font._rr_px = size, px
    axes = inspect_font(fallback_path).axes
    if axes:
        chosen = dict(values)
        font.set_variation_by_axes([chosen.get(tag, default) for tag, _lo, default, _hi in axes])
        font._rr_variations = tuple((tag, chosen.get(tag, default)) for tag, _lo, default, _hi in axes)
    font._rr_fallback_for = Path(brand_path).name
    return font


def raster_px(font) -> float:
    """The size `font` is rasterised at: its own size, or a fallback face's matched size (_fallback_font)."""
    return float(getattr(font, "_rr_px", font.size))


def choose_face(text, font):
    """The face `text` would be drawn in (as field_face), or None when no bundled face maps it; records nothing."""
    if not isinstance(text, str) or not text or getattr(font, "path", None) is None:
        return font
    path = str(font.path)
    if not _missing(path, text):
        return font
    for fallback in getattr(font, "_rr_fallbacks", ()):
        if not _missing(fallback, text):
            face = _fallback_font(fallback, path, getattr(font, "_rr_variations", ()), font.size)
            face._rr_family = getattr(font, "_rr_family", None)
            face._rr_brand_font = font
            return face
    return None


@lru_cache(maxsize=16)
def _plain_face(path: str, size: float, variations: tuple) -> ImageFont.FreeTypeFont:
    font = ImageFont.FreeTypeFont(path, size)
    if variations:
        font.set_variation_by_axes([value for _tag, value in variations])
    return font


def plain_face(font) -> ImageFont.FreeTypeFont:
    """`font` itself, without the field-face lookup (a plain FreeTypeFont of its file, size and instance): it
    measures and masks missing glyphs as the face's .notdef and never refuses. Only for text another renderer
    draws (libass's captions, which take a system face for what the bundled faces lack) and for an operator-only
    sheet that is not art (the photo contact sheet's archive titles), never for art."""
    return _plain_face(str(font.path), raster_px(font), tuple(getattr(font, "_rr_variations", ())))


def field_face(text, font):
    """The face `text` is drawn in: `font` when it maps every character, else the first of its bundled fallback
    faces (font._rr_fallbacks, in order) that maps them all, matched to it (fallback_match): the whole field in
    one face. GlyphRefused when none does. Each fallback and refusal is recorded (record_glyph_event)."""
    face = choose_face(text, font)
    if face is font:
        return font
    path = str(font.path)
    missing = _missing(path, text)
    if face is not None:
        record_glyph_event("fallback", text, Path(path).name, missing, Path(str(face.path)).name)
        return face
    record_glyph_event("refused", text, Path(path).name, missing)
    raise GlyphRefused(text, Path(path).name, missing)


def brand_font(font):
    """The face a field was asked for (the brand face a fallback stands in for), to refit it at another size."""
    return getattr(font, "_rr_brand_font", None) or font


# The run's glyph ledger: ops.RunLog points it at output/<run>/glyph_faces.jsonl for the run's length; each event
# is written there once (and logged once), and the review card lists them (telegram_bot.glyph_notes).
LEDGER_NAME = "glyph_faces.jsonl"
_LEDGER: dict = {"path": None, "seen": set()}
_LEDGER_LOCK = threading.Lock()
_FIELD = threading.local()        # the field this thread is fitting and drawing (typography.drawable sets it)


def begin_field(text: str) -> None:
    """`text` is the whole field this thread fits and draws next: its lines, words and fitting trials are parts of
    it, recorded as parts of it (part_of)."""
    _FIELD.text = text


@contextmanager
def surface(where: str):
    """What this thread is drawing ("Story frame 2", "cover", "end card", "graphic map", "design card chapter",
    "carousel slide 3", "captions"): every glyph event inside is labelled with it, for the review card."""
    before = (getattr(_FIELD, "where", None), getattr(_FIELD, "text", None))
    _FIELD.where, _FIELD.text = where, None
    try:
        yield
    finally:
        _FIELD.where, _FIELD.text = before


def current_surface() -> str | None:
    return getattr(_FIELD, "where", None)


def on_surface(where: str, fn, *args, **kwargs):
    """fn(*args, **kwargs) drawn on the surface `where` (surface): for a stage submitted to a worker thread."""
    with surface(where):
        return fn(*args, **kwargs)


def set_glyph_ledger(path) -> None:
    with _LEDGER_LOCK:
        _LEDGER["path"], _LEDGER["seen"] = (Path(path) if path else None), set()


def record_glyph_event(kind: str, text: str, face: str, chars: str, fallback: str | None = None) -> None:
    """One string set in a fallback face ("fallback"), refused ("refused"), with emoji removed by the art gate
    ("emoji_removed"), or a caption whose characters no bundled face draws ("caption_system_face"): logged, and
    written to the run's glyph ledger once per surface. Every distinct string is written: one that is a part (a
    line, a word, a fitting trial) of the field this thread is drawing (begin_field) carries `part_of`, so the
    review card lists the field once. Never raises."""
    where = getattr(_FIELD, "where", None)
    key = (kind, text, face, fallback, where)
    field = getattr(_FIELD, "text", None)
    part_of = field if field and text != field and text.rstrip("…") in field else None
    with _LEDGER_LOCK:
        if key in _LEDGER["seen"]:
            return
        _LEDGER["seen"].add(key)
        path = _LEDGER["path"]
    event = {"at": datetime.now(UTC).isoformat(), "kind": kind, "where": where, "text": text, "face": face,
             "fallback": fallback, "chars": chars, "scripts": scripts_of(chars)}
    if part_of:
        event["part_of"] = part_of
    if part_of:
        log.debug("Glyphs: %s part %.80r of %.80r (%s)", kind, text, part_of, where)
    elif kind == "refused":
        log.warning("No bundled face draws %s (%s): %.80r is refused (face %s, %s)", " ".join(chars),
                    ", ".join(event["scripts"]), text, face, where or "no surface named")
    else:
        log.info("Glyphs: %s %.80r in %s for %s (face %s)", kind, text, fallback or face, " ".join(chars), face)
    if path is None:
        return
    try:
        with _LEDGER_LOCK, path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        log.debug("Glyph event not recorded: %s", e)


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


def shape_in_face(text: str, font, tracking: float = 0, features=None) -> GlyphRun:
    """`text` shaped in `font` itself, missing glyphs and all (their .notdef advance): for a measure of text that
    another renderer draws (libass's captions, which take a system face for what the bundled ones lack)."""
    return _shape(str(font.path), raster_px(font), getattr(font, "_rr_variations", ()),
                  text, float(tracking), resolve_features(text, features))


def shape(text: str, font, tracking: float = 0, features=None) -> GlyphRun:
    """`text` shaped in `font` (its size and variation instance) with `features` (None: default_features) and
    `tracking` px between clusters. A field `font` cannot map whole is shaped in its field_face (QF-94)."""
    font = field_face(text, font)
    return _shape(str(font.path), raster_px(font), getattr(font, "_rr_variations", ()),
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
    stroked run), so a kerned neighbour's stroke never cuts into a letter. A field `font` cannot map whole is drawn
    in its field_face (QF-94)."""
    font = field_face(text, font)
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
        raster_font = font if char is not None else _glyph_font(path, raster_px(font), variations)
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
