"""The ground text stands on over a photograph: the shipped bands ("base") or the block family ("block").

Lander decision, design freeze cycle 6 (2026-11-27): the BLOCK family is the system's text-ground form for text over
a photograph; it ships behind RR_PLATE_FORM ("base" unless set, read by config.plate_form_setting) until the lander
flips the default after review. Every call site keeps its own code path for "base" (the integration-5 bands, hook
plate and myth panel, unchanged).

The block: one rounded INK plate per text block (a carousel's kicker, heading and body one block, its share / CTA line
and gold rule another; a Story frame's kicker, titles and body one block, its credit another; the hook's claim; the
9:16 myth's kickers, struck lines, fact lines and source), each its lines' ink bounds padded by block.pad_x /
block.pad_y em (never under block.pad_min), every block of a frame on the column's left edge, widths within
block.width_snap shared, one gap token between blocks, inside the deliverable's safe box with equal left and right
pads, at the opaque tier, and inside the block never lighter than the shipped ground on the same pixel
(darker_inside). Where it is laid is `choose`: the block where the shipped ground leaves a line under the target
after the re-encode margin, or where the block darkens less of the frame than the shipped ground; else the shipped
ground (ties keep it: the block would be invisible there). Every number is a token (TOKENS, or the system's own).
Nothing is niche-specific: the form reads pixels, boxes and tokens only.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

FORMS = ("base", "block")
UNKNOWN: dict[str, int] = {}   # RR_PLATE_FORM values that name no form, each taken as base (recorded, logged once)
BT709 = np.array([0.2126, 0.7152, 0.0722], np.float32)   # luma / relative luminance weights (ITU-R BT.709)
FULL = 255.0                                              # 8-bit full scale
SRGB = (0.04045, 12.92, 0.055, 1.055, 2.4)                # the sRGB transfer function (IEC 61966-2-1)
WCAG_FLARE = 0.05                                         # WCAG 2.x contrast: the flare term added to each luminance
from .safe_zones import REFERENCE_WIDTH                   # noqa: E402  (1080: the canvas the px tokens are written at)

# The block's tokens (on adoption they move into brand_system.SYSTEM with their ranges). Lengths in em are of the
# block's median line ink height; px at 1080 wide, scaled by the canvas width; a string names a system token.
TOKENS: dict[str, Any] = {
    "target": "contrast.high",   # the text target a block is laid for (7.0; ruling N1: 7:1 is the target)
    "block.pad_x": 0.30,         # em: margin left and right of the ink
    "block.pad_y": 0.22,         # em: above the first and below the last line's ink
    "block.pad_min": 18,         # px at 1080: never inside the ring a line's contrast is measured on (2-10 px)
    "block.gap": 24,             # px at 1080: the one vertical gap between two blocks of a frame (closer: one block)
    "block.gap_reach": 1.0,      # x the block's pad_y: how far a facing edge may grow to make the gap the token
    "block.width_snap": 0.08,    # of the frame width: blocks whose widths differ by less share the wider width
    "block.radius": "plate.radius",      # the system's plate radius (14 px at 1080)
    "block.tier": 1.0,           # the opaque plate tier (a held 0.875 block read kickers 0.3-0.4 under base)
    "block.encode_margin": 1.2,  # a line's need is judged at target x this (a platform re-encode takes 10-18 % off)
    "block.supersample": 4,      # the rounded rectangles are drawn at this multiple and reduced (antialiased)
    "block.dark_step": 20,       # luma (8-bit): a pixel darker than the photograph by more counts as darkened
    "block.share_tie": 1e-4,     # of the frame: darkened shares nearer than this are equal (the shipped ground stays)
    "block.ring": 10,            # px: the ring round a line its ground is read on (typography.ring_contrast's 2-10)
    "block.ring_pct": 90,        # the ring's brighter tail a line is measured against (typography.RING_PCT)
}

ENV = "RR_PLATE_FORM"


def active() -> str:
    """The form in force: "base" (the shipped design) unless RR_PLATE_FORM names a form. A value that names no form is
    taken as base, recorded in UNKNOWN and logged once (lander ruling, review of cycle 6: no setting value may change
    what ships by accident, and none may switch a gate off)."""
    from .config import plate_form_setting
    v = plate_form_setting()
    if v not in FORMS:
        if v not in UNKNOWN:
            log.warning("RR_PLATE_FORM=%r names no plate form: base is drawn", v)
        UNKNOWN[v] = UNKNOWN.get(v, 0) + 1
        return "base"
    return v


def tk(name: str) -> Any:
    """A block token's value (a string names a system token, read now)."""
    v = TOKENS[name]
    if isinstance(v, str):
        from .brand_system import tok
        return tok(v)
    return v


