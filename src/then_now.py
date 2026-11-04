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
from .typography import TextPlate, cover_crop, get_font, plan_text
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
# Panel round 3: registration is measured, not assumed. The inliers' RMS reprojection error under the fitted
# transform must stay within MAX_REPROJ of the old photo's width, and the numbers are written beside the clip
# (<clip>.registration.json). After the warp the recent photo's exposure is matched (a global gamma) to within
# MAX_LSTAR_GAP of the old photo's mean L*, so a brightness jump does not read as change; the old photo is untouched.
MAX_REPROJ = 0.015
MAX_LSTAR_GAP = 10.0


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
    keep = mask.ravel() > 0
    fitted = src[keep] @ m[:, :2].T + m[:, 2]
    reproj = float(np.sqrt(np.mean(np.sum((fitted - dst[keep]) ** 2, axis=1)))) if keep.any() else float("inf")
    reproj_share = reproj / a.shape[1]
    scale = float((m[0, 0] ** 2 + m[1, 0] ** 2) ** 0.5)
    rotation = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
    share = inliers / len(good)
    log.info("Then-and-now geometry: %d inliers of %d matches, scale %.2f, rotation %.1f deg, reprojection %.2f%%",
             inliers, len(good), scale, rotation, 100 * reproj_share)
    if not (inliers >= MIN_INLIERS and share >= MIN_INLIER_SHARE and 1 / MAX_SCALE <= scale <= MAX_SCALE
            and abs(rotation) <= MAX_ROTATION and reproj_share <= MAX_REPROJ):
        return None
    # Full resolution: old_full = (1/sa) * M * (sb * new_full)
    full = np.vstack([m, [0, 0, 1]]) @ np.diag([sb, sb, 1.0])
    full = (np.diag([1 / sa, 1 / sa, 1.0]) @ full)[:2]
    return {"inliers": inliers, "share": round(share, 3), "scale": scale, "rotation": rotation,
            "reproj_px": round(reproj / sa, 2), "reproj_share": round(reproj_share, 5), "matrix": full}


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


def _mean_lstar(rgb: Any) -> float:
    import cv2
    import numpy as np
    return float(np.mean(cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2LAB)[..., 0])) * 100 / 255


def match_exposure(old_rgb: Any, new_rgb: Any, gap: float = MAX_LSTAR_GAP) -> tuple[Any, float]:
    """`new_rgb` with a global gamma so its mean L* is within `gap` of `old_rgb`'s (the least change that does it),
    and the gamma used (1.0: already within)."""
    import numpy as np
    lo_, ln = _mean_lstar(old_rgb), _mean_lstar(new_rgb)
    if abs(ln - lo_) <= gap:
        return new_rgb, 1.0
    goal = lo_ + (gap - 0.5 if ln > lo_ else -(gap - 0.5))
    norm = new_rgb.astype(np.float32) / 255.0
    lo, hi = 0.2, 5.0                       # gamma > 1 darkens, < 1 brightens
    for _ in range(30):
        g = (lo * hi) ** 0.5
        if _mean_lstar((np.power(norm, g) * 255).round().astype(np.uint8)) > goal:
            lo = g
        else:
            hi = g
    g = (lo * hi) ** 0.5
    return (np.power(norm, g) * 255).round().astype(np.uint8), round(g, 3)


REGISTRATION_SIDECAR = "registration.json"


def register(old: Path, new: Path, matrix: Any, work_dir: Path, stats: dict[str, Any] | None = None) -> tuple[Path, Path] | None:
    """The recent photo warped onto the old one by `matrix`, both cropped to the area they share, the recent one's
    exposure matched: (old, new) paths of equal size, or None when they share too little to fill a frame. `stats`
    (the geometric match) is written with the exposure gamma to `work_dir`/REGISTRATION_SIDECAR."""
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
    old_crop = a[y0:y1, x0:x1]
    new_crop, gamma = match_exposure(old_crop, warped[y0:y1, x0:x1])
    Image.fromarray(old_crop).save(op, quality=94)
    Image.fromarray(new_crop).save(np_, quality=94)
    import json
    rec = {k: v for k, v in (stats or {}).items() if k != "matrix"}
    rec.update({"shared": [x0, y0, x1, y1], "shared_share": round((x1 - x0) * (y1 - y0) / (w * h), 3),
                "exposure_gamma": gamma, "old_lstar": round(_mean_lstar(old_crop), 1),
                "new_lstar": round(_mean_lstar(new_crop), 1)})
    (work_dir / REGISTRATION_SIDECAR).write_text(json.dumps(rec, indent=1), encoding="utf-8", newline="\n")
    return op, np_


