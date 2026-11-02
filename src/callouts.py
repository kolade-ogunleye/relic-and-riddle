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
    return stills[result.image - 1], points[:4]


# Label geometry and motion (panel round 2, 2026-11-02; operator round 3, 2026-11-02). The labels stay inside the
# Reel text box on 9:16 (x 83..870, clear of the Shorts/Reels action rail and Meta's inset) and inside broadcast
# title-safe on 16:9. Each leader is a 3 px gold line on a 5 px ink halo either side and each dot has an ink ring,
# so neither vanishes on gold rock. The label is Montserrat Bold on an ink plate (brand INK at 230/255), at most
# two lines. One attachment rule (operator, round 3: leaders met the plate near a corner and the text alignment
# flipped per label): the leader meets the plate at the CENTRE of the edge nearest the dot, never a corner. A
# mostly vertical leader meets the top or bottom edge at its middle and the text is centred in its plate; a mostly
# horizontal one meets a side at mid-height and the text is set flush toward the leader. Layouts with top/bottom
# attachment are preferred, so most labels read centred. 16:9 leaders are elbows (out from the dot, then up or down
# to the plate's top/bottom centre). Plates keep LABEL_GAP px from each other, from every dot and from every other
# leader. The leader draws first, then the label fades in. Every label line is drawn at a measured contrast and
# recorded beside the clip (<clip>.contrast.json).
LABEL_PX = {"portrait": 42, "landscape": 36}
LABEL_MAX_W = {"portrait": 400, "landscape": 460}   # the label's text column, before its plate padding
PLATE_PAD_X, PLATE_PAD_Y, PLATE_ALPHA = 18, 12, 230
LEADER_W, LEADER_HALO = 3, 5                         # gold line width; ink halo on each side of it
DOT_AT, LEADER_DRAW, LABEL_FADE, STAGGER = 0.5, 0.3, 0.3, 1.0   # seconds
ELBOW_RUN, ELBOW_RISE = 120, 110                     # 16:9: horizontal run from the dot, vertical gap to the plate
LABEL_GAP = 24                                       # px between a plate and another plate, a dot or a leader
DOT_R = 25                                           # the dot's outer ring at its widest pulse
PUSH_IN = 0.06                                       # the background's push-in over the clip (dots move with it)
RISES = (110, 150, 200, 260, 340, 440)               # leader lengths tried, shortest first
SIDE_RUNS = (90, 140, 200)                           # side-attached fallback: horizontal leader lengths


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


def _candidates(x: float, y: float, pw: int, ph: int, bounds: tuple, landscape: bool, width: int) -> list[dict]:
    """Placements for a pw x ph plate for the dot at (x, y), each with a preference `rank` (lower is better), the
    edge its leader meets and its leader path. Top/bottom attachments (centred text) rank before side ones."""
    bx0, by0, bx1, by1 = bounds
    out: list[dict] = []
    outward = 1 if x >= width / 2 else -1
    above_first = y - RISES[0] - ph >= by0   # above the feature when there is room, as a caption reads

    def add(box: tuple, edge: str, elbow: float | None, rank: float) -> None:
        if box[0] < bx0 or box[2] > bx1 or box[1] < by0 or box[3] > by1:
            return
        end = _attach(box, edge)
        path = [(x, y), *([(elbow, y)] if elbow is not None else []), end]
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
                    continue   # no longer a mostly vertical leader
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


