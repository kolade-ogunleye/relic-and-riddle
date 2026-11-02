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
TOP = 780                # y of the share line's first row (9:16)
MAST_TOP = 490           # y of the masthead's cap top (9:16): the composition spreads through the Reel box
MASTHEAD_W = 740         # the masthead group at cover scale (panel round 2, 2026-11-02: ~200 px wide here, big
                         # everywhere else); on 9:16 the Reel box's centred width (696) caps it first
RULE_HALF = 120          # half the gold rule under the masthead
FOLLOW_GAP = 60          # px from the share block's last row to the follow line
# The share ask: two lines at the largest of these that fits (operator, 2026-10-29: two lines at >= 54 px), else
# three at the largest that fits, then longer asks smaller. Montserrat SemiBold (panel round 2: ~56 px, balanced).
SHARE_SIZES = (58, 56, 54)
SHARE_WEIGHT = 600
SHARE_PLAN = ((2, SHARE_SIZES), (3, SHARE_SIZES), (4, (52,)), (5, (46,)))
# The 16:9 YouTube end screen: YouTube draws its own elements over the last 5-20 s (a video or playlist slot and a
# subscribe button); the card's text stays clear of where they are placed (1920x1080 px).
LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT = 1920, 1080
END_SCREEN_SECONDS = 10.0
END_SCREEN_SLOTS = {"video": (1100, 260, 1820, 660), "subscribe": (140, 700, 420, 980)}
END_SCREEN_GAP = 60
LANDSCAPE_SHARE_SIZES = (64, 60, 56)
LANDSCAPE_MASTHEAD_W = 620
# The 9:16 loop bridge (operator, 2026-11-02): the Reel's ending, one share line that resolves into frame 0.
LOOP_SECONDS = 1.0
LOOP_TIMES = {"in": 0.25, "out": 0.5}   # the line is up by "in"; the crossfade to frame 0 starts at "out"
LOOP_DIM = 0.8                          # of the end card's measured dim: lighter, it is on screen for a second
LOOP_CENTRE = 0.40                      # the line's vertical centre, as a share of the height (y 768: the Reel box)
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


_FOLLOW_WORD = re.compile(r"\bfollow", re.IGNORECASE)


def says_follow(text: str) -> bool:
    """Whether a share line already asks for a follow (then the card does not ask again)."""
    return bool(_FOLLOW_WORD.search(text or ""))


def _masthead(max_w: int) -> dict[str, Any]:
    """The cover's masthead (src/cover_design.py _brand: the gold diamond, then RELIC & RIDDLE in tracked Cinzel
    Bold bone), scaled up from the cover's 64 px to the largest size whose group fits MASTHEAD_W and `max_w`."""
    from .cover_design import BRAND_PX, BRAND_TEXT, BRAND_TRACK, MARK_GAP, MARK_R
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    goal = min(MASTHEAD_W, max_w)
    best = None
    for size in range(BRAND_PX, 140):
        k = size / BRAND_PX
        font = get_font("Cinzel", size, weight=700)
        track = BRAND_TRACK * k
        r, gap = round(MARK_R * k), round(MARK_GAP * k)
        name_w = probe.textlength(BRAND_TEXT, font=font) + track * (len(BRAND_TEXT) - 1)
        group = 2 * r + gap + name_w
        if group > goal and best is not None:
            break
        best = {"text": BRAND_TEXT, "font": font, "track": track, "mark_r": r, "gap": gap, "width": round(group),
                "size": size}
    return best


