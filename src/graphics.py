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
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from . import geo
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .typography import (cap_height, drawable, fit_font, fit_lines, fit_text, get_font, plan_text,
                         spoken_system, spoken_units, tracked_width)
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

from .reference_facts import references, selected_reference, measurement_text, REFERENCE_FILE

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


def scale_bar(lat: float, px_per_deg_lat: float, narration: str) -> Dict[str, Any]:
    """A scale bar for the map's last frame, in the unit the narration speaks for lengths (2026-10-29: the drawn
    unit follows the voice; km when the narration gives no length): its length in that unit (the longest nice
    length up to 260 px), its px and label."""
    import math
    phi = math.radians(lat)
    km_per_deg = (111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)) / 1000.0
    imperial = spoken_system(narration) == "imperial"
    unit_km = 1.609344 if imperial else 1.0
    px_per_unit = px_per_deg_lat / km_per_deg * unit_km
    value = max([v for v in SCALE_STEPS if v * px_per_unit <= 260] or [SCALE_STEPS[0]])
    return {"value": value, "unit": "MI" if imperial else "KM", "px": round(value * px_per_unit, 1),
            "label": f"{value} {'MI' if imperial else 'KM'}", "km_per_deg_lat": round(km_per_deg, 3)}


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


def camera_spans(n: int, wide: float, close: float, travel: float, fps: int) -> list[float]:
    """The locator's span (degrees across the frame) on each of `n` frames (round 3, 2026-11-02): eased in and
    out in log space over `travel` seconds, so each halving of the view takes the same time (a cubic ease-out on
    the linear span spent most of the move in the first half-second), then held."""
    lw, lc = math.log(wide), math.log(close)
    return [math.exp(lw + (lc - lw) * _ease_io(i / fps / travel)) for i in range(n)]


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
                transient: Optional[List[Dict[str, Any]]] = None) -> Image.Image:
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
    if home is not None and homea.max() > 0:     # a bone inner stroke 6 px inside the home country's border
        inner = home_m.filter(ImageFilter.MinFilter(13))
        ring = np.clip(np.asarray(inner, np.float32) - np.asarray(inner.filter(ImageFilter.MinFilter(3)), np.float32),
                       0, 255)[..., None] / 255.0
        out = out * (1 - ring * 0.5) + np.array(brand.BONE, dtype=np.float32) * ring * 0.5
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
    font = get_font("Montserrat", 66)
    placed: List[Tuple[float, float, float, float]] = list(reserved)  # e.g. the pin and place label
    for c in sorted(visible, key=lambda c: (c["label_rank"], -c["size"])):
        # Areas of indeterminate status (e.g. Siachen Glacier) are not countries and stay unnamed.
        if c is home or not c["label_at"] or c["label_rank"] > 5 or c.get("type") == "Indeterminate":
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
        rings: list | None = None
        for dx, dy in _NAME_NUDGES:
            x, y = x0_ + dx * (r0 - l0), y0_ + dy * (b0 - t0)
            l, t, r, b = l0 + x - x0_, t0 + y - y0_, r0 + x - x0_, b0 + y - y0_
            if dx or dy:
                if rings is None:
                    rings = [[[project(px_, py_) for px_, py_ in ring] for ring in poly] for poly in c["polygons"]]
                if not all(geo.point_in_polygons(qx, qy, rings)
                           for qx, qy in ((x, y), (l, t), (r, t), (l, b), (r, b))):
                    continue
            if _place_name(c, name, font, (x, y), (l, t, r, b), bw, bh, label_bottom_fraction, final_view, final_safe,
                           placed, transient, draw):
                break
    return base


