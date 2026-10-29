"""
src/multi_format.py
===================
Companion assets derived from the Reel's script package, for manual posting:
1. 3-frame Story sequence (1080x1920): hook, revelation, question. The poll itself is
   added as a native Instagram poll sticker when posting; frame 3 asks the question, draws no
   options or reply instruction, leaves POLL_STICKER_ROOM empty for the sticker, and the
   question/options are saved in companion_posting.json.
2. 7-slide carousel (1440x1800, 4:5): cover, matched-photo fact slides, closing question.
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
from .typography import (drawable, fit_line, fit_text, cover_crop, fit_font, get_font, is_video, spoken_units,
                         text_size, wrap_text)

log = logging.getLogger(__name__)

GOLD = (255, 215, 0)
WHITE = (255, 255, 255)
LIGHT = (225, 225, 225)


# The poll frame's room for the native poll sticker, which the operator places when posting (y range on the
# 1080x1920 frame). Nothing is drawn there: the frame asks the question and the sticker answers it. It drew its
# own A and B option boxes and "REPLY WITH A OR B" until 2026-10-29: fake poll controls and a set-reply ask,
# which the engagement-bait check itself flags ("Reply A").
POLL_STICKER_ROOM = (1300, 1560)

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
        """Three Story frames, each on its own matched photo: hook, reveal, and the poll's question
        (the native poll sticker can't be posted through the API: the operator adds it in POLL_STICKER_ROOM,
        which the frame keeps empty; the frame draws no options of its own and asks for no set reply).
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
        # One unit per run: figures in the unit the narration speaks (2026-10-29); matched to photos as written.
        narration = script_payload.get("narration_script") or ""
        slides_data = [{k: (spoken_units(v, narration) if isinstance(v, str) else v) for k, v in s.items()}
                       for s in slides_data]
        if poll.get("question"):
            poll = dict(poll, question=spoken_units(poll["question"], narration))
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
        fb_caption = f"{clean_title}\n\n{first_comment}\n\nWeigh in with your thoughts in the comments."  # no "below" (operator, 2026-10-28)
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
        asks_poll = i == count - 1 and len(poll.get("options", [])) >= 2
        lay = Layout("story", (VIDEO_WIDTH, VIDEO_HEIGHT), (sx0, sy0, sx1, sy1), tight=tight,
                     protected=[("poll_sticker_room", (0, POLL_STICKER_ROOM[0], VIDEO_WIDTH, POLL_STICKER_ROOM[1]))]
                     if asks_poll else None)
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
        has_options = asks_poll
        body = slide["body"]
        if has_options:  # one question per frame: the poll's; the body keeps only its context
            body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))
        k_h = text_size(d, kicker, font_k)[1] + 2 * pad_y if kicker else 0
        tail = 90 if i == 0 else 20
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
        # No arrow on the pointer or the reply line (operator, 2026-10-28: a pointer must point the right way,
        # and each platform places its own controls differently and may move them): the words say where to go.
        if i == 0:
            lay.text(d, (VIDEO_WIDTH // 2, y + 30), "FULL STORY IN OUR NEW REEL", get_font("Montserrat", 32), "reel_pointer",
                     anchor="mt", fill=GOLD, stroke_width=2, stroke_fill=(10, 10, 12, 220))
            y_end = y + 30 + 40
        # The poll frame ends on its question: no option boxes and no "REPLY WITH A OR B" (2026-10-29). The
        # native sticker, placed by the operator in POLL_STICKER_ROOM, is the only way to answer it.
        from .still_layout import union
        ux0, uy0, ux1, uy1 = union([tuple(e["box"]) for e in lay.elements])
        lay.meta = {"frame": None, "size": [VIDEO_WIDTH, VIDEO_HEIGHT],
                    "body_source": " ".join(body.split()), "body_drawn": " ".join(body_lines),
                    "body_px": font_b.size, "body_lines": len(body_lines),
                    "text_box": [min(ux0, (VIDEO_WIDTH - max(max_w, body_w)) // 2), min(uy0, int(pill_box[1])),
                                 max(ux1, (VIDEO_WIDTH + max(max_w, body_w)) // 2), max(uy1, int(y_end))],
                    "safe_box": [sx0, sy0, sx1, sy1],
                    **({"poll_sticker_room": [0, POLL_STICKER_ROOM[0], VIDEO_WIDTH, POLL_STICKER_ROOM[1]]}
                       if has_options else {})}
        return base, lay

    # 4:5 is the tallest ratio Meta's content-publishing API accepts for feed images (4:5 to 1.91:1);
    # the profile grid shows the centre 3:4 of it, so cover text stays in the middle. 1440x1800 is Meta's
    # recommended 4:5 feed size and the widest the Graph API takes (operator, 2026-10-28: sharper slides, same
    # layout). Every carousel pixel value below is a design value on the 1080-wide canvas, drawn through
    # `_px(cw)` (x cw / 1080), so a 1440 slide is the 1080 slide's layout at 4/3 the resolution.
    CAROUSEL_SIZE = (1440, 1800)
    CAROUSEL_DESIGN_W = 1080
    # JPEG: quality 97 with full-resolution colour (4:4:4). 4:2:0 halves the chroma, which fringed the gold type and
    # capped its colour fidelity at ~38 dB PSNR at any quality; 4:4:4 at 97 measured >= 50 dB there and 46 dB luma,
    # the largest of 28 production slides 1.6 MB (Meta's limit is 8 MB; docs/reviews/carousel-1440-2026-10-28).
    CAROUSEL_JPEG = {"quality": 97, "subsampling": 0, "optimize": True}
    PHOTO_H = 760  # fact slides: photo on top, editorial text panel below (design px)

    @classmethod
    def _px(cls, cw: int):
        """Design px (on the 1080-wide canvas) to this canvas's px: px(80) is 80 at 1080 and 107 at 1440."""
        s = cw / cls.CAROUSEL_DESIGN_W

        def px(v):
            return int(round(v * s))

        def exact(v):  # a font size or stroke width: exactly x 4/3 (FreeType and Pillow take fractional ones)
            v = v * s
            return int(v) if v == int(v) else v
        px.s, px.exact = s, exact
        return px

    @staticmethod
    def _fit_text(d, text: str, family: str, size: int, width: int, max_lines: int, min_size: int, px=round, **kw):
        """fit_text decided on the design canvas (design sizes, strokes and floor; `width` in canvas px) and drawn
        at this canvas's scale: the lines break where the 1080 slide breaks them and each font is the 1080 size
        x 4/3. Fitted on the large canvas itself, fit_text's 2 px steps and the fonts' hinting picked other sizes
        and line breaks (a cover heading that fitted its 920 px on one line went onto two at 1440)."""
        lines, font, short = fit_text(d, text, family, size, int(round(width / getattr(px, "s", 1.0))), max_lines,
                                      min_size, **kw)
        return lines, get_font(family, getattr(px, "exact", px)(font.size)), short

    @staticmethod
    def _fill(photo: Path, size: Tuple[int, int]) -> Image.Image:
        """The photo scaled and centre-cropped to `size` in one Lanczos resample from the file itself (the
        pipeline's upscaled copy for a low-resolution source), never from a smaller intermediate."""
        return cover_crop(photo, size).convert("RGB")

    @classmethod
    def _save_slide(cls, im: Image.Image, path: Path) -> None:
        im.save(path, "JPEG", **cls.CAROUSEL_JPEG)

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

    def _carousel_photos(self, cards: List[Dict[str, Any]], stills: List[Path], output_dir: Path) -> List[Path]:
        """The slides' photos, matched once per run and shared by both copies (platform audit, 2026-10-28: the
        Instagram and Facebook copies were matched separately, in parallel, and showed different photos for the
        same slide). The match is kept in carousel_photos.json beside the copies; the second copy to be drawn
        waits for the first and reuses it, and a later redraw of one copy reuses it while the cards and the
        photo pool are the same. A run drawn before the record existed reuses the other copy's recorded photos."""
        output_dir = Path(output_dir)
        record = output_dir / CAROUSEL_PHOTOS
        key = {"cards": [f"{c.get('heading', '')}|{c.get('body', '')}" for c in cards],
               "stills": [str(Path(p)) for p in stills]}
        with _photo_lock(output_dir):
            try:
                kept = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
            except (OSError, ValueError):
                kept = {}
            chosen = [Path(x) for x in kept.get("photos") or []]
            if kept.get("key") == key and len(chosen) == len(cards) and all(x.is_file() for x in chosen):
                return chosen
            chosen = _recorded_photos(output_dir, cards) or self._match_photos(cards, stills)
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                record.write_text(json.dumps({"key": key, "photos": [str(x) for x in chosen]}, indent=1,
                                             ensure_ascii=False), encoding="utf-8", newline="\n")
            except OSError as e:  # the record is a convenience; the slides are still drawn
                log.warning("Carousel photo match not recorded (%s).", e)
            return chosen

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
        """The slide's badge, or "" for none. A slide may not state a qualifier its script did not (platform
        audit, 2026-10-28): keyword labels put "DISCOVERY RECORD" on a slide about marine concrete ("seawalls"
        has "wall"), "ARCHIVAL EVIDENCE" and "ENGINEERING BLUEPRINT" on slides that cite no archive or blueprint,
        and "FORENSIC" on a question. Nothing on a card says what kind of evidence it is (it has a heading and
        a body), so fact slides carry no badge; the closing slide is labelled for what it does, and only when
        it does it: it asks the reader a question."""
        if archetype == "debate" and "?" in f"{card.get('heading', '')} {card.get('body', '')}":
            return "YOUR TAKE"
        return ""

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

    def _tag(self, category: str, index: int) -> str:
        """A slide's badge: its category. No slide number ("ENGINEERING // 07"): Instagram shows its own "N/M"
        counter and dots, and our slide-style chrome made the carousel look awkward (operator, 2026-10-28)."""
        return category

    # Carousel chrome (operator, 2026-10-28): "the design we added to the Instagram carousel to make it look like
    # a slide is needless as Instagram has its own look". The dossier counter ("ARCHIVAL RECORD // 01 OF 07"),
    # the progress bar, the dots, the cover's SWIPE cue and the badges' slide numbers are gone from both copies;
    # the band the cues reserved at the bottom is the text's again (_footer_top).
    #
    # Brand placement: "RELIC & RIDDLE" sat top-right, exactly where Instagram overlays its "N/M" counter on
    # carousel media. What each platform draws over the media: Instagram, the counter pill at the top-right
    # (about 14 pt in from the corner, about 40 x 26 pt: on a 390 pt phone, ~2.8 canvas px per pt, x 930-1041,
    # y 39-111), a tagged-people icon at the bottom-left on tagged photos (we post none), and the dots below the
    # media, outside it; Facebook's photo grid draws "+N" over the centre of its last tile and its viewer draws
    # nothing over the photo. The top-left corner is clear on both, so the brand is drawn there, small, at the
    # 80 px text margin. A square grid tile crops 135 px off the top of a 4:5 slide: the brand (y 70-92) then
    # drops out whole rather than half cut. IG_COUNTER is checked on every slide (still_layout `protected`), so
    # nothing we draw ever lands under the counter.
    HEADER_Y = 70
    BAND_GAP = 16

    @staticmethod
    def _ig_counter(cw: int) -> Tuple[int, int, int, int]:
        """Where Instagram overlays its carousel counter: the measured pill (above) with room for "10/10" and
        narrower phones: the right 280 px of the top 140 px (design px; the counter is drawn at a fixed share
        of the media's width, so it scales with the canvas)."""
        px = MultiFormatGenerator._px(cw)
        return (cw - px(280), 0, cw, px(140))

    def _zone(self, cw: int, chh: int):
        """Where a slide's text and badges stay: the canvas less 40 px each side (the profile grid's 3:4 crop
        takes 33 px off each side of a 4:5 slide) and 30 px top and bottom (design px)."""
        px = self._px(cw)
        return (px(40), px(30), cw - px(40), chh - px(30))

    def _header_bottom(self, d: ImageDraw.ImageDraw, cw: int, tight: bool) -> int:
        """The bottom of the header band: the brand, top-left."""
        px = self._px(cw)
        b = d.textbbox((px(80), px(self.HEADER_Y)), "RELIC & RIDDLE", font=get_font("Montserrat", px.exact(28)), anchor="lt",
                       stroke_width=px.exact(2))
        return b[3] + (px(10) if tight else 0)

    def _footer_top(self, chh: int) -> int:
        """The lowest y a slide's text may reach: the zone's bottom less two gaps. Nothing is drawn under the
        text any more (the swipe cues are gone, operator 2026-10-28; Instagram's dots sit below the media).
        The canvas is 4:5, so its width follows from `chh`."""
        cw = chh * 4 // 5
        return self._zone(cw, chh)[3] - 2 * self._px(cw)(self.BAND_GAP)

    def _plan_panel(self, d, heading: str, body: str, width: int, room: int, h_spec, b_spec, tight: bool,
                    h_stroke: int = 0, b_stroke: int = 0, h_pitch: float = 1.1, body_max: int = 4, px=round):
        """Heading and body measured to fit `width` x `room`: the heading on one line (shrinking to its floor),
        else on two; the body wrapped, shrunk to 80% of its size, then kept to whole sentences, and only last
        shortened at a word with an ellipsis. Returns the plan the panel is drawn from. `h_spec`, `b_spec` and
        the strokes are design px, `width` and `room` canvas px; the plan's fonts and steps are canvas px (_px)."""
        # Planned in design px (the 1080 slide's sizes, lines and line count), drawn at the canvas's scale.
        s = getattr(px, "s", 1.0)
        width, room = int(round(width / s)), int(room / s)
        h_size, h_min = h_spec
        b_size = b_spec
        if tight:
            h_size, b_size = int(h_size * 0.88), int(b_size * 0.9)
        h_lines, font_h, h_short = fit_text(d, heading, "Anton", h_size, width, 1, h_min, stroke=h_stroke, sentences=False,
                                            quiet=True)
        if h_short:
            h_lines, font_h, h_short = fit_text(d, heading, "Anton", h_size, width, 2, h_min, stroke=h_stroke, sentences=False)
        h_height = len(h_lines) * int(font_h.size * h_pitch)
        b_min = max(28, int(b_size * 0.8))
        b_step = int(round(b_size * 1.38))
        left = room - h_height
        max_lines = max(1, min(body_max, (left - int(b_size * 1.25)) // b_step + 1)) if left > b_size else 1
        b_lines, font_b, b_short = fit_text(d, body, "Montserrat", b_size, width, max_lines, b_min, stroke=b_stroke) \
            if body.strip() else ([], get_font("Montserrat", b_size), False)
        fpx = getattr(px, "exact", px)
        font_h, font_b = get_font("Anton", fpx(font_h.size)), get_font("Montserrat", fpx(font_b.size))
        h_step, b_step = int(font_h.size * h_pitch), int(round(font_b.size * 1.38))
        shortened = (["heading"] if h_short else []) + (["body"] if b_short else [])
        return {"h_lines": h_lines, "font_h": font_h, "h_step": h_step, "b_lines": b_lines, "font_b": font_b,
                "b_step": b_step, "height": len(h_lines) * h_step + len(b_lines) * b_step, "shortened": shortened}

    def _draw_panel(self, d, lay, plan, x: int, y: int, h_fill=WHITE, b_fill=LIGHT, h_stroke=0, b_stroke=0,
                    h_stroke_fill=None, b_stroke_fill=None, gap: int = 0, px=round) -> int:
        """Draws a planned heading and body from `y`; records each as one block; returns the y below them.
        Strokes and `gap` are design px, drawn through `px` (_px)."""
        exact = getattr(px, "exact", px)
        h_stroke, b_stroke, gap = exact(h_stroke), exact(b_stroke), px(gap)
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
            y += max(0, heading_bottom + px(6) - body_top)
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
        text = drawable(text, font)  # no emoji, no missing-glyph boxes (2026-10-29)
        d.text(xy, text, font=font, fill=fill, anchor=anchor, **kw)
        return tuple(d.textbbox(xy, text, font=font, anchor=anchor, stroke_width=kw.get("stroke_width", 0)))

    @staticmethod
    def _badge(d, lay, name, text, x0, y0, font, max_w: int = 920, px=round, **kw):
        """A badge that fits its width, recorded as its pill. `max_w`, the padding, radius and border are design
        px, drawn through `px` (_px)."""
        design = {k: kw.get(k, v) for k, v in (("pad_x", 18), ("pad_y", 8), ("radius", 4), ("border_width", 1))}
        kw = {**kw, **{k: px(v) for k, v in design.items()}}
        family = _font_family(font)
        lines, font, short = MultiFormatGenerator._fit_text(d, text, family, int(round(font.size / getattr(px, "s", 1.0))),
                                                            px(max_w - 2 * design["pad_x"]), 1, 16, px=px,
                                                            sentences=False)
        text = lines[0] if lines else ""
        box = MultiFormatGenerator._draw_badge(d, text, x0, y0, font, **kw)
        if lay is not None:
            lay.add(name, box)
            if short:
                lay.shortened.append(name)
        return box

    def _render_cover(self, photo: Path, card: Dict[str, Any], cw: int, chh: int,
                      lay=None, tight: bool = False) -> Image.Image:
        heading, body = card["heading"].upper(), card["body"]
        px = self._px(cw)
        im = self._fill(photo, (cw, chh))
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(chh // 3, chh):
            a = int(235 * ((y - chh / 3) / (chh * 2 / 3)) ** 1.4)
            d.line([(0, y), (cw, y)], fill=(8, 8, 10, a))
        im = ensure_legible(im, (px(40), chh - px(720), cw - px(40), chh - px(80)), feather=px(60))  # up to 3 heading lines and the body
        d = ImageDraw.Draw(im, "RGBA")
        x, width, h_stroke, b_stroke = px(80), cw - px(160), px.exact(3), px.exact(2)
        font_h = fit_font(d, heading, "Anton", 96 if tight else 110, 920, 56)   # design px, as _fit_text
        lines, font_h, h_short = self._fit_text(d, heading, "Anton", font_h.size, width, 3, 48 if tight else 56, px=px,
                                                stroke=3, sentences=False)
        cover_lines, font_cb, b_short = self._fit_text(d, body, "Montserrat", 32 if tight else 36, width, 2, 28, px=px,
                                                       stroke=2)
        step = int(font_h.size * 1.05)
        # The old placement (heading baselines from chh-470, body two rows under), moved up only when the body
        # would pass the footer line. The SWIPE cue's row it used to clear is gone (operator, 2026-10-28: Instagram
        # shows its own dots), so a long cover keeps the room down to the footer line.
        footer = self._footer_top(chh)
        y = chh - px(470) - (len(lines) - 1) * px(60)
        # The body clears the heading's measured bottom: an accented or descending heading line reaches below
        # its baseline step, and the body overlapped it on every redraw (review, 2026-10-28).
        drop = 0
        if lines and cover_lines:
            last = y + (len(lines) - 1) * step
            h_bottom = d.textbbox((x, last), lines[-1], font=font_h, anchor="ls", stroke_width=h_stroke)[3]
            b_top = d.textbbox((x, last + step + px(20)), cover_lines[0], font=font_cb, anchor="ls", stroke_width=b_stroke)[1]
            drop = max(0, h_bottom + px(6) - b_top)
        body_bottom = y + len(lines) * step + px(20) + drop + px(48) * max(0, len(cover_lines) - 1) + px(12)
        if body_bottom > footer:
            y -= body_bottom - footer
        boxes = []
        for line in lines:
            boxes.append(self._text(d, lay, (x, y), line, font_h, WHITE, anchor="ls", stroke_width=h_stroke,
                                    stroke_fill=(8, 8, 10, 230)))
            y += step
        y += drop
        bboxes = []
        for j, line in enumerate(cover_lines):
            bboxes.append(self._text(d, lay, (x, y + px(20) + px(48) * j), line, font_cb, LIGHT, anchor="ls",
                                     stroke_width=b_stroke, stroke_fill=(8, 8, 10, 220)))
        if lay is not None:
            lay.block("heading", boxes)
            lay.block("body", bboxes)
            lay.shortened += (["heading"] if h_short else []) + (["body"] if b_short else [])
        return im

    def _render_immersive(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """Archetype A: Full-Bleed Immersive Photo with floating translucent charcoal badges."""
        heading, body = card["heading"].upper(), card["body"]
        px = self._px(cw)
        im = self._fill(photo, (cw, chh))
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle([(0, 0), (cw, chh)], fill=(6, 8, 12, 70))
        for y in range(chh // 2, chh):
            a = int(220 * ((y - chh / 2) / (chh / 2)) ** 1.3)
            d.line([(0, y), (cw, y)], fill=(8, 10, 14, a))
        bottom = self._footer_top(chh)
        # Measured first: badge, heading and body stacked upward from the cues, at the old place when they fit.
        top_min = chh // 2 + px(40)                               # where the shading gradient is already deep
        tag = self._tag(self._get_card_category(card, "immersive"), index)
        badge_h = px(90) if tag else 0                            # no badge: the heading starts where it sat
        plan = self._plan_panel(d, heading, body, cw - px(160), bottom - (top_min + badge_h) - px(18), (76, 44), 40, tight,
                                h_stroke=3, b_stroke=2, h_pitch=1.08, px=px)
        block_h = badge_h + plan["height"] + px(18)
        top = min(chh - px(530) + (px(90) - badge_h), bottom - block_h)
        im = ensure_legible(im, (px(40), top - px(30), cw - px(40), chh - px(50)), feather=px(60))  # badge, heading and body
        d = ImageDraw.Draw(im, "RGBA")
        if tag:
            self._badge(d, lay, "badge", tag, px(80), top, get_font("Montserrat", px.exact(24)), text_color=GOLD, border_color=GOLD,
                        border_width=2, radius=8, px=px)
        self._draw_panel(d, lay, plan, px(80), top + badge_h, h_stroke=3, b_stroke=2, h_stroke_fill=(6, 8, 10, 240),
                         b_stroke_fill=(6, 8, 10, 220), gap=18, px=px)
        return im

    def _render_comparison(self, photo1: Path, photo2: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                           lay=None, tight: bool = False) -> Image.Image:
        """Archetype B: Side-by-Side Comparative Card."""
        heading, body = card["heading"].upper(), card["body"]
        px = self._px(cw)
        im = Image.new("RGB", (cw, chh), (12, 14, 18))
        h_half = px(380)
        im.paste(self._fill(photo1, (cw, h_half)), (0, 0))
        im.paste(self._fill(photo2, (cw, h_half)), (0, h_half))
        d = ImageDraw.Draw(im, "RGBA")
        d.line([(0, h_half), (cw, h_half)], fill=GOLD, width=px(3))
        d.line([(0, 2 * h_half), (cw, 2 * h_half)], fill=GOLD, width=px(2))
        # No "REF A // SURFACE CONTEXT" / "REF B // SUBTERRANEAN DEPTH" over the two photos: every comparison
        # slide said so, whatever it showed (it called the Pantheon's dome subterranean; platform audit,
        # 2026-10-28). The photos are matched to the slide's words, not to a role, so nothing is said about them.
        tag = self._tag(self._get_card_category(card, "comparison"), index)
        if tag:
            self._badge(d, lay, "badge", tag, px(80), 2 * h_half + px(25), get_font("Montserrat", px.exact(24)), text_color=GOLD,
                        border_color=GOLD, radius=6, px=px)
        y = 2 * h_half + px(85 if tag else 40)
        plan = self._plan_panel(d, heading, body, cw - px(160), self._footer_top(chh) - y, (64, 38), 38, tight, px=px)
        self._draw_panel(d, lay, plan, px(80), y, px=px)
        return im

    def _render_blueprint(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """Archetype C: Sourced Measurement & Blueprint Card."""
        heading, body = card["heading"].upper(), card["body"]
        px = self._px(cw)
        im = Image.new("RGB", (cw, chh), (10, 16, 24))
        p_h = px(620)
        im.paste(self._fill(photo, (cw, p_h)), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for x in range(px(60), cw, px(70)):
            d.line([(x, p_h), (x, chh)], fill=(20, 36, 52, 70), width=px(1))
        for y_g in range(p_h, chh, px(70)):
            d.line([(0, y_g), (cw, y_g)], fill=(20, 36, 52, 70), width=px(1))
        d.line([(0, p_h), (cw, p_h)], fill=GOLD, width=px(3))
        tag = self._tag(self._get_card_category(card, "blueprint"), index)
        if tag:
            self._badge(d, lay, "badge", tag, px(80), p_h + px(25), get_font("Montserrat", px.exact(24)), text_color=GOLD,
                        border_color=GOLD, fill_color=(8, 12, 18, 230), radius=4, px=px)
        y = p_h + px(85 if tag else 40)
        plan = self._plan_panel(d, heading, body, cw - px(160), self._footer_top(chh) - y, (64, 38), 40, tight, body_max=5,
                                px=px)
        self._draw_panel(d, lay, plan, px(80), y, px=px)
        return im

    def _render_colossal_stat(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                              lay=None, tight: bool = False) -> Image.Image:
        """Archetype D: High-Impact Colossal Stat Card."""
        heading, body = card["heading"].upper(), card["body"]
        px = self._px(cw)
        im = Image.new("RGB", (cw, chh), (11, 14, 18))
        p_h, fade = px(520), px(140)
        im.paste(self._fill(photo, (cw, p_h)), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(p_h - fade, p_h):
            a = int(255 * ((y - (p_h - fade)) / fade) ** 1.5)
            d.line([(0, y), (cw, y)], fill=(11, 14, 18, a))
        tag = self._tag(self._get_card_category(card, "colossal_stat"), index)
        if tag:
            self._badge(d, lay, "badge", tag, px(80), p_h + px(10), get_font("Montserrat", px.exact(24)), text_color=GOLD,
                        border_color=GOLD, fill_color=(16, 20, 26, 230), radius=6, px=px)
        y = p_h + px(65 if tag else 30)
        # The figure is the card's own (its first number, with its unit when it has one); a card without one
        # gets no stat line rather than a label in its place.
        stat_match = re.search(r"(\b\d+[\d,]*\+?(?:\s*(?:TONS?|PEOPLE|FEET|FT|METERS?|M|STORIES|LEVELS))?)\b",
                               f"{heading} {body}", re.IGNORECASE)
        if stat_match:
            stat_lines, f_stat, s_short = self._fit_text(d, stat_match.group(1).upper(), "Anton", 96 if tight else 110,
                                                         cw - px(160), 1, 56, px=px, sentences=False)
            stat_text = stat_lines[0] if stat_lines else ""
            box = self._text(d, lay, (px(80), y), stat_text, f_stat, GOLD)
            if lay is not None:
                lay.add("stat", box)
                if s_short:
                    lay.shortened.append("stat")
            y += int(f_stat.size * 1.1)
        # No "VERIFIED BY ARCHEOLOGICAL RECORD" pill: it was drawn on every stat slide whether or not anything
        # was verified, and by what (platform audit, 2026-10-28). The fact review traces the package's claims,
        # not a source per slide, so no slide can say what verified it; the panel has the room down to the footer.
        plan = self._plan_panel(d, heading, body, cw - px(160), self._footer_top(chh) - y, (62, 38), 38, tight, h_pitch=1.15,
                                px=px)
        self._draw_panel(d, lay, plan, px(80), y, px=px)
        return im

    def _render_debate(self, photo: Path, card: Dict[str, Any], index: int, total: int, cw: int, chh: int, poll: Dict[str, Any], f_small,
                       share: str = "", lay=None, tight: bool = False) -> Image.Image:
        """Archetype E: Forensic Closing Dilemma / Debate Card. `share`: the share-and-follow line, above the
        comments footer (Meta's creator tips, 2026-10-25)."""
        heading, body = card["heading"].upper(), card["body"]
        px = self._px(cw)
        im = Image.new("RGB", (cw, chh), (12, 14, 18))
        p_h, fade = px(560), px(160)
        im.paste(self._fill(photo, (cw, p_h)), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(p_h - fade, p_h):
            a = int(255 * ((y - (p_h - fade)) / fade) ** 1.5)
            d.line([(0, y), (cw, y)], fill=(12, 14, 18, a))
        tag = self._tag(self._get_card_category(card, "debate"), index)
        if tag:
            self._badge(d, lay, "badge", tag, px(80), p_h + px(20), get_font("Montserrat", px.exact(24)), text_color=GOLD,
                        border_color=GOLD, fill_color=(16, 18, 22, 230), radius=6, px=px)
        # No arrow after the call to comment (operator, 2026-10-28: where the comments are differs between
        # Instagram's carousel and Facebook's photo grid). It sits on the footer line, now that the swipe cues
        # under it are gone (it was 120 px up, clear of them).
        footer_y = self._footer_top(chh) - px(8)                          # its baseline: descenders stay above the line
        footer = self._text(d, lay, (px(80), footer_y), "TELL US IN THE COMMENTS", f_small, GOLD, anchor="ls")
        share_lines, f_share, sh_short = (self._fit_text(d, share, "Montserrat", 34, cw - px(160), 2, 28, px=px) if share
                                          else ([], None, False))
        step = int(f_share.size * 1.3) if f_share else 0
        share_top = footer_y - px(56) - step * len(share_lines)           # above the footer
        y = p_h + px(80 if tag else 40)
        room = (share_top - px(24) if share_lines else footer[1] - px(self.BAND_GAP)) - y
        plan = self._plan_panel(d, heading, body, cw - px(160), room, (68, 42), 40, tight, h_pitch=1.15, body_max=3, px=px)
        self._draw_panel(d, lay, plan, px(80), y, px=px)
        if share_lines:
            ys, boxes = share_top, []
            for line in share_lines:
                boxes.append(self._text(d, lay, (px(80), ys), line, f_share, WHITE))
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
        px = self._px(cw)
        p_h, fade = px(self.PHOTO_H), px(180)
        im = Image.new("RGB", (cw, chh), (13, 16, 21))
        im.paste(self._fill(photo, (cw, p_h)), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for y in range(p_h - fade, p_h):
            d.line([(0, y), (cw, y)], fill=(13, 16, 21, int(255 * ((y - (p_h - fade)) / fade) ** 1.6)))
        tag = self._tag(self._get_card_category(card, "editorial_split"), index)
        if tag:
            self._badge(d, lay, "badge", tag, px(80), p_h + px(25), get_font("Montserrat", px.exact(24)), text_color=GOLD,
                        border_color=GOLD, radius=6, px=px)
        y = p_h + px(85 if tag else 40)
        plan = self._plan_panel(d, heading, body, cw - px(160), self._footer_top(chh) - y, (66, 40), 42, tight, body_max=5,
                                px=px)
        self._draw_panel(d, lay, plan, px(80), y, px=px)
        return im

    def _render_carousel_slide(self, archetype: str, photo: Path, photo_next: Path, card: Dict[str, Any], i: int,
                               total: int, poll: Dict[str, Any], share: str, facebook: bool, tight: bool = False):
        """One carousel slide and its measured layout: the archetype's own text, then the brand in its band at the
        top-left. Nothing is drawn under Instagram's counter (`protected`). Both copies are free of slide chrome
        (operator, 2026-10-28); `facebook` now changes only the words: the grid copy's slide text says nothing
        about swiping ("Swipe to see how ..." is drawn "See how ..."), and neither copy draws a direction cue.
        `tight`: smaller type, and the brand on a solid pill so it reads over any photo (Richat 2's header
        "collides with the spacecraft's solar panel")."""
        from .still_layout import Layout
        from .script_engine import facebook_photo_caption, neutral_cues
        clean = facebook_photo_caption if facebook else neutral_cues
        card = dict(card, heading=clean(card.get("heading") or ""), body=clean(card.get("body") or ""))
        share = clean(share or "")
        cw, chh = self.CAROUSEL_SIZE
        lay = Layout("carousel", (cw, chh), self._zone(cw, chh), tight=tight,
                     protected=[("instagram_counter", self._ig_counter(cw))])
        px = self._px(cw)
        f_small = get_font("Montserrat", px.exact(28))
        if archetype == "cover":
            im = self._render_cover(photo, card, cw, chh, lay=lay, tight=tight)
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

        # Shade behind the brand's own extent, not a band across the slide: the band's 90th-percentile
        # luminance was mostly black sky, so a badge over a bright solar panel was left at 1.1:1 (measured 2026-10-25).
        # The brand is top-left, clear of Instagram's counter (see HEADER_Y); no counter, bar or dots of our own.
        d = ImageDraw.Draw(im, "RGBA")
        xy = (px(80), px(self.HEADER_Y))
        brand_w = d.textlength("RELIC & RIDDLE", font=f_small)
        im = ensure_legible(im, (px(50), px(50), int(px(80) + brand_w + px(30)), px(100)), feather=px(60))
        d = ImageDraw.Draw(im, "RGBA")
        box = d.textbbox(xy, "RELIC & RIDDLE", font=f_small, anchor="lt", stroke_width=px.exact(2))
        if tight:  # a solid pill behind the brand, whatever the photo does there
            box = (box[0] - px(12), box[1] - px(8), box[2] + px(12), box[3] + px(8))
            d.rounded_rectangle(box, radius=px(8), fill=(8, 10, 14, 215))
        d.text(xy, "RELIC & RIDDLE", font=f_small, fill=(235, 235, 235, 255), anchor="lt", stroke_width=px.exact(2),
               stroke_fill=(8, 10, 14, 230))
        lay.add("header_brand", box)
        return im, lay

    def generate_carousel_sequence(self, scene_images: List[Path], script_payload: Dict[str, Any], output_dir: Path,
                                   facebook: bool = False) -> List[Path]:
        """Multi-Template Editorial Carousel (CAROUSEL_SIZE, 1440x1800, 4:5):
        Dynamically routes each slide to one of 5 National Geographic / The Economist archetypes
        (Full-Bleed Immersive, Comparative Side-by-Side, Architectural Blueprint, Colossal Stat, Debate).
        facebook=True: the same slides for Facebook's multi-photo post (carousel_fb/). Neither copy has a swipe
        cue, slide counter, progress bar or dots any more (operator, 2026-10-28: Instagram draws its own counter
        and dots; Facebook shows the set as a grid); they differ only in that the grid copy's text never says
        "swipe". The folder and the publisher's per-platform request are kept.
        Each slide is measured and checked after drawing (still_layout); a failing one is drawn once more
        with the tighter fit, and its record is kept in carousel*/still_layout.json."""
        from . import still_layout
        out_dir = output_dir / ("carousel_fb" if facebook else "carousel")
        out_dir.mkdir(parents=True, exist_ok=True)
        stills = [p for p in scene_images if not is_video(p)]
        cards = script_payload["carousel_slides"]
        poll = script_payload.get("poll") or {}
        total = len(cards)
        photos = self._carousel_photos(cards, stills, output_dir)
        from .script_engine import share_prompt
        share = share_prompt(script_payload)  # the closing slide asks for a share and a follow (2026-10-25)
        narration = script_payload.get("narration_script") or ""

        slides = []
        for i, card in enumerate(cards):
            archetype = self.route_carousel_archetype(card, i, total)
            # One unit per run: figures in the unit the narration speaks (2026-10-29), after the photo match.
            card = dict(card, **{k: spoken_units(card[k], narration) for k in ("heading", "body")
                                 if isinstance(card.get(k), str)})
            photo = photos[i]
            photo_next = photos[(i + 1) % len(photos)]
            im, lay, attempts = still_layout.drawn(
                lambda tight: self._render_carousel_slide(archetype, photo, photo_next, card, i, total, poll, share,
                                                          facebook, tight=tight))
            path = out_dir / f"carousel_slide_{i + 1:02d}.jpg"
            self._save_slide(im, path)
            still_layout.save(path, lay, {"renderer": "multi_format.redraw_carousel_slide", "archetype": archetype,
                                          "photo": str(photo), "photo_next": str(photo_next), "card": card, "index": i,
                                          "total": total, "poll": poll, "share": share, "facebook": facebook}, attempts)
            slides.append(path)

        log.info("Generated %d multi-template carousel slides -> %s", len(slides), out_dir)
        return slides


CAROUSEL_PHOTOS = "carousel_photos.json"
_photo_locks: Dict[str, threading.Lock] = {}
_photo_locks_guard = threading.Lock()


def _photo_lock(output_dir: Path) -> threading.Lock:
    with _photo_locks_guard:
        return _photo_locks.setdefault(str(Path(output_dir).resolve()), threading.Lock())


def _recorded_photos(output_dir: Path, cards: List[Dict[str, Any]]) -> List[Path]:
    """The photos a copy drawn before carousel_photos.json existed recorded for these cards ([] if none)."""
    from . import still_layout
    for copy in ("carousel", "carousel_fb"):
        folder = Path(output_dir) / copy
        recs = [still_layout.load(folder / f"carousel_slide_{i + 1:02d}.jpg") or {} for i in range(len(cards))]
        recipes = [r.get("recipe") or {} for r in recs]
        photos = [Path(r.get("photo") or "") for r in recipes]
        same = all((r.get("card") or {}).get("heading") == c.get("heading") and (r.get("card") or {}).get("body") == c.get("body")
                   for r, c in zip(recipes, cards))
        if same and all(str(x) not in ("", ".") and x.is_file() for x in photos):
            return photos
    return []


# ---------------------------------------------------------------- what a posted slide shows
# A slide's record (still_layout.json beside it) keeps its card and photos, so the publisher can say what each
# posted slide shows (alt text), credit every photo it shows, and pick Facebook's photos.

def slide_record(slide: Path) -> Dict[str, Any]:
    from . import still_layout
    return (still_layout.load(Path(slide)) or {}).get("recipe") or {}


def slide_photos(slide: Path) -> List[Path]:
    """The photos a slide shows: its own, and on a comparison slide the second one under it."""
    rec = slide_record(slide)
    photos = [rec.get("photo")] + ([rec.get("photo_next")] if rec.get("archetype") == "comparison" else [])
    return [Path(x) for x in photos if x]


def photo_credit(photo: Path) -> Dict[str, Any]:
    """The credits.json entry of a sourced photo ({} if none). A derived copy (crop, blur, upscale) has no entry
    beside it; its lineage leads back to the original."""
    photo = Path(photo)
    for _ in range(4):
        try:
            data = json.loads((photo.parent / "credits.json").read_text(encoding="utf-8"))
            images = data if isinstance(data, list) else data.get("images", [])
            entry = next((e for e in images if e.get("file") == photo.name), None)
        except (OSError, ValueError, AttributeError):
            entry = None
        if entry:
            return entry
        try:
            from .lineage import sidecar
            photo = Path(json.loads(sidecar(photo).read_text(encoding="utf-8"))["sources"][0]["path"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return {}
    return {}


CAROUSEL_SHARE_ALIKE = "📷 These slides are licensed CC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0/"


CAROUSEL_ADAPTED = "📷 Photos adapted: cropped, with text added."


def carousel_credits(slides: List[Path]) -> Tuple[List[str], bool]:
    """The credit line of every photo the slides show whose licence asks for one, in slide order, once each
    (credits.asset_attribution's format), and whether the slides could be read at all. A CC BY-SA photo makes
    the slides an adaptation under the same licence, which they then say (as the video does)."""
    from .credits import asset_attribution, is_share_alike
    lines, share_alike, readable = [], False, bool(slides)
    for slide in slides:
        if not slide_record(slide):
            readable = False
        for photo in slide_photos(slide):
            entry = photo_credit(photo)
            line = asset_attribution(entry) if entry else ""
            if entry and is_share_alike(entry.get("license", "")):
                share_alike = True
            if line and line not in lines:
                lines.append(line)
    # CC BY 4.0 s3(a)(1)(B): say that the photos were changed (each slide crops its photo and sets text on it)
    adapted = [CAROUSEL_ADAPTED] if lines else []
    return ([CAROUSEL_SHARE_ALIKE] if share_alike else []) + lines + adapted, readable


ALT_TEXT_MAX = 1000  # Instagram's and Upload-Post's limit per photo
_CAMERA_FILE = re.compile(r"\b(?:DSCN?|IMG|DCP)_?\d{3,}\w*\b", re.IGNORECASE)


def _photo_subject(photo: Path) -> str:
    """What a photo shows, in plain words, from its archive record: the description, else the file title."""
    entry = photo_credit(photo)
    text = str(entry.get("description") or "").strip() or re.sub(
        r"^File:|\.(jpe?g|png|tiff?|webp)$", "", str(entry.get("title") or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", _CAMERA_FILE.sub(" ", text)).replace("_", " ")
    text = " ".join(text.split()).strip(" .,-")
    # Its first sentence: uploaders append notes ("Title is mistake. Tree image is taken ...").
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    first = parts[0]
    for more in parts[1:]:
        if len(first) >= 20:
            break
        first += " " + more
    text = first.strip(" .,-")
    return text[:300].rsplit(" ", 1)[0] if len(text) > 300 else text


def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if not text or text[-1] in ".?!" else text + "."


def slide_alt_text(slide: Path) -> str:
    """Alt text for one posted slide: what its photo shows (the archive's own description), then the words on
    it, in the case the script wrote them ("" when the slide has no record). Plain sentences, at most 1,000
    characters; it states nothing the slide and the photo's record do not."""
    rec = slide_record(slide)
    card = rec.get("card") or {}
    if not rec or not (card.get("heading") or card.get("body")):
        return ""
    subjects = [x for x in (_photo_subject(photo) for photo in slide_photos(slide)) if x]
    if len(subjects) > 1:
        picture = f"Two photos: {_sentence(subjects[0])[:-1]}; and {_sentence(subjects[1])}"
    elif subjects:
        picture = f"Photo: {_sentence(subjects[0])}"
    else:
        picture = ""
    from .script_engine import facebook_photo_caption, neutral_cues
    clean = facebook_photo_caption if Path(slide).parent.name == "carousel_fb" else neutral_cues
    words = [_sentence(clean(card.get("heading", ""))), _sentence(clean(card.get("body", "")))]
    if rec.get("archetype") == "debate":
        words += [_sentence(rec.get("share") or ""), "Tell us in the comments."]
    text = " ".join(x for x in [picture, "Text on the slide:", *words] if x)
    if len(text) > ALT_TEXT_MAX:
        text = text[:ALT_TEXT_MAX - 1].rsplit(" ", 1)[0] + "…"
    return text


FACEBOOK_PHOTOS = 4


def facebook_pick(slides: List[str], limit: int = FACEBOOK_PHOTOS) -> List[str]:
    """The slides Facebook's multi-photo post gets: its grid shows four tiles and puts "+N" over the last one, so
    a seven-photo post hid the closing share-and-follow slide (platform audit, 2026-10-28). The cover, the
    closing slide, and between them the strongest content slides: those drawn as designed (no layout problem,
    nothing shortened), showing a photo the post does not already show, then the earliest (the script's order is
    its order of importance: the reveal comes first). Kept in slide order."""
    slides = list(slides)
    if len(slides) <= limit:
        return slides
    from . import still_layout
    first, last, middle = slides[0], slides[-1], list(enumerate(slides[1:-1], 1))
    shown = {str(p) for p in slide_photos(Path(first)) + slide_photos(Path(last))}

    def rank(item):
        i, slide = item
        rec = still_layout.load(Path(slide)) or {}
        photos = {str(p) for p in slide_photos(Path(slide))}
        return (bool(rec.get("problems")), len(rec.get("shortened") or []), bool(photos and photos <= shown), i)

    picked = []
    while middle and len(picked) < limit - 2:  # one at a time: a pick's photos count as shown for the next
        best = min(middle, key=rank)
        middle.remove(best)
        picked.append(best)
        shown |= {str(p) for p in slide_photos(Path(best[1]))}
    return [first] + [slide for _, slide in sorted(picked)] + [last]


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
    gen._save_slide(im, path)
    prior = still_layout.load(path) or {}
    return still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)
