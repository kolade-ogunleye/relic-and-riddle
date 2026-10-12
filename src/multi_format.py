"""
src/multi_format.py
===================
Modular Multi-Format Monetization Multiplier.
Derives companion assets from the locked Reel topic package:
1. 3-Frame Interactive Story Teaser Sequence (9:16 Vertical, 1080x1920)
   - Slide 1: The Sensory Hook & Curated Question
   - Slide 2: The Core Architectural Revelation
   - Slide 3: The Interactive Poll/Debate Sticker Prompt
2. 7-Slide High-Retention Educational Instagram Carousel (1:1 Square, 1080x1080)
   - Engineered for deep dwell-time, carousel bookmarking, and save/share velocity.
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .config import FONTS_DIR, VIDEO_WIDTH, VIDEO_HEIGHT


class MultiFormatGenerator:
    def __init__(self, fonts_dir: Path = FONTS_DIR):
        self.fonts_dir = fonts_dir

    def _get_font(self, font_name: str, size: int) -> ImageFont.FreeTypeFont:
        ttf_file = "Anton-Regular.ttf" if font_name == "Anton" else "Montserrat-Variable.ttf"
        p = self.fonts_dir / ttf_file
        if p.exists():
            return ImageFont.truetype(str(p), size)
        return ImageFont.load_default()

    # --------------------------------------------------------------------------
    # 3-FRAME INTERACTIVE STORY TEASER (9:16)
    # --------------------------------------------------------------------------
    def generate_story_sequence(
        self,
        hero_img_path: Path,
        script_payload: Dict[str, Any],
        output_dir: Path,
    ) -> List[Path]:
        """
        Renders a 3-part interactive 9:16 Story sequence.
        """
        out_story_dir = output_dir / "stories"
        out_story_dir.mkdir(parents=True, exist_ok=True)
        raw_bg = Image.open(hero_img_path).convert("RGBA").resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)

        font_anton = self._get_font("Anton", 80)
        font_mont = self._get_font("Montserrat", 36)
        font_badge = self._get_font("Montserrat", 30)

        slides = []

        # Slide 1: Hook Teaser
        s1 = raw_bg.copy()
        draw1 = ImageDraw.Draw(s1)
        # Dark vignette overlay
        overlay1 = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 140))
        s1 = Image.alpha_composite(s1, overlay1)
        d1 = ImageDraw.Draw(s1)
        d1.rounded_rectangle([100, 400, VIDEO_WIDTH - 100, 680], radius=24, fill=(16, 16, 16, 220), outline=(255, 215, 0, 240), width=2)
        d1.text((VIDEO_WIDTH // 2, 450), "DID YOU KNOW?", font=font_badge, fill=(255, 215, 0), anchor="mm")
        d1.text((VIDEO_WIDTH // 2, 540), "A MAN KNOCKED DOWN", font=font_anton, fill=(255, 255, 255), anchor="mm")
        d1.text((VIDEO_WIDTH // 2, 620), "HIS BASEMENT WALL...", font=font_anton, fill=(255, 215, 0), anchor="mm")
        # Bottom swipe-up indicator
        d1.text((VIDEO_WIDTH // 2, 1650), "Tap Next to Reveal →", font=font_mont, fill=(240, 240, 240), anchor="mm")
        p1 = out_story_dir / "story_01_hook.jpg"
        s1.convert("RGB").save(p1, quality=95)
        slides.append(p1)

        # Slide 2: The Core Revelation
        s2 = raw_bg.copy()
        overlay2 = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 160))
        s2 = Image.alpha_composite(s2, overlay2)
        d2 = ImageDraw.Draw(s2)
        d2.rounded_rectangle([80, 450, VIDEO_WIDTH - 80, 800], radius=24, fill=(16, 16, 16, 225), outline=(255, 215, 0, 240), width=2)
        d2.text((VIDEO_WIDTH // 2, 510), "THE 18-STORY FORBIDDEN CITY", font=font_badge, fill=(255, 215, 0), anchor="mm")
        d2.text((VIDEO_WIDTH // 2, 600), "SHELTERED 20,000 PEOPLE", font=font_anton, fill=(255, 255, 255), anchor="mm")
        d2.text((VIDEO_WIDTH // 2, 680), "280 FEET UNDERGROUND", font=font_anton, fill=(255, 215, 0), anchor="mm")
        d2.text((VIDEO_WIDTH // 2, 750), "With water wells & 15,000 ventilation shafts.", font=font_mont, fill=(220, 220, 220), anchor="mm")
        p2 = out_story_dir / "story_02_scale.jpg"
        s2.convert("RGB").save(p2, quality=95)
        slides.append(p2)

        # Slide 3: Interactive Poll Sticker Prompt
        s3 = raw_bg.copy()
        overlay3 = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 175))
        s3 = Image.alpha_composite(s3, overlay3)
        d3 = ImageDraw.Draw(s3)
        d3.rounded_rectangle([80, 500, VIDEO_WIDTH - 80, 950], radius=24, fill=(16, 16, 16, 230), outline=(255, 215, 0, 240), width=2)
        d3.text((VIDEO_WIDTH // 2, 570), "THE ULTIMATE QUESTION:", font=font_badge, fill=(255, 215, 0), anchor="mm")
        d3.text((VIDEO_WIDTH // 2, 650), "WOULD YOU VENTURE INSIDE?", font=font_anton, fill=(255, 255, 255), anchor="mm")
        # Simulated native voting buttons
        d3.rounded_rectangle([140, 720, VIDEO_WIDTH - 140, 800], radius=16, fill=(35, 35, 35, 240), outline=(255, 255, 255, 180), width=1)
        d3.text((VIDEO_WIDTH // 2, 760), "YES — I must explore it!", font=font_mont, fill=(255, 255, 255), anchor="mm")
        d3.rounded_rectangle([140, 830, VIDEO_WIDTH - 140, 910], radius=16, fill=(35, 35, 35, 240), outline=(255, 255, 255, 180), width=1)
        d3.text((VIDEO_WIDTH // 2, 870), "NO WAY — Leave it sealed!", font=font_mont, fill=(255, 255, 255), anchor="mm")
        d3.text((VIDEO_WIDTH // 2, 1650), "Watch the full documentary in our latest Reel 🎬", font=font_mont, fill=(240, 240, 240), anchor="mm")
        p3 = out_story_dir / "story_03_poll.jpg"
        s3.convert("RGB").save(p3, quality=95)
        slides.append(p3)

        print(f"[MULTI-FORMAT] Generated {len(slides)} companion Story slides -> {out_story_dir}")
        return slides

    # --------------------------------------------------------------------------
    # 7-SLIDE HIGH-RETENTION CAROUSEL (1:1)
    # --------------------------------------------------------------------------
    def generate_carousel_sequence(
        self,
        scene_images: List[Path],
        script_payload: Dict[str, Any],
        output_dir: Path,
    ) -> List[Path]:
        """
        Renders a 7-slide educational Instagram Carousel (1080x1080).
        """
        out_car_dir = output_dir / "carousel"
        out_car_dir.mkdir(parents=True, exist_ok=True)

        font_title = self._get_font("Anton", 72)
        font_body = self._get_font("Montserrat", 32)
        font_num = self._get_font("Anton", 36)

        slides = []
        texts = [
            ("BEHIND A FAKE WALL...", "The 1963 discovery of the 18-story underground city.", "01/07"),
            ("THE ACCIDENTAL DISCOVERY", "A Turkish homeowner noticed his chickens disappearing into a crevice in the wall.", "02/07"),
            ("IMPOSSIBLE DEPTH", "Descends 280 feet into solid volcanic rock across 18 subterranean tiers.", "03/07"),
            ("FRESH AIR AT 280 FEET", "Over 15,000 stone ventilation shafts kept air circulating to the lowest depths.", "04/07"),
            ("1,000-POUND ROLLING DOORS", "Massive stone wheels could only be opened or locked from the inside.", "05/07"),
            ("AN UNSOLVED THREAT", "Who forced 20,000 citizens to flee into complete darkness?", "06/07"),
            ("WHAT IS YOUR THEORY?", "Drop your theory below and follow for daily ancient marvels.", "07/07"),
        ]

        for i, (heading, body, counter) in enumerate(texts):
            bg_source = scene_images[i % len(scene_images)]
            im = Image.open(bg_source).convert("RGBA").resize((1080, 1080), Image.LANCZOS)
            # Dark card overlay
            overlay = Image.new("RGBA", (1080, 1080), (0, 0, 0, 155))
            im = Image.alpha_composite(im, overlay)
            draw = ImageDraw.Draw(im)

            # Card Box
            draw.rounded_rectangle([70, 220, 1010, 860], radius=24, fill=(14, 14, 14, 225), outline=(255, 215, 0, 230), width=2)
            # Counter Badge
            draw.text((950, 260), counter, font=font_num, fill=(255, 215, 0), anchor="rt")
            # Heading
            draw.text((540, 380), heading, font=font_title, fill=(255, 255, 255), anchor="mm")
            # Body text
            draw.text((540, 560), body, font=font_body, fill=(230, 230, 230), anchor="mm")

            p = out_car_dir / f"carousel_slide_{i+1:02d}.jpg"
            im.convert("RGB").save(p, quality=95)
            slides.append(p)

        print(f"[MULTI-FORMAT] Generated {len(slides)} companion Carousel slides -> {out_car_dir}")
        return slides


if __name__ == "__main__":
    gen = MultiFormatGenerator()
    test_hero = Path("output/test_visual/thumbnail_cover.jpg")
    mock_payload = {"title": "The 18-Story City"}
    s_slides = gen.generate_story_sequence(test_hero, mock_payload, Path("output/test_multiformat"))
    c_slides = gen.generate_carousel_sequence([test_hero] * 7, mock_payload, Path("output/test_multiformat"))
    print(f"MultiFormat test passed: {len(s_slides)} stories, {len(c_slides)} carousel slides.")
