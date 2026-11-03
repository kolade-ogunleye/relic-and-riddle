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
import logging
import math
import subprocess
import weakref
from dataclasses import dataclass
from pathlib import Path
from functools import cache, lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from . import geo
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .typography import (cap_height, drawable, fit_font, fit_lines, fit_text, get_font, plan_text,
                         spoken_system, spoken_units, tracked_width, wrap_text)
from .art_direction import accent, count_start, push_in, MOTION
from .encoding import bounded_encode

log = logging.getLogger(__name__)

CLIP_SECONDS = 6.0
W, H = VIDEO_WIDTH, VIDEO_HEIGHT
from . import brand
# Brand tokens (src/brand.py, visual overhaul 2026-10-29): one gold, bone text, the ink ground.
SEA, LAND, HOME, BORDER, GRID = brand.INK, (40, 43, 48), (92, 74, 42), (96, 98, 104), (24, 28, 34)
GOLD, WHITE, MUTED = brand.GOLD, brand.BONE, brand.STONE_LT
GOLD_LINE = brand.GOLD_DIM
M = 84  # a 9:16 graphic's left text margin (19 px inside the Reel box), shared with the Story column
# Where a 9:16 graphic may put text and figures: the Reel box every platform leaves free (safe_zones: Meta's
# 14/35/6 %, YouTube Shorts' right-hand icon column), less the caption band at its bottom. TEXT_TOP stays a
# little below the box's top edge, as it was; TEXT_MAX_W is the widest text centred on the frame.
from . import safe_zones
_BOX = safe_zones.graphics_box(VIDEO_WIDTH, VIDEO_HEIGHT)
TEXT_TOP, TEXT_BOTTOM = max(330, _BOX[1]), _BOX[3]
TEXT_LEFT, TEXT_RIGHT = _BOX[0], _BOX[2]
TEXT_MAX_W = safe_zones.REEL.centred_width(VIDEO_WIDTH, VIDEO_HEIGHT)
# The shared inset edge (2026-11-02): every 9:16 graphic (timeline, scale, cross-section, number cards) starts its
# words and drawings on one left edge at x 100 and keeps its drawings to x 100-860, inside the Reel box (65-888), so a
# cut from one graphic to the next keeps the column still (each had its own edge at 65-102 before).
INSET_X0, INSET_X1 = 100, 860


def inset_x0(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> int:
    """The shared left edge of a 9:16 graphic on this frame (INSET_X0 on 1080 wide)."""
    return round(INSET_X0 * width / VIDEO_WIDTH)


def inset_x1(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> int:
    """The right edge of a 9:16 graphic's drawings on this frame (INSET_X1 on 1080 wide)."""
    return round(INSET_X1 * width / VIDEO_WIDTH)

from .reference_facts import references, selected_reference, measurement_text, reference_note, REFERENCE_FILE

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
        targets: List[float],
        heights: List[float],
        bounds: Tuple[float, float],
        min_gap: Optional[float] = None,
        iterations: int = 40,
    ) -> List[float]:
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
    ) -> Dict[str, Any]:
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
    def boxes_intersect(box_a: Tuple[float, float, float, float], box_b: Tuple[float, float, float, float], margin: float = 0.0) -> bool:
        """Determines if two Axis-Aligned Bounding Boxes (AABB) intersect, with optional clearance margin."""
        la, ta, ra, ba = box_a
        lb, tb, rb, bb = box_b
        return not (ra + margin <= lb or rb + margin <= la or ba + margin <= tb or bb + margin <= ta)

    @staticmethod
    def box_intersects_line(box: Tuple[float, float, float, float], line_y: float, x_range: Tuple[float, float]) -> bool:
        """Determines if a bounding box intersects a horizontal line segment."""
        l, t, r, b = box
        lx0, lx1 = min(x_range), max(x_range)
        if t <= line_y <= b:
            if not (r <= lx0 or l >= lx1):
                return True
        return False


@bounded_encode
def _encode(frames: Iterable[Image.Image], out: Path, n: int, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """Raw RGB frames piped to ffmpeg (local only) -> H.264 MP4 + poster JPEG of the last frame."""
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from .encoding import get_video_encoder
        enc_args = get_video_encoder()
    except Exception:
        enc_args = ["-c:v", "libx264", "-preset", "medium", "-crf", "17"]
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(VIDEO_FPS),
           "-i", "-", "-frames:v", str(n), *enc_args, "-pix_fmt", "yuv420p", str(out)]
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
# A graphic's text is measured where the viewer reads it: on its last frame after the master's film grade
# (video_composer.film_grade_filter and noise; the lens vignette is lifted while a graphic is on screen,
# video_composer.graphic_vignette_enable, 2026-11-02), with the glyph masks it was drawn with. The planned and
# graded ratios of each element are kept beside the clip (<clip>.contrast.json) for video_qa.graphic_contrast_problems.
CONTRAST_SIDECAR = ".contrast.json"


def graded_frame(frame: Image.Image, work: Path, vignette: bool = False) -> Optional[Image.Image]:
    """`frame` through the master's picture chain (format, hue-safe film print, noise; `vignette` adds the lens
    vignette, which the master lifts under graphic clips); None when ffmpeg cannot run here (the planned ratios
    are still recorded)."""
    try:
        from .video_composer import film_grade_filter
        work.mkdir(parents=True, exist_ok=True)
        src, dst = work / "_contrast_in.png", work / "_contrast_out.png"
        frame.convert("RGB").save(src)
        aspect = "16/9" if frame.width > frame.height else "9/16"
        vf = (f"format=yuv420p,{film_grade_filter(0.0, out_dir=work)},format=yuv420p,"
              f"noise=alls=8:allf=t+u:all_seed=7,{f'vignette=PI/4.2:aspect={aspect},' if vignette else ''}format=rgb24")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", str(dst)],
                       check=True, timeout=120, capture_output=True)
        with Image.open(dst) as im:
            out = im.convert("RGB")
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)
        return out
    except Exception as e:  # a measurement, never a reason to lose the graphic
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


def record_contrast(out: Path, frame: Image.Image, plates: List[Tuple[str, Any]]) -> Dict[str, Any]:
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


def _label_plates(frame: Image.Image, cx: int, y: int, title: str, sub: str) -> List[Tuple[str, Any]]:
    """The place label as knockout type, no pill (2026-10-29): the place in bone Anton, the country under it in
    tracked gold Montserrat, each on its own ink halo at least 80% deep (the map under it is busy at any value),
    raised further while the ratio is short of the target. Planned on the final frame; drawn on every frame."""
    max_w = centred_text_width(frame.width, frame.height) - 20
    d = ImageDraw.Draw(frame)
    # One line down to 56 px, else two balanced lines (a long name, "Richat Structure, the Eye of the Sahara"),
    # the last on the label's baseline, so the country line and the pin keep their places.
    lines, f1, _ = fit_text(d, title.upper(), "Anton", 120, max_w, 1, 56, sentences=False, quiet=True)
    if d.textlength(lines[0] if lines else "", font=f1) > max_w:
        lines, f1, _ = fit_text(d, title.upper(), "Anton", 88, max_w, 2, 48, sentences=False)
    f2 = get_font("Montserrat", 40, weight=800)
    while tracked_width(sub.upper(), f2, f2.size * 0.15) > max_w and f2.size > 24:
        f2 = get_font("Montserrat", f2.size - 2, weight=800)
    step = round(cap_height(f1) * 1.15)
    plates = [("place", plan_text(frame, (cx, y - step * (len(lines) - 1 - k)), ln, f1, WHITE, "ms", halo_px=25,
                                  min_halo=0.8)) for k, ln in enumerate(lines)]
    plates.append(("country", plan_text(frame, (cx, y + 20 + cap_height(f2) + 16), sub.upper(), f2, GOLD, "ms",
                                        track=f2.size * 0.15, halo_px=21, min_halo=0.8)))
    return plates


# Nice lengths for a scale bar, in its unit.
SCALE_STEPS = (5, 10, 20, 25, 50, 100, 150, 200, 250, 300, 500, 1000)


def scale_bar(lat: float, px_per_deg_lat: float, narration: str, max_px: float = 260) -> Dict[str, Any]:
    """A scale bar for the map's last frame, in the unit the narration speaks for lengths (2026-10-29: the drawn
    unit follows the voice; km when the narration gives no length): its length in that unit (the longest nice
    length up to `max_px`, 260 px), its px and label."""
    import math
    phi = math.radians(lat)
    km_per_deg = (111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)) / 1000.0
    imperial = spoken_system(narration) == "imperial"
    unit_km = 1.609344 if imperial else 1.0
    px_per_unit = px_per_deg_lat / km_per_deg * unit_km
    value = max([v for v in SCALE_STEPS if v * px_per_unit <= max_px] or [SCALE_STEPS[0]])
    px = round(value * px_per_unit, 1)
    # Round 4 (2026-11-02): the other system's ticks under the bar, each at its own true length (not one converted
    # label): the longest nice length no longer than the spoken unit's bar.
    px_other = px_per_deg_lat / km_per_deg * (1.0 if imperial else 1.609344)
    other = max([v for v in SCALE_STEPS if v * px_other <= px + 0.5] or [SCALE_STEPS[0]])
    unit2 = "KM" if imperial else "MI"
    return {"value": value, "unit": "MI" if imperial else "KM", "px": px,
            "label": f"{value} {'MI' if imperial else 'KM'}", "km_per_deg_lat": round(km_per_deg, 3),
            "secondary": {"value": other, "unit": unit2, "px": round(other * px_other, 1), "label": f"{other} {unit2}"}}


ROUTE_BAR_STEPS = (260, 180, 120)  # round 4 review 3: a route's scale bar, longest first, until every stop is named
ROUTE_SCALE_AIR = 24  # round 4 review 3: px a route stop's name keeps from the scale bar's figures (plus the 8 px margin)
SCALE_BAR_LIFT = 40   # round 4: px the bar rises so the second unit's ticks and label sit under it
# Round 4 review 4 (2026-11-02): the bar's figures are reference type, a step under the stops' 34 px Montserrat 800
# names: Plex Mono Medium at the 30 px tertiary floor (38 px SemiBold before, as heavy as a name and wider than a
# 120 px bar), both in BONE: STONE_LT measured 3.5-4.0:1 over a gold home country even at full halo.
SCALE_FIGURE_PX = 30
SCALE_FIGURE_WEIGHT = 500
# Round 4 review 6 (2026-11-03): on 16:9 the 30 px figures (cap 21) stood too close to the 34 px names (cap 24); they
# are 26 px there (over WIDE_TERTIARY_MIN), clearly the reference type.
SCALE_FIGURE_PX_WIDE = 26


def scale_figure_px(width: int, height: int) -> int:
    """The scale bar's figures' size for a frame: SCALE_FIGURE_PX on 9:16, SCALE_FIGURE_PX_WIDE on 16:9."""
    return SCALE_FIGURE_PX if height > width else SCALE_FIGURE_PX_WIDE


def _scale_bar_plates(rest: Image.Image, bar: dict[str, Any], x: float, y: float,
                      px: int = SCALE_FIGURE_PX) -> list[tuple[str, Any]]:
    """The bar's figures: the spoken unit's beside its end over the line, the other unit's under the line centred
    on its own end tick, both measured knockout labels. Round 4 review (2026-11-02): the second label sat at the
    main bar's end (x + max(sec, bar) + 22), so "150 KM" stood over "50 MI" as a false equivalence (150 km is about
    93 mi); each label now marks its own tick, so no two lengths read as equal unless they are."""
    f1 = get_font(brand.DATA_FONT, px, weight=SCALE_FIGURE_WEIGHT)
    out = [("scale", plan_text(rest, (x + bar["px"] + 18, y + 11), bar["label"], f1, WHITE, "ls", track=1,
                               min_halo=0.7, halo_px=21))]
    sec = bar.get("secondary")
    if sec:
        f2 = f1
        half = tracked_width(sec["label"], f2, 1) / 2
        cx = max(x + sec["px"], x + half)         # on its tick; never left of the bar's zero end
        out.append(("scale_secondary", plan_text(rest, (cx, y + 13 + 40), sec["label"], f2, WHITE, "ms",
                                                 track=1, min_halo=0.7, halo_px=21)))
    return out


def _draw_scale_bar(im: Image.Image, bar: dict[str, Any], x: float, y: float, ab: float) -> None:
    """A cartographic bar drawn from its left end (`ab` 0-1): alternating bone and stone quarters over an ink
    keyline, the spoken unit's ticks up, the other unit's ticks down at its own true length."""
    if ab <= 0:
        return
    d = ImageDraw.Draw(im, "RGBA")
    x1 = x + bar["px"] * ab
    d.line([(x, y), (x1, y)], fill=(*brand.INK, 200), width=11)
    for k in range(4):                                        # alternating quarters
        a, b = x + bar["px"] * k / 4, min(x1, x + bar["px"] * (k + 1) / 4)
        if b > a:
            d.line([(a, y), (b, y)], fill=(*(WHITE if k % 2 == 0 else brand.STONE), 255), width=5)
    for xx, tall in ((x, 16), (x + bar["px"] / 2, 9), (x + bar["px"], 16)):
        if xx <= x1 + 0.5:
            d.line([(xx, y - tall), (xx, y + 2)], fill=(*WHITE, 255), width=5)
    sec = bar.get("secondary")
    if sec:
        for xx in (x, x + sec["px"]):
            if xx <= x1 + 0.5:
                d.line([(xx, y - 2), (xx, y + 14)], fill=(*MUTED, 255), width=4)


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
LAND_TINT, HOME_TINT = (88, 92, 100), (150, 122, 72)
# Round 3 (2026-11-02): neighbours' names and borders on the nearest brand token (an untokenised (150, 152, 160)
# grey before), the coastal glow's strength on TEAL.
MAP_NAME, MAP_BORDER, MAP_GLOW = brand.STONE, brand.STONE, 0.38
MAP_NAME_PX = 36      # round 4: a neighbour's name on the close-up (it was 66 px in the base, about 49 on the frame)
MAP_NAME_HALO = 0.75  # the depth of its ink halo


def camera_spans(n: int, wide: float, close: float, travel: float, fps: int) -> list[float]:
    """The locator's span (degrees across the frame) on each of `n` frames (round 3, 2026-11-02): eased in and
    out in log space over `travel` seconds, so each halving of the view takes the same time (a cubic ease-out on
    the linear span spent most of the move in the first half-second), then held."""
    lw, lc = math.log(wide), math.log(close)
    return [math.exp(lw + (lc - lw) * _ease_io(i / fps / travel)) for i in range(n)]


GLOBE_PX = 160            # round 4: the context globe inset's diameter at 1080 wide (both orientations)
GLOBE_FADE = 0.4          # s: it fades in over the clip's first 0.4 s and holds to the last frame
MAP_LEADER_MAX = 60       # round 4: px, the place label's leader to its pin (about 210 px of hairline before)


def globe_box(width: int, height: int, corner: str = "tr", shrink: float = 1.0) -> tuple[int, int, int, int]:
    """Where the context globe sits: 9:16 top-right inside the Reel box (x 705-865, y 350-510 at full size); 16:9
    top-right of title-safe (clear of YouTube's timestamp corner, which is bottom-right). Round 4 review 4
    (2026-11-02): a route map may move it to the top-left (`corner` "tl", the same inset from that edge) or draw it
    `shrink` times its size, so it never takes a stop's name."""
    if width > height:
        k = min(width / 1920, height / 1080)
        size = round(170 * k * shrink)
        x1, y0 = round(WIDE_TITLE_SAFE[2] * k), round((WIDE_TITLE_SAFE[1] + 10) * k)
        if corner == "tl":
            x0 = round(WIDE_TITLE_SAFE[0] * k)
            return x0, y0, x0 + size, y0 + size
        return x1 - size, y0, x1, y0 + size
    k = width / VIDEO_WIDTH
    size = round(GLOBE_PX * k * shrink)
    x1, y0 = round(865 * k), max(round(350 * height / VIDEO_HEIGHT), TEXT_TOP)
    if corner == "tl":                       # mirrored: as far inside the Reel box's left edge as x1 is from its right
        bx0, _by0, bx1, _by1 = safe_zones.graphics_box(width, height)
        x0 = bx0 + (bx1 - x1)
        return x0, y0, x0 + size, y0 + size
    return x1 - size, y0, x1, y0 + size


def globe_inset(size: int, lon0: float, lat0: float, home, footprint: tuple[float, float, float, float]) -> Image.Image:
    """The context globe (round 4, 2026-11-02: a final frame showed one country in unnamed water, with no answer to
    "where on Earth"): an orthographic globe centred on the place, Natural Earth land in STONE on INK, the home
    country GOLD_DIM, the camera's final footprint (lon0, lat0, lon1, lat1) as a 3 px GOLD outline; no words.
    An RGBA image `size` px across."""
    ss = 3
    big = size * ss
    r = big / 2 - 2 * ss
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = cy = big / 2

    def px(x: float, y: float) -> tuple[float, float]:
        return cx + x * r, cy - y * r
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*brand.INK, 255))
    if geo.available():
        for c, rings in geo.globe_rings(lon0, lat0):
            fill = brand.GOLD_DIM if home is not None and c is home else brand.STONE
            for ring in rings:
                d.polygon([px(x, y) for x, y in ring], fill=(*fill, 255))
    lo0, la0, lo1, la1 = footprint
    edge = []
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
        d.line(pts + [pts[0]], fill=(*brand.GOLD, 255), width=3 * ss, joint="curve")
    # the limb: a GOLD_DIM ring, and nothing drawn outside the disc
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    clipped = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    clipped.paste(im, (0, 0), mask)
    ImageDraw.Draw(clipped).ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*brand.GOLD_DIM, 255), width=2 * ss)
    return clipped.resize((size, size), Image.LANCZOS)


def _draw_globe(im: Image.Image, globe: Image.Image | None, box: tuple[int, int, int, int], t: float) -> Image.Image:
    """The context globe composited onto `im` (RGB, changed in place and returned) at its fade-in by `t`. Round 4
    review 6 (2026-11-03): only the globe's own box is composited (the whole frame went to RGBA and back on every
    frame, ~11 ms of a route's frame, long after the fade had ended)."""
    a = _ease(t / GLOBE_FADE)
    if globe is None or a <= 0:
        return im
    g = globe if a >= 0.999 else globe.copy()
    if a < 0.999:
        g.putalpha(globe.getchannel("A").point(lambda v, a=a: int(v * a)))
    x0, y0 = box[0], box[1]
    region = im.crop((x0, y0, x0 + g.width, y0 + g.height)).convert("RGBA")
    region.alpha_composite(g)
    im.paste(region.convert(im.mode), (x0, y0))
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
    """The globe's disc as a non-text mark mask on the frame (its contrast against the map around it)."""
    if globe is None:
        return []
    m = Image.new("L", size, 0)
    m.paste(globe.getchannel("A").point(lambda v: 255 if v > 127 else 0), (box[0], box[1]))
    return [("globe", m)]


