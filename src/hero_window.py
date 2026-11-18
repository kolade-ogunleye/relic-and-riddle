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


# ---- photographed text (cycle 0 round 8, official panel 1 consensus 3: the Antikythera museum label "Ο ΜΗΧΑΝΙΣΜΟΣ
# ΤΩΝ ΑΝΤΙΚΥΘΗΡΩΝ / THE ANTIKYTHERA MECHANISM" read through the carousel's body band and the Reel cover's strip).
# OCR-free: a TEXT LINE is a chain of at least TEXT_MIN_GLYPHS glyph-like blobs (local-contrast components, either
# polarity) of one height (TEXT_CAP_MIN..TEXT_CAP_MAX of the picture's height, heights within TEXT_HEIGHT_CV), each
# beside the next (a gap under TEXT_GAP_MAX x the height, feet within TEXT_BASE_TOL of it: a tilted label still
# chains), never all one width (TEXT_WIDTH_CV) and never solid (a fill of 0.12-0.85: windows, posts and columns fill
# their boxes). A picture whose text reaches its middle (a document, a map, a manuscript, a museum panel: the text IS the subject)
# keeps it. Otherwise text (lines of TEXT_LINE_MIN_W of the width or more) in the outer TEXT_EDGE_SHARE at the top or the bottom (a label, a plaque, a sign in front
# of or under the subject) is one forbidden row band per edge, so every crop keeps copy and the masthead off it.
TEXT_ANALYSIS = 1600     # px: the longer side text is analysed at (a 4000 px photo's 1 % cap is 16 px here)
TEXT_CAP_MIN = 0.008     # of the picture's height: smaller is texture
TEXT_CAP_MAX = 0.06      # larger is architecture
TEXT_MIN_GLYPHS = 6
TEXT_HEIGHT_CV = 0.22
TEXT_WIDTH_CV = 0.06     # capitals of one label are near one width (0.09 measured); a solid shape fails the fill test
TEXT_GAP_MAX = 0.9
TEXT_BASE_TOL = 0.22     # of the height: a glyph's foot against its neighbour's
TEXT_EDGE_SHARE = 0.35
TEXT_MARGIN = 0.6        # of the line's height, kept clear above and below it
TEXT_LINE_MIN_W = 0.10   # of the picture's width: a shorter chain is texture (eaves, railings, distant arcades:
                         # 31 of 574 pool photos measured 0.02-0.09; the Antikythera label's lines 0.24-0.63)


def _glyph_blobs(g: np.ndarray, polarity: int, lo: float, hi: float) -> np.ndarray:
    import cv2
    block = int(max(15, 2 * round(hi) + 1)) | 1
    bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, polarity, block, 18)
    _n, _lab, st, _c = cv2.connectedComponentsWithStats(bw, connectivity=8)
    st = st[1:].astype(np.float64)
    _x, _y, w, h, area = st.T if len(st) else (np.zeros(0),) * 5
    fill = area / np.maximum(1.0, w * h)
    keep = (h >= lo) & (h <= hi) & (w <= 1.6 * h) & (w >= 0.12 * h) & (fill >= 0.12) & (fill <= 0.85)
    return st[keep][:, :4]


