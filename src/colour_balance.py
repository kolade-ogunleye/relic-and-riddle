"""
src/colour_balance.py
=====================
A gentle, automatic colour and brightness balance when a photo is prepared for the video (operator, 2026-10-29).

Production's Derinkuyu stills were dark and murky with a heavy yellow-green cast, so every shot looked alike. Each
prepared still gets two capped, deterministic corrections:

- a lamp's colour cast is neutralised toward grey, at CAST_STRENGTH of the way and with each channel's gain held
  within GAIN_LIMIT, so a warm lamp-lit room stays warm, only less tinted. Only a lamp-lit scene is corrected: dim
  (95th-percentile luminance under DIM_P95, or a bright frame lit yellow-green: LAMP_GREEN_HUE), no daylight sky in its upper half, no vegetation (FOLIAGE_MIN), and
  even its brightest unclipped surfaces tinted (a white light shows as near-white highlights). The cast is measured only on neutral candidates:
  the least saturated NEUTRAL_SHARE of the mid-tones, never sky blue or foliage green. A natural scene's colour (a
  blue sky, desert sand, sea, foliage, red sandstone, a painted pot on a grey plinth) is never neutralised
  (2026-10-29 review: grey-world over the whole frame had turned Derinkuyu photo 5's blue sky grey-teal). Nor is a
  warm cast (neutral candidates' hue within WARM_CAST_HUE): candle, torch and tungsten light, which viewers read as
  natural, share that hue with warm materials (amber, gold, ochre, bronze, wood), and nothing in the pixels tells
  the two apart (final rc72 review: the Amber Room's panels were cooled toward grey, and Derinkuyu photo 19 turned
  greenish). Only the off-white casts are taken out: yellow-green (the murk that started this), magenta, blue;
- a very dark exposure (mean luminance under DARK_BELOW) is lifted by a gamma curve, at most to MAX_LIFT of the way
  toward LIFT_TARGET and never below MIN_GAMMA, applied to the luminance with every channel scaled alike (so its
  saturation and hue are kept): black stays black and white stays white, so contrast is not flattened and a dark
  interior stays dark, only readable.

Never touched: black-and-white and toned (sepia) archival photos (every channel follows the luminance), our own
graphics and maps, clips, and anything already balanced (a copy this module made, or a photo whose cast and
exposure are inside the thresholds: returned as it is, no copy). A corrected copy keeps its file name in its own
folder under cache/colour_balance (keyed by the source's hash and VERSION, so a resume or re-render pays nothing)
and records its source and settings (src/lineage.py, operation "colour_balance"), so its credit and rights resolve
to the original. Never raises: on any failure the photo is used as it is.
"""

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from .config import BASE_DIR

log = logging.getLogger(__name__)

VERSION = 4
OPERATION = "colour_balance"
CACHE_DIR = BASE_DIR / "cache" / "colour_balance"
CAST_MIN = 0.04        # a channel this far (as a gain) from grey is a cast; below it the colours are left alone
CAST_STRENGTH = 0.6    # how much of the way to grey a cast is taken
DIM_P95 = 0.72         # a scene whose 95th-percentile luminance (0-1) is at or above this is daylit: no cast taken out
HIGHLIGHT_SHARE = 0.03  # its brightest unclipped pixels (this share) ...
HIGHLIGHT_TINT = 0.20   # ... must be at least this saturated (HSV) for the light itself to be coloured
SKY_HUE = (190.0, 250.0)
SKY_MIN = 0.05         # this share of the upper half bright and sky blue is daylight
FOLIAGE_HUE = (80.0, 170.0)
FOLIAGE_MIN = 0.03     # this share of the frame green and saturated is vegetation in daylight (a lamp yellows greens)
FOLIAGE_LUMA = 30.0    # 8-bit luminance: darker "green" pixels are JPEG noise in shadow, not vegetation
WARM_CAST_HUE = (10.0, 48.0)   # a cast in this hue is warm light or a warm material: left as it is
# A bright frame is daylight, except one lit yellow-green by its lamps: production's Derinkuyu stone door (29 Oct,
# 95th-percentile luminance 0.79, highlights at saturation 0.52, neutral candidates at hue 62) passed as daylit and
# reached the video neon lime. Daylight shows near-white highlights; these lamps tint even the brightest surfaces.
LAMP_GREEN_HUE = (48.0, 75.0)
BRIGHT_LAMP_TINT = 0.40
NEUTRAL_SHARE = 0.35   # the least saturated share of the candidate mid-tones on which the cast is measured
GAIN_LIMIT = (0.85, 1.18)
DARK_BELOW = 0.30      # mean luminance (0-1) under which an exposure is lifted
LIFT_TARGET = 0.40
MAX_LIFT = 0.5         # of the way from the mean to LIFT_TARGET
MIN_GAMMA = 0.75       # the strongest lift
MONO_CHROMA = 8.0      # 8-bit levels: a mean chroma under this is black and white
MONO_RESIDUAL = 6.0    # 8-bit levels: every channel within this of a line through the luminance is one tone
TONED_HUES = ((10.0, 48.0), (185.0, 235.0))   # sepia/brown and cyanotype tonings
STAT_SIDE = 256        # measured on a copy this size


