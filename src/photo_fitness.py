"""Is a photograph fit for the window it is put in? One measure, used by every picker and cropper (design-freeze
cycle 5, PHOTO stream).

Four cycles patched one root cause per surface and it came back on the next one each time: a Reel cover whose
photograph stopped short of the frame's top and was padded with a blurred copy of its own edge; a 16:9 end screen whose
ground kept its source's black oval surround under a feather; a carousel slide recropped to clear a black wedge that
then showed sand and no subject; a masthead set over a spacecraft's solar array; a then-and-now pair cut from two
places of one statue. Each is the same question asked nowhere: does THIS photograph, cropped to THIS window, show a
whole rectangle of picture with its subject in view and nothing foreign under the type?

A placement is (photograph, Window). The Window is the output size, its `view` (the part of the output where the
photograph shows unobscured: a full-bleed slide's photo above its text stack) and its `protected` boxes (where type or
a mark sits: a masthead, a heading). `place()` measures the crop the surface would take and, when it is unfit, looks
for a fit one inside the photograph's own content. Everything is measured on the pixels, at ANALYSIS px; no model, no
network, no topic and no photo list.

Measures (each in the record `place()` returns, so a surface's layout record and the corpus proof read the same
numbers):

- content: the photograph's CONTENT is what is left of it without its frame: hero_window's clean region (no caption
  strip, banner row or panel seam) less every region of digital black that touches its edge (a spacecraft window's
  wedge, an oval surround, a letterbox, a scan border: `matte`). `content_box` is the largest whole rectangle of it.
  "matte": the share of the crop that is frame. A fit crop shows none (MATTE_MAX).
- coverage: "upscale", the enlargement the crop needs to fill the output (source px per output px, inverted), against
  UPSCALE_MAX (the hook card's refusal, design_cards.UPSCALE_MAX, is this constant). A photograph is never padded to
  reach a size: a crop that would need a pad is a crop that needs a larger enlargement, and is refused the same way.
- subject: the photograph's SALIENCE is where it differs from itself: local luma detail and distance from its own
  dominant colour (`salience`, summing to 1 over the content). No detector is claimed: where a subject was REVIEWED
  (cover_design.reviewed_focus) its box is the subject and "focus" is the share of it in view (all of it,
  FOCUS_KEEP; a window with a `hold` box asks for all of it there and Window.focus_min of it in the view). Otherwise the measure
  is relative to what the photograph itself offers this window: "keep" is the salience in the crop's view over the
  most any crop of the same shape holds in its view (SUBJECT_KEEP). A crop that keeps the photograph's emptiest half
  fails it whatever the subject is. "keep" compares crops of ONE shape, so it cannot see what the shape itself leaves
  out (a 2:1 miniature cut to 9:16 keeps a quarter of itself in its best crop as in its worst). A window that crops an
  unreviewed photograph hard asks for "shown" as well (Window.shown_min): the share of the photograph's whole
  salience that lies where the photograph shows unobscured (`hold`, else the view); with a `tile` (the Reel cover's
  profile-grid square) the tile must show TILE_SHARE of that ("shown_tile"), and with `centre_half` the crop must
  hold the photograph's central half on both axes (lander ruling, second review).
- flat: the share of the view whose blocks have no spread of luma (FLAT_STD over FLAT_BLOCK: sand, sky, a wall,
  out-of-focus ground; the panel's own block measure), against FLAT_MAX.
- tone: "extreme", the share of the view that is near-white or near-black (TONE_WHITE, TONE_BLACK), against TONE_MAX;
  asked only of a hero (hero_photo.pick: Window.checks names it), where another photograph of the run can lead.
- protected: per protected box, "foreign" (hero_window.foreign_object: a saturated, straight-edged man-made shape)
  "line" (the longest straight strong edge, against LINE_MAX) and "step" (two tonal masses, against STEP_MAX):
  type is not set on a wing, a solar array or across a roofline; "busy" (the share of strong edges) is recorded, and
  a MOVED window may not raise it past BUSY_MOVE_MAX (the asked window is not judged on it: the masthead's own
  plan answers a busy ground).

The ladder when a crop is unfit (the brief's order; never a blur pad, a feather or a dim):
1. another photograph the run already has that is fit: `rank()` orders candidates, the pickers call it;
2. another crop of this photograph: `place()` searches positions, then closer crops up to ZOOM_MAX and UPSCALE_MAX,
   inside the content, when the asked crop shows the frame, is enlarged past the limit, sets type on a structure or
   loses a reviewed subject (the certain measures); an unreviewed photograph's salience measures (SOFT) choose
   between those crops and between photographs, and are recorded, but move no window by themselves;
3. the surface's designed alternative layout (the caller's: the record says "unfit" and why);
4. a recorded refusal or omission (the caller's, with `reason()`).

Thresholds are set from the corpus distribution (docs/reviews/freeze-system-2026-11-15/corpus/photo_fitness.py prints
it over every dev run's photographs and windows), not from the board's topics; the numbers are beside each constant.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float]

ANALYSIS = 256            # px: the longer side every measure is taken at
THUMB = 640               # px: the longer side the protected boxes are read at (hero_window.deck_window's)
UPSCALE_MAX = 2.0         # the most a crop is enlarged to fill its output (the hook card's limit since QF-39)
ZOOM_MAX = 1.6            # the closest a remedy crop goes past the plain fit (a detail, not a different picture)
ZOOM_STEPS = (1.0, 1.1, 1.2, 1.3, 1.45, 1.6)
GRID = 9                  # candidate positions along each free axis

# the frame (matte): digital black touching the picture's edge. Lane D cycle 4 measured it on the dev pool (541
# photos): a source's own frame sits at a mean luma of 0.1-0.8, a photographed dark (a black cloth, a museum case, a
# doorway, a mine's shadow) at 4-12 and never under 3. Lane D took every pixel at 12 or under and asked the region to
# be even; a dark subject that touches the frame then merges with it and the whole region is no longer even (the
# Richat 16:9 master's last frame: one 38 % region at 12, its four black corners at 4). This rule takes digital black
# itself. A video frame's black carries the encoder's noise (matte_mask(video=True): twice the levels).
MATTE_LUMA = 4            # digital black: a frame pixel (a photographed dark is lighter and uneven)
MATTE_STD = 2.5
MATTE_MEAN = 2.0
MATTE_MIN_AREA = 0.003    # of the picture: a smaller dark corner is not a frame
MATTE_MAX = 0.002         # of a crop: more than this of frame in it shows as an edge
# Which dark is the FRAME (second review of cycle 5, B5 / B6: a shape-only rule took a night sky over a straight
# horizon and a shelf against a black ground for frames, and missed a plate's foot border broken by its caption, a
# slide mount's edge and a scan's dark surround): frame_bands judges shape and content. A side's band is the run of
# lines from the edge each FRAME_LINE dark (luma FRAME_DARK or under) between the side's FRAME_ENDS, ending in a hard
# cut (FRAME_STEP, FRAME_CRISP, FRAME_FLAT), read on the THUMB-px picture (a thin mount edge is lines deep there);
# it claims at most FRAME_BAND_MAX of the depth, or
# FRAME_PAIR_MAX with an opposite band of the same depth (letterbox, pillarbox); unpaired bands together at most
# FRAME_SHARE_MAX of the picture, and a lone band (no other side framed) at most FRAME_LONE_MAX. A mount's rounded
# corners count in two corners or more. Measured on the dev pool (353 photographs) and the review's constructed cases:
# corpus/photo_fitness.py --frames prints the table.
FRAME_DARK = 40
FRAME_LINE = 0.60
FRAME_TEXT_DARK = 0.45     # a caption line in a band: at least this dark ...
FRAME_TEXT_EDGES = 0.12    # ... and alternating dark / bright on this share of its length (glyphs)
FRAME_ENDS = 0.25          # the side's central half: a mount's rounded corners and a scan's skew stay out of it
FRAME_STEP = 0.45          # the dark share's drop across the cut, within two lines
FRAME_TRANSITION = 0.30    # a single line between the band and the picture, part of each
FRAME_CRISP = 0.20         # the picture's lines after the cut, at most this dark ...
FRAME_FLAT = 0.05          # ... or as dark two lines on (no gradient into the black)
FRAME_BAND_MAX = 0.20
FRAME_PAIR_MAX = 0.35
FRAME_SHARE_MAX = 0.30
FRAME_LONE_MAX = 0.06        # a band with no band on any other side, at most this deep
FRAME_CORNER = 0.12
FRAME_CORNER_MIN = 8      # analysis px: a mount's corner alone in it is a frame from this size
# A window moved without a REVIEWED subject box keeps the asked window's centre: the moved crop holds the central
# MOVE_HOLD of the asked crop on each axis, and its view keeps MOVE_KEEP of the salience the asked view held. "keep"
# compares crops with each other, not with the subject: a spacecraft is more salient than the crater it flies over.
MOVE_HOLD = 0.5
MOVE_KEEP = 0.70

# The soft measures' thresholds are the corpus' own 5 % tail: over the 353 pool photographs of the dev runs in every
# photo window (5289 asked crops; corpus/photo_fitness.py --distribution prints it), the 5th percentile of "keep" is
# 0.701 and of "density" 0.732, the 95th of "flat" 0.739, of "extreme" 0.536, of "step" 142.5 and of "line" 0.600
# ("line" is also bimodal: 2160 boxes at 0, 1136 between 0.2 and 0.5, 146 at 0.8 and over, 51 between 0.6 and 0.8:
# the threshold lies in the valley). None is set from a board topic: the asked crop of a photograph is unfit on a
# measure when it is among the worst twentieth of what the runs' own photographs give that measure.
FLAT_BLOCK = 5            # analysis px: the block a patch's luma spread is taken over (about 48 px of a 1440 px slide)
FLAT_STD = 4.0            # luma std: at or under this a block is flat (the rubric's CD1 measure)
FLAT_MAX = 0.74           # of the view
SUBJECT_KEEP = 0.70       # of the best any crop of this size keeps
DENSITY_MIN = 0.73        # the view's salience over its share of the content: under 1 the view is emptier than the
                          # picture as a whole
TILE_SHARE = 0.5          # of what a window's `hold` shows of an unreviewed photograph, its `tile` shows at least
FOCUS_KEEP = 0.995        # of a reviewed subject's box in view: all of it (type is never set on it)
# A subject larger than the window (design-freeze cycle 6, SUBJECT stream, round 2): "all of it" is judged against
# what the window CAN hold. With the labelled subjects fed as the focus, "all of it" made a wide subject unfit in every
# narrow window (819 placements fit -> unfit) and the search then took zoomed or shifted crops that showed less of it
# (75 placements). So a window asks for the subject as whole as any crop of its shape at the plain fit can show it
# (`focus_need` / `held_need` in the record), and a moved crop never shows less of it than the asked crop.
# Window.hold_min: the least of the subject a window's `hold` must keep when it cannot keep all of it; under it the
# window is unfit (the Reel cover then stands contained: the subject whole, smaller). 0 for the slide and Story
# windows (the deck shows the photograph as it can); SUBJECT_CUT_MAX for the cover.
SUBJECT_CUT_MAX = 0.85    # of a subject: a crop keeping less cuts it (the subject stream's failure measure)
SUBJECT_WHOLE = 0.98      # of a subject in a cover's hold: nothing a viewer reads as cut (lander ruling, round 3)
BUSY_EDGE = 24.0          # luma gradient: a strong edge (recorded as "busy"; half the boxes are over 0.19: no gate)
BUSY_MOVE_MAX = 0.44      # of a protected box: a MOVED window sets type on no busier ground than this (the corpus'
                          # upper quartile, 0.441 over 8560 boxes) unless the asked window's was busier still: a move
                          # off black space set a masthead on a spacecraft's module (0.09 -> 0.83)
TONE_WHITE, TONE_BLACK = 232, 22   # luma: near-white, near-black (a tile of either reads flat grey or murky in a feed)
TONE_MAX = 0.54           # of the view: only a window that asks for it ("tone" in Window.checks: the hero's)
LINE_CANNY = (100, 220)   # a strong edge for the straight-line read under type
STEP_SHARE = 0.10         # each tonal mass of a protected box, at least (a speck is not a mass)
STEP_MAX = 142.0          # luma between the two masses: type set across a boundary this hard
LINE_MAX = 0.60           # of a protected box's width: a straight strong edge this long is a foreign structure


@dataclass(frozen=True)
class Window:
    """Where a photograph is put: `size` (output px), `view` (output px box where it shows unobscured; None: all of
    it), `protected` (output px boxes under type or a mark), `name` (the surface's, for the record)."""
    size: tuple[int, int]
    view: Box | None = None
    protected: tuple[Box, ...] = ()
    name: str = ""
    upscale_max: float = UPSCALE_MAX
    zoom_max: float = ZOOM_MAX
    checks: tuple[str, ...] = ("matte", "upscale", "subject", "flat", "protected")   # the measures that can fail it
    hold: Box | None = None   # output px box a reviewed subject must lie whole in (None: the view)
    focus_min: float = FOCUS_KEEP   # the share of a reviewed subject the VIEW must show (with `hold`: under 1)
    hold_min: float = 0.0     # with a subject the hold cannot keep whole: the least share it must keep (else unfit)
    shown_min: float = 0.0    # of an UNREVIEWED photograph's whole salience, the least `hold` (else the view) shows
    tile: Box | None = None   # output px: a second view the photograph is seen through (the profile-grid square)
    centre_half: bool = False   # the crop holds the photograph's central half on both axes

    def view_box(self) -> Box:
        w, h = self.size
        if self.view is None:
            return (0.0, 0.0, float(w), float(h))
        x0, y0, x1, y1 = self.view
        x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(float(w), x1), min(float(h), y1)
        return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else (0.0, 0.0, float(w), float(h))


@dataclass
class Analysis:
    """One photograph's maps at ANALYSIS px (of its CLEAN picture: hero_window.clean's region)."""
    size: tuple[int, int]                 # the clean picture's px
    scale: float                          # analysis px per picture px
    matte: np.ndarray                     # bool: frame pixels
    detail: np.ndarray                    # float32: local luma gradient (0-255 per px)
    salience: np.ndarray                  # float32: sums to 1 over the content
    rgb: np.ndarray                       # uint8 HxWx3
    content_box: tuple[int, int, int, int]   # picture px: the largest whole rectangle of content
    matte_share: float
    focus: Box | None = None              # picture px: the reviewed subject, when the photo has one
    thumb: Image.Image | None = None      # the clean picture at THUMB px (the protected boxes are read on it)
    _sat: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def table(self, name: str) -> np.ndarray:
        """The summed-area table of one map ("matte", "flat", "salience", "edge", "extreme"), made on first use."""
        if name not in self._sat:
            if name == "extreme":
                lum = self.rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
                src = ((lum >= TONE_WHITE) | (lum <= TONE_BLACK)) & ~self.matte
            else:
                src = {"matte": self.matte, "flat": self._flat() & ~self.matte,
                       "salience": self.salience, "edge": self.detail >= BUSY_EDGE}[name]
            a = np.zeros((src.shape[0] + 1, src.shape[1] + 1), np.float64)
            a[1:, 1:] = np.cumsum(np.cumsum(src.astype(np.float64), axis=0), axis=1)
            self._sat[name] = a
        return self._sat[name]

    def _flat(self) -> np.ndarray:
        """Where the picture is flat: the luma's spread (std) over FLAT_BLOCK px is FLAT_STD or under (the panel's
        own measure: blocks of the window with std <= 4)."""
        import cv2
        lum = self.rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        mean = cv2.blur(lum, (FLAT_BLOCK, FLAT_BLOCK))
        var = cv2.blur(lum * lum, (FLAT_BLOCK, FLAT_BLOCK)) - mean * mean
        return np.sqrt(np.maximum(var, 0.0)) <= FLAT_STD

    def total(self, name: str, box: Box) -> float:
        """The sum of a map over `box` (picture px), bilinear at the box's edges."""
        t = self.table(name)
        h, w = t.shape[0] - 1, t.shape[1] - 1
        x0, y0, x1, y1 = (v * self.scale for v in box)
        x0, x1 = min(max(x0, 0.0), w), min(max(x1, 0.0), w)
        y0, y1 = min(max(y0, 0.0), h), min(max(y1, 0.0), h)

        def at(x: float, y: float) -> float:
            xi, yi = min(int(x), w - 1), min(int(y), h - 1)
            fx, fy = x - xi, y - yi
            return float(t[yi, xi] * (1 - fx) * (1 - fy) + t[yi, xi + 1] * fx * (1 - fy)
                         + t[yi + 1, xi] * (1 - fx) * fy + t[yi + 1, xi + 1] * fx * fy)
        return max(0.0, at(x1, y1) - at(x0, y1) - at(x1, y0) + at(x0, y0))

    def share(self, name: str, box: Box) -> float:
        """The mean of a map over `box` (a share, for the boolean maps)."""
        area = max(1e-9, (box[2] - box[0]) * (box[3] - box[1]) * self.scale * self.scale)
        return min(1.0, self.total(name, box) / area)


def frame_bands(lum: np.ndarray, video: bool = False) -> tuple[np.ndarray, dict[str, int]]:
    """The source's FRAME as bands along the picture's sides (second review of cycle 5, B5 / B6), judged by shape AND
    by content: per side, the run of lines from the edge that are each FRAME_LINE dark (luma FRAME_DARK or under) over
    the side's length between its FRAME_ENDS (a plate's foot border broken by its caption and inset map, a scan's
    dark surround, a slide mount's edge), ending in a HARD CUT: the dark share drops by FRAME_STEP within two lines
    and the picture after it is crisp or even (its content stops at the cut over its length; a limb's haze, a
    spacecraft or a specimen running into the black is no cut). A band claims at most FRAME_BAND_MAX of the
    picture's depth, or FRAME_PAIR_MAX when the opposite side carries a band of the same depth (a letterbox, a
    pillarbox); bands that are not such a pair claim at most FRAME_SHARE_MAX of the picture together, and a lone one
    at most FRAME_LONE_MAX. Anything larger or softer is SCENE (a night sky over
    a straight horizon, an object on a shelf against a black ground, a black table): no window moves for it.
    Returns (mask, {side: depth in lines})."""
    k = 2.0 if video else 1.0
    dark = lum <= FRAME_DARK * k
    h, w = lum.shape
    views = {"top": (dark, lambda m: m), "bottom": (dark[::-1], lambda m: m[::-1]),
             "left": (dark.T, lambda m: m.T), "right": (dark.T[::-1], lambda m: m[::-1].T)}
    depth: dict[str, int] = {}
    even: set[str] = set()
    for side, (arr, _back) in views.items():
        n = arr.shape[1]
        lo, hi = int(FRAME_ENDS * n), max(int(FRAME_ENDS * n) + 1, int(np.ceil((1 - FRAME_ENDS) * n)))
        part = arr[:, lo:hi]
        frac = part.mean(axis=1)
        # a line of a caption or subtitle set in the band is still the band (third review, B8: two subtitle lines in
        # a letterbox's bar hid the bar): a line FRAME_TEXT_DARK dark that alternates dark and bright at least
        # FRAME_TEXT_EDGES of its length (glyphs) counts; a soft gradient (a limb's haze) alternates rarely
        edges = (part[:, 1:] != part[:, :-1]).mean(axis=1) if part.shape[1] > 1 else np.zeros(len(frac))
        banded = (frac >= FRAME_LINE) | ((frac >= FRAME_TEXT_DARK) & (edges >= FRAME_TEXT_EDGES))
        d = 0
        while d < len(frac) and banded[d]:
            d += 1
        if d == 0 or d + 3 >= len(frac):
            continue
        # a HARD cut: within two lines the dark share drops by FRAME_STEP, and what follows is the picture, crisp
        # (at most FRAME_CRISP dark) or even (no gradient: two lines on, within FRAME_FLAT): a planet's limb fades
        # into its black over several lines, a plate's border stops at its picture in one
        if frac[d - 1] - frac[d + 1] < FRAME_STEP:
            continue
        crisp = float(frac[d + 1:d + 3].max()) <= FRAME_CRISP
        if not (crisp or abs(float(frac[d + 1] - frac[d + 3])) <= FRAME_FLAT):
            continue
        if not crisp:
            even.add(side)           # dark picture beyond the cut: a frame only as part of a surround (below)
        if frac[d] >= FRAME_TRANSITION:
            d += 1                   # the transition line, part of each, goes with the band
        depth[side] = d
    opposite = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
    keep: dict[str, int] = {}
    loose: dict[str, int] = {}
    for side, d in depth.items():
        n = h if side in ("top", "bottom") else w
        o = depth.get(opposite[side])
        if o is not None and abs(o - d) <= max(2, 0.02 * n) and max(d, o) <= FRAME_PAIR_MAX * n:
            keep[side] = d
        elif d <= FRAME_BAND_MAX * n:
            loose[side] = d
    if loose and sum(d / (h if s in ("top", "bottom") else w) for s, d in loose.items()) > FRAME_SHARE_MAX:
        loose = {}
    # a LONE band deeper than FRAME_LONE_MAX (no band on any other side) is a straight-edged dark ground, not a
    # surround: a book on a black cloth, a frieze under a band of shadow, a black table (second review, B5)
    if len(loose) == 1 and not keep:
        (side, d), = loose.items()
        if d > FRAME_LONE_MAX * (h if side in ("top", "bottom") else w):
            loose = {}
    keep.update(loose)
    if len(keep) == 1 and set(keep) <= even:
        keep = {}                    # a lone band with dark picture running on past its cut: a wedge, a shadow
    mask = np.zeros(lum.shape, bool)
    for side, d in keep.items():
        part = np.zeros(views[side][0].shape, bool)
        part[:d] = True
        mask |= views[side][1](part)
    return mask, keep


def _scaled_bands(sides: dict[str, int], fine: tuple[int, int], shape: tuple[int, int]) -> np.ndarray:
    """The bands found at `fine` (h, w) px as a mask at `shape` (h, w): each depth scaled up to whole lines."""
    fh, fw = fine
    h, w = shape
    mask = np.zeros(shape, bool)
    for side, d in sides.items():
        if side == "top":
            mask[:int(np.ceil(d * h / fh))] = True
        elif side == "bottom":
            mask[h - int(np.ceil(d * h / fh)):] = True
        elif side == "left":
            mask[:, :int(np.ceil(d * w / fw))] = True
        else:
            mask[:, w - int(np.ceil(d * w / fw)):] = True
    return mask


def _mount_corner(comp: np.ndarray) -> bool:
    """Whether one edge-touching region of digital black lies alone in a corner of the picture, on both of its edges,
    within FRAME_CORNER of each side: a slide mount's rounded corner."""
    h, w = comp.shape
    ch, cw = max(1, int(round(FRAME_CORNER * h))), max(1, int(round(FRAME_CORNER * w)))
    ys, xs = np.nonzero(comp)
    return bool(ys.size and (xs.max() < cw or xs.min() >= w - cw) and (ys.max() < ch or ys.min() >= h - ch)
                and (xs.min() == 0 or xs.max() == w - 1) and (ys.min() == 0 or ys.max() == h - 1))


def matte_mask(lum: np.ndarray, video: bool = False, fine: np.ndarray | None = None) -> np.ndarray:
    """The frame pixels of a luma picture (0-255): its frame bands (frame_bands, read on `fine`, the same picture at a
    higher resolution, where a mount's or a scan's thin edge is several lines deep), and every region of digital
    black (MATTE_LUMA or under, even, MATTE_MEAN or under as a whole) that is a mount's corner (_mount_corner), in
    two corners or more.
    `video`: an encoded frame, whose black carries noise (twice the levels)."""
    import cv2
    k = 2.0 if video else 1.0
    if fine is not None and fine.shape[0] > lum.shape[0]:
        _, sides = frame_bands(fine, video)
        mask = _scaled_bands(sides, fine.shape, lum.shape)
    else:
        mask, _ = frame_bands(lum, video)
    n, lab, st, _ = cv2.connectedComponentsWithStats((lum <= MATTE_LUMA * k).astype(np.uint8), connectivity=8)
    corners: dict[tuple[bool, bool], np.ndarray] = {}
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in st[i])
        if area < FRAME_CORNER_MIN or not (x == 0 or y == 0 or x + bw == lum.shape[1] or y + bh == lum.shape[0]):
            continue
        comp = lab == i
        if (float(lum[comp].std()) <= MATTE_STD * k and float(lum[comp].mean()) <= MATTE_MEAN * k
                and _mount_corner(comp)):
            corners[(x == 0, y == 0)] = comp
    if len(corners) >= 2:    # a mount rounds its corners alike; one dark corner alone is the scene's shadow
        for comp in corners.values():
            mask |= comp
    return mask


