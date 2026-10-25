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
from .typography import fit_font, get_font
from .art_direction import accent, type_scale, count_start, push_in, MOTION
from .encoding import bounded_encode

log = logging.getLogger(__name__)

CLIP_SECONDS = 6.0
W, H = VIDEO_WIDTH, VIDEO_HEIGHT
SEA, LAND, HOME, BORDER, GRID = (13, 16, 21), (40, 43, 48), (92, 74, 42), (96, 98, 104), (24, 28, 34)
GOLD, WHITE, MUTED = (255, 215, 0), (255, 255, 255), (170, 170, 175)
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
LAND_TINT, HOME_TINT = (74, 76, 82), (122, 100, 66)  # home: warm but not a flood of gold (big countries)


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
                reserved: Tuple[Tuple[float, float, float, float], ...] = ()) -> Image.Image:
    """The map background: hillshaded land (the topic's country in warm gold), a navy sea with a
    soft coastal glow, thin borders, and neighbouring countries named at Natural Earth's label points."""
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
        outside = box[2] < fx0 or box[0] > fx1 or box[3] < fy0 or box[1] > fy1
        if not (inside or outside) or any(not (box[2] < p[0] or box[0] > p[2] or box[3] < p[1] or box[1] > p[3]) for p in placed):
            continue
        placed.append(box)
        _text(draw, (x, y), name, font=font, fill=(150, 152, 160), anchor="mm")
    return base


