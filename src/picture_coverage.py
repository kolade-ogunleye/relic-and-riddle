"""
src/picture_coverage.py
=======================
Every narrated line has a planned, honest visual (2026-11-02).

Renders failed the video check for "no photo for this beat": a line named something no approved photo showed, and
whatever stood under it was flagged. Two halves:

1. Before the voice (src/prevoice_lines.py) each sentence gets a planned visual class (VISUAL_CLASSES): a named
   subject needs a real photo of that very thing; a kind of thing takes a photo of the same kind and period; an
   abstract line (a number, time, comparison or question) is carried by a graphic or any topic photo.
2. At the render, a beat whose picture does not show its line is filled by the fallback cascade, in fixed order
   (TIERS): the exact photo, a photo of the same kind, a detail crop (punch-in) of one of the topic's strongest
   approved photos on a region not already shown, archive film, the locator map when the line names a place, one of
   our designed graphics, and last a labelled reconstruction (the existing image_gen path and its cap). Holding the
   previous shot (HELD) is what is left when nothing in the cascade could fill the beat.

Which tier filled each beat is recorded in the run folder (COVERAGE_FILE), so the video check and the operator see
it: a beat a lower tier covered honestly is a warning, never a blocker (src/video_qa.coverage_severity), while a
beat showing a wrong or invented subject stays a blocker.
"""

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ the per-line visual plan (before the voice)
NAMED_SUBJECT = "named_subject"   # needs a real photo of that very thing
KIND_OF_THING = "kind_of_thing"   # a photo of the same kind and period is honest
ABSTRACT = "abstract"             # a number, time, comparison or question: a graphic or any topic photo
VISUAL_CLASSES = (NAMED_SUBJECT, KIND_OF_THING, ABSTRACT)

# ------------------------------------------------------------------ the fallback cascade (at the render)
EXACT = "exact"
SAME_KIND = "same_kind"
DETAIL_CROP = "detail_crop"
ARCHIVE_FILM = "archive_film"
MAP = "map"
GRAPHIC = "graphic"
RECONSTRUCTION = "reconstruction"
TIERS = (EXACT, SAME_KIND, DETAIL_CROP, ARCHIVE_FILM, MAP, GRAPHIC, RECONSTRUCTION)
LOWER_TIERS = TIERS[1:]
HELD = "held"            # beyond the cascade: the previous shot held on
UNJUDGED = "unjudged"    # a still the planner placed that no look judged against its line (the hook, a stopped check)
COVERAGE_FILE = "beat_coverage.json"
DETAIL_OPERATION = "detail_crop"
DETAIL_DIR = "detail_crops"

# A punch-in: a window of the frame's shape, DETAIL_SCALE of the largest that fits the photo, at least
# config.DETAIL_CROP_MIN_WIDTH source pixels wide, overlapping any region already shown by at most DETAIL_MAX_IOU.
DETAIL_SCALE = 0.55
DETAIL_GRID = 4          # positions tried along each axis
DETAIL_MAX_IOU = 0.25


def tier_rank(tier: str) -> int:
    """Position in the cascade (0 = exact); HELD and UNJUDGED after every tier."""
    return TIERS.index(tier) if tier in TIERS else len(TIERS)


# ------------------------------------------------------------------ detail crops
Box = tuple[float, float, float, float]   # x0, y0, x1, y1 as fractions of the photo


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two boxes (fractions)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _detail(gray, box_px: tuple[int, int, int, int]) -> float:
    """How much there is to see in a region: the mean edge strength of its grayscale pixels."""
    from PIL import ImageFilter, ImageStat
    region = gray.crop(box_px).filter(ImageFilter.FIND_EDGES)
    return float(ImageStat.Stat(region).mean[0])


