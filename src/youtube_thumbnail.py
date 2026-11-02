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
from .typography import cap_height, fit_font, get_font, plan_text, tracked_width

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
QUAL_CONDENSED = "Anton"   # r7: the kicker's face when one word cannot hold the floor in Montserrat (_qual_font)
QUAL_FONT, QUAL_WEIGHT, QUAL_TRACK = "Montserrat", 800, 3   # the covers' eyebrow face (r5 review: in IBM Plex Mono
                                                            # the period took a full cell, "EST ." with a gap)
DIM_TITLE_GAP = 40        # px of clear field from the dimension word's baseline to the title's cap line
PLATE_TARGET = 5.1        # every line's planned ratio at full size ...
FEED_TARGET_STEP, FEED_TARGET_MAX = 1.0, 9.1   # ... raised per line until it holds 4.5 at 246 px wide
FEED_HALO_GROWTH = 0.35   # each raise also widens that line's halo by 35% of its default spread
FEED_WIDTHS = (168, 246)  # YouTube's mobile search and home-feed thumbnail widths
HERO_FLOOR, LINE_FLOOR = 12, 7   # px of cap height at 168 px wide: the hero line, every other line
_STOP = {"THE", "A", "AN", "OF", "IN", "AND", "ON", "AT", "TO", "BY", "FROM", "HOW", "WHY", "WHAT"}
# Feed-size texture: no floor, no contrast gate. r7: never a hedge or a year's event word ("qualifier",
# "figure_qualifier"): wherever a figure is shown its qualifier holds LINE_FLOOR at 168 px, else the figure is not shown.
_TEXTURE = frozenset({"brand", "figure_dim", "provenance"})
QUAL_LEADING = 18         # px between the two lines of a two-line hedge kicker ("BY ONE / ESTIMATE")
NUM_MAX = 300             # the numeral's ceiling (Anton). Round 4 proposed 400; held at 300: the larger type
                          # passes the feed gate on the branch's hard-photo test, which then no longer exercises
                          # the gate's re-plan loop (operator decision pending)
FEATHER_PAD, FEATHER_EASE, FEATHER_MIN_EASE = 40, 520, 320   # r7: the full ink holds to the type's edge + 40 (the
                          # type sits in solid ink), then a soft ease over 520 px that ends by the subject's centre
                          # (never under 320 px); the round-4 ease (240 px, capped at SHADE_END) still read as a hard
                          # vertical edge, and a long title ran past where it began
FEATHER_SLOPE_MAX = 1.0   # the fade's steepest step, in 8-bit levels over white per px (round 4: ~1.4)
BAND_PAD = (40, 72)       # the hero stack is centred between the masthead's foot + 40 and the frame's foot - 72
KICKER_RATIO, KICKER_GAP = 0.6, 30   # round 4: a withheld figure's headline filled from the eyebrow (kicker)
HEDGE_GAP = 22   # the headline path's hedge line (r7: at the qualifier kicker's feed floor, _qual_font) to the headline


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


def _gradient(im: Image.Image, type_right: float | None = None, stop: float | None = None) -> Image.Image:
    """Ink at SHADE_ALPHA over the left 42% (the type's side), easing to nothing by SHADE_END. With the type's
    measured right edge (round 4), the full ink holds to that edge plus FEATHER_PAD, so every line sits in solid
    ink (r7: it was capped short of a long title), and then eases over FEATHER_EASE, its steepest step under
    FEATHER_SLOPE_MAX: a soft gradient with no visible vertical edge. `stop`: the subject's centre on the canvas;
    the ink is gone by there (never sooner than FEATHER_MIN_EASE past the solid ink)."""
    import numpy as np
    solid, end = SHADE_SOLID, SHADE_END
    if type_right is not None:
        solid = type_right + FEATHER_PAD
        end = solid + FEATHER_EASE
        if stop is not None:
            end = max(solid + FEATHER_MIN_EASE, min(end, stop))
    x = np.arange(W, dtype=np.float32)
    t = np.clip((x - solid) / max(1.0, end - solid), 0, 1)
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


def _type_edge(plates) -> float | None:
    """The drawn type's right edge (every plate), or None when nothing is drawn."""
    boxes = [p.box[2] for _, p in plates if not p.empty]
    return float(max(boxes)) if boxes else None


