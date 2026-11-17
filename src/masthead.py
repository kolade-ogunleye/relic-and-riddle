"""
src/masthead.py
===============
The masthead, one treatment for every surface that carries it (design-freeze cycle 0, src/brand_system.py): the gold
diamond with its INK edge, then the brand name in the masthead face at weight 700, tracked, in BONE.

Its ground is solved in the order the operator ruled (round 3 (2)): nothing where bone already reaches the target;
else a soft INK gradient from the top edge (full width, never bounded in x, easing out by smoothstep), as light as
the target needs and never past `scrim.masthead_peak` (0.60) at its peak; else that 0.60 gradient plus the name's
own halo up to `halo.masthead_max` (0.16); else, under the same shape, the ground's highlights levelled down (the
park's step 5: no hue changed, never under `scrim.levels_floor`); else the light-ground variant (the name in INK) where
that reads better. Round 7 (preview panel F7: the INK variant was never chosen, even on near-white): the light variant
sits on its own least LIFT, a bone gradient from the top edge in the dark scrim's shape and under its cap
(scrim.masthead_peak), recorded as `light_lift`; it is taken where bone under the full dark scrim and its halo stays
short of the target and the light variant reaches it (only a light ground lets INK reach the target), and again
before the exception wherever bone misses the gate; else (operator ruling 4, 2026-11-16) the HALO EXCEPTION: the least halo
above HALO_CAP (0.16), up to HALO_EXCEPTION_CAP (0.24), that brings the bone name to the gate, recorded as
`halo_exception` (brand_system's gate pair; no brand moves either).

One gate (cycle 0 round 6): every masthead on every surface passes `verify` (its room, its name at the gate, its mark
at contrast.mark on the pixels it changes) before anything of it is drawn, and the mark is drawn only by `draw_mark`;
`draw` here, the end card and end screen, and the cover and hook paths all call them.

Contrast is judged GLYPH BY GLYPH (cycle 0 rounds 2-3, adversary findings 2 and r2-1): the element's ring contrast on
the composed pixels and, for every glyph (a connected component of the ink), the ground under its own ink pixels plus
its immediate surround, at the 95th percentile (`glyph_contrast`, `judge`): no more than 5 % of any glyph may sit on
ground that fails the ratio, whatever a feature's width, offset or angle. Fail closed (finding 1): where no ruled step brings the worst local
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
from .brand_system import HALO_EXCEPTION_CAP, colour_of, masthead_spec, tok
from .typography import (
    GLYPH_MIN_PX,
    GLYPH_PCT,  # noqa: F401  (the per-glyph method's constants, kept importable here)
    GLYPH_SURROUND,  # noqa: F401
    RING_PCT,
    _lin,
    _tail,
    cap_height,
    get_font,
    plan_text,
    rel_luminance,
    tracked_width,
)
from .typography import glyph_contrast as typography_glyph_contrast

log = logging.getLogger(__name__)

SCRIM_STEPS = 8     # bisection steps for the least scrim peak
LEVELS_STEP = 5     # the levels ceiling is searched in steps of this (255 down to scrim.levels_floor)


def font_for(size: float):
    return get_font(brand.MASTHEAD_FONT, size, weight=tok("masthead.weight"))


FIT_STEP = 2.0   # px: the masthead's fit ladder steps down by this from its canvas size to its floor


def fit_ladder(size: float) -> list[float]:
    """The masthead's declared fit ladder on a canvas whose masthead size is `size`: `size`, then FIT_STEP px steps
    down to tok("masthead.fit_floor") x `size` (a token with a declared range, read now), the exact floor its last step
    (round 6: the 2 px steps from 53 1/3 px stopped at 41 1/3 and refused a name that fits at the 40 px floor)."""
    floor = tok("masthead.fit_floor") * size
    steps, s = [], float(size)
    while s >= floor - 1e-9:
        steps.append(s)
        s -= FIT_STEP
    if steps[-1] > floor + 1e-9:
        steps.append(floor)
    return steps


def fit_size(size: float, room: float, x0: float = 0.0) -> float | None:
    """The largest size on the fit ladder whose group fits `room`; None below the floor (the masthead is refused).
    `x0`: the diamond's left point (its fraction places the name's pixels; group_width)."""
    return next((s for s in fit_ladder(size) if group_width(s, x0) <= room + 1e-9), None)


def group_width(size: float, x0: float = 0.0) -> float:
    """The masthead's DRAWN width at `size`: from the diamond's left point `x0` to the right edge of the last pixel the
    group draws (measure sweep r7 #6: the tracked advance omitted the last glyph's overhang, and "AVAVAV" drew 1.8 px
    past the room it was accepted in). The name is rasterised as the plate draws it (font_engine.draw_run, tracked,
    at `x0`'s fraction), so its overhang, combining marks and antialiased edge are counted; the diamond spans 2 x
    mark_r. Unrounded; the left edge is x0 unless an ink overhang reaches further left."""
    import math

    from .font_engine import draw_run
    spec = masthead_spec(size)
    font = font_for(size)
    lead = 2 * spec["mark_r"] + spec["gap"]
    frac = float(x0) - math.floor(float(x0))
    margin = math.ceil(2 * size) + 8
    adv = tracked_width(brand.MASTHEAD, font, spec["track"])
    W, H = math.ceil(frac + lead + max(0.0, adv) + 2 * margin), math.ceil(4 * size) + 16
    m = Image.new("L", (W, H), 0)
    draw_run(ImageDraw.Draw(m), (margin + frac + lead, 2 * size + 8), brand.MASTHEAD, font, 255, "ls", spec["track"])
    bb = m.getbbox()
    left, right = margin + frac, margin + frac + 2 * spec["mark_r"]
    if bb is not None:
        left, right = min(left, float(bb[0])), max(right, float(bb[2]))
    return right - left


def top_scrim(im: Image.Image, peak: float, reach: float, colour=None) -> Image.Image:
    """An INK gradient from the top edge (alpha `peak` there) easing out by smoothstep to nothing at y `reach`;
    `colour` another fill (the light-ground variant's lift: masthead.colour_dark, bone)."""
    import numpy as np
    if peak <= 0:
        return im
    W, H = im.size
    y = np.arange(H, dtype=np.float32)
    v = np.clip(1 - y / max(1.0, float(reach)), 0, 1)
    ramp = v * v * (3 - 2 * v)
    mask = Image.fromarray(np.round(ramp * 255 * peak).astype(np.uint8).reshape(H, 1)).resize((W, H))
    fill = tuple(colour_of("plate.shade")) if colour is None else tuple(colour)
    layer = Image.new("RGBA", im.size, fill[:3] + (255,))
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


def glyph_contrast(ground: Image.Image, plate) -> tuple[float | None, dict[str, Any]]:
    """The worst GLYPH of `plate` (a TextPlate) on `ground` (the pixels the text is drawn on, before it is drawn; the
    plate's own halo is composited first, as it will be): (ratio, UNROUNDED, or None; detail). The ONE per-glyph
    method every text element is judged by (typography.glyph_contrast, planned, over the plate's shaped glyphs:
    measure sweep r7 #4), here for the masthead's planner and gate."""
    import numpy as np
    if plate is None or plate.empty:
        return None, {}
    under = plate.under(plate._crop(ground.convert("RGB")), plate.halo)
    worst, detail = typography_glyph_contrast(np.asarray(under.convert("RGB")), plate.glyphs, (0, 0), plate.fill,
                                              drawn=False, parts=plate.glyph_parts())
    if "worst_glyph_x" in detail:
        detail["worst_glyph_x"] += int(plate.origin[0])
    return worst, detail


def window_contrast(im: Image.Image, plate) -> float | None:
    """The worst ring contrast of `plate` on the composed pixels `im`, window by window along the name (round 2): the
    element's 2-10 px ring cut into windows half a cap wide, one every quarter cap, each window's
    worse tail BY THE RATIO (any fill, sweep r7 #3) against the glyph colour, UNROUNDED: it reads the wider ring the glyph
    surround does not (judge)."""
    import cv2
    import numpy as np
    if plate is None or plate.empty:
        return None
    ox, oy = (int(v) for v in plate.origin)
    h, w = plate.glyphs.shape
    rgb = np.asarray(im.convert("RGB"))[max(0, oy):oy + h, max(0, ox):ox + w]
    g = (plate.glyphs[max(0, -oy):max(0, -oy) + rgb.shape[0], max(0, -ox):max(0, -ox) + rgb.shape[1]] > 127)
    g = g.astype(np.uint8)
    ring = cv2.dilate(g, np.ones((21, 21), np.uint8)) - cv2.dilate(g, np.ones((5, 5), np.uint8))
    ch = max(8, cap_height(plate.font))
    win, step = max(8, round(0.5 * ch)), max(4, round(0.25 * ch))
    lt = rel_luminance(plate.fill)
    worst = None
    for x0 in range(0, g.shape[1], step):
        if int(g[:, x0:x0 + win].sum()) < GLYPH_MIN_PX:
            continue
        bg = rgb[:, x0:x0 + win][ring[:, x0:x0 + win] > 0]
        if len(bg) < 50:
            continue
        r = _tail(_lin(bg), lt, RING_PCT)       # the worse tail by the ratio, any fill (sweep r7 #3); unrounded
        if worst is None or r < worst:
            worst = r
    return worst


def judge_detail(ground: Image.Image, plate) -> dict[str, float | None]:
    """Every reading the gate takes of the name, UNROUNDED, so a record can say which one bound: `glyph`
    (glyph_contrast, the ground under each glyph before it is drawn), `drawn` (TextPlate.measure on the composed
    pixels: the whole 2-10 px ring and each glyph's ink as drawn against its surround) and `window`
    (window_contrast, the 2-10 px ring half a cap at a time)."""
    if plate is None or plate.empty:
        return {"glyph": None, "drawn": None, "window": None}
    composed = plate.draw(ground)
    return {"glyph": glyph_contrast(ground, plate)[0], "drawn": plate.measure(composed),
            "window": window_contrast(composed, plate)}


def judge(ground: Image.Image, plate) -> float:
    """The element's ratio as judged, UNROUNDED: the lowest reading of judge_detail. The windowed ring stays in the
    minimum (lane MEASURE r7, real-runs sweep (b)): it reads ground 5-10 px from the ink that the glyph surround
    (1..4 px) does not, and on a bright sky with a dark mass under some letters (tests/test_brand_system_r6.py) it is
    the reading that binds, 0.25-0.42 under the glyph and whole-ring readings; on the round-2/3 adversaries it adds
    nothing the grouped per-glyph method misses."""
    vals = [v for v in judge_detail(ground, plate).values() if v is not None]
    return min(vals) if vals else 0.0


def mark_geometry(x0: float, baseline: float, size: float):
    """(the diamond's four points, the masthead spec) for a masthead whose diamond starts at `x0`."""
    spec = masthead_spec(size)
    r = spec["mark_r"]
    cx, cy = x0 + r, baseline - cap_height(font_for(size)) / 2
    return [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], spec


MARK_MIN_PX = 12   # a part of the mark (its gold fill, its INK edge) with fewer visible pixels than this does not count


def mark_contrast(before: Image.Image, after: Image.Image, diamond, edge: int, fill=None,
                  edge_colour: Any = "INK") -> tuple[float, dict[str, int]]:
    """The mark's non-text contrast (WCAG 1.4.11) measured on the pixels it ACTUALLY changed (round 5: an edge as wide
    as the radius left no gold, and an INK edge on an INK ground is no mark at all): (ratio, visible px per part).
    The changed pixels inside the diamond grown by its edge are split by colour into the gold fill and the INK edge;
    a part with at least MARK_MIN_PX visible pixels counts: the edge against the brightest of the ground around the
    mark (90th percentile of a ring 2-10 px out, on `before`), the fill against the darkest (10th). The ratio is the
    better counting part's, 1.0 when no part is visible. `fill` (default brand.GOLD) and `edge_colour` (default
    brand.INK; None: a mark drawn without an edge) are the colours it was drawn in."""
    import cv2
    import numpy as np
    m = Image.new("L", before.size, 0)
    ImageDraw.Draw(m).polygon(diamond, fill=255)
    g = (np.asarray(m) > 127).astype(np.uint8)
    if not g.any():
        return 1.0, {"fill": 0, "edge": 0}
    k = 2 * int(edge) + 3
    region = cv2.dilate(g, np.ones((k, k), np.uint8)) > 0
    b = np.asarray(before.convert("RGB")).astype(np.int16)
    a = np.asarray(after.convert("RGB")).astype(np.int16)
    changed = region & (np.abs(a - b).max(axis=2) > 8)
    gold = np.array(brand.GOLD if fill is None else fill, np.int16)
    if edge_colour is None:
        near_gold = np.ones(changed.shape, bool)
    else:
        ink = np.array(brand.INK if edge_colour == "INK" else edge_colour, np.int16)
        near_gold = np.abs(a - gold).sum(axis=2) < np.abs(a - ink).sum(axis=2)
    parts = {"fill": changed & near_gold, "edge": changed & ~near_gold}
    counts = {name: int(mask.sum()) for name, mask in parts.items()}
    rg = region.astype(np.uint8)
    ring = (cv2.dilate(rg, np.ones((21, 21), np.uint8)) - cv2.dilate(rg, np.ones((5, 5), np.uint8))) > 0
    bg = np.asarray(before.convert("RGB"))[ring]
    if len(bg) < 20:
        return 1.0, counts
    lum = _lin(bg)
    hi, lo = float(np.percentile(lum, 90)), float(np.percentile(lum, 10))
    best = 1.0
    if counts["edge"] >= MARK_MIN_PX:
        le = float(np.median(_lin(np.asarray(after.convert("RGB"))[parts["edge"]])))
        best = max(best, (max(le, hi) + 0.05) / (min(le, hi) + 0.05))
    if counts["fill"] >= MARK_MIN_PX:
        lf = float(np.median(_lin(np.asarray(after.convert("RGB"))[parts["fill"]])))
        best = max(best, (max(lf, lo) + 0.05) / (min(lf, lo) + 0.05))
    return round(best, 2), counts


def plan(im: Image.Image, x0: float, baseline: float, size: float, reach: float | None = None,
         target: float | None = None, max_width: float | None = None) -> tuple[Image.Image, Any, dict[str, Any]]:
    """(the image with the masthead's ground solved, the name's TextPlate, the record). `x0`: the diamond's left
    point; `baseline`: the name's baseline; `reach`: where the top scrim has eased out (default: baseline + 6 caps);
    `max_width`: the room the group may take (default: the canvas less x0 each side). The record's `contrast` is the
    worst glyph's ratio as judged (`judge`); `best_tried` the best any tried step reached. `shown` False (fail closed)
    when no ruled step reaches the gate or the group does not fit its room: the returned image is then `im` untouched
    and `reason` says why."""
    target = tok("contrast.masthead_target") if target is None else target
    gate = tok("contrast.masthead_gate")
    peak_max, halo_max, halo_step = tok("scrim.masthead_peak"), tok("halo.masthead_max"), tok("halo.step")
    room = (im.width - 2 * x0) if max_width is None else max_width
    asked = size
    fitted = fit_size(size, room)          # round 5: the declared fit ladder, never a clip
    size = fitted if fitted is not None else size
    font = font_for(size)
    spec = masthead_spec(size)
    xy = (x0 + 2 * spec["mark_r"] + spec["gap"], baseline)
    reach = baseline + 6 * cap_height(font) if reach is None else reach
    bone, ink = colour_of("masthead.colour_dark"), colour_of("masthead.colour_light")
    base = im.convert("RGB")
    width = group_width(size)
    rec: dict[str, Any] = {"variant": "dark", "scrim_peak": 0.0, "halo": 0.0, "size": round(float(size), 1),
                           "size_px": float(size), "size_asked": round(float(asked), 1),
                           "group_width": round(width, 1), "room": round(float(room), 1)}
    if fitted is None:   # round 3, finding 7: never clipped silently: refused below the fit ladder's floor
        floor = fit_ladder(asked)[-1]   # round 6: the TRUE floor (the ladder's exact last step), its width there
        rec.update(shown=False, contrast=None, best_tried=None, short_of_target=True, below_gate=True,
                   fit_floor_px=round(floor, 3), floor_width=round(group_width(floor), 1),
                   reason=(f"masthead not drawn: its group is {group_width(floor):.1f} px wide at the fit ladder's "
                           f"floor ({floor:.1f} px; {width:.1f} px at {asked:.1f} px), its room {room:.1f} px"))
        log.warning("%s", rec["reason"])
        p = plan_text(base, xy, brand.MASTHEAD, font, bone, "ls", spec["track"], target=target, max_halo=0.0)
        return base, p, rec
    seen = {"best": 0.0, "step": None, "dark": 0.0, "light": 0.0}   # the best overall and per variant

    def name(ground, fill, halo_cap=0.0, step="", start=0.0, inc=None, goal=None):
        """(plate, judged ratio): the least halo (from `start` in `inc` steps, halo.step by default, up to `halo_cap`)
        at which the judged ratio reaches `goal` (the target by default), else the halo that reads best. Every try
        updates the best seen (the record's best_tried)."""
        best = None
        halo = start
        inc = halo_step if inc is None else inc
        goal = target if goal is None else goal
        while True:
            p = plan_text(ground, xy, brand.MASTHEAD, font, fill, "ls", spec["track"], target=target,
                          min_halo=halo, max_halo=halo)
            got = judge(ground, p)
            if got > seen["best"]:
                seen.update(best=got, step=f"{step} halo {round(halo, 2)}".strip())
            variant = "dark" if tuple(fill) == tuple(bone) else "light"
            seen[variant] = max(seen[variant], got)
            if best is None or got > best[1]:
                best = (p, got)
            if got >= goal or halo >= halo_cap - 1e-9 or not p.light:
                break
            halo = min(halo_cap, halo + inc)
        return best

    def light(goal):
        """(plate, ratio, ground, lift) of the light-ground variant (the name in INK) on the least bone LIFT from the
        top edge (the dark scrim's shape and cap, scrim.masthead_peak) at which it reaches `goal`; None if even the
        full lift does not (cycle 0 round 7, preview panel F7: the INK variant was never chosen on near-white)."""
        q, qgot = name(base, ink, step="light variant")
        if qgot >= goal:
            return q, qgot, base, 0.0
        full = top_scrim(base, peak_max, reach, bone)
        q, qgot = name(full, ink, step=f"light variant lift {peak_max}")
        if qgot < goal:
            return None
        lo, hi = 0.0, peak_max
        for _ in range(SCRIM_STEPS):
            mid = (lo + hi) / 2
            ok = name(top_scrim(base, mid, reach, bone), ink, step=f"light variant lift {mid:.3f}")[1] >= goal
            lo, hi = (lo, mid) if ok else (mid, hi)
        g = top_scrim(base, hi, reach, bone)
        q, qgot = name(g, ink, step=f"light variant lift {hi:.3f}")
        return q, qgot, g, round(hi, 3)

    def take_light(found):
        nonlocal p, got, ground
        p, got, ground, lift = found
        rec.update(variant="light", scrim_peak=0.0, light_lift=lift)
        rec.pop("levels_ceiling", None)

    p, got = name(base, bone, step="bone")
    ground = base
    if got < target:
        top = top_scrim(base, peak_max, reach)
        if name(top, bone, step=f"scrim {peak_max}")[1] >= target:   # the least peak that reaches the target
            lo, hi = 0.0, peak_max
            for _ in range(SCRIM_STEPS):
                mid = (lo + hi) / 2
                ok = name(top_scrim(base, mid, reach), bone, step=f"scrim {mid:.3f}")[1] >= target
                lo, hi = (lo, mid) if ok else (mid, hi)
            ground = top_scrim(base, hi, reach)
            rec["scrim_peak"] = round(hi, 3)
            p, got = name(ground, bone, step=f"scrim {hi:.3f}")
        else:                                                 # the full peak, then the halo it allows
            ground, rec["scrim_peak"] = top, peak_max
            p, got = name(ground, bone, halo_max, step=f"scrim {peak_max}")
    if got < target and rec["scrim_peak"] >= peak_max:
        # F7 (round 7): where bone under the full dark scrim and its halo stays short of the target, the light-ground
        # variant on its least lift is taken when IT reaches the target (it reads better; ruling 1: INK only on light
        # grounds, and only a light ground lets INK reach the target under a lift within the scrim's cap)
        found = light(target)
        if found is not None:
            take_light(found)
    if got < target and rec["scrim_peak"] >= peak_max:
        # Park step 5 (2026-11-04), within the rulings: under the same top-edge shape, the ground's highlights are
        # levelled down (no hue changed, the peak stays 0.60 and the halo 0.16): the least change that reaches the
        # target, else the least that reaches the gate. Only where the 0.60 scrim and the halo left the name short.
        met = None
        for ceiling in range(255 - LEVELS_STEP, tok("scrim.levels_floor") - 1, -LEVELS_STEP):
            trial = top_scrim(levelled(base, ceiling, reach), peak_max, reach)
            q, qgot = name(trial, bone, halo_max, step=f"scrim {peak_max} levels {ceiling}")
            if qgot >= gate and met is None:
                met = (ceiling, trial, q, qgot)
            if qgot >= target:
                met = (ceiling, trial, q, qgot)
                break
        if met is not None and met[3] > got:
            rec["levels_ceiling"], ground, p, got = met
    if got < gate:                         # bone cannot reach the gate: the light-ground variant, on its least lift
        found = light(gate)
        if found is not None:
            take_light(found)
        else:
            q, qgot = name(base, ink, step="light variant")
            if qgot > got:
                p, ground, got = q, base, qgot
                rec.update(variant="light", scrim_peak=0.0, light_lift=0.0)
                rec.pop("levels_ceiling", None)
    rec["halo_exception"] = False
    if got < gate:
        # Operator ruling 4 (2026-11-16): only where no ruled step within scrim 0.60 / halo 0.16 (and the light
        # variant) reached the gate, the bone name may take a halo above HALO_CAP up to HALO_EXCEPTION_CAP: the LEAST
        # halo (halo.exception_step steps) at which it reaches the gate, on the least levelled ground at that halo.
        inc = tok("halo.exception_step")
        trials = [(None, top_scrim(base, peak_max, reach))] + [
            (c, top_scrim(levelled(base, c, reach), peak_max, reach))
            for c in range(255 - LEVELS_STEP, tok("scrim.levels_floor") - 1, -LEVELS_STEP)]
        halos, h = [], halo_max + inc
        while h <= HALO_EXCEPTION_CAP + 1e-9:
            halos.append(min(h, HALO_EXCEPTION_CAP))
            h += inc
        found = None
        for h in halos:
            for ceiling, trial in trials:
                q, qgot = name(trial, bone, h, step=f"exception scrim {peak_max} levels {ceiling or 255}",
                               start=h, goal=gate)
                if qgot >= gate:
                    found = (ceiling, trial, q, qgot)
                    break
            if found:
                break
        if found:
            ceiling, ground, p, got = found
            rec.update(variant="dark", scrim_peak=peak_max, halo_exception=True,
                       halo_exception_cap=HALO_EXCEPTION_CAP)
            rec.pop("levels_ceiling", None)
            if ceiling is not None:
                rec["levels_ceiling"] = ceiling
            log.info("masthead: halo exception (ruling 4): halo %.2f reaches %.2f:1", p.halo, got)
    _, detail = glyph_contrast(ground, p)
    rec.update(halo=p.halo, contrast=round(got, 2), contrast_whole=p.measure(p.draw(ground)),
               contrast_unaided=p.contrast_unaided, best_tried=round(seen["best"], 2), best_step=seen["step"],
               best_dark=round(seen["dark"], 2), best_light=round(seen["light"], 2),
               glyphs=detail.get("glyphs"), worst_glyph_x=detail.get("worst_glyph_x"),
               short_of_target=got < target, below_gate=got < gate, shown=got >= gate)
    if got < gate:                                            # fail closed: never drawn under the gate
        rec["contrast"] = round(seen["best"], 2)              # a refusal reports the best any step reached
        rec["reason"] = (f"masthead not drawn: its worst glyph reaches at best {seen['best']:.2f}:1 ({seen['step']}) "
                         f"after every ruled step (scrim peak {peak_max}, halo {halo_max}, levels floor "
                         f"{tok('scrim.levels_floor')}, light variant, halo exception up to {HALO_EXCEPTION_CAP}), "
                         f"under the {gate}:1 gate (bone best "
                         f"{seen['dark']:.2f}, INK best {seen['light']:.2f})")
        log.warning("%s", rec["reason"])
        return base, p, rec
    p.contrast = round(got, 2)       # what the element's record reports: its ratio as judged (the worst glyph)
    return ground, p, rec


def draw_mark(d: ImageDraw.ImageDraw, diamond, edge: int, alpha: int | None = None, fill=None,
              edge_colour: Any = "INK") -> None:
    """THE drawer of the brand's mark (cycle 0 round 6: every surface draws it here, after `verify` passed it): the
    fill first (brand.GOLD by default), then the edge over it (brand.INK; None: no edge), so the edge survives on a
    gold ground. `alpha` (0-255) draws on an RGBA layer at that opacity; None draws opaque RGB."""
    fill = brand.GOLD if fill is None else tuple(fill)
    edge_colour = brand.INK if edge_colour == "INK" else edge_colour
    if alpha is None:
        d.polygon(diamond, fill=fill)
        if edge_colour is not None:
            d.polygon(diamond, outline=edge_colour, width=edge)
    else:
        d.polygon(diamond, fill=(*fill[:3], int(alpha)))
        if edge_colour is not None:
            d.polygon(diamond, outline=(*tuple(edge_colour)[:3], int(alpha)), width=edge)


def exception_plate(ground: Image.Image, plate_at) -> tuple[Any, float] | None:
    """Operator ruling 4 (2026-11-16) for a surface whose ground the masthead cannot scrim (the end card's, which
    moves): (plate, judged ratio) at the LEAST halo above HALO_CAP, in halo.exception_step steps up to
    HALO_EXCEPTION_CAP, at which `plate_at(halo)` (the name planned at exactly that halo) reaches the gate on
    `ground`; None when none does. Only for a name that missed the gate within HALO_CAP."""
    gate, inc = tok("contrast.masthead_gate"), tok("halo.exception_step")
    h = tok("halo.masthead_max") + inc
    while h <= HALO_EXCEPTION_CAP + 1e-9:
        p = plate_at(min(h, HALO_EXCEPTION_CAP))
        got = judge(ground, p)
        if got >= gate:
            return p, got
        h += inc
    return None


def verify(ground: Image.Image, plate, diamond, edge: int, rec: dict[str, Any], *, judged: float | None = None,
           width: float | None = None, room: float | None = None, fill=None,
           edge_colour: Any = "INK") -> Image.Image | None:
    """THE masthead gate (cycle 0 round 6, adversary r5-1: the end card had its own and passed a 1.38:1 mark): every
    masthead on every surface passes here before its name or its mark is drawn. In order: the group fits its room
    (`width` <= `room`, when given); the name, judged as `judge` judges it (`judged` when the planner already did),
    reaches contrast.masthead_gate; the mark, drawn by `draw_mark` over the name on `ground`, reaches contrast.mark on
    the pixels it actually changed (`mark_contrast`). Returns the ground with the name and the mark drawn at full
    strength, or None (fail closed) with `rec` saying why; `rec` gets shown, contrast, mark_contrast and mark_px."""
    gate, mark_gate = tok("contrast.masthead_gate"), tok("contrast.mark")
    rec["shown"] = False
    got = judge(ground, plate) if judged is None else judged
    rec["contrast"] = round(got, 2)
    if width is not None and room is not None and width > room + 1e-9:
        rec["reason"] = f"masthead not drawn: its group is {width} px wide, its room {room} px"
        log.warning("%s", rec["reason"])
        return None
    cap = HALO_EXCEPTION_CAP if rec.get("halo_exception") else tok("halo.masthead_max")
    if float(getattr(plate, "halo", 0.0) or 0.0) > cap + 1e-9:   # the caps are the gate's, not only the planner's
        rec["reason"] = (f"masthead not drawn: its halo {plate.halo} is over the {cap} cap"
                         + (" (the ruling-4 exception's ceiling)" if rec.get("halo_exception") else ""))
        log.warning("%s", rec["reason"])
        return None
    if got < gate:
        rec["reason"] = (f"masthead not drawn: its worst glyph reaches {got:.2f}:1, under the {gate}:1 gate")
        log.warning("%s", rec["reason"])
        return None
    out = plate.draw(ground.convert("RGB"))
    before = out.copy()
    draw_mark(ImageDraw.Draw(out), diamond, edge, fill=fill, edge_colour=edge_colour)
    rec["mark_contrast"], rec["mark_px"] = mark_contrast(before, out, diamond, edge, fill, edge_colour)
    if rec["mark_contrast"] < mark_gate:
        rec["reason"] = (f"masthead not drawn: its mark reaches {rec['mark_contrast']:.2f}:1 on its visible pixels "
                         f"{rec['mark_px']}, under the {mark_gate}:1 mark gate")
        log.warning("%s", rec["reason"])
        return None
    rec["shown"] = True
    return out


def draw(im: Image.Image, x0: float, baseline: float, size: float, reach: float | None = None,
         target: float | None = None, max_width: float | None = None
         ) -> tuple[Image.Image, Any, dict[str, Any], tuple[int, int, int, int] | None]:
    """The masthead drawn: (image, the name's plate, the record, the group's box including the diamond). When the
    record says `shown: False` (no ruled step reaches the gate, or the group does not fit its room) the image comes
    back untouched, the plate is None and the box is None."""
    mode = im.mode
    ground, p, rec = plan(im, x0, baseline, size, reach, target, max_width)
    if not rec["shown"]:
        return im, None, rec, None
    size = rec["size_px"]                                     # the size the fit ladder chose
    diamond, spec = mark_geometry(x0, baseline, size)
    out = verify(ground, p, diamond, spec["edge"], rec, judged=rec["contrast"])
    if out is None:                                           # fail closed: no masthead with an invisible mark
        return im, None, rec, None
    r = spec["mark_r"]
    cx, cy = diamond[0][0], diamond[1][1]
    bx = p.box
    box = (int(min(bx[0], cx - r)), int(min(bx[1], cy - r)), int(max(bx[2], cx + r)), int(max(bx[3], cy + r)))
    rec["box"] = list(box)
    return (out if mode == "RGB" else out.convert(mode)), p, rec, box
