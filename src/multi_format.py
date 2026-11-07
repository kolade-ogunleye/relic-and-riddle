"""
src/multi_format.py
===================
Companion assets derived from the Reel's script package, for manual posting:
1. 3-frame Story sequence (1080x1920): hook, revelation, question. The poll itself is
   added as a native Instagram poll sticker when posting; frame 3 asks the question, draws no
   options or reply instruction, leaves POLL_STICKER_ROOM empty for the sticker, and the
   question/options are saved in companion_posting.json.
2. 7-slide carousel (1440x1800, 4:5): cover, matched-photo fact slides, closing question.
Stories and carousels are posted on their own schedule (src/post_schedule.py).
"""

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw

from .config import VIDEO_WIDTH, VIDEO_HEIGHT
from .typography import (balanced_wrap, cap_height, drawable, fit_font, fit_line, fit_text, cover_crop, get_font,
                         is_video, plan_text, record_contrast, spoken_units, text_size, tracked_width, wrap_text)
from . import brand

log = logging.getLogger(__name__)

# Brand tokens (src/brand.py, visual overhaul 2026-10-29): one gold across every surface, bone text, ink grounds.
GOLD = brand.GOLD
WHITE = brand.BONE
LIGHT = brand.BONE
INK = brand.INK


# The poll frame's room for the native poll sticker, which the operator places when posting (y range on the
# 1080x1920 frame). Nothing is drawn there: the frame asks the question and the sticker answers it. It drew its
# own A and B option boxes and "REPLY WITH A OR B" until 2026-10-29: fake poll controls and a set-reply ask,
# which the engagement-bait check itself flags ("Reply A").
POLL_STICKER_ROOM = (1300, 1560)

STORY_BODY_PX = 46      # the Story body's size; never shrunk below it (2026-10-25: 30 px read as ~11 pt; 40 until
                        # the overhaul of 2026-10-29, which sets it at the critic's measured 46-48 px, ~16 pt)
STORY_BODY_LINES = 3
STORY_BODY_WEIGHT = 600  # Montserrat SemiBold (2026-11-02 round 2: stated, so a default elsewhere cannot thin it)
STORY_TITLE_W = 860      # the title's measure: three lines at its design size before it shrinks (2026-11-02)
STORY_TITLE_LINES = 3
STORY_ONE_LINE = 0.86     # down to this share of the design size, each title line stays whole before it wraps
STORY_FIGURE_RATIO = 2.0  # the reveal's hero figure against the title's design size (150 -> 300 px)
STORY_FIGURE_PAIR = 0.3   # the qualifier and the unit, one size, against the figure's
STORY_FIGURE_GAP = 18     # qualifier to figure and figure to unit, cap to cap (28 and 22 before round 3)
STORY_INLINE_GAP = 0.45   # an inline qualifier to its numeral, as a share of the qualifier's cap height (round 4)
# The poll frame's sticker room sits directly under the question (2026-11-02 round 2: at y 1300-1560 it was half a
# screen from the words it answers), on a quiet ink panel, nothing drawn in it; recorded per frame in layout.json
# (`poll_sticker_room`) and as the frame's protected region. Every frame still leaves POLL_STICKER_ROOM empty.
# Round 3 (2026-11-02): the room is Instagram's default poll footprint (about 760 x 300 on the 1080 frame), centred on
# the text column, and sits on a feathered ink band built with scrim(), not a hard-edged rectangle (a flat grey box on
# Richat's grey photo; hard top and bottom edges across Derinkuyu's architecture). The band is full strength across
# the room, fades over STORY_STICKER_FEATHER px above and below, and is only as deep as the white sticker needs: the
# band's 95th-percentile luma brought to STORY_STICKER_TARGET (about L* 40). Its box is written to
# companion_posting.json (story_poll_sticker.position) so the operator places the sticker on it.
STORY_STICKER_H = 300
STORY_STICKER_W = 760
STORY_STICKER_GAP = 36
STORY_STICKER_FEATHER = 120
STORY_STICKER_TARGET = 0.37
_QUALIFIER = re.compile(r"^(ABOUT|APPROX\.?|APPROXIMATELY|ROUGHLY|NEARLY|ALMOST|SOME|OVER|UP TO|MORE THAN|AROUND)$")


_RANGE_DASH = re.compile(r"(?<![\w.,-])(\d[\d,]*(?:\.\d+)?)\s*(?:-|\u2014|\s\u2013\s)\s*(\d[\d,]*(?:\.\d+)?)(?![\d-])")


def typeset(text: str) -> str:
    """Display typography for a drawn string, glyphs only (round 3, 2026-11-02): never a word or a figure changed.
    A numeric span takes an en dash with no spaces ("94 - 104" -> "94\u2013104"; "25-MILE" is no span and stays);
    straight apostrophes and quotes become curly ("EARTH'S" -> "EARTH\u2019S"); three dots become an ellipsis."""
    if not text:
        return text or ""
    t = text.replace("...", "\u2026")
    t = _RANGE_DASH.sub(lambda m: f"{m.group(1)}\u2013{m.group(2)}", t)
    t = re.sub(r"(?<=\w)'|'(?=\d0s\b)", "\u2019", t)          # EARTH'S, the '60s
    t = re.sub(r"'(?=\w)", "\u2018", t)                       # an opening single quote
    t = re.sub(r"'", "\u2019", t)
    t = re.sub(r'(^|[\s(\[\u2014\u2013])"', "\\1\u201c", t)   # an opening double quote after a space or bracket
    return t.replace('"', "\u201d")


def story_display(line: str) -> str:
    """A Story title line as display type: no tilde glyph ("~280" -> "ABOUT 280") and a figure's unit in its
    short form ("280 FEET DEEP" -> "280 FT DEEP"); the figure itself is never changed."""
    from .typography import _FIGURE, _figure_ok, _unit
    line = re.sub(r"~\s*(?=\d)", "ABOUT ", line or "")

    def short(m):
        info = _unit(m.group("unit"))
        if not info or not _figure_ok(m) or m.group("sep") == "-":   # "THE 25-MILE" stays an adjective
            return m.group(0)
        return f"{m.group('num')}{m.group('plus')} {info[3].upper()}"
    return " ".join(_FIGURE.sub(short, line).split())


def story_figure(line1: str, line2: str):
    """The reveal frame's hero: {"number", "unit", "qualifier", "title"} when a title line carries a measured
    figure (a number with its unit), else None. The unit line keeps what the line says of the figure
    ("FT DEEP"); a qualifier before it ("ABOUT") is kept, never dropped; the other line is the title."""
    from .cover_design import monument_figure
    span = _story_span(line1, line2)
    if span is not None:      # a stated span is the hero whole ("94\u2013104"), never its first number (round 4)
        return span or None
    found = monument_figure({"line1": line1, "line2": line2})
    if not found or not found[1] or found[2] not in ("line1", "line2"):
        return None
    number, unit, field, (a, b) = found
    text = line1 if field == "line1" else line2
    prefix, suffix = text[:a].strip(), text[b:].strip()
    if prefix and not _QUALIFIER.match(prefix):
        return None   # words before the figure ("THE 144-FOOT DOME") read as a phrase: the frame stays a title
    return {"number": number, "unit": " ".join(x for x in (unit, suffix) if x), "qualifier": prefix,
            "title": line2 if field == "line1" else line1}


def _story_span(line1: str, line2: str):
    """story_figure for a title line that states a span ("94\u2013104 MILLION YEARS OLD", "BETWEEN 8 AND 9 KM WIDE"):
    the hero dict with the span whole as its number (round 4, 2026-11-02: ranges are shown whole, never rounded to
    one figure); {} when a line states a span the frame cannot set as its hero (words before it), so the frame stays
    the two-tone title; None when neither line states a span."""
    for field, text in (("line1", line1 or ""), ("line2", line2 or "")):
        for m in _SPAN.finditer(text):
            lo = m.group("a") or m.group("a2")
            if float(lo.replace(",", "")) >= float(m.group("b").replace(",", "")):
                continue
            prefix, suffix = text[:m.start()].strip(), text[m.end():].strip()
            if prefix and not _QUALIFIER.match(prefix):
                return {}
            unit = " ".join(m.group("unit").upper().split())
            return {"number": f"{lo}\u2013{m.group('b')}", "unit": " ".join(x for x in (unit, suffix) if x),
                    "qualifier": prefix, "title": line2 if field == "line1" else line1, "span": True}
    return None


STORY_MARGIN = 84       # the Story's text column starts here, 19 px inside the safe box (2026-10-29: left-aligned)


def story_body_width() -> int:
    """The Story text column: the safe box less STORY_MARGIN's inset on each side (912 px at 1080x1920)."""
    from .safe_zones import safe_box
    sx0, _, sx1, _ = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
    inset = STORY_MARGIN - sx0
    return (sx1 - inset) - (sx0 + inset)


def poll_room_box(y0: float, height: int = STORY_STICKER_H) -> tuple[int, int, int, int]:
    """The poll sticker's room from `y0`: STORY_STICKER_W wide (no wider than the text column), centred on the
    Story's text column."""
    col = story_body_width()
    w = min(STORY_STICKER_W, col)
    cx = STORY_MARGIN + col / 2
    return (round(cx - w / 2), round(y0), round(cx + w / 2), round(y0 + height))


def scrim(im: Image.Image, y0: float, y1: float, feather: int = 300, target: float = 0.30, cap: float = 0.90,
          floor: float = 0.18, colour=None, pct: float = 95) -> Image.Image:
    """An ink gradient behind one text band, deep only as far as the band's own brightness needs (its `pct`
    percentile luminance brought toward `target`), fading over `feather` px above and below: never a box and
    never the whole frame (visual overhaul, 2026-10-29: a 35% shade over every Story and a fixed vignette band
    left bright stone at 3:1 and dark photos darker than they needed). Each line's own halo (place_text) does
    the rest, line by line."""
    import numpy as np
    W, H = im.size
    colour = colour or INK
    a = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    band = a[max(0, int(y0)):max(int(y0) + 1, int(y1))] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    p = float(np.percentile(band, pct)) if band.size else 0.0
    strength = float(np.clip(1 - target / max(p, 1e-3), floor, cap))
    y = np.arange(H, dtype=np.float32)
    v = np.where((y >= y0) & (y <= y1), 1.0, np.where(y < y0, 1 - (y0 - y) / feather, 1 - (y - y1) / feather))
    v = np.clip(v, 0, 1)
    v = v * v * (3 - 2 * v)
    mask = Image.fromarray((v * 255 * strength).astype(np.uint8).reshape(H, 1)).resize((W, H))
    layer = Image.new("RGBA", im.size, tuple(colour) + (255,))
    layer.putalpha(mask)
    out = Image.alpha_composite(im.convert("RGBA"), layer)
    return out if im.mode == "RGBA" else out.convert(im.mode)


# Ground-aware legibility (round 4, 2026-11-02), measured and fail-closed. A Story's text band is shaded deeper while
# (a) the texture behind any of its lines is busy (the high-pass RMS of the luma in a ring round the line, on the
# shaded frame, above GROUND_ENERGY_MAX), or (b) a gold line sits on a ground of its own hue (within GOLD_HUE_SEP
# degrees of brand.GOLD at a similar chroma, for GOLD_CLASH_SHARE of its ring: hue gives no separation there). The
# band stays the feathered gradient (never a box: boxes read as caption bars on a Story). A gold line still on a
# gold ground at the deepest step is set in bone. What was measured and chosen is the frame's `ground` record.
GROUND_ENERGY_MAX = 9.0
GROUND_RING = 14
GOLD_HUE_SEP = 25.0
GOLD_CLASH_SHARE = 0.10
STORY_SCRIM_STEPS = (0.28, 0.2, 0.13, 0.08)


def _ring(im: Image.Image, box, pad: int = GROUND_RING) -> Image.Image:
    W, H = im.size
    x0, y0, x1, y1 = (int(v) for v in box)
    return im.crop((max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)))


def ground_energy(im: Image.Image, box) -> float:
    """The texture behind a line: the RMS of the luma less its 6 px blur, over the line's box and GROUND_RING px
    round it (0-255 luma units; a flat ground is 0)."""
    import numpy as np
    from PIL import ImageFilter
    g = _ring(im.convert("L"), box)
    if not g.width or not g.height:
        return 0.0
    a = np.asarray(g, dtype=np.float32)
    b = np.asarray(g.filter(ImageFilter.GaussianBlur(6)), dtype=np.float32)
    return round(float(np.sqrt(np.mean((a - b) ** 2))), 2)


def gold_clash(im: Image.Image, box, gold=None) -> float:
    """The share of the ground round `box` whose hue is within GOLD_HUE_SEP degrees of `gold` (brand.GOLD) at a
    similar chroma (at least half its saturation) and not dark: where gold type has no hue separation."""
    import colorsys

    import numpy as np
    gold = gold or GOLD
    gh, gs, _ = colorsys.rgb_to_hsv(*(c / 255 for c in gold))
    hsv = np.asarray(_ring(im.convert("RGB"), box).convert("HSV"), dtype=np.float32) / 255.0
    if not hsv.size:
        return 0.0
    dh = np.abs(hsv[..., 0] - gh)
    dh = np.minimum(dh, 1 - dh) * 360
    near = (dh <= GOLD_HUE_SEP) & (hsv[..., 1] >= gs * 0.5) & (hsv[..., 2] >= 0.3)
    return round(float(near.mean()), 3)


def story_ground(base: Image.Image, d, rows, x: float, top: float, block: float):
    """The Story's text band shaded (scrim) only as deep as its ground needs (GROUND_ENERGY_MAX, GOLD_CLASH_SHARE),
    trying STORY_SCRIM_STEPS in turn; returns (image, rows, record). A gold row still clashing at the deepest step
    is set in bone (its fill changed, nothing else)."""
    def boxes(gold: bool):
        return [d.textbbox((x + r[6], top + r[1]), r[2], font=r[3], anchor="ls") for r in rows
                if r[2] and ((r[4] == GOLD) if gold else True)]
    every, golds = boxes(False), boxes(True)
    for target in STORY_SCRIM_STEPS:
        shaded = scrim(base, top - 40, top + block + 30, feather=320, target=target)
        energy = max((ground_energy(shaded, b) for b in every), default=0.0)
        clash = max((gold_clash(shaded, b) for b in golds), default=0.0)
        if energy <= GROUND_ENERGY_MAX and clash < GOLD_CLASH_SHARE:
            break
    record = {"scrim_target": target, "energy": energy, "gold_clash": clash, "gold_to_bone": False}
    if clash >= GOLD_CLASH_SHARE:
        rows = [(r[0], r[1], r[2], r[3], WHITE if r[4] == GOLD else r[4], r[5], r[6]) for r in rows]
        record["gold_to_bone"] = True
        log.info("Story gold line set in bone: its ground is gold (%.0f%% of its ring).", clash * 100)
    return shaded, rows, record


WARM_HUE = (15, 62)      # degrees: ochre, gold and amber grounds, where the brand gold has no hue contrast
WARM_SHARE = 0.45


def warm_ground(im: Image.Image, box) -> bool:
    """Whether the pixels in `box` are mostly a warm or gold hue (WARM_HUE, saturated and not dark) for at least
    WARM_SHARE of them."""
    import numpy as np
    hsv = np.asarray(im.convert("RGB").crop(tuple(int(v) for v in box)).reduce(4).convert("HSV"), dtype=np.float32)
    if not hsv.size:
        return False
    hue = hsv[..., 0] * 360 / 255
    warm = (hue >= WARM_HUE[0]) & (hue <= WARM_HUE[1]) & (hsv[..., 1] > 64) & (hsv[..., 2] > 70)
    return float(warm.mean()) >= WARM_SHARE


def masthead_scrim(im: Image.Image, box, text_rgb, target: float | None = None, feather: int = 150) -> Image.Image:
    """A soft ink gradient from the top edge down past `box` (the masthead's cap box), only where the pixels behind
    the masthead hold it under `target` (MASTHEAD_TARGET) and only as deep as that needs; unchanged otherwise."""
    target = MASTHEAD_TARGET if target is None else target
    import numpy as np
    x0, y0, x1, y1 = (int(v) for v in box)
    measured = (x0, max(0, y0 - 6), x1, y1 + 6)
    if _contrast(im, measured, text_rgb) >= target:
        return im
    W, H = im.size
    reach = min(H, y1 + feather)                         # the gradient's foot: nothing shaded below it
    y = np.arange(H, dtype=np.float32)
    v = np.clip(1 - y / reach, 0, 1)
    ramp = v * v * (3 - 2 * v)                           # full at the top edge, easing out to nothing at `reach`
    at_box = float(ramp[min(H - 1, y1)])

    def shaded(strength: float) -> Image.Image:
        mask = Image.fromarray((ramp * 255 * strength).astype(np.uint8).reshape(H, 1)).resize((W, H))
        layer = Image.new("RGBA", im.size, tuple(INK) + (255,))
        layer.putalpha(mask)
        return Image.alpha_composite(im.convert("RGBA"), layer).convert(im.mode)
    cap = min(1.0, 0.85 / max(at_box, 1e-3))
    lo, hi = 0.0, cap
    for _ in range(8):                                   # the least strength that brings the box to `target`
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if _contrast(shaded(mid), measured, text_rgb) >= target else (mid, hi)
    return shaded(hi)


KICKER_TRACK = 0.15     # a kicker's letter spacing, as a share of its size (Montserrat 800, tracked caps)
KICKER_RULE = 1.0       # the kicker's gold rule: as long as the capitals are tall
KICKER_GAP = 0.55       # and this far from the words
# Planning margins (round 3, 2026-11-02): small gold kickers and the masthead are planned at 5.5:1 so Facebook's
# downscale and recompression (960 px, JPEG ~70) still leave them at 4.5 (the FB copy measured 5.01 and 4.83).
KICKER_TARGET = 5.5
MASTHEAD_TARGET = 5.5
PLATFORM_WIDTH, PLATFORM_QUALITY = 960, 70   # the platform re-encode every delivered ratio is measured after too


def kicker_width(text: str, font) -> float:
    """A kicker's drawn width: its gold rule and gap, then the tracked text."""
    ch = cap_height(font)
    return KICKER_RULE * ch + KICKER_GAP * ch + tracked_width(text, font, font.size * KICKER_TRACK)


def draw_kicker(im: Image.Image, x: float, baseline: float, text: str, font, colour=None, lay=None,
                name: str = "kicker", plates=None) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """A kicker: a short gold rule at mid-cap height, then the words in tracked gold capitals (the covers'
    eyebrow). A label, never a pill or a button. A cap-height vertical bar read as a letter I before the words
    ("I DID YOU KNOW?", 2026-10-29), so the rule lies flat."""
    colour = colour or GOLD
    ch = cap_height(font)
    bar_w, gap = max(8, round(ch * KICKER_RULE)), round(ch * KICKER_GAP)
    thick = max(3, round(ch * 0.16))
    mid = baseline - ch / 2
    d = ImageDraw.Draw(im)
    d.rectangle([round(x), round(mid - thick / 2), round(x) + bar_w - 1, round(mid - thick / 2) + thick - 1],
                fill=tuple(colour))
    plate = plan_text(im, (x + bar_w + gap, baseline), text, font, colour, "ls", track=font.size * KICKER_TRACK,
                      target=KICKER_TARGET)
    im = plate.draw(im)
    box = (int(round(x)), min(plate.box[1], int(round(baseline - ch))), plate.box[2], max(plate.box[3], int(round(baseline))))
    if lay is not None:
        lay.add(name, box)
        lay.elements[-1].update(plate.record())
        if isinstance(getattr(lay, "meta", None), dict):   # the rule's own box (the swipe strip meets it)
            lay.meta[f"{name}_rule"] = [round(x), round(mid - thick / 2), round(x) + bar_w, round(mid - thick / 2) + thick]
    if plates is not None:
        plates.setdefault(name, []).append(plate)
    return im, box