def render(source: Image.Image, copy: dict[str, Any], focus: dict | None = None,
           meta: dict | None = None, figure: dict | None = None, youtube_title: str | None = None,
           illustration: bool = False, reveal: str | None = None) -> tuple[Image.Image, list]:
    """(the 1920x1080 thumbnail, its text plates) from a hero photograph and the cover copy. `meta`, when given,
    receives the crop's zoom and the reviewed feature's box on the canvas (`focus_box`). `figure`: the fact
    sheet's word on the hero figure (cover_design.figure_check); `youtube_title`: the video's title, so the
    thumbnail does not repeat it; `illustration`: the photo is a generated illustration (the disclosure label);
    `reveal`: the script's reveal or takeaway line (a tagline that answers it is never drawn).

    The feed-size gate closes the loop (r5 review, 2026-11-02: the delivered Gol Gumbaz sample planned its title
    at 5.1 full size and measured 4.43 at 246 px wide, failing its own gate): a line whose ratio falls under 4.5
    at feed size is planned again with a higher target and a wider halo until it holds or FEED_TARGET_MAX. The
    first pass also measures the type's right edge, and the ink's feather is fitted to it (round 4)."""
    targets: dict[str, float] = {}
    given = dict(meta or {})
    edge = None
    while True:
        trial = dict(given)   # each attempt starts from the caller's meta, so nothing stale carries over
        im, plates = _render(source, copy, focus, trial, figure, youtube_title, illustration, targets, edge, reveal)
        if edge is None and _type_edge(plates) is not None:
            edge = _type_edge(plates)
            continue
        low = [n for n in _low_contrast(trial) if targets.get(n, PLATE_TARGET) + FEED_TARGET_STEP <= FEED_TARGET_MAX]
        if not low:
            break
        for n in low:
            targets[n] = targets.get(n, PLATE_TARGET) + FEED_TARGET_STEP
    if targets:
        trial["feed_targets"] = {n: round(t, 2) for n, t in targets.items()}
    if edge is not None:
        trial["ink_edge"] = round(edge)
    if meta is not None:
        meta.update(trial)
    return im, plates


def _low_contrast(meta: dict) -> list:
    """The gated lines downscale_check found under 4.5 at 246 px wide."""
    return [n for n, row in (meta.get("downscale") or {}).items() if n not in _TEXTURE
            and row.get("contrast_246") is not None and row["contrast_246"] < 4.5]


def _qual_font(text: str, width: float):
    """(lines, font, track) for a qualifier or event kicker at LINE_FLOOR px of cap height at 168 px wide: one line
    (tracked, else untracked), else two at the word break that balances them (16:9 has the height the 9:16 cover
    lacks: "BY ONE / ESTIMATE" over the figure); else (r7) in Anton, the brand's condensed display face, at the same
    cap floor: one word cannot break, and the column beside a reviewed feature is ~660 px ("ESTIMATED" in
    Montserrat 800 is 710, "REDISCOVERED" 975). None when no such setting fits `width`."""
    size = QUAL_PX
    while cap_height(get_font(QUAL_FONT, size, weight=QUAL_WEIGHT)) * FEED_WIDTHS[0] / W < LINE_FLOOR:
        size += 4
    font = get_font(QUAL_FONT, size, weight=QUAL_WEIGHT)
    for track in (QUAL_TRACK, 0):
        if tracked_width(text, font, track) <= width:
            return [text], font, track
    words = text.split()
    best = None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        wide = max(tracked_width(a, font, QUAL_TRACK), tracked_width(b, font, QUAL_TRACK))
        if wide <= width and (best is None or wide < best[0]):
            best = (wide, [a, b])
    if best:
        return best[1], font, QUAL_TRACK
    size = QUAL_PX
    while cap_height(get_font(QUAL_CONDENSED, size)) * FEED_WIDTHS[0] / W < LINE_FLOOR:
        size += 2
    condensed = get_font(QUAL_CONDENSED, size)
    for track in (QUAL_TRACK, 0):
        if tracked_width(text, condensed, track) <= width:
            return [text], condensed, track
    return None


