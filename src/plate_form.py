"""Candidate ground forms for text over a photograph (design freeze cycle 6, PLATEFORM design stream).

A DESIGN switch, not a shipped default: `active()` is "base" unless RR_PLATE_FORM names a candidate, and every call
site keeps its own code path for "base" (the integration-5 bands, hook plate and myth panel, unchanged). The lander
chooses a form from the measured, rendered options in queue/DESIGN-plateform-c6.md; on adoption its tokens move into
brand_system.SYSTEM (they live here, in one table, so the default brand's token probes are not touched by a form
that draws nothing by default).

Every form is ONE function of the photograph under the text and the text's own ink boxes (and their fills): it
returns an RGBA layer laid over the photograph before the type (the type and its own halos are drawn by the caller,
as always) and a record. Nothing is niche-specific: the forms read only pixels, boxes and tokens.

Forms (the queue document gives the analysis, the limits and the measures):
- "hug":   a text-hugging plate per line group: each line's ink box padded by hug.pad_x / hug.pad_y em, the lines of a
           group joined into one stepped plate that follows the rag, corners rounded by plate.radius; INK at the held
           tier (plate.held), the opaque tier only where held misses the target. The caption pill and the overlay
           label's held plate (typography.plan_label) are this form already.
- "block": THE ADOPTED FAMILY (lander decision, cycle 6): one rounded INK plate per text block (kicker + heading +
           body one block; a CTA / share line and a credit their own), sized to its lines' ink plus block.* pads, on
           the text column's left edge, widths snapped, one gap token between blocks, inside the safe box; the held
           or opaque tier chosen for the target with a re-encode margin (block_rects, block_mask). Call sites lay it
           only where it darkens no more of the frame than the shipped ground would (else that ground, recorded).
- "field": a local contrast field: INK alpha computed per pixel from the photograph's own luminance under and round
           the type, only as deep as that pixel needs for the target, inside an envelope that falls off as a Gaussian
           (field.grow / field.sigma em); no straight edge exists to read.
- "grade": a graded photograph region: inside a soft block envelope the photograph is gradient-mapped from INK to
           its own mean hue at the brightest luminance the target allows (a duotone of the photograph itself), so
           its texture stays and its luminance range is compressed instead of veiled.
- "split": the type moves onto an INK field anchored to the nearer frame edge, its boundary a designed one (a crisp
           edge marked by a GOLD rule the text column's length, the cover seam rule's motif), the photograph whole
           on the other side.
- "type":  typographic first (the overlay label rule, typography.plan_label, applied to every line): no ground where
           each line reaches the target with its own halo at the label cap; where any line of the frame cannot, the
           frame's lines take the hug plate (one state per frame, PLATES-04's rule).
- "none":  no ground at all (a measuring reference: what the photograph gives the type by itself).
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw

FORMS = ("base", "hug", "block", "field", "grade", "split", "type", "none")

# The candidate token set (proposed; on adoption these move into brand_system.SYSTEM with ranges). Lengths in em are
# of the line's own ink height (a cap-height-like measure every face has); px are at 1080 wide, scaled by the canvas.
TOKENS: dict[str, Any] = {
    "target": 7.0,               # the text target the forms are laid for (contrast.high; ruling N1: 7:1 is the target)
    "hug.pad_x": 0.30,           # em of the line's ink height: the plate's margin left and right of the ink
    "hug.pad_y": 0.22,           # em: above and below the ink
    "hug.pad_min": 18,           # px at 1080: never inside the ring a line's contrast is measured on (10-14 px)
    "hug.group_gap": 0.95,       # em: lines nearer than this (vertically) are one group, one plate
    "hug.radius": "plate.radius",    # the system's plate radius (14 px at 1080)
    "hug.tiers": ("plate.held", 1.0),  # the system's held tier, then opaque
    "hug.encode_margin": 1.2,    # a plate's tier is chosen for target x this (a platform re-encode takes ~10-18 %
    #                              off a small gold label: contrast.kicker 5.5 -> 4.5); measured, cycle 6 first round
    # ---- the adopted family (lander decision, cycle 6): ONE plate per text block (kicker + heading + body; a CTA /
    # share line or a credit its own block), every block of a frame on the text column's left edge
    "block.pad_x": 0.30,         # em of the block's median line ink height: margin left and right of the ink
    "block.pad_y": 0.22,         # em: above the first and below the last line's ink
    "block.pad_min": 18,         # px at 1080: never inside the ring a line's contrast is measured on (10-14 px)
    "block.gap": 24,             # px at 1080: the one vertical gap between two blocks of a frame (closer: one block)
    "block.gap_reach": 1.0,      # x the block's pad_y: how far a facing edge may grow to make the gap the token
    "block.width_snap": 0.08,    # of the frame width: blocks whose widths differ by less share the wider width
    "block.radius": "plate.radius",      # the system's plate radius (14 px at 1080)
    "block.tiers": ("plate.held", 1.0),  # the held tier, then opaque
    "block.encode_margin": 1.2,  # the tier is chosen for target x this (a platform re-encode takes 10-18 % off)
    "field.grow": 0.35,          # em: the envelope's full-strength core past the ink
    "field.sigma": 0.45,         # em: its Gaussian fall-off
    "field.cap": 0.92,           # the deepest INK alpha the field lays
    "field.smooth": 0.10,        # em: the need map's own blur (no dot pattern)
    "grade.grow": 0.45,          # em: the block envelope's core past the ink
    "grade.sigma": 0.55,         # em: its fall-off
    "split.air": 0.60,           # em of the first line's ink height between the boundary and the nearest ink
    "split.rule": 4,             # px at 1080: the boundary rule's thickness (the cover seam rule's)
    "type.target": 5.5,          # contrast.kicker: the tier a bare line needs (re-encodes to >= 4.5)
    "type.halo": 0.16,           # halo.label_max: the label halo's cap
}

ENV = "RR_PLATE_FORM"


def active() -> str:
    """The form in force: "base" (the shipped design) unless RR_PLATE_FORM names a candidate."""
    v = (os.environ.get(ENV) or "base").strip().lower()
    if v not in FORMS:
        raise ValueError(f"{ENV}={v!r} is not a plate form ({', '.join(FORMS)})")
    return v


def tk(name: str) -> Any:
    v = TOKENS[name]
    if isinstance(v, str):
        from .brand_system import tok
        return tok(v)
    if isinstance(v, tuple):
        from .brand_system import tok
        return tuple(tok(x) if isinstance(x, str) else x for x in v)
    return v


# ------------------------------------------------------------------------------------------------ colour measures
def _lin(v):
    v = np.asarray(v, np.float32) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def rel_lum(rgb) -> np.ndarray:
    """WCAG relative luminance of sRGB values (... x 3)."""
    return _lin(rgb) @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def ratio(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _max_ground(fill, target: float) -> float:
    """The brightest ground luminance (relative) a `fill` line reaches `target` on."""
    return max(0.0, (float(rel_lum(np.asarray(fill[:3], np.float32))) + 0.05) / target - 0.05)


def _fills(boxes) -> list[tuple[tuple[float, float, float, float], tuple]]:
    """[(box, fill)]: a bare box takes the darkest of the brand's text fills (GOLD / BONE: the conservative need)."""
    from . import brand
    default = min((brand.GOLD, brand.BONE), key=lambda c: float(rel_lum(np.asarray(c, np.float32))))
    out = []
    for b in boxes:
        if len(b) in (2, 3) and hasattr(b[0], "__len__"):
            box, fill = b[0], b[1]
        else:
            box, fill = b, default
        x0, y0, x1, y1 = (float(v) for v in box)
        if x1 - x0 >= 2 and y1 - y0 >= 2:
            out.append(((x0, y0, x1, y1), tuple(fill[:3]) if fill is not None else default))
    return out


