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
    THUMB_OPACITY,
    THUMB_BLUR_RADIUS,
    THUMB_EYEBROW_FONT_SIZE,
    THUMB_TITLE_FONT_SIZE,
    THUMB_TAGLINE_FONT_SIZE,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
)
from .credits import CONTACT_SHEET, CREDITS_FILE, load_credits
from .typography import cover_crop, fit_font, get_font, is_video, text_size

log = logging.getLogger(__name__)

GOLD = (255, 215, 0, 255)
WHITE = (255, 255, 255, 255)
SAFE_TEXT_WIDTH = VIDEO_WIDTH - 2 * 110
COVER_STYLES = ("badge", "lower_third", "minimal")

# The shared Cappadocia photo suite in assets/ only depicts the Derinkuyu topic.
SHARED_SUITE = {
    "01_derinkuyu_underground_city": [
        "scene1_stairs.jpg", "scene07_ozkonak_entrance.jpg", "scene2_tunnel.jpg",
        "scene08_ozkonak_tunnel.jpg", "scene3_door.jpg", "scene09_kaymakli_arch.jpg",
        "scene4_pillars.jpg", "scene5_room.jpg", "scene13_derinkuyu_church.jpg",
        "scene10_goreme_town.jpg", "scene11_panorama.jpg", "scene12_uchisar.jpg",
        "scene2_tunnel.jpg",  # match-cut ending
    ],
}


