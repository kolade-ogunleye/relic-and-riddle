"""
src/then_now.py
===============
"Then and now": an old photograph of the site (1960 or earlier) and a recent one (2000 or later),
revealed with a wipe, each labelled with the year its own Commons record states.

Honesty rules: years come only from each photo's recorded capture date (exactly one year stated;
"circa"/ranges are skipped); both photos must pass the licence filter; a vision check must say,
with high confidence, that they show the same place from a comparable view; and the chosen pair must
pass the viewpoint gate (feature geometry AND a second vision check: same structure, similar camera
position). Otherwise no graphic. A pair that passes is registered (the recent photo warped onto the
old one, both cropped to what they share) so the wipe reveals the same structure in the same place.
"""

import logging
import re
from pathlib import Path
from typing import Any, Literal

import requests
from PIL import Image, ImageDraw
from pydantic import BaseModel

from . import commons, config, safe_zones
from .brand import BONE, DATA_FONT, INK, PLACE_TARGET, rgba
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .credits import normalize
from .graphics import (
    CLIP_SECONDS,
    GOLD,
    TEXT_TOP,
    WHITE,
    _ease,
    _ease_io,
    _encode,
    get_text_box,
    record_contrast,
)
from .llm import LLMError, image_block
from .llm import task as llm_task
from .typography import cover_crop, get_font, plan_text
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
def _pick(old: Path, recents: list[Path], place: str) -> PairPick:
    content = [{"type": "text", "text": "OLD photo:"}, image_block(old, 768)]
    for i, r in enumerate(recents, 1):
        content += [{"type": "text", "text": f"Recent {i}:"}, image_block(r, 512)]
    content.append({"type": "text", "text": (
        f"Named place: {place}. Is the OLD image a real photograph (not a painting, print or drawing)? Does either "
        "image show dead or injured people or other graphic or sensitive content? Which recent "
        "photo, if any, shows the same place from a comparable viewpoint, so a 'then and now' comparison is honest? "
        "Be strict; answer 0 if none.")})
    from . import free_llm
    return free_llm.call("You verify archive photo pairs for a history documentary.", content, PairPick,
                         role="checker", task_class="light", effort="low", max_tokens=1000)


# Viewpoint gate (operator round 3: a then-and-now whose two photos do not share a viewpoint must not be shown).
# The pick above only chose the best of several recent photos; the chosen pair is then checked twice, and both
# must pass (fail closed: an error, a missing library or an unclear answer is a no):
#  1. geometry: ORB features matched between the two (Lowe's ratio test), then a RANSAC similarity transform
#     (rotation, uniform scale, shift); at least MIN_INLIERS consistent matches and a plausible transform (scale
#     within 1/MAX_SCALE..MAX_SCALE, rotation within MAX_ROTATION degrees) mean the same structure seen from about
#     the same place. Photos of the same site from elsewhere (the interior against the exterior) match nothing.
#  2. vision: a separate call, both photos side by side: the same structure, from a similar camera position?
#     A high-confidence yes to both.
# Registration: when the gate passes, the recent photo is warped onto the old one by that transform and both are
# cropped to the area they share, so the wipe reveals the same structure in the same place.
MATCH_SIDE = 1000           # px: the long side both photos are matched at
MIN_INLIERS, MIN_INLIER_SHARE = 15, 0.25
MAX_SCALE, MAX_ROTATION = 2.5, 12.0
MIN_SHARED_AREA = 0.45      # the shared crop keeps at least this share of the old photo


class ViewpointCheck(BaseModel):
    same_structure: bool                  # the same building / room / landscape feature in both
    similar_camera_position: bool         # taken from about the same place, looking the same way
    confidence: Literal["high", "medium", "low"]
    note: str


def _gray(path: Path) -> tuple[Any, float]:
    """`path` as an equalised grey array at MATCH_SIDE on its long side, and the scale it was read at."""
    import cv2
    import numpy as np
    with Image.open(path) as im:
        im = im.convert("L")
        s = MATCH_SIDE / max(im.size)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        arr = np.asarray(im)
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(arr), s


