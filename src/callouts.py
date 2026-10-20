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
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from PIL import Image, ImageDraw, ImageFilter
from pydantic import BaseModel

from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .graphics import _text, CLIP_SECONDS, GOLD, TEXT_BOTTOM, TEXT_TOP, WHITE, _ease, _encode, _wrap
from .llm import LLMError, claude_available, image_block, structured_call, task as llm_task
from .typography import cover_crop, get_font, is_video

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
    from . import gemini
    if not ((claude_available() or gemini.available()) and stills and labels):
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
        result = structured_call(SYSTEM, content, CalloutPlan, effort="medium", max_tokens=3000)
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


def render(photo: Path, points: List[tuple], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """Slow push-in on the photo; each feature gets a pulsing dot, a leader line and a label pill,
    one after another. Labels sit inside the text-safe band, on the same side as the feature."""
    base = cover_crop(photo, (width, height)).convert("RGB")
    n = int(CLIP_SECONDS * VIDEO_FPS)
    font = get_font("Montserrat", 28 if width > height else 36)
    measure = ImageDraw.Draw(base, "RGBA")
    top, bottom = (int(height * 0.12), int(height * 0.82)) if width > height else (TEXT_TOP, TEXT_BOTTOM)
    # Label positions: same vertical order as the points, spread evenly through the text-safe band.
    order = sorted(range(len(points)), key=lambda k: points[k][2])
    slots = [top + 60 + k * ((bottom - top - 120) / max(1, len(points) - 1)) for k in range(len(points))]
    layout = {}
    for slot_y, k in zip(slots, order):
        label, x, y = points[k]
        # Position label on the SAME side as the feature to keep leader lines short and direct,
        # avoiding long diagonal lines that slice across the subject
        on_right = x >= width / 2
        lines = _wrap(measure, label.upper(), font, int(width * 0.28) if width > height else 360)[:2]
        tw = max(measure.textlength(ln, font=font) for ln in lines)
        lx = width - 70 - tw if on_right else 70
        layout[k] = (lines, lx, slot_y, tw, on_right)

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            z = 1.0 + 0.06 * (t / CLIP_SECONDS)  # gentle push-in, centred (points scale with it)
            cw, ch = width / z, height / z
            im = base.resize((width, height), Image.LANCZOS, box=((width - cw) / 2, (height - ch) / 2, (width + cw) / 2, (height + ch) / 2)).convert("RGB")
            d = ImageDraw.Draw(im, "RGBA")
            d.rectangle([0, 0, width, height], fill=(0, 0, 0, int(70 * _ease(t / 0.8))))  # dim slightly so labels read
            for k, (label, x, y) in enumerate(points):
                a = _ease((t - 0.5 - 1.0 * k) / 0.5)
                if a <= 0:
                    continue
                px, py = width / 2 + (x - width / 2) * z, height / 2 + (y - height / 2) * z
                lines, lx, ly, tw, on_right = layout[k]
                anchor_x = lx - 16 if on_right else lx + tw + 16
                d.line([(px, py), (anchor_x, ly)], fill=(*GOLD, int(230 * a)), width=4)
                pulse = 14 + 8 * abs(((t * 1.2) % 1.0) - 0.5) * 2
                d.ellipse([px - pulse, py - pulse, px + pulse, py + pulse], outline=(*GOLD, int(255 * a)), width=4)
                d.ellipse([px - 7, py - 7, px + 7, py + 7], fill=(*GOLD, int(255 * a)))
                line_step = 36 if width > height else 46
                hh = line_step * len(lines)
                d.rounded_rectangle([lx - 18, ly - hh / 2 - 8, lx + tw + 18, ly + hh / 2 + 8], radius=14,
                                    fill=(10, 10, 12, int(205 * a)), outline=(*GOLD, int(220 * a)), width=2)
                for j, line in enumerate(lines):
                    _text(d, (lx, ly - hh / 2 + 20 + line_step * j), line, font=font, fill=(*WHITE, int(255 * a)), anchor="lm")
            yield im

    return _encode(frames(), out, n, width=width, height=height)


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
