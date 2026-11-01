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
from .typography import (cap_height, drawable, fit_line, fit_text, cover_crop, get_font, is_video,
                         plan_text, record_contrast, spoken_units, text_size, tracked_width, wrap_text)
from . import brand

log = logging.getLogger(__name__)

# Brand tokens (src/brand.py, visual overhaul 2026-10-29): one gold across every surface, bone text, ink grounds.
GOLD = brand.GOLD
WHITE = brand.BONE
LIGHT = brand.BONE
INK = brand.INK


# The poll frame's room for the native poll sticker, which the operator places when posting (y range on the
# 1080x1920 frame). Nothing is drawn there: the frame asks the question and the sticker answers it. It drew its
# own A and B option boxes and "REPLY WITH A OR B" until 2026-10-29: fake poll controls and a set-reply ask,
# which the engagement-bait check itself flags ("Reply A").
POLL_STICKER_ROOM = (1300, 1560)

STORY_BODY_PX = 46      # the Story body's size; never shrunk below it (2026-10-25: 30 px read as ~11 pt; 40 until
                        # the overhaul of 2026-10-29, which sets it at the critic's measured 46-48 px, ~16 pt)
STORY_BODY_LINES = 3
STORY_MARGIN = 84       # the Story's text column starts here, 19 px inside the safe box (2026-10-29: left-aligned)


def story_body_width() -> int:
    """The Story text column: the safe box less STORY_MARGIN's inset on each side (912 px at 1080x1920)."""
    from .safe_zones import safe_box
    sx0, _, sx1, _ = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
    inset = STORY_MARGIN - sx0
    return (sx1 - inset) - (sx0 + inset)


def scrim(im: Image.Image, y0: float, y1: float, feather: int = 300, target: float = 0.30, cap: float = 0.90,
          floor: float = 0.18, colour=None, pct: float = 95) -> Image.Image:
    """An ink gradient behind one text band, deep only as far as the band's own brightness needs (its `pct`
    percentile luminance brought toward `target`), fading over `feather` px above and below: never a box and
    never the whole frame (visual overhaul, 2026-10-29: a 35% shade over every Story and a fixed vignette band
    left bright stone at 3:1 and dark photos darker than they needed). Each line's own halo (place_text) does
    the rest, line by line."""
    import numpy as np
    W, H = im.size
    colour = colour or INK
    a = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    band = a[max(0, int(y0)):max(int(y0) + 1, int(y1))] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    p = float(np.percentile(band, pct)) if band.size else 0.0
    strength = float(np.clip(1 - target / max(p, 1e-3), floor, cap))
    y = np.arange(H, dtype=np.float32)
    v = np.where((y >= y0) & (y <= y1), 1.0, np.where(y < y0, 1 - (y0 - y) / feather, 1 - (y - y1) / feather))
    v = np.clip(v, 0, 1)
    v = v * v * (3 - 2 * v)
    mask = Image.fromarray((v * 255 * strength).astype(np.uint8).reshape(H, 1)).resize((W, H))
    layer = Image.new("RGBA", im.size, tuple(colour) + (255,))
    layer.putalpha(mask)
    out = Image.alpha_composite(im.convert("RGBA"), layer)
    return out if im.mode == "RGBA" else out.convert(im.mode)


KICKER_TRACK = 0.15     # a kicker's letter spacing, as a share of its size (Montserrat 800, tracked caps)
KICKER_RULE = 1.0       # the kicker's gold rule: as long as the capitals are tall
KICKER_GAP = 0.55       # and this far from the words


def kicker_width(text: str, font) -> float:
    """A kicker's drawn width: its gold rule and gap, then the tracked text."""
    ch = cap_height(font)
    return KICKER_RULE * ch + KICKER_GAP * ch + tracked_width(text, font, font.size * KICKER_TRACK)


def draw_kicker(im: Image.Image, x: float, baseline: float, text: str, font, colour=None, lay=None,
                name: str = "kicker", plates=None) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """A kicker: a short gold rule at mid-cap height, then the words in tracked gold capitals (the covers'
    eyebrow). A label, never a pill or a button. A cap-height vertical bar read as a letter I before the words
    ("I DID YOU KNOW?", 2026-10-29), so the rule lies flat."""
    colour = colour or GOLD
    ch = cap_height(font)
    bar_w, gap = max(8, round(ch * KICKER_RULE)), round(ch * KICKER_GAP)
    thick = max(3, round(ch * 0.16))
    mid = baseline - ch / 2
    d = ImageDraw.Draw(im)
    d.rectangle([round(x), round(mid - thick / 2), round(x) + bar_w - 1, round(mid - thick / 2) + thick - 1],
                fill=tuple(colour))
    plate = plan_text(im, (x + bar_w + gap, baseline), text, font, colour, "ls", track=font.size * KICKER_TRACK)
    im = plate.draw(im)
    box = (int(round(x)), min(plate.box[1], int(round(baseline - ch))), plate.box[2], max(plate.box[3], int(round(baseline))))
    if lay is not None:
        lay.add(name, box)
        lay.elements[-1].update(plate.record())
    if plates is not None:
        plates.setdefault(name, []).append(plate)
    return im, box