# Capture year at source (panel round 4). Commons' DateTimeOriginal often comes from the Information template's date,
# which for postcards, photochroms, lantern slides, stereograph cards and printmaking (engravings, lithographs) is
# the date the object was published, not when the photo was taken, so "THEN 1900" would misstate it. An old
# candidate is refused (fail closed) when its title or categories name such a publication object, or its date text
# (when the record carries it as "date_text"), title or description says the date is a publication date.
# Only those objects are named: a bare "print" is not one. "Albumen prints", "Gelatin silver prints" and
# "Photographic prints" are how Commons files real 19th-century photographs, whose dates are capture dates
# (round-4 review: matching every print refused most of the genuine old-photo pool).
PUBLISHED_OBJECT = re.compile(r"\b(post ?cards?|photochroms?|lantern slides?|magic lantern|stereographs?|stereo ?cards?|"
                              r"stereoviews?|stereoscopic cards?|engravings?|(?:chromo)?lithographs?|woodcuts?|"
                              r"etchings?)\b", re.IGNORECASE)
PUBLISHED_DATE = re.compile(r"\b(published|publication|printed|issued)\b", re.IGNORECASE)


def capture_dated(cand: dict[str, Any]) -> bool:
    """Does the candidate's year read as when the photo was taken (not when a postcard or print was issued)?"""
    title = re.sub(r"[_]+", " ", str(cand.get("title") or ""))
    cats = " | ".join(str(c) for c in cand.get("categories") or [])
    if PUBLISHED_OBJECT.search(title) or PUBLISHED_OBJECT.search(cats):
        return False
    said = " ".join(str(cand.get(k) or "") for k in ("date_text", "title", "description"))
    return not PUBLISHED_DATE.search(said)


def _candidates(query: str, keywords: list[str], old: bool) -> list[dict[str, Any]]:
    pages = commons.search(query, limit=40, max_wait=60)
    found = commons.candidates(pages, keywords, config.BEAT_IMAGES_ALLOW_SHARE_ALIKE,
                               **({"min_pixels": 400_000, "min_height": 600} if old else {}))
    out = [c for c in found if c.get("year") and ((c["year"] <= OLD_BEFORE) if old else (c["year"] >= NEW_FROM))]
    if not old:
        return out
    kept = [c for c in out if capture_dated(c)]
    for c in out:
        if c not in kept:
            log.info("Then-and-now: %s skipped: its date reads as a publication date, not a capture year.", c.get("title"))
    return kept


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
            pair = register(old_hr, np_, geo["matrix"], work_dir / "registered", geo) if geo else None
            if pair:
                return pair[0], {**old, "file": str(op)}, pair[1], {**new, "file": str(np_)}
            log.info("Then-and-now: old photo %d and its pick do not share a viewpoint; not shown.", k + 1)
            continue
        log.info("Then-and-now: no honest match for old photo %d: %s", k + 1, pick.note[:160])
    return None


