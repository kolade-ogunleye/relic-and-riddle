"""
src/callouts.py
===============
Labelled photos: a real photo of the site with animated callouts naming the features the
narration talks about ("ventilation shaft", "rolling stone door"). Original, educational, and it
tells viewers what they're looking at.

Truthfulness: labels come only from the script's photo_labels (features named in the fact-checked
narration). A vision model locates each feature on the uncropped photo at its native aspect (once per photo,
cached beside it, so the 9:16 Reel and the 16:9 YouTube cut share one answer) and must locate it with HIGH
confidence for its label to be proposed; it picks the one approved topic photo that shows the most features.
Each proposed label is then checked again, by a different model, on a padded full-resolution crop centred on
its dot with a crosshair (verify), and dropped unless that check is a high-confidence yes on the same site.
Fewer than two labels left -> no graphic. Every render writes an audit trail beside the clip, and with
--auto-approve the graphic is drawn only while a passing accuracy audit covers the current models and prompts.
"""

import contextvars
import copy
import hashlib
import heapq
import json
import logging
import math
from itertools import combinations, count, pairwise
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFilter
from pydantic import BaseModel, Field

from . import safe_zones
from .brand import BONE, INK
from .camera_motion import sample as camera_sample
from .config import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .graphics import (
    CLIP_SECONDS,
    GOLD,
    TEXT_BOTTOM,
    TEXT_TOP,
    WHITE,
    _ease,
    _ease_io,
    _encode,
    get_text_bounds,
    get_text_box,
    record_contrast,
)
from .llm import LLMError, image_block, watch_served
from .llm import task as llm_task
from .typography import (
    cap_height,
    get_font,
    is_video,
    plan_text,
    tracked_width,
)

log = logging.getLogger(__name__)
W, H = VIDEO_WIDTH, VIDEO_HEIGHT
MAX_PHOTOS = 6
MAX_LABELS = 3   # panel round 3: four labels crowded the frame with near-crossing leaders; three at most


class Located(BaseModel):
    label: str
    visible: bool
    x: float                              # 0-1 across the image: a pointable point on the feature's surface
    y: float                              # 0-1 down the image
    w: float                              # 0-1: the feature's width as a share of the image width
    h: float                              # 0-1: its height as a share of the image height
    confidence: Literal["high", "medium", "low"]
    box: list[float] = Field(default_factory=list)   # (x0, y0, x1, y1) fractions: the feature's box
    kind: Literal["point", "extent"] = "point"       # extent: a wall, stair, dome, frieze or facade
    evidence: str = ""                                # a short note on what identifies it


class CalloutPlan(BaseModel):
    image: int                            # 1-based number of the best photo (0 if none shows two features)
    features: list[Located]


SYSTEM = """You annotate photos for a history documentary. You get numbered photos of one site, each whole and
uncropped, and a list of physical features. Pick the ONE photo that clearly shows the most of these features, and
for each feature say whether it is visible in that photo and where: x, y as fractions of that photo's width and
height at a point ON the feature's surface (never in a hole, opening or the sky through it); w, h its extent as
fractions of the width and height; box its bounding box [x0, y0, x1, y1] as fractions; kind "extent" for a surface
the label names as a whole (a wall, dome, facade, floor, ceiling, stair or frieze), "point" for an object (a door,
window, tower, statue, niche or shaft); evidence a few words on what identifies it. Use confidence "high" only when
you are sure that exact feature is what you are pointing at; never guess. If no photo clearly shows at least two
features, return image 0."""


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def prompt_hash(text: str) -> str:
    return _sha(text)[:16]


def models() -> dict[str, str]:
    """The model each step runs on (recorded in the audit): the locator, and for every check (its blind location,
    its blind look at the crosshair, the text match, the decoy) a different model wherever the configuration offers
    two distinct ones. On the free engine those are FREE_WRITER_MODEL and FREE_CHECKER_MODEL (the match, a text
    call, runs on FREE_LIGHT_MODEL when one is set); on Claude, CLAUDE_MODEL and the first other configured Claude
    model."""
    from . import config, free_llm
    if free_llm.enabled():
        loc, chk = config.FREE_WRITER_MODEL, config.FREE_CHECKER_MODEL
        match = config.FREE_LIGHT_MODEL or chk
    else:
        return _claude_pair()
    return {"locate": loc, "check": chk, "match": match}


def _claude_pair() -> dict[str, str]:
    """The locator's and the checker's Claude models: CLAUDE_MODEL, and the first other configured Claude model."""
    from . import config
    loc = config.CLAUDE_MODEL
    chk = next((m for m in (*config.CLAUDE_FALLBACK_MODELS, *config.LIGHT_TASK_MODELS, *config.SECOND_CHECK_MODELS)
                if m and m != loc), loc)
    return {"locate": loc, "check": chk, "match": chk}


def _model_kw(step: str) -> dict[str, Any]:
    """free_llm.call's arguments that run `step` on its model: the role picks the Gemini model on the free engine;
    `models` the Claude one (on Claude, and for the free engine's Claude last resort, which never gets a Gemini id)."""
    return {"role": "writer" if step == "locate" else "checker", "models": [_claude_pair()[step]]}


def prompt_hashes() -> dict[str, str]:
    return {"locate": prompt_hash(SYSTEM), "check": prompt_hash(VERIFY_SYSTEM + LOOK_SYSTEM + MATCH_SYSTEM + CHECK_LOCATE_SYSTEM)}


def _clean(photo: Path) -> Image.Image:
    """The photo as cover_crop sees it before fitting: upright, any caption strip cut off (RGB)."""
    from PIL import ImageOps

    from .photo_hygiene import without_caption_strip
    with Image.open(photo) as im:
        clean, _ = without_caption_strip(ImageOps.exif_transpose(im).convert("RGBA"))
        return clean.convert("RGB")


def cover_map(src: tuple[int, int], size: tuple[int, int]) -> tuple[float, float, float]:
    """(k, ox, oy) with frame = (source - (ox, oy)) * k: the transform cover_crop (ImageOps.fit, centred) applies."""
    iw, ih = src
    k = max(size[0] / iw, size[1] / ih)
    return k, (iw - size[0] / k) / 2, (ih - size[1] / k) / 2


LOCATE_PX = 1536                      # the long side the uncropped photo is sent at
LOCATE_CACHE = "callout_locate.json"  # beside the photos: one location per photo for every format
# Three location passes that must agree (round 5 section 9): the whole photo, a 1.25x centre crop, and that crop
# shifted 8 % (toward where the first pass found the features). Every pass must answer high and visible; the three
# points' spread (the largest distance between two of them) must be at most max(SPREAD_A, 0.25 x min(w, h)) and
# never over SPREAD_MAX; the median point is drawn. A spread over SPREAD_A makes it tier B. The spread and the
# extent are measured in photo pixels scaled to a SPREAD_REF px long side, never in a format's frame pixels, so a
# label gets the same tier on the 9:16 and the 16:9 cut (a portrait photo's 16:9 crop magnifies it 1.8x).
VIEW_ZOOM, VIEW_SHIFT = 1.25, 0.08
SPREAD_A, SPREAD_MAX = 24, 60
SPREAD_REF = 1920
LOCATE_VERSION = "3pass-2"            # 3pass-2: each pass records the model that served it
FULL_VIEW = (0.0, 0.0, 1.0, 1.0)
# The view (x0, y0, x1, y1 as fractions of the whole photo) the location pass in progress is shown.
locate_view: contextvars.ContextVar[tuple[float, float, float, float]] = contextvars.ContextVar(
    "callout_locate_view", default=FULL_VIEW)


def location_views(features: list[Located]) -> list[tuple[float, float, float, float]]:
    """The three views the location passes are made on: whole, a 1.25x centre crop, and that crop moved VIEW_SHIFT
    toward the side where the first pass's confident features lie (so it keeps them in view)."""
    s = 1 / VIEW_ZOOM
    m = (1 - s) / 2
    sure = [f for f in features if f.visible and f.confidence == "high"]
    mx = sum(f.x for f in sure) / len(sure) if sure else 0.5
    my = sum(f.y for f in sure) / len(sure) if sure else 0.5
    dx = VIEW_SHIFT if mx >= 0.5 else -VIEW_SHIFT
    dy = VIEW_SHIFT if my >= 0.5 else -VIEW_SHIFT
    return [FULL_VIEW, (m, m, m + s, m + s), (m + dx, m + dy, m + s + dx, m + s + dy)]


def _map_located(f: Located, a: tuple, b: tuple) -> Located:
    """`f` (fractions of view `a`) as fractions of view `b` (both views as fractions of the whole photo)."""
    aw, ah, bw, bh = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]

    def X(v: float) -> float:
        return (a[0] + v * aw - b[0]) / bw

    def Y(v: float) -> float:
        return (a[1] + v * ah - b[1]) / bh
    box = list(f.box or [])
    if len(box) == 4:
        box = [X(box[0]), Y(box[1]), X(box[2]), Y(box[3])]
    return f.model_copy(update={"x": X(f.x), "y": Y(f.y), "w": f.w * aw / bw, "h": f.h * ah / bh, "box": box})


def from_view(f: Located, view: tuple) -> Located:
    """A feature located on `view` as fractions of the whole photo."""
    return _map_located(f, view, FULL_VIEW)


def to_view(f: Located, view: tuple) -> Located:
    """A feature located on the whole photo as `view` shows it (not visible when its point is outside the view)."""
    g = _map_located(f, FULL_VIEW, view)
    inside = 0.0 <= g.x <= 1.0 and 0.0 <= g.y <= 1.0
    return g if inside else g.model_copy(update={"visible": False})


def _cache_path(cache_dir: Path | None, photo: Path) -> Path:
    return Path(cache_dir or Path(photo).parent) / LOCATE_CACHE


def _cache_read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_write(path: Path, key: str, value: Any) -> None:
    data = _cache_read(path)
    data[key] = value
    try:
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    except OSError as e:   # a read-only photo folder: the location is not shared, nothing else changes
        log.info("Callouts: location cache not written (%s).", e)


def _photo_sha(p: Path) -> str:
    try:
        return _sha(Path(p).read_bytes())
    except OSError:
        return _sha(str(p))


def locate(stills: list[Path], labels: list[str], topic_title: str, cache_dir: Path | None = None) -> dict | None:
    """The vision model's answer for `stills` (each sent whole at its native aspect, long side LOCATE_PX), cached
    beside the photos under the photos' sha256, the labels, the model and the prompt, so every format reads the
    same answer: {"image", "photo", "size": clean (w, h), "plan": CalloutPlan dict}. None when it cannot run."""
    from . import free_llm
    key = _sha("|".join([*(_photo_sha(p) for p in stills), *labels, models()["locate"], prompt_hashes()["locate"],
                         LOCATE_VERSION]))
    cache = _cache_path(cache_dir, stills[0])
    hit = _cache_read(cache).get(key)
    if isinstance(hit, dict) and "plan" in hit:
        return hit
    tmps: list[Path] = []
    content: list[dict] = []
    sizes = []
    try:
        for i, p in enumerate(stills, 1):
            clean = _clean(p)
            sizes.append(list(clean.size))
            small = clean.copy()
            small.thumbnail((LOCATE_PX, LOCATE_PX), Image.LANCZOS)
            tmp = Path(p).parent / f".callout_locate_{i}.jpg"
            small.save(tmp, quality=88)
            tmps.append(tmp)
            content += [{"type": "text", "text": f"Photo {i}:"}, image_block(tmp, LOCATE_PX)]
        ask = {"type": "text", "text": f"Site: {topic_title}\nFeatures: " + "; ".join(labels)}
        content.append(ask)
        token = locate_view.set(FULL_VIEW)
        try:
            with watch_served() as got:
                result = free_llm.call(SYSTEM, content, CalloutPlan, task_class="light", effort="medium",
                                       max_tokens=3000, **_model_kw("locate"))
        finally:
            locate_view.reset(token)
        served: list[str | None] = [got[-1] if got else None]   # the model that answered each pass
        idx = result.image
        passes: list[dict | None] = [result.model_dump()]
        views = location_views(result.features)
        if 1 <= idx <= len(stills):   # the second and third passes, on the chosen photo only
            clean = _clean(stills[idx - 1])
            for n, v in enumerate(views[1:], 2):
                crop = clean.crop((round(v[0] * clean.width), round(v[1] * clean.height),
                                   round(v[2] * clean.width), round(v[3] * clean.height)))
                crop.thumbnail((LOCATE_PX, LOCATE_PX), Image.LANCZOS)
                tmp = Path(stills[idx - 1]).parent / f".callout_locate_pass{n}.jpg"
                crop.save(tmp, quality=88)
                tmps.append(tmp)
                token = locate_view.set(v)
                try:
                    with watch_served() as by:
                        got = free_llm.call(SYSTEM, [{"type": "text", "text": "Photo 1:"}, image_block(tmp, LOCATE_PX),
                                                     ask], CalloutPlan, task_class="light", effort="medium",
                                            max_tokens=3000, **_model_kw("locate"))
                    passes.append(CalloutPlan(image=got.image, features=[from_view(f, v) for f in got.features])
                                  .model_dump())
                    served.append(by[-1] if by else None)
                except LLMError as e:   # a pass that does not answer: no feature agrees (plan drops them all)
                    log.warning("Callout location pass %d failed: %s", n, e)
                    passes.append(None)
                    served.append(None)
                finally:
                    locate_view.reset(token)
    except LLMError as e:
        log.warning("Callout planning failed: %s", e)
        return None
    finally:
        for t in tmps:
            t.unlink(missing_ok=True)
    entry = {"image": idx, "photo": Path(stills[idx - 1]).name if 1 <= idx <= len(stills) else None,
             "size": sizes[idx - 1] if 1 <= idx <= len(stills) else None, "plan": result.model_dump(),
             "passes": passes, "views": [list(v) for v in views], "model": models()["locate"],
             "served": served, "prompt_hash": prompt_hashes()["locate"]}
    if None not in passes:   # a failed pass is asked again next time, not cached
        _cache_write(cache, key, entry)
    return entry


def _frac_box(f: Located) -> tuple[float, float, float, float]:
    b = list(f.box or [])
    if len(b) == 4 and all(0.0 <= v <= 1.0 for v in b) and b[0] < b[2] and b[1] < b[3]:
        return b[0], b[1], b[2], b[3]
    w, h = max(0.0, f.w), max(0.0, f.h)
    return f.x - w / 2, f.y - h / 2, f.x + w / 2, f.y + h / 2


@llm_task("photo_callouts")
def plan(photos: list[Path], labels: list[str], topic_title: str, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
         cache_dir: Path | None = None) -> tuple | None:
    """(photo, [(label, x_px, y_px, area, w_px, h_px, kind, box_px, trust)]) for confidently located features on
    this format's frame (area: the share of the frame the feature covers; w_px, h_px: its extent; box_px its box;
    trust: {"spread_px" (photo px at the SPREAD_REF px long side, the same on every format), "answers_px" (frame px),
    "tier" (from the spread), "locate_served" (the models that answered the passes)}), or None. The location is
    made once on the uncropped photo (locate, three passes) and mapped into this format's crop with the transform cover_crop uses; a feature is kept only when
    all three passes answer high and visible within the allowed spread, at the median point. Each point is then
    checked on its own (verify)."""
    stills = [p for p in photos if not is_video(p)][:MAX_PHOTOS]
    from . import free_llm
    if not (free_llm.ready() and stills and labels):
        return None
    found = locate(stills, labels, topic_title, cache_dir)
    if not found:
        return None
    result = CalloutPlan(**found["plan"])
    log.info("Callouts plan: photo %d; %s", result.image,
             "; ".join(f"{f.label}={f.confidence}{'' if f.visible else ' (not visible)'}" for f in result.features))
    if not 1 <= result.image <= len(stills):
        return None
    photo = stills[result.image - 1]
    iw, ih = found.get("size") or _clean(photo).size
    k, ox, oy = cover_map((iw, ih), (width, height))
    wanted = {lbl.lower() for lbl in labels}
    top, bottom = (int(height * 0.12), int(height * 0.82)) if width > height else (TEXT_TOP, TEXT_BOTTOM)
    passes = [None if q is None else CalloutPlan(**q) for q in (found.get("passes") or [found["plan"]])]
    points = []
    for f in result.features:
        if not (f.visible and f.confidence == "high" and f.label.lower() in wanted):
            continue
        answers = [None if q is None else next((g for g in q.features if g.label.lower() == f.label.lower()), None)
                   for q in passes]
        if len(answers) < 3 or any(a is None or not (a.visible and a.confidence == "high") for a in answers):
            log.info("Callout '%s' dropped: not located high and visible on all three passes.", f.label)
            continue
        xy = [((a.x * iw - ox) * k, (a.y * ih - oy) * k) for a in answers]
        ref = SPREAD_REF / max(iw, ih)          # photo px -> the reference scale, the same for every format
        spread = ref * max(math.hypot((p.x - q.x) * iw, (p.y - q.y) * ih) for p, q in combinations(answers, 2))
        fw, fh = max(0.0, f.w) * iw * k, max(0.0, f.h) * ih * k
        limit = min(SPREAD_MAX, max(SPREAD_A, 0.25 * min(max(0.0, f.w) * iw, max(0.0, f.h) * ih) * ref))
        if spread > limit:
            log.info("Callout '%s' dropped: its three locations spread %.0f px (at most %.0f).", f.label, spread, limit)
            continue
        x, y = sorted(p[0] for p in xy)[1], sorted(p[1] for p in xy)[1]
        b = _frac_box(f)
        box = ((b[0] * iw - ox) * k, (b[1] * ih - oy) * k, (b[2] * iw - ox) * k, (b[3] * ih - oy) * k)
        trust = {"spread_px": round(spread, 1), "spread_ref_px": SPREAD_REF,
                 "answers_px": [[round(v, 1) for v in p] for p in xy], "tier": "A" if spread <= SPREAD_A else "B",
                 "locate_served": sorted({m or "unknown" for m in (found.get("served") or [None])})}
        if 0.05 * width < x < 0.95 * width and (top - 80) <= y <= (bottom + 40):
            points.append((f.label, x, y, fw * fh / (width * height), fw, fh, f.kind, box, trust))
    if len(points) < 2:
        log.info("Callouts: fewer than two confidently located features in safe zone; skipped.")
        return None
    return photo, points[:MAX_LABELS]


