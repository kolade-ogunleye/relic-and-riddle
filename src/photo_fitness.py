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
  fails it whatever the subject is.
- flat: the share of the view whose blocks have no spread of luma (FLAT_STD over FLAT_BLOCK: sand, sky, a wall,
  out-of-focus ground; the panel's own block measure), against FLAT_MAX.
- tone: "extreme", the share of the view that is near-white or near-black (TONE_WHITE, TONE_BLACK), against TONE_MAX;
  asked only of a hero (hero_photo.pick: Window.checks names it), where another photograph of the run can lead.
- protected: per protected box, "foreign" (hero_window.foreign_object: a saturated, straight-edged man-made shape)
  "line" (the longest straight strong edge, against LINE_MAX) and "step" (two tonal masses, against STEP_MAX):
  type is not set on a wing, a solar array or across a roofline; "busy" (the share of strong edges) is recorded.

The ladder when a crop is unfit (the brief's order; never a blur pad, a feather or a dim):
1. another photograph the run already has that is fit: `rank()` orders candidates, the pickers call it;
2. another crop of this photograph: `place()` searches positions, then closer crops up to ZOOM_MAX and UPSCALE_MAX,
   inside the content;
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
FOCUS_KEEP = 0.995        # of a reviewed subject's box in view: all of it (type is never set on it)
BUSY_EDGE = 24.0          # luma gradient: a strong edge (recorded as "busy"; half the boxes are over 0.19: no gate)
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


def matte_mask(lum: np.ndarray, video: bool = False) -> np.ndarray:
    """The frame pixels of a luma picture (0-255): every connected region of digital black (MATTE_LUMA or under,
    even, MATTE_MEAN or under as a whole) of at least MATTE_MIN_AREA that touches the picture's edge. `video`: an
    encoded frame, whose black carries noise (twice the levels)."""
    import cv2
    k = 2.0 if video else 1.0
    n, lab, st, _ = cv2.connectedComponentsWithStats((lum <= MATTE_LUMA * k).astype(np.uint8), connectivity=8)
    mask = np.zeros(lum.shape, bool)
    ah, aw = lum.shape
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in st[i])
        if area < MATTE_MIN_AREA * ah * aw or not (x == 0 or y == 0 or x + bw == aw or y + bh == ah):
            continue
        comp = lab == i
        if float(lum[comp].std()) <= MATTE_STD * k and float(lum[comp].mean()) <= MATTE_MEAN * k:
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
    import cv2
    w, h = clean.size
    scale = min(1.0, ANALYSIS / max(w, h))
    aw, ah = max(1, round(w * scale)), max(1, round(h * scale))
    rgb = np.asarray(clean.convert("RGB").resize((aw, ah), Image.Resampling.BOX), np.uint8)
    lum = rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    matte = matte_mask(lum, video)
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
    thumb = clean.convert("RGB")
    thumb.thumbnail((THUMB, THUMB), Image.Resampling.BILINEAR)
    thumb.info = {}   # no EXIF or ICC blob kept with the analysis
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
    """The reviewed subject of the photo at `path` as a box on its clean picture (None: not reviewed)."""
    try:
        from .cover_design import reviewed_focus
        found = reviewed_focus(path)
    except Exception:                                   # no art-direction file: no reviewed subject
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
    prot = [protected_measure(a, _sub(box, out, p)) for p in window.protected]
    if prot:
        rec["protected"] = prot
    return rec


def problems(rec: dict[str, Any], window: Window) -> list[str]:
    """Why a measured crop is unfit for `window` (the measures' names; [] when it is fit)."""
    out = []
    if rec["matte"] > MATTE_MAX:
        out.append("matte")
    if rec["upscale"] > window.upscale_max + 1e-6:
        out.append("upscale")
    if "focus" in rec:
        if rec["focus"] < window.focus_min or rec.get("held", 1.0) < FOCUS_KEEP:
            out.append("subject")
    elif rec.get("keep", 1.0) < SUBJECT_KEEP or rec["density"] < DENSITY_MIN:
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
    """The crop of a photograph for `window`, and what was measured. The crop the surface would take (the plain fit
    at `centering`) is kept when it is fit; otherwise the fit crop that changes least, cut from the content box:
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

    def judged(b: Box, zoom: float, best: float) -> dict[str, Any]:
        rec = measure(a, b, window, best)
        rec["problems"] = problems(rec, window)
        if earlier:
            rec["overlap"] = round(max(_overlap(b, e) for e in earlier), 3)
        rec["zoom"] = zoom
        return rec

    def crowded(r: dict[str, Any]) -> bool:
        return overlap_max is not None and r.get("overlap", 0.0) > overlap_max + 1e-6

    plain = _candidates(a, window, 1.0)
    best1 = max([held(asked), *(held(b) for b in plain)])
    base = judged(asked, 1.0, best1)
    if fixed or (not base["problems"] and not crowded(base)):
        return {**base, "fit": not base["problems"], "moved": False, "window": window.name}

    def near(r: dict[str, Any]) -> float:
        b = r["box"]
        return abs((b[0] + b[2]) - (asked[0] + asked[2])) + abs((b[1] + b[3]) - (asked[1] + asked[3]))

    found: dict[str, Any] | None = None       # the first fit crop (it may crowd an earlier showing)
    least: tuple[tuple, dict[str, Any]] | None = None
    for zoom in ZOOM_STEPS:
        if zoom > window.zoom_max + 1e-9:
            break
        boxes = plain if zoom == 1.0 else _candidates(a, window, zoom)
        if window.size[0] / max(1e-6, boxes[0][2] - boxes[0][0]) > window.upscale_max + 1e-6:
            break
        best = best1 if zoom == 1.0 else max(held(b) for b in boxes)
        recs = [judged(b, zoom, best) for b in boxes]
        ok = sorted((r for r in recs if not r["problems"]),
                    key=lambda r: (crowded(r), round(r.get("overlap", 0.0), 2) if crowded(r) else 0.0,
                                   -round(r.get("focus", 0.0), 2), -round(r["subject"], 3), near(r)))
        if ok:
            if found is None:
                found = ok[0]
            if not crowded(ok[0]):
                found = ok[0]
                break
        for r in recs:   # no fit crop: the record is the crop with the fewest problems, then the most subject
            k = (len(r["problems"]), "matte" in r["problems"], zoom, -round(r["subject"], 3), near(r))
            if least is None or k < least[0]:
                least = (k, r)
    asked_rec = {k: v for k, v in base.items() if k != "zoom"}
    if found is not None:
        return {**found, "fit": True, "moved": True, "window": window.name, "base": asked_rec}
    if base["problems"] and least is not None and least[0][:2] < (len(base["problems"]), "matte" in base["problems"]):
        return {**least[1], "fit": False, "moved": True, "window": window.name, "base": asked_rec}
    return {**base, "fit": not base["problems"], "moved": False, "window": window.name}


def place_in_view(analysis: Analysis, window: Window) -> dict[str, Any]:
    """The crop of a photograph whose VIEW holds its subject, for a window most of which another picture covers (a
    then-and-now pane: the frame is split and each photograph shows in its own part only). Of every crop inside the
    content (each position, each step closer to window.zoom_max and window.upscale_max) with no frame in it, the one
    whose view holds the most of the photograph's salience AND the most of the crop's own (subject x the view's
    share of what the crop holds: a subject left under the other pane counts against it); then the least zoom.
    Returns place()'s record ("fit" by the same problems())."""
    a = analysis
    view = window.view_box()
    best: tuple[float, dict[str, Any]] | None = None
    for zoom in ZOOM_STEPS:
        if zoom > window.zoom_max + 1e-9:
            break
        boxes = _candidates(a, window, zoom)
        if window.size[0] / max(1e-6, boxes[0][2] - boxes[0][0]) > window.upscale_max + 1e-6:
            break
        top = max(a.total("salience", _sub(b, window.size, view)) for b in boxes)
        for b in boxes:
            rec = measure(a, b, window, top)
            if rec["matte"] > MATTE_MAX:
                continue
            whole = a.total("salience", b)
            score = rec["subject"] * (rec["subject"] / whole if whole > 1e-9 else 0.0)
            if best is None or score > best[0] + 1e-9:
                rec["zoom"] = zoom
                rec["in_view"] = round(rec["subject"] / whole, 3) if whole > 1e-9 else 0.0
                best = (score, rec)
    if best is None:
        rec = measure(a, fit_box(a.size, window.size), window)
        rec.update(zoom=1.0, problems=problems(rec, window))
        return {**rec, "fit": False, "moved": False, "window": window.name}
    rec = best[1]
    rec["problems"] = problems(rec, window)
    return {**rec, "fit": not rec["problems"], "moved": True, "window": window.name}


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