def geometric_match(old: Path, new: Path) -> dict[str, Any] | None:
    """{"inliers", "share", "scale", "rotation", "matrix"} when the two photos share a viewpoint by feature
    geometry, else None. `matrix` (2x3) maps the recent photo's full-resolution pixels onto the old one's."""
    try:
        import cv2
        import numpy as np
        a, sa = _gray(old)
        b, sb = _gray(new)
        orb = cv2.ORB_create(nfeatures=4000)
        ka, da = orb.detectAndCompute(a, None)
        kb, db = orb.detectAndCompute(b, None)
        if da is None or db is None or len(ka) < MIN_INLIERS or len(kb) < MIN_INLIERS:
            return None
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(db, da, k=2)
        good = [m for m, *rest in (p for p in pairs if p) if rest and m.distance < 0.8 * rest[0].distance]
        if len(good) < MIN_INLIERS:
            return None
        src = np.float32([kb[m.queryIdx].pt for m in good])
        dst = np.float32([ka[m.trainIdx].pt for m in good])
        m, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=8.0,
                                              maxIters=4000, confidence=0.995)
    except Exception as e:  # noqa: BLE001 - fail closed: no geometry, no pair
        log.info("Then-and-now geometry check unavailable: %s", e)
        return None
    if m is None or mask is None:
        return None
    inliers = int(mask.sum())
    scale = float((m[0, 0] ** 2 + m[1, 0] ** 2) ** 0.5)
    rotation = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
    share = inliers / len(good)
    log.info("Then-and-now geometry: %d inliers of %d matches, scale %.2f, rotation %.1f deg", inliers, len(good),
             scale, rotation)
    if not (inliers >= MIN_INLIERS and share >= MIN_INLIER_SHARE and 1 / MAX_SCALE <= scale <= MAX_SCALE
            and abs(rotation) <= MAX_ROTATION):
        return None
    # Full resolution: old_full = (1/sa) * M * (sb * new_full)
    full = np.vstack([m, [0, 0, 1]]) @ np.diag([sb, sb, 1.0])
    full = (np.diag([1 / sa, 1 / sa, 1.0]) @ full)[:2]
    return {"inliers": inliers, "share": round(share, 3), "scale": scale, "rotation": rotation, "matrix": full}


@llm_task("then_now_viewpoint")
def _viewpoint(old: Path, new: Path, place: str) -> ViewpointCheck:
    content = [{"type": "text", "text": "OLD photo:"}, image_block(old, 768),
               {"type": "text", "text": "RECENT photo:"}, image_block(new, 768),
               {"type": "text", "text": (
                   f"Named place: {place}. Do both photos show the same structure (the same building, room or "
                   "feature, not just the same site)? Were they taken from a similar camera position, looking the "
                   "same way, so a wipe from one to the other shows the same thing in the same place? Be strict.")}]
    from . import free_llm
    return free_llm.call("You verify 'then and now' photo pairs for a history documentary.", content, ViewpointCheck,
                         role="checker", task_class="light", effort="medium", max_tokens=800)


def shares_viewpoint(old: Path, new: Path, place: str) -> dict[str, Any] | None:
    """The geometric match when the pair passes both checks (fail closed), else None."""
    geo = geometric_match(old, new)
    if geo is None:
        log.info("Then-and-now: %s and %s do not share a viewpoint by feature geometry.", Path(old).name, Path(new).name)
        return None
    try:
        v = _viewpoint(old, new, place)
    except Exception as e:  # noqa: BLE001 - fail closed: an unchecked pair is not shown
        log.info("Then-and-now viewpoint check failed: %s", e)
        return None
    if not (v.same_structure and v.similar_camera_position and v.confidence == "high"):
        log.info("Then-and-now: viewpoint check said no (%s): %s", v.confidence, v.note[:160])
        return None
    return geo