# The comparison ends on both photographs (panel round 2, 2026-11-02: the last frame showed only the recent one,
# the old photo's year faded out). Panel round 3: a true registered wipe, the photos still and only the divider
# moving (each panel shows its photo's own pixels at the same coordinates, so registered structure stays put). The
# old photo holds alone for WIPE["then"], the divider sweeps in to the split, dwells there, carries on until the
# recent photo fills SWEEP_TO of the frame, settles back to the split, and the two hold for the rest
# (CLIP_SECONDS 6.0 - the 3.4 s of WIPE = 2.6 s):
# 9:16 one above the other (split at the middle of the Reel box, so each half shows the same height clear of
# the platforms' interface), 16:9 left and right. The divider is BONE. Each year is a mono THEN / NOW kicker in
# tracked gold over the year in Anton bone, no box, at a measured contrast on every background it sits on (recorded beside the clip).
# The years are the ones passed in, from each photo's own record; nothing is added.
# Panel round 4: the kickers read at about 7 pt on a phone (a 20 px cap). Now a 34 px cap on 9:16 (IBM Plex Mono
# 600 at 48 px, about 0.3 of the year's cap), 40 px on 16:9, tracked 0.15 of size; and one colour for the pair (both
# gold, or both bone when either fails 4.5:1 at a full halo), so the two labels read in one colour role.
KICKER_PX = {"portrait": 48, "landscape": 40}
YEAR_PX = {"portrait": 128, "landscape": 112}
KICKER_TRACK = 0.15                 # the kicker's letter spacing, as a share of its size
# Panel round 4: one slow push, identical on both photos (the same scale and centre, eased, 1.00 -> 1 + PUSH over
# the clip), so registration holds and the split hold is not a still frame. The divider, shades and markers stay
# fixed on screen.
PUSH = 0.04
DIVIDER_W = 5
WIPE = {"then": 1.2, "to_split": 0.5, "dwell": 0.4, "to_far": 0.5, "settle": 0.8}   # seconds; then the split holds
SWEEP_TO = 0.8                      # the share of the frame the recent photo reaches before settling back


def _split_at(width: int, height: int) -> int:
    """The divider's resting place: x 960 on 16:9; on 9:16 the middle of the Reel box (y 768 at 1920)."""
    if width > height:
        return width // 2
    _, y0, _, y1 = safe_zones.safe_box("reel", width, height)
    return (y0 + y1) // 2


def divider_at(t: float, width: int, height: int) -> float:
    """Where the divider is at `t`: the far edge (the old photo whole) for WIPE["then"], in to the split, a dwell,
    on to (1 - SWEEP_TO) of the edge, then back to the resting split. One ease-in-out per move."""
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    split = _split_at(width, height)
    far = edge * (1 - SWEEP_TO)
    t1 = WIPE["then"]
    t2 = t1 + WIPE["to_split"]
    t3 = t2 + WIPE["dwell"]
    t4 = t3 + WIPE["to_far"]
    if t < t1:
        return float(edge)
    if t < t2:
        return edge + (split - edge) * _ease_io((t - t1) / WIPE["to_split"])
    if t < t3:
        return float(split)
    if t < t4:
        return split + (far - split) * _ease_io((t - t3) / WIPE["to_far"])
    return far + (split - far) * _ease_io((t - t4) / WIPE["settle"])


def push_at(t: float) -> float:
    """The photos' shared zoom at `t`: 1 at the start, 1 + PUSH at the end, eased."""
    return 1.0 + PUSH * _ease_io(t / CLIP_SECONDS)


def pushed(src: Image.Image, t: float, size: tuple[int, int]) -> Image.Image:
    """`src` (cover-cropped at (1 + PUSH) x the frame, so the push shows the photo's own detail) at the push's zoom
    for `t`, centred, at frame `size`: at zoom 1 the whole of `src`, at 1 + PUSH its centre 1 / (1 + PUSH)."""
    z = push_at(t)
    cw, ch = src.width / z, src.height / z
    x0, y0 = (src.width - cw) / 2, (src.height - ch) / 2
    return src.resize(size, Image.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch))


def compose(a: Image.Image, b: Image.Image, pos: float) -> Image.Image:
    """The old photo `a` before the divider at `pos` (left on 16:9, above on 9:16), the recent `b` after it, each
    at its own pixels (a fixed crop, never a sliding window), so a registered pair lines up across the divider."""
    width, height = a.size
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    p = round(max(0, min(edge, pos)))
    im = b.copy() if b.size == a.size else b.resize(a.size)
    if p > 0:
        box = (0, 0, p, height) if axis == 0 else (0, 0, width, p)
        im.paste(a.crop(box), (0, 0))
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


