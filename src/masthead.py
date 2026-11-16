"""
src/masthead.py
===============
The masthead, one treatment for every surface that carries it (design-freeze cycle 0, src/brand_system.py): the gold
diamond with its INK edge, then the brand name in the masthead face at weight 700, tracked, in BONE.

Its ground is solved in the order the operator ruled (round 3 (2)): nothing where bone already reaches the target;
else a soft INK gradient from the top edge (full width, never bounded in x, easing out by smoothstep), as light as
the target needs and never past `scrim.masthead_peak` (0.60) at its peak; else that 0.60 gradient plus the name's
own halo up to `halo.masthead_max` (0.16); else, under the same shape, the ground's highlights levelled down (the
park's step 5: no hue changed, never under `scrim.levels_floor`); else the light-ground variant (the name in INK on the
untouched ground) where that reads better.

Contrast is the WORST LOCAL one (cycle 0 round 2, adversary finding 2): the element's ring contrast and, window by
window along the name (half a cap wide, one every quarter cap), the element's ring cut to that window
(`local_contrast`), so a bright stripe behind one letter is never averaged away. Fail closed (finding 1): where no ruled step brings the worst local
contrast to the gate (`contrast.masthead_gate`, 5.5), the masthead is NOT drawn: `draw` returns the image untouched
and the record says `shown: False` with the reason and the best ratio reached (the caller keeps the record where the
lander reads it).

The mark (finding 3): the gold fill is drawn first and the INK edge over it, so the edge survives on any ground (a
gold band included); its non-text contrast (the better of the edge and the fill against the ground around it, WCAG
1.4.11) is recorded against `contrast.mark`.
"""
from __future__ import annotations

import logging
from typing import Any

from PIL import Image, ImageDraw

from . import brand
from .brand_system import colour_of, masthead_spec, tok
from .typography import (
    _RING_INNER,
    _RING_OUTER,
    _lin,
    cap_height,
    get_font,
    plan_text,
    rel_luminance,
    tracked_width,
)

log = logging.getLogger(__name__)

SCRIM_STEPS = 8     # bisection steps for the least scrim peak
LEVELS_STEP = 5     # the levels ceiling is searched in steps of this (255 down to scrim.levels_floor)
LOCAL_WINDOW = 0.5  # the local windows are this many cap heights wide (about half a letter) ...
LOCAL_STEP = 0.25   # ... and start every this many cap heights
LOCAL_MIN_INK = 12  # a window with fewer glyph pixels than this is not measured on its own


def font_for(size: float):
    return get_font(brand.MASTHEAD_FONT, size, weight=tok("masthead.weight"))


def group_width(size: float) -> float:
    """The masthead's drawn width at `size`: diamond, gap, tracked name."""
    spec = masthead_spec(size)
    return 2 * spec["mark_r"] + spec["gap"] + tracked_width(brand.MASTHEAD, font_for(size), spec["track"])


def top_scrim(im: Image.Image, peak: float, reach: float) -> Image.Image:
    """An INK gradient from the top edge (alpha `peak` there) easing out by smoothstep to nothing at y `reach`."""
    import numpy as np
    if peak <= 0:
        return im
    W, H = im.size
    y = np.arange(H, dtype=np.float32)
    v = np.clip(1 - y / max(1.0, float(reach)), 0, 1)
    ramp = v * v * (3 - 2 * v)
    mask = Image.fromarray(np.round(ramp * 255 * peak).astype(np.uint8).reshape(H, 1)).resize((W, H))
    layer = Image.new("RGBA", im.size, tuple(colour_of("plate.shade")) + (255,))
    layer.putalpha(mask)
    return Image.alpha_composite(im.convert("RGBA"), layer).convert(im.mode)


def levelled(im: Image.Image, ceiling: int, reach: float) -> Image.Image:
    """`im` with its output levels scaled to `ceiling` (0-255 -> 0-ceiling) under the top scrim's shape: fully at the
    top edge, easing out by smoothstep to the untouched photograph at y `reach` (no hue changed)."""
    import numpy as np
    W, H = im.size
    lut = [round(v * ceiling / 255) for v in range(256)]
    low = im.convert("RGB").point(lut * 3)
    y = np.arange(H, dtype=np.float32)
    v = np.clip(1 - y / max(1.0, float(reach)), 0, 1)
    mask = Image.fromarray(np.round(v * v * (3 - 2 * v) * 255).astype(np.uint8).reshape(H, 1)).resize((W, H))
    return Image.composite(low, im.convert("RGB"), mask)


