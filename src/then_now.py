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
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import requests
from PIL import Image, ImageDraw
from pydantic import BaseModel

from . import commons, config, safe_zones, url_guard
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
    get_text_box,
    record_contrast,
)
from .llm import LLMError, image_block
from .llm import task as llm_task
from .typography import TextPlate, _draw_tracked, cap_height, full_halo_pad, get_font, plan_text
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
            except (requests.RequestException, OSError, url_guard.RefusedURL) as e:
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
        except (requests.RequestException, OSError, LLMError, url_guard.RefusedURL) as e:
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
# moving (each panel shows its photo's own pixels at the same coordinates, so registered structure stays put).
# 9:16 one above the other (split at the middle of the Reel box, so each half shows the same height clear of
# the platforms' interface), 16:9 left and right. The divider is BONE. Each year is a mono THEN / NOW kicker in
# tracked gold over the year in Anton bone, no box, at a measured contrast on every background it sits on (recorded
# beside the clip). The years are the ones passed in, from each photo's own record; nothing is added.
# Panel round 4: the kickers read at about 7 pt on a phone (a 20 px cap). Now a 34 px cap on 9:16 (IBM Plex Mono
# 600 at 48 px, about 0.3 of the year's cap); and one colour for the pair (both gold, or both bone when either fails
# 4.5:1 at a full halo), so the two labels read in one colour role.
# Freeze panel 2026-11-04 (T9): the 16:9 kicker is the smallest size whose measured THEN cap is KICKER_CAP_WIDE px
# (about 46 px), tracked per format (0.118 of size on 16:9, the panel's cut on 0.15), and its year sits
# KICKER_GAP_WIDE px of ink below it (measured on the drawn glyphs). 9:16 keeps its 34 px cap, 0.15 tracking and
# its measured 28 px gap.
YEAR_PX = {"portrait": 128, "landscape": 112}
KICKER_CAP_WIDE = 32
KICKER_GAP_WIDE = 24
KICKER_TRACK = {"portrait": 0.15, "landscape": 0.118}   # the kicker's letter spacing, as a share of its size


def _kicker_px_for_cap(cap: int) -> int:
    """The smallest kicker size whose measured "THEN" cap height (its ink above the baseline) is at least `cap`."""
    for px in range(24, 96):
        if -get_font(DATA_FONT, px, weight=600).getbbox("THEN", anchor="ls")[1] >= cap:
            return px
    return 96


KICKER_PX = {"portrait": 48, "landscape": _kicker_px_for_cap(KICKER_CAP_WIDE)}
# The kicker sits on the year as one lock-up: on 9:16 the year's anchor YEAR_OFFSET kicker sizes below the
# kicker's. Each left-set line's first ink starts at the marker's x and each right-set line's last ink ends on it
# (optical alignment measured from the drawn glyphs), so THEN, NOW and every leading digit line up. On 9:16 the NOW
# kicker's cap top sits NOW_DROP px below the divider. Halo spread: KICKER_HALO px for the kicker, plan_text's own
# default for the year, each cropped wide enough for its whole tail (typography.full_halo_pad).
YEAR_OFFSET = 1.30
NOW_DROP = 96
KICKER_HALO = 36
# Freeze panel 2026-11-04 (T8, fact safety): the recent photo's kicker reads NOW only for a photo at most
# NOW_YEARS years old (UTC); an older "recent" photo reads LATER. The years drawn are always the ones passed in.
NOW_YEARS = 25
# One push, identical on both photos (the same scale and centre), so registration holds and the split hold is not a
# still frame. Freeze panel 2026-11-04 (T4): linear, 1 -> 1 + PUSH over the clip, so it moves from the first frame.
PUSH = 0.04
DIVIDER_W = 5
# Freeze panel 2026-11-04 (T4): one landing. The old photo holds alone for WIPE["then"]; the divider sweeps in
# from the far edge on a quadratic ease-out until the recent photo fills SWEEP_TO of the frame, holds there for
# WIPE["hold_far"], then settles on a smoothstep to the split, which holds for the rest (6.0 - 3.3 = 2.7 s).
WIPE = {"then": 1.2, "to_far": 1.0, "hold_far": 0.25, "settle": 0.85}   # seconds; then the split holds
SWEEP_TO = 0.8                      # the share of the frame the recent photo reaches before settling back
# Review round 3 (T4d): the settle's smoothstep peaks at 1.5 x its distance / its frames. On 16:9 (576 px in 0.85 s)
# that is 33.9 px a frame, over the 30 px limit, so the settle takes at least long enough to keep its peak within
# SETTLE_MAX_STEP: 0.96 s on 16:9; 9:16 (384 px) keeps WIPE["settle"]. Builder's choice of the two the review named
# (the far point stays 384 on both formats); the panel may take SWEEP_TO 0.70 on 16:9 instead.
SETTLE_MAX_STEP = 30.0


def settle_seconds(width: int, height: int) -> float:
    """The settle's length on this format: WIPE["settle"], or longer when its smoothstep would step more than
    SETTLE_MAX_STEP px a frame (rounded up to the frame)."""
    import math
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    dist = abs(_split_at(width, height) - edge * (1 - SWEEP_TO))
    need = math.ceil(1.5 * dist / SETTLE_MAX_STEP) / VIDEO_FPS
    return max(WIPE["settle"], need)


def _utc_year() -> int:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).year


def kicker_word(which: str, year: int) -> str:
    """THEN for the old photo; NOW for a recent one at most NOW_YEARS old (UTC year), else LATER."""
    if which == "then":
        return "THEN"
    return "NOW" if int(year) >= _utc_year() - NOW_YEARS else "LATER"


def _split_at(width: int, height: int) -> int:
    """The divider's resting place: x 960 on 16:9; on 9:16 the middle of the Reel box (y 768 at 1920)."""
    if width > height:
        return width // 2
    _, y0, _, y1 = safe_zones.safe_box("reel", width, height)
    return (y0 + y1) // 2


def divider_at(t: float, width: int, height: int) -> float:
    """Where the divider is at `t`: the far edge (the old photo whole) for WIPE["then"], a quadratic ease-out to
    far = edge x (1 - SWEEP_TO), a hold there for WIPE["hold_far"], then a smoothstep back to the resting split
    over settle_seconds (WIPE["settle"] on 9:16, 0.96 s on 16:9)."""
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    split = _split_at(width, height)
    far = edge * (1 - SWEEP_TO)
    t1 = WIPE["then"]
    t2 = t1 + WIPE["to_far"]
    t3 = t2 + WIPE["hold_far"]
    if t < t1:
        return float(edge)
    if t < t2:
        u = min(1.0, max(0.0, (t - t1) / WIPE["to_far"]))
        return edge + (far - edge) * (1 - (1 - u) ** 2)
    if t < t3:
        return float(far)
    return far + (split - far) * _ease_io((t - t3) / settle_seconds(width, height))


def push_at(t: float) -> float:
    """The photos' shared zoom at `t`: 1 at the start, 1 + PUSH at the end, linear (equal steps every frame)."""
    return 1.0 + PUSH * min(1.0, max(0.0, t / CLIP_SECONDS))


def pushed(src: Image.Image, t: float, size: tuple[int, int]) -> Image.Image:
    """`src` (cover-cropped at (1 + PUSH) x the frame, so the push shows the photo's own detail) at the push's zoom
    for `t`, centred, at frame `size`: at zoom 1 the whole of `src`, at 1 + PUSH its centre 1 / (1 + PUSH)."""
    z = push_at(t)
    cw, ch = src.width / z, src.height / z
    x0, y0 = (src.width - cw) / 2, (src.height - ch) / 2
    return src.resize(size, Image.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch))


# ---------------------------------------------------------------- framing (freeze panel 2026-11-04, T7)
# Scan borders: rows / columns within TRIM_BAND of an edge that are flat (Y std under TRIM_STD over the line's
# middle 90%) and unlike the picture (mean more than TRIM_GAP from the interior's) are a mount or scanner edge. A
# run of them from the edge (up to TRIM_LEAD odd lines at the very edge, a ruled scan edge, and up to TRIM_SKIP odd
# lines inside it are passed over) that ends inside the band is trimmed; a flat run that reaches the band's end is
# the picture itself (a sky, a wall) and is kept, so only a mount before it comes off. The union of the two
# photos' trims (as shares of each photo) comes off both, so a registered pair keeps its registration.
# 9:16 framing: when the THEN pane at rest is empty (Canny edge density under FRAME_EDGE_MIN, or more than
# FRAME_FLAT_SHARE of its rows flat), one shared window (zoom and vertical place, the same for both photos) is
# searched so the old photo's subject top sits SUBJECT_TOP of the pane below its top and both panes carry
# structure; never past FRAME_Z_MAX, nor below FRAME_MIN_SOURCE source pixels per frame pixel.
TRIM_BAND, TRIM_STD, TRIM_GAP = 0.05, 6.0, 12.0
TRIM_LEAD = 16                      # lines at the very edge (a ruled or torn scan edge) passed over before a mount
TRIM_SKIP = 12                      # odd lines inside a mount (a ruled line) passed over
TRIM_GROUND_DE, TRIM_GROUND_SHARE = 4.0, 0.5   # a border this like this much of the interior is the picture's ground
# Review round 3 (T7): nested borders. A real archive negative scan (Aswan then_0) has a black film rebate inside
# its white print margin; one pass took the margin only (the rebate ran to the band's end, or its handwriting raised
# the line std). A second (nested) pass runs on the already-trimmed image, only inside a side the first trimmed: it
# reads a line by its median and MAD spread (under TRIM_NEST_FLAT: film base is flatter than the dark shadow beside
# it, which is flat by TRIM_STD too) and takes only a level unlike the outer border's.
TRIM_PASSES = 2                     # the border, then one border nested inside it
TRIM_NEST_BAND = 0.08               # a nested pass reads this share of each edge (Aswan's right rebate: 6.4 %)
TRIM_NEST_FLAT = 2.0                # MAD spread of film base (Aswan's rebate: 0-1.5; the dark shadow beside it: 3-4.5)
TRIM_NEST_SKIP = 24                 # odd lines (a light leak, the rebate's own glow: 18 on Aswan) passed over
TRIM_JOIN_GAP, TRIM_JOIN_DY = 2, 2.0   # fix round 3: odd lines (and level spread) inside one flat field, joined
FRAME_EDGE_MIN, FRAME_FLAT_SHARE, FRAME_FLAT_STD = 0.06, 0.55, 8.0
FRAME_Z_MAX, FRAME_MIN_SOURCE, FRAME_Z_STEP = 1.6, 0.8, 0.05
SUBJECT_TOP = (0.12, 0.15)
FRAME_BEST_GAIN = 1.5               # the fallback window must carry this many times the centred THEN pane's edges
SUBJECT_LEVEL, SUBJECT_SMOOTH = 0.25, 15
# A row's edge energy is that of its most structured SUBJECT_BLOCK px column block (frame px), not the row's mean:
# a statue's head over a sky is a few blocks of a row whose mean a cracked foreground outweighs ten to one.
SUBJECT_BLOCK = 64


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


