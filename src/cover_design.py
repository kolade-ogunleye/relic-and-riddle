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


def _shade(im, top, bottom, strength=0.78, feather=280):
    """Neutral, smoothly feathered shade; never re-grade the photograph's hue."""
    import numpy as np

    y = np.arange(im.height, dtype=np.float32)
    distance = np.maximum(np.maximum(top - y, y - bottom), 0)
    v = np.clip(1 - distance / feather, 0, 1)
    v = v * v * (3 - 2 * v)
    alpha = Image.fromarray((v * strength * 255).astype("uint8")[:, None]).resize(im.size)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 255))
    layer.putalpha(alpha)
    return Image.alpha_composite(im.convert("RGBA"), layer)


EYEBROW_PX, EYEBROW_MIN, EYEBROW_TRACK = 40, 36, 4.0   # Montserrat 800 tracked capitals, every style (2026-11-02)
TAGLINE_PX, TAGLINE_MIN = 38, 32                        # never under 32 px: at 24-30 it vanished in the grid
_STOP = {"THE", "A", "AN", "OF", "IN", "AND", "ON", "AT", "TO", "BY"}


def display(text):
    """Display copy as drawn: three full stops become one true ellipsis (U+2026), as a shortened line ends."""
    import re
    return re.sub(r"\s*\.{3}", "\u2026", str(text or ""))


def redundant_tagline(copy):
    """True when every word of the tagline already stands in the headline or the eyebrow (a subline that
    repeats the place adds a line, not information, and is omitted)."""
    import re
    def words(t):
        return {w for w in re.findall(r"[A-Z0-9']+", str(t or "").upper()) if w not in _STOP}
    tag = words(copy.get("tagline"))
    return bool(tag) and tag <= words(" ".join(str(copy.get(k) or "") for k in ("line1", "line2", "eyebrow")))


def _eyebrow_lines(measure, text, width, size=EYEBROW_PX):
    """The eyebrow on one line in Montserrat 800 with EYEBROW_TRACK between letters, never under EYEBROW_MIN
    (shortened at a word with an ellipsis before it shrinks further). Returns (lines, font, shortened)."""
    room = width - EYEBROW_TRACK * max(0, len(text) - 1)
    return fit_text(measure, text, "Montserrat", max(EYEBROW_MIN, size), int(room), 1, EYEBROW_MIN,
                    sentences=False, weight=800)


def _rows(im, copy, width, style, tight):
    """Fit deliberate headline hierarchy, retaining all four supplied fields."""
    measure = ImageDraw.Draw(im)
    factor = 0.88 if tight else 1
    sizes = {
        "depth_sandwich": (118, 128),
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
    for name, key, face, size, minimum, colour, gap in specs:
        text = display(copy.get(key)).strip().upper()
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
            size = max(minimum, int(size * factor))
        else:
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
            row["fill"] = brand.INK if row["name"] != "tagline" else brand.INK2
    return rows, shortened


def _height(rows):
    return sum(r["height"] + r["gap"] for r in rows)


TAB_PAD = (24, 14)   # Discovery's eyebrow tab: horizontal, vertical padding
DIM_ROOM = 36        # Monument: the dimension line's room under the figure


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
        im, plate = place_text(im, (x, top), row["text"], row["font"], row["fill"],
                               anchor="mt" if centered else "lt", target=5.1, track=row.get("track", 0),
                               halo_px=0 if row.get("tab") else None)
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


def _brand(im, lay, centered=False, paper=False):
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
    x0 = round(540 - group / 2)
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


def _immersive(source, aperture, focus, lay):
    """Scale the reviewed feature into its aperture, retaining photographic context.

    The protected feature remains unshaded. Type may sit on surrounding context,
    but never on the reviewed feature itself. No segmentation or synthesis.
    """
    import math
    source = ImageOps.exif_transpose(source).convert("RGBA")
    sw, sh = source.size
    fx0, fy0, fx1, fy1 = [v * (sw if i % 2 == 0 else sh) for i, v in enumerate(focus["focus_box"])]
    x0, y0, x1, y1 = aperture
    scale = min((x1 - x0) / (fx1 - fx0), (y1 - y0) / (fy1 - fy0))
    scale = min(scale, 2.0, 4096 / max(sw, sh))
    size = (round(sw * scale), round(sh * scale))
    sx, sy = size[0] / sw, size[1] / sh
    left = round((x0 + x1) / 2 - (fx0 + fx1) * sx / 2)
    top = round((y0 + y1) / 2 - (fy0 + fy1) * sy / 2)
    # Extend the same projected photograph's edge colours, not a second crop
    # of the subject. A sky therefore continues into sky without a ghost dome.
    import numpy as np
    photo = source.resize(size, Image.Resampling.LANCZOS)
    xx = np.clip(np.arange(1080) - left, 0, size[0] - 1)
    yy = np.clip(np.arange(1920) - top, 0, size[1] - 1)
    backdrop = np.asarray(photo)[yy[:, None], xx[None, :]]
    im = _ambient(Image.fromarray(backdrop), (left, top, left + size[0], top + size[1]))
    # Feather just the physical photograph's outer edge into that backdrop.
    # Limit the fade to the margin outside the explicitly reviewed feature.
    margin = max(0, min(fx0 * sx, fy0 * sy, (sw - fx1) * sx, (sh - fy1) * sy))
    feather = min(64, int(margin * 0.8))
    if feather > 1:
        yy, xx = np.ogrid[:size[1], :size[0]]
        edge = np.minimum(np.minimum(xx, size[0] - 1 - xx), np.minimum(yy, size[1] - 1 - yy))
        mask = np.clip(edge / feather, 0, 1)
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
            "image_box": [max(0, left), max(0, top), min(1080, left + size[0]), min(1920, top + size[1])],
            "focus_box": focus["focus_box"], "protected_box": protected,
            "aperture": list(aperture), "reviewed": focus["reviewed"], "source_sha256": focus["sha256"],
        }
    return im