def local_contrast(im: Image.Image, plate) -> float | None:
    """The worst ring contrast of `plate` (a TextPlate) on the pixels `im` (as composed), window by window along the
    name: the element's ring (2-10 px outside ALL its glyphs, typography.ring_contrast's rule) is cut into windows
    LOCAL_WINDOW caps wide, one every LOCAL_STEP caps, and each window's ring (90th / 10th percentile, as for the whole
    element) is held against the glyph colour, so a bright stripe behind one letter is never averaged away. None when
    nothing is measurable."""
    import cv2
    import numpy as np
    if plate is None or plate.empty:
        return None
    ox, oy = (int(v) for v in plate.origin)
    h, w = plate.glyphs.shape
    rgb = np.asarray(im.convert("RGB"))[max(0, oy):oy + h, max(0, ox):ox + w]
    g = (plate.glyphs[max(0, -oy):max(0, -oy) + rgb.shape[0], max(0, -ox):max(0, -ox) + rgb.shape[1]] > 127)
    g = g.astype(np.uint8)
    ring = cv2.dilate(g, np.ones((_RING_OUTER, _RING_OUTER), np.uint8)) - cv2.dilate(g, np.ones((_RING_INNER, _RING_INNER), np.uint8))
    ch = max(8, cap_height(plate.font))
    win, step = max(8, round(LOCAL_WINDOW * ch)), max(4, round(LOCAL_STEP * ch))
    lt = rel_luminance(plate.fill)       # the glyphs' own colour (a window can hold too little interior to sample it)
    worst = None
    for x0 in range(0, g.shape[1], step):
        if int(g[:, x0:x0 + win].sum()) < LOCAL_MIN_INK:
            continue
        bg = rgb[:, x0:x0 + win][ring[:, x0:x0 + win] > 0]
        if len(bg) < 50:
            continue
        lb = float(np.percentile(_lin(bg), 90 if plate.light else 10))
        r = round((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05), 2)
        if worst is None or r < worst:
            worst = r
    return worst


def worst_contrast(im: Image.Image, plate) -> float:
    """The element's ratio as judged: the lower of its whole-ring ratio and its worst local window (on `im`)."""
    if plate is None or plate.empty:
        return 0.0
    vals = [v for v in (plate.measure(im), local_contrast(im, plate)) if v is not None]
    return min(vals) if vals else 0.0


def mark_geometry(x0: float, baseline: float, size: float):
    """(the diamond's four points, the masthead spec) for a masthead whose diamond starts at `x0`."""
    spec = masthead_spec(size)
    r = spec["mark_r"]
    cx, cy = x0 + r, baseline - cap_height(font_for(size)) / 2
    return [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], spec


def mark_contrast(ground: Image.Image, diamond) -> float | None:
    """The mark's non-text contrast (WCAG 1.4.11) on `ground` (the pixels before the mark is drawn): the better of its
    INK edge against the brightest of the ground around it (90th percentile of a 2-10 px ring) and its gold fill
    against the darkest (10th percentile) of the same ring."""
    import cv2
    import numpy as np
    m = Image.new("L", ground.size, 0)
    ImageDraw.Draw(m).polygon(diamond, fill=255)
    g = (np.asarray(m) > 127).astype(np.uint8)
    if not g.any():
        return None
    ring = cv2.dilate(g, np.ones((21, 21), np.uint8)) - cv2.dilate(g, np.ones((5, 5), np.uint8))
    bg = np.asarray(ground.convert("RGB"))[ring > 0]
    if len(bg) < 20:
        return None
    lum = _lin(bg)
    hi, lo = float(np.percentile(lum, 90)), float(np.percentile(lum, 10))
    li, lg = rel_luminance(brand.INK), rel_luminance(brand.GOLD)
    edge = (max(li, hi) + 0.05) / (min(li, hi) + 0.05)
    fill = (max(lg, lo) + 0.05) / (min(lg, lo) + 0.05)
    return round(max(edge, fill), 2)


