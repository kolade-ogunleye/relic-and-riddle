"""
src/visual_engine.py
====================
Manages visual asset sourcing, scene sequencing, and the locked Variant B Thumbnail Engine.
1. Manages scene image sequences for the 42s video composer.
2. Generates the locked Variant B (85% Opacity) thumbnail cover in dual aspect ratios:
   - 1080x1920 (9:16 Vertical Reel Cover)
   - 1080x1080 (1:1 Instagram Profile Grid Crop)
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

from .config import (
    ASSETS_DIR,
    FONTS_DIR,
    THUMB_OPACITY,
    THUMB_BLUR_RADIUS,
    THUMB_EYEBROW_FONT_SIZE,
    THUMB_TITLE_FONT_SIZE,
    THUMB_TAGLINE_FONT_SIZE,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
)


class VisualEngine:
    def __init__(self, assets_dir: Path = ASSETS_DIR, fonts_dir: Path = FONTS_DIR):
        self.assets_dir = assets_dir
        self.fonts_dir = fonts_dir

    def _get_font(self, font_name: str, size: int) -> ImageFont.FreeTypeFont:
        ttf_file = "Anton-Regular.ttf" if font_name == "Anton" else "Montserrat-Variable.ttf"
        p = self.fonts_dir / ttf_file
        if p.exists():
            return ImageFont.truetype(str(p), size)
        return ImageFont.load_default()

    def get_scene_sequence(self, topic: Dict[str, Any]) -> List[Path]:
        """
        Retrieves the 13-scene sequence for video rendering.
        Detects if a high-impact Kling/AI motion video clip exists for Beat 1 (The Hook),
        and pairs it with 2.5D parallax archival key-art for the rest of the cut.
        """
        tid = topic.get("id", "")
        hook_video = None

        # Check for topic-specific motion hook video (Kling / Gen-3)
        hook_candidates = [
            self.assets_dir / tid / "hook_motion.mp4",
            self.assets_dir / f"{tid}_hook.mp4",
            self.assets_dir / "hook_motion.mp4",
        ]
        for hc in hook_candidates:
            if hc.exists():
                hook_video = hc
                print(f"[VISUAL ENGINE] Hybrid Motion Hook detected: {hc.name} (Beat 1)")
                break

        # Check if topic has custom asset folder
        custom_dir = self.assets_dir / tid
        resolved = []
        if custom_dir.exists():
            imgs = sorted(list(custom_dir.glob("*.jpg")) + list(custom_dir.glob("*.png")))
            if len(imgs) >= 5:
                resolved = imgs

        if not resolved:
            # Archival default suite from assets/
            candidates = [
                "scene1_stairs.jpg",
                "scene07_ozkonak_entrance.jpg",
                "scene2_tunnel.jpg",
                "scene08_ozkonak_tunnel.jpg",
                "scene3_door.jpg",
                "scene09_kaymakli_arch.jpg",
                "scene4_pillars.jpg",
                "scene5_room.jpg",
                "scene13_derinkuyu_church.jpg",
                "scene10_goreme_town.jpg",
                "scene11_panorama.jpg",
                "scene12_uchisar.jpg",
                "scene2_tunnel.jpg"  # Match-cut ending
            ]
            for c in candidates:
                p = self.assets_dir / c
                if p.exists():
                    resolved.append(p)

        if not resolved:
            resolved = list(self.assets_dir.glob("*.jpg"))

        if not resolved:
            raise FileNotFoundError(f"No scene images found in {self.assets_dir}")

        # If motion hook video detected, prepend as Beat 1 (replacing first static image)
        if hook_video:
            resolved = [hook_video] + resolved[1:]

        return resolved

    def generate_thumbnails(
        self,
        hero_image_path: Path,
        script_payload: Dict[str, Any],
        output_dir: Path
    ) -> Tuple[Path, Path]:
        """
        Renders the locked Variant B (85% Opacity) thumbnail cover in:
        1. 9:16 Vertical Reel Cover (1080x1920)
        2. 1:1 Instagram Profile Grid Crop (1080x1080)
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        out_916_path = output_dir / "thumbnail_cover.jpg"
        out_1x1_path = output_dir / "thumbnail_profile_grid.jpg"

        raw = Image.open(hero_image_path).convert("RGBA")

        # -------------------------------------------------------------
        # Typography & Copy
        # -------------------------------------------------------------
        title_text = script_payload.get("title", "The 18-Story Underground City")
        # Split title into 2 punchy lines
        words = title_text.upper().split()
        mid = len(words) // 2
        line1_text = " ".join(words[:mid]) if mid > 0 else words[0]
        line2_text = " ".join(words[mid:]) if mid > 0 else " ".join(words[1:])
        
        # Override for Derinkuyu specific hook for maximum polish
        if "derinkuyu" in script_payload.get("topic_id", "") or "basement" in title_text.lower():
            line1_text = "BEHIND A FAKE WALL..."
            line2_text = "A 280-FT FORBIDDEN CITY"
            eyebrow_text = "DECLASSIFIED ARCHAEOLOGY"
            tagline_text = "THE 1963 DERINKUYU DISCOVERY"
        else:
            eyebrow_text = "ARCHAEOLOGICAL ENIGMA"
            tagline_text = f"THE {script_payload.get('location', {}).get('name', 'ANCIENT').upper()} DISCOVERY"

        font_eye = self._get_font("Montserrat", THUMB_EYEBROW_FONT_SIZE)
        font_title = self._get_font("Anton", THUMB_TITLE_FONT_SIZE)
        font_tag = self._get_font("Montserrat", THUMB_TAGLINE_FONT_SIZE)

        def render_on_canvas(canvas_w: int, canvas_h: int, center_y: int, bg_img: Image.Image) -> Image.Image:
            im = bg_img.copy().convert("RGBA")
            dummy = ImageDraw.Draw(im)

            def get_wh(txt, font):
                bb = dummy.textbbox((0, 0), txt, font=font, anchor="lt")
                return bb[2] - bb[0], bb[3] - bb[1]

            w_eye, h_eye = get_wh(eyebrow_text, font_eye)
            w_l1, h_l1 = get_wh(line1_text, font_title)
            w_l2, h_l2 = get_wh(line2_text, font_title)
            w_tag, h_tag = get_wh(tagline_text, font_tag)

            pad_eye_x, pad_eye_y = 28, 14
            eye_box_w, eye_box_h = w_eye + pad_eye_x * 2, h_eye + pad_eye_y * 2

            pad_tag_x, pad_tag_y = 34, 16
            tag_box_w, tag_box_h = w_tag + pad_tag_x * 2, h_tag + pad_tag_y * 2

            gap_eye_to_title = 28
            gap_title_lines = 16
            gap_title_to_tag = 32

            total_h = eye_box_h + gap_eye_to_title + h_l1 + gap_title_lines + h_l2 + gap_title_to_tag + tag_box_h
            start_y = center_y - total_h // 2

            # Title block boundaries for the dark halo
            title_block_y0 = start_y + eye_box_h + gap_eye_to_title
            title_block_y1 = title_block_y0 + h_l1 + gap_title_lines + h_l2
            title_block_h = title_block_y1 - title_block_y0
            title_center_y = (title_block_y0 + title_block_y1) // 2

            title_pad_y = 38
            title_pad_x = 48
            bg_w = max(w_l1, w_l2) + title_pad_x * 2
            bg_h = title_block_h + title_pad_y * 2

            bg_x0 = (canvas_w - bg_w) // 2
            bg_x1 = bg_x0 + bg_w
            bg_y0 = title_center_y - bg_h // 2
            bg_y1 = title_center_y + bg_h // 2

            # 85% Opacity Soft Feathered Halo (Locked Variant B)
            halo = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            hdraw = ImageDraw.Draw(halo)
            hdraw.rounded_rectangle([bg_x0, bg_y0, bg_x1, bg_y1], radius=20, fill=(0, 0, 0, THUMB_OPACITY))
            halo = halo.filter(ImageFilter.GaussianBlur(THUMB_BLUR_RADIUS))
            im = Image.alpha_composite(im, halo)
            draw = ImageDraw.Draw(im)

            # 1. Eyebrow Badge
            eye_center_x = canvas_w // 2
            eye_box_y0 = start_y
            eye_box_y1 = start_y + eye_box_h
            eye_center_y = (eye_box_y0 + eye_box_y1) // 2
            draw.rounded_rectangle(
                [eye_center_x - eye_box_w // 2, eye_box_y0, eye_center_x + eye_box_w // 2, eye_box_y1],
                radius=10, fill=(12, 12, 12, 220), outline=(255, 215, 0, 240), width=2
            )
            draw.text((eye_center_x, eye_center_y), eyebrow_text, font=font_eye, fill=(255, 215, 0, 255), anchor="mm")

            # 2. Main Title Line 1 (White) & Line 2 (Gold)
            l1_y = title_block_y0 + h_l1 // 2
            l2_y = title_block_y0 + h_l1 + gap_title_lines + h_l2 // 2
            draw.text((canvas_w // 2, l1_y), line1_text, font=font_title, fill=(255, 255, 255, 255), anchor="mm")
            draw.text((canvas_w // 2, l2_y), line2_text, font=font_title, fill=(255, 215, 0, 255), anchor="mm")

            # 3. Tagline Badge
            tag_center_x = canvas_w // 2
            tag_box_y0 = title_block_y1 + gap_title_to_tag
            tag_box_y1 = tag_box_y0 + tag_box_h
            tag_center_y = (tag_box_y0 + tag_box_y1) // 2
            draw.rounded_rectangle(
                [tag_center_x - tag_box_w // 2, tag_box_y0, tag_center_x + tag_box_w // 2, tag_box_y1],
                radius=12, fill=(14, 14, 14, 225), outline=(255, 215, 0, 240), width=2
            )
            draw.text((tag_center_x, tag_center_y), tagline_text, font=font_tag, fill=(255, 255, 255, 255), anchor="mm")

            return im.convert("RGB")

        # -------------------------------------------------------------
        # Render 9:16 Vertical
        # -------------------------------------------------------------
        base_916 = raw.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)
        final_916 = render_on_canvas(VIDEO_WIDTH, VIDEO_HEIGHT, center_y=960, bg_img=base_916)
        final_916.save(out_916_path, quality=97)

        # -------------------------------------------------------------
        # Render 1:1 Profile Grid Crop
        # -------------------------------------------------------------
        crop_1x1 = final_916.crop((0, 450, VIDEO_WIDTH, 1470 + 60))
        crop_1x1 = crop_1x1.resize((1080, 1080), Image.LANCZOS)
        crop_1x1.save(out_1x1_path, quality=97)

        print(f"[VISUAL ENGINE] Locked Variant B thumbnails generated:")
        print(f"  -> 9:16: {out_916_path}")
        print(f"  -> 1:1:  {out_1x1_path}")
        return out_916_path, out_1x1_path


if __name__ == "__main__":
    ve = VisualEngine()
    test_hero = Path(r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57\derinkuyu_discovery_breach_1791761708022.jpg")
    mock_payload = {
        "title": "The 18-Story City Hidden Beneath a Basement",
        "topic_id": "01_derinkuyu_underground_city"
    }
    t916, t1x1 = ve.generate_thumbnails(test_hero, mock_payload, Path("output/test_visual"))
    print("VisualEngine test passed successfully!")
