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

from . import brand, commons, config, safe_zones, url_guard
from .brand import PLACE_TARGET, rgba
from .camera_motion import kenburns
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .credits import normalize
from .encoding import X264_THREADS
from .graphics import (
    CLIP_SECONDS,
    TEXT_TOP,
    _ease_io,
    get_text_box,
    record_contrast,
)
from .llm import LLMError, image_block
from .llm import task as llm_task
from .typography import TextPlate, _draw_tracked, cap_height, full_halo_pad, get_font, plan_text
from .upscale import ensure_resolution

log = logging.getLogger(__name__)

# Colours are the active brand's (src/brand.py), read when drawn (QF-35a); the names this module bound at import
# before are served to their readers by __getattr__ (WHITE is BONE).
_BRAND_NAMES = {"BONE": "BONE", "INK": "INK", "GOLD": "GOLD", "WHITE": "BONE", "DATA_FONT": "DATA_FONT",
                "GLOW_INK": "GLOW_INK"}


def __getattr__(name: str):
    """A colour or face this module bound at import before QF-35a: the active brand's, read now; and the measures
    taken in a brand face (KICKER_PX, BAND_FALL), measured in the active brand's face when read (QF-35a round 2)."""
    if name in _BRAND_NAMES:
        return getattr(brand, _BRAND_NAMES[name])
    if name == "KICKER_PX":
        return kicker_px()
    if name == "BAND_FALL":
        return band_fall(1080, 1920)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
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
    op, np_ = work_dir / f"{Path(old).stem}_reg.png", work_dir / f"{Path(new).stem}_reg.png"
    old_crop = a[y0:y1, x0:x1]
    new_crop, gamma = match_exposure(old_crop, warped[y0:y1, x0:x1])
    Image.fromarray(old_crop).save(op, "PNG")
    Image.fromarray(new_crop).save(np_, "PNG")
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
            path = seen_recent.get(new["title"]) or commons.stored(work_dir / f"now_{k}_{j}.png")
            try:
                if not path.exists():
                    commons.download_jpeg(new["download_url"], path)
                    path = commons.stored(path)   # the kept source JPEG, or the decoded PNG (QF-13)
                seen_recent[new["title"]] = path
                out.append((path, new))
            except (requests.RequestException, OSError, url_guard.RefusedURL) as e:
                log.info("Recent photo skipped: %s", e)
        return out

    for k, old in enumerate(olds[:4]):  # one vision call per old photo, against recent photos of its subject
        op = commons.stored(work_dir / f"then_{k}.png")
        recents = recent_for(old, k)
        if not recents:
            continue
        try:
            if not op.exists():
                commons.download_jpeg(old["download_url"], op)
                op = commons.stored(op)   # the kept source JPEG, or the decoded PNG (QF-13)
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
            LAST_REFUSAL.append({"old": k + 1, "reason": "not registered: the pair does not share a viewpoint "
                                 "(shares_viewpoint / register); an unregistered pair is not a then-and-now"})
            continue
        log.info("Then-and-now: no honest match for old photo %d: %s", k + 1, pick.note[:160])
        LAST_REFUSAL.append({"old": k + 1, "reason": f"no honest match: {pick.note[:160]}"})
    return None


# Review round 1 (lander ruling 2026-11-27): production draws REGISTERED pairs only; source() refuses every other
# pairing and says why, per old photograph (LAST_REFUSAL, written beside the run by make() as REFUSAL_SIDECAR).
LAST_REFUSAL: list[dict[str, Any]] = []
REFUSAL_SIDECAR = "graphic_thennow.refused.json"


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
# Cycle 0 round 8 (official panel 1 consensus 7, BC-4 / CD-12 / TY-A-7: the one surface family untouched since round
# 1): THEN / NOW is the SYSTEM kicker (brand_system KICKER: the text face at kicker.weight, tracked kicker.track em, in
# kicker.colour, led by its flat rule one cap long at mid-cap, kicker.rule_gap caps from the words) at the ladder's
# kicker size per canvas; the year is a stated quantity (operator ruling 3: the figure face in figure.colour, gold);
# both are one overlay label planned by the ONE rule (typography.plan_label, operator ruling 2): the label halo (one
# spread, <= 0.16), INK type where the ground is paper, else a held INK plate. The halo boxes and glows are retired.
YEAR_PX = {"portrait": 128, "landscape": 112}
KICKER_GAP_WIDE = 24


def kicker_px() -> dict[str, int]:
    """The kicker's size per orientation: the system kicker's ladder size (brand_system TYPE "kicker") on the tall and
    wide canvases."""
    from .brand_system import type_px
    return {"portrait": round(type_px("kicker", "tall")), "landscape": round(type_px("kicker", "wide"))}


def kicker_font(key: str) -> Any:
    """The THEN / NOW kicker's font: the system kicker (text face, kicker.weight) at kicker_px()[key]."""
    from .brand_system import FACE, tok
    return get_font(brand.active().font(FACE["kicker"]), kicker_px()[key], weight=tok("kicker.weight"))


def kicker_track(key: str) -> float:
    from .brand_system import tok
    return tok("kicker.track") * kicker_px()[key]


def kicker_lead(font: Any) -> tuple[int, int, int]:
    """The kicker's lead rule on `font`: (length, gap to the words, thickness) px, the system kicker's tokens."""
    from .brand_system import tok
    ch = cap_height(font)
    return (max(8, round(ch * tok("kicker.rule"))), round(ch * tok("kicker.rule_gap")),
            max(3, round(ch * tok("kicker.rule_thick"))))


def year_font(key: str) -> Any:
    """The year's font: a stated quantity, the figure face (operator ruling 3) at YEAR_PX[key]."""
    from .graphics import figure_font
    return figure_font(YEAR_PX[key])


# The kicker sits on the year as one lock-up: on 9:16 the year's anchor YEAR_OFFSET kicker sizes below the
# kicker's. Left-set, the kicker's lead rule and the year's first ink start on the marker's column; right-set, the
# kicker's last ink and the year's end on the marker's x (optical alignment measured from the drawn glyphs). On 9:16
# the NOW kicker's cap top sits NOW_DROP px below the divider.
YEAR_OFFSET = 1.30
NOW_DROP = 96
SPLIT_TALL = 0.4                    # 9:16: the divider's resting y as a share of the height (768 at 1920)
# Freeze panel 2026-11-04 (T8, fact safety): the recent photo's kicker reads NOW only for a photo at most
# NOW_YEARS years old (UTC); an older "recent" photo reads LATER. The years drawn are always the ones passed in.
NOW_YEARS = 25
# One push, identical on both photos (the same scale and centre), so registration holds and the split hold is not a
# still frame. Freeze panel 2026-11-04 (T4): linear, 1 -> 1 + PUSH over the clip, so it moves from the first frame.
PUSH = 0.04
DIVIDER_W = 5
# Freeze cycle 6 (THENNOW T1; panel 7 MO-T1 / MO-TN-REVERSE, lander ruling 2026-11-27: MO2 8.0 on all 12 cases): ONE
# wipe. The old photo holds alone, then the divider travels once from the far edge straight to the resting split on
# the system's ease_in_out (a field moving between rests), monotonic: no frame where it moves back. It replaced
# WIPE {then 1.2, to_far 1.0, hold_far 0.25, settle 0.85} and SWEEP_TO 0.8, which ran the divider past the split to a
# far point, held, and brought it back (the THEN marker faded out for 1.4-2 s while the divider stood over it).
# Every value is read from the system's tokens and reading-time rules, never a local copy:
#   - the THEN marker enters over tok("motion.reveal") on ease_out (an element arriving);
#   - the old photo then holds alone until its marker has been read twice: the dwell is reveal + read_seconds(the
#     marker's text) (MO1: "long enough to read twice at <= 17 cps shown", with the composer's own reading need,
#     config.CAPTION_READ_PER_CHAR / CAPTION_READ_BASE, as the floor);
#   - the wipe takes wipe_seconds: the smoothstep's peak (1.5 x its distance / its frames) at most
#     tok("motion.wipe_step") px a frame;
#   - the NOW marker enters as the divider lands (group_alpha's fade), and the split then holds to the clip's end,
#     which must leave both markers' text its reading time (hold_seconds; recorded in the clip's meta).