def layout_labels(points: list[tuple], width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> list[dict[str, Any]]:
    """Where each label goes: its lines and font, its plate box (inside text_box), the edge its leader meets
    ("top"/"bottom"/"left"/"right"), its text anchor, and the leader's fixed end at that edge's centre. In the order
    of `points` (each (label, x, y[, area])). All labels are placed together (a small branch-and-bound over each
    label's candidate placements), so an early label never takes the only room a later one had."""
    landscape = width > height
    key = "landscape" if landscape else "portrait"
    bounds = bx0, by0, bx1, by1 = text_box(width, height)
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    z = 1.0 + PUSH_IN

    def dot_box(x: float, y: float, r: float) -> tuple:   # the dot where it starts and where the push-in takes it
        zx, zy = width / 2 + (x - width / 2) * z, height / 2 + (y - height / 2) * z
        return (min(x, zx) - r, min(y, zy) - r, max(x, zx) + r, max(y, zy) + r)

    loupes = [_needs_loupe(p) for p in points]
    dots = [dot_box(p[1], p[2], _loupe_r(width, height) if lp else DOT_R) for p, lp in zip(points, loupes)]
    fitted, cands = [], []
    for k, p in enumerate(points):
        label, x, y = p[0], p[1], p[2]
        lines, font = _fit_label(measure, label.upper(), LABEL_PX[key], LABEL_MAX_W[key])
        step = round(font.size * 1.2)
        tw = int(max(measure.textlength(ln, font=font) for ln in lines))
        pw, ph = tw + 2 * PLATE_PAD_X, step * len(lines) + 2 * PLATE_PAD_Y
        fitted.append((lines, font, step))
        cs = _candidates(x, y, pw, ph, bounds, landscape, width)
        if loupes[k]:   # the leader leaves the loupe's rim and runs LOUPE_LEADER px or more before the plate
            r = _loupe_r(width, height)
            cs = [c for c in cs if not _overlaps(c["box"], dots[k], LOUPE_LEADER)
                  and (c["elbow"] is None or abs(c["elbow"] - x) >= r + LOUPE_LEADER)] or cs
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
                   "path": [(x, y), _attach(box, edge)], "rank": 99, "static": 99}]
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
        c, (lines, font, step) = pick[k], fitted[k]
        box = [round(v) for v in c["box"]]
        edge = c["edge"]
        end = (round(c["end"][0]), round(c["end"][1]))
        if edge in ("top", "bottom"):
            tx, anchor = (box[0] + box[2]) / 2, "mm"
        elif edge == "left":
            tx, anchor = box[0] + PLATE_PAD_X, "lm"
        else:
            tx, anchor = box[2] - PLATE_PAD_X, "rm"
        out.append({"lines": lines, "font": font, "step": step, "box": box, "attach": edge, "on_right": end[0] >= p[1],
                    "text_x": round(tx), "anchor": anchor, "end": end, "loupe": loupes[k],
                    "elbow": (round(c["elbow"]), p[2]) if c["elbow"] is not None else None})
    return out


# A loupe (operator round 3): a feature the vision model located as small (under LOUPE_AREA of the frame) gets a
# circular inset at its dot, the photo magnified LOUPE_MAG times inside a 2 px GOLD ring on an ink halo, so the
# viewer can see what the label names. Its leader leaves from the loupe's rim.
LOUPE_AREA, LOUPE_MAG = 0.04, 2.2
LOUPE_R = {"portrait": 100, "landscape": 90}
LOUPE_LEADER = 56          # px of visible leader between the loupe's rim and its plate


def _needs_loupe(point: tuple) -> bool:
    return len(point) > 3 and point[3] is not None and 0 < float(point[3]) < LOUPE_AREA


def _loupe_r(width: int, height: int) -> int:
    return LOUPE_R["landscape" if width > height else "portrait"]


