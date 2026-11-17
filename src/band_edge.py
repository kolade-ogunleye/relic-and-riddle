"""The one band / plate edge (design-freeze cycle 0 round 7, panel F8 and F17).

Where a photograph meets the INK band or plate that text sits on, the shade eases in by smoothstep over a feather
that grows with the luma step it crosses (brand_system.edge_feather: its steepest point at most
tok("edge.slope_max") luma per px at 1080 wide). A fixed feather put a 217 -> 24 luma step into ~40 px on a bright
ground (6.1 luma / px: a lid at feed size) while the same feather on a dark ground read soft (1.8). The step is read
on the picture itself: the bright end (the `pct` percentile of luma across the width) of the rows the feather
crosses, less the band colour's luma, times the band's strength (its peak alpha), so the brightest column, where a
lid shows first, sets the length.

Every renderer that draws a band or plate edge goes through feather() (multi_format.scrim, the split and comparison
grounds through it, the myth plate in design_cards); tests/test_freeze_r7_plate.py holds them to it.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .brand_system import edge_feather

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
EDGE_PCT = 95


def luma_rows(im: Image.Image | np.ndarray) -> np.ndarray:
    """H x W luma (0-255) of an RGB image or array."""
    a = np.asarray(im.convert("RGB") if isinstance(im, Image.Image) else im, dtype=np.float32)
    return a[..., :3] @ LUMA


def step_across(luma: np.ndarray, y0: float, y1: float, colour, strength: float = 1.0, pct: float = EDGE_PCT) -> float:
    """The luma step a band edge crosses over rows y0..y1 of `luma` (H x W): the rows' `pct` percentile less the
    band colour's luma, times `strength` (the band's alpha at its full); 0 for a band no lighter than the ground."""
    h = luma.shape[0]
    a, b = max(0, int(np.floor(min(y0, y1)))), min(h, int(np.ceil(max(y0, y1))) + 1)
    if b <= a:
        return 0.0
    band = float(np.dot(np.asarray(colour[:3], np.float32), LUMA))
    return max(0.0, (float(np.percentile(luma[a:b], pct)) - band) * float(strength))


def feather(luma: np.ndarray, edge: float, side: str, colour, strength: float = 1.0, minimum: float = 0.0,
            width: int | None = None) -> int:
    """The feather (px) of a band edge at row `edge` easing out toward `side` ("up": the photo above a band, "down":
    the photo below it): the length edge_feather gives for the step measured over the rows it crosses (measured
    again over the longer reach once, so a longer feather is judged on the ground it really covers)."""
    w = luma.shape[1]
    width = w if width is None else width
    length = max(1.0, float(minimum))
    for _ in range(2):
        span = (edge - length, edge) if side == "up" else (edge, edge + length)
        need = edge_feather(step_across(luma, *span, colour, strength), width, minimum)
        if need <= length:
            break
        length = float(need)
    return round(length)


def smoothstep(v: np.ndarray) -> np.ndarray:
    v = np.clip(v, 0.0, 1.0)
    return v * v * (3 - 2 * v)


def band_profile(h: int, y0: float, y1: float, up: float, down: float) -> np.ndarray:
    """Per row (h), 1 inside y0..y1, easing by smoothstep to 0 over `up` px above y0 and `down` px below y1."""
    y = np.arange(h, dtype=np.float32)
    v = np.where((y >= y0) & (y <= y1), 1.0,
                 np.where(y < y0, 1 - (y0 - y) / max(1.0, up), 1 - (y - y1) / max(1.0, down)))
    return smoothstep(v)


def steepest(luma_column: np.ndarray) -> float:
    """The steepest luma change per px down one column (or a row-mean profile)."""
    c = np.asarray(luma_column, np.float32)
    return float(np.abs(np.diff(c)).max()) if c.size > 1 else 0.0