def register(old: Path, new: Path, matrix: Any, work_dir: Path) -> tuple[Path, Path] | None:
    """The recent photo warped onto the old one by `matrix`, both cropped to the area they share: (old, new)
    paths of equal size, or None when they share too little to fill a frame."""
    import cv2
    import numpy as np
    with Image.open(old) as im:
        a = np.asarray(im.convert("RGB"))
    with Image.open(new) as im:
        b = np.asarray(im.convert("RGB"))
    h, w = a.shape[:2]
    warped = cv2.warpAffine(b, np.asarray(matrix, dtype=np.float64), (w, h), flags=cv2.INTER_LANCZOS4,
                            borderMode=cv2.BORDER_CONSTANT)
    bh, bw = b.shape[:2]
    c = np.asarray(matrix) @ np.array([[0, bw, bw, 0], [0, 0, bh, bh], [1, 1, 1, 1]], dtype=np.float64)
    (tlx, trx, brx, blx), (tly, try_, bry, bly) = c
    x0, y0 = int(np.ceil(max(tlx, blx, 0))) + 2, int(np.ceil(max(tly, try_, 0))) + 2
    x1, y1 = int(np.floor(min(trx, brx, w))) - 2, int(np.floor(min(bly, bry, h))) - 2
    if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) < MIN_SHARED_AREA * w * h:
        log.info("Then-and-now: the registered photos share too little of the frame.")
        return None
    work_dir.mkdir(parents=True, exist_ok=True)
    op, np_ = work_dir / f"{Path(old).stem}_reg.jpg", work_dir / f"{Path(new).stem}_reg.jpg"
    Image.fromarray(a[y0:y1, x0:x1]).save(op, quality=94)
    Image.fromarray(warped[y0:y1, x0:x1]).save(np_, quality=94)
    return op, np_


def _candidates(query: str, keywords: list[str], old: bool) -> list[dict[str, Any]]:
    pages = commons.search(query, limit=40, max_wait=60)
    found = commons.candidates(pages, keywords, config.BEAT_IMAGES_ALLOW_SHARE_ALIKE,
                               **({"min_pixels": 400_000, "min_height": 600} if old else {}))
    return [c for c in found if c.get("year") and ((c["year"] <= OLD_BEFORE) if old else (c["year"] >= NEW_FROM))]


def source(topic: dict[str, Any], work_dir: Path) -> tuple[Path, dict[str, Any], Path, dict[str, Any]] | None:
    """(old photo, its credit, new photo, its credit) for a verified pair, or None."""
    loc = topic.get("location", {})
    name = (loc.get("name") or "").strip()
    from . import free_llm
    if not (name and free_llm.ready()):
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
    seen_recent: dict[str, Path] = {}

    def recent_for(old: dict[str, Any], k: int) -> list[tuple[Path, dict[str, Any]]]:
        """Recent photos of the same subject: searched with the old photo's own title (its street,
        building or chamber), then the site name; downloaded once each."""
        subject = re.sub(r"\.(jpe?g|png|tiff?)$", "", old["title"].replace("File:", ""), flags=re.IGNORECASE)
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
            old_hr = ensure_resolution(op)
            geo = shares_viewpoint(old_hr, np_, place)
            pair = register(old_hr, np_, geo["matrix"], work_dir / "registered") if geo else None
            if pair:
                return pair[0], {**old, "file": str(op)}, pair[1], {**new, "file": str(np_)}
            log.info("Then-and-now: old photo %d and its pick do not share a viewpoint; not shown.", k + 1)
            continue
        log.info("Then-and-now: no honest match for old photo %d: %s", k + 1, pick.note[:160])
    return None


# The comparison ends on both photographs (panel round 2, 2026-11-02: the last frame showed only the recent one,
# the old photo's year faded out). The old photo holds full frame, the recent one wipes across it, then the
# divider comes back to the middle and the two hold side by side for the last WIPE["hold"] seconds or more:
# 9:16 one above the other (split at the middle of the Reel box, so each half shows the same height clear of
# the platforms' interface), 16:9 left and right. Each panel shows the centre of its photo at full-frame scale (a
# window, never a squeeze). The divider is BONE. Each year is a mono THEN / NOW kicker in tracked gold over the
# year in Anton bone, no box, at a measured contrast on every background it sits on (recorded beside the clip).
# The years are the ones passed in, from each photo's own record; nothing is added.
KICKER_PX = {"portrait": 28, "landscape": 28}
YEAR_PX = {"portrait": 128, "landscape": 112}
KICKER_TRACK = 0.22                 # the kicker's letter spacing, as a share of its size
DIVIDER_W = 5
WIPE = {"then": 1.6, "to_now": 1.1, "now": 0.6, "to_split": 1.0}   # seconds; the split holds for the rest


def _split_at(width: int, height: int) -> int:
    """The divider's resting place: x 960 on 16:9; on 9:16 the middle of the Reel box (y 768 at 1920)."""
    if width > height:
        return width // 2
    _, y0, _, y1 = safe_zones.safe_box("reel", width, height)
    return (y0 + y1) // 2