def render_map(place: Dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
    """Regional locator map: eases from a ~40° view to ~18° around the site, pin drop, label."""
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
    label_y = int(height * 0.18) if width > height else max(TEXT_TOP + 70, height // 2 - 210)
    # Keep neighbours' names clear of the place label and the pin in the final close-up: the band from
    # the label's top to just below the pin, across the label's width plus a margin (output pixels).
    half_w = min(width // 2, 60 + max(len(label) * 22, len(country) * 18))
    to_base = lambda x, y: (final_view[0] + x * (final_view[2] - final_view[0]) / width,  # noqa: E731
                            final_view[1] + y * (final_view[3] - final_view[1]) / height)
    reserved = (to_base(width // 2 - half_w, label_y - 90) + to_base(width // 2 + half_w, height // 2 + 70),)
    base = relief_base(lat0, lon0, bw, bh, kx, coslat, geo.find_country(place["country"]), project, final_view,
                       label_bottom_fraction=get_text_bounds(width, height)[1] / height, reserved=reserved)

    n = int(CLIP_SECONDS * VIDEO_FPS)

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            zoom = span_wide / (span_wide + (span_close - span_wide) * _ease(t / 1.6))
            cw, ch = bw / zoom, bh / zoom
            box = (bw / 2 - cw / 2, bh / 2 - ch / 2, bw / 2 + cw / 2, bh / 2 + ch / 2)
            im = base.resize((width, height), Image.BICUBIC, box=box).convert("RGB")
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
            im = _label_box(im, cx, label_y, label, country, _ease((t - 1.5) / 0.4))
            yield im

    return _encode(frames(), out, n, width=width, height=height)


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


def render_timeline(events: List[Dict[str, Any]], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
    """Timeline: the spine draws down (or across in landscape), then each date appears in order.
    Entries are evenly spaced: an ordered sequence, not a time scale."""
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
                    lines = _wrap(d, e["label"], f_lab, max_label_w)[:2]
                    for j, line in enumerate(lines):
                        if above:
                            ly = spine_y - 74 - 34 * (len(lines) - 1 - j)
                            _text(d, (x, ly), line, font=f_lab, fill=(*WHITE, a), anchor="ms")
                        else:
                            ly = spine_y + 74 + 34 * j
                            _text(d, (x, ly), line, font=f_lab, fill=(*WHITE, a), anchor="mt")
                yield im
    else:
        # Vertical: spine draws downward
        x0 = 190
        # The spine spans the box with room for the first year's glyphs above its first dot and the last
        # entry's two label lines below its last one (both were drawn past the bounds before, measured 2026-10-24).
        y_first, y_last = top + 70, bottom - 110
        step = (y_last - y_first) / (len(events) - 1)
        f_year, f_lab = get_font("Anton", 74), get_font("Montserrat", 34)

        def frames():
            for i in range(n):
                t = i / VIDEO_FPS
                im = bg.copy()
                d = ImageDraw.Draw(im, "RGBA")
                spine = y_first + (y_last - y_first) * _ease(t / 1.0)
                d.line([(x0, y_first), (x0, spine)], fill=(*accent(), 255), width=6)
                for k, e in enumerate(events):
                    appear = _ease((t - 0.4 - 0.45 * k) / 0.4)
                    if appear <= 0:
                        continue
                    y = y_first + step * k
                    a = int(255 * appear)
                    r = 16 * appear
                    d.ellipse([x0 - r, y - r, x0 + r, y + r], fill=(*accent(), a))
                    xt = x0 + 50 + int(30 * (1 - appear))
                    _text(d, (xt, y - 8), year_text(e["year"], e.get("approximate", False)), font=f_year, fill=(*GOLD, a), anchor="ls")
                    for j, line in enumerate(_wrap(d, e["label"], f_lab, min(width - xt - 90, get_text_box(width, height)[2] - xt))[:2]):
                        _text(d, (xt, y + 18 + 42 * j), line, font=f_lab, fill=(*WHITE, a), anchor="lt")
                yield im

    return _encode(frames(), out, n, width=width, height=height)


# -------------------------------------------------------------------- scale
def pick_reference(feet: float) -> Optional[Tuple[str, float, str]]:
    """The reference whose size is closest (by ratio) to `feet`, if within 1/3x-3x."""
    ref = selected_reference({"value": feet, "unit": "ft"})
    return (ref["label"], ref["feet"], ref["source_url"]) if ref else None


def render_scale(fact: Dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
    """Two bars, the subject (gold) and a landmark (grey), growing to true proportion.
    Depths grow downward from a ground line; heights grow upward."""
    feet = fact["value"] * (3.28084 if fact["unit"] == "m" else 1.0)
    ref = pick_reference(feet)
    if ref is None or feet <= 0:
        return None
    ref_label, ref_feet, _ = ref
    depth = fact.get("dimension") == "depth"
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
                    lines = _wrap(d, label.upper(), cur_lab_font, max_label_w)[:2]
                    for j, line in enumerate(lines):
                        lh = 32 if width > height else 36
                        ly = ty + 46 + lh * j if depth else ty - 46 - lh * (len(lines) - 1 - j)
                        _text(d, (cx, ly), line, font=cur_lab_font, fill=(*WHITE, a), anchor="mm")
            yield im

    return _encode(frames(), out, n, width=width, height=height)


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


def render_stat_cards(cards: List[Dict[str, Any]], out: Path, photo: Optional[Path] = None, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
    """1-2 headline figures counting up to their value, each with its label, over a dark photo backdrop."""
    cards = cards[:2]
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
    f_lab = get_font("Montserrat", ts["label"])

    def shown(card: Dict[str, Any], p: float) -> str:
        pre, num, post = _split_number(card["value"])
        if num is None:
            return card["value"]
        decimals = len(str(num).split(".")[1]) if num % 1 else 0
        cur = num * p
        txt = f"{cur:,.{decimals}f}" if decimals else f"{round(cur):,}"
        return f"{pre}{txt}{post}"

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = _pushed_in(bg, push_in(t, CLIP_SECONDS))  # a slow push on the backdrop: a card never sits static
            d = ImageDraw.Draw(im, "RGBA")
            for k, (card, (cx, cy)) in enumerate(zip(cards, centres)):
                start = count_start(k)  # every count rests by count_done_by of the clip
                a = _ease((t - start) / MOTION["reveal"])
                if a <= 0:
                    continue
                count = _ease((t - start) / MOTION["count"])
                value_font = fit_font(d, card["value"].upper(), "Anton", f_num_size, max_w, 50)
                _text(d, (cx, cy - 30), shown(card, count).upper(), font=value_font, fill=(*GOLD, int(255 * a)), anchor="mm")
                d.line([(cx - 80, cy + 60), (cx + 80, cy + 60)], fill=(*WHITE, int(160 * a)), width=3)
                for j, line in enumerate(_wrap(d, card["label"].upper(), f_lab, max_w)[:2]):
                    _text(d, (cx, cy + 105 + 46 * j), line, font=f_lab, fill=(*WHITE, int(255 * a)), anchor="mm")
            yield im

    return _encode(frames(), out, n, width=width, height=height)


# ------------------------------------------------------------ cross-section
def render_cross_section(cs: Dict[str, Any], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
    """A cutaway: sky, ground line, strata, and a shaft that a gold marker descends; each sourced
    level appears as the marker reaches its depth (true proportion), labelled in ft and m."""
    import random
    levels = sorted(cs["levels"], key=lambda lv: lv["depth_ft"])
    total = float(cs["total_depth_ft"])
    if total <= 0 or len(levels) < 2:
        return None
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
                    lines = _wrap(d, lv["label"].upper(), f_lab, label_w)[:2]
                    for j, line in enumerate(lines):
                        _text(d, (label_x, y_t + (j - (len(lines) - 1) / 2) * lh), line, font=f_lab, fill=(*WHITE, a), anchor="lm")
                else:
                    d.rectangle([shaft_x + 22, y_phys - 14, label_x - 25, y_phys + 14], fill=(22, 19, 16, a))
                    _text(d, (shaft_x - 50, y_phys - 10), f"{lv['depth_ft']:,.0f} FT", font=f_dep, fill=(*GOLD, a), anchor="rm")
                    _text(d, (shaft_x - 50, y_phys + 18), f"({lv['depth_ft'] / 3.28084:,.0f} M)", font=f_sub, fill=(*MUTED, a), anchor="rm")
                    lines = _wrap(d, lv["label"].upper(), f_lab, label_w)[:2]
                    for j, line in enumerate(lines):
                        _text(d, (label_x, y_phys + (j - (len(lines) - 1) / 2) * lh), line, font=f_lab, fill=(*WHITE, a), anchor="lm")
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
                for j, line in enumerate(_wrap(d, p["label"].upper(), f_lab, 380)[:2]):
                    _text(d, (tx, y - 18 + 36 * j), line, font=f_lab, fill=(*WHITE, int(255 * a)), anchor="lm" if right else "rm")
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


def render_rolling_stone_defense(
    spec: Dict[str, Any],
    out: Path,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
) -> Optional[Path]:
    """Animated architectural diagram of an ancient subterranean rolling stone door.
    Shows the stone disc rolling smoothly along its carved track from an interior alcove
    to securely lock the defensive threshold against surface invaders."""
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
def render_ventilation_airflow(
    spec: Dict[str, Any],
    out: Path,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
) -> Optional[Path]:
    """Animated aerodynamic simulation of subterranean ventilation.
    Visualizes cool surface air descending through the 55m master shaft
    and warm stale air venting upward through secondary micro-ducts."""
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
        jobs.append(("numbers", lambda: render_stat_cards(payload["stat_cards"], work_dir / "graphic_numbers.mp4", photo, width=width, height=height)))
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
    if place:
        jobs.append(("map", lambda: render_map(place, work_dir / "graphic_map.mp4", width=width, height=height)))
    if payload.get("timeline"):
        jobs.append(("timeline", lambda: render_timeline(payload["timeline"], work_dir / "graphic_timeline.mp4", width=width, height=height)))
    if payload.get("scale_fact"):
        jobs.append(("scale", lambda: render_scale(payload["scale_fact"], work_dir / "graphic_scale.mp4", width=width, height=height)))
    def _run_job(name, job):
        from .checkpoints import Checkpoints, file_inputs
        from .typography import FONT_FILES, FONTS_DIR
        from .lineage import sidecar
        source_files = [Path(__file__), Path(__file__).with_name("typography.py"),
                        *[FONTS_DIR / filename for filename in FONT_FILES.values()]]
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