# Label design, round 5 (creative director, 2026-11-02; supersedes the round-3/4 notes: 16:9 elbows, side
# fallbacks ranked 4 + run/100, the pulse loop, a 230/255 plate, the 13 px leader band and the gold outline).
# Rules that hold everywhere:
#  - Rigid lock: a label (plate, leader, marker, loupe) moves by its own dot's push-in displacement and never
#    scales, so its leader keeps one shape on every frame; it is drawn from masks moved by sub-pixel offsets.
#  - One leader style on both formats: the leader meets the exact centre of one plate edge (any of the four). It is
#    S1, one straight segment on the edge's normal (the dot within 2 px of the centre line; a 2-34 px offset is
#    removed by sliding the plate), or S2, a stub square out of the edge then one 45-degree diagonal aimed at the
#    dot's centre, the corner rounded by a 16 px [14 px] fillet. No 90-degree elbows, curves or second bends.
#  - Which edge follows the direction from the plate to the dot: within 40 degrees of vertical only top/bottom,
#    within 30 degrees of horizontal only a side, between them the cost decides. Text is centred on a top/bottom
#    attachment and set flush toward a side one.
#  - The leader's gold runs into the marker (0.5 px into the dot's gold core, to the outline of an extent marker,
#    to the loupe's source ring) and 1.5 px into the plate's gold attachment bar: one unbroken gold mark.
#  - Draw order: background and spotlight; every casing and marker ink; every leader and marker gold; every plate
#    with its bar and text; the loupe insets. A leader never paints over a plate.
# Pixel values are 9:16 at 1080 x 1920, [16:9 at 1920 x 1080] where they differ.
FMT = ("portrait", "landscape")
LABEL_PX = {"portrait": 42, "landscape": 44}
LABEL_MAX_W = {"portrait": 400, "landscape": 520}   # the label's text column, before its plate padding
LABEL_TRACK = 0.04                                   # em of letter spacing
LABEL_LEADING = 1.08                                 # two-line step, x the size
LABEL_MAX_CHARS = 22                                 # per line
PLATE_PAD_X = 26                                     # 9:16 horizontal padding; PLATE_PAD per format
PLATE_PAD = {"portrait": 26, "landscape": 24}
PLATE_PAD_CAP = 20                                   # cap top to plate top, baseline to plate bottom
PLATE_ALPHA = 255                                    # the plate is opaque INK
PLATE_R = {"portrait": 10, "landscape": 9}
HAIRLINE_ALPHA = 46                                  # a 1 px BONE hairline inset 0.5 px
SHADOW_DY, SHADOW_BLUR, SHADOW_ALPHA = 4, 14, 90     # the plate's lift shadow, under it only
BAR_W = 3                                            # the gold attachment bar: as wide as the leader
BAR_LEN = {"portrait": (48, 140), "landscape": (44, 120)}   # top/bottom bar: clamp(0.45 x plate width, lo, hi)
BAR_SIDE_INSET = 10                                  # a side bar: the plate height less this at each end
LEADER_W, LEADER_HALO = 3, 2                         # gold core; INK casing each side (7 px in all)
CASING_ALPHA = 200
LEADER_LEN = {"portrait": (56, 80, 180, 260), "landscape": (48, 70, 160, 240)}   # min, preferred lo-hi, max
LEADER_TRIES = {"portrait": (56, 80, 110, 150, 200, 260), "landscape": (48, 70, 100, 140, 190, 240)}
STUB = 40                                            # 9:16 S2 stub; STUB_MIN per format
STUB_MIN = {"portrait": 40, "landscape": 36}
DIAG_MIN = 48                                        # S2 diagonal, along itself (a 34 px offset)
JOG_MAX, JOG_SNAP = 2, 34                            # S1 tolerance; offsets below JOG_SNAP slide the plate
FILLET_R = {"portrait": 16, "landscape": 14}
FILLET_PTS = 8
AIM_MIN = 24                                         # straight diagonal after the fillet, aimed at the dot
SLIDES = (0.0, -0.25, 0.25, -0.45, 0.45)             # plate slides along the attached edge, x its length
# Between those, a finer grid (review 2026-11-03: the reference 9:16 pair had valid placements only off the coarse
# grid, 41 px of slide where the grid offered 0 or 80): every FINE_STEP px of slide across the edge and of leader
# length. The cap applies to the valid candidates, cheapest first, so a finer grid never pushes a valid one out.
FINE_STEP = 12
# QF-24: on the organic Reel box (x 113-848, 88 px narrower than the ads box) a 9:16 set the reviewed candidates hold no
# valid layout for is tried again before it is refused: first at its full size with leader lengths every
# STEP_DOWN_FINE px (the window between a plate's own-dot ratio and a surface's inner half can be under FINE_STEP
# wide), then its labels stepped down together, LABEL_STEP px at a time to the existing floor (four fifths of
# LABEL_PX: _shapes), on that finer grid (as a map's stepped-down names are, graphics.NEIGHBOUR_FINE_STEP). One size
# for every label of a set, so no label reads as ranked under another; a set no size holds is refused as before
# (declutter: a pair dropped, or no graphic). 16:9 is unchanged.
STEP_DOWN_FINE = 4
LABEL_STEP = 2
MAX_CANDS = 240
SNAP_TOP = 32                                        # the cheapest valid sets the alignment snaps are tried on
DOT_CORE = {"portrait": 6, "landscape": 5}           # tier A marker: gold core on an INK disc
DOT_DISC = {"portrait": 8, "landscape": 7}
LEADER_INTO_CORE = 0.5                               # the leader's gold overlaps the core by this much
RIPPLE = {"portrait": (8, 30), "landscape": (7, 26)}   # the landing ripple's radius, from -> to
RIPPLE_TIME = 0.45
EXTENT_GROW, EXTENT_R = 12, 10                       # tier B outline: the box grown 12 px, radius 10 px
# The outline's gold is as wide as the leader's (3 px, not round 5's 2 px): a 2 px line moved by sub-pixel offsets
# keeps one pixel at full strength and the rest blends into its ink casing, so on a pale wall it read brown, not gold
# (review 2026-11-03). Its casing follows the guard's casing width like the leader's.
EXTENT_W = 3
EXTENT_MAX_SHARE = 0.35                              # a box larger than this share of the frame gets a point marker
TRACE_TIME = 0.25                                    # tier B: the outline traces out from its docking point
LABEL_GAP = 24                                       # px between a plate and another plate, a marker or a leader
LEADER_MARK_GAP = 16                                 # px between a leader and another label's marker
EXTENT_CLEAR = 12                                    # a leader never enters another feature's extent grown this
OWN_CLEAR = 16                                       # a plate stays this far off its own feature
# What a plate keeps off and a leader keeps out of follows the locator's `kind`, never the feature's size
# (operator, 2026-11-03, replacing the round-5 size rule OBJECT_MAX / SURFACE_CLEAR):
#  - an object (kind "point": a door, window, tower, statue, niche) keeps its whole located box (grown OWN_CLEAR)
#    clear of every plate, its own or another's, and no other label's leader enters it (grown EXTENT_CLEAR). The box,
#    never an ellipse round the dot: the dot is a pointable point on the object, often off its middle (review
#    2026-11-03: a tower's crown, a portal's lintel), and the box is stricter than the ellipse inscribed in it;
#  - a surface (kind "extent": a wall, dome, facade, floor, ceiling) drawn with a point marker may have a plate over
#    its outer part, but every plate keeps SURFACE_DOT px clear of its dot and stays out of its inner half (the
#    ellipse inscribed in its box at SURFACE_INNER of the half sides, centred on the box), and no other label's
#    leader enters that inner half;
#  - a tier-B feature (its outline drawn) keeps its whole outlined box clear of plates and other labels' leaders.
#    A surface on tier-A evidence whose outline cannot be laid out takes a point marker instead (declutter).
SURFACE_DOT = {"portrait": 40, "landscape": 36}
SURFACE_INNER = 0.5
LEADER_GAP, PARALLEL_GAP, NEAR, MIN_ANGLE = 20, 32, 120, 15
OWN_RATIO = 0.7                                      # plate to own dot <= 0.7 x plate to any other dot
ROW_SNAP, COL_SNAP = 40, 32
DOT_R = 30                                           # the dot's widest footprint (its landing ripple)
PUSH_IN = 0.06                                       # the background's push-in over the clip (labels move with it)
SAMPLES = tuple(i * 0.5 for i in range(int(CLIP_SECONDS / 0.5) + 1))   # t checked by every hard limit
SS = 4                                               # supersampling of drawn masks
ELBOW_RUN = ELBOW_RISE = None                        # retired (round 5): one leader style on both formats


def _fmt(width: int, height: int) -> str:
    return "landscape" if width > height else "portrait"


# The push-in, as one object (zoom(t), where a photo point lands at(t), the crop shown, a dot's track) so the
# layout, the caption-band check, the spotlight and the frames all use the same push. It is the centred push,
# 1 -> 1 + PUSH_IN linear over the clip (so loupe_mag holds). Round 4 found an aimed push pinned to the photo's
# edge within about 1 s, so the push stays centred and the spotlight carries the hierarchy.


class Push:
    """The background's centred push-in: zoom(t), and where a point on the unzoomed frame lands at(t)."""

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height

    def zoom(self, t: float) -> float:
        return 1.0 + PUSH_IN * max(0.0, min(1.0, t / CLIP_SECONDS))

    def at(self, x: float, y: float, t: float) -> tuple[float, float]:
        z = self.zoom(t)
        return self.width / 2 + (x - self.width / 2) * z, self.height / 2 + (y - self.height / 2) * z

    def shift(self, x: float, y: float, t: float) -> tuple[float, float]:
        """The rigid lock: how far a label whose dot rests at (x, y) has moved at `t`."""
        ax, ay = self.at(x, y, t)
        return ax - x, ay - y

    def crop(self, t: float) -> tuple[float, float, float, float]:
        """The part of the frame-sized photo shown at `t`."""
        z = self.zoom(t)
        cw, ch = self.width / z, self.height / z
        return (self.width - cw) / 2, (self.height - ch) / 2, (self.width + cw) / 2, (self.height + ch) / 2

    def track(self, x: float, y: float) -> list[tuple[float, float]]:
        """The positions the point takes: where it starts and where it ends (a straight line out from the centre)."""
        return [self.at(x, y, 0.0), self.at(x, y, CLIP_SECONDS)]


def text_box(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) every label plate stays inside: on 9:16 the Reel box edge to edge (x 113..848 at 1080, as the
    graphics' words run; QF-24: the box already holds the platforms' clearance, so the 18 px inset the ads box had,
    x 83..870, is not taken again), between the graphics' text rows; on 16:9 broadcast title-safe
    (graphics.get_text_box)."""
    if width > height:
        return get_text_box(width, height)
    x0, _, x1, _ = safe_zones.safe_box("reel", width, height)
    top, bottom = get_text_bounds(width, height)
    return x0, top, x1, bottom


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


def _length(pts: list[tuple[float, float]]) -> float:
    return sum(math.hypot(bx - ax, by - ay) for (ax, ay), (bx, by) in pairwise(pts))


def _trim(pts: list[tuple[float, float]], r: float) -> list[tuple[float, float]]:
    """`pts` starting `r` px along it."""
    if r <= 0:
        return list(pts)
    total = _length(pts)
    if r >= total:
        return [pts[-1], pts[-1]]
    rest = _partial(list(reversed(pts)), (total - r) / total)
    return list(reversed(rest))


def _attach(box: tuple, edge: str) -> tuple[float, float]:
    """The centre of the plate edge the leader meets."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return {"top": (cx, box[1]), "bottom": (cx, box[3]), "left": (box[0], cy), "right": (box[2], cy)}[edge]


# The edge's outward normal: from the plate toward its dot ("bottom": the dot is below the plate).
NORMAL = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}
EDGES = ("bottom", "top", "left", "right")


def _route(dot: tuple[float, float], end: tuple[float, float], edge: str,
           stub_min: float | None = None) -> list[tuple[float, float]]:
    """The leader's corners from the dot to the edge centre `end`: S1, straight, when the dot is within JOG_MAX of
    the edge's centre line; else S2, one 45-degree diagonal from the dot then a stub square out of the edge."""
    (x, y), (ex, ey) = dot, end
    nx, ny = NORMAL[edge]
    along = (x - ex) * nx + (y - ey) * ny
    across = (x - ex) if edge in ("top", "bottom") else (y - ey)
    if abs(across) <= JOG_MAX:
        return [dot, end]
    s = along - abs(across)
    return [dot, (ex + nx * s, ey + ny * s), end]


def _stub(path: list[tuple[float, float]]) -> float:
    """The length of a route's last run, the stub out of the plate."""
    (ax, ay), (bx, by) = path[-2], path[-1]
    return ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5


def _fillet(path: list[tuple[float, float]], r: float, n: int = FILLET_PTS) -> list[tuple[float, float]]:
    """An S2 route with its bend rounded: the arc of radius `r` tangent to both runs, sampled as `n` points."""
    if len(path) < 3:
        return list(path)
    (px, py), (bx, by), (ex, ey) = path
    lu, lv = math.hypot(px - bx, py - by), math.hypot(ex - bx, ey - by)
    if lu < 1e-6 or lv < 1e-6:
        return [path[0], path[-1]]
    ux, uy, vx, vy = (px - bx) / lu, (py - by) / lu, (ex - bx) / lv, (ey - by) / lv
    half = math.acos(max(-1.0, min(1.0, ux * vx + uy * vy))) / 2
    if half <= 1e-6 or abs(half - math.pi / 2) < 1e-6:
        return list(path)
    d = min(r / math.tan(half), lu, lv)
    rr = d * math.tan(half)
    t1, t2 = (bx + ux * d, by + uy * d), (bx + vx * d, by + vy * d)
    mx, my = ux + vx, uy + vy
    mn = math.hypot(mx, my)
    cx, cy = bx + mx / mn * rr / math.sin(half), by + my / mn * rr / math.sin(half)
    a1, a2 = math.atan2(t1[1] - cy, t1[0] - cx), math.atan2(t2[1] - cy, t2[0] - cx)
    da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
    arc = [(cx + rr * math.cos(a1 + da * i / (n - 1)), cy + rr * math.sin(a1 + da * i / (n - 1))) for i in range(n)]
    return [path[0], *arc, path[-1]]


def _tracked(measure: ImageDraw.ImageDraw, text: str, font: Any, track: float) -> float:
    """The width _draw_tracked draws `text` at: the shaped, kerned run plus `track` between its letters
    (typography.tracked_width; summing single letters dropped the kerning the plate is drawn with, QF-21)."""
    if not text:
        return 0.0
    return float(tracked_width(text, font, track))


class _Shape:
    """One way to set a label: its lines in one font, and the plate that holds them."""

    def __init__(self, lines: list[str], font: Any, track: float, fmt: str, measure: ImageDraw.ImageDraw):
        self.lines, self.font, self.track = lines, font, track
        self.step = round(LABEL_LEADING * font.size)
        self.cap = cap_height(font)
        self.widths = [_tracked(measure, ln, font, track) for ln in lines]
        self.tw = max(self.widths)
        self.pad = PLATE_PAD[fmt]
        self.radius = PLATE_R[fmt]
        lo = BAR_LEN[fmt][0]
        pw = max(self.tw + 2 * self.pad, lo + 2 * self.radius)
        self.pw = 2 * round(pw / 2)   # even (a whole-pixel centre), within 1 px of the tracked text + padding
        self.ph = 2 * math.ceil((2 * PLATE_PAD_CAP + self.cap + self.step * (len(lines) - 1)) / 2)


def label_sizes(fmt: str) -> list[int]:
    """The label sizes, largest first: LABEL_PX down to four fifths of it, LABEL_STEP px apart."""
    size0 = LABEL_PX[fmt]
    return list(range(size0, math.ceil(size0 * 0.8) - 1, -LABEL_STEP))


