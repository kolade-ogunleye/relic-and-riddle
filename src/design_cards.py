"""
src/design_cards.py
===================
Four new design types (build spec 2026-11-03, "design-cards-1"): the hook frame (frame 0 of a Reel, which doubles
as its cover), the chapter card, the myth-vs-fact card and the quote card. Each one is drawn in the house look of
src/graphics.py and src/cover_design.py: Anton display caps, Montserrat sentence and kicker type, IBM Plex Mono
for data, the brand tokens only (INK ground, BONE text, GOLD figures and rules, STONE for what steps back), every
line a measured TextPlate (typography.plan_text) re-measured on the graded frame, every rule and strike a measured
non-text mark (graphics.record_nontext).

A card line comes only from the fact sheet's checked card fields (src/card_fields.py: statements, myths, quotes,
each with the project fact checker's verdict and runs recorded on the sheet), referenced by id (CardRef), plus the
gate-passed title and youtube_title (Gated) for the hook and chapter cards (operator decision 2026-11-03, see "line
provenance"). The earlier NLP check (ground_text) still runs on every line as a second guard that can only refuse.
A card that cannot be drawn
whole at its stated sizes refuses (CardRefused) and render_* writes the reason beside the clip instead of a
partial card. plan_* and frame_at are pure and need no ffmpeg; render_* encode with graphics._encode.

Nothing imports this module yet (the composer has to honour `caption_clear_until_s` before the hook is wired in).
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from . import brand
from .brand import (
    BONE,
    DATA_FONT,
    GOLD,
    INK,
    PLACE_TARGET,
    RGB,
    STONE_LT,
    TEXT_CONTRAST,
)
from .config import CAPTION_READ_BASE, CAPTION_READ_PER_CHAR, VIDEO_FPS
from .cover_design import (
    EYEBROW_PX,
    EYEBROW_TRACK,
    QUALIFIERS,
    _brand,
    _provenance,
    figure_check,
    grid_proof,
    reviewed_focus,
)
from .end_card import LOOP_PUSH
from .graphics import (
    FIGURE_SETTLE,
    FIGURE_SETTLE_FROM,
    NONTEXT_BAND,
    NONTEXT_MIN,
    GroundDrift,
    _ease,
    _ease_io,
    _encode,
    _pushed_in,
    _twin,
    _WideGrid,
    draw_plate,
    figure_text,
    graded_frame,
    mark_mask,
    nontext_contrast,
    record_contrast,
    record_nontext,
    spine_colour,
    timeline_backdrop,
)
from .multi_format import typeset
from .photo_hygiene import remap_focus, without_caption_strip
from .safe_zones import graphics_box
from .still_layout import Layout
from .typography import (
    _FIGURE,
    _lin,
    _unit,
    balanced_wrap,
    cover_crop,
    drawable,
    get_font,
    plan_text,
)
from .video_composer import TRANS_DUR

log = logging.getLogger(__name__)

VERSION = "design-cards-1"
HOOK_SECONDS = 1.5
# s: every hook element at full alpha before this (frames 0-32: frame 32 is 1.067 s). Freeze panel round 4 (review:
# with H6's HOOK_FADE 0.16 a 1.10 hold left the type at 0.156 alpha at 1.22 s, against <= 0.10): 1.09, so the type
# is at 0.092 at 1.22 s and gone at 1.25, before the plate and wash leave from HOOK_GROUND_HOLD (was 1.10).
# Fix round 1 (review: measure at the spec time, not a chosen instant): the frame on screen at 1.22 s is frame 36
# (1.200 s), which a 1.09 hold left at 0.21 alpha; 1.07 puts frame 36 at 0.092 and keeps frames 0-32 whole (32 is
# 1.067 s), gone by frame 37 (1.233 s)
HOOK_HOLD = 1.07
HOOK_FADE = 0.16            # s: freeze panel H6 (5): then a smoothstep dissolve of the type (H4: 0.24)
HOOK_GROUND_HOLD = 1.22     # s: the plate and wash dissolve a beat after the type (H6: was 1.16) ...
HOOK_GROUND_FADE = 0.18     # s: ... over this, so they end at CAPTION_CLEAR
CAPTION_CLEAR = 1.40        # s: the composer's first caption burst may start here
HOOK_TARGET = 5.1           # the hook's planned contrast (the brand's own plate keeps PLACE_TARGET)
REVEAL = 0.40               # s: a line's reveal
RISE = 14                   # design px a revealing line rises from
STEP_BACK = 0.40            # s: the myth's BONE -> STONE crossfade
STRIKE_PEN = 0.45           # s: the pen across the widest myth line (a one-line myth's whole strike)
STRIKE_LINE_MIN = 0.16      # s: freeze panel 2026-11-04 (M6): each line's own stroke is at least this
STRIKE_LINE_GAP = 0.033     # s: between one line's stroke ending and the next starting (M11: one frame, was 0.05)
STRIKE_PEN_MAX = 0.80       # s: every stroke is scaled down so the whole pen fits this (a 1- or 2-line myth) ...
STRIKE_PEN_MAX_LONG = 0.95  # s: ... or this, for a myth of 3 lines or more (freeze panel M11)
STRIKE_LINE_FLOOR = 0.20    # s: a scaled stroke stays at least this where the pen's budget allows (M11)
STRIKE_SETTLE = 0.10        # s: the pen's end to the fact's kicker and the myth's step-back
UPSCALE_MAX = 2.0
MAX_SECONDS = 10.0
MIN_SECONDS = 6.0
CHAPTER_SECONDS = {False: 2.5, True: 3.5}   # 9:16, 16:9
FOCUS_OVERLAP = 0.10
KICKER_WIDE_PX = 40         # freeze panel M9: the 16:9 kicker labels (MYTH, FACT, CHAPTER), was 34
# the source line's baseline below the last fact baseline (round 2 review 10: 52 left ~22 px of air on 9:16 under
# a 60 px fact line; 72 gives the data line the air of a fact line step)
SOURCE_DROP = {False: 72, True: 68}     # 16:9: max(68, round(0.58 x P)) (freeze panel M10, was 56): _source_drop
SOURCE_DROP_WIDE_SHARE = 0.58
FACT_CHARS_WIDE = 96        # the 16:9 myth card's fact: the measured 4-line ceiling of its column
CHAPTER_BLUR = 36          # design px (the myth's: a photo's burned-in words never read)
CHAPTER_INNER = (0.06, 0.26)
CHAPTER_ABOVE = (0.08, 0.32)
CHAPTER_BELOW = (0.20, 0.70)
CHAPTER_FEATHER = (280, 560)
CHAPTER_WIDE_ONE = (184, 176, 168, 160, 152, 144, 136, 128, 120)   # 16:9 one-line title sizes, largest first
CHAPTER_WIDE_TWO = (176, 168, 160, 152, 144, 136, 128, 120, 112, 104, 96)
# freeze panel C2: two 16:9 title lines' cap-top to last baseline stand no taller than the shown numeral's measured
# ink height (plan_chapter_card: num_tall), so the title's cap top meets the numeral's ink top (was a fixed 330)
CHAPTER_GAP = 66            # 16:9 chapter: design px from the numeral's ink to the divider, and divider to title
HOOK_TOP_AIR = 56           # freeze panel H6: the moved claim block's top stands this far under the drawn masthead's
                            # ink foot (re-measured, so a smaller masthead moves it up)
HOOK_TOP_BLOCK = 565        # the claim block's top when moved off the reviewed subject (freeze panel H3: masthead
                            # foot + 56, was 547)
# freeze panel H1: the claim's size by its line count, the largest whose lines' right ink stays inside x 888
# freeze panel H6 (1): the 2-line ladder from 176 (was 152)
HOOK_SIZES = {1: (192, 176, 160, 144, 128), 2: (176, 168, 160, 152, 144, 136, 128, 120, 112),
              3: tuple(range(128, 111, -8))}
HOOK_MEASURE = 788          # flush left from x 100 to the Reel box's right edge (888)
HOOK_MEASURE_CENTRED = 696  # centred on x 540, inside 192..888
HOOK_EYEBROW_ALPHA = 0.8    # freeze panel H2: the eyebrow in BONE at this alpha, so the claim holds the only gold type
HOOK_SCRIM_HUE = (15.0, 55.0)   # freeze panel H5: a warm, saturated ground under the block (hue degrees, saturation
HOOK_SCRIM_SAT = 90             # of 255) gets a soft radial INK scrim behind it
HOOK_SCRIM_FILL = INK       # the house INK (the panel's #0B0D10 is not a brand token)
HOOK_SCRIM_ALPHA = 0.55
HOOK_SCRIM_REACH = 120      # design px past the block
MASTHEAD_FOOT_AIR = 523     # a disclosure plate above this collides with the masthead (the 64 px masthead's foot + 14)
MASTHEAD_FOOT_PAD = 14      # freeze panel H6: the disclosure's top stays this far under the drawn masthead's ink foot
# freeze panel H6 (2), declined: a 44 px hook masthead fails the profile-grid gate (grid_proof: its cap draws 3.6 px at
# the 124 px tile, floor 5), so the hook keeps the covers' 64 px masthead; only its foot is re-measured here
HOOK_BRAND_PX = 64
HOOK_PLATE_FEATHER = 64     # freeze panel H6 (4): design px over which the plate's top and bottom edges feather
EYEBROW_SCRIM_AIR = 56      # design px: the hook's wash is flat from this far above the eyebrow rule
RULE_HALO_PX = 12           # the eyebrow rule's INK halo: its feather past the measuring band
DISCLOSURE_AIR = 32         # design px between the disclosure plate's foot and the claim block
GRAPHIC_NAME = re.compile(r"^graphic_(chapter|mythfact|quote)_\d{2}\.mp4$")

LIMITERS = tuple(sorted({q.lower() for q in QUALIFIERS} | {
    "approximately", "estimated", "by one estimate", "may", "might", "possibly", "probably", "likely", "perhaps",
    "believed", "thought", "not", "no", "never", "without"}, key=len, reverse=True))
SUPERLATIVES = ("no one", "only", "first", "last", "oldest", "largest", "biggest", "tallest", "deepest", "longest",
                "never", "always", "all")
MYTH_MARKERS = re.compile(r"\b(myths?|misconceptions?|popular belief|popularly|legends?|often said|often claimed|"
                          r"commonly said|commonly believed|wrongly|debunked|disproved|disproven|mistaken|"
                          r"once thought|contrary to)\b", re.IGNORECASE)
_STOP = frozenset(["a", "an", "the", "of", "in", "on", "at", "to", "by", "for", "from", "with", "and", "or", "but", "nor", "is", "are", "was", "were", "be", "been", "being", "am", "it", "its", "it's", "this", "that", "these", "those", "as", "into", "onto", "than", "then", "so", "such", "has", "have", "had", "do", "does", "did", "he", "she", "they", "we", "you", "i", "him", "her", "them", "us", "his", "hers", "their", "theirs", "our", "ours", "your", "who", "whom", "whose", "which", "what", "when", "where", "why", "how", "there", "here", "also", "very", "just", "s"])
_FACT_KEYS = ("id", "statement", "kind", "disputed", "hedge", "evidence")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORDS = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+(?:'[a-z]+)?")
_RANGE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:to|and|-|–|—)\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_FIG_RUN = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?:–\d[\d,]*(?:\.\d+)?)?\+?")
_MAGNITUDE = re.compile(r"(?:\s*(?:-|–|—|to)\s*\d[\d,]*(?:\.\d+)?)?[\s-]*(hundred|thousand|million|billion|trillion|"
                        r"bn|mn)\b", re.IGNORECASE)
_MAG_ALIAS = {"bn": "billion", "mn": "million"}
# spelled-out counts: a title that carries one cites a fact (round 1 review 12), and any role draws one only as the
# facts spell it ("one" is left out: "no one" is a superlative, and "one of" is not a count)
NUMBER_WORDS = frozenset((
    "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "dozen", "dozens", "hundred", "hundreds", "thousand", "thousands",
    "million", "millions", "billion", "billions", "trillion", "trillions", "twice", "thrice"))
_TITLE_PREFIX = re.compile(r"^(act|part|chapter|section)\s*\d+\s*[:.\-–]", re.IGNORECASE)
_PROBE = ImageDraw.Draw(Image.new("L", (8, 8)))
_ROLE_FACE = {"claim": ("Anton", None), "title": ("Anton", None), "fact": ("Montserrat", 700),
              "eyebrow": ("Montserrat", 800), "myth": ("Montserrat", 600)}


class CardRefused(ValueError):
    """A card that cannot be drawn whole and true; `reason` says why (written to <out>.design.json)."""

    def __init__(self, reason: str, code: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.code = code


# ------------------------------------------------------------------------------------------------ specs and plans
@dataclass(frozen=True)
class HookFrame:
    claim: str
    fact_ids: tuple[str, ...]
    photo: Path
    first_burst: str
    eyebrow: str = ""
    photo_kind: str = "photo"
    focus: tuple[int, int, int, int] | None = None
    run_myths: tuple[str, ...] = ()
    claim_src: CardRef | Gated | None = None      # a checked statement field, or the gated title (provenance)
    eyebrow_src: CardRef | Gated | None = None


@dataclass(frozen=True)
class ChapterCard:
    number: int
    total: int
    title: str
    photo: Path | None = None
    fact_ids: tuple[str, ...] = ()
    title_src: CardRef | Gated | None = None


@dataclass(frozen=True)
class MythFact:
    """A myth card: everything it draws is the checked myth field `myth_src` names (its whole myth text, its
    correction as the fact line, its refuting fact and source). The text and id fields, when given, are what the
    caller expects that field to hold; a mismatch refuses."""
    myth_text: str = ""
    myth_fact_id: str = ""
    fact_text: str = ""
    fact_id: str = ""
    photo: Path | None = None
    fact_at: float | None = None
    myth_src: CardRef | None = None               # CardRef("myths", id)
    fact_src: Any = None                          # retired: the fact line is the myth field's correction


@dataclass(frozen=True)
class QuoteCard:
    """A quote card: everything it draws is the checked quote field `quote_src` names. The text, speaker, fact and
    citation fields, when given, are what the caller expects that field to hold; a mismatch refuses."""
    text: str = ""
    speaker: str = ""
    fact_id: str = ""
    evidence_index: int | None = None
    role: str = ""
    work: str = ""
    year: int | None = None
    approximate: bool = False
    photo: Path | None = None
    quote_src: CardRef | None = None              # CardRef("quotes", id): the checked quote field drawn


@dataclass(frozen=True)
class Element:
    name: str
    kind: str        # "text" | "mark"
    text: str
    face: str
    weight: int | None
    px: int
    fill: RGB
    xy: tuple[float, float]
    anchor: str
    track: float
    box: tuple[int, int, int, int]
    t_in: float
    t_full: float


@dataclass(frozen=True, eq=False)
class CardPlan:
    kind: str
    width: int
    height: int
    seconds: float
    frames: int
    elements: tuple[Element, ...]
    timeline: Mapping[str, tuple[float, float]]
    fact_ids: tuple[str, ...]
    drawn_text: Mapping[str, str]
    meta: Mapping[str, Any]


# ------------------------------------------------------------------------------------------------ small helpers
def _rhu(v: float) -> int:
    """Round half up (the spec's scaling rule)."""
    return math.floor(v + 0.5)


def _frames(seconds: float) -> tuple[int, float]:
    n = math.ceil(seconds * VIDEO_FPS - 1e-9)
    return n, n / VIDEO_FPS


def _read(s: str) -> float:
    """The house caption reading model (config.CAPTION_READ_BASE + per character)."""
    return CAPTION_READ_BASE + CAPTION_READ_PER_CHAR * len(s or "")


def _smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _hook_alpha(t: float, hold: float = HOOK_HOLD, fade: float | None = None) -> float:
    """Freeze panel H4: a real dissolve, 1 - smoothstep((t - hold) / fade): the type (claim, eyebrow, masthead) from
    HOOK_HOLD over HOOK_FADE, the plate and wash from HOOK_GROUND_HOLD over HOOK_GROUND_FADE (H6)."""
    if fade is None:
        fade = HOOK_GROUND_FADE if hold == HOOK_GROUND_HOLD else HOOK_FADE
    return 1.0 - _smoothstep((t - hold) / fade)


class _Geo:
    """Design px -> frame px: 9:16 by s = width / 1080; 16:9 through graphics._WideGrid."""

    def __init__(self, width: int, height: int):
        self.width, self.height = int(width), int(height)
        if width < height:
            if abs(height * 9 - width * 16) > 16:
                raise ValueError(f"{width}x{height} is not 9:16")
            self.wide, self.k, self._ox, self._oy = False, width / 1080, 0.0, 0.0
        elif width > height:
            if abs(width * 9 - height * 16) > 16:
                raise ValueError(f"{width}x{height} is not 16:9")
            g = _WideGrid(width, height)
            self.wide, self.k, self._ox, self._oy = True, g.k, g.ox, g.oy
        else:
            raise ValueError(f"{width}x{height} is neither 9:16 nor 16:9")

    def x(self, v: float) -> int:
        return _rhu(self._ox + v * self.k)

    def y(self, v: float) -> int:
        return _rhu(self._oy + v * self.k)

    def px(self, v: float) -> int:
        return max(1, _rhu(v * self.k))

    def box(self, b: Sequence[float]) -> tuple[int, int, int, int]:
        return self.x(b[0]), self.y(b[1]), self.x(b[2]), self.y(b[3])


@lru_cache(maxsize=4096)
def _ink(text: str, font: Any) -> tuple[int, int, int, int]:
    """The ink box of `text` drawn at the origin with anchor "ls" (left, baseline), from its rendered mask."""
    if not text:
        return 0, 0, 0, 0
    pad = font.size
    m = Image.new("L", (int(font.getlength(text)) + 2 * pad, 3 * pad), 0)
    ImageDraw.Draw(m).text((pad, 2 * pad), text, font=font, fill=255, anchor="ls")
    bb = m.getbbox()
    return (bb[0] - pad, bb[1] - 2 * pad, bb[2] - pad, bb[3] - 2 * pad) if bb else (0, 0, 0, 0)


def _width(text: str, font: Any) -> int:
    b = _ink(text, font)
    return b[2] - b[0]


def _tracked_width(text: str, font: Any, track: float) -> float:
    return font.getlength(text) + track * max(0, len(text) - 1)


WEAK_LINE_END = frozenset((
    "a", "an", "the", "of", "in", "on", "at", "to", "by", "for", "from", "with", "into", "onto", "over", "under",
    "and", "or", "but", "nor", "so", "yet", "as", "than", "that", "which", "who", "whom", "whose", "where", "when",
    "is", "are", "was", "were", "be", "been", "its", "their", "his", "her", "this", "these", "those", "not", "no"))
DESIGN_WRAP_SLACK = 12     # design px a 9:16 line broken at design size may overrun its measure once scaled
REWRAP_MAX_WORDS = 32      # the re-wrap search (every break set) runs on texts up to this many words


def _wrap(text: str, font: Any, measure: int, max_lines: int, min_lines: int = 1, even: bool = False,
          cohesive: bool = False) -> list[str] | None:
    """`text` in balanced lines no wider than `measure` (ink), never with a qualifier, a figure's magnitude or
    its unit on another line than the figure (round 2 review 7: such a break re-wraps instead of refusing);
    None when no such wrap fits `max_lines`. With `even` (display type: review 2026-11-03, "SLAVES / DRAGGED EVERY
    / ..." left one word alone on a line) the break set is always searched: the fewest lines, then no one-word line
    where the text has four words or more, then the most even widths. With `cohesive` (the myth card's fact, freeze
    panel round 4: "WHEN SUSPECTED / COESITE"), among breaks as good on those counts the fewest that part two content
    words (neither a WEAK_LINE_END nor a stop word, the first not closing on punctuation) come first, so a line
    breaks before a function word where the measure allows it."""
    lines = balanced_wrap(_PROBE, text, font, measure)
    if (not even and lines and min_lines <= len(lines) <= max_lines
            and all(_width(ln, font) <= measure for ln in lines) and not _qualifier_split(lines)):
        return lines
    words = text.split()
    if not words or len(words) > REWRAP_MAX_WORDS:
        return None
    widths: dict[tuple[int, int], int] = {}

    def wd(i: int, j: int) -> int:
        if (i, j) not in widths:
            widths[(i, j)] = _width(" ".join(words[i:j]), font)
        return widths[(i, j)]
    lo = max(min_lines, len(lines) if lines else 1)
    fallback = None
    for n in range(lo, max_lines + 1):
        best = None
        for cuts in itertools.combinations(range(1, len(words)), n - 1):
            bounds = (0, *cuts, len(words))
            spans = list(itertools.pairwise(bounds))
            ws = [wd(i, j) for i, j in spans]
            if max(ws) > measure:
                continue
            cand = [" ".join(words[i:j]) for i, j in spans]
            if _qualifier_split(cand):
                continue
            mean = sum(ws) / len(ws)
            lone = sum(j - i == 1 or all(w.lower().strip(".,;:") in _STOP for w in words[i:j]) for i, j in spans
                       ) if (even and n > 1 and len(words) >= 4) else 0
            # review 2026-11-03 (second round): a line that ends on an article, preposition, conjunction or bare
            # verb ("ALIENS BUILT THE / RING, WHICH IS", "ACROSS AND") hangs the phrase; such breaks come last
            # freeze panel W1: an article left at a line's end hangs worst, so among hanging breaks it comes last
            weak = sum((2 if w in ("a", "an", "the") else 1) for w in (words[j - 1].lower().strip(".,;:")
                                                                         for _, j in spans[:-1])
                       if w in WEAK_LINE_END) if even else 0
            # freeze panel W2 (operator: no line ends on THE or OF): within one size a break with no W1_STOPS end
            # always beats one with, whatever their balance
            stop = sum(words[j - 1].lower().strip(".,;:") in W1_STOPS for _, j in spans[:-1]) if even else 0
            split = sum(_parts_phrase(words[j - 1], words[j]) for _, j in spans[:-1]) if cohesive else 0
            score = (stop, lone, weak, split, sum((w - mean) ** 2 for w in ws), max(ws))  # most even, then narrowest
            if best is None or score < best[0]:
                best = (score, cand)
        if best and (best[0][2] == 0 or not even) and best[0][0] == 0:
            return fallback[1] if fallback and fallback[0][:3] <= best[0][:3] else best[1]
        if best and (fallback is None or best[0][0] < fallback[0][0]):
            fallback = best      # a hanging break: one more line is tried first
    return fallback[1] if fallback else None


def _parts_phrase(a: str, b: str) -> bool:
    """Freeze panel round 4 (cohesive wraps): a break between `a` and `b` parts two content words."""
    if a[-1:] in ",;:.":
        return False
    fn = WEAK_LINE_END | _STOP
    return a.lower().strip(".,;:") not in fn and b.lower().strip(".,;:") not in fn


def _qualifier_split(lines: Sequence[str]) -> bool:
    """True when a line ends on a limiter and the next starts with its figure (the hedge left on another line),
    or a line ends on a figure and the next starts with its magnitude word or unit ("2.3 / MILLION")."""
    singles = {lim for lim in LIMITERS}
    for a, b in itertools.pairwise(lines):
        words = [w.strip(".,;:").lower() for w in a.split()]
        tail = {" ".join(words[-2:]), words[-1] if words else ""}
        if tail & singles and b[:1].isdigit():
            return True
        head = b.split()[0].strip(".,;:").lower() if b.split() else ""
        if words and words[-1][:1].isdigit() and (head in _MAG_WORDS or (_unit(head) and head != "in")):
            return True
    return False


def _design_wrap(text: str, face: str, weight: int | None, px: float, measure: float, max_lines: int,
                 geo: _Geo, even: bool = False, cohesive: bool = False) -> list[str] | None:
    """The line breaks decided once at design size (1080 or 1920 wide), so every render size shows the same
    composition (round 2 review 9: 3 lines at 1080x1920, 4 at 720x1280). Only if a design line would not fit
    the scaled measure (font px rounding) is it re-broken at the frame's own size."""
    lines = _wrap(text, get_font(face, round(px), weight=weight), round(measure), max_lines, even=even,
                  cohesive=cohesive)
    if lines is None or geo.k == 1:
        return lines
    f = get_font(face, geo.px(px), weight=weight)
    # 9:16 lines may run DESIGN_WRAP_SLACK design px past the measure (font px rounding; the text column ends 28
    # px inside the graphics box); 16:9 columns end on title-safe itself, so they get none
    slack = 0 if geo.wide else DESIGN_WRAP_SLACK
    if all(_width(ln, f) <= geo.px(measure + slack) for ln in lines):
        return lines
    return _wrap(text, f, geo.px(measure), max_lines, even=even, cohesive=cohesive)


def _union(boxes: Sequence[Sequence[int]]) -> tuple[int, int, int, int]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


# ------------------------------------------------------------------------------------------------ measured lines
def _dilate(m: np.ndarray, k: int) -> np.ndarray:
    r = k // 2
    p = np.pad(m, r)
    h, w = m.shape
    rows = np.zeros((h + 2 * r, w), bool)
    for d in range(2 * r + 1):
        rows |= p[:, d:d + w]
    out = np.zeros((h, w), bool)
    for d in range(2 * r + 1):
        out |= rows[d:d + h]
    return out


def _erode3(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1)
    h, w = m.shape
    out = np.ones((h, w), bool)
    for dy in range(3):
        for dx in range(3):
            out &= p[dy:dy + h, dx:dx + w]
    return out


def _ratio(im: Image.Image, parts: Sequence[Any], cut: Sequence[tuple[int, int]] = ()) -> float | None:
    """WCAG ratio of one drawn line made of several plates (a BONE sentence with GOLD figure runs, or a struck
    myth line): the 90th-percentile luminance of the ring 2-10 px outside all of the line's glyphs (never on
    another of its glyphs, never on the rows in `cut`, the strike band) against each part's own glyph colour
    (the median of its eroded interior, outside `cut`); the lowest part's ratio. None off the frame."""
    parts = [p for p in parts if not p.empty and p.glyphs is not None]
    if not parts:
        return None
    x0 = min(p.origin[0] for p in parts)
    y0 = min(p.origin[1] for p in parts)
    x1 = max(p.origin[0] + p.glyphs.shape[1] for p in parts)
    y1 = max(p.origin[1] + p.glyphs.shape[0] for p in parts)
    W, H = im.size
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    rgb = np.asarray(im.convert("RGB").crop((cx0, cy0, cx1, cy1)))
    masks = []
    for p in parts:
        m = np.zeros((cy1 - cy0, cx1 - cx0), bool)
        ox, oy = p.origin[0] - cx0, p.origin[1] - cy0
        g = p.glyphs > 127
        sy0, sx0 = max(0, -oy), max(0, -ox)
        ey, ex = min(g.shape[0], m.shape[0] - oy), min(g.shape[1], m.shape[1] - ox)
        if ey > sy0 and ex > sx0:
            m[oy + sy0:oy + ey, ox + sx0:ox + ex] = g[sy0:ey, sx0:ex]
        masks.append(m)
    full = np.logical_or.reduce(masks)
    keep = np.ones(full.shape[0], bool)
    for a, b in cut:
        keep[max(0, a - cy0):max(0, b - cy0)] = False
    ring = _dilate(full, 21) & ~_dilate(full, 5) & keep[:, None]
    if ring.sum() < 50:
        return None
    lb = float(np.percentile(_lin(rgb[ring]), 90))
    worst = None
    for p, m in zip(parts, masks):
        inner = _erode3(m) & keep[:, None]
        if inner.sum() > 30:
            lt = float(np.median(_lin(rgb[inner])))
        else:
            from .typography import rel_luminance
            lt = rel_luminance(p.fill)
        r = (max(lt, lb) + 0.05) / (min(lt, lb) + 0.05)
        worst = r if worst is None else min(worst, r)
    return round(worst, 2) if worst is not None else None


class _LinePlate:
    """Several plates recorded and measured as one line (graphics.record_contrast reads these attributes)."""

    def __init__(self, parts: Sequence[Any], ground: Image.Image, cut: Sequence[tuple[int, int]] = (),
                 fallback: float | None = None):
        self.parts = [p for p in parts if not p.empty]
        self.empty = not self.parts
        self.cut = tuple(cut)
        self.text = "".join(p.text for p in parts)
        self.font = self.parts[0].font if self.parts else None
        self.fill = self.parts[0].fill if self.parts else BONE
        self.halo = max([p.halo for p in self.parts] or [0.0])
        self.box = _union([p.box for p in self.parts]) if self.parts else (0, 0, 0, 0)
        composed = ground.convert("RGB").copy()
        for p in self.parts:
            composed = draw_plate(composed, p)
        c = self.measure(composed)
        self.contrast = c if c is not None else fallback

    def measure(self, im: Image.Image) -> float | None:
        return _ratio(im, self.parts, self.cut)


# ------------------------------------------------------------------------------------------------ fact rules
def fact_by_id(sheet: dict, fact_id: str) -> dict:
    """The fact `fact_id` from a fact_sheet._apply_rules sheet, whole; CardRefused when unknown or incomplete."""
    for f in (sheet or {}).get("facts") or []:
        if isinstance(f, dict) and f.get("id") == fact_id:
            missing = [k for k in _FACT_KEYS if k not in f]
            if missing:
                raise CardRefused(f"fact {fact_id!r} lacks {', '.join(missing)}")
            return f
    raise CardRefused(f"unknown fact id {fact_id!r}")


# Text folded before it is tokenised (round 3 review 5, 8, 9): curly apostrophes; "per cent" to "%" (so "40
# percent" and "40%" are one figure and one unit); a negative contraction to its verb and "not" (so "isn't" is
# read as the negation it is, and a title cannot slip one past the negation rule).
_PERCENT = re.compile(r"(?<=\d)\s*(?:per\s?cent|pct)\b")
_NEG_CONTRACTION = re.compile(r"\b(can|won|shan|[a-z]+)n't\b")
_NEG_BASE = {"ca": "can", "wo": "will", "sha": "shall", "can": "can", "won": "will", "shan": "shall"}


def _fold(s: str) -> str:
    s = str(s or "").lower().replace("’", "'").replace("‘", "'")
    s = _PERCENT.sub("%", s)
    s = re.sub(r"\bcannot\b", "can not", s)
    return _NEG_CONTRACTION.sub(lambda m: f"{_NEG_BASE.get(m.group(1), m.group(1))} not", s)


def _words(s: str) -> list[str]:
    return _WORDS.findall(_fold(s))


# irregular past forms folded to their verb, so "built" and "build" are one word to every check (round 3 review 1:
# "Slaves built it" passed against "Slaves did not build it")
_IRREGULAR = {
    "built": "build", "made": "make", "took": "take", "taken": "take", "saw": "see", "seen": "see", "found": "find",
    "laid": "lay", "began": "begin", "begun": "begin", "wrote": "write", "written": "write", "said": "say",
    "stood": "stand", "held": "hold", "brought": "bring", "thought": "think", "rose": "rise", "risen": "rise",
    "fell": "fall", "fallen": "fall", "gave": "give", "given": "give", "knew": "know", "known": "know",
    "led": "lead", "ran": "run", "dug": "dig", "grew": "grow", "grown": "grow", "drew": "draw", "drawn": "draw",
    "came": "come", "went": "go", "gone": "go", "kept": "keep", "left": "leave",
    "lost": "lose", "met": "meet", "paid": "pay", "sent": "send", "spent": "spend", "stole": "steal",
    "stolen": "steal", "struck": "strike", "swept": "sweep", "threw": "throw", "thrown": "throw", "told": "tell",
    "wore": "wear", "worn": "wear", "won": "win", "hid": "hide", "hidden": "hide", "sank": "sink", "sunk": "sink",
    "bore": "bear", "borne": "bear", "carried": "carry", "buried": "bury", "died": "die", "lived": "live"}


def _stem(w: str) -> str:
    w = w.removesuffix("'s")
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _is_num(w: str) -> bool:
    return w[:1].isdigit()


def _content(s: str) -> set[str]:
    """The content words of `s`, stemmed, units folded to their abbreviation (round 3 review 9: "146 m tall" and
    "146 metres tall" carry one unit)."""
    return {_norm_word(w) for w in _words(s) if not _is_num(w) and w not in _STOP}


def _nums(s: str) -> list[str]:
    return [m.group(0).replace(",", "").rstrip(".") for m in _NUM.finditer(str(s or ""))]


def _num_mags(s: str) -> list[tuple[str, str]]:
    """Every figure in `s` with the magnitude word after it (or after its range's far end): "600 million years"
    gives ("600", "million"), "10-15 million" gives ("10", "million") and ("15", "million"), "600 years" ("600", "")."""
    s = str(s or "")
    out = []
    for m in _NUM.finditer(s):
        mm = _MAGNITUDE.match(s, m.end())
        if not mm and re.search(r"between\s+$", s[:m.start()], re.IGNORECASE):
            mm = re.compile(r"\s+and\s+\d[\d,]*(?:\.\d+)?[\s-]*(hundred|thousand|million|billion|trillion|bn|mn)\b",
                            re.IGNORECASE).match(s, m.end())
        mag = mm.group(1).lower() if mm else ""
        out.append((m.group(0).replace(",", "").rstrip("."), _MAG_ALIAS.get(mag, mag)))
    return out


# ---- figure binding (round 2 review 1): a figure keeps the unit or noun the facts give it
# Tokens with the punctuation kept, so a figure's phrase and a limiter's reach stop at a clause boundary.
_TOK = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?|\d+|%|[a-z]+(?:'[a-z]+)?|[;,.:!?()\"]|[–—-]")
_PUNCT = frozenset(";,.:!?()\"")
_RANGE_JOIN = frozenset(("-", "–", "—", "to", "and"))
# Words that end a clause: a figure's phrase and a limiter's window never run past them (round 2 review 5:
# "not built by slaves; paid labourers built it" made "paid labourers built it" refuse for a dropped "not").
CONTRAST_WORDS = frozenset(("but", "yet", "while", "whilst", "whereas", "although", "though", "however", "instead",
                            "rather", "unlike", "nor"))
_PHRASE_END = CONTRAST_WORDS | {"and", "or", "than"}
_MAG_WORDS = frozenset(("hundred", "thousand", "million", "billion", "trillion", "bn", "mn"))
FIGURE_PHRASE_TEXT = 3      # content words after a drawn figure that are checked against the facts' binding
FIGURE_PHRASE_FACT = 5      # content words after a figure in the facts that it binds
FIGURE_LEAD_TEXT = 3        # content words before a drawn figure checked the same way (round 3 review 2)
FIGURE_LEAD_FACT = 3        # content words before a figure in the facts that it binds
# Limiters and comparatives a text may never add (round 2 review 2: "Over 230 metres" from "230 metres"). The
# negations and the pure hedges refuse anywhere; the prepositions only where they govern a figure ("over the
# Nile" is a place, "over 230 metres" a claim).
ADDED_ANYWHERE = ("more than", "less than", "fewer than", "at least", "at most", "not", "no", "never", "without",
                  "none", "neither", "nearly", "almost", "roughly", "approximately", "circa", "barely", "estimated")
# a negation governs its clause's subject too (check 4); "n't" forms are folded to "not" before any check
NEGATIONS = frozenset(("not", "no", "never", "without", "none", "neither", "nor"))
# comparatives that bind a figure: "than" before a figure must stand before that figure in the facts
FIGURE_COMPARATIVES = ("than",)
ADDED_BEFORE_FIGURE = ("up to", "over", "under", "about", "around", "above", "below", "beyond", "some", "just",
                       "only", "exceeding", "almost", "nearly")


_UNBOUND = frozenset(w for p in (ADDED_ANYWHERE + ADDED_BEFORE_FIGURE + LIMITERS) for w in p.lower().split()
                     if w not in _STOP) | {"between", "than", "not", "no"}


def _toks(s: str) -> list[str]:
    s = _fold(s).replace("“", '"').replace("”", '"')
    return [t.replace(",", "") if t[:1].isdigit() else t for t in _TOK.findall(s)]


def _norm_word(w: str) -> str:
    info = _unit(w)
    return info[3] if info else _stem(w)


def _bind_word(w: str) -> str | None:
    """`w` as a figure's binding word, or None for a stop word, a magnitude word, a dash or a limiter (limiters are
    the limiter rules' business, and would otherwise bind "over" to one figure and refuse it before another)."""
    if w in _STOP or w in _MAG_WORDS or w in ("-", "–", "—") or w in _UNBOUND:
        return None
    return "%" if w == "%" else _norm_word(w)


def _figure_spans(s: str, after: int, before: int) -> list[tuple[str, list[str], list[str]]]:
    """Every figure in `s` with the content words that follow it inside its clause (at most `after`; a range's
    first figure takes its far end's phrase) and the content words that lead up to it (at most `before`, nearest
    first; through a label's colon, never past a clause end, a dash, another figure or a word the figure before
    took). Units fold to their abbreviation, words are stemmed, stop, magnitude and limiter words are skipped.
    "Height: 146 metres tall" gives [("146", ["m", "tall"], ["height"])] (round 3 review 2)."""
    toks = _toks(s)
    figs = [i for i, tok in enumerate(toks) if tok[:1].isdigit()]
    claimed: set[int] = set()
    out = []
    for i in figs:
        j = i + 1
        if j + 1 < len(toks) and toks[j] in _RANGE_JOIN and toks[j + 1][:1].isdigit():
            j += 2
        words: list[str] = []
        while j < len(toks) and len(words) < after:
            w = toks[j]
            if w in _PUNCT or w in _PHRASE_END or w[:1].isdigit():
                break
            b = _bind_word(w)
            if b:
                words.append(b)
            claimed.add(j)
            j += 1
        lead: list[str] = []
        j = i - 1
        while j >= 0 and len(lead) < before:
            w = toks[j]
            if j in claimed or w[:1].isdigit() or w in _PHRASE_END or w in _RANGE_JOIN or (w in _PUNCT and w != ":"):
                break
            b = _bind_word(w) if w != ":" else None
            if b:
                lead.append(b)
            j -= 1
        out.append((toks[i], words, lead))
    return out


def _figure_phrases(s: str, limit: int) -> list[tuple[str, list[str]]]:
    """Every figure in `s` with the content words that follow it inside its clause (_figure_spans):
    "2000 steps and 40 chambers" gives [("2000", ["step"]), ("40", ["chamber"])]."""
    return [(n, a) for n, a, _ in _figure_spans(s, limit, 0)]


def _bindings(facts: Sequence[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """(figure -> the words the cited facts put after it, figure -> every word they bind to it, after or before,
    word -> the figures it is bound to). A disputed fact's statement and hedge describe one quantity, so their
    figures share one binding ("12 metres high" and "between 10 and 15 metres")."""
    after_of: dict[str, set[str]] = {}
    by_num: dict[str, set[str]] = {}
    for f in facts:
        passages = [str(f["statement"])]
        if f["disputed"] and f["hedge"]:
            passages.append(str(f["hedge"]))
        found = [sp for p in passages for sp in _figure_spans(p, FIGURE_PHRASE_FACT, FIGURE_LEAD_FACT)]
        if f["disputed"] and found:
            shared_a = set().union(*(set(a) for _, a, _ in found))
            shared = shared_a.union(*(set(b) for _, _, b in found))
            for n, _, _ in found:
                after_of.setdefault(n, set()).update(shared_a)
                by_num.setdefault(n, set()).update(shared)
        else:
            for n, a, b in found:
                after_of.setdefault(n, set()).update(a)
                by_num.setdefault(n, set()).update(a, b)
    by_word: dict[str, set[str]] = {}
    for n, ws in by_num.items():
        for w in ws:
            by_word.setdefault(w, set()).add(n)
    return after_of, by_num, by_word


def _clauses(tokens: Sequence[str]) -> list[list[str]]:
    out, cur = [], []
    for w in tokens:
        if w in _PUNCT or w in CONTRAST_WORDS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(w)
    if cur:
        out.append(cur)
    return out


def _governed_figure(tokens: Sequence[str], i: int, n: int) -> str | None:
    """The figure or count the phrase at tokens[i:i+n] sits right before (two words at most between), else None."""
    for w in tokens[i + n:i + n + 3]:
        if w in _PUNCT:
            return None
        if w[:1].isdigit() or w in NUMBER_WORDS:
            return w
    return None


def _has_phrase(tokens: Sequence[str], phrase: Sequence[str]) -> bool:
    n = len(phrase)
    return any(tuple(tokens[i:i + n]) == tuple(phrase) for i in range(len(tokens) - n + 1))


def _phrase_tokens(p: str) -> tuple[str, ...]:
    return tuple(_words(p))


def disputed_span(fact: dict) -> str | None:
    """"A–B" when the fact's hedge (or a ranged statement) gives two endpoints, drawn as figure_text draws a
    range; None when it gives one value or none (the figure is then withheld or the card refuses)."""
    for text in (fact.get("hedge"), fact.get("statement")):
        m = _RANGE.search(str(text or ""))
        if m:
            return figure_text(f"{m.group(1)}-{m.group(2)}")
    return None


def _ranged(statement: str, n: str) -> bool:
    return any(n in (m.group(1).replace(",", ""), m.group(2).replace(",", "")) for m in _RANGE.finditer(statement))


_HOST_LABEL = re.compile(r"^(?:www|m|[a-z]{2,3})\.")
_SECOND_LEVEL = frozenset(("co", "com", "org", "net", "gov", "ac", "edu", "or", "ne", "go", "mil", "nic", "gob"))


def display_host(host: str) -> str:
    """Freeze panel M12 (1): the host as a card shows it: a leading "www.", "m." or 2-3-letter language label
    ("en.") before the registered domain stripped ("en.wikipedia.org" -> "wikipedia.org"), never down to a bare
    public suffix ("bbc.co.uk" and "pbs.org" stay whole). Display only: the signed field and the record keep the
    raw host."""
    h = (host or "").lower().strip(".")
    while True:
        m = _HOST_LABEL.match(h)
        if not m:
            return h
        rest = h[m.end():]
        labels = rest.split(".")
        if len(labels) < 2 or (len(labels) == 2 and labels[0] in _SECOND_LEVEL):
            return h
        h = rest


def source_host(fact: dict) -> str | None:
    """The host of the fact's best evidence of tier 1 or 2 as a card shows it (display_host), upper case; None when
    none qualifies."""
    ev = sorted((e for e in fact.get("evidence") or [] if isinstance(e, dict)), key=lambda e: e.get("tier", 9))
    for e in ev:
        if int(e.get("tier", 9)) > 2:
            continue
        host = display_host((urlparse(str(e.get("url") or "")).hostname or "").lower())
        if host and re.fullmatch(r"[a-z0-9.-]+", host) and "." in host:
            return host.upper()
    return None


def _glyphs_ok(t: str, face: str, weight: int | None) -> None:
    font = get_font(face, 40, weight=weight)
    for s in (t, t.upper()) if face == "Anton" else (t,):
        if drawable(s, font) != s:
            raise CardRefused(f"{face} cannot draw every glyph of {t!r}")


def _src_parts(sheet: dict, fact_ids: Sequence[str]) -> tuple[list[dict], list[str]]:
    facts = [fact_by_id(sheet, fid) for fid in fact_ids]
    passages = []
    for f in facts:
        passages.append(str(f["statement"]))
        if f["disputed"] and f["hedge"]:
            passages.append(str(f["hedge"]))
    return facts, passages


def ground_text(text: str, sheet: dict, fact_ids: Sequence[str], *, role: str,
                extra: Sequence[str] = ()) -> str:
    """`text` as a card draws it (multi_format.typeset), checked against the cited facts: every glyph drawable in
    its face; every number a number the facts give, with its "+" and its unit; a disputed or ranged figure only as
    its span; each limiter the facts put before the words taken from them kept; no superlative they do not use;
    (claim, fact, eyebrow) every content word from the facts, their hedges or the topic. CardRefused otherwise."""
    if role not in _ROLE_FACE:
        raise ValueError(f"unknown role {role!r}")
    raw = " ".join(str(text or "").split())
    if not raw:
        raise CardRefused(f"empty {role}")
    if "…" in raw or "..." in raw:
        raise CardRefused(f"{role} carries an ellipsis")
    t = typeset(raw)
    face, weight = _ROLE_FACE[role]
    _glyphs_ok(t, face, weight)
    low_tokens = _words(t)
    if role == "title" and not fact_ids and (any(ch.isdigit() for ch in t)
                                             or any(w in NUMBER_WORDS for w in low_tokens)
                                             or any(_has_phrase(low_tokens, _phrase_tokens(s)) for s in SUPERLATIVES)):
        raise CardRefused("a title with a figure, a count or a superlative must cite a fact")
    facts, passages = _src_parts(sheet, fact_ids)
    passages += [str(x) for x in extra if str(x or "").strip()]
    src = " ".join(passages)
    src_nums = set(_nums(src))
    src_tokens = _words(src)
    # 2. numbers, exactly as the facts give them, with the same magnitude word ("600 million", never "600")
    src_mags = {pair for p in passages for pair in _num_mags(p)}
    for m in _NUM.finditer(t):
        n = m.group(0).replace(",", "").rstrip(".")
        if n not in src_nums:
            raise CardRefused(f"{role}: the figure {m.group(0)} is not in the cited facts")
        if t[m.end():m.end() + 1] == "+" and not re.search(r"(?<![\d.,])" + re.escape(m.group(0).rstrip(".")) + r"\+",
                                                          src.replace(",", "") if "," not in m.group(0) else src):
            raise CardRefused(f"{role}: {m.group(0)}+ is not how the facts give it")
    for n, mag in _num_mags(t):
        if (n, mag) not in src_mags:
            given = sorted({f"{n} {g}".strip() for k, g in src_mags if k == n})
            raise CardRefused(f"{role}: {f'{n} {mag}'.strip()!r} changes the figure's magnitude (the facts give "
                              f"{', '.join(repr(x) for x in given)})")
    for w in low_tokens:
        if w in NUMBER_WORDS and w not in src_tokens:
            raise CardRefused(f"{role}: the count {w!r} is not in the cited facts")
    for m in _FIGURE.finditer(t):
        info = _unit(m.group("unit"))
        if not info or not (m.group("sep") or m.group("unit").lower() not in ("m", "in")):
            continue
        n = m.group("num").replace(",", "")
        ok = False
        for p in passages:
            for m2 in _FIGURE.finditer(p):
                i2 = _unit(m2.group("unit"))
                if i2 and m2.group("num").replace(",", "") == n and i2[3] == info[3]:
                    ok = True
        if not ok:
            raise CardRefused(f"{role}: {m.group(0)} is not given in that unit (no conversion is drawn)")
    # 2b. each figure keeps the unit or noun the facts bind it to (round 2 review 1: "There are 2000 chambers"
    # from "2000 steps and 40 chambers"; round 3 review 2: "Each side is 146 metres" when 146 m is the height,
    # "Chambers: 2000" when 2000 counts steps). The first word after the figure must be one the facts put after
    # it; no word after it or leading up to it may be one the facts bind only to another figure.
    after_of, by_num, by_word = _bindings(facts)
    for n, words, lead in _figure_spans(t, FIGURE_PHRASE_TEXT, FIGURE_LEAD_TEXT):
        own_after, own = after_of.get(n, set()), by_num.get(n, set())
        if words and own_after and words[0] not in own_after:
            raise CardRefused(f"{role}: the facts give {n} as {', '.join(sorted(own_after))}, not {words[0]!r}",
                              code="figure_noun")
        for w in words + lead:
            others = by_word.get(w, set()) - {n}
            if w not in own and others:
                raise CardRefused(f"{role}: {w!r} belongs to {', '.join(sorted(others))} in the facts, not to {n}",
                                  code="figure_noun")
    # 3. a disputed or ranged figure only as its span
    spans = {s for s in (disputed_span(f) for f in facts) if s}
    bare = t
    for s in spans:
        bare = bare.replace(s, " ")
    for n in set(_nums(t)):
        owners = [f for f in facts if n in _nums(f["statement"]) or (f["disputed"] and n in _nums(f["hedge"]))]
        if any(f["disputed"] or _ranged(str(f["statement"]), n) for f in owners) and n in _nums(bare):
            if not any(disputed_span(f) for f in owners):
                raise CardRefused(f"{role}: {n} is disputed and the facts give no span", code="disputed_no_span")
            raise CardRefused(f"{role}: {n} is disputed; only its span may be drawn")
    # 4. limiters stay with the words they govern. A limiter reaches 4 words, never past a ; , . or a contrast
    # word, and only words found in no clause free of it count (round 2 review 5: "Paid labourers built it" is
    # the second clause of "not built by slaves; paid labourers built it"; "10–20 years" comes from the hedge,
    # not from the statement's "about 20 years"). A negation also governs its clause's subject (round 3 review
    # 1: "Slaves built the pyramid" against "Slaves did not build the pyramid; the pyramid was built by workers"
    # takes "build" and "pyramid" from the free clause but "slaves" from the negated one): once the text takes a
    # word the negation reaches, every word it takes from the negated clause, before or after the negation, must
    # also stand in a clause free of it.
    taken = _content(t) | set(_nums(t))
    clauses = [c for p in passages for c in _clauses(_toks(p))]

    def _key(w: str) -> str:
        return w if _is_num(w) else _norm_word(w)
    for lim in LIMITERS:
        lt = _phrase_tokens(lim)
        if not lt or _has_phrase(low_tokens, lt):
            continue
        free = {_key(w) for c in clauses if not _has_phrase(c, lt) for w in c if w not in _STOP}
        negation = lim in NEGATIONS
        for c in clauses:
            for i in range(len(c) - len(lt) + 1):
                if tuple(c[i:i + len(lt)]) != lt:
                    continue
                reach = {_key(w) for w in c[i + len(lt):i + len(lt) + 4] if w not in _STOP} & taken
                if reach - free:
                    raise CardRefused(f"{role}: drops the facts' {lim!r}")
                if negation and reach:
                    subject = {_key(w) for w in c[:i] if w not in _STOP} & taken
                    if subject - free:
                        raise CardRefused(f"{role}: drops the facts' {lim!r} (it negates "
                                          f"{', '.join(sorted(subject - free))} {', '.join(sorted(reach))})")
    # 4b. no limiter, comparative or negation the facts do not use (round 2 review 2: a title "Over 230 metres"
    # or "Not 146 metres tall" from "230 metres" and "146 metres tall"), and one that governs a figure only
    # before that same figure in the facts (round 3: "Taller than 146 metres" from "146 metres tall, taller than
    # the 60 metre Djoser pyramid"; "Over 20000 workers" from "about 20000 workers ... over 20 years")
    t_toks = _toks(t)
    src_toks = _toks(src)
    p_toks = [_toks(p) for p in passages]
    for lim in dict.fromkeys(ADDED_ANYWHERE + ADDED_BEFORE_FIGURE + FIGURE_COMPARATIVES):
        lt = _phrase_tokens(lim)
        used = _has_phrase(src_toks, lt)
        for i in range(len(t_toks) - len(lt) + 1):
            if tuple(t_toks[i:i + len(lt)]) != lt:
                continue
            fig = _governed_figure(t_toks, i, len(lt))
            if fig is None:
                if lim in ADDED_ANYWHERE and not used:
                    raise CardRefused(f"{role}: adds {lim!r}, which the cited facts do not use",
                                      code="added_limiter")
                continue
            if not any(tuple(pt[j:j + len(lt)]) == lt and _governed_figure(pt, j, len(lt)) == fig
                       for pt in p_toks for j in range(len(pt) - len(lt) + 1)):
                raise CardRefused(f"{role}: adds {lim!r} before {fig}, which the cited facts do not put there"
                                  if used else f"{role}: adds {lim!r}, which the cited facts do not use",
                                  code="added_limiter")
    # 5. superlatives and absolutes only where the facts use them
    for s in SUPERLATIVES:
        st = _phrase_tokens(s)
        if _has_phrase(low_tokens, st) and not _has_phrase(src_tokens, st):
            raise CardRefused(f"{role}: {s!r} is not in the cited facts")
    # 6. content words: every role but a title, and a title too once it carries a figure or a count (round 2
    # review 2)
    title_figure = any(ch.isdigit() for ch in t) or any(w in NUMBER_WORDS for w in low_tokens)
    if role != "title" or title_figure:
        pool = _content(src) | _content(" ".join(str(f["hedge"]) for f in facts)) | _content((sheet or {}).get("topic", ""))
        extra = sorted(_content(t) - pool)
        if extra:
            raise CardRefused(f"{role}: {', '.join(extra)} not in the cited facts")
    return t


# ------------------------------------------------------------------------------------------------ line provenance
# Operator decision (2026-11-03, "cards render ONLY from these fields"): after four review rounds in which spans of
# fact passages, cover and slide headlines and their sibling context kept leaking false or out-of-context lines
# (round 4 review 2, blocking 1 and 2), a card line comes from exactly one of two places, named by the spec:
#   CardRef   a card field of the fact sheet (src/card_fields.py), by kind and id: a statement (at most 84
#             characters, tied to its fact), a myth (the whole myth text and its correction) or a quote (its exact
#             quotation, structured speaker, work and date). Each field carries the verdict of the project's own fact
#             checker (trace_check.check and evidence_check, the gate the narration and cover copy go through), with
#             the checkers' runs, models and hashes; card_fields.verify_field re-derives that verdict from the runs
#             and refuses anything but "supported", or a record not signed under FACT_RECORD_KEY when its checker ran
#             (operator decision A; src/record_signing.py). The renderer never calls a model.
#   Gated     the run's title or youtube_title (only those: they stand alone, with no sibling to frame them), for the
#             hook and chapter cards, while the payload's fact review is current AND carries the checkers' own
#             record (fact_evidence.checked_fact_review): a payload stamped by record_fact_review with no check
#             run is refused.
# Free text, fact-passage spans (Verbatim), cover and slide headlines are no longer sources. ground_text (the
# earlier NLP check) still runs on every line, as a second guard that can only refuse.
@dataclass(frozen=True)
class CardRef:
    """A checked card field of the fact sheet: `kind` is "statements", "myths" or "quotes", `id` its id."""
    kind: str
    id: str


@dataclass(frozen=True)
class Verbatim:
    """Retired (operator decision 2026-11-03): a span of a fact passage is no card line. Kept so a spec that still
    names one is refused with a reason, not an error."""
    fact_id: str
    start: int
    end: int
    field: str = "statement"
    evidence: int | None = None


@dataclass(frozen=True)
class Gated:
    """The run's title or youtube_title, passed by the fact gate: `path` is "title" or "youtube_title", `review` the
    public fingerprint the gate recorded (payload["fact_review"]["public_fingerprint"]). `payload` holds the record;
    when None, the plan_* call's `payload` is used."""
    path: str
    review: str
    payload: Mapping[str, Any] | None = field(default=None, compare=False, hash=False, repr=False)


LineSource = CardRef | Gated
GATED_PATHS = re.compile(r"^(?:title|youtube_title)$")
# The verdicts under which a fact may carry a card line (models.Verdict; a sheet without the field is "supported").
FACT_VERDICTS = ("", "supported")
_DQUOTES = ('"', "“", "”", "„")


def _canon(s: str) -> str:
    """A line as provenance compares it: straight quotes and hyphens for the typeset glyphs, one space, case
    folded, trailing , ; : . ! ? dropped."""
    return _qnorm(str(s or "")).casefold().rstrip(" ,;:.!?")


def card_field(sheet: dict, ref: Any, kind: str) -> dict:
    """The checked card field `ref` names (card_fields.verify_field), of `kind`; CardRefused otherwise."""
    from .card_fields import FieldRefused, verify_field
    if isinstance(ref, Verbatim):
        raise CardRefused("a span of a fact passage is no longer a card line: cards draw only the fact sheet's "
                          "checked card fields", code="no_provenance")
    if not isinstance(ref, CardRef):
        raise CardRefused(f"the {kind[:-1]} has no provenance: a card draws only a checked card field of the fact "
                          "sheet", code="no_provenance")
    if ref.kind != kind:
        raise CardRefused(f"a {ref.kind[:-1]} field is not a {kind[:-1]}", code="bad_field")
    try:
        f = verify_field(sheet, kind, ref.id)
    except FieldRefused as e:
        raise CardRefused(e.reason, code=e.code) from e
    for fid in {f.get("fact_id"), f.get("refuting_fact_id")} - {None}:
        v = str(fact_by_id(sheet, fid).get("verdict") or "").strip().lower()
        if v not in FACT_VERDICTS:
            raise CardRefused(f"fact {fid!r}'s verdict is {v!r}: only a supported fact carries a card line",
                              code="labelled_false" if v == "false" else "unverified_fact")
    return f


def gated_text(src: Gated, payload: Mapping[str, Any] | None, sheet: dict) -> str:
    """The title or youtube_title a Gated source names, after checking the gate's recorded check run;
    CardRefused otherwise."""
    if not GATED_PATHS.match(str(src.path or "")):
        raise CardRefused(f"{src.path!r}: only the title and youtube_title are taken from the payload (a headline "
                          "or slide line was passed only with the rest of its surface)", code="ungated")
    if not isinstance(payload, Mapping):
        raise CardRefused(f"{src.path}: no payload, so no record of the fact gate's pass", code="ungated")
    from .fact_evidence import checked_fact_review
    try:
        ok, why = checked_fact_review(dict(payload))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        ok, why = False, f"the record cannot be read ({e.__class__.__name__})"
    if not ok:
        raise CardRefused(f"{src.path}: {why} (fact_evidence.checked_fact_review)",
                          code="ungated" if why == "no current fact review" else "unchecked_gate")
    review = payload.get("fact_review") or {}
    if not src.review or review.get("public_fingerprint") != src.review:
        raise CardRefused(f"{src.path}: the review the card names is not the payload's recorded review",
                          code="ungated")
    try:
        reviewed = json.loads(Path(review["sheet_path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CardRefused(f"{src.path}: the reviewed fact sheet cannot be read", code="ungated") from e
    if not isinstance(reviewed, dict) or reviewed.get("facts") != (sheet or {}).get("facts"):
        raise CardRefused(f"{src.path}: the gate reviewed the copy against a different fact sheet", code="ungated")
    text = " ".join(str(payload.get(src.path) or "").split())
    if not text:
        raise CardRefused(f"{src.path}: empty in the payload", code="ungated")
    return text


def ground_line(text: str, src: Any, sheet: dict, fact_ids: Sequence[str], *, role: str,
                payload: Mapping[str, Any] | None = None) -> tuple[str, tuple[str, ...], dict[str, Any]]:
    """(the line as drawn, the fact ids it cites, its provenance record) for one hook or chapter line (claim,
    eyebrow, title): a checked statement field (CardRef) or the gated title / youtube_title, then checked again by
    ground_text as a second guard."""
    raw = " ".join(str(text or "").split())
    if not raw:
        raise CardRefused(f"empty {role}")
    if src is None:
        raise CardRefused(f"the {role} {raw!r} has no provenance: a card line is a checked card field of the fact "
                          "sheet or the gate-passed title", code="no_provenance")
    ids = tuple(dict.fromkeys(fact_ids))
    for fid in ids:
        v = str(fact_by_id(sheet, fid).get("verdict") or "").strip().lower()
        if v not in FACT_VERDICTS:
            raise CardRefused(f"fact {fid!r}'s verdict is {v!r}: only a supported fact carries a {role}",
                              code="labelled_false" if v == "false" else "unverified_fact")
    if isinstance(src, Gated):
        gated = gated_text(src, src.payload if src.payload is not None else payload, sheet)
        if _canon(raw) != _canon(gated) or gated.rstrip().endswith("?") != raw.rstrip().endswith("?"):
            raise CardRefused(f"the {role} {raw!r} is not the gated {src.path} {gated!r}", code="not_verbatim")
        record = {"source": "gated", "path": src.path, "review": src.review}
    else:
        f = card_field(sheet, src, "statements")
        if _canon(raw) != _canon(f["text"]):
            raise CardRefused(f"the {role} {raw!r} is not statement {src.id!r} ({f['text']!r})", code="not_verbatim")
        ids = tuple(dict.fromkeys(ids + (f["fact_id"],)))
        record = {"source": "card_field", "kind": "statements", "id": src.id, "fact_id": f["fact_id"]}
    t = ground_text(raw, sheet, ids, role=role)    # the second guard: it can refuse, never permit
    hit = denied_by_fact(raw, sheet)
    if hit:
        raise CardRefused(f"the {role} {raw!r} states what fact {hit[0]!r} denies ({hit[1]!r})",
                          code="prose_contradicts")
    return t, ids, record


_ACTIVE_AUX = frozenset(("did", "does", "do", "had", "has", "have", "could", "can", "will"))


def denied_agents(statement: str) -> set[str]:
    """The agents a fact's statement denies: "formed through erosion, not by aliens" and "aliens did not build it"
    give {"alien"}."""
    toks = _toks(statement)
    out: set[str] = set()
    for i, w in enumerate(toks):
        if w not in ("not", "never"):
            continue
        by = next((j for j in range(i + 1, min(len(toks), i + 4)) if toks[j] in ("by", "from")
                   and not any(t in _PUNCT for t in toks[i + 1:j])), None)
        if by is not None:          # "not by aliens", "was not built by slaves": the agent after "by"
            for v in toks[by + 1:by + 4]:
                if v in _PUNCT:
                    break
                if v not in _STOP and not _is_num(v):
                    out.add(_norm_word(v))
                    break
        elif (i >= 2 and toks[i - 1] in _ACTIVE_AUX and toks[i - 2] not in _STOP
              and toks[i - 2] not in _PUNCT):      # "aliens did not build it": the subject
            out.add(_norm_word(toks[i - 2]))
    return out


def denied_by_fact(line: str, sheet: dict) -> tuple[str, str] | None:
    """(fact id, agent) when a line states, unnegated, an agent a supported fact denies (review 2026-11-03, second
    round: a forged record put "ALIENS BUILT THE RING" on a chapter card over "formed through erosion, not by
    aliens"). A guard that can only refuse."""
    if set(_words(line)) & NEGATIONS:
        return None
    words = _content(line)
    for f in (sheet or {}).get("facts") or []:
        if not isinstance(f, dict) or str(f.get("verdict") or "").strip().lower() not in FACT_VERDICTS:
            continue
        for agent in denied_agents(str(f.get("statement") or "")):
            if agent in words:
                return str(f.get("id")), agent
    return None


def validate_chapters(cards: Sequence[ChapterCard]) -> None:
    """Chapter numbers run 1..N with no gap or duplicate, and every card's total is N."""
    nums = [c.number for c in cards]
    n = len(cards)
    if sorted(nums) != list(range(1, n + 1)):
        raise CardRefused(f"chapters numbered {sorted(nums)}, not 1..{n}")
    if any(c.total != n for c in cards):
        raise CardRefused(f"a chapter's total is not {n}")


# ------------------------------------------------------------------------------------------------ the card builder
class _Card:
    """Collects one card's elements, motion, draw items and measured plates in draw order."""

    def __init__(self, kind: str, geo: _Geo, ground: Image.Image, target: float = PLACE_TARGET):
        self.kind, self.geo, self.ground, self.target = kind, geo, ground, target
        self.items: list[dict[str, Any]] = []
        self.elements: list[Element] = []
        self.motion: dict[str, tuple] = {}
        self.timeline: dict[str, tuple[float, float]] = {}
        self.roles: dict[str, str] = {}
        self.drawn: dict[str, str] = {}
        self.contrast: list[tuple[str, Any]] = []
        self.marks: list[tuple[str, Image.Image]] = []

    def font(self, face: str, weight: int | None, px: float):
        return get_font(face, self.geo.px(px), weight=weight)

    def _el(self, name, kind, text, face, weight, px, fill, xy, anchor, track, box, motion, t_full=None):
        timed = motion[0] not in ("static", "hook")
        t_in = float(motion[1]) if timed else 0.0
        t_end = float(t_full if t_full is not None else (motion[1] + motion[2] if timed else 0.0))
        self.elements.append(Element(name, kind, text, face, weight, int(px), tuple(fill), (float(xy[0]), float(xy[1])),
                                     anchor, float(track), tuple(int(v) for v in box), round(t_in, 4), round(t_end, 4)))
        self.motion[name] = motion
        self.timeline[name] = (round(t_in, 4), round(t_end, 4))

    def text(self, name: str, text: str, face: str, weight: int | None, px: float, fill: RGB, x: float, y: float,
             motion: tuple, role: str, track: float = 0.0, rise: bool = True, settle: tuple | None = None,
             runs: bool = False, record: bool = True, target: float | None = None, pivot_top: bool = False,
             kind: str = "text", halo_px: int | None = None, gold_tail: bool = False,
             gold_from: int | None = None) -> dict[str, Any]:
        f = self.font(face, weight, px)
        X, Y = self.geo.x(x), self.geo.y(y)
        tr = track * self.geo.k
        X0 = X - _ink(text[:1], f)[0]
        tgt = self.target if target is None else target
        plan = self._plan
        if gold_from is not None and 0 < gold_from < len(text):
            # a second run on the same baseline (the hook's one-line claim: its last word in GOLD, freeze panel H2)
            parts = [(text[:gold_from], fill), (text[gold_from:], GOLD)]
            plates = [plan(X0 + f.getlength(text[:sum(len(s) for s, _ in parts[:j])]), Y, s, f, c, 0.0, tgt,
                           halo_px) for j, (s, c) in enumerate(parts) if s.strip()]
        elif gold_tail and text.endswith("”") and len(text) > 1:
            # the quote's closing mark in the opening mark's GOLD (review 2026-11-03, second round: a GOLD opening
            # mark over a small BONE closing one read as two different marks)
            parts = [(text[:-1], fill), ("”", GOLD)]
            plates = [plan(X0 + f.getlength(text[:sum(len(s) for s, _ in parts[:j])]), Y, s, f, c, 0.0, tgt,
                           halo_px) for j, (s, c) in enumerate(parts) if s.strip()]
        elif runs:
            parts = []
            pos = 0
            for m in _FIG_RUN.finditer(text):
                end = m.end()
                fm = _FIGURE.match(text, m.start() + (m.group(0).rfind("–") + 1 if "–" in m.group(0) else 0))
                if fm and _unit(fm.group("unit")):
                    end = max(end, fm.end())
                if m.start() > pos:
                    parts.append((text[pos:m.start()], fill))
                parts.append((text[m.start():end], GOLD))
                pos = end
            if pos < len(text):
                parts.append((text[pos:], fill))
            plates = [plan(X0 + f.getlength(text[:sum(len(s) for s, _ in parts[:j])]), Y, s, f, c, 0.0, tgt,
                           halo_px) for j, (s, c) in enumerate(parts) if s.strip()]
        else:
            plates = [plan(X0, Y, text, f, fill, tr, tgt, halo_px)]
        plates = [p for p in plates if not p.empty]
        if not plates:
            raise CardRefused(f"{name} draws nothing")
        thin = any(getattr(p, "_thin", False) for p in plates)
        measured = plates[0] if len(plates) == 1 and not thin else _LinePlate(plates, self.ground)
        if record and (measured.contrast is None or measured.contrast < tgt):
            raise CardRefused(f"{name} reaches only {measured.contrast} against {tgt} at the strongest halo")
        box = _union([p.box for p in plates])
        it = {"type": "plate", "name": name, "parts": plates, "twins": [], "rise": rise, "settle": settle,
              "face": face, "weight": weight, "fill": fill, "measured": measured,
              "pivot": (box[0], box[1]) if pivot_top else (box[0], Y)}
        self.items.append(it)
        t_full = None
        if settle:
            t_full = max(motion[1] + motion[2], settle[0] + settle[1])
        self._el(name, kind, text, face, weight, f.size, fill, (X0, Y), "ls", tr, box, motion, t_full)
        self.roles[name] = role
        if kind == "text":
            self.drawn[name] = text
        if record and kind == "text":
            self.contrast.append((name, measured))
        return it

    def _plan(self, x: float, y: float, text: str, font, fill: RGB, track: float, target: float,
              halo_px: int | None = None):
        """typography.plan_text. Only for glyphs so thin their eroded interior is 30 px or less (a 16:9 source line at
        720p), which typography.ring_contrast cannot read a colour from (it would take fill=None's luminance), the
        same halo search measured by _ratio against the fill colour. Decided before the call, from the glyph mask
        itself, so no error from plan_text is ever swallowed (round 1 review 13)."""
        from .typography import TextPlate
        p = TextPlate(drawable(text, font), font, fill, (x, y), "ls", track, 0, INK,
                      int(min(31, max(21, font.size // 3))), False, self.ground.size)
        if p.empty or int(_erode3(p.glyphs > 127).sum()) > 30:
            return plan_text(self.ground, (x, y), text, font, fill, "ls", track=track, target=target, halo_px=halo_px)
        p._thin = True
        halo, r = 0.0, None
        while True:
            p.halo = round(halo, 2)
            r = _ratio(draw_plate(self.ground.convert("RGB").copy(), p), [p])
            if r is None or r >= target or halo >= 0.95:
                break
            halo = min(0.95, halo + 0.08)
        p.contrast = r
        p.measure = lambda im, p=p: _ratio(im, [p])
        return p

    def rect(self, name: str, box_d: Sequence[float], fill: RGB, motion: tuple, wipe: str | None, role: str = "rule",
             box_px: Sequence[int] | None = None) -> dict[str, Any]:
        b = tuple(box_px) if box_px is not None else self.geo.box(box_d)
        it = {"type": "rect", "name": name, "box": b, "fill": fill, "wipe": wipe}
        self.items.append(it)
        self._el(name, "mark", "", "", None, 0, fill, (b[0], b[1]), "", 0.0, b, motion)
        self.roles[name] = role
        self.marks.append((name, mark_mask(self.ground.size, lambda d, b=b: d.rectangle([b[0], b[1], b[2] - 1, b[3] - 1],
                                                                                       fill=255))))
        return it

    def plan(self, kind: str, seconds: float, frames: int, fact_ids, meta: dict) -> CardPlan:
        meta = dict(meta)
        meta["roles"] = dict(self.roles)
        meta["_motion"] = dict(self.motion)
        return CardPlan(kind, self.geo.width, self.geo.height, round(seconds, 6), frames, tuple(self.elements),
                        dict(self.timeline), tuple(fact_ids), dict(self.drawn), meta)


def _kicker(card: _Card, name: str, label: str, x: float, rule_top: float, rule_fill: RGB, text_fill: RGB,
            wipe: tuple, reveal: tuple) -> None:
    """A 44 x 4 rule, then the tracked Montserrat 800 label on its baseline MYTH_KICK below the rule's top (9:16 40/6,
    16:9 KICKER_WIDE_PX/5)."""
    px, track, drop = [KICKER_WIDE_PX, 5, MYTH_KICK[True]] if card.geo.wide else [40, 6, MYTH_KICK[False]]
    card.rect(f"{name}_rule", (x, rule_top, x + 44, rule_top + 4), rule_fill, ("ease", *wipe), "x")
    card.text(f"{name}_label", label, "Montserrat", 800, px, text_fill, x, rule_top + drop, ("ease", *reveal),
              "kicker", track=track)


def _marks_ok(plan: CardPlan) -> CardPlan:
    """Every rule, strike, divider and quote mark measured on the planned last frame against its ground
    (graphics.nontext_contrast): under NONTEXT_MIN the card refuses before anything is encoded."""
    last = frame_at(plan, plan.frames - 1)
    for name, mask in plan.meta["_marks"]:
        v = nontext_contrast(last, mask, ground=plan.meta["_r"]["rest"])
        if v is None or v < NONTEXT_MIN:
            raise CardRefused(f"{name} reaches only {v} against its ground (non-text floor {NONTEXT_MIN})")
    return plan


def _divider_fill(card: _Card, box_d: Sequence[float]) -> RGB:
    """A divider's token: GOLD_DIM where it measures NONTEXT_MIN on the card's rest ground, else stepped up to GOLD,
    the timeline spine's own rule (graphics.spine_colour; round 1 review 5: GOLD_DIM measured 2.5-2.8 on bright
    photos and refused every 16:9 chapter and myth card). Under 3:1 even in GOLD, the card refuses (_marks_ok)."""
    b = card.geo.box(box_d)
    mask = mark_mask(card.ground.size, lambda d: d.rectangle([b[0], b[1], b[2] - 1, b[3] - 1], fill=255))
    return spine_colour(card.ground, mask)


def _ground(photo: Path | None, geo: _Geo, last_t: float, dim: float = 0.0
            ) -> tuple[Image.Image, GroundDrift, Image.Image]:
    ground = timeline_backdrop(photo, geo.width, geo.height)
    if dim > 0:
        ground = Image.blend(ground, Image.new("RGB", ground.size, INK), dim)
    drift = GroundDrift(ground)
    return ground, drift, drift(last_t)


# ------------------------------------------------------------------------------------------------ chapter card
CHAPTER_TOTAL_PX = {False: 72}          # freeze panel C6: the 9:16 "/ N" after the numeral, in GOLD (was 90 GOLD_DIM)
CHAPTER_TOTAL_GAP = 16                    # design px from the numeral's ink to the total's (9:16)
CHAPTER_TOTAL_TARGET = 7.0              # C5 / C6: the GOLD total is planned to draw >= 7.0 (was PLACE_TARGET, muted)
CHAPTER_TOTAL_KICK_GAP = 24             # 16:9 (freeze panel C5): design px from the CHAPTER label's ink to the "/ N"
CHAPTER_TOTAL_HALO_PX = 31              # 16:9 total's halo spread (typography's widest default; the label keeps its own)
CHAPTER_TALL_SLACK = 12                 # 16:9 (C5): two title lines may stand this much taller than the numeral's ink
# 16:9 (C5, round 4 review: act 2's two lines at 152 stood 30 px under the numeral's ink top): the step between two
# title lines grows until their first cap top meets the video's numeral ink top, at most to this x P (so the sizes
# from 145 px up meet it; smaller two-line sizes keep a gap rather than open the lines further)
CHAPTER_WIDE_LEAD_MAX = 1.20
CHAPTER_TALL_MEASURE = 788              # 9:16 (C6): the title measure (HOOK_MEASURE, was 760)
CHAPTER_THREE_GAIN = 16                 # 9:16 (C6): 3 title lines when they set at least this much larger than 2
CHAPTER_TALL_CENTRE = 864               # 9:16 (C6): the block's centre (0.45 x 1920), never past 1058
CHAPTER_TALL_BOX = (288, 1058)          # 9:16: the graphics box's y
CHAPTER_INK_FOOT = 2                    # design px a round letter's overshoot may run below the last baseline
CHAPTER_TITLE_T0 = {False: 0.62, True: 0.80}     # s: the first title line's start (C6: 9:16 was 0.55)
CHAPTER_TITLE_STAGGER = {False: 0.18, True: 0.18}  # s: per title line (C6: 9:16 was 0.12)
CHAPTER_TOTAL_T0 = 0.55                 # s: the 9:16 total's reveal, after the numeral's settle (C6)
ACCENT_AIR = 16                         # design px (freeze panel A1): an accented line's ink top clears the line
                                        # above's baseline by at least this
CHAPTER_NUMERAL_PX = {False: 300, True: 360}
CHAPTER_SAT_MAX = 90                      # freeze panel C3: a ground more saturated than this (of 255) under the block
CHAPTER_SAT_TARGET = 7.0                  # is darkened (x CHAPTER_SAT_STEP luma) until the drawn minimum reaches this
CHAPTER_SAT_STEP = 0.86
CHAPTER_SAT_STEPS = 6
TITLE_ALPHA = 0.28                        # s: freeze panel C4: a title line's alpha ramp (its rise keeps its motion)
W1_STOPS = frozenset(("the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "and", "or", "from", "with"))


def _accented(text: str) -> bool:
    """A1: a line carrying a mark above the cap height (Ä Ö Ü É Á Å Ñ and similar: a precomposed letter whose
    decomposition adds a combining mark)."""
    import unicodedata
    return any(len(unicodedata.normalize("NFD", ch)) > 1 for ch in text if ch.isalpha())


def _lead(lines: Sequence[str], P: int, step: int, cap: int, face: str = "Anton", weight: int | None = None
          ) -> list[int]:
    """Freeze panel A1: each line's baseline offset from the first (0, ...): the step above a line whose ink top
    rises above the cap height (an accent) grows to max(step, cap + rise + ACCENT_AIR), so its marks never touch the
    line above; every other step stays `step`, so unaccented text plans exactly as before."""
    out, acc = [0], 0
    f = None
    for ln in lines[1:]:
        st = step
        if _accented(ln):
            f = f or get_font(face, P, weight=weight)
            rise = -_ink(ln, f)[1] - cap
            if rise > 0:
                st = max(step, cap + rise + ACCENT_AIR)
        acc += st
        out.append(acc)
    return out


def _stop_end(lines: Sequence[str]) -> bool:
    """Freeze panel W1: a line other than the last ends on a W1_STOPS word."""
    return any(ln.split() and ln.split()[-1].lower().strip(".,;:") in W1_STOPS for ln in lines[:-1])


W2_STEPS = 3       # freeze panel W2: further ladder steps tried below the first size whose break ends on a stop word
W2_DROP = 24       # design px: and never more than this below that first size


def _w1_ladder(sizes: Sequence[int], wrap: Callable[[int], list[str] | None]) -> tuple[int, list[str]] | None:
    """(px, lines) from a caller's size ladder under W2 (the hard W1): the first size that wraps, unless a line other
    than the last ends on a W1_STOPS word; then up to W2_STEPS further steps of the ladder, no more than W2_DROP px
    below the first size (never past the ladder's floor), the first whose break has no stop-word end. None when no
    size wraps; CardRefused (code "stop_word_end") when every allowed size ends a line on a stop word."""
    sizes = list(sizes)
    for i, P in enumerate(sizes):
        lines = wrap(P)
        if not lines:
            continue
        if not _stop_end(lines):
            return P, lines
        for Q in sizes[i + 1:i + 1 + W2_STEPS]:
            if Q < P - W2_DROP:
                break
            got = wrap(Q)
            if got and not _stop_end(got):
                return Q, got
        raise CardRefused(f"every break of {' '.join(lines)!r} from {P} to {P - W2_DROP} px ends a line on a stop "
                          f"word", code="stop_word_end")
    return None


def _mix(a: RGB, b: RGB, q: float) -> RGB:
    return tuple(_rhu(x + (y - x) * q) for x, y in zip(a, b))


def _sat_under(im: Image.Image, box: Sequence[int]) -> float:
    """Mean HSV saturation (0..255) of `im` inside the frame-px `box`."""
    crop = im.crop(tuple(int(v) for v in box)).convert("HSV")
    return float(np.asarray(crop, np.float32)[..., 1].mean()) if crop.width and crop.height else 0.0


def _numeral_column(total: int) -> int:
    """16:9 (freeze panel C2): design px of the widest numeral this video shows (1..total, Anton at the numeral's
    px), so the numeral's right edge, the divider and the title stand at one x on every chapter of the video."""
    f = get_font("Anton", CHAPTER_NUMERAL_PX[True])
    return max(_width(str(k), f) for k in range(1, max(1, total) + 1))


def _numeral_tall(total: int) -> int:
    """16:9 (C5): design px of the tallest numeral ink of 1..total at the numeral's px (311 for a 5-act video), one
    per video, so every chapter of the video sets its title by the same rule."""
    f = get_font("Anton", CHAPTER_NUMERAL_PX[True])
    return max(-_ink(str(k), f)[1] for k in range(1, max(1, total) + 1))


def _chapter_wide_off(lines: Sequence[str], P: int, col_tall: int) -> list[int]:
    """16:9 title baseline offsets (A1's _lead); with two lines the step grows (C5, round 4) so the first line's cap
    top (baseline - round(0.86 P)) meets the numeral column's ink top, never past CHAPTER_WIDE_LEAD_MAX x P."""
    cap, step = round(0.86 * P), round(0.98 * P)
    off = _lead(lines, P, step, cap)
    if len(off) == 2:
        off = [0, max(off[1], min(col_tall - cap, round(CHAPTER_WIDE_LEAD_MAX * P)))]
    return off


def _chapter_wide_two_ok(P: int, col_tall: int) -> bool:
    """16:9 (C5, fix round 1): two title lines at P stand no more than CHAPTER_TALL_SLACK taller than the numeral
    column's ink at the 0.98 P step. Operator decision 2026-11-05 ("chapter cards: draw all of them"): the lower
    bound (the lines reaching the numeral's ink top at CHAPTER_WIDE_LEAD_MAX x P, 145 px up for a 311 px numeral) is
    gone; a smaller two-line size is top-aligned instead (_chapter_wide_rise), so a long act title steps down the
    ladder to its floor rather than dropping its card."""
    return round(0.86 * P) + round(0.98 * P) <= col_tall + CHAPTER_TALL_SLACK


def _chapter_wide_rise(off: Sequence[int], P: int, col_tall: int) -> int:
    """16:9: design px from the numeral's baseline up to the first title line's cap top. The lines' own height
    (cap + the last offset; the last baseline on the numeral's), except two lines that cannot reach the numeral
    column's ink top at CHAPTER_WIDE_LEAD_MAX x P (sizes under 145 px for a 311 px numeral; operator decision
    2026-11-05): those keep the cap-top-to-numeral alignment, their first cap top on the column's ink top, and the
    last baseline stands above the numeral's rather than the lines opening further."""
    h = off[-1] + round(0.86 * P)
    return col_tall if len(off) == 2 and h < col_tall - CHAPTER_TALL_SLACK else h


def _chapter_tall_block(P: int, lines: Sequence[str]) -> tuple[int, list[int], int]:
    """9:16 (freeze panel C6): (block height from the kicker rule's top to the last title baseline, the title lines'
    offsets (A1), the block's top R): centred on min(CHAPTER_TALL_CENTRE, 1058 - H / 2), never above 288."""
    cap, step = round(0.86 * P), round(0.98 * P)
    off = _lead(lines, P, step, cap)
    H = 340 + 56 + cap + off[-1]
    top, bottom = CHAPTER_TALL_BOX
    centre = max(top + H / 2, min(CHAPTER_TALL_CENTRE, bottom - CHAPTER_INK_FOOT - H / 2))
    return H, off, round(centre - H / 2)


def plan_chapter_card(spec: ChapterCard, sheet: dict, width: int = 1080, height: int = 1920, *,
                      payload: Mapping[str, Any] | None = None) -> CardPlan:
    geo = _Geo(width, height)
    if not (1 <= spec.total <= 12 and 1 <= spec.number <= spec.total):
        raise CardRefused(f"chapter {spec.number} of {spec.total} (1..total, total <= 12)")
    title = " ".join(str(spec.title or "").split())
    if not title:
        raise CardRefused("a chapter title is empty")
    if _TITLE_PREFIX.match(title):
        raise CardRefused("the title repeats the chapter number")
    t, cited, prov = ground_line(title, spec.title_src, sheet, spec.fact_ids, role="title", payload=payload)
    t = t.upper()
    num = str(spec.number)
    total_t = f"/ {spec.total}" if spec.total >= 2 else ""      # C1: the chapter count is structural (ChapterCard.total)
    f_num = get_font("Anton", CHAPTER_NUMERAL_PX[geo.wide])
    num_ink = _ink(num, f_num)
    # 16:9 (C2): the numeral right-aligned on a column fixed per video; the divider a fixed CHAPTER_GAP after that
    # column, and the title the same gap after the divider. Freeze panel C5 (operator decision): the "/ N" stands on
    # the kicker line, so it no longer widens the column and the title keeps its full measure
    edge = 153 + _numeral_column(spec.total)
    div_x = edge + CHAPTER_GAP
    title_x = div_x + 2 + CHAPTER_GAP
    num_tall = -num_ink[1]                      # C2: the numeral's measured ink height (CHAPTER_WIDE_TALL)
    # C5: the height cap is one per video (the tallest numeral ink of 1..total, 311 at 360 px for a 5-act video), so
    # every chapter of the video sets its title by the same rule
    col_tall = max(num_tall, _numeral_tall(spec.total))
    # review 2026-11-03 (second round, 3): the 9:16 title runs larger (it sat small in the upper third)
    sizes, measure = (((120, 112, 104, 96), 1766 - title_x) if geo.wide
                      else ((136, 128, 120, 112, 104, 100, 96, 92, 88, 84), CHAPTER_TALL_MEASURE))
    fit = None
    if geo.wide:
        # review 2026-11-03 (round 3, C): a 120 px title left the right third empty. The title now fills its
        # measure: the largest CHAPTER_WIDE_ONE size that sets it on one line, else two even lines at the largest
        # CHAPTER_WIDE_TWO size whose two lines stand at most CHAPTER_TALL_SLACK taller than the numeral's own ink
        # (C2, C5), under W2
        for P in CHAPTER_WIDE_ONE:
            lines = _design_wrap(t, "Anton", None, P, measure, 1, geo)
            if lines:
                fit = (P, lines)
                break
        if fit is None:
            # fix round 1 (review: the cap top within CHAPTER_TALL_SLACK of the numeral's ink top at every title
            # size): from 145 px up (311 px numeral) the two lines span the numeral's ink, the step opened at most to
            # CHAPTER_WIDE_LEAD_MAX x P; operator decision 2026-11-05 (draw every chapter card): a long title steps
            # on down the ladder to its 96 px floor, its first cap top still on the numeral's ink top
            # (_chapter_wide_rise); only a title that does not fit two lines at the floor is refused
            two = [P for P in CHAPTER_WIDE_TWO if _chapter_wide_two_ok(P, col_tall)]
            fit = _w1_ladder(two, lambda P: _design_wrap(t, "Anton", None, P, measure, 2, geo, even=True))
            if fit is None:
                raise CardRefused(f"the 16:9 title does not fit two lines at {two[-1] if two else '-'} px, the "
                                  f"type floor", code="no_fit")
    if fit is None:
        refused = None
        try:
            fit = _w1_ladder(sizes, lambda P: _design_wrap(t, "Anton", None, P, measure, 2, geo, even=True))
        except CardRefused as e:
            if geo.wide:
                raise
            refused = e
        if not geo.wide:
            # C6: three lines when they set at least CHAPTER_THREE_GAIN px larger than two (or two cannot be set
            # without a stop-word end) and the block stays inside the graphics box
            room = CHAPTER_TALL_BOX[1] - CHAPTER_TALL_BOX[0] - CHAPTER_INK_FOOT

            def three(P: int) -> list[str] | None:
                ls = _design_wrap(t, "Anton", None, P, measure, 3, geo, even=True)
                if not ls or len(ls) != 3:
                    return None
                return ls if _chapter_tall_block(P, ls)[0] <= room else None
            try:
                fit3 = _w1_ladder(sizes, three)
            except CardRefused:
                fit3 = None
            if fit3 and (fit is None or fit3[0] >= fit[0] + CHAPTER_THREE_GAIN):
                fit = fit3
            if fit is None and refused is not None:
                raise refused
    if fit is None:
        raise CardRefused("the title does not fit two lines at the floor size")
    P, lines = fit
    cap, step, n = round(0.86 * P), round(0.98 * P), len(lines)
    off = _chapter_wide_off(lines, P, col_tall) if geo.wide else _lead(lines, P, step, cap)
    seconds = CHAPTER_SECONDS[geo.wide]
    frames, seconds = _frames(seconds)
    last_t = (frames - 1) / VIDEO_FPS
    if not geo.wide:
        H, _, y0 = _chapter_tall_block(P, lines)
        y1 = y0 + H
    else:
        top_rel = max(310, _chapter_wide_rise(off, P, col_tall) + MYTH_LABEL_AIR[True] + MYTH_KICK[True])
        y1 = round(475 + top_rel / 2)
        y0 = y1 - top_rel - MYTH_KICK[True]
    inner = CHAPTER_INNER
    sat = None
    for attempt in range(CHAPTER_SAT_STEPS + 1):
        # the photo toned (toned_ground), dark only behind the block: the chapter is a breath on the topic's picture
        # (review 2026-11-03, second round: "the photo is barely visible" on the 84 % INK backdrop)
        ground = toned_ground(spec.photo, geo, CHAPTER_BLUR, band_levels(geo, y0 - 40, y1 + 40, inner,
                                                                         CHAPTER_ABOVE, CHAPTER_BELOW,
                                                                         *CHAPTER_FEATHER))
        drift = GroundDrift(ground)
        rest = drift(last_t)
        if sat is None:
            # the photo's own colour under the block (the toned ground there is dark, where HSV saturation is noise)
            sat = (_sat_under(cover_crop(spec.photo, (geo.width, geo.height)).convert("RGB"),
                              geo.box((100 if not geo.wide else 153, y0, 1766 if geo.wide else 888, y1)))
                   if spec.photo else 0.0)
        plan = _chapter_compose(spec, geo, rest, drift, last_t, seconds, frames, cited, prov, P, lines, num, total_t,
                                div_x, title_x, edge)
        # C5 / C6: the total is GOLD type now (no longer muted), so C3 measures it with the rest
        dmin = min((p.contrast for nm, p in plan.meta["_contrast"] if p.contrast is not None), default=None)
        if sat <= CHAPTER_SAT_MAX or (dmin is not None and dmin >= CHAPTER_SAT_TARGET) or attempt == CHAPTER_SAT_STEPS:
            plan.meta["anchors"].update(ground_saturation=round(sat, 1), darken_steps=attempt,
                                        drawn_contrast_min=None if dmin is None else round(dmin, 2))
            return _marks_ok(plan)
        inner = (inner[0] * CHAPTER_SAT_STEP, inner[1] * CHAPTER_SAT_STEP)     # C3: one more darkening step
    raise AssertionError("unreachable")


def _chapter_compose(spec: ChapterCard, geo: _Geo, rest: Image.Image, drift: Any, last_t: float, seconds: float,
                     frames: int, cited: Any, prov: Any, P: int, lines: list[str], num: str, total_t: str,
                     div_x: int, title_x: int, edge: int) -> CardPlan:
    card = _Card("chapter", geo, rest)
    cap, step = round(0.86 * P), round(0.98 * P)
    col_tall = _numeral_tall(spec.total)
    off = (_chapter_wide_off(lines, P, col_tall) if geo.wide else _lead(lines, P, step, cap))
    anchors: dict[str, Any] = {"title_px": P, "title_lines": list(lines)}
    provenance = {"title": prov}
    npx = CHAPTER_NUMERAL_PX[geo.wide]
    f_num = get_font("Anton", npx)
    t0, dt = CHAPTER_TITLE_T0[geo.wide], CHAPTER_TITLE_STAGGER[geo.wide]
    if not geo.wide:
        H, _, R = _chapter_tall_block(P, lines)
        Bn = R + 340
        first = Bn + 56 + cap
        _kicker(card, "kicker", "CHAPTER", 100, R, GOLD, GOLD, (0.10, 0.30), (0.15, REVEAL))
        n_start, num_x = 0.25, 100
        card.text("numeral", num, "Anton", None, npx, GOLD, num_x, Bn, ("ease", n_start, REVEAL), "display",
                  rise=False, settle=(n_start, FIGURE_SETTLE))
        baselines = [first + o for o in off]
        for k, (ln, b) in enumerate(zip(lines, baselines)):
            card.text(f"title_{k}", ln, "Anton", None, P, BONE, 100, b, ("ease", t0 + dt * k, REVEAL, TITLE_ALPHA),
                      "display")
        anchors.update(R=R, Bn=Bn, title_baselines=baselines, block_centre=round(R + H / 2, 1), block_h=H)
        if total_t:
            # C6: the chapter count after the numeral in the kicker's GOLD, baseline-aligned, revealed after the
            # numeral's settle
            tot_x = num_x + _width(num, f_num) + CHAPTER_TOTAL_GAP
            card.text("total", total_t, "Anton", None, CHAPTER_TOTAL_PX[False], GOLD, tot_x, Bn,
                      ("ease", CHAPTER_TOTAL_T0, REVEAL), "display", rise=False, target=CHAPTER_TOTAL_TARGET)
            anchors["total_fill"] = brand.hex_colour(GOLD)
            anchors["total_placement"] = "after_numeral"
    else:
        rise = _chapter_wide_rise(off, P, col_tall)
        top_rel = max(310, rise + MYTH_LABEL_AIR[True] + MYTH_KICK[True])
        Bn = round(475 + top_rel / 2)
        # the last baseline on the numeral's; a top-aligned two-line title (_chapter_wide_rise) above it
        baselines = [Bn - rise + cap + o for o in off]
        K = baselines[0] - cap - MYTH_LABEL_AIR[True]     # the kicker label's baseline
        # freeze panel C4: the 16:9 reveal paced to its 3.5 s card
        n_start = 0.30
        num_x = edge - _width(num, f_num)        # C2: right-aligned on the video's numeral column
        card.text("numeral", num, "Anton", None, npx, GOLD, num_x, Bn, ("ease", n_start, REVEAL), "display",
                  rise=False, settle=(n_start, FIGURE_SETTLE))
        div = (div_x, Bn - 310, div_x + 2, Bn)
        fill = _divider_fill(card, div)
        card.rect("divider", div, fill, ("ease", 0.55, 0.45), "y", role="divider")
        _kicker(card, "kicker", "CHAPTER", title_x, K - MYTH_KICK[True], GOLD, GOLD, (0.20, 0.40), (0.20, 0.40))
        if total_t:
            # freeze panel C5 (operator decision): the chapter count on the kicker line, in the kicker's own face,
            # tracking and GOLD, CHAPTER_TOTAL_KICK_GAP after the label's ink, revealed with it; never the numeral
            # repeated ("CHAPTER 2 OF 5" is rejected)
            lab = next(e for e in card.elements if e.name == "kicker_label").box
            tx = (lab[2] - geo._ox) / geo.k + CHAPTER_TOTAL_KICK_GAP
            # planned to read as the label beside it does (within 0.1, and never under CHAPTER_TOTAL_TARGET)
            lab_c = dict(card.contrast)["kicker_label"].contrast or 0.0
            # round 2 (review: 9.68 against the label's 9.85 on act 2 busy, the halo at its strongest): the total's
            # halo spreads CHAPTER_TOTAL_HALO_PX, so it can reach the label's own contrast first
            targets = [t for t in (lab_c, lab_c - 0.05, lab_c - 0.10) if t > CHAPTER_TOTAL_TARGET] + [
                CHAPTER_TOTAL_TARGET]
            for i, tgt in enumerate(targets):
                try:
                    card.text("total", total_t, "Montserrat", 800, KICKER_WIDE_PX, GOLD, tx, K, ("ease", 0.20, 0.40),
                              "kicker", track=5, target=tgt,
                              halo_px=max(21, round(CHAPTER_TOTAL_HALO_PX * geo.k)))
                    break
                except CardRefused:
                    if i == len(targets) - 1:
                        raise
            anchors["total_fill"] = brand.hex_colour(GOLD)
            anchors["total_placement"] = "kicker"
        for k, (ln, b) in enumerate(zip(lines, baselines)):
            card.text(f"title_{k}", ln, "Anton", None, P, BONE, title_x, b, ("ease", t0 + dt * k, 0.50, TITLE_ALPHA),
                      "display")
        anchors.update(Bn=Bn, K=K, title_baselines=baselines, divider_x=div_x, title_x=title_x,
                       divider_fill=brand.hex_colour(fill), numeral_right=edge)
    meta = {"anchors": anchors, "_r": {"drift": drift, "items": card.items, "last_t": last_t, "rest": rest},
            "_contrast": card.contrast, "_marks": card.marks, "rest_s": max(v[1] for v in card.timeline.values()),
            "provenance": provenance}
    return card.plan("chapter", seconds, frames, cited, meta)


# ------------------------------------------------------------------------------------------------ myth vs fact
_MYTH_CLAUSE = re.compile(r"\s*(?:[,;:]\s*(?:and|but|which|who|where|while|so)?|\s(?:and|but|which|while|whereas|so)\s)\s*",
                          re.IGNORECASE)


def myth_clauses(myth: str) -> list[str]:
    """The myth and each of its clauses (split at commas, semicolons and joining words): a compound myth with one
    true clause ("The ring is about 40 km across and aliens built it") is tested clause by clause."""
    parts = [p.strip(" .") for p in _MYTH_CLAUSE.split(" ".join(str(myth or "").split())) if p and p.strip(" .")]
    return list(dict.fromkeys([" ".join(str(myth or "").split())] + [p for p in parts if len(_content(p)) >= 2]))


def _affirmed(myth: str, sheet: dict) -> str | None:
    """The id of a supported fact whose own statement states the myth's words, or any one of its clauses, as true (no
    limiter or negation dropped), or None: such a "myth" would strike out a fact (round 4 review 2, blocking 1: every
    fact on the sheet is asked; review 2026-11-03, blocking 1: every clause too, so a true clause joined to a false
    one is caught even when the clause is the fact's exact statement)."""
    clauses = myth_clauses(myth)
    for f in (sheet or {}).get("facts") or []:
        if (not isinstance(f, dict) or str(f.get("verdict") or "").strip().lower() not in FACT_VERDICTS
                or str(f.get("statement") or "").rstrip().endswith("?")):
            continue
        st = str(f.get("statement") or "")
        st_words, st_neg = _content(st), set(_words(st)) & NEGATIONS
        for c in clauses:
            # the fact with only a hedge or limiter dropped ("The ring is 40 km across" for "about 40 km"): every
            # content word and figure of the clause is the fact's, and the fact negates nothing the clause keeps
            cw = _content(c) - NEGATIONS
            if (len(cw) >= 2 and cw <= st_words and set(_nums(c)) <= set(_nums(st))
                    and st_neg <= set(_words(c))):
                return str(f["id"])
            # a figure with its unit the fact states ("40 km"): the strike would run through a true measurement,
            # whatever the clause adds around it (review 2026-11-03, round 3: "The ring is 40 km wide and aliens
            # built it", where "wide" paraphrases the fact's "across")
            if _figures_with_units(c) & _figures_with_units(st):
                return str(f["id"])
            # the clause said as true by one sentence of the fact's own evidence, negations and all (review
            # 2026-11-03, round 3: "Aliens did not build the ring" passed while the evidence said "aliens did not
            # build it"); a sentence that reports a belief or a myth states nothing true
            # (a negated clause only: a positive one found in evidence is most often the myth being reported)
            if (len(cw) >= 2 and set(_words(c)) & NEGATIONS
                    and any(_evidence_states(c, cw, q) for q in _evidence_sentences(f))):
                return str(f["id"])
            try:
                ground_text(c, {"facts": [f], "topic": ""}, [f["id"]], role="myth")
            except (CardRefused, KeyError):
                continue
            return str(f["id"])
    return None


_FIG_UNIT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(%|[a-z]+)")
_REPORTING = re.compile(r"\b(believes?|believed|claims?|claimed|says?|said|thinks?|thought|suggests?|suggested|"
                        r"legends?|rumou?rs?|rumou?red|theory|theories|supposedly|allegedly|imagined|popular|"
                        r"considered|regarded|reputed|according|whether|if|might|may|perhaps|possibly|asked|"
                        r"some say)\b", re.IGNORECASE)


def _figures_with_units(s: str) -> set[tuple[str, str]]:
    out = set()
    for m in _FIG_UNIT.finditer(_fold(s)):
        unit = _norm_word(m.group(2))
        if unit and unit not in _STOP and not _is_num(unit):
            out.add((m.group(1).replace(",", ""), unit))
    return out


def _evidence_sentences(fact: dict) -> list[str]:
    out = []
    for e in fact.get("evidence") or []:
        for sent in re.split(r"(?<=[.!?])\s+", str((e or {}).get("quote") or "")):
            # an assertion only: no question, no reported belief or myth marker
            if (sent.strip() and not sent.rstrip().endswith("?") and not MYTH_MARKERS.search(sent)
                    and not _REPORTING.search(sent)):
                out.append(sent)
    return out


def _evidence_states(clause: str, words: set[str], sentence: str) -> bool:
    """`sentence` holds every content word of `clause` and the same negation (both negate, or neither)."""
    return (words <= _content(sentence)
            and bool(set(_words(clause)) & NEGATIONS) == bool(set(_words(sentence)) & NEGATIONS))


def _expect(spec_value: Any, field_value: Any, label: str) -> None:
    v = " ".join(str(spec_value or "").split())
    if v and _canon(v) != _canon(" ".join(str(field_value or "").split())):
        raise CardRefused(f"the {label} {v!r} is not the checked field's {label} {field_value!r}", code="not_verbatim")


def plan_myth_fact(spec: MythFact, sheet: dict, width: int = 1080, height: int = 1920, *,
                   payload: Mapping[str, Any] | None = None) -> CardPlan:
    """The myth card from one checked myth field (CardRef("myths", id)): the WHOLE myth text, never clause-trimmed,
    struck out; the field's correction as the fact line; its source's host. `payload` is accepted for a uniform
    signature; a myth card never needs the fact gate's record."""
    geo = _Geo(width, height)
    if spec.fact_src is not None:
        raise CardRefused("the fact line is the myth field's own correction: no other source is taken",
                          code="no_provenance")
    f = card_field(sheet, spec.myth_src, "myths")
    myth = " ".join(f["myth"].split())
    fact_t = " ".join(f["correction"].split())
    _expect(spec.myth_text, myth, "myth")
    _expect(spec.fact_text, fact_t, "correction")
    fid = f["refuting_fact_id"]
    for label, v in (("myth fact", spec.myth_fact_id), ("fact", spec.fact_id)):
        if v and v != fid:
            raise CardRefused(f"the {label} {v!r} is not the myth field's refuting fact {fid!r}", code="bad_field")
    # 16:9 fact: 96, not the brief's 110 (round 1 review 10). The character limits are a first gate only; the binding
    # one is the line limit at the fixed size, checked below. The myth is the whole field: too long refuses.
    lim_m, lim_f = (70, FACT_CHARS_WIDE) if geo.wide else (56, 84)
    if len(myth) > lim_m or len(fact_t) > lim_f:
        raise CardRefused(f"the myth ({len(myth)}) or fact ({len(fact_t)}) runs past {lim_m}/{lim_f} characters")
    if myth.lower() == fact_t.lower():
        raise CardRefused("the myth and the fact say the same")
    if "…" in myth or "..." in myth:
        raise CardRefused("the myth carries an ellipsis")
    # second guards, which can only refuse: no supported fact on the sheet states the myth as true; the correction
    # holds to its fact by the earlier NLP check; the two share a content word
    hit = _affirmed(myth, sheet)
    if hit:
        raise CardRefused(f"the fact {hit!r} states the myth's words as true: the card would strike out a fact",
                          code="affirmed_myth")
    myth = typeset(myth)
    _glyphs_ok(myth, "Montserrat", 600)
    fact_t = ground_text(fact_t, sheet, [fid], role="fact")
    if not {w for w in _content(myth) if len(w) >= 4} & {w for w in _content(fact_t) if len(w) >= 4}:
        raise CardRefused("the myth and the fact share no content word")
    raw_host = (urlparse(str(f["source"]["url"])).hostname or "").lower()
    host = display_host(raw_host).upper() or None        # M12: display only; the record keeps raw_host
    prov_m = {"source": "card_field", "kind": "myths", "id": spec.myth_src.id, "part": "myth",
              "refuting_fact_id": fid}
    prov_f = {**prov_m, "part": "correction"}
    spec = MythFact(myth, fid, fact_t, fid, spec.photo, spec.fact_at, spec.myth_src)
    m_draw, f_draw = myth.upper(), fact_t.upper()
    _glyphs_ok(m_draw, "Anton", None)
    fit = _myth_fit(m_draw, f_draw, host, geo)
    if fit is None:
        raise CardRefused("the myth or the fact runs past its line limit at the floor size"
                          + (" with its source line kept" if host else ""), code="no_fit")
    P, m_lines, f_lines, host, omitted, Pm, compact = fit
    nm, nf = len(m_lines), len(f_lines)
    # the pen (freeze panel M6): one stroke per myth line, each timed to its width
    pen = _pen_schedule([_width(ln, get_font("Anton", geo.px(Pm))) for ln in m_lines])
    pen_total = pen[2]
    # timing (freeze panel M7): the myth lands, holds for its read (at most MYTH_READ_MAX), steps back under the
    # strike, then the fact lands and holds for its own read from its first line
    rest_m = 0.65 + 0.08 * (nm - 1)
    t_s = rest_m + min(MYTH_READ_MAX, max(MYTH_READ_MIN, _read(myth)))      # M11: read 1.00..2.00 s
    if spec.fact_at is not None:
        t_s = max(rest_m + MYTH_READ_MIN, float(spec.fact_at) - pen_total)
    t_f = t_s + pen_total + STRIKE_SETTLE
    fact_starts = [t_f + 0.10 + 0.12 * k for k in range(nf)]
    t_src = fact_starts[-1] + 0.20
    fk_rule = fk_label = t_f
    panel_open = None
    if not geo.wide:
        # freeze panel M11 (4): the INK panel's foot stands MYTH_PANEL_OPEN under the last myth baseline until t_f,
        # then eases to its final foot over STEP_BACK; every fact element waits until the foot is FACT_PANEL_AIR
        # below its ink
        m_rel, f_rel, F_rel, src_rel, h_b, R_b = _myth_place(P, m_lines, f_lines, host, False, Pm, compact)
        b0 = R_b + m_rel[-1] + MYTH_PANEL_OPEN
        b1 = max(R_b + h_b + 40, MYTH_PANEL[1])
        panel_open = (b0, b1, t_f, STEP_BACK)

        def need(y: float) -> float:
            q = (y + FACT_PANEL_AIR - b0) / max(1.0, b1 - b0)
            if q <= 0:
                return t_f
            lo, hi = 0.0, 1.0
            for _ in range(40):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if _ease_io(mid) < q else (lo, mid)
            return t_f + STEP_BACK * hi
        f_lab = get_font("Montserrat", KICKER_WIDE_PX if geo.wide else 40, weight=800)
        f_fact = get_font("Anton", P)
        fk_rule = need(R_b + F_rel + 4)
        fk_label = need(R_b + F_rel + MYTH_KICK[False] + _ink("FACT", f_lab)[3])
        fact_starts = [max(t0, need(R_b + b + _ink(ln, f_fact)[3]))
                       for t0, b, ln in zip(fact_starts, f_rel, f_lines)]
        if host:
            f_src = get_font(DATA_FONT, SOURCE_PX[False], weight=SOURCE_WEIGHT)
            t_src = max(fact_starts[-1] + 0.20, need(R_b + src_rel + max(_ink(host, f_src)[3], 12)))
    rest_f = max(t_f + STEP_BACK, t_f + REVEAL, fact_starts[-1] + REVEAL, (t_src + REVEAL) if host else 0.0,
                 fk_label + REVEAL)
    seconds = max(MIN_SECONDS, max(rest_f + FACT_REST_HOLD, fact_starts[0] + max(2.0, _read(fact_t))) + TRANS_DUR)
    frames, seconds = _frames(seconds)
    if seconds > MAX_SECONDS:
        raise CardRefused(f"the myth card would run {seconds:.2f} s (over {MAX_SECONDS:.0f})")
    last_t = (frames - 1) / VIDEO_FPS
    why = ""
    for dim in MYTH_DIM:
        # round 4 review 5: a bright photo is dimmed further, never a refusal, while a dimmer ground can still
        # carry the stepped-back myth
        try:
            return _myth_compose(spec, geo, P, m_lines, f_lines, host, omitted, dim, last_t, frames, seconds,
                                 {"t_s": t_s, "t_f": t_f, "rest_m": rest_m, "rest_f": rest_f,
                                  "fact_starts": fact_starts, "t_src": t_src, "pen": pen, "fk_rule": fk_rule,
                                  "fk_label": fk_label, "panel_open": panel_open, "raw_host": raw_host,
                                  "notes": _correction_notes(fact_t, fact_by_id(sheet, fid))},
                                 {"myth": prov_m, "fact": prov_f}, Pm=Pm, verdict=str(f.get("verdict") or "false"),
                                 compact=compact)
        except CardRefused as e:
            if e.code != "weak_twin":
                raise
            why = e.reason
    raise CardRefused(why + " on the darkest ground")


# The myth card (round 4, F): MYTH and FACT as two equal display statements in Anton caps, the myth struck through
# by a bold GOLD bar on every line and stepped back to STONE_LT, the fact revealed in BONE (its figures GOLD) as the
# hero. One size for both, the largest of MYTH_SIZES at which both wrap within their line limits and the block
# fits the text zone (9:16: y 330-1058 over the caption band; 16:9: two columns in title-safe y 129-816, the block
# centred on y 475 like the 16:9 quote).
MYTH_SIZES = (128, 120, 112, 104, 96, 92, 88, 84, 80, 76, 72, 68, 64)
# The ground: the photo toned (toned_ground), dark across the block by MYTH_DIM (tried in turn for the STONE_LT
# twin: a bright photo is darkened further, never refused), opening above and below it (_myth_ground).
MYTH_DIM = (0.80, 0.86, 0.91, 0.95)
MYTH_FEATHER = 300                   # design px over which the ground opens above the block
MYTH_BLUR = 36                       # design px Gaussian (review 2026-11-03: at least 18; 22 left 200 px caps legible)
MYTH_LINES = {False: (4, 4), True: (4, 5)}
MYTH_MEASURE = {False: (788, 788), True: (617, 926)}   # 9:16: x 100 to the Reel box's right edge (888)
MYTH_X = {False: (100, 100), True: (153, 840)}         # 16:9: the fact's column runs to title-safe's right (1766)
# freeze panel M10: the 16:9 divider at 786 (was 806), so the myth column gains 17 px and the fact 20
# review 2026-11-03 (second round, 3): on 16:9 the struck myth outweighed the fact; the myth is set at this share of
# the fact's size there, so the fact is the hero (9:16 keeps one size: its two statements stack)
MYTH_RATIO = {False: 0.86, True: 0.70}
# review 2026-11-03 (round 3, C): the 9:16 myth is set a step under the fact too (0.86), so a long myth takes two
# lines and the card keeps its size (the long myth sets at 88 px, not 76); on 16:9 the myth takes the largest of
# MYTH_WIDE_RATIOS whose column is no taller than the fact's, and is centred against it (no empty myth column)
# freeze panel round 4 (M10: a 96 px fact's myth at >= 84 needs a share of 0.875): the ladder starts at 0.88 (was
# 0.86); the M3 column rule still steps a myth down wherever its column would stand too tall
MYTH_WIDE_RATIOS = (0.88, 0.86, 0.80, 0.75, 0.70, 0.65, 0.60)   # freeze panel M2: to 0.60
MYTH_DIVIDER_AIR = 54              # 16:9: design px from the divider to the fact column
MYTH_TOP = {False: 288, True: 129}          # design px: the block's top, its lowest ink and its centre
MYTH_BOTTOM = {False: 1058, True: 816}
MYTH_CENTRE = {False: 694, True: 475}
# freeze panel M11 (5): the rule-to-label drop grows by 8 (was 46 / 42) and the label-to-content air gives them up
# (was 36), so the rule top, every content baseline and the lowest ink stand where they did (chapter kickers too)
# 16:9 takes 51 / 27, not the panel's 50 / 28: its 40 px label (M9) has a 29 px cap, so 50 left 17 px of air under the
# rule against the 18 the panel asks for
MYTH_KICK = {False: 54, True: 51}  # the kicker rule's top to its label's baseline
MYTH_LABEL_AIR = {False: 28, True: 27}   # the label's baseline to the first line's cap top
MYTH_GAP = 76                      # 9:16: the myth's last baseline to the FACT kicker's rule
MYTH_STEP = 1.16                   # x P: line step under a strike (round 4 review 2: 1.06 read as one lump)
FACT_STEP = 1.0
MYTH_READ_MAX = 2.00               # s: the myth's read before the strike, at most (freeze panel M11; M7 2.60)
MYTH_READ_MIN = 1.00               # s: and at least (M11: was 1.20)
FACT_REST_HOLD = 1.50              # s: the whole card held at rest at least this before the transition
STRIKE_BACK = _mix(GOLD, INK, 0.28)   # freeze panel M5: the strike's stepped-back colour, #B18728 (GOLD 72 / INK 28)
STRIKE_KEYLINE = 1                 # design px of INK directly above and below the bar (M11: was 2)
STRIKE_THICK = (0.08, 5, 10)       # the bar: clamp(round(0.08 x Pm), 5, 10) design px (M11: was 0.10, 6..12)
MYTH_PANEL_OPEN = 120              # 9:16 (M11): design px under the last myth baseline the INK panel's foot stands
                                   # until the fact lands, then it eases down to its final foot over STEP_BACK
FACT_PANEL_AIR = 24                # 9:16 (M11): a fact element shows only once the panel's foot is this far below it


def _pen_schedule(widths: Sequence[float]) -> tuple[list[float], list[float], float]:
    """(start offsets from t_s, durations, pen total) of the myth's strokes (freeze panel M6, M11): each line's
    stroke STRIKE_PEN x its width / the widest, at least STRIKE_LINE_MIN, STRIKE_LINE_GAP between strokes, scaled
    down so the pen fits STRIKE_PEN_MAX (STRIKE_PEN_MAX_LONG for 3 lines or more) with no stroke under
    STRIKE_LINE_FLOOR where that budget allows it (the longer strokes give the time up)."""
    mx = max(1.0, max(widths))
    durs = [max(STRIKE_LINE_MIN, STRIKE_PEN * w / mx) for w in widths]
    gaps = STRIKE_LINE_GAP * (len(durs) - 1)
    budget = (STRIKE_PEN_MAX_LONG if len(durs) >= 3 else STRIKE_PEN_MAX) - gaps
    if sum(durs) > budget:
        floor = min(STRIKE_LINE_FLOOR, budget / len(durs))
        lo, hi = 0.0, 1.0
        for _ in range(60):          # the scale k at which sum(max(floor, d x k)) meets the budget
            k = (lo + hi) / 2
            if sum(max(floor, d * k) for d in durs) > budget:
                hi = k
            else:
                lo = k
        durs = [max(floor, d * lo) for d in durs]
    starts, acc = [], 0.0
    for d in durs:
        starts.append(round(acc, 6))
        acc += d + STRIKE_LINE_GAP
    return starts, [round(d, 6) for d in durs], round(sum(durs) + gaps, 6)


@lru_cache(maxsize=64)
def _cap(px: int) -> int:
    return -get_font("Anton", px).getbbox("H", anchor="ls")[1]


def myth_px(P: int, wide: bool, ratio: float | None = None) -> int:
    """The myth's size for fact size P (MYTH_RATIO, or `ratio`, to an even px)."""
    return 2 * round((MYTH_RATIO[wide] if ratio is None else ratio) * P / 2)


def _source_drop(P: int, wide: bool, compact: bool = False) -> int:
    """The source baseline's drop below the last fact baseline (16:9, freeze panel M10: max(68, round(0.58 x P)))."""
    if wide:
        return max(SOURCE_DROP[True], round(SOURCE_DROP_WIDE_SHARE * P))
    return SOURCE_DROP_COMPACT if compact else SOURCE_DROP[False]


def _myth_rows(P: int, nm: int, nf: int, host: bool, wide: bool, Pm: int | None = None, compact: bool = False,
               m_lines: Sequence[str] | None = None, f_lines: Sequence[str] | None = None
               ) -> tuple[list[int], list[int], int, int, int]:
    """(myth baselines, fact baselines, the FACT kicker's rule top, source baseline, block bottom), relative to the
    MYTH kicker's rule top. `compact` (9:16, under MYTH_FLOOR_916 only): the tighter gap and source drop. With the
    lines given, an accented line's step grows (freeze panel A1)."""
    kick, cap, Pm = MYTH_KICK[wide], _cap(P), (myth_px(P, wide) if Pm is None else Pm)
    first = kick + MYTH_LABEL_AIR[wide] + _cap(Pm)
    m_step, f_step = round(MYTH_STEP * Pm), round(FACT_STEP * P)
    m_off = _lead(m_lines, Pm, m_step, _cap(Pm)) if m_lines else [m_step * k for k in range(nm)]
    m_base = [first + o for o in m_off]
    F = 0 if wide else m_base[-1] + (MYTH_GAP_COMPACT if compact else MYTH_GAP)
    f_first = F + kick + MYTH_LABEL_AIR[wide] + cap
    f_off = _lead(f_lines, P, f_step, cap) if f_lines else [f_step * k for k in range(nf)]
    f_base = [f_first + o for o in f_off]
    drop = _source_drop(P, wide, compact)
    src = f_base[-1] + drop if host else f_base[-1]
    bottom = max(m_base[-1], src) + 12          # to the last line's descender
    return m_base, f_base, F, src, bottom


MYTH_FLOOR_916 = 84     # review 2026-11-03 (5; round 3: the long myth reaches 84): the 9:16 type is at least this where it fits
MYTH_FLOOR_SOURCE = 76  # design handover 2026-11-04 (freeze step 1): the 9:16 type may drop to this to keep the
                        # source line; below it, the largest size that still holds the source (never drawn without)
MYTH_EXTRA_LINE = 1     # 9:16: the fact may take this many lines past MYTH_LINES to keep its source line
# freeze panel 2026-11-04 (M1): at each 9:16 fact size the myth tries these shares in turn, in at most
# MYTH_TALL_MYTH_LINES lines, never under MYTH_FLOOR_MYTH (round(0.76 x 76))
MYTH_TALL_RATIOS = (0.86, 0.80, 0.76)
MYTH_TALL_MYTH_LINES = 2
MYTH_FLOOR_MYTH = 58
MYTH_GAP_COMPACT = 56       # the compact tier (9:16 cards under MYTH_FLOOR_916 only): MYTH_GAP 76 -> 56
SOURCE_DROP_COMPACT = 60    # and SOURCE_DROP[False] 72 -> 60
# freeze panel 2026-11-04 (M2): 16:9 floors (a hosted card under them is refused) and the long-word lone exception
MYTH_WIDE_FLOOR = 88
MYTH_WIDE_MYTH_FLOOR = 64
# freeze panel round 2 (review: 'THE IMPACT / THEORY WAS / DISPROVED IN 1969', 'WAS DISPROVED WHEN'): a fit whose
# myth or fact ends a non-last line on a WEAK_LINE_END word takes the first smaller size, at most this many design px
# down (and never under the tier's floor), whose lines have fewer such ends
MYTH_WEAK_STEP = 24
MYTH_FOUR_STEP = 8         # 16:9: a 4-line fact this many design px smaller beats a 5-line one with as many weak ends
LONE_WORD_CHARS = 12        # a one-word 16:9 myth line of this many letters, or this share of the myth measure, is
LONE_WORD_SHARE = 0.70      # a long word filling its line, not a word left alone
STRIKE_OVERHANG = 0.06      # x the myth's px: the strike runs this far past each end of its line's ink (T1)
# freeze panel 2026-11-04 (M9): the source line and its chrome label
SOURCE_PX = {False: 38, True: 38}      # freeze panel M12 (was 34 / 36)
SOURCE_WEIGHT = 600
SOURCE_FILL = _mix(STONE_LT, BONE, 0.285)              # #C9C2B5
SOURCE_TARGET = 7.0         # the source domain's drawn contrast
SOURCE_LABEL = "SOURCE"     # design chrome, like MYTH and FACT: never card text
SOURCE_LABEL_PX = {False: 28, True: 28}      # freeze panel M12 (was 24 / 26)
SOURCE_LABEL_TARGET = 7.0   # M12: the label is planned at this, not PLACE_TARGET
SOURCE_LABEL_TRACK = 0.18   # em
SOURCE_LABEL_FILL = _mix(brand.STONE, INK, 0.083)      # #8E887B (the panel's #8E887D, from the tokens)
SOURCE_LABEL_GAP = 14       # design px from the label's ink to the domain


def _overhang(Pm: int) -> int:
    """Design px the strike runs past each end of a myth line's ink (T1: round(0.06 x Pm))."""
    return round(STRIKE_OVERHANG * Pm)


def _source_label_font(geo: _Geo):
    return get_font("Montserrat", geo.px(SOURCE_LABEL_PX[geo.wide]), weight=800)


def _source_width(host: str, geo: _Geo) -> int:
    """Frame px the source line takes: the SOURCE label, its gap and the domain."""
    lf = _source_label_font(geo)
    label = _tracked_width(SOURCE_LABEL, lf, SOURCE_LABEL_TRACK * lf.size)
    return round(label) + geo.px(SOURCE_LABEL_GAP) + _width(host, get_font(DATA_FONT, geo.px(SOURCE_PX[geo.wide]),
                                                                          weight=SOURCE_WEIGHT))


def _myth_wrap(m_draw: str, pm: int, m_meas: int, m_lim: int, geo: _Geo) -> list[str] | None:
    """The myth's lines at `pm`, broken inside the measure less the strike's overhang on both sides (T1), unless that
    costs a line the full measure does not (the clamp to the zone then trims the overhang instead)."""
    full = _design_wrap(m_draw, "Anton", None, pm, m_meas, m_lim, geo, even=True)
    held = _design_wrap(m_draw, "Anton", None, pm, m_meas - 2 * math.ceil(STRIKE_OVERHANG * pm), m_lim, geo,
                        even=True)
    if held and full and len(held) == len(full) and _stop_end(held) != _stop_end(full):
        # freeze panel W2: of two wraps with one line count, the one with no stop-word end (the strike's clamp then
        # trims the overhang)
        return full if _stop_end(held) else held
    return held if held and (not full or len(held) <= len(full)) else full


def _myth_fit(m_draw: str, f_draw: str, host: str | None, geo: _Geo):
    """(P, myth lines, fact lines, host, why omitted, myth px, compact) at the largest size that fits; None when none
    does. Never a smaller size to save a line (review 2026-11-03: an extra line is preferred), and lines are evenly
    broken with no lone word (_wrap even). The source line is never dropped (design handover, 2026-11-04).
    9:16 with a source (freeze panel M1): at each fact size from MYTH_FLOOR_916 down the myth tries MYTH_TALL_RATIOS
    in at most two lines; the fact may take MYTH_EXTRA_LINE more lines; then the same down to MYTH_FLOOR_SOURCE; then
    a compact tier (MYTH_GAP_COMPACT, SOURCE_DROP_COMPACT) at those sizes under MYTH_FLOOR_916; then (freeze step 1)
    the largest size under MYTH_FLOOR_SOURCE that holds the source; else refused. As in freeze step 1, a break with
    no lone word on any rung is taken before a lone-word break on any rung.
    16:9 (M2): the largest fact size from MYTH_WIDE_FLOOR at which a myth share of MYTH_WIDE_RATIOS sets at least
    MYTH_WIDE_MYTH_FLOOR px in at most four lines, no more than one myth line taller than the fact column (M3)."""
    (m_max, f_max), (m_meas, f_meas) = MYTH_LINES[geo.wide], MYTH_MEASURE[geo.wide]
    top, bottom = MYTH_TOP[geo.wide], MYTH_BOTTOM[geo.wide]
    room = bottom - top
    stops: list[str] = []
    strict = [True]

    def ok(lines: list[str] | None) -> list[str] | None:
        """W2 (hard): a wrap ending a line other than the last on a W1_STOPS word is no wrap at this size."""
        if lines and strict[0] and _stop_end(lines):
            stops.append(" / ".join(lines))
            return None
        return lines

    def mwrap(pm: int, lim: int) -> list[str] | None:
        return ok(_myth_wrap(m_draw, pm, m_meas, lim, geo))

    def fwrap(P: int, lim: int) -> list[str] | None:
        return ok(_design_wrap(f_draw, "Anton", None, P, f_meas, lim, geo, even=True, cohesive=True))

    def lone(lines: list[str], text: str, pm: int | None = None) -> bool:
        def alone(ln: str) -> bool:
            w = ln.split()
            if len(w) == 1 and pm is not None and geo.wide and (
                    len(w[0].strip(".,;:")) >= LONE_WORD_CHARS
                    or _width(ln, get_font("Anton", pm)) >= LONE_WORD_SHARE * m_meas):
                return False      # M2: a long word fills its line, it is not left alone
            return len(w) == 1 or all(x.lower().strip(".,;:") in _STOP for x in w)
        return len(lines) > 1 and len(text.split()) >= 4 and any(alone(ln) for ln in lines)

    def bare_first():
        """The 9:16 card with no source: one myth share, as before the freeze panel."""
        fits = []
        for P in MYTH_SIZES:
            pm = myth_px(P, False)
            m_lines = mwrap(pm, m_max)
            f_lines = fwrap(P, f_max)
            if not m_lines or not f_lines:
                continue
            if _myth_rows(P, len(m_lines), len(f_lines), False, False, m_lines=m_lines, f_lines=f_lines)[4] > room:
                continue
            if not (lone(m_lines, m_draw) or lone(f_lines, f_draw)):
                return P, m_lines, f_lines, pm, False
            fits.append((P, m_lines, f_lines, pm, False))
        return fits[0] if fits else None

    def tall_at(P: int, f_lim: int, compact: bool):
        """(fit, lone) at fact size P: the first myth share with no lone word, else the first that fits."""
        f_lines = fwrap(P, f_lim)
        if not f_lines:
            return None, False
        fit_lone = None
        for r in MYTH_TALL_RATIOS:
            pm = myth_px(P, False, r)
            if pm < MYTH_FLOOR_MYTH:
                continue
            m_lines = mwrap(pm, MYTH_TALL_MYTH_LINES)
            if not m_lines:
                continue
            if _myth_rows(P, len(m_lines), len(f_lines), True, False, pm, compact, m_lines, f_lines)[4] > room:
                continue
            if not (lone(m_lines, m_draw) or lone(f_lines, f_draw)):
                return (P, m_lines, f_lines, pm, compact), False
            fit_lone = fit_lone or (P, m_lines, f_lines, pm, compact)
        return fit_lone, fit_lone is not None

    def tall_first(f_lim: int, floor: int, compact: bool, clean: bool = False):
        fits = []
        sizes = [p for p in MYTH_SIZES if p >= floor and not (compact and p >= MYTH_FLOOR_916)]
        for i, P in enumerate(sizes):
            out, is_lone = tall_at(P, f_lim, compact)
            if out is None:
                continue
            if not is_lone:
                return less_weak(out, (q for q in sizes[i + 1:]), lambda Q: tall_at(Q, f_lim, compact)[::-1])
            fits.append(out)
        return fits[0] if fits and not clean else None

    def less_weak(out, smaller, at):
        """Round 2: the first smaller size, at most MYTH_WEAK_STEP px down, whose myth and fact lines have fewer weak
        ends than `out`'s (no lone word); else `out`. `at(Q)` gives (lone, fit) at size Q."""
        w0 = weak_ends(out[1]) + weak_ends(out[2])
        if not w0:
            return out
        for Q in smaller:
            if Q < out[0] - MYTH_WEAK_STEP:
                break
            is_lone, cand = at(Q)
            if cand and not is_lone and weak_ends(cand[1]) + weak_ends(cand[2]) < w0:
                return cand
        return out

    def weak_ends(lines: list[str]) -> int:
        return sum(ln.split()[-1].lower().strip(".,;:") in WEAK_LINE_END for ln in lines[:-1] if ln.split())

    def wide_at(P: int, hosted: bool, f_lim: int, allow_lone: bool = False):
        kick = MYTH_KICK[True]
        f_lines = fwrap(P, f_lim)
        if not f_lines:
            return None
        f_rows = _myth_rows(P, 1, len(f_lines), hosted, True, f_lines=f_lines)
        f_low = f_rows[3] if hosted else f_rows[1][-1]
        if f_low + 12 > room:
            return None
        for r in MYTH_WIDE_RATIOS:
            pm = myth_px(P, True, r)
            if hosted and pm < MYTH_WIDE_MYTH_FLOOR:
                continue
            ml = mwrap(pm, m_max)
            if not ml or (lone(ml, m_draw, pm) and not allow_lone):
                continue
            m_step = round(MYTH_STEP * pm)
            m_low = kick + MYTH_LABEL_AIR[True] + _cap(pm) + _lead(ml, pm, m_step, _cap(pm))[-1]
            # M3: the myth column may stand at most one myth line taller than the fact column
            if m_low > f_low + m_step or max(m_low, f_low) + 12 > room:
                continue
            return P, ml, f_lines, pm, False
        return None

    def wide_first(hosted: bool, allow_lone: bool = False):
        """W2 before M2: when the only breaks with no stop-word end leave a myth word alone ("EXTRATERRESTRIALS /
        CONSTRUCTED / THE CIRCULAR RINGS"), the second pass (allow_lone) takes them rather than refusing."""
        fits = []
        sizes = [p for p in MYTH_SIZES if p >= (MYTH_WIDE_FLOOR if hosted else 0)]
        for i, P in enumerate(sizes):
            out = wide_at(P, hosted, f_max, allow_lone)
            if out is None:
                continue
            if len(out[2]) == f_max and f_max > 4:
                # freeze panel M10 (2): four fact lines at a smaller size beat five at a larger one when they leave
                # fewer weak line ends (never under the floors); round 2: or as few, at most MYTH_FOUR_STEP px
                # smaller (five lines at 104 split "WHEN SUSPECTED / COESITE" where four at 96 keep the phrase)
                for Q in sizes[i + 1:]:
                    four = wide_at(Q, hosted, 4, allow_lone)
                    if not four or len(four[2]) > 4 or lone(four[2], f_draw):
                        continue
                    w4, w5 = weak_ends(four[2]), weak_ends(out[2])
                    if w4 < w5 or (w4 <= w5 and Q >= out[0] - MYTH_FOUR_STEP):
                        out = four
                        break
            if not lone(out[2], f_draw):
                def at(Q: int):
                    c = wide_at(Q, hosted, f_max, allow_lone)
                    return (c is None or lone(c[2], f_draw)), c
                return less_weak(out, sizes[i + 1:], at)
            fits.append(out)
        return fits[0] if fits else None

    if host and _source_width(host, geo) > geo.px(f_meas):
        return None           # the source line is never dropped: a domain wider than its column refuses

    def first_fit():
        if geo.wide:
            return wide_first(bool(host)) or wide_first(bool(host), allow_lone=True)
        if host:
            extra = f_max + MYTH_EXTRA_LINE
            tries = ((f_max, MYTH_FLOOR_916, False), (extra, MYTH_FLOOR_916, False),
                     (f_max, MYTH_FLOOR_SOURCE, False), (extra, MYTH_FLOOR_SOURCE, False),
                     (f_max, MYTH_FLOOR_SOURCE, True), (extra, MYTH_FLOOR_SOURCE, True),
                     # freeze step 1: then the largest size that holds the source (never drawn without it)
                     (f_max, 0, False), (extra, 0, False), (f_max, 0, True), (extra, 0, True))
            # freeze step 1: a break with no lone word first, on every rung, before any rung takes one
            return next((h for h in (tall_first(n, fl, c, clean) for clean in (True, False) for n, fl, c in tries)
                         if h), None)
        return bare_first()
    fit = first_fit()
    if not fit:
        if stops:
            strict[0] = False
            if first_fit():      # it fits only with a stop-word end: W2 refuses it (never shipped)
                raise CardRefused("every size that holds the myth and the fact ends a line on a stop word ("
                                  + stops[0] + ")", code="stop_word_end")
        return None
    P, m_lines, f_lines, Pm, compact = fit
    return P, m_lines, f_lines, host, None, Pm, compact


MYTH_SPREAD = 80        # 9:16 design px the FACT group may move down from the myth (freeze panel M4: was 150)
MYTH_PAIR_MAX = 1.4     # x P: the myth's last baseline to the FACT rule, at most (freeze panel M4)


def _myth_place(P: int, m_lines: Sequence[str], f_lines: Sequence[str], host: str | None, wide: bool, Pm: int,
                compact: bool) -> tuple[list[int], list[int], int, int, int, int]:
    """(myth baselines, fact baselines, FACT rule top, source baseline, block height), relative to R, and R: the
    block placed in its zone. 9:16 (review 2026-11-03 (5)): the FACT group set down from the myth by up to
    MYTH_SPREAD of the room left, never so far that the pair parts (M4: the myth's last baseline to the FACT rule at
    most MYTH_PAIR_MAX x P). Both formats (M3): the block centred on MYTH_CENTRE while the zone allows."""
    m_rel, f_rel, F_rel, src_rel, h = _myth_rows(P, len(m_lines), len(f_lines), bool(host), wide, Pm, compact,
                                                 m_lines, f_lines)
    top, bottom_zone, centre = MYTH_TOP[wide], MYTH_BOTTOM[wide], MYTH_CENTRE[wide]
    if not wide:
        gap = F_rel - m_rel[-1]
        extra = max(0, min(MYTH_SPREAD, (bottom_zone - top) - h, round(MYTH_PAIR_MAX * P) - gap))
        f_rel = [v + extra for v in f_rel]
        F_rel, src_rel, h = F_rel + extra, src_rel + extra, h + extra
    R = max(top, min(round(centre - h / 2), bottom_zone - h))
    return m_rel, f_rel, F_rel, src_rel, h, R


def _correction_notes(correction: str, fact: dict) -> list[str]:
    """Freeze panel M12 (3): "correction_drops_specifics" when the correction keeps none of its refuting fact's
    numbers, dates or named findings (its figures and its capitalised content words after the first)."""
    stmt = str(fact.get("statement") or "")
    words = re.findall(r"[^\W\d_][\w'-]*", stmt)
    names = {w.casefold() for w in words[1:] if w[:1].isupper() and w.casefold() not in _STOP}
    specifics = set(_nums(stmt)) | names
    if not specifics:
        return []
    kept = set(_nums(correction)) | {w.casefold() for w in re.findall(r"[^\W\d_][\w'-]*", correction)}
    return [] if specifics & kept else ["correction_drops_specifics"]


# ---- the toned ground (review 2026-11-03, second round, items 3-5): the photo blurred and re-toned, never dimmed
# toward one flat charcoal. Its own light and shade are kept (normalised, so a flat white or yellow frame and a busy
# one read alike), its hue kept at TONE_KEEP of its saturation (yellows turned toward amber, so a dark yellow never
# reads olive), a colourless photo given INK's cool slate; the brightness of every row is set by a band profile:
# dark across the text block, opening above and below it on one smooth curve (no step, no flat slab).
TONE_KEEP = 0.60                  # share of the photo's saturation kept
TONE_SAT_MAX = 0.62
TONE_NEUTRAL = (213.0, 0.40)      # hue (degrees) and saturation given to a colourless photo: INK's slate
TONE_NEUTRAL_S = 0.16             # a photo pixel under this saturation counts as colourless
TONE_YELLOW = (58.0, 24.0, 22.0)  # yellows (centre, half width, degrees) turned this far toward amber
TONE_SPREAD_MIN = 0.32            # the photo's light is normalised over at least this luminance spread
TONE_VIGNETTE = 0.16              # the edges' darkening across the width


def _smooth(v: np.ndarray) -> np.ndarray:
    v = np.clip(v, 0.0, 1.0)
    return v * v * (3 - 2 * v)


def band_levels(geo: _Geo, y0: float, y1: float, inner: tuple[float, float], above: tuple[float, float],
                below: tuple[float, float], feather_up: float, feather_down: float
                ) -> tuple[np.ndarray, np.ndarray]:
    """(lo, hi) brightness per frame row: `inner` across design rows y0..y1, easing (smoothstep over the feathers)
    to `above` toward the frame's top and `below` toward its foot."""
    yy = (np.arange(geo.height, dtype=np.float32) + 0.5 - geo._oy) / geo.k
    up = _smooth((y0 - yy) / feather_up)
    down = _smooth((yy - y1) / feather_down)
    lo = inner[0] + (above[0] - inner[0]) * up + (below[0] - inner[0]) * down
    hi = inner[1] + (above[1] - inner[1]) * up + (below[1] - inner[1]) * down
    return lo, hi


def toned_ground(photo: Path | None, geo: _Geo, blur: float, levels: tuple[np.ndarray, np.ndarray]) -> Image.Image:
    """The photo cover-cropped, blurred by `blur` design px, re-toned (see TONE_*) to the rows' (lo, hi)
    brightness; without a photo, INK eased by the same profile."""
    from PIL import ImageFilter
    W, H = geo.width, geo.height
    lo, hi = (np.asarray(v, np.float32) for v in levels)
    q = 4
    sw, sh = max(8, W // q), max(8, H // q)
    rows = np.interp(np.linspace(0, H - 1, sh), np.arange(H), lo), np.interp(np.linspace(0, H - 1, sh),
                                                                            np.arange(H), hi)
    xs = (np.arange(sw, dtype=np.float32) + 0.5) / sw
    vig = 1.0 - TONE_VIGNETTE * ((xs - 0.5) * 2) ** 2
    if photo and Path(photo).exists():
        small = cover_crop(Path(photo), (W, H)).convert("RGB").resize((sw, sh), Image.BILINEAR)
        small = small.filter(ImageFilter.GaussianBlur(max(0.5, geo.px(blur) / q)))
        rgb = np.asarray(small, np.float32) / 255.0
        Y = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        spread = max(TONE_SPREAD_MIN, float(np.percentile(Y, 98) - np.percentile(Y, 2)))
        Yn = np.clip(0.5 + (Y - float(Y.mean())) / spread, 0.0, 1.0)
        hsv = np.asarray(small.convert("HSV"), np.float32) / 255.0
        h, sat = hsv[..., 0] * 360.0, hsv[..., 1] * np.clip(hsv[..., 2] * 1.4, 0, 1)
        c, half, turn = TONE_YELLOW
        h = h - turn * np.clip(1 - np.abs(h - c) / half, 0, 1)
        w = np.clip(1 - sat / TONE_NEUTRAL_S, 0, 1)
        nh, ns = TONE_NEUTRAL
        a = sat * TONE_KEEP * np.cos(np.radians(h)) * (1 - w) + ns * np.cos(np.radians(nh)) * w
        b = sat * TONE_KEEP * np.sin(np.radians(h)) * (1 - w) + ns * np.sin(np.radians(nh)) * w
    else:
        Yn = np.full((sh, sw), 0.5, np.float32)
        nh, ns = TONE_NEUTRAL
        a = np.full((sh, sw), ns * math.cos(math.radians(nh)), np.float32)
        b = np.full((sh, sw), ns * math.sin(math.radians(nh)), np.float32)
    S = np.clip(np.hypot(a, b), 0, TONE_SAT_MAX)
    Hd = (np.degrees(np.arctan2(b, a)) % 360.0) / 360.0
    V = (rows[0][:, None] + (rows[1] - rows[0])[:, None] * Yn) * vig[None, :]
    hsv_out = np.stack([Hd, S, np.clip(V, 0, 1)], -1)
    out = Image.fromarray(np.clip(hsv_out * 255 + 0.5, 0, 255).astype(np.uint8), "HSV").convert("RGB")
    return out.resize((W, H), Image.BICUBIC)


MYTH_INNER = 0.045                  # the block's darkest; its brightest is MYTH_INNER + (1 - dim) * MYTH_INNER_SPAN
MYTH_INNER_SPAN = 0.62
MYTH_ABOVE = (0.22, 0.70)           # the ground above the block (the photo opens toward the top)
MYTH_BELOW = (0.17, 0.62)           # and below it (9:16: the caption half keeps the photo's colour, no step)
MYTH_FEATHER_LOW = 460              # design px over which the ground opens below the block


def _myth_ground(photo: Path | None, geo: _Geo, last_t: float, dim: float, y0: float, y1: float,
                 opening: tuple[float, float, float, float] | None = None) -> tuple[GroundDrift, Image.Image]:
    """The myth card's ground (review 2026-11-03, second round, item 3): the whole frame one toned ground
    (toned_ground) blurred by MYTH_BLUR (text burned into a photo cannot be read behind the myth): dark across the
    block (design y0..y1; `dim` sets how dark), opening to MYTH_ABOVE over MYTH_FEATHER above it and to MYTH_BELOW
    over MYTH_FEATHER_LOW below it, so a flat bright photo is no grey slab over a light band and a dark one no
    flat charcoal."""
    inner = (MYTH_INNER, MYTH_INNER + (1 - dim) * MYTH_INNER_SPAN)
    if not geo.wide:
        # review 2026-11-03 (round 4): the panel is the graphics box itself (MYTH_PANEL), dark edge to edge, the
        # block centred in it; the photograph shows as itself above and below its crisp edges
        y0, y1 = min(y0, MYTH_PANEL[0]), max(y1, MYTH_PANEL[1])
    ground = toned_ground(photo, geo, MYTH_BLUR, band_levels(geo, y0, y1, inner, MYTH_ABOVE, MYTH_BELOW,
                                                             MYTH_FEATHER, MYTH_FEATHER_LOW))
    parts = None if geo.wide else _photo_outside(photo, geo, y1, top=y0)
    if parts and opening is not None:
        # freeze panel M11 (4): the panel's foot from opening[0] (design y) to its final y1, eased from opening[2]
        # over opening[3]; its top and edges stay crisp
        b0, b1, t0, dur = opening
        drift = _PanelDrift(ground, *parts, foot=(lambda t: b0 + (b1 - b0) * _ease_io((t - t0) / dur)),
                            geo=geo, top=y0)
    else:
        drift = _PanelDrift(ground, *parts) if parts else GroundDrift(ground)
    return drift, drift(last_t)


def _panel_mask(geo: _Geo, top: float, foot: float) -> Image.Image:
    """255 outside design y top..foot, 0 inside, one antialiased pixel between (as _photo_outside's mask)."""
    W, H = geo.width, geo.height
    Y = np.arange(H, dtype=np.float32) + 0.5
    m = np.maximum(np.clip(Y - geo.y(foot) + 0.5, 0, 1), np.clip(geo.y(top) - Y + 0.5, 0, 1))
    return Image.fromarray(np.clip(np.repeat(m[:, None], W, axis=1) * 255 + 0.5, 0, 255).astype(np.uint8), "L")


class _PanelDrift:
    """The 9:16 myth's ground per frame: the photograph outside the panel drifts (GroundDrift), the panel itself
    (the toned ground inside MYTH_PANEL) holds still, so its crisp edges stay on the graphics box all clip long."""

    def __init__(self, panel: Image.Image, outside: Image.Image, mask: Image.Image,
                 foot: Callable[[float], float] | None = None, geo: _Geo | None = None, top: float = 0.0) -> None:
        self.panel, self.outside, self.mask = panel, GroundDrift(outside), mask
        self.foot, self.geo, self.top = foot, geo, top
        self._masks: dict[int, Image.Image] = {}

    def foot_at(self, t: float) -> float | None:
        """The panel's foot (design y) at `t` (freeze panel M11 (4)); None when it does not move."""
        return None if self.foot is None else float(self.foot(t))

    def __call__(self, t: float) -> Image.Image:
        mask = self.mask
        if self.foot is not None:
            y = self.geo.y(self.foot(t))
            if y not in self._masks:
                self._masks[y] = _panel_mask(self.geo, self.top, (y - self.geo._oy) / self.geo.k)
            mask = self._masks[y]
        return Image.composite(self.outside(t), self.panel, mask)


# Review 2026-11-03 (round 3, C): under the 9:16 block (the caption half, past the graphics box) the toned ground
# was a dead flat slate on snow, white, cyan and busy photos. Round 4: an INK shade easing over it still read as a
# muddy grey field on flat grounds, and the block sat in a dark area that simply faded out. The 9:16 myth is now
# composed as the hook's title panel is: ONE designed panel, the graphics box with MYTH_PANEL_AIR round it (design y
# MYTH_PANEL), dark across its whole height with the block centred in it, its top and foot crisp (one antialiased
# pixel); above and below it the photograph itself, blurred as the ground is (MYTH_BLUR: an 8 px blur left a photo's
# burned-in 200 px caps legible above the panel), in its own colour (yellows turned toward amber) under a light even
# veil (MYTH_OUT_SHADE), so a flat white sky reads white, not grey.
MYTH_PANEL_AIR = 40          # design px the panel runs past the graphics box (its foot is the caption band's top)
MYTH_PANEL = (288 - MYTH_PANEL_AIR, 1058 + MYTH_PANEL_AIR)   # safe_zones.graphics_box() y, plus the air
MYTH_OUT_SHADE = 0.12        # the INK veil over the photograph outside the panel


def _photo_outside(photo: Path | None, geo: _Geo, y1: float, top: float | None = None
                   ) -> tuple[Image.Image, Image.Image] | None:
    """(the photograph as shown outside the panel, the mask that is 255 outside design y `top`..`y1` and 0 inside,
    one antialiased pixel between); None without a photo."""
    if not photo or not Path(photo).exists():
        return None
    from PIL import ImageFilter
    W, H = geo.width, geo.height
    pic = cover_crop(Path(photo), (W, H)).convert("RGB").filter(ImageFilter.GaussianBlur(geo.px(MYTH_BLUR)))
    # yellows turned toward amber first (TONE_YELLOW, as toned_ground does): a shaded yellow otherwise reads olive
    hsv = np.asarray(pic.convert("HSV"), np.float32)
    h = hsv[..., 0] * (360.0 / 255.0)
    c, half, turn = TONE_YELLOW
    hsv[..., 0] = ((h - turn * np.clip(1 - np.abs(h - c) / half, 0, 1)) % 360.0) * (255.0 / 360.0)
    pic = Image.fromarray(np.clip(hsv + 0.5, 0, 255).astype(np.uint8), "HSV").convert("RGB")
    Y = np.arange(H, dtype=np.float32) + 0.5                   # frame rows
    below = np.clip(Y - geo.y(y1) + 0.5, 0, 1)
    above = np.clip(geo.y(top) - Y + 0.5, 0, 1) if top is not None else np.zeros_like(Y)
    m = np.maximum(below, above)                               # 1 outside the panel, 0 inside, one pixel between
    p = np.asarray(pic, np.float32)
    ink = np.asarray(INK, np.float32)[None, None, :]
    shaded = p * (1 - MYTH_OUT_SHADE) + ink * MYTH_OUT_SHADE
    mask = Image.fromarray(np.clip(np.repeat(m[:, None], W, axis=1) * 255 + 0.5, 0, 255).astype(np.uint8), "L")
    return Image.fromarray(np.clip(shaded + 0.5, 0, 255).astype(np.uint8), "RGB"), mask


def _myth_compose(spec: MythFact, geo: _Geo, P: int, m_lines: list[str], f_lines: list[str], host: str | None,
                  omitted: str | None, dim: float, last_t: float, frames: int, seconds: float, tm: dict,
                  provenance: dict, Pm: int | None = None, verdict: str = "false", compact: bool = False) -> CardPlan:
    t_s, t_f, fact_starts, t_src = tm["t_s"], tm["t_f"], tm["fact_starts"], tm["t_src"]
    Pm = myth_px(P, geo.wide) if Pm is None else Pm
    # freeze panel M3: on 16:9 both kicker rules stand at R (the myth column is no longer shifted against the fact
    # column) and the taller column's block (rule top to its last line's descender) is centred on its centre
    m_rel, f_rel, F_rel, src_rel, h, R = _myth_place(P, m_lines, f_lines, host, geo.wide, Pm, compact)
    misleading = verdict == "misleading"
    bottom_zone = MYTH_BOTTOM[geo.wide]
    drift, rest = _myth_ground(spec.photo, geo, last_t, dim, R - 40, R + h + 40, opening=tm.get("panel_open"))
    M = R                               # the MYTH kicker's rule top
    card = _Card("mythfact", geo, rest)
    mx, fx = MYTH_X[geo.wide]
    m_base = [R + v for v in m_rel]
    f_base = [R + v for v in f_rel]
    F = R + F_rel
    s_base = R + src_rel
    # a "misleading" myth is labelled so and struck with a broken pen (MYTH_DASH), never drawn as a false one
    _kicker(card, "myth", MISLEADING_LABEL if misleading else "MYTH", mx, M, STONE_LT, STONE_LT, (0.20, 0.30),
            (0.20, REVEAL))
    myth_items = []
    for k, (ln, b) in enumerate(zip(m_lines, m_base)):
        it = card.text(f"myth_{k}", ln, "Anton", None, Pm, BONE, mx, b, ("ease", 0.25 + 0.08 * k, REVEAL),
                       "display", record=True)
        it["back"] = (t_f, STEP_BACK)
        myth_items.append(it)
    # the strike (freeze panel M5): one GOLD bar per line, round(0.10 x Pm) thick (6..12), its centre 0.58 of the
    # cap height above the baseline (clear of Anton's E/F/H crossbars), an INK keyline directly above and below;
    # it runs STRIKE_OVERHANG x Pm past each end of the line's ink, clamped to the zone (T1)
    th = geo.px(min(STRIKE_THICK[2], max(STRIKE_THICK[1], round(STRIKE_THICK[0] * Pm))))   # M11
    keyline = geo.px(STRIKE_KEYLINE)
    casing = 0
    cap = -get_font("Anton", geo.px(Pm)).getbbox("H", anchor="ls")[1]
    if geo.wide:
        z_left, z_right = geo.x(MYTH_X[True][0]), geo.x(fx - MYTH_DIVIDER_AIR) - geo.px(16)
    else:
        gb = graphics_box(geo.width, geo.height)
        z_left, z_right = gb[0], gb[2]
    ov = geo.px(_overhang(Pm))
    if geo.wide:
        # freeze panel M10 (3): the overhang is symmetric on 16:9 (the left clamp sits one overhang before the column)
        z_left = geo.x(MYTH_X[True][0]) - ov
    segs = []
    for it, b in zip(myth_items, m_base):
        box = it["parts"][0].box
        x0 = max(z_left, box[0] - ov)
        x1 = min(z_right, box[2] + ov)
        top_px = round(geo.y(b) - 0.58 * cap - th / 2)
        segs.append((x0, x1, top_px, top_px + th))
    starts, durs, pen_total = tm["pen"]
    dash = (geo.px(round(MYTH_DASH[0] * Pm)), geo.px(round(MYTH_DASH[1] * Pm))) if misleading else None
    strike = {"type": "strike", "name": "strike", "segs": segs, "casing": casing, "keyline": keyline, "t0": t_s,
              "names": [], "dash": dash, "back": (t_f, STEP_BACK), "fill": GOLD, "fill_back": STRIKE_BACK}
    contrast_twins = []
    for k, (it, sg) in enumerate(zip(myth_items, segs)):
        twins = []
        for pl in it["parts"]:
            tw = _twin(rest, pl, STONE_LT)
            if tw.contrast is None or tw.contrast < PLACE_TARGET or not tw.light:
                tw.light = True
                tw.solve(rest, PLACE_TARGET, min_halo=pl.halo)
            twins.append(tw)
        it["twins"] = twins
        band = (sg[2] - keyline - 2, sg[3] + keyline + 2)
        masked = _LinePlate(twins, rest, cut=[band], fallback=None)
        if masked.contrast is None or masked.contrast < PLACE_TARGET:
            raise CardRefused(f"myth_{k}'s STONE_LT twin reaches only {masked.contrast}", code="weak_twin")
        contrast_twins.append((f"myth_{k}", masked))
        name = f"strike_{k}"
        t0 = t_s + starts[k]
        card.elements.append(Element(name, "mark", "", "", None, 0, GOLD, (float(sg[0]), float(sg[2])), "", 0.0,
                                     (sg[0], sg[2], sg[1], sg[3]), round(t0, 4), round(t0 + durs[k], 4)))
        card.motion[name] = ("pen", t0, durs[k])
        card.timeline[name] = (round(t0, 4), round(t0 + durs[k], 4))
        card.roles[name] = "strike"
        strike["names"].append(name)
        card.marks.append((name, mark_mask(rest.size, lambda d, s=sg: [d.rectangle([a, s[2], b - 1, s[3] - 1],
                                                                                   fill=255)
                                                                       for a, b in _dashes(s[0], s[1], dash)])))
    card.items.append(strike)
    card.contrast = [(n, p) for n, p in card.contrast if not n.startswith("myth_") or n == "myth_label"]
    card.contrast += contrast_twins
    _kicker(card, "fact", "FACT", fx, F, GOLD, GOLD, (tm.get("fk_rule", t_f), REVEAL),
            (tm.get("fk_label", t_f), REVEAL))
    for k, (ln, b) in enumerate(zip(f_lines, f_base)):
        card.text(f"fact_{k}", ln, "Anton", None, P, BONE, fx, b, ("ease", fact_starts[k], REVEAL), "display",
                  runs=True)
    if host:
        # freeze panel M9: the SOURCE label (design chrome, like MYTH and FACT) then the signed field's host
        lpx = SOURCE_LABEL_PX[geo.wide]
        # muted, but never under the house target: on a ground where its halo cannot carry it (16:9, a dark or busy
        # photo opening under the block), it steps toward the domain's colour in 10 % steps until it reads
        for i in range(11):
            try:
                card.text("source_label", SOURCE_LABEL, "Montserrat", 800, lpx,
                          _mix(SOURCE_LABEL_FILL, SOURCE_FILL, 0.1 * i), fx, s_base, ("ease", t_src, REVEAL), "chrome",
                          track=SOURCE_LABEL_TRACK * lpx, target=SOURCE_LABEL_TARGET)
                break
            except CardRefused:
                if i == 10:
                    raise
        lab = card.elements[-1].box
        sx = (lab[2] + geo.px(SOURCE_LABEL_GAP) - geo._ox) / geo.k
        card.text("source", host, DATA_FONT, SOURCE_WEIGHT, SOURCE_PX[geo.wide], SOURCE_FILL, sx, s_base,
                  ("ease", t_src, REVEAL), "data", target=SOURCE_TARGET)
    div_fill = None
    if geo.wide:
        low = max(m_base[-1], s_base if host else f_base[-1])
        div = (fx - MYTH_DIVIDER_AIR, R, fx - MYTH_DIVIDER_AIR + 2, low + 16)
        div_fill = _divider_fill(card, div)
        # freeze panel M8: the divider wipes down as the pen starts, not before the myth is read
        card.rect("divider", div, div_fill, ("ease", t_s + STRIKE_PEN - 0.10, 0.40), "y", role="divider")
    bottom = max(e.box[3] for e in card.elements)
    if bottom > geo.y(bottom_zone):
        raise CardRefused(f"the myth card's last ink at {bottom} runs past y {bottom_zone} (the caption band)")
    anchors_m: dict[str, Any] = {"R": R, "F": F, "myth_baselines": m_base, "fact_baselines": f_base, "px": P,
                                 "myth_px": Pm, "ground_dim": dim, "M": M, "verdict": verdict,
                                 "strike": "dashed" if misleading else "solid", "compact": compact,
                                 "block": (R, R + h), "pen_total": pen_total, "panel_open": tm.get("panel_open")}
    if div_fill is not None:
        anchors_m["divider_fill"] = brand.hex_colour(div_fill)
    meta = {"anchors": anchors_m, "provenance": provenance,
            "never_first": True, "min_seconds": round(seconds, 6), "source_omitted": omitted,
            "quality_notes": list(tm.get("notes") or []), "source_host_raw": tm.get("raw_host"),
            "t_s": round(t_s, 6), "t_f": round(t_f, 6), "rest_m": round(tm["rest_m"], 6),
            "rest_f": round(tm["rest_f"], 6), "rest_s": tm["rest_f"], "pen_total": pen_total,
            "_r": {"drift": drift, "items": card.items, "last_t": last_t, "rest": rest},
            "_contrast": card.contrast, "_marks": card.marks}
    return _marks_ok(card.plan("mythfact", seconds, frames, (spec.myth_fact_id, spec.fact_id), meta))


MISLEADING_LABEL = "MISLEADING"
MYTH_DASH = (0.42, 0.20)    # x the myth's px: a misleading myth's strike, dash and gap (a false one is one bar)


def _dashes(x0: int, x1: int, dash: tuple[int, int] | None, end: int | None = None) -> list[tuple[int, int]]:
    """The spans [a, b) a strike from x0 to x1 is drawn in: one span, or dashes of dash[0] with dash[1] gaps laid
    over the whole bar (x0..`end`, default x1; a last dash under half its length is dropped), cut at x1."""
    if x1 <= x0:
        return []
    if not dash:
        return [(x0, x1)]
    on, off = max(2, dash[0]), max(2, dash[1])
    end = x1 if end is None else end
    out, a = [], x0
    while a < end:
        b = min(end, a + on)
        if b - a >= on / 2 or not out:
            out.append((a, b))
        a += on + off
    return [(a, min(b, x1)) for a, b in out if a < x1]


def strike_progress(plan: CardPlan, t: float, name: str | None = None) -> float:
    """The myth card's strike, 0..1: the drawn share of every bar's length (freeze panel M6: one stroke per line,
    _ease_io on its own window), or of the bar `name` only."""
    motion = plan.meta["_motion"]
    bars = [e for e in plan.elements if e.name.startswith("strike_") and (name is None or e.name == name)]
    total = sum(e.box[2] - e.box[0] for e in bars)
    return sum((e.box[2] - e.box[0]) * _alpha(motion[e.name], t) for e in bars) / max(1e-9, total)


# ------------------------------------------------------------------------------------------------ quote card
def _qnorm(s: str) -> str:
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("„", '"'),
                 ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    return " ".join(s.split())


def _quote_pairs(q: str) -> list[tuple[int, int]]:
    marks = [i for i, c in enumerate(q) if c == '"']
    return list(zip(marks[0::2], marks[1::2]))


def _verbatim_pairs(text: str, quote: str) -> list[int]:
    """The indexes of the quotation-mark pairs of `quote` (normalised) inside which `text` stands as a whole
    quotation: from the opening mark or a sentence start, to the closing mark or a sentence end."""
    t, q = _qnorm(text), _qnorm(quote)
    if not t or '"' in t:
        return []
    pairs = _quote_pairs(q)
    hits: list[int] = []
    start = q.find(t)
    while start != -1:
        end = start + len(t)
        for k, (o, c) in enumerate(pairs):
            if o < start and end <= c:
                starts = start == o + 1 or (start - 2 > o and re.fullmatch(r"[.;:!?] ", q[start - 2:start]))
                ends = end == c or t[-1] in ".!?"
                if starts and ends and k not in hits:
                    hits.append(k)
        start = q.find(t, start + 1)
    return hits


def _verbatim(text: str, quote: str) -> bool:
    return bool(_verbatim_pairs(text, quote))


# ---- the speaker of one quotation (round 2 review 3: any word outside the marks passed as the speaker, so
# Petrie's words could be credited to Smith from 'Petrie wrote: "..." Smith said "..."')
SPEECH_VERBS = frozenset((
    "said", "says", "say", "wrote", "writes", "write", "noted", "notes", "added", "adds", "recalled", "recalls",
    "explained", "explains", "observed", "observes", "remarked", "remarks", "declared", "declares", "told", "tells",
    "asked", "asks", "replied", "replies", "stated", "states", "insisted", "argued", "argues", "claimed", "claims",
    "reported", "reports", "described", "describes", "exclaimed", "commented", "comments", "concluded",
    "continued", "answered", "warned", "admitted", "according", "recorded", "records", "put"))
_PRONOUNS = frozenset(("he", "she", "they", "him", "her", "his"))
# Words that make an attribution clause refuse outright, wherever they stand in it (round 3 review 3): a negation
# ("Petrie never said", "Neither Petrie nor Smith wrote", "Petrie denied ever writing") or a second-hand credit
# ("Smith quoted a letter to Petrie", "falsely attributed to Petrie", "Smith said, quoting Petrie").
ATTRIBUTION_NEGATORS = frozenset((
    "not", "never", "nor", "no", "neither", "none", "nobody", "denied", "denies", "deny", "denying", "disputed",
    "disputes", "doubted", "doubts", "false", "falsely", "wrongly", "mistakenly", "untrue", "myth"))
SECOND_HAND = frozenset((
    "quoting", "quoted", "quotes", "quote", "citing", "cited", "cites", "attributed", "attributes", "attributing",
    "misattributed", "paraphrasing", "paraphrased", "reportedly", "allegedly", "supposedly", "apocryphal",
    "apocryphally", "translated", "translating"))
FORGERY_WORDS = frozenset((
    "forger", "forgers", "forged", "forgery", "forgeries", "fake", "faked", "spurious", "pseudonym", "signing",
    "signed", "impostor", "imposter", "hoax", "fabricated", "invented", "doubtful", "dubious"))
# Words between a speaker and the speech verb that leave the speaker its subject ("Smith later wrote")
SPEECH_ADVERBS = frozenset((
    "later", "also", "once", "then", "famously", "first", "again", "afterwards", "simply", "memorably", "still",
    "thus", "now", "himself", "herself", "privately", "publicly", "plainly", "frankly", "finally", "already"))
_NOT_NAMES = _STOP | SPEECH_VERBS | SPEECH_ADVERBS | ATTRIBUTION_NEGATORS | SECOND_HAND | frozenset((
    "according", "unlike", "like", "however", "yet", "after", "before", "while", "when", "in", "on", "as", "if",
    "mr", "mrs", "ms", "dr", "sir", "lady", "lord", "professor", "prof", "dame", "st"))
_NAME_PARTICLES = frozenset(("de", "da", "di", "du", "van", "von", "der", "den", "la", "le", "al", "bin", "ibn"))
_ATTR_TOK = re.compile(r"[A-Za-z][A-Za-z'.-]*|[,:;]")
_NAME_TOK = re.compile(r"[A-Za-z][A-Za-z'.-]*")


def _lead_in(q: str, pairs: list[tuple[int, int]], k: int) -> tuple[int, str]:
    """(offset, text) of the clause before pair k's opening mark: back to the previous closing mark or sentence
    end."""
    o = pairs[k][0]
    start = pairs[k - 1][1] + 1 if k > 0 else 0
    seg = q[start:o]
    cut = max((m.end() for m in re.finditer(r"[.;!?](?:\s|$)", seg)), default=0)
    lead = seg[cut:]
    pad = len(lead) - len(lead.lstrip())
    return start + cut + pad, lead.strip()


def _after(q: str, pairs: list[tuple[int, int]], k: int) -> tuple[int, str, str]:
    """(offset, text, sentence) of the clause after pair k's closing mark, up to the next , ; . ! ? or quotation
    mark ('"...," said X'), and of the rest of its sentence (scanned for a negator or a second-hand credit:
    '"...," Smith said, quoting Petrie')."""
    c = pairs[k][1]
    end = pairs[k + 1][0] if k + 1 < len(pairs) else len(q)
    raw = q[c + 1:end]
    seg = raw.lstrip(" ,")
    m = re.match(r"[^,;.!?\"]*", seg)
    sm = re.match(r"[^.!?\"]*", seg)
    return c + 1 + len(raw) - len(seg), (m.group(0) if m else "").strip(), (sm.group(0) if sm else "").strip()


def _is_name(tok: str) -> bool:
    return tok[:1].isupper() and tok.strip(".").lower() not in _NOT_NAMES and tok.lower() not in _PRONOUNS


def _name_run(toks: Sequence[str], i: int, step: int) -> list[str]:
    """The name that ends at toks[i] (step -1, read backwards) or starts there (step +1): capitalised name tokens
    and the particles between them ("Gertrude Caton-Thompson", "Howard Vyse", "Ludwig von Wiesel")."""
    run: list[str] = []
    j = i
    while 0 <= j < len(toks):
        w = toks[j]
        if not (_is_name(w) or (w.lower() in _NAME_PARTICLES and run)):
            break
        run.append(w)
        j += step
    if step < 0:
        run.reverse()
    while run and run[0].lower() in _NAME_PARTICLES:
        run.pop(0)
    while run and run[-1].lower() in _NAME_PARTICLES:
        run.pop()
    return run


def _nearest_name(q: str, end: int) -> list[str]:
    """The last name in `q[:end]` outside every quotation: who a pronoun refers to (round 3 review 3: "Petrie
    wrote "A." Smith disagreed. Later he said "B."" makes "he" Smith, not the previous quotation's speaker)."""
    toks = _ATTR_TOK.findall(_unquoted(q[:end]))
    for i in range(len(toks) - 1, -1, -1):
        if _is_name(toks[i]):
            return _name_run(toks, i, -1)
    return []


def _subject(q: str, offset: int, clause: str, lead: bool, scope: str = "") -> tuple[list[str] | None, str]:
    """(the speaker's name tokens, why not) for one attribution clause: the grammatical subject of its speech
    verb, either the name right before the verb (past adverbs and a ", ... ," aside: "Smith, mocking Petrie,
    wrote") or the name right after it ("said Smith"); "according to X" names X; a pronoun is the nearest name
    before it; a lead-in that is only a name and a colon ("Petrie:") names that. A negator or a second-hand
    credit anywhere in the clause refuses."""
    spans = [m.start() for m in _ATTR_TOK.finditer(clause)]
    toks = _ATTR_TOK.findall(clause)
    low = [w.lower().strip(".") for w in toks]
    scan = [w.lower().strip(".") for w in _ATTR_TOK.findall(scope or clause)]
    neg = [w for w in scan if w in ATTRIBUTION_NEGATORS or w.endswith("n't")]
    if neg:
        return None, f"the attribution {scope or clause!r} negates the credit ({neg[0]!r})"
    second = [w for w in scan if w in SECOND_HAND]
    if second:
        return None, f"the attribution {scope or clause!r} credits the words second-hand ({second[0]!r})"
    verbs = [i for i, w in enumerate(low) if w in SPEECH_VERBS and w != "according"]
    if not verbs:
        if "according" in low:
            i = low.index("according")
            if i + 1 < len(low) and low[i + 1] == "to":
                run = _name_run(toks, i + 2, 1)
                if run:
                    return run, ""
        words = [w for w in toks if w not in (",", ":", ";")]
        if lead and clause.rstrip().endswith(":") and words and _name_run(toks, len(toks) - 2, -1) == words:
            return words, ""
        return None, f"the attribution {clause!r} has no speech verb"
    v = verbs[-1] if lead else verbs[0]
    j = v - 1
    while j >= 0 and low[j] in SPEECH_ADVERBS:
        j -= 1
    if j >= 0 and toks[j] == ",":
        p = max((i for i in range(j) if toks[i] == ","), default=-1)
        if p <= 0:
            return None, f"the attribution {clause!r} names no subject for {toks[v]!r}"
        j = p - 1
    if j >= 0 and low[j] in _PRONOUNS:
        run = _nearest_name(q, offset + spans[j])
        return (run or None), ("" if run else f"the attribution {clause!r} has a pronoun with no name before it")
    if j >= 0 and _is_name(toks[j]):
        return _name_run(toks, j, -1), ""
    if j < 0:
        k = v + 1
        while k < len(toks) and low[k] in SPEECH_ADVERBS:
            k += 1
        run = _name_run(toks, k, 1) if k < len(toks) else []
        if run:
            return run, ""
        if k < len(toks) and low[k] in _PRONOUNS:
            run = _nearest_name(q, offset)
            if run:
                return run, ""
    return None, f"the attribution {clause!r} names no subject for {toks[v]!r}"


def _attribution(q: str, pairs: list[tuple[int, int]], k: int) -> tuple[str, list[str] | None, str] | None:
    """(clause, the speaker it names, why not) for the quotation in pair k: the lead-in before its opening mark
    when it attributes (carries a speech verb, "according to", or ends on a colon), else a '"...," said X' clause
    after its closing mark. None when no clause next to the marks attributes it."""
    offset, clause = _lead_in(q, pairs, k)
    a_offset, a_clause, a_sentence = _after(q, pairs, k)
    for lead, off, cl, scope in ((True, offset, clause, clause), (False, a_offset, a_clause, a_sentence)):
        if not cl or not _is_attribution(cl):
            continue
        run, why = _subject(q, off, cl, lead, scope)
        return cl, run, why
    return None


def _is_attribution(clause: str) -> bool:
    words = [w.lower() for w in _NAME_TOK.findall(clause)]
    return clause.rstrip().endswith((":", ",")) or any(w in SPEECH_VERBS for w in words)


def _speaker_ok(speaker: str, clause: str, run: Sequence[str] | None, why: str = "") -> str | None:
    """None when every name token of `speaker` stands in `run`, the subject the attribution `clause` names; else
    the reason, quoting the clause it checked (round 3 review 7)."""
    named = [t.strip(".") for t in _NAME_TOK.findall(speaker) if t.strip(".").lower() not in _STOP]
    if not named:
        return "the speaker names no one"
    for tok in named:
        if tok.lower() in SPEECH_VERBS:
            return f"the speaker's {tok!r} is not a name"
    if run is None:
        return why or f"the attribution {clause!r} names no speaker"
    have = {w.strip(".").lower() for w in run}
    for tok in named:
        if tok.lower() not in have:
            return (f"the attribution {clause!r} credits {' '.join(run)!r}, and {tok!r} is not in that name"
                    if run else f"the attribution {clause!r} names no speaker")
    return None


def _unquoted(q: str) -> str:
    """`q` (normalised) without its quoted passages: what lies outside every pair of double quotation marks (an
    unclosed mark hides the rest)."""
    out, inside = [], False
    for piece in q.split('"'):
        if not inside:
            out.append(piece)
        inside = not inside
    return " ".join(" ".join(out).split())


def _whole(phrase: str, text: str) -> bool:
    return bool(phrase) and re.search(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", text,
                                      re.IGNORECASE) is not None


def quote_attribution_problem(quotation: str, speaker: str, evidence: str) -> str | None:
    """Why `evidence` does not show `speaker` saying `quotation`, or None: the quotation must be the whole text
    between one pair of the evidence's quotation marks, the attribution next to those marks must name the speaker
    (every name token), and no word of doubt, denial, second-hand credit or forgery may stand outside the marks.
    card_fields runs it when a quote field is made and checked; the quote card runs it again as a guard."""
    qn = _qnorm(str(evidence or ""))
    pairs = _quote_pairs(qn)
    hits = [k for k, (o, c) in enumerate(pairs) if qn[o + 1:c].strip() == _qnorm(quotation).strip()]
    if not hits:
        return "the quotation is not the whole text between one pair of the evidence's quotation marks"
    speaker = " ".join(str(speaker or "").split())
    why = f"speaker {speaker!r}: the evidence names no speaker next to the quotation"
    for k in hits:
        found = _attribution(qn, pairs, k)
        if found is None:
            continue
        clause, run, w = found
        bad = _speaker_ok(speaker, clause, run, w)
        if bad is None:
            why = None
            break
        why = f"speaker {speaker!r}: {bad}"
    if why:
        return why
    doubt = sorted({w for w in re.findall(r"[a-z]+", _unquoted(qn).lower())
                    if w in ATTRIBUTION_NEGATORS | SECOND_HAND | FORGERY_WORDS})
    if doubt:
        return f"speaker {speaker!r}: the evidence's prose doubts or relays the credit ({', '.join(doubt)})"
    return None


# ---- the quote's role, work and date (review 2026-11-03, blocking 2): each is tied to the clause that attributes
# THIS quotation to its speaker, never only found somewhere in the evidence ('Monod wrote: "..." The geologist
# Jean Smith agreed' gave Monod Smith's role; "Smith's Desert Atlas" became Monod's work; "Monod, born in 1902,
# wrote" made 1902 the quotation's date).
LIFE_WORDS = frozenset(("born", "died", "dies", "birth", "death", "aged", "age", "b", "d", "until", "since", "lived",
                        "living", "buried", "married", "retired", "graduated", "joined"))
_FOLLOW_UP = re.compile(r"\b(?:this|that|these\s+words|those\s+words|the\s+words|so)\b", re.IGNORECASE)


def _citation_scopes(quotation: str, speaker: str, evidence: str) -> list[tuple[str, list[str]]]:
    """(clause, the speaker's name tokens as written in it) for every clause that attributes `quotation` to
    `speaker` in its own words: the attribution next to the marks (its whole sentence for a '"...," said X'
    clause) and a following sentence whose subject is the speaker and that points back at the words ("Petrie, the
    Egyptologist, wrote this in ... in 1883"). A pronoun subject ("Later he said") names nobody in the clause."""
    qn = _qnorm(str(evidence or ""))
    pairs = _quote_pairs(qn)
    speaker = " ".join(str(speaker or "").split())
    out: list[tuple[str, list[str]]] = []
    for k, (o, c) in enumerate(pairs):
        if qn[o + 1:c].strip() != _qnorm(quotation).strip():
            continue
        found = _attribution(qn, pairs, k)
        if found is None or _speaker_ok(speaker, *found) is not None:
            continue
        clause, run, _ = found
        _, lead = _lead_in(qn, pairs, k)
        a_off, a_clause, a_sentence = _after(qn, pairs, k)
        after = clause == a_clause and clause != lead
        out.append((a_sentence if after else clause, list(run or [])))
        if not after and a_sentence and _FOLLOW_UP.search(a_sentence) and _is_attribution(a_sentence):
            run2, _w = _subject(qn, a_off, a_sentence, True, a_sentence)
            if run2 and _speaker_ok(speaker, a_sentence, run2) is None:
                out.append((a_sentence, list(run2)))
    return out


def _aside_spans(clause: str, run: Sequence[str]) -> list[tuple[int, int]]:
    """Character spans of the ", ... ," asides right after the speaker's name in `clause` ("Monod, born in 1902,
    wrote"): what describes the person, never the words."""
    if not run:
        return []
    m = re.search(r"(?<![A-Za-z])" + r"\s+".join(re.escape(t) for t in run) + r"(?![A-Za-z])", clause)
    if not m:
        return []
    a = re.match(r"\s*,([^,:;]*),", clause[m.end():])
    return [(m.end(), m.end() + a.end())] if a else []


def _role_tied(role: str, clause: str, run: Sequence[str]) -> bool:
    """The role names the speaker: right before the name ("the geologist Theodore Monod") or in the aside right
    after it ("Petrie, the Egyptologist, wrote")."""
    if not run:
        return False
    name = r"\s+".join(re.escape(t) for t in run)
    r = re.escape(" ".join(role.split())).replace(r"\ ", r"\s+")
    art = r"(?:(?:the|a|an)\s+)?"
    return bool(re.search(r"(?<![A-Za-z])" + art + r + r"\s*,?\s+" + name + r"(?![A-Za-z])", clause, re.IGNORECASE)
                or re.search(r"(?<![A-Za-z])" + name + r"\s*,\s*" + art + r + r"\s*(?:,|:|$)", clause, re.IGNORECASE))


def _work_tied(work: str, clause: str, run: Sequence[str]) -> bool:
    """The work stands whole in the attribution and is not someone else's ("Smith's Desert Atlas")."""
    w = re.escape(" ".join(work.split())).replace(r"\ ", r"\s+")
    own = {t.lower() for t in run} | {"his", "her", "their"}
    for m in re.finditer(r"(?<![A-Za-z0-9])" + w + r"(?![A-Za-z0-9])", clause, re.IGNORECASE):
        pos = re.search(r"([A-Za-z.-]+)(?:'s|')\s+$", clause[:m.start()])
        if pos and pos.group(1).lower() not in own:
            continue
        return True
    return False


def _date_tied(date: str, clause: str, run: Sequence[str]) -> bool:
    """Every number of the date stands in the attribution, outside the aside about the speaker and not after a
    word of a life ("born in 1902")."""
    want = _nums(date)
    if not want:
        return False
    asides = _aside_spans(clause, run)
    for n in want:
        ok = False
        for m in re.finditer(r"(?<![\d,.])" + re.escape(n) + r"(?![\d])", clause):
            if any(a <= m.start() < b for a, b in asides):
                continue
            before = [w.lower().strip(".") for w in re.findall(r"[A-Za-z.]+", clause[:m.start()])][-3:]
            if set(before) & LIFE_WORDS:
                continue
            ok = True
            break
        if not ok:
            return False
    return True


def quote_citation_problem(quotation: str, speaker: str, role: str | None, work: str | None, date: str | None,
                           evidence: str) -> str | None:
    """Why the quote's role, work or date is not tied to the clause of `evidence` that attributes `quotation` to
    `speaker`, or None (each that is empty passes). card_fields runs it when the field is made and checked; the
    quote card runs it again."""
    role, work, date = (" ".join(str(v or "").split()) for v in (role, work, date))
    if not (role or work or date):
        return None
    scopes = _citation_scopes(quotation, speaker, evidence)
    if not scopes:
        return f"speaker {speaker!r}: no attribution clause to tie the citation to"
    for label, v, tied in (("role", role, _role_tied), ("work", work, _work_tied), ("date", date, _date_tied)):
        if v and not any(tied(v, cl, run) for cl, run in scopes):
            return (f"the {label} {v!r} is not tied to the clause that attributes the quotation to {speaker!r} "
                    f"({'; '.join(repr(cl) for cl, _ in scopes)})")
    return None


def _quote_gates(spec: QuoteCard, sheet: dict, wide: bool) -> tuple[str, dict, dict]:
    """(quotation, its fact, the checked quote field) for a quote card; CardRefused otherwise."""
    f = card_field(sheet, spec.quote_src, "quotes")
    text = " ".join(f["quotation"].split())
    _expect(spec.text, text, "quotation")
    _expect(spec.speaker, f["speaker"]["name"], "speaker")
    _expect(spec.role, f["speaker"].get("role"), "role")
    _expect(spec.work, f.get("work"), "work")
    if spec.fact_id and spec.fact_id != f["fact_id"]:
        raise CardRefused(f"the fact {spec.fact_id!r} is not the quote field's fact {f['fact_id']!r}", code="bad_field")
    if spec.year is not None and str(int(spec.year)) not in str(f.get("date") or ""):
        raise CardRefused(f"the year {spec.year} is not the quote field's date {f.get('date')!r}",
                          code="wrong_citation")
    if not text:
        raise CardRefused("an empty quote")
    if "…" in text or "..." in text or "[" in text or "]" in text:
        raise CardRefused("a quote is drawn whole: no elision, no editorial brackets")
    if not wide and (len(text.split()) > 25 or len(text) > 140):
        raise CardRefused(f"the quote runs {len(text.split())} words / {len(text)} characters (25 / 140)")
    if wide and len(text) > 170:
        raise CardRefused(f"the quote runs {len(text)} characters (170)")
    speaker = " ".join(str(f["speaker"]["name"]).split())
    if not speaker or len(speaker) > 28:
        raise CardRefused("a speaker of 1-28 characters is required")
    fact = fact_by_id(sheet, f["fact_id"])
    if fact["disputed"]:
        raise CardRefused("a disputed fact is not quoted")
    # the guards again, at render time (card_fields.findings ran them when the field was checked)
    e = next((x for x in fact["evidence"] if isinstance(x, dict) and _qnorm(str(x.get("quote") or ""))
              == _qnorm(f["evidence"])), None)
    if e is None or int(e.get("tier", 9)) > 2:
        raise CardRefused("the quote's evidence is not tier 1-2 evidence of its fact", code="weak_source")
    why = quote_attribution_problem(text, speaker, str(e.get("quote") or ""))
    if why:
        raise CardRefused(why, code="prose_contradicts" if "doubts" in why else "wrong_speaker")
    why = quote_citation_problem(text, speaker, f["speaker"].get("role"), f.get("work"), f.get("date"),
                                 str(e.get("quote") or ""))
    if why:
        raise CardRefused(why, code="wrong_citation")
    return text, fact, f


CITE_STEP = {False: 40, True: 38}
SPEAKER_STEP = {False: 52, True: 44}  # design px between two speaker lines (9:16 40 px, 16:9 34 px Montserrat 800)
SPEAKER_TRACKS = (1.0, 0.67)          # the speaker's tracking, as a share of its house tracking, tried in turn


def _speaker_lines(speaker: str, font: Any, track: float, k: float, measure: float
                   ) -> tuple[list[str] | None, float]:
    """The speaker in one line or two at the kicker size (it is already at the kicker floor, round 3 review 6:
    "Gertrude Caton-Thompson" refused the 9:16 card), by a stated rule: one line at the house tracking; one line at
    two thirds of it; two lines at the house tracking, broken at the space (or a hyphen, kept on the first line)
    that leaves the longer line shortest. (None, track) when none fits."""
    def fits(t: str, tr: float) -> bool:
        return _tracked_width(t, font, tr * k) <= measure
    for share in SPEAKER_TRACKS:
        if fits(speaker, track * share):
            return [speaker], track * share
    best = None
    for m in re.finditer(r" |-", speaker):
        a = speaker[:m.end()].strip() if m.group(0) == "-" else speaker[:m.start()]
        b = speaker[m.end():].strip()
        if a and b and fits(a, track) and fits(b, track):
            score = max(_tracked_width(a, font, track * k), _tracked_width(b, font, track * k))
            if best is None or score < best[0]:
                best = (score, [a, b])
    return (best[1] if best else None), track     # design px between two citation lines (9:16 30 px, 16:9 28 px Plex Mono)


def _citation_lines(parts: Sequence[str], host: str | None, font: Any, measure: int) -> list[str] | None:
    """The citation (role · work · year · source host), upper case, in at most two lines, by a stated rule
    (round 2 review 8: a long work title refused the whole 9:16 card): one line with the host; one line without
    it; two lines broken at a " · " (with the host, then without); two lines broken between words (with, then
    without). Nothing is ever cut or elided. [] when there is nothing to cite, None when nothing fits."""
    def fits(line: str) -> bool:
        return _width(line, font) <= measure

    def bits(with_host: bool) -> list[str]:
        return [str(b).strip().upper() for b in list(parts) + ([host] if (with_host and host) else [])
                if str(b or "").strip()]
    if not bits(False) and not host:
        return []
    for with_host in (True, False):
        b = bits(with_host)
        if b and fits(" · ".join(b)):
            return [" · ".join(b)]
    for with_host in (True, False):
        b = bits(with_host)
        best = None
        for k in range(1, len(b)):
            pair = [" · ".join(b[:k]), " · ".join(b[k:])]
            if all(fits(x) for x in pair):
                score = max(_width(x, font) for x in pair)
                if best is None or score < best[0]:
                    best = (score, pair)
        if best:
            return best[1]
    for with_host in (True, False):
        words = " · ".join(bits(with_host)).split(" ")
        best = None
        for k in range(1, len(words)):
            a = " ".join(words[:k]).removesuffix(" ·")
            c = " ".join(words[k:]).removeprefix("· ")
            if a in ("", "·") or c in ("", "·"):
                continue
            if fits(a) and fits(c):
                score = max(_width(a, font), _width(c, font))
                if best is None or score < best[0]:
                    best = (score, [a, c])
        if best:
            return best[1]
    return None


QUOTE_SIZES_TALL = (104, 96, 88, 80, 76, 72, 68, 64)   # review 2026-11-03 (4): the 9:16 floor is 64 px
# Round 4 review 2: a short quote ("Empty.") sat small in a nearly empty frame at both sizes, and the 16:9 quote
# stayed at one 56 px size in its column. Both now take the largest size that fits; above QUOTE_SHORT_PX a quote
# may take at most QUOTE_SHORT_LINES lines, so the large sizes are for short quotes only.
# review 2026-11-03 (second round, 3): a short 16:9 quote ("Not a crater.") sat at 96 px in a frame two thirds
# empty; the large sizes (over QUOTE_SHORT_PX, so at most QUOTE_SHORT_LINES lines) now run to 144 over a column
# that runs to title-safe's right
QUOTE_SIZES_WIDE = (144, 128, 112, 104, 96, 88, 80, 72, 64, 56)
QUOTE_BLUR = 36                    # design px (the myth's): the photo's shapes and colour, never its burned-in words
QUOTE_INNER = (0.05, 0.21)         # the toned ground's brightness behind the block
QUOTE_ABOVE = (0.07, 0.30)         # above it (barely lifted: the frame darkens from the top, no band across it)
QUOTE_BELOW = (0.17, 0.62)         # below it, on a long ease (the photo's colour comes up under the caption band)
QUOTE_FEATHER = (300, 640)
QUOTE_SHORT_PX = {False: 76, True: 72}
QUOTE_SHORT_LINES = 3
QUOTE_WIDE_ROOM = 2 * (475 - 160)   # the 16:9 block is centred on y 475 and never starts above y 160
QUOTE_LINES_LARGE = 6      # lines a 9:16 quote may take at or under QUOTE_SHORT_PX
QUOTE_TALL_TOP = 300       # design px: the 9:16 block is centred on y 694, never starts above y 300 nor ends past 1058
QUOTE_TALL_ROOM = 1058 - QUOTE_TALL_TOP
QUOTE_STEP = 1.28          # x P: the 9:16 quote's line step (1.33 left a six-line quote no room at 64 px)


def _quote_tall_height(P: int, n: int, n_sp: int, n_ci: int) -> int:
    """The 9:16 quote block's height (as plan_quote_card lays it out) for n quote lines at P px."""
    return (166 + round(QUOTE_STEP * P) * (n - 1) + 57 + 47 + SPEAKER_STEP[False] * (n_sp - 1) + (50 if n_ci else 0)
            + CITE_STEP[False] * max(0, n_ci - 1))


def _quote_wide_step(P: int) -> int:
    return 74 if P <= 56 else round(1.32 * P)       # 56 px keeps its 74 px step


def _quote_wide_height(P: int, n: int, sp_extra: int, n_ci: int) -> int:
    """The 16:9 quote block's height for n quote lines at P px (to the data line's descender)."""
    return (24 + P + _quote_wide_step(P) * (n - 1) + 50 + 42 + sp_extra + (44 if n_ci else 0)
            + CITE_STEP[True] * max(0, n_ci - 1) + 12)


def plan_quote_card(spec: QuoteCard, sheet: dict, width: int = 1080, height: int = 1920, *,
                    payload: Mapping[str, Any] | None = None) -> CardPlan:
    """The quote card (rule B: the quote and its speaker come from the fact sheet's structure). `payload` is
    accepted for a uniform signature; a quote never needs the fact gate's record.

    Held (operator decision B, 2026-11-03): while config.QUOTE_CARDS_ENABLED is false (the default) every quote card
    refuses with reason "held", before anything is read. It must not be turned on until fact-check records are signed
    (FACT_RECORD_KEY) and a real checker key exists; the speaker, work and date must then come only from the
    checker's signed record, the evidence's prose parsed only to refuse (the quote guard below stays as it is)."""
    from . import config
    if not getattr(config, "QUOTE_CARDS_ENABLED", False):
        raise CardRefused("held", code="held")
    geo = _Geo(width, height)
    text, _, qf = _quote_gates(spec, sheet, geo.wide)
    body = typeset(text) + "”"
    _glyphs_ok(body, "Montserrat", 600)
    _glyphs_ok("“", "Montserrat", 600)   # both marks one face and one GOLD (review 2026-11-03, second round)
    # the 44 px GOLD rule above it is the attribution's dash (round 1 review 9: "— " under the rule read as two
    # stacked dashes), so the speaker is drawn bare
    speaker = " ".join(qf["speaker"]["name"].split()).upper()
    _glyphs_ok(speaker, "Montserrat", 800)
    host = display_host((urlparse(str(qf["source_url"])).hostname or "").lower()).upper() or None
    parts = [qf["speaker"].get("role") or "", qf.get("work") or "", typeset(str(qf.get("date") or ""))]
    if geo.wide:
        mark_px, x_mark, x_text, measure, sizes, max_lines = 200, 153, 300, 1440, QUOTE_SIZES_WIDE, 5
        sp_px, sp_track = 34, 5
        ci_px = 28
    else:
        # round 4 (H): the 9:16 quote takes the largest size whose block still fits the text zone (y 340-1058)
        mark_px, x_mark, x_text, measure, sizes, max_lines = 240, 100, 100, 760, QUOTE_SIZES_TALL, 5
        sp_px, sp_track = 40, 6
        ci_px = 30
    f_ci = get_font(DATA_FONT, geo.px(ci_px), weight=500)
    f_sp = get_font("Montserrat", geo.px(sp_px), weight=800)
    sp_lines, sp_track = _speaker_lines(speaker, f_sp, sp_track, geo.k, geo.px(measure))
    if sp_lines is None:
        raise CardRefused("the speaker does not fit two lines at the kicker floor")
    sp_step = SPEAKER_STEP[geo.wide]
    sp_extra = sp_step * (len(sp_lines) - 1)
    cite = _citation_lines(parts, host, get_font(DATA_FONT, ci_px, weight=500), measure)
    if cite is not None and geo.k != 1 and any(_width(c, f_ci) > geo.px(measure) for c in cite):
        cite = _citation_lines(parts, host, f_ci, geo.px(measure))
    if cite is None:
        raise CardRefused("the citation does not fit two lines, even without its source")
    for c in cite:
        _glyphs_ok(c, DATA_FONT, 500)
    citation = cite[0] if cite else ""
    # review 2026-11-03 (4): the largest size that fits, full stop. A smaller size is never taken to save a line
    # (the 25 % "unwrap" rule set a 75-character quote at 58 px); an extra line is preferred, down to the floor.
    fit = None
    for P in sizes:
        cap_lines = max_lines if geo.wide else QUOTE_LINES_LARGE
        if P > QUOTE_SHORT_PX[geo.wide]:
            cap_lines = QUOTE_SHORT_LINES
        lines = _design_wrap(body, "Montserrat", 600, P, measure, cap_lines, geo)
        if not lines:
            continue
        if geo.wide:
            room = _quote_wide_height(P, len(lines), sp_extra, len(cite)) <= QUOTE_WIDE_ROOM
        else:
            room = _quote_tall_height(P, len(lines), len(sp_lines), len(cite)) <= QUOTE_TALL_ROOM
        if room:
            fit = (P, lines)
            break
    if fit is None:
        raise CardRefused("the quote does not fit its lines at the floor size")
    P, lines = fit
    n = len(lines)
    seconds = max(MIN_SECONDS, 1.00 + max(2.5, _read(text)) + TRANS_DUR)
    frames, seconds = _frames(seconds)
    if seconds > MAX_SECONDS:
        raise CardRefused(f"the quote card would run {seconds:.2f} s")
    last_t = (frames - 1) / VIDEO_FPS
    if geo.wide:
        step = _quote_wide_step(P)
        ci_step = CITE_STEP[True]
        H = _quote_wide_height(P, n, sp_extra, len(cite))
        M = max(160, round(475 - H / 2))
        first, r_drop, sp_drop, ci_drop = M + 24 + P, 50, 42, 44
    else:
        step = round(QUOTE_STEP * P)
        ci_step = CITE_STEP[False]
        H = (166 + step * (n - 1) + 57 + 47 + sp_extra + (50 if citation else 0)
             + ci_step * max(0, len(cite) - 1))
        M = max(QUOTE_TALL_TOP, min(round(694 - H / 2), 1058 - H))
        first, r_drop, sp_drop, ci_drop = M + 166, 57, 47, 50
    # review 2026-11-03 (second round, 3): the quote's ground is the photo toned, dark behind the block and opening
    # around it (it read as one flat dark grey on every photo)
    ground = toned_ground(spec.photo, geo, QUOTE_BLUR, band_levels(geo, M - 40, M + H + 40, QUOTE_INNER, QUOTE_ABOVE,
                                                                   QUOTE_BELOW, *QUOTE_FEATHER))
    drift = GroundDrift(ground)
    rest = drift(last_t)
    card = _Card("quote", geo, rest)
    if geo.wide:      # the hanging mark scales with a large short quote (200 px sat under 144 px type)
        mark_px = max(mark_px, 2 * P)
    f_mark = get_font("Montserrat", geo.px(mark_px), weight=600)
    ink = _ink("“", f_mark)
    mark_base = geo.y(M) - ink[1]
    mark_x = geo.x(x_mark)
    card.text("quote_mark", "“", "Montserrat", 600, mark_px, GOLD, (mark_x - geo._ox) / geo.k,
              (mark_base - geo._oy) / geo.k, ("ease", 0.20, REVEAL), "mark", rise=False, settle=(0.20, FIGURE_SETTLE),
              record=False, target=NONTEXT_MIN, pivot_top=True, kind="mark")
    qp = card.items[-1]["parts"][0]
    card.marks.append(("quote_mark", _plate_mask(qp, rest.size)))
    baselines = [first + k * step for k in range(n)]
    for k, (ln, b) in enumerate(zip(lines, baselines)):
        card.text(f"quote_{k}", ln, "Montserrat", 600, P, BONE, x_text, b, ("ease", 0.40, REVEAL), "running",
                  gold_tail=k == n - 1)
    rule_top = baselines[-1] + r_drop
    card.rect("rule", (x_text, rule_top, x_text + 44, rule_top + 4), GOLD, ("ease", 0.30, 0.30), "x")
    sp_base = rule_top + sp_drop
    for j, sl in enumerate(sp_lines):
        card.text("speaker" if j == 0 else f"speaker_{j}", sl, "Montserrat", 800, sp_px, GOLD, x_text,
                  sp_base + j * sp_step, ("ease", 0.30, REVEAL), "kicker", track=sp_track)
    for j, c in enumerate(cite):
        card.text("citation" if j == 0 else f"citation_{j}", c, DATA_FONT, 500, ci_px, STONE_LT, x_text,
                  sp_base + sp_extra + ci_drop + j * ci_step, ("ease", 0.30, REVEAL), "data")
    if not geo.wide:
        bottom = max(e.box[3] for e in card.elements)
        if bottom > geo.y(1058):
            raise CardRefused(f"the quote card's last ink at {bottom} runs into the caption band")
    meta = {"provenance": {"quote": {"source": "card_field", "kind": "quotes", "id": spec.quote_src.id,
                                     "fact_id": qf["fact_id"]}},
            "anchors": {"M": M, "quote_baselines": baselines, "rule_top": rule_top, "speaker": sp_base,
                        "quote_px": P, "citation_lines": len(cite)},
            "rest_s": 1.00, "_r": {"drift": drift, "items": card.items, "last_t": last_t, "rest": rest},
            "_contrast": card.contrast, "_marks": card.marks}
    return _marks_ok(card.plan("quote", seconds, frames, (qf["fact_id"],), meta))


def _plate_mask(plate: Any, size: tuple[int, int]) -> Image.Image:
    m = Image.new("L", size, 0)
    m.paste(Image.fromarray(np.where(plate.glyphs > 127, 255, 0).astype(np.uint8)), plate.origin)
    return m


# ------------------------------------------------------------------------------------------------ hook frame
def _focus_frame(photo: Path, size: tuple[int, int]) -> tuple[float, float, float, float] | None:
    """The reviewed focus (cover_design.reviewed_focus) in frame px, through the caption-strip crop and the fit."""
    found = reviewed_focus(Path(photo))
    if not found:
        return None
    with Image.open(photo) as im:
        src = ImageOps.exif_transpose(im)
        clean, kept = without_caption_strip(src.convert("RGB"))
        box = remap_focus(found["focus_box"], kept, src.size)
    if not box:
        return None
    cw, ch = clean.size
    W, Hh = size
    if cw / ch > W / Hh:
        crop_w, crop_h = ch * W / Hh, ch
    else:
        crop_w, crop_h = cw, cw * Hh / W
    ox, oy = (cw - crop_w) / 2, (ch - crop_h) / 2
    sx, sy = W / crop_w, Hh / crop_h
    return ((box[0] * cw - ox) * sx, (box[1] * ch - oy) * sy, (box[2] * cw - ox) * sx, (box[3] * ch - oy) * sy)


def _overlap(focus, box) -> float:
    ix = max(0.0, min(focus[2], box[2]) - max(focus[0], box[0]))
    iy = max(0.0, min(focus[3], box[3]) - max(focus[1], box[1]))
    area = max(1e-9, (focus[2] - focus[0]) * (focus[3] - focus[1]))
    return ix * iy / area


def _hook_geometry(n: int, P: int, eyebrow: bool, L: int = 1010, lines: Sequence[str] | None = None
                   ) -> dict[str, Any]:
    cap, step = round(0.86 * P), round(0.98 * P)
    # freeze panel A1: an accented line's step grows (its marks clear the line above); the block keeps L
    off = _lead(lines, P, step, cap) if lines is not None else [k * step for k in range(n)]
    baselines = [L - (off[-1] - o) for o in off]
    T = baselines[0] - cap
    E = T - 44
    block_top = (E - 46) if eyebrow else T
    return {"baselines": baselines, "T": T, "E": E, "block_top": block_top, "L": L, "cap": cap, "step": step}


def _rule_halo(bg: Image.Image, under: Image.Image, rule: Sequence[int],
               shape: Sequence[tuple[float, float]] | None = None) -> Image.Image:
    """`under` with a soft INK halo set under the eyebrow's 44 x 4 GOLD rule, as a text plate's halo sits under its
    glyphs: the least strength (8% steps, at most 0.95) at which the rule measures NONTEXT_MIN on the ground it is
    drawn on; none when the scrim already gives it that. Part of the ground, so it fades with the scrim."""
    from PIL import ImageFilter
    size = bg.size
    if shape:      # a polygon mark (the masthead's diamond) is solved on its own shape, `rule` its box
        mask = mark_mask(size, lambda d: d.polygon(list(shape), fill=255))
    else:
        mask = mark_mask(size, lambda d: d.rectangle([rule[0], rule[1], rule[2] - 1, rule[3] - 1], fill=255))
    grow = NONTEXT_BAND + RULE_HALO_PX      # past the whole band the rule is measured on, then feathered
    if shape:
        # round 3 review 4: a blurred grown rectangle read as a small dark box behind the diamond; the patch is the
        # diamond itself grown past the band, feathered round
        cx = sum(x for x, _ in shape) / len(shape)
        cy = sum(y for _, y in shape) / len(shape)
        r = max(max(abs(x - cx), abs(y - cy)) for x, y in shape)
        k = (r + grow) / max(1e-9, r)
        grown = [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in shape]
        soft = mark_mask(size, lambda d: d.polygon(grown, fill=255))
        soft = soft.filter(ImageFilter.GaussianBlur(RULE_HALO_PX))
    else:
        soft = mark_mask(size, lambda d: d.rounded_rectangle(
            [rule[0] - grow, rule[1] - grow, rule[2] + grow, rule[3] + grow], radius=grow, fill=255))
        soft = soft.filter(ImageFilter.GaussianBlur(RULE_HALO_PX * 0.8))
    base = bg.convert("RGBA")
    strength = 0.0
    while True:
        layer = under
        if strength > 0:
            ink = Image.new("RGBA", size, INK + (255,))
            ink.putalpha(soft.point(lambda v, a=strength: int(v * a)))
            layer = Image.alpha_composite(under, ink)
        ground = Image.alpha_composite(base, layer).convert("RGB")
        probe = ground.copy()
        probe.paste(GOLD, (0, 0), mask)
        v = nontext_contrast(probe, mask, ground=ground)
        if v is None or v >= NONTEXT_MIN or strength >= 0.95:
            return layer
        strength = min(0.95, strength + 0.08)


def _band_layer(size: tuple[int, int], band: Sequence[int], strength: float) -> Image.Image:
    """cover_design._mast_scrim's INK band (its recorded strength and band), as a layer to fade with the masthead."""
    from .cover_design import MAST_SCRIM_FEATHER
    y0, y1 = float(band[0]), float(band[1])
    mid = (y0 + y1) / 2
    yy = np.arange(size[1], dtype=np.float32)
    up, down = MAST_SCRIM_FEATHER
    d = np.where(yy < y0, (y0 - yy) / up, np.where(yy > mid, (yy - mid) / down, 0))
    v = np.clip(1 - d, 0, 1)
    v = v * v * (3 - 2 * v)
    layer = Image.new("RGBA", size, INK + (255,))
    layer.putalpha(Image.fromarray((v * strength * 255).astype("uint8")[:, None]).resize(size))
    return layer


# The masthead's band (round 2 review 4) is sized to what its ground needs: its first strength is stepped by the
# ground's measured luminance, then raised one step at a time only while it would need a halo over HOOK_HALO_SOFT
# to reach HOOK_TARGET. The claim block's ground is the wash below (_hook_scrim, round 4).
MAST_BAND_STEPS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5)
MAST_BAND_START = ((0.05, 0.0), (1.01, 0.1))
HOOK_HALO_SOFT = 0.9        # the halo may reach this before the scrim steps up
HOOK_HALO_STEPS = (0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0)
# The hook's glyph halos (round 3 review 4): one INK field for every line, drawn on the ground under all of them
# (never a later line's halo over an earlier line's glyphs), grown and feathered on the whole frame (never cut at
# a plate's crop, which drew straight-edged boxes on gold, yellow and bright noisy grounds). Grow and feather are
# shares of the type size, with a floor for the small caps.
HOOK_HALO_GROW = 0.18       # x the font px: the glyph mask is grown this far (Anton 112: 20 px), past the ring
HOOK_HALO_SIGMA = 0.12      # x the font px: then feathered with this Gaussian sigma (Anton 112: 13 px)
SMALL_HALO_GROW, SMALL_HALO_SIGMA = 9, 5    # floors for the eyebrow and the masthead (past their 10 px ring)


def _halo_field(size: tuple[int, int], plates: Sequence[Any], px: float) -> np.ndarray:
    """The feathered INK field (0..1) under `plates`' glyphs on the whole frame: their union grown by
    HOOK_HALO_GROW x px and blurred by HOOK_HALO_SIGMA x px (at least the small-type floors)."""
    from PIL import ImageFilter
    m = Image.new("L", size, 0)
    for p in plates:
        if getattr(p, "empty", True) or p.full is None:
            continue
        m.paste(255, (int(p.origin[0]), int(p.origin[1])), p.full.point(lambda v: 255 if v > 127 else 0))
    grow = max(SMALL_HALO_GROW, round(HOOK_HALO_GROW * px))
    sigma = max(SMALL_HALO_SIGMA, HOOK_HALO_SIGMA * px)
    bb = m.getbbox()
    if bb is None:
        return np.zeros((size[1], size[0]), np.float32)
    pad = grow + int(4 * sigma) + 2
    box = (max(0, bb[0] - pad), max(0, bb[1] - pad), min(size[0], bb[2] + pad), min(size[1], bb[3] + pad))
    crop = np.asarray(m.crop(box)) > 127
    grown = Image.fromarray((_dilate(crop, 2 * grow + 1) * 255).astype(np.uint8))
    soft = np.asarray(grown.filter(ImageFilter.GaussianBlur(sigma)), np.float32) / 255.0
    out = np.zeros((size[1], size[0]), np.float32)
    out[box[1]:box[3], box[0]:box[2]] = soft
    return out


def _halo_layer(field: np.ndarray, strength: float) -> Image.Image:
    h, w = field.shape
    layer = Image.new("RGBA", (w, h), INK + (255,))
    layer.putalpha(Image.fromarray(np.clip(field * strength * 255.0, 0, 255).astype(np.uint8)))
    return layer


def _ground_lum(im: Image.Image, box: Sequence[float]) -> float:
    """The ground's luminance under `box`: the 75th percentile of the relative luminance of its pixels."""
    W, H = im.size
    b = (max(0, int(box[0])), max(0, int(box[1])), min(W, int(box[2])), min(H, int(box[3])))
    if b[2] <= b[0] or b[3] <= b[1]:
        return 0.0
    rgb = np.asarray(im.convert("RGB").crop(b).resize((max(1, (b[2] - b[0]) // 4), max(1, (b[3] - b[1]) // 4))))
    return float(np.percentile(_lin(rgb.reshape(-1, 3)), 75))


def _steps_from(lum: float, start: Sequence[tuple[float, float]], steps: Sequence[float]) -> list[float]:
    first = next(v for lim, v in start if lum < lim)
    return [s for s in steps if s >= first - 1e-9]


def _row_plates(ground: Image.Image, rows: Sequence[tuple]) -> list[Any]:
    """`rows` planned as the card plans them, with no plate halo (the shared halo field carries it)."""
    probe = _Card("hook", _Geo(*ground.size), ground, target=HOOK_TARGET)
    out = []
    for j, (text, face, weight, px, fill, x, y, track) in enumerate(rows):
        out += probe.text(f"probe_{j}", text, face, weight, px, fill, x, y, ("hook",), "display", track=track,
                          rise=False, record=False, halo_px=0)["parts"]
    return out


def _least_halo(base: Image.Image, layer: Image.Image, field: np.ndarray, plates: Sequence[Any],
                ceiling: float) -> tuple[float | None, float, Image.Image]:
    """(the least HOOK_HALO_STEPS strength up to `ceiling` at which every plate measures HOOK_TARGET on `base`
    under `layer` and the halo field, or None; the lowest ratio there; the layer with that halo)."""
    worst, out = 0.0, layer
    for h in HOOK_HALO_STEPS:
        if h > ceiling + 1e-9:
            break
        out = layer if h <= 0 else Image.alpha_composite(layer, _halo_layer(field, h))
        ground = Image.alpha_composite(base, out).convert("RGB")
        worst = min((p.solve(ground, HOOK_TARGET, 0.0, 0.0).contrast or 0.0) for p in plates)
        if worst >= HOOK_TARGET:
            return h, worst, out
    return None, worst, out


# Round 4 (G): the boxed scrim, the eyebrow's own soft patch and a halo free to 0.9 read on bright grounds as a dirty
# grey box behind the eyebrow and a heavy black meme-style glow. The hook's ground is now one full-width INK wash
# from the frame's top (under the masthead too) to the claim's foot, eased out over HOOK_WASH_FEATHER[1] below, so
# it has no left, right, top or corner edge and reads as the photograph's sky darkening, not a panel or a stripe. Its strength steps up from the one
# the ground's luminance calls for until every claim line and the eyebrow plan HOOK_TARGET with at most a light
# soft shadow (HOOK_GLOW_MAX); only at the last step may the halo rise further (the card then refuses on the line
# that still cannot).
HOOK_WASH_STEPS = tuple(round(0.05 * i, 2) for i in range(20))      # 0.0 .. 0.95
HOOK_WASH_START = ((0.05, 0.0), (0.18, 0.15), (0.45, 0.35), (1.01, 0.5))
HOOK_WASH_FEATHER = (260, 560)    # design px eased out above the eyebrow and below the claim
HOOK_GLOW_MAX = 0.0        # round 4 review 2 (6): even 0.3 read as a dark cloud round the gold figure on yellow, so the
#                            wash carries the contrast alone (a halo only at its last step, where the card may refuse)
# Round 4 review 2 (6): a wash the full width of the frame from its very top turned a bright photo into a flat grey
# (white) or olive (yellow) card that ended in a short grey-to-white step under the claim. The wash is now a
# directional shade from the top-left corner: right of the claim block (the text is ranged left) it eases to
# HOOK_WASH_FLOOR of its strength over HOOK_WASH_SIDE, and below the claim it eases out over a long
# HOOK_WASH_FEATHER[1], so the photograph's brightness shows on the right and the lower half, never a panel. (A wash
# opened above the masthead too was tried: on white it drew a dark capsule across the frame.)
HOOK_WASH_SIDE = 520        # design px over which it eases from the block's right edge to HOOK_WASH_FLOOR
HOOK_WASH_FLOOR = 0.55      # share of the strength it keeps at the frame's right side (under the masthead's end)
HOOK_WASH_RIGHT_AIR = 90    # design px past the block's right ink before the ease begins


def _wash_layer(size: tuple[int, int], y0: float, y1: float, strength: float, x1: float | None = None
                ) -> Image.Image:
    """An INK wash: `strength` from y0 to y1, smoothstepped to 0 over HOOK_WASH_FEATHER above and below; with `x1`,
    smoothstepped right of x1 down to HOOK_WASH_FLOOR x strength over HOOK_WASH_SIDE (no edge anywhere)."""
    up, down = HOOK_WASH_FEATHER
    yy = np.arange(size[1], dtype=np.float32)
    d = np.where(yy < y0, (y0 - yy) / up, np.where(yy > y1, (yy - y1) / down, 0.0))
    v = np.clip(1 - d, 0, 1)
    v = v * v * (3 - 2 * v)
    if x1 is None:
        hx = np.ones(size[0], np.float32)
    else:
        xx = np.arange(size[0], dtype=np.float32)
        u = np.clip((xx - x1) / HOOK_WASH_SIDE, 0, 1)
        u = u * u * (3 - 2 * u)
        hx = 1 - (1 - HOOK_WASH_FLOOR) * u
    a = np.clip(v[:, None] * hx[None, :] * strength * 255.0, 0, 255).astype("uint8")
    layer = Image.new("RGBA", size, INK + (255,))
    layer.putalpha(Image.fromarray(a))
    return layer


# Review 2026-11-03 (3): on flat bright grounds (white, snow, cyan) the soft wash reached the strength the claim
# needs only as a large grey smudge over two thirds of the frame. There the claim now sits on a designed plate: one
# full-bleed INK band with crisp top and bottom edges from the eyebrow's air to the claim's foot, at the least
# HOOK_PLATE_STEPS opacity that carries every line (a title band, the photograph untouched above and below it).
HOOK_PLATE_LUM = 0.45        # the block's ground luminance (75th percentile) from which the plate replaces the wash
HOOK_PLATE_STEPS = (0.96,)   # near-solid (review 2026-11-03, round 3: 0.86-0.95 read olive-black on yellow)
MAST_PLATE_PAD = 14          # design px the masthead's plate runs past its band
# Review 2026-11-03 (round 3): a masthead plate and a claim plate stacked two charcoal strips with ~200 px of bright
# ground between them. On a bright ground the masthead and the claim now share ONE plate, from MAST_PLATE_PAD above
# the masthead's band to the claim's foot: a single designed title panel, the photograph untouched above and below.
HOOK_PLATE_RULE = 6          # design px: the GOLD foot rule the panel had until round 4 (no longer drawn)
HOOK_PLATE_PAD = (28, 34)    # design px the band runs above the block's air and below the claim's foot
# Review 2026-11-03 (round 4): the shared panel's padding was lopsided (20 px above the masthead, 57 px below the
# claim), its GOLD foot rule vanished against a yellow wall, and a centred masthead over a flush-left claim read as
# two pieces. The panel now runs HOOK_PLATE_AIR past the masthead's ink and past the claim's ink, alike; the claim
# (and an eyebrow) is centred under the centred masthead; the panel has no foot rule (its accents are the GOLD
# diamond and the GOLD last line, both inside it).
HOOK_PLATE_AIR = 44          # design px from the panel's top edge to the masthead's ink, and from the claim's ink foot


def _plate_layer(size: tuple[int, int], y0: float, y1: float, strength: float, feather: float = 0.0
                 ) -> Image.Image:
    """A full-bleed INK band from y0 to y1 at `strength`, its edges crisp (one antialiased pixel), or with
    `feather` (freeze panel H6 (4)) eased (smoothstep) from nothing `feather` px outside y0 and y1 to full at them."""
    yy = np.arange(size[1], dtype=np.float32) + 0.5
    if feather > 0:
        d = np.maximum(y0 - yy, yy - y1)
        x = np.clip(1.0 - d / feather, 0.0, 1.0)
        v = np.where(d <= 0, 1.0, x * x * (3 - 2 * x)).astype(np.float32)
    else:
        v = np.clip(np.minimum(yy - y0, y1 - yy) + 0.5, 0, 1)
    a = np.clip(v[:, None] * strength * 255.0 + np.zeros((1, size[0]), np.float32), 0, 255).astype("uint8")
    layer = Image.new("RGBA", size, INK + (255,))
    layer.putalpha(Image.fromarray(a))
    return layer


def _hook_scrim(bg: Image.Image, box: Sequence[float], rows: Sequence[tuple],
                eyebrow_row: tuple | None = None, eyebrow_box: Sequence[float] | None = None,
                head: float = 0.0, plate_top: float | None = None,
                plate_bottom: float | None = None, feather: float = 0.0) -> tuple[Image.Image, dict]:
    """The claim block's ground: the full-width wash (_wash_layer over box's rows) at the least HOOK_WASH_STEPS
    strength, from the one its ground's luminance calls for, at which every claim line and the eyebrow plan
    HOOK_TARGET with the shared halo field at most HOOK_GLOW_MAX (at the last step the halo may reach 1.0)."""
    lum = _ground_lum(bg, box)
    base = bg.convert("RGBA")
    plates = _row_plates(bg, rows)
    field = _halo_field(bg.size, plates, rows[0][3])
    if eyebrow_row is not None:
        eb = _row_plates(bg, [eyebrow_row])
        plates = plates + eb
        field = np.maximum(field, _halo_field(bg.size, eb, eyebrow_row[3]))
    under, tried = Image.new("RGBA", bg.size, (0, 0, 0, 0)), []
    if lum >= HOOK_PLATE_LUM:
        y0, y1 = box[1] - HOOK_PLATE_PAD[0], box[3] + HOOK_PLATE_PAD[1]
        if plate_top is not None:
            y0 = min(y0, plate_top)       # one panel from the masthead down (no second strip, no gap)
        if plate_bottom is not None:
            y1 = plate_bottom             # the claim's ink foot plus the same air as above the masthead
        for i, st in enumerate(HOOK_PLATE_STEPS):
            last = i == len(HOOK_PLATE_STEPS) - 1
            h, worst, under = _least_halo(base, _plate_layer(bg.size, y0, y1, st, feather), field, plates,
                                          1.0 if last else HOOK_GLOW_MAX)
            tried.append({"strength": st, "min": round(worst, 2), "halo": h})
            if h is not None:
                break
        rec = {"luminance": round(lum, 3), "mode": "plate", "strength": tried[-1]["strength"],
               "halo": tried[-1]["halo"], "tried": tried, "plate": [round(y0), round(y1)],
               "feather": feather}
        if eyebrow_row is not None:
            rec["eyebrow_patch"], rec["eyebrow_halo"] = 0.0, tried[-1]["halo"]
        return under, rec
    steps = _steps_from(lum, HOOK_WASH_START, HOOK_WASH_STEPS)
    right = max([p.box[2] for p in plates if not getattr(p, "empty", False)] + [box[0]]) + HOOK_WASH_RIGHT_AIR
    for i, st in enumerate(steps):
        wash = _wash_layer(bg.size, head, box[3], st, right)    # open at the top and to the right, no edge
        last = i == len(steps) - 1
        h, worst, under = _least_halo(base, wash, field, plates, 1.0 if last else HOOK_GLOW_MAX)
        tried.append({"strength": st, "min": round(worst, 2), "halo": h})
        if h is not None:
            break
    rec: dict[str, Any] = {"luminance": round(lum, 3), "mode": "wash", "strength": tried[-1]["strength"],
                           "halo": tried[-1]["halo"], "tried": tried, "wash": [round(head), round(box[3])],
                           "wash_right": round(right)}
    if eyebrow_row is not None:
        rec["eyebrow_patch"], rec["eyebrow_halo"] = 0.0, tried[-1]["halo"]
    return under, rec


def _mast_band(plate: Any) -> tuple[float, float]:
    from .cover_design import BRAND_TEXT
    box = plate.font.getbbox(BRAND_TEXT, anchor="lt")
    return plate.xy[1] + box[1] - 6, plate.xy[1] + box[3] + 6


def _hook_masthead(bg: Image.Image, under: Image.Image, plate: Any, crisp: bool = False,
                   on_plate: float | None = None) -> tuple[Image.Image, Any, dict]:
    """The masthead re-planned at HOOK_TARGET (round 2 review 6: cover_design._brand plans PLACE_TARGET): the
    least INK band across its line (cover_design._mast_scrim's eased band), from the strength its ground's
    luminance calls for, under which it reaches HOOK_TARGET with its own whole-frame halo field (_halo_field, so
    no crop-edge box: round 3 review 4) at most HOOK_HALO_SOFT; else the strongest band with the halo free to
    reach 1.0. The plate itself draws no halo."""
    from .cover_design import BRAND_TEXT
    font, xy = plate.font, plate.xy
    box = font.getbbox(BRAND_TEXT, anchor="lt")
    band = (xy[1] + box[1] - 6, xy[1] + box[3] + 6)
    base_rgba = bg.convert("RGBA")
    base = Image.alpha_composite(base_rgba, under)
    lum = _ground_lum(base, plate.box)
    p = plan_text(base.convert("RGB"), xy, BRAND_TEXT, font, BONE, "lt", plate.track, target=HOOK_TARGET,
                  halo_px=0, min_halo=0.0, shadow=True, max_halo=0.0)
    field = _halo_field(bg.size, [p], font.size)
    steps = _steps_from(lum, MAST_BAND_START, MAST_BAND_STEPS)
    if on_plate is not None:
        # review 2026-11-03 (round 3): the masthead sits on the claim's own plate (one panel), so no band of its own
        steps = [0.0]
    elif crisp:
        # review 2026-11-03 (3): beside the claim's plate, the masthead's band is a plate too (crisp edges, the
        # same INK), never a soft grey smear across a bright sky
        steps = [s for s in HOOK_PLATE_STEPS] if lum >= HOOK_PLATE_LUM else steps
        band = (band[0] - MAST_PLATE_PAD, band[1] + MAST_PLATE_PAD)
    layer, h, st = under, None, 0.0
    for i, st in enumerate(steps):
        last = i == len(steps) - 1
        lay_fn = _plate_layer(bg.size, band[0], band[1], st) if crisp else _band_layer(bg.size, band, st)
        banded = under if st <= 0 else Image.alpha_composite(under, lay_fn)
        h, _, layer = _least_halo(base_rgba, banded, field, [p], 1.0 if last else HOOK_HALO_SOFT)
        if h is not None:
            break
    p.solve(Image.alpha_composite(base_rgba, layer).convert("RGB"), HOOK_TARGET, 0.0, 0.0)
    if on_plate is not None:   # its ground is the shared plate, at the plate's strength
        return layer, p, {"luminance": round(lum, 3), "band": [round(v) for v in band], "strength": on_plate,
                          "halo": h, "on_plate": True}
    return layer, p, {"luminance": round(lum, 3), "band": [round(v) for v in band], "strength": st, "halo": h}


def _hook_mast_foot() -> int:
    """Freeze panel H6: the hook masthead's ink foot (y), measured from the drawn masthead's font."""
    from .cover_design import BRAND_TEXT, BRAND_TOP
    return BRAND_TOP + get_font("Cinzel", HOOK_BRAND_PX, weight=700).getbbox(BRAND_TEXT, anchor="lt")[3]


def _hook_claim(spec: HookFrame, sheet: dict, payload: Mapping[str, Any] | None
                ) -> tuple[str, dict | None, tuple[str, ...], dict]:
    claim = " ".join(str(spec.claim or "").split())
    if not claim:
        raise CardRefused("an empty claim")
    if len(claim.split()) > 6 or len(claim) > 30 or "!" in claim:
        raise CardRefused("a claim is at most 6 words and 30 characters, with no '!'")
    if any(claim.lower() == " ".join(str(m).split()).lower() for m in spec.run_myths):
        raise CardRefused("the claim repeats a myth this run debunks")
    if not spec.fact_ids:
        raise CardRefused("a claim must cite a fact")
    # the claim is permitted by its provenance only (round 4); a disputed figure the guard refuses is no longer
    # withheld into a new line ("Basin wide"), which would be words neither the sheet nor the gate ever said
    t, cited, prov = ground_line(claim, spec.claim_src, sheet, spec.fact_ids, role="claim", payload=payload)
    fc = figure_check({"line1": t}, sheet)
    if fc and fc.get("hedged") and fc.get("unit") and not fc.get("qualifier"):
        raise CardRefused("an estimate drawn without its qualifier")
    words = _content(t)
    if words and len(words & _content(spec.first_burst)) / len(words) > 0.6:
        raise CardRefused("the claim repeats the first caption burst")
    return t, None, cited, prov


def _proper_run(claim: str) -> tuple[int, int] | None:
    """Freeze panel H6 (3): the word span [i, j) of the claim's first proper-noun run: capitalised words after its
    first word, read from the signed field's own casing (before .upper()); None when there is none."""
    words = claim.split()
    i = next((k for k in range(1, len(words)) if words[k][:1].isupper()), None)
    if i is None:
        return None
    j = i
    while j < len(words) and words[j][:1].isupper():
        j += 1
    return i, j


def _hook_gold_lines(lines: Sequence[str], claim: str = "") -> tuple[tuple[int, ...], int | None]:
    """Freeze panel H6 (3, topic-agnostic): (the GOLD lines, the character a one-line claim's GOLD run starts at,
    None for whole lines): the first line with a figure; else the line or lines holding the claim's first
    proper-noun run (the run may span lines; on a one-line claim the run itself); else a 2- or 3-line claim's last
    line, else a one-line claim's last word (H2)."""
    for k, ln in enumerate(lines):
        if any(ch.isdigit() for ch in ln):
            return (k,), None
    run = _proper_run(claim) if claim and len(claim.split()) == sum(len(ln.split()) for ln in lines) else None
    if run:
        i, j = run
        where, w = [], 0
        for k, ln in enumerate(lines):
            n = len(ln.split())
            if w < j and w + n > i:
                where.append(k)
            w += n
        if len(lines) == 1:
            words = lines[0].split()
            if i == 0:
                return (0,), None
            return (0,), len(" ".join(words[:i])) + 1
        return tuple(where), None
    if len(lines) >= 2:
        return (len(lines) - 1,), None
    words = lines[0].split()
    return (0,), (len(lines[0]) - len(words[-1])) if len(words) >= 2 else None


def _hook_gold(lines: Sequence[str], claim: str = "") -> tuple[int, int | None]:
    """Freeze panel H2: (the first GOLD line, the character its GOLD run starts at, None for the whole line);
    _hook_gold_lines gives every GOLD line."""
    ks, frm = _hook_gold_lines(lines, claim)
    return ks[0], frm


def _hue_sat(im: Image.Image, box: Sequence[float]) -> tuple[float, float]:
    """The mean hue (degrees, circular) and saturation (0..255) of `im` inside `box`."""
    b = [int(round(v)) for v in box]
    b = [max(0, b[0]), max(0, b[1]), min(im.width, b[2]), min(im.height, b[3])]
    a = np.asarray(im.crop(b).convert("HSV"), np.float32)
    if not a.size:
        return 0.0, 0.0
    ang = a[..., 0] / 255.0 * 2 * math.pi
    w = a[..., 1]
    hue = math.degrees(math.atan2(float((np.sin(ang) * w).sum()), float((np.cos(ang) * w).sum()))) % 360
    return hue, float(w.mean())


def _radial_scrim(size: tuple[int, int], box: Sequence[float], reach: float, alpha: float) -> Image.Image:
    """A soft INK scrim: full `alpha` inside the block's ellipse, feathered to nothing `reach` px past it."""
    W, H = size
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    rx, ry = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    # the distance past the block's (rounded) edge, in px
    dx = np.maximum(0.0, np.abs(xx - cx) - rx)
    dy = np.maximum(0.0, np.abs(yy - cy) - ry)
    d = np.sqrt(dx * dx + dy * dy)
    x = np.clip(1.0 - d / max(1.0, reach), 0.0, 1.0)
    m = x * x * (3 - 2 * x) * alpha
    layer = Image.new("RGBA", size, HOOK_SCRIM_FILL + (0,))
    layer.putalpha(Image.fromarray(np.clip(m * 255 + 0.5, 0, 255).astype(np.uint8), "L"))
    return layer


def plan_hook_frame(spec: HookFrame, sheet: dict, width: int = 1080, height: int = 1920, *,
                    payload: Mapping[str, Any] | None = None) -> CardPlan:
    if (width, height) != (1080, 1920):
        raise ValueError("the hook frame is drawn at 1080x1920 only (the masthead's own geometry)")
    size = (width, height)
    t, withheld, cited, prov_c = _hook_claim(spec, sheet, payload)
    provenance: dict[str, Any] = {"claim": prov_c}
    eyebrow = ""
    if spec.eyebrow:
        eb = " ".join(spec.eyebrow.split())
        if len(eb) > 24:
            raise CardRefused("an eyebrow is at most 24 characters")
        if any(ch.isdigit() for ch in eb):
            raise CardRefused("the eyebrow is texture: it never carries a figure")
        eyebrow, _, provenance["eyebrow"] = ground_line(eb, spec.eyebrow_src, sheet, spec.fact_ids, role="eyebrow",
                                                        payload=payload)
        eyebrow = eyebrow.upper()
    eyebrow_dropped = None
    if eyebrow and spec.photo_kind == "generated":
        # round 2 review 11: masthead, disclosure and eyebrow stacked three tiers of small type over the claim. The
        # disclosure is required and the eyebrow is texture, so the disclosure takes the eyebrow's place.
        eyebrow, eyebrow_dropped = "", "the disclosure takes its place"
    photo = Path(spec.photo) if spec.photo else None
    if not photo or not photo.is_file():
        raise CardRefused("the hook needs its photo")
    try:
        with Image.open(photo) as im:
            src = ImageOps.exif_transpose(im)
            clean, _ = without_caption_strip(src.convert("RGB"))
            cw, ch = clean.size
    except Exception as e:  # an unreadable file is a refusal, not an INK fallback
        raise CardRefused(f"the photo cannot be read ({e})") from e
    up = max(width / cw, height / ch)
    if up > UPSCALE_MAX:
        raise CardRefused(f"the photo would be enlarged {up:.2f}x (max {UPSCALE_MAX})")
    upper = t.upper()

    def hook_fit(measure: int):
        """H1: one line at the largest of HOOK_SIZES[1] that holds it, else two, else three (W2 on each ladder: a
        line count whose every allowed size ends a line on a stop word gives way to the next count)."""
        refused = None
        for n_l in sorted(HOOK_SIZES):
            try:
                got = _w1_ladder(HOOK_SIZES[n_l], lambda P: (lambda ls: ls if ls and len(ls) == n_l else None)(
                    _wrap(upper, get_font("Anton", P), measure, n_l, min_lines=n_l, even=n_l > 1)))
            except CardRefused as e:
                refused = refused or e
                continue
            if got:
                return got
        if refused is not None:
            raise refused
        return None
    def hook_fits(measure: int) -> list[tuple[int, list[str]]]:
        """hook_fit's choice, then (only to keep the claim off the reviewed subject) the same line count's smaller
        sizes, then the other line counts' fits, largest first."""
        first = hook_fit(measure)
        if not first:
            return []
        out = [first]
        order = [len(first[1])] + [n_l for n_l in sorted(HOOK_SIZES) if n_l != len(first[1])]
        for n_l in order:
            for px in HOOK_SIZES[n_l]:
                if n_l == len(first[1]) and px >= first[0]:
                    continue
                ls = _wrap(upper, get_font("Anton", px), measure, n_l, min_lines=n_l, even=n_l > 1)
                if ls and len(ls) == n_l and not _stop_end(ls):     # W2: never a stop-word end
                    out.append((px, ls))
        return out
    cands = hook_fits(HOOK_MEASURE)
    if not cands:
        raise CardRefused("the claim does not fit three lines at 112 px")
    P, lines = cands[0]
    f_claim = get_font("Anton", P)
    f_eb = get_font("Montserrat", EYEBROW_PX, weight=800)
    geo = _hook_geometry(len(lines), P, bool(eyebrow), lines=lines)
    focus = tuple(spec.focus) if spec.focus else _focus_frame(photo, size)

    eb_w = _tracked_width(eyebrow, f_eb, EYEBROW_TRACK) if eyebrow else 0.0

    def line_x(ln: str, centred: bool) -> int:
        """The line's ink-left x: 100 (flush left), or centred on the canvas under the centred masthead."""
        if not centred:
            return 100
        ib = _ink(ln, f_claim)
        return round(width / 2 - (ib[2] - ib[0]) / 2)

    def block_box(g, centred=False):
        boxes = []
        for ln, b in zip(lines, g["baselines"]):
            ib, x = _ink(ln, f_claim), line_x(ln, centred)
            boxes.append((x, b + ib[1], x + ib[2] - ib[0], b + ib[3]))
        if eyebrow:
            ex = round(width / 2 - eb_w / 2) if centred else 100
            boxes.append((min(ex, width / 2 - 22) if centred else ex, g["E"] - 46, ex + int(eb_w), g["E"] + 4))
        return _union(boxes)
    position = None
    mast_foot = _hook_mast_foot()
    top_block = mast_foot + HOOK_TOP_AIR       # H6: re-measured from the drawn masthead (565 under the 64 px one)
    for P, lines in cands:
        f_claim = get_font("Anton", P)
        geo = _hook_geometry(len(lines), P, bool(eyebrow), lines=lines)
        if not (focus and _overlap(focus, block_box(geo)) > FOCUS_OVERLAP):
            position = "normal"
            break
        geo = _hook_geometry(len(lines), P, bool(eyebrow), lines=lines, L=1010 - (geo["block_top"] - top_block))
        if _overlap(focus, block_box(geo)) <= FOCUS_OVERLAP:
            position = "top"
            break
    if position is None:
        raise CardRefused("the claim covers the reviewed subject in both positions")
    if position == "top" and spec.photo_kind == "generated":
        raise CardRefused("no room for the disclosure above a moved claim")
    bg = cover_crop(photo, size).convert("RGB")
    if position == "normal" and spec.photo_kind != "generated":
        # review 2026-11-03 (round 3, C): on a bright ground the claim moves up under the masthead, so the two share
        # one compact plate (a title panel) instead of a tall panel with an empty middle; never onto the subject
        box_n = (76, geo["block_top"] - (EYEBROW_SCRIM_AIR if eyebrow else 24), 908, geo["L"] + 30)
        # operator decision 2026-11-04 (review: the rework's flush-left panel stood 48 px off the masthead's axis):
        # on the plate the claim is centred under the centred masthead again, refit to HOOK_MEASURE_CENTRED so its
        # lines stay inside 192..888; the plate keeps H6 (4)'s moved-up position and feathered edges
        try:
            c_fit = hook_fit(HOOK_MEASURE_CENTRED)
        except CardRefused:
            c_fit = None
        if c_fit and _ground_lum(bg, box_n) >= HOOK_PLATE_LUM:
            kept = (P, lines, f_claim)
            P, lines = c_fit
            f_claim = get_font("Anton", P)
            g_c = _hook_geometry(len(lines), P, bool(eyebrow), lines=lines)
            g_top = _hook_geometry(len(lines), P, bool(eyebrow), lines=lines,
                                   L=1010 - (g_c["block_top"] - top_block))
            if not (focus and _overlap(focus, block_box(g_top, centred=True)) > FOCUS_OVERLAP):
                geo, position = g_top, "plate"
            else:
                P, lines, f_claim = kept
    block_top, L = geo["block_top"], geo["L"]
    lay = Layout("hook_frame", size, graphics_box())
    n = len(lines)
    on_plate = position == "plate"       # one title panel under the masthead
    centred = on_plate                   # operator 2026-11-04: the panel centred again (the rework's flush left undone)
    # freeze panel H3: one alignment axis per position: flush left at x 100 on the wash (the masthead's mark too),
    # all centred on the plate. The masthead's geometry and layout record; its plate is re-planned at HOOK_TARGET
    _brand(bg.copy(), lay, left=None if centred else 100)
    xs = [line_x(ln, centred) for ln in lines]
    eb_x = round(width / 2 - eb_w / 2) if centred else 100
    gold_ks, gold_from = _hook_gold_lines(lines, t)
    gold_k = gold_ks[0]
    rows = []
    for k, (ln, x, b) in enumerate(zip(lines, xs, geo["baselines"])):
        if k == gold_k and gold_from:
            rows += [(ln[:gold_from], "Anton", None, P, BONE, x, b, 0.0),
                     (ln[gold_from:], "Anton", None, P, GOLD, x + f_claim.getlength(ln[:gold_from]), b, 0.0)]
        else:
            rows.append((ln, "Anton", None, P, GOLD if k in gold_ks else BONE, x, b, 0.0))
    eb_row = eb_box = None
    if eyebrow:
        eb_row = (eyebrow, "Montserrat", 800, EYEBROW_PX, BONE, eb_x, geo["E"], EYEBROW_TRACK)
        eb_box = (eb_x - 8, geo["E"] - 54, eb_x + 8 + eb_w, geo["E"] + 10)
    # with an eyebrow the scrim starts EYEBROW_SCRIM_AIR above its rule (round 1 review 6: 24 px left the GOLD rule
    # on the scrim's soft edge, 1.7-2.5 on bright, white and busy photos)
    scrim_box = (76, block_top - (EYEBROW_SCRIM_AIR if eyebrow else 24), 908, L + 30)
    mast_top = _mast_band(lay.meta["plates"]["brand"][0])[0] - MAST_PLATE_PAD
    plate_bottom = None
    if on_plate:   # the same air above the masthead's ink and below the claim's ink (review 2026-11-03, round 4)
        mast_top = lay.meta["plates"]["brand"][0].box[1] - HOOK_PLATE_AIR
        plate_bottom = max(b + _ink(ln, f_claim)[3] for ln, b in zip(lines, geo["baselines"])) + HOOK_PLATE_AIR
    under, scrim_rec = _hook_scrim(bg, scrim_box, rows, eb_row, eb_box, plate_top=mast_top,
                                   plate_bottom=plate_bottom, feather=HOOK_PLATE_FEATHER)
    # freeze panel H5: a warm, saturated ground under the block gets a soft radial INK scrim behind it
    tb = block_box(geo, centred)
    hue, sat_ = _hue_sat(bg, tb)
    radial = None
    if (scrim_rec.get("mode") == "wash" and HOOK_SCRIM_HUE[0] <= hue <= HOOK_SCRIM_HUE[1]
            and sat_ > HOOK_SCRIM_SAT):     # the plate is near-solid INK already
        radial = _radial_scrim(size, tb, HOOK_SCRIM_REACH, HOOK_SCRIM_ALPHA)
        under = Image.alpha_composite(radial, under)
    scrim_rec["radial"] = {"hue": round(hue, 1), "saturation": round(sat_, 1), "applied": radial is not None}
    shared = scrim_rec["strength"] if scrim_rec.get("mode") == "plate" else None
    under, brand_plate, band_rec = _hook_masthead(bg, under, lay.meta["plates"]["brand"][0],
                                                  crisp=scrim_rec.get("mode") == "plate", on_plate=shared)
    lay.meta["plates"]["brand"] = [brand_plate]
    rule = None
    if eyebrow:
        E = geo["E"]
        rule = (round(width / 2) - 22, E - 46, round(width / 2) + 22, E - 42) if centred else (100, E - 46, 144, E - 42)
        under = _rule_halo(bg, under, rule)
    # the masthead's GOLD diamond: the lighter band no longer darkens it on a yellow wall, so it gets the eyebrow
    # rule's measured soft INK halo, the least that brings its box to NONTEXT_MIN (none on most grounds)
    mark_box = next(e["box"] for e in lay.elements if e["name"] == "brand_mark")
    cx, cy = (mark_box[0] + mark_box[2]) / 2, (mark_box[1] + mark_box[3]) / 2
    r = (mark_box[2] - mark_box[0]) / 2
    diamond = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    under = _rule_halo(bg, under, [round(v) for v in mark_box], shape=diamond)
    ground0 = Image.alpha_composite(bg.convert("RGBA"), under).convert("RGB")
    if brand_plate.contrast is None or brand_plate.contrast < HOOK_TARGET:
        raise CardRefused(f"the masthead reaches only {brand_plate.contrast} against {HOOK_TARGET}")
    # two mark layers: `marks` under the plates (the disclosure's INK plate, which its text sits on) and `top` over
    # them (the diamond and the eyebrow rule: a neighbouring plate's INK halo would otherwise dim them)
    marks = Image.new("RGBA", size, (0, 0, 0, 0))
    top = Image.new("RGBA", size, (0, 0, 0, 0))
    md, td = ImageDraw.Draw(marks), ImageDraw.Draw(top)
    td.polygon(diamond, outline=INK + (255,), width=3)
    td.polygon(diamond, fill=GOLD + (255,))
    # no GOLD rule along the panel's foot (review 2026-11-03, round 4: it vanished against a yellow wall); the
    # panel's accents are the diamond and the GOLD last line, both on the INK
    card = _Card("hook", _Geo(width, height), ground0, target=HOOK_TARGET)
    hook = ("hook",)
    card._el("brand", "text", brand_plate.text, "Cinzel", 700, brand_plate.font.size, BONE, brand_plate.xy, "lt",
             brand_plate.track,
             brand_plate.box, hook)
    card.roles["brand"], card.drawn["brand"] = "masthead", brand_plate.text
    card._el("brand_mark", "mark", "", "", None, 0, GOLD, (cx, cy), "", 0.0, tuple(mark_box), hook)
    card.roles["brand_mark"] = "mark"
    plates: list[Any] = [brand_plate]
    lay_plates: dict[str, list] = {"title_1": [], "title_2": []}
    if rule:
        E = geo["E"]
        td.rectangle([rule[0], rule[1], rule[2] - 1, rule[3] - 1], fill=GOLD + (255,))
        card._el("eyebrow_rule", "mark", "", "", None, 0, GOLD, rule[:2], "", 0.0, rule, hook)
        card.roles["eyebrow_rule"] = "rule"
        it = card.text("eyebrow", eyebrow, "Montserrat", 800, EYEBROW_PX, BONE, eb_x, E, hook, "kicker",
                       track=EYEBROW_TRACK, rise=False, halo_px=0)
        for p in it["parts"]:
            p.alpha_scale = HOOK_EYEBROW_ALPHA      # H2: BONE at 80 %, the claim holds the only gold type
        plates += it["parts"]
        lay_plates["eyebrow"] = it["parts"]
    n = len(lines)
    for k, (ln, x, b) in enumerate(zip(lines, xs, geo["baselines"])):
        gold = k in gold_ks and not gold_from
        it = card.text(f"title_{k}", ln, "Anton", None, P, GOLD if gold else BONE, x, b, hook, "display", rise=False,
                       halo_px=0, gold_from=gold_from if k == gold_k else None)
        plates += it["parts"]
        for p in it["parts"]:
            lay_plates["title_2" if p.fill == GOLD else "title_1"].append(p)
    if not lay_plates["title_2"]:
        del lay_plates["title_2"]
    lay.meta["plates"].update(lay_plates)
    prov = None
    if spec.photo_kind == "generated":
        # the plate ends DISCLOSURE_AIR above the block (round 1 review 11: 16 px made it read as the claim's eyebrow)
        lay.meta["composition"] = {"image_box": [84, 0, 996, block_top - DISCLOSURE_AIR + 24]}
        _provenance(ground0.convert("RGBA"), lay)
        prov = lay.meta["provenance"]["plate"]
        if prov[1] < mast_foot + MASTHEAD_FOOT_PAD:
            raise CardRefused("the disclosure would collide with the masthead")
        md.rectangle([prov[0], prov[1], prov[2], prov[3]], fill=INK + (255,))
        pp = lay.meta["plates"]["provenance"][0]
        if pp.contrast is None or pp.contrast < HOOK_TARGET:
            raise CardRefused(f"the disclosure reaches only {pp.contrast}")
        card._el("provenance_plate", "mark", "", "", None, 0, INK, prov[:2], "", 0.0, tuple(prov), hook)
        card.roles["provenance_plate"] = "mark"
        card._el("provenance", "text", pp.text, DATA_FONT, 600, pp.font.size, STONE_LT, pp.xy, "ls", 1.0, pp.box, hook)
        card.roles["provenance"], card.drawn["provenance"] = "data", pp.text
        plates.append(pp)
    r_ = {"hook": True, "bg": bg, "under": under, "marks": marks, "top": top, "plates": plates, "last_t": 0.0}
    frame0 = _hook_compose(r_, 0)
    proof = grid_proof(frame0, lay)
    if proof["hook_fails"]:
        raise CardRefused("profile grid: " + "; ".join(proof["hook_fails"]))
    nontext = []
    dmask = mark_mask(size, lambda d: d.polygon(diamond, fill=255))
    diamond_c = nontext_contrast(frame0, dmask, ground=ground0)
    if diamond_c is None or diamond_c < NONTEXT_MIN:
        raise CardRefused(f"the masthead's diamond reaches only {diamond_c}")
    if rule:
        mask = mark_mask(size, lambda d: d.rectangle([rule[0], rule[1], rule[2] - 1, rule[3] - 1], fill=255))
        v = nontext_contrast(frame0, mask, ground=ground0)
        if v is None or v < NONTEXT_MIN:
            raise CardRefused(f"the eyebrow rule reaches only {v}")
        nontext.append(("eyebrow_rule", mask))
    frames, seconds = _frames(HOOK_SECONDS)
    contrast = [("brand", brand_plate)] + [(nm, p) for nm, p in card.contrast]
    if spec.photo_kind == "generated":
        contrast.append(("provenance", lay.meta["plates"]["provenance"][0]))
    # H5: the drawn contrast, measured on frames 0 and 32 as the chapter and myth cards measure theirs
    drawn = {}
    frame32 = _hook_compose(r_, 32)
    for nm, p in contrast:
        for pl in (p.parts if isinstance(p, _LinePlate) else [p]):
            vals = [v for v in (pl.measure(frame0), pl.measure(frame32)) if v is not None]
            if vals:
                drawn[nm] = round(min(min(vals), drawn.get(nm, 99.0)), 2)
    drawn_min = min(drawn.values()) if drawn else None
    meta = {"anchors": {"P": P, "lines": n, "baselines": geo["baselines"], "T": geo["T"], "E": geo["E"] if eyebrow
                        else None, "block_top": block_top, "L": L, "position": position},
            "caption_clear_until_s": CAPTION_CLEAR, "grid_proof": proof, "withheld": withheld, "focus": focus,
            "scrim": scrim_rec, "mast_band": band_rec, "diamond_contrast": diamond_c, "eyebrow_dropped": eyebrow_dropped,
            "drawn_contrast": drawn, "drawn_contrast_min": drawn_min,
            "gold": {"line": gold_k, "lines": list(gold_ks), "from": gold_from},
            "eyebrow_alpha": HOOK_EYEBROW_ALPHA if eyebrow else None,
            "upscale": round(up, 3), "_r": r_, "_contrast": contrast, "_marks": nontext, "_ground0": ground0,
            "rest_s": 0.0, "provenance": provenance}
    return card.plan("hook", seconds, frames, tuple(dict.fromkeys(tuple(spec.fact_ids) + cited)), meta)


def _over(im: Image.Image, layer: Image.Image, a: float) -> Image.Image:
    if a <= 0:
        return im
    if a < 1:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a)))
    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")


def _hook_compose(r: Mapping[str, Any], i: int) -> Image.Image:
    im = _pushed_in(r["bg"], 1.0 + LOOP_PUSH * i)
    t = i / VIDEO_FPS
    a, g = _hook_alpha(t), _hook_alpha(t, HOOK_GROUND_HOLD, HOOK_GROUND_FADE)
    if g <= 0:
        return im
    im = _over(im, r["under"], g)
    im = _over(im, r["marks"], g)
    if a <= 0:
        return im
    for p in r["plates"]:
        im = draw_plate(im, p, a * getattr(p, "alpha_scale", 1.0))
    return _over(im, r["top"], a)


# ------------------------------------------------------------------------------------------------ motion and frames
def _alpha(m: tuple, t: float) -> float:
    if m[0] == "hook":
        return _hook_alpha(t)
    if m[0] == "static":
        return 1.0
    if m[0] == "pen":
        return _ease_io((t - m[1]) / m[2])
    return _ease((t - m[1]) / (m[3] if len(m) > 3 else m[2]))    # a 4th value: the alpha's own ramp (C4)


def alpha_at(plan: CardPlan, name: str, t: float) -> float:
    """An element's visibility at `t`: a line's reveal alpha, a rule's wipe, a strike bar's drawn share."""
    return _alpha(plan.meta["_motion"][name], t)


def _paint(im: Image.Image, xy, text: str, font, fill: RGB, a: float) -> Image.Image:
    bb = ImageDraw.Draw(im).textbbox(xy, text, font=font, anchor="ls")
    x0, y0, x1, y1 = int(bb[0]) - 2, int(bb[1]) - 2, int(bb[2]) + 3, int(bb[3]) + 3
    if x1 <= x0 or y1 <= y0:
        return im
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).text((xy[0] - x0, xy[1] - y0), text, font=font, fill=int(255 * a), anchor="ls")
    im.paste(fill, (x0, y0, x1, y1), mask)
    return im


def frame_at(plan: CardPlan, i: int, *, freeze_drift: bool = False) -> Image.Image:
    """Frame `i` of the card (pure, deterministic). `freeze_drift`: the ground held at its last frame's drift."""
    r = plan.meta["_r"]
    if r.get("hook"):
        return _hook_compose(r, i)
    t = i / VIDEO_FPS
    k = plan.width / 1080 if plan.width < plan.height else min(plan.width / 1920, plan.height / 1080)
    im = r["drift"](r["last_t"] if freeze_drift else t).convert("RGB")
    motion = plan.meta["_motion"]
    for it in r["items"]:
        if it["type"] == "rect":
            p = _alpha(motion[it["name"]], t)
            if p <= 0:
                continue
            x0, y0, x1, y1 = it["box"]
            if it["wipe"] == "x":
                x1 = x0 + _rhu((x1 - x0) * p)
            elif it["wipe"] == "y":
                y1 = y0 + _rhu((y1 - y0) * p)
            if x1 > x0 and y1 > y0:
                im.paste(it["fill"], (x0, y0, x1, y1))
        elif it["type"] == "strike":
            c, kl = it["casing"], it.get("keyline", 0)
            fill = it.get("fill", GOLD)
            if it.get("back"):      # freeze panel M5: GOLD under the pen, then stepped back with the myth
                q = _ease((t - it["back"][0]) / it["back"][1])
                fill = tuple(_rhu(a + (b - a) * q) for a, b in zip(fill, it["fill_back"]))
            for nm, (x0, x1, top, bot) in zip(it["names"], it["segs"]):
                p = _alpha(motion[nm], t)
                if p <= 0:
                    continue
                xe = x0 + _rhu((x1 - x0) * p)
                for a, b in _dashes(x0, xe, it.get("dash"), x1):
                    if c:
                        im.paste(INK, (a - c, top - c, b + c, bot + c))
                    if kl:
                        im.paste(INK, (a, top - kl, b, top))
                        im.paste(INK, (a, bot, b, bot + kl))
                    im.paste(fill, (a, top, b, bot))
        else:
            a = _alpha(motion[it["name"]], t)
            if a <= 0:
                continue
            if it["settle"]:
                s0, sd = it["settle"]
                sc = FIGURE_SETTLE_FROM + (1 - FIGURE_SETTLE_FROM) * _ease((t - s0) / sd)
                if sc > 1.0005:
                    pl = it["parts"][0]
                    im = pl.draw_halo(im, a)
                    f = get_font(it["face"], max(8, _rhu(pl.font.size * sc)), weight=it["weight"])
                    px, py = it["pivot"]
                    if py != pl.xy[1]:      # pivot on the ink top: scale the baseline about it
                        xy = (px + (pl.xy[0] - px) * sc, py + (pl.xy[1] - py) * sc)
                    else:
                        xy = (px + (pl.xy[0] - px) * sc, pl.xy[1])
                    im = _paint(im, xy, pl.text, f, pl.fill, a)
                    continue
            m = motion[it["name"]]
            rp = _ease((t - m[1]) / m[2]) if (m[0] == "ease" and len(m) > 3) else a     # the rise keeps its motion
            dy = _rhu(RISE * (1 - rp) * k) if it["rise"] else 0
            back = _ease((t - it["back"][0]) / it["back"][1]) if it.get("back") else 0.0
            if back < 0.999:
                for pl in it["parts"]:
                    im = draw_plate(im, pl, a * (1 - back), dy)
            if back > 0.001:
                for tw in it["twins"]:
                    im = draw_plate(im, tw, a * back, dy)
    return im


# ------------------------------------------------------------------------------------------------ render
def _sidecars(out: Path) -> dict[str, Path]:
    return {"mp4": out, "jpg": out.with_suffix(".jpg"), "contrast": Path(str(out) + ".contrast.json"),
            "design": Path(str(out) + ".design.json")}


def _refuse(kind: str, out: Path, e: CardRefused) -> None:
    log.warning("%s card refused (%s): %s", kind, out.name, e.reason)
    s = _sidecars(out)
    for key in ("mp4", "jpg", "contrast"):
        s[key].unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    s["design"].write_text(json.dumps({"kind": kind, "version": VERSION, "refused": e.reason}, indent=1,
                                      ensure_ascii=False), encoding="utf-8", newline="\n")


def _design_record(plan: CardPlan, poster: str) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "kind": plan.kind, "version": VERSION, "width": plan.width, "height": plan.height, "seconds": plan.seconds,
        "frames": plan.frames, "fact_ids": list(plan.fact_ids), "drawn_text": dict(plan.drawn_text),
        "elements": [{"name": e.name, "box": list(e.box), "px": e.px, "face": e.face, "weight": e.weight,
                      "fill": brand.hex_colour(e.fill), "t_in": e.t_in, "t_full": e.t_full} for e in plan.elements],
        "poster": poster, "refused": None}
    m = plan.meta
    if plan.kind == "hook":
        rec["caption_clear_until_s"] = m["caption_clear_until_s"]
        rec["grid_proof"] = m["grid_proof"]
        if m.get("withheld"):
            rec["withheld"] = m["withheld"]
    if m.get("provenance"):
        rec["provenance"] = m["provenance"]     # which lines are verbatim spans and which the fact gate passed
    if plan.kind == "mythfact":
        rec.update(never_first=True, min_seconds=m["min_seconds"], source_omitted=m["source_omitted"],
                   quality_notes=list(m.get("quality_notes") or []), source_host_raw=m.get("source_host_raw"))
    return rec


def _emit_graphic(plan: CardPlan, out: Path) -> Path:
    s = _sidecars(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    last = frame_at(plan, plan.frames - 1)
    rec = record_contrast(out, last, list(plan.meta["_contrast"]))
    if rec["min"] is None or rec["min"] < TEXT_CONTRAST:
        raise CardRefused(f"graded contrast {rec['min']} under {TEXT_CONTRAST}")
    nt = record_nontext(out, last, list(plan.meta["_marks"]), ground=plan.meta["_r"]["rest"])
    if nt is not None and (nt.get("nontext_min") is None or nt["nontext_min"] < NONTEXT_MIN):
        raise CardRefused(f"a rule, strike or divider reaches only {nt.get('nontext_min')}")
    _encode((frame_at(plan, i) for i in range(plan.frames)), out, plan.frames, plan.width, plan.height)
    s["design"].write_text(json.dumps(_design_record(plan, "last"), indent=1, ensure_ascii=False), encoding="utf-8",
                           newline="\n")
    return out


def _check_out(out: Path, kind: str) -> Path:
    out = Path(out)
    m = GRAPHIC_NAME.match(out.name)
    if not m or m.group(1) != kind:
        raise ValueError(f"{out.name}: a {kind} card is written as graphic_{kind}_<nn>.mp4")
    return out


def _render(kind: str, planner: Callable[..., CardPlan], spec, sheet, out: Path, width: int, height: int,
            payload: Mapping[str, Any] | None = None):
    out = _check_out(out, kind)
    try:
        return _emit_graphic(planner(spec, sheet, width, height, payload=payload), out)
    except CardRefused as e:
        _refuse(kind, out, e)
        return None


def render_chapter_card(spec: ChapterCard, sheet: dict, out: Path, width: int = 1080, height: int = 1920, *,
                            payload: Mapping[str, Any] | None = None) -> Path | None:
    return _render("chapter", plan_chapter_card, spec, sheet, out, width, height, payload)


def render_myth_fact(spec: MythFact, sheet: dict, out: Path, width: int = 1080, height: int = 1920, *,
                         payload: Mapping[str, Any] | None = None) -> Path | None:
    return _render("mythfact", plan_myth_fact, spec, sheet, out, width, height, payload)


def render_quote_card(spec: QuoteCard, sheet: dict, out: Path, width: int = 1080, height: int = 1920, *,
                          payload: Mapping[str, Any] | None = None) -> Path | None:
    return _render("quote", plan_quote_card, spec, sheet, out, width, height, payload)


def render_hook_frame(spec: HookFrame, sheet: dict, out_dir: Path, *,
                      payload: Mapping[str, Any] | None = None) -> Path | None:
    """out_dir/hook_frame.mp4 (1.5 s, the vignette kept), its poster frame 0, contrast and design records."""
    out = Path(out_dir) / "hook_frame.mp4"
    s = _sidecars(out)
    try:
        plan = plan_hook_frame(spec, sheet, payload=payload)
        out.parent.mkdir(parents=True, exist_ok=True)
        rows, vals = [], []
        work = out.parent / "stage_cache" / "contrast"
        graded_any = False
        for i in (0, 32):
            frame = frame_at(plan, i)
            graded = graded_frame(frame, work, vignette=True)
            graded_any = graded_any or graded is not None
            for name, p in plan.meta["_contrast"]:
                g = p.measure(graded) if graded is not None else None
                d = p.measure(frame)
                rows.append({"name": name, "frame": i, "text": p.text, "planned": p.contrast, "drawn": d,
                             "graded": g, "halo": p.halo, "box": list(p.box)})
                v = g if g is not None else d
                if v is not None:
                    vals.append(v)
        low = min(vals) if vals else None
        frame0 = frame_at(plan, 0)
        nt = []
        for name, mask in plan.meta["_marks"]:
            nt.append({"name": name, "drawn": nontext_contrast(frame0, mask, ground=plan.meta["_ground0"])})
        record = {"clip": out.name, "target": TEXT_CONTRAST, "graded": graded_any, "frames": [0, 32], "min": low,
                  "elements": rows, "nontext_target": NONTEXT_MIN, "nontext": nt,
                  "nontext_min": min([r["drawn"] for r in nt if r["drawn"] is not None] or [None]) if nt else None}
        s["contrast"].write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        if low is None or low < TEXT_CONTRAST:
            raise CardRefused(f"graded contrast {low} under {TEXT_CONTRAST} (vignette kept)")
        _encode((frame_at(plan, i) for i in range(plan.frames)), out, plan.frames, plan.width, plan.height)
        frame0.convert("RGB").save(s["jpg"], quality=90)
        s["design"].write_text(json.dumps(_design_record(plan, "first"), indent=1, ensure_ascii=False),
                               encoding="utf-8", newline="\n")
        return out
    except CardRefused as e:
        _refuse("hook", out, e)
        return None
