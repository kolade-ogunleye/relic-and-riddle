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
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw

from .config import VIDEO_WIDTH, VIDEO_HEIGHT
from .typography import fit_line, fit_text, cover_crop, fit_font, get_font, is_video, text_size, wrap_text

log = logging.getLogger(__name__)
_TLS = threading.local()

GOLD = (255, 215, 0)
WHITE = (255, 255, 255)
LIGHT = (225, 225, 225)


STORY_BODY_PX = 40      # the Story body's size; never shrunk below it (2026-10-25: 30 px read as ~11 pt)
STORY_BODY_LINES = 3


def story_body_width() -> int:
    from .safe_zones import safe_box
    sx0, _, sx1, _ = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
    return min(VIDEO_WIDTH - 2 * 110, sx1 - sx0)


def story_body_lines(body: str, asks_poll: bool = False) -> int:
    """How many lines a Story frame's body takes at its fixed size, as the renderer draws it (the poll frame
    drops the body's own questions)."""
    if asks_poll:
        body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))
    if not body.strip():
        return 0
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    return len(wrap_text(d, " ".join(body.split()), get_font("Montserrat", STORY_BODY_PX), story_body_width()))


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
        out_dir = output_dir / "stories"
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in (scene_images or []) if not is_video(p)] or [hero_img_path]
        slides_data = script_payload["story_slides"]
        cards = [{"heading": f"{s['line1']} {s['line2']}", "body": s["body"]} for s in slides_data]
        photos = self._match_photos(cards, stills) if len(stills) > 1 else [hero_img_path] * len(cards)
        names = ["story_01_hook.jpg", "story_02_reveal.jpg", "story_03_question.jpg"]
        poll = script_payload.get("poll") or {}
        layout = []  # each frame's text block, for the audit and the evidence renders

        from . import still_layout
        slides = []
        for i, (slide, name) in enumerate(zip(slides_data, names)):
            recipe = {"renderer": "multi_format.redraw_story_frame", "photo": str(photos[i]), "slide": slide,
                      "index": i, "count": len(slides_data), "poll": poll}
            base, lay, attempts = still_layout.drawn(
                lambda tight: self._render_story_frame(photos[i], slide, i, len(slides_data), poll, tight=tight))
            layout.append({**lay.meta, "frame": name})
            path = out_dir / name
            base.save(path, quality=95)
            still_layout.save(path, lay, recipe, attempts)
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

    def _render_story_frame(self, photo: Path, slide: Dict[str, Any], i: int, count: int, poll: Dict[str, Any],
                            tight: bool = False):
        """One Story frame and its measured layout. Every block is measured with the font's real metrics
        before anything is drawn, the whole stack is placed inside the Story box (safe_zones.STORY: Meta's 14%
        top, 35% bottom, 6% sides) and each drawn box is recorded for the post-draw check (still_layout).
        `tight`: the second, tighter fit (smaller title and kicker, the body over the whole safe width)."""
        from .safe_zones import safe_box
        from .still_layout import Layout
        from .visual_engine import calm_center_y
        sx0, sy0, sx1, sy1 = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
        max_w = min(VIDEO_WIDTH - 2 * 110, sx1 - sx0)
        wide_w = sx1 - sx0 - 20                                  # the whole safe width, less a hair for the stroke
        lay = Layout("story", (VIDEO_WIDTH, VIDEO_HEIGHT), (sx0, sy0, sx1, sy1), tight=tight)
        base = cover_crop(photo, (VIDEO_WIDTH, VIDEO_HEIGHT)).convert("RGB")
        cy = calm_center_y(base, candidates=(640, 790, 940), band=520)  # bands inside the Story box
        d = ImageDraw.Draw(base, "RGBA")
        # Global 35% dark underlay across the entire base image to tame bright sunlit stone/sand
        d.rectangle([(0, 0), (VIDEO_WIDTH, VIDEO_HEIGHT)], fill=(6, 6, 8, 90))
        for y in range(VIDEO_HEIGHT):  # soft vignette band behind the text, darkest at its centre
            k = max(0.0, 1 - abs(y - cy) / 520)
            d.line([(0, y), (VIDEO_WIDTH, y)], fill=(6, 6, 8, int(220 * k ** 1.3)))
        kicker, l1, l2 = slide["kicker"].upper(), slide["line1"].upper(), slide["line2"].upper()
        if i == count - 1 and len(poll.get("options", [])) >= 2 and poll.get("question"):
            l1, l2 = split_balanced(poll["question"].upper())  # the frame asks the question its options answer
        pad_x, pad_y = 26, 12
        kicker, font_k, k_short = fit_line(d, kicker, "Montserrat", 30 if tight else 34, max_w - 2 * pad_x, 22)
        t_size, t_min = (88, 48) if tight else (104, 56)
        size = min(fit_line(d, l1, "Anton", t_size, max_w, t_min, stroke=3, quiet=True)[1].size,
                   fit_line(d, l2, "Anton", t_size, max_w, t_min, stroke=3, quiet=True)[1].size)
        l1, font_t, s1 = fit_line(d, l1, "Anton", size, max_w, size, stroke=3)
        l2, _, s2 = fit_line(d, l2, "Anton", size, max_w, size, stroke=3)
        has_options = i == count - 1 and len(poll.get("options", [])) >= 2
        body = slide["body"]
        if has_options:  # one question per frame: the poll's; the body keeps only its context
            body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))
        k_h = text_size(d, kicker, font_k)[1] + 2 * pad_y if kicker else 0
        tail = (40 + 2 * 120 + 70) if has_options else (90 if i == 0 else 20)
        fixed = k_h + 20 + 2 * (size + 12) + 28 + tail
        room = (sy1 - (sy0 + 20)) - fixed                      # the height left for the body inside the box
        n_room = max(1, (room - 48) // 54 + 1)
        # Never smaller than its design size: shrunk to 30 px it read about 11 pt on a phone (operator,
        # 2026-10-25). The script stage keeps each body within three lines at this size (story_body_lines);
        # a longer one is set over the whole safe width before it takes a fourth line, and is never cut
        # unless the box has no room left for it (then at a sentence, else a word, with an ellipsis).
        body_lines, font_b, b_short, body_w = [], get_font("Montserrat", STORY_BODY_PX), False, max_w
        if body.strip():
            for w in ([wide_w] if tight else [max_w, wide_w]):
                body_lines, font_b, b_short = fit_text(d, body, "Montserrat", STORY_BODY_PX, w, STORY_BODY_LINES,
                                                       STORY_BODY_PX, stroke=2, quiet=True)
                body_w = w
                if not b_short:
                    break
            if b_short:
                body_w = wide_w if tight else max_w
                body_lines, font_b, b_short = fit_text(d, body, "Montserrat", STORY_BODY_PX, body_w,
                                                       max(STORY_BODY_LINES, n_room), STORY_BODY_PX, stroke=2)
        lay.shortened = [n for n, short in (("kicker", k_short), ("title_1", s1), ("title_2", s2), ("body", b_short)) if short]
        block = k_h + 20 + 2 * (size + 12) + 28 + len(body_lines) * 54 + tail
        # Keep the whole block (with options or the Reel pointer) inside the Story box.
        y = max(sy0 + 20, min(cy - block // 2, sy1 - block))
        # Translucent dark charcoal pill behind kicker for WCAG AAA legibility (mathematically centered)
        kx = VIDEO_WIDTH // 2
        k_cy = y + k_h // 2
        k_bbox = d.textbbox((kx, k_cy), kicker, font=font_k, anchor="mm")
        pill_box = [k_bbox[0] - pad_x, k_bbox[1] - pad_y, k_bbox[2] + pad_x, k_bbox[3] + pad_y]
        d.rounded_rectangle(pill_box, radius=14, fill=(10, 10, 14, 215), outline=(255, 215, 0, 180), width=2)
        d.text((kx, k_cy), kicker, font=font_k, fill=GOLD, anchor="mm")
        lay.add("kicker", pill_box)
        y = pill_box[3] + 20
        lay.text(d, (VIDEO_WIDTH // 2, y), l1, font_t, "title_1", anchor="mt", fill=WHITE, stroke_width=3, stroke_fill=(10, 10, 12, 230))
        y += size + 12
        lay.text(d, (VIDEO_WIDTH // 2, y), l2, font_t, "title_2", anchor="mt", fill=GOLD, stroke_width=3, stroke_fill=(10, 10, 12, 230))
        y += size + 40
        boxes = []
        for line in body_lines:
            boxes.append(lay.text(d, (VIDEO_WIDTH // 2, y), line, font_b, "body", anchor="mt", record=False, fill=LIGHT,
                                  stroke_width=2, stroke_fill=(10, 10, 12, 220)))
            y += 54
        lay.block("body", boxes)
        y_end = y
        if i == 0:
            lay.text(d, (VIDEO_WIDTH // 2, y + 30), "FULL STORY IN OUR NEW REEL  →", get_font("Montserrat", 32), "reel_pointer",
                     anchor="mt", fill=GOLD, stroke_width=2, stroke_fill=(10, 10, 12, 220))
            y_end = y + 30 + 40
        if has_options:
            font_o = get_font("Anton", 42)
            ox0, ox1 = max(120, sx0), min(VIDEO_WIDTH - 120, sx1)
            for j, (prefix, option) in enumerate(zip(["A", "B"], poll["options"][:2])):
                oy = y + 40 + j * 120
                d.rounded_rectangle([ox0, oy, ox1, oy + 96], radius=24, fill=(10, 10, 12, 215),
                                    outline=(255, 215, 0, 230), width=2)
                lay.add(f"option_{prefix}", (ox0, oy, ox1, oy + 96))
                d.text((ox0 + 30, oy + 48), prefix, font=font_o, fill=GOLD, anchor="lm")
                opt, f_opt, o_short = fit_line(d, option.upper(), "Montserrat", 34, ox1 - ox0 - 100 - 30, 22)
                if o_short:
                    lay.shortened.append(f"option_{prefix}")
                d.text((ox0 + 100, oy + 48), opt, font=f_opt, fill=WHITE, anchor="lm")
            lay.text(d, (VIDEO_WIDTH // 2, y + 40 + 2 * 120 + 20), "WEIGH IN BELOW  ↓", get_font("Montserrat", 32), "weigh_in",
                     anchor="mt", fill=LIGHT, stroke_width=2, stroke_fill=(10, 10, 12, 220))
            y_end = y + 40 + 2 * 120 + 20 + 40
        from .still_layout import union
        ux0, uy0, ux1, uy1 = union([tuple(e["box"]) for e in lay.elements])
        lay.meta = {"frame": None, "size": [VIDEO_WIDTH, VIDEO_HEIGHT],
                    "body_source": " ".join(body.split()), "body_drawn": " ".join(body_lines),
                    "body_px": font_b.size, "body_lines": len(body_lines),
                    "text_box": [min(ux0, (VIDEO_WIDTH - max(max_w, body_w)) // 2), min(uy0, int(pill_box[1])),
                                 max(ux1, (VIDEO_WIDTH + max(max_w, body_w)) // 2), max(uy1, int(y_end))],
                    "safe_box": [sx0, sy0, sx1, sy1]}
        return base, lay

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

    # Whether badges carry slide numbers (Instagram's swipe copy) or not (Facebook's grid copy). Per thread:
    # the two copies are drawn at the same time on one generator (main.py's packaging pool).
    @property
    def _numbered(self) -> bool:
        return getattr(_TLS, "numbered", True)

    @_numbered.setter
    def _numbered(self, value: bool) -> None:
        _TLS.numbered = bool(value)

    def _tag(self, category: str, index: int) -> str:
        """A slide's badge: its category, with its number on the swipe copy (Instagram) only."""
        return f"{category} // {index + 1:02d}" if self._numbered else category

    # Carousel layout bands (still-layout audit, 2026-10-28): the header (dossier counter and brand) and the
    # swipe cues (progress bar and dots) are measured and reserved, so no badge or text block is placed over them.
    HEADER_Y = 70
    CUES_TOP_GAP = 67           # the dots' top edge, this far above the bottom
    BAND_GAP = 16

    def _zone(self, cw: int, chh: int):
        """Where a slide's text and badges stay: the canvas less 40 px each side (the profile grid's 3:4 crop
        takes 33 px off each side of a 4:5 slide) and 30 px top and bottom."""
        return (40, 30, cw - 40, chh - 30)

    def _header_bottom(self, d: ImageDraw.ImageDraw, cw: int, tight: bool) -> int:
        f_small, f_dossier = get_font("Montserrat", 28), get_font("Montserrat", 20)
        b1 = d.textbbox((cw - 80, self.HEADER_Y), "RELIC & RIDDLE", font=f_small, anchor="rt", stroke_width=2)
        b2 = d.textbbox((80, self.HEADER_Y), "ARCHIVAL RECORD // 00 OF 00", font=f_dossier, anchor="lt", stroke_width=2)
        return max(b1[3], b2[3]) + (10 if tight else 0)

    def _footer_top(self, chh: int) -> int:
        """The lowest y a slide's text may reach: above the swipe cues, with a gap."""
        return chh - self.CUES_TOP_GAP - self.BAND_GAP

    def _plan_panel(self, d, heading: str, body: str, width: int, room: int, h_spec, b_spec, tight: bool,
                    h_stroke: int = 0, b_stroke: int = 0, h_pitch: float = 1.1, body_max: int = 4):
        """Heading and body measured to fit `width` x `room`: the heading on one line (shrinking to its floor),
        else on two; the body wrapped, shrunk to 80% of its size, then kept to whole sentences, and only last
        shortened at a word with an ellipsis. Returns the plan the panel is drawn from."""
        h_size, h_min = h_spec
        b_size = b_spec
        if tight:
            h_size, b_size = int(h_size * 0.88), int(b_size * 0.9)
        h_lines, font_h, h_short = fit_text(d, heading, "Anton", h_size, width, 1, h_min, stroke=h_stroke, sentences=False,
                                            quiet=True)
        if h_short:
            h_lines, font_h, h_short = fit_text(d, heading, "Anton", h_size, width, 2, h_min, stroke=h_stroke, sentences=False)
        h_step = int(font_h.size * h_pitch)
        h_height = len(h_lines) * h_step
        b_min = max(28, int(b_size * 0.8))
        b_step = int(round(b_size * 1.38))
        left = room - h_height
        max_lines = max(1, min(body_max, (left - int(b_size * 1.25)) // b_step + 1)) if left > b_size else 1
        b_lines, font_b, b_short = fit_text(d, body, "Montserrat", b_size, width, max_lines, b_min, stroke=b_stroke) \
            if body.strip() else ([], get_font("Montserrat", b_size), False)
        b_step = int(round(font_b.size * 1.38))
        shortened = (["heading"] if h_short else []) + (["body"] if b_short else [])
        return {"h_lines": h_lines, "font_h": font_h, "h_step": h_step, "b_lines": b_lines, "font_b": font_b,
                "b_step": b_step, "height": h_height + len(b_lines) * b_step, "shortened": shortened}

    def _draw_panel(self, d, lay, plan, x: int, y: int, h_fill=WHITE, b_fill=LIGHT, h_stroke=0, b_stroke=0,
                    h_stroke_fill=None, b_stroke_fill=None, gap: int = 0) -> int:
        """Draws a planned heading and body from `y`; records each as one block; returns the y below them."""
        hk = {"stroke_width": h_stroke, "stroke_fill": h_stroke_fill} if h_stroke else {}
        bk = {"stroke_width": b_stroke, "stroke_fill": b_stroke_fill} if b_stroke else {}
        boxes = []
        for line in plan["h_lines"]:
            boxes.append(self._text(d, lay, (x, y), line, plan["font_h"], h_fill, **hk))
            y += plan["h_step"]
        if lay is not None:
            lay.block("heading", boxes)
        y += gap
        # The body starts below the heading's measured bottom: an accented or descending heading line
        # ("ÇATALHÖYÜK", "KRAKÓW") reaches lower than its step, and the body overlapped it (review, 2026-10-28).
        heading_bottom = max((bx[3] for bx in boxes if bx), default=None)
        if heading_bottom is not None and plan["b_lines"]:
            body_top = d.textbbox((x, y), plan["b_lines"][0], font=plan["font_b"], anchor="lt", stroke_width=bk.get("stroke_width", 0))[1]
            y += max(0, heading_bottom + 6 - body_top)
        boxes = []
        for line in plan["b_lines"]:
            boxes.append(self._text(d, lay, (x, y), line, plan["font_b"], b_fill, **bk))
            y += plan["b_step"]
        if lay is not None:
            lay.block("body", boxes)
            lay.shortened += plan["shortened"]
        return y

    @staticmethod
    def _text(d, lay, xy, text, font, fill, anchor="lt", **kw):
        d.text(xy, text, font=font, fill=fill, anchor=anchor, **kw)
        return tuple(d.textbbox(xy, text, font=font, anchor=anchor, stroke_width=kw.get("stroke_width", 0)))

    @staticmethod
    def _badge(d, lay, name, text, x0, y0, font, max_w: int = 920, **kw):
        """A badge that fits its width, recorded as its pill."""
        text, font, short = fit_line(d, text, _font_family(font), font.size, max_w - 2 * kw.get("pad_x", 18), 16)
        box = MultiFormatGenerator._draw_badge(d, text, x0, y0, font, **kw)
        if lay is not None:
            lay.add(name, box)
            if short:
                lay.shortened.append(name)
        return box

    def _render_cover(self, photo: Path, card: Dict[str, Any], cw: int, chh: int, swipe: bool = True,
                      lay=None, tight: bool = False) -> Image.Image:
        heading, body = card["heading"].upper(), card["body"]
        im = cover_crop(photo, self.CAROUSEL_SIZE).convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(chh // 3, chh):
            a = int(235 * ((y - chh / 3) / (chh * 2 / 3)) ** 1.4)
            d.line([(0, y), (cw, y)], fill=(8, 8, 10, a))
        im = ensure_legible(im, (40, chh - 720, cw - 40, chh - 80))  # up to 3 heading lines, body and swipe
        d = ImageDraw.Draw(im, "RGBA")
        width = cw - 160
        h_size = 96 if tight else 110
        font_h = fit_font(d, heading, "Anton", h_size, width, 56)
        lines, font_h, h_short = fit_text(d, heading, "Anton", font_h.size, width, 3, 48 if tight else 56, stroke=3, sentences=False)
        cover_lines, font_cb, b_short = fit_text(d, body, "Montserrat", 32 if tight else 36, width, 2, 28, stroke=2)
        step = int(font_h.size * 1.05)
        swipe_top = chh - 110 - 50                                  # the SWIPE cue's row (its cap height, with room)
        # The old placement (heading baselines from chh-470, body two rows under), moved up only when the body
        # would reach the swipe row.
        y = chh - 470 - (len(lines) - 1) * 60
        # The body clears the heading's measured bottom: an accented or descending heading line reaches below
        # its baseline step, and the body overlapped it on every redraw (review, 2026-10-28).
        drop = 0
        if lines and cover_lines:
            last = y + (len(lines) - 1) * step
            h_bottom = d.textbbox((80, last), lines[-1], font=font_h, anchor="ls", stroke_width=3)[3]
            b_top = d.textbbox((80, last + step + 20), cover_lines[0], font=font_cb, anchor="ls", stroke_width=2)[1]
            drop = max(0, h_bottom + 6 - b_top)
        body_bottom = y + len(lines) * step + 20 + drop + 48 * max(0, len(cover_lines) - 1) + 12
        if body_bottom > swipe_top - self.BAND_GAP:
            y -= body_bottom - (swipe_top - self.BAND_GAP)
        boxes = []
        for line in lines:
            boxes.append(self._text(d, lay, (80, y), line, font_h, WHITE, anchor="ls", stroke_width=3, stroke_fill=(8, 8, 10, 230)))
            y += step
        y += drop
        bboxes = []
        for j, line in enumerate(cover_lines):
            bboxes.append(self._text(d, lay, (80, y + 20 + 48 * j), line, font_cb, LIGHT, anchor="ls",
                                     stroke_width=2, stroke_fill=(8, 8, 10, 220)))
        if lay is not None:
            lay.block("heading", boxes)
            lay.block("body", bboxes)
            lay.shortened += (["heading"] if h_short else []) + (["body"] if b_short else [])
        if swipe:
            box = self._text(d, lay, (cw - 80, chh - 110), "SWIPE  →", get_font("Anton", 44), GOLD, anchor="rs",
                             stroke_width=2, stroke_fill=(8, 8, 10, 220))
            if lay is not None:
                lay.add("swipe", box)
        return im

    def _render_immersive(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """Archetype A: Full-Bleed Immersive Photo with floating translucent charcoal badges."""
        heading, body = card["heading"].upper(), card["body"]
        im = cover_crop(photo, self.CAROUSEL_SIZE).convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle([(0, 0), (cw, chh)], fill=(6, 8, 12, 70))
        for y in range(chh // 2, chh):
            a = int(220 * ((y - chh / 2) / (chh / 2)) ** 1.3)
            d.line([(0, y), (cw, y)], fill=(8, 10, 14, a))
        bottom = self._footer_top(chh)
        # Measured first: badge, heading and body stacked upward from the cues, at the old place when they fit.
        top_min = chh // 2 + 40                                   # where the shading gradient is already deep
        plan = self._plan_panel(d, heading, body, cw - 160, bottom - (top_min + 90) - 18, (76, 44), 40, tight,
                                h_stroke=3, b_stroke=2, h_pitch=1.08)
        block_h = 90 + plan["height"] + 18
        top = min(chh - 530, bottom - block_h)
        im = ensure_legible(im, (40, top - 30, cw - 40, chh - 50))  # badge, heading and body
        d = ImageDraw.Draw(im, "RGBA")
        category = self._get_card_category(card, "immersive")
        tag = self._tag(category, index)
        self._badge(d, lay, "badge", tag, 80, top, get_font("Montserrat", 24), text_color=GOLD, border_color=GOLD,
                    border_width=2, radius=8)
        self._draw_panel(d, lay, plan, 80, top + 90, h_stroke=3, b_stroke=2, h_stroke_fill=(6, 8, 10, 240),
                         b_stroke_fill=(6, 8, 10, 220), gap=18)
        return im

    def _render_comparison(self, photo1: Path, photo2: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                           lay=None, tight: bool = False) -> Image.Image:
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
        # REF A sat at y=35, under the slide's own dossier header at y=70 (measured overlap, 2026-10-28): it
        # now starts below the header band.
        ref_a_y = max(35, self._header_bottom(d, cw, tight) + self.BAND_GAP)
        self._badge(d, lay, "ref_a", "REF A // SURFACE CONTEXT", 60, ref_a_y, f_sub, text_color=WHITE, border_color=GOLD,
                    fill_color=(10, 12, 16, 220), radius=6)
        self._badge(d, lay, "ref_b", "REF B // SUBTERRANEAN DEPTH", 60, h_half + 35, f_sub, text_color=GOLD,
                    border_color=GOLD, fill_color=(10, 12, 16, 220), radius=6)
        category = self._get_card_category(card, "comparison")
        tag = self._tag(category, index)
        self._badge(d, lay, "badge", tag, 80, 2 * h_half + 25, get_font("Montserrat", 24), text_color=GOLD,
                    border_color=GOLD, radius=6)
        y = 2 * h_half + 85
        plan = self._plan_panel(d, heading, body, cw - 160, self._footer_top(chh) - y, (64, 38), 38, tight)
        self._draw_panel(d, lay, plan, 80, y)
        return im

    def _render_blueprint(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
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
        self._badge(d, lay, "badge", tag, 80, p_h + 25, get_font("Montserrat", 24), text_color=GOLD, border_color=GOLD,
                    fill_color=(8, 12, 18, 230), radius=4)
        y = p_h + 85
        plan = self._plan_panel(d, heading, body, cw - 160, self._footer_top(chh) - y, (64, 38), 40, tight, body_max=5)
        self._draw_panel(d, lay, plan, 80, y)
        return im

    def _render_colossal_stat(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                              lay=None, tight: bool = False) -> Image.Image:
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
        self._badge(d, lay, "badge", tag, 80, p_h + 10, get_font("Montserrat", 24), text_color=GOLD, border_color=GOLD,
                    fill_color=(16, 20, 26, 230), radius=6)
        stat_match = re.search(r"(\b\d+[\d,]*\+?(?:\s*(?:TONS?|PEOPLE|FEET|FT|METERS?|M|STORIES|LEVELS))?)\b", f"{heading} {body}", re.IGNORECASE)
        stat_text = stat_match.group(1).upper() if stat_match else (f"{index + 1:02d} DATA" if self._numbered else category)
        stat_text, f_stat, s_short = fit_line(d, stat_text, "Anton", 96 if tight else 110, cw - 160, 56)
        box = self._text(d, lay, (80, p_h + 65), stat_text, f_stat, GOLD)
        if lay is not None:
            lay.add("stat", box)
            if s_short:
                lay.shortened.append("stat")
        y = p_h + 65 + int(f_stat.size * 1.1)
        ver_y = chh - 140
        plan = self._plan_panel(d, heading, body, cw - 160, ver_y - self.BAND_GAP - y, (62, 38), 38, tight, h_pitch=1.15)
        self._draw_panel(d, lay, plan, 80, y)
        self._badge(d, lay, "verified", "VERIFIED BY ARCHEOLOGICAL RECORD", 80, ver_y, get_font("Montserrat", 22),
                    text_color=GOLD, border_color=(255, 215, 0, 180), fill_color=(16, 20, 26, 230), radius=6)
        return im

    def _render_debate(self, photo: Path, card: Dict[str, Any], index: int, total: int, cw: int, chh: int, poll: Dict[str, Any], f_small,
                       share: str = "", lay=None, tight: bool = False) -> Image.Image:
        """Archetype E: Forensic Closing Dilemma / Debate Card. `share`: the share-and-follow line, above the
        comments footer (Meta's creator tips, 2026-10-25)."""
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
        self._badge(d, lay, "badge", tag, 80, p_h + 20, get_font("Montserrat", 24), text_color=GOLD, border_color=GOLD,
                    fill_color=(16, 18, 22, 230), radius=6)
        footer = self._text(d, lay, (80, chh - 120), "TELL US IN THE COMMENTS  ↓", f_small, GOLD, anchor="ls")
        share_lines, f_share, sh_short = fit_text(d, share, "Montserrat", 34, cw - 160, 2, 28) if share else ([], None, False)
        step = int(f_share.size * 1.3) if f_share else 0
        share_top = chh - 176 - step * len(share_lines)                   # above the footer
        y = p_h + 80
        room = (share_top - 24 if share_lines else footer[1] - self.BAND_GAP) - y
        plan = self._plan_panel(d, heading, body, cw - 160, room, (68, 42), 40, tight, h_pitch=1.15, body_max=3)
        self._draw_panel(d, lay, plan, 80, y)
        if share_lines:
            ys, boxes = share_top, []
            for line in share_lines:
                boxes.append(self._text(d, lay, (80, ys), line, f_share, WHITE))
                ys += step
            if lay is not None:
                lay.block("share", boxes)
                if sh_short:
                    lay.shortened.append("share")
        if lay is not None:
            lay.add("footer", footer)
        return im

    def _render_editorial_split(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                                lay=None, tight: bool = False) -> Image.Image:
        heading, body = card["heading"].upper(), card["body"]
        im = Image.new("RGB", (cw, chh), (13, 16, 21))
        im.paste(cover_crop(photo, (cw, self.PHOTO_H)).convert("RGB"), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(self.PHOTO_H - 180, self.PHOTO_H):
            d.line([(0, y), (cw, y)], fill=(13, 16, 21, int(255 * ((y - (self.PHOTO_H - 180)) / 180) ** 1.6)))
        category = self._get_card_category(card, "editorial_split")
        tag = self._tag(category, index)
        self._badge(d, lay, "badge", tag, 80, self.PHOTO_H + 25, get_font("Montserrat", 24), text_color=GOLD,
                    border_color=GOLD, radius=6)
        y = self.PHOTO_H + 85
        plan = self._plan_panel(d, heading, body, cw - 160, self._footer_top(chh) - y, (66, 40), 42, tight, body_max=5)
        self._draw_panel(d, lay, plan, 80, y)
        return im

    def _render_carousel_slide(self, archetype: str, photo: Path, photo_next: Path, card: Dict[str, Any], i: int,
                               total: int, poll: Dict[str, Any], share: str, facebook: bool, tight: bool = False):
        """One carousel slide and its measured layout: the archetype's own text, then the header (dossier and
        brand) and the swipe cues, each in its reserved band. `tight`: smaller type, and the header on solid
        pills so it reads over any photo (Richat 2's header "collides with the spacecraft's solar panel")."""
        from .still_layout import Layout
        cw, chh = self.CAROUSEL_SIZE
        self._numbered = not facebook
        lay = Layout("carousel", (cw, chh), self._zone(cw, chh), tight=tight)
        f_small = get_font("Montserrat", 28)
        if archetype == "cover":
            im = self._render_cover(photo, card, cw, chh, swipe=not facebook, lay=lay, tight=tight)
        elif archetype == "immersive":
            im = self._render_immersive(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "comparison":
            im = self._render_comparison(photo, photo_next, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "blueprint":
            im = self._render_blueprint(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "colossal_stat":
            im = self._render_colossal_stat(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "debate":
            im = self._render_debate(photo, card, i, total, cw, chh, poll, f_small, share=share, lay=lay, tight=tight)
        else:
            im = self._render_editorial_split(photo, card, i, cw, chh, lay=lay, tight=tight)

        # Shade behind each header text's own extent, not a band across the slide: the band's 90th-percentile
        # luminance was mostly black sky, so a badge over a bright solar panel was left at 1.1:1 (measured 2026-10-25).
        d = ImageDraw.Draw(im, "RGBA")
        f_dossier = get_font("Montserrat", 20)
        dossier = f"ARCHIVAL RECORD // {i + 1:02d} OF {total:02d}"
        brand_w = d.textlength("RELIC & RIDDLE", font=f_small)
        im = ensure_legible(im, (int(cw - 80 - brand_w - 30), 50, cw - 50, 100), feather=60)
        if not facebook:  # the dossier header with its counter is a swipe cue; Facebook keeps only the brand
            im = ensure_legible(im, (50, 50, int(80 + d.textlength(dossier, font=f_dossier) + 30), 100), text_rgb=(212, 175, 55), feather=60)
        d = ImageDraw.Draw(im, "RGBA")
        header = [("brand", (cw - 80, self.HEADER_Y), "RELIC & RIDDLE", f_small, (235, 235, 235, 255), "rt")]
        if not facebook:
            header.insert(0, ("dossier", (80, self.HEADER_Y), dossier, f_dossier, (212, 175, 55, 255), "lt"))
        for name, xy, text, font, fill, anchor in header:
            box = d.textbbox(xy, text, font=font, anchor=anchor, stroke_width=2)
            if tight:  # a solid pill behind the header, whatever the photo does there
                box = (box[0] - 12, box[1] - 8, box[2] + 12, box[3] + 8)
                d.rounded_rectangle(box, radius=8, fill=(8, 10, 14, 215))
            d.text(xy, text, font=font, fill=fill, anchor=anchor, stroke_width=2, stroke_fill=(8, 10, 14, 230))
            lay.add(f"header_{name}", box)

        if not facebook:  # continuous panoramic progress timeline and dots: swipe cues
            bar_y = chh - 45
            d.line([(80, bar_y), (cw - 80, bar_y)], fill=(50, 60, 75, 140), width=2)
            seg_w = (cw - 160) / total
            d.line([(80 + i * seg_w, bar_y), (80 + (i + 1) * seg_w, bar_y)], fill=GOLD, width=3)
            x0 = cw // 2 - (total - 1) * 14
            for j in range(total):
                r = 7 if j == i else 5
                d.ellipse([x0 + 28 * j - r, chh - 60 - r, x0 + 28 * j + r, chh - 60 + r],
                          fill=GOLD if j == i else (110, 110, 115))
            lay.add("swipe_cues", (80, chh - self.CUES_TOP_GAP, cw - 80, bar_y + 2))
        return im, lay

    def generate_carousel_sequence(self, scene_images: List[Path], script_payload: Dict[str, Any], output_dir: Path,
                                   facebook: bool = False) -> List[Path]:
        """Multi-Template Editorial Carousel (1080x1350, 4:5):
        Dynamically routes each slide to one of 5 National Geographic / The Economist archetypes
        (Full-Bleed Immersive, Comparative Side-by-Side, Architectural Blueprint, Colossal Stat, Debate).
        facebook=True: the same slides for Facebook's multi-photo post (carousel_fb/), without the swipe
        cue, slide counter, progress bar or dots: Facebook shows the set as a grid, not a swipe.
        Each slide is measured and checked after drawing (still_layout); a failing one is drawn once more
        with the tighter fit, and its record is kept in carousel*/still_layout.json."""
        from . import still_layout
        out_dir = output_dir / ("carousel_fb" if facebook else "carousel")
        self._numbered = not facebook  # slide numbers on the badges ("ENGINEERING // 07") are swipe cues too
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in scene_images if not is_video(p)]
        cards = script_payload["carousel_slides"]
        poll = script_payload.get("poll") or {}
        total = len(cards)
        photos = self._match_photos(cards, stills)
        from .script_engine import share_prompt
        share = share_prompt(script_payload)  # the closing slide asks for a share and a follow (2026-10-25)

        slides = []
        for i, card in enumerate(cards):
            archetype = self.route_carousel_archetype(card, i, total)
            photo = photos[i]
            photo_next = photos[(i + 1) % len(photos)]
            im, lay, attempts = still_layout.drawn(
                lambda tight: self._render_carousel_slide(archetype, photo, photo_next, card, i, total, poll, share,
                                                          facebook, tight=tight))
            path = out_dir / f"carousel_slide_{i + 1:02d}.jpg"
            im.save(path, quality=95)
            still_layout.save(path, lay, {"renderer": "multi_format.redraw_carousel_slide", "archetype": archetype,
                                          "photo": str(photo), "photo_next": str(photo_next), "card": card, "index": i,
                                          "total": total, "poll": poll, "share": share, "facebook": facebook}, attempts)
            slides.append(path)

        log.info("Generated %d multi-template carousel slides -> %s", len(slides), out_dir)
        return slides


def _font_family(font) -> str:
    """The bundled family a loaded font came from (for refitting it at another size)."""
    from .typography import FONT_FILES
    base = Path(getattr(font, "path", "") or "").name
    return next((n for n, f in FONT_FILES.items() if f == base), "Montserrat")


def redraw_story_frame(path: Path, recipe: Dict[str, Any]) -> Dict[str, Any]:
    """Draws one Story frame again with the tighter fit, from its kept recipe, in place (still_vision: the
    look reported its text cut off or overlapping); updates layout.json and the frame's layout record."""
    from . import still_layout
    gen = MultiFormatGenerator()
    im, lay = gen._render_story_frame(Path(recipe["photo"]), recipe["slide"], int(recipe["index"]), int(recipe["count"]),
                                      recipe.get("poll") or {}, tight=True)
    im.save(path, quality=95)
    layout_file = Path(path).parent / "layout.json"
    try:
        entries = json.loads(layout_file.read_text(encoding="utf-8")) if layout_file.is_file() else []
    except (OSError, ValueError):
        entries = []
    entry = {**lay.meta, "frame": Path(path).name}
    entries = [entry if e.get("frame") == entry["frame"] else e for e in entries] if any(
        e.get("frame") == entry["frame"] for e in entries) else entries + [entry]
    layout_file.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    prior = still_layout.load(path) or {}
    return still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)


def redraw_carousel_slide(path: Path, recipe: Dict[str, Any]) -> Dict[str, Any]:
    """Draws one carousel slide again with the tighter fit, from its kept recipe, in place."""
    from . import still_layout
    gen = MultiFormatGenerator()
    im, lay = gen._render_carousel_slide(recipe["archetype"], Path(recipe["photo"]), Path(recipe["photo_next"]),
                                         recipe["card"], int(recipe["index"]), int(recipe["total"]),
                                         recipe.get("poll") or {}, recipe.get("share") or "", bool(recipe.get("facebook")),
                                         tight=True)
    im.save(path, quality=95)
    prior = still_layout.load(path) or {}
    return still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)