def largest_rectangle(ok: np.ndarray) -> tuple[int, int, int, int]:
    """The largest axis-aligned rectangle of True cells (x0, y0, x1, y1, end exclusive); the whole array when all of
    it is True, (0, 0, 0, 0) when none is."""
    h, w = ok.shape
    if ok.all():
        return 0, 0, w, h
    heights = np.zeros(w, np.int32)
    best, box = 0, (0, 0, 0, 0)
    for y in range(h):
        heights = np.where(ok[y], heights + 1, 0)
        stack: list[int] = []
        for x in range(w + 1):
            cur = int(heights[x]) if x < w else 0
            while stack and int(heights[stack[-1]]) >= cur:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                area = int(heights[top]) * (x - left)
                if area > best:
                    best, box = area, (left, y - int(heights[top]) + 1, x, y + 1)
            stack.append(x)
    return box


def _salience(rgb: np.ndarray, detail: np.ndarray, matte: np.ndarray) -> np.ndarray:
    """Where the picture differs from itself, summing to 1 over the content: its local detail (what is drawn finely)
    and its distance in Lab from its own dominant colour (what stands out from the ground), each over its own mean, in
    equal parts, smoothed over ANALYSIS / 32 px so a subject is a region, not its outline."""
    import cv2
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    keep = ~matte
    if not keep.any():
        return np.zeros(detail.shape, np.float32)
    ground = np.median(lab[keep], axis=0)
    rare = np.sqrt(((lab - ground) ** 2).sum(axis=2))
    sigma = max(1.5, ANALYSIS / 32)
    out = np.zeros(detail.shape, np.float32)
    for part in (detail, rare):
        part = np.where(keep, part, 0.0).astype(np.float32)
        mean = float(part[keep].mean())
        if mean > 1e-6:
            out += cv2.GaussianBlur(part / mean, (0, 0), sigma)
    out[matte] = 0.0
    s = float(out.sum())
    return out / s if s > 0 else out