def groups(boxes: Sequence[tuple[tuple[float, float, float, float], tuple]]) -> list[list]:
    """Lines into groups: sorted by top, a line joins the group above when its vertical gap is under
    hug.group_gap x the taller line's ink height and the two overlap horizontally."""
    out: list[list] = []
    for bf in sorted(boxes, key=lambda b: (b[0][1], b[0][0])):
        b = bf[0]
        if out:
            p = out[-1][-1][0]
            h = max(p[3] - p[1], b[3] - b[1])
            if b[1] - p[3] <= tk("hug.group_gap") * h and min(p[2], b[2]) > max(p[0], b[0]):
                out[-1].append(bf)
                continue
        out.append([bf])
    return out


# ------------------------------------------------------------------------------------------------ the forms
def _ink_rgba(size, alpha: np.ndarray, colour=None) -> Image.Image:
    from . import brand
    layer = Image.new("RGBA", size, tuple((colour or brand.INK)[:3]) + (255,))
    layer.putalpha(Image.fromarray(np.clip(alpha * 255 + 0.5, 0, 255).astype(np.uint8), "L"))
    return layer


def _hug_mask(size, grp: list, scale: float, block: bool = False) -> np.ndarray:
    """The stepped plate of one group: each line's padded ink box, the gaps between lines bridged over their common
    width, convex corners rounded by the radius, drawn at 4x and reduced (antialiased)."""
    s = 4
    W, H = size
    r = max(0.0, float(tk("hug.radius")) * scale)
    m = Image.new("L", (W * s, H * s), 0)
    d = ImageDraw.Draw(m)
    rects = []
    for (x0, y0, x1, y1), _f in grp:
        h = y1 - y0
        floor = tk("hug.pad_min") * scale
        px_, py_ = max(floor, tk("hug.pad_x") * h), max(floor, tk("hug.pad_y") * h)
        rects.append((x0 - px_, y0 - py_, x1 + px_, y1 + py_))
    if block:
        xs0, ys0, xs1, ys1 = zip(*rects)
        rects = [(min(xs0), min(ys0), max(xs1), max(ys1))]
    for a, b in zip(rects, rects[1:]):
        xa, xb = max(a[0], b[0]), min(a[2], b[2])
        if xb > xa and b[1] > a[3]:
            d.rectangle([xa * s, a[3] * s - s, xb * s, b[1] * s + s], fill=255)
    for k, (x0, y0, x1, y1) in enumerate(rects):
        d.rounded_rectangle([x0 * s, y0 * s, x1 * s, y1 * s], radius=r * s, fill=255)
    return np.asarray(m.resize((W, H), Image.Resampling.BOX), np.float32) / 255.0


