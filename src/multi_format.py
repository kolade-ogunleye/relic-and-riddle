"""
src/multi_format.py
===================
Companion assets derived from the Reel's script package, for manual posting:
1. 3-frame Story sequence (1080x1920): hook, revelation, question. The poll itself is
   added as a native Instagram poll sticker when posting; frame 3 leaves room for it and
   the question/options are saved in companion_posting.json.
2. 7-slide carousel (1080x1080).
Stories and carousels are not auto-published (the Reel publishers don't support them).
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List

from PIL import Image, ImageDraw

from .config import VIDEO_WIDTH, VIDEO_HEIGHT
from .typography import cover_crop, fit_font, get_font, is_video, wrap_text

log = logging.getLogger(__name__)

GOLD = (255, 215, 0)
WHITE = (255, 255, 255)
LIGHT = (225, 225, 225)


class MultiFormatGenerator:
    def generate_story_sequence(self, hero_img_path: Path, script_payload: Dict[str, Any], output_dir: Path) -> List[Path]:
        out_dir = output_dir / "stories"
        out_dir.mkdir(parents=True, exist_ok=True)
        base = cover_crop(hero_img_path, (VIDEO_WIDTH, VIDEO_HEIGHT))
        max_w = VIDEO_WIDTH - 2 * 130
        names = ["story_01_hook.jpg", "story_02_reveal.jpg", "story_03_question.jpg"]

        slides = []
        for i, (slide, name) in enumerate(zip(script_payload["story_slides"], names)):
            im = Image.alpha_composite(base.copy(), Image.new("RGBA", base.size, (0, 0, 0, 140 + 15 * i)))
            d = ImageDraw.Draw(im)
            kicker, l1, l2 = slide["kicker"].upper(), slide["line1"].upper(), slide["line2"].upper()
            font_k = fit_font(d, kicker, "Montserrat", 30, max_w, 20)
            size = min(fit_font(d, l1, "Anton", 80, max_w, 48).size, fit_font(d, l2, "Anton", 80, max_w, 48).size)
            font_t = get_font("Anton", size)
            font_b = get_font("Montserrat", 34)
            body_lines = wrap_text(d, slide["body"], font_b, max_w)[:3]

            top = 420
            box_h = 70 + 2 * (size + 14) + 30 + len(body_lines) * 46 + 40
            d.rounded_rectangle([90, top, VIDEO_WIDTH - 90, top + box_h], radius=24,
                                fill=(16, 16, 16, 225), outline=(255, 215, 0, 240), width=2)
            y = top + 50
            d.text((VIDEO_WIDTH // 2, y), kicker, font=font_k, fill=GOLD, anchor="mm")
            y += 30 + size // 2 + 10
            d.text((VIDEO_WIDTH // 2, y), l1, font=font_t, fill=WHITE, anchor="mm")
            y += size + 14
            d.text((VIDEO_WIDTH // 2, y), l2, font=font_t, fill=GOLD, anchor="mm")
            y += size // 2 + 40
            for line in body_lines:
                d.text((VIDEO_WIDTH // 2, y), line, font=font_b, fill=LIGHT, anchor="mm")
                y += 46

            path = out_dir / name
            im.convert("RGB").save(path, quality=95)
            slides.append(path)

        with open(output_dir / "companion_posting.json", "w", encoding="utf-8") as f:
            json.dump({
                "story_poll_sticker": script_payload["poll"],
                "story_frames": [p.name for p in slides],
                "note": "Add the poll as a native sticker on the last frame when posting.",
            }, f, indent=2, ensure_ascii=False)

        log.info("Generated %d Story frames -> %s", len(slides), out_dir)
        return slides

    def generate_carousel_sequence(self, scene_images: List[Path], script_payload: Dict[str, Any], output_dir: Path) -> List[Path]:
        out_dir = output_dir / "carousel"
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in scene_images if not is_video(p)]
        cards = script_payload["carousel_slides"]
        total = len(cards)
        max_w = 1080 - 2 * 130

        slides = []
        for i, card in enumerate(cards):
            im = cover_crop(stills[i % len(stills)], (1080, 1080))
            im = Image.alpha_composite(im, Image.new("RGBA", im.size, (0, 0, 0, 155)))
            d = ImageDraw.Draw(im)
            heading = card["heading"].upper()
            font_h = fit_font(d, heading, "Anton", 72, max_w, 44)
            font_b = get_font("Montserrat", 34)
            body_lines = wrap_text(d, card["body"], font_b, max_w)[:4]

            d.rounded_rectangle([70, 300, 1010, 780], radius=24, fill=(14, 14, 14, 225), outline=(255, 215, 0, 230), width=2)
            d.text((950, 340), f"{i + 1:02d}/{total:02d}", font=get_font("Anton", 36), fill=GOLD, anchor="rt")
            d.text((540, 460), heading, font=font_h, fill=WHITE, anchor="mm")
            y = 590 - (len(body_lines) - 1) * 24
            for line in body_lines:
                d.text((540, y), line, font=font_b, fill=LIGHT, anchor="mm")
                y += 48

            path = out_dir / f"carousel_slide_{i + 1:02d}.jpg"
            im.convert("RGB").save(path, quality=95)
            slides.append(path)

        log.info("Generated %d carousel slides -> %s", len(slides), out_dir)
        return slides