def analyse(clean: Image.Image, focus: Box | None = None, video: bool = False) -> Analysis:
    """The maps of a CLEAN picture (hero_window.clean's result, or a video frame)."""
    w, h = clean.size
    scale = min(1.0, ANALYSIS / max(w, h))
    aw, ah = max(1, round(w * scale)), max(1, round(h * scale))
    rgb = np.asarray(clean.convert("RGB").resize((aw, ah), Image.Resampling.BOX), np.uint8)
    thumb = clean.convert("RGB")
    thumb.thumbnail((THUMB, THUMB), Image.Resampling.BILINEAR)
    thumb.info = {}   # no EXIF or ICC blob kept with the analysis
    return analyse_maps(rgb, (w, h), focus, video, thumb)


def analyse_maps(rgb: np.ndarray, size: tuple[int, int], focus: Box | None = None, video: bool = False,
                 thumb: Image.Image | None = None) -> Analysis:
    """The maps of a picture of `size` px from its ANALYSIS-px RGB (analyse's, or a kept analysis' `rgb`)."""
    import cv2
    w, h = size
    ah, aw = rgb.shape[:2]
    lum = rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    fine = np.asarray(thumb.convert("L"), np.float32) if thumb is not None else None
    matte = matte_mask(lum, video, fine)
    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0, ksize=3) / 4.0
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1, ksize=3) / 4.0
    grad = np.hypot(gx, gy)
    if matte.any():   # the frame's own edge is not the picture's detail
        grad[cv2.dilate(matte.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0] = 0.0
    detail = cv2.blur(grad, (9, 9))
    box = largest_rectangle(~matte)
    sx, sy = w / aw, h / ah
    content = (int(np.ceil(box[0] * sx)), int(np.ceil(box[1] * sy)), int(np.floor(box[2] * sx)),
               int(np.floor(box[3] * sy)))
    return Analysis((w, h), aw / w, matte, detail, _salience(rgb, detail, matte), rgb, content,
                    float(matte.mean()), focus, thumb)


# ---- reading a photograph: its clean picture and its analysis, kept per file (path, size, mtime)
_cache: dict[tuple, tuple[tuple[int, int, int, int], Analysis]] = {}
_cache_lock = threading.Lock()
CACHE_MAX = 1024


def _key(path: Path) -> tuple | None:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return (str(Path(path).resolve()), st.st_size, st.st_mtime_ns)


def load(path: Path) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """(the photograph's clean picture, RGB; the clean region's box in the whole EXIF-turned file)."""
    from .photo_hygiene import without_caption_strip
    with Image.open(path) as im:
        whole = ImageOps.exif_transpose(im).convert("RGB")
    clean, box = without_caption_strip(whole)
    return clean, box


def analysis_of(path: Path, clean: Image.Image | None = None) -> Analysis:
    """The analysis of the photograph at `path` (cached per file), with its reviewed subject where it has one."""
    key = _key(Path(path))
    with _cache_lock:
        hit = _cache.get(key) if key else None
    if hit is not None:
        return hit[1]
    kept = None
    if clean is None:
        clean, kept = load(Path(path))
    a = analyse(clean, _reviewed(Path(path), clean, kept))
    if key:
        with _cache_lock:
            if len(_cache) >= CACHE_MAX:
                _cache.pop(next(iter(_cache)))
            _cache[key] = (kept or (0, 0) + clean.size, a)
    return a


def _reviewed(path: Path, clean: Image.Image, kept) -> Box | None:
    """The subject of the photo at `path` as a box on its clean picture (None: none): its REVIEWED box, else a derived
    box at or over photo_subject.ACCEPT (design-freeze cycle 6; under it, none: the measures as without a subject)."""
    try:
        from .photo_subject import subject_focus
        found = subject_focus(path)
    except Exception:                                   # no art-direction file, no record: no subject
        return None
    if not found:
        return None
    try:
        with Image.open(path) as im:
            w, h = ImageOps.exif_transpose(im).size
    except OSError:
        return None
    x, y = (kept[0], kept[1]) if kept else (0, 0)
    fx0, fy0, fx1, fy1 = found["focus_box"]
    box = (fx0 * w - x, fy0 * h - y, fx1 * w - x, fy1 * h - y)
    cw, ch = clean.size
    box = (max(0.0, box[0]), max(0.0, box[1]), min(float(cw), box[2]), min(float(ch), box[3]))
    return box if box[2] > box[0] and box[3] > box[1] else None


# ---- one crop, measured
def fit_box(src: tuple[int, int], out: tuple[int, int], centering: Sequence[float] = (0.5, 0.5),
            zoom: float = 1.0) -> Box:
    """The box ImageOps.fit crops from a `src`-sized picture for `out` at `centering`, `zoom` times closer."""
    sw, sh = src
    ow, oh = out
    if sw / sh > ow / oh:
        cw, ch = sh * ow / oh, float(sh)
    else:
        cw, ch = float(sw), sw * oh / ow
    cw, ch = cw / zoom, ch / zoom
    x0 = (sw - cw) * centering[0]
    y0 = (sh - ch) * centering[1]
    return (x0, y0, x0 + cw, y0 + ch)


def _sub(box: Box, out: tuple[int, int], part: Box) -> Box:
    """The picture-px box of `part` (output px) when `box` (picture px) is drawn at `out`."""
    kx, ky = (box[2] - box[0]) / out[0], (box[3] - box[1]) / out[1]
    return (box[0] + part[0] * kx, box[1] + part[1] * ky, box[0] + part[2] * kx, box[1] + part[3] * ky)


def _clip(part: Box, out: tuple[int, int]) -> Box:
    """`part` (output px) inside the output."""
    return (max(0.0, part[0]), max(0.0, part[1]), min(float(out[0]), part[2]), min(float(out[1]), part[3]))


def _overlap(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    area = (a[2] - a[0]) * (a[3] - a[1])
    return max(0.0, w) * max(0.0, h) / area if area > 0 and w > 0 and h > 0 else 0.0


def protected_measure(a: Analysis, pb: Box) -> dict[str, Any]:
    """What lies under type set on the box `pb` (picture px): "foreign" (hero_window.foreign_object: a saturated,
    straight-edged shape), "line" (the longest straight strong edge, as a share of the box's width: a wing's, an
    array's, a mast's) and "busy" (the share of strong edges, recorded)."""
    rec: dict[str, Any] = {"busy": round(a.share("edge", pb), 3), "foreign": False, "line": 0.0, "step": 0.0}
    if a.thumb is None:
        return rec
    import cv2

    from .hero_window import foreign_object
    k = a.thumb.width / a.size[0]
    part = a.thumb.crop(tuple(int(round(v * k)) for v in pb))
    if part.width < 8 or part.height < 4:
        return rec
    rec["foreign"] = bool(foreign_object(part))
    g = np.asarray(part.convert("L"), np.uint8)
    # "step": the box split between two tonal masses (Otsu's two classes, each at least STEP_SHARE of it): the luma
    # between their means. A wing's white skin over dark ground, a roofline against the sky, a horizon.
    cut, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    hi = g > cut
    share = float(hi.mean())
    rec["step"] = (round(float(g[hi].mean() - g[~hi].mean()), 1) if STEP_SHARE <= share <= 1 - STEP_SHARE else 0.0)
    edges = cv2.Canny(g, LINE_CANNY[0], LINE_CANNY[1])
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=20, minLineLength=max(8, int(0.2 * g.shape[1])),
                            maxLineGap=3)
    if lines is not None:
        rec["line"] = round(float(max(np.hypot(x1 - x0, y1 - y0) for x0, y0, x1, y1 in lines[:, 0])) / g.shape[1], 3)
    return rec


def measure(a: Analysis, box: Box, window: Window, best_view: float | None = None) -> dict[str, Any]:
    """The measures of the crop `box` (picture px) of an analysed photograph in `window`. `best_view`: the most
    salience any crop of this size holds in its view (for "keep"; None leaves it out)."""
    out = window.size
    view = _sub(box, out, window.view_box())
    cb = a.content_box
    content = max(1.0, float((cb[2] - cb[0]) * (cb[3] - cb[1])))
    share = max(1e-9, (view[2] - view[0]) * (view[3] - view[1]) / content)
    subject = a.total("salience", view)
    rec: dict[str, Any] = {
        "box": [round(v, 1) for v in box],
        "upscale": round(out[0] / max(1e-6, box[2] - box[0]), 3),
        "matte": round(a.share("matte", box), 4),
        "flat": round(a.share("flat", view), 3),
        "subject": round(subject, 4),
        "density": round(subject / share, 3),
    }
    rec["extreme"] = round(a.share("extreme", view), 3)
    if best_view is not None:
        rec["keep"] = round(min(1.0, subject / best_view), 3) if best_view > 1e-9 else 1.0
    if a.focus is not None:
        rec["focus"] = round(_overlap(a.focus, view), 3)
        if window.hold is not None:
            rec["held"] = round(_overlap(a.focus, _sub(box, out, window.hold)), 3)
    elif window.shown_min > 0:
        open_part = _sub(box, out, _clip(window.hold, out)) if window.hold is not None else view
        rec["shown"] = round(a.total("salience", open_part), 3)
        if window.tile is not None:
            rec["shown_tile"] = round(a.total("salience", _sub(box, out, _clip(window.tile, out))), 3)
        if window.centre_half:
            W, H = a.size
            rec["centre_half"] = bool(box[0] <= 0.25 * W + 1 and box[2] >= 0.75 * W - 1
                                      and box[1] <= 0.25 * H + 1 and box[3] >= 0.75 * H - 1)
    prot = [protected_measure(a, _sub(box, out, p)) for p in window.protected]
    if prot:
        rec["protected"] = prot
        if a.focus is not None:   # the share of the subject that lies under the type or a mark
            rec["on_subject"] = round(min(1.0, sum(_overlap(a.focus, _sub(box, out, p)) for p in window.protected)), 3)
    return rec


def problems(rec: dict[str, Any], window: Window) -> list[str]:
    """Why a measured crop is unfit for `window` (the measures' names; [] when it is fit)."""
    out = []
    if rec["matte"] > MATTE_MAX:
        out.append("matte")
    if rec["upscale"] > window.upscale_max + 1e-6:
        out.append("upscale")
    if "focus" in rec:
        need = rec.get("focus_need", window.focus_min)
        held_need = rec.get("held_need", FOCUS_KEEP)
        if rec["focus"] < need - 1e-3 or rec.get("held", 1.0) < held_need - 1e-3:
            out.append("subject")
    elif (rec.get("keep", 1.0) < SUBJECT_KEEP or rec["density"] < DENSITY_MIN
          or rec.get("shown", 1.0) < window.shown_min
          or rec.get("shown_tile", 1.0) < TILE_SHARE * rec.get("shown", 0.0) or rec.get("centre_half") is False):
        out.append("subject")
    if rec["flat"] > FLAT_MAX and "focus" not in rec:   # a reviewed subject in view is not an empty view
        out.append("flat")
    if rec.get("extreme", 0.0) > TONE_MAX:
        out.append("tone")
    for p in rec.get("protected", ()):
        if p.get("foreign") or p.get("line", 0.0) >= LINE_MAX or p.get("step", 0.0) >= STEP_MAX:
            out.append("protected")
            break
    return [p for p in out if p in window.checks]


SOFT = ("subject", "flat", "tone")   # read from salience and tone: recorded, never alone the cause of a move


REASONS = {
    "tone": "the view is mostly a near-white or near-black field",
    "matte": "the crop shows the photograph's own frame (a black surround, wedge, border or letterbox)",
    "upscale": "the crop would be enlarged past the limit",
    "subject": "the crop does not keep the photograph's subject in view",
    "flat": "the view is flat ground with no detail",
    "protected": "a foreign or busy structure lies under the type",
}


def reason(rec: dict[str, Any], surface: str = "") -> str:
    """A recorded refusal's or omission's reason for an unfit placement (names the rule and the surface)."""
    why = "; ".join(REASONS[p] for p in rec.get("problems", ())) or "no fit crop"
    return (f"{surface or rec.get('window') or 'the window'}: {why} (rule: a photograph is fit for the window it "
            f"is put in, src/photo_fitness.py)")


def _region(a: Analysis) -> tuple[int, int, int, int]:
    """Where remedy crops are cut from: the content box, or the whole picture when it has no frame."""
    x0, y0, x1, y1 = a.content_box
    return a.content_box if x1 - x0 >= 8 and y1 - y0 >= 8 else (0, 0) + tuple(a.size)


def _candidates(a: Analysis, window: Window, zoom: float) -> list[Box]:
    """The crops of `window`'s shape inside the content box, `zoom` times closer than the largest one, on a GRID."""
    rx0, ry0, rx1, ry1 = _region(a)
    base = fit_box((rx1 - rx0, ry1 - ry0), window.size, (0.5, 0.5), zoom)
    cw, ch = base[2] - base[0], base[3] - base[1]
    fx, fy = (rx1 - rx0) - cw, (ry1 - ry0) - ch
    xs = [0.5] if fx < 1 else [i / (GRID - 1) for i in range(GRID)]
    ys = [0.5] if fy < 1 else [i / (GRID - 1) for i in range(GRID)]
    return [(rx0 + fx * px, ry0 + fy * py, rx0 + fx * px + cw, ry0 + fy * py + ch) for py in ys for px in xs]


def place(photo: Path | None, window: Window, centering: Sequence[float] = (0.5, 0.5),
          earlier: Sequence[Box] = (), overlap_max: float | None = None, *, image: Image.Image | None = None,
          analysis: Analysis | None = None, fixed: bool = False) -> dict[str, Any]:
    """The crop of a photograph for `window` (_place). Without a subject box it is _place's, unchanged. With one
    (cycle 6, SUBJECT stream, rounds 2-3) the subject-safe crop is drawn only where it shows no less of the subject,
    in the view and in the hold, than BOTH bases: the crop the photograph gets without the box ("today") and the
    crop the cycle-5 rules give with the same box ("cycle 5"), and brings no problem neither had. Otherwise the base
    that shows more of the subject is drawn (the cycle-5 crop when neither dominates), with its own record. A crop
    that is the photograph's crop without the box keeps that record's fitness ("subject_partial"): a box never turns
    a drawn crop unfit by itself, so no picker swaps a photograph for it."""
    if analysis is None:
        analysis = analysis_of(Path(photo)) if image is None else analyse(image)
    a = analysis
    if a.focus is None:
        return _place(photo, window, centering, earlier, overlap_max, image=image, analysis=a, fixed=fixed)
    import dataclasses
    blind = dataclasses.replace(a, focus=None, _sat=a._sat)
    today = _place(photo, window, centering, earlier, overlap_max, image=image, analysis=blind, fixed=fixed)
    c5 = _place(photo, window, centering, earlier, overlap_max, image=image, analysis=a, fixed=fixed, legacy=True)
    rec = _place(photo, window, centering, earlier, overlap_max, image=image, analysis=a, fixed=fixed,
                 floor=tuple(today["box"]))

    def shown(r: dict[str, Any]) -> tuple[float, float]:
        m = measure(a, tuple(r["box"]), window)
        return m.get("focus", 1.0), m.get("held", 1.0)

    def at_least(x: tuple[float, float], y: tuple[float, float]) -> bool:
        return x[0] >= y[0] - 1e-3 and x[1] >= y[1] - 1e-3

    v_today, v_c5, v_rec = shown(today), shown(c5), shown(rec)
    added = set(rec["problems"]) - set(today["problems"]) - set(c5["problems"]) - {"subject"}
    if not (at_least(v_rec, v_today) and at_least(v_rec, v_c5) and not added):
        if at_least(v_today, v_c5) and not at_least(v_c5, v_today):
            t = measure(a, tuple(today["box"]), window)
            t.update({k: rec[k] for k in ("focus_need", "held_need", "hold_short") if k in rec})
            t["problems"] = problems(t, window)
            rec = {**today, **t, "window": window.name, "basis": "subject floor (the crop without the box)"}
        else:
            rec = {**c5, "basis": "subject floor (the cycle-5 crop with the box)"}
    same = all(abs(float(x) - float(y)) < 1.0 for x, y in zip(rec["box"], today["box"]))
    if (same and today.get("fit") and "subject" in rec["problems"]
            and not set(rec["problems"]) - {"subject"} - set(today["problems"])):
        # the crop drawn without the box: the box does not turn it unfit (recorded, not refused)
        rec = {**rec, "problems": [p for p in rec["problems"] if p != "subject"], "subject_partial": True}
    rec["fit"] = not rec["problems"]
    return rec


def _place(photo: Path | None, window: Window, centering: Sequence[float] = (0.5, 0.5),
           earlier: Sequence[Box] = (), overlap_max: float | None = None, *, image: Image.Image | None = None,
           analysis: Analysis | None = None, fixed: bool = False, floor: Box | None = None,
           legacy: bool = False) -> dict[str, Any]:
    """The crop of a photograph for `window`, and what was measured. The crop the surface would take (the plain fit
    at `centering`) is kept when it is fit, or unfit on SOFT measures only (recorded); otherwise the fit crop that
    changes least, cut from the content box:
    the largest crops first (every position), then closer ones step by step (to window.zoom_max and
    window.upscale_max); at each step the one with the most subject in view, then the nearest the asked position.
    `earlier` (picture px boxes this deck already shows of the photograph) with `overlap_max`: a crop overlapping one
    of them by more than that is taken only when no step has a fit crop that does not (the deck window rule).
    `fixed`: measure the asked crop only (no search).
    Returns {"box" (clean picture px), "fit": bool, "problems": [...], "moved": bool, "zoom", the measures, "base":
    the asked crop's measures when another was taken, "window": the window's name}."""
    if analysis is None:   # a photograph on file is analysed once (analysis_of's cache); a bare image each time
        analysis = analysis_of(Path(photo)) if image is None else analyse(image)
    a = analysis
    view = window.view_box()
    asked = fit_box(a.size, window.size, centering)

    def held(b: Box) -> float:
        return a.total("salience", _sub(b, window.size, view))

    needs = {} if legacy else _subject_needs(a, window, asked)   # legacy: the cycle-5 rules with a box

    def judged(b: Box, zoom: float, best: float, was: dict[str, Any] | None = None) -> dict[str, Any]:
        rec = measure(a, b, window, best)
        rec.update(needs)
        rec["problems"] = problems(rec, window)
        if was is not None and "protected" in window.checks and "protected" not in rec["problems"] and any(
                p["busy"] > max(BUSY_MOVE_MAX, q["busy"]) + 1e-9
                for p, q in zip(rec.get("protected", ()), was.get("protected", ()))):
            rec["problems"].append("protected")   # a move does not trade its cause for a busier ground under the type
        if earlier:
            rec["overlap"] = round(max(_overlap(b, e) for e in earlier), 3)
        rec["zoom"] = zoom
        return rec

    def crowded(r: dict[str, Any]) -> bool:
        return overlap_max is not None and r.get("overlap", 0.0) > overlap_max + 1e-6

    # Without a REVIEWED subject box no measure knows where the subject is, so a move keeps the asked window's centre
    # (MOVE_HOLD of it on each axis) and MOVE_KEEP of the salience its view held: a window moved off black space onto
    # a spacecraft left the crater it was asked for out of the picture, recorded fit because the spacecraft is salient
    # (review of cycle 5, B1). With a reviewed box the box itself is held (FOCUS_KEEP).
    cx, cy = (asked[0] + asked[2]) / 2, (asked[1] + asked[3]) / 2
    hw, hh = (asked[2] - asked[0]) * MOVE_HOLD / 2, (asked[3] - asked[1]) * MOVE_HOLD / 2
    rx0, ry0, rx1, ry1 = _region(a)   # the frame is not picture: the centre held is the asked centre's picture part
    centre = (max(cx - hw, rx0), max(cy - hh, ry0), min(cx + hw, rx1), min(cy + hh, ry1))
    asked_view = held(asked)

    def holds(b: Box) -> bool:
        return a.focus is not None or (b[0] <= centre[0] + 1e-6 and b[1] <= centre[1] + 1e-6
                                       and b[2] >= centre[2] - 1e-6 and b[3] >= centre[3] - 1e-6)

    def kept(r: dict[str, Any]) -> dict[str, Any]:
        """A moved crop of an unreviewed photograph: its view's salience against the asked view's ("kept_asked");
        under MOVE_KEEP the move lost what was asked for ("subject")."""
        if a.focus is None:
            r["kept_asked"] = round(min(9.99, r["subject"] / asked_view), 3) if asked_view > 1e-9 else 1.0
            if r["kept_asked"] < MOVE_KEEP and "subject" not in r["problems"] and "subject" in window.checks:
                r["problems"].append("subject")
        return r

    def keeps(r: dict[str, Any]) -> bool:
        """Whether a moved crop keeps what was asked for, on EVERY window, whatever its checks (second review, B5:
        a frame-only ground, typography.cover_crop's, moved to the land band and lost the disc in the sky). With a
        subject: it shows no less of it than the asked crop, in the view and in the hold (cycle 6, round 2)."""
        if a.focus is not None:
            return legacy or (r.get("focus", 1.0) >= floor_focus[0] - 1e-6
                              and r.get("held", 1.0) >= floor_focus[1] - 1e-6)
        return r.get("kept_asked", 1.0) >= MOVE_KEEP

    every = _candidates(a, window, 1.0)
    best1 = max([held(asked), *(held(b) for b in every)])
    base = judged(asked, 1.0, best1)
    if a.focus is not None and not legacy:   # the floor: the crop without a subject (place()), else the asked crop
        ref = measure(a, floor, window) if floor is not None else base
        floor_focus = (min(base.get("focus", 1.0), ref.get("focus", 1.0)), min(base.get("held", 1.0), ref.get("held", 1.0)))
    # What moves a window is what is measured for certain: the source's frame in it, the enlargement, a structure
    # under the type, a REVIEWED subject out of view. What salience suggests of an unreviewed photograph (SOFT) is
    # recorded and decides between the crops once a move is due, and between photographs (the pickers: rank(), the
    # deck's photo guard); alone it moves nothing. Salience is no subject detector: a move made for it alone took
    # a Story frame's window to the aircraft's wing at the photograph's edge, the most salient thing in it.
    due = [p for p in base["problems"] if p not in SOFT or (p == "subject" and a.focus is not None)]
    if fixed or (not due and not crowded(base)):
        return {**base, "fit": not base["problems"], "moved": False, "window": window.name}
    if due == ["matte"] and a.focus is None and not earlier:
        return _minimal_move(a, window, asked, base, judged, kept, keeps)

    def near(r: dict[str, Any]) -> float:
        b = r["box"]
        return abs((b[0] + b[2]) - (asked[0] + asked[2])) + abs((b[1] + b[3]) - (asked[1] + asked[3]))

    found: dict[str, Any] | None = None       # the first fit crop (it may crowd an earlier showing)
    least: tuple[tuple, dict[str, Any]] | None = None
    for zoom in ZOOM_STEPS:
        if zoom > window.zoom_max + 1e-9:
            break
        every = _candidates(a, window, zoom) + ([] if legacy else _on_subject(a, window, zoom) + (
            [floor] if floor is not None and zoom == 1.0 else []))
        if window.size[0] / max(1e-6, every[0][2] - every[0][0]) > window.upscale_max + 1e-6:
            break
        boxes = [b for b in every if holds(b)]
        if not boxes:
            continue
        best = best1 if zoom == 1.0 else max(held(b) for b in every)
        recs = [r for r in (kept(judged(b, zoom, best, base)) for b in boxes) if keeps(r)]
        if not recs:
            continue
        ok = sorted((r for r in recs if not r["problems"]),
                    key=(lambda r: (crowded(r), round(r.get("overlap", 0.0), 2) if crowded(r) else 0.0,
                                    -round(r.get("focus", 0.0), 2), -round(r["subject"], 3), near(r))) if legacy else
                        (lambda r: (crowded(r), round(r.get("overlap", 0.0), 2) if crowded(r) else 0.0,
                                    -round(r.get("focus", 0.0), 2), -round(r.get("held", 0.0), 2),
                                    round(r.get("on_subject", 0.0), 2), -round(r["subject"], 3), near(r))))
        if ok:
            if found is None:
                found = ok[0]
            if not crowded(ok[0]):
                found = ok[0]
                break
        for r in recs:   # no fit crop: the crop with only some of the asked crop's problems, the fewest first
            if not set(r["problems"]) < set(base["problems"]):
                continue
            k = (len(r["problems"]), "matte" in r["problems"], zoom, -round(r["subject"], 3), near(r))
            if least is None or k < least[0]:
                least = (k, r)
    asked_rec = {k: v for k, v in base.items() if k != "zoom"}
    # what a moved crop's "fit" rests on: a reviewed box held whole, or (no subject box) the asked centre held and
    # MOVE_KEEP of the asked view's salience kept; never the salience ranking alone
    basis = ("reviewed subject" if legacy else "subject") if a.focus is not None else "asked centre held"
    if found is not None:
        return {**found, "fit": True, "moved": True, "window": window.name, "base": asked_rec, "basis": basis}
    # no fit crop: a moved crop is taken only when its problems are some of the asked crop's, never another one (a
    # crop that clears the frame and loses the subject trades the lesser defect for the greater: review B1)
    if least is not None:
        return {**least[1], "fit": False, "moved": True, "window": window.name, "base": asked_rec, "basis": basis}
    return {**base, "fit": not base["problems"], "moved": False, "window": window.name}


def _on_subject(a: Analysis, window: Window, zoom: float) -> list[Box]:
    """The crop of `window`'s shape at `zoom` centred on the subject (and on its hold, when the window has one),
    inside the content region: the position the GRID may step over (empty without a subject)."""
    if a.focus is None:
        return []
    rx0, ry0, rx1, ry1 = _region(a)
    base = fit_box((rx1 - rx0, ry1 - ry0), window.size, (0.5, 0.5), zoom)
    cw, ch = base[2] - base[0], base[3] - base[1]
    fx, fy = (a.focus[0] + a.focus[2]) / 2, (a.focus[1] + a.focus[3]) / 2
    out = []
    for part in [window.view_box()] + ([_clip(window.hold, window.size)] if window.hold is not None else []):
        # the output-px centre of the part, as a share of the crop
        px, py = (part[0] + part[2]) / 2 / window.size[0], (part[1] + part[3]) / 2 / window.size[1]
        x0 = min(max(fx - px * cw, rx0), rx1 - cw)
        y0 = min(max(fy - py * ch, ry0), ry1 - ch)
        out.append((x0, y0, x0 + cw, y0 + ch))
    return out


def _subject_needs(a: Analysis, window: Window, asked: Box) -> dict[str, Any]:
    """What of the subject a crop for `window` is asked to show (cycle 6, round 3). A window with `hold_min` (the
    Reel cover) asks for window.focus_min in its view (the profile tile) and hold_min in its hold, never less: the
    cover stands as without the box otherwise. Any other window asks for all of it (window.focus_min in the view,
    FOCUS_KEEP in the hold), or, when no frame-free crop of its shape on the whole zoom ladder it may use (to
    zoom_max and upscale_max) can show that much, as much as the best of them shows; a window that cannot show the
    subject at all keeps the whole ask (no subject makes any crop fit). {} without a subject."""
    if a.focus is None:
        return {}
    size, view = window.size, window.view_box()
    hold = _clip(window.hold, size) if window.hold is not None else None
    boxes = [asked]
    for zoom in ZOOM_STEPS:
        if zoom > window.zoom_max + 1e-9:
            break
        every = _candidates(a, window, zoom)
        if size[0] / max(1e-6, every[0][2] - every[0][0]) > window.upscale_max + 1e-6:
            break
        boxes += every + _on_subject(a, window, zoom)
    clean = [b for b in boxes if a.share("matte", b) <= MATTE_MAX] or boxes
    best_view = max(_overlap(a.focus, _sub(b, size, view)) for b in clean)
    best_held = max(_overlap(a.focus, _sub(b, size, hold)) for b in clean) if hold is not None else 1.0
    if window.hold_min > 0:
        out: dict[str, Any] = {"focus_need": window.focus_min}
        if hold is not None:
            out["held_need"] = round(window.hold_min, 3)
            out["hold_short"] = bool(best_held < window.hold_min)
        return out
    out = {"focus_need": round(min(window.focus_min, best_view), 3) if best_view > 1e-3 else window.focus_min}
    if hold is not None:
        out["held_need"] = round(min(FOCUS_KEEP, best_held), 3) if best_held > 1e-3 else FOCUS_KEEP
    return out


def _clearing_position(a: Analysis, cx: float, cy: float, bw: float, bh: float) -> Box | None:
    """The crop of bw x bh picture px nearest to the centre (cx, cy) that shows none of the frame, measured on the
    frame mask itself (each side trimmed only as far as its own frame and a mount's corner need), or
    None. Positions are whole analysis px; the nearest by |dx| + |dy|."""
    t = a.table("matte")
    ah, aw = t.shape[0] - 1, t.shape[1] - 1
    k = a.scale
    bwa, bha = max(1, int(round(bw * k))), max(1, int(round(bh * k)))
    if bwa > aw or bha > ah:
        return None
    x0 = int(round((cx - bw / 2) * k))
    y0 = int(round((cy - bh / 2) * k))
    xs = np.arange(0, aw - bwa + 1)
    ys = np.arange(0, ah - bha + 1)
    X, Y = np.meshgrid(xs, ys)
    share = (t[Y + bha, X + bwa] - t[Y, X + bwa] - t[Y + bha, X] + t[Y, X]) / float(bwa * bha)
    ok = share <= 1e-9
    if not ok.any():
        return None
    dist = np.abs(X - x0) + np.abs(Y - y0)
    dist = np.where(ok, dist, np.iinfo(np.int64).max)
    j = np.unravel_index(int(np.argmin(dist)), dist.shape)
    dx, dy = (int(X[j]) - x0) / k, (int(Y[j]) - y0) / k
    W, H = a.size
    bx0 = min(max(cx - bw / 2 + dx, 0.0), W - bw)
    by0 = min(max(cy - bh / 2 + dy, 0.0), H - bh)
    return (bx0, by0, bx0 + bw, by0 + bh)


def _minimal_move(a: Analysis, window: Window, asked: Box, base: dict[str, Any], judged, kept, keeps
                  ) -> dict[str, Any]:
    """A move caused ONLY by the source's frame is the minimal one (third review, B7: the most salient clearing crop
    slid a Story window 230 px sideways off a 15 px scan edge and cut the Eye's ring): at the least zoom about the
    asked centre (steps of 0.005) that leaves room, the crop nearest the asked one that shows no frame, measured on
    the frame mask itself, so each side moves only as far as its own frame needs (a re-verification: trimming all
    four sides to the content box asked Amber 05's lantern slide, framed at its foot and right side only, for a
    closer crop than needed, and the move was refused). It is taken when it holds MOVE_KEEP of the asked view and
    adds no problem the asked crop did not have (but a structure under the type, which the frame had hidden: then it
    is drawn and recorded "protected"); otherwise the asked crop stays, recorded."""
    cx, cy = (asked[0] + asked[2]) / 2, (asked[1] + asked[3]) / 2
    w, h = asked[2] - asked[0], asked[3] - asked[1]
    asked_rec = {k: v for k, v in base.items() if k != "zoom"}
    z = 1.0
    while z <= window.zoom_max + 1e-9:
        bw, bh = w / z, h / z
        if window.size[0] / bw > window.upscale_max + 1e-6:
            break
        box = _clearing_position(a, cx, cy, bw, bh)
        if box is not None:
            best = max([a.total("salience", _sub(b, window.size, window.view_box()))
                        for b in [box, *_candidates(a, window, z)]])
            r = kept(judged(box, round(z, 3), best, base))
            # a structure under the type that the asked crop hid only behind the frame (a mount's black corner under
            # the masthead) is the picture's own: the minimal move may show it (recorded "protected"), as the base
            # did, rather than keep the frame in view
            added = set(r["problems"]) - set(base["problems"]) - {"protected"}
            if keeps(r) and "matte" not in r["problems"] and not added:
                return {**r, "fit": not r["problems"], "moved": True, "window": window.name, "base": asked_rec,
                        "basis": "minimal frame move"}
            if "matte" not in r["problems"]:
                break        # a closer crop would only keep less
        z += 0.005
    return {**base, "fit": False, "moved": False, "window": window.name}


def crop(image: Image.Image, box: Iterable[float], size: tuple[int, int]) -> Image.Image:
    """`box` (picture px) of the clean picture at `size`, one Lanczos resample."""
    x0, y0, x1, y1 = box
    return image.resize(size, Image.Resampling.LANCZOS, box=(float(x0), float(y0), float(x1), float(y1)))


def fill(photo: Path, window: Window, centering: Sequence[float] = (0.5, 0.5), **kw: Any
         ) -> tuple[Image.Image, dict[str, Any]]:
    """(the photograph cropped to `window` by place(), RGB; the placement record)."""
    image, _ = load(Path(photo))
    rec = place(photo, window, centering, image=image, analysis=analysis_of(Path(photo), image), **kw)
    return crop(image, rec["box"], window.size).convert("RGB"), rec


def rank(photos: Sequence[Path], window: Window, **kw: Any) -> list[tuple[Path, dict[str, Any]]]:
    """Every candidate photograph with its placement in `window`, the fit ones first (as they stood in `photos`),
    then the unfit by the fewest problems: the pickers take the first that their own rules also allow."""
    out = []
    for i, p in enumerate(photos):
        try:
            rec = place(Path(p), window, **kw)
        except (OSError, ValueError):
            continue
        out.append((i, Path(p), rec))
    out.sort(key=lambda t: (not t[2]["fit"], len(t[2]["problems"]) if not t[2]["fit"] else 0, t[0]))
    return [(p, rec) for _, p, rec in out]


def drawn_record(photo: Path, window: Window, rec: dict[str, Any], drew: dict[str, Any]) -> dict[str, Any]:
    """The placement record of the crop a surface DREW when it did not take `rec`'s (place()'s) crop: `drew` is what
    typography.cover_crop filled in ("box", "moved", "full_image"). The same box: `rec` as it is. Another box: that
    box measured in `window`, with `rec` kept as "measured" (the record is the drawn box: review of cycle 5, D1)."""
    if drew.get("full_image"):
        return {**{k: v for k, v in rec.items() if k != "box"}, "drawn": "full image", "box": None}
    box = drew.get("box")
    if box is None or all(abs(float(x) - float(y)) < 1.0 for x, y in zip(box, rec["box"])):
        return rec
    a = analysis_of(Path(photo))
    got = measure(a, tuple(float(v) for v in box), window)
    if a.focus is not None:   # the drawn crop is the one without a subject: as whole as it shows it (cycle 6)
        got.update(_subject_needs(a, window, fit_box(a.size, window.size)))
    got["problems"] = problems(got, window)
    if "subject" in got["problems"] and a.focus is not None and not got.get("hold_short"):
        # the drawn crop is the surface's crop without the box: the box does not turn it unfit (round 3, D3)
        got["problems"] = [p for p in got["problems"] if p != "subject"]
        got["subject_partial"] = True
    return {**got, "fit": not got["problems"], "moved": bool(drew.get("moved")), "window": window.name,
            "drawn": "cover crop", "measured": {k: v for k, v in rec.items() if k != "window"}}


def ground_fit(a: Analysis, size: tuple[int, int], centering: Sequence[float] = (0.5, 0.5)) -> dict[str, Any]:
    """The fit record of a GROUND crop (typography.cover_crop: the photograph under a card, a data graphic, a motion
    still; usually blurred or dimmed): the asked fit at `centering`, or, where it shows the source's frame, the
    nearest crop inside the content that keeps MOVE_KEEP of the asked view (place(), frame-only window). When none
    does, the asked crop is drawn and the record says so: "fit" False, "problems" ["matte"], "reason" (second review
    follow-up: a blurred ground wrote no record, so a plate's border under it was neither fixed nor recorded)."""
    window = Window((int(size[0]), int(size[1])), checks=("matte",), name="ground")
    asked = fit_box(a.size, window.size, tuple(centering))
    if a.matte_share <= 0:
        return {"window": "ground", "box": [round(v, 1) for v in asked], "fit": True, "moved": False, "problems": [],
                "matte": 0.0}
    rec = place(None, window, tuple(centering), analysis=a)
    if rec["fit"] and rec["moved"]:
        return {**rec, "window": "ground"}
    got = measure(a, asked, window)
    got["problems"] = problems(got, window)
    out = {**got, "fit": not got["problems"], "moved": False, "window": "ground"}
    if got["problems"]:
        out["reason"] = reason(out, "the ground under the surface")
    return out


_grounds = threading.local()


def note_ground(photo: Path, size: tuple[int, int], rec: dict[str, Any]) -> None:
    """Keeps a drawn ground's fit record on this thread until the surface's record takes it (take_grounds)."""
    kept = getattr(_grounds, "kept", None)
    if kept is None:
        kept = _grounds.kept = []
    entry = {"photo": Path(photo).name, "size": [int(size[0]), int(size[1])],
             **{k: rec.get(k) for k in ("box", "fit", "moved", "problems", "matte", "reason", "kept_asked")
                if k in rec}}
    if entry not in kept:
        kept.append(entry)
    del kept[:-64]


def take_grounds() -> list[dict[str, Any]]:
    """The ground records noted on this thread since the last take (and clears them)."""
    kept = getattr(_grounds, "kept", None) or []
    _grounds.kept = []
    return kept
