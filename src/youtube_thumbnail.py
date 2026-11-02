"""The 16:9 YouTube thumbnail (2026-11-01 design review: 4.5/10 before).

The documentary's thumbnail was drawn by the old depth-layered engine: a Cinzel headline, outlined pills around
an eyebrow and a tagline, eight words competing, no masthead, and at YouTube's mobile search size (about 168 px
wide) the text was about 6% of the frame, unreadable. The panel's specification, in the covers' own system:

- the photograph full-bleed, its reviewed feature (else its centre) placed right of centre;
- an ink gradient from the left edge (92% at x 0 to 0 at x 1050), the type on it;
- the masthead top-left: the gold diamond and RELIC & RIDDLE in Cinzel Bold;
- the hero: the copy's measured figure in gold Anton ("25 MILES") with the subject's name under it in bone,
  else the two headline lines in Anton, bone then gold; at most four words, no eyebrow, no tagline, no pills;
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


def _photo(source: Image.Image, focus: dict | None) -> Image.Image:
    src = ImageOps.exif_transpose(source).convert("RGB")
    sw, sh = src.size
    scale = max(W / sw, H / sh)
    w, h = round(sw * scale), round(sh * scale)
    fx, fy = 0.5, 0.5
    if focus:
        b = focus["focus_box"]
        fx, fy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    left = min(0, max(W - w, round(1340 - fx * w)))
    top = min(0, max(H - h, round(540 - fy * h)))
    canvas = Image.new("RGB", (W, H))
    canvas.paste(src.resize((w, h), Image.Resampling.LANCZOS), (left, top))
    return canvas


def _gradient(im: Image.Image) -> Image.Image:
    import numpy as np
    x = np.arange(W, dtype=np.float32)
    a = np.clip(1 - x / 1050.0, 0, 1) ** 1.2 * 0.92
    alpha = Image.fromarray((a * 255).astype("uint8")[None, :]).resize((W, H))
    ink = Image.new("RGBA", (W, H), brand.INK + (255,))
    ink.putalpha(alpha)
    return Image.alpha_composite(im.convert("RGBA"), ink)


def _words(text: str, limit: int) -> str:
    words = " ".join((text or "").split()).upper().split()
    return " ".join(words[:limit])


def render(source: Image.Image, copy: dict[str, Any], focus: dict | None = None) -> tuple[Image.Image, list]:
    """(the 1920x1080 thumbnail, its text plates) from a hero photograph and the cover copy."""
    from .cover_design import _without, monument_figure
    im = _gradient(_photo(source, focus))
    measure = ImageDraw.Draw(im)
    plates = []

    # The masthead: the diamond, then the name, top-left.
    f_brand = get_font("Cinzel", 44, weight=700)
    r, gap, top = 13, 18, 84
    cy = top + cap_height(f_brand) / 2
    name = plan_text(im, (LEFT + 2 * r + gap, top + cap_height(f_brand)), "RELIC & RIDDLE", f_brand, brand.BONE,
                     "ls", 3, halo_px=40, min_halo=0.35, max_halo=1.0)
    im = name.draw(im)
    d = ImageDraw.Draw(im)
    diamond = [(LEFT + r, cy - r), (LEFT + 2 * r, cy), (LEFT + r, cy + r), (LEFT, cy)]
    d.polygon(diamond, outline=brand.INK + (255,), width=3)
    d.polygon(diamond, fill=brand.GOLD + (255,))
    plates.append(("brand", name))

    width = TEXT_RIGHT - LEFT
    found = monument_figure(copy)
    if found and found[1]:
        number, unit, field, span = found
        rest = dict(copy)
        if field in ("line1", "line2"):
            rest[field] = _without(str(copy[field]), span)
        title = _words(" ".join(str(rest.get(k) or "") for k in ("line1", "line2")), 3)
        f_num = fit_font(measure, number, "Anton", 300, width - 260, 160)
        f_unit = get_font("Anton", max(80, int(f_num.size * 0.37)))
        base = 600
        p_num = plan_text(im, (LEFT, base), number, f_num, brand.GOLD, "ls", target=5.1)
        im = p_num.draw(im)
        plates.append(("figure", p_num))
        nx = LEFT + measure.textlength(number, font=f_num) + 24
        p_unit = plan_text(im, (nx, base), unit, f_unit, brand.GOLD, "ls", target=5.1)
        im = p_unit.draw(im)
        plates.append(("figure_unit", p_unit))
        if title:
            f_t = fit_font(measure, title, "Anton", 150, width, 90)
            p_t = plan_text(im, (LEFT, base + 40 + cap_height(f_t)), title, f_t, brand.BONE, "ls", target=5.1)
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
        y = 540 - (pitch * len(lines)) // 2 + cap_height(f)
        for k, (t, colour) in enumerate(lines):
            p = plan_text(im, (LEFT, y + k * pitch), t, f, colour, "ls", target=5.1)
            im = p.draw(im)
            plates.append((f"title_{k + 1}", p))
    return im.convert("RGB"), plates
