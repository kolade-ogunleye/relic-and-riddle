"""
src/callouts.py
===============
Labelled photos: a real photo of the site with animated callouts naming the features the
narration talks about ("ventilation shaft", "rolling stone door"). Original, educational, and it
tells viewers what they're looking at.

Truthfulness: labels come only from the script's photo_labels (features named in the fact-checked
narration). Claude's vision looks at the photos exactly as the video crops them (9:16) and must
locate a feature with HIGH confidence for its label to be drawn; it picks the one photo that shows
the most features. Fewer than two confident features -> no graphic. The reviewer sees it in the video.
"""

import logging
from itertools import pairwise
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from PIL import Image, ImageDraw
from pydantic import BaseModel

from . import safe_zones
from .brand import INK, rgba
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .graphics import (CLIP_SECONDS, GOLD, TEXT_BOTTOM, TEXT_TOP, WHITE, _ease, _ease_io, _encode, get_text_bounds,
                       get_text_box, record_contrast)
from .llm import LLMError, image_block, task as llm_task
from .typography import balanced_wrap, cover_crop, fit_lines, get_font, is_video, plan_text, wrap_text

log = logging.getLogger(__name__)
W, H = VIDEO_WIDTH, VIDEO_HEIGHT
MAX_PHOTOS = 6


class Located(BaseModel):
    label: str
    visible: bool
    x: float                              # 0-1 across the image, at the feature
    y: float                              # 0-1 down the image
    confidence: Literal["high", "medium", "low"]


class CalloutPlan(BaseModel):
    image: int                            # 1-based number of the best photo (0 if none shows two features)
    features: List[Located]


SYSTEM = """You annotate photos for a history documentary. You get numbered photos of one site and a list
of physical features. Pick the ONE photo that clearly shows the most of these features, and for each
feature say whether it is visible in that photo and where (x, y as fractions of the width and height,
pointing at the feature itself). Use confidence "high" only when you are sure that exact feature is
what you are pointing at; never guess. If no photo clearly shows at least two features, return image 0."""


