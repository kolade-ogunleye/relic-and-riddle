"""Deterministic photo hygiene: caption strips printed along a photograph's edge.

Satellite and archive photographs often carry a provider caption bar ("Copernicus Sentinel data [2023],
processed by Sentinel Hub/Pierre Markuse", Richat stories, 2026-11-01). The vision screen in beat_images finds
these when it runs; when it is off, unavailable, or a photo is kept because a pool is short, the strip went out
on Stories and covers. This check needs no model: a band along the top or bottom edge whose rows share one flat
background colour, that carries glyph-like marks, and that ends at a clear colour boundary is a caption strip and
is cropped away. Sky, sea, walls and letterboxing without marks are left alone.
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

MAX_STRIP = 0.08        # a strip deeper than this share of the height is not a caption bar
MIN_STRIP_PX = 8        # thinner than this at analysis scale is noise
PAD = 0.75              # share of a padding row in the strip's background colour
STOP = 0.12             # at most this share in the rows just after the strip: it stops, it does not fade
NEAR = 45.0             # RGB distance counted as the strip's background
MAX_BORDER = 3          # thin frame rows allowed before the strip (analysis scale)
MARKS = (0.02, 0.5)     # share of strip pixels away from that colour: glyphs, not texture, not empty
BOUNDARY = 30.0         # RGB distance between the strip colour and the picture beneath it
ANALYSIS_W = 600


def _strip_depth(rgb: np.ndarray) -> int:
    """Rows (from the top of `rgb`, H x W x 3 float32) up to the end of a caption strip, 0 when there is none.

    A printed bar is a flat background with padding rows at both ends and glyph rows between (bold text in a
    JPEG takes most of a glyph row: Richat's bar measured 17-31% background there), and it stops: the row after
    its last padding row is almost none of its colour. A border of up to MAX_BORDER rows before it is cut too.
    """
    h = rgb.shape[0]
    limit = max(MIN_STRIP_PX + 1, int(h * MAX_STRIP))
    for start in range(MAX_BORDER + 1):
        bg = np.median(rgb[start:start + 3].reshape(-1, 3), axis=0)
        near = np.linalg.norm(rgb[:limit + 4] - bg, axis=2) < NEAR
        share = near.mean(axis=1)
        if share[start:start + 3].min() < 0.8:
            continue
        for end in range(start + MIN_STRIP_PX, limit):
            if share[end - 1] < PAD or share[end:end + 3].max() > STOP:
                continue
            depth = end - start
            body = share[start:end]
            pad = max(1, depth // 8)
            if body[:pad].min() < PAD or body[-pad:].min() < PAD:
                break
            marks = 1 - near[start:end].mean()
            if not (MARKS[0] <= marks <= MARKS[1]) or (body < 0.9).sum() < 2:
                break
            below = rgb[end + 1: end + 1 + max(4, depth // 2)].reshape(-1, 3)
            if np.linalg.norm(np.median(below, axis=0) - bg) < BOUNDARY:
                break
            return end
    return 0


def caption_strip(im: Image.Image) -> tuple[str, int] | None:
    """("top" | "bottom", pixels in `im`) of a caption strip printed along that edge, or None."""
    w, h = im.size
    if w < 64 or h < 64:
        return None
    scale = min(1.0, ANALYSIS_W / w)
    small = im.convert("RGB").resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BOX)
    rgb = np.asarray(small, dtype=np.float32)
    for edge, arr in (("top", rgb), ("bottom", rgb[::-1])):
        depth = _strip_depth(arr)
        if depth:
            px = min(int(h * MAX_STRIP), int(np.ceil((depth + 2) / scale)))
            return edge, px
    return None


def without_caption_strip(im: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """`im` with any caption strip cropped off, and the kept box in `im`'s pixels (the whole image if none)."""
    w, h = im.size
    found = caption_strip(im)
    if not found:
        return im, (0, 0, w, h)
    edge, px = found
    box = (0, px, w, h) if edge == "top" else (0, 0, w, h - px)
    log.info("Caption strip along the %s edge (%d px) cropped away.", edge, px)
    return im.crop(box), box


def remap_focus(focus_box, kept_box, size) -> list | None:
    """A 0-1 focus box on the original `size` expressed on the cropped `kept_box`; None if it no longer fits."""
    w, h = size
    x0, y0, x1, y1 = kept_box
    kw, kh = x1 - x0, y1 - y0
    fx0, fy0, fx1, fy1 = focus_box
    out = [(fx0 * w - x0) / kw, (fy0 * h - y0) / kh, (fx1 * w - x0) / kw, (fy1 * h - y0) / kh]
    out = [min(1.0, max(0.0, v)) for v in out]
    return out if out[0] < out[2] and out[1] < out[3] else None


def clean_hero(source: Image.Image, focus: dict | None) -> tuple[Image.Image, dict | None]:
    """A cover's hero without its caption strip, and its reviewed focus re-expressed on the cropped picture (a
    focus that no longer fits is dropped: the cover keeps the whole-photo layout)."""
    if source.info.get("essential_full_image"):
        return source, None
    clean, box = without_caption_strip(source)
    if focus is None or box == (0, 0) + source.size:
        return clean, focus
    moved = remap_focus(focus["focus_box"], box, source.size)
    return clean, ({**focus, "focus_box": moved, "caption_strip_box": list(box)} if moved else None)