def _render(source, copy, focus, meta, figure, youtube_title, illustration, targets, edge=None, reveal=None):
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
        drawn_qualifier,
        figure_qualifier,
        hedge_line,
        monument_figure,
        must_withhold,
        resolving_tagline,
        withhold_figure,
    )
    from .multi_format import typeset
    why = resolving_tagline(copy, reveal)
    if why:
        meta["tagline_dropped"] = {"tagline": copy.get("tagline"), "reason": why}
        copy = dict(copy, tagline="")
    if must_withhold(figure):
        # A range or disputed value, or (r7) an estimate whose hedge cannot be shown legibly: no figure anywhere.
        withheld = withhold_figure(copy, figure)
        if withheld != dict(copy):
            meta["figure_withheld"] = {"number": figure.get("number"), "unit": figure.get("unit"),
                                       "reason": figure.get("reason")}
            if not figure.get("conflict"):
                meta["figure_declined"] = figure.get("reason") or "the figure's hedge cannot be shown"
        copy = withheld

    def withhold_here(why, found_):
        """The figure lifted out of the copy: its qualifier cannot hold the feed floor here (r7)."""
        nonlocal copy
        fig = figure if (figure and figure.get("unit")) else {"number": found_[0], "unit": found_[1]}
        meta["figure_declined"] = why
        meta["figure_withheld"] = {"number": fig.get("number"), "unit": fig.get("unit"), "reason": why}
        copy = withhold_figure(copy, fig, force=True)
    photo = _photo(source, focus, meta)
    im = _gradient(photo, edge, meta.get("focus_x"))
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
    # The band under the masthead the hero stack is centred in (round 4: it sat at a fixed y 560).
    centre = round((top + cap_height(f_brand) + BAND_PAD[0] + H - BAND_PAD[1]) / 2)

    # The type never reaches the reviewed feature: its column ends 48 px short of it.
    right = TEXT_RIGHT
    fb = meta.get("focus_box")
    if fb and LEFT + 640 <= fb[0] - 48 < right:   # a feature reaching further left than that is wider than the
        right = fb[0] - 48                       # frame's right side: the type keeps its column (recorded below)
    width = right - LEFT
    found = monument_figure(copy)
    q_raw = figure_qualifier(copy, found)[0] if (found and found[1]) else ""
    same = bool(figure and found and str(figure.get("number")) == found[0])
    qualifier = ""
    if q_raw:
        qualifier = str(figure.get("drawn_qualifier") or "") if (same and figure.get("drawn_qualifier")) else (
            drawn_qualifier(q_raw, found[1]))
    q_fit, q_name = None, "figure_qualifier"
    if found and found[1] and figure and figure.get("sheet_missing"):
        meta["figure_declined"] = figure.get("reason") or "the payload was fact-checked but its sheet did not load"
        found = None
    elif found and found[1] and figure and figure.get("hedged") and not qualifier:
        meta["figure_declined"] = "the sheet hedges this figure and the copy carries no qualifier for it"
        found = None   # never one bare number for an estimate: the headline carries it as written instead
    elif found and found[1] and qualifier:
        q_fit = _qual_font(qualifier, width)
        if q_fit is None:
            # The hedge does not hold the feed floor: the figure is shown nowhere (r7, operator default; round 4
            # set it in the headline with the hedge as texture, so the figure read bare at feed size).
            withhold_here(f"the hedge {qualifier!r} does not fit at feed size", found)
            found = None
    dimension = str((figure or {}).get("dimension") or "") if (found and found[1]) else ""
    year = None
    if not (found and found[1]) and "figure_declined" not in meta:
        # No measured figure: a year from the tagline or the eyebrow is the hero when the copy has one. With the
        # fact sheet's word on it (round 4), only with the sheet's own event word set above it ("REDISCOVERED").
        for field in ("tagline", "eyebrow"):
            m = _YEAR.search(str(copy.get(field) or ""))
            if m:
                year = (m.group(1), "", field, m.span())
                break
        if year and figure and figure.get("kind") == "year" and str(figure.get("number")) == year[0]:
            event = str(figure.get("event") or "")
            q_fit = _qual_font(event, width) if event else None
            if not event:
                meta["year_declined"] = figure.get("reason") or "the fact sheet gives no event word for this year"
                year = None
            elif q_fit is None:
                # r7: the event word cannot hold the feed floor ("REDISCOVERED", 12 capitals): the year is not the
                # hero (round 4 set the word as texture and 1963 read bare at feed size). The covers decline it too.
                meta["year_declined"] = f"the event word {event!r} does not fit at feed size"
                year = None
            else:
                qualifier = event
                meta["year_event"] = event
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
        title = typeset(title)
        # The numeral fills its column up to NUM_MAX.
        f_num = fit_font(measure, number, "Anton", NUM_MAX, width - (300 if unit else 0), 160)
        f_unit = get_font("Anton", max(80, int(f_num.size * UNIT_RATIO)))
        f_dim = get_font("Montserrat", DIM_PX, weight=700)
        dim_lines = (dimension.split() if dimension and measure.textlength(dimension, font=f_dim) > 300 else (
            [dimension] if dimension else []))[:2] if unit else []
        q_lines, f_q, q_track = q_fit if (q_fit and qualifier) else ([], None, QUAL_TRACK)
        num_w = measure.textlength(number, font=f_num)
        f_t = fit_font(measure, title, "Anton", 150, width, 90) if title else None
        gap_t = round(TITLE_GAP_EM * f_t.size) if f_t else 0
        if dim_lines:   # the dimension word ends on the figure's baseline: the title keeps clear of it (r5: ~12 px)
            gap_t = max(gap_t, DIM_TITLE_GAP)
        q_h = (len(q_lines) * cap_height(f_q) + (len(q_lines) - 1) * QUAL_LEADING + 28) if f_q else 0
        stack = q_h + cap_height(f_num) + (gap_t + cap_height(f_t) if f_t else 0)
        base = round(centre - stack / 2 + q_h + cap_height(f_num))
        for k, line in enumerate(q_lines if f_q else []):
            yq = base - cap_height(f_num) - 28 - (len(q_lines) - 1 - k) * (cap_height(f_q) + QUAL_LEADING)
            p_q = plan_text(im, (LEFT, yq), line, f_q, brand.GOLD, "ls", q_track, **feed(q_name, f_q))
            im = p_q.draw(im)
            plates.append((q_name, p_q))
        p_num = plan_text(im, (LEFT, base), number, f_num, brand.GOLD, "ls", **feed("figure", f_num))
        im = p_num.draw(im)
        plates.append(("figure", p_num))
        if unit:
            nx = LEFT + num_w + 20
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
        hedge = hedge_line(copy, figure)
        h_fit = _qual_font(hedge[0], width) if hedge else None
        if hedge and h_fit is None:
            found_h = monument_figure(copy)
            if found_h:
                withhold_here(f"the hedge {hedge[0]!r} does not fit at feed size", found_h)
            hedge = None
        l1, l2 = _words(copy.get("line1", ""), 3), _words(copy.get("line2", ""), 3)
        kicker = ""
        if bool(l1) != bool(l2) and ("figure_withheld" in meta or "figure_declined" in meta):
            # Round 4: a withheld or declined figure left one headline line (Richat: "SAHARA EYE" alone, a third
            # of the left half empty). The other line of the two-tone system comes from the verified copy: the
            # eyebrow, else the tagline, when it is short, new to the headline and not the video title again.
            kept = l1 or l2
            fill = next(((k, _words(str(copy.get(k) or ""), 3)) for k in ("eyebrow", "tagline")
                         if str(copy.get(k) or "").strip() and len(str(copy.get(k)).split()) <= 3
                         and not _content(copy.get(k)) <= _content(kept)
                         and not (youtube_title and len(_content(copy.get(k)) & _content(youtube_title)) >= 2)),
                        None)
            if fill:
                meta["headline_fill_from"] = fill[0]
                kicker = typeset(fill[1])
        lines = [(typeset(t), c) for t, c in ((l1, brand.BONE), (l2, brand.GOLD)) if t]
        h_lines, f_h, h_track = h_fit if (hedge and h_fit) else ([], None, QUAL_TRACK)
        size = 190
        while size > 96 and any(measure.textlength(t, font=get_font("Anton", size)) > width for t, _ in lines):
            size -= 4
        f = get_font("Anton", size)
        pitch = int(cap_height(f) * 1.18)
        h_h = (len(h_lines) * cap_height(f_h) + (len(h_lines) - 1) * QUAL_LEADING + HEDGE_GAP) if f_h else 0
        f_k = None
        if kicker:
            # The filled line sits over the kept one as a kicker, in bone Anton at most KICKER_RATIO of its size:
            # the kept line stays the hero at full size (at one shared size both fell under the feed floor).
            f_k = fit_font(measure, kicker, "Anton", int(size * KICKER_RATIO), width, 72)
            h_h += cap_height(f_k) + KICKER_GAP
        y = round(centre - (pitch * (len(lines) - 1) + cap_height(f) + h_h) / 2 + h_h + cap_height(f))
        if f_k:
            p_k = plan_text(im, (LEFT, y - cap_height(f) - KICKER_GAP), kicker, f_k, brand.BONE, "ls",
                            **feed("kicker", f_k))
            im = p_k.draw(im)
            plates.append(("kicker", p_k))
        if f_h:
            # The hedge on its own line above the headline (round 4), as on the covers ("ESTIMATED"), at the
            # feed floor and gated like every other line (r7: it was texture).
            foot = y - cap_height(f) - (cap_height(f_k) + KICKER_GAP if f_k else 0) - HEDGE_GAP
            for k, line in enumerate(h_lines):
                yh = foot - (len(h_lines) - 1 - k) * (cap_height(f_h) + QUAL_LEADING)
                p_h = plan_text(im, (LEFT, yh), line, f_h, brand.GOLD, "ls", h_track, **feed("qualifier", f_h))
                im = p_h.draw(im)
                plates.append(("qualifier", p_h))
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
        # halo, so the ring measure there reads low even for the 300 px figure (recorded, not gated). Round 4
        # tried gating the hero lines at 168 too: raising a line's target and halo does not lift its 168 px
        # measure on a busy photo, so the gate could not be met; it stays recorded.
        c = row["contrast_246"]
        if c is not None and c < 4.5:
            fails.append(f"{name} contrast {c} at 246 wide")

        if p.box[2] > SHADE_END:
            fails.append(f"{name} reaches past the ink's fade (x {p.box[2]})")
    meta["downscale"], meta["downscale_fails"], meta["downscale_ok"] = out, fails, not fails
    return not fails