def read_seconds(text: str, cps: float | None = None) -> float:
    """Seconds `text` needs on screen: twice its characters at tok("motion.caption_cps") (MO1: read twice), never
    under the composer's own reading need for it (config.CAPTION_READ_PER_CHAR a character + CAPTION_READ_BASE)."""
    from .brand_system import tok
    n = len(text)
    cps = float(tok("motion.caption_cps")) if cps is None else float(cps)
    return max(2.0 * n / cps, n * config.CAPTION_READ_PER_CHAR + config.CAPTION_READ_BASE)


# Review round 1 (D6, lander ruling 2026-11-27): the split's hold is never under the reading need of both markers, as a
# gate at configuration (BrandConfig refuses a brand whose motion tokens break it), not a log line. The worst case is
# the longest pair of markers the clip draws ("THEN 1876 LATER 1999": four-figure years, the longer kicker) on the
# format with the longer wipe (9:16: 1152 px from the foot to the split).
WORST_THEN, WORST_BOTH = "THEN 0000", "THEN 0000 LATER 0000"


def timing(reveal: float, step: float, cps: float, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
           then: str = WORST_THEN, both: str = WORST_BOTH) -> dict[str, float]:
    """The clip's timing for these motion values: the old photo's dwell (its marker's entry and reading time, whole
    frames), the wipe (whole frames at a smoothstep peak of `step` px a frame at 1080), the NOW marker's entry (the
    same reveal, after the divider lands) and the split's hold after it, against both markers' reading time."""
    import math
    dwell = math.ceil((reveal + read_seconds(then, cps)) * VIDEO_FPS - 1e-9) / VIDEO_FPS
    axis = 0 if width > height else 1
    dist = abs((width, height)[axis] - _split_at(width, height))
    wipe = math.ceil(1.5 * dist / (step * min(width, height) / 1080.0) - 1e-9) / VIDEO_FPS
    hold = CLIP_SECONDS - dwell - wipe - reveal
    return {"dwell": dwell, "wipe": wipe, "land": dwell + wipe, "now_in": dwell + wipe + reveal, "hold": hold,
            "need": read_seconds(both, cps)}


def timing_problems(system: Any) -> list[str]:
    """The then-and-now clip's reading rule broken by a brand's motion tokens (`system`: its token values), on both
    formats: [] when the split holds both markers for their reading time."""
    from .brand_system import GATES
    cps = float(GATES["motion.caption_cps"])
    out = []
    for w, h in ((1080, 1920), (1920, 1080)):
        t = timing(float(system["motion.reveal"]), float(system["motion.wipe_step"]), cps, w, h)
        if t["hold"] + 1e-9 < t["need"]:
            out.append(f"then-and-now {w}x{h}: the split holds {t['hold']:.2f} s after the NOW marker's entry, under "
                       f"the {t['need']:.2f} s both markers need (motion.reveal {system['motion.reveal']}, "
                       f"motion.wipe_step {system['motion.wipe_step']})")
    return out


def then_text(year: int) -> str:
    """The THEN marker's text as read: its kicker and its year."""
    return f"{kicker_word('then', year)} {year}"


def _timing_now(width: int, height: int, then: str = WORST_THEN, both: str = WORST_BOTH) -> dict[str, float]:
    from .brand_system import tok
    return timing(float(tok("motion.reveal")), float(tok("motion.wipe_step")), float(tok("motion.caption_cps")),
                  width, height, then, both)


def then_dwell(text: str = "THEN 0000") -> float:
    """Seconds the old photo holds alone: its marker's entry (motion.reveal) and then its reading time, rounded up
    to the frame. The default is a four-figure year's marker."""
    return _timing_now(VIDEO_WIDTH, VIDEO_HEIGHT, then=text)["dwell"]


def wipe_seconds(width: int, height: int) -> float:
    """The wipe's length on this format: the least whole number of frames over which the smoothstep from the far
    edge to the split steps at most tok("motion.wipe_step") px a frame (scaled to the canvas's short side)."""
    return _timing_now(width, height)["wipe"]


def wipe_window(width: int, height: int, text: str = "THEN 0000") -> tuple[float, float]:
    """(start, end) seconds of the one wipe: it starts when the THEN marker has been read (then_dwell(text))."""
    t0 = then_dwell(text)
    return t0, t0 + wipe_seconds(width, height)


def hold_seconds(width: int, height: int, text: str = "THEN 0000") -> float:
    """Seconds the split holds whole after the NOW marker's entry (motion.reveal, from the divider's landing)."""
    from .brand_system import tok
    return CLIP_SECONDS - wipe_window(width, height, text)[1] - float(tok("motion.reveal"))


def _utc_year() -> int:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).year


def kicker_word(which: str, year: int) -> str:
    """THEN for the old photo; NOW for a recent one at most NOW_YEARS old (UTC year), else LATER."""
    if which == "then":
        return "THEN"
    return "NOW" if int(year) >= _utc_year() - NOW_YEARS else "LATER"


def _split_at(width: int, height: int) -> int:
    """The divider's resting place: x 960 on 16:9; on 9:16 SPLIT_TALL of the frame height (y 768 at 1920, the
    reviewed split). QF-24: it was the middle of the ads Reel box (288-1248), which is 0.4 of the height; the organic
    box's middle (829) would put the NOW marker under the graphics foot (1090), and the graphics box's middle (689,
    QF-24 review 1) set the NOW kicker on the old photo's subject and cut 79 px of it. At 768 the NOW year's foot is
    1038, inside the graphics box, and the markers sit on the graphics box (marker_xy)."""
    if width > height:
        return width // 2
    return round(height * SPLIT_TALL)


def wipe_progress(t: float, width: int, height: int, text: str = "THEN 0000") -> float:
    """The wipe's eased progress at `t`: 0 while the old photo holds alone, ease_in_out over the wipe, 1 after."""
    from .brand_system import ease_in_out
    t0, t1 = wipe_window(width, height, text)
    return ease_in_out((t - t0) / (t1 - t0))


def divider_at(t: float, width: int, height: int, text: str = "THEN 0000") -> float:
    """Where the divider is at `t`: the far edge (the old photo whole) until the THEN marker is read, then one
    ease_in_out travel straight to the resting split, which holds to the end. Monotonic by construction."""
    axis = 0 if width > height else 1
    edge = (width, height)[axis]
    split = _split_at(width, height)
    return edge + (split - edge) * wipe_progress(t, width, height, text)


def push_at(t: float) -> float:
    """The photos' shared zoom at `t`: 1 at the start, 1 + PUSH at the end, linear (equal steps every frame)."""
    return 1.0 + PUSH * min(1.0, max(0.0, t / CLIP_SECONDS))