MAP_TRAVEL = 2.2          # s: the camera's move from the wide view to the close one
MAP_PIN_AT = MAP_TRAVEL + 0.1   # the pin drops once the camera rests


@lru_cache(maxsize=1)
def _relief() -> Optional[Image.Image]:
    """Natural Earth shaded relief (21600x10800, public domain), loaded once."""
    if not geo.RELIEF_FILE.exists():
        return None
    limit, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None  # our own trusted 233 MP raster
    try:
        im = Image.open(geo.RELIEF_FILE)
        im.load()
        return im.convert("L")
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def relief_base(lat0: float, lon0: float, bw: int, bh: int, kx: float, coslat: float, home, project,
                final_view: Tuple[float, float, float, float], label_bottom_fraction: float = 1.0,
                reserved: Tuple[Tuple[float, float, float, float], ...] = (),
                final_safe: Optional[Tuple[float, float, float, float]] = None,
                transient: Optional[List[Dict[str, Any]]] = None, name_px: int = 66,
                skip: frozenset[str] | set[str] = frozenset(), name_home: bool = False) -> Image.Image:
    """The map background: hillshaded land (the topic's country in warm gold), a navy sea with a
    soft coastal glow, thin borders, and neighbouring countries named at Natural Earth's label points.
    final_safe: the safe box of the last frame, in base pixels; a name drawn into the base must lie in it.
    transient: given, the names outside the close-up are not drawn into the base but added to it
    ({"name", "sprite": an L mask, "box": its base box}) for the frames to draw while wholly visible;
    the names drawn into the base are added with "sprite": None."""
    import numpy as np
    lon_span, lat_span = bw / (kx * coslat), bh / kx
    lon_min, lat_max = lon0 - lon_span / 2, lat0 + lat_span / 2
    rel = _relief()
    if rel is not None:
        px = lambda lon: (lon + 180) / 360 * rel.width  # noqa: E731
        py = lambda lat: (90 - lat) / 180 * rel.height  # noqa: E731
        box = (int(px(lon_min)), int(py(lat_max)), int(px(lon_min + lon_span)), int(py(lat_max - lat_span)))
        hill = np.asarray(rel.crop(box).resize((bw, bh), Image.BICUBIC), dtype=np.float32) / 255.0
    else:
        hill = np.full((bh, bw), 0.75, dtype=np.float32)

    lon_half, lat_half = lon_span / 2 + 2, lat_span / 2 + 2
    land_m, home_m = Image.new("L", (bw, bh), 0), Image.new("L", (bw, bh), 0)
    dl, dh = ImageDraw.Draw(land_m), ImageDraw.Draw(home_m)
    visible = []
    for c in geo.countries():
        shown = False
        for poly in c["polygons"]:
            ring = poly[0]
            if not any(abs(x - lon0) < lon_half and abs(y - lat0) < lat_half for x, y in ring[:: max(1, len(ring) // 50)]):
                continue
            shown = True
            pts = [project(x, y) for x, y in ring]
            dl.polygon(pts, fill=255)
            if c is home:
                dh.polygon(pts, fill=255)
            for hole in poly[1:]:
                hp = [project(x, y) for x, y in hole]
                dl.polygon(hp, fill=0)
                dh.polygon(hp, fill=0)
        if shown:
            visible.append(c)
    land = np.asarray(land_m, dtype=np.float32)[..., None] / 255.0
    homea = np.asarray(home_m, dtype=np.float32)[..., None] / 255.0
    shade = (0.30 + 0.95 * hill)[..., None]
    land_rgb = np.array(LAND_TINT, dtype=np.float32) * shade
    home_rgb = np.array(HOME_TINT, dtype=np.float32) * shade
    rows = np.linspace(0, 1, bh, dtype=np.float32)[:, None, None]
    sea_rgb = np.array((14, 22, 34), dtype=np.float32) * (1 - rows) + np.array((7, 11, 19), dtype=np.float32) * rows
    glow = np.asarray(land_m.filter(ImageFilter.GaussianBlur(28)), dtype=np.float32)[..., None] / 255.0
    # The coastal glow on a brand token (round 3, 2026-11-02: an off-token sky blue (40, 62, 84) before): TEAL at
    # a low alpha, so the coast still reads against the land at phone size.
    sea_rgb = sea_rgb + np.array(brand.TEAL, dtype=np.float32) * np.clip(glow - land, 0, 1) * MAP_GLOW
    out = sea_rgb * (1 - land) + land_rgb * land * (1 - homea) + home_rgb * homea
    hb = home_m.getbbox() if home is not None else None
    if hb:                                       # a bone inner stroke 6 px inside the home country's border
        # Round 4 review 5 (2026-11-02): eroded within the country's own bounds (cv2, the edge replicated as
        # PIL's MinFilter does), not by a 13 px rank filter over the whole 2x canvas (~3.5 s a render)
        import cv2
        pad_ = 16
        cx0, cy0 = max(0, hb[0] - pad_), max(0, hb[1] - pad_)
        cx1, cy1 = min(bw, hb[2] + pad_), min(bh, hb[3] + pad_)
        crop = np.asarray(home_m.crop((cx0, cy0, cx1, cy1)))
        inner = cv2.erode(crop, np.ones((13, 13), np.uint8), borderType=cv2.BORDER_REPLICATE)
        thin = cv2.erode(inner, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_REPLICATE)
        ring = np.clip(inner.astype(np.float32) - thin.astype(np.float32), 0, 255)[..., None] / 255.0
        region = out[cy0:cy1, cx0:cx1]
        out[cy0:cy1, cx0:cx1] = region * (1 - ring * 0.5) + np.array(brand.BONE, dtype=np.float32) * ring * 0.5
    base = Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")

    draw = ImageDraw.Draw(base, "RGBA")
    for c in sorted(visible, key=lambda c: c is home):  # borders crisp on top; the home outline last, in gold
        for poly in c["polygons"]:
            ring = poly[0]
            if any(abs(x - lon0) < lon_half and abs(y - lat0) < lat_half for x, y in ring[:: max(1, len(ring) // 50)]):
                colour, width = ((*accent(),), 7) if c is home else (MAP_BORDER, 3)
                draw.line([project(x, y) for x, y in ring] + [project(*ring[0])], fill=colour, width=width)

    # Neighbouring countries, named at Natural Earth's label points. A label is drawn only if it
    # collides with no other label and is either wholly inside the final close-up or wholly
    # outside it, so the last frame never shows half a name.
    font = get_font("Montserrat", name_px, weight=700)   # round 4: `name_px` sized for the close-up (66 fixed before)
    home_np = np.asarray(home_m) if home is not None else None
    placed: List[Tuple[float, float, float, float]] = list(reserved)  # e.g. the pin and place label
    for c in sorted(visible, key=lambda c: (c["label_rank"], -c["size"])):
        # Areas of indeterminate status (e.g. Siachen Glacier) are not countries and stay unnamed.
        if (c is home and not name_home) or not c["label_at"] or c["label_rank"] > 5 or c.get("type") == "Indeterminate":
            continue
        if c["display"] in skip:          # measured under brand.TEXT_CONTRAST on the close-up: not named
            continue
        x0_, y0_ = project(*c["label_at"])
        name = " ".join(c["display"].upper())  # letter-spaced small caps
        l0, t0, r0, b0 = draw.textbbox((x0_, y0_), name, font=font, anchor="mm")
        if c.get("extent"):  # a name much wider than its land reads as naming something else
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
                break
    return base


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
        transient.append({"name": c["display"], "sprite": sprite,
                          "box": (l - 4, t - 4, l - 4 + sprite.width, t - 4 + sprite.height)})
        return True
    glyph = Image.new("L", (int(r - l) + 8, int(b - t) + 8), 0)
    ImageDraw.Draw(glyph).text((4 - l + x, 4 - t + y), drawable(name, font), font=font, fill=255, anchor="mm")
    if transient is not None:  # drawn into the base: recorded so its place on each frame can be checked
        transient.append({"name": c["display"], "sprite": None, "box": box, "glyph": glyph,
                          "gbox": (l - 4, t - 4, l - 4 + glyph.width, t - 4 + glyph.height)})
    # round 4: an ink halo under the name, so it is measured (and passes) on busy relief
    halo = glyph.filter(ImageFilter.MaxFilter(max(3, round(font.size * 0.18)) | 1)).filter(
        ImageFilter.GaussianBlur(max(2, font.size * 0.12))).point(lambda v: int(v * MAP_NAME_HALO))
    draw._image.paste(brand.INK, (int(l - 4), int(t - 4)), halo)
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


def render_map(place: Dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
               narration: str = "") -> Optional[Path]:
    """Regional locator map: moves from a ~40° view to ~18° around the site (camera_spans: log-space ease, then
    held), and only once the camera rests the arrival: the pin drops with a small overshoot and one ripple that
    settles, the label rises, a bone leader ties it to the pin, and a scale bar in the narration's unit draws from
    its left end (both orientations; round 3, 2026-11-02). A point that stands for the topic's whole region
    (geo.is_region_fallback) is drawn as an area with "APPROX. LOCATION", never as a precise pin.
    `narration`: the run's narration (typography.spoken_system)."""
    if not geo.available():
        return None
    lat0, lon0 = place["lat"], place["lon"]
    span_wide, span_close, scale = 40.0, 18.0, 3  # degrees of longitude across the frame (keeps neighbours in view)
    kx = width * scale / span_wide                  # px per degree of longitude in the base image
    coslat = max(0.2, math.cos(math.radians(lat0)))
    bw, bh = width * scale, height * scale

    # Equirectangular with the x axis shrunk by cos(lat) so shapes keep their proportions.
    def project(lon: float, lat: float) -> Tuple[float, float]:
        return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx

    zf = span_wide / span_close  # the close-up the camera ends on (labels must not be cut by it)
    final_view = (bw / 2 - bw / 2 / zf, bh / 2 - bh / 2 / zf, bw / 2 + bw / 2 / zf, bh / 2 + bh / 2 / zf)
    label = place["label"]
    country = place["country"]
    city = str(place.get("city") or "").strip()
    if city and city.lower() != label.lower() and city.lower() != country.lower():
        sub_label = f"{city}, {country}"
    else:
        sub_label = country
    area = geo.is_region_fallback(place)
    cx, cy = width // 2, height // 2
    # Round 4 (2026-11-02): the label on its pin, its leader under MAP_LEADER_MAX (it floated about 210 px above
    # the pin on a hairline that read as a title card's cable): measured on a blank frame (a plate's box does not
    # depend on its ground), its last line ending 72 px over the pin's top, never above the text box.
    probe_frame = Image.new("RGB", (width, height), brand.INK)
    trial = _label_plates(probe_frame, cx, cy, label, sub_label)
    rel_top, rel_bottom = min(pl.box[1] for _, pl in trial) - cy, max(pl.box[3] for _, pl in trial) - cy
    if area:
        rel_bottom += 16 + cap_height(get_font("Montserrat", 30, weight=700)) + 6
    pin_top = cy - (78 if area else 22)
    label_y = round(max(get_text_bounds(width, height)[0] - rel_top, pin_top - 72 - rel_bottom))
    # Keep neighbours' names clear of the place label, its leader and the pin in the final close-up: the band
    # from the label's top to just below the pin, across the label's width plus a margin (output pixels).
    half_w = min(width // 2, 60 + max(len(label) * 22, len(sub_label) * 18))
    to_base = lambda x, y: (final_view[0] + x * (final_view[2] - final_view[0]) / width,  # noqa: E731
                            final_view[1] + y * (final_view[3] - final_view[1]) / height)
    reserved = (to_base(width // 2 - half_w, label_y + rel_top - 10) + to_base(width // 2 + half_w, height // 2 + 70),)
    gbox = globe_box(width, height)                     # round 4: and none under the context globe
    reserved += (to_base(gbox[0] - 16, gbox[1] - 16) + to_base(gbox[2] + 16, gbox[3] + 16),)
    portrait = height > width
    # The scale bar: the final close-up shows `span_close` degrees of longitude across the frame at cos(lat) scale,
    # so one degree of latitude is width / span_close px there; drawn once the camera rests, in both orientations
    # (16:9 bottom-left of title-safe, clear of YouTube's timestamp corner).
    bar = scale_bar(lat0, width / span_close, narration)
    sx0, sy0, sx1, sy1 = get_text_box(width, height)
    bar_x = M if portrait else sx0 + 10
    bar_y = (TEXT_BOTTOM - 22 if portrait else sy1 - 30) - SCALE_BAR_LIFT
    if bar:  # no neighbour's name under the bar and its labels
        reserved += (to_base(bar_x - 20, bar_y - 70) + to_base(bar_x + bar["px"] + 260, bar_y + 30 + SCALE_BAR_LIFT),)
    labels: List[Dict[str, Any]] = []
    home = geo.find_country(place["country"])
    name_px = round(MAP_NAME_PX * scale / zf)          # round 4: a neighbour's name MAP_NAME_PX tall on the close-up
    base = relief_base(lat0, lon0, bw, bh, kx, coslat, home, project, final_view,
                       label_bottom_fraction=get_text_bounds(width, height)[1] / height, reserved=reserved,
                       final_safe=to_base(sx0, sy0) + to_base(sx1, sy1), transient=labels, name_px=name_px)

    n = int(CLIP_SECONDS * VIDEO_FPS)
    zooms = [span_wide / sp for sp in camera_spans(n, span_wide, span_close, MAP_TRAVEL, VIDEO_FPS)]
    plan = map_label_plan(labels, zooms, width, height, bw, bh)
    # The place label and the bar's figure are planned on the camera's resting view (their halos solved there).
    zf_last = zooms[-1]
    rest = base.resize((width, height), Image.BICUBIC, box=(bw / 2 - bw / 2 / zf_last, bh / 2 - bh / 2 / zf_last,
                                                            bw / 2 + bw / 2 / zf_last, bh / 2 + bh / 2 / zf_last)).convert("RGB")
    # Neighbours' names under the measured-contrast rule (round 4): each name drawn into the base is measured on
    # the resting view, and the base is drawn again without any name under brand.TEXT_CONTRAST.
    names = _base_name_plates(labels, rest, final_view, width, height)
    weak = {nm.text for nm in names if nm.contrast is not None and nm.contrast < brand.TEXT_CONTRAST}
    if weak:
        labels = []
        base = relief_base(lat0, lon0, bw, bh, kx, coslat, home, project, final_view,
                           label_bottom_fraction=get_text_bounds(width, height)[1] / height, reserved=reserved,
                           final_safe=to_base(sx0, sy0) + to_base(sx1, sy1), transient=labels, name_px=name_px,
                           skip=weak)
        plan = map_label_plan(labels, zooms, width, height, bw, bh)
        rest = base.resize((width, height), Image.BICUBIC, box=(bw / 2 - bw / 2 / zf_last, bh / 2 - bh / 2 / zf_last,
                                                                bw / 2 + bw / 2 / zf_last, bh / 2 + bh / 2 / zf_last)).convert("RGB")
        names = _base_name_plates(labels, rest, final_view, width, height)
    # the context globe: centred on the place, the close-up's footprint outlined
    lon_half_c = (bw / zf_last / 2) / (kx * coslat)
    lat_half_c = (bh / zf_last / 2) / kx
    globe = globe_inset(gbox[2] - gbox[0], lon0, lat0, home,
                        (lon0 - lon_half_c, lat0 - lat_half_c, lon0 + lon_half_c, lat0 + lat_half_c))
    label_plates = _label_plates(rest, width // 2, label_y, label, sub_label)
    if area:   # the region's point: say so, in the system-label pattern ("APPROX. DEPTHS")
        f_ap = get_font("Montserrat", 30, weight=700)
        under = max(pl.box[3] for _, pl in label_plates)
        label_plates.append(("approx", plan_text(rest, (width // 2, under + 16 + cap_height(f_ap)), "APPROX. LOCATION",
                                                 f_ap, MUTED, "ms", track=4, halo_px=21, min_halo=0.8)))
    lead_top = max(pl.box[3] for _, pl in label_plates) + 14
    lead_bot = cy - (78 if area else 34)
    bar_plates = _scale_bar_plates(rest, bar, bar_x, bar_y, scale_figure_px(width, height)) if bar else []
    t_pin, t_label = MAP_PIN_AT, MAP_PIN_AT + 0.35
    last: List[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            zoom = zooms[i]
            cw, ch = bw / zoom, bh / zoom
            box = (bw / 2 - cw / 2, bh / 2 - ch / 2, bw / 2 + cw / 2, bh / 2 + ch / 2)
            im = base.resize((width, height), Image.BICUBIC, box=box).convert("RGB")
            for lab in plan[i]:
                if lab["mask"] is not None:
                    im.paste(MAP_NAME, lab["box"][:2], lab["mask"])
            im = _draw_globe(im, globe, gbox, t)
            d = ImageDraw.Draw(im, "RGBA")
            if area:      # the region as a soft area: a gold-edged disc that settles to its size
                a_ar = _ease((t - t_pin) / 0.35)
                if a_ar > 0:
                    r = 70 * (1 + 0.08 * math.sin(math.pi * min(1.0, (t - t_pin) / 0.35)))
                    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*GOLD, int(46 * a_ar)),
                              outline=(*GOLD, int(230 * a_ar)), width=4)
            else:
                # one ripple as the pin lands, settling (no endless pulse)
                u_r = (t - t_pin - 0.2) / 0.7
                if 0 < u_r < 1:
                    r = 22 + 80 * _ease(u_r)
                    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*accent(), int(200 * (1 - u_r))), width=4)
                if t >= t_pin:
                    u = (t - t_pin) / 0.2                      # a 200 ms drop overshooting by about 8 %
                    drop = 1 + 0.08 * math.sin(math.pi * min(1.0, (u - 1) / 0.6)) if u > 1 else _ease(u)
                    drop = drop if u < 1.6 else 1.0
                    py = cy - 160 * (1 - drop)
                    d.ellipse([cx - 22, py - 22, cx + 22, py + 22], fill=(*accent(), 255), outline=(*brand.INK, 255),
                              width=4)
                    d.ellipse([cx - 7, py - 7, cx + 7, py + 7], fill=(*brand.INK, 255))
            a = _ease((t - t_label) / 0.4)
            for _, plate in label_plates:  # knockout labels rise 12 px as they fade in
                im = draw_plate(im, plate, a, round(12 * (1 - a)))
            al = _ease((t - t_label - 0.2) / 0.3)
            if al > 0 and lead_bot - lead_top > 28:          # the leader from the label down to the pin
                d = ImageDraw.Draw(im, "RGBA")
                y_end = lead_top + (lead_bot - lead_top) * al
                d.line([(cx, lead_top), (cx, y_end)], fill=(*brand.INK, int(200 * al)), width=6)
                d.line([(cx, lead_top), (cx, y_end)], fill=(*WHITE, int(255 * al)), width=2)
            if bar:
                ab = _ease((t - t_label - 0.15) / 0.3)        # drawn from its left end
                if ab > 0:
                    _draw_scale_bar(im, bar, bar_x, bar_y, ab)
                    for _n, pl in bar_plates:
                        im = draw_plate(im, pl, _ease((t - t_label - 0.35) / 0.3))
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], label_plates + bar_plates
                        + [(f"neighbour_{nm.text}", nm) for nm in names])
        record_nontext(out, last[0], globe_marks(globe, gbox, (width, height)))
        if bar:
            import json
            Path(str(out) + ".scale.json").write_text(json.dumps(bar, indent=1), encoding="utf-8", newline="\n")
    return result


def map_label_plan(labels: List[Dict[str, Any]], zooms: List[float], width: int, height: int,
                   bw: int, bh: int) -> List[List[Dict[str, Any]]]:
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

    plan: List[List[Dict[str, Any]]] = [[] for _ in zooms]
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
            alpha = min(1.0, (shown - i) / fade)
            if alpha < 1.0:
                mask = mask.point(lambda v, a=alpha: int(v * a))
            plan[i].append({"name": lab["name"], "box": b, "mask": mask})
    return plan


def get_text_bounds(width: int, height: int) -> Tuple[int, int]:
    """(top, bottom) between which a graphic's text and diagrams stay. 16:9 (the documentary): broadcast
    title-safe above the caption line. 9:16: the Reel box the platforms leave free, less the caption band
    (safe_zones.graphics_box), so no figure sits under a platform's interface or reads as a caption."""
    if width > height:
        return int(height * 0.12), int(height * 0.76)  # 130px to 820px (captions center at 965px)
    box = safe_zones.graphics_box(width, height)
    return max(round(330 * height / VIDEO_HEIGHT), box[1]), box[3]


def get_text_box(width: int, height: int) -> Tuple[int, int, int, int]:
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


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> List[str]:
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


_LENGTHS: "weakref.WeakKeyDictionary[Any, dict[str, float]]" = weakref.WeakKeyDictionary()


def _tracked_w(text: str, font, track: float) -> float:
    """typography.tracked_width without a new probe image per call, each font's advances memoised (a 16:9 layout
    wraps the same labels at the same sizes for every measure it tries)."""
    lengths = _LENGTHS.setdefault(font, {})
    w = lengths.get(text)
    if w is None:
        if len(lengths) > 50_000:
            lengths.clear()
        w = lengths[text] = _TRACK_PROBE.textlength(text, font=font)
    return w + track * max(0, len(text) - 1)


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

    @cache
    def rest(i: int, lines: int) -> tuple[float, float, tuple[int, ...]] | None:
        # words[i:] on exactly `lines` lines: (widest, sum of squared widths, the breaks), the widest least
        if lines == 1:
            w = w_of(i, n)
            return (w, w * w, ()) if w <= width + 1e-6 else None
        best = None
        for j in range(i + 1, n - lines + 2):
            w = w_of(i, j)
            if w > width + 1e-6:
                break
            sub = rest(j, lines - 1)
            if sub is None:
                continue
            cand = (max(w, sub[0]), w * w + sub[1], (j, *sub[2]))
            if best is None or cand[:2] < best[:2]:
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
            lone = w < lone_share * sub[0] or (count >= 3 and n == count)
        choices.append((lone, round(max(w, sub[0]), 3), w * w + sub[1], (j, *sub[2])))
    if not choices:
        return None
    breaks = (0, *min(choices)[3], n)
    from itertools import pairwise
    return [" ".join(words[a:b]) for a, b in pairwise(breaks)]


def fit_tracked(probe: ImageDraw.ImageDraw, text: str, name: str, px: int, width: float, rows: int, lo: int,
                track: float, sentences: bool = True, weight: int | None = None, quiet: bool = False,
                **_kw: Any) -> tuple[list[str], Any, bool]:
    """typography.fit_text for lines drawn with `track` px of letter spacing: (lines, font, shortened). Every
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
    font = get_font(name, px, weight=weight)
    for s in sizes or [px]:
        font = get_font(name, s, weight=weight)
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
            font = get_font(name, s, weight=weight)
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
TIMELINE_SPAN_RATIO = 0.8         # a 9:16 span label on its segment: at most this share of the labels' size
SPAN_AIR = 0.6                    # round 4: a 9:16 span band's air above and below its label, in label sizes
SPAN_MIN_PX = 30                  # round 4: the 9:16 span label's floor (24 before: tertiary type on a phone)
TALL_LABEL_MIN_PX = 34            # round 4: 9:16 event labels never step below this while the years can (30 before)
TALL_TOP_PAD = 12                 # px from the Reel box's top (safe_zones.graphics_box) to the first year's cap
                                  # (round 4 review 2: 30 px under TEXT_TOP's legacy 330 before, 72 px in all)
TALL_YEAR_STEPS = (1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.6)   # the Anton years' ladder, paid first
CIRCA = "c."                      # round 4: circa drawn lowercase, smaller, in Montserrat beside the Anton year
SPAN_UNDER_LABEL = 2             # round 4 review 3: a 9:16 span is always at least this many px under the labels
TALL_LABEL_ROWS = 3               # round 4 review 3: a 9:16 label's rows before it is cut (never cut where it fits)
TALL_LABEL_ROWS_MAX = 6           # round 4 review 4: and more, up to this, while the column has room for them
TALL_TIGHT_LEADING = 0.88         # round 4 review 3: the entries' leading, last, where nothing else fits the column
SPAN_FALLBACK_AIR = 6             # round 4 review 3: a fallback span's least air to any other plate, px
CIRCA_MIN_PX = SPAN_MIN_PX        # round 4 review: the circa mark's floor, the tertiary type's (it fell to ~22 px)
TALL_YEAR_RATIO = 2.0             # round 4 review 6: a 9:16 year's cap at least this many times the labels' cap
                                  # while the column holds every label whole (the years fell to the labels' class)
TALL_YEAR_RATIO_MIN = 1.6         # and, only where the column cannot hold every label whole at that, this (logged)


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
    above, below = span_airs(label_px, gap_font)
    return max(above, span_least_above(inner_air)), below


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
    elapsed time never reads larger or heavier than the white events it sits between; SPAN_MIN_PX (30) at least."""
    return max(SPAN_MIN_PX, min(label_px - 4, round(label_px * TIMELINE_SPAN_RATIO)))


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


DRIFT_STEP_PX = 0.5    # round 4 review 7: the drift moves the ground's long edge in steps of this many px


def drifted(ground: Image.Image, t: float) -> Image.Image:
    """The timeline's ground at time `t`: a slow scale drift about its centre (BACKDROP_DRIFT over the clip)."""
    return _pushed_in(ground, 1.0 + BACKDROP_DRIFT * max(0.0, min(1.0, t / CLIP_SECONDS)))


class GroundDrift:
    """drifted() for one render, cached (round 4 review 7, 2026-11-03: the per-frame resize cost ~18 ms a frame):
    the scale moves the ground's long edge in DRIFT_STEP_PX steps (half a pixel on a ground blurred 34 px, so no
    step shows) and the last step's image is kept, so a 30 fps clip resizes about 30-60 times instead of 180; a
    flat ground (no photo) is not resized at all. Each call returns a copy the caller may draw on."""

    def __init__(self, ground: Image.Image) -> None:
        ex = ground.getextrema()
        ex = ex if isinstance(ex[0], tuple) else (ex,)
        self.ground, self.flat = ground, all(lo == hi for lo, hi in ex)
        self.q: int | None = None
        self.img = ground

    def step(self, t: float) -> int:
        scale = 1.0 + BACKDROP_DRIFT * max(0.0, min(1.0, t / CLIP_SECONDS))
        return round(max(self.ground.size) * (scale - 1.0) / 2 / DRIFT_STEP_PX)

    def __call__(self, t: float) -> Image.Image:
        q = 0 if self.flat else self.step(t)
        if q != self.q:
            self.q = q
            self.img = (_pushed_in(self.ground, 1.0 + 2 * q * DRIFT_STEP_PX / max(self.ground.size)) if q
                        else self.ground)
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
    from .typography import cover_crop
    im = cover_crop(Path(photo), (width, height)).convert("RGB").filter(ImageFilter.GaussianBlur(34))
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
    return plan_text(ground, plate.xy, plate.text, plate.font, fill, plate.anchor, track=plate.track)


def year_parts(year: int, approximate: bool) -> tuple[str, str]:
    """A timeline year in its two drawn parts (round 4, 2026-11-02): the circa mark, lowercase ("c.", CIRCA; set
    upper it read as an initial, "C. 700 BC") and the era in capitals ("700 BC", "AD 780", "1963"); the same
    words as year_text."""
    era = year_text(year, False).upper()
    return (CIRCA if approximate else ""), era


def circa_font(f_year, min_px: int | None = None) -> Any:
    """The circa mark's face beside an Anton year: Montserrat SemiBold at about a third of the year's size, never
    under CIRCA_MIN_PX (`min_px` for another frame scale). Round 4 review (2026-11-02): at 0.36 of a stepped-down
    year it fell to about 22 px on a 1080-wide frame, under the span label's floor, and "c. 700 BC" read as an
    exact date; the hedge is a fact, so it keeps the tertiary type's legibility floor whatever the year does."""
    floor = CIRCA_MIN_PX if min_px is None else min_px
    return get_font("Montserrat", max(floor, round(f_year.size * 0.36)), weight=600)


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


def render_timeline(events: List[Dict[str, Any]], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                    photo: Path | None = None) -> Optional[Path]:
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
    """The 9:16 timeline: two events get the large years and two-line labels; more get smaller years and one-line
    labels fitted to the column (each line paying for its own tracking, fit_tracked). No label is set under
    TALL_LABEL_MIN_PX: one that cannot fit one line there takes two, then more, as many as it needs whole at its
    largest size (at least TALL_LABEL_ROWS, at most TALL_LABEL_ROWS_MAX); only where the column cannot hold it
    whole is it cut, with an ellipsis (logged). Measured first: the first year's cap TALL_TOP_PAD under the graphics
    box's top and the last entry's label ending on its bottom (the column uses the box), and when the entries no
    longer fit between the bounds the column gives way in this order (round 4 review 6, 2026-11-03: the years are
    the hero): at each year size, the compressed gaps' honest lead, the spans to SPAN_MIN_PX, the spans'
    separation (span_clear_airs, down to the bare lean of span_airs, never under SPAN_MIN_SEPARATION) and the
    labels from 38 px (42 for two entries) to TALL_LABEL_MIN_PX (34); then the Anton years, a step at a time down
    to TALL_YEAR_RATIO x the labels' cap height; then the entries' leading (TALL_TIGHT_LEADING); then the years to
    TALL_YEAR_RATIO_MIN (logged) where that keeps every label whole; and last the label rows, one at a time down
    to two (a label cut), at the full ratio unless the least cuts fewer words (round 4 review 9, logged)."""
    n = int(CLIP_SECONDS * VIDEO_FPS)
    _top, bottom = get_text_bounds(width, height)
    x0 = inset_x0(width, height) + round(16 * width / VIDEO_WIDTH)   # the spine: its dots' left edge on the inset
    xt = x0 + round(56 * width / VIDEO_WIDTH)
    # round 4 review 6 (2026-11-03): the labels are drawn at xt + 4, so their measure starts there, and their ink
    # may overshoot the last advance by SPAN_OVERSHOOT (the labels ran 1-2 px past the graphics box)
    label_w = TEXT_RIGHT * width // VIDEO_WIDTH - (xt + 4) - SPAN_OVERSHOOT
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    many = len(events) > 2
    spans = [gap_text(events[k - 1], events[k]) for k in range(1, len(events))]
    scales = (1.0, 0.92, 0.84, 0.76, 0.68, 0.6)
    on_segment = any(spans)
    year_px0 = 104 if many else 180

    col_top = safe_zones.graphics_box(width, height)[1]   # round 4 review 2: the column's own top, the Reel box's
    fit_memo: dict[tuple[str, int, int, int], tuple[list[str], Any, bool]] = {}

    def measure(year_scale: float, scale: float, honest: bool = True, separate: bool = True,
                span_px: int | None = None, rows_max: int = TALL_LABEL_ROWS, leading: float = 1.0):
        f_year = get_font("Anton", int(year_px0 * year_scale))
        cap_y = cap_height(f_year)
        # round 4 review 3: the labels never step under TALL_LABEL_MIN_PX (a crowded column took them to 27 px
        # under 30 px spans); a label too long for one line there wraps to two (then, never cut, to rows_max)
        lab_px = max(TALL_LABEL_MIN_PX, int((38 if many else 42) * scale))
        entries = []
        texts = [drawable(e["label"].upper(), "Montserrat") for e in events]
        floor = max(TALL_LABEL_MIN_PX, round(lab_px * (0.78 if many else 0.8)))

        def fitted(text: str, px: int, rows: int, lo: int):
            key = (text, px, rows, lo)
            if key not in fit_memo:   # the labels' fits do not depend on the years: measured once per render
                fit_memo[key] = fit_tracked(probe, text, "Montserrat", px, label_w, rows, lo, 2,
                                            sentences=rows > 1, weight=800, quiet=True)
            return fit_memo[key]

        def wrapped(text: str, px: int, lo: int, first: int) -> tuple[list[str], Any, bool]:
            short = True
            for rows in range(first, rows_max + 1):
                lines, f_lab, short = fitted(text, px, rows, lo)
                if not short:
                    break
            return lines, f_lab, short
        if many:  # one size for every label: the smallest at which those that fit one line do; the rest take two
            fits = [fitted(tx, lab_px, 1, floor) for tx in texts]
            common = min([f[1].size for f in fits if not f[2]] or [floor])
        cut = False
        for e, text in zip(events, texts):
            if many:
                lines, f_lab, short = wrapped(text, common, common, 1)
            else:
                lines, f_lab, short = wrapped(text, lab_px, floor, 2)
            cut = cut or short
            f_lab = get_font("Montserrat", f_lab.size, weight=800)
            step = round(f_lab.size * 1.3 * leading)
            gap = round(f_lab.size * (1.25 if many else 1.6) * leading)   # round 4: 1.4 before, the year sat a line off its label
            e_top = -cap_y // 2              # offsets from the entry's dot: the year's top, the last row's ink bottom
            rows = [cap_y // 2 + gap + j * step for j in range(len(lines))]
            e_bottom = max(probe.textbbox((0, y), ln, font=f_lab, anchor="ls")[3] for y, ln in zip(rows, lines))
            entries.append({"approx": bool(e.get("approximate")), "year_n": int(e["year"]), "lines": lines,
                            "font": f_lab, "rows": rows, "top": e_top, "bottom": e_bottom,
                            "inner": entry_inner_air(cap_y, f_lab, gap, step, len(lines)),
                            # review 7: a one-line label's line gap is its nominal leading's, also under the
                            # tight leading (a span kept 1.5 x the tightened 15 px under a one-line label whose
                            # line gap reads as 20); a wrapped label's is the gap its lines have
                            "line": (step if len(lines) > 1 else max(step, round(f_lab.size * 1.3)))
                                    - cap_height(f_lab)})
        y_first = col_top + TALL_TOP_PAD - entries[0]["top"]
        y_last = bottom - entries[-1]["bottom"]                    # lifted so its last row ends on the bound
        # The elapsed time on its own segment, always (round 4, 2026-11-02: the round-2 fallback on the later
        # year's baseline read "C. AD 780  ABOUT 1,500 YEARS" as that event's duration): its band under the
        # labels' size with SPAN_AIR of air on each side (span_band).
        # Round 4 review 2: the band's air above is at least SPAN_SEPARATION x the entries' own inner air
        # (span_clear_airs), so the span never reads as the earlier label's next line.
        lab_min = min(en["font"].size for en in entries)
        f_gap = get_font("Montserrat", min(span_px or span_font_px(lab_min), lab_min - SPAN_UNDER_LABEL), weight=600)
        clear = span_air_rule(lab_min, f_gap, span_basis(entries), separate)
        band = cap_height(f_gap) + sum(clear) if on_segment else 0
        # review 7: each span's band from the label above it (span_basis_at), not the column's loosest
        bands_k = [cap_height(f_gap) + sum(span_air_rule(lab_min, f_gap, span_basis_at(entries, k + 1), separate))
                   for k in range(len(entries) - 1)]
        # round 4 review 6: a span's ink under its baseline (the comma of "2,956") is paid in its band, not taken
        # from its air above
        needs = [entries[k]["bottom"] - entries[k + 1]["top"]
                 + (bands_k[k] + span_descent(spans[k], f_gap) if band and spans[k] else 28)
                 for k in range(len(entries) - 1)]
        spacing = timeline_positions(events, y_first, y_last, needs)
        steps = [b - a for a, b in zip(spacing.positions, spacing.positions[1:])]
        ok = (spacing.honest or not honest) and all(st >= nd - 1 for st, nd in zip(steps, needs))
        return ok, (f_year, cap_y, entries, y_first, y_last, f_gap, needs, band, spacing, clear, separate, cut)

    # Round 4 review 6 (operator, 2026-11-03: the years are the hero of a timeline): the years hold their size
    # longest. At each year size, from the largest down, every other valve is tried first: the compressed gaps'
    # honest lead, the spans to SPAN_MIN_PX and their separation down to the bare lean (never under
    # SPAN_MIN_SEPARATION), the labels down to TALL_LABEL_MIN_PX. The years step down only to TALL_YEAR_RATIO x the
    # labels' cap height while every label is whole (its rows, then the entries' tight leading); only a column that
    # cannot hold its labels whole even so sets the years under that, and last cuts a label.
    stages = ([(True, True, None), (False, True, None), (False, True, SPAN_MIN_PX), (False, False, None),
               (False, False, SPAN_MIN_PX)] if on_segment else [(True, True, None), (False, True, None)])
    label_scales = list(dict.fromkeys(max(TALL_LABEL_MIN_PX, int((38 if many else 42) * sc)) for sc in scales))
    scale_of = {px: next(sc for sc in scales if max(TALL_LABEL_MIN_PX, int((38 if many else 42) * sc)) == px)
                for px in label_scales}

    def year_floor_ok(ys: float, lab_px: int, ratio: float) -> bool:
        return (cap_height(get_font("Anton", int(year_px0 * ys)))
                >= ratio * cap_height(get_font("Montserrat", lab_px, weight=800)))

    def year_steps(ratio: float) -> list[float]:
        # TALL_YEAR_STEPS down to `ratio` x the smallest labels' cap, then that floor itself (between two steps)
        cap_l = cap_height(get_font("Montserrat", label_scales[-1], weight=800))
        px_floor = next((px for px in range(int(year_px0 * TALL_YEAR_STEPS[0]), 1, -1)
                         if cap_height(get_font("Anton", px - 1)) < ratio * cap_l), 2)
        return sorted({*(s_ for s_ in TALL_YEAR_STEPS if int(year_px0 * s_) >= px_floor), px_floor / year_px0},
                      reverse=True)

    def choose(rows_max: int, leading: float, ratio: float):
        for ys in year_steps(ratio):
            for lab_px in label_scales:
                if not year_floor_ok(ys, lab_px, ratio):
                    continue
                for honest, separate, span_px in stages:
                    ok, m = measure(ys, scale_of[lab_px], honest, separate, span_px, rows_max, leading)
                    if ok:
                        return True, (ys, scale_of[lab_px], m)
        return False, None

    def labels_px(choice) -> int:
        return min(en["font"].size for en in choice[2][2])
    chosen = None
    # Round 4 review 4 (2026-11-02): a label grows rows while the column has room (a 108-character pair was cut at
    # three rows with ~120 px of the column spare); the ladder starts at the rows the longest label needs whole at
    # its largest size, and steps down one row at a time to two.
    px_most = 38 if many else 42                     # measure()'s label size at scale 1: the most rows a label needs
    most = max(TALL_LABEL_ROWS, *(next((r for r in range(1, TALL_LABEL_ROWS_MAX + 1)
                                        if not fit_tracked(probe, tx, "Montserrat", px_most, label_w, r, px_most, 2,
                                                           sentences=r > 1, weight=800, quiet=True)[2]),
                                       TALL_LABEL_ROWS_MAX)
                                  for tx in (drawable(e["label"].upper(), "Montserrat") for e in events)))
    ladder = list(range(most, 1, -1))
    tight = TALL_TIGHT_LEADING
    full, least = TALL_YEAR_RATIO, TALL_YEAR_RATIO_MIN
    # Round 4 review 7 (2026-11-03): as the docstring says, where a cut cannot be avoided the years drop to
    # TALL_YEAR_RATIO_MIN before any label loses a row (the rows were reduced at the full ratio first, so more
    # labels were cut while the years kept a size the column could give up): fewer rows only at the least ratio,
    # each trying its larger years first (choose), so no row is given up while smaller years would keep it.
    # Round 4 review 9 (2026-11-03): the years give way only for what it buys. xv1, xv18 and xv21 set the years
    # under the full ratio "to keep 4 entries whole" and then cut a label all the same (the rows went at the least
    # ratio). Now: whole labels at the full ratio, then whole at the least; where no layout keeps every label whole,
    # the most rows at the full ratio, and the least ratio only where it cuts fewer words than that (logged so).
    def dropped(choice) -> int:
        words = sum(len(tx.split()) for tx in (drawable(e["label"].upper(), "Montserrat") for e in events))
        return words - sum(len(" ".join(en["lines"]).replace("…", "").split()) for en in choice[2][2])

    def first_fit(tries_: list[tuple[int, float, float]], whole: bool):
        for rows_max, leading, ratio in tries_:
            ok, choice = choose(rows_max, leading, ratio)
            if ok and not (whole and choice[2][11]):
                return choice
        return None

    chosen = first_fit([(most, 1.0, full), (most, tight, full), (most, 1.0, least), (most, tight, least)], True)
    if chosen is not None:
        if not year_floor_ok(chosen[0], labels_px(chosen), full):
            log.warning("Timeline: the years set under %.1f x the labels' cap height to keep %d entries whole.",
                        full, len(events))
    else:
        at_full = first_fit([(r, ld, full) for r in ladder for ld in (1.0, tight)], False)
        at_least = first_fit([(r, ld, least) for r in ladder for ld in (1.0, tight)], False)
        chosen = at_full
        if at_least is not None and (at_full is None or dropped(at_least) < dropped(at_full)):
            chosen = at_least
            if not year_floor_ok(chosen[0], labels_px(chosen), full):
                log.warning("Timeline: the years set under %.1f x the labels' cap height to cut fewer words (%d, "
                            "not %s); no layout keeps every label whole.", full, dropped(at_least),
                            dropped(at_full) if at_full is not None else "no layout")
    if chosen is None:
        log.warning("Timeline: %d entries do not fit the column even at the label floor; set at the tightest.",
                    len(events))
        chosen = (TALL_YEAR_STEPS[-1], scales[-1], measure(TALL_YEAR_STEPS[-1], scales[-1], False, False, SPAN_MIN_PX,
                                                           2, TALL_TIGHT_LEADING)[1])
    if on_segment and not chosen[2][10]:
        log.warning("Timeline spans set without their full separation to keep %d labels at %d px.",
                    len(events), labels_px(chosen))
    if chosen[2][11]:
        log.warning("Timeline: a label cut to fit its rows at %d px.", labels_px(chosen))
    year_scale, scale, m = chosen
    f_year, cap_y, entries, y_first, _y_last, f_gap, _needs, _band, spacing, _clear, separate, _cut = m
    if scale < 1.0 or year_scale < 1.0:
        log.info("Timeline years at %d%%, labels at %d%% to keep %d entries above the caption band.",
                 round(year_scale * 100), round(scale * 100), len(events))
    ground = timeline_backdrop(photo, width, height)
    plates: list[tuple[str, Any]] = []
    twins = []
    for k, en in enumerate(entries):
        y = spacing.positions[k]
        en["y"] = y
        en["year_plates"] = _year_plates(ground, xt, y + cap_y // 2, en["year_n"], en["approx"], f_year, GOLD)
        en["label_plates"] = [plan_text(ground, (xt + 4, y + r), ln, en["font"], WHITE, "ls", track=2)
                              for r, ln in zip(en["rows"], en["lines"])]
        en["dim_year"] = [_twin(ground, pl, brand.STONE) for pl in en["year_plates"]]
        en["dim_labels"] = [_twin(ground, pl, brand.STONE_LT) for pl in en["label_plates"]]
        plates += [(f"year{k}" if j == len(en["year_plates"]) - 1 else f"year{k}_circa", pl)
                   for j, pl in enumerate(en["year_plates"])] + [(f"label{k}", p) for p in en["label_plates"]]
        twins += [(f"dim{k}", pl) for pl in en["dim_year"]] + [(f"dim{k}_label", p) for p in en["dim_labels"]]
    # The time since the previous date: centred in the band between the two entries, on the segment it measures,
    # beside the spine at its break mark, "LATER" kept (round 4: the whole-timeline "LATER" drop is gone; a span
    # too wide for the column alone loses it).
    gaps = []
    col_right = TEXT_RIGHT * width // VIDEO_WIDTH
    bands = {k: (entries[k - 1]["y"] + entries[k - 1]["bottom"], entries[k]["y"] + entries[k]["top"])
             for k in range(1, len(entries))}


    lab_min = min(en["font"].size for en in entries)

    def gap_xy(k: int, font, descent: float = 0.0) -> tuple[float, float]:
        # round 4 review: the span leans toward the later entry (span_airs); round 4 review 4: of any slack the
        # time scale leaves in the band, a third goes under it and two thirds above (span_slack_below), so it
        # stays the later date's lead-in without crowding that year (`descent`: the span's ink under its
        # baseline, a comma's tail, counted in its air below)
        a, b = bands[k]
        above, below = span_air_rule(lab_min, font, span_basis_at(entries, k), separate)
        slack = max(0.0, (b - a) - (cap_height(font) + above + below + descent))
        return xt, b - below - descent - span_slack_below(slack)

    def fits(k: int, text: str, font) -> bool:
        a, b = bands[k]
        need = (cap_height(font) + sum(span_air_rule(lab_min, font, span_basis_at(entries, k), separate))
                + span_descent(text, font))
        return b - a >= need - 1 and xt + tracked_width(text, font, 2) <= col_right - SPAN_OVERSHOOT
    for k, text in [(k, t) for k, t in enumerate(spans, start=1) if t]:
        # operator (round 4 review 2): never dropped; the span shrinks to SPAN_MIN_PX to fit its band, "LATER"
        # kept while any size does, and where none does it is set at SPAN_MIN_PX in the clear band between the
        # two entries (span_fallback_y), or the nearest place on its segment clear of every plate (free_span_xy)
        placed = span_fit(text, f_gap.size, lambda tx, px, k=k: fits(k, tx, get_font("Montserrat", px, weight=600)))
        if placed:
            text, font = placed[0], get_font("Montserrat", placed[1], weight=600)
            xy = gap_xy(k, font, span_descent(text, font))
        else:
            # round 4 review 3: centred in the clear band between the two entries (span_fallback_y), and where
            # that band is too short even at SPAN_MIN_PX, the nearest place on its segment clear of every plate
            # (free_span_xy), so the span is never dropped and never set on a label or a year
            font = get_font("Montserrat", SPAN_MIN_PX, weight=600)
            if xt + tracked_width(text, font, 2) > col_right:
                text = text.removesuffix(" LATER")
            a, b = bands[k]
            inner = span_basis_at(entries, k)
            xy = (xt, span_fallback_y(a, b, cap_height(font), inner))
            trial = plan_text(ground, xy, text, font, brand.STONE_LT, "ls", track=2)
            lo, hi = entries[k - 1]["y"], entries[k]["y"] + cap_y / 2
            ys = sorted(range(math.floor(lo), math.ceil(hi) + 1, 2), key=lambda y: abs(y - xy[1]))
            # review 5: under a label it keeps SPAN_MIN_SEPARATION x the entries' inner air, never less
            under = max(0, span_least_above(inner) - SPAN_FALLBACK_AIR)
            obstacles = [(b[0], b[1], b[2], b[3] + (under if nm.startswith("label") else 0))
                         for nm, b in ((nm, pl.box) for nm, pl in plates)]
            spot = free_span_xy(trial.box, xy, [xy[1]] + ys, [xt], obstacles, col_right,
                                SPAN_FALLBACK_AIR, [pl.box for nm, pl in plates if nm.startswith("year")])
            if spot is None:
                log.error("Timeline span %r: no clear place on its segment; set in its band.", text)
            xy = spot or xy
            log.warning("Timeline span %r set at %d px without its full air: its band is %d px.", text,
                        SPAN_MIN_PX, b - a)
        plate = plan_text(ground, xy, text, font, brand.STONE_LT, "ls", track=2)
        gaps.append((k, plate))
        plates.append((f"gap{k}", plate))
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
WIDE_SPAN_PX = (32, SPAN_MIN_PX)   # design px: a 16:9 span's size (round 4: 26 before) and, review 5, its floor
WIDE_FLAG_MIN = 40.0    # round 4 review 5: design px, the shortest flag from the spine to a block (74 at rest)
WIDE_FLAG_STEP = 36.0   # round 4 review 6: and the steps it lengthens by where a span needs the air by the spine
# (above, below) added to the 74 px flags; round 4 review 7: in half steps too, the least added first, so a span
# that needs the air on both sides of the spine gets it on both (a stacked "LATER" under the spine needs the
# lower years lower, a span over it the upper labels higher) where title-safe has the room
WIDE_FLAG_TRIES = tuple(sorted(((a * WIDE_FLAG_STEP / 2, b * WIDE_FLAG_STEP / 2) for a in range(5) for b in range(5)),
                               key=lambda ab: (ab[0] + ab[1], abs(ab[0] - ab[1]))))
WIDE_LABEL_MAX = 1000.0  # round 4 review 6: design px, the widest measure a 16:9 label takes to stay whole
WIDE_LABEL_STEP = 40.0  # and the steps it widens by from the layout's common measure
WIDE_LONE_SHARE = 0.6   # round 4 review 6: a label's lone first word narrower than this share of its widest line
WIDE_SPAN_LEVEL_NEAR = 2 * WIDE_SPAN_CLEAR  # round 4 review 7: design px, a label line this near a span never
                                            # shares its baseline (nz27's "ABOUT 7,500 YEARS" 40 px after a line)
WIDE_SPAN_REACH = 170   # and, the last resort only (logged), the furthest a span's nearest ink sits from the spine
WIDE_SPAN_ROW_NEAR = 2 * WIDE_SPAN_LEVEL_NEAR  # round 4 review 9 (2026-11-03): design px, and there a label line
                                               # level with it keeps this far (xv15's "ABOUT 2,500 YEARS LATER" sat
                                               # 100 px from the lines either side and read as part of their row)
WIDE_SPAN_LEAN = 18     # round 4 review 7: design px a span's centre sits past its segment's middle, toward the later
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


def wide_span_lines(text: str, stacked: bool | int) -> list[str]:
    """A 16:9 span's lines: one, or (`stacked`) broken before its last word so "LATER" is kept, never dropped.
    Round 4 review 7: `stacked` 3 also breaks before "YEARS" ("ABOUT 5,500 / YEARS / LATER", the narrowest, for a
    segment a block overhangs), where the span has the words for it (else as stacked)."""
    words = text.split()
    if stacked == 3 and len(words) >= 4:
        return [" ".join(words[:-2]), words[-2], words[-1]]
    head, _sep, tail = text.rpartition(" ")
    return [head, tail] if stacked and head else [text]


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
    year_w = [year_width(int(e["year"]), bool(e.get("approximate")), f_year) / g.k for e in events]
    raw_gaps = [gap_text(events[k - 1], events[k]) for k in range(1, len(events))]
    # Round 4 review (2026-11-02): the same words as 9:16, "LATER" kept: a span too wide for its gap on one line
    # breaks before "LATER" (wide_span_lines) instead of dropping it ("ABOUT 1,500 YEARS" here, "... LATER" there).
    gap_texts = raw_gaps
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
            got = fit_tracked(probe, text, "Montserrat", g.f(38), m, 3, g.f(34), track, weight=800,
                              quiet=True)   # round 4: 34 / 32 before
            for rows in range(4, rows_max + 1):
                if not got[2]:
                    break
                got = fit_tracked(probe, text, "Montserrat", g.f(34), m, rows, g.f(34), track,
                                  weight=800, quiet=True)
            if not got[2] and lone_first(got[0], got[1]):
                # review 6: a lone first word at 38 px ("FIRST / CHAMBERS / CUT") gives way to the floor size
                # where that breaks it better ("FIRST CHAMBERS CUT"); review 7: then a balanced break of the same
                # rows, then one more row, that leaves it as wide as the lines under it
                alts = []
                if got[1].size > g.f(34):
                    alts.append(fit_tracked(probe, text, "Montserrat", g.f(34), m, len(got[0]), g.f(34), track,
                                            weight=800, quiet=True))
                for f_try in dict.fromkeys((got[1], floor_font)):
                    for rows in range(len(got[0]), rows_max + 1):
                        lines = tracked_wrap(text, f_try, m, track, lines=rows, lone_share=WIDE_LONE_SHARE)
                        if lines:
                            alts.append((lines, f_try, False))
                got = next((alt for alt in alts if not alt[2] and not lone_first(alt[0], alt[1])), got)
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
        if len(lines) < 2 or len(lines[0].split()) != 1:
            return False
        if len(lines) >= 3 and all(len(ln.split()) == 1 for ln in lines):
            return True
        # review 9: measured on the ink, as the frame shows it ("GERMAN" 168 px over "EXCAVATIONS" 282, 0.596,
        # passed on the advances)
        def ink_w(ln: str) -> float:
            bb = ink_box((0.0, 0.0), ln, font, "ls", track)
            return 0.0 if bb is None else float(bb[2] - bb[0])
        return ink_w(lines[0]) < WIDE_LONE_SHARE * max(ink_w(ln) for ln in lines[1:])

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
                      rows_cap: int | None = None) -> list[dict[str, Any]]:
        """Each label's block at `label_max`; with `widen`, a label cut or left with a lone first word there takes
        the narrowest wider measure (WIDE_LABEL_STEP steps to WIDE_LABEL_MAX) at which it is neither (round 4
        review 6, 2026-11-03: one common measure cut "REDISCOVER…" where the frame had room for it whole).
        Round 4 review 7: a label no measure holds whole keeps the cut with the most words of all those tried, and
        with `rows_cap` a label on more rows than that widens too, to the first measure that sets it on as few (a
        deep block takes the spine's air on its side: fz26 cut "ABANDONED…" rather than widen to three rows).
        Review 8: measured once per setting (each span wording asked again for the same blocks); never mutated."""
        key = (label_max, widen, rows_max, rows_cap)
        if key in layout_memo:
            return layout_memo[key]
        blocks = []
        for k, text in enumerate(texts):
            lines, f_lab, short = fit_block(text, label_max, rows_max)
            deep = rows_cap is not None and len(lines) > rows_cap
            if widen and (short or lone_first(lines, f_lab) or deep):
                m = label_max
                while m < WIDE_LABEL_MAX:
                    m = min(WIDE_LABEL_MAX, m + WIDE_LABEL_STEP)
                    got = fit_block(text, m, rows_max)
                    if (not got[2] and not lone_first(got[0], got[1])
                            and (rows_cap is None or len(got[0]) <= rows_cap)):
                        lines, f_lab, short = got
                        break
                    if short and (not got[2] or kept_words(got[0]) > kept_words(lines)):
                        lines, f_lab, short = got        # whole at least (its first word alone), or more of it
            f_lab = get_font("Montserrat", f_lab.size, weight=800)
            blocks.append({"lines": lines, "font": f_lab, "w": ink_reach(k, lines, f_lab), "short": short,
                           "lone": lone_first(lines, f_lab),
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
                  mode: str = "segment"):
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
        for air, stackings in (("full", (False, True)), ("across", (False, True)), ("least", (False, True)),
                               ("across", (3,)), ("least", (3,))):
            full = air == "full"
            for fi, font in enumerate(span_fonts):
                cap_g, step_g = cap_(font) / g.k, round(font.size * 1.2) / g.k
                for stacked in stackings:
                    lines = wide_span_lines(text, stacked)
                    if stacked == 3 and len(lines) < 3:
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
                    for off in offs:
                        for dx in shifts:
                            cx = round(X(mid + dx))
                            for above in (prefer, not prefer):
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
                                    cost = (not full) + (air == "least") + (fi > 0) + 2 * (len(lines) > 2)
                                    return fi, lines, cx, ys, boxes, cost
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
            spot = span_spot(k, gap_texts[k - 1], xs, spine, obstacles, years, label_lines, earlier[k], "off")
            if spot is not None:
                out[k] = spot
                obstacles = obstacles + [(_union(spot[4]), S(clear), S(clear))]
        return out

    def spans_placed(xs: list[float], spine: float, geo_: list[dict[str, Any]], blocks: list[dict[str, Any]]):
        """Every span's spot (span_spot) in order, each clear of the ones before it: ({k: spot}, [k unplaced])."""
        obstacles, years, label_lines, earlier = span_context(geo_, blocks)
        spots, missing = {}, []
        for k, text in enumerate(gap_texts, start=1):
            if not text:
                continue
            spot = span_spot(k, text, xs, spine, obstacles, years, label_lines, earlier[k])
            if spot is None:
                missing.append(k)
                continue
            spots[k] = spot
            obstacles = obstacles + [(_union(spot[4]), S(clear), S(clear))]
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
        for widen, rows_max, rows_cap in ((False, 4, None), (False, 5, None), (False, 6, None), (True, 4, None),
                                          (True, 5, None), (True, 6, None), (True, 6, 3), (True, 6, 2)):
            blocks = layout_blocks(label_max, widen, rows_max, rows_cap)
            sig = [(b["lines"], b["font"].size) for b in blocks]
            if sig in seen:
                continue
            seen.append(sig)
            yield blocks
            if len(seen) == 1 and not any(b["short"] or b["lone"] for b in blocks):
                return
    # each span's settings' widths as gap steps (its room is the step less WIDE_SPAN_OFF each side), narrowest first
    span_steps = [sorted({max(tracked_width(ln, f_, S(1)) for ln in wide_span_lines(t, st_)) / g.k
                          + 2 * WIDE_SPAN_OFF + 2 for f_ in span_fonts for st_ in (False, True, 3)}) if t else []
                  for t in gap_texts]
    best = None                                    # (score, layout..., the order it was found in)
    pending: list[tuple[tuple[Any, ...], int, tuple[Any, ...]]] = []
    seq = 0
    perfect = (False, 0, 0, 0, 0, 0, 0, 0)
    right = float(WIDE_TITLE_SAFE[2])              # design px: a block's ink edge (review 9: ink_reach, not advance)
    # review 6: the gaps' least lengths sized for one-line spans, then stacked ones, then stacked at the floor size
    for wording, fi_w in (("LINE", 0), ("STACKED", 0), ("STACKED", len(span_fonts) - 1)):
        gap_w = [max(tracked_width(ln, span_fonts[fi_w], S(1)) for ln in wide_span_lines(t, wording == "STACKED"))
                 / g.k if t else 0.0 for t in gap_texts]
        for label_max in (560, 500, 460, 420, 380, 340, 300, 270):
            for blocks in blocks_variants(label_max):
                # review 8 (2026-11-03): the labels' own costs bound every score these blocks can reach (inside,
                # apart, every span by the spine at no cost); where that bound cannot beat the best, no spacing,
                # flag or span is tried (the search took 2.9 x HEAD's time)
                typo0 = (sum(b["one_word"] for b in blocks), sum(b["short"] for b in blocks),
                         sum(b["dropped"] for b in blocks), sum(b["lone"] for b in blocks))
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
                        typo = (sum(b["one_word"] for b in blocks), sum(b["short"] for b in blocks),
                                sum(b["dropped"] for b in blocks), sum(b["lone"] for b in blocks))
                        cost = sum(sp[5] for sp in spots.values())
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
                        wanting = missing + [k for k, sp in spots.items() if sp[5]]
                        if fewest is None or (len(missing), len(wanting)) < fewest[0]:
                            fewest = ((len(missing), len(wanting)), wanting)
                        if not wanting:
                            break
                    if fewest is None or not fewest[1] or not apart:
                        break
                    for k in fewest[1]:           # review 7: from the step it has, which may exceed its need,
                        step_k = xs[k] - xs[k - 1]    # to the next width at which one of its settings fits
                        wider = [w_ for w_ in span_steps[k - 1] if w_ > step_k + 1]
                        base[k - 1] = max(base[k - 1], min(wider) if wider else step_k + 40.0)
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
        score = (outside, len(missing) - len(off) + not_apart, *typo, len(off), cost)
        if best is None or (score, seq_) < (best[0], best[-1]):
            best = (score, blocks, spacing, spine_y, flag_y, geo_, spots, missing, off, seq_)
    assert best is not None
    (_score, blocks, spacing, spine_y, flag_y, geo_, spots, missing, off, _seq), xs = best, best[2].positions
    if _score[0]:
        log.error("Timeline (16:9): no layout holds the entries inside title-safe; set at the closest.")
    # review 8: each last resort names its labels, so a check can hold every other label to the rule
    def named(flag: str) -> str:
        return " | ".join(texts[k] for k, b in enumerate(blocks) if b[flag])
    if _score[3]:
        log.warning("Timeline (16:9): %d label(s) cut: no measure holds them whole: %s.", _score[3], named("short"))
    if _score[2]:
        log.warning("Timeline (16:9): %d label(s) cut to a single word: no layout keeps two: %s.", _score[2],
                    named("one_word"))
    if _score[5]:
        log.warning("Timeline (16:9): %d label(s) start with a lone word: no layout breaks them better: %s.",
                    _score[5], named("lone"))
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
            fi, lines = len(span_fonts) - 1, wide_span_lines(text, True)
            cap_g, step_g = cap_(span_fonts[fi]) / g.k, round(span_fonts[fi].size * 1.2) / g.k
            above = k % 2 == 0
            first = spine_y - WIDE_SPAN_OFF - step_g * (len(lines) - 1) if above else spine_y + WIDE_SPAN_OFF + cap_g
            spot = (fi, lines, round(X((xs[k - 1] + xs[k]) / 2)),
                    [round(Y(first + step_g * j)) for j in range(len(lines))], [], 9)
        fi, lines, cx, ys = spot[:4]
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
def pick_reference(feet: float) -> Optional[Tuple[str, float, str]]:
    """The reference whose size is closest (by ratio) to `feet`, if within 1/3x-3x."""
    ref = selected_reference({"value": feet, "unit": "ft"})
    return (ref["label"], ref["feet"], ref["source_url"]) if ref else None


def render_scale(fact: Dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
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
    the uncovered edge takes `fill` (the plain ground)."""
    if abs(scale - 1.0) <= 0.0005:
        return im
    w, h = im.size
    big = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BICUBIC)
    x0, y0 = round(centre[0] * scale - centre[0]), round(centre[1] * scale - centre[1])
    if scale > 1.0:
        return big.crop((x0, y0, x0 + w, y0 + h))
    out = Image.new(im.mode, (w, h), fill if fill is not None else im.getpixel((0, 0)))
    out.paste(big, (-x0, -y0))
    return out


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
            # the data face at the ruler's size, two lines at most, inside the box or not at all
            note = reference_note(ref_label)
            if note:
                f_rn = get_font(brand.DATA_FONT, SCALE_RULER_PX - 4, weight=600)
                nl, _f, short = fit_text(probe, note, brand.DATA_FONT, f_rn.size, int(box_x1 - ref_note.box[0] - 4), 2,
                                         f_rn.size, sentences=False, weight=600, quiet=True)
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
        d = ImageDraw.Draw(im, "RGBA")
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
    fits = [fit_text(probe, text, "Montserrat", g.f(36), int(col), 2, g.f(32), sentences=False,
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
        d = ImageDraw.Draw(im, "RGBA")
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


def _person(d: ImageDraw.ImageDraw, cx: int, base_y: float, height: float, colour, upward: bool = True) -> None:
    """A simple standing-figure silhouette `height` px tall (head, torso, arms, legs)."""
    if height < 4:
        return
    sgn = -1 if upward else 1
    y = lambda frac: base_y + sgn * height * frac  # noqa: E731  (0 = feet, 1 = top of head)
    head_r = height * 0.065
    fill = (*colour, 255)
    d.ellipse([cx - head_r, y(0.935) - head_r, cx + head_r, y(0.935) + head_r], fill=fill)
    w = height * 0.11
    d.rounded_rectangle([cx - w, min(y(0.86), y(0.47)), cx + w, max(y(0.86), y(0.47))], radius=max(2, int(w / 2)), fill=fill)
    for side in (-1, 1):  # arms and legs
        d.line([(cx + side * w * 0.9, y(0.84)), (cx + side * w * 1.6, y(0.50))], fill=fill, width=max(2, int(w * 0.55)))
        d.line([(cx + side * w * 0.5, y(0.48)), (cx + side * w * 0.8, y(0.0))], fill=fill, width=max(2, int(w * 0.75)))


def _backdrop(photo: Optional[Path], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Image.Image:
    """A dark, heavily blurred version of the topic's photo (or the plain ground) behind text graphics."""
    if photo and Path(photo).exists():
        from .typography import cover_crop
        im = cover_crop(Path(photo), (width, height)).convert("RGB").filter(ImageFilter.GaussianBlur(28))
        dark = Image.new("RGB", (width, height), SEA)
        return Image.blend(im, dark, 0.72).convert("RGB")
    return Image.new("RGB", (width, height), SEA)


# ------------------------------------------------------------- number cards
def _pushed_in(bg: Image.Image, scale: float) -> Image.Image:
    """The backdrop scaled by `scale` about its centre and cropped back to size (the push-in)."""
    if scale <= 1.0:
        return bg.copy()
    w, h = bg.size
    big = bg.resize((int(w * scale) + 1, int(h * scale) + 1), Image.Resampling.BILINEAR)
    x0, y0 = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((x0, y0, x0 + w, y0 + h))


def _split_number(value: str) -> Tuple[str, Optional[float], str]:
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


def figure_parts(value: str) -> Dict[str, str]:
    """A number card's value in its drawn parts: what comes before the figure ("ABOUT", "EST."), the figure
    ("280", "25+", "94–104"), its unit ("FT", "MILLION YEARS") and a figure in brackets ("85 M"), which the card
    sets as its second, data-face line. A value with no figure is all `unit` (drawn as it is)."""
    import re
    m = re.match(r"^\s*(?P<pre>[^\d(]*?)\s*(?P<num>\d[\d,.]*(?:\s*[–-]\s*\d[\d,.]*)?\+?)\s*(?P<unit>[^()]*?)\s*"
                 r"(?:\((?P<sec>[^)]*)\))?\s*$", value or "")
    if not m:
        return {"pre": "", "num": "", "unit": (value or "").strip(), "sec": ""}
    return {k: " ".join((m.group(k) or "").split()) for k in ("pre", "num", "unit", "sec")}


def _cells(d: ImageDraw.ImageDraw, text: str, font) -> List[Tuple[str, float]]:
    """Each character of a figure with its advance: digits on one fixed cell (the widest digit), so a count-up
    rolls in place without the unit beside it moving."""
    cell = max(d.textlength(c, font=font) for c in "0123456789")
    return [(c, cell if c.isdigit() else d.textlength(c, font=font)) for c in text]


def _stat_layout(d: ImageDraw.ImageDraw, card: Dict[str, Any], num_px: int, width: int, scale: float = 1.0,
                 label_min: int | None = None, num_min: int = 120) -> Dict[str, Any]:
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
    rows: List[Dict[str, Any]] = []
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


def render_stat_cards(cards: List[Dict[str, Any]], out: Path, photo: Optional[Path] = None, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                      narration: str = "") -> Optional[Path]:
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
    from .typography import cover_crop
    n = int(CLIP_SECONDS * VIDEO_FPS)
    top, bottom = get_text_bounds(width, height)
    x_text = inset_x0(width, height)                     # the shared inset edge (was M, 84)
    text_w = TEXT_RIGHT - x_text - 4
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    # Measured first: both cards between the bounds (the lower one ending on the graphics box's bottom at most),
    # the type stepped down together when they do not fit.
    for scale in (1.0, 0.9, 0.8, 0.7, 0.6):
        num_px = round((380 if len(cards) == 1 else 230) * scale)
        plans = [_stat_layout(probe, c, num_px, text_w, scale) for c in cards]
        gap = 90
        total = sum(p["height"] for p in plans) + gap * (len(plans) - 1)
        if total <= bottom - top:
            break
    if len(plans) > 1:   # one figure size for both (round 3): a smaller second figure implied a ranking
        size = min(p["figure_px"] for p in plans)
        plans = [_stat_layout(probe, c, size, text_w, scale, num_min=size) for c in cards]
        total = sum(p["height"] for p in plans) + gap * (len(plans) - 1)
    y = max(top, (top + bottom) // 2 - total // 2)
    for p in plans:
        p["y0"] = y - p["top"]
        p["x0"] = x_text
        y += p["height"] + gap
    if photo and Path(photo).exists():
        bg = cover_crop(Path(photo), (width, height)).convert("RGB")
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
    from .typography import cover_crop
    im = cover_crop(Path(photo), (width, height)).convert("RGB")
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
    plates: List[Tuple[str, Any]] = []
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
    last: List[Image.Image] = []

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
                            d = ImageDraw.Draw(im, "RGBA")
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
                        for j, (dx0, dy0, dx1, dy1) in enumerate(r["dashes"]):
                            shift = spread * (sum(1 for _c, cx in r["chars"] if cx < dx0) - 0.5)
                            base = yy
                            d.rectangle([ox + (dx0 - ox) * sc + shift, base + (dy0 - (yy - dy)) * sc,
                                         ox + (dx1 - ox) * sc + shift, base + (dy1 - (yy - dy)) * sc], fill=fill)
                    else:
                        if r["kind"] == "kicker":
                            x0, y0, x1, y1 = r.get("rule", r.get("bar", (p["x0"], yy, p["x0"] + 44, yy + 3)))
                            fill = _lerp(r["fill"], r["dim_fill"], dim)
                            ImageDraw.Draw(im, "RGBA").rectangle([x0, y0 + dy, x1, y1 + dy], fill=(*fill, int(255 * a)))
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


def _render_stat_cards_landscape(cards: List[Dict[str, Any]], out: Path, photo: Optional[Path] = None, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                      narration: str = "") -> Optional[Path]:
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
        plans = [_stat_layout(probe, c, num_px, col_w - 8, scale * k,
                              label_min=round(40 * k), num_min=round(num_px * 0.62)) for c in cards]
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
    """Design coordinates (the 1080x1920 frame, text box 65-888 x 330-1058) on this frame's text box."""

    def __init__(self, width: int, height: int):
        self.bx0, self.by0, self.bx1, self.by1 = get_text_box(width, height)
        self.k = min((self.bx1 - self.bx0) / 823, (self.by1 - self.by0) / 728)

    def x(self, v: float) -> float:
        return self.bx0 + (v - 65) * self.k

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
        return 65 + (px - self.bx0) / self.k


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


def _dash_runs(pts: list[tuple[float, float]], dash: float, gap: float, phase: float, upto: float):
    """Dash segments along a polyline (phase moves them forward), drawn only for its first `upto` px."""
    segs = []
    lens = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = min(sum(lens), upto)
    period = dash + gap
    s = (phase % period) - period
    while s < total:
        a, b = max(0.0, s), min(total, s + dash)
        if b > a:
            segs.append(_sub_path(pts, lens, a, b))
        s += period
    return segs


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


def _chevron(pen: _Pen, tip: tuple[float, float], direction: tuple[float, float], size: float, fill) -> None:
    """A filled arrowhead at `tip`, pointing along `direction`."""
    dx, dy = direction
    n = math.hypot(dx, dy) or 1.0
    dx, dy = dx / n, dy / n
    bx, by = tip[0] - dx * size, tip[1] - dy * size
    pen.poly([tip, (bx - dy * size * 0.62, by + dx * size * 0.62), (bx + dy * size * 0.62, by - dx * size * 0.62)],
             fill=fill)


def _tag_pill(ground: Image.Image, g: _Grid, right: float, top: float, text: str):
    """The ILLUSTRATIVE tag: tracked STONE_LT Montserrat in a GOLD_DIM outlined pill, its right edge at `right`
    (design px). Returns (plate, pill sprite)."""
    font = get_font("Montserrat", g.f(24), weight=700)
    track = g.s(3)
    tw = tracked_width(text, font, track)
    pad_x, h = g.s(16), g.s(40)
    x1 = g.x(right)
    x0 = x1 - tw - 2 * pad_x
    y0 = g.y(top)
    pen = _Pen((x0 - 2, y0 - 2, x1 + 2, y0 + h + 2))
    pen.rect(x0, y0, x1, y0 + h, outline=(*brand.GOLD_DIM, 255), width=max(1.5, g.s(2)), radius=h / 2)
    pill = pen.sprite()
    plate = _plan(ground, (x0 + pad_x, y0 + h / 2), text, font, brand.STONE_LT, "lm", track=track)
    return plate, pill


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


def _plain_title(title: str) -> str:
    """The plain head of a jargon title, cut at its first joint ("SUBTERRANEAN VENTILATION & CONVECTION
    DYNAMICS" -> "SUBTERRANEAN VENTILATION"): only words the fact-checked title already has."""
    for sep in (" & ", " AND ", ": ", " — ", " – ", " - "):
        head = title.split(sep)[0].strip()
        if head != title and len(head.split()) >= 2:
            return head
    return title


def _fit_title(g: _Grid, title: str, width_px: float) -> tuple[list[str], Any]:
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, font, short = fit_text(probe, title, "Anton", g.f(60), int(width_px), 1, g.f(56), sentences=False,
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
    moves on, so the deepest figure ends brightest."""
    return _render_cross_section(cs, levels, total, out, width, height, _Grid(width, height), _SECTION_TALL)


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
    title_lines, f_title, short = fit_text(probe_t, title, "Anton", g.f(L.title_px[0]), int(S(L.title_w)), 1,
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
            lines, f_fit, _ = fit_text(probe, lv["label"], "Montserrat", g.f(lab_px), int(lab_w), 2, g.f(lab_px),
                                       weight=600, quiet=True)
            if probe.textlength(lines[-1] if lines else "", font=f_fit) > lab_w:
                lines, f_fit, _ = fit_text(probe, lv["label"], "Montserrat", g.f(lab_px), int(lab_w), 3,
                                           g.f(lab_px), weight=600, quiet=True)
            capf = cap_height(ff) / g.k
            h = capf + label_gap + line_step * (len(lines) - 1) + 8
            out_blocks.append({"font": ff, "lines": lines, "cap": capf, "h": h,
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


def render_cross_section(cs: Dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
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


ROUTE_FIT = 0.74            # round 4: the share of the graphics box the stops span (0.62 before; their labels need the rest)
ROUTE_STOP_R = 10           # px: a stop on the way; the destination is the locator's 22 px pin
ROUTE_CHEVRON_EVERY = 120   # px between the direction chevrons on a completed leg
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


def route_chevrons(leg: list[tuple[float, float]], every: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """(tip, direction) of the chevrons along a leg: one every `every` px from half a step in, none within half a
    step of its end (the stop's disc sits there)."""
    lens = [math.dist(leg[i], leg[i + 1]) for i in range(len(leg) - 1)]
    total = sum(lens)
    out = []
    at = every / 2
    while at < total - every / 2 + 1e-6:
        acc = 0.0
        for i, ln in enumerate(lens):
            if acc + ln >= at and ln > 0:
                f = (at - acc) / ln
                (ax, ay), (bx, by) = leg[i], leg[i + 1]
                out.append(((ax + (bx - ax) * f, ay + (by - ay) * f), (bx - ax, by - ay)))
                break
            acc += ln
        at += every
    return out


def _route_chevron(d: ImageDraw.ImageDraw, tip: tuple[float, float], direction: tuple[float, float],
                   size: float = 9) -> None:
    """A small open chevron on the route line, pointing the way the journey goes: INK cased, BONE core."""
    dx, dy = direction
    nrm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / nrm, dy / nrm
    bx, by = tip[0] - dx * size, tip[1] - dy * size
    pts = [(bx - dy * size * 0.8, by + dx * size * 0.8), (tip[0] + dx * 3, tip[1] + dy * 3),
           (bx + dy * size * 0.8, by - dx * size * 0.8)]
    d.line(pts, fill=(*brand.INK, 255), width=6, joint="curve")
    d.line(pts, fill=(*brand.BONE, 255), width=3, joint="curve")


ROUTE_MAX_STOPS = 5         # the stops a route draws
ROUTE_NAME_PX = 34          # a stop's name, both orientations (round 4 review 5: 30 on 16:9, the bar figures' size)
ROUTE_LEADER_AT = 56        # round 4 review 5: px from a stop to its name's block beyond which a leader ties them
ROUTE_LEADER_W = 2          # round 4 review 5: the leader's core, the timeline flags' weight (GOLD_DIM, 2 px at 1080)
ROUTE_LEADER_CASE = 6       # and its ink casing, so it reads on the relief as the locator's pin leader does
# Round 4 review 9 (2026-11-03): before a stop is left unnamed its name steps down to ROUTE_NAME_MIN_PX (rr4 9:16's
# LIGHTHOUSE, 172 px from its nearest stop, and rr8's TOMB, 242 px, were left unnamed at 34 px)
ROUTE_NAME_STEPS = (34, 32, 30, 28)
ROUTE_NAME_MIN_PX = ROUTE_NAME_STEPS[-1]
ROUTE_DEEP_TRIES = 24       # and a stop it left unnamed tries this many of its clear places first (place_names)
# words that never stand alone on a name's line ("CUSTOMS / HOUSE OF / THE / GENOESE / TRADERS")
ROUTE_FUNCTION_WORDS = frozenset({"A", "AN", "THE", "OF", "ON", "IN", "AT", "TO", "BY", "AND", "OR", "FOR", "HIS",
                                  "HER", "ITS", "AS", "UP", "NEAR", "WITH", "FROM"})


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


def render_route(stops: List[Dict[str, Any]], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                 narration: str = "") -> Optional[Path]:
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
    lat0 = lat_c - (bh / 2 - (top + bottom) / 2 * scale) / kx
    lon0 = lon_c + (bw / 2 - (sx0 + sx1) / 2 * scale) / (kx * coslat)

    def project(lon: float, lat: float) -> Tuple[float, float]:
        return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx

    portrait = height > width
    fig_px = scale_figure_px(width, height)
    bar_left = (M if portrait else sx0 + 10)
    bar_right = sx1 - (bar_left - sx0)          # the other corner: the figure's end as far inside the box's right
    bar_base = (TEXT_BOTTOM - 22 if portrait else sy1 - 30)
    legs = []
    for pa, pb in itertools.pairwise(located):
        arc = great_circle((pa["lon"], pa["lat"]), (pb["lon"], pb["lat"]))
        legs.append([(project(lo, la)[0] / scale, project(lo, la)[1] / scale) for lo, la in arc])
    corridor = tuple(((min(xa, xb) - 14) * scale, (min(ya, yb) - 14) * scale, (max(xa, xb) + 14) * scale,
                      (max(ya, yb) + 14) * scale) for leg in legs for (xa, ya), (xb, yb) in itertools.pairwise(leg))
    pts = [(project(p["lon"], p["lat"])[0] / scale, project(p["lon"], p["lat"])[1] / scale) for p in located]
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
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    fixed: list[tuple[float, float, float, float]] = [(x - 26, y - 26, x + 26, y + 26) for x, y in pts]
    for leg in legs:                                     # no name over a stop or the route itself
        for (xa, ya), (xb, yb) in itertools.pairwise(leg):
            for f in (0.0, 0.5):
                x, y = xa + (xb - xa) * f, ya + (yb - ya) * f
                fixed.append((x - 9, y - 9, x + 9, y + 9))

    import numpy as np
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
            f_px = f_lab if px == f_lab.size else get_font("Montserrat", px, weight=800)
            for group in (measures, wide):
                if settings and not last:
                    break
                for measure, max_lines, tier in group:
                    # round 4 review 3: a third line before a smaller size
                    lines = route_name_lines(label, f_px, measure, max_lines)
                    if lines is not None and all(lines != st_[0] or f_px is not st_[1] for st_ in settings):
                        settings.append((lines, f_px, max_lines, tier))
            if settings and not last:
                break
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
                    a_, b_ = route_leader((x, y), u, (22 if k == len(pts) - 1 else ROUTE_STOP_R) + 3)
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
                found.append(((vague, 1000 if hero else -round(min(near - dist, ROUTE_NAME_MARGIN)) if vague else 0,
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
    stop_r = [22 if k == len(pts) - 1 else ROUTE_STOP_R for k in range(len(pts))]
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
        while orders:
            order, forced = orders.pop(0)
            got: dict[int, int] = {}
            named: list[tuple[float, float, float, float]] = []
            for k in order:
                if forced is not None and k == order[0]:
                    got[k] = forced
                    named += [tuple(b) for b in places[k]["boxes"][forced] if not np.isnan(b[0])]
                    continue
                ok = free[k] & boxes_clear(places[k]["boxes"], named, ROUTE_NAME_AIR)
                hit = np.flatnonzero(ok)
                if len(hit):
                    got[k] = int(hit[0])
                    named += [tuple(b) for b in places[k]["boxes"][got[k]] if not np.isnan(b[0])]
            vague = [k for k, i in got.items() if places[k]["vague"][i]]
            # review 8: the destination (the story's place, the hero stop) is the last a layout leaves unnamed
            key = (len(pts) - 1 not in got, len(pts) - len(got), sum(k not in cluster for k in vague))
            if best_key is None or key < best_key:
                best, best_key = (got, vague), key
            if key == (False, 0, 0):
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
        got, vague = best
        specs = [(f"stop{k}", sp) for k in sorted(got) for sp in places[k]["specs"][got[k]]]
        leaders = [route_leader((x, y), places[k]["union"][got[k]], stop_r[k] + 3)
                   if k in got and places[k]["lead"][got[k]] else None for k, (x, y) in enumerate(pts)]
        return specs, leaders, [k for k in range(len(pts)) if k not in got], vague, \
            [k for k, i in got.items() if places[k]["hero"][i]]

    def seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        (ax, ay), (bx, by) = a, b
        L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1.0
        f = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
        return math.dist(p, (ax + (bx - ax) * f, ay + (by - ay) * f))

    def globe_clear(gb: tuple[int, int, int, int], leaders: list) -> bool:
        """Round 4 review 6 (2026-11-03): the globe's disc keeps GLOBE_AIR px from every stop, leg (its casing
        and chevrons) and leader, not only from the names (it covered Constantinople and sat on Trebizond)."""
        c = ((gb[0] + gb[2]) / 2, (gb[1] + gb[3]) / 2)
        r = (gb[2] - gb[0]) / 2
        if any(math.dist(c, q) - r < stop_r[k] + 4 + GLOBE_AIR for k, q in enumerate(pts)):
            return False
        if any(seg_dist(c, a_, b_) - r < 10 + GLOBE_AIR for leg in legs for a_, b_ in itertools.pairwise(leg)):
            return False
        return not any(seg_dist(c, *ld) - r < ROUTE_LEADER_CASE / 2 + GLOBE_AIR for ld in leaders if ld)

    def bar_layout(max_px: float, corner: str, second: bool) -> dict[str, Any]:
        """The scale bar at `max_px` at most, in the bottom-left ("l") or bottom-right ("r") corner, with or without
        the other unit, and the boxes a name keeps clear of: the bar's body and its figures with ROUTE_SCALE_AIR."""
        bar = scale_bar(lat_c, kx / scale, narration, max_px)
        if not second:
            bar = {**bar, "secondary": None}
        y = bar_base - (SCALE_BAR_LIFT if second else 0)   # the bar rises only to make room for the other unit
        x = bar_left
        if corner == "r":
            x = bar_right - max(pl.box[2] for _n, pl in _scale_bar_plates(rest, bar, 0, y, fig_px))
        figs = [pl.box for _n, pl in _scale_bar_plates(rest, bar, x, y, fig_px)]
        a = ROUTE_SCALE_AIR
        boxes = [(x - 12, y - 28, x + bar["px"] + 12, y + 26)] + [(b[0] - a, b[1] - a, b[2] + a, b[3] + a) for b in figs]
        return {"bar": bar, "x": x, "y": y, "boxes": boxes, "figs": figs,
                "why": f"bottom-{'right' if corner == 'r' else 'left'} at {bar['px']:.0f} px"
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
            plates_, leaders_, unlabelled_, vague_, hero_ = place_names(furniture)
            if g and not globe_clear(g["box"], leaders_):
                continue
            # review 9: the destination first of all (it was third, so a layout naming one stop more left it out)
            key = (len(pts) - 1 in unlabelled_, len(unlabelled_), sum(k not in cluster for k in vague_), rank_)
            if best_ is None or key < best_[0]:
                best_ = (key, g, b, (plates_, leaders_), unlabelled_, vague_, hero_)
            if key[:3] == (False, 0, 0):
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
        if again is not None and again[0][:3] < best[0][:3]:
            best = again
    assert best is not None
    (rank_key, glay, blay, (plates, leaders), unlabelled, vague, hero) = best
    rank = rank_key[3]
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
    bar_x, bar_y = (blay["x"], blay["y"]) if blay else (bar_left, bar_base)
    words = tuple((b[0] * scale - 20, b[1] * scale - 20, b[2] * scale + 20, b[3] * scale + 20)
                  for b in [sp[4] for _, sp in plates] + [_leader_box(ld) for ld in leaders if ld])
    home = geo.find_country(located[-1]["country"])   # round 4: the destination's country as the figure
    gbox = glay["box"] if glay else None
    if gbox:
        words += (((gbox[0] - 16) * scale, (gbox[1] - 16) * scale, (gbox[2] + 16) * scale, (gbox[3] + 16) * scale),)
    if blay:                                          # no neighbour's name on the bar or its figures
        bx0, by0, bx1, by1 = _union(blay["boxes"])
        words += (((bx0 - 8) * scale, (by0 - 42) * scale, (bx1 + 8) * scale, (by1 + 8) * scale),)
    base = relief_base(lat0, lon0, bw, bh, kx, coslat, home, project, (0, 0, bw, bh),
                       label_bottom_fraction=bottom / height,
                       reserved=corridor + words,   # no name under the route, a stop's name, the globe or the bar
                       final_safe=(sx0 * scale, sy0 * scale, sx1 * scale, sy1 * scale),
                       name_home=True).resize((width, height), Image.LANCZOS)
    rest = base.convert("RGB")
    plates = [(name, plan_text(rest, sp[0], sp[1], sp[2], WHITE, sp[3], track=2, halo_px=21, min_halo=0.7))
              for name, sp in plates]       # review 5: only the chosen places are planned and measured
    stop_of = {id(pl): int(name[4:]) for name, pl in plates}
    bar_plates = _scale_bar_plates(rest, bar, bar_x, bar_y, fig_px) if bar else []
    t_bar = arrive[-1] + 0.3
    view = (lon0 - bw / 2 / (kx * coslat), lat0 - bh / 2 / kx, lon0 + bw / 2 / (kx * coslat), lat0 + bh / 2 / kx)
    globe = globe_inset(gbox[2] - gbox[0], lon_c, lat_c, home, view) if gbox else None
    last: list[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = _draw_globe(base.convert("RGB"), globe, gbox or (0, 0, 0, 0), t)
            d = ImageDraw.Draw(im, "RGBA")
            head = None
            for k, leg in enumerate(legs):
                prog = (t - arrive[k]) / max(1e-6, arrive[k + 1] - arrive[k])
                if prog <= 0:
                    break
                part = _sub_path(leg, [math.dist(leg[j], leg[j + 1]) for j in range(len(leg) - 1)], 0.0,
                                 lengths[k] * min(1.0, prog))
                if len(part) > 1:
                    d.line(part, fill=(*brand.INK, 255), width=13, joint="curve")    # the casing
                    d.line(part, fill=(*accent(), 255), width=7, joint="curve")
                    if prog < 1:
                        head = part[-1]
                    else:                    # round 4: direction on a still frame, chevrons on a completed leg
                        for tip, dirn in route_chevrons(leg, ROUTE_CHEVRON_EVERY):
                            _route_chevron(d, tip, dirn)
            for k, (x, y) in enumerate(pts):
                a = _ease((t - arrive[k] + 0.1) / 0.3)
                if a <= 0:
                    continue
                if k == len(pts) - 1:        # round 4: the destination as the locator's pin, with its ink core
                    d.ellipse([x - 22, y - 22, x + 22, y + 22], fill=(*accent(), int(255 * a)),
                              outline=(*brand.INK, int(255 * a)), width=4)
                    d.ellipse([x - 7, y - 7, x + 7, y + 7], fill=(*brand.INK, int(255 * a)))
                else:                        # a stop on the way: a smaller disc
                    d.ellipse([x - ROUTE_STOP_R, y - ROUTE_STOP_R, x + ROUTE_STOP_R, y + ROUTE_STOP_R],
                              fill=(*accent(), int(255 * a)), outline=(*brand.INK, int(255 * a)), width=3)
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
            ab = _ease((t - t_bar) / 0.3)
            if bar and ab > 0:
                _draw_scale_bar(im, bar, bar_x, bar_y, ab)
                for _n, pl in bar_plates:
                    im = draw_plate(im, pl, _ease((t - t_bar - 0.2) / 0.3))
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates + bar_plates)
        record_nontext(out, last[0], globe_marks(globe, gbox or (0, 0, 0, 0), (width, height)))
    return result


# ------------------------------------------------ rolling stone door defense
DIAGRAM_LABELS = {
    "defense": {"disc": "STONE DISC", "passage": "ILLUSTRATIVE PASSAGE SECTION",
                "opening": "CENTRAL OPENING", "opening_detail": "DETAIL VIEW",
                "track": "DOOR MOVEMENT", "summary": "ILLUSTRATIVE MECHANISM"},
    "ventilation": {"shaft": "AIR SHAFT", "exhaust_detail": "ILLUSTRATIVE AIRFLOW",
                    "airflow": "AIRFLOW CONCEPT", "summary": "ILLUSTRATIVE SECTION"},
}


# Shown when a spec gives no title of its own.
DIAGRAM_TITLES = {"defense": "ROLLING STONE DOOR DEFENSE MECHANISM",
                  "ventilation": "SUBTERRANEAN VENTILATION & CONVECTION DYNAMICS"}


def diagram_title(kind, spec):
    return (spec.get("title") or DIAGRAM_TITLES[kind]).upper()


def diagram_labels(kind, spec):
    overrides = spec.get("labels") or {}
    return {key: str(overrides.get(key, value)) for key, value in DIAGRAM_LABELS[kind].items()}


def _disc_sprite(r: float, seed: int = 5) -> Image.Image:
    """The rolling stone face-on, unrotated: a dressed stone face, chisel marks, a chipped notch on the rim (so
    the roll reads), a gold rim and the central opening."""
    import random
    rnd = random.Random(seed)
    size = math.ceil(r * 2 + 8)
    c = size / 2
    pen = _Pen((0, 0, size, size))
    face = tuple(int(a + (b - a) * 0.42) for a, b in zip(brand.INK, brand.GOLD_DIM))
    pen.ellipse(c, c, r, r, fill=(*face, 255))
    pen.ellipse(c, c, r * 0.7, r * 0.7, outline=(*brand.GOLD_DIM, 255), width=2)
    for j in range(11):
        a = 2 * math.pi * j / 11 + rnd.uniform(-0.15, 0.15)
        r0, r1 = r * rnd.uniform(0.26, 0.36), r * rnd.uniform(0.5, 0.62)
        pen.line([(c + r0 * math.cos(a), c + r0 * math.sin(a)), (c + r1 * math.cos(a), c + r1 * math.sin(a))],
                 (*brand.GOLD_DIM, 230), 2)
    for j in range(16):
        a = 2 * math.pi * j / 16 + rnd.uniform(-0.1, 0.1)
        r0 = r * rnd.uniform(0.76, 0.8)
        pen.line([(c + r0 * math.cos(a), c + r0 * math.sin(a)),
                  (c + r * 0.9 * math.cos(a + 0.05), c + r * 0.9 * math.sin(a + 0.05))], (*brand.GOLD_DIM, 200), 1.6)
    pen.ellipse(c, c, r, r, outline=(*brand.GOLD, 255), width=max(2.5, r * 0.04))
    notch = [(c + r * 0.985 * math.cos(a), c + r * 0.985 * math.sin(a)) for a in (-1.74, -1.40)]
    pen.poly([notch[0], (c + r * 0.86 * math.cos(-1.57), c + r * 0.86 * math.sin(-1.57)), notch[1]],
             fill=(*brand.INK, 255))
    hole = r * 0.16
    pen.ellipse(c, c, hole, hole, fill=(*brand.INK, 255), outline=(*brand.GOLD, 255), width=2)
    return pen.image()


def _arch(x0: float, y0: float, x1: float, y1: float, rad: float) -> list[tuple[float, float]]:
    """An opening with a square foot on the floor (y1) and rounded top corners of radius `rad`."""
    rad = min(rad, (x1 - x0) / 2, y1 - y0)
    pts = [(x0, y1)]
    for j in range(13):
        a = math.pi + (math.pi / 2) * j / 12
        pts.append((x0 + rad + rad * math.cos(a), y0 + rad + rad * math.sin(a)))
    for j in range(13):
        a = 1.5 * math.pi + (math.pi / 2) * j / 12
        pts.append((x1 - rad + rad * math.cos(a), y0 + rad + rad * math.sin(a)))
    return pts + [(x1, y1)]


def _hatch(pen: _Pen, box: tuple[float, float, float, float], gap: float, colour, width: float = 1.2) -> None:
    """Section hatching (45 degrees) clipped to `box`."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    x = x0 - h
    while x < x1:
        a = (max(x0, x), y0 + (max(x0, x) - x))
        b = (min(x1, x + h), y0 + (min(x1, x + h) - x))
        if b[0] > a[0]:
            pen.line([(a[0], a[1]), (b[0], b[1])], colour, width, caps=False)
        x += gap


@dataclass(frozen=True)
class _DoorLayout:
    """Where the rolling-stone door's two views sit, in their grid's design px (9:16 column or 16:9 frame)."""
    title_x: float
    title_w: float
    title_base: float
    title_step: float
    title_px: tuple[int, int]
    lab_px: int
    side_px: int
    heading_gap: float
    slot_h: float
    corr_h: float
    rock_h: float
    door_x: float
    slab_t: float
    elev_gap: float
    elev_min: float
    floor: float
    floor_bot: float
    r: float
    door_w: float
    door_h: float
    open_dx: float
    person_h: float
    person_x: float
    px0: float
    px1: float
    text_x: float
    inside: tuple[float, str]       # INSIDE's x and anchor
    outside: tuple[float, str]
    tag: tuple[float, float] | None  # the ILLUSTRATIVE tag's right and top; None: after the plan's heading
    disc_leader_x0: float | None     # where the STONE DISC leader starts; None: after its words
    disc_dy: float
    line_step: float
    heading_fill: tuple[int, int, int] = brand.STONE_LT


_DOOR_TALL = _DoorLayout(
    title_x=77, title_w=561, title_base=382, title_step=64, title_px=(60, 56), lab_px=30, side_px=30,
    heading_gap=50, slot_h=74, corr_h=64, rock_h=42, door_x=600, slab_t=20, elev_gap=50, elev_min=742,
    floor=1022, floor_bot=1050, r=100, door_w=124, door_h=172, open_dx=232, person_h=222, person_x=806,
    px0=65, px1=888, text_x=79, inside=(84, "lm"), outside=(870, "rm"), tag=(888, 342), disc_leader_x0=318,
    disc_dy=46, line_step=36)
# 16:9: both views run the width of the frame, stacked as in 9:16 so the door's axis still links them; every word
# inside the vignette-safe area (see _SECTION_WIDE), INSIDE and OUTSIDE set either side of the door.
_DOOR_WIDE = _DoorLayout(
    title_x=420, title_w=1180, title_base=252, title_step=72, title_px=(68, 56), lab_px=32, side_px=32,
    heading_gap=60, slot_h=80, corr_h=70, rock_h=40, door_x=1060, slab_t=24, elev_gap=62, elev_min=0,
    floor=796, floor_bot=822, r=96, door_w=132, door_h=180, open_dx=250, person_h=196, person_x=1330,
    px0=400, px1=1600, text_x=422, inside=(960, "rm"), outside=(1170, "lm"), tag=None, disc_leader_x0=None,
    disc_dy=52, line_step=40, heading_fill=brand.BONE)


def _render_defense_vertical(spec: dict[str, Any], out: Path, width: int, height: int) -> Path | None:
    """The 9:16 rolling-stone door (2026-11-02 design review, 3.5/10 -> rebuilt as two linked views): a plan on
    top (the corridor running across, a slot cut into its wall, the disc edge-on as a gold slab between INSIDE in
    teal and OUTSIDE in coral, a straight gold arrow along its track: it rolls, it does not swing) and an
    elevation below (the doorway face-on, the disc rolling out of its recess to block it, a standing figure for
    scale). The disc turns by distance / radius, so it visibly rolls, overshoots a little and settles. Words only
    from diagram_labels; tagged ILLUSTRATIVE."""
    return _render_defense(spec, out, width, height, _Grid(width, height), _DOOR_TALL)


def _render_defense_wide(spec: dict[str, Any], out: Path, width: int, height: int) -> Path | None:
    """The 16:9 rolling-stone door (2026-11-02 review round 2, 2-3/10: invented words, off-brand colours, 14-18 px
    type): the 9:16 rebuild composed for the wide frame, the plan a long corridor across the top, the elevation
    under it with the recess, the doorway and the figure in a row; only diagram_labels' words and the fixed
    ILLUSTRATIVE / INSIDE / OUTSIDE tags, brand colours, 28 px type or more."""
    return _render_defense(spec, out, width, height, _WideGrid(width, height), _DOOR_WIDE)


def _render_defense(spec: dict[str, Any], out: Path, width: int, height: int, g: _Grid, L: _DoorLayout) -> Path | None:
    """The rolling-stone door on grid `g` with layout `L` (see the two orientations above)."""
    labels = diagram_labels("defense", spec)
    title = diagram_title("defense", spec)
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    title_lines, f_title, short = fit_text(probe, title, "Anton", g.f(L.title_px[0]), int(S(L.title_w)), 1,
                                           g.f(L.title_px[1]), sentences=False, quiet=True)
    if short or probe.textlength(title_lines[0] if title_lines else "", font=f_title) > S(L.title_w):
        title_lines, f_title, _ = fit_text(probe, title, "Anton", g.f(L.title_px[0]), int(S(L.title_w)), 2,
                                           g.f(L.title_px[1]), sentences=False, quiet=True)
    title_base = [L.title_base + L.title_step * j for j in range(len(title_lines))]
    f_lab = get_font("Montserrat", g.f(L.lab_px), weight=600)
    f_side = get_font("Montserrat", g.f(L.side_px), weight=700)

    # ---- plan (design y)
    ph = title_base[-1] + L.heading_gap      # the plan's heading baseline
    rock_top = ph + 12
    slot_top = rock_top + 6
    c_top = slot_top + L.slot_h              # the corridor's walls
    c_bot = c_top + L.corr_h
    rock_bot = c_bot + L.rock_h
    door_x = L.door_x                        # both views share the door's axis
    slab_t, slab_len = L.slab_t, L.corr_h + 8
    slab_open, slab_shut = slot_top + 1, c_top - 4
    # ---- elevation
    eh = max(rock_bot + L.elev_gap, L.elev_min)   # its heading baseline
    face_top = eh + 14
    floor = L.floor
    r = L.r
    door_w, door_h = L.door_w, L.door_h
    open_x = door_x - L.open_dx
    person_h = L.person_h
    person_x = L.person_x

    sky = _rock_ground(width, height, lambda xs: xs * 0 + height * 2)       # the ink ground with its fine noise
    base = sky.copy()
    pen = _Pen((0, 0, width, height))
    rock = tuple(int(a + (b - a) * 0.16) for a, b in zip(brand.INK, brand.GOLD_DIM))
    hatch = (*brand.GOLD_DIM, 120)
    teal_floor = tuple(int(a + (b - a) * 0.14) for a, b in zip(brand.INK, brand.TEAL))
    coral_floor = tuple(int(a + (b - a) * 0.14) for a, b in zip(brand.INK, brand.CORAL))
    px0, px1 = X(L.px0), X(L.px1)
    # plan: rock walls, the corridor tinted inside / outside, the slot and its groove
    for y0, y1 in ((rock_top, c_top), (c_bot, rock_bot)):
        pen.rect(px0, Y(y0), px1, Y(y1), fill=(*rock, 255))
        _hatch(pen, (px0, Y(y0), px1, Y(y1)), S(14), hatch)
    pen.rect(px0, Y(c_top), X(door_x), Y(c_bot), fill=(*teal_floor, 255))
    pen.rect(X(door_x), Y(c_top), px1, Y(c_bot), fill=(*coral_floor, 255))
    pen.rect(X(door_x - slab_t / 2 - 3), Y(slot_top - 2), X(door_x + slab_t / 2 + 3), Y(c_top + 1),
             fill=(*brand.INK, 255), outline=(*brand.GOLD_DIM, 255), width=S(2))
    pen.rect(X(door_x - slab_t / 2 - 3), Y(c_bot - 1), X(door_x + slab_t / 2 + 3), Y(c_bot + 6),
             fill=(*brand.INK, 255), outline=(*brand.GOLD_DIM, 255), width=S(2))
    for yy in (c_top, c_bot):
        pen.line([(px0, Y(yy)), (X(door_x - slab_t / 2 - 3), Y(yy))], (*brand.STONE_LT, 255), S(3), caps=False)
        pen.line([(X(door_x + slab_t / 2 + 3), Y(yy)), (px1, Y(yy))], (*brand.STONE_LT, 255), S(3), caps=False)
    # the projection line between the views
    yy = rock_bot + 8
    while yy < face_top - 8:
        pen.line([(X(door_x), Y(yy)), (X(door_x), Y(min(face_top - 8, yy + 7)))], (*brand.GOLD_DIM, 255), S(2))
        yy += 15
    # elevation: the rock face, the recess, the track, the doorway, the floor
    pen.rect(px0, Y(face_top), px1, Y(floor), fill=(*rock, 255))
    _hatch(pen, (px0, Y(face_top), px1, Y(floor)), S(16), (*brand.GOLD_DIM, 70))
    pen.rect(px0, Y(floor), px1, Y(L.floor_bot), fill=(*tuple(int(v * 0.8) for v in rock), 255))
    _hatch(pen, (px0, Y(floor), px1, Y(L.floor_bot)), S(10), hatch)
    recess = g.pts(_arch(open_x - r - 12, floor - 2 * r - 14, door_x - door_w / 2 + 2, floor, r * 0.95))
    pen.poly(recess, fill=(*brand.INK2, 255))
    pen.line(recess, (*brand.GOLD_DIM, 255), S(2), caps=False)
    doorway = g.pts(_arch(door_x - door_w / 2, floor - door_h, door_x + door_w / 2, floor, 30))
    pen.poly(doorway, fill=(*coral_floor, 255))
    pen.line(doorway, (*brand.STONE_LT, 255), S(2.5), caps=False)
    pen.line([(px0, Y(floor)), (px1, Y(floor))], (*brand.STONE_LT, 255), S(3), caps=False)
    figure = _Pen((0, 0, width, height))
    _person(figure.d, int(X(person_x) * _SS), Y(floor) * _SS, S(person_h) * _SS, brand.STONE_LT)
    base.alpha_composite(pen.image())
    base.alpha_composite(figure.image())

    disc = _disc_sprite(S(r))
    shadow = _Pen((0, 0, S(2 * r + 60), S(40)))
    shadow.ellipse(S(r + 30), S(20), S(r * 0.95), S(9), fill=(0, 0, 0, 150))
    shadow_im = shadow.image().filter(ImageFilter.GaussianBlur(S(6)))

    def roll(t: float) -> float:
        """The disc's travel (0 = in its recess, 1 = across the door), with a small overshoot that settles."""
        p = _ease_io((t - 1.25) / 2.0) ** 1.15
        if t > 3.25:
            p = 1 + 0.035 * math.sin((t - 3.25) * 9.0) * math.exp(-(t - 3.25) * 4.0)
        return p

    # The disc's path in plan: a straight arrow beside the slab along its track, out of the slot and across the
    # corridor (2026-11-02 round 2: the curved arrow read as a door swinging on a hinge; the stone rolls).
    ax = door_x + slab_t / 2 + 14
    arrow_pts = [(ax, slot_top + 6), (ax, c_bot - 6)]
    arrow_box = (X(ax - 16), Y(slot_top - 6), X(ax + 16), Y(c_bot + 10))

    def arrow(progress: float) -> Image.Image | None:
        if progress <= 0:
            return None
        ap = _Pen(arrow_box)
        pts = g.pts(arrow_pts)
        head = S(16)
        tip = (pts[0][0], pts[0][1] + (pts[1][1] - pts[0][1]) * progress)
        if tip[1] - pts[0][1] > 1:
            ap.line([pts[0], (tip[0], tip[1] - (head * 0.8 if progress >= 1 else 0))], (*GOLD, 255), S(3.5))
            if progress >= 1:
                _chevron(ap, tip, (0, 1), head, (*GOLD, 255))
        return ap.image()

    arrow_full = arrow(1.0)
    leaders = _Pen((0, 0, width, height))
    hole_xy = (door_x, floor - r)
    disc_y = eh + L.disc_dy
    disc_x0 = (L.disc_leader_x0 if L.disc_leader_x0 is not None else
               L.text_x + probe.textlength(labels["disc"], font=f_side) / g.k + 16)
    leaders.line(g.pts([(disc_x0, disc_y), (door_x - 70, disc_y), (door_x - 52, floor - 2 * r + 22)]),
                 (*brand.GOLD_DIM, 255), S(2))
    leaders.ellipse(X(door_x - 52), Y(floor - 2 * r + 22), S(4), S(4), fill=(*GOLD, 255))
    # the central opening's words: two lines left of the recess where they fit there, else one line
    recess_x0 = open_x - r - 12
    room = S(recess_x0 - L.text_x - 12)
    op_lines, _f, short = fit_text(probe, labels["opening"], "Montserrat", g.f(L.side_px), int(room), 2,
                                   g.f(L.side_px), weight=700, quiet=True)
    if short or max(probe.textlength(ln, font=f_side) for ln in op_lines) > room:
        op_lines = [labels["opening"]]
    op_end = L.text_x + max(probe.textlength(ln, font=f_side) for ln in op_lines) / g.k
    leaders.line(g.pts([(op_end + 12, floor - r), (hole_xy[0] - r * 0.16 - 6, floor - r)]), (*brand.GOLD_DIM, 255),
                 S(2))
    leaders.ellipse(X(hole_xy[0] - r * 0.16 - 6), Y(floor - r), S(4), S(4), fill=(*GOLD, 255))
    leader_sprite = leaders.sprite()

    def art(t: float, final: bool = False) -> Image.Image:
        im = base.copy()
        p = 1.0 if final else roll(t)
        # plan: the slab moves across the corridor
        top = slab_open + (slab_shut - slab_open) * p
        slab = _Pen((X(door_x - slab_t / 2 - 6), Y(top - 4), X(door_x + slab_t / 2 + 6), Y(top + slab_len + 4)))
        slab.rect(X(door_x - slab_t / 2), Y(top), X(door_x + slab_t / 2), Y(top + slab_len), radius=S(4),
                  fill=(*GOLD, 255))
        slab.line([(X(door_x), Y(top + slab_len / 2) - S(5)), (X(door_x), Y(top + slab_len / 2) + S(5))],
                  (*brand.INK, 255), S(3))
        im.alpha_composite(slab.image(), (slab.x0, slab.y0))
        a_arrow = 1.0 if final else _ease((t - 0.55) / 0.6)
        if a_arrow >= 1 and arrow_full is not None:
            im.alpha_composite(arrow_full, (math.floor(arrow_box[0]), math.floor(arrow_box[1])))
        elif a_arrow > 0:
            part = arrow(a_arrow)
            if part is not None:
                im.alpha_composite(part, (math.floor(arrow_box[0]), math.floor(arrow_box[1])))
        # elevation: the disc rolls (turns by distance / radius)
        cx = open_x + (door_x - open_x) * p
        angle = math.degrees((cx - open_x) / r)
        spun = disc.rotate(-angle, resample=Image.BICUBIC)
        im.alpha_composite(shadow_im, (int(X(cx) - S(r + 30)), int(Y(floor) - S(20))))
        im.alpha_composite(spun, (round(X(cx) - spun.width / 2), round(Y(floor - r) - spun.height / 2)))
        leader_sprite.draw(im, 1.0 if final else _ease((t - 3.55) / 0.45))
        return im

    final = art(0, final=True)
    timed = []
    for j, (ln, yb) in enumerate(zip(title_lines, title_base)):
        timed.append((f"title{j}", _plan(final, (X(L.title_x), Y(yb)), ln, f_title, WHITE, "ls"), 0.1 + 0.08 * j))
    heading = _plan(final, (X(L.text_x), Y(ph)), labels["track"], f_lab, L.heading_fill, "ls", track=S(2))
    if L.tag is not None:
        tag, pill = _tag_pill(final, g, L.tag[0], L.tag[1], "ILLUSTRATIVE")
    else:   # on the plan heading's line, after it
        probe_tag = get_font("Montserrat", g.f(24), weight=700)
        tw = tracked_width("ILLUSTRATIVE", probe_tag, g.s(3)) + 2 * g.s(16)
        tag, pill = _tag_pill(final, g, g.ix(heading.box[2] + S(28) + tw), ph - 30, "ILLUSTRATIVE")
    timed.append(("tag", tag, 0.3))
    timed.append(("plan_heading", heading, 0.35))
    timed.append(("inside", _plan(final, (X(L.inside[0]), Y((c_top + c_bot) / 2)), "INSIDE", f_side, brand.TEAL,
                                  L.inside[1], track=S(3)), 0.6))
    timed.append(("outside", _plan(final, (X(L.outside[0]), Y((c_top + c_bot) / 2)), "OUTSIDE", f_side, brand.CORAL,
                                   L.outside[1], track=S(3)), 0.7))
    timed.append(("elevation_heading", _plan(final, (X(L.text_x), Y(eh)), labels["opening_detail"], f_lab,
                                             L.heading_fill, "ls", track=S(2)), 0.45))
    timed.append(("disc", _plan(final, (X(L.text_x), Y(disc_y)), labels["disc"], f_side, GOLD, "lm"), 3.6))
    for j, ln in enumerate(op_lines):
        yc = floor - r + L.line_step * (j - (len(op_lines) - 1) / 2)
        timed.append((f"opening{j}", _plan(final, (X(L.text_x), Y(yc)), ln, f_side, WHITE, "lm"), 3.75 + 0.06 * j))
    last: list[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = art(t)
            pill.draw(im, _ease((t - 0.3) / 0.45))
            for _name, plate, t_in in timed:
                a = _ease((t - t_in) / 0.45)
                if a > 0:
                    im = plate.draw(im, a, round(S(10) * (1 - a)))
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], [(name, plate) for name, plate, _t in timed])
    return result


def render_rolling_stone_defense(
    spec: Dict[str, Any],
    out: Path,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
) -> Optional[Path]:
    """Animated architectural diagram of an ancient subterranean rolling stone door: the stone disc rolling along
    its carved track out of its recess to block the doorway, in two linked views (plan and elevation).

    9:16 (2026-11-02 design review, scored 3.5/10): rebuilt in _render_defense_vertical, a disc that truly rolls
    (rotation = distance / radius) and brand colours only; 16:9 (round 2, 2-3/10) the same rebuild composed for
    the wide frame (_render_defense_wide)."""
    if width <= height:
        return _render_defense_vertical(spec, out, width, height)
    return _render_defense_wide(spec, out, width, height)


# ----------------------------------------------- ventilation & airflow dynamics
@dataclass(frozen=True)
class _VentLayout:
    """Where the ventilation section's parts sit, in its grid's design px (9:16 column or 16:9 frame)."""
    title_x: float
    title_w: float
    title_base: float
    title_step: float
    title_px: tuple[int, int]
    lab_px: int
    side_px: int
    sub_gap: float
    ground_gap: float
    hill: tuple[float, float, float]          # x, height, width
    in_shaft: tuple[float, float]             # x, width
    ex_shaft: tuple[float, float]
    well_d: float
    gal_h: float
    room_start: tuple[float, float]
    room_w: tuple[float, float]
    room_gap: tuple[float, float]
    room_h: tuple[float, float]
    room_stop: float                          # rooms end this far short of the outlet shaft's wall
    in_label: tuple[float, str]               # the intake's AIR SHAFT: x and anchor
    out_label: tuple[float, str]
    tag_right: float | None                   # the tag's right edge (30 above the subtitle); None: after it
    text_x: float
    heading_fill: tuple[int, int, int] = brand.STONE_LT
    hold: float | None = None                 # out-air waits this long after the in-air has landed (None: overlap)
    label_level: int | None = None            # the AIR SHAFT labels on this level's lane; None: above the first


_VENT_TALL = _VentLayout(
    title_x=77, title_w=811, title_base=382, title_step=64, title_px=(60, 56), lab_px=30, side_px=30, sub_gap=48,
    ground_gap=92, hill=(690, 40, 190), in_shaft=(300, 36), ex_shaft=(690, 26), well_d=990, gal_h=30,
    room_start=(26, 50), room_w=(84, 120), room_gap=(30, 52), room_h=(62, 74), room_stop=90, in_label=(79, "lm"),
    out_label=(886, "rm"), tag_right=888, text_x=79)
# 16:9: the section centred in the vignette-safe area (see _SECTION_WIDE), the hill and its outlet to the right,
# each AIR SHAFT beside its own shaft; the in-air lands first and holds 0.6 s before the out-air moves.
_VENT_WIDE = _VentLayout(
    title_x=420, title_w=1180, title_base=252, title_step=72, title_px=(68, 56), lab_px=32, side_px=32, sub_gap=50,
    ground_gap=84, hill=(1300, 46, 250), in_shaft=(740, 44), ex_shaft=(1300, 30), well_d=792, gal_h=32,
    room_start=(30, 56), room_w=(110, 160), room_gap=(36, 60), room_h=(50, 60), room_stop=110,
    in_label=(682, "rm"), out_label=(1342, "lm"), tag_right=None, text_x=422, heading_fill=brand.BONE, hold=0.6,
    label_level=1)


def _render_ventilation_vertical(spec: dict[str, Any], out: Path, width: int, height: int) -> Path | None:
    """The 9:16 ventilation section (2026-11-02 design review, 3.5/10 -> rebuilt): a hill's ground profile near
    the top of the text box, one deep shaft to the bottom ending in a well, four levels of rooms off it and a
    second, higher opening. Air in as a TEAL dashed path down the shaft and into the levels, air out as a CORAL
    path through the second shaft (no sky blue or red); the title is the plain head of diagram_title, and the
    only words are diagram_labels' (the intake and the outlet are both the AIR SHAFT), tagged ILLUSTRATIVE."""
    return _render_ventilation(spec, out, width, height, _Grid(width, height), _VENT_TALL)


def _render_ventilation_wide(spec: dict[str, Any], out: Path, width: int, height: int) -> Path | None:
    """The 16:9 ventilation section (2026-11-02 review round 2, 2-3/10: invented words such as "DRAWS COOL DENSE
    AIR DOWN" and "CONVECTION EXHAUST", sky blue and amber, 14-18 px type): the 9:16 rebuild composed for the wide
    frame, TEAL air in first, a 0.6 s hold, then CORAL air out; only diagram_labels' words and the plain title."""
    return _render_ventilation(spec, out, width, height, _WideGrid(width, height), _VENT_WIDE)


def _render_ventilation(spec: dict[str, Any], out: Path, width: int, height: int, g: _Grid,
                        L: _VentLayout) -> Path | None:
    """The ventilation section on grid `g` with layout `L` (see the two orientations above)."""
    import random

    import numpy as np
    labels = diagram_labels("ventilation", spec)
    title = _plain_title(diagram_title("ventilation", spec))
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    title_lines, f_title, short = fit_text(probe, title, "Anton", g.f(L.title_px[0]), int(S(L.title_w)), 1,
                                           g.f(L.title_px[1]), sentences=False, quiet=True)
    if short or probe.textlength(title_lines[0] if title_lines else "", font=f_title) > S(L.title_w):
        title_lines, f_title, _ = fit_text(probe, title, "Anton", g.f(L.title_px[0]), int(S(L.title_w)), 2,
                                           g.f(L.title_px[1]), sentences=False, quiet=True)
    title_base = [L.title_base + L.title_step * j for j in range(len(title_lines))]
    sub_base = title_base[-1] + L.sub_gap
    f_lab = get_font("Montserrat", g.f(L.lab_px), weight=600)
    f_side = get_font("Montserrat", g.f(L.side_px), weight=700)

    ground_lo = sub_base + L.ground_gap  # the ground at the frame's edges (design y)
    hill_x, hill_h, hill_w = L.hill

    def ground_d(x: float) -> float:
        return ground_lo - hill_h * math.exp(-((x - hill_x) / hill_w) ** 2)

    in_x, in_w = L.in_shaft              # the deep shaft
    ex_x, ex_w = L.ex_shaft              # the outlet shaft, through the hill's crown
    well_d = L.well_d
    floors = [ground_lo + (well_d - 60 - ground_lo) * f for f in (0.2, 0.44, 0.68, 0.92)]
    gal_h = L.gal_h
    rnd = random.Random(11)
    rooms = []
    for k, fl in enumerate(floors):
        x = in_x + in_w / 2 + rnd.uniform(*L.room_start)
        while x < ex_x - ex_w / 2 - L.room_stop:
            w = rnd.uniform(*L.room_w)
            w = min(w, ex_x - ex_w / 2 - 30 - x)
            rooms.append((x, x + w, fl, rnd.uniform(*L.room_h), 300 + 10 * k + len(rooms)))
            x += w + rnd.uniform(*L.room_gap)

    carve_box = (X(in_x - 50), Y(ground_lo - hill_h - 4), X(ex_x + 30), Y(well_d + 30))

    def paint(pen: _Pen) -> None:
        pen.rect(X(in_x - in_w / 2), Y(ground_d(in_x) - 6), X(in_x + in_w / 2), Y(well_d - 20), fill=255)
        pen.ellipse(X(in_x), Y(well_d - 24), S(40), S(30), fill=255)                       # the well
        pen.rect(X(ex_x - ex_w / 2), Y(ground_d(ex_x) - 6), X(ex_x + ex_w / 2), Y(floors[-1]), fill=255)
        for fl in floors:
            pen.rect(X(in_x), Y(fl - gal_h), X(ex_x), Y(fl), fill=255)
        for x0, x1, fl, h, seed in rooms:
            pen.poly(g.pts(_vault(x0, x1, fl, h, seed)), fill=255)

    city = _carve(carve_box, paint, S(2))

    def gpx(xs) -> Any:
        """The ground's frame y at frame x (the hill in design units, mapped)."""
        return np.array([Y(ground_d(g.ix(x))) for x in xs], dtype=np.float32)

    base = _rock_ground(width, height, gpx)
    pen = _Pen((0, Y(ground_lo - hill_h - 10), width, Y(ground_lo + 10)))
    surface = [(float(x), gpx([float(x)])[0]) for x in range(0, width + 1, max(4, width // 120))]
    pen.line(surface, (*brand.STONE_LT, 190), S(3), caps=False)   # it runs past the text box: kept dim
    base.alpha_composite(pen.image(), (pen.x0, pen.y0))
    city.draw(base)
    water = _Pen((X(in_x - 44), Y(well_d - 40), X(in_x + 44), Y(well_d + 10)))
    for j, yy in enumerate((well_d - 22, well_d - 12, well_d - 3)):
        half = 30 - 7 * j
        water.line([(X(in_x - half), Y(yy)), (X(in_x + half), Y(yy))], (*brand.GOLD_DIM, 255), S(2))
    water.sprite().draw(base)

    # ---- the air: the paths (design px), dashed and moving
    def lane(fl: float) -> float:
        return fl - gal_h / 2

    split = (in_x + ex_x) / 2 + 10
    cool = [[(in_x, ground_d(in_x) - 46), (in_x, lane(floors[-1]))]]
    cool += [[(in_x, lane(fl)), (split - 22, lane(fl))] for fl in floors]
    warm = [[(split + 22, lane(fl)), (ex_x, lane(fl))] for fl in floors]
    warm.append([(ex_x, lane(floors[-1])), (ex_x, ground_d(ex_x) - 26)])
    starts_c = [0.5] + [0.5 + 1.0 * (lane(fl) - cool[0][0][1]) / (lane(floors[-1]) - cool[0][0][1]) for fl in floors]
    if L.hold is None:
        starts_w = [s + 0.55 for s in starts_c[1:]] + [starts_c[-1] + 0.75]
    else:   # every in-path lands (0.8 s each), the hold, then the out-air level by level and up the outlet
        t_out = max(starts_c) + 0.8 + L.hold
        starts_w = [t_out + 0.12 * k for k in range(len(floors))] + [t_out + 0.12 * len(floors) + 0.3]
    paths = [(p, brand.TEAL, st) for p, st in zip(cool, starts_c)]
    paths += [(p, brand.CORAL, st) for p, st in zip(warm, starts_w)]
    air_box = (X(in_x - 30), Y(ground_lo - hill_h - 70), X(ex_x + 30), Y(lane(floors[-1]) + 24))

    def air(t: float) -> Image.Image:
        ap = _Pen(air_box)
        for pts_d, colour, start in paths:
            pts = g.pts(pts_d)
            grow = _ease((t - start) / 0.8)
            if grow <= 0:
                continue
            length = _path_len(pts)
            for seg in _dash_runs(pts, S(16), S(13), S(70) * t, length * grow - S(14)):
                if len(seg) > 1:
                    ap.line(seg, (*colour, 255), S(6))
            if grow > 0.98:
                tip = pts[-1]
                prev = pts[-2]
                _chevron(ap, tip, (tip[0] - prev[0], tip[1] - prev[1]), S(18), (*colour, 255))
        return ap.image()

    def art(t: float, final: bool = False) -> Image.Image:
        im = base.copy()
        im.alpha_composite(air(6.0 if final else t), (math.floor(air_box[0]), math.floor(air_box[1])))
        return im

    final = art(0, final=True)
    timed = []
    for j, (ln, yb) in enumerate(zip(title_lines, title_base)):
        timed.append((f"title{j}", _plan(final, (X(L.title_x), Y(yb)), ln, f_title, WHITE, "ls"), 0.1 + 0.08 * j))
    subtitle = _plan(final, (X(L.text_x), Y(sub_base)), labels["airflow"], f_lab, L.heading_fill, "ls", track=S(2))
    timed.append(("subtitle", subtitle, 0.3))
    if L.tag_right is not None:
        tag, pill = _tag_pill(final, g, L.tag_right, sub_base - 30, labels["summary"])
    else:   # on the subtitle's line, after it
        f_tag = get_font("Montserrat", g.f(24), weight=700)
        tw = tracked_width(labels["summary"], f_tag, g.s(3)) + 2 * g.s(16)
        tag, pill = _tag_pill(final, g, g.ix(subtitle.box[2] + S(28) + tw), sub_base - 30, labels["summary"])
    timed.append(("tag", tag, 0.35))
    shaft_y = ((ground_d(in_x) + floors[0] - gal_h) / 2 + 6 if L.label_level is None else
               lane(floors[L.label_level]))
    leaders = _Pen((0, 0, width, height))
    in_label = _plan(final, (X(L.in_label[0]), Y(shaft_y)), labels["shaft"], f_side, brand.TEAL, L.in_label[1])
    leaders.line([(in_label.box[2] + S(10), Y(shaft_y)), (X(in_x - in_w / 2 - 6), Y(shaft_y))],
                 (*brand.GOLD_DIM, 255), S(2))
    out_label = _plan(final, (X(L.out_label[0]), Y(shaft_y)), labels["shaft"], f_side, brand.CORAL, L.out_label[1])
    leaders.line([(X(ex_x + ex_w / 2 + 6), Y(shaft_y)), (out_label.box[0] - S(10), Y(shaft_y))],
                 (*brand.GOLD_DIM, 255), S(2))
    leader_sprite = leaders.sprite()
    timed.append(("shaft_in", in_label, 0.6))
    timed.append(("shaft_out", out_label, starts_w[-1] + 0.2))
    final = art(0, final=True)
    leader_sprite.draw(final)
    timed = [(name, _plan(final, plate.xy, plate.text, plate.font, plate.fill, plate.anchor, track=plate.track),
              t_in) for name, plate, t_in in timed]
    last: list[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = art(t)
            pill.draw(im, _ease((t - 0.35) / 0.45))
            leader_sprite.draw(im, _ease((t - 0.6) / 0.45))
            for _name, plate, t_in in timed:
                a = _ease((t - t_in) / 0.45)
                if a > 0:
                    im = plate.draw(im, a, round(S(10) * (1 - a)))
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], [(name, plate) for name, plate, _t in timed])
    return result


def render_ventilation_airflow(
    spec: Dict[str, Any],
    out: Path,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
) -> Optional[Path]:
    """Animated section of subterranean ventilation: air drawn in down a deep shaft and through the levels, and
    out through a second, higher opening.

    9:16 (2026-11-02 design review, scored 3.5/10): rebuilt in _render_ventilation_vertical as a section under a
    hill, a deep shaft to a well, four levels, TEAL air in and CORAL air out, a plain title and diagram_labels'
    words only (the old frame drew sky blue and red and unchecked claims); 16:9 (round 2, 2-3/10) the same rebuild
    composed for the wide frame (_render_ventilation_wide)."""
    if width <= height:
        return _render_ventilation_vertical(spec, out, width, height)
    return _render_ventilation_wide(spec, out, width, height)


# ----------------------------------------------- every graphic for a run (make_all)
PHOTO_BUILDERS = frozenset({"numbers", "timeline"})  # builders that draw the hero photo as a backdrop
RETIRED_DIAGRAM_KEYS = ("rolling_stone_defense", "ventilation_airflow")  # payload keys make_all no longer draws


def make_all(payload: Dict[str, Any], topic: Dict[str, Any], work_dir: Path, photo: Optional[Path] = None, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
             palette: Optional[Dict[str, Any]] = None) -> List[Path]:
    """Every graphic the script and topic support. Never raises: graphics are optional.
    `photo`: the topic's hero photo, used (blurred) behind the number cards.
    `palette`: the run's art direction (src/art_direction.py), made active here because this runs in a
    worker thread, where the caller's context variable would not be seen."""
    from .art_direction import use_palette
    use_palette(palette)
    made: List[Path] = []
    jobs = []
    if payload.get("stat_cards"):
        jobs.append(("numbers", lambda: render_stat_cards(payload["stat_cards"], work_dir / "graphic_numbers.mp4", photo, width=width, height=height,
                                                                narration=payload.get("narration_script") or "")))
    if payload.get("cross_section"):
        jobs.append(("cross-section", lambda: render_cross_section(payload["cross_section"], work_dir / "graphic_section.mp4", width=width, height=height)))
    if payload.get("route"):
        jobs.append(("route", lambda: render_route(payload["route"], work_dir / "graphic_route.mp4", width=width, height=height)))
    # The rolling-stone door and ventilation diagrams are retired (2026-11-02): no generated script declares
    # `rolling_stone_defense` or `ventilation_airflow` (script_engine never emits them), so an old payload that
    # still carries either key renders its other graphics and not these. RETIRED_DIAGRAM_KEYS names them; their
    # renderers stay only because tests draw them directly.
    place = None
    try:
        place = geo.locate(topic)
    except Exception as e:
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
        from .typography import FONTS_DIR, bundled_font_files
        from .lineage import sidecar
        source_files = [Path(__file__), Path(__file__).with_name("typography.py"),
                        Path(__file__).with_name("brand.py"),
                        *[FONTS_DIR / filename for filename in bundled_font_files()]]
        if photo and name in PHOTO_BUILDERS:  # timeline also draws the photo (2026-11-02)
            source_files.append(Path(photo))
        if name == "scale":
            source_files.append(REFERENCE_FILE)
        if name in ("map", "route"):
            source_files += [p for p in (geo.COUNTRIES_FILE, geo.RELIEF_FILE) if p.is_file()]
        from .encoding import get_video_encoder
        inputs = {"sources": file_inputs(source_files), "place": place, "width": width,
                  "height": height, "fps": VIDEO_FPS, "encoder": get_video_encoder(),
                  "spec": {key: payload.get(key) for key in ("stat_cards", "cross_section", "route",
                           "timeline", "scale_fact", "rolling_stone_defense", "ventilation_airflow")}}
        if name in ("numbers", "map"):  # figures (a map's scale bar) in the narration's units (2026-10-29)
            inputs["narration"] = payload.get("narration_script") or ""
        checkpoint = Checkpoints(work_dir / "stage_cache" / name)
        try:
            if checkpoint.reusable("graphic", inputs):
                return Path(next(iter(checkpoint.read()["graphic"]["outputs"])))
            result = job()
            # Maps incorporate external relief/geodata and require their own source
            # registry. Only these builders are entirely drawn by this module.
            original_builders = {"cross-section", "timeline", "scale", "defense", "ventilation", "numbers"}
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
        except Exception as e:
            log.warning("Graphic '%s' failed (%s); continuing without it.", name, e)
            return None

    if len(jobs) <= 1:
        results = [_run_job(name, job) for name, job in jobs]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(jobs), 4)) as pool:
            futures = [pool.submit(_run_job, name, job) for name, job in jobs]
            results = [f.result() for f in futures]

    made = [p for p in results if p]
    log.info("Original graphics (%dx%d): %s", width, height, ", ".join(p.stem.replace("graphic_", "") for p in made) or "none")
    return made