def text_lines(im: Image.Image) -> list[tuple[int, int, int, int]]:
    """Photographed text lines (x0, y0, x1, y1, image px) anywhere in `im` (see the rule above)."""
    import cv2
    w, h = im.size
    scale = min(1.0, TEXT_ANALYSIS / max(w, h))
    g = np.asarray(im.convert("L").resize((max(1, round(w * scale)), max(1, round(h * scale))),
                                          Image.Resampling.BOX), np.uint8)
    hs = g.shape[0]
    lo, hi = max(4.0, TEXT_CAP_MIN * hs), TEXT_CAP_MAX * hs
    lines: list[tuple[int, int, int, int]] = []
    for polarity in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):     # dark glyphs on light, light on dark
        blobs = _glyph_blobs(g, polarity, lo, hi)
        if len(blobs) < TEXT_MIN_GLYPHS:
            continue
        order = np.argsort(blobs[:, 0])
        bx, by, bw_, bh = (blobs[order, k] for k in range(4))
        foot, right = by + bh, bx + bw_
        nxt = np.full(len(bx), -1)
        for i in range(len(bx)):                   # each glyph's nearest right neighbour on its baseline
            j0 = np.searchsorted(bx, right[i] - 0.2 * bh[i], "left")
            j1 = np.searchsorted(bx, right[i] + TEXT_GAP_MAX * bh[i], "right")
            if j1 <= j0:
                continue
            cand = np.arange(j0, j1)
            cand = cand[(cand != i) & (np.abs(foot[cand] - foot[i]) <= TEXT_BASE_TOL * bh[i])
                        & (np.abs(bh[cand] - bh[i]) <= 0.35 * bh[i])]
            if len(cand):
                nxt[i] = cand[np.argmin(bx[cand] - right[i])]
        has_prev = np.zeros(len(bx), bool)
        has_prev[nxt[nxt >= 0]] = True
        for start in np.flatnonzero(~has_prev):
            chain, k = [], int(start)
            while k >= 0 and k not in chain and len(chain) < 400:
                chain.append(k)
                k = int(nxt[k])
            if len(chain) < TEXT_MIN_GLYPHS:
                continue
            ch = np.array(chain)
            if bh[ch].std() / bh[ch].mean() > TEXT_HEIGHT_CV or bw_[ch].std() / bw_[ch].mean() < TEXT_WIDTH_CV:
                continue
            x0, y0, x1, y1 = bx[ch].min(), by[ch].min(), right[ch].max(), foot[ch].max()
            lines.append((int(x0 / scale), int(y0 / scale), int(np.ceil(x1 / scale)), int(np.ceil(y1 / scale))))
    return lines


def photo_text(im: Image.Image, small: np.ndarray | None = None, scale: float = 1.0) -> list[Band]:
    """Photographed text at the picture's top or bottom edge (the outer TEXT_EDGE_SHARE), one forbidden row band per
    edge (its lines, TEXT_MARGIN of a line's height either side); none where the text reaches the middle (the
    picture is a document, a map or a panel: its text is the subject)."""
    w, h = im.size
    found = [ln for ln in text_lines(im) if ln[2] - ln[0] >= TEXT_LINE_MIN_W * w]
    if any(ln[3] > TEXT_EDGE_SHARE * h and ln[1] < (1 - TEXT_EDGE_SHARE) * h for ln in found):
        return []
    bands: list[Band] = []
    for edge in ("top", "bottom"):
        near = [ln for ln in found if (ln[3] <= TEXT_EDGE_SHARE * h if edge == "top" else ln[1] >= (1 - TEXT_EDGE_SHARE) * h)]
        if not near:
            continue
        pad = max(TEXT_MARGIN * (ln[3] - ln[1]) for ln in near)
        a = max(0, int(min(ln[1] for ln in near) - pad))
        b = min(h, int(np.ceil(max(ln[3] for ln in near) + pad)))
        bands.append(("row", a, b))
    return bands


DETECTORS: list[Detector] = [caption_strips, panel_seams, photo_text]


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


# ---- the deck window rule and the masthead box (cycle 0 round 8, official panel 1 consensus 3): Aswan s6 and s7
# showed one photograph in near-identical windows on consecutive slides (the swipe stalls, CD-6), and Nazca s1 set the
# masthead on an aircraft wing's red trim and white skin (CD-4). A deck that shows one photograph again takes a window
# that overlaps its earlier ones as little as the picture allows (under DECK_OVERLAP_MAX where it can), and every
# window keeps the picture's centre (the subject: subject visibility ranks above rhythm); among those, one whose
# masthead box holds no foreign object (foreign_object) is preferred.
DECK_OVERLAP_MAX = 0.5
DECK_STEPS = 9           # candidate positions along the free axis, 0..1
FOREIGN_CHROMA = 90      # RGB max - min: a saturated man-made colour (a red trim, a sign), not sand, stone or sky
FOREIGN_SHARE = 0.03     # of the masthead box
FOREIGN_LINE = 0.4       # a straight edge at least this share of the box's width (a wing's, a sign's)


def foreign_object(region: Image.Image) -> bool:
    """Does `region` (the masthead box on a candidate window) hold a foreign object: a saturated, straight-edged
    shape (FOREIGN_SHARE of its pixels at FOREIGN_CHROMA or more, and a straight edge of FOREIGN_LINE of its width)?"""
    import cv2
    rgb = np.asarray(region.convert("RGB"), np.int16)
    if rgb.size == 0:
        return False
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    if float((chroma >= FOREIGN_CHROMA).mean()) < FOREIGN_SHARE:
        return False
    g = np.asarray(region.convert("L"), np.uint8)
    edges = cv2.Canny(g, 60, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=30, minLineLength=max(8, int(FOREIGN_LINE * g.shape[1])),
                            maxLineGap=4)
    return lines is not None and len(lines) > 0


