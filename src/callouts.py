"""
src/callouts.py
===============
Labelled photos: a real photo of the site with animated callouts naming the features the
narration talks about ("ventilation shaft", "rolling stone door"). Original, educational, and it
tells viewers what they're looking at.

Truthfulness: labels come only from the script's photo_labels (features named in the fact-checked
narration). Claude's vision looks at the photos exactly as the video crops them (9:16) and must
locate a feature with HIGH confidence for its label to be proposed; it picks the one approved topic photo
that shows the most features. Each proposed label is then checked again, independently, on a full-resolution
crop around its dot (verify) and dropped unless that check is a high-confidence yes. Fewer than two labels
left -> no graphic. The reviewer sees it in the video.
"""

import logging
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel

from . import safe_zones
from .brand import INK, rgba
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
from .llm import LLMError, image_block
from .llm import task as llm_task
from .typography import (
    balanced_wrap,
    cover_crop,
    fit_lines,
    get_font,
    is_video,
    plan_text,
    wrap_text,
)

log = logging.getLogger(__name__)
W, H = VIDEO_WIDTH, VIDEO_HEIGHT
MAX_PHOTOS = 6
MAX_LABELS = 3   # panel round 3: four labels crowded the frame with near-crossing leaders; three at most


class Located(BaseModel):
    label: str
    visible: bool
    x: float                              # 0-1 across the image, at the feature
    y: float                              # 0-1 down the image
    w: float                              # 0-1: the feature's width as a share of the image width
    h: float                              # 0-1: its height as a share of the image height
    confidence: Literal["high", "medium", "low"]


class CalloutPlan(BaseModel):
    image: int                            # 1-based number of the best photo (0 if none shows two features)
    features: list[Located]


SYSTEM = """You annotate photos for a history documentary. You get numbered photos of one site and a list
of physical features. Pick the ONE photo that clearly shows the most of these features, and for each
feature say whether it is visible in that photo and where (x, y as fractions of the width and height,
pointing at the feature itself) and how big it is (w, h: its extent as fractions of the width and height). Use confidence "high" only when you are sure that exact feature is
what you are pointing at; never guess. If no photo clearly shows at least two features, return image 0."""