def pushed(src: Image.Image, t: float, size: tuple[int, int]) -> Image.Image:
    """`src` (cover-cropped at (1 + PUSH) x the frame, so the push shows the photo's own detail) at the push's zoom
    for `t`, centred, at frame `size`: at zoom 1 the whole of `src`, at 1 + PUSH its centre 1 / (1 + PUSH)."""
    return kenburns(src, size, zoom=push_at(t), resample=Image.LANCZOS)   # the shared camera (QF-22)


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
    return _load_clean_subject(path)[0]


def _load_clean_subject(path: Path) -> tuple[Image.Image, Image.Image | None]:
    """(_load_clean's photo, its subject mask: an "L" image of the same size, 255 inside the photo system's subject
    box (hero_window.subject_box, read on the whole photo and moved through the caption-strip crop), or None when the
    photo system knows no subject). SYS-A-3."""
    from PIL import ImageDraw, ImageOps

    from .hero_window import subject_box
    from .photo_hygiene import without_caption_strip
    with Image.open(path) as im:
        whole = ImageOps.exif_transpose(im).convert("RGBA")
    clean, kept = without_caption_strip(whole)
    box = subject_box(whole, path)
    if box is None:
        return clean, None
    x0, y0, x1, y1 = box[0] - kept[0], box[1] - kept[1], box[2] - kept[0], box[3] - kept[1]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(clean.width, x1), min(clean.height, y1)
    if x1 <= x0 or y1 <= y0:
        return clean, None
    mask = Image.new("L", clean.size, 0)
    ImageDraw.Draw(mask).rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
    return clean, mask


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


def _cover_window(size: tuple[int, int], big: tuple[int, int], z: float = 1.0, v: float = 0.5,
                  u: float = 0.5) -> tuple[float, ...]:
    """The source box (of a photo `size`) a cover crop to `big` at zoom `z` takes, its centre at `u` of the photo's
    width (0.5: centred across; freeze cycle 3) and at `v` of its height (both clamped inside the photo)."""
    sw, sh = size
    s = max(big[0] / sw, big[1] / sh)
    ww, wh = big[0] / s / z, big[1] / s / z
    cy = min(max(v * sh, wh / 2), sh - wh / 2)
    cx = min(max(u * sw, ww / 2), sw - ww / 2)
    return (cx - ww / 2, cy - wh / 2, cx + ww / 2, cy + wh / 2)


# Freeze cycle 3 (lane A, A-S2-1: the Memnon 9:16 THEN pane was 55-70 % flat sky with both colossi sliced at the
# frame's edges): a landscape pair cover-cropped to 9:16 keeps under 40 % of its width, and centred across, a pair
# framed by two subjects keeps the gap between them. The shared window is also searched ACROSS (FRAME_U: the same
# horizontal place for both photos, so a registered pair stays registered; the subject box's centre first where the
# photo system knows one, hero_window.subject_box), and of the windows that pass, the one whose THEN pane holds its
# structure most in its middle columns (FRAME_MIDDLE) at the least zoom is taken: the subject in the pane, not cut by
# its edges. No topic, no photo list.
FRAME_U = (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.1, 0.9)
FRAME_MIDDLE = 0.6                  # the share of a pane's columns read as its middle
# Freeze cycle 6 (THENNOW T2, review round 1, lander ruling 2026-11-27): production draws only REGISTERED pairs (make()
# -> source() -> register(): one picture, both photographs at one size, one shared window, the divider across it). The
# window must keep that picture's central region: on each axis the photograph's central half (photo_fitness's
# centre_half), or, where the frame's aspect cannot hold half of that axis even at the least zoom, all of what the
# centred least-zoom window holds of it. A window the search (T7) would move or zoom past that is not taken: the
# centred window is kept and the reason recorded. (A registered 4:3 picture at 9:16 holds 0.42 of its width at the
# least zoom: its window stays centred and unzoomed.) An unregistered pair is not a then-and-now (production refuses
# it, source()); render() draws one as before, recorded "unregistered", for stand-in evidence only.
CENTRE_HALF = 0.25


def central_region(size: tuple[int, int], big: tuple[int, int]) -> tuple[float, float, float, float]:
    """The part of a picture `size` every shared window must keep in view at rest (shares of its width and height)."""
    rest = _rest_view(_cover_window(size, big), size)
    out = []
    for lo, hi in ((rest[0], rest[2]), (rest[1], rest[3])):
        out.append((lo, hi) if hi - lo < 2 * CENTRE_HALF else (CENTRE_HALF, 1 - CENTRE_HALF))
    return out[0][0], out[1][0], out[0][1], out[1][1]


def _rest_view(window: tuple[float, ...], size: tuple[int, int]) -> tuple[float, float, float, float]:
    """The part of the picture (shares) a source `window` shows at rest: its central 1 / (1 + PUSH) (pushed)."""
    x0, y0, x1, y1 = window
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 / (1 + PUSH), (y1 - y0) / 2 / (1 + PUSH)
    return (cx - hw) / size[0], (cy - hh) / size[1], (cx + hw) / size[0], (cy + hh) / size[1]


def keeps_centre(window: tuple[float, ...], size: tuple[int, int], big: tuple[int, int]) -> bool:
    """Does `window` keep the picture's central region (central_region) in view at rest?"""
    r, v = central_region(size, big), _rest_view(window, size)
    return v[0] <= r[0] + 1e-3 and v[1] <= r[1] + 1e-3 and v[2] >= r[2] - 1e-3 and v[3] >= r[3] - 1e-3


def _middle_density(pane: Any) -> float:
    """_edge_density over the middle FRAME_MIDDLE of `pane`'s columns."""
    w = pane.shape[1]
    a = int(w * (1 - FRAME_MIDDLE) / 2)
    return _edge_density(pane[:, a:w - a])


def _subject_top(src: Image.Image, big: tuple[int, int], u: float = 0.5) -> float | None:
    """The old photo's subject top as a share of its height: the first row of its 15-row smoothed row edge-energy
    profile (over the columns a centred cover crop to `big` keeps, each row read as its most structured
    SUBJECT_BLOCK px block) above SUBJECT_LEVEL of the profile's maximum."""
    import cv2
    import numpy as np
    x0, _, x1, _ = _cover_window(src.size, big, u=u)
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


