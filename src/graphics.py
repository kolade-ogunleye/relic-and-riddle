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

log = logging.getLogger(__name__)

CLIP_SECONDS = 6.0
W, H = VIDEO_WIDTH, VIDEO_HEIGHT
SEA, LAND, HOME, BORDER, GRID = (13, 16, 21), (40, 43, 48), (92, 74, 42), (96, 98, 104), (24, 28, 34)
GOLD, WHITE, MUTED = (255, 215, 0), (255, 255, 255), (170, 170, 175)
# Graphics text stays between these: the caption bar's top edge is at about y=1140
# (CAPTION_MARGIN_V 672 + a 92 pt line + padding), measured in a real render.
TEXT_TOP, TEXT_BOTTOM = 330, 1080

# Well-documented landmarks for scale comparisons: (label, feet, source).
REFERENCES = [
    ("Statue of Liberty", 305.0, "NPS: ground to torch tip, 305 ft 1 in"),
    ("Big Ben's tower", 316.0, "UK Parliament: Elizabeth Tower, 96 m"),
    ("Great Pyramid of Giza", 454.0, "about 138.5 m today"),
    ("An average US man", 5.75, "CDC NHANES: mean adult male height about 69 in"),
]
PERSON = "An average US man"


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


def _encode(frames: Iterable[Image.Image], out: Path, n: int) -> Path:
    """Raw RGB frames piped to ffmpeg (local only) -> H.264 MP4 + poster JPEG of the last frame."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(VIDEO_FPS),
           "-i", "-", "-frames:v", str(n), "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(out)]
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
    f1, f2 = get_font("Anton", 66), get_font("Montserrat", 30)
    w1 = draw.textlength(title.upper(), font=f1)
    w2 = draw.textlength(sub.upper(), font=f2)
    bw = int(max(w1, w2) + 70)
    d = ImageDraw.Draw(im, "RGBA")  # RGB frame + RGBA drawing = real alpha blending (fades work)
    a = int(255 * alpha)
    d.rounded_rectangle([cx - bw // 2, y - 62, cx + bw // 2, y + 66], radius=18, fill=(10, 10, 12, int(200 * alpha)),
                        outline=(*GOLD, int(220 * alpha)), width=2)
    _text(d, (cx, y - 18), title.upper(), font=f1, fill=(*WHITE, a), anchor="mm")
    _text(d, (cx, y + 38), sub.upper(), font=f2, fill=(*GOLD, a), anchor="mm")
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
                final_view: Tuple[float, float, float, float]) -> Image.Image:
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
                colour, width = ((*GOLD,), 7) if c is home else ((150, 150, 158), 3)
                draw.line([project(x, y) for x, y in ring] + [project(*ring[0])], fill=colour, width=width)

    # Neighbouring countries, named at Natural Earth's label points. A label is drawn only if it
    # collides with no other label and is either wholly inside the final close-up or wholly
    # outside it, so the last frame never shows half a name.
    font = get_font("Montserrat", 66)
    fx0, fy0, fx1, fy1 = final_view
    placed: List[Tuple[float, float, float, float]] = []
    for c in sorted(visible, key=lambda c: (c["label_rank"], -c["size"])):
        if c is home or not c["label_at"] or c["label_rank"] > 5:
            continue
        x, y = project(*c["label_at"])
        name = " ".join(c["display"].upper())  # letter-spaced small caps
        l, t, r, b = draw.textbbox((x, y), name, font=font, anchor="mm")
        box = (l - 30, t - 20, r + 30, b + 20)
        if box[0] < 0 or box[1] < 0 or box[2] > bw or box[3] > bh:
            continue
        inside = fx0 <= box[0] and box[2] <= fx1 and fy0 <= box[1] and box[3] <= fy1
        outside = box[2] < fx0 or box[0] > fx1 or box[3] < fy0 or box[1] > fy1
        if not (inside or outside) or any(not (box[2] < p[0] or box[0] > p[2] or box[3] < p[1] or box[1] > p[3]) for p in placed):
            continue
        placed.append(box)
        _text(draw, (x, y), name, font=font, fill=(150, 152, 160), anchor="mm")
    return base


def render_map(place: Dict[str, Any], out: Path) -> Optional[Path]:
    """Regional locator map: eases from a ~40° view to ~18° around the site, pin drop, label."""
    if not geo.available():
        return None
    lat0, lon0 = place["lat"], place["lon"]
    span_wide, span_close, scale = 40.0, 18.0, 3  # degrees of longitude across the frame (keeps neighbours in view)
    kx = W * scale / span_wide                  # px per degree of longitude in the base image
    coslat = max(0.2, math.cos(math.radians(lat0)))
    bw, bh = W * scale, H * scale

    # Equirectangular with the x axis shrunk by cos(lat) so shapes keep their proportions.
    def project(lon: float, lat: float) -> Tuple[float, float]:
        return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx

    zf = span_wide / span_close  # the close-up the camera ends on (labels must not be cut by it)
    final_view = (bw / 2 - bw / 2 / zf, bh / 2 - bh / 2 / zf, bw / 2 + bw / 2 / zf, bh / 2 + bh / 2 / zf)
    base = relief_base(lat0, lon0, bw, bh, kx, coslat, geo.find_country(place["country"]), project, final_view)

    n = int(CLIP_SECONDS * VIDEO_FPS)
    label = place["label"]
    country = place["country"]

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            zoom = span_wide / (span_wide + (span_close - span_wide) * _ease(t / 1.6))
            cw, ch = bw / zoom, bh / zoom
            box = (bw / 2 - cw / 2, bh / 2 - ch / 2, bw / 2 + cw / 2, bh / 2 + ch / 2)
            im = base.resize((W, H), Image.BICUBIC, box=box).convert("RGB")
            d = ImageDraw.Draw(im, "RGBA")
            cx, cy = W // 2, H // 2
            # Pulse rings after the pin lands.
            if t > 1.6:
                for k in range(2):
                    p = ((t - 1.6) * 0.8 + k * 0.5) % 1.0
                    r = 20 + 90 * p
                    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*GOLD, int(200 * (1 - p))), width=4)
            # Pin drop with a small bounce (1.8 s -> 2.3 s).
            if t >= 1.1:
                drop = _ease((t - 1.1) / 0.5)
                py = cy - 160 * (1 - drop) - (12 * math.sin(math.pi * min(1.0, (t - 1.6) / 0.25)) if 1.6 <= t <= 1.85 else 0)
                d.ellipse([cx - 22, py - 22, cx + 22, py + 22], fill=(*GOLD, 255), outline=(20, 20, 20, 255), width=4)
                d.ellipse([cx - 7, py - 7, cx + 7, py + 7], fill=(20, 20, 20, 255))
            im = _label_box(im, cx, max(TEXT_TOP + 70, cy - 210), label, country, _ease((t - 1.5) / 0.4))
            yield im

    return _encode(frames(), out, n)


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


def render_timeline(events: List[Dict[str, Any]], out: Path) -> Optional[Path]:
    """Vertical timeline: the spine draws down, then each date appears in order.
    Entries are evenly spaced: an ordered sequence, not a time scale."""
    events = sorted(events, key=lambda e: e["year"])[:4]
    if len(events) < 2:
        return None
    n = int(CLIP_SECONDS * VIDEO_FPS)
    x0, top, bottom = 190, TEXT_TOP + 40, TEXT_BOTTOM - 120
    step = (bottom - top) / (len(events) - 1)
    f_year, f_lab = get_font("Anton", 74), get_font("Montserrat", 34)
    bg = Image.new("RGB", (W, H), SEA)
    g = ImageDraw.Draw(bg, "RGBA")
    for y in range(0, H, 6):  # subtle vertical gradient
        g.line([(0, y), (W, y)], fill=(13 + y // 120, 16 + y // 120, 21 + y // 110, 255))

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = bg.copy()
            d = ImageDraw.Draw(im, "RGBA")
            spine = top + (bottom - top) * _ease(t / 1.0)
            d.line([(x0, top), (x0, spine)], fill=(*GOLD, 255), width=6)
            for k, e in enumerate(events):
                appear = _ease((t - 0.4 - 0.45 * k) / 0.4)
                if appear <= 0:
                    continue
                y = top + step * k
                a = int(255 * appear)
                r = 16 * appear
                d.ellipse([x0 - r, y - r, x0 + r, y + r], fill=(*GOLD, a))
                xt = x0 + 50 + int(30 * (1 - appear))
                _text(d, (xt, y - 8), year_text(e["year"], e.get("approximate", False)), font=f_year, fill=(*GOLD, a), anchor="ls")
                for j, line in enumerate(_wrap(d, e["label"], f_lab, W - xt - 90)[:2]):
                    _text(d, (xt, y + 18 + 42 * j), line, font=f_lab, fill=(*WHITE, a), anchor="lt")
            yield im

    return _encode(frames(), out, n)


# -------------------------------------------------------------------- scale
def pick_reference(feet: float) -> Optional[Tuple[str, float, str]]:
    """The reference whose size is closest (by ratio) to `feet`, if within 1/3x-3x."""
    best = min(REFERENCES, key=lambda r: abs(math.log(feet / r[1])))
    return best if 1 / 3 <= feet / best[1] <= 3 else None


def render_scale(fact: Dict[str, Any], out: Path) -> Optional[Path]:
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
    span = TEXT_BOTTOM - TEXT_TOP - 330
    base_y = TEXT_TOP + 150 if depth else TEXT_BOTTOM - 40
    f_num, f_lab = get_font("Anton", 64), get_font("Montserrat", 30)
    metres = lambda ft: ft / 3.28084  # noqa: E731
    # In a depth comparison the landmark is measured by its height; say so.
    bars = [(fact["subject"], feet, GOLD, 330), (ref_label + (" (height)" if depth else ""), ref_feet, (150, 150, 158), 750)]
    hedge = "ABOUT " if fact.get("approximate", True) else ""  # the narration's hedge, kept on screen

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = Image.new("RGB", (W, H), SEA)
            d = ImageDraw.Draw(im, "RGBA")
            d.line([(140, base_y), (W - 140, base_y)], fill=(*MUTED, 255), width=4)
            _text(d, (W // 2, base_y + (-28 if depth else 40)), "GROUND LEVEL" if depth else "", font=get_font("Montserrat", 24),
                   fill=(*MUTED, 255), anchor="mm")
            for k, (label, ft, colour, cx) in enumerate(bars):
                grow = _ease((t - 0.3 - 0.5 * k) / 1.3)
                length = span * ft / longest * grow
                y1 = base_y + length if depth else base_y - length
                if label.startswith(PERSON):
                    _person(d, cx, base_y, length, colour, upward=not depth)
                else:
                    d.rectangle([cx - 70, min(base_y, y1), cx + 70, max(base_y, y1)], fill=(*colour, 255))
                a = int(255 * _ease((t - 1.4 - 0.5 * k) / 0.4))
                if a > 0:
                    num = (f"{ft:,.0f} FT ({metres(ft):,.0f} M)" if ft >= 20 else
                           f"{int(ft)} FT {round((ft % 1) * 12)} IN ({metres(ft):.1f} M)")
                    if k == 0 and hedge:
                        num = hedge + num
                    ty = (y1 + 50) if depth else (y1 - 70)
                    _text(d, (cx, ty), num, font=f_num, fill=(*colour, a), anchor="mm")
                    lines = _wrap(d, label.upper(), f_lab, 340)[:2]
                    for j, line in enumerate(lines):  # reading order top to bottom, whichever side of the bar
                        ly = ty + 52 + 36 * j if depth else ty - 52 - 36 * (len(lines) - 1 - j)
                        _text(d, (cx, ly), line, font=f_lab, fill=(*WHITE, a), anchor="mm")
            yield im

    return _encode(frames(), out, n)


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


def _backdrop(photo: Optional[Path]) -> Image.Image:
    """A dark, heavily blurred version of the topic's photo (or the plain ground) behind text graphics."""
    if photo and Path(photo).exists():
        from .typography import cover_crop
        im = cover_crop(Path(photo), (W, H)).convert("RGB").filter(ImageFilter.GaussianBlur(28))
        dark = Image.new("RGB", (W, H), SEA)
        return Image.blend(im, dark, 0.72).convert("RGB")
    return Image.new("RGB", (W, H), SEA)