def _load_clean(path: Path) -> Image.Image:
    """The photo as cover_crop reads it: EXIF-rotated, any caption strip cut off, RGBA."""
    from PIL import ImageOps

    from .photo_hygiene import without_caption_strip
    with Image.open(path) as im:
        clean, _ = without_caption_strip(ImageOps.exif_transpose(im).convert("RGBA"))
    return clean


def _trim_pass(im: Image.Image, outer: tuple | None = None) -> tuple[list[int], list]:
    """One pass of the scan-border read (see TRIM_BAND) on `im`: ([left, top, right, bottom] px, each trimmed
    strip's median Y or None).

    `outer` (a nested pass, on an image an outer border already came off: that border's median Y per side, None
    where it trimmed nothing) reads only the sides an outer border came off, over TRIM_NEST_BAND. A line there is
    read by its median and its median absolute deviation (x 1.4826, a std that handwriting on a film rebate, under
    half of the line, does not inflate); it is border only when that spread is under TRIM_NEST_FLAT (film base is
    flatter than a mount, and a dark shadow beside a black rebate is not) and its median is more than TRIM_GAP from
    both the interior's and the outer border's (a rebate inside a margin; more of the same sky or mount is not a
    nested border). Up to TRIM_NEST_SKIP odd lines (a light leak, a rebate's own glow) are passed over."""
    import numpy as np
    nested = outer is not None
    y = _luma(np.asarray(im.convert("RGB")))
    h, w = y.shape
    inner = float(y[int(h * 0.1):max(int(h * 0.1) + 1, int(h * 0.9)), int(w * 0.1):max(int(w * 0.1) + 1, int(w * 0.9))].mean())
    ys, xs = slice(int(h * 0.05), max(int(h * 0.05) + 1, int(h * 0.95))), slice(int(w * 0.05), max(int(w * 0.05) + 1, int(w * 0.95)))

    def stats(v) -> tuple[float, float]:
        """(level for the gap test, spread for the flat test) of one line."""
        if not nested:
            return float(v.mean()), float(v.std())
        med = float(np.median(v))
        return med, 1.4826 * float(np.median(np.abs(v - med)))

    def run(line, band: int, level: float | None) -> int:
        segments: list[list[float]] = []      # runs of border lines: [first, last, sum of levels, lines]
        miss = 0
        for i in range(band):
            lv, sd = stats(line(i))
            border = sd < (TRIM_NEST_FLAT if nested else TRIM_STD) and abs(lv - inner) > TRIM_GAP
            if border and nested:
                border = abs(lv - level) > TRIM_GAP
            if border:
                # Fix round 3: a line or two that is not border, between two runs of one level (within
                # TRIM_JOIN_DY), is noise inside one flat field, not a mount's edge (Aswan then_2's sky: four runs at
                # 249.6-251.4 Y split by single lines, read as 203 px of 'mount' over the sky). A mount's edge is a
                # transition of several lines (Colossi then_0's top: 7 lines, 229.7 -> 225.1 Y), so it stays.
                if segments and (miss == 0 or (miss <= TRIM_JOIN_GAP
                                               and abs(lv - segments[-1][2] / segments[-1][3]) <= TRIM_JOIN_DY)):
                    segments[-1][1] = i
                    segments[-1][2] += lv
                    segments[-1][3] += 1
                else:
                    segments.append([i, i, lv, 1])
                miss = 0
            else:
                miss += 1
                if miss >= (TRIM_NEST_SKIP if nested else TRIM_SKIP if segments else TRIM_LEAD):
                    break
        if not segments:
            return 0
        if segments[-1][1] >= band - 3:       # flat to the band's end: the picture itself (a sky, a wall) ...
            return int(segments[-2][1]) + 1 if len(segments) > 1 else 0   # ... after the mount, if one came first
        return int(segments[-1][1]) + 1

    band = TRIM_NEST_BAND if nested else TRIM_BAND
    bh, bw = int(h * band), int(w * band)
    lines = ((lambda i: y[ys, i], bw), (lambda i: y[i, xs], bh), (lambda i: y[ys, w - 1 - i], bw),
             (lambda i: y[h - 1 - i, xs], bh))
    levels = outer if nested else (0.0, 0.0, 0.0, 0.0)
    trims = [0 if lvl is None else run(f, n, lvl) for (f, n), lvl in zip(lines, levels)]
    if any(trims):
        trims = _keep_picture_ground(im, trims)
    strips = (lambda n: y[:, :n], lambda n: y[:n], lambda n: y[:, w - n:], lambda n: y[h - n:])
    return trims, [float(np.median(strips[k](n))) if n else None for k, n in enumerate(trims)]


def scan_trims(im: Image.Image) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) px of scan border on `im` (see TRIM_BAND), then (TRIM_PASSES) a border nested
    inside it: a second pass reads the image the first trimmed (its own interior, line widths and bands), so a
    negative's black film rebate inside the print's white margin comes off too (see _trim_pass)."""
    total = [0, 0, 0, 0]
    cur, outer = im, None
    for _ in range(TRIM_PASSES):
        t, levels = _trim_pass(cur, outer)
        if not any(t):
            break
        total = [a + b for a, b in zip(total, t)]
        if total[0] + total[2] >= im.width // 2 or total[1] + total[3] >= im.height // 2:
            break
        cur, outer = im.crop((total[0], total[1], im.width - total[2], im.height - total[3])), tuple(levels)
    return tuple(total)


def _keep_picture_ground(im: Image.Image, trims: list) -> list:
    """Review round 3 (T7e): a flat edge whose colour is the picture's own ground is not a mount. A side's border
    (its median CIELAB colour) that matches (within TRIM_GROUND_DE, 8-bit Lab) more than TRIM_GROUND_SHARE of the
    photo's interior (the middle 80 %) is kept: a map or diagram set on white paper (Odessa now_0_4: 53 % of its
    interior is its margin's white) keeps its margin, while a scan mount (Colossi then_0: 8-42 %) comes off."""
    import cv2
    import numpy as np
    s = min(1.0, 1000 / max(im.size))
    small = im.convert("RGB")
    if s < 1.0:
        small = small.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.BILINEAR)
    lab = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    inner = lab[int(h * 0.1):max(int(h * 0.1) + 1, int(h * 0.9)), int(w * 0.1):max(int(w * 0.1) + 1, int(w * 0.9))].reshape(-1, 3)
    out = []
    for k, n in enumerate(trims):
        m = max(1, round(n * s)) if n else 0
        if not m:
            out.append(0)
            continue
        strip = (lab[:, :m], lab[:m], lab[:, w - m:], lab[h - m:])[k].reshape(-1, 3)
        med = np.median(strip, axis=0)
        share = float((np.sqrt(((inner - med) ** 2).sum(axis=1)) < TRIM_GROUND_DE).mean())
        out.append(0 if share > TRIM_GROUND_SHARE else n)
    return out


def _cover_window(size: tuple[int, int], big: tuple[int, int], z: float = 1.0, v: float = 0.5) -> tuple[float, ...]:
    """The source box (of a photo `size`) a cover crop to `big` at zoom `z` takes, centred across and with its
    centre at `v` of the photo's height (clamped inside the photo)."""
    sw, sh = size
    s = max(big[0] / sw, big[1] / sh)
    ww, wh = big[0] / s / z, big[1] / s / z
    cy = min(max(v * sh, wh / 2), sh - wh / 2)
    x0 = (sw - ww) / 2
    return (x0, cy - wh / 2, x0 + ww, cy + wh / 2)


