"""
src/masthead.py
===============
The masthead, one treatment for every surface that carries it (design-freeze cycle 0, src/brand_system.py): the gold
diamond outlined in INK, then the brand name in the masthead face at weight 700, tracked, in BONE.

Its ground is solved in the order the operator ruled (round 3 (2)): nothing where bone already reaches the target;
else a soft INK gradient from the top edge (full width, never bounded in x, easing out by smoothstep), as light as
the target needs and never past `scrim.masthead_peak` (0.60) at its peak; else that 0.60 gradient plus the name's
own halo up to `halo.masthead_max` (0.16); else, under the same shape, the ground's highlights levelled down (the
park's step 5: no hue changed, never under `scrim.levels_floor`). Where bone still cannot reach the gate (5.5)
the light-ground variant sets the name in INK on the untouched ground (a near-white sky), if that reads better.
Every step is recorded (variant, scrim peak, halo, contrast, short of target, below the gate).
"""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import brand
from .brand_system import colour_of, masthead_spec, tok
from .typography import cap_height, get_font, plan_text, tracked_width

SCRIM_STEPS = 8     # bisection steps for the least scrim peak
LEVELS_STEP = 5     # the levels ceiling is searched in steps of this (255 down to scrim.levels_floor)


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


def plan(im: Image.Image, x0: float, baseline: float, size: float, reach: float | None = None,
         target: float | None = None) -> tuple[Image.Image, Any, dict[str, Any]]:
    """(the image with the masthead's ground solved, the name's TextPlate, the record). `x0`: the diamond's left
    point; `baseline`: the name's baseline; `reach`: where the top scrim has eased out (default: baseline + 6 caps)."""
    target = tok("contrast.masthead_target") if target is None else target
    gate = tok("contrast.masthead_gate")
    peak_max, halo_max = tok("scrim.masthead_peak"), tok("halo.masthead_max")
    font = font_for(size)
    spec = masthead_spec(size)
    xy = (x0 + 2 * spec["mark_r"] + spec["gap"], baseline)
    reach = baseline + 6 * cap_height(font) if reach is None else reach
    bone, ink = colour_of("masthead.colour_dark"), colour_of("masthead.colour_light")
    base = im.convert("RGB")

    def name(ground, fill, halo=0.0):
        return plan_text(ground, xy, brand.MASTHEAD, font, fill, "ls", spec["track"], target=target,
                         min_halo=0.0, max_halo=halo)

    rec: dict[str, Any] = {"variant": "dark", "scrim_peak": 0.0, "halo": 0.0, "size": round(float(size), 1)}
    p = name(base, bone)
    ground = base
    if (p.contrast or 0.0) < target:
        top = top_scrim(base, peak_max, reach)
        if (name(top, bone).contrast or 0.0) >= target:      # the least peak that reaches the target
            lo, hi = 0.0, peak_max
            for _ in range(SCRIM_STEPS):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if (name(top_scrim(base, mid, reach), bone).contrast or 0.0) >= target else (mid, hi)
            ground = top_scrim(base, hi, reach)
            rec["scrim_peak"] = round(hi, 3)
            p = name(ground, bone)
        else:                                                # the full peak, then the halo it allows
            ground, rec["scrim_peak"] = top, peak_max
            p = name(ground, bone, halo_max)
    if (p.contrast or 0.0) < target and rec["scrim_peak"] >= peak_max:
        # Park step 5 (2026-11-04), within the rulings: under the same top-edge shape, the ground's highlights are
        # levelled down (no hue changed, the peak stays 0.60 and the halo 0.16): the least change that reaches the
        # target, else the least that reaches the gate. Only where the 0.60 scrim and the halo left the name short.
        met = None
        for ceiling in range(255 - LEVELS_STEP, tok("scrim.levels_floor") - 1, -LEVELS_STEP):
            trial = top_scrim(levelled(base, ceiling, reach), peak_max, reach)
            q = name(trial, bone, halo_max)
            got = q.contrast or 0.0
            if got >= gate and met is None:
                met = (ceiling, trial, q)
            if got >= target:
                met = (ceiling, trial, q)
                break
        if met is not None and (met[2].contrast or 0.0) > (p.contrast or 0.0):
            rec["levels_ceiling"], ground, p = met
    if (p.contrast or 0.0) < gate:                           # bone cannot reach the gate: the light-ground variant
        q = name(base, ink)
        if (q.contrast or 0.0) > (p.contrast or 0.0):
            p, ground = q, base
            rec.update(variant="light", scrim_peak=0.0)
    rec.update(halo=p.halo, contrast=p.contrast, contrast_unaided=p.contrast_unaided,
               short_of_target=(p.contrast or 0.0) < target, below_gate=(p.contrast or 0.0) < gate)
    return ground, p, rec


def draw(im: Image.Image, x0: float, baseline: float, size: float, reach: float | None = None,
         target: float | None = None) -> tuple[Image.Image, Any, dict[str, Any], tuple[int, int, int, int]]:
    """The masthead drawn: (image, the name's plate, the record, the group's box including the diamond)."""
    mode = im.mode
    ground, p, rec = plan(im, x0, baseline, size, reach, target)
    out = p.draw(ground.convert("RGB"))
    spec = masthead_spec(size)
    r = spec["mark_r"]
    cx, cy = x0 + r, baseline - cap_height(font_for(size)) / 2
    diamond = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    d = ImageDraw.Draw(out)
    d.polygon(diamond, outline=brand.INK, width=spec["edge"])
    d.polygon(diamond, fill=brand.GOLD)
    bx = p.box
    box = (int(min(bx[0], cx - r)), int(min(bx[1], cy - r)), int(max(bx[2], cx + r)), int(max(bx[3], cy + r)))
    rec["box"] = list(box)
    return (out if mode == "RGB" else out.convert(mode)), p, rec, box