def _ground_p95(G: np.ndarray, mask: np.ndarray) -> float:
    sel = mask > 0.5
    if not sel.any():
        return 0.0
    return float(np.percentile(rel_lum(G[sel]), 95))


def _composite_lum(g_rgb: np.ndarray, a: float, ink) -> float:
    c = (1 - a) * g_rgb + a * np.asarray(ink[:3], np.float32)
    return float(rel_lum(c))


def _hug(G: np.ndarray, fb, size, scale, rec, block: bool = False) -> np.ndarray:
    from . import brand
    tiers = tk("hug.tiers")
    alpha = np.zeros(G.shape[:2], np.float32)
    need_tier = tiers[0]
    plates = []
    for grp in groups(fb):
        m = _hug_mask(size, grp, scale, block)
        sel = m > 0.5
        if not sel.any():
            continue
        # the group's brightest ground (p95 by luminance) and its darkest fill
        lum = rel_lum(G[sel])
        g = G[sel][int(np.argsort(lum)[int(0.95 * (len(lum) - 1))])]
        worst = min(_max_ground(f, tk("target") * tk("hug.encode_margin")) for _b, f in grp)
        tier = next((t for t in tiers if rel_lum(np.asarray(((1 - t) * g + t * np.asarray(brand.INK, np.float32)),
                                                            np.float32)) <= worst + 1e-6), tiers[-1])
        need_tier = max(need_tier, tier)
        plates.append((m, tier))
        x = np.where(sel.any(axis=0))[0]
        y = np.where(sel.any(axis=1))[0]
        rec.setdefault("plates", []).append([int(x[0]), int(y[0]), int(x[-1]) + 1, int(y[-1]) + 1])
    for m, _t in plates:   # one tier per frame: the deepest any group needs (one form on the frame)
        alpha = np.maximum(alpha, m * need_tier)
    rec["tier"] = need_tier
    return alpha