# Where a country's name may move when its label point is taken: offsets in its own width (dx) and height (dy),
# nearest first; (0, 0) is Natural Earth's point.
_NAME_NUDGES = sorted(((dx, dy) for dx in (0.0, -0.35, 0.35, -0.7, 0.7) for dy in (0.0, -1.5, 1.5, -3.0, 3.0, -4.5, 4.5)),
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
    if transient is not None:  # drawn into the base: recorded so its place on each frame can be checked
        transient.append({"name": c["display"], "sprite": None, "box": box})
    _text(draw, (x, y), name, font=font, fill=MAP_NAME, anchor="mm")
    return True


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
    # 16:9: low enough that the Anton title's top stays inside title-safe (at 18 % it rose to y 91, round 3)
    label_y = int(height * 0.24) if width > height else max(TEXT_TOP + 70, height // 2 - 210)
    # Keep neighbours' names clear of the place label, its leader and the pin in the final close-up: the band
    # from the label's top to just below the pin, across the label's width plus a margin (output pixels).
    half_w = min(width // 2, 60 + max(len(label) * 22, len(sub_label) * 18))
    to_base = lambda x, y: (final_view[0] + x * (final_view[2] - final_view[0]) / width,  # noqa: E731
                            final_view[1] + y * (final_view[3] - final_view[1]) / height)
    reserved = (to_base(width // 2 - half_w, label_y - 200) + to_base(width // 2 + half_w, height // 2 + 70),)
    portrait = height > width
    # The scale bar: the final close-up shows `span_close` degrees of longitude across the frame at cos(lat) scale,
    # so one degree of latitude is width / span_close px there; drawn once the camera rests, in both orientations
    # (16:9 bottom-left of title-safe, clear of YouTube's timestamp corner).
    bar = scale_bar(lat0, width / span_close, narration)
    sx0, sy0, sx1, sy1 = get_text_box(width, height)
    bar_x = M if portrait else sx0 + 10
    bar_y = TEXT_BOTTOM - 22 if portrait else sy1 - 30
    if bar:  # no neighbour's name under the bar and its label
        reserved += (to_base(bar_x - 20, bar_y - 70) + to_base(bar_x + bar["px"] + 260, bar_y + 30),)
    labels: List[Dict[str, Any]] = []
    base = relief_base(lat0, lon0, bw, bh, kx, coslat, geo.find_country(place["country"]), project, final_view,
                       label_bottom_fraction=get_text_bounds(width, height)[1] / height, reserved=reserved,
                       final_safe=to_base(sx0, sy0) + to_base(sx1, sy1), transient=labels)

    n = int(CLIP_SECONDS * VIDEO_FPS)
    zooms = [span_wide / sp for sp in camera_spans(n, span_wide, span_close, MAP_TRAVEL, VIDEO_FPS)]
    plan = map_label_plan(labels, zooms, width, height, bw, bh)
    # The place label and the bar's figure are planned on the camera's resting view (their halos solved there).
    zf_last = zooms[-1]
    rest = base.resize((width, height), Image.BICUBIC, box=(bw / 2 - bw / 2 / zf_last, bh / 2 - bh / 2 / zf_last,
                                                            bw / 2 + bw / 2 / zf_last, bh / 2 + bh / 2 / zf_last)).convert("RGB")
    label_plates = _label_plates(rest, width // 2, label_y, label, sub_label)
    if area:   # the region's point: say so, in the system-label pattern ("APPROX. DEPTHS")
        f_ap = get_font("Montserrat", 30, weight=700)
        under = max(pl.box[3] for _, pl in label_plates)
        label_plates.append(("approx", plan_text(rest, (width // 2, under + 16 + cap_height(f_ap)), "APPROX. LOCATION",
                                                 f_ap, MUTED, "ms", track=4, halo_px=21, min_halo=0.8)))
    cx, cy = width // 2, height // 2
    lead_top = max(pl.box[3] for _, pl in label_plates) + 14
    lead_bot = cy - (78 if area else 34)
    bar_plate = None
    if bar:
        bar_plate = plan_text(rest, (bar_x + bar["px"] + 22, bar_y + 13), bar["label"],
                              get_font(brand.DATA_FONT, 38, weight=600), WHITE, "ls", track=1, min_halo=0.6, halo_px=17)
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
                im = plate.draw(im, a, dy=round(12 * (1 - a)))
            al = _ease((t - t_label - 0.2) / 0.3)
            if al > 0 and lead_bot - lead_top > 28:          # the leader from the label down to the pin
                d = ImageDraw.Draw(im, "RGBA")
                y_end = lead_top + (lead_bot - lead_top) * al
                d.line([(cx, lead_top), (cx, y_end)], fill=(*brand.INK, int(200 * al)), width=6)
                d.line([(cx, lead_top), (cx, y_end)], fill=(*WHITE, int(255 * al)), width=2)
            if bar:
                ab = _ease((t - t_label - 0.15) / 0.3)        # drawn from its left end
                if ab > 0:
                    d = ImageDraw.Draw(im, "RGBA")
                    x1 = bar_x + bar["px"] * ab
                    d.line([(bar_x, bar_y), (x1, bar_y)], fill=(*brand.INK, 200), width=9)   # an ink keyline
                    d.line([(bar_x, bar_y), (x1, bar_y)], fill=(*WHITE, 255), width=5)
                    for xx, tall in ((bar_x, 16), (bar_x + bar["px"] / 2, 9), (bar_x + bar["px"], 16)):
                        if xx <= x1 + 0.5:
                            d.line([(xx, bar_y - tall), (xx, bar_y + 2)], fill=(*WHITE, 255), width=5)
                    im = bar_plate.draw(im, _ease((t - t_label - 0.35) / 0.3))
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], label_plates + ([("scale", bar_plate)] if bar_plate else []))
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
WIDE_TIMELINE = (160, 1760)       # the 16:9 timeline's run, design px (x 8.3-91.7 %: title-safe, clear of the corner)
COMPRESSED_FLOOR = 1.2            # a broken gap is drawn at least this much longer than the longest true-scale gap
DIM_LEVEL = 0.55                  # an earlier event's brightness while a later one is current (RevealSchedule.level)
TIMELINE_SPAN_RATIO = 0.8         # a 9:16 span label on its segment: at most this share of the labels' size


def span_band(label_px: int, gap_font) -> tuple[int, int]:
    """A 9:16 span label's own band between two entries (round 3 review, 2026-11-02): (the band's height, the air
    above and below the label), the air one label line (1.3 x the label size), so the span reads as the time
    between the two entries and not as a third line of the earlier label (14 px of air before)."""
    air = round(label_px * 1.3)
    return cap_height(gap_font) + 2 * air, air


def span_font_px(label_px: int) -> int:
    """The 9:16 on-segment span label's size: below the event labels' (TIMELINE_SPAN_RATIO), so the secondary
    elapsed time never reads larger or heavier than the white events it sits between; 24 px at least."""
    return max(24, min(label_px - 4, round(label_px * TIMELINE_SPAN_RATIO)))


@dataclass(frozen=True)
class TimelineSpacing:
    """Where each event sits on the spine and which gaps are drawn shorter than true scale (`breaks`: k for the
    gap between events k-1 and k, the same keys as the gap labels), with the px per year the honest gaps use."""
    positions: list[float]
    breaks: tuple[int, ...]
    px_per_year: float


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
            return TimelineSpacing(placed(steps), breaks, s)
    # Not even one true-scale gap fits: even steps, every gap longer than the shortest marked as compressed.
    step = span / n
    low = min(years)
    return TimelineSpacing([first + step * k for k in range(n + 1)],
                           tuple(k + 1 for k in range(n) if years[k] > low * 1.5), step / low)


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


def reveal_schedule(count: int, clip: float) -> RevealSchedule:
    """Landings spread over 8-66 % of the clip (0.5-4.0 s of 6 s), the restore at 76 % so the last frame holds."""
    if count < 2:
        return RevealSchedule([0.08 * clip], 0.5 * clip)
    step = (0.66 - 0.08) * clip / (count - 1)
    return RevealSchedule([0.08 * clip + step * k for k in range(count)], 0.76 * clip)


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
            im = pl.draw(im, a * (1 - back), dy)
        if back > 0.001:
            im = tw.draw(im, a * back, dy)
    return im


def _twin(ground: Image.Image, plate: Any, fill) -> Any:
    """A plate's stepped-back twin: the same words, font and place in `fill` (an earlier event while a later one is
    current), planned on the same ground so its contrast is measured too."""
    return plan_text(ground, plate.xy, plate.text, plate.font, fill, plate.anchor, track=plate.track)


def render_timeline(events: List[Dict[str, Any]], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                    photo: Path | None = None) -> Optional[Path]:
    """Timeline v2 (2026-11-02): a gold spine that draws on event by event, each date landing in turn at full
    brightness while the earlier ones dim (all full again for the held last frame); the events on a true time scale
    (timeline_positions), any gap too long to draw to scale cut with a break mark; the elapsed time (gap_text) on
    each gap; the topic's photo, blurred, desaturated and dark, as the ground (`photo`; plain ink without one).
    9:16: a vertical spine on the shared inset edge (INSET_X0) inside the Reel box; 16:9: across the frame."""
    events = sorted(events, key=lambda e: e["year"])[:4]
    if len(events) < 2:
        return None
    if width > height:
        return _render_timeline_wide(events, out, width, height, photo)
    return _render_timeline_tall(events, out, width, height, photo)


def _render_timeline_tall(events: list[dict[str, Any]], out: Path, width: int, height: int,
                          photo: Path | None) -> Path | None:
    """The 9:16 timeline: two events get the large years and two-line labels; more get smaller years and one-line
    labels fitted to the column (a label that cannot fit one line at its floor size takes two, never cut). Measured
    first: the last entry's label ends on the graphics box's bottom, and when the entries no longer fit between the
    bounds the type steps down."""
    n = int(CLIP_SECONDS * VIDEO_FPS)
    top, bottom = get_text_bounds(width, height)
    x0 = inset_x0(width, height) + round(16 * width / VIDEO_WIDTH)   # the spine: its dots' left edge on the inset
    xt = x0 + round(56 * width / VIDEO_WIDTH)
    label_w = TEXT_RIGHT * width // VIDEO_WIDTH - xt - 4
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    many = len(events) > 2
    spans = [gap_text(events[k - 1], events[k]) for k in range(1, len(events))]
    scales = (1.0, 0.92, 0.84, 0.76, 0.68, 0.6)

    def measure(scale: float, on_segment: bool):
        f_year = get_font("Anton", int((104 if many else 180) * scale))
        cap_y = cap_height(f_year)
        lab_px = int((38 if many else 42) * scale)
        entries = []
        texts = [drawable(e["label"].upper(), "Montserrat") for e in events]
        floor = max(26, round(lab_px * 0.78))
        if many:  # one size for every label: the smallest at which those that fit one line do; the rest take two
            fits = [fit_text(probe, tx, "Montserrat", lab_px, label_w - 2 * len(tx), 1, floor, sentences=False,
                             weight=800, quiet=True) for tx in texts]
            common = min([f[1].size for f in fits if not f[2]] or [floor])
        for e, text in zip(events, texts):
            if many:
                lines, f_lab, short = fit_text(probe, text, "Montserrat", common, label_w - 2 * len(text), 1, common,
                                               sentences=False, weight=800, quiet=True)
                if short:
                    lines, f_lab, _ = fit_text(probe, text, "Montserrat", common, label_w - 2 * len(text), 2, common,
                                               weight=800, quiet=True)
            else:
                lines, f_lab, _ = fit_text(probe, text, "Montserrat", lab_px, label_w - 2 * len(text), 2,
                                           max(22, int(lab_px * 0.8)), weight=800, quiet=True)
            f_lab = get_font("Montserrat", f_lab.size, weight=800)
            step = round(f_lab.size * 1.3)
            gap = round(f_lab.size * (1.4 if many else 1.6))
            e_top = -cap_y // 2              # offsets from the entry's dot: the year's top, the last row's ink bottom
            rows = [cap_y // 2 + gap + j * step for j in range(len(lines))]
            e_bottom = max(probe.textbbox((0, y), ln, font=f_lab, anchor="ls")[3] for y, ln in zip(rows, lines))
            entries.append({"year": year_text(e["year"], e.get("approximate", False)).upper(), "lines": lines,
                            "font": f_lab, "rows": rows, "top": e_top, "bottom": e_bottom})
        y_first = top + 30 - entries[0]["top"]
        y_last = bottom - entries[-1]["bottom"]                    # lifted so its last row ends on the bound
        # On its segment (round 3, 2026-11-02) the elapsed time takes its own band between the two entries, set
        # under the labels' size with a label line of air on each side (span_band); on the later year's baseline
        # (round 2) it takes no height.
        lab_min = min(en["font"].size for en in entries)
        f_gap = (get_font("Montserrat", span_font_px(lab_min), weight=600) if on_segment else
                 get_font("Montserrat", max(24, int((30 if many else 36) * scale)), weight=700))
        band = span_band(lab_min, f_gap)[0] if on_segment else 0
        needs = [entries[k]["bottom"] - entries[k + 1]["top"] + (band if band and spans[k] else 28)
                 for k in range(len(entries) - 1)]
        ok = (y_last - y_first) / (len(entries) - 1) >= max(needs)
        return ok, (f_year, cap_y, entries, y_first, y_last, f_gap, needs, band)

    def first_fit(on_segment: bool) -> tuple[int, tuple]:
        for j, sc in enumerate(scales):
            ok, m = measure(sc, on_segment)
            if ok:
                return j, m
        return len(scales) - 1, m
    j_base, m_base = first_fit(False)
    j_seg, m_seg = first_fit(True) if any(spans) else (len(scales), m_base)
    # The span on its own segment only when its band costs the type nothing (round 3 review, 2026-11-02: one step
    # down was allowed, and the events' labels shrank under their own spans); a column too tight for that keeps
    # the elapsed time on the later year's baseline, so the dates and labels stay at their size.
    on_segment = j_seg <= j_base
    scale = scales[min(j_seg if on_segment else j_base, len(scales) - 1)]
    f_year, cap_y, entries, y_first, y_last, f_gap, needs, band = m_seg if on_segment else m_base
    if scale < 1.0:
        log.info("Timeline type set at %d%% to keep %d entries above the caption band.", round(scale * 100), len(events))
    spacing = timeline_positions(events, y_first, y_last, needs)
    ground = timeline_backdrop(photo, width, height)
    plates: list[tuple[str, Any]] = []
    twins = []
    for k, en in enumerate(entries):
        y = spacing.positions[k]
        en["y"] = y
        en["year_plate"] = plan_text(ground, (xt, y + cap_y // 2), en["year"], f_year, GOLD, "ls")
        en["label_plates"] = [plan_text(ground, (xt + 4, y + r), ln, en["font"], WHITE, "ls", track=2)
                              for r, ln in zip(en["rows"], en["lines"])]
        en["dim_year"] = _twin(ground, en["year_plate"], brand.STONE)
        en["dim_labels"] = [_twin(ground, pl, brand.STONE_LT) for pl in en["label_plates"]]
        plates += [(f"year{k}", en["year_plate"])] + [(f"label{k}", p) for p in en["label_plates"]]
        twins += [(f"dim{k}", en["dim_year"])] + [(f"dim{k}_label", p) for p in en["dim_labels"]]
    # The time since the previous date: centred in the band between the two entries, on the segment it measures,
    # beside the spine (on_segment), else after the later year on its baseline ("1923  40 YEARS LATER"); "LATER"
    # dropped for the whole timeline when one wording does not fit the column.
    gaps = []
    col_right = TEXT_RIGHT * width // VIDEO_WIDTH
    bands = {k: (entries[k - 1]["y"] + entries[k - 1]["bottom"], entries[k]["y"] + entries[k]["top"])
             for k in range(1, len(entries))}

    def gap_xy(k: int) -> tuple[float, float]:
        if on_segment:
            a, b = bands[k]
            return xt, (a + b) / 2 + cap_height(f_gap) / 2
        return entries[k]["year_plate"].box[2] + 28, entries[k]["y"] + cap_y // 2

    def fits(k: int, text: str) -> bool:
        a, b = bands[k]
        room = b - a >= band - 1 if on_segment else True
        return room and gap_xy(k)[0] + tracked_width(text, f_gap, 2 if on_segment else 3) <= col_right
    wanted = [(k, t) for k, t in enumerate(spans, start=1) if t]
    if not all(fits(k, t) for k, t in wanted):
        wanted = [(k, t.removesuffix(" LATER")) for k, t in wanted]
    for k, text in wanted:
        if not fits(k, text):
            continue
        plate = plan_text(ground, gap_xy(k), text, f_gap, brand.STONE_LT, "ls", track=2 if on_segment else 3)
        gaps.append((k, plate))
        plates.append((f"gap{k}", plate))
    # each break mark on the spine in the clear band between the two entries it separates (between the dots if
    # there is none); the span label beside it is on the other side of the column
    marks = []
    for k in spacing.breaks:
        a, b = bands[k]
        marks.append((k, (a + b) / 2 if b - a >= 36 else (entries[k - 1]["y"] + entries[k]["y"]) / 2))
    sched = reveal_schedule(len(entries), CLIP_SECONDS)
    ys = [en["y"] for en in entries]
    u = width / VIDEO_WIDTH
    last: list[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = ground.copy()
            d = ImageDraw.Draw(im, "RGBA")
            head = sched.head(ys, t, ys[-1])
            d.line([(x0, y_first), (x0, head)], fill=(*brand.GOLD_DIM, 255), width=max(2, round(6 * u)))
            for k, y_mark in marks:
                if head >= y_mark + 12 * u:
                    _break_glyph(d, x0, y_mark, False, 30 * u, (*brand.STONE_LT, 255), 3 * u, ground.getpixel((x0, int(y_mark))))
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
                im = _focused(im, [en["year_plate"], *en["label_plates"]], [en["dim_year"], *en["dim_labels"]],
                              a, back, dy)
                d = ImageDraw.Draw(im, "RGBA")
            for k, plate in gaps:   # the elapsed time lands with its later year
                a = sched.appear(k, t)
                if a > 0:
                    im = plate.draw(im, a, round(14 * u * (1 - a)))
                    d = ImageDraw.Draw(im, "RGBA")
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)
    return result


# 16:9 words used to stay where the master's vignette left them legible (see _SECTION_WIDE): design x 400-1600,
# y 190-800. The scale and cross-section still keep to it; the timeline v2 runs across WIDE_TIMELINE.
WIDE_SAFE = (400, 190, 1600, 800)
WIDE_TITLE_SAFE = (153, 129, 1766, 820)   # the 16:9 title-safe box above the caption line, design px


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
    f_gap = get_font("Montserrat", g.f(26), weight=700)
    track = S(2)
    cap_y = cap_height(f_year) / g.k
    flag, pad, clear, gap_pad = 74.0, 22.0, 24.0, 30.0      # dot to block, flag to text, block to block, gap label
    texts = [drawable(e["label"].upper(), "Montserrat") for e in events]
    years = [year_text(e["year"], e.get("approximate", False)).upper() for e in events]
    year_w = [probe.textlength(y, font=f_year) / g.k for y in years]
    raw_gaps = [gap_text(events[k - 1], events[k]) for k in range(1, len(events))]
    for wording in ("LATER", "SHORT"):
        gap_texts = [t if wording == "LATER" else t.removesuffix(" LATER") for t in raw_gaps]
        gap_w = [tracked_width(t, f_gap, S(1)) / g.k if t else 0.0 for t in gap_texts]
        for label_max in (560, 500, 460, 420, 380, 340, 300, 270):
            blocks = []
            for k, text in enumerate(texts):
                lines, f_lab, short = fit_text(probe, text, "Montserrat", g.f(34), int(S(label_max) - 2 * len(text)),
                                               3, g.f(32), weight=800, quiet=True)
                if short:   # never cut: a fourth line at the floor size
                    lines, f_lab, _ = fit_text(probe, text, "Montserrat", g.f(32), int(S(label_max) - 2 * len(text)),
                                               4, g.f(32), weight=800, quiet=True)
                f_lab = get_font("Montserrat", f_lab.size, weight=800)
                lab_w = max(tracked_width(ln, f_lab, track) for ln in lines) / g.k
                blocks.append({"lines": lines, "font": f_lab, "w": pad + max(year_w[k], lab_w)})
            needs = [max(140.0, gw + 2 * gap_pad) for gw in gap_w]
            last_x = run1 - blocks[-1]["w"]
            spacing = None
            for _ in range(8):                      # same-side neighbours (k, k + 2) keep their 24 px
                spacing = timeline_positions(events, run0, last_x, needs)
                xs = spacing.positions
                short_by = [(k, blocks[k]["w"] + clear - (xs[k + 2] - xs[k])) for k in range(len(xs) - 2)]
                short_by = [(k, s) for k, s in short_by if s > 0.5]
                if not short_by:
                    break
                for k, s in short_by:
                    needs[k] += s / 2 + 1
                    needs[k + 1] += s / 2 + 1
            assert spacing is not None
            xs = spacing.positions
            ok = (all(xs[k + 1] - xs[k] >= needs[k] - 1 for k in range(len(xs) - 1))
                  and all(xs[k + 2] - xs[k] >= blocks[k]["w"] + clear - 1 for k in range(len(xs) - 2)))
            if ok:
                break
        if ok:
            break
    ground = timeline_backdrop(photo, width, height)
    plates: list[tuple[str, Any]] = []
    entries = []

    def block_h(b: dict[str, Any]) -> float:
        return (cap_y + 28 + cap_height(b["font"]) / g.k
                + round(b["font"].size * 1.3) / g.k * (len(b["lines"]) - 1))
    # One baseline grid per side (round 3, 2026-11-02): every year above the spine on one baseline, every year
    # below on another, each side's flag sized for its tallest block (a 4-line label lifted its year 80 px).
    side_h = {side: max(block_h(b) for k, b in enumerate(blocks) if (k % 2 == 0) == side) for side in (True, False)
              if any((k % 2 == 0) == side for k in range(len(blocks)))}
    for k, (b, x) in enumerate(zip(blocks, xs)):
        f_lab = b["font"]
        cap_l = cap_height(f_lab) / g.k
        step = round(f_lab.size * 1.3) / g.k
        h = block_h(b)
        above = k % 2 == 0
        top = spine_y - flag - side_h[above] if above else spine_y + flag
        tx = x + pad
        en = {"k": k, "x": x, "above": above, "top": top, "bottom": top + h,
              "far": top if above else top + side_h[above]}
        en["year_plate"] = plan_text(ground, (X(tx), Y(top + cap_y)), years[k], f_year, GOLD, "ls")
        en["label_plates"] = [plan_text(ground, (X(tx + 2), Y(top + cap_y + 28 + cap_l + step * j)), ln, f_lab, WHITE,
                                        "ls", track=track) for j, ln in enumerate(b["lines"])]
        en["dim_year"] = _twin(ground, en["year_plate"], brand.STONE)
        en["dim_labels"] = [_twin(ground, pl, brand.STONE_LT) for pl in en["label_plates"]]
        plates += [(f"year{k}", en["year_plate"])] + [(f"label{k}", pl) for pl in en["label_plates"]]
        entries.append(en)
    gaps = []
    for k, text in enumerate(gap_texts, start=1):
        if not text or tracked_width(text, f_gap, S(1)) / g.k > xs[k] - xs[k - 1] - 2 * 26:
            continue
        plate = plan_text(ground, (X((xs[k - 1] + xs[k]) / 2), Y(spine_y - 26)), text, f_gap, brand.STONE_LT, "ms",
                          track=S(1))
        gaps.append((k, plate))
        plates.append((f"gap{k}", plate))
    marks = [(k, (xs[k - 1] + xs[k]) / 2) for k in spacing.breaks]
    end = max(xs[-1] + blocks[-1]["w"], xs[-1] + 60)
    sched = reveal_schedule(len(entries), CLIP_SECONDS)
    last: list[Image.Image] = []
    tail = 70.0                                   # the spine runs on past the last date, fading out

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = ground.copy()
            d = ImageDraw.Draw(im, "RGBA")
            head = sched.head(xs, t, end)
            lw = max(2, round(S(6)))
            solid = min(head, xs[-1])
            d.line([(X(xs[0]), Y(spine_y)), (X(solid), Y(spine_y))], fill=(*brand.GOLD_DIM, 255), width=lw)
            if head > xs[-1]:                     # the run past the last date fades into the ground
                span = head - xs[-1]
                for j in range(12):
                    a0 = xs[-1] + span * j / 12
                    a1 = xs[-1] + span * (j + 1) / 12
                    fade = 1 - (a0 - xs[-1]) / max(1.0, end - xs[-1])
                    if a1 > end - tail:
                        fade *= max(0.0, (end - a1) / tail)
                    d.line([(X(a0), Y(spine_y)), (X(a1), Y(spine_y))], fill=(*brand.GOLD_DIM, int(255 * fade)), width=lw)
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
                im = _focused(im, [en["year_plate"], *en["label_plates"]], [en["dim_year"], *en["dim_labels"]],
                              a, back, dy)
                d = ImageDraw.Draw(im, "RGBA")
            for k, plate in gaps:
                a = sched.appear(k, t)
                if a > 0:
                    im = plate.draw(im, a)
                    d = ImageDraw.Draw(im, "RGBA")
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)
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


def _subject_name(fact: dict[str, Any]) -> str:
    """The payload subject without a trailing dimension word ("KAILASA TEMPLE MONOLITH HEIGHT" -> "... MONOLITH"):
    only the payload's own words, so the comparative names what it compares."""
    words = str(fact.get("subject") or "").upper().split()
    while words and words[-1] in ("HEIGHT", "DEPTH", "TALL", "DEEP"):
        words.pop()
    return " ".join(words)


def scale_meaning(fact: dict[str, Any], feet: float, ref_label: str, ref_feet: float, depth: bool) -> list[str]:
    """The scale graphic's line of meaning, from the subject's and the landmark's sourced figures only. A height
    (round 3, 2026-11-02) says which is taller and names both ("ABOUT 2.9 TIMES TALLER" had no referent and read
    as n + 1): "STATUE OF LIBERTY: 305 FT." / "ABOUT 3 TIMES AS TALL AS KAILASA TEMPLE." (the landmark taller),
    "KAILASA TEMPLE IS ABOUT 3 TIMES AS TALL." (the subject taller), the ratio from comparison_ratio."""
    hedge = "ABOUT " if fact.get("approximate", True) else ""
    ref_name = ref_label.split(" (")[0]
    gap = abs(ref_feet - feet)
    if depth:
        return ([f"THE {ref_name.upper()}, {_short(ref_feet)},",
                 f"WOULD SHOW ONLY ITS TOP {hedge}{_short(gap)}."] if ref_feet > feet else
                [f"THE {ref_name.upper()} WOULD VANISH:",
                 f"ITS TOP {hedge}{_short(gap)} BELOW THE SURFACE."])
    first = f"{ref_name.upper()}: {_short(ref_feet)}."
    if max(feet, ref_feet) / min(feet, ref_feet) < 1.1:
        return [first, "NEARLY THE SAME HEIGHT."]
    times = f"ABOUT {comparison_ratio(max(feet, ref_feet), min(feet, ref_feet))} TIMES AS TALL"
    subject = _subject_name(fact)
    if not subject:
        return [first, f"{times}."]
    return [first, f"{times} AS {subject}." if ref_feet > feet else f"{subject} IS {times}."]


SCALE_MEANING_PX = (36, 34)   # the 9:16 line of meaning's size and floor (round 3: it was the smallest text, 30-22)
SCALE_RULER_PX = 26           # the depth ruler's data-face figures on 9:16


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
    meaning = scale_meaning(fact, feet, ref_label, ref_feet, depth)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    wide = box_x1 - x0 - 8
    f_sub = fit_font(probe, subject, "Montserrat", 30, wide, 22)
    f_sub = get_font("Montserrat", f_sub.size, weight=800)
    f_met = get_font(brand.DATA_FONT, 34, weight=600)
    f_fig = fit_font(probe, figure, "Anton", 76, int(wide), 48)
    mean_lines, f_mean = [], None
    for px in range(SCALE_MEANING_PX[0], SCALE_MEANING_PX[1] - 1, -1):
        f_mean = get_font("Montserrat", px, weight=700)
        mean_lines = [ln for text in meaning for ln in fit_text(probe, text, "Montserrat", px, int(wide - len(text)), 3,
                                                                px, sentences=False, weight=700, quiet=True)[0]]
        if len(mean_lines) <= len(meaning) + 1:
            break
    mean_step = int(cap_height(f_mean) * 1.6)
    y_sub = top + 10 + cap_height(f_sub)
    y_fig = y_sub + 24 + cap_height(f_fig)
    y_met = y_fig + 16 + cap_height(f_met) if metric else y_fig
    # The meaning's last baseline on the box's foot (it sat a whole line step, ~15 px, above it), so the drawing
    # keeps its height (round 5 review, 2026-11-02); 4 px for a comma or Q tail.
    y_mean0 = bottom - 4 - (len(mean_lines) - 1) * mean_step
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
    plates += [(f"meaning{k}", plan_text(ground, (xt, y_mean0 + k * mean_step), t, f_mean, WHITE, "ls", track=1))
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
        block = 190
        room = x1 - x0 - 60 - block - 40                    # beside the subject's block
        shaft = (x0 + 30, x0 + 30 + block)
        f_blk = get_font("Montserrat", 28, weight=800)
        blk_text = hedge + _short(feet)                    # hedged like the figure: never more precise
        ref_txt_w = tracked_width(_short(ref_feet), f_blk, 2)
        # Each part keeps SCALE_TEXT_GAP under the heading lines over its own columns (heading_clear; round 5
        # review, 2026-11-02: a flat 40 px reserve under the metric line took 13 % off the landmark): the block's
        # label, the landmark's top and its figure's label.
        c_blk = heading_clear(heads, shaft[0] - 30, shaft[1] + 30, top) + 16 + cap_height(f_blk)
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
    t_drop_end = 2.6
    t_bracket = t_drop_end + 0.25
    last: list = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
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
                rise = _ease((t - 0.3) / 1.2)
                hs = feet * px_ft * rise
                d.rectangle([shaft[0], base_y - hs, shaft[1], base_y], fill=(*GOLD, 255))
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
            for name, plate in plates:
                at = {"subject": 0.1, "figure": 0.4, "figure_metric": 0.5, "block_label": 1.5,
                      "ref_label": 2.4, "bracket": t_bracket + 0.2}.get(name)
                if at is None:
                    at = 0.5 + 0.1 * int(name[5:]) if name.startswith("ruler") else 3.2
                a = _ease((t - at) / 0.4)
                if a > 0:
                    im = plate.draw(im, a, round(10 * (1 - a)))
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)
    return result


def _render_scale_wide(fact: dict[str, Any], feet: float, ref_label: str, ref_feet: float, depth: bool, out: Path,
                       width: int, height: int) -> Path | None:
    """16:9 scale (see render_scale): the drawing in the left half of the vignette-safe area (WIDE_SAFE), a depth
    as a shaft drilled from the ground with the landmark standing on its floor, a height as a gold block rising
    from the ground beside the landmark; the subject, its figure and the meaning in a column at the right."""
    g = _WideGrid(width, height)
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)
    _sx0, sy0, sx1, sy1 = WIDE_SAFE
    dx0, dx1 = WIDE_TITLE_SAFE[0] + 80, 1000.0   # the drawing (round 3: from title-safe, the master lifts its
    #                                              vignette under graphics; it kept to WIDE_SAFE's x 400 before)
    cx0, cx1 = 1090.0, sx1                    # the column of words
    hedge = "ABOUT " if fact.get("approximate", True) else ""
    subject = str(fact.get("subject") or "").upper()
    figure, metric = scale_figure(fact, feet, depth)
    meaning = scale_meaning(fact, feet, ref_label, ref_feet, depth)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    col = S(cx1 - cx0) - 8
    sub_lines, f_sub, _ = fit_text(probe, subject, "Montserrat", g.f(32), int(col - 3 * len(subject)), 2, g.f(28),
                                   sentences=False, weight=800, quiet=True)
    f_sub = get_font("Montserrat", f_sub.size, weight=800)
    fig_lines, f_fig, _ = fit_text(probe, figure, "Anton", g.f(96), int(col), 2, g.f(64), sentences=False, quiet=True)
    fits = [fit_text(probe, text, "Montserrat", g.f(36), int(col - len(text)), 2, g.f(32), sentences=False,
                     weight=700, quiet=True) for text in meaning]
    f_mean = get_font("Montserrat", min(f.size for _, f, _ in fits), weight=700)
    mean_lines = [ln for text in meaning for ln in fit_text(probe, text, "Montserrat", f_mean.size, int(col - len(text)),
                                                            3, f_mean.size, sentences=False, weight=700, quiet=True)[0]]
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
        rows.append((f"meaning{j}", ln, f_mean, WHITE, S(1), y))
    block_h = y
    col_top = (sy0 + sy1) / 2 - block_h / 2
    ground = Image.new("RGB", (width, height), SEA)
    width_per_px = _silhouette_width(ref_label)
    top_d, draw_bottom = sy0 + 20, sy1 - 10
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
        room = dx1 - dx0 - 60 - block - 60
        px_ft = min((base_y - top_d) / max(feet, ref_feet), room / max(1.0, ref_feet * width_per_px))
        shaft = (dx0 + 40, dx0 + 40 + block)
        ref_cx = shaft[1] + 60 + max(room, ref_feet * px_ft * width_per_px) / 2

    # The ground plane ends inside the frame on both sides (round 3: it ran off x 0 into the dark and stopped
    # hard at x 1000 mid-frame), from title-safe to just short of the column of words.
    gx0, gx1 = WIDE_TITLE_SAFE[0], cx0 - 50
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
        bracket = (tick_x, ref_top, ref_top)

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
                u = (t - 2.6) / 0.15                                 # a 2.5 % squash and settle as it lands
                hh *= 1 - 0.025 * math.sin(math.pi * u) if 0 < u < 1 else 1
                base = floor_y - (1 - drop) * (floor_y - line_y + hh + 40)
                _silhouette(d, ref_label, X(ref_cx), Y(base), S(hh), brand.STONE_LT)
            d.rectangle([X(gx0), Y(line_y) - S(2), X(gx1), Y(line_y) + S(1)], fill=(*WHITE, 255))
            ab = _ease((t - 2.85) / 0.4)
            if bracket and ab > 0 and bracket[2] - bracket[1] >= 6:
                bx, b0, b1 = bracket
                y_a = b0 + (b1 - b0) * (1 - ab) if b1 == line_y else b0
                y_b = b1 if b1 == line_y else b0 + (b1 - b0) * ab
                _bracket(d, X(bx), Y(y_a), Y(y_b), S(12), lw, (*GOLD, 255))
        else:
            d.rectangle([X(gx0), Y(base_y), X(gx1), Y(base_y) + S(3)], fill=(*WHITE, 255))
            rise = _ease((t - 0.3) / 1.2)
            hs = feet * px_ft * rise
            d.rectangle([X(shaft[0]), Y(base_y - hs), X(shaft[1]), Y(base_y)], fill=(*GOLD, 255))
            rr = _ease((t - 1.2) / 1.2)
            if rr > 0:
                _silhouette(d, ref_label, X(ref_cx), Y(base_y), S(ref_feet * px_ft * rr), brand.STONE_LT)
            ab = _ease((t - 2.4) / 0.4)
            if bracket and ab > 0 and "ref_label" in shown:          # the landmark's hairline tick
                d.line([(X(bracket[0]), Y(bracket[1])), (X(bracket[0] + 26 * ab), Y(bracket[1]))],
                       fill=(*brand.STONE_LT, 255), width=max(1, round(S(2))))
        return im

    shown: set[str] = {"ref_label"}
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
    last: list[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = drawing(t)
            a_rule = _ease((t - 0.6) / 0.4)
            if a_rule > 0:
                ImageDraw.Draw(im, "RGBA").rectangle(rule, fill=(*GOLD, int(255 * a_rule)))
            for name, plate in plates:
                at = (0.1 if name.startswith("subject") else 0.4 if name.startswith("figure") else
                      0.5 + 0.1 * int(name[5:]) if name.startswith("ruler") else
                      {"block_label": 1.5, "ref_label": 2.5, "bracket": 3.05}.get(name, 3.2))
                a = _ease((t - at) / 0.4)
                if a > 0:
                    im = plate.draw(im, a, round(S(10) * (1 - a)))
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates)
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
        num_w = sum(w for _, w in _cells(d, num, f_num)) if num else 0
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
            num_w = sum(w for _, w in _cells(d, num, f_num))
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
    cards = [dict(c, value=figure_text(spoken_units(str(c.get("value") or ""), narration)),
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
    bg = scrim(bg, y0b, y1b, feather=380, target=stat_scrim_target(bg, (x_text, y0b, x_text + text_w, y1b))).convert("RGB")
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


def _stat_clip(cards: list[dict[str, Any]], plans: list[dict[str, Any]], bg: Image.Image, out: Path, n: int,
               width: int, height: int) -> Path | None:
    """The number cards' clip (both orientations): each line planned on the resting frame at a measured contrast
    (a figure one plate per character, recorded as one), the figure counting up on fixed cells, the push-in."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    rest = _pushed_in(bg, push_in((n - 1) / VIDEO_FPS, CLIP_SECONDS))
    plates: List[Tuple[str, Any]] = []
    for p in plans:   # each line planned on the resting frame: its halo, and for a figure one plate per character
        x0 = p["x0"]
        for r in p["rows"]:
            yy = p["y0"] + r["y"]
            if r["kind"] == "kicker":
                bx0, by0, bx1, by1 = r.get("bar", (0, 0, 44, 4))
                r["rule"] = (x0 + bx0, p["y0"] + by0, x0 + bx1, p["y0"] + by1)
                r["plates"] = [plan_text(rest, (x0, yy), r["text"], r["font"],
                                         GOLD, "ls", track=r["font"].size * 0.15)]
            elif r["kind"] == "figure":
                x, r["plates"] = x0, []
                for c, w in _cells(probe, r["text"], r["font"]):
                    cx = x + w / 2
                    r["plates"].append(plan_text(rest, (cx, yy), c, r["font"], GOLD, "ms", halo_px=25))
                    x += w
                r["x_end"] = x
            elif r["kind"] == "count":
                r["plates"] = [plan_text(rest, (x0, yy), r["text"], r["font"], r["fill"], "ls", track=r.get("track", 0))]
                r["x_end"] = x0 + probe.textlength(r["text"], font=r["font"])
            else:
                r["plates"] = [plan_text(rest, (x0 + r.get("x", 0), yy), r["text"], r["font"], r["fill"], "ls",
                                         track=r.get("track", 0))]
            if r["kind"] == "figure":
                plates.append((r["name"], _JoinedPlate(r["plates"], rest, r["text"])))
            else:
                plates += [(r["name"], pl) for pl in r["plates"]]
    last: List[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = _pushed_in(bg, push_in(t, CLIP_SECONDS))  # a slow push on the photo: a card never sits static
            for k, (card, p) in enumerate(zip(cards, plans)):
                start = count_start(k)  # every count rests by count_done_by of the clip
                a = _ease((t - start) / MOTION["reveal"])
                if a <= 0:
                    continue
                count = _ease((t - start) / MOTION["count"])
                fig = next((r["text"] for r in p["rows"] if r["kind"] == "figure"), "")
                shown_q = shown_fraction(fig, count) if fig else count   # the bracketed figure follows it
                dy = round(14 * (1 - a))
                for r in p["rows"]:
                    yy = p["y0"] + r["y"] + dy
                    if r["kind"] == "figure":
                        for pl in r["plates"]:
                            im = pl.draw_halo(im, a, dy)
                        shown = _shown_value(r["text"], count)
                        d = ImageDraw.Draw(im, "RGBA")
                        x = r["x_end"] - sum(w for _, w in _cells(d, shown, r["font"]))
                        for c, w in _cells(d, shown, r["font"]):   # right-aligned on the final figure's cells
                            _text(d, (x + w / 2, yy), c, r["font"], (*GOLD, int(255 * a)), anchor="ms")
                            x += w
                    elif r["kind"] == "count":
                        pl = r["plates"][0]
                        im = pl.draw_halo(im, a, dy)
                        d = ImageDraw.Draw(im, "RGBA")
                        _text(d, (r["x_end"], yy), _shown_value(r["text"], shown_q), r["font"], (*r["fill"], int(255 * a)),
                              anchor="rs")
                    else:
                        if r["kind"] == "kicker":
                            x0, y0, x1, y1 = r.get("rule", r.get("bar", (p["x0"], yy, p["x0"] + 44, yy + 3)))
                            ImageDraw.Draw(im, "RGBA").rectangle([x0, y0 + dy, x1, y1 + dy], fill=(*GOLD, int(255 * a)))
                        for pl in r["plates"]:
                            im = pl.draw(im, a, dy)
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
    cards = [dict(c, value=figure_text(spoken_units(str(c.get("value") or ""), narration)),
                  label=spoken_units(str(c.get("label") or ""), narration)) for c in cards[:2]]
    if not cards:
        return None
    n = int(CLIP_SECONDS * VIDEO_FPS)
    bg = _backdrop(photo, width=width, height=height)
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
    for j, p in enumerate(plans):       # each block centred in its column, the figures on one baseline
        left = bx0 + j * (col_w + gutter)
        p["x0"] = round(left + (col_w - p["width"]) / 2)
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
_SECTION_WIDE = _SectionLayout(
    title_x=420, title_w=760, title_base=252, title_step=72, title_px=(68, 56), ruler_x=488, shaft_x=566,
    shaft_w=28, rooms_x1=1110, label_x=1200, right=1600, bottom_d=790, axis_limit=812, label_hi=818,
    surface_gap=380, fig_px=54, deep_px=80, lab_px=34, small_px=28, line_step=42, label_gap=44, fan_gap=20,
    room_start=(12, 26), room_w=(70, 120), room_gap=(18, 36), niche_w=(46, 62), room_h_max=58,
    turns=(900, 1076, 988), bends=(1130, 1160, 1186), approx_inline=False, ruler_fill=brand.BONE,
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
        ramp, landings = [], []
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
                if j < legs - 1:                                     # a landing at each switchback
                    lw = rnd.uniform(34, 50)
                    lx = xb - lw * 0.75 if xb < mid_turn else xb - lw * 0.25
                    landings.append((lx, lx + lw, yb + 3, rnd.uniform(18, 26), 100 * k + 70 + j, 2.4))
                xa = xb
        plan.append({"floor": floor, "rooms": rooms + landings, "passage_h": passage_h, "ramp": ramp,
                     "end": max(r[1] for r in rooms) if rooms else shaft_x + 40,
                     "reach": max((r[1] for r in rooms if r[0] > shaft_x), default=shaft_x + 40)})
        prev_floor, prev_rooms = floor, rooms

    carve_box = (X(ruler_x + 8), Y(ground_d - 6), X(rooms_x1 + 8), Y(bottom_d + 14))

    def paint_upto(last: int):
        def paint(pen: _Pen) -> None:
            pen.rect(X(shaft_x - shaft_w / 2), Y(ground_d - 2), X(shaft_x + shaft_w / 2), Y(bottom_d), fill=255)
            pen.ellipse(X(shaft_x), Y(bottom_d - 12), S(24), S(17), fill=255)          # the shaft's foot
            for p in plan[:last + 1]:
                fl = p["floor"]
                pen.poly(g.pts([(shaft_x, fl - p["passage_h"]), (p["reach"] - 8, fl - p["passage_h"] - 2),
                                (p["reach"] - 8, fl - 2), (shaft_x, fl)]), fill=255)
                for x0, x1, f0, h, seed, flat in p["rooms"]:
                    pen.poly(g.pts(_vault(x0, x1, f0, h, seed, flat)), fill=255)
                if p["ramp"]:
                    pen.line(g.pts(p["ramp"]), 255, S(7))
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
    lantern = _lantern(S(10))
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
        if ly > ground_d:
            d.rectangle([X(shaft_x) - S(3), Y(ground_d - 2), X(shaft_x) + S(3), Y(ly)], fill=(*accent(), 255))
        for k in range(len(levels)):
            leaders[k].draw(im, 1.0 if final else _ease((t - arrive[k] - 0.05) / 0.35))
            registers[k].draw(im, 1.0 if final else _ease((t - arrive[k] + 0.05) / 0.3))
        lantern.draw(im, a_shaft, X(shaft_x), Y(ly))
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
        t_in = arrive[k] - 0.05
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

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
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
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], [(name, dim if dim is not None else plate) for name, plate, _a, _b, dim in timed])
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
    for s in stops[:5]:
        p = geo.locate({"location": {"name": s["place"], "country": s["country"]}})
        if p:
            located.append({**p, "label": s["label"]})
    if len(located) < 2:
        return None
    lats, lons = [p["lat"] for p in located], [p["lon"] for p in located]
    lat0, lon0 = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    coslat = max(0.2, math.cos(math.radians(lat0)))
    scale = 2
    bw, bh = width * scale, height * scale
    top, bottom = get_text_bounds(width, height)
    sx0, sy0, sx1, sy1 = get_text_box(width, height)
    # Fit every stop inside the middle of the graphics box (62 % of its width, y between the text bounds).
    span_lon = max((max(lons) - min(lons)) * coslat / (0.62 * (sx1 - sx0) / width), 6.0)
    span_lat = max((max(lats) - min(lats)) / ((bottom - top - 100) / height), 6.0)
    kx = min(bw / span_lon, bh / span_lat)
    # The stops centred on the graphics box, not the frame (round 3: on 9:16 the frame's centre is below the
    # box's, and a southern stop fell under the caption band): the view's centre moved to match.
    lat_c, lon_c = lat0, lon0
    lat0 = lat_c - (bh / 2 - (top + bottom) / 2 * scale) / kx
    lon0 = lon_c + (bw / 2 - (sx0 + sx1) / 2 * scale) / (kx * coslat)

    def project(lon: float, lat: float) -> Tuple[float, float]:
        return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx

    portrait = height > width
    bar = scale_bar(lat_c, kx / scale, narration)
    bar_x = (M if portrait else sx0 + 10)
    bar_y = (TEXT_BOTTOM - 22 if portrait else sy1 - 30)
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
    f_lab = get_font("Montserrat", 30 if width > height else 34, weight=800)
    label_w = 380
    plates: list[tuple[str, Any]] = []
    leaders: list[tuple[tuple[float, float], tuple[float, float]] | None] = []
    taken: list[tuple[float, float, float, float]] = [(x - 26, y - 26, x + 26, y + 26) for x, y in pts]
    for leg in legs:                                 # and no name over the route itself
        for (xa, ya), (xb, yb) in itertools.pairwise(leg):
            for f in (0.0, 0.5):
                x, y = xa + (xb - xa) * f, ya + (yb - ya) * f
                taken.append((x - 9, y - 9, x + 9, y + 9))
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for k, ((x, y), p) in enumerate(zip(pts, located)):
        placed = None
        for max_lines in (2, 3):
            lines, f_fit = fit_lines(probe, p["label"].upper(), f_lab, label_w, max_lines)
            step = round(f_fit.size * 1.22)
            block = step * (len(lines) - 1) + cap_height(f_fit)
            # beside the stop on its open side, then the other side, then above and below it, then stepped off
            sides = [("r" if x < width / 2 else "l"), ("l" if x < width / 2 else "r"), "a", "b"]
            for dy in (0, -1, 1, -2, 2):
                for side in sides:
                    if side in "rl":
                        tx, anchor = (x + 34, "ls") if side == "r" else (x - 34, "rs")
                        y_top = y - block / 2 + dy * (block + 16)
                    else:
                        if dy:
                            continue
                        tx, anchor = x, "ms"
                        y_top = y - 34 - block if side == "a" else y + 34
                    trial = [plan_text(rest, (tx, y_top + step * j + cap_height(f_fit)), ln, f_fit, WHITE, anchor,
                                       track=2, halo_px=21, min_halo=0.7) for j, ln in enumerate(lines)]
                    boxes = [pl.box for pl in trial]
                    inside = all(sx0 <= b[0] and b[2] <= sx1 and sy0 <= b[1] and b[3] <= sy1 for b in boxes)
                    clear = not any(LayoutConstraintSolver.boxes_intersect(b, o, 8) for b in boxes for o in taken)
                    if inside and clear:
                        placed = (trial, dy, tx)
                        break
                if placed:
                    break
            if placed:
                break
        if placed is None:
            leaders.append(None)
            log.info("Route: no clear place for the label of stop %d (%s); the stop is drawn unlabelled.", k,
                     p["label"])
            continue
        trial, dy, tx = placed
        taken += [pl.box for pl in trial]
        plates += [(f"stop{k}", pl) for pl in trial]
        leaders.append(((x, y), (tx, (trial[0].box[1] + trial[-1].box[3]) / 2)) if dy else None)
    words = tuple((b[0] * scale - 20, b[1] * scale - 20, b[2] * scale + 20, b[3] * scale + 20)
                  for b in [pl.box for _, pl in plates] + [_leader_box(ld) for ld in leaders if ld])
    base = relief_base(lat0, lon0, bw, bh, kx, coslat, None, project, (0, 0, bw, bh),
                       label_bottom_fraction=bottom / height,
                       reserved=(((bar_x - 20) * scale, (bar_y - 70) * scale, (bar_x + bar["px"] + 260) * scale,
                                  (bar_y + 30) * scale),) + corridor + words,   # no name under the route or a stop's
                       final_safe=(sx0 * scale, sy0 * scale, sx1 * scale, sy1 * scale)).resize((width, height),
                                                                                              Image.LANCZOS)
    rest = base.convert("RGB")
    plates = [(name, plan_text(rest, pl.xy, pl.text, pl.font, pl.fill, pl.anchor, track=pl.track, halo_px=21,
                               min_halo=0.7)) for name, pl in plates]
    stop_of = {id(pl): int(name[4:]) for name, pl in plates}
    bar_plate = plan_text(rest, (bar_x + bar["px"] + 22, bar_y + 13), bar["label"],
                          get_font(brand.DATA_FONT, 38, weight=600), WHITE, "ls", track=1, min_halo=0.6, halo_px=17)
    t_bar = arrive[-1] + 0.3
    last: list[Image.Image] = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = base.convert("RGB")
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
            for k, (x, y) in enumerate(pts):
                a = _ease((t - arrive[k] + 0.1) / 0.3)
                if a <= 0:
                    continue
                d.ellipse([x - 16, y - 16, x + 16, y + 16], fill=(*accent(), int(255 * a)),
                          outline=(*brand.INK, int(255 * a)), width=4)
                lead = leaders[k] if k < len(leaders) else None
                if lead and a > 0.5:
                    (lx, ly), (tx, ty) = lead
                    d.line([(lx, ly), (tx, ty)], fill=(*WHITE, int(220 * a)), width=2)
            if head is not None:                     # the drawing head
                d.ellipse([head[0] - 9, head[1] - 9, head[0] + 9, head[1] + 9], fill=(*WHITE, 255),
                          outline=(*brand.INK, 255), width=3)
            for name, pl in plates:
                k = stop_of[id(pl)]
                a = _ease((t - arrive[k] - 0.05) / 0.35)
                if a > 0:
                    im = pl.draw(im, a, round(8 * (1 - a)))
            ab = _ease((t - t_bar) / 0.3)
            if ab > 0:
                d = ImageDraw.Draw(im, "RGBA")
                x1 = bar_x + bar["px"] * ab
                d.line([(bar_x, bar_y), (x1, bar_y)], fill=(*brand.INK, 200), width=9)
                d.line([(bar_x, bar_y), (x1, bar_y)], fill=(*WHITE, 255), width=5)
                for xx, tall in ((bar_x, 16), (bar_x + bar["px"] / 2, 9), (bar_x + bar["px"], 16)):
                    if xx <= x1 + 0.5:
                        d.line([(xx, bar_y - tall), (xx, bar_y + 2)], fill=(*WHITE, 255), width=5)
                im = bar_plate.draw(im, _ease((t - t_bar - 0.2) / 0.3))
            if i == n - 1:
                last.append(im.copy())
            yield im

    result = _encode(frames(), out, n, width=width, height=height)
    if result and last:
        record_contrast(out, last[0], plates + [("scale", bar_plate)])
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


# ----------------------------------------------- LiDAR subterranean radar scan
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