def _shapes(measure: ImageDraw.ImageDraw, label: str, fmt: str, top: int | None = None) -> list[_Shape]:
    """The label's plate shapes: on one line and on two balanced lines (at most LABEL_MAX_CHARS each), in
    Montserrat Bold tracked +0.04 em, at the largest size down to four fifths of LABEL_PX where either fits
    (`top`: the largest size tried, for a set stepped down together; QF-24)."""
    text = label.upper()
    max_w = LABEL_MAX_W[fmt]
    words = text.split()
    sizes = [s for s in label_sizes(fmt) if top is None or s <= top] or label_sizes(fmt)[-1:]
    for s in sizes:
        font = get_font("Montserrat", s, weight=700)
        track = LABEL_TRACK * s
        out = []
        if len(text) <= LABEL_MAX_CHARS and _tracked(measure, text, font, track) <= max_w:
            out.append([text])
        best = None
        for i in range(1, len(words)):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            wa, wb = _tracked(measure, a, font, track), _tracked(measure, b, font, track)
            fits = max(len(a), len(b)) <= LABEL_MAX_CHARS and max(wa, wb) <= max_w
            if fits and (best is None or max(wa, wb) < best[0]):
                best = (max(wa, wb), [a, b])
        if best:
            out.append(best[1])
        if out:
            return [_Shape(lines, font, track, fmt, measure) for lines in out]
    font = get_font("Montserrat", sizes[-1], weight=700)   # nothing fits: the smallest size, on two lines
    mid = max(1, len(words) // 2)
    lines = [" ".join(words[:mid]), " ".join(words[mid:])] if len(words) > 1 else [text]
    return [_Shape(lines, font, LABEL_TRACK * sizes[-1], fmt, measure)]


# Point accessors: (label, x, y[, area[, w_px, h_px[, kind[, box_px]]]]).
def _extent(p: tuple) -> tuple[float, float]:
    if len(p) > 5 and p[4] and p[5]:
        return float(p[4]), float(p[5])
    if len(p) > 3 and p[3]:
        s = (float(p[3]) * W * H) ** 0.5
        return s, s
    return 0.0, 0.0


def _kind(p: tuple) -> str:
    return p[6] if len(p) > 6 and p[6] in ("point", "extent") else "point"


def _fbox(p: tuple) -> tuple[float, float, float, float] | None:
    if len(p) > 7 and p[7] and len(p[7]) == 4:
        return tuple(float(v) for v in p[7])
    w, h = _extent(p)
    if w and h:
        return p[1] - w / 2, p[2] - h / 2, p[1] + w / 2, p[2] + h / 2
    return None


def trust(p: tuple) -> dict[str, Any]:
    """The point's trust record (plan's location spread, verify's box agreement and tier), {} when it has none."""
    return dict(p[8]) if len(p) > 8 and isinstance(p[8], dict) else {}


def evidence_tier(p: tuple) -> str:
    """The tier the location evidence alone gives, whatever the feature's kind: "B" when its locations spread over
    SPREAD_A or its check's box agreed by IoU under IOU_A, else "A" (a point with no trust record: "A")."""
    t = trust(p)
    return str(t.get("evidence_tier") or t.get("tier") or "A")


def tier(p: tuple, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> str:
    """The marker a point gets: "B", an outline round its box, for an extent feature or a trust tier B one (its
    locations spread 24-60 px, or its check's box agreed by IoU 0.5-0.6) whose box is at most EXTENT_MAX_SHARE of
    the frame; else "A", a point marker. A surface declutter moved to a point marker (its outline could not be
    laid out; trust "marker": "A", only on tier-A evidence) is "A"."""
    if trust(p).get("marker") == "A" and _kind(p) == "extent" and evidence_tier(p) == "A":
        return "A"
    b = _fbox(p)
    if (_kind(p) == "extent" or trust(p).get("tier") == "B") and b \
            and (b[2] - b[0]) * (b[3] - b[1]) <= EXTENT_MAX_SHARE * width * height:
        return "B"
    return "A"


def _grow(b: tuple, g: float) -> tuple[float, float, float, float]:
    return b[0] - g, b[1] - g, b[2] + g, b[3] + g


class _Feat:
    """One labelled feature as the layout sees it."""

    def __init__(self, k: int, p: tuple, width: int, height: int, fmt: str, loupe: bool, src_r: float):
        self.k, self.label, self.x, self.y = k, p[0], float(p[1]), float(p[2])
        self.tier = tier(p, width, height)
        self.loupe = bool(loupe) and self.tier == "A"
        self.surface = _kind(p) == "extent"
        w, h = _extent(p)
        self.a, self.b = w / 2, h / 2              # the extent's semi-axes (w, h); the rules below use the box
        # The located box (frame px), never an ellipse round the dot: the dot is a pointable point on the feature,
        # often far from its middle (a tower's crown, a portal's lintel), so a dot-centred extent misses the object.
        self.box = _fbox(p)
        self.dot_clear = SURFACE_DOT[fmt] if self.surface else 0.0
        self.fbox = self.box if self.tier == "B" else None
        self.mbox = _grow(self.fbox, EXTENT_GROW) if self.fbox else None      # the outline's centre line
        self.src_r = src_r if self.loupe else 0.0
        if self.loupe:
            self.r_mark, self.r_vis, self.r_gold = src_r + 3, src_r + 1, src_r + 1
        elif self.tier == "B":
            self.r_mark, self.r_vis, self.r_gold = 0.0, 0.0, 0.0
        else:
            self.r_mark, self.r_vis = DOT_DISC[fmt], DOT_DISC[fmt]
            self.r_gold = DOT_CORE[fmt] - LEADER_INTO_CORE

    def target(self, edge: str) -> tuple[float, float]:
        """Where the leader ends: the dot, or (tier B) the centre of the outline side facing the plate."""
        if self.mbox is None:
            return self.x, self.y
        x0, y0, x1, y1 = self.mbox
        return {"bottom": ((x0 + x1) / 2, y0), "top": ((x0 + x1) / 2, y1),
                "left": (x1, (y0 + y1) / 2), "right": (x0, (y0 + y1) / 2)}[edge]


def _leader(feat: _Feat, E: tuple[float, float], edge: str, fmt: str) -> dict | None:
    """The S1 or S2 leader from `feat`'s target to the edge centre E, or None when neither fits."""
    P = feat.target(edge)
    nx, ny = NORMAL[edge]
    along = (P[0] - E[0]) * nx + (P[1] - E[1]) * ny
    across = (P[0] - E[0]) if edge in ("top", "bottom") else (P[1] - E[1])
    if along <= 0:
        return None
    pts = _route(P, E, edge)
    if len(pts) == 2:
        kind = "S1"
    else:
        if abs(across) < JOG_SNAP or along - abs(across) < STUB_MIN[fmt] or abs(across) * 2 ** 0.5 < DIAG_MIN:
            return None
        kind = "S2"
        r = FILLET_R[fmt]
        diag = abs(across) * 2 ** 0.5
        if diag - r * math.tan(math.pi / 8) - feat.r_vis < AIM_MIN:
            return None
    total = _length(pts)
    vis = total - feat.r_vis
    lo, _, _, hi = LEADER_LEN[fmt]
    if not lo - 1e-6 <= vis <= hi + 1e-6:
        return None
    sign = None
    if kind == "S2":
        sign = (int(math.copysign(1, P[0] - pts[1][0])), int(math.copysign(1, P[1] - pts[1][1])))
    return {"pts": pts, "route": kind, "vis": vis, "target": P, "diag": sign}


def _unit_dims(shape: _Shape, edge: str, feat: _Feat, lr: int) -> tuple[int, int]:
    if not feat.loupe:
        return shape.pw, shape.ph
    if edge in ("top", "bottom"):
        return 2 * math.ceil(max(shape.pw, 2 * lr) / 2), shape.ph + LOUPE_GAP + 2 * lr
    return shape.pw + LOUPE_GAP + 2 * lr, 2 * math.ceil(max(shape.ph, 2 * lr) / 2)


def _build(feat: _Feat, si: int, shape: _Shape, edge: str, E: tuple[float, float], fmt: str, lr: int,
           bounds: tuple) -> dict | None:
    """A candidate placement: the unit (plate, and loupe when it has one) whose `edge` centre is E."""
    uw, uh = _unit_dims(shape, edge, feat, lr)
    ex, ey = E
    if edge == "bottom":
        u = (ex - uw / 2, ey - uh, ex + uw / 2, ey)
    elif edge == "top":
        u = (ex - uw / 2, ey, ex + uw / 2, ey + uh)
    elif edge == "left":
        u = (ex, ey - uh / 2, ex + uw, ey + uh / 2)
    else:
        u = (ex - uw, ey - uh / 2, ex, ey + uh / 2)
    bx0, by0, bx1, by1 = bounds
    if u[0] < bx0 or u[2] > bx1 or u[1] < by0 or u[3] > by1:
        return None
    pw, ph = shape.pw, shape.ph
    ucx, ucy = (u[0] + u[2]) / 2, (u[1] + u[3]) / 2
    loupe_at = None
    if not feat.loupe:
        plate = u
    elif edge == "bottom":
        plate, loupe_at = (ucx - pw / 2, u[3] - ph, ucx + pw / 2, u[3]), (ucx, u[1] + lr)
    elif edge == "top":
        plate, loupe_at = (ucx - pw / 2, u[1], ucx + pw / 2, u[1] + ph), (ucx, u[3] - lr)
    elif edge == "left":
        plate, loupe_at = (u[0], ucy - ph / 2, u[0] + pw, ucy + ph / 2), (u[2] - lr, ucy)
    else:
        plate, loupe_at = (u[2] - pw, ucy - ph / 2, u[2], ucy + ph / 2), (u[0] + lr, ucy)
    # which edges the direction to the dot allows: 0 deg is straight down
    pcx, pcy = (plate[0] + plate[2]) / 2, (plate[1] + plate[3]) / 2
    phi = math.degrees(math.atan2(abs(feat.x - pcx), abs(feat.y - pcy)))
    if edge in ("top", "bottom") and phi >= 60:
        return None
    if edge in ("left", "right") and phi <= 40:
        return None
    lead = _leader(feat, E, edge, fmt)
    if lead is None:
        return None
    pts = lead["pts"]
    vis = _trim(pts, feat.r_vis)
    segs = [(a[0], a[1], b[0], b[1]) for a, b in pairwise(vis)]
    if len(segs) == 1:
        segs.append(segs[0])
    return {"k": feat.k, "si": si, "shape": shape, "edge": edge, "end": E, "unit": tuple(map(float, u)),
            "plate": tuple(map(float, plate)), "loupe_at": loupe_at, "path": pts, "route": lead["route"],
            "vis": lead["vis"], "target": lead["target"], "diag": lead["diag"], "segs": segs[:2]}


def _candidates(feat: _Feat, shapes: list[_Shape], fmt: str, lr: int, bounds: tuple,
                len_step: int = FINE_STEP) -> list[dict]:
    """Every placement worth scoring: each shape, each edge, each leader length (LEADER_TRIES and every `len_step`
    px between the shortest and the longest: FINE_STEP, or STEP_DOWN_FINE on a retry), each slide along the edge
    (SLIDES and every FINE_STEP px)."""
    out, seen = [], set()
    bx0, by0, bx1, by1 = bounds
    lo, hi = LEADER_TRIES[fmt][0], LEADER_TRIES[fmt][-1]
    lengths = sorted({*LEADER_TRIES[fmt], *range(lo, hi + 1, len_step)})
    for si, shape in enumerate(shapes):
        for edge in EDGES:
            P = feat.target(edge)
            nx, ny = NORMAL[edge]
            tb = edge in ("top", "bottom")
            uw, uh = _unit_dims(shape, edge, feat, lr)
            span = (uw if tb else uh) / 2
            offs = [sl * 2 * span for sl in SLIDES]
            offs += [d * FINE_STEP for d in range(-int(span // FINE_STEP), int(span // FINE_STEP) + 1)]
            for L in lengths:
                along = L + feat.r_vis
                for off in offs:
                    if tb:
                        ex, ey = round(P[0] + off), round(P[1] - ny * along)
                        ex = min(max(ex - uw / 2, bx0), bx1 - uw) + uw / 2
                        across = ex - P[0]
                        if JOG_MAX < abs(across) < JOG_SNAP:   # a visible jog is never drawn: slide it out
                            ex = round(P[0])
                    else:
                        ex, ey = round(P[0] - nx * along), round(P[1] + off)
                        ey = min(max(ey - uh / 2, by0), by1 - uh) + uh / 2
                        across = ey - P[1]
                        if JOG_MAX < abs(across) < JOG_SNAP:
                            ey = round(P[1])
                    key = (si, edge, ex, ey)
                    if key in seen:
                        continue
                    seen.add(key)
                    c = _build(feat, si, shape, edge, (ex, ey), fmt, lr, bounds)
                    if c is not None:
                        out.append(c)
    return out


# ---------------------------------------------------------------- vectorised geometry for the hard limits
def _np():
    import numpy as np
    return np


def _pt_seg(px, py, ax, ay, bx, by):
    np = _np()
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    u = np.clip(np.where(l2 > 0, ((px - ax) * dx + (py - ay) * dy) / np.where(l2 > 0, l2, 1.0), 0.0), 0.0, 1.0)
    return np.hypot(px - (ax + u * dx), py - (ay + u * dy))


def _pt_rect(px, py, x0, y0, x1, y1):
    np = _np()
    dx = np.maximum(np.maximum(x0 - px, 0.0), px - x1)
    dy = np.maximum(np.maximum(y0 - py, 0.0), py - y1)
    return np.hypot(dx, dy)


def _rect_rect(a, b):
    np = _np()
    dx = np.maximum(np.maximum(b[0] - a[2], a[0] - b[2]), 0.0)
    dy = np.maximum(np.maximum(b[1] - a[3], a[1] - b[3]), 0.0)
    return np.hypot(dx, dy)


def _seg_rect_hit(ax, ay, bx, by, x0, y0, x1, y1):
    np = _np()
    ax, ay, bx, by, x0, y0, x1, y1 = np.broadcast_arrays(ax, ay, bx, by, x0, y0, x1, y1)
    dx, dy = bx - ax, by - ay
    lo, hi = np.zeros(ax.shape), np.ones(ax.shape)
    ok = np.ones(ax.shape, bool)
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        zero = np.abs(p) < 1e-12
        ok &= ~(zero & (q < 0))
        r = np.where(zero, 0.0, q / np.where(zero, 1.0, p))
        lo = np.where(~zero & (p < 0), np.maximum(lo, r), lo)
        hi = np.where(~zero & (p > 0), np.minimum(hi, r), hi)
    return ok & (lo <= hi)


def _seg_rect(ax, ay, bx, by, x0, y0, x1, y1):
    np = _np()
    d = np.minimum.reduce([_pt_rect(ax, ay, x0, y0, x1, y1) + 0 * bx, _pt_rect(bx, by, x0, y0, x1, y1) + 0 * ax,
                           _pt_seg(x0, y0, ax, ay, bx, by), _pt_seg(x1, y0, ax, ay, bx, by),
                           _pt_seg(x0, y1, ax, ay, bx, by), _pt_seg(x1, y1, ax, ay, bx, by)])
    return np.where(_seg_rect_hit(ax, ay, bx, by, x0, y0, x1, y1), 0.0, d)


def _seg_seg(a0, a1, a2, a3, b0, b1, b2, b3):
    np = _np()

    def orient(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)
    o1, o2 = orient(a0, a1, a2, a3, b0, b1), orient(a0, a1, a2, a3, b2, b3)
    o3, o4 = orient(b0, b1, b2, b3, a0, a1), orient(b0, b1, b2, b3, a2, a3)
    hit = (o1 * o2 < 0) & (o3 * o4 < 0)
    d = np.minimum.reduce([_pt_seg(b0, b1, a0, a1, a2, a3), _pt_seg(b2, b3, a0, a1, a2, a3),
                           _pt_seg(a0, a1, b0, b1, b2, b3), _pt_seg(a2, a3, b0, b1, b2, b3)])
    return np.where(hit, 0.0, d)


class _Ctx:
    """What every hard limit is checked against: the features, where the push takes them at each sampled t, the
    text box, and (for the busyness cost) the photo's edges."""

    def __init__(self, feats: list[_Feat], width: int, height: int, bounds: tuple, fmt: str, ground: Any = None):
        np = _np()
        self.feats, self.width, self.height, self.bounds, self.fmt = feats, width, height, bounds, fmt
        self.T = np.array(SAMPLES)
        f = PUSH_IN * np.clip(self.T / CLIP_SECONDS, 0.0, 1.0)
        self.z = 1.0 + f
        self.disp = [np.stack([(ft.x - width / 2) * f, (ft.y - height / 2) * f], axis=1) for ft in feats]
        self.integral = None
        if ground is not None:
            import cv2
            g = np.asarray(ground.convert("L"), dtype=np.float32)
            mag = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
            self.p90 = float(np.percentile(mag, 90)) or 1.0
            self.integral = (cv2.integral(mag.astype(np.float64)), cv2.integral((mag.astype(np.float64)) ** 2))

    def box_at(self, b: tuple, i: int, grow: float) -> tuple:
        """Box `b` (frame px at t = 0) where the push takes it at sample i, grown `grow`."""
        z, cx, cy = self.z[i], self.width / 2, self.height / 2
        return _grow((cx + (b[0] - cx) * z, cy + (b[1] - cy) * z, cx + (b[2] - cx) * z, cy + (b[3] - cy) * z), grow)

    def fbox_at(self, ft: _Feat, i: int, grow: float) -> tuple:
        return self.box_at(ft.fbox, i, grow)

    @staticmethod
    def whole(ft: _Feat) -> bool:
        """Whether plates and other leaders keep out of `ft`'s whole box: an object (either tier) or an outlined
        feature. Only a point-marked surface lets a plate over its outer part."""
        return ft.box is not None and (not ft.surface or ft.fbox is not None)

    def inner(self, ft: _Feat, i: int) -> tuple[float, float, float, float]:
        """A point-marked surface's inner half at sample i: the ellipse inscribed in its box at SURFACE_INNER of
        the box's half sides, centred on the box (not on the dot), as (cx, cy, a, b)."""
        b = self.box_at(ft.box, i, 0.0)
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(1e-6, (b[2] - b[0]) / 2 * SURFACE_INNER),
                max(1e-6, (b[3] - b[1]) / 2 * SURFACE_INNER))

    def plate_clear(self, U: Any, ft: _Feat, i: int, mx: float, my: float) -> Any:
        """Which units (plate and loupe boxes) keep off feature `ft` (at its dot (mx, my) at sample i): an object's
        or an outlined feature's whole located box grown OWN_CLEAR (the box, stricter than the ellipse inscribed in
        it); a point-marked surface's inner half (centred on its box) and SURFACE_DOT px round its dot."""
        np = _np()
        if self.whole(ft):
            return _rect_rect(U.T, self.box_at(ft.box, i, OWN_CLEAR)) > 0
        ok = np.ones(len(U), bool)
        if ft.dot_clear:
            ok &= _pt_rect(mx, my, U[:, 0], U[:, 1], U[:, 2], U[:, 3]) >= ft.dot_clear
        if ft.box is not None:
            cx, cy, a, b = self.inner(ft, i)
            nx = np.clip(0.0, U[:, 0] - cx, U[:, 2] - cx) / a
            ny = np.clip(0.0, U[:, 1] - cy, U[:, 3] - cy) / b
            ok &= np.hypot(nx, ny) >= 1.0
        return ok

    def busy(self, box: tuple) -> float:
        """The standard deviation of the edge magnitude under `box` over the frame's 90th percentile, at most 1."""
        if self.integral is None:
            return 0.0
        s1, s2 = self.integral
        x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
        x1, y1 = min(self.width, int(box[2])), min(self.height, int(box[3]))
        n = (x1 - x0) * (y1 - y0)
        if n <= 0:
            return 0.0
        a = s1[y1, x1] - s1[y0, x1] - s1[y1, x0] + s1[y0, x0]
        b = s2[y1, x1] - s2[y0, x1] - s2[y1, x0] + s2[y0, x0]
        var = max(0.0, b / n - (a / n) ** 2)
        return min(1.0, var ** 0.5 / self.p90)

    def static(self, c: dict) -> float:
        """A candidate's own cost: its length, an S2 bend, a plate below its dot, busy ground, the length band."""
        _, plo, phi_, _ = LEADER_LEN[self.fmt]
        vis = c["vis"]
        cost = vis / 100 + (1.5 if c["route"] == "S2" else 0.0)
        ft = self.feats[c["k"]]
        if (c["plate"][1] + c["plate"][3]) / 2 > ft.y:
            cost += 0.3
        cost += 3.0 * self.busy(c["plate"])
        out = max(0.0, plo - vis, vis - phi_)
        cost += 0.5 * out / 50
        return cost

    @staticmethod
    def arrays(cs: list[dict]) -> tuple:
        np = _np()
        U = np.array([c["unit"] for c in cs], dtype=float).reshape(-1, 4)
        PL = np.array([c["plate"] for c in cs], dtype=float).reshape(-1, 4)
        S = np.array([c["segs"] for c in cs], dtype=float).reshape(-1, 2, 4)
        VL = np.array([c["vis"] for c in cs], dtype=float)
        return U, PL, S, VL

    def single_ok(self, k: int, cs: list[dict]) -> Any:
        """The hard limits one candidate must hold alone, at every sampled t."""
        np = _np()
        U0, PL0, S0, _ = self.arrays(cs)
        ok = np.ones(len(cs), bool)
        fk = self.feats[k]
        bx0, by0, bx1, by1 = self.bounds
        for i in range(len(self.T)):
            dx, dy = self.disp[k][i]
            sh = np.array([dx, dy, dx, dy])
            U, PL, S = U0 + sh, PL0 + sh, S0 + sh
            ok &= (U[:, 0] >= bx0 - 1e-6) & (U[:, 2] <= bx1 + 1e-6) & (U[:, 1] >= by0 - 1e-6) & (U[:, 3] <= by1 + 1e-6)
            cx, cy = fk.x + dx, fk.y + dy
            d_own = _pt_rect(cx, cy, PL[:, 0], PL[:, 1], PL[:, 2], PL[:, 3])
            for j, fj in enumerate(self.feats):
                mx, my = fj.x + self.disp[j][i][0], fj.y + self.disp[j][i][1]
                ok &= self.plate_clear(U, fj, i, mx, my)
                if fj.mbox is not None:
                    mb = _grow(fj.mbox, 3)
                    mb = (mb[0] + self.disp[j][i][0], mb[1] + self.disp[j][i][1],
                          mb[2] + self.disp[j][i][0], mb[3] + self.disp[j][i][1])
                    ok &= _rect_rect(U.T, mb) >= LABEL_GAP
                else:
                    ok &= _pt_rect(mx, my, U[:, 0], U[:, 1], U[:, 2], U[:, 3]) - fj.r_mark >= LABEL_GAP
                if j == k:
                    continue
                for s in range(2):
                    ax, ay, qx, qy = S[:, s, 0], S[:, s, 1], S[:, s, 2], S[:, s, 3]
                    if fj.mbox is not None:
                        ok &= _seg_rect(ax, ay, qx, qy, *mb) >= LEADER_MARK_GAP
                        ok &= _seg_rect(ax, ay, qx, qy, *self.fbox_at(fj, i, EXTENT_CLEAR)) > 0
                    else:
                        ok &= _pt_seg(mx, my, ax, ay, qx, qy) - fj.r_mark >= LEADER_MARK_GAP
                        if self.whole(fj):          # never into another object's box
                            ok &= _seg_rect(ax, ay, qx, qy, *self.box_at(fj.box, i, EXTENT_CLEAR)) > 0
                        elif fj.box is not None:    # nor into a point-marked surface's inner half
                            ecx, ecy, a, b = self.inner(fj, i)
                            ok &= _pt_seg(0.0, 0.0, (ax - ecx) / a, (ay - ecy) / b,
                                          (qx - ecx) / a, (qy - ecy) / b) >= 1.0
                d_j = _pt_rect(mx, my, PL[:, 0], PL[:, 1], PL[:, 2], PL[:, 3])
                ok &= d_own <= OWN_RATIO * d_j + 1e-6
            if not ok.any():
                break
        return ok

    def pair_ok(self, k: int, ca: list[dict], j: int, cb: list[dict]) -> Any:
        """The hard limits two candidates must hold together, at every sampled t: (len(ca), len(cb)) booleans."""
        np = _np()
        Ua0, _, Sa0, VLa = self.arrays(ca)
        Ub0, _, Sb0, VLb = self.arrays(cb)
        ok = np.ones((len(ca), len(cb)), bool)
        same_len = np.abs(VLa[:, None] - VLb[None, :]) <= 8
        cos_min = math.cos(math.radians(MIN_ANGLE))
        for i in range(len(self.T)):
            da, db = self.disp[k][i], self.disp[j][i]
            sa, sb = np.array([*da, *da]), np.array([*db, *db])
            Ua, Ub = (Ua0 + sa)[:, None, :], (Ub0 + sb)[None, :, :]
            g = LABEL_GAP
            ok &= ((Ua[..., 2] + g <= Ub[..., 0]) | (Ub[..., 2] + g <= Ua[..., 0])
                   | (Ua[..., 3] + g <= Ub[..., 1]) | (Ub[..., 3] + g <= Ua[..., 1]))
            Sa, Sb = Sa0 + sa, Sb0 + sb
            for s in range(2):
                a = [Sa[:, s, c][:, None] for c in range(4)]
                ok &= _seg_rect(*a, Ub[..., 0], Ub[..., 1], Ub[..., 2], Ub[..., 3]) >= LABEL_GAP
                b = [Sb[:, s, c][None, :] for c in range(4)]
                ok &= _seg_rect(*b, Ua[..., 0], Ua[..., 1], Ua[..., 2], Ua[..., 3]) >= LABEL_GAP
            for s in range(2):
                a = [Sa[:, s, c][:, None] for c in range(4)]
                ua = (a[2] - a[0], a[3] - a[1])
                for r in range(2):
                    b = [Sb[:, r, c][None, :] for c in range(4)]
                    d = _seg_seg(*a, *b)
                    ok &= d >= LEADER_GAP
                    ub = (b[2] - b[0], b[3] - b[1])
                    na, nb = np.hypot(*ua), np.hypot(*ub)
                    cos = np.abs(ua[0] * ub[0] + ua[1] * ub[1]) / np.maximum(na * nb, 1e-9)
                    near = d < NEAR
                    parallel = cos > 0.9999
                    ok &= ~(near & (cos > cos_min) & ~parallel)
                    ok &= ~(parallel & (d < PARALLEL_GAP) & ~same_len)
            if not ok.any():
                break
        return ok

    def plates_ok(self, k: int, ca: list[dict], j: int, cb: list[dict]) -> Any:
        """Only the plate-to-plate gap of pair_ok."""
        np = _np()
        Ua0, Ub0 = self.arrays(ca)[0], self.arrays(cb)[0]
        ok = np.ones((len(ca), len(cb)), bool)
        g = LABEL_GAP
        for i in range(len(self.T)):
            da, db = self.disp[k][i], self.disp[j][i]
            Ua, Ub = (Ua0 + np.array([*da, *da]))[:, None, :], (Ub0 + np.array([*db, *db]))[None, :, :]
            ok &= ((Ua[..., 2] + g <= Ub[..., 0]) | (Ub[..., 2] + g <= Ua[..., 0])
                   | (Ua[..., 3] + g <= Ub[..., 1]) | (Ub[..., 3] + g <= Ua[..., 1]))
        return ok

    @staticmethod
    def pair_cost(ca: list[dict], cb: list[dict]) -> Any:
        """Soft costs between two candidates: leader lengths more than 2:1 apart; diagonals that neither share a
        direction nor mirror one another."""
        np = _np()
        va = np.array([c["vis"] for c in ca])[:, None]
        vb = np.array([c["vis"] for c in cb])[None, :]
        cost = np.where(np.maximum(va, vb) > 2 * np.minimum(va, vb), 0.5, 0.0)
        da = [c["diag"] for c in ca]
        db = [c["diag"] for c in cb]
        ax = np.array([d[0] if d else 0 for d in da])[:, None]
        ay = np.array([d[1] if d else 0 for d in da])[:, None]
        bx = np.array([d[0] if d else 0 for d in db])[None, :]
        by = np.array([d[1] if d else 0 for d in db])[None, :]
        both = (ax != 0) & (bx != 0)
        cost = cost + np.where(both & (ax != bx) & (ay != by), 1.0, 0.0)
        return cost


def _set_cost(ctx: _Ctx, chosen: list[dict], spoken: list[int]) -> float:
    """The costs only a whole frame has: the plates' centroid off centre, mixed attachment kinds, screen order
    against spoken order."""
    cost = 0.0
    area = [(c["plate"][2] - c["plate"][0]) * (c["plate"][3] - c["plate"][1]) for c in chosen]
    if sum(area) > 0:
        gx = sum(a * (c["plate"][0] + c["plate"][2]) / 2 for a, c in zip(area, chosen)) / sum(area)
        gy = sum(a * (c["plate"][1] + c["plate"][3]) / 2 for a, c in zip(area, chosen)) / sum(area)
        cost += math.hypot(gx - ctx.width / 2, gy - ctx.height / 2) / min(ctx.width, ctx.height)
    kinds = {c["edge"] in ("top", "bottom") for c in chosen}
    if len(kinds) > 1:
        cost += 0.8
    if len(chosen) > 1:
        screen = [c["k"] for c in sorted(chosen, key=lambda c: (c["plate"][1], c["plate"][0]))]
        said = [k for k in spoken if k in {c["k"] for c in chosen}]
        if screen != said:
            cost += 0.5
    return cost


def _moved(ctx: _Ctx, c: dict, dx: float, dy: float, lr: int) -> dict | None:
    ft = ctx.feats[c["k"]]
    n = _build(ft, c["si"], c["shape"], c["edge"], (c["end"][0] + dx, c["end"][1] + dy), ctx.fmt, lr, ctx.bounds)
    if n is None:
        return None
    n["static"] = ctx.static(n)
    return n


def _valid_set(ctx: _Ctx, chosen: list[dict]) -> bool:
    for c in chosen:
        if not ctx.single_ok(c["k"], [c])[0]:
            return False
    for a, b in combinations(chosen, 2):
        if not ctx.pair_ok(a["k"], [a], b["k"], [b])[0, 0]:
            return False
    return True


def _snap(ctx: _Ctx, chosen: list[dict], lr: int) -> tuple[list[dict], int]:
    """Plates whose tops (or bottoms) are within ROW_SNAP px share one line, and whose centres or matching sides
    are within COL_SNAP px share one column, where every hard limit still holds; each snap that cannot be made
    is counted (it costs 1.0)."""
    chosen = list(chosen)
    failed = 0
    for ia, ib in combinations(range(len(chosen)), 2):
        for axis in ("row", "col"):
            a, b = chosen[ia], chosen[ib]
            pa, pb = a["plate"], b["plate"]
            if axis == "row":
                opts = [(pa[1] - pb[1], 0), (pa[3] - pb[3], 0)]
                lim = ROW_SNAP
            else:
                opts = [((pa[0] + pa[2]) / 2 - (pb[0] + pb[2]) / 2, 1), (pa[0] - pb[0], 1), (pa[2] - pb[2], 1)]
                lim = COL_SNAP
            gap = next((g for g, _ in opts if abs(g) <= lim), None)
            if gap is None or abs(gap) < 0.5 or any(abs(g) < 0.5 for g, _ in opts):
                continue
            done = False
            for who, delta in ((ib, gap), (ia, -gap)):
                c = chosen[who]
                n = _moved(ctx, c, *((0.0, delta) if axis == "row" else (delta, 0.0)), lr)
                if n is None:
                    continue
                trial = list(chosen)
                trial[who] = n
                if _valid_set(ctx, trial):
                    chosen, done = trial, True
                    break
            failed += 0 if done else 1
    return chosen, failed


def _total(ctx: _Ctx, chosen: list[dict], spoken: list[int]) -> float:
    cost = sum(c["static"] for c in chosen)
    for a, b in combinations(chosen, 2):
        cost += float(ctx.pair_cost([a], [b])[0, 0])
    return cost + _set_cost(ctx, chosen, spoken)


def layout_labels(points: list[tuple], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                  loupe: bool = True, push: Push | None = None, ground: Any = None, mag: float | None = None,
                  spoken: list[int] | None = None, step_down: bool = True) -> list[dict[str, Any]]:
    """Where each label goes (_layout_set). On 9:16 a set with no valid layout on the reviewed grid at full size is
    tried again (`step_down`) on the finer leader grid, then with its labels stepped down together (STEP_DOWN_FINE,
    LABEL_STEP; QF-24); the first valid set is kept. When none is, the set keeps its conflicts (declutter drops or
    refuses it; render never draws one): the largest attempt whose plates still stand LABEL_GAP apart, else the
    full-size one."""
    first = _layout_set(points, width, height, loupe, ground, mag, spoken)
    if not step_down or width > height or not any(lab["conflict"] for lab in first):
        return first
    labs = _stepped_layout(points, width, height, loupe, ground, mag, spoken)
    if labs is not None:
        return labs
    if _plates_apart(first):
        return first
    for top in label_sizes(_fmt(width, height)):
        labs = _layout_set(points, width, height, loupe, ground, mag, spoken, top=top, len_step=STEP_DOWN_FINE)
        if _plates_apart(labs):
            return labs
    return first


def _stepped_layout(points: list[tuple], width: int, height: int, loupe: bool, ground: Any, mag: float | None,
                    spoken: list[int] | None) -> list[dict[str, Any]] | None:
    """The first valid layout of the set on the finer leader grid, at full size and then stepped down together
    (QF-24), or None."""
    for top in label_sizes(_fmt(width, height)):
        labs = _layout_set(points, width, height, loupe, ground, mag, spoken, top=top, len_step=STEP_DOWN_FINE,
                           relax=False)
        if labs is not None:
            return labs
    return None


def _plates_apart(labs: list[dict[str, Any]]) -> bool:
    """Whether every two plates of a layout stand LABEL_GAP apart."""
    return not any(_overlaps(tuple(a["box"]), tuple(b["box"]), LABEL_GAP) for a, b in combinations(labs, 2))


def _layout_set(points: list[tuple], width: int, height: int, loupe: bool, ground: Any, mag: float | None,
                spoken: list[int] | None, top: int | None = None, len_step: int = FINE_STEP,
                relax: bool = True) -> list[dict[str, Any]] | None:
    """Where each label goes, in the order of `points` (each (label, x, y[, area[, w, h[, kind[, box]]]])): its
    lines and font, its plate box (inside text_box at every sampled t), the edge its leader meets and that edge's
    centre (`end`), its S1/S2 route and text anchor. Every label's candidates (2 plate shapes x 4 edges x 6 leader
    lengths x 5 slides, kept when the route is S1 or S2) are checked against the hard limits at every sampled t,
    pairwise compatibility is tabled once, and the cheapest valid set is found exactly by branch and bound. When
    no valid set exists the labels that break a limit are marked `conflict` (declutter drops them; render refuses
    them). `loupe`: False when the photo has no native detail to magnify (see loupe_mag); `mag` its magnification.
    `ground`: the frame-sized photo, for the busyness cost. `spoken`: the label indices in spoken order. `top`: the
    largest label size; `len_step`: the leader-length grid; `relax` False returns None instead of a conflict set."""
    np = _np()
    fmt = _fmt(width, height)
    bounds = text_box(width, height)
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lr = _loupe_r(width, height)
    src_r = lr / (mag or LOUPE_MAG)
    li = loupe_index(points) if loupe else None
    feats = [_Feat(k, p, width, height, fmt, k == li, src_r) for k, p in enumerate(points)]
    ctx = _Ctx(feats, width, height, bounds, fmt, ground)
    spoken = list(spoken) if spoken else list(range(len(points)))
    shapes = [_shapes(measure, p[0], fmt, top) for p in points]
    cands, valid = [], []
    def scored(k: int, ft: _Feat) -> tuple[list[dict], Any]:
        cs = _candidates(ft, shapes[k], fmt, lr, bounds, len_step)
        for c in cs:
            c["static"] = ctx.static(c)
        cs.sort(key=lambda c: c["static"])
        ok = ctx.single_ok(k, cs) if cs else np.zeros(0, bool)
        # the cheapest MAX_CANDS valid ones, and (for a conflict placement) the cheapest invalid ones after them
        good = [c for c, o in zip(cs, ok) if o][:MAX_CANDS]
        bad = [c for c, o in zip(cs, ok) if not o][:MAX_CANDS]
        return good + bad, np.array([True] * len(good) + [False] * len(bad), bool)

    for k, ft in enumerate(feats):
        cs, ok = scored(k, ft)
        if ft.loupe and not ok.any():   # no room for the inset: the label without it
            ft.loupe, ft.src_r = False, 0.0
            ft.r_mark, ft.r_vis = DOT_DISC[fmt], DOT_DISC[fmt]
            ft.r_gold = DOT_CORE[fmt] - LEADER_INTO_CORE
            cs, ok = scored(k, ft)
        cands.append(cs)
        valid.append([i for i in range(len(cs)) if ok[i]])
    n = len(points)
    compat: dict[tuple[int, int], Any] = {}
    pcost: dict[tuple[int, int], Any] = {}
    for k, j in combinations(range(n), 2):
        a = [cands[k][i] for i in valid[k]]
        b = [cands[j][i] for i in valid[j]]
        if a and b:
            compat[(k, j)] = ctx.pair_ok(k, a, j, b)
            pcost[(k, j)] = ctx.pair_cost(a, b)
    best = _exact(ctx, cands, valid, compat, pcost, list(range(n)), spoken, lr)
    conflict = [False] * n
    if best is None:
        if not relax:
            return None
        chosen, conflict = _relaxed(ctx, cands, valid, compat, pcost, feats, shapes, fmt, lr, bounds, spoken)
        set_cost = float("inf")
    else:
        set_cost, chosen = best
    return [_describe(c, feats[c["k"]], conflict[c["k"]], set_cost, fmt) for c in sorted(chosen, key=lambda c: c["k"])]


def _exact(ctx: _Ctx, cands: list, valid: list, compat: dict, pcost: dict, keys: list[int], spoken: list[int],
           lr: int) -> tuple[float, list[dict]] | None:
    """The cheapest valid set for the labels `keys`: candidates in cost order, branch and bound for the SNAP_TOP
    cheapest valid sets before alignment (static and pairwise costs are a lower bound; the frame's own costs are
    added at each complete set), then the alignment snaps tried on those, cheapest first, until a set's cost before
    snapping reaches the best total after it. (Snapping every complete set made a fine candidate grid's near-equal
    sets cost a minute; review 2026-11-03.)"""
    if not all(valid[k] for k in keys):
        return None
    order = sorted(keys, key=lambda k: len(valid[k]))
    floor = [min(cands[k][i]["static"] for i in valid[k]) for k in order]
    top: list[tuple[float, int, list[dict]]] = []        # a max-heap (negated) of the cheapest complete sets
    seq = count()

    def bound() -> float:
        return -top[0][0] if len(top) >= SNAP_TOP else float("inf")

    def search(depth: int, pick: dict[int, int], cost: float) -> None:
        if cost + sum(floor[depth:]) >= bound():
            return
        if depth == len(order):
            chosen = [cands[k][valid[k][pick[k]]] for k in sorted(pick)]
            pre = cost + _set_cost(ctx, chosen, spoken)
            if pre < bound():
                heapq.heappush(top, (-pre, next(seq), chosen))
                if len(top) > SNAP_TOP:
                    heapq.heappop(top)
            return
        k = order[depth]
        rest = sum(floor[depth + 1:])
        for vi, ci in enumerate(valid[k]):
            c = cands[k][ci]
            if cost + c["static"] + rest >= bound():
                break
            extra = 0.0
            fine = True
            for j, vj in pick.items():
                key = (min(j, k), max(j, k))
                ij = (vi, vj) if k < j else (vj, vi)
                if not compat[key][ij]:
                    fine = False
                    break
                extra += float(pcost[key][ij])
            if not fine:
                continue
            pick[k] = vi
            search(depth + 1, pick, cost + c["static"] + extra)
            del pick[k]

    search(0, {}, 0.0)
    best: tuple[float, list[dict]] | None = None
    for neg, _, chosen in sorted(top, key=lambda e: (-e[0], e[1])):
        if best is not None and -neg >= best[0]:
            break
        snapped, failed = _snap(ctx, chosen, lr)
        total = _total(ctx, snapped, spoken) + 1.0 * failed
        if best is None or total < best[0]:
            best = (total, snapped)
    return best


def _relaxed(ctx: _Ctx, cands: list, valid: list, compat: dict, pcost: dict, feats: list, shapes: list, fmt: str,
             lr: int, bounds: tuple, spoken: list[int]) -> tuple[list[dict], list[bool]]:
    """No valid set: the largest subset that has one (the cheapest among those, a tie to the labels said
    earlier), the others placed where they break the fewest limits (their plates kept off the placed ones first)
    and marked as conflicts."""
    n = len(cands)
    pinned = [k for k in range(n) if not cands[k]]
    for k in pinned:   # cannot fit at all: pinned inside the box, wherever it keeps clear of the other plates
        feats[k].loupe = False
        cands[k] = _grid(feats[k], shapes[k][0], bounds)
    kept: list[dict] = []
    for size in range(n - 1, 0, -1):
        found = None
        for keys in combinations(range(n), size):
            got = _exact(ctx, cands, valid, compat, pcost, list(keys), spoken, lr)
            if got is not None and (found is None or got[0] < found[0] - 1e-9):
                found = got
        if found is not None:
            kept = found[1]
            break
    chosen = list(kept)
    conflict = [True] * n
    for c in kept:
        conflict[c["k"]] = False
    placed = {c["k"] for c in kept}
    for k in range(n):
        if k in placed:
            continue
        cs = cands[k] if k in pinned else cands[k][:60] + _grid(feats[k], shapes[k][0], bounds, feats[k].loupe)
        single = ctx.single_ok(k, cs)
        score = [(0 if single[i] else 1, 0, 0, cs[i]["static"]) for i in range(len(cs))]
        for o in chosen:
            full = ctx.pair_ok(k, cs, o["k"], [o])[:, 0]
            plates = ctx.plates_ok(k, cs, o["k"], [o])[:, 0]
            score = [(a, b + (0 if plates[i] else 1), c + (0 if full[i] else 1), d)
                     for i, (a, b, c, d) in enumerate(score)]
        i = min(range(len(cs)), key=lambda i: (score[i][1], score[i][0], score[i][2], score[i][3]))
        chosen.append(cs[i])
    return chosen, conflict


def _grid(ft: _Feat, sh: _Shape, bounds: tuple, skip: bool = False) -> list[dict]:
    """Fallback plates on a 24 px grid inside the box, each with a straight leader (a conflict placement)."""
    if skip:
        return []
    out = []
    bx0, by0, bx1, by1 = bounds
    for y0 in range(int(by1 - sh.ph), int(by0) - 1, -24):
        for x0 in range(int(bx0), int(bx1 - sh.pw) + 1, 24):
            box = (float(x0), float(y0), float(x0 + sh.pw), float(y0 + sh.ph))
            edge = "bottom" if ft.y >= box[3] else "top"
            E = _attach(box, edge)
            pts = [ft.target(edge), E]
            out.append({"k": ft.k, "si": 0, "shape": sh, "edge": edge, "end": E, "unit": box, "plate": box,
                        "loupe_at": None, "path": pts, "route": "S1", "vis": _length(pts), "target": pts[0],
                        "diag": None, "segs": [(*pts[0], *pts[1])] * 2,
                        "static": 99.0 + math.hypot(E[0] - ft.x, E[1] - ft.y) / 100})
    out.sort(key=lambda c: c["static"])
    return out


def _describe(c: dict, ft: _Feat, conflict: bool, set_cost: float, fmt: str) -> dict[str, Any]:
    """The layout record render draws from."""
    sh: _Shape = c["shape"]
    box = [round(v) for v in c["plate"]]
    unit = [round(v) for v in c["unit"]]
    edge = c["edge"]
    x0, y0, x1, y1 = box
    if edge in ("top", "bottom"):
        tx, anchor = round((x0 + x1) / 2), "mm"
    elif edge == "left":
        tx, anchor = x0 + sh.pad, "lm"
    else:
        tx, anchor = x1 - sh.pad, "rm"
    hb = sh.font.getbbox("H", anchor="mm")
    yoff = (hb[1] + hb[3]) / 2
    ys = [y0 + PLATE_PAD_CAP + sh.cap / 2 + j * sh.step - yoff for j in range(len(sh.lines))]
    ex, ey = c["end"]
    if edge in ("top", "bottom"):
        blen = min(BAR_LEN[fmt][1], max(BAR_LEN[fmt][0], 0.45 * (x1 - x0)))
        by = y1 - BAR_W if edge == "bottom" else y0
        bar = [ex - blen / 2, by, ex + blen / 2, by + BAR_W]
    else:
        blen = (y1 - y0) - 2 * BAR_SIDE_INSET
        bx = x0 if edge == "left" else x1 - BAR_W
        bar = [bx, ey - blen / 2, bx + BAR_W, ey + blen / 2]
    la = c["loupe_at"]
    return {"lines": sh.lines, "font": sh.font, "track": sh.track, "step": sh.step, "cap": sh.cap,
            "widths": sh.widths, "pad": sh.pad, "radius": sh.radius, "box": box, "unit": unit, "attach": edge,
            "on_right": ex >= ft.x, "text_x": tx, "anchor": anchor, "text_y": ys, "end": (ex, ey),
            "loupe": ft.loupe and la is not None, "loupe_at": (round(la[0]), round(la[1])) if la else None,
            "conflict": bool(conflict), "elbow": None, "route": c["route"], "path": [tuple(p) for p in c["path"]],
            "drawn": _fillet(c["path"], FILLET_R[fmt]) if c["route"] == "S2" else [tuple(p) for p in c["path"]],
            "target": tuple(c["target"]), "dot": (ft.x, ft.y), "tier": ft.tier, "mbox": ft.mbox,
            "length": c["vis"], "bar": bar, "r_gold": ft.r_gold, "r_vis": ft.r_vis, "src_r": ft.src_r,
            "cost": c.get("static"), "set_cost": set_cost}


# Caption band (panel round 4: a low dot ran its leader from the plate down through the caption pills). No dot (its
# ripple, its loupe's source ring, or its outline) may reach caption_top, where it starts or anywhere the push takes
# it. A leader runs from the dot to its plate, and every plate sits inside text_box (above the band), so a leader
# never reaches lower than its dot: keeping the dots out keeps the leaders out. 16:9: the burned-in captions' zone.
CAPTION_TOP_169 = 905                                # y at 1080 tall (src/video_qa.py caption_zone_top)


def caption_top(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> int:
    """The highest y a caption reaches: the top of the 9:16 caption band, or the 16:9 caption zone."""
    if width > height:
        return round(CAPTION_TOP_169 * height / 1080)
    return safe_zones.caption_band_top(width, height)


def clear_of_captions(points: list[tuple], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                      mag: float = 0.0, push: Push | None = None) -> list[tuple]:
    """`points` without any whose dot reaches the caption band at the start of the clip or anywhere along the push
    (`mag`: the loupe's magnification, 0 for none; the loupe's feature is ringed at loupe_r / mag)."""
    push = push or Push(width, height)
    top = caption_top(width, height)
    li = loupe_index(points) if mag > 0 else None
    kept = []
    for k, p in enumerate(points):
        r = dot_r = (_loupe_r(width, height) / mag + 5) if k == li else DOT_R
        if tier(p, width, height) == "B":
            r = max(r, _fbox(p)[3] - p[2] + EXTENT_GROW + 3)
        low = max(q[1] for q in [(p[1], p[2]), *push.track(p[1], p[2])])
        if low + r >= top and low + dot_r < top and len(_marker_variants([p], width, height)) > 1:
            # an outline that reaches the band cannot be laid out: the surface takes a point marker (declutter)
            log.info("Callouts: '%s' drawn with a point marker: its outline reaches the caption band.", p[0])
            p, r = as_marker(p), dot_r
        if low + r >= top:
            log.info("Callouts: '%s' dropped: its dot reaches the caption band (y %d >= %d).", p[0], round(low + r), top)
            continue
        kept.append(p)
    return kept


# Focus spotlight (panel round 4). While a label is the newest one said, the frame outside a soft ellipse round its
# feature is darkened SPOT_DARK more, on top of the global dim. The ellipse is the feature's extent (w, h, the box
# for an extent marker) grown by SPOT_PAD, never under SPOT_MIN, never over SPOT_MAX of the frame's short side,
# feathered over SPOT_FEATHER px, and it rides the push. It eases in over SPOT_FADE and crossfades to the next label
# from that label's start. It is drawn under the plates, leaders and loupe.
SPOT_DARK, SPOT_PAD, SPOT_FEATHER = 0.2, 1.25, 140
SPOT_MIN = 1.5 * 25
SPOT_MAX = 0.35
SPOT_SCALE = 4                                       # the mask is drawn at 1/SPOT_SCALE and smoothed up
SPOT_FADE = 0.32

# Measuring under the spotlight (round-4 review): each label's text and non-text marks are measured on every probe
# frame from when its plate is fully open until the exit starts (every MEASURE_STEP, and each point where the
# spotlight starts or finishes moving), and planned and guarded on the worst of them.
MEASURE_STEP = 0.5                                   # seconds between a label's probe frames


def spot_axes(point: tuple, width: int, height: int) -> tuple[float, float]:
    """The spotlight's semi-axes for `point`: its feature's extent (w_px, h_px), else a circle of its area."""
    if len(point) > 5 and point[4] and point[5]:
        a, b = float(point[4]) / 2, float(point[5]) / 2
    elif len(point) > 3 and point[3]:
        a = b = (float(point[3]) * width * height) ** 0.5 / 2
    else:
        return SPOT_MIN, SPOT_MIN
    cap = SPOT_MAX * min(width, height)
    return (min(cap, max(SPOT_MIN, a * SPOT_PAD)), min(cap, max(SPOT_MIN, b * SPOT_PAD)))


def spot_mask(size: tuple[int, int], centre: tuple[float, float], axes: tuple[float, float]) -> Any:
    """1 inside the ellipse, a smoothstep to 0 over SPOT_FEATHER px past it (float array, frame size)."""
    import numpy as np
    w, h = size
    sw, sh = max(1, w // SPOT_SCALE), max(1, h // SPOT_SCALE)
    ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float32)
    xs, ys = (xs + 0.5) * w / sw, (ys + 0.5) * h / sh
    a, b = axes
    d = np.sqrt(((xs - centre[0]) / a) ** 2 + ((ys - centre[1]) / b) ** 2)
    u = np.clip((d - 1.0) * min(a, b) / SPOT_FEATHER, 0.0, 1.0)
    small = 1.0 - u * u * (3 - 2 * u)
    up = Image.fromarray(np.round(small * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR)
    return np.asarray(up, dtype=np.float32) / 255


def spotlight(im: Image.Image, t: float, points: list[tuple], starts: list[float], push: Push) -> Image.Image:
    """`im` darkened SPOT_DARK outside the newest label's ellipse (crossfading from the one before on its cue)."""
    import numpy as np
    order = sorted(range(len(points)), key=lambda k: starts[k])
    begun = [k for k in order if t >= starts[k]]
    if not begun:
        return im
    strength = SPOT_DARK * _ease((t - starts[begun[0]]) / SPOT_FADE)
    if strength <= 0:
        return im
    cur = begun[-1]
    w_cur = _ease_io((t - starts[cur]) / SPOT_FADE) if len(begun) > 1 else 1.0
    z = push.zoom(t)
    inside = 0.0
    for k, wk in ((cur, w_cur), *(((begun[-2], 1.0 - w_cur),) if len(begun) > 1 and w_cur < 1 else ())):
        a, b = spot_axes(points[k], im.width, im.height)
        inside = inside + wk * spot_mask(im.size, push.at(points[k][1], points[k][2], t), (a * z, b * z))
    factor = 1.0 - strength * (1.0 - np.clip(inside, 0.0, 1.0))
    arr = np.asarray(im.convert("RGB"), dtype=np.float32) * factor[..., None]
    return Image.fromarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8), "RGB")


def as_marker(p: tuple) -> tuple:
    """`p`, a surface, drawn with a point marker instead of its outline (tier A; see tier)."""
    return _with_trust(p, dict(trust(p), marker="A"))


def _marker_variants(pts: list[tuple], width: int, height: int) -> list[list[tuple]]:
    """`pts` as given, then (operator, 2026-11-03) with an outlined surface moved to a point marker when its outline
    cannot be laid out: the fewest moved first. Only a surface on tier-A evidence moves (a point marker claims a
    precise point; an outline drawn for a loose location keeps it), and the move keeps every object rule and the
    surface's own inner-half and dot rules (the layout applies them to a point-marked surface)."""
    movable = [k for k, p in enumerate(pts)
               if _kind(p) == "extent" and tier(p, width, height) == "B" and evidence_tier(p) == "A"]
    out = [list(pts)]
    for r in range(1, len(movable) + 1):
        for sub in combinations(movable, r):
            out.append([as_marker(p) if k in sub else p for k, p in enumerate(pts)])
    return out


def declutter(points: list[tuple], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
              loupe: bool = True, ground: Any = None, mag: float | None = None,
              spoken: list[int] | None = None) -> list[tuple] | None:
    """At most MAX_LABELS points laid out with no hard limit broken (an outlined surface that cannot be laid out
    may take a point marker instead: _marker_variants). When three cannot be, the valid pair with the lowest cost,
    a tie going to the pair said earlier; when no pair is valid, None (no graphic). `spoken`: the indices of
    `points` in spoken order (default: their order), for the layout's order cost and the tie."""
    pts = list(points[:MAX_LABELS])
    if len(pts) < 2:
        return None
    order = [k for k in (spoken or []) if 0 <= k < len(pts)]
    order += [k for k in range(len(pts)) if k not in order]
    rank = {k: r for r, k in enumerate(order)}

    def fit(keys: tuple[int, ...], step_down: bool) -> tuple[float, list[tuple]] | None:
        sub = [pts[i] for i in keys]
        said = sorted(range(len(keys)), key=lambda j: rank[keys[j]])
        for v in _marker_variants(sub, width, height):
            if len(unambiguous(v, width, height)) < len(v):   # a moved surface's dot too close to another's
                continue
            if step_down:   # QF-24: only a valid stepped-down set; the full-size one has failed already
                labs = _stepped_layout(v, width, height, loupe, ground, mag, said) or [{"conflict": True}]
            else:
                labs = layout_labels(v, width, height, loupe, ground=ground, mag=mag, spoken=said, step_down=False)
            if not any(lab["conflict"] for lab in labs):
                moved = [q[0] for q, o in zip(v, sub) if q is not o]
                if moved:
                    log.info("Callouts: '%s' drawn with a point marker: its outline could not be laid out.",
                             "', '".join(moved))
                return labs[0]["set_cost"], v
        return None

    # Every set and pair at the full label size first (the reviewed search); only when none holds are the sets
    # tried again stepped down (layout_labels, QF-24), so a smaller size never replaces a full-size graphic.
    for step_down in (False, True) if width < height else (False,):
        got = _declutter_pass(pts, rank, fit, step_down)
        if got is not None:
            return got
    return None


def _declutter_pass(pts: list[tuple], rank: dict[int, int], fit: Any, step_down: bool) -> list[tuple] | None:
    """One declutter search (the whole set, else the cheapest valid pair) with `fit` at full size, or stepped down."""
    whole = fit(tuple(range(len(pts))), step_down)
    if whole is not None:
        return whole[1]
    if len(pts) < 3:
        log.info("Callouts: the two labels cannot be placed without breaking a limit%s; no graphic.",
                 " at any label size" if step_down else "")
        return None
    best = None
    for combo in combinations(range(len(pts)), 2):
        got = fit(combo, step_down)
        if got is None:
            continue
        key = (got[0], sorted(rank[i] for i in combo))
        if best is None or key[0] < best[0][0] - 1e-9 or (abs(key[0] - best[0][0]) <= 1e-9 and key[1] < best[0][1]):
            best = (key, combo, got[1])
    if best is None:
        log.info("Callouts: no two labels can be placed without breaking a limit; no graphic.")
        return None
    dropped = [pts[i][0] for i in range(len(pts)) if i not in best[1]]
    log.info("Callouts: '%s' dropped: it could not be placed clear of the others.", "', '".join(dropped))
    return best[2]


# A loupe (operator round 3): the smallest feature the vision model located (under LOUPE_AREA of the frame; one loupe
# per clip; point markers only) gets a circular inset beside its label, the photo magnified LOUPE_MAG times (less
# when the photo has fewer native pixels, and no loupe under LOUPE_MIN_MAG) inside a 2 px GOLD ring cased in INK.
# The source region is ringed on the photo and its leader starts at that ring (src_r + 1), so the gold meets.
LOUPE_AREA, LOUPE_MAG, LOUPE_MIN_MAG = 0.04, 2.0, 1.3
LOUPE_R = {"portrait": 100, "landscape": 90}
LOUPE_LEADER = 56          # px of visible leader between the source ring and the plate
LOUPE_GAP = 14             # px between the inset's ring and its plate


def _needs_loupe(point: tuple) -> bool:
    return len(point) > 3 and point[3] is not None and 0 < float(point[3]) < LOUPE_AREA and _kind(point) == "point"


def loupe_index(points: list[tuple]) -> int | None:
    """The one point that gets the loupe: the smallest point feature under LOUPE_AREA, else None."""
    small = [(float(p[3]), k) for k, p in enumerate(points) if _needs_loupe(p)]
    return min(small)[1] if small else None


def loupe_mag(native: float) -> float:
    """The loupe's magnification for a photo with `native` source pixels per frame pixel: LOUPE_MAG at most, never
    past the photo's own pixels at the end of the push-in; 0 when that leaves less than LOUPE_MIN_MAG."""
    m = min(LOUPE_MAG, native / (1.0 + PUSH_IN))
    return round(m, 3) if m >= LOUPE_MIN_MAG else 0.0


def _loupe_r(width: int, height: int) -> int:
    return LOUPE_R["landscape" if width > height else "portrait"]


def _loupe(im: Image.Image, src: Image.Image, cx: float, cy: float, r: int, a: float,
           at: tuple[float, float] | None = None, s: float | None = None) -> Image.Image:
    """`src` around (cx, cy), `s` px each way (default r / LOUPE_MAG), in a disc of radius r centred on `at`
    (default (cx, cy)) on `im`, at opacity `a`: a 2 px GOLD ring cased in 2 px INK either side."""
    from PIL import ImageChops
    s = r / LOUPE_MAG if s is None else s
    ox, oy = at if at is not None else (cx, cy)
    patch = src.resize((2 * r, 2 * r), Image.LANCZOS, box=(cx - s, cy - s, cx + s, cy + s))
    mask = Image.new("L", (2 * r * SS, 2 * r * SS), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 2 * r * SS - 1, 2 * r * SS - 1], fill=255)
    mask = mask.reduce(SS)
    if a < 1:
        mask = ImageChops.multiply(mask, Image.new("L", mask.size, round(255 * a)))
    im = im.copy()
    im.paste(patch, (round(ox - r), round(oy - r)), mask)
    m = 6
    size = 2 * (r + m)
    ink, gold = Image.new("L", (size * SS, size * SS), 0), Image.new("L", (size * SS, size * SS), 0)
    xy = (round(ox - r - m), round(oy - r - m))
    cx, cy = (ox - xy[0] + 0.5) * SS, (oy - xy[1] + 0.5) * SS
    ImageDraw.Draw(ink).ellipse([cx - (r + 3.5) * SS, cy - (r + 3.5) * SS, cx + (r + 3.5) * SS, cy + (r + 3.5) * SS],
                                outline=255, width=6 * SS)
    ImageDraw.Draw(gold).ellipse([cx - (r + 1.5) * SS, cy - (r + 1.5) * SS, cx + (r + 1.5) * SS, cy + (r + 1.5) * SS],
                                 outline=255, width=2 * SS)
    lut = _lut(a)
    im.paste(INK, (*xy, xy[0] + size, xy[1] + size), ink.reduce(SS).point(lut))
    im.paste(GOLD, (*xy, xy[0] + size, xy[1] + size), gold.reduce(SS).point(lut))
    return im


def _from_rim(path: list[tuple], r: float) -> list[tuple]:
    """`path` starting r px along its first segment (the leader leaves the loupe's rim, not its centre)."""
    (ax, ay), (bx, by) = path[0], path[1]
    d = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    if d <= r:
        return path[1:] if len(path) > 2 else [path[1], path[1]]
    return [(ax + (bx - ax) * r / d, ay + (by - ay) * r / d), *path[1:]]


# Motion (round 5), from the cue F0 (the onset of the label's first word): the dot grows from F0 - 0.20 s (6
# frames) over DOT_GROW, ease-out-back to 1.08 then 1.0; the leader draws from when the dot is 60 % grown at a
# constant LEADER_SPEED px/s along its path, fillet included (LEADER_MIN..LEADER_MAX s); the gold attachment bar
# grows from the attachment point 2 frames before the leader arrives (BAR_GROW); the plate opens from the attached
# edge's centre out to both corners from 85 % of the leader (PLATE_OPEN, ease-out expo); the text fades in over
# TEXT_FRAMES from when the plate is 70 % open, moving 6 px from the attached edge toward the plate's centre. At
# landing one ripple ring grows and fades (RIPPLE_TIME; never repeated). When a newer label's dot lands the older
# ones' leader, marker and bar ease to DIM_OLDER over DIM_TIME; fill, hairline and text stay full. All labels exit
# together from CLIP_SECONDS - EXIT_LEAD over EXIT_TIME: the text and its plate (fill, hairline, shadow, bar, loupe)
# fade out as one over EXIT_FADE, the plate never more opaque than its text (review 2026-11-03: the text used to go
# first, leaving empty plates); only then does the leader retract (EXIT_RETRACT) and the marker shrink.
LEAD_FRAMES = 6
DOT_LEAD = LEAD_FRAMES / VIDEO_FPS
DOT_AT, DOT_GROW, STAGGER = 0.5, 0.12, 1.3                # seconds
LEADER_SPEED, LEADER_MIN, LEADER_MAX = 1500.0, 0.14, 0.26
LEADER_DRAW = LEADER_MAX                                  # the longest a leader takes
BAR_GROW, PLATE_OPEN, TEXT_FRAMES, TEXT_RISE = 0.08, 0.16, 4, 6
LABEL_FADE = PLATE_OPEN
DIM_OLDER, DIM_TIME = 0.55, 0.30
EXIT_LEAD, EXIT_TIME = 0.40, 0.30
EXIT_FADE, EXIT_RETRACT, EXIT_SHRINK = 0.15, 0.10, 0.08   # within EXIT_TIME; the marker shrinks over its end
BACK = 1.5                                                # ease-out-back constant: 8 % overshoot


def _ease_back(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1 + (BACK + 1) * (u - 1) ** 3 + BACK * (u - 1) ** 2


def _ease_expo(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1.0 if u >= 1 else 1 - 2 ** (-10 * u)


def _ease_in(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u ** 3


def _first(f, target: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < target else (lo, mid)
    return hi


DOT60 = _first(_ease_back, 0.6) * DOT_GROW                # the dot 60 % grown
TEXT_AT = _first(_ease_expo, 0.7)                         # the plate 70 % open
OPEN_FROM_START = DOT60 + max(LEADER_MAX * 0.85 + max(PLATE_OPEN, PLATE_OPEN * TEXT_AT + TEXT_FRAMES / VIDEO_FPS),
                              LEADER_MAX - 2 / VIDEO_FPS + BAR_GROW)
OPEN_TIME = OPEN_FROM_START - DOT_LEAD                    # from the cue to the label fully open, at the longest
READ_BY = 0.30                                            # text readable within F0 + this
CUE_LATE = 0.70                                           # a held cue is never more than this after its word
LAST_OPEN = 1.5                                           # the last label fully open this long before the exit
MIN_REVEAL_GAP, READ_PER_CHAR, HOLD_169 = 1.4, 0.06, 1.3  # seconds
REVEAL_LAST = math.floor((CLIP_SECONDS - EXIT_LEAD - LAST_OPEN - (OPEN_FROM_START - DOT_LEAD)) * 100) / 100


class _Timeline:
    """One label's moments, from its start (its cue less DOT_LEAD) and its leader's drawn length."""

    def __init__(self, start: float, arc: float, fps: float = VIDEO_FPS):
        self.start = start
        self.lead0 = start + DOT60
        self.dur = min(LEADER_MAX, max(LEADER_MIN, arc / LEADER_SPEED))
        self.lead1 = self.lead0 + self.dur
        self.bar0 = self.lead1 - 2 / fps
        self.plate0 = self.lead0 + 0.85 * self.dur
        self.text0 = self.plate0 + TEXT_AT * PLATE_OPEN
        self.text1 = self.text0 + TEXT_FRAMES / fps
        self.opened = max(self.text1, self.plate0 + PLATE_OPEN, self.bar0 + BAR_GROW)
        self.landed = start + DOT_GROW


def exit_start() -> float:
    return CLIP_SECONDS - EXIT_LEAD


def probe_frames(k: int, starts: list[float], n: int, fps: float = VIDEO_FPS,
                 opened: list[float] | None = None) -> list[int]:
    """The frame indices label `k` is measured on: from the first frame it is fully open (`opened[k]` s after its
    start; default the longest) to the last frame before the exit, every MEASURE_STEP, plus each frame where the
    spotlight starts or settles (a label's start, and that plus SPOT_FADE)."""
    last = min(n - 1, math.ceil(exit_start() * fps - 1e-6) - 1)
    first = min(last, math.ceil((starts[k] + (opened[k] if opened else OPEN_FROM_START)) * fps - 1e-6))
    ts = {first, last}
    ts |= {math.ceil((s + d) * fps - 1e-6) for s in starts for d in (0.0, SPOT_FADE)}
    step = max(1, round(MEASURE_STEP * fps))
    ts |= set(range(first, last + 1, step))
    return sorted(i for i in ts if first <= i <= last)


def hold(label: str, landscape: bool = False) -> float:
    """How long `label` keeps the focus before the next one starts: max(1.4 s, 0.06 s a character) on 9:16, 1.3 s
    on 16:9."""
    if landscape:
        return HOLD_169
    return max(MIN_REVEAL_GAP, READ_PER_CHAR * len(label))


def _tokens(text: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())]


def _mention(beat_tokens: list[str], label: str) -> int | None:
    """Index of the token where `label` is first said in `beat_tokens`: its whole phrase, else its last word (the
    head noun: "ventilation shaft" -> "shaft") when that word occurs exactly once in the beat."""
    lt = _tokens(label)
    if not lt:
        return None
    for i in range(len(beat_tokens) - len(lt) + 1):
        if beat_tokens[i:i + len(lt)] == lt:
            return i
    head = lt[-1].rstrip("s")
    hits = [i for i, t in enumerate(beat_tokens) if len(head) > 2 and t.rstrip("s") == head]
    return hits[0] if len(hits) == 1 else None


def reveal_plan(labels: list[str], beat_times: list[dict[str, Any]] | None,
                words: list[dict[str, Any]] | None = None, squeeze: bool = True,
                landscape: bool = False) -> tuple[int | None, list[Any], list[dict[str, Any]]]:
    """reveal_times, and for each label how its cue was found: {"word", "start" (seconds into the narration),
    "method": "timed" (a timed word), "estimated" (its place in the beat's text) or "stagger"}."""
    stagger = stagger_times(labels, landscape)
    info = [{"word": None, "start": None, "method": "stagger"} for _ in labels]
    beats = [b for b in (beat_times or []) if isinstance(b, dict) and "start" in b]
    if not beats:
        return None, stagger, info
    scored = []
    for bi, b in enumerate(beats):
        toks = _tokens(str(b.get("text") or ""))
        hits = {k: _mention(toks, lbl) for k, lbl in enumerate(labels)}
        scored.append((sum(v is not None for v in hits.values()), -bi, bi, toks, hits))
    n_hit, _, bi, toks, hits = max(scored)
    if n_hit < 2:
        return None, stagger, info
    start = float(beats[bi]["start"])
    end = float(beats[bi + 1]["start"]) if bi + 1 < len(beats) else start + CLIP_SECONDS
    timed = [w for w in (words or []) if start - 0.05 <= float(w.get("start", -1)) < end]
    at: dict[int, float] = {}
    for k, i in hits.items():
        if i is None:
            continue
        if len(timed) >= len(toks) and timed:   # the beat's own words, in order: the i-th token's word
            wt = [(t, float(w["start"])) for w in timed for t in _tokens(str(w.get("word", "")))]
            at[k] = wt[min(i, len(wt) - 1)][1] - start
            info[k] = {"word": wt[min(i, len(wt) - 1)][0], "start": round(at[k] + start, 3), "method": "timed"}
        else:                                   # no timings: the token's share of the beat
            at[k] = (end - start) * i / max(1, len(toks))
            info[k] = {"word": toks[i], "start": round(at[k] + start, 3), "method": "estimated"}
    out: list[float] = [0.0] * len(labels)
    last = None
    prev = ""
    for k in sorted(range(len(labels)), key=lambda j: (at.get(j, float("inf")), j)):
        if k in at:
            t = max(DOT_AT, at[k])
            if last is not None:   # the one before holds, but never pushes this cue past its word by CUE_LATE
                t = max(t, min(last + hold(prev, landscape), at[k] + CUE_LATE), last + 0.6)
        else:
            t = DOT_AT if last is None else last + max(STAGGER, hold(prev, landscape))
        out[k] = last = t
        prev = labels[k]
    if not squeeze:
        return bi, [round(t, 3) if t <= REVEAL_LAST + 1e-9 else None for t in out], info
    if max(out) > REVEAL_LAST:   # squeeze into the clip, keeping the order and the gaps' proportions
        lo = min(out)
        span = max(out) - lo
        room = max(REVEAL_LAST - DOT_AT, 0.1)
        out = [DOT_AT + (t - lo) * room / span if span else DOT_AT for t in out]
    return bi, [round(t, 3) for t in out], info


def reveal_times(labels: list[str], beat_times: list[dict[str, Any]] | None,
                 words: list[dict[str, Any]] | None = None, squeeze: bool = True,
                 landscape: bool = False) -> tuple[int | None, list[Any]]:
    """(anchor beat index or None, each label's cue in the clip: the onset of the word that names it). Labels the
    anchor beat never names, and every label when no beat names two, keep the fixed stagger after the ones that are
    said. Each cue waits for the one before it to hold (hold()), but no longer than CUE_LATE past its own word.
    Cues past REVEAL_LAST are squeezed into the clip, or with squeeze=False are None (that label is not drawn)."""
    anchor, times, _ = reveal_plan(labels, beat_times, words, squeeze, landscape)
    return anchor, times


def stagger_times(labels: list[str], landscape: bool = False) -> list[float]:
    """The fixed clock when no beat names the labels: DOT_AT, then STAGGER apart (longer after a long label)."""
    out: list[float] = []
    for k in range(len(labels)):
        out.append(DOT_AT if not k else out[-1] + max(STAGGER, hold(labels[k - 1], landscape)))
    return out


# Non-text contrast (panel round 3): every leader, marker and loupe ring is measured on its probe frames, its gold
# core against the 90th-percentile luminance of the band its casing covers, and must reach NONTEXT_MIN (WCAG
# 1.4.11), at the dimmed values. Short of it, the label steps through _GUARD: the casing fully opaque, then 3 px,
# then the older labels dimmed less (0.8, then 1.0). Recorded in <clip>.contrast.json beside the text rows.
NONTEXT_MIN = 3.0
_GUARD = ((CASING_ALPHA, LEADER_HALO, DIM_OLDER), (255, LEADER_HALO, DIM_OLDER), (255, 3, DIM_OLDER),
          (255, 3, 0.8), (255, 3, 1.0))


def nontext_contrast(frame: Image.Image, mask: Image.Image, band: int, skip: int = 0,
                     exclude: Image.Image | None = None) -> float | None:
    """WCAG ratio of a drawn shape's core (`mask`, L, 255 on the gold) against the 90th-percentile luminance of the
    `band` px around it (past the first `skip` px: the anti-aliased edge, a blend of the two colours being compared;
    never on `exclude`, the same gold continuing, as a leader joining its dot); None when the shape is off the frame."""
    import cv2
    import numpy as np

    from .typography import _lin
    m = (np.asarray(mask) > 127).astype(np.uint8)
    if m.sum() < 8:
        return None
    rgb = np.asarray(frame.convert("RGB"))
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * (band + skip) + 1, 2 * (band + skip) + 1))
    inner = m if not skip else cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * skip + 1, 2 * skip + 1)))
    ring = cv2.dilate(m, disc) - inner
    if exclude is not None:
        ex = cv2.dilate((np.asarray(exclude) > 0).astype(np.uint8), np.ones((3, 3), np.uint8))
        ring = ring * (1 - ex)
    if ring.sum() < 8:
        return None
    lt = float(np.median(_lin(rgb[m > 0])))
    lb = float(np.percentile(_lin(rgb[ring > 0]), 90))
    return round((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05), 2)


def _wipe_mask(size: tuple[int, int], box: list, edge: str, f: float, pad: int = 6) -> Image.Image:
    """A mask opening `box` (grown by `pad`) from the edge its leader meets, to fraction `f`."""
    x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    f = max(0.0, min(1.0, f))
    if edge == "bottom":
        y0 = y1 - (y1 - y0) * f
    elif edge == "top":
        y1 = y0 + (y1 - y0) * f
    elif edge == "left":
        x1 = x0 + (x1 - x0) * f
    else:
        x0 = x1 - (x1 - x0) * f
    m = Image.new("L", size, 0)
    if f > 0:
        ImageDraw.Draw(m).rectangle([x0, y0, x1, y1], fill=255)
    return m


def native_scale(photo: Path, width: int, height: int) -> float:
    """Source pixels per frame pixel once `photo` is cover-cropped to the frame (below 1: it is upscaled)."""
    with Image.open(photo) as im:
        iw, ih = im.size
    return min(iw / width, ih / height)


_LUTS: dict[int, list[int]] = {}


def _lut(a: float) -> list[int]:
    key = max(0, min(1000, round(a * 1000)))
    if key not in _LUTS:
        _LUTS[key] = [round(v * key / 1000) for v in range(256)]
    return _LUTS[key]


def _rrect_perimeter(box: tuple, r: float, start: tuple[float, float], step: float = 2.0) -> list[tuple]:
    """The outline of a rounded rectangle as points every ~`step` px, from the point nearest `start`, clockwise."""
    x0, y0, x1, y1 = box
    pts: list[tuple[float, float]] = []

    def arc(cx, cy, a0):
        for i in range(9):
            a = math.radians(a0 + 90 * i / 8)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    def run(ax, ay, bx, by):
        n = max(1, int(math.hypot(bx - ax, by - ay) / step))
        pts.extend((ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(n))
    run(x0 + r, y0, x1 - r, y0)
    arc(x1 - r, y0 + r, -90)
    run(x1, y0 + r, x1, y1 - r)
    arc(x1 - r, y1 - r, 0)
    run(x1 - r, y1, x0 + r, y1)
    arc(x0 + r, y1 - r, 90)
    run(x0, y1 - r, x0, y0 + r)
    arc(x0 + r, y0 + r, 180)
    i0 = min(range(len(pts)), key=lambda i: (pts[i][0] - start[0]) ** 2 + (pts[i][1] - start[1]) ** 2)
    return pts[i0:] + pts[:i0]


class _Art:
    """One label drawn as masks in its own local frame at rest (the rigid lock moves the masks, never redraws
    them): its leader casing and marker ink, its gold, its plate (shadow, fill, hairline), bar and text."""

    def __init__(self, lab: dict, fmt: str, width: int, height: int):
        self.lab, self.fmt = lab, fmt
        dot = lab["dot"]
        xs = [lab["unit"][0], lab["unit"][2], dot[0]] + [p[0] for p in lab["drawn"]]
        ys = [lab["unit"][1], lab["unit"][3], dot[1]] + [p[1] for p in lab["drawn"]]
        reach = max(RIPPLE[fmt][1], lab["src_r"]) + 8
        if lab["mbox"] is not None:
            xs += [lab["mbox"][0], lab["mbox"][2]]
            ys += [lab["mbox"][1], lab["mbox"][3]]
        m = SHADOW_BLUR * 3 + SHADOW_DY + 4
        self.ox = math.floor(min(min(xs) - m, dot[0] - reach))
        self.oy = math.floor(min(min(ys) - m, dot[1] - reach))
        self.w = math.ceil(max(max(xs) + m, dot[0] + reach)) - self.ox + 2
        self.h = math.ceil(max(max(ys) + m, dot[1] + reach)) - self.oy + 2
        nx, ny = NORMAL[lab["attach"]]
        drawn = lab["drawn"]
        self.casing_path = _trim(drawn, lab["r_gold"])
        ex, ey = lab["end"]
        self.gold_path = [*self.casing_path, (ex - nx * 1.5, ey - ny * 1.5)]
        self.arc = _length(self.casing_path)
        self.cache: dict[Any, Any] = {}
        self.text_mask = None
        self.plates: list[Any] = []
        if lab["mbox"] is not None:
            self.outline = _rrect_perimeter(lab["mbox"], EXTENT_R, lab["target"])

    def p(self, x: float, y: float) -> tuple[float, float]:
        # frame coordinates name pixel centres (as Pillow's do), so x maps to the middle of its SS block
        return (x - self.ox + 0.5) * SS, (y - self.oy + 0.5) * SS

    def _new(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        im = Image.new("L", (self.w * SS, self.h * SS), 0)
        return im, ImageDraw.Draw(im)

    def _line(self, d: ImageDraw.ImageDraw, pts: list[tuple], width: float) -> None:
        if len(pts) > 1 and _length(pts) > 0.05:
            d.line([self.p(*q) for q in pts], fill=255, width=max(1, round(width * SS)), joint="curve")

    def _disc(self, d: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
        if r > 0.05:
            x, y = self.p(cx, cy)
            d.ellipse([x - r * SS, y - r * SS, x + r * SS, y + r * SS], fill=255)

    def _ring(self, d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, w: float) -> None:
        if r > 0.05:
            x, y = self.p(cx, cy)
            d.ellipse([x - r * SS, y - r * SS, x + r * SS, y + r * SS], outline=255, width=max(1, round(w * SS)))

    def outline_part(self, f: float) -> list[list[tuple]]:
        """The extent outline traced to fraction `f` from its docking point, both ways round."""
        if f <= 0:
            return []
        pts = self.outline
        half = max(2, int(len(pts) / 2 * min(1.0, f)) + 1)
        fwd = pts[:half]
        back = [pts[0], *list(reversed(pts))[:half - 1]]
        if f >= 1:
            return [pts + [pts[0]]]
        return [fwd, back]

    def lines(self, g: float, trace: float, lead: float, casing_px: float) -> tuple[Image.Image, Image.Image]:
        """(ink, gold) masks: the leader drawn to fraction `lead`, the marker at scale `g` (or traced to `trace`)."""
        key = ("lines", round(g, 3), round(trace, 3), round(lead, 4), casing_px)
        if key in self.cache:
            return self.cache[key]
        lab, fmt = self.lab, self.fmt
        ink, di = self._new()
        gold, dg = self._new()
        cas = _partial(self.casing_path, lead)
        if lead > 0:
            self._line(di, cas, LEADER_W + 2 * casing_px)
            gp = self.gold_path if lead >= 1 else cas
            self._line(dg, gp, LEADER_W)
        x, y = lab["dot"]
        if lab["mbox"] is not None:
            for part in self.outline_part(trace):
                self._line(di, part, EXTENT_W + 2 * casing_px)
                self._line(dg, part, EXTENT_W)
        elif lab["loupe"]:
            r = max(1.0, lab["src_r"] * g)
            self._ring(di, x, y, r + 3, 6)
            self._ring(dg, x, y, r + 1, 2)
        else:
            self._disc(di, x, y, (DOT_CORE[fmt] + max(DOT_DISC[fmt] - DOT_CORE[fmt], casing_px)) * g)
            self._disc(dg, x, y, DOT_CORE[fmt] * g)
        out = (ink.reduce(SS), gold.reduce(SS))
        self.cache[key] = out
        return out

    def ripple(self, r: float) -> Image.Image:
        key = ("ripple", round(r, 1))
        if key not in self.cache:
            im, d = self._new()
            self._ring(d, *self.lab["dot"], r + 1, 2)
            self.cache[key] = im.reduce(SS)
        return self.cache[key]

    def plate(self, f: float) -> tuple[Image.Image, Image.Image, Image.Image]:
        """(shadow, fill, hairline) masks with the plate opened to fraction `f` from its attached edge's centre."""
        key = ("plate", round(f, 3))
        if key in self.cache:
            return self.cache[key]
        lab = self.lab
        x0, y0, x1, y1 = lab["box"]
        ex, ey = lab["end"]
        if lab["attach"] in ("top", "bottom"):
            hw = (x1 - x0) / 2 * f
            box = (ex - hw, y0, ex + hw, y1)
        else:
            hh = (y1 - y0) / 2 * f
            box = (x0, ey - hh, x1, ey + hh)
        r = min(lab["radius"], (box[2] - box[0]) / 2, (box[3] - box[1]) / 2)
        fill, df = self._new()
        hair, dh = self._new()
        if box[2] - box[0] > 0.1 and box[3] - box[1] > 0.1:
            a, b = self.p(box[0], box[1]), self.p(box[2], box[3])
            df.rounded_rectangle([a[0], a[1], b[0] - 1, b[1] - 1], radius=r * SS, fill=255)
            dh.rounded_rectangle([a[0], a[1], b[0] - 1, b[1] - 1], radius=r * SS, outline=255, width=SS)
        fill1 = fill.reduce(SS)
        shadow = Image.new("L", fill1.size, 0)
        shadow.paste(fill1, (0, SHADOW_DY))
        shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR / 2))
        out = (shadow, fill1, hair.reduce(SS))
        self.cache[key] = out
        return out

    def bar(self, f: float) -> Image.Image:
        key = ("bar", round(f, 3))
        if key in self.cache:
            return self.cache[key]
        lab = self.lab
        bx0, by0, bx1, by1 = lab["bar"]
        ex, ey = lab["end"]
        if lab["attach"] in ("top", "bottom"):
            hw = (bx1 - bx0) / 2 * f
            box = (ex - hw, by0, ex + hw, by1)
        else:
            hh = (by1 - by0) / 2 * f
            box = (bx0, ey - hh, bx1, ey + hh)
        im, d = self._new()
        if f > 0:
            a, b = self.p(box[0], box[1]), self.p(box[2], box[3])
            d.rectangle([a[0], a[1], b[0] - 1, b[1] - 1], fill=255)
        out = im.reduce(SS)
        self.cache[key] = out
        return out

    def text(self) -> Image.Image:
        if self.text_mask is None:
            import numpy as np
            arr = np.zeros((self.h, self.w), np.uint8)
            for q in self.plates:
                if q.empty:
                    continue
                gh, gw = q.glyphs.shape
                x, y = q.origin
                sub = arr[y:y + gh, x:x + gw]
                np.maximum(sub, q.glyphs[:sub.shape[0], :sub.shape[1]], out=sub)
            self.text_mask = Image.fromarray(arr, "L")
        return self.text_mask

    def leader_core(self, lead: float) -> Image.Image:
        """The leader's own gold run, drawn as it is drawn (for its measured contrast), clear of its marker and its
        plate."""
        key = ("core", round(lead, 4))
        if key in self.cache:
            return self.cache[key]
        m, d = self._new()
        self._line(d, _partial(self.casing_path, lead), LEADER_W)
        x, y = self.lab["dot"]
        r0 = self.lab["r_vis"] + 6
        cx, cy = self.p(x, y)
        d.ellipse([cx - r0 * SS, cy - r0 * SS, cx + r0 * SS, cy + r0 * SS], fill=0)
        if self.lab["mbox"] is not None:
            tx, ty = self.p(*self.lab["target"])
            d.ellipse([tx - 8 * SS, ty - 8 * SS, tx + 8 * SS, ty + 8 * SS], fill=0)
        b = self.lab["box"]
        p0, p1 = self.p(b[0] - 6, b[1] - 6), self.p(b[2] + 6, b[3] + 6)
        d.rectangle([p0[0], p0[1], p1[0], p1[1]], fill=0)
        out = m.reduce(SS)
        self.cache[key] = out
        return out

    def leader_gold(self, lead: float) -> Image.Image:
        """The whole leader gold as drawn (kept out of its marker's measured ring, where the two golds join)."""
        key = ("lgold", round(lead, 4))
        if key not in self.cache:
            m, d = self._new()
            self._line(d, self.gold_path if lead >= 1 else _partial(self.casing_path, lead), LEADER_W)
            self.cache[key] = m.reduce(SS)
        return self.cache[key]

    def marker_core(self, g: float, trace: float) -> Image.Image:
        """The marker's gold, drawn as it is drawn (for its measured contrast)."""
        key = ("mcore", round(g, 3), round(trace, 3))
        if key in self.cache:
            return self.cache[key]
        m, d = self._new()
        x, y = self.lab["dot"]
        if self.lab["mbox"] is not None:
            for part in self.outline_part(trace):
                self._line(d, part, EXTENT_W)
        elif self.lab["loupe"]:
            self._ring(d, x, y, max(1.0, self.lab["src_r"] * g) + 1, 2)
        else:
            self._disc(d, x, y, DOT_CORE[self.fmt] * g)
        out = m.reduce(SS)
        self.cache[key] = out
        return out


def _shifted(mask: Image.Image, size: tuple[int, int], x: float, y: float) -> Image.Image:
    """`mask` placed on a frame-sized mask at (x, y), sub-pixel as _blit draws it."""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    m = mask
    if fx > 1e-3 or fy > 1e-3:
        m = mask.transform(mask.size, Image.AFFINE, (1, 0, -fx, 0, 1, -fy), resample=Image.BILINEAR)
    full = Image.new("L", size, 0)
    full.paste(m, (ix, iy))
    return full


def _blit(im: Image.Image, colour: tuple, mask: Image.Image, x: float, y: float, alpha: float = 1.0) -> None:
    """Paints `colour` through `mask` at (x, y): the integer part by position, the rest by a bilinear shift."""
    if alpha <= 0:
        return
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    m = mask
    if fx > 1e-3 or fy > 1e-3:
        m = mask.transform(mask.size, Image.AFFINE, (1, 0, -fx, 0, 1, -fy), resample=Image.BILINEAR)
    if alpha < 1:
        m = m.point(_lut(alpha))
    im.paste(tuple(colour[:3]), (ix, iy, ix + m.width, iy + m.height), m)


def _placed(q: Any, dx: int, dy: int) -> Any:
    """A text plate planned in a label's local frame, moved onto the delivered frame (for measuring)."""
    c = copy.copy(q)
    if q.empty:
        return c
    c.origin = (q.origin[0] + dx, q.origin[1] + dy)
    c.box = (q.box[0] + dx, q.box[1] + dy, q.box[2] + dx, q.box[3] + dy)
    c.xy = (q.xy[0] + dx, q.xy[1] + dy)
    return c


class LowContrast(ValueError):
    """Fewer than two labels reach NONTEXT_MIN non-text contrast after every _GUARD step: no graphic."""


def drawn_labels(clip: Path) -> list[str] | None:
    """The labels `clip` was drawn with (render drops any under NONTEXT_MIN), from its contrast record; None when
    there is no record."""
    try:
        rec = json.loads(Path(str(clip) + ".contrast.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    lay = rec.get("layout")
    return [r["label"] for r in lay] if isinstance(lay, list) else None


def render(photo: Path, points: list[tuple], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
           reveal: list[float] | None = None, record: dict | None = None) -> Path:
    """Slow centred push-in on the photo (Push); each feature at its cue (`reveal[k]`, the word that names it; else
    a fixed stagger), from DOT_LEAD before it: its marker lands, the leader draws out to the plate, the bar grows,
    the plate opens from that edge's centre, the text fades in, and the spotlight moves to it (see _Timeline). Each
    label is rigidly locked to its dot's push-in displacement. Older labels' leaders, markers and bars step back to
    DIM_OLDER once a newer one lands. Every label exits together before the clip ends. Raises ValueError on a
    layout with a label in conflict (a hard limit broken). A label still under NONTEXT_MIN non-text contrast after
    every _GUARD step is not drawn (fail closed; recorded under "dropped" in <clip>.contrast.json), and with fewer
    than two left it raises LowContrast. `record`, when given, is filled with what each delivered frame drew: under
    "labels" each label's text mask (its local frame) and plate box at rest, under "frames" one entry per frame with
    each live label's drawn plate and text alpha, its plate fraction, leader and marker, and where its masks sit."""
    import numpy as np

    from .photo_grade import cover
    base = cover(photo, (width, height))   # graded as the master grades the bare photo (QF-86)
    n = int(CLIP_SECONDS * VIDEO_FPS)
    fmt = _fmt(width, height)
    landscape = width > height
    native = native_scale(photo, width, height)
    mag = loupe_mag(native)
    cues = list(reveal) if reveal and len(reveal) == len(points) else stagger_times([p[0] for p in points], landscape)
    starts = [c - DOT_LEAD for c in cues]
    push = Push(width, height)
    spoken = sorted(range(len(points)), key=lambda k: cues[k])
    labels = layout_labels(points, width, height, loupe=mag > 0, push=push, ground=base, mag=mag or None,
                           spoken=spoken)
    bad = [lab["lines"] for lab in labels if lab["conflict"]]
    if bad:
        raise ValueError(f"callout layout breaks a hard limit for {bad}")
    arts = [_Art(lab, fmt, width, height) for lab in labels]
    tls = [_Timeline(starts[k], arts[k].arc) for k in range(len(points))]
    t_exit = exit_start()
    exit_frame = math.ceil(t_exit * VIDEO_FPS - 1e-6)
    nexts = [min((tls[j].landed for j in range(len(points)) if starts[j] > starts[k] + 1e-6), default=None)
             for k in range(len(points))]
    loupe_r = _loupe_r(width, height)
    src_r = loupe_r / mag if mag else 0.0
    view = None
    if any(lab["loupe"] for lab in labels):
        view, _ = _full_res_view(photo, width, height)
    vk = view.width / width if view is not None else 1.0
    guard = [0] * len(points)

    def background(t: float) -> Image.Image:
        im = camera_sample(base, (width, height), push.crop(t), resample=Image.LANCZOS)   # the shared camera
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle([0, 0, width, height], fill=(0, 0, 0, int(70 * _ease(t / 0.8))))
        return spotlight(im, t, points, starts, push)

    def state(k: int, t: float) -> dict | None:
        s, tl = starts[k], tls[k]
        if t < s:
            return None
        u = t - t_exit
        casing, casing_px, dim_to = _GUARD[guard[k]]
        g = _ease_back((t - s) / DOT_GROW)
        trace = _ease((t - s) / TRACE_TIME)
        lead = max(0.0, min(1.0, (t - tl.lead0) / tl.dur))
        bar = _ease((t - tl.bar0) / BAR_GROW)
        plate = _ease_expo((t - tl.plate0) / PLATE_OPEN)
        tp = max(0.0, min(1.0, (t - tl.text0) / (TEXT_FRAMES / VIDEO_FPS)))
        text_a, text_off = tp, TEXT_RISE * (1 - _ease(tp))
        loupe_a = _ease((t - tl.plate0) / 0.25)
        plate_a = 1.0
        if u > 0:   # the exit: text and plate (with its bar, shadow and loupe) fade as one, then leader, marker
            leave = 1 - _ease_io(u / EXIT_FADE)
            text_a *= leave
            plate_a = min(leave, text_a)   # a plate is never more opaque than its text (review 2026-11-03)
            bar, loupe_a = bar * plate_a, loupe_a * plate_a
            lead *= 1 - _ease_in((u - EXIT_FADE) / EXIT_RETRACT)
            shrink = 1 - min(1.0, max(0.0, (u - (EXIT_TIME - EXIT_SHRINK)) / EXIT_SHRINK))
            g, trace = g * shrink, trace * shrink
        ripple = None
        p = (t - tl.landed) / RIPPLE_TIME
        if 0 <= p < 1 and labels[k]["mbox"] is None and not labels[k]["loupe"]:
            e = 1 - (1 - p) ** 3
            r0, r1 = RIPPLE[fmt]
            ripple = (r0 + (r1 - r0) * e, 0.9 * (1 - e))
        dim = 1.0 if nexts[k] is None else 1 - (1 - dim_to) * _ease_io((t - nexts[k]) / DIM_TIME)
        return {"g": g, "trace": trace, "lead": lead, "bar": bar, "plate": plate, "plate_a": plate_a,
                "text_a": text_a, "text_off": text_off, "loupe_a": loupe_a, "ripple": ripple, "dim": dim,
                "casing": casing, "casing_px": casing_px}

    def draw(t: float, masks: dict | None = None, skip_text: bool = False, drew: dict | None = None) -> Image.Image:
        z = push.zoom(t)
        im = background(t).copy()
        live = []
        for k in range(len(points)):
            st = state(k, t)
            if st is None:
                continue
            dx, dy = push.shift(*labels[k]["dot"], t)
            live.append((k, st, dx, dy))
        drawn = {}
        for k, st, dx, dy in live:   # every plate's lift shadow, under every leader (it never darkens their gold)
            a = arts[k]
            if st["plate"] > 0:
                _blit(im, (0, 0, 0), a.plate(st["plate"])[0], a.ox + dx, a.oy + dy, SHADOW_ALPHA / 255 * st["plate_a"])
        for k, st, dx, dy in live:   # every casing and marker ink
            ink, gold = arts[k].lines(st["g"], st["trace"], st["lead"], st["casing_px"])
            drawn[k] = gold
            # an older label fades as a whole mark (its casing with its gold), so its gold keeps its hue over the
            # photo instead of darkening to brown over its own ink casing
            _blit(im, INK, ink, arts[k].ox + dx, arts[k].oy + dy, st["casing"] / 255 * st["dim"])
        for k, st, dx, dy in live:   # every leader and marker gold, and the landing ripple
            a = arts[k]
            _blit(im, GOLD, drawn[k], a.ox + dx, a.oy + dy, st["dim"])
            if st["ripple"]:
                _blit(im, GOLD, a.ripple(st["ripple"][0]), a.ox + dx, a.oy + dy, st["ripple"][1] * st["dim"])
        for k, st, dx, dy in live:   # every plate, bar and text
            a = arts[k]
            if drew is not None:
                nx, ny = NORMAL[labels[k]["attach"]]
                drew[k] = {"plate_a": PLATE_ALPHA / 255 * st["plate_a"] if st["plate"] > 0 else 0.0,
                           "text_a": st["text_a"] if not skip_text else 0.0, "plate": st["plate"],
                           "lead": st["lead"], "g": st["g"], "shift": (dx, dy),
                           "text_at": (a.ox + dx + nx * st["text_off"], a.oy + dy + ny * st["text_off"])}
            if st["plate"] > 0:
                _, fill, hair = a.plate(st["plate"])
                _blit(im, INK, fill, a.ox + dx, a.oy + dy, PLATE_ALPHA / 255 * st["plate_a"])
                _blit(im, BONE, hair, a.ox + dx, a.oy + dy, HAIRLINE_ALPHA / 255 * st["plate_a"])
            if st["bar"] > 0:
                _blit(im, GOLD, a.bar(st["bar"]), a.ox + dx, a.oy + dy, st["dim"] * st["plate_a"])
            if st["text_a"] > 0 and not skip_text:
                nx, ny = NORMAL[labels[k]["attach"]]
                off = st["text_off"]
                _blit(im, WHITE, a.text(), a.ox + dx + nx * off, a.oy + dy + ny * off, st["text_a"])
        for k, st, dx, dy in live:   # the loupe insets
            lab = labels[k]
            if lab["loupe"] and view is not None and st["loupe_a"] > 0:
                la = st["loupe_a"]
                r = round(loupe_r * (0.6 + 0.4 * la))
                at = (lab["loupe_at"][0] + dx, lab["loupe_at"][1] + dy)
                x, y = points[k][1], points[k][2]
                im = _loupe(im, view, x * vk, y * vk, r, la, at=at, s=src_r * vk / z * r / loupe_r)
                if masks is not None:
                    mk = masks.setdefault(k, {})
                    mk["ring"] = Image.new("L", (width, height), 0)
                    ImageDraw.Draw(mk["ring"]).ellipse([at[0] - r - 1, at[1] - r - 1, at[0] + r + 1, at[1] + r + 1],
                                                       outline=255, width=2)
        if masks is not None:
            for k, st, dx, dy in live:
                a = arts[k]
                mk = masks.setdefault(k, {})
                for name, m in (("leader", a.leader_core(st["lead"])), ("dot", a.marker_core(st["g"], st["trace"])),
                                ("_gold", a.leader_gold(st["lead"]))):
                    mk[name] = _shifted(m, (width, height), a.ox + dx, a.oy + dy)
        return im

    # Text plans: each line planned in its label's local frame on the worst of its probe frames, plates up.
    opened = [tl.opened - tl.start for tl in tls]
    probes = [probe_frames(k, starts, n, opened=opened) for k in range(len(points))]
    for k, lab in enumerate(labels):
        a = arts[k]
        a.plates = []
        grounds = []
        for i in probes[k][:3]:
            t = i / VIDEO_FPS
            g = draw(t, skip_text=True)
            dx, dy = push.shift(*lab["dot"], t)
            grounds.append(g.crop((a.ox + round(dx), a.oy + round(dy), a.ox + round(dx) + a.w,
                                   a.oy + round(dy) + a.h)))
        for j, line in enumerate(lab["lines"]):
            xy = (lab["text_x"] - a.ox, lab["text_y"][j] - a.oy)
            plans = [plan_text(gr, xy, line, lab["font"], WHITE, anchor=lab["anchor"], track=lab["track"])
                     for gr in grounds]
            a.plates.append(max(plans, key=lambda q: (q.halo, -(q.contrast if q.contrast is not None else 99.0))))

    def measure_nontext() -> list[dict[str, Any]]:
        """Each leader, marker and loupe ring at its lowest ratio over its label's probe frames."""
        worst: dict[str, dict[str, Any]] = {}
        for i in sorted({i for ps in probes for i in ps}):
            masks: dict = {}
            frame = draw(i / VIDEO_FPS, masks)
            for k, mk in masks.items():
                if i not in probes[k]:
                    continue
                for e, m in mk.items():
                    if e.startswith("_"):
                        continue
                    # the gold against the ring just past its anti-aliased edge: its own casing or ink rim
                    band = 1
                    name, r = f"label_{k + 1}_{e}", nontext_contrast(frame, m, band, skip=1,
                                                                        exclude=mk.get("_gold") if e == "dot" else None)
                    old = worst.get(name)
                    if old is None or (r is not None and (old["ratio"] is None or r < old["ratio"])):
                        worst[name] = {"name": name, "ratio": r, "t": round(i / VIDEO_FPS, 3),
                                       "casing": _GUARD[guard[k]][0], "casing_px": _GUARD[guard[k]][1],
                                       "dim": _GUARD[guard[k]][2]}
        return sorted(worst.values(), key=lambda r: (int(r["name"].split("_")[1]), r["name"]))

    rows = measure_nontext()
    for _ in range(len(_GUARD) - 1):
        short = {int(r["name"].split("_")[1]) - 1 for r in rows if r["ratio"] is not None and r["ratio"] < NONTEXT_MIN}
        short = {k for k in short if guard[k] < len(_GUARD) - 1}
        if not short:
            break
        for k in short:
            guard[k] += 1
            for key in [c for c in arts[k].cache if c[0] == "lines"]:
                del arts[k].cache[key]
        rows = measure_nontext()
    # Fail closed (WCAG 1.4.11): a label still under NONTEXT_MIN after every _GUARD step is not drawn. The rest are
    # laid out again without it (and measured again); fewer than two left -> no graphic (LowContrast).
    weak = sorted({int(r["name"].split("_")[1]) - 1 for r in rows
                   if r["ratio"] is not None and r["ratio"] < NONTEXT_MIN})
    if weak:
        why = {points[k][0]: min(r["ratio"] for r in rows if r["name"].startswith(f"label_{k + 1}_")
                                 and r["ratio"] is not None) for k in weak}
        keep = [k for k in range(len(points)) if k not in weak]
        log.info("Callouts: %s under %.1f:1 non-text contrast after every guard step; not drawn.",
                 ", ".join(f"'{lb}' ({v:.2f}:1)" for lb, v in why.items()), NONTEXT_MIN)
        if len(keep) < 2:
            raise LowContrast(f"fewer than two labels reach {NONTEXT_MIN}:1 non-text contrast: {why}")
        clip = render(photo, [points[k] for k in keep], out, width=width, height=height,
                      reveal=[cues[k] for k in keep], record=record)
        side = Path(str(clip) + ".contrast.json")
        try:
            rec = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = {"clip": Path(clip).name}
        rec["dropped"] = [{"label": lb, "ratio": v, "reason": "non-text contrast"} for lb, v in why.items()] + \
            rec.get("dropped", [])
        side.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        return clip
    # Each label's lines are recorded on the delivered frame where they measure lowest, among every frame from when
    # it is fully open (probes[k][0]) to the last frame before the exit, each frame measured where the label is.
    low: dict[int, tuple[float, int, Image.Image, list]] = {}
    poster: list[Image.Image] = []

    if record is not None:
        record["labels"] = [{"text": a.text().copy(), "box": tuple(lab["box"]), "radius": lab["radius"]}
                            for a, lab in zip(arts, labels)]
        record["frames"] = []
        record["exit_frame"] = exit_frame

    def frames():
        for i in range(n):
            t = i / VIDEO_FPS
            drew: dict | None = {} if record is not None else None
            im = draw(t, drew=drew)
            if record is not None:
                record["frames"].append({"i": i, "t": t, "labels": drew})
            for k, lab in enumerate(labels):
                if probes[k] and probes[k][0] <= i < exit_frame:
                    dx, dy = push.shift(*lab["dot"], t)
                    placed = [_placed(q, arts[k].ox + round(dx), arts[k].oy + round(dy)) for q in arts[k].plates]
                    v = min((r for r in (q.measure(im) for q in placed) if r is not None), default=None)
                    if v is not None and (k not in low or v < low[k][0]):
                        low[k] = (v, i, im, placed)
            if i == exit_frame - 1:
                poster.append(im)
            yield im

    clip = _encode(frames(), out, n, width=width, height=height)
    if poster:   # the review still is the last frame with every label open, not the exit
        try:
            poster[0].convert("RGB").save(Path(clip).with_suffix(".jpg"), quality=90)
        except OSError:
            pass
    if low:
        elements = []
        for i in sorted({v[1] for v in low.values()}):
            ks = [k for k, v in low.items() if v[1] == i]
            got = record_contrast(clip, low[ks[0]][2], [(f"label_{k + 1}_{j + 1}", q) for k in ks
                                                        for j, q in enumerate(low[k][3])])
            elements += [dict(e, t=round(i / VIDEO_FPS, 3)) for e in (got or {}).get("elements", [])]
        elements.sort(key=lambda e: [int(v) for v in e["name"].split("_")[1:]])
        side = Path(str(clip) + ".contrast.json")
        try:
            rec = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = {"clip": Path(clip).name}
        vals = [e["graded"] if e.get("graded") is not None else e.get("drawn") for e in elements]
        vals = [v for v in vals if v is not None]
        rec["elements"], rec["min"] = elements, (min(vals) if vals else None)
        rec["measured_on"] = "each label's lowest delivered frame from fully open to the exit (t), where it is"
        rec["nontext_target"] = NONTEXT_MIN
        rec["nontext"] = rows
        rec["dropped"] = []
        rec["loupe_mag"] = mag if any(lab["loupe"] for lab in labels) else None
        rec["layout"] = [{"label": p[0], "attach": lab["attach"], "route": lab["route"], "tier": lab["tier"],
                          "length": round(lab["length"], 1), "box": lab["box"]} for p, lab in zip(points, labels)]
        side.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    _ = np
    return clip


# Accuracy (operator round 3, round 5 section 9): the location is only a proposal. Each label is then checked on
# its own, by a different model from the locator's wherever the configuration offers one (models()), in four calls
# that never show the checker the claim it is checking:
#  1. blind location: the whole photo and the label, no point: the checker returns the feature's box (required),
#     whether it is visible, and whether the photo shows the named site at all (same_site: false drops every label);
#     its box must overlap the located box by IoU >= MIN_IOU (under IOU_A the label is tier B);
#  2. blind look: a crop of the photo at its full resolution centred exactly on the point (padded with grey at the
#     photo's edge), a thin crosshair with a gap marking it, sized to the feature (clamp(2 x its extent, 256, 1024)
#     source px, sent at VERIFY_PX), plus the whole photo with the point ringed, and no label: "what is at the
#     crosshair?";
#  3. a text call matches that answer to the label (a high-confidence same feature, or dropped);
#  4. a decoy: the label asked about at a point DECOY_STEP feature-widths away; a yes there drops it.
# Any error, a missing box or anything short of high confidence drops the label (fail closed). Every answer is
# cached beside the photo so both formats agree, and each crop is kept for the audit.
VERIFY_PX = 512
VERIFY_MIN, VERIFY_MAX = 256, 1024
VERIFY_PAD = (128, 128, 128)
CROSS_GAP, CROSS_ARM = 6, 14
MIN_IOU, IOU_A = 0.5, 0.6
DECOY_STEP = 1.5
VERIFY_VERSION = "blind-decoy-2"     # 2: each answer is cached with the model that served it


class CheckLocate(BaseModel):
    visible: bool                         # the named feature is in the photo
    confidence: Literal["high", "medium", "low"]
    same_site: bool                       # the photo shows the named site at all
    box: list[float] = Field(default_factory=list)   # the feature's box [x0, y0, x1, y1] as fractions of the photo
    note: str = ""


class CheckLook(BaseModel):
    feature: str                          # the physical feature at the crosshair, named plainly
    confidence: Literal["high", "medium", "low"]
    note: str = ""


class LabelMatch(BaseModel):
    same_feature: bool                    # the description and the label name the same physical feature
    confidence: Literal["high", "medium", "low"]


class LabelCheck(BaseModel):
    shows_feature: bool                   # the crop's centre shows exactly the named feature
    confidence: Literal["high", "medium", "low"]
    note: str
    same_site: bool = True                # the photo shows the named site at all
    box: list[float] = Field(default_factory=list)   # the feature's box in the close-up, fractions


CHECK_LOCATE_SYSTEM = """You check where a physical feature is in a photo for a history documentary, before a label
naming it is drawn. You get the whole photo, the site and the feature's name. Say whether the photo shows the named
site at all (same_site), whether that exact feature is visible, and its bounding box [x0, y0, x1, y1] as fractions
of the photo's width and height. Answer visible true with confidence "high" only when you are sure; never guess."""

LOOK_SYSTEM = """You describe photos for a history documentary. You get a close-up crop centred on one point (a thin
red crosshair marks it; grey is outside the photo) and the whole photo with that point ringed. Name the physical
feature the crosshair is on, plainly and specifically (for example "arched stone doorway", "plastered dome",
"carved frieze", "window opening", "sky"). Use confidence "high" only when you are sure what it is."""

MATCH_SYSTEM = """You compare two names for a history documentary. Say whether the description of what is at a
point names the same physical feature as the label that would be drawn there (a synonym or a more general name for
the same thing is the same feature; a neighbouring or different feature is not). Use confidence "high" only when you
are sure."""

VERIFY_SYSTEM = """You check labels for a history documentary before they are drawn on a photo. You get a close-up
crop centred on the point a label would point at (a thin red crosshair marks the point; grey is outside the photo),
and the whole photo with that point ringed. Say whether the point at the crosshair is on exactly the named physical
feature. Answer shows_feature true only when the crosshair is on that feature; if it is on something else, or you
cannot tell, answer false."""


def _full_res_view(photo: Path, width: int, height: int) -> tuple[Image.Image, float]:
    """The video's view of `photo` at the photo's own resolution (never below the frame's), and its scale to it."""
    with Image.open(photo) as im:
        iw, ih = im.size
    k = max(1.0, min(iw / width, ih / height))
    from .photo_grade import cover
    return cover(photo, (round(width * k), round(height * k))), k   # graded as the frame is (QF-86)


def verify_crop(src: Image.Image, x: float, y: float, side: int) -> Image.Image:
    """A `side` px square of `src` centred exactly on (x, y), grey where it runs off the photo, sent at VERIFY_PX
    with a 1 px crosshair (a CROSS_GAP px gap at its centre)."""
    side = int(side)
    x0, y0 = round(x - side / 2), round(y - side / 2)
    crop = Image.new("RGB", (side, side), VERIFY_PAD)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(src.width, x0 + side), min(src.height, y0 + side)
    if sx1 > sx0 and sy1 > sy0:
        crop.paste(src.crop((sx0, sy0, sx1, sy1)), (sx0 - x0, sy0 - y0))
    crop = crop.resize((VERIFY_PX, VERIFY_PX), Image.LANCZOS)
    d = ImageDraw.Draw(crop)
    c = VERIFY_PX / 2 + (x - x0 - side / 2) * VERIFY_PX / side
    cy = VERIFY_PX / 2 + (y - y0 - side / 2) * VERIFY_PX / side
    red = (255, 40, 40)
    for sx, sy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        d.line([(c + sx * CROSS_GAP, cy + sy * CROSS_GAP), (c + sx * (CROSS_GAP + CROSS_ARM), cy + sy * (CROSS_GAP + CROSS_ARM))],
               fill=red, width=1)
    return crop


def _iou(a: tuple, b: tuple) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _valid_box(b: Any) -> tuple[float, float, float, float] | None:
    b = list(b or [])
    if len(b) == 4 and all(isinstance(v, (int, float)) and -0.01 <= v <= 1.01 for v in b) and b[0] < b[2] and b[1] < b[3]:
        return float(b[0]), float(b[1]), float(b[2]), float(b[3])
    return None


def decoy_point(x: float, y: float, fw: float, size: tuple[int, int]) -> tuple[float, float]:
    """The point DECOY_STEP feature-widths (`fw`, source px) from (x, y), sideways toward the photo's interior (up or
    down when neither side has room)."""
    d = DECOY_STEP * max(fw, 32.0)
    for dx, dy in ((d, 0.0), (-d, 0.0), (0.0, d), (0.0, -d)):
        if 0 <= x + dx < size[0] and 0 <= y + dy < size[1]:
            return x + dx, y + dy
    return (x + d, y) if x < size[0] / 2 else (x - d, y)


def _ringed(src: Image.Image, x: float, y: float, side: float, path: Path) -> Path:
    ctx = src.copy()
    ctx.thumbnail((512, 512))
    s = ctx.width / src.width
    r = max(10, round(side * s / 2))
    ImageDraw.Draw(ctx).ellipse([x * s - r, y * s - r, x * s + r, y * s + r], outline=(255, 40, 40), width=3)
    ctx.save(path, quality=88)
    return path


@llm_task("photo_callout_verify")
def verify(photo: Path, points: list[tuple], topic_title: str, width: int = VIDEO_WIDTH,
           height: int = VIDEO_HEIGHT, crop_dir: Path | None = None, cache_dir: Path | None = None) -> list[tuple]:
    """The points whose label the independent checks confirm (blind location, blind look and match, decoy: see
    above; fail closed), each with its trust record updated (`iou`, `decoy`, `tier`). `crop_dir`: where each
    check's crops and its record (checks.json) are kept for the audit."""
    import tempfile

    from . import free_llm
    try:
        if not free_llm.ready():
            log.info("Callouts: no vision model to check the labels; none drawn.")
            return []
        src = _clean(photo)
    except Exception as e:  # noqa: BLE001 - unreadable photo, no engine: nothing is checked, so nothing is drawn
        log.warning("Callout check unavailable: %s", e)
        return []
    k, ox, oy = cover_map(src.size, (width, height))
    cache = _cache_path(cache_dir, photo)
    psha = _photo_sha(photo)
    kept: list[tuple] = []
    records: list[dict[str, Any]] = []
    keep = Path(crop_dir) if crop_dir else None
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
    mdl, hashes = models(), prompt_hashes()
    with tempfile.TemporaryDirectory() as tmp:
        whole = Path(tmp) / "whole.jpg"
        small = src.copy()
        small.thumbnail((LOCATE_PX, LOCATE_PX), Image.LANCZOS)
        small.save(whole, quality=88)

        def ask(step: str, key_parts: list[str], system: str, content: Any, schema: type, **kw: Any) -> Any:
            """The answer, after checking the model that served it (rec["served"]) is known and is not one the
            location ran on: a checker collapsed onto the locator (a budget swap, a fallback) is no check."""
            key = _sha("|".join([psha, step, *key_parts, mdl["match" if step == "match" else "check"],
                                 hashes["check"], VERIFY_VERSION]))
            hit = _cache_read(cache).get(key)
            if isinstance(hit, dict) and step in hit:
                got, served = schema(**hit[step]), hit.get("served")
            else:
                with watch_served() as by:
                    got = free_llm.call(system, content, schema, task_class="light", effort="medium", max_tokens=800,
                                        **_model_kw("match" if step == "match" else "check"), **kw)
                if not isinstance(got, schema):
                    raise TypeError(f"unexpected answer {type(got).__name__}")
                served = by[-1] if by else None
                _cache_write(cache, key, {step: got.model_dump(), "served": served})
            rec.setdefault("served", {})[step] = served
            if not served:
                raise _Drop(f"the model that answered the {step} check is not known")
            if "unknown" in located_by:
                raise _Drop("the model that located it is not known")
            if served in located_by:
                rec["collapsed"] = True
                raise _Drop(f"the {step} check ran on the locator's model {served}")
            return got

        for n, p in enumerate(points, 1):
            label = p[0]
            x, y = p[1] / k + ox, p[2] / k + oy                         # source pixels
            fw, fh = _extent(p)
            side = min(VERIFY_MAX, max(VERIFY_MIN, 2 * max(fw, fh) / k))
            at = f"{x:.0f},{y:.0f},{side:.0f}"
            # the models the location ran on (plan's record; a bare point: the configured locator)
            located_by = set(trust(p).get("locate_served") or [mdl["locate"]])
            rec: dict[str, Any] = {"label": label, "source_xy": [round(x, 1), round(y, 1)], "crop_px": round(side),
                                   "models": {"check": mdl["check"], "match": mdl["match"]},
                                   "located_by": sorted(located_by)}
            fb = _fbox(p)
            # the located box as fractions of the photo, cut at its edges like the checker's (a box has no part off
            # the photo, so the two are compared on the photo only)
            loc = None if fb is None else _valid_box([min(1.0, max(0.0, v)) for v in (
                (fb[0] / k + ox) / src.width, (fb[1] / k + oy) / src.height,
                (fb[2] / k + ox) / src.width, (fb[3] / k + oy) / src.height)])
            crop = verify_crop(src, x, y, side)                       # kept for the review, whatever the verdict
            crop_p = (keep / f"check_{n}.jpg") if keep else Path(tmp) / f"crop_{n}.jpg"
            crop.save(crop_p, quality=92)
            rec["crop"] = str(crop_p)
            try:
                # 1. blind location: the label, the whole photo, no point
                found = ask("locate", [label], CHECK_LOCATE_SYSTEM,
                            [{"type": "text", "text": "The whole photo:"}, image_block(whole, LOCATE_PX),
                             {"type": "text", "text": f"Site: {topic_title}\nFeature: {label}\n"
                                                      "Is it in the photo, and where is its box?"}], CheckLocate)
                rec["blind_locate"] = found.model_dump()
                if not found.same_site:
                    log.info("Callouts: the check says the photo does not show the site; every label dropped.")
                    rec["result"] = "dropped: not the same site"
                    records.append(rec)
                    kept = []
                    break
                box = _valid_box(found.box)
                iou = round(_iou(loc, box), 3) if (box and loc) else None
                rec["iou"] = iou
                if not (found.visible and found.confidence == "high"):
                    raise _Drop(f"the blind location did not find it ({found.confidence})")
                if iou is None:
                    raise _Drop("the check returned no box")
                if iou < MIN_IOU:
                    raise _Drop(f"the check's box disagrees (IoU {iou} < {MIN_IOU})")
                # 2. blind look at the crosshair: no label
                ctx_p = _ringed(src, x, y, side, Path(tmp) / f"ctx_{n}.jpg")
                look = ask("look", [at], LOOK_SYSTEM,
                           [{"type": "text", "text": "Close-up, centred on the point (red crosshair):"},
                            image_block(crop_p, VERIFY_PX),
                            {"type": "text", "text": "Whole photo, the point ringed in red:"}, image_block(ctx_p, 512),
                            {"type": "text", "text": f"Site: {topic_title}\nPoint: x={x / src.width:.4f}, "
                                                     f"y={y / src.height:.4f} of the whole photo\n"
                                                     "What physical feature is at the crosshair?"}], CheckLook)
                rec["blind_look"] = look.model_dump()
                if look.confidence != "high" or not look.feature.strip():
                    raise _Drop(f"the blind look was not sure ({look.confidence})")
                # 3. the look matched to the label, in a separate text call
                match = ask("match", [at, label, look.feature], MATCH_SYSTEM,
                            f"Site: {topic_title}\nLabel: {label}\nDescribed: {look.feature}\n"
                            "Do they name the same physical feature?", LabelMatch)
                rec["match"] = match.model_dump()
                if not (match.same_feature and match.confidence == "high"):
                    raise _Drop(f"the blind look saw '{look.feature}'")
                # 4. the decoy: the label asked about at a point beside the feature
                dx_, dy_ = decoy_point(x, y, fw / k, src.size)
                dcrop = verify_crop(src, dx_, dy_, side)
                dcrop_p = (keep / f"decoy_{n}.jpg") if keep else Path(tmp) / f"decoy_{n}.jpg"
                dcrop.save(dcrop_p, quality=92)
                rec["decoy_crop"], rec["decoy_xy"] = str(dcrop_p), [round(dx_, 1), round(dy_, 1)]
                dctx_p = _ringed(src, dx_, dy_, side, Path(tmp) / f"dctx_{n}.jpg")
                decoy = ask("decoy", [f"{dx_:.0f},{dy_:.0f},{side:.0f}", label], VERIFY_SYSTEM,
                            [{"type": "text", "text": "Close-up, centred on the point (red crosshair):"},
                             image_block(dcrop_p, VERIFY_PX),
                             {"type": "text", "text": "Whole photo, the point ringed in red:"},
                             image_block(dctx_p, 512),
                             {"type": "text", "text": f"Site: {topic_title}\nLabel: {label}\nPoint: "
                                                      f"x={dx_ / src.width:.4f}, y={dy_ / src.height:.4f} of the "
                                                      "whole photo\nIs the crosshair on exactly this feature?"}],
                            LabelCheck)
                rec["decoy"] = decoy.model_dump()
                if decoy.shows_feature:
                    raise _Drop("the decoy point was also called the feature")
            except _Drop as why:
                log.info("Callout '%s' dropped by the check: %s.", label, why)
                rec["result"] = f"dropped: {why}"
                records.append(rec)
                continue
            except Exception as e:  # noqa: BLE001 - LLMError, a malformed answer, a dropped link: dropped
                log.info("Callout '%s' dropped: its check failed (%s).", label, e)
                rec["result"] = f"error: {e}"
                records.append(rec)
                continue
            t = trust(p)
            t["iou"] = iou
            t["evidence_tier"] = "B" if (t.get("tier") == "B" or iou < IOU_A) else "A"
            t["tier"] = "B" if (t["evidence_tier"] == "B" or _kind(p) == "extent") else "A"
            t["check_served"] = rec.get("served", {})
            rec["tier"], rec["result"] = t["tier"], "kept"
            records.append(rec)
            kept.append(_with_trust(p, t))
    if keep:
        (keep / "checks.json").write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8",
                                          newline="\n")
    return kept


class _Drop(Exception):
    """A check that says no: the label is dropped (fail closed)."""


def _with_trust(p: tuple, t: dict[str, Any]) -> tuple:
    """`p` as a full point (label, x, y, area, w, h, kind, box, trust) carrying the trust record `t`."""
    w, h = _extent(p)
    area = p[3] if len(p) > 3 and p[3] else (w * h / (W * H) if w and h else 0.0)
    return (p[0], p[1], p[2], area, w, h, _kind(p), _fbox(p), t)


def unambiguous(points: list[tuple], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> list[tuple]:
    """`points` without the later of two point markers closer than 90 px [80 px], or of two outlines whose boxes
    overlap by IoU over 0.2: a viewer could not tell which is which."""
    near = 80 if width > height else 90
    kept: list[tuple] = []
    for p in points:
        clash = False
        for q in kept:
            if tier(p, width, height) == tier(q, width, height) == "B":
                clash = _iou(_fbox(p), _fbox(q)) > 0.2
            elif tier(p, width, height) == tier(q, width, height) == "A":
                clash = math.hypot(p[1] - q[1], p[2] - q[2]) < near
            if clash:
                log.info("Callouts: '%s' dropped: too close to '%s' to tell apart.", p[0], q[0])
                break
        if not clash:
            kept.append(p)
    return kept


def approved_photos(photos: list[Path], topic: dict[str, Any], stems: Any = ...) -> list[Path]:
    """Only the topic's approved photos (image_review tier "topic" or "kept": the strong set Stories and covers use),
    never a context shot, a beat image or film. `stems` defaults to the topic folder's credits; None means a folder
    without credits, the photos you supplied yourself, which are all yours to label. Unlike image_review.only_strong
    there is no fallback to every photo: none approved -> none."""
    if stems is ...:
        from .config import ASSETS_DIR
        from .image_review import strong_stems
        if not topic.get("id"):
            return []
        stems = strong_stems(ASSETS_DIR / str(topic["id"]))
    stills = [p for p in photos if not is_video(p)]
    if stems is None:
        return stills
    return [p for p in stills if any(Path(p).stem.startswith(s) for s in stems)]


# The gate (round 5 section 9): with --auto-approve a graphic is drawn only while the accuracy audit at
# AUDIT_LATEST passes on the current model and prompt hashes; any change to either voids it. Without the flag a
# human approves every video, and the review shows each label's check crop.
AUDIT_LATEST = Path("audit") / "callouts_latest.json"
AUDIT_SIDECAR = ".audit.json"


def audit_passes(path: Path | None = None) -> bool:
    """Whether the latest accuracy audit passed on the models and prompts in use now."""
    from . import config
    p = Path(path) if path else Path(config.BASE_DIR) / AUDIT_LATEST
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(rec.get("pass") is True and rec.get("models") == models()
                and rec.get("prompt_hashes") == prompt_hashes())


def _audit(clip: Path, photo: Path, points: list[tuple], reveal: list[float], info: list[dict], anchor: Any,
           width: int, height: int, labels: list[str], crop_dir: Path, cache_dir: Path | None) -> Path:
    """<clip>.audit.json: models and prompt hashes, the location answer, each label's check (its crop, verdict and
    box agreement), tier, final coordinates and cue, so the render replays without a model call (replay)."""
    loc = None
    try:
        data = _cache_read(_cache_path(cache_dir, photo))
        hits = [v for v in data.values() if isinstance(v, dict) and v.get("photo") == Path(photo).name
                and v.get("model") == models()["locate"] and v.get("prompt_hash") == prompt_hashes()["locate"]
                and "served" in v]
        loc = hits[-1] if hits else None
    except Exception:  # noqa: BLE001 - the audit records what it can
        loc = None
    try:
        checks = json.loads((crop_dir / "checks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        checks = []
    passes = (loc or {}).get("passes") or []
    mdl = models()
    # the models that actually served (a budget swap, a fallback or the free engine's Claude last resort can differ
    # from the configured `models`): every location pass, and every check answer, drawn label or dropped
    loc_served = [m or "unknown" for m in loc.get("served") or [None]] if loc else []
    chk_served = [m or "unknown" for c in checks for m in (c.get("served") or {}).values()]
    for p in points:
        loc_served += trust(p).get("locate_served") or []
        chk_served += [m or "unknown" for m in (trust(p).get("check_served") or {}).values()]
    served = {"locate": sorted(set(loc_served)), "check": sorted(set(chk_served))}
    distinct = bool(served["locate"] and served["check"] and "unknown" not in served["locate"] + served["check"]
                    and not set(served["locate"]) & set(served["check"]))
    rec = {"clip": Path(clip).name, "photo": str(photo), "photo_sha256": _photo_sha(photo),
           "format": {"width": width, "height": height}, "models": mdl, "prompt_hashes": prompt_hashes(),
           "models_served": served, "models_distinct": distinct,
           "checks_collapsed": [c.get("label") for c in checks if c.get("collapsed")],
           "labels_asked": labels, "location": loc, "location_passes": sum(q is not None for q in passes),
           "location_views": (loc or {}).get("views"),
           "location_spread_px": {p[0]: trust(p).get("spread_px") for p in points},
           "checks": checks, "anchor_beat": anchor,
           "labels": [{"label": p[0], "x": round(p[1], 2), "y": round(p[2], 2), "point": [_json(v) for v in p],
                       "tier": tier(p, width, height), "trust_tier": trust(p).get("tier"),
                       "location_answers_px": trust(p).get("answers_px"), "spread_px": trust(p).get("spread_px"),
                       "iou": trust(p).get("iou"), "kind": _kind(p), "box": _fbox(p),
                       "cue": reveal[k], **{f"cue_{a}": b for a, b in (info[k] if k < len(info) else {}).items()}}
                      for k, p in enumerate(points)]}
    path = Path(str(clip) + AUDIT_SIDECAR)
    path.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return path


def _json(v: Any) -> Any:
    if isinstance(v, tuple):
        return list(v)
    return v


def replay(audit_path: Path, out: Path | None = None) -> Path:
    """Re-renders a graphic from its audit trail alone: no model call."""
    rec = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    pts = [tuple(tuple(v) if isinstance(v, list) else v for v in lab["point"]) for lab in rec["labels"]]
    out = Path(out) if out else Path(audit_path).with_name(rec["clip"])
    return render(Path(rec["photo"]), pts, out, width=rec["format"]["width"], height=rec["format"]["height"],
                  reveal=[lab["cue"] for lab in rec["labels"]])


def make(payload: dict[str, Any], topic: dict[str, Any], photos: list[Path], work_dir: Path, width: int = VIDEO_WIDTH,
         height: int = VIDEO_HEIGHT, words: list[dict[str, Any]] | None = None, approved: Any = ...,
         auto_approve: bool = False, grade_bias: float | None = None) -> Path | None:
    """The labelled-photo graphic if the script names features, an approved topic photo shows two or more, and each
    drawn label passes its own check. `words`: the narration's timed words, so each label appears as it is said
    (payload["beat_times"] alone gives an estimate). `approved`: the approved photo stems (see approved_photos).
    `auto_approve`: no human reviews the video, so the graphic needs a passing accuracy audit (audit_passes).
    `grade_bias`: the bias the master grades the run's photos with (default payload["art_direction"]); the
    labelled photo takes the same photo grade (QF-86, src/photo_grade.py)."""
    from .config import CALLOUTS_ENABLED
    if not CALLOUTS_ENABLED:
        return None
    if grade_bias is None:
        grade_bias = float((payload.get("art_direction") or {}).get("grade_bias") or 0.0)
    labels = payload.get("photo_labels") or []
    if len(labels) < 2:
        return None
    if auto_approve and not audit_passes():
        log.info("Callouts: auto-approve without a passing accuracy audit for these models and prompts; skipped.")
        return None
    try:
        pool = approved_photos(photos, topic, approved)
        if not pool:
            log.info("Callouts: no approved topic photo to label; skipped.")
            return None
        found = plan(pool, labels, topic.get("title", ""), width=width, height=height)
        if not found:
            return None
        photo, points = found
        work_dir.mkdir(parents=True, exist_ok=True)
        crop_dir = work_dir / "callout_checks"
        points = verify(photo, points, topic.get("title", ""), width=width, height=height, crop_dir=crop_dir)
        points = unambiguous(points, width, height)
        if len(points) < 2:
            log.info("Callouts: fewer than two labels passed their check; no graphic.")
            return None
        mag = loupe_mag(native_scale(photo, width, height))
        points = clear_of_captions(points, width, height, mag)
        if len(points) < 2:
            log.info("Callouts: fewer than two labels clear the caption band; no graphic.")
            return None
        from .photo_grade import cover
        ground = cover(photo, (width, height), grade_bias)   # the ground as render draws it (graded, QF-86)
        _, said_at, _ = reveal_plan([p[0] for p in points], payload.get("beat_times"), words, squeeze=False,
                                    landscape=width > height)
        spoken = sorted(range(len(points)), key=lambda k: (said_at[k] is None, said_at[k] or 0.0, k))
        points = declutter(points, width, height, loupe=mag > 0, ground=ground, mag=mag or None, spoken=spoken)
        if not points:
            return None
        anchor, reveal, info = reveal_plan([p[0] for p in points], payload.get("beat_times"), words, squeeze=False,
                                           landscape=width > height)
        said = [k for k, t in enumerate(reveal) if t is not None]   # a label said after the clip ends is not drawn
        if len(said) < 2:
            log.info("Callouts: fewer than two labels are said inside the clip; no graphic.")
            return None
        points, reveal, info = [points[k] for k in said], [reveal[k] for k in said], [info[k] for k in said]
        out = work_dir / "graphic_labels.mp4"
        from .photo_grade import using
        try:
            with using(grade_bias):
                clip = render(photo, points, out, width=width, height=height, reveal=reveal)
        except LowContrast as e:
            log.info("Callouts: %s; no graphic.", e)
            return None
        drawn = drawn_labels(clip)   # render leaves out a label under the non-text contrast floor
        if drawn is not None and drawn != [p[0] for p in points]:
            keep = [k for k, p in enumerate(points) if p[0] in drawn]
            points, reveal, info = [points[k] for k in keep], [reveal[k] for k in keep], [info[k] for k in keep]
        Path(str(clip) + ".cues.json").write_text(json.dumps(
            {"anchor_beat": anchor, "labels": [p[0] for p in points], "reveal": reveal,
             "cues": [dict(i, label=p[0], cue=r) for p, r, i in zip(points, reveal, info)]}), encoding="utf-8")
        try:
            _audit(clip, photo, points, reveal, info, anchor, width, height, labels, crop_dir, None)
        except Exception as e:  # noqa: BLE001 - the clip stands; its audit is reported missing
            log.warning("Callouts: audit trail not written (%s).", e)
        return clip
    except Exception as e:  # optional graphic: never sink a run
        log.warning("Labelled photo failed: %s", e)
        return None
