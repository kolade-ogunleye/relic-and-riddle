"""
src/graphics.py
===============
Original animated graphics made by the channel itself: a locator map, a timeline and a
scale comparison. Meta's originality guidelines favour material "created by the publisher",
and these add information the photos can't show.

Each clip is 1080x1920, CLIP_SECONDS long (enough for the longest beat plus transitions),
with a poster frame saved beside it (<clip>.jpg) for the scene planner. Text stays above
the caption zone. Data: Natural Earth (public domain) outlines, Wikidata/topic coordinates
(src/geo.py); timeline and scale facts come from the fact-checked script; scale references
are fixed, sourced values (REFERENCES; docs/sources.md).
"""

import concurrent.futures
import dataclasses
import logging
import math
import os
import re
import subprocess
import uuid
import weakref
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFilter
from PIL.ImageFont import FreeTypeFont

from . import geo, raster
from .art_direction import MOTION, accent, count_start, push_in
from .camera_motion import kenburns
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .encoding import bounded_encode
from .trusted_image import open_trusted
from .typography import (
    LayoutError,
    TextFitError,
    TextPlate,
    cap_height,
    drawable,
    field_font,
    fit_font,
    fit_text,
    get_font,
    plan_text,
    probe_text,
    spoken_system,
    spoken_units,
    tracked_width,
)

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt
    BoolArray = npt.NDArray[np.bool_]
    FloatArray = npt.NDArray[np.float32]
else:
    BoolArray = FloatArray = object

Box = tuple[float, float, float, float]

log = logging.getLogger(__name__)

CLIP_SECONDS = 6.0
W, H = VIDEO_WIDTH, VIDEO_HEIGHT
from . import brand

# Brand tokens (src/brand.py, visual overhaul 2026-10-29): one gold, bone text, the ink ground.
SEA, LAND, HOME, BORDER, GRID = brand.INK, (40, 43, 48), (92, 74, 42), (96, 98, 104), (24, 28, 34)
GOLD, WHITE, MUTED = brand.GOLD, brand.BONE, brand.STONE_LT
GOLD_LINE = brand.GOLD_DIM
M = 84  # a 9:16 graphic's left text margin (19 px inside the ads Reel box), shared with the Story column
# Where a 9:16 graphic may put text and figures: the Reel box every platform leaves free (safe_zones: the shared
# organic Reel profile, x 113-848, y 288-1370 at 1080 x 1920), less the caption band and its gap at its bottom (the
# graphics end at y 1090). TEXT_TOP stays a little below the box's top edge, as it was; TEXT_MAX_W is the widest
# text centred on the frame.
from . import safe_zones

_BOX = safe_zones.graphics_box(VIDEO_WIDTH, VIDEO_HEIGHT)
TEXT_TOP, TEXT_BOTTOM = max(330, _BOX[1]), _BOX[3]
TEXT_LEFT, TEXT_RIGHT = _BOX[0], _BOX[2]
TEXT_MAX_W = safe_zones.REEL.centred_width(VIDEO_WIDTH, VIDEO_HEIGHT)
# The shared inset edge (2026-11-02): every 9:16 graphic (timeline, scale, cross-section, number cards) starts its
# words and drawings on one left edge and keeps its drawings to INSET_X0-INSET_X1, inside the Reel box, so a cut from
# one graphic to the next keeps the column still (each had its own edge at 65-102 before). QF-24: x 120-820, inside
# the organic box (113-848), whose left edge already holds 28 px of clearance and annotation uncertainty (x 100-860
# inside the ads box, 65-888, until then). The right edge keeps its 28 px inside the box.
INSET_PAD_LEFT, INSET_PAD_RIGHT = 7, 28
INSET_X0, INSET_X1 = TEXT_LEFT + INSET_PAD_LEFT, TEXT_RIGHT - INSET_PAD_RIGHT


def inset_x0(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> int:
    """The shared left edge of a 9:16 graphic on this frame (INSET_X0 on 1080 wide): the graphics box's left edge
    plus INSET_PAD_LEFT, scaled."""
    return safe_zones.graphics_box(width, height)[0] + round(INSET_PAD_LEFT * width / VIDEO_WIDTH)


def inset_x1(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> int:
    """The right edge of a 9:16 graphic's drawings on this frame (INSET_X1 on 1080 wide)."""
    return safe_zones.graphics_box(width, height)[2] - round(INSET_PAD_RIGHT * width / VIDEO_WIDTH)

from .reference_facts import (
    REFERENCE_FILE,
    measurement_text,
    reference_note,
    references,
    selected_reference,
)

REFERENCES = [(r["label"], r["feet"], r["source_url"]) for r in references()]
PERSON = next(r["label"] for r in references() if r["id"] == "R_US_HEIGHT")


def _ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def _text(d: ImageDraw.ImageDraw, xy, text: str, font, fill, anchor: str = "la") -> None:
    """d.text with real transparency. Pillow ignores the alpha of text ink (alpha 0 still paints solid),
    so partly transparent text is drawn through an alpha mask instead."""
    alpha = fill[3] if len(fill) == 4 else 255
    text = drawable(text, font)  # no emoji, no missing-glyph boxes (2026-10-29)
    if alpha <= 0 or not text:
        return
    if alpha >= 255:
        d.text(xy, text, font=font, fill=tuple(fill[:3]), anchor=anchor)
        return
    l, t, r, b = d.textbbox(xy, text, font=font, anchor=anchor)
    l, t, r, b = int(l) - 2, int(t) - 2, int(r) + 3, int(b) + 3
    if r <= l or b <= t:
        return
    mask = Image.new("L", (r - l, b - t), 0)
    ImageDraw.Draw(mask).text((xy[0] - l, xy[1] - t), text, font=font, fill=alpha, anchor=anchor)
    d._image.paste(tuple(fill[:3]), (l, t, r, b), mask)


def _draw_cross_hatching(d: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, spacing: int = 14, fill=(100, 115, 130, 50), width: int = 1) -> None:
    """Architectural blueprint 45-degree drafting cross-hatching for technical schematics."""
    h = y1 - y0
    w = x1 - x0
    if h <= 0 or w <= 0:
        return
    for k in range(x0 - h, x1, spacing):
        lx0 = k
        lx1 = k + h
        if lx1 > x0 and lx0 < x1:
            sx = max(x0, lx0)
            sy = y0 + (sx - lx0)
            ex = min(x1, lx1)
            ey = y1 - (lx1 - ex)
            if ex > sx and ey > sy:
                d.line([(sx, sy), (ex, ey)], fill=fill, width=width)


class LayoutConstraintSolver:
    """Enforces non-overlapping bounding-box invariants and minimum margins across graphic elements."""

    def __init__(self, min_padding: int = 24):
        self.min_padding = min_padding

    def relax_vertical_stack(
        self,
        targets: list[float],
        heights: list[float],
        bounds: tuple[float, float],
        min_gap: float | None = None,
        iterations: int = 40,
    ) -> list[float]:
        """Applies bidirectional spring-relaxation to push overlapping elements apart while respecting boundary limits."""
        gap = min_gap if min_gap is not None else self.min_padding
        n = len(targets)
        if n < 2:
            return list(targets)
        ys = [float(y) for y in targets]
        top_limit, bottom_limit = bounds

        for _ in range(iterations):
            for i in range(n - 1):
                ys[i] = max(top_limit, ys[i])
                curr_gap = ys[i + 1] - (ys[i] + heights[i])
                if curr_gap < gap:
                    overlap = gap - curr_gap
                    push = overlap / 2.0
                    ys[i] = max(top_limit, ys[i] - push)
                    ys[i + 1] = min(bottom_limit - heights[i + 1], ys[i + 1] + push)

            # Clamp boundaries
            ys[0] = max(top_limit, ys[0])
            ys[-1] = min(bottom_limit - heights[-1], ys[-1])

        return ys

    @staticmethod
    def get_shelf_coordinates(
        d: ImageDraw.ImageDraw,
        title: str,
        f_title: Any,
        subtitle: str,
        f_sub: Any,
        shelf_x: int,
        shelf_y: int,
        align: str = "left",
        shelf_gap: int = 10,
    ) -> dict[str, Any]:
        """Calculates strict non-overlapping coordinates around a horizontal shelf line."""
        if align == "left":
            title_anchor = "ld"
            sub_anchor = "la"
            title_pos = (shelf_x, shelf_y - shelf_gap)
            sub_pos = (shelf_x, shelf_y + shelf_gap + 4)
        else:  # right
            title_anchor = "rd"
            sub_anchor = "ra"
            title_pos = (shelf_x, shelf_y - shelf_gap)
            sub_pos = (shelf_x, shelf_y + shelf_gap + 4)

        tb = d.textbbox(title_pos, title, font=f_title, anchor=title_anchor)
        sb = d.textbbox(sub_pos, subtitle, font=f_sub, anchor=sub_anchor)

        return {
            "title_pos": title_pos,
            "title_anchor": title_anchor,
            "title_bbox": tb,
            "sub_pos": sub_pos,
            "sub_anchor": sub_anchor,
            "sub_bbox": sb,
            "shelf_y": shelf_y,
            "is_valid": (tb[3] < shelf_y < sb[1]),
        }

    @staticmethod
    def boxes_intersect(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float], margin: float = 0.0) -> bool:
        """Determines if two Axis-Aligned Bounding Boxes (AABB) intersect, with optional clearance margin."""
        la, ta, ra, ba = box_a
        lb, tb, rb, bb = box_b
        return not (ra + margin <= lb or rb + margin <= la or ba + margin <= tb or bb + margin <= ta)

    @staticmethod
    def box_intersects_line(box: tuple[float, float, float, float], line_y: float, x_range: tuple[float, float]) -> bool:
        """Determines if a bounding box intersects a horizontal line segment."""
        l, t, r, b = box
        lx0, lx1 = min(x_range), max(x_range)
        return t <= line_y <= b and not (r <= lx0 or l >= lx1)


@bounded_encode
def _encode(frames: Iterable[Image.Image], out: Path, n: int, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """Raw RGB frames piped to ffmpeg (local only) -> H.264 MP4 + poster JPEG of the last frame."""
    out.parent.mkdir(parents=True, exist_ok=True)
    from .encoding import BT709_FLAGS, RGB_TO_BT709
    try:
        from .encoding import get_video_encoder
        enc_args = get_video_encoder()
    except Exception:  # noqa: BLE001  any encoder probe failure falls back to software x264
        enc_args = ["-c:v", "libx264", "-preset", "medium", "-crf", "17"]
    # BT.709 limited range, tagged (QF-01): swscale's default matrix is BT.601 and the master is BT.709.
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(VIDEO_FPS),
           "-i", "-", "-frames:v", str(n), "-vf", RGB_TO_BT709, *enc_args, *BT709_FLAGS, "-pix_fmt", "yuv420p", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    last = None
    try:
        for frame in frames:
            last = frame
            proc.stdin.write(frame.convert("RGB").tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait(timeout=300) != 0:
            raise RuntimeError(f"ffmpeg failed encoding {out.name}: {err[-500:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    if last is not None:
        last.convert("RGB").save(out.with_suffix(".jpg"), quality=90)
    return out


# ------------------------------------------------------- contrast on the graded frame (2026-10-29)
# A graphic's text is measured where the viewer reads it: after the master's colour conversions.
# No photo grade or grain is applied; the lens vignette is lifted while a graphic is on screen,
# video_composer.graphic_vignette_enable, 2026-11-02), with the glyph masks it was drawn with. The planned and
# graded ratios of each element are kept beside the clip (<clip>.contrast.json) for video_qa.graphic_contrast_problems.
CONTRAST_SIDECAR = ".contrast.json"


def graded_frame(frame: Image.Image, work: Path, vignette: bool = False) -> Image.Image | None:
    """`frame` through the master's graphic path (colour conversion, no photo curve; `vignette` adds the lens
    vignette, which the master lifts under graphic clips); None when ffmpeg cannot run here (the planned ratios
    are still recorded)."""
    try:
        from .encoding import BT709_TO_RGB, RGB_TO_BT709
        from .video_composer import picture_chain
        work.mkdir(parents=True, exist_ok=True)
        # round 8 (review): a name of its own per call: two renders sharing a stage_cache read each other's frame
        tag = f"{os.getpid()}_{uuid.uuid4().hex[:12]}"
        src, dst = work / f"_contrast_in_{tag}.png", work / f"_contrast_out_{tag}.png"
        frame.convert("RGB").save(src)
        aspect = "16/9" if frame.width > frame.height else "9/16"
        # BT.709 in and out, as the master converts and tags its pictures (QF-01); the grade and vignette in
        # 16-bit RGB, as the master's picture_chain (QF-02)
        chain = picture_chain("null", frame.width, frame.height,
                              vignette=vignette,
                              lens_filter=f"vignette=PI/4.2:aspect={aspect}")
        graph = f"[0:v]{RGB_TO_BT709},format=yuv420p,{chain},format=yuv420p,{BT709_TO_RGB},format=rgb24[out]"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[out]",
                        "-frames:v", "1", str(dst)], check=True, timeout=120, capture_output=True)
        with Image.open(dst) as im:
            out = im.convert("RGB")
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)
        return out
    except Exception as e:  # noqa: BLE001  a measurement, never a reason to lose the graphic
        log.warning("Graded contrast not measured (%s).", e)
        return None


NONTEXT_MIN = 3.0   # WCAG 1.4.11: an essential mark (spine, node, break, outline, ruler tick) against its ground
NONTEXT_BAND = 10   # px of ground around a mark that it is measured against


def nontext_contrast(frame: Image.Image, mask: Image.Image, band: int = NONTEXT_BAND,
                     ground: Image.Image | None = None) -> float | None:
    """WCAG ratio of a drawn mark (`mask`, L, 255 on the mark) on `frame`: the mark's median luminance against the
    90th-percentile luminance of the `band` px around it (round 4, 2026-11-02: the callouts' measure,
    callouts.nontext_contrast, extended to every graphic's essential marks). `ground`: the plate the mark is drawn
    on, read for the band instead of the frame, so a neighbouring mark (a node on its spine) is not taken for its
    ground. None when the mark is off the frame."""
    if ground is None:
        from .callouts import nontext_contrast as measure
        return measure(frame, mask, band)
    import cv2
    import numpy as np

    from .typography import _lin
    m = (np.asarray(mask) > 127).astype(np.uint8)
    if m.sum() < 8:
        return None
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
    ring = cv2.dilate(m, disc) - m
    if ring.sum() < 8:
        return None
    lt = float(np.median(_lin(np.asarray(frame.convert("RGB"))[m > 0])))
    lb = float(np.percentile(_lin(np.asarray(ground.convert("RGB"))[ring > 0]), 90))
    return round((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05), 2)


def mark_mask(size: tuple[int, int], paint) -> Image.Image:
    """An L mask of one mark: `paint(draw)` draws it in 255 on black (the same geometry the frame draws)."""
    m = Image.new("L", size, 0)
    paint(ImageDraw.Draw(m))
    return m


def nontext_rows(frame: Image.Image, graded: Image.Image | None, marks: list[tuple[str, Image.Image]],
                 ground: Image.Image | None = None) -> list:
    """Each mark's ratio as drawn and as graded (on the graded frame its band is read there too: the grade moves
    mark and ground together)."""
    rows = []
    for name, mask in marks:
        drawn = nontext_contrast(frame, mask, ground=ground)
        if drawn is None:
            continue
        rows.append({"name": name, "drawn": drawn, "graded": None if graded is None else
                     nontext_contrast(graded, mask, ground=None if ground is None else _grade_like(ground, frame, graded))})
    return rows


def record_nontext(out: Path, frame: Image.Image, marks: list[tuple[str, Image.Image]],
                   ground: Image.Image | None = None) -> dict[str, Any] | None:
    """Round 4 (2026-11-02): the clip's essential marks (masks on its last frame) measured against their ground
    and added to <clip>.contrast.json as a "nontext" block with its target (NONTEXT_MIN) and minimum, beside the
    text rows record_contrast wrote."""
    import json
    side = Path(str(out) + CONTRAST_SIDECAR)
    if not marks or not side.exists():
        return None
    record = json.loads(side.read_text(encoding="utf-8"))
    graded = graded_frame(frame, Path(out).parent / "stage_cache" / "contrast")
    nt = nontext_rows(frame, graded, marks, ground)
    vals = [r["graded"] if r["graded"] is not None else r["drawn"] for r in nt]
    record.update({"nontext_target": NONTEXT_MIN, "nontext": nt, "nontext_min": min(vals) if vals else None})
    side.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return record


def _grade_like(ground: Image.Image, frame: Image.Image, graded: Image.Image) -> Image.Image:
    """The ground as the grade leaves it: the frame's graded pixels where the frame shows the ground itself."""
    import numpy as np
    f, g, gr = (np.asarray(x.convert("RGB"), np.int16) for x in (frame, ground, graded))
    same = (np.abs(f - g).max(axis=2) <= 3)[..., None]
    shift = (gr - f)[same[..., 0]].mean(axis=0) if same.any() else np.zeros(3)
    return Image.fromarray(np.clip(g + shift, 0, 255).astype(np.uint8), "RGB")


def record_contrast(out: Path, frame: Image.Image, plates: list[tuple[str, Any]]) -> dict[str, Any]:
    """Each element's ratio on the clip's last frame as drawn and as graded, written beside the clip."""
    import json
    graded = graded_frame(frame, Path(out).parent / "stage_cache" / "contrast")
    rows = []
    for name, plate in plates:
        if plate is None or plate.empty:
            continue
        rows.append({"name": name, "text": plate.text, "planned": plate.contrast, "drawn": plate.measure(frame),
                     "graded": plate.measure(graded) if graded is not None else None, "halo": plate.halo,
                     "box": list(plate.box)})
    vals = [r["graded"] if r["graded"] is not None else r["drawn"] for r in rows]
    vals = [v for v in vals if v is not None]
    record = {"clip": Path(out).name, "target": brand.TEXT_CONTRAST, "graded": graded is not None,
              "min": min(vals) if vals else None, "elements": rows}
    Path(str(out) + CONTRAST_SIDECAR).write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8",
                                                 newline="\n")
    return record


# Round 6 (2026-11-03): the dual-unit scale bar. One 3 px line (SCALE_LINE) cased 1 px in INK either side, a shared
# zero tick 9 px up and down, the primary unit's end tick up and the secondary's down, each label right-aligned to
# its own end tick (no "0"). Round 7 (coordinator R1, R2): the unit the narration speaks on top, KM without one (the
# metric default for a global audience); the primary a 1-2-5 length (SCALE_SERIES x 10^n), the longest in the
# orientation's range, and the bar is the primary's length; the secondary is a labelled tick on that same bar at its
# nearest round value (SCALE_SECONDARY_SERIES) at least SCALE_MIN_SEP_PX and SCALE_MIN_SEP_SHARE short of the end,
# never a second bar past it and never the same number, else it is dropped (logged).
SCALE_SERIES = (1.0, 2.0, 5.0)            # the primary's lengths x 10^n (round 7, R2: 1.5 and 2.5 were in it)
SCALE_STEPS = tuple(sorted({round(s * 10 ** n, 6) for s in SCALE_SERIES for n in range(-1, 5)}))
SCALE_SECONDARY_SERIES = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0)   # the secondary tick's round values x 10^n
SCALE_SECONDARY_STEPS = tuple(sorted({round(s * 10 ** n, 6) for s in SCALE_SECONDARY_SERIES for n in range(-1, 5)}))
# coordinator ruling R10 (round 11): the primary bar 80-240 px on 9:16 and 100-300 px on 16:9 (150-230 and 180-280
# before: most locator bars fell short of the floor and were logged out of range)
SCALE_RANGE_TALL = (80.0, 240.0)     # px: the longer bar on 9:16
SCALE_RANGE_WIDE = (100.0, 300.0)    # px: and on 16:9 (design px)
SCALE_MIN_SEP_PX = 40.0              # px between the two end ticks
SCALE_MIN_SEP_SHARE = 0.25           # and the share of the longer they differ by
SCALE_LINE = (232, 227, 218)         # #E8E3DA, the line, ticks and labels
SCALE_LINE_W = 3
SCALE_CASE_W = 1
SCALE_TICK = 9
SCALE_LABEL_PX = 24                  # Montserrat 600 on both orientations (the mono face stays for the rulers)
SCALE_LABEL_WEIGHT = 600
SCALE_LABEL_TRACK = 0.08             # em
SCALE_LABEL_AIR = 8                  # px from the line to the top label's baseline and the bottom label's cap top
SCALE_LABEL_HALO = 0.6
SCALE_ORIGIN_TALL = (89.0, 1030.0)   # the zero tick's x and the line's y, 9:16
SCALE_ORIGIN_WIDE = (140.0, 790.0)   # 16:9 (y above title-safe's foot, 820: the labels stay inside it)
SCALE_SHIFT = 40.0                   # px a bar moves right (or left, mirrored) until it is clear
SCALE_HOME_MAX = 0.20                # the most of the bar's box over the home fill
SCALE_LABEL_CLEAR = 24.0             # px the bar and its labels keep from every other label (round 7: 16)
SCALE_COAST_AIR = 16.0               # px from route legs and coasts
SCALE_DRAW_S = 0.4                   # the line draws from zero over this, labels in over its last 0.15 s
SCALE_BAR_LIFT = 0                   # retired (round 6): the bar no longer rises for a second unit


def _nice_text(value: float) -> str:
    """A scale value as written: 100 -> "100", 1500 -> "1,500", 2.5 -> "2.5"."""
    return f"{value:,.0f}" if abs(value - round(value)) < 1e-9 else f"{value:g}"


def _km_per_deg(lat: float) -> float:
    phi = math.radians(lat)
    return (111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)) / 1000.0


def _nice_below(limit_px: float, px_per_unit: float) -> list[float]:
    """Every nice length whose bar is no longer than `limit_px`, longest first."""
    return [v for v in sorted(SCALE_STEPS, reverse=True) if v * px_per_unit <= limit_px + 1e-9]


def scale_ends_apart(a_px: float, b_px: float) -> bool:
    """Whether two end ticks read as two lengths: SCALE_MIN_SEP_PX apart and SCALE_MIN_SEP_SHARE different."""
    longer = max(a_px, b_px)
    return abs(a_px - b_px) >= SCALE_MIN_SEP_PX and longer > 0 and abs(a_px - b_px) >= SCALE_MIN_SEP_SHARE * longer


@dataclass(frozen=True)
class ScaleUnit:
    """One unit of the scale bar: its nice value, its unit ("KM" or "MI") and the px its end tick is from zero."""
    value: float
    unit: str
    px: float

    @property
    def label(self) -> str:
        return f"{_nice_text(self.value)} {self.unit}"

    def __getitem__(self, key: str) -> float | str:
        """Read access by the field names of the dict it replaced ("value", "unit", "px", "label")."""
        if key not in ("value", "unit", "px", "label"):
            raise KeyError(key)
        got: float | str = getattr(self, key)
        return got


@dataclass(frozen=True)
class ScaleBar:
    """The map's dual-unit scale bar (round 6; round 7, R1): the primary unit (the narration's, KM without one) on top, the
    secondary under it or None (dropped, logged), the bar's length (the longer of the two, px) and the km per degree
    of latitude it was measured at."""
    primary: ScaleUnit
    secondary: ScaleUnit | None
    length: float
    km_per_deg_lat: float

    @property
    def value(self) -> float:
        return self.primary.value

    @property
    def unit(self) -> str:
        return self.primary.unit

    @property
    def px(self) -> float:
        return self.primary.px

    @property
    def label(self) -> str:
        return self.primary.label

    def one_unit(self) -> "ScaleBar":
        """The same bar with its primary unit alone."""
        return ScaleBar(self.primary, None, self.primary.px, self.km_per_deg_lat)

    def __getitem__(self, key: str) -> float | str | ScaleUnit | None:
        """Read access by the keys of the dict it replaced ("value", "unit", "px", "label", "secondary",
        "length", "km_per_deg_lat")."""
        if key not in ("value", "unit", "px", "label", "secondary", "length", "km_per_deg_lat"):
            raise KeyError(key)
        got: float | str | ScaleUnit | None = getattr(self, key)
        return got

    def get(self, key: str, default: ScaleUnit | None = None) -> float | str | ScaleUnit | None:
        got = self[key]
        return default if got is None else got


SCALE_SHORT_MIN_PX = 40.0            # px: the shorter end tick at least this far from zero (a readable length)
SCALE_LABEL_HOLD = 0.6               # and at least this share of its label's width (round 6 review)


def scale_bar(lat: float, px_per_deg_lat: float, narration: str, max_px: float | None = None,
              bar_range: tuple[float, float] = SCALE_RANGE_TALL) -> ScaleBar:
    """The map's dual-unit scale bar (round 7, coordinator R1 and R2). The primary unit is the one the narration
    speaks, KM where it speaks none. The primary is the longest 1-2-5 length (SCALE_STEPS) no longer than the
    range's top (and `max_px`, a route's ceiling ladder) that holds its own label, and the bar is that long (under
    the range's foot where the series has no length in it: logged). The secondary is a tick on the same bar at the
    longest round value (SCALE_SECONDARY_STEPS) that ends at least SCALE_MIN_SEP_PX and SCALE_MIN_SEP_SHARE short of
    the primary's end (scale_ends_apart), at least SCALE_SHORT_MIN_PX from zero, holds its label and is not the
    primary's number ("50 KM" over "50 MI" read as a mistake); none: the primary alone (logged). O(s) over the
    ladders."""
    km_per_deg = _km_per_deg(lat)
    primary_metric = spoken_system(narration) != "imperial"
    px_km = px_per_deg_lat / km_per_deg
    units = {"KM": px_km, "MI": px_km * 1.609344}
    u1, u2 = ("KM", "MI") if primary_metric else ("MI", "KM")
    hi = bar_range[1] if max_px is None else min(bar_range[1], max_px)
    lo = min(bar_range[0], 0.65 * hi)
    font = get_font("Montserrat", SCALE_LABEL_PX, weight=SCALE_LABEL_WEIGHT)

    def holds(v: float, unit: str) -> bool:
        # long enough to read: SCALE_SHORT_MIN_PX, and at least SCALE_LABEL_HOLD of its own label's width (a
        # label wider than its run starts at the zero end instead of passing it: scale_bar_label_specs)
        rel = ink_box((0.0, 0.0), f"{_nice_text(v)} {unit}", font, "ls", font.size * SCALE_LABEL_TRACK)
        need = max(SCALE_SHORT_MIN_PX, SCALE_LABEL_HOLD * (rel[2] - rel[0]) if rel else 0.0)
        return v * units[unit] >= need
    firsts = [v for v in _nice_below(hi, units[u1]) if holds(v, u1)] or _nice_below(hi, units[u1]) or [min(SCALE_STEPS)]
    v1 = firsts[0]
    p1 = v1 * units[u1]
    if p1 < lo - 0.5:
        # coordinator R10: where no 1-2-5 length is in the range, the one nearest it (by ratio), either side; one
        # over the top only where no route ceiling (`max_px`) binds
        up = next((v for v in SCALE_STEPS if v * units[u1] > hi and holds(v, u1)), None)
        if up is not None and max_px is None and up * units[u1] / hi < lo / max(p1, 1e-9):
            v1, p1 = up, up * units[u1]
        log.info("Scale bar: no 1-2-5 length of %s inside %.0f-%.0f px; the nearest, %s at %.0f px.", u1, lo, hi,
                 _nice_text(v1), p1)
    for v2 in sorted(SCALE_SECONDARY_STEPS, reverse=True):     # the longest round tick short enough of the end
        p2 = v2 * units[u2]
        if p2 > p1 or not scale_ends_apart(p1, p2):
            continue
        if abs(v1 - v2) < 1e-9:
            continue                 # never the primary's number
        if not holds(v2, u2):
            break                    # every shorter one is shorter still: no readable secondary
        return ScaleBar(ScaleUnit(v1, u1, round(p1, 1)), ScaleUnit(v2, u2, round(p2, 1)), round(p1, 1),
                        round(km_per_deg, 3))
    log.info("Scale bar: no %s tick reads apart from %s %s (%.0f px); drawn in one unit.", u2, _nice_text(v1), u1, p1)
    return ScaleBar(ScaleUnit(v1, u1, round(p1, 1)), None, round(p1, 1), round(km_per_deg, 3))


ROUTE_BAR_STEPS = (260, 180, 120)  # round 4 review 3: a route's scale bar, longest first, until every stop is named
ROUTE_SCALE_AIR = 24  # round 4 review 3: px a route stop's name keeps from the scale bar's figures (plus the 8 px margin)
# Round 4 review 4 (2026-11-02): the rulers' figures are reference type, a step under the stops' 34 px names: Plex
# Mono Medium at the 30 px tertiary floor (round 6: the map's scale bar labels are SCALE_LABEL_PX Montserrat 600).
SCALE_FIGURE_PX = 30
SCALE_FIGURE_WEIGHT = 500
# Round 4 review 6 (2026-11-03): on 16:9 the 30 px figures (cap 21) stood too close to the 34 px names (cap 24); they
# are 26 px there (over WIDE_TERTIARY_MIN), clearly the reference type.
SCALE_FIGURE_PX_WIDE = 26


def scale_figure_px(width: int, height: int) -> int:
    """The rulers' figures' size for a frame: SCALE_FIGURE_PX on 9:16, SCALE_FIGURE_PX_WIDE on 16:9."""
    return SCALE_FIGURE_PX if height > width else SCALE_FIGURE_PX_WIDE


def scale_range(width: int, height: int) -> tuple[float, float]:
    """The longer bar's px range on a frame (SCALE_RANGE_TALL on 9:16, SCALE_RANGE_WIDE scaled on 16:9)."""
    if height > width:
        return SCALE_RANGE_TALL
    k = min(width / 1920, height / 1080)
    return SCALE_RANGE_WIDE[0] * k, SCALE_RANGE_WIDE[1] * k


def scale_label_font(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> FreeTypeFont:
    k = 1.0 if height > width else min(width / 1920, height / 1080)
    return get_font("Montserrat", max(1, round(SCALE_LABEL_PX * k)), weight=SCALE_LABEL_WEIGHT)


@dataclass(frozen=True)
class ScaleBarLayout:
    """Where a scale bar is drawn (round 6): the zero tick's x, the line's y, its direction (1 runs right from x,
    -1 runs left: the mirrored bottom-right bar keeps its zero on the right), each label's anchor point (its ink's
    right edge on its own end tick) and the box the bar and its labels take."""
    x: float
    y: float
    direction: int
    length: float
    primary_end: float
    secondary_end: float | None
    box: tuple[float, float, float, float]


def scale_bar_label_specs(bar: ScaleBar, x: float, y: float, font: FreeTypeFont,
                          direction: int = 1) -> list[tuple[str, str, tuple[float, float]]]:
    """(name, text, (x, baseline)) of the bar's labels, each placed so its ink's right edge (ink_box) is on its own
    end tick: the primary over the line (its baseline SCALE_LABEL_AIR above it), the secondary under it (its cap top
    SCALE_LABEL_AIR below)."""
    track = font.size * SCALE_LABEL_TRACK
    half = SCALE_LINE_W / 2 + SCALE_CASE_W
    out: list[tuple[str, str, tuple[float, float]]] = []
    rows: list[tuple[str, ScaleUnit, float]] = [("scale", bar.primary, y - half - SCALE_LABEL_AIR)]
    if bar.secondary is not None:
        rows.append(("scale_secondary", bar.secondary, y + half + SCALE_LABEL_AIR + cap_height(font)))
    for name, part, base in rows:
        tick = x + direction * part.px
        rel = ink_box((0.0, 0.0), part.label, font, "ls", track)
        if rel is None:
            continue
        lx = tick - rel[2]                     # its ink's right edge on the tick
        if direction > 0:                      # never left of the zero end (a label wider than its run starts there)
            lx = max(lx, x - rel[0])
        if direction < 0:                      # mirrored: the label stays inside the bar's run (right of its tick)
            lx = tick - rel[0]
        out.append((name, part.label, (lx, base)))
    return out


def scale_bar_box(bar: ScaleBar, x: float, y: float, font: FreeTypeFont, direction: int = 1) -> Box:
    """The box a bar takes with its ticks and labels."""
    track = font.size * SCALE_LABEL_TRACK
    x_end = x + direction * bar.length
    boxes = [(min(x, x_end) - 3, y - SCALE_TICK - 2, max(x, x_end) + 3, y + SCALE_TICK + 2)]
    for _n, text, xy in scale_bar_label_specs(bar, x, y, font, direction):
        bb = ink_box(xy, text, font, "ls", track)
        if bb:
            boxes.append(bb)
    return _union(boxes)


def _scale_bar_plates(rest: Image.Image, bar: ScaleBar, x: float, y: float, px: int | None = None,
                      direction: int = 1) -> list[tuple[str, TextPlate]]:
    """The bar's labels as measured knockout plates (Montserrat 600, SCALE_LINE on a SCALE_LABEL_HALO ink halo,
    raised while the ratio is short of the target)."""
    font = get_font("Montserrat", px if px is not None else SCALE_LABEL_PX, weight=SCALE_LABEL_WEIGHT)
    track = font.size * SCALE_LABEL_TRACK
    return [(name, plan_text(rest, xy, text, font, SCALE_LINE, "ls", track=track, halo_px=21,
                             min_halo=SCALE_LABEL_HALO))
            for name, text, xy in scale_bar_label_specs(bar, x, y, font, direction)]


def _draw_scale_bar(im: Image.Image, bar: ScaleBar, x: float, y: float, ab: float, direction: int = 1) -> None:
    """The bar drawn from its zero tick (`ab` 0-1): one SCALE_LINE line on an INK casing, the shared zero tick up and
    down, the primary's end tick up and the secondary's down, each drawn once the line reaches it."""
    if ab <= 0:
        return
    d = raster.Draw(im, "RGBA")                       # QF-26: antialiased (it grew in whole-pixel steps)
    reach = bar.length * min(1.0, ab)
    x1 = x + direction * reach
    case = SCALE_LINE_W + 2 * SCALE_CASE_W
    ticks = [(x, -SCALE_TICK, SCALE_TICK)]
    ticks.append((x + direction * bar.primary.px, -SCALE_TICK, 0))
    if bar.secondary is not None:
        ticks.append((x + direction * bar.secondary.px, 0, SCALE_TICK))
    shown = [(tx, a, b) for tx, a, b in ticks if abs(tx - x) <= reach + 0.5]
    for tx, a, b in shown:                                      # the casing first, then the cores over it
        d.line([(tx, y + a - SCALE_CASE_W), (tx, y + b + SCALE_CASE_W)], fill=(*brand.INK, 255), width=case)
    d.line([(min(x, x1), y), (max(x, x1), y)], fill=(*brand.INK, 255), width=case)
    for tx, a, b in shown:
        d.line([(tx, y + a), (tx, y + b)], fill=(*SCALE_LINE, 255), width=SCALE_LINE_W)
    d.line([(min(x, x1), y), (max(x, x1), y)], fill=(*SCALE_LINE, 255), width=SCALE_LINE_W)


def _with_bar(rest: Image.Image, bar: ScaleBar, x: float, y: float) -> Image.Image:
    """`rest` with the bar drawn: the ground its labels are planned on (their ticks stand in the ring they are
    measured against, so the halo is solved for them)."""
    im = rest.copy()
    _draw_scale_bar(im, bar, x, y, 1.0)
    return im


def scale_bar_motion(t: float, start: float) -> tuple[float, float]:
    """(line drawn 0-1, labels' alpha 0-1) at `t` for a bar starting at `start`: the line from zero over SCALE_DRAW_S
    (ease-out cubic), the labels fading in over its last 0.15 s."""
    ab = _ease((t - start) / SCALE_DRAW_S)
    al = max(0.0, min(1.0, (t - start - (SCALE_DRAW_S - 0.15)) / 0.15))
    return ab, al


SCALE_RISE_ROWS = 12                 # round 7: rows (SCALE_SHIFT apart) the bar may rise from its origin's line
SCALE_RISE_ROWS_LOCATOR = 20         # round 9: on a locator, as far as the box's top band (SCALE_TOP_BAND)
SCALE_HERO_AIR = 48.0                # round 9: px the bar keeps from the marker and the site's name, always
SCALE_TOP_BAND = 0.25                # and, risen past the marker's foot, only into this top share of the text box
SCALE_NEAR_ROWS = 4                  # round 11: rows within which a locator's bar may sit over the home fill


def place_scale_bar(bar: ScaleBar, width: int, height: int, labels: Sequence[Box],
                    home: BoolArray | None = None, obstacles: BoolArray | None = None,
                    origin: tuple[float, float] | None = None, box_x: tuple[float, float] | None = None,
                    prefer: str = "l", hero: Box | None = None, rise_rows: int = SCALE_RISE_ROWS) -> ScaleBarLayout:
    """The bar's place (round 6; round 7 review 3: in two dimensions). From the bottom-left origin, in SCALE_SHIFT
    steps right, then the same mirrored from the bottom-right (`prefer` "r": the bottom-right first), on the origin's
    line and then up to SCALE_RISE_ROWS rows above it (inside the text box), the first place whose box keeps
    SCALE_LABEL_CLEAR from every label (`labels`), SCALE_COAST_AIR from `obstacles` (a bool array, frame px: route
    legs, coasts, the land a route bar must not sit on) and has no more than SCALE_HOME_MAX of its area over `home`
    (the home fill, frame px). Where none keeps every rule, the place that breaks the fewest (a label first, then
    the obstacles' pixels, then the home fill's excess), logged. Round 9 (review 5): never inside `hero` (the
    marker and the site's name) grown by SCALE_HERO_AIR, not even as the least-bad place (Iguazu's bar rose 280 px
    to sit 40 px over the site's name), and risen past the hero's foot only into the text box's top SCALE_TOP_BAND
    (a corner, never mid-map). Each place is tested in O(1) on summed-area tables, the bar's box measured
    once and moved."""
    import cv2
    import numpy as np
    font = scale_label_font(width, height)
    tall = height > width
    k = 1.0 if tall else min(width / 1920, height / 1080)
    ox, oy = origin if origin is not None else ((SCALE_ORIGIN_TALL if tall else SCALE_ORIGIN_WIDE)[0] * k,
                                                 (SCALE_ORIGIN_TALL if tall else SCALE_ORIGIN_WIDE)[1] * k)
    tb_ = get_text_box(width, height)
    lo_x, hi_x = box_x if box_x is not None else (tb_[0] - 30 * k, tb_[2] + 30 * k)
    if origin is None:          # its labels inside the text box (the secondary's under the line): moved into it
        over = scale_bar_box(bar, ox, oy, font)[3] - tb_[3]
        if over > 0:
            oy -= math.ceil(over)
        lefts = [b_[0] for b_ in (ink_box(xy, t, font, "ls", font.size * SCALE_LABEL_TRACK)
                                  for _n, t, xy in scale_bar_label_specs(bar, ox, oy, font)) if b_]
        if lefts and min(lefts) < tb_[0]:
            ox += math.ceil(tb_[0] - min(lefts))
    home_a = None if home is None else (np.asarray(home) > 127) if np.asarray(home).dtype != bool else np.asarray(home)
    obs_a = None
    if obstacles is not None:
        r = int(SCALE_COAST_AIR)
        obs_a = cv2.dilate((np.asarray(obstacles) > 0).astype(np.uint8),
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0
    ii_obs = _integral(obs_a) if obs_a is not None else None
    ii_home = _integral(home_a) if home_a is not None else None
    b0 = scale_bar_box(bar, ox, oy, font)
    a_ = SCALE_LABEL_CLEAR       # round 6 review: clear of every label by this, not only off it

    def faults(b: Box) -> tuple[int, int, float]:
        hit = sum(1 for lb in labels
                  if not (b[2] + a_ < lb[0] or b[0] - a_ > lb[2] or b[3] + a_ < lb[1] or b[1] - a_ > lb[3]))
        x0, y0, x1, y1 = (max(0, int(b[0])), max(0, int(b[1])), min(width, math.ceil(b[2])), min(height, math.ceil(b[3])))
        if x1 <= x0 or y1 <= y0:
            return hit, 0, 0.0
        obs = _box_sum(ii_obs, (x0, y0, x1, y1)) if ii_obs is not None else 0
        share = _box_sum(ii_home, (x0, y0, x1, y1)) / ((x1 - x0) * (y1 - y0)) if ii_home is not None else 0.0
        return hit, obs, max(0.0, share - SCALE_HOME_MAX)

    def tries() -> Iterator[tuple[float, float, Box]]:
        start_r = ox + (hi_x - (b0[0] - lo_x)) - b0[2]   # mirrored: its box as far inside the right edge
        order = ((ox, SCALE_SHIFT), (start_r, -SCALE_SHIFT))
        for row in range(rise_rows + 1):
            dy = -SCALE_SHIFT * k * row
            if b0[1] + dy < tb_[1]:
                break
            for start, step in (order if prefer == "l" else order[::-1]):
                for j in range(60):
                    x = start + step * j
                    b = (b0[0] + x - ox, b0[1] + dy, b0[2] + x - ox, b0[3] + dy)
                    if b[0] < lo_x - 0.5 or b[2] > hi_x + 0.5:
                        if j:
                            break
                        continue
                    yield x, oy + dy, b
    ha = SCALE_HERO_AIR * k
    hero_g = None if hero is None else (hero[0] - ha, hero[1] - ha, hero[2] + ha, hero[3] + ha)
    best: tuple[tuple[int, int, int, float, int], float, float, Box] | None = None
    near: tuple[float, float, float, Box] | None = None     # round 11: the near rows' least home cover, else clear
    near_dy = SCALE_NEAR_ROWS * SCALE_SHIFT * k
    for i, (x, y, b) in enumerate(tries()):
        if hero_g is not None and (LayoutConstraintSolver.boxes_intersect(b, hero_g, 0) or (
                b[3] < hero[3] and b[1] > tb_[1] + SCALE_TOP_BAND * (tb_[3] - tb_[1]))):
            continue    # by the hero, or risen over its foot short of the box's top band (mid-map)
        f = faults(b)
        if f == (0, 0, 0.0) and near is not None and oy - y > near_dy + 0.5:
            # round 12 (review: Eureka's bar rose 520 px to a clear place): a near-row place over the home fill
            # alone outranks a clear one further up, not only one that breaks a rule
            _e, x_, y_, b_ = near
            log.info("Scale bar: kept within %.0f px of its line over the home fill (%.0f %% past its limit) rather "
                     "than raised %.0f px to a clear place.", oy - y_, 100 * _e, oy - y)
            return _scale_layout(bar, x_, y_, 1, b_)
        if f == (0, 0, 0.0):
            if i and y != oy:
                log.info("Scale bar: raised %.0f px off its line to keep clear.", oy - y)
            return _scale_layout(bar, x, y, 1, b)
        # round 11 (review: Burana's bar rose 520 px, Vostok's 600, to float in the top corner among the
        # neighbours' names): on a locator (`hero` given) a place within SCALE_NEAR_ROWS of the line that breaks
        # only the home-fill limit (the bar is cased and its labels haloed) is kept, the least cover first
        if hero is not None and f[0] == 0 and f[1] == 0 and oy - y <= near_dy + 0.5:
            if near is None or f[2] < near[0] - 1e-9:
                near = (f[2], x, y, b)
        elif near is not None and oy - y > near_dy + 0.5:
            _e, x_, y_, b_ = near
            log.info("Scale bar: kept within %.0f px of its line over the home fill (%.0f %% past its limit) rather "
                     "than raised further.", oy - y_, 100 * _e)
            return _scale_layout(bar, x_, y_, 1, b_)
        key = (f[0], int(f[1] > 0), f[1], f[2], i)
        if best is None or key < best[0]:
            best = (key, x, y, b)
    if best is None:
        if hero is None:
            log.info("Scale bar: no place in the box; set at its origin.")
        else:
            log.warning("Scale bar: no place in the box clear of the marker and the site's name; set at its origin.")
        return _scale_layout(bar, ox, oy, 1, b0)
    (hit, _o, obs, excess, _i), x, y, b = best
    log.info("Scale bar: no clear place; set where it breaks the least (%d label(s), %d obstacle px, %.0f %% over "
             "the home fill's limit).", hit, obs, 100 * excess)
    return _scale_layout(bar, x, y, 1, b)


def _scale_layout(bar: ScaleBar, x: float, y: float, direction: int, box: Box) -> ScaleBarLayout:
    sec = bar.secondary
    return ScaleBarLayout(x=x, y=y, direction=direction, length=bar.length, primary_end=x + direction * bar.primary.px,
                          secondary_end=None if sec is None else x + direction * sec.px, box=box)


def _label_box(im: Image.Image, cx: int, y: int, title: str, sub: str, alpha: float) -> Image.Image:
    """Place-name label: Anton title over a gold Montserrat line, on a soft dark pill."""
    if alpha <= 0:
        return im
    draw = ImageDraw.Draw(im, "RGBA")
    max_w = centred_text_width(im.width, im.height) - 70              # the pill stays inside the Reel box
    f1 = fit_font(draw, title.upper(), "Anton", 66, max_w, 40)
    f2 = fit_font(draw, sub.upper(), "Montserrat", 30, max_w, 22)
    w1 = draw.textlength(title.upper(), font=f1)
    w2 = draw.textlength(sub.upper(), font=f2)
    bw = int(min(max(w1, w2), max_w) + 70)
    d = ImageDraw.Draw(im, "RGBA")  # RGB frame + RGBA drawing = real alpha blending (fades work)
    a = int(255 * alpha)
    d.rounded_rectangle([cx - bw // 2, y - 62, cx + bw // 2, y + 66], radius=18, fill=(10, 10, 12, int(200 * alpha)),
                        outline=(*accent(), int(220 * alpha)), width=2)
    _text(d, (cx, y - 18), title.upper(), font=f1, fill=(*WHITE, a), anchor="mm")
    _text(d, (cx, y + 38), sub.upper(), font=f2, fill=(*accent(), a), anchor="mm")
    return im


# ---------------------------------------------------------------------- map
# Land a step lighter than it was (74, 76, 82 / 122, 100, 66): after the film grade the land and the sea were close
# in value (visual critic round 4, 2026-10-29); home stays warm but not a flood of gold (big countries).
LAND_TINT, HOME_TINT = (88, 92, 100), (168, 137, 81)   # round 6 review: home a step brighter (150, 122, 72),
# so INK set in it measures 4.5:1 with margin (its relief now only darkens it)
# Round 3 (2026-11-02): neighbours' names and borders on the nearest brand token (an untokenised (150, 152, 160)
# grey before), the coastal glow's strength on TEAL.
MAP_NAME, MAP_BORDER, MAP_GLOW = brand.STONE_LT, brand.STONE, 0.38   # round 6: names STONE_LT (STONE before)
# Round 7 (coordinator R4): a neighbour's name in the country name's Montserrat, letter-spaced, a weight under it and
# no larger, in STONE_LT (MAP_NAME), so a neighbour ("UKRAINE") never outranks the subject (36 px 700 before)
MAP_NAME_PX, MAP_NAME_PX_WIDE = 24, 26
MAP_NAME_WEIGHT = 500
MAP_HOME_NAME_WEIGHT = 600   # a route's own country named in the base: the country name's weight
MAP_NAME_HALO = 0.75  # the depth of its ink halo


def camera_spans(n: int, wide: float, close: float, travel: float, fps: int) -> list[float]:
    """The locator's span (degrees across the frame) on each of `n` frames (round 3, 2026-11-02): eased in and
    out in log space over `travel` seconds, so each halving of the view takes the same time (a cubic ease-out on
    the linear span spent most of the move in the first half-second), then held."""
    lw, lc = math.log(wide), math.log(close)
    return [math.exp(lw + (lc - lw) * _ease_io(i / fps / travel)) for i in range(n)]


GLOBE_PX = 200            # round 6: the context globe inset's diameter at 1080 wide, 9:16 (160 before)
GLOBE_PX_WIDE = 180       # round 6: and on 16:9, design px
GLOBE_FADE = 0.4          # s: it fades in over the clip's first 0.4 s
GLOBE_OUT_S = 0.3         # round 6: and fades out over this, from GLOBE_OUT (gone before the pin lands)
GLOBE_SPIN = 8.0          # degrees it turns into the site's longitude
GLOBE_SPIN_S = 1.2        # over the clip's first 1.2 s
GLOBE_TILT_MAX = 35.0     # degrees: the tilt toward the site's latitude, clamped
GLOBE_LAND = (176, 170, 160)   # round 6: the other land, STONE lightened
GLOBE_SHADOW = 14         # px: its soft INK shadow
GLOBE_RING = 3            # px: the INK ring outside its 2 px GOLD_DIM limb
GLOBE_FOOTPRINT_MIN = 36  # px: the close-up's footprint is outlined only when at least this big on the globe
GLOBE_MAP_AIR = 24        # px it keeps from every plate, label and the home fill on a locator
GLOBE_SITE_D = 12         # px: the site's GOLD dot on the globe
GLOBE_SITE_RING = 22      # px: and the 2 px INK ring round it, the home fill showing between
GLOBE_LIMB_DARK = 0.25    # the limb darkening at the rim
# Round 6 review: the 2 px limb. GOLD_DIM (the spec's) measured 2.4-2.5:1 on the grey neighbours' land under the
# globe's shadow (the 3:1 non-text floor, spec B); GOLD's measures 3.5:1 or more on land and sea alike.
GLOBE_LIMB = brand.GOLD
MAP_LEADER_MAX = 48       # round 6: px, the longest leader from a site label to its dot (60 before)
MAP_LEADER_MIN = 24       # and the shortest


def globe_box(width: int, height: int, corner: str = "tr", shrink: float = 1.0) -> tuple[int, int, int, int]:
    """Where the context globe sits (round 6): 9:16 in the Reel box's top-right corner (x 688-888, its top on the
    text box's, y 330: the graphics box's 288 is above it); 16:9 its right edge on title-safe's (1766), its top at
    130 (clear of YouTube's timestamp corner, bottom-right). `corner` "tl" mirrors it, "br" and "bl" set it low (over
    the scale bar's row); `shrink` draws it smaller, from the same corner."""
    if width > height:
        k = min(width / 1920, height / 1080)
        size = round(GLOBE_PX_WIDE * k * shrink)
        right, left = round(WIDE_TITLE_SAFE[2] * k), round(WIDE_TITLE_SAFE[0] * k)
        top, foot = round(130 * k), round((WIDE_TITLE_SAFE[3] - 110) * k)
    else:
        k = width / VIDEO_WIDTH
        size = round(GLOBE_PX * k * shrink)
        bx0, _by0, bx1, _by1 = safe_zones.graphics_box(width, height)
        right, left = bx1, bx0
        top, foot = TEXT_TOP * height // VIDEO_HEIGHT, round((TEXT_BOTTOM - 110) * height / VIDEO_HEIGHT)
    x0 = left if corner in ("tl", "bl") else right - size
    y0 = top if corner in ("tr", "tl") else foot - size
    return x0, y0, x0 + size, y0 + size


def globe_centre(lon0: float, lat0: float, t: float | None = None) -> tuple[float, float]:
    """The globe's centre at `t` (None: at rest): on the site's longitude, the tilt clamped to GLOBE_TILT_MAX, the
    last GLOBE_SPIN degrees turned into the site over GLOBE_SPIN_S (ease-out)."""
    lat = max(-GLOBE_TILT_MAX, min(GLOBE_TILT_MAX, lat0))
    if t is None:
        return lon0, lat
    return lon0 - GLOBE_SPIN * (1 - _ease(t / GLOBE_SPIN_S)), lat


def globe_inset(size: int, lon0: float, lat0: float, home: geo.Country | None,
                footprint: Box | Sequence[tuple[float, float]],
                site: tuple[float, float] | None = None) -> Image.Image:
    """The context globe (round 6): an orthographic globe centred on (lon0, lat0), INK ocean, other land GLOBE_LAND,
    the home country solid GOLD, radial limb darkening (25 % at the rim), a 2 px GOLD_DIM limb, a GLOBE_RING px INK
    ring and a GLOBE_SHADOW px soft INK shadow at 45 %; the site (`site`, default the centre) a 12 px GOLD dot in a
    22 px INK ring, and the close-up's footprint (lon0, lat0, lon1, lat1) outlined only when it is at least
    GLOBE_FOOTPRINT_MIN px across. An RGBA image GLOBE_SHADOW px wider than `size` each side (the disc is `size`)."""
    import numpy as np
    ss = 3
    pad = GLOBE_SHADOW
    full = (size + 2 * pad) * ss
    r = size * ss / 2 - GLOBE_RING * ss
    cx = cy = full / 2
    im = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def px(x: float, y: float) -> tuple[float, float]:
        return cx + x * r, cy - y * r
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*brand.INK, 255))
    if geo.available():
        matched = False
        for c, rings in geo.globe_rings(lon0, lat0):
            mine = geo.same_entity(c, home)      # round 15: the 1:50m form of the 1:10m home is the same entity
            matched |= mine
            fill = brand.GOLD if mine else GLOBE_LAND
            for ring in rings:
                d.polygon([px(x, y) for x, y in ring], fill=(*fill, 255))
        if home is not None and not matched:     # a territory the 1:50m data has no country for (Tokelau)
            for poly in home["polygons"]:
                pts = [geo.orthographic(x, y, lon0, lat0) for x, y in poly[0][::4] or poly[0]]
                vis = [px(x, y) for x, y, v in pts if v]
                if len(vis) >= 3:
                    d.polygon(vis, fill=(*brand.GOLD, 255))
    edge: list[tuple[float, float]] = []
    if footprint and isinstance(footprint[0], tuple):    # round 11: a polar view's outline, as (lon, lat) points
        edge = [(float(p[0]), float(p[1])) for p in footprint]   # type: ignore[index]
    else:
        lo0, la0, lo1, la1 = footprint                           # type: ignore[misc]
        for f in range(9):
            edge.append((lo0 + (lo1 - lo0) * f / 8, la1))
        for f in range(9):
            edge.append((lo1, la1 + (la0 - la1) * f / 8))
        for f in range(9):
            edge.append((lo1 + (lo0 - lo1) * f / 8, la0))
        for f in range(9):
            edge.append((lo0, la0 + (la1 - la0) * f / 8))
    pts = [px(x, y) for x, y, v in (geo.orthographic(lo, la, lon0, lat0) for lo, la in edge) if v]
    if len(pts) > 2:
        xs_, ys_ = [p[0] for p in pts], [p[1] for p in pts]
        if min(max(xs_) - min(xs_), max(ys_) - min(ys_)) / ss >= GLOBE_FOOTPRINT_MIN:
            d.line(pts + [pts[0]], fill=(*brand.GOLD, 255), width=2 * ss, joint="curve")
    # limb darkening, 25 % at the rim: a sphere's (1 - mu) law (mu the cosine to the view), the light a touch up
    # and left, so the darkening gathers at the limb and the disc reads round (a radial r^2 ramp read flat)
    arr = np.asarray(im).astype(np.float32)
    yy, xx = np.mgrid[0:full, 0:full]
    lx, ly = cx - 0.18 * r, cy - 0.22 * r
    rho = np.clip(np.hypot(xx - lx + 0.5, yy - ly + 0.5) / (1.25 * r), 0, 1)
    fall = 1 - np.sqrt(1 - rho ** 2)
    rim = (np.abs(np.hypot(xx - cx + 0.5, yy - cy + 0.5) - 0.985 * r) < 0.015 * r + 1)
    fall *= GLOBE_LIMB_DARK / max(1e-6, float(fall[rim].mean()))   # 25 % at the rim, on average round it
    arr[..., :3] *= np.clip(1 - fall, 0.55, 1.0)[..., None]
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    d = ImageDraw.Draw(im)
    # the site: a GLOBE_SITE_D GOLD dot (an INK keyline round it) in a 2 px INK ring GLOBE_SITE_RING across, the home
    # country seen between them (the solid 22 px ink disc before covered Jordan and bit peninsular India)
    sx, sy = px(*(geo.orthographic(site[0], site[1], lon0, lat0)[:2])) if site else (cx, cy)
    ro_, ri_ = GLOBE_SITE_RING / 2 * ss, (GLOBE_SITE_RING / 2 - 2) * ss
    d.ellipse([sx - ro_, sy - ro_, sx + ro_, sy + ro_], outline=(*brand.INK, 255), width=round(ro_ - ri_))
    rd_ = GLOBE_SITE_D / 2 * ss
    d.ellipse([sx - rd_ - ss, sy - rd_ - ss, sx + rd_ + ss, sy + rd_ + ss], fill=(*brand.INK, 255))
    d.ellipse([sx - rd_, sy - rd_, sx + rd_, sy + rd_], fill=(*brand.GOLD, 255))
    mask = Image.new("L", (full, full), 0)
    ImageDraw.Draw(mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    disc = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    disc.paste(im, (0, 0), mask)
    dd = ImageDraw.Draw(disc)
    ro = r + GLOBE_RING * ss
    dd.ellipse([cx - ro, cy - ro, cx + ro, cy + ro], outline=(*brand.INK, 255), width=GLOBE_RING * ss + ss)
    dd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*GLOBE_LIMB, 255), width=2 * ss)
    shadow = Image.new("L", (full, full), 0)
    ImageDraw.Draw(shadow).ellipse([cx - ro, cy - ro + 2 * ss, cx + ro, cy + ro + 2 * ss], fill=int(255 * 0.45))
    shadow = shadow.filter(ImageFilter.GaussianBlur(GLOBE_SHADOW * ss / 2.5))
    out = Image.new("RGBA", (full, full), (*brand.INK, 0))
    out.putalpha(shadow)
    out.alpha_composite(disc)
    return out.resize((size + 2 * pad, size + 2 * pad), Image.LANCZOS)




def globe_alpha(t: float, out_at: float | None = None) -> float:
    """The globe's opacity at `t`: in over GLOBE_FADE, held, out over GLOBE_OUT_S from `out_at` (GLOBE_OUT on a
    locator; a route passes the time its first leg starts drawing)."""
    out_at = GLOBE_OUT if out_at is None else out_at
    return _ease(t / GLOBE_FADE) * (1 - _ease((t - out_at) / GLOBE_OUT_S))


def _draw_globe(im: Image.Image, globe: Image.Image | None, box: tuple[int, int, int, int], t: float,
                out_at: float | None = None) -> Image.Image:
    """The context globe composited onto `im` (RGB, changed in place and returned) at its opacity by `t`
    (globe_alpha). Round 4 review 6 (2026-11-03): only the globe's own box (with its shadow's pad) is composited."""
    a = globe_alpha(t, out_at)
    if globe is None or a <= 0.002:
        return im
    g = globe if a >= 0.999 else globe.copy()
    if a < 0.999:
        g.putalpha(globe.getchannel("A").point(lambda v, a=a: int(v * a)))
    pad = (g.width - (box[2] - box[0])) // 2
    x0, y0 = box[0] - pad, box[1] - pad
    cx0, cy0 = max(0, x0), max(0, y0)
    region = im.crop((cx0, cy0, min(im.width, x0 + g.width), min(im.height, y0 + g.height))).convert("RGBA")
    region.alpha_composite(g.crop((cx0 - x0, cy0 - y0, cx0 - x0 + region.width, cy0 - y0 + region.height)))
    im.paste(region.convert(im.mode), (cx0, cy0))
    return im


def draw_plate(im: Image.Image, plate: Any, alpha: float = 1.0, dy: int = 0) -> Image.Image:
    """typography.TextPlate.draw composited in the plate's own box only (round 4 review 6, 2026-11-03: draw turns
    the whole frame to RGBA and back for each plate, the larger part of a route frame's cost): the same pixels.
    `im` (RGB) is changed in place and returned."""
    if getattr(plate, "empty", True) or alpha <= 0 or im.mode != "RGB" or plate.glyphs is None:
        return plate.draw(im, alpha, dy)
    h, w = plate.glyphs.shape
    x0, y0 = plate.origin[0], plate.origin[1] + int(dy)
    region = im.crop((x0, y0, x0 + w, y0 + h)).convert("RGBA")
    im.paste(plate._layer(region, plate.halo, alpha).convert("RGB"), (x0, y0))
    return im


def globe_marks(globe: Image.Image | None, box: tuple[int, int, int, int], size: tuple[int, int]) -> list:
    """The globe's disc (its limb and ring, not its shadow) as a non-text mark mask on the frame."""
    if globe is None:
        return []
    m = Image.new("L", size, 0)
    pad = (globe.width - (box[2] - box[0])) // 2
    m.paste(globe.getchannel("A").point(lambda v: 255 if v > 127 else 0), (box[0] - pad, box[1] - pad))
    return [("globe", m)]


MAP_TRAVEL = 2.2          # s: the camera's move from the wide view to the close one
MAP_PIN_AT = MAP_TRAVEL + 0.1   # the pin drops once the camera rests
GLOBE_OUT = MAP_PIN_AT - 0.2    # round 6: the globe fades out from here (gone by 2.4 s, before the pin lands)


@lru_cache(maxsize=1)
def _relief() -> Image.Image | None:
    """Natural Earth shaded relief (21600x10800, public domain), loaded once."""
    if not geo.RELIEF_FILE.exists():
        return None
    # Our own trusted 233 MP raster, decoded under its own limit (the process-wide one stays for downloads)
    with open_trusted(geo.RELIEF_FILE, geo.RELIEF_MAX_PIXELS, formats=("TIFF",)) as im:
        return im.convert("L")


# Round 6 (2026-11-03): the relief inside the home fill only darkens, in a narrow band ([RELIEF_HOME_FLOOR, 1.00] of
# the fill, the hillshade blurred RELIEF_BLUR px first), and fades to RELIEF_NEAR_AMP of that within RELIEF_NEAR_PX
# of the marker and under the label plates (feathered RELIEF_FEATHER px): the fill's luminance spread fell from 17-26
# to 6-9. Neighbours' land is shaded 0.45 + 0.75 x the hillshade (0.30 + 0.95 before). The home coast is a 1.5 px
# GOLD_LIGHT inner edge inside a 2 px INK line (the teal stroke and the sea's teal glow are gone); an island under
# RELIEF_ISLAND_PX2 on screen takes a 1 px edge, and one under RELIEF_ISLAND_MIN px across is not drawn.
RELIEF_HOME_FLOOR = 0.86
RELIEF_BLUR_TALL, RELIEF_BLUR_WIDE = 1.2, 1.6
RELIEF_NEAR_PX, RELIEF_NEAR_AMP, RELIEF_FEATHER = 120.0, 0.15, 60.0
RELIEF_NEIGHBOUR = (0.45, 0.75)
RELIEF_ISLAND_PX2 = 1500.0
RELIEF_ISLAND_MIN = 6.0
HOME_EDGE_W, HOME_LINE_W = 1.5, 2.0
HOME_EDGE_ALPHA = 0.85
RELIEF_STD_TARGET = 7.5         # the home fill's luminance spread aimed for (6 to 9)


@dataclass(frozen=True)
class ReliefContext:
    """What a render tells relief_base beyond its (unchanged) signature: base px per frame px on the resting view,
    the marker's point and the label plates' boxes (base px) the relief fades under, and the orientation."""
    base_per_px: float
    marker: tuple[float, float] | None = None
    plates: tuple[tuple[float, float, float, float], ...] = ()
    wide: bool = False


import contextvars as _contextvars

_RELIEF_CTX: "_contextvars.ContextVar[ReliefContext | None]" = _contextvars.ContextVar("relief_ctx", default=None)


def _soft_floor(x: "npt.NDArray[np.float32]", floor: float) -> "npt.NDArray[np.float32]":
    """A smooth map of the relief's deviation `x` (0 at the fill's median) into (floor, 1.0): a logistic, so the
    darkest relief eases into the floor and the brightest into 1.0 with no clipped edge (a hard clip at 1.0 left the
    highlights as flat-edged dark blotches round them, like camouflage: Jordan, round 6 review). Never over 1.0."""
    import numpy as np
    out: npt.NDArray[np.float32] = (floor + (1.0 - floor) / (1.0 + np.exp(-4.0 * x - 1.2))).astype(np.float32)
    return out


def relief_multiplier(hill: "npt.NDArray[np.float32]", home: "npt.NDArray[np.float32] | None",
                      ctx: ReliefContext | None) -> "npt.NDArray[np.float32]":
    """The home fill's relief multiplier (an H x W float array, never over 1.0): the hillshade blurred, eased
    about its median over the fill into [RELIEF_HOME_FLOOR, 1.0] (_soft_floor: it only darkens, and smoothly), its
    gain solved for RELIEF_STD_TARGET of luminance spread, and its amplitude faded to RELIEF_NEAR_AMP near the marker
    and under the plates (a smoothstep over RELIEF_FEATHER, the quiet area itself blurred, so neither the disc round
    the marker nor a plate's rectangle shows as a shape)."""
    import cv2
    import numpy as np
    bpp = ctx.base_per_px if ctx else 1.0
    sigma = (RELIEF_BLUR_WIDE if ctx and ctx.wide else RELIEF_BLUR_TALL) * bpp
    hb = cv2.GaussianBlur(hill.astype(np.float32), (0, 0), sigma)
    sel = hb[home > 0.5] if home is not None and (home > 0.5).any() else hb.ravel()
    med = float(np.median(sel)) if sel.size else 0.75
    sub = sel[:: max(1, sel.size // 20000)] if sel.size else np.array([med], np.float32)
    luma = 0.2126 * HOME_TINT[0] + 0.7152 * HOME_TINT[1] + 0.0722 * HOME_TINT[2]

    def spread(g: float) -> float:
        return float(np.std(_soft_floor(g * (sub - med), RELIEF_HOME_FLOOR))) * luma
    gain = min((0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24), key=lambda g: abs(spread(g) - RELIEF_STD_TARGET))
    m = _soft_floor(gain * (hb - med), RELIEF_HOME_FLOOR)
    if ctx and (ctx.marker or ctx.plates):
        h, w = m.shape
        quiet = np.zeros((h, w), np.uint8)
        if ctx.marker:
            cv2.circle(quiet, (int(ctx.marker[0]), int(ctx.marker[1])), int(RELIEF_NEAR_PX * bpp), 255, -1)
        for x0, y0, x1, y1 in ctx.plates:
            cv2.rectangle(quiet, (int(x0), int(y0)), (int(x1), int(y1)), 255, -1)
        dist = cv2.distanceTransform((quiet == 0).astype(np.uint8), cv2.DIST_L2, 5)
        u = np.clip(1 - dist / (RELIEF_FEATHER * bpp), 0, 1)
        near = u * u * (3 - 2 * u)                                  # smoothstep: no crease at either edge
        near = cv2.GaussianBlur(near.astype(np.float32), (0, 0), RELIEF_FEATHER * bpp / 3)
        m = 1 + (m - 1) * (1 - (1 - RELIEF_NEAR_AMP) * near)
    return m


def _relief_crop(rel: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """`rel` (the world's relief, equirectangular) cut to `box` (its px), wrapped across the antimeridian and
    mirrored past a pole (round 8, review 6: the polar view's relief was black beyond 180 degrees and the pole line):
    a plain crop where the box is inside it."""
    x0, y0, x1, y1 = box
    w_, h_ = rel.size
    if 0 <= x0 and x1 <= w_ and 0 <= y0 and y1 <= h_:
        return rel.crop(box)
    out = Image.new(rel.mode, (max(1, x1 - x0), max(1, y1 - y0)))

    def cols(xa: int, ra: int, rb: int) -> Image.Image:
        strip = Image.new(rel.mode, (max(1, x1 - x0), max(1, rb - ra)))
        x = xa
        while x < xa + (x1 - x0):
            xm = x % w_
            take = min(xa + (x1 - x0) - x, w_ - xm)
            strip.paste(rel.crop((xm, ra, xm + take, rb)), (x - xa, 0))
            x += take
        return strip
    segs = ((y0, min(y1, 0), "top"), (max(y0, 0), min(y1, h_), "mid"), (max(y0, h_), y1, "bottom"))
    for ya, yb, kind in segs:
        if yb <= ya:
            continue
        if kind == "mid":
            out.paste(cols(x0, ya, yb), (0, ya - y0))
            continue
        ra, rb = (-yb, -ya) if kind == "top" else (2 * h_ - yb, 2 * h_ - ya)
        ra, rb = max(0, ra), min(h_, rb)
        if rb > ra:
            out.paste(cols(x0 + w_ // 2, ra, rb).transpose(Image.Transpose.FLIP_TOP_BOTTOM), (0, ya - y0))
    return out


RELIEF_POLAR = (60.0, 82.0)     # round 9 (review 9): latitudes over which the relief fades to its own soft mean


def _polar_relief(hill: "npt.NDArray[np.float32]", lat_top: float, lat_span: float) -> "npt.NDArray[np.float32]":
    """Round 9 (review 9): the hillshade toward the poles, where the equirectangular raster's rows are stretched
    1 / cos(lat) wide (horizontal streaks at the South Pole, blotchy steps at Port Lockroy): each row blended
    toward a broad blur of it, fully past RELIEF_POLAR[1], not at all short of RELIEF_POLAR[0]. Unchanged where
    no row reaches RELIEF_POLAR[0]."""
    import cv2
    import numpy as np
    h = hill.shape[0]
    lats = np.abs(lat_top - (np.arange(h, dtype=np.float32) + 0.5) / h * lat_span)
    w = np.clip((RELIEF_POLAR[1] - lats) / (RELIEF_POLAR[1] - RELIEF_POLAR[0]), 0.0, 1.0)
    if float(w.min()) >= 1.0:
        return hill
    soft = cv2.GaussianBlur(hill, (0, 0), max(2.0, hill.shape[1] / 120))
    out: npt.NDArray[np.float32] = hill * w[:, None] + soft * (1 - w[:, None])
    return out


def _polar_hill(rel: Image.Image, proj: "PolarProjection", bw: int, bh: int) -> "npt.NDArray[np.float32]":
    """Round 11 (coordinator directive 2): the hillshade (`rel`, the world's equirectangular relief) resampled onto
    the polar view `proj` over bw x bh px: only the latitude band the view's cap reaches is read, every
    longitude wrapped, in strips of rows (memory bounded by the strip); the rows toward the pole softened as
    _polar_relief does (the raster's own rows there are stretched 1 / cos(lat) wide)."""
    import cv2
    import numpy as np
    w_, h_ = rel.size
    r = proj.radius(bw, bh)
    lat_hi, lat_lo = min(90.0, proj.lat0 + r), max(-90.0, proj.lat0 - r)
    y0 = max(0, int((90 - lat_hi) / 180 * h_) - 2)
    y1 = min(h_, math.ceil((90 - lat_lo) / 180 * h_) + 2)
    src = np.asarray(rel.crop((0, y0, w_, y1)))
    hill = np.empty((bh, bw), np.float32)
    lat_abs = np.empty((bh, bw), np.float32)
    xs = np.arange(bw, dtype=np.float64) + 0.5
    for r0 in range(0, bh, 256):
        r1 = min(bh, r0 + 256)
        lon, lat = proj.inverse_grid(xs, np.arange(r0, r1, dtype=np.float64) + 0.5)
        mx = ((lon + 180.0) / 360.0 * w_ - 0.5).astype(np.float32)
        my = np.clip((90.0 - lat) / 180.0 * h_ - 0.5 - y0, 0, src.shape[0] - 1).astype(np.float32)
        hill[r0:r1] = cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP).astype(np.float32) / 255.0
        lat_abs[r0:r1] = np.abs(lat)
    wgt = np.clip((RELIEF_POLAR[1] - lat_abs) / (RELIEF_POLAR[1] - RELIEF_POLAR[0]), 0.0, 1.0)
    if float(wgt.min()) < 1.0:
        soft = cv2.GaussianBlur(hill, (0, 0), max(2.0, bw / 120))
        hill = hill * wgt + soft * (1 - wgt)
    return hill


RELIEF_MAX_MAG = 6.0            # round 15: frame px one relief pixel may span before its shading is eased
RELIEF_COARSE_FLOOR = 0.15      # and the least share of its contrast it keeps


def relief_base(lat0: float, lon0: float, bw: int, bh: int, kx: float, coslat: float, home, project,
                final_view: tuple[float, float, float, float], label_bottom_fraction: float = 1.0,
                reserved: tuple[tuple[float, float, float, float], ...] = (),
                final_safe: tuple[float, float, float, float] | None = None,
                transient: list[dict[str, Any]] | None = None, name_px: int = 66,
                skip: frozenset[str] | set[str] = frozenset(), name_home: bool = False,
                named: list[str] | None = None, polar: "PolarProjection | None" = None,
                named_boxes: list[tuple[str, tuple[float, float, float, float]]] | None = None) -> Image.Image:
    """The map background: hillshaded land (the topic's country in warm gold, its relief quiet: relief_multiplier),
    an ink sea, thin borders, and neighbouring countries named at Natural Earth's label points.
    final_safe: the safe box of the last frame, in base pixels; a name drawn into the base must lie in it.
    transient: given, the names outside the close-up are not drawn into the base but added to it
    ({"name", "sprite": an L mask, "box": its base box}) for the frames to draw while wholly visible;
    the names drawn into the base are added with "sprite": None. A render's ReliefContext (_RELIEF_CTX) gives the
    base's scale on the frame and the marker and plates the home relief fades under."""
    import cv2
    import numpy as np
    ctx = _RELIEF_CTX.get()
    bpp = ctx.base_per_px if ctx else 1.0
    lon_span, lat_span = bw / (kx * coslat), bh / kx
    lon_min, lat_max = lon0 - lon_span / 2, lat0 + lat_span / 2
    rel = _relief()
    if rel is not None and polar is not None:     # round 11: the polar view's relief, resampled onto it
        hill = _polar_hill(rel, polar, bw, bh)
    elif rel is not None:
        px = lambda lon: (lon + 180) / 360 * rel.width
        py = lambda lat: (90 - lat) / 180 * rel.height
        box = (int(px(lon_min)), int(py(lat_max)), int(px(lon_min + lon_span)), int(py(lat_max - lat_span)))
        hill = np.asarray(_relief_crop(rel, box).resize((bw, bh), Image.BICUBIC), dtype=np.float32) / 255.0
        hill = _polar_relief(hill, lat_max, lat_span)
        # round 15 (review: Monaco's and Gibraltar's relief a blur of 40 px squares): where one relief pixel is
        # drawn wider than RELIEF_MAX_MAG frame px, its shading eased toward the mean (no made-up texture)
        mag = bw / max(1, box[2] - box[0]) / max(1e-6, bpp)
        if mag > RELIEF_MAX_MAG:
            w_ = max(RELIEF_COARSE_FLOOR, RELIEF_MAX_MAG / mag)
            hill = float(hill.mean()) + (hill - float(hill.mean())) * w_
    else:
        hill = np.full((bh, bw), 0.75, dtype=np.float32)

    lon_half, lat_half = lon_span / 2 + 2, lat_span / 2 + 2
    land_m, home_m, home_big = Image.new("L", (bw, bh), 0), Image.new("L", (bw, bh), 0), Image.new("L", (bw, bh), 0)
    # QF-26: the land, the home's fill and its outline's source antialiased, each filled as one shape from the
    # projected rings exactly (raster.Rings: no seam where two polygons meet, holes taken away)
    dl, dh, dhb = raster.Rings(), raster.Rings(), raster.Rings()
    pinholes: list[list[tuple[float, float]]] = []     # the home's lochs the half cut below would close
    visible = []
    # id(country): (projected ring or, cut at a seam, an open run of it, small, closed)
    drawn: dict[int, list[tuple[list[tuple[float, float]], bool, bool]]] = {}
    seen: set[int] = set()
    # round 8 (review 6, 8): the polygons across the antimeridian and past a pole as well, the enclaves last
    polys = _view_polygons(polar, bw, bh) if polar is not None else geo.view_polygons(lon0, lat0, lon_half, lat_half)
    for c, poly in polys:
        ring = poly[0]
        pts = [project(x, y) for x, y in ring]
        xs_, ys_ = [p[0] for p in pts], [p[1] for p in pts]
        if max(max(xs_) - min(xs_), max(ys_) - min(ys_)) / bpp < RELIEF_ISLAND_MIN:
            if c is home:                             # round 7 (R6): the home's island at its least extent
                cx_, cy_ = (max(xs_) + min(xs_)) / 2, (max(ys_) + min(ys_)) / 2
                r_ = RELIEF_ISLAND_MIN / 2 * bpp
                for dd in (dl, dh):
                    dd.oval([cx_ - r_, cy_ - r_, cx_ + r_, cy_ + r_])
                if id(c) not in seen:
                    seen.add(id(c))
                    visible.append(c)
            continue                                  # round 6: under 6 px across on screen: not drawn
        small = geo.ring_area(pts) / (bpp * bpp) < RELIEF_ISLAND_PX2
        seams = geo.seam_edges(ring)
        if any(seams):      # round 9 (review 4): a cut at a pole or the antimeridian is never stroked as a border
            runs: list[list[tuple[float, float]]] = []
            start = next(i for i, sm in enumerate(seams) if sm)
            cur: list[tuple[float, float]] = []
            for jj in range(len(pts)):
                i_ = (start + 1 + jj) % len(pts)
                if not cur:
                    cur = [pts[i_]]
                if seams[i_]:
                    if len(cur) > 1:
                        runs.append(cur)
                    cur = []
                else:
                    cur.append(pts[(i_ + 1) % len(pts)])
            if len(cur) > 1:
                runs.append(cur)
            for run in runs:
                drawn.setdefault(id(c), []).append((run, small, False))
        else:
            drawn.setdefault(id(c), []).append((pts, small, True))
        dl.add(pts)
        if c is home:
            dh.add(pts)
            if not small:
                dhb.add(pts)
        if polar is not None and any(seams):     # round 11: the polar view's cut closed (no slit, no line)
            _close_seams([dl, dh, dhb] if c is home and not small else [dl, dh] if c is home else [dl], ring, pts,
                         255, max(3, round(2 * bpp)))
        for hole in poly[1:]:
            hp = [project(x, y) for x, y in hole]
            dl.add(hp, hole=True)
            if c is home:                     # another country's hole is its enclave's ground, not the home's
                dh.add(hp, hole=True)
                dhb.add(hp, hole=True)
                if _closed_at_half(hp):
                    pinholes.append(hp)
        if id(c) not in seen:
            seen.add(id(c))
            visible.append(c)
    for mask, rings in ((land_m, dl), (home_m, dh), (home_big, dhb)):
        raster.Draw(mask, grid="exact").fill_rings(rings, 255)
    # round 2 (review 1): the home edge's ring and INK line are cut by morphology and then blurred, a treatment
    # made for a two-level mask (fed the coverage itself, the coast was softened twice). Their source is the
    # coverage taken at its half, the pixels whose centre the home covers: the same exact coast as the fill,
    # without ImageDraw's half-pixel spread, so the INK line covers the neighbours' centred borders
    home_big = home_big.point(lambda v: 255 if v >= 128 else 0)
    if pinholes:          # a loch no pixel centre falls in keeps the pixels it touches, as ImageDraw kept them
        dp = ImageDraw.Draw(home_big)
        for hp in pinholes:
            dp.polygon(hp, fill=0)
    land = np.asarray(land_m, dtype=np.float32)[..., None] / 255.0
    homea = np.asarray(home_m, dtype=np.float32)[..., None] / 255.0
    shade = (RELIEF_NEIGHBOUR[0] + RELIEF_NEIGHBOUR[1] * hill)[..., None]
    land_rgb = np.array(LAND_TINT, dtype=np.float32) * shade
    home_rgb = np.array(HOME_TINT, dtype=np.float32) * relief_multiplier(hill, homea[..., 0], ctx)[..., None]
    rows = np.linspace(0, 1, bh, dtype=np.float32)[:, None, None]
    sea_rgb = np.array((14, 22, 34), dtype=np.float32) * (1 - rows) + np.array((7, 11, 19), dtype=np.float32) * rows
    out = sea_rgb * (1 - land) + land_rgb * land * (1 - homea) + home_rgb * homea
    hb = home_big.getbbox() if home is not None else None
    if hb:                       # the home coast's GOLD_LIGHT inner edge, inside the INK line drawn over the border
        pad_ = 16
        cx0, cy0 = max(0, hb[0] - pad_), max(0, hb[1] - pad_)
        cx1, cy1 = min(bw, hb[2] + pad_), min(bh, hb[3] + pad_)
        crop = np.asarray(home_big.crop((cx0, cy0, cx1, cy1)))

        def eroded(r: float) -> "npt.NDArray[np.uint8]":
            k = max(1, round(r))
            return cv2.erode(crop, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)),
                             borderType=cv2.BORDER_REPLICATE)
        outer = eroded(HOME_LINE_W / 2 * bpp)
        inner = eroded((HOME_LINE_W / 2 + HOME_EDGE_W) * bpp)
        ring = np.clip(outer.astype(np.float32) - inner.astype(np.float32), 0, 255) / 255.0
        ring = cv2.GaussianBlur(ring, (0, 0), max(0.5, 0.45 * bpp))[..., None]     # antialiased, not stepped
        region = out[cy0:cy1, cx0:cx1]
        ring = ring * HOME_EDGE_ALPHA        # a hair under full: its JPEG ringing stays under the text whites
        out[cy0:cy1, cx0:cx1] = region * (1 - ring) + np.array(GOLD_LIGHT, dtype=np.float32) * ring
    base = Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")

    draw = ImageDraw.Draw(base, "RGBA")
    borders = raster.Draw(base, "RGBA", grid="exact")  # QF-26: borders antialiased, centred on the coast
    runs: list[tuple[tuple[int, int, int], int, list[list[tuple[float, float]]]]] = []   # one stroke a style
    for c in sorted(visible, key=lambda c: c is home):  # borders on top; the home outline last, in ink
        for pts, small, closed in drawn.get(id(c), []):
            if small:
                width = max(1, round(bpp))
                colour = brand.INK if c is home else MAP_BORDER
            elif c is home:
                continue                  # the home outline: an antialiased INK ring, composited below
            else:
                colour, width = MAP_BORDER, 3
            if not runs or runs[-1][:2] != (colour, width):
                runs.append((colour, width, []))
            runs[-1][2].append(pts + [pts[0]] if closed else pts)
    for colour, width, lines in runs:
        borders.lines(lines, fill=colour, width=width, joint="curve")
    if hb:                                # the home coast's 2 px INK line over the border, antialiased (it stepped)
        crop = np.asarray(home_big.crop((cx0, cy0, cx1, cy1)))
        r_ = max(1, round(HOME_LINE_W / 2 * bpp))
        disc_ = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r_ + 1, 2 * r_ + 1))
        line_ = cv2.dilate(crop, disc_).astype(np.float32) - cv2.erode(crop, disc_, borderType=cv2.BORDER_REPLICATE)
        line_ = cv2.GaussianBlur(np.clip(line_, 0, 255), (0, 0), max(0.5, 0.45 * bpp))
        base.paste(brand.INK, (cx0, cy0), Image.fromarray(np.clip(line_, 0, 255).astype(np.uint8), "L"))
    # Neighbouring countries, named at Natural Earth's label points. A label is drawn only if it
    # collides with no other label and is either wholly inside the final close-up or wholly
    # outside it, so the last frame never shows half a name.
    # round 4: `name_px` sized for the close-up (66 fixed before); round 7 (R4): neighbours at MAP_NAME_WEIGHT, the
    # home (a route's) at the country name's weight and size, so no neighbour outranks it
    font_n = get_font("Montserrat", name_px, weight=MAP_NAME_WEIGHT)
    font_h = get_font("Montserrat", round(name_px * COUNTRY_PX / MAP_NAME_PX), weight=MAP_HOME_NAME_WEIGHT)
    home_np = np.asarray(home_m) if home is not None else None
    placed: list[tuple[float, float, float, float]] = list(reserved)  # e.g. the pin and place label
    for c in sorted(visible, key=lambda c: (c["label_rank"], -c["size"])):
        # Areas of indeterminate status (e.g. Siachen Glacier) are not countries and stay unnamed.
        if (c is home and not name_home) or not c["label_at"] or c["label_rank"] > 5 or c.get("type") == "Indeterminate":
            continue
        if c["display"] in skip or c["display"].upper() in skip:   # measured under the target on the close-up
            continue
        x0_, y0_ = project(*c["label_at"])
        font = font_h if c is home else font_n
        name = " ".join(c["display"].upper())  # letter-spaced small caps
        l0, t0, r0, b0 = draw.textbbox((x0_, y0_), name, font=font, anchor="mm")
        if c.get("extent") and polar is None:  # a name much wider than its land reads as naming something else
            ex0, _ = project(c["extent"][0], c["extent"][3])
            ex1, _ = project(c["extent"][2], c["extent"][1])
            if abs(ex1 - ex0) < 0.6 * (r0 - l0):
                continue
        # Natural Earth's label point first; when that collides (with a route leg, a stop's label, the scale
        # bar: round 3 review, 2026-11-02, the route map named no country at all), nearby places on the same
        # country's land, nearest first, so the journey keeps its geography.
        # Round 4 review (2026-11-02): a neighbour's name never touches the home country's fill ("SYRIA" was set
        # on the gold of Turkey at its southern edge and read as naming part of it), and sits on its own land:
        # Natural Earth's point by its centre, a nudged place by its centre and corners.
        rings = [[[project(px_, py_) for px_, py_ in ring] for ring in poly] for poly in c["polygons"]]
        if polar is not None:      # round 11: on a polar view the land's own drawn width (no lon/lat extent)
            xs_all = [p[0] for poly in rings for p in poly[0]]
            wide_ = max(xs_all) - min(xs_all) if xs_all else 0.0
            if wide_ < 0.6 * (r0 - l0):
                continue
        for dx, dy in _NAME_NUDGES:
            x, y = x0_ + dx * (r0 - l0), y0_ + dy * (b0 - t0)
            l, t, r, b = l0 + x - x0_, t0 + y - y0_, r0 + x - x0_, b0 + y - y0_
            probes = ((x, y), (l, t), (r, t), (l, b), (r, b)) if dx or dy else ((x, y),)
            if not all(geo.point_in_polygons(qx, qy, rings) for qx, qy in probes):
                continue
            if c is not home and name_on_mask(home_np, (l, t, r, b)):
                continue
            if _place_name(c, name, font, (x, y), (l, t, r, b), bw, bh, label_bottom_fraction, final_view, final_safe,
                           placed, transient, draw):
                if named is not None:
                    named.append(str(c["display"]))
                if named_boxes is not None:     # round 16 (review 4): its box too, for the name check
                    named_boxes.append((str(c["display"]), (l, t, r, b)))
                break
    return base if polar is not None else pole_fade(base, project, lon0)


POLE_FADE_DEG = 4.0      # round 10 (review): degrees past a pole over which the base fades to the sea's ink


def pole_fade(base: Image.Image, project: Callable[[float, float], tuple[float, float]], lon0: float) -> Image.Image:
    """Round 10 (review: the Chile route's 9:16 frame showed Antarctica mirrored past the South Pole, ground that
    is no real place): an equirectangular base that runs past a pole keeps its ground to the pole, fades it to
    SEA over POLE_FADE_DEG past it (the mirrored ground there, geo.view_polygons, so no seam is cut at the pole)
    and is SEA beyond, so the frame ends at the pole rather than on a reflection of the land. `base` is
    returned as it is where neither pole is in it (logged where one is)."""
    import numpy as np
    h = base.height
    y_s, y_n = project(lon0, -90.0)[1], project(lon0, 90.0)[1]
    if not (y_s < h or y_n > 0):
        return base
    fade_px = max(1.0, abs(project(lon0, -90.0 + POLE_FADE_DEG)[1] - y_s))
    arr = np.array(base.convert("RGB"))
    sea = np.array(SEA, np.float32)[None, None, :]
    # only the rows the fade reaches are touched (a base is up to 3x the frame: no whole-image float copy)
    spans = ([(max(0, int(y_s)), h, True)] if y_s < h else []) \
        + ([(0, min(h, math.ceil(y_n)), False)] if y_n > 0 else [])
    for r0, r1, south in spans:
        if r1 <= r0:
            continue
        ys = np.arange(r0, r1, dtype=np.float32)[:, None, None]
        a = np.clip(((y_s + fade_px - ys) if south else (ys - y_n + fade_px)) / fade_px, 0.0, 1.0)
        arr[r0:r1] = np.round(arr[r0:r1].astype(np.float32) * a + sea * (1 - a)).astype(np.uint8)
    log.info("Map: the view runs past the %s pole; its ground fades out there.", "south" if y_s < h else "north")
    return Image.fromarray(arr, "RGB")


def name_on_mask(mask, box: tuple[float, float, float, float], pad: int = 6) -> bool:
    """Whether any pixel of `mask` (an L array: the home country's fill in base pixels) lies within `box` grown by
    `pad` px: a neighbour's name there would read as naming part of the home country."""
    if mask is None:
        return False
    h, w = mask.shape[:2]
    x0, y0 = max(0, int(box[0]) - pad), max(0, int(box[1]) - pad)
    x1, y1 = min(w, math.ceil(box[2]) + pad), min(h, math.ceil(box[3]) + pad)
    return x1 > x0 and y1 > y0 and bool((mask[y0:y1, x0:x1] > 127).any())


# Where a country's name may move when its label point is taken: offsets in its own width (dx) and height (dy),
# nearest first; (0, 0) is Natural Earth's point.
# Round 4: further, up to two widths and six heights, so a route that fills the box still names its country.
_NAME_NUDGES = sorted(((dx, dy) for dx in (0.0, -0.35, 0.35, -0.7, 0.7, -1.05, 1.05, -1.4, 1.4, -1.75, 1.75, -2.1, 2.1)
                       for dy in (0.0, -1.5, 1.5, -3.0, 3.0, -4.5, 4.5, -6.0, 6.0)),
                      key=lambda o: (abs(o[0]) * 3) ** 2 + abs(o[1]) ** 2)


def _name_halo(glyph: Image.Image, px: int) -> Image.Image:
    """A base name's ink halo (MAP_NAME_HALO deep) from its glyph mask."""
    return glyph.filter(ImageFilter.MaxFilter(max(3, round(px * 0.18)) | 1)).filter(
        ImageFilter.GaussianBlur(max(2, px * 0.12))).point(lambda v: int(v * MAP_NAME_HALO))


def _place_name(c: dict[str, Any], name: str, font, xy: tuple[float, float], ink: tuple[float, float, float, float],
                bw: int, bh: int, label_bottom_fraction: float, final_view, final_safe, placed: list,
                transient: list[dict[str, Any]] | None, draw: ImageDraw.ImageDraw) -> bool:
    """Draws (or, see relief_base's `transient`, records) one neighbour's name at `xy` if its padded box is on the
    base, above the caption band, wholly inside or outside the close-up and clear of everything placed; True when
    it is placed."""
    x, y = xy
    l, t, r, b = ink
    fx0, fy0, fx1, fy1 = final_view
    box = (l - 30, t - 20, r + 30, b + 20)
    if box[0] < 0 or box[1] < 0 or box[2] > bw or box[3] > bh:
        return False
    # Reserve the subtitle band throughout both the wide view and final zoom.
    label_bottom = min(bh * label_bottom_fraction, fy0 + (fy1 - fy0) * label_bottom_fraction)
    if box[3] > label_bottom:
        return False
    m = 60  # clear of the close-up's edge, so the last frame shows no name touching it
    inside = fx0 + m <= box[0] and box[2] <= fx1 - m and fy0 + m <= box[1] and box[3] <= fy1 - m
    if inside and final_safe is not None:  # and inside the Reel's safe box on the last frame (so on every frame)
        inside = (final_safe[0] <= box[0] and box[2] <= final_safe[2]
                  and final_safe[1] <= box[1] and box[3] <= final_safe[3])
    outside = box[2] < fx0 or box[0] > fx1 or box[3] < fy0 or box[1] > fy1
    if not (inside or outside or transient is not None) or any(
            not (box[2] < p[0] or box[0] > p[2] or box[3] < p[1] or box[1] > p[3]) for p in placed):
        return False
    placed.append(box)
    if not inside and transient is not None:
        # A name not wholly inside the close-up's safe box is crossed by the frame's edge as the camera
        # zooms in: drawn into the base, it showed cut ("THUANIA", "ND": Derinkuyu, prod 2026-10-29). It
        # is drawn on the frames instead, only while it is wholly inside the safe box, fading out before.
        sprite = Image.new("L", (int(r - l) + 8, int(b - t) + 8), 0)
        ImageDraw.Draw(sprite).text((4 - l + x, 4 - t + y), drawable(name, font), font=font, fill=255, anchor="mm")
        # round 7 (review 6): its ink halo with it, as a name drawn into the base has ("GEORGIA" on Gobekli Tepe
        # 16:9 measured 1.6:1 with none, and was never recorded)
        transient.append({"name": c["display"], "sprite": sprite, "halo": _name_halo(sprite, font.size),
                          "box": (l - 4, t - 4, l - 4 + sprite.width, t - 4 + sprite.height)})
        return True
    glyph = Image.new("L", (int(r - l) + 8, int(b - t) + 8), 0)
    ImageDraw.Draw(glyph).text((4 - l + x, 4 - t + y), drawable(name, font), font=font, fill=255, anchor="mm")
    if transient is not None:  # drawn into the base: recorded so its place on each frame can be checked
        transient.append({"name": c["display"], "sprite": None, "box": box, "glyph": glyph,
                          "gbox": (l - 4, t - 4, l - 4 + glyph.width, t - 4 + glyph.height)})
    # round 4: an ink halo under the name, so it is measured (and passes) on busy relief
    draw._image.paste(brand.INK, (int(l - 4), int(t - 4)), _name_halo(glyph, font.size))
    _text(draw, (x, y), name, font=font, fill=MAP_NAME, anchor="mm")
    return True


class _MaskPlate:
    """A word drawn into the map's base, measured like a TextPlate on a frame (round 4): its glyph mask placed at
    `origin` on the frame, light text on its halo."""

    def __init__(self, text: str, glyphs, origin: tuple[int, int], fill, frame: Image.Image):
        import numpy as np
        self.text, self.fill, self.origin, self.light = text, tuple(fill), origin, True
        self.glyphs = (np.asarray(glyphs) > 0).astype(np.uint8) * np.asarray(glyphs)
        h, w = self.glyphs.shape
        self.box = (origin[0], origin[1], origin[0] + w, origin[1] + h)
        self.empty = not self.glyphs.any()
        self.halo = MAP_NAME_HALO
        self.contrast = None if self.empty else self.measure(frame)

    def measure(self, im: Image.Image) -> float | None:
        import numpy as np

        from .typography import ring_contrast
        if self.empty:
            return None
        rgb = np.asarray(im.convert("RGB"))
        try:
            return ring_contrast(rgb, self.glyphs, self.origin, None, True)
        except TypeError:          # too thin to read its own interior: its drawn colour
            return ring_contrast(rgb, self.glyphs, self.origin, self.fill, True)


def _base_name_plates(labels: list[dict[str, Any]], rest: Image.Image, final_view, width: int,
                      height: int) -> list[_MaskPlate]:
    """The neighbours' names drawn into the base that the close-up shows, as measured plates on its frame."""
    fx0, fy0, fx1, fy1 = final_view
    sx, sy = width / (fx1 - fx0), height / (fy1 - fy0)
    out = []
    for lab in labels:
        if lab.get("sprite") is not None or lab.get("glyph") is None:
            continue
        gx0, gy0, gx1, gy1 = lab["gbox"]
        x0, y0 = round((gx0 - fx0) * sx), round((gy0 - fy0) * sy)
        w, h = max(1, round((gx1 - gx0) * sx)), max(1, round((gy1 - gy0) * sy))
        if x0 < 0 or y0 < 0 or x0 + w > width or y0 + h > height:
            continue
        out.append(_MaskPlate(lab["name"].upper(), lab["glyph"].resize((w, h), Image.BICUBIC), (x0, y0), MAP_NAME,
                              rest))
    return out


# ------------------------------------------------------------- round 6 (2026-11-03): the site marker
# MAP_MARKER_STYLE picks the locator's marker: "pin" (default; a dropped pin, its name on top), "dot" (a dot, its
# name at the first free Imhof position) or "leader" (a dot, its name on a short leader). Route stops always use the
# dot. The label nearest the marker is the site's name, never the country's; the floating site title, the teal ring
# dot and its leader to the country are gone; a region's point (geo.is_region_fallback) gets no marker at all.
GOLD_LIGHT = (250, 214, 120)   # the home coast's inner edge (not a brand.py token: defined here, round 6)
MAP_MARKER_STYLE = "pin"
MAP_MARKER_STYLES = ("pin", "dot", "leader")
PIN_HEAD_D = 56.0              # px at 1080 (scaled by min(w, h) / 1080)
PIN_HEIGHT = 84.0              # tip to the head's top
PIN_TIP_ANGLE = 50.0           # degrees at the tip
PIN_STROKE = 4.0               # its GOLD stroke
PIN_CORE_D = 18.0              # its GOLD inner disc
PIN_SHADOW = (28.0, 8.0, 0.45, 6.0)   # the contact shadow: w, h, opacity, blur
PIN_FALL_PX = 120.0
PIN_FALL_S = 0.30
PIN_SQUASH = (1.10, 0.86)
PIN_SQUASH_FRAMES = 3
PIN_SQUASH_FPS = 30            # the squash's three frames, in seconds at the delivery rate
PIN_SETTLE_S = 0.18
PIN_OVERSHOOT = 1.4
PIN_RIPPLE = (20.0, 120.0, 0.6)       # from, to (diameter, px) and seconds
DOT_D, DOT_CORE_D, DOT_RING_W, DOT_KEY_W = 24.0, 14.0, 4.0, 1.0
DOT_POP_S = 0.3
DOT_RIPPLE = (24.0, 72.0, 0.6)
MARKER_LABEL_PX, MARKER_LABEL_PX_WIDE = 64, 72
MARKER_LABEL_MEASURE, MARKER_LABEL_MEASURE_WIDE = 640, 760
MARKER_LABEL_ROWS = 2
MARKER_LABEL_MIN_PX = 44
MARKER_LABEL_GAP = 16          # px: label to the pin's head, to a dot's edge
MARKER_LABEL_TOP_AIR = 40      # px: a pin label this near the box top flips under the pin
MARKER_LABEL_CLEAR = 24        # px a dot's label keeps from the globe, the scale bar and other labels
MARKER_COAST_SHARE = 0.30      # the most of a dot label's width that may straddle a coast
MARKER_LEAD_RING_GAP = 4       # px a leader stops short of the dot's ring
MARKER_LEAD_LABEL_GAP = 6      # and of its label
COUNTRY_PX, COUNTRY_PX_WIDE = 28, 32
COUNTRY_TRACK = 0.30           # em
COUNTRY_INK = 0.55             # INK's opacity in the fill (raised, logged, until it measures brand.TEXT_CONTRAST)
COUNTRY_ANCHOR_AIR = 140       # px from the marker's anchor to the name's centre
COUNTRY_ANCHOR_AIR_MIN = 60    # round 15: and at least, in a fill that holds it nowhere further (Lesotho)
COUNTRY_LABEL_AIR = 40         # px from the marker's label to the name's box
COUNTRY_OUT_PX = 24            # outside the fill: STONE_LT, a 0.9 INK halo
COUNTRY_OUT_AIR = 12           # px off the border
# round 9 (coordinator R7): the outside name in spec A.4's STONE_LT (round 7-8 set it in GOLD_LIGHT). It reads as
# the home's, not a neighbour's, by its weight (600 against the neighbours' MAP_NAME_WEIGHT) and its place: set by
# the home's border and nearer the home than any other country (country_outside_place)
COUNTRY_OUT_FILL = brand.STONE_LT
WATER_PX = 26
WATER_TRACK = 0.25
WATER_FILL = (110, 155, 166)
WATER_ALPHA = 0.75
WATER_MAX = 2
# Round 11 (2026-11-04, coordinator directive 1): the water named from Natural Earth's marine polygons
# (geo.marine_polygons), each body only inside its own polygon; the nearest-anchor table (WATER_SEAS, sea_names)
# named Portobelo's Caribbean the Pacific. Without the file no water is named.
WATER_STEP = 12                  # px: the grid a sea's name is tried on
WATER_CLEAR = 48                 # round 7 (review 5): px a sea's name keeps from every word and the bar (24 before:
                                 # Mont-Saint-Michel 9:16 stacked ATLANTIC OCEAN, FRANCE and the bar in 80 px)


def marker_style() -> str:
    """MAP_MARKER_STYLE, read at render time (so a test or a run can set it) and checked."""
    style = MAP_MARKER_STYLE
    assert style in MAP_MARKER_STYLES, f"MAP_MARKER_STYLE must be one of {MAP_MARKER_STYLES}, not {style!r}"
    return style


def _ease_in(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t ** 3


def _ease_out_back(t: float, overshoot: float = PIN_OVERSHOOT) -> float:
    t = min(1.0, max(0.0, t))
    c3 = overshoot + 1
    return 1 + c3 * (t - 1) ** 3 + overshoot * (t - 1) ** 2


@dataclass(frozen=True)
class PinPose:
    """The pin at one moment: its drop (px, negative is above its rest), its scale about the tip (sx, sy), its
    contact shadow's width share, and its ripple (diameter px, opacity; 0 opacity for none)."""
    drop: float
    sx: float
    sy: float
    shadow: float
    ripple_d: float
    ripple_a: float
    visible: bool


def pin_motion(t: float, t0: float | None = None) -> PinPose:
    """The pin's pose at `t` (round 6): it falls PIN_FALL_PX over PIN_FALL_S from `t0` (MAP_PIN_AT) on a cubic ease-in,
    its shadow growing from 0.4 to full width; on impact it squashes (PIN_SQUASH) for PIN_SQUASH_FRAMES frames, then
    settles to 1.0 on an ease-out-back (PIN_OVERSHOOT) by impact + PIN_SETTLE_S; one GOLD ripple runs from the tip
    over PIN_RIPPLE's seconds; no looping pulse."""
    t0 = MAP_PIN_AT if t0 is None else t0
    if t < t0:
        return PinPose(-PIN_FALL_PX, 1.0, 1.0, 0.4, 0.0, 0.0, False)
    impact = t0 + PIN_FALL_S
    if t < impact:
        u = (t - t0) / PIN_FALL_S
        return PinPose(-PIN_FALL_PX * (1 - _ease_in(u)), 1.0, 1.0, 0.4 + 0.6 * _ease_in(u), 0.0, 0.0, True)
    dt = t - impact
    hold = PIN_SQUASH_FRAMES / PIN_SQUASH_FPS
    if dt < hold:
        sx, sy = PIN_SQUASH
    elif dt < PIN_SETTLE_S:
        f = _ease_out_back((dt - hold) / (PIN_SETTLE_S - hold))
        sx, sy = PIN_SQUASH[0] + (1 - PIN_SQUASH[0]) * f, PIN_SQUASH[1] + (1 - PIN_SQUASH[1]) * f
    else:
        sx = sy = 1.0
    d0, d1, dur = PIN_RIPPLE
    u = dt / dur
    rd, ra = (d0 + (d1 - d0) * _ease(u), 0.8 * (1 - u)) if u < 1 else (0.0, 0.0)
    return PinPose(0.0, sx, sy, 1.0, rd, ra, True)


def marker_label_motion(t: float, style: str, t0: float | None = None) -> tuple[float, float]:
    """(alpha, rise px) of the site's name: a pin's from impact + 0.10 over 0.25 s (in by about 2.95 s), a dot's
    from 0.15 s after it lands, over 0.35 s; both rise 12 px."""
    t0 = MAP_PIN_AT if t0 is None else t0
    if style == "pin":
        a = _ease((t - (t0 + PIN_FALL_S + 0.10)) / 0.25)
    else:
        a = _ease((t - (t0 + DOT_POP_S + 0.15)) / 0.35)
    return a, 12 * (1 - a)


def dot_motion(t: float, t0: float | None = None) -> tuple[float, float, float]:
    """(scale, ripple diameter, ripple opacity) of a dot: 0 -> 1.15 -> 1.0 over DOT_POP_S (ease-out-back) from `t0`,
    then one GOLD ring from DOT_RIPPLE's start to its end diameter, fading, over its seconds."""
    t0 = MAP_PIN_AT if t0 is None else t0
    u = (t - t0) / DOT_POP_S
    if u <= 0:
        return 0.0, 0.0, 0.0
    s = _ease_out_back(u, 2.2) if u < 1 else 1.0
    d0, d1, dur = DOT_RIPPLE
    v = (t - t0 - DOT_POP_S) / dur
    rd, ra = (d0 + (d1 - d0) * _ease(v), 0.8 * (1 - v)) if 0 <= v < 1 else (0.0, 0.0)
    return s, rd, ra


def pin_outline(tip: tuple[float, float], k: float = 1.0, sx: float = 1.0, sy: float = 1.0,
                steps: int = 72) -> list[tuple[float, float]]:
    """The pin's outline (its head circle and the two sides that meet at the tip at PIN_TIP_ANGLE), scaled by `k` and
    by (sx, sy) about the tip, in sub-pixel coordinates; the tip is exactly `tip`."""
    r = PIN_HEAD_D / 2 * k
    c_dist = (PIN_HEIGHT * k) - r                   # tip to the head's centre
    half = math.radians(PIN_TIP_ANGLE / 2)
    # where each side, from the tip at +/- half the angle off vertical, meets the head circle (the far meeting)
    pts: list[tuple[float, float]] = []
    s_hit = c_dist * math.cos(half) - math.sqrt(max(0.0, r * r - (c_dist * math.sin(half)) ** 2))
    left = (-s_hit * math.sin(half), -s_hit * math.cos(half))
    right = (s_hit * math.sin(half), -s_hit * math.cos(half))
    a_r = math.atan2(right[1] + c_dist, right[0])
    a_l = math.atan2(left[1] + c_dist, left[0])
    # the arc over the top, from the right side's meeting round to the left's (counter-clockwise in y-up terms)
    span = (a_l - a_r) % (2 * math.pi)
    span = 2 * math.pi - span if span < math.pi else span
    for i in range(steps + 1):
        a = a_r - span * i / steps          # y-down: decreasing angles pass over the head's top
        pts.append((r * math.cos(a), -c_dist + r * math.sin(a)))
    outline = [(0.0, 0.0), right, *pts, left]
    return [(tip[0] + x * sx, tip[1] + y * sy) for x, y in outline]


def _draw_pin(im: Image.Image, tip: tuple[float, float], k: float, pose: PinPose) -> Image.Image:
    """The pin composited at its pose (supersampled 4x, the tip on its sub-pixel place): its contact shadow, INK body,
    PIN_STROKE GOLD stroke and GOLD inner disc, and its ripple."""
    if not pose.visible:
        return im
    ss = 4
    pad = int(PIN_RIPPLE[1] * k / 2 + 16)
    top_h = int((PIN_HEIGHT + PIN_FALL_PX) * k + 16)
    ox, oy = math.floor(tip[0]) - pad, math.floor(tip[1]) - top_h
    w, h = 2 * pad, top_h + pad
    fx, fy = (tip[0] - ox) * ss, (tip[1] - oy) * ss
    layer = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    # the contact shadow, on the ground at the tip
    sw, sh, sa, sb = PIN_SHADOW
    shadow = Image.new("L", layer.size, 0)
    ew, eh = sw * k * pose.shadow * ss / 2, sh * k * ss / 2
    ImageDraw.Draw(shadow).ellipse([fx - ew, fy - eh, fx + ew, fy + eh], fill=int(255 * sa))
    shadow = shadow.filter(ImageFilter.GaussianBlur(sb * k * ss / 2))
    ink = Image.new("RGBA", layer.size, (*brand.INK, 0))
    ink.putalpha(shadow)
    layer.alpha_composite(ink)
    d = ImageDraw.Draw(layer)
    if pose.ripple_a > 0:
        rr = pose.ripple_d * k * ss / 2
        d.ellipse([fx - rr, fy - rr, fx + rr, fy + rr], outline=(*brand.GOLD, int(255 * pose.ripple_a)),
                  width=max(1, round(2 * k * ss)))
    tip_ss = (fx, fy + pose.drop * k * ss)
    body = pin_outline(tip_ss, k * ss, pose.sx, pose.sy)
    # the GOLD stroke inside the outline, the INK body inside the stroke, the GOLD inner disc on the head's centre
    import cv2
    import numpy as np
    full_m = Image.new("L", layer.size, 0)
    ImageDraw.Draw(full_m).polygon(body, fill=255)
    stroke = max(1, round(PIN_STROKE * k * ss))
    er = cv2.erode(np.asarray(full_m), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * stroke + 1, 2 * stroke + 1)))
    layer.paste((*brand.GOLD, 255), (0, 0), full_m)
    layer.paste((*brand.INK, 255), (0, 0), Image.fromarray(er))
    hc = (tip_ss[0], tip_ss[1] - (PIN_HEIGHT - PIN_HEAD_D / 2) * k * ss * pose.sy)
    cr = PIN_CORE_D / 2 * k * ss
    ImageDraw.Draw(layer).ellipse([hc[0] - cr * pose.sx, hc[1] - cr * pose.sy, hc[0] + cr * pose.sx,
                                   hc[1] + cr * pose.sy], fill=(*brand.GOLD, 255))
    small = layer.resize((w, h), Image.LANCZOS)
    region = im.crop((ox, oy, ox + w, oy + h)).convert("RGBA")
    region.alpha_composite(small)
    im.paste(region.convert(im.mode), (ox, oy))
    return im


MARKER_SS = 4                  # the dot's and the legs' supersampling (drawn at 4x and reduced: no stair-steps)
Painter = Callable[[ImageDraw.ImageDraw, Callable[[float, float], tuple[float, float]], float], None]


def _ss_composite(im: Image.Image, box: Box, paint: Painter, ss: int = MARKER_SS) -> Image.Image:
    """`paint` drawn at `ss` x into a transparent layer over `box` (frame px) and reduced with a box filter, then
    composited onto `im` (changed in place and returned): antialiased edges at the cost of the box only. `paint`
    gets the layer's ImageDraw, a frame-to-layer point mapping and the scale."""
    x0, y0 = math.floor(box[0]) - 1, math.floor(box[1]) - 1
    x1, y1 = math.ceil(box[2]) + 1, math.ceil(box[3]) + 1
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)
    if x1 <= x0 or y1 <= y0:
        return im
    w, h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))

    def to_layer(x: float, y: float) -> tuple[float, float]:
        return (x - x0) * ss, (y - y0) * ss
    paint(ImageDraw.Draw(layer), to_layer, float(ss))
    small = layer.resize((w, h), Image.BOX)
    region = im.crop((x0, y0, x1, y1)).convert("RGBA")
    region.alpha_composite(small)
    im.paste(region.convert(im.mode), (x0, y0))
    return im


def dot_radii(k: float, scale: float = 1.0) -> tuple[float, float, float]:
    """The A.2 dot's radii (outer edge of the INK keyline, the BONE ring's, the INK core's): DOT_D across in all,
    a DOT_CORE_D core, a DOT_RING_W ring and a DOT_KEY_W keyline (24 = 14 + 2 x 4 + 2 x 1)."""
    ro = DOT_D / 2 * k * scale
    return ro, ro - DOT_KEY_W * k * scale, DOT_CORE_D / 2 * k * scale


def _draw_dot(im: Image.Image, xy: tuple[float, float], k: float, scale: float = 1.0, alpha: float = 1.0,
              ripple: tuple[float, float] = (0.0, 0.0)) -> Image.Image:
    """The A.2 dot composited on `im` (changed in place and returned), supersampled MARKER_SS x: a DOT_CORE_D INK
    core in a DOT_RING_W BONE ring inside a DOT_KEY_W INK keyline, DOT_D across, and its ripple ring (diameter,
    opacity)."""
    x, y = xy
    reach = max(DOT_D * k * max(scale, 1.0), ripple[0] * k if ripple[1] > 0 else 0.0) / 2 + 3
    ro, rb, rc = dot_radii(k, max(scale, 0.0))
    a = int(255 * alpha)

    def paint(d: ImageDraw.ImageDraw, at: Callable[[float, float], tuple[float, float]], ss: float) -> None:
        cx, cy = at(x, y)
        if ripple[1] > 0:
            rr = ripple[0] * k / 2 * ss
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(*brand.GOLD, int(255 * ripple[1] * alpha)),
                      width=max(1, round(2 * k * ss)))
        if scale <= 0:
            return
        for r, col in ((ro, brand.INK), (rb, brand.BONE), (rc, brand.INK)):
            d.ellipse([cx - r * ss, cy - r * ss, cx + r * ss, cy + r * ss], fill=(*col, a))
    return _ss_composite(im, (x - reach, y - reach, x + reach, y + reach), paint)


@dataclass(frozen=True)
class MarkerLayout:
    """A locator's marker and its site label (round 6): the style, the anchor (the site's pixel), the label's rows
    as (text, (x, baseline), anchor), its font size, the label's ink box, where it went ("above", "below", "right",
    "left", "leader", "flip"), the leader (from its end at the label to its end at the dot) or None, and the box
    the marker itself takes."""
    style: str
    anchor: tuple[float, float]
    rows: tuple[tuple[str, tuple[float, float], str], ...]
    font_px: int
    label_box: Box
    position: str
    leader: tuple[tuple[float, float], tuple[float, float]] | None
    marker_box: Box


def marker_label_lines(text: str, width: int, height: int) -> tuple[list[str], FreeTypeFont]:
    """The site's name in Anton on at most MARKER_LABEL_ROWS balanced rows no wider than the orientation's measure,
    at its size, stepping down to MARKER_LABEL_MIN_PX only where no break holds it."""
    tall = height > width
    k = 1.0 if tall else min(width / 1920, height / 1080)
    measure = (MARKER_LABEL_MEASURE if tall else MARKER_LABEL_MEASURE_WIDE) * k
    px0 = round((MARKER_LABEL_PX if tall else MARKER_LABEL_PX_WIDE) * k)
    shown = " ".join(text.split()).upper()
    for px in range(px0, max(8, round(MARKER_LABEL_MIN_PX * k)) - 1, -2):
        font = field_font(shown, get_font("Anton", px))            # the name's rows in one face (QF-94)
        lines = route_name_lines(text, font, measure, MARKER_LABEL_ROWS)
        if lines is not None:
            return lines, font
    font = field_font(shown, get_font("Anton", max(8, round(MARKER_LABEL_MIN_PX * k))))
    raise TextFitError(text, [text.upper()], font, measure, MARKER_LABEL_ROWS)


def _rows_box(rows: list[tuple[str, tuple[float, float], str]], font: FreeTypeFont) -> Box | None:
    boxes = [b for b in (ink_box(xy, t, font, a, 0) for t, xy, a in rows) if b]
    return _union(boxes) if boxes else None


def _label_rows(lines: list[str], font: FreeTypeFont, x: float, first_base: float,
                anchor: str) -> list[tuple[str, tuple[float, float], str]]:
    step = round(cap_height(font) * 1.22)
    return [(ln, (x, first_base + step * j), anchor) for j, ln in enumerate(lines)]


PIN_ISLAND_COVER_MAX = 0.12     # round 11 (directive 4): the share of the island the pin and its name may cover
PIN_LABEL_SLIDE = 0.8           # and the share of its half-width the name may slide off the pin's x to cover less


def island_cover(boxes: Sequence[Box], home: BoolArray | None) -> float:
    """Round 11 (coordinator directive 4): the share of the island's own fill (`home`, frame-sized; all of it in
    view) that the union of `boxes` (the pin and its label, frame px) covers; 0 with no fill."""
    if home is None:
        return 0.0
    total = int(home.sum())
    if total == 0:
        return 0.0
    import numpy as np
    h, w = home.shape
    under = np.zeros((h, w), bool)
    for b in boxes:
        x0, y0, x1, y1 = max(0, int(b[0])), max(0, int(b[1])), min(w, math.ceil(b[2])), min(h, math.ceil(b[3]))
        if x1 > x0 and y1 > y0:
            under[y0:y1, x0:x1] = True
    return float((under & home).sum()) / total


def _mask_share(b: Box, mask: BoolArray | None) -> float:
    """The share of box `b` (frame px) that `mask` (frame-sized bool array) covers."""
    if mask is None:
        return 0.0
    h, w = mask.shape
    x0, y0, x1, y1 = max(0, int(b[0])), max(0, int(b[1])), min(w, math.ceil(b[2])), min(h, math.ceil(b[3]))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float(mask[y0:y1, x0:x1].mean())


PIN_FLOAT_PX = 6.0               # round 13 (directive 2): px the pin's tip may stand off the drawn land
PART_SMALL_SHARE = 0.15          # a site's own part of the fill this share of the graphics box or less is small
PART_REACH_PX = 40.0             # px from the site within which the nearest part of the fill is the site's own


def site_part(home: BoolArray | None, land: BoolArray | None, anchor: tuple[float, float],
              k: float = 1.0) -> BoolArray | None:
    """Round 13 (directive 2): the site's own part of the drawn ground (the island, or the small country, under the
    marker): the 8-connected part of the home fill at the site or within PART_REACH_PX of it (else of any land),
    where that part lies wholly inside the frame; None where none does (a fill running off the frame is no island)."""
    import numpy as np
    for mask in (home, land):
        if mask is None or not np.asarray(mask).any():
            continue
        comps = _components(mask)
        part = _site_component(comps, anchor, PART_REACH_PX * k)
        if not part or comps is None:
            continue
        m = comps[0] == part
        if m[0, :].any() or m[-1, :].any() or m[:, 0].any() or m[:, -1].any():
            return None
        out: BoolArray = m
        return out
    return None


def ground_word(part: BoolArray | None, home: BoolArray | None, land: BoolArray | None) -> str:
    """What the site's own ground is, for the logs: "country" where its part of the fill borders another country's
    land (a landlocked or small mainland country: Liechtenstein), else "island"."""
    import cv2
    import numpy as np
    if part is None or land is None:
        return "island"
    ring = cv2.dilate(np.asarray(part, np.uint8), np.ones((5, 5), np.uint8)) > 0
    other = np.asarray(land, bool) & ~(np.asarray(home, bool) if home is not None else np.zeros_like(ring))
    return "country" if bool((ring & other).any()) else "island"


def pin_fallback(lay: "MarkerLayout", land: BoolArray | None, part: BoolArray | None, k: float = 1.0) -> str:
    """Why the pin cannot mark the site honestly ("" where it can): its tip stands more than PIN_FLOAT_PX off the
    drawn land (the data leaves the site's islet out: Betio, 9 km from the drawn Tarawa), or the pin and its name
    cover more than PIN_ISLAND_COVER_MAX of the site's own island or small country wherever the name is set (Aldabra
    at 24 %, Liechtenstein at 46 %)."""
    import cv2
    import numpy as np
    x, y = lay.anchor
    if land is not None:
        h, w = land.shape
        r = int(PIN_FLOAT_PX * k) + 1
        x0, y0, x1, y1 = max(0, int(x) - r), max(0, int(y) - r), min(w, int(x) + r + 1), min(h, int(y) + r + 1)
        win = np.asarray(land[y0:y1, x0:x1], bool)
        if win.size == 0 or not win.any():
            near = "no drawn land in view" if not np.asarray(land).any() else None
            if near is None:
                dt = cv2.distanceTransform((~np.asarray(land, bool)).astype(np.uint8), cv2.DIST_L2, 5)
                near = f"the pin's tip would stand {float(dt[min(h - 1, int(y)), min(w - 1, int(x))]):.0f} px off " \
                       "the drawn land"
            return near
    if part is not None:
        # (on a small island or country only: a large country's fill takes its pin and name as any locator does)
        h, w = part.shape
        tb = get_text_box(w, h)
        small = float(np.count_nonzero(part)) <= PART_SMALL_SHARE * (tb[2] - tb[0]) * (tb[3] - tb[1])
        cover = island_cover([lay.label_box, lay.marker_box], part) if small else 0.0
        if cover > PIN_ISLAND_COVER_MAX:
            return f"the pin and its name would cover {100 * cover:.0f} % of the site's own ground " \
                   f"({100 * PIN_ISLAND_COVER_MAX:.0f} % the most)"
    return ""


def leader_marker(anchor: tuple[float, float], label: str, width: int, height: int, land: BoolArray | None,
                  part: BoolArray | None, k: float = 1.0) -> "MarkerLayout":
    """The honest fallback (round 13, directive 2): the dot at the site and its name on a leader, the name kept
    MARKER_LABEL_CLEAR off the site's own island or small country (its box as an obstacle) so the dot alone stands
    on it; the cover it leaves is logged where it is still over PIN_ISLAND_COVER_MAX."""
    import numpy as np
    obst: list[Box] = []
    if part is not None and np.asarray(part).any():
        ys, xs = np.nonzero(part)
        obst.append((float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)))
    lay = dot_layout(anchor, label, width, height, obst, land, leader_only=True)
    if part is not None:     # the dot is the site's precise mark; its name is what must keep off the ground
        cover = island_cover([lay.label_box], part)
        if cover > PIN_ISLAND_COVER_MAX:
            log.warning("Map: the dot's name still covers %.0f %% of the site's own ground.", 100 * cover)
    return lay


def pin_layout(anchor: tuple[float, float], text: str, width: int, height: int,
               box: Box | None = None, home: BoolArray | None = None, ground: str = "island") -> MarkerLayout:
    """The pin's site label (A.1): centred on the pin's x, the last row's baseline MARKER_LABEL_GAP above the head's
    top; where its top would come within MARKER_LABEL_TOP_AIR of the graphics box's top it flips under the pin, its
    cap top MARKER_LABEL_GAP under the tip's shadow."""
    k = min(width, height) / 1080
    box = box or get_text_box(width, height)
    lines, font = marker_label_lines(text, width, height)
    step = round(cap_height(font) * 1.22)
    cap = cap_height(font)
    head_top = anchor[1] - PIN_HEIGHT * k
    last_base = head_top - MARKER_LABEL_GAP * k
    rows = _label_rows(lines, font, anchor[0], last_base - step * (len(lines) - 1), "ms")
    lb = _rows_box(rows, font) or (anchor[0], last_base - cap, anchor[0], last_base)
    position = "above"
    if lb[1] < box[1] + MARKER_LABEL_TOP_AIR:
        first_base = anchor[1] + PIN_SHADOW[1] * k / 2 + MARKER_LABEL_GAP * k + cap
        rows = _label_rows(lines, font, anchor[0], first_base, "ms")
        lb = _rows_box(rows, font) or lb
        position = "flip"
    r = PIN_HEAD_D / 2 * k
    mbox = (anchor[0] - r - PIN_STROKE * k, anchor[1] - PIN_HEIGHT * k - PIN_STROKE * k, anchor[0] + r + PIN_STROKE * k,
            anchor[1] + PIN_SHADOW[1] * k)
    cover0 = island_cover([lb, mbox], home) if position == "above" else 0.0
    if cover0 > PIN_ISLAND_COVER_MAX:
        # round 11 (coordinator directive 4): the label and the pin over the island's own fill measured as the
        # share of the ISLAND they cover (Funafuti's thin atoll: its name ran across the north third under a box
        # rule that measured the box's share). Tried above or flipped under the pin, slid along its row up to
        # PIN_LABEL_SLIDE of its width, or set beside the pin's head on either side; the least cover wins (logged)
        first_base = anchor[1] + PIN_SHADOW[1] * k / 2 + MARKER_LABEL_GAP * k + cap
        half = (lb[2] - lb[0]) / 2
        best = (cover0, 0.0, rows, lb, position)
        opts: list[tuple[str, float]] = [(pos, dx) for pos in ("above", "flip") for dx in sorted(
            {0.0, *(f * half * PIN_LABEL_SLIDE * sgn for f in (0.5, 1.0) for sgn in (-1, 1))}, key=abs)]
        opts += [("right", 0.0), ("left", 0.0)]
        head_cy = anchor[1] - PIN_HEIGHT * k + r
        for pos, dx in opts:
            if pos in ("right", "left"):
                block = step * (len(lines) - 1) + cap
                base_ = head_cy - block / 2 + cap
                gap = MARKER_LABEL_GAP * k + r + PIN_STROKE * k
                if pos == "right":
                    rows2 = _label_rows(lines, font, anchor[0] + gap, base_, "ls")
                else:
                    rows2 = _label_rows(lines, font, anchor[0] - gap, base_, "rs")
            else:
                base_ = first_base if pos == "flip" else last_base - step * (len(lines) - 1)
                rows2 = _label_rows(lines, font, anchor[0] + dx, base_, "ms")
            lb2 = _rows_box(rows2, font) or lb
            if not (box[0] <= lb2[0] and lb2[2] <= box[2] and box[1] <= lb2[1] and lb2[3] <= box[3]):
                continue
            sh = island_cover([lb2, mbox], home)
            if sh + 0.02 < best[0]:
                best = (sh, dx, rows2, lb2, pos)
        if best[0] < cover0:
            rows, lb, position = best[2], best[3], best[4]
            log.info("Map: the site's name set %s the pin%s: it and the pin cover %.0f %% of the %s (%.0f %% "
                     "centred above).", {"flip": "under", "above": "over", "right": "right of",
                                         "left": "left of"}[position],
                     f", slid {best[1]:+.0f} px" if best[1] else "", 100 * best[0], ground, 100 * cover0)
        if best[0] > PIN_ISLAND_COVER_MAX:
            log.info("Map: the pin and its name cover %.0f %% of the %s wherever set (%.0f %% the most wanted).",
                     100 * best[0], ground, 100 * PIN_ISLAND_COVER_MAX)
    return MarkerLayout("pin", anchor, tuple(rows), font.size, lb, position, None, mbox)


def _box_clear(b: Box, obstacles: list[Box], air: float) -> bool:
    return not any(LayoutConstraintSolver.boxes_intersect(b, o, air) for o in obstacles)


def coast_share(b: Box, land: BoolArray | None) -> float:
    """The share of a box's width that straddles a coast (`land`: a bool array, frame px): its columns not wholly
    on its main side (all land, or all sea)."""
    import numpy as np
    if land is None:
        return 0.0
    h, w = land.shape
    x0, y0, x1, y1 = max(0, int(b[0])), max(0, int(b[1])), min(w, math.ceil(b[2])), min(h, math.ceil(b[3]))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    sub = land[y0:y1, x0:x1]
    all_land, all_sea = sub.all(axis=0), ~sub.any(axis=0)
    n = max(1, x1 - x0)
    # the columns not on its main side: a coast through it (mixed columns) or beside it (the other side's)
    return 1.0 - max(float(np.count_nonzero(all_land)), float(np.count_nonzero(all_sea))) / n


def dot_layout(anchor: tuple[float, float], text: str, width: int, height: int, obstacles: Sequence[Box] = (),
               land: BoolArray | None = None, box: Box | None = None, leader_only: bool = False) -> MarkerLayout:
    """The dot's site label (A.2): MARKER_LABEL_GAP from the dot's edge at the first free Imhof position (9:16 above,
    below, right, left; 16:9 right, left, above, below), free meaning inside the graphics box, MARKER_LABEL_CLEAR
    from `obstacles` and straddling a coast for at most MARKER_COAST_SHARE of its width; right and left set the first
    row's cap middle on the dot's centre, above and below centre the label on it. No position free: the leader
    layout (leader_layout). `leader_only`: the leader layout at once (the "leader" style)."""
    k = min(width, height) / 1080
    box = box or get_text_box(width, height)
    lines, font = marker_label_lines(text, width, height)
    step = round(cap_height(font) * 1.22)
    cap = cap_height(font)
    gap = (DOT_D / 2 + MARKER_LABEL_GAP) * k
    x, y = anchor
    tall = height > width
    order = ("above", "below", "right", "left") if tall else ("right", "left", "above", "below")
    rd = DOT_D / 2 * k
    mbox = (x - rd, y - rd, x + rd, y + rd)
    if not leader_only:
        for pos in order:
            if pos == "above":
                rows = _label_rows(lines, font, x, y - gap - step * (len(lines) - 1), "ms")
            elif pos == "below":
                rows = _label_rows(lines, font, x, y + gap + cap, "ms")
            elif pos == "right":
                rows = _label_rows(lines, font, x + gap, y + cap / 2, "ls")
            else:
                rows = _label_rows(lines, font, x - gap, y + cap / 2, "rs")
            lb = _rows_box(rows, font)
            if lb is None:
                continue
            if not (box[0] <= lb[0] and lb[2] <= box[2] and box[1] <= lb[1] and lb[3] <= box[3]):
                continue
            if not _box_clear(lb, list(obstacles), MARKER_LABEL_CLEAR * k):
                continue
            if coast_share(lb, land) > MARKER_COAST_SHARE:
                continue
            return MarkerLayout("dot", anchor, tuple(rows), font.size, lb, pos, None, mbox)
    return leader_layout(anchor, lines, font, width, height, obstacles, land, box, "dot" if not leader_only else "leader")


def leader_layout(anchor: tuple[float, float], lines: list[str], font: FreeTypeFont, width: int, height: int,
                  obstacles: Sequence[Box] = (), land: BoolArray | None = None, box: Box | None = None,
                  style: str = "leader") -> MarkerLayout:
    """The leader layout (A.3): the site's name off the dot on a leader MAP_LEADER_MIN-MAP_LEADER_MAX long, from
    MARKER_LEAD_LABEL_GAP short of the label to MARKER_LEAD_RING_GAP short of the dot's ring; past MAP_LEADER_MAX the
    label moves round the dot (eight directions) instead of the leader lengthening. The first clear place wins; none
    clear, the first inside the box (logged)."""
    k = min(width, height) / 1080
    box = box or get_text_box(width, height)
    step = round(cap_height(font) * 1.22)
    cap = cap_height(font)
    x, y = anchor
    ring = (DOT_D / 2 + MARKER_LEAD_RING_GAP) * k
    rd = DOT_D / 2 * k
    mbox = (x - rd, y - rd, x + rd, y + rd)
    h_block = step * (len(lines) - 1) + cap
    first: MarkerLayout | None = None
    tall = height > width
    dirs = ((0, -1), (0, 1), (1, -1), (-1, -1), (1, 1), (-1, 1), (1, 0), (-1, 0)) if tall else \
        ((1, -1), (-1, -1), (1, 1), (-1, 1), (1, 0), (-1, 0), (0, -1), (0, 1))
    for length in range(MAP_LEADER_MIN, MAP_LEADER_MAX + 1, 6):
        for dx, dy in dirs:
            nrm = math.hypot(dx, dy)
            ux, uy = dx / nrm, dy / nrm
            reach = ring + length * k + MARKER_LEAD_LABEL_GAP * k     # the dot's centre to the label's near edge
            ex, ey = x + ux * reach, y + uy * reach
            anchor_s = "ms" if dx == 0 else ("ls" if dx > 0 else "rs")
            if dy < 0:
                base0 = ey - step * (len(lines) - 1)
            elif dy > 0:
                base0 = ey + cap
            else:
                base0 = ey - h_block / 2 + cap
            rows = _label_rows(lines, font, ex, base0, anchor_s)
            lb = _rows_box(rows, font)
            if lb is None:
                continue
            if not (box[0] <= lb[0] and lb[2] <= box[2] and box[1] <= lb[1] and lb[3] <= box[3]):
                continue
            # the leader: from MARKER_LEAD_LABEL_GAP off the label's nearest point to MARKER_LEAD_RING_GAP off the ring
            nx, ny = min(max(x, lb[0]), lb[2]), min(max(y, lb[1]), lb[3])
            dist = math.hypot(nx - x, ny - y) or 1.0
            vx, vy = (nx - x) / dist, (ny - y) / dist
            a = (nx - vx * MARKER_LEAD_LABEL_GAP * k, ny - vy * MARKER_LEAD_LABEL_GAP * k)
            b = (x + vx * ring, y + vy * ring)
            if math.dist(a, b) > MAP_LEADER_MAX * k + 0.5 or math.dist(a, b) < MAP_LEADER_MIN * k - 0.5:
                continue
            lay = MarkerLayout(style, anchor, tuple(rows), font.size, lb, "leader", (a, b), mbox)
            if first is None:
                first = lay
            if _box_clear(lb, list(obstacles), MARKER_LABEL_CLEAR * k) and coast_share(lb, land) <= MARKER_COAST_SHARE:
                return lay
    if first is None:     # nothing inside the box at all: above the dot, the shortest leader
        rows = _label_rows(lines, font, x, y - ring - MAP_LEADER_MIN * k - MARKER_LEAD_LABEL_GAP * k
                           - step * (len(lines) - 1), "ms")
        lb = _rows_box(rows, font) or mbox
        first = MarkerLayout(style, anchor, tuple(rows), font.size, lb, "leader",
                             ((x, lb[3] + MARKER_LEAD_LABEL_GAP * k), (x, y - ring)), mbox)
    log.info("Map: no clear place for the site's name on a leader; set at the first inside the box.")
    return first


def marker_layout(style: str, anchor: tuple[float, float], text: str, width: int, height: int,
                  obstacles: Sequence[Box] = (), land: BoolArray | None = None,
                  home: BoolArray | None = None, ground: str = "island") -> MarkerLayout:
    if style == "pin":
        # round 13 (review): `ground` names what the cover is of in the log ("country" for a landlocked one)
        return pin_layout(anchor, text, width, height, home=home, ground=ground)
    return dot_layout(anchor, text, width, height, obstacles, land, leader_only=style == "leader")


def _draw_leader(im: Image.Image, leader: tuple[tuple[float, float], tuple[float, float]], k: float,
                 progress: float, alpha: float = 1.0) -> Image.Image:
    """A leader drawn from its label end toward the dot (`progress` 0-1), supersampled: a 2 px BONE core cased 1 px
    in INK either side. `im` is changed in place and returned."""
    if progress <= 0:
        return im
    (ax, ay), (bx, by) = leader
    end = (ax + (bx - ax) * progress, ay + (by - ay) * progress)

    def paint(d: ImageDraw.ImageDraw, at: Callable[[float, float], tuple[float, float]], ss: float) -> None:
        a_, b_ = at(ax, ay), at(*end)
        d.line([a_, b_], fill=(*brand.INK, int(255 * alpha)), width=max(1, round(4 * k * ss)))
        d.line([a_, b_], fill=(*brand.BONE, int(255 * alpha)), width=max(1, round(2 * k * ss)))
    return _ss_composite(im, (min(ax, end[0]) - 4, min(ay, end[1]) - 4, max(ax, end[0]) + 4, max(ay, end[1]) + 4),
                         paint)


def label_plates_for(rest: Image.Image, layout: MarkerLayout) -> list[tuple[str, TextPlate]]:
    """The site's name rows as measured knockout plates: BONE Anton on an INK halo at least 60 % deep (the 8 px soft
    shadow under it), raised while short of the target."""
    font = get_font("Anton", layout.font_px)
    return [("place", plan_text(rest, xy, text, font, WHITE, anchor, halo_px=max(8, round(font.size * 0.2)),
                                min_halo=0.6, shadow=True)) for text, xy, anchor in layout.rows]


# ------------------------------------------------------------- round 6 (2026-11-03): the locator's framing and words
LOCATOR_SPAN = 18.0              # degrees across the close-up before framing (camera_spans ends on the framed span)
LOCATOR_HOME_SHARE = (0.20, 0.60)
LOCATOR_HOME_TARGET = 0.4       # the share aimed for (round 6 review: 0.5 left no room for the neighbours)
LOCATOR_OTHER_MIN = 0.25         # of the graphics box: sea or neighbouring land
LOCATOR_NEIGHBOUR_MIN = 0.10     # round 6 review: and neighbouring land at least this share of it, where any span has it


POLAR_REACH = 75.0        # round 11 (coordinator directive 2): a view reaching this latitude is drawn polar


@dataclass(frozen=True)
class PolarProjection:
    """Round 11 (coordinator directive 2): the azimuthal equidistant view centred on the site (lon0, lat0) for a
    map whose view reaches within 15 degrees of a pole (POLAR_REACH): the site at px (cx, cy), `k` px a degree of
    arc, north up at the site. No void, mirrored land or seam: the pole is a point on the map like any other.
    Called as a (lon, lat) -> px map."""
    lon0: float
    lat0: float
    cx: float
    cy: float
    k: float

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        u, v = geo.azimuthal(lon, lat, self.lon0, self.lat0)
        return self.cx + u * self.k, self.cy - v * self.k

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        return geo.azimuthal_inverse((x - self.cx) / self.k, (self.cy - y) / self.k, self.lon0, self.lat0)

    def scaled(self, by: float, dx: float = 0.0, dy: float = 0.0) -> "PolarProjection":
        """The same view `by` times the px (and moved by dx, dy px after)."""
        return PolarProjection(self.lon0, self.lat0, self.cx * by + dx, self.cy * by + dy, self.k * by)

    def radius(self, width: float, height: float, pad: float = 2.0) -> float:
        """Degrees of arc from the site to the furthest corner of a width x height frame, `pad` more."""
        return max(math.hypot(x - self.cx, y - self.cy) for x in (0, width) for y in (0, height)) / self.k + pad

    def inverse_grid(self, xs: "npt.NDArray[np.float64]", ys: "npt.NDArray[np.float64]"
                     ) -> tuple["npt.NDArray[np.float64]", "npt.NDArray[np.float64]"]:
        """(lon, lat) arrays over the grid of px columns `xs` and rows `ys` (vectorised inverse)."""
        import numpy as np
        u = np.radians((xs[None, :] - self.cx) / self.k)
        v = np.radians((self.cy - ys[:, None]) / self.k)
        rho = np.hypot(u, v)
        la0 = math.radians(self.lat0)
        c = rho
        sc, cc = np.sin(c), np.cos(c)
        with np.errstate(invalid="ignore", divide="ignore"):
            lat = np.arcsin(np.clip(cc * math.sin(la0) + np.where(rho > 1e-12, v * sc * math.cos(la0) / rho, 0.0),
                                    -1.0, 1.0))
            lon = self.lon0 + np.degrees(np.arctan2(u * sc, rho * math.cos(la0) * cc - v * math.sin(la0) * sc))
        lon = np.where(rho > 1e-12, lon, self.lon0)
        return (lon + 180.0) % 360.0 - 180.0, np.degrees(lat)


def is_polar_view(site_lat: float, width: int, height: int, span_close: float, span_wide: float | None = None
                  ) -> bool:
    """Whether a locator is drawn polar: its close-up (`span_close` degrees across the frame's width) reaches
    POLAR_REACH, or its widest view (`span_wide`) would run past the pole itself (plate carree never does)."""
    def reach(span: float) -> float:
        return abs(site_lat) + span * height / width / 2
    return reach(span_close) >= POLAR_REACH or (span_wide is not None and reach(span_wide) >= 90.0)


def polar_lift(width: int, height: int) -> float:
    """Px the site stands over the frame's centre on a polar view: on 9:16 MAP_PIN_Y_SHARE down the graphics box
    (as view_centre_lat puts it), on 16:9 none."""
    if width >= height:
        return 0.0
    tb = get_text_box(width, height)
    return max(0.0, height / 2 - (tb[1] + MAP_PIN_Y_SHARE * (tb[3] - tb[1])))


def _view_polygons(to_frame: Callable[[float, float], tuple[float, float]], width: float, height: float
                   ) -> list[tuple[geo.Country, list[geo.Ring]]]:
    """The country polygons a frame shows: geo.polar_polygons for a PolarProjection, else geo.view_polygons over
    the linear view's extent (_frame_view)."""
    if isinstance(to_frame, PolarProjection):
        return geo.polar_polygons(to_frame.lon0, to_frame.lat0, to_frame.radius(width, height))
    return geo.view_polygons(*_frame_view(to_frame, round(width), round(height)))


def _closed_at_half(ring: Sequence[tuple[float, float]]) -> bool:
    """QF-26 round 2: a hole whose coverage reaches half of no pixel (relief_base's home edge is cut where the
    home covers half a pixel, so such a loch would vanish from the coast's INK and gold); holes over 256 px across
    always reach it."""
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    x0, y0 = math.floor(min(xs)) - 1, math.floor(min(ys)) - 1
    w, h = math.ceil(max(xs)) - x0 + 1, math.ceil(max(ys)) - y0 + 1
    if w > 256 or h > 256:
        return False
    tile = Image.new("L", (w, h), 0)
    raster.Draw(tile, grid="exact").polygon([(x - x0, y - y0) for x, y in ring], fill=255)
    return max(tile.getextrema()) < 128


def _close_seams(draws: Sequence[Any], ring: Sequence[tuple[float, float]],
                 pts: Sequence[tuple[float, float]], fill: int, width: int = 3) -> None:
    """Round 11: on a polar view the antimeridian cut of a polygon round the pole (Antarctica's) is two coincident
    edges, which a polygon fill leaves as an open one-pixel slit (stroked as a coast line from the pole out): the
    cut's edges (geo.seam_edges) are filled over with `fill`, `width` px wide."""
    seams = geo.seam_edges(list(ring))
    n = len(pts)
    for i, sm in enumerate(seams):
        if sm and n > 1:
            for dd in draws:
                dd.line([pts[i], pts[(i + 1) % n]], fill=fill, width=width)


def view_masks(width: int, height: int, lon0: float, lat0: float, span: float, home: geo.Country | None,
               down: int = 1, polar: PolarProjection | None = None) -> tuple[BoolArray, BoolArray]:
    """(land, home) bool arrays of the resting view (`span` degrees of longitude across the frame at cos(lat)
    scale, centred on the site), at 1/`down` of the frame's resolution; round 11: `polar`, the frame's polar view
    instead (lon0, lat0 and span then unused)."""
    import numpy as np
    coslat = max(0.2, math.cos(math.radians(lat0)))
    kf = width / span
    w, h = max(1, width // down), max(1, height // down)
    lon_half = width / 2 / (kf * coslat) + 2
    lat_half = height / 2 / kf + 2
    land_m, home_m = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    dl, dh = ImageDraw.Draw(land_m), ImageDraw.Draw(home_m)

    def pt(x: float, y: float) -> tuple[float, float]:
        if polar is not None:
            px_, py_ = polar(x, y)
            return px_ / down, py_ / down
        return (width / 2 + (x - lon0) * kf * coslat) / down, (height / 2 - (y - lat0) * kf) / down
    polys = _view_polygons(polar, width, height) if polar is not None else geo.view_polygons(lon0, lat0, lon_half,
                                                                                             lat_half)
    for c, poly in polys:   # round 8: wrapped, enclaves last
        pts = [pt(x, y) for x, y in poly[0]]
        dl.polygon(pts, fill=255)
        if c is home:
            dh.polygon(pts, fill=255)
        if polar is not None:
            _close_seams([dl, dh] if c is home else [dl], poly[0], pts, 255, 3)
        for hole in poly[1:]:
            hp = [pt(x, y) for x, y in hole]
            dl.polygon(hp, fill=0)
            if c is home:
                dh.polygon(hp, fill=0)
    return np.asarray(land_m) > 127, np.asarray(home_m) > 127


def home_share(home: BoolArray, box: Box, down: int = 1) -> float:
    """The share of `box` (frame px) the home fill covers (`home` at 1/`down` resolution)."""
    x0, y0, x1, y1 = (int(v / down) for v in box)
    sub = home[y0:y1, x0:x1]
    return float(sub.mean()) if sub.size else 0.0


LOCATOR_MAX_ZOOM = 5.0          # the wide view's span over the close-up's at most (the base's own detail holds)


MAP_PIN_Y_SHARE = 0.6           # round 10: 9:16, the pin's tip this share down the graphics box (it was at 0.87)


def view_centre_lat(site_lat: float, width: int, height: int, span: float, area: bool = False
                    ) -> tuple[float, float]:
    """(the latitude the resting view is centred on, px the site then stands over the frame's centre) for a
    locator at `span` degrees across: on 9:16 the site MAP_PIN_Y_SHARE down the graphics box (the view centred
    south of it, never past 89.5 degrees), on 16:9 and for a region's area at the centre."""
    if width >= height or area:
        return site_lat, 0.0
    tb = get_text_box(width, height)
    want = height / 2 - (tb[1] + MAP_PIN_Y_SHARE * (tb[3] - tb[1]))
    if want <= 0:
        return site_lat, 0.0
    kf = width / span
    centre = max(-89.5, min(89.5, site_lat - want / kf))
    return centre, (site_lat - centre) * kf


def snap_onto(lon: float, lat: float, country: geo.Country, inset_km: float = 0.15) -> tuple[float, float] | None:
    """Round 16 (review 3): (lon, lat) of the point of `country`'s ground nearest the site, `inset_km` inside its
    outline (halved until inside), or None where it has no ring. O(vertices)."""
    coslat = max(0.2, math.cos(math.radians(lat)))
    best: tuple[float, tuple[float, float], Sequence[tuple[float, float]]] | None = None
    for poly in country["polygons"]:
        ring = poly[0]
        if len(ring) < 3:
            continue
        for sh in (0.0, 360.0, -360.0):
            nlat, nlon = _nearest_on_ring(lat, lon + sh, ring)
            d = math.hypot((nlon - lon - sh) * coslat, nlat - lat)
            if best is None or d < best[0]:
                best = (d, (nlat, nlon - sh), ring)
    if best is None:
        return None
    (nlat, nlon), ring = best[1], best[2]
    vx, vy = (nlon - lon) * coslat, nlat - lat
    n = math.hypot(vx, vy) or 1.0
    step = inset_km / 111.2
    for _ in range(6):
        out = (nlon + vx / n * step / coslat, nlat + vy / n * step)
        if any(geo.point_in_polygons(out[0] + sh, out[1], [[ring]]) for sh in (0.0, 360.0, -360.0)):
            return out
        step /= 2
    return nlon, nlat


def _one_edit(a: str, b: str) -> bool:
    """Whether `a` and `b` differ by exactly one substituted letter (MACAO, MACAU)."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def home_title_form(name: str, title: str, home: geo.Country | None) -> str:
    """Round 16 (review 11 and 7): the home's name as set beside the site's title. A name that only repeats the
    title (PALMYRA ATOLL under "Palmyra Atoll") gives way to its sovereign's written-out name (UNITED STATES OF
    AMERICA: the entity's own SOVEREIGNT row, never a name looked up); a one-letter variant of a word of the title
    (MACAO under "... Macau") takes the title's spelling, so the two never disagree."""
    if not name or home is None:
        return name
    t_words = re.findall(r"[A-Z0-9']+", geo.fold(title).upper())
    if " ".join(t_words) == " ".join(re.findall(r"[A-Z0-9']+", name)):
        sov = geo.fold(str(home.get("sovereign") or "")).lower()
        if sov and sov != str(home.get("key") or ""):
            for c in geo.countries():
                if str(c.get("key") or "") == sov:
                    log.info("Map: the home's name %r repeats the site's title; named by its sovereign, %s.",
                             name, c.get("full_name") or c.get("display"))
                    return str(c.get("full_name") or c.get("display") or name).upper()
        return name
    if " " not in name:
        for w in t_words:
            if len(w) >= 4 and _one_edit(w, name):
                log.info("Map: the home's name %r set as the title spells it, %r.", name, w)
                return w
    return name


def locator_span(lat0: float, lon0: float, home: geo.Country | None, width: int, height: int,
                 default: float = LOCATOR_SPAN, polar: bool = False) -> float:
    """The close-up's span (round 6, framing): of the spans from 40 / LOCATOR_MAX_ZOOM degrees (the camera's wide
    view is at least 40 degrees, and its base holds its detail to that zoom) to 2.2x `default` (5 % log steps), the
    one whose home fill covers nearest LOCATOR_HOME_TARGET of the graphics box, inside LOCATOR_HOME_SHARE with sea or
    neighbouring land at least LOCATOR_OTHER_MIN of it (ties to the nearest `default`): Derinkuyu 9:16 zooms in
    about 1.5x, Bijapur 16:9 out to about 0.75x. Where no span keeps the rule (a small island: Malta), the span
    nearest it (logged)."""
    if home is None or not geo.available():
        return default
    box = get_text_box(width, height)
    lo_span = 40.0 / LOCATOR_MAX_ZOOM
    best, best_key = default, (math.inf, math.inf)
    j0 = math.floor(math.log(lo_span / default) / math.log(1.05))
    for j in range(j0, 17):
        span = max(lo_span, default * 1.05 ** j)
        pv = PolarProjection(lon0, lat0, width / 2, height / 2 - polar_lift(width, height), width / span) \
            if polar else None        # round 11: a polar view framed on its own projection
        land_m, home_m = view_masks(width, height, lon0, view_centre_lat(lat0, width, height, span)[0], span, home,
                                    down=8, polar=pv)
        share = home_share(home_m, box, 8)
        neigh = home_share(land_m & ~home_m, box, 8)
        lo, hi = LOCATOR_HOME_SHARE
        hi = min(hi, 1 - LOCATOR_OTHER_MIN)
        # round 6 review: neighbouring land in the box too, room to name two of them (spec E; the close-ups named
        # none: Turkey filled the box from sea to sea); a span short of it is ranked by how far short
        ok = lo <= share <= hi and neigh >= LOCATOR_NEIGHBOUR_MIN
        # inside the rule: nearest the target (then the default); outside it: nearest the rule itself
        # round 10 (review 1, R6): the neighbour-land shortfall counts only where the home's share is not under
        # the rule; a home too small for it is ranked by its own shortfall alone (else the shortfall
        # fell as the span grew, and the widest span ranked best: Barbados framed at 39 degrees, a pixel)
        home_miss = max(0.0, lo - share, share - hi)
        miss = 0.0 if ok else (home_miss + (max(0.0, LOCATOR_NEIGHBOUR_MIN - neigh) if share >= lo else 0.0))
        # round 9: outside it, a fill too small for the rule ties (to 0.1 % of the box: a small island's share is
        # raster noise, and its smoothed outline moved Malta's) to the closest span, which shows it the largest
        key = ((miss, abs(share - LOCATOR_HOME_TARGET) + 0.005 * abs(j)) if ok
               else (round(miss, 3), span if share < lo else 0.005 * abs(j)))
        if key < best_key:
            best, best_key = span, key
    if best_key[0] > 0:
        log.info("Map framing: no span puts the home fill inside %s of the box; %.1f degrees.", LOCATOR_HOME_SHARE, best)
    return round(best, 4)


HOME_ZOOM_STEP = 0.8            # round 16 (review 1): the span's factor a time a home holding its name nowhere is
HOME_ZOOM_MIN = 0.5             # framed closer by, to this share of its span at the most
HOME_ZOOM_FILL_PX = 20000       # px (at 1080) of home fill in the frame for that (a country, never a speck)
LOCATOR_EMPTY_LAND = 0.03       # round 7 (R6): a close-up with less land than this share of the box is empty sea
LOCATOR_ISLAND_SHARE = 0.30     # and its island is then framed to this share of the box's width (or height)
ISLAND_READ_PX = 120            # round 10 (R6): px, a site's island narrower than this on a close-up too small for the
                                # framing rule is framed as an island too, though other land is in view
LOCATOR_ISLAND_MIN_SPAN = 0.12  # degrees: the closest such a close-up goes (round 9, R6: 0.4 held Nauru at 140 px)
# Round 8 (review 1): the island zoom's floor is the outline's own detail. Natural Earth 1:50m draws Pohnpei with 7
# points and Rapa Nui with 4, so at 0.8 degrees Pohnpei was a crude heptagon with Nan Madol's pin 40 px off it.
# Round 15 (coordinator directive 3, the outline quality gate): the close-up draws the 1:10m data, and an outline is
# never smoothed or made up (geo.smoothed_islands is gone): it is framed no closer than its own vertices hold.
ISLAND_VERTEX_PX_RAW = 28.0     # px (at 1080): the most the site's outline's mean source edge may be on the frame
ISLAND_PART_PX = 300.0          # round 15: px (at 1080) under which an island of a larger home is framed on itself
ISLAND_PART_SHARE = 0.25        # and the most of the home's ground that island may be
ISLAND_SNAP_KM = 5.0            # km: a site at most this far off its island's outline (the data's own error) is
                                # moved onto it; the marker then stands on the island it is on
ISLAND_SNAP_IN_PX = 4.0         # px inside the outline it is moved to
ISLAND_OFF_PX = 24.0            # round 14: px (at 1080) the most a site off all drawn ground may stand off it


def _site_island(lat0: float, lon0: float, home: geo.Country
                 ) -> tuple[float, list[tuple[float, float]]] | None:
    """(distance in degrees of latitude, ring) of the home's outer ring nearest the site (0 inside it), lon scaled
    by cos(lat); None with no ring."""
    coslat = max(0.2, math.cos(math.radians(lat0)))
    best: tuple[float, list[tuple[float, float]]] | None = None
    for poly in home["polygons"]:
        ring = poly[0]
        xs, ys = [x for x, _y in ring], [y for _x, y in ring]
        dx = max(min(xs) - lon0, 0.0, lon0 - max(xs)) * coslat
        dy = max(min(ys) - lat0, 0.0, lat0 - max(ys))
        d = math.hypot(dx, dy)
        if best is None or d < best[0]:
            best = (d, ring)
    if best is None:
        return None
    ring = best[1]
    if geo.point_in_polygons(lon0, lat0, [[ring]]):
        return 0.0, ring
    near = _nearest_on_ring(lat0, lon0, ring)
    return math.hypot((near[1] - lon0) * coslat, near[0] - lat0), ring


def _nearest_on_ring(lat0: float, lon0: float, ring: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """(lat, lon) of the point of `ring` nearest the site (lon scaled by cos(lat)). O(n)."""
    coslat = max(0.2, math.cos(math.radians(lat0)))
    best, out = math.inf, (lat0, lon0)
    for (ax, ay), (bx, by) in zip(ring, list(ring[1:]) + [ring[0]]):
        ax_, bx_, px_ = ax * coslat, bx * coslat, lon0 * coslat
        vx, vy = bx_ - ax_, by - ay
        L = vx * vx + vy * vy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((px_ - ax_) * vx + (lat0 - ay) * vy) / L))
        qx, qy = ax_ + t * vx, ay + t * vy
        d = math.hypot(qx - px_, qy - lat0)
        if d < best:
            best, out = d, (qy, qx / coslat)
    return out


COUNTRY_SMALL_FIT = 1.3       # round 12: a small country's width over its name's, framed for it to hold the name
COUNTRY_NAME_EM = 0.95         # round 12: a tracked capital's advance, in em, for a name's width before it is set
ISLAND_MIN_AREA_PX2 = 1600.0    # round 12: px2 (at 1080) of ground under which an island close-up reads as empty sea
ISLAND_CONTEXT_SPAN = 3.5       # and the span, in multiples of the nearest other home ground's distance, it then takes


def pin_island_cover(rings: Sequence[Sequence[tuple[float, float]]], lat0: float, lon0: float, span: float,
                     width: int, height: int) -> float:
    """Round 12 (coordinator directive 4): the share of the island ground `rings` (outer rings, degrees) that the
    pin's box covers with its tip at (lon0, lat0) on a frame `span` degrees across: the ground under the box
    (rasterised on the box alone) over the rings' whole area, both in frame px; 0 with no area."""
    import numpy as np
    k = min(width, height) / 1080
    kpx = width / span
    coslat = max(0.2, math.cos(math.radians(lat0)))
    area = sum(geo.ring_area([(x * coslat, y) for x, y in r]) for r in rings) * kpx * kpx
    if area <= 0:
        return 0.0
    r = PIN_HEAD_D / 2 * k + PIN_STROKE * k
    x0, y0 = -r, -(PIN_HEIGHT + PIN_STROKE) * k
    bw, bh = max(1, math.ceil(2 * r)), max(1, math.ceil(-y0 + PIN_SHADOW[1] * k))
    im = Image.new("L", (bw, bh), 0)
    dd = ImageDraw.Draw(im)
    for ring in rings:
        pts = [((x - lon0) * coslat * kpx - x0, -(y - lat0) * kpx - y0) for x, y in ring]
        if len(pts) >= 3:
            dd.polygon(pts, fill=255)
    return float((np.asarray(im) > 127).sum()) / area


VIEW_EDGE_PX_OWN = 18.0         # round 16 (review 8): px (at 1080), the median drawn source edge of the home's outlines
VIEW_EDGE_PX_OTHER = 22.0       # and of the other land's, in an island or small-country close-up at the most


def view_edge_px(lat0: float, lon0: float, home: geo.Country | None, width: int, height: int,
                 span: float) -> tuple[float, float]:
    """Round 16 (review 8): (the home's, the other land's) median source edge, in frame px, over the outline
    vertices inside the close-up at `span` (0 where it has none). O(vertices in view)."""
    import statistics
    coslat = max(0.2, math.cos(math.radians(lat0)))
    kf = width / span
    lon_half, lat_half = width / 2 / (kf * coslat), height / 2 / kf
    own: list[float] = []
    other: list[float] = []
    for c, poly in geo.view_polygons(lon0, lat0, lon_half, lat_half):
        r = poly[0]
        dst = own if c is home else other
        for (ax, ay), (bx, by) in zip(r, list(r[1:]) + [r[0]]):
            d_lon = (ax - lon0 + 180.0) % 360.0 - 180.0
            if abs(d_lon) < lon_half and abs(ay - lat0) < lat_half:
                dx = (bx - ax + 180.0) % 360.0 - 180.0
                dst.append(math.hypot(dx * coslat, by - ay) * kf)
    return (statistics.median(own) if own else 0.0), (statistics.median(other) if other else 0.0)


def island_span(lat0: float, lon0: float, home: geo.Country | None, width: int, height: int,
                span: float) -> float | None:
    """Round 7 (coordinator R6): where the close-up at `span` is empty sea (land under LOCATOR_EMPTY_LAND of the
    graphics box: Rapa Nui, 3,700 km from Chile, was a few pixels under the pin), the span that frames the site's
    own island (the home polygon nearest the site) to LOCATOR_ISLAND_SHARE of the box's width or height, whichever
    binds, but never closer than its outline's detail holds (round 8, review 1: its mean edge at most
    ISLAND_VERTEX_PX_RAW on the frame); None where the close-up has its land (logged when used)."""
    if home is None or not geo.available():
        return None
    box = get_text_box(width, height)
    land_m, home_m = view_masks(width, height, lon0, view_centre_lat(lat0, width, height, span)[0], span, home,
                                down=8)
    got = _site_island(lat0, lon0, home)
    if got is None or got[0] > 1.0:
        return None
    ring = got[1]
    coslat = max(0.2, math.cos(math.radians(lat0)))
    xs, ys = [x for x, _y in ring], [y for _x, y in ring]
    ex = (min(xs), min(ys), max(xs), max(ys))
    # round 12 (review: Bikini): the island's group, the home's rings within geo.ISLAND_GROUP_KM of the site (an
    # atoll's islets round its lagoon), is what the close-up frames; it held one islet sliver, the pin on open sea
    # round 15 (review: Thursday Island framed at 37 degrees): never with a mainland in the group (Cape York, 30 km
    # off Thursday Island, made the "group" a continent): only rings under ISLAND_PART_SHARE of the home's ground
    whole_ = sum(abs(geo.ring_area(poly[0])) for poly in home["polygons"]) or 1.0
    group = [ring] + [poly[0] for poly in home["polygons"]
                      if poly[0] is not ring and abs(geo.ring_area(poly[0])) < ISLAND_PART_SHARE * whole_
                      and geo.ring_km(poly[0], lon0, lat0) <= geo.ISLAND_GROUP_KM]
    gxs, gys = [x for r in group for x, _y in r], [y for r in group for _x, y in r]
    gex = (min(gxs), min(gys), max(gxs), max(gys))
    empty = home_share(land_m, box, 8) < LOCATOR_EMPTY_LAND
    # round 10 (review 1, R6): also a small island by other land (Barbados by the Antilles, Male in its atolls):
    # the home short of the framing rule's share and the site's island under ISLAND_READ_PX across at `span`
    # (a pin's height or two: it read as a pixel under the pin)
    isl_px = max((ex[2] - ex[0]) * coslat, ex[3] - ex[1]) * width / span
    # round 12 (review: Schengen 16:9, Luxembourg 120 px across under the site's name, unnamed): or narrower than
    # its own name at the least in-fill size (with COUNTRY_IN_PAD either side), at both sizes
    k_ = min(width, height) / 1080
    name_px = len(str(home.get("display") or "")) * COUNTRY_SMALL_PX[-1] * k_ * COUNTRY_NAME_EM + 2 * COUNTRY_IN_PAD * k_
    tiny = (home_share(home_m, box, 8) < LOCATOR_HOME_SHARE[0]
            and isl_px < max(ISLAND_READ_PX * k_, name_px))
    # round 15 (review: Bonifacio 9:16 at Europe scale, Corsica a speck under the site's name): or an island of a
    # larger home (no vertex shared with any other ring, a small share of the home's ground) under ISLAND_PART_PX
    # across: framed on itself, the rest of the home still its fill
    if not (empty or tiny) and isl_px < ISLAND_PART_PX * k_:
        own = abs(geo.ring_area(ring))
        whole = sum(abs(geo.ring_area(poly[0])) for poly in home["polygons"])
        pts_ = set(map(tuple, ring))
        shared = any(poly[0] is not ring and c["extent"][0] <= ex[2] and ex[0] <= c["extent"][2]
                     and c["extent"][1] <= ex[3] and ex[1] <= c["extent"][3]
                     and any(tuple(p_) in pts_ for p_ in poly[0])
                     for c in geo.countries() for poly in c["polygons"])
        if not shared and own < ISLAND_PART_SHARE * whole:
            tiny = True
    if not (empty or tiny):
        return None
    w_deg = max((gex[2] - gex[0]) * coslat, 2 * abs(lon0 - (gex[0] + gex[2]) / 2) * coslat, 0.02)
    h_deg = max(gex[3] - gex[1], 2 * abs(lat0 - (gex[1] + gex[3]) / 2), 0.02)
    bw_px, bh_px = box[2] - box[0], box[3] - box[1]
    # frame px per degree is width / span: the island's px across is w_deg * width / span
    # round 10: whichever binds is the farther of the two (min framed Male's atoll 0.8 of the 16:9 box's height)
    share_span = max(w_deg * width / (LOCATOR_ISLAND_SHARE * bw_px), h_deg * width / (LOCATOR_ISLAND_SHARE * bh_px))
    edges = [math.hypot((bx - ax) * coslat, by - ay) for (ax, ay), (bx, by) in zip(ring, list(ring[1:]) + [ring[0]])]
    mean_edge = sum(edges) / max(1, len(edges))
    detail_span = mean_edge * width / (ISLAND_VERTEX_PX_RAW * min(width, height) / 1080)
    out = max(LOCATOR_ISLAND_MIN_SPAN, share_span, detail_span)
    # round 11 (review: Andorra, landlocked, was logged as "the site's island"): an outline sharing a vertex with
    # another country's is a small country's, not an island's
    pts = set(map(tuple, ring))
    bordered = any(c is not home and c["extent"][0] <= ex[2] and ex[0] <= c["extent"][2] and c["extent"][1] <= ex[3]
                   and ex[1] <= c["extent"][3] and any(tuple(p) in pts for poly in c["polygons"] for p in poly[0])
                   for c in geo.countries())
    what = "small country" if bordered else "island"
    if bordered:
        # round 12 (review: Schengen 16:9): a small country is framed wide enough to hold its own name in its fill
        # at the first stepped-down size, COUNTRY_SMALL_FIT times its width across (its name was set outside, on
        # Germany's ground, and read as Germany's)
        fit_px = len(str(home.get("display") or "")) * COUNTRY_SMALL_PX[0] * k_ * COUNTRY_NAME_EM * COUNTRY_SMALL_FIT
        fit_span = (ex[2] - ex[0]) * coslat * width / max(1.0, fit_px)
        floor_ = max(LOCATOR_ISLAND_MIN_SPAN, detail_span)
        if fit_span < out and out > floor_:
            log.info("Map framing: the small country framed closer, at %.2f, to hold its name in its fill.",
                     max(floor_, fit_span))
            out = max(floor_, fit_span)
    # round 11 (coordinator directive 4): a thin island (Funafuti's atoll) framed close enough that the pin covers
    # at most half PIN_ISLAND_COVER_MAX of its drawn area (its name then sets beside it: pin_layout). Round 12
    # (review): measured as the island ground under the pin's own box at the span (pin_island_cover), so a pin in
    # an atoll's lagoon, over no ground, is no reason to zoom past the atoll (Bikini framed on one islet)
    if not bordered:
        floor = max(LOCATOR_ISLAND_MIN_SPAN, detail_span)
        cover = pin_island_cover(group, lat0, lon0, out, width, height)
        thin = out
        while cover > 0.5 * PIN_ISLAND_COVER_MAX and thin * 0.9 >= floor:
            thin *= 0.9
            cover = pin_island_cover(group, lat0, lon0, thin, width, height)
        if thin < out:
            log.info("Map framing: the pin covers much of the island's ground; framed closer, at %.2f (the pin "
                     "over %.0f %% of it).", thin, 100 * cover)
            out = thin
    # round 12 (review: Bikini): ground too small to read as an island at the span it is framed at (the 1:10m data
    # holds two islets of Bikini's ring, half a square km each: one sliver in a frame of empty sea) is framed with
    # its country's nearest other ground instead, ISLAND_CONTEXT_SPAN times its distance across, so the frame
    # says where the site is (the globe stays as its inset)
    area_px2 = sum(geo.ring_area([(x * coslat, y) for x, y in r]) for r in group) * (width / out) ** 2
    if area_px2 < ISLAND_MIN_AREA_PX2 * (min(width, height) / 1080) ** 2:
        others = [geo.ring_km(poly[0], lon0, lat0) for poly in home["polygons"]
                  if not any(poly[0] is r for r in group)]
        near = min([d for d in others if d > 0] or [math.inf])
        ctx = ISLAND_CONTEXT_SPAN * near / 111.2 * width / max(1.0, box[2] - box[0])
        if math.isfinite(ctx) and out < ctx < span:
            log.info("Map framing: the site's %s is %.0f px2 at %.2f degrees, too small to read; framed with its "
                     "country's nearest ground, %.0f km off, at %.2f.", what, area_px2, out, near, ctx)
            out = ctx
    # round 14 (coordinator directive 2; review: Rangiroa, a dot on empty sea at a 20 km scale; Cape Horn, the dot
    # 53 px off the drawn islands): where the data does not hold the site's own island (the site more than
    # ISLAND_SNAP_KM off the nearest drawn ground), the close-up goes no closer than that offset reads as at most
    # ISLAND_OFF_PX on the frame, so the dot stands at the best outline the data has, never floating off it
    d_off = got[0] * 111.2
    if not bordered and d_off > ISLAND_SNAP_KM:
        off_span = d_off / 111.2 * width / (ISLAND_OFF_PX * k_)
        if off_span > out:
            log.info("Map framing: the data does not hold the site's own island (%.1f km off the nearest drawn "
                     "ground); framed no closer than %.2f, where that reads as %d px.", d_off, off_span,
                     round(ISLAND_OFF_PX * k_))
            out = off_span
    # round 16 (review 8, directive 3: Saint-Pierre's 18-point outline, Kwajalein's leaf, Macau's straight-edged
    # China and Hengqin): every outline in the close-up, the site's own and its neighbours', drawn no coarser than
    # VIEW_EDGE_PX_OWN / VIEW_EDGE_PX_OTHER px a source edge (the median, so one long straight border does not
    # count); a close-up coarser is framed wider until it is (the honest fallback: the dot at a scale where the
    # shape holds, the globe with it)
    for _ in range(4):
        own_e, other_e = view_edge_px(lat0, lon0, home, width, height, out)
        need = max(own_e / (VIEW_EDGE_PX_OWN * k_), other_e / (VIEW_EDGE_PX_OTHER * k_))
        if need <= 1.02:
            break
        log.info("Map framing: at %.2f degrees the outlines in view are drawn %.0f px (the site's) and %.0f px (the "
                 "others') a source edge; framed wider, at %.2f, where the data's detail holds.", out, own_e,
                 other_e, out * need)
        out *= need
    if out >= span:
        return None
    log.info("Map framing: the close-up at %.1f degrees %s; framed on the site's %s at %.2f%s.", span,
             "is empty sea" if empty else f"shows the site's {what} {isl_px:.0f} px across", what, out,
             " (its outline's detail holds no closer)" if detail_span > share_span else "")
    return round(out, 4)


def island_site(lat0: float, lon0: float, home: geo.Country | None, width: int, height: int,
                span: float) -> tuple[float, float]:
    """Round 8 (review 1): the point the island close-up is centred on and its marker stands at. A site off its
    island's outline by no more than ISLAND_SNAP_KM (the 1:50m outline's own error: Nan Madol is on reef islets off
    Pohnpei's coast) is moved ISLAND_SNAP_IN_PX inside it, so the pin stands on the island it is on; else the site
    itself (logged where it stays off the outline)."""
    if home is None:
        return lat0, lon0
    got = _site_island(lat0, lon0, home)
    if got is None or got[0] == 0.0:
        return lat0, lon0
    d_deg, ring = got
    if d_deg * 111.2 > ISLAND_SNAP_KM:
        log.info("Map framing: the site is %.1f km off its island's outline; the marker stays on it.", d_deg * 111.2)
        return lat0, lon0
    coslat = max(0.2, math.cos(math.radians(lat0)))
    nlat, nlon = _nearest_on_ring(lat0, lon0, ring)
    vx, vy = (nlon - lon0) * coslat, nlat - lat0
    n = math.hypot(vx, vy) or 1.0
    step = ISLAND_SNAP_IN_PX * min(width, height) / 1080 * span / width     # degrees of latitude
    out = (nlat + vy / n * step, nlon + vx / n * step / coslat)
    if not geo.point_in_polygons(out[1], out[0], [[ring]]):
        out = (nlat, nlon)
    log.info("Map framing: the site is %.1f km off its island's outline; the marker moved onto it.",
             d_deg * 111.2)
    return out


def _integral(mask: BoolArray) -> "npt.NDArray[np.int64]":
    """The summed-area table of a bool (or 0/1) mask, padded by a row and a column of zeros: any box's count of set
    pixels in O(1) (_box_sum)."""
    import numpy as np
    out: npt.NDArray[np.int64] = np.pad(np.asarray(mask).astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return out


def _box_sum(ii: "npt.NDArray[np.int64]", b: Box) -> int:
    """The set pixels of the mask `ii` was made from (_integral) inside box `b` (clipped to it)."""
    h, w = ii.shape[0] - 1, ii.shape[1] - 1
    x0, y0 = max(0, min(w, int(b[0]))), max(0, min(h, int(b[1])))
    x1, y1 = max(0, min(w, math.ceil(b[2]))), max(0, min(h, math.ceil(b[3])))
    if x1 <= x0 or y1 <= y0:
        return 0
    return int(ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0])


@dataclass(frozen=True)
class CountryName:
    """The home country's name on a locator (A.4): its text, the place it is set at ((x, y), anchor "mm"), its font
    size, fill and ink box, whether it sits inside the fill, and the INK opacity it took there."""
    text: str
    xy: tuple[float, float]
    px: int
    fill: tuple[int, int, int]
    box: Box
    inside: bool
    ink: float
    halo: float


@dataclass(frozen=True)
class CountrySpot:
    """Where the home country's name goes, on geometry alone: its centre ("mm"), ink box, font px and whether it is
    set inside the fill (INK in the gold) or outside it (STONE_LT on the sea)."""
    xy: tuple[float, float]
    box: Box
    px: int
    inside: bool


COUNTRY_SMALL_PX = (24, 22, 20)   # inside the fill at these before any outside place (round 9, R5; logged)
COUNTRY_IN_PAD = 6                # round 9: px the name's box keeps inside its fill's edge
COUNTRY_SMALL_LAB_SHARE = 0.6     # and a stepped-down name keeps this share of the site name's distance from the marker
COUNTRY_LEVEL_REACH = 160         # and px either side of the site's name its row stays clear for
COUNTRY_LEVEL_MIN_REACH = 60      # round 11 (directive 5): and at least, where the fill has no other place
COUNTRY_NEAR_PX = 260             # px: an outside name keeps within this of the fill (of the anchor, with no fill)


class HomeRings(list):  # type: ignore[type-arg]
    """Round 14 (review: the home fill cancelled itself): home_rings' rings, with `groups`, the index of the polygon
    each ring belongs to and `outer`, whether it is that polygon's outer ring, so a fill drawn from them is the union
    of its polygons (each its outer ring less its holes). Rasterised even-odd as one set, a 1:10m polygon drawn over
    the 1:50m one it overlaps cancelled it: South Africa's 46,807 px fill left 300 px and its name went to sea."""
    groups: list[int]
    outer: list[bool]
    allow: "BoolArray | None" = None     # round 14: the frame px the name may sit on (allowed_home_parts)


def allowed_home_parts(home: BoolArray | None, anchor: tuple[float, float], k: float = 1.0) -> BoolArray | None:
    """Round 14 (review: CHILE dropped at Punta Arenas 16:9, every place tried on Tierra del Fuego): the parts of
    the home's fill its name may sit in, by home_name_fault's own rule: the site's part, any part at least as large,
    and any part that runs off the frame; None (all of it) where no part is under or near the site."""
    import numpy as np
    comps = _components(home)
    if comps is None:
        return None
    lab, area = comps
    site = _site_component(comps, anchor, NAME_SITE_REACH_PX * k)
    if not site:
        return None
    edge = set(np.unique(lab[0, :])) | set(np.unique(lab[-1, :])) | set(np.unique(lab[:, 0])) | set(np.unique(lab[:, -1]))
    ok = [v for v in range(1, len(area)) if v == site or area[v] >= area[site] or v in edge]
    return np.isin(lab, ok)


def rings_fill(rings: Sequence[Sequence[tuple[float, float]]], width: int, height: int) -> BoolArray:
    """The frame mask `rings` fill: for HomeRings, each polygon painted in turn (its outer ring, then its holes
    cleared) and the polygons OR-ed, so overlapping polygons never cancel; for a plain ring list, even-odd (holes
    and parts alike). O(w h) per polygon."""
    import numpy as np
    mask = np.zeros((height, width), bool)
    groups = getattr(rings, "groups", None)
    outer = getattr(rings, "outer", None)
    if groups is None or outer is None or len(groups) != len(rings):
        for ring in rings:
            if len(ring) < 3:
                continue
            im = Image.new("1", (width, height), 0)
            ImageDraw.Draw(im).polygon([(float(x_), float(y_)) for x_, y_ in ring], fill=1)
            mask ^= np.asarray(im, bool)
        return mask
    by: dict[int, list[tuple[bool, Sequence[tuple[float, float]]]]] = {}
    for g, o, ring in zip(groups, outer, rings):
        by.setdefault(g, []).append((o, ring))
    for parts in by.values():
        im = Image.new("1", (width, height), 0)
        dd = ImageDraw.Draw(im)
        for o, ring in sorted(parts, key=lambda t: not t[0]):
            if len(ring) >= 3:
                dd.polygon([(float(x_), float(y_)) for x_, y_ in ring], fill=1 if o else 0)
        mask |= np.asarray(im, bool)
    allow = getattr(rings, "allow", None)
    if allow is not None and allow.shape == mask.shape:
        mask &= allow
    return mask


def fill_pole(rings: Sequence[Sequence[tuple[float, float]]], precision: float = 4.0
              ) -> tuple[float, float, float] | None:
    """The pole of inaccessibility (geo.polylabel) of the fill `rings` bound: for HomeRings, of its largest polygon
    alone (round 14: overlapping polygons, even-odd, put it in the cancelled ground between them)."""
    groups = getattr(rings, "groups", None)
    if groups is None or len(groups) != len(rings):
        return geo.polylabel(rings, precision=precision)
    by: dict[int, list[Sequence[tuple[float, float]]]] = {}
    for g, ring in zip(groups, rings):
        by.setdefault(g, []).append(ring)
    if not by:
        return None
    best = max(by.values(), key=lambda rs: geo.ring_area(rs[0]) if len(rs[0]) >= 3 else 0.0)
    return geo.polylabel(best, precision=precision)


def home_rings(home: geo.Country, to_frame: Callable[[float, float], tuple[float, float]],
               clip: Box) -> list[list[tuple[float, float]]]:
    """The home country's rings (outer and holes) on the frame, cut to `clip`, as HomeRings (round 14: with the
    polygon each belongs to, for rings_fill)."""
    out = HomeRings()
    out.groups, out.outer = [], []
    if isinstance(to_frame, PolarProjection):
        polys = _view_polygons(to_frame, clip[2], clip[3])
    else:
        lo0, la0, lo_h, la_h = _frame_view(lambda x, y: (to_frame(x, y)[0] - clip[0], to_frame(x, y)[1] - clip[1]),
                                           round(clip[2] - clip[0]), round(clip[3] - clip[1]))
        polys = geo.view_polygons(lo0, la0, lo_h, la_h)
    for gi, (c, poly) in enumerate(polys):     # round 8: across the antimeridian too
        if c is not home:
            continue
        for ri, ring in enumerate(poly):
            cut = geo.clip_ring([to_frame(x, y) for x, y in ring[:: max(1, len(ring) // 400)]], clip)
            if len(cut) >= 3 and geo.ring_area(cut) > 4:
                out.append(cut)
                out.groups.append(gi)
                out.outer.append(ri == 0)
    return out


def country_name_place(name: str, rings: list[list[tuple[float, float]]], anchor: tuple[float, float],
                       label: Box, keep_out: Sequence[Box], width: int, height: int,
                       px: int | None = None, reasons: list[str] | None = None,
                       anchor_air: float | None = None) -> tuple[tuple[float, float], Box] | None:
    """Where the country's name fits inside its frame-clipped fill (A.4; round 9, R5): its box, COUNTRY_IN_PAD inside
    the edge, wholly in the clipped rings' fill (rings_fill), its centre COUNTRY_ANCHOR_AIR from the anchor and its box
    COUNTRY_LABEL_AIR from the site's label (and out of `keep_out`); of those places the one that keeps the most
    clearance from the edge, ties to the pole of inaccessibility (polylabel). None where it does not fit (or the
    clip leaves nothing), so the caller tries a smaller size, then outside. `px`: the size (COUNTRY_PX / _WIDE by
    default). O(w h) on the fill's distance transform and a 4 px grid."""
    tall = height > width
    k = 1.0 if tall else min(width / 1920, height / 1080)
    size = round((COUNTRY_PX if tall else COUNTRY_PX_WIDE) * k) if px is None else px
    font = get_font("Montserrat", size, weight=600)
    track = font.size * COUNTRY_TRACK
    rel = ink_box((0.0, 0.0), name, font, "mm", track)
    if rel is None or not rings:
        return None
    import cv2
    import numpy as np
    air = COUNTRY_LABEL_AIR * k
    # round 9 (review 1, R5): the name fits where its box, COUNTRY_IN_PAD inside the edge, lies wholly in the
    # clipped fill (an inscribed circle half its width shut AFGHANISTAN and SWITZERLAND out of fills that held them
    # with room to spare); of those places, the one whose box keeps the most clearance from the edge, ties to the
    # pole of inaccessibility. Each place tested in O(1) on its integral image.
    # Round 14: the union of the home's polygons (rings_fill), never their even-odd sum
    mask = rings_fill(rings, width, height)
    if not mask.any():
        return None
    pad = max(2, round(COUNTRY_IN_PAD * k))
    bw_, bh_ = math.ceil(rel[2] - rel[0]) + 2 * pad, math.ceil(rel[3] - rel[1]) + 2 * pad
    if bw_ >= width or bh_ >= height:
        return None
    dt = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    # the least edge distance under a box of the name's size, at each box's top-left corner
    clear_ = cv2.erode(dt, np.ones((bh_, bw_), np.uint8), anchor=(0, 0), borderType=cv2.BORDER_CONSTANT,
                       borderValue=0)
    step = max(2, round(4 * k))
    oy0, ox0 = rel[1] - pad, rel[0] - pad           # a box's top-left from the name's centre
    ys = np.arange(max(0.0, -oy0), height - bh_ - oy0, step)
    xs = np.arange(max(0.0, -ox0), width - bw_ - ox0, step)
    if not len(xs) or not len(ys):
        return None
    gx, gy = np.meshgrid(xs, ys)
    cx_i, cy_i = (gx + ox0).astype(int), (gy + oy0).astype(int)
    score = clear_[cy_i, cx_i].astype(np.float64)
    ok = score >= 1.0
    ok &= np.hypot(gx - anchor[0], gy - anchor[1]) >= (COUNTRY_ANCHOR_AIR * k if anchor_air is None else anchor_air)
    # round 9: and its box no nearer the marker than the site's name is (the name nearest the marker is the site's)
    d_lab = math.hypot(max(label[0] - anchor[0], 0.0, anchor[0] - label[2]), max(label[1] - anchor[1], 0.0, anchor[1] - label[3]))
    bdx = np.maximum(np.maximum(gx + rel[0] - anchor[0], anchor[0] - (gx + rel[2])), 0.0)
    bdy = np.maximum(np.maximum(gy + rel[1] - anchor[1], anchor[1] - (gy + rel[3])), 0.0)
    # (a name stepped down to fit a small fill, a tier under the site's, may sit to 0.6 of that: St Gall's fill is
    # 146 px tall in the box, the pin in it)
    base_px = round((COUNTRY_PX if tall else COUNTRY_PX_WIDE) * k)
    ok &= np.hypot(bdx, bdy) >= (d_lab if size >= base_px else COUNTRY_SMALL_LAB_SHARE * d_lab)
    fits = ok.copy()
    for ob, grow in [(label, air)] + [(b, 0.0) for b in keep_out]:
        ok &= ~((gx + rel[0] < ob[2] + grow) & (gx + rel[2] > ob[0] - grow)
                & (gy + rel[1] < ob[3] + grow) & (gy + rel[3] > ob[1] - grow))

    def off_level(reach: float) -> "npt.NDArray[np.bool_]":
        level = (label[0] - reach, label[1], label[2] + reach, label[3])
        return ~((gx + rel[0] < level[2] + air / 2) & (gx + rel[2] > level[0] - air / 2)
                 & (gy + rel[1] < level[3] + air / 2) & (gy + rel[3] > level[1] - air / 2))
    # round 9: never level with the site's name beside it either ("TURKEY  GOBEKLI TEPE" read as one line).
    # Round 11 (coordinator directive 5): that row's reach is COUNTRY_LEVEL_REACH where the fill has another
    # place; where it has none, COUNTRY_LEVEL_MIN_REACH (the name then a clear gap off the site's name, in its own
    # face and size, not on its line: Bratislava's fill right of the castle's name held SLOVAKIA with 40 px of air
    # and the full reach set it outside on Hungary)
    full = ok & off_level(COUNTRY_LEVEL_REACH * k)
    if not full.any():
        near_ok = ok & off_level(COUNTRY_LEVEL_MIN_REACH * k)
        if near_ok.any():
            log.info("Map: the country's name set in its fill level with the site's name, at least %.0f px off it "
                     "(the fill has no other place).", COUNTRY_LEVEL_MIN_REACH * k + air / 2)
        full = near_ok
    ok = full
    if not ok.any():
        if reasons is not None:
            reasons.append("kept out" if fits.any() else "small")
        return None
    pole = fill_pole(rings)
    near = np.hypot(gx - pole[0], gy - pole[1]) if pole else np.zeros_like(gx)
    best = np.argmax(np.where(ok, score * 1000.0 - np.minimum(near, 999.0), -np.inf))
    x, y = float(gx.flat[best]), float(gy.flat[best])
    box = (rel[0] + x, rel[1] + y, rel[2] + x, rel[3] + y)
    return (x, y), box


COUNTRY_OUT_NB_AIR = 90          # round 10 (review 2): px every neighbour's name keeps from an outside home name (THAILAND
                                 # sat on Laos 27 px under LAOS, and the pair read as two names for Laos)


def _grow(b: Box, by: float) -> Box:
    return (b[0] - by, b[1] - by, b[2] + by, b[3] + by)


def _frame_view(to_frame: Callable[[float, float], tuple[float, float]], width: int, height: int,
                pad: float = 2.0) -> tuple[float, float, float, float]:
    """(lon0, lat0, lon_half, lat_half) of the frame a linear lon/lat-to-frame map `to_frame` shows, `pad` degrees
    wider each way (for geo.view_polygons)."""
    ox, oy = to_frame(0.0, 0.0)
    bx = to_frame(1.0, 0.0)[0] - ox or 1e-9
    by = to_frame(0.0, 1.0)[1] - oy or -1e-9
    la, lb = (0 - ox) / bx, (width - ox) / bx
    ya, yb = (0 - oy) / by, (height - oy) / by
    return (la + lb) / 2, (ya + yb) / 2, abs(lb - la) / 2 + pad, abs(yb - ya) / 2 + pad


def view_country_ids(width: int, height: int, to_frame: Callable[[float, float], tuple[float, float]],
                     home: geo.Country | None, down: int = 2) -> tuple["npt.NDArray[np.uint16]", int]:
    """Round 7 (R5): each frame pixel's country on the resting view at 1/`down` resolution (0 the sea, 1.. the
    countries in view) and the home country's id (0 with none)."""
    import numpy as np
    w, h = max(1, width // down), max(1, height // down)
    im = Image.new("I;16", (w, h), 0)
    d = ImageDraw.Draw(im)
    home_id = 0
    cids: dict[int, int] = {}
    for c, poly in _view_polygons(to_frame, width, height):   # round 8: wrapped, enclaves last
        cid = cids.setdefault(id(c), len(cids) + 1)
        if c is home:
            home_id = cid
        pts_ = [(x / down, y / down) for x, y in (to_frame(lo, la) for lo, la in poly[0])]
        d.polygon(pts_, fill=cid)
        if isinstance(to_frame, PolarProjection):
            _close_seams([d], poly[0], pts_, cid, 2)
        for hole in poly[1:]:
            d.polygon([(x / down, y / down) for x, y in (to_frame(lo, la) for lo, la in hole)], fill=0)
    out: npt.NDArray[np.uint16] = np.asarray(im, dtype=np.uint16)
    return out, home_id


def country_outside_place(name: str, home: BoolArray | None, anchor: tuple[float, float], obstacles: Sequence[Box],
                          width: int, height: int, aim: tuple[float, float] | None = None,
                          land: BoolArray | None = None, label: Box | None = None,
                          ids: tuple["npt.NDArray[np.uint16]", int] | None = None,
                          ) -> tuple[tuple[float, float], Box] | None:
    """The name set outside its fill (A.4 fallback). Round 15 (coordinator directive 2, LEADER OR DROP): only over
    open water: its box, grown COUNTRY_OUT_AIR, on no land of any size (the home's or any other: the tiers that let
    it sit on a neighbour's land, as RUSSIA did on Lithuania, are gone), within COUNTRY_NEAR_PX of the home's ground
    (of the anchor with no fill) and nearer that ground than any other land; inside the text box, its centre
    COUNTRY_ANCHOR_AIR from the anchor (then, for a fill a few px across, only MARKER_LABEL_CLEAR), its box
    COUNTRY_LABEL_AIR from the site's label and MARKER_LABEL_CLEAR from `obstacles`. Of those places the one
    nearest the home's ground, then nearest `aim`; it is drawn with a leader to that ground (ground_leader). None
    where no place is. `ids` is unused (kept for callers). O(w h) over a 6 px grid, each box tested in O(1) on
    summed-area tables."""
    import cv2
    import numpy as np
    tall = height > width
    k = 1.0 if tall else min(width / 1920, height / 1080)
    font = get_font("Montserrat", round(COUNTRY_OUT_PX * k), weight=600)
    track = font.size * COUNTRY_TRACK
    rel = ink_box((0.0, 0.0), name, font, "mm", track)
    if rel is None:
        return None
    air = max(1, round(COUNTRY_OUT_AIR * k))
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * air + 1, 2 * air + 1))
    ground = np.zeros((height, width), bool) if land is None else np.asarray(land, bool).copy()
    has_home = home is not None and bool(np.asarray(home).any())
    if has_home:
        ground |= np.asarray(home, bool)
    ii_land = _integral(cv2.dilate(ground.astype(np.uint8), disc) > 0)
    if has_home:
        h8 = np.asarray(home, bool)
        d_home = cv2.distanceTransform((~h8).astype(np.uint8), cv2.DIST_L2, 5)
        other = ground & ~h8
        d_other = cv2.distanceTransform((~other).astype(np.uint8), cv2.DIST_L2, 5) if other.any() else None
    else:
        d_home = d_other = None
    tb = get_text_box(width, height)
    aim = aim or anchor
    label_air = COUNTRY_LABEL_AIR * k
    near_px = COUNTRY_NEAR_PX * k

    def box_min(dt: "npt.NDArray[np.float32]", b: Box) -> float:
        x0, y0 = max(0, int(b[0])), max(0, int(b[1]))
        x1, y1 = min(width, math.ceil(b[2])), min(height, math.ceil(b[3]))
        return float(dt[y0:y1, x0:x1].min()) if x1 > x0 and y1 > y0 else math.inf

    def gather(anchor_air: float) -> list[tuple[float, float, float, float, Box]]:
        cands: list[tuple[float, float, float, float, Box]] = []
        for y in range(int(tb[1] - rel[1]), int(tb[3] - rel[3]), 6):
            for x in range(int(tb[0] - rel[0]), int(tb[2] - rel[2]), 6):
                b = (rel[0] + x, rel[1] + y, rel[2] + x, rel[3] + y)
                if _box_sum(ii_land, b):
                    continue                           # over land, or nearer it than COUNTRY_OUT_AIR
                if math.dist((x, y), anchor) < anchor_air:
                    continue
                if label is not None and LayoutConstraintSolver.boxes_intersect(b, label, label_air):
                    continue
                if any(LayoutConstraintSolver.boxes_intersect(b, o, MARKER_LABEL_CLEAR * k) for o in obstacles):
                    continue
                if d_home is not None:
                    dh = box_min(d_home, b)
                    if dh > near_px:
                        continue
                    if LEADER_TOUCH_PX * k <= dh < LEADER_MIN_PX * k:
                        continue                       # round 16: its leader would be a stub; touching or clear
                    if d_other is not None and box_min(d_other, b) + NAME_NEARER_PX * k < dh:
                        continue                       # other land nearer than its own: it would read as its
                else:
                    dh = math.dist((x, y), anchor)
                    if dh > near_px:
                        continue
                cands.append((dh, math.dist((x, y), aim), float(x), float(y), b))
        cands.sort(key=lambda c: (round(c[0] / (6 * k)), c[1]))
        return cands
    cands = gather(COUNTRY_ANCHOR_AIR * k)
    if not cands and has_home:
        # a fill a few px across (Malta) sits under the marker: the name then keeps only MARKER_LABEL_CLEAR from
        # the marker and its label (`obstacles`, `label`) (logged)
        cands = gather(0.0)
        if cands:
            log.info("Map: the country's name set by its small fill, nearer the marker than %d px.",
                     round(COUNTRY_ANCHOR_AIR * k))
    if not cands:
        return None
    _dh, _da, x, y, b = cands[0]
    return (x, y), b


def country_spot(name: str, rings: list[list[tuple[float, float]]], home: BoolArray | None, land: BoolArray | None,
                 anchor: tuple[float, float], label: Box, keep_out: Sequence[Box], obstacles: Sequence[Box],
                 width: int, height: int, skip_inside: bool = False,
                 ids: tuple["npt.NDArray[np.uint16]", int] | None = None,
                 inside_only: bool = False) -> CountrySpot | None:
    """The country's name's place (A.4), on geometry: inside its fill at COUNTRY_PX (country_name_place); where it
    does not fit there, outside it at COUNTRY_OUT_PX (country_outside_place: 12 px off its border on the sea or the
    land next to it, nearer the home than any third country); where no such place is, inside the fill at the COUNTRY_SMALL_PX steps (logged); else None (logged, FM1:
    no crash). `skip_inside`: straight to the outside place (the fill measured too dark for any ink)."""
    tall = height > width
    k = 1.0 if tall else min(width / 1920, height / 1080)
    px0 = round((COUNTRY_PX if tall else COUNTRY_PX_WIDE) * k)
    if not skip_inside:
        why: list[str] = []
        got = country_name_place(name, rings, anchor, label, keep_out, width, height, px0, reasons=why)
        if got is not None:
            return CountrySpot(got[0], got[1], px0, True)
        # round 9 (R5): inside the fill at a reduced size wherever it fits there, before any outside place
        for px in COUNTRY_SMALL_PX:
            if round(px * k) >= px0:
                continue
            got = country_name_place(name, rings, anchor, label, keep_out, width, height, round(px * k), reasons=why)
            if got is not None:
                log.info("Map: the country's name set in its fill at %d px (%d %s).", round(px * k), px0,
                         "is kept out by the marker and its name" if why[:1] == ["kept out"] else "does not fit")
                return CountrySpot(got[0], got[1], round(px * k), True)
        # round 15 (coordinator directive 2: Lesotho's 270 px fill must hold LESOTHO): then nearer the marker, to
        # COUNTRY_ANCHOR_AIR_MIN, its box kept clear of the marker and its name (`obstacles`), at every size
        for px in [px0] + [round(p_ * k) for p_ in COUNTRY_SMALL_PX if round(p_ * k) < px0]:
            got = country_name_place(name, rings, anchor, label, list(keep_out) + list(obstacles), width, height,
                                     px, reasons=why, anchor_air=COUNTRY_ANCHOR_AIR_MIN * k)
            if got is not None:
                log.info("Map: the country's name set in its fill at %d px, nearer the marker (%d px off it at "
                         "least): its fill holds it nowhere further.", px, round(COUNTRY_ANCHOR_AIR_MIN * k))
                return CountrySpot(got[0], got[1], px, True)
        # round 11 (directive 5): the log says why: the fill too small, or the marker and the words keeping the
        # name out of a fill that would hold it
        if inside_only:          # round 14: the caller tries a shorter form inside before any outside place
            return None
        if "kept out" in why:
            log.info("Map: the country's fill would hold its name, but the marker, the site's name and their air "
                     "keep it out at every size down to %d px; set outside it.", round(COUNTRY_SMALL_PX[-1] * k))
        else:
            log.info("Map: the country's name fits its fill at no size down to %d px; set outside it.",
                     round(COUNTRY_SMALL_PX[-1] * k))
    pole = fill_pole(rings) if rings else None
    out = country_outside_place(name, home, anchor, list(keep_out) + list(obstacles), width, height,
                                pole[:2] if pole else None, land, label, ids)
    if out is not None:
        return CountrySpot(out[0], out[1], round(COUNTRY_OUT_PX * k), False)
    log.warning("Map: no place for the country's name %r inside or outside its fill.", name)
    return None


def country_name(rest: Image.Image, name: str, spot: CountrySpot, rings: list[list[tuple[float, float]]],
                 home: BoolArray | None, land: BoolArray | None, anchor: tuple[float, float], label: Box,
                 keep_out: Sequence[Box], obstacles: Sequence[Box], width: int, height: int,
                 ids: tuple["npt.NDArray[np.uint16]", int] | None = None) -> CountryName | None:
    """The home country's name (A.4) at `spot`, planned on the resting frame `rest` (where its ground is final):
    inside the fill Montserrat 600 tracked COUNTRY_TRACK, INK at COUNTRY_INK with no halo, the ink raised in steps
    (logged) until it measures brand.TEXT_CONTRAST; where no ink reaches it, outside the fill on the sea
    (country_spot, skip_inside); outside, COUNTRY_OUT_FILL on a 0.9 ink halo at COUNTRY_OUT_PX."""
    import numpy as np
    text = name.upper()
    if spot.inside:
        font = get_font("Montserrat", spot.px, weight=600)
        arr = np.asarray(rest.convert("RGB"))
        x0, y0, x1, y1 = (int(v) for v in spot.box)
        ground = np.median(arr[max(0, y0):max(1, y1), max(0, x0):max(1, x1)].reshape(-1, 3), axis=0)
        for ink in (COUNTRY_INK, 0.65, 0.75, 0.85, 0.95, 1.0):
            fill = (round(float(ground[0]) * (1 - ink) + brand.INK[0] * ink),
                    round(float(ground[1]) * (1 - ink) + brand.INK[1] * ink),
                    round(float(ground[2]) * (1 - ink) + brand.INK[2] * ink))
            plate = plan_text(rest, spot.xy, text, font, fill, "mm", track=font.size * COUNTRY_TRACK, halo_px=0)
            if plate.contrast is not None and plate.contrast >= brand.TEXT_CONTRAST:
                if ink > COUNTRY_INK:
                    log.info("Map: the country's name in its fill at %.0f %% ink (%.0f %% measured %.2f:1 there).",
                             100 * ink, 100 * COUNTRY_INK, plate.contrast)
                return CountryName(text, spot.xy, font.size, fill, plate.box, True, ink, 0.0)
        log.info("Map: no ink reaches %.1f:1 for the country's name in its fill; set outside it.", brand.TEXT_CONTRAST)
        spot2 = country_spot(text, rings, home, land, anchor, label, keep_out, obstacles, width, height, True, ids)
        if spot2 is None:
            return None
        spot = spot2
    return CountryName(text, spot.xy, spot.px, COUNTRY_OUT_FILL, spot.box, False, 0.0, 0.9)


def country_plate(rest: Image.Image, cn: CountryName) -> TextPlate:
    font = get_font("Montserrat", cn.px, weight=600)
    if cn.inside:
        return plan_text(rest, cn.xy, cn.text, font, cn.fill, "mm", track=font.size * COUNTRY_TRACK, halo_px=0)
    return plan_text(rest, cn.xy, cn.text, font, cn.fill, "mm", track=font.size * COUNTRY_TRACK, halo_px=21,
                     min_halo=cn.halo)


def _italic_mask(text: str, font: FreeTypeFont, track: float, slant: float = 0.21) -> Image.Image:
    """`text` drawn tracked into an L mask and sheared to an oblique (the bundled Montserrat has no italic); a
    "\n" sets a second row, each row centred, 1.25 x the size apart."""
    from .typography import _draw_tracked
    rows = text.split("\n")
    pitch = round(font.size * 1.25)
    w = int(max(_tracked_w(r, font, track) for r in rows) + font.size * (1 + slant) + 8)
    h = int(font.size * 1.5 + pitch * (len(rows) - 1))
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    for j, r in enumerate(rows):
        rw = _tracked_w(r, font, track)
        _draw_tracked(d, (4 + (w - 8 - font.size * slant - rw) / 2, int(font.size * 1.1) + pitch * j), r, font, 255,
                      "ls", track)
    return m.transform(m.size, Image.AFFINE, (1, slant, -slant * h * 0.6, 0, 1, 0), Image.BICUBIC)


@dataclass(frozen=True)
class WaterLabel:
    name: str
    mask: Image.Image
    origin: tuple[int, int]
    box: Box


WATER_SITE_PX = 120               # round 12: px from the site within which a water body is the site's own
WATER_MINOR_SHARE = 0.06          # round 12: of the text box, a minor water body's own water before it is tried
WATER_MIN_PX = 6000               # round 11: frame px of a body's own water in the text box before it is tried


def _affine_inverse(from_frame: Callable[[float, float], tuple[float, float]], width: int, height: int
                    ) -> Callable[[float, float], tuple[float, float]]:
    """The (lon, lat) -> frame px map of an affine `from_frame` (the locator's and the route's equirectangular
    views), fitted at three points round the frame's centre."""
    cx, cy = width / 2, height / 2
    o, ex, ey = from_frame(cx, cy), from_frame(cx + 100, cy), from_frame(cx, cy + 100)
    a, c = (ex[0] - o[0]) / 100, (ex[1] - o[1]) / 100
    b, d = (ey[0] - o[0]) / 100, (ey[1] - o[1]) / 100
    det = a * d - b * c or 1e-12

    def to_frame(lon: float, lat: float) -> tuple[float, float]:
        u, v = lon - o[0], lat - o[1]
        return cx + (d * u - b * v) / det, cy + (-c * u + a * v) / det
    return to_frame


def marine_in_view(width: int, height: int, from_frame: Callable[[float, float], tuple[float, float]],
                   to_frame: Callable[[float, float], tuple[float, float]], box: Box
                   ) -> list[tuple[geo.Marine, list[list[list[tuple[float, float]]]], "npt.NDArray[np.bool_]"]]:
    """Each named water body (geo.marine_polygons) whose polygon reaches `box` (frame px): (it, its polygons in
    frame px, its own mask over the frame). The view's (lon, lat) extent from `from_frame` on the box's edge; a
    polygon is tried at its own longitudes and 360 degrees either side (a view across the antimeridian)."""
    import numpy as np
    bodies = geo.marine_polygons()
    if not bodies:
        return []
    xs = np.linspace(box[0], box[2], 9)
    ys = np.linspace(box[1], box[3], 9)
    edge = [from_frame(float(x), float(y)) for x in xs for y in ys]
    lon_c = from_frame((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)[0]
    lons = [lon_c + ((lo - lon_c + 180.0) % 360.0 - 180.0) for lo, _ in edge]
    lats = [la for _, la in edge]
    polar = max(abs(v) for v in lats) > 85.0 or max(lons) - min(lons) > 300.0
    lo0, lo1, la0, la1 = min(lons) - 1, max(lons) + 1, min(lats) - 1, max(lats) + 1
    out = []
    for m in bodies:
        e = m["extent"]
        if e[3] < la0 or e[1] > la1:
            continue
        shifts = [0.0] if polar else [sh for sh in (0.0, 360.0, -360.0) if e[0] + sh <= lo1 and e[2] + sh >= lo0]
        if not shifts:
            continue
        mask = Image.new("L", (width, height), 0)
        dd = ImageDraw.Draw(mask)
        polys_px: list[list[list[tuple[float, float]]]] = []
        for sh in shifts:
            for poly in m["polygons"]:
                rings = [[to_frame(x + sh, y) for x, y in ring] for ring in poly]
                xs_ = [p[0] for p in rings[0]]
                ys_ = [p[1] for p in rings[0]]
                if max(xs_) < 0 or min(xs_) > width or max(ys_) < 0 or min(ys_) > height:
                    continue
                dd.polygon(rings[0], fill=255)
                for hole in rings[1:]:
                    dd.polygon(hole, fill=0)
                polys_px.append(rings)
        if polys_px:
            out.append((m, polys_px, np.asarray(mask) > 127))
    return out


def water_labels(width: int, height: int, from_frame: Callable[[float, float], tuple[float, float]],
                 land: BoolArray, keep_out: Sequence[Box], avoid: BoolArray | None = None,
                 to_frame: Callable[[float, float], tuple[float, float]] | None = None,
                 site: tuple[float, float] | None = None) -> list[WaterLabel]:
    """At most WATER_MAX water bodies named where they are in view (E): Montserrat 500 WATER_PX, tracked
    WATER_TRACK, set oblique (on two rows where one does not fit). Round 11 (coordinator directive 1): each body is
    Natural Earth's own marine polygon (marine_in_view), named only where its whole name lies on its own polygon,
    8 px off any land: never on land, never on another body's water. The place is the pole of
    inaccessibility (geo.polylabel) of the polygon clipped to the text box, measured in a space squeezed to the
    name's proportions and kept off every word; where that place does not hold the name, the most open grid place
    of the body's own water that does. The bodies showing the most water first; one with no place is not named
    (logged), and with no marine file none is (geo logs it). `from_frame` / `to_frame`: a frame point's
    (lon, lat) on the resting view and back (to_frame fitted from from_frame where not given: an affine view)."""
    import cv2
    import numpy as np
    tall = height > width
    k = 1.0 if tall else min(width / 1920, height / 1080)
    font = get_font("Montserrat", round(WATER_PX * k), weight=500)
    track = font.size * WATER_TRACK
    land_a = np.asarray(land, bool)
    tb = get_text_box(width, height)
    if tb[2] <= tb[0] or tb[3] <= tb[1]:
        return []
    to_frame = to_frame or _affine_inverse(from_frame, width, height)
    bodies = marine_in_view(width, height, from_frame, to_frame, tb)
    if not bodies:
        return []
    inbox = np.zeros((height, width), bool)
    inbox[int(tb[1]):int(tb[3]), int(tb[0]):int(tb[2])] = True
    blocked = land_a | (np.asarray(avoid, bool) if avoid is not None else False)
    scored = []
    reach = WATER_SITE_PX * (1.0 if tall else k)
    for m, polys, own in bodies:
        water = own & ~blocked & inbox
        n = int(water.sum())
        # round 12 (review: Istanbul's Sea of Marmara and Tarifa's Alboran Sea unnamed): the water at the site
        # (within WATER_SITE_PX of it) is named first, nearest first; a minor body (geo.MARINE_MINOR_RANK) only
        # there, or where its water is WATER_MINOR_SHARE of the box's, so a wide view names no small bays
        near = math.inf
        if site is not None:
            x0, y0 = max(0, int(site[0] - reach)), max(0, int(site[1] - reach))
            ys_, xs_ = np.nonzero(own[y0:int(site[1] + reach) + 1, x0:int(site[0] + reach) + 1])
            if len(xs_):
                near = float(np.min(np.hypot(xs_ + x0 - site[0], ys_ + y0 - site[1])))
        floor = WATER_MIN_PX * k * k
        if m["scalerank"] >= geo.MARINE_MINOR_RANK and near > reach:
            floor = max(floor, WATER_MINOR_SHARE * (tb[2] - tb[0]) * (tb[3] - tb[1]))
        if n >= floor:
            scored.append((n, m, polys, water, own, near))
        elif n and m["scalerank"] <= 3:     # round 13 (review: Suez 9:16 left the Red Sea unnamed, unlogged)
            log.info("Map: %s is in view, but its open water in the graphics box (%d px2, %d wanted) is too little "
                     "for its name; not named.", m["name"], n, round(floor))
    scored.sort(key=lambda t: (t[5] > reach, t[5] if t[5] <= reach else 0.0, -t[0]))
    step = max(4, round(WATER_STEP * k))
    out: list[WaterLabel] = []
    ii_block = _integral(blocked)
    for _n, m, polys, water, own, _near in scored:
        if len(out) >= WATER_MAX:
            break
        name = m["name"]
        # the name's box wholly on this body's own polygon, inside the text box; it and 8 px round it off any land
        # (the polygons are Natural Earth's labelling areas, a little short of the coast in narrow gulfs)
        ii_out = _integral(~(own & inbox))
        openness = cv2.distanceTransform(water.astype(np.uint8), cv2.DIST_L2, 5)
        words = name.upper().split()
        texts = [name.upper()] + [" ".join(words[:j]) + "\n" + " ".join(words[j:]) for j in range(1, len(words))]
        taken = list(keep_out) + [w.box for w in out]
        placed = None
        geo_tries = [0]
        geo_fault: list[str] = []
        # round 14 (review: Canakkale 9:16 named no water, the Aegean's open water broken by its islands): the
        # name tried at WATER_SMALL_SHARE of its size before the body is left unnamed (logged)
        sizes = [(font, track)]
        f_small = get_font("Montserrat", max(1, round(WATER_PX * WATER_SMALL_SHARE * k)), weight=500)
        sizes.append((f_small, f_small.size * WATER_TRACK))
        for text, (font_, track_) in [(t_, s_) for s_ in sizes for t_ in texts]:
            if placed is not None:
                break
            mask = _italic_mask(text, font_, track_)
            bb = mask.getbbox()
            if bb is None:
                continue
            hw, hh = (bb[2] - bb[0]) / 2, (bb[3] - bb[1]) / 2

            def fits(x: float, y: float, hw: float = hw, hh: float = hh, ii_out: "npt.NDArray[np.int64]" = ii_out,
                     taken: list[Box] = taken, name: str = name, geo_tries: list[int] = geo_tries,
                     geo_fault: list[str] = geo_fault) -> bool:
                box = (x - hw, y - hh, x + hw, y + hh)
                if not (tb[0] <= box[0] and box[2] <= tb[2] and tb[1] <= box[1] and box[3] <= tb[3]):
                    return False
                if _box_sum(ii_out, box) or _box_sum(ii_block, (box[0] - 8, box[1] - 8, box[2] + 8, box[3] + 8)):
                    return False
                if any(LayoutConstraintSolver.boxes_intersect(box, o, WATER_CLEAR * k) for o in taken):
                    return False
                # round 13 (directive 1): and its box, taken back to (lon, lat), wholly on this body's own water
                if geo_tries[0] >= WATER_GEO_TRIES:
                    return False
                geo_tries[0] += 1
                fault = water_name_fault(name, box, from_frame, land_a)
                if fault:
                    geo_fault[:] = [fault]
                return not fault
            sq = hh / hw                                  # the name's box squeezed to a square
            clipped = [[(x * sq, y) for x, y in geo.clip_ring(ring, tb)] for poly in polys for ring in poly]
            grown = [((o[0] - WATER_CLEAR * k) * sq, o[1] - WATER_CLEAR * k, (o[2] + WATER_CLEAR * k) * sq,
                      o[3] + WATER_CLEAR * k) for o in taken]
            pole = geo.polylabel([r for r in clipped if len(r) >= 3], precision=2.0, avoid=grown)
            cands: list[tuple[float, float]] = []
            if pole is not None:
                cands.append((pole[0] / sq, pole[1]))
            ys_, xs_ = np.nonzero(water[step // 2::step, step // 2::step])
            gx, gy = xs_ * step + step // 2, ys_ * step + step // 2
            order = np.argsort(-openness[gy, gx], kind="stable")
            cands += [(float(gx[i]), float(gy[i])) for i in order if openness[gy[i], gx[i]] >= hh]
            for x, y in cands:
                if fits(x, y):
                    ox, oy = round(x - (bb[0] + bb[2]) / 2), round(y - (bb[1] + bb[3]) / 2)
                    placed = WaterLabel(name, mask.crop(bb), (ox + bb[0], oy + bb[1]),
                                        (ox + bb[0], oy + bb[1], ox + bb[2], oy + bb[3]))
                    if font_ is not font:
                        log.info("Map: %s named at %d px (%d has no place on its own water).", name, font_.size,
                                 font.size)
                    break
        if placed is not None:
            out.append(placed)
        elif geo_fault:
            log.info("Map: %s is in view but no place for its name lies wholly on its own water (last refused: %s); "
                     "not named.", name, geo_fault[0])
        else:
            log.info("Map: %s is in view but its own water holds no place for its name; not named.", name)
    return out


def water_plate(rest: Image.Image, wl: WaterLabel) -> tuple[Image.Image, "_MaskPlate"] | None:
    """The sea's name drawn on `rest` at WATER_ALPHA, raised (logged) until it measures brand.TEXT_CONTRAST:
    (the frame with it drawn, its measured _MaskPlate), or None where no opacity does."""
    for alpha in (WATER_ALPHA, 0.85, 1.0):
        im = rest.copy()
        im.paste(WATER_FILL, wl.origin, wl.mask.point(lambda v, a=alpha: int(v * a)))
        plate = _MaskPlate(wl.name.upper(), wl.mask, wl.origin, WATER_FILL, im)
        if plate.contrast is not None and plate.contrast >= brand.TEXT_CONTRAST:
            if alpha > WATER_ALPHA:
                log.info("Map: %s at %.0f %% to measure %.1f:1.", wl.name, 100 * alpha, brand.TEXT_CONTRAST)
            plate.alpha = alpha
            return im, plate
    log.info("Map: %s under %.1f:1 at full opacity; not named.", wl.name, brand.TEXT_CONTRAST)
    return None


@dataclass(frozen=True)
class NeighbourName:
    """A neighbouring country named on the resting close-up (E): its display name, its rows (letter-spaced, as the
    base draws them), the font px, its centre and its ink box."""
    name: str
    rows: tuple[str, ...]
    px: int
    xy: tuple[float, float]
    box: Box


NEIGHBOUR_MAX = 3                 # neighbours named on a close-up at most (2 wanted, spec E)
NEIGHBOUR_STEP = 10               # px: the grid a neighbour's name is tried on


def map_name_px(width: int, height: int) -> int:
    """A neighbour's name's px on the frame (R4): MAP_NAME_PX on 9:16, MAP_NAME_PX_WIDE scaled on 16:9."""
    return MAP_NAME_PX if height > width else round(MAP_NAME_PX_WIDE * min(width / 1920, height / 1080))


def neighbour_font(width: int, height: int) -> FreeTypeFont:
    return get_font("Montserrat", map_name_px(width, height), weight=MAP_NAME_WEIGHT)


NEIGHBOUR_PX_STEPS = (0, 2, 4, 6) # round 8 (review 5): px under map_name_px a neighbour's name may step down to fit
NEIGHBOUR_FINE_STEP = 4           # round 10: px, the grid the stepped-down sizes are tried on
NEIGHBOUR_LAND_SHARE = 0.55       # and, where it fits wholly on no land, the least share of its box on its country


NEIGHBOUR_TOUCH_PX = 4            # round 9: px a neighbour's ground lies from the home's to count as sharing its border


NEIGHBOUR_SITE_PX = 160          # round 11 (review 6): px from the marker within which an adjacent country ranks first


def neighbour_rank(masks: Sequence[tuple[geo.Country, "npt.NDArray[np.uint8]"]], home: BoolArray | None,
                   k: float = 1.0, anchor: tuple[float, float] | None = None) -> dict[int, tuple[float, float, float]]:
    """Round 9 (review 6): {id(country): sort key} for the neighbours in view, adjacency first: (0, -px of shared
    border, -area) for a country whose ground lies within NEIGHBOUR_TOUCH_PX of the home's, (1, px to the home,
    -area) for one that does not. Every country ranks by its area alone without a home. Round 11 (review 6:
    Bratislava did not name Austria, whose border the castle stands on, nor Aachen Belgium): with the marker's
    `anchor`, an adjacent country within NEIGHBOUR_SITE_PX of it ranks before every other, the nearest first.
    O(w h) per country, on the frame at a quarter of its resolution."""
    import cv2
    import numpy as np
    out: dict[int, tuple[float, float, float]] = {}
    has = home is not None and bool(np.asarray(home).any())
    if has:
        h4 = np.asarray(home, bool)[::4, ::4]
        r = max(1, round(NEIGHBOUR_TOUCH_PX * k / 4) + 1)
        near = cv2.dilate(h4.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0
        dist = cv2.distanceTransform((~h4).astype(np.uint8), cv2.DIST_L2, 5) * 4
    for c, arr in masks:
        m4 = np.asarray(arr)[::4, ::4] > 0
        area = float(np.count_nonzero(m4)) * 16
        if not has or not m4.any():
            out[id(c)] = (1.0, 0.0, -area)
            continue
        touch = float(np.count_nonzero(m4 & near))
        out[id(c)] = (0.0, -touch, -area) if touch else (1.0, float(dist[m4].min()), -area)
        if touch and anchor is not None:
            ys_, xs_ = np.nonzero(m4)
            d_site = float(np.hypot(xs_ * 4 + 2 - anchor[0], ys_ * 4 + 2 - anchor[1]).min())
            if d_site <= NEIGHBOUR_SITE_PX * k:
                out[id(c)] = (0.0, d_site - 1e9, -area)
    return out


def neighbours_in_view(width: int, height: int, home: geo.Country | None,
                       to_frame: Callable[[float, float], tuple[float, float]],
                       anchor: tuple[float, float] | None = None) -> list[tuple[str, bool]]:
    """Round 9 (review 6): (display name upper-cased, adjacent) of every nameable country with ground in the text
    box, in neighbour_rank's order (the home's neighbours first). Rasterised at a quarter of the frame."""
    import numpy as np
    tb = get_text_box(width, height)
    w4, h4 = max(1, width // 4), max(1, height // 4)
    masks: dict[int, tuple[geo.Country, Image.Image]] = {}
    hm = Image.new("L", (w4, h4), 0)
    for c, poly in _view_polygons(to_frame, width, height):
        outer = [(x / 4, y / 4) for x, y in (to_frame(lo, la) for lo, la in poly[0])]
        holes = [[(x / 4, y / 4) for x, y in (to_frame(lo, la) for lo, la in hole)] for hole in poly[1:]]
        if c is home:
            target = hm
        elif not c.get("display") or c.get("type") == "Indeterminate" or c["label_rank"] > 6:
            continue
        else:
            target = masks.setdefault(id(c), (c, Image.new("L", (w4, h4), 0)))[1]
        d = ImageDraw.Draw(target)
        d.polygon(outer, fill=255)
        for hp in holes:
            d.polygon(hp, fill=0)
    x0, y0, x1, y1 = (int(v / 4) for v in tb)
    clip = np.zeros((h4, w4), bool)
    clip[y0:y1, x0:x1] = True
    cands = [(c, np.repeat(np.repeat((np.asarray(m) > 0) & clip, 4, 0), 4, 1)) for c, m in masks.values()]
    cands = [(c, a) for c, a in cands if a.any()]
    home_a = np.repeat(np.repeat(np.asarray(hm) > 0, 4, 0), 4, 1) if home is not None else None
    rank = neighbour_rank(cands, home_a, 1.0 if height > width else min(width / 1920, height / 1080), anchor)
    order = sorted(cands, key=lambda t: rank[id(t[0])])
    return [(str(c["display"]).upper(), rank[id(c)][0] == 0.0) for c, _a in order]


def close_neighbours(width: int, height: int, home: geo.Country | None,
                     to_frame: Callable[[float, float], tuple[float, float]], keep_out: Sequence[Box],
                     already: set[str], wanted: int = NEIGHBOUR_MAX,
                     anchor: tuple[float, float] | None = None) -> list[NeighbourName]:
    """The neighbours named on the resting close-up (round 6 review: none was, at the new zooms, their Natural
    Earth label points off the frame): the countries with the most land in the text box first, each name (MAP_NAME_PX
    Montserrat MAP_NAME_WEIGHT, letter-spaced, on two rows where one does not fit) set where its land is deepest (the
    furthest from its own border) among the NEIGHBOUR_STEP grid places whose box, grown 6 px, is wholly on that
    country, off the home fill, inside the text box and MARKER_LABEL_CLEAR from every word (`keep_out`) and from the
    names set before it. Round 8 (review 5: Urnes named no neighbour with Sweden filling the frame's right): where
    no such place is, a place whose box is at least NEIGHBOUR_LAND_SHARE on the country and otherwise on the sea
    (never on the home or a third country), then each a NEIGHBOUR_PX_STEPS step smaller (logged). `already`: the
    names the base shows on the close-up (upper case), not set twice."""
    import cv2
    import numpy as np
    k = 1.0 if height > width else min(width / 1920, height / 1080)
    px0 = map_name_px(width, height)
    tb = get_text_box(width, height)
    x0, y0, x1, y1 = (int(v) for v in tb)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    masks: dict[int, tuple[geo.Country, Image.Image]] = {}
    land_im = Image.new("L", (width, height), 0)
    dland = ImageDraw.Draw(land_im)
    hm = Image.new("L", (width, height), 0)
    dh = ImageDraw.Draw(hm)
    for c, poly in _view_polygons(to_frame, width, height):   # round 8: wrapped, enclaves last
        outer = [to_frame(x, y) for x, y in poly[0]]
        holes = [[to_frame(x, y) for x, y in hole] for hole in poly[1:]]
        dland.polygon(outer, fill=255)
        for hp in holes:
            dland.polygon(hp, fill=0)
        if c is home:
            dh.polygon(outer, fill=255)
            for hp in holes:
                dh.polygon(hp, fill=0)
            continue
        if not c.get("display") or c.get("type") == "Indeterminate" or c["label_rank"] > 6:
            continue
        if c["display"].upper() in already:
            continue
        if id(c) not in masks:
            masks[id(c)] = (c, Image.new("L", (width, height), 0))
        dm = ImageDraw.Draw(masks[id(c)][1])
        dm.polygon(outer, fill=255)
        for hp in holes:
            dm.polygon(hp, fill=0)
    cands: list[tuple[int, geo.Country, npt.NDArray[np.uint8]]] = []
    for c, m in masks.values():
        arr = np.asarray(m)
        area = int(np.count_nonzero(arr[y0:y1, x0:x1]))
        if area > 0:
            cands.append((area, c, arr))
    land_all = np.asarray(land_im) > 0
    home_m = _integral(np.asarray(hm) > 0) if home is not None else None
    # round 9 (review 6): the neighbours by adjacency to the home, not by area: the countries that share its border
    # in view first (the longest shared border first), then the others nearest the home (St Gall named Belgium and
    # not Italy or Austria; North Cape named Greenland, not Sweden or Finland)
    rank = neighbour_rank([(c, arr) for _a, c, arr in cands], np.asarray(hm) > 0, k, anchor)
    out: list[NeighbourName] = []
    step = max(4, round(NEIGHBOUR_STEP * k))
    # round 10 (review 1): a stepped-down name is tried on a grid NEIGHBOUR_FINE_STEP fine (Slovakia's room east of
    # Pannonhalma's name, 147 px between the label's air and the text box's edge, fell between the 10 px places)
    fine = max(2, round(NEIGHBOUR_FINE_STEP * k))
    grids = {False: np.mgrid[y0 + step // 2:y1:step, x0 + step // 2:x1:step],
             True: np.mgrid[y0 + fine // 2:y1:fine, x0 + fine // 2:x1:fine]}

    def clear(b: Box) -> bool:
        return not any(LayoutConstraintSolver.boxes_intersect(b, o, MARKER_LABEL_CLEAR * k)
                       for o in list(keep_out) + [n.box for n in out])

    def inside_tb(b: Box) -> bool:
        return tb[0] <= b[0] and b[2] <= tb[2] and tb[1] <= b[1] and b[3] <= tb[3]

    def full(b: Box) -> int:
        return (math.ceil(b[2]) - int(b[0])) * (math.ceil(b[3]) - int(b[1]))
    for area, c, arr in sorted(cands, key=lambda t: rank.get(id(t[1]), (0.0, 0.0, -t[0]))):
        if len(out) >= wanted:
            break
        text = " ".join(c["display"].upper())
        words = c["display"].upper().split()
        options = [(text,)] + [(" ".join(" ".join(words[:j])), " ".join(" ".join(words[j:])))
                               for j in range(1, len(words))]
        # round 12 (review: Khasab's UNITED ARAB EMIRATES, a third of the frame, unnamed): then its shorter forms
        options += [(" ".join(alt.upper()),) for alt in c.get("short_names", [])]
        mask = arr > 0
        ii = _integral(mask)
        ii_other = _integral(land_all & ~mask)        # the home's and every third country's ground
        depth = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
        done = None
        why: dict[str, int] = {}
        for dpx in NEIGHBOUR_PX_STEPS:
            font = get_font("Montserrat", max(1, round(px0 - dpx * k)), weight=MAP_NAME_WEIGHT)
            ys_, xs_ = grids[bool(dpx)]
            for relaxed in (False,):      # round 15 (directive 2): never partly over the sea
                for rows in options:
                    pitch = round(font.size * 1.25)
                    rels = [probe.textbbox((0, 0), drawable(r, font), font=font, anchor="mm") for r in rows]
                    hw = max(r[2] - r[0] for r in rels) / 2
                    hh = (rels[0][3] - rels[0][1] + pitch * (len(rows) - 1)) / 2
                    if area < (2 if relaxed else 4) * hw * hh * 1.2:
                        why["area"] = why.get("area", 0) + 1
                        continue
                    ok = depth[ys_, xs_] >= (1 if relaxed else hh + 6)
                    for i in np.argsort(-depth[ys_, xs_][ok], kind="stable"):
                        x, y = float(xs_[ok][i]), float(ys_[ok][i])
                        b = (x - hw, y - hh, x + hw, y + hh)
                        if not inside_tb(b):
                            why["box"] = why.get("box", 0) + 1
                            continue
                        grown = (b[0] - 6, b[1] - 6, b[2] + 6, b[3] + 6)
                        if not relaxed:
                            if _box_sum(ii, grown) < full(grown):
                                why["land"] = why.get("land", 0) + 1
                                continue
                        else:
                            # round 8: partly on the sea, never on the home or a third country's ground
                            if _box_sum(ii_other, grown) or _box_sum(ii, b) < NEIGHBOUR_LAND_SHARE * full(b):
                                why["land"] = why.get("land", 0) + 1
                                continue
                        if home_m is not None and _box_sum(home_m, grown):
                            why["home"] = why.get("home", 0) + 1
                            continue
                        if not clear(b):
                            why["words"] = why.get("words", 0) + 1
                            continue
                        done = NeighbourName(c["display"], rows, font.size, (x, y), b)
                        break
                    if done is not None:
                        break
                if done is not None:
                    break
            if done is not None:
                if dpx or relaxed:
                    log.info("Map: the neighbour %s named at %d px%s.", c["display"], font.size,
                             ", partly over the sea" if relaxed else "")
                break
        if done is not None:
            out.append(done)
        else:
            log.info("Map: no place for the neighbour %s's name (places refused: %s).", c["display"],
                     ", ".join(f"{k_} {v_}" for k_, v_ in sorted(why.items())) or "none tried")
    return out


class _ThinPlate(TextPlate):
    """A TextPlate whose glyphs are too thin to read their own colour: measured against the drawn colour."""

    def measure(self, im: Image.Image) -> float | None:
        import numpy as np

        from .typography import ring_contrast
        try:
            return super().measure(im)
        except TypeError:
            got: float | None = ring_contrast(np.asarray(im.convert("RGB")), self.glyphs, self.origin, self.fill, True)
            return got


def _plan_thin(rest: Image.Image, xy: tuple[float, float], text: str, font: FreeTypeFont, fill: tuple[int, int, int],
               anchor: str = "mm", halo_px: int = 21, min_halo: float = 0.0) -> TextPlate:
    """plan_text for a light face too thin for typography.ring_contrast to read its own interior (round 7: a
    neighbour's MAP_NAME_WEIGHT 500 at 24 px has no eroded stem): measured against its drawn colour instead, its
    halo raised in the same 8 % steps until it reaches brand.PLACE_TARGET."""
    try:
        return plan_text(rest, xy, text, font, fill, anchor, halo_px=halo_px, min_halo=min_halo)
    except TypeError:
        import numpy as np

        from .brand import INK, PLACE_TARGET
        from .typography import ring_contrast
        plate = _ThinPlate(drawable(text, font), font, fill, xy, anchor, 0, 0, INK, halo_px, False, rest.size)
        if plate.empty:
            return plate
        crop = plate._crop(rest)
        halo = min_halo
        r = ring_contrast(np.asarray(plate._layer(crop, halo).convert("RGB")), plate.glyphs, (0, 0), plate.fill, True)
        while r is not None and r < PLACE_TARGET and halo < 0.95:
            halo = min(0.95, halo + 0.08)
            r = ring_contrast(np.asarray(plate._layer(crop, halo).convert("RGB")), plate.glyphs, (0, 0), plate.fill,
                              True)
        plate.halo, plate.contrast = round(halo, 2), r
        return plate


def neighbour_plates(rest: Image.Image, names: Sequence[NeighbourName]) -> list[tuple[str, TextPlate]]:
    """The close-up's neighbour names as measured plates (MAP_NAME on an ink halo at least MAP_NAME_HALO deep)."""
    out: list[tuple[str, TextPlate]] = []
    for nb in names:
        font = get_font("Montserrat", nb.px, weight=MAP_NAME_WEIGHT)
        pitch = round(font.size * 1.25)
        top = nb.xy[1] - pitch * (len(nb.rows) - 1) / 2
        for j, r in enumerate(nb.rows):
            out.append((f"neighbour_{nb.name.upper()}", _plan_thin(rest, (nb.xy[0], top + pitch * j), r, font, MAP_NAME,
                                                                  "mm", halo_px=21, min_halo=MAP_NAME_HALO)))
    return out


SCALE_NAME_AIR = 56            # round 10 (review): px a neighbour's name keeps from the scale bar's box
MARKER_SCALE_AIR = 56          # round 8 (review 4): px past SCALE_LABEL_CLEAR the scale bar keeps from the marker


def land_borders(id_map: "npt.NDArray[np.uint16]", width: int, height: int) -> BoolArray:
    """Round 8 (review 4): the frame px (width x height) on a border between two countries' ground of `id_map`
    (view_country_ids, at a fraction of the frame), 1-2 px wide."""
    import cv2
    import numpy as np
    a = np.asarray(id_map)
    edge = np.zeros(a.shape, bool)
    dx = (a[:, 1:] != a[:, :-1]) & (a[:, 1:] != 0) & (a[:, :-1] != 0)
    dy = (a[1:, :] != a[:-1, :]) & (a[1:, :] != 0) & (a[:-1, :] != 0)
    edge[:, 1:] |= dx
    edge[1:, :] |= dy
    out: BoolArray = cv2.resize(edge.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST) > 0
    return out


def place_globe(width: int, height: int, home: BoolArray | None, obstacles: Sequence[Box]
                ) -> tuple[int, int, int, int] | None:
    """The locator's globe corner (B): top-right, then top-left, bottom-right, bottom-left (then each at 0.7 size),
    the first whose disc keeps GLOBE_MAP_AIR from every obstacle (plates, labels, the bar) and from the home fill
    (no pixel of the fill under it); None where no corner does (logged)."""
    import cv2
    import numpy as np
    k = min(width, height) / 1080
    air = round(GLOBE_MAP_AIR * k)
    grown = cv2.dilate(home.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * air + 1, 2 * air + 1))) \
        if home is not None and home.any() else None
    for shrink in (1.0, 0.7):
        for corner in ("tr", "tl", "br", "bl"):
            gb = globe_box(width, height, corner, shrink)
            if any(LayoutConstraintSolver.boxes_intersect(gb, o, air) for o in obstacles):
                continue
            if grown is not None:
                disc = np.zeros(home.shape, np.uint8)
                cv2.circle(disc, ((gb[0] + gb[2]) // 2, (gb[1] + gb[3]) // 2), (gb[2] - gb[0]) // 2, 1, -1)
                if (disc & grown).any():
                    continue
            if (corner, shrink) != ("tr", 1.0):
                log.info("Map: the globe %s%s (the top-right is taken).", corner, "" if shrink == 1 else ", smaller")
            return gb
    log.info("Map: no corner keeps the globe off the home fill and the words; no globe.")
    return None


# ------------------------------------------------------------- round 13 (2026-11-04): the name verifier
# Coordinator directive 1: one final check every drawn place name passes before it is drawn, fail closed: a name
# that fails is dropped (logged with the reason), never drawn wrong. Water: its box (corners and an interior grid)
# taken back to (lon, lat) through the projection the frame uses, each point on its own body's polygon (the
# smallest named body there, geo.marine_at) and off the drawn land. Home: inside its fill on one island of it (the
# site's, or one larger), or wholly off the home and off every small island. Neighbour: on its own country's ground,
# never on a third country's.
NAME_CHECK_GRID = (7, 3)          # interior points across and down a name's box, with its four corners
NAME_CHECK_INSET = 2.0            # px the points keep inside the box's edge (its anti-aliased rim)
NAME_ISLET_BOX_SHARE = 20.0       # a part of the home this many name boxes big holds its name though not the site's
NAME_BESIDE_PX = 40.0             # px round an outside name its nearest other land is looked for in, past COUNTRY_NEAR_PX
NAME_NEARER_PX = 30.0             # round 15: px other land may lie nearer an outside name than its own ground (a
                                  # microstate's coast runs on into its neighbour's: Monaco's water is France's too)
NAME_SITE_REACH_PX = 100.0        # px from the marker within which the nearest island is the site's own
NAME_RETRIES = 3                  # places a failed home name is tried at again before it is dropped
WATER_SMALL_SHARE = 0.8          # round 14: a water name's stepped-down size, of WATER_PX, before it goes unnamed
WATER_GEO_TRIES = 60              # candidate places a water name's geography is checked at before it gives up


@dataclass(frozen=True)
class NameCheck:
    """One name's verdict (verify_map_names): its kind ("water", "home", "neighbour"), text, box, and the fault
    that dropped it ("" where it passed)."""
    kind: str
    text: str
    box: Box
    fault: str

    @property
    def ok(self) -> bool:
        return not self.fault


LAST_NAME_CHECKS: list[NameCheck] = []     # the last locator's verdicts (tests read them; reset per render)


def name_check_points(box: Box, grid: tuple[int, int] = NAME_CHECK_GRID,
                      inset: float = NAME_CHECK_INSET) -> list[tuple[float, float]]:
    """The points a name's box is checked at: its four corners and a `grid` of interior points, `inset` px inside
    its edge (a box thinner than twice that: its centre line)."""
    x0, y0, x1, y1 = box
    if x1 - x0 > 2 * inset:
        x0, x1 = x0 + inset, x1 - inset
    else:
        x0 = x1 = (x0 + x1) / 2
    if y1 - y0 > 2 * inset:
        y0, y1 = y0 + inset, y1 - inset
    else:
        y0 = y1 = (y0 + y1) / 2
    pts = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    nx, ny = grid
    for j in range(ny):
        for i in range(nx):
            pts.append((x0 + (x1 - x0) * (i + 0.5) / nx, y0 + (y1 - y0) * (j + 0.5) / ny))
    return pts


def _at(mask: "npt.NDArray[Any] | None", x: float, y: float, down: int = 1) -> Any:
    if mask is None:
        return 0
    h, w = mask.shape[:2]
    xi, yi = min(w - 1, max(0, int(x // down))), min(h - 1, max(0, int(y // down)))
    return mask[yi, xi]


def water_name_fault(name: str, box: Box, from_frame: Callable[[float, float], tuple[float, float]],
                     land: BoolArray | None) -> str:
    """Why a water name at `box` (frame px) would be wrong ("" where it is right): a point of the box on the drawn
    land, or one whose (lon, lat), through `from_frame` (the frame's own projection), is not on `name`'s body: off
    its polygon, or on a smaller named body inside it (geo.marine_at: the smallest body holding the point)."""
    pts = name_check_points(box)
    c = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    for x, y in [c] + pts:
        if land is not None and bool(_at(land, x, y)):
            return f"on land at ({x:.0f}, {y:.0f})"
        lon, lat = from_frame(x, y)
        m = geo.marine_at(lon, lat)
        if m is None or m["name"] != name:
            return (f"({lon:.2f}, {lat:.2f}) is on " + (m["name"] if m is not None else "no named water")
                    + f", not {name}")
    return ""


def _components(mask: BoolArray | None) -> tuple["npt.NDArray[np.int32]", "npt.NDArray[np.int64]"] | None:
    """The 8-connected parts of a frame mask: (labels, area of each label), or None with no mask."""
    if mask is None:
        return None
    import cv2
    import numpy as np
    _n, lab, stats, _c = cv2.connectedComponentsWithStats(np.asarray(mask, np.uint8), connectivity=8)
    return lab, stats[:, cv2.CC_STAT_AREA].astype(np.int64)


def _site_component(comps: tuple[Any, Any] | None, anchor: tuple[float, float], reach: float) -> int:
    """The part of `comps` under the site, or the nearest one within `reach` px of it (0 where none is)."""
    if comps is None:
        return 0
    import numpy as np
    lab = comps[0]
    v = int(_at(lab, *anchor))
    if v:
        return v
    h, w = lab.shape
    x0, y0 = max(0, int(anchor[0] - reach)), max(0, int(anchor[1] - reach))
    x1, y1 = min(w, int(anchor[0] + reach) + 1), min(h, int(anchor[1] + reach) + 1)
    sub = lab[y0:y1, x0:x1]
    ys, xs = np.nonzero(sub)
    if not len(xs):
        return 0
    d = np.hypot(xs + x0 - anchor[0], ys + y0 - anchor[1])
    i = int(np.argmin(d))
    return int(sub[ys[i], xs[i]]) if d[i] <= reach else 0


LEADER_MIN_PX = 16.0           # round 16 (review 9): px (at 1080): an outside name either touches its ground (under
                               # 6 px, no leader) or keeps at least this off it, so its leader is never a 5 to 7 px
                               # tick that reads as an apostrophe (MALTA, ARUBA, SEYCHELLES)
LEADER_TOUCH_PX = 3.0          # and under this it touches its ground (a leader is drawn from 6 px)


def ground_leader(box: Box, ground: BoolArray | None, k: float = 1.0, reach: float = math.inf,
                  min_px: float = 6.0) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Round 15 (coordinator directives 2 and 3): the leader from `box` (a name set outside its own fill, or a
    marker off its land) to the nearest pixel of `ground` (its own polygon's fill): (the box's edge point, the
    ground point), each end pulled 3 px in; None where the box touches its ground (under 6 px off) or the ground is
    further than `reach` or not in the frame. O(ground px)."""
    import numpy as np
    if ground is None:
        return None
    ys, xs = np.nonzero(np.asarray(ground, bool)[::2, ::2])
    if len(xs):
        xs, ys = xs * 2.0 + 1.0, ys * 2.0 + 1.0
    else:   # round 16: a speck of a few px can miss the half-resolution grid (Bikar's islet: no leader drawn)
        ys, xs = np.nonzero(np.asarray(ground, bool))
        if not len(xs):
            return None
        xs, ys = xs + 0.5, ys + 0.5
    dx = np.maximum(np.maximum(box[0] - xs, xs - box[2]), 0.0)
    dy = np.maximum(np.maximum(box[1] - ys, ys - box[3]), 0.0)
    d = np.hypot(dx, dy)
    i = int(np.argmin(d))
    if d[i] < min_px * k or d[i] > reach:
        return None
    gx, gy = float(xs[i]), float(ys[i])
    bx, by = min(max(gx, box[0]), box[2]), min(max(gy, box[1]), box[3])
    n = max(1e-6, math.hypot(gx - bx, gy - by))
    ux, uy = (gx - bx) / n, (gy - by) / n
    pull = 3 * k
    return (bx + ux * pull, by + uy * pull), (gx - ux * pull, gy - uy * pull)


def home_name_fault(box: Box, inside: bool, home: BoolArray | None, land: BoolArray | None,
                    anchor: tuple[float, float], k: float = 1.0,
                    comps: tuple[tuple[Any, Any] | None, tuple[Any, Any] | None] | None = None) -> str:
    """Why the home's name at `box` would be wrong ("" where it is right). Inside its fill: every point on the
    home's ground, all on one island of it, and that island the site's own or a larger one (never KIRIBATI over
    Maiana, a different atoll). Outside (round 15, directive 2): every point on open water (no land of any size
    under it), and no other land nearer it than the home's own ground (it is drawn with a leader to that ground)."""
    hc = comps[0] if comps is not None else _components(home)
    lc = comps[1] if comps is not None else _components(land)
    pts = name_check_points(box)
    area_box = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    if inside:
        if hc is None:
            return "no home fill to sit in"
        labs = {int(_at(hc[0], x, y)) for x, y in pts}
        if 0 in labs:
            return "partly off the home's fill"
        if len(labs) > 1:
            return "across two islands of the home"
        part = labs.pop()
        site = _site_component(hc, anchor, NAME_SITE_REACH_PX * k)
        lab_ = hc[0] == part
        edge = bool(lab_[0, :].any() or lab_[-1, :].any() or lab_[:, 0].any() or lab_[:, -1].any())
        if part != site and not edge:      # (a part running off the frame is a mainland, not an islet)
            if site == 0 and hc[1][part] < NAME_ISLET_BOX_SHARE * area_box:
                return "on an island of the home that does not hold the site"
            if site and hc[1][part] < hc[1][site]:
                return "on a smaller island of the home than the site's"
        return ""
    # round 15 (coordinator directive 2, LEADER OR DROP): outside its fill only over open water, never over any
    # land of any size (RUSSIA on Lithuania at Kaliningrad, SINGAPORE on Johor, MONACO on France, LESOTHO on South
    # Africa); it then has a leader to its own ground (ground_leader)
    for x, y in pts:
        if home is not None and bool(_at(home, x, y)):
            return f"outside name over the home's ground at ({x:.0f}, {y:.0f})"
        if land is not None and bool(_at(land, x, y)):
            return f"outside name over other land at ({x:.0f}, {y:.0f})"
    if lc is not None:
        import numpy as np
        h, w = lc[0].shape
        # round 14 (review: GREENLAND 5 px off Svalbard, 90 px off Greenland, at Cape Morris Jesup): nor nearer
        # a separate landmass of any size (one with none of the home's ground, and not under the name) than the
        # home's own ground; the name reads as the nearest land's
        if home is not None:
            reach = int(COUNTRY_NEAR_PX * k + NAME_BESIDE_PX * k)
            x0, y0 = max(0, int(box[0]) - reach), max(0, int(box[1]) - reach)
            x1, y1 = min(w, int(box[2]) + reach + 1), min(h, int(box[3]) + reach + 1)
            sub_l = lc[0][y0:y1, x0:x1]
            sub_h = np.asarray(home[y0:y1, x0:x1], bool) if home.shape == lc[0].shape else None
            if sub_h is not None:
                homey = {int(v) for v in np.unique(lc[0][np.asarray(home, bool)])} - {0} if home.any() else set()
                under = {int(_at(lc[0], x, y)) for x, y in pts} - {0}
                yy, xx = np.mgrid[y0:y1, x0:x1]
                dx_ = np.maximum(np.maximum(box[0] - xx, xx - box[2]), 0)
                dy_ = np.maximum(np.maximum(box[1] - yy, yy - box[3]), 0)
                dist = np.hypot(dx_, dy_)
                d_home = float(dist[sub_h].min()) if sub_h.any() else math.inf
                other = (sub_l > 0) & ~np.isin(sub_l, list(homey | under) or [-1])
                d_other = float(dist[other].min()) if other.any() else math.inf
                if d_other + NAME_NEARER_PX * k < d_home:
                    return (f"nearer other land ({d_other:.0f} px) than the home's ground ({d_home:.0f} px); it "
                            "would read as that land's name")
    return ""


def _country_keys(c: geo.Country) -> set[str]:
    keys = {str(c.get("display") or "").upper()} | {str(n).upper() for n in c.get("names", [])} \
        | {str(n).upper() for n in c.get("short_names", [])}
    return {k_.replace(" ", "") for k_ in keys if k_}


def view_country_table(width: int, height: int, to_frame: Callable[[float, float], tuple[float, float]],
                       down: int = 2) -> tuple["npt.NDArray[np.uint16]", dict[int, geo.Country]]:
    """Each frame pixel's country id at 1/`down` resolution (0 the sea; view_country_ids' raster) and the country
    each id stands for."""
    ids, _h = view_country_ids(width, height, to_frame, None, down)
    table: dict[int, geo.Country] = {}
    seen: dict[int, int] = {}
    for c, _poly in _view_polygons(to_frame, width, height):
        cid = seen.setdefault(id(c), len(seen) + 1)
        table[cid] = c
    return ids, table


def neighbour_name_fault(text: str, box: Box, table: tuple["npt.NDArray[np.uint16]", dict[int, geo.Country]],
                         down: int = 2) -> str:
    """Why a neighbour's name at `box` would be wrong ("" where it is right): a point on a third country's ground,
    or (round 15, coordinator directive 2: every name inside its own polygon) a point off its own country's."""
    ids, by = table
    want = text.upper().replace(" ", "")
    pts = name_check_points(box)
    own = 0
    for x, y in pts:
        cid = int(_at(ids, x, y, down))
        if not cid:
            continue
        c = by.get(cid)
        if c is not None and want in _country_keys(c):
            own += 1
        else:
            return "over " + (str(c.get("display") or "another country") if c is not None else "another country") \
                + f" at ({x:.0f}, {y:.0f})"
    if own < len(pts):
        return f"only {own} of {len(pts)} points on its own ground (the rest on the sea)"
    return ""


def render_map(place: dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
               narration: str = "") -> Path | None:
    """The locator map (_render_map). Round 15 (coordinator directive 1): drawn from the 1:10m entities
    (geo.detail_view), the home being the entity whose polygon holds the site, else the nearest within
    geo.SITE_NEAR_KM (geo.site_home: the site's own geometry decides; the topic's country only chooses between
    grounds within geo.SITE_SNAP_KM, the data's own error). Its fill and its name are that one entity's: Gibraltar's
    Rock is Gibraltar's, Fakaofo Tokelau's, Sao Tome Sao Tome and Principe's, Kaliningrad Russia's. A site further
    from all ground is on the open sea: marked there, no home named (logged)."""
    if not geo.available():
        return None
    with geo.detail_view():
        found = geo.site_home(float(place["lon"]), float(place["lat"]), hint=str(place.get("country") or ""))
        home = found.country
        given = str(place.get("country") or "")
        if home is None:
            log.warning("Map: the site is more than %.0f km from all ground in the data; marked on the open sea, no "
                        "home named%s.", geo.SITE_NEAR_KM, f" (the topic gives {given!r})" if given else "")
        else:
            if given and not geo._hint_matches(home, given):
                log.info("Map: the site lies on %s's ground (the topic gives %r); named by its ground.",
                         home["display"], given)
            if found.km > ISLAND_SNAP_KM:
                log.info("Map: the site is %.1f km off %s's ground; the marker stands on the sea there, a leader to "
                         "the land.", found.km, home["display"])
            if found.overrode:
                # round 16 (review 3): the topic's country chosen within geo.SITE_HINT_KM over the polygon that
                # holds the site: the marker moved onto the chosen ground, so it never stands on a fill other than
                # the highlighted one (logged)
                moved = snap_onto(float(place["lon"]), float(place["lat"]), home)
                if moved is not None:
                    log.info("Map: the site lies %.1f km inside a neighbour's 1:10m border (the data's own error, "
                             "under %.0f km); named %s as the topic gives, the marker moved onto its ground.",
                             found.km, geo.SITE_HINT_KM, home["display"])
                    place = {**place, "lon": moved[0], "lat": moved[1]}
        return _render_map(place, out, width, height, narration, open_sea=home is None, home_entity=home,
                           resolved=True)


def _render_map(place: dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                narration: str = "", open_sea: bool = False, home_entity: geo.Country | None = None,
                resolved: bool = False, zoom: float = 1.0) -> Path | None:
    """Regional locator map (round 6, 2026-11-03): the camera moves from a wide view to a close-up framed so the home
    fill covers 20-60 % of the graphics box (locator_span; camera_spans' log-space ease, then held) while the context
    globe turns into the site and fades out before the arrival; then the marker (MAP_MARKER_STYLE: the pin drops,
    squashes and settles with one ripple, or the dot pops) with the site's name beside it, the country's name set
    inside its fill, the seas in view named, and the dual-unit scale bar drawn from its zero tick. A point that
    stands for the topic's whole region (geo.is_region_fallback) is drawn as an area with "APPROX. LOCATION", never
    with a marker. `narration`: the run's narration (typography.spoken_system)."""
    import numpy as np
    if not geo.available():
        return None
    style = marker_style()
    lat0, lon0 = place["lat"], place["lon"]
    home = home_entity if resolved else geo.site_home(float(lon0), float(lat0), hint=str(place.get("country") or "")).country
    portrait = height > width
    k = min(width, height) / 1080
    # round 11 (coordinator directive 2): a view reaching POLAR_REACH is drawn on the azimuthal equidistant
    # projection centred on the site (PolarProjection), never plate carree run past the pole
    span_close = locator_span(lat0, lon0, home, width, height)
    span_wide, scale = max(40.0, span_close * 1.6), 3    # degrees of longitude across the frame (neighbours in view)
    # round 14 (review: Torshavn and Jan Mayen 9:16, framed at Europe scale, the island hidden under the dot): the
    # island close-up is decided first, so a high-latitude island is framed on itself (then drawn polar only where
    # that close-up's views still reach the pole's latitudes)
    island = island_span(lat0, lon0, home, width, height, span_close) \
        if not open_sea and abs(lat0) < POLAR_REACH else None
    if island is not None:   # round 7 (R6): a tiny island in an empty sea: zoomed to it (the wide view with it)
        span_close = island
        span_wide = max(span_close * 1.6, min(40.0, span_close * LOCATOR_MAX_ZOOM))
        lat0, lon0 = island_site(lat0, lon0, home, width, height, span_close)   # round 8: the pin on its island
    polar = is_polar_view(lat0, width, height, span_close, span_wide)
    if polar and island is None:
        span_close = locator_span(lat0, lon0, home, width, height, polar=True)
        span_wide = max(40.0, span_close * 1.6)
    if polar:
        log.info("Map: the view reaches %.0f degrees of latitude; drawn on a polar (azimuthal equidistant) "
                 "projection centred on the site.", POLAR_REACH)
    if zoom != 1.0:          # round 16 (review 1): framed closer for the home's fill to hold its name (logged)
        span_close = round(span_close * zoom, 4)
        span_wide = max(span_close * 1.6, min(span_wide, span_close * LOCATOR_MAX_ZOOM))
        log.info("Map framing: the close-up drawn %.2fx closer, at %.2f degrees, for the home's fill to hold its "
                 "name.", 1 / zoom, span_close)
    area = geo.is_region_fallback(place)
    # round 10 (review, 9:16): the pin stands MAP_PIN_Y_SHARE down the graphics box, not at the frame's centre
    # 100 px over the box's foot, so the home's ground south of the site is in the box for its name (Basel,
    # Golden Triangle and Utqiagvik set the name outside, on a neighbour or the sea). The view is centred south
    # of the site to put it there; a region's area stays at the centre.
    site_lat, site_lon = lat0, lon0
    if polar:
        lift = 0.0 if area else polar_lift(width, height)
    else:
        lat0, lift = view_centre_lat(site_lat, width, height, span_close, area)
    kx = width * scale / span_wide                        # px per degree of longitude in the base image
    coslat = max(0.2, math.cos(math.radians(lat0)))
    bw, bh = width * scale, height * scale
    kf = width / span_close                               # frame px per degree of latitude at rest
    zf = span_wide / span_close  # the close-up the camera ends on (labels must not be cut by it)
    pv_base = PolarProjection(lon0, site_lat, bw / 2, bh / 2 - lift * scale / zf, kx) if polar else None
    pv_frame = PolarProjection(lon0, site_lat, width / 2, height / 2 - lift, kf) if polar else None

    # Equirectangular with the x axis shrunk by cos(lat) so shapes keep their proportions (a polar view: pv_base)
    def project(lon: float, lat: float) -> tuple[float, float]:
        if pv_base is not None:
            return pv_base(lon, lat)
        return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx

    def to_frame_eq(lon: float, lat: float) -> tuple[float, float]:
        return width / 2 + (lon - lon0) * kf * coslat, height / 2 - (lat - lat0) * kf
    to_frame: Callable[[float, float], tuple[float, float]] = pv_frame if pv_frame is not None else to_frame_eq

    final_view = (bw / 2 - bw / 2 / zf, bh / 2 - bh / 2 / zf, bw / 2 + bw / 2 / zf, bh / 2 + bh / 2 / zf)
    to_base = lambda x, y: (final_view[0] + x * (final_view[2] - final_view[0]) / width,
                            final_view[1] + y * (final_view[3] - final_view[1]) / height)

    def box_to_base(b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return to_base(b[0], b[1]) + to_base(b[2], b[3])
    label = str(place["label"])
    anchor = (width / 2, height / 2 - lift)
    land_f, home_f = view_masks(width, height, lon0, lat0, span_close, home, polar=pv_frame)
    sx0, sy0, sx1, sy1 = get_text_box(width, height)
    # the site's name by its marker (A); a region's point: its name over the area, "APPROX. LOCATION" under it
    f_ap = get_font("Montserrat", round(30 * k), weight=700)
    if area:
        lines, f_area = marker_label_lines(label, width, height)
        step = round(cap_height(f_area) * 1.22)
        base_last = anchor[1] - 70 * k - 16 * k - cap_height(f_ap) - 16 * k
        rows = _label_rows(lines, f_area, anchor[0], base_last - step * (len(lines) - 1), "ms")
        lb = _rows_box(rows, f_area) or (anchor[0] - 1, base_last - 1, anchor[0] + 1, base_last)
        lay = MarkerLayout("area", anchor, tuple(rows), f_area.size, lb, "above", None,
                           (anchor[0] - 70 * k, anchor[1] - 70 * k, anchor[0] + 70 * k, anchor[1] + 70 * k))
    else:
        # round 10: on an island close-up the pin's label keeps off the island's fill where it can; round 13
        # (coordinator directive 2): on any close-up, off the site's own island or small country (its part of the
        # fill), and where the pin would float off the drawn land or cover more than PIN_ISLAND_COVER_MAX of that
        # part wherever its name goes, a precise dot on a leader instead (pin_fallback)
        part_f = site_part(home_f, land_f, anchor, k)
        lay = marker_layout(style, anchor, label, width, height, land=land_f,
                            home=part_f if (island is not None or part_f is not None) else None,
                            ground=ground_word(part_f, home_f, land_f))
        if style == "pin":
            why_dot = pin_fallback(lay, land_f, part_f, k)
            if why_dot:
                log.info("Map: %s; the site marked by a dot on a leader instead.", why_dot)
                style = "leader"
                lay = leader_marker(anchor, label, width, height, land_f, part_f, k)
    # the country's name never stacks on the site's name like a title over it: it keeps out of the run beyond the
    # label on its side (to 200 px past it), as well as the label's own air
    lb = lay.label_box
    reach = 200 * k
    if lay.position in ("above", "leader") and lb[3] <= anchor[1]:
        column = (lb[0], lb[1] - reach, lb[2], anchor[1])
    elif lay.position in ("below", "flip") or lb[1] >= anchor[1]:
        column = (lb[0], anchor[1], lb[2], lb[3] + reach)
    elif lay.position == "right":
        column = (anchor[0], lb[1], lb[2] + reach, lb[3])
    else:
        column = (lb[0] - reach, lb[1], anchor[0], lb[3])
    words: list[tuple[float, float, float, float]] = [lb, lay.marker_box]
    rings = home_rings(home, to_frame, (sx0, sy0, sx1, sy1)) if home is not None else []
    if isinstance(rings, HomeRings):
        rings.allow = allowed_home_parts(home_f, anchor, k)
    # the scale bar, placed after the site's and the country's words (C)
    bar = scale_bar(site_lat, kf, narration, bar_range=scale_range(width, height))
    cname = None
    # round 15 (coordinator directive 1): the home's own name, its entity's (geo.entity_name: written out, accents
    # folded, never an initialism), and its shorter written-out forms
    c_text = str((home or {}).get("full_name") or (home or {}).get("display") or "").upper()
    c_text = home_title_form(c_text, label, home)
    forms = [c_text] + [str(a_).upper() for a_ in (home or {}).get("short_names", [])
                        if geo.clean_name(str(a_)) and str(a_).upper() != c_text]
    spot = None
    ids: tuple[npt.NDArray[np.uint16], int] | None = None
    comps_hl = (_components(home_f), _components(land_f))
    # round 16 (review 2: SPAIN set 430 px off Melilla on a leader to the Chafarinas, FRENCH POLYNESIA to Taha'a): a
    # name set outside its fill names, and has its leader to, the part of the home that holds the site (or the
    # nearest within NAME_SITE_REACH_PX), never another speck of it; the other parts count as other land there
    site_part_lab = _site_component(comps_hl[0], anchor, NAME_SITE_REACH_PX * k)
    home_site = (comps_hl[0][0] == site_part_lab) if comps_hl[0] is not None and site_part_lab else home_f
    home_drop = "" if home is not None else f"the open sea: no ground within {geo.SITE_NEAR_KM:.0f} km of the site"
    if home is not None and c_text:
        # its place on geometry first, so the bar, the globe and the neighbours keep clear of it; its ink is
        # measured on the finished frame below. Round 15 (coordinator directive 2, LEADER OR DROP): inside its own
        # fill, in every form and at every size, before any place outside it; outside only over open water (never
        # over any land, of any size: RUSSIA was set on Lithuania at Kaliningrad), with a leader to its own ground;
        # every place through the verifier (home_name_fault), else the name is dropped (logged)
        ids = view_country_ids(width, height, to_frame, home)
        refused: list[Box] = []
        last_fault = ""
        for outside in (False, True):
            for form in forms:
                tries = 0
                while spot is None and tries <= NAME_RETRIES:
                    got_ = country_spot(form, rings, home_site, land_f, anchor, lb, [column, *refused], words[:2],
                                        width, height, skip_inside=outside, ids=ids, inside_only=not outside)
                    if got_ is None:
                        break
                    fault = home_name_fault(got_.box, got_.inside, home_f if got_.inside else home_site, land_f,
                                            anchor, k, comps_hl if got_.inside else None)
                    if not fault:
                        spot = got_
                        if form != c_text:
                            log.info("Map: the country's name set in its shorter form %r (%r has no place %s).",
                                     form, c_text, "outside its fill" if outside else "in its fill")
                        c_text = form
                        break
                    last_fault = fault
                    log.info("Map: name check: %r at (%.0f, %.0f) is %s; set elsewhere.", form, got_.box[0],
                             got_.box[1], fault)
                    refused.append(_grow(got_.box, 8 * k))
                    tries += 1
                if spot is not None:
                    break
            if spot is not None:
                break
        if spot is None and zoom * HOME_ZOOM_STEP >= HOME_ZOOM_MIN and not polar and not area \
                and int(np.count_nonzero(home_f)) >= HOME_ZOOM_FILL_PX * k * k:
            # round 16 (review 1: Luxembourg's 250 x 420 px fill held no name beside LUXEMBOURG CITY, a regression
            # against HEAD): a fill large enough to be a country, not a speck, that holds its name nowhere is drawn
            # closer (HOME_ZOOM_STEP a time, to HOME_ZOOM_MIN) before the name is dropped
            log.info("Map: the home's name %r has no place at %.2f degrees; framed closer.", c_text, span_close)
            return _render_map(place, out, width, height, narration, open_sea, home_entity=home, resolved=True,
                               zoom=zoom * HOME_ZOOM_STEP)
        if spot is None:
            home_drop = "fits neither its own fill nor open water beside it" + (f" (last: {last_fault})"
                                                                                 if last_fault else "")
            log.warning("Map: name check: the home's name %r %s in any form; not named.", c_text, home_drop)
        if spot is not None:
            words.append(spot.box)
            cname = CountryName(c_text, spot.xy, spot.px, brand.INK, spot.box, spot.inside, 0.0, 0.0)
    import cv2
    coast = (land_f.astype(np.uint8) - cv2.erode(land_f.astype(np.uint8), np.ones((3, 3), np.uint8))) > 0
    if ids is not None:      # round 8 (review 4): the land borders are obstacles too (Narva's bar sat on Russia's)
        coast |= land_borders(ids[0], width, height)
    # and the marker keeps the bar off by MARKER_SCALE_AIR more than the words do (Narva's bar sat 40 px under the
    # pin's tip and read as part of the marker)
    mb = lay.marker_box
    m_air = MARKER_SCALE_AIR * k
    sbl = place_scale_bar(bar, width, height, words + [(mb[0] - m_air, mb[1] - m_air, mb[2] + m_air, mb[3] + m_air)],
                          home=home_f, obstacles=coast, hero=_union([lb, mb]), rise_rows=SCALE_RISE_ROWS_LOCATOR)
    gbox = place_globe(width, height, home_f, words + [sbl.box])
    # round 9 (coordinator R6): on an island close-up the globe stays at rest as its locator inset (the frame is
    # mostly ocean; the globe says where in it the island is), instead of fading out before the pin lands; round 12
    # (review): so too on a close-up with next to no land in it (a site marked on the open sea: it was a pin on
    # blank sea, nothing else in the frame)
    globe_rest = island is not None or open_sea or home_share(land_f, get_text_box(width, height)) < LOCATOR_EMPTY_LAND

    def from_frame(x: float, y: float) -> tuple[float, float]:
        if pv_frame is not None:
            return pv_frame.inverse(x, y)
        return lon0 + (x - width / 2) / (kf * coslat), lat0 - (y - height / 2) / kf
    wl = water_labels(width, height, from_frame, land_f, words + [sbl.box] + ([gbox] if gbox else []),
                      to_frame=to_frame, site=anchor)
    reserved = tuple(box_to_base((b[0] - 16, b[1] - 16, b[2] + 16, b[3] + 16)) for b in words + [column])
    if spot is not None and not spot.inside:    # round 10 (review 2): no neighbour's name stacked on the home's
        reserved += (box_to_base(_grow(spot.box, COUNTRY_OUT_NB_AIR * k)),)
    # round 9: the bar keeps a base name MARKER_LABEL_CLEAR off, as it does every other word (16: Iguazu's BOLIVIA)
    # round 10 (review): SCALE_NAME_AIR, not 28 px (Hallstatt's and Utqiagvik's 16:9 bars stacked under ITALY and
    # RUSSIA and read as one block with them)
    sa = SCALE_NAME_AIR * k
    reserved += (box_to_base((sbl.box[0] - sa, sbl.box[1] - sa, sbl.box[2] + sa, sbl.box[3] + sa)),)
    reserved += tuple(box_to_base((w.box[0] - 12, w.box[1] - 12, w.box[2] + 12, w.box[3] + 12)) for w in wl)
    if gbox:
        reserved += (box_to_base((gbox[0] - 16, gbox[1] - 16, gbox[2] + 16, gbox[3] + 16)),)
    labels: list[dict[str, Any]] = []
    name_px = round(map_name_px(width, height) * scale / zf)   # a neighbour's name map_name_px tall on the close-up
    ctx = ReliefContext(base_per_px=scale / zf, marker=to_base(*anchor),
                        plates=tuple(box_to_base(b) for b in [lb] + ([cname.box] if cname else [])), wide=not portrait)

    def build(skip: set[str] | frozenset[str]):
        lab: list[dict[str, Any]] = []
        token = _RELIEF_CTX.set(ctx)
        try:
            b = relief_base(lat0, lon0, bw, bh, kx, coslat, home, project, final_view,
                            label_bottom_fraction=get_text_bounds(width, height)[1] / height, reserved=reserved,
                            final_safe=to_base(sx0, sy0) + to_base(sx1, sy1), transient=lab, name_px=name_px,
                            skip=skip, polar=pv_base)
        finally:
            _RELIEF_CTX.reset(token)
        return b, lab
    base, labels = build(frozenset())
    n = int(CLIP_SECONDS * VIDEO_FPS)
    zooms = [span_wide / sp for sp in camera_spans(n, span_wide, span_close, MAP_TRAVEL, VIDEO_FPS)]
    zf_last = zooms[-1]

    def resting(b: Image.Image) -> Image.Image:
        return b.resize((width, height), Image.BICUBIC, box=(bw / 2 - bw / 2 / zf_last, bh / 2 - bh / 2 / zf_last,
                                                             bw / 2 + bw / 2 / zf_last, bh / 2 + bh / 2 / zf_last)).convert("RGB")
    rest = resting(base)
    # Neighbours' names under the measured-contrast rule (round 4): each name drawn into the base is measured on
    # the resting view, and the base is drawn again without any name under brand.TEXT_CONTRAST.
    names = _base_name_plates(labels, rest, final_view, width, height)
    weak = {nm.text for nm in names if nm.contrast is not None and nm.contrast < brand.TEXT_CONTRAST}
    if weak:
        base, labels = build(weak)
        rest = resting(base)
        names = _base_name_plates(labels, rest, final_view, width, height)
    # round 9 (review 6): the neighbours by adjacency. A name the base drew for a country that does not share the
    # home's border, while one that does is unnamed, gives way to it (the base is drawn again without it; the
    # close-up names the adjacent ones below)
    dropped: set[str] = set()
    if home is not None:
        in_view = neighbours_in_view(width, height, home, to_frame, anchor)
        adjacent = [nm for nm, adj in in_view if adj]
        base_named = {nm.text for nm in names} | {str(lab["name"]).upper() for lab in labels
                                                    if lab.get("sprite") is not None}
        missing_adj = [nm for nm in adjacent[:NEIGHBOUR_MAX] if nm not in base_named]
        # the furthest first; round 11: an adjacent one ranked past NEIGHBOUR_MAX too (Aachen's CZECHIA, far off,
        # kept its slot from BELGIUM by the pin)
        top_adj = set(adjacent[:NEIGHBOUR_MAX])
        far_named = [nm for nm, adj in reversed(in_view) if nm in base_named and (not adj or nm not in top_adj)]
        # round 10 (review 1): as many give way as leave the adjacent ones their slots within NEIGHBOUR_MAX
        # (Pannonhalma kept CZECHIA, which does not border Hungary, while SLOVENIA, which does, had no slot)
        n_drop = max(len(missing_adj), len(base_named) + len(missing_adj) - NEIGHBOUR_MAX) if missing_adj else 0
        drop = set(far_named[:n_drop])
        dropped = set(drop)
        if drop:
            log.info("Map: the base's name(s) %s give way to the home's neighbour(s) %s.", ", ".join(sorted(drop)),
                     ", ".join(missing_adj))
            weak = set(weak) | drop
            base, labels = build(weak)
            rest = resting(base)
            names = _base_name_plates(labels, rest, final_view, width, height)
    # round 13 (directive 1): the name verifier's neighbour check on every name the base draws on the close-up;
    # one over a third country's ground (or mostly off its own) is drawn again without it (logged)
    table = view_country_table(width, height, to_frame)
    bad_base = {nm.text: f for nm in names if (f := neighbour_name_fault(nm.text, nm.box, table))}
    if bad_base:
        for t_, f_ in sorted(bad_base.items()):
            log.info("Map: name check: the neighbour %r drawn on the base is %s; dropped.", t_, f_)
        weak = set(weak) | set(bad_base) | {t_.title() for t_ in bad_base}
        dropped |= set(bad_base)
        base, labels = build(weak | {str(lab["name"]) for lab in labels if str(lab["name"]).upper() in bad_base})
        rest = resting(base)
        names = _base_name_plates(labels, rest, final_view, width, height)
    plan = map_label_plan(labels, zooms, width, height, bw, bh)
    # the seas' names, then every word planned on the resting view (their halos solved there)
    waters: list[tuple[WaterLabel, _MaskPlate]] = []
    for w_ in wl:
        got = water_plate(rest, w_)
        if got is not None:
            waters.append((w_, got[1]))
    if cname is not None and spot is not None:
        cname = country_name(rest, c_text, spot, rings, home_site, land_f, anchor, lb, [column], words[:2], width, height,
                             ids)
    # round 6 review: the neighbours the base leaves unnamed on the close-up, named on their own land in view
    # round 8: the names the frames draw that are still shown at rest count as named too (Zambia was named twice,
    # its frame-drawn name and a close-up name beside it)
    shown_rest = [lab for lab in plan[-1] if lab["mask"] is not None]
    rest_boxes = [(float(lab["box"][0]), float(lab["box"][1]), float(lab["box"][0] + lab["mask"].width),
                   float(lab["box"][1] + lab["mask"].height)) for lab in shown_rest]
    # round 10 (review 1): the globe's box keeps the close-up's names out only where the globe stays at rest (an
    # island's locator inset); elsewhere it is gone before the pin lands and a name there fades in after it
    # (Pannonhalma's SLOVAKIA had its only room under the faded globe's corner)
    taken = [*words, _grow(sbl.box, max(0.0, SCALE_NAME_AIR - MARKER_LABEL_CLEAR) * k),
             *([gbox] if gbox and globe_rest else []), *(w_.box for w_, _p in waters),
             *(nm.box for nm in names), *([cname.box] if cname else []), *rest_boxes]
    if cname is not None and not cname.inside:
        taken.append(_grow(cname.box, COUNTRY_OUT_NB_AIR * k))
    already = {nm.text for nm in names} | {str(lab["name"]).upper() for lab in shown_rest}
    already |= {t.replace(" ", "") for t in already}
    wanted_nb = max(0, NEIGHBOUR_MAX - len(names) - len(shown_rest))
    # round 10 (review 1): a far name the base gave way with is not set again by the close-up while an adjacent
    # one could be (Pannonhalma named POLAND again after SLOVAKIA found no room); only to reach two names
    near_names = close_neighbours(width, height, home, to_frame, taken, already | dropped, wanted_nb, anchor)
    if dropped and len(names) + len(shown_rest) + len(near_names) < 2:
        log.info("Map: under two neighbours named; the far name(s) %s allowed again.", ", ".join(sorted(dropped)))
        near_names += close_neighbours(width, height, home, to_frame, taken + [n.box for n in near_names],
                                       already | {n.name.upper() for n in near_names},
                                       max(0, 2 - len(names) - len(shown_rest) - len(near_names)), anchor)
    # round 12 (review: Corinth's TURKEY, which borders Greece, gave way to BULGARIA, which then found no room):
    # an adjacent name the base gave way with is set again on the close-up while the slots it left are empty
    back = sorted(nm for nm in dropped if nm in set(adjacent)) if home is not None else []
    n_named = len(names) + len(shown_rest) + len(near_names)
    if back and n_named < NEIGHBOUR_MAX:
        others = {nm for nm, _adj in in_view} - set(back)
        again = close_neighbours(width, height, home, to_frame, taken + [n.box for n in near_names],
                                 already | others | {n.name.upper() for n in near_names}, NEIGHBOUR_MAX - n_named,
                                 anchor)
        if again:
            log.info("Map: the adjacent name(s) %s set again on the close-up (the neighbour(s) they gave way to "
                     "found no room).", ", ".join(n.name.upper() for n in again))
            near_names += again
    # round 13 (coordinator directive 1): the name verifier, last, on every name the close-up draws: one that
    # fails is not drawn (logged with why); the verdicts kept in LAST_NAME_CHECKS and the layout sidecar
    checks: list[NameCheck] = []
    keep_w = []
    for w_, pl in waters:
        f_ = water_name_fault(w_.name, w_.box, from_frame, land_f)
        checks.append(NameCheck("water", w_.name.upper(), w_.box, f_))
        if not f_:
            keep_w.append((w_, pl))
    waters = keep_w
    if cname is not None:
        f_ = home_name_fault(cname.box, cname.inside, home_f if cname.inside else home_site, land_f, anchor, k,
                             comps_hl if cname.inside else None)
        checks.append(NameCheck("home", cname.text, cname.box, f_))
        if f_:
            cname = None
            home_drop = f"failed the final check: {f_}"
    # round 15 (directive 2): a home name set outside its fill has a leader to its own ground; a site off all
    # drawn ground (offshore, directive 3) a leader from its marker to the land it belongs to
    c_leader = ground_leader(cname.box, home_site, k) if cname is not None and not cname.inside else None
    site_leader = None
    if home is not None and not area and not bool(_at(home_f, *anchor)):
        mr = 14 * k
        site_leader = ground_leader((anchor[0] - mr, anchor[1] - mr, anchor[0] + mr, anchor[1] + mr), home_f, k,
                                    reach=600 * k)
        if site_leader is not None:
            log.info("Map: the marker stands %.0f px off its land; a leader drawn to it.", math.dist(*site_leader))
    keep_n = []
    for nb in near_names:
        f_ = neighbour_name_fault(nb.name, nb.box, table)
        checks.append(NameCheck("neighbour", nb.name.upper(), nb.box, f_))
        if not f_:
            keep_n.append(nb)
    near_names = keep_n
    for nm in names:
        checks.append(NameCheck("neighbour", nm.text, nm.box, ""))   # checked above, on the base
    for lab in shown_rest:
        b_ = (float(lab["box"][0]), float(lab["box"][1]), float(lab["box"][0] + lab["mask"].width),
              float(lab["box"][1] + lab["mask"].height))
        f_ = neighbour_name_fault(str(lab["name"]), b_, table)
        checks.append(NameCheck("neighbour", str(lab["name"]).upper(), b_, f_))
        if f_:
            for fr in plan:
                for lab2 in fr:
                    if lab2["name"] == lab["name"]:
                        lab2["mask"] = None
    shown_rest = [lab for lab in shown_rest if lab["mask"] is not None]
    for c_ in checks:
        if not c_.ok:
            log.warning("Map: name check: the %s name %r at (%.0f, %.0f) is %s; not drawn.", c_.kind, c_.text,
                        c_.box[0], c_.box[1], c_.fault)
    LAST_NAME_CHECKS[:] = checks
    nb_plates = neighbour_plates(rest, near_names)
    nb_after_globe = {f"neighbour_{n.name.upper()}" for n in near_names if gbox is not None and not globe_rest
                      and LayoutConstraintSolver.boxes_intersect(n.box, gbox, MARKER_LABEL_CLEAR * k)}
    if home is not None:
        named_now = {nm.text for nm in names} | {str(lab["name"]).upper() for lab in shown_rest} \
            | {nb.name.upper() for nb in near_names}
        unplaced = [nm for nm in adjacent[:NEIGHBOUR_MAX] if nm not in named_now]
        if unplaced:     # round 11 (review 6): says what was named instead, or that none was
            others = sorted(named_now - set(adjacent))
            log.info("Map: the home's neighbour(s) %s have no room for a name in the box; %s.", ", ".join(unplaced),
                     f"named instead: {', '.join(others)}" if others else "no other neighbour named either")
    if len(names) + len(shown_rest) + len(near_names) < 2:
        # round 13 (directive 3): says why: names are set only inside the graphics box (the captions' zone under it
        # is kept clear), and no other neighbour's ground there holds its name with air
        log.info("Map: %d neighbour(s) named on the close-up (2 wanted): no other neighbour's ground inside the "
                 "graphics box holds its name.", len(names) + len(shown_rest) + len(near_names))
    label_plates = label_plates_for(rest, lay)
    if area:   # the region's point: say so, in the system-label pattern ("APPROX. DEPTHS")
        under = max(pl.box[3] for _, pl in label_plates)
        label_plates.append(("approx", plan_text(rest, (width // 2, under + 16 * k + cap_height(f_ap)), "APPROX. LOCATION",
                                                 f_ap, MUTED, "ms", track=4, halo_px=21, min_halo=0.8)))
    c_plates = [("country", country_plate(rest, cname))] if cname is not None else []
    bar_plates = _scale_bar_plates(_with_bar(rest, bar, sbl.x, sbl.y), bar, sbl.x, sbl.y) if bar else []
    # the context globe: centred on the site's longitude, turning into it (cached per tenth of a degree)
    lon_half_c = (bw / zf_last / 2) / (kx * coslat)
    lat_half_c = (bh / zf_last / 2) / kx
    footprint: Box | list[tuple[float, float]] = (lon0 - lon_half_c, lat0 - lat_half_c, lon0 + lon_half_c,
                                                  lat0 + lat_half_c)
    if pv_frame is not None:      # round 11: the polar close-up's own outline, its edge walked on the frame
        footprint = [pv_frame.inverse(x, y) for x, y in
                     [(width * f / 8, 0) for f in range(9)] + [(width, height * f / 8) for f in range(9)]
                     + [(width * (1 - f / 8), height) for f in range(9)] + [(0, height * (1 - f / 8)) for f in range(9)]]
    globes: dict[float, Image.Image] = {}

    globe_out = math.inf if globe_rest else None
    if globe_rest:
        log.info("Map: the globe held at rest as the %s locator inset%s.",
                 "close-up's" if island is not None else "open sea's", "" if gbox else " (no corner holds it)")

    def globe_at(t: float) -> Image.Image | None:
        if gbox is None or globe_alpha(t, globe_out) <= 0.002:
            return None
        lo, la = globe_centre(site_lon, site_lat, t)
        key = round(lo, 1)
        if key not in globes:
            globes[key] = globe_inset(gbox[2] - gbox[0], lo, la, home, footprint, (site_lon, site_lat))
        return globes[key]
    t_pin = MAP_PIN_AT
    t_bar = MAP_PIN_AT + 0.5
    last: list[Image.Image] = []
    marker_masks: list[tuple[str, Image.Image]] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            zoom = zooms[i]
            cw, ch = bw / zoom, bh / zoom
            box = (max(0.0, bw / 2 - cw / 2), max(0.0, bh / 2 - ch / 2), min(float(bw), bw / 2 + cw / 2),
                   min(float(bh), bh / 2 + ch / 2))
            im = base.resize((width, height), Image.BICUBIC, box=box).convert("RGB")
            for lab in plan[i]:
                if lab["mask"] is not None:
                    if lab.get("halo") is not None:
                        im.paste(brand.INK, lab["box"][:2], lab["halo"])
                    im.paste(MAP_NAME, lab["box"][:2], lab["mask"])
            im = _draw_globe(im, globe_at(t), gbox or (0, 0, 0, 0), t, globe_out)
            a_w = _ease((t - t_pin) / 0.5)
            for w_, pl in waters:
                if a_w > 0:
                    im.paste(WATER_FILL, w_.origin, w_.mask.point(lambda v, a=a_w * pl.alpha: int(v * a)))
            a_nb = _ease((t - (MAP_TRAVEL - 0.2)) / 0.4)    # the close-up's own neighbour names, once at rest
            a_nb_g = _ease((t - (GLOBE_OUT + GLOBE_OUT_S)) / 0.4)   # round 10: one where the globe was, after it
            for _n, pl in nb_plates:
                a_ = a_nb_g if _n in nb_after_globe else a_nb
                if a_ > 0:
                    im = draw_plate(im, pl, a_)
            a_lab, rise = marker_label_motion(t, "pin" if style == "pin" and not area else "dot")
            for _n, pl in c_plates:
                im = draw_plate(im, pl, a_lab)
            if c_leader is not None and a_lab > 0:
                im = _draw_leader(im, c_leader, k * 0.75, 1.0, a_lab)
            if site_leader is not None and a_lab > 0:
                im = _draw_leader(im, site_leader, k * 0.75, 1.0, a_lab)
            d = ImageDraw.Draw(im, "RGBA")
            if area:      # the region as a soft area: a gold-edged disc that settles to its size
                a_ar = _ease((t - t_pin) / 0.35)
                if a_ar > 0:
                    r = 70 * k * (1 + 0.08 * math.sin(math.pi * min(1.0, (t - t_pin) / 0.35)))
                    d.ellipse([anchor[0] - r, anchor[1] - r, anchor[0] + r, anchor[1] + r], fill=(*GOLD, int(46 * a_ar)),
                              outline=(*GOLD, int(230 * a_ar)), width=4)
            elif style == "pin":
                im = _draw_pin(im, anchor, k, pin_motion(t))
            else:
                s_, rd, ra = dot_motion(t)
                im = _draw_dot(im, anchor, k, s_, 1.0, (rd, ra))
            for _n, plate in label_plates:  # the site's name rises 12 px as it fades in
                im = draw_plate(im, plate, a_lab, round(rise))
            if lay.leader is not None:      # the leader draws from the label toward the dot once it is in
                im = _draw_leader(im, lay.leader, k, _ease((t - t_pin - DOT_POP_S - 0.5) / 0.2))
            if bar:
                ab, al = scale_bar_motion(t, t_bar)
                if ab > 0:
                    _draw_scale_bar(im, bar, sbl.x, sbl.y, ab)
                    for _n, pl in bar_plates:
                        im = draw_plate(im, pl, al)
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], label_plates + c_plates + bar_plates + nb_plates
                        + [(f"neighbour_{nm.text}", nm) for nm in names]
                        # round 7 (review 6): a name drawn on the frames that is still shown at rest, measured too
                        + [(f"neighbour_{lab['name'].upper()}", _MaskPlate(str(lab["name"]).upper(), lab["mask"],
                                                                           lab["box"][:2], MAP_NAME, last[0]))
                           for lab in plan[-1] if lab["mask"] is not None]
                        + [(f"water_{w_.name}", pl) for w_, pl in waters])
        if not area:
            marker_masks.append(("marker", mark_mask((width, height), lambda dd: dd.polygon(
                pin_outline(anchor, k), fill=255) if style == "pin" else dd.ellipse(
                [anchor[0] - 12 * k, anchor[1] - 12 * k, anchor[0] + 12 * k, anchor[1] + 12 * k], fill=255))))
        record_nontext(out, last[0], marker_masks)
        if gbox is not None:
            _record_globe_contrast(out, rest, globe_at(1.0), gbox)
        import json
        Path(str(out) + ".scale.json").write_text(json.dumps(
            {**dataclasses.asdict(bar), "label": bar.label, "x": sbl.x, "y": sbl.y, "box": list(sbl.box)},
            indent=1), encoding="utf-8", newline="\n")
        Path(str(out) + ".layout.json").write_text(json.dumps({
            "style": style if not area else "area", "label": label, "anchor": list(anchor),
            "label_box": list(lay.label_box),
            "position": lay.position, "leader": [list(p) for p in lay.leader] if lay.leader else None,
            "country": None if cname is None else {"text": cname.text, "box": list(cname.box), "inside": cname.inside,
                                                   "ink": cname.ink,
                                                   "leader": [list(p_) for p_ in c_leader] if c_leader else None},
            "site_leader": [list(p_) for p_ in site_leader] if site_leader else None,
            "home_drop": home_drop if cname is None else "",
            "globe": list(gbox) if gbox else None, "span_close": span_close,
            "water": [w_.name for w_, _p in waters],
            "neighbours": [nm.text for nm in names] + [str(lab["name"]).upper() for lab in shown_rest]
            + [nb.name.upper() for nb in near_names],
            "marker": style if not area else "area",
            # round 14: the entity the home's name stands for (Natural Earth's NAME), for a check outside the
            # renderer, and every name's checked points as (lon, lat) (the water's alone before)
            "home": None if home is None else str(home.get("display") or ""),
            "anchor_lonlat": list(from_frame(*anchor)),
            "name_checks": [{"kind": c_.kind, "text": c_.text, "box": list(c_.box), "fault": c_.fault,
                             "lonlat": [list(from_frame(x_, y_)) for x_, y_ in name_check_points(c_.box)]}
                            for c_ in checks]}, indent=1), encoding="utf-8", newline="\n")
    return result


def _record_globe_contrast(out: Path, rest: Image.Image, globe: Image.Image | None,
                           gbox: tuple[int, int, int, int]) -> None:
    """The globe's disc measured on the frame it is fully shown on (the resting map under it; it is gone by the
    last frame), added to <clip>.contrast.json's non-text rows."""
    import json
    side = Path(str(out) + CONTRAST_SIDECAR)
    if globe is None or not side.exists():
        return
    frame = _draw_globe(rest.copy(), globe, gbox, 1.0)
    # its edge (the GOLD_DIM limb inside the INK ring) against the map around it: what lifts it off the ground
    cx, cy, r = (gbox[0] + gbox[2]) / 2, (gbox[1] + gbox[3]) / 2, (gbox[2] - gbox[0]) / 2 - GLOBE_RING
    limb = mark_mask(frame.size, lambda d: d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=255, width=2))
    ro = r + GLOBE_RING
    ring = mark_mask(frame.size, lambda d: d.ellipse([cx - ro, cy - ro, cx + ro, cy + ro], outline=255,
                                                     width=GLOBE_RING))
    # the edge that lifts it off its ground: the INK ring on light land, the GOLD_DIM limb on the dark sea
    both = nontext_rows(frame, None, [("globe_limb", limb), ("globe_ring", ring)], rest)
    rows = [max(both, key=lambda r_: r_["drawn"]) | {"name": "globe"}] if both else []
    record = json.loads(side.read_text(encoding="utf-8"))
    record.setdefault("nontext", [])
    record["nontext"] = [r for r in record["nontext"] if r.get("name") != "globe"] + rows
    vals = [r["graded"] if r.get("graded") is not None else r["drawn"] for r in record["nontext"]]
    record["nontext_min"] = min(vals) if vals else None
    record.setdefault("nontext_target", NONTEXT_MIN)
    side.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")


def map_label_plan(labels: list[dict[str, Any]], zooms: list[float], width: int, height: int,
                   bw: int, bh: int) -> list[list[dict[str, Any]]]:
    """Each frame's country names: {"name", "box": its frame pixels, "mask": what is pasted, None for a name
    drawn into the base}. A name outside the close-up is shown only while its whole box lies in the safe box
    (get_text_box); the zoom only ever carries it outward, so it is shown from the first frame until just
    before it would be cut, fading out over 0.3 s. A name not wholly inside on the first frame is not drawn."""
    safe = get_text_box(width, height)
    fade = max(1, round(0.3 * VIDEO_FPS))

    def frame_box(box, zoom):
        cw, ch = bw / zoom, bh / zoom
        x0, y0 = bw / 2 - cw / 2, bh / 2 - ch / 2
        sx, sy = width / cw, height / ch
        left, top = round((box[0] - x0) * sx), round((box[1] - y0) * sy)
        return (left, top, left + max(1, round((box[2] - box[0]) * sx)), top + max(1, round((box[3] - box[1]) * sy)))

    def within(b):
        return safe[0] <= b[0] and b[2] <= safe[2] and safe[1] <= b[1] and b[3] <= safe[3]

    plan: list[list[dict[str, Any]]] = [[] for _ in zooms]
    for lab in labels:
        boxes = [frame_box(lab["box"], z) for z in zooms]
        if lab["sprite"] is None:
            for i, b in enumerate(boxes):
                plan[i].append({"name": lab["name"], "box": b, "mask": None})
            continue
        shown = 0
        while shown < len(boxes) and within(boxes[shown]):
            shown += 1
        for i in range(shown):
            b = boxes[i]
            mask = lab["sprite"].resize((b[2] - b[0], b[3] - b[1]), Image.BICUBIC)
            halo = lab["halo"].resize(mask.size, Image.BICUBIC) if lab.get("halo") is not None else None
            # round 7: a name never cut (wholly inside to the last frame) holds; only one the frame will cut fades
            alpha = 1.0 if shown == len(boxes) else min(1.0, (shown - i) / fade)
            if alpha < 1.0:
                mask = mask.point(lambda v, a=alpha: int(v * a))
                halo = None if halo is None else halo.point(lambda v, a=alpha: int(v * a))
            plan[i].append({"name": lab["name"], "box": b, "mask": mask, "halo": halo})
    return plan


def get_text_bounds(width: int, height: int) -> tuple[int, int]:
    """(top, bottom) between which a graphic's text and diagrams stay. 16:9 (the documentary): broadcast
    title-safe above the caption line. 9:16: the Reel box the platforms leave free, less the caption band
    (safe_zones.graphics_box), so no figure sits under a platform's interface or reads as a caption."""
    if width > height:
        return int(height * 0.12), int(height * 0.76)  # 130px to 820px (captions center at 965px)
    box = safe_zones.graphics_box(width, height)
    return max(round(330 * height / VIDEO_HEIGHT), box[1]), box[3]


def get_text_box(width: int, height: int) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) for a graphic's text; in 16:9 the whole title-safe width."""
    top, bottom = get_text_bounds(width, height)
    if width > height:
        return int(width * 0.08), top, int(width * 0.92), bottom
    box = safe_zones.graphics_box(width, height)
    return box[0], top, box[2], bottom


def centred_text_width(width: int, height: int) -> int:
    """The widest text centred on the frame that stays inside get_text_box."""
    x0, _, x1, _ = get_text_box(width, height)
    return int(2 * min(width / 2 - x0, x1 - width / 2))


# ----------------------------------------------------------------- timeline
def year_text(year: int, approximate: bool) -> str:
    era = f"{-year} BC" if year < 0 else (f"AD {year}" if year < 1000 else str(year))
    return ("c. " if approximate else "") + era


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def ink_box(xy: tuple[float, float], text: str, font, anchor: str = "la",
            track: float = 0.0) -> tuple[int, int, int, int] | None:
    """The ink box a TextPlate of `text` set at `xy` would have (typography.plan_text, no stroke), measured on a small
    mask of its own (round 4 review 5, 2026-11-02): no frame-sized glyph mask, no halo and no contrast solve, so a
    layout can try many places cheaply and plan only the one it keeps; and a place partly off the frame is measured
    whole (an inside check then rejects it) instead of clipped to the frame (Sinope's name planned at y = -2 crashed
    the route). None for text with no ink."""
    from .typography import _draw_tracked
    ox, oy = math.floor(xy[0]), math.floor(xy[1])
    memo = _INK_BOXES.setdefault(font, {})        # the box relative to the whole pixel depends only on the rest
    key = (text, anchor, track, xy[0] - ox, xy[1] - oy)
    if key not in memo:
        if len(memo) > 20_000:
            memo.clear()
        shown = drawable(text, font)
        bb = None
        if shown:
            w = math.ceil(tracked_width(shown, font, track)) + 4
            em = int(font.size)
            pad = em + 8
            dx, dy = -(w + pad), -(2 * em + pad)          # whole-pixel shift: the glyphs rasterise the same
            m = Image.new("L", (2 * w + 2 * pad, 4 * em + 2 * pad), 0)
            _draw_tracked(ImageDraw.Draw(m), (xy[0] - ox - dx, xy[1] - oy - dy), shown, font, 255, anchor, track)
            got = m.getbbox()
            bb = None if got is None else (got[0] + dx, got[1] + dy, got[2] + dx, got[3] + dy)
        memo[key] = bb
    r = memo[key]
    return None if r is None else (r[0] + ox, r[1] + oy, r[2] + ox, r[3] + oy)


_INK_BOXES: "weakref.WeakKeyDictionary[Any, dict[tuple, tuple[int, int, int, int] | None]]" = \
    weakref.WeakKeyDictionary()


_TRACK_PROBE = ImageDraw.Draw(Image.new("L", (8, 8)))   # typography.tracked_width's own probe, made once


def _tracked_w(text: str, font, track: float) -> float:
    """Use the same cached OpenType run as the eventual text plate."""
    return tracked_width(text, font, track)


def tracked_wrap(text: str, font, width: float, track: float, lines: int | None = None,
                 lone_share: float | None = None) -> list[str] | None:
    """`text` broken into the fewest lines that each fit `width` px drawn with `track` px of letter spacing (each
    line measured tracked, as drawn), or None where one word alone is wider. Of the breaks into that many lines it
    takes (round 4 review 6, 2026-11-03), in order: no lone word on the first line while a later line holds two
    or more ("FIRST / CHAMBERS CUT" read as a heading over its label), the narrowest widest line (balanced, as
    typography.balanced_wrap), then the evenest. A figure never breaks from its unit (typography.glue).
    Round 4 review 7 (2026-11-03): `lines` asks for exactly that many lines (None where the words cannot fill
    them, or the fewest is more), and with `lone_share` a lone first word counts as lone only while it is
    narrower than that share of the widest later line (as the 16:9 timeline's lone_first measures it), so a
    balanced break that leaves "MAUSOLEUM" as wide as the lines under it is taken over one that does not."""
    from .typography import _plain, _words, glue
    words = [_plain(w) for w in _words(glue(text))]
    if not words:
        return []
    memo: dict[tuple[int, int], float] = {}

    def w_of(i: int, j: int) -> float:          # words[i:j] on one line
        if (i, j) not in memo:
            memo[(i, j)] = _tracked_w(" ".join(words[i:j]), font, track)
        return memo[(i, j)]
    n = len(words)
    if any(w_of(i, i + 1) > width + 1e-6 for i in range(n)):
        return None
    count, i = 0, 0                              # the greedy count is the fewest lines
    while i < n:
        j = i + 1
        while j < n and w_of(i, j + 1) <= width + 1e-6:
            j += 1
        count, i = count + 1, j
    if lines is not None:
        if lines < count or lines > n:
            return None
        count = lines

    def stop_end(j: int) -> int:
        # round 6 review: a row (not the last) ending on an article or a preposition ("BUILDS A / ROYAL ESTATE")
        return 1 if j < n and words[j - 1].upper().strip(",.;:") in TALL_STOP_WORDS else 0

    @cache
    def rest(i: int, lines: int) -> tuple[int, float, float, tuple[int, ...]] | None:
        # words[i:] on exactly `lines` lines: (rows ending on a short word, widest, sum of squared widths, the
        # breaks), the fewest short-word ends, then the widest least
        if lines == 1:
            w = w_of(i, n)
            return (0, w, w * w, ()) if w <= width + 1e-6 else None
        best = None
        for j in range(i + 1, n - lines + 2):
            w = w_of(i, j)
            if w > width + 1e-6:
                break
            sub = rest(j, lines - 1)
            if sub is None:
                continue
            cand = (stop_end(j) + sub[0], max(w, sub[1]), w * w + sub[2], (j, *sub[3]))
            if best is None or cand[:3] < best[:3]:
                best = cand
        return best
    if count == 1:
        return [" ".join(words)]
    choices = []
    for j in range(1, n - count + 2):            # the first line's words, then the rest at their best
        w = w_of(0, j)
        if w > width + 1e-6:
            break
        sub = rest(j, count - 1)
        if sub is None:
            continue
        lone = j == 1 and n > count
        if lone and lone_share is not None:
            # lone only while narrower than lone_share of the widest later line, or one word to every line
            lone = w < lone_share * sub[1] or (count >= 3 and n == count)
        choices.append((lone, stop_end(j) + sub[0], round(max(w, sub[1]), 3), w * w + sub[2], (j, *sub[3])))
    if not choices:
        return None
    breaks = (0, *min(choices)[4], n)
    from itertools import pairwise
    return [" ".join(words[a:b]) for a, b in pairwise(breaks)]


def fit_tracked(probe: ImageDraw.ImageDraw, text: str, name: str, px: int, width: float, rows: int, lo: int,
                track: float, **kwargs: Any) -> tuple[list[str], Any, bool]:
    """typography.fit_text for lines drawn with `track` px of letter spacing: (lines, font, False), every size from
    `px` down to `lo` tried (_probe_tracked), else TextFitError (QF-23: never a dropped word or sentence, never an
    ellipsis)."""
    lines, font, short = _probe_tracked(probe, text, name, px, width, rows, lo, track, **kwargs)
    if short:
        raise TextFitError(text, tracked_wrap(text, font, width, track) or [text], font, width, rows)
    return lines, font, False


def _probe_tracked(probe: ImageDraw.ImageDraw, text: str, name: str, px: int, width: float, rows: int, lo: int,
                track: float, sentences: bool = True, weight: int | None = None, quiet: bool = False,
                **_kw: Any) -> tuple[list[str], Any, bool]:
    """The trial behind fit_tracked, for the timelines' layout search only: (lines, font, shortened). A shortened
    candidate is scored, never drawn: a timeline whose chosen layout still has one is refused (QF-23), so the
    layouts that hold every label whole are chosen exactly as before. Every
    size from `px` down to `lo` (2 px steps) is wrapped with each line measured as drawn, tracking included
    (tracked_wrap), so a label is cut only where no size gives it `rows` lines of `width` (round 4 review 6,
    2026-11-03: the tracking reserve was set from the lines wrapped at the largest size and never relaxed, so
    "POPULATION 1923 CARVED" was cut to "POPULATI…" at 270 px though both its lines fit at 34 px). Where nothing
    fits, at `lo`: whole sentences while they fit (`sentences`), else the most words that fit with an ellipsis,
    always cut at a word boundary; a first word too wide even alone is set smaller until it fits (logged), never
    broken. `probe` is kept for fit_text's signature."""
    text = drawable(" ".join((text or "").split()), name)
    if not text:
        return [], get_font(name, px, weight=weight), False
    sizes = list(range(px, lo - 1, -2)) + ([lo] if (px - lo) % 2 else [])
    font = field_font(text, get_font(name, px, weight=weight))
    for s in sizes or [px]:
        font = field_font(text, get_font(name, s, weight=weight))     # the label's lines in one face (QF-94)
        lines = tracked_wrap(text, font, width, track)
        if lines is not None and len(lines) <= rows:
            if s != px and not quiet:
                log.info("Text set at %dpx instead of %dpx to fit %d line(s) of %d px: %.60s", s, px, rows, width, text)
            return lines, font, False

    def fits(candidate: str, f) -> list[str] | None:
        got = tracked_wrap(candidate, f, width, track)
        return got if got is not None and len(got) <= rows else None
    if sentences:
        import re
        parts = re.split(r"(?<=[.!?])\s+", text)
        kept: list[str] = []
        for part in parts:
            if fits(" ".join(kept + [part]), font) is None:
                break
            kept.append(part)
        if kept and len(kept) < len(parts):
            if not quiet:
                log.warning("Text kept to %d of %d sentence(s) to fit %d line(s): %.80s", len(kept), len(parts), rows, text)
            return fits(" ".join(kept), font) or [], font, True
    words = text.split()
    lo_n, hi_n, best = 1, len(words) - 1, None   # the most words that fit (more words never fit better)
    while lo_n <= hi_n:
        mid = (lo_n + hi_n) // 2
        got = fits(" ".join(words[:mid]).rstrip(",;:-–—") + "…", font)
        if got is not None:
            best, lo_n = got, mid + 1
        else:
            hi_n = mid - 1
    if best is None:                             # not even one word: smaller, never a word broken
        first = words[0].rstrip(",;:-–—") + ("…" if len(words) > 1 else "")
        s = font.size
        while s > 8 and _tracked_w(first, font, track) > width:
            s -= 2
            font = field_font(text, get_font(name, s, weight=weight))
        best = [first]
        if not quiet:
                log.warning("Text set at %d px, under its %d px floor, so its first word is not broken: %.60s", s, lo,
                            text)
    elif not quiet:
        log.warning("Text shortened with an ellipsis to fit %d line(s) of %d px at %d px: %.80s", rows, width,
                    font.size, text)
    return best, font, True


def years_between(a: int, b: int) -> int:
    """Calendar years from year `a` to year `b` (negative years are BC; there is no year 0)."""
    return (b - a) - (1 if a < 0 < b else 0)


def gap_text(a: dict[str, Any], b: dict[str, Any]) -> str:
    """The elapsed time drawn on the spine between two entries ("40 YEARS LATER"; when either date is approximate,
    "ABOUT 1,500 YEARS LATER", rounded to the nearest 100 from 500 years, 10 from 50; "" for less than two years)."""
    years = years_between(int(a["year"]), int(b["year"]))
    if years < 2:
        return ""
    if a.get("approximate") or b.get("approximate"):
        # An approximate date cannot give an exact span (2026-11-02): round to what the dates support.
        step = 100 if years >= 500 else 10 if years >= 50 else 1
        return f"ABOUT {max(step, round(years / step) * step):,} YEARS LATER"
    return f"{years:,} YEARS LATER"


def spaced_positions(events: list[dict[str, Any]], y_first: float, y_last: float, need: float) -> list[float]:
    """Each entry's y on the vertical spine: every step at least `need`, and the room left over shared out by the
    logarithm of the years elapsed, so 2,600 years read longer than 40 without squeezing the recent dates
    (2026-11-01; evenly spaced before, an ordered list rather than a time scale)."""
    import math
    from itertools import pairwise
    n = len(events)
    if n < 2:
        return [y_first]
    gaps = [math.log10(1 + max(0, years_between(int(a["year"]), int(b["year"]))))
            for a, b in pairwise(events)]
    spare = (y_last - y_first) - need * (n - 1)
    if spare <= 0 or sum(gaps) <= 0:
        step = (y_last - y_first) / (n - 1)
        return [y_first + step * k for k in range(n)]
    ys, y = [y_first], y_first
    for g in gaps:
        y += need + spare * g / sum(gaps)
        ys.append(y)
    return ys


# Timeline v2 (2026-11-02, operator-approved brief): honest spacing with drawn breaks, every block starting at its
# own marker, a tracking reveal, a quieter ground; 16:9 across the frame (the master lifts its lens vignette while
# a graphic is on screen, video_composer.graphic_vignette_enable, so 16:9 words no longer keep to WIDE_SAFE).
# The 16:9 layout grid (round 4, 2026-11-02): 12 columns between title-safe's x 153 and x 1766; every 16:9
# graphic's first text column or drawing starts on WIDE_LEFT, interior columns on wide_col(); its type keeps the
# floors the 16:9 tests enforce (primary, secondary, tertiary/meta at 1080 tall).
WIDE_LEFT, WIDE_RIGHT, WIDE_COLUMNS = 153.0, 1766.0, 12
WIDE_PRIMARY_MIN, WIDE_SECONDARY_MIN, WIDE_TERTIARY_MIN = 48, 28, 22


def wide_col(i: float) -> float:
    """The design x of 16:9 column line `i` (0 = WIDE_LEFT, 12 = WIDE_RIGHT)."""
    return WIDE_LEFT + (WIDE_RIGHT - WIDE_LEFT) * i / WIDE_COLUMNS


WIDE_TIMELINE = (WIDE_LEFT, 1760)  # the 16:9 timeline's run, design px (from the grid's left edge, clear of the corner)
COMPRESSED_FLOOR = 1.2            # a broken gap is drawn at least this much longer than the longest true-scale gap
DIM_LEVEL = 0.55                  # an earlier event's brightness while a later one is current (RevealSchedule.level)
TIMELINE_SPAN_RATIO = 0.76        # a 9:16 span label on its segment: this share of the labels' size (34 -> 26)
                                  # (2026-11-03 hierarchy spec: 0.8 before; the 16:9 spans keep WIDE_SPAN_PX)
SPAN_AIR = 0.6                    # round 4: a 9:16 span band's air above and below its label, in label sizes
SPAN_MIN_PX = 24                  # 2026-11-03 hierarchy spec: the 9:16 span label's floor (30 in round 4); the
                                  # tiers are now told apart by size, weight and colour, so 26 px spans step to 24
SPAN_AIR_BELOW_MIN = 12           # 2026-11-03: a set 9:16 span's least air under it to the later year (the fallback
                                  # path keeps SPAN_FALLBACK_AIR); its air above stays >= 1.5 x this. Round 9 (review
                                  # 8): 10 before; 12 px of ink over the next year (Hagia Sophia, the Colosseum) was
                                  # tighter than a year over its own label (14) and read as that date's; now 14
TALL_LABEL_MIN_PX = 30            # round 6 (2026-11-03): 9:16 event labels never step below this (34 before)
TALL_LABEL_WEIGHT = 600           # round 6: the 9:16 labels' Montserrat weight (700 before, 800 before that)
TALL_LABEL_FILL = (189, 182, 170)  # round 6: #BDB6AA, so the gold years are the brightest ink (#E8E3DA before),
                                   # a step under the gold years; brand.py is outside the spec's scope
TALL_TOP_PAD = 12                 # px from the Reel box's top (safe_zones.graphics_box) to the first year's cap
                                  # (round 4 review 2: 30 px under TEXT_TOP's legacy 330 before, 72 px in all)
TALL_YEAR_STEPS = tuple(round(1.0 - 0.04 * i, 2) for i in range(11))   # 2026-11-03: a 4 % ladder, 1.00 .. 0.60
CIRCA = "c."                      # round 4: circa drawn lowercase, smaller, in Montserrat beside the Anton year
SPAN_UNDER_LABEL = 2             # round 4 review 3: a 9:16 span is always at least this many px under the labels
TALL_LABEL_ROWS = 3               # round 4 review 3: a 9:16 label's rows before it is cut (never cut where it fits)
TALL_LABEL_ROWS_MAX = 6           # round 4 review 4: and more, up to this, while the column has room for them
TALL_LEADING = 1.12               # round 6: the 9:16 labels' leading, absolute (30 px -> a 34 px pitch; 1.15 before)
TALL_TIGHT_LEADING = 1.08         # and the tight one (37 px), taken before any year step (0.88 x 1.3 before)
TALL_YEAR_LABEL_AIR = 14          # 2026-11-03: px from a year's baseline to its label's cap top (3-4 entries)
TALL_NO_SPAN_AIR = 32             # round 6: px from a label's ink bottom to the next year's cap (42 before)
TALL_ENTRY_SEPARATION = 2.5       # 2026-11-03: the gap between entries, at least this x an entry's largest inner air
TALL_GAP_CAP = 1.5                # 2026-11-03: a gap between entries grows to at most this x the year cap (or its need)
SPAN_FALLBACK_AIR = 6             # round 4 review 3: a fallback span's least air to any other plate, px
CIRCA_MIN_PX = 30                 # the circa mark's floor (its own since 2026-11-03; it was SPAN_MIN_PX, 30)
CIRCA_RATIO = 0.5                 # 2026-11-03: the circa mark's size, of the year's (0.36 before: it read as a speck)
TALL_YEAR_RATIO = 4.5             # round 6: a 9:16 year's cap at least this many times the labels' cap (3.0 before)
                                  # while the column holds every label whole (2.0 in round 4)
TALL_YEAR_RATIO_MIN = 4.0         # and, only where the column cannot hold every label whole at that, this (logged)
TALL_YEAR_RATIO_LAST = 3.0        # round 7 (R3): and, only where nothing holds at that with the spans in their rows, this
TALL_INK_RATIO = 1.2              # round 6: a year block's ink area at least this x its label block's (logged under)
TALL_LABEL_TRACK = 0.05           # round 6: em, the 9:16 labels' tracking (+2 px, about 0.06-0.12 em, before)
TALL_SPAN_WEIGHT = 500            # round 7 (R3): the 9:16 span in its own row between the entries, Montserrat 500 in
                                  # STONE (a tier under the labels' 600 in TALL_LABEL_FILL)
TALL_INK_RESERVE = 6              # px of the measure a label row's ink may overshoot its advance by
TALL_STOP_WORDS = frozenset({"A", "AN", "THE", "OF", "ON", "IN", "AT", "TO", "INTO", "AND", "OR", "FOR", "BY",
                             "WITH", "FROM"})   # 2026-11-03: no 9:16 label row but the last ends on one of these


def tall_label_rows_budget(count: int) -> int:
    """The rows a 9:16 timeline label may take before it is over budget (2026-11-03): 2 at 4 entries, 3 at 3, and
    TALL_LABEL_ROWS at 2."""
    return 2 if count >= 4 else 3 if count == 3 else TALL_LABEL_ROWS


TALL_NUMBER_WORDS = frozenset({"TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN",
                               "TWELVE", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY",
                               "NINETY", "HUNDRED", "HUNDREDS", "THOUSAND", "THOUSANDS", "MILLION", "MILLIONS",
                               "DOZEN", "DOZENS"})  # 2026-11-03 review 15: no row ends on a count before its noun
NAME_PARTICLES = frozenset({"de", "du", "des", "del", "della", "di", "da", "la", "le", "van", "von", "der", "den",
                            "y", "al", "el", "bin", "ibn"})   # round 9: lower-case links inside a name ("Champ de Mars")


def _soft_count_breaks(words: list[str]) -> set[int]:
    """Round 10 (review 6): the break points after a spelled count of two words or more and before its noun
    ("TWENTY THOUSAND / WORKERS"): held like any count's (_held_breaks), but a lighter fault than a break inside
    the count ("TWENTY / THOUSAND WORKERS"), which is the only other place such a count can give way."""
    def count(w: str) -> bool:
        return w.upper().strip(",;:.") in TALL_NUMBER_WORDS
    return {j for j in range(2, len(words)) if count(words[j - 1]) and count(words[j - 2]) and not count(words[j])
            and not words[j - 1].endswith((",", ";", ":"))}


WIDE_HARD_LEVEL = 4               # round 13: weak_rank's level for a hard fault (label_hard_fault)
NAME_EPITHET_LINKS = frozenset({"the", "and", "&", "of"})   # round 13: "William the Conqueror", "Alfred the
                                                       # Great": inside the name; round 15 (R11): "Ferdinand and
                                                       # Isabella" held; round 16 (review 6): "Tower of Jesus Christ"


def label_hard_fault(lines: Sequence[str], source: str | None = None) -> str:
    """Round 13 (coordinator directive 4): why a label's break is never set ("" where it may be): a middle row of
    one word ("LORD CURZON / RESTORES / THE GARDENS", "NAMED A / WORLD / HERITAGE SITE"), or a break inside a
    proper name of the writer's `source` ("WILLIAM THE / CONQUEROR": _held_breaks, a spelled count's soft breaks
    aside)."""
    if len(lines) >= 3 and any(len(ln.split()) == 1 for ln in lines[1:-1]):
        return "a one-word middle row"
    if len(lines) < 2 or not source:
        return ""
    words = " ".join(lines).split()
    cuts, at = set(), 0
    for ln in lines[:-1]:
        at += len(ln.split())
        cuts.add(at)
    hit = cuts & _held_breaks(words, source)
    hit -= _soft_count_breaks(words)
    hit = {j for j in hit if not words[j - 1].replace(",", "").replace(".", "").isdigit()
           and words[j - 1].upper().strip(",;:.") not in TALL_NUMBER_WORDS}
    return "a proper name split" if hit else ""


def _held_breaks(words: list[str], source: str | None) -> set[int]:
    """The break points j (a row ending after words[j - 1]) that split a phrase read as one (2026-11-03 review 15):
    inside a run of capitalised words in the writer's `source` ("RAPA / NUI", "RANO / RARAKU"; a sentence's
    first word does not start a run), and after a spelled count ("A HUNDRED / DAYS"). Empty where `source`
    does not give the same words, or is set in capitals throughout (no case to read)."""
    from .typography import _plain, _words, glue
    held = {j for j in range(1, len(words)) if words[j - 1].upper().strip(",;:.") in TALL_NUMBER_WORDS}
    # round 9 (review 8): a figure before its noun too ("WITH 100 / DAYS OF GAMES"); never one that ends a clause
    held |= {j for j in range(1, len(words))
             if words[j - 1].replace(",", "").replace(".", "").isdigit() and not words[j - 1].endswith((",", ";", ":"))}
    if not source or not any(ch.islower() for ch in source):
        return held
    src = [_plain(w) for w in _words(glue(" ".join(source.split())))]
    if len(src) != len(words) or any(a.upper() != b.upper() for a, b in zip(src, words)):
        return held

    def cap(k: int) -> bool:   # a capitalised word that is not a sentence's first
        w = src[k].lstrip("\"'(\u201c\u2018")
        first = k == 0 or src[k - 1].rstrip("\"')\u201d\u2019").endswith((".", "!", "?", ":"))
        return bool(w) and w[0].isupper() and not first
    for j in range(1, len(words)):
        if src[j - 1].endswith((",", ";", ":", ".", "!", "?")):
            continue
        prev = src[j - 1].lstrip("\"'(\u201c\u2018")
        # round 16 (review 6): a sentence's first article is not part of the name after it ("The / Tower of Jesus
        # Christ" may break; the name itself may not)
        lead_article = prev.lower() in {"the", "a", "an"} and not cap(j - 1)
        if cap(j) and prev[:1].isupper() and not lead_article:   # "Jacob Roggeveen", "Rapa Nui National Park"
            held.add(j)
    # round 9 (review 8): a lower-case particle between two capitalised words is inside the name ("Champ de Mars":
    # "ON THE CHAMP / DE MARS" split it), so neither break by it is taken
    for j in range(1, len(words) - 1):
        if (src[j] in NAME_PARTICLES or src[j] in NAME_EPITHET_LINKS) and src[j - 1][:1].isupper() \
                and src[j + 1][:1].isupper() and not src[j - 1].endswith((",", ";", ":", ".", "!", "?")) \
                and cap(j - 1) | (j - 1 == 0):
            held |= {j, j + 1}
    return held


def balanced_label_rows(lines: list[str], font, width: float, track: float,
                        source: str | None = None) -> tuple[list[str], bool]:
    """`lines` (a whole label wrapped into its fewest rows) re-broken over the same number of rows (2026-11-03,
    hierarchy spec B5): the widest tracked row as narrow as it can be, every row within `width`, and no row but the
    last ending on a TALL_STOP_WORDS word. Returns (rows, obeyed): where no break set keeps the word rule the
    plain balanced break is returned with obeyed False (the caller logs it). The joined text never changes; a
    figure keeps to its unit (typography.glue). Review 15 (2026-11-03): with the writer's `source` (its case), a
    break set that also keeps every name and spelled count whole (_held_breaks) is taken first, the widest row
    least among those; only where none holds within `width` is a name split (the word rule still kept)."""
    from itertools import pairwise

    from .typography import _plain, _words, glue
    rows = len(lines)
    if rows < 2:
        return list(lines), True
    words = [_plain(w) for w in _words(glue(" ".join(lines)))]
    n = len(words)
    if n < rows:
        return list(lines), True
    widths: dict[tuple[int, int], float] = {}

    def w_of(i: int, j: int) -> float:
        if (i, j) not in widths:
            widths[(i, j)] = _tracked_w(" ".join(words[i:j]), font, track)
        return widths[(i, j)]

    def stop(j: int) -> bool:          # a row ending after words[j - 1] ends on a stop word
        return words[j - 1].upper().strip(",;:.") in TALL_STOP_WORDS
    held = _held_breaks(words, source)

    def best(rule: bool, keep: bool = False):
        @cache
        def rest(i: int, k: int) -> tuple[float, float, tuple[int, ...]] | None:
            if k == 1:
                w = w_of(i, n)
                return (w, w * w, ()) if w <= width + 1e-6 else None
            out = None
            for j in range(i + 1, n - k + 2):
                w = w_of(i, j)
                if w > width + 1e-6:
                    break
                if (rule and stop(j)) or (keep and j in held):
                    continue
                sub = rest(j, k - 1)
                if sub is None:
                    continue
                cand = (max(w, sub[0]), w * w + sub[1], (j, *sub[2]))
                if out is None or cand[:2] < out[:2]:
                    out = cand
            return out
        got = rest(0, rows)
        return None if got is None else [" ".join(words[a:b]) for a, b in pairwise((0, *got[2], n))]
    ruled = (best(True, True) if held else None) or best(True)
    if ruled is not None:
        return ruled, True
    return best(False) or list(lines), False


def span_band(label_px: int, gap_font) -> tuple[int, int]:
    """A 9:16 span label's own band between two entries (round 3 review, 2026-11-02): (the band's height, the air
    above and below the label), so the span reads as the time between the two entries and not as a third line of
    the earlier label (14 px of air before). Round 4 (2026-11-02): the air is SPAN_AIR (0.6) of the label size,
    not a whole label line (1.3), so the band fits the clear air a four-entry column already has and the span
    is always set on its segment, at its break mark."""
    air = round(label_px * SPAN_AIR)
    return cap_height(gap_font) + 2 * air, air


def span_airs(label_px: int, gap_font) -> tuple[int, int]:
    """A 9:16 span's air (above, below) inside its band (span_band): the same band, the span leaning toward the
    entry it leads into by a fifth of its own cap height. Round 4 review (2026-11-02): centred, "40 YEARS LATER"
    had the same air above as the earlier label's own line gap and read as that label's third line; set closer to
    the next year it reads as that date's lead-in ("40 years later: 1963"), never as the earlier event's words."""
    _band, air = span_band(label_px, gap_font)
    lean = round(cap_height(gap_font) / 5)
    return air + lean, air - lean


SPAN_SEPARATION = 2.0             # round 4 review 2: a 9:16 span's air above, at least this many times the entries'
                                  # own inner air (year to label, label line to line), so it never reads as a line


def entry_inner_air(cap_y: int, label_font, gap: int, step: int, rows: int) -> int:
    """A 9:16 timeline entry's own tightest air, in px: its year's baseline to its label's cap top (`gap` from
    the year's baseline to the label's first baseline) and, for a two-line label, line to line (`step`)."""
    cap_l = cap_height(label_font)
    return min([gap - cap_l] + ([step - cap_l] if rows > 1 else []))


def span_clear_airs(label_px: int, gap_font, inner_air: int) -> tuple[int, int]:
    """A 9:16 span's air (above, below) once its separation is enforced (round 4 review 2, 2026-11-02): the lean
    of span_airs, and above it at least SPAN_SEPARATION times the entries' own inner air (entry_inner_air).
    With 23 px above against the 1923 label's 18 px line gap, "40 YEARS LATER" still read as that label's third
    line; proximity groups only once the gap between groups is about twice the gap inside one."""
    above, below = span_airs(label_px, gap_font)
    return max(above, math.ceil(SPAN_SEPARATION * inner_air)), below


def span_basis(entries: list[dict[str, Any]]) -> int:
    """The air a 9:16 span's separation is measured against (round 4 review 6, 2026-11-03): the entries' own
    tightest air (entry_inner_air) or their labels' line gap (leading less cap height, `line`), whichever is more,
    so a span under a one-line label still keeps 1.5 x the gap that label's lines would have (the year-to-label air
    of a four-entry column, 18 px, is tighter than its 20 px line gap)."""
    return max(min(en["inner"] for en in entries), max(en["line"] for en in entries))


def span_basis_at(entries: list[dict[str, Any]], k: int) -> int:
    """Span k's own basis (round 4 review 7, 2026-11-03): the entries' tightest inner air or the line gap of the
    label just above it (entry k - 1), whichever is more: the label it must not read as the next line of."""
    return max(min(en["inner"] for en in entries), entries[k - 1]["line"])


SPAN_MIN_SEPARATION = 1.5         # round 4 review 5: and never less than this many times it, even at the bare lean
SPAN_OVERSHOOT = 2                # px: round letters and figures overshoot the cap and the baseline (ink measures)


def span_least_above(inner_air: float) -> int:
    """A 9:16 span's least air above, in layout px: SPAN_MIN_SEPARATION x the entries' inner air as the ink
    measures it (both edges' overshoot, SPAN_OVERSHOOT, paid)."""
    return math.ceil(SPAN_MIN_SEPARATION * inner_air) + SPAN_OVERSHOOT


def span_air_rule(label_px: int, gap_font, inner_air: int, separate: bool) -> tuple[int, int]:
    """The (above, below) air a 9:16 span keeps in its band: span_clear_airs when separated, else the bare lean of
    span_airs with its air above still at least SPAN_MIN_SEPARATION x the entries' inner air (round 4 review 5,
    2026-11-02: at the bare lean a span sat 22 px under a two-line label whose own line gap is 18 px)."""
    if separate:
        return span_clear_airs(label_px, gap_font, inner_air)
    # 2026-11-03 hierarchy spec, stage 3: down to the minimums, SPAN_MIN_SEPARATION x the inner air above and
    # SPAN_AIR_BELOW_MIN below as the ink measures it (both edges' overshoot paid; 16 px at the lean), the air
    # above still at least 1.5 x the air below
    _above, below = span_airs(label_px, gap_font)
    below = min(below, SPAN_AIR_BELOW_MIN + SPAN_OVERSHOOT)
    return max(span_least_above(inner_air), math.ceil(1.5 * below)), below


def span_fit(text: str, start_px: int, fits) -> tuple[str, int] | None:
    """The first (text, px) at which a 9:16 span fits its band (`fits(text, px)`): from `start_px` down to
    SPAN_MIN_PX in 2 px steps with "LATER", then the same without it; None when nothing fits (operator, round 4
    review 2: shrink to fit, keep the words, never drop the span)."""
    sizes = list(range(start_px, SPAN_MIN_PX - 1, -2)) or [SPAN_MIN_PX]
    if sizes[-1] != SPAN_MIN_PX:
        sizes.append(SPAN_MIN_PX)
    for tx in dict.fromkeys((text, text.removesuffix(" LATER"))):
        for px in sizes:
            if fits(tx, px):
                return tx, px
    return None


def span_fallback_y(top: float, bottom: float, cap: int, inner_air: float = 0.0) -> float:
    """A 9:16 span's baseline when no size gives it its full air, in the clear band between the earlier entry's
    label bottom (`top`) and the later entry's year top (`bottom`). Round 4 review 3 (2026-11-02): centred between
    the two dots instead, at the label column, it sat on the earlier entry's label. Round 4 review 4: centred in the
    band it sat 24 px under a two-line label whose own line gap is 18 px and read as its third line; it now leans
    to the later year as a set span does (span_slack_below), its air above at least SPAN_SEPARATION x the
    entries' inner air (`inner_air`) where the band holds that with SPAN_FALLBACK_AIR under it, else
    SPAN_MIN_SEPARATION x it (span_least_above). Round 4 review 6 (2026-11-03): a band too short even for that
    no longer takes "the most it gives" (23 px under an 18 px line gap): the baseline keeps the least air above
    and the caller moves the span off the year it then reaches (free_span_xy), never closer to the label."""
    slack = bottom - top - cap
    want_up = math.ceil(SPAN_SEPARATION * inner_air)
    least_up = span_least_above(inner_air)
    if slack - least_up < SPAN_FALLBACK_AIR:
        return top + least_up + cap
    if slack <= 2 * SPAN_FALLBACK_AIR:
        return (top + bottom) / 2 + cap / 2
    if slack - want_up < SPAN_FALLBACK_AIR:          # review 5: 1.5 x where the band holds that, not 2 x
        return bottom - (slack - least_up)
    below = max(SPAN_FALLBACK_AIR, min(span_slack_below(slack), slack - want_up))
    return bottom - below


def span_descent(text: str, font) -> int:
    """A span's ink under its baseline in px (a comma's tail, "2,956 YEARS LATER"), 0 for none."""
    return max(0, _TRACK_PROBE.textbbox((0, 0), text, font=font, anchor="ls")[3]) if text else 0


def span_slack_below(slack: float) -> float:
    """Of the free height in a 9:16 span's band, the share set under it (round 4 review 4, 2026-11-02): a third,
    the other two thirds above. All of it above put "2 YEARS LATER" 15 px over a 150 px year and 120 px under the
    earlier label (cramped against the year); a third keeps the lean to the later date without crowding it."""
    return slack / 3


def free_span_xy(box: tuple[float, float, float, float], xy: tuple[float, float], ys: list[float],
                 xs: list[float], obstacles: list[tuple[float, float, float, float]], right: float,
                 air: float = 0.0, years: list[tuple[float, float, float, float]] = ()) -> tuple[float, float] | None:
    """The first (x, baseline) for a span whose ink box is `box` when set at `xy`, from the baselines `ys` (in
    order of preference) at the x positions `xs`, then just right of whatever it would sit on,
    at which the box keeps `air` px from every obstacle and ends left of `right` (every baseline at `xs` is tried
    before any place beside an obstacle); None where nothing does. Round 4 review 4 (2026-11-02): never beside a
    year (`years`, their ink boxes): a span on a year's line, right of it, reads as that event's duration
    ("C. AD 780  ABOUT 1,500 YEARS", the reading round 4 removed), so no baseline level with one is tried there."""
    w = box[2] - box[0]
    for beside in (False, True):          # the column's own x on any baseline first, then beside what is there
        for y in ys:
            dy = y - xy[1]
            top, bot = box[1] + dy - air, box[3] + dy + air
            level = [o for o in obstacles if o[1] < bot and top < o[3]]
            if beside and any(o[1] < bot and top < o[3] for o in years):
                continue
            for x in sorted(o[2] + air + (xy[0] - box[0]) for o in level) if beside else xs:
                dx = x - xy[0]
                moved = (box[0] + dx - air, top, box[2] + dx + air, bot)
                if box[0] + dx + w <= right and not any(LayoutConstraintSolver.boxes_intersect(moved, o)
                                                        for o in level):
                    return x, y
    return None


def span_font_px(label_px: int) -> int:
    """The 9:16 on-segment span label's size: below the event labels' (TIMELINE_SPAN_RATIO), so the secondary
    elapsed time never reads larger or heavier than the events it sits between; at least 8 px under them
    (2026-11-03 hierarchy spec: 34 -> 26, 38 -> 29, 42 -> 32) and SPAN_MIN_PX (24) at least."""
    return max(SPAN_MIN_PX, min(label_px - 8, round(label_px * TIMELINE_SPAN_RATIO)))


@dataclass(frozen=True)
class TimelineSpacing:
    """Where each event sits on the spine and which gaps are drawn shorter than true scale (`breaks`: k for the
    gap between events k-1 and k, the same keys as the gap labels), with the px per year the honest gaps use."""
    positions: list[float]
    breaks: tuple[int, ...]
    px_per_year: float
    honest: bool = True     # round 4: every compressed gap is at least COMPRESSED_FLOOR x the longest true one


def timeline_positions(events: list[dict[str, Any]], first: float, last: float,
                       need: float | list[float]) -> TimelineSpacing:
    """Each event's position from `first` to `last`, proportional to the years elapsed (2026-11-02, timeline v2:
    the log spacing before drew 40 years nearly as long as 1,500). `need`: the least step, one value or one per
    gap. When true scale cannot give every gap its least step, the fewest longest gaps are compressed and listed in
    `breaks` (drawn with a break glyph): the others stay on one true scale, the shortest of them at its least step,
    and each compressed gap is still drawn longer than any true-scale one, sharing what is left by the logarithm of
    its years, so the order of lengths never lies even where the scale is broken (no shorter than the longest
    true-scale gap where the column is too tight for COMPRESSED_FLOOR)."""
    from itertools import pairwise
    n = len(events) - 1
    if n < 1:
        return TimelineSpacing([first], (), 0.0)
    years = [max(1, years_between(int(a["year"]), int(b["year"]))) for a, b in pairwise(events)]
    needs = list(need) if isinstance(need, (list, tuple)) else [float(need)] * n
    span = last - first

    def placed(steps: list[float]) -> list[float]:
        out = [first]
        for s in steps:
            out.append(out[-1] + s)
        return out
    scale = span / sum(years)
    if all(y * scale >= nd - 1e-6 for y, nd in zip(years, needs)):
        return TimelineSpacing(placed([y * scale for y in years]), (), scale)
    order = sorted(range(n), key=lambda i: -years[i])
    for lead in (COMPRESSED_FLOOR, 1.0):          # a tight column (9:16) may only keep them no shorter
        for m in range(1, n):
            broken = set(order[:m])
            honest = [i for i in range(n) if i not in broken]
            s = max(needs[i] / years[i] for i in honest)           # the least scale that gives each its step
            lengths = {i: years[i] * s for i in honest}
            floor_b = {i: max(needs[i], lead * max(lengths.values())) for i in broken}
            rest = span - sum(lengths.values()) - sum(floor_b.values())
            if rest < -1e-6:
                continue
            weights = {i: math.log10(1 + years[i]) for i in broken}
            for i in broken:
                lengths[i] = floor_b[i] + rest * weights[i] / sum(weights.values())
            steps = [lengths[i] for i in range(n)]
            breaks = tuple(sorted(i + 1 for i in broken if steps[i] < years[i] * s * 0.95))
            return TimelineSpacing(placed(steps), breaks, s, lead >= COMPRESSED_FLOOR)
    if sum(needs) <= span + 1e-6:
        # Round 4 review 6 (2026-11-03): no true scale holds every least step, but the steps themselves fit: each
        # gap its least step and the rest shared by the logarithm of its years (even steps took a 16:9 span's
        # room and cut its labels); every gap drawn shorter than the longest-scaled one is marked compressed.
        weights = [math.log10(1 + y) for y in years]
        spare = span - sum(needs)
        steps = [nd + spare * w / sum(weights) for nd, w in zip(needs, weights)]
        s = max(st / y for st, y in zip(steps, years))
        breaks = tuple(k + 1 for k in range(n) if steps[k] < years[k] * s * 0.95)
        return TimelineSpacing(placed(steps), breaks, s, False)
    # Not even one true-scale gap fits: even steps, every gap longer than the shortest marked as compressed.
    step = span / n
    low = min(years)
    return TimelineSpacing([first + step * k for k in range(n + 1)],
                           tuple(k + 1 for k in range(n) if years[k] > low * 1.5), step / low, False)


@dataclass(frozen=True)
class RevealSchedule:
    """The tracking reveal: event k lands at `at[k]`, the current event full, earlier ones at DIM_LEVEL, and from
    `restore` every event comes back to full for the held last frame."""
    at: list[float]
    restore: float

    def appear(self, k: int, t: float) -> float:
        return _ease((t - self.at[k]) / 0.45)

    def level(self, k: int, t: float) -> float:
        """Event k's brightness (0-1): its landing times how far it has dimmed since the next one landed."""
        a = self.appear(k, t)
        if k + 1 < len(self.at):
            a *= 1 - (1 - DIM_LEVEL) * _ease((t - self.at[k + 1]) / 0.4)
        back = _ease((t - self.restore) / 0.5)
        return a + (min(1.0, self.appear(k, t)) - a) * back

    def focus(self, k: int, t: float) -> float:
        """How far event k has stepped back (0-1) since the next one landed, back to 0 from `restore` (round 3,
        2026-11-02: an earlier event is recoloured to a stone twin at full opacity, not faded, so it keeps its
        measured contrast)."""
        if k + 1 >= len(self.at):
            return 0.0
        return _ease((t - self.at[k + 1]) / 0.4) * (1 - _ease((t - self.restore) / 0.5))

    def pulse(self, k: int, t: float) -> float:
        """Event k's node size as it lands: 1.0 to 1.3 and back over 0.5 s, once."""
        u = (t - self.at[k] - 0.3) / 0.5
        return 1.0 + 0.3 * math.sin(math.pi * u) if 0.0 < u < 1.0 else 1.0

    def head(self, positions: list[float], t: float, end: float) -> float:
        """Where the spine's drawn end is: it travels to each event as that event lands, then on to `end`."""
        if t <= self.at[0] - 0.25:
            return positions[0]
        for k in range(1, len(positions)):
            if t <= self.at[k]:
                u = (t - (self.at[k - 1] + 0.05)) / max(1e-6, self.at[k] - self.at[k - 1] - 0.05)
                return positions[k - 1] + (positions[k] - positions[k - 1]) * _ease_io(max(0.0, u))
        return positions[-1] + (end - positions[-1]) * _ease_io((t - self.at[-1]) / 0.7)


REVEAL_FIRST = 0.025   # round 4: the first landing at 2.5 % of the clip, inside the composer's 0.4 s cross-fade
BACKDROP_DRIFT = 0.015  # round 4: the timeline's ground scales 1.0 -> 1.015 over the clip, so no frame is frozen


def reveal_schedule(count: int, clip: float) -> RevealSchedule:
    """Landings spread over 2.5-66 % of the clip (0.15-4.0 s of 6 s; round 4: from 8 %, an empty plate for half a
    second), the restore at 76 % so the last frame holds."""
    if count < 2:
        return RevealSchedule([REVEAL_FIRST * clip], 0.5 * clip)
    step = (0.66 - REVEAL_FIRST) * clip / (count - 1)
    return RevealSchedule([REVEAL_FIRST * clip + step * k for k in range(count)], 0.76 * clip)


def drift_scale(t: float) -> float:
    """The timeline ground's scale at `t`: 1.0 -> 1 + BACKDROP_DRIFT, linear over the clip (no frame is frozen)."""
    return 1.0 + BACKDROP_DRIFT * max(0.0, min(1.0, t / CLIP_SECONDS))


def drifted(ground: Image.Image, t: float) -> Image.Image:
    """The timeline's ground at time `t`: a slow scale drift about its centre (BACKDROP_DRIFT over the clip)."""
    return _pushed_in(ground, drift_scale(t))


class GroundDrift:
    """drifted() for one render. QF-22: every frame is sampled at its exact scale through the shared camera (the
    half-pixel steps of round 4 review 7 are gone); only a repeated scale reuses the last image, and a flat ground
    (no photo) is not resampled at all. Each call returns a copy the caller may draw on."""

    def __init__(self, ground: Image.Image) -> None:
        ex = ground.getextrema()
        ex = ex if isinstance(ex[0], tuple) else (ex,)
        self.ground, self.flat = ground, all(lo == hi for lo, hi in ex)
        self.q: float | None = None
        self.img = ground

    def step(self, t: float) -> float:
        return drift_scale(t)

    def __call__(self, t: float) -> Image.Image:
        q = 1.0 if self.flat else self.step(t)
        if q != self.q:
            self.q = q
            self.img = _pushed_in(self.ground, q) if q != 1.0 else self.ground
        return self.img.copy()


def spine_marks(size: tuple[int, int], along_x: bool, fixed: float, a0: float, a1: float, lw: float,
                nodes: list[tuple[float, float, bool]], stroke: float, breaks: list[float], glyph: float,
                glyph_w: float) -> list[tuple[str, Image.Image]]:
    """The timeline's essential marks as masks on the last frame (round 4): the spine (`fixed` the cross
    coordinate, from a0 to a1, cut at each break), each node (position, radius, hollow) and each break glyph."""
    def pt(a: float, c: float = 0.0) -> tuple[float, float]:
        return (a, fixed + c) if along_x else (fixed + c, a)
    gap = glyph * 0.42

    def spine(d) -> None:
        cuts = sorted(breaks)
        lo = a0
        for b in cuts + [a1]:
            hi = b - gap if b != a1 else a1
            if hi > lo:
                d.line([pt(lo), pt(hi)], fill=255, width=max(2, round(lw)))
            lo = b + gap
    out = [("spine", mark_mask(size, spine))]
    for k, (a, r, hollow) in enumerate(nodes):
        def node(d, a=a, r=r, hollow=hollow) -> None:
            x, y = pt(a)
            box = [x - r, y - r, x + r, y + r]
            if hollow:
                d.ellipse(box, outline=255, width=max(2, round(min(stroke, r))))
            else:
                d.ellipse(box, fill=255)
        out.append((f"node{k}", mark_mask(size, node)))
    for k, b in enumerate(breaks):
        def brk(d, b=b) -> None:
            w = max(1, round(glyph_w))
            for o in (-gap * 0.55, gap * 0.55):
                if along_x:
                    x, y = pt(b + o)
                    d.line([(x - glyph * 0.28, y + glyph * 0.5), (x + glyph * 0.28, y - glyph * 0.5)], fill=255, width=w)
                else:
                    x, y = pt(b + o)
                    d.line([(x - glyph * 0.5, y + glyph * 0.28), (x + glyph * 0.5, y - glyph * 0.28)], fill=255, width=w)
        out.append((f"break{k}", mark_mask(size, brk)))
    return out


def spine_colour(ground: Image.Image, mask: Image.Image) -> tuple[int, int, int]:
    """The spine's token: GOLD_DIM where it measures NONTEXT_MIN on its ground, else stepped up to GOLD."""
    probe = ground.convert("RGB").copy()
    probe.paste(brand.GOLD_DIM, (0, 0), mask)
    ratio = nontext_contrast(probe, mask, ground=ground)
    return brand.GOLD_DIM if ratio is None or ratio >= NONTEXT_MIN else brand.GOLD


def timeline_backdrop(photo: Path | None, width: int, height: int) -> Image.Image:
    """The timeline's ground (v2): the topic's photo blurred, taken to 25 % of its colour and down to about a sixth
    of its brightness, so the gold line carries the frame (the blurred photo at 28 % read as muddy brown); the
    plain ink without a photo."""
    from PIL import ImageEnhance
    if not (photo and Path(photo).exists()):
        return Image.new("RGB", (width, height), brand.INK)
    from .photo_grade import cover
    im = cover(Path(photo), (width, height)).filter(ImageFilter.GaussianBlur(34))   # graded as a bare photo (QF-86)
    im = ImageEnhance.Color(im).enhance(0.25)
    return Image.blend(im, Image.new("RGB", (width, height), brand.INK), 0.84).convert("RGB")


def _break_glyph(d: ImageDraw.ImageDraw, x: float, y: float, along_x: bool, size: float, fill, width: float,
                 ground) -> None:
    """The axis-break mark on a spine at (x, y): the line cut and two parallel slashes across the cut."""
    w = max(1, round(width))
    gap = size * 0.42
    if along_x:
        d.rectangle([x - gap, y - width * 2, x + gap, y + width * 2], fill=ground)
        for dx in (-gap * 0.55, gap * 0.55):
            d.line([(x + dx - size * 0.28, y + size * 0.5), (x + dx + size * 0.28, y - size * 0.5)], fill=fill, width=w)
    else:
        d.rectangle([x - width * 2, y - gap, x + width * 2, y + gap], fill=ground)
        for dy in (-gap * 0.55, gap * 0.55):
            d.line([(x - size * 0.5, y + dy + size * 0.28), (x + size * 0.5, y + dy - size * 0.28)], fill=fill, width=w)


def _node(d: ImageDraw.ImageDraw, x: float, y: float, r: float, approximate: bool, alpha: float, stroke: float,
          ground) -> None:
    """A timeline event's marker (round 3, 2026-11-02: certainty on the node): an exact year a solid gold dot, a
    circa date a hollow gold ring `stroke` px wide, its inside the ground's colour so the spine does not run
    through it."""
    if r <= 0.5 or alpha <= 0:
        return
    box = [x - r, y - r, x + r, y + r]
    if approximate:
        d.ellipse(box, fill=(*ground, int(255 * alpha)), outline=(*GOLD, int(255 * alpha)),
                  width=max(2, round(min(stroke, r))))
    else:
        d.ellipse(box, fill=(*GOLD, int(255 * alpha)))


def _focused(im: Image.Image, bright: list[Any], dim: list[Any], a: float, back: float, dy: float = 0) -> Image.Image:
    """An event's words at landing `a`: the bright plates, cross-faded to their stone twins as it steps back."""
    for pl, tw in zip(bright, dim):
        if back < 0.999:
            im = draw_plate(im, pl, a * (1 - back), dy)
        if back > 0.001:
            im = draw_plate(im, tw, a * back, dy)
    return im


def _twin(ground: Image.Image, plate: Any, fill) -> Any:
    """A plate's stepped-back twin: the same words, font and place in `fill` (an earlier event while a later one is
    current), planned on the same ground so its contrast is measured too."""
    return plan_text(ground, plate.xy, plate.text, plate.font, fill, plate.anchor, track=plate.track,
                     features=plate.features)


def year_parts(year: int, approximate: bool) -> tuple[str, str]:
    """A timeline year in its two drawn parts (round 4, 2026-11-02): the circa mark, lowercase ("c.", CIRCA; set
    upper it read as an initial, "C. 700 BC") and the era in capitals ("700 BC", "AD 780", "1963"); the same
    words as year_text."""
    era = year_text(year, False).upper()
    return (CIRCA if approximate else ""), era


def circa_font(f_year, min_px: int | None = None) -> Any:
    """The circa mark's face beside an Anton year: Montserrat SemiBold at half the year's size (CIRCA_RATIO), never
    under CIRCA_MIN_PX (`min_px` for another frame scale). Round 4 review (2026-11-02): at 0.36 of a stepped-down
    year it fell to about 22 px on a 1080-wide frame, under the span label's floor, and "c. 700 BC" read as an
    exact date; the hedge is a fact, so it keeps a legibility floor whatever the year does. 2026-11-03 hierarchy
    spec: 0.36 read as a speck beside the numeral; lowercase still (capitals read as an initial), both frames."""
    floor = CIRCA_MIN_PX if min_px is None else min_px
    return get_font("Montserrat", max(floor, round(f_year.size * CIRCA_RATIO)), weight=600)


def year_width(year: int, approximate: bool, f_year) -> float:
    """The drawn width of a year with its circa mark (see _year_plates)."""
    pre, era = year_parts(year, approximate)
    w = tracked_width(era, f_year, 0)
    if pre:
        f_c = circa_font(f_year)
        w += tracked_width(pre, f_c, 0) + round(f_year.size * 0.08)
    return w


def _year_plates(ground: Image.Image, x: float, baseline: float, year: int, approximate: bool, f_year, fill) -> list:
    """A year as measured plates on one baseline from `x`: the lowercase circa mark, smaller, then the Anton era."""
    pre, era = year_parts(year, approximate)
    out = []
    if pre:
        f_c = circa_font(f_year)
        out.append(_plan(ground, (x, baseline), pre, f_c, fill, "ls"))
        x = out[-1].box[2] + round(f_year.size * 0.08)
    out.append(plan_text(ground, (x, baseline), era, f_year, fill, "ls"))
    return out


def year_ink_boxes(x: float, baseline: float, year: int, approximate: bool, f_year) -> list:
    """The ink boxes _year_plates' plates would have (ink_box), without planning them."""
    pre, era = year_parts(year, approximate)
    out = []
    if pre:
        out.append(ink_box((x, baseline), pre, circa_font(f_year), "ls"))
        if out[-1]:
            x = out[-1][2] + round(f_year.size * 0.08)
    out.append(ink_box((x, baseline), era, f_year, "ls"))
    return [b for b in out if b]


TIMELINE_MAX_EVENTS = 4   # the entries a timeline draws (script_engine asks for 2-4)


def timeline_events(events: list[dict[str, Any]], limit: int = TIMELINE_MAX_EVENTS) -> list[dict[str, Any]]:
    """The events a timeline draws, oldest first (round 4 review 4, 2026-11-02: `sorted(events)[:4]` kept the four
    earliest, so six events lost 1923 and 1963, the most recent and often the climax). Over `limit`, the first and
    the last are always kept and the middle ones are chosen by a numeric `significance` where any event has one
    (the highest, ties to the earlier), else evenly spaced through the run; the dropped years are logged."""
    events = sorted(events, key=lambda e: e["year"])
    if len(events) <= limit:
        return events
    n, inner = len(events), limit - 2
    middle = list(range(1, n - 1))
    if inner <= 0:
        keep = [0, n - 1][:max(limit, 0)]
    elif any(isinstance(events[i].get("significance"), (int, float)) for i in middle):
        rank = sorted(middle, key=lambda i: (-float(events[i].get("significance") or 0), i))
        keep = [0, *sorted(rank[:inner]), n - 1]
    else:
        keep = [0, *sorted({round(j * (n - 1) / (limit - 1)) for j in range(1, limit - 1)}), n - 1]
    dropped = [events[i] for i in range(n) if i not in keep]
    log.warning("Timeline: %d events, %d drawn; dropped %s.", n, len(keep),
                ", ".join(year_text(int(e["year"]), bool(e.get("approximate"))) for e in dropped))
    return [events[i] for i in keep]


def tall_label_measure(width: int, height: int) -> float:
    """The 9:16 timeline labels' measure in px: from the column's left ink edge (xt, every row's ink set on it by
    tall_left_x) to the graphics box's right, less the ink a row may overshoot its advance on either side
    (2026-11-03: the rows were drawn at xt + 4 and measured from there)."""
    x0 = inset_x0(width, height) + round(16 * width / VIDEO_WIDTH)
    xt = x0 + round(56 * width / VIDEO_WIDTH)
    return TEXT_RIGHT * width // VIDEO_WIDTH - xt - 2 * SPAN_OVERSHOOT


def tall_left_x(xt: float, baseline: float, text: str, font, track: float) -> float:
    """The x to draw `text` from ("ls") so that its left ink edge sits on `xt` (2026-11-03 hierarchy spec: the
    year group, every label row and every span share one left edge, each glyph's side bearing measured by
    ink_box, not a fixed xt + 4)."""
    bb = ink_box((xt, baseline), text, font, "ls", track)
    return xt if bb is None else xt + (xt - bb[0])


def capped_spacing(spacing: TimelineSpacing, events: list[dict[str, Any]], caps: list[float]) -> TimelineSpacing:
    """`spacing` with no step longer than its cap (2026-11-03 hierarchy spec, box fill): each step past its cap is
    set to it and its excess shared by the others in proportion to their lengths (so the true scale among them
    holds) until none is past its cap; what is left when every step is at its cap stays under the column, never
    stretched over it. A step drawn shorter than the scale the honest steps keep is marked compressed (`breaks`)."""
    from itertools import pairwise
    steps = [b - a for a, b in pairwise(spacing.positions)]
    if not steps or all(st <= c + 1e-6 for st, c in zip(steps, caps)):
        return spacing
    fixed: set[int] = set()
    while True:
        over = [k for k in range(len(steps)) if k not in fixed and steps[k] > caps[k] + 1e-6]
        if not over:
            break
        excess = sum(steps[k] - caps[k] for k in over)
        for k in over:
            steps[k] = caps[k]
            fixed.add(k)
        free = [k for k in range(len(steps)) if k not in fixed]
        total = sum(steps[k] for k in free)
        if not free or total <= 0:
            break                                  # every gap at its cap: the rest stays under the column
        for k in free:
            steps[k] += excess * steps[k] / total
    years = [max(1, years_between(int(a["year"]), int(b["year"]))) for a, b in pairwise(events)]
    honest = ([k for k in range(len(steps)) if k + 1 not in spacing.breaks and k not in fixed]
              or [k for k in range(len(steps)) if k + 1 not in spacing.breaks] or list(range(len(steps))))
    s = max(steps[k] / years[k] for k in honest)
    breaks = tuple(k + 1 for k in range(len(steps)) if steps[k] < years[k] * s * 0.95)
    positions = [spacing.positions[0]]
    for st in steps:
        positions.append(positions[-1] + st)
    return TimelineSpacing(positions, breaks, s, spacing.honest)


def tall_track(px: float) -> float:
    """The 9:16 labels' tracking in px at `px` (TALL_LABEL_TRACK em)."""
    return round(TALL_LABEL_TRACK * px, 2)


def tall_label_rows_needed(text: str, width: float) -> int | None:
    """The rows a 9:16 timeline label (`text`, as the column draws it) needs to be set whole at TALL_LABEL_MIN_PX,
    Montserrat TALL_LABEL_WEIGHT, +2 tracking on `width`: the fit the render makes (fit_tracked, sentences kept
    whole past one row), so timeline_copy_fit and the render judge a label alike (review 15). None over
    TALL_LABEL_ROWS_MAX rows, or where one word alone is wider than `width`."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for r in range(1, TALL_LABEL_ROWS_MAX + 1):
        _lines, _font, short = _probe_tracked(probe, text, "Montserrat", TALL_LABEL_MIN_PX, width, r, TALL_LABEL_MIN_PX,
                                         tall_track(TALL_LABEL_MIN_PX), sentences=r > 1, weight=TALL_LABEL_WEIGHT,
                                         quiet=True)
        if not short:
            return r
    return None


def timeline_copy_fit(events: list[dict[str, Any]], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> list[str]:
    """The copy-fit problems of a 9:16 timeline's events, for upstream callers (2026-11-03 hierarchy spec): one
    reason per label over its row budget (tall_label_rows_budget for the entries drawn) at TALL_LABEL_MIN_PX,
    Montserrat TALL_LABEL_WEIGHT, +2 tracking on the column's measure, and one if there are more than
    TIMELINE_MAX_EVENTS events. Empty when the copy fits. Pure: nothing is logged or drawn."""
    out: list[str] = []
    if len(events) > TIMELINE_MAX_EVENTS:
        out.append(f"{len(events)} events, at most {TIMELINE_MAX_EVENTS} are drawn")
    budget = tall_label_rows_budget(min(len(events), TIMELINE_MAX_EVENTS))
    width_px = tall_label_measure(width, height) - TALL_INK_RESERVE   # round 6: as the render fits
    font = get_font("Montserrat", TALL_LABEL_MIN_PX, weight=TALL_LABEL_WEIGHT)
    for e in events:
        text = drawable(" ".join(str(e.get("label") or "").upper().split()), "Montserrat")
        if not text:
            continue
        rows = tall_label_rows_needed(text, width_px)   # the render's own fit (review 15: not tracked_wrap alone)
        if rows is not None and rows <= budget:
            continue
        if tracked_wrap(text, font, width_px, tall_track(TALL_LABEL_MIN_PX)) is None:
            out.append(f"label {text!r}: a word is wider than the {width_px:.0f} px measure at "
                       f"{TALL_LABEL_MIN_PX} px")
        else:
            got = f"{rows}" if rows is not None else f"more than {TALL_LABEL_ROWS_MAX}"
            out.append(f"label {text!r} needs {got} rows at {TALL_LABEL_MIN_PX} px, budget {budget}")
    return out


def render_timeline(events: list[dict[str, Any]], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                    photo: Path | None = None) -> Path | None:
    """Timeline v2 (2026-11-02): a gold spine that draws on event by event, each date landing in turn at full
    brightness while the earlier ones dim (all full again for the held last frame); the events on a true time scale
    (timeline_positions), any gap too long to draw to scale cut with a break mark; the elapsed time (gap_text) on
    each gap; the topic's photo, blurred, desaturated and dark, as the ground (`photo`; plain ink without one).
    9:16: a vertical spine on the shared inset edge (INSET_X0) inside the Reel box; 16:9: across the frame."""
    events = timeline_events(events)
    if len(events) < 2:
        return None
    if width > height:
        return _render_timeline_wide(events, out, width, height, photo)
    return _render_timeline_tall(events, out, width, height, photo)


def _render_timeline_tall(events: list[dict[str, Any]], out: Path, width: int, height: int,
                          photo: Path | None) -> Path | None:
    """The 9:16 timeline (2026-11-03 hierarchy spec): the Anton years the hero, one size for the column, from 104
    px (4 entries), 120 (3) or 180 (2); the labels Montserrat 700 in TALL_LABEL_FILL at 34 px for 3-4 entries
    (42 down to 34 for two), TALL_YEAR_LABEL_AIR under their year and TALL_LEADING apart, each label within its
    row budget (tall_label_rows_budget) and re-broken balanced with no row but the last ending on a short word
    (balanced_label_rows); the spans 26 px (span_font_px) on their own segments. Every left ink edge (the year
    group, each label row, each span) sits on the same x, side bearings corrected (tall_left_x). The first year's
    cap is TALL_TOP_PAD under the graphics box's top; spare height goes to the years (up to their starting size),
    then to the gaps between entries on the honest time scale, each at most max(its need, TALL_GAP_CAP x the year
    cap) (capped_spacing), and what is left stays under the column. When the entries do not fit, the column gives
    way in this order: the compressed gaps' honest lead, the spans to SPAN_MIN_PX, the spans' air to its minimums
    (span_air_rule), the labels' leading (TALL_TIGHT_LEADING), the years down TALL_YEAR_STEPS to TALL_YEAR_RATIO x
    the labels' cap, then to TALL_YEAR_RATIO_MIN (logged); a span with no band loses " LATER" and takes the
    fallback place (never dropped); a label over its row budget is a copy-fit error (logged) set whole on up to
    TALL_LABEL_ROWS_MAX rows; and last the cut ladder, a label cut with an ellipsis (logged). The labels never
    step under TALL_LABEL_MIN_PX."""
    n = int(CLIP_SECONDS * VIDEO_FPS)
    _top, bottom = get_text_bounds(width, height)
    x0 = inset_x0(width, height) + round(16 * width / VIDEO_WIDTH)   # the spine: its dots' left edge on the inset
    xt = x0 + round(56 * width / VIDEO_WIDTH)
    label_w = tall_label_measure(width, height)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    many = len(events) > 2
    budget = tall_label_rows_budget(len(events))
    spans = [gap_text(events[k - 1], events[k]) for k in range(1, len(events))]
    scales = (1.0, 0.92, 0.84, 0.76, 0.68, 0.6)
    # round 7 (coordinator R3): the spans back between the entries, in their own row ("17 YEARS LATER"): beside a
    # year (round 6's "+17 YRS") a span read as part of the date and did not say "later"
    on_segment = any(spans)
    year_px0 = (128 if len(events) >= 4 else 150) if many else 190   # round 6: 104 / 120 / 180 before
    texts = [drawable(e["label"].upper(), "Montserrat") for e in events]
    source_of: dict[str, str] = {}     # review 15: each label's own case, for the balanced break's held names
    for e, tx in zip(events, texts):
        source_of.setdefault(tx, str(e["label"]))

    col_top = safe_zones.graphics_box(width, height)[1]   # round 4 review 2: the column's own top, the Reel box's
    fit_memo: dict[tuple[str, int, int, int], tuple[list[str], Any, bool, bool]] = {}

    def basis_at(entries_: list[dict[str, Any]], k: int) -> int:
        # span k's basis (span_basis_at), for 3-4 entries never under the year-to-label air (2026-11-03: the
        # span's least air above is 1.5 x 14 + 2 = 23 px also where the tight leading closes the line gap to 13)
        b = span_basis_at(entries_, k)
        return max(b, TALL_YEAR_LABEL_AIR) if many else b

    def label_px_at(scale: float) -> int:
        # 2026-11-03: 34 px fixed for 3-4 entries, never smaller; two entries keep 42 -> 34
        return TALL_LABEL_MIN_PX if many else max(TALL_LABEL_MIN_PX, int(42 * scale))

    def fitted(text: str, px: int, rows: int, lo: int):
        key = (text, px, rows, lo)
        if key not in fit_memo:   # the labels' fits do not depend on the years: measured once per render
            # round 6: TALL_INK_RESERVE px kept for a row's ink past its advance (a final S or quote at 0.05 em)
            lines, font, short = _probe_tracked(probe, text, "Montserrat", px, label_w - TALL_INK_RESERVE, rows, lo, tall_track(px),
                                             sentences=rows > 1, weight=TALL_LABEL_WEIGHT, quiet=True)
            obeyed = True
            if len(lines) > 1:   # hierarchy spec B5: the same rows (and words), balanced, no short word last
                lines, obeyed = balanced_label_rows(lines, font, label_w - TALL_INK_RESERVE, tall_track(font.size),
                                                    source=source_of.get(text))
            fit_memo[key] = (lines, font, short, obeyed)
        return fit_memo[key]

    def measure(year_px: int, scale: float, honest: bool = True, separate: bool = True,
                span_px: int | None = None, rows_max: int = TALL_LABEL_ROWS, leading: float = TALL_LEADING):
        f_year = get_font("Anton", year_px)
        cap_y = cap_height(f_year)
        lab_px = label_px_at(scale)
        entries = []
        floor = TALL_LABEL_MIN_PX if many else max(TALL_LABEL_MIN_PX, round(lab_px * 0.8))

        def wrapped(text: str, px: int, lo: int, first: int):
            got = fitted(text, px, first, lo)
            for rows in range(first, rows_max + 1):   # the fewest rows that hold it whole, up to rows_max
                got = fitted(text, px, rows, lo)
                if not got[2]:
                    break
            return got
        cut = False
        for e, text in zip(events, texts):
            lines, f_lab, short, obeyed = wrapped(text, lab_px, floor, 1 if many else 2)
            cut = cut or short
            f_lab = get_font("Montserrat", f_lab.size, weight=TALL_LABEL_WEIGHT)
            cap_l = cap_height(f_lab)
            step = round(f_lab.size * leading)
            # 2026-11-03: the first label's cap top TALL_YEAR_LABEL_AIR under its year's baseline (1.25 x the
            # label size before); two entries keep their 1.6 x, tightened with the leading
            gap = (TALL_YEAR_LABEL_AIR + cap_l if many
                   else round(f_lab.size * 1.6 * leading / TALL_LEADING))
            e_top = -cap_y // 2              # offsets from the entry's dot: the year's top, the last row's ink bottom
            rows = [cap_y // 2 + gap + j * step for j in range(len(lines))]
            e_bottom = max(probe.textbbox((0, y), ln, font=f_lab, anchor="ls")[3] for y, ln in zip(rows, lines))
            entries.append({"approx": bool(e.get("approximate")), "year_n": int(e["year"]), "lines": lines,
                            "font": f_lab, "rows": rows, "top": e_top, "bottom": e_bottom, "text": text,
                            "obeyed": obeyed, "short": short,
                            "inner": entry_inner_air(cap_y, f_lab, gap, step, len(lines)),
                            # the largest inner air: year to label, and line to line where it wraps
                            "inner_max": max([gap - cap_l] + ([step - cap_l] if len(lines) > 1 else [])),
                            # review 7: a one-line label's line gap is its nominal leading's, also under the
                            # tight leading; a wrapped label's is the gap its lines have (2026-11-03: the
                            # nominal leading is TALL_LEADING, 1.3 before)
                            "line": (step if len(lines) > 1 else max(step, round(f_lab.size * TALL_LEADING)))
                                    - cap_l})
        y_first = col_top + TALL_TOP_PAD - entries[0]["top"]
        y_last = bottom - entries[-1]["bottom"]                    # the furthest its last row may end
        lab_min = min(en["font"].size for en in entries)
        f_gap = get_font("Montserrat", min(span_px or span_font_px(lab_min), lab_min - SPAN_UNDER_LABEL),
                         weight=TALL_SPAN_WEIGHT)
        clear = span_air_rule(lab_min, f_gap, span_basis(entries), separate)
        band = cap_height(f_gap) + sum(clear) if on_segment else 0
        # review 7: each span's band from the label above it (span_basis_at), not the column's loosest
        bands_k = [cap_height(f_gap) + sum(span_air_rule(lab_min, f_gap, basis_at(entries, k + 1), separate))
                   for k in range(len(entries) - 1)]
        needs, caps = [], []
        for k in range(len(entries) - 1):
            ext = entries[k]["bottom"] - entries[k + 1]["top"]
            # round 4 review 6: a span's ink under its baseline (the comma of "2,956") is paid in its band
            air = bands_k[k] + span_descent(spans[k], f_gap) if band and spans[k] else TALL_NO_SPAN_AIR
            # 2026-11-03: the gap between entries at least TALL_ENTRY_SEPARATION x their largest inner air, as
            # the ink measures both (a row's ink can be a px shorter than its cap; the edges overshoot)
            least = (math.ceil(TALL_ENTRY_SEPARATION * (max(entries[k]["inner_max"], entries[k + 1]["inner_max"])
                                                        + 1)) + SPAN_OVERSHOOT)
            air = max(air, least)
            needs.append(ext + air)
            caps.append(ext + max(air, TALL_GAP_CAP * cap_y))   # and grows to at most this
        spacing = capped_spacing(timeline_positions(events, y_first, y_last, needs), events, caps)
        steps = [b - a for a, b in zip(spacing.positions, spacing.positions[1:])]
        ok = (spacing.honest or not honest) and all(st >= nd - 1 for st, nd in zip(steps, needs))
        return ok, (f_year, cap_y, entries, y_first, y_last, f_gap, needs, band, spacing, clear, separate, cut,
                    leading)

    # The order of give-way (2026-11-03 hierarchy spec C): at each year size (outer), from the largest down, the
    # leading (TALL_LEADING, then TALL_TIGHT_LEADING, so the tight leading comes before any year step), then the
    # stages. Spare height goes to the years only from a nominal layout (spec A, box fill; D: Pompeii at 84 px
    # with its spans whole): the years step down to TALL_YEAR_RATIO x the labels' cap height with the spans at
    # their size and air (the honest lead alone given up), and only at that floor do the spans step to
    # SPAN_MIN_PX and their air to its minimums; then the years to TALL_YEAR_RATIO_MIN (logged) with every stage,
    # and last a label is cut.
    nominal = [(True, True, None), (False, True, None)]
    squeeze = [(False, True, SPAN_MIN_PX), (False, False, None), (False, False, SPAN_MIN_PX)] if on_segment else []
    label_scales = list(dict.fromkeys(label_px_at(sc) for sc in scales))
    scale_of = {px: next(sc for sc in scales if label_px_at(sc) == px) for px in label_scales}

    def year_floor_ok(year_px: int, lab_px: int, ratio: float) -> bool:
        return (cap_height(get_font("Anton", year_px))
                >= ratio * cap_height(get_font("Montserrat", lab_px, weight=TALL_LABEL_WEIGHT)))

    def year_sizes(ratio: float) -> list[int]:
        # TALL_YEAR_STEPS down to `ratio` x the smallest labels' cap, then that floor itself (between two steps)
        cap_l = cap_height(get_font("Montserrat", label_scales[-1], weight=TALL_LABEL_WEIGHT))
        px_floor = next((px for px in range(year_px0, 1, -1)
                         if cap_height(get_font("Anton", px - 1)) < ratio * cap_l), 2)
        return sorted({*(round(year_px0 * s_) for s_ in TALL_YEAR_STEPS if round(year_px0 * s_) >= px_floor),
                       px_floor}, reverse=True)

    def choose(rows_max: int, ratio: float, floor_only: bool = False, squeeze_ok: bool = True):
        # at the full ratio the nominal stages over every year size, then the squeeze over them; under it (the
        # sizes the full ratio does not allow) every stage at once; `floor_only`: the years at `ratio` itself
        # round 10 (review, Petra): under the full ratio too, every year size with the spans' full separation
        # before any without it (the spans gave theirs up while the years still had steps to give)
        passes = [nominal, squeeze] if squeeze_ok else [nominal]
        for stages in passes:
            for year_px in (year_sizes(ratio)[-1:] if floor_only else year_sizes(ratio)):
                for leading in (TALL_LEADING, TALL_TIGHT_LEADING):
                    for lab_px in label_scales:
                        if not year_floor_ok(year_px, lab_px, ratio) or (
                                ratio < TALL_YEAR_RATIO and year_floor_ok(year_px, lab_px, TALL_YEAR_RATIO)):
                            continue
                        for honest, separate, span_px in stages:
                            ok, m = measure(year_px, scale_of[lab_px], honest, separate, span_px, rows_max,
                                            leading)
                            if ok:
                                return True, (year_px, scale_of[lab_px], m)
        return False, None

    def labels_px(choice) -> int:
        return min(en["font"].size for en in choice[2][2])

    # the rows a label needs whole at the label floor, None over 6 (the measure timeline_copy_fit reports)
    needed = [tall_label_rows_needed(tx, label_w - TALL_INK_RESERVE) for tx in texts]
    over = [(tx, r) for tx, r in zip(texts, needed) if r is None or r > budget]
    most = max(budget, *(r or TALL_LABEL_ROWS_MAX for r in needed))
    ladder = list(range(most, 1, -1))
    full, least = TALL_YEAR_RATIO, TALL_YEAR_RATIO_MIN

    # Round 4 review 9 (2026-11-03): the years give way only for what it buys: whole labels at the full ratio, then
    # whole at the least; where no layout keeps every label whole, the most rows at the full ratio, and the least
    # ratio only where it cuts fewer words than that (logged so).
    def dropped(choice) -> int:
        words = sum(len(tx.split()) for tx in texts)
        return words - sum(len(" ".join(en["lines"]).replace("…", "").split()) for en in choice[2][2])

    def first_fit(tries_: list[tuple[int, float]] | list[tuple[int, float, bool]], whole: bool):
        # round 12 (review: Colosseum 9:16's "8 YEARS LATER" 20 px over AD 80, read as AD 72's third label line):
        # every try with the spans' full separation (the years down to the least ratio) before any without it
        for squeeze_ok in (False, True):
            for t_ in tries_:
                rows_max, ratio = t_[0], t_[1]
                ok, choice = choose(rows_max, ratio, bool(t_[2]) if len(t_) > 2 else False, squeeze_ok)
                if ok and not (whole and choice[2][11]):
                    return choice
        return None

    chosen = None
    if not over:     # every label within its row budget (2 rows at 4 entries, 3 at 3)
        # round 6 review (spec F, give-way 5): two rows preferred; a third only once the years are at
        # TALL_YEAR_RATIO_MIN (3-4 entries)
        chosen = first_fit([(2, full), (2, least), (budget, least, True), (budget, least)] if many and budget > 2
                           else [(budget, full), (budget, least)], True)
    else:            # hierarchy spec C8: a copy-fit error upstream; set whole on more rows where they hold it
        for tx, r in over:
            log.error("Timeline copy-fit: label %r needs %s rows, budget %d at %d px; set whole on more rows "
                      "where the column holds it.", tx, r if r is not None else f"more than {TALL_LABEL_ROWS_MAX}",
                      budget, TALL_LABEL_MIN_PX)
        chosen = first_fit([(TALL_LABEL_ROWS_MAX, full), (TALL_LABEL_ROWS_MAX, least)], True)
    if chosen is not None:
        if not year_floor_ok(chosen[0], labels_px(chosen), full):
            log.warning("Timeline: the years set under %.1f x the labels' cap height to keep %d entries whole.",
                        full, len(events))
    else:
        at_full = first_fit([(r, full) for r in ladder], False)
        at_least = first_fit([(r, least) for r in ladder], False)
        chosen = at_full
        if at_least is not None and (at_full is None or dropped(at_least) < dropped(at_full)):
            chosen = at_least
            if not year_floor_ok(chosen[0], labels_px(chosen), full):
                log.warning("Timeline: the years set under %.1f x the labels' cap height to cut fewer words (%d, "
                            "not %s); no layout keeps every label whole.", full, dropped(at_least),
                            dropped(at_full) if at_full is not None else "no layout")
    if chosen is None:
        # round 7 (R3): the spans' own rows cost the column about 135 px; where nothing holds at the least ratio,
        # the years step on down to TALL_YEAR_RATIO_LAST (round 5's floor) before the column is set at its tightest
        chosen = first_fit([(r, TALL_YEAR_RATIO_LAST) for r in ladder], False)
        if chosen is not None:
            log.warning("Timeline: the years set under %.1f x the labels' cap height (at %.1f x, the last resort) to "
                        "keep %d entries and their spans in the column%s.", least, TALL_YEAR_RATIO_LAST, len(events),
                        " and to cut fewer words" if chosen[2][11] else "")
    if chosen is None:
        log.warning("Timeline: %d entries do not fit the column even at the label floor; set at the tightest, "
                    "the years set under %.1f x the labels' cap height.", len(events), least)
        least_px = year_sizes(TALL_YEAR_RATIO_LAST)[-1]
        chosen = (least_px, scales[-1], measure(least_px, scales[-1], False, False, SPAN_MIN_PX, 2,
                                                TALL_TIGHT_LEADING)[1])
    if on_segment and not chosen[2][10]:
        log.warning("Timeline spans set without their full separation to keep %d labels at %d px.",
                    len(events), labels_px(chosen))
    if chosen[2][11]:   # QF-23: no layout holds every label whole; the timeline is refused, never drawn cut
        raise TextFitError(" | ".join(en["text"] for en in chosen[2][2] if en["short"]),
                           where=f"the portrait timeline's label rows at {labels_px(chosen)} px")
    year_px, scale, m = chosen
    f_year, cap_y, entries, y_first, _y_last, f_gap, _needs, _band, spacing, _clear, separate, _cut, _lead = m
    for en in entries:
        if not en["obeyed"]:
            log.info("Timeline label %r: no break of its %d rows avoids a short word at a row's end; set balanced.",
                     en["text"], len(en["lines"]))
    if scale < 1.0 or year_px < year_px0:
        log.info("Timeline years at %d px (%d%%), labels at %d%% to keep %d entries above the caption band.",
                 year_px, round(100 * year_px / year_px0), round(scale * 100), len(events))
    ground = timeline_backdrop(photo, width, height)
    plates: list[tuple[str, Any]] = []
    twins = []
    for k, en in enumerate(entries):
        y = spacing.positions[k]
        en["y"] = y
        base = y + cap_y // 2
        # 2026-11-03: the year group's left ink edge (its circa mark, else the numeral) on xt
        first_box = year_ink_boxes(xt, base, en["year_n"], en["approx"], f_year)
        x_year = xt + (xt - first_box[0][0]) if first_box else xt
        en["year_plates"] = _year_plates(ground, x_year, base, en["year_n"], en["approx"], f_year, GOLD)
        en["label_plates"] = [plan_text(ground, (tall_left_x(xt, y + r, ln, en["font"], tall_track(en["font"].size)),
                                                 y + r), ln, en["font"], TALL_LABEL_FILL, "ls",
                                        track=tall_track(en["font"].size))
                              for r, ln in zip(en["rows"], en["lines"])]
        en["dim_year"] = [_twin(ground, pl, brand.STONE) for pl in en["year_plates"]]
        en["dim_labels"] = [_twin(ground, pl, brand.STONE_LT) for pl in en["label_plates"]]
        plates += [(f"year{k}" if j == len(en["year_plates"]) - 1 else f"year{k}_circa", pl)
                   for j, pl in enumerate(en["year_plates"])] + [(f"label{k}", p) for p in en["label_plates"]]
        twins += [(f"dim{k}", pl) for pl in en["dim_year"]] + [(f"dim{k}_label", p) for p in en["dim_labels"]]
    # The time since the previous date (round 7, coordinator R3: back from round 6's inline "+17 YRS"): centred in
    # the band between the two entries, on the segment it measures, beside the spine at its break mark, "LATER"
    # kept; never level with a year (a span beside a year read as part of the date). Montserrat TALL_SPAN_WEIGHT in
    # STONE, a tier under the TALL_LABEL_FILL labels; its air keeps SPAN_MIN_SEPARATION x the entries' own air.
    gaps = []
    col_right = TEXT_RIGHT * width // VIDEO_WIDTH
    bands = {k: (entries[k - 1]["y"] + entries[k - 1]["bottom"], entries[k]["y"] + entries[k]["top"])
             for k in range(1, len(entries))}
    lab_min = min(en["font"].size for en in entries)

    def span_font(px: int) -> FreeTypeFont:
        return get_font("Montserrat", px, weight=TALL_SPAN_WEIGHT)

    def gap_xy(k: int, font, descent: float = 0.0) -> tuple[float, float]:
        # the span leans toward the later entry (span_airs); of any slack the time scale leaves in the band, a
        # third goes under it and two thirds above (span_slack_below)
        a, b = bands[k]
        above, below = span_air_rule(lab_min, font, basis_at(entries, k), separate)
        slack = max(0.0, (b - a) - (cap_height(font) + above + below + descent))
        return xt, max(b - below - descent - span_slack_below(slack), a + above + cap_height(font))

    def fits(k: int, text: str, font) -> bool:
        a, b = bands[k]
        need = (cap_height(font) + sum(span_air_rule(lab_min, font, basis_at(entries, k), separate))
                + span_descent(text, font))
        return b - a >= need - 1 and xt + tracked_width(text, font, 2) <= col_right - SPAN_OVERSHOOT
    for k, text in [(k, t) for k, t in enumerate(spans, start=1) if t]:
        # never dropped: the span shrinks to SPAN_MIN_PX to fit its band, "LATER" kept while any size does; where
        # none does it is set at SPAN_MIN_PX in the clear band between the two entries (span_fallback_y), or the
        # nearest place on its segment clear of every plate (free_span_xy), never on a year's band
        placed = span_fit(text, f_gap.size, lambda tx, px, k=k: fits(k, tx, span_font(px)))
        if placed:
            text, font = placed[0], span_font(placed[1])
            xy = gap_xy(k, font, span_descent(text, font))
            xy = (tall_left_x(xt, xy[1], text, font, 2), xy[1])
        else:
            font = span_font(SPAN_MIN_PX)
            if xt + tracked_width(text, font, 2) > col_right:
                text = text.removesuffix(" LATER")
            a, b = bands[k]
            inner = basis_at(entries, k)
            y_fb = span_fallback_y(a, b, cap_height(font), inner)
            xy = (tall_left_x(xt, y_fb, text, font, 2), y_fb)
            trial = plan_text(ground, xy, text, font, brand.STONE, "ls", track=2)
            lo, hi = entries[k - 1]["y"], entries[k]["y"] + cap_y / 2
            ys = sorted(range(math.floor(lo), math.ceil(hi) + 1, 2), key=lambda y: abs(y - xy[1]))
            under = max(0, span_least_above(inner) - SPAN_FALLBACK_AIR)
            obstacles = [(b_[0], b_[1], b_[2], b_[3] + (under if nm.startswith("label") else 0))
                         for nm, b_ in ((nm, pl.box) for nm, pl in plates)]
            # a year's whole band (the column's width) is an obstacle: a span never sits level with a year
            obstacles += [(xt - 4, pl.box[1], col_right, pl.box[3]) for nm, pl in plates if nm.startswith("year")]
            spot = free_span_xy(trial.box, xy, [xy[1]] + ys, [xy[0]], obstacles, col_right,
                                SPAN_FALLBACK_AIR, [pl.box for nm, pl in plates if nm.startswith("year")])
            if spot is None:
                log.error("Timeline span %r: no clear place on its segment; set in its band.", text)
            xy = spot or xy
            log.warning("Timeline span %r set at %d px without its full air: its band is %d px.", text,
                        SPAN_MIN_PX, b - a)
        plate = plan_text(ground, xy, text, font, brand.STONE, "ls", track=2)
        gaps.append((k, plate))
        plates.append((f"gap{k}", plate))
    # the years' ink outweighs their labels' (TALL_INK_RATIO), logged where it does not
    for k, en in enumerate(entries):
        yi = sum(int((pl.glyphs > 127).sum()) for pl in en["year_plates"] if pl.glyphs is not None)
        li = sum(int((pl.glyphs > 127).sum()) for pl in en["label_plates"] if pl.glyphs is not None)
        if li and yi < TALL_INK_RATIO * li:
            log.info("Timeline entry %d: its year's ink is %.2f x its label's (under %.1f).", k, yi / li,
                     TALL_INK_RATIO)
    # each break mark on the spine in the clear band between the two entries it separates (between the dots if
    # there is none), level with the span label beside it
    marks = []
    spans_at = {k: p.xy[1] - cap_height(p.font) / 2 for k, p in gaps}
    for k in spacing.breaks:
        a, b = bands[k]
        marks.append((k, spans_at[k] if k in spans_at else (a + b) / 2 if b - a >= 36
                      else (entries[k - 1]["y"] + entries[k]["y"]) / 2))
    sched = reveal_schedule(len(entries), CLIP_SECONDS)
    ys = [en["y"] for en in entries]
    u = width / VIDEO_WIDTH
    last: list[Image.Image] = []
    marks_nt = spine_marks((width, height), False, x0, y_first, ys[-1], max(2, round(6 * u)),
                           [(y, 16 * u, bool(e.get("approximate"))) for y, e in zip(ys, events)], 4 * u,
                           [m for _, m in marks], 36 * u, 4 * u)
    spine_col = spine_colour(ground, marks_nt[0][1])

    drift = GroundDrift(ground)

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = drift(t)
            d = ImageDraw.Draw(im, "RGBA")
            head = sched.head(ys, t, ys[-1])
            d.line([(x0, y_first), (x0, head)], fill=(*spine_col, 255), width=max(2, round(6 * u)))
            for k, y_mark in marks:
                if head >= y_mark + 12 * u:
                    _break_glyph(d, x0, y_mark, False, 36 * u, (*brand.STONE_LT, 255), 4 * u, ground.getpixel((x0, int(y_mark))))
            for k, en in enumerate(entries):
                a = sched.appear(k, t)
                if a <= 0:
                    continue
                back = sched.focus(k, t)            # stepped back: a stone twin at full opacity, a smaller node
                r, y = 16 * u * a * sched.pulse(k, t) * (1 - 0.3 * back), en["y"]
                _node(d, x0, y, r, bool(events[k].get("approximate")), 1.0, 4 * u, brand.INK)
                current = a * (1 - _ease((t - sched.at[k + 1]) / 0.4)) if k + 1 < len(entries) else a
                if current > 0.02:                                    # the current event's ring
                    rr = (16 + 10 * a) * u
                    d.ellipse([x0 - rr, y - rr, x0 + rr, y + rr], outline=(*GOLD, int(150 * current)),
                              width=max(1, round(3 * u)))
                dy = round(14 * u * (1 - a))
                im = _focused(im, [*en["year_plates"], *en["label_plates"]], [*en["dim_year"], *en["dim_labels"]],
                              a, back, dy)
                d = ImageDraw.Draw(im, "RGBA")
            for k, plate in gaps:   # the elapsed time lands with its later year
                a = sched.appear(k, t)
                if a > 0:
                    im = draw_plate(im, plate, a, round(14 * u * (1 - a)))
                    d = ImageDraw.Draw(im, "RGBA")
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)
        record_nontext(out, last[0], marks_nt, drift((n - 1) / VIDEO_FPS))
    return result


# 16:9 words used to stay where the master's vignette left them legible (see _SECTION_WIDE): design x 400-1600,
# y 190-800. The scale and cross-section still keep to it; the timeline v2 runs across WIDE_TIMELINE.
WIDE_SAFE = (400, 190, 1600, 800)
WIDE_TITLE_SAFE = (153, 129, 1766, 820)   # the 16:9 title-safe box above the caption line, design px


WIDE_SPAN_OFF = 26      # design px from the 16:9 spine to a span's nearest ink, either side
WIDE_SPAN_CLEAR = 48    # round 4 review: a 16:9 span's least air to any event block (2 x the labels' line air)
WIDE_SPAN_MIN_PX = 30   # the 16:9 span's floor, its own since the 9:16 floor fell to 24 (2026-11-03: 16:9 out of scope)
WIDE_SPAN_PX = (32, WIDE_SPAN_MIN_PX)   # design px: a 16:9 span's size (round 4: 26 before) and, review 5, its floor
WIDE_FLAG_MIN = 40.0    # round 4 review 5: design px, the shortest flag from the spine to a block (74 at rest)
WIDE_FLAG_STEP = 36.0   # round 4 review 6: and the steps it lengthens by where a span needs the air by the spine
# (above, below) added to the 74 px flags; round 4 review 7: in half steps too, the least added first, so a span
# that needs the air on both sides of the spine gets it on both (a stacked "LATER" under the spine needs the
# lower years lower, a span over it the upper labels higher) where title-safe has the room
WIDE_FLAG_TRIES = tuple(sorted(((a * WIDE_FLAG_STEP / 2, b * WIDE_FLAG_STEP / 2) for a in range(5) for b in range(5)),
                               key=lambda ab: (ab[0] + ab[1], abs(ab[0] - ab[1]))))
WIDE_LABEL_COL = 420.0  # round 6 (2026-11-03): design px, a 16:9 label's column (it widened to WIDE_LABEL_MAX before)
WIDE_LABEL_ROWS = 3     # round 6: and its rows at most, balanced (no lone first word: WIDE_LONE_SHARE)
WIDE_LABEL_SLACK = 40.0  # round 8 (review 7): design px a label may run past its column where that loses a weak row
                         # (round 9, review 8: 16 before; "THE SITE IS / DELIBERATELY BURIED" is 456 px at the floor)
WIDE_LABEL_MAX = 1000.0  # round 4 review 6: design px, the widest measure a 16:9 label takes to stay whole
WIDE_LABEL_STEP = 40.0  # and the steps it widens by from the layout's common measure
WIDE_LONE_SHARE = 0.6   # round 4 review 6: a label's lone first word narrower than this share of its widest line
WIDE_MIDDLE_SHARE = 0.66  # round 8 (review 7): and a middle row ("AND BANK" at 0.645 of "THE FIRST DITCH" read weak)
WIDE_SPAN_LEVEL_NEAR = 2 * WIDE_SPAN_CLEAR  # round 4 review 7: design px, a label line this near a span never
                                            # shares its baseline (nz27's "ABOUT 7,500 YEARS" 40 px after a line)
WIDE_SPAN_REACH = 170   # and, the last resort only (logged), the furthest a span's nearest ink sits from the spine
WIDE_SPAN_ROW_NEAR = 2 * WIDE_SPAN_LEVEL_NEAR  # round 4 review 9 (2026-11-03): design px, and there a label line
                                               # level with it keeps this far (xv15's "ABOUT 2,500 YEARS LATER" sat
                                               # 100 px from the lines either side and read as part of their row)
WIDE_SPAN_LEAN = 18     # round 4 review 7: design px a span's centre sits past its segment's middle, toward the later
WIDE_SPAN_ABOVE_COST = 10  # round 9 (review 7): a span over the spine, against a span's own costs (at most 5)
WIDE_SPAN_ACROSS = 2.0  # round 4 review 8: x a block's line gap, a span's least air under or over it, across its width


def box_clearance(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """The air between two boxes: the gap across or down between them (their corner distance where they are
    apart both ways), negative where they overlap."""
    dx = max(b[0] - a[2], a[0] - b[2])
    dy = max(b[1] - a[3], a[1] - b[3])
    if dx < 0 and dy < 0:
        return max(dx, dy)
    if dx >= 0 and dy >= 0:
        return math.hypot(dx, dy)
    return max(dx, dy)


def compact_gap(text: str) -> str:
    """Round 7 (R3): a 16:9 span's compact wording where its full one has no place, still saying "later":
    "ABOUT 1,700 YEARS LATER" -> "~1,700 YRS LATER", "17 YEARS LATER" -> "17 YRS LATER"."""
    return text.replace("ABOUT ", "~").replace(" YEARS LATER", " YRS LATER")


TIMELINE_UNCLEAN = "unclean layout"   # round 14 (directive 4): the ERROR log's mark for a 16:9 set no layout keeps
                                      # clean (a span off one row, a weak row): logged and tested, never silent


def wide_span_lines(text: str, stacked: bool | int) -> list[str]:
    """A 16:9 span's lines: one, or (`stacked`; ruling R11, spec G amended) two in the one fixed form, the count
    over its unit: "1,669 / YEARS LATER", "ABOUT 700 / YEARS LATER" (the break before "YEARS" for an exact count,
    after the count for an approximate one). "LATER" alone on a row is never set (the old breaks before "LATER",
    and "ABOUT 5,500 / YEARS / LATER", are gone): any `stacked` asks for that form. A span without "YEARS LATER"
    (a compact or other wording) breaks before its last two words where it has three, else stays on one row."""
    words = text.split()
    if not stacked or len(words) < 3:
        return [text]
    return [" ".join(words[:-2]), " ".join(words[-2:])]


def wide_span_clearance(box: tuple[float, float, float, float], block: tuple[float, float, float, float]) -> float:
    """The air between a span's box and an event block's: their vertical gap where they overlap across (infinite
    where they do not, the span then reading only with its own segment)."""
    if box[2] <= block[0] or box[0] >= block[2]:
        return float("inf")
    return max(block[1] - box[3], box[1] - block[3])


def _render_timeline_wide(events: list[dict[str, Any]], out: Path, width: int, height: int,
                          photo: Path | None) -> Path | None:
    """The 16:9 timeline v2: a GOLD_DIM spine across the frame (WIDE_TIMELINE) over the quiet photo, the events on a
    true time scale (timeline_positions, break marks on compressed gaps), each event one block that starts at its
    own marker (a hairline flag up or down from the dot, the year in gold Anton and the label in bone Montserrat
    left-aligned beside the flag), alternately above and below the spine, at least 24 px clear of everything else;
    the elapsed time (gap_text) centred on each gap, above the line."""
    g = _WideGrid(width, height)
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    run0, run1 = WIDE_TIMELINE
    spine_y = 540.0
    f_year = get_font("Anton", g.f(92))
    track = S(2)
    cap_y = cap_height(f_year) / g.k
    flag, pad, clear, gap_pad = 74.0, 22.0, 24.0, 30.0      # dot to block, flag to text, block to block, gap label
    texts = [drawable(e["label"].upper(), "Montserrat") for e in events]
    wide_source: dict[str, str] = {}   # round 9 (review 8): each label's own case, for the names its breaks keep whole
    for e, tx in zip(events, texts):
        wide_source.setdefault(tx, str(e["label"]))
    held_memo: dict[str, set[int]] = {}
    # Round 4 review (2026-11-02): the same words as 9:16, "LATER" kept: a span too wide for its gap on one line
    # breaks before "LATER" (wide_span_lines) instead of dropping it ("ABOUT 1,500 YEARS" here, "... LATER" there).
    # Round 7 (coordinator R3): the full form ("1,669 YEARS LATER"), as on 9:16 ("+1,669 YRS" did not say later);
    # one row where it fits, broken before "LATER" where not, always under the spine (spec G)
    gap_texts = [gap_text(events[k - 1], events[k]) for k in range(1, len(events))]
    safe_px = (X(WIDE_TITLE_SAFE[0]), Y(WIDE_TITLE_SAFE[1]), X(WIDE_TITLE_SAFE[2]), Y(WIDE_TITLE_SAFE[3]))
    span_fonts = [get_font("Montserrat", g.f(px), weight=700) for px in WIDE_SPAN_PX]
    span_rel: dict[tuple[int, str], tuple[int, int, int, int] | None] = {}
    layout_memo: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    geo_memo: dict[tuple[Any, ...], tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    cap_memo: dict[Any, int] = {}       # keyed by the font itself (kept alive, so never a reused id)
    width_memo: dict[tuple[int, tuple[str, ...]], float] = {}

    def cap_(font) -> int:
        # review 8 (2026-11-03): the layout search asked for the same few fonts' cap heights 80,000 times
        if font not in cap_memo:
            cap_memo[font] = cap_height(font)
        return cap_memo[font]

    def span_w(fi: int, lines: list[str]) -> float:
        # design px: a span setting's widest line, measured once per setting (review 8: 67,000 measures)
        key = (fi, tuple(lines))
        if key not in width_memo:
            width_memo[key] = max(tracked_width(ln, span_fonts[fi], S(1)) for ln in lines) / g.k
        return width_memo[key]

    def span_rel_box(fi: int, line: str) -> tuple[int, int, int, int] | None:
        # a span line's ink set at the origin, measured once (ink_box): moved to each whole-pixel place
        if (fi, line) not in span_rel:
            span_rel[(fi, line)] = ink_box((0, 0), line, span_fonts[fi], "ms", S(1))
        return span_rel[(fi, line)]

    def span_box(fi: int, line: str, x: int, y: int) -> tuple[int, int, int, int] | None:
        r = span_rel_box(fi, line)
        return None if r is None else (r[0] + x, r[1] + y, r[2] + x, r[3] + y)

    block_memo: dict[tuple[str, float, int], tuple[list[str], Any, bool]] = {}
    widened: set[str] = set()   # round 6: labels set past the WIDE_LABEL_COL column to stay whole
    deep_set: set[str] = set()  # round 7: labels set on more than WIDE_LABEL_ROWS rows of the column to stay whole
    slack_set: set[str] = set()  # round 8: labels set up to WIDE_LABEL_SLACK past the column to lose a weak row
    floor_font = get_font("Montserrat", g.f(34), weight=800)

    def widest_word(text: str) -> float:
        # design px: the label's widest word at the floor size, the narrowest measure that never splits it
        return max((_tracked_w(w, floor_font, track) for w in text.split()), default=0.0) / g.k + 1

    def fit_block(text: str, measure: float, rows_max: int = 4) -> tuple[list[str], Any, bool]:
        """A label's lines at `measure`: three at 38 px, else three at a smaller size down to the 34 px floor, else
        four there, else (with `rows_max`) five or six (round 4 review 7: the order the label gives way in before
        any wider measure, which `layout_blocks` takes last). The measure is never narrower than the label's
        widest word, so no word is cut for its own width ("FLOODED…" where "CONSTANTINOPLE" did not fit 270 px).
        A lone first word ("MAUSOLEUM / FORTIFICATIONS / ...") takes the floor size, then the balanced break at
        the same rows that does not leave it lone, then one more row, while any of them avoids it."""
        key = (text, measure, rows_max)
        if key not in block_memo:
            m = S(max(measure, widest_word(text)))
            # round 4 review 5: each line pays for its own tracking (fit_tracked), not the whole label's
            got = _probe_tracked(probe, text, "Montserrat", g.f(38), m, 3, g.f(34), track, weight=800,
                              quiet=True)   # round 4: 34 / 32 before
            if rows_max < 3:
                got = _probe_tracked(probe, text, "Montserrat", g.f(38), m, rows_max, g.f(34), track, weight=800,
                                  quiet=True)
            for rows in range(4, rows_max + 1):
                if not got[2]:
                    break
                got = _probe_tracked(probe, text, "Montserrat", g.f(34), m, rows, g.f(34), track,
                                  weight=800, quiet=True)
            if not got[2] and weak_rows(got[0], got[1]):
                block_prev = got
                # review 6: a lone first word at 38 px ("FIRST / CHAMBERS / CUT") gives way to the floor size
                # where that breaks it better ("FIRST CHAMBERS CUT"); review 7: then a balanced break of the same
                # rows, then one more row, that leaves it as wide as the lines under it
                alts = []
                # round 7 (review 7): the most even break of the same rows at its own size first ("THE FIRST /
                # DITCH AND / BANK ARE DUG", not "THE FIRST DITCH / AND BANK / ARE DUG")
                even = even_rows(text, got[1], m, len(got[0]))
                if even:
                    alts.append((even, got[1], False))
                # round 12 (review: "NAMED A / WORLD / HERITAGE SITE"): the most even breaks at the floor size too,
                # in fewer rows first ("NAMED A / WORLD HERITAGE SITE" holds the name whole only there)
                for rows in range(2, len(got[0]) + 1):
                    even_f = even_rows(text, floor_font, m, rows) if got[1].size > floor_font.size else None
                    if even_f:
                        alts.append((even_f, floor_font, False))
                # round 16 (review 6: "THE TOWER / OF JESUS CHRIST"): where the break taken splits a proper name,
                # the break that keeps every name whole (and leaves no weak row) in 2 to rows_max rows, at its own
                # size then the floor's, before the even breaks; the layout then widens its column for it
                if label_hard_fault(got[0], wide_source.get(" ".join(got[0]))):
                    m_try, whole_got = m, None
                    while whole_got is None and m_try <= S(WIDE_LABEL_MAX):
                        for f_try in dict.fromkeys((got[1], floor_font)):
                            # in its own column, more rows first (to six, as a cut label takes them); wider only
                            # at the rows allowed (logged)
                            top = 6 if m_try == m else max(rows_max, len(got[0]))
                            for rows in range(2, top + 1):
                                whole = name_whole_rows(text, f_try, m_try, rows, weak_ok=m_try == m)
                                if whole:
                                    whole_got = (whole, f_try, False)
                                    break
                            if whole_got is not None:
                                break
                        m_try += S(WIDE_LABEL_STEP)
                    if whole_got is not None:
                        alts.insert(0, whole_got)
                if got[1].size > g.f(34):
                    alts.append(_probe_tracked(probe, text, "Montserrat", g.f(34), m, len(got[0]), g.f(34), track,
                                            weight=800, quiet=True))
                for rows in range(2, len(got[0])):      # round 7: fewer rows at the floor size first
                    alt = _probe_tracked(probe, text, "Montserrat", g.f(34), m, rows, g.f(34), track, weight=800,
                                      quiet=True)
                    if not alt[2] and len(alt[0]) == rows:
                        alts.append(alt)
                for f_try in dict.fromkeys((got[1], floor_font)):
                    for rows in range(len(got[0]), rows_max + 1):
                        lines = tracked_wrap(text, f_try, m, track, lines=rows, lone_share=WIDE_LONE_SHARE)
                        if lines:
                            alts.append((lines, f_try, False))
                rank0 = weak_rank(got[0], got[1])
                got = next((alt for alt in alts if not alt[2] and not weak_rows(alt[0], alt[1])),
                           # round 8: else one less weak in as many rows (a weak middle row before a widow, a
                           # weak row nearer full before a shorter one)
                           min((alt for alt in alts if not alt[2] and not lone_first(alt[0], alt[1])
                                and len(alt[0]) <= len(got[0]) and weak_rank(alt[0], alt[1]) < rank0),
                               key=lambda alt: weak_rank(alt[0], alt[1]),
                               default=next((alt for alt in alts if not alt[2] and not lone_first(alt[0], alt[1])),
                                            got)))
                if weak_rank(got[0], got[1]) > weak_rank(*block_prev[:2]):
                    got = block_prev
            elif got[2] and lone_first(got[0], got[1]):
                # a cut label keeps its words and its size; only its break is balanced, in as many rows
                for rows in range(len(got[0]), rows_max + 1):
                    lines = tracked_wrap(" ".join(got[0]), got[1], m, track, lines=rows, lone_share=WIDE_LONE_SHARE)
                    if lines and not lone_first(lines, got[1]):
                        got = (lines, got[1], True)
                        break
            block_memo[key] = got
        return block_memo[key]

    def lone_first(lines: list[str], font) -> bool:
        """Round 4 review 6 (2026-11-03): one short word alone on a label's first line ("FIRST / CHAMBERS CUT"),
        narrower than WIDE_LONE_SHARE of its widest line, or a label stacked a word to a line on three or more."""
        return weak_rows(lines, font, middle=False)

    def weak_rows(lines: list[str], font, middle: bool = True) -> bool:
        """lone_first, and (round 7, review 7; `middle`) a weak middle row between two ("SURYAVARMAN II / BEGINS /
        THE TEMPLE", "THE FIRST DITCH / AND BANK / ARE DUG"), narrower than WIDE_LONE_SHARE of the widest other,
        or (round 8, review 7) a widow: a last row of one word that narrow ("... / LAYS THE FIRST / STONE")."""
        return weak_level(lines, font, middle) > 0

    def weak_level(lines: list[str], font, middle: bool = True) -> int:
        """weak_rows as a rank: 0 none, 1 a lone first word or a weak middle row, 2 a widow (round 8, review 7: a
        weak middle row of two words reads better than a one-word last row, "BISHOP MAURICE / LAYS THE / FIRST
        STONE"), 3 a label stacked a word to a row."""
        return weak_rank(lines, font, middle)[0]

    def weak_rank(lines: list[str], font, middle: bool = True) -> tuple[int, float, int]:
        """(weak_level, how short its weakest flagged row falls: the share it misses by), so of two weak breaks
        the one whose weak row is nearer full reads better ("BEGIN AT THE" at 0.58 before "BEGIN" at 0.28)."""
        if len(lines) < 2:
            return 0, 0.0, 0
        if len(lines) >= 3 and all(len(ln.split()) == 1 for ln in lines):
            return 3, 1.0, len(lines)
        if middle and label_hard_fault(lines, wide_source.get(" ".join(lines))):
            return WIDE_HARD_LEVEL, 1.0, 1        # round 13 (directive 4): a hard fault, worst of all

        # review 9: measured on the ink, as the frame shows it ("GERMAN" 168 px over "EXCAVATIONS" 282, 0.596,
        # passed on the advances)
        def ink_w(ln: str) -> float:
            bb = ink_box((0.0, 0.0), ln, font, "ls", track)
            return 0.0 if bb is None else float(bb[2] - bb[0])
        last = len(lines) - 1
        level, miss, flags = 0, 0.0, 0
        # round 9 (review 8): a break inside a name or after a figure ("ON THE CHAMP / DE MARS", "WITH 100 / DAYS")
        # reads as weak as a widow (_held_breaks), so a weak middle row that keeps it whole is taken before it
        joined = " ".join(lines)
        # round 10 (review 6): lone_first (`middle` False) asks about a lone first word alone; a held break
        # counted there flagged "TWENTY THOUSAND / WORKERS FINISH IT" lone and kept the three-row break
        if middle and joined in wide_source:
            if joined not in held_memo:
                held_memo[joined] = _held_breaks(joined.split(), wide_source[joined])
            cuts, at = set(), 0
            for ln in lines[:-1]:
                at += len(ln.split())
                cuts.add(at)
            hit = cuts & held_memo[joined]
            if hit:
                # round 10 (review 6): after a whole spelled count ("TWENTY THOUSAND / WORKERS FINISH IT") weighs
                # as a weak row, under a break inside it ("TWENTY / THOUSAND / ...", which also left a lone word)
                soft = hit <= _soft_count_breaks(joined.split())
                level, miss = (1, 0.5) if soft else (2, 1.0)
                flags += len(hit)
        # the first row, each middle row, then (round 8) the last: a widow only in a block of three rows (a deeper
        # block is already the copy-fit's last resort, its breaks kept as they were)
        widow = middle and 3 <= len(lines) <= WIDE_LABEL_ROWS     # (a two-row label's short last row is its own)
        for j in range(len(lines) if widow else len(lines) - 1 if middle else 1):
            if j in (0, last) and len(lines[j].split()) != 1:
                continue                         # a first or last row of two words or more is never lone
            others = [ink_w(ln) for i, ln in enumerate(lines) if i != j]
            share = WIDE_MIDDLE_SHARE if 0 < j < last else WIDE_LONE_SHARE
            ratio = ink_w(lines[j]) / max(1.0, max(others))
            if ratio < share:
                # a middle row: by its width, one word or two ("AND BANK"); a last row: a widow
                level = max(level, 2 if (j == last and j != 0) else 1)
                miss = max(miss, share - ratio)
                flags += 1
        # round 12 (review: "NAMED A / WORLD / HERITAGE SITE" tied "NAMED A WORLD / HERITAGE SITE"): of two
        # equally weak breaks, the one with fewer weak rows and held breaks
        return level, round(miss, 4), flags

    def name_whole_rows(text: str, font, measure: float, rows: int, weak_ok: bool = False) -> list[str] | None:
        """Round 16 (review 6): of the breaks of `text` into `rows` rows within `measure`, those that split no
        proper name and (unless `weak_ok`: in the label's own column, a weak row costs less than a wider measure)
        leave no weak row (label_hard_fault, weak_rows), the one whose widest row is narrowest; None where none is.
        O(C(words, rows - 1)), labels being a few words."""
        from itertools import combinations, pairwise
        words = text.split()
        src = wide_source.get(text)
        best_: tuple[float, list[str]] | None = None
        if len(words) < rows or len(words) > 14:
            return None
        for cuts in combinations(range(1, len(words)), rows - 1):
            bounds = (0, *cuts, len(words))
            lines = [" ".join(words[a:b]) for a, b in pairwise(bounds)]
            w_ = max(_tracked_w(ln, font, track) for ln in lines)
            if w_ > measure or (best_ is not None and w_ >= best_[0]):
                continue
            if label_hard_fault(lines, src) or (not weak_ok and weak_rows(lines, font)):
                continue
            best_ = (w_, lines)
        return None if best_ is None else best_[1]

    def even_rows(text: str, font, measure: float, rows: int) -> list[str] | None:
        """Round 7: the break of `text` into `rows` rows, each within `measure` (frame px), whose narrowest row is
        widest (no row ending on a TALL_STOP_WORDS word where any break avoids it); None where none fits. O(n^2)
        break pairs for n words (a label has a dozen at most)."""
        from itertools import combinations, pairwise
        words = text.split()
        if rows < 2 or len(words) < rows:
            return None
        best: tuple[tuple[int, int, float, int, int, float], list[str]] | None = None
        for cuts in combinations(range(1, len(words)), rows - 1):
            bounds = (0, *cuts, len(words))
            lines = [" ".join(words[a:b]) for a, b in pairwise(bounds)]
            ws = [_tracked_w(ln, font, track) for ln in lines]
            if max(ws) > measure:
                continue
            # round 8 (review 7): a break with no weak row or widow first, even where a row then ends on a short
            # word ("BISHOP MAURICE / LAYS THE / FIRST STONE", not "... / LAYS THE FIRST / STONE")
            key = (int(lone_first(lines, font)), *weak_rank(lines, font),
                   sum(len(ln.split()) == 1 for ln in lines[1:-1]), sum(ln.split()[-1] in TALL_STOP_WORDS for ln in lines[:-1]),
                   -min(ws[:-1]) / max(ws))
            if best is None or key < best[0]:
                best = (key, lines)
        return None if best is None else best[1]

    def kept_words(lines: list[str]) -> int:
        return len(" ".join(lines).replace("…", "").split())

    def ink_reach(k: int, lines: list[str], f_lab) -> float:
        """Round 4 review 9 (2026-11-03): design px from block k's flag to the right edge of its ink (its year's
        and its label lines', ink_box), not their last advance: a line ending in S overshoots its advance by 5-6
        px ("EXCAVATIONS": advance 278.2, ink 284), so the advance judged every layout of babylon4 outside
        title-safe. One frame px more for the fraction of a pixel the block's real place adds."""
        x0, base = X(pad), Y(400.0)
        boxes = year_ink_boxes(x0, base, int(events[k]["year"]), bool(events[k].get("approximate")), f_year)
        boxes += [ink_box((X(pad + 2), base), ln, f_lab, "ls", track) for ln in lines]
        return pad + (max((bb[2] for bb in boxes if bb), default=x0) - x0 + 1) / g.k

    def layout_blocks(label_max: float, widen: bool = False, rows_max: int = 4,
                      rows_cap: int | None = None, deep: bool = True, unweak: bool = False) -> list[dict[str, Any]]:
        """Each label's block at `label_max`; with `widen`, a label cut or left with a lone first word there takes
        the narrowest wider measure (WIDE_LABEL_STEP steps to WIDE_LABEL_MAX) at which it is neither (round 4
        review 6, 2026-11-03: one common measure cut "REDISCOVER…" where the frame had room for it whole).
        Round 4 review 7: a label no measure holds whole keeps the cut with the most words of all those tried, and
        with `rows_cap` a label on more rows than that widens too, to the first measure that sets it on as few (a
        deep block takes the spine's air on its side: fz26 cut "ABANDONED…" rather than widen to three rows).
        Review 8: measured once per setting (each span wording asked again for the same blocks); never mutated."""
        key = (label_max, widen, rows_max, rows_cap, deep, unweak)
        if key in layout_memo:
            return layout_memo[key]
        blocks = []
        for k, text in enumerate(texts):
            lines, f_lab, short = fit_block(text, label_max, rows_max)
            deep = rows_cap is not None and len(lines) > rows_cap
            # round 8 (review 7, `unweak`: its own variants, after the others): a weak row or a widow widens too
            weak0 = unweak and not short and weak_rows(lines, f_lab)
            if widen and (short or lone_first(lines, f_lab) or deep or weak0):
                m = label_max
                while m < WIDE_LABEL_COL:
                    m = min(WIDE_LABEL_COL, m + WIDE_LABEL_STEP)
                    got = fit_block(text, m, rows_max)
                    # round 8 (review 7): a weak row or a widow widens the measure too, up to the column
                    if (not got[2] and not lone_first(got[0], got[1])
                            and not (weak0 and weak_level(got[0], got[1]) >= weak_level(lines, f_lab))
                            and (rows_cap is None or len(got[0]) <= rows_cap)):
                        lines, f_lab, short = got
                        break
                    if short and (not got[2] or kept_words(got[0]) > kept_words(lines)):
                        lines, f_lab, short = got        # whole at least (its first word alone), or more of it
                if weak0 and not short and weak_level(lines, f_lab) > 1:
                    # round 8 (review 7): a weak row the column cannot break away ("HUGO'S NOVEL / SAVES / THE
                    # BUILDING": "SAVES THE BUILDING" is 422 px) runs up to WIDE_LABEL_SLACK past it (logged)
                    got = fit_block(text, WIDE_LABEL_COL + WIDE_LABEL_SLACK, rows_max)
                    if (not got[2] and not lone_first(got[0], got[1])
                            and weak_level(got[0], got[1]) < weak_level(lines, f_lab)
                            and (rows_cap is None or len(got[0]) <= rows_cap)):
                        lines, f_lab, short = got
                        slack_set.add(text)
                # round 7 (review 7, spec G): a label no WIDE_LABEL_COL column holds whole in WIDE_LABEL_ROWS rows
                # takes more rows in that column first (a copy-fit error, logged), and widens past it only where
                # no row count holds it (logged), rather than lose words
                for rows_more in range(WIDE_LABEL_ROWS + 1, 7):
                    if not short or not deep:
                        break
                    for m2 in dict.fromkeys((label_max, WIDE_LABEL_COL)):
                        got = fit_block(text, m2, rows_more)
                        if not got[2]:
                            lines, f_lab, short = got
                            deep_set.add(text)
                            break
                while short and m < WIDE_LABEL_MAX:            # wider, at the rows allowed (shallow blocks)
                    m = min(WIDE_LABEL_MAX, m + WIDE_LABEL_STEP)
                    got = fit_block(text, m, rows_max)
                    if not got[2]:
                        lines, f_lab, short = got
                        widened.add(text)
                for rows_more in range(rows_max + 1, 7):          # then wider and deeper, before any cut
                    if not short:
                        break
                    for m2 in (label_max, WIDE_LABEL_COL, WIDE_LABEL_MAX):
                        got = fit_block(text, m2, rows_more)
                        if not got[2]:
                            lines, f_lab, short = got
                            widened.add(text)
                            break
            f_lab = get_font("Montserrat", f_lab.size, weight=800)
            blocks.append({"lines": lines, "font": f_lab, "w": ink_reach(k, lines, f_lab), "short": short,
                           "lone": lone_first(lines, f_lab),
                           "weak": 0 if lone_first(lines, f_lab) else weak_level(lines, f_lab),
                           # round 13 (coordinator directive 4): a proper name split or a one-word middle row
                           "hard": bool(label_hard_fault(lines, wide_source.get(" ".join(lines)))),
                           # round 7 (review 7): set past its column (ranked after a cut and a lone first word)
                           "wide": max((_tracked_w(ln, f_lab, track) for ln in lines), default=0.0) / g.k
                                   > WIDE_LABEL_COL + (WIDE_LABEL_SLACK if text in slack_set else 0) + 1,
                           # review 7: a label cut to a single word ("FLOODED…") reads worst of all
                           "one_word": short and kept_words(lines) < 2,
                           "dropped": len(text.split()) - kept_words(lines) if short else 0,
                           # its own line gap (leading less cap height): a span keeps 1.5 x it (review 6)
                           "line_gap": (round(f_lab.size * 1.3) - cap_(f_lab)) / g.k})
        layout_memo[key] = blocks
        return blocks

    def block_h(b: dict[str, Any]) -> float:
        return (cap_y + 28 + cap_(b["font"]) / g.k
                + round(b["font"].size * 1.3) / g.k * (len(b["lines"]) - 1))

    def block_geometry(blocks: list[dict[str, Any]], xs: list[float], spine: float,
                       flags: tuple[float, float]):
        """Each block's top (design px) and its ink box (frame px): years and label lines (ink_box). Review 8:
        remembered per blocks, positions, spine and flags (vertical_fit measures the same geometry the layout
        then asks for wherever the flags need no change)."""
        key = (id(blocks), tuple(xs), spine, tuple(flags))
        hit = geo_memo.get(key)
        if hit is not None and hit[0] is blocks:
            return hit[1]
        if len(geo_memo) > 4000:
            geo_memo.clear()
        got = block_geometry_(blocks, xs, spine, flags)
        geo_memo[key] = (blocks, got)
        return got

    def block_geometry_(blocks: list[dict[str, Any]], xs: list[float], spine: float, flags: tuple[float, float]):
        # One baseline grid per side (round 3, 2026-11-02): every year above the spine on one baseline, every year
        # below on another, each side's flag sized for its tallest block (a 4-line label lifted its year 80 px).
        side_h = {side: max(block_h(b) for k, b in enumerate(blocks) if (k % 2 == 0) == side)
                  for side in (True, False) if any((k % 2 == 0) == side for k in range(len(blocks)))}
        out = []
        for k, (b, x) in enumerate(zip(blocks, xs)):
            above = k % 2 == 0
            top = spine - flags[0] - side_h[above] if above else spine + flags[1]
            tx = x + pad
            boxes = year_ink_boxes(X(tx), Y(top + cap_y), int(events[k]["year"]), bool(events[k].get("approximate")),
                                   f_year)
            cap_l, step = cap_(b["font"]) / g.k, round(b["font"].size * 1.3) / g.k
            n_year = len(boxes)
            boxes += [ink_box((X(tx + 2), Y(top + cap_y + 28 + cap_l + step * j)), ln, b["font"], "ls", track)
                      for j, ln in enumerate(b["lines"])]
            out.append({"top": top, "far": top if above else top + side_h[above], "above": above,
                        "box": _union([bb for bb in boxes if bb]), "years": [bb for bb in boxes[:n_year] if bb],
                        "lines": [bb for bb in boxes[n_year:] if bb]})
        return out

    def rows_reach(blocks: list[dict[str, Any]], xs: list[float], spine: float,
                   flags: tuple[float, float]) -> tuple[float, float]:
        """The top of the row above the spine and the foot of the row below (frame px), as block_geometry's boxes
        would give them, measuring only the ink that reaches them (review 8, 2026-11-03: vertical_fit measured every
        line of every block for each of WIDE_FLAG_TRIES): an upper block's years and first line, a lower block's
        years and last line (the lines between sit inside them)."""
        side_h = {side: max(block_h(b) for k, b in enumerate(blocks) if (k % 2 == 0) == side)
                  for side in (True, False) if any((k % 2 == 0) == side for k in range(len(blocks)))}
        top_px, bot_px = math.inf, -math.inf
        for k, (b, x) in enumerate(zip(blocks, xs)):
            above = k % 2 == 0
            top = spine - flags[0] - side_h[above] if above else spine + flags[1]
            tx = x + pad
            boxes = year_ink_boxes(X(tx), Y(top + cap_y), int(events[k]["year"]), bool(events[k].get("approximate")),
                                   f_year)
            cap_l, step = cap_(b["font"]) / g.k, round(b["font"].size * 1.3) / g.k
            order = range(len(b["lines"])) if above else range(len(b["lines"]) - 1, -1, -1)
            for j in order:
                bb = ink_box((X(tx + 2), Y(top + cap_y + 28 + cap_l + step * j)), b["lines"][j], b["font"], "ls",
                             track)
                if bb:
                    boxes.append(bb)
                    break
            if above:
                top_px = min(top_px, min(bb[1] for bb in boxes))
            else:
                bot_px = max(bot_px, max(bb[3] for bb in boxes))
        return top_px, bot_px

    def vertical_fit(blocks: list[dict[str, Any]], xs: list[float],
                     flags0: tuple[float, float] = (flag, flag)) -> tuple[float, tuple[float, float], bool]:
        """(spine, (flag above, flag below), fits): round 4 review 5 (2026-11-02), every block inside
        WIDE_TITLE_SAFE: a deep bottom row (a four-line label) lifts the spine, and where both rows together are
        too tall for the box the flags shorten, to WIDE_FLAG_MIN. Round 4 review 6: from `flags0`, either side's
        flag longer where a span needs the air by the spine on that side."""
        top_px, bot_px = rows_reach(blocks, xs, 540.0, flags0)
        fa, fb = flags0
        over = (bot_px - top_px) - (safe_px[3] - safe_px[1])
        if over > 0:                                  # the longer flag gives way first, then both, to the floor
            need = over / g.k + 2                     # (1 px each side of rounding, as before)
            d = min(need, abs(fa - fb), max(0.0, max(fa, fb) - WIDE_FLAG_MIN))
            if fa > fb:
                fa -= d
            else:
                fb -= d
            need -= d
            if need > 0:
                fa, fb = fa - min(need / 2, fa - WIDE_FLAG_MIN), fb - min(need / 2, fb - WIDE_FLAG_MIN)
            top_px += (flags0[0] - fa) * g.k
            bot_px -= (flags0[1] - fb) * g.k
        shift = 0.0
        if bot_px > safe_px[3]:
            shift = safe_px[3] - bot_px
        if top_px + shift < safe_px[1]:
            shift = safe_px[1] - top_px
        fits = top_px + shift >= safe_px[1] - 0.5 and bot_px + shift <= safe_px[3] + 0.5
        return 540.0 + shift / g.k, (fa, fb), fits

    def span_spot(k: int, text: str, xs: list[float], spine: float,
                  obstacles: list[tuple[tuple[float, ...], float, float]], years: list[tuple[float, ...]],
                  label_lines: list[tuple[float, ...]], earlier: tuple[bool, tuple[float, ...] | None],
                  mode: str = "segment", rows_min: int = 1):
        """Span k's (font index, lines, centre x, first baseline y, ink boxes) on its own segment, clear of every
        block and span placed (`obstacles`: each box with the air a span keeps from it at full clearance and at the
        least) and inside WIDE_TITLE_SAFE, or None. Round 4 review 5 (2026-11-02): "ABOUT 320 YEARS LATER" was set
        over "FIRST CHAMBERS CUT" where neither side of the spine was clear; the span takes, in order, the full
        WIDE_SPAN_CLEAR at its size (on one line, then stacked, centred on the segment then moved along it, the
        side the earlier block does not hang over first), then the smaller size, then the least air (1.5 x a
        block's own line gap). Round 4 review 7 (2026-11-03), never relaxed: no line level with a year (`years`:
        a stacked "LATER" beside "AD 341" read "LATER AD 341"), none on a baseline with a label line within
        WIDE_SPAN_LEVEL_NEAR (`label_lines`), never over the earlier event's year on its side of the spine
        (`earlier`: that block's side and its year's box; "ABOUT 320 YEARS LATER" sat on "c. AD 780") and never
        left of its segment's middle (it leans toward the later event). `mode` "off" (the last resort, logged)
        also tries places further from the spine, up to WIDE_SPAN_REACH."""
        prefer = k % 2 == 0                      # block k - 1 sits above the spine when k - 1 is even
        mid = (xs[k - 1] + xs[k]) / 2
        room = xs[k] - xs[k - 1] - 2 * WIDE_SPAN_OFF
        e_above, e_year = earlier
        near = S(WIDE_SPAN_LEVEL_NEAR if mode == "segment" else WIDE_SPAN_ROW_NEAR)
        offs = [WIDE_SPAN_OFF] if mode == "segment" else list(range(WIDE_SPAN_OFF, WIDE_SPAN_REACH + 1, 12))
        # the three-line break last of all, at the least air (it reads least as one span)
        # review 8 (2026-11-03): between the full air and the least, the least only beside a block, under or over
        # it (across its width) WIDE_SPAN_ACROSS x its line gap (nz27's "ABOUT 7,500 YEARS LATER" 20 px under
        # "CARVED FIRST CHAMBERS BY THE" read as its next line); the least everywhere costs one more
        # round 9 (review 7, spec G): every setting under the spine before any over it; a span over the spine
        # is the last place (counted against the layout as a compact span is: spans_above), taken only where no
        # layout keeps it under the spine without cutting a label
        # round 11 (review 9, spec G: spans stay on one row): a span takes every air on one row before any on
        # two ("337 YEARS / LATER" was stacked at the full air in a set whose other spans read on one row)
        # round 14 (review: "ABOUT 120 YEARS / LATER", "LATER" alone on its row): on two rows, the count over
        # "YEARS LATER" (2) before the break before "LATER" alone (True; a three-word span has only that one)
        # round 15 (ruling R11): two rows only in the count-over-unit form (wide_span_lines), after every one-row
        # setting; never "LATER" alone on a row
        airs = (("full", (False,)), ("across", (False,)), ("least", (False,)), ("full", (2,)),
                ("across", (2,)), ("least", (2,)))
        for sides, air, stackings in [((False,), a_, st_) for a_, st_ in airs] + [((True,), a_, st_) for a_, st_ in airs]:
            full = air == "full"
            for fi, font in enumerate(span_fonts):
                cap_g, step_g = cap_(font) / g.k, round(font.size * 1.2) / g.k
                for stacked in stackings:
                    lines = wide_span_lines(text, stacked)
                    if stacked and len(lines) < 2:
                        continue
                    if len(lines) < rows_min:
                        continue
                    w = span_w(fi, lines)
                    if w > room:
                        continue
                    reach = (room - w) / 2
                    # review 7: never left of the middle, and leaning toward the later event (WIDE_SPAN_LEAN) where
                    # the segment has the room, then further along it, then back to the middle
                    lean = min(reach, WIDE_SPAN_LEAN)
                    shifts = sorted({0.0, lean, *(min(reach, d) for d in range(12, int(reach) + 12, 12))},
                                    key=lambda d: (d < lean, abs(d - lean)))
                    rels = [span_rel_box(fi, ln) for ln in lines]
                    if not any(rels):
                        continue
                    # review 8 (2026-11-03): what depends on the height alone (the baselines, title-safe's top and
                    # foot, a year level with a line, the label lines level with one) is found once per distance
                    # off the spine and side, not again for every shift along the segment (the layout search
                    # spent most of its time here)
                    rows = {}
                    for off in offs:
                        for above in (prefer, not prefer):
                            first = spine - off - step_g * (len(lines) - 1) if above else spine + off + cap_g
                            ys = [round(Y(first + step_g * j)) for j in range(len(lines))]
                            ext = [(r[1] + y, r[3] + y) for r, y in zip(rels, ys) if r]
                            ok = (safe_px[1] <= min(a for a, _b in ext) and max(b for _a, b in ext) <= safe_px[3]
                                  and not any(a < yb[3] and yb[1] < b for a, b in ext for yb in years))
                            level = [lb for lb in label_lines if any(a < lb[3] and lb[1] < b for a, b in ext)]
                            rows[(off, above)] = (ok, ys, level)
                    # round 6: every span under the spine (spec G); round 9: over it only in the second pass
                    for above, off, dx in ((a_, o_, d_) for a_ in sides for o_ in offs for d_ in shifts):
                        cx = round(X(mid + dx))
                        ok, ys, level = rows[(off, above)]
                        if not ok:
                            continue                    # outside title-safe, or level with a year: never
                        boxes = [(r[0] + cx, r[1] + y, r[2] + cx, r[3] + y) for r, y in zip(rels, ys) if r]
                        u = _union(boxes)
                        if not (safe_px[0] <= u[0] and u[2] <= safe_px[2]):
                            continue
                        if level and any(b[1] < lb[3] and lb[1] < b[3] and box_clearance(b, lb) < near
                                         for b in boxes for lb in level):
                            continue                    # on a label line's baseline beside it: never
                        if e_year is not None and above == e_above and u[0] < e_year[2] and e_year[0] < u[2]:
                            continue                    # over the earlier event's year: never
                        if all(box_clearance(u, o) >= (n_full if full else n_least if air == "least" or not (
                                u[0] < o[2] and o[0] < u[2]) else min(n_full, across(n_least))) - 0.5
                               for o, n_full, n_least in obstacles):
                            # review 7: what the place gave up, ranked by the layout (0 at its best)
                            cost = (not full) + (air == "least") + (fi > 0) + (len(lines) == 2) + 2 * (len(lines) > 2)
                            return fi, lines, cx, ys, boxes, cost, above
        return None

    def across(n_least: float) -> float:
        # the least air under or over a block: WIDE_SPAN_ACROSS x its line gap where beside it takes
        # SPAN_MIN_SEPARATION x (n_least is that, with the ink's overshoot)
        return (n_least - SPAN_OVERSHOOT) * WIDE_SPAN_ACROSS / SPAN_MIN_SEPARATION + SPAN_OVERSHOOT

    def span_context(geo_: list[dict[str, Any]], blocks: list[dict[str, Any]]):
        """What every span keeps clear of: the blocks (with the full and the least air), the years and the label
        lines (never level with them), and per span its earlier block's side and year."""
        obstacles = [(gb["box"], S(WIDE_SPAN_CLEAR), S(SPAN_MIN_SEPARATION * b["line_gap"]) + SPAN_OVERSHOOT)
                     for gb, b in zip(geo_, blocks)]
        years = [yb for gb in geo_ for yb in gb["years"]]
        label_lines = [lb for gb in geo_ for lb in gb["lines"]]
        earlier = {k: (geo_[k - 1]["above"], _union(geo_[k - 1]["years"]) if geo_[k - 1]["years"] else None)
                   for k in range(1, len(geo_))}
        return obstacles, years, label_lines, earlier

    def spans_off(k_s: list[int], xs: list[float], spine: float, geo_: list[dict[str, Any]],
                  blocks: list[dict[str, Any]], spots: dict[int, Any]) -> dict[int, Any]:
        """The spans in `k_s` (no place by the spine) at their last resort, span_spot's "off" mode, each clear of
        the spans placed and of the ones before it: {k: spot} for those it places."""
        obstacles, years, label_lines, earlier = span_context(geo_, blocks)
        obstacles += [(_union(sp[4]), S(clear), S(clear)) for sp in spots.values()]
        out = {}
        for k in k_s:
            spot = (span_spot(k, gap_texts[k - 1], xs, spine, obstacles, years, label_lines, earlier[k], "off")
                    or span_spot(k, compact_gap(gap_texts[k - 1]), xs, spine, obstacles, years, label_lines,
                                 earlier[k], "off"))
            if spot is not None:
                out[k] = spot
                obstacles = obstacles + [(_union(spot[4]), S(clear), S(clear))]
        return out

    def compact_count(spots: dict[int, Any]) -> int:
        """The spans of `spots` set in compact_gap's wording."""
        return sum(any(" YRS " in f" {ln} " for ln in sp[1]) for sp in spots.values())

    def span_wraps(spots: dict[int, Any]) -> int:
        """Round 12: the spans of `spots` set on more rows than the fewest any of them takes (a set reads as one
        where its spans share their rows)."""
        # round 13 (coordinator directive 4, spec G): every span off one row counts, not only the ones on more
        # rows than the set's fewest (a set all on two rows, "53 YEARS / LATER", scored as clean)
        rows = [len(sp[1]) for sp in spots.values()]
        return sum(r > 1 for r in rows) if rows else 0

    def span_above(sp: Any) -> bool:
        """Whether a span's spot is over the spine (its lines' baselines above the spine's own line)."""
        return len(sp) > 6 and bool(sp[6])

    def spans_placed(xs: list[float], spine: float, geo_: list[dict[str, Any]], blocks: list[dict[str, Any]]):
        """Every span's spot (span_spot) in order, each clear of the ones before it: ({k: spot}, [k unplaced])."""
        context = span_context(geo_, blocks)

        def place(rows_min: int):
            obstacles, years, label_lines, earlier = context
            spots, missing = {}, []
            for k, text in enumerate(gap_texts, start=1):
                if not text:
                    continue
                spot = (span_spot(k, text, xs, spine, obstacles, years, label_lines, earlier[k], rows_min=rows_min)
                        or span_spot(k, compact_gap(text), xs, spine, obstacles, years, label_lines, earlier[k],
                                     rows_min=rows_min))
                if spot is None:
                    missing.append(k)
                    continue
                spots[k] = spot
                obstacles = obstacles + [(_union(spot[4]), S(clear), S(clear))]
            return spots, missing
        # round 13 (coordinator directive 4, spec G): a span that fits on one row is set on one row; round 12's
        # every-span-on-two-rows pass (a set read as one) set "53 YEARS / LATER" where one row held it. Two rows
        # only where they keep a span under the spine that one row sets over it (spec G's first rule)
        spots, missing = place(1)
        if any(map(span_above, spots.values())) or missing:
            # round 16 (review 5, ruling R11: Westminster's "34 / YEARS LATER" where one row held it): only the
            # spans one row leaves over the spine or unplaced take two rows; the others keep their one-row spots
            obstacles, years, label_lines, earlier = context
            bad = sorted([k for k, sp in spots.items() if span_above(sp)] + missing)
            mixed = {k: sp for k, sp in spots.items() if k not in bad}
            for sp in mixed.values():
                obstacles = obstacles + [(_union(sp[4]), S(clear), S(clear))]
            missing_m = []
            for k in bad:
                text = gap_texts[k - 1]
                sp2 = (span_spot(k, text, xs, spine, obstacles, years, label_lines, earlier[k], rows_min=2)
                       or span_spot(k, compact_gap(text), xs, spine, obstacles, years, label_lines, earlier[k],
                                    rows_min=2))
                if sp2 is None or span_above(sp2):
                    sp2 = spots.get(k)
                if sp2 is None:
                    missing_m.append(k)
                    continue
                mixed[k] = sp2
                obstacles = obstacles + [(_union(sp2[4]), S(clear), S(clear))]
            spots2, missing2 = place(2)
            cands_ = [(spots, missing), (mixed, missing_m), (spots2, missing2)]
            spots, missing = min(cands_, key=lambda c_: (len(c_[1]), sum(map(span_above, c_[0].values())),
                                                         span_wraps(c_[0])))
        return spots, missing

    def inside_safe(geo_: list[dict[str, Any]]) -> bool:
        return all(safe_px[0] <= gb["box"][0] and gb["box"][2] <= safe_px[2] and safe_px[1] <= gb["box"][1]
                   and gb["box"][3] <= safe_px[3] for gb in geo_)

    # Round 4 review 7 (2026-11-03): every layout is scored on, in order, every block inside title-safe on both
    # axes (a hard constraint: a layout outside it loses to any inside; nz39's widened label ran to x 1854), the
    # spans placed and the blocks apart, the labels cut to a single word ("FLOODED…"), the labels cut at all
    # (none, where any measure holds them whole), the words cut, the lone first words and what the spans gave up
    # for their places; the search stops at the first layout with none of them. A span level with a year, on a
    # near label line's baseline or over the earlier year is no longer a place at all (its gap widens instead).
    def blocks_variants(label_max: float):
        """The blocks at `label_max`, then, while any is cut or has a lone first word, with five rows, then six
        (`fit_block`), then each widened at four, five and six rows, then widened to three rows at most, then two
        (the shallower blocks leave the spine more air): only the variants that change a block (review 7: a smaller
        size, then more rows, then a wider measure, before any label is cut)."""
        seen = []
        # round 6: at most WIDE_LABEL_ROWS rows, never wider than the WIDE_LABEL_COL column
        # (round 4's ladder, more rows and wider measures, only for a label the column cuts)
        # round 7 (review 7): a label the column cuts first takes more rows of it (deep), and only where those blocks
        # cannot be laid out, the wider measure
        ladder = [(False, WIDE_LABEL_ROWS, None, True), (True, WIDE_LABEL_ROWS, WIDE_LABEL_ROWS, True),
                  (True, WIDE_LABEL_ROWS, 2, True), (True, WIDE_LABEL_ROWS, WIDE_LABEL_ROWS, False),
                  (True, WIDE_LABEL_ROWS, 2, False)]
        if label_max > WIDE_LABEL_COL:
            ladder += [(False, 4, None, True), (False, 5, None, True), (False, 6, None, True), (True, 4, None, True),
                       (True, 5, None, True), (True, 6, None, True), (True, 6, 3, True), (True, 6, 2, True)]
        # round 8 (review 7): then each with its weak rows and widows widened away (never in place of a variant
        # above: a block widened for its widow changed which combinations of the others were tried)
        ladder = [(*v, False) for v in ladder] + [(*v, True) for v in ladder]
        for widen, rows_max, rows_cap, deep, unweak in ladder:
            if unweak and not widen:
                continue
            blocks = layout_blocks(label_max, widen, rows_max, rows_cap, deep, unweak)
            sig = [(b["lines"], b["font"].size) for b in blocks]
            if sig in seen:
                continue
            seen.append(sig)
            yield blocks
            # round 12 (review: "NAMED A / WORLD / HERITAGE SITE"): a weak row asks for the variants too
            if len(seen) == 1 and not any(b["short"] or b["lone"] or b["weak"] or b["hard"] for b in blocks):
                return
    # each span's settings' widths as gap steps (its room is the step less WIDE_SPAN_OFF each side), narrowest first
    span_steps = [sorted({max(tracked_width(ln, f_, S(1)) for ln in wide_span_lines(t, st_)) / g.k
                          + 2 * WIDE_SPAN_OFF + 2 for f_ in span_fonts for st_ in (False, 2)}) if t else []
                  for t in gap_texts]
    best = None                                    # (score, layout..., the order it was found in)
    spacing_args: dict[int, tuple[Any, float, list[float]]] = {}   # id(spacing): (it, last x, needs), kept alive
    pending: list[tuple[tuple[Any, ...], int, tuple[Any, ...]]] = []
    seq = 0
    perfect = (False, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    right = float(WIDE_TITLE_SAFE[2])              # design px: a block's ink edge (review 9: ink_reach, not advance)
    # review 6: the gaps' least lengths sized for one-line spans, then stacked ones, then stacked at the floor size
    for wording, fi_w in (("LINE", 0), ("STACKED", 0), ("STACKED", len(span_fonts) - 1)):
        gap_w = [max(tracked_width(ln, span_fonts[fi_w], S(1)) for ln in wide_span_lines(t, wording == "STACKED"))
                 / g.k if t else 0.0 for t in gap_texts]
        # round 6: 420 px columns first (560 first before); the wider ones only where those cut a label
        for label_max in (WIDE_LABEL_COL, 380, 340, 300, 270, 560, 500, 460):
            for blocks in blocks_variants(label_max):
                # review 8 (2026-11-03): the labels' own costs bound every score these blocks can reach (inside,
                # apart, every span by the spine at no cost); where that bound cannot beat the best, no spacing,
                # flag or span is tried (the search took 2.9 x HEAD's time)
                typo0 = (sum(b["one_word"] for b in blocks), sum(b["short"] for b in blocks),
                         sum(b["dropped"] for b in blocks), sum(b["hard"] for b in blocks), 0,
                         sum(b["lone"] for b in blocks),
                         sum(b["wide"] for b in blocks), 0, 0, sum(b["weak"] for b in blocks))
                if best is not None and (False, 0, *typo0, 0, 0) >= best[0]:
                    continue
                base = [max(140.0, gw + 2 * gap_pad) for gw in gap_w]
                last_x = run1 - blocks[-1]["w"]
                for _grow in range(10):                 # review 5: a span with no clear spot widens its own gap
                    # review 7: each try starts from the spans' own needs (and the gaps grown for them), so the
                    # neighbours' adjustments of one try do not pile up into the next and push the run past its end
                    needs = list(base)
                    spacing = None
                    for _ in range(8):
                        spacing = timeline_positions(events, run0, last_x, needs)
                        spacing_args[id(spacing)] = (spacing, last_x, list(needs))
                        xs = spacing.positions
                        # same-side neighbours (k, k + 2) keep their 24 px
                        short_by = [(k, blocks[k]["w"] + clear - (xs[k + 2] - xs[k])) for k in range(len(xs) - 2)]
                        short_by = [(k, s_) for k, s_ in short_by if s_ > 0.5]
                        # review 7: and every block ends inside title-safe (only the last one was held in the run):
                        # the gaps after a block that runs past it lengthen, so the blocks after it move right
                        over = [(k, xs[k] + blocks[k]["w"] - right) for k in range(len(xs) - 1)]
                        over = [(k, o) for k, o in over if o > 0.5]
                        if not (short_by or over):
                            break
                        for k, s_ in short_by:
                            needs[k] += s_ / 2 + 1
                            needs[k + 1] += s_ / 2 + 1
                        for k, o in over:
                            for j in range(k, len(xs) - 1):
                                needs[j] = max(needs[j], xs[j + 1] - xs[j]) + o / (len(xs) - 1 - k) + 1
                    assert spacing is not None
                    xs = spacing.positions
                    apart = (all(xs[k + 1] - xs[k] >= needs[k] - 1 for k in range(len(xs) - 1))
                             and all(xs[k + 2] - xs[k] >= blocks[k]["w"] + clear - 1 for k in range(len(xs) - 2))
                             and all(xs[k] + blocks[k]["w"] <= right + 1 for k in range(len(xs))))
                    # review 6: where a span finds no place clear of the blocks and level with no year, the flags
                    # lengthen (while title-safe has the room) so the spine has more air either side
                    tried: list[tuple[float, float]] = []
                    fewest: list[int] | None = None        # review 7: the spans the best flags still miss
                    for flags0 in WIDE_FLAG_TRIES:
                        spine_y, flag_y, upright = vertical_fit(blocks, xs, (flag + flags0[0], flag + flags0[1]))
                        if any(abs(flag_y[0] - t[0]) < 0.5 and abs(flag_y[1] - t[1]) < 0.5 for t in tried):
                            continue                  # title-safe had no room for that: the same layout again
                        tried.append(flag_y)
                        geo_ = block_geometry(blocks, xs, spine_y, flag_y)
                        outside = not (upright and inside_safe(geo_))
                        # round 4 review 9: a layout judged outside still places its spans under every span
                        # rule (it skipped them, so a closest layout set every span unchecked)
                        spots, missing = spans_placed(xs, spine_y, geo_, blocks)
                        # review 7: then the lone first words, then what the spans gave up (the least air, the
                        # smaller size, three lines). Round 4 review 8 (2026-11-03): a span with no place by the
                        # spine gives way before a label does (qz9 stacked every label a word a line, 12 words cut,
                        # so one span could keep its place): a span its last resort places (further from the spine,
                        # every other rule kept, logged) costs less than any cut or lone word; only a span with no
                        # place at all outranks them. The last resort is searched only where it could win.
                        # round 8: a span in its compact wording ("2,221 YRS LATER", R3 wants "YEARS LATER") ranks
                        # after a cut word and before a lone, wide or weak label: taken only to keep labels whole
                        n_cmp = compact_count(spots)
                        # round 12 (review: Stonehenge's ABOUT 4,400 YEARS LATER over the spine; 68 YEARS / LATER
                        # and 339 YEARS / LATER on two rows by spans on one): a span over the spine, then a span
                        # broken onto rows where another is on one, rank as the labels' faults do, before a weak
                        # row (a label's break is its own; the spans read as one set)
                        # round 13 (directive 4): a hard break (a name split, a one-word middle row) ranks after
                        # a cut word and before every other fault: taken only where no layout avoids it (logged)
                        typo = (sum(b["one_word"] for b in blocks), sum(b["short"] for b in blocks),
                                sum(b["dropped"] for b in blocks), sum(b["hard"] for b in blocks), n_cmp,
                                sum(b["lone"] for b in blocks),
                                sum(b["wide"] for b in blocks), sum(span_above(sp) for sp in spots.values()),
                                span_wraps(spots), sum(b["weak"] for b in blocks))
                        # round 9 (spec G): a span over the spine costs more than any span's own give-way, so it
                        # is taken only where nothing else in the layout (no cut, lone, wide or weak label) is lost
                        cost = sum(sp[5] + WIDE_SPAN_ABOVE_COST * span_above(sp) for sp in spots.values())
                        seq += 1
                        if missing:
                            # review 8: its score waits on the last resort; searched after the loop, best bound
                            # first, and only while it could still win (the search's time, 2.9 x HEAD)
                            least = (outside, int(not apart), *typo, len(missing), cost)
                            if best is None or least < best[0]:
                                pending.append((least, seq, (blocks, spacing, spine_y, flag_y, geo_, spots, missing,
                                                             int(not apart), typo, cost, outside)))
                        else:
                            score = (outside, len(missing) + (not apart), *typo, 0, cost)
                            if best is None or score < best[0]:
                                best = (score, blocks, spacing, spine_y, flag_y, geo_, spots, missing, {}, seq)
                        # the spans still wanting a better place: unplaced, or placed at some cost
                        # round 9 (review 7): a span in its compact wording wants a wider gap too (spans under the
                        # spine only: Gobekli's "ABOUT 10,200 YEARS LATER" took "~10,200 YRS" rather than widen)
                        wanting = missing + [k for k, sp in spots.items()
                                             if sp[5] or any(" YRS " in f" {ln} " for ln in sp[1])
                                             or span_above(sp)]
                        # round 12 (review: Stonehenge): a span unplaced or over the spine grows its gap alone
                        # first (every wanting span grown at once ran the run past its end, and the search gave up)
                        # (then one broken onto more rows than the set's others: span_wraps)
                        urgent = missing + [k for k, sp in spots.items() if span_above(sp)]
                        if not urgent and spots:
                            fewest_rows = min(len(sp[1]) for sp in spots.values())
                            urgent = [k for k, sp in spots.items() if len(sp[1]) > fewest_rows]
                        if fewest is None or (len(missing), len(wanting)) < fewest[0]:
                            fewest = ((len(missing), len(wanting)), urgent or wanting)
                        if not wanting:
                            break
                    if fewest is None or not fewest[1] or not apart:
                        break
                    for k in fewest[1]:           # review 7: from the step it has, which may exceed its need,
                        step_k = xs[k] - xs[k - 1]    # to the next width at which one of its settings fits
                        wider = [w_ for w_ in span_steps[k - 1] if w_ > step_k + 1]
                        need_k = min(wider) if wider else step_k + 40.0
                        # round 12 (review: Stonehenge's span over the spine): under the spine, the earlier
                        # block hangs over the segment where it is below it (k - 1 odd), so the gap that holds the
                        # span there is that block's width and its air before the span's narrowest setting
                        if (k - 1) % 2 == 1 and span_steps[k - 1]:
                            need_k = max(need_k, min(blocks[k - 1]["w"] + WIDE_SPAN_CLEAR + span_steps[k - 1][0],
                                                     step_k + 160.0))
                        base[k - 1] = max(base[k - 1], need_k)
                if best is not None and best[0] == perfect:
                    break
            if best is not None and best[0] == perfect:
                break
        if best is not None and best[0] == perfect:
            break
    # the layouts that wait on their spans' last resort, best bound first (ties to the one found first): each takes
    # its exact score while it could still beat the best (the first found wins a tie, as in the loop)
    for least, seq_, (blocks, spacing, spine_y, flag_y, geo_, spots, missing, not_apart, typo, cost, outside) in sorted(
            pending, key=lambda t: (t[0], t[1])):
        if best is not None and (least, seq_) >= (best[0], best[-1]):
            break
        off = spans_off(k_s=missing, xs=spacing.positions, spine=spine_y, geo_=geo_, blocks=blocks, spots=spots)
        typo_ = (*typo[:4], typo[4] + compact_count(off), *typo[5:])
        score = (outside, len(missing) - len(off) + not_apart, *typo_, len(off), cost)
        if best is None or (score, seq_) < (best[0], best[-1]):
            best = (score, blocks, spacing, spine_y, flag_y, geo_, spots, missing, off, seq_)
    assert best is not None
    (_score, blocks, spacing, spine_y, flag_y, geo_, spots, missing, off, _seq), xs = best, best[2].positions
    # round 9: the chosen spacing derived once more from its own needs (the same positions), so the last spacing
    # the search computed is the one drawn (the wider search for the spans under the spine ends on another)
    _sp, last_x_, needs_ = spacing_args[id(spacing)]
    spacing = timeline_positions(events, run0, last_x_, needs_)
    xs = spacing.positions
    if _score[0]:
        log.error("Timeline (16:9): no layout holds the entries inside title-safe; set at the closest.")
    for k_s, sp in sorted(spots.items()):
        if len(sp[1]) > 1:      # round 13 (directive 4, spec G): said, never silent
            # round 15 (ruling R11): two rows in the count-over-unit form are clean (logged as such); any other
            # break is unclean (an ERROR, TIMELINE_UNCLEAN)
            if len(sp[1]) == 2 and sp[1][1] in ("YEARS LATER", "YRS LATER"):
                log.info("Timeline (16:9): the span %r set on two rows, %s (ruling R11: no spacing of the run "
                         "holds it on one).", gap_texts[k_s - 1], " / ".join(sp[1]))
            else:
                log.error("Timeline (16:9) %s: the span %r set as %s.", TIMELINE_UNCLEAN, gap_texts[k_s - 1],
                          " / ".join(sp[1]))
    for k_b, b in enumerate(blocks):
        b_w = max((tracked_width(ln, b["font"], track) for ln in b["lines"]), default=0) / g.k
        if b_w > WIDE_LABEL_COL + 1 and texts[k_b] in slack_set and b_w <= WIDE_LABEL_COL + WIDE_LABEL_SLACK + 1:
            log.info("Timeline (16:9): a label set wider than its %d px column (%.0f px) to keep a weak row off: %s.",
                     WIDE_LABEL_COL, b_w, " ".join(b["lines"]))
        elif b_w > WIDE_LABEL_COL + 1:
            log.info("Timeline (16:9): a label set wider than its %d px column to stay whole: %s.", WIDE_LABEL_COL,
                     " ".join(b["lines"]))
        elif len(b["lines"]) > WIDE_LABEL_ROWS:
            log.error("Timeline (16:9) copy-fit: label %r needs %d rows of its %d px column (%d wanted); set whole.",
                      " ".join(b["lines"]), len(b["lines"]), WIDE_LABEL_COL, WIDE_LABEL_ROWS)
    # review 8: each last resort names its labels, so a check can hold every other label to the rule
    def named(flag: str) -> str:
        return " | ".join(texts[k] for k, b in enumerate(blocks) if b[flag])
    if _score[3]:
        # QF-23: no measure holds them whole; the timeline is refused, never drawn cut
        raise TextFitError(named("short"), where="the widescreen timeline's label measure")
    if _score[2]:
        log.warning("Timeline (16:9): %d label(s) cut to a single word: no layout keeps two: %s.", _score[2],
                    named("one_word"))
    if _score[5]:
        # round 13 (coordinator directive 4): logged as a hard failure (the tests hold every set to none)
        log.error("Timeline (16:9) hard failure: %d label(s) split a name or leave a one-word middle row; no "
                  "size, measure or layout avoids it: %s.", _score[5], named("hard"))
    if _score[7]:
        log.warning("Timeline (16:9): %d label(s) start with a lone word: no layout breaks them better: %s.",
                    _score[7], named("lone"))
    if _score[11]:
        # round 15 (ruling R11): a short middle row of two words or more is no fault of R11's (a one-word one, or a
        # split name, is the hard failure above); said, as a warning
        log.warning("Timeline (16:9): %d label(s) keep a short middle row: no break of their rows avoids it: %s.",
                    sum(1 for b in blocks if b["weak"]), named("weak"))
    # review 7: the last resort, logged: further from the spine, with every other rule kept (never level with a
    # year or a label line, never over the earlier year, at least 1.5 x a block's line gap from it)
    for k in missing:
        if k not in off:
            log.error("Timeline (16:9): no clear place for the span %r; set on its segment.", gap_texts[k - 1])
            continue
        log.warning("Timeline (16:9): the span %r set further from the spine: no layout gives it its place by the "
                    "spine.", gap_texts[k - 1])
        spots[k] = off[k]
    ground = timeline_backdrop(photo, width, height)
    plates: list[tuple[str, Any]] = []
    entries = []
    for k, (b, x, gb) in enumerate(zip(blocks, xs, geo_)):
        f_lab = b["font"]
        cap_l = cap_(f_lab) / g.k
        step = round(f_lab.size * 1.3) / g.k
        top, tx = gb["top"], x + pad
        en = {"k": k, "x": x, "above": gb["above"], "top": top, "far": gb["far"]}
        en["year_plates"] = _year_plates(ground, X(tx), Y(top + cap_y), int(events[k]["year"]),
                                         bool(events[k].get("approximate")), f_year, GOLD)
        en["label_plates"] = [plan_text(ground, (X(tx + 2), Y(top + cap_y + 28 + cap_l + step * j)), ln, f_lab, WHITE,
                                        "ls", track=track) for j, ln in enumerate(b["lines"])]
        en["dim_year"] = [_twin(ground, pl, brand.STONE) for pl in en["year_plates"]]
        en["dim_labels"] = [_twin(ground, pl, brand.STONE_LT) for pl in en["label_plates"]]
        plates += [(f"year{k}" if j == len(en["year_plates"]) - 1 else f"year{k}_circa", pl)
                   for j, pl in enumerate(en["year_plates"])] + [(f"label{k}", pl) for pl in en["label_plates"]]
        entries.append(en)
    gaps = []
    for k, text in enumerate(gap_texts, start=1):
        if not text:
            continue
        spot = spots.get(k)
        if spot is None:      # no place at all (logged above): centred on its segment, on the earlier block's free side
            fi, lines = len(span_fonts) - 1, wide_span_lines(text, 2)
            cap_g, step_g = cap_(span_fonts[fi]) / g.k, round(span_fonts[fi].size * 1.2) / g.k
            above = False                     # round 6: under the spine (spec G)
            first = spine_y - WIDE_SPAN_OFF - step_g * (len(lines) - 1) if above else spine_y + WIDE_SPAN_OFF + cap_g
            spot = (fi, lines, round(X((xs[k - 1] + xs[k]) / 2)),
                    [round(Y(first + step_g * j)) for j in range(len(lines))], [], 9)
        fi, lines, cx, ys = spot[:4]
        if max(ys) < Y(spine_y):
            log.warning("Timeline (16:9): the span %r set over the spine: no layout keeps it under the spine "
                        "without cutting a label (spec G's last resort).", text)
        for j, (ln, y) in enumerate(zip(lines, ys)):
            plate = plan_text(ground, (cx, y), ln, span_fonts[fi], brand.STONE_LT, "ms", track=S(1))
            gaps.append((k, plate))
            plates.append((f"gap{k}" if j == 0 else f"gap{k}_{j}", plate))
    marks = [(k, (xs[k - 1] + xs[k]) / 2) for k in spacing.breaks]
    end = max(xs[-1] + blocks[-1]["w"], xs[-1] + 60)
    sched = reveal_schedule(len(entries), CLIP_SECONDS)
    last: list[Image.Image] = []
    tail = 70.0                                   # the spine runs on past the last date, fading out
    marks_nt = spine_marks((width, height), True, Y(spine_y), X(xs[0]), X(xs[-1]), max(2, round(S(6))),
                           [(X(x), S(14), bool(e.get("approximate"))) for x, e in zip(xs, events)], S(4),
                           [X(xm) for _, xm in marks], S(30), S(3))
    spine_col = spine_colour(ground, marks_nt[0][1])

    drift = GroundDrift(ground)

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = drift(t)
            d = ImageDraw.Draw(im, "RGBA")
            head = sched.head(xs, t, end)
            lw = max(2, round(S(6)))
            solid = min(head, xs[-1])
            d.line([(X(xs[0]), Y(spine_y)), (X(solid), Y(spine_y))], fill=(*spine_col, 255), width=lw)
            if head > xs[-1]:                     # the run past the last date fades into the ground
                span = head - xs[-1]
                for j in range(12):
                    a0 = xs[-1] + span * j / 12
                    a1 = xs[-1] + span * (j + 1) / 12
                    fade = 1 - (a0 - xs[-1]) / max(1.0, end - xs[-1])
                    if a1 > end - tail:
                        fade *= max(0.0, (end - a1) / tail)
                    d.line([(X(a0), Y(spine_y)), (X(a1), Y(spine_y))], fill=(*spine_col, int(255 * fade)), width=lw)
            for k, xm in marks:
                if head >= xm + 14:
                    _break_glyph(d, X(xm), Y(spine_y), True, S(30), (*brand.STONE_LT, 255), S(3),
                                 ground.getpixel((int(X(xm)), int(Y(spine_y)))))
            for en in entries:
                k = en["k"]
                a = sched.appear(k, t)
                if a <= 0:
                    continue
                back = sched.focus(k, t)
                x, r = X(en["x"]), S(14) * a * sched.pulse(k, t) * (1 - 0.3 * back)
                y_flag = spine_y + (en["far"] - spine_y) * a
                d.line([(x, Y(spine_y)), (x, Y(y_flag))], fill=(*brand.GOLD_DIM, 255), width=max(1, round(S(2))))
                _node(d, x, Y(spine_y), r, bool(events[k].get("approximate")), 1.0, S(4), brand.INK)
                current = a * (1 - _ease((t - sched.at[k + 1]) / 0.4)) if k + 1 < len(entries) else a
                if current > 0.02:                # the current event's ring
                    rr = S(14 + 10 * a)
                    d.ellipse([x - rr, Y(spine_y) - rr, x + rr, Y(spine_y) + rr], outline=(*GOLD, int(150 * current)),
                              width=max(1, round(S(3))))
                dy = round(S(14) * (1 - a)) * (-1 if en["above"] else 1)
                im = _focused(im, [*en["year_plates"], *en["label_plates"]], [*en["dim_year"], *en["dim_labels"]],
                              a, back, dy)
                d = ImageDraw.Draw(im, "RGBA")
            for k, plate in gaps:
                a = sched.appear(k, t)
                if a > 0:
                    im = draw_plate(im, plate, a)
                    d = ImageDraw.Draw(im, "RGBA")
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)
        record_nontext(out, last[0], marks_nt, drift((n - 1) / VIDEO_FPS))
    return result


# -------------------------------------------------------------------- scale
def pick_reference(feet: float) -> tuple[str, float, str] | None:
    """The reference whose size is closest (by ratio) to `feet`, if within 1/3x-3x."""
    ref = selected_reference({"value": feet, "unit": "ft"})
    return (ref["label"], ref["feet"], ref["source_url"]) if ref else None


def render_scale(fact: dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path | None:
    """The subject's size against a familiar landmark, to true proportion. 9:16 (2026-11-01 review, 5/10
    before): a depth is a shaft drilled down from a ground line with the landmark standing on its floor, a height
    a block risen beside the landmark on a shared baseline; the landmark is drawn as its silhouette, and one
    line says what the comparison means. 16:9 (2026-11-02 review round 2, 3-4.5/10: a doubled "(TOTAL) (HEIGHT)"
    label, a height drawn hanging down like a depth, a grey outside the brand): the same drawing on the left of the
    wide frame, the subject, its figure and the meaning in a column at the right (_render_scale_wide)."""
    feet = fact["value"] * (3.28084 if fact["unit"] == "m" else 1.0)
    ref = pick_reference(feet)
    if ref is None or feet <= 0:
        return None
    ref_label, ref_feet, _ = ref
    depth = fact.get("dimension") == "depth"
    if width < height:
        return _render_scale_vertical(fact, feet, ref_label, ref_feet, depth, out, width, height)
    return _render_scale_wide(fact, feet, ref_label, ref_feet, depth, out, width, height)


# Each landmark's width per px of its height, from its sourced proportions (reference_facts.json heights):
# the Statue of Liberty's Fort Wood star wall (about 110 ft across) against 305 ft; Elizabeth Tower's 12 m square
# shaft with its clock stage and corner pinnacles against 96.3 m; the Great Pyramid's 230.3 m base against its
# original 146.5 m (faces at 51.8 degrees); an adult man's arms-at-side width, about 0.27 of his height.
SILHOUETTE_WIDTH = {"liberty": 0.36, "elizabeth": 0.15, "pyramid": 1.57, "person": 0.256}


def _landmark_kind(label: str) -> str:
    low = label.lower()
    return ("liberty" if "liberty" in low else "elizabeth" if ("elizabeth" in low or "big ben" in low) else
            "pyramid" if "pyramid" in low else "person")


class _LandmarkPen:
    """Draws in a landmark's own units: x in heights from its centre line, y as the fraction of its height above
    its base (0 = ground, 1 = top), so each drawing is written once and scales to any size."""

    def __init__(self, d: ImageDraw.ImageDraw, cx: float, base_y: float, h: float, colour) -> None:
        self.d, self.cx, self.base_y, self.h = d, cx, base_y, h
        self.fill = (*colour, 255)
        shade = brand.STONE if tuple(colour) == tuple(brand.STONE_LT) else tuple(int(c * 0.82) for c in colour)
        self.shade = (*shade, 255)
        self.ink, self.gold = (*brand.INK, 255), (*brand.GOLD_DIM, 255)
        self.detail = h >= 110                      # fine lines only where they read; the outline carries it below
        self.lw = max(1, round(h * 0.0035))

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return self.cx + x * self.h, self.base_y - y * self.h

    def poly(self, pts, fill=None, outline=None) -> None:
        self.d.polygon([self.pt(x, y) for x, y in pts], fill=fill or self.fill, outline=outline)

    def rect(self, x0: float, y0: float, x1: float, y1: float, fill=None) -> None:
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], fill)

    def ellipse(self, x: float, y: float, rx: float, ry: float, fill=None, outline=None, width: int = 1) -> None:
        (a, b), (c, e) = self.pt(x - rx, y + ry), self.pt(x + rx, y - ry)
        self.d.ellipse([a, b, c, e], fill=fill, outline=outline, width=width)

    def line(self, pts, fill=None, width: int | None = None) -> None:
        if self.detail:
            self.d.line([self.pt(x, y) for x, y in pts], fill=fill or self.ink, width=width or self.lw)


def _draw_liberty(p: _LandmarkPen) -> None:
    """NPS statistics as fractions of 305 ft: foundation to 65 ft (0.21), granite pedestal to 154 ft (0.505),
    heel to head 111 ft (to 0.87), the torch to 1.0. Facing us, her raised right arm is on our left, the tablet in
    her left arm on our right."""
    # Fort Wood's star wall: a low wall whose bastion points face us, lit and shaded facets alternating.
    p.poly([(-0.18, 0), (0.18, 0), (0.17, 0.045), (-0.17, 0.045)])
    for x0 in (-0.18, -0.06, 0.06):
        p.poly([(x0 + 0.06, 0), (x0 + 0.12, 0), (x0 + 0.115, 0.045), (x0 + 0.06, 0.045)], p.shade)
    # The foundation: a stepped, truncated pyramid of concrete.
    for (y0, y1, a, b) in ((0.045, 0.105, 0.148, 0.138), (0.105, 0.16, 0.128, 0.118), (0.16, 0.213, 0.11, 0.102)):
        p.poly([(-a, y0), (a, y0), (b, y1), (-b, y1)])
        p.poly([(a * 0.55, y0), (a, y0), (b, y1), (b * 0.55, y1)], p.shade)
        p.line([(-a, y0), (a, y0)])
    # The granite pedestal: base course, dado with its door, cornice, the loggia's columns, top cornice, parapet.
    p.rect(-0.098, 0.213, 0.098, 0.25)
    p.rect(-0.084, 0.25, 0.084, 0.365)
    p.rect(-0.093, 0.365, 0.093, 0.38)
    p.rect(-0.079, 0.38, 0.079, 0.452)
    p.rect(-0.09, 0.452, 0.09, 0.47)
    p.rect(-0.074, 0.47, 0.074, 0.505)
    p.rect(0.045, 0.25, 0.084, 0.365, p.shade)
    p.rect(0.042, 0.38, 0.079, 0.452, p.shade)
    if p.detail:
        p.rect(-0.016, 0.262, 0.016, 0.33, p.ink)                       # the door
        for k in range(3):                                              # the loggia between its columns
            x = -0.05 + k * 0.042
            p.rect(x - 0.011, 0.392, x + 0.011, 0.44, p.ink)
        for yy in (0.25, 0.365, 0.38, 0.452, 0.47):
            p.line([(-0.093, yy), (0.093, yy)])
    # The figure: robe from the plinth to the shoulders, folds in gold.
    p.rect(-0.046, 0.505, 0.046, 0.516)
    p.poly([(-0.066, 0.516), (0.064, 0.516), (0.054, 0.56), (0.05, 0.64), (0.047, 0.72), (0.041, 0.785),
            (0.02, 0.8), (-0.02, 0.8), (-0.043, 0.785), (-0.046, 0.7), (-0.052, 0.6), (-0.058, 0.55)])
    p.poly([(0.022, 0.516), (0.064, 0.516), (0.054, 0.56), (0.05, 0.64), (0.047, 0.72), (0.041, 0.785),
            (0.026, 0.79), (0.03, 0.7)], p.shade)
    p.line([(-0.04, 0.785), (0.04, 0.64)], p.gold)                                        # the stola's hem
    for x0, x1 in ((-0.044, -0.03), (-0.018, -0.012), (0.006, 0.006)):                    # the robe's folds
        p.line([(x0, 0.522), (x1, 0.66)], p.gold)
    # The tablet, held edge-on against her left side and tilted outward.
    p.poly([(0.03, 0.655), (0.056, 0.652), (0.078, 0.745), (0.054, 0.752)])
    p.line([(0.03, 0.655), (0.054, 0.752)])
    p.poly([(0.02, 0.72), (0.06, 0.70), (0.064, 0.715), (0.024, 0.735)])                     # her forearm across
    # Neck, head and the crown's seven rays.
    p.rect(-0.011, 0.795, 0.011, 0.815)
    p.ellipse(0.0, 0.838, 0.019, 0.03, fill=p.fill)
    p.rect(-0.021, 0.848, 0.021, 0.857)
    for k in range(7):
        a = math.radians(-72 + k * 24)
        bx, by = 0.017 * math.sin(a), 0.853 + 0.006 * math.cos(a)
        tx, ty = bx + 0.036 * math.sin(a), by + 0.036 * math.cos(a)
        nx, ny = 0.0045 * math.cos(a), -0.0045 * math.sin(a)
        p.poly([(bx - nx, by - ny), (bx + nx, by + ny), (tx, ty)])
    # The raised right arm, its sleeve falling from it, and the torch with its flame.
    p.poly([(-0.042, 0.775), (-0.02, 0.79), (-0.048, 0.932), (-0.07, 0.928)])
    p.poly([(-0.044, 0.776), (-0.07, 0.862), (-0.064, 0.89), (-0.048, 0.83)])                # the sleeve
    p.poly([(-0.062, 0.928), (-0.054, 0.928), (-0.058, 0.955), (-0.068, 0.955)])
    p.poly([(-0.084, 0.955), (-0.042, 0.955), (-0.048, 0.968), (-0.078, 0.968)])
    p.poly([(-0.075, 0.968), (-0.051, 0.968), (-0.052, 0.982), (-0.06, 1.0), (-0.071, 0.984)])
    p.line([(-0.075, 0.968), (-0.071, 0.984), (-0.06, 1.0), (-0.052, 0.982), (-0.051, 0.968)], p.gold)


def _draw_elizabeth(p: _LandmarkPen) -> None:
    """Parliament's 96.3 m: a 12 m square shaft of Perpendicular panelling, the clock stage (dials 7 m across,
    their centres about 55 m up), the belfry's arcade, the iron roof and lantern, the spire and its finial."""
    p.rect(-0.066, 0.0, 0.066, 0.03)
    p.rect(-0.0623, 0.03, 0.0623, 0.53)
    p.rect(0.03, 0.03, 0.0623, 0.53, p.shade)
    if p.detail:
        for x in (-0.045, -0.015, 0.015, 0.045):                         # the shaft's vertical panels
            p.line([(x, 0.05), (x, 0.52)], p.gold)
        for k in range(1, 5):                                            # its string courses
            yy = 0.03 + k * 0.1
            p.line([(-0.0623, yy), (0.0623, yy)], p.ink)
        for x in (-0.03, 0.0, 0.03):                                     # lancet windows in the upper stages
            for y0 in (0.36, 0.42, 0.475):
                p.rect(x - 0.005, y0, x + 0.005, y0 + 0.035, p.ink)
    # The clock stage, a little wider, with its dial, gable and corner pinnacles.
    p.rect(-0.069, 0.53, 0.069, 0.618)
    p.rect(0.034, 0.53, 0.069, 0.618, p.shade)
    p.ellipse(0.0, 0.571, 0.036, 0.036, fill=(*brand.BONE, 255), outline=p.ink, width=max(1, p.lw))
    if p.detail:
        p.line([(0.0, 0.571), (0.0, 0.597)], width=max(1, p.lw))
        p.line([(0.0, 0.571), (0.018, 0.56)], width=max(1, p.lw))
    p.poly([(-0.05, 0.618), (0.05, 0.618), (0.0, 0.652)])
    for s in (-1, 1):
        p.poly([(s * 0.069, 0.618), (s * 0.057, 0.618), (s * 0.063, 0.668)])
    # The belfry and its arcade.
    p.rect(-0.058, 0.618, 0.058, 0.70)
    p.rect(0.03, 0.618, 0.058, 0.70, p.shade)
    p.poly([(-0.05, 0.618), (0.05, 0.618), (0.0, 0.652)])
    for x in (-0.03, 0.0, 0.03):
        p.poly([(x - 0.009, 0.632), (x + 0.009, 0.632), (x + 0.009, 0.676), (x, 0.688), (x - 0.009, 0.676)], p.ink)
    p.rect(-0.066, 0.70, 0.066, 0.714)
    for s in (-1, 1):
        p.poly([(s * 0.074, 0.714), (s * 0.058, 0.714), (s * 0.066, 0.78)])
    # The iron roof, lantern, spire and finial.
    p.poly([(-0.056, 0.714), (0.056, 0.714), (0.031, 0.84), (-0.031, 0.84)])
    p.poly([(0.018, 0.714), (0.056, 0.714), (0.031, 0.84), (0.01, 0.84)], p.shade)
    p.rect(-0.025, 0.84, 0.025, 0.882)
    if p.detail:
        p.rect(-0.012, 0.848, 0.012, 0.874, p.ink)
    p.poly([(-0.021, 0.882), (0.021, 0.882), (0.0, 0.985)])
    p.poly([(0.006, 0.882), (0.021, 0.882), (0.0, 0.985)], p.shade)
    p.rect(-0.003, 0.975, 0.003, 1.0)


def _draw_pyramid(p: _LandmarkPen) -> None:
    """Base 1.57 times its original height (faces at 51.8 degrees), seen a little off its axis: a lit face and a
    shaded one, coursing on the lit face, and the original apex with its pyramidion."""
    half = 1.57 / 2
    split = 0.16
    p.poly([(-half, 0), (split, 0), (0, 1.0)])
    p.poly([(split, 0), (half, 0), (0, 1.0)], p.shade)
    if p.detail:
        for k in range(1, 12):
            f = k / 12
            p.line([(-half * (1 - f), f), (split * (1 - f), f)], p.shade)
        p.line([(-half * 0.035, 0.965), (split * 0.035, 0.965)], p.gold, width=max(1, p.lw))
    p.line([(split, 0), (0, 1.0)], p.gold, width=max(1, p.lw))


def _draw_person(p: _LandmarkPen) -> None:
    """An adult standing, arms at his sides: head an eighth of his height, shoulders about a quarter wide, hands
    at mid-thigh, legs a little under half his height."""
    p.ellipse(0.0, 0.94, 0.042, 0.06, fill=p.fill)
    p.rect(-0.019, 0.85, 0.019, 0.89)
    p.poly([(-0.112, 0.812), (-0.1, 0.835), (-0.05, 0.858), (0.05, 0.858), (0.1, 0.835), (0.112, 0.812),
            (0.086, 0.76), (0.074, 0.62), (0.088, 0.5), (0.088, 0.47), (-0.088, 0.47), (-0.088, 0.5),
            (-0.074, 0.62), (-0.086, 0.76)])
    for s in (-1, 1):
        p.poly([(s * 0.098, 0.838), (s * 0.122, 0.81), (s * 0.128, 0.74), (s * 0.126, 0.6), (s * 0.128, 0.455),
                (s * 0.106, 0.455), (s * 0.104, 0.6), (s * 0.094, 0.74), (s * 0.088, 0.79)])        # arm
        p.ellipse(s * 0.114, 0.43, 0.014, 0.03, fill=p.fill)                                       # hand
        p.poly([(s * 0.088, 0.5), (s * 0.002, 0.5), (s * 0.002, 0.46), (s * 0.012, 0.27), (s * 0.016, 0.035),
                (s * 0.052, 0.035), (s * 0.06, 0.27), (s * 0.084, 0.42)])                         # leg
        p.poly([(s * 0.012, 0.0), (s * 0.074, 0.0), (s * 0.07, 0.022), (s * 0.052, 0.04), (s * 0.016, 0.04)])  # foot


_LANDMARK_DRAWINGS = {"liberty": _draw_liberty, "elizabeth": _draw_elizabeth, "pyramid": _draw_pyramid,
                      "person": _draw_person}


def _silhouette(d: ImageDraw.ImageDraw, label: str, cx: float, base_y: float, h: float, colour) -> float:
    """The reference landmark as a flat illustration (2026-11-02 rebuild: the old outlines read as a lamp post, a
    chimney and a wedge), `h` px tall standing on `base_y`, in one fill with a shaded side and INK / GOLD_DIM
    detail lines: the Statue of Liberty (star wall, stepped foundation, pedestal, robed figure, crown, tablet and
    raised torch), Elizabeth Tower (panelled shaft, clock, belfry, roof, spire), the Great Pyramid (lit and shaded
    faces, original apex), else an adult standing. Its height is the landmark's true height on the scale and its
    width _silhouette_width(label) * h; returns that width."""
    kind = _landmark_kind(label)
    if h >= 2:
        _LANDMARK_DRAWINGS[kind](_LandmarkPen(d, cx, base_y, h, colour))
    return h * SILHOUETTE_WIDTH[kind]


EARTH, EARTH_LINE = (38, 31, 24), (52, 43, 33)   # the ground in section: dark warm earth, its strata a step up


def _silhouette_width(label: str) -> float:
    """A landmark silhouette's width per px of its height (as _silhouette draws it)."""
    return SILHOUETTE_WIDTH[_landmark_kind(label)]


SCALE_TEXT_GAP = 44   # px of clear air between a 9:16 scale drawing and its words (2026-11-02)
SECTION_FOOT = 16     # px of ground drawn under a depth shaft's floor (it holds the floor's ruler figure)
SCALE_NOTE_LIFT = 14  # px from a depth's ground line up to its bracket note's baseline (ring clear of the line)


def heading_clear(heads: list[tuple[float, float, float, float]], xa: float, xb: float, top: float,
                  gap: float = SCALE_TEXT_GAP) -> float:
    """The highest y a drawing that spans xa..xb may reach under a 9:16 scale heading: SCALE_TEXT_GAP under the
    lowest heading line within SCALE_TEXT_GAP of its columns, so the air holds sideways too (round 5 review,
    2026-11-02: holding the whole drawing under the short metric line "85 M" took 20 % off the well and its
    landmark, which sit under the feet figure only)."""
    return max((b[3] for b in heads if b[0] < xb + gap and b[2] > xa - gap), default=top - gap) + gap


def scale_figure(fact: dict[str, Any], feet: float, depth: bool) -> tuple[str, str]:
    """The scale graphic's figure in its two parts (round 3, 2026-11-02): the hedged feet and the dimension in
    Anton ("ABOUT 280 FT DEEP") and the metric figure on its own, set small in the data face ("85 M"); the same
    figures as measurement_text, never more precise."""
    hedge = "ABOUT " if fact.get("approximate", True) else ""
    text = measurement_text(feet)
    main, _, metric = text.partition(" (")
    return hedge + main + (" DEEP" if depth else " TALL"), metric.rstrip(")")


def _short(feet: float) -> str:
    return measurement_text(feet).split(" (")[0]


def comparison_ratio(big: float, small: float) -> str:
    """How many times one sourced figure is the other, never stated more confidently than the figures: one decimal
    below 2 ("1.5", which the old f"{ratio:.0f}" wrote as 2), else the whole number rounded half up (2.5 -> 3)."""
    ratio = big / small
    if ratio < 2:
        return f"{math.floor(ratio * 10 + 0.5) / 10:g}"
    return str(math.floor(ratio + 0.5))


def scale_meaning(fact: dict[str, Any], feet: float, ref_label: str, ref_feet: float, depth: bool) -> list[str]:
    """The line of meaning's words in capitals (round 4, 2026-11-02: the drawn lines are scale_meaning_lines, in
    sentence case; this is the same text, for the checks that read it in capitals)."""
    return [ln.upper() for ln in scale_meaning_lines(fact, feet, ref_label, ref_feet, depth)]


SCALE_MEANING_PX = (36, 34)   # the 9:16 line of meaning's size and floor (round 3: it was the smallest text, 30-22)
SCALE_RULER_PX = 34           # the rulers' data-face figures on 9:16 (round 4: 26 before, an 18 px cap)
SCALE_SETTLE = 2.6            # s: the landmark lands (a depth) or finishes rising (a height)
SCALE_MEANING_AT = 2.75       # round 4: the line of meaning on the impact, not 0.6 s after it (3.2 before)
SCALE_HOLD_PUSH = 0.025       # round 4: the drawing pushes in 1.0 -> 1.025 about its tip over the hold
SCALE_HOLD_FROM = 3.0         # s: the hold's push starts once the meaning is in


def _scale_case_name(fact: dict[str, Any]) -> str:
    """The payload subject in its own case, without a trailing dimension word ("Kailasa Temple height" ->
    "Kailasa Temple"), for the sentence-case line of meaning."""
    words = str(fact.get("subject") or "").split()
    while words and words[-1].upper() in ("HEIGHT", "DEPTH", "TALL", "DEEP"):
        words.pop()
    return " ".join(words)


def _ft(feet: float) -> str:
    """A sourced figure as a sentence writes it ("305 ft", "5 ft 9 in")."""
    return _short(feet).replace(" FT", " ft").replace(" IN", " in")


def _landmark_name(ref_label: str) -> str:
    """The landmark's name for a sentence: the label without its bracket, with "the" for the named monuments."""
    name = ref_label.split(" (")[0]
    return f"the {name}" if _landmark_kind(ref_label) in ("liberty", "pyramid") else name


def _cap_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def scale_meaning_lines(fact: dict[str, Any], feet: float, ref_label: str, ref_feet: float, depth: bool) -> list[str]:
    """The line of meaning as drawn (round 4, 2026-11-02): in sentence case (three lines of tracked capitals were
    the slowest text on the card) and, for a height, with the subject as the grammatical subject ("Kailasa Temple:
    about 107 ft." / "The Statue of Liberty (305 ft) is about 3 times as tall."; the landmark taller cast the
    subject as the loser before). The same figures as scale_meaning (the subject's hedged figure, the landmark's
    sourced one, the ratio from comparison_ratio, no more precise) and only the payload's and the reference
    file's words, with the connectives the comparison needs."""
    hedge = "about " if fact.get("approximate", True) else ""
    ref = _landmark_name(ref_label)
    gap = abs(ref_feet - feet)
    if depth:
        return ([f"{_cap_first(ref)}, {_ft(ref_feet)},", f"would show only its top {hedge}{_ft(gap)}."]
                if ref_feet > feet else
                [f"{_cap_first(ref)} would vanish:", f"its top {hedge}{_ft(gap)} below the surface."])
    subject = _scale_case_name(fact)
    near = max(feet, ref_feet) / min(feet, ref_feet) < 1.1
    ratio = comparison_ratio(max(feet, ref_feet), min(feet, ref_feet))
    if not subject:
        return [f"{_cap_first(ref)}: {_ft(ref_feet)}.", "Nearly the same height." if near else f"About {ratio} times as tall."]
    first = f"{subject}: {hedge}{_ft(feet)}"
    if near:
        return [first + ".", f"{_cap_first(ref)}, {_ft(ref_feet)}, is nearly the same height."]
    if ref_feet > feet:
        return [first + ".", f"{_cap_first(ref)}, {_ft(ref_feet)}, is about {ratio} times as tall."]
    return [first + ",", f"about {ratio} times as tall as {ref}, {_ft(ref_feet)}."]


def height_ruler(top_ft: float) -> list[tuple[float, str]]:
    """The height view's shared ruler (round 4): "0 FT" on the baseline and the round steps up to the taller of the
    two (hundreds from 200 ft, fifties from 40, tens below), reading up, so the ratio reads without the caption."""
    step = 100 if top_ft >= 200 else 50 if top_ft >= 40 else 10
    marks = [(0.0, "0 FT")]
    v = step
    while v <= top_ft + 1e-6:
        marks.append((float(v), f"{v:,}"))
        v += step
    return marks


def _push_about(im: Image.Image, scale: float, centre: tuple[float, float], fill=None) -> Image.Image:
    """`im` scaled by `scale` about `centre`, back on a frame of its size (the hold's push on a drawing); below 1
    the uncovered edge takes `fill` (the plain ground). Through the shared camera (QF-22): never rounded to a pixel."""
    return kenburns(im, zoom=scale, centre=centre, fill=fill if fill is not None else im.getpixel((0, 0)))


def hold_push(t: float) -> float:
    """The scale drawing's push at time `t`: SCALE_HOLD_PUSH over the hold from SCALE_HOLD_FROM, ending at 1.0, so
    the held last frame is the planned layout exactly (its air to the words, its safe edges) and the drawing
    reaches it by pushing in rather than past it."""
    return (1.0 + SCALE_HOLD_PUSH * _ease_io((t - SCALE_HOLD_FROM) / (CLIP_SECONDS - SCALE_HOLD_FROM))) / (
        1.0 + SCALE_HOLD_PUSH)


def _monolith(d: ImageDraw.ImageDraw, x0: float, x1: float, base_y: float, h: float) -> None:
    """The subject of a height comparison as a drawn monolith (round 4, 2026-11-02: a flat gold rectangle beside the
    detailed landmark read as a placeholder and a bar chart): a generic rock-cut mass, never a topic's own profile
    (no sourced silhouette exists), `h` px tall from `base_y`, in the landmarks' flat-illustration finish: a lit GOLD
    face and a GOLD_DIM shadow plane on its right, coursing and staggered joints in INK, and a stepped top whose
    highest tier ends exactly at the figure's height."""
    if h < 2:
        return
    w = x1 - x0
    s = max(2.0, min(w * 0.09, h * 0.05))
    tiers = [(x0, x1, base_y - h * 0.9, base_y), (x0 + s, x1 - s, base_y - h * 0.96, base_y - h * 0.9),
             (x0 + 2 * s, x1 - 2 * s, base_y - h, base_y - h * 0.96)]
    lit, shade, ink = (*brand.GOLD, 255), (*brand.GOLD_DIM, 255), (*brand.INK, 150)
    for a, b, top, bot in tiers:
        if bot - top < 0.5:
            continue
        split = b - (b - a) * 0.28
        d.rectangle([a, top, b, bot], fill=lit)
        d.rectangle([split, top, b, bot], fill=shade)
    if h >= 40:            # a carved mass, not masonry: a plinth, string courses and recessed bays
        lw = max(1, round(w / 110))
        body_top = base_y - h * 0.9
        plinth = base_y - h * 0.1
        d.rectangle([x0, plinth, x1 - (x1 - x0) * 0.28, plinth + max(2, lw * 2)], fill=shade)   # the plinth's edge
        course = body_top + (plinth - body_top) * 0.42                                        # a string course
        d.line([(x0, course), (x1, course)], fill=ink, width=lw)
        d.line([(x0, body_top + lw), (x1, body_top + lw)], fill=ink, width=lw)                  # the cornice
        lit_w = (x1 - x0) * 0.72
        for k in range(4):                                         # carved pilasters: tall recesses on the lit face
            bx0 = x0 + lit_w * (0.1 + 0.235 * k)
            bx1 = bx0 + max(1.0, lit_w * 0.06)
            for ya, yb in ((body_top + 3 * lw, course - 2 * lw), (course + 2 * lw, plinth - 2 * lw)):
                if yb > ya:
                    d.rectangle([bx0, ya, bx1, yb], fill=shade)


def _depth_ruler(feet: float, hedge: str) -> list[tuple[float, str, bool]]:
    """The depth ruler's marks (round 3, 2026-11-02; it replaces five unlabelled strata that suggested levels
    that do not exist): (feet, label, is the floor) for "0 FT" at the ground, the round hundreds above the floor
    (fifties under 200 ft, tens under 40), none within a fifth of a step of it, and the payload figure, hedged
    like the figure, at the floor."""
    step = 100 if feet >= 200 else 50 if feet >= 40 else 10
    marks = [(0.0, "0 FT", False)]
    v = step
    while v < feet - step * 0.2:
        marks.append((float(v), f"{v:,}", False))
        v += step
    marks.append((feet, f"{hedge}{_short(feet)}", True))
    return marks


def _bracket_x(ref_cx: float, ref_w: float, gap: float, ref_feet: float, feet: float, shaft_x1: float) -> float:
    """Where a depth's bracket stands: just right of the landmark's part it measures (its top, narrower than its
    base: about the base's half-width times the share of its height that is measured), inside the shaft."""
    reach = ref_w / 2 * min(1.0, 1.5 * gap / max(ref_feet, 1.0)) if ref_feet > feet else ref_w / 2 * 0.5
    return min(ref_cx + max(14.0, reach) + 16, shaft_x1 - 12)


def _bracket(d: ImageDraw.ImageDraw, x: float, y0: float, y1: float, tick: float, width: int, fill) -> None:
    """A dimension bracket: a vertical line from y0 to y1 with a tick at each end, pointing left."""
    d.line([(x, y0), (x, y1)], fill=fill, width=width)
    for yy in (y0, y1):
        d.line([(x - tick, yy), (x, yy)], fill=fill, width=width)


def _render_scale_vertical(fact, feet, ref_label, ref_feet, depth, out, width, height):
    """9:16 scale (see render_scale), inside the Reel text box: the subject and its figure above (the metric figure
    on its own line under it in the data face, as on 16:9: set beside "DEEP" it read as a tag, "DEEP 85 M"), the
    drawing, then the meaning. Words and drawing start on the shared inset
    edge (INSET_X0), the drawing ends at INSET_X1 and the words may run on to the Reel box's right edge. Round 3
    (2026-11-02): a depth carries a data-face ruler on the shaft's left wall and a gold bracket giving the
    landmark's part above (or below) the surface; a height labels the landmark's figure at its top; the meaning
    from scale_meaning at 34-36 px."""
    _bx0, top, box_x1, bottom = get_text_box(width, height)
    x0, x1 = inset_x0(width, height), inset_x1(width, height)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    hedge = "ABOUT " if fact.get("approximate", True) else ""
    subject = str(fact.get("subject") or "").upper()
    figure, metric = scale_figure(fact, feet, depth)
    gap = abs(ref_feet - feet)
    meaning = scale_meaning_lines(fact, feet, ref_label, ref_feet, depth)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    wide = box_x1 - x0 - 8
    f_sub = fit_font(probe, subject, "Montserrat", 30, wide, 22)
    f_sub = get_font("Montserrat", f_sub.size, weight=800)
    f_met = get_font(brand.DATA_FONT, 34, weight=600)
    f_fig = fit_font(probe, figure, "Anton", 76, int(wide), 48)
    mean_lines, f_mean = [], None
    for px in range(SCALE_MEANING_PX[0], SCALE_MEANING_PX[1] - 1, -1):   # round 4: SemiBold, sentence case
        f_mean = get_font("Montserrat", px, weight=600)
        mean_lines = [ln for text in meaning for ln in fit_text(probe, text, "Montserrat", px, int(wide), 3,
                                                                px, sentences=False, weight=600, quiet=True)[0]]
        if len(mean_lines) <= len(meaning) + 1:
            break
    mean_step = int(cap_height(f_mean) * 1.75)
    y_sub = top + 10 + cap_height(f_sub)
    y_fig = y_sub + 24 + cap_height(f_fig)
    y_met = y_fig + 16 + cap_height(f_met) if metric else y_fig
    # The meaning's last baseline on the box's foot (it sat a whole line step, ~15 px, above it), so the drawing
    # keeps its height (round 5 review, 2026-11-02); 4 px for a comma or Q tail.
    # round 4: sentence case has descenders; the last line's own ink ends on the foot
    desc = max(4, probe.textbbox((0, 0), mean_lines[-1], font=f_mean, anchor="ls")[3]) if mean_lines else 4
    y_mean0 = bottom - desc - (len(mean_lines) - 1) * mean_step
    mean_top = y_mean0 - cap_height(f_mean)
    # Clear air between the drawing and the line of meaning (a depth's ground section under the shaft floor,
    # SECTION_FOOT, counts as drawing; a height's ground line is 3 px), and between the heading and anything
    # rising above the ground (operator, 2026-11-02: they touched).
    draw_bottom = mean_top - SCALE_TEXT_GAP - (SECTION_FOOT if depth else 3)
    ground = Image.new("RGB", (width, height), SEA)
    xt = x0                                                # on the inset edge, well inside the box
    plates = [("subject", plan_text(ground, (xt, y_sub), subject, f_sub, MUTED, "ls", track=3)),
              ("figure", plan_text(ground, (xt, y_fig), figure, f_fig, GOLD, "ls"))]
    if metric:
        plates.append(("figure_metric", plan_text(ground, (xt, y_met), metric, f_met, MUTED, "ls", track=1)))
    plates += [(f"meaning{k}", plan_text(ground, (xt, y_mean0 + k * mean_step), t, f_mean, WHITE, "ls"))
               for k, t in enumerate(mean_lines)]
    heads = [pl.box for name, pl in plates if name in ("subject", "figure", "figure_metric")]
    ref_grey = brand.STONE_LT
    width_per_px = _silhouette_width(ref_label)            # the landmark's width per px of its height
    f_note = get_font("Montserrat", 28, weight=800)
    f_rule = get_font(brand.DATA_FONT, SCALE_RULER_PX, weight=600)
    rulers: list[tuple[float, Any]] = []
    bracket = None                                          # (x, y_top, y_bottom)
    if depth:
        # The landmark stands on the shaft's floor, inside it: room above the ground for any part taller than
        # the depth, and a shaft wide enough to hold it.
        # Everything above the ground keeps SCALE_TEXT_GAP of air under the heading lines it shares columns with
        # (heading_clear): the ground line spans the drawing, so it clears the metric line; the landmark's top
        # and the bracket's note, right of the short "85 M", clear the feet figure (round 3 review: the note sat
        # 10 px under the figure's baseline; round 5 review: clearing the whole heading cost the well 20 %).
        above = max(0.0, ref_feet - feet)
        cx = (x0 + x1) / 2 + 30                            # a little right of centre: the ruler takes the left
        note = f"{hedge}{_short(gap)}"
        note_w = tracked_width(note, f_note, 2)
        floor_y = draw_bottom
        c_line = heading_clear(heads, x0, x1, top) + (2 if ref_feet > feet else 26)   # the 3 px line's top
        px_ft = min((floor_y - c_line) / max(feet, 1.0), (x1 - x0 - 120) / max(1.0, ref_feet * width_per_px))
        for _ in range(3):           # the landmark's and the note's columns depend on the scale: settle them
            ref_w = ref_feet * px_ft * width_per_px
            c_top = heading_clear(heads, cx - ref_w / 2, cx + ref_w / 2, top)
            cand = min((floor_y - c_line) / max(feet, 1.0), (floor_y - c_top) / max(1.0, feet + above),
                       (x1 - x0 - 120) / max(1.0, ref_feet * width_per_px))
            if ref_feet > feet:      # the note's top, SCALE_NOTE_LIFT + its cap height over the ground line
                nx = _bracket_x(cx, ref_w, gap, ref_feet, feet, cx + max(ref_w / 2 + 50, 150)) + 14
                c_note = heading_clear(heads, nx, nx + note_w, top)
                cand = min(cand, (floor_y - c_note - SCALE_NOTE_LIFT - cap_height(f_note)) / max(feet, 1.0))
            px_ft = cand
        line_y = floor_y - feet * px_ft
        ref_w = ref_feet * px_ft * width_per_px
        half = max(ref_w / 2 + 50, 150)
        shaft = (cx - half, cx + half)
        ref_cx = cx
        top_y = floor_y - ref_feet * px_ft
        # The bracket beside the landmark: its part above the ground, or (it would vanish) from its top up to
        # the ground; labelled with the gap, hedged like the meaning.
        bx = _bracket_x(ref_cx, ref_w, gap, ref_feet, feet, shaft[1])
        bracket = (bx, min(top_y, line_y), max(top_y, line_y))
        note_x = bx + 14 if ref_feet > feet else shaft[1] + 18
        if note_x + note_w > box_x1:
            note_x = None
        note_y = (bracket[1] + bracket[2]) / 2 + cap_height(f_note) / 2
        if ref_feet > feet:          # above the ground line, never across it
            note_y = min(note_y, line_y - SCALE_NOTE_LIFT)   # its measured ring clear of the bone line
        if note_x is not None and bracket[2] - bracket[1] >= 6:
            plates.append(("bracket", plan_text(ground, (note_x, note_y), note, f_note, GOLD, "ls", track=2)))
        for ft, text, is_floor in _depth_ruler(feet, hedge):
            yy = line_y + ft * px_ft
            rx = shaft[0] - 26
            f_rule = get_font(brand.DATA_FONT, SCALE_RULER_PX, weight=600)
            while is_floor and rx - tracked_width(text, f_rule, 1) < x0 and f_rule.size > 26:
                f_rule = get_font(brand.DATA_FONT, f_rule.size - 2, weight=600)   # the hedged floor, never cut
            if rx - tracked_width(text, f_rule, 1) < x0:
                continue
            # "0 FT" just under the ground line, its 10 px measured ring clear of the 3 px bone line (the review
            # fix moved the line by a few px and a cap-relative offset let the ring reach it: 1.8:1)
            yb = yy + (16 + cap_height(f_rule) if ft == 0 else cap_height(f_rule) * 0.5)
            plate = plan_text(ground, (rx, yb), text, f_rule, GOLD if is_floor else MUTED, "rs", track=1)
            if any(LayoutConstraintSolver.boxes_intersect(plate.box, q.box, 6) for _, q in rulers):
                continue
            rulers.append((yy, plate))
        plates += [(f"ruler{j}", pl) for j, (_, pl) in enumerate(rulers)]
    else:
        base_y = draw_bottom
        block = 170
        # round 4: the shared ruler (height_ruler) on the baseline's left edge, its figures right-aligned to a
        # line RULE_W in from the inset edge; the monolith and the landmark beside it
        f_rule_h = get_font(brand.DATA_FONT, SCALE_RULER_PX, weight=600)
        rule_x = x0 + round(max(tracked_width(t, f_rule_h, 1) for _f, t in height_ruler(max(feet, ref_feet)))) + 16
        shaft = (rule_x + 30, rule_x + 30 + block)
        room = x1 - shaft[1] - 40 - 20                      # beside the subject's block
        f_blk = get_font("Montserrat", 28, weight=800)
        blk_text = hedge + _short(feet)                    # hedged like the figure: never more precise
        ref_txt_w = tracked_width(_short(ref_feet), f_blk, 2)
        # Each part keeps SCALE_TEXT_GAP under the heading lines over its own columns (heading_clear; round 5
        # review, 2026-11-02: a flat 40 px reserve under the metric line took 13 % off the landmark): the block's
        # label, the landmark's top and its figure's label.
        c_blk = heading_clear(heads, shaft[0] - 30, shaft[1] + 30, top) + 16 + cap_height(f_blk)
        c_rule = heading_clear(heads, x0, rule_x, top) + cap_height(f_rule_h) / 2 + 2
        px_ft = min((base_y - c_blk) / max(feet, 1.0), room / max(1.0, ref_feet * width_per_px))
        for _ in range(3):
            ref_w = ref_feet * px_ft * width_per_px
            rcx = shaft[1] + 40 + max(room, ref_w) / 2
            c_ref = heading_clear(heads, rcx - ref_w / 2, rcx + ref_w / 2, top)
            lab_x = rcx + max(0.05 * ref_feet * px_ft, 6) + 34
            c_lab = heading_clear(heads, lab_x, lab_x + ref_txt_w, top) + cap_height(f_blk) / 2 + 2
            px_ft = min((base_y - c_blk) / max(feet, 1.0), (base_y - max(c_ref, c_lab)) / max(1.0, ref_feet),
                        room / max(1.0, ref_feet * width_per_px))
        ref_cx = shaft[1] + 40 + max(room, ref_feet * px_ft * width_per_px) / 2
        for j, (ft, text) in enumerate(height_ruler(max(feet, ref_feet))):
            yy = base_y - ft * px_ft
            yb = yy - 12 if ft == 0 else yy + cap_height(f_rule_h) / 2     # "0 FT" sits on the ground line
            plate = plan_text(ground, (rule_x - 14, yb), text, f_rule_h, MUTED, "rs", track=1)
            if (plate.box[0] >= x0 - 2 and plate.box[1] >= c_rule - cap_height(f_rule_h)   # clear of the heading
                    and not any(LayoutConstraintSolver.boxes_intersect(plate.box, q.box, 6) for _, q in rulers)):
                rulers.append((yy, plate))
        plates += [(f"ruler{j}", pl) for j, (_, pl) in enumerate(rulers)]
        # The subject's block is labelled with its own figure, so the gold block reads as the subject at a
        # glance (round-2 panel, 2026-11-02); the same words as the figure above, never new ones.
        plates.append(("block_label", plan_text(ground, ((shaft[0] + shaft[1]) / 2, base_y - feet * px_ft - 16),
                                                blk_text, f_blk, GOLD, "ms", track=2)))
        # and the landmark's figure at its top, on a hairline tick (round 3)
        ref_top = base_y - ref_feet * px_ft
        tick_x = ref_cx + max(0.05 * ref_feet * px_ft, 6)        # just right of the landmark's top
        ref_note = plan_text(ground, (tick_x + 34, ref_top + cap_height(f_blk) / 2), _short(ref_feet), f_blk, MUTED,
                             "ls", track=2)
        if ref_note.box[2] <= box_x1 and not LayoutConstraintSolver.boxes_intersect(ref_note.box, plates[-1][1].box, 8):
            plates.append(("ref_label", ref_note))
            bracket = (tick_x, ref_top, ref_top)
            # round 4: what the landmark's figure measures, under it (reference_note: "GROUND TO TORCH TIP"), in
            # the data face at the ruler's size, three lines at most (two until QF-24), inside the box or not at all
            note = reference_note(ref_label)
            if note:
                f_rn = get_font(brand.DATA_FONT, SCALE_RULER_PX - 4, weight=600)
                # QF-24: wrapped on its tracked ink (it is drawn with track=1), three rows at most: wrapped on the
                # untracked advance and held to two, the narrower organic box dropped "GROUND TO TORCH TIP" whole
                nl, _f, short = _probe_tracked(probe, note, brand.DATA_FONT, f_rn.size, int(box_x1 - ref_note.box[0] - 4),
                                               3, f_rn.size, 1, sentences=False, weight=600)
                yb = ref_note.box[3] + 14 + cap_height(f_rn)
                rows_n = [plan_text(ground, (ref_note.box[0], yb + k * round(cap_height(f_rn) * 1.6)), ln, f_rn, MUTED,
                                    "ls", track=1) for k, ln in enumerate(nl)]
                if not short and all(r.box[2] <= box_x1 for r in rows_n):
                    plates += [(f"ref_note{k}", r) for k, r in enumerate(rows_n)]
    t_drop_end = SCALE_SETTLE
    t_bracket = t_drop_end                 # round 4: the bracket draws on at the settle frame (2.85 s before)
    heading = {"subject", "figure", "figure_metric"}
    if depth:
        centre = (bracket[0] if bracket else ref_cx, line_y)
    else:
        centre = ((shaft[0] + ref_cx) / 2, base_y - max(feet, ref_feet) * px_ft)
    last: list = []

    def compose(t: float, push: bool = True) -> Image.Image:
        """One frame: the drawing and the words tied to it, pushed in over the hold; then the heading and the
        meaning, which never move (the push stays inside the drawing's own air)."""
        im = ground.copy()
        d = raster.Draw(im, "RGBA")                   # QF-26: every shape of the drawing antialiased
        if depth:
            d.rectangle([x0, line_y, x1, floor_y + SECTION_FOOT], fill=(*EARTH, 255))  # the ground, in section
            dig = _ease((t - 0.3) / 1.2)
            y_end = line_y + (floor_y - line_y) * dig
            for yy, _pl in rulers:                                                     # the ruler's ticks
                if yy <= y_end + 1:
                    d.line([(shaft[0] - 18, yy), (shaft[0] - 3, yy)], fill=(*brand.STONE_LT, 255), width=2)
            d.rectangle([shaft[0], line_y, shaft[1], y_end], fill=(*SEA, 255), outline=(*GOLD, 255), width=3)
            drop = _ease((t - 1.6) / 1.0)
            if drop > 0:
                hh = ref_feet * px_ft
                u = (t - t_drop_end) / 0.15                    # a 2.5 % squash and settle as it lands
                hh *= 1 - 0.025 * math.sin(math.pi * u) if 0 < u < 1 else 1
                base = floor_y - (1 - drop) * (floor_y - line_y + hh + 40)
                _silhouette(d, ref_label, ref_cx, base, hh, ref_grey)
            d.rectangle([x0, line_y - 2, x1, line_y + 1], fill=(*WHITE, 255))         # ground line over it
        else:
            d.rectangle([x0, base_y, x1, base_y + 3], fill=(*WHITE, 255))
            for yy, _pl in rulers:                                      # the shared ruler's ticks
                if yy < base_y - 1:
                    d.line([(rule_x - 6, yy), (rule_x + 10, yy)], fill=(*brand.STONE_LT, 255), width=2)
            rise = _ease((t - 0.3) / 1.2)
            hs = feet * px_ft * rise
            _monolith(d, shaft[0], shaft[1], base_y, hs)
            rr = _ease((t - 1.2) / 1.2)
            if rr > 0:
                _silhouette(d, ref_label, ref_cx, base_y, ref_feet * px_ft * rr, ref_grey)
        ab = _ease((t - (t_bracket if depth else 2.4)) / 0.4)
        if bracket and ab > 0:
            bx, b0, b1 = bracket
            if depth:   # drawn on from the ground line
                grow = b0 + (b1 - b0) * (1 - ab) if b1 == line_y else b0
                end = b1 if b1 == line_y else b0 + (b1 - b0) * ab
                _bracket(d, bx, grow, end, 12, 3, (*GOLD, 255))
            else:       # the landmark's hairline tick
                d.line([(bx, b0), (bx + 26 * ab, b0)], fill=(*brand.STONE_LT, 255), width=2)

        def at_of(name: str) -> float:
            at = {"subject": 0.1, "figure": 0.4, "figure_metric": 0.5, "block_label": 1.5,
                  "ref_label": 2.4, "bracket": t_bracket + 0.1}.get(name)
            if at is None:
                at = (0.5 + 0.1 * int(name[5:]) if name.startswith("ruler") else 2.5 if name.startswith("ref_note")
                      else SCALE_MEANING_AT)
            return at
        for name, plate in plates:
            if name in heading or name.startswith("meaning"):
                continue
            a = _ease((t - at_of(name)) / 0.4)
            if a > 0:
                im = plate.draw(im, a, round(10 * (1 - a)))
        if push:
            im = _push_about(im, hold_push(t), centre)
        for name, plate in plates:
            if name in heading or name.startswith("meaning"):
                a = _ease((t - at_of(name)) / 0.4)
                if a > 0:
                    im = plate.draw(im, a, round(10 * (1 - a)))
        return im

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = compose(t)
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)      # the push ends at 1.0: the last frame is the planned layout
        size = (width, height)
        if depth:
            marks = [("ground_line", mark_mask(size, lambda d: d.rectangle([x0, line_y - 2, x1, line_y + 1], fill=255))),
                     ("shaft", mark_mask(size, lambda d: d.rectangle([shaft[0], line_y, shaft[1], floor_y], outline=255,
                                                                     width=3)))]
        else:
            marks = [("ground_line", mark_mask(size, lambda d: d.rectangle([x0, base_y, x1, base_y + 3], fill=255))),
                     ("subject", mark_mask(size, lambda d: d.rectangle([shaft[0], base_y - feet * px_ft, shaft[1],
                                                                        base_y - 1], fill=255)))]
        record_nontext(out, last[0], marks, None if depth else ground)
    return result


def scale_figure_lines(probe, figure: str, px: int, one_line_min: int, two_line_min: int,
                       width: float) -> tuple[list[str], Any]:
    """The 16:9 scale figure's lines (round 4, 2026-11-02: "ABOUT 280 FT / DEEP" orphaned "DEEP" at display size):
    one line at the largest size from `px` down to `one_line_min` that fits `width`; else two balanced lines (the
    break whose longer line is shortest, never a single short word alone on the last line) at the largest size
    from `px` down to `two_line_min`."""
    for size in range(px, one_line_min - 1, -2):
        f = get_font("Anton", size)
        if probe.textlength(figure, font=f) <= width:
            return [figure], f
    words = figure.split()
    splits = [(" ".join(words[:k]), " ".join(words[k:])) for k in range(1, len(words))]
    # never a short word alone on the last line, never a figure split from its unit ("ABOUT 280 / FT DEEP")
    splits = [sp for sp in splits if (len(sp[1].split()) > 1 or len(words) == 2)
              and not sp[0].split()[-1][-1:].isdigit()] or splits
    for size in range(px, two_line_min - 1, -2):
        f = get_font("Anton", size)
        best = min(splits, key=lambda sp: max(probe.textlength(x, font=f) for x in sp)) if splits else (figure,)
        if max(probe.textlength(x, font=f) for x in best) <= width:
            return [x for x in best if x], f
    f = get_font("Anton", two_line_min)
    return [x for x in (best if splits else (figure,)) if x], f


def _render_scale_wide(fact: dict[str, Any], feet: float, ref_label: str, ref_feet: float, depth: bool, out: Path,
                       width: int, height: int) -> Path | None:
    """16:9 scale (see render_scale): the drawing in the left half of the vignette-safe area (WIDE_SAFE), a depth
    as a shaft drilled from the ground with the landmark standing on its floor, a height as a gold block rising
    from the ground beside the landmark; the subject, its figure and the meaning in a column at the right."""
    g = _WideGrid(width, height)
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)
    _sx0, sy0, _sx1, sy1 = WIDE_SAFE
    dx0, dx1 = WIDE_LEFT + 80, 1000.0         # the drawing (round 3: from title-safe, the master lifts its
    #                                              vignette under graphics; it kept to WIDE_SAFE's x 400 before)
    cx0, cx1 = wide_col(7), WIDE_RIGHT        # round 4: the words on a grid column, to title-safe's right edge
    hedge = "ABOUT " if fact.get("approximate", True) else ""
    subject = str(fact.get("subject") or "").upper()
    figure, metric = scale_figure(fact, feet, depth)
    meaning = scale_meaning_lines(fact, feet, ref_label, ref_feet, depth)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    col = S(cx1 - cx0) - 8
    sub_lines, f_sub, _ = fit_text(probe, subject, "Montserrat", g.f(32), int(col - 3 * len(subject)), 2, g.f(28),
                                   sentences=False, weight=800, quiet=True)
    f_sub = get_font("Montserrat", f_sub.size, weight=800)
    fig_lines, f_fig = scale_figure_lines(probe, figure, g.f(96), g.f(72), g.f(64), col)
    fits = [probe_text(probe, text, "Montserrat", g.f(36), int(col), 2, g.f(32), sentences=False,
                     weight=600, quiet=True) for text in meaning]
    f_mean = get_font("Montserrat", min(f.size for _, f, _ in fits), weight=600)
    mean_lines = [ln for text in meaning for ln in fit_text(probe, text, "Montserrat", f_mean.size, int(col),
                                                            3, f_mean.size, sentences=False, weight=600, quiet=True)[0]]
    sub_step = cap_height(f_sub) * 1.5 / g.k
    fig_step = cap_height(f_fig) * 1.22 / g.k
    mean_step = cap_height(f_mean) * 1.75 / g.k
    rows: list[tuple[str, str, Any, Any, float, float]] = []    # name, text, font, colour, track, baseline (design)
    y = 0.0
    for j, ln in enumerate(sub_lines):
        y += cap_height(f_sub) / g.k if j == 0 else sub_step
        rows.append((f"subject{j}" if j else "subject", ln, f_sub, MUTED, S(3), y))
    y += 30
    for j, ln in enumerate(fig_lines):
        y += cap_height(f_fig) / g.k if j == 0 else fig_step
        rows.append((f"figure{j}" if j else "figure", ln, f_fig, GOLD, 0, y))
    if metric:   # the metric figure on its own line in the data face (round 3: no Anton parentheses)
        f_met = get_font(brand.DATA_FONT, g.f(40), weight=600)
        y += 22 + cap_height(f_met) / g.k
        rows.append(("figure_metric", metric, f_met, MUTED, S(1), y))
    rule_y = y + 34
    y = rule_y + 30
    for j, ln in enumerate(mean_lines):
        y += cap_height(f_mean) / g.k if j == 0 else mean_step
        rows.append((f"meaning{j}", ln, f_mean, WHITE, 0, y))
    block_h = y
    col_top = (sy0 + sy1) / 2 - block_h / 2
    ground = Image.new("RGB", (width, height), SEA)
    width_per_px = _silhouette_width(ref_label)
    top_d, draw_bottom = WIDE_TITLE_SAFE[1], WIDE_TITLE_SAFE[3] - 14  # round 4: title-safe's height (210-790 before)
    if depth:
        line_y = top_d + 60
        above = max(0.0, ref_feet - feet)
        px_ft = (draw_bottom - line_y) / max(feet, 1.0)
        if above * px_ft > line_y - top_d:
            px_ft = (draw_bottom - top_d) / (feet + above)
        px_ft = min(px_ft, (dx1 - dx0 - 160) / max(1.0, ref_feet * width_per_px))
        floor_y = draw_bottom
        line_y = floor_y - feet * px_ft
        half = max(ref_feet * px_ft * width_per_px / 2 + 60, 160)
        cx = (dx0 + dx1) / 2
        shaft = (cx - half, cx + half)
        ref_cx = cx
    else:
        base_y = draw_bottom
        block = 200.0
        f_rule_w = get_font(brand.DATA_FONT, g.f(28), weight=600)
        rule_x = WIDE_LEFT + 16 + max(tracked_width(t, f_rule_w, S(1)) for _f, t in
                                      height_ruler(max(feet, ref_feet))) / g.k   # round 4: the ruler's figures end here
        room = dx1 - dx0 - 60 - block - 60
        top_ft = max(ft for ft, _ in height_ruler(max(feet, ref_feet)))
        px_ft = min((base_y - top_d - 40) / max(feet, ref_feet, top_ft), room / max(1.0, ref_feet * width_per_px))
        shaft = (dx0 + 40, dx0 + 40 + block)
        ref_cx = shaft[1] + 60 + max(room, ref_feet * px_ft * width_per_px) / 2

    # The ground plane ends inside the frame on both sides (round 3: it ran off x 0 into the dark and stopped
    # hard at x 1000 mid-frame), from title-safe to just short of the column of words.
    gx0, gx1 = WIDE_LEFT, cx0 - 50
    f_note = get_font("Montserrat", g.f(30), weight=800)
    f_rule = get_font(brand.DATA_FONT, g.f(28), weight=600)
    ann: list[tuple[str, str, Any, Any, float, float, str]] = []     # name, text, font, colour, x, baseline, anchor
    bracket = None
    if depth:
        top_y = floor_y - ref_feet * px_ft
        ref_w = ref_feet * px_ft * width_per_px
        bx = _bracket_x(ref_cx, ref_w, abs(ref_feet - feet), ref_feet, feet, shaft[1])
        bracket = (bx, min(top_y, line_y), max(top_y, line_y))
        note_x = bx + 16 if ref_feet > feet else shaft[1] + 20
        note_y = (bracket[1] + bracket[2]) / 2 + cap_height(f_note) / g.k / 2
        if ref_feet > feet:          # above the ground line, never across it
            note_y = min(note_y, line_y - 20)   # its measured ring clear of the bone line
        if bracket[2] - bracket[1] >= 6:
            ann.append(("bracket", f"{hedge}{_short(abs(ref_feet - feet))}", f_note, GOLD, note_x, note_y, "ls"))
        for j, (ft, text, is_floor) in enumerate(_depth_ruler(feet, hedge)):
            ann.append((f"ruler{j}", text, f_rule, GOLD if is_floor else MUTED, shaft[0] - 30,
                        line_y + ft * px_ft + cap_height(f_rule) / g.k * (1.5 if ft == 0 else 0.5), "rs"))
    else:
        f_blk = get_font("Montserrat", g.f(30), weight=800)
        ann.append(("block_label", hedge + _short(feet), f_blk, GOLD, (shaft[0] + shaft[1]) / 2,
                    base_y - feet * px_ft - 18, "ms"))
        ref_top = base_y - ref_feet * px_ft
        tick_x = ref_cx + max(0.05 * ref_feet * px_ft, 6)        # just right of the landmark's top
        ann.append(("ref_label", _short(ref_feet), f_blk, MUTED, tick_x + 34, ref_top + cap_height(f_blk) / g.k / 2,
                    "ls"))
        note = reference_note(ref_label)             # round 4: what the landmark's figure measures
        if note:          # one line, or two balanced ones when it would run into the column of words
            words = note.split()
            lines_n = [note]
            if tick_x + 34 + tracked_width(note, f_rule, S(1)) / g.k > cx0 - 20 and len(words) > 1:
                half = (len(words) + 1) // 2
                lines_n = [" ".join(words[:half]), " ".join(words[half:])]
            for k, ln in enumerate(lines_n):
                ann.append((f"ref_note{k}", ln, f_rule, MUTED, tick_x + 34,
                            ref_top + cap_height(f_blk) / g.k / 2 + 16 + cap_height(f_rule) / g.k * (1 + 1.6 * k), "ls"))
        bracket = (tick_x, ref_top, ref_top)
        for j, (ft, text) in enumerate(height_ruler(max(feet, ref_feet))):
            ann.append((f"ruler{j}", text, f_rule, MUTED, rule_x - 14,
                        base_y - 14 if ft == 0 else base_y - ft * px_ft + cap_height(f_rule) / g.k / 2, "rs"))

    def drawing(t: float) -> Image.Image:
        im = ground.copy()
        d = raster.Draw(im, "RGBA")                   # QF-26: every shape of the drawing antialiased
        lw = max(1, round(S(3)))
        if depth:
            d.rectangle([X(gx0), Y(line_y), X(gx1), Y(floor_y + 24)], fill=(*EARTH, 255))   # the ground, in section
            dig = _ease((t - 0.3) / 1.2)
            y_end = line_y + (floor_y - line_y) * dig
            for name, _text, _f, _c, _x, yb, _a in ann:                                  # the ruler's ticks
                if name.startswith("ruler") and name != "ruler0":
                    yy = yb - cap_height(f_rule) / g.k / 2
                    if yy <= y_end + 1:
                        d.line([(X(shaft[0] - 22), Y(yy)), (X(shaft[0] - 4), Y(yy))], fill=(*brand.STONE_LT, 255),
                               width=max(1, round(S(2))))
            d.rectangle([X(shaft[0]), Y(line_y), X(shaft[1]), Y(y_end)], fill=(*SEA, 255), outline=(*GOLD, 255),
                        width=lw)
            drop = _ease((t - 1.6) / 1.0)
            if drop > 0:
                hh = ref_feet * px_ft
                u = (t - SCALE_SETTLE) / 0.15                        # a 2.5 % squash and settle as it lands
                hh *= 1 - 0.025 * math.sin(math.pi * u) if 0 < u < 1 else 1
                base = floor_y - (1 - drop) * (floor_y - line_y + hh + 40)
                _silhouette(d, ref_label, X(ref_cx), Y(base), S(hh), brand.STONE_LT)
            d.rectangle([X(gx0), Y(line_y) - S(2), X(gx1), Y(line_y) + S(1)], fill=(*WHITE, 255))
            ab = _ease((t - SCALE_SETTLE) / 0.4)            # round 4: on the settle frame (2.85 s before)
            if bracket and ab > 0 and bracket[2] - bracket[1] >= 6:
                bx, b0, b1 = bracket
                y_a = b0 + (b1 - b0) * (1 - ab) if b1 == line_y else b0
                y_b = b1 if b1 == line_y else b0 + (b1 - b0) * ab
                _bracket(d, X(bx), Y(y_a), Y(y_b), S(12), lw, (*GOLD, 255))
        else:
            d.rectangle([X(gx0), Y(base_y), X(gx1), Y(base_y) + S(3)], fill=(*WHITE, 255))
            for name, _text, _f, _c, _x, yb, _a in ann:                              # the shared ruler's ticks
                if name.startswith("ruler") and name != "ruler0" and name in shown:
                    yy = yb - cap_height(f_rule) / g.k / 2
                    d.line([(X(rule_x - 6), Y(yy)), (X(rule_x + 12), Y(yy))], fill=(*brand.STONE_LT, 255),
                           width=max(1, round(S(2))))
            rise = _ease((t - 0.3) / 1.2)
            hs = feet * px_ft * rise
            _monolith(d, X(shaft[0]), X(shaft[1]), Y(base_y), S(hs))
            rr = _ease((t - 1.2) / 1.2)
            if rr > 0:
                _silhouette(d, ref_label, X(ref_cx), Y(base_y), S(ref_feet * px_ft * rr), brand.STONE_LT)
            ab = _ease((t - 2.4) / 0.4)
            if bracket and ab > 0 and "ref_label" in shown:          # the landmark's hairline tick
                d.line([(X(bracket[0]), Y(bracket[1])), (X(bracket[0] + 26 * ab), Y(bracket[1]))],
                       fill=(*brand.STONE_LT, 255), width=max(1, round(S(2))))
        return im

    shown: set[str] = {"ref_label"} | {a[0] for a in ann if a[0].startswith("ruler")}
    final = drawing(1e9)
    plates = [(name, plan_text(final, (X(cx0 + 4), Y(col_top + yb)), text, font, colour, "ls", track=track))
              for name, text, font, colour, track, yb in rows]
    for name, text, font, colour, x, yb, anchor in ann:
        plate = plan_text(final, (X(x), Y(yb)), text, font, colour, anchor, track=S(1))
        if plate.box[0] < X(WIDE_TITLE_SAFE[0]) or plate.box[2] > X(cx0 - 20):
            continue                                                 # never into the margin or the column
        if any(LayoutConstraintSolver.boxes_intersect(plate.box, q.box, 6) for _, q in plates):
            continue
        plates.append((name, plate))
    shown.intersection_update(name for name, _ in plates)
    rule = (X(cx0 + 4), Y(col_top + rule_y), X(cx0 + 4 + 64), Y(col_top + rule_y) + max(2, S(4)))
    column = ("subject", "figure", "meaning")
    if depth:
        centre = (X(bracket[0]) if bracket else X(ref_cx), Y(line_y))
    else:
        centre = (X((shaft[0] + ref_cx) / 2), Y(base_y - max(feet, ref_feet) * px_ft))
    last: list[Image.Image] = []

    def at_of(name: str) -> float:
        # round 4: the column starts with the dig (0.3-0.6 s), so the heading never sits over an empty box; the
        # meaning lands on the impact (SCALE_MEANING_AT), the bracket and the landmark's labels on the settle
        return (0.3 if name.startswith("subject") else 0.45 if name.startswith("figure") else
                0.5 + 0.1 * int(name[5:]) if name.startswith("ruler") else
                {"block_label": 1.5, "ref_label": 2.4, "ref_note0": 2.5, "ref_note1": 2.55,
                 "bracket": SCALE_SETTLE + 0.1}.get(name, SCALE_MEANING_AT))

    def compose(t: float, push: bool = True) -> Image.Image:
        im = drawing(t)
        for name, plate in plates:
            if not name.startswith(column):
                a = _ease((t - at_of(name)) / 0.4)
                if a > 0:
                    im = plate.draw(im, a, round(S(10) * (1 - a)))
        if push:
            im = _push_about(im, hold_push(t), centre)
        a_rule = _ease((t - 0.6) / 0.4)
        if a_rule > 0:
            ImageDraw.Draw(im, "RGBA").rectangle(rule, fill=(*GOLD, int(255 * a_rule)))
        for name, plate in plates:
            if name.startswith(column):
                a = _ease((t - at_of(name)) / 0.4)
                if a > 0:
                    im = plate.draw(im, a, round(S(10) * (1 - a)))
        return im

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = compose(t)
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)   # the push ends at 1.0 (see the 9:16 renderer)
        size, lw = (width, height), max(1, round(S(3)))
        if depth:
            marks = [("ground_line", mark_mask(size, lambda d: d.rectangle([X(gx0), Y(line_y) - S(2), X(gx1),
                                                                            Y(line_y) + S(1)], fill=255))),
                     ("shaft", mark_mask(size, lambda d: d.rectangle([X(shaft[0]), Y(line_y), X(shaft[1]), Y(floor_y)],
                                                                     outline=255, width=lw)))]
        else:
            marks = [("ground_line", mark_mask(size, lambda d: d.rectangle([X(gx0), Y(base_y), X(gx1), Y(base_y) + S(3)],
                                                                           fill=255))),
                     ("subject", mark_mask(size, lambda d: d.rectangle([X(shaft[0]), Y(base_y - feet * px_ft),
                                                                        X(shaft[1]), Y(base_y) - 1], fill=255)))]
        record_nontext(out, last[0], marks, None if depth else ground)
    return result


def _backdrop(photo: Path | None, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Image.Image:
    """A dark, heavily blurred version of the topic's photo (or the plain ground) behind text graphics."""
    if photo and Path(photo).exists():
        from .photo_grade import cover
        im = cover(Path(photo), (width, height)).filter(ImageFilter.GaussianBlur(28))   # graded as a bare photo
        dark = Image.new("RGB", (width, height), SEA)
        return Image.blend(im, dark, 0.72).convert("RGB")
    return Image.new("RGB", (width, height), SEA)


# ------------------------------------------------------------- number cards
def _pushed_in(bg: Image.Image, scale: float) -> Image.Image:
    """The backdrop scaled by `scale` about its centre and cropped back to size (the push-in), through the shared
    camera (QF-22: a fractional window, so a slow push moves every frame). Bilinear, as before: these grounds are
    soft photographs under cards, and it is twice as fast per frame as bicubic (GroundDrift resamples every frame)."""
    return kenburns(bg, zoom=max(1.0, scale), resample=Image.Resampling.BILINEAR)


def _split_number(value: str) -> tuple[str, float | None, str]:
    """'20,000' -> ('', 20000, ''); 'about 280 ft' -> ('about ', 280, ' ft'); no number -> (value, None, '')."""
    import re
    m = re.search(r"\d[\d,]*(\.\d+)?", value)
    if not m:
        return value, None, ""
    return value[:m.start()], float(m.group(0).replace(",", "")), value[m.end():]


def _shown_value(value: str, p: float) -> str:
    """A number card's figure `p` of the way through its count-up, written as the writer wrote it: a thousands
    separator only where the value has one ("1404-1438" was drawn as "1,404-1438": Voynich, 2026-10-25), and
    a year (a plain four-digit number) shown whole from the start, never counted up from zero. Every figure in
    the value counts together: "280 FT (85 M)" counted the feet up while the metres sat at 85 from the first
    frame, so the two units disagreed until the count landed (number card, 2026-10-29). The count moves in the
    figure's own precision (count_step), and a second figure ("(85 M)") follows the first one's shown fraction,
    so the two still name the same depth on every frame (round 3, 2026-11-02)."""
    import re
    pattern = r"\d[\d,]*(?:\.\d+)?"

    def is_year(raw: str, num: float) -> bool:
        return "," not in raw and "." not in raw and 1000 <= num <= 2199     # a year, or reads as one
    q_lead = shown_fraction(value, p)

    def counted(m) -> str:
        raw = m.group(0)
        num = float(raw.replace(",", ""))
        separated = "," in raw
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        spec = ("," if separated else "") + (f".{decimals}f" if decimals else "")
        if is_year(raw, num):
            return raw
        if decimals:
            return format(num * q_lead, spec)
        step = count_step(raw)
        return format(round(num * q_lead / step) * step, spec)
    return re.sub(pattern, counted, value)


def shown_fraction(value: str, p: float) -> float:
    """How far through its count `value`'s first counted figure is shown at progress `p`, once quantised to its
    count_step (a second figure, in the value or on its own line, counts to this fraction so both agree)."""
    import re
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", value):
        raw = m.group(0)
        num = float(raw.replace(",", ""))
        if ("," not in raw and "." not in raw and 1000 <= num <= 2199) or num <= 0:
            continue
        if "." in raw:
            return p
        step = count_step(raw)
        return round(num * p / step) * step / num
    return p


def count_step(raw: str) -> int:
    """The step a count-up of the written figure `raw` moves in (round 3, 2026-11-02): the precision its written
    form carries, 10 to the power of its trailing zeros ("1,200,000" by 100,000, "280" by 10, "20,000" by 1,000), so
    no frame shows an invented, more precise figure (597,340 on the way to 1,200,000); a figure with fewer than ten
    such steps counts in the next finer round step (100 by 10), never finer than the units it is written in."""
    digits = raw.replace(",", "").split(".")[0]
    num = int(digits or 0)
    if num <= 0:
        return 1
    step = 10 ** (len(digits) - len(digits.rstrip("0")))
    while step > 1 and num / step < 10:
        step //= 10
    return step


def figure_parts(value: str) -> dict[str, str]:
    """A number card's value in its drawn parts: what comes before the figure ("ABOUT", "EST."), the figure
    ("280", "25+", "94–104"), its unit ("FT", "MILLION YEARS") and a figure in brackets ("85 M"), which the card
    sets as its second, data-face line. A value with no figure is all `unit` (drawn as it is)."""
    import re
    m = re.match(r"^\s*(?P<pre>[^\d(]*?)\s*(?P<num>\d[\d,.]*(?:\s*[–-]\s*\d[\d,.]*)?\+?)\s*(?P<unit>[^()]*?)\s*"
                 r"(?:\((?P<sec>[^)]*)\))?\s*$", value or "")
    if not m:
        return {"pre": "", "num": "", "unit": (value or "").strip(), "sec": ""}
    return {k: " ".join((m.group(k) or "").split()) for k in ("pre", "num", "unit", "sec")}


def _cells(d: ImageDraw.ImageDraw, text: str, font) -> list[tuple[str, float]]:
    """Each character of a figure with its advance: digits on one fixed cell (the widest digit), so a count-up
    rolls in place without the unit beside it moving."""
    cell = max(d.textlength(c, font=font) for c in "0123456789")
    return [(c, cell if c.isdigit() else d.textlength(c, font=font)) for c in text]


def _stat_layout(d: ImageDraw.ImageDraw, card: dict[str, Any], num_px: int, width: int, scale: float = 1.0,
                 label_min: int | None = None, num_min: int = 120) -> dict[str, Any]:
    """One card measured (offsets from its block's top, x from the left margin): the figure's font fitted so the
    figure and its unit stay inside the graphics box, the label in up to two lines, the bracketed figure in the
    data face (IBM Plex Mono), the prefix as a kicker. A unit that cannot share the figure's line without the
    figure falling below `num_min` px goes under it ("94 – 104" over "MILLION YEARS AGO", 2026-11-02; it ran past
    the box before). `label_min`: the label's smallest size (default 75% of its own)."""
    parts = figure_parts(card["value"].upper())
    pre, num, unit, sec = parts["pre"], parts["num"], parts["unit"], parts["sec"]
    if not num:                          # no figure: the value is drawn as a heading
        num, unit = "", parts["unit"]
    size = num_px
    below = False
    floor = num_min
    while True:
        f_num = get_font("Anton", size)
        f_unit = get_font("Anton", max(48, round(size * 0.37)))
        num_w = sum(w for _, w in _figure_cells(d, num, f_num)) if num else 0
        unit_w = d.textlength(unit, font=f_unit) if unit else 0
        if num_w + (24 if num and unit else 0) + unit_w <= width:
            break
        if size <= floor:
            below = bool(num and unit)
            break
        size -= 10
    if below:                            # the unit on its own line: the figure as large as it fits alone
        size = num_px
        while size > num_min:
            f_num, f_unit = get_font("Anton", size), get_font("Anton", max(48, round(size * 0.37)))
            num_w = sum(w for _, w in _figure_cells(d, num, f_num))
            unit_w = d.textlength(unit, font=f_unit)
            if num_w <= width and unit_w <= width:
                break
            size -= 10
    rows: list[dict[str, Any]] = []
    y = 0
    if pre:
        f_k = get_font("Montserrat", round(40 * scale), weight=800)
        bar_w = round(44 * scale)
        bar_h = max(3, round(4 * scale))
        y += bar_h
        bar_y = y
        y += round(14 * scale)
        y += cap_height(f_k)
        rows.append({"name": "prefix", "kind": "kicker", "text": pre, "font": f_k, "y": y,
                     "bar": (0, round(bar_y - bar_h), bar_w, round(bar_y))})
        y += round(34 * scale)
    y += cap_height(f_num if num else f_unit)
    num_y = y
    if num:
        rows.append({"name": "figure", "kind": "figure", "text": num, "font": f_num, "y": y, "width": num_w})
    if unit and below:
        y += round(cap_height(f_unit) * 1.45)
        rows.append({"name": "unit", "kind": "text", "text": unit, "font": f_unit, "y": y, "x": 0, "fill": WHITE})
    elif unit:
        rows.append({"name": "unit", "kind": "text", "text": unit, "font": f_unit, "y": y, "x": (num_w + 24) if num else 0,
                     "fill": WHITE})
    f_lab_px = round(46 * scale)
    lines, f_lab, _ = fit_text(d, card["label"].upper(), "Montserrat", f_lab_px, width - 120, 2,  # 3 px tracking
                               label_min if label_min is not None else max(26, round(f_lab_px * 0.75)), weight=800,
                               quiet=True)
    f_lab = get_font("Montserrat", f_lab.size, weight=800)
    y += round(f_lab.size * 1.75)
    for j, line in enumerate(lines):
        rows.append({"name": "label", "kind": "text", "text": line, "font": f_lab, "y": y + j * round(f_lab.size * 1.26),
                     "fill": WHITE, "track": 3})
    y += (len(lines) - 1) * round(f_lab.size * 1.26)
    if sec:
        f_sec = get_font(brand.DATA_FONT, round(46 * scale), weight=600)
        y += round(f_sec.size * 1.45)
        rows.append({"name": "secondary", "kind": "count", "text": sec, "font": f_sec, "y": y, "fill": WHITE})
    probe_rows = [r for r in rows if r["kind"] != "kicker"]
    low = max(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in probe_rows)
    high = min([0] + [d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in rows])
    right = max(r.get("x", 0) + (r["width"] if r["kind"] == "figure" else
                                 tracked_width(r["text"], r["font"], r.get("track", 0))) for r in rows)
    return {"rows": rows, "top": high, "bottom": low, "height": low - high, "num_y": num_y, "figure_px": size,
            "width": right}


def render_stat_cards(cards: list[dict[str, Any]], out: Path, photo: Path | None = None, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                      narration: str = "") -> Path | None:
    """1-2 headline figures counting up to their value, each with its label (visual overhaul, 2026-10-29).
    9:16: left-aligned on the text margin over the photo itself (pushing in), an adaptive ink gradient behind the
    block; the figure in gold Anton with its digits on fixed cells, right-aligned so they roll in place, its unit
    in bone beside it, the pair fitted inside the graphics box (safe_zones.graphics_box: clear of Shorts' icon
    column and the caption band); a bracketed figure ("(85 m)") in the data face under the label, counting with
    it. Every line is drawn at a measured contrast, re-measured on the graded last frame (<clip>.contrast.json).
    `narration`: the run's narration, whose units the figures are drawn in (typography.spoken_units)."""
    # One unit per run: a figure in the other system is drawn in the unit the voice speaks (2026-10-29).
    cards = [dict(c, value=spoken_scale(figure_text(spoken_units(str(c.get("value") or ""), narration)), narration),
                  label=spoken_units(str(c.get("label") or ""), narration)) for c in cards[:2]]
    if not cards:
        return None
    if width > height:
        return _render_stat_cards_landscape(cards, out, photo, width, height)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    top, bottom = get_text_bounds(width, height)
    x_text = inset_x0(width, height)                     # the shared inset edge (was M, 84)
    text_w = get_text_box(width, height)[2] - x_text - 4
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    # Measured first: both cards between the bounds (the lower one ending on the graphics box's bottom at most),
    # the type stepped down together when they do not fit.
    for scale in (1.0, 0.9, 0.8, 0.7, 0.6):
        num_px = round((380 if len(cards) == 1 else 230) * scale)
        try:
            plans = [_stat_layout(probe, c, num_px, text_w, scale) for c in cards]
            if len(plans) > 1:   # one figure size for both (round 3): a smaller second figure implied a ranking
                # QF-24: measured at the shared size it is drawn at. The fit was tested on the two cards' own
                # (larger) sizes, so the pair could step down a whole scale, kickers and labels too, where the drawn
                # pair fitted; the narrower organic box made that more frequent.
                size = min(p["figure_px"] for p in plans)
                plans = [_stat_layout(probe, c, size, text_w, scale, num_min=size) for c in cards]
        except TextFitError:
            if scale == 0.6:
                raise
            continue
        gap = 90
        total = sum(p["height"] for p in plans) + gap * (len(plans) - 1)
        if total <= bottom - top:
            break
    y = max(top, (top + bottom) // 2 - total // 2)
    for p in plans:
        p["y0"] = y - p["top"]
        p["x0"] = x_text
        y += p["height"] + gap
    if photo and Path(photo).exists():
        from .photo_grade import cover
        bg = cover(Path(photo), (width, height))   # the photo graded as the master grades it bare (QF-86)
    else:
        bg = Image.new("RGB", (width, height), SEA)
    from .multi_format import scrim
    y0b, y1b = plans[0]["y0"] + plans[0]["top"] - 40, plans[-1]["y0"] + plans[-1]["bottom"] + 40
    block = (x_text, y0b, x_text + text_w, y1b)
    target = stat_scrim_target(bg, block)    # the scrim's depth read on the photo itself, before the hue gate: the
    bg = stat_hue_gate(bg, y0b, y1b, block)  # gate adds separation by hue, it never buys a lighter scrim (the
    bg = scrim(bg, y0b, y1b, feather=380, target=target).convert("RGB")   # stepped-back stone figure needs it)
    return _stat_clip(cards, plans, bg, out, n, width, height)


def stat_scrim_target(bg: Image.Image, box: tuple[float, float, float, float]) -> float:
    """The 9:16 number card's scrim target (multi_format.scrim brings the block's band toward it; lower is deeper):
    0.24 as before, deepened to 0.16 behind a bright warm block (gold or ochre tuff, sandstone), where the gold
    figure separates from its ground by luminance alone (round 3, 2026-11-02): scaled by how bright the block is
    and how much of it lies in the gold's hues."""
    import numpy as np
    x0, y0, x1, y1 = (int(max(0, v)) for v in box)
    crop = bg.convert("RGB").crop((x0, y0, max(x0 + 1, min(bg.width, x1)), max(y0 + 1, min(bg.height, y1))))
    hsv = np.asarray(crop.convert("HSV"), dtype=np.float32) / 255.0
    rgb = np.asarray(crop, dtype=np.float32) / 255.0
    lum = float((rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean()) if rgb.size else 0.0
    hue = hsv[..., 0] * 360.0
    warm = float(((hue >= 15) & (hue <= 62) & (hsv[..., 1] > 0.25)).mean()) if hsv.size else 0.0
    bright = float(np.clip((lum - 0.25) / 0.3, 0.0, 1.0))
    return round(0.24 - 0.08 * warm * bright, 3)


_SCALE_WORDS = ((1_000_000_000, "BILLION"), (1_000_000, "MILLION"))


def spoken_scale(value: str, narration: str) -> str:
    """A round million-scale figure written the way the voice says it (round 4, 2026-11-02): "1,200,000 TONS" with
    a narration that says "1.2 million" draws "1.2 MILLION TONS", the same value at the same precision, shorter
    and matching the captions. Only when the narration says exactly that figure with its scale word and the
    written figure is that round number; otherwise unchanged. Render time only: the gates see the payload digits."""
    import re
    if not narration:
        return value

    def short(m) -> str:
        raw = m.group(0)
        num = int(raw.replace(",", ""))
        for size, word in _SCALE_WORDS:
            if num >= size and num % (size // 10) == 0:
                head = f"{num / size:g}"
                if re.search(rf"(?<![\d.,]){re.escape(head)}\s*{word}\b", narration, re.IGNORECASE):
                    return f"{head} {word if value.isupper() else word.lower()}"
        return raw
    return re.sub(r"(?<![\d.])\d{1,3}(?:,\d{3}){2,}(?![\d.,])", short, value)


STAT_GROUND_SAT = 0.18   # round 4 review: the gated band's mean saturation (the warm measure counts pixels over 0.25)
STAT_GATE_MAX = 0.85     # never fully grey: the photo keeps a trace of its colour


def stat_hue_gate(bg: Image.Image, y0: float, y1: float, box: tuple[float, float, float, float],
                  amount: float = 0.4, feather: int = 220) -> Image.Image:
    """Gold-on-gold (round 4, 2026-11-02): behind a bright warm block (the same warm x bright measure as
    stat_scrim_target), the band from y0 to y1 loses `amount` of its saturation, feathered, so the gold figure
    separates by hue as well as by luminance; any other ground is returned unchanged."""
    import numpy as np
    from PIL import ImageEnhance
    x0, by0, x1, by1 = (int(max(0, v)) for v in box)
    crop = bg.convert("RGB").crop((x0, by0, max(x0 + 1, min(bg.width, x1)), max(by0 + 1, min(bg.height, by1))))
    hsv = np.asarray(crop.convert("HSV"), dtype=np.float32) / 255.0
    rgb = np.asarray(crop, dtype=np.float32) / 255.0
    lum = float((rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean()) if rgb.size else 0.0
    hue = hsv[..., 0] * 360.0
    warm = float(((hue >= 15) & (hue <= 62) & (hsv[..., 1] > 0.25)).mean()) if hsv.size else 0.0
    bright = float(np.clip((lum - 0.25) / 0.3, 0.0, 1.0))
    if warm * bright < 0.25:
        return bg
    # Round 4 review (2026-11-02): a fixed 40 % left an ochre band at about 0.35 saturation, still "warm" to
    # stat_scrim_target, so the scrim deepened as if the gate had not run and the two looked the same. The amount
    # is now solved from the band's own saturation: down to STAT_GROUND_SAT (under the warm measure's 0.25), so
    # the gold separates by hue as well as by the scrim's luminance (render_stat_cards reads the scrim's depth on
    # the photo before this gate).
    def band_sat(keep: float) -> float:
        return float(np.asarray(ImageEnhance.Color(crop).enhance(keep).convert("HSV"))[..., 1].mean()) / 255.0
    lo, hi = amount, STAT_GATE_MAX              # the least amount that brings the band to STAT_GROUND_SAT
    if band_sat(1 - lo) > STAT_GROUND_SAT:
        for _ in range(7):
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if band_sat(1 - mid) <= STAT_GROUND_SAT else (mid, hi)
        amount = hi
    grey = ImageEnhance.Color(bg.convert("RGB")).enhance(1 - amount)
    ys = np.arange(bg.height, dtype=np.float32)
    w = np.clip(np.minimum((ys - (y0 - feather)) / feather, ((y1 + feather) - ys) / feather), 0, 1)
    mask = Image.fromarray((np.repeat(w[:, None], bg.width, axis=1) * 255).astype(np.uint8), "L")
    return Image.composite(grey, bg.convert("RGB"), mask)


def stat_backdrop_wide(photo: Path | None, width: int, height: int) -> Image.Image:
    """The 16:9 single number card's ground (round 4, 2026-11-02): on the thirds grid, the topic's photo sharp but
    taken down to the timeline's luminance band (timeline_backdrop: the cards cut between graphics without the
    ground pumping) on the right two-thirds, an INK gradient over the left third where the block sits."""
    import numpy as np
    from PIL import ImageEnhance
    if not (photo and Path(photo).exists()):
        return timeline_backdrop(photo, width, height)
    from .photo_grade import cover
    im = cover(Path(photo), (width, height))   # graded as a bare photo (QF-86)
    im = ImageEnhance.Color(im).enhance(0.55)
    im = Image.blend(im, Image.new("RGB", (width, height), brand.INK), 0.78)
    xs = np.arange(width, dtype=np.float32) / width
    a = np.clip((0.62 - xs) / 0.3, 0, 1) ** 1.2 * 0.92                      # ink over the left third, feathered
    mask = Image.fromarray((np.repeat(a[None, :], height, axis=0) * 255).astype(np.uint8), "L")
    return Image.composite(Image.new("RGB", (width, height), brand.INK), im, mask)


def figure_text(value: str) -> str:
    """A number card's value as the card writes it: a range with a closed en dash ("94-104" -> "94–104"; round 3,
    2026-11-02: the spaced dash of round 2 read in Anton at hero size as a minus sign or as two stats), and an "M"
    that means millions of years spelled out ("94-104M YEARS AGO" read as metres): "94–104 MILLION YEARS AGO".
    The figures themselves are never changed."""
    import re
    text = re.sub(r"(\d)\s*[-–]\s*(\d)", r"\1–\2", value or "")
    return re.sub(r"(\d)\s*M\s*((?:YEARS|YRS|YEAR)\b)", r"\1 MILLION \2", text, flags=re.IGNORECASE)


class _JoinedPlate:
    """Several TextPlates recorded as one element: a count-up's figure is planned a character at a time (each
    digit on its cell), but measured on the union of their glyphs, so the ring around one digit is not read on
    the digit 4 px beside it (2026-11-02: each "0" of "1,200,000" recorded 1.1:1 against its gold neighbour while
    the figure stood at 5.7:1 on its photo)."""

    def __init__(self, plates: list[Any], frame: Image.Image, text: str = ""):
        import numpy as np
        plates = [p for p in plates if not p.empty]
        self.empty = not plates
        self.text = text or "".join(p.text for p in plates)
        self.font = plates[0].font if plates else None
        self.halo = max([p.halo for p in plates] or [0.0])
        self.box = (0, 0, 0, 0)
        self.contrast = None
        if self.empty:
            return
        self.fill, self.light = plates[0].fill, plates[0].light
        x0 = min(p.origin[0] for p in plates)
        y0 = min(p.origin[1] for p in plates)
        x1 = max(p.origin[0] + p.glyphs.shape[1] for p in plates)
        y1 = max(p.origin[1] + p.glyphs.shape[0] for p in plates)
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        for p in plates:
            ox, oy = p.origin[0] - x0, p.origin[1] - y0
            h, w = p.glyphs.shape
            np.maximum(mask[oy:oy + h, ox:ox + w], p.glyphs, out=mask[oy:oy + h, ox:ox + w])
        self.glyphs, self.origin = mask, (x0, y0)
        self.box = (min(p.box[0] for p in plates), min(p.box[1] for p in plates),
                    max(p.box[2] for p in plates), max(p.box[3] for p in plates))
        composed = frame
        for p in plates:
            composed = p.draw(composed)
        self.contrast = self.measure(composed)

    def measure(self, im: Image.Image) -> float | None:
        import numpy as np

        from .typography import ring_contrast
        if self.empty:
            return None
        return ring_contrast(np.asarray(im.convert("RGB")), self.glyphs, self.origin, None if self.light else self.fill,
                             self.light)


FIGURE_SETTLE = 0.8         # s: round 4, a number card's figure fades up and settles from 103 % (no count-up)
FIGURE_SETTLE_FROM = 1.03
RANGE_TRACK = 0.06          # a range's characters track in from this much extra spacing (em) as it settles
DASH_EM, DASH_BEARING = 0.5, 0.08   # the range's en dash: a gold rule this wide, with these side bearings (em)


def _figure_cells(d: ImageDraw.ImageDraw, text: str, font) -> list[tuple[str, float]]:
    """A static figure's characters with their advances (round 4, 2026-11-02): proportional digits (the fixed
    cells were for the count-up; on "94–104" the narrow "1" opened a hole) and the range's en dash as a rule
    DASH_EM wide with DASH_BEARING each side (Anton draws U+2013 as its hyphen: it read "94- 104")."""
    em = font.size
    return [(c, em * (DASH_EM + 2 * DASH_BEARING) if c == "\u2013" else d.textlength(c, font=font)) for c in text]


def _dash_rule(font, x: float, baseline: float) -> tuple[float, float, float, float]:
    """The en dash's rule for a dash cell starting at `x` on `baseline`: the hyphen's own weight and height."""
    em = font.size
    pad = 2 * em
    ink = Image.new("L", (pad * 2, pad * 2), 0)
    ImageDraw.Draw(ink).text((pad // 2, pad), "-", font=font, fill=255, anchor="ls")   # the hyphen's own ink
    box = ink.getbbox() or (0, pad - em // 3, 1, pad - em // 4)
    t, b = box[1] - pad, box[3] - pad
    return (x + em * DASH_BEARING, baseline + t, x + em * (DASH_BEARING + DASH_EM), baseline + b)


def _lerp(c0, c1, f: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))


def _stat_clip(cards: list[dict[str, Any]], plans: list[dict[str, Any]], bg: Image.Image, out: Path, n: int,
               width: int, height: int) -> Path | None:
    """The number cards' clip (both orientations): each line planned on the resting frame at a measured contrast
    (a figure one plate per character, recorded as one), the push-in. Round 4 (2026-11-02): no count-up, so no
    frame shows a figure the fact sheet does not (the quantised count drew "800,000 TONS" and a false "63–70" on
    the way to "94–104"): the figure fades up at its final value and settles from FIGURE_SETTLE_FROM, a range
    tracking in; its en dash a gold rule (_figure_cells). With two cards the first steps back to its stone twins
    as the second lands (the timeline's grammar), and those twins are what the contrast record measures."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    rest = _pushed_in(bg, push_in((n - 1) / VIDEO_FPS, CLIP_SECONDS))
    plates: list[tuple[str, Any]] = []
    two = len(plans) > 1
    for k, p in enumerate(plans):   # each line planned on the resting frame: its halo, a figure per character
        x0 = p["x0"]
        p["dims"] = two and k == 0
        for r in p["rows"]:
            yy = p["y0"] + r["y"]
            if r["kind"] == "kicker":
                bx0, by0, bx1, by1 = r.get("bar", (0, 0, 44, 4))
                r["rule"] = (x0 + bx0, p["y0"] + by0, x0 + bx1, p["y0"] + by1)
                r["plates"] = [plan_text(rest, (x0, yy), r["text"], r["font"],
                                         GOLD, "ls", track=r["font"].size * 0.15)]
                r["fill"], r["dim_fill"] = GOLD, brand.STONE
            elif r["kind"] == "figure":
                x, r["plates"], r["chars"], r["dashes"] = x0, [], [], []
                for c, w in _figure_cells(probe, r["text"], r["font"]):
                    if c == "\u2013":
                        r["dashes"].append(_dash_rule(r["font"], x, yy))
                    else:
                        r["plates"].append(plan_text(rest, (x + w / 2, yy), c, r["font"], GOLD, "ms", halo_px=25))
                        r["chars"].append((c, x + w / 2))
                    x += w
                r["x_end"] = x
                r["fill"], r["dim_fill"] = GOLD, brand.STONE
            else:
                r["plates"] = [plan_text(rest, (x0 + r.get("x", 0), yy), r["text"], r["font"], r["fill"], "ls",
                                         track=r.get("track", 0))]
                r["dim_fill"] = brand.STONE_LT
            r["twins"] = [_twin(rest, pl, r["dim_fill"]) for pl in r["plates"]] if p["dims"] else []
            shown = r["twins"] or r["plates"]
            if r["kind"] == "figure":
                plates.append((r["name"], _JoinedPlate(shown, rest, r["text"])))
            else:
                plates += [(r["name"], pl) for pl in shown]
    last: list[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = _pushed_in(bg, push_in(t, CLIP_SECONDS))  # a slow push on the photo: a card never sits static
            for k, (card, p) in enumerate(zip(cards, plans)):
                start = count_start(k)
                a = _ease((t - start) / MOTION["reveal"])
                if a <= 0:
                    continue
                settle = _ease((t - start) / FIGURE_SETTLE)
                dim = _ease((t - count_start(k + 1)) / 0.4) if p["dims"] else 0.0
                dy = round(14 * (1 - a))
                for r in p["rows"]:
                    yy = p["y0"] + r["y"] + dy
                    if r["kind"] == "figure":
                        sc = FIGURE_SETTLE_FROM + (1 - FIGURE_SETTLE_FROM) * settle
                        spread = RANGE_TRACK * r["font"].size * (1 - settle) if r["dashes"] else 0.0
                        if sc <= 1.0005 and spread < 0.25:            # at rest: the measured plates themselves
                            for pl in r["plates"]:
                                if dim < 0.999:
                                    im = pl.draw(im, a * (1 - dim), dy)
                            for tw in r["twins"]:
                                if dim > 0.001:
                                    im = tw.draw(im, a * dim, dy)
                            d = raster.Draw(im, "RGBA")       # QF-26: the dashes antialiased
                            fill = _lerp(r["fill"], r["dim_fill"], dim)
                            for dx0, dy0, dx1, dy1 in r["dashes"]:
                                d.rectangle([dx0, dy0 + dy, dx1, dy1 + dy], fill=(*fill, int(255 * a)))
                            continue
                        for pl in r["plates"]:
                            im = pl.draw_halo(im, a, dy)
                        d = ImageDraw.Draw(im, "RGBA")
                        f = get_font("Anton", max(8, round(r["font"].size * sc)))
                        ox = p["x0"]
                        fill = (*_lerp(r["fill"], r["dim_fill"], dim), int(255 * a))
                        for j, (c, cx) in enumerate(r["chars"]):    # the final figure, scaled about its start
                            _text(d, (ox + (cx - ox) * sc + spread * j, yy), c, f, fill, anchor="ms")
                        bars = raster.Draw(im, "RGBA")            # QF-26: the settling dashes antialiased
                        for j, (dx0, dy0, dx1, dy1) in enumerate(r["dashes"]):
                            shift = spread * (sum(1 for _c, cx in r["chars"] if cx < dx0) - 0.5)
                            base = yy
                            bars.rectangle([ox + (dx0 - ox) * sc + shift, base + (dy0 - (yy - dy)) * sc,
                                         ox + (dx1 - ox) * sc + shift, base + (dy1 - (yy - dy)) * sc], fill=fill)
                    else:
                        if r["kind"] == "kicker":
                            x0, y0, x1, y1 = r.get("rule", r.get("bar", (p["x0"], yy, p["x0"] + 44, yy + 3)))
                            fill = _lerp(r["fill"], r["dim_fill"], dim)
                            raster.Draw(im, "RGBA").rectangle([x0, y0 + dy, x1, y1 + dy], fill=(*fill, int(255 * a)))
                        for pl in r["plates"]:
                            if dim < 0.999:
                                im = pl.draw(im, a * (1 - dim), dy)
                        for tw in r["twins"]:
                            if dim > 0.001:
                                im = tw.draw(im, a * dim, dy)
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)
    return result


def _render_stat_cards_landscape(cards: list[dict[str, Any]], out: Path, photo: Path | None = None, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                      narration: str = "") -> Path | None:
    """16:9 (the documentary, 2026-11-02 review): the 9:16 card's type set for the wide frame over the dark photo
    backdrop. One card centred as a block, two side by side in the title-safe halves; the figure in gold Anton on
    fixed cells counting up, its unit in bone beside it (under it when the pair cannot share a line), the label in
    bone Montserrat at 40 px or more, every line at a measured contrast (<clip>.contrast.json).
    `narration`: the run's narration, whose units the figures are drawn in (typography.spoken_units)."""
    cards = [dict(c, value=spoken_scale(figure_text(spoken_units(str(c.get("value") or ""), narration)), narration),
                  label=spoken_units(str(c.get("label") or ""), narration)) for c in cards[:2]]
    if not cards:
        return None
    n = int(CLIP_SECONDS * VIDEO_FPS)
    bg = stat_backdrop_wide(photo, width, height) if len(cards) == 1 else timeline_backdrop(photo, width, height)
    bx0, top, bx1, bottom = get_text_box(width, height)
    k = min(width / 1920, height / 1080)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    gutter = round(110 * k)
    col_w = (bx1 - bx0) if len(cards) == 1 else (bx1 - bx0 - gutter) // 2
    for scale in (1.0, 0.9, 0.8, 0.7):
        num_px = round((300 if len(cards) == 1 else 210) * scale * k)
        try:
            plans = [_stat_layout(probe, c, num_px, col_w - 8, scale * k,
                                  label_min=round(40 * k), num_min=round(num_px * 0.62)) for c in cards]
        except TextFitError:
            if scale == 0.7:
                raise
            continue
        if max(p["height"] for p in plans) <= bottom - top:
            break
    if len(plans) > 1:   # one figure size for both cards, so their figures share a baseline
        size = min(p["figure_px"] for p in plans)
        plans = [_stat_layout(probe, c, size, col_w - 8, scale * k, label_min=round(40 * k), num_min=size)
                 for c in cards]
    tallest = max(p["height"] for p in plans)
    # Round 4 (2026-11-02): on the 16:9 grid, every block left-aligned on its column's edge (the shared WIDE_LEFT,
    # x 153, for the first; the second on its own column's line), a single card's numeral cap line at about 34 %
    # of the frame height (the thirds grid), two cards' figures on one baseline in the title-safe middle.
    g = _WideGrid(width, height)
    for j, p in enumerate(plans):
        left = g.x(WIDE_LEFT) if j == 0 else bx0 + j * (col_w + gutter)
        p["x0"] = round(left)
        if len(plans) == 1:
            fig = next((r for r in p["rows"] if r["kind"] == "figure"), None)
            cap_line = p["num_y"] - cap_height(fig["font"]) if fig else 0
            p["y0"] = round(max(top - p["top"], min(bottom - p["bottom"], height * 0.34 - cap_line)))
        else:
            p["y0"] = round((top + bottom) / 2 - tallest / 2 - min(q["top"] for q in plans))
    return _stat_clip(cards, plans, bg, out, n, width, height)



# ------------------------------------------------------------ cross-section
# ------------------------------------------- vertical explanatory diagrams (2026-11-02 design review)
# The panel scored the 9:16 cross-section, rolling-stone door and ventilation diagrams 6, 3.5 and 3.5 out of 10:
# floating labels, a comb instead of a city, one view of a two-view mechanism, sky blue and red outside the brand.
# The rebuilt vertical branches share these helpers: a grid that maps the 1080x1920 design onto any 9:16 frame's
# Reel text box (65-888 x 330-1058 at full size), antialiased vector layers (drawn at 2x, downsampled), carved
# rock (one INK mask with a GOLD_DIM edge grown around its union, so joined rooms share one outline) and every
# word a measured TextPlate planned on the clip's final art.
_SS = 2  # supersampling of the diagrams' vector layers


class _Grid:
    """Design coordinates (the 1080x1920 frame as the ads Reel box laid it out: words from the inset edge at x 100,
    text box 65-888 x 330-1058) on this frame: design x 100 on the shared inset edge (inset_x0), y 330 on the text
    box's top, at the frame's own scale. QF-24: the scale was the design box fitted into the text box, so the
    organic box (735 px wide, 88 fewer) would have set every word 11% smaller; a layout reads its own right edge
    and foot from the grid instead (ix(bx1), iy(by1))."""
    DESIGN_X0 = 100

    def __init__(self, width: int, height: int):
        self.bx0, self.by0, self.bx1, self.by1 = get_text_box(width, height)
        self.k = min(width / VIDEO_WIDTH, height / VIDEO_HEIGHT)
        self.ox = inset_x0(width, height)

    def x(self, v: float) -> float:
        return self.ox + (v - self.DESIGN_X0) * self.k

    def y(self, v: float) -> float:
        return self.by0 + (v - 330) * self.k

    def s(self, v: float) -> float:
        return v * self.k

    def f(self, px: float) -> int:
        return max(10, round(px * self.k))

    def pts(self, pts: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(self.x(px), self.y(py)) for px, py in pts]

    def ix(self, px: float) -> float:
        """The design x of frame x `px`."""
        return self.DESIGN_X0 + (px - self.ox) / self.k

    def iy(self, py: float) -> float:
        """The design y of frame y `py`."""
        return 330 + (py - self.by0) / self.k


class _Sprite:
    """An RGBA layer cropped to its ink, composited at a fade and an offset."""

    def __init__(self, im: Image.Image, xy: tuple[int, int] = (0, 0)):
        bb = im.getbbox() or (0, 0, 1, 1)
        self.im = im.crop(bb)
        self.xy = (int(xy[0] + bb[0]), int(xy[1] + bb[1]))
        self._alpha = self.im.getchannel("A")

    def draw(self, im: Image.Image, alpha: float = 1.0, dx: float = 0, dy: float = 0) -> None:
        if alpha <= 0.004:
            return
        src = self.im
        if alpha < 0.996:
            src = src.copy()
            src.putalpha(self._alpha.point(lambda v, a=alpha: int(v * a)))
        im.alpha_composite(src, (max(0, round(self.xy[0] + dx)), max(0, round(self.xy[1] + dy))))


class _Pen:
    """Antialiased drawing over `box` (frame px): shapes at _SS x, downsampled into a _Sprite."""

    def __init__(self, box: tuple[float, float, float, float], mode: str = "RGBA"):
        self.x0, self.y0 = math.floor(box[0]), math.floor(box[1])
        self.size = (max(1, math.ceil(box[2]) - self.x0), max(1, math.ceil(box[3]) - self.y0))
        self.im = Image.new(mode, (self.size[0] * _SS, self.size[1] * _SS), 0 if mode == "L" else (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im, "RGBA" if mode == "RGBA" else None)

    def p(self, pts: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
        return [((px - self.x0) * _SS, (py - self.y0) * _SS) for px, py in pts]

    def line(self, pts, fill, width: float, caps: bool = True) -> None:
        q = self.p(pts)
        w = max(1, round(width * _SS))
        if len(q) > 1:
            self.d.line(q, fill=fill, width=w, joint="curve")
        if caps:
            r = w / 2
            for px, py in (q[0], q[-1]):
                self.d.ellipse([px - r, py - r, px + r, py + r], fill=fill)

    def poly(self, pts, fill=None, outline=None, width: float = 1) -> None:
        self.d.polygon(self.p(pts), fill=fill, outline=outline, width=max(1, round(width * _SS)))

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, fill=None, outline=None, width: float = 1) -> None:
        (x0, y0), (x1, y1) = self.p([(cx - rx, cy - ry), (cx + rx, cy + ry)])
        self.d.ellipse([x0, y0, x1, y1], fill=fill, outline=outline, width=max(1, round(width * _SS)))

    def rect(self, x0: float, y0: float, x1: float, y1: float, fill=None, outline=None, width: float = 1,
             radius: float = 0) -> None:
        (a, b), (c, e) = self.p([(x0, y0), (x1, y1)])
        self.d.rounded_rectangle([a, b, c, e], radius=radius * _SS, fill=fill, outline=outline,
                                 width=max(1, round(width * _SS)) if outline is not None else 0)

    def image(self) -> Image.Image:
        return self.im.resize(self.size, Image.LANCZOS)

    def sprite(self) -> _Sprite:
        return _Sprite(self.image(), (self.x0, self.y0))


def _carve(box: tuple[float, float, float, float], paint, edge_px: float, fill=brand.INK,
           edge=brand.GOLD_DIM) -> _Sprite:
    """Rock cut away: `paint(pen)` draws the openings as white on a mask; the union is filled with `fill` and
    outlined `edge_px` wide in `edge` (grown around the union, so a passage joins its room without a seam)."""
    pen = _Pen(box, "L")
    paint(pen)
    mask = pen.im
    grow = max(3, round(edge_px * _SS) * 2 + 1)
    edge_mask = mask.filter(ImageFilter.MaxFilter(grow | 1))
    big = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    big.paste(Image.new("RGBA", mask.size, (*edge, 255)), (0, 0), edge_mask)
    big.paste(Image.new("RGBA", mask.size, (*fill, 255)), (0, 0), mask)
    return _Sprite(big.resize(pen.size, Image.LANCZOS), (pen.x0, pen.y0))


def _rock_ground(width: int, height: int, ground_y, seed: int = 3) -> Image.Image:
    """The frame's ground: INK sky over dark banded rock below `ground_y(x)` (an array function of x, px), a
    faint STONE_LT surface line. Everything stays dark, so nothing outside the text box reads as text."""
    import numpy as np
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)[:, None]
    gy = np.asarray(ground_y(xs), dtype=np.float32)[None, :]
    rng = np.random.default_rng(seed)
    sky = np.zeros((height, width, 3), np.float32) + np.array(brand.INK, np.float32)
    sky += (ys / height)[..., None] * np.array([3, 3, 4], np.float32)
    depth = np.clip((ys - gy) / max(1.0, height * 0.55), 0, 1)
    band = (np.sin(ys / (9.0 * width / 1080) + 0.7 * np.sin(xs[None, :] / (130.0 * width / 1080))) * 2.2
            + np.sin(ys / (31.0 * width / 1080)) * 2.0)
    top = np.array([36, 31, 27], np.float32)
    deep = np.array([18, 17, 17], np.float32)
    rock = top + (deep - top) * depth[..., None] + band[..., None]
    rock += rng.normal(0, 1.1, (height, width, 1)).astype(np.float32)
    below = (ys >= gy)[..., None]
    img = np.where(below, rock, sky)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def _ease_io(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def _lantern(r: float) -> _Sprite:
    """A gold point of light: a soft glow, a gold core and a bone centre."""
    import numpy as np
    size = int(r * 10) | 1
    c = size / 2
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    d = np.hypot(xx - c, yy - c)
    glow = 110 * np.exp(-(d / (r * 2.2)) ** 2)
    im = np.zeros((size, size, 4), np.uint8)
    im[..., 0], im[..., 1], im[..., 2] = brand.GOLD
    im[..., 3] = np.clip(glow, 0, 255).astype(np.uint8)
    out = Image.fromarray(im, "RGBA")
    pen = _Pen((0, 0, size, size))
    pen.ellipse(c, c, r, r, fill=(*brand.GOLD, 255))
    pen.ellipse(c, c, r * 0.42, r * 0.42, fill=(*brand.BONE, 255))
    out.alpha_composite(pen.image())
    return _Sprite(out, (-size // 2, -size // 2))


def _vault(x0: float, x1: float, floor: float, h: float, seed: int, flat: float = 2.4) -> list[tuple[float, float]]:
    """A hand-cut room: a flat floor and an irregular rounded vault `h` high (`flat`: the larger, the flatter its
    crown; 2.4 a rounded vault, 5 a low flat-roofed chamber)."""
    import random
    rnd = random.Random(seed)
    p1, p2, a1 = rnd.uniform(0, 6.28), rnd.uniform(0, 6.28), rnd.uniform(0.03, 0.07)
    tilt = rnd.uniform(-0.14, 0.14)
    pts = []
    steps = 28
    for i in range(steps + 1):
        u = i / steps
        lift = max(0.0, 1 - abs(2 * u - 1) ** flat) ** 0.55
        wob = 1 + a1 * math.sin(3 * math.pi * u + p1) + 0.025 * math.sin(9 * math.pi * u + p2)
        pts.append((x0 + (x1 - x0) * u, floor - h * lift * wob * (1 + tilt * (u - 0.5))))
    return pts + [(x1, floor), (x0, floor)]


def _sub_path(pts, lens, a: float, b: float) -> list[tuple[float, float]]:
    out, acc = [], 0.0
    for i, seg_len in enumerate(lens):
        s0, s1 = acc, acc + seg_len
        if s1 >= a and s0 <= b and seg_len > 0:
            u0, u1 = max(0.0, (a - s0) / seg_len), min(1.0, (b - s0) / seg_len)
            p, q = pts[i], pts[i + 1]
            start = (p[0] + (q[0] - p[0]) * u0, p[1] + (q[1] - p[1]) * u0)
            end = (p[0] + (q[0] - p[0]) * u1, p[1] + (q[1] - p[1]) * u1)
            if not out:
                out.append(start)
            out.append(end)
        acc = s1
    return out


def _path_len(pts: list[tuple[float, float]]) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _plan(ground: Image.Image, xy, text: str, font, fill, anchor: str = "la", track: float = 0) -> Any:
    """plan_text, stepping the font up 1 px while the glyphs are too thin to measure (a 540x960 test frame's
    14 px type leaves typography.ring_contrast no eroded interior to read); full-size frames plan unchanged."""
    while True:
        try:
            return plan_text(ground, xy, text, font, fill, anchor, track=track)
        except TypeError:
            if font.size >= 40:
                raise
            family = font.getname()[0] or Path(font.path).stem.split("-")[0]   # "Montserrat", "IBM Plex Mono"
            weight = 700 if family == "Montserrat" else 600 if family == brand.DATA_FONT else None
            font = get_font(family, font.size + 1, weight=weight)


def _fit_title(g: _Grid, title: str, width_px: float) -> tuple[list[str], Any]:
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, font, short = probe_text(probe, title, "Anton", g.f(60), int(width_px), 1, g.f(56), sentences=False,
                                  quiet=True)
    if short or probe.textlength(lines[0] if lines else "", font=font) > width_px:
        lines, font, _ = fit_text(probe, title, "Anton", g.f(60), int(width_px), 2, g.f(56), sentences=False,
                                  quiet=True)
    return lines, font


def _plates_on(frame: Image.Image, plates) -> Image.Image:
    for plate, alpha, dy in plates:
        if alpha > 0.004:
            frame = plate.draw(frame, min(1.0, alpha), dy)
    return frame


@dataclass(frozen=True)
class _SectionLayout:
    """Where the cutaway's parts sit, in its grid's design px: the 9:16 column (the Reel text box, 65-888) or the
    16:9 frame (1920 x 1080, title-safe 153-1766 x 129-820)."""
    title_x: float
    title_w: float
    title_base: float
    title_step: float
    title_px: tuple[int, int]
    ruler_x: float
    shaft_x: float
    shaft_w: float
    rooms_x1: float
    label_x: float
    right: float
    bottom_d: float
    axis_limit: float
    label_hi: float
    surface_gap: float
    fig_px: int
    deep_px: int
    lab_px: int
    small_px: int
    line_step: float
    label_gap: float
    fan_gap: float
    room_start: tuple[float, float]
    room_w: tuple[float, float]
    room_gap: tuple[float, float]
    niche_w: tuple[float, float]
    room_h_max: float
    turns: tuple[float, float, float]
    bends: tuple[float, float, float]
    approx_inline: bool = False      # "APPROX. DEPTHS" after the title on its baseline, not under it
    ruler_fill: tuple[int, int, int] = brand.STONE_LT
    label_lo: float = 14              # the first label block's top below the ground line


_SECTION_TALL = _SectionLayout(
    title_x=99, title_w=789, title_base=382, title_step=64, title_px=(60, 56), ruler_x=186, shaft_x=260, shaft_w=22,
    rooms_x1=506, label_x=546, right=888, bottom_d=985, axis_limit=1040, label_hi=1052, surface_gap=86,
    fig_px=44, deep_px=68, lab_px=30, small_px=28, line_step=36, label_gap=42, fan_gap=14, room_start=(10, 24),
    room_w=(52, 96), room_gap=(14, 30), niche_w=(36, 50), room_h_max=54, turns=(326, 472, 400),
    bends=(518, 530, 536))   # on the shared inset edge (2026-11-02): title and ruler figures from x 100
_SECTION_TALL_FOOT = 1058    # the design foot _SECTION_TALL's bottom_d, axis_limit and label_hi were set against
# The 16:9 master's vignette (video_composer: vignette=PI/4.2, aspect 16/9) takes the frame's edges to about half
# their brightness, so the wide layouts keep their words inside x 400-1600 and y 190-800, where a bone or gold
# line still measures 4.5:1 on the graded frame (the 153 px title-safe margin alone left text at 2.7:1).
# Round 4 (2026-11-02): on the shared 16:9 grid (the master lifts its vignette under graphics, so the x 400-1600
# limit above is stale): the title and caption on WIDE_LEFT, the ruler just inside it, the city of rooms widened
# to x 1250 with wider rooms and gaps, the label column ending on WIDE_RIGHT.
_SECTION_WIDE = _SectionLayout(
    title_x=WIDE_LEFT, title_w=1000, title_base=252, title_step=72, title_px=(68, 56), ruler_x=250, shaft_x=330,
    shaft_w=28, rooms_x1=1250, label_x=1330, right=WIDE_RIGHT, bottom_d=790, axis_limit=812, label_hi=818,
    surface_gap=146, fig_px=54, deep_px=80, lab_px=34, small_px=28, line_step=42, label_gap=44, fan_gap=20,
    room_start=(14, 30), room_w=(100, 170), room_gap=(26, 50), niche_w=(46, 62), room_h_max=58,
    turns=(895, 1192, 1044), bends=(1270, 1296, 1316), approx_inline=False, ruler_fill=brand.BONE,
    label_lo=30)   # round 3: the depth caption under the title, as on 9:16 (inline it graded to 4.88); the axis
#                    down to y 790 and labels to 818, inside title-safe (820), for the room the caption took


class _WideGrid(_Grid):
    """Design coordinates on the 1920 x 1080 frame (16:9 title-safe text box 153-1766 x 129-820), scaled to this
    landscape frame and centred on it."""

    def __init__(self, width: int, height: int):
        self.bx0, self.by0, self.bx1, self.by1 = get_text_box(width, height)
        self.k = min(width / 1920, height / 1080)
        self.ox, self.oy = (width - 1920 * self.k) / 2, (height - 1080 * self.k) / 2

    def x(self, v: float) -> float:
        return self.ox + v * self.k

    def y(self, v: float) -> float:
        return self.oy + v * self.k

    def ix(self, px: float) -> float:
        return (px - self.ox) / self.k


def _render_cross_section_vertical(cs: dict[str, Any], levels: list[dict[str, Any]], total: float, out: Path,
                                   width: int, height: int) -> Path | None:
    """The 9:16 cutaway (2026-11-02 design review, 6/10 -> rebuilt): a linear depth axis from the ground near the
    top of the text box to the total depth at its bottom, a ruler at the left (a tick every 50 ft, running on to
    the next tick past the deepest level), rooms cut as irregular vaults branching off an accent shaft with sloped
    passages between levels (a city, not a comb), labels fanned out on leaders so the shallow ones never overlap,
    and a gold lantern that descends the shaft, each level's label fading in as it arrives and dimming as it
    moves on, so the deepest figure ends brightest.

    QF-24: _SECTION_TALL was drawn for the ads box (right edge 888, foot 1058). On the organic box the label column
    ends on the box's right edge and the depth axis and labels run down to its foot (1090), the type at its size."""
    grid = _Grid(width, height)
    right, foot = grid.ix(grid.bx1), grid.iy(grid.by1)
    extra = foot - _SECTION_TALL_FOOT
    layout = dataclasses.replace(_SECTION_TALL, right=right, title_w=right - _SECTION_TALL.title_x,
                                 bottom_d=_SECTION_TALL.bottom_d + extra, axis_limit=_SECTION_TALL.axis_limit + extra,
                                 label_hi=_SECTION_TALL.label_hi + extra)
    return _render_cross_section(cs, levels, total, out, width, height, grid, layout)


def _render_cross_section_wide(cs: dict[str, Any], levels: list[dict[str, Any]], total: float, out: Path,
                               width: int, height: int) -> Path | None:
    """The 16:9 cutaway (2026-11-02 review, 4-5.5/10: the old design, its 15 and 35 ft levels pushed apart off
    the depth scale): the 9:16 rebuild composed for the wide frame. The ruler at the left, the shaft and a city
    of rooms running wide across the centre-left on the same true-depth axis, the levels' figures and labels in
    a column at the right on leaders, the lantern descending the shaft."""
    return _render_cross_section(cs, levels, total, out, width, height, _WideGrid(width, height), _SECTION_WIDE)


SECTION_CAPTION = "ILLUSTRATIVE · APPROX. DEPTHS"   # round 3: the chambers and their extent are schematic
ACCESS_W = 7                 # round 4: design px, the access line that replaces the full-depth shaft
LABEL_AFTER_CONTACT = 0.08   # s: a level's label and leader follow the lantern's contact by 2-3 frames
SECTION_DRIFT = 0.02         # round 4: the drift's reach (1.02 -> 1.00 over the end hold, capped to stay in the box)


def section_drift(t: float, hold: float, t_last: float, drift_max: float) -> float:
    """The cross-section's scale at `t`: `drift_max` (at most 1 + SECTION_DRIFT) through the descent, easing back
    to exactly 1.0 over the end hold, so no frame is frozen and the held last frame is the planned layout (the
    one its words and marks are measured on). Round 4 review (2026-11-02): it drifted 1.0 -> 1.02 and the record
    described the frame at rest, which was never shown; never under 1.0, so no edge of the art is uncovered."""
    if drift_max <= 1.0 or t_last <= hold:
        return 1.0
    return drift_max - (drift_max - 1.0) * _ease_io(min(1.0, max(0.0, (t - hold) / (t_last - hold))))


def contact_pulse(dt: float) -> float:
    """The lantern's size `dt` s after it reaches a level: 1 -> 1.4 -> 1 over 180 ms, once."""
    return 1.0 + 0.4 * math.sin(math.pi * dt / 0.18) if 0.0 < dt < 0.18 else 1.0
SECTION_LABEL_CLEAR = 24                             # design px: the first label block's least gap under the ground
# Chamber profiles by depth band (round 3, 2026-11-02; the same rounded bump at every level read as a comb):
# low, wide, flat-roofed chambers near the surface, taller halls with rock pillars left standing mid-depth, fewer
# and larger rooms at the deepest level. Width and gap scale the layout's ranges; `h` its room height; `flat`
# the vault's crown (_vault). Driven by depth only, never by the labels' words.
SECTION_PROFILES = {"shallow": {"w": 1.35, "gap": 0.8, "h": 0.72, "flat": 5.0},
                    "mid": {"w": 0.85, "gap": 0.55, "h": 1.0, "flat": 2.4},
                    "deep": {"w": 1.7, "gap": 1.3, "h": 1.12, "flat": 1.8}}


def section_band(depth_ft: float, total: float, deepest: bool) -> str:
    """A level's depth band: the deepest level is "deep", the top quarter "shallow", the rest "mid"."""
    if deepest:
        return "deep"
    return "shallow" if depth_ft <= 0.25 * total else "mid"


def label_stack(wants: list[float], heights: list[float], lo: float, hi: float, gap: float,
                weights: list[float] | None = None) -> list[float]:
    """Label blocks' tops as near their wanted tops as the stack allows (round 3: least squares, not the greedy
    top-down push that moved every block below a crowded pair off its floor): each block at least `gap` under
    the one above, the first never above `lo` (a hard floor under the ground line), the last ending by `hi`. In
    offset coordinates the order constraint is monotone, solved by pooling adjacent violators and clipping to the
    bounds (`weights`: how strongly each block keeps its place; a heavy one moves its light neighbours instead);
    when the stack cannot fit, the gaps shrink to 4 px before anything moves above `lo`."""
    n = len(wants)
    if not n:
        return []
    for g in (gap, gap * 0.6, gap * 0.3, 4.0):
        offs = [0.0]
        for k in range(1, n):
            offs.append(offs[-1] + heights[k - 1] + g)
        upper = hi - heights[-1] - offs[-1]
        if upper >= lo or g == 4.0:
            break
    z = [w - o for w, o in zip(wants, offs)]
    ws = list(weights) if weights else [1.0] * n
    pools: list[list[float]] = []                      # [weighted mean, weight, count]
    for v, wt in zip(z, ws):
        pools.append([v, wt, 1])
        while len(pools) > 1 and pools[-2][0] > pools[-1][0]:
            m2, w2, c2 = pools.pop()
            m1, w1, c1 = pools.pop()
            pools.append([(m1 * w1 + m2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    fit = [m for m, _w, c in pools for _ in range(int(c))]
    fit = [max(lo, min(max(lo, upper), v)) for v in fit]
    return [v + o for v, o in zip(fit, offs)]


def _render_cross_section(cs: dict[str, Any], levels: list[dict[str, Any]], total: float, out: Path,
                          width: int, height: int, g: _Grid, L: _SectionLayout) -> Path | None:
    """The cutaway on grid `g` with layout `L` (see the two orientations above)."""
    import itertools
    import random
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    probe_t = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    title = cs["subject"].upper()
    title_lines, f_title, short = probe_text(probe_t, title, "Anton", g.f(L.title_px[0]), int(S(L.title_w)), 1,
                                           g.f(L.title_px[1]), sentences=False, quiet=True)
    if short or probe_t.textlength(title_lines[0] if title_lines else "", font=f_title) > S(L.title_w):
        title_lines, f_title, _ = fit_text(probe_t, title, "Anton", g.f(L.title_px[0]), int(S(L.title_w)), 2,
                                           g.f(L.title_px[1]), sentences=False, quiet=True)
    title_base = [L.title_base + L.title_step * j for j in range(len(title_lines))]
    approx = cs.get("approximate", True)
    cap_base = title_base[-1] + 46
    ground_d = (cap_base if not L.approx_inline else title_base[-1] + 20) + 32   # design y of the ground
    tick = 50 if total <= 400 else 100 if total <= 900 else 250
    axis_end = math.ceil(total / tick) * tick                       # the ruler runs on to the next tick (2026-11-02)
    scale = min((L.bottom_d - ground_d) / total, (L.axis_limit - ground_d) / axis_end)
    bottom_d = ground_d + total * scale                             # design y of the total depth

    def dy(ft: float) -> float:
        return ground_d + ft * scale

    ruler_x, shaft_x, shaft_w = L.ruler_x, L.shaft_x, L.shaft_w
    rooms_x1, label_x = L.rooms_x1, L.label_x
    f_fig = get_font("Anton", g.f(L.fig_px))
    f_deep = get_font("Anton", g.f(L.deep_px))
    f_lab = get_font("Montserrat", g.f(L.lab_px), weight=600)
    f_small = get_font("Montserrat", g.f(L.small_px), weight=600)
    # QF-24: a level's feet and metres share one line; the label column starts where the widest such line still
    # ends on the column's right edge (the organic box's is 60 design px further in than the ads box's), and the
    # rooms keep their gap to it. A narrower column moves, never clips a unit or shrinks the type.
    inline_w = max((probe.textbbox((0, 0), f"{lv['depth_ft']:,.0f} FT", font=f_deep if k == len(levels) - 1 else f_fig,
                                   anchor="ls")[2] + S(14)
                    + probe.textbbox((0, 0), f"{lv['depth_ft'] / 3.28084:,.0f} M", font=f_lab, anchor="ls")[2])
                   for k, lv in enumerate(levels))
    label_x = min(label_x, L.right - (math.ceil(inline_w) + 1) / g.k)
    rooms_x1 = min(rooms_x1, label_x - (L.label_x - L.rooms_x1))
    lab_w = S(L.right - label_x)
    left_turn, right_turn, mid_turn = L.turns

    # ---- the city: rooms per level, a passage from the shaft, ramps between levels
    plan = []
    prev_floor, prev_rooms = ground_d, []
    for k, lv in enumerate(levels):
        rnd = random.Random(31 + 7 * k)
        floor = dy(lv["depth_ft"])
        band = section_band(lv["depth_ft"], total, k == len(levels) - 1)
        prof = SECTION_PROFILES[band]
        room_h = max(20.0, min(L.room_h_max * prof["h"], (floor - prev_floor) * 0.7))
        x = shaft_x + shaft_w / 2 + rnd.uniform(*L.room_start)
        rooms = []      # (x0, x1, floor, height, seed, flat): each room's floor a few px off the level's
        while x < rooms_x1 - 40:
            w = rnd.uniform(*L.room_w) * prof["w"]
            if x + w > rooms_x1 or rooms_x1 - (x + w) < 40:
                w = rooms_x1 - x
            rooms.append((x, x + w, floor - rnd.uniform(0, 4), room_h * rnd.uniform(0.7, 1.0), 100 * k + len(rooms),
                          prof["flat"]))
            x += w + rnd.uniform(*L.room_gap) * prof["gap"]
        if room_h > 24 and rnd.random() < 0.75:                       # a niche on the shaft's other side
            w = rnd.uniform(*L.niche_w)
            rooms.append((shaft_x - shaft_w / 2 - w, shaft_x - shaft_w / 2 + 2, floor, room_h * 0.6, 100 * k + 50, 2.4))
        passage_h = min(11.0, room_h * 0.5)
        ramp, joints = [], []
        if k and prev_rooms and floor - room_h - prev_floor > 40:   # close levels share the shaft only
            gap = floor - room_h - prev_floor
            legs = max(1, round(gap / 80))
            src = max(prev_rooms, key=lambda r: r[1])
            xa = (src[0] + src[1]) / 2
            ramp = [(xa, prev_floor - 3)]
            for j in range(legs):
                xb = (left_turn + rnd.uniform(-8, 12) if (j % 2 == 0) == (xa > mid_turn) else
                      right_turn + rnd.uniform(-12, 8))
                yb = prev_floor + (floor - 3 - prev_floor) * (j + 1) / legs
                ramp.append((xb, yb))
                if j < legs - 1:     # round 4: a plain bend, a little wider at the joint (no vault: it read as a level)
                    joints.append((xb, yb))
                xa = xb
        plan.append({"floor": floor, "rooms": rooms, "joints": joints, "passage_h": passage_h, "ramp": ramp,
                     "end": max(r[1] for r in rooms) if rooms else shaft_x + 40,
                     "reach": max((r[1] for r in rooms if r[0] > shaft_x), default=shaft_x + 40)})
        prev_floor, prev_rooms = floor, rooms

    carve_box = (X(ruler_x + 8), Y(ground_d - 6), X(rooms_x1 + 8), Y(bottom_d + 14))

    def paint_upto(last: int):
        def paint(pen: _Pen) -> None:
            # Round 4 (2026-11-02): no payload states a shaft from the ground to the total depth, so none is drawn:
            # a narrow access line from the surface joins the levels' passages, down to the deepest level only.
            deepest_floor = plan[-1]["floor"] if plan else bottom_d
            pen.rect(X(shaft_x - ACCESS_W / 2), Y(ground_d - 2), X(shaft_x + ACCESS_W / 2), Y(deepest_floor), fill=255)
            for p in plan[:last + 1]:
                fl = p["floor"]
                pen.poly(g.pts([(shaft_x, fl - p["passage_h"]), (p["reach"] - 8, fl - p["passage_h"] - 2),
                                (p["reach"] - 8, fl - 2), (shaft_x, fl)]), fill=255)
                for x0, x1, f0, h, seed, flat in p["rooms"]:
                    pen.poly(g.pts(_vault(x0, x1, f0, h, seed, flat)), fill=255)
                if p["ramp"]:
                    pen.line(g.pts(p["ramp"]), 255, S(7))
                for jx, jy in p["joints"]:
                    pen.ellipse(X(jx), Y(jy), S(6), S(6), fill=255)
        return paint

    carves = [_carve(carve_box, paint_upto(k), S(3)) for k in range(-1, len(levels))]   # [shaft only, +lvl0, ...]

    # ---- label blocks fanned out between the ground and the box's bottom (round 3, 2026-11-02): each block as
    # near its floor as the stack allows (label_stack), a level with room above it held on its floor, the first
    # block never above SECTION_LABEL_CLEAR under the ground; a stack that cannot do that tightens its gaps, then
    # its label leading, then takes one 2 px step of label type.
    lo, hi = ground_d + max(L.label_lo, SECTION_LABEL_CLEAR), L.label_hi

    def build(lab_px: int, label_gap: float, line_step: float) -> list[dict[str, Any]]:
        out_blocks = []
        for k, (lv, p) in enumerate(zip(levels, plan)):
            deepest = k == len(levels) - 1
            ff = f_deep if deepest else f_fig
            lines, _f, overflow = probe_text(probe, lv["label"], "Montserrat", g.f(lab_px), int(lab_w), 2, g.f(lab_px),
                                             weight=600, quiet=True)
            if overflow:   # QF-23: three lines before the graphic is refused (it was cut on two with an ellipsis)
                lines, _f, _ = fit_text(probe, lv["label"], "Montserrat", g.f(lab_px), int(lab_w), 3,
                                        g.f(lab_px), weight=600, quiet=True)
            capf = cap_height(ff) / g.k
            h = capf + label_gap + line_step * (len(lines) - 1) + 8
            out_blocks.append({"font": ff, "lines": lines, "cap": capf, "h": h, "three": overflow,
                               "want": p["floor"] - p["passage_h"] / 2})
        return out_blocks
    best = None
    for fan, lgap, dpx in ((1.0, 1.0, 0), (0.6, 1.0, 0), (0.3, 1.0, 0), (0.3, 0.8, 0), (0.3, 0.8, 2)):
        label_gap, line_step = L.label_gap * lgap, L.line_step - dpx * 1.2
        blocks = build(L.lab_px - dpx, label_gap, line_step)
        wants = [b["want"] - b["cap"] / 2 for b in blocks]
        anchored = [k > 0 and blocks[k]["want"] - blocks[k - 1]["want"] >= blocks[k - 1]["h"]
                    for k in range(len(blocks))]
        tops = label_stack(wants, [b["h"] for b in blocks], lo, hi, L.fan_gap * fan,
                           weights=[1000.0 if a else 1.0 for a in anchored])
        off = max([abs(t - w) for t, w, a in zip(tops, wants, anchored) if a] or [0.0])
        if best is None or off <= 4:     # a tighter stack only where it puts every such level on its floor
            best = (off, blocks, tops, label_gap, line_step, dpx)
        if off <= 4:
            break
    _off, blocks, tops, label_gap, line_step, dpx = best
    if any(b["three"] for b in blocks) and tops and tops[-1] + blocks[-1]["h"] > hi + 0.5:
        # QF-23: a label two lines held only cut takes three, and then the stack does not fit the column even at its
        # tightest: refused rather than drawn past the text box (it was cut to fit before); stacks of labels that two
        # lines hold whole are placed exactly as before
        raise TextFitError(" | ".join(str(lv["label"]) for lv in levels), where="the cross-section's label column")
    f_lab = get_font("Montserrat", g.f(L.lab_px - dpx), weight=600)

    # ---- the static ground: sky, banded rock, the surface, the ruler
    ground_px = Y(ground_d)
    axis_px = Y(dy(axis_end))
    base = _rock_ground(width, height, lambda xs: xs * 0 + ground_px)
    pen = _Pen((0, Y(ground_d - 8), width, max(Y(bottom_d), axis_px) + S(16)))
    surface = (*brand.STONE_LT, 190)                           # it runs past the text box: kept dim
    pen.line([(0, ground_px), (X(L.surface_gap), ground_px)], surface, S(3), caps=False)   # gapped for "0 FT"
    pen.line([(X(ruler_x - 16), ground_px), (width, ground_px)], surface, S(3), caps=False)
    pen.line([(X(ruler_x), ground_px), (X(ruler_x), axis_px)], (*brand.GOLD_DIM, 255), S(2), caps=False)
    for ft in range(0, int(axis_end) + 1, 10 if total <= 400 else tick // 5):
        major = ft % tick == 0
        yy = Y(dy(ft))
        pen.line([(X(ruler_x - (16 if major else 7)), yy), (X(ruler_x), yy)],
                 (*(brand.STONE_LT if major else brand.GOLD_DIM), 255), S(2 if major else 1.5), caps=False)
    base.alpha_composite(pen.image(), (pen.x0, pen.y0))

    # ---- leaders, registration ticks, the lantern and its route
    leaders, registers = [], []
    b0, b1, b2 = L.bends
    for k, (p, b) in enumerate(zip(plan, blocks)):
        deepest = k == len(levels) - 1
        y0 = p["floor"] - p["passage_h"] / 2
        y1 = tops[k] + b["cap"] / 2
        lp = _Pen((X(p["reach"] - 12), Y(min(y0, y1) - 8), X(label_x), Y(max(y0, y1) + 8)))
        col = (*(brand.GOLD if deepest else brand.GOLD_DIM), 255)
        lp.line(g.pts([(p["reach"] - 4, y0), (b0, y0), (b1, y1), (b2, y1)]), col, S(2.75))
        lp.ellipse(X(p["reach"] - 4), Y(y0), S(4), S(4), fill=col)
        leaders.append(lp.sprite())
        # registration (round 3): a tick on the ruler at the level's true depth and its floor carried left from
        # the shaft to it, so each figure is read against its own place on the scale
        fy = dy(levels[k]["depth_ft"])
        rp = _Pen((X(ruler_x - 14), Y(fy) - S(4), X(shaft_x), Y(fy) + S(4)))
        rp.line([(X(ruler_x - 12), Y(fy)), (X(ruler_x + 10), Y(fy))], col, S(2.5), caps=False)
        rp.line([(X(ruler_x + 10), Y(fy)), (X(shaft_x - shaft_w / 2), Y(fy))], (*brand.GOLD_DIM, 200), S(1.25),
                caps=False)
        registers.append(rp.sprite())
    lanterns: dict[int, _Sprite] = {}

    def lantern_at(scale: float) -> _Sprite:
        key = round(scale * 20)
        if key not in lanterns:
            lanterns[key] = _lantern(S(10) * key / 20)
        return lanterns[key]
    keys = [(0.55, ground_d - 16)]
    arrive = []
    for k, p in enumerate(plan):
        y_stop = p["floor"] - p["passage_h"] / 2
        travel = 0.34 + 0.95 * (y_stop - keys[-1][1]) / (bottom_d - ground_d)
        keys.append((keys[-1][0] + travel, y_stop))
        arrive.append(keys[-1][0])
        keys.append((keys[-1][0] + 0.26, y_stop))

    def lantern_y(t: float) -> float:
        if t <= keys[0][0]:
            return keys[0][1]
        for (t0, y0), (t1, y1) in itertools.pairwise(keys):
            if t <= t1:
                return y0 + (y1 - y0) * _ease_io((t - t0) / max(1e-6, t1 - t0))
        return keys[-1][1]

    def art(t: float, final: bool = False) -> Image.Image:
        im = base.copy()
        a_shaft = 1.0 if final else _ease((t - 0.15) / 0.45)
        carves[0].draw(im, a_shaft)
        for k in range(len(levels)):
            a = 1.0 if final else _ease((t - arrive[k] + 0.18) / 0.4)
            carves[k + 1].draw(im, a)
        ly = lantern_y(1e9 if final else t)
        d = ImageDraw.Draw(im, "RGBA")
        if ly > ground_d:   # the lantern's trail: a GOLD_DIM access line, never an accent-coloured shaft
            d.rectangle([X(shaft_x) - S(1.5), Y(ground_d - 2), X(shaft_x) + S(1.5), Y(ly)], fill=(*brand.GOLD_DIM, 255))
        for k in range(len(levels)):
            leaders[k].draw(im, 1.0 if final else _ease((t - arrive[k] - LABEL_AFTER_CONTACT) / 0.35))
            registers[k].draw(im, 1.0 if final else _ease((t - arrive[k] + 0.05) / 0.3))
        pulse = 1.0 if final else max([contact_pulse(t - at) for at in arrive] or [1.0])
        glow = 1.0 if final or not arrive or t < arrive[-1] + 0.3 else 0.82 + 0.18 * math.cos(2.4 * (t - arrive[-1] - 0.3))
        lantern_at(pulse).draw(im, a_shaft * glow, X(shaft_x), Y(ly))   # round 4: it breathes through the hold
        return im

    final = art(0, final=True)

    # ---- every word, planned on the final art
    timed = []   # (name, plate, fades in at, dims at, its dim twin)
    for j, (ln, yb) in enumerate(zip(title_lines, title_base)):
        timed.append((f"title{j}", _plan(final, (X(L.title_x), Y(yb)), ln, f_title, WHITE, "ls"), 0.1 + 0.08 * j,
                      None, None))
    caption = SECTION_CAPTION if approx else "ILLUSTRATIVE"    # round 3: the drawing is schematic either way
    if L.approx_inline:
        title_end = timed[-1][1].box[2]
        timed.append(("approx", _plan(final, (title_end + S(36), Y(title_base[-1])), caption, f_small, MUTED,
                                      "ls", track=S(3)), 0.3, None, None))
    else:
        timed.append(("approx", _plan(final, (X(L.title_x + 2), Y(cap_base)), caption, f_small, MUTED, "ls",
                                      track=S(3)), 0.3, None, None))
    f_ruler = get_font(brand.DATA_FONT, g.f(L.small_px - 2), weight=600)   # round 3: the axis in the data face
    for j, ft in enumerate(range(0, int(axis_end) + 1, tick)):
        if ft and 0 <= total - ft < tick * 0.3:
            continue                                                 # the deepest level names the bottom
        text = f"{ft} FT" if ft == 0 else f"{ft:,}"
        timed.append((f"ruler{j}", _plan(final, (X(ruler_x - 24), Y(dy(ft))), text, f_ruler, L.ruler_fill, "rm"),
                      0.25 + 0.05 * j, None, None))
    for k, (lv, b) in enumerate(zip(levels, blocks)):
        deepest = k == len(levels) - 1
        top = tops[k]
        fig_base = top + b["cap"]
        fig = f"{lv['depth_ft']:,.0f} FT"
        t_in = arrive[k] + LABEL_AFTER_CONTACT   # round 4: the label follows the contact (it led by 0.05 s)
        t_out = None if deepest else arrive[k + 1]
        bright = _plan(final, (X(label_x), Y(fig_base)), fig, b["font"], GOLD, "ls")
        dim = None if deepest else _plan(final, (X(label_x), Y(fig_base)), fig, b["font"], MUTED, "ls")
        timed.append((f"level{k}_depth", bright, t_in, t_out, dim))
        metres = _plan(final, (bright.box[2] + S(14), Y(fig_base)), f"{lv['depth_ft'] / 3.28084:,.0f} M",
                           f_lab, MUTED, "ls")
        timed.append((f"level{k}_metres", metres, t_in, None, None))
        for j, ln in enumerate(b["lines"]):
            yb = fig_base + label_gap + line_step * j
            bp = _plan(final, (X(label_x), Y(yb)), ln, f_lab, WHITE, "ls")
            dim = None if deepest else _plan(final, (X(label_x), Y(yb)), ln, f_lab, MUTED, "ls")
            timed.append((f"level{k}_label{j}", bp, t_in + 0.08, t_out, dim))

    last: list[Image.Image] = []
    # Round 4: the end hold drifts (SECTION_DRIFT), the whole composition scaled about its own centre, no further
    # than keeps every word and the drawing inside the text box, settling back to 1.0 by the last frame.
    boxes = [pl.box for _n, pl, _a, _b, _d in timed if not pl.empty]
    cx0_, cy0_ = min(b[0] for b in boxes), min(b[1] for b in boxes)
    cx1_, cy1_ = max(max(b[2] for b in boxes), X(rooms_x1)), max(max(b[3] for b in boxes), Y(bottom_d))
    centre = ((cx0_ + cx1_) / 2, (cy0_ + cy1_) / 2)
    bx0_, by0_, bx1_, by1_ = get_text_box(width, height)
    room = min((centre[0] - bx0_) / max(1.0, centre[0] - cx0_), (bx1_ - centre[0]) / max(1.0, cx1_ - centre[0]),
               (centre[1] - by0_) / max(1.0, centre[1] - cy0_), (by1_ - centre[1]) / max(1.0, cy1_ - centre[1]))
    hold = arrive[-1] + 0.3 if arrive else CLIP_SECONDS
    t_last = (n - 1) / VIDEO_FPS
    drift_max = max(1.0, min(1.0 + SECTION_DRIFT, room)) if t_last - hold >= 0.3 else 1.0

    def compose(t: float) -> Image.Image:
        im = art(t)
        for _name, plate, t_in, t_out, dim in timed:
            a = _ease((t - t_in) / 0.45)
            if a <= 0:
                continue
            fade = _ease((t - t_out) / 0.4) if dim is not None and t_out is not None else 0.0
            if fade < 1:
                im = plate.draw(im, a * (1 - fade), round(S(12) * (1 - a)))
            if fade > 0:
                im = dim.draw(im, fade)
        return im

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = _push_about(compose(t), section_drift(t, hold, t_last, drift_max), centre)
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        # the drift settles to 1.0 on the last frame (section_drift), so the frame measured is the frame shown
        record_contrast(out, last[0], [(name, dim if dim is not None else plate) for name, plate, _a, _b, dim in timed])
        marks = [("ruler", mark_mask((width, height), lambda d: d.line([(X(ruler_x), ground_px), (X(ruler_x), axis_px)],
                                                                         fill=255, width=max(1, round(S(2))))))]
        for k, sp in enumerate(leaders):
            m = Image.new("L", (width, height), 0)
            m.paste(sp.im.getchannel("A").point(lambda v: 255 if v > 127 else 0), sp.xy)
            marks.append((f"leader{k}", m))
        record_nontext(out, last[0], marks, base.convert("RGB"))
    return result


def render_cross_section(cs: dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path | None:
    """A cutaway: sky, ground line, strata, and a shaft that a gold marker descends; each sourced
    level appears as the marker reaches its depth (true proportion), labelled in ft and m.

    9:16 (2026-11-02 design review, scored 6/10): rebuilt in _render_cross_section_vertical as a city on a linear
    depth axis with a ruler, fanned leader labels and a lantern descending the shaft; 16:9 (round 2, 4-5.5/10)
    the same rebuild composed for the wide frame (_render_cross_section_wide)."""
    levels = sorted(cs["levels"], key=lambda lv: lv["depth_ft"])
    total = float(cs["total_depth_ft"])
    if total <= 0 or len(levels) < 2:
        return None
    if width <= height:
        return _render_cross_section_vertical(cs, levels, total, out, width, height)
    return _render_cross_section_wide(cs, levels, total, out, width, height)


# ---------------------------------------------------------------- route map
def great_circle(a: tuple[float, float], b: tuple[float, float], steps: int = 32) -> list[tuple[float, float]]:
    """`steps` + 1 (lon, lat) points along the great circle from `a` to `b` (both (lon, lat), degrees), so a long
    leg bows as it does on the globe instead of a chord (round 3, 2026-11-02)."""
    def vec(lon: float, lat: float) -> tuple[float, float, float]:
        lo, la = math.radians(lon), math.radians(lat)
        return math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)
    p, q = vec(*a), vec(*b)
    omega = math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(p, q)))))
    if omega < 1e-9:
        return [a] * (steps + 1)
    out = []
    for k in range(steps + 1):
        f = k / steps
        s0, s1 = math.sin((1 - f) * omega) / math.sin(omega), math.sin(f * omega) / math.sin(omega)
        x, y, z = (s0 * u + s1 * v for u, v in zip(p, q))
        out.append((math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))))
    return out


def _leader_box(leader: tuple[tuple[float, float], tuple[float, float]]) -> tuple[float, float, float, float]:
    (ax, ay), (bx, by) = leader
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


ROUTE_FIT = 0.80            # round 6: the stops' box centred in the graphics box with 10 % padding (0.74 before)
ROUTE_STOP_R = 12           # round 6: px, every stop the A.2 dot (Ø 24), the destination too (it was a 22 px pin)
ROUTE_LEG_W, ROUTE_LEG_CASE = 6, 2   # round 6: a leg's core (the run's accent) and its INK casing either side
ROUTE_BOW, ROUTE_BOW_MAX = 0.08, 0.12   # round 6: a leg bows this share of its length toward the sea (then the most)
ROUTE_ARROW_AT, ROUTE_ARROW_PX = 0.55, 18   # round 6: one arrowhead per leg, at this share of its length
ROUTE_LABEL_LAND_PX2 = 2000.0   # round 6: a stop's name keeps ROUTE_LABEL_LAND_AIR from other land masses this big
ROUTE_LABEL_LAND_AIR = 16
ROUTE_LEG_AIR = 16          # round 6: px a stop's name keeps from a leg's edge
ROUTE_NAME_AIR = 24         # round 4 review 4: px between two stops' names, over a name's own ~17 px line gap
ROUTE_NAME_MARGIN = 16      # round 4 review 6: px a stop's name is nearer its own stop than any other
ROUTE_CLUSTER_PX = 48       # round 4 review 6: stops closer than this are a cluster (a vague name accepted, logged)
ROUTE_FAR_REACH = 320       # round 4 review 7: px, the furthest a name is pushed off a stop with no clear place nearer
GLOBE_AIR = 16              # round 4 review 6: px the context globe keeps from a route's stops, legs and leaders


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def leader_end(stop: tuple[float, float], box: tuple[float, float, float, float], gap: float = 8) -> tuple[float, float]:
    """Where a stop's leader meets its label (round 4: it ran into the letter A of "ANCYRA"): the midpoint of the
    label block's edge nearest the stop, `gap` px outside it."""
    x0, y0, x1, y1 = box
    cands = [((x0 + x1) / 2, y0 - gap), ((x0 + x1) / 2, y1 + gap), (x0 - gap, (y0 + y1) / 2), (x1 + gap, (y0 + y1) / 2)]
    return min(cands, key=lambda p: math.dist(p, stop))


def route_arrow(leg: list[tuple[float, float]], at: float = ROUTE_ARROW_AT) -> tuple[tuple[float, float], tuple[float, float]]:
    """(tip, direction) of a leg's one arrowhead (round 6, 2026-11-03: the chevrons every 120 px are gone): at `at`
    of the leg's length, pointing the way the journey goes."""
    lens = [math.dist(leg[i], leg[i + 1]) for i in range(len(leg) - 1)]
    goal, acc = sum(lens) * at, 0.0
    for i, ln in enumerate(lens):
        if acc + ln >= goal and ln > 0:
            f = (goal - acc) / ln
            (ax, ay), (bx, by) = leg[i], leg[i + 1]
            return (ax + (bx - ax) * f, ay + (by - ay) * f), (bx - ax, by - ay)
        acc += ln
    return leg[-1], (leg[-1][0] - leg[0][0], leg[-1][1] - leg[0][1])


def _route_arrow(d: ImageDraw.ImageDraw, tip: tuple[float, float], direction: tuple[float, float], size: float,
                 colour: tuple[int, int, int], alpha: float = 1.0) -> None:
    """A filled arrowhead `size` px long on the leg, in the leg's colour on an INK casing."""
    dx, dy = direction
    nrm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / nrm, dy / nrm
    half = size * 0.55
    head = (tip[0] + dx * size / 2, tip[1] + dy * size / 2)
    back = (tip[0] - dx * size / 2, tip[1] - dy * size / 2)
    pts = [head, (back[0] - dy * half, back[1] + dx * half), (back[0] + dy * half, back[1] - dx * half)]
    d.polygon(pts, fill=(*brand.INK, int(255 * alpha)), outline=(*brand.INK, int(255 * alpha)), width=4)
    d.polygon(pts, fill=(*colour, int(255 * alpha)))


def bowed_leg(a: tuple[float, float], b: tuple[float, float], land: BoolArray | None = None, steps: int = 32
              ) -> list[tuple[float, float]]:
    """A leg from `a` to `b` (frame px) bowed ROUTE_BOW of its length toward the sea (the side with less land under
    it, `land` a bool array), flipped where that bow crosses land and the other does not, else ROUTE_BOW_MAX toward
    the side with the least land (round 6): a quadratic curve, `steps` + 1 points."""
    (ax, ay), (bx, by) = a, b
    length = math.hypot(bx - ax, by - ay)
    if length < 1e-6:
        return [a] * (steps + 1)
    nx, ny = -(by - ay) / length, (bx - ax) / length

    def curve(sign: float, share: float) -> list[tuple[float, float]]:
        cx = (ax + bx) / 2 + nx * sign * 2 * share * length
        cy = (ay + by) / 2 + ny * sign * 2 * share * length
        return [((1 - t) ** 2 * ax + 2 * (1 - t) * t * cx + t * t * bx, (1 - t) ** 2 * ay + 2 * (1 - t) * t * cy + t * t * by)
                for t in (i / steps for i in range(steps + 1))]

    def on_land(pts: list[tuple[float, float]]) -> int:
        if land is None:
            return 0
        h, w = land.shape
        inner = pts[3:-3] or pts
        return int(sum(bool(land[int(min(h - 1, max(0, y))), int(min(w - 1, max(0, x)))]) for x, y in inner))
    if land is None:
        return curve(1.0, ROUTE_BOW)
    c1, c2 = curve(1.0, ROUTE_BOW), curve(-1.0, ROUTE_BOW)
    l1, l2 = on_land(c1), on_land(c2)
    first, lf = (c1, l1) if l1 <= l2 else (c2, l2)
    if lf == 0:
        return first
    sign = 1.0 if first is c1 else -1.0
    wide = curve(sign, ROUTE_BOW_MAX)
    return wide if on_land(wide) < lf else first


ROUTE_MAX_STOPS = 5         # the stops a route draws
ROUTE_NAME_PX = 34          # a stop's name, both orientations (round 4 review 5: 30 on 16:9, the bar figures' size)
ROUTE_LEADER_AT = 56        # round 4 review 5: px from a stop to its name's block beyond which a leader ties them
ROUTE_LEADER_W = 2          # round 4 review 5: the leader's core, the timeline flags' weight (GOLD_DIM, 2 px at 1080)
ROUTE_LEADER_CASE = 6       # and its ink casing, so it reads on the relief as the locator's pin leader does
# Round 4 review 9 (2026-11-03): before a stop is left unnamed its name steps down to ROUTE_NAME_MIN_PX (rr4 9:16's
# LIGHTHOUSE, 172 px from its nearest stop, and rr8's TOMB, 242 px, were left unnamed at 34 px)
ROUTE_NAME_STEPS = (34, 32, 30, 28)
ROUTE_NAME_MIN_PX = ROUTE_NAME_STEPS[-1]
# QF-24: on 9:16 a name's places at its first size are joined by its places this many px smaller, ranked after every
# full-size place (size_drop): on the organic Reel box (x 113-848, 88 px narrower than the ads box) a name that just
# misses its side of the box at full size took the other side, where a far stop's only room was (rr8: the customs
# house's name crossed to the tomb's side, and the tomb was left unnamed)
ROUTE_NAME_NEXT_STEP = 2
ROUTE_NAME_NB_AIR = 36      # round 16 (review): px (at 1080) a country's name keeps off a stop's name
ROUTE_DEEP_TRIES = 24       # and a stop it left unnamed tries this many of its clear places first (place_names)
# words that never stand alone on a name's line ("CUSTOMS / HOUSE OF / THE / GENOESE / TRADERS")
ROUTE_FUNCTION_WORDS = frozenset({"A", "AN", "THE", "OF", "ON", "IN", "AT", "TO", "BY", "AND", "OR", "FOR", "HIS",
                                  "HER", "ITS", "AS", "UP", "NEAR", "WITH", "FROM"})


NAME_ABBREVIATIONS = frozenset({"ST", "STE", "STS", "MT", "FT", "PT", "DR", "SS"})   # round 16: bound to the next word


def route_name_lines(text: str, font, measure: float, max_lines: int, track: float = 0) -> list[str] | None:
    """A stop's name on at most `max_lines` lines no wider than `measure` (their advance, as wrap_text measured
    them; the places are judged on the drawn ink), or None: the
    fewest lines it takes, broken so the widest line is as narrow as it can be and the lines as even as they can
    be (a line ending on a function word, "LIGHTHOUSE OF / ...", costs a little width), and never a line of
    function words alone (round 4 review 9,
    2026-11-03: the narrower 'further' measure set "LIGHTHOUSE / OF THE / PTOLEMIES / ON THE / EASTERN / MOLE"
    on six lines, and "THE" alone on one, past its three)."""
    import itertools as _it
    words = drawable(" ".join(text.split()).upper(), font).split()
    if not words:
        return None
    widths: dict[tuple[int, int], float] = {}

    def w(i: int, j: int) -> float:
        if (i, j) not in widths:
            widths[(i, j)] = _tracked_w(" ".join(words[i:j]), font, track)
        return widths[(i, j)]
    for n in range(1, min(max_lines, len(words)) + 1):
        best, best_key = None, None
        for cuts in _it.combinations(range(1, len(words)), n - 1):
            edges = (0, *cuts, len(words))
            spans = list(_it.pairwise(edges))
            if any(w(i, j) > measure for i, j in spans):
                continue
            if n > 1 and any(all(wd.rstrip(",;:") in ROUTE_FUNCTION_WORDS for wd in words[i:j]) for i, j in spans):
                continue
            # round 16 (review 7: "RUINS OF ST / PAUL'S, MACAU"): never a break after a title's abbreviation
            if n > 1 and any(words[j - 1].rstrip(".") in NAME_ABBREVIATIONS for _i, j in spans[:-1]):
                continue
            dangling = sum(words[j - 1].rstrip(",;:") in ROUTE_FUNCTION_WORDS for i, j in spans[:-1])
            ws = [w(i, j) for i, j in spans]
            key = (max(ws) * (1 + 0.08 * dangling) + 0.5 * (max(ws) - min(ws)), dangling)
            if best_key is None or key < best_key:
                best, best_key = [" ".join(words[i:j]) for i, j in spans], key
        if best is not None:
            return best
    return None


def even_keep(n: int, limit: int) -> list[int]:
    """The indices of `limit` of `n` items kept in order: the first and the last always, the middle ones evenly
    spaced through the run (timeline_events' rule)."""
    if n <= limit:
        return list(range(n))
    if limit <= 2:
        return [0, n - 1][:max(limit, 0)]
    return [0, *sorted({round(j * (n - 1) / (limit - 1)) for j in range(1, limit - 1)}), n - 1]


def route_stops(located: list[dict[str, Any]], limit: int = ROUTE_MAX_STOPS) -> list[dict[str, Any]]:
    """The located stops a route draws, in order (round 4 review 5, 2026-11-02: `stops[:5]` silently dropped a
    sixth stop, which can be the destination): over `limit`, the first and the last always and the middle ones
    evenly spaced (even_keep); the dropped are logged."""
    keep = even_keep(len(located), limit)
    if len(keep) < len(located):
        log.warning("Route: %d stops, %d drawn; dropped %s.", len(located), len(keep),
                    ", ".join(str(located[i].get("label")) for i in range(len(located)) if i not in keep))
    return [located[i] for i in keep]


def box_distance(pt: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    """The distance from a point to the nearest point of a box (0 inside it)."""
    dx = max(box[0] - pt[0], 0.0, pt[0] - box[2])
    dy = max(box[1] - pt[1], 0.0, pt[1] - box[3])
    return math.hypot(dx, dy)


def route_leader(stop: tuple[float, float], box: tuple[float, float, float, float], r: float,
                 gap: float = 8) -> tuple[tuple[float, float], tuple[float, float]]:
    """A stop's leader to its name (round 4 review 5, 2026-11-02): from the stop disc's rim (radius `r`) to the
    nearest point of the name's block grown by `gap`, so it is as short as the two allow (the hairline ran from
    the stop's centre to the middle of the block's edge, up to ~300 px on a long name)."""
    gx0, gy0, gx1, gy1 = box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap
    end = (min(max(stop[0], gx0), gx1), min(max(stop[1], gy0), gy1))
    d = math.dist(stop, end) or 1.0
    start = (stop[0] + (end[0] - stop[0]) * min(1.0, r / d), stop[1] + (end[1] - stop[1]) * min(1.0, r / d))
    return start, end


ROUTE_STACK_AIR = 64         # round 12 (review: Peru): px two stops' names stacked one over the other keep apart
ROUTE_STACK_SHARE = 0.25     # where they share this much of the narrower one's width (else ROUTE_NAME_AIR)


def stacks_clear(cands, named: list[tuple[float, float, float, float]], air: float = ROUTE_STACK_AIR,
                 share: float = ROUTE_STACK_SHARE):
    """Round 12: which candidate name boxes (an array (C, 4)) are not stacked on any of the `named` boxes: one
    over or under another by under `air` px, sharing over `share` of the narrower one's width (the two then read
    as one name's lines). A (C,) bool array."""
    import numpy as np
    cands = np.asarray(cands, dtype=np.float64).reshape(-1, 4)
    if not len(named) or not len(cands):
        return np.ones(len(cands), dtype=bool)
    a = cands[:, None, :]
    b = np.asarray(named, dtype=np.float64)[None, :, :]
    overlap = np.minimum(a[..., 2], b[..., 2]) - np.maximum(a[..., 0], b[..., 0])
    narrow = np.minimum(a[..., 2] - a[..., 0], b[..., 2] - b[..., 0])
    gap = np.maximum(a[..., 1] - b[..., 3], b[..., 1] - a[..., 3])
    hit = (overlap > share * narrow) & (gap < air)
    return ~hit.any(axis=1)


def boxes_clear(cands, obstacles: list[tuple[float, float, float, float]], margin: float):
    """Which candidates (an array (C, L, 4) of their lines' boxes, NaN rows unused) clear every obstacle by
    `margin` (LayoutConstraintSolver.boxes_intersect, vectorised): a (C,) bool array."""
    import numpy as np
    if not len(obstacles) or not len(cands):
        return np.ones(len(cands), dtype=bool)
    a = cands[:, :, None, :]
    b = np.asarray(obstacles, dtype=np.float64)[None, None, :, :]
    with np.errstate(invalid="ignore"):
        hit = ((a[..., 2] + margin > b[..., 0]) & (b[..., 2] + margin > a[..., 0])
               & (a[..., 3] + margin > b[..., 1]) & (b[..., 3] + margin > a[..., 1]))
    return ~hit.any(axis=(1, 2))


def route_land(project: Callable[[float, float], tuple[float, float]], scale: float, width: int, height: int,
               lon0: float, lat0: float, lon_span: float,
               lat_span: float) -> tuple[BoolArray, "npt.NDArray[np.int32]"]:
    """(land, parts) of a route's view on the frame (round 6): every country's land as a bool array, and its
    connected land masses labelled (cv2.connectedComponents: 0 the sea)."""
    import cv2
    import numpy as np
    m = Image.new("L", (width, height), 0)
    dm = ImageDraw.Draw(m)
    lon_half, lat_half = lon_span / 2 + 2, lat_span / 2 + 2
    for _c, poly in geo.view_polygons(lon0, lat0, lon_half, lat_half):   # round 8: wrapped, enclaves last
        dm.polygon([(project(x, y)[0] / scale, project(x, y)[1] / scale) for x, y in poly[0]], fill=255)
        for hole in poly[1:]:
            dm.polygon([(project(x, y)[0] / scale, project(x, y)[1] / scale) for x, y in hole], fill=0)
    land = np.asarray(m) > 127
    _n, parts = cv2.connectedComponents(land.astype(np.uint8), connectivity=8)
    return land, parts


def route_other_land(parts: "npt.NDArray[np.int32]", pts: list[tuple[float, float]]
                     ) -> list["npt.NDArray[np.int64] | None"]:
    """Per stop, an integral image of the land masses of at least ROUTE_LABEL_LAND_PX2 that are not the stop's own
    (the mass under it, or the nearest within 30 px for a stop off the coast), or None where there are none."""
    import numpy as np
    labels, counts = np.unique(parts, return_counts=True)
    big = {int(lab) for lab, cnt in zip(labels, counts) if lab and cnt >= ROUTE_LABEL_LAND_PX2}
    h, w = parts.shape
    out: list[npt.NDArray[np.int64] | None] = []
    for x, y in pts:
        xi, yi = int(min(w - 1, max(0, x))), int(min(h - 1, max(0, y)))
        own = int(parts[yi, xi])
        if not own:
            win = parts[max(0, yi - 30):yi + 31, max(0, xi - 30):xi + 31]
            vals, cnt = np.unique(win[win > 0], return_counts=True)
            own = int(vals[np.argmax(cnt)]) if len(vals) else 0
        others = big - {own}
        out.append(_integral(np.isin(parts, list(others))) if others else None)
    return out


ROUTE_STOP_SEP = 40.0       # round 6 review: px between two stops' centres at least (Naples and Pompeii read as one blob)


def separate_stops(pts: Sequence[tuple[float, float]], sep: float, rounds: int = 60) -> list[tuple[float, float]]:
    """The stops' dots moved apart, where closer than `sep`, along the line between them (cartographic
    displacement: each of a pair by half the shortfall, repeated until no pair is too close or `rounds` is spent),
    so two stops never read as one blob. The stops' order and every far stop are kept. Pairs are found by sorting
    on x and sweeping (O(n log n) a round; a route has at most ROUTE_MAX_STOPS)."""
    out = [(float(x), float(y)) for x, y in pts]
    for _ in range(rounds):
        moved = False
        order = sorted(range(len(out)), key=lambda i: out[i][0])
        for a_i, i in enumerate(order):
            for j in order[a_i + 1:]:
                if out[j][0] - out[i][0] >= sep:
                    break
                (xi, yi), (xj, yj) = out[i], out[j]
                d = math.hypot(xj - xi, yj - yi)
                if d >= sep - 1e-6:
                    continue
                if d < 1e-6:
                    ux, uy = 1.0, 0.0
                else:
                    ux, uy = (xj - xi) / d, (yj - yi) / d
                push = (sep - d) / 2 + 0.01
                out[i] = (xi - ux * push, yi - uy * push)
                out[j] = (xj + ux * push, yj + uy * push)
                moved = True
        if not moved:
            break
    return out


def _leg_paint(part: Sequence[tuple[float, float]], colour: tuple[int, int, int], k: float,
               arrow: tuple[tuple[float, float], tuple[float, float]] | None) -> Painter:
    """A leg's drawing for _ss_composite: its INK casing, its core in `colour` and, once drawn through it, its
    arrowhead."""
    def paint(d: ImageDraw.ImageDraw, at: Callable[[float, float], tuple[float, float]], ss: float) -> None:
        pts = [at(x, y) for x, y in part]
        d.line(pts, fill=(*brand.INK, 255), width=round((ROUTE_LEG_W + 2 * ROUTE_LEG_CASE) * ss), joint="curve")
        r0 = (ROUTE_LEG_W + 2 * ROUTE_LEG_CASE) * ss / 2
        for x, y in (pts[0], pts[-1]):                    # round caps, so a leg's ends are not cut square
            d.ellipse([x - r0, y - r0, x + r0, y + r0], fill=(*brand.INK, 255))
        d.line(pts, fill=(*colour, 255), width=round(ROUTE_LEG_W * ss), joint="curve")
        r1 = ROUTE_LEG_W * ss / 2
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - r1, y - r1, x + r1, y + r1], fill=(*colour, 255))
        if arrow is not None:
            (tx, ty), dirn = arrow
            _route_arrow_ss(d, at(tx, ty), dirn, ROUTE_ARROW_PX * k * ss, colour, ss)
    return paint


def _route_arrow_ss(d: ImageDraw.ImageDraw, tip: tuple[float, float], direction: tuple[float, float], size: float,
                    colour: tuple[int, int, int], ss: float) -> None:
    """The arrowhead in a supersampled layer: its INK casing 2 px (at 1x) round it, then its fill."""
    dx, dy = direction
    nrm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / nrm, dy / nrm

    def tri(sz: float) -> list[tuple[float, float]]:
        half = sz * 0.55
        head = (tip[0] + dx * sz / 2, tip[1] + dy * sz / 2)
        back = (tip[0] - dx * sz / 2, tip[1] - dy * sz / 2)
        return [head, (back[0] - dy * half, back[1] + dx * half), (back[0] + dy * half, back[1] - dx * half)]
    d.polygon(tri(size + 5 * ss), fill=(*brand.INK, 255))
    d.polygon(tri(size), fill=(*colour, 255))


def _path_box(part: Sequence[tuple[float, float]], pad: float) -> Box:
    return (min(p[0] for p in part) - pad, min(p[1] for p in part) - pad, max(p[0] for p in part) + pad,
            max(p[1] for p in part) + pad)


ROUTE_GLOBE_HOLD = 1.0   # round 7: s past the first leg's start the route's globe may hold, while that leg draws


def render_route(stops: list[dict[str, Any]], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                 narration: str = "") -> Path | None:
    """The journey on the relief map (round 3, 2026-11-02: brought to the locator's standard): each leg a
    great-circle arc (great_circle), drawn at constant speed as a gold stroke over an ink casing with a leading
    dot; each stop a gold disc and its name as a measured knockout label (halo, recorded on the last frame) inside
    the graphics box, moved off an earlier label (with a leader) rather than over it; the neighbours' names inside
    the safe box and above the caption band; a scale bar once the route is drawn. Stops are located like the topic
    itself (inside their stated country, or skipped)."""
    import itertools
    if not geo.available():
        return None
    located = []
    for s in stops:
        p = geo.locate({"location": {"name": s["place"], "country": s["country"]}})
        if p:
            located.append({**p, "label": s["label"]})
    located = route_stops(located)
    if len(located) < 2:
        return None
    lats, lons = [p["lat"] for p in located], [p["lon"] for p in located]
    lat0, lon0 = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    coslat = max(0.2, math.cos(math.radians(lat0)))
    scale = 2
    bw, bh = width * scale, height * scale
    top, bottom = get_text_bounds(width, height)
    sx0, sy0, sx1, sy1 = get_text_box(width, height)
    # Fit every stop to the graphics box (round 4: 62 % of its width before): ROUTE_FIT of its width and of its
    # height less the labels' room, whichever binds, so an east-west route uses the width and a north-south one
    # the height.
    span_lon = max((max(lons) - min(lons)) * coslat / (ROUTE_FIT * (sx1 - sx0) / width), 4.0)
    span_lat = max((max(lats) - min(lats)) / (ROUTE_FIT * (bottom - top - 140) / height), 4.0)
    kx = min(bw / span_lon, bh / span_lat)
    # The stops centred on the graphics box, not the frame (round 3: on 9:16 the frame's centre is below the
    # box's, and a southern stop fell under the caption band): the view's centre moved to match.
    lat_c, lon_c = lat0, lon0
    # Round 6 (2026-11-03): on 9:16 on the graphics box's own centre (y about 673; the text box's, 694, before)
    portrait = height > width
    centre_y = (safe_zones.graphics_box(width, height)[1] + bottom) / 2 if portrait else (top + bottom) / 2
    lat0 = lat_c - (bh / 2 - centre_y * scale) / kx
    lon0 = lon_c + (bw / 2 - (sx0 + sx1) / 2 * scale) / (kx * coslat)

    def project(lon: float, lat: float) -> tuple[float, float]:
        return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx

    import numpy as np
    land_f, land_parts = route_land(project, scale, width, height, lon0, lat0, bw / (kx * coslat), bh / kx)
    k_px = min(width, height) / 1080
    pts = [(project(p["lon"], p["lat"])[0] / scale, project(p["lon"], p["lat"])[1] / scale) for p in located]
    # round 6 review: no two stops' dots closer than ROUTE_STOP_SEP (Naples and Pompeii were one blob): displaced
    spread_pts = separate_stops(pts, ROUTE_STOP_SEP * k_px)
    if any(math.dist(a_, b_) > 0.5 for a_, b_ in zip(pts, spread_pts)):
        log.info("Route: stops %s moved apart to %.0f px (displaced up to %.0f px).",
                 [k for k, (a_, b_) in enumerate(zip(pts, spread_pts)) if math.dist(a_, b_) > 0.5],
                 ROUTE_STOP_SEP * k_px, max(math.dist(a_, b_) for a_, b_ in zip(pts, spread_pts)))
    pts = spread_pts
    legs, chords = [], []
    for (pa, pb), (qa, qb) in zip(itertools.pairwise(located), itertools.pairwise(pts)):
        arc = great_circle((pa["lon"], pa["lat"]), (pb["lon"], pb["lat"]))
        chords.append([(project(lo, la)[0] / scale, project(lo, la)[1] / scale) for lo, la in arc])
        # round 6: each leg bowed toward the sea (bowed_leg), not the great circle's chord-like arc
        legs.append(bowed_leg(qa, qb, land_f))
    corridor = tuple(((min(xa, xb) - 14) * scale, (min(ya, yb) - 14) * scale, (max(xa, xb) + 14) * scale,
                      (max(ya, yb) + 14) * scale) for leg in legs for (xa, ya), (xb, yb) in itertools.pairwise(leg))
    lengths = [_path_len(leg) for leg in legs]
    total_len = sum(lengths) or 1.0
    t0, t_draw = 0.6, 3.4                        # the route draws at one speed over t_draw seconds
    arrive = [t0]
    for ln in lengths:
        arrive.append(arrive[-1] + t_draw * ln / total_len)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    # The stops' labels are placed first, on geometry alone (a plate's box does not depend on its ground), so the
    # neighbours' names can keep clear of them as well as of the legs and the scale bar; each is then measured on
    # the finished map (round 3 review, 2026-11-02).
    rest = Image.new("RGB", (width, height), brand.INK)
    # round 4 review 5: 34 px on 16:9 too (30 before, the scale bar's own figures' size): the names dominate
    f_lab = get_font("Montserrat", ROUTE_NAME_PX, weight=800)
    label_w = 380
    fixed: list[tuple[float, float, float, float]] = [(x - 26, y - 26, x + 26, y + 26) for x, y in pts]
    # round 6: a name keeps ROUTE_LEG_AIR from a leg's edge (the boxes below are cleared by 8 px more)
    leg_half = ROUTE_LEG_W / 2 + ROUTE_LEG_CASE + ROUTE_LEG_AIR - 8
    other_land = route_other_land(land_parts, pts)
    for leg in legs:                                     # no name over a stop or the route itself
        for (xa, ya), (xb, yb) in itertools.pairwise(leg):
            for f in (0.0, 0.5):
                x, y = xa + (xb - xa) * f, ya + (yb - ya) * f
                fixed.append((x - leg_half, y - leg_half, x + leg_half, y + leg_half))
    rel_memo: dict[tuple[str, int, str], tuple[int, int, int, int] | None] = {}

    def rel_box(line: str, font, anchor: str) -> tuple[int, int, int, int] | None:
        # a name line's ink set at the origin (ink_box), moved to each whole-pixel place it is tried at
        key = (line, font.size, anchor)
        if key not in rel_memo:
            rel_memo[key] = ink_box((0, 0), line, font, anchor, 2)
        return rel_memo[key]

    def seg_near(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float], r: float) -> bool:
        (ax, ay), (bx, by) = a, b
        L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1.0
        f = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
        return math.dist(p, (ax + (bx - ax) * f, ay + (by - ay) * f)) < r

    # review 6: a stop this close to another is a genuine cluster, where no name can be nearer its own stop alone.
    # Round 4 review 7 (2026-11-03): only that: a stop whose every place was vague counted as one too, though it
    # stood 69 px from its nearest neighbour (zigzag's HALICARNASSUS); such a stop searches further first
    # (name_places' `further`), and a vague name outside a cluster is the last resort, logged at WARNING.
    cluster = {k for k, q in enumerate(pts)
               if min((math.dist(q, q2) for j, q2 in enumerate(pts) if j != k), default=1e9) < ROUTE_CLUSTER_PX}

    def name_places(k: int, x: float, y: float, label: str, further: bool = False,
                    last: bool = False) -> dict[str, Any]:
        """A stop's name's places in order of preference, each inside the box and clear of the stops and legs,
        found on geometry alone (ink_box: round 4 review 5, 2026-11-02, every candidate was planned and its
        contrast solved, which doubled the render and crashed on a place off the frame's top): {"specs": per place
        its lines as (xy, text, font, anchor), "boxes": an array (C, L, 4) of their lines' ink boxes (NaN rows unused),
        "union": the block's box, "lead": whether it needs a leader}. Round 4 review 5: the places next to the stop
        first (beside it on its open side, the other side, above, below, slid along that side while still beside
        it), then, with a leader, the nearest places further off, the shortest leader first. Round 4 review 7
        (2026-11-03), `further`, for a stop with no clear place among those: longer leaders (to ROUTE_FAR_REACH),
        the four corners beside it, and the name on two lines of a narrower measure.
        Round 4 review 9 (2026-11-03): a name never runs past its lines (route_name_lines: the narrower measure
        set LIGHTHOUSE on six) and never leaves a function word alone on one; a name no wrap holds at
        ROUTE_NAME_PX takes the first of ROUTE_NAME_STEPS that does; and `last`, for a stop the layout left
        unnamed, every step down to ROUTE_NAME_MIN_PX (ranked after the larger), with finer steps and a ring of
        places all round the stop."""
        found = []
        shown_label = " ".join(label.split()).upper()      # as route_name_lines sets it
        reach_px = ROUTE_FAR_REACH if further or last else 160
        measures = [(label_w, ml, 0) for ml in (2, 3)]
        if (further or last) and len(label.split()) > 1:
            measures += [(label_w * 0.6, 2, 1), (label_w * 0.6, 3, 1)]
        # review 9: a wider, shallower block, before any smaller size ("DERINKUYU UNDERGROUND CITY OF CAPPADOCIA"
        # takes no three lines of 380 px at 34 px; four before, past its block), and in the last pass always
        wide = [(label_w * f_, ml, 2) for f_ in (1.4, 1.8) for ml in (1, 2, 3)] if len(label.split()) > 1 else []
        fine = 2 if last else 1                   # review 9: the last pass steps half as far between places
        settings = []
        for px in ROUTE_NAME_STEPS:
            if settings and not last and (not portrait or px < settings[0][1].size - ROUTE_NAME_NEXT_STEP):
                break
            f_px = field_font(shown_label, f_lab if px == f_lab.size else get_font("Montserrat", px, weight=800))
            at_px: list[tuple[list[str], Any, int, int]] = []
            for group in (measures, wide):
                if at_px and not last:
                    break
                for measure, max_lines, tier in group:
                    # round 4 review 3: a third line before a smaller size
                    lines = route_name_lines(label, f_px, measure, max_lines)
                    if lines is not None and all(lines != st_[0] or f_px is not st_[1] for st_ in settings + at_px):
                        at_px.append((lines, f_px, max_lines, tier))
            settings += at_px
        for lines, f_fit, max_lines, tier in settings:
            size_drop = f_lab.size - f_fit.size
            step = round(f_fit.size * 1.22)
            cap = cap_height(f_fit)
            block = step * (len(lines) - 1) + cap
            rels = {a: [rel_box(ln, f_fit, a) for ln in lines] for a in ("ls", "rs", "ms")}
            open_r = x < width / 2
            tried = []
            # beside the stop (its open side first), slid up or down while beside it, then pushed further off
            for side, pref in (("r" if open_r else "l", 0), ("l" if open_r else "r", 1)):
                reach = block / 2 + 30
                for push in range(0, reach_px + 1, 16 // fine):
                    for dv in sorted({0.0, *(sg * min(reach, d) for d in range(14 // fine, int(reach) + 14, 14 // fine)
                                              for sg in (-1, 1))}, key=abs):
                        tx = x + 34 + push if side == "r" else x - 34 - push
                        tried.append((tx, "ls" if side == "r" else "rs", y - block / 2 + dv, pref, push + abs(dv)))
            # above and below it, centred then slid along, then lifted further off
            wmax = max((r[2] - r[0]) for r in rels["ms"] if r) if any(rels["ms"]) else 0
            for side, pref in (("a", 2), ("b", 3)):
                reach = wmax / 2 + 24
                for lift in range(0, reach_px + 1, 14 // fine):
                    for dh in sorted({0.0, *(sg * min(reach, d) for d in range(24 // fine, int(reach) + 24, 24 // fine)
                                              for sg in (-1, 1))}, key=abs):
                        y_top = y - 40 - block - lift if side == "a" else y + 40 + lift
                        tried.append((x + dh, "ms", y_top, pref, lift + abs(dh)))
            if further or last:           # review 7: the corners, the name's near edge on the stop's
                for (sh, sv), pref in zip(((1, -1), (1, 1), (-1, -1), (-1, 1)), (4, 5, 6, 7)):
                    for off in range(0, reach_px + 1, 16 // fine):
                        tx = x + (24 + off) * sh
                        y_top = y - 24 - off - block if sv < 0 else y + 24 + off
                        tried.append((tx, "ls" if sh > 0 else "rs", y_top, pref, off))
            if last:                      # review 9: and a ring all round it, the name centred on each point
                for ang in range(0, 360, 15):
                    ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
                    for r_ in range(40, reach_px + 1, 10):
                        tried.append((x + r_ * ca, "ms", y + r_ * sa - block / 2, 8, r_))
            for tx, anchor, y_top, pref, slide in tried:
                tx, base = round(tx), round(y_top + cap)
                boxes = [(r[0] + tx, r[1] + base + step * j, r[2] + tx, r[3] + base + step * j)
                         for j, r in enumerate(rels[anchor]) if r]
                if not boxes:
                    continue
                u = _union(boxes)
                if not (sx0 <= u[0] and u[2] <= sx1 and sy0 <= u[1] and u[3] <= sy1):
                    continue          # review 5: rejected on its box before anything is measured
                dist = box_distance((x, y), u)
                lead = dist > ROUTE_LEADER_AT
                # review 5: never read as another stop's name ("SMYRNA HARBOUR" beside the Halicarnassus pin):
                # a name is nearer its own stop than any other, and a leader passes no other stop
                # (such places are kept, last, for stops too close together for any other: a cluster)
                near = min((box_distance(q, u) for j, q in enumerate(pts) if j != k), default=1e9)
                # review 6 (2026-11-03): a leader no longer excuses a name nearer another stop (HALICARNASSUS sat
                # under SMYRNA, read as its second line): every name is nearer its own stop by ROUTE_NAME_MARGIN
                vague = near < dist + ROUTE_NAME_MARGIN
                # round 4 review 8 (2026-11-03): outside a cluster a name nearer another stop than its own, or
                # on a leader that passes another stop, is never kept, not even as the last resort ("ROCK-CUT
                # TOMBS" sat 41 px from the Basilica stop and 304 px from its own): the stop is left unnamed
                # (logged) instead; a vague place left is ranked by how much nearer its own stop it is
                misread = near <= dist
                crossing = False
                if lead:
                    a_, b_ = route_leader((x, y), u, ROUTE_STOP_R + 3)
                    crossing = any(seg_near(a_, b_, q, 30) for j, q in enumerate(pts) if j != k)
                    vague, misread = vague or crossing, misread or crossing
                # review 9: the destination is always named; where every place by it reads as a neighbour's (the
                # zigzag's HALICARNASSUS between SMYRNA, 70 px off, and ATTALEIA), its last resort is a leader that
                # passes no other stop, to a name ROUTE_LEADER_AT clear of every other stop (logged)
                hero = (misread and last and k == len(pts) - 1 and k not in cluster and lead and not crossing
                        and near >= ROUTE_LEADER_AT)
                if misread and k not in cluster and not hero:
                    continue
                specs = [((tx, base + step * j), ln, f_fit, anchor,
                          (r[0] + tx, r[1] + base + step * j, r[2] + tx, r[3] + base + step * j))
                         for j, (ln, r) in enumerate(zip(lines, rels[anchor])) if r]
                # round 6: a name on another land mass (ANTIKYTHERA WRECK on Crete) only after every place off it
                a_ = ROUTE_LABEL_LAND_AIR
                on_land = other_land[k] is not None and _box_sum(other_land[k], (u[0] - a_, u[1] - a_, u[2] + a_,
                                                                                 u[3] + a_)) > 0
                found.append(((vague, on_land, 1000 if hero else -round(min(near - dist, ROUTE_NAME_MARGIN)) if vague else 0,
                               size_drop, lead, round(dist / 12) if lead else 0, pref, slide, max_lines, tier), specs,
                              boxes, u, lead, hero))
        if not found:
            return {"specs": [], "boxes": np.zeros((0, 3, 4)), "union": [], "lead": [], "vague": [], "hero": []}
        arr = np.full((len(found), max(len(f[2]) for f in found), 4), np.nan)   # fit_lines may keep more lines
        for i, f in enumerate(found):
            arr[i, :len(f[2])] = f[2]
        ok = boxes_clear(arr, fixed, 8)
        order = sorted((i for i in range(len(found)) if ok[i]), key=lambda i: found[i][0])
        return {"specs": [found[i][1] for i in order], "boxes": arr[order], "union": [found[i][3] for i in order],
                "lead": [found[i][4] for i in order], "vague": [found[i][0][0] for i in order],
                "hero": [found[i][5] for i in order]}
    places = [name_places(k, x, y, p["label"]) for k, ((x, y), p) in enumerate(zip(pts, located))]
    stop_r = [ROUTE_STOP_R for _k in range(len(pts))]   # round 6: the destination is the same dot
    for k, ((x, y), p) in enumerate(zip(pts, located)):
        if k not in cluster and all(places[k]["vague"]):
            places[k] = name_places(k, x, y, p["label"], further=True)

    deep = [False]                    # review 9: the last pass also tries a left stop's later places first

    def place_names(furniture: list[tuple[float, float, float, float]]):
        """Every stop's name at its first place clear of the furniture (the globe, the scale bar and its figures)
        and of the names placed before it: in the route's order, then the stops with the fewest places first, then
        those with the fewest clear ones (nearer their own stop than any other) first; the order that names the
        most stops, then leaves the fewest vague outside a cluster, is kept: (name specs, leaders, the stops left
        unlabelled, the stops named vaguely). Round 4 review 9 (2026-11-03): with `deep` (the last pass), a stop
        named first also tries up to ROUTE_DEEP_TRIES of its later clear places (its first took the others'
        room: rr8 9:16's TOMB had 810 clear places and was left unnamed)."""
        best, best_key = None, None
        free = [boxes_clear(pl["boxes"], furniture, 8) for pl in places]
        few = sorted(range(len(pts)), key=lambda k: len(places[k]["specs"]))
        orders: list[tuple[list[int], int | None]] = [
            (list(range(len(pts))), None), (few, None),
            (sorted(range(len(pts)), key=lambda k: sum(not v for v in places[k]["vague"])), None)]
        tried_first: set[int] = set()
        unions = [np.asarray(pl["union"], dtype=np.float64).reshape(-1, 4) for pl in places]
        while orders:
            order, forced = orders.pop(0)
            got: dict[int, int] = {}
            named: list[tuple[float, float, float, float]] = []
            named_u: list[tuple[float, float, float, float]] = []
            stacked: list[int] = []
            for k in order:
                if forced is not None and k == order[0]:
                    got[k] = forced
                    named += [tuple(b) for b in places[k]["boxes"][forced] if not np.isnan(b[0])]
                    named_u.append(tuple(unions[k][forced]))
                    continue
                ok = free[k] & boxes_clear(places[k]["boxes"], named, ROUTE_NAME_AIR)
                # round 12 (review: Peru, NAZCA LINES set under CUSCO, the two read as one block of the Cusco
                # stop's): a name is not stacked over or under another's where any other place keeps it clear
                strict = ok & stacks_clear(unions[k], named_u)
                hit = np.flatnonzero(strict)
                if not len(hit):
                    hit = np.flatnonzero(ok)
                    if len(hit):
                        stacked.append(k)
                if len(hit):
                    got[k] = int(hit[0])
                    named += [tuple(b) for b in places[k]["boxes"][got[k]] if not np.isnan(b[0])]
                    named_u.append(tuple(unions[k][got[k]]))
            vague = [k for k, i in got.items() if places[k]["vague"][i]]
            # review 8: the destination (the story's place, the hero stop) is the last a layout leaves unnamed
            key = (len(pts) - 1 not in got, len(pts) - len(got), sum(k not in cluster for k in vague), len(stacked))
            if best_key is None or key < best_key:
                best, best_key = (got, vague, stacked), key
            if key == (False, 0, 0, 0):
                break
            if not orders:
                # review 8: a stop the greedy orders left unnamed (rr7's destination, once ROCK-CUT TOMBS took a
                # clear place in its way) is named first, then the rest: each such stop once
                left = [k for k in range(len(pts)) if k not in best[0] and k not in tried_first and places[k]["specs"]]
                tried_first.update(left)
                for k in left:
                    rest_ = [k] + [j for j in few if j != k]
                    hits = np.flatnonzero(free[k])
                    if not deep[0] or len(hits) <= 1:
                        orders.append((rest_, None))
                        continue
                    picks = sorted({int(hits[round(i * (len(hits) - 1) / (ROUTE_DEEP_TRIES - 1))])
                                    for i in range(ROUTE_DEEP_TRIES)}) if len(hits) > ROUTE_DEEP_TRIES else \
                        [int(h) for h in hits]
                    orders += [(rest_, i) for i in picks]
        got, vague, stacked = best
        specs = [(f"stop{k}", sp) for k in sorted(got) for sp in places[k]["specs"][got[k]]]
        leaders = [route_leader((x, y), places[k]["union"][got[k]], stop_r[k] + 3)
                   if k in got and places[k]["lead"][got[k]] else None for k, (x, y) in enumerate(pts)]
        return specs, leaders, [k for k in range(len(pts)) if k not in got], vague, \
            [k for k, i in got.items() if places[k]["hero"][i]], stacked

    def seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        (ax, ay), (bx, by) = a, b
        L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1.0
        f = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
        return math.dist(p, (ax + (bx - ax) * f, ay + (by - ay) * f))

    def globe_clear(gb: tuple[int, int, int, int], leaders: list) -> bool:
        """Round 4 review 6 (2026-11-03): the globe's disc keeps GLOBE_AIR px from every stop, leg (its casing) and
        leader, not only from the names (it covered Constantinople and sat on Trebizond); round 6: from the legs'
        great-circle chords too (a leg now bows off its chord)."""
        c = ((gb[0] + gb[2]) / 2, (gb[1] + gb[3]) / 2)
        r = (gb[2] - gb[0]) / 2
        if any(math.dist(c, q) - r < stop_r[k] + 4 + GLOBE_AIR for k, q in enumerate(pts)):
            return False
        if any(seg_dist(c, a_, b_) - r < 10 + GLOBE_AIR for leg in legs + chords for a_, b_ in itertools.pairwise(leg)):
            return False
        return not any(seg_dist(c, *ld) - r < ROUTE_LEADER_CASE / 2 + GLOBE_AIR for ld in leaders if ld)

    # round 6: the bar keeps SCALE_COAST_AIR from the legs and off the land (Crete), where any place in its row does
    keep_off = land_f.copy()
    leg_m = Image.new("L", (width, height), 0)
    for leg in legs:
        ImageDraw.Draw(leg_m).line(leg, fill=255, width=ROUTE_LEG_W + 2 * ROUTE_LEG_CASE)
    keep_off |= np.asarray(leg_m) > 0
    bar_range = scale_range(width, height)
    bar_font = scale_label_font(width, height)

    def bar_layout(max_px: float, corner: str, second: bool) -> dict[str, Any]:
        """The scale bar at `max_px` at most, from the bottom-left ("l") or bottom-right ("r"), with or without the
        other unit (place_scale_bar: off the legs and the land where it can be), and the boxes a name keeps clear
        of: the bar's body and its figures with ROUTE_SCALE_AIR."""
        bar = scale_bar(lat_c, kx / scale, narration, max_px, bar_range=bar_range)
        if not second:
            bar = bar.one_unit()
        box_x = None if portrait else ((sx0 - 13.0, width / 2 - 1) if corner == "l" else (width / 2, sx1 + 30.0))
        lay = place_scale_bar(bar, width, height, [], obstacles=keep_off, box_x=box_x, prefer=corner)
        figs = [ink_box(xy, text, bar_font, "ls", bar_font.size * SCALE_LABEL_TRACK)
                for _n, text, xy in scale_bar_label_specs(bar, lay.x, lay.y, bar_font)]
        figs = [f for f in figs if f]
        a = ROUTE_SCALE_AIR
        x, y = lay.x, lay.y
        boxes = [(x - 12, y - 20, x + bar.length + 12, y + 20)] + [(b[0] - a, b[1] - a, b[2] + a, b[3] + a)
                                                                       for b in figs]
        return {"bar": bar, "x": x, "y": y, "boxes": boxes, "figs": figs,
                "why": f"bottom-{'right' if corner == 'r' else 'left'} at {bar.length:.0f} px"
                       f"{'' if second else ', one unit'}"}

    def globe_layout(corner: str, shrink: float) -> dict[str, Any]:
        gb = globe_box(width, height, corner, shrink)
        return {"box": gb, "boxes": [(gb[0] - 12, gb[1] - 12, gb[2] + 12, gb[3] + 12)],
                "why": f"{'top-left' if corner == 'tl' else 'top-right'}{'' if shrink == 1 else ', smaller'}"}

    # Round 4 review 4 (2026-11-02): the stops' names outrank the globe and the scale bar (the globe took
    # Trebizond's name and the lifted bar Attaleia's, where the commit before had both). Layouts are tried in order,
    # moving before dropping: the globe top-right then top-left; the bar bottom-left then bottom-right at each
    # length in ROUTE_BAR_STEPS; the bar with its second unit, then without; then a smaller globe; then no globe;
    # then no bar. The first layout that names every stop wins; else the one naming the most (the earliest).
    bars2 = [bar_layout(px, c, True) for px in ROUTE_BAR_STEPS for c in ("l", "r")]
    bars1 = [bar_layout(px, c, False) for px in ROUTE_BAR_STEPS for c in ("l", "r")]
    if not portrait:
        # Round 4 review 5 (2026-11-02): on 16:9 the bottom-right is YouTube's timestamp corner (see globe_box):
        # every bar on the left, shorter and then with one unit, before any on the right.
        lefts = [b for b in bars2 + bars1 if b["why"].startswith("bottom-left")]
        rights = [b for b in bars2 + bars1 if not b["why"].startswith("bottom-left")]
        bars2, bars1 = lefts, rights
    globes = [globe_layout("tr", 1.0), globe_layout("tl", 1.0)]
    small = [globe_layout("tr", 0.7), globe_layout("tl", 0.7)]
    layouts = ([(g, b) for b in bars2 for g in globes] + [(g, b) for b in bars1 for g in globes]
               + [(g, b) for b in bars2 + bars1 for g in small] + [(None, b) for b in bars2 + bars1]
               + [(g, None) for g in globes + small] + [(None, None)])
    # Round 4 review 6 (2026-11-03): a layout is ranked on clarity too: every layout is tried (the one-unit bar on
    # the left among them) until one names every stop, each nearer its own stop than any other; a vague name is
    # accepted only inside a cluster of stops (logged), and a globe never covers a stop, a leg or a leader.
    def choose_layout():
        best_ = None
        for rank_, (g, b) in enumerate(layouts):
            furniture = (g["boxes"] if g else []) + (b["boxes"] if b else [])
            plates_, leaders_, unlabelled_, vague_, hero_, stacked_ = place_names(furniture)
            if g and not globe_clear(g["box"], leaders_):
                continue
            # review 9: the destination first of all (it was third, so a layout naming one stop more left it out)
            # round 12: then the fewest names stacked on another's (stacks_clear)
            key = (len(pts) - 1 in unlabelled_, len(unlabelled_), sum(k not in cluster for k in vague_),
                   len(stacked_), rank_)
            if best_ is None or key < best_[0]:
                best_ = (key, g, b, (plates_, leaders_), unlabelled_, vague_, hero_, stacked_)
            if key[:4] == (False, 0, 0, 0):
                break
        return best_
    best = choose_layout()
    # Round 4 review 9 (2026-11-03): before a stop is left unnamed (rr4 9:16's stop 3, 172 px from its nearest stop;
    # rr8's stop 2, 242 px; the zigzag destination) its name takes the last pass of name_places (smaller sizes to
    # ROUTE_NAME_MIN_PX, finer steps, a ring of places round the stop) and every layout is tried again; the
    # neighbours' country names are placed after the stops' (relief_base's `reserved`), so they give way to it
    if best is not None and (best[4] or best[0][0]):
        for k in best[4]:
            places[k] = name_places(k, *pts[k], located[k]["label"], last=True)
        deep[0] = True
        again = choose_layout()
        if again is not None and again[0][:4] < best[0][:4]:
            best = again
    assert best is not None
    (rank_key, glay, blay, (plates, leaders), unlabelled, vague, hero, stacked) = best
    rank = rank_key[4]
    for k in stacked:
        log.info("Route: the name of stop %d (%s) is stacked on another stop's name; no layout keeps it %d px off "
                 "one.", k, located[k]["label"], ROUTE_STACK_AIR)
    if rank:
        log.info("Route: globe %s, scale bar %s, so %d of %d stops keep their names.",
                 glay["why"] if glay else "dropped", blay["why"] if blay else "dropped",
                 len(pts) - len(unlabelled), len(pts))
    for k in unlabelled:                              # review 8: a last resort, so a WARNING
        log.warning("Route: no clear place for the label of stop %d (%s); the stop is drawn unlabelled.", k,
                    located[k]["label"])
    for k in vague:
        if k in hero:                                 # review 9: the destination's last resort
            log.warning("Route: the destination's name, stop %d (%s), set on a leader: every place nearer its own "
                        "stop reads as another's; it keeps %d px from every other stop.", k, located[k]["label"],
                        ROUTE_LEADER_AT)
        elif k in cluster:
            log.info("Route: the name of stop %d (%s) is as near another stop as its own: a cluster.", k,
                     located[k]["label"])
        else:                                         # review 8: still nearer its own stop, by under the margin
            log.warning("Route: the name of stop %d (%s) is nearer its own stop by under %d px; no layout gives it "
                        "a clear place.", k, located[k]["label"], ROUTE_NAME_MARGIN)
    bar = blay["bar"] if blay else None
    bar_x, bar_y = (blay["x"], blay["y"]) if blay else (0.0, 0.0)
    # round 16 (review: the Vietnam route's THAILAND sat 6 px under HUE IMPERIAL CITY, VIETNAM under Hanoi's name,
    # and the stop read as Thailand's): every country's name keeps ROUTE_NAME_NB_AIR off a stop's name
    nb_air = ROUTE_NAME_NB_AIR * min(width, height) / 1080
    words = tuple((b[0] * scale - nb_air * scale, b[1] * scale - nb_air * scale, b[2] * scale + nb_air * scale,
                   b[3] * scale + nb_air * scale)
                  for b in [sp[4] for _, sp in plates] + [_leader_box(ld) for ld in leaders if ld])
    # round 4: the destination's country as the figure; round 15 (coordinator directive 1): the entity whose ground
    # holds the destination (geo.site_home), never a name looked up
    home = geo.site_home(float(located[-1]["lon"]), float(located[-1]["lat"]),
                         hint=str(located[-1].get("country") or "")).country
    gbox = glay["box"] if glay else None
    # round 6 review: the seas the route crosses named (E), off its legs, stops, names, globe and bar
    route_words = [sp[4] for _, sp in plates] + [_leader_box(ld) for ld in leaders if ld] + list(fixed[:len(pts)])
    route_words += list(blay["boxes"]) if blay else []
    route_words += [gbox] if gbox else []

    def from_frame(x: float, y: float) -> tuple[float, float]:
        return lon0 + (x * scale - bw / 2) / (kx * coslat), lat0 - (y * scale - bh / 2) / kx
    leg_mask = np.asarray(leg_m) > 0
    wl = water_labels(width, height, from_frame, land_f, route_words, avoid=leg_mask)
    words += tuple(((w_.box[0] - 12) * scale, (w_.box[1] - 12) * scale, (w_.box[2] + 12) * scale,
                    (w_.box[3] + 12) * scale) for w_ in wl)
    if gbox:
        words += (((gbox[0] - 16) * scale, (gbox[1] - 16) * scale, (gbox[2] + 16) * scale, (gbox[3] + 16) * scale),)
    if blay:                                          # no neighbour's name on the bar or its figures
        bx0, by0, bx1, by1 = _union(blay["boxes"])
        words += (((bx0 - 8) * scale, (by0 - 42) * scale, (bx1 + 8) * scale, (by1 + 8) * scale),)
    token = _RELIEF_CTX.set(ReliefContext(base_per_px=float(scale), wide=not portrait,
                                          plates=tuple((b[0] * scale, b[1] * scale, b[2] * scale, b[3] * scale)
                                                       for b in [sp[4] for _, sp in plates])))
    named: list[str] = []
    named_bx: list[tuple[str, tuple[float, float, float, float]]] = []
    skipped: set[str] = set()

    def route_base(skip_: set[str]) -> Image.Image:
        named.clear()
        named_bx.clear()
        skipped.clear()
        skipped.update(skip_)
        return relief_base(lat0, lon0, bw, bh, kx, coslat, home, project, (0, 0, bw, bh),
                           label_bottom_fraction=bottom / height,
                           reserved=corridor + words,   # no name under the route, a stop's name, the globe or the bar
                           final_safe=(sx0 * scale, sy0 * scale, sx1 * scale, sy1 * scale),
                           name_home=True, name_px=round(map_name_px(width, height) * scale),
                           named=named, skip=skip_, named_boxes=named_bx).resize((width, height), Image.LANCZOS)

    def to_frame_r(lon: float, lat: float) -> tuple[float, float]:
        px_, py_ = project(lon, lat)
        return px_ / scale, py_ / scale
    try:
        base = route_base(set())
        # round 13 (coordinator directive 3): true land neighbours before distant countries. A name the base drew
        # for a country that does not border the home, while one that does is unnamed, gives way to it (the Egypt
        # route named IRAQ, and left SUDAN unnamed with room to spare); the close-up names the adjacent below
        if home is not None:
            in_view_r = neighbours_in_view(width, height, home, to_frame_r)
            adj_r = [nm for nm, adj in in_view_r if adj]
            named_up = {nm.upper() for nm in named}
            missing_r = [nm for nm in adj_r[:NEIGHBOUR_MAX] if nm not in named_up]
            # round 16 (review: the Vietnam route left CAMBODIA unnamed): as many give way as leave the adjacent
            # ones their slots within NEIGHBOUR_MAX (as the locator does), the furthest first
            far_named_r = [nm for nm, adj in reversed(in_view_r) if not adj and nm in named_up]
            n_other = sum(1 for nm in named if nm != home["display"])
            n_give = max(len(missing_r), n_other + len(missing_r) - NEIGHBOUR_MAX) if missing_r else 0
            give = [nm for nm in named if nm.upper() in set(far_named_r[:n_give])]
            if give and missing_r:
                log.info("Route: the base's name(s) %s give way to the home's neighbour(s) %s.",
                         ", ".join(n_.upper() for n_ in give), ", ".join(missing_r))
                base = route_base(set(give) | {n_.upper() for n_ in give})
        # round 16 (review 4: MOROCCO, drawn by the base, was never checked): every name the base draws goes
        # through the verifier too (the home's in its own fill, a neighbour's on its own ground); one that fails is
        # drawn again without it (logged), the home's then set by country_spot below
        table_b = view_country_table(width, height, to_frame_r)
        base_checks: list[NameCheck] = []
        if home is not None:
            import cv2
            ids_b = view_country_ids(width, height, to_frame_r, home)
            home_b = cv2.resize((ids_b[0] == ids_b[1]).astype(np.uint8), (width, height),
                                interpolation=cv2.INTER_NEAREST) > 0 if ids_b[1] else None
        for nm_, bx_ in named_bx:
            fb = (bx_[0] / scale, bx_[1] / scale, bx_[2] / scale, bx_[3] / scale)
            if home is not None and nm_ == str(home["display"]):
                f_ = home_name_fault(fb, True, home_b, land_f, (-1e5, -1e5), k_px)
                base_checks.append(NameCheck("home", nm_.upper(), fb, f_))
            else:
                f_ = neighbour_name_fault(nm_, fb, table_b)
                base_checks.append(NameCheck("neighbour", nm_.upper(), fb, f_))
        bad_b = {c_.text for c_ in base_checks if not c_.ok}
        if bad_b:
            for c_ in base_checks:
                if not c_.ok:
                    log.info("Route: name check: the base's %s name %r is %s; drawn again without it.", c_.kind,
                             c_.text, c_.fault)
            again_skip = set(skipped) | {nm_ for nm_, _b in named_bx if nm_.upper() in bad_b} | bad_b
            base = route_base(again_skip)
            base_checks = [c_ for c_ in base_checks if c_.ok and c_.text in {n_.upper() for n_ in named}]
    finally:
        _RELIEF_CTX.reset(token)
    rest = base.convert("RGB")
    # Round 7 (review 8): the route's own country named where the base could not (Japan 9:16: its label point and
    # every nudge lay under the route), as a locator names it (country_spot: in its fill, else 12 px off its border),
    # and the neighbours in view the base left unnamed (close_neighbours), two in all where there is room
    taken_r = (route_words + list(fixed) + [w_.box for w_ in wl] + ([gbox] if gbox else [])
               + (list(blay["boxes"]) if blay else [])
               + [_grow(sp[4], nb_air) for _, sp in plates])     # round 16: ROUTE_NAME_NB_AIR off a stop's name
    r_cname = None
    if home is not None and str(home["display"]) not in named:
        import cv2
        ids_r = view_country_ids(width, height, to_frame_r, home)
        home_r = cv2.resize((ids_r[0] == ids_r[1]).astype(np.uint8), (width, height),
                            interpolation=cv2.INTER_NEAREST) > 0 if ids_r[1] else None
        rings_r = home_rings(home, to_frame_r, (sx0, sy0, sx1, sy1))
        far = (-1e5, -1e5, -1e5 + 1, -1e5 + 1)
        r_text = str(home["display"]).upper()
        spot_r = country_spot(r_text, rings_r, home_r, land_f, far[:2], far, taken_r, [], width, height, ids=ids_r)
        if spot_r is not None:
            r_cname = country_name(rest, r_text, spot_r, rings_r, home_r, land_f, far[:2], far, taken_r, [], width,
                                   height, ids_r)
        if r_cname is None:
            log.info("Route: no place names %s on the map.", r_text)
    r_cplates = [("country", country_plate(rest, r_cname))] if r_cname is not None else []
    n_named = sum(1 for nm in named if home is None or nm != home["display"])
    want_r = max(0, 2 - n_named)
    if home is not None:     # round 13 (directive 3): an adjacent neighbour still unnamed takes a slot up to the most
        named_up = {nm.upper() for nm in named}
        if any(nm not in named_up for nm in adj_r[:NEIGHBOUR_MAX]):
            want_r = max(want_r, NEIGHBOUR_MAX - n_named)
    r_near = close_neighbours(width, height, home, to_frame_r, taken_r + [pl.box for _n, pl in r_cplates],
                              {nm.upper() for nm in named}, want_r)
    # round 14 (review: the route's names never went through the final check): the name verifier on every name
    # the route itself sets (its country's, the close-up neighbours', the seas'), one that fails not drawn
    # (logged); the verdicts in LAST_NAME_CHECKS and the route's own layout sidecar
    r_checks: list[NameCheck] = list(base_checks)
    table_r = view_country_table(width, height, to_frame_r)
    if r_cname is not None:
        f_ = home_name_fault(r_cname.box, r_cname.inside, home_r, land_f, (-1e5, -1e5), k_px)
        r_checks.append(NameCheck("home", r_cname.text, r_cname.box, f_))
        if f_:
            r_cname, r_cplates = None, []
    # round 15 (coordinator directive 2): an outside name has a leader to its own ground
    r_leader = ground_leader(r_cname.box, home_r, k_px) if r_cname is not None and not r_cname.inside else None
    keep_rn = []
    for nb in r_near:
        f_ = neighbour_name_fault(nb.name, nb.box, table_r)
        r_checks.append(NameCheck("neighbour", nb.name.upper(), nb.box, f_))
        if not f_:
            keep_rn.append(nb)
    r_near = keep_rn
    keep_wl = []
    for w_ in wl:
        f_ = water_name_fault(w_.name, w_.box, from_frame, land_f)
        r_checks.append(NameCheck("water", w_.name.upper(), w_.box, f_))
        if not f_:
            keep_wl.append(w_)
    for c_ in r_checks:
        if not c_.ok:
            log.warning("Route: name check: the %s name %r at (%.0f, %.0f) is %s; not drawn.", c_.kind, c_.text,
                        c_.box[0], c_.box[1], c_.fault)
    LAST_NAME_CHECKS[:] = r_checks
    r_nbplates = neighbour_plates(rest, r_near)
    waters: list[tuple[WaterLabel, _MaskPlate]] = []
    for w_ in keep_wl:                  # drawn into the map: there from the first frame, under the route
        got = water_plate(rest, w_)
        if got is not None:
            rest = got[0]
            waters.append((w_, got[1]))
    base = rest
    plates = [(name, plan_text(rest, sp[0], sp[1], sp[2], WHITE, sp[3], track=2, halo_px=21, min_halo=0.7))
              for name, sp in plates]       # review 5: only the chosen places are planned and measured
    stop_of = {id(pl): int(name[4:]) for name, pl in plates}
    bar_plates = _scale_bar_plates(_with_bar(rest, bar, bar_x, bar_y), bar, bar_x, bar_y) if bar else []
    t_bar = arrive[-1] + 0.3
    view = (lon0 - bw / 2 / (kx * coslat), lat0 - bh / 2 / kx, lon0 + bw / 2 / (kx * coslat), lat0 + bh / 2 / kx)
    g_lon, g_lat = globe_centre(lon_c, lat_c)
    globe = globe_inset(gbox[2] - gbox[0], g_lon, g_lat, home, view,
                        (located[-1]["lon"], located[-1]["lat"])) if gbox else None
    arrows = [route_arrow(leg) for leg in legs]
    # round 6: the legs keep the run's accent, never TEAL on a map (its fallback): GOLD then
    leg_colour = GOLD if tuple(accent()) == tuple(brand.TEAL) else accent()
    last: list[Image.Image] = []
    # round 7 (review 8): the globe holds while the first leg draws (to at most a second), not 0.2 s
    globe_out = max(t0, min(arrive[1], t0 + ROUTE_GLOBE_HOLD))
    leg_pad = (ROUTE_LEG_W + 2 * ROUTE_LEG_CASE) / 2 + ROUTE_ARROW_PX * k_px + 4
    done_layers: dict[int, tuple[Image.Image, tuple[int, int]]] = {}

    def leg_layer(k: int) -> tuple[Image.Image, tuple[int, int]]:
        """A finished leg (with its arrowhead) supersampled once: its layer and origin, composited every frame."""
        if k not in done_layers:
            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            box = _path_box(legs[k], leg_pad)
            _ss_composite(layer, box, _leg_paint(legs[k], leg_colour, k_px, arrows[k]))
            x0, y0 = max(0, math.floor(box[0]) - 1), max(0, math.floor(box[1]) - 1)
            x1, y1 = min(width, math.ceil(box[2]) + 1), min(height, math.ceil(box[3]) + 1)
            done_layers[k] = (layer.crop((x0, y0, x1, y1)), (x0, y0))
        return done_layers[k]

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            # round 6: the globe fades out as the first leg starts drawing
            im = _draw_globe(base.convert("RGB"), globe, gbox or (0, 0, 0, 0), t, out_at=globe_out)
            a_names = _ease((t - 0.2) / 0.4)    # round 7: the country names the base could not take, from the start
            for _n, pl in r_cplates + r_nbplates:
                if a_names > 0:
                    im = draw_plate(im, pl, a_names)
            if r_leader is not None and a_names > 0:
                im = _draw_leader(im, r_leader, k_px * 0.75, 1.0, a_names)
            head = None
            for k, leg in enumerate(legs):
                prog = (t - arrive[k]) / max(1e-6, arrive[k + 1] - arrive[k])
                if prog <= 0:
                    break
                if prog >= 1:            # round 6 review: every leg antialiased (its casing stepped before)
                    layer, (lx0, ly0) = leg_layer(k)
                    region = im.crop((lx0, ly0, lx0 + layer.width, ly0 + layer.height)).convert("RGBA")
                    region.alpha_composite(layer)
                    im.paste(region.convert("RGB"), (lx0, ly0))
                    continue
                part = _sub_path(leg, [math.dist(leg[j], leg[j + 1]) for j in range(len(leg) - 1)], 0.0,
                                 lengths[k] * min(1.0, prog))
                if len(part) > 1:
                    # round 6: one arrowhead, shown as the line draws through it
                    im = _ss_composite(im, _path_box(part, leg_pad),
                                       _leg_paint(part, leg_colour, k_px, arrows[k] if prog >= ROUTE_ARROW_AT else None))
                    head = part[-1]
            d = ImageDraw.Draw(im, "RGBA")
            for k, (x, y) in enumerate(pts):
                s_, rd, ra = dot_motion(t, arrive[k] - 0.1)
                if s_ <= 0:
                    continue
                a = min(1.0, s_)
                im = _draw_dot(im, (x, y), k_px, s_, 1.0, (rd, ra))   # round 6: every stop the A.2 dot
                d = ImageDraw.Draw(im, "RGBA")
                lead = leaders[k] if k < len(leaders) else None
                if lead and a > 0.5:         # review 5: the timeline flags' weight on an ink casing, not a hairline
                    d.line(list(lead), fill=(*brand.INK, int(200 * a)), width=ROUTE_LEADER_CASE)
                    d.line(list(lead), fill=(*brand.GOLD_DIM, int(255 * a)), width=ROUTE_LEADER_W)
            if head is not None:                     # the drawing head
                d.ellipse([head[0] - 9, head[1] - 9, head[0] + 9, head[1] + 9], fill=(*WHITE, 255),
                          outline=(*brand.INK, 255), width=3)
            for name, pl in plates:
                k = stop_of[id(pl)]
                a = _ease((t - arrive[k] - 0.05) / 0.35)
                if a > 0:
                    im = draw_plate(im, pl, a, round(8 * (1 - a)))
            ab, al = scale_bar_motion(t, t_bar)
            if bar and ab > 0:
                _draw_scale_bar(im, bar, bar_x, bar_y, ab)
                for _n, pl in bar_plates:
                    im = draw_plate(im, pl, al)
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates + bar_plates + r_cplates + r_nbplates
                        + [(f"water_{w_.name}", pl) for w_, pl in waters])
        # the globe is gone by the last frame: measured on the map with it shown
        shown = _draw_globe(rest.copy(), globe, gbox or (0, 0, 0, 0), GLOBE_FADE + 0.05, out_at=globe_out)
        record_nontext(out, shown, globe_marks(globe, gbox or (0, 0, 0, 0), (width, height)))
        import json
        # round 14: the route's own layout sidecar, its names' verdicts with it (as the locator's)
        Path(str(out) + ".layout.json").write_text(json.dumps({
            "kind": "route",
            "country": ({"text": r_cname.text, "box": list(r_cname.box), "inside": r_cname.inside,
                         "leader": [list(p_) for p_ in r_leader] if r_leader else None} if r_cname is not None
                        else next(({"text": c_.text, "box": list(c_.box), "inside": True, "leader": None,
                                    "base": True} for c_ in base_checks if c_.kind == "home"), None)),
            "water": [w_.name for w_, _p in waters],
            "neighbours": sorted({str(nm).upper() for nm in named if home is None or nm != home["display"]})
            + [nb.name.upper() for nb in r_near],
            "home": None if home is None else str(home.get("display") or ""),
            "name_checks": [{"kind": c_.kind, "text": c_.text, "box": list(c_.box), "fault": c_.fault,
                             "lonlat": [list(from_frame(x_, y_)) for x_, y_ in name_check_points(c_.box)]}
                            for c_ in r_checks]}, indent=1),
            encoding="utf-8", newline="\n")
    return result


# ----------------------------------------------- every graphic for a run (make_all)
PHOTO_BUILDERS = frozenset({"numbers", "timeline"})  # builders that draw the hero photo as a backdrop


def make_all(payload: dict[str, Any], topic: dict[str, Any], work_dir: Path, photo: Path | None = None, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
             palette: dict[str, Any] | None = None, grade_bias: float | None = None, words=None) -> list[Path]:
    """Every graphic the script and topic support. Never raises: graphics are optional.
    `photo`: the topic's hero photo, used (blurred) behind the number cards.
    `palette`: the run's art direction (src/art_direction.py), made active here because this runs in a
    worker thread, where the caller's context variable would not be seen.
    `grade_bias`: the bias the master grades this run's photos with (QF-86; default the palette's grade_bias); a
    photo drawn inside a graphic takes the same grade (src/photo_grade.py)."""
    from .art_direction import use_palette
    use_palette(palette)
    bias = float((palette or {}).get("grade_bias") or 0.0) if grade_bias is None else float(grade_bias)
    made: list[Path] = []
    jobs = []
    if payload.get("stat_cards"):
        jobs.append(("numbers", lambda: render_stat_cards(payload["stat_cards"], work_dir / "graphic_numbers.mp4", photo, width=width, height=height,
                                                                narration=payload.get("narration_script") or "")))
    if payload.get("cross_section"):
        jobs.append(("cross-section", lambda: render_cross_section(payload["cross_section"], work_dir / "graphic_section.mp4", width=width, height=height)))
    if payload.get("route"):
        jobs.append(("route", lambda: render_route(payload["route"], work_dir / "graphic_route.mp4", width=width, height=height)))
    # The rolling-stone door and ventilation diagrams are retired (2026-11-02) and their renderers deleted
    # (2026-11-03): an old payload that still carries `rolling_stone_defense` or `ventilation_airflow` renders its
    # other graphics and not these.
    place = None
    try:
        place = geo.locate(topic)
    except Exception as e:  # noqa: BLE001  graphics are optional: a failed lookup drops the map, never the run
        log.warning("Map lookup failed: %s", e)
    region = str((topic.get("location") or {}).get("region") or "").strip()
    city = str((topic.get("location") or {}).get("city") or "").strip()
    if place:
        updates = {}
        if region and not place.get("region"):  # recorded with the map, so the line naming it gets the map
            updates["region"] = region
        if city and not place.get("city"):
            updates["city"] = city
        if updates:
            place = dict(place, **updates)
    if place:
        jobs.append(("map", lambda: render_map(place, work_dir / "graphic_map.mp4", width=width, height=height,
                                               narration=payload.get("narration_script") or "")))
    if payload.get("timeline"):
        jobs.append(("timeline", lambda: render_timeline(payload["timeline"], work_dir / "graphic_timeline.mp4", width=width,
                                                         height=height, photo=photo)))
    if payload.get("scale_fact"):
        jobs.append(("scale", lambda: render_scale(payload["scale_fact"], work_dir / "graphic_scale.mp4", width=width, height=height)))
    def _run_job(name, job):
        from .checkpoints import Checkpoints, file_inputs
        from .lineage import sidecar
        from .typography import FONTS_DIR, bundled_font_files
        source_files = [Path(__file__), Path(__file__).with_name("typography.py"),
                        Path(__file__).with_name("brand.py"), Path(__file__).with_name("camera_motion.py"),
                        Path(__file__).with_name("raster.py"),
                        *[FONTS_DIR / filename for filename in bundled_font_files()]]
        if photo and name in PHOTO_BUILDERS:  # timeline also draws the photo (2026-11-02)
            source_files.append(Path(photo))
            # the photo's grade (QF-86): its code and the master's curve
            source_files += [Path(__file__).with_name("photo_grade.py"), Path(__file__).with_name("video_composer.py")]
        if name == "scale":
            source_files.append(REFERENCE_FILE)
        if name in ("map", "route"):
            source_files += [p for p in (geo.COUNTRIES_FILE, geo.RELIEF_FILE) if p.is_file()]
        from .encoding import get_video_encoder
        inputs = {"sources": file_inputs(source_files), "place": place, "width": width,
                  "height": height, "fps": VIDEO_FPS, "encoder": get_video_encoder(),
                  "spec": {key: payload.get(key) for key in ("stat_cards", "cross_section", "route",
                           "timeline", "scale_fact")}}
        if name in ("numbers", "map"):  # figures (a map's scale bar) in the narration's units (2026-10-29)
            inputs["narration"] = payload.get("narration_script") or ""
        if name in PHOTO_BUILDERS:   # the photo's grade is part of the picture (QF-86)
            inputs["photo_grade_bias"] = bias
        checkpoint = Checkpoints(work_dir / "stage_cache" / name)
        try:
            if checkpoint.reusable("graphic", inputs):
                return Path(next(iter(checkpoint.read()["graphic"]["outputs"])))
            from .font_engine import surface
            from .photo_grade import using
            # set in the worker thread that draws this graphic: its photos' grade (QF-86) and its glyph events'
            # surface (QF-94), two independent thread-local values, each restored on exit
            with using(bias), surface(f"graphic {name}"):
                result = job()
            # Maps incorporate external relief/geodata and require their own source
            # registry. Only these builders are entirely drawn by this module.
            original_builders = {"cross-section", "timeline", "scale", "numbers"}
            if result and name in original_builders:
                from .lineage import record_graphic
                media = [photo] if name in PHOTO_BUILDERS and photo else []
                record_graphic(result, Path(__file__), {"payload": payload, "builder": name,
                               "width": width, "height": height}, media,
                               dependencies=[REFERENCE_FILE] if name == "scale" else [])
            if result and name in ("map", "route"):
                from .lineage import record_graphic
                sources = [p for p in (geo.COUNTRIES_FILE, geo.RELIEF_FILE) if p.is_file()]
                terms = "https://www.naturalearthdata.com/about/terms-of-use/"
                credits = [{"path": str(p.resolve()), "license": "Public domain",
                            "source_url": "https://www.naturalearthdata.com/",
                            "license_url": terms, "attribution_required": False,
                            "checked_on": "2026-10-16"} for p in sources]
                record_graphic(result, Path(__file__), {"payload": payload, "builder": name,
                               "place": place, "width": width, "height": height}, sources, credits)
            if result and sidecar(result).is_file():
                import json
                declaration = json.loads(sidecar(result).read_text(encoding="utf-8"))
                checkpoint.complete("graphic", inputs, [result, Path(result).with_suffix(".jpg"),
                                    sidecar(result), Path(declaration["sources"][0]["path"])])
            return result
        except LayoutError as e:   # QF-23: refused rather than drawn with its copy cut; held for the review card
            log.error("Graphic '%s' held: its text cannot be set whole (%s); continuing without it.", name, e)
            payload.setdefault("layout_holds", []).append({"deck": f"graphics/{name}", "error": str(e), "moved": []})
            return None
        except Exception as e:  # noqa: BLE001  never raises (make_all): one failed graphic, not the run
            log.warning("Graphic '%s' failed (%s); continuing without it.", name, e)
            return None

    if len(jobs) <= 1:
        results = [_run_job(name, job) for name, job in jobs]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(jobs), 4)) as pool:
            futures = [pool.submit(_run_job, name, job) for name, job in jobs]
            results = [f.result() for f in futures]

    made = [p for p in results if p]
    from . import card_composition
    made += card_composition.make_all(payload, work_dir / "design_cards", photo=photo, width=width, height=height, words=words)
    log.info("Original graphics (%dx%d): %s", width, height, ", ".join(p.stem.replace("graphic_", "") for p in made) or "none")
    return made
