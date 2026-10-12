"""
src/multi_format.py
===================
Companion assets derived from the Reel's script package, for manual posting:
1. 3-frame Story sequence (1080x1920): hook, revelation, question. The poll itself is
   added as a native Instagram poll sticker when posting; frame 3 leaves room for it and
   the question/options are saved in companion_posting.json.
2. 7-slide carousel (1080x1350, 4:5): cover, matched-photo fact slides, closing question.
Stories and carousels are posted on their own schedule (src/post_schedule.py).
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

    # 4:5 is the tallest ratio Meta's content-publishing API accepts for feed images (4:5 to 1.91:1);
    # the profile grid shows the centre 3:4 of it, so cover text stays in the middle.
    CAROUSEL_SIZE = (1080, 1350)
    PHOTO_H = 760  # fact slides: photo on top, editorial text panel below

    def _match_photos(self, cards: List[Dict[str, Any]], stills: List[Path]) -> List[Path]:
        """One photo per slide, matched to what the slide says (the scene planner's vision matching)."""
        from .scene_planner import plan_scenes
        beats = [{"start": float(i), "end": float(i + 1), "text": f"{c['heading']}. {c['body']}"} for i, c in enumerate(cards)]
        try:
            chosen, _, _ = plan_scenes(beats, stills)
            return [Path(c) for c in chosen]
        except Exception as e:  # matching is a refinement; rotation is the fallback
            log.warning("Carousel photo matching failed (%s); rotating photos.", e)
            return [stills[i % len(stills)] for i in range(len(cards))]

    def generate_carousel_sequence(self, scene_images: List[Path], script_payload: Dict[str, Any], output_dir: Path) -> List[Path]:
        """Cover slide (full-bleed photo, title, swipe cue), fact slides (matched photo above an
        editorial text panel, slide number, progress dots) and a closing question slide."""
        out_dir = output_dir / "carousel"
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in scene_images if not is_video(p)]
        cards = script_payload["carousel_slides"]
        total = len(cards)
        cw, chh = self.CAROUSEL_SIZE
        photos = self._match_photos(cards, stills)
        f_num, f_small = get_font("Anton", 44), get_font("Montserrat", 28)

        def dots(d: ImageDraw.ImageDraw, k: int) -> None:
            x0 = cw // 2 - (total - 1) * 14
            for j in range(total):
                r = 7 if j == k else 5
                d.ellipse([x0 + 28 * j - r, chh - 60 - r, x0 + 28 * j + r, chh - 60 + r],
                          fill=GOLD if j == k else (110, 110, 115))

        slides = []
        for i, card in enumerate(cards):
            heading, body = card["heading"].upper(), card["body"]
            if i == 0:  # cover
                im = cover_crop(photos[i], self.CAROUSEL_SIZE).convert("RGB")
                d = ImageDraw.Draw(im, "RGBA")
                for y in range(chh // 3, chh):  # bottom gradient for the title
                    a = int(235 * ((y - chh / 3) / (chh * 2 / 3)) ** 1.4)
                    d.line([(0, y), (cw, y)], fill=(8, 8, 10, a))
                font_h = fit_font(d, heading, "Anton", 110, cw - 160, 56)
                lines = wrap_text(d, heading, font_h, cw - 160)[:3]
                y = chh - 470 - (len(lines) - 1) * 60
                for line in lines:
                    d.text((80, y), line, font=font_h, fill=WHITE, anchor="ls")
                    y += int(font_h.size * 1.05)
                for j, line in enumerate(wrap_text(d, body, get_font("Montserrat", 36), cw - 160)[:2]):
                    d.text((80, y + 20 + 48 * j), line, font=get_font("Montserrat", 36), fill=LIGHT, anchor="ls")
                d.text((cw - 80, chh - 110), "SWIPE  →", font=f_num, fill=GOLD, anchor="rs")
            else:
                im = Image.new("RGB", self.CAROUSEL_SIZE, (13, 16, 21))
                im.paste(cover_crop(photos[i], (cw, self.PHOTO_H)).convert("RGB"), (0, 0))
                d = ImageDraw.Draw(im, "RGBA")
                for y in range(self.PHOTO_H - 180, self.PHOTO_H):  # photo melts into the panel
                    d.line([(0, y), (cw, y)], fill=(13, 16, 21, int(255 * ((y - (self.PHOTO_H - 180)) / 180) ** 1.6)))
                d.text((80, self.PHOTO_H + 30), f"{i + 1:02d}", font=get_font("Anton", 64), fill=GOLD, anchor="lt")
                font_h = fit_font(d, heading, "Anton", 66, cw - 280, 40)
                d.text((200, self.PHOTO_H + 64), heading, font=font_h, fill=WHITE, anchor="lm")
                font_b = get_font("Montserrat", 42)  # sized for phone reading; the panel has room for 5 lines
                y = self.PHOTO_H + 150
                for line in wrap_text(d, body, font_b, cw - 160)[:5]:
                    d.text((80, y), line, font=font_b, fill=LIGHT, anchor="lt")
                    y += 58
                if i == total - 1:  # closing slide: the invitation to comment
                    d.text((80, chh - 120), "TELL US IN THE COMMENTS  ↓", font=f_small, fill=GOLD, anchor="ls")
            d.text((cw - 80, 70), "RELIC & RIDDLE", font=f_small, fill=(235, 235, 235, 210), anchor="rt")
            dots(d, i)
            path = out_dir / f"carousel_slide_{i + 1:02d}.jpg"
            im.save(path, quality=95)
            slides.append(path)

        log.info("Generated %d carousel slides -> %s", len(slides), out_dir)
        return slides
