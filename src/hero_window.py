"""The hero-window rule (design-freeze cycle 0 round 7, panel F9): no delivered crop of a photograph contains a
panel seam or a printed banner row.

A source photograph may be several pictures in one file (a multi-panel composite: a row or column where the
picture steps abruptly across nearly the whole frame) or carry a provider's banner printed along an edge ("Copernicus
Sentinel data [2023], processed by Sentinel Hub/Pierre Markuse", Richat Story frame 1, y 0-40). Either one inside a
slide, Story frame, card or cover reads as a rendering fault. The rule is topic-agnostic and sits where every hero
crop starts: photo_hygiene.without_caption_strip (typography.cover_crop, photo_grade.cover, the cards' toned
grounds, then-and-now, callouts and the covers' clean_hero all crop from its result), so every window cut from
the picture lies inside its CLEAN REGION.

The clean region: every DETECTOR returns forbidden bands (("row", y0, y1) full-width rows or ("col", x0, x1)
full-height columns, image px); the region is the longest run of rows free of every row band times the longest run
of columns free of every column band (the centre's run on a tie). A window of any aspect fitted inside it (cover
crop) can then contain no forbidden band.

Detectors here (this row's part):
- panel_seams: a row (or column) where the luma steps by at least SEAM_STEP over at least SEAM_SHARE of the width
  (the panel's "> 80 % of the width") within SEAM_SPAN analysis rows, while just off it the picture does not (not
  texture), the two sides stay apart (not a thin printed line) and one side is a gutter or frame (FLAT_ROWS rows of
  one flat colour edge to edge). Measured on 196 pool photos of 12 topics (round 7): without the gutter condition a
  roof's eaves on a drone shot and a chart's double rule were taken for seams (half a picture cut away); with it the
  hits are scan frames, black poster borders and caption strips. Two pictures butted with no gutter are NOT found
  here (no measured case; a statistics detector can plug in through register());
- caption_strips: photo_hygiene's printed edge strip (a flat-coloured band with glyph marks that stops at a clear
  boundary), now analysed at a height that resolves a strip of ~1 % of the picture.

The boundary with QF-144 (burned-in text bands, its own row): QF-144 owns text DETECTION beyond these two shapes
(glyph / OCR detection of interior or corner text and watermarks, the Commons description's own note, ingest-time
measurement over every pool photo, rejecting a photo whose subject the crop would lose). It plugs in through
register(detector): a detector is called as detector(image, small, scale) -> list of bands in image px, and every
surface then honours its bands with no change of its own.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

Band = tuple[str, int, int]          # ("row" | "col", start, end) in image px, end exclusive
Detector = Callable[[Image.Image, np.ndarray, float], list[Band]]

ANALYSIS = 900          # px: the longer side the seams are analysed at
SEAM_STEP = 48.0        # luma (0-255) a column steps across the seam
SEAM_SHARE = 0.80       # of the width (columns) that step at the same row: the panel's "> 80 %"
SEAM_SPAN = 3           # analysis rows the step completes within
SEAM_OFF = 0.30         # the windows just off a seam step so on fewer columns than this (texture is not a seam)
SEAM_KEEP = 6           # analysis rows either side whose mean must stay apart (a thin printed line comes back)
FLAT_STD = 4.0          # luma: a row whose spread across the width is under this is one flat colour (8 took a
                        # busy picture's downsampled noise rows, std ~7, for a gutter: round 7 CI, then-and-now)
FLAT_ROWS = 2           # a gutter or frame: at least this many flat rows on one side of the seam
GUTTER_MAX = 0.06       # ... and its flat run at most this share of the axis: a longer one is a clear sky or a plain
                        # ground meeting the picture (a horizon), not a gutter or a frame
SEAM_MARGIN = 2         # analysis px cut either side of a seam (resampling spreads it)
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _small(im: Image.Image) -> tuple[np.ndarray, float]:
    w, h = im.size
    scale = min(1.0, ANALYSIS / max(w, h))
    small = im.convert("RGB").resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BOX)
    return np.asarray(small, dtype=np.float32), scale


def _seam_rows(lum: np.ndarray) -> list[int]:
    """Rows (analysis px) of seams: the luma steps by SEAM_STEP or more within SEAM_SPAN rows on SEAM_SHARE of the
    columns, while the windows just off it on both sides step so on under SEAM_OFF of them (a seam, not texture)."""
    h = lum.shape[0]
    s = SEAM_SPAN
    if h < 4 * s + 4:
        return []
    share = (np.abs(lum[s:] - lum[:-s]) >= SEAM_STEP).mean(axis=1)    # [y]: rows y -> y + s
    spread = lum.std(axis=1)
    flat = spread <= FLAT_STD                                          # rows one flat colour edge to edge
    means = lum.mean(axis=1)
    out: list[int] = []
    for y in range(s + 1, len(share) - s - 1):
        if share[y] < SEAM_SHARE or max(share[y - s - 1], share[y + s + 1]) >= SEAM_OFF:
            continue
        above, below = lum[max(0, y - SEAM_KEEP):y].mean(axis=0), lum[y + s:y + s + SEAM_KEEP].mean(axis=0)
        if (np.abs(above - below) >= SEAM_STEP).mean() < SEAM_SHARE:
            continue                                        # it comes back: a printed line, not two pictures
        gutter = False
        for start, step in ((y - 1, -1), (y + s, 1)):           # the flat run outward from the seam, each side
            end = start
            while 0 <= end < h and flat[end]:
                end += step
            run = abs(end - start)
            if not FLAT_ROWS <= run <= GUTTER_MAX * h:
                continue
            # a band: it reaches the edge (a frame) or the picture resumes clearly after it (a gutter)
            if not 0 <= end < h or spread[end] >= 3 * FLAT_STD or                     abs(means[end] - means[end - step]) >= SEAM_STEP / 2:
                gutter = True
        if not gutter:
            continue                                        # no gutter or frame: a straight edge or a horizon
        out.append(y + s // 2)
    merged: list[int] = []
    for y in out:                                           # one seam per run of adjacent rows
        if not merged or y - merged[-1] > s + 1:
            merged.append(y)
    return merged


def panel_seams(im: Image.Image, small: np.ndarray, scale: float) -> list[Band]:
    """Seams of a multi-panel picture, rows and columns, as thin forbidden bands in image px."""
    lum = small @ LUMA
    bands: list[Band] = []
    for kind, arr, size in (("row", lum, im.size[1]), ("col", lum.T, im.size[0])):
        for y in _seam_rows(arr):
            a = max(0, int(np.floor((y - SEAM_MARGIN) / scale)))
            b = min(size, int(np.ceil((y + SEAM_MARGIN + 1) / scale)))
            bands.append((kind, a, b))
    return bands


def caption_strips(im: Image.Image, small: np.ndarray, scale: float) -> list[Band]:
    """A provider's caption strip printed along the top or bottom edge (photo_hygiene.caption_strip)."""
    from .photo_hygiene import caption_strip
    found = caption_strip(im)
    if not found:
        return []
    edge, px = found
    return [("row", 0, px)] if edge == "top" else [("row", im.size[1] - px, im.size[1])]