class MissingAssetsError(FileNotFoundError):
    pass


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
            resolved = sorted(topic_dir / c["file"] for c in credits["images"])
        elif topic_dir.is_dir():
            resolved = sorted(
                p for p in topic_dir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.name != CONTACT_SHEET
            )
        if len(resolved) < MIN_SCENE_IMAGES and tid in SHARED_SUITE:
            resolved = [self.assets_dir / name for name in SHARED_SUITE[tid] if (self.assets_dir / name).exists()]

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

    @staticmethod
    def hero_image(scene_media: List[Path]) -> Path:
        """First still image in the sequence (a motion hook clip can't be a cover)."""
        return next(p for p in scene_media if not is_video(p))

    def generate_thumbnails(self, hero_image_path: Path, script_payload: Dict[str, Any], output_dir: Path,
                            style: str = "badge") -> Tuple[Path, Path]:
        """Cover in one of COVER_STYLES (rotated per run so neighbouring grid posts differ).
        Every layout keeps its text inside the centre 1080x1080 square, which survives both the
        1:1 and the 3:4 profile-grid crops."""
        output_dir.mkdir(parents=True, exist_ok=True)
        out_916_path = output_dir / "thumbnail_cover.jpg"
        out_1x1_path = output_dir / "thumbnail_profile_grid.jpg"
        copy = script_payload["thumbnail"]
        im = cover_crop(hero_image_path, (VIDEO_WIDTH, VIDEO_HEIGHT))
        render = {"lower_third": self._cover_lower_third, "minimal": self._cover_minimal}.get(style, self._cover_badge)
        self._text_cy = calm_center_y(im)  # badge/minimal titles sit where the photo is calmest
        final_916 = render(im, copy).convert("RGB")
        final_916.save(out_916_path, quality=95)
        center_y = VIDEO_HEIGHT // 2
        top = max(0, min(VIDEO_HEIGHT - VIDEO_WIDTH, center_y - VIDEO_WIDTH // 2))
        final_916.crop((0, top, VIDEO_WIDTH, top + VIDEO_WIDTH)).save(out_1x1_path, quality=95)
        log.info("Thumbnails generated (%s): %s, %s", style, out_916_path.name, out_1x1_path.name)
        return out_916_path, out_1x1_path

    @staticmethod
    def _shadowed_text(im: Image.Image, xy, text: str, font, fill, anchor: str) -> Image.Image:
        """Text with a soft drop shadow, for layouts without a backing box."""
        shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((xy[0] + 3, xy[1] + 5), text, font=font, fill=(0, 0, 0, 200), anchor=anchor)
        im = Image.alpha_composite(im, shadow.filter(ImageFilter.GaussianBlur(8)))
        ImageDraw.Draw(im).text(xy, text, font=font, fill=fill, anchor=anchor)
        return im

    def _cover_lower_third(self, im: Image.Image, copy: Dict[str, str]) -> Image.Image:
        """Cinematic: dark gradient rising from the bottom of the square, left-aligned title."""
        eyebrow, line1, line2 = copy["eyebrow"].upper(), copy["line1"].upper(), copy["line2"].upper()
        square_bottom = VIDEO_HEIGHT // 2 + VIDEO_WIDTH // 2
        grad = Image.new("L", (1, VIDEO_HEIGHT), 0)
        for y in range(VIDEO_HEIGHT):  # 0 above the middle, up to ~80% at the square's bottom and below
            grad.putpixel((0, y), int(205 * min(1.0, max(0.0, (y - VIDEO_HEIGHT * 0.45) / (square_bottom - VIDEO_HEIGHT * 0.45)))))
        shade = Image.new("RGBA", im.size, (0, 0, 0, 255))
        shade.putalpha(grad.resize(im.size))
        im = Image.alpha_composite(im, shade)
        draw = ImageDraw.Draw(im)
        x0, width = 110, SAFE_TEXT_WIDTH
        size = min(fit_font(draw, line1, "Anton", 118, width, 56).size, fit_font(draw, line2, "Anton", 118, width, 56).size)
        font_title, font_eye = get_font("Anton", size), fit_font(draw, eyebrow, "Montserrat", 34, width, 22)
        y2 = square_bottom - 150  # clear of the square crop's bottom edge
        y1 = y2 - size - 10
        ye = y1 - size // 2 - 40
        draw.rectangle([x0, ye - 4, x0 + 8, ye + 40], fill=GOLD)
        draw.text((x0 + 26, ye), eyebrow, font=font_eye, fill=GOLD, anchor="lt")
        draw.text((x0, y1), line1, font=font_title, fill=WHITE, anchor="lm")
        draw.text((x0, y2), line2, font=font_title, fill=GOLD, anchor="lm")
        return im

    def _cover_minimal(self, im: Image.Image, copy: Dict[str, str]) -> Image.Image:
        """Minimal: no boxes; a thin gold rule, eyebrow and title with soft shadows, centred."""
        eyebrow, line1, line2 = copy["eyebrow"].upper(), copy["line1"].upper(), copy["line2"].upper()
        veil = Image.new("RGBA", im.size, (0, 0, 0, 70))  # light overall dim keeps white text readable
        im = Image.alpha_composite(im, veil)
        draw = ImageDraw.Draw(im)
        size = min(fit_font(draw, line1, "Anton", 104, SAFE_TEXT_WIDTH, 56).size,
                   fit_font(draw, line2, "Anton", 104, SAFE_TEXT_WIDTH, 56).size)
        font_title, font_eye = get_font("Anton", size), fit_font(draw, eyebrow, "Montserrat", 32, SAFE_TEXT_WIDTH, 22)
        cx, cy = VIDEO_WIDTH // 2, getattr(self, "_text_cy", VIDEO_HEIGHT // 2)
        draw.line([cx - 70, cy - size - 70, cx + 70, cy - size - 70], fill=GOLD, width=4)
        im = self._shadowed_text(im, (cx, cy - size - 30), eyebrow, font_eye, GOLD, "mm")
        im = self._shadowed_text(im, (cx, cy - size // 2), line1, font_title, WHITE, "mm")
        im = self._shadowed_text(im, (cx, cy + size // 2 + 40), line2, font_title, WHITE, "mm")
        return im

    def _cover_badge(self, im: Image.Image, copy: Dict[str, str]) -> Image.Image:
        """The original Variant B: centred title on an 85% halo, gold-outlined eyebrow and tagline pills."""
        eyebrow = copy["eyebrow"].upper()
        line1 = copy["line1"].upper()
        line2 = copy["line2"].upper()
        tagline = copy["tagline"].upper()

        measure = ImageDraw.Draw(im)
        font_eye = fit_font(measure, eyebrow, "Montserrat", THUMB_EYEBROW_FONT_SIZE, SAFE_TEXT_WIDTH, 22)
        title_size = min(
            fit_font(measure, line1, "Anton", THUMB_TITLE_FONT_SIZE, SAFE_TEXT_WIDTH, 56).size,
            fit_font(measure, line2, "Anton", THUMB_TITLE_FONT_SIZE, SAFE_TEXT_WIDTH, 56).size,
        )
        font_title = get_font("Anton", title_size)
        font_tag = fit_font(measure, tagline, "Montserrat", THUMB_TAGLINE_FONT_SIZE, SAFE_TEXT_WIDTH - 68, 24)

        w_eye, h_eye = text_size(measure, eyebrow, font_eye)
        w_l1, h_l1 = text_size(measure, line1, font_title)
        w_l2, h_l2 = text_size(measure, line2, font_title)
        w_tag, h_tag = text_size(measure, tagline, font_tag)

        eye_box_w, eye_box_h = w_eye + 56, h_eye + 28
        tag_box_w, tag_box_h = w_tag + 68, h_tag + 32
        gap_eye, gap_lines, gap_tag = 28, 16, 32

        center_y = getattr(self, "_text_cy", VIDEO_HEIGHT // 2)
        total_h = eye_box_h + gap_eye + h_l1 + gap_lines + h_l2 + gap_tag + tag_box_h
        start_y = center_y - total_h // 2
        title_y0 = start_y + eye_box_h + gap_eye
        title_y1 = title_y0 + h_l1 + gap_lines + h_l2
        title_cy = (title_y0 + title_y1) // 2

        # 85% opacity soft-feathered halo behind the title (locked Variant B)
        bg_w = max(w_l1, w_l2) + 96
        bg_h = (title_y1 - title_y0) + 76
        halo = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle(
            [(VIDEO_WIDTH - bg_w) // 2, title_cy - bg_h // 2, (VIDEO_WIDTH + bg_w) // 2, title_cy + bg_h // 2],
            radius=20, fill=(0, 0, 0, THUMB_OPACITY),
        )
        im = Image.alpha_composite(im, halo.filter(ImageFilter.GaussianBlur(THUMB_BLUR_RADIUS)))
        draw = ImageDraw.Draw(im)
        cx = VIDEO_WIDTH // 2

        draw.rounded_rectangle(
            [cx - eye_box_w // 2, start_y, cx + eye_box_w // 2, start_y + eye_box_h],
            radius=10, fill=(12, 12, 12, 220), outline=(255, 215, 0, 240), width=2,
        )
        draw.text((cx, start_y + eye_box_h // 2), eyebrow, font=font_eye, fill=GOLD, anchor="mm")
        draw.text((cx, title_y0 + h_l1 // 2), line1, font=font_title, fill=WHITE, anchor="mm")
        draw.text((cx, title_y0 + h_l1 + gap_lines + h_l2 // 2), line2, font=font_title, fill=GOLD, anchor="mm")

        tag_y0 = title_y1 + gap_tag
        draw.rounded_rectangle(
            [cx - tag_box_w // 2, tag_y0, cx + tag_box_w // 2, tag_y0 + tag_box_h],
            radius=12, fill=(14, 14, 14, 225), outline=(255, 215, 0, 240), width=2,
        )
        draw.text((cx, tag_y0 + tag_box_h // 2), tagline, font=font_tag, fill=WHITE, anchor="mm")
        return im