def frame_pair(old: Path, new: Path, width: int, height: int, subjects: list | None = None
               ) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
    """Both photos cover-cropped at (1 + PUSH) x the frame, scan borders trimmed and (9:16) re-anchored on one
    shared window when the THEN pane would show nothing; and what was done (trims, zoom, subject row). `subjects`:
    a list the caller passes to receive each photo's subject mask (SYS-A-3) through the same trims and window as
    the photo (an "L" image at the returned photos' size, or None where no subject is known)."""
    import numpy as np
    from PIL import ImageOps
    big = (round(width * (1 + PUSH)), round(height * (1 + PUSH)))
    loaded = [_load_clean_subject(old), _load_clean_subject(new)]
    ims = [im for im, _ in loaded]
    masks = [m for _, m in loaded]

    def _out(a: Image.Image, b: Image.Image, geo) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
        if subjects is not None:
            subjects[:] = [None if m is None else geo(m) for m in masks]
        return a, b, info

    trims = [scan_trims(im) for im in ims]
    share = [max(t[k] / (im.width if k % 2 == 0 else im.height) for t, im in zip(trims, ims)) for k in range(4)]
    info: dict[str, Any] = {"trims_px": {"old": list(trims[0]), "new": list(trims[1])},
                            "trim_share": [round(v, 4) for v in share], "zoom": 1.0, "window": "centred"}
    if any(share):
        info["trimmed_px"] = {k: [round(share[0] * im.width), round(share[1] * im.height), round(share[2] * im.width),
                                  round(share[3] * im.height)] for k, im in zip(("old", "new"), ims)}
        def _trim(im: Image.Image) -> Image.Image:
            return im.crop((round(share[0] * im.width), round(share[1] * im.height),
                            im.width - round(share[2] * im.width), im.height - round(share[3] * im.height)))
        ims = [_trim(im) for im in ims]
        masks = [None if m is None else _trim(m) for m in masks]
    from .photo_grade import grade
    # Freeze cycle 5 (PHOTO-06, verified on the dev runs' Memnon and Pantheon pairs, which predate register()): the
    # split shows the THEN photo in one part of the frame and the NOW photo in the other, both through ONE window.
    # That compares only a REGISTERED pair (register() writes both photos at one size, the recent one warped onto the
    # old one; find_pair ships no other). Two photographs of different sizes are not registered: the same window
    # lands on different parts of them at different scales (Memnon 9:16: the head and torso above, the pedestal
    # below). No crop made here can register them (a crop per pane by salience was tried and measured: it cut the
    # Memnon THEN statue's head at 16:9 and showed a fountain beside the Pantheon), so the pair is drawn through the
    # shared window as before and RECORDED as not registered, with the reason; the remedy is a registered input.
    info["registered"] = ims[0].size == ims[1].size
    if not info["registered"]:
        info["unregistered"] = (f"then-and-now: the photographs are not registered ({ims[0].size[0]}x{ims[0].size[1]} "
                                f"and {ims[1].size[0]}x{ims[1].size[1]} px): one window does not show the same part "
                                "of both at one scale (rule: both panes are cut from one registered frame, "
                                "src/then_now.py register)")
        log.info("Then-and-now: the pair is not registered (%s and %s px); drawn through the shared window.",
                 ims[0].size, ims[1].size)
    # both photos graded as the master grades a bare photo, before anything is drawn on them (QF-86)
    srcs = [grade(ImageOps.fit(im, big, Image.LANCZOS)) for im in ims]

    def _fitted(m: Image.Image) -> Image.Image:
        return ImageOps.fit(m, big, Image.NEAREST)
    if width > height:
        return _out(srcs[0], srcs[1], _fitted)
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
        return _out(srcs[0], srcs[1], _fitted)
    info["gate"] = "THEN pane empty"
    per_px = min(_cover_window(im.size, big)[3] - _cover_window(im.size, big)[1] for im in ims) / big[1]
    z_max = min(FRAME_Z_MAX, per_px / FRAME_MIN_SOURCE)
    # the horizontal places tried (FRAME_U), the subject box's centre first where the photo system knows one
    us = list(FRAME_U)
    if masks[0] is not None and masks[0].getbbox():
        bx0, _by0, bx1, _by1 = masks[0].getbbox()
        us.insert(0, round((bx0 + bx1) / 2 / masks[0].width, 4))
    us = list(dict.fromkeys(us))
    subjects_u = {u: _subject_top(ims[0], big, u) for u in us}
    subject = subjects_u[us[0]] if subjects_u.get(0.5) is None else subjects_u[0.5]
    info.update({"subject_top_share": None if subject is None else round(subject, 4), "z_max": round(z_max, 3)})
    if all(s is None for s in subjects_u.values()) or z_max < 1.0:
        log.info("Then-and-now 9:16: the THEN pane is empty and no shared window fixes it (%s); centred crop kept.",
                 "no subject found" if subject is None else f"zoom limit {z_max:.2f}")
        info["window"] = "centred (no window found)"
        return _out(srcs[0], srcs[1], _fitted)
    ch = big[1] / (1 + PUSH)                      # the big image's rows the frame shows at rest
    lead = (big[1] - ch) / 2
    want = sum(SUBJECT_TOP) / 2 * split
    old_im = ims[0]
    best = None                                    # the in-band window with the most THEN structure
    passed = None                                  # (score, ...) of the windows that pass both panes
    for u in us:
        s_top = subjects_u[u]
        if s_top is None:
            continue
        for k in range(int((z_max - 1.0) / FRAME_Z_STEP + 1e-6) + 1):
            z = round(1.0 + k * FRAME_Z_STEP, 4)
            x0, _, x1, _ = _cover_window(old_im.size, big, z, u=u)
            wh = (x1 - x0) * big[1] / big[0]
            s_row = s_top * old_im.height
            top = s_row - (lead + want * ch / height) * wh / big[1]
            top = min(max(top, 0.0), old_im.height - wh)
            v = (top + wh / 2) / old_im.height
            row = ((s_row - top) * big[1] / wh - lead) * height / ch
            if not SUBJECT_TOP[0] * split <= row <= SUBJECT_TOP[1] * split:
                log.debug("Then-and-now 9:16 window: zoom %.2f at %.2f puts the subject at row %.0f.", z, u, row)
                continue
            if info["registered"] and not keeps_centre(_cover_window(old_im.size, big, z, v, u), old_im.size, big):
                info["centre_kept"] = info.get("centre_kept", 0) + 1     # windows the rule set aside
                continue
            cand = [im.resize(big, Image.LANCZOS, box=_cover_window(im.size, big, z, v, u)).convert("RGB")
                    for im in ims]
            tp, lp = panes(*cand)
            dt, dl = _edge_density(tp), _edge_density(lp)
            log.debug("Then-and-now 9:16 window: zoom %.2f at %.2f, subject row %.0f, pane edges %.3f / %.3f.",
                      z, u, row, dt, dl)
            score = _middle_density(tp) / z         # the THEN structure in the pane's middle, per zoom
            if dl >= FRAME_EDGE_MIN and dt >= FRAME_BEST_GAIN * dens and (best is None or score > best[7] + 1e-9):
                best = (dt, dl, z, v, row, cand, u, score)
            if dt >= FRAME_EDGE_MIN and dl >= FRAME_EDGE_MIN:
                if passed is None or score > passed[0] + 1e-9:
                    passed = (score, dt, dl, z, v, row, cand, u)
                break                                  # this place's least zoom that passes
    if passed is not None:
        _score, dt, dl, z, v, row, cand, u = passed
        info.update({"zoom": z, "window": "shared", "v_centre": round(v, 4), "u_centre": round(u, 4),
                     "subject_row": round(row, 1), "subject_row_share": round(row / split, 4),
                     "then_pane_edge_after": round(dt, 4), "now_pane_edge_after": round(dl, 4),
                     "source_px_per_frame_px": round(per_px / z, 3)})
        log.info("Then-and-now 9:16: shared window at zoom %.2f across %.2f, the subject top at %.1f%% of the THEN "
                 "pane.", z, u, 100 * row / split)
        return _out(cand[0], cand[1], lambda m, z=z, v=v, u=u: m.resize(big, Image.NEAREST,
                                                                          box=_cover_window(m.size, big, z, v, u)))
    # Review round 3 (T7a): a soft old print (Colossi then_0, an 1876 albumen print) never reaches FRAME_EDGE_MIN
    # under Canny(50, 150), so no window passed and the pane stayed sky. The in-band window (the subject's top at
    # SUBJECT_TOP of the pane) with the most THEN structure is taken when it carries at least FRAME_BEST_GAIN x the
    # centred pane's, and recorded as below the threshold; otherwise the centred crop is kept.
    if best is not None and best[0] >= FRAME_BEST_GAIN * dens:
        dt, dl, z, v, row, cand, u, _score = best
        info.update({"zoom": z, "window": "shared (most THEN structure; under FRAME_EDGE_MIN)", "v_centre": round(v, 4),
                     "u_centre": round(u, 4), "subject_row": round(row, 1), "subject_row_share": round(row / split, 4),
                     "then_pane_edge_after": round(dt, 4), "now_pane_edge_after": round(dl, 4),
                     "source_px_per_frame_px": round(per_px / z, 3)})
        log.info("Then-and-now 9:16: no window reaches edge density %.2f; the subject-top window at zoom %.2f "
                 "across %.2f (THEN pane %.3f against %.3f centred) is used.", FRAME_EDGE_MIN, z, u, dt, dens)
        return _out(cand[0], cand[1], lambda m, z=z, v=v, u=u: m.resize(big, Image.NEAREST,
                                                                          box=_cover_window(m.size, big, z, v, u)))
    log.info("Then-and-now 9:16: the THEN pane is empty and no zoom up to %.2f places the subject; centred crop kept.",
             z_max)
    info["window"] = "centred (no window found)"
    return _out(srcs[0], srcs[1], _fitted)


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
        d.rectangle(band(lo, hi), fill=rgba(brand.INK, KEYLINE_ALPHA))
    d.rectangle(band(p - half, p + half), fill=rgba(brand.BONE))
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
DIVIDER_BLUR = False                # cycle 5: the divider is never averaged over the shutter (a hard rule on a soft join)
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
    # Freeze cycle 5 (FIGURES, panel 6 MOTION-02: the moving divider read as a soft grey strip about 35 px tall):
    # the bone rule and its INK keylines box-averaged over the shutter mixed to a grey band over both photos. The
    # shutter stays on the JOIN (the two photos ramp into each other, which is a blend of the photos, never an
    # overlay); the divider itself is drawn hard at its position (draw_divider), as on every slow frame.
    fb, fk = (divider_profile(edge, pos, dpos) if DIVIDER_BLUR else
              (np.zeros(edge, np.float32), np.zeros(edge, np.float32)))
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
        out = (out * (1.0 - bb - kb * ka) + np.asarray(brand.BONE, np.float32) * bb
               + np.asarray(brand.INK, np.float32) * (ka * kb))
        out_im.paste(Image.fromarray(np.clip(np.round(out), 0, 255).astype(np.uint8), "RGB"), box[:2])
    return out_im if DIVIDER_BLUR else draw_divider(out_im, pos)


