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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from . import geo
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .typography import get_font

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
]


def _ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


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
    draw = ImageDraw.Draw(im)
    f1, f2 = get_font("Anton", 66), get_font("Montserrat", 30)
    w1 = draw.textlength(title.upper(), font=f1)
    w2 = draw.textlength(sub.upper(), font=f2)
    bw = int(max(w1, w2) + 70)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    d.rounded_rectangle([cx - bw // 2, y - 62, cx + bw // 2, y + 66], radius=18, fill=(10, 10, 12, int(200 * alpha)),
                        outline=(*GOLD, int(220 * alpha)), width=2)
    d.text((cx, y - 18), title.upper(), font=f1, fill=(*WHITE, a), anchor="mm")
    d.text((cx, y + 38), sub.upper(), font=f2, fill=(*GOLD, a), anchor="mm")
    return Image.alpha_composite(im, layer)


# ---------------------------------------------------------------------- map
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

    base = Image.new("RGB", (bw, bh), SEA)
    draw = ImageDraw.Draw(base)
    lat_half = bh / 2 / kx + 2
    lon_half = bw / 2 / (kx * coslat) + 2
    for g in range(-180, 181, 5):  # faint graticule
        if abs(g - lon0) < lon_half:
            x = project(g, lat0)[0]
            draw.line([(x, 0), (x, bh)], fill=GRID, width=2)
        if abs(g - lat0) < lat_half:
            y = project(lon0, g)[1]
            draw.line([(0, y), (bw, y)], fill=GRID, width=2)
    home = geo.find_country(place["country"])
    for c in geo.countries():
        for poly in c["polygons"]:
            ring = poly[0]
            if not any(abs(x - lon0) < lon_half and abs(y - lat0) < lat_half for x, y in ring[:: max(1, len(ring) // 50)]):
                continue
            pts = [project(x, y) for x, y in ring]
            draw.polygon(pts, fill=HOME if c is home else LAND, outline=BORDER)
            for hole in poly[1:]:
                draw.polygon([project(x, y) for x, y in hole], fill=SEA, outline=BORDER)
    base = base.filter(ImageFilter.SMOOTH)

    n = int(CLIP_SECONDS * VIDEO_FPS)
    label = place["label"]
    country = place["country"]

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            zoom = span_wide / (span_wide + (span_close - span_wide) * _ease(t / 1.6))
            cw, ch = bw / zoom, bh / zoom
            box = (bw / 2 - cw / 2, bh / 2 - ch / 2, bw / 2 + cw / 2, bh / 2 + ch / 2)
            im = base.resize((W, H), Image.BICUBIC, box=box).convert("RGBA")
            d = ImageDraw.Draw(im)
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
    bg = Image.new("RGBA", (W, H), (*SEA, 255))
    g = ImageDraw.Draw(bg)
    for y in range(0, H, 6):  # subtle vertical gradient
        g.line([(0, y), (W, y)], fill=(13 + y // 120, 16 + y // 120, 21 + y // 110, 255))

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = bg.copy()
            d = ImageDraw.Draw(im)
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
                d.text((xt, y - 8), year_text(e["year"], e.get("approximate", False)), font=f_year, fill=(*GOLD, a), anchor="ls")
                for j, line in enumerate(_wrap(d, e["label"], f_lab, W - xt - 90)[:2]):
                    d.text((xt, y + 18 + 42 * j), line, font=f_lab, fill=(*WHITE, a), anchor="lt")
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

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            im = Image.new("RGBA", (W, H), (*SEA, 255))
            d = ImageDraw.Draw(im)
            d.line([(140, base_y), (W - 140, base_y)], fill=(*MUTED, 255), width=4)
            d.text((W // 2, base_y + (-28 if depth else 40)), "GROUND LEVEL" if depth else "", font=get_font("Montserrat", 24),
                   fill=(*MUTED, 255), anchor="mm")
            for k, (label, ft, colour, cx) in enumerate(bars):
                grow = _ease((t - 0.3 - 0.5 * k) / 1.3)
                length = span * ft / longest * grow
                y1 = base_y + length if depth else base_y - length
                d.rectangle([cx - 70, min(base_y, y1), cx + 70, max(base_y, y1)], fill=(*colour, 255))
                a = int(255 * _ease((t - 1.4 - 0.5 * k) / 0.4))
                if a > 0:
                    num = f"{ft:,.0f} FT ({metres(ft):,.0f} M)"
                    ty = (y1 + 50) if depth else (y1 - 70)
                    d.text((cx, ty), num, font=f_num, fill=(*colour, a), anchor="mm")
                    for j, line in enumerate(_wrap(d, label.upper(), f_lab, 340)[:2]):
                        d.text((cx, ty + (52 + 36 * j) * (1 if depth else -1) - (0 if depth else 20)), line, font=f_lab,
                               fill=(*WHITE, a), anchor="mm")
            yield im

    return _encode(frames(), out, n)


# ----------------------------------------------------------------------- all
def make_all(payload: Dict[str, Any], topic: Dict[str, Any], work_dir: Path) -> List[Path]:
    """Every graphic the script and topic support. Never raises: graphics are optional."""
    made: List[Path] = []
    jobs = []
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