def deck_window(src_size: tuple[int, int], out_size: tuple[int, int], k: int = 0,
                masthead_box: tuple[float, float, float, float] | None = None,
                image: Image.Image | None = None) -> dict:
    """The window (ImageOps.fit centering) for the k-th showing (0 = first) of one photograph (its clean region is
    `src_size`) in a deck, at `out_size`: every window holds the picture's centre; occurrence k takes the position
    whose largest overlap with occurrences 0..k-1 (each chosen the same way) is least, then (with `image`, the clean
    picture, and `masthead_box`, out px) one with no foreign object in the masthead box, then the nearest the centre.
    {"centering", "overlap" (the largest with an earlier showing), "masthead_clear", "positions"}."""
    sw, sh = src_size
    ow, oh = out_size
    if sw <= 0 or sh <= 0:
        return {"centering": (0.5, 0.5), "overlap": 0.0, "masthead_clear": None, "positions": [0.5]}
    horizontal = sw / sh > ow / oh                 # the free axis: across a wider picture, down a taller one
    span = (oh * sw / sh) / ow if horizontal else (ow * sh / sw) / oh   # the source / window length on that axis
    L = 1.0 / span if span > 0 else 1.0            # the window's share of the free axis
    grid = [i / (DECK_STEPS - 1) for i in range(DECK_STEPS)]
    lo, hi = ((0.5 - L) / (1 - L), 0.5 / (1 - L)) if L < 1 else (0.5, 0.5)   # the centre stays in view
    lo, hi = max(0.0, lo), min(1.0, hi)
    keep = sorted({round(p, 4) for p in grid if lo - 1e-9 <= p <= hi + 1e-9} | {round(lo, 4), round(hi, 4), 0.5})

    def overlap(p: float, q: float) -> float:
        return max(0.0, L - abs(p - q) * (1 - L)) / L if L > 0 else 1.0

    small = None
    if image is not None and masthead_box is not None:
        small = image.convert("RGB")
        small.thumbnail((640, 640))
    seen: dict[float, bool | None] = {}

    def clear(p: float) -> bool | None:
        if small is None:
            return None
        if p not in seen:
            seen[p] = _clear_at(p)
        return seen[p]

    def _clear_at(p: float) -> bool:
        cen = (p, 0.5) if horizontal else (0.5, p)
        win = _fit_box(small.size, out_size, cen)
        kx, ky = (win[2] - win[0]) / ow, (win[3] - win[1]) / oh
        x0, y0, x1, y1 = masthead_box
        box = (round(win[0] + x0 * kx), round(win[1] + y0 * ky), round(win[0] + x1 * kx), round(win[1] + y1 * ky))
        return not foreign_object(small.crop(box))

    positions: list[float] = []
    cleared: list[bool | None] = []
    for _ in range(k + 1):
        def key(p: float):
            worst = max((overlap(p, q) for q in positions), default=0.0)
            # past DECK_OVERLAP_MAX the least overlap wins; under it every position is as good, so the masthead
            # box's ground decides, then the centre
            return (round(min(worst, 1.0), 3) if worst > DECK_OVERLAP_MAX + 1e-6 else 0.0, clear(p) is False,
                    abs(p - 0.5))
        best = min(keep, key=key)
        positions.append(best)
        cleared.append(clear(best))
    p = positions[-1]
    worst = max((overlap(p, q) for q in positions[:-1]), default=0.0)
    return {"centering": (p, 0.5) if horizontal else (0.5, p), "overlap": round(worst, 3),
            "masthead_clear": cleared[-1], "positions": positions}


def _fit_box(src: tuple[int, int], out: tuple[int, int], centering: tuple[float, float]) -> tuple[float, ...]:
    """The box ImageOps.fit crops from a `src`-sized picture for `out` at `centering` (src px)."""
    sw, sh = src
    ow, oh = out
    if sw / sh > ow / oh:
        cw, ch = sh * ow / oh, sh
    else:
        cw, ch = sw, sw * oh / ow
    x0 = (sw - cw) * centering[0]
    y0 = (sh - ch) * centering[1]
    return (x0, y0, x0 + cw, y0 + ch)


def window_problems(window: Image.Image, detectors: Iterable[Detector] | None = None) -> list[Band]:
    """The forbidden bands a delivered window (a crop as drawn) still contains: [] when it is clean."""
    return forbidden(window, detectors)