# The marker shade (panel round 3: the feathered rectangle read as a smudge on a flat wall) is an INK gradient anchored
# to the edge of the marker's panel and bleeding off it, like a lens's corner fall-off rather than a patch: on 9:16
# from the panel's left edge level with the marker, on 16:9 from the top edge at the divider the two markers flank.
# Its depth holds over the marker (the hull of the anchor and the marker's box) and falls smoothly to nothing
# SHADE_FALL screen pixels beyond it. Used only when the halo alone falls short; deepened in SHADE_STEPS until
# the marker holds its floor.
# Panel round 4: the corner gradient still read as a dark smudge on a dome or a facade, so render() now draws the
# shade as a full-width edge band: full depth from the frame's top across its width down through the
# marker, then the same SHADE_FALL smoothstep below, and on every frame kept to the part of the screen its own
# photo shows at that frame's divider (photo_extent). So THEN's band covers the whole old photo while it is alone
# and only the THEN panel at the split; it never darkens the recent photo (round-4 review: on 16:9 THEN's band
# darkened the top of the NOW photo too). The NOW band on 9:16 hangs off the divider wherever it is (full depth
# from the divider down through the marker), so its top is the divider's own edge, never a cut across the photo.
# shade_corner stays for the corner form.
# Round-4 review of the band as rendered (twice): a full-width band, even graduated from the edge, read as a flat grey
# header bar over the top ~35% of a bright 16:9 frame, with a visible lower edge, and it was the first thing seen
# (THEN alone). The shade is now a local glow behind the marker only (band_mask): ink at full depth over the
# marker's lines and BAND_PAD px round them, falling on a smoothstep (no edge) to nothing BAND_FALL px beyond, in
# every direction, so it starts fading at the marker's top and is gone a short way below its last line. It is
# deepened in BAND_STEPS from a light start and stops at the first depth at which the marker holds its floor, and it
# is kept to its own photo at each frame's divider (photo_extent), so on 9:16 NOW's glow hangs from the divider at
# rest (its upper fall is cut by the divider's own edge) and never darkens the old photo.
SHADE_ALPHA, SHADE_FALL = 0.5, 180
SHADE_STEPS = (0.5, 0.62, 0.75, 0.88)
BAND_PAD, BAND_FALL = 12, 84        # screen px: full depth this far round the marker's lines, gone this far beyond
BAND_STEPS = (0.2, 0.28, 0.36, 0.45, 0.55, 0.65, 0.75, 0.88)
# Freeze panel 2026-11-04 (step 1, ink on paper): on a near-white paper ground (an old map, a document) bone type in
# a heavy dark halo read as a stepped dark outline where the short kicker sits over the wide year. The box round a
# marker's kicker and year (their ink boxes' union grown by PAPER_PAD px, kept to its photo) is paper when, on its
# split and on its alone frame (unshaded), its mean luma is at least PAPER_MEAN, its 10th percentile at least
# PAPER_P10, its Canny edge density at most PAPER_EDGE and at most PAPER_DARK of its pixels are darker than
# PAPER_DARK_FRAC x its mean (plain paper only: a diagram's own black rules and labels collide with ink type, which
# the contrast ratio cannot see). Such a marker is set in brand INK, no halo and no glow; if an ink line then measures
# under PLACE_TARGET (planned or drawn, split or alone) the marker takes the bone/gold path. With one ink marker the
# other's kicker is bone (two neutrals). Off paper nothing changes.
INK_ON_PAPER = True
PAPER_MEAN, PAPER_P10, PAPER_EDGE, PAPER_PAD = 195.0, 140.0, 0.20, 24
PAPER_DARK_FRAC, PAPER_DARK = 0.6, 0.02
LAST_PAPER: dict[str, dict[str, Any]] = {}   # the last render's gate stats and type mode per marker (a record only)


def _luma(arr: Any) -> Any:
    import numpy as np
    a = np.asarray(arr, np.float32)
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _edge_density(rgb: Any) -> float:
    """Canny(50, 150) edge pixels per pixel on the luma of `rgb` (an RGB array or image)."""
    import cv2
    import numpy as np
    y = np.clip(np.round(_luma(np.asarray(rgb))), 0, 255).astype(np.uint8)
    if y.size == 0:
        return 0.0
    return float((cv2.Canny(y, 50, 150) > 0).mean())