DETECTORS: list[Detector] = [caption_strips, panel_seams]


def register(detector: Detector) -> None:
    """Adds a detector (QF-144's text-band detection plugs in here); every surface honours its bands."""
    if detector not in DETECTORS:
        DETECTORS.append(detector)


def unregister(detector: Detector) -> None:
    if detector in DETECTORS:
        DETECTORS.remove(detector)


def forbidden(im: Image.Image, detectors: Iterable[Detector] | None = None) -> list[Band]:
    """Every forbidden band every detector finds in `im` (image px)."""
    small, scale = _small(im)
    out: list[Band] = []
    for det in (DETECTORS if detectors is None else detectors):
        try:
            out += [(k, int(a), int(b)) for k, a, b in det(im, small, scale) if b > a]
        except Exception:                                   # a detector that fails leaves the others' bands
            log.exception("hero window detector %s failed", getattr(det, "__name__", det))
    return out


def _longest_run(size: int, cuts: list[tuple[int, int]]) -> tuple[int, int]:
    """The longest run of 0..size free of every cut (the one nearest the centre on a tie)."""
    edges, start = [], 0
    for a, b in sorted(cuts):
        if a > start:
            edges.append((start, a))
        start = max(start, b)
    if start < size:
        edges.append((start, size))
    if not edges:
        return 0, size
    centre = size / 2
    return max(edges, key=lambda r: (r[1] - r[0], -abs((r[0] + r[1]) / 2 - centre)))


def clean_box(im: Image.Image, bands: list[Band] | None = None) -> tuple[int, int, int, int]:
    """The clean region of `im` (x0, y0, x1, y1): no forbidden band inside it."""
    bands = forbidden(im) if bands is None else bands
    w, h = im.size
    y0, y1 = _longest_run(h, [(a, b) for k, a, b in bands if k == "row"])
    x0, x1 = _longest_run(w, [(a, b) for k, a, b in bands if k == "col"])
    return x0, y0, x1, y1


CLEAN_PASSES = 3        # a region cut once is examined again (a framed picture: its foot first, then its top)


def clean(im: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int], list[Band]]:
    """(`im` cropped to its clean region, that box in `im`'s px, the bands found). The region is examined again
    after each cut (at most CLEAN_PASSES times): a band that spans most of a framed picture's panel but not of the
    whole file shows only once the frame around it is gone."""
    x, y = 0, 0
    cur, found = im, []
    for _ in range(CLEAN_PASSES):
        bands = forbidden(cur)
        box = clean_box(cur, bands)
        if box == (0, 0) + cur.size:
            break
        found += [(k, a + (y if k == "row" else x), b + (y if k == "row" else x)) for k, a, b in bands]
        cur = cur.crop(box)
        x, y = x + box[0], y + box[1]
    box = (x, y, x + cur.size[0], y + cur.size[1])
    if found:
        log.info("Hero window: %s cut away; the clean region is %s of %s.", found, box, im.size)
    return cur, box, found


def window_problems(window: Image.Image, detectors: Iterable[Detector] | None = None) -> list[Band]:
    """The forbidden bands a delivered window (a crop as drawn) still contains: [] when it is clean."""
    return forbidden(window, detectors)