def _loupe(im: Image.Image, src: Image.Image, cx: float, cy: float, r: int, a: float) -> Image.Image:
    """`src` around (cx, cy) magnified LOUPE_MAG times in a disc of radius r on `im`, at opacity `a`."""
    from PIL import ImageChops
    s = r / LOUPE_MAG
    patch = src.resize((2 * r, 2 * r), Image.LANCZOS, box=(cx - s, cy - s, cx + s, cy + s))
    mask = Image.new("L", (2 * r, 2 * r), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=255)
    if a < 1:
        mask = ImageChops.multiply(mask, Image.new("L", mask.size, round(255 * a)))
    im = im.copy()
    im.paste(patch, (round(cx - r), round(cy - r)), mask)
    d = ImageDraw.Draw(im, "RGBA")
    d.ellipse([cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4], outline=rgba(INK, int(200 * a)), width=6)
    d.ellipse([cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1], outline=rgba(GOLD, int(255 * a)), width=2)
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
MIN_REVEAL_GAP = 0.6   # seconds between two labels' dots
REVEAL_LAST = CLIP_SECONDS - (0.1 + LEADER_DRAW + LABEL_FADE + 0.9)   # the last dot leaves the label 0.9 s on screen


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
                 words: list[dict[str, Any]] | None = None) -> tuple[int | None, list[float]]:
    """(anchor beat index or None, each label's dot time in the clip). Labels the anchor beat never names, and every
    label when no beat names two, keep the fixed stagger after the ones that are said."""
    stagger = [DOT_AT + STAGGER * k for k in range(len(labels))]
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
    for k in sorted(range(len(labels)), key=lambda j: (at.get(j, float("inf")), j)):
        t = max(DOT_AT, at.get(k, (last + STAGGER) if last is not None else DOT_AT))
        if last is not None:
            t = max(t, last + MIN_REVEAL_GAP)
        out[k] = last = t
    if max(out) > REVEAL_LAST:   # squeeze into the clip, keeping the order and the gaps' proportions
        lo = min(out)
        span = max(out) - lo
        room = max(REVEAL_LAST - DOT_AT, 0.1)
        out = [DOT_AT + (t - lo) * room / span if span else DOT_AT for t in out]
    return bi, [round(t, 3) for t in out]


def render(photo: Path, points: list[tuple], out: Path, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
           reveal: list[float] | None = None) -> Path:
    """Slow push-in on the photo; each feature in turn (at `reveal[k]` seconds, else on a fixed stagger) gets a
    pulsing dot (or a loupe, for a small feature), then its leader draws out (0.3 s), then its label fades in.
    Labels stay inside the platforms' text box; each leader meets its plate at an edge centre."""
    base = cover_crop(photo, (width, height)).convert("RGB")
    n = int(CLIP_SECONDS * VIDEO_FPS)
    labels = layout_labels(points, width, height)
    starts = list(reveal) if reveal and len(reveal) == len(points) else [DOT_AT + STAGGER * k for k in range(len(points))]
    loupe_r = _loupe_r(width, height)

    def background(t: float) -> Image.Image:
        z = 1.0 + PUSH_IN * (t / CLIP_SECONDS)  # gentle push-in, centred (points scale with it)
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
            z = 1.0 + PUSH_IN * (t / CLIP_SECONDS)
            bg = background(t)
            im = bg.copy()
            for k, p in enumerate(points):
                x, y = p[1], p[2]
                s = starts[k]
                a_dot = _ease((t - s) / 0.25)
                if a_dot <= 0:
                    continue
                lab = labels[k]
                px, py = width / 2 + (x - width / 2) * z, height / 2 + (y - height / 2) * z
                path = [(px, py), *([(lab["elbow"][0], py)] if lab["elbow"] else []), lab["end"]]
                if lab["loupe"]:
                    path = _from_rim(path, loupe_r + 4)
                drawn = _partial(path, _ease_io((t - s - 0.1) / LEADER_DRAW))
                d = ImageDraw.Draw(im, "RGBA")
                if len(drawn) > 1:
                    d.line(drawn, fill=rgba(INK, int(200 * a_dot)), width=LEADER_W + 2 * LEADER_HALO, joint="curve")
                    d.line(drawn, fill=rgba(GOLD, int(255 * a_dot)), width=LEADER_W, joint="curve")
                if lab["loupe"]:
                    im = _loupe(im, bg, px, py, round(loupe_r * (0.6 + 0.4 * a_dot)), a_dot)
                    d = ImageDraw.Draw(im, "RGBA")
                else:
                    pulse = 14 + 8 * abs(((t * 1.2) % 1.0) - 0.5) * 2
                    d.ellipse([px - pulse - 3, py - pulse - 3, px + pulse + 3, py + pulse + 3],
                              outline=rgba(INK, int(190 * a_dot)), width=9)
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
        anchor, reveal = reveal_times([p[0] for p in points], payload.get("beat_times"), words)
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
