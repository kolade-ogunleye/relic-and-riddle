"""The 16:9 YouTube thumbnail (2026-11-01 design review: 4.5/10 before).

The documentary's thumbnail was drawn by the old depth-layered engine: a Cinzel headline, outlined pills around
an eyebrow and a tagline, eight words competing, no masthead, and at YouTube's mobile search size (about 168 px
wide) the text was about 6% of the frame, unreadable. The panel's specification, in the covers' own system:

- the photograph full-bleed, its reviewed feature (else its centre) placed right of centre;
- an ink gradient from the left edge (85% over the left 42%, easing out by x 1180), the type on it;
- the masthead top-left: the gold diamond and RELIC & RIDDLE in gold Cinzel Bold at 64 px;
- the hero: the copy's measured figure in gold Anton ("25 MILES", the unit at half the figure's size) with the
  subject's name tight under it in bone (at most four words in all), else a year from the tagline ("1963") over
  the payoff line, else the two headline lines in Anton, bone then gold; no eyebrow, no tagline, no pills;
- a crop that clamps and leaves the reviewed feature left of x 1250 is enlarged (at most x1.35) until it
  lands at x 1300-1380, and the type's column stops short of the feature;
- YouTube's timestamp corner (x 1620-1920, y 950-1080) carries nothing.

Every line is planned at a measured contrast (typography.plan_text), like every other still.
"""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageOps

from . import brand
from .typography import cap_height, fit_font, get_font, plan_text

W, H = 1920, 1080
LEFT = 96
TEXT_RIGHT = 1000
TIMESTAMP_ZONE = (1620, 950, 1920, 1080)
FOCUS_X = 1340            # where the reviewed feature's centre is placed
FOCUS_MIN_X = 1250        # a clamped crop that leaves it left of here is enlarged ...
FOCUS_LAND = (1300, 1380)  # ... until it lands in this band
MAX_ZOOM = 1.35           # never more than this beyond the cover fill
SHADE_ALPHA, SHADE_SOLID, SHADE_END = 0.85, 0.42 * W, 1180   # the left ink: 85% over the left 42%, then fading
BRAND_PX, BRAND_TRACK, BRAND_R, BRAND_GAP = 64, 4, 19, 22    # the masthead (2026-11-02 round 2: 44 px read small)
MAX_WORDS = 4             # across the headline lines
UNIT_RATIO = 0.5          # the unit beside the figure, at half its size
TITLE_GAP_EM = 0.08       # the figure's baseline to the title's caps, in title ems


def _photo(source: Image.Image, focus: dict | None, meta: dict | None = None) -> Image.Image:
    """The photograph filling the frame, its reviewed feature (else its centre) placed at x FOCUS_X. When the
    crop clamps at the photo's edge and leaves the feature left of FOCUS_MIN_X, it is enlarged (at most
    MAX_ZOOM) until the feature lands in FOCUS_LAND: on the ink side it sat under the headline."""
    src = ImageOps.exif_transpose(source).convert("RGB")
    sw, sh = src.size
    fill = max(W / sw, H / sh)
    fx, fy = 0.5, 0.5
    if focus:
        b = focus["focus_box"]
        fx, fy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2

    def place(scale):
        w, h = round(sw * scale), round(sh * scale)
        left = min(0, max(W - w, round(FOCUS_X - fx * w)))
        top = min(0, max(H - h, round(540 - fy * h)))
        return w, h, left, top

    scale = fill
    w, h, left, top = place(scale)
    if left + fx * w < FOCUS_MIN_X:   # the feature (or, unreviewed, the photo's centre) left on the ink side
        target = sum(FOCUS_LAND) / 2
        scale = fill * min(MAX_ZOOM, max(1.0, target / max(1.0, fx * sw * fill)))
        w, h, left, top = place(scale)
    canvas = Image.new("RGB", (W, H))
    canvas.paste(src.resize((w, h), Image.Resampling.LANCZOS), (left, top))
    if meta is not None:
        meta["zoom"] = round(scale / fill, 3)
        meta["focus_x"] = round(left + fx * w)
        if focus:
            b = focus["focus_box"]
            meta["focus_box"] = [round(left + b[0] * w), round(top + b[1] * h), round(left + b[2] * w),
                                 round(top + b[3] * h)]
    return canvas


def _gradient(im: Image.Image) -> Image.Image:
    """Ink at SHADE_ALPHA over the left 42% (the type's side), easing to nothing by SHADE_END."""
    import numpy as np
    x = np.arange(W, dtype=np.float32)
    t = np.clip((x - SHADE_SOLID) / (SHADE_END - SHADE_SOLID), 0, 1)
    a = (1 - t * t * (3 - 2 * t)) * SHADE_ALPHA
    alpha = Image.fromarray((a * 255).astype("uint8")[None, :]).resize((W, H))
    ink = Image.new("RGBA", (W, H), brand.INK + (255,))
    ink.putalpha(alpha)
    return Image.alpha_composite(im.convert("RGBA"), ink)