def choose_region(size: tuple[int, int], aspect: float, used: Sequence[Box], gray=None,
                  min_width: int = 0) -> Box | None:
    """The punch-in window (fractions) with the most detail among DETAIL_GRID x DETAIL_GRID positions, of the
    frame's `aspect` (width / height), that overlaps none of `used` by more than DETAIL_MAX_IOU; None when no
    window is wide enough (min_width source pixels) or every one repeats a region already shown."""
    w, h = size
    if w <= 0 or h <= 0 or aspect <= 0:
        return None
    fit_w = min(w, h * aspect)
    cw = fit_w * DETAIL_SCALE
    ch = cw / aspect
    if cw < min_width or ch > h:
        return None
    best, best_score = None, -1.0
    for i in range(DETAIL_GRID):
        for j in range(DETAIL_GRID):
            x0 = (w - cw) * i / max(1, DETAIL_GRID - 1)
            y0 = (h - ch) * j / max(1, DETAIL_GRID - 1)
            box = (x0 / w, y0 / h, (x0 + cw) / w, (y0 + ch) / h)
            if any(iou(box, u) > DETAIL_MAX_IOU for u in used):
                continue
            score = _detail(gray, (int(x0), int(y0), int(x0 + cw), int(y0 + ch))) if gray is not None else 0.0
            # Ties (a flat picture, or no pixels given) keep the most central window.
            centred = -abs(box[0] + box[2] - 1) - abs(box[1] + box[3] - 1)
            if best is None or score > best_score + 1e-9 or (abs(score - best_score) <= 1e-9 and centred > best[1]):
                best, best_score = (box, centred), score
    return best[0] if best else None


def detail_crop(photo: Path, out_dir: Path, used: Sequence[Box], aspect: float,
                min_width: int = 0) -> tuple[Path, Box] | None:
    """A punch-in of `photo` on a region not already shown (choose_region), saved under out_dir/DETAIL_DIR/<n>/
    with the photo's own name (its credit record is found by name, as a text-band crop's is) and a lineage record
    back to it (operation DETAIL_OPERATION). (path, box), or None when no region qualifies or the photo can't be read."""
    from PIL import Image, ImageOps

    from .lineage import record_derived
    try:
        with Image.open(photo) as raw:
            im = ImageOps.exif_transpose(raw).convert("RGB")
    except (OSError, ValueError) as e:
        log.debug("Detail crop: %s unreadable (%s).", Path(photo).name, e)
        return None
    gray = im.convert("L").reduce(4) if min(im.size) >= 64 else im.convert("L")
    scale = gray.size[0] / im.size[0]
    # Scored on the reduced copy (cheap), in fractions, then cut from the full photo.
    box = choose_region(gray.size, aspect, used, gray=gray, min_width=int(min_width * scale))
    if box is None:
        return None
    px = (round(box[0] * im.size[0]), round(box[1] * im.size[1]), round(box[2] * im.size[0]), round(box[3] * im.size[1]))
    n = 1
    while (Path(out_dir) / DETAIL_DIR / str(n) / Path(photo).name).exists():
        n += 1
    out = Path(out_dir) / DETAIL_DIR / str(n) / Path(photo).name
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        im.crop(px).save(out, "JPEG", quality=95)
        record_derived(out, [photo], DETAIL_OPERATION, {"box": [round(x, 4) for x in box]})
    except (OSError, ValueError) as e:
        log.debug("Detail crop of %s not saved (%s).", Path(photo).name, e)
        return None
    return out, box


def is_detail_crop(p: Path) -> bool:
    """Whether a scene file is a detail crop this module made (by its lineage record's operation)."""
    from .lineage import sidecar
    try:
        return json.loads(sidecar(Path(p)).read_text(encoding="utf-8")).get("operation") == DETAIL_OPERATION
    except (OSError, ValueError, AttributeError):
        return False


# ------------------------------------------------------------------ the record
def _by_asset(p: Path) -> str:
    """The tier a scene file is by what it is (no fit-check row says otherwise)."""
    stem = Path(p).stem
    if stem.startswith("gap_gen_"):
        return RECONSTRUCTION
    if stem.startswith("footage_"):
        return ARCHIVE_FILM
    if stem.startswith("graphic_"):
        from .scene_planner import _is_map
        return MAP if _is_map(Path(p)) else GRAPHIC
    if is_detail_crop(Path(p)):
        return DETAIL_CROP
    return UNJUDGED


def _norm(text: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in str(text or "")).split())


