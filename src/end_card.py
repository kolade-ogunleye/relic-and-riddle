"""
src/end_card.py
===============
The Reel's closing card, appended after the narration. Plain type, no capsule ("plain_v4", operator decision
2026-10-29: the approved capsule v3 read as a button, visual critic round 4): the channel name, a gold rule drawn
out from a small gold diamond (the brand's mark, kept at the operator's request), the share ask in bone on two lines where it fits at 54 px or more, then FOLLOW FOR THE NEXT RELIC as one line of
tracked gold capitals. No outline, no dark ground behind it, no sheen.

The last shot keeps pushing in while it is dimmed and, when busy, softened, by the least that makes the card
read on that frame (measured: the text band at most 30/255 with little texture left, the rows above and below
at most 40/255). The channel name fades in, the gold rule draws out from the centre, the share line rises word by
word, then the follow line rises into place as the bell lands (BELL_AT). No handle: it differs between platforms.
Everything stays inside the Reel safe area's centred width (src/safe_zones.py), clear of the like/comment/share
rail, and every line's contrast is measured on the card's last frame (stats["contrast"]).

Its sound is part of the ambient bed (src/audio_engine.py), so it is mastered with the rest of the programme
and its recordings are in the bed's rights evidence: the music carries on under the card, a soft whoosh opens
it and the bell lands with the follow line at BELL_AT.
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import brand
from .safe_zones import REEL
from .typography import get_font

VARIANT = "plain_v4"     # capsule_v3 until 2026-10-29
SECONDS = 2.9            # the card's length after the narration
FPS = 30
WIDTH, HEIGHT = 1080, 1920
WHOOSH_AT, BELL_AT = 0.0, 1.95   # seconds into the card
BRAND = "RELIC & RIDDLE"
FOLLOW = "FOLLOW FOR THE NEXT RELIC"
GOLD, WHITE, INK = brand.GOLD, brand.BONE, brand.INK   # brand tokens; #D4AF37 gold until 2026-10-29
BAND = (0.26, 0.66)      # the rows the card's text occupies
TARGETS = {"band": 30.0, "band_std": 12.0, "outside": 40.0}   # 0-255 luminance after the dim
TOP = 650                # y of the share line's first row
# The share ask: two lines at the largest of these that fits (operator, 2026-10-29: two lines at >= 54 px), else
# three at the largest that fits, then longer asks smaller.
SHARE_SIZES = (62, 58, 54)
SHARE_PLAN = ((2, SHARE_SIZES), (3, SHARE_SIZES), (4, (52,)), (5, (46,)))
FOLLOW_SIZES = range(40, 29, -1)
FOLLOW_TRACK = 0.12      # the follow line's letter spacing, as a share of its size

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
    """Where everything goes: the share line's size and lines, the follow line's size and its box (the drawn
    line's ink, no capsule), all inside the Reel safe area's centred width."""
    from .typography import drawable
    max_w = REEL.centred_width(width, height)
    words = drawable(share, "Montserrat").split() or ["…"]  # no emoji or missing-glyph boxes (2026-10-29)
    share_font, lines = None, None
    for max_lines, sizes in SHARE_PLAN:
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
    line_h = int(share_font.size * 1.24)
    follow_font = spacing = follow_w = None
    for size in FOLLOW_SIZES:  # the largest follow line that fits the safe width
        follow_font, spacing = get_font("Montserrat", size, weight=800), size * FOLLOW_TRACK
        follow_w = _tracked_width(follow_font, FOLLOW, spacing)
        if follow_w <= max_w - 20:
            break
    follow_y = TOP + len(lines) * line_h + 46
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    ink = probe.textbbox((0, 0), FOLLOW, font=follow_font)
    cx = width / 2
    box = (cx - follow_w / 2, follow_y + ink[1], cx + follow_w / 2, follow_y + ink[3])
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



def _pushed(arr: Image.Image, z: float, width: int, height: int) -> np.ndarray:
    w, h = arr.size
    cw, ch = w / z, h / z
    return np.asarray(arr.resize((width, height), Image.LANCZOS, box=((w - cw) / 2, (h - ch) / 2, (w + cw) / 2, (h + ch) / 2)),
                      dtype=np.float32)


def zoom_at(t: float) -> float:
    """The push-in: from the last frame as it is (1.0), easing out to 3.5% closer by the card's end."""
    u = max(0.0, min(1.0, t / SECONDS))
    return 1.0 + 0.035 * (1 - (1 - u) ** 2)


def _masks(lay: Dict[str, Any], f_brand, width: int, height: int) -> Dict[str, Image.Image]:
    """Each line's glyph mask as drawn at rest (for measuring its contrast on the card's last frame)."""
    out = {}
    m = Image.new("L", (width, height), 0)
    _tracked(ImageDraw.Draw(m), width / 2, TOP - 120, BRAND, f_brand, 255, 6)
    out["brand"] = m
    m = Image.new("L", (width, height), 0)
    d = ImageDraw.Draw(m)
    for li, line in enumerate(lay["lines"]):
        text = " ".join(line)
        d.text((width / 2 - d.textlength(text, font=lay["share_font"]) / 2, TOP + li * lay["line_h"]), text,
               font=lay["share_font"], fill=255)
    out["share"] = m
    m = Image.new("L", (width, height), 0)
    _tracked(ImageDraw.Draw(m), width / 2, lay["follow_y"], FOLLOW, lay["follow_font"], 255, lay["spacing"])
    out["follow"] = m
    return out


def frames(last: Image.Image, share: str, stats: Optional[Dict[str, Any]] = None) -> Iterator[Image.Image]:
    """The card's frames (RGB, WIDTHxHEIGHT, FPS) over the narration's last frame. `stats`, when given, receives
    the measured dim, the layout and each line's contrast on the last frame."""
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
    ink = np.array(INK, np.float32)
    rule_y = TOP - 44
    total = int(round(SECONDS * FPS))
    for i in range(total):
        t = i / FPS
        z = zoom_at(t)
        im = _pushed(base, z, width, height)
        sw = (ease(t / 0.6) * soften_w)[..., None]
        im = im * (1 - sw) + _pushed(soft_base, z, width, height) * sw
        dark = (ease(t / 0.45) * shape)[..., None]
        arr = im * (1 - dark) + ink * dark
        frame = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert("RGBA")
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        a = ease((t - 0.15) / 0.4)
        if a > 0:
            _tracked(d, width / 2, TOP - 120, BRAND, f_brand, (*GOLD, int(255 * a)), 6)
        r = ease((t - 0.25) / 0.5)
        if r > 0:   # the rule draws out from a gold diamond, both ways at once (the brand's mark, operator 2026-10-29)
            cx = width / 2
            d.polygon([(cx, rule_y - 9), (cx + 9, rule_y), (cx, rule_y + 9), (cx - 9, rule_y)],
                      fill=(*GOLD, int(255 * min(1, r * 2))))
            d.line([(cx + 20, rule_y), (cx + 20 + 170 * r, rule_y)], fill=(*GOLD, 235), width=4)
            d.line([(cx - 20, rule_y), (cx - 20 - 170 * r, rule_y)], fill=(*GOLD, 235), width=4)
        for k, (li, wi, w) in enumerate(words):   # the share line rises word by word
            wa = ease((t - 0.45 - 0.055 * k) / 0.3)
            if wa <= 0:
                continue
            line = lines[li]
            x = width / 2 - d.textlength(" ".join(line), font=f_share) / 2 + d.textlength(" ".join(line[:wi]) + (" " if wi else ""), font=f_share)
            d.text((x, TOP + li * line_h + 14 * (1 - wa)), w, font=f_share, fill=(*WHITE, int(255 * wa)))
        fa = ease((t - (BELL_AT - 0.35)) / 0.35)   # the follow line lands with the bell
        if fa > 0:
            _tracked(d, width / 2, follow_y + 10 * (1 - fa), FOLLOW, f_follow, (*GOLD, int(255 * fa)), spacing)
        out = Image.alpha_composite(frame, layer).convert("RGB")
        if stats is not None and i == total - 1:
            from .typography import ring_contrast
            rgb = np.asarray(out)
            stats["contrast"] = {name: ring_contrast(rgb, np.asarray(m), (0, 0)) for name, m in
                                 _masks(lay, f_brand, width, height).items()}
        yield out