def paper_stats(im: Image.Image, box: tuple[int, int, int, int]) -> dict[str, float] | None:
    """{"mean", "p10", "edge", "dark"} of the luma of `im` inside `box` (Canny(50, 150) density; the share of pixels
    darker than PAPER_DARK_FRAC x the mean), or None when empty."""
    import numpy as np
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    if x1 <= x0 or y1 <= y0:
        return None
    rgb = np.asarray(im.convert("RGB").crop((x0, y0, x1, y1)))
    y = _luma(rgb)
    mean = float(y.mean())
    return {"mean": round(mean, 2), "p10": round(float(np.percentile(y, 10)), 2),
            "edge": round(_edge_density(rgb), 4),
            "dark": round(float((y < PAPER_DARK_FRAC * mean).mean()), 4)}


def paper_gate(boxes: list, frames: list) -> tuple[bool, list]:
    """Is the ground round a marker's lines `boxes` paper on every (frame, clip box) of `frames`? The lines' union
    grown by PAPER_PAD px, kept to each clip box. (ok, the measured stats per frame)."""
    x0, y0 = min(b[0] for b in boxes) - PAPER_PAD, min(b[1] for b in boxes) - PAPER_PAD
    x1, y1 = max(b[2] for b in boxes) + PAPER_PAD, max(b[3] for b in boxes) + PAPER_PAD
    ok, out = True, []
    for im, (cx0, cy0, cx1, cy1) in frames:
        st = paper_stats(im, (max(x0, cx0), max(y0, cy0), min(x1, cx1), min(y1, cy1)))
        out.append(st)
        if (st is None or st["mean"] < PAPER_MEAN or st["p10"] < PAPER_P10 or st["edge"] > PAPER_EDGE
                or st["dark"] > PAPER_DARK):
            ok = False
    return ok, out


def on_paper(boxes: list, frames: list) -> bool:
    return paper_gate(boxes, frames)[0]


def panel_box(which: str, width: int, height: int) -> tuple[int, int, int, int]:
    """The THEN (old) or NOW (recent) panel at rest on the split."""
    split = _split_at(width, height)
    if width > height:
        return (0, 0, split, height) if which == "then" else (split, 0, width, height)
    return (0, 0, width, split) if which == "then" else (0, split, width, height)


def shade_corner(which: str, boxes: list, width: int, height: int) -> tuple[int, int]:
    """Where the marker's shade is anchored: 9:16, the panel's left edge level with the markers; 16:9, the top
    edge at the divider (the panel's top corner on the markers' side)."""
    px0, py0, px1, _ = panel_box(which, width, height)
    cx = (min(bx[0] for bx in boxes) + max(bx[2] for bx in boxes)) / 2
    x = px0 if abs(cx - px0) <= abs(cx - px1) else px1
    if width > height:
        return x, py0
    return x, round((min(bx[1] for bx in boxes) + max(bx[3] for bx in boxes)) / 2)


def _shade_mask(size: tuple[int, int], boxes: list, corner: tuple[int, int] | None = None,
                alpha: float = SHADE_ALPHA, pad: float = 0, fall: float = SHADE_FALL,
                lines: bool = False) -> Image.Image:
    """An ink gradient (L mode, 0..255 at `alpha`) anchored at `corner` (default the top-left) on the frame's edge:
    full over the hull of the anchor and the box round the markers' `boxes`, then a smoothstep to 0 over SHADE_FALL px of screen
    distance past that hull (measured in pixels, so the fall-off is the same width on 9:16 and 16:9; panel round 3
    review: a fade over normalised ellipse radius came to ~30 px on 16:9, a hard-edged half-disc). `pad` holds full
    depth that many px past the hull and `fall` sets the fall-off's width; `lines` measures from each of `boxes`
    itself, not a hull (glow_mask's local glow: a block's empty corner beside a short kicker read as a grey box)."""
    import cv2
    import numpy as np
    w, h = size
    ax, ay = corner if corner is not None else (0, 0)
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)     # the marker's lines as one block
    outside = np.full((h, w), 255, np.uint8)
    if lines:
        for b in boxes:
            cv2.rectangle(outside, (round(b[0]), round(b[1])), (round(b[2]) - 1, round(b[3]) - 1), 0, -1)
    else:
        pts = np.array([(ax, ay), (x0, y0), (x1, y0), (x1, y1), (x0, y1)], np.float32)
        cv2.fillConvexPoly(outside, cv2.convexHull(pts).round().astype(np.int32), 0)
    d = cv2.distanceTransform(outside, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)   # px to the hull; 0 inside it
    f = np.clip((d - pad) / fall, 0.0, 1.0)
    f = 1.0 - f * f * (3 - 2 * f)            # smoothstep fall-off
    return Image.fromarray(np.round(255 * alpha * f).astype(np.uint8), "L")