def _words(text: str, limit: int) -> str:
    words = " ".join((text or "").split()).upper().split()
    return " ".join(words[:limit])


def _intersects(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def render(source: Image.Image, copy: dict[str, Any], focus: dict | None = None,
           meta: dict | None = None) -> tuple[Image.Image, list]:
    """(the 1920x1080 thumbnail, its text plates) from a hero photograph and the cover copy. `meta`, when given,
    receives the crop's zoom and the reviewed feature's box on the canvas (`focus_box`)."""
    from .cover_design import _YEAR, _without, monument_figure
    meta = meta if meta is not None else {}
    im = _gradient(_photo(source, focus, meta))
    measure = ImageDraw.Draw(im)
    plates = []

    # The masthead: the gold diamond, then the name in gold Cinzel Bold, top-left.
    f_brand = get_font("Cinzel", BRAND_PX, weight=700)
    r, gap, top = BRAND_R, BRAND_GAP, 72
    cy = top + cap_height(f_brand) / 2
    name = plan_text(im, (LEFT + 2 * r + gap, top + cap_height(f_brand)), "RELIC & RIDDLE", f_brand, brand.GOLD,
                     "ls", BRAND_TRACK, halo_px=40, min_halo=0.35, max_halo=1.0)
    im = name.draw(im)
    d = ImageDraw.Draw(im)
    diamond = [(LEFT + r, cy - r), (LEFT + 2 * r, cy), (LEFT + r, cy + r), (LEFT, cy)]
    d.polygon(diamond, outline=brand.INK + (255,), width=3)
    d.polygon(diamond, fill=brand.GOLD + (255,))
    plates.append(("brand", name))

    # The type never reaches the reviewed feature: its column ends 48 px short of it.
    right = TEXT_RIGHT
    fb = meta.get("focus_box")
    if fb and LEFT + 640 <= fb[0] - 48 < right:   # a feature reaching further left than that is wider than the
        right = fb[0] - 48                       # frame's right side: the type keeps its column (recorded below)
    width = right - LEFT
    found = monument_figure(copy)
    year = None
    if not (found and found[1]):
        # No measured figure: a year from the tagline or the eyebrow is the hero when the copy has one.
        for field in ("tagline", "eyebrow"):
            m = _YEAR.search(str(copy.get(field) or ""))
            if m:
                year = (m.group(1), "", field, m.span())
                break
    hero = found if (found and found[1]) else year
    if hero:
        number, unit, field, span = hero
        rest = dict(copy)
        if field in ("line1", "line2"):
            rest[field] = _without(str(copy[field]), span)
        lines = [str(rest.get(k) or "") for k in ("line1", "line2")]
        if not unit:   # a year: the payoff line (the second) under it, else the first
            lines = [ln for ln in lines[::-1] if ln.strip()][:1]
        title = _words(" ".join(lines), MAX_WORDS - (2 if unit else 1))   # the figure (and its unit) count
        f_num = fit_font(measure, number, "Anton", 300, width - (300 if unit else 0), 160)
        f_unit = get_font("Anton", max(80, int(f_num.size * UNIT_RATIO)))
        f_t = fit_font(measure, title, "Anton", 150, width, 90) if title else None
        gap_t = round(TITLE_GAP_EM * f_t.size) if f_t else 0
        stack = cap_height(f_num) + (gap_t + cap_height(f_t) if f_t else 0)
        base = round(560 - stack / 2 + cap_height(f_num))
        p_num = plan_text(im, (LEFT, base), number, f_num, brand.GOLD, "ls", target=5.1)
        im = p_num.draw(im)
        plates.append(("figure", p_num))
        if unit:
            nx = LEFT + measure.textlength(number, font=f_num) + 20
            p_unit = plan_text(im, (nx, base), unit, f_unit, brand.GOLD, "ls", target=5.1)
            im = p_unit.draw(im)
            plates.append(("figure_unit", p_unit))
        if title:
            p_t = plan_text(im, (LEFT, base + gap_t + cap_height(f_t)), title, f_t, brand.BONE, "ls", target=5.1)
            im = p_t.draw(im)
            plates.append(("title", p_t))
    else:
        l1, l2 = _words(copy.get("line1", ""), 3), _words(copy.get("line2", ""), 3)
        lines = [(t, c) for t, c in ((l1, brand.BONE), (l2, brand.GOLD)) if t]
        size = 190
        while size > 96 and any(measure.textlength(t, font=get_font("Anton", size)) > width for t, _ in lines):
            size -= 4
        f = get_font("Anton", size)
        pitch = int(cap_height(f) * 1.18)
        y = 560 - (pitch * len(lines)) // 2 + cap_height(f)
        for k, (t, colour) in enumerate(lines):
            p = plan_text(im, (LEFT, y + k * pitch), t, f, colour, "ls", target=5.1)
            im = p.draw(im)
            plates.append((f"title_{k + 1}", p))
    if fb:
        meta["text_over_focus"] = [n for n, p in plates if n != "brand" and _intersects(p.box, fb)]
    return im.convert("RGB"), plates
