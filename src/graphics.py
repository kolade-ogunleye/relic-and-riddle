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
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from . import geo
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .typography import (cap_height, drawable, fit_font, fit_lines, fit_text, get_font, plan_text,
                         spoken_system, spoken_units, tracked_width)
from .art_direction import accent, type_scale, count_start, push_in, MOTION
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
# (video_composer.film_grade_filter, noise and vignette), with the glyph masks it was drawn with. The planned and
# graded ratios of each element are kept beside the clip (<clip>.contrast.json) for video_qa.graphic_contrast_problems.
CONTRAST_SIDECAR = ".contrast.json"


def graded_frame(frame: Image.Image, work: Path) -> Optional[Image.Image]:
    """`frame` through the master's picture chain (format, hue-safe film print, noise, vignette); None when ffmpeg
    cannot run here (the planned ratios are still recorded)."""
    try:
        from .video_composer import film_grade_filter
        work.mkdir(parents=True, exist_ok=True)
        src, dst = work / "_contrast_in.png", work / "_contrast_out.png"
        frame.convert("RGB").save(src)
        aspect = "16/9" if frame.width > frame.height else "9/16"
        vf = (f"format=yuv420p,{film_grade_filter(0.0, out_dir=work)},format=yuv420p,"
              f"noise=alls=8:allf=t+u:all_seed=7,vignette=PI/4.2:aspect={aspect},format=rgb24")
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
    sea_rgb = sea_rgb + np.array((40, 62, 84), dtype=np.float32) * np.clip(glow - land, 0, 1) * 0.9
    out = sea_rgb * (1 - land) + land_rgb * land * (1 - homea) + home_rgb * homea
    base = Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")

    draw = ImageDraw.Draw(base, "RGBA")
    for c in sorted(visible, key=lambda c: c is home):  # borders crisp on top; the home outline last, in gold
        for poly in c["polygons"]:
            ring = poly[0]
            if any(abs(x - lon0) < lon_half and abs(y - lat0) < lat_half for x, y in ring[:: max(1, len(ring) // 50)]):
                colour, width = ((*accent(),), 7) if c is home else ((150, 150, 158), 3)
                draw.line([project(x, y) for x, y in ring] + [project(*ring[0])], fill=colour, width=width)

    # Neighbouring countries, named at Natural Earth's label points. A label is drawn only if it
    # collides with no other label and is either wholly inside the final close-up or wholly
    # outside it, so the last frame never shows half a name.
    font = get_font("Montserrat", 66)
    fx0, fy0, fx1, fy1 = final_view
    placed: List[Tuple[float, float, float, float]] = list(reserved)  # e.g. the pin and place label
    for c in sorted(visible, key=lambda c: (c["label_rank"], -c["size"])):
        # Areas of indeterminate status (e.g. Siachen Glacier) are not countries and stay unnamed.
        if c is home or not c["label_at"] or c["label_rank"] > 5 or c.get("type") == "Indeterminate":
            continue
        x, y = project(*c["label_at"])
        name = " ".join(c["display"].upper())  # letter-spaced small caps
        l, t, r, b = draw.textbbox((x, y), name, font=font, anchor="mm")
        if c.get("extent"):  # a name much wider than its land reads as naming something else
            ex0, _ = project(c["extent"][0], c["extent"][3])
            ex1, _ = project(c["extent"][2], c["extent"][1])
            if abs(ex1 - ex0) < 0.6 * (r - l):
                continue
        box = (l - 30, t - 20, r + 30, b + 20)
        if box[0] < 0 or box[1] < 0 or box[2] > bw or box[3] > bh:
            continue
        # Reserve the subtitle band throughout both the wide view and final zoom.
        label_bottom = min(bh * label_bottom_fraction, fy0 + (fy1 - fy0) * label_bottom_fraction)
        if box[3] > label_bottom:
            continue
        m = 60  # clear of the close-up's edge, so the last frame shows no name touching it
        inside = fx0 + m <= box[0] and box[2] <= fx1 - m and fy0 + m <= box[1] and box[3] <= fy1 - m
        if inside and final_safe is not None:  # and inside the Reel's safe box on the last frame (so on every frame)
            inside = (final_safe[0] <= box[0] and box[2] <= final_safe[2]
                      and final_safe[1] <= box[1] and box[3] <= final_safe[3])
        outside = box[2] < fx0 or box[0] > fx1 or box[3] < fy0 or box[1] > fy1
        if not (inside or outside or transient is not None) or any(not (box[2] < p[0] or box[0] > p[2] or box[3] < p[1] or box[1] > p[3]) for p in placed):
            continue
        placed.append(box)
        if not inside and transient is not None:
            # A name not wholly inside the close-up's safe box is crossed by the frame's edge as the camera
            # zooms in: drawn into the base, it showed cut ("THUANIA", "ND": Derinkuyu, prod 2026-10-29). It
            # is drawn on the frames instead, only while it is wholly inside the safe box, fading out before.
            sprite = Image.new("L", (int(r - l) + 8, int(b - t) + 8), 0)
            ImageDraw.Draw(sprite).text((4 - l + x, 4 - t + y), drawable(name, font), font=font, fill=255, anchor="mm")
            transient.append({"name": c["display"], "sprite": sprite,
                              "box": (l - 4, t - 4, l - 4 + sprite.width, t - 4 + sprite.height)})
            continue
        if transient is not None:  # drawn into the base: recorded so its place on each frame can be checked
            transient.append({"name": c["display"], "sprite": None, "box": box})
        _text(draw, (x, y), name, font=font, fill=(150, 152, 160), anchor="mm")
    return base


def render_map(place: Dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
               narration: str = "") -> Optional[Path]:
    """Regional locator map: eases from a ~40° view to ~18° around the site, pin drop, label, and on a 9:16
    frame a scale bar in the narration's unit once the camera rests (2026-10-29). `narration`: the run's
    narration (typography.spoken_system)."""
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
    label_y = int(height * 0.18) if width > height else max(TEXT_TOP + 70, height // 2 - 210)
    # Keep neighbours' names clear of the place label and the pin in the final close-up: the band from
    # the label's top to just below the pin, across the label's width plus a margin (output pixels).
    half_w = min(width // 2, 60 + max(len(label) * 22, len(sub_label) * 18))
    to_base = lambda x, y: (final_view[0] + x * (final_view[2] - final_view[0]) / width,  # noqa: E731
                            final_view[1] + y * (final_view[3] - final_view[1]) / height)
    reserved = (to_base(width // 2 - half_w, label_y - 200) + to_base(width // 2 + half_w, height // 2 + 70),)
    portrait = height > width
    # The scale bar: the final close-up shows `span_close` degrees of longitude across the frame at cos(lat) scale,
    # so one degree of latitude is width / span_close px there; the bar appears only once the zoom has stopped.
    bar = scale_bar(lat0, width / span_close, narration) if portrait else None
    bar_y = TEXT_BOTTOM - 22
    if bar:  # no neighbour's name under the bar and its label
        reserved += (to_base(M - 20, bar_y - 70) + to_base(M + bar["px"] + 260, bar_y + 30),)
    sx0, sy0, sx1, sy1 = get_text_box(width, height)
    labels: List[Dict[str, Any]] = []
    base = relief_base(lat0, lon0, bw, bh, kx, coslat, geo.find_country(place["country"]), project, final_view,
                       label_bottom_fraction=get_text_bounds(width, height)[1] / height, reserved=reserved,
                       final_safe=to_base(sx0, sy0) + to_base(sx1, sy1), transient=labels)

    n = int(CLIP_SECONDS * VIDEO_FPS)
    zooms = [span_wide / (span_wide + (span_close - span_wide) * _ease(i / VIDEO_FPS / 1.6)) for i in range(n)]
    plan = map_label_plan(labels, zooms, width, height, bw, bh)
    # The place label and the bar's figure are planned on the camera's resting view (their halos solved there).
    zf_last = zooms[-1]
    rest = base.resize((width, height), Image.BICUBIC, box=(bw / 2 - bw / 2 / zf_last, bh / 2 - bh / 2 / zf_last,
                                                            bw / 2 + bw / 2 / zf_last, bh / 2 + bh / 2 / zf_last)).convert("RGB")
    label_plates = _label_plates(rest, width // 2, label_y, label, sub_label)
    bar_plate = None
    if bar:
        bar_plate = plan_text(rest, (M + bar["px"] + 22, bar_y + 13), bar["label"], get_font(brand.DATA_FONT, 38, weight=600),
                              WHITE, "ls", track=1, min_halo=0.6, halo_px=17)
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
                    im.paste((150, 152, 160), lab["box"][:2], lab["mask"])
            d = ImageDraw.Draw(im, "RGBA")
            cx, cy = width // 2, height // 2
            # Pulse rings after the pin lands.
            if t > 1.6:
                for k in range(2):
                    p = ((t - 1.6) * 0.8 + k * 0.5) % 1.0
                    r = 20 + 90 * p
                    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*accent(), int(200 * (1 - p))), width=4)
            # Pin drop with a small bounce (1.8 s -> 2.3 s).
            if t >= 1.1:
                drop = _ease((t - 1.1) / 0.5)
                py = cy - 160 * (1 - drop) - (12 * math.sin(math.pi * min(1.0, (t - 1.6) / 0.25)) if 1.6 <= t <= 1.85 else 0)
                d.ellipse([cx - 22, py - 22, cx + 22, py + 22], fill=(*accent(), 255), outline=(20, 20, 20, 255), width=4)
                d.ellipse([cx - 7, py - 7, cx + 7, py + 7], fill=(20, 20, 20, 255))
            a = _ease((t - 1.5) / 0.4)
            for _, plate in label_plates:  # knockout labels rise 12 px as they fade in
                im = plate.draw(im, a, dy=round(12 * (1 - a)))
            if bar:
                ab = _ease((t - 1.75) / 0.3)
                if ab > 0:
                    d = ImageDraw.Draw(im, "RGBA")
                    x1 = M + bar["px"]
                    col = (*WHITE, int(255 * ab))
                    d.line([(M, bar_y), (x1, bar_y)], fill=col, width=5)
                    for xx in (M, x1):
                        d.line([(xx, bar_y - 14), (xx, bar_y + 2)], fill=col, width=5)
                    im = bar_plate.draw(im, ab)
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
    """The elapsed time drawn on the spine between two entries ("1,479 YEARS LATER"; "ABOUT ..." when either
    date is approximate; "" for less than two years)."""
    years = years_between(int(a["year"]), int(b["year"]))
    if years < 2:
        return ""
    about = "ABOUT " if (a.get("approximate") or b.get("approximate")) else ""
    return f"{about}{years:,} YEARS LATER"


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


def render_timeline(events: List[Dict[str, Any]], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                    photo: Path | None = None) -> Optional[Path]:
    """Timeline: the spine draws down (or across in landscape), then each date appears in order. In 9:16 the
    entries are spaced by the time between them (spaced_positions), the elapsed years are written on the spine
    between them, and the topic's photo, blurred and dark, is the ground (`photo`; plain ink without one)."""
    events = sorted(events, key=lambda e: e["year"])[:4]
    if len(events) < 2:
        return None
    n = int(CLIP_SECONDS * VIDEO_FPS)
    top, bottom = get_text_bounds(width, height)
    bg = Image.new("RGB", (width, height), SEA)
    g = ImageDraw.Draw(bg, "RGBA")
    for y in range(0, height, 6):  # subtle vertical gradient
        g.line([(0, y), (width, y)], fill=(13 + y // 120, 16 + y // 120, 21 + y // 110, 255))

    if width > height:
        # Landscape: horizontal spine across the centre; entries alternate above/below to prevent overlap
        spine_y = height // 2
        left, right = int(width * 0.15), int(width * 0.85)
        step = (right - left) / (len(events) - 1)
        f_year, f_lab = get_font("Anton", 54), get_font("Montserrat", 28)
        max_label_w = max(240, int(step * 0.88))

        def frames():
            for i in range(n):
                t = i / VIDEO_FPS
                im = bg.copy()
                d = ImageDraw.Draw(im, "RGBA")
                spine_x = left + (right - left) * _ease(t / 1.0)
                d.line([(left, spine_y), (spine_x, spine_y)], fill=(*accent(), 255), width=6)
                for k, e in enumerate(events):
                    appear = _ease((t - 0.4 - 0.45 * k) / 0.4)
                    if appear <= 0:
                        continue
                    x = left + step * k
                    a = int(255 * appear)
                    r = 16 * appear
                    d.ellipse([x - r, spine_y - r, x + r, spine_y + r], fill=(*accent(), a))
                    # Alternate above (k even) and below (k odd)
                    above = (k % 2 == 0)
                    yt_year = spine_y - 28 if above else spine_y + 30
                    anchor_year = "ms" if above else "mt"
                    _text(d, (x, yt_year), year_text(e["year"], e.get("approximate", False)), font=f_year, fill=(*GOLD, a), anchor=anchor_year)
                    lines, f_fit = fit_lines(d, e["label"], f_lab, max_label_w, 2)
                    for j, line in enumerate(lines):
                        if above:
                            ly = spine_y - 74 - 34 * (len(lines) - 1 - j)
                            _text(d, (x, ly), line, font=f_fit, fill=(*WHITE, a), anchor="ms")
                        else:
                            ly = spine_y + 74 + 34 * j
                            _text(d, (x, ly), line, font=f_lab, fill=(*WHITE, a), anchor="mt")
                yield im
        return _encode(frames(), out, n, width=width, height=height)
    else:
        # Vertical (2026-10-29): a gold-dim spine draws down, each date lands with its label. Two events get the
        # large years and two-line labels; more get smaller years and one-line labels fitted to the column (a
        # label that cannot fit one line at its floor size takes two, never cut). Measured first: if the last
        # entry's label would pass the graphics box's bottom (graphics_box()[3], above the caption band) the
        # spine is lifted, and when the entries no longer fit between the bounds the type steps down.
        x0 = M + 18
        xt = x0 + 56
        label_w = TEXT_RIGHT - xt - 4
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        many = len(events) > 2
        for scale in (1.0, 0.92, 0.84, 0.76, 0.68, 0.6):
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
                # offsets from the entry's dot: the year's top, and the last label row's ink bottom
                e_top = -cap_y // 2
                rows = [cap_y // 2 + gap + j * step for j in range(len(lines))]
                e_bottom = max(probe.textbbox((0, y), ln, font=f_lab, anchor="ls")[3] for y, ln in zip(rows, lines))
                entries.append({"year": year_text(e["year"], e.get("approximate", False)).upper(), "lines": lines,
                                "font": f_lab, "rows": rows, "top": e_top, "bottom": e_bottom})
            y_first = top + 30 - entries[0]["top"]
            y_last = bottom - entries[-1]["bottom"]                    # lifted so its last row ends on the bound
            f_gap = get_font("Montserrat", max(24, int((30 if many else 36) * scale)), weight=700)
            need = max(entries[k]["bottom"] - entries[k + 1]["top"] + 34 for k in range(len(entries) - 1))
            step_y = (y_last - y_first) / (len(entries) - 1)
            if step_y >= need:
                break
        if scale < 1.0:
            log.info("Timeline type set at %d%% to keep %d entries above the caption band.", round(scale * 100), len(events))
        spine_gold, track = brand.GOLD_DIM, 2
        ground = _backdrop(photo, width, height) if photo else Image.new("RGB", (width, height), brand.INK)
        plates = []
        ys = spaced_positions(events, y_first, y_last, need)   # by the time between them, where there is room
        for k, en in enumerate(entries):
            y = ys[k]
            en["y"] = y
            en["year_plate"] = plan_text(ground, (xt, y + cap_y // 2), en["year"], f_year, GOLD, "ls")
            en["label_plates"] = [plan_text(ground, (xt + 4, y + r), ln, en["font"], WHITE, "ls", track=track)
                                  for r, ln in zip(en["rows"], en["lines"])]
            plates += [(f"year{k}", en["year_plate"])] + [(f"label{k}", p) for p in en["label_plates"]]
        # The time since the previous date, after each year on its baseline ("1923  1,143 YEARS LATER"): the
        # vertical room between the platforms' bands holds four entries and nothing more, so it takes no height.
        gaps = []

        def fits(text, en):
            return en["year_plate"].box[2] + 28 + probe.textlength(text, font=f_gap) + 3 * len(text) <= TEXT_RIGHT
        wanted = [(k, gap_text(events[k - 1], events[k])) for k in range(1, len(entries))]
        wanted = [(k, t) for k, t in wanted if t]
        # One wording for the whole timeline: "... YEARS LATER" where every one fits, else "... YEARS" for all.
        if not all(fits(t, entries[k]) for k, t in wanted):
            wanted = [(k, t.removesuffix(" LATER")) for k, t in wanted]
        for k, text in wanted:
            en = entries[k]
            if not fits(text, en):
                continue
            plate = plan_text(ground, (en["year_plate"].box[2] + 28, en["y"] + cap_y // 2), text, f_gap,
                              brand.STONE_LT, "ls", track=3)
            gaps.append((k, plate))
            plates.append((f"gap{k}", plate))
        last: List[Image.Image] = []

        def frames():
            for i in range(n):
                t = i / VIDEO_FPS
                im = ground.copy()
                d = ImageDraw.Draw(im, "RGBA")
                spine = y_first + (entries[-1]["y"] - y_first) * _ease(t / 1.0)
                d.line([(x0, y_first), (x0, spine)], fill=(*spine_gold, 255), width=6)
                for k, en in enumerate(entries):
                    appear = _ease((t - 0.4 - 0.45 * k) / 0.4)
                    if appear <= 0:
                        continue
                    r = 16 * appear
                    y = en["y"]
                    d.ellipse([x0 - r, y - r, x0 + r, y + r], fill=(*GOLD, int(255 * appear)))
                    dy = round(14 * (1 - appear))
                    im = en["year_plate"].draw(im, appear, dy)
                    for p in en["label_plates"]:
                        im = p.draw(im, appear, dy)
                    d = ImageDraw.Draw(im, "RGBA")
                for k, plate in gaps:   # the elapsed time lands with its year (fully drawn by the same frame)
                    shown = _ease((t - 0.4 - 0.45 * k) / 0.4)
                    if shown > 0:
                        im = plate.draw(im, shown, round(14 * (1 - shown)))
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
    line says what the comparison means. 16:9: two bars, the subject (gold) and the landmark (grey)."""
    feet = fact["value"] * (3.28084 if fact["unit"] == "m" else 1.0)
    ref = pick_reference(feet)
    if ref is None or feet <= 0:
        return None
    ref_label, ref_feet, _ = ref
    depth = fact.get("dimension") == "depth"
    if width < height:
        return _render_scale_vertical(fact, feet, ref_label, ref_feet, depth, out, width, height)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    longest = max(feet, ref_feet)
    top, bottom = get_text_bounds(width, height)
    span = bottom - top - (160 if width > height else 330)
    base_y = top + (80 if width > height else 150) if depth else bottom - 40

    if width > height:
        bars = [(fact["subject"], feet, GOLD, int(width * 0.35)),
                (ref_label + (" (height)" if depth else ""), ref_feet, (150, 150, 158), int(width * 0.65))]
        max_label_w = int(width * 0.28)
        num_font_size = 54
        lab_font_size = 28
    else:
        bx0, _, bx1, _ = get_text_box(width, height)                 # both bars and their labels inside the Reel box
        cx_a, cx_b = bx0 + int((bx1 - bx0) * 0.32), bx0 + int((bx1 - bx0) * 0.78)   # 328 and 707 at 1080x1920
        bars = [(fact["subject"], feet, GOLD, cx_a),
                (ref_label + (" (height)" if depth else ""), ref_feet, (150, 150, 158), cx_b)]
        max_label_w = min(340, 2 * (cx_a - bx0), 2 * (bx1 - cx_b))
        num_font_size = 64
        lab_font_size = 30

    hedge = "ABOUT " if fact.get("approximate", True) else ""

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = Image.new("RGB", (width, height), SEA)
            d = ImageDraw.Draw(im, "RGBA")
            d.line([(int(width * 0.08), base_y), (int(width * 0.92), base_y)], fill=(*MUTED, 255), width=4)
            _text(d, (width // 2, base_y + (-24 if depth else 36)), "GROUND LEVEL" if depth else "",
                  font=get_font("Montserrat", 22), fill=(*MUTED, 255), anchor="mm")
            for k, (label, ft, colour, cx) in enumerate(bars):
                grow = _ease((t - 0.3 - 0.5 * k) / 1.3)
                length = span * ft / longest * grow
                y1 = base_y + length if depth else base_y - length
                if label.startswith(PERSON):
                    _person(d, cx, base_y, length, colour, upward=not depth)
                else:
                    bar_w = 55 if width > height else 70
                    d.rectangle([cx - bar_w, min(base_y, y1), cx + bar_w, max(base_y, y1)], fill=(*colour, 255))
                a = int(255 * _ease((t - 1.4 - 0.5 * k) / 0.4))
                if a > 0:
                    num = measurement_text(ft)
                    if k == 0 and hedge:
                        num = hedge + num
                    elif k == 1:
                        num = "ABOUT " + num
                    cur_num_font = fit_font(d, num, "Anton", num_font_size, max_label_w, 26)
                    cur_lab_font = fit_font(d, label.upper(), "Montserrat", lab_font_size, max_label_w, 20)
                    ty = (y1 + (44 if width > height else 50)) if depth else (y1 - (58 if width > height else 70))
                    _text(d, (cx, ty), num, font=cur_num_font, fill=(*colour, a), anchor="mm")
                    lines, cur_lab_font = fit_lines(d, label.upper(), cur_lab_font, max_label_w, 2)
                    for j, line in enumerate(lines):
                        lh = 32 if width > height else 36
                        ly = ty + 46 + lh * j if depth else ty - 46 - lh * (len(lines) - 1 - j)
                        _text(d, (cx, ly), line, font=cur_lab_font, fill=(*WHITE, a), anchor="mm")
            yield im

    return _encode(frames(), out, n, width=width, height=height)


def _silhouette(d: ImageDraw.ImageDraw, label: str, cx: float, base_y: float, h: float, colour) -> float:
    """The reference landmark drawn as its outline, `h` px tall, standing on `base_y`: the Statue of Liberty
    (foundation and pedestal to the raised torch), Elizabeth Tower (shaft, clock stage, belfry, spire), the
    Great Pyramid, else a standing person. Returns the drawn width."""
    fill = (*colour, 255)
    y = lambda f: base_y - h * f  # 0 = base, 1 = top
    low = label.lower()
    if "liberty" in low:
        w = h * 0.36
        d.polygon([(cx - w / 2, y(0)), (cx + w / 2, y(0)), (cx + w * 0.36, y(0.17)), (cx - w * 0.36, y(0.17))], fill=fill)
        d.rectangle([cx - w * 0.26, y(0.40), cx + w * 0.26, y(0.17)], fill=fill)            # pedestal
        d.polygon([(cx - w * 0.30, y(0.40)), (cx + w * 0.30, y(0.40)), (cx + w * 0.22, y(0.47)),
                   (cx - w * 0.22, y(0.47))], fill=fill)
        d.polygon([(cx - w * 0.13, y(0.47)), (cx + w * 0.13, y(0.47)), (cx + w * 0.08, y(0.80)),
                   (cx - w * 0.09, y(0.80))], fill=fill)                                    # robe
        r = h * 0.025
        d.ellipse([cx - r, y(0.83) - r, cx + r, y(0.83) + r], fill=fill)                    # head
        for k in range(-2, 3):                                                              # crown rays
            d.line([(cx + k * r * 0.5, y(0.85)), (cx + k * r * 1.4, y(0.88))], fill=fill, width=max(1, int(h * 0.006)))
        d.line([(cx + w * 0.06, y(0.79)), (cx + w * 0.10, y(0.95))], fill=fill, width=max(2, int(h * 0.022)))   # arm
        d.polygon([(cx + w * 0.07, y(0.95)), (cx + w * 0.13, y(0.95)), (cx + w * 0.10, y(1.0))], fill=fill)    # torch
        return w
    if "elizabeth" in low or "big ben" in low:
        w = h * 0.13
        d.rectangle([cx - w / 2, y(0.72), cx + w / 2, y(0)], fill=fill)
        d.rectangle([cx - w * 0.6, y(0.80), cx + w * 0.6, y(0.70)], fill=fill)              # clock stage
        r = w * 0.38
        d.ellipse([cx - r, y(0.75) - r, cx + r, y(0.75) + r], fill=(*SEA, 255))             # clock face
        d.rectangle([cx - w * 0.45, y(0.86), cx + w * 0.45, y(0.80)], fill=fill)            # belfry
        d.polygon([(cx - w * 0.45, y(0.86)), (cx + w * 0.45, y(0.86)), (cx, y(1.0))], fill=fill)
        return w * 1.2
    if "pyramid" in low:
        w = h * 1.57
        d.polygon([(cx - w / 2, y(0)), (cx + w / 2, y(0)), (cx, y(1.0))], fill=fill)
        return w
    _person(d, int(cx), base_y, h, colour, upward=True)
    return h * 0.3


EARTH, EARTH_LINE = (38, 31, 24), (52, 43, 33)   # the ground in section: dark warm earth, its strata a step up


def _silhouette_width(label: str) -> float:
    """A landmark silhouette's width per px of its height (as _silhouette draws it)."""
    low = label.lower()
    return 0.36 if "liberty" in low else 0.16 if ("elizabeth" in low or "big ben" in low) else 1.57 if "pyramid" in low else 0.3


def _render_scale_vertical(fact, feet, ref_label, ref_feet, depth, out, width, height):
    """9:16 scale (see render_scale), inside the Reel text box: the subject and its figure above, the drawing,
    then one line of meaning."""
    x0, top, x1, bottom = get_text_box(width, height)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    hedge = "ABOUT " if fact.get("approximate", True) else ""
    ref_name = ref_label.split(" (")[0]
    subject = str(fact.get("subject") or "").upper()
    figure = hedge + measurement_text(feet) + (" DEEP" if depth else " TALL")
    gap = abs(ref_feet - feet)
    if depth:
        meaning = ([f"THE {ref_name.upper()}, {measurement_text(ref_feet).split(' (')[0]},",
                    f"WOULD SHOW ONLY ITS TOP {measurement_text(gap).split(' (')[0]}."] if ref_feet > feet else
                   [f"THE {ref_name.upper()} WOULD VANISH:",
                    f"ITS TOP {measurement_text(gap).split(' (')[0]} BELOW THE SURFACE."])
    else:
        ratio = max(feet, ref_feet) / min(feet, ref_feet)
        taller, shorter = (subject.title() if feet > ref_feet else "The " + ref_name), (
            "the " + ref_name if feet > ref_feet else subject.lower())
        meaning = [f"{ref_name.upper()}: {measurement_text(ref_feet).split(' (')[0]}.",
                   (f"ABOUT {ratio:.0f} TIMES TALLER." if ratio >= 1.5 else "NEARLY THE SAME HEIGHT.")]
        del taller, shorter
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    wide = x1 - x0 - 8
    f_sub = fit_font(probe, subject, "Montserrat", 30, wide, 22)
    f_sub = get_font("Montserrat", f_sub.size, weight=800)
    f_fig = fit_font(probe, figure, "Anton", 76, wide, 48)
    f_mean = get_font("Montserrat", 30, weight=700)
    while any(probe.textlength(t, font=f_mean) > wide for t in meaning) and f_mean.size > 22:
        f_mean = get_font("Montserrat", f_mean.size - 2, weight=700)
    y_sub = top + 10 + cap_height(f_sub)
    y_fig = y_sub + 24 + cap_height(f_fig)
    mean_h = 2 * int(cap_height(f_mean) * 1.6)
    y_mean0 = bottom - mean_h + cap_height(f_mean)
    draw_bottom = bottom - mean_h - 28
    ground = Image.new("RGB", (width, height), SEA)
    xt = x0 + 4                                            # glyph side-bearings stay inside the box
    plates = [("subject", plan_text(ground, (xt, y_sub), subject, f_sub, MUTED, "ls", track=3)),
              ("figure", plan_text(ground, (xt, y_fig), figure, f_fig, GOLD, "ls")),
              *[(f"meaning{k}", plan_text(ground, (xt, y_mean0 + k * int(cap_height(f_mean) * 1.6)), t, f_mean,
                                         WHITE, "ls", track=1)) for k, t in enumerate(meaning)]]
    ref_grey = brand.STONE_LT
    width_per_px = _silhouette_width(ref_label)            # the landmark's width per px of its height
    if depth:
        # The landmark stands on the shaft's floor, inside it: room above the ground for any part taller than
        # the depth, and a shaft wide enough to hold it.
        line_y = y_fig + 70
        above = max(0.0, ref_feet - feet)
        px_ft = (draw_bottom - line_y) / max(feet, 1.0)
        if above * px_ft > line_y - (y_fig + 16):
            px_ft = (draw_bottom - (y_fig + 16)) / (feet + above)
        px_ft = min(px_ft, (x1 - x0 - 120) / max(1.0, ref_feet * width_per_px))
        floor_y = draw_bottom
        line_y = floor_y - feet * px_ft
        ref_w = ref_feet * px_ft * width_per_px
        half = max(ref_w / 2 + 50, 150)
        cx = (x0 + x1) / 2
        shaft = (cx - half, cx + half)
        ref_cx = cx
    else:
        base_y = draw_bottom
        block = 190
        room = x1 - x0 - 60 - block - 40                    # beside the subject's block
        px_ft = min((base_y - (y_fig + 40)) / max(feet, ref_feet), room / max(1.0, ref_feet * width_per_px))
        shaft = (x0 + 30, x0 + 30 + block)
        ref_cx = shaft[1] + 40 + max(room, ref_feet * px_ft * width_per_px) / 2
    last: list = []

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = ground.copy()
            d = ImageDraw.Draw(im, "RGBA")
            if depth:
                d.rectangle([x0, line_y, x1, floor_y + 24], fill=(*EARTH, 255))            # the ground, in section
                for k in range(1, 6):                                                      # faint strata
                    yy = line_y + (floor_y + 24 - line_y) * k / 6
                    d.line([(x0, yy), (x1, yy)], fill=(*EARTH_LINE, 255), width=2)
                dig = _ease((t - 0.3) / 1.2)
                y_end = line_y + (floor_y - line_y) * dig
                d.rectangle([shaft[0], line_y, shaft[1], y_end], fill=(*SEA, 255), outline=(*GOLD, 255), width=3)
                drop = _ease((t - 1.6) / 1.0)
                if drop > 0:
                    hh = ref_feet * px_ft
                    base = floor_y - (1 - drop) * (floor_y - line_y + hh + 40)
                    _silhouette(d, ref_label, ref_cx, base, hh, ref_grey)
                    d.rectangle([x0, line_y - 2, x1, line_y + 1], fill=(*WHITE, 255))     # ground line over it
                else:
                    d.rectangle([x0, line_y - 2, x1, line_y + 1], fill=(*WHITE, 255))
            else:
                d.rectangle([x0, base_y, x1, base_y + 3], fill=(*WHITE, 255))
                rise = _ease((t - 0.3) / 1.2)
                hs = feet * px_ft * rise
                d.rectangle([shaft[0], base_y - hs, shaft[1], base_y], fill=(*GOLD, 255))
                rr = _ease((t - 1.2) / 1.2)
                if rr > 0:
                    _silhouette(d, ref_label, ref_cx, base_y, ref_feet * px_ft * rr, ref_grey)
            for name, plate in plates:
                at = {"subject": 0.1, "figure": 0.4}.get(name, 2.7)
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
    frame, so the two units disagreed until the count landed (number card, 2026-10-29)."""
    import re

    def counted(m) -> str:
        raw = m.group(0)
        num = float(raw.replace(",", ""))
        separated = "," in raw
        q = 1.0 if not separated and "." not in raw and 1000 <= num <= 2199 else p  # a year, or reads as one
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        spec = ("," if separated else "") + (f".{decimals}f" if decimals else "")
        return format(num * q if decimals else round(num * q), spec)
    return re.sub(r"\d[\d,]*(?:\.\d+)?", counted, value)


def _stat_plan(d: ImageDraw.ImageDraw, card: Dict[str, Any], f_num_size: int, max_w: int, label_px: int,
               pitch: int) -> Dict[str, Any]:
    """One number card's text, measured around its centre (offsets from cy): the value's font (sized on its final
    figure, the widest the count reaches), the rule under it, the label's lines, and the block's top and bottom.
    The rule sits below the value's measured ink: a large single figure ("40+ KM") reached through it (2026-10-29)."""
    value = card["value"].upper()
    value_font = fit_font(d, value, "Anton", f_num_size, max_w, 50)
    v_top, v_bottom = d.textbbox((0, -30), value, font=value_font, anchor="mm")[1::2]
    rule = max(60, v_bottom + 18)
    lines, f_fit = fit_lines(d, card["label"].upper(), get_font("Montserrat", label_px), max_w, 2)
    step = max(1, round(pitch * f_fit.size / label_px))
    rows = [rule + 45 + step * j for j in range(len(lines))]
    bottom = max([rule + 2] + [d.textbbox((0, y), line, font=f_fit, anchor="mm")[3] for y, line in zip(rows, lines)])
    return {"value_font": value_font, "rule": rule, "lines": lines, "label_font": f_fit, "rows": rows,
            "top": v_top, "bottom": bottom}


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


def _stat_layout(d: ImageDraw.ImageDraw, card: Dict[str, Any], num_px: int, width: int, scale: float = 1.0) -> Dict[str, Any]:
    """One card measured (offsets from its block's top, x from the left margin): the figure's font fitted so the
    figure and its unit stay inside the graphics box, the label in up to two lines, the bracketed figure in the
    data face (IBM Plex Mono), the prefix as a kicker."""
    parts = figure_parts(card["value"].upper())
    pre, num, unit, sec = parts["pre"], parts["num"], parts["unit"], parts["sec"]
    if not num:                          # no figure: the value is drawn as a heading
        num, unit = "", parts["unit"]
    size = num_px
    while True:
        f_num = get_font("Anton", size)
        f_unit = get_font("Anton", max(48, round(size * 0.37)))
        num_w = sum(w for _, w in _cells(d, num, f_num)) if num else 0
        unit_w = d.textlength(unit, font=f_unit) if unit else 0
        if num_w + (24 if num and unit else 0) + unit_w <= width or size <= 120:
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
    if unit:
        rows.append({"name": "unit", "kind": "text", "text": unit, "font": f_unit, "y": y, "x": (num_w + 24) if num else 0,
                     "fill": WHITE})
    f_lab_px = round(46 * scale)
    lines, f_lab, _ = fit_text(d, card["label"].upper(), "Montserrat", f_lab_px, width - 120, 2,  # 3 px tracking
                               max(26, round(f_lab_px * 0.75)), weight=800, quiet=True)
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
    return {"rows": rows, "top": high, "bottom": low, "height": low - high, "num_y": num_y, "figure_px": size}


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
    cards = [dict(c, value=spoken_units(str(c.get("value") or ""), narration),
                  label=spoken_units(str(c.get("label") or ""), narration)) for c in cards[:2]]
    if not cards:
        return None
    if width > height:
        return _render_stat_cards_landscape(cards, out, photo, width, height)
    from .typography import cover_crop
    n = int(CLIP_SECONDS * VIDEO_FPS)
    top, bottom = get_text_bounds(width, height)
    text_w = TEXT_RIGHT - M - 4
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
    y = max(top, (top + bottom) // 2 - total // 2)
    for p in plans:
        p["y0"] = y - p["top"]
        y += p["height"] + gap
    if photo and Path(photo).exists():
        bg = cover_crop(Path(photo), (width, height)).convert("RGB")
    else:
        bg = Image.new("RGB", (width, height), SEA)
    from .multi_format import scrim
    bg = scrim(bg, plans[0]["y0"] + plans[0]["top"] - 40, plans[-1]["y0"] + plans[-1]["bottom"] + 40, feather=380,
               target=0.24).convert("RGB")
    rest = _pushed_in(bg, push_in((n - 1) / VIDEO_FPS, CLIP_SECONDS))
    plates: List[Tuple[str, Any]] = []
    for p in plans:   # each line planned on the resting frame: its halo, and for a figure one plate per character
        for r in p["rows"]:
            yy = p["y0"] + r["y"]
            if r["kind"] == "kicker":
                bx0, by0, bx1, by1 = r.get("bar", (0, 0, 44, 4))
                r["rule"] = (M + bx0, p["y0"] + by0, M + bx1, p["y0"] + by1)
                r["plates"] = [plan_text(rest, (M, yy), r["text"], r["font"],
                                         GOLD, "ls", track=r["font"].size * 0.15)]
            elif r["kind"] == "figure":
                x, r["plates"], r["cells"] = M, [], []
                for c, w in _cells(probe, r["text"], r["font"]):
                    cx = x + w / 2
                    r["plates"].append(plan_text(rest, (cx, yy), c, r["font"], GOLD, "ms", halo_px=25))
                    x += w
                r["x_end"] = x
            elif r["kind"] == "count":
                r["plates"] = [plan_text(rest, (M, yy), r["text"], r["font"], r["fill"], "ls", track=r.get("track", 0))]
                r["x_end"] = M + probe.textlength(r["text"], font=r["font"])
            else:
                r["plates"] = [plan_text(rest, (M + r.get("x", 0), yy), r["text"], r["font"], r["fill"], "ls",
                                         track=r.get("track", 0))]
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
                        _text(d, (r["x_end"], yy), _shown_value(r["text"], count), r["font"], (*r["fill"], int(255 * a)),
                              anchor="rs")
                    else:
                        if r["kind"] == "kicker":
                            x0, y0, x1, y1 = r.get("rule", r.get("bar", (M, yy, M + 44, yy + 3)))
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
    """16:9 (the documentary): 1-2 headline figures counting up, centred, over a dark photo backdrop.
    `narration`: the run's narration, whose units the figures are drawn in (typography.spoken_units)."""
    # One unit per run: a figure in the other system is drawn in the unit the voice speaks (2026-10-29).
    cards = [dict(c, value=spoken_units(str(c.get("value") or ""), narration),
                  label=spoken_units(str(c.get("label") or ""), narration)) for c in cards[:2]]
    if not cards:
        return None
    n = int(CLIP_SECONDS * VIDEO_FPS)
    bg = _backdrop(photo, width=width, height=height)
    top, bottom = get_text_bounds(width, height)
    ts = type_scale(width, height)  # one hierarchy for every graphic (art direction, 2026-10-24)

    if width > height:
        if len(cards) == 1:
            centres = [(width // 2, height // 2)]
            max_w = int(width * 0.70)
        else:
            centres = [(int(width * 0.30), height // 2), (int(width * 0.70), height // 2)]
            max_w = int(width * 0.35)
    else:
        centres = [(width // 2, (top + bottom) // 2)] if len(cards) == 1 else [(width // 2, top + 190), (width // 2, bottom - 230)]
        max_w = min(width - 160, centred_text_width(width, height))  # clear of Shorts' icon column
    f_num_size = int(ts["figure"] * (1.15 if len(cards) == 1 else 1.0))
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    # Every card ends above the caption band's gap (get_text_bounds' bottom, safe_zones.graphics_box) and clear of
    # the other card: a label kept whole on three or more lines ran past it and under the second figure
    # (2026-10-29). A card that would is lifted by its overrun (the one above with it); when the two still do not
    # fit between the bounds, the labels are set smaller, a step at a time, never cut.
    for scale in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
        plans = [_stat_plan(probe, card, f_num_size, max_w, max(16, int(ts["label"] * scale)), 46) for card in cards]
        if width > height:
            placed = centres
            break
        placed, limit = [], bottom
        for (cx, cy), plan in reversed(list(zip(centres, plans))):
            cy = min(cy, limit - plan["bottom"])
            placed.insert(0, (cx, cy))
            limit = cy + plan["top"] - 24
        if all(cy + plan["top"] >= top for (cx, cy), plan in zip(placed, plans)):
            break
    centres = placed

    def shown(card: Dict[str, Any], p: float) -> str:
        return _shown_value(card["value"], p)

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = _pushed_in(bg, push_in(t, CLIP_SECONDS))  # a slow push on the backdrop: a card never sits static
            d = ImageDraw.Draw(im, "RGBA")
            for k, (card, plan, (cx, cy)) in enumerate(zip(cards, plans, centres)):
                start = count_start(k)  # every count rests by count_done_by of the clip
                a = _ease((t - start) / MOTION["reveal"])
                if a <= 0:
                    continue
                count = _ease((t - start) / MOTION["count"])
                _text(d, (cx, cy - 30), shown(card, count).upper(), font=plan["value_font"], fill=(*GOLD, int(255 * a)), anchor="mm")
                d.line([(cx - 80, cy + plan["rule"]), (cx + 80, cy + plan["rule"])], fill=(*WHITE, int(160 * a)), width=3)
                for y, line in zip(plan["rows"], plan["lines"]):
                    _text(d, (cx, cy + y), line, font=plan["label_font"], fill=(*WHITE, int(255 * a)), anchor="mm")
            yield im

    return _encode(frames(), out, n, width=width, height=height)



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


def _vault(x0: float, x1: float, floor: float, h: float, seed: int) -> list[tuple[float, float]]:
    """A hand-cut room: a flat floor and an irregular rounded vault `h` high."""
    import random
    rnd = random.Random(seed)
    p1, p2, a1 = rnd.uniform(0, 6.28), rnd.uniform(0, 6.28), rnd.uniform(0.03, 0.07)
    tilt = rnd.uniform(-0.14, 0.14)
    pts = []
    steps = 28
    for i in range(steps + 1):
        u = i / steps
        lift = max(0.0, 1 - abs(2 * u - 1) ** 2.4) ** 0.55
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
            family = Path(font.path).stem.split("-")[0]                  # "Montserrat-Variable" -> "Montserrat"
            font = get_font(family, font.size + 1, weight=700 if family == "Montserrat" else None)


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


def _render_cross_section_vertical(cs: dict[str, Any], levels: list[dict[str, Any]], total: float, out: Path,
                                   width: int, height: int) -> Path | None:
    """The 9:16 cutaway (2026-11-02 design review, 6/10 -> rebuilt): a linear depth axis from the ground near the
    top of the text box to the total depth at its bottom, a ruler at the left (a tick every 50 ft), rooms cut as
    irregular vaults branching off an accent shaft with sloped passages between levels (a city, not a comb),
    labels fanned out on leaders so the shallow ones never overlap, and a gold lantern that descends the shaft,
    each level's label fading in as it arrives and dimming as it moves on, so the deepest figure ends brightest."""
    import itertools
    import random
    g = _Grid(width, height)
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    title_lines, f_title = _fit_title(g, cs["subject"].upper(), S(811))
    title_base = [382 + 64 * j for j in range(len(title_lines))]
    approx = cs.get("approximate", True)
    cap_base = title_base[-1] + 46
    ground_d = (cap_base if approx else title_base[-1]) + 32        # design y of the ground
    bottom_d = 985.0                                                 # design y of the total depth
    scale = (bottom_d - ground_d) / total

    def dy(ft: float) -> float:
        return ground_d + ft * scale

    ruler_x, shaft_x, shaft_w = 150.0, 226.0, 22.0
    rooms_x1, label_x = 486.0, 526.0
    f_fig = get_font("Anton", g.f(44))
    f_deep = get_font("Anton", g.f(68))
    f_lab = get_font("Montserrat", g.f(30), weight=600)
    f_small = get_font("Montserrat", g.f(28), weight=600)
    lab_w = S(888 - label_x)

    # ---- the city: rooms per level, a passage from the shaft, ramps between levels
    plan = []
    prev_floor, prev_rooms = ground_d, []
    for k, lv in enumerate(levels):
        rnd = random.Random(31 + 7 * k)
        floor = dy(lv["depth_ft"])
        room_h = max(20.0, min(54.0, (floor - prev_floor) * 0.7))
        x = shaft_x + shaft_w / 2 + rnd.uniform(10, 24)
        rooms = []      # (x0, x1, floor, height, seed): each room's floor a few px off the level's
        while x < rooms_x1 - 40:
            w = rnd.uniform(52, 96)
            if x + w > rooms_x1:
                w = rooms_x1 - x
            rooms.append((x, x + w, floor - rnd.uniform(0, 4), room_h * rnd.uniform(0.7, 1.0), 100 * k + len(rooms)))
            x += w + rnd.uniform(14, 30)
        if room_h > 24 and rnd.random() < 0.75:                       # a niche on the shaft's other side
            w = rnd.uniform(36, 50)
            rooms.append((shaft_x - shaft_w / 2 - w, shaft_x - shaft_w / 2 + 2, floor, room_h * 0.6, 100 * k + 50))
        passage_h = min(11.0, room_h * 0.5)
        ramp, landings = [], []
        if k and prev_rooms and floor - room_h - prev_floor > 40:   # close levels share the shaft only
            gap = floor - room_h - prev_floor
            legs = max(1, round(gap / 80))
            src = max(prev_rooms, key=lambda r: r[1])
            xa = (src[0] + src[1]) / 2
            ramp = [(xa, prev_floor - 3)]
            for j in range(legs):
                xb = 292.0 + rnd.uniform(-8, 12) if (j % 2 == 0) == (xa > 380) else 452.0 + rnd.uniform(-12, 8)
                yb = prev_floor + (floor - 3 - prev_floor) * (j + 1) / legs
                ramp.append((xb, yb))
                if j < legs - 1:                                     # a landing at each switchback
                    lw = rnd.uniform(34, 50)
                    lx = xb - lw * 0.75 if xb < 380 else xb - lw * 0.25
                    landings.append((lx, lx + lw, yb + 3, rnd.uniform(18, 26), 100 * k + 70 + j))
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
                for x0, x1, f0, h, seed in p["rooms"]:
                    pen.poly(g.pts(_vault(x0, x1, f0, h, seed)), fill=255)
                if p["ramp"]:
                    pen.line(g.pts(p["ramp"]), 255, S(7))
        return paint

    carves = [_carve(carve_box, paint_upto(k), S(2)) for k in range(-1, len(levels))]   # [shaft only, +lvl0, ...]

    # ---- label blocks fanned out between the ground and the box's bottom
    blocks = []
    for k, (lv, p) in enumerate(zip(levels, plan)):
        deepest = k == len(levels) - 1
        ff = f_deep if deepest else f_fig
        lines, f_fit, _ = fit_text(probe, lv["label"], "Montserrat", g.f(30), int(lab_w), 2, g.f(30), weight=600,
                                   quiet=True)
        if probe.textlength(lines[-1] if lines else "", font=f_fit) > lab_w:
            lines, f_fit, _ = fit_text(probe, lv["label"], "Montserrat", g.f(30), int(lab_w), 3, g.f(30),
                                       weight=600, quiet=True)
        capf = cap_height(ff) / g.k
        h = capf + 42 + 36 * (len(lines) - 1) + 8
        blocks.append({"font": ff, "lines": lines, "cap": capf, "h": h, "want": p["floor"] - p["passage_h"] / 2})
    tops = [b["want"] - b["cap"] / 2 for b in blocks]
    lo, hi, gap = ground_d + 14, 1052.0, 14.0
    for k in range(len(tops)):
        tops[k] = max(tops[k], lo if k == 0 else tops[k - 1] + blocks[k - 1]["h"] + gap)
    for k in range(len(tops) - 1, -1, -1):
        limit = hi - blocks[k]["h"] if k == len(tops) - 1 else tops[k + 1] - gap - blocks[k]["h"]
        tops[k] = min(tops[k], limit)

    # ---- the static ground: sky, banded rock, the surface, the ruler
    ground_px = Y(ground_d)
    base = _rock_ground(width, height, lambda xs: xs * 0 + ground_px)
    pen = _Pen((0, Y(ground_d - 8), width, Y(bottom_d + 16)))
    surface = (*brand.STONE_LT, 190)                           # it runs past the text box: kept dim
    pen.line([(0, ground_px), (X(50), ground_px)], surface, S(3), caps=False)   # gapped for "0 FT"
    pen.line([(X(ruler_x - 16), ground_px), (width, ground_px)], surface, S(3), caps=False)
    pen.line([(X(ruler_x), ground_px), (X(ruler_x), Y(bottom_d))], (*brand.GOLD_DIM, 255), S(2), caps=False)
    tick = 50 if total <= 400 else 100 if total <= 900 else 250
    for ft in range(0, int(total) + 1, 10 if total <= 400 else tick // 5):
        major = ft % tick == 0
        yy = Y(dy(ft))
        pen.line([(X(ruler_x - (16 if major else 7)), yy), (X(ruler_x), yy)],
                 (*(brand.STONE_LT if major else brand.GOLD_DIM), 255), S(2 if major else 1.5), caps=False)
    pen.line([(X(ruler_x - 16), Y(bottom_d)), (X(ruler_x), Y(bottom_d))], (*brand.STONE_LT, 255), S(2), caps=False)
    base.alpha_composite(pen.image(), (pen.x0, pen.y0))

    # ---- leaders, the lantern and its route
    leaders = []
    for k, (p, b) in enumerate(zip(plan, blocks)):
        deepest = k == len(levels) - 1
        y0 = p["floor"] - p["passage_h"] / 2
        y1 = tops[k] + b["cap"] / 2
        lp = _Pen((X(p["reach"] - 12), Y(min(y0, y1) - 8), X(label_x), Y(max(y0, y1) + 8)))
        col = (*(brand.GOLD if deepest else brand.GOLD_DIM), 255)
        lp.line(g.pts([(p["reach"] - 4, y0), (498, y0), (510, y1), (label_x - 10, y1)]), col, S(2))
        lp.ellipse(X(p["reach"] - 4), Y(y0), S(3.5), S(3.5), fill=col)
        leaders.append(lp.sprite())
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
        lantern.draw(im, a_shaft, X(shaft_x), Y(ly))
        return im

    final = art(0, final=True)

    # ---- every word, planned on the final art
    timed = []   # (name, plate, fades in at, dims at, its dim twin)
    for j, (ln, yb) in enumerate(zip(title_lines, title_base)):
        timed.append((f"title{j}", _plan(final, (X(77), Y(yb)), ln, f_title, WHITE, "ls"), 0.1 + 0.08 * j, None, None))
    if approx:
        timed.append(("approx", _plan(final, (X(79), Y(cap_base)), "APPROX. DEPTHS", f_small, MUTED, "ls",
                                          track=S(3)), 0.3, None, None))
    for j, ft in enumerate(range(0, int(total) + 1, tick)):
        if total - ft < tick * 0.3 and ft:
            continue                                                 # the deepest level names the bottom
        text = f"{ft} FT" if ft == 0 else f"{ft:,}"
        timed.append((f"ruler{j}", _plan(final, (X(ruler_x - 24), Y(dy(ft))), text, f_small, MUTED, "rm"),
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
            yb = fig_base + 42 + 36 * j
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
    depth axis with a ruler, fanned leader labels and a lantern descending the shaft; 16:9 is unchanged."""
    import random
    levels = sorted(cs["levels"], key=lambda lv: lv["depth_ft"])
    total = float(cs["total_depth_ft"])
    if total <= 0 or len(levels) < 2:
        return None
    if width <= height:
        return _render_cross_section_vertical(cs, levels, total, out, width, height)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    top, bottom_bound = get_text_bounds(width, height)

    if width > height:
        ground = top + 70
        bottom = bottom_bound - 30
        shaft_x = int(width * 0.22)
        label_x = int(width * 0.42)
        label_w = int(width * 0.52)
        f_lab = get_font("Montserrat", 28)
        f_dep = get_font("Anton", 34)
        f_sub = get_font("Montserrat", 20)
        f_title_size = 46
        min_gap = 64
        lh = 34
    else:
        ground = top + 110
        bottom = bottom_bound - 40
        shaft_x = 250
        label_x = width // 2
        label_w = get_text_box(width, height)[2] - label_x          # labels end before Shorts' icon column
        f_lab = get_font("Montserrat", 32)
        f_dep = get_font("Anton", 40)
        f_sub = get_font("Montserrat", 22)
        f_title_size = 56
        min_gap = 76
        lh = 38

    scale = (bottom - ground) / total
    rnd = random.Random(7)
    bg = Image.new("RGB", (width, height), SEA)
    d0 = ImageDraw.Draw(bg, "RGBA")
    for y in range(ground, height):  # strata: warm earth bands darkening with depth
        k = (y - ground) / (height - ground)
        band = 6 * math.sin(y / 23.0) + rnd.uniform(-2, 2)
        d0.line([(0, y), (width, y)], fill=(int(70 - 30 * k + band), int(56 - 26 * k + band), int(44 - 22 * k + band), 255))
    d0.line([(0, ground), (width, ground)], fill=(*MUTED, 255), width=4)
    bx0, _, bx1, _ = get_text_box(width, height)
    _text(d0, (bx1, ground - 26), "GROUND LEVEL", font=get_font("Montserrat", 24), fill=(*MUTED, 255), anchor="rm")
    title = cs["subject"].upper() + ("  ·  APPROX. DEPTHS" if cs.get("approximate", True) else "")
    tx = max(int(width * 0.08), bx0)
    _text(d0, (tx, top + 16), title, font=fit_font(d0, title, "Anton", f_title_size, bx1 - tx, 28), fill=(*WHITE, 255), anchor="lm")

    # Relaxation pass to completely eliminate any label/callout collisions and enforce breathing room below ground
    min_top_pad = 65 if width > height else 75
    phys_ys = [ground + lv["depth_ft"] * scale for lv in levels]
    target_ys = [max(ground + min_top_pad, y) for y in phys_ys]
    for _ in range(35):
        for i in range(len(target_ys) - 1):
            target_ys[i] = max(ground + min_top_pad, target_ys[i])
            gap = target_ys[i + 1] - target_ys[i]
            if gap < min_gap:
                push = (min_gap - gap) / 2
                target_ys[i] = max(ground + min_top_pad, target_ys[i] - push)
                target_ys[i + 1] = min(bottom + 20, target_ys[i + 1] + push)

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = bg.copy()
            d = ImageDraw.Draw(im, "RGBA")
            depth_now = total * _ease((t - 0.3) / 3.4)
            y_now = ground + depth_now * scale
            d.rectangle([shaft_x - 22, ground, shaft_x + 22, y_now], fill=(18, 16, 14, 255))
            for idx, lv in enumerate(levels):
                # A label fades in over the next 8% of the dig; one at the very bottom starts that
                # fade 8% earlier, or it would never become visible.
                reveal_at = min(lv["depth_ft"], total * 0.92)
                if reveal_at > depth_now:
                    continue
                y_phys = phys_ys[idx]
                y_t = target_ys[idx]
                a = int(255 * _ease((depth_now - reveal_at) / (total * 0.08) if total else 1))

                # Chamber rect & leader line
                if abs(y_t - y_phys) > 4:
                    # Shaft opening
                    d.rectangle([shaft_x + 20, y_phys - 10, shaft_x + 50, y_phys + 10], fill=(22, 19, 16, a))
                    # Leader line from shaft to target y
                    d.line([(shaft_x + 50, y_phys), (label_x - 20, y_t)], fill=(*accent(), int(180 * a)), width=2)
                    d.rectangle([label_x - 20, y_t - 12, label_x - 10, y_t + 12], fill=(*accent(), int(220 * a)))
                    # Depth leader line (clean orthogonal architectural leader)
                    d.line([(shaft_x - 22, y_phys), (shaft_x - 38, y_phys), (shaft_x - 38, y_t), (shaft_x - 50, y_t)], fill=(*GOLD, int(160 * a)), width=2)
                    _text(d, (shaft_x - 58, y_t - 10), f"{lv['depth_ft']:,.0f} FT", font=f_dep, fill=(*GOLD, a), anchor="rm")
                    _text(d, (shaft_x - 58, y_t + 18), f"({lv['depth_ft'] / 3.28084:,.0f} M)", font=f_sub, fill=(*MUTED, a), anchor="rm")
                    lines, f_fit = fit_lines(d, lv["label"].upper(), f_lab, label_w, 2)
                    for j, line in enumerate(lines):
                        _text(d, (label_x, y_t + (j - (len(lines) - 1) / 2) * lh), line, font=f_fit, fill=(*WHITE, a), anchor="lm")
                else:
                    d.rectangle([shaft_x + 22, y_phys - 14, label_x - 25, y_phys + 14], fill=(22, 19, 16, a))
                    _text(d, (shaft_x - 50, y_phys - 10), f"{lv['depth_ft']:,.0f} FT", font=f_dep, fill=(*GOLD, a), anchor="rm")
                    _text(d, (shaft_x - 50, y_phys + 18), f"({lv['depth_ft'] / 3.28084:,.0f} M)", font=f_sub, fill=(*MUTED, a), anchor="rm")
                    lines, f_fit = fit_lines(d, lv["label"].upper(), f_lab, label_w, 2)
                    for j, line in enumerate(lines):
                        _text(d, (label_x, y_phys + (j - (len(lines) - 1) / 2) * lh), line, font=f_fit, fill=(*WHITE, a), anchor="lm")
            d.ellipse([shaft_x - 14, y_now - 14, shaft_x + 14, y_now + 14], fill=(*accent(), 255))
            yield im

    return _encode(frames(), out, n, width=width, height=height)


# ---------------------------------------------------------------- route map
def render_route(stops: List[Dict[str, Any]], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
    """The journey on the relief map: a gold path drawn stop by stop, each stop pinned and labelled.
    Stops are located like the topic itself (inside their stated country, or skipped)."""
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
    # Fit every stop inside the middle of the frame (x between 15-85%, y between the text bounds).
    span_lon = max((max(lons) - min(lons)) * coslat / 0.62, 6.0)
    span_lat = max((max(lats) - min(lats)) / ((bottom - top - 100) / height), 6.0)
    kx = min(bw / span_lon, bh / span_lat)

    def project(lon: float, lat: float) -> Tuple[float, float]:
        return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx

    base = relief_base(lat0, lon0, bw, bh, kx, coslat, None, project, (0, 0, bw, bh)).resize((width, height), Image.LANCZOS)
    pts = [(project(p["lon"], p["lat"])[0] / scale, project(p["lon"], p["lat"])[1] / scale) for p in located]
    n = int(CLIP_SECONDS * VIDEO_FPS)
    f_lab = get_font("Montserrat", 28 if width > height else 34)
    seg_time = 3.6 / (len(pts) - 1)

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = base.convert("RGB")
            d = ImageDraw.Draw(im, "RGBA")
            for k in range(len(pts) - 1):
                prog = _ease((t - 0.4 - k * seg_time) / seg_time)
                if prog <= 0:
                    break
                (x0, y0), (x1, y1) = pts[k], pts[k + 1]
                d.line([(x0, y0), (x0 + (x1 - x0) * prog, y0 + (y1 - y0) * prog)], fill=(*accent(), 255), width=7)
            for k, ((x, y), p) in enumerate(zip(pts, located)):
                a = _ease((t - 0.2 - k * seg_time) / 0.35)
                if a <= 0:
                    continue
                d.ellipse([x - 16, y - 16, x + 16, y + 16], fill=(*accent(), int(255 * a)), outline=(20, 20, 20, int(255 * a)), width=4)
                right = x < width / 2  # label on the open side
                tx = x + 34 if right else x - 34
                stop_lines, f_fit = fit_lines(d, p["label"].upper(), f_lab, 380, 2)
                for j, line in enumerate(stop_lines):
                    _text(d, (tx, y - 18 + 36 * j), line, font=f_fit, fill=(*WHITE, int(255 * a)), anchor="lm" if right else "rm")
            yield im

    return _encode(frames(), out, n, width=width, height=height)


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


def _render_defense_vertical(spec: dict[str, Any], out: Path, width: int, height: int) -> Path | None:
    """The 9:16 rolling-stone door (2026-11-02 design review, 3.5/10 -> rebuilt as two linked views): a plan on
    top (the corridor running across, a slot cut into its wall, the disc edge-on as a gold slab between INSIDE in
    teal and OUTSIDE in coral, a curved arrow for its path) and an elevation below (the doorway face-on, the disc
    rolling out of its recess to block it, a standing figure for scale). The disc turns by distance / radius, so
    it visibly rolls, overshoots a little and settles. Words only from diagram_labels; tagged ILLUSTRATIVE."""
    labels = diagram_labels("defense", spec)
    title = diagram_title("defense", spec)
    g = _Grid(width, height)
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)

    title_lines, f_title = _fit_title(g, title, S(811 - 250))          # clear of the tag at the top right
    title_base = [382 + 64 * j for j in range(len(title_lines))]
    f_lab = get_font("Montserrat", g.f(30), weight=600)
    f_side = get_font("Montserrat", g.f(30), weight=700)

    # ---- plan (design y)
    ph = title_base[-1] + 50                 # the plan's heading baseline
    rock_top = ph + 12
    slot_top = rock_top + 6
    c_top = slot_top + 74                    # the corridor's walls
    c_bot = c_top + 64
    rock_bot = c_bot + 42
    door_x = 600.0                           # both views share the door's axis
    slab_t, slab_len = 20.0, 72.0
    slab_open, slab_shut = slot_top + 1, c_top - 4
    # ---- elevation
    eh = max(rock_bot + 50, 742)              # its heading baseline
    face_top = eh + 14
    floor = 1022.0
    r = 100.0
    door_w, door_h = 124.0, 172.0
    open_x = door_x - 232
    person_h = 222.0
    person_x = 806.0

    sky = _rock_ground(width, height, lambda xs: xs * 0 + height * 2)       # the ink ground with its fine noise
    base = sky.copy()
    pen = _Pen((0, 0, width, height))
    rock = tuple(int(a + (b - a) * 0.16) for a, b in zip(brand.INK, brand.GOLD_DIM))
    hatch = (*brand.GOLD_DIM, 120)
    teal_floor = tuple(int(a + (b - a) * 0.14) for a, b in zip(brand.INK, brand.TEAL))
    coral_floor = tuple(int(a + (b - a) * 0.14) for a, b in zip(brand.INK, brand.CORAL))
    px0, px1 = X(65), X(888)
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
    pen.rect(px0, Y(floor), px1, Y(1050), fill=(*tuple(int(v * 0.8) for v in rock), 255))
    _hatch(pen, (px0, Y(floor), px1, Y(1050)), S(10), hatch)
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

    arrow_pts = []
    for j in range(25):
        a = -math.pi / 2 + math.pi * j / 24
        arrow_pts.append((door_x + 26 + 30 * math.cos(a), (slot_top + c_bot) / 2 + (c_bot - slot_top) / 2 * 0.82 * math.sin(a)))
    arrow_box = (X(door_x), Y(slot_top - 6), X(door_x + 80), Y(c_bot + 10))

    def arrow(progress: float) -> Image.Image | None:
        if progress <= 0:
            return None
        ap = _Pen(arrow_box)
        pts = g.pts(arrow_pts)
        upto = _path_len(pts) * progress
        seg = _sub_path(pts, [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)], 0, upto)
        if len(seg) > 1:
            ap.line(seg[:-1] if progress >= 1 else seg, (*GOLD, 255), S(3.5))
            if progress >= 1:
                _chevron(ap, pts[-1], (pts[-1][0] - pts[-3][0], pts[-1][1] - pts[-3][1]), S(16), (*GOLD, 255))
        return ap.image()

    arrow_full = arrow(1.0)
    leaders = _Pen((0, 0, width, height))
    hole_xy = (door_x, floor - r)
    leaders.line(g.pts([(318, eh + 46), (door_x - 70, eh + 46), (door_x - 52, floor - 2 * r + 22)]),
                 (*brand.GOLD_DIM, 255), S(2))
    leaders.ellipse(X(door_x - 52), Y(floor - 2 * r + 22), S(4), S(4), fill=(*GOLD, 255))
    # the central opening's words: two lines left of the recess where they fit there, else one line
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    recess_x0 = open_x - r - 12
    op_lines, _f, short = fit_text(probe, labels["opening"], "Montserrat", g.f(30), int(S(recess_x0 - 79 - 12)), 2,
                                   g.f(30), weight=700, quiet=True)
    if short or max(probe.textlength(ln, font=f_side) for ln in op_lines) > S(recess_x0 - 79 - 12):
        op_lines = [labels["opening"]]
    op_end = 79 + max(probe.textlength(ln, font=f_side) for ln in op_lines) / g.k
    leaders.line(g.pts([(op_end + 12, floor - r), (hole_xy[0] - r * 0.16 - 6, floor - r)]), (*brand.GOLD_DIM, 255),
                 S(2))
    leaders.ellipse(X(hole_xy[0] - r * 0.16 - 6), Y(floor - r), S(4), S(4), fill=(*GOLD, 255))
    leader_sprite = leaders.sprite()

    def art(t: float, final: bool = False) -> Image.Image:
        im = base.copy()
        p = 1.0 if final else roll(t)
        # plan: the slab moves across the corridor
        top = slab_open + (slab_shut - slab_open) * p
        d = ImageDraw.Draw(im, "RGBA")
        slab = _Pen((X(door_x - 16), Y(top - 4), X(door_x + 16), Y(top + slab_len + 4)))
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
        del d
        leader_sprite.draw(im, 1.0 if final else _ease((t - 3.55) / 0.45))
        return im

    final = art(0, final=True)
    timed = []
    for j, (ln, yb) in enumerate(zip(title_lines, title_base)):
        timed.append((f"title{j}", _plan(final, (X(77), Y(yb)), ln, f_title, WHITE, "ls"), 0.1 + 0.08 * j))
    tag, pill = _tag_pill(final, g, 888, 342, "ILLUSTRATIVE")
    timed.append(("tag", tag, 0.3))
    timed.append(("plan_heading", _plan(final, (X(79), Y(ph)), labels["track"], f_lab, MUTED, "ls",
                                            track=S(2)), 0.35))
    timed.append(("inside", _plan(final, (X(84), Y((c_top + c_bot) / 2)), "INSIDE", f_side, brand.TEAL, "lm",
                                      track=S(3)), 0.6))
    timed.append(("outside", _plan(final, (X(870), Y((c_top + c_bot) / 2)), "OUTSIDE", f_side, brand.CORAL,
                                       "rm", track=S(3)), 0.7))
    timed.append(("elevation_heading", _plan(final, (X(79), Y(eh)), labels["opening_detail"], f_lab, MUTED,
                                                 "ls", track=S(2)), 0.45))
    timed.append(("disc", _plan(final, (X(79), Y(eh + 46)), labels["disc"], f_side, GOLD, "lm"), 3.6))
    for j, ln in enumerate(op_lines):
        yc = floor - r + 36 * (j - (len(op_lines) - 1) / 2)
        timed.append((f"opening{j}", _plan(final, (X(79), Y(yc)), ln, f_side, WHITE, "lm"), 3.75 + 0.06 * j))
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
    """Animated architectural diagram of an ancient subterranean rolling stone door.
    Shows the stone disc rolling smoothly along its carved track from an interior alcove
    to securely lock the defensive threshold against surface invaders.

    9:16 (2026-11-02 design review, scored 3.5/10): rebuilt in _render_defense_vertical as two linked views (plan
    and elevation), a disc that truly rolls (rotation = distance / radius) and brand colours only; 16:9 unchanged."""
    if width <= height:
        return _render_defense_vertical(spec, out, width, height)
    labels = diagram_labels("defense", spec)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    top, bottom_bound = get_text_bounds(width, height)
    title = diagram_title("defense", spec)

    f_title = fit_font(ImageDraw.Draw(Image.new("RGB", (10, 10))), title, "Anton", 42 if width > height else 34, centred_text_width(width, height), 22)
    f_stat = get_font("Anton", 30 if width > height else 24)
    f_lab = get_font("Montserrat", 22 if width > height else 18)
    f_sub = get_font("Montserrat", 16 if width > height else 14)

    floor_y = int(bottom_bound - (60 if width > height else 90))
    ceiling_y = int(top + (100 if width > height else 130))
    corridor_h = floor_y - ceiling_y
    disc_r = int(corridor_h * (0.58 if width > height else 0.40))
    alcove_x = int(width * 0.30 if width > height else width * 0.35)
    sealed_x = int(width * 0.52 if width > height else width * 0.54)
    track_y = floor_y - disc_r + 14

    bg = Image.new("RGB", (width, height), SEA)
    d0 = ImageDraw.Draw(bg, "RGBA")

    # Static architectural tunnel structure: solid rock above and below
    for y in range(top + 40, ceiling_y):
        k = (y - top) / max(1, ceiling_y - top)
        d0.line([(0, y), (width, y)], fill=(int(65 - 15 * k), int(52 - 12 * k), int(42 - 10 * k), 255))
    for y in range(floor_y, height):
        k = (y - floor_y) / max(1, height - floor_y)
        d0.line([(0, y), (width, y)], fill=(int(55 - 20 * k), int(44 - 16 * k), int(34 - 12 * k), 255))

    # Architectural blueprint cross-hatching across solid bedrock
    _draw_cross_hatching(d0, 0, top + 40, width, ceiling_y, spacing=16, fill=(130, 115, 95, 40))
    _draw_cross_hatching(d0, 0, floor_y, width, height, spacing=16, fill=(120, 105, 85, 40))

    # Corridor walls
    d0.line([(0, ceiling_y), (width, ceiling_y)], fill=(*MUTED, 255), width=3)
    d0.line([(0, floor_y), (width, floor_y)], fill=(*MUTED, 255), width=3)

    # Alcove groove in ceiling and floor
    d0.rectangle([alcove_x - disc_r - 20, ceiling_y - 25, sealed_x + 15, ceiling_y], fill=(22, 19, 16, 255))
    d0.rectangle([alcove_x - disc_r - 20, floor_y, sealed_x + 15, floor_y + 25], fill=(22, 19, 16, 255))

    # Title
    bx0, _, bx1, _ = get_text_box(width, height)  # labels at the edges stay inside the Reel box (Shorts' icon column)
    _text(d0, (int(width * 0.08), top + 16), title, font=f_title, fill=(*WHITE, 255), anchor="lm")

    # Defensive zone labels with ample breathing room
    if width > height:
        _text(d0, (int(width * 0.12), ceiling_y + 16), "DEFENDER INTERIOR", font=f_lab, fill=(*accent(), 230), anchor="lm")
        _text(d0, (int(width * 0.12), ceiling_y + 54), "SAFE REFUGE ZONE", font=f_sub, fill=(*MUTED, 200), anchor="lm")
        _text(d0, (width - int(width * 0.12), ceiling_y + 16), "SURFACE PASSAGE", font=f_lab, fill=(210, 90, 80, 230), anchor="rm")
        _text(d0, (width - int(width * 0.12), ceiling_y + 54), "EXTERNAL INVADERS", font=f_sub, fill=(*MUTED, 200), anchor="rm")
    else:
        _text(d0, (bx0, ceiling_y + 16), "DEFENDER INTERIOR", font=f_sub, fill=(*accent(), 230), anchor="la")
        _text(d0, (bx1, ceiling_y + 16), "SURFACE PASSAGE", font=f_sub, fill=(210, 90, 80, 230), anchor="ra")

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = bg.copy()
            d = ImageDraw.Draw(im, "RGBA")

            # Stone roll progression: starts at 0.5s, locks at 2.4s
            roll = _ease((t - 0.5) / 1.9)
            cx = int(alcove_x + (sealed_x - alcove_x) * roll)
            rot = roll * math.pi * 1.5

            # Draw stone disc with concentric basalt texture
            d.ellipse([cx - disc_r, track_y - disc_r, cx + disc_r, track_y + disc_r], fill=(50, 48, 46, 255), outline=(*GOLD, 220), width=4)
            d.ellipse([cx - int(disc_r * 0.65), track_y - int(disc_r * 0.65), cx + int(disc_r * 0.65), track_y + int(disc_r * 0.65)],
                      outline=(75, 70, 65, 180), width=2)

            for a_deg in (0, 45, 90, 135, 180, 225, 270, 315):
                rad = math.radians(a_deg) + rot
                x0 = cx + int(disc_r * 0.28 * math.cos(rad))
                y0 = track_y + int(disc_r * 0.28 * math.sin(rad))
                x1 = cx + int(disc_r * 0.85 * math.cos(rad))
                y1 = track_y + int(disc_r * 0.85 * math.sin(rad))
                d.line([(x0, y0), (x1, y1)], fill=(75, 70, 65, 160), width=2)

            # Central spear port
            port_r = max(10, int(disc_r * 0.14))
            d.ellipse([cx - port_r, track_y - port_r, cx + port_r, track_y + port_r], fill=(16, 14, 12, 255), outline=(*GOLD, 240), width=2)

            # Annotations fade in at t >= 2.0s
            a_callout = int(255 * _ease((t - 2.0) / 0.8))
            if a_callout > 0:
                call1_x = width - int(width * 0.10) if width > height else bx1
                leader1_y = ceiling_y - 45 if width < height else track_y - disc_r - 35
                d.line([(cx, track_y - disc_r), (cx + 35, leader1_y), (call1_x, leader1_y)],
                       fill=(*accent(), int(180 * a_callout / 255)), width=2)
                _text(d, (call1_x, leader1_y - 10), labels["disc"], font=f_stat, fill=(*accent(), a_callout), anchor="rd")
                _text(d, (call1_x, leader1_y + 14), labels["passage"], font=f_sub, fill=(*WHITE, a_callout), anchor="ra")

                call2_x = int(width * 0.10) if width > height else bx0
                leader2_y = track_y
                d.line([(cx - port_r, leader2_y), (cx - disc_r - 20, leader2_y), (call2_x, leader2_y)],
                       fill=(*accent(), int(180 * a_callout / 255)), width=2)
                _text(d, (call2_x, leader2_y - 10), labels["opening"], font=f_stat, fill=(*accent(), a_callout), anchor="ld")
                _text(d, (call2_x, leader2_y + 14), labels["opening_detail"], font=f_sub, fill=(*WHITE, a_callout), anchor="la")

                call3_y = min(bottom_bound - 10, floor_y + 30)
                if width < height:
                    _text(d, (width // 2, call3_y - 10), labels["track"], font=get_font("Montserrat", 18), fill=(*WHITE, a_callout), anchor="ma")
                    _text(d, (width // 2, call3_y + 16), labels["summary"], font=get_font("Montserrat", 14), fill=(*MUTED, a_callout), anchor="ma")
                else:
                    _text(d, (width // 2, call3_y), labels["track"] + " · " + labels["summary"],
                          font=f_lab, fill=(*WHITE, a_callout), anchor="ma")

            yield im

    return _encode(frames(), out, n, width=width, height=height)


# ----------------------------------------------- ventilation & airflow dynamics
def _render_ventilation_vertical(spec: dict[str, Any], out: Path, width: int, height: int) -> Path | None:
    """The 9:16 ventilation section (2026-11-02 design review, 3.5/10 -> rebuilt): a hill's ground profile near
    the top of the text box, one deep shaft to the bottom ending in a well, four levels of rooms off it and a
    second, higher opening. Air in as a TEAL dashed path down the shaft and into the levels, air out as a CORAL
    path through the second shaft (no sky blue or red); the title is the plain head of diagram_title, and the
    only words are diagram_labels' (the intake and the outlet are both the AIR SHAFT), tagged ILLUSTRATIVE."""
    import random

    import numpy as np
    labels = diagram_labels("ventilation", spec)
    title = _plain_title(diagram_title("ventilation", spec))
    g = _Grid(width, height)
    X, Y, S = g.x, g.y, g.s
    n = int(CLIP_SECONDS * VIDEO_FPS)

    title_lines, f_title = _fit_title(g, title, S(811))
    title_base = [382 + 64 * j for j in range(len(title_lines))]
    sub_base = title_base[-1] + 48
    f_lab = get_font("Montserrat", g.f(30), weight=600)
    f_side = get_font("Montserrat", g.f(30), weight=700)

    ground_lo = sub_base + 92            # the ground at the frame's edges (design y)
    hill_x, hill_h, hill_w = 690.0, 40.0, 190.0

    def ground_d(x: float) -> float:
        return ground_lo - hill_h * math.exp(-((x - hill_x) / hill_w) ** 2)

    in_x, in_w = 300.0, 36.0             # the deep shaft
    ex_x, ex_w = 690.0, 26.0             # the outlet shaft, through the hill's crown
    well_d = 990.0
    floors = [ground_lo + (well_d - 60 - ground_lo) * f for f in (0.2, 0.44, 0.68, 0.92)]
    gal_h = 30.0
    rnd = random.Random(11)
    rooms = []
    for k, fl in enumerate(floors):
        x = in_x + in_w / 2 + rnd.uniform(26, 50)
        while x < ex_x - ex_w / 2 - 90:
            w = rnd.uniform(84, 120)
            w = min(w, ex_x - ex_w / 2 - 30 - x)
            rooms.append((x, x + w, fl, rnd.uniform(62, 74), 300 + 10 * k + len(rooms)))
            x += w + rnd.uniform(30, 52)

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
        return np.array([Y(ground_d(65 + (x - g.bx0) / g.k)) for x in xs], dtype=np.float32)

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
    starts_w = [s + 0.55 for s in starts_c[1:]] + [starts_c[-1] + 0.75]
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
        timed.append((f"title{j}", _plan(final, (X(77), Y(yb)), ln, f_title, WHITE, "ls"), 0.1 + 0.08 * j))
    timed.append(("subtitle", _plan(final, (X(79), Y(sub_base)), labels["airflow"], f_lab, MUTED, "ls",
                                        track=S(2)), 0.3))
    tag, pill = _tag_pill(final, g, 888, sub_base - 30, labels["summary"])
    timed.append(("tag", tag, 0.35))
    shaft_y = (ground_d(in_x) + floors[0] - gal_h) / 2 + 6
    leaders = _Pen((0, 0, width, height))
    in_label = _plan(final, (X(79), Y(shaft_y)), labels["shaft"], f_side, brand.TEAL, "lm")
    leaders.line([(in_label.box[2] + S(10), Y(shaft_y)), (X(in_x - in_w / 2 - 6), Y(shaft_y))],
                 (*brand.GOLD_DIM, 255), S(2))
    out_label = _plan(final, (X(886), Y(shaft_y)), labels["shaft"], f_side, brand.CORAL, "rm")
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
    """Animated aerodynamic simulation of subterranean ventilation.
    Visualizes cool surface air descending through the 55m master shaft
    and warm stale air venting upward through secondary micro-ducts.

    9:16 (2026-11-02 design review, scored 3.5/10): rebuilt in _render_ventilation_vertical as a section under a
    hill, a deep shaft to a well, four levels, TEAL air in and CORAL air out, a plain title and diagram_labels'
    words only (the old frame drew sky blue and red and unchecked claims); 16:9 unchanged."""
    if width <= height:
        return _render_ventilation_vertical(spec, out, width, height)
    labels = diagram_labels("ventilation", spec)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    top, bottom_bound = get_text_bounds(width, height)
    title = diagram_title("ventilation", spec)

    f_title = fit_font(ImageDraw.Draw(Image.new("RGB", (10, 10))), title, "Anton", 44 if width > height else 50, centred_text_width(width, height), 26)
    f_stat = get_font("Anton", 32 if width > height else 28)
    f_lab = get_font("Montserrat", 24 if width > height else 22)
    f_sub = get_font("Montserrat", 18 if width > height else 18)

    ground_y = int(top + (50 if width > height else 80))
    shaft_x = int(width * 0.28) if width > height else int(width * 0.36)
    shaft_w = 40 if width > height else 48
    exhaust_x = int(width * 0.68) if width > height else int(width * 0.64)
    exhaust_w = 26 if width > height else 32
    deep_y = int(bottom_bound - 45)

    bg = Image.new("RGB", (width, height), SEA)
    d0 = ImageDraw.Draw(bg, "RGBA")

    for y in range(ground_y, height):
        k = (y - ground_y) / max(1, height - ground_y)
        d0.line([(0, y), (width, y)], fill=(int(60 - 25 * k), int(48 - 20 * k), int(38 - 16 * k), 255))
    _draw_cross_hatching(d0, 0, ground_y, width, height, spacing=18, fill=(110, 95, 75, 40))
    d0.line([(0, ground_y), (width, ground_y)], fill=(*MUTED, 255), width=3)
    bx0, _, bx1, _ = get_text_box(width, height)  # labels at the edges stay inside the Reel box (Shorts' icon column)
    _text(d0, (width - int(width * 0.08) if width > height else bx1, ground_y - 24), "GROUND LEVEL", font=get_font("Montserrat", 22), fill=(*MUTED, 255), anchor="rm")
    _text(d0, (int(width * 0.08), top + 16), title, font=f_title, fill=(*WHITE, 255), anchor="lm")

    level_ys = [ground_y + int((deep_y - ground_y) * f) for f in (0.28, 0.52, 0.75, 0.94)]
    bottom_gallery_floor = level_ys[-1] + 14

    # Shaft carve-outs: cleanly terminated flush with lowest horizontal gallery floor
    d0.rectangle([shaft_x - shaft_w // 2, ground_y, shaft_x + shaft_w // 2, bottom_gallery_floor], fill=(16, 14, 12, 255))
    d0.rectangle([exhaust_x - exhaust_w // 2, ground_y, exhaust_x + exhaust_w // 2, bottom_gallery_floor], fill=(16, 14, 12, 255))

    for ly in level_ys:
        d0.rectangle([shaft_x + shaft_w // 2, ly - 14, exhaust_x - exhaust_w // 2, ly + 14], fill=(22, 19, 16, 255))

    CYAN_AIR = (80, 195, 245)
    AMBER_HEAT = (245, 150, 60)

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = bg.copy()
            d = ImageDraw.Draw(im, "RGBA")

            # Cool air descends cleanly through master shaft into lowest level
            arrow_speed = (t * 110) % 60
            for y_arr in range(ground_y - 20, level_ys[-1], 45):
                curr_y = int(y_arr + arrow_speed)
                if ground_y - 15 <= curr_y <= level_ys[-1] - 8:
                    d.polygon([(shaft_x, curr_y + 14), (shaft_x - 12, curr_y), (shaft_x + 12, curr_y)], fill=(*CYAN_AIR, 220))

            # Cross-ventilation airflow across horizontal living galleries
            for ly in level_ys:
                h_shift = (t * 80) % 50
                for x_arr in range(shaft_x + shaft_w // 2 + 20, exhaust_x - exhaust_w // 2 - 20, 50):
                    curr_x = int(x_arr + h_shift)
                    if shaft_x + shaft_w // 2 < curr_x < exhaust_x - exhaust_w // 2:
                        d.polygon([(curr_x + 12, ly), (curr_x, ly - 8), (curr_x, ly + 8)], fill=(*CYAN_AIR, 180))

            # Warm exhaust air ascends from lowest floor up to the surface
            exhaust_speed = (t * 90) % 50
            for y_arr in range(level_ys[-1] - 10, ground_y - 30, -45):
                curr_y = int(y_arr - exhaust_speed)
                if ground_y - 20 <= curr_y <= level_ys[-1] - 8:
                    d.polygon([(exhaust_x, curr_y - 12), (exhaust_x - 10, curr_y), (exhaust_x + 10, curr_y)], fill=(*AMBER_HEAT, 210))

            a_callout = int(255 * _ease((t - 1.5) / 0.8))
            if a_callout > 0:
                call1_x = int(width * 0.08) if width > height else bx0
                shelf1_y = ground_y + (80 if width > height else 60)
                d.line([(shaft_x - shaft_w // 2, shelf1_y), (call1_x, shelf1_y)], fill=(*CYAN_AIR, int(180 * a_callout / 255)), width=2)
                _text(d, (call1_x, shelf1_y - 10), labels["shaft"], font=f_stat, fill=(*CYAN_AIR, a_callout), anchor="ld")
                _text(d, (call1_x, shelf1_y + 14), "DRAWS COOL DENSE AIR DOWN", font=f_sub, fill=(*WHITE, a_callout), anchor="la")

                call2_x = width - int(width * 0.08) if width > height else bx1
                shelf2_y = ground_y + (100 if width > height else 80)
                d.line([(exhaust_x + exhaust_w // 2, shelf2_y), (call2_x, shelf2_y)], fill=(*AMBER_HEAT, int(180 * a_callout / 255)), width=2)
                _text(d, (call2_x, shelf2_y - 10), "CONVECTION EXHAUST", font=f_stat, fill=(*AMBER_HEAT, a_callout), anchor="rd")
                _text(d, (call2_x, shelf2_y + 14), labels["exhaust_detail"], font=f_sub, fill=(*WHITE, a_callout), anchor="ra")

                call3_y = min(bottom_bound - 10, bottom_gallery_floor + 20)
                if width < height:
                    _text(d, (width // 2, call3_y - 10), labels["airflow"], font=get_font("Montserrat", 20), fill=(*accent(), a_callout), anchor="ma")
                    _text(d, (width // 2, call3_y + 16), labels["summary"], font=get_font("Montserrat", 15), fill=(*WHITE, a_callout), anchor="ma")
                else:
                    _text(d, (width // 2, call3_y), labels["airflow"] + " · " + labels["summary"],
                          font=f_lab, fill=(*accent(), a_callout), anchor="ma")

            yield im

    return _encode(frames(), out, n, width=width, height=height)


# ----------------------------------------------- LiDAR subterranean radar scan
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
    # Diagrams only when the script declares them (and so the fact check sees their text); a topic-ID
    # substring used to add them to any topic named "door", "fortress", "hypogeum" or "catacomb".
    if payload.get("rolling_stone_defense"):
        jobs.append(("defense", lambda: render_rolling_stone_defense(payload.get("rolling_stone_defense") or {}, work_dir / "graphic_defense.mp4", width=width, height=height)))
    if payload.get("ventilation_airflow"):
        jobs.append(("ventilation", lambda: render_ventilation_airflow(payload.get("ventilation_airflow") or {}, work_dir / "graphic_ventilation.mp4", width=width, height=height)))
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
        if photo and name == "numbers":
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
                media = [photo] if name == "numbers" and photo else []
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
