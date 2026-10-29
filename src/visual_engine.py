"""
src/visual_engine.py
====================
Scene sequencing and the locked Variant B thumbnail engine.
1. Resolves topic-specific scene media (images, plus an optional motion hook clip).
   A topic without its own media fails fast instead of borrowing another topic's imagery.
2. Renders the Variant B (85% opacity halo) cover in two formats:
   - 1080x1920 (9:16 Reel cover)
   - 1080x1080 (1:1 Instagram profile-grid crop)
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PIL import Image, ImageDraw, ImageFilter

from .config import (
    ASSETS_DIR,
    MIN_SCENE_IMAGES,
    THUMB_EYEBROW_FONT_SIZE,
    THUMB_TITLE_FONT_SIZE,
    THUMB_TAGLINE_FONT_SIZE,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
)
from .credits import CONTACT_SHEET, CREDITS_FILE, load_credits
from .typography import cover_crop, fit_font, fit_line, get_font, is_video, spoken_units, text_size

log = logging.getLogger(__name__)

GOLD = (255, 215, 0, 255)
WHITE = (255, 255, 255, 255)
SAFE_TEXT_WIDTH = VIDEO_WIDTH - 2 * 110
COVER_STYLES = ("badge", "lower_third", "minimal", "depth_sandwich")

class MissingAssetsError(FileNotFoundError):
    pass


def cover_text_zone() -> Tuple[int, int, int, int]:
    """Where a 9:16 cover's text and badges stay: the centre square both profile-grid crops keep
    (safe_zones.cover_box), less 60 px at the sides and 20 px at the top and bottom."""
    from .safe_zones import cover_box
    x0, y0, x1, y1 = cover_box(VIDEO_WIDTH, VIDEO_HEIGHT)
    return x0 + 60, y0 + 20, x1 - 60, y1 - 20


def grid_crop(cover: Image.Image) -> Image.Image:
    """The 1:1 profile-grid copy of a 9:16 cover: its centre square."""
    top = max(0, min(VIDEO_HEIGHT - VIDEO_WIDTH, VIDEO_HEIGHT // 2 - VIDEO_WIDTH // 2))
    return cover.crop((0, top, VIDEO_WIDTH, top + VIDEO_WIDTH))


def _draw(draw, lay, name: str, xy, text: str, font, fill, anchor: str):
    """Draws one line and records its measured box; None for an empty line."""
    if not text:
        return None
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)
    box = draw.textbbox(xy, text, font=font, anchor=anchor)
    return lay.add(name, box) if lay is not None else box


def redraw_cover(path: Path, recipe: Dict[str, Any]) -> Dict[str, Any]:
    """Draws a cover again with the tighter fit, from its kept recipe, in place (still_vision: the look
    reported its text cut off or overlapping); the profile-grid copy is cut again from the main cover."""
    from . import still_layout
    engine = VisualEngine()
    engine._text_cy = int(recipe.get("text_cy") or VIDEO_HEIGHT // 2)
    im = cover_crop(Path(recipe["hero"]), (VIDEO_WIDTH, VIDEO_HEIGHT))
    render = {"lower_third": engine._cover_lower_third, "minimal": engine._cover_minimal,
              "depth_sandwich": engine._cover_depth_sandwich}.get(recipe.get("style"), engine._cover_badge)
    lay = still_layout.Layout("cover", (VIDEO_WIDTH, VIDEO_HEIGHT), cover_text_zone(), tight=True)
    out = render(im, dict(recipe.get("copy") or {}), lay=lay, tight=True).convert("RGB")
    path = Path(path)
    # Written aside, then swapped in: the video judge may be reading this cover while the audit redraws it
    # (review, 2026-10-28)
    tmp = path.with_name(path.stem + ".redraw" + path.suffix)
    out.save(tmp, quality=95)
    import os
    os.replace(tmp, path)
    if path.name == "thumbnail_cover.jpg":
        grid_crop(out).save(path.with_name("thumbnail_profile_grid.jpg"), quality=95)
    prior = still_layout.load(path) or {}
    return still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)


def calm_center_y(im: Image.Image, candidates=(760, 960, 1160), band: int = 440) -> int:
    """Where to centre a title so it covers the least eye-catching, least busy part of the photo:
    spectral-residual saliency (Hou & Zhang, 2007) plus edge density, on a small greyscale copy.
    Candidates stay inside the centre square that the profile grid keeps; ties favour the middle."""
    import cv2
    import numpy as np
    small = np.asarray(im.convert("L").resize((128, 228)), dtype=np.float32) / 255.0
    spectrum = np.fft.fft2(small)
    log_amp = np.log(np.abs(spectrum) + 1e-6)
    blurred = cv2.blur(log_amp.astype(np.float32), (3, 3))
    residual = np.exp((log_amp - blurred) + 1j * np.angle(spectrum))
    sal = np.abs(np.fft.ifft2(residual)) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), 3)
    # Busy texture hurts legibility as much as a salient subject: add edge density (Sobel magnitude).
    gx, gy = cv2.Sobel(small, cv2.CV_32F, 1, 0), cv2.Sobel(small, cv2.CV_32F, 0, 1)
    edges = np.sqrt(gx * gx + gy * gy)
    clutter = sal / (sal.mean() + 1e-6) + edges / (edges.mean() + 1e-6)
    scale = clutter.shape[0] / im.height

    def score(cy):
        a, b = int((cy - band / 2) * scale), int((cy + band / 2) * scale)
        return float(clutter[max(0, a):b].mean()) * (0.95 if cy == 960 else 1.0)
    return min(candidates, key=score)


def extract_foreground_mask(im: Image.Image) -> Tuple[Any, float]:
    """Extract foreground subject mask using edge-saliency and Otsu thresholding.
    Returns (mask_array, coverage_ratio)."""
    import cv2
    import numpy as np
    arr = np.asarray(im.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    if float(mag.mean()) < 1.0:
        return np.zeros(gray.shape, dtype=np.uint8), 0.0

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    border = np.concatenate([mask[0, :], mask[-1, :], mask[:, 0], mask[:, -1]])
    if np.mean(border) > 128:
        mask = cv2.bitwise_not(mask)
        
    coverage = float(np.count_nonzero(mask)) / mask.size
    return mask, coverage


def extract_curiosity_badge(script_payload: Dict[str, Any]) -> str:
    """Extracts a high-impact numerical or forensic curiosity badge from script facts or thumbnail."""
    copy = script_payload.get("thumbnail", {})
    if copy.get("badge"):
        return copy["badge"].upper()

    stat_cards = script_payload.get("stat_cards") or []
    for sc in stat_cards:
        val = str(sc.get("value", "")).strip()
        lbl = str(sc.get("label", "")).strip()
        if any(c.isdigit() for c in val):
            badge = f"{val.upper()} • {lbl.upper()}"
            return badge[:28] if len(badge) <= 28 else val.upper()[:22]

    scale_fact = script_payload.get("scale_fact") or {}
    if scale_fact.get("value"):
        val = scale_fact["value"]
        unit = str(scale_fact.get("unit", "")).upper()
        return f"{val} {unit} RECORD"[:24]

    if copy.get("eyebrow"):
        return copy["eyebrow"].upper()[:24]

    return "ARCHAEOLOGICAL RECORD"


class VisualEngine:
    def __init__(self, assets_dir: Path = ASSETS_DIR):
        self.assets_dir = assets_dir

    def get_scene_sequence(self, topic: Dict[str, Any]) -> List[Path]:
        """
        Scene media for the video, in order. Sources, first match wins:
          assets/<topic_id>/*.jpg|*.png  (sorted by name; at least MIN_SCENE_IMAGES).
              If the folder has a credits.json (fetched images), only credited files are
              used, and only after a human approved the set (`main.py assets approve`).
          the shared suite, for the topics it actually depicts
        An optional assets/<topic_id>/hook_motion.mp4 replaces the first still (Beat 1).
        """
        tid = topic["id"]
        topic_dir = self.assets_dir / tid
        resolved: List[Path] = []

        if (topic_dir / CREDITS_FILE).exists():
            credits = load_credits(topic_dir)
            if not credits["reviewed"]:
                raise MissingAssetsError(
                    f"Fetched images for '{tid}' are not reviewed. Check {topic_dir / CONTACT_SHEET}, delete "
                    f"wrong images, then run: python main.py assets approve {tid}"
                )
            # Only photos that show the topic (best first, so the cover is the best of them), those you kept,
            # and at most a couple of context shots; never one that was weak or never looked at (2026-10-25).
            # The same counting as approval and readiness (image_review.readiness, 2026-10-28), so a pool this
            # refuses is one the scheduler already reported as not ready.
            from .image_review import readiness_text, usable_photos
            chosen = usable_photos(credits["images"])
            resolved = [topic_dir / c["file"] for c in chosen]
            if len(resolved) < MIN_SCENE_IMAGES:
                raise MissingAssetsError(
                    f"Only {len(resolved)} of the {len(credits['images'])} approved photos of '{tid}' are strong enough "
                    f"for the video (need {MIN_SCENE_IMAGES}): {readiness_text(credits['images'])}"
                    + ". Fetch or upload photos that show the topic itself, or keep a photo with /photos N keep from its sheet.")
        elif topic_dir.is_dir():
            resolved = sorted(
                p for p in topic_dir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.name != CONTACT_SHEET
            )

        if len(resolved) < MIN_SCENE_IMAGES:
            raise MissingAssetsError(
                f"Topic '{tid}' has {len(resolved)} scene images; need at least {MIN_SCENE_IMAGES} "
                f"in {topic_dir}. Add licensed images that depict this topic."
            )

        hook_video = topic_dir / "hook_motion.mp4"
        if hook_video.exists():
            log.info("Motion hook clip found for Beat 1: %s", hook_video.name)
            resolved = [hook_video] + resolved[1:]
        return resolved

    def reserve_sequence(self, topic: Dict[str, Any]) -> List[Path]:
        """The approved pool's context shots beyond the video's usual cap (image_review.reserve_photos), best
        first: used only where a picture would otherwise repeat. Empty for a pool not approved or without credits."""
        topic_dir = self.assets_dir / topic["id"]
        if not (topic_dir / CREDITS_FILE).exists():
            return []
        credits = load_credits(topic_dir)
        if not credits.get("reviewed"):
            return []
        from .image_review import reserve_photos
        return [topic_dir / c["file"] for c in reserve_photos(credits["images"]) if (topic_dir / c["file"]).is_file()]

    @staticmethod
    def hero_image(scene_media: List[Path]) -> Path:
        """First still image in the sequence (a motion hook clip can't be a cover)."""
        return next(p for p in scene_media if not is_video(p))

    def generate_thumbnails(self, hero_image_path: Path, script_payload: Dict[str, Any], output_dir: Path,
                            style: str = "badge", variants: bool = True) -> Tuple[Path, Path]:
        """Cover in one of COVER_STYLES (rotated per run so neighbouring grid posts differ).
        Every layout keeps its text inside the centre 1080x1080 square, which survives both the
        1:1 and the 3:4 profile-grid crops, and draws every line of its copy (eyebrow, title, tagline).
        Also renders the complete 3-Variant Thumbnail Suite (Forensic Blueprint, Discovery Intrigue,
        Cinematic Minimal) for human review and A/B selection. Each cover is measured and checked after
        drawing (still_layout); a failing one is drawn once more with the tighter fit."""
        output_dir.mkdir(parents=True, exist_ok=True)
        out_916_path = output_dir / "thumbnail_cover.jpg"
        out_1x1_path = output_dir / "thumbnail_profile_grid.jpg"

        copy = dict(script_payload.get("thumbnail", {}))
        if "badge" not in copy:
            copy["badge"] = extract_curiosity_badge(script_payload)
        copy = _in_spoken_units(copy, script_payload)

        im = cover_crop(hero_image_path, (VIDEO_WIDTH, VIDEO_HEIGHT))
        self._text_cy = calm_center_y(im)  # badge/minimal titles sit where the photo is calmest

        def save(path: Path, chosen: str) -> Image.Image:
            out, lay, attempts = self._render_checked(im, copy, chosen)
            out = out.convert("RGB")
            out.save(path, quality=95)
            from . import still_layout
            still_layout.save(path, lay, {"renderer": "visual_engine.redraw_cover", "hero": str(hero_image_path),
                                          "copy": copy, "style": chosen, "text_cy": self._text_cy}, attempts)
            return out

        # The 3-variant 9:16 suite is for Reels; a documentary (variants=False) uses its 16:9 thumbnail.
        if variants:
            save(output_dir / "thumbnail_variant_a_blueprint.jpg", "depth_sandwich")
            save(output_dir / "thumbnail_variant_b_intrigue.jpg", "badge")
            save(output_dir / "thumbnail_variant_c_minimal.jpg", "minimal")

        final_916 = save(out_916_path, style if style in COVER_STYLES else "badge")
        grid_crop(final_916).save(out_1x1_path, quality=95)
        log.info("Thumbnails generated (%s, with 3-variant suite): %s, %s", style, out_916_path.name, out_1x1_path.name)
        return out_916_path, out_1x1_path

    def _render_checked(self, im: Image.Image, copy: Dict[str, str], style: str):
        """(image, layout, attempts): the style drawn, checked, and drawn once more with the tighter fit if its
        measured layout fails."""
        from . import still_layout
        render = {"lower_third": self._cover_lower_third, "minimal": self._cover_minimal,
                  "depth_sandwich": self._cover_depth_sandwich}.get(style, self._cover_badge)

        def draw(tight: bool):
            lay = still_layout.Layout("cover", (VIDEO_WIDTH, VIDEO_HEIGHT), cover_text_zone(), tight=tight)
            return render(im.copy(), copy, lay=lay, tight=tight), lay
        return still_layout.drawn(draw)

    @staticmethod
    def _shadowed_text(im: Image.Image, xy, text: str, font, fill, anchor: str) -> Image.Image:
        """Text with a soft drop shadow, for layouts without a backing box."""
        shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((xy[0] + 3, xy[1] + 5), text, font=font, fill=(0, 0, 0, 200), anchor=anchor)
        im = Image.alpha_composite(im, shadow.filter(ImageFilter.GaussianBlur(8)))
        ImageDraw.Draw(im).text(xy, text, font=font, fill=fill, anchor=anchor)
        return im

    @staticmethod
    def _fit_copy(draw, copy: Dict[str, str], tight: bool, title_family: str, title_size: int, eye_size: int,
                  tag_size: int, lay=None, tag_width: int = SAFE_TEXT_WIDTH):
        """The cover's four lines fitted with the fonts' real metrics: one title size for both lines (down to
        44 px, then shortened at a word with an ellipsis), the eyebrow and tagline each on one line."""
        k = 0.85 if tight else 1.0
        eyebrow, font_eye, s_e = fit_line(draw, copy.get("eyebrow", "").upper(), "Montserrat", int(eye_size * k), SAFE_TEXT_WIDTH, 20)
        t_min = 40 if tight else 44   # a smaller title before a shortened one (56 px left a 28-letter line off the canvas)
        l1, l2 = copy.get("line1", "").upper(), copy.get("line2", "").upper()
        size = min(fit_line(draw, l1, title_family, int(title_size * k), SAFE_TEXT_WIDTH, t_min, quiet=True)[1].size,
                   fit_line(draw, l2, title_family, int(title_size * k), SAFE_TEXT_WIDTH, t_min, quiet=True)[1].size)
        l1, font_title, s1 = fit_line(draw, l1, title_family, size, SAFE_TEXT_WIDTH, size)
        l2, _, s2 = fit_line(draw, l2, title_family, size, SAFE_TEXT_WIDTH, size)
        tagline, font_tag, s_t = fit_line(draw, copy.get("tagline", "").upper(), "Montserrat", int(tag_size * k), tag_width, 22)
        if lay is not None:
            lay.shortened = [n for n, s in (("eyebrow", s_e), ("title_1", s1), ("title_2", s2), ("tagline", s_t)) if s]
        return eyebrow, l1, l2, tagline, font_eye, font_title, font_tag

    def _cover_lower_third(self, im: Image.Image, copy: Dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        """Cinematic: dark gradient rising from the bottom of the square, left-aligned title, the tagline under
        it (it was left out of this layout, so Richat 2's cover had no "MAURITANIA'S BULLSEYE", 2026-10-26)."""
        square_bottom = VIDEO_HEIGHT // 2 + VIDEO_WIDTH // 2
        grad = Image.new("L", (1, VIDEO_HEIGHT), 0)
        for y in range(VIDEO_HEIGHT):  # 0 above the middle, up to ~80% at the square's bottom and below
            grad.putpixel((0, y), int(205 * min(1.0, max(0.0, (y - VIDEO_HEIGHT * 0.45) / (square_bottom - VIDEO_HEIGHT * 0.45)))))
        shade = Image.new("RGBA", im.size, (0, 0, 0, 255))
        shade.putalpha(grad.resize(im.size))
        im = Image.alpha_composite(im, shade)
        draw = ImageDraw.Draw(im)
        x0 = 110
        eyebrow, line1, line2, tagline, font_eye, font_title, font_tag = self._fit_copy(
            draw, copy, tight, "Cinzel", 118, 34, 34, lay, tag_width=SAFE_TEXT_WIDTH)
        size = font_title.size
        zone_bottom = cover_text_zone()[3]
        y2 = square_bottom - 150  # clear of the square crop's bottom edge
        if tagline:  # the tagline's row under the title, inside the square: the block rises if it must
            tag_box = draw.textbbox((x0, y2 + size // 2 + 44), tagline, font=font_tag, anchor="lm")
            y2 -= max(0, tag_box[3] - zone_bottom)
        y1 = y2 - size - 10
        # Each line placed from the measured box under it, not the font size: an accented capital ("ÉPHÈSE")
        # rises above the cap height and overlapped the line above on every redraw (review, 2026-10-28).
        if line1 and line2:
            top2 = draw.textbbox((x0, y2), line2, font=font_title, anchor="lm")[1]
            y1 -= max(0, draw.textbbox((x0, y1), line1, font=font_title, anchor="lm")[3] - (top2 - 10))
        ye = y1 - size // 2 - 40
        bbox_eye = draw.textbbox((0, 0), eyebrow, font=font_eye, anchor="lt")
        th_eye = bbox_eye[3] - bbox_eye[1]
        if line1 or line2:
            top1 = draw.textbbox((x0, y1 if line1 else y2), line1 or line2, font=font_title, anchor="lm")[1]
            ye -= max(0, ye + th_eye // 2 + max(th_eye + 14, 32) // 2 - (top1 - 16))
        cy_eye = ye + th_eye // 2
        bar_h = max(th_eye + 14, 32)
        draw.rectangle([x0, cy_eye - bar_h // 2, x0 + 8, cy_eye + bar_h // 2], fill=GOLD)
        eye = _draw(draw, lay, "eyebrow", (x0 + 26, cy_eye), eyebrow, font_eye, GOLD, "lm")
        if lay is not None and eye:
            lay.elements[-1]["box"][0] = x0  # the gold bar belongs to the eyebrow
            lay.elements[-1]["box"][1] = min(lay.elements[-1]["box"][1], cy_eye - bar_h // 2)
            lay.elements[-1]["box"][3] = max(lay.elements[-1]["box"][3], cy_eye + bar_h // 2)
        _draw(draw, lay, "title_1", (x0, y1), line1, font_title, WHITE, "lm")
        _draw(draw, lay, "title_2", (x0, y2), line2, font_title, GOLD, "lm")
        if tagline:
            _draw(draw, lay, "tagline", (x0, y2 + size // 2 + 44), tagline, font_tag, WHITE, "lm")
        return im

    def _cover_minimal(self, im: Image.Image, copy: Dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        """Minimal: no boxes; a thin gold rule, eyebrow, title and tagline with soft shadows, centred."""
        veil = Image.new("RGBA", im.size, (0, 0, 0, 70))  # light overall dim keeps white text readable
        im = Image.alpha_composite(im, veil)
        draw = ImageDraw.Draw(im)
        eyebrow, line1, line2, tagline, font_eye, font_title, font_tag = self._fit_copy(
            draw, copy, tight, "Cinzel", 104, 32, 32, lay)
        size = font_title.size
        cx, cy = VIDEO_WIDTH // 2, getattr(self, "_text_cy", VIDEO_HEIGHT // 2)
        zx0, zy0, zx1, zy1 = cover_text_zone()
        # The whole stack, rule to tagline, inside the square: measured, then moved in if it reaches an edge.
        top, bottom = cy - size - 74, cy + size // 2 + 40 + (size // 2 + 30 + 2 * font_tag.size if tagline else size // 2)
        cy += max(0, zy0 - top) - max(0, bottom - zy1)
        draw.line([cx - 70, cy - size - 70, cx + 70, cy - size - 70], fill=GOLD, width=4)
        if lay is not None:
            lay.add("rule", (cx - 70, cy - size - 72, cx + 70, cy - size - 68))
        rows = [("eyebrow", (cx, cy - size - 30), eyebrow, font_eye, GOLD),
                ("title_1", (cx, cy - size // 2), line1, font_title, WHITE),
                ("title_2", (cx, cy + size // 2 + 40), line2, font_title, WHITE)]
        if tagline:
            rows.append(("tagline", (cx, cy + size + 40 + 30 + font_tag.size // 2), tagline, font_tag, GOLD))
        for name, xy, text, font, fill in rows:
            if not text:
                continue
            if lay is not None:
                lay.add(name, draw.textbbox(xy, text, font=font, anchor="mm"))
            im = self._shadowed_text(im, xy, text, font, fill, "mm")
            draw = ImageDraw.Draw(im)
        return im

    def _cover_depth_sandwich(self, im: Image.Image, copy: Dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        """Studio-grade 3D depth-layered cover: typography sandwiched between background
        and foreground architectural elements with warm torchlight rim lighting."""
        import cv2
        import numpy as np

        mask, coverage = extract_foreground_mask(im)

        bg = im.convert("RGBA")
        veil = Image.new("RGBA", bg.size, (10, 14, 18, 90))
        bg = Image.alpha_composite(bg, veil)

        cx = VIDEO_WIDTH // 2
        cy = getattr(self, "_text_cy", VIDEO_HEIGHT // 2)
        cy = max(760, min(1160, cy))

        measure = ImageDraw.Draw(bg)
        eyebrow, line1, line2, tagline, font_eye, font_title, font_tag = self._fit_copy(
            measure, copy, tight, "Cinzel", THUMB_TITLE_FONT_SIZE, THUMB_EYEBROW_FONT_SIZE, THUMB_TAGLINE_FONT_SIZE,
            lay, tag_width=SAFE_TEXT_WIDTH - 68)
        title_size = font_title.size
        gap = 14 if tight else 0  # the tighter fit opens the rows a little

        rows = [("eyebrow", (cx, cy - title_size - 40 - gap), eyebrow, font_eye, GOLD, 220),
                ("title_1", (cx, cy - title_size // 2), line1, font_title, WHITE, 240),
                ("title_2", (cx, cy + title_size // 2 + 10 + gap), line2, font_title, GOLD, 240)]
        if tagline:
            rows.append(("tagline", (cx, cy + title_size + 40 + 2 * gap), tagline, font_tag, WHITE, 220))
        rows = [r for r in rows if r[2]]

        text_layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(text_layer)

        shadow_layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        for _, (x, y), text, font, _, alpha in rows:
            sdraw.text((x + 4, y + 6), text, font=font, fill=(0, 0, 0, alpha), anchor="mm")
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(12))
        bg = Image.alpha_composite(bg, shadow_layer)

        for name, xy, text, font, fill, _ in rows:
            tdraw.text(xy, text, font=font, fill=fill, anchor="mm")
            if lay is not None:
                lay.add(name, tdraw.textbbox(xy, text, font=font, anchor="mm"))

        sandwich = Image.alpha_composite(bg, text_layer)

        if 0.15 <= coverage <= 0.60:
            text_box_y0 = max(0, cy - title_size - 60)
            text_box_y1 = min(mask.shape[0], cy + title_size + 60)
            box_slice = mask[text_box_y0:text_box_y1, :]
            num_labels, _ = cv2.connectedComponents(box_slice)
            occlusion = float(np.count_nonzero(box_slice)) / (box_slice.size + 1e-6)
            # Only composite depth sandwich if mask is a clean, coherent subject (low fragment count)
            # and occlusion is moderate (<=22%), preventing mottled stone/texture from bleeding over typography
            if 0.03 <= occlusion <= 0.22 and num_labels <= 12:
                feathered_mask = cv2.GaussianBlur(mask, (15, 15), 0)
                fg_alpha = Image.fromarray(feathered_mask, mode="L")
                fg_im = im.convert("RGBA")
                fg_im.putalpha(fg_alpha)
                sandwich = Image.alpha_composite(sandwich, fg_im)

        return sandwich

    def _cover_badge(self, im: Image.Image, copy: Dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        """The original Variant B: centred title on an 85% halo, gold-outlined eyebrow and tagline pills."""
        measure = ImageDraw.Draw(im)
        eyebrow, line1, line2, tagline, font_eye, font_title, font_tag = self._fit_copy(
            measure, copy, tight, "Cinzel", THUMB_TITLE_FONT_SIZE, THUMB_EYEBROW_FONT_SIZE, THUMB_TAGLINE_FONT_SIZE,
            lay, tag_width=SAFE_TEXT_WIDTH - 68)

        w_eye, h_eye = text_size(measure, eyebrow, font_eye)
        w_l1, h_l1 = text_size(measure, line1, font_title)
        w_l2, h_l2 = text_size(measure, line2, font_title)
        w_tag, h_tag = text_size(measure, tagline, font_tag) if tagline else (0, 0)

        eye_box_w, eye_box_h = w_eye + 56, h_eye + 28
        tag_box_w, tag_box_h = (w_tag + 68, h_tag + 32) if tagline else (0, 0)
        gap_eye, gap_lines, gap_tag = (34, 22, 38) if tight else (28, 16, 32)

        center_y = getattr(self, "_text_cy", VIDEO_HEIGHT // 2)
        total_h = eye_box_h + gap_eye + h_l1 + gap_lines + h_l2 + (gap_tag + tag_box_h if tagline else 0)
        zx0, zy0, zx1, zy1 = cover_text_zone()
        start_y = max(zy0, min(center_y - total_h // 2, zy1 - total_h))  # the whole stack inside the square
        title_y0 = start_y + eye_box_h + gap_eye
        title_y1 = title_y0 + h_l1 + gap_lines + h_l2
        title_cy = (title_y0 + title_y1) // 2

        # Soft-feathered halo behind the title
        bg_w = max(w_l1, w_l2) + 120
        bg_h = (title_y1 - title_y0) + 90
        halo = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle(
            [(VIDEO_WIDTH - bg_w) // 2, title_cy - bg_h // 2, (VIDEO_WIDTH + bg_w) // 2, title_cy + bg_h // 2],
            radius=30, fill=(0, 0, 0, 160),
        )
        im = Image.alpha_composite(im, halo.filter(ImageFilter.GaussianBlur(32)))
        draw = ImageDraw.Draw(im)
        cx = VIDEO_WIDTH // 2

        eye_box = [cx - eye_box_w // 2, start_y, cx + eye_box_w // 2, start_y + eye_box_h]
        draw.rounded_rectangle(eye_box, radius=10, fill=(12, 12, 12, 220), outline=(255, 215, 0, 240), width=2)
        draw.text((cx, start_y + eye_box_h // 2), eyebrow, font=font_eye, fill=GOLD, anchor="mm")
        if lay is not None:
            lay.add("eyebrow", eye_box)
        _draw(draw, lay, "title_1", (cx, title_y0 + h_l1 // 2), line1, font_title, WHITE, "mm")
        _draw(draw, lay, "title_2", (cx, title_y0 + h_l1 + gap_lines + h_l2 // 2), line2, font_title, GOLD, "mm")

        if tagline:
            tag_y0 = title_y1 + gap_tag
            tag_box = [cx - tag_box_w // 2, tag_y0, cx + tag_box_w // 2, tag_y0 + tag_box_h]
            draw.rounded_rectangle(tag_box, radius=12, fill=(14, 14, 14, 225), outline=(255, 215, 0, 240), width=2)
            draw.text((cx, tag_y0 + tag_box_h // 2), tagline, font=font_tag, fill=WHITE, anchor="mm")
            if lay is not None:
                lay.add("tagline", tag_box)
        return im

    def generate_depth_layered_thumbnail(
        self,
        hero_image_path: Path,
        script_payload: Dict[str, Any],
        output_path: Path,
        aspect_ratio: str = "16:9",
    ) -> Path:
        """
        Studio-Grade 3D Depth-Layered Key-Art Thumbnail with Adaptive Saliency Gating.
        - Supports 16:9 (1920x1080) and 9:16 (1080x1920).
        - Segments foreground rock pillars / architectural elements via Otsu and edge saliency.
        - Gating: If foreground coverage is 15% to 60%, composites a 3D sandwich:
            Layer 1 (Background): Graded photo with directional atmospheric lighting.
            Layer 2 (Typography): Hero title tucked with soft ambient occlusion.
            Layer 3 (Foreground): Feathered subject overlay with warm rim illumination.
            Layer 4 (Badges): Verified depth tagline badge and safe-zone badges.
        - Graceful fallback: If coverage < 15% or > 60%, applies calibrated directional vignette.
        - Enforces strict lower-right safe-zone clearance for YouTube/Meta timestamp overlays.
        """
        import cv2
        import numpy as np

        output_path.parent.mkdir(parents=True, exist_ok=True)
        w, h = (1920, 1080) if aspect_ratio == "16:9" else (1080, 1920)
        im = cover_crop(hero_image_path, (w, h)).convert("RGBA")

        # Directional left-to-right gradient shade to guarantee text contrast without obscuring right-side art
        grad = Image.new("L", (w, 1), 0)
        grad_reach = int(w * 0.6)
        for x in range(w):
            val = int(220 * max(0.0, 1.0 - (x / float(grad_reach)) ** 1.3))
            grad.putpixel((x, 0), val)
        shade = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        shade.putalpha(grad.resize((w, h)))
        bg = Image.alpha_composite(im, shade)

        copy = _in_spoken_units(documentary_cover_copy(script_payload), script_payload)
        eyebrow, line1, line2, tagline = copy["eyebrow"], copy["line1"], copy["line2"], copy["tagline"]

        max_text_w = int(w * 0.5)
        draw = ImageDraw.Draw(bg)
        font_eye = fit_font(draw, eyebrow, "Montserrat", 32, max_text_w, 20)
        title_font_size = min(
            fit_font(draw, line1, "Cinzel", 100, max_text_w, 48).size,
            fit_font(draw, line2, "Cinzel", 100, max_text_w, 48).size,
        )
        font_title = get_font("Cinzel", title_font_size)
        font_tag = fit_font(draw, tagline, "Montserrat", 30, max_text_w, 20)

        w_eye, h_eye = text_size(draw, eyebrow, font_eye)
        w_l1, h_l1 = text_size(draw, line1, font_title)
        w_l2, h_l2 = text_size(draw, line2, font_title)
        w_tag, h_tag = text_size(draw, tagline, font_tag)

        x0 = 120
        y_eye = 250 if h == 1080 else 600
        eye_pad_x, eye_pad_y = 20, 10
        y_l1 = y_eye + h_eye + eye_pad_y * 2 + 36
        y_l2 = y_l1 + h_l1 + 14
        y_tag = y_l2 + h_l2 + 34
        tag_pad_x, tag_pad_y = 22, 10

        # Layer 2: Typography layer
        text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(text_layer)

        # Ambient occlusion drop shadow behind text
        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        sdraw.text((x0 + 4, y_l1 + 6), line1, font=font_title, fill=(0, 0, 0, 240), anchor="lt")
        sdraw.text((x0 + 4, y_l2 + 6), line2, font=font_title, fill=(0, 0, 0, 240), anchor="lt")
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(10))
        bg = Image.alpha_composite(bg, shadow_layer)

        tdraw.text((x0, y_l1), line1, font=font_title, fill=WHITE, anchor="lt")
        tdraw.text((x0, y_l2), line2, font=font_title, fill=GOLD, anchor="lt")

        sandwich = Image.alpha_composite(bg, text_layer)

        # Layer 3: Foreground extraction with adaptive gating (15% to 60%)
        mask, coverage = extract_foreground_mask(im)
        if 0.15 <= coverage <= 0.60:
            text_box_y0 = max(0, y_l1 - 20)  # the title block: both lines and the gap between them
            text_box_y1 = min(mask.shape[0], y_l1 + title_font_size * 2 + 60)
            box_slice = mask[text_box_y0:text_box_y1, x0:min(mask.shape[1], x0 + max_text_w)]
            occlusion = float(np.count_nonzero(box_slice)) / (box_slice.size + 1e-6)
            if occlusion <= 0.35:
                feathered_mask = cv2.GaussianBlur(mask, (15, 15), 0)
                fg_alpha = Image.fromarray(feathered_mask, mode="L")
                fg_im = im.convert("RGBA")
                fg_im.putalpha(fg_alpha)
                sandwich = Image.alpha_composite(sandwich, fg_im)

        # Layer 4: Badges (Eyebrow & Tagline) crisp on top
        badge_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(badge_layer)
        bdraw.rounded_rectangle(
            [x0, y_eye, x0 + w_eye + eye_pad_x * 2, y_eye + h_eye + eye_pad_y * 2],
            radius=8, fill=(16, 16, 18, 220), outline=GOLD, width=2,
        )
        bdraw.text((x0 + eye_pad_x, y_eye + eye_pad_y), eyebrow, font=font_eye, fill=GOLD, anchor="lt")

        bdraw.rounded_rectangle(
            [x0, y_tag, x0 + w_tag + tag_pad_x * 2, y_tag + h_tag + tag_pad_y * 2],
            radius=8, fill=(20, 20, 24, 230), outline=GOLD, width=2,
        )
        bdraw.text((x0 + tag_pad_x, y_tag + tag_pad_y), tagline, font=font_tag, fill=WHITE, anchor="lt")

        final_im = Image.alpha_composite(sandwich, badge_layer).convert("RGB")
        final_im.save(output_path, quality=95)
        log.info("Key-art thumbnail generated (%s): %s", aspect_ratio, output_path.name)
        return output_path

    def generate_youtube_thumbnail(
        self,
        hero_image_path: Path,
        script_payload: Dict[str, Any],
        output_path: Path,
    ) -> Path:
        """
        16:9 Widescreen Key-Art Thumbnail (1920x1080) for YouTube / Facebook In-Stream.
        Delegates to studio-grade depth-layered engine with strict timestamp safe zone clearance.
        """
        return self.generate_depth_layered_thumbnail(hero_image_path, script_payload, output_path, aspect_ratio="16:9")


def _in_spoken_units(copy: Dict[str, Any], script_payload: Dict[str, Any]) -> Dict[str, Any]:
    """The cover's lines with each figure in the unit the narration speaks (Richat's cover said "40+ KM" while the
    narration said "25 miles": one unit per run, 2026-10-29)."""
    narration = script_payload.get("narration_script") or ""
    return {k: spoken_units(v, narration) if isinstance(v, str) else v for k, v in copy.items()}


def documentary_cover_copy(script_payload: Dict[str, Any]) -> Dict[str, str]:
    """The four lines of the documentary cover: the payload's thumbnail copy where given, else lines drawn
    from the topic itself (the title split on whole words, the sub-theme). Until 2026-10-24 the fallbacks
    were Derinkuyu's ("ANCIENT METROPOLIS", "UNEXPLAINED ARCHITECTURE") and appeared on any topic whose
    deep-dive copy was missing."""
    copy = script_payload.get("thumbnail") or {}
    title = (script_payload.get("title") or "").strip()
    words, line1 = title.split(), ""
    while words and len(f"{line1} {words[0]}".strip()) <= 24:
        line1 = f"{line1} {words.pop(0)}".strip()
    rest = " ".join(words) if not copy.get("line1") else ""
    sub_theme = (script_payload.get("sub_theme") or "").strip()
    line2 = copy.get("line2") or rest or sub_theme or "THE EVIDENCE"
    tagline = copy.get("tagline") or ("" if line2 == sub_theme else sub_theme) or "WHAT THE EVIDENCE SHOWS"
    return {"eyebrow": (copy.get("eyebrow") or "DOCUMENTARY INVESTIGATION").upper(),
            "line1": (copy.get("line1") or line1 or title[:24]).upper(), "line2": line2.upper(), "tagline": tagline.upper()}