def marker_xy(which: str, width: int, height: int, side: str = "left", word: str | None = None) -> tuple[int, int, str]:
    """(x, y, anchor) of a year marker's kicker. 9:16: in each panel's part of the graphics box (the Reel box above
    its caption band), left-set at its left edge + 18 or (side "right", T10) right-set to its right edge - 18, the
    NOW kicker's cap top NOW_DROP px
    below the divider. 16:9: either side of the divider at the top of title-safe, THEN set right to it and NOW
    left (side "inner", the default; the master's vignette takes the corners of a 16:9 frame below 4.5:1, measured on
    the graded frame), or (side "outer") at its panel's outer edge of title-safe."""
    split = _split_at(width, height)
    if width > height:
        tx0, y0, tx1, _ = get_text_box(width, height)
        if side == "outer":     # freeze cycle 6 (T2a): the panel's outer edge of title-safe, the other documented place
            return (tx0, y0 + 40, "lt") if which == "then" else (tx1, y0 + 40, "rt")
        return (split - 64, y0 + 40, "rt") if which == "then" else (split + 64, y0 + 40, "lt")
    _gx0, y0, _gx1, _ = safe_zones.graphics_box(width, height)
    # freeze cycle 3 (lane A, MG-8: Roman's plates started 4 px inside the Reel box): across, the text column (the box
    # inset by safe_zones.TEXT_INSET, as every card), so a left- or right-set plate keeps the system's margin
    x0, _, x1, _ = safe_zones.text_box("reel", width, height)
    # lane A cycle 1: side "centre:<x>" centres the marker on the line x (marker_lines centres each line on it)
    x, anchor = ((x1 - 18, "rt") if side == "right" else (int(side.split(":")[1]), "mt")
                 if side.startswith("centre:") else (x0 + 18, "lt"))
    if which == "then":
        return x, max(y0 + 42, TEXT_TOP), anchor
    return x, split + NOW_DROP - kicker_font("portrait").getbbox(word or "NOW", anchor="lt")[1], anchor


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
    """(kicker words' xy, year xy, anchor) of a marker whose kicker reads `word` (default which.upper()). Left-set,
    the kicker's lead rule starts on the marker's column and its words follow the rule's gap; the year's first ink
    starts on the column (shifted by its measured side bearing). Right-set (16:9 THEN, 9:16 side "right"), the
    kicker's words and the year end on the marker's x (their drawn last ink), the rule before the words. The year sits
    YEAR_OFFSET kicker sizes below on 9:16, and KICKER_GAP_WIDE px of drawn ink below the kicker's ink on 16:9."""
    word = word or which.upper()
    x, y, anchor = marker_xy(which, width, height, side, word)
    key = "landscape" if width > height else "portrait"
    kicker, figure, track = kicker_font(key), year_font(key), kicker_track(key)
    rule, gap, _ = kicker_lead(kicker)
    if key == "landscape":
        fy = y + _ink(kicker, word, track)[3] + KICKER_GAP_WIDE - _ink(figure, str(year))[1]
    else:
        fy = y + round(kicker_px()[key] * YEAR_OFFSET)
    if anchor[0] == "r":
        return ((x - _ink(kicker, word, track, "rt")[2], y), (x - _ink(figure, str(year), 0, "rt")[2], fy), anchor)
    if anchor[0] == "m":    # centred (9:16 side "centre"): each line's drawn ink, the kicker's rule included, on x
        k_ink, f_ink = _ink(kicker, word, track), _ink(figure, str(year))
        k_left = x - (rule + gap + k_ink[2] - k_ink[0]) / 2
        f_left = x - (f_ink[2] - f_ink[0]) / 2
        return ((round(k_left + rule + gap - k_ink[0]), y), (round(f_left - f_ink[0]), fy), "lt")
    return (x + rule + gap - _ink_left(kicker, word), y), (x - _ink_left(figure, str(year)), fy), anchor


def marker_rule(kxy: tuple[int, int], word: str, key: str, anchor: str) -> tuple[int, int, int, int]:
    """The kicker's lead rule (x0, y0, x1, y1, inclusive) before its words set at `kxy` with `anchor`: one cap long
    at mid-cap, kicker.rule_gap caps before the words' first ink."""
    font, track = kicker_font(key), kicker_track(key)
    rule, gap, thick = kicker_lead(font)
    ink = _ink(font, word, track, anchor)
    x_end = kxy[0] + ink[0] - gap
    mid = kxy[1] + ink[3] - cap_height(font) / 2
    return (round(x_end - rule), round(mid - thick / 2), round(x_end) - 1, round(mid - thick / 2) + thick - 1)


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
    """The measured cap height of the year as drawn (year_font: the figure face at YEAR_PX[key]; cycle 0 round 8
    integration: it measured the Anton year, a second font of its own the drawn year no longer uses)."""
    return cap_height(year_font(key))


brand.on_change(year_cap.cache_clear)   # a measure in the display face: dropped when the brand changes


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
    ink_y = 0.299 * brand.GLOW_INK[0] + 0.587 * brand.GLOW_INK[1] + 0.114 * brand.GLOW_INK[2]
    return max(base, math.ceil(1.5 * depth * max(0.0, ground_y - ink_y) / GLOW_MAX_STEP))


