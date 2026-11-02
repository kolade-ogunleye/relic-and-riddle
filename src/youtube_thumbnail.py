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

Round 3 (2026-11-02): the ink feather ends at x 1040 (it dimmed the subject's left edge); a figure the fact sheet
ranges or disputes is lifted out of the copy (never one bare number), a hedged one is the hero only with the
copy's own qualifier above it, and the claim's dimension word sits under the unit (figure_dim, outside the word
budget, like the qualifier: both belong to the figure); when the hero's words repeat the video title, the subject
line gives way to another line of the verified thumbnail copy (else meta["title_overlap"]); every render is
measured at feed size (downscale_check: 168 and 246 px wide).

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
SHADE_ALPHA, SHADE_SOLID, SHADE_END = 0.85, 0.42 * W, 1040   # the left ink: 85% over the left 42%, then fading
BRAND_PX, BRAND_TRACK, BRAND_R, BRAND_GAP = 64, 4, 19, 22    # the masthead (2026-11-02 round 2: 44 px read small)
MAX_WORDS = 4             # across the headline lines
UNIT_RATIO = 0.5          # the unit beside the figure, at half its size
TITLE_GAP_EM = 0.08       # the figure's baseline to the title's caps, in title ems
DIM_PX = 44               # the claim's dimension word under the unit (figure_dim)
QUAL_PX = 96              # the copy's qualifier over the figure (figure_qualifier), at least; raised until it
                          # keeps LINE_FLOOR px of cap height at 168 px wide
QUAL_FONT, QUAL_WEIGHT, QUAL_TRACK = "Montserrat", 800, 3   # the covers' eyebrow face (r5 review: in IBM Plex Mono
                                                            # the period took a full cell, "EST ." with a gap)
DIM_TITLE_GAP = 40        # px of clear field from the dimension word's baseline to the title's cap line
PLATE_TARGET = 5.1        # every line's planned ratio at full size ...
FEED_TARGET_STEP, FEED_TARGET_MAX = 1.0, 9.1   # ... raised per line until it holds 4.5 at 246 px wide
FEED_HALO_GROWTH = 0.35   # each raise also widens that line's halo by 35% of its default spread
FEED_WIDTHS = (168, 246)  # YouTube's mobile search and home-feed thumbnail widths
HERO_FLOOR, LINE_FLOOR = 12, 7   # px of cap height at 168 px wide: the hero line, every other line
_STOP = {"THE", "A", "AN", "OF", "IN", "AND", "ON", "AT", "TO", "BY", "FROM", "HOW", "WHY", "WHAT"}
_TEXTURE = frozenset({"brand", "figure_dim", "provenance"})   # feed-size texture: no floor, no contrast gate


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


def _content(text: str) -> set:
    import re
    return {w for w in re.findall(r"[A-Z0-9']+", str(text or "").upper()) if w not in _STOP and len(w) > 1}


def render(source: Image.Image, copy: dict[str, Any], focus: dict | None = None,
           meta: dict | None = None, figure: dict | None = None, youtube_title: str | None = None,
           illustration: bool = False) -> tuple[Image.Image, list]:
    """(the 1920x1080 thumbnail, its text plates) from a hero photograph and the cover copy. `meta`, when given,
    receives the crop's zoom and the reviewed feature's box on the canvas (`focus_box`). `figure`: the fact
    sheet's word on the hero figure (cover_design.figure_check); `youtube_title`: the video's title, so the
    thumbnail does not repeat it; `illustration`: the photo is a generated illustration (the disclosure label).

    The feed-size gate closes the loop (r5 review, 2026-11-02: the delivered Gol Gumbaz sample planned its title
    at 5.1 full size and measured 4.43 at 246 px wide, failing its own gate): a line whose ratio falls under 4.5
    at feed size is planned again with a higher target and a wider halo until it holds or FEED_TARGET_MAX."""
    targets: dict[str, float] = {}
    given = dict(meta or {})
    while True:
        trial = dict(given)   # each attempt starts from the caller's meta, so nothing stale carries over
        im, plates = _render(source, copy, focus, trial, figure, youtube_title, illustration, targets)
        low = [n for n in _low_contrast(trial) if targets.get(n, PLATE_TARGET) + FEED_TARGET_STEP <= FEED_TARGET_MAX]
        if not low:
            break
        for n in low:
            targets[n] = targets.get(n, PLATE_TARGET) + FEED_TARGET_STEP
    if targets:
        trial["feed_targets"] = {n: round(t, 2) for n, t in targets.items()}
    if meta is not None:
        meta.update(trial)
    return im, plates


def _low_contrast(meta: dict) -> list:
    """The gated lines downscale_check found under 4.5 at 246 px wide."""
    return [n for n, row in (meta.get("downscale") or {}).items() if n not in _TEXTURE
            and row.get("contrast_246") is not None and row["contrast_246"] < 4.5]


def _render(source, copy, focus, meta, figure, youtube_title, illustration, targets):
    def feed(name: str, font) -> dict:
        """plan_text's target (and, once raised, a wider halo: at feed size a 21-31 px halo shrinks to 3-4 px and
        stops separating the glyph from a busy photo) for line `name`."""
        t = targets.get(name, PLATE_TARGET)
        if t <= PLATE_TARGET:
            return {"target": t}
        steps = (t - PLATE_TARGET) / FEED_TARGET_STEP
        base = int(min(31, max(21, font.size // 3)))
        return {"target": t, "halo_px": round(base * (1 + FEED_HALO_GROWTH * steps)), "max_halo": 1.0}
    from .cover_design import (
        _YEAR,
        _without,
        figure_qualifier,
        monument_figure,
        withhold_figure,
    )
    if figure and figure.get("conflict"):
        withheld = withhold_figure(copy, figure)
        if withheld != dict(copy):
            meta["figure_withheld"] = {"number": figure.get("number"), "unit": figure.get("unit"),
                                       "reason": figure.get("reason")}
        copy = withheld
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
    qualifier = figure_qualifier(copy, found)[0] if (found and found[1]) else ""
    if found and found[1] and figure and figure.get("hedged") and not qualifier:
        meta["figure_declined"] = "the sheet hedges this figure and the copy carries no qualifier for it"
        found = None   # never one bare number for an estimate: the headline carries it as written instead
    dimension = str((figure or {}).get("dimension") or "") if (found and found[1]) else ""
    year = None
    if not (found and found[1]) and "figure_declined" not in meta:
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
        budget = MAX_WORDS - (2 if unit else 1)   # the figure (and its unit) count
        title = _words(" ".join(lines), budget)
        if youtube_title and len(_content(title) & _content(youtube_title)) >= 2:
            # The title already says it: the subject line gives way to another verified line of the copy.
            alt = next(((k, _words(display_line, budget)) for k in ("eyebrow", "tagline")
                        for display_line in [str(rest.get(k) or "")]
                        if display_line.strip() and len(display_line.split()) <= budget
                        and len(_content(display_line) & _content(youtube_title)) < 2
                        and not _content(display_line) <= _content(title)), None)
            if alt:
                meta["tension_from"] = alt[0]
                title = alt[1]
            else:
                meta["title_overlap"] = True
        f_num = fit_font(measure, number, "Anton", 300, width - (300 if unit else 0), 160)
        f_unit = get_font("Anton", max(80, int(f_num.size * UNIT_RATIO)))
        f_dim = get_font("Montserrat", DIM_PX, weight=700)
        dim_lines = (dimension.split() if dimension and measure.textlength(dimension, font=f_dim) > 300 else (
            [dimension] if dimension else []))[:2] if unit else []
        f_q = None
        if qualifier:
            size = QUAL_PX
            while cap_height(get_font(QUAL_FONT, size, weight=QUAL_WEIGHT)) * FEED_WIDTHS[0] / W < LINE_FLOOR:
                size += 4
            f_q = get_font(QUAL_FONT, size, weight=QUAL_WEIGHT)
        f_t = fit_font(measure, title, "Anton", 150, width, 90) if title else None
        gap_t = round(TITLE_GAP_EM * f_t.size) if f_t else 0
        if dim_lines:   # the dimension word ends on the figure's baseline: the title keeps clear of it (r5: ~12 px)
            gap_t = max(gap_t, DIM_TITLE_GAP)
        q_h = cap_height(f_q) + 28 if f_q else 0
        stack = q_h + cap_height(f_num) + (gap_t + cap_height(f_t) if f_t else 0)
        base = round(560 - stack / 2 + q_h + cap_height(f_num))
        if f_q:
            p_q = plan_text(im, (LEFT, base - cap_height(f_num) - 28), qualifier, f_q, brand.GOLD, "ls", QUAL_TRACK,
                            **feed("figure_qualifier", f_q))
            im = p_q.draw(im)
            plates.append(("figure_qualifier", p_q))
        p_num = plan_text(im, (LEFT, base), number, f_num, brand.GOLD, "ls", **feed("figure", f_num))
        im = p_num.draw(im)
        plates.append(("figure", p_num))
        if unit:
            nx = LEFT + measure.textlength(number, font=f_num) + 20
            pitch = cap_height(f_dim) + 12
            unit_base = base - (len(dim_lines) * pitch + 6 if dim_lines else 0)
            p_unit = plan_text(im, (nx, unit_base), unit, f_unit, brand.GOLD, "ls", **feed("figure_unit", f_unit))
            im = p_unit.draw(im)
            plates.append(("figure_unit", p_unit))
            for k, t in enumerate(dim_lines[:2]):
                yb = base - (min(2, len(dim_lines)) - 1 - k) * pitch
                p_dim = plan_text(im, (nx, yb), t, f_dim, brand.BONE, "ls", 2, target=5.1)
                im = p_dim.draw(im)
                plates.append(("figure_dim", p_dim))
        if title:
            p_t = plan_text(im, (LEFT, base + gap_t + cap_height(f_t)), title, f_t, brand.BONE, "ls",
                            **feed("title", f_t))
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
            p = plan_text(im, (LEFT, y + k * pitch), t, f, colour, "ls", **feed(f"title_{k + 1}", f))
            im = p.draw(im)
            plates.append((f"title_{k + 1}", p))
    if illustration:
        im, p = _provenance(im)
        plates.append(("provenance", p))
    if fb:
        meta["text_over_focus"] = [n for n, p in plates if n != "brand" and _intersects(p.box, fb)]
    meta["_plates"] = plates
    downscale_check(im.convert("RGB"), meta)
    return im.convert("RGB"), plates


def _provenance(im: Image.Image):
    """The existing on-screen disclosure (video_composer.RECON_BADGE_LABEL) bottom-left, clear of the timestamp
    corner: IBM Plex Mono in STONE_LT on an INK plate."""
    from .video_composer import RECON_BADGE_LABEL
    f = get_font(brand.DATA_FONT, 30, weight=600)
    w = ImageDraw.Draw(im).textlength(RECON_BADGE_LABEL, font=f)
    y1 = H - 64
    ImageDraw.Draw(im).rectangle((LEFT, y1 - cap_height(f) - 20, LEFT + w + 28, y1), fill=brand.INK + (255,))
    p = plan_text(im, (LEFT + 14, y1 - 10), RECON_BADGE_LABEL, f, brand.STONE_LT, "ls", target=5.1, halo_px=0)
    return p.draw(im), p


def downscale_check(im: Image.Image, meta: dict) -> bool:
    """The thumbnail as the feed shows it (FEED_WIDTHS): each text plate's cap height there and its contrast on
    the downscaled pixels, into meta["downscale"]; meta["downscale_ok"] and meta["downscale_fails"] say whether
    the hero line keeps HERO_FLOOR px at 168 wide, every other line LINE_FLOOR px (the masthead, the dimension word
    and the disclosure are texture), every contrast 4.5 at 246 wide, and no plate reaches past the ink's fade."""
    plates = meta.get("_plates") or []
    hero = next((n for n in ("figure", "title_1", "title") if any(k == n for k, _ in plates)), None)
    texture = _TEXTURE
    out, fails = {}, []
    blurred = {w: im.resize((w, round(w * H / W)), Image.Resampling.LANCZOS).resize((W, H), Image.Resampling.BICUBIC)
               for w in FEED_WIDTHS}
    for name, p in plates:
        if p.empty:
            continue
        cap = cap_height(p.font)
        row = {f"cap_{w}": round(cap * w / W, 1) for w in FEED_WIDTHS}
        row.update({f"contrast_{w}": (round(v, 2) if v is not None else None)
                    for w in FEED_WIDTHS for v in [p.measure(blurred[w])]})
        out.setdefault(name, row)
        if name in texture:
            continue
        floor = HERO_FLOOR if name == hero else LINE_FLOOR
        if row["cap_168"] < floor:
            fails.append(f"{name} {row['cap_168']} px cap at 168 wide (floor {floor})")
        # Contrast is gated on the 246 px home-feed tile: at 168 px the resampling blurs every stroke into its
        # halo, so the ring measure there reads low even for the 300 px figure (recorded, not gated).
        c = row["contrast_246"]
        if c is not None and c < 4.5:
            fails.append(f"{name} contrast {c} at 246 wide")
        if p.box[2] > SHADE_END:
            fails.append(f"{name} reaches past the ink's fade (x {p.box[2]})")
    meta["downscale"], meta["downscale_fails"], meta["downscale_ok"] = out, fails, not fails
    return not fails