def _fit_kicker(text: str, font, width: float) -> Tuple[str, bool]:
    """A kicker that fits `width` at this size: whole, else shortened at a word with an ellipsis (logged)."""
    if not text or kicker_width(text, font) <= width:
        return text, False
    words = text.split()
    while len(words) > 1 and kicker_width(" ".join(words) + "…", font) > width:
        words.pop()
    log.warning("Kicker shortened to fit %d px: %s", width, text)
    return " ".join(words) + "…", True


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
            _delivered_contrast(lay, path)
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
            # What each frame credits on itself (Stories carry no caption; story_credit, 2026-10-29)
            "story_credits": {e["frame"]: e["photo_credit"].get("lines", []) for e in layout},
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
        """One Story frame and its measured layout (visual overhaul, 2026-10-29): a left-aligned editorial column
        at STORY_MARGIN, a kicker (gold bar, tracked gold capitals), a two-tone Anton title (bone, then gold), the
        Montserrat body in balanced lines, and on the first frame the pointer to the Reel. Every block is measured
        with the font's real metrics before anything is drawn and placed inside the Story box (safe_zones.STORY:
        Meta's 14% top, 35% bottom, 6% sides), on the calmest band of the photo; an adaptive ink gradient sits
        behind that band only, and each line is drawn at a measured contrast (typography.place_text: >= 4.5:1 on
        the pixels touching its glyphs), the ratio recorded with its box for the post-draw check (still_layout).
        `tight`: the second, tighter fit (smaller title and kicker). A photo whose licence asks for attribution is
        credited at the foot of the box (story_credit), recorded as the `photo_credit` element and in the frame's
        layout.json entry."""
        from .safe_zones import safe_box
        from .still_layout import Layout
        from .visual_engine import calm_center_y
        sx0, sy0, sx1, sy1 = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
        x = STORY_MARGIN
        max_w = story_body_width()
        asks_poll = i == count - 1 and len(poll.get("options", [])) >= 2
        lay = Layout("story", (VIDEO_WIDTH, VIDEO_HEIGHT), (sx0, sy0, sx1, sy1), tight=tight,
                     protected=[("poll_sticker_room", (0, POLL_STICKER_ROOM[0], VIDEO_WIDTH, POLL_STICKER_ROOM[1]))]
                     if asks_poll else None)
        lay.plates = {}
        base = cover_crop(photo, (VIDEO_WIDTH, VIDEO_HEIGHT)).convert("RGB")
        d = ImageDraw.Draw(base)
        # The photo's credit, when its licence asks for one (story_credit; 2026-10-29): at the foot of the Story box,
        # above the reply bar and clear of the poll sticker's room, which both sit lower. Set in the data face (IBM
        # Plex Mono), left-aligned on the text column; its backing is shaded only as far as 4.5:1 needs, before any
        # text is drawn; the frame's text block then stays above it.
        credit = dict(story_credit(photo))
        text_floor = sy1
        c_fill = brand.STONE_LT
        if credit["required"]:
            for c_px in range(STORY_CREDIT_PX, STORY_CREDIT_MIN_PX - 1, -2):  # at most three lines, never under 22 px
                font_c = get_font(brand.DATA_FONT, c_px, weight=500)
                c_drawn = [ln for line in credit["lines"] for ln in _credit_wrap(d, drawable(line, font_c), font_c, max_w)]
                if len(c_drawn) <= STORY_CREDIT_LINES:
                    break
            c_lead = round(c_px * STORY_CREDIT_LEADING / STORY_CREDIT_PX)
            c_bottom = sy1 - 10
            c_top = c_bottom - len(c_drawn) * c_lead
            c_w = max(text_size(d, ln, font_c)[0] for ln in c_drawn)
            backing = (x - 16, c_top - 8, x + c_w + 16, c_bottom + 6)
            before = _contrast(base, backing, c_fill)
            # A soft band of ink across the frame, not a box (a feathered rectangle read as a caption bar, 2026-10-29);
            # each line then gets its own halo if the photo still shows through too brightly.
            base = scrim(base, c_top - 10, c_bottom + 8, feather=90, target=0.16, cap=0.85, floor=0.0)
            d = ImageDraw.Draw(base)
            credit.update(drawn=c_drawn, px=c_px, backing=list(backing), contrast_unshaded=before,
                          contrast=_contrast(base, backing, c_fill),
                          complete=all(drawable(line, font_c) == line for line in credit["lines"]))  # no glyph missing
            text_floor = c_top - STORY_CREDIT_GAP
        kicker, l1, l2 = slide["kicker"].upper(), slide["line1"].upper(), slide["line2"].upper()
        if asks_poll and poll.get("question"):
            l1, l2 = split_balanced(poll["question"].upper())  # the frame asks the question its options answer
        body = slide["body"]
        if asks_poll:  # one question per frame: the poll's; the body keeps only its context
            body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))

        # Measured first: the kicker on one line, both title lines at one size (the larger the better, to the
        # column's width), the body at its fixed size in balanced lines.
        k_px = 34 if tight else 40
        font_k = get_font("Montserrat", k_px, weight=800)
        while kicker and kicker_width(kicker, font_k) > max_w and font_k.size > 24:
            font_k = get_font("Montserrat", font_k.size - 2, weight=800)
        kicker, k_short = _fit_kicker(kicker, font_k, max_w)
        t_size, t_min = (124, 56) if tight else (150, 64)
        size = min(fit_line(d, l1, "Anton", t_size, max_w, t_min, quiet=True)[1].size,
                   fit_line(d, l2, "Anton", t_size, max_w, t_min, quiet=True)[1].size)
        l1, font_t, s1 = fit_line(d, l1, "Anton", size, max_w, size)
        l2, _, s2 = fit_line(d, l2, "Anton", size, max_w, size)
        cap_k, cap_t = cap_height(font_k), cap_height(font_t)
        b_step = round(STORY_BODY_PX * 1.36)
        b_gap = round(STORY_BODY_PX * 1.62)                       # title's baseline to the body's first baseline
        body_lines, font_b, b_short = [], get_font("Montserrat", STORY_BODY_PX), False
        fixed = cap_k + 30 + cap_t + round(cap_t * 1.13) + 40 + (110 if i == 0 else 0)
        room = (text_floor - (sy0 + 30)) - fixed                  # the height left for the body inside the box
        n_room = max(1, (room - b_gap) // b_step + 1)
        # Never smaller than its design size (operator, 2026-10-25). The script stage keeps each body within three
        # lines at this size (story_body_lines); a longer one takes more lines while the box has room, and is cut
        # only when it has none (then at a sentence, else a word, with an ellipsis).
        if body.strip():
            body_lines, font_b, b_short = fit_text(d, body, "Montserrat", STORY_BODY_PX, max_w, STORY_BODY_LINES,
                                                   STORY_BODY_PX, quiet=True)
            if b_short:
                body_lines, font_b, b_short = fit_text(d, body, "Montserrat", STORY_BODY_PX, max_w,
                                                       max(STORY_BODY_LINES, n_room), STORY_BODY_PX)
        lay.shortened = [n for n, short in (("kicker", k_short), ("title_1", s1), ("title_2", s2), ("body", b_short)) if short]

        # Baselines, relative to the block's top.
        rows: List[Tuple[str, float, str, Any, Any, float]] = []   # (name, baseline, text, font, fill, track)
        y = cap_k if kicker else 0
        y_t1 = y + (30 if kicker else 0) + cap_t
        y_t2 = y_t1 + round(cap_t * 1.13)
        rows += [("title_1", y_t1, l1, font_t, WHITE, 0), ("title_2", y_t2, l2, font_t, GOLD, 0)]
        y = y_t2
        for j, line in enumerate(body_lines):
            y = y_t2 + b_gap + j * b_step
            rows.append(("body", y, line, font_b, LIGHT, 0))
        font_p = get_font("Montserrat", 32 if tight else 36, weight=800)
        if i == 0:  # No arrow on the pointer (operator, 2026-10-28): the words say where to go.
            y += 96
            rows.append(("reel_pointer", y, "FULL STORY IN OUR NEW REEL", font_p, GOLD, font_p.size * 0.11))
        bottom = max(d.textbbox((x, yy), txt, font=f, anchor="ls")[3] for _, yy, txt, f, _, _ in rows)
        block = bottom
        top_cand = sy0 + 40 + block // 2
        mid_cand = (sy0 + text_floor) // 2
        bot_cand = text_floor - 20 - block // 2
        cy = calm_center_y(base, candidates=(top_cand, 720, mid_cand, bot_cand), band=block)  # bands inside the Story box
        top = max(sy0 + 30, min(cy - block // 2, text_floor - block))   # above the photo credit, when there is one
        base = scrim(base, top - 40, top + block + 30, feather=320, target=0.28)
        im = base.convert("RGBA")
        if kicker:
            im, k_box = draw_kicker(im, x, top + cap_k, kicker, font_k, GOLD, lay, plates=lay.plates)
        bodies = []
        for name, yy, txt, f, fill, track in rows:
            plate = plan_text(im, (x, top + yy), txt, f, fill, "ls", track=track)
            im = plate.draw(im)
            lay.plates.setdefault(name, []).append(plate)
            if name == "body":
                bodies.append(plate)
            else:
                lay.add(name, plate.box)
                lay.elements[-1].update(plate.record())
        record_contrast(lay, "body", bodies)
        y_end = top + block
        # The poll frame ends on its question: no option boxes and no "REPLY WITH A OR B" (2026-10-29). The
        # native sticker, placed by the operator in POLL_STICKER_ROOM, is the only way to answer it.
        from .still_layout import union
        ux0, uy0, ux1, uy1 = union([tuple(e["box"]) for e in lay.elements])
        if credit["required"]:  # drawn last, recorded as its own element (text_box stays the frame's text block)
            plates = []
            for k, ln in enumerate(c_drawn):
                plate = plan_text(im, (x, c_top + k * c_lead), ln, font_c, c_fill, "la", halo_px=9)
                im = plate.draw(im)
                plates.append(plate)
            lay.plates["photo_credit"] = plates
            credit["box"] = list(record_contrast(lay, "photo_credit", plates))
            # The credit's ratio as drawn: the lowest of its lines on the pixels touching their glyphs.
            measured = [p.contrast for p in plates if p.contrast is not None]
            if measured:
                credit["contrast"] = round(min(measured), 2)
        base = im.convert("RGB")
        lay.meta = {"frame": None, "size": [VIDEO_WIDTH, VIDEO_HEIGHT], "photo_credit": credit,
                    "body_source": " ".join(body.split()), "body_drawn": " ".join(body_lines),
                    "body_px": font_b.size, "body_lines": len(body_lines),
                    "text_box": [min(ux0, x), uy0, max(ux1, x + max_w), max(uy1, int(y_end))],
                    "safe_box": [sx0, sy0, sx1, sy1],
                    **({"poll_sticker_room": [0, POLL_STICKER_ROOM[0], VIDEO_WIDTH, POLL_STICKER_ROOM[1]]}
                       if asks_poll else {})}
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
        relic_keywords = ("gear", "gears", "dial", "dials", "fragment", "fragments", "inscription", "inscriptions",
                          "bronze", "relic", "artifact", "tablet", "device", "pointer", "teeth",
                          "astronomical", "clockwork", "corroded", "ancient computer")
        if any(w in text for w in relic_keywords):
            return "blueprint" if any(w in text for w in ("shaft", "ventilation", "channel", "door", "rolling stone", "tunnel", "chiseled", "engineered", "mechanism", "structure", "architecture", "block")) else "editorial_split"
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
        counter and dots, and our slide-style chrome made the carousel look awkward (operator, 2026-10-28).
        A slide with no category in Facebook's copy carries the subject's name instead (_kicker_subject): each
        photo of a Facebook multi-photo post is opened on its own, without the post's caption."""
        return category or getattr(self, "_kicker_subject", "")

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

    # The overhaul's slide grammar (visual critic round 4, 2026-10-29). Every slide's text is one stack, measured
    # first and set bottom-aligned on one shared line, KEY_BOTTOM less KEY_GAP: the line sits inside the profile
    # grid's 1:1 crop (it takes 135 design px off each end of a 4:5 slide), so the same block reads in the grid,
    # the feed and the viewer. Slides alternate full-bleed photographs and photo-over-ink splits (the router's
    # "immersive" and "editorial_split"; blueprint and stat slides are splits, comparison a two-photo split).
    # Light text is drawn at a measured contrast (typography.place_text), over an adaptive ink gradient behind the
    # block only; no pill, badge outline or box anywhere (operator, 2026-10-28: no slide chrome). Sizes are design
    # px on the 1080-wide canvas, drawn through _px.
    KEY_TOP, KEY_BOTTOM, KEY_GAP = 169, 1181, 18
    MARGIN = 84
    SPLIT_PHOTO_H = 675

    def _stack(self, im: Image.Image, lay, rows: List[Dict[str, Any]], x: int, bottom: int, plates: Dict[str, list]):
        """Draws `rows` (each {"name", "text", "font", "fill", "y": its baseline relative to the stack's top,
        optional "track", "x": an x offset}) so the stack's lowest ink sits on `bottom`; records each row (rows of
        one name as one block, with their lowest measured ratio). Returns (image, the stack's top)."""
        d = ImageDraw.Draw(im)
        drawn = [r for r in rows if r.get("text")]
        if not drawn:
            return im, bottom
        low = max(d.textbbox((x + r.get("x", 0), r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
        off = bottom - low
        by_name: Dict[str, list] = {}
        for r in drawn:
            if r.get("kicker"):
                im, _ = draw_kicker(im, x, off + r["y"], r["text"], r["font"], r["fill"], lay, r["name"], plates)
                continue
            plate = plan_text(im, (x + r.get("x", 0), off + r["y"]), r["text"], r["font"], r["fill"], "ls",
                              track=r.get("track", 0), min_halo=r.get("min_halo", 0.0))
            im = plate.draw(im)
            by_name.setdefault(r["name"], []).append(plate)
            plates.setdefault(r["name"], []).append(plate)
        for name, ps in by_name.items():
            record_contrast(lay, name, ps)
        top = min(d.textbbox((x + r.get("x", 0), off + r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
        return im, top

    def _rows(self, d, px, heading: str, body: str, width: int, kicker: str = "", stat: str = "", h_spec=(112, 60),
              b_lines: int = 3, two_tone: bool = False, scale: float = 1.0, body_spec=(38, 32)):
        """A slide's text stack, measured (sizes in design px x `scale`): an optional kicker, an optional figure
        (its unit beside it, smaller, in bone), the heading on one or two balanced lines, the body in balanced
        lines. Returns (rows, shortened)."""
        fx = getattr(px, "exact", px)
        rows, y, short = [], 0, []
        if kicker:
            fk = get_font("Montserrat", fx(34 * scale), weight=800)
            text, ks = _fit_kicker(kicker, fk, width)
            y += cap_height(fk)
            rows.append({"name": "kicker", "text": text, "font": fk, "fill": GOLD, "y": y, "kicker": True})
            y += px(26 * scale)
            short += ["kicker"] if ks else []
        if stat:
            m = re.match(r"(\S+)\s*(.*)", stat)
            num, unit = m.group(1), m.group(2).strip()
            size = 225 * scale
            while True:
                fs, fu = get_font("Anton", fx(size)), get_font("Anton", fx(max(40, size * 0.42)))
                w = d.textlength(num, font=fs) + (px(18) + d.textlength(unit, font=fu) if unit else 0)
                if w <= width or size <= 96:
                    break
                size -= 6
            y += cap_height(fs)
            rows.append({"name": "stat", "text": num, "font": fs, "fill": GOLD, "y": y})
            if unit:
                rows.append({"name": "stat", "text": unit, "font": fu, "fill": WHITE, "y": y,
                             "x": int(d.textlength(num, font=fs)) + px(18)})
            y += px(30 * scale)
        if two_tone and len(heading.split()) > 1:
            # Two-tone: two balanced lines, each as large as the narrower of the two allows (one size for both).
            halves = [h for h in split_balanced(heading) if h]
            fits = [self._fit_text(d, h, "Anton", int(h_spec[0] * scale), width, 1, int(h_spec[1] * scale), px=px,
                                   sentences=False, quiet=True) for h in halves]
            font_h = min((f[1] for f in fits), key=lambda f: f.size)
            h_short = any(f[2] for f in fits)
            h_lines = [f[0][0] if f[0] else "" for f in fits]
        else:
            h_lines, font_h, h_short = self._fit_text(d, heading, "Anton", int(h_spec[0] * scale), width, 1,
                                                      int(h_spec[1] * scale), px=px, sentences=False, quiet=True)
            if h_short:
                h_lines, font_h, h_short = self._fit_text(d, heading, "Anton", int(h_spec[0] * scale), width, 2,
                                                          int(h_spec[1] * scale * 0.9), px=px, sentences=False)
        ch = cap_height(font_h)
        for j, line in enumerate(h_lines):
            y += ch if j == 0 else round(ch * 1.13)
            rows.append({"name": "heading", "text": line, "font": font_h,
                         "fill": GOLD if (two_tone and j == len(h_lines) - 1) else WHITE, "y": y})
        short += ["heading"] if h_short else []
        if body.strip():
            b_size = int(body_spec[0] * scale)
            b_lines_, font_b, b_short = self._fit_text(d, body, "Montserrat", b_size, width, b_lines,
                                                       min(body_spec[1], b_size), px=px)
            step = round(font_b.size * 1.44)
            y += round(font_b.size * 2.0)
            for j, line in enumerate(b_lines_):
                rows.append({"name": "body", "text": line, "font": font_b, "fill": LIGHT, "y": y + j * step})
            short += ["body"] if b_short else []
        return rows, short

    def _slide_text(self, im: Image.Image, lay, cw: int, chh: int, heading: str, body: str, kicker: str = "",
                    stat: str = "", full: bool = True, two_tone: bool = False, tight: bool = False, b_lines: int = 3,
                    h_spec=(112, 60), extra=None):
        """Plans a slide's stack at the largest scale whose top clears KEY_TOP, shades behind it (a full-bleed
        photo) and draws it bottom-aligned on the shared line. `extra(rows, y)`: rows appended below the body."""
        px = self._px(cw)
        d = ImageDraw.Draw(im)
        width = cw - 2 * px(self.MARGIN)
        bottom = px(self.KEY_BOTTOM - self.KEY_GAP)
        for scale in ((0.88, 0.8, 0.72) if tight else (1.0, 0.92, 0.84, 0.76, 0.7)):
            rows, short = self._rows(d, px, heading, body, width, kicker, stat, h_spec, b_lines, two_tone, scale)
            if extra:
                rows = extra(rows, scale)
            drawn = [r for r in rows if r.get("text")]
            if not drawn:
                break
            low = max(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
            high = min(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
            if bottom - (low - high) >= px(self.KEY_TOP):
                break
        else:
            log.warning("Carousel slide text taller than the key area at its smallest scale: %.60s", heading)
        if lay is not None:
            lay.shortened += short
        drawn = [r for r in rows if r.get("text")]
        if full and drawn:
            low = max(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
            high = min(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
            im = scrim(im, bottom - (low - high) - px(40), chh, feather=px(300), target=0.26)
        plates = getattr(lay, "plates", None) if lay is not None else None
        if plates is None:
            plates = {}
            if lay is not None:
                lay.plates = plates
        im, _ = self._stack(im.convert("RGBA"), lay, rows, px(self.MARGIN), bottom, plates)
        return im.convert("RGB")

    def _split_ground(self, photo: Path, cw: int, chh: int, photo2: Path = None) -> Image.Image:
        """Photo over ink: the photo (or two, stacked) across the top, fading into the ink panel the text sits on."""
        px = self._px(cw)
        ph = px(self.SPLIT_PHOTO_H)
        im = Image.new("RGB", (cw, chh), brand.INK2)
        if photo2 is None:
            im.paste(self._fill(photo, (cw, ph + px(150))), (0, 0))
        else:
            half = (ph + px(150)) // 2
            im.paste(self._fill(photo, (cw, half - px(3))), (0, 0))
            im.paste(self._fill(photo2, (cw, half - px(3))), (0, half + px(3)))
        return scrim(im, ph + px(60), chh, feather=px(180), target=0.0, cap=1.0, floor=1.0, colour=brand.INK2)

    def _render_cover(self, photo: Path, card: Dict[str, Any], cw: int, chh: int,
                      lay=None, tight: bool = False) -> Image.Image:
        """The cover: full-bleed, the heading in two balanced Anton lines (bone, then gold), the body under it."""
        im = self._fill(photo, (cw, chh))
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], two_tone=True, tight=tight,
                                b_lines=2, h_spec=(150, 72))

    def _render_immersive(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """Full-bleed photograph, the stack over an ink gradient at its foot."""
        im = self._fill(photo, (cw, chh))
        tag = self._tag(self._get_card_category(card, "immersive"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, tight=tight,
                                h_spec=(112, 60))

    def _render_comparison(self, photo1: Path, photo2: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                           lay=None, tight: bool = False) -> Image.Image:
        """Two photographs stacked over the ink panel. No "REF A // SURFACE CONTEXT" labels over them: every
        comparison slide said so, whatever it showed (platform audit, 2026-10-28)."""
        im = self._split_ground(photo1, cw, chh, photo2)
        tag = self._tag(self._get_card_category(card, "comparison"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, b_lines=4, h_spec=(96, 56))

    def _render_blueprint(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """A structure slide: the split layout (the drafting grid it drew was decoration that said nothing)."""
        im = self._split_ground(photo, cw, chh)
        tag = self._tag(self._get_card_category(card, "blueprint"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, b_lines=4, h_spec=(96, 56))

    def _render_colossal_stat(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                              lay=None, tight: bool = False) -> Image.Image:
        """A figure slide: the split layout led by the card's own figure (its first number, with its unit when it
        has one); a card without one gets no figure rather than a label in its place. No "VERIFIED BY
        ARCHEOLOGICAL RECORD" pill (platform audit, 2026-10-28)."""
        heading, body = card["heading"].upper(), card["body"]
        im = self._split_ground(photo, cw, chh)
        stat_match = re.search(r"(\b\d+[\d,]*\+?(?:\s*(?:TONS?|PEOPLE|FEET|FT|METERS?|M|STORIES|LEVELS))?)\b",
                               f"{heading} {body}", re.IGNORECASE)
        stat = stat_match.group(1).upper() if stat_match else ""
        if lay is not None and stat:
            lay.meta["stat"] = stat
        tag = self._tag(self._get_card_category(card, "colossal_stat"), index)
        return self._slide_text(im, lay, cw, chh, heading, body, kicker=tag, stat=stat, full=False, tight=tight,
                                b_lines=3, h_spec=(84, 52))

    def _render_debate(self, photo: Path, card: Dict[str, Any], index: int, total: int, cw: int, chh: int, poll: Dict[str, Any], f_small,
                       share: str = "", lay=None, tight: bool = False) -> Image.Image:
        """The closing slide: full-bleed, the heading (its last line gold), the card's question, then the run's
        share-and-follow line (operator decision 2026-10-25) as a sentence under a short gold rule. No "TELL US IN
        THE COMMENTS" footer (operator decision 2026-10-29: a second ask beside the share line)."""
        px = self._px(cw)
        im = self._fill(photo, (cw, chh))
        tag = self._tag(self._get_card_category(card, "debate"), index)
        width = cw - 2 * px(self.MARGIN)
        d = ImageDraw.Draw(im)
        rule = {}

        def with_share(rows, scale):
            if not share:
                return rows
            lines, f_share, sh_short = self._fit_text(d, share, "Montserrat", int(36 * scale), width, 2, 28, px=px)
            if sh_short and lay is not None and "share" not in lay.shortened:
                lay.shortened.append("share")
            last = max((r["y"] for r in rows), default=0)
            rule["y"] = last + px(46 * scale)                       # the rule's top, relative to the stack
            y = rule["y"] + px(6) + px(26 * scale) + cap_height(f_share)
            step = round(f_share.size * 1.38)
            return rows + [{"name": "share", "text": ln, "font": f_share, "fill": WHITE, "y": y + j * step}
                           for j, ln in enumerate(lines)]

        im = self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, two_tone=True,
                              tight=tight, b_lines=3, h_spec=(112, 60), extra=with_share)
        share_box = next((e["box"] for e in (lay.elements if lay is not None else []) if e["name"] == "share"), None)
        if share_box and "y" in rule:
            # The gold rule: 6 design px tall, 96 wide, its own height above the share line's first cap.
            top = share_box[1] - px(26) - px(6)
            ImageDraw.Draw(im).rectangle([px(self.MARGIN), top, px(self.MARGIN) + px(96), top + px(6) - 1], fill=GOLD)
        return im

    def _render_editorial_split(self, photo: Path, card: Dict[str, Any], index: int, cw: int, chh: int,
                                lay=None, tight: bool = False) -> Image.Image:
        """Photo over ink, the stack on the ink panel."""
        im = self._split_ground(photo, cw, chh)
        tag = self._tag(self._get_card_category(card, "editorial_split"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, b_lines=4, h_spec=(96, 56))

    def _render_carousel_slide(self, archetype: str, photo: Path, photo_next: Path, card: Dict[str, Any], i: int,
                               total: int, poll: Dict[str, Any], share: str, facebook: bool, tight: bool = False,
                               subject: str = ""):
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
        # Facebook: every photo after the cover names its subject (2026-11-01). Instagram's swipe keeps the
        # sequence together; Facebook's viewer opens one photo at a time, with the caption on the first only.
        self._kicker_subject = subject.upper() if facebook and i > 0 else ""
        cw, chh = self.CAROUSEL_SIZE
        lay = Layout("carousel", (cw, chh), self._zone(cw, chh), tight=tight,
                     protected=[("instagram_counter", self._ig_counter(cw))])
        lay.plates = {}
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

        # The brand, top-left (clear of Instagram's counter, see HEADER_Y): the gold diamond mark, then Cinzel Bold
        # in tracked bone capitals at the covers' 40 px (one masthead across covers and carousels, 2026-11-01; 30 px
        # before), drawn at a measured contrast like every other line (a halo where the photo is bright), never on
        # a pill.
        from .cover_design import MARK_GAP, MARK_R
        brand_font = get_font("Cinzel", px.exact(40), weight=700)
        r, gap = px(MARK_R), px(MARK_GAP)
        x0, base = px(80), px(self.HEADER_Y) + cap_height(brand_font)
        plate = plan_text(im, (x0 + 2 * r + gap, base), "RELIC & RIDDLE", brand_font, WHITE,
                          "ls", track=px(3), min_halo=0.6 if tight else 0.0, halo_px=36)
        im = plate.draw(im.convert("RGB"))
        cx, cy = x0 + r, base - cap_height(brand_font) / 2
        diamond = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
        d = ImageDraw.Draw(im)
        d.polygon(diamond, outline=INK, width=max(2, px(3)))
        d.polygon(diamond, fill=GOLD)
        # One header element, as before: its box takes in the mark.
        bx = plate.box
        lay.add("header_brand", (min(bx[0], cx - r), min(bx[1], cy - r), max(bx[2], cx + r), max(bx[3], cy + r)))
        lay.elements[-1].update(plate.record())
        lay.plates.setdefault("header_brand", []).append(plate)
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
        subject = standalone_subject(script_payload) if facebook else ""

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
                                                          facebook, tight=tight, subject=subject))
            path = out_dir / f"carousel_slide_{i + 1:02d}.jpg"
            self._save_slide(im, path)
            _delivered_contrast(lay, path)
            still_layout.save(path, lay, {"renderer": "multi_format.redraw_carousel_slide", "archetype": archetype,
                                          "photo": str(photo), "photo_next": str(photo_next), "card": card, "index": i,
                                          "total": total, "poll": poll, "share": share, "facebook": facebook,
                                          "subject": subject}, attempts)
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


# ---------------------------------------------------------------- a Story frame's photo credit
# A Story goes out one frame per request with no caption (publisher.publish_photos: "Stories take none"), so a CC BY
# or BY-SA photo on a frame was credited nowhere (design review, 2026-10-29). CC licences ask for attribution "in any
# reasonable manner based on the medium": on a Story that is the frame itself. The frame names the author, the
# licence and its address, and says it was adapted (each frame crops its photo and sets text on it, as the carousel
# says); a BY-SA photo makes the frame an adaptation under BY-SA, which it then states, as the slides and the video do.
STORY_CREDIT_PX = 26        # ~9.5 pt on a phone: small, and read at a glance like a printed photo credit
STORY_CREDIT_LEADING = 34
STORY_CREDIT_MIN_PX = 22    # a long name or a share-alike notice is set smaller before it takes a fourth line
STORY_CREDIT_LINES = 3
STORY_CREDIT_GAP = 28       # between the frame's text block and the credit (the credit's backing fades over 18 px)
STORY_CREDIT_CONTRAST = 4.5  # WCAG AA for small text, against the pixels under the line
STORY_ADAPTED = "adapted: cropped, with text added"
STORY_SHARE_ALIKE = ("CC BY-SA 4.0", "creativecommons.org/licenses/by-sa/4.0")


def _short_url(url: str) -> str:
    return re.sub(r"^(?:https?:)?//(?:www\.)?", "", str(url or "").strip()).rstrip("/")


def story_credit(photo) -> Dict[str, Any]:
    """What a Story frame showing `photo` must credit on itself: {"required", "lines", ...}. Public-domain and CC0
    photos need none, and our own graphics and maps have no credits entry (their map data is public domain)."""
    from .credits import author_name, is_share_alike, needs_attribution
    entry = photo_credit(Path(photo)) if photo else {}
    if not entry:
        return {"required": False, "reason": "no credits entry (our own graphic, or a photo with no record)", "lines": []}
    licence = " ".join(str(entry.get("license") or "").split())
    if not needs_attribution(entry):
        return {"required": False, "reason": "licence asks for no attribution", "license": licence, "lines": []}
    author, share_alike, url = author_name(entry), is_share_alike(licence), _short_url(entry.get("license_url"))
    lines = [f"Photo: {author} · {licence} · {STORY_ADAPTED}"]
    # An adaptation carries the address of the licence it is under (BY-SA 3.0 s4(b), 4.0 s3(b)): for a BY-SA photo
    # that is BY-SA 4.0, the frame's own; a BY photo's frame gives the photo's licence address (BY 4.0 s3(a)(1)(C)).
    if share_alike:
        lines.append(f"This frame: {STORY_SHARE_ALIKE[0]} · {STORY_SHARE_ALIKE[1]}")
    elif url:
        lines.append(url)
    return {"required": True, "author": author, "license": licence, "license_url": entry.get("license_url") or "",
            "share_alike": share_alike, "source_url": entry.get("source_url") or "", "lines": lines}


def _credit_wrap(d, line: str, font, width: int) -> List[str]:
    """A credit line wrapped at its " · " breaks first (so "adapted: cropped, with text added" is never split from
    itself), and word by word only inside a part that is wider than the line on its own."""
    out: List[str] = []
    for part in line.split(" · "):
        if out and text_size(d, f"{out[-1]} · {part}", font)[0] <= width:
            out[-1] = f"{out[-1]} · {part}"
        else:
            out += wrap_text(d, part, font, width)
    return out


def _contrast(im: Image.Image, box, text_rgb) -> float:
    """WCAG contrast of `text_rgb` against the bright end (90th-percentile luminance) of the pixels in `box`."""
    import numpy as np
    region = np.asarray(im.convert("RGB").crop(tuple(int(v) for v in box)), dtype=np.float32) / 255.0
    lin = np.where(region <= 0.03928, region / 12.92, ((region + 0.055) / 1.055) ** 2.4)
    bg = float(np.percentile(lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), 90))
    return round((_rel_luminance(text_rgb) + 0.05) / (bg + 0.05), 2)


def story_credit_report(frames: List[Path]) -> Dict[str, Any]:
    """For the release report: {"credited": [{"frame", "lines"}], "problems": [...]}. Every frame whose photo's
    licence asks for attribution must carry that credit, drawn whole, at the contrast it needs; a frame without its
    layout record cannot show what it credited, which is a problem too."""
    credited, problems = [], []
    by_dir: Dict[str, Dict[str, Any]] = {}
    for frame in [Path(f) for f in frames]:
        folder = str(frame.parent)
        if folder not in by_dir:
            try:
                by_dir[folder] = {e.get("frame"): e for e in json.loads((frame.parent / "layout.json").read_text(encoding="utf-8"))}
            except (OSError, ValueError, AttributeError):
                by_dir[folder] = {}
        photo = slide_record(frame).get("photo")
        drawn = (by_dir[folder].get(frame.name) or {}).get("photo_credit")
        if not photo or drawn is None:
            problems.append(f"{frame.name}: no layout record of its photo and credit (drawn before 2026-10-29?)")
            continue
        expected = story_credit(photo)
        if not expected["required"]:
            continue
        if drawn.get("lines") != expected["lines"]:
            problems.append(f"{frame.name}: the frame credits {drawn.get('lines')}, its photo needs {expected['lines']}")
        elif not drawn.get("complete"):
            problems.append(f"{frame.name}: the credit could not be drawn whole (a character the font lacks): {drawn.get('drawn')}")
        elif float(drawn.get("contrast") or 0) < STORY_CREDIT_CONTRAST:
            problems.append(f"{frame.name}: the credit's contrast is {drawn.get('contrast')}:1, under {STORY_CREDIT_CONTRAST}:1")
        else:
            credited.append({"frame": frame.name, "lines": expected["lines"]})
    return {"credited": credited, "problems": problems}


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
    if rec.get("archetype") == "debate":  # the closing slide's share line; no comments footer (2026-10-29)
        words += [_sentence(rec.get("share") or "")]
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


def _delivered_contrast(lay, path: Path) -> None:
    """Each placed element's ratio measured again on the saved JPEG (what is posted), written into its layout
    record, which still_layout keeps and video_qa.still_contrast_problems asserts (2026-10-29)."""
    from .typography import remeasure
    plates = getattr(lay, "plates", None)
    if not plates:
        return
    try:
        with Image.open(path) as im:
            remeasure(lay, im.convert("RGB"), plates)
    except OSError as e:  # the planned ratios stay recorded
        log.warning("Contrast not re-measured on %s (%s).", Path(path).name, e)


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
    _delivered_contrast(lay, path)
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


SUBJECT_MAX_WORDS = 6


def standalone_subject(script_payload: Dict[str, Any]) -> str:
    """The subject's name for Facebook's standalone photos: the carousel cover's heading ("THE EYE OF THE SAHARA"),
    the script's own words, never a qualifier it did not state; "" when it is missing or too long to be a name."""
    from .script_engine import facebook_photo_caption
    cards = script_payload.get("carousel_slides") or []
    heading = " ".join(facebook_photo_caption(str((cards[0] if cards else {}).get("heading") or "")).split())
    heading = heading.rstrip(" .!?:")
    return heading if heading and len(heading.split()) <= SUBJECT_MAX_WORDS else ""


def redraw_carousel_slide(path: Path, recipe: Dict[str, Any]) -> Dict[str, Any]:
    """Draws one carousel slide again with the tighter fit, from its kept recipe, in place."""
    from . import still_layout
    gen = MultiFormatGenerator()
    im, lay = gen._render_carousel_slide(recipe["archetype"], Path(recipe["photo"]), Path(recipe["photo_next"]),
                                         recipe["card"], int(recipe["index"]), int(recipe["total"]),
                                         recipe.get("poll") or {}, recipe.get("share") or "", bool(recipe.get("facebook")),
                                         tight=True, subject=recipe.get("subject") or "")
    gen._save_slide(im, path)
    _delivered_contrast(lay, path)
    prior = still_layout.load(path) or {}
    return still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)