# BAND_FALL (module __getattr__): band_fall(1080, 1920), the longer fall (9:16), the furthest any glow reaches;
# measured in the active brand's display face when read (QF-35a round 2).
# The glow's distance is a soft minimum over the marker's lines (each line's own distance field, combined as
# -K ln sum exp(-d / K)), so where the short kicker meets the wider year the glow's outline is rounded, not a
# crease. The glow is a warm ink (GLOW_INK), composited in float with a static TPDF dither (T3: GLOW_DITHER_LSB
# video codes, one value per 2 x 2 cell, the same on all three channels and every frame, only where the glow is
# partial) so a shallow ramp on a smooth sky does not terrace after the delivery encode.
GLOW_SMOOTH_K = 16.0
# brand.GLOW_INK: #1E1610
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
    grow = max(BAND_PAD + band_fall(1080, 1920), reach) + 8 * k
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
    out = base * (1 - a) + np.asarray(brand.GLOW_INK, np.float32) * a
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
# Freeze cycle 5 (panel 6 MOTION-01: a part-transparent plate beside the arriving divider): the fade keeps its six
# frames (T5: nothing blinks) and is over GROUP_LEAD of the wipe axis before the divider reaches the group, so no
# part-transparent marker stands next to the divider (group_clear). Under the split's own air at rest (50 px of 1920).
GROUP_LEAD = 0.02


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
    return gap >= max(DIVIDER_W / 2 + 2, GROUP_LEAD * edge)


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
# Design-freeze lane A cycle 1 (r8 AD backlog, Memnon 9:16: the left-set NOW plate sat over the colossus): centred
# columns across the box are candidates too (MARKER_CENTRES of them, evenly spaced, each keeping the widest marker
# inside the box), so a pair framed by two subjects (two colossi, two towers) sets its markers in the air between
# them. Left stays the default; another side only when its sum is SIDE_MARGIN lower, the quietest such side.
MARKER_CENTRES = 9
KICKER_PLATE_ROOM = 24     # px: a held plate's pad round the widest line, kept inside the box with it


def marker_sides(width: int, height: int, half_w: float) -> tuple[str, ...]:
    """The 9:16 markers' candidate sides: "left", the centred columns "centre:<x>" (x on the box, `half_w` the
    widest marker's half ink width plus 18 px from either box edge) and "right"."""
    x0, _y0, x1, _ = safe_zones.text_box("reel", width, height)    # cycle 3: the text column (marker_xy)
    lo, hi = x0 + 18 + half_w, x1 - 18 - half_w
    if hi <= lo:
        return ("left", "right")
    xs = sorted({round(lo + (hi - lo) * k / (MARKER_CENTRES - 1)) for k in range(MARKER_CENTRES)})
    return ("left", *(f"centre:{x}" for x in xs), "right")


SUBJECT_TIE = 0.01         # of a plate's area: subject shares this close are one share (the edge sum decides)