def _luma(a: np.ndarray) -> np.ndarray:
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _hue(rgb: np.ndarray) -> float:
    """The hue (degrees) of one colour."""
    import colorsys
    return colorsys.rgb_to_hsv(*(np.clip(rgb, 0, 255) / 255))[0] * 360


def _hsv(a: np.ndarray):
    """Per-pixel hue (degrees) and saturation (0-1) of an 8-bit RGB array."""
    x = a / 255
    mx, mn = x.max(axis=-1), x.min(axis=-1)
    d = mx - mn
    sat = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0.0)
    dd = np.maximum(d, 1e-6)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    hue = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4)) * 60
    return np.where(d > 1e-6, hue, 0.0), sat


def scene(a: np.ndarray) -> Dict[str, Any]:
    """What the cast decision rests on: the sky and foliage masks, whether the scene is lamp-lit, and the colour of
    its neutral candidates (None when there are too few)."""
    hue, sat = _hsv(a)
    y = _luma(a)
    sky = (hue >= SKY_HUE[0]) & (hue <= SKY_HUE[1]) & (sat > 0.15) & (y > 0.30 * 255)
    foliage = (hue >= FOLIAGE_HUE[0]) & (hue <= FOLIAGE_HUE[1]) & (sat > 0.20) & (y > FOLIAGE_LUMA)
    upper = sky[: max(1, a.shape[0] // 2)]
    daylight_sky = float(upper.mean()) >= SKY_MIN
    p95 = float(np.percentile(y, 95) / 255)
    unclipped = a.max(axis=-1) < 250
    tint = 0.0
    if unclipped.sum() > 50:
        bright = unclipped & (y >= np.percentile(y[unclipped], 100 * (1 - HIGHLIGHT_SHARE)))
        tint = float(sat[bright].mean())
    cand = (y > 20) & (y < 235) & ~sky & ~foliage
    neutral = None
    if cand.sum() > 100:
        keep = cand & (sat <= np.percentile(sat[cand], 100 * NEUTRAL_SHARE))
        if keep.sum() > 50:
            neutral = a[keep].mean(axis=0)
    green_lamp = (neutral is not None and tint >= BRIGHT_LAMP_TINT
                  and LAMP_GREEN_HUE[0] <= _hue(neutral) <= LAMP_GREEN_HUE[1])
    lamp_lit = ((p95 < DIM_P95 or green_lamp) and not daylight_sky and tint >= HIGHLIGHT_TINT
                and float(foliage.mean()) < FOLIAGE_MIN)
    return {"sky": sky, "foliage": foliage, "daylight_sky": daylight_sky, "p95_luma": p95, "highlight_tint": tint,
            "lamp_lit": lamp_lit, "neutral": neutral}


def monochrome(a: np.ndarray) -> bool:
    """Black-and-white (no chroma), or a toned archival print (sepia, cyanotype): every channel a straight line
    through the luminance, in a toning's hue. A colour photo under one lamp is a straight line too, but yellow-green
    (Derinkuyu's lamp-lit rooms, hue about 55-75): that is a cast, and is balanced."""
    chroma = a.max(axis=-1) - a.min(axis=-1)
    if float(chroma.mean()) < MONO_CHROMA:
        return True
    y = _luma(a).ravel()
    if np.ptp(y) < 1:
        return True
    for c in range(3):
        ch = a[..., c].ravel()
        slope, icpt = np.polyfit(y, ch, 1)
        if np.sqrt(np.mean((ch - (slope * y + icpt)) ** 2)) > MONO_RESIDUAL:
            return False
    hue = _hue(a.reshape(-1, 3).mean(axis=0))
    return any(lo <= hue <= hi for lo, hi in TONED_HUES)


def plan(im: Image.Image) -> Optional[Dict[str, Any]]:
    """The correction for `im` ({"gains", "gamma", "cast", "mean_luma"}), or None when it needs none or must not
    be touched (monochrome). `cast` is the neutral candidates' cast, and 0 for a scene that is not lamp-lit; a
    warm cast is measured but left as it is."""
    small = im.convert("RGB")
    small.thumbnail((STAT_SIDE, STAT_SIDE))
    a = np.asarray(small, dtype=np.float64)
    if monochrome(a):
        return None
    y = _luma(a)
    sc = scene(a)
    cast, gains, full = 0.0, np.ones(3), np.ones(3)
    if sc["lamp_lit"] and sc["neutral"] is not None:     # a natural scene's colour is its own, never a cast
        means = sc["neutral"]
        full = means.mean() / np.maximum(means, 1e-6)
        cast = float(np.max(np.abs(full - 1)))
    warm = cast > 0 and WARM_CAST_HUE[0] <= _hue(sc["neutral"]) <= WARM_CAST_HUE[1]
    if cast >= CAST_MIN and not warm:
        gains = np.clip(1 + CAST_STRENGTH * (full - 1), *GAIN_LIMIT)
        gains = np.clip(gains / (gains @ np.array([0.299, 0.587, 0.114])), *GAIN_LIMIT)   # brightness is the lift's
    mean_luma = float(y.mean() / 255)
    gamma = 1.0
    if mean_luma < DARK_BELOW and mean_luma > 0.01:
        target = mean_luma + MAX_LIFT * (LIFT_TARGET - mean_luma)
        gamma = float(np.clip(np.log(target) / np.log(mean_luma), MIN_GAMMA, 1.0))
    if np.allclose(gains, 1, atol=1e-3) and gamma > 0.995:
        return None
    return {"gains": [round(float(g), 4) for g in gains], "gamma": round(gamma, 4), "cast": round(cast, 4),
            "mean_luma": round(mean_luma, 4)}


def apply(im: Image.Image, settings: Dict[str, Any]) -> Image.Image:
    """`im` with the planned gains and gamma (deterministic)."""
    a = np.asarray(im.convert("RGB"), dtype=np.float64) / 255
    a = np.clip(a * np.array(settings["gains"]), 0, 1)
    if settings["gamma"] < 1:     # on the luminance, every channel scaled alike: lifted without washing the colour out
        y = _luma(a)
        a = np.clip(a * (np.power(np.maximum(y, 1e-6), settings["gamma"]) / np.maximum(y, 1e-6))[..., None], 0, 1)
    return Image.fromarray(np.round(a * 255).astype(np.uint8), "RGB")


def _skip(path: Path) -> bool:
    from .typography import is_video
    p = Path(path)
    return (is_video(p) or p.stem.startswith(("graphic_", "footage_")) or p.suffix.lower() not in (".jpg", ".jpeg", ".png")
            or CACHE_DIR in p.resolve().parents)


def balanced(path: Path) -> Path:
    """A balanced copy of `path` (cached), or `path` itself when it needs none, must not be touched, or the
    correction fails."""
    path = Path(path)
    try:
        if _skip(path) or not path.is_file():
            return path
        from .credits import sha256_file
        from .lineage import record_derived, source_chain
        key = hashlib.sha256(f"{sha256_file(path)}|v{VERSION}".encode()).hexdigest()[:16]
        out = CACHE_DIR / key / path.name
        if out.is_file():
            try:
                if source_chain(out):
                    return out
            except (ValueError, KeyError, OSError):
                pass
        with Image.open(path) as im:
            settings = plan(im)
            if settings is None:
                return path
            fixed = apply(im, settings)
            extra = {k: im.info[k] for k in ("exif",) if im.info.get(k)}   # same orientation
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() in (".jpg", ".jpeg"):
            fixed.save(out, "JPEG", quality=95, **extra)
        else:
            fixed.save(out, **extra)
        record_derived(out, [path], OPERATION, {"version": VERSION, **settings})
        log.info("Colour balance: %s (cast %.2f, gains %s, gamma %.2f).", path.name, settings["cast"],
                 settings["gains"], settings["gamma"])
        return out
    except Exception as e:  # a saving, never a new way for a run to fail
        log.warning("Colour balance of %s skipped (%s).", path.name, e)
        return path


def balance_all(paths: List[Path]) -> List[Path]:
    return [balanced(p) for p in paths]