@llm_task("photo_callouts")
def plan(photos: list[Path], labels: list[str], topic_title: str, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> tuple | None:
    """(photo, [(label, x_px, y_px, area)]) for confidently located features (area: the share of the frame the
    feature covers), or None. Each point is then checked on its own (verify)."""
    stills = [p for p in photos if not is_video(p)][:MAX_PHOTOS]
    from . import free_llm
    if not (free_llm.ready() and stills and labels):
        return None
    crops = []
    content: list[dict] = []
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
    points = [(f.label, f.x * width, f.y * height, max(0.0, f.w) * max(0.0, f.h)) for f in result.features
              if f.visible and f.confidence == "high" and f.label.lower() in wanted
              and 0.05 < f.x < 0.95 and (top - 80) <= (f.y * height) <= (bottom + 40)]
    if len(points) < 2:
        log.info("Callouts: fewer than two confidently located features in safe zone; skipped.")
        return None
    return stills[result.image - 1], points[:MAX_LABELS]


# Label geometry and motion (panel round 2, 2026-11-02; operator round 3, 2026-11-02). The labels stay inside the
# Reel text box on 9:16 (x 83..870, clear of the Shorts/Reels action rail and Meta's inset) and inside broadcast
# title-safe on 16:9. Each leader is a 3 px gold line on a 5 px ink halo either side and each dot has an ink ring,
# so neither vanishes on gold rock. The label is Montserrat Bold on an ink plate (brand INK at 230/255), at most
# two lines. One attachment rule (operator, round 3: leaders met the plate near a corner and the text alignment
# flipped per label): the leader meets the plate at the CENTRE of the edge nearest the dot, never a corner. A
# mostly vertical leader meets the top or bottom edge at its middle and the text is centred in its plate; a mostly
# horizontal one meets a side at mid-height and the text is set flush toward the leader. Layouts with top/bottom
# attachment are preferred, so most labels read centred. 16:9 leaders are elbows (out from the dot, then up or down
# to the plate's top/bottom centre); 9:16 leaders (panel round 3) leave the edge centre square to it for STUB px or
# more, then make one 45-degree run to the dot (_route). Plates keep LABEL_GAP px from each other, from every dot and
# from every other leader. The dot grows, the leader draws out, then the plate wipes open (see DOT_GROW). Every label line is drawn at a measured contrast and
# recorded beside the clip (<clip>.contrast.json).
LABEL_PX = {"portrait": 42, "landscape": 36}
LABEL_MAX_W = {"portrait": 400, "landscape": 460}   # the label's text column, before its plate padding
PLATE_PAD_X, PLATE_PAD_Y, PLATE_ALPHA = 18, 12, 230
LEADER_W, LEADER_HALO = 3, 5                         # gold line width; ink halo on each side of it
# Motion (panel round 3): the dot grows in DOT_GROW, the leader draws out from it in LEADER_DRAW, then the plate
# wipes open from the edge the leader meets in LABEL_FADE. All of it starts LEAD_FRAMES before the word is said.
DOT_AT, DOT_GROW, LEADER_DRAW, LABEL_FADE, STAGGER = 0.5, 0.12, 0.2, 0.15, 1.3   # seconds
LEAD_FRAMES = 2
DIM_OLDER = 0.6                                      # an older label's leader, dot and outline once a newer one is said
ELBOW_RUN, ELBOW_RISE = 120, 110                     # 16:9: horizontal run from the dot, vertical gap to the plate
LABEL_GAP = 24                                       # px between a plate and another plate, a dot or a leader
DOT_R = 25                                           # the dot's outer ring at its widest pulse
PUSH_IN = 0.06                                       # the background's push-in over the clip (dots move with it)
RISES = (110, 150, 200, 260, 340, 440)               # leader lengths tried, shortest first
SIDE_RUNS = (90, 140, 200)                           # side-attached fallback: horizontal leader lengths
STUB = 40                                            # 9:16: the straight run out of the plate edge before the one bend


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


def _seg_hits_box(a: tuple, b: tuple, box: tuple, gap: float = 0) -> bool:
    """Does the segment a-b pass through `box` grown by `gap`? (Liang-Barsky clip.)"""
    x0, y0, x1, y1 = box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap
    dx, dy = b[0] - a[0], b[1] - a[1]
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            lo = max(lo, r)
        else:
            hi = min(hi, r)
        if lo > hi:
            return False
    return True


def _segs_cross(a: tuple, b: tuple, c: tuple, d: tuple) -> bool:
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def _attach(box: tuple, edge: str) -> tuple[float, float]:
    """The centre of the plate edge the leader meets."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return {"top": (cx, box[1]), "bottom": (cx, box[3]), "left": (box[0], cy), "right": (box[2], cy)}[edge]


def _route(dot: tuple[float, float], end: tuple[float, float], edge: str) -> list[tuple[float, float]]:
    """9:16 leader (panel round 3: a steep diagonal under the plate read as leaving from its corner): from the dot
    one 45-degree run, then a straight stub square out of the plate edge's centre; a straight line when the dot is
    level with (or square below/above) the attachment."""
    (x, y), (ex, ey) = dot, end
    if edge in ("top", "bottom"):
        dx = abs(x - ex)
        if dx < 1:
            return [dot, end]
        return [dot, (ex, y - (1 if y > ey else -1) * dx), end]
    dy = abs(y - ey)
    if dy < 1:
        return [dot, end]
    return [dot, (x - (1 if x > ex else -1) * dy, ey), end]


def _stub(path: list[tuple[float, float]]) -> float:
    """The length of a route's last run, the stub out of the plate."""
    (ax, ay), (bx, by) = path[-2], path[-1]
    return ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5


def _route_ok(dot: tuple[float, float], end: tuple[float, float], edge: str, zoomed: tuple[float, float]) -> bool:
    """The 45-degree run fits with a stub of STUB px or more, where the dot starts and where the push-in takes it."""
    for d in (dot, zoomed):
        along = abs(d[1] - end[1]) if edge in ("top", "bottom") else abs(d[0] - end[0])
        across = abs(d[0] - end[0]) if edge in ("top", "bottom") else abs(d[1] - end[1])
        if across >= 1 and along - across < STUB:
            return False
        if along < STUB:
            return False
    return True


def _candidates(x: float, y: float, pw: int, ph: int, bounds: tuple, landscape: bool, width: int,
                height: int | None = None) -> list[dict]:
    """Placements for a pw x ph plate for the dot at (x, y), each with a preference `rank` (lower is better), the
    edge its leader meets and its leader path. Top/bottom attachments (centred text) rank before side ones."""
    bx0, by0, bx1, by1 = bounds
    out: list[dict] = []
    outward = 1 if x >= width / 2 else -1
    above_first = y - RISES[0] - ph >= by0   # above the feature when there is room, as a caption reads
    z = 1.0 + PUSH_IN
    zoomed = (width / 2 + (x - width / 2) * z, (height or 2 * y) / 2 + (y - (height or 2 * y) / 2) * z)

    def add(box: tuple, edge: str, elbow: float | None, rank: float) -> None:
        if box[0] < bx0 or box[2] > bx1 or box[1] < by0 or box[3] > by1:
            return
        end = _attach(box, edge)
        if landscape:
            path = [(x, y), *([(elbow, y)] if elbow is not None else []), end]
        else:
            if not _route_ok((x, y), end, edge, zoomed):
                return
            path = _route((x, y), end, edge)
        out.append({"box": box, "edge": edge, "elbow": elbow, "end": end, "path": path, "rank": rank})

    for above in (True, False):
        for rise in RISES:
            y0 = y - rise - ph if above else y + rise
            if landscape:   # elbow: out from the dot, then up or down to the plate's top/bottom centre
                runs = [d * r for r in (ELBOW_RUN, 180, 260) for d in (outward, -outward)]
            else:           # a straight leader up or down to the plate's top/bottom centre, the plate a little off
                runs = [0, -pw * 0.3, pw * 0.3, -pw * 0.45, pw * 0.45]   # centre if the way is blocked
            for run in runs:
                x0 = min(max(x + run - pw / 2, bx0), bx1 - pw)
                ax = x0 + pw / 2
                if not landscape and abs(ax - x) > 0.75 * rise:
                    continue   # no longer a mostly vertical leader (the stub check in add() then keeps one bend)
                elbow = ax if landscape and abs(ax - x) >= 40 else None
                rank = rise / 100 + abs(ax - x) / 250 + (0 if above == above_first else 0.6)
                add((x0, y0, x0 + pw, y0 + ph), "bottom" if above else "top", elbow, rank)
    for run in SIDE_RUNS:   # fallback: beside the dot, the leader to the near side's mid-height
        for d in (outward, -outward):
            x0 = x + run if d > 0 else x - run - pw
            add((x0, y - ph / 2, x0 + pw, y + ph / 2), "left" if d > 0 else "right", None, 4 + run / 100)
    return sorted(out, key=lambda c: c["rank"])


def _pair_cost(a: dict, b: dict) -> float:
    """What placing candidates `a` and `b` together costs: a clash (plates closer than LABEL_GAP, a leader through
    the other's plate) is all but forbidden; leaders crossing are only discouraged."""
    clash = _overlaps(a["box"], b["box"], LABEL_GAP)
    clash += any(_seg_hits_box(p, q, b["box"], LABEL_GAP / 2) for p, q in pairwise(a["path"]))
    clash += any(_seg_hits_box(p, q, a["box"], LABEL_GAP / 2) for p, q in pairwise(b["path"]))
    cross = sum(_segs_cross(p, q, r, s) for p, q in pairwise(a["path"]) for r, s in pairwise(b["path"]))
    return clash * 10_000 + cross * 5


def layout_labels(points: list[tuple], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                  loupe: bool = True) -> list[dict[str, Any]]:
    """Where each label goes: its lines and font, its plate box (inside text_box), the edge its leader meets
    ("top"/"bottom"/"left"/"right"), its text anchor, and the leader's fixed end at that edge's centre. In the order
    of `points` (each (label, x, y[, area])). All labels are placed together (a small branch-and-bound over each
    label's candidate placements), so an early label never takes the only room a later one had. A label that still
    clashes with another or crosses its leader is marked `conflict` (declutter drops it). `loupe`: False when the
    photo has no native detail to magnify (see loupe_mag)."""
    landscape = width > height
    key = "landscape" if landscape else "portrait"
    bounds = bx0, by0, bx1, by1 = text_box(width, height)
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    z = 1.0 + PUSH_IN
    lr = _loupe_r(width, height)

    def dot_box(x: float, y: float, r: float) -> tuple:   # the dot where it starts and where the push-in takes it
        zx, zy = width / 2 + (x - width / 2) * z, height / 2 + (y - height / 2) * z
        return (min(x, zx) - r, min(y, zy) - r, max(x, zx) + r, max(y, zy) + r)

    li = loupe_index(points) if loupe else None
    loupes = [k == li for k in range(len(points))]
    dots = [dot_box(p[1], p[2], lr if lp else DOT_R) for p, lp in zip(points, loupes)]
    fitted, cands = [], []
    for k, p in enumerate(points):
        label, x, y = p[0], p[1], p[2]
        lines, font = _fit_label(measure, label.upper(), LABEL_PX[key], LABEL_MAX_W[key])
        step = round(font.size * 1.2)
        tw = int(max(measure.textlength(ln, font=font) for ln in lines))
        pw, ph = tw + 2 * PLATE_PAD_X, step * len(lines) + 2 * PLATE_PAD_Y
        fitted.append((lines, font, step, pw, ph))
        if loupes[k]:   # the inset rides with its plate: stacked beyond it (top/bottom leader) or beside it (side)
            tall = [c for c in _candidates(x, y, max(pw, 2 * lr), ph + LOUPE_GAP + 2 * lr, bounds, landscape, width,
                                           height) if c["edge"] in ("top", "bottom")]
            wide = [c for c in _candidates(x, y, pw + LOUPE_GAP + 2 * lr, max(ph, 2 * lr), bounds, landscape, width,
                                           height) if c["edge"] in ("left", "right")]
            cs = sorted(tall + wide, key=lambda c: c["rank"])
            cs = [c for c in cs if not _overlaps(c["box"], dots[k], LOUPE_LEADER)
                  and (c["elbow"] is None or abs(c["elbow"] - x) >= lr + LOUPE_LEADER)] or cs
        else:
            cs = _candidates(x, y, pw, ph, bounds, landscape, width, height)
        for c in cs:
            c["static"] = c["rank"] + 10_000 * (
                _overlaps(c["box"], dots[k], LABEL_GAP // 2)
                + sum(_overlaps(c["box"], d, LABEL_GAP) for j, d in enumerate(dots) if j != k)
                + sum(_seg_hits_box(a, b, d) for a, b in pairwise(c["path"]) for j, d in enumerate(dots) if j != k))
        cs.sort(key=lambda c: c["static"])
        if not cs:   # cannot fit the box at all: pinned inside it, above its dot
            x0 = min(max(x - pw / 2, bx0), bx1 - pw)
            y0 = min(max(y - RISES[0] - ph, by0), by1 - ph)
            box = (x0, y0, x0 + pw, y0 + ph)
            edge = "bottom" if y >= box[3] else "top"
            cs = [{"box": box, "edge": edge, "elbow": None, "end": _attach(box, edge),
                   "path": [(x, y), _attach(box, edge)], "rank": 99, "static": 10_099}]
            loupes[k] = False
        cands.append(cs[:48])
    order = sorted(range(len(points)), key=lambda i: len(cands[i]))
    floor = [min(c["static"] for c in cands[i]) for i in order]
    best: dict[str, Any] = {"cost": float("inf"), "pick": None}
    budget = [60_000]

    def search(depth: int, pick: dict[int, dict], cost: float) -> None:
        budget[0] -= 1
        if cost + sum(floor[depth:]) >= best["cost"] or budget[0] < 0:
            return
        if depth == len(order):
            best["cost"], best["pick"] = cost, dict(pick)
            return
        k = order[depth]
        for c in cands[k]:
            extra = c["static"] + sum(_pair_cost(c, o) for o in pick.values())
            pick[k] = c
            search(depth + 1, pick, cost + extra)
            del pick[k]

    search(0, {}, 0.0)
    pick = best["pick"] or {k: cands[k][0] for k in range(len(points))}
    out: list[dict[str, Any]] = []
    for k, p in enumerate(points):
        c, (lines, font, step, pw, ph) = pick[k], fitted[k]
        unit = [round(v) for v in c["box"]]
        edge = c["edge"]
        end = (round(c["end"][0]), round(c["end"][1]))
        box, loupe_at = unit, None
        if loupes[k]:
            ucx, ucy = (unit[0] + unit[2]) / 2, (unit[1] + unit[3]) / 2
            if edge == "bottom":
                box, loupe_at = [ucx - pw / 2, unit[3] - ph, ucx + pw / 2, unit[3]], (ucx, unit[1] + lr)
            elif edge == "top":
                box, loupe_at = [ucx - pw / 2, unit[1], ucx + pw / 2, unit[1] + ph], (ucx, unit[3] - lr)
            elif edge == "left":
                box, loupe_at = [unit[0], ucy - ph / 2, unit[0] + pw, ucy + ph / 2], (unit[2] - lr, ucy)
            else:
                box, loupe_at = [unit[2] - pw, ucy - ph / 2, unit[2], ucy + ph / 2], (unit[0] + lr, ucy)
            box = [round(v) for v in box]
            loupe_at = (round(loupe_at[0]), round(loupe_at[1]))
        if edge in ("top", "bottom"):
            tx, anchor = (box[0] + box[2]) / 2, "mm"
        elif edge == "left":
            tx, anchor = box[0] + PLATE_PAD_X, "lm"
        else:
            tx, anchor = box[2] - PLATE_PAD_X, "rm"
        conflict = c["static"] >= 10_000 or any(_pair_cost(c, pick[j]) > 0 for j in pick if j != k)
        out.append({"lines": lines, "font": font, "step": step, "box": box, "unit": unit, "attach": edge,
                    "on_right": end[0] >= p[1], "text_x": round(tx), "anchor": anchor, "end": end,
                    "loupe": loupes[k], "loupe_at": loupe_at, "conflict": bool(conflict),
                    "elbow": (round(c["elbow"]), p[2]) if c["elbow"] is not None else None})
    return out


def declutter(points: list[tuple], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
              loupe: bool = True) -> list[tuple]:
    """At most MAX_LABELS points, then the lowest-priority one (the last) dropped while any label still clashes
    with another or a leader crosses another (panel round 3), never below two."""
    pts = list(points[:MAX_LABELS])
    while len(pts) > 2 and any(lab["conflict"] for lab in layout_labels(pts, width, height, loupe)):
        log.info("Callouts: '%s' dropped: its leader or plate could not clear the others.", pts[-1][0])
        pts = pts[:-1]
    return pts


# A loupe (operator round 3; panel round 3 spec): the smallest feature the vision model located (under LOUPE_AREA of
# the frame; one loupe per clip) gets a circular inset beside its label, the photo magnified LOUPE_MAG times (less
# when the photo has fewer native pixels: it never upscales past them, and no loupe under LOUPE_MIN_MAG) inside a
# 2 px GOLD ring cased in INK (2 px each side: 1 px measured under 3:1 on bright rock). The source region is ringed thinly on the photo and its leader runs from that
# ring to the label, so the inset and what it magnifies are tied. It opens with the label, on the spoken word.
LOUPE_AREA, LOUPE_MAG, LOUPE_MIN_MAG = 0.04, 2.0, 1.3
LOUPE_R = {"portrait": 100, "landscape": 90}
LOUPE_LEADER = 56          # px of visible leader between the source ring and the plate
LOUPE_GAP = 14             # px between the inset's ring and its plate


def _needs_loupe(point: tuple) -> bool:
    return len(point) > 3 and point[3] is not None and 0 < float(point[3]) < LOUPE_AREA


def loupe_index(points: list[tuple]) -> int | None:
    """The one point that gets the loupe: the smallest feature under LOUPE_AREA, else None."""
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
    mask = Image.new("L", (2 * r, 2 * r), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=255)
    if a < 1:
        mask = ImageChops.multiply(mask, Image.new("L", mask.size, round(255 * a)))
    im = im.copy()
    im.paste(patch, (round(ox - r), round(oy - r)), mask)
    d = ImageDraw.Draw(im, "RGBA")
    d.ellipse([ox - r - 3, oy - r - 3, ox + r + 3, oy + r + 3], outline=rgba(INK, int(255 * a)), width=6)
    d.ellipse([ox - r - 1, oy - r - 1, ox + r + 1, oy + r + 1], outline=rgba(GOLD, int(255 * a)), width=2)
    return im


def _from_rim(path: list[tuple], r: float) -> list[tuple]:
    """`path` starting r px along its first segment (the leader leaves the loupe's rim, not its centre)."""
    (ax, ay), (bx, by) = path[0], path[1]
    d = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    if d <= r:
        return path[1:] if len(path) > 2 else [path[1], path[1]]
    return [(ax + (bx - ax) * r / d, ay + (by - ay) * r / d), *path[1:]]


# Reveal on the word (operator round 3): each label appears when the narration says its feature, not on a fixed
# stagger. The clip is cut onto the beat that names the most labels (its `anchor` beat; recorded beside the clip in
# <clip>.cues.json for the scene planner), so a label's dot lands at that word's time from the beat's start. Word
# timings ({"word", "start", "dur"}) are used when given; otherwise the word's position in the beat's text.
# Panel round 3: each label holds the focus for at least max(MIN_REVEAL_GAP, READ_PER_CHAR x its characters)
# before the next one starts, and the last one stays open that long too; a label whose word falls past the clip is
# not drawn (make: squeeze=False), rather than appearing before it is said.
MIN_REVEAL_GAP, READ_PER_CHAR = 1.2, 0.06   # seconds
OPEN_TIME = DOT_GROW + LEADER_DRAW + LABEL_FADE  # from the dot to the label fully open
REVEAL_LAST = CLIP_SECONDS - (OPEN_TIME + MIN_REVEAL_GAP)   # the last dot leaves its open label 1.2 s on screen


def hold(label: str) -> float:
    """How long `label` keeps the focus before the next one starts."""
    return max(MIN_REVEAL_GAP, READ_PER_CHAR * len(label))


def _tokens(text: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())]


def _mention(beat_tokens: list[str], label: str) -> int | None:
    """Index of the token where `label` is first said in `beat_tokens` (its whole phrase, else its last word, the
    head noun: "ventilation shaft" -> "shaft")."""
    lt = _tokens(label)
    if not lt:
        return None
    for i in range(len(beat_tokens) - len(lt) + 1):
        if beat_tokens[i:i + len(lt)] == lt:
            return i
    head = lt[-1].rstrip("s")
    for i, t in enumerate(beat_tokens):
        if len(head) > 2 and t.rstrip("s") == head:
            return i
    return None


def reveal_times(labels: list[str], beat_times: list[dict[str, Any]] | None,
                 words: list[dict[str, Any]] | None = None, squeeze: bool = True) -> tuple[int | None, list[Any]]:
    """(anchor beat index or None, each label's cue in the clip: the onset of the word that names it). Labels the
    anchor beat never names, and every label when no beat names two, keep the fixed stagger after the ones that are
    said. Each cue waits for the one before it to hold (hold()). Cues past REVEAL_LAST are squeezed into the clip,
    or with squeeze=False are None (that label is not drawn)."""
    stagger = stagger_times(labels)
    beats = [b for b in (beat_times or []) if isinstance(b, dict) and "start" in b]
    if not beats:
        return None, stagger
    scored = []
    for bi, b in enumerate(beats):
        toks = _tokens(str(b.get("text") or ""))
        hits = {k: _mention(toks, lbl) for k, lbl in enumerate(labels)}
        scored.append((sum(v is not None for v in hits.values()), -bi, bi, toks, hits))
    n_hit, _, bi, toks, hits = max(scored)
    if n_hit < 2:
        return None, stagger
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
        else:                                   # no timings: the token's share of the beat
            at[k] = (end - start) * i / max(1, len(toks))
    out: list[float] = [0.0] * len(labels)
    last = None
    prev = ""
    for k in sorted(range(len(labels)), key=lambda j: (at.get(j, float("inf")), j)):
        t = max(DOT_AT, at.get(k, (last + STAGGER) if last is not None else DOT_AT))
        if last is not None:
            t = max(t, last + hold(prev))
        out[k] = last = t
        prev = labels[k]
    if not squeeze:
        return bi, [round(t, 3) if t <= REVEAL_LAST + 1e-9 else None for t in out]
    if max(out) > REVEAL_LAST:   # squeeze into the clip, keeping the order and the gaps' proportions
        lo = min(out)
        span = max(out) - lo
        room = max(REVEAL_LAST - DOT_AT, 0.1)
        out = [DOT_AT + (t - lo) * room / span if span else DOT_AT for t in out]
    return bi, [round(t, 3) for t in out]


def stagger_times(labels: list[str]) -> list[float]:
    """The fixed clock when no beat names the labels: DOT_AT, then STAGGER apart (longer after a long label)."""
    out: list[float] = []
    for k in range(len(labels)):
        out.append(DOT_AT if not k else out[-1] + max(STAGGER, hold(labels[k - 1])))
    return out


# Non-text contrast (panel round 3): every leader, dot and loupe ring is measured on the clip's last frame, its gold
# core against the 90th-percentile luminance of the band its casing covers, and must reach NONTEXT_MIN (WCAG 1.4.11).
# Short of it, the casing goes fully opaque, then the older labels are dimmed less. Recorded in <clip>.contrast.json
# beside the text rows ("nontext").
NONTEXT_MIN = 3.0
CASING_ALPHA = 200
_GUARD = ((CASING_ALPHA, DIM_OLDER), (255, DIM_OLDER), (255, 0.8), (255, 1.0))


def nontext_contrast(frame: Image.Image, mask: Image.Image, band: int) -> float | None:
    """WCAG ratio of a drawn shape's core (`mask`, L, 255 on the gold) against the 90th-percentile luminance of the
    `band` px around it; None when the shape is off the frame."""
    import cv2
    import numpy as np

    from .typography import _lin
    m = (np.asarray(mask) > 127).astype(np.uint8)
    if m.sum() < 8:
        return None
    rgb = np.asarray(frame.convert("RGB"))
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
    ring = cv2.dilate(m, disc) - m
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


def render(photo: Path, points: list[tuple], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
           reveal: list[float] | None = None) -> Path:
    """Slow push-in on the photo; each feature at its cue (`reveal[k]`, the word that names it; else a fixed
    stagger), from LEAD_FRAMES before it: its dot grows (or its loupe's source ring), the leader draws out to the
    plate, the plate wipes open from that edge. Once a newer label is said the older ones' leaders, dots and
    outlines step back to DIM_OLDER; their text stays full. Labels stay inside the platforms' text box."""
    base = cover_crop(photo, (width, height)).convert("RGB")
    n = int(CLIP_SECONDS * VIDEO_FPS)
    native = native_scale(photo, width, height)
    mag = loupe_mag(native)
    labels = layout_labels(points, width, height, loupe=mag > 0)
    cues = list(reveal) if reveal and len(reveal) == len(points) else stagger_times([p[0] for p in points])
    starts = [c - LEAD_FRAMES / VIDEO_FPS for c in cues]
    nexts = [min((s for s in starts if s > starts[k] + 1e-6), default=None) for k in range(len(points))]
    landscape = width > height
    loupe_r = _loupe_r(width, height)
    src_r = loupe_r / mag if mag else 0.0          # the source ring's radius on screen
    view = None
    if any(lab["loupe"] for lab in labels):
        view, _ = _full_res_view(photo, width, height)
    vk = view.width / width if view is not None else 1.0

    def background(t: float) -> Image.Image:
        z = 1.0 + PUSH_IN * (t / CLIP_SECONDS)  # gentle push-in, centred (points scale with it)
        cw, ch = width / z, height / z
        im = base.resize((width, height), Image.LANCZOS,
                         box=((width - cw) / 2, (height - ch) / 2, (width + cw) / 2, (height + ch) / 2)).convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle([0, 0, width, height], fill=(0, 0, 0, int(70 * _ease(t / 0.8))))  # dim slightly so labels read
        return im

    def plates_on(im: Image.Image, lab: dict[str, Any], a: float, outline: float = 1.0) -> None:
        d = ImageDraw.Draw(im, "RGBA")
        d.rounded_rectangle(lab["box"], radius=12, fill=rgba(INK, int(PLATE_ALPHA * a)),
                            outline=rgba(GOLD, int(220 * a * outline)), width=2)

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
    guard = [0] * len(points)       # each label's step in _GUARD

    def ring(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, a: float, mk: Any = None) -> None:
        """A 2 px GOLD ring cased in INK either side (2 px: the measured guard failed at 1 px on bright rock)."""
        d.ellipse([cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3], outline=rgba(INK, int(255 * a)), width=6)
        d.ellipse([cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1], outline=rgba(GOLD, int(255 * a)), width=2)
        if mk is not None:
            mk.ellipse([cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1], outline=255, width=2)

    def draw(t: float, masks: dict | None = None) -> Image.Image:
        z = 1.0 + PUSH_IN * (t / CLIP_SECONDS)
        bg = background(t)
        im = bg.copy()
        for k, p in enumerate(points):
            x, y = p[1], p[2]
            s = starts[k]
            g = _ease((t - s) / DOT_GROW)
            if t < s:
                continue
            lab = labels[k]
            casing, dim_to = _GUARD[guard[k]]
            dim = 1.0 if nexts[k] is None else 1 - (1 - dim_to) * _ease((t - nexts[k]) / 0.25)
            px, py = width / 2 + (x - width / 2) * z, height / 2 + (y - height / 2) * z
            if landscape:
                path = [(px, py), *([(lab["elbow"][0], py)] if lab["elbow"] else []), lab["end"]]
            else:
                path = _route((px, py), lab["end"], lab["attach"])
            if lab["loupe"]:
                path = _from_rim(path, src_r + 5)
            drawn = _partial(path, _ease_io((t - s - DOT_GROW) / LEADER_DRAW))
            mk = {}
            if masks is not None:
                mk = {e: Image.new("L", (width, height), 0) for e in ("leader", "dot")}
                masks[k] = mk
            d = ImageDraw.Draw(im, "RGBA")
            if len(drawn) > 1:
                d.line(drawn, fill=rgba(INK, int(casing * dim)), width=LEADER_W + 2 * LEADER_HALO, joint="curve")
                d.line(drawn, fill=rgba(GOLD, int(255 * dim)), width=LEADER_W, joint="curve")
                if mk:   # the leader's own run: clear of its dot (or source ring) and its plate, which overlap it
                    ml = ImageDraw.Draw(mk["leader"])
                    ml.line(drawn, fill=255, width=LEADER_W, joint="curve")
                    r0 = (src_r + 8) if lab["loupe"] else DOT_R + 8
                    ml.ellipse([px - r0, py - r0, px + r0, py + r0], fill=0)
                    bx = lab["box"]
                    ml.rectangle([bx[0] - 10, bx[1] - 10, bx[2] + 10, bx[3] + 10], fill=0)
            if lab["loupe"]:
                ring(d, px, py, max(1.0, src_r * g), dim, ImageDraw.Draw(mk["dot"]) if mk else None)
            else:
                pulse = (14 + 8 * abs(((t * 1.2) % 1.0) - 0.5) * 2) * g
                d.ellipse([px - pulse - 3, py - pulse - 3, px + pulse + 3, py + pulse + 3],
                          outline=rgba(INK, int(190 * dim)), width=9)
                d.ellipse([px - pulse, py - pulse, px + pulse, py + pulse], outline=rgba(GOLD, int(255 * dim)), width=3)
                d.ellipse([px - 10 * g, py - 10 * g, px + 10 * g, py + 10 * g], fill=rgba(INK, int(230 * dim)))
                d.ellipse([px - 7 * g, py - 7 * g, px + 7 * g, py + 7 * g], fill=rgba(GOLD, int(255 * dim)))
                if mk:
                    md = ImageDraw.Draw(mk["dot"])
                    md.ellipse([px - pulse, py - pulse, px + pulse, py + pulse], outline=255, width=3)
                    md.ellipse([px - 7 * g, py - 7 * g, px + 7 * g, py + 7 * g], fill=255)
            t_open = s + DOT_GROW + LEADER_DRAW
            w = _ease((t - t_open) / LABEL_FADE)
            if lab["loupe"] and view is not None:   # zooms in with the label
                la = _ease((t - t_open) / 0.25)
                if la > 0:
                    r = round(loupe_r * (0.6 + 0.4 * la))
                    im = _loupe(im, view, x * vk, y * vk, r, la, at=lab["loupe_at"], s=src_r * vk / z * r / loupe_r)
                    if masks is not None:
                        mk["ring"] = Image.new("L", (width, height), 0)
                        ax, ay = lab["loupe_at"]
                        ImageDraw.Draw(mk["ring"]).ellipse([ax - r - 1, ay - r - 1, ax + r + 1, ay + r + 1],
                                                           outline=255, width=2)
            if w > 0:
                layer = im.copy()
                plates_on(layer, lab, 1.0, dim)
                for plate in lab["plates"]:
                    layer = plate.draw(layer)
                im = Image.composite(layer, im, _wipe_mask((width, height), lab["box"], lab["attach"], w))
        return im

    def measure_nontext() -> tuple[Image.Image, list[dict[str, Any]]]:
        masks: dict = {}
        frame = draw(t_end, masks)
        rows = []
        for k, mk in masks.items():
            for e, m in mk.items():
                band = LEADER_HALO if e == "leader" else 2
                rows.append({"name": f"label_{k + 1}_{e}", "ratio": nontext_contrast(frame, m, band),
                             "casing": _GUARD[guard[k]][0], "dim": _GUARD[guard[k]][1]})
        return frame, rows

    _, rows = measure_nontext()
    for _ in range(len(_GUARD) - 1):
        short = {int(r["name"].split("_")[1]) - 1 for r in rows if r["ratio"] is not None and r["ratio"] < NONTEXT_MIN}
        short = {k for k in short if guard[k] < len(_GUARD) - 1}
        if not short:
            break
        for k in short:
            guard[k] += 1
        _, rows = measure_nontext()
    final: dict[str, Image.Image] = {}

    def frames():
        for i in range(n):
            im = draw(i / VIDEO_FPS)
            final["frame"] = im
            yield im

    clip = _encode(frames(), out, n, width=width, height=height)
    if "frame" in final:
        record_contrast(clip, final["frame"], [(f"label_{k + 1}_{j + 1}", p) for k, lab in enumerate(labels)
                                               for j, p in enumerate(lab["plates"])])
        import json
        side = Path(str(clip) + ".contrast.json")
        try:
            rec = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = {"clip": Path(clip).name}
        rec["nontext_target"] = NONTEXT_MIN
        rec["nontext"] = rows
        rec["loupe_mag"] = mag if any(lab["loupe"] for lab in labels) else None
        side.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return clip


# Accuracy (operator round 3: labels must be accurate). The planning call's high-confidence point is only a
# proposal: each label is then checked on its own, by a second vision call that never sees the first one's answer,
# on a VERIFY_PX square crop around the dot at the photo's full resolution plus a small context view with the
# point ringed. A label is drawn only on a high-confidence yes; a failed, unclear or unavailable check drops it
# (fail closed). Fewer than two labels left -> no graphic.
VERIFY_PX = 384


class LabelCheck(BaseModel):
    shows_feature: bool                   # the crop's centre shows exactly the named feature
    confidence: Literal["high", "medium", "low"]
    note: str


VERIFY_SYSTEM = """You check labels for a history documentary before they are drawn on a photo. You get a close-up
crop centred on the point a label will point at, and the whole photo with that point ringed. Say whether the centre
of the close-up shows exactly the named physical feature, so a viewer reading the label there is told the truth.
Answer shows_feature true with confidence "high" only when you are sure; if the centre shows something else, or you
cannot tell, answer false. Never guess."""


def _full_res_view(photo: Path, width: int, height: int) -> tuple[Image.Image, float]:
    """The video's view of `photo` at the photo's own resolution (never below the frame's), and its scale to it."""
    with Image.open(photo) as im:
        iw, ih = im.size
    k = max(1.0, min(iw / width, ih / height))
    return cover_crop(photo, (round(width * k), round(height * k))).convert("RGB"), k


@llm_task("photo_callout_verify")
def verify(photo: Path, points: list[tuple], topic_title: str, width: int = VIDEO_WIDTH,
           height: int = VIDEO_HEIGHT) -> list[tuple]:
    """The points whose label an independent vision check confirms on a crop around its dot (fail closed)."""
    import tempfile

    from . import free_llm
    try:
        if not free_llm.ready():
            log.info("Callouts: no vision model to check the labels; none drawn.")
            return []
        view, k = _full_res_view(photo, width, height)
    except Exception as e:  # noqa: BLE001 - unreadable photo, no engine: nothing is checked, so nothing is drawn
        log.warning("Callout check unavailable: %s", e)
        return []
    kept: list[tuple] = []
    with tempfile.TemporaryDirectory() as tmp:
        for n, p in enumerate(points, 1):
            label, x, y = p[0], p[1] * k, p[2] * k
            half = VERIFY_PX / 2
            x0 = min(max(x - half, 0), max(0, view.width - VERIFY_PX))
            y0 = min(max(y - half, 0), max(0, view.height - VERIFY_PX))
            crop_p, ctx_p = Path(tmp) / f"crop_{n}.jpg", Path(tmp) / f"ctx_{n}.jpg"
            view.crop((round(x0), round(y0), round(x0) + VERIFY_PX, round(y0) + VERIFY_PX)).save(crop_p, quality=92)
            ctx = view.copy()
            ctx.thumbnail((512, 512))
            s = ctx.width / view.width
            d = ImageDraw.Draw(ctx)
            r = max(10, round(VERIFY_PX * s / 2))
            d.ellipse([x * s - r, y * s - r, x * s + r, y * s + r], outline=(255, 40, 40), width=3)
            ctx.save(ctx_p, quality=88)
            content = [{"type": "text", "text": "Close-up, centred on the point:"}, image_block(crop_p, VERIFY_PX),
                       {"type": "text", "text": "Whole photo, the point ringed in red:"}, image_block(ctx_p, 512),
                       {"type": "text", "text": f"Site: {topic_title}\nLabel: {label}\n"
                                                "Does the centre of the close-up show exactly this feature?"}]
            try:
                check = free_llm.call(VERIFY_SYSTEM, content, LabelCheck, role="checker", task_class="light",
                                      effort="medium", max_tokens=800)
            except Exception as e:  # noqa: BLE001 - LLMError, a malformed answer, a dropped link: unchecked -> dropped
                log.info("Callout '%s' dropped: its check failed (%s).", label, e)
                continue
            if check.shows_feature and check.confidence == "high":
                kept.append(p)
            else:
                log.info("Callout '%s' dropped by the check (%s): %s", label, check.confidence, check.note[:160])
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


def make(payload: dict[str, Any], topic: dict[str, Any], photos: list[Path], work_dir: Path, width: int = VIDEO_WIDTH,
         height: int = VIDEO_HEIGHT, words: list[dict[str, Any]] | None = None, approved: Any = ...) -> Path | None:
    """The labelled-photo graphic if the script names features, an approved topic photo shows two or more, and each
    drawn label passes its own check. `words`: the narration's timed words, so each label appears as it is said
    (payload["beat_times"] alone gives an estimate). `approved`: the approved photo stems (see approved_photos)."""
    from .config import CALLOUTS_ENABLED
    if not CALLOUTS_ENABLED:
        return None
    labels = payload.get("photo_labels") or []
    if len(labels) < 2:
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
        points = verify(photo, points, topic.get("title", ""), width=width, height=height)
        if len(points) < 2:
            log.info("Callouts: fewer than two labels passed their check; no graphic.")
            return None
        points = declutter(points, width, height, loupe=loupe_mag(native_scale(photo, width, height)) > 0)
        anchor, reveal = reveal_times([p[0] for p in points], payload.get("beat_times"), words, squeeze=False)
        said = [k for k, t in enumerate(reveal) if t is not None]   # a label said after the clip ends is not drawn
        if len(said) < 2:
            log.info("Callouts: fewer than two labels are said inside the clip; no graphic.")
            return None
        points, reveal = [points[k] for k in said], [reveal[k] for k in said]
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / "graphic_labels.mp4"
        clip = render(photo, points, out, width=width, height=height, reveal=reveal)
        import json
        Path(str(clip) + ".cues.json").write_text(json.dumps(
            {"anchor_beat": anchor, "labels": [p[0] for p in points], "reveal": reveal}), encoding="utf-8")
        return clip
    except Exception as e:  # optional graphic: never sink a run
        log.warning("Labelled photo failed: %s", e)
        return None
