"""
src/end_card.py
===============
The Reel's closing card (operator-approved "capsule v3", 2026-10-25), appended after the narration.

The last shot keeps pushing in while it is dimmed and, when busy, softened, by the least that makes the card
read on that frame (measured: the text band at most 30/255 with little texture left, the rows above and below
at most 40/255). The channel name fades in, a gold rule draws out from a diamond, the share line rises word by
word, then FOLLOW FOR THE NEXT RELIC. A gold outline traces round it from the bottom centre (both ends at once,
meeting at the top), the capsule's ground darkens, and a sheen crosses capsule and letters as the bell lands.
No handle: it differs between platforms. Everything stays inside the Reel safe area (src/safe_zones.py), clear
of the like/comment/share rail.

Its sound is part of the ambient bed (src/audio_engine.py), so it is mastered with the rest of the programme
and its recordings are in the bed's rights evidence: the music carries on under the card, a soft whoosh opens
it and the bell lands with the sheen at BELL_AT.
"""
from __future__ import annotations

import math
import re
from itertools import combinations
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .safe_zones import REEL
from .typography import get_font

VARIANT = "capsule_v3"
SECONDS = 2.9            # the card's length after the narration
FPS = 30
WIDTH, HEIGHT = 1080, 1920
WHOOSH_AT, BELL_AT = 0.0, 1.95   # seconds into the card
BRAND = "RELIC & RIDDLE"
FOLLOW = "FOLLOW FOR THE NEXT RELIC"
GOLD, WHITE, INK = (212, 175, 55), (255, 255, 255), (8, 8, 11)
LIGHT, DEEP = np.array([248, 224, 142], np.float32), np.array([170, 128, 34], np.float32)  # gilt: lit top, deep foot
BAND = (0.26, 0.66)      # the rows the card's text occupies
TARGETS = {"band": 30.0, "band_std": 12.0, "outside": 40.0}   # 0-255 luminance after the dim
TOP = 650                # y of the share line's first row
SHARE_SIZES = (66, 60, 56, 52)   # px: the share line's size, the largest whose lines fit
FOLLOW_SIZES = range(40, 29, -1)
CAPSULE_PAD_X = 40

_FOLLOW_CLAUSE = re.compile(r"[\s,;:—–-]*(?:and\s+|then\s+|&\s+)?(?:please\s+)?follow\b.*$", re.IGNORECASE)


def card_text(share_line: str) -> str:
    """The share half of a caption's share-and-follow line: the card says FOLLOW itself ("Send this to the
    codebreaker you know, and follow for the next relic." -> "Send this to the codebreaker you know.")."""
    line = " ".join((share_line or "").split())
    head = _FOLLOW_CLAUSE.sub("", line).rstrip(" ,;:-—–")
    if not head:
        return ""
    return head if head[-1] in ".!?" else head + "."


def end_card_record(master, fallback: bool = False) -> Optional[Dict[str, Any]]:
    """What the run's payload keeps about its end card (from the master's composition record), so audience
    analysis can compare Reels with and without one; None when the master has none."""
    import json
    from pathlib import Path
    try:
        card = json.loads(Path(master).with_suffix(".composition.json").read_text(encoding="utf-8")).get("end_card")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(card, dict):
        return None
    return {"variant": card.get("variant"), "start_s": card.get("start_s"), "duration_s": card.get("duration_s"),
            "text": card.get("text"), "fallback": bool(fallback)}