def layout(share: str, width: int = WIDTH, height: int = HEIGHT) -> dict[str, Any]:
    """Where everything goes. 9:16: the masthead centred at cover scale, a short gold rule, the share line in bone
    and, unless the share line already says follow, FOLLOW FOR THE NEXT RELIC in tracked gold, all inside the Reel
    safe area's centred width and spread through its height. 16:9 (the YouTube end screen): the masthead top-left
    in title-safe, then the share line, left-aligned, all left of the end screen's video element and above its
    subscribe button (END_SCREEN_SLOTS); no follow line (the subscribe element asks)."""
    from .typography import drawable
    landscape = width > height
    words = drawable(share, "Montserrat").split() or ["…"]  # no emoji or missing-glyph boxes (2026-10-29)
    if landscape:
        x0, y0 = round(width * 0.08), round(height * 0.12)
        max_w = END_SCREEN_SLOTS["video"][0] - END_SCREEN_GAP - x0
        plan = ((1, LANDSCAPE_SHARE_SIZES), (2, LANDSCAPE_SHARE_SIZES), (3, LANDSCAPE_SHARE_SIZES), (4, (48,)))
    else:
        max_w = REEL.centred_width(width, height)
        plan = SHARE_PLAN
    share_font, lines = None, None
    for max_lines, sizes in plan:
        for size in sizes:
            share_font = get_font("Montserrat", size, weight=SHARE_WEIGHT)
            lines = _balanced(words, share_font, max_w, max_lines)
            if lines:
                break
        if lines:
            break
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    if not lines:  # longer still (the writer's limit keeps it far shorter): filled lines, never wider than the area
        lines = [[]]
        for w in words:
            if lines[-1] and probe.textlength(" ".join(lines[-1] + [w]), font=share_font) > max_w:
                lines.append([])
            lines[-1].append(w)
    line_h = round(share_font.size * 1.24)
    mast = _masthead(max_w if not landscape else LANDSCAPE_MASTHEAD_W)
    follow = not landscape and not says_follow(share)
    follow_font = spacing = follow_w = None
    for size in FOLLOW_SIZES:  # the largest follow line that fits the safe width
        follow_font, spacing = get_font("Montserrat", size, weight=800), size * FOLLOW_TRACK
        follow_w = _tracked_width(follow_font, FOLLOW, spacing)
        if follow_w <= max_w - 20:
            break
    cap = mast["font"].getbbox("R", anchor="lt")
    if landscape:
        mast_xy = (x0 + 2 * mast["mark_r"] + mast["gap"], y0)
        mark_c = (x0 + mast["mark_r"], y0 + (cap[1] + cap[3]) / 2)
        rule_y = y0 + cap[3] + 40
        rule = (x0, x0 + 240)
        top = rule_y + 70
        align_x = x0
    else:
        cx = width / 2
        mast_x0 = round(cx - mast["width"] / 2)
        mast_xy = (mast_x0 + 2 * mast["mark_r"] + mast["gap"], MAST_TOP)
        mark_c = (mast_x0 + mast["mark_r"], MAST_TOP + (cap[1] + cap[3]) / 2)
        rule_y = MAST_TOP + cap[3] + 64
        rule = (cx - RULE_HALF, cx + RULE_HALF)
        top = TOP
        align_x = None
    share_bottom = top + len(lines) * line_h
    follow_y = share_bottom + FOLLOW_GAP
    ink = probe.textbbox((0, 0), FOLLOW, font=follow_font)
    if follow:
        box = (width / 2 - follow_w / 2, follow_y + ink[1], width / 2 + follow_w / 2, follow_y + ink[3])
    else:   # no follow line: the box is the share block's
        widest = max(probe.textlength(" ".join(ln), font=share_font) for ln in lines)
        left = align_x if landscape else width / 2 - widest / 2
        box = (left, top, left + widest, share_bottom)
    return {"share_font": share_font, "lines": lines, "line_h": line_h, "follow_font": follow_font, "spacing": spacing,
            "follow_y": follow_y, "box": box, "max_width": max_w, "follow": follow, "top": top,
            "masthead": mast, "masthead_xy": mast_xy, "mark_centre": mark_c, "rule_y": rule_y, "rule": rule,
            "align_x": align_x, "landscape": landscape,
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


def _word_xs(lay: dict[str, Any], width: int) -> list[tuple[int, int, str, float, int]]:
    """(line, word, text, x, y) of every word of the share line as it rests."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f = lay["share_font"]
    out = []
    for li, line in enumerate(lay["lines"]):
        text = " ".join(line)
        left = lay["align_x"] if lay["align_x"] is not None else width / 2 - probe.textlength(text, font=f) / 2
        for wi, w in enumerate(line):
            x = left + probe.textlength(" ".join(line[:wi]) + (" " if wi else ""), font=f)
            out.append((li, wi, w, x, lay["top"] + li * lay["line_h"]))
    return out


def _mark(d: ImageDraw.ImageDraw, lay: dict[str, Any], a: float, r_draw: float) -> None:
    """The masthead's gold diamond (ink edge, as on the cover) and the gold rule drawn out under the masthead."""
    cx, cy = lay["mark_centre"]
    r = lay["masthead"]["mark_r"]
    if a > 0:
        diamond = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
        d.polygon(diamond, outline=(*INK, int(255 * a)), width=3)
        d.polygon(diamond, fill=(*GOLD, int(255 * a)))
    if r_draw > 0:
        x0, x1 = lay["rule"]
        y = lay["rule_y"]
        if lay["landscape"]:
            d.line([(x0, y), (x0 + (x1 - x0) * r_draw, y)], fill=(*GOLD, 235), width=4)
        else:
            mid, half = (x0 + x1) / 2, (x1 - x0) / 2 * r_draw
            d.line([(mid - half, y), (mid + half, y)], fill=(*GOLD, 235), width=4)


def frames(last: Image.Image, share: str, stats: dict[str, Any] | None = None) -> Iterator[Image.Image]:
    """The card's frames (RGB, FPS) over the narration's last frame: WIDTHxHEIGHT for SECONDS when `last` is
    upright, the 16:9 YouTube end screen (1920x1080, END_SCREEN_SECONDS) when it is wide. `stats`, when given,
    receives the measured dim, the layout and each line's contrast on the last frame."""
    from .typography import plan_text
    landscape = last.width > last.height
    width, height = (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT) if landscape else (WIDTH, HEIGHT)
    seconds = END_SCREEN_SECONDS if landscape else SECONDS
    base = last.convert("RGB").resize((width, height))
    shape, soften_w, dim = fit_dim(base)
    soft_base = base.filter(ImageFilter.GaussianBlur(14))
    lay = layout(share, width, height)
    mast = lay["masthead"]
    total = round(seconds * FPS)
    ink = np.array(INK, np.float32)

    def ground(t: float) -> Image.Image:
        z = zoom_at(t * SECONDS / seconds)
        im = _pushed(base, z, width, height)
        sw = (ease(t / 0.6) * soften_w)[..., None]
        im = im * (1 - sw) + _pushed(soft_base, z, width, height) * sw
        dark = (ease(t / 0.45) * shape)[..., None]
        return Image.fromarray((im * (1 - dark) + ink * dark).clip(0, 255).astype(np.uint8))

    # Every line is planned on the card's last frame (the ground at rest) and drawn from that plan on each frame.
    rest = ground((total - 1) / FPS)
    brand_plate = plan_text(rest, lay["masthead_xy"], mast["text"], mast["font"], WHITE, "lt", mast["track"])
    words = _word_xs(lay, width)
    word_plates = [plan_text(rest, (round(x), y), w, lay["share_font"], WHITE) for _, _, w, x, y in words]
    follow_plate = (plan_text(rest, (width / 2, lay["follow_y"]), FOLLOW, lay["follow_font"], GOLD, "ma", lay["spacing"])
                    if lay["follow"] else None)
    if stats is not None:
        stats.update(dim=dim, box=[round(v, 1) for v in lay["box"]], share_lines=[" ".join(ln) for ln in lay["lines"]],
                     share_size=lay["share_font"].size, follow_size=lay["follow_font"].size if lay["follow"] else None,
                     follow=lay["follow"], safe_width=lay["max_width"], masthead_width=mast["width"],
                     size=[width, height], seconds=seconds)
    for i in range(total):
        t = i / FPS
        frame = ground(t)
        a = ease((t - 0.15) / 0.4)
        frame = brand_plate.draw(frame, a)
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _mark(ImageDraw.Draw(layer), lay, a, ease((t - 0.25) / 0.5))
        frame = Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB")
        for k, plate in enumerate(word_plates):   # the share line rises word by word
            wa = ease((t - 0.45 - 0.055 * k) / 0.3)
            if wa > 0:
                frame = plate.draw(frame, wa, round(14 * (1 - wa)))
        if follow_plate is not None:
            fa = ease((t - (BELL_AT - 0.35)) / 0.35)   # the follow line lands with the bell
            if fa > 0:
                frame = follow_plate.draw(frame, fa, round(10 * (1 - fa)))
        if stats is not None and i == total - 1:
            measured = {"brand": brand_plate.measure(frame),
                        "share": min((v for v in (p.measure(frame) for p in word_plates) if v is not None), default=None)}
            if follow_plate is not None:
                measured["follow"] = follow_plate.measure(frame)
            stats["contrast"] = measured
        yield frame


def loop_text(share: str) -> str:
    """The loop bridge's one line: the share half only (it never asks for a follow)."""
    return card_text(share) or " ".join((share or "").split())


def loop_bridge(last: Image.Image, first: Image.Image, share: str,
                stats: dict[str, Any] | None = None) -> Iterator[Image.Image]:
    """The 9:16 Reel's ending (operator, 2026-11-02): a loop bridge of LOOP_SECONDS instead of the end card. Over
    the narration's last frame the ground dims and the share line rises in, in bone, at a measured contrast; it
    holds, then the picture crossfades into the Reel's first frame `first` while the line fades out, so the bridge's
    last frame is the opening frame and the loop restarts without a cut. WIDTHxHEIGHT, FPS. `stats` receives the
    line, its size and its contrast at the hold."""
    from .typography import plan_text
    width, height = WIDTH, HEIGHT
    a_img = last.convert("RGB").resize((width, height))
    b_img = np.asarray(first.convert("RGB").resize((width, height)), dtype=np.float32)
    shape, _, dim = fit_dim(a_img)
    shape = shape * LOOP_DIM
    text = loop_text(share)
    lay = layout(text, width, height)
    lines, line_h = lay["lines"], lay["line_h"]
    top = round(LOOP_CENTRE * height - len(lines) * line_h / 2)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    a_arr = np.asarray(a_img, dtype=np.float32)
    ink = np.array(INK, np.float32)
    held = Image.fromarray((a_arr * (1 - shape[..., None]) + ink * shape[..., None]).clip(0, 255).astype(np.uint8))
    plates = []
    for li, line in enumerate(lines):
        s = " ".join(line)
        x = width / 2 - probe.textlength(s, font=lay["share_font"]) / 2
        plates.append(plan_text(held, (round(x), top + li * line_h), s, lay["share_font"], WHITE))
    total = round(LOOP_SECONDS * FPS)
    t_in, t_out = LOOP_TIMES["in"], LOOP_TIMES["out"]
    if stats is not None:
        stats.update(text=text, lines=[" ".join(ln) for ln in lines], share_size=lay["share_font"].size,
                     dim=dim, seconds=LOOP_SECONDS, follow=False)
    for i in range(total):
        u = i / max(1, total - 1)          # 0 on the narration's last frame, 1 on the Reel's first
        t = u * LOOP_SECONDS
        dark = ease(t / t_in) * shape
        ground = a_arr * (1 - dark[..., None]) + ink * dark[..., None]
        mix = ease_io((t - t_out) / (LOOP_SECONDS - t_out))
        frame = Image.fromarray((ground * (1 - mix) + b_img * mix).clip(0, 255).astype(np.uint8))
        la = ease(t / t_in) * (1 - ease_io((t - t_out) / (LOOP_SECONDS - t_out) * 1.4))
        for p in plates:
            frame = p.draw(frame, la, round(12 * (1 - ease(t / t_in))))
        if stats is not None and abs(t - (t_in + t_out) / 2) <= 0.5 / FPS + 1e-9:
            vals = [v for v in (p.measure(frame) for p in plates) if v is not None]
            stats["contrast"] = min(vals) if vals else None
        yield frame