def _fit_kicker(text: str, font, width: float) -> Tuple[str, bool]:
    """A kicker that fits `width` at this size: whole, else shortened at a word with an ellipsis (logged)."""
    if not text or kicker_width(text, font) <= width:
        return text, False
    words = text.split()
    while len(words) > 1 and kicker_width(" ".join(words) + "…", font) > width:
        words.pop()
    log.warning("Kicker shortened to fit %d px: %s", width, text)
    return " ".join(words) + "…", True


def story_body_lines(body: str, asks_poll: bool = False) -> int:
    """How many lines a Story frame's body takes at its fixed size, as the renderer draws it (the poll frame
    drops the body's own questions)."""
    if asks_poll:
        body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))
    if not body.strip():
        return 0
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    return len(wrap_text(d, " ".join(body.split()), get_font("Montserrat", STORY_BODY_PX), story_body_width()))


_AUXILIARY = re.compile(r"^(?:would|will|do|does|did|is|are|was|were|can|could|should|shall|have|has|had|may|might|"
                        r"must)\b", re.IGNORECASE)
_YES_NO = re.compile(r"^\W*(?:yes|no|yeah|nope|definitely|never)\b", re.IGNORECASE)


def poll_problems(poll: dict[str, Any]) -> list[str]:
    """What breaks the poll's own rule, "never a yes/no question" (round 4, 2026-11-02): options that answer yes or
    no ("Yes, safety first" / "No, I need daylight"), or a question opening on an auxiliary verb ("Would you ...?")
    that offers no "or" between choices. [] for a real either/or poll."""
    question = " ".join(str(poll.get("question") or "").split())
    options = [str(o) for o in poll.get("options") or []]
    out = []
    if sum(bool(_YES_NO.match(o)) for o in options) >= 2:
        out.append("the options answer yes or no")
    if question and _AUXILIARY.match(question) and not re.search(r"\bor\b", question, re.IGNORECASE):
        out.append("the question asks for yes or no")
    return out


# Words a line should not end on (round 4 review, 2026-11-02): an article, a conjunction or a short preposition
# leaves the reader holding a word that belongs to the next line ("Tell us in the comments and / follow ...").
_DANGLING = frozenset(("a", "an", "the", "and", "or", "but", "nor", "so", "yet", "of", "in", "on", "at", "to", "for",
                       "with", "from", "by", "as", "into", "onto", "than", "via", "per"))
RAG_DANGLE = 0.3   # a line ending on one of them costs as much as a line this share of `width` off the mean


def dangles(line: str) -> bool:
    """True when the line ends on a word in _DANGLING (punctuation after it means it closes a phrase: not counted)."""
    words = line.split()
    return bool(words) and words[-1].lower() in _DANGLING


def even_rag(d, lines: list[str], font, width: float, measure: int) -> list[str]:
    """The same words on the same number of lines, broken for the evenest rag (round 4, 2026-11-02): the least
    spread of line widths, every line within `width` px and `measure` characters, never a one-word last line where
    the given lines avoid it. A short first line over a full second ("Visible from space, the") is evened out.
    A line that ends on an article, conjunction or short preposition (dangles) costs RAG_DANGLE of the width
    squared, in the given lines too, so an even rag never buys its evenness with a dangling "and".
    Words split at ordinary spaces only, so a figure glued to its unit stays whole. Unchanged when no other break
    is better."""
    from itertools import combinations
    words = " ".join(lines).split(" ")
    n = len(lines)
    if n < 2 or len(words) > 24 or len(words) <= n:
        return lines
    widths = {}

    def w(a: int, b: int) -> float:
        if (a, b) not in widths:
            widths[(a, b)] = d.textlength(" ".join(words[a:b]), font=font)
        return widths[(a, b)]

    def cost(cand):
        ws = [w(a, b) for a, b in cand]
        mean = sum(ws) / len(ws)
        # A last line longer than the one above it reads as an inverted rag: counted as much again.
        dangle = sum(dangles(" ".join(words[a:b])) for a, b in cand[:-1]) * (RAG_DANGLE * width) ** 2
        return sum((x - mean) ** 2 for x in ws) + max(0.0, ws[-1] - ws[-2]) ** 2 + dangle
    best, best_cost = None, None
    for cuts in combinations(range(1, len(words)), n - 1):
        bounds = list(zip((0,) + cuts, cuts + (len(words),)))
        if any(w(a, b) > width or len(" ".join(words[a:b])) > measure for a, b in bounds):
            continue
        if bounds[-1][1] - bounds[-1][0] == 1 and len(lines[-1].split(" ")) > 1:
            continue
        c = cost(bounds)
        if best_cost is None or c < best_cost - 1e-6:
            best, best_cost = bounds, c
    if best is None:
        return lines
    out = [" ".join(words[a:b]) for a, b in best]
    return out if cost(best) < cost(list(_bounds(lines))) - 1e-6 else lines


def _bounds(lines: list[str]):
    k = 0
    for ln in lines:
        m = len(ln.split(" "))
        yield (k, k + m)
        k += m


def split_balanced(text: str) -> Tuple[str, str]:
    """Splits a headline into two lines of similar length at a word boundary."""
    words = text.split()
    if len(words) < 2:
        return text, ""
    best = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
    return drop_break_comma(" ".join(words[:best])), " ".join(words[best:])


def drop_break_comma(line: str) -> str:
    """A display line that a break ends drops its trailing comma: the break (and, two-tone, the colour change)
    already makes the pause, and at the headline's tight leading the comma's tail lands on the next line's cap
    height ("CARVED," over "NOT BUILT" read as one joined mark). Other end punctuation is kept."""
    return line[:-1].rstrip() if line.endswith(",") else line


