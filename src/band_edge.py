"""The one band / plate edge (design-freeze cycle 0 round 7, panel F8 and F17).

Where a photograph meets the INK band or plate that text sits on, the shade eases in by smoothstep over a feather
that grows with the luma step it crosses (brand_system.edge_feather: its steepest point at most
tok("edge.slope_max") luma per px at 1080 wide). A fixed feather put a 217 -> 24 luma step into ~40 px on a bright
ground (6.1 luma / px: a lid at feed size) while the same feather on a dark ground read soft (1.8). The step is read
on the picture itself: the bright end (the `pct` percentile of luma across the width) of the rows the feather
crosses, less the band colour's luma, times the band's strength (its peak alpha), so the brightest column, where a
lid shows first, sets the length.

Freeze cycle 5 (stream PLATES, panel 6: the 9:16 myth panel's foot at 2.16 luma / px on a blurred ground and the hook's
bright-ground plate at 2.6 read as a slab and a dark bar, while the carousel's bands at up to 2.0 over sharp
photographs read as eased): ONE limit, slope_limit(), measured on the ground the edge crosses. The ceiling is
tok("edge.slope_max") for a band that runs to the frame's edge (a ground the photograph ends in: one edge shows) and
tok("edge.slope_float") for a FLOATING band (the photograph goes on above and below it: it reads as an object, both
edges show); under either, the limit falls with the ground's own detail (ground_texture: the mean luma change per px
down the rows the feather crosses, the lower quartile of the column blocks, times tok("edge.texture_gain")), never
under the field gate scrim.slope_max: on a flat or blurred ground the edge is the only structure there is, and a
sharp photograph hides a steeper one. A feather may run past the frame's edge (the band is then never fully open
there; nothing steps).

Every renderer that draws a band or plate edge goes through feather() (multi_format.scrim, the split and comparison
grounds through it, the myth plate in design_cards); tests/test_freeze_r7_plate.py holds them to it.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .brand_system import GATES, edge_feather, tok

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
EDGE_PCT = 95


def luma_rows(im: Image.Image | np.ndarray) -> np.ndarray:
    """H x W luma (0-255) of an RGB image or array."""
    a = np.asarray(im.convert("RGB") if isinstance(im, Image.Image) else im, dtype=np.float32)
    return a[..., :3] @ LUMA


def step_across(luma: np.ndarray, y0: float, y1: float, colour, strength: float = 1.0, pct: float = EDGE_PCT,
                by_row: bool = False) -> float:
    """The luma step a band edge crosses over rows y0..y1 of `luma` (H x W): the rows' `pct` percentile less the
    band colour's luma, times `strength` (the band's alpha at its full); 0 for a band no lighter than the ground.
    `by_row` (a floating band, freeze cycle 5): the bright end is read row by row and the `pct` percentile of the
    rows' own taken, so one bright stretch the edge crosses sets the length (the steepest row is where it shows)."""
    h = luma.shape[0]
    a, b = max(0, int(np.floor(min(y0, y1)))), min(h, int(np.ceil(max(y0, y1))) + 1)
    if b <= a:
        return 0.0
    band = float(np.dot(np.asarray(colour[:3], np.float32), LUMA))
    bright = (float(np.percentile(np.percentile(luma[a:b], pct, axis=1), pct)) if by_row
              else float(np.percentile(luma[a:b], pct)))
    return max(0.0, (bright - band) * float(strength))


FLOAT_HEADROOM = 0.85   # feather(): a floating band's edge is laid at this share of its limit
TEXTURE_BLOCKS = 16     # ground_texture: the column blocks the ground is read in
TEXTURE_PCT = 25        # and the percentile of their detail that counts (the flattest quarter: where an edge shows first)


def ground_texture(luma: np.ndarray, y0: float, y1: float, width: int | None = None) -> float:
    """The ground's own detail over rows y0..y1 of `luma` (H x W), as luma per px at 1080 wide: per column block
    (TEXTURE_BLOCKS across), the mean absolute luma change from one row to the next; the TEXTURE_PCT percentile of the
    blocks. ~0.2 on a blurred or flat ground, 1-20 on a sharp photograph."""
    h, w = luma.shape
    width = w if width is None else width
    a, b = max(0, int(np.floor(min(y0, y1)))), min(h, int(np.ceil(max(y0, y1))) + 1)
    if b - a < 2 or w < 2:
        return 0.0
    d = np.abs(np.diff(luma[a:b], axis=0)).mean(axis=0)
    e = np.linspace(0, w, min(TEXTURE_BLOCKS, w) + 1).astype(int)
    per = [float(d[e[i]:e[i + 1]].mean()) for i in range(len(e) - 1) if e[i + 1] > e[i]]
    return float(np.percentile(per, TEXTURE_PCT)) * width / 1080.0