def _subject_top(src: Image.Image, big: tuple[int, int]) -> float | None:
    """The old photo's subject top as a share of its height: the first row of its 15-row smoothed row edge-energy
    profile (over the columns a centred cover crop to `big` keeps, each row read as its most structured
    SUBJECT_BLOCK px block) above SUBJECT_LEVEL of the profile's maximum."""
    import cv2
    import numpy as np
    x0, _, x1, _ = _cover_window(src.size, big)
    s = big[1] / src.height if big[1] / src.height < 1 else 1.0
    small = src.convert("RGB").crop((round(x0), 0, round(x1), src.height))
    small = small.resize((max(1, round(small.width * s)), max(1, round(small.height * s))), Image.BILINEAR)
    y = _luma(np.asarray(small))
    g = np.hypot(cv2.Sobel(y, cv2.CV_32F, 1, 0), cv2.Sobel(y, cv2.CV_32F, 0, 1))
    nb = max(1, g.shape[1] // SUBJECT_BLOCK)
    blocks = g[:, :nb * SUBJECT_BLOCK].reshape(g.shape[0], nb, -1).mean(axis=2) if g.shape[1] >= SUBJECT_BLOCK else g.mean(axis=1, keepdims=True)
    k = np.ones(SUBJECT_SMOOTH, np.float32) / SUBJECT_SMOOTH
    prof = np.stack([np.convolve(blocks[:, j], k, mode="same") for j in range(blocks.shape[1])], axis=1).max(axis=1)
    if prof.size == 0 or prof.max() <= 1.0:
        return None
    rows = np.flatnonzero(prof > SUBJECT_LEVEL * prof.max())
    return float(rows[0] / len(prof)) if rows.size else None


def frame_pair(old: Path, new: Path, width: int, height: int) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
    """Both photos cover-cropped at (1 + PUSH) x the frame, scan borders trimmed and (9:16) re-anchored on one
    shared window when the THEN pane would show nothing; and what was done (trims, zoom, subject row)."""
    import numpy as np
    from PIL import ImageOps
    big = (round(width * (1 + PUSH)), round(height * (1 + PUSH)))
    ims = [_load_clean(old), _load_clean(new)]
    trims = [scan_trims(im) for im in ims]
    share = [max(t[k] / (im.width if k % 2 == 0 else im.height) for t, im in zip(trims, ims)) for k in range(4)]
    info: dict[str, Any] = {"trims_px": {"old": list(trims[0]), "new": list(trims[1])},
                            "trim_share": [round(v, 4) for v in share], "zoom": 1.0, "window": "centred"}
    if any(share):
        info["trimmed_px"] = {k: [round(share[0] * im.width), round(share[1] * im.height), round(share[2] * im.width),
                                  round(share[3] * im.height)] for k, im in zip(("old", "new"), ims)}
        ims = [im.crop((round(share[0] * im.width), round(share[1] * im.height),
                        im.width - round(share[2] * im.width), im.height - round(share[3] * im.height))) for im in ims]
    srcs = [ImageOps.fit(im, big, Image.LANCZOS).convert("RGB") for im in ims]
    if width > height:
        return srcs[0], srcs[1], info
    size = (width, height)
    split = _split_at(width, height)

    def panes(a: Image.Image, b: Image.Image) -> tuple[Any, Any]:
        return (np.asarray(pushed(a, CLIP_SECONDS, size))[:split], np.asarray(pushed(b, CLIP_SECONDS, size))[split:])

    top_pane, low_pane = panes(*srcs)
    y = _luma(top_pane)
    flat = float((y.std(axis=1) < FRAME_FLAT_STD).mean())
    dens = _edge_density(top_pane)
    info.update({"then_pane_edge": round(dens, 4), "then_pane_flat_rows": round(flat, 3),
                 "now_pane_edge": round(_edge_density(low_pane), 4)})
    if dens >= FRAME_EDGE_MIN and flat <= FRAME_FLAT_SHARE:
        return srcs[0], srcs[1], info
    info["gate"] = "THEN pane empty"
    subject = _subject_top(ims[0], big)
    per_px = min(_cover_window(im.size, big)[3] - _cover_window(im.size, big)[1] for im in ims) / big[1]
    z_max = min(FRAME_Z_MAX, per_px / FRAME_MIN_SOURCE)
    info.update({"subject_top_share": None if subject is None else round(subject, 4), "z_max": round(z_max, 3)})
    if subject is None or z_max < 1.0:
        log.info("Then-and-now 9:16: the THEN pane is empty and no shared window fixes it (%s); centred crop kept.",
                 "no subject found" if subject is None else f"zoom limit {z_max:.2f}")
        info["window"] = "centred (no window found)"
        return srcs[0], srcs[1], info
    ch = big[1] / (1 + PUSH)                      # the big image's rows the frame shows at rest
    lead = (big[1] - ch) / 2
    want = sum(SUBJECT_TOP) / 2 * split
    old_im = ims[0]
    best = None                                    # the in-band window with the most THEN structure
    for k in range(int((z_max - 1.0) / FRAME_Z_STEP + 1e-6) + 1):
        z = round(1.0 + k * FRAME_Z_STEP, 4)
        x0, _, x1, _ = _cover_window(old_im.size, big, z)
        wh = (x1 - x0) * big[1] / big[0]
        s_row = subject * old_im.height
        top = s_row - (lead + want * ch / height) * wh / big[1]
        top = min(max(top, 0.0), old_im.height - wh)
        v = (top + wh / 2) / old_im.height
        row = ((s_row - top) * big[1] / wh - lead) * height / ch
        if not SUBJECT_TOP[0] * split <= row <= SUBJECT_TOP[1] * split:
            log.debug("Then-and-now 9:16 window: zoom %.2f puts the subject at row %.0f.", z, row)
            continue
        cand = [im.resize(big, Image.LANCZOS, box=_cover_window(im.size, big, z, v)).convert("RGB") for im in ims]
        tp, lp = panes(*cand)
        dt, dl = _edge_density(tp), _edge_density(lp)
        log.debug("Then-and-now 9:16 window: zoom %.2f, subject row %.0f, pane edges %.3f / %.3f.", z, row, dt, dl)
        if dl >= FRAME_EDGE_MIN and (best is None or dt > best[0] + 1e-9):
            best = (dt, dl, z, v, row, cand)
        if dt >= FRAME_EDGE_MIN and dl >= FRAME_EDGE_MIN:
            info.update({"zoom": z, "window": "shared", "v_centre": round(v, 4), "subject_row": round(row, 1),
                         "subject_row_share": round(row / split, 4), "then_pane_edge_after": round(dt, 4),
                         "now_pane_edge_after": round(dl, 4),
                         "source_px_per_frame_px": round(per_px / z, 3)})
            log.info("Then-and-now 9:16: shared window at zoom %.2f, the subject top at %.1f%% of the THEN pane.",
                     z, 100 * row / split)
            return cand[0], cand[1], info
    # Review round 3 (T7a): a soft old print (Colossi then_0, an 1876 albumen print) never reaches FRAME_EDGE_MIN
    # under Canny(50, 150), so no window passed and the pane stayed sky. The in-band window (the subject's top at
    # SUBJECT_TOP of the pane) with the most THEN structure is taken when it carries at least FRAME_BEST_GAIN x the
    # centred pane's, and recorded as below the threshold; otherwise the centred crop is kept.
    if best is not None and best[0] >= FRAME_BEST_GAIN * dens:
        dt, dl, z, v, row, cand = best
        info.update({"zoom": z, "window": "shared (most THEN structure; under FRAME_EDGE_MIN)", "v_centre": round(v, 4),
                     "subject_row": round(row, 1), "subject_row_share": round(row / split, 4),
                     "then_pane_edge_after": round(dt, 4), "now_pane_edge_after": round(dl, 4),
                     "source_px_per_frame_px": round(per_px / z, 3)})
        log.info("Then-and-now 9:16: no window reaches edge density %.2f; the subject-top window at zoom %.2f "
                 "(THEN pane %.3f against %.3f centred) is used.", FRAME_EDGE_MIN, z, dt, dens)
        return cand[0], cand[1], info
    log.info("Then-and-now 9:16: the THEN pane is empty and no zoom up to %.2f places the subject; centred crop kept.",
             z_max)
    info["window"] = "centred (no window found)"
    return srcs[0], srcs[1], info


KEYLINE_W, KEYLINE_ALPHA = 2, 170   # the INK keyline either side of the bone divider (freeze panel 2026-11-04)


def draw_divider(im: Image.Image, pos: float) -> Image.Image:
    """The divider at `pos`, drawn into `im`: bone over the DIVIDER_W px centred on round(pos) (p - 2 .. p + 2),
    and an INK keyline KEYLINE_W px wide at KEYLINE_ALPHA on each side (p - 4 .. p - 3 and p + 3 .. p + 4), the
    same on both sides. Nothing at either edge (the divider is off-frame there)."""
    width, height = im.size
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    p = round(max(0, min(edge, pos)))
    if not 0 < p < edge:
        return im
    half = DIVIDER_W // 2

    def band(lo: int, hi: int) -> list:      # inclusive pixel columns (16:9) or rows (9:16)
        return [lo, 0, hi, height - 1] if axis == 0 else [0, lo, width - 1, hi]
    d = ImageDraw.Draw(im, "RGBA")
    for lo, hi in ((p - half - KEYLINE_W, p - half - 1), (p + half + 1, p + half + KEYLINE_W)):
        d.rectangle(band(lo, hi), fill=rgba(INK, KEYLINE_ALPHA))
    d.rectangle(band(p - half, p + half), fill=rgba(BONE))
    return im


def compose(a: Image.Image, b: Image.Image, pos: float, divider: bool = True) -> Image.Image:
    """The old photo `a` before the divider at `pos` (left on 16:9, above on 9:16), the recent `b` after it, each
    at its own pixels (a fixed crop, never a sliding window), so a registered pair lines up across the divider.
    `divider`: draw it (draw_divider); render() draws it last, over the glow and the labels."""
    width, height = a.size
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    p = round(max(0, min(edge, pos)))
    im = b.copy() if b.size == a.size else b.resize(a.size)
    if p > 0:
        box = (0, 0, p, height) if axis == 0 else (0, 0, width, p)
        im.paste(a.crop(box), (0, 0))
    return draw_divider(im, p) if divider else im


# Freeze panel 2026-11-04 (T6): a 180-degree shutter on the seam. When the divider moves more than BLUR_MIN_STEP px
# in a frame, the THEN / NOW join is a linear ramp SHUTTER x |step| wide on its trailing side and the divider is
# its own profile box-averaged over that width (the same bone and keyline energy, spread); slower frames take the
# hard join, byte for byte.
MOTION_BLUR = True
BLUR_MIN_STEP = 8.0
SHUTTER = 0.5


def seam_weights(edge: int, pos: float, dpos: float) -> Any:
    """The THEN layer's weight along the wipe axis for a divider at `pos` that moved `dpos` px this frame: 1 before
    the divider, 0 after it, and a linear ramp SHUTTER x |dpos| wide on the side it came from."""
    import numpy as np
    p = round(max(0, min(edge, pos)))
    x = np.arange(edge, dtype=np.float32) + 0.5
    w = max(1e-6, SHUTTER * abs(dpos))
    if dpos < 0:      # moving towards 0: it came from after p, where the THEN photo still shows in part
        return np.where(x < p, 1.0, np.clip(1.0 - (x - p) / w, 0.0, 1.0)).astype(np.float32)
    return np.clip((p - x) / w, 0.0, 1.0).astype(np.float32)


def divider_profile(edge: int, pos: float, dpos: float) -> tuple[Any, Any]:
    """(bone coverage, keyline coverage) along the axis: the static divider's profile averaged over the positions
    it passed through in the shutter (the trailing SHUTTER x |dpos| px)."""
    import numpy as np
    w = SHUTTER * abs(dpos)
    k = max(2, 2 * int(np.ceil(w)))
    sgn = 1.0 if dpos < 0 else -1.0
    xs = np.arange(edge)
    fb = np.zeros(edge, np.float32)
    fk = np.zeros(edge, np.float32)
    half = DIVIDER_W // 2
    for j in range(k):
        q = round(pos + sgn * (j + 0.5) / k * w)
        if not 0 < q < edge:
            continue
        d = np.abs(xs - q)
        fb += d <= half
        fk += (d > half) & (d <= half + KEYLINE_W)
    return fb / k, fk / k


def join_blurred(then_im: Image.Image, now_im: Image.Image, pos: float, dpos: float) -> Image.Image:
    """The two layers joined on the motion-blurred seam, with the motion-blurred divider over it."""
    import numpy as np
    width, height = then_im.size
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    m = seam_weights(edge, pos, dpos)
    fb, fk = divider_profile(edge, pos, dpos)
    # Review round 2 (T6c): only the band where the join ramps or the divider draws is mixed in float; before it the
    # THEN layer shows whole (weight 1) and after it the NOW layer (weight 0), pasted as they are, which is the
    # same result byte for byte at a fraction of the cost.
    mixed = np.flatnonzero(((m > 0) & (m < 1)) | (fb > 0) | (fk > 0))
    full = np.flatnonzero(m >= 1)
    lo = int(mixed[0]) if mixed.size else edge
    hi = int(mixed[-1]) + 1 if mixed.size else edge
    then_to = int(full[-1]) + 1 if full.size else 0       # m is 1 then falls: THEN up to here
    then_rgb = then_im if then_im.mode == "RGB" else then_im.convert("RGB")
    out_im = now_im.convert("RGB")                     # a copy (convert always returns a new image)
    b_band = None
    if hi > lo:
        box = (lo, 0, hi, height) if axis == 0 else (0, lo, width, hi)
        b_band = np.asarray(out_im.crop(box), np.float32)
    if then_to > 0:
        out_im.paste(then_rgb.crop((0, 0, then_to, height) if axis == 0 else (0, 0, width, then_to)), (0, 0))
    if hi > lo:
        shape = (1, hi - lo, 1) if axis == 0 else (hi - lo, 1, 1)
        mb, bb, kb = (v[lo:hi].reshape(shape) for v in (m, fb, fk))
        a = np.asarray(then_rgb.crop(box), np.float32)
        b = b_band
        out = b + (a - b) * mb
        ka = KEYLINE_ALPHA / 255.0
        out = (out * (1.0 - bb - kb * ka) + np.asarray(BONE, np.float32) * bb
               + np.asarray(INK, np.float32) * (ka * kb))
        out_im.paste(Image.fromarray(np.clip(np.round(out), 0, 255).astype(np.uint8), "RGB"), box[:2])
    return out_im


def marker_xy(which: str, width: int, height: int, side: str = "left", word: str | None = None) -> tuple[int, int, str]:
    """(x, y, anchor) of a year marker's kicker. 9:16: in each panel's part of the Reel box, left-set at its
    left edge + 18 or (side "right", T10) right-set to its right edge - 18, the NOW kicker's cap top NOW_DROP px
    below the divider. 16:9: either side of the divider at the top of title-safe, THEN set right to it and NOW
    left (the master's vignette takes the corners of a 16:9 frame below 4.5:1, measured on the graded frame)."""
    split = _split_at(width, height)
    if width > height:
        _, y0, _, _ = get_text_box(width, height)
        return (split - 64, y0 + 40, "rt") if which == "then" else (split + 64, y0 + 40, "lt")
    x0, y0, x1, _ = safe_zones.safe_box("reel", width, height)
    x, anchor = (x1 - 18, "rt") if side == "right" else (x0 + 18, "lt")
    if which == "then":
        return x, max(y0 + 42, TEXT_TOP), anchor
    kicker = get_font(DATA_FONT, KICKER_PX["portrait"], weight=600)
    return x, split + NOW_DROP - kicker.getbbox(word or "NOW", anchor="lt")[1], anchor


def _ink_left(font: Any, text: str) -> int:
    """How far right of a left-anchored ("lt") x the first glyph of `text` starts to ink (its side bearing),
    measured on the glyph as drawn (the font's own bbox is hinted and was up to 2 px off the drawn ink)."""
    return _drawn_ink_left(str(getattr(font, "path", "")), int(font.size), text[:1], font) if text else 0


@lru_cache(maxsize=64)
def _drawn_ink_left(path: str, size: int, ch: str, font: Any) -> int:
    pad = 4 * size
    im = Image.new("L", (6 * size, 3 * size), 0)
    ImageDraw.Draw(im).text((pad, size), ch, font=font, fill=255, anchor="lt")
    bb = im.getbbox()
    return bb[0] - pad if bb else 0


@lru_cache(maxsize=64)
def _drawn_box(path: str, size: int, text: str, track: float, anchor: str, font: Any) -> tuple[int, int, int, int]:
    """The drawn ink box of `text` set at (0, 0) with `anchor` and `track` (tracked as typography draws it)."""
    w = int(size * (len(text) + 4) + track * len(text)) + 8 * size
    ox, oy = w // 2, 2 * size
    im = Image.new("L", (w, 4 * size), 0)
    _draw_tracked(ImageDraw.Draw(im), (ox, oy), text, font, 255, anchor, track)
    bb = im.getbbox() or (ox, oy, ox, oy)
    return bb[0] - ox, bb[1] - oy, bb[2] - ox, bb[3] - oy


def _ink(font: Any, text: str, track: float = 0, anchor: str = "lt") -> tuple[int, int, int, int]:
    return _drawn_box(str(getattr(font, "path", "")), int(font.size), text, float(track), anchor, font)


def marker_lines(which: str, year: int, width: int, height: int, word: str | None = None,
                 side: str = "left") -> tuple[tuple[int, int], tuple[int, int], str]:
    """(kicker xy, year xy, anchor) of a marker whose kicker reads `word` (default which.upper()). Left-set lines
    are shifted by their first glyph's measured side bearing, so the kicker's and the year's first ink start on one
    column (the column where the THEN kicker set at the marker's x inks); right-set ones (16:9 THEN, 9:16 side
    "right") are shifted by their drawn last ink, so both end on the marker's x. The year sits YEAR_OFFSET kicker
    sizes below on 9:16, and KICKER_GAP_WIDE px of drawn ink below the kicker's ink on 16:9."""
    word = word or which.upper()
    x, y, anchor = marker_xy(which, width, height, side, word)
    key = "landscape" if width > height else "portrait"
    kicker = get_font(DATA_FONT, KICKER_PX[key], weight=600)
    figure = get_font("Anton", YEAR_PX[key])
    track = KICKER_PX[key] * KICKER_TRACK[key]
    if key == "landscape":
        fy = y + _ink(kicker, word, track)[3] + KICKER_GAP_WIDE - _ink(figure, str(year))[1]
    else:
        fy = y + round(KICKER_PX[key] * YEAR_OFFSET)
    if anchor[0] == "r":
        return ((x - _ink(kicker, word, track, "rt")[2], y), (x - _ink(figure, str(year), 0, "rt")[2], fy), anchor)
    column = x + _ink_left(kicker, "THEN")
    return (column - _ink_left(kicker, word), y), (column - _ink_left(figure, str(year)), fy), anchor


def _marker_plate(split_frame: Image.Image, alone: Image.Image, xy: tuple[int, int], text: str, font: Any,
                  colour: tuple[int, int, int], anchor: str, track: float, halo_px: int | None, min_halo: float,
                  target: float = PLACE_TARGET, max_halo: float = 1.0) -> Any:
    """A marker line planned on the split and solved on the whole photo it also sits on, then on the split again
    from that halo, so it holds `target` on both (the thin mono kicker loses about 0.6 to the grade). The plate is
    cropped wide enough for its whole halo (full_halo_pad); `halo_px` 0 is a line with no halo (ink type);
    `max_halo` caps the halo (GLOW_HALO_CAP while a glow is used)."""
    hp = halo_px if halo_px is not None else _year_halo_px(font)
    plate = plan_text(split_frame, xy, text, font, colour, anchor=anchor, track=track, halo_px=hp,
                      target=target, min_halo=min_halo, max_halo=max_halo, halo_pad=full_halo_pad(hp) if hp else None)
    plate.solve(alone, target, min_halo=plate.halo, max_halo=max_halo)
    return plate.solve(split_frame, target, min_halo=plate.halo, max_halo=max_halo)


def _year_halo_px(font: Any) -> int:
    return int(min(31, max(21, font.size // 3)))   # plan_text's own default


# The marker's glow (round 4 onwards): ink behind the marker only, full depth over its lines and BAND_PAD px
# round them, falling on a smoothstep (no edge) to nothing band_fall px beyond, kept to its own photo at each
# frame's divider. Deepened in BAND_STEPS from light, stopping at the first depth at which the marker holds.
# Freeze panel 2026-11-04 (T2): the fall is GLOW_REACH x the year's measured Anton cap less BAND_PAD (about 38 px on
# 9:16 and 32 on 16:9, down from 84): no smudge round the lock-up. BAND_FALL is the longer (9:16) fall, the
# furthest any glow reaches. The glow's alpha is float end to end (T3) and dithered where it falls.
SHADE_ALPHA, SHADE_FALL = 0.5, 180
SHADE_STEPS = (0.5, 0.62, 0.75, 0.88)
BAND_PAD = 12
GLOW_REACH = 0.45
BAND_STEPS = (0.2, 0.28, 0.36, 0.45, 0.55, 0.65, 0.75, 0.88)
# T2: one halo per pair (the largest any bone/gold line needed, at least PAIR_HALO_FLOOR), and when a glow is used
# the shortfall is made up as: halo up to GLOW_HALO_CAP, glow up to GLOW_BRIGHT_CAP (on a ground whose local mean
# luma is at least GLOW_BRIGHT_Y; any depth on a darker one), halo up to 1.0, then the deeper BAND_STEPS.
PAIR_HALO_FLOOR = 0.6
GLOW_HALO_CAP, GLOW_BRIGHT_CAP, GLOW_BRIGHT_Y = 0.80, 0.45, 200.0
# Review round 3 (handover: no grey cloud on bright grounds): on a ground of local mean luma CLOUD_Y or more the
# halo goes to 1.0 before any glow, so a glow is used only where the full halo cannot hold, and then as shallow as
# holds. A builder's change to T2(2)'s order (halo 0.80, glow, halo 1.0), recorded for the panel.
CLOUD_Y = 180.0


@lru_cache(maxsize=4)
def year_cap(key: str) -> int:
    """The measured cap height of the Anton year at YEAR_PX[key]."""
    return cap_height(get_font("Anton", YEAR_PX[key]))


def band_fall(width: int, height: int) -> int:
    """The glow's fall on this format: round(GLOW_REACH x the year's cap) - BAND_PAD."""
    return round(GLOW_REACH * year_cap("landscape" if width > height else "portrait")) - BAND_PAD


# Review round 3 (T3b against T2's short fall, the handover's "smooth"): a smoothstep fall of F px from depth d over
# a ground of luma Y steps at most 1.5 x d x (Y - GLOW_INK's luma) / F per px. The fall is band_fall, lengthened
# where that would step more than GLOW_MAX_STEP Y a px (a deep glow on a bright ground), so the fall is smooth on
# every ground; a shallow glow or a darker ground keeps band_fall. Builder's resolution of the conflict the review
# named, pending the panel: smoothness wins over the reach limit, and only where the two disagree.
GLOW_MAX_STEP = 2.0


def glow_fall(width: int, height: int, depth: float = 0.0, ground_y: float | None = None) -> int:
    """The glow's fall for `depth` over a ground of mean luma `ground_y` (None: band_fall)."""
    import math
    base = band_fall(width, height)
    if ground_y is None or depth <= 0:
        return base
    ink_y = 0.299 * GLOW_INK[0] + 0.587 * GLOW_INK[1] + 0.114 * GLOW_INK[2]
    return max(base, math.ceil(1.5 * depth * max(0.0, ground_y - ink_y) / GLOW_MAX_STEP))


BAND_FALL = band_fall(1080, 1920)   # the longer fall (9:16): the furthest any glow reaches
# The glow's distance is a soft minimum over the marker's lines (each line's own distance field, combined as
# -K ln sum exp(-d / K)), so where the short kicker meets the wider year the glow's outline is rounded, not a
# crease. The glow is a warm ink (GLOW_INK), composited in float with a static TPDF dither (T3: GLOW_DITHER_LSB
# video codes, one value per 2 x 2 cell, the same on all three channels and every frame, only where the glow is
# partial) so a shallow ramp on a smooth sky does not terrace after the delivery encode.
GLOW_SMOOTH_K = 16.0
GLOW_INK = (30, 22, 16)             # #1E1610
GLOW_DITHER_SEED = 1925
GLOW_DITHER_LSB = 1.75              # TPDF peak, in limited-range video codes (x 255 / 219 in RGB codes)
# Freeze panel 2026-11-04 (T1): ink type on paper, per marker. The box round a marker's kicker and year (their
# ink boxes' union grown by PAPER_PAD px, kept to its photo) is paper when, on its split and on its alone frame
# (unshaded), its mean luma is at least PAPER_MEAN, its 10th percentile at least PAPER_P10 and its Canny edge
# density at most PAPER_EDGE. Such a marker is set in brand INK, no glow and no halo; if an ink line then measures
# under PLACE_TARGET it takes the bone/gold path. With one ink marker the other's kicker is bone (two neutrals).
# Review round 2 (T1b): ink type has no halo to fall back on, so it must plan at least INK_TARGET on its split
# frame; a paper ground that holds ink under it (Gol Gumbaz THEN: 6.30 and 6.35) takes the bone/gold path.
INK_ON_PAPER = True
PAPER_MEAN, PAPER_P10, PAPER_EDGE, PAPER_PAD = 195.0, 140.0, 0.20, 24
# Review round 3 (T1, line art): paper is plain. A ground with drawn or dark structure in the box (a diagram's black
# rules and labels, a map's printed names, a building's dark edge) collides with ink type, which the contrast ratio
# cannot see (Aswan's diagram measured 13-14:1 with NOW 2005 over its own black labels). So the box must also hold
# at most PAPER_DARK of its pixels darker than PAPER_DARK_FRAC x its mean luma. Measured: Odessa 9:16 NOW 0.000,
# Colossi THEN 0.000-0.002; Aswan 0.017-0.061, Gol Gumbaz map THEN 0.031-0.040, Gol Gumbaz 16:9 NOW 0.068-0.069.
# Fix round 2 (operator 2026-11-04: no grey cloud on a bright ground worse than HEAD): the dark share is read under
# the lines themselves (their ink boxes' union, PAPER_DARK_PAD px, kept to its photo), not over the PAPER_PAD box: a
# rock ridge or a statue's head beside the type does not collide with ink, structure under it does. The mean, p10
# and edge tests keep the PAPER_PAD box. Measured on the real runs (split / alone): Aswan 9:16 THEN on white sky
# 0.016 / 0.003 (was 0.048 / 0.017 padded: bone, halo 1.0 and a 0.20 glow), so ink as at HEAD; 9:16 Colossi THEN on
# albumen sky 0.000 / 0.000 (was 0.002 / 0.027), so ink as on 16:9; still refused: Aswan's diagram NOW 0.090 / 0.090,
# Gol Gumbaz map THEN 0.038 / 0.037 (9:16) and 0.021 / 0.024 (16:9), Gol Gumbaz 16:9 NOW 0.039 / 0.047.
PAPER_DARK_FRAC, PAPER_DARK, PAPER_DARK_PAD = 0.6, 0.02, 0
INK_TARGET = 7.0
# freeze step 1: the last render's gate stats and type mode per marker ("ink" or "bone/gold"; a record only)
LAST_PAPER: dict[str, dict[str, Any]] = {}


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
                lines: bool = False, as_float: bool = False) -> Any:
    """An ink gradient (L mode, 0..255 at `alpha`) anchored at `corner` (default the top-left) on the frame's edge:
    full over the hull of the anchor and the box round the markers' `boxes`, then a smoothstep to 0 over `fall` px
    of screen distance past that hull. `pad` holds full depth that many px past the hull; `lines` measures from
    each of `boxes` itself, not a hull. `as_float`: the alpha itself (H x W float32, 0..`alpha`), not an L image."""
    import cv2
    import numpy as np
    w, h = size
    ax, ay = corner if corner is not None else (0, 0)
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)     # the marker's lines as one block
    outside = np.full((h, w), 255, np.uint8)
    if lines:
        d = _lines_distance(w, h, tuple(tuple(round(v) for v in b[:4]) for b in boxes), int(pad + fall))
    else:
        pts = np.array([(ax, ay), (x0, y0), (x1, y0), (x1, y1), (x0, y1)], np.float32)
        cv2.fillConvexPoly(outside, cv2.convexHull(pts).round().astype(np.int32), 0)
        d = cv2.distanceTransform(outside, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)   # px to the hull; 0 inside it
    f = np.clip((np.asarray(d, np.float32) - np.float32(pad)) / np.float32(fall), 0.0, 1.0)
    f = 1.0 - f * f * (3 - 2 * f)            # smoothstep fall-off
    if as_float:
        return (f * np.float32(alpha)).astype(np.float32)
    return Image.fromarray(np.round(255 * alpha * f).astype(np.uint8), "L")


@lru_cache(maxsize=8)
def _lines_distance(w: int, h: int, boxes: tuple, reach: int = 0) -> Any:
    """Screen px to the marker's lines `boxes` (each the pixels b[0]..b[2]-1 by b[1]..b[3]-1), as a soft minimum of
    the lines' own distances (GLOW_SMOOTH_K): 0 inside a line, each line's own distance far from the others, and
    rounded where two lines' fields meet at an angle. The soft minimum's cut is weighted by how far the fields'
    directions differ (1 - cos, at most 1), so the glow's outer extent does not grow. Worked out in a window round
    the lines; beyond the glow's reach it is left far."""
    import numpy as np
    k = GLOW_SMOOTH_K
    grow = max(BAND_PAD + BAND_FALL, reach) + 8 * k
    x0, y0 = max(0, int(min(b[0] for b in boxes) - grow)), max(0, int(min(b[1] for b in boxes) - grow))
    x1, y1 = min(w, int(max(b[2] for b in boxes) + grow)), min(h, int(max(b[3] for b in boxes) + grow))
    out = np.full((h, w), float(w + h))
    if x1 > x0 and y1 > y0:
        ys = np.arange(y0, y1, dtype=np.float64)[:, None]
        xs = np.arange(x0, x1, dtype=np.float64)[None, :]
        vx, vy = [], []
        for b in boxes:          # the vector from each line's nearest pixel
            vx.append(np.broadcast_to(np.where(xs < b[0], xs - b[0], np.maximum(xs - (b[2] - 1), 0.0)), (y1 - y0, x1 - x0)))
            vy.append(np.broadcast_to(np.where(ys < b[1], ys - b[1], np.maximum(ys - (b[3] - 1), 0.0)), (y1 - y0, x1 - x0)))
        dist = np.stack([np.hypot(a, b) for a, b in zip(vx, vy)])
        near = dist.argmin(axis=0)
        low = np.take_along_axis(dist, near[None], 0)[0]
        nx = np.take_along_axis(np.stack(vx), near[None], 0)[0]
        ny = np.take_along_axis(np.stack(vy), near[None], 0)[0]
        share = np.exp(-(dist - low) / k)                       # each line's part in the cut (the nearest: 1)
        weight = np.zeros_like(low)
        for i in range(len(boxes)):
            with np.errstate(invalid="ignore", divide="ignore"):
                cos = (nx * vx[i] + ny * vy[i]) / (low * dist[i])
            apart = np.where((low > 0) & (dist[i] > 0), np.minimum(1.0, 1.0 - cos), 1.0)
            weight += np.where(near == i, 0.0, share[i] * apart)
        others = share.sum(axis=0) - 1.0
        with np.errstate(invalid="ignore", divide="ignore"):
            weight = np.where(others > 0, weight / others, 0.0)
        out[y0:y1, x0:x1] = np.maximum(low - weight * k * np.log(share.sum(axis=0)), 0.0)
    out.setflags(write=False)
    return out


def glow_alpha(boxes: list, width: int, height: int, alpha: float = SHADE_ALPHA,
               ground_y: float | None = None) -> Any:
    """The glow as float alpha (H x W, float32): `alpha` over each of the marker's lines grown by BAND_PAD, then a
    smoothstep to 0 over glow_fall px (band_fall, longer for a deep glow on a bright `ground_y`) past the nearest
    line."""
    return _shade_mask((width, height), boxes, None, alpha, pad=BAND_PAD,
                       fall=glow_fall(width, height, alpha, ground_y), lines=True, as_float=True)


def glow_mask(boxes: list, width: int, height: int, alpha: float = SHADE_ALPHA) -> Image.Image:
    """glow_alpha as an L image (0..255), for display and the tests; render() uses the float alpha."""
    import numpy as np
    return Image.fromarray(np.round(255 * glow_alpha(boxes, width, height, alpha)).astype(np.uint8), "L")


def photo_extent(which: str, pos: float, width: int, height: int) -> tuple[int, int, int, int]:
    """Where the old (THEN) or recent (NOW) photo shows with the divider at `pos` (compose): THEN before it, NOW
    after it."""
    p = round(max(0, min(max(width, height), pos)))
    if width > height:
        return (0, 0, p, height) if which == "then" else (p, 0, width, height)
    return (0, 0, width, p) if which == "then" else (0, p, width, height)


def clip_to_photo(mask: Any, which: str, pos: float) -> Any:
    """`mask` (an L image or a float array) kept to the part of the screen `which`'s photo shows at divider `pos`
    (nothing elsewhere)."""
    import numpy as np
    if isinstance(mask, Image.Image):
        x0, y0, x1, y1 = photo_extent(which, pos, mask.width, mask.height)
        out = Image.new("L", mask.size, 0)
        if x1 > x0 and y1 > y0:
            out.paste(mask.crop((x0, y0, x1, y1)), (x0, y0))
        return out
    h, w = mask.shape
    x0, y0, x1, y1 = photo_extent(which, pos, w, h)
    out = np.zeros_like(mask)
    if x1 > x0 and y1 > y0:
        out[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
    return out


def band_mask(which: str, boxes: list, width: int, height: int, alpha: float = SHADE_ALPHA,
              pos: float | None = None, as_float: bool = False, ground_y: float | None = None) -> Any:
    """The marker's shade: its glow, kept to the part of the screen its own photo shows with the divider at `pos`
    (default the split, where the clip rests). An L image, or (`as_float`) the float alpha render() uses."""
    pos = _split_at(width, height) if pos is None else pos
    if as_float:
        return clip_to_photo(glow_alpha(boxes, width, height, alpha, ground_y), which, pos)
    return clip_to_photo(glow_mask(boxes, width, height, alpha), which, pos)


def _union(masks: list) -> Any:
    """The deepest of several (mask, alpha) shades at each pixel (L images, or float arrays: the deeper wins)."""
    import numpy as np
    from PIL import ImageChops
    out = None
    for m, a in masks:
        if isinstance(m, Image.Image):
            m = m if a >= 1 else m.point(lambda v, a=a: round(v * max(0.0, a)))
            out = m if out is None else ImageChops.lighter(out, m)
        else:
            m = m if a >= 1 else m * np.float32(max(0.0, a))
            out = m if out is None else np.maximum(out, m)
    return out


@lru_cache(maxsize=4)
def _glow_dither(width: int, height: int) -> Any:
    """A static TPDF dither (H x W x 1, float32): one value per 2 x 2 cell, the sum of two uniform draws scaled to
    a peak of GLOW_DITHER_LSB video codes (x 255 / 219 in RGB), upsampled nearest, from a fixed seed: the same at
    every frame and every render of a size, so the glow's ramp does not terrace and does not shimmer."""
    import numpy as np
    rng = np.random.default_rng(GLOW_DITHER_SEED)
    ch, cw = (height + 1) // 2, (width + 1) // 2
    cells = (rng.random((ch, cw), dtype=np.float32) + rng.random((ch, cw), dtype=np.float32) - 1.0)
    cells *= np.float32(GLOW_DITHER_LSB * 255.0 / 219.0)
    out = np.repeat(np.repeat(cells, 2, axis=0), 2, axis=1)[:height, :width, None]
    out = np.ascontiguousarray(out)
    out.setflags(write=False)
    return out


def _nonzero_box(a: Any) -> tuple[int, int, int, int] | None:
    import numpy as np
    rows, cols = np.flatnonzero(a.any(axis=1)), np.flatnonzero(a.any(axis=0))
    if rows.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _shade(im: Image.Image, mask: Any, alpha: float = 1.0, bbox: tuple[int, int, int, int] | None = None) -> Image.Image:
    """`im` under the glow `mask` (an L image, 0..255, or a float alpha array 0..1) at `alpha`: GLOW_INK
    composited in float, out = base (1 - a) + GLOW_INK a, dithered (_glow_dither) where 0 < a < 1, then rounded."""
    import numpy as np
    if alpha <= 0 or mask is None:
        return im
    rgb = im.convert("RGB")
    if isinstance(mask, Image.Image):
        bb = mask.getbbox()
    else:
        bb = bbox if bbox is not None else _nonzero_box(mask)
    if bb is None:
        return rgb
    x0, y0, x1, y1 = bb
    if isinstance(mask, Image.Image):
        a = np.asarray(mask.crop(bb), np.float32)[..., None] * np.float32(min(1.0, alpha) / 255.0)
    else:
        a = np.asarray(mask[y0:y1, x0:x1], np.float32)[..., None] * np.float32(min(1.0, alpha))
    base = np.asarray(rgb.crop(bb), np.float32)
    out = base * (1 - a) + np.asarray(GLOW_INK, np.float32) * a
    out += _glow_dither(*im.size)[y0:y1, x0:x1] * ((a > 0) & (a < 1))
    rgb.paste(Image.fromarray(np.clip(np.round(out), 0, 255).astype(np.uint8), "RGB"), (x0, y0))
    return rgb


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
    grown by PAPER_PAD px, kept to each clip box; its dark share under the lines (grown by PAPER_DARK_PAD px). (ok,
    the measured stats per frame; "dark" is the share the gate reads, "dark_padded" the PAPER_PAD box's, a record.)"""
    def grown(pad: int, clip: tuple) -> tuple:
        return (max(min(b[0] for b in boxes) - pad, clip[0]), max(min(b[1] for b in boxes) - pad, clip[1]),
                min(max(b[2] for b in boxes) + pad, clip[2]), min(max(b[3] for b in boxes) + pad, clip[3]))
    ok, out = True, []
    for im, clip in frames:
        st = paper_stats(im, grown(PAPER_PAD, clip))
        if st is not None:
            under = paper_stats(im, grown(PAPER_DARK_PAD, clip))
            st["dark_padded"] = st["dark"]
            st["dark"] = under["dark"] if under is not None else 1.0
        out.append(st)
        if (st is None or st["mean"] < PAPER_MEAN or st["p10"] < PAPER_P10 or st["edge"] > PAPER_EDGE
                or st["dark"] > PAPER_DARK):
            ok = False
    return ok, out


def on_paper(boxes: list, frames: list) -> bool:
    return paper_gate(boxes, frames)[0]


# Freeze panel 2026-11-04 (T5): each marker's kicker and year are one group. The group is drawn only while it is
# clear: its lines (each padded by its own halo spread) wholly inside its photo and at least DIVIDER_W / 2 + 2 px
# from the divider. It fades out over GROUP_RAMP frames before it would be cut and back in over GROUP_RAMP frames
# after, so the divider never slices a label and nothing blinks.
GROUP_RAMP = 6


def group_clear(box: tuple[int, int, int, int], which: str, pos: float, width: int, height: int) -> bool:
    """Is the marker group `box` wholly inside its photo at divider `pos`, and clear of the divider?"""
    x0, y0, x1, y1 = photo_extent(which, pos, width, height)
    bx0, by0 = max(0, box[0]), max(0, box[1])
    bx1, by1 = min(width, box[2]), min(height, box[3])
    if not (x1 > x0 and y1 > y0 and bx0 >= x0 and by0 >= y0 and bx1 <= x1 and by1 <= y1):
        return False
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    p = round(max(0, min(edge, pos)))
    if not 0 < p < edge:
        return True
    lo, hi = (bx0, bx1) if axis == 0 else (by0, by1)
    gap = (p - hi) if which == "then" else (lo - p)
    return gap >= DIVIDER_W / 2 + 2


def group_alpha(clear: list) -> list:
    """Per frame: 0 where not clear, a smoothstep over the GROUP_RAMP frames either side of a not-clear run, else 1."""
    n = len(clear)
    far = float("inf")
    dist = [far] * n
    last = None
    for i in range(n):
        if not clear[i]:
            last = i
        if last is not None:
            dist[i] = i - last
    last = None
    for i in range(n - 1, -1, -1):
        if not clear[i]:
            last = i
        if last is not None:
            dist[i] = min(dist[i], last - i)
    return [1.0 if d > GROUP_RAMP else _ease_io(d / (GROUP_RAMP + 1)) for d in dist]


# Freeze panel 2026-11-04 (T10): on 9:16 both markers take one side, left or right, whichever puts less of the
# photos' structure under them (the summed Canny edge density under THEN's and NOW's padded groups, split frame,
# unshaded); the right only when it is at least SIDE_MARGIN lower.
SIDE_MARGIN = 0.15

# What the last render() did (type modes, halo, glow steps, side, framing); also written into the contrast sidecar.
last_meta: dict[str, Any] = {}


def _encode(frames: Any, out: Path, n: int, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """Raw RGB frames piped to ffmpeg -> H.264 MP4 (libx264 slow, CRF 16, grain tune, BT.709 limited range,
    tagged) + poster JPEG of the last frame (freeze panel 2026-11-04, T3: the glow's dithered fall survives)."""
    import subprocess
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
           "-r", str(VIDEO_FPS), "-i", "-", "-frames:v", str(n),
           # ffmpeg 6+ takes the colour tags from the frames, so setparams marks them (the -color_* flags alone
           # left primaries and transfer "unknown" in the file).
           "-vf", ("scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                   "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"),
           "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-tune", "grain",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-pix_fmt", "yuv420p",
           str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    last = None
    try:
        for frame in frames:
            last = frame
            proc.stdin.write(frame.convert("RGB").tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait(timeout=600) != 0:
            raise RuntimeError(f"ffmpeg failed encoding {out.name}: {err[-500:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    if last is not None:
        last.convert("RGB").save(out.with_suffix(".jpg"), quality=90)
    return out


def render(old: Path, old_year: int, new: Path, new_year: int, out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Path:
    """The old photo, the recent one wiped across it, then both side by side to the end, each with its year; both
    photos under one slow push (push_at)."""
    import json
    size = (width, height)
    a_src, b_src, framing = frame_pair(old, new, width, height)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    key = "landscape" if width > height else "portrait"
    kicker_font = get_font(DATA_FONT, KICKER_PX[key], weight=600)
    year_font = get_font("Anton", YEAR_PX[key])
    track = KICKER_PX[key] * KICKER_TRACK[key]
    split = _split_at(width, height)
    edge = max(width, height)
    srcs = {"then": a_src, "now": b_src}
    years = {"then": old_year, "now": new_year}
    words = {w: kicker_word(w, years[w]) for w in years}
    # Each marker is planned where it rests (the split, at the end of the push) and solved again on the whole photo
    # it also sits on (THEN alone at the start; NOW whole at the start of the hold, the sweep's far end).
    t_far = WIPE["then"] + WIPE["to_far"]
    split0 = compose(pushed(a_src, CLIP_SECONDS, size), pushed(b_src, CLIP_SECONDS, size), split)
    alone0 = {"then": compose(pushed(a_src, 0.0, size), pushed(b_src, 0.0, size), edge),
              "now": compose(pushed(a_src, t_far, size), pushed(b_src, t_far, size), 0)}
    at = {"then": edge, "now": 0}

    def ink_boxes(which: str, ln: tuple) -> list:
        kxy, fxy, anchor = ln
        return [TextPlate(text, font, WHITE, xy, anchor, tr, 0, INK, 0, False, size).box
                for text, font, xy, tr in ((words[which], kicker_font, kxy, track), (str(years[which]), year_font, fxy, 0))]

    def padded(boxes: list, pad: int, clip: tuple) -> tuple:
        return (max(clip[0], min(b[0] for b in boxes) - pad), max(clip[1], min(b[1] for b in boxes) - pad),
                min(clip[2], max(b[2] for b in boxes) + pad), min(clip[3], max(b[3] for b in boxes) + pad))

    side, side_meta = "left", None
    if key == "portrait":
        sums = {}
        for s in ("left", "right"):
            total = 0.0
            for w in ("then", "now"):
                st = paper_stats(split0, padded(ink_boxes(w, marker_lines(w, years[w], width, height, words[w], s)),
                                                PAPER_PAD, panel_box(w, width, height)))
                total += st["edge"] if st else 0.0
            sums[s] = round(total, 4)
        if sums["left"] > 0 and sums["right"] <= (1 - SIDE_MARGIN) * sums["left"]:
            side = "right"
        side_meta = {"side": side, "edge_sum": sums}
    lines = {w: marker_lines(w, years[w], width, height, words[w], side) for w in years}
    gate = {}
    for w in years:
        ok, st = paper_gate(ink_boxes(w, lines[w]), [(split0, panel_box(w, width, height)), (alone0[w], (0, 0, width, height))])
        gate[w] = {"ok": ok, "split": st[0], "alone": st[1]}

    def drawn(ps: list, im: Image.Image) -> Image.Image:
        for p in ps:
            im = p.draw(im)
        return im

    def short(ps: list, split_im: Image.Image, alone_im: Image.Image) -> bool:
        """Is any marker line under PLACE_TARGET as drawn with its marker's other line (measured on the delivered
        pixels, on the split and on the marker's alone frame)?"""
        on_split, on_alone = drawn(ps, split_im), drawn(ps, alone_im)
        return any(min(p.contrast or 0, p.measure(on_split) or 0, p.measure(on_alone) or 0) < PLACE_TARGET
                   for p in ps)   # review round 2 (T2b): the planned ratio too, not only the drawn ones

    plates: dict[str, list] = {}
    mode: dict[str, str] = {}
    for w in ("then", "now"):
        if not (INK_ON_PAPER and gate[w]["ok"]):
            continue
        kxy, fxy, anchor = lines[w]
        ps = [_marker_plate(split0, alone0[w], kxy, words[w], kicker_font, INK, anchor, track, halo_px=0, min_halo=0.0),
              _marker_plate(split0, alone0[w], fxy, str(years[w]), year_font, INK, anchor, 0, halo_px=0, min_halo=0.0)]
        if short(ps, split0, alone0[w]) or min(p.contrast or 0 for p in ps) < INK_TARGET:
            log.info("Then-and-now: ink type on paper plans %s for %s (under %.1f); bone/gold type instead.",
                     "/".join(f"{p.contrast or 0:.2f}" for p in ps), w, INK_TARGET)
            gate[w]["ink_short"] = [round(p.contrast or 0, 2) for p in ps]
            continue
        plates[w], mode[w] = ps, "ink"
    rest = [w for w in ("then", "now") if w not in plates]
    shades: dict[str, Any] = {}          # each bone/gold marker's glow (float alpha, its whole photo)
    steps: dict[str, str] = {}
    depth: dict[str, float] = {}
    colours: dict[str, Any] = {}
    pair_colour: list[Any] = [WHITE if len(rest) == 1 and plates else None]

    def split_shaded() -> Image.Image:
        if not shades:
            return split0
        return _shade(split0, _union([(clip_to_photo(m, w, split), 1.0) for w, m in shades.items()]))

    def alone_shaded(which: str) -> Image.Image:
        if not shades:
            return alone0[which]
        return _shade(alone0[which], _union([(clip_to_photo(m, w, at[which]), 1.0) for w, m in shades.items()]))

    def plan_marker(which: str, split_im: Image.Image, alone_im: Image.Image, max_halo: float) -> list:
        kxy, fxy, anchor = lines[which]
        # Gold, unless a bright sky keeps it under 4.5:1 at a full halo: then bone, which clears it there.
        for colour in ((pair_colour[0],) if pair_colour[0] is not None else (GOLD, WHITE)):
            kicker = _marker_plate(split_im, alone_im, kxy, words[which], kicker_font, colour, anchor, track,
                                   halo_px=KICKER_HALO, min_halo=0.5, target=PLACE_TARGET + 0.6, max_halo=max_halo)
            colours[which] = colour
            if (kicker.contrast or 0) >= PLACE_TARGET and (kicker.measure(alone_im) or 0) >= PLACE_TARGET:
                break
        figure = _marker_plate(split_im, alone_im, fxy, str(years[which]), year_font, WHITE, anchor, 0, halo_px=None,
                               min_halo=0.35, max_halo=max_halo)
        return [kicker, figure]

    def halo_cap(which: str) -> float:
        """The halo's cap while a glow is used: GLOW_HALO_CAP, or 1.0 on a ground whose local mean luma is at least
        CLOUD_Y, where a glow reads as a grey cloud and the tight halo is the lesser darkening (review round 3)."""
        return 1.0 if (gate[which]["split"] or {}).get("mean", 0.0) >= CLOUD_Y else GLOW_HALO_CAP

    def plan_all() -> dict[str, list]:
        shades.clear()
        steps.clear()
        depth.clear()
        ps = {w: plan_marker(w, split0, alone0[w], 1.0) for w in rest}
        if not any(short(ps[w], split0, alone0[w]) for w in rest):
            steps.update({w: "halo" for w in rest})
            return ps
        # A glow is needed: the pair's halo is capped at GLOW_HALO_CAP (1.0 on a bright ground, review round 3) and
        # the shortfall made up in order.
        for w in rest:
            sp, al = split_shaded(), alone_shaded(w)
            cap_w = halo_cap(w)
            p = plan_marker(w, sp, al, cap_w)
            if not short(p, sp, al):
                ps[w], steps[w] = p, f"halo<={cap_w:.2f}"
                continue
            boxes = [q.box for q in p]
            mean_y = (gate[w]["split"] or {}).get("mean", 0.0)
            capped = [d for d in BAND_STEPS if d <= GLOW_BRIGHT_CAP + 1e-9] if mean_y >= GLOW_BRIGHT_Y else list(BAND_STEPS)
            ladder = ([(d, cap_w, "glow" if cap_w < 1.0 else "glow (halo 1.0 first)") for d in capped]
                      + ([(capped[-1], 1.0, "glow+halo<=1.0")] if cap_w < 1.0 else [])
                      + [(d, 1.0, "deep glow") for d in BAND_STEPS if d > capped[-1]])
            for d, mh, step in ladder:
                if depth.get(w) != d:
                    shades[w] = band_mask(w, boxes, width, height, d, pos=at[w], as_float=True, ground_y=mean_y)
                sp, al = split_shaded(), alone_shaded(w)
                p = plan_marker(w, sp, al, mh)
                steps[w], depth[w] = f"{step} {d:.2f}", d
                if not short(p, sp, al):
                    break
            ps[w] = p
        return ps

    if rest:
        planned = plan_all()
        if len(rest) == 2 and len({colours[w] for w in rest}) > 1:   # one gold, one bone: both bone
            pair_colour[0] = WHITE
            planned = plan_all()
        # One halo for the pair (T2): the largest any bone/gold line needed, at least PAIR_HALO_FLOOR.
        halo_h = max(p.halo for w in rest for p in planned[w])
        if halo_h > 0:
            halo_h = max(halo_h, PAIR_HALO_FLOOR)
        # Review round 2 (T2b): the pair's one halo can leave a line short that its own plan held (Odessa 9:16
        # ink off: NOW's kicker at 4.78). Then the shortfall is made up in T2's order, with the halo kept one for
        # the pair: the halo up to its cap (GLOW_HALO_CAP while a glow is used), the short marker's glow a step
        # deeper up to the bright-ground cap, the halo up to 1.0, then the deeper BAND_STEPS.
        def resolve_pair() -> list:
            sp = split_shaded()
            bad = []
            for w in rest:
                al = alone_shaded(w)
                for p in planned[w]:
                    p.solve(al, PLACE_TARGET, min_halo=halo_h, max_halo=halo_h)
                    p.solve(sp, PLACE_TARGET, min_halo=halo_h, max_halo=halo_h)
                if short(planned[w], sp, al):
                    bad.append(w)
            return bad

        def deepen(w: str, cap: float, step: str) -> bool:
            nxt = [d for d in BAND_STEPS if d > (depth.get(w) or 0.0) + 1e-9 and d <= cap + 1e-9]
            if not nxt:
                return False
            shades[w] = band_mask(w, [q.box for q in planned[w]], width, height, nxt[0], pos=at[w], as_float=True,
                                  ground_y=(gate[w]["split"] or {}).get("mean"))
            depth[w], steps[w] = nxt[0], f"{step} {nxt[0]:.2f} (pair)"
            return True

        bad = resolve_pair()
        while bad:
            hcap = min(halo_cap(w) for w in shades) if shades else 1.0
            if halo_h < hcap - 1e-9:
                halo_h = min(hcap, round(halo_h + 0.05, 4))
            elif not any([deepen(w, GLOW_BRIGHT_CAP if (gate[w]["split"] or {}).get("mean", 0.0) >= GLOW_BRIGHT_Y
                                 else 1.0, "glow") for w in bad]):
                if halo_h < 1.0 - 1e-9:
                    halo_h = min(1.0, round(halo_h + 0.05, 4))
                    for w in bad:
                        steps[w] = f"glow+halo<=1.0 {depth.get(w) or 0.0:.2f} (pair)"
                elif not any([deepen(w, 1.0, "deep glow") for w in bad]):
                    log.info("Then-and-now: %s short at the pair's halo %.2f and the deepest glow.", bad, halo_h)
                    break
            bad = resolve_pair()
        for w in rest:
            plates[w], mode[w] = planned[w], ("gold" if colours[w] == GOLD else "bone")
    else:
        halo_h = 0.0
    log.info("Then-and-now type: THEN %s, NOW %s (%s); halo %.2f; %s.", mode["then"], mode["now"],
             "/".join(words[w] for w in ("then", "now")), halo_h,
             ", ".join(f"{w} {steps[w]}" for w in rest) or "no bone/gold marker")
    LAST_PAPER.clear()
    LAST_PAPER.update({w: {**gate[w], "mode": "ink" if mode[w] == "ink" else "bone/gold"} for w in years})

    # The group schedule (T5): G is each marker's ink boxes padded by its lines' own halo spreads.
    spread = {w: (0, 0) if mode[w] == "ink" else (KICKER_HALO, _year_halo_px(year_font)) for w in years}
    groups = {w: (min(p.box[0] - s for p, s in zip(plates[w], spread[w])), min(p.box[1] - s for p, s in zip(plates[w], spread[w])),
                  max(p.box[2] + s for p, s in zip(plates[w], spread[w])), max(p.box[3] + s for p, s in zip(plates[w], spread[w])))
              for w in years}
    positions = [divider_at(i / VIDEO_FPS, width, height) for i in range(n)]
    sched = {w: group_alpha([group_clear(groups[w], w, positions[i], width, height) for i in range(n)]) for w in years}
    for i in range(n):
        sched["then"][i] *= _ease((i / VIDEO_FPS - 0.1) / 0.3)

    meta = {"type_mode": dict(mode), "kicker": dict(words), "halo": round(halo_h, 2), "glow_step": dict(steps),
            "glow_depth": {w: depth.get(w) for w in years},
            "glow_fall": {w: glow_fall(width, height, depth[w], (gate[w]["split"] or {}).get("mean")) if depth.get(w)
                          else None for w in years},
            "paper_gate": gate, "side": side_meta,
            "framing": framing, "groups": {w: list(groups[w]) for w in years},
            "planned_contrast": {f"{w}_{part}": round(p.contrast or 0, 2) for w in ("then", "now")
                                 for part, p in zip(("kicker", "year"), plates[w])}}
    last_meta.clear()
    last_meta.update(meta)
    last_meta["schedule"] = {w: [round(v, 4) for v in sched[w]] for w in years}
    final: dict[str, Image.Image] = {}

    def layer(which: str, t: float, g: float) -> Image.Image:
        """One photo at this frame's push with its own glow and labels at the group alpha `g`."""
        im = pushed(srcs[which], t, size)
        if g <= 0:
            return im
        if which in shades:
            im = _shade(im, shades[which], g)
        for p in plates[which]:
            im = p.draw(im, g)
        return im

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            pos = positions[i]
            dpos = pos - positions[i - 1] if i else 0.0
            p = round(max(0, min(edge, pos)))
            if MOTION_BLUR and abs(dpos) > BLUR_MIN_STEP:
                im = join_blurred(layer("then", t, sched["then"][i]), layer("now", t, sched["now"][i]), pos, dpos)
            else:
                im = layer("now", t, sched["now"][i]) if p < edge else pushed(b_src, t, size)
                if p > 0:
                    box = (0, 0, p, height) if width > height else (0, 0, width, p)
                    im.paste(layer("then", t, sched["then"][i]).crop(box), (0, 0))
                im = draw_divider(im, pos)
            final["frame"] = im
            yield im

    clip = _encode(frames(), out, n, width=width, height=height)
    if "frame" in final:
        record_contrast(clip, final["frame"], [(f"{w}_{part}", p) for w in ("then", "now")
                                               for part, p in zip(("kicker", "year"), plates[w])])
        side_car = Path(str(clip) + ".contrast.json")
        try:
            if side_car.exists():
                rec = json.loads(side_car.read_text(encoding="utf-8"))
                rec["meta"] = meta
                side_car.write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str), encoding="utf-8",
                                    newline="\n")
        except (OSError, ValueError) as e:      # the record is evidence, never a reason to lose the clip
            log.info("Then-and-now: contrast sidecar meta not written (%s).", e)
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