def _rel_luminance(rgb) -> float:
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ensure_legible(im: Image.Image, box: Tuple[int, int, int, int], text_rgb=(235, 235, 235),
                   target: float = 4.5, feather: int = 60) -> Image.Image:
    """Darkens the area behind a block of light text until the text reaches WCAG contrast `target`
    against the bright end of the background there (its 90th-percentile luminance), with feathered
    edges so no box shows. Bright photos get a deeper shade; dark ones are left alone."""
    import numpy as np
    x0, y0, x1, y1 = [int(v) for v in box]
    region = np.asarray(im.convert("RGB").crop((x0, y0, x1, y1)), dtype=np.float32) / 255.0
    weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

    def bright_end(alpha: float) -> float:  # 90th-percentile luminance once shaded by `alpha`
        v = region * (1.0 - alpha)
        lin = np.where(v <= 0.03928, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
        return float(np.percentile(lin @ weights, 90))

    needed = (_rel_luminance(text_rgb) + 0.05) / target - 0.05  # the brightest background that still gives `target`
    if bright_end(0.0) <= needed:
        return im
    lo, hi = 0.0, 0.92
    for _ in range(12):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if bright_end(mid) <= needed else (mid, hi)
    alpha = min(0.92, hi + 0.03)
    from PIL import ImageChops, ImageFilter
    level = int(round(255 * alpha))
    box_only, grown = Image.new("L", im.size, 0), Image.new("L", im.size, 0)
    ImageDraw.Draw(box_only).rectangle([x0, y0, x1, y1], fill=level)
    ImageDraw.Draw(grown).rectangle([x0 - feather, y0 - feather, x1 + feather, y1 + feather], fill=level)
    # Full strength over the whole box (text may sit at its edges), fading out over `feather` px outside it
    shade = ImageChops.lighter(grown.filter(ImageFilter.GaussianBlur(feather / 3)), box_only)
    black = Image.new("RGB", im.size, (0, 0, 0))
    return Image.composite(black, im.convert("RGB"), shade)


SHARE_WEIGHT = 700   # the closing slide's share-and-follow line: Montserrat Bold (round 4). The panel asked for
                     # SemiBold, but every Montserrat line is already 600 (typography.VARIABLE_WEIGHTS), the question
                     # with it; one step heavier is what sets the ask apart as a second voice.

_render_state = threading.local()   # the slide being drawn on this thread: its subject


class MultiFormatGenerator:
    def generate_story_sequence(self, hero_img_path: Path, script_payload: Dict[str, Any], output_dir: Path,
                                scene_images: List[Path] = None) -> List[Path]:
        """Three Story frames, each on its own matched photo: hook, reveal, and the poll's question
        (the native poll sticker can't be posted through the API: the operator adds it in POLL_STICKER_ROOM,
        which the frame keeps empty; the frame draws no options of its own and asks for no set reply).
        Text sits on the calmest band of the photo inside Story-safe zones (clear of the profile bar
        at the top and the reply bar at the bottom), over a soft gradient rather than a box."""
        out_dir = output_dir / "stories"
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in (scene_images or []) if not is_video(p)] or [hero_img_path]
        slides_data = script_payload["story_slides"]
        cards = [{"heading": f"{s['line1']} {s['line2']}", "body": s["body"]} for s in slides_data]
        photos = self._match_photos(cards, stills) if len(stills) > 1 else [hero_img_path] * len(cards)
        names = ["story_01_hook.jpg", "story_02_reveal.jpg", "story_03_question.jpg"]
        poll = script_payload.get("poll") or {}
        # One unit per run: figures in the unit the narration speaks (2026-10-29); matched to photos as written.
        narration = script_payload.get("narration_script") or ""
        slides_data = [{k: (spoken_units(v, narration) if isinstance(v, str) else v) for k, v in s.items()}
                       for s in slides_data]
        if poll.get("question"):
            poll = dict(poll, question=spoken_units(poll["question"], narration))
        layout = []  # each frame's text block, for the audit and the evidence renders

        from . import still_layout
        slides = []
        for i, (slide, name) in enumerate(zip(slides_data, names)):
            recipe = {"renderer": "multi_format.redraw_story_frame", "photo": str(photos[i]), "slide": slide,
                      "index": i, "count": len(slides_data), "poll": poll}
            base, lay, attempts = still_layout.drawn(
                lambda tight: self._render_story_frame(photos[i], slide, i, len(slides_data), poll, tight=tight))
            layout.append({**lay.meta, "frame": name})
            path = out_dir / name
            base.save(path, quality=95)
            _delivered_contrast(lay, path)
            still_layout.save(path, lay, recipe, attempts)
            slides.append(path)
        (out_dir / "layout.json").write_text(json.dumps(layout, indent=2), encoding="utf-8")

        # Multi-platform publishing manifest with dedicated platform packages
        hashtags_list = script_payload.get("hashtags") or ["#Archaeology", "#AncientHistory", "#HistoryBuff", "#AncientTechnology"]
        hashtags_str = " ".join(hashtags_list)
        first_comment = script_payload.get("first_comment", "")
        title = script_payload.get("title", "")
        clean_title = re.sub(r"[^\w\s\-\:\,\']", "", title).strip()
        if len(clean_title) > 60:
            clean_title = clean_title[:57].rstrip() + "..."

        ig_caption = script_payload.get("caption", "")
        fb_caption = f"{clean_title}\n\n{first_comment}\n\nWeigh in with your thoughts in the comments."  # no "below" (operator, 2026-10-28)
        yt_desc = f"{title}\n\n{script_payload.get('narration_script', '')}\n\n{first_comment}\n\n{hashtags_str}"
        yt_tags = [h.lstrip("#").lower() for h in hashtags_list] + ["archaeology", "ancient history", "documentary", "history mysteries"]

        sticker_spec = dict(script_payload.get("poll") or {})
        if sticker_spec and poll_problems(sticker_spec):
            # Recorded for the operator (round 4): the script's rule is "never a yes/no question". Not yet a gate here.
            sticker_spec["problems"] = poll_problems(sticker_spec)
        room = next(((e["frame"], e.get("poll_sticker_room")) for e in layout if e.get("poll_sticker_room")), None)
        if sticker_spec and room:
            # Where the operator places the native sticker: the room the frame keeps for it (round 3, 2026-11-02).
            sticker_spec["position"] = {"frame": room[0], "box": room[1], "frame_size": [VIDEO_WIDTH, VIDEO_HEIGHT]}
        companion_manifest = {
            "story_poll_sticker": sticker_spec,
            "story_frames": [p.name for p in slides],
            # What each frame credits on itself (Stories carry no caption; story_credit, 2026-10-29)
            "story_credits": {e["frame"]: e["photo_credit"].get("lines", []) for e in layout},
            "first_comment": first_comment,
            "platforms": {
                "instagram_reels": {
                    "caption": ig_caption,
                    "hashtags": hashtags_list,
                    "first_comment": first_comment,
                    "recommended_sound": "Original Audio",
                },
                "facebook_reels": {
                    "caption": fb_caption,
                    "first_comment": first_comment,
                    "discussion_prompt": first_comment,
                },
                "youtube_shorts": {
                    "title": clean_title,
                    "description": yt_desc,
                    "tags": yt_tags,
                    "pinned_comment": first_comment,
                },
            },
            "note": "Multi-platform publishing manifest: native Instagram poll sticker specs, Facebook MSI discussion copy, and YouTube Shorts SEO metadata.",
        }

        with open(output_dir / "companion_posting.json", "w", encoding="utf-8") as f:
            json.dump(companion_manifest, f, indent=2, ensure_ascii=False)

        log.info("Generated %d Story frames -> %s", len(slides), out_dir)
        return slides

    def _render_story_frame(self, photo: Path, slide: Dict[str, Any], i: int, count: int, poll: Dict[str, Any],
                            tight: bool = False):
        """One Story frame and its measured layout (visual overhaul, 2026-10-29): a left-aligned editorial column
        at STORY_MARGIN, a kicker (gold bar, tracked gold capitals), a two-tone Anton title (bone, then gold), the
        Montserrat body in balanced lines, and on the first frame the pointer to the Reel. Every block is measured
        with the font's real metrics before anything is drawn and placed inside the Story box (safe_zones.STORY:
        Meta's 14% top, 35% bottom, 6% sides), on the calmest band of the photo; an adaptive ink gradient sits
        behind that band only, and each line is drawn at a measured contrast (typography.place_text: >= 4.5:1 on
        the pixels touching its glyphs), the ratio recorded with its box for the post-draw check (still_layout).
        `tight`: the second, tighter fit (smaller title and kicker). A photo whose licence asks for attribution is
        credited at the foot of the box (story_credit), recorded as the `photo_credit` element and in the frame's
        layout.json entry."""
        from .safe_zones import safe_box
        from .still_layout import Layout
        sx0, sy0, sx1, sy1 = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
        x = STORY_MARGIN
        max_w = story_body_width()
        asks_poll = i == count - 1 and len(poll.get("options", [])) >= 2
        lay = Layout("story", (VIDEO_WIDTH, VIDEO_HEIGHT), (sx0, sy0, sx1, sy1), tight=tight,
                     protected=[("poll_sticker_room", (0, POLL_STICKER_ROOM[0], VIDEO_WIDTH, POLL_STICKER_ROOM[1]))]
                     if asks_poll else None)
        lay.plates = {}
        base = cover_crop(photo, (VIDEO_WIDTH, VIDEO_HEIGHT)).convert("RGB")
        d = ImageDraw.Draw(base)
        # The photo's credit, when its licence asks for one (story_credit; 2026-10-29): at the foot of the Story box,
        # above the reply bar and clear of the poll sticker's room, which both sit lower. Set in the data face (IBM
        # Plex Mono), left-aligned on the text column; its backing is shaded only as far as 4.5:1 needs, before any
        # text is drawn; the frame's text block then stays above it.
        credit = dict(story_credit(photo))
        text_floor = sy1
        c_fill = brand.STONE_LT
        if credit["required"]:
            for c_px in range(STORY_CREDIT_PX, STORY_CREDIT_MIN_PX - 1, -2):  # at most three lines, never under 22 px
                font_c = get_font(brand.DATA_FONT, c_px, weight=500)
                c_drawn = [ln for line in credit["lines"] for ln in _credit_wrap(d, drawable(line, font_c), font_c, max_w)]
                if len(c_drawn) <= STORY_CREDIT_LINES:
                    break
            c_lead = round(c_px * STORY_CREDIT_LEADING / STORY_CREDIT_PX)
            c_bottom = sy1 - 10
            c_top = c_bottom - len(c_drawn) * c_lead
            c_w = max(text_size(d, ln, font_c)[0] for ln in c_drawn)
            backing = (x - 16, c_top - 8, x + c_w + 16, c_bottom + 6)
            before = _contrast(base, backing, c_fill)
            # A soft band of ink across the frame, not a box (a feathered rectangle read as a caption bar, 2026-10-29);
            # each line then gets its own halo if the photo still shows through too brightly.
            base = scrim(base, c_top - 10, c_bottom + 8, feather=90, target=0.16, cap=0.85, floor=0.0)
            d = ImageDraw.Draw(base)
            credit.update(drawn=c_drawn, px=c_px, backing=list(backing), contrast_unshaded=before,
                          contrast=_contrast(base, backing, c_fill),
                          complete=all(drawable(line, font_c) == line for line in credit["lines"]))  # no glyph missing
            text_floor = c_top - STORY_CREDIT_GAP
        kicker, l1, l2 = (typeset(slide[k]).upper() for k in ("kicker", "line1", "line2"))
        if asks_poll and poll.get("question") and not (l1.strip() or l2.strip()):
            # Round 4 (2026-11-02): the frame's headline is the slide's own lines (fact-checked with the slide) and the
            # native sticker asks the poll's question; the question is set as the headline only when the slide has
            # no lines (it duplicated the sticker word for word, and went on screen unchecked).
            l1, l2 = split_balanced(typeset(poll["question"]).upper())
        l1, l2 = story_display(l1), story_display(l2)
        body = typeset(slide["body"])
        if asks_poll:  # one question per frame: the poll's; the body keeps only its context
            body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))

        # Measured first: the kicker on one line; the title in Anton at its design size on up to STORY_TITLE_LINES
        # lines of STORY_TITLE_W (2026-11-02: three lines before it shrinks), bone then gold; the body at its fixed
        # size in balanced lines. The reveal frame with a measured figure is set as the covers' monument instead:
        # the figure huge in gold, its unit (and what the line says of it) under it, the other line as the title.
        k_px = 34 if tight else 40
        font_k = get_font("Montserrat", k_px, weight=800)
        while kicker and kicker_width(kicker, font_k) > max_w and font_k.size > 24:
            font_k = get_font("Montserrat", font_k.size - 2, weight=800)
        kicker, k_short = _fit_kicker(kicker, font_k, max_w)
        cap_k = cap_height(font_k)
        hero = story_figure(l1, l2) if i == 1 and not asks_poll else None
        b_step = round(STORY_BODY_PX * 1.36)
        b_gap = round(STORY_BODY_PX * 1.62)                       # title's baseline to the body's first baseline
        font_p = get_font("Montserrat", 32 if tight else 36, weight=800)
        sticker = STORY_STICKER_H if asks_poll else 0
        room = text_floor - (sy0 + 30)                           # the height the whole block may take

        def title_plan(size: int, wrap: bool = True):
            """(rows, height) of the title at `size`: rows of (name, baseline offset from the title's top, text,
            font, fill, x offset)."""
            out, y = [], 0
            if hero:
                # The qualifier and the unit are one matched pair (round 3, 2026-11-02): one face, one size, in bone, so
                # the numeral is the frame's single gold read and three type tiers no longer stack around one number.
                f_fig = fit_font(d, hero["number"], "Anton", round(size * STORY_FIGURE_RATIO), STORY_TITLE_W, 160)
                f_unit = get_font("Anton", max(56, round(f_fig.size * STORY_FIGURE_PAIR)))
                q_font = f_unit
                f_title = get_font("Anton", round(size * 0.66))
                for ln in balanced_wrap(d, hero["title"], f_title, STORY_TITLE_W) if hero["title"] else []:
                    y += cap_height(f_title) if not out else round(cap_height(f_title) * 1.13)
                    out.append(("title_1", y, ln, f_title, WHITE, 0))
                # Round 4 (2026-11-02): the qualifier sits inline before the numeral, on its baseline, at the unit's
                # size ("ABOUT 280"): on a line of its own it read as a one-word headline line. Stacked above the
                # numeral only when the inline pair is wider than the title's measure.
                q_w = d.textlength(hero["qualifier"], font=q_font) + round(cap_height(q_font) * STORY_INLINE_GAP) \
                    if hero["qualifier"] else 0
                inline = bool(hero["qualifier"]) and q_w + d.textlength(hero["number"], font=f_fig) <= STORY_TITLE_W
                if hero["qualifier"] and not inline:
                    y += (34 if out else 0) + cap_height(q_font)
                    out.append(("figure_qualifier", y, hero["qualifier"], q_font, WHITE, 0))
                y += (STORY_FIGURE_GAP if out else 0) + cap_height(f_fig)
                if inline:
                    out.append(("figure_qualifier", y, hero["qualifier"], q_font, WHITE, 0))
                out.append(("figure", y, hero["number"], f_fig, GOLD, round(q_w) if inline else 0))
                if hero["unit"]:
                    y += STORY_FIGURE_GAP + cap_height(f_unit)
                    # Under the numeral it measures (inline: indented past the qualifier, so "FT DEEP" reads with "280").
                    out.append(("figure_unit", y, hero["unit"], f_unit, WHITE, round(q_w) if inline else 0))
                return out, y
            f = get_font("Anton", size)
            for name, text, fill in (("title_1", l1, WHITE), ("title_2", l2, GOLD)):
                for ln in (balanced_wrap(d, text, f, STORY_TITLE_W) if wrap else [text]) if text else []:
                    y += cap_height(f) if not out else round(cap_height(f) * 1.13)
                    out.append((name, y, ln, f, fill, 0))
            return out, y

        def fits(plan) -> bool:
            rows_, _ = plan
            texts = [r for r in rows_ if not r[0].startswith("figure")]
            return (len(texts) <= (2 if hero else STORY_TITLE_LINES)
                    and all(r[5] + d.textlength(r[2], font=r[3]) <= STORY_TITLE_W for r in rows_))

        t_size, t_min = (124, 56) if tight else (150, 64)
        # Each title line whole while it fits at STORY_ONE_LINE of the design size or more; below that, back at
        # the design size on up to three lines; only then smaller. The body is kept whole where the box allows
        # it: the title gives way first; a body past any title's room keeps the script's three-line budget.
        trials = ([(sz, False) for sz in range(t_size, round(t_size * STORY_ONE_LINE) - 1, -2)]
                  + [(sz, True) for sz in range(t_size, t_min - 1, -2)])
        f_body = get_font("Montserrat", STORY_BODY_PX, weight=STORY_BODY_WEIGHT)
        n_body = len(wrap_text(d, " ".join(body.split()), f_body, max_w)) if body.strip() else 0

        def choose(sticker_h: int, lines: int):
            fixed_ = (cap_k + 30 if kicker else 0) + (110 if i == 0 else 0) + (
                sticker_h + 2 * STORY_STICKER_GAP if sticker_h else 0)
            need = (lines - 1) * b_step + b_gap + 12 if lines else 0
            for size_, wrap_ in trials:
                plan = title_plan(size_, wrap_)
                if fits(plan) and plan[1] + fixed_ + need <= room:
                    return size_, wrap_, fixed_
            return None
        got = choose(sticker, n_body)
        if got is None and sticker:
            # The question, the sticker's room under it and the whole body do not fit the box: the sticker goes
            # to POLL_STICKER_ROOM below the box, as before 2026-11-02, rather than the body being cut.
            sticker = 0
            got = choose(0, n_body)
        got = got or choose(sticker, min(n_body, STORY_BODY_LINES)) or (t_min, True, None)
        size, wrap, fixed = got
        if fixed is None:
            fixed = (cap_k + 30 if kicker else 0) + (110 if i == 0 else 0) + (
                sticker + 2 * STORY_STICKER_GAP if sticker else 0)
        if asks_poll and not sticker:
            lay.meta_room = [0, POLL_STICKER_ROOM[0], VIDEO_WIDTH, POLL_STICKER_ROOM[1]]
        t_rows, t_height = title_plan(size, wrap)
        s1 = s2 = False
        if not fits((t_rows, t_height)):   # even the floor does not fit: one line each, shortened with an ellipsis
            l1, font_t, s1 = fit_line(d, l1, "Anton", t_min, STORY_TITLE_W, t_min)
            l2, _, s2 = fit_line(d, l2, "Anton", t_min, STORY_TITLE_W, t_min)
            hero = None
            ct = cap_height(font_t)
            t_rows = [r for r in (("title_1", ct, l1, font_t, WHITE, 0),
                                  ("title_2", ct + round(ct * 1.13), l2, font_t, GOLD, 0)) if r[2]]
            t_height = t_rows[-1][1] if t_rows else 0
        n_room = max(1, (room - fixed - t_height - b_gap - 16) // b_step + 1)   # 16: the last line's descenders
        body_lines, font_b, b_short = [], get_font("Montserrat", STORY_BODY_PX, weight=STORY_BODY_WEIGHT), False
        # Never smaller than its design size (operator, 2026-10-25). The script stage keeps each body within three
        # lines at this size (story_body_lines); a longer one takes more lines while the box has room, and is cut
        # only when it has none (then at a sentence, else a word, with an ellipsis).
        if body.strip():
            body_lines, font_b, b_short = fit_text(d, body, "Montserrat", STORY_BODY_PX, max_w, STORY_BODY_LINES,
                                                   STORY_BODY_PX, quiet=True, weight=STORY_BODY_WEIGHT)
            if b_short:
                body_lines, font_b, b_short = fit_text(d, body, "Montserrat", STORY_BODY_PX, max_w,
                                                       max(STORY_BODY_LINES, n_room), STORY_BODY_PX,
                                                       weight=STORY_BODY_WEIGHT)
        lay.shortened = [n for n, short in (("kicker", k_short), ("title_1", s1), ("title_2", s2), ("body", b_short)) if short]

        # Baselines, relative to the block's top.
        rows: List[Tuple[str, float, str, Any, Any, float, int]] = []   # (name, baseline, text, font, fill, track, dx)
        y0 = cap_k + 30 if kicker else 0
        for name, yy, txt, f, fill, dx in t_rows:
            rows.append((name, y0 + yy, txt, f, fill, 0, dx))
        y = y0 + t_height
        sticker_at = None
        if sticker:   # the poll sticker's room, directly under the question (2026-11-02 round 2)
            sticker_at = y + STORY_STICKER_GAP
            y = sticker_at + sticker + STORY_STICKER_GAP - b_gap + cap_height(font_b)
        title_base = y
        for j, line in enumerate(body_lines):
            y = title_base + b_gap + j * b_step
            rows.append(("body", y, line, font_b, LIGHT, 0, 0))
        if i == 0:  # No arrow on the pointer (operator, 2026-10-28): the words say where to go.
            y += 96
            # In bone (2026-11-01): gold is the frame's one first read (the title's second line); the kicker's
            # gold rule and capitals are the brand's kicker, and a third gold line competed with both.
            rows.append(("reel_pointer", y, "FULL STORY IN OUR NEW REEL", font_p, WHITE, font_p.size * 0.11, 0))
        bottom = max([d.textbbox((x, yy), txt, font=f, anchor="ls")[3] for _, yy, txt, f, _, _, _ in rows]
                     + ([sticker_at + sticker] if sticker else []))
        block = bottom
        top_cand = sy0 + 40 + block // 2
        # One text anchor across the sequence (round 3, 2026-11-02): every frame's kicker sets at the same top y, so a
        # tap to the next frame keeps the eye in place (calm_center_y's per-frame choice set the hook 160-340 px lower
        # for no narrative reason). The scrim and each line's measured halo hold the contrast on any photo.
        cy = top_cand
        top = max(sy0 + 30, min(cy - block // 2, text_floor - block))   # above the photo credit, when there is one
        base, rows, ground = story_ground(base, d, rows, x, top, block)
        lay.ground = ground
        im = base.convert("RGBA")
        if sticker:
            room_box = poll_room_box(top + sticker_at, sticker)
            im = scrim(im, room_box[1], room_box[3], feather=STORY_STICKER_FEATHER, target=STORY_STICKER_TARGET,
                       cap=0.9, floor=0.0)
            lay.protected = [{"name": "poll_sticker_room", "box": [int(v) for v in room_box]}]
            lay.meta_room = [int(v) for v in room_box]
        if kicker:
            im, k_box = draw_kicker(im, x, top + cap_k, kicker, font_k, GOLD, lay, plates=lay.plates)
        by_name: dict[str, list] = {}
        for name, yy, txt, f, fill, track, dx in rows:
            plate = plan_text(im, (x + dx, top + yy), txt, f, fill, "ls", track=track)
            im = plate.draw(im)
            lay.plates.setdefault(name, []).append(plate)
            by_name.setdefault(name, []).append(plate)
        for name, plates_ in by_name.items():   # each element one block, in reading order
            record_contrast(lay, name, plates_)
        y_end = top + block
        # The poll frame ends on its question: no option boxes and no "REPLY WITH A OR B" (2026-10-29). The
        # native sticker, placed by the operator in POLL_STICKER_ROOM, is the only way to answer it.
        from .still_layout import union
        ux0, uy0, ux1, uy1 = union([tuple(e["box"]) for e in lay.elements])
        if credit["required"]:  # drawn last, recorded as its own element (text_box stays the frame's text block)
            plates = []
            for k, ln in enumerate(c_drawn):
                plate = plan_text(im, (x, c_top + k * c_lead), ln, font_c, c_fill, "la", halo_px=9)
                im = plate.draw(im)
                plates.append(plate)
            lay.plates["photo_credit"] = plates
            credit["box"] = list(record_contrast(lay, "photo_credit", plates))
            # The credit's ratio as drawn: the lowest of its lines on the pixels touching their glyphs.
            measured = [p.contrast for p in plates if p.contrast is not None]
            if measured:
                credit["contrast"] = round(min(measured), 2)
        base = im.convert("RGB")
        lay.meta = {"frame": None, "size": [VIDEO_WIDTH, VIDEO_HEIGHT], "photo_credit": credit,
                    "body_source": " ".join(body.split()), "body_drawn": " ".join(body_lines),
                    "body_px": font_b.size, "body_lines": len(body_lines),
                    "text_box": [min(ux0, x), uy0, max(ux1, x + max_w), max(uy1, int(y_end))],
                    "safe_box": [sx0, sy0, sx1, sy1],
                    **({"poll_sticker_room": getattr(lay, "meta_room", None)} if asks_poll else {}),
                    "ground": ground,
                    **({"hero_figure": hero["number"]} if hero else {})}
        return base, lay

    # 4:5 is the tallest ratio Meta's content-publishing API accepts for feed images (4:5 to 1.91:1);
    # the profile grid shows the centre 3:4 of it, so cover text stays in the middle. 1440x1800 is Meta's
    # recommended 4:5 feed size and the widest the Graph API takes (operator, 2026-10-28: sharper slides, same
    # layout). Every carousel pixel value below is a design value on the 1080-wide canvas, drawn through
    # `_px(cw)` (x cw / 1080), so a 1440 slide is the 1080 slide's layout at 4/3 the resolution.
    CAROUSEL_SIZE = (1440, 1800)
    CAROUSEL_DESIGN_W = 1080
    # JPEG: quality 97 with full-resolution colour (4:4:4). 4:2:0 halves the chroma, which fringed the gold type and
    # capped its colour fidelity at ~38 dB PSNR at any quality; 4:4:4 at 97 measured >= 50 dB there and 46 dB luma,
    # the largest of 28 production slides 1.6 MB (Meta's limit is 8 MB; docs/reviews/carousel-1440-2026-10-28).
    CAROUSEL_JPEG = {"quality": 97, "subsampling": 0, "optimize": True}
    PHOTO_H = 760  # fact slides: photo on top, editorial text panel below (design px)

    @classmethod
    def _px(cls, cw: int):
        """Design px (on the 1080-wide canvas) to this canvas's px: px(80) is 80 at 1080 and 107 at 1440."""
        s = cw / cls.CAROUSEL_DESIGN_W

        def px(v):
            return int(round(v * s))

        def exact(v):  # a font size or stroke width: exactly x 4/3 (FreeType and Pillow take fractional ones)
            v = v * s
            return int(v) if v == int(v) else v
        px.s, px.exact = s, exact
        return px

    @staticmethod
    def _fit_text(d, text: str, family: str, size: int, width: int, max_lines: int, min_size: int, px=round, **kw):
        """fit_text decided on the design canvas (design sizes, strokes and floor; `width` in canvas px) and drawn
        at this canvas's scale: the lines break where the 1080 slide breaks them and each font is the 1080 size
        x 4/3. Fitted on the large canvas itself, fit_text's 2 px steps and the fonts' hinting picked other sizes
        and line breaks (a cover heading that fitted its 920 px on one line went onto two at 1440)."""
        lines, font, short = fit_text(d, text, family, size, int(round(width / getattr(px, "s", 1.0))), max_lines,
                                      min_size, **kw)
        return lines, get_font(family, getattr(px, "exact", px)(font.size)), short

    @staticmethod
    def _fill(photo: Path, size: Tuple[int, int]) -> Image.Image:
        """The photo scaled and centre-cropped to `size` in one Lanczos resample from the file itself (the
        pipeline's upscaled copy for a low-resolution source), never from a smaller intermediate."""
        return cover_crop(photo, size).convert("RGB")

    @classmethod
    def _save_slide(cls, im: Image.Image, path: Path) -> None:
        im.save(path, "JPEG", **cls.CAROUSEL_JPEG)

    def _match_photos(self, cards: List[Dict[str, Any]], stills: List[Path]) -> List[Path]:
        """One photo per slide, matched to what the slide says (the scene planner's vision matching)."""
        from .scene_planner import plan_scenes
        beats = [{"start": float(i), "end": float(i + 1), "text": f"{c['heading']}. {c['body']}"} for i, c in enumerate(cards)]
        try:
            chosen, _, _ = plan_scenes(beats, stills)
            return [Path(c) for c in chosen]
        except Exception as e:  # matching is a refinement; rotation is the fallback
            log.warning("Carousel photo matching failed (%s); rotating photos.", e)
            return [stills[i % len(stills)] for i in range(len(cards))]

    def _carousel_photos(self, cards: List[Dict[str, Any]], stills: List[Path], output_dir: Path) -> List[Path]:
        """The slides' photos, matched once per run and shared by both copies (platform audit, 2026-10-28: the
        Instagram and Facebook copies were matched separately, in parallel, and showed different photos for the
        same slide). The match is kept in carousel_photos.json beside the copies; the second copy to be drawn
        waits for the first and reuses it, and a later redraw of one copy reuses it while the cards and the
        photo pool are the same. A run drawn before the record existed reuses the other copy's recorded photos."""
        output_dir = Path(output_dir)
        record = output_dir / CAROUSEL_PHOTOS
        key = {"cards": [f"{c.get('heading', '')}|{c.get('body', '')}" for c in cards],
               "stills": [str(Path(p)) for p in stills]}
        with _photo_lock(output_dir):
            try:
                kept = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
            except (OSError, ValueError):
                kept = {}
            chosen = [Path(x) for x in kept.get("photos") or []]
            if kept.get("key") == key and len(chosen) == len(cards) and all(x.is_file() for x in chosen):
                return chosen
            chosen = _recorded_photos(output_dir, cards) or self._match_photos(cards, stills)
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                record.write_text(json.dumps({"key": key, "photos": [str(x) for x in chosen]}, indent=1,
                                             ensure_ascii=False), encoding="utf-8", newline="\n")
            except OSError as e:  # the record is a convenience; the slides are still drawn
                log.warning("Carousel photo match not recorded (%s).", e)
            return chosen

    @staticmethod
    def route_carousel_archetype(card: Dict[str, Any], index: int, total: int) -> str:
        """Semantic intent router that selects the optimal editorial archetype."""
        if index == 0:
            return "cover"
        if index == total - 1:
            return "debate"
        text = (card.get("heading", "") + " " + card.get("body", "")).lower()
        stat_patterns = [
            r"\b\d+[\d,]*\s*(?:tons?|people|feet|ft|meters?|m|stories|levels|degrees|bce?|years?)\b",
            r"\b\d+[,.]\d+\b",
        ]
        if range_figure(text):   # a stated span ("between 94 and 104 million years ago") is the slide's figure
            return "colossal_stat"
        if any(re.search(p, text) for p in stat_patterns) and any(w in text for w in ("tons", "people", "deep", "stories", "levels", "feet", "meters", "capacity")):
            return "colossal_stat"
        # Whole words only (round 3, 2026-11-02): "surface" matched inside "Subsurface" and "vs" inside other words,
        # so slides that contrast nothing got the two-photo stack. Bare "surface" is no contrast ("beneath the
        # surface"); the card must say it compares.
        if _CONTRAST.search(text):
            return "comparison"
        relic_keywords = ("gear", "gears", "dial", "dials", "fragment", "fragments", "inscription", "inscriptions",
                          "bronze", "relic", "artifact", "tablet", "device", "pointer", "teeth",
                          "astronomical", "clockwork", "corroded", "ancient computer")
        if any(w in text for w in relic_keywords):
            return "blueprint" if any(w in text for w in ("shaft", "ventilation", "channel", "door", "rolling stone", "tunnel", "chiseled", "engineered", "mechanism", "structure", "architecture", "block")) else "editorial_split"
        if any(w in text for w in ("shaft", "ventilation", "channel", "door", "rolling stone", "tunnel", "chiseled", "engineered", "mechanism", "structure", "architecture", "block")):
            return "blueprint"
        return "immersive" if index % 2 == 1 else "editorial_split"

    REPEATABLE = ("colossal_stat",)   # a figure slide follows its content: two in a row stay two

    @classmethod
    def sequence_archetypes(cls, cards: list[dict[str, Any]]) -> list[str]:
        """Each slide's archetype (route_carousel_archetype), then a pass over the set so two neighbours never get
        the same photo treatment (2026-11-01: Richat's slides 4 and 5 were both comparisons): the second becomes
        the full-bleed or the split archetype, whichever neither neighbour has. Figure slides keep theirs."""
        total = len(cards)
        out = [cls.route_carousel_archetype(c, i, total) for i, c in enumerate(cards)]
        for i in range(1, total - 1):
            if out[i] == out[i - 1] and out[i] not in cls.REPEATABLE:
                after = out[i + 1] if i + 1 < total else None
                out[i] = next((a for a in ("immersive", "editorial_split") if a not in (out[i - 1], after)),
                              "editorial_split" if out[i - 1] == "immersive" else "immersive")
        return out

    @staticmethod
    def _get_card_category(card: Dict[str, Any], archetype: str) -> str:
        """The slide's badge, or "" for none. A slide may not state a qualifier its script did not (platform
        audit, 2026-10-28): keyword labels put "DISCOVERY RECORD" on a slide about marine concrete ("seawalls"
        has "wall"), "ARCHIVAL EVIDENCE" and "ENGINEERING BLUEPRINT" on slides that cite no archive or blueprint,
        and "FORENSIC" on a question. Nothing on a card says what kind of evidence it is (it has a heading and
        a body), so fact slides carry no badge; the closing slide is labelled for what it does, and only when
        it does it: it asks the reader a question."""
        if archetype == "debate" and "?" in f"{card.get('heading', '')} {card.get('body', '')}":
            return "YOUR TAKE"
        return ""

    @staticmethod
    def _draw_badge(
        d: ImageDraw.ImageDraw,
        text: str,
        x0: int,
        y0: int,
        font: Any,
        text_color: Any = GOLD,
        border_color: Any = GOLD,
        fill_color: Any = (12, 16, 22, 230),
        pad_x: int = 18,
        pad_y: int = 8,
        radius: int = 4,
        border_width: int = 1,
    ) -> Tuple[int, int, int, int]:
        """Draws an editorial badge/pill with mathematically centered horizontal and vertical text alignment."""
        bbox = d.textbbox((0, 0), text, font=font, anchor="mm")
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x1 = x0 + tw + 2 * pad_x
        y1 = y0 + th + 2 * pad_y
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill_color, outline=border_color, width=border_width)
        d.text((cx, cy), text, font=font, fill=text_color, anchor="mm")
        return (int(x0), int(y0), int(x1), int(y1))

    def _tag(self, category: str, index: int) -> str:
        """A slide's badge: its category. No slide number ("ENGINEERING // 07"): Instagram shows its own "N/M"
        counter and dots, and our slide-style chrome made the carousel look awkward (operator, 2026-10-28).
        Every slide after the cover carries the subject's name (_render_state, per thread), before its category
        when it has one: each photo of a Facebook multi-photo post is opened on its own, without the caption."""
        subject = getattr(_render_state, "subject", "")
        if subject and category:
            return f"{subject} · {category}"     # the closing slide names its subject too (PR review, 2026-11-02)
        return category or subject

    # Carousel chrome (operator, 2026-10-28): "the design we added to the Instagram carousel to make it look like
    # a slide is needless as Instagram has its own look". The dossier counter ("ARCHIVAL RECORD // 01 OF 07"),
    # the progress bar, the dots, the cover's SWIPE cue and the badges' slide numbers are gone from both copies;
    # the band the cues reserved at the bottom is the text's again (_footer_top).
    #
    # Brand placement: "RELIC & RIDDLE" sat top-right, exactly where Instagram overlays its "N/M" counter on
    # carousel media. What each platform draws over the media: Instagram, the counter pill at the top-right
    # (about 14 pt in from the corner, about 40 x 26 pt: on a 390 pt phone, ~2.8 canvas px per pt, x 930-1041,
    # y 39-111), a tagged-people icon at the bottom-left on tagged photos (we post none), and the dots below the
    # media, outside it; Facebook's photo grid draws "+N" over the centre of its last tile and its viewer draws
    # nothing over the photo. The top-left corner is clear on both, so the brand is drawn there, small, at the
    # 80 px text margin. A square grid tile crops 135 px off the top of a 4:5 slide: the brand (y 70-92) then
    # drops out whole rather than half cut. IG_COUNTER is checked on every slide (still_layout `protected`), so
    # nothing we draw ever lands under the counter.
    HEADER_Y = 70
    BAND_GAP = 16

    @staticmethod
    def _ig_counter(cw: int) -> Tuple[int, int, int, int]:
        """Where Instagram overlays its carousel counter: the measured pill (above) with room for "10/10" and
        narrower phones: the right 280 px of the top 140 px (design px; the counter is drawn at a fixed share
        of the media's width, so it scales with the canvas)."""
        px = MultiFormatGenerator._px(cw)
        return (cw - px(280), 0, cw, px(140))

    def _zone(self, cw: int, chh: int):
        """Where a slide's text and badges stay: the canvas less 40 px each side (the profile grid's 3:4 crop
        takes 33 px off each side of a 4:5 slide) and 30 px top and bottom (design px)."""
        px = self._px(cw)
        return (px(40), px(30), cw - px(40), chh - px(30))

    def _header_bottom(self, d: ImageDraw.ImageDraw, cw: int, tight: bool) -> int:
        """The bottom of the header band: the brand, top-left."""
        px = self._px(cw)
        b = d.textbbox((px(80), px(self.HEADER_Y)), "RELIC & RIDDLE", font=get_font("Montserrat", px.exact(28)), anchor="lt",
                       stroke_width=px.exact(2))
        return b[3] + (px(10) if tight else 0)

    def _footer_top(self, chh: int) -> int:
        """The lowest y a slide's text may reach: the zone's bottom less two gaps. Nothing is drawn under the
        text any more (the swipe cues are gone, operator 2026-10-28; Instagram's dots sit below the media).
        The canvas is 4:5, so its width follows from `chh`."""
        cw = chh * 4 // 5
        return self._zone(cw, chh)[3] - 2 * self._px(cw)(self.BAND_GAP)

    def _plan_panel(self, d, heading: str, body: str, width: int, room: int, h_spec, b_spec, tight: bool,
                    h_stroke: int = 0, b_stroke: int = 0, h_pitch: float = 1.1, body_max: int = 4, px=round):
        """Heading and body measured to fit `width` x `room`: the heading on one line (shrinking to its floor),
        else on two; the body wrapped, shrunk to 80% of its size, then kept to whole sentences, and only last
        shortened at a word with an ellipsis. Returns the plan the panel is drawn from. `h_spec`, `b_spec` and
        the strokes are design px, `width` and `room` canvas px; the plan's fonts and steps are canvas px (_px)."""
        # Planned in design px (the 1080 slide's sizes, lines and line count), drawn at the canvas's scale.
        s = getattr(px, "s", 1.0)
        width, room = int(round(width / s)), int(room / s)
        h_size, h_min = h_spec
        b_size = b_spec
        if tight:
            h_size, b_size = int(h_size * 0.88), int(b_size * 0.9)
        h_lines, font_h, h_short = fit_text(d, heading, "Anton", h_size, width, 1, h_min, stroke=h_stroke, sentences=False,
                                            quiet=True)
        if h_short:
            h_lines, font_h, h_short = fit_text(d, heading, "Anton", h_size, width, 2, h_min, stroke=h_stroke, sentences=False)
        h_height = len(h_lines) * int(font_h.size * h_pitch)
        b_min = max(28, int(b_size * 0.8))
        b_step = int(round(b_size * 1.38))
        left = room - h_height
        max_lines = max(1, min(body_max, (left - int(b_size * 1.25)) // b_step + 1)) if left > b_size else 1
        b_lines, font_b, b_short = fit_text(d, body, "Montserrat", b_size, width, max_lines, b_min, stroke=b_stroke) \
            if body.strip() else ([], get_font("Montserrat", b_size), False)
        fpx = getattr(px, "exact", px)
        font_h, font_b = get_font("Anton", fpx(font_h.size)), get_font("Montserrat", fpx(font_b.size))
        h_step, b_step = int(font_h.size * h_pitch), int(round(font_b.size * 1.38))
        shortened = (["heading"] if h_short else []) + (["body"] if b_short else [])
        return {"h_lines": h_lines, "font_h": font_h, "h_step": h_step, "b_lines": b_lines, "font_b": font_b,
                "b_step": b_step, "height": len(h_lines) * h_step + len(b_lines) * b_step, "shortened": shortened}

    def _draw_panel(self, d, lay, plan, x: int, y: int, h_fill=WHITE, b_fill=LIGHT, h_stroke=0, b_stroke=0,
                    h_stroke_fill=None, b_stroke_fill=None, gap: int = 0, px=round) -> int:
        """Draws a planned heading and body from `y`; records each as one block; returns the y below them.
        Strokes and `gap` are design px, drawn through `px` (_px)."""
        exact = getattr(px, "exact", px)
        h_stroke, b_stroke, gap = exact(h_stroke), exact(b_stroke), px(gap)
        hk = {"stroke_width": h_stroke, "stroke_fill": h_stroke_fill} if h_stroke else {}
        bk = {"stroke_width": b_stroke, "stroke_fill": b_stroke_fill} if b_stroke else {}
        boxes = []
        for line in plan["h_lines"]:
            boxes.append(self._text(d, lay, (x, y), line, plan["font_h"], h_fill, **hk))
            y += plan["h_step"]
        if lay is not None:
            lay.block("heading", boxes)
        y += gap
        # The body starts below the heading's measured bottom: an accented or descending heading line
        # ("ÇATALHÖYÜK", "KRAKÓW") reaches lower than its step, and the body overlapped it (review, 2026-10-28).
        heading_bottom = max((bx[3] for bx in boxes if bx), default=None)
        if heading_bottom is not None and plan["b_lines"]:
            body_top = d.textbbox((x, y), plan["b_lines"][0], font=plan["font_b"], anchor="lt", stroke_width=bk.get("stroke_width", 0))[1]
            y += max(0, heading_bottom + px(6) - body_top)
        boxes = []
        for line in plan["b_lines"]:
            boxes.append(self._text(d, lay, (x, y), line, plan["font_b"], b_fill, **bk))
            y += plan["b_step"]
        if lay is not None:
            lay.block("body", boxes)
            lay.shortened += plan["shortened"]
        return y

    @staticmethod
    def _text(d, lay, xy, text, font, fill, anchor="lt", **kw):
        text = drawable(text, font)  # no emoji, no missing-glyph boxes (2026-10-29)
        d.text(xy, text, font=font, fill=fill, anchor=anchor, **kw)
        return tuple(d.textbbox(xy, text, font=font, anchor=anchor, stroke_width=kw.get("stroke_width", 0)))

    @staticmethod
    def _badge(d, lay, name, text, x0, y0, font, max_w: int = 920, px=round, **kw):
        """A badge that fits its width, recorded as its pill. `max_w`, the padding, radius and border are design
        px, drawn through `px` (_px)."""
        design = {k: kw.get(k, v) for k, v in (("pad_x", 18), ("pad_y", 8), ("radius", 4), ("border_width", 1))}
        kw = {**kw, **{k: px(v) for k, v in design.items()}}
        family = _font_family(font)
        lines, font, short = MultiFormatGenerator._fit_text(d, text, family, int(round(font.size / getattr(px, "s", 1.0))),
                                                            px(max_w - 2 * design["pad_x"]), 1, 16, px=px,
                                                            sentences=False)
        text = lines[0] if lines else ""
        box = MultiFormatGenerator._draw_badge(d, text, x0, y0, font, **kw)
        if lay is not None:
            lay.add(name, box)
            if short:
                lay.shortened.append(name)
        return box

    # The overhaul's slide grammar (visual critic round 4, 2026-10-29). Every slide's text is one stack, measured
    # first and set bottom-aligned on one shared line, KEY_BOTTOM less KEY_GAP: the line sits inside the profile
    # grid's 1:1 crop (it takes 135 design px off each end of a 4:5 slide), so the same block reads in the grid,
    # the feed and the viewer. Slides alternate full-bleed photographs and photo-over-ink splits (the router's
    # "immersive" and "editorial_split"; blueprint and stat slides are splits, comparison a two-photo split).
    # Light text is drawn at a measured contrast (typography.place_text), over an adaptive ink gradient behind the
    # block only; no pill, badge outline or box anywhere (operator, 2026-10-28: no slide chrome). Sizes are design
    # px on the 1080-wide canvas, drawn through _px.
    KEY_TOP, KEY_BOTTOM, KEY_GAP = 169, 1181, 18
    MARGIN = 84
    # The Facebook cover's side inset, design px: the 2:3 hero tile's centre crop starts 90 design px (120 at 1440) in
    # from each side, plus a 45 design px (60) gutter: the text column is x 180-1260 on the 1440 slide (round 4).
    FB_COVER_MARGIN = 135
    SPLIT_PHOTO_H = 675

    def _stack(self, im: Image.Image, lay, rows: List[Dict[str, Any]], x: int, bottom: int, plates: Dict[str, list]):
        """Draws `rows` (each {"name", "text", "font", "fill", "y": its baseline relative to the stack's top,
        optional "track", "x": an x offset}) so the stack's lowest ink sits on `bottom`; records each row (rows of
        one name as one block, with their lowest measured ratio). Returns (image, the stack's top)."""
        d = ImageDraw.Draw(im)
        drawn = [r for r in rows if r.get("text")]
        if not drawn:
            return im, bottom
        low = max(d.textbbox((x + r.get("x", 0), r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
        off = bottom - low
        by_name: Dict[str, list] = {}
        for r in drawn:
            if r.get("kicker"):
                im, _ = draw_kicker(im, x, off + r["y"], r["text"], r["font"], r["fill"], lay, r["name"], plates)
                continue
            plate = plan_text(im, (x + r.get("x", 0), off + r["y"]), r["text"], r["font"], r["fill"], "ls",
                              track=r.get("track", 0), min_halo=r.get("min_halo", 0.0))
            im = plate.draw(im)
            by_name.setdefault(r["name"], []).append(plate)
            plates.setdefault(r["name"], []).append(plate)
        for name, ps in by_name.items():
            record_contrast(lay, name, ps)
        top = min(d.textbbox((x + r.get("x", 0), off + r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
        return im, top

    # Content slides (2026-11-02 panel round 2): the kicker's and the heading's baselines are the same on every
    # content slide (they drifted ~15 px with each body's length when the whole stack was bottom-aligned), so a
    # swipe keeps the eye in place; the body still ends on the shared bottom line. The baselines are those of the
    # tallest regular stack (a one-line heading over BODY_LINES body lines; on Facebook, whose collage shows each
    # photo about a third of the screen wide, a two-line 140 px heading over BODY_LINES_FB): a shorter body opens
    # the gap under the heading instead of moving it. A slide whose stack is taller, or leads with a figure, is
    # bottom-aligned as before.
    # Round 3 (2026-11-02): Instagram's content headlines take the Facebook treatment (Anton two-tone, one line,
    # else two balanced lines split at the seam, "IMPACT THEORY / DISPROVED"), one step below Facebook's size: the
    # Instagram viewer shows each slide full width, Facebook's collage shows it a third as wide. At (100, 60) on one
    # line the type fell about 2x from the cover to slide 2.
    # Round 3 fix (2026-11-02, second pass): the frame is chosen PER DECK, not per slide (a per-slide frame moved the
    # kicker and heading top up to ~190 px between a deck's one-line and two-line slides, undoing the shared line
    # above). generate_carousel_sequence measures every content heading of the copy first (_deck_heading_frame):
    # - no content heading takes two lines: the ONE-LINE frame. No second heading line is reserved (that
    #   reservation put ~330 canvas px between a one-line heading and its body on Instagram, and shrank the diagram
    #   slide's drawing by about a fifth), and BODY_LINES body lines are, so every body meets the frame;
    # - some content heading takes two lines: the TWO-LINE frame, exactly round 2's (BODY_LINES_TWO_LINE_FRAME body
    #   lines, the second heading line at Facebook's floor, at Instagram's design size). A one-line heading in such
    #   a deck keeps the open gap under it: one shared kicker and heading line across the swipe is worth more than
    #   that gap, and it cannot close without moving the body off the shared bottom line.
    # Every slide of the copy, the diagram slide with it, sits on the deck's frame; the frame is kept in each slide's
    # recipe (`heading_frame`) so a redraw lands on the same lines. A slide drawn alone defaults to the two-line frame.
    CONTENT_HEADING = (138, 124)         # Instagram: one line from 138 px down to 124, else two balanced lines
    CONTENT_HEADING_FB = (150, 140)      # Facebook: one line, else two balanced lines, at 140 px or more
    SPLIT_PHOTO_H_FB = 560
    BODY_SPEC = (36, 34)                 # Montserrat, design px: its size and its floor
    BODY_LEADING = 1.38
    BODY_MEASURE = 34                    # characters a body line may hold
    BODY_LINES = 4
    BODY_LINES_FB = 3                    # the stack's body lines planned by the Facebook-only callers (unchanged)
    BODY_LINES_TWO_LINE_FRAME = 3        # body lines the two-line frame reserves, both copies (round 2's frame)
    BODY_LINES_ONE_LINE_FRAME = BODY_LINES   # body lines the one-line frame reserves: every body meets it
    NON_CONTENT_ARCHETYPES = ("cover", "colossal_stat", "debate")   # every other archetype draws a content stack

    def _content_frame(self, px, facebook: bool, heading_lines: int = 2) -> dict[str, int]:
        """The deck's fixed content baselines, canvas px, for a deck frame of `heading_lines` (1 or 2) heading
        lines: {"kicker", "heading" (the heading's first line), "heading_last" (where a heading's last line falls
        when it fills the frame), "heading_lines"}. heading_lines=2 is round 2's frame, value for value."""
        fx = px.exact
        b_size = fx(self.BODY_SPEC[0])
        step = round(b_size * self.BODY_LEADING)
        last = px(self.KEY_BOTTOM - self.KEY_GAP) - round(b_size * 0.25)   # the last body line's baseline
        two = heading_lines > 1
        lines = self.BODY_LINES_TWO_LINE_FRAME if two else self.BODY_LINES_ONE_LINE_FRAME
        first_body = last - (lines - 1) * step
        spec = self.CONTENT_HEADING_FB if facebook else self.CONTENT_HEADING
        h = get_font("Anton", fx(spec[0]))
        h_last = first_body - round(b_size * 2.0)
        hb = h_last
        if two:
            # The second heading line: at Facebook's floor (2026-11-02), at Instagram's design size (round 3: at its
            # floor the two-line "IMPACT THEORY / DISPROVED" missed the frame by a few px and dropped to bottom-aligned).
            second = spec[1] if facebook else spec[0]
            hb = h_last - round(cap_height(get_font("Anton", fx(second))) * 1.13)
        return {"kicker": hb - cap_height(h) - px(26), "heading": hb, "heading_last": h_last,
                "heading_lines": 2 if two else 1}

    def _content_baselines(self, px, facebook: bool, heading_lines: int = 2) -> tuple[int, int]:
        """(kicker baseline, heading first-line baseline) of the content slides on a `heading_lines` deck frame."""
        f = self._content_frame(px, facebook, heading_lines)
        return f["kicker"], f["heading"]

    @staticmethod
    def _frame_lines() -> int:
        """The deck frame the slide being drawn sits on (_render_state, per thread); 2 when drawn alone."""
        return 2 if int(getattr(_render_state, "heading_frame", 2) or 2) > 1 else 1

    def _heading_lines(self, d, px, heading: str, width: int, two_tone: bool | str = "wrap") -> int:
        """How many lines a content slide's heading takes at its design scale (the frame it is planned on)."""
        rows, _ = self._rows(d, px, heading, "", width, "", "", self.CONTENT_HEADING, self.BODY_LINES, two_tone,
                             1.0, facebook_heading=True)
        return max(1, sum(1 for r in rows if r["name"] == "heading" and r.get("text")))

    def _deck_heading_frame(self, cards: list[dict[str, Any]], archetypes: list[str], facebook: bool) -> int:
        """The copy's frame (1 or 2 heading lines): 2 when any content slide's heading, as that copy draws it
        (its words cleaned and typeset as _render_carousel_slide does), takes two lines at its design scale."""
        from .script_engine import facebook_photo_caption, neutral_cues
        clean = facebook_photo_caption if facebook else neutral_cues
        cw = self.CAROUSEL_SIZE[0]
        px = self._px(cw)
        d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        prev = getattr(_render_state, "facebook", False)
        _render_state.facebook = bool(facebook)
        try:
            for card, archetype in zip(cards, archetypes):
                if archetype in self.NON_CONTENT_ARCHETYPES:
                    continue
                heading = typeset(clean(card.get("heading") or "")).upper()
                if heading.strip() and self._heading_lines(d, px, heading, cw - 2 * px(self.MARGIN)) > 1:
                    return 2
        finally:
            _render_state.facebook = prev
        return 1

    def _body(self, d, px, body: str, width: int, max_lines: int, size: float, floor: float):
        """The body in balanced lines of at most BODY_MEASURE characters, not ending on a one-word line where a
        narrower measure avoids it: (lines, font, shortened). Planned on the design canvas like every fit."""
        first = fallback = None
        w = width
        while w >= px(420):
            got = self._fit_text(d, body, "Montserrat", int(size), w, max_lines, int(min(floor, size)), px=px,
                                 quiet=True)
            lines, _, short = got
            first = first or got
            measure_ok = all(len(ln) <= self.BODY_MEASURE for ln in lines)
            if not short and measure_ok:
                if not (len(lines) > 1 and len(lines[-1].split()) == 1):
                    return (even_rag(d, lines, got[1], width, self.BODY_MEASURE), got[1], got[2])
                fallback = fallback or got
            w -= px(16)
        chosen = fallback or first
        if chosen[2]:   # shortened even so: fitted once more at the full measure, logged
            return self._fit_text(d, body, "Montserrat", int(size), width, max_lines, int(min(floor, size)), px=px)
        return chosen

    def _rows(self, d, px, heading: str, body: str, width: int, kicker: str = "", stat: str = "", h_spec=(112, 60),
              b_lines: int = 3, two_tone: bool | str = False, scale: float = 1.0, body_spec=None,
              facebook_heading: bool = False):
        """A slide's text stack, measured (sizes in design px x `scale`): an optional kicker, an optional figure
        (its unit beside it, smaller, in bone), the heading on one or two balanced lines, the body in balanced
        lines (BODY_SPEC, BODY_LEADING, at most BODY_MEASURE characters a line). `facebook_heading`: the heading
        on one line at the copy's content size (CONTENT_HEADING_FB on Facebook, CONTENT_HEADING on Instagram since
        round 3), else two balanced lines at that size, before the usual fit. Returns
        (rows, shortened)."""
        fx = getattr(px, "exact", px)
        body_spec = body_spec or self.BODY_SPEC
        rows, y, short = [], 0, []
        if kicker:
            fk = get_font("Montserrat", fx(34 * scale), weight=800)
            text, ks = _fit_kicker(kicker, fk, width)
            y += cap_height(fk)
            rows.append({"name": "kicker", "text": text, "font": fk, "fill": GOLD, "y": y, "kicker": True})
            y += px(26 * scale)
            short += ["kicker"] if ks else []
        if stat:
            m = re.match(r"(\S+)\s*(.*)", stat)
            num, unit = m.group(1), m.group(2).strip()
            size, stacked = 225 * scale, []
            while True:
                fs, fu = get_font("Anton", fx(size)), get_font("Anton", fx(max(40, size * 0.42)))
                w = d.textlength(num, font=fs) + (px(18) + d.textlength(unit, font=fu) if unit else 0)
                if w <= width or size <= 96:
                    break
                if unit and len(unit.split()) > 1 and size <= 170 * scale:
                    # A long unit ("MILLION YEARS AGO") is set as a two-line lockup beside the figure, its foot on the
                    # figure's baseline, rather than shrinking the figure or adding a row under it (round 3).
                    size = 225 * scale
                    while size > 96:
                        fs = get_font("Anton", fx(size))
                        fu = get_font("Anton", fx(max(40, size * 0.3)))
                        halves = [h for h in split_balanced(unit) if h]
                        lw = max(d.textlength(h, font=fu) for h in halves)
                        if d.textlength(num, font=fs) + px(22) + lw <= width:
                            stacked = halves
                            break
                        size -= 6
                    break
                size -= 6
            y += cap_height(fs)
            rows.append({"name": "stat", "text": num, "font": fs, "fill": GOLD, "y": y})
            if unit and stacked:
                ux = int(d.textlength(num, font=fs)) + px(22)
                step = round(cap_height(fu) * 1.25)
                for j, half in enumerate(stacked):
                    rows.append({"name": "stat", "text": half, "font": fu, "fill": WHITE,
                                 "y": y - (len(stacked) - 1 - j) * step, "x": ux})
            elif unit:
                rows.append({"name": "stat", "text": unit, "font": fu, "fill": WHITE, "y": y,
                             "x": int(d.textlength(num, font=fs)) + px(18)})
            y += px(30 * scale)
        h_lines, font_h, h_short, use_two = None, None, False, False
        if facebook_heading:
            spec = self.CONTENT_HEADING_FB if getattr(_render_state, "facebook", False) else self.CONTENT_HEADING
            hi, lo = (int(v * scale) for v in spec)
            one = self._fit_text(d, heading, "Anton", hi, width, 1, lo, px=px, sentences=False, quiet=True)
            if not one[2]:
                h_lines, font_h = one[0], one[1]
            elif len(heading.split()) > 1:
                halves = [h for h in split_balanced(heading) if h]
                fits = [self._fit_text(d, h, "Anton", hi, width, 1, lo, px=px, sentences=False, quiet=True)
                        for h in halves]
                if not any(f[2] for f in fits):
                    font_h = min((f[1] for f in fits), key=lambda f: f.size)
                    h_lines, use_two = [f[0][0] for f in fits], bool(two_tone)
        if h_lines is None:
            use_two = bool(two_tone) and len(heading.split()) > 1
            if use_two and two_tone == "wrap":
                # Content slides (2026-11-02): two-tone only when the heading needs two lines anyway; a heading that
                # fits one line stays one bone line ("25+ MILES WIDE" is one figure, not two thoughts).
                use_two = self._fit_text(d, heading, "Anton", int(h_spec[0] * scale), width, 1, int(h_spec[1] * scale),
                                         px=px, sentences=False, quiet=True)[2]
            if use_two:
                # Two-tone: two balanced lines, each as large as the narrower of the two allows (one size for both).
                halves = [h for h in split_balanced(heading) if h]
                fits = [self._fit_text(d, h, "Anton", int(h_spec[0] * scale), width, 1, int(h_spec[1] * scale), px=px,
                                       sentences=False, quiet=True) for h in halves]
                font_h = min((f[1] for f in fits), key=lambda f: f.size)
                h_short = any(f[2] for f in fits)
                h_lines = [f[0][0] if f[0] else "" for f in fits]
            else:
                h_lines, font_h, h_short = self._fit_text(d, heading, "Anton", int(h_spec[0] * scale), width, 1,
                                                          int(h_spec[1] * scale), px=px, sentences=False, quiet=True)
                if h_short:
                    h_lines, font_h, h_short = self._fit_text(d, heading, "Anton", int(h_spec[0] * scale), width, 2,
                                                              int(h_spec[1] * scale * 0.9), px=px, sentences=False)
        h_lines = [drop_break_comma(t) if j < len(h_lines) - 1 else t for j, t in enumerate(h_lines)]
        ch = cap_height(font_h)
        for j, line in enumerate(h_lines):
            y += ch if j == 0 else round(ch * 1.13)
            rows.append({"name": "heading", "text": line, "font": font_h,
                         "fill": GOLD if (use_two and j == len(h_lines) - 1) else WHITE, "y": y})
        short += ["heading"] if h_short else []
        if body.strip():
            b_size = body_spec[0] * scale
            b_lines_, font_b, b_short = self._body(d, px, body, width, b_lines, b_size, body_spec[1])
            step = round(font_b.size * self.BODY_LEADING)
            y += round(font_b.size * 2.0)
            for j, line in enumerate(b_lines_):
                rows.append({"name": "body", "text": line, "font": font_b, "fill": LIGHT, "y": y + j * step})
            short += ["body"] if b_short else []
        return rows, short

    def _slide_text(self, im: Image.Image, lay, cw: int, chh: int, heading: str, body: str, kicker: str = "",
                    stat: str = "", full: bool = True, two_tone: bool | str = False, tight: bool = False, b_lines: int = 3,
                    h_spec=(112, 60), extra=None, content: bool = False, deep: bool = False,
                    margin: int | None = None):
        """Plans a slide's stack at the largest scale whose top clears KEY_TOP, its foot on the shared line; on a
        content slide (`content`) the kicker and the heading sit on the deck's fixed baselines
        (_content_baselines) while the stack fits them. Shades behind it (a full-bleed photo) and draws it.
        `extra(rows, y)`: rows appended below the body. `deep`: the gradient behind a full-bleed stack is taken almost
        to ink (a warm or gold photo under gold type: hue gives no contrast, so luminance must). `margin`: the text
        column's side inset, design px (default MARGIN)."""
        px = self._px(cw)
        d = ImageDraw.Draw(im)
        margin = self.MARGIN if margin is None else margin
        width = cw - 2 * px(margin)
        bottom = px(self.KEY_BOTTOM - self.KEY_GAP)
        facebook = bool(getattr(_render_state, "facebook", False))
        if content:
            h_spec = self.CONTENT_HEADING
            b_lines = max(b_lines, self.BODY_LINES)
        fixed = {}

        def place(rows):
            """Content slides: rows on absolute baselines (the kicker on kb, the heading's first line on hb, the
            body's foot on the shared line), or unchanged (bottom-aligned by _stack) when the stack does not fit
            that frame or leads with a figure."""
            fixed.clear()
            drawn_ = [r for r in rows if r.get("text")]
            heads = [r for r in drawn_ if r["name"] == "heading"]
            bodies = [r for r in drawn_ if r["name"] == "body"]
            kick = next((r for r in drawn_ if r["name"] == "kicker"), None)
            if not content or not heads or not bodies or any(r["name"] == "stat" for r in drawn_):
                return rows

            def ink(r, dy, i):   # the row's ink box edge (1 top, 3 bottom) once moved by dy
                return d.textbbox((0, r["y"] + dy), r["text"], font=r["font"], anchor="ls")[i]
            # The deck's frame; a heading that takes more lines than it (measured otherwise than as drawn) gets the
            # two-line frame rather than overrunning the body, and says so.
            lines = self._frame_lines()
            if len(heads) > lines:
                log.warning("Carousel heading takes %d lines on a %d-line deck frame: %.60s", len(heads), lines,
                            heading)
            frame = self._content_frame(px, facebook, max(lines, len(heads)))
            kb, hb = frame["kicker"], frame["heading"]
            shift_h = hb - heads[0]["y"]            # the heading's first line on the deck's shared baseline
            shift_k = kb - kick["y"] if kick else shift_h
            shift_b = bottom - max(ink(r, 0, 3) for r in bodies)
            head_foot = max(ink(r, shift_h, 3) for r in heads)
            body_top = min(ink(r, shift_b, 1) for r in bodies)
            if body_top < head_foot + round(bodies[0]["font"].size * 0.9) or shift_k > shift_h:
                return rows
            out = []
            for r in rows:
                dy = shift_b if r["name"] == "body" else shift_h if r["name"] == "heading" else shift_k
                out.append(dict(r, y=r["y"] + dy))
            fixed.update(kicker=kb, heading=hb, heading_frame=frame["heading_lines"], heading_lines=len(heads))
            return out

        for scale in ((0.88, 0.8, 0.72) if tight else (1.0, 0.92, 0.84, 0.76, 0.7)):
            rows, short = self._rows(d, px, heading, body, width, kicker, stat, h_spec, b_lines, two_tone, scale,
                                     facebook_heading=content)
            if extra:
                rows = extra(rows, scale)
            rows = place(rows)
            drawn = [r for r in rows if r.get("text")]
            if not drawn:
                break
            low = max(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
            high = min(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
            if bottom - (low - high) >= px(self.KEY_TOP):
                break
        else:
            log.warning("Carousel slide text taller than the key area at its smallest scale: %.60s", heading)
        if lay is not None:
            lay.shortened += short
        drawn = [r for r in rows if r.get("text")]
        if full and drawn:
            low = max(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
            high = min(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
            im = scrim(im, bottom - (low - high) - px(40), chh, feather=px(300), target=0.08 if deep else 0.26,
                       cap=0.95 if deep else 0.90)
            if lay is not None and deep:
                lay.meta["ink_band"] = True
        plates = getattr(lay, "plates", None) if lay is not None else None
        if plates is None:
            plates = {}
            if lay is not None:
                lay.plates = plates
        im, _ = self._stack(im.convert("RGBA"), lay, rows, px(margin), bottom, plates)
        if lay is not None and fixed:
            lay.meta["baselines"] = dict(fixed)
        return im.convert("RGB")

    def _split_ground(self, photo: Path, cw: int, chh: int, photo2: Path = None) -> Image.Image:
        """Photo over ink: the photo (or two, stacked) across the top, fading into the ink panel the text sits on."""
        px = self._px(cw)
        ph = px(self.SPLIT_PHOTO_H_FB if getattr(_render_state, "facebook", False) else self.SPLIT_PHOTO_H)
        im = Image.new("RGB", (cw, chh), brand.INK2)
        if photo2 is None:
            im.paste(self._fill(photo, (cw, ph + px(150))), (0, 0))
        else:
            # Two whole photographs with an ink gutter, both above the fade (2026-11-02 design review: the lower
            # one ran into the fade and read as a rendering seam).
            gutter = px(8)
            half = (ph + px(40) - gutter) // 2
            im.paste(self._fill(photo, (cw, half)), (0, 0))
            im.paste(self._fill(photo2, (cw, half)), (0, half + gutter))
            return scrim(im, 2 * half + gutter, chh, feather=px(40), target=0.0, cap=1.0, floor=1.0,
                         colour=brand.INK2)
        return scrim(im, ph + px(60), chh, feather=px(180), target=0.0, cap=1.0, floor=1.0, colour=brand.INK2)

    def _render_cover(self, photo: Path, card: Dict[str, Any], cw: int, chh: int,
                      lay=None, tight: bool = False) -> Image.Image:
        """The cover: full-bleed, the heading in two balanced Anton lines (bone, then gold), the body under it. On the
        Facebook copy the cover is the collage's tall 2:3 hero tile, which shows the slide's centre 2:3 (x 120-1320 at
        1440): its text column is FB_COVER_MARGIN in from each side, so no letter is cut (round 4, 2026-11-02:
        "THE EYE OF" lost its T, the body its first letters)."""
        im = self._fill(photo, (cw, chh))
        facebook = bool(getattr(_render_state, "facebook", False))
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], two_tone=True, tight=tight,
                                b_lines=3, h_spec=(150, 72), margin=self.FB_COVER_MARGIN if facebook else None)

    def _render_immersive(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """Full-bleed photograph, the stack over an ink gradient at its foot."""
        im = self._fill(photo, (cw, chh))
        tag = self._tag(self._get_card_category(card, "immersive"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, tight=tight,
                                two_tone="wrap", h_spec=(112, 60), content=True)

    def _render_comparison(self, photo1: Path, photo2: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                           lay=None, tight: bool = False) -> Image.Image:
        """Two photographs stacked over the ink panel. No "REF A // SURFACE CONTEXT" labels over them: every
        comparison slide said so, whatever it showed (platform audit, 2026-10-28)."""
        im = self._split_ground(photo1, cw, chh, photo2)
        tag = self._tag(self._get_card_category(card, "comparison"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, two_tone="wrap", b_lines=4, h_spec=(96, 56), content=True)

    def _render_blueprint(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """A structure slide: the split layout (the drafting grid it drew was decoration that said nothing)."""
        im = self._split_ground(photo, cw, chh)
        tag = self._tag(self._get_card_category(card, "blueprint"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, two_tone="wrap", b_lines=4, h_spec=(96, 56), content=True)

    def _render_colossal_stat(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                              lay=None, tight: bool = False) -> Image.Image:
        """A figure slide: the split layout led by the card's own figure (its first number, with its unit when it
        has one); a card without one gets no figure rather than a label in its place. No "VERIFIED BY
        ARCHEOLOGICAL RECORD" pill (platform audit, 2026-10-28)."""
        heading, body = card["heading"].upper(), card["body"]
        im = self._split_ground(photo, cw, chh)
        # A span is drawn whole ("94–104 MILLION YEARS AGO"), never as its first number (round 3, 2026-11-02).
        span = range_figure(f"{heading} {body}")
        stat_match = None if span else re.search(
            r"(\b\d+[\d,]*\+?(?:\s*(?:TONS?|PEOPLE|FEET|FT|METERS?|M|STORIES|LEVELS))?)\b", f"{heading} {body}",
            re.IGNORECASE)
        stat = " ".join(x for x in span if x) if span else stat_match.group(1).upper() if stat_match else ""
        if lay is not None and stat:
            lay.meta["stat"] = stat
        tag = self._tag(self._get_card_category(card, "colossal_stat"), index)
        return self._slide_text(im, lay, cw, chh, heading, body, kicker=tag, stat=stat, full=False, tight=tight,
                                b_lines=3, h_spec=(84, 52))   # its figure leads: bottom-aligned

    def _render_debate(self, photo: Path, card: Dict[str, Any], index: int, total: int, cw: int, chh: int, poll: Dict[str, Any], f_small,
                       share: str = "", lay=None, tight: bool = False) -> Image.Image:
        """The closing slide: full-bleed, the heading (its last line gold), the card's question, then the run's
        share-and-follow line (operator decision 2026-10-25) as a sentence under a short gold rule. No "TELL US IN
        THE COMMENTS" footer (operator decision 2026-10-29: a second ask beside the share line)."""
        px = self._px(cw)
        im = self._fill(photo, (cw, chh))
        tag = self._tag(self._get_card_category(card, "debate"), index)
        width = cw - 2 * px(self.MARGIN)
        d = ImageDraw.Draw(im)
        rule = {}

        def with_share(rows, scale):
            if not share:
                return rows
            lines, f_share, sh_short = self._fit_text(d, share, "Montserrat", int(36 * scale), width, 2, 28, px=px,
                                                      weight=SHARE_WEIGHT)
            # Round 4 (2026-11-02): the ask one weight above the question (SHARE_WEIGHT), a second voice, and
            # a full body line of air above its gold rule (at one weight and 46 px the two read as one block).
            f_share = get_font("Montserrat", f_share.size, weight=SHARE_WEIGHT)
            # Its break as the body's (round 4 review): never a line ending on "a" or "and" where another fits.
            lines = even_rag(d, lines, f_share, width, max([self.BODY_MEASURE] + [len(ln) for ln in lines]))
            if sh_short and lay is not None and "share" not in lay.shortened:
                lay.shortened.append("share")
            last = max((r["y"] for r in rows), default=0)
            body_step = round(px.exact(self.BODY_SPEC[0] * scale) * self.BODY_LEADING)
            rule["y"] = last + body_step + px(16 * scale)           # the rule's top, relative to the stack
            y = rule["y"] + px(6) + px(26 * scale) + cap_height(f_share)
            step = round(f_share.size * 1.38)
            return rows + [{"name": "share", "text": ln, "font": f_share, "fill": WHITE, "y": y + j * step}
                           for j, ln in enumerate(lines)]

        # Round 3 (2026-11-02): over a warm or gold photo the gold line has no hue contrast ("HIDDEN MARVELS" in gold
        # over the gold cave measured 4.88), so the stack sits on an ink lower band.
        warm = warm_ground(im, (0, chh // 2, cw, chh))
        im = self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, two_tone=True,
                              tight=tight, b_lines=3, h_spec=(112, 60), extra=with_share, deep=warm)
        share_box = next((e["box"] for e in (lay.elements if lay is not None else []) if e["name"] == "share"), None)
        if share_box and "y" in rule:
            # The gold rule: 6 design px tall, 96 wide, its own height above the share line's first cap.
            top = share_box[1] - px(26) - px(6)
            ImageDraw.Draw(im).rectangle([px(self.MARGIN), top, px(self.MARGIN) + px(96), top + px(6) - 1], fill=GOLD)
        return im

    def _render_diagram(self, poster: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                        lay=None, tight: bool = False) -> Image.Image:
        """The run's explanatory graphic (its poster frame) re-laid at 4:5 (2026-11-02 panel round 2): the drawing
        cropped to what it draws, on its own ink ground above the deck's fixed text baselines, with the card's own
        kicker, heading and body. Nothing is added to the drawing; its own labels and qualifiers stay as drawn."""
        px = self._px(cw)
        if is_map_poster(poster):
            return self._render_map_slide(poster, card, index, cw, chh, lay=lay, tight=tight)
        with Image.open(poster) as src:
            g = src.convert("RGB")
        ground, box = diagram_crop(g)
        im = Image.new("RGB", (cw, chh), ground)
        facebook = bool(getattr(_render_state, "facebook", False))
        # The drawing takes the room above the deck's kicker (round 3 fix): on a one-line deck frame it has the
        # room a second heading line would have taken; it never moves the kicker off the deck's line. A heading
        # longer than the deck frame (see place() in _slide_text) takes the two-line frame here too.
        heading = card["heading"].upper()
        lines = max(self._frame_lines(), self._heading_lines(ImageDraw.Draw(im), px, heading, cw - 2 * px(self.MARGIN)))
        kb = self._content_baselines(px, facebook, lines)[0]
        top, bottom = px(self.HEADER_Y) + px(80), kb - px(34) - px(34)   # the kicker cap, then air
        art = g.crop(box)
        art.thumbnail((cw - 2 * px(self.MARGIN - 24), max(1, bottom - top)), Image.Resampling.LANCZOS)
        if art.width < cw - 2 * px(self.MARGIN - 24) and art.height < bottom - top:   # small drawings scale up
            k = min((cw - 2 * px(self.MARGIN - 24)) / art.width, (bottom - top) / art.height)
            art = g.crop(box).resize((round(art.width * k), round(art.height * k)), Image.Resampling.LANCZOS)
        ax, ay = (cw - art.width) // 2, top + (bottom - top - art.height) // 2
        # Feathered into the ground over its padding, so the drawing's own soft backdrop shows no hard edge.
        from PIL import ImageFilter
        f = max(4, px(14))
        mask = Image.new("L", art.size, 0)
        ImageDraw.Draw(mask).rectangle((f, f, art.width - 1 - f, art.height - 1 - f), fill=255)
        im.paste(art, (ax, ay), mask.filter(ImageFilter.GaussianBlur(f / 2)))
        if lay is not None:
            lay.meta["diagram"] = {"poster": str(poster), "source_box": list(box),
                                   "image_box": [ax, ay, ax + art.width, ay + art.height]}
        tag = self._tag(self._get_card_category(card, "diagram"), index)
        return self._slide_text(im, lay, cw, chh, heading, card["body"], kicker=tag, full=False,
                                tight=tight, two_tone="wrap", b_lines=4, content=True)

    def _render_map_slide(self, poster: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """The locator map's resting frame full bleed (round 4): scaled to the slide's width and cut so the poster's
        centre (the pin) sits at MAP_FOCUS of the slide, never cropped to a drawn box (diagram_crop: a map has no
        flat ground); the card's stack over an ink foot (MAP_FOOT_INK), as on a full-bleed photo slide.
        Round 4 review (2026-11-02): the map's own labels are measured (map_label_bands) against the stack. The
        place label and pin must show whole above the foot; every other label (the scale bar, a neighbour's name)
        shows whole above the foot or lies wholly under its ink, never half under it or behind the text. When the
        pin's resting place leaves no such cut, the map rises (up to MAP_LIFT_MAX design px), then the fade
        shortens (MAP_FADE down to MAP_FADE_MIN). What it chose is in lay.meta["diagram"]; a map it could not
        clear says so there ("labels_clear": False) and in the log."""
        import numpy as np

        from .still_layout import Layout
        px = self._px(cw)
        with Image.open(poster) as src:
            g = src.convert("RGB")
        k = cw / g.width
        tag = self._tag(self._get_card_category(card, "diagram"), index)
        heading = card["heading"].upper()

        def stack(im_, lay_):
            return self._slide_text(im_, lay_, cw, chh, heading, card["body"], kicker=tag, tight=tight,
                                    two_tone="wrap", h_spec=(112, 60), content=True, full=False)
        # Where the stack will stand: a dry run on a throwaway layout (its rows do not depend on the pixels).
        probe = Layout("carousel", (cw, chh), self._zone(cw, chh), tight=tight)
        probe.plates = {}
        stack(Image.new("RGB", (cw, chh), brand.INK), probe)
        texts = [e["box"] for e in probe.elements]
        stack_top = min(b[1] for b in texts) if texts else chh
        if g.height * k < chh:                     # a wide poster: the usual centre crop
            full = cover_crop(poster, (cw, chh)).convert("RGB")
            tops, base_top, record = [0], 0, None   # its crop is not the record's frame: measured by pixels only
        else:
            full = g.resize((cw, round(g.height * k)), Image.Resampling.LANCZOS)
            h = full.height
            base_top = int(min(max(0, MAP_FOCUS[0] * h - MAP_FOCUS[1] * chh), h - chh))
            lift_max = min(h - chh, base_top + px(MAP_LIFT_MAX))
            tops, record = list(range(base_top, lift_max + 1, max(1, px(8)))) or [base_top], poster
            if tops[-1] != lift_max:
                tops.append(lift_max)
        bands = map_label_bands(record, full, k, px)
        pin_y = full.height / 2
        primary, secondary = [], []
        for b in bands:   # the place label stands over the pin; the pin itself is MAP_PIN design px about its centre
            (primary if b[3] > pin_y - px(MAP_LABEL_REACH) and b[1] < pin_y + px(MAP_PIN) else secondary).append(b)
        primary_bottom = max([pin_y + px(MAP_PIN)] + [b[3] for b in primary])
        primary_top = min([pin_y - px(MAP_PIN)] + [b[1] for b in primary])
        foot_low = stack_top - px(MAP_CLEAR)          # the foot's full ink starts here at the latest
        head_room = px(self.HEADER_Y) + px(80)         # the place label stays under the masthead, as a drawing does

        def cut(top, fade):
            """(top, fade, y1) when this cut shows the place whole and no label half under the foot, else None."""
            if primary_top - top < head_room:
                return None
            y1 = foot_low
            for b in sorted(secondary, key=lambda b: -b[3]):   # from the foot up: a half-covered label is covered
                if b[1] - top < y1 and b[3] - top > y1 - fade:
                    y1 = min(y1, b[1] - top)
            return (top, fade, y1) if y1 - fade >= primary_bottom - top + px(MAP_GAP) else None
        step = max(1, px(10))
        choice = None
        # A soft foot (MAP_FADE_GOOD or more) at the least lift first; only then a shorter fade.
        for fades in ([*range(px(MAP_FADE), px(MAP_FADE_GOOD), -step), px(MAP_FADE_GOOD)],
                      [*range(px(MAP_FADE_GOOD) - step, px(MAP_FADE_MIN), -step), px(MAP_FADE_MIN)]):
            choice = next((c for top in tops for fade in fades if (c := cut(top, fade))), None)
            if choice:
                break
        clear = choice is not None
        if not clear:
            log.warning("Locator map slide: no cut keeps the map's labels clear of the text (%.40s).", heading)
            choice = (tops[0], px(MAP_FADE_MIN), foot_low)
        top, fade, y1 = choice
        im = full.crop((0, top, cw, top + chh))
        # The ink foot: none above y1 - fade, a smooth rise to MAP_FOOT_INK at y1, held to the slide's foot.
        ys = np.arange(chh, dtype=np.float32)
        u = np.clip((ys - (y1 - fade)) / max(1, fade), 0, 1)
        alpha = (u * u * (3 - 2 * u) * MAP_FOOT_INK)[:, None, None]
        a = np.asarray(im, dtype=np.float32)
        im = Image.fromarray(np.clip(a * (1 - alpha) + np.array(brand.INK, np.float32) * alpha, 0, 255)
                             .astype(np.uint8), "RGB")
        labels = []
        for b in bands:
            y0b, y1b = b[1] - top, b[3] - top
            if y1b <= 0 or y0b >= chh:
                continue
            shown = y1b <= y1 - fade
            labels.append({"box": [int(b[0]), round(y0b), int(b[2]), round(y1b)],
                           "place": b in primary, "shown": bool(shown)})
        if lay is not None:
            window = None if g.height * k < chh else [0, round(top / k), g.width, round((top + chh) / k)]
            lay.meta["diagram"] = {"poster": str(poster), "kind": MAP_KIND, "source_box": window,
                                   "image_box": [0, 0, cw, chh], "lift": int(top - base_top),
                                   "foot": [int(y1 - fade), int(y1)], "stack_top": int(stack_top),
                                   "labels": labels, "labels_clear": clear}
        return stack(im, lay)

    def _render_editorial_split(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                                lay=None, tight: bool = False) -> Image.Image:
        """Photo over ink, the stack on the ink panel."""
        im = self._split_ground(photo, cw, chh)
        tag = self._tag(self._get_card_category(card, "editorial_split"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, two_tone="wrap", b_lines=4, h_spec=(96, 56), content=True)

    def _render_carousel_slide(self, archetype: str, photo: Path, photo_next: Path, card: Dict[str, Any], i: int,
                               total: int, poll: Dict[str, Any], share: str, facebook: bool, tight: bool = False,
                               subject: str = "", heading_frame: int = 2, strip: dict[str, Any] | None = None):
        """One carousel slide and its measured layout: the archetype's own text, then the brand in its band at the
        top-left. Nothing is drawn under Instagram's counter (`protected`). Both copies are free of slide chrome
        (operator, 2026-10-28); `facebook` now changes only the words: the grid copy's slide text says nothing
        about swiping ("Swipe to see how ..." is drawn "See how ..."), and neither copy draws a direction cue.
        `tight`: smaller type, and the brand on a solid pill so it reads over any photo (Richat 2's header
        "collides with the spacecraft's solar panel"). `heading_frame`: the deck's content frame (1 or 2 heading
        lines, _deck_heading_frame); 2, round 2's frame, when the slide is drawn alone. `strip`: the Instagram copy's
        swipe strip on slide 1 or 2 (swipe_strip)."""
        from .still_layout import Layout
        from .script_engine import facebook_photo_caption, neutral_cues
        clean = facebook_photo_caption if facebook else neutral_cues
        card = dict(card, heading=typeset(clean(card.get("heading") or "")), body=typeset(clean(card.get("body") or "")))
        share = typeset(clean(share or ""))
        # Every slide after the cover names its subject in its kicker: Facebook's viewer opens one photo at a time
        # with the caption on the first only (2026-11-01), and Instagram's copy carries the same kicker so the two
        # copies keep one system and the gold rule (2026-11-02 design review).
        # Per thread: the Instagram and Facebook copies render at once on one generator (PR review, 2026-11-02).
        _render_state.subject = typeset(subject).upper() if i > 0 else ""
        _render_state.facebook = bool(facebook)
        _render_state.heading_frame = 2 if int(heading_frame or 2) > 1 else 1
        cw, chh = self.CAROUSEL_SIZE
        lay = Layout("carousel", (cw, chh), self._zone(cw, chh), tight=tight,
                     protected=[("instagram_counter", self._ig_counter(cw))])
        lay.plates = {}
        px = self._px(cw)
        f_small = get_font("Montserrat", px.exact(28))
        if archetype == "cover":
            im = self._render_cover(photo, card, cw, chh, lay=lay, tight=tight)
        elif archetype == "immersive":
            im = self._render_immersive(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "comparison":
            im = self._render_comparison(photo, photo_next, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "blueprint":
            im = self._render_blueprint(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "colossal_stat":
            im = self._render_colossal_stat(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "diagram":
            im = self._render_diagram(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "debate":
            im = self._render_debate(photo, card, i, total, cw, chh, poll, f_small, share=share, lay=lay, tight=tight)
        else:
            im = self._render_editorial_split(photo, card, i, cw, chh, lay=lay, tight=tight)

        # The brand, top-left (clear of Instagram's counter, see HEADER_Y): the gold diamond mark, then Cinzel Bold
        # in tracked bone capitals at the covers' 40 px (one masthead across covers and carousels, 2026-11-01; 30 px
        # before), drawn at a measured contrast like every other line (a halo where the photo is bright), never on
        # a pill.
        from .cover_design import MARK_GAP, MARK_R
        brand_font = get_font("Cinzel", px.exact(40), weight=700)
        r, gap = px(MARK_R), px(MARK_GAP)
        # On the Facebook cover (the collage's 2:3 hero tile) the masthead keeps the cover's inset too (round 4: its
        # diamond fell outside the tile's crop at x 107).
        x0 = px(self.FB_COVER_MARGIN) if facebook and archetype == "cover" else px(80)
        base = px(self.HEADER_Y) + cap_height(brand_font)
        # Round 3 (2026-11-02): where the photo is too bright behind the masthead, a soft ink gradient from the top
        # edge, deep only as far as the masthead's planned ratio needs, takes the place of a 36 px halo per line
        # (a dark smudge over Richat's blue sky). The line's own halo then does only what is left.
        im = masthead_scrim(im.convert("RGB"), (x0, base - cap_height(brand_font), x0 + px(520), base), WHITE,
                            feather=px(300))
        plate = plan_text(im, (x0 + 2 * r + gap, base), "RELIC & RIDDLE", brand_font, WHITE,
                          "ls", track=px(3), min_halo=0.6 if tight else 0.0, target=MASTHEAD_TARGET)
        im = plate.draw(im.convert("RGB"))
        cx, cy = x0 + r, base - cap_height(brand_font) / 2
        diamond = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
        d = ImageDraw.Draw(im)
        d.polygon(diamond, outline=INK, width=max(2, px(3)))
        d.polygon(diamond, fill=GOLD)
        # One header element, as before: its box takes in the mark.
        bx = plate.box
        lay.add("header_brand", (min(bx[0], cx - r), min(bx[1], cy - r), max(bx[2], cx + r), max(bx[3], cy + r)))
        lay.elements[-1].update(plate.record())
        lay.plates.setdefault("header_brand", []).append(plate)
        if strip and not facebook:
            im = swipe_strip(im, lay, strip)
        return im, lay

    def generate_carousel_sequence(self, scene_images: List[Path], script_payload: Dict[str, Any], output_dir: Path,
                                   facebook: bool = False, graphics: list[Path] | None = None) -> List[Path]:
        """Multi-Template Editorial Carousel (CAROUSEL_SIZE, 1440x1800, 4:5):
        Dynamically routes each slide to one of 5 National Geographic / The Economist archetypes
        (Full-Bleed Immersive, Comparative Side-by-Side, Architectural Blueprint, Colossal Stat, Debate).
        facebook=True: the same slides for Facebook's multi-photo post (carousel_fb/). Neither copy has a swipe
        cue, slide counter, progress bar or dots any more (operator, 2026-10-28: Instagram draws its own counter
        and dots; Facebook shows the set as a grid); they differ only in that the grid copy's text never says
        "swipe". The folder and the publisher's per-platform request are kept.
        Each slide is measured and checked after drawing (still_layout); a failing one is drawn once more
        with the tighter fit, and its record is kept in carousel*/still_layout.json.
        `graphics`: the run's graphics (graphic_*.mp4, each with its .jpg poster beside it; default: those in
        output_dir/graphics). When one is an explanatory drawing (DIAGRAM_KINDS), slide 3 or 4 shows it instead of
        a photo (carousel_diagram), the same slide in both copies; without one nothing changes."""
        from . import still_layout
        out_dir = output_dir / ("carousel_fb" if facebook else "carousel")
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in scene_images if not is_video(p)]
        cards = script_payload["carousel_slides"]
        poll = script_payload.get("poll") or {}
        total = len(cards)
        photos = self._carousel_photos(cards, stills, output_dir)
        from .script_engine import share_prompt
        share = share_prompt(script_payload)  # the closing slide asks for a share and a follow (2026-10-25)
        narration = script_payload.get("narration_script") or ""
        subject = standalone_subject(script_payload)   # both copies (2026-11-02): one rule for IG and FB

        slides = []
        archetypes = self.sequence_archetypes(cards)
        diagram = carousel_diagram(output_dir, cards, graphics)
        if diagram:
            archetypes = [("diagram" if i == diagram[0] else a) for i, a in enumerate(archetypes)]
        # One unit per run: figures in the unit the narration speaks (2026-10-29), after the photo match.
        cards = [dict(card, **{k: spoken_units(card[k], narration) for k in ("heading", "body")
                               if isinstance(card.get(k), str)}) for card in cards]
        frame = self._deck_heading_frame(cards, archetypes, facebook)   # one frame for every slide of this copy
        # Instagram's swipe strip (round 4): slide 2 is drawn first, so slide 1's strip meets slide 2's kicker rule.
        strips: dict[int, dict[str, Any]] = {}
        order = list(range(total))
        if not facebook and total > 2 and archetypes[0] == "cover" and archetypes[1] not in self.NON_CONTENT_ARCHETYPES:
            order = [1, 0] + order[2:]
            strips[1] = {"side": "left"}
        drawn_paths: dict[int, Path] = {}
        for i in order:
            card = cards[i]
            archetype = archetypes[i]
            photo = diagram[1] if archetype == "diagram" else photos[i]
            photo_next = photos[(i + 1) % len(photos)]
            strip = strips.get(i)
            im, lay, attempts = still_layout.drawn(
                lambda tight, strip=strip: self._render_carousel_slide(archetype, photo, photo_next, card, i, total,
                                                                       poll, share, facebook, tight=tight,
                                                                       subject=subject, heading_frame=frame,
                                                                       strip=strip))
            drew = lay.meta.get("swipe_strip") or {}
            if i == 1 and drew.get("box"):
                strips[0] = {"side": "right", "y0": drew["box"][1], "y1": drew["box"][3]}
            path = out_dir / f"carousel_slide_{i + 1:02d}.jpg"
            self._save_slide(im, path)
            _delivered_contrast(lay, path)
            still_layout.save(path, lay, {"renderer": "multi_format.redraw_carousel_slide", "archetype": archetype,
                                          "photo": str(photo), "photo_next": str(photo_next), "card": card, "index": i,
                                          "total": total, "poll": poll, "share": share, "facebook": facebook,
                                          "subject": subject, "heading_frame": frame,
                                          **({"strip": drew} if drew else {})}, attempts)
            drawn_paths[i] = path
        slides = [drawn_paths[i] for i in range(total)]

        if facebook:
            write_collage_report(slides, out_dir)
        log.info("Generated %d multi-template carousel slides -> %s", len(slides), out_dir)
        return slides


_CONTRAST = re.compile(r"\b(?:versus|vs\.?|compared|unlike|contrast|above and below|then and now|modern)\b")
_SPAN_UNIT = (r"(?:(?:million|billion|thousand)\s+)?(?:years?|feet|ft|metres?|meters?|m|miles?|km|kilometres?|"
              r"kilometers?|tons?|tonnes|people|levels|storeys|stories|centuries)(?:\s+(?:ago|old|deep|tall|high|wide|bce?))?")
_SPAN = re.compile(r"(?<![\w.])(?:between\s+(?P<a>\d[\d,]*(?:\.\d+)?)\s+and\s+|(?P<a2>\d[\d,]*(?:\.\d+)?)\s*"
                   r"(?:-|\u2013|\u2014|to)\s*)(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>" + _SPAN_UNIT + r")\b",
                   re.IGNORECASE)


def range_figure(text: str) -> tuple[str, str] | None:
    """A stated span and its unit, whole: ("94\u2013104", "MILLION YEARS AGO") for "between 94 and 104 million years
    ago" or "94-104 million years ago", en dash and no spaces (round 3, 2026-11-02: the stat slide took the first
    number, "94", which is more precise than the sheet). None when the text states no span with a unit. A span whose
    ends are not in order ("1 to 3" is a span; "2025 to 12" is not) is no span."""
    for m in _SPAN.finditer(text or ""):
        lo = m.group("a") or m.group("a2")
        a, b = (float(x.replace(",", "")) for x in (lo, m.group("b")))
        if a < b:
            return f"{lo}\u2013{m.group('b')}", " ".join(m.group("unit").upper().split())
    return None


CAROUSEL_PHOTOS = "carousel_photos.json"
CAROUSEL_DIAGRAM = "carousel_diagram.json"
# The graphics that explain something drawn (graphics.make_all's file names), in order of preference; the number
# cards, the timeline and the map draw over a photograph and are not re-laid as a diagram slide.
DIAGRAM_KINDS = ("section", "ventilation", "defense", "scale", "route")
_DIAGRAM_WORDS = {
    "section": ("deep", "depth", "level", "levels", "storey", "storeys", "carved", "below", "down", "feet", "metres"),
    "ventilation": ("air", "shaft", "shafts", "ventilation", "breathe", "fresh"),
    "defense": ("door", "doors", "stone", "rolling", "roll", "seal", "sealed", "wheel"),
    "scale": ("tall", "high", "height", "deep", "depth", "wide", "size"),
    "route": ("route", "road", "journey", "path", "trade"),
}
DIAGRAM_SLIDES = (2, 3)   # slide 3 or 4 (0-based): after the hook and its first fact
# Round 4 (2026-11-02): with no explanatory graphic, the locator map's resting frame (graphic_map.jpg: relief, globe
# inset, place label) is the deck's drawn slide, so every carousel has one. It has no flat ground to crop to: it is
# laid full bleed (_render_diagram), its pin and label in the slide's upper part (MAP_FOCUS), the text over its foot.
MAP_KIND = "map"
MAP_FOCUS = (0.5, 0.36)   # this share of the poster's height (its centre: the map's pin) lands at this share of the slide's
_DIAGRAM_WORDS[MAP_KIND] = ("where", "desert", "located", "location", "country", "region", "map", "landmark",
                            "space", "orbit", "visible", "border", "coast", "near", "across")


MAP_FOOT_INK = 0.94   # the ink foot under the map slide's stack: the map's lines and labels there all but gone
MAP_CLEAR = 40        # design px from the foot's full ink to the stack's top (the kicker's cap)
MAP_FADE, MAP_FADE_GOOD, MAP_FADE_MIN = 160, 110, 56   # design px over which the foot rises to its ink: at most,
                                                       # preferred at least, and at least
MAP_GAP = 12          # design px from the place label and pin to where the foot starts
MAP_PIN = 30          # design px about the poster's centre the pin (22 px, its ink outline) takes
MAP_LABEL_REACH = 380  # design px above the pin the place label can stand (graphics.render_map: 72 px plus it)
MAP_LIFT_MAX = 160    # design px the map may rise above MAP_FOCUS to clear its labels
MAP_LABEL_INK = 200   # a pixel at least this bright in every channel is label ink (bone type, the scale bar)


def map_label_bands(poster, full: Image.Image, k: float, px) -> list[tuple[float, float, float, float]]:
    """The locator map's own labels as horizontal bands (x0, y0, x1, y1) in `full`'s pixels (the poster scaled by
    `k`): the label boxes graphics.render_map recorded beside its clip (<clip>.contrast.json: the place, the
    country, the scale bar's figures, a neighbour's name), each padded to take in its halo and the bar under it,
    and the rows of `full` that carry label ink (MAP_LABEL_INK: bone type and the bar's white line), for a poster
    with no record. Overlapping bands are merged."""
    import numpy as np
    boxes = []
    side = Path(str(Path(poster).with_suffix(".mp4")) + ".contrast.json") if poster else None
    try:
        record = json.loads(side.read_text(encoding="utf-8")) if side and side.is_file() else {}
    except (OSError, ValueError):
        record = {}
    pad = px(14)
    for e in record.get("elements") or []:
        b = e.get("box") or []
        if len(b) == 4:
            boxes.append((b[0] * k - pad, b[1] * k - pad, b[2] * k + pad, b[3] * k + pad))
    a = np.asarray(full.convert("RGB"), dtype=np.int16)
    ink = a.min(axis=2) >= MAP_LABEL_INK
    rows = np.nonzero(ink.sum(axis=1) >= 3)[0]
    if len(rows):
        gap = max(1, px(10))
        start = prev = int(rows[0])
        for r in [int(v) for v in rows[1:]] + [None]:
            if r is not None and r - prev <= gap:
                prev = r
                continue
            xs = np.nonzero(ink[start:prev + 1].any(axis=0))[0]
            boxes.append((float(xs.min()) - px(6), start - px(6), float(xs.max()) + px(6), prev + 1 + px(6)))
            if r is not None:
                start = prev = r
    boxes.sort(key=lambda b: b[1])
    merged: list[list[float]] = []
    for b in boxes:
        if merged and b[1] <= merged[-1][3]:
            m = merged[-1]
            merged[-1] = [min(m[0], b[0]), m[1], max(m[2], b[2]), max(m[3], b[3])]
        else:
            merged.append(list(b))
    return [tuple(m) for m in merged]


def is_map_poster(poster) -> bool:
    return Path(str(poster)).stem == f"graphic_{MAP_KIND}"


def diagram_poster(output_dir: Path, graphics: list[Path] | None = None) -> tuple[str, Path] | None:
    """(kind, poster) of the run's preferred explanatory graphic whose poster frame exists, else the locator map's
    (MAP_KIND), else None."""
    if graphics is None:
        folder = Path(output_dir) / "graphics"
        graphics = sorted(folder.glob("graphic_*.jpg")) if folder.is_dir() else []
    posters = {}
    for g in graphics or []:
        poster = Path(g).with_suffix(".jpg")
        kind = poster.stem.replace("graphic_", "", 1)
        if kind in (*DIAGRAM_KINDS, MAP_KIND) and poster.is_file():
            posters.setdefault(kind, poster)
    kind = next((k for k in DIAGRAM_KINDS + (MAP_KIND,) if k in posters), None)   # the map only as the fallback
    return (kind, posters[kind]) if kind else None


def carousel_diagram(output_dir: Path, cards: list[dict[str, Any]],
                     graphics: list[Path] | None = None) -> tuple[int, Path] | None:
    """(slide index, poster) for the carousel's one diagram slide, or None: the run's explanatory graphic on
    whichever of DIAGRAM_SLIDES says most about what it draws (else the first). The locator map (MAP_KIND, the
    fallback drawing) goes only on a slide whose words are about place (_DIAGRAM_WORDS["map"]: "visible from
    space ... desert landmark"), on any content slide (slide 2 included: the place is often said first), the one
    that says most, DIAGRAM_SLIDES first on a tie; with no such slide the deck has no drawn slide (round 4 review,
    2026-11-02: the map replaced the photo of a myth-bust about the crater theory). Decided once per run and kept
    in CAROUSEL_DIAGRAM, so the Instagram and Facebook copies, drawn at once, show it on the same slide."""
    output_dir = Path(output_dir)
    record = output_dir / CAROUSEL_DIAGRAM
    key = [f"{c.get('heading', '')}|{c.get('body', '')}" for c in cards]
    with _photo_lock(output_dir):
        try:
            kept = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
        except (OSError, ValueError):
            kept = {}
        if kept.get("key") == key and (not kept.get("poster") or Path(kept["poster"]).is_file()):
            return (int(kept["index"]), Path(kept["poster"])) if kept.get("poster") else None
        found = diagram_poster(output_dir, graphics)
        slots = [i for i in DIAGRAM_SLIDES if 0 < i < len(cards) - 1]
        chosen = None
        if found and slots:
            kind, poster = found
            words = set(_DIAGRAM_WORDS.get(kind, ()))

            def says(i):
                text = re.findall(r"[a-z]+", f"{cards[i].get('heading', '')} {cards[i].get('body', '')}".lower())
                return sum(w in words for w in text)
            if kind == MAP_KIND:
                slots = [i for i in range(1, len(cards) - 1) if says(i) > 0]
                index = max(slots, key=lambda i: (says(i), i in DIAGRAM_SLIDES, -i)) if slots else None
            else:
                index = max(slots, key=lambda i: (says(i), -i))
            chosen = (index, poster) if index is not None else None
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps({"key": key, "index": chosen[0] if chosen else None,
                                          "poster": str(chosen[1]) if chosen else ""}, indent=1, ensure_ascii=False),
                              encoding="utf-8", newline="\n")
        except OSError as e:
            log.warning("Carousel diagram choice not recorded (%s).", e)
        return chosen


def diagram_crop(poster: Image.Image) -> tuple[tuple[int, int, int], tuple[int, int, int, int]]:
    """(the poster's ground colour, the box of what it draws, padded): the ground is the median of its border."""
    import numpy as np
    a = np.asarray(poster.convert("RGB"), dtype=np.int16)
    border = np.concatenate([a[:8].reshape(-1, 3), a[-8:].reshape(-1, 3), a[:, :8].reshape(-1, 3), a[:, -8:].reshape(-1, 3)])
    ground = tuple(int(v) for v in np.median(border, axis=0))
    # Drawn: clearly off the ground (a soft backdrop gradient is not), in a row or column with a few such pixels.
    ink = np.abs(a - np.array(ground, dtype=np.int16)).max(axis=2) > 40
    ys, xs = np.nonzero(ink.sum(axis=1) >= 4)[0], np.nonzero(ink.sum(axis=0) >= 4)[0]
    if not len(xs) or not len(ys):
        return ground, (0, 0, poster.width, poster.height)
    pad = 28
    return ground, (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
                    min(poster.width, int(xs.max()) + 1 + pad), min(poster.height, int(ys.max()) + 1 + pad))
_photo_locks: Dict[str, threading.Lock] = {}
_photo_locks_guard = threading.Lock()


def _photo_lock(output_dir: Path) -> threading.Lock:
    with _photo_locks_guard:
        return _photo_locks.setdefault(str(Path(output_dir).resolve()), threading.Lock())


def _recorded_photos(output_dir: Path, cards: List[Dict[str, Any]]) -> List[Path]:
    """The photos a copy drawn before carousel_photos.json existed recorded for these cards ([] if none)."""
    from . import still_layout
    for copy in ("carousel", "carousel_fb"):
        folder = Path(output_dir) / copy
        recs = [still_layout.load(folder / f"carousel_slide_{i + 1:02d}.jpg") or {} for i in range(len(cards))]
        recipes = [r.get("recipe") or {} for r in recs]
        photos = [Path(r.get("photo") or "") for r in recipes]
        same = all((r.get("card") or {}).get("heading") == c.get("heading") and (r.get("card") or {}).get("body") == c.get("body")
                   for r, c in zip(recipes, cards))
        if same and all(str(x) not in ("", ".") and x.is_file() for x in photos):
            return photos
    return []


# ---------------------------------------------------------------- what a posted slide shows
# A slide's record (still_layout.json beside it) keeps its card and photos, so the publisher can say what each
# posted slide shows (alt text), credit every photo it shows, and pick Facebook's photos.

def slide_record(slide: Path) -> Dict[str, Any]:
    from . import still_layout
    return (still_layout.load(Path(slide)) or {}).get("recipe") or {}


def slide_photos(slide: Path) -> List[Path]:
    """The photos a slide shows: its own, and on a comparison slide the second one under it."""
    rec = slide_record(slide)
    photos = [rec.get("photo")] + ([rec.get("photo_next")] if rec.get("archetype") == "comparison" else [])
    return [Path(x) for x in photos if x]


def photo_credit(photo: Path) -> Dict[str, Any]:
    """The credits.json entry of a sourced photo ({} if none). A derived copy (crop, blur, upscale) has no entry
    beside it; its lineage leads back to the original."""
    photo = Path(photo)
    for _ in range(4):
        try:
            data = json.loads((photo.parent / "credits.json").read_text(encoding="utf-8"))
            images = data if isinstance(data, list) else data.get("images", [])
            entry = next((e for e in images if e.get("file") == photo.name), None)
        except (OSError, ValueError, AttributeError):
            entry = None
        if entry:
            return entry
        try:
            from .lineage import sidecar
            photo = Path(json.loads(sidecar(photo).read_text(encoding="utf-8"))["sources"][0]["path"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return {}
    return {}


CAROUSEL_SHARE_ALIKE = "📷 These slides are licensed CC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0/"


CAROUSEL_ADAPTED = "📷 Photos adapted: cropped, with text added."


def carousel_credits(slides: List[Path]) -> Tuple[List[str], bool]:
    """The credit line of every photo the slides show whose licence asks for one, in slide order, once each
    (credits.asset_attribution's format), and whether the slides could be read at all. A CC BY-SA photo makes
    the slides an adaptation under the same licence, which they then say (as the video does)."""
    from .credits import asset_attribution, is_share_alike
    lines, share_alike, readable = [], False, bool(slides)
    for slide in slides:
        if not slide_record(slide):
            readable = False
        for photo in slide_photos(slide):
            entry = photo_credit(photo)
            line = asset_attribution(entry) if entry else ""
            if entry and is_share_alike(entry.get("license", "")):
                share_alike = True
            if line and line not in lines:
                lines.append(line)
    # CC BY 4.0 s3(a)(1)(B): say that the photos were changed (each slide crops its photo and sets text on it)
    adapted = [CAROUSEL_ADAPTED] if lines else []
    return ([CAROUSEL_SHARE_ALIKE] if share_alike else []) + lines + adapted, readable


# ---------------------------------------------------------------- a Story frame's photo credit
# A Story goes out one frame per request with no caption (publisher.publish_photos: "Stories take none"), so a CC BY
# or BY-SA photo on a frame was credited nowhere (design review, 2026-10-29). CC licences ask for attribution "in any
# reasonable manner based on the medium": on a Story that is the frame itself. The frame names the author, the
# licence and its address, and says it was adapted (each frame crops its photo and sets text on it, as the carousel
# says); a BY-SA photo makes the frame an adaptation under BY-SA, which it then states, as the slides and the video do.
STORY_CREDIT_PX = 26        # ~9.5 pt on a phone: small, and read at a glance like a printed photo credit
STORY_CREDIT_LEADING = 34
STORY_CREDIT_MIN_PX = 22    # a long name or a share-alike notice is set smaller before it takes a fourth line
STORY_CREDIT_LINES = 3
STORY_CREDIT_GAP = 28       # between the frame's text block and the credit (the credit's backing fades over 18 px)
STORY_CREDIT_CONTRAST = 4.5  # WCAG AA for small text, against the pixels under the line
STORY_ADAPTED = "adapted: cropped, with text added"
STORY_SHARE_ALIKE = ("CC BY-SA 4.0", "creativecommons.org/licenses/by-sa/4.0")


def _short_url(url: str) -> str:
    return re.sub(r"^(?:https?:)?//(?:www\.)?", "", str(url or "").strip()).rstrip("/")


def story_credit(photo) -> Dict[str, Any]:
    """What a Story frame showing `photo` must credit on itself: {"required", "lines", ...}. Public-domain and CC0
    photos need none, and our own graphics and maps have no credits entry (their map data is public domain)."""
    from .credits import author_name, is_share_alike, needs_attribution
    entry = photo_credit(Path(photo)) if photo else {}
    if not entry:
        return {"required": False, "reason": "no credits entry (our own graphic, or a photo with no record)", "lines": []}
    licence = " ".join(str(entry.get("license") or "").split())
    if not needs_attribution(entry):
        return {"required": False, "reason": "licence asks for no attribution", "license": licence, "lines": []}
    author, share_alike, url = author_name(entry), is_share_alike(licence), _short_url(entry.get("license_url"))
    lines = [f"Photo: {author} · {licence} · {STORY_ADAPTED}"]
    # An adaptation carries the address of the licence it is under (BY-SA 3.0 s4(b), 4.0 s3(b)): for a BY-SA photo
    # that is BY-SA 4.0, the frame's own; a BY photo's frame gives the photo's licence address (BY 4.0 s3(a)(1)(C)).
    if share_alike:
        lines.append(f"This frame: {STORY_SHARE_ALIKE[0]} · {STORY_SHARE_ALIKE[1]}")
    elif url:
        lines.append(url)
    return {"required": True, "author": author, "license": licence, "license_url": entry.get("license_url") or "",
            "share_alike": share_alike, "source_url": entry.get("source_url") or "", "lines": lines}


def _credit_wrap(d, line: str, font, width: int) -> List[str]:
    """A credit line wrapped at its " · " breaks first (so "adapted: cropped, with text added" is never split from
    itself), and word by word only inside a part that is wider than the line on its own."""
    out: List[str] = []
    for part in line.split(" · "):
        if out and text_size(d, f"{out[-1]} · {part}", font)[0] <= width:
            out[-1] = f"{out[-1]} · {part}"
        else:
            out += wrap_text(d, part, font, width)
    return out


def _contrast(im: Image.Image, box, text_rgb) -> float:
    """WCAG contrast of `text_rgb` against the bright end (90th-percentile luminance) of the pixels in `box`."""
    import numpy as np
    region = np.asarray(im.convert("RGB").crop(tuple(int(v) for v in box)), dtype=np.float32) / 255.0
    lin = np.where(region <= 0.03928, region / 12.92, ((region + 0.055) / 1.055) ** 2.4)
    bg = float(np.percentile(lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), 90))
    return round((_rel_luminance(text_rgb) + 0.05) / (bg + 0.05), 2)


def story_credit_report(frames: List[Path]) -> Dict[str, Any]:
    """For the release report: {"credited": [{"frame", "lines"}], "problems": [...]}. Every frame whose photo's
    licence asks for attribution must carry that credit, drawn whole, at the contrast it needs; a frame without its
    layout record cannot show what it credited, which is a problem too."""
    credited, problems = [], []
    by_dir: Dict[str, Dict[str, Any]] = {}
    for frame in [Path(f) for f in frames]:
        folder = str(frame.parent)
        if folder not in by_dir:
            try:
                by_dir[folder] = {e.get("frame"): e for e in json.loads((frame.parent / "layout.json").read_text(encoding="utf-8"))}
            except (OSError, ValueError, AttributeError):
                by_dir[folder] = {}
        photo = slide_record(frame).get("photo")
        drawn = (by_dir[folder].get(frame.name) or {}).get("photo_credit")
        if not photo or drawn is None:
            problems.append(f"{frame.name}: no layout record of its photo and credit (drawn before 2026-10-29?)")
            continue
        expected = story_credit(photo)
        if not expected["required"]:
            continue
        if drawn.get("lines") != expected["lines"]:
            problems.append(f"{frame.name}: the frame credits {drawn.get('lines')}, its photo needs {expected['lines']}")
        elif not drawn.get("complete"):
            problems.append(f"{frame.name}: the credit could not be drawn whole (a character the font lacks): {drawn.get('drawn')}")
        elif float(drawn.get("contrast") or 0) < STORY_CREDIT_CONTRAST:
            problems.append(f"{frame.name}: the credit's contrast is {drawn.get('contrast')}:1, under {STORY_CREDIT_CONTRAST}:1")
        else:
            credited.append({"frame": frame.name, "lines": expected["lines"]})
    return {"credited": credited, "problems": problems}


ALT_TEXT_MAX = 1000  # Instagram's and Upload-Post's limit per photo
_CAMERA_FILE = re.compile(r"\b(?:DSCN?|IMG|DCP)_?\d{3,}\w*\b", re.IGNORECASE)


def _photo_subject(photo: Path) -> str:
    """What a photo shows, in plain words, from its archive record: the description, else the file title."""
    entry = photo_credit(photo)
    text = str(entry.get("description") or "").strip() or re.sub(
        r"^File:|\.(jpe?g|png|tiff?|webp)$", "", str(entry.get("title") or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", _CAMERA_FILE.sub(" ", text)).replace("_", " ")
    text = " ".join(text.split()).strip(" .,-")
    # Its first sentence: uploaders append notes ("Title is mistake. Tree image is taken ...").
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    first = parts[0]
    for more in parts[1:]:
        if len(first) >= 20:
            break
        first += " " + more
    text = first.strip(" .,-")
    return text[:300].rsplit(" ", 1)[0] if len(text) > 300 else text


def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if not text or text[-1] in ".?!" else text + "."


def slide_alt_text(slide: Path) -> str:
    """Alt text for one posted slide: what its photo shows (the archive's own description), then the words on
    it, in the case the script wrote them ("" when the slide has no record). Plain sentences, at most 1,000
    characters; it states nothing the slide and the photo's record do not."""
    rec = slide_record(slide)
    card = rec.get("card") or {}
    if not rec or not (card.get("heading") or card.get("body")):
        return ""
    subjects = [x for x in (_photo_subject(photo) for photo in slide_photos(slide)) if x]
    if rec.get("archetype") == "diagram" and is_map_poster(rec.get("photo") or ""):
        picture = "A locator map from the video."
    elif rec.get("archetype") == "diagram":
        picture = "A diagram from the video."
    elif len(subjects) > 1:
        picture = f"Two photos: {_sentence(subjects[0])[:-1]}; and {_sentence(subjects[1])}"
    elif subjects:
        picture = f"Photo: {_sentence(subjects[0])}"
    else:
        picture = ""
    from .script_engine import facebook_photo_caption, neutral_cues
    clean = facebook_photo_caption if Path(slide).parent.name == "carousel_fb" else neutral_cues
    words = [_sentence(clean(card.get("heading", ""))), _sentence(clean(card.get("body", "")))]
    if rec.get("archetype") == "debate":  # the closing slide's share line; no comments footer (2026-10-29)
        words += [_sentence(rec.get("share") or "")]
    text = " ".join(x for x in [picture, "Text on the slide:", *words] if x)
    if len(text) > ALT_TEXT_MAX:
        text = text[:ALT_TEXT_MAX - 1].rsplit(" ", 1)[0] + "…"
    return text


# Operator decision 2026-11-02, approved for the default 2026-11-03: Facebook's collage shows five tiles (the fifth
# under "+N"), so its gallery takes at most five (the cover, the three strongest content slides, the closing slide).
# FACEBOOK_PHOTOS is the cap facebook_pick and publisher.py use (4 until 2026-11-03); FACEBOOK_GALLERY is its old name.
FACEBOOK_PHOTOS = 5
FACEBOOK_GALLERY = FACEBOOK_PHOTOS


_MYTH_BUST = re.compile(r"\b(?:disproved|disproven|debunked|myths?|mistaken|wrongly|false|once thought|initially|"
                        r"considered|believed)\b", re.IGNORECASE)
_ANAPHORA = re.compile(r"^\W*(?:it|its|this|that|these|those|they|their|them|he|she|his|her|here|there|such)\b",
                       re.IGNORECASE)


# slide_strength's tiers (review of round 4, 2026-11-02): the figure slide is the deck's one number hero and the
# only tile that is not a heading-and-body stack, so it outranks the drawn slide, which outranks a myth-bust (a text
# claim, usually on a stand-in photo). With two content tiles the figure and the drawn slide both make the post.
STRENGTH_FIGURE, STRENGTH_DRAWN, STRENGTH_MYTH_BUST = 3, 2, 1


def slide_strength(rec: dict[str, Any]) -> int:
    """How strongly a slide's record says it stands on its own as a tile (round 4, 2026-11-02), highest first:
    STRENGTH_FIGURE for a figure slide (a stat, or the colossal_stat archetype), STRENGTH_DRAWN for the drawn slide
    (diagram), STRENGTH_MYTH_BUST for a myth-bust (its heading or body contradicts an earlier view: "IMPACT THEORY
    DISPROVED"); else 0. A slide that is several of these takes the highest."""
    recipe = rec.get("recipe") or {}
    card = recipe.get("card") or {}
    if recipe.get("archetype") == "colossal_stat" or any(e.get("name") == "stat" for e in rec.get("elements") or []):
        return STRENGTH_FIGURE
    if recipe.get("archetype") == "diagram":
        return STRENGTH_DRAWN
    return STRENGTH_MYTH_BUST if _MYTH_BUST.search(f"{card.get('heading', '')} {card.get('body', '')}") else 0


def standalone_problems(rec: dict[str, Any]) -> list[str]:
    """Why a slide does not stand alone as a Facebook photo (each opens alone, without the caption or its
    neighbours): a body that opens on a pronoun ("It ...", "This ...") whose referent is on another slide, or a
    heading that is only a figure with no unit beside it. [] when it stands alone; nothing is rewritten."""
    card = (rec.get("recipe") or {}).get("card") or {}
    out = []
    if _ANAPHORA.match(str(card.get("body") or "")):
        out.append("its body opens on a word that points to another slide")
    heading = " ".join(str(card.get("heading") or "").split())
    if re.fullmatch(r"[\d,.\u2013+-]+", heading or "x"):
        out.append("its heading is a bare number")
    return out


def facebook_pick(slides: List[str], limit: int = FACEBOOK_PHOTOS) -> List[str]:
    """The slides Facebook's multi-photo post gets, at most `limit` (FACEBOOK_PHOTOS, 5): its collage puts "+N"
    over the last tile it shows, so a seven-photo post hid the closing share-and-follow slide (platform audit,
    2026-10-28). The cover, the
    closing slide, and between them the strongest content slides: those drawn as designed (no layout problem,
    nothing shortened), standing alone (standalone_problems), a single photo, the strongest (slide_strength: a
    figure, then the drawn slide, then a myth-bust), showing a photo the post does not already show, then the
    earliest (the script's order is its order of importance: the reveal comes first). Kept in slide order."""
    slides = list(slides)
    if len(slides) <= limit:
        return slides
    from . import still_layout
    first, last, middle = slides[0], slides[-1], list(enumerate(slides[1:-1], 1))
    shown = {str(p) for p in slide_photos(Path(first)) + slide_photos(Path(last))}

    def rank(item):
        i, slide = item
        rec = still_layout.load(Path(slide)) or {}
        photos = {str(p) for p in slide_photos(Path(slide))}
        # A single full-bleed photo before a two-photo stack (round 3, 2026-11-02): a stack's hard seam reads as a
        # deck slide in a standalone lightbox and spends a collage tile on two half pictures.
        # Round 4: a photo that does not stand alone (its body leans on another slide) after one that does; then the
        # strongest (a figure, the drawn slide, a myth-bust) before photo novelty, so the order never rests on
        # which stand-in photos happened to repeat.
        return (bool(rec.get("problems")), len(rec.get("shortened") or []), bool(standalone_problems(rec)),
                len(photos) > 1, -slide_strength(rec), bool(photos and photos <= shown), i)

    picked = []
    while middle and len(picked) < limit - 2:  # one at a time: a pick's photos count as shown for the next
        best = min(middle, key=rank)
        middle.remove(best)
        picked.append(best)
        shown |= {str(p) for p in slide_photos(Path(best[1]))}
    return [first] + [slide for _, slide in sorted(picked)] + [last]


# Facebook's collage for a multi-photo post of 4:5 photos (round 3, 2026-11-02), as tiles of a square frame, each
# (x, y, w, h) in shares of it: four photos as one large portrait beside three stacked, five as two over three. Each
# tile shows the centre crop of its photo. No "+N" is drawn: the gallery sends exactly as many photos as tiles.
FACEBOOK_COLLAGE = {
    4: ((0, 0, 2 / 3, 1), (2 / 3, 0, 1 / 3, 1 / 3), (2 / 3, 1 / 3, 1 / 3, 1 / 3), (2 / 3, 2 / 3, 1 / 3, 1 / 3)),
    5: ((0, 0, 1 / 2, 1 / 2), (1 / 2, 0, 1 / 2, 1 / 2), (0, 1 / 2, 1 / 3, 1 / 2), (1 / 3, 1 / 2, 1 / 3, 1 / 2),
        (2 / 3, 1 / 2, 1 / 3, 1 / 2)),
}
COLLAGE_TEXT = ("kicker", "heading", "body", "stat", "share")


def collage_crop(size: tuple[int, int], aspect: float) -> tuple[int, int, int, int]:
    """The centre crop of a `size` photo that a tile of `aspect` (width / height) shows."""
    w, h = size
    if w / h < aspect:          # the tile is wider: the photo's top and bottom are cut
        vh = w / aspect
        return (0, round((h - vh) / 2), w, round((h + vh) / 2))
    vw = h * aspect
    return (round((w - vw) / 2), 0, round((w + vw) / 2), h)


def facebook_collage_report(slides: list[Path], board: Path | None = None, width: int = 1080) -> dict[str, Any]:
    """The posted Facebook set in its collage (FACEBOOK_COLLAGE): per tile, what its centre crop shows and which
    text elements it cuts (from each slide's layout record). {"layout", "tiles": [...], "problems": [...]}. With
    `board`, the collage is drawn there as a QA image. A set with no collage model (under 4 or over 5) is reported
    as such, with no problems."""
    from . import still_layout
    slides = [Path(x) for x in slides]
    tiles = FACEBOOK_COLLAGE.get(len(slides))
    if not tiles:
        return {"layout": None, "tiles": [], "problems": []}
    out, problems = [], []
    canvas = Image.new("RGB", (width, width), brand.BONE) if board else None
    for slide, (tx, ty, tw, th) in zip(slides, tiles):
        rec = still_layout.load(slide) or {}
        size = tuple(rec.get("size") or MultiFormatGenerator.CAROUSEL_SIZE)
        crop = collage_crop(size, tw / th)
        cut = []
        for e in rec.get("elements") or []:
            if e["name"] in COLLAGE_TEXT:
                b = e["box"]
                if b[0] < crop[0] or b[1] < crop[1] or b[2] > crop[2] or b[3] > crop[3]:
                    cut.append(e["name"])
        out.append({"slide": slide.name, "tile": [round(v, 4) for v in (tx, ty, tw, th)], "crop": list(crop),
                    "cut": cut})
        problems += [f"{slide.name}: the collage tile cuts its {name}" for name in cut]
        if canvas is not None and slide.is_file():
            gap = 4
            box = (round(tx * width) + gap // 2, round(ty * width) + gap // 2,
                   round((tx + tw) * width) - gap // 2, round((ty + th) * width) - gap // 2)
            with Image.open(slide) as im:
                tile = im.convert("RGB").crop(crop).resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
            canvas.paste(tile, box[:2])
            if cut:
                ImageDraw.Draw(canvas).rectangle(box, outline=brand.CORAL, width=6)
    if canvas is not None:
        Path(board).parent.mkdir(parents=True, exist_ok=True)
        canvas.save(board, quality=90)
    return {"layout": f"{len(slides)} photos", "tiles": out, "problems": problems}


COLLAGE_REPORT = "collage_report.json"


def write_collage_report(slides: list[Path], out_dir: Path) -> dict[str, Any]:
    """ADVISORY, not a gate: the collage report (facebook_collage_report) of the set publisher.py posts by default
    (facebook_pick(slides), FACEBOOK_PHOTOS), written to <carousel_fb>/collage_report.json with its board
    (collage_board.jpg) and each cut logged as a warning. Nothing fails, redraws or blocks on it: no QA certifier
    reads this file yet (video_qa would have to), and the cuts it names (a collage tile's centre crop through a
    slide's kicker, heading or body) are an OPEN defect of the Facebook copy's layout, not fixed by recording them."""
    out_dir = Path(out_dir)
    posted = [Path(x) for x in facebook_pick([str(x) for x in slides])]
    rep = dict(facebook_collage_report(posted, out_dir / "collage_board.jpg"), advisory=True,
               posted=[x.name for x in posted])
    try:
        (out_dir / COLLAGE_REPORT).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("Collage report not written to %s (%s).", out_dir, e)
    for problem in rep["problems"]:
        log.warning("Facebook collage (advisory): %s", problem)
    return rep


def _delivered_contrast(lay, path: Path) -> None:
    """Each placed element's ratio measured again on the saved JPEG (what is posted), written into its layout
    record, which still_layout keeps and video_qa.still_contrast_problems asserts (2026-10-29)."""
    from .typography import remeasure
    plates = getattr(lay, "plates", None)
    if not plates:
        return
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            remeasure(lay, im, plates)
            platform = platform_encode(im)
    except OSError as e:  # the planned ratios stay recorded
        log.warning("Contrast not re-measured on %s (%s).", Path(path).name, e)
        return
    # Round 3 (2026-11-02): measured again after a platform-like downscale and recompression; an element under 4.5
    # there is flagged in its element record (`contrast_platform`, `platform_low`, both kept in still_layout.json)
    # and listed in lay.meta["platform_contrast_low"]. ADVISORY, not a gate: only the delivered-JPEG `contrast` is
    # asserted (video_qa.still_contrast_problems); nothing asserts the platform figures, and lay.meta is not written
    # to still_layout.json (still_layout.Layout.to_dict leaves meta out), so the still-level list lives only in
    # memory and in the warning below. The per-element flags are the durable record.
    low = []
    for e in getattr(lay, "elements", []):
        ps = [p for p in plates.get(e["name"], []) if not p.empty]
        vals = [v for v in (p.measure(platform) for p in ps) if v is not None]
        if vals:
            e["contrast_platform"] = round(min(vals), 2)
            if e["contrast_platform"] < brand.TEXT_CONTRAST:
                e["platform_low"] = True
                low.append(e["name"])
    if isinstance(getattr(lay, "meta", None), dict):
        lay.meta["platform_contrast_low"] = low
    if low:
        log.warning("%s: under %.1f:1 after a platform re-encode: %s", Path(path).name, brand.TEXT_CONTRAST, low)


def platform_encode(im: Image.Image) -> Image.Image:
    """`im` as a platform delivers it, back at its own size for measuring: downscaled to PLATFORM_WIDTH, JPEG at
    PLATFORM_QUALITY with 4:2:0 chroma, scaled up again."""
    import io
    w, h = im.size
    small = im.convert("RGB").resize((PLATFORM_WIDTH, max(1, round(h * PLATFORM_WIDTH / w))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=PLATFORM_QUALITY, subsampling=2)
    buf.seek(0)
    with Image.open(buf) as back:
        return back.convert("RGB").resize((w, h), Image.Resampling.BICUBIC)


def _font_family(font) -> str:
    """The bundled family a loaded font came from (for refitting it at another size)."""
    from .typography import FONT_FILES
    base = Path(getattr(font, "path", "") or "").name
    return next((n for n, f in FONT_FILES.items() if f == base), "Montserrat")


def redraw_story_frame(path: Path, recipe: Dict[str, Any]) -> Dict[str, Any]:
    """Draws one Story frame again with the tighter fit, from its kept recipe, in place (still_vision: the
    look reported its text cut off or overlapping); updates layout.json and the frame's layout record."""
    from . import still_layout
    gen = MultiFormatGenerator()
    im, lay = gen._render_story_frame(Path(recipe["photo"]), recipe["slide"], int(recipe["index"]), int(recipe["count"]),
                                      recipe.get("poll") or {}, tight=True)
    im.save(path, quality=95)
    _delivered_contrast(lay, path)
    layout_file = Path(path).parent / "layout.json"
    try:
        entries = json.loads(layout_file.read_text(encoding="utf-8")) if layout_file.is_file() else []
    except (OSError, ValueError):
        entries = []
    entry = {**lay.meta, "frame": Path(path).name}
    entries = [entry if e.get("frame") == entry["frame"] else e for e in entries] if any(
        e.get("frame") == entry["frame"] for e in entries) else entries + [entry]
    layout_file.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    prior = still_layout.load(path) or {}
    return still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)


SUBJECT_MAX_WORDS = 6


def standalone_subject(script_payload: dict[str, Any]) -> str:
    """The subject's name for Facebook's standalone photos: the carousel cover's heading ("THE EYE OF THE SAHARA"),
    the script's own words, never a qualifier it did not state; "" when it is missing or too long to be a name."""
    from .script_engine import facebook_photo_caption
    cards = script_payload.get("carousel_slides") or []
    heading = " ".join(facebook_photo_caption(str((cards[0] if cards else {}).get("heading") or "")).split())
    heading = heading.rstrip(" .!?:")
    return heading if heading and len(heading.split()) <= SUBJECT_MAX_WORDS else ""


def _redraw_strip(kept: dict[str, Any] | None, path: Path | None = None) -> dict[str, Any] | None:
    """The swipe strip a redrawn slide draws again: the same side; on slide 1 at the height slide 2's kicker rule
    has now (its record beside `path`, carousel_slide_02.jpg), so the line still meets across the seam after
    slide 2 was redrawn (round 4 review, 2026-11-02); the kept height only when slide 2 has no record. None when
    slide 2 now draws no strip."""
    if not kept or kept.get("side") is None:
        return None
    if kept.get("side") == "left":
        return {"side": "left"} if kept.get("box") else None
    if path is not None:
        from . import still_layout
        rec = still_layout.load(Path(path).with_name("carousel_slide_02.jpg"))
        if rec is not None:
            box = ((rec.get("recipe") or {}).get("strip") or {}).get("box")
            return {"side": "right", "y0": box[1], "y1": box[3]} if box else None
    box = kept.get("box") or [0, kept.get("y0"), 0, kept.get("y1")]
    if box[1] is None or box[3] is None:
        return None
    return {"side": "right", "y0": box[1], "y1": box[3]}


# ---------------------------------------------------------------- Instagram's swipe strip
# Round 4 (2026-11-02): a non-verbal swipe cue on the Instagram copy only. Slide 2's gold kicker rule runs in from
# the slide's left edge, and slide 1 carries the same strip's first SWIPE_STRIP design px at the same height, cut at
# its right edge: across the seam it reads as one line that continues onto the next slide. No chevron and no "swipe"
# words (operator, 2026-10-28). The Facebook copy is a standalone grid and draws none. Slide 1 draws its part only
# where nothing of its own text is near; else it draws none and says why (lay.meta["swipe_strip"]).
SWIPE_STRIP = 72          # design px of the strip on slide 1 (96 at 1440)
SWIPE_CLEAR = 24          # design px it keeps from any text of slide 1


def swipe_strip(im: Image.Image, lay, strip: dict[str, Any]) -> Image.Image:
    """Draws `strip` ({"side": "left"} on slide 2: from the left edge to its kicker rule; {"side": "right", "y0",
    "y1"} on slide 1: the last SWIPE_STRIP px to the right edge) in gold; recorded in lay.meta["swipe_strip"]."""
    cw = im.width
    px = MultiFormatGenerator._px(cw)
    if strip.get("side") == "left":
        rule = lay.meta.get("kicker_rule")
        if not rule:
            lay.meta["swipe_strip"] = {"side": "left", "skipped": "the slide has no kicker rule"}
            return im
        box = [0, rule[1], rule[0], rule[3]]
    else:
        box = [cw - px(SWIPE_STRIP), int(strip["y0"]), cw, int(strip["y1"])]
        clear = px(SWIPE_CLEAR)
        hit = [e["name"] for e in lay.elements if e["name"] != "header_brand" and e["box"][2] + clear > box[0]
               and e["box"][1] - clear < box[3] and e["box"][3] + clear > box[1]]
        if hit:
            lay.meta["swipe_strip"] = {"side": "right", "skipped": f"it would touch the slide's {hit[0]}"}
            return im
    im = im.convert("RGB")
    if box[2] > box[0]:
        ImageDraw.Draw(im).rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=GOLD)
    lay.meta["swipe_strip"] = {"side": strip.get("side"), "box": box}
    return im


def redraw_carousel_slide(path: Path, recipe: Dict[str, Any], tight: bool = True) -> Dict[str, Any]:
    """Draws one carousel slide again with the tighter fit (or `tight` as given), from its kept recipe, in place.
    The record keeps the strip it drew now. When slide 2's strip moved (its kicker rule did), slide 1 is drawn
    again at its own fit so its part of the strip meets slide 2's across the seam (round 4 review, 2026-11-02)."""
    from . import still_layout
    path = Path(path)
    gen = MultiFormatGenerator()
    im, lay = gen._render_carousel_slide(recipe["archetype"], Path(recipe["photo"]), Path(recipe["photo_next"]),
                                         recipe["card"], int(recipe["index"]), int(recipe["total"]),
                                         recipe.get("poll") or {}, recipe.get("share") or "", bool(recipe.get("facebook")),
                                         tight=tight, subject=recipe.get("subject") or "",
                                         heading_frame=int(recipe.get("heading_frame") or 2),
                                         strip=_redraw_strip(recipe.get("strip"), path))
    gen._save_slide(im, path)
    _delivered_contrast(lay, path)
    prior = still_layout.load(path) or {}
    old_strip = recipe.get("strip") or {}
    recipe = {k: v for k, v in recipe.items() if k != "strip"}
    drew = lay.meta.get("swipe_strip") or {}
    if drew:
        recipe["strip"] = drew
    entry = still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)
    if old_strip.get("side") == "left" and old_strip.get("box") != drew.get("box"):
        first = path.with_name("carousel_slide_01.jpg")
        rec1 = still_layout.load(first) or {}
        r1 = rec1.get("recipe") or {}
        if r1 and int(r1.get("index", -1)) == 0 and not r1.get("facebook"):   # a slide 1 drawn with no part as well
            redraw_carousel_slide(first, {**r1, "strip": r1.get("strip") or {"side": "right"}},
                                  tight=bool(rec1.get("tight")))
    return entry