def slope_limit(luma: np.ndarray, y0: float, y1: float, width: int | None = None, floating: bool = False) -> float:
    """THE band-edge limit (luma per px at 1080 wide) for an edge easing over rows y0..y1 of the ground `luma`: the
    ground's detail times tok("edge.texture_gain"), never under the field gate (scrim.slope_max) and never over the
    ceiling: tok("edge.slope_max"), or tok("edge.slope_float") for a floating band (never over the first)."""
    ceiling = float(tok("edge.slope_max"))
    if floating:
        ceiling = min(ceiling, float(tok("edge.slope_float")))
    gate = min(float(GATES["scrim.slope_max"]), ceiling)
    return float(min(ceiling, max(gate, tok("edge.texture_gain") * ground_texture(luma, y0, y1, width))))


def feather(luma: np.ndarray, edge: float, side: str, colour, strength: float = 1.0, minimum: float = 0.0,
            width: int | None = None, floating: bool = False) -> int:
    """The feather (px) of a band edge at row `edge` easing out toward `side` ("up": the photo above a band, "down":
    the photo below it): the length edge_feather gives for the step measured over the rows it crosses at the limit
    those rows allow (slope_limit; `floating`: the photograph goes on past the band's other edge too), measured
    again over the longer reach (a longer feather is judged on the ground it really covers)."""
    w = luma.shape[1]
    width = w if width is None else width
    length = max(1.0, float(minimum))
    for _ in range(5):
        span = (edge - length, edge) if side == "up" else (edge, edge + length)
        # a floating band is laid FLOAT_HEADROOM inside its limit (its feather is judged where it is steepest)
        limit = slope_limit(luma, *span, width, floating) * (FLOAT_HEADROOM if floating else 1.0)
        need = edge_feather(step_across(luma, *span, colour, strength, by_row=floating), width, minimum,
                            slope=limit)
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


HOLD_POWER = 6.0   # hold_hue: the band colour's share of the layer's own colour grows as (alpha / full) ** HOLD_POWER


def hold_hue(ground: Image.Image, layer: Image.Image, full: float | None = None) -> Image.Image:
    """`layer` (RGBA: a band, wash or scrim in one colour) with the same alpha and a colour that HOLDS THE GROUND'S HUE
    AND SATURATION where the layer grades out (freeze cycle 5, stream PLATES; panel 6: right of the hook's claim on
    a saturated amber ground the INK wash lost saturation faster than brightness and its hue drifted toward 45-50
    degrees, read as dark amber by six reviewers and as an olive cast by three). Where the layer is at `full` alpha or
    more (default: its own maximum; the text's ground) it stays its own colour, value for value; under that its
    colour eases, by (alpha / full) ** HOLD_POWER, to the ground's own colour scaled down to the layer colour's luma
    (a scale changes neither hue nor saturation; a ground darker than the layer is left as it is, never brightened).
    Composited over `ground` the luma is the plain layer's (so every edge slope and every luma contrast measured on
    it stands); only the chroma of the graded part follows the ground."""
    lay = np.asarray(layer.convert("RGBA"), np.float32)
    g = np.asarray(ground.convert("RGB"), np.float32)
    a = lay[..., 3] / 255.0
    top = float(a.max()) if full is None else float(full)
    if top <= 0:
        return layer
    c = lay[..., :3]
    scale = np.minimum(1.0, (c @ LUMA) / np.maximum(g @ LUMA, 1.0))
    w = np.clip(a / top, 0.0, 1.0) ** HOLD_POWER
    rgb = w[..., None] * c + (1.0 - w[..., None]) * g * scale[..., None]
    out = np.dstack([np.clip(rgb + 0.5, 0, 255), lay[..., 3:4]]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")
