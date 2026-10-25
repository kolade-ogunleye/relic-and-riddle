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
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw

from .config import VIDEO_WIDTH, VIDEO_HEIGHT
from .typography import cover_crop, fit_font, get_font, is_video, wrap_text

log = logging.getLogger(__name__)

GOLD = (255, 215, 0)
WHITE = (255, 255, 255)
LIGHT = (225, 225, 225)


def split_balanced(text: str) -> Tuple[str, str]:
    """Splits a headline into two lines of similar length at a word boundary."""
    words = text.split()
    if len(words) < 2:
        return text, ""
    best = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
    return " ".join(words[:best]), " ".join(words[best:])


def _rel_luminance(rgb) -> float:
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ensure_legible(im: Image.Image, box: Tuple[int, int, int, int], text_rgb=(235, 235, 235),
                   target: float = 4.5, feather: int = 60) -> Image.Image:
    """Darkens the area behind a block of light text until the text reaches WCAG contrast `target`
    against the bright end of the background there (its 90th-percentile luminance), with feathered
    edges so no box shows. Bright photos get a deeper shade; dark ones are left alone."""
    import numpy as np
    x0, y0, x1, y1 = [int(v) for v in box]
    region = np.asarray(im.convert("RGB").crop((x0, y0, x1, y1)), dtype=np.float32) / 255.0
    weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

    def bright_end(alpha: float) -> float:  # 90th-percentile luminance once shaded by `alpha`
        v = region * (1.0 - alpha)
        lin = np.where(v <= 0.03928, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
        return float(np.percentile(lin @ weights, 90))

    needed = (_rel_luminance(text_rgb) + 0.05) / target - 0.05  # the brightest background that still gives `target`
    if bright_end(0.0) <= needed:
        return im
    lo, hi = 0.0, 0.92
    for _ in range(12):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if bright_end(mid) <= needed else (mid, hi)
    alpha = min(0.92, hi + 0.03)
    from PIL import ImageChops, ImageFilter
    level = int(round(255 * alpha))
    box_only, grown = Image.new("L", im.size, 0), Image.new("L", im.size, 0)
    ImageDraw.Draw(box_only).rectangle([x0, y0, x1, y1], fill=level)
    ImageDraw.Draw(grown).rectangle([x0 - feather, y0 - feather, x1 + feather, y1 + feather], fill=level)
    # Full strength over the whole box (text may sit at its edges), fading out over `feather` px outside it
    shade = ImageChops.lighter(grown.filter(ImageFilter.GaussianBlur(feather / 3)), box_only)
    black = Image.new("RGB", im.size, (0, 0, 0))
    return Image.composite(black, im.convert("RGB"), shade)


class MultiFormatGenerator:
    def generate_story_sequence(self, hero_img_path: Path, script_payload: Dict[str, Any], output_dir: Path,
                                scene_images: List[Path] = None) -> List[Path]:
        """Three Story frames, each on its own matched photo: hook, reveal, and an A-or-B question
        (the native poll sticker can't be posted through the API, so the question invites replies).
        Text sits on the calmest band of the photo inside Story-safe zones (clear of the profile bar
        at the top and the reply bar at the bottom), over a soft gradient rather than a box."""
        from .visual_engine import calm_center_y
        out_dir = output_dir / "stories"
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in (scene_images or []) if not is_video(p)] or [hero_img_path]
        slides_data = script_payload["story_slides"]
        cards = [{"heading": f"{s['line1']} {s['line2']}", "body": s["body"]} for s in slides_data]
        photos = self._match_photos(cards, stills) if len(stills) > 1 else [hero_img_path] * len(cards)
        # The text block stays inside the area Instagram and Facebook leave free on a Story (safe_zones.STORY:
        # Meta's 14% top, 35% bottom, 6% sides), clear of the profile bar, the reply bar and the call-to-action.
        from .safe_zones import safe_box
        sx0, sy0, sx1, sy1 = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
        max_w = min(VIDEO_WIDTH - 2 * 110, sx1 - sx0)
        names = ["story_01_hook.jpg", "story_02_reveal.jpg", "story_03_question.jpg"]
        poll = script_payload.get("poll") or {}
        layout = []  # each frame's text block, for the audit and the evidence renders

        slides = []
        for i, (slide, name) in enumerate(zip(slides_data, names)):
            base = cover_crop(photos[i], (VIDEO_WIDTH, VIDEO_HEIGHT)).convert("RGB")
            cy = calm_center_y(base, candidates=(640, 790, 940), band=520)  # bands inside the Story box
            d = ImageDraw.Draw(base, "RGBA")
            # Global 35% dark underlay across the entire base image to tame bright sunlit stone/sand
            d.rectangle([(0, 0), (VIDEO_WIDTH, VIDEO_HEIGHT)], fill=(6, 6, 8, 90))
            for y in range(VIDEO_HEIGHT):  # soft vignette band behind the text, darkest at its centre
                k = max(0.0, 1 - abs(y - cy) / 520)
                d.line([(0, y), (VIDEO_WIDTH, y)], fill=(6, 6, 8, int(220 * k ** 1.3)))
            kicker, l1, l2 = slide["kicker"].upper(), slide["line1"].upper(), slide["line2"].upper()
            if i == len(slides_data) - 1 and len(poll.get("options", [])) >= 2 and poll.get("question"):
                l1, l2 = split_balanced(poll["question"].upper())  # the frame asks the question its options answer
            font_k = fit_font(d, kicker, "Montserrat", 34, max_w, 22)
            size = min(fit_font(d, l1, "Anton", 104, max_w, 56).size, fit_font(d, l2, "Anton", 104, max_w, 56).size)
            font_t, font_b = get_font("Anton", size), get_font("Montserrat", 40)
            has_options = i == len(slides_data) - 1 and len(poll.get("options", [])) >= 2
            body = slide["body"]
            if has_options:  # one question per frame: the poll's; the body keeps only its context
                body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))
            body_lines = wrap_text(d, body, font_b, max_w)[:3] if body.strip() else []
            block = 60 + 2 * (size + 12) + 40 + len(body_lines) * 54 + (40 + 2 * 120 + 70 if has_options else (90 if i == 0 else 20))
            # Keep the whole block (with options or the Reel pointer) inside the Story box.
            y = max(sy0 + 20, min(cy - block // 2, sy1 - block))
            y_top = y
            # Translucent dark charcoal pill behind kicker for WCAG AAA legibility (mathematically centered)
            kx = VIDEO_WIDTH // 2
            k_cy = y + 16
            pad_x, pad_y = 26, 12
            k_bbox = d.textbbox((kx, k_cy), kicker, font=font_k, anchor="mm")
            pill_box = [k_bbox[0] - pad_x, k_bbox[1] - pad_y, k_bbox[2] + pad_x, k_bbox[3] + pad_y]
            d.rounded_rectangle(pill_box, radius=14, fill=(10, 10, 14, 215), outline=(255, 215, 0, 180), width=2)
            d.text((kx, k_cy), kicker, font=font_k, fill=GOLD, anchor="mm")
            y = pill_box[3] + 20
            d.text((VIDEO_WIDTH // 2, y), l1, font=font_t, fill=WHITE, anchor="mt", stroke_width=3, stroke_fill=(10, 10, 12, 230))
            y += size + 12
            d.text((VIDEO_WIDTH // 2, y), l2, font=font_t, fill=GOLD, anchor="mt", stroke_width=3, stroke_fill=(10, 10, 12, 230))
            y += size + 40
            for line in body_lines:
                d.text((VIDEO_WIDTH // 2, y), line, font=font_b, fill=LIGHT, anchor="mt", stroke_width=2, stroke_fill=(10, 10, 12, 220))
                y += 54
            y_end = y
            if i == 0:
                d.text((VIDEO_WIDTH // 2, y + 30), "FULL STORY IN OUR NEW REEL  →", font=get_font("Montserrat", 32),
                       fill=GOLD, anchor="mt", stroke_width=2, stroke_fill=(10, 10, 12, 220))
                y_end = y + 30 + 40
            if has_options:
                font_o = get_font("Anton", 42)
                ox0, ox1 = max(120, sx0), min(VIDEO_WIDTH - 120, sx1)
                for j, (prefix, option) in enumerate(zip(["A", "B"], poll["options"][:2])):
                    oy = y + 40 + j * 120
                    d.rounded_rectangle([ox0, oy, ox1, oy + 96], radius=24, fill=(10, 10, 12, 215),
                                        outline=(255, 215, 0, 230), width=2)
                    d.text((ox0 + 30, oy + 48), prefix, font=font_o, fill=GOLD, anchor="lm")
                    opt = option.upper()
                    d.text((ox0 + 100, oy + 48), opt, font=fit_font(d, opt, "Montserrat", 34, ox1 - ox0 - 100 - 30, 22),
                           fill=WHITE, anchor="lm")
                d.text((VIDEO_WIDTH // 2, y + 40 + 2 * 120 + 20), "WEIGH IN BELOW  ↓", font=get_font("Montserrat", 32),
                       fill=LIGHT, anchor="mt", stroke_width=2, stroke_fill=(10, 10, 12, 220))
                y_end = y + 40 + 2 * 120 + 20 + 40
            layout.append({"frame": name, "size": [VIDEO_WIDTH, VIDEO_HEIGHT],
                           "text_box": [(VIDEO_WIDTH - max_w) // 2, int(pill_box[1]), (VIDEO_WIDTH + max_w) // 2, int(y_end)],
                           "safe_box": [sx0, sy0, sx1, sy1]})
            path = out_dir / name
            base.save(path, quality=95)
            slides.append(path)
        (out_dir / "layout.json").write_text(json.dumps(layout, indent=2), encoding="utf-8")

        # Multi-platform publishing manifest with dedicated platform packages
        hashtags_list = script_payload.get("hashtags") or ["#Archaeology", "#AncientHistory", "#HistoryBuff", "#AncientTechnology"]
        hashtags_str = " ".join(hashtags_list)
        first_comment = script_payload.get("first_comment", "")
        title = script_payload.get("title", "")
        clean_title = re.sub(r"[^\w\s\-\:\,\']", "", title).strip()
        if len(clean_title) > 60:
            clean_title = clean_title[:57].rstrip() + "..."

        ig_caption = script_payload.get("caption", "")
        fb_caption = f"{clean_title}\n\n{first_comment}\n\nWeigh in with your thoughts in the discussion below."
        yt_desc = f"{title}\n\n{script_payload.get('narration_script', '')}\n\n{first_comment}\n\n{hashtags_str}"
        yt_tags = [h.lstrip("#").lower() for h in hashtags_list] + ["archaeology", "ancient history", "documentary", "history mysteries"]

        companion_manifest = {
            "story_poll_sticker": script_payload.get("poll", {}),
            "story_frames": [p.name for p in slides],
            "first_comment": first_comment,
            "platforms": {
                "instagram_reels": {
                    "caption": ig_caption,
                    "hashtags": hashtags_list,
                    "first_comment": first_comment,
                    "recommended_sound": "Original Audio",
                },
                "facebook_reels": {
                    "caption": fb_caption,
                    "first_comment": first_comment,
                    "discussion_prompt": first_comment,
                },
                "youtube_shorts": {
                    "title": clean_title,
                    "description": yt_desc,
                    "tags": yt_tags,
                    "pinned_comment": first_comment,
                },
            },
            "note": "Multi-platform publishing manifest: native Instagram poll sticker specs, Facebook MSI discussion copy, and YouTube Shorts SEO metadata.",
        }

        with open(output_dir / "companion_posting.json", "w", encoding="utf-8") as f:
            json.dump(companion_manifest, f, indent=2, ensure_ascii=False)

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

    @staticmethod
    def route_carousel_archetype(card: Dict[str, Any], index: int, total: int) -> str:
        """Semantic intent router that selects the optimal editorial archetype."""
        if index == 0:
            return "cover"
        if index == total - 1:
            return "debate"
        text = (card.get("heading", "") + " " + card.get("body", "")).lower()
        stat_patterns = [
            r"\b\d+[\d,]*\s*(?:tons?|people|feet|ft|meters?|m|stories|levels|degrees|bce?|years?)\b",
            r"\b\d+[,.]\d+\b",
        ]
        if any(re.search(p, text) for p in stat_patterns) and any(w in text for w in ("tons", "people", "deep", "stories", "levels", "feet", "meters", "capacity")):
            return "colossal_stat"
        if any(w in text for w in ("versus", "vs", "compared", "surface", "unlike", "contrast", "above and below", "then and now", "modern")):
            return "comparison"
        if any(w in text for w in ("shaft", "ventilation", "channel", "door", "rolling stone", "tunnel", "chiseled", "engineered", "mechanism", "structure", "architecture", "block")):
            return "blueprint"
        return "immersive" if index % 2 == 1 else "editorial_split"

    @staticmethod
    def _get_card_category(card: Dict[str, Any], archetype: str) -> str:
        """Derives a dynamic editorial category label for the slide badge."""
        text = (card.get("heading", "") + " " + card.get("body", "")).lower()
        if archetype == "debate":
            return "FORENSIC DEBATE"
        if archetype == "blueprint" or any(w in text for w in ("blueprint", "metric", "engineered", "shaft", "ventilation", "channel", "door")):
            return "ENGINEERING BLUEPRINT"
        if archetype == "comparison" or any(w in text for w in ("versus", "vs", "compare", "surface", "contrast", "above and below")):
            return "COMPARATIVE EVIDENCE"
        if archetype == "colossal_stat" or any(w in text for w in ("tons", "feet", "meters", "capacity", "levels", "stories")):
            return "ARCHAEOLOGICAL METRIC"
        if any(w in text for w in ("carved", "rock", "stone", "tuff", "excavated", "cut")):
            return "ARCHIVAL EVIDENCE"
        if any(w in text for w in ("who", "built", "dug", "phrygian", "hittite", "byzantine", "mystery", "enigma")):
            return "HISTORICAL ENIGMA"
        if any(w in text for w in ("discovery", "found", "breach", "basement", "revealed", "chickens", "wall")):
            return "DISCOVERY RECORD"
        return "FIELD INVESTIGATION"

    @staticmethod
    def _draw_badge(
        d: ImageDraw.ImageDraw,
        text: str,
        x0: int,
        y0: int,
        font: Any,
        text_color: Any = GOLD,
        border_color: Any = GOLD,
        fill_color: Any = (12, 16, 22, 230),
        pad_x: int = 18,
        pad_y: int = 8,
        radius: int = 4,
        border_width: int = 1,
    ) -> Tuple[int, int, int, int]:
        """Draws an editorial badge/pill with mathematically centered horizontal and vertical text alignment."""
        bbox = d.textbbox((0, 0), text, font=font, anchor="mm")
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x1 = x0 + tw + 2 * pad_x
        y1 = y0 + th + 2 * pad_y
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill_color, outline=border_color, width=border_width)
        d.text((cx, cy), text, font=font, fill=text_color, anchor="mm")
        return (int(x0), int(y0), int(x1), int(y1))

    _numbered = True

    def _tag(self, category: str, index: int) -> str:
        """A slide's badge: its category, with its number on the swipe copy (Instagram) only."""
        return f"{category} // {index + 1:02d}" if self._numbered else category

    def _render_cover(self, photo: Path, card: Dict[str, Any], cw: int, chh: int, swipe: bool = True) -> Image.Image:
        heading, body = card["heading"].upper(), card["body"]
        im = cover_crop(photo, self.CAROUSEL_SIZE).convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(chh // 3, chh):
            a = int(235 * ((y - chh / 3) / (chh * 2 / 3)) ** 1.4)
            d.line([(0, y), (cw, y)], fill=(8, 8, 10, a))
        im = ensure_legible(im, (40, chh - 720, cw - 40, chh - 80))  # up to 3 heading lines, body and swipe
        d = ImageDraw.Draw(im, "RGBA")
        font_h = fit_font(d, heading, "Anton", 110, cw - 160, 56)
        lines = wrap_text(d, heading, font_h, cw - 160)[:3]
        y = chh - 470 - (len(lines) - 1) * 60
        for line in lines:
            d.text((80, y), line, font=font_h, fill=WHITE, anchor="ls", stroke_width=3, stroke_fill=(8, 8, 10, 230))
            y += int(font_h.size * 1.05)
        for j, line in enumerate(wrap_text(d, body, get_font("Montserrat", 36), cw - 160)[:2]):
            d.text((80, y + 20 + 48 * j), line, font=get_font("Montserrat", 36), fill=LIGHT, anchor="ls", stroke_width=2, stroke_fill=(8, 8, 10, 220))
        if swipe:
            d.text((cw - 80, chh - 110), "SWIPE  →", font=get_font("Anton", 44), fill=GOLD, anchor="rs", stroke_width=2, stroke_fill=(8, 8, 10, 220))
        return im

    def _render_immersive(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int) -> Image.Image:
        """Archetype A: Full-Bleed Immersive Photo with floating translucent charcoal badges."""
        heading, body = card["heading"].upper(), card["body"]
        im = cover_crop(photo, self.CAROUSEL_SIZE).convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle([(0, 0), (cw, chh)], fill=(6, 8, 12, 70))
        for y in range(chh // 2, chh):
            a = int(220 * ((y - chh / 2) / (chh / 2)) ** 1.3)
            d.line([(0, y), (cw, y)], fill=(8, 10, 14, a))
        im = ensure_legible(im, (40, chh - 560, cw - 40, chh - 50))  # badge, heading and up to 4 body lines
        d = ImageDraw.Draw(im, "RGBA")
        category = self._get_card_category(card, "immersive")
        tag = self._tag(category, index)
        f_tag = get_font("Montserrat", 24)
        self._draw_badge(d, tag, 80, chh - 530, f_tag, text_color=GOLD, border_color=GOLD, border_width=2, radius=8)
        font_h = fit_font(d, heading, "Anton", 76, cw - 160, 44)
        h_lines = wrap_text(d, heading, font_h, cw - 160)[:2]
        y = chh - 440
        for line in h_lines:
            d.text((80, y), line, font=font_h, fill=WHITE, anchor="lt", stroke_width=3, stroke_fill=(6, 8, 10, 240))
            y += int(font_h.size * 1.08)
        y += 18
        font_b = get_font("Montserrat", 40)
        for line in wrap_text(d, body, font_b, cw - 160)[:4]:
            d.text((80, y), line, font=font_b, fill=LIGHT, anchor="lt", stroke_width=2, stroke_fill=(6, 8, 10, 220))
            y += 56
        return im

    def _render_comparison(self, photo1: Path, photo2: Path, card: Dict[str, Any], index: int, cw: int, chh: int) -> Image.Image:
        """Archetype B: Side-by-Side Comparative Card."""
        heading, body = card["heading"].upper(), card["body"]
        im = Image.new("RGB", (cw, chh), (12, 14, 18))
        h_half = 380
        im.paste(cover_crop(photo1, (cw, h_half)).convert("RGB"), (0, 0))
        im.paste(cover_crop(photo2, (cw, h_half)).convert("RGB"), (0, h_half))
        d = ImageDraw.Draw(im, "RGBA")
        d.line([(0, h_half), (cw, h_half)], fill=GOLD, width=3)
        d.line([(0, 2 * h_half), (cw, 2 * h_half)], fill=GOLD, width=2)
        f_sub = get_font("Montserrat", 22)
        t_ref_a = "REF A // SURFACE CONTEXT"
        self._draw_badge(d, t_ref_a, 60, 35, f_sub, text_color=WHITE, border_color=GOLD, fill_color=(10, 12, 16, 220), radius=6)

        t_ref_b = "REF B // SUBTERRANEAN DEPTH"
        self._draw_badge(d, t_ref_b, 60, h_half + 35, f_sub, text_color=GOLD, border_color=GOLD, fill_color=(10, 12, 16, 220), radius=6)

        category = self._get_card_category(card, "comparison")
        tag = self._tag(category, index)
        f_tag = get_font("Montserrat", 24)
        self._draw_badge(d, tag, 80, 2 * h_half + 25, f_tag, text_color=GOLD, border_color=GOLD, radius=6)
        font_h = fit_font(d, heading, "Anton", 64, cw - 160, 38)
        d.text((80, 2 * h_half + 85), heading, font=font_h, fill=WHITE, anchor="lt")
        font_b = get_font("Montserrat", 38)
        y = 2 * h_half + 155
        for line in wrap_text(d, body, font_b, cw - 160)[:4]:
            d.text((80, y), line, font=font_b, fill=LIGHT, anchor="lt")
            y += 52
        return im

    def _render_blueprint(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int) -> Image.Image:
        """Archetype C: Sourced Measurement & Blueprint Card."""
        heading, body = card["heading"].upper(), card["body"]
        im = Image.new("RGB", (cw, chh), (10, 16, 24))
        p_h = 620
        im.paste(cover_crop(photo, (cw, p_h)).convert("RGB"), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for x in range(60, cw, 70):
            d.line([(x, p_h), (x, chh)], fill=(20, 36, 52, 70), width=1)
        for y_g in range(p_h, chh, 70):
            d.line([(0, y_g), (cw, y_g)], fill=(20, 36, 52, 70), width=1)
        d.line([(0, p_h), (cw, p_h)], fill=GOLD, width=3)
        category = self._get_card_category(card, "blueprint")
        tag = self._tag(category, index)
        f_tech = get_font("Montserrat", 24)
        self._draw_badge(d, tag, 80, p_h + 25, f_tech, text_color=GOLD, border_color=GOLD, fill_color=(8, 12, 18, 230), radius=4)
        font_h = fit_font(d, heading, "Anton", 64, cw - 160, 38)
        d.text((80, p_h + 85), heading, font=font_h, fill=WHITE, anchor="lt")
        font_b = get_font("Montserrat", 40)
        y = p_h + 155
        for line in wrap_text(d, body, font_b, cw - 160)[:5]:
            d.text((80, y), line, font=font_b, fill=LIGHT, anchor="lt")
            y += 56
        return im

    def _render_colossal_stat(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int) -> Image.Image:
        """Archetype D: High-Impact Colossal Stat Card."""
        heading, body = card["heading"].upper(), card["body"]
        im = Image.new("RGB", (cw, chh), (11, 14, 18))
        p_h = 520
        im.paste(cover_crop(photo, (cw, p_h)).convert("RGB"), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(p_h - 140, p_h):
            a = int(255 * ((y - (p_h - 140)) / 140) ** 1.5)
            d.line([(0, y), (cw, y)], fill=(11, 14, 18, a))
        category = self._get_card_category(card, "colossal_stat")
        tag = self._tag(category, index)
        f_tag = get_font("Montserrat", 24)
        self._draw_badge(d, tag, 80, p_h + 10, f_tag, text_color=GOLD, border_color=GOLD, fill_color=(16, 20, 26, 230), radius=6)
        stat_match = re.search(r"(\b\d+[\d,]*\+?(?:\s*(?:TONS?|PEOPLE|FEET|FT|METERS?|M|STORIES|LEVELS))?)\b", f"{heading} {body}", re.IGNORECASE)
        stat_text = stat_match.group(1).upper() if stat_match else (f"{index + 1:02d} DATA" if self._numbered else category)
        f_stat = fit_font(d, stat_text, "Anton", 110, cw - 160, 68)
        d.text((80, p_h + 65), stat_text, font=f_stat, fill=GOLD, anchor="lt")
        y = p_h + 65 + int(f_stat.size * 1.1)
        font_h = fit_font(d, heading, "Anton", 62, cw - 160, 38)
        d.text((80, y), heading, font=font_h, fill=WHITE, anchor="lt")
        y += int(font_h.size * 1.15)
        font_b = get_font("Montserrat", 38)
        for line in wrap_text(d, body, font_b, cw - 160)[:4]:
            d.text((80, y), line, font=font_b, fill=LIGHT, anchor="lt")
            y += 52
        ver_font = get_font("Montserrat", 22)
        t_ver = "VERIFIED BY ARCHEOLOGICAL RECORD"
        self._draw_badge(d, t_ver, 80, chh - 140, ver_font, text_color=GOLD, border_color=(255, 215, 0, 180), fill_color=(16, 20, 26, 230), radius=6)
        return im

    def _render_debate(self, photo: Path, card: Dict[str, Any], index: int, total: int, cw: int, chh: int, poll: Dict[str, Any], f_small) -> Image.Image:
        """Archetype E: Forensic Closing Dilemma / Debate Card."""
        heading, body = card["heading"].upper(), card["body"]
        im = Image.new("RGB", (cw, chh), (12, 14, 18))
        p_h = 560
        im.paste(cover_crop(photo, (cw, p_h)).convert("RGB"), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(p_h - 160, p_h):
            a = int(255 * ((y - (p_h - 160)) / 160) ** 1.5)
            d.line([(0, y), (cw, y)], fill=(12, 14, 18, a))
        category = self._get_card_category(card, "debate")
        tag = self._tag(category, index)
        f_tag = get_font("Montserrat", 24)
        self._draw_badge(d, tag, 80, p_h + 20, f_tag, text_color=GOLD, border_color=GOLD, fill_color=(16, 18, 22, 230), radius=6)
        font_h = fit_font(d, heading, "Anton", 68, cw - 160, 42)
        d.text((80, p_h + 80), heading, font=font_h, fill=WHITE, anchor="lt")
        font_b = get_font("Montserrat", 40)
        y = p_h + 80 + int(font_h.size * 1.15)
        for line in wrap_text(d, body, font_b, cw - 160)[:3]:
            d.text((80, y), line, font=font_b, fill=LIGHT, anchor="lt")
            y += 54
        d.text((80, chh - 120), "TELL US IN THE COMMENTS  ↓", font=f_small, fill=GOLD, anchor="ls")
        return im

    def _render_editorial_split(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int) -> Image.Image:
        heading, body = card["heading"].upper(), card["body"]
        im = Image.new("RGB", (cw, chh), (13, 16, 21))
        im.paste(cover_crop(photo, (cw, self.PHOTO_H)).convert("RGB"), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(self.PHOTO_H - 180, self.PHOTO_H):
            d.line([(0, y), (cw, y)], fill=(13, 16, 21, int(255 * ((y - (self.PHOTO_H - 180)) / 180) ** 1.6)))
        category = self._get_card_category(card, "editorial_split")
        tag = self._tag(category, index)
        f_tag = get_font("Montserrat", 24)
        self._draw_badge(d, tag, 80, self.PHOTO_H + 25, f_tag, text_color=GOLD, border_color=GOLD, radius=6)
        font_h = fit_font(d, heading, "Anton", 66, cw - 160, 40)
        d.text((80, self.PHOTO_H + 85), heading, font=font_h, fill=WHITE, anchor="lt")
        font_b = get_font("Montserrat", 42)
        y = self.PHOTO_H + 155
        for line in wrap_text(d, body, font_b, cw - 160)[:5]:
            d.text((80, y), line, font=font_b, fill=LIGHT, anchor="lt")
            y += 58
        return im

    def generate_carousel_sequence(self, scene_images: List[Path], script_payload: Dict[str, Any], output_dir: Path,
                                   facebook: bool = False) -> List[Path]:
        """Multi-Template Editorial Carousel (1080x1350, 4:5):
        Dynamically routes each slide to one of 5 National Geographic / The Economist archetypes
        (Full-Bleed Immersive, Comparative Side-by-Side, Architectural Blueprint, Colossal Stat, Debate).
        facebook=True: the same slides for Facebook's multi-photo post (carousel_fb/), without the swipe
        cue, slide counter, progress bar or dots: Facebook shows the set as a grid, not a swipe."""
        out_dir = output_dir / ("carousel_fb" if facebook else "carousel")
        self._numbered = not facebook  # slide numbers on the badges ("ENGINEERING // 07") are swipe cues too
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in scene_images if not is_video(p)]
        cards = script_payload["carousel_slides"]
        poll = script_payload.get("poll") or {}
        total = len(cards)
        cw, chh = self.CAROUSEL_SIZE
        photos = self._match_photos(cards, stills)
        f_small = get_font("Montserrat", 28)

        def dots(d: ImageDraw.ImageDraw, k: int) -> None:
            x0 = cw // 2 - (total - 1) * 14
            for j in range(total):
                r = 7 if j == k else 5
                d.ellipse([x0 + 28 * j - r, chh - 60 - r, x0 + 28 * j + r, chh - 60 + r],
                          fill=GOLD if j == k else (110, 110, 115))

        slides = []
        for i, card in enumerate(cards):
            archetype = self.route_carousel_archetype(card, i, total)
            photo = photos[i]
            photo_next = photos[(i + 1) % len(photos)]

            if archetype == "cover":
                im = self._render_cover(photo, card, cw, chh, swipe=not facebook)
            elif archetype == "immersive":
                im = self._render_immersive(photo, card, i, cw, chh)
            elif archetype == "comparison":
                im = self._render_comparison(photo, photo_next, card, i, cw, chh)
            elif archetype == "blueprint":
                im = self._render_blueprint(photo, card, i, cw, chh)
            elif archetype == "colossal_stat":
                im = self._render_colossal_stat(photo, card, i, cw, chh)
            elif archetype == "debate":
                im = self._render_debate(photo, card, i, total, cw, chh, poll, f_small)
            else:
                im = self._render_editorial_split(photo, card, i, cw, chh)

            im = ensure_legible(im, (0, 0, cw, 110), text_rgb=(212, 175, 55), feather=90)  # a full-width header band, no corners
            d = ImageDraw.Draw(im, "RGBA")
            f_dossier = get_font("Montserrat", 20)
            if not facebook:  # the dossier header with its counter is a swipe cue; Facebook keeps only the brand
                d.text((80, 70), f"ARCHIVAL RECORD // {i + 1:02d} OF {total:02d}", font=f_dossier, fill=(212, 175, 55, 255),
                       anchor="lt", stroke_width=2, stroke_fill=(8, 10, 14, 230))
            d.text((cw - 80, 70), "RELIC & RIDDLE", font=f_small, fill=(235, 235, 235, 255), anchor="rt", stroke_width=2, stroke_fill=(8, 10, 14, 230))

            if not facebook:  # continuous panoramic progress timeline and dots: swipe cues
                bar_y = chh - 45
                d.line([(80, bar_y), (cw - 80, bar_y)], fill=(50, 60, 75, 140), width=2)
                seg_w = (cw - 160) / total
                d.line([(80 + i * seg_w, bar_y), (80 + (i + 1) * seg_w, bar_y)], fill=GOLD, width=3)
                dots(d, i)

            path = out_dir / f"carousel_slide_{i + 1:02d}.jpg"
            im.save(path, quality=95)
            slides.append(path)

        log.info("Generated %d multi-template carousel slides -> %s", len(slides), out_dir)
        return slides