def _envelope(size, fb, grow_em: float, sigma_em: float, block: bool = False) -> np.ndarray:
    """1 inside each ink box grown by grow_em (or each group's bounding block), falling off as a Gaussian of
    sigma_em (em of the line's ink height), solved at 1/4 size."""
    from scipy.ndimage import gaussian_filter
    W, H = size
    k = 4
    w, h = -(-W // k), -(-H // k)
    env = np.zeros((h, w), np.float32)
    items = []
    if block:
        for grp in groups(fb):
            xs0, ys0, xs1, ys1 = zip(*(b for b, _f in grp))
            lh = float(np.median([b[3] - b[1] for b, _f in grp]))
            items.append(((min(xs0), min(ys0), max(xs1), max(ys1)), lh))
    else:
        items = [(b, b[3] - b[1]) for b, _f in fb]
    floor = tk("hug.pad_min") * W / 1080.0
    for (x0, y0, x1, y1), lh in items:
        g, s = max(floor, grow_em * lh), max(1.0, sigma_em * lh)
        core = np.zeros_like(env)
        core[max(0, int((y0 - g) / k)):int((y1 + g) / k) + 1, max(0, int((x0 - g) / k)):int((x1 + g) / k) + 1] = 1
        env = np.maximum(env, np.clip(gaussian_filter(core, s / k, mode="constant") * 2.0, 0, 1) if s > 1 else core)
        env = np.maximum(env, core)
    big = Image.fromarray((env * 255).astype(np.uint8)).resize((W, H), Image.Resampling.BICUBIC)
    return np.asarray(big, np.float32) / 255.0


def _field(G: np.ndarray, fb, size, rec) -> np.ndarray:
    from scipy.ndimage import gaussian_filter, maximum_filter

    from . import brand
    lh = float(np.median([b[3] - b[1] for b, _f in fb]))
    worst = min(_max_ground(f, tk("target")) for _b, f in fb)
    Y = rel_lum(G)
    Yi = float(rel_lum(np.asarray(brand.INK, np.float32)))
    # per pixel: the INK alpha that brings this pixel's neighbourhood (its max over ~0.15 em) to the target ground
    r = max(1, int(0.15 * lh))
    Ym = maximum_filter(Y[::2, ::2], size=r)
    # composite luminance is not linear in alpha in gamma space; solve in linear light (a close, slightly deep bound)
    need = np.clip((Ym - worst) / np.maximum(Ym - Yi, 1e-4), 0, tk("field.cap"))
    need = gaussian_filter(need, max(1.0, tk("field.smooth") * lh / 2))
    need = np.asarray(Image.fromarray(need.astype(np.float32)).resize(size, Image.Resampling.BILINEAR), np.float32)
    env = _envelope(size, fb, tk("field.grow"), tk("field.sigma"))
    a = np.clip(need * env, 0, tk("field.cap"))
    rec["peak"] = round(float(a.max()), 3)
    return a


def _grade(G: np.ndarray, fb, size, rec) -> tuple[np.ndarray, np.ndarray]:
    from . import brand
    env = _envelope(size, fb, tk("grade.grow"), tk("grade.sigma"), block=True)
    worst = min(_max_ground(f, tk("target")) for _b, f in fb)
    sel = env > 0.5
    mean = G[sel].mean(axis=0) if sel.any() else np.array([128, 128, 128], np.float32)
    # the tone: the region's own mean colour, scaled (in linear light) to the brightest luminance the target allows
    lm = max(1e-4, float(rel_lum(mean)))
    tone_lin = _lin(mean) * min(1.0, worst / lm)
    tone = np.where(tone_lin <= 0.0031308, tone_lin * 12.92, 1.055 * np.power(np.maximum(tone_lin, 0), 1 / 2.4) - 0.055)
    tone = np.clip(tone * 255, 0, 255)
    ink = np.asarray(brand.INK, np.float32)
    t = (G @ np.array([0.2126, 0.7152, 0.0722], np.float32)) / 255.0   # the photograph's own luma as the map's input
    duo = ink[None, None, :] * (1 - t[..., None]) + tone[None, None, :] * t[..., None]
    rec["tone"] = [round(float(v)) for v in tone]
    return env, duo


def _split(G: np.ndarray, fb, size, scale, rec) -> np.ndarray:
    from . import brand
    W, H = size
    x0 = min(b[0] for b, _f in fb)
    x1 = max(b[2] for b, _f in fb)
    top = min(b[1] for b, _f in fb)
    foot = max(b[3] for b, _f in fb)
    first = sorted(fb, key=lambda b: b[0][1])[0][0]
    air = tk("split.air") * (first[3] - first[1])
    alpha = np.zeros((H, W), np.float32)
    rule = max(2, round(tk("split.rule") * scale))
    if (top + foot) / 2 >= H / 2:
        y = max(0, int(round(top - air)))
        alpha[y:, :] = 1.0
        rec["field"] = [0, y, W, H]
        rec["rule"] = [int(x0), y, int(x1), y + rule]
    else:
        y = min(H, int(round(foot + air)))
        alpha[:y, :] = 1.0
        rec["field"] = [0, 0, W, y]
        rec["rule"] = [int(x0), y - rule, int(x1), y]
    rec["anchor"] = "foot" if rec["field"][3] == H else "top"
    return alpha


def _items(boxes) -> list[tuple[tuple[float, float, float, float], tuple, str]]:
    """[(box, fill, block tag)]: a box without a tag belongs to the frame's "main" block."""
    out = []
    for b in boxes:
        tag = b[2] if len(b) == 3 and hasattr(b[0], "__len__") else "main"
        got = _fills([b])
        if got:
            out.append((got[0][0], got[0][1], tag))
    return out


def block_rects(items, size, safe=None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The frame's blocks: one per tag (first appearance order), each its lines' ink bounds padded by block.pad_x /
    block.pad_y em of the block's median line ink height (never under block.pad_min px); every block's left edge on
    the text column's (the leftmost); blocks whose widths differ by less than block.width_snap of the frame width
    take the wider width; two blocks nearer than block.gap are one block, farther ones have their facing edges grown
    (at most block.gap_reach x pad_y each) toward a gap of exactly block.gap; every edge kept inside `safe` (the
    deliverable's safe box, else the frame). Returns ([{box, tag, lines, pad, clamped}], record)."""
    W, H = size
    k = W / 1080.0
    sx0, sy0, sx1, sy1 = safe if safe is not None else (0, 0, W, H)
    order: list[str] = []
    by: dict[str, list] = {}
    for bx, _f, t in items:
        if t not in by:
            order.append(t)
            by[t] = []
        by[t].append(bx)
    blocks = []
    for t in order:
        bs = by[t]
        lh = float(np.median([b[3] - b[1] for b in bs]))
        px_ = max(tk("block.pad_min") * k, tk("block.pad_x") * lh)
        py_ = max(tk("block.pad_min") * k, tk("block.pad_y") * lh)
        blocks.append({"tag": t, "lines": len(bs), "pad": [round(px_, 1), round(py_, 1)],
                       "box": [min(b[0] for b in bs) - px_, min(b[1] for b in bs) - py_,
                               max(b[2] for b in bs) + px_, max(b[3] for b in bs) + py_]})
    rec: dict[str, Any] = {"merged": [], "gap_short": []}
    gap = tk("block.gap") * k
    changed = True
    while changed:           # nearer than the gap token: one block
        changed = False
        blocks.sort(key=lambda b: b["box"][1])
        for a, b in zip(blocks, blocks[1:]):
            if b["box"][1] - a["box"][3] < gap:
                a["box"] = [min(a["box"][0], b["box"][0]), a["box"][1], max(a["box"][2], b["box"][2]),
                            max(a["box"][3], b["box"][3])]
                a["lines"] += b["lines"]
                rec["merged"].append([a["tag"], b["tag"]])
                if a["tag"] != b["tag"]:
                    a["tag"] = a["tag"] + "+" + b["tag"]
                blocks.remove(b)
                changed = True
                break
    left = min(b["box"][0] for b in blocks)
    for b in blocks:
        b["box"][0] = left
    snap = tk("block.width_snap") * W
    for a in blocks:            # widths within the snap share the wider
        for b in blocks:
            wa, wb = a["box"][2] - a["box"][0], b["box"][2] - b["box"][0]
            if a is not b and abs(wa - wb) < snap:
                a["box"][2] = b["box"][2] = max(a["box"][2], b["box"][2])
    for a, b in zip(blocks, blocks[1:]):   # the facing edges grow toward the gap token
        g = b["box"][1] - a["box"][3]
        if g > gap:
            grow = min((g - gap) / 2, tk("block.gap_reach") * a["pad"][1], tk("block.gap_reach") * b["pad"][1])
            a["box"][3] += grow
            b["box"][1] -= grow
            if b["box"][1] - a["box"][3] > gap + 0.5:
                rec["gap_short"].append({"tags": [a["tag"], b["tag"]], "gap": round(b["box"][1] - a["box"][3])})
    for b in blocks:
        x0, y0, x1, y1 = b["box"]
        b["clamped"] = bool(x0 < sx0 or y0 < sy0 or x1 > sx1 or y1 > sy1)
        b["box"] = [round(max(x0, sx0)), round(max(y0, sy0)), round(min(x1, sx1)), round(min(y1, sy1))]
    rec["blocks"] = [{"tag": b["tag"], "box": b["box"], "lines": b["lines"], "clamped": b["clamped"]} for b in blocks]
    return blocks, rec


def block_mask(size, blocks) -> np.ndarray:
    """The blocks' rounded rectangles (block.radius), drawn at 4x and reduced (antialiased), 0..1."""
    W, H = size
    s = 4
    m = Image.new("L", (W * s, H * s), 0)
    d = ImageDraw.Draw(m)
    r = float(tk("block.radius")) * W / 1080.0 * s
    for b in blocks:
        x0, y0, x1, y1 = b["box"]
        d.rounded_rectangle([x0 * s, y0 * s, x1 * s - 1, y1 * s - 1], radius=r, fill=255)
    return np.asarray(m.resize((W, H), Image.Resampling.BOX), np.float32) / 255.0


def _block_alpha(G: np.ndarray, items, size, safe, rec) -> np.ndarray:
    from . import brand
    blocks, brec = block_rects(items, size, safe)
    rec.update(brec)
    mask = block_mask(size, blocks)
    sel = mask > 0.5
    tiers = tk("block.tiers")
    tier = tiers[-1]
    if sel.any():
        lum = rel_lum(G[sel])
        g = G[sel][int(np.argsort(lum)[int(0.95 * (len(lum) - 1))])]
        worst = min(_max_ground(f, tk("target") * tk("block.encode_margin")) for _b, f, _t in items)
        ink = np.asarray(brand.INK, np.float32)
        tier = next((t for t in tiers if float(rel_lum(((1 - t) * g + t * ink).astype(np.float32))) <= worst + 1e-6),
                    tiers[-1])
    rec["tier"] = tier
    return mask * tier


def dark_share(before: Image.Image, after: Image.Image, step: float = 20.0) -> float:
    """The share of the frame `after` darkens by more than `step` luma (8-bit BT.709) against `before`."""
    L = np.array([0.2126, 0.7152, 0.0722], np.float32)
    a = np.asarray(before.convert("RGB"), np.float32) @ L
    b = np.asarray(after.convert("RGB"), np.float32) @ L
    return float(((a - b) > step).mean())


def layer(im: Image.Image, boxes: Iterable, form: str | None = None, safe=None) -> tuple[Image.Image, dict[str, Any]]:
    """(the RGBA layer laid over `im` before the type, the record) for the text `boxes` ((x0, y0, x1, y1) ink boxes,
    or (box, fill) pairs) under `form` (active() by default)."""
    from . import brand
    form = form or active()
    size = im.size
    scale = size[0] / 1080.0
    rec: dict[str, Any] = {"form": form, "target": tk("target")}
    fb = _fills(boxes)
    clear = Image.new("RGBA", size, (0, 0, 0, 0))
    if form in ("base", "none") or not fb:
        rec["lines"] = len(fb)
        return clear, rec
    G = np.asarray(im.convert("RGB"), np.float32)
    rec["lines"] = len(fb)
    rec["groups"] = len(groups(fb))
    if form == "block":
        return _ink_rgba(size, _block_alpha(G, _items(boxes), size, safe, rec)), rec
    if form == "hug":
        return _ink_rgba(size, _hug(G, fb, size, scale, rec)), rec
    if form == "type":
        halo = tk("type.halo")
        short = []
        for grp in groups(fb):
            boxm = np.zeros(G.shape[:2], bool)
            for (x0, y0, x1, y1), _f in grp:
                boxm[max(0, int(y0)):int(y1), max(0, int(x0)):int(x1)] = True
            gp = _ground_p95(G, boxm.astype(np.float32))
            yi = float(rel_lum(np.asarray(brand.INK, np.float32)))
            g_halo = (1 - halo) * gp + halo * yi     # the label halo's cap over the line's brightest ground (approx.)
            got = min(ratio(float(rel_lum(np.asarray(f, np.float32))), g_halo) for _b, f in grp)
            if got < tk("type.target"):
                short.append(round(got, 2))
        rec["bare_short"] = short
        if not short:
            rec["state"] = "bare"
            return clear, rec
        rec["state"] = "plated"
        return _ink_rgba(size, _hug(G, fb, size, scale, rec)), rec
    if form == "field":
        return _ink_rgba(size, _field(G, fb, size, rec)), rec
    if form == "grade":
        env, duo = _grade(G, fb, size, rec)
        lay = np.dstack([np.clip(duo, 0, 255), env * 255]).astype(np.uint8)
        return Image.fromarray(lay, "RGBA"), rec
    if form == "split":
        a = _split(G, fb, size, scale, rec)
        lay = _ink_rgba(size, a)
        x0, y0, x1, y1 = rec["rule"]
        ImageDraw.Draw(lay).rectangle([x0, y0, x1 - 1, y1 - 1], fill=tuple(brand.GOLD[:3]) + (255,))
        return lay, rec
    raise ValueError(form)


def lay(im: Image.Image, boxes: Iterable, form: str | None = None, safe=None) -> tuple[Image.Image, dict[str, Any]]:
    """`im` with the form's layer composited (same mode back), and the record."""
    lyr, rec = layer(im, boxes, form, safe)
    out = Image.alpha_composite(im.convert("RGBA"), lyr)
    return (out if im.mode == "RGBA" else out.convert(im.mode)), rec