def subject_share(mask: Image.Image | None, box: tuple) -> float:
    """The share of `box` (frame px) over the subject mask (255 inside the subject), 0 when no mask."""
    if mask is None:
        return 0.0
    import numpy as np
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(mask.width, x1), min(mask.height, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float((np.asarray(mask.crop((x0, y0, x1, y1))) > 127).mean())


def subject_side(shares: dict[str, float], sums: dict[str, float]) -> str:
    """SYS-A-3 (lane A ruling 2): the 9:16 markers' side when a subject is known: the sides whose markers' plates
    lie least over the subject (within SUBJECT_TIE), then among those the edge-sum rule (quieter_side, left the
    default when left is among them; else the lowest edge sum)."""
    best = min(shares.values())
    tied = [s for s in shares if shares[s] <= best + SUBJECT_TIE]
    if "left" in tied:
        return quieter_side({s: sums[s] for s in tied})
    return min(tied, key=lambda s: (sums.get(s, 0.0), tied.index(s)))


def quieter_side(sums: dict[str, float], default: str = "left") -> str:
    """The side from a measure per side (lower is calmer): `default` unless another side's is at least SIDE_MARGIN
    below the default's, then the lowest of those (the order given on a tie)."""
    base = sums.get(default, 0.0)
    if base <= 0:
        return default
    order = list(sums)
    better = [s for s in order if s != default and sums[s] <= (1 - SIDE_MARGIN) * base]
    return min(better, key=lambda s: (sums[s], order.index(s))) if better else default


# Freeze cycle 6 (THENNOW T2a; panel 7 AD-T1, lander ruling 2026-11-27: on 9:16 Memnon both year plates sat on the
# colossus while open sky was free, because T10 put BOTH markers on ONE side by their summed edges, and the NOW
# panel's palms outvoted the THEN panel's sky). Each year plate now takes ITS OWN panel's calmest region among its
# documented positions (9:16: left, the centred columns, right, marker_sides; 16:9: "inner" against the divider, the
# default, or "outer" at its panel's outer edge): the least ground detail under its padded plate box (ground_detail),
# on the split at rest (and, for THEN, on the frame it opens on, averaged), the default kept unless another place is
# SIDE_MARGIN calmer (quieter_side). The same rule on both formats; a known subject (SYS-A-3) still comes first.
def ground_detail(frame: Image.Image, box: tuple) -> float:
    """The ground's detail under `box` (frame px): the mean absolute luma change from one pixel to the next, across
    and down, in luma per px at 1080 wide (0 on a flat field; the band-edge rule's ground_texture reads the same
    change, by rows only)."""
    import numpy as np
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(frame.width, x1), min(frame.height, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0.0
    y = _luma(np.asarray(frame.convert("RGB").crop((x0, y0, x1, y1)))).astype(np.float32)
    d = (float(np.abs(np.diff(y, axis=0)).mean()) + float(np.abs(np.diff(y, axis=1)).mean())) / 2
    return d * min(frame.width, frame.height) / 1080.0


def marker_candidates(width: int, height: int, half_w: float) -> tuple[str, ...]:
    """A marker's documented positions on this format, the default first."""
    if width > height:
        return ("inner", "outer")
    return marker_sides(width, height, half_w)

HIGH_MARGIN = PLACE_TARGET - 4.5     # the held tier's allowance for the platform re-encode (4.8 for a 4.5 gate)


def plate_tier(plates: list, rec: dict[str, Any], grounds: list, high: float, high_held: float) -> dict[str, Any]:
    """A planned marker's plate tier (B2): its held plate when every row plans `high_held` or more on its grounds,
    else its plate made opaque INK (each row then reads its fill against INK, every ground hidden); the record:
    {"tier": "held" | "opaque" | variant, "contrast": the least row's ratio (None when it is not drawn)}."""
    if not rec.get("shown"):
        return {"tier": "none", "contrast": None}
    least = min((p.contrast or 0.0) for p in plates)
    if rec.get("variant") != "plate" or getattr(plates[0], "backing", None) is None:
        return {"tier": str(rec.get("variant")), "contrast": round(least, 3)}
    if least + 1e-9 >= high_held:
        return {"tier": "held", "contrast": round(least, 3)}
    from .typography import plan_text
    for p in plates:
        p.backing.parent.alpha = 1.0
    worst = None
    for g in grounds:
        backed = g
        for p in plates:
            backed = p.backing.draw(backed)
        for p in plates:
            c = plan_text(backed, p.xy, p.text, p.font, p.fill, p.anchor, p.track, target=high, halo_px=0).contrast
            worst = c if worst is None else min(worst, c)
    for p in plates:
        p.contrast = worst
    return {"tier": "opaque", "contrast": round(float(worst or 0.0), 3), "held_contrast": round(least, 3)}


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
           "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-tune", "grain", *X264_THREADS,
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
    subjects: list = []
    a_src, b_src, framing = frame_pair(old, new, width, height, subjects=subjects)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    key = "landscape" if width > height else "portrait"
    k_font, y_font, track = kicker_font(key), year_font(key), kicker_track(key)
    split = _split_at(width, height)
    edge = max(width, height)
    srcs = {"then": a_src, "now": b_src}
    years = {"then": old_year, "now": new_year}
    words = {w: kicker_word(w, years[w]) for w in years}
    # The one wipe (freeze cycle 6, T1): its timing from the THEN marker's text and the motion tokens.
    text0 = then_text(old_year)
    t0, t1 = wipe_window(width, height, text0)
    # Each marker is planned where it rests (the split, at the end of the push) and solved again on the other frame
    # it is read on: THEN alone at the start, NOW as the divider lands (its entry).
    split0 = compose(pushed(a_src, CLIP_SECONDS, size), pushed(b_src, CLIP_SECONDS, size), split)
    alone0 = {"then": compose(pushed(a_src, 0.0, size), pushed(b_src, 0.0, size), edge),
              "now": compose(pushed(a_src, t1, size), pushed(b_src, t1, size), split)}
    # Review round 1 (B3, ruling 2026-11-27): a place is chosen on the PHOTOGRAPHS as placed, before any palette-
    # coloured element (no divider, no INK, no plate in a candidate box), so the choice is the same in every palette.
    photos0 = {"rest": compose(pushed(a_src, CLIP_SECONDS, size), pushed(b_src, CLIP_SECONDS, size), split,
                               divider=False),
               "open": compose(pushed(a_src, 0.0, size), pushed(b_src, 0.0, size), edge, divider=False)}

    def ink_boxes(which: str, ln: tuple) -> list:
        kxy, fxy, anchor = ln
        return [TextPlate(text, font, brand.BONE, xy, anchor, tr, 0, brand.INK, 0, False, size).box
                for text, font, xy, tr in ((words[which], k_font, kxy, track), (str(years[which]), y_font, fxy, 0))]

    def padded(boxes: list, pad: int, clip: tuple) -> tuple:
        return (max(clip[0], min(b[0] for b in boxes) - pad), max(clip[1], min(b[1] for b in boxes) - pad),
                min(clip[2], max(b[2] for b in boxes) + pad), min(clip[3], max(b[3] for b in boxes) + pad))

    # T2a (freeze cycle 6): each marker in its own panel's calmest documented place (ground_detail, quieter_side)
    half_w = max((b[2] - b[0]) / 2 for w in years for b in ink_boxes(w, marker_lines(w, years[w], width,
                                                                                     height, words[w])))
    cands = marker_candidates(width, height, half_w + KICKER_PLATE_ROOM)
    subj = None
    if any(m is not None for m in subjects):
        # SYS-A-3: the markers placed against the photo system's subject (its mask through the same trims, window
        # and push as the photo), the plate's share over it first, the ground's detail second
        blank = Image.new("L", b_src.size, 0)
        masks = [m if m is not None else blank for m in subjects]
        subj = compose(pushed(masks[0], CLIP_SECONDS, size), pushed(masks[1], CLIP_SECONDS, size), split,
                       divider=False)
    sides: dict[str, str] = {}
    ranked: dict[str, list] = {}
    side_meta = {"rule": "calmest documented place per panel (ground_detail; SIDE_MARGIN)", "candidates": list(cands)}
    for w in ("then", "now"):
        details, shares = {}, {}
        for s in cands:
            box = padded(ink_boxes(w, marker_lines(w, years[w], width, height, words[w], s)), PAPER_PAD,
                         panel_box(w, width, height))
            grounds = [photos0["rest"]] + ([photos0["open"]] if w == "then" else [])
            details[s] = round(sum(ground_detail(g, box) for g in grounds) / len(grounds), 4)
            if subj is not None:
                shares[s] = round(subject_share(subj, box), 4)
        pool = details
        if shares:
            best = min(shares.values())
            pool = {s: details[s] for s in cands if shares[s] <= best + SUBJECT_TIE}
        default = cands[0] if cands[0] in pool else min(pool, key=lambda s: (pool[s], cands.index(s)))
        sides[w] = quieter_side(pool, default)
        # the places in the order the rule takes them: its choice, then the next calmest (B2's fallback)
        ranked[w] = [sides[w]] + sorted((s for s in pool if s != sides[w]), key=lambda s: (pool[s], cands.index(s)))
        side_meta[w] = {"side": sides[w], "detail": details, **({"subject_share": shares} if shares else {})}

    # Cycle 0 round 8 (operator ruling 2; panel consensus 7): each marker (its kicker and its year) is ONE overlay
    # label, planned by typography.plan_label on the lightest and the darkest of the pictures it sits on (its split
    # and its alone frame): the kicker in kicker.colour and the year in figure.colour on the label halo (<= 0.16),
    # else bone, else INK type (only where the ground under it is paper: paper_gate keeps ink off line art), else a
    # held INK plate holding the kicker's rule. The pair takes ONE step (below: plan_label_set), so one marker in
    # gold and the other in bone, or bare INK type beside a plate, no longer happens on one frame (round 8 R1).
    # The halo up to 1.0 and the glows are retired.
    import numpy as np

    from .brand_system import colour_of, tok
    from .typography import plan_label_set
    lightest = {w: Image.fromarray(np.maximum(np.asarray(split0), np.asarray(alone0[w]))) for w in years}
    darkest = {w: Image.fromarray(np.minimum(np.asarray(split0), np.asarray(alone0[w]))) for w in years}
    fills = [colour_of("kicker.colour"), colour_of("figure.colour")]

    # Review round 1 (B2, lander ruling 2026-11-27): a year plate's text holds contrast.high (7.0) in every palette,
    # through the plate's own tiers: the held plate (plate.held), then the opaque tier (an opaque INK year plate).
    # The held tier must plan HIGH_MARGIN over it (PLACE_TARGET's own allowance for the platform re-encode, 4.8 for
    # 4.5); the opaque tier's ratio is its fill against INK itself. Where the chosen place cannot reach it at the
    # opaque tier, the next calmest documented place that does is taken; a drop against the base place is then the
    # cost of moving off the subject, recorded per marker ("tier", "contrast").
    high = float(tok("contrast.high"))
    high_held = high + HIGH_MARGIN

    def plan(sides_now: dict[str, str]) -> tuple:
        lines_ = {w: marker_lines(w, years[w], width, height, words[w], sides_now[w]) for w in years}
        gate_ = {}
        for w in years:
            ok, st = paper_gate(ink_boxes(w, lines_[w]), [(split0, panel_box(w, width, height)),
                                                          (alone0[w], (0, 0, width, height))])
            gate_[w] = {"ok": ok, "split": st[0], "alone": st[1]}
        rules_ = {w: marker_rule(lines_[w][0], words[w], key, lines_[w][2]) for w in years}

        def member(which: str) -> dict:
            kxy, fxy, anchor = lines_[which]
            return {"ground": lightest[which], "rows": [(kxy, words[which], k_font, anchor, track),
                                                        (fxy, str(years[which]), y_font, anchor, 0)],
                    "colours": ([tuple(c) for c in fills], [tuple(brand.BONE)] * 2), "target": PLACE_TARGET,
                    "dark_ground": darkest[which], "ink": INK_ON_PAPER and gate_[which]["ok"],
                    "extra": [rules_[which]],
                    "colour_held": True}   # freeze cycle 4 (SYS item 3): the one kicker colour rule
        # Cycle 0 round 8 (R1, one treatment per set): THEN and NOW are one twin pair on one frame, so they are
        # planned as ONE set (typography.plan_label_set): the least intrusive step that holds on both markers'
        # grounds, the same on both; each keeps its own only when no one step holds both (the gate is never traded).
        order_ = [w for w in ("then", "now") if w in years]
        results, rec_ = plan_label_set([member(w) for w in order_])
        return lines_, gate_, rules_, dict(zip(order_, results)), rec_

    tiers: dict[str, dict] = {}
    while True:
        lines, gate, rules, planned, set_rec = plan(sides)
        short = []
        for w in years:
            ps, rec = planned[w]
            tiers[w] = plate_tier(ps, rec, [lightest[w], darkest[w]], high, high_held)
            if tiers[w]["contrast"] is not None and tiers[w]["contrast"] + 1e-9 < high:
                short.append(w)
        moved = False
        for w in short:            # the next calmest documented place, while one is left
            k = ranked[w].index(sides[w])
            if k + 1 < len(ranked[w]):
                sides[w] = ranked[w][k + 1]
                moved = True
        if not moved:
            break
    for w in years:
        side_meta[w].update({"side": sides[w], "tier": tiers[w]})
        if tiers[w]["contrast"] is not None and tiers[w]["contrast"] + 1e-9 < high:
            log.warning("Then-and-now: the %s marker holds %.2f:1 at its last place, under contrast.high %.1f.",
                        w, tiers[w]["contrast"], high)
    plates: dict[str, list] = {}
    mode: dict[str, str] = {}
    for w in years:
        ps, rec = planned[w]
        if not rec["shown"]:
            log.warning("Then-and-now: the %s marker is not drawn: %s.", w, rec["reason"])
        plates[w], mode[w] = ps, (rec["variant"] if rec["shown"] else "none")
        if rec["shown"] and getattr(ps[0], "backing", None) is not None:
            ps[0].backing.parent.devices.append((rules[w], tuple(ps[0].fill)))   # the rule rides on its plate
    halo_h = max((p.halo for w in years for p in plates[w]), default=0.0)
    log.info("Then-and-now type: THEN %s, NOW %s (%s); halo %.2f.", mode["then"], mode["now"],
             "/".join(words[w] for w in ("then", "now")), halo_h)
    LAST_PAPER.clear()
    LAST_PAPER.update({w: {**gate[w], "mode": "ink" if mode[w] == "ink" else "bone/gold"} for w in years})

    # The group schedule (T5): G is each marker's ink boxes and rule, padded by its halo's tail, or its plate.
    from .brand_system import label_halo

    def group(which: str) -> tuple:
        ps = [p for p in plates[which] if not p.empty]
        if not ps:
            return (0, 0, 0, 0)
        if getattr(ps[0], "backing", None) is not None:   # its rows' plates, together
            bx = [p.backing.parent.box for p in ps if getattr(p, "backing", None) is not None]
            return (min(b[0] for b in bx), min(b[1] for b in bx), max(b[2] for b in bx), max(b[3] for b in bx))
        pad = max((full_halo_pad(label_halo(p.font.size)[0]) if p.halo > 0 else 0) for p in ps)
        boxes = [p.box for p in ps] + [rules[which]]
        return (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
                max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)
    groups = {w: group(w) for w in years}
    positions = [divider_at(i / VIDEO_FPS, width, height, text0) for i in range(n)]
    sched = {w: group_alpha([group_clear(groups[w], w, positions[i], width, height) for i in range(n)]) for w in years}
    from .brand_system import ease_out
    reveal = float(tok("motion.reveal"))
    for i in range(n):
        t = i / VIDEO_FPS
        sched["then"][i] *= ease_out(t / reveal)                 # the THEN marker arrives (motion.reveal, ease_out)
        # review round 1 (D5): the NOW marker arrives the same way, once the divider has landed, on every placement
        sched["now"][i] = (ease_out((t - t1) / reveal) if t >= t1 - 1e-9 else 0.0) * (
            1.0 if group_clear(groups["now"], "now", positions[i], width, height) else 0.0)
    hold = hold_seconds(width, height, text0)
    both = f"{text0} {words['now']} {years['now']}"
    timing = {"then_dwell": round(t0, 4), "wipe": [round(t0, 4), round(t1, 4)], "split_hold": round(hold, 4),
              "read_then": round(read_seconds(text0), 4), "read_both": round(read_seconds(both), 4),
              "hold_reads": bool(hold + 1e-9 >= read_seconds(both))}
    if not timing["hold_reads"]:
        log.warning("Then-and-now: the split holds %.2f s, under the %.2f s both markers need to be read.", hold,
                    read_seconds(both))

    meta = {"type_mode": dict(mode), "kicker": dict(words), "halo": round(halo_h, 2),
            "label": {w: {k: v for k, v in planned[w][1].items() if k != "tried"} for w in years},
            "label_set": set_rec,
            "rules": {w: list(rules[w]) for w in years},
            "paper_gate": gate, "side": side_meta, "timing": timing,
            "framing": framing, "groups": {w: list(groups[w]) for w in years},
            "planned_contrast": {f"{w}_{part}": round(p.contrast or 0, 2) for w in ("then", "now")
                                 for part, p in zip(("kicker", "year"), plates[w])}}
    last_meta.clear()
    last_meta.update(meta)
    last_meta["schedule"] = {w: [round(v, 4) for v in sched[w]] for w in years}
    final: dict[str, Image.Image] = {}

    def layer(which: str, t: float, g: float) -> Image.Image:
        """One photo at this frame's push with its own labels at the group alpha `g`."""
        im = pushed(srcs[which], t, size)
        if g <= 0 or not plates[which]:
            return im
        if getattr(plates[which][0], "backing", None) is None:   # the kicker's rule (on a plate, the plate draws it)
            x0, y0, x1, y1 = rules[which]
            lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
            ImageDraw.Draw(lay).rectangle([x0, y0, x1, y1], fill=tuple(plates[which][0].fill) + (round(255 * g),))
            im = Image.alpha_composite(im.convert("RGBA"), lay).convert("RGB")
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
                                               for part, p in zip(("kicker", "year"), plates[w]) if plates[w]])
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


