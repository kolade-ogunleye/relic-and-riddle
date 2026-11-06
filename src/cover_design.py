"""Photograph-first art direction for the existing Reel cover variants.

Photographs are composed inside the centre square before delivery crops are
made. Explicitly reviewed features can be framed more closely; other photos
stay whole. No saliency mask is treated as a semantic subject detector.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import brand
from .typography import (
    fit_text,
    get_font,
    place_text,
    plan_text,
    record_contrast,
    text_size,
)

DESIGN_VERSION = "cover-composition-2"


def reviewed_focus(path, kept=None):
    """An explicitly reviewed source region, bound to exact photo bytes.

    Unknown or changed photographs keep the conservative whole-image layout.
    Geometry-changing derivatives never inherit coordinates from another file.
    """
    import hashlib
    import json
    import math
    from .config import BASE_DIR
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if kept is not None:
            photos = [kept]
        else:
            data = json.loads((BASE_DIR / "data/cover_art_direction.json").read_text(encoding="utf-8"))
            photos = data.get("photos", []) if isinstance(data, dict) else []
        for photo in photos:
            if not isinstance(photo, dict):
                continue
            if photo.get("sha256") != digest:
                continue
            box = photo.get("focus_box") or []
            if (len(box) == 4 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
                    and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
                return {"focus_box": box, "sha256": digest, "reviewed": photo.get("reviewed", "")}
    except (OSError, ValueError, TypeError):
        pass
    return None


def _shade(im, top, bottom, strength=0.78, feather=280, foot=None):
    """Neutral, smoothly feathered shade; never re-grade the photograph's hue. `foot` (y, strength, ease): from y
    down, the shade eases to that lighter strength over `ease` px and holds it to `bottom` (round 4: the badge's
    empty area under the deck shows the ambient field faintly, not a dead black band)."""
    import numpy as np

    y = np.arange(im.height, dtype=np.float32)
    distance = np.maximum(np.maximum(top - y, y - bottom), 0)
    v = np.clip(1 - distance / feather, 0, 1)
    v = v * v * (3 - 2 * v)
    level = np.full_like(y, strength)
    if foot is not None:
        y0, light, ease = foot
        t = np.clip((y - y0) / max(1.0, ease), 0, 1)
        level = strength + (light - strength) * (t * t * (3 - 2 * t))
    alpha = Image.fromarray(np.clip(v * level * 255, 0, 255).astype("uint8")[:, None]).resize(im.size)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 255))
    layer.putalpha(alpha)
    return Image.alpha_composite(im.convert("RGBA"), layer)


def _scrim(im, box, strength=0.55, feather=120):
    """An INK scrim confined to `box`, eased to nothing over `feather` px outside it (round 3: the monument's
    full-width shade turned the lower half of every tile to flat ink and dimmed the subject under the type).

    The falloff is the box blurred by a Gaussian (sigma feather / SCRIM_SIGMAS), not a linear ramp: a ramp's
    plateau and foot are kinks, and over a bright wall they read as a grey rectangle with a hard right edge
    (r5 review, 2026-11-02, Gol Gumbaz's interior). A Gaussian has no kink anywhere, so the edge is invisible;
    the box is first grown by SCRIM_GROW sigmas so the type keeps ~93% of `strength` at its own edge."""
    import numpy as np
    from scipy.ndimage import gaussian_filter
    w, h = im.size
    sigma = max(1.0, feather / SCRIM_SIGMAS)
    k = SCRIM_DOWNSAMPLE
    x0, y0, x1, y1 = (v / k for v in box)
    pad = SCRIM_GROW * sigma / k
    yy, xx = np.ogrid[:-(-h // k), :-(-w // k)]
    inside = ((xx + 0.5 >= x0 - pad) & (xx + 0.5 <= x1 + pad) & (yy + 0.5 >= y0 - pad)
              & (yy + 0.5 <= y1 + pad)).astype(np.float32)
    # mode="nearest": the canvas edge continues the mask, so a stack against the left edge stays shaded there.
    v = gaussian_filter(inside, sigma / k, mode="nearest", truncate=4.0)
    alpha = Image.fromarray(np.clip(v * strength * 255, 0, 255).astype("uint8")).resize(
        (w, h), Image.Resampling.BICUBIC)
    layer = Image.new("RGBA", im.size, brand.INK + (255,))
    layer.putalpha(alpha)
    return Image.alpha_composite(im.convert("RGBA"), layer)


SCRIM_SIGMAS = 3.5      # _scrim: the feather spans this many sigmas past the box (~2% of strength left there)
SCRIM_DOWNSAMPLE = 4    # _scrim: the mask is solved at 1/4 size (a smooth field; bicubic up)
SCRIM_GROW = 1.5        # _scrim: the box grows by this many sigmas first, so the type keeps ~93% of strength


def cap_h_of(font):
    """A font's cap height (the top of "H" above its baseline), 0 for no font."""
    return -font.getbbox("H", anchor="ls")[1] if font is not None else 0


MONUMENT_SCRIM, MONUMENT_SCRIM_FEATHER = 0.6, 200    # round 3: the type stack's own scrim, not a full-width shade;
                                                     # r5: a Gaussian falloff over 200 px (no visible edge)
DIM_WORD_PX, DIM_WORD_TRACK = 38, 2.0                # the claim's dimension word under the monument's unit
QUALIFIER_GAP, QUALIFIER_TRACK = 24, 3.0             # the qualifier kicker's foot to the figure's cap line
QUALIFIER_FONT, QUALIFIER_WEIGHT = "Montserrat", 800  # the eyebrow's face (r5 review: in IBM Plex Mono the period took
                                                      # a full monospace cell and "EST." read as "EST .")
EYEBROW_QUALIFIER_GAP = 44                           # the eyebrow's ink foot to the qualifier's cap line (was ~20)
GRID_WIDTHS = (124, 180)                             # the profile grid's tile widths on phones (3:4 crop)
GRID_SCALE = tuple(w / 1080 for w in GRID_WIDTHS)
QUALIFIER_GRID_CAP = 9                               # px of cap height a qualifier keeps at 124 px wide
EYEBROW_PX, EYEBROW_MIN, EYEBROW_TRACK = 40, 36, 4.0   # Montserrat 800 tracked capitals, every style (2026-11-02)
TAGLINE_PX, TAGLINE_MIN = 38, 32                        # never under 32 px: at 24-30 it vanished in the grid
TAGLINE_TRACK = 3.0     # round 4: the deck's tracked capitals (~+80/1000 at 38 px), like the eyebrow; it was the
                        # only untracked caps line in the system and clotted ("BUILT WITHOUT RINGS")
_STOP = {"THE", "A", "AN", "OF", "IN", "AND", "ON", "AT", "TO", "BY"}


def display(text):
    """Display copy as drawn: three full stops become one true ellipsis (U+2026), as a shortened line ends; then
    the shared display typography (round 4, multi_format.typeset: an en dash in a numeric span, curly quotes).
    Glyphs only: never a word or a figure changed."""
    import re

    from .multi_format import typeset
    return typeset(re.sub(r"\s*\.{3}", "\u2026", str(text or "")))


def _tagline_lines(measure, text, width, size, minimum=TAGLINE_MIN):
    """The deck on one line in Montserrat with TAGLINE_TRACK between letters: the room is reduced by the tracking,
    as the eyebrow's is (_eyebrow_lines). Returns (lines, font, shortened)."""
    room = width - TAGLINE_TRACK * max(0, len(text) - 1)
    return fit_text(measure, text, "Montserrat", max(minimum, size), int(room), 1, minimum, sentences=False)


def redundant_tagline(copy):
    """True when every word of the tagline already stands in the headline or the eyebrow (a subline that
    repeats the place adds a line, not information, and is omitted)."""
    import re
    def words(t):
        return {w for w in re.findall(r"[A-Z0-9']+", str(t or "").upper()) if w not in _STOP}
    tag = words(copy.get("tagline"))
    return bool(tag) and tag <= words(" ".join(str(copy.get(k) or "") for k in ("line1", "line2", "eyebrow")))


_CAUSE = __import__("re").compile(
    r"\b(?P<verb>[A-Z]{3,}ED|MADE|BUILT|DUG|CUT|BORN|WORN|DRAWN|LEFT|SHAPED|WROUGHT|BLOWN|THROWN)\s+BY\s+"
    r"(?P<agent>[A-Z0-9' ]+)")


def _stem(word):
    """A content word's comparable stem: CARVED/CARVING/CARVES -> CARV, NATURAL -> NATUR (crude, on purpose: it
    only decides whether a reveal repeats a tagline's own cause)."""
    import re
    return re.sub(r"(?:ING|ED|ES|S|AL|E)$", "", word.upper())[:6] or word.upper()


def resolving_tagline(copy, reveal):
    """Why the tagline answers the mystery the cover should open (round 4), or "". Only when the script has a real
    reveal or takeaway line (`reveal`, the payload's closing line; r7: None or a blank line means none): the
    tagline states a cause or maker ("CARVED BY NATURE": a participle and "BY") that the reveal itself states (the
    maker's own word stands there), or every content word of it (two or more) stands in the reveal.
    A mystery hook the reveal does not answer ("GUARDED BY MONKS", "HAUNTED BY ...") is kept. The tagline is then
    dropped, never rewritten."""
    import re
    tag = str(copy.get("tagline") or "").upper()
    if not str(reveal or "").strip() or not tag.strip():
        return ""

    def words(t):
        return {w for w in re.findall(r"[A-Z0-9']+", str(t or "").upper()) if w not in _STOP and len(w) > 2}
    said = {_stem(w) for w in words(reveal)}
    cause = _CAUSE.search(tag)
    # The maker itself must stand in the reveal: a shared verb alone ("the gate is still guarded") is no answer.
    if cause and {_stem(w) for w in words(cause.group("agent"))} & said:
        return "the tagline states the cause the script reveals"
    tw = words(tag)
    if len(tw) >= 2 and tw <= words(reveal):
        return "the tagline repeats the script's reveal"
    return ""


def _eyebrow_lines(measure, text, width, size=EYEBROW_PX):
    """The eyebrow on one line in Montserrat 800 with EYEBROW_TRACK between letters, never under EYEBROW_MIN
    (shortened at a word with an ellipsis before it shrinks further). Returns (lines, font, shortened)."""
    room = width - EYEBROW_TRACK * max(0, len(text) - 1)
    return fit_text(measure, text, "Montserrat", max(EYEBROW_MIN, size), int(room), 1, EYEBROW_MIN,
                    sentences=False, weight=800)


def hedge_line(copy, figure):
    """(text, field) of the hedge a non-monument style sets on its own line above the headline line that carries
    the figure (round 4), or None. Only for a hedged figure (figure_check) whose qualifier is not in a title line
    (the badge "EST. 144 FT (44 M)", which these styles never draw, or an eyebrow or deck, which is texture at grid
    size: r7), in the same drawn form as the monument ("ESTIMATED"), so the styles never disagree on one figure.
    A flat-stated figure stays flat."""
    if not figure or must_withhold(figure) or not figure.get("hedged") or not figure.get("unit"):
        return None
    if not figure.get("qualifier") or figure.get("qualifier_from") in ("line1", "line2"):
        return None
    found = monument_figure(copy)
    if not found or found[2] not in ("line1", "line2") or found[0] != figure.get("number"):
        return None
    text = str(figure.get("drawn_qualifier") or figure.get("qualifier") or "").upper()
    return (text, found[2]) if text else None


def _rows(im, copy, width, style, tight, figure=None):
    """Fit deliberate headline hierarchy, retaining all four supplied fields."""
    measure = ImageDraw.Draw(im)
    factor = 0.88 if tight else 1
    sizes = {
        "depth_sandwich": (110, 118),   # round 3: the window takes the room (still 9 px cap at 124 px)
        "badge": (108, 164),
        "minimal": (100, 142),
        "lower_third": (120, 138),
    }[style]
    paper = style == "depth_sandwich"
    # Two-tone everywhere it applies (2026-11-02): the second title line is gold on ink; ink on the paper card.
    specs = [
        ("eyebrow", "eyebrow", "Montserrat", EYEBROW_PX, EYEBROW_MIN, brand.GOLD, 22),
        ("title_1", "line1", "Anton", sizes[0], 64, brand.BONE, 10),
        ("title_2", "line2", "Anton", sizes[1], 64,
         brand.GOLD if style in ("badge", "minimal") else brand.BONE, 26),
        ("tagline", "tagline", "Montserrat", TAGLINE_PX, TAGLINE_MIN, brand.STONE_LT, 0),
    ]
    rows, shortened = [], []
    hedge = hedge_line(copy, figure)
    for name, key, face, size, minimum, colour, gap in specs:
        text = display(copy.get(key)).strip().upper()
        if hedge and key == hedge[1] and text:
            # r7: at the qualifier kicker's grid floor (QUALIFIER_GRID_CAP at 124 px), never texture; figure_check
            # withholds the figure when it cannot be set so, and the last resort keeps the eyebrow size.
            fit = _qualifier_fit(hedge[0], width)
            lines, font, track = fit if fit else (_eyebrow_lines(measure, hedge[0], width, EYEBROW_PX)[:2]
                                                  + (EYEBROW_TRACK,))
            rows += [{"name": "qualifier", "text": line, "font": font, "fill": brand.GOLD, "track": track,
                      "height": text_size(measure, line, font)[1], "gap": HEDGE_GAP, "keep": bool(fit)}
                     for line in lines]
        if not text or (name == "tagline" and redundant_tagline(copy)):
            continue
        if name == "eyebrow":
            lines, font, short = _eyebrow_lines(measure, text, width)
            if short:
                shortened.append(name)
            rows += [{"name": name, "text": line, "font": font, "fill": colour, "track": EYEBROW_TRACK,
                      "height": text_size(measure, line, font)[1], "gap": gap} for line in lines]
            continue
        if name == "tagline":
            lines, font, short = _tagline_lines(measure, text, width, max(minimum, int(size * factor)))
            if short:
                shortened.append(name)
            rows += [{"name": name, "text": line, "font": font, "fill": colour, "track": TAGLINE_TRACK,
                      "height": text_size(measure, line, font)[1], "gap": gap} for line in lines]
            continue
        size = int(size * factor)
        if name.startswith("title"):
            # Keep normal short copy on one line. Longer wording gets two lines
            # before descending to the emergency floor, rather than tiny type.
            _, trial, short = fit_text(measure, text, face, size, width, 1,
                                        min(size, int(96 * factor)), sentences=False, quiet=True)
            if not short:
                size = trial.size
        lines, font, short = fit_text(measure, text, face, size, width, 2,
                                     min(size, minimum), sentences=False)
        if short:
            shortened.append(name)
        for i, line in enumerate(lines):
            rows.append(dict(name=name, text=line, font=font, fill=colour,
                             height=text_size(measure, line, font)[1],
                             gap=gap if i == len(lines) - 1 else 10))
    if paper:
        for row in rows:
            row["fill"] = brand.INK if row["name"] not in ("tagline", "qualifier") else brand.INK2
    return rows, shortened


HEDGE_GAP = 16   # the hedge line's foot to the headline line it qualifies


def _height(rows):
    return sum(r["height"] + r["gap"] for r in rows)


TAB_PAD = (24, 14)   # Discovery's eyebrow tab: horizontal, vertical padding
DIM_ROOM = 36        # Monument: the dimension line's room under the figure


def _top_anchor(xy, text, font, anchor, track):
    """(xy, anchor) that draws a tracked line where `anchor` ("lt"/"mt") would put it, on its baseline instead:
    tracked text is drawn a glyph at a time, and a per-glyph top anchor lifts a comma or a full stop to the cap
    line ("DERINKUYU, 1963" read "DERINKUYU' 1963"). Untracked lines are unchanged."""
    if not track or anchor[1] != "t":
        return xy, anchor
    return (xy[0], xy[1] - font.getbbox(text, anchor="ls")[1]), anchor[0] + "s"


def _paint(im, rows, x, top, centered, lay):
    plates = {}
    for row in rows:
        if row.get("tab"):
            # Discovery's badge (2026-11-02): the eyebrow on a solid gold tab, ink capitals on it.
            from .typography import tracked_width
            w_txt = tracked_width(row["text"], row["font"], row.get("track", 0))
            cap = row["font"].getbbox("H", anchor="lt")
            x_tab = x - w_txt / 2 - TAB_PAD[0] if centered else x - TAB_PAD[0] // 2
            box = (x_tab, top + cap[1] - TAB_PAD[1], x_tab + w_txt + 2 * TAB_PAD[0] - (0 if centered else TAB_PAD[0] // 2),
                   top + cap[3] + TAB_PAD[1])
            ImageDraw.Draw(im).rectangle(box, fill=brand.GOLD + (255,))
            if lay is not None:
                lay.meta.setdefault("tabs", {})[row["name"]] = [round(v) for v in box]
        xy, anchor = _top_anchor((x, top), row["text"], row["font"], "mt" if centered else "lt", row.get("track", 0))
        im, plate = place_text(im, xy, row["text"], row["font"], row["fill"], anchor=anchor, target=5.1,
                               track=row.get("track", 0), halo_px=0 if row.get("tab") else None)
        plates.setdefault(row["name"], []).append(plate)
        top += row["height"] + row["gap"]
    for name, ps in plates.items():
        record_contrast(lay, name, ps)
    if lay is not None:
        lay.meta.setdefault("plates", {}).update(plates)
    return im


BRAND_TEXT = "RELIC & RIDDLE"
BRAND_PX, BRAND_TRACK = 64, 4.0
BRAND_TOP = 462     # the masthead's ascender line, the same band in the grid's square on every style
MINIMAL_GAPS = (40, 22)   # minimal: masthead foot -> eyebrow caps, eyebrow foot -> title caps (ink to ink)
MARK_R, MARK_GAP = 13, 20   # the gold diamond (the brand's mark, as on the end card) and its gap to the name


MAST_HALO_MAX = 0.3                 # above this planned halo, the masthead gets a band scrim instead
MAST_SCRIM = (0.2, 0.75, 0.05)      # band strengths tried: from, to, step
MAST_SCRIM_MIN_EASE = 36            # px the band needs to ease out above a protected photo, else no band
MAST_SCRIM_FEATHER = (170, 140)     # the band's ease above the masthead's line, and below from its cap centre
                                    # (never onto a protected photograph below it)


def _mast_scrim(im, lay, xy, font, plate):
    """(image, plate): the least INK band (MAST_SCRIM steps) under which the masthead clears brand.PLACE_TARGET
    with a halo of at most MAST_HALO_MAX; the original plan when no band up to the strongest does (a pure-white
    striped sky keeps the full halo: legibility first)."""
    import numpy as np
    box = font.getbbox(BRAND_TEXT, anchor="lt")
    y0, y1 = xy[1] + box[1] - 6, xy[1] + box[3] + 6
    # The band eases out from the masthead's cap centre, not its foot (r5 review, 2026-11-02): eased from the
    # foot it had only the 40-60 px left above a full-width photo, a flat dark plateau then a fast ramp, read as
    # a hard line across Gol Gumbaz's sky. From the centre the knee sits behind the glyphs and the ramp is long.
    mid = (y0 + y1) / 2
    yy = np.arange(im.height, dtype=np.float32)
    up, down = MAST_SCRIM_FEATHER
    below = [b["box"][1] for b in (lay.protected if lay is not None else []) if b["box"][1] >= y1]
    if below:   # the reviewed feature (or a contained photograph) under the masthead stays ungraded
        down = max(1, min(down, min(below) - 2 - mid))
        if down < MAST_SCRIM_MIN_EASE:
            # A band that has to stop within a few px of the photograph reads as a hard seam across it
            # (round 3 review, 2026-11-02): keep the measured glyph halo instead.
            return im, plate
    d = np.where(yy < y0, (y0 - yy) / up, np.where(yy > mid, (yy - mid) / down, 0))
    v = np.clip(1 - d, 0, 1)
    v = v * v * (3 - 2 * v)
    strength = MAST_SCRIM[0]
    while strength <= MAST_SCRIM[1] + 1e-9:
        layer = Image.new("RGBA", im.size, brand.INK + (255,))
        layer.putalpha(Image.fromarray((v * strength * 255).astype("uint8")[:, None]).resize(im.size))
        trial = Image.alpha_composite(im.convert("RGBA"), layer)
        p = plan_text(trial, xy, BRAND_TEXT, font, brand.BONE, "lt", BRAND_TRACK, halo_px=24, min_halo=0.0,
                      shadow=True, max_halo=MAST_HALO_MAX)
        if p.contrast is not None and p.contrast >= brand.PLACE_TARGET:
            if lay is not None:
                lay.meta["mast_scrim"] = {"strength": round(strength, 2), "band": [round(y0), round(y1)]}
            return trial, p
        strength += MAST_SCRIM[2]
    return im, plate


def _brand(im, lay, centered=False, paper=False, left=None):
    """The masthead: the gold diamond mark, then RELIC & RIDDLE, at a measured contrast like every other line.

    40 px Cinzel Bold (2026-11-01): at 32 px regular it was 11 px tall on a 390 px phone and a smear in the
    profile grid; the diamond is what still registers at grid size. On the paper ground (a known light, flat
    field) the name is ink and the mark dark gold. On photographs the name is always bone with the solved ink
    halo (brand.PLACE_TARGET), as the carousel header is: a mean-luminance switch between ink and bone, with the
    halo disabled, left it unreadable on mid-tone or busy photographs.
    """
    font = get_font("Cinzel", BRAND_PX, weight=700)
    measure = ImageDraw.Draw(im)
    name_w = text_size(measure, BRAND_TEXT, font)[0] + BRAND_TRACK * (len(BRAND_TEXT) - 1)
    group = 2 * MARK_R + MARK_GAP + name_w
    # One place on every style (2026-11-02 design review: it moved between left and centre): centred on the
    # canvas, the same band in the profile grid's square on every tile.
    # Freeze pass (2026-11-04): a left-set card (the first frame) sets it flush left, its mark's ink at x `left`.
    x0 = round(540 - group / 2) if left is None else int(left)
    top = BRAND_TOP
    cap = font.getbbox("R", anchor="lt")
    cy = top + (cap[1] + cap[3]) / 2
    cx = x0 + MARK_R
    xy = (x0 + 2 * MARK_R + MARK_GAP, top)
    if paper:
        im, plate = place_text(im, xy, BRAND_TEXT, font, brand.INK,
                               anchor="lt", track=BRAND_TRACK, halo_px=0, shadow=False)
        mark, edge = brand.GOLD_DIM, None
    else:
        # A 40 px halo allowed to reach full strength is what clears brand.PLACE_TARGET on a pure-white sky;
        # the 0.35 floor keeps busy texture off the glyph edges.
        plate = plan_text(im, xy, BRAND_TEXT, font, brand.BONE, "lt", BRAND_TRACK,
                          halo_px=40, min_halo=0.35, shadow=True, max_halo=1.0)
        if plate.halo > MAST_HALO_MAX:
            # Round 3: over a bright sky the glyph-shaped halo grew into a grey rounded box. An eased INK band
            # across the masthead's line takes the load instead, so a smaller halo still clears the target.
            im, plate = _mast_scrim(im, lay, xy, font, plate)
        im = plate.draw(im)
        mark, edge = brand.GOLD, brand.INK
    diamond = [(cx, cy - MARK_R), (cx + MARK_R, cy), (cx, cy + MARK_R), (cx - MARK_R, cy)]
    rgba = im.convert("RGBA")
    d = ImageDraw.Draw(rgba)
    if edge is not None:
        d.polygon(diamond, outline=edge + (255,), width=3)
    d.polygon(diamond, fill=mark + (255,))
    im = rgba if im.mode == "RGBA" else rgba.convert(im.mode)
    if lay is not None:
        lay.add("brand_mark", (cx - MARK_R, cy - MARK_R, cx + MARK_R, cy + MARK_R))
    record_contrast(lay, "brand", [plate])
    if lay is not None:
        lay.meta.setdefault("plates", {})["brand"] = [plate]
    return im


def _ambient(backdrop, photo_box, grid=(4, 20), dim=0.6, reach=560):
    """A streak-free colour field from the edge-extended photograph.

    Clamping edge pixels outward repeats each edge column (or row) for the
    whole margin, so any texture along the edge became hard vertical streaks
    (Richat's cloud edge, 2026-11-01). Resampling through a coarse grid keeps
    each region's local colour (sky continues as sky, no ghost subject) while
    removing that column-wise detail. The field then dims towards ink with
    distance from the photograph so the canvas edge reads as depth, not smear.
    """
    import numpy as np
    w, h = backdrop.size
    def smooth(cells):
        coarse = backdrop.convert("RGB").resize(cells, Image.Resampling.BOX)
        out = coarse.resize((w, h), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(64))
        return np.asarray(out, dtype=np.float32)

    near, far = smooth(grid), smooth((3, 6))
    x0, y0, x1, y1 = photo_box
    yy, xx = np.ogrid[:h, :w]
    dx = np.maximum(np.maximum(x0 - xx, xx - (x1 - 1)), 0)
    dy = np.maximum(np.maximum(y0 - yy, yy - (y1 - 1)), 0)
    t = np.clip(np.hypot(dx, dy) / reach, 0, 1)
    t = (t * t * (3 - 2 * t))[..., None]
    # Close to the photograph: its local colours. Further out: a broad wash of
    # the same palette (no column structure left), dimming towards ink.
    blend = np.clip(t * 2.2, 0, 1)
    arr = near + (far - near) * blend
    ink = np.array(brand.INK, dtype=np.float32)
    out = ink + (arr - ink) * (1 - dim * t)
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8")).convert("RGBA")


def _immersive(source, aperture, focus, lay, full_width=False):
    """Scale the reviewed feature into its aperture, retaining photographic context.

    The protected feature remains unshaded. Type may sit on surrounding context,
    but never on the reviewed feature itself. No segmentation or synthesis.
    """
    if source.info.get("essential_full_image"):
        return _photograph(Image.new("RGBA", (1080, 1920), (0, 0, 0, 255)), source, aperture, lay)
    import math
    source = ImageOps.exif_transpose(source).convert("RGBA")
    sw, sh = source.size
    fx0, fy0, fx1, fy1 = [v * (sw if i % 2 == 0 else sh) for i, v in enumerate(focus["focus_box"])]
    x0, y0, x1, y1 = aperture
    scale = min((x1 - x0) / (fx1 - fx0), (y1 - y0) / (fy1 - fy0))
    if full_width:
        # Round 3 (Discovery): the photograph spans the canvas width, never a floating window in dead ink. The
        # reviewed feature still fits its band (clear of the masthead and the type); the cap is lifted to what
        # fills the width.
        fill = 1080 / sw
        scale = min(scale, max(IMMERSIVE_MAX_SCALE, fill), 4096 / max(sw, sh))
        if (fy1 - fy0) * fill <= y1 - y0:   # filling the width still keeps the feature inside its band
            scale = max(scale, fill)
    else:
        scale = min(scale, 2.0, 4096 / max(sw, sh))
    size = (round(sw * scale), round(sh * scale))
    sx, sy = size[0] / sw, size[1] / sh
    left = round((x0 + x1) / 2 - (fx0 + fx1) * sx / 2)
    top = round((y0 + y1) / 2 - (fy0 + fy1) * sy / 2)
    full_width = full_width and size[0] >= 1080   # else (a tall feature) the old feathered window stands
    if full_width:
        left = min(0, max(1080 - size[0], left))   # no ink at either side
    # Extend the same projected photograph's edge colours, not a second crop
    # of the subject. A sky therefore continues into sky without a ghost dome.
    import numpy as np
    photo = source.resize(size, Image.Resampling.LANCZOS)
    xx = np.clip(np.arange(1080) - left, 0, size[0] - 1)
    yy = np.clip(np.arange(1920) - top, 0, size[1] - 1)
    backdrop = np.asarray(photo)[yy[:, None], xx[None, :]]
    im = _ambient(Image.fromarray(backdrop), (left, top, left + size[0], top + size[1]))
    if full_width and top > 0:
        # Above a full-width photograph the canvas continues its own top row (the edge-extended backdrop,
        # unblurred) and eases into the ambient field over FULL_WIDTH_BLEND px, so the photo's top edge is not
        # a hard seam (round 3 review, 2026-11-02: a visible line across Gol Gumbaz's sky and over Richat).
        ys = np.arange(1920, dtype=np.float32)
        ease = np.clip((ys - (top - FULL_WIDTH_BLEND)) / FULL_WIDTH_BLEND, 0, 1)
        ease[ys >= top + size[1]] = 0   # never the clamped rows below the photo (they streak)
        ease = (ease * ease * (3 - 2 * ease))[:, None]
        # Smoothed across (not down): the continued row keeps its colour run but loses the column texture that
        # clamping repeats into vertical streaks.
        raw = Image.fromarray(backdrop).convert("RGB").resize((24, 1920), Image.Resampling.BOX).resize(
            (1080, 1920), Image.Resampling.BICUBIC).convert("RGBA")
        raw.putalpha(Image.fromarray((np.repeat(ease, 1080, axis=1) * 255).astype("uint8")))
        im = Image.alpha_composite(im, raw)
    # Feather just the physical photograph's outer edge into that backdrop.
    # Limit the fade to the margin outside the explicitly reviewed feature.
    clear = 0   # rows of the photograph's top left to the ambient field (the inset window only)
    if full_width:   # only the top edge can end inside the canvas; the shade below does the bottom fade
        margin = max(0, fy0 * sy)
        feather = min(64, int(margin * 0.8))
        if feather > 1:
            mask = np.clip(np.arange(size[1], dtype=np.float32) / feather, 0, 1)[:, None] + np.zeros((1, size[0]))
            photo.putalpha(Image.fromarray((mask * mask * (3 - 2 * mask) * 255).astype("uint8")))
    else:
        # The inset window (a feature too tall to fill the width inside its band). Its top edge stays
        # INSET_CLEAR px under the masthead's foot (r5 review, 2026-11-02: Richat's window started 12 px under
        # RELIC & RIDDLE, so the name sat on the photo's edge); the context above that row is the ambient field.
        # Each edge fades over its own margin outside the feature (one shared minimum gave hard sides).
        clear = max(0, min(fy0 * sy, (_brand_foot() + INSET_CLEAR) - top))
        margins = (fx0 * sx, fy0 * sy - clear, (sw - fx1) * sx, (sh - fy1) * sy)   # left, top, right, bottom
        f = [min(INSET_FEATHER_MAX, int(max(0, m) * 0.8)) for m in margins]
        f[1] = min(INSET_FEATHER_MAX, int(max(0, margins[1]) - 2))   # the top fades over all its room: soft edge
        yy = np.arange(size[1], dtype=np.float32)[:, None] - clear
        xx = np.arange(size[0], dtype=np.float32)[None, :]

        def ramp(dist, width):
            return np.clip(dist / width, 0, 1) if width > 1 else (dist >= 0).astype(np.float32)
        mask = np.minimum(np.minimum(ramp(xx, f[0]), ramp(size[0] - 1 - xx, f[2])),
                          np.minimum(ramp(yy, f[1]), ramp(size[1] - 1 - clear - yy, f[3])))
        photo.putalpha(Image.fromarray((mask * mask * (3 - 2 * mask) * 255).astype("uint8")))
    visible = photo.crop((-left, -top, 1080 - left, 1920 - top))
    im = Image.alpha_composite(im, visible)
    protected = [math.floor(left + fx0 * sx), math.floor(top + fy0 * sy),
                 math.ceil(left + fx1 * sx), math.ceil(top + fy1 * sy)]
    if lay is not None:
        lay.protected.append({"name": "reviewed photographic feature", "box": protected})
        lay.meta["composition"] = {
            "version": DESIGN_VERSION, "mode": "reviewed_focus", "source_size": [sw, sh],
            "source_box": [max(0, -left / sx), max(0, -top / sy),
                           min(sw, (1080 - left) / sx), min(sh, (1920 - top) / sy)],
            "image_box": [max(0, left), max(0, top + clear), min(1080, left + size[0]), min(1920, top + size[1])],
            "focus_box": focus["focus_box"], "protected_box": protected,
            "aperture": list(aperture), "reviewed": focus["reviewed"], "source_sha256": focus["sha256"],
            "full_width": bool(full_width),
        }
    return im


BADGE_FOOT = (40, 0.6, 220)   # Discovery: 40 px under the deck the shade eases from 0.84 to 0.6 over 220 px
IMMERSIVE_MAX_SCALE = 3.0   # Discovery full width: the most a reviewed feature is enlarged
FULL_WIDTH_TOP = 580       # Discovery full width: the reviewed feature's band starts below the masthead's halo
FULL_WIDTH_BLEND = 220      # px above a full-width photo over which its continued top row eases into the field
INSET_CLEAR = 24            # Discovery inset window: px of clear field between the masthead's foot and the
                            # photo's first row (which then fades in to the feature: half strength ~45 px down)
INSET_FEATHER_MAX = 120     # the inset window's widest edge fade (each edge within its own margin)


def _brand_foot():
    """The masthead's ink foot (y) on every style."""
    return BRAND_TOP + get_font("Cinzel", BRAND_PX, weight=700).getbbox(BRAND_TEXT, anchor="lt")[3]


def _photograph(im, source, aperture, lay, bottom=False):
    """Contain the complete photo, with neither distortion nor an invented crop.

    The source may be landscape, portrait or square. Its exact pasted rectangle
    is protected and retained in the redraw recipe. This is a geometric promise,
    not a claim to have detected or classified a monument or person.
    """
    x0, y0, x1, y1 = map(int, aperture)
    source = ImageOps.exif_transpose(source).convert("RGBA")
    if source.info.get("essential_full_image"):
        photo = source.copy()
        photo.thumbnail((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    else:
        photo = ImageOps.contain(source, (x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    x = x0 + (x1 - x0 - photo.width) // 2
    # Bottom-aligned on the paper card (round 3), so the window's foot keeps its set gap to the kicker.
    y = y1 - photo.height if bottom else y0 + (y1 - y0 - photo.height) // 2
    im.alpha_composite(photo, (x, y))
    box = [x, y, x + photo.width, y + photo.height]
    if lay is not None:
        lay.protected.append({"name": "photograph", "box": box})
        lay.meta["composition"] = {
            "version": DESIGN_VERSION, "mode": "contain", "source_size": list(source.size),
            "source_box": [0, 0, source.width, source.height], "image_box": box,
            "aperture": [x0, y0, x1, y1],
        }
    return im


_YEAR = __import__("re").compile(r"(?<![\w.])(1[0-9]{3}|20[0-9]{2})(?![\w])")


def monument_figure(copy):
    """(number, unit, field, span) of the figure a "monument" cover makes its hero, or None.

    The first measured figure in the title lines, the badge or the tagline ("THE 144-FOOT" -> ("144", "FT");
    "THE 25-MILE" -> ("25", "MILES")), else a year in the tagline or eyebrow ("DERINKUYU, 1963" -> ("1963", "")).
    `span` is the figure's place in that field, so the line it came from can be shown without repeating it.
    """
    from .typography import _FIGURE, _figure_ok, _unit
    for field in ("line1", "line2", "badge", "tagline"):
        text = str(copy.get(field) or "")
        for m in _FIGURE.finditer(text):
            info = _unit(m.group("unit"))
            if info and _figure_ok(m):
                return m.group("num") + m.group("plus"), info[3].upper(), field, m.span()
    for field in ("tagline", "eyebrow"):
        m = _YEAR.search(str(copy.get(field) or ""))
        if m:
            return m.group(1), "", field, m.span()
    return None


# Round 3 (2026-11-02): a hero figure carries its hedge, says what it measures, and never turns a disputed or
# ranged figure into one bare number (operator rule: figures are never more precise than the fact sheet).
QUALIFIERS = ("UP TO", "MORE THAN", "AT LEAST", "ABOUT", "ALMOST", "NEARLY", "OVER", "SOME", "AROUND", "ROUGHLY",
              "APPROX.", "APPROXIMATELY", "CIRCA", "EST.", "C.")
_QUAL = "|".join(__import__("re").escape(q) for q in sorted(QUALIFIERS, key=len, reverse=True))
_DIMENSIONS = ("diameter", "across", "wide", "width", "deep", "depth", "tall", "high", "height", "long", "length",
               "thick", "circumference")
_DIM_MODIFIERS = ("external", "internal", "outer", "inner")
_HEDGE_WORDS = ("approximately", "about", "roughly", "almost", "nearly", "some", "up to", "more than", "over",
                "at least", "around", "estimated", "an estimated", "circa")


def figure_qualifier(copy, found):
    """(qualifier, field) for the hero figure `found` (monument_figure's tuple): the qualifier the copy itself
    sets before that number, in the figure's own field first ("UP TO 144-FOOT"), else in another field carrying
    the same number (the badge "EST. 144 FT (44 M)"). ("", "") when the copy states it flat."""
    import re
    if not found:
        return "", ""
    number, field = found[0], found[2]
    pattern = re.compile(r"(?<![\w.])(" + _QUAL + r")\s*" + re.escape(number) + r"(?![\d])", re.IGNORECASE)
    for key in (field, "badge", "line1", "line2", "tagline", "eyebrow"):
        m = pattern.search(str(copy.get(key) or ""))
        if m:
            q = m.group(1).upper()
            return (q if q.endswith(".") or q not in ("EST", "C", "APPROX") else q + "."), key
    return "", ""


# Round 4 (2026-11-02): "EST." on a dimension read as "established", turning a size claim into a founding date
# (eight lenses). An abbreviated hedge on a measured figure is drawn in full, in the sheet's or the copy's own
# words: the claim's hedge word before the figure ("approximately"), else the sheet's hedge lead as one word
# ("by one estimate" -> "ESTIMATED", HEDGE_LEAD_WORDS), else the copy's own word spelled out ("EST." ->
# "ESTIMATED"). "EST." and "C." stay on dates.
ABBREVIATED = {"EST.": "ESTIMATED", "APPROX.": "APPROXIMATELY"}
# r7 (operator default, 2026-11-02): a single-estimate measurement draws its hedge as the one word ESTIMATED, faithful
# to the sheet's "by one estimate"; the three-word phrase could not hold the grid floor on one line, so the
# monument never rendered and the fallback set the hedge as unreadable texture.
HEDGE_LEAD_WORDS = (("estimat", "ESTIMATED"), ("approximat", "APPROXIMATELY"))


def drawn_qualifier(qualifier, unit, lead_word="", hedge=""):
    """The qualifier as a cover draws it: `qualifier` (the copy's, figure_qualifier) unchanged unless it is an
    abbreviation on a measured figure (`unit`), then the claim's own hedge word (`lead_word`), else the sheet's
    hedge lead as its one word (`hedge`: "by one estimate, the central dome ..." -> "ESTIMATED"), else the
    abbreviation spelled out. Never a word that is in neither the copy nor the sheet."""
    import re
    q = str(qualifier or "").upper()
    if not q or not unit or q not in ABBREVIATED:
        return q
    if lead_word:
        return str(lead_word).upper()
    m = re.match(r"\s*([A-Za-z][A-Za-z ]{1,40}?)\s*,\s", str(hedge or ""))
    if m and len(m.group(1).split()) <= 4:
        for stem, word in HEDGE_LEAD_WORDS:
            if stem in m.group(1).lower():
                return word
    return ABBREVIATED[q]


_EVENT_VERB = r"([a-z]+ed|built|found|begun|dug|made|cut|laid|won|lost|struck|hit|seen|known|drawn|written)"


def year_event(year, sheet):
    """(word, fact_id): the fact sheet's own event word for `year` ("accidentally rediscovered in 1963" ->
    "REDISCOVERED"), the verb directly before "in/around/by/during <year>" in a claim, or ("", "")."""
    import re
    if not isinstance(sheet, dict):
        return "", ""
    pattern = re.compile(r"\b" + _EVENT_VERB + r"\s+(?:in|around|by|during)\s+(?:the\s+year\s+)?"
                         + re.escape(str(year)) + r"\b", re.IGNORECASE)
    for fact in sheet.get("facts") or []:
        if isinstance(fact, dict):
            m = pattern.search(str(fact.get("statement") or ""))
            if m:
                return m.group(1).upper(), str(fact.get("id") or "")
    return "", ""


def _numbers(text):
    import re
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text or ""))}


def _dimension_near(text, start, end):
    """The dimension word the claim itself uses nearest to the figure at text[start:end] ("EXTERNAL DIAMETER"),
    from the same clause, or ""."""
    import re
    best = None
    for m in re.finditer(r"\b(" + "|".join(_DIMENSIONS) + r")\b", text, re.IGNORECASE):
        gap = start - m.end() if m.end() <= start else m.start() - end
        clause = text[min(m.end(), start):max(m.start(), end)]
        if gap > 60 or re.search(r"[;.]\s|\band\b", clause):
            continue
        if best is None or gap < best[0]:
            best = (gap, m)
    if best is None:
        return ""
    m = best[1]
    word = m.group(1).upper()
    before = re.search(r"\b(" + "|".join(_DIM_MODIFIERS) + r")\s+$", text[:m.start()], re.IGNORECASE)
    return f"{before.group(1).upper()} {word}" if before else word


def figure_check(copy, sheet=None, payload=None):
    """What the fact sheet says about the copy's hero figure, or None (no measured figure, or no sheet to ask).

    {"number", "unit", "field", "qualifier", "qualifier_from", "fact_id", "dimension", "hedged", "conflict",
    "monument_ok", "reason"}. `conflict` is a figure the sheet gives as a range or marks disputed with another
    value in its hedge (Richat: 40 km, "almost 50 km" from orbit): it is never drawn as one number. `hedged` is
    an estimate (disputed with the same value, or "approximately" in the claim): a hero shows it only with the
    copy's own qualifier. `dimension` is only ever the claim's own word (Gol Gumbaz: "EXTERNAL DIAMETER"); the
    payload's matching stat card label is the fallback source. Nothing here invents a word.
    """
    import re

    from .typography import _FIGURE, _figure_ok, _unit
    found = monument_figure(copy)
    if not found or not isinstance(sheet, dict):
        return None
    if not found[1]:
        # A year hero (round 4): it says what happened in it only with the sheet's own event word, set above it.
        event, fact_id = year_event(found[0], sheet)
        return {"number": found[0], "unit": "", "field": found[2], "kind": "year", "event": event,
                "fact_id": fact_id, "qualifier": "", "qualifier_from": "", "drawn_qualifier": event,
                "dimension": "", "hedged": False, "conflict": False, "monument_ok": False,
                "reason": "" if event else "the fact sheet gives no event word for this year"}
    number, unit, field = found[0], found[1], found[2]
    value = float(number.rstrip("+").replace(",", ""))
    qualifier, q_from = figure_qualifier(copy, found)
    out = {"number": number, "unit": unit, "field": field, "qualifier": qualifier, "qualifier_from": q_from,
           "drawn_qualifier": drawn_qualifier(qualifier, unit), "fact_id": "", "dimension": "", "hedged": False,
           "conflict": False, "monument_ok": False, "reason": ""}
    match = None
    for fact in sheet.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        statement = str(fact.get("statement") or "")
        for m in _FIGURE.finditer(statement):
            info = _unit(m.group("unit"))
            if (info and _figure_ok(m) and info[3].upper() == unit
                    and abs(float(m.group("num").replace(",", "")) - value) < 1e-9):
                match = (fact, statement, m)
                break
        if match:
            break
    if match is None:
        out["reason"] = "the figure is not in the fact sheet in this unit"
        return out
    fact, statement, m = match
    out["fact_id"] = str(fact.get("id") or "")
    # A range in the claim ("8 to 9 kilometres", "94-104"), or a hedge giving another value, is a conflict.
    ranged = (re.search(r"\d[\d,.]*\s*(?:to|-|–|and)\s*$", statement[:m.start()])
              or re.match(r"\s*(?:to|-|–)\s*\d", statement[m.end():]))
    other = _numbers(fact.get("hedge")) - _numbers(statement)
    out["conflict"] = bool(ranged) or bool(fact.get("disputed") and other)
    lead = statement[max(0, m.start() - 28):m.start()].lower()
    lead_word = next((w for w in sorted(_HEDGE_WORDS, key=len, reverse=True)
                      if re.search(r"\b" + re.escape(w) + r"\s+$", lead)), "")
    out["hedged"] = bool(fact.get("disputed")) or bool(lead_word)
    out["drawn_qualifier"] = drawn_qualifier(qualifier, unit, lead_word, fact.get("hedge"))
    out["dimension"] = _dimension_near(statement, m.start(), m.end())
    if not out["dimension"]:
        for card in (payload or {}).get("stat_cards") or []:
            if isinstance(card, dict) and value in _numbers(card.get("value")):
                label = str(card.get("label") or "")
                hit = re.search(r"\b(" + "|".join(_DIMENSIONS) + r")\b", label, re.IGNORECASE)
                if hit:
                    out["dimension"] = _dimension_near(label, hit.start(), hit.start())
                    break
    # r7: the hedge is never texture. Where it is not in a title line, every style sets it at the grid floor
    # (_qualifier_fit, on the narrowest measure, the paper card's); a hedge that cannot be set so withholds the
    # figure on every style (withhold_figure: a figure-free headline), as no qualifier at all does.
    in_title = q_from in ("line1", "line2")
    out["hedge_fits"] = bool(qualifier) and (in_title or _qualifier_fit(out["drawn_qualifier"], HEDGE_MEASURE)
                                             is not None)
    if out["conflict"]:
        out["reason"] = "the sheet gives this figure as a range or disputes its value"
    elif out["hedged"] and not qualifier:
        out["reason"] = "the sheet hedges this figure and the copy carries no qualifier for it"
    elif out["hedged"] and not out["hedge_fits"]:
        out["reason"] = (f"the hedge {out['drawn_qualifier']!r} does not fit one line at profile-grid size "
                         "(the figure is withheld)")
    elif out["drawn_qualifier"] and not in_title and _qualifier_fit(out["drawn_qualifier"]) is None:
        out["reason"] = f"the hedge {out['drawn_qualifier']!r} does not fit one kicker line at profile-grid size"
    elif not out["dimension"]:
        out["reason"] = "the claim does not say what the figure measures"
    else:
        out["monument_ok"] = True
    return out


def must_withhold(figure):
    """True when the hero figure may be shown nowhere (figure_check): a range or disputed value (`conflict`), or
    an estimate whose hedge cannot be shown legibly at grid size (no qualifier, or one that does not fit one line
    at the floor: r7, operator default). A year and an unhedged figure stand."""
    if not figure or figure.get("kind") == "year":
        return False
    if figure.get("conflict"):
        return True
    return bool(figure.get("unit") and figure.get("hedged") and figure.get("fact_id")
                and not figure.get("hedge_fits", True))


def withhold_figure(copy, figure, force=False):
    """The copy with a conflicted figure (figure_check: a range or a disputed value), or an estimate whose hedge
    cannot be shown legibly (must_withhold; `force`: the caller's own floor failed), lifted out of every line
    that states it, with the qualifier in front of it: the cover falls back to a non-numeric headline rather than
    one number the sheet does not give (operator default, round 3). Only payload words remain; none is added."""
    import re

    from .typography import _FIGURE, _figure_ok, _unit
    out = dict(copy)
    if not figure or not figure.get("unit") or not (force or must_withhold(figure)):
        return out
    value = float(str(figure["number"]).rstrip("+").replace(",", ""))
    for key in ("eyebrow", "line1", "line2", "tagline", "badge"):
        text = str(out.get(key) or "")
        for m in reversed(list(_FIGURE.finditer(text))):
            info = _unit(m.group("unit"))
            if info and _figure_ok(m) and abs(float(m.group("num").replace(",", "")) - value) < 1e-9:
                start = m.start()
                q = re.search(r"(?<![\w.])(" + _QUAL + r")\s*$", text[:start], re.IGNORECASE)
                text = _without(text, (q.start() if q else start, m.end()))
        out[key] = text
    return out


def _without(text, span):
    """`text` with the figure at `span` removed, and the article or punctuation it leaves dangling."""
    import re
    out = (text[:span[0]] + text[span[1]:]).strip()
    out = re.sub(r"^(THE|A|AN)\s*$", "", out.strip(), flags=re.IGNORECASE)
    out = re.sub(r"^(THE|A|AN)\s+(?=[,.]|$)", "", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip(" ,;:-")


def _monument(im, copy, lay, tight, source, focus, figure=None):
    """The "monument number" cover (2026-11-01): one measured figure as the hero, in gold Anton across the
    centre square, over the full-bleed photograph; the subject's name below it and the eyebrow above.

    It replaces lower_third in the rotation (lower_third differed from Discovery only in its headline colour).
    The line the figure came from is shown without it ("THE 144-FOOT" is not repeated under "144 FT"), and the
    figure's own field is dropped when nothing else is left in it. A reviewed focus frames the photograph as
    Discovery does; otherwise the whole photograph fills the canvas.

    Round 3 (2026-11-02): the figure keeps the copy's own qualifier ("EST.", "UP TO") as a kicker attached above
    it, large enough to read in the profile grid; the claim's dimension word ("EXTERNAL DIAMETER", from the fact
    sheet via figure_check) sits under the unit; a figure the sheet ranges, disputes or hedges without a
    qualifier is never the hero (Discovery is drawn instead); the shade is a scrim behind the type only.
    """
    found = monument_figure(copy)
    margin, width = 84, 1080 - 2 * 84
    declined = None
    if found is None or (figure and found[1] and not figure.get("monument_ok")):
        declined = (figure or {}).get("reason") or "figure check" if figure else None
    elif figure and not found[1] and figure.get("kind") == "year" and not figure.get("event"):
        # Round 4: a bare year next to a place reads as a founding date; without the sheet's event word for it
        # the monument declines (operator default: the recommended route).
        declined = figure.get("reason") or "the fact sheet gives no event word for this year"
    q_raw, q_from = figure_qualifier(copy, found) if (found and found[1]) else ("", "")
    if found and found[1]:
        qualifier = str((figure or {}).get("drawn_qualifier") or drawn_qualifier(q_raw, found[1])) if q_raw else ""
    elif figure and figure.get("kind") == "year" and figure.get("event"):
        qualifier, q_from = str(figure["event"]), "sheet"
    else:
        qualifier = ""
    q_fit = _qualifier_fit(qualifier, width) if qualifier and declined is None else None
    if qualifier and declined is None and q_fit is None:
        declined = f"the qualifier {qualifier!r} does not fit the measure at profile-grid size"
    if found is None or declined is not None:
        # No figure to make the hero, or one the sheet will not let stand alone: Discovery is the fallback.
        if lay is not None and declined:
            lay.meta["monument_declined"] = declined
        return render_cover(im, withhold_figure(copy, figure), "badge", lay=lay, tight=tight, source=source,
                            focus=focus, figure=figure)
    number, unit, field, span = found
    dimension = str((figure or {}).get("dimension") or "") if unit else ""
    rest = dict(copy)
    if field in ("line1", "line2", "tagline", "eyebrow"):
        cut = span
        if q_raw and q_from == field:   # "UP TO 144-FOOT": the qualifier goes up with the figure
            import re
            q = re.search(r"(?<![\w.])(" + _QUAL + r")\s*$", str(copy[field])[:span[0]], re.IGNORECASE)
            cut = (q.start() if q else span[0], span[1])
        rest[field] = _without(str(copy[field]), cut)
    # The same figure elsewhere in the copy (badge "EST. 144 FT (44 M)" and line1 "THE 144-FOOT") is not
    # repeated under the hero either.
    for other in ("line1", "line2", "tagline"):
        if other != field:
            found_other = monument_figure({other: rest.get(other)})
            if found_other and found_other[0] == number:
                rest[other] = _without(str(rest[other]), found_other[3])
    titles = [(name, display(rest.get(key)).strip().upper()) for name, key in (("title_1", "line1"),
                                                                                ("title_2", "line2"))]
    titles = [(n, t) for n, t in titles if t]
    eyebrow = display(rest.get("eyebrow")).strip().upper()
    tagline = "" if redundant_tagline(rest) else display(rest.get("tagline")).strip().upper()

    measure = ImageDraw.Draw(im)
    factor = 0.88 if tight else 1.0
    from .typography import fit_font, tracked_width
    # The unit block right of the figure: the unit, and under it the claim's dimension word (one or two lines).
    dim_font = get_font("Montserrat", DIM_WORD_PX, weight=700)
    dim_lines = []
    if dimension:
        words = dimension.split()
        one = " ".join(words)
        dim_lines = [one] if len(words) == 1 or tracked_width(one, dim_font, DIM_WORD_TRACK) <= 300 else words[:2]
    dim_w = max([tracked_width(t, dim_font, DIM_WORD_TRACK) for t in dim_lines] or [0])
    reserve = max(240, int(dim_w) + 22) if unit else 0
    # The figure: as large as fits the width, up to 420 px (it is the cover's one idea).
    num_font = fit_font(measure, number, "Anton", int(420 * factor), width - reserve, 160)
    unit_font = get_font("Anton", max(72, int(num_font.size * 0.33)))
    # The qualifier kicker, one line read in the 124 px profile grid (QUALIFIER_GRID_CAP, _qualifier_fit).
    q_lines, q_font, q_track = q_fit if q_fit else ([], None, QUALIFIER_TRACK)
    eyebrow_rows, title_rows, tag_rows = [], [], []
    if eyebrow:
        lines, f_e, _ = _eyebrow_lines(measure, eyebrow, width)
        eyebrow_rows = [(lines[0], f_e, brand.GOLD)]
    if titles:
        # One size for both title lines: the largest at which each fits on one line (down to 64 px), each kept
        # as its own element (title_1, title_2) as in every other style; a line too long even then wraps.
        size = int(104 * factor)
        while size > 64 and any(text_size(measure, t, get_font("Anton", size))[0] > width for _, t in titles):
            size -= 2
        shortened = []
        for name, text in titles:
            lines, f_t, short = fit_text(measure, text, "Anton", size, width, 2, min(size, 64), sentences=False)
            # Two-tone when both title lines are left (2026-11-02): the second in gold, like the figure above it.
            colour = brand.GOLD if name == "title_2" and len(titles) > 1 else brand.BONE
            title_rows += [(name, line, f_t, colour) for line in lines]
            if short:
                shortened.append(name)
        if lay is not None:
            lay.shortened = shortened
    if tagline:
        lines, f_g, _ = _tagline_lines(measure, tagline, width, int(36 * factor))
        tag_rows = [(lines[0], f_g, brand.STONE_LT)]

    num_h = cap_h(num_font)
    q_h = cap_h_of(q_font) + QUALIFIER_GAP if q_font else 0
    # The dimension line marks a measurement; a year or a bare count has no length (operator, 2026-11-02).
    measured_figure = bool(unit)
    gaps = {"eyebrow": 26, "number": 34 + (DIM_ROOM if measured_figure else 0), "title": 10, "tagline": 0}
    # A qualifier under the eyebrow: the two kickers keep EYEBROW_QUALIFIER_GAP of clear field, ink to ink, so they
    # read as two lines (eyebrow, then the figure's own hedge), not one crowded block.
    eyebrow_to_q = None
    if q_font and eyebrow_rows:
        t, f, _ = eyebrow_rows[-1]
        eyebrow_to_q = f.getbbox(t, anchor="lt")[3] + EYEBROW_QUALIFIER_GAP - (text_size(measure, t, f)[1]
                                                                              + gaps["eyebrow"])
        q_h += eyebrow_to_q
    def stack_height():
        return (sum(text_size(measure, t, f)[1] + gaps["eyebrow"] for t, f, _ in eyebrow_rows) + q_h + num_h
                + gaps["number"] + sum(text_size(measure, t, f)[1] + gaps["title"] for _, t, f, _ in title_rows)
                + 14 + sum(text_size(measure, t, f)[1] for t, f, _ in tag_rows))
    top = 1434 - stack_height()
    # The subject sits in the clear band above the type. Round 3: the shade is a scrim confined to the type
    # stack (the photo outside it, and the focus band above it, stay ungraded); each line's own measured halo
    # (place_text) still holds it at contrast wherever the scrim is light.
    im = _full_bleed(source, focus, (440 + top - 120) / 2, lay)
    widths = ([tracked_width(t, f, EYEBROW_TRACK) for t, f, _ in eyebrow_rows]
              + [tracked_width(t, f, TAGLINE_TRACK) for t, f, _ in tag_rows]
              + [text_size(measure, t, f)[0] for _, t, f, _ in title_rows]
              + [text_size(measure, number, num_font)[0] + (reserve if unit else 0)])
    stack_right = min(1080, margin + max(widths))
    # Round 4: the box runs one deck cap height past the last line, so the STONE_LT deck sits on a dark field
    # over a pale facade and does not lean on its halo.
    scrim_foot = 1434 + (cap_h_of(tag_rows[-1][1]) if tag_rows else cap_h_of(get_font("Montserrat", TAGLINE_PX)))
    im = _scrim(im, (margin, top, stack_right, scrim_foot), MONUMENT_SCRIM, MONUMENT_SCRIM_FEATHER)
    if lay is not None:
        lay.meta["scrim"] = {"box": [margin, top, stack_right, scrim_foot], "strength": MONUMENT_SCRIM,
                             "feather": MONUMENT_SCRIM_FEATHER}
    im = _brand(im, lay)

    y = top
    plates = {}

    def put(name, text, font, fill, xy, anchor="lt", target=5.1, track=None):
        nonlocal im
        track = {"eyebrow": EYEBROW_TRACK, "tagline": TAGLINE_TRACK}.get(name, 0) if track is None else track
        xy, anchor = _top_anchor(xy, text, font, anchor, track)
        im, plate = place_text(im, xy, text, font, fill, anchor=anchor, target=target, track=track)
        plates.setdefault(name, []).append(plate)

    for t, f, c in eyebrow_rows:
        put("eyebrow", t, f, c, (margin, y))
        y += text_size(measure, t, f)[1] + gaps["eyebrow"]
    if q_font:
        y += eyebrow_to_q or 0
        put("figure_qualifier", q_lines[0], q_font, brand.GOLD, (margin, y + cap_h_of(q_font)), anchor="ls",
            track=q_track)
        y += q_h - (eyebrow_to_q or 0)
    base = y + num_h
    im = _gold_ground(im, (margin, base - num_h, margin + text_size(measure, number, num_font)[0]
                           + (reserve if unit else 0), base), lay)
    put("figure", number, num_font, brand.GOLD, (margin, base), anchor="ls")
    right = margin + text_size(measure, number, num_font)[0]
    if unit:
        nx = right + 22
        # Unit over the dimension word, the block's last baseline locked to the numeral's.
        dim_pitch = cap_h_of(dim_font) + 12
        unit_base = base - (len(dim_lines) * dim_pitch + 6 if dim_lines else 0)
        put("figure_unit", unit, unit_font, brand.GOLD, (nx, unit_base), anchor="ls")
        block_right = nx + text_size(measure, unit, unit_font)[0]
        for k, t in enumerate(dim_lines):
            yb = base - (len(dim_lines) - 1 - k) * dim_pitch
            put("figure_dim", t, dim_font, brand.STONE_LT, (nx, yb), anchor="ls", target=5.1, track=DIM_WORD_TRACK)
            block_right = max(block_right, nx + tracked_width(t, dim_font, DIM_WORD_TRACK))
        right = round(block_right)
    # The style's signature (2026-11-02): a dimension line under a measured figure, end ticks up, as on a
    # survey drawing. Never under a year or a count: it would claim a length that is not there.
    if measured_figure:
        fig_bottom = max(p.box[3] for n, ps in plates.items() if n.startswith("figure") for p in ps if not p.empty)
        dy = fig_bottom + 30
        d = ImageDraw.Draw(im)
        d.line([(margin, dy), (right, dy)], fill=brand.GOLD + (255,), width=4)
        for xx in (margin, right):
            d.line([(xx, dy - 16), (xx, dy + 2)], fill=brand.GOLD + (255,), width=4)
        if lay is not None:
            lay.add("dimension", (margin, dy - 16, right, dy + 2))
    y = base + gaps["number"]
    for name, t, f, c in title_rows:
        put(name, t, f, c, (margin, y))
        y += text_size(measure, t, f)[1] + gaps["title"]
    y += 14
    for t, f, c in tag_rows:
        put("tagline", t, f, c, (margin, y))
    for name, ps in plates.items():
        record_contrast(lay, name, ps)
    if lay is not None:
        lay.meta.setdefault("plates", {}).update(plates)
        lay.meta["monument"] = {"number": number, "unit": unit, "from": field}
        if qualifier or dimension:
            lay.meta["monument_figure"] = {"qualifier": qualifier, "qualifier_from": q_from,
                                           "dimension": dimension, "copy_qualifier": q_raw}
    return im


MONUMENT_MEASURE = 1080 - 2 * 84   # the monument's type measure
HEDGE_MEASURE = 1080 - 2 * 96     # the narrowest measure a hedge line is set on (the paper card's)
GOLD_HUE_NEAR, GOLD_CHROMA_MIN = 25.0, 25.0   # a ground within 25 deg of GOLD's hue and above C* 25 camouflages it
GOLD_GROUND = (0.4, 0.85, 48)                  # then: 40% desaturated, 0.85 luminance, eased over 48 px


def _qualifier_fit(text, width=MONUMENT_MEASURE):
    """(lines, font, track) for the qualifier kicker on one line at QUALIFIER_GRID_CAP px of cap height in the
    124 px grid: with QUALIFIER_TRACK, else untracked; None when it is wider than `width` even then. Round 4
    (operator default, the synthesis's second route): a hedge phrase that cannot hold the floor on one line
    ("BY ONE ESTIMATE", 1,121 px) declines the monument to Discovery, which sets it on its own line; two
    112 px kicker lines made the hedge the cover's second-largest mark and pushed the subject out of frame."""
    from .typography import tracked_width
    size = 60
    while cap_h_of(get_font(QUALIFIER_FONT, size, weight=QUALIFIER_WEIGHT)) * GRID_SCALE[0] < QUALIFIER_GRID_CAP:
        size += 2
    font = get_font(QUALIFIER_FONT, size, weight=QUALIFIER_WEIGHT)
    for track in (QUALIFIER_TRACK, 0.0):
        if tracked_width(text, font, track) <= width:
            return [text], font, track
    return None


def _lch(rgb):
    """CIE L*, C*, h (degrees) of sRGB rows (N x 3, 0-255)."""
    import numpy as np
    a = np.asarray(rgb, dtype=np.float64) / 255
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    xyz = lin @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    lab_l, lab_a, lab_b = 116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])
    return lab_l, np.hypot(lab_a, lab_b), np.degrees(np.arctan2(lab_b, lab_a)) % 360


def _gold_ground(im, box, lay):
    """Gold-on-gold camouflage gate (round 4): when the photograph behind the gold numeral's box has a median hue
    within GOLD_HUE_NEAR of GOLD's and a chroma above GOLD_CHROMA_MIN (sandstone, a sunlit dome, sand), that
    ground alone is desaturated and pulled darker (GOLD_GROUND), eased at its edge, before the numeral's contrast
    is solved. Elsewhere the photograph is untouched; on other grounds nothing changes."""
    import numpy as np
    x0, y0, x1, y1 = (round(v) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return im
    rgba = im.convert("RGBA")
    patch = np.asarray(rgba.convert("RGB").crop((x0, y0, x1, y1)), dtype=np.float64).reshape(-1, 3)
    _, chroma, hue = _lch(np.median(patch, axis=0)[None, :])
    _, _, gold_hue = _lch(np.array([brand.GOLD], dtype=np.float64))
    near = abs((float(hue[0]) - float(gold_hue[0]) + 180) % 360 - 180) <= GOLD_HUE_NEAR
    hit = bool(near and float(chroma[0]) > GOLD_CHROMA_MIN)
    if lay is not None:
        lay.meta["gold_ground"] = {"chroma": round(float(chroma[0]), 1), "hue": round(float(hue[0]), 1),
                                   "applied": hit}
    if not hit:
        return im
    desat, lum, ease = GOLD_GROUND
    arr = np.asarray(rgba, dtype=np.float32)
    rgb = arr[..., :3]
    grey = (rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32))[..., None]
    graded = (rgb + (grey - rgb) * desat) * lum
    yy, xx = np.ogrid[:im.height, :im.width]
    d = np.maximum(np.maximum(np.maximum(x0 - xx, xx - x1), np.maximum(y0 - yy, yy - y1)), 0).astype(np.float32)
    v = np.clip(1 - d / ease, 0, 1)
    v = (v * v * (3 - 2 * v))[..., None]
    arr[..., :3] = rgb + (graded - rgb) * v
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGBA")


def _full_bleed(source, focus, cy, lay):
    """The photograph filling the 9:16 canvas, its reviewed feature (else its centre) placed at x 540, y `cy`
    as far as the canvas allows: the monument cover is photograph-first behind its type, not an inset."""
    if source.info.get("essential_full_image"):
        # Keep the entire photo in the central profile-grid region as well.
        return _photograph(Image.new("RGBA", (1080, 1920), (0, 0, 0, 255)), source,
                           (0, 420, 1080, 1500), lay)
    src = ImageOps.exif_transpose(source).convert("RGBA")
    sw, sh = src.size
    scale = max(1080 / sw, 1920 / sh)
    w, h = round(sw * scale), round(sh * scale)
    fx, fy = 0.5, 0.5
    if focus:
        b = focus["focus_box"]
        fx, fy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    left = min(0, max(1080 - w, round(540 - fx * w)))
    top = min(0, max(1920 - h, round(cy - fy * h)))
    canvas = Image.new("RGBA", (1080, 1920))
    canvas.paste(src.resize((w, h), Image.Resampling.LANCZOS), (left, top))
    if lay is not None:
        lay.meta["composition"] = {"version": DESIGN_VERSION, "mode": "monument_full_bleed",
                                   "image_box": [left, top, left + w, top + h], "source_size": [sw, sh],
                                   **({"focus_box": focus["focus_box"]} if focus else {})}
    return canvas


def cap_h(font):
    return -font.getbbox("8", anchor="ls")[1]


def render_cover(im, copy, style, center_y=960, lay=None, tight=False, source=None, focus=None, figure=None,
                 illustration=False, reveal=None):
    """Render the three existing choices and the editorial rotation.

    A: a bone-paper archival print, inset image over a compact caption.
    B: immersive discovery photograph, with a dominant gold headline below.
    C: centred cinema title above the photograph.
    Editorial: a wider, quieter photo-first stack with a fine separating rule.

    A/C preserve the full source. B/editorial can frame a manually reviewed
    feature; absent such a record, they also retain the complete photograph.

    `figure` (figure_check, round 3): what the fact sheet says about the copy's hero figure. A conflicted one
    (a range, a disputed value) is lifted out of the headline on every style; the monument draws a figure only
    when the check lets it stand. `illustration`: the hero is a generated illustration, so the window carries
    the on-screen disclosure label (the existing wording, video_composer.RECON_BADGE_LABEL). `reveal` (round 4):
    the script's reveal or takeaway line; a tagline that answers it is dropped (resolving_tagline).
    """
    why = resolving_tagline(copy, reveal)
    if why:
        if lay is not None:
            lay.meta["tagline_dropped"] = {"tagline": copy.get("tagline"), "reason": why}
        copy = dict(copy, tagline="")
    if must_withhold(figure):
        withheld = withhold_figure(copy, figure)
        if lay is not None and withheld != dict(copy):
            lay.meta["figure_withheld"] = {"number": figure.get("number"), "unit": figure.get("unit"),
                                           "reason": figure.get("reason")}
        copy = withheld
    out = _render(im, copy, style, lay, tight, source, focus, figure)
    if illustration:
        out = _provenance(out, lay)
    if lay is not None:
        lay.meta["grid_proof"] = grid_proof(out, lay)
    return out


def _render(im, copy, style, lay, tight, source, focus, figure):
    source = source if source is not None else im
    if style == "monument":
        return _monument(im, copy, lay, tight, source, focus, figure)
    centered = style == "minimal"
    paper = style == "depth_sandwich"
    margin = 96 if paper else 84
    width = 1080 - 2 * margin
    rows, shortened = _rows(im, copy, width, style, tight, figure)
    # Very long generated copy shares the central square with the photograph:
    # reduce the *whole* stack consistently before sacrificing its image space.
    if _height(rows) > 500:
        kept = sum(r["height"] for r in rows if r.get("keep"))
        scale = (500 - sum(r["gap"] for r in rows) - kept) / max(1, sum(r["height"] for r in rows) - kept)
        for row in rows:
            if row.get("keep"):
                continue   # r7: the hedge holds its grid floor (it carries the figure's qualifier)
            # The eyebrow and the subline keep their floors (36 and 32 px): they are what vanished in the grid.
            floor = {"eyebrow": EYEBROW_MIN, "qualifier": EYEBROW_MIN, "tagline": TAGLINE_MIN}.get(row["name"], 24)
            row["font"] = get_font("Anton" if row["name"].startswith("title") else "Montserrat",
                                   max(floor, int(row["font"].size * scale)),
                                   weight=800 if row["name"] in ("eyebrow", "qualifier") else None)
            row["height"] = text_size(ImageDraw.Draw(im), row["text"], row["font"])[1]
    if lay is not None:
        lay.shortened = shortened
    if style in ("badge", "depth_sandwich"):
        # One kicker treatment on both (round 3): ink tracked capitals on a solid gold tab.
        for row in rows:
            if row["name"] == "eyebrow":
                row.update(tab=True, fill=brand.INK, gap=row["gap"] + TAB_PAD[1] + 6)
    height = _height(rows)
    if focus and style in ("badge", "lower_third"):
        top = 1434 - height
        full = style == "badge"
        # The feature's band starts clear of the masthead's measured halo (BRAND_TOP + cap + 40 px halo): at 530
        # the halo washed the reviewed feature's top edge (round 3 review, 2026-11-02).
        im = _immersive(source, (0 if full else 72, FULL_WIDTH_TOP if full else 560, 1080 if full else 1008, top - 100),
                        focus, lay,
                        full_width=full)
        im = _shade(im, top + 10, 1920, strength=0.84 if style == "badge" else 0.78, feather=100,
                    foot=(top + height + BADGE_FOOT[0], BADGE_FOOT[1], BADGE_FOOT[2]) if full else None)
        im = _brand(im, lay)
        return _paint(im, rows, margin, top, False, lay)
    if paper:
        # Round 3: the photograph appears once, inside the card, on a flat ink mat (no blurred continuation of
        # the same photograph above and below it).
        im = Image.new("RGBA", im.size, brand.INK + (255,))
        ImageDraw.Draw(im).rectangle(_sandwich_card(), fill=brand.BONE + (255,))
    else:
        # A quiet continuation of the photograph outside the profile crop, fading
        # into an ink ground behind the deliberately arranged central composition.
        im = _shade(im, 0, im.height, strength=70 / 255, feather=1)
        im = _shade(im, 420, 1500, strength=1.0, feather=180)
        # Ink is an editorial ground, not a colour grade applied to the photograph.
        ImageDraw.Draw(im).rectangle((0, 420, 1079, 1499), fill=brand.INK + (255,))
    im = _brand(im, lay, centered, paper)
    if style == "minimal":
        # Measured ink to ink (2026-11-02 round 2): the masthead's foot to the eyebrow's caps MINIMAL_GAPS[0], the
        # eyebrow's foot to the title's caps MINIMAL_GAPS[1]. Rows are placed by their ascender line, so each gap
        # is corrected by how far the next line's ink starts below it.
        from itertools import pairwise

        def off(r):
            return r["font"].getbbox(r["text"], anchor="lt")[1]
        brand_foot = BRAND_TOP + get_font("Cinzel", BRAND_PX, weight=700).getbbox(BRAND_TEXT, anchor="lt")[3]
        top = brand_foot + MINIMAL_GAPS[0] - (off(rows[0]) if rows else 0)
        for a, b in pairwise(rows):
            if a["name"] == "eyebrow" and b["name"] != "eyebrow":
                a["gap"] = MINIMAL_GAPS[1] - off(b) + off(a)
        height = _height(rows)
        photo_top = top + height + 40
        aperture = (60, photo_top, 1020, 1456)
    elif paper:
        # One internal grid on the card (round 3): the window, kicker, headline and tagline share x 96-984; the
        # window's foot to the kicker tab is twice the kicker-to-headline gap; the room saved goes to the window.
        top = SANDWICH_FOOT - height
        kicker = next((r for r in rows if r["name"] == "eyebrow"), None)
        tab_top = top - TAB_PAD[1] if kicker else top
        gap = 2 * (kicker["gap"] - TAB_PAD[1] if kicker else 22)
        aperture = (margin, SANDWICH_WINDOW_TOP, 1080 - margin, tab_top - gap)
    else:
        top = 1434 - height
        image_margin = 0 if style == "badge" else 48
        aperture = (image_margin, 528, 1080 - image_margin, top - 40)
    if paper and focus:
        im = _window(im, source, aperture, focus, lay)
    else:
        im = _photograph(im, source, aperture, lay, bottom=paper)
    if paper and lay is not None:
        lay.meta["sandwich"] = {"aperture": list(aperture), "card": list(_sandwich_card())}
    if style == "lower_third":
        y = top - 21
        right = 1080 - margin
        ImageDraw.Draw(im).line((margin, y, right, y), fill=brand.GOLD_DIM + (255,), width=1)
        if lay is not None:
            lay.add("rule", (margin, y, right, y + 1))
    return _paint(im, rows, 540 if centered else margin, top, centered, lay)


SANDWICH_WINDOW_TOP = 520   # the paper card's window starts under the masthead (12 px under its ink; round 4:
                            # 4 px of it went to the deck's 32 px card foot, so the window keeps half the card)
SANDWICH_CARD_FOOT = 1479   # the card's own foot (inside the 1:1 square, y 420-1500)
SANDWICH_FOOT = SANDWICH_CARD_FOOT - 32   # the card's type ends here: >= 32 px of card under the deck (round 4)
SANDWICH_INSET = 36         # the card's side inset to its type (x 60 -> 96); the masthead keeps it at the top too


def _sandwich_card():
    """The paper card's rectangle. Round 4: its top sits SANDWICH_INSET above the masthead's cap line (it was
    ~22 px, the visible cramp), never above the 1:1 square's top (y 420); the masthead itself stays locked."""
    return (60, max(420, BRAND_TOP - SANDWICH_INSET), 1019, SANDWICH_CARD_FOOT)


def _window_scale(aw, ah, sw, sh, fw, fh):
    """The paper window's scale: cover the aperture (aw x ah) where the reviewed feature (fw x fh source px) still
    fits inside it, never past 2x the source pixels (as _immersive), never under complete containment."""
    contain = min(aw / sw, ah / sh)
    return max(contain, min(max(aw / sw, ah / sh), aw / fw, ah / fh, 2.0))


def _window(im, source, aperture, focus, lay):
    """The paper card's window filled by a reviewed photograph (round 4): scaled to cover the full aperture
    (x 96-984) as far as the reviewed feature still fits inside it (the protecting scale Discovery uses, capped at
    2x), the feature centred across and the photograph's foot on the aperture's foot (the kicker gap). Where the
    feature needs a smaller scale the window is the photograph's own extent, bottom-aligned. Unreviewed photographs
    keep the complete-photograph containment (_photograph)."""
    if source.info.get("essential_full_image"):
        return _photograph(im, source, aperture, lay, bottom=True)
    import math
    x0, y0, x1, y1 = map(int, aperture)
    src = ImageOps.exif_transpose(source).convert("RGBA")
    sw, sh = src.size
    fx0, fy0, fx1, fy1 = [v * (sw if i % 2 == 0 else sh) for i, v in enumerate(focus["focus_box"])]
    scale = _window_scale(x1 - x0, y1 - y0, sw, sh, fx1 - fx0, fy1 - fy0)
    w, h = max(1, round(sw * scale)), max(1, round(sh * scale))
    left = round((x0 + x1) / 2 - (fx0 + fx1) / 2 * scale)
    left = min(x0, max(x1 - w, left)) if w >= x1 - x0 else x0 + (x1 - x0 - w) // 2
    top = round((y0 + y1) / 2 - (fy0 + fy1) / 2 * scale)
    if h >= y1 - y0:
        top = min(y0, max(y1 - h, top))
        # the feature's foot never below the window's: shift up only as far as the photograph allows
        top = max(y1 - h, min(top, y1 - math.ceil(fy1 * scale)))
    else:
        top = y1 - h
    photo = src.resize((w, h), Image.Resampling.LANCZOS)
    box = [max(x0, left), max(y0, top), min(x1, left + w), min(y1, top + h)]
    im.alpha_composite(photo.crop((box[0] - left, box[1] - top, box[2] - left, box[3] - top)), (box[0], box[1]))
    protected = [math.floor(left + fx0 * scale), math.floor(top + fy0 * scale),
                 math.ceil(left + fx1 * scale), math.ceil(top + fy1 * scale)]
    protected = [max(box[0], protected[0]), max(box[1], protected[1]), min(box[2], protected[2]),
                 min(box[3], protected[3])]
    if lay is not None:
        lay.protected.append({"name": "reviewed photographic feature", "box": protected})
        lay.meta["composition"] = {
            "version": DESIGN_VERSION, "mode": "reviewed_window", "source_size": [sw, sh],
            "source_box": [(box[0] - left) / scale, (box[1] - top) / scale, (box[2] - left) / scale,
                           (box[3] - top) / scale],
            "image_box": box, "focus_box": focus["focus_box"], "protected_box": protected,
            "aperture": [x0, y0, x1, y1], "reviewed": focus.get("reviewed", ""),
            "source_sha256": focus.get("sha256", ""),
        }
    return im


def sandwich_fill(copy, focus, source_size, figure=None):
    """The share (0-1) of the paper card's window the reviewed feature fills once placed there (_window), from
    the copy's own stack height; None without a reviewed focus. cover_selector reads it (round 4: a small subject
    on a flat ceiling made the weakest grid tile)."""
    if not focus or not source_size:
        return None
    im = Image.new("RGBA", (1080, 1920))
    rows, _ = _rows(im, copy, 1080 - 2 * 96, "depth_sandwich", False, figure)
    for row in rows:
        if row["name"] == "eyebrow":
            row["gap"] += TAB_PAD[1] + 6
    height = _height(rows)
    kicker = next((r for r in rows if r["name"] == "eyebrow"), None)
    top = SANDWICH_FOOT - height
    tab_top = top - TAB_PAD[1] if kicker else top
    gap = 2 * (kicker["gap"] - TAB_PAD[1] if kicker else 22)
    ap = (96, SANDWICH_WINDOW_TOP, 984, tab_top - gap)
    aw, ah = ap[2] - ap[0], ap[3] - ap[1]
    if aw <= 0 or ah <= 0:
        return 0.0
    sw, sh = source_size
    b = focus["focus_box"]
    fw, fh = (b[2] - b[0]) * sw, (b[3] - b[1]) * sh
    scale = _window_scale(aw, ah, sw, sh, fw, fh)
    window = min(aw, sw * scale) * min(ah, sh * scale)
    return round(min(1.0, fw * scale * fh * scale / window), 3) if window else 0.0


def _provenance(im, lay):
    """The on-screen disclosure over a generated hero illustration: the existing wording
    (video_composer.RECON_BADGE_LABEL), IBM Plex Mono in STONE_LT on an INK plate at the window's lower-left edge,
    inside the profile-grid square, measured like every other line."""
    from .video_composer import RECON_BADGE_LABEL
    comp = (lay.meta.get("composition") if lay is not None else None) or {}
    box = comp.get("image_box") or [84, 560, 996, 1100]
    x0, y1 = max(84, int(box[0]) + 24), min(1460, int(box[3]) - 24)
    font = get_font(brand.DATA_FONT, PROVENANCE_PX, weight=600)
    from .typography import tracked_width
    w = tracked_width(RECON_BADGE_LABEL, font, 1.0)
    cap = cap_h_of(font)
    plate = (x0, y1 - cap - 2 * PROVENANCE_PAD[1], x0 + w + 2 * PROVENANCE_PAD[0], y1)
    im = im.convert("RGBA")
    ImageDraw.Draw(im).rectangle(plate, fill=brand.INK + (255,))
    im, p = place_text(im, (x0 + PROVENANCE_PAD[0], y1 - PROVENANCE_PAD[1]), RECON_BADGE_LABEL, font,
                       brand.STONE_LT, anchor="ls", target=5.1, track=1.0, halo_px=0)
    if lay is not None:
        # Kept in the renderer's record, not as a layout element: it sits on the photograph on purpose (the
        # window it labels), which the layout check would otherwise report as an overlap.
        lay.meta.setdefault("plates", {})["provenance"] = [p]
        lay.meta["provenance"] = {"label": RECON_BADGE_LABEL, "plate": [round(v) for v in plate],
                                  "contrast": p.contrast}
    return im


PROVENANCE_PX, PROVENANCE_PAD = 30, (14, 10)

# The profile-grid proof (round 3): which recorded elements must carry the hook at grid size, and their floors in
# px of cap height at 124 px wide. Texture elements (eyebrow, tagline, dimension word, disclosure) may go small,
# but must never be the only carrier of a fact-bearing word (a qualifier, a unit, a date).
# r7: the hedge line ("qualifier") is a hook like the monument's kicker: it carries the figure's qualifier.
GRID_HOOK = {"title_1": 9, "title_2": 9, "figure": 9, "figure_unit": 9, "figure_qualifier": 9,
             "qualifier": QUALIFIER_GRID_CAP, "brand": 5}
GRID_TEXTURE = ("eyebrow", "tagline", "figure_dim", "provenance")
CROP_34 = (0, 240, 1080, 1680)


def grid_proof(im, lay):
    """The cover as the profile grid shows it: each recorded text element's cap height in px at 124 and 180 px
    wide (the centred 3:4 crop), its contrast measured on the downscaled tile, whether it stays inside the crop,
    the tile's mean L* (colour-balance QA reads it), and what fails: a hook element under its floor or outside the
    crop, or a fact-bearing word carried only by texture."""
    import re

    import numpy as np

    from .typography import _FIGURE
    plates = lay.meta.get("plates", {}) if lay is not None else {}
    crop = im.convert("RGB").crop(CROP_34)
    small = crop.resize((GRID_WIDTHS[0], round(GRID_WIDTHS[0] * 4 / 3)), Image.Resampling.LANCZOS)
    blurred = Image.new("RGB", im.size)
    blurred.paste(small.resize(crop.size, Image.Resampling.BICUBIC), CROP_34[:2])
    elements, fails, hook_fails = {}, [], []
    for name, ps in plates.items():
        ps = [p for p in ps if not p.empty]
        if not ps:
            continue
        cap = min(cap_h_of(p.font) for p in ps)
        inside = all(CROP_34[1] <= p.box[1] and p.box[3] <= CROP_34[3] for p in ps)
        vals = [v for v in (p.measure(blurred) for p in ps) if v is not None]
        kind = "hook" if name in GRID_HOOK else "texture"
        elements[name] = {"class": kind, "cap_124": round(cap * GRID_SCALE[0], 1),
                          "cap_180": round(cap * GRID_SCALE[1], 1), "inside_34": inside,
                          "contrast_124": round(min(vals), 2) if vals else None,
                          "text": " ".join(p.text for p in ps)}
        if kind == "hook":
            found = []
            if cap * GRID_SCALE[0] < GRID_HOOK[name]:
                found.append(f"{name} {cap * GRID_SCALE[0]:.1f} px cap at 124 px (floor {GRID_HOOK[name]})")
            if not inside:
                found.append(f"{name} outside the 3:4 crop")
            fails += found
            hook_fails += found
    hook_text = " ".join(e["text"] for e in elements.values() if e["class"] == "hook").upper()

    def facts(text):
        # A qualifier is fact-bearing only attached to a figure ("OVER 4,000", "EST. 144"): as a plain word
        # ("LOOKING OVER THE CITY", "SOME SAY") it is texture like any other (r5 review: false grid warnings).
        out = {m.group(1).upper() for m in re.finditer(r"(?<![\w.])(" + _QUAL + r")\s*(?=\d)", text, re.IGNORECASE)}
        out |= {m.group(0).upper() for m in _FIGURE.finditer(text)}
        out |= set(_YEAR.findall(text))
        return out
    for name, e in elements.items():
        if e["class"] == "texture" and name != "provenance":
            alone = {w for w in facts(e["text"]) if w not in hook_text}
            if alone:
                fails.append(f"{name} alone carries {', '.join(sorted(alone))} (texture at grid size)")
    arr = np.asarray(small, dtype=np.float32) / 255
    lin = np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)
    y = (lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)).mean()
    lstar = 116 * (y ** (1 / 3) if y > 0.008856 else 7.787 * y + 16 / 116) - 16
    # hook_fails (round 4): the failures the engine acts on (a tighter draw, then another fitting style); a fact
    # carried only by texture stays a warning.
    return {"ok": not fails, "fails": fails, "hook_fails": hook_fails, "elements": elements,
            "mean_lstar": round(float(lstar), 1)}


def delivered_contrast(lay, path):
    """Measure the actual saved JPEG, including compression, before saving layout."""
    from .typography import remeasure
    with Image.open(path) as saved:
        remeasure(lay, saved.convert("RGB"), lay.meta.get("plates", {}))