def _photograph(im, source, aperture, lay):
    """Contain the complete photo, with neither distortion nor an invented crop.

    The source may be landscape, portrait or square. Its exact pasted rectangle
    is protected and retained in the redraw recipe. This is a geometric promise,
    not a claim to have detected or classified a monument or person.
    """
    x0, y0, x1, y1 = map(int, aperture)
    source = ImageOps.exif_transpose(source).convert("RGBA")
    photo = ImageOps.contain(source, (x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    x = x0 + (x1 - x0 - photo.width) // 2
    y = y0 + (y1 - y0 - photo.height) // 2
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


def _without(text, span):
    """`text` with the figure at `span` removed, and the article or punctuation it leaves dangling."""
    import re
    out = (text[:span[0]] + text[span[1]:]).strip()
    out = re.sub(r"^(THE|A|AN)\s*$", "", out.strip(), flags=re.IGNORECASE)
    out = re.sub(r"^(THE|A|AN)\s+(?=[,.]|$)", "", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip(" ,;:-")


def _monument(im, copy, lay, tight, source, focus):
    """The "monument number" cover (2026-11-01): one measured figure as the hero, in gold Anton across the
    centre square, over the full-bleed photograph; the subject's name below it and the eyebrow above.

    It replaces lower_third in the rotation (lower_third differed from Discovery only in its headline colour).
    The line the figure came from is shown without it ("THE 144-FOOT" is not repeated under "144 FT"), and the
    figure's own field is dropped when nothing else is left in it. A reviewed focus frames the photograph as
    Discovery does; otherwise the whole photograph fills the canvas.
    """
    found = monument_figure(copy)
    if found is None:  # no figure to make the hero: Discovery is the fallback (cover_selector never picks this)
        return render_cover(im, copy, "badge", lay=lay, tight=tight, source=source, focus=focus)
    number, unit, field, span = found
    rest = dict(copy)
    if field in ("line1", "line2", "tagline", "eyebrow"):
        rest[field] = _without(str(copy[field]), span)
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

    margin, width = 84, 1080 - 2 * 84
    measure = ImageDraw.Draw(im)
    factor = 0.88 if tight else 1.0
    # The figure: as large as fits the width, up to 330 px (it is the cover's one idea).
    from .typography import fit_font
    num_font = fit_font(measure, number, "Anton", int(420 * factor), width - (240 if unit else 0), 160)
    unit_font = get_font("Anton", max(72, int(num_font.size * 0.33)))
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
        lines, f_g, _ = fit_text(measure, tagline, "Montserrat", max(TAGLINE_MIN, int(36 * factor)), width, 1,
                                 TAGLINE_MIN, sentences=False)
        tag_rows = [(lines[0], f_g, brand.STONE_LT)]

    num_h = cap_h(num_font)
    gaps = {"eyebrow": 26, "number": 34 + DIM_ROOM, "title": 10, "tagline": 0}
    stack = (sum(text_size(measure, t, f)[1] + gaps["eyebrow"] for t, f, _ in eyebrow_rows) + num_h + gaps["number"]
             + sum(text_size(measure, t, f)[1] + gaps["title"] for _, t, f, _ in title_rows) + 14
             + sum(text_size(measure, t, f)[1] for t, f, _ in tag_rows))
    top = 1434 - stack
    # The subject sits in the clear band above the type, and the shade starts just above the stack: a deeper
    # fade (feather 300 from 40 px above) dimmed the dome and the eye themselves. The type's own measured halo
    # (place_text) keeps it at contrast wherever the shade is lighter.
    im = _full_bleed(source, focus, (440 + top - 120) / 2, lay)
    im = _shade(im, top - 10, 1920, strength=0.86, feather=170)
    im = _brand(im, lay)

    y = top
    plates = {}

    def put(name, text, font, fill, xy, anchor="lt", target=5.1):
        nonlocal im
        im, plate = place_text(im, xy, text, font, fill, anchor=anchor, target=target,
                               track=EYEBROW_TRACK if name == "eyebrow" else 0)
        plates.setdefault(name, []).append(plate)

    for t, f, c in eyebrow_rows:
        put("eyebrow", t, f, c, (margin, y))
        y += text_size(measure, t, f)[1] + gaps["eyebrow"]
    base = y + num_h
    put("figure", number, num_font, brand.GOLD, (margin, base), anchor="ls")
    right = margin + text_size(measure, number, num_font)[0]
    if unit:
        nx = right + 22
        put("figure_unit", unit, unit_font, brand.GOLD, (nx, base), anchor="ls")
        right = nx + text_size(measure, unit, unit_font)[0]
    # The style's signature (2026-11-02): a dimension line under the figure, end ticks up, as on a survey drawing.
    fig_bottom = max(p.box[3] for ps in plates.values() for p in ps if not p.empty)
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
    return im


def _full_bleed(source, focus, cy, lay):
    """The photograph filling the 9:16 canvas, its reviewed feature (else its centre) placed at x 540, y `cy`
    as far as the canvas allows: the monument cover is photograph-first behind its type, not an inset."""
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


def render_cover(im, copy, style, center_y=960, lay=None, tight=False, source=None, focus=None):
    """Render the three existing choices and the editorial rotation.

    A: a bone-paper archival print, inset image over a compact caption.
    B: immersive discovery photograph, with a dominant gold headline below.
    C: centred cinema title above the photograph.
    Editorial: a wider, quieter photo-first stack with a fine separating rule.

    A/C preserve the full source. B/editorial can frame a manually reviewed
    feature; absent such a record, they also retain the complete photograph.
    """
    source = source if source is not None else im
    if style == "monument":
        return _monument(im, copy, lay, tight, source, focus)
    centered = style == "minimal"
    margin = 96 if style == "depth_sandwich" else 84
    width = 1080 - 2 * margin
    rows, shortened = _rows(im, copy, width, style, tight)
    # Very long generated copy shares the central square with the photograph:
    # reduce the *whole* stack consistently before sacrificing its image space.
    if _height(rows) > 500:
        scale = (500 - sum(r["gap"] for r in rows)) / sum(r["height"] for r in rows)
        for row in rows:
            # The eyebrow and the subline keep their floors (36 and 32 px): they are what vanished in the grid.
            floor = {"eyebrow": EYEBROW_MIN, "tagline": TAGLINE_MIN}.get(row["name"], 24)
            row["font"] = get_font("Anton" if row["name"].startswith("title") else "Montserrat",
                                   max(floor, int(row["font"].size * scale)),
                                   weight=800 if row["name"] == "eyebrow" else None)
            row["height"] = text_size(ImageDraw.Draw(im), row["text"], row["font"])[1]
    if lay is not None:
        lay.shortened = shortened
    height = _height(rows)
    if style == "badge":
        for row in rows:
            if row["name"] == "eyebrow":
                row.update(tab=True, fill=brand.INK, gap=row["gap"] + TAB_PAD[1] + 6)
    height = _height(rows)
    paper = style == "depth_sandwich"
    if focus and style in ("badge", "lower_third"):
        top = 1434 - height
        im = _immersive(source, (72, 560, 1008, top - 100), focus, lay)
        im = _shade(im, top + 10, 1920, strength=0.84 if style == "badge" else 0.78, feather=100)
        im = _brand(im, lay)
        return _paint(im, rows, margin, top, False, lay)
    # A quiet continuation of the photograph outside the profile crop, fading
    # into an ink ground behind the deliberately arranged central composition.
    im = _shade(im, 0, im.height, strength=70 / 255, feather=1)
    im = _shade(im, 420, 1500, strength=1.0, feather=180)
    # Ink is an editorial ground, not a colour grade applied to the photograph.
    ImageDraw.Draw(im).rectangle((0, 420, 1079, 1499), fill=brand.INK + (255,))
    if paper:
        ImageDraw.Draw(im).rectangle((60, 440, 1019, 1479), fill=brand.BONE + (255,))
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
    else:
        top = 1434 - height
        image_margin = 84 if style == "depth_sandwich" else (0 if style == "badge" else 48)
        aperture = (image_margin, 528, 1080 - image_margin, top - 40)
    im = _photograph(im, source, aperture, lay)
    if style in ("depth_sandwich", "lower_third"):
        y = top - 21
        right = 1080 - margin
        ImageDraw.Draw(im).line((margin, y, right, y), fill=brand.GOLD_DIM + (255,), width=1)
        if lay is not None:
            lay.add("rule", (margin, y, right, y + 1))
    return _paint(im, rows, 540 if centered else margin, top, centered, lay)


def delivered_contrast(lay, path):
    """Measure the actual saved JPEG, including compression, before saving layout."""
    from .typography import remeasure
    with Image.open(path) as saved:
        remeasure(lay, saved.convert("RGB"), lay.meta.get("plates", {}))
