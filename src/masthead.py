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
from .brand_system import colour_of, masthead_spec, tok
from .typography import (
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
GLYPH_SURROUND = 4  # px: a glyph's immediate surround, 1..this outside its ink
GLYPH_PCT = 95      # the percentile of a glyph's background held against its fill: at most 5 % of it may fail
GLYPH_MIN_PX = 8    # a connected component smaller than this (a stray antialiasing speck) is not a glyph


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


def glyph_contrast(ground: Image.Image, plate) -> tuple[float | None, dict[str, Any]]:
    """The worst GLYPH of `plate` (a TextPlate) on `ground` (the pixels the text is drawn on, before it is drawn; the
    plate's own halo is composited first, as it will be): (ratio, detail). Each glyph is one SHAPED glyph: the drawn
    ink partitioned by every glyph's own mask, rasterised alone at the position and advance HarfBuzz gave it
    (font_engine.glyph_masks: tracking, kerning and ligatures included), never an ink component (round 4: at a tracking of -0.14 em thirty letters touched and were
    judged as one). Its background is the ground UNDER its own ink pixels plus its immediate surround (a ring
    1..GLYPH_SURROUND px outside it, every glyph's ink excluded). A glyph scores the lower of the fill's ratio against
    the GLYPH_PCT percentile (the brightest share, for light text; the darkest, for dark text) of (a) its under-ink
    pixels alone and (b) under-ink plus surround: so no more than (100 - GLYPH_PCT) % of a glyph's ink may sit on ground
    that fails the ratio, whatever the feature's width, angle or offset, and whatever its neighbours touch. None when
    nothing is measurable."""
    import cv2
    import numpy as np

    from .font_engine import glyph_masks
    if plate is None or plate.empty:
        return None, {}
    under_im = plate.draw_halo(ground.convert("RGB")) if plate.halo > 0 else ground.convert("RGB")
    ox, oy = (int(v) for v in plate.origin)
    h, w = plate.glyphs.shape
    rgb = np.asarray(under_im)[max(0, oy):oy + h, max(0, ox):ox + w]
    ink_all = (plate.glyphs[max(0, -oy):max(0, -oy) + rgb.shape[0], max(0, -ox):max(0, -ox) + rgb.shape[1]] > 127)
    lum = _lin(rgb.reshape(-1, 3)).reshape(rgb.shape[:2])
    lt = rel_luminance(plate.fill)
    k = np.ones((2 * GLYPH_SURROUND + 1, 2 * GLYPH_SURROUND + 1), np.uint8)
    pct = GLYPH_PCT if plate.light else 100 - GLYPH_PCT
    worst, detail = None, {"glyphs": 0}
    H, W = lum.shape
    # Each drawn ink pixel belongs to the glyph(s) whose OWN shaped mask covers it (>= half coverage), else to the glyph
    # covering it most: the drawn ink partitioned by the shaping, each glyph's part then cut into its connected pieces.
    # Letters that do not touch get exactly the ink components they had (so untouched text judges as in round 3,
    # pixel for pixel); letters that touch are still told apart.
    masks = glyph_masks(ground.size, plate.xy, plate.text, plate.font, plate.anchor, plate.track, plate.features)
    if not masks:
        return None, detail
    stack = np.zeros((len(masks), H, W), np.uint8)
    for i, (_, (gx0, gy0, gx1, gy1), gmask) in enumerate(masks):
        m = np.asarray(gmask)
        sx0, sy0 = gx0 - ox, gy0 - oy
        X0, Y0, X1, Y1 = max(0, sx0), max(0, sy0), min(W, sx0 + m.shape[1]), min(H, sy0 + m.shape[0])
        if X1 > X0 and Y1 > Y0:
            stack[i, Y0:Y1, X0:X1] = m[Y0 - sy0:Y1 - sy0, X0 - sx0:X1 - sx0]
    owner = stack.argmax(axis=0)
    pieces = []   # each glyph's partition, cut into its connected pieces (a detached serif or speck is judged alone)
    for i, (cluster, _box, _m) in enumerate(masks):
        part = (ink_all & ((owner == i) | (stack[i] > 127))).astype(np.uint8)
        n, lab = cv2.connectedComponents(part, connectivity=8)
        pieces += [(cluster, lab == j) for j in range(1, n)]
    for cluster, comp in pieces:
        if int(comp.sum()) < GLYPH_MIN_PX:
            continue
        ys, xs = np.nonzero(comp)
        y0, y1 = max(0, ys.min() - GLYPH_SURROUND - 1), ys.max() + GLYPH_SURROUND + 2
        x0, x1 = max(0, xs.min() - GLYPH_SURROUND - 1), xs.max() + GLYPH_SURROUND + 2
        c = comp[y0:y1, x0:x1].astype(np.uint8)
        ring = (cv2.dilate(c, k) > 0) & ~ink_all[y0:y1, x0:x1]
        under = lum[y0:y1, x0:x1][c > 0]
        both = np.concatenate([under, lum[y0:y1, x0:x1][ring]])
        pair = (float(np.percentile(under, pct)), float(np.percentile(both, pct)))
        lb = max(pair) if plate.light else min(pair)
        r = (max(lt, lb) + 0.05) / (min(lt, lb) + 0.05)
        detail["glyphs"] += 1
        if worst is None or r < worst:
            worst = r
            detail.update(worst_glyph_x=int(ox + xs.min()), worst_glyph_px=int(comp.sum()), worst_glyph_cluster=int(cluster))
    return (None if worst is None else round(worst, 2)), detail


def window_contrast(im: Image.Image, plate) -> float | None:
    """The worst ring contrast of `plate` on the composed pixels `im`, window by window along the name (round 2): the
    element's 2-10 px ring cut into windows half a cap wide, one every quarter cap, each window's 90th / 10th
    percentile against the glyph colour. Kept beside the glyph judgement: it reads the wider ring the glyph surround
    does not."""
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
        lb = float(np.percentile(_lin(bg), 90 if plate.light else 10))
        r = round((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05), 2)
        if worst is None or r < worst:
            worst = r
    return worst


def judge(ground: Image.Image, plate) -> float:
    """The element's ratio as judged: the lowest of its whole-ring ratio and its windowed rings on the composed pixels
    and its worst glyph (`glyph_contrast`) on the ground under it."""
    if plate is None or plate.empty:
        return 0.0
    composed = plate.draw(ground)
    vals = [v for v in (plate.measure(composed), window_contrast(composed, plate), glyph_contrast(ground, plate)[0])
            if v is not None]
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
    font = font_for(size)
    spec = masthead_spec(size)
    xy = (x0 + 2 * spec["mark_r"] + spec["gap"], baseline)
    reach = baseline + 6 * cap_height(font) if reach is None else reach
    bone, ink = colour_of("masthead.colour_dark"), colour_of("masthead.colour_light")
    base = im.convert("RGB")
    room = (im.width - 2 * x0) if max_width is None else max_width
    width = group_width(size)
    rec: dict[str, Any] = {"variant": "dark", "scrim_peak": 0.0, "halo": 0.0, "size": round(float(size), 1),
                           "group_width": round(width, 1), "room": round(float(room), 1)}
    if width > room:   # round 3, finding 7: never clipped silently: refused, with the reason
        rec.update(shown=False, contrast=None, best_tried=None, short_of_target=True, below_gate=True,
                   reason=f"masthead not drawn: its group is {width:.1f} px wide, its room {room:.1f} px")
        log.warning("%s", rec["reason"])
        p = plan_text(base, xy, brand.MASTHEAD, font, bone, "ls", spec["track"], target=target, max_halo=0.0)
        return base, p, rec
    seen = {"best": 0.0, "step": None, "dark": 0.0, "light": 0.0}   # the best overall and per variant

    def name(ground, fill, halo_cap=0.0, step=""):
        """(plate, judged ratio): the least halo (in halo.step steps, up to `halo_cap`) at which the judged ratio
        reaches the target, else the halo that reads best. Every try updates the best seen (the record's best_tried)."""
        best = None
        halo = 0.0
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
            if got >= target or halo >= halo_cap - 1e-9 or not p.light:
                break
            halo = min(halo_cap, halo + halo_step)
        return best

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
    if got < gate:                                            # bone cannot reach the gate: the light-ground variant
        q, qgot = name(base, ink, step="light variant")
        if qgot > got:
            p, ground, got = q, base, qgot
            rec.update(variant="light", scrim_peak=0.0)
            rec.pop("levels_ceiling", None)
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
                         f"{tok('scrim.levels_floor')}, light variant), under the {gate}:1 gate (bone best "
                         f"{seen['dark']:.2f}, INK best {seen['light']:.2f})")
        log.warning("%s", rec["reason"])
        return base, p, rec
    p.contrast = round(got, 2)       # what the element's record reports: its ratio as judged (the worst glyph)
    return ground, p, rec


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