def _window(full: Image.Image, lo: int, hi: int, axis: int) -> Image.Image:
    """The centre `hi - lo` px of `full` along `axis` (0: x, 1: y): a panel of the photo at full-frame scale."""
    size = full.size[axis]
    span = max(1, hi - lo)
    a = (size - span) // 2
    return full.crop((a, 0, a + span, full.size[1])) if axis == 0 else full.crop((0, a, full.size[0], a + span))


def divider_at(t: float, width: int, height: int) -> float:
    """Where the divider is at `t`: the far edge (the old photo whole), the near edge (the recent one whole),
    then the resting split."""
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    split = _split_at(width, height)
    t1 = WIPE["then"]
    t2 = t1 + WIPE["to_now"]
    t3 = t2 + WIPE["now"]
    if t < t2:
        return edge * (1 - _ease_io((t - t1) / WIPE["to_now"]))
    return split * _ease_io((t - t3) / WIPE["to_split"])


def compose(a: Image.Image, b: Image.Image, pos: float) -> Image.Image:
    """The old photo `a` before the divider at `pos` (left on 16:9, above on 9:16), the recent `b` after it."""
    width, height = a.size
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    p = round(max(0, min(edge, pos)))
    im = Image.new("RGB", (width, height))
    if p > 0:
        im.paste(_window(a, 0, p, axis), (0, 0))
    if p < edge:
        im.paste(_window(b, p, edge, axis), (p, 0) if axis == 0 else (0, p))
    if 0 < p < edge:
        d = ImageDraw.Draw(im, "RGBA")
        half = DIVIDER_W / 2
        box = [p - half - 1, 0, p + half + 1, height] if axis == 0 else [0, p - half - 1, width, p + half + 1]
        d.rectangle(box, fill=rgba(INK, 140))
        box = [p - half, 0, p + half - 1, height] if axis == 0 else [0, p - half, width, p + half - 1]
        d.rectangle(box, fill=rgba(BONE))
    return im


def marker_xy(which: str, width: int, height: int) -> tuple[int, int, str]:
    """(x, y, anchor) of a year marker. 9:16: top-left of each panel's part of the Reel box. 16:9: either side
    of the divider at the top of title-safe, THEN set right to it and NOW left (the master's vignette takes the
    corners of a 16:9 frame below 4.5:1, measured on the graded frame)."""
    split = _split_at(width, height)
    if width > height:
        _, y0, _, _ = get_text_box(width, height)
        return (split - 64, y0 + 40, "rt") if which == "then" else (split + 64, y0 + 40, "lt")
    x0, y0, _, _ = safe_zones.safe_box("reel", width, height)
    return (x0 + 18, max(y0 + 42, TEXT_TOP), "lt") if which == "then" else (x0 + 18, split + 48, "lt")


def _marker_plate(split_frame: Image.Image, alone: Image.Image, xy: tuple[int, int], text: str, font: Any,
                  colour: tuple[int, int, int], anchor: str, track: float, halo_px: int | None, min_halo: float,
                  target: float = PLACE_TARGET) -> Any:
    """A marker line planned on the split and solved on the whole photo it also sits on, then on the split again
    from that halo, so it holds `target` on both (the thin mono kicker loses about 0.6 to the grade)."""
    plate = plan_text(split_frame, xy, text, font, colour, anchor=anchor, track=track, halo_px=halo_px,
                      target=target, min_halo=min_halo, max_halo=1.0)
    plate.solve(alone, target, min_halo=plate.halo, max_halo=1.0)
    return plate.solve(split_frame, target, min_halo=plate.halo, max_halo=1.0)


SHADE_ALPHA, SHADE_PAD = 0.62, 56


def _shade_mask(size: tuple[int, int], boxes: list) -> Image.Image:
    """A feathered ink shade over the union of `boxes` (L mode, 0..255 at SHADE_ALPHA)."""
    from PIL import ImageFilter
    m = Image.new("L", size, 0)
    x0, y0 = min(bx[0] for bx in boxes) - SHADE_PAD, min(bx[1] for bx in boxes) - SHADE_PAD
    x1, y1 = max(bx[2] for bx in boxes) + SHADE_PAD, max(bx[3] for bx in boxes) + SHADE_PAD
    ImageDraw.Draw(m).rectangle([x0, y0, x1, y1], fill=round(255 * SHADE_ALPHA))
    return m.filter(ImageFilter.GaussianBlur(SHADE_PAD * 0.7))