@llm_task("photo_callouts")
def plan(photos: List[Path], labels: List[str], topic_title: str, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[tuple]:
    """(photo, [(label, x_px, y_px)]) for confidently located features, or None."""
    stills = [p for p in photos if not is_video(p)][:MAX_PHOTOS]
    from . import free_llm
    if not (free_llm.ready() and stills and labels):
        return None
    crops = []
    content: List[dict] = []
    for i, p in enumerate(stills, 1):
        crop = cover_crop(p, (width, height)).convert("RGB")  # exactly what the video shows
        tmp = p.parent / f".callout_crop_{i}.jpg"
        crop.resize((576, 1024)).save(tmp, quality=88)
        crops.append(tmp)
        content += [{"type": "text", "text": f"Photo {i}:"}, image_block(tmp, 1024)]
    content.append({"type": "text", "text": f"Site: {topic_title}\nFeatures: " + "; ".join(labels)})
    try:
        result = free_llm.call(SYSTEM, content, CalloutPlan, role="checker", task_class="light",
                               effort="medium", max_tokens=3000)
    except LLMError as e:
        log.warning("Callout planning failed: %s", e)
        return None
    finally:
        for c in crops:
            c.unlink(missing_ok=True)
    log.info("Callouts plan: photo %d; %s", result.image,
             "; ".join(f"{f.label}={f.confidence}{'' if f.visible else ' (not visible)'}" for f in result.features))
    if not 1 <= result.image <= len(stills):
        return None
    wanted = {lbl.lower() for lbl in labels}
    top, bottom = (int(height * 0.12), int(height * 0.82)) if width > height else (TEXT_TOP, TEXT_BOTTOM)
    points = [(f.label, f.x * width, f.y * height) for f in result.features
              if f.visible and f.confidence == "high" and f.label.lower() in wanted
              and 0.05 < f.x < 0.95 and (top - 80) <= (f.y * height) <= (bottom + 40)]
    if len(points) < 2:
        log.info("Callouts: fewer than two confidently located features in safe zone; skipped.")
        return None
    return stills[result.image - 1], points[:4]


# Label geometry and motion (panel round 2, 2026-11-02). The labels stay inside the Reel text box on 9:16 (the
# right-hand label used to end at x 1028, under the Shorts/Reels action rail past x 888, and the left one started
# at x 52, inside Meta's 65 px inset) and inside broadcast title-safe on 16:9. Each leader is a 3 px gold line on
# a 5 px ink halo either side and each dot has an ink ring, so neither vanishes on gold rock; on 16:9 the leaders
# are elbows (horizontal, then vertical). The label is Montserrat Bold on an ink plate (brand INK at 230/255),
# at most two lines, its text starting at the edge the leader arrives at; the leader draws first, then the label
# fades in. Every label line is drawn at a measured contrast and recorded beside the clip (<clip>.contrast.json).
LABEL_PX = {"portrait": 42, "landscape": 36}
LABEL_MAX_W = {"portrait": 400, "landscape": 460}   # the label's text column, before its plate padding
PLATE_PAD_X, PLATE_PAD_Y, PLATE_ALPHA = 18, 12, 230
LEADER_W, LEADER_HALO = 3, 5                         # gold line width; ink halo on each side of it
DOT_AT, LEADER_DRAW, LABEL_FADE, STAGGER = 0.5, 0.3, 0.3, 1.0   # seconds
ELBOW_RUN, ELBOW_RISE = 120, 110                     # 16:9: horizontal run from the dot, vertical gap to the plate


def text_box(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) every label plate stays inside: on 9:16 the Reel box 18 px in on each side (x 83..870
    at 1080), between the graphics' text rows; on 16:9 broadcast title-safe (graphics.get_text_box)."""
    if width > height:
        return get_text_box(width, height)
    x0, _, x1, _ = safe_zones.safe_box("reel", width, height)
    top, bottom = get_text_bounds(width, height)
    return x0 + PLATE_PAD_X, top, x1 - PLATE_PAD_X, bottom


def _fit_label(measure: ImageDraw.ImageDraw, text: str, size: int, max_w: int) -> tuple[list[str], Any]:
    """The label on at most two balanced lines in Montserrat Bold, shrinking to four fifths of `size` first."""
    for s in range(size, int(size * 0.8) - 1, -2):
        f = get_font("Montserrat", s, weight=700)
        lines = wrap_text(measure, text, f, max_w)
        if len(lines) <= 2:
            return (balanced_wrap(measure, text, f, max_w) if len(lines) > 1 else lines), f
    return fit_lines(measure, text, get_font("Montserrat", int(size * 0.8), weight=700), max_w, 2)


def _overlaps(a: tuple, b: tuple, gap: int = 16) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _partial(pts: list[tuple[float, float]], f: float) -> list[tuple[float, float]]:
    """The polyline `pts` drawn to fraction `f` of its length."""
    seg = [((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 for (ax, ay), (bx, by) in pairwise(pts)]
    goal, out = sum(seg) * max(0.0, min(1.0, f)), [pts[0]]
    for ((ax, ay), (bx, by)), s in zip(pairwise(pts), seg):
        if goal >= s:
            out.append((bx, by))
            goal -= s
            continue
        if s > 0 and goal > 0:
            out.append((ax + (bx - ax) * goal / s, ay + (by - ay) * goal / s))
        break
    return out


def layout_labels(points: list[tuple], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> list[dict[str, Any]]:
    """Where each label goes: its lines and font, its plate box (inside text_box), where its text starts, and the
    leader's fixed end. In the order of `points`."""
    landscape = width > height
    key = "landscape" if landscape else "portrait"
    bx0, by0, bx1, by1 = text_box(width, height)
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    order = sorted(range(len(points)), key=lambda k: points[k][2])
    slots = [by0 + 60 + j * ((by1 - by0 - 120) / max(1, len(points) - 1)) for j in range(len(points))]
    placed: dict[int, dict[str, Any]] = {}
    boxes: list[tuple] = []
    for slot_y, k in zip(slots, order):
        label, x, y = points[k]
        lines, font = _fit_label(measure, label.upper(), LABEL_PX[key], LABEL_MAX_W[key])
        step = round(font.size * 1.2)
        tw = int(max(measure.textlength(ln, font=font) for ln in lines))
        pw, ph = tw + 2 * PLATE_PAD_X, step * len(lines) + 2 * PLATE_PAD_Y
        on_right = x >= width / 2
        if landscape:   # elbow: out from the dot, then up or down to the plate's near edge
            ex = x + ELBOW_RUN if on_right else x - ELBOW_RUN
            ex = min(max(ex, bx0 + PLATE_PAD_X), bx1 - pw + PLATE_PAD_X) if on_right else \
                max(min(ex, bx1 - PLATE_PAD_X), bx0 + pw - PLATE_PAD_X)
            x0 = ex - PLATE_PAD_X if on_right else ex + PLATE_PAD_X - pw
            above = y > (by0 + by1) / 2
            y0 = y - ELBOW_RISE - ph if above else y + ELBOW_RISE
        else:           # a straight leader to the plate edge facing the feature
            x0 = bx1 - pw if on_right else bx0
            y0 = slot_y - ph / 2
            above = y > slot_y
        y0 = min(max(y0, by0), by1 - ph)
        box = [x0, y0, x0 + pw, y0 + ph]
        dot = (x - 30, y - 30, x + 30, y + 30)
        for _ in range(6):   # off the dot and off the labels already placed
            hit = [b for b in boxes if _overlaps(tuple(box), b)] + ([dot] if _overlaps(tuple(box), dot, 0) else [])
            if not hit:
                break
            for b in hit:
                dy = (b[1] - 16 - box[3]) if above else (b[3] + 16 - box[1])
                box[1] += dy
                box[3] += dy
            if box[1] < by0 or box[3] > by1:   # no room that way: try the other
                above = not above
                box[1] = min(max(box[1], by0), by1 - ph)
                box[3] = box[1] + ph
        box = [round(v) for v in box]
        boxes.append(tuple(box))
        cy = (box[1] + box[3]) / 2
        if landscape:
            end = (ex, box[3] if box[3] <= y else box[1] if box[1] >= y else cy)
            elbow = (ex, y)
        else:
            end, elbow = ((box[0], cy) if on_right else (box[2], cy)), None
        tx = box[0] + PLATE_PAD_X if on_right else box[2] - PLATE_PAD_X
        placed[k] = {"lines": lines, "font": font, "step": step, "box": box, "on_right": on_right,
                     "text_x": tx, "anchor": "lm" if on_right else "rm", "end": end, "elbow": elbow}
    return [placed[k] for k in range(len(points))]


def render(photo: Path, points: List[tuple], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """Slow push-in on the photo; each feature in turn gets a pulsing dot, then its leader draws out (0.3 s),
    then its label fades in. Labels stay inside the platforms' text box, on the same side as the feature."""
    base = cover_crop(photo, (width, height)).convert("RGB")
    n = int(CLIP_SECONDS * VIDEO_FPS)
    labels = layout_labels(points, width, height)

    def background(t: float) -> Image.Image:
        z = 1.0 + 0.06 * (t / CLIP_SECONDS)  # gentle push-in, centred (points scale with it)
        cw, ch = width / z, height / z
        im = base.resize((width, height), Image.LANCZOS,
                         box=((width - cw) / 2, (height - ch) / 2, (width + cw) / 2, (height + ch) / 2)).convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle([0, 0, width, height], fill=(0, 0, 0, int(70 * _ease(t / 0.8))))  # dim slightly so labels read
        return im

    def plates_on(im: Image.Image, lab: dict[str, Any], a: float) -> None:
        d = ImageDraw.Draw(im, "RGBA")
        d.rounded_rectangle(lab["box"], radius=12, fill=rgba(INK, int(PLATE_ALPHA * a)), outline=rgba(GOLD, int(220 * a)),
                            width=2)

    # The label lines are planned on the clip's last frame, plates up, and drawn from those plans on every frame.
    t_end = (n - 1) / VIDEO_FPS
    ref = background(t_end)
    for lab in labels:
        plates_on(ref, lab, 1.0)
    for lab in labels:
        cy = (lab["box"][1] + lab["box"][3]) / 2
        first = cy - lab["step"] * (len(lab["lines"]) - 1) / 2
        lab["plates"] = [plan_text(ref, (lab["text_x"], round(first + j * lab["step"])), line, lab["font"], WHITE,
                                   anchor=lab["anchor"]) for j, line in enumerate(lab["lines"])]
    final: dict[str, Image.Image] = {}

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            z = 1.0 + 0.06 * (t / CLIP_SECONDS)
            im = background(t)
            for k, (_label, x, y) in enumerate(points):
                s = DOT_AT + STAGGER * k
                a_dot = _ease((t - s) / 0.25)
                if a_dot <= 0:
                    continue
                lab = labels[k]
                px, py = width / 2 + (x - width / 2) * z, height / 2 + (y - height / 2) * z
                path = [(px, py), *([(lab["elbow"][0], py)] if lab["elbow"] else []), lab["end"]]
                drawn = _partial(path, _ease_io((t - s - 0.1) / LEADER_DRAW))
                d = ImageDraw.Draw(im, "RGBA")
                if len(drawn) > 1:
                    d.line(drawn, fill=rgba(INK, int(200 * a_dot)), width=LEADER_W + 2 * LEADER_HALO, joint="curve")
                    d.line(drawn, fill=rgba(GOLD, int(255 * a_dot)), width=LEADER_W, joint="curve")
                pulse = 14 + 8 * abs(((t * 1.2) % 1.0) - 0.5) * 2
                d.ellipse([px - pulse - 3, py - pulse - 3, px + pulse + 3, py + pulse + 3], outline=rgba(INK, int(190 * a_dot)), width=9)
                d.ellipse([px - pulse, py - pulse, px + pulse, py + pulse], outline=rgba(GOLD, int(255 * a_dot)), width=3)
                d.ellipse([px - 10, py - 10, px + 10, py + 10], fill=rgba(INK, int(230 * a_dot)))
                d.ellipse([px - 7, py - 7, px + 7, py + 7], fill=rgba(GOLD, int(255 * a_dot)))
                la = _ease((t - s - 0.1 - LEADER_DRAW) / LABEL_FADE)
                if la > 0:
                    plates_on(im, lab, la)
                    for plate in lab["plates"]:
                        im = plate.draw(im, la)
            final["frame"] = im
            yield im

    clip = _encode(frames(), out, n, width=width, height=height)
    if "frame" in final:
        record_contrast(clip, final["frame"], [(f"label_{k + 1}_{j + 1}", p) for k, lab in enumerate(labels)
                                               for j, p in enumerate(lab["plates"])])
    return clip


def make(payload: Dict[str, Any], topic: Dict[str, Any], photos: List[Path], work_dir: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Optional[Path]:
    """The labelled-photo graphic if the script names features and a photo confidently shows two or more."""
    from .config import CALLOUTS_ENABLED
    if not CALLOUTS_ENABLED:
        return None
    labels = payload.get("photo_labels") or []
    if len(labels) < 2:
        return None
    try:
        found = plan(photos, labels, topic.get("title", ""), width=width, height=height)
        if not found:
            return None
        photo, points = found
        work_dir.mkdir(parents=True, exist_ok=True)
        return render(photo, points, work_dir / "graphic_labels.mp4", width=width, height=height)
    except Exception as e:  # optional graphic: never sink a run
        log.warning("Labelled photo failed: %s", e)
        return None
