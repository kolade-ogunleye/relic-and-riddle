"""Which photograph a run's cover, Stories and graphics are built on (2026-11-01).

The hero was the first still of the scene sequence, whatever it looked like: Gol Gumbaz's interior (grey, flat,
low resolution) made every cover style look weak, while its sharp, colourful exterior was in the same pool.
Each candidate is scored on what a 9:16 cover needs from a photograph, measured on its pixels:

- sharpness: the variance of the Laplacian (fine detail survives the crop);
- contrast: the RMS contrast of its luminance;
- colour: Hasler and Süsstrunk's colourfulness;
- exposure: a mid-key picture without large clipped areas;
- resolution: its pixels against a 1080x1920 cover.

Only strong photos compete (image_review.strong_stems: they show the topic, or you kept them), so a handsome
context shot never replaces the subject; one with a reviewed focus (cover_design.reviewed_focus) gets a bonus,
since its framing was art-directed. The sequence's first still stays the hero unless another scores at least
SWITCH_MARGIN higher: the storyboard put it first for a reason.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

SWITCH_MARGIN = 0.12
FOCUS_BONUS = 0.08
WEIGHTS = {"sharpness": 0.25, "contrast": 0.25, "colour": 0.2, "exposure": 0.15, "resolution": 0.15}
ANALYSIS_EDGE = 512


def _sat(x: float, full: float) -> float:
    """0..1, rising steadily and reaching 1 at `full`."""
    return float(min(1.0, max(0.0, x / full)))


def measures(path: Path) -> dict[str, float] | None:
    """Each criterion in 0..1 for the photo at `path`, or None when it cannot be read."""
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            w, h = im.size
            im.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE), Image.Resampling.LANCZOS)
    except OSError:
        return None
    rgb = np.asarray(im, dtype=np.float32)
    luma = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255.0
    lap = (-4 * luma[1:-1, 1:-1] + luma[:-2, 1:-1] + luma[2:, 1:-1] + luma[1:-1, :-2] + luma[1:-1, 2:])
    rg = rgb[..., 0] - rgb[..., 1]
    yb = 0.5 * (rgb[..., 0] + rgb[..., 1]) - rgb[..., 2]
    colourful = float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))
    clipped = float(((luma < 0.02) | (luma > 0.98)).mean())
    exposure = max(0.0, 1 - abs(float(luma.mean()) - 0.45) / 0.45) * max(0.0, 1 - clipped / 0.25)
    return {"sharpness": _sat(float(lap.var()) * 1e4, 120.0),    # crisp photos measured 50-200 at 512 px, 13-37 blurred
            "contrast": _sat(float(luma.std()), 0.25),
            "colour": _sat(colourful, 60.0),                      # Hasler-Süsstrunk: ~60 is "quite colourful"
            "exposure": float(exposure),
            "resolution": _sat(w * h, 1080 * 1920)}


def score(path: Path) -> float | None:
    """The weighted score in 0..1 (plus FOCUS_BONUS for a reviewed focus), or None when unreadable."""
    m = measures(path)
    if m is None:
        return None
    total = sum(WEIGHTS[k] * m[k] for k in WEIGHTS)
    from .cover_design import reviewed_focus
    if reviewed_focus(Path(path)) is not None:
        total += FOCUS_BONUS
    return total


def pick(scene_media: list[Path], strong_stems: set | None = None) -> Path:
    """The hero for a run: the sequence's first still, unless another strong still scores SWITCH_MARGIN higher."""
    from .image_review import only_strong
    from .typography import is_video
    stills = [Path(p) for p in scene_media if not is_video(p)]
    if not stills:
        raise StopIteration("no still image in the scene sequence")
    first = stills[0]
    candidates = only_strong(stills, strong_stems)
    scores = {p: s for p in dict.fromkeys([first, *candidates]) if (s := score(p)) is not None}
    if first not in scores or len(scores) < 2:
        return first
    best = max(scores, key=lambda p: scores[p])
    if best != first and scores[best] >= scores[first] + SWITCH_MARGIN:
        log.info("Hero photo: %s (score %.2f) over the first still %s (%.2f).", best.name, scores[best],
                 first.name, scores[first])
        return best
    return first