# ------------------------------------------------------------- number cards
def _split_number(value: str) -> Tuple[str, Optional[float], str]:
    """'20,000' -> ('', 20000, ''); 'about 280 ft' -> ('about ', 280, ' ft'); no number -> (value, None, '')."""
    import re
    m = re.search(r"\d[\d,]*(\.\d+)?", value)
    if not m:
        return value, None, ""
    return value[:m.start()], float(m.group(0).replace(",", "")), value[m.end():]


def render_stat_cards(cards: List[Dict[str, Any]], out: Path, photo: Optional[Path] = None) -> Optional[Path]:
    """1-2 headline figures counting up to their value, each with its label, over a dark photo backdrop."""
    cards = cards[:2]
    if not cards:
        return None
    n = int(CLIP_SECONDS * VIDEO_FPS)
    bg = _backdrop(photo)
    f_num, f_lab = get_font("Anton", 190 if len(cards) == 1 else 150), get_font("Montserrat", 40)
    centres = [(TEXT_TOP + TEXT_BOTTOM) // 2] if len(cards) == 1 else [TEXT_TOP + 190, TEXT_BOTTOM - 230]

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
            im = bg.copy()
            d = ImageDraw.Draw(im, "RGBA")
            for k, (card, cy) in enumerate(zip(cards, centres)):
                start = 0.2 + 1.2 * k
                a = _ease((t - start) / 0.4)
                if a <= 0:
                    continue
                count = _ease((t - start) / 1.3)
                value_font = fit_font(d, card["value"].upper(), "Anton", f_num.size, W - 160, 70)  # long values shrink to fit
                _text(d, (W // 2, cy - 30), shown(card, count).upper(), font=value_font, fill=(*GOLD, int(255 * a)), anchor="mm")
                d.line([(W // 2 - 80, cy + 70), (W // 2 + 80, cy + 70)], fill=(*WHITE, int(160 * a)), width=3)
                for j, line in enumerate(_wrap(d, card["label"].upper(), f_lab, W - 220)[:2]):
                    _text(d, (W // 2, cy + 115 + 50 * j), line, font=f_lab, fill=(*WHITE, int(255 * a)), anchor="mm")
            yield im

    return _encode(frames(), out, n)


# ------------------------------------------------------------ cross-section
def render_cross_section(cs: Dict[str, Any], out: Path) -> Optional[Path]:
    """A cutaway: sky, ground line, strata, and a shaft that a gold marker descends; each sourced
    level appears as the marker reaches its depth (true proportion), labelled in ft and m."""
    import random
    levels = sorted(cs["levels"], key=lambda lv: lv["depth_ft"])
    total = float(cs["total_depth_ft"])
    if total <= 0 or len(levels) < 2:
        return None
    n = int(CLIP_SECONDS * VIDEO_FPS)
    ground, bottom = TEXT_TOP + 110, TEXT_BOTTOM - 40
    scale = (bottom - ground) / total
    shaft_x = 250
    label_x, label_w = W // 2 + 40, W // 2 - 120  # labels get the right half, wrapped to fit
    f_lab, f_dep, f_title = get_font("Montserrat", 32), get_font("Anton", 40), get_font("Anton", 56)
    rnd = random.Random(7)
    bg = Image.new("RGB", (W, H), SEA)
    d0 = ImageDraw.Draw(bg, "RGBA")
    for y in range(ground, H):  # strata: warm earth bands darkening with depth
        k = (y - ground) / (H - ground)
        band = 6 * math.sin(y / 23.0) + rnd.uniform(-2, 2)
        d0.line([(0, y), (W, y)], fill=(int(70 - 30 * k + band), int(56 - 26 * k + band), int(44 - 22 * k + band), 255))
    d0.line([(0, ground), (W, ground)], fill=(*MUTED, 255), width=4)
    _text(d0, (W - 120, ground - 30), "GROUND LEVEL", font=get_font("Montserrat", 26), fill=(*MUTED, 255), anchor="rm")
    title = cs["subject"].upper() + ("  ·  APPROX. DEPTHS" if cs.get("approximate", True) else "")
    _text(d0, (120, TEXT_TOP + 20), title, font=fit_font(d0, title, "Anton", 56, W - 240, 30), fill=(*WHITE, 255), anchor="lm")

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = bg.copy()
            d = ImageDraw.Draw(im, "RGBA")
            depth_now = total * _ease((t - 0.3) / 3.4)
            y_now = ground + depth_now * scale
            d.rectangle([shaft_x - 22, ground, shaft_x + 22, y_now], fill=(18, 16, 14, 255))  # the shaft opens as we descend
            for lv in levels:
                if lv["depth_ft"] > depth_now:
                    continue
                y = ground + lv["depth_ft"] * scale
                a = int(255 * _ease((depth_now - lv["depth_ft"]) / (total * 0.08) if total else 1))
                d.rectangle([shaft_x + 22, y - 16, label_x - 30, y + 16], fill=(22, 19, 16, a))  # the chamber or level
                _text(d, (shaft_x - 50, y), f"{lv['depth_ft']:,.0f} FT", font=f_dep, fill=(*GOLD, a), anchor="rm")
                _text(d, (shaft_x - 50, y + 34), f"({lv['depth_ft'] / 3.28084:,.0f} M)", font=get_font("Montserrat", 22),
                       fill=(*MUTED, a), anchor="rm")
                lines = _wrap(d, lv["label"].upper(), f_lab, label_w)[:2]
                for j, line in enumerate(lines):
                    _text(d, (label_x, y + (j - (len(lines) - 1) / 2) * 38), line, font=f_lab, fill=(*WHITE, a), anchor="lm")
            d.ellipse([shaft_x - 14, y_now - 14, shaft_x + 14, y_now + 14], fill=(*GOLD, 255))
            yield im

    return _encode(frames(), out, n)


# ---------------------------------------------------------------- route map
def render_route(stops: List[Dict[str, Any]], out: Path) -> Optional[Path]:
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
    bw, bh = W * scale, H * scale
    # Fit every stop inside the middle of the frame (x between 15-85%, y between the text bounds).
    span_lon = max((max(lons) - min(lons)) * coslat / 0.62, 6.0)
    span_lat = max((max(lats) - min(lats)) / ((TEXT_BOTTOM - TEXT_TOP - 200) / H), 6.0)
    kx = min(bw / span_lon, bh / span_lat)

    def project(lon: float, lat: float) -> Tuple[float, float]:
        return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx

    base = relief_base(lat0, lon0, bw, bh, kx, coslat, None, project, (0, 0, bw, bh)).resize((W, H), Image.LANCZOS)
    pts = [(project(p["lon"], p["lat"])[0] / scale, project(p["lon"], p["lat"])[1] / scale) for p in located]
    n = int(CLIP_SECONDS * VIDEO_FPS)
    f_lab = get_font("Montserrat", 34)
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
                d.line([(x0, y0), (x0 + (x1 - x0) * prog, y0 + (y1 - y0) * prog)], fill=(*GOLD, 255), width=7)
            for k, ((x, y), p) in enumerate(zip(pts, located)):
                a = _ease((t - 0.2 - k * seg_time) / 0.35)
                if a <= 0:
                    continue
                d.ellipse([x - 16, y - 16, x + 16, y + 16], fill=(*GOLD, int(255 * a)), outline=(20, 20, 20, int(255 * a)), width=4)
                right = x < W / 2  # label on the open side
                tx = x + 34 if right else x - 34
                for j, line in enumerate(_wrap(d, p["label"].upper(), f_lab, 380)[:2]):
                    _text(d, (tx, y - 18 + 40 * j), line, font=f_lab, fill=(*WHITE, int(255 * a)), anchor="lm" if right else "rm")
            yield im

    return _encode(frames(), out, n)


# ----------------------------------------------------------------------- all
def make_all(payload: Dict[str, Any], topic: Dict[str, Any], work_dir: Path, photo: Optional[Path] = None) -> List[Path]:
    """Every graphic the script and topic support. Never raises: graphics are optional.
    `photo`: the topic's hero photo, used (blurred) behind the number cards."""
    made: List[Path] = []
    jobs = []
    if payload.get("stat_cards"):
        jobs.append(("numbers", lambda: render_stat_cards(payload["stat_cards"], work_dir / "graphic_numbers.mp4", photo)))
    if payload.get("cross_section"):
        jobs.append(("cross-section", lambda: render_cross_section(payload["cross_section"], work_dir / "graphic_section.mp4")))
    if payload.get("route"):
        jobs.append(("route", lambda: render_route(payload["route"], work_dir / "graphic_route.mp4")))
    place = None
    try:
        place = geo.locate(topic)
    except Exception as e:
        log.warning("Map lookup failed: %s", e)
    if place:
        jobs.append(("map", lambda: render_map(place, work_dir / "graphic_map.mp4")))
    if payload.get("timeline"):
        jobs.append(("timeline", lambda: render_timeline(payload["timeline"], work_dir / "graphic_timeline.mp4")))
    if payload.get("scale_fact"):
        jobs.append(("scale", lambda: render_scale(payload["scale_fact"], work_dir / "graphic_scale.mp4")))
    for name, job in jobs:
        try:
            path = job()
            if path:
                made.append(path)
        except Exception as e:
            log.warning("Graphic '%s' failed (%s); continuing without it.", name, e)
    log.info("Original graphics: %s", ", ".join(p.stem.replace("graphic_", "") for p in made) or "none")
    return made