def glow_mask(boxes: list, width: int, height: int, alpha: float = SHADE_ALPHA) -> Image.Image:
    """Ink (L mode, 0..255 at `alpha`) at full depth over each of the marker's lines `boxes` grown by BAND_PAD,
    then a smoothstep to 0 over BAND_FALL px of screen distance past the nearest line: a soft glow behind the
    marker that follows its lines' shape, no edge and no box."""
    return _shade_mask((width, height), boxes, None, alpha, pad=BAND_PAD, fall=BAND_FALL, lines=True)


def photo_extent(which: str, pos: float, width: int, height: int) -> tuple[int, int, int, int]:
    """Where the old (THEN) or recent (NOW) photo shows with the divider at `pos` (compose): THEN before it, NOW
    after it."""
    p = round(max(0, min(max(width, height), pos)))
    if width > height:
        return (0, 0, p, height) if which == "then" else (p, 0, width, height)
    return (0, 0, width, p) if which == "then" else (0, p, width, height)


def clip_to_photo(mask: Image.Image, which: str, pos: float) -> Image.Image:
    """`mask` kept to the part of the screen `which`'s photo shows at divider `pos` (nothing elsewhere)."""
    x0, y0, x1, y1 = photo_extent(which, pos, mask.width, mask.height)
    out = Image.new("L", mask.size, 0)
    if x1 > x0 and y1 > y0:
        out.paste(mask.crop((x0, y0, x1, y1)), (x0, y0))
    return out


def band_mask(which: str, boxes: list, width: int, height: int, alpha: float = SHADE_ALPHA,
              pos: float | None = None) -> Image.Image:
    """The marker's shade: its glow (glow_mask), kept to the part of the screen its own photo shows with the
    divider at `pos` (default the split, where the clip rests), so neither shade darkens the other photo."""
    return clip_to_photo(glow_mask(boxes, width, height, alpha), which, _split_at(width, height) if pos is None else pos)


def _union(masks: list) -> Image.Image:
    """The deepest of several (mask, alpha) shades at each pixel."""
    from PIL import ImageChops
    out = None
    for m, a in masks:
        m = m if a >= 1 else m.point(lambda v, a=a: round(v * max(0.0, a)))
        out = m if out is None else ImageChops.lighter(out, m)
    return out


def _shade(im: Image.Image, mask: Image.Image, alpha: float = 1.0) -> Image.Image:
    if alpha <= 0:
        return im
    m = mask if alpha >= 1 else mask.point(lambda v: round(v * alpha))
    return Image.composite(Image.new("RGB", im.size, INK), im.convert("RGB"), m)


