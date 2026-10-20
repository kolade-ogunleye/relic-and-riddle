"""
src/then_now.py
===============
"Then and now": an old photograph of the site (1960 or earlier) and a recent one (2000 or later),
revealed with a wipe, each labelled with the year its own Commons record states.

Honesty rules: years come only from each photo's recorded capture date (exactly one year stated;
"circa"/ranges are skipped); both photos must pass the licence filter; a vision check must say,
with high confidence, that they show the same place from a comparable view. Otherwise no graphic.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import requests
from PIL import Image, ImageDraw
from pydantic import BaseModel

from . import commons, config
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .credits import normalize
from .graphics import _text, CLIP_SECONDS, GOLD, TEXT_TOP, WHITE, _ease, _encode
from .llm import LLMError, claude_available, image_block, structured_call, task as llm_task
from .typography import cover_crop, get_font
from .upscale import ensure_resolution

log = logging.getLogger(__name__)
W, H = VIDEO_WIDTH, VIDEO_HEIGHT
OLD_BEFORE, NEW_FROM = 1960, 2000
# Archive/catalogue words in old photo titles that would derail a search for the same subject today.
ARCHIVE_WORDS = {"photochrom", "postcard", "lantern", "slide", "stereograph", "stereo", "print", "loc", "ppmsc",
                 "cropped", "restored", "crop", "view", "photograph", "photo", "card", "jpg", "colour", "color", "the", "of"}


class PairPick(BaseModel):
    old_is_photograph: bool               # a real photograph, not a painting, print or drawing
    graphic_or_sensitive: bool            # dead or injured people, atrocities: never used
    best_match: int                       # 1-based recent photo with the same place and a comparable view; 0 = none
    confidence: Literal["high", "medium", "low"]
    note: str


@llm_task("then_now_check")
def _pick(old: Path, recents: List[Path], place: str) -> PairPick:
    content = [{"type": "text", "text": "OLD photo:"}, image_block(old, 768)]
    for i, r in enumerate(recents, 1):
        content += [{"type": "text", "text": f"Recent {i}:"}, image_block(r, 512)]
    content.append({"type": "text", "text": (
        f"Named place: {place}. Is the OLD image a real photograph (not a painting, print or drawing)? Does either "
        "image show dead or injured people or other graphic or sensitive content? Which recent "
        "photo, if any, shows the same place from a comparable viewpoint, so a 'then and now' comparison is honest? "
        "Be strict; answer 0 if none.")})
    return structured_call("You verify archive photo pairs for a history documentary.", content, PairPick,
                           effort="low", max_tokens=1000)


def _candidates(query: str, keywords: List[str], old: bool) -> List[Dict[str, Any]]:
    pages = commons.search(query, limit=40, max_wait=60)
    found = commons.candidates(pages, keywords, config.BEAT_IMAGES_ALLOW_SHARE_ALIKE,
                               **({"min_pixels": 400_000, "min_height": 600} if old else {}))
    return [c for c in found if c.get("year") and ((c["year"] <= OLD_BEFORE) if old else (c["year"] >= NEW_FROM))]


def source(topic: Dict[str, Any], work_dir: Path) -> Optional[Tuple[Path, Dict[str, Any], Path, Dict[str, Any]]]:
    """(old photo, its credit, new photo, its credit) for a verified pair, or None."""
    loc = topic.get("location", {})
    name = (loc.get("name") or "").strip()
    if not (name and claude_available()):
        return None
    keywords = [normalize(w) for w in name.split() if len(w) > 3]
    try:
        olds = _candidates(f"{name} historical photograph", keywords, old=True) + _candidates(name, keywords, old=True)
    except requests.RequestException as e:
        log.warning("Then-and-now search failed: %s", e)
        return None
    if not olds:
        log.info("Then-and-now: no dated old/new pair on Commons for %s.", name)
        return None
    work_dir.mkdir(parents=True, exist_ok=True)
    place = ", ".join(v for v in (name, loc.get("country")) if v)
    seen_recent: Dict[str, Path] = {}

    def recent_for(old: Dict[str, Any], k: int) -> List[Tuple[Path, Dict[str, Any]]]:
        """Recent photos of the same subject: searched with the old photo's own title (its street,
        building or chamber), then the site name; downloaded once each."""
        subject = re.sub(r"\.(jpe?g|png|tiff?)$", "", old["title"].replace("File:", ""), flags=re.I)
        subject = " ".join(w for w in re.split(r"[^\w']+", subject)
                           if w and not w.isdigit() and w.lower() not in ARCHIVE_WORDS)[:80]
        pool, out = [], []
        for q in (subject, f"{name} {loc.get('country', '')}".strip()):  # the country keeps out namesakes (Odessa, Texas)
            try:
                pool += [c for c in _candidates(q, keywords, old=False) if c["title"] not in {p["title"] for p in pool}]
            except requests.RequestException:
                continue
        for j, new in enumerate(pool[:6]):
            path = seen_recent.get(new["title"]) or work_dir / f"now_{k}_{j}.jpg"
            try:
                if not path.exists():
                    commons.download_jpeg(new["download_url"], path)
                seen_recent[new["title"]] = path
                out.append((path, new))
            except (requests.RequestException, OSError) as e:
                log.info("Recent photo skipped: %s", e)
        return out

    for k, old in enumerate(olds[:4]):  # one vision call per old photo, against recent photos of its subject
        op = work_dir / f"then_{k}.jpg"
        recents = recent_for(old, k)
        if not recents:
            continue
        try:
            if not op.exists():
                commons.download_jpeg(old["download_url"], op)
            pick = _pick(op, [r[0] for r in recents], place)
        except (requests.RequestException, OSError, LLMError) as e:
            log.info("Then-and-now candidate skipped: %s", e)
            continue
        if (pick.old_is_photograph and not pick.graphic_or_sensitive and 1 <= pick.best_match <= len(recents)
                and pick.confidence == "high"):
            np_, new = recents[pick.best_match - 1]
            return ensure_resolution(op), {**old, "file": str(op)}, np_, {**new, "file": str(np_)}
        log.info("Then-and-now: no honest match for old photo %d: %s", k + 1, pick.note[:160])
    return None


def render(old: Path, old_year: int, new: Path, new_year: int, out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """The old photo first, then a gold line wipes across to reveal the recent one; year badges on each."""
    a = cover_crop(old, (width, height)).convert("RGB")
    b = cover_crop(new, (width, height)).convert("RGB")
    n = int(CLIP_SECONDS * VIDEO_FPS)
    font = get_font("Anton", 60 if width > height else 72)
    top = int(height * 0.12) if width > height else TEXT_TOP
    badge_h = 80 if width > height else 100

    def badge(im: Image.Image, text: str, x: int, alpha: float) -> None:
        d = ImageDraw.Draw(im, "RGBA")
        tw = d.textlength(text, font=font)
        d.rounded_rectangle([x, top, x + tw + 40, top + badge_h], radius=14, fill=(10, 10, 12, int(210 * alpha)),
                            outline=(*GOLD, int(230 * alpha)), width=3)
        _text(d, (x + 20, top + badge_h // 2), text, font=font, fill=(*WHITE, int(255 * alpha)), anchor="lm")

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            p = _ease((t - 2.2) / 1.6)  # hold on "then", wipe to "now"
            xw = int(width * p)
            im = a.copy()
            if xw > 0:
                im.paste(b.crop((0, 0, xw, height)), (0, 0))
            im = im.convert("RGB")
            d = ImageDraw.Draw(im, "RGBA")
            if 0 < p < 1:
                d.line([(xw, 0), (xw, height)], fill=(*GOLD, 255), width=6)
            badge(im, str(old_year), 70, 1 - p)
            badge(im, str(new_year), width - 70 - int(ImageDraw.Draw(im, "RGBA").textlength(str(new_year), font=font)) - 40, p)
            yield im

    return _encode(frames(), out, n, width=width, height=height)


def make(topic: Dict[str, Any], work_dir: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Tuple[Optional[Path], List[Dict[str, Any]]]:
    """(clip, credits of both photos) or (None, [])."""
    try:
        found = source(topic, work_dir)
        if not found:
            return None, []
        old, old_c, new, new_c = found
        return render(old, old_c["year"], new, new_c["year"], work_dir / "graphic_thennow.mp4", width=width, height=height), [old_c, new_c]
    except Exception as e:  # optional graphic
        log.warning("Then-and-now failed: %s", e)
        return None, []