def end_card_note(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """An info note for the review card when the end card had to use the plain fallback line."""
    if not record or not record.get("fallback"):
        return None
    return {"type": "end_card", "card": "End card", "severity": "info",
            "issue": (f"The writer's share line did not pass its checks, so the end card shows the plain line "
                      f"“{record.get('text')}”, not one about this story.")}


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_io(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 3 * t * t - 2 * t * t * t


def _tracked_width(font, text: str, spacing: float) -> float:
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    return sum(probe.textlength(c, font=font) for c in text) + spacing * (len(text) - 1)


def _tracked(d: ImageDraw.ImageDraw, cx: float, y: float, text: str, font, fill, spacing: float) -> float:
    widths = [d.textlength(c, font=font) for c in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = cx - total / 2
    for c, w in zip(text, widths):
        d.text((x, y), c, font=font, fill=fill)
        x += w + spacing
    return total


def _balanced(words: List[str], font, width: float, max_lines: int) -> Optional[List[List[str]]]:
    """`words` on the fewest lines (up to max_lines) that fit `width`, the most even break of those."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for n in range(1, min(max_lines, len(words)) + 1):
        best = None
        for cuts in combinations(range(1, len(words)), n - 1):
            parts = [words[a:b] for a, b in zip((0, *cuts), (*cuts, len(words)))]
            ws = [probe.textlength(" ".join(p), font=font) for p in parts]
            if max(ws) <= width and (best is None or max(ws) - min(ws) < best[0]):
                best = (max(ws) - min(ws), parts)
        if best:
            return best[1]
    return None


def layout(share: str, width: int = WIDTH, height: int = HEIGHT) -> Dict[str, Any]:
    """Where everything goes: the share line's size and lines, the follow line's size and the capsule's box, all
    inside the Reel safe area's centred width."""
    max_w = REEL.centred_width(width, height)
    words = share.split() or ["…"]
    share_font, lines = None, None
    for max_lines, sizes in ((3, SHARE_SIZES), (4, SHARE_SIZES[-1:]), (5, (46,))):   # longer: more lines, then smaller
        for size in sizes:
            share_font = get_font("Montserrat", size, weight=700)
            lines = _balanced(words, share_font, max_w, max_lines)
            if lines:
                break
        if lines:
            break
    if not lines:  # longer still (the writer's limit keeps it far shorter): filled lines, never wider than the area
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        lines = [[]]
        for w in words:
            if lines[-1] and probe.textlength(" ".join(lines[-1] + [w]), font=share_font) > max_w:
                lines.append([])
            lines[-1].append(w)
    line_h = int(share_font.size * 1.22)
    follow_font = spacing = follow_w = None
    for size in FOLLOW_SIZES:  # the largest follow line whose capsule fits the safe width
        follow_font, spacing = get_font("Montserrat", size, weight=700), size / 8
        follow_w = _tracked_width(follow_font, FOLLOW, spacing)
        if follow_w + 2 * CAPSULE_PAD_X <= max_w:
            break
    follow_y = TOP + len(lines) * line_h + 70
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    ink = probe.textbbox((0, 0), FOLLOW, font=follow_font)
    pad = follow_font.size * 0.55
    cx = width / 2
    box = (cx - follow_w / 2 - CAPSULE_PAD_X, follow_y + ink[1] - pad, cx + follow_w / 2 + CAPSULE_PAD_X, follow_y + ink[3] + pad)
    return {"share_font": share_font, "lines": lines, "line_h": line_h, "follow_font": follow_font, "spacing": spacing,
            "follow_y": follow_y, "box": box, "max_width": max_w,
            "share_widths": [probe.textlength(" ".join(ln), font=share_font) for ln in lines]}


def _bump(height: int) -> np.ndarray:
    rows = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    return 0.5 + 0.5 * np.cos(np.pi * np.clip(np.abs(rows - 0.46) / 0.54, 0, 1))   # smooth: no crease at the peak


def fit_dim(base: Image.Image) -> Tuple[np.ndarray, np.ndarray, Dict[str, float]]:
    """The lightest dim (per row) and softening (per row) that make the card read on this frame, and what they
    measure to. A busy text band (handwriting, foliage) is softened too, over the whole frame so no hazy stripe
    shows; a smooth one (a sky) is not."""
    h = base.size[1]
    lum = np.asarray(base.convert("L"), dtype=np.float32)
    bump = _bump(h)
    a, b = int(h * BAND[0]), int(h * BAND[1])
    texture = float(lum[a:b].std())
    soften = float(np.clip((texture - 18) / 30, 0, 0.85))
    soft = np.asarray(base.convert("L").filter(ImageFilter.GaussianBlur(14)), dtype=np.float32)
    weight = soften * (0.55 + 0.45 * bump)
    seen = lum * (1 - weight) + soft * weight
    ink = float(np.mean(INK))
    for floor in np.arange(0.50, 0.92, 0.02):
        for peak in np.arange(0.86, 0.975, 0.01):
            shape = np.clip(floor + (peak - floor) * bump, 0, 0.97)
            out = seen * (1 - shape) + ink * shape
            if (out[a:b].mean() <= TARGETS["band"] and out[a:b].std() <= TARGETS["band_std"]
                    and out[:a].mean() <= TARGETS["outside"] and out[b:].mean() <= TARGETS["outside"]):
                break
        else:
            continue
        break
    stats = {"floor": round(float(floor), 2), "peak": round(float(peak), 2), "soften": round(soften, 2),
             "texture": round(texture, 1), "band": round(float(out[a:b].mean()), 1),
             "band_std": round(float(out[a:b].std()), 1), "above": round(float(out[:a].mean()), 1),
             "below": round(float(out[b:].mean()), 1)}
    return shape, weight, stats


def _half_path(x0: float, y0: float, x1: float, y1: float, side: int, steps: int = 240) -> List[Tuple[float, float]]:
    """Half the capsule's outline, bottom centre round one end to top centre, as evenly spaced points."""
    r = (y1 - y0) / 2
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ex = x1 - r if side > 0 else x0 + r
    straight, arc = abs(ex - cx), math.pi * r
    total = 2 * straight + arc
    pts = []
    for k in range(steps + 1):
        s = total * k / steps
        if s <= straight:
            pts.append((cx + side * s, y1))
        elif s <= straight + arc:
            a = (s - straight) / r
            pts.append((ex + side * r * math.sin(a), cy + r * math.cos(a)))
        else:
            pts.append((ex - side * (s - straight - arc), y0))
    return pts


OUTLINE_WIDTH = 4.0      # px
OUTLINE_SS = 4           # supersampling for the stroke


class _Outline:
    """The capsule's outline as a direction-independent stroke: each half is sampled about every pixel and a disc
    of half the line width is stamped at each point, on a supersampled layer downsampled with LANCZOS. (PIL's
    wide lines are offset by their drawing direction, so the two halves, drawn in opposite directions, did not
    meet: a step at the centre, one edge thicker; operator, 2026-10-25.) A finished outline is drawn the same
    way, so the end state matches the traced one."""

    def __init__(self, box: Tuple[float, float, float, float]):
        margin = OUTLINE_WIDTH + 4
        self.x0, self.y0 = int(math.floor(box[0] - margin)), int(math.floor(box[1] - margin))
        self.size = (int(math.ceil(box[2] + margin)) - self.x0, int(math.ceil(box[3] + margin)) - self.y0)
        r = (box[3] - box[1]) / 2
        half = (box[2] - box[0] - 2 * r) + math.pi * r      # one half's length
        steps = max(8, int(math.ceil(half)))
        local = (box[0] - self.x0, box[1] - self.y0, box[2] - self.x0, box[3] - self.y0)
        self.halves = [_half_path(*local, side, steps) for side in (1, -1)]
        self._cache: Dict[int, Image.Image] = {}

    def alpha(self, n: int) -> Image.Image:
        """The first `n` points of each half, stroked: an L image the size of the outline's region."""
        n = max(1, min(n, len(self.halves[0])))
        if n in self._cache:
            return self._cache[n]
        ss, rad = OUTLINE_SS, OUTLINE_WIDTH / 2 * OUTLINE_SS
        big = Image.new("L", (self.size[0] * ss, self.size[1] * ss), 0)
        d = ImageDraw.Draw(big)
        for path in self.halves:
            for px, py in path[:n]:
                cx, cy = px * ss, py * ss
                d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=255)
        small = big.resize(self.size, Image.LANCZOS)
        if n == len(self.halves[0]):
            self._cache[n] = small
        return small

    def draw(self, layer: Image.Image, fraction: float, opacity: int = 240) -> None:
        n = max(2, int(len(self.halves[0]) * fraction))
        a = np.asarray(self.alpha(n), dtype=np.float32) * (opacity / 255)
        stroke = np.zeros((*a.shape, 4), np.uint8)
        stroke[..., :3] = GOLD
        stroke[..., 3] = a.clip(0, 255).astype(np.uint8)
        layer.alpha_composite(Image.fromarray(stroke, "RGBA"), (self.x0, self.y0))
        if fraction < 1:   # a bright point leads each stroke
            d = ImageDraw.Draw(layer)
            for path in self.halves:
                px, py = path[n - 1]
                px, py = px + self.x0, py + self.y0
                d.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(255, 236, 170, 255))


def _gild(arr: np.ndarray, y0: float, y1: float, sheen_cols: Optional[np.ndarray] = None) -> None:
    """Gold elements in rows y0..y1 get the gilt gradient; a passing sheen lights the letters too."""
    y0, y1 = max(0, int(y0)), min(arr.shape[0], int(y1))
    if y1 <= y0:
        return
    t = np.linspace(0, 1, y1 - y0, dtype=np.float32)[:, None, None]
    colour = LIGHT * (1 - t) + DEEP * t
    seg = arr[y0:y1]
    on = seg[..., 3:4] > 0
    rgb = np.where(on, np.broadcast_to(colour, seg[..., :3].shape), seg[..., :3])
    if sheen_cols is not None:
        rgb = rgb + (np.array([255, 246, 210], np.float32) - rgb) * (sheen_cols[None, :, None] * 1.6).clip(0, 0.85) * on
    seg[..., :3] = rgb


def _pushed(arr: Image.Image, z: float, width: int, height: int) -> np.ndarray:
    w, h = arr.size
    cw, ch = w / z, h / z
    return np.asarray(arr.resize((width, height), Image.LANCZOS, box=((w - cw) / 2, (h - ch) / 2, (w + cw) / 2, (h + ch) / 2)),
                      dtype=np.float32)


def zoom_at(t: float) -> float:
    """The push-in: from the last frame as it is (1.0), easing out to 3.5% closer by the card's end."""
    u = max(0.0, min(1.0, t / SECONDS))
    return 1.0 + 0.035 * (1 - (1 - u) ** 2)


def frames(last: Image.Image, share: str, stats: Optional[Dict[str, Any]] = None) -> Iterator[Image.Image]:
    """The card's frames (RGB, WIDTHxHEIGHT, FPS) over the narration's last frame. `stats`, when given, receives
    the measured dim and the layout."""
    width, height = WIDTH, HEIGHT
    base = last.convert("RGB").resize((width, height))
    shape, soften_w, dim = fit_dim(base)
    soft_base = base.filter(ImageFilter.GaussianBlur(14))
    lay = layout(share, width, height)
    f_brand, f_share, f_follow = get_font("Cinzel", 44), lay["share_font"], lay["follow_font"]
    lines, line_h, box, follow_y, spacing = lay["lines"], lay["line_h"], lay["box"], lay["follow_y"], lay["spacing"]
    if stats is not None:
        stats.update(dim=dim, box=[round(v, 1) for v in box], share_lines=[" ".join(ln) for ln in lines],
                     share_size=f_share.size, follow_size=f_follow.size, safe_width=lay["max_width"])
    words = [(li, wi, w) for li, line in enumerate(lines) for wi, w in enumerate(line)]
    outline = _Outline(box)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=(box[3] - box[1]) / 2, fill=255)
    mask_arr = np.asarray(mask, dtype=np.float32) / 255
    xs = np.arange(width, dtype=np.float32)[None, :]
    ink, gold = np.array(INK, np.float32), np.array(GOLD, np.float32)
    rule_y = TOP - 44
    for i in range(int(round(SECONDS * FPS))):
        t = i / FPS
        z = zoom_at(t)
        im = _pushed(base, z, width, height)
        sw = (ease(t / 0.6) * soften_w)[..., None]
        im = im * (1 - sw) + _pushed(soft_base, z, width, height) * sw
        dark = (ease(t / 0.45) * shape)[..., None]
        arr = im * (1 - dark) + ink * dark
        ground = (0.78 * ease((t - 1.35) / 0.6) * mask_arr)[..., None]   # the capsule's own dark ground
        arr = arr * (1 - ground) + ink * ground
        sheen_on = BELL_AT <= t <= BELL_AT + 0.55
        sx = box[0] - 120 + (box[2] - box[0] + 240) * ease_io((t - BELL_AT) / 0.55)
        sheen = np.exp(-((xs - sx) / 60) ** 2) * (0.30 if sheen_on else 0.0)
        glow = ((0.08 * ease((t - BELL_AT) / 0.3) + sheen) * mask_arr)[..., None]
        arr = arr * (1 - glow) + gold * glow
        frame = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert("RGBA")
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        a = ease((t - 0.15) / 0.4)
        if a > 0:
            _tracked(d, width / 2, TOP - 120, BRAND, f_brand, (*GOLD, int(255 * a)), 6)
        r = ease((t - 0.25) / 0.5)
        if r > 0:
            cx = width / 2
            d.polygon([(cx, rule_y - 9), (cx + 9, rule_y), (cx, rule_y + 9), (cx - 9, rule_y)], fill=(*GOLD, int(255 * min(1, r * 2))))
            d.line([(cx + 20, rule_y), (cx + 20 + 170 * r, rule_y)], fill=(*GOLD, 230), width=4)
            d.line([(cx - 20, rule_y), (cx - 20 - 170 * r, rule_y)], fill=(*GOLD, 230), width=4)
        for k, (li, wi, w) in enumerate(words):   # the share line rises word by word
            wa = ease((t - 0.45 - 0.055 * k) / 0.3)
            if wa <= 0:
                continue
            line = lines[li]
            x = width / 2 - d.textlength(" ".join(line), font=f_share) / 2 + d.textlength(" ".join(line[:wi]) + (" " if wi else ""), font=f_share)
            d.text((x, TOP + li * line_h + 14 * (1 - wa)), w, font=f_share, fill=(*WHITE, int(255 * wa)))
        fa = ease((t - 1.15) / 0.35)
        if fa > 0:
            _tracked(d, width / 2, follow_y + 8 * (1 - fa), FOLLOW, f_follow, (*GOLD, int(255 * fa)), spacing)
        trace = ease_io((t - 1.35) / 0.6)   # the outline runs round both ends and meets at the top
        if trace > 0:
            outline.draw(layer, trace)
        la = np.asarray(layer, dtype=np.float32).copy()
        _gild(la, TOP - 120, TOP - 120 + 52)
        _gild(la, rule_y - 10, rule_y + 10)
        _gild(la, box[1] - 4, box[3] + 4, sheen[0] if sheen_on else None)
        layer = Image.fromarray(la.clip(0, 255).astype(np.uint8), "RGBA")
        yield Image.alpha_composite(frame, layer).convert("RGB")