# ------------------------------------------------------------------------------------------------ measures
def _lin(v):
    v = np.asarray(v, np.float32) / FULL
    knee, slope, off, scale, gamma = SRGB
    return np.where(v <= knee, v / slope, ((v + off) / scale) ** gamma)


def rel_lum(rgb) -> np.ndarray:
    """WCAG relative luminance of sRGB values (... x 3)."""
    return _lin(rgb) @ BT709


def ratio(a: float, b: float) -> float:
    """The WCAG ratio of two relative luminances."""
    hi, lo = max(a, b), min(a, b)
    return (hi + WCAG_FLARE) / (lo + WCAG_FLARE)


def max_ground(fill, target: float) -> float:
    """The brightest ground luminance (relative) a `fill` line reaches `target` on."""
    return max(0.0, (float(rel_lum(np.asarray(fill[:3], np.float32))) + WCAG_FLARE) / target - WCAG_FLARE)


def luma(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"), np.float32) @ BT709


def dark_share(before: Image.Image, after: Image.Image) -> float:
    """The share of the frame `after` darkens by more than block.dark_step luma against `before`."""
    return float(((luma(before) - luma(after)) > tk("block.dark_step")).mean())


def ring_mask(shape, boxes) -> np.ndarray:
    """The type boxes each grown by block.ring px (the ground a line is read on)."""
    H, W = shape
    r = int(tk("block.ring"))
    m = np.zeros((H, W), bool)
    for b in boxes:
        x0, y0, x1, y1 = (int(round(float(v))) for v in b[:4])
        m[max(0, y0 - r):min(H, y1 + r), max(0, x0 - r):min(W, x1 + r)] = True
    return m


def ground_under(im: Image.Image, boxes) -> float:
    """The block.ring_pct percentile luma of the ground round and under the type boxes."""
    m = ring_mask((im.height, im.width), boxes)
    return float(np.percentile(luma(im)[m], tk("block.ring_pct"))) if m.any() else 0.0


def under_target(im: Image.Image, items) -> list[str]:
    """The lines (by index) `im` leaves under the target x block.encode_margin: each line's ground at block.ring_pct
    of its ring, against its own fill."""
    out = []
    rgb = np.asarray(im.convert("RGB"), np.float32)
    for k, (box, fill, _tag) in enumerate(items):
        m = ring_mask(rgb.shape[:2], [box])
        if not m.any():
            continue
        g = float(np.percentile(rel_lum(rgb[m]), tk("block.ring_pct")))
        if g > max_ground(fill, tk("target") * tk("block.encode_margin")):
            out.append(str(k))
    return out


# ------------------------------------------------------------------------------------------------ the block
def _fill_default():
    from . import brand
    return min((brand.GOLD, brand.BONE), key=lambda c: float(rel_lum(np.asarray(c, np.float32))))


def items_of(boxes) -> list[tuple[tuple[float, float, float, float], tuple, str]]:
    """[(ink box, fill, block tag)]: a bare box takes the darker of GOLD / BONE (the conservative need) and the "main"
    tag; boxes under one ink px wide or tall are dropped."""
    out = []
    for b in boxes:
        if hasattr(b[0], "__len__"):     # an item: (box, fill) or (box, fill, tag)
            box, fill, tag = b[0], b[1], (b[2] if len(b) > 2 else "main")
        else:
            box, fill, tag = b, None, "main"
        x0, y0, x1, y1 = (float(v) for v in box)
        if x1 > x0 + 1 and y1 > y0 + 1:
            out.append(((x0, y0, x1, y1), tuple((fill or _fill_default())[:3]), tag))
    return out


def block_rects(items, size, safe=None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The frame's blocks: one per tag (first appearance order), each its lines' ink bounds padded by block.pad_x /
    block.pad_y em of the block's median line ink height (never under block.pad_min px); every block's left edge on
    the text column's (the leftmost); blocks whose widths differ by less than block.width_snap of the frame width
    take the wider width; two blocks nearer than block.gap are one block, farther ones have their facing edges grown
    (at most block.gap_reach x pad_y each) toward a gap of exactly block.gap; inside `safe` (the deliverable's safe
    box, else the frame), a pad the box cuts sets the opposite pad to match (left and right equal, never under
    pad_min: a block whose cut pad would fall under it is recorded `clamped`). Returns ([{box, tag, lines, pad,
    clamped}], record)."""
    W, H = size
    k = W / REFERENCE_WIDTH
    sx0, sy0, sx1, sy1 = safe if safe is not None else (0, 0, W, H)
    floor = tk("block.pad_min") * k
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
        px_, py_ = max(floor, tk("block.pad_x") * lh), max(floor, tk("block.pad_y") * lh)
        ink = [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]
        blocks.append({"tag": t, "lines": len(bs), "pad": [px_, py_], "ink": ink,
                       "box": [ink[0] - px_, ink[1] - py_, ink[2] + px_, ink[3] + py_]})
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
                a["ink"] = [min(a["ink"][0], b["ink"][0]), a["ink"][1], max(a["ink"][2], b["ink"][2]),
                            max(a["ink"][3], b["ink"][3])]
                a["lines"] += b["lines"]
                rec["merged"].append([a["tag"], b["tag"]])
                if a["tag"] != b["tag"]:
                    a["tag"] = a["tag"] + "+" + b["tag"]
                blocks.remove(b)
                changed = True
                break
    # the side pads: every block on the column's left edge with ONE side pad p (left and right equal, lander ruling 6,
    # review of cycle 6), p the blocks' own pad_x unless the safe box cuts a side, then what the box leaves; widths
    # within the snap share the wider (never past the box)
    lx = min(b["ink"][0] for b in blocks)
    p = max(b["pad"][0] for b in blocks)
    p = min(p, lx - sx0, min(sx1 - b["ink"][2] for b in blocks))
    snap = tk("block.width_snap") * W
    for b in blocks:
        b["box"][0], b["box"][2] = lx - p, b["ink"][2] + p
    for a in blocks:
        for b in blocks:
            wa, wb = a["box"][2] - a["box"][0], b["box"][2] - b["box"][0]
            if a is not b and abs(wa - wb) < snap:
                a["box"][2] = b["box"][2] = min(sx1, max(a["box"][2], b["box"][2]))
    for a, b in zip(blocks, blocks[1:]):   # the facing edges grow toward the gap token
        g = b["box"][1] - a["box"][3]
        if g > gap:
            grow = min((g - gap) / 2, tk("block.gap_reach") * a["pad"][1], tk("block.gap_reach") * b["pad"][1])
            a["box"][3] += grow
            b["box"][1] -= grow
            if b["box"][1] - a["box"][3] > gap:
                rec["gap_short"].append({"tags": [a["tag"], b["tag"]], "gap": round(b["box"][1] - a["box"][3])})
    for b in blocks:
        b["clamped"] = p < floor
        b["box"] = [round(b["box"][0]), round(max(b["box"][1], sy0)), round(b["box"][2]), round(min(b["box"][3], sy1))]
    rec["side_pad"] = round(p, 1)
    rec["blocks"] = [{"tag": b["tag"], "box": b["box"], "lines": b["lines"], "clamped": b["clamped"],
                      "pad_x": [round(lx - b["box"][0], 1), round(b["box"][2] - b["ink"][2], 1)]} for b in blocks]
    return blocks, rec


def block_mask(size, blocks) -> np.ndarray:
    """The blocks' rounded rectangles (block.radius), drawn at block.supersample x and reduced (antialiased), 0..1."""
    W, H = size
    s = int(tk("block.supersample"))
    m = Image.new("L", (W * s, H * s), 0)
    d = ImageDraw.Draw(m)
    r = float(tk("block.radius")) * W / REFERENCE_WIDTH * s
    for b in blocks:
        x0, y0, x1, y1 = b["box"]
        d.rounded_rectangle([x0 * s, y0 * s, x1 * s - 1, y1 * s - 1], radius=r, fill=int(FULL))
    return np.asarray(m.resize((W, H), Image.Resampling.BOX), np.float32) / FULL


def ink_layer(size, alpha: np.ndarray, colour=None) -> Image.Image:
    from . import brand
    layer = Image.new("RGBA", size, tuple((colour or brand.INK)[:3]) + (int(FULL),))
    layer.putalpha(Image.fromarray(np.clip(alpha * FULL, 0, FULL).astype(np.uint8), "L"))
    return layer


def darker_inside(laid: Image.Image, other: Image.Image, lyr: Image.Image) -> Image.Image:
    """`laid` (a block laid over a photograph) with, inside the block's own area (the layer's alpha), the darker of it
    and `other` (the shipped ground on the same photograph), pixel by pixel by luma: the block is never lighter than
    the ground it replaces. RGB."""
    a = np.asarray(laid.convert("RGB"), np.float32)
    b = np.asarray(other.convert("RGB"), np.float32)
    inside = (np.asarray(lyr.getchannel("A"), np.float32) > 0)[..., None]
    take = inside & ((b @ BT709) < (a @ BT709))[..., None]
    return Image.fromarray(np.where(take, b, a).astype(np.uint8), "RGB")


def layer(im: Image.Image, boxes: Iterable, form: str | None = None, safe=None) -> tuple[Image.Image, dict[str, Any]]:
    """(the RGBA layer laid over `im` before the type, the record) for the text `boxes` ((x0, y0, x1, y1) ink boxes,
    or (box, fill[, tag]) items) under `form` (active() by default): nothing under base, the block family's blocks
    at block.tier under block."""
    form = form or active()
    items = items_of(boxes)
    rec: dict[str, Any] = {"form": form, "target": tk("target"), "lines": len(items)}
    if form != "block" or not items:
        return Image.new("RGBA", im.size, (0, 0, 0, 0)), rec
    blocks, brec = block_rects(items, im.size, safe)
    rec.update(brec)
    rec["tier"] = tk("block.tier")
    return ink_layer(im.size, block_mask(im.size, blocks) * tk("block.tier")), rec


def with_block(im: Image.Image, lyr: Image.Image, shipped: Image.Image) -> Image.Image:
    """The block laid on `im`, never lighter inside it than `shipped` (darker_inside); in `im`'s mode."""
    out = darker_inside(Image.alpha_composite(im.convert("RGBA"), lyr), shipped, lyr)
    return out.convert(im.mode) if im.mode != "RGB" else out


def choose(photo: Image.Image, shipped: Image.Image, blocked: Image.Image, items, rec: dict[str, Any]) -> str:
    """Where the block is laid (lander ruling 2, review of cycle 6): "block" where the shipped ground leaves any line
    under the target x block.encode_margin (under_target) or where the block darkens less of the frame than the
    shipped ground (by more than block.share_tie); else "band" (the shipped ground; a tie keeps it). Recorded."""
    rec["dark_share"] = round(dark_share(photo, blocked), 5)
    rec["band_dark_share"] = round(dark_share(photo, shipped), 5)
    rec["band_under_target"] = under_target(shipped, items)
    less = rec["dark_share"] < rec["band_dark_share"] - tk("block.share_tie")
    rec["laid"] = "block" if rec["band_under_target"] or less else "band"
    return rec["laid"]