def render(old: Path, old_year: int, new: Path, new_year: int, out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """The old photo, the recent one wiped across it, then both side by side to the end, each with its year; both
    photos under one slow push (push_at)."""
    size = (width, height)
    big = (round(width * (1 + PUSH)), round(height * (1 + PUSH)))
    a_src = cover_crop(old, big).convert("RGB")
    b_src = cover_crop(new, big).convert("RGB")
    n = int(CLIP_SECONDS * VIDEO_FPS)
    key = "landscape" if width > height else "portrait"
    kicker_font = get_font(DATA_FONT, KICKER_PX[key], weight=600)
    year_font = get_font("Anton", YEAR_PX[key])
    track = KICKER_PX[key] * KICKER_TRACK
    split = _split_at(width, height)
    edge = max(width, height)
    # Each marker is planned where it rests (the split, at the end of the push, where the clip holds) and solved
    # again on the whole photo it also sits on (THEN alone at the start; NOW at its widest, the sweep's far end).
    t_far = sum(v for k, v in WIPE.items() if k != "settle")
    split_frame = compose(pushed(a_src, CLIP_SECONDS, size), pushed(b_src, CLIP_SECONDS, size), split)
    then_frame = compose(pushed(a_src, 0.0, size), pushed(b_src, 0.0, size), edge)
    now_frame = compose(pushed(a_src, t_far, size), pushed(b_src, t_far, size), 0)
    pair_colour: list[Any] = [None]      # one kicker colour for both markers (None: each tries gold first)
    colours: dict[str, Any] = {}         # the kicker colour each marker was planned in

    def plan_marker(which: str, year: int, alone: Image.Image) -> list:
        x, y, anchor = marker_xy(which, width, height)
        nudge = 0 if anchor[0] == "r" else -4   # Anton's side bearing, so the year's stem lines up with the kicker
        # A floor under each halo: the thin mono kicker lost its edge to the grade's chroma smear on a bare photo.
        # Gold, unless a bright sky keeps it under 4.5:1 at a full halo: then bone, which clears it there.
        for colour in ((pair_colour[0],) if pair_colour[0] is not None else (GOLD, WHITE)):
            kicker = _marker_plate(split_frame, alone, (x, y), which.upper(), kicker_font, colour, anchor, track,
                                   halo_px=36, min_halo=0.5, target=PLACE_TARGET + 0.6)
            colours[which] = colour
            if (kicker.contrast or 0) >= PLACE_TARGET and (kicker.measure(alone) or 0) >= PLACE_TARGET:
                break
        figure = _marker_plate(split_frame, alone, (x + nudge, y + round(KICKER_PX[key] * 1.5)), str(year),
                               year_font, WHITE, anchor, 0, halo_px=None, min_halo=0.35)
        return [kicker, figure]

    def drawn(ps: list, im: Image.Image) -> Image.Image:
        for p in ps:
            im = p.draw(im)
        return im

    def short(ps: list, alone: Image.Image) -> bool:
        """Is any marker line under PLACE_TARGET as drawn with its marker's other line (measure() reads delivered
        pixels: on the bare frames every marker read ~1:1, so every pair got the shade; and the year's halo dims
        the kicker above it)?"""
        on_split, on_alone = drawn(ps, split_frame), drawn(ps, alone)
        return any(min(p.measure(on_split) or 0, p.measure(on_alone) or 0) < PLACE_TARGET for p in ps)

    split0 = split_frame
    # Ink on paper: each marker's ground measured once, unshaded, on its split and its alone frame.
    alone_of = {"then": then_frame, "now": now_frame}
    paper: dict[str, dict[str, Any]] = {}
    for which, year in (("then", old_year), ("now", new_year)):
        x, y, anchor = marker_xy(which, width, height)
        nudge = 0 if anchor[0] == "r" else -4
        boxes = [TextPlate(text, font, WHITE, xy, anchor, tr, 0, INK, 0, False, size).box
                 for text, font, xy, tr in ((which.upper(), kicker_font, (x, y), track),
                                            (str(year), year_font, (x + nudge, y + round(KICKER_PX[key] * 1.5)), 0))]
        ok, st = paper_gate(boxes, [(split0, panel_box(which, width, height)), (alone_of[which], (0, 0, width, height))])
        paper[which] = {"ok": bool(INK_ON_PAPER and ok), "split": st[0], "alone": st[1]}
    LAST_PAPER.clear()
    LAST_PAPER.update(paper)

    def ink_marker(which: str, year: int, alone: Image.Image) -> list | None:
        """The marker in brand INK, no halo (and no glow), or None when a line would hold under PLACE_TARGET."""
        x, y, anchor = marker_xy(which, width, height)
        nudge = 0 if anchor[0] == "r" else -4
        ps = [_marker_plate(split_frame, alone, (x, y), which.upper(), kicker_font, INK, anchor, track,
                            halo_px=0, min_halo=0.0),
              _marker_plate(split_frame, alone, (x + nudge, y + round(KICKER_PX[key] * 1.5)), str(year),
                            year_font, INK, anchor, 0, halo_px=0, min_halo=0.0)]
        if short(ps, alone) or min(p.contrast or 0 for p in ps) < PLACE_TARGET:
            log.info("Then-and-now: ink type on paper plans %s for %s; bone/gold type instead.",
                     "/".join(f"{p.contrast or 0:.2f}" for p in ps), which)
            paper[which]["ink_short"] = [round(p.contrast or 0, 2) for p in ps]
            return None
        return ps

    inks: dict[str, list] = {}

    def shaded(im: Image.Image, shades: dict[str, Image.Image], pos: float,
               alphas: dict[str, float] | None = None) -> Image.Image:
        """`im` (composed with the divider at `pos`) under every band, each kept to its own photo's part of the
        screen at `pos`; overlapping ones as one (the deeper wins, never stacked)."""
        if not shades:
            return im
        return _shade(im, _union([(clip_to_photo(m, w, pos), 1.0 if alphas is None else alphas[w])
                                  for w, m in shades.items()]))

    def plan_all() -> tuple[dict[str, list], dict[str, Image.Image]]:
        nonlocal split_frame
        split_frame = split0
        inks.clear()
        plates: dict[str, list] = {}
        shades: dict[str, Image.Image] = {}      # each marker's glow on the whole frame; clipped per frame
        for which, year, alone0, at in (("then", old_year, then_frame, edge), ("now", new_year, now_frame, 0)):
            alone = shaded(alone0, shades, at)
            if paper[which]["ok"]:
                ps = ink_marker(which, year, alone)
                if ps is not None:
                    plates[which] = inks[which] = ps
                    continue
                paper[which]["ok"] = False
            ps = plan_marker(which, year, alone)
            if short(ps, alone):
                # A bright, busy photo the halo alone cannot quiet: a soft ink glow behind the marker only (no
                # edge, no bar), deepened step by step from light; the marker is planned again on the shaded
                # pictures.
                boxes = [p.box for p in ps]
                for depth in BAND_STEPS:
                    shades[which] = band_mask(which, boxes, width, height, depth, pos=edge if which == "then" else 0)
                    split_frame, alone = shaded(split0, shades, split), shaded(alone0, shades, at)
                    ps = plan_marker(which, year, alone)
                    if not short(ps, alone):
                        break
            plates[which] = ps
        return plates, shades

    plates, shades = plan_all()
    if len(inks) == 1 and set(colours.values()) != {WHITE}:
        pair_colour[0] = WHITE              # one ink marker: the other's kicker is bone (two neutrals)
        plates, shades = plan_all()
    elif len(set(colours.values())) > 1:    # one gold, one bone: both bone, so the pair reads in one colour role
        pair_colour[0] = WHITE
        plates, shades = plan_all()
    for w in ("then", "now"):
        LAST_PAPER[w]["mode"] = "ink" if w in inks else "bone/gold"
    final: dict[str, Image.Image] = {}

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            pos = divider_at(t, width, height)
            im = compose(pushed(a_src, t, size), pushed(b_src, t, size), pos)
            # THEN shows while its panel covers it; NOW from when the recent photo has crossed it.
            then_clear = pos - max(p.box[2] if width > height else p.box[3] for p in plates["then"])
            now_clear = min(p.box[0] if width > height else p.box[1] for p in plates["now"]) - pos
            alphas = {"then": _ease((t - 0.2) / 0.4) * max(0.0, min(1.0, then_clear / 40)),
                      "now": max(0.0, min(1.0, now_clear / 40)) if t > WIPE["then"] else 0.0}
            im = shaded(im, shades, pos, alphas)   # each band on its own photo only, at this frame's divider
            for which in ("then", "now"):
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
        clip = render(old, old_c["year"], new, new_c["year"], work_dir / "graphic_thennow.mp4", width=width, height=height)
        reg = Path(old).parent / REGISTRATION_SIDECAR
        if clip and reg.exists():   # the registration's measured numbers beside the clip, as its contrast is
            Path(str(clip) + ".registration.json").write_text(reg.read_text(encoding="utf-8"), encoding="utf-8")
        return clip, [old_c, new_c]
    except Exception as e:  # optional graphic
        log.warning("Then-and-now failed: %s", e)
        return None, []