def plan(im: Image.Image, x0: float, baseline: float, size: float, reach: float | None = None,
         target: float | None = None) -> tuple[Image.Image, Any, dict[str, Any]]:
    """(the image with the masthead's ground solved, the name's TextPlate, the record). `x0`: the diamond's left
    point; `baseline`: the name's baseline; `reach`: where the top scrim has eased out (default: baseline + 6 caps).
    The record's `contrast` is the worst local ratio; `shown` False (fail closed) when no ruled step reaches the gate:
    the returned image is then `im` untouched."""
    target = tok("contrast.masthead_target") if target is None else target
    gate = tok("contrast.masthead_gate")
    peak_max, halo_max, halo_step = tok("scrim.masthead_peak"), tok("halo.masthead_max"), tok("halo.step")
    font = font_for(size)
    spec = masthead_spec(size)
    xy = (x0 + 2 * spec["mark_r"] + spec["gap"], baseline)
    reach = baseline + 6 * cap_height(font) if reach is None else reach
    bone, ink = colour_of("masthead.colour_dark"), colour_of("masthead.colour_light")
    base = im.convert("RGB")

    def name(ground, fill, halo_cap=0.0):
        """(plate, worst local ratio): the least halo (in halo.step steps, up to `halo_cap`) at which the WORST local
        ratio reaches the target, else the halo that reads best."""
        best = None
        halo = 0.0
        while True:
            p = plan_text(ground, xy, brand.MASTHEAD, font, fill, "ls", spec["track"], target=target,
                          min_halo=halo, max_halo=halo)
            got = worst_contrast(p.draw(ground), p)
            if best is None or got > best[1]:
                best = (p, got)
            if got >= target or halo >= halo_cap - 1e-9 or not p.light:
                break
            halo = min(halo_cap, halo + halo_step)
        return best

    rec: dict[str, Any] = {"variant": "dark", "scrim_peak": 0.0, "halo": 0.0, "size": round(float(size), 1)}
    p, got = name(base, bone)
    ground = base
    if got < target:
        top = top_scrim(base, peak_max, reach)
        if name(top, bone)[1] >= target:                      # the least peak that reaches the target
            lo, hi = 0.0, peak_max
            for _ in range(SCRIM_STEPS):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if name(top_scrim(base, mid, reach), bone)[1] >= target else (mid, hi)
            ground = top_scrim(base, hi, reach)
            rec["scrim_peak"] = round(hi, 3)
            p, got = name(ground, bone)
        else:                                                 # the full peak, then the halo it allows
            ground, rec["scrim_peak"] = top, peak_max
            p, got = name(ground, bone, halo_max)
    if got < target and rec["scrim_peak"] >= peak_max:
        # Park step 5 (2026-11-04), within the rulings: under the same top-edge shape, the ground's highlights are
        # levelled down (no hue changed, the peak stays 0.60 and the halo 0.16): the least change that reaches the
        # target, else the least that reaches the gate. Only where the 0.60 scrim and the halo left the name short.
        met = None
        for ceiling in range(255 - LEVELS_STEP, tok("scrim.levels_floor") - 1, -LEVELS_STEP):
            trial = top_scrim(levelled(base, ceiling, reach), peak_max, reach)
            q, qgot = name(trial, bone, halo_max)
            if qgot >= gate and met is None:
                met = (ceiling, trial, q, qgot)
            if qgot >= target:
                met = (ceiling, trial, q, qgot)
                break
        if met is not None and met[3] > got:
            rec["levels_ceiling"], ground, p, got = met
    if got < gate:                                            # bone cannot reach the gate: the light-ground variant
        q, qgot = name(base, ink)
        if qgot > got:
            p, ground, got = q, base, qgot
            rec.update(variant="light", scrim_peak=0.0)
            rec.pop("levels_ceiling", None)
    rec.update(halo=p.halo, contrast=round(got, 2), contrast_whole=p.contrast, contrast_unaided=p.contrast_unaided,
               short_of_target=got < target, below_gate=got < gate, shown=got >= gate)
    if got < gate:                                            # fail closed: never drawn under the gate
        rec["reason"] = (f"masthead not drawn: its worst local contrast reaches {got:.2f}:1 after every ruled step "
                         f"(scrim peak {peak_max}, halo {halo_max}, levels floor {tok('scrim.levels_floor')}, light "
                         f"variant), under the {gate}:1 gate")
        log.warning("%s", rec["reason"])
        return base, p, rec
    p.contrast = round(got, 2)       # what the element's record reports: the worst local ratio, as judged
    return ground, p, rec


def draw(im: Image.Image, x0: float, baseline: float, size: float, reach: float | None = None,
         target: float | None = None) -> tuple[Image.Image, Any, dict[str, Any], tuple[int, int, int, int] | None]:
    """The masthead drawn: (image, the name's plate, the record, the group's box including the diamond). When the
    record says `shown: False` (no ruled step reaches the gate) the image comes back untouched, the plate is None and
    the box is None."""
    mode = im.mode
    ground, p, rec = plan(im, x0, baseline, size, reach, target)
    if not rec["shown"]:
        return im, None, rec, None
    out = p.draw(ground.convert("RGB"))
    diamond, spec = mark_geometry(x0, baseline, size)
    rec["mark_contrast"] = mark_contrast(out, diamond)
    d = ImageDraw.Draw(out)
    d.polygon(diamond, fill=brand.GOLD)                        # the fill first ...
    d.polygon(diamond, outline=brand.INK, width=spec["edge"])  # ... then the INK edge over it, so it survives
    r = spec["mark_r"]
    cx, cy = diamond[0][0], diamond[1][1]
    bx = p.box
    box = (int(min(bx[0], cx - r)), int(min(bx[1], cy - r)), int(max(bx[2], cx + r)), int(max(bx[3], cy + r)))
    rec["box"] = list(box)
    return (out if mode == "RGB" else out.convert(mode)), p, rec, box