def _shade(im: Image.Image, mask: Image.Image, alpha: float = 1.0) -> Image.Image:
    if alpha <= 0:
        return im
    m = mask if alpha >= 1 else mask.point(lambda v: round(v * alpha))
    return Image.composite(Image.new("RGB", im.size, INK), im.convert("RGB"), m)


def render(old: Path, old_year: int, new: Path, new_year: int, out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """The old photo, the recent one wiped across it, then both side by side to the end, each with its year."""
    a = cover_crop(old, (width, height)).convert("RGB")
    b = cover_crop(new, (width, height)).convert("RGB")
    n = int(CLIP_SECONDS * VIDEO_FPS)
    key = "landscape" if width > height else "portrait"
    kicker_font = get_font(DATA_FONT, KICKER_PX[key], weight=600)
    year_font = get_font("Anton", YEAR_PX[key])
    track = KICKER_PX[key] * KICKER_TRACK
    split = _split_at(width, height)
    edge = max(width, height)
    # Each marker is planned where it rests (the split) and solved again on the whole photo it also sits on.
    split_frame, then_frame, now_frame = compose(a, b, split), compose(a, b, edge), compose(a, b, 0)

    def plan_marker(which: str, year: int, alone: Image.Image) -> list:
        x, y, anchor = marker_xy(which, width, height)
        nudge = 0 if anchor[0] == "r" else -4   # Anton's side bearing, so the year's stem lines up with the kicker
        # A floor under each halo: the thin mono kicker lost its edge to the grade's chroma smear on a bare photo.
        # Gold, unless a bright sky keeps it under 4.5:1 at a full halo: then bone, which clears it there.
        for colour in (GOLD, WHITE):
            kicker = _marker_plate(split_frame, alone, (x, y), which.upper(), kicker_font, colour, anchor, track,
                                   halo_px=36, min_halo=0.5, target=PLACE_TARGET + 0.6)
            if (kicker.contrast or 0) >= PLACE_TARGET and (kicker.measure(alone) or 0) >= PLACE_TARGET:
                break
        figure = _marker_plate(split_frame, alone, (x + nudge, y + round(KICKER_PX[key] * 1.5)), str(year),
                               year_font, WHITE, anchor, 0, halo_px=None, min_halo=0.35)
        return [kicker, figure]

    def short(ps: list, alone: Image.Image) -> bool:
        return any(min(p.measure(split_frame) or 0, p.measure(alone) or 0) < PLACE_TARGET for p in ps)

    plates: dict[str, list] = {}
    shades: dict[str, Image.Image] = {}
    for which, year, alone in (("then", old_year, then_frame), ("now", new_year, now_frame)):
        ps = plan_marker(which, year, alone)
        if short(ps, alone):
            # A bright, busy photo the halo alone cannot quiet: a feathered ink shade under the marker (no edge,
            # no box), then the marker is planned again on the shaded pictures.
            shades[which] = _shade_mask((width, height), [p.box for p in ps])
            split_frame = _shade(split_frame, shades[which])
            alone = _shade(alone, shades[which])
            ps = plan_marker(which, year, alone)
        plates[which] = ps
    final: dict[str, Image.Image] = {}

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            pos = divider_at(t, width, height)
            im = compose(a, b, pos)
            # THEN shows while its panel covers it; NOW from when the recent photo has crossed it.
            then_clear = pos - max(p.box[2] if width > height else p.box[3] for p in plates["then"])
            now_clear = min(p.box[0] if width > height else p.box[1] for p in plates["now"]) - pos
            alphas = {"then": _ease((t - 0.2) / 0.4) * max(0.0, min(1.0, then_clear / 40)),
                      "now": max(0.0, min(1.0, now_clear / 40)) if t > WIPE["then"] else 0.0}
            for which in ("then", "now"):
                if which in shades:
                    im = _shade(im, shades[which], alphas[which])
                for p in plates[which]:
                    im = p.draw(im, alphas[which])
            final["frame"] = im
            yield im

    clip = _encode(frames(), out, n, width=width, height=height)
    if "frame" in final:
        record_contrast(clip, final["frame"], [(f"{w}_{part}", p) for w in ("then", "now")
                                               for part, p in zip(("kicker", "year"), plates[w])])
    return clip


def make(topic: dict[str, Any], work_dir: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> tuple[Path | None, list[dict[str, Any]]]:
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
