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


def reach(luma: np.ndarray, edge: float, side: str, colour, strength: float = 1.0, minimum: float = 0.0,
          width: int | None = None, floating: bool = False, cap: float | None = None) -> tuple[int, bool]:
    """(the feather, met): the band-edge rule's feather held to its designed reach: the designed form's own ramp on this
    ground (designed_feather; `cap` gives another, e.g. a surface's fixed designed feather). Lander ruling 2026-11-26
    (D1): a ramp is never lengthened past it (never more of the photograph darkened than the designed form did);
    `met` False when the ground-measured limit wants more: the band keeps its designed length, and its corpus row says
    so. The hook plate and the 9:16 myth panel no longer ask: their form is a crisp edge (ruling R1)."""
    w = luma.shape[1]
    width_ = w if width is None else width
    if cap is None:
        cap = designed_feather(luma, edge, side, colour, strength, minimum, width_)
    need = _feather(luma, edge, side, colour, strength, minimum, width, floating, cap)
    return (need, True) if need <= cap else (cap, False)


def designed_feather(luma: np.ndarray, edge: float, side: str, colour, strength: float = 1.0, minimum: float = 0.0,
                     width: int | None = None) -> int:
    """The feather a panel edge had as designed before the freeze cycle 5 limit (cycle 0 round 7: edge_feather at the
    token tok("edge.slope_max"), not the ground's own limit): the ruling's ceiling for a panel's ramp (lander ruling
    2026-11-26: never more of the photograph darkened than the designed form did)."""
    w = luma.shape[1]
    width = w if width is None else width
    length = max(1.0, float(minimum))
    for _ in range(5):
        span = (edge - length, edge) if side == "up" else (edge, edge + length)
        need = edge_feather(step_across(luma, *span, colour, strength), width, minimum)
        if need <= length:
            break
        length = float(need)
    return round(length)


def feather(luma: np.ndarray, edge: float, side: str, colour, strength: float = 1.0, minimum: float = 0.0,
            width: int | None = None, floating: bool = False) -> int:
    """The feather reach() gives, its length only (never past its designed reach)."""
    return reach(luma, edge, side, colour, strength, minimum, width, floating)[0]


def _feather(luma: np.ndarray, edge: float, side: str, colour, strength: float = 1.0, minimum: float = 0.0,
             width: int | None = None, floating: bool = False, cap: float | None = None) -> int:
    """The feather (px) of a band edge at row `edge` easing out toward `side` ("up": the photo above a band, "down":
    the photo below it): the length edge_feather gives for the step measured over the rows it crosses at the limit
    those rows allow (slope_limit; `floating`: the photograph goes on past the band's other edge too), measured
    again over the longer reach (a longer feather is judged on the ground it really covers)."""
    w = luma.shape[1]
    width = w if width is None else width
    length = max(1.0, float(minimum))
    def need_at(n: float) -> int:
        span = (edge - n, edge) if side == "up" else (edge, edge + n)
        # a floating band is laid FLOAT_HEADROOM inside its limit (its feather is judged where it is steepest)
        limit = slope_limit(luma, *span, width, floating) * (FLOAT_HEADROOM if floating else 1.0)
        return edge_feather(step_across(luma, *span, colour, strength, by_row=floating), width, minimum, slope=limit)
    for _ in range(5):
        if cap is not None and length > cap:   # judged once more over the capped reach itself
            return int(cap) if need_at(cap) <= cap else round(length)
        need = need_at(length)
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


# The grade-out holds the ground's hue for as long as the ledger's check reads it (PLATES-02: "until luma < 20"). Review
# of the c5 fix (D4): with a power of the alpha (HOLD_POWER 6, then 10) the colour snapped back to INK between 0.8 and
# 0.97 of full, where a bright ground's composite is still 20-60 luma: 64 of 127 grounds drifted over 5 degrees
# there (47 at worst). The weight is now the composite's own luma: the ground's colour wherever the composite is at
# least HOLD_LUMA, the layer's own under HOLD_LUMA - HOLD_SPAN (where hue no longer reads), smoothstepped between.
HOLD_LUMA = 20.0
HOLD_SPAN = 8.0