def planned_visuals(run_dir: Path, beats: list[dict[str, Any]]) -> list[str | None]:
    """Each beat's planned visual class from the check before the voice (prevoice_lines.RECORD_FILE's verdicts):
    the class of the sentence that carries the beat's words (the sentence the beat starts in), None when unknown."""
    try:
        from .prevoice_lines import RECORD_FILE
        record = json.loads((Path(run_dir) / RECORD_FILE).read_text(encoding="utf-8"))
        verdicts = [v for v in record.get("verdicts") or [] if isinstance(v, dict)]
    except (OSError, ValueError, AttributeError):
        return [None] * len(beats)
    sentences = [(_norm(v.get("sentence")), v.get("visual")) for v in verdicts]
    out: list[str | None] = []
    for b in beats:
        words = _norm(b.get("text"))
        head = " ".join(words.split()[:4])
        hit = next((vis for sent, vis in sentences if words and (words in sent or (head and head in sent))), None)
        out.append(hit if hit in VISUAL_CLASSES else None)
    return out


def coverage(beats: list[dict[str, Any]], scenes: list[Path], fit: dict[str, Any] | None,
             visuals: list[str | None] | None = None) -> list[dict[str, Any]]:
    """One row per beat of the video: {"beat", "line", "picture", "tier", "cascade", "planned_visual"}. The tier is
    the fit check's own record (fit["tiers"]) when it names the picture the beat now has; otherwise it is read from
    the picture (a judged fit is EXACT, a held shot HELD, a reconstruction, film, map or graphic by its file).
    "cascade": the tier was chosen because no photo showed the line (only those may turn a finding into a warning)."""
    fit = fit or {}
    rows_by_beat: dict[int, dict[str, Any]] = {}
    for row in fit.get("tiers") or []:
        if isinstance(row, dict) and isinstance(row.get("beat"), int):
            rows_by_beat[row["beat"]] = row
    fits = set(fit.get("fits") or [])
    held = {r.get("beat") for r in fit.get("held") or [] if isinstance(r, dict)}
    out = []
    for i, (beat, pic) in enumerate(zip(beats, scenes), 1):
        name = Path(pic).name
        row = rows_by_beat.get(i)
        if row and Path(str(row.get("to") or "")).name == name:
            tier, cascade = str(row.get("tier")), bool(row.get("cascade", row.get("tier") != EXACT))
        elif i in held:
            tier, cascade = HELD, False
        else:
            tier = _by_asset(Path(pic))
            if tier == UNJUDGED and i in fits:
                tier = EXACT
            cascade = False
        out.append({"beat": i, "line": " ".join(str(beat.get("text") or "").split()), "picture": name,
                    "tier": tier, "cascade": cascade,
                    "planned_visual": (visuals[i - 1] if visuals and i <= len(visuals) else None) or beat.get("visual")})
    return out


def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Beats per tier, in cascade order."""
    counts: dict[str, int] = {}
    for r in sorted(rows, key=lambda r: tier_rank(r.get("tier", ""))):
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    return counts


def write(run_dir: Path, beats: list[dict[str, Any]], scenes: list[Path],
          fit: dict[str, Any] | None) -> dict[str, Any]:
    """Records the beats' coverage in run_dir/COVERAGE_FILE and returns it ({"beats", "summary"}). Never raises:
    a record that cannot be written is logged and still returned."""
    rows = coverage(beats, scenes, fit, planned_visuals(run_dir, beats))
    record = {"tiers": list(TIERS), "beats": rows, "summary": summary(rows)}
    try:
        from .atomic_files import write_json
        write_json(Path(run_dir) / COVERAGE_FILE, record)
    except (OSError, ValueError, TypeError) as e:
        log.warning("Beat coverage not recorded (%s).", e)
    return record


def load(run_dir: Path) -> dict[str, Any]:
    """The run's coverage record, {} when none or unreadable."""
    try:
        data = json.loads((Path(run_dir) / COVERAGE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cascade_beats(record: dict[str, Any] | None) -> dict[int, str]:
    """{beat: tier} of the beats a lower cascade tier filled because no photo showed the line."""
    return {int(r["beat"]): str(r["tier"]) for r in (record or {}).get("beats") or []
            if isinstance(r, dict) and r.get("cascade") and r.get("tier") in LOWER_TIERS and isinstance(r.get("beat"), int)}