def make(topic: dict[str, Any], work_dir: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
         grade_bias: float = 0.0) -> tuple[Path | None, list[dict[str, Any]]]:
    """(clip, credits of both photos) or (None, []). `grade_bias`: the bias the master grades the run's photos with;
    the pair takes the same photo grade (QF-86, src/photo_grade.py)."""
    try:
        LAST_REFUSAL.clear()
        found = source(topic, work_dir)
        if not found:
            import json
            refused = list(LAST_REFUSAL) or [{"reason": "no dated, licensed old/new pair to compare"}]
            (work_dir / REFUSAL_SIDECAR).write_text(json.dumps({"drawn": False, "refused": refused},
                                                               indent=1), encoding="utf-8", newline="\n")
            return None, []
        old, old_c, new, new_c = found
        from .photo_grade import using
        with using(grade_bias):
            clip = render(old, old_c["year"], new, new_c["year"], work_dir / "graphic_thennow.mp4", width=width,
                          height=height)
        reg = Path(old).parent / REGISTRATION_SIDECAR
        if clip and reg.exists():   # the registration's measured numbers beside the clip, as its contrast is
            Path(str(clip) + ".registration.json").write_text(reg.read_text(encoding="utf-8"), encoding="utf-8")
        return clip, [old_c, new_c]
    except Exception as e:  # optional graphic
        log.warning("Then-and-now failed: %s", e)
        return None, []