def hold_hue(ground: Image.Image, layer: Image.Image, full: float | None = None) -> Image.Image:
    """`layer` (RGBA: a band, wash or scrim in one colour) with the same alpha and a colour that HOLDS THE GROUND'S HUE
    AND SATURATION (freeze cycle 5, stream PLATES; panel 6: right of the hook's claim on a saturated amber ground the
    INK wash lost saturation faster than brightness and its hue drifted toward 45-50 degrees, read as dark amber by
    six reviewers and as an olive cast by three): its colour is the ground's own colour scaled down to the layer
    colour's luma (a scale changes neither hue nor saturation; a ground darker than the layer is left as it is, never
    brightened) wherever the composite is at least HOLD_LUMA, and the layer's own colour where the composite is under
    HOLD_LUMA - HOLD_SPAN (smoothstepped between). Composited over `ground` the luma is the plain layer's at every
    pixel (so every edge slope and every luma contrast measured on it stands); only the chroma follows the ground.
    Lander ruling R2 (2026-11-26): only inside the grade-out (the layer under `full`, default its own maximum); the
    body at full strength, under the type, is the palette's INK, value for value (one brand system).
    """
    lay = np.asarray(layer.convert("RGBA"), np.float32)
    g = np.asarray(ground.convert("RGB"), np.float32)
    a = lay[..., 3] / 255.0
    if float(a.max()) <= 0:
        return layer
    c = lay[..., :3]
    lc, lg = c @ LUMA, g @ LUMA
    scale = np.minimum(1.0, lc / np.maximum(lg, 1.0))
    comp = (1.0 - a) * lg + a * lc                  # the composite's luma (the same for either colour)
    w = smoothstep((HOLD_LUMA - comp) / HOLD_SPAN)  # 0: the ground's colour, 1: the layer's own
    top = float(a.max()) if full is None else float(full)
    w = np.where(a >= top - 1.0 / 255.0, 1.0, w)    # ruling R2: the body under the type is the palette's INK, always
    rgb = w[..., None] * c + (1.0 - w[..., None]) * g * scale[..., None]
    out = np.dstack([np.clip(rgb + 0.5, 0, 255), lay[..., 3:4]]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


# Freeze cycle 5 (stream PLATES, PLATES-03; panel 6: the scrim behind the copy on a bright, coloured ground read as a
# grey-brown veil: Aswan's granite, Richat's sand): a neutral INK alpha over a saturated ground pulls every pixel
# toward the INK's own grey, so the band's chroma falls faster than its value. ONE rule for a text band that never
# goes opaque: it GRADES the photograph, each pixel the ground's own colour scaled to the luma the INK alpha would
# give it (graded()), so hue and saturation stay the ground's and every luma (each edge slope, each contrast measured
# on luma) is the INK alpha's, value for value. An opaque panel (the split's INK panel) stays its colour.
def graded(ground: Image.Image, alpha: np.ndarray, colour, full: float | None = None) -> Image.Image:
    """`ground` under a `colour` band of per-pixel `alpha` (H x W, 0-1), graded where it grades out: each pixel scaled
    by (1 - a) + a x min(1, luma(colour) / luma(pixel)), its luma the plain alpha composite's (a pixel darker than the
    band colour is left as it is, never brightened). Where the band is at `full` strength or more (default: its own
    maximum), under the type, it is the plain `colour` alpha, value for value: lander rulings R2 / N2 (2026-11-26,
    review 2): the type's ground is the palette's INK and its contrast never changes. RGB out."""
    g = np.asarray(ground.convert("RGB"), np.float32)
    a = np.clip(np.asarray(alpha, np.float32), 0.0, 1.0)
    c = np.asarray(colour[:3], np.float32)
    lc = float(np.dot(c, LUMA))
    k = np.minimum(1.0, lc / np.maximum(g @ LUMA, 1e-3))
    out = g * ((1.0 - a) + a * k)[..., None]
    top = float(a.max()) if full is None else float(full)
    body = a >= top - 1.0 / 255.0
    out[body] = (g * (1.0 - a)[..., None] + c * a[..., None])[body]
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


# Freeze cycle 5 (stream PLATES, PLATES-15; panel 6: Story gold lines at 4.92-5.18:1, carousel headings and bodies at
# 4.8-5.3 on bright photographs, the Richat closing slide's body under 7:1 once its photograph moved): a text band's
# depth was solved for the band's own 95th-percentile pixel, while each line is judged on the brightest pixels round
# ITS glyphs, so a bright patch under one line was answered by that line's halo, raised only to the 4.8 placement
# target. ONE rule: a band laid behind light text over a photograph is at least as deep as its LINES need to read at
# tok("contrast.high") (7:1) against the TEXT_PCT percentile of their own ground (the line's ink box and TEXT_RING px
# round it), as composited; deeper only where the band's own rule asks it; never past the caller's cap (the line
# is then at the best the band gives, and the caller records the shortfall). Kickers and the masthead keep their own
# ruled labels (plan_label, masthead.plan); they are not rows here.
TEXT_PCT = 97.0     # per glyph-wide slice: the line is judged glyph by glyph on its worst 5 % (typography.GLYPH_PCT)
TEXT_RING = 4       # px round the line's ink box: a glyph's immediate surround (typography.GLYPH_SURROUND)
TEXT_SLICE = 0.7    # a slice's width, in the line's height: about one glyph
# the band is planned this much above the target (as placement plans 4.8 for the 4.5 gate): the band's 8-bit alpha,
# the glyph's antialiased ink and the composed measure read a planned 7.0 at 6.87-6.97 on the dev Story frames, and
# 1.04 still left 11 of 1,103 dev corpus lines at 6.63-6.98 (a heading's own ring, a stat over the split's fade)
TEXT_PLAN_MARGIN = 1.10


def _lin(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def text_target() -> float:
    """The ratio a line over a photograph is shaded to read at (tok contrast.high)."""
    return float(GATES["contrast.high"])


def text_strength(im: Image.Image, rows, colour, target: float | None = None, pct: float = TEXT_PCT,
                  ring: int = TEXT_RING, grade: bool = True) -> float:
    """The band alpha (0-1) at which every row of `rows` ((box, fill): the line's ink box on `im`, canvas px, and
    its colour) reads at `target` (text_target(): 7:1; planned TEXT_PLAN_MARGIN above it) against its ground once a `colour` band of that alpha is laid
    over it, composited as the band composites (per channel, in sRGB): the line is cut into glyph-wide slices
    (TEXT_SLICE of its height) and each slice's ground (its columns and `ring` px round them) is read at its `pct`
    percentile of relative luminance, as each glyph is judged on the worst of its own surround; `grade`: the band
    grades the photograph (graded(), PLATES-03), else it is a plain alpha of `colour`. 0 when every row
    already reads; 1.0 + the shortfall when even an opaque band does not. Rows whose fill is darker than mid-grey
    (dark type on a light ground) are not rows of a dark band and are skipped."""
    target = (text_target() if target is None else float(target)) * TEXT_PLAN_MARGIN
    W, H = im.size
    rgb = None
    c = np.asarray(colour[:3], np.float32) / 255.0
    worst = 0.0
    for box, fill in rows:
        lf = float(_lin(np.asarray(fill[:3], np.float32) / 255.0) @ LUMA)
        if lf < 0.18:
            continue
        x0, y0 = max(0, int(box[0]) - ring), max(0, int(box[1]) - ring)
        x1, y1 = min(W, int(box[2]) + ring), min(H, int(box[3]) + ring)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        if rgb is None:
            rgb = im.convert("RGB")
        a = np.asarray(rgb.crop((x0, y0, x1, y1)), np.float32) / 255.0
        step = max(4, round(TEXT_SLICE * (y1 - y0)))
        slices = [a[:, i:i + step + 2 * ring].reshape(-1, 3) for i in range(0, max(1, a.shape[1] - 2 * ring), step)]
        bright = (lf + 0.05) / target - 0.05   # the ground's relative luminance at which the line reads at `target`

        lc = float(c @ LUMA)
        ks = [np.minimum(1.0, lc / np.maximum(g @ LUMA, 1e-4))[:, None] for g in slices]

        def at(s: float, slices=slices, ks=ks) -> float:
            if grade:
                return max(float(np.percentile(_lin(g * ((1.0 - s) + s * k)) @ LUMA, pct)) for g, k in zip(slices, ks))
            return max(float(np.percentile(_lin(g * (1.0 - s) + c * s) @ LUMA, pct)) for g in slices)
        if at(0.0) <= bright:
            continue
        if at(1.0) > bright:
            worst = max(worst, 1.0 + (at(1.0) - bright))
            continue
        lo, hi = 0.0, 1.0
        for _ in range(12):
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if at(mid) <= bright else (mid, hi)
        worst = max(worst, hi)
    return float(worst)
