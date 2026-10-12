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
from .typography import cover_crop, fit_font, get_font, is_video, text_size

log = logging.getLogger(__name__)

GOLD = (255, 215, 0, 255)
WHITE = (255, 255, 255, 255)
SAFE_TEXT_WIDTH = VIDEO_WIDTH - 2 * 110

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


class VisualEngine:
    def __init__(self, assets_dir: Path = ASSETS_DIR):
        self.assets_dir = assets_dir

    def get_scene_sequence(self, topic: Dict[str, Any]) -> List[Path]:
        """
        Scene media for the video, in order. Sources, first match wins:
          assets/<topic_id>/*.jpg|*.png  (sorted by name; at least MIN_SCENE_IMAGES)
          the shared suite, for the topics it actually depicts
        An optional assets/<topic_id>/hook_motion.mp4 replaces the first still (Beat 1).
        """
        tid = topic["id"]
        topic_dir = self.assets_dir / tid
        resolved: List[Path] = []

        if topic_dir.is_dir():
            resolved = sorted(p for p in topic_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
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

    def generate_thumbnails(self, hero_image_path: Path, script_payload: Dict[str, Any], output_dir: Path) -> Tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_916_path = output_dir / "thumbnail_cover.jpg"
        out_1x1_path = output_dir / "thumbnail_profile_grid.jpg"

        copy = script_payload["thumbnail"]
        eyebrow = copy["eyebrow"].upper()
        line1 = copy["line1"].upper()
        line2 = copy["line2"].upper()
        tagline = copy["tagline"].upper()

        im = cover_crop(hero_image_path, (VIDEO_WIDTH, VIDEO_HEIGHT))
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

        center_y = VIDEO_HEIGHT // 2
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

        final_916 = im.convert("RGB")
        final_916.save(out_916_path, quality=95)

        # Square crop centered on the text block
        top = max(0, min(VIDEO_HEIGHT - VIDEO_WIDTH, center_y - VIDEO_WIDTH // 2))
        final_916.crop((0, top, VIDEO_WIDTH, top + VIDEO_WIDTH)).save(out_1x1_path, quality=95)

        log.info("Thumbnails generated: %s, %s", out_916_path.name, out_1x1_path.name)
        return out_916_path, out_1x1_path
