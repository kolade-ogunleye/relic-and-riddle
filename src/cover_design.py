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
    specs = [
        ("eyebrow", "eyebrow", brand.DATA_FONT if style == "depth_sandwich" else "Montserrat",
         36, 28, brand.GOLD, 22),
        ("title_1", "line1", "Anton", sizes[0], 64, brand.BONE, 10),
        ("title_2", "line2", "Anton", sizes[1], 64,
         brand.GOLD if style == "badge" else brand.BONE, 26),
        ("tagline", "tagline", "Montserrat", 38, 30, brand.STONE_LT, 0),
    ]
    rows, shortened = [], []
    for name, key, face, size, minimum, colour, gap in specs:
        text = str(copy.get(key) or "").strip().upper()
        if not text:
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
                                     min(size, minimum), sentences=False,
                                     weight=800 if name == "eyebrow" and face == "Montserrat" else None)
        if short:
            shortened.append(name)
        for i, line in enumerate(lines):
            rows.append(dict(name=name, text=line, font=font, fill=colour,
                             height=text_size(measure, line, font)[1],
                             gap=gap if i == len(lines) - 1 else 10))
    return rows, shortened


def _height(rows):
    return sum(r["height"] + r["gap"] for r in rows)


def _paint(im, rows, x, top, centered, lay):
    plates = {}
    for row in rows:
        im, plate = place_text(im, (x, top), row["text"], row["font"], row["fill"],
                               anchor="mt" if centered else "lt", target=5.1)
        plates.setdefault(row["name"], []).append(plate)
        top += row["height"] + row["gap"]
    for name, ps in plates.items():
        record_contrast(lay, name, ps)
    if lay is not None:
        lay.meta.setdefault("plates", {}).update(plates)
    return im


def _brand(im, lay, centered=False, paper=False):
    """The masthead, drawn at a measured contrast like every other cover line.

    On the paper ground (a known light, flat field) it is ink. On photographs it
    is always bone with the solved ink halo (brand.PLACE_TARGET), matching the
    carousel header: a mean-luminance switch between ink and bone, with the halo
    disabled, left the masthead unreadable on mid-tone or busy photographs.
    """
    xy = (540 if centered else 84, 462)
    anchor = "mt" if centered else "lt"
    font = get_font("Cinzel", 32)
    if paper:
        im, plate = place_text(im, xy, "RELIC & RIDDLE", font, brand.INK,
                               anchor=anchor, track=2.0, halo_px=0, shadow=False)
    else:
        # 32 px Cinzel is a thin face: a 40 px halo allowed to reach full strength is what clears
        # brand.PLACE_TARGET on a pure-white sky; the 0.35 floor keeps busy texture off the glyph edges.
        plate = plan_text(im, xy, "RELIC & RIDDLE", font, brand.BONE, anchor, 2.0,
                          halo_px=40, min_halo=0.35, shadow=True, max_halo=1.0)
        im = plate.draw(im)
    record_contrast(lay, "brand", [plate])
    if lay is not None:
        lay.meta.setdefault("plates", {})["brand"] = [plate]
    return im


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
    im = Image.fromarray(backdrop).filter(ImageFilter.GaussianBlur(60))
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
    centered = style == "minimal"
    margin = 96 if style == "depth_sandwich" else 84
    width = 1080 - 2 * margin
    rows, shortened = _rows(im, copy, width, style, tight)
    # Very long generated copy shares the central square with the photograph:
    # reduce the *whole* stack consistently before sacrificing its image space.
    if _height(rows) > 500:
        scale = (500 - sum(r["gap"] for r in rows)) / sum(r["height"] for r in rows)
        for row in rows:
            row["font"] = get_font("Anton" if row["name"].startswith("title") else
                                   (brand.DATA_FONT if style == "depth_sandwich" and row["name"] == "eyebrow"
                                    else "Montserrat"),
                                   max(24, int(row["font"].size * scale)),
                                   weight=800 if row["name"] == "eyebrow" and style != "depth_sandwich" else None)
            row["height"] = text_size(ImageDraw.Draw(im), row["text"], row["font"])[1]
    if lay is not None:
        lay.shortened = shortened
    height = _height(rows)
    paper = style == "depth_sandwich"
    if paper:
        for row in rows:
            row["fill"] = brand.INK if row["name"] != "tagline" else brand.INK2
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
        top = 534
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
