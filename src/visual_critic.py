"""QF-136: the visual critic, an ADVISORY check on design render sets before a freeze panel looks at them.

ADVISORY: the visual critic blocks nothing. 1 of 622 planted defects passed it (0.16%: an accent added over a busy ground); a pass is not a guarantee (operator ruling, decisions/FREEZE-HARD-LIMITS-2026-11-17.md).

It emits findings and a pass/fail per frame and per defect class; it never scores a design and never decides that a
design is frozen. A renderer's own record is not evidence for that renderer (independent critique 2026-11-15,
finding 9: drawable() once deleted glyphs while the records still held the signed string, GLYPH-DROP), so:

- Text identity is verified from the pixels. The truth is what the renderer was asked to draw (the signed card field,
  the still's recipe copy, the narration's words), never what it says it drew. Each word of a field is shaped here,
  in a separate code path (Pillow's raqm/HarfBuzz layout with the bundled font files, not font_engine/typography),
  rasterised and found in the frame by normalised cross-correlation; the face, size, case and tracking are searched,
  so only the letters decide. A word that is not in the pixels is a text_identity finding.
- Overruns, cuts and safe zones are judged on the boxes found in the pixels: text_cut (a word crossing the frame
  edge: the edge is inside a word), safe_zone (the repo's safe-zone table, safe_zones: the Reel box and graphics box
  for 9:16 graphics, the Story box, the cover's centre square), overlap (two fields' inks over each other in a still).
- Contrast is measured on the pixels with the found glyph masks (typography.ring_contrast, WCAG AA 4.5:1, QF-88);
  essential marks keep their recorded 3:1 measure (WCAG 1.4.11) as a record-basis check.
- The records are kept as a cross-check: a record box away from where the pixels put its text, or record text that is
  not the truth, is a record_mismatch finding. Checks that only a record can make (a mark's ratio, a fallback face it
  recorded, a box with no text to find) are reported with basis "record".
- Captions (libass, inside ffmpeg) are rendered here from the caption file: their glyph boxes against the plate drawn
  for them (plate_overrun) and the Reel box, a face libass never resolved (the same pixels as a family that cannot
  exist: glyph_fallback, the QF-133 badge case), a character the resolved face lacks, and caption words that are not the
  narration's. The encoded master is decoded at caption times: position, ratio and faces after composition.
- Faces: the pipeline's YuNet model (tools/yunet.onnx) through onnx_session's DirectML path, or the CPU.

It never writes into the render set it reads: outputs go to a report folder outside the set, temporary files to the
system temp folder.
"""
from __future__ import annotations

import io
import itertools
import json
import math
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import safe_zones
from .brand import TEXT_CONTRAST
from .config import BASE_DIR, FONTS_DIR

CLASSES = ("coverage", "text_identity", "record_mismatch", "text_cut", "safe_zone", "overlap", "plate_overrun", "glyph_fallback",
           "contrast", "mark_contrast", "face_overlap")
NONTEXT_MIN = 3.0          # graphics.NONTEXT_MIN (WCAG 1.4.11); the tests assert they are equal
FACE_MODEL = BASE_DIR / "tools" / "yunet.onnx"   # the pipeline's face model (privacy.MODEL_PATH)
FACE_INPUT = 640           # the model's fixed input side
FACE_SCORE = 0.6           # a face for this check (the privacy blur keeps 0.5: it favours recall)
FACE_NMS = 0.3
FACE_MIN_PX = 24           # smaller detections (frame px) are not judged
FACE_SHARE = 0.02          # text over at least this share of a face's box is a finding (round 3: was 0.10)
FALLBACK_PROBE = "RR Critic No Such Face"     # a family name no system has
PLATE_TOLERANCE = 0        # px glyphs may stand past their plate (QF-133 acceptance: 0 px)
WORD_MIN = 0.60            # normalised cross-correlation at which a shaped word is found in the pixels
STYLE_MIN = 0.70           # ... and at which a field's longest word fixes the face, size, case and tracking
OVERLAP_HINT = 0.45        # a missing word matched at least this well where another field's ink lies: the inks cross
RECORD_TOL = 10            # px a record box may differ from the box its text has in the pixels
PIXEL_TOL = 1              # px a shaped word's ink box may differ from the drawn ink (sub-pixel placement; round 3: 2)
CAPTION_SAMPLES = 8        # caption events sampled per caption file / master


# ------------------------------------------------------------------------------------------------ geometry
def _box(b) -> tuple[int, int, int, int]:
    return tuple(round(float(v)) for v in b[:4])  # type: ignore[return-value]


def _inside(b, w) -> bool:
    return b[0] >= w[0] and b[1] >= w[1] and b[2] <= w[2] and b[3] <= w[3]


def _intersects(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _area(b) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _inter(a, b) -> int:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _union(boxes) -> tuple[int, int, int, int]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def finding(cls: str, element: str, detail: str, box=None, value=None, threshold=None, basis: str = "pixels",
            **extra) -> dict[str, Any]:
    out: dict[str, Any] = {"class": cls, "element": element or "", "detail": detail, "basis": basis}
    if box is not None:
        out["box"] = [int(v) for v in box]
    if value is not None:
        out["value"] = value
    if threshold is not None:
        out["limit"] = threshold         # (not "threshold": scripts/check_ffmpeg.py reads that word as an ffmpeg filter)
    out.update(extra)
    return out


# ------------------------------------------------------------------------------------------------ render sets
@dataclass
class Frame:
    """One frame of a render set: its picture, the truth it must show and the layout its renderer recorded."""
    name: str                                  # stable id: the frame's path relative to the set
    kind: str                                  # still | graphic | card | caption | master | unreadable
    size: tuple[int, int]
    deliverable: str                           # reel | story | cover | carousel | wide | none
    image: Path | None = None
    expect: list[dict[str, Any]] | None = None    # [{"field", "text", "truth"}]; None: no truth given
    elements: list[dict[str, Any]] = field(default_factory=list)  # the record: [{"name", "box", "text"?, ...}]
    nontext: list[dict[str, Any]] = field(default_factory=list)
    zone: tuple[int, int, int, int] | None = None
    protected: list[dict[str, Any]] = field(default_factory=list)
    overlap_rule: bool = False                 # the design's rule: no two fields over each other
    source: str = ""
    ass: Path | None = None
    words: Path | None = None                  # the narration (words.json) the captions must show
    video: Path | None = None
    until: float | None = None


def _rel(p: Path, root: Path) -> str:
    try:
        return Path(p).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(p).name


def _deliverable_of(kind: str, size: tuple[int, int]) -> str:
    k = (kind or "").lower()
    if "story" in k:
        return "story"
    if "cover" in k or "thumb" in k:
        return "cover" if size[1] > size[0] else "wide"
    if "carousel" in k or "slide" in k or "gallery" in k:
        return "carousel"
    if size[1] > size[0]:
        return "reel"
    return "wide"


# The recipe fields each still renderer draws (its inputs: the truth for a real run's stills).
RECIPE_FIELDS = {"multi_format.redraw_story_frame": ("slide", ("kicker", "line1", "line2", "body")),
                 "multi_format.redraw_carousel_slide": ("card", ("heading", "title", "body", "kicker", "subtitle")),
                 "visual_engine.redraw_cover": ("copy", ("eyebrow", "title", "tagline", "line1", "line2"))}


def story_truth(slide: dict[str, Any], index: int, count: int, poll: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What a Story frame must show of its slide: kicker, title lines and body; on the frame that asks the poll (the
    last, with two or more options) the body keeps only its context (its questions are the platform sticker's) and
    the poll's question is the headline when the slide has none (the design rule, multi_format)."""
    slide, poll = dict(slide or {}), poll or {}
    if index == count - 1 and len(poll.get("options") or []) >= 2:
        slide["body"] = " ".join(x for x in re.split(r"(?<=[.!?])\s+", str(slide.get("body") or ""))
                                 if not x.rstrip().endswith("?"))
        if poll.get("question") and not (str(slide.get("line1") or "").strip() or str(slide.get("line2") or "").strip()):
            slide["line1"] = str(poll["question"])
    return [{"field": n, "text": slide[n], "truth": "recipe", "caps": n in CAPS_BY_DESIGN}
            for n in ("kicker", "line1", "line2", "body") if isinstance(slide.get(n), str) and tokens(slide[n])]


# The cover's editorial rule (cover_design.must_withhold / withhold_figure, operator default round 3), modelled here
# from the recipe's own inputs: a figure the fact sheet gives as a range or disputes (conflict), or an estimate whose
# hedge cannot be shown (hedged, from the sheet, its qualifier not fitting), is lifted out of every cover line with
# the qualifier before it and an article left dangling. A year is never withheld.
_COVER_QUALIFIERS = ("UP TO", "MORE THAN", "AT LEAST", "APPROXIMATELY", "ROUGHLY", "AROUND", "ALMOST", "NEARLY",
                     "ABOUT", "OVER", "SOME", "APPROX.", "CIRCA", "EST.", "C.")


def cover_withheld(figure: dict[str, Any] | None) -> bool:
    if not figure or figure.get("kind") == "year":
        return False
    if figure.get("conflict"):
        return True
    return bool(figure.get("unit") and figure.get("hedged") and figure.get("fact_id") and not figure.get("hedge_fits", True))


def cover_truth(copy: dict[str, Any], figure: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What a cover must show: its copy lines, after the withhold rule (cover_withheld). A line the rule changes is
    marked `withheld` with what it held before; a line it leaves as a fragment (cover_design.withheld_fragment,
    QF-141) is not drawn, so it is not in the truth."""
    from .cover_design import withheld_fragment
    names = ("eyebrow", "title", "line1", "line2", "tagline")
    out = []
    withhold = cover_withheld(figure) and figure and figure.get("unit")
    value = None
    if withhold:
        try:
            value = float(str(figure["number"]).rstrip("+").replace(",", ""))
        except (TypeError, ValueError):
            value = None
    qual = "|".join(re.escape(q) for q in _COVER_QUALIFIERS)
    num = re.compile(r"(?:(?<![\w.])(?:" + qual + r")\s+)?(?<![\w.])(\d[\d,]*(?:\.\d+)?)\+?(?:[  -]?[A-Za-z².]+)?",
                     re.IGNORECASE)
    for n in names:
        text = copy.get(n)
        if not isinstance(text, str) or not text.strip():
            continue
        row = {"field": n, "text": text, "truth": "recipe", "caps": True}
        if value is not None:
            new = text
            for m in reversed(list(num.finditer(text))):
                try:
                    hit = abs(float(m.group(1).replace(",", "")) - value) < 1e-9
                except ValueError:
                    hit = False
                unit = re.sub(r"^[\d,.+\s -]+", "", m.group(0)[m.start(1) - m.start():])
                if hit and (not unit or unit[:1].casefold() == str(figure["unit"])[:1].casefold()):
                    new = (new[:m.start()] + new[m.end():]).strip()
            if new != text:
                new = re.sub(r"^(THE|A|AN)\s*$", "", new.strip(), flags=re.IGNORECASE)
                new = re.sub(r"^(THE|A|AN)\s+(?=[,.]|$)", "", new, flags=re.IGNORECASE)
                new = re.sub(r"\s{2,}", " ", new).strip(" ,;:-")
                row = {**row, "text": new, "withheld": text}
                if not new or withheld_fragment(new):     # the documented rule (QF-141): no fragment is drawn
                    continue
        out.append(row)
    return out


def expect_from_recipe(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    """The truth of a still from its recipe (the renderer's inputs) under the documented editorial rules; [] when the
    renderer has no truth reader (a coverage finding)."""
    if str(recipe.get("renderer", "")) == "multi_format.redraw_story_frame" and isinstance(recipe.get("slide"), dict):
        return story_truth(recipe["slide"], int(recipe.get("index") or 0), int(recipe.get("count") or 0), recipe.get("poll"))
    if str(recipe.get("renderer", "")) == "visual_engine.redraw_cover" and isinstance(recipe.get("copy"), dict):
        return cover_truth(recipe["copy"], recipe.get("figure"))
    key, names = RECIPE_FIELDS.get(str(recipe.get("renderer", "")), (None, ()))
    src = recipe.get(key) if key else None
    if not isinstance(src, dict):
        return []
    return [{"field": n, "text": src[n], "truth": "recipe", "caps": n in CAPS_BY_DESIGN}
            for n in names if isinstance(src.get(n), str) and src[n].strip()]


# Fields a design sets in capitals whatever the copy's case (multi_format's carousel heading/kicker; cards; a map's
# place); any other field must show the copy's own case (round 3)
CAPS_BY_DESIGN = {"heading", "title", "kicker", "subtitle", "eyebrow", "line1", "line2", "tagline", "place", "claim",
                  "fact", "myth"}


def harness_recipe_truth(recipe: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The truth of a harness still from its kept recipe (story: its slide and poll; carousel: its card), or None."""
    if recipe.get("renderer"):
        return expect_from_recipe(recipe) or None
    if recipe.get("kind") == "story" and isinstance(recipe.get("slide"), dict):
        return story_truth(recipe["slide"], int(recipe.get("index") or 0), int(recipe.get("count") or 0),
                           recipe.get("poll"))
    if isinstance(recipe.get("card"), dict):
        return expect_from_recipe({"renderer": "multi_format.redraw_carousel_slide", "card": recipe["card"]}) or None
    return None


def frames_from_still_layout(path: Path, root: Path) -> list[Frame]:
    """Each still of a still_layout.json (Story frames, carousel slides, covers) as a Frame, with its truth from its
    recipe (None when it has no recipe or its renderer has no truth reader: a coverage finding, never a pass); a
    cover's profile-grid tile (visual_engine.grid_crop: the cover's centre square) as a frame of its own."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for still, rec in sorted(data.items()):
        if not isinstance(rec, dict) or "elements" not in rec:     # an entry the critic cannot read: not judged
            out.append(Frame(name=_rel(Path(path).parent / still, root), kind="unreadable", size=(0, 0),
                             deliverable="none", source=f"{_rel(path, root)}: entry {still!r} has no layout"))
            continue
        size = (int(rec["size"][0]), int(rec["size"][1]))
        image = Path(path).parent / still
        expect = (expect_from_recipe(rec["recipe"]) or None) if rec.get("recipe") else None
        frame = Frame(name=_rel(image, root), kind="still", size=size, deliverable=_deliverable_of(rec.get("kind", ""), size),
                      image=image if image.is_file() else None, expect=expect,
                      elements=[dict(e) for e in rec.get("elements") or []],
                      zone=_box(rec["zone"]) if rec.get("zone") else None,
                      protected=list(rec.get("protected") or []), overlap_rule=True, source=_rel(path, root))
        out.append(frame)
        grid = image.with_name("thumbnail_profile_grid.jpg")
        if still == "thumbnail_cover.jpg" and grid.is_file() and grid.name not in data:
            top = (size[1] - size[0]) // 2
            out.append(Frame(name=_rel(grid, root), kind="still", size=(size[0], size[0]), deliverable="grid",
                             image=grid, expect=expect,
                             elements=[{**e, "box": [e["box"][0], e["box"][1] - top, e["box"][2], e["box"][3] - top]}
                                       for e in frame.elements if e.get("box")],
                             overlap_rule=True, source=_rel(path, root) + " (grid tile of thumbnail_cover.jpg)"))
    return out


def frame_from_record(path: Path, root: Path) -> Frame:
    """A harness record (<frame>.record.json): the renderer's inputs as `expect`, its layout as `elements`."""
    rec = json.loads(Path(path).read_text(encoding="utf-8"))
    image = Path(path).parent / rec["frame"]
    size = (int(rec["size"][0]), int(rec["size"][1]))
    expect = rec.get("expect")
    if isinstance(rec.get("recipe"), dict):    # the recipe is the truth; a record's own expectation must agree
        from_recipe = harness_recipe_truth(rec["recipe"])
        if from_recipe is not None:
            if expect is not None and {(e["field"], e["text"]) for e in expect} !=                     {(e["field"], e["text"]) for e in from_recipe}:
                from_recipe = [{**e, "conflict": "the record's expectation disagrees with its recipe"} for e in from_recipe]
            expect = from_recipe
    if expect is not None and (rec.get("kind") == "card"):
        expect = [{**e, "caps": True} for e in expect]
    return Frame(name=_rel(image, root), kind=rec.get("kind") or "still", size=size,
                 deliverable=rec.get("deliverable") or _deliverable_of(rec.get("kind", ""), size),
                 image=image if image.is_file() else None, expect=expect,
                 elements=[dict(e) for e in rec.get("elements") or []], nontext=list(rec.get("nontext") or []),
                 zone=_box(rec["zone"]) if rec.get("zone") else None, protected=list(rec.get("protected") or []),
                 overlap_rule=bool(rec.get("overlap_rule", rec.get("kind") in ("still", "card"))), source=_rel(path, root))


def frame_from_contrast(path: Path, root: Path) -> Frame | None:
    """A graphic's <clip>.contrast.json, with its poster frame (<clip stem>.png/.jpg) when the set holds one."""
    rec = json.loads(Path(path).read_text(encoding="utf-8"))
    clip = Path(path).name[: -len(".contrast.json")]
    stem = Path(clip).stem
    image = next((Path(path).parent / f"{stem}{e}" for e in (".png", ".jpg") if (Path(path).parent / f"{stem}{e}").is_file()), None)
    elements = []
    for e in rec.get("elements") or []:
        ratio = e.get("graded") if e.get("graded") is not None else e.get("drawn")
        el = {"name": e.get("name"), "text": e.get("text"), "contrast": ratio}
        if e.get("box"):
            el["box"] = list(e["box"])
        elements.append(el)
    nontext = [{"name": n.get("name"), "value": n.get("graded") if n.get("graded") is not None else n.get("drawn")}
               for n in rec.get("nontext") or []]
    if image is None:                    # nothing to look at: judged as a frame that fails (coverage), its record's
        return Frame(name=_rel(Path(path).parent / f"{stem}.png", root), kind="graphic", size=(0, 0),   # marks too
                     deliverable="none", image=None, expect=None, elements=elements, nontext=nontext,
                     source=_rel(path, root))
    with Image.open(image) as im:
        size = im.size
    exp = Path(path).parent / f"{stem}.expect.json"      # the renderer's inputs, kept by the harness
    lineage = Path(path).parent / f"{clip}.lineage.json"   # or a run's own record of them
    if exp.is_file():
        expect = json.loads(exp.read_text(encoding="utf-8"))
    elif lineage.is_file():
        expect = lineage_truth(stem, json.loads(lineage.read_text(encoding="utf-8")).get("settings", {}).get("inputs") or {})
    else:
        expect = None
    return Frame(name=_rel(image, root), kind="graphic", size=(int(size[0]), int(size[1])),
                 deliverable="reel" if size[1] > size[0] else "wide", image=image, expect=expect, elements=elements,
                 nontext=nontext, source=_rel(path, root))


_SKIP_DIRS = {"stage_cache", "audit", "qa_frames", "parallax", "beat_images", "__pycache__"}
GRAPHIC_KINDS = ("map",)      # the graphic kinds with a truth reader (lineage_truth)


def lineage_truth(stem: str, inputs: dict[str, Any]) -> list[dict[str, Any]] | None:
    """A graphic's truth from the inputs its renderer was given (never from its own record): the map's place name.
    None for a kind with no truth reader yet: number cards (the poster frame shows every card, a value's qualifier
    set apart), timelines (labels set in tracked capitals), scale, cross-section, then-and-now (QF-136b): such a
    frame fails as uncovered, never passes on its record."""
    kind = next((k for k in GRAPHIC_KINDS if k in stem.lower()), None)
    if kind == "map":
        place = inputs.get("place") or {}
        name = place.get("label") or place.get("name") or place.get("place")
        return [{"field": "place", "text": str(name), "truth": "input", "caps": True}] if name else None
    return None


# What the gate does not judge (a pass never reads as coverage of these): written into every report.
NOT_GATED = (
    ("graphics other than maps (place name only): number cards, timelines, scale, cross-section, then-and-now, "
     "callouts, quote cards fail as uncovered (QF-136b); a graphic's clip is judged on its poster (last) frame only, "
     "and a clip with no poster record fails as unjudged media"),
    "map labels other than the place (seas, countries) and map label placement",
    ("text outside the truth's fields: text burned into the photograph (a source's licence banner), the licence "
     "credit block, the masthead, a carousel's share line, Story poll option rows and the 'REPLY WITH A OR B' line "
     "(extra words are looked for inside the truth's own recorded boxes and on its own lines only; QF-144 crops "
     "photo-borne banners at source)"),
    "the cover's badge copy and the thumbnail variants a/b/c (the harness renders covers without variants)",
    "end card and loop bridge, first frame, chapter / hook / myth cards in run folders (the harness renders them)",
    "the YouTube thumbnail (unjudged media: it fails as coverage)",
    "captions: reading speed (CPS) and punctuation (narration words are compared without it)",
    "colour-vision (CVD) contrast, lone function words, faux capitals",
    "an accent added over a busy ground (judged on a plain plate only)",
    ("a glyph cut away from a frame or zone edge by under a column of ink (cuts are judged where a word meets a "
     "frame or zone edge)"),
    "faces hidden by the text itself (only faces the detector still finds are judged)",
    "a word burned into a master away from every caption glyph (the master is checked where its captions are)",
    "captions after an end card's start, taken from the run's composition record, are not owed narration",
    ("libass's own rendering of the caption file is trusted (the .ass is checked against the narration, the master "
     "against the .ass)"),
)
# Files a run folder holds that are not deliverables (listed in not_judged with this reason, never failed): the
# pipeline's review proxies and strips
NON_DELIVERABLES = ("review_scene_strip.jpg", "review_proxy.mp4", "review_contact_sheet.jpg")


def discover(root: Path) -> list[Frame]:
    """Every judged frame of a render set (a harness set or a real run folder), sorted by name."""
    root = Path(root)
    frames: list[Frame] = []
    for p in sorted(root.rglob("*")):
        rel_parts = p.relative_to(root).parts
        if not p.is_file() or _SKIP_DIRS & set(rel_parts[:-1]):
            continue
        n = p.name
        try:
            if n.endswith(".record.json"):
                frames.append(frame_from_record(p, root))
            elif n == "still_layout.json":
                frames += frames_from_still_layout(p, root)
            elif n.endswith(".contrast.json") and not n.startswith("cap")                     and not p.with_name(n[: -len(".contrast.json")] + ".ass").is_file():   # a caption file's own record
                f = frame_from_contrast(p, root)
                if f is not None:
                    frames.append(f)
            elif n.endswith(".ass"):
                txt = p.read_text(encoding="utf-8", errors="replace")
                w, h = _play_res(txt)
                words = next((q for q in (p.with_name(p.stem + ".words.json"), p.parent / "words.json") if q.is_file()), None)
                frames.append(Frame(name=_rel(p, root), kind="caption", size=(w, h), deliverable="reel" if h > w else "wide",
                                    ass=p, words=words, until=_end_card_start(p.parent), source=_rel(p, root)))
                if n.endswith("_captions.ass"):
                    for m in sorted(p.parent.glob(n[: -len("_captions.ass")] + ".mp4")):
                        frames.append(Frame(name=_rel(m, root), kind="master", size=(w, h),
                                            deliverable="reel" if h > w else "wide", ass=p, video=m,
                                            until=_end_card_start(p.parent), source=_rel(p, root)))
        except (OSError, ValueError, KeyError, TypeError) as e:
            frames.append(Frame(name=_rel(p, root), kind="unreadable", size=(0, 0), deliverable="none",
                                source=f"{_rel(p, root)}: {type(e).__name__}"))
    out, seen = [], set()
    for f in frames:            # a harness record wins over a still_layout.json entry for the same picture
        key = ("frame" if f.kind in ("still", "card", "graphic") else f.kind, f.name)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return sorted(out, key=lambda f: (f.name, f.kind))


def _end_card_start(folder: Path) -> float | None:
    for comp in sorted(Path(folder).glob("*.composition.json")):
        try:
            v = (json.loads(comp.read_text(encoding="utf-8")).get("end_card") or {}).get("start_s")
            return float(v) if v else None
        except (OSError, ValueError, TypeError):
            return None
    return None


def platform_box(deliverable: str, size: tuple[int, int], kind: str) -> tuple[int, int, int, int] | None:
    """The box the repo's safe-zone table gives this deliverable at this size (None: no platform interface)."""
    w, h = size
    if deliverable == "reel" and h > w:
        return safe_zones.graphics_box(w, h) if kind in ("graphic", "card") else safe_zones.safe_box("reel", w, h)
    if deliverable == "story" and h > w:
        return safe_zones.safe_box("story", w, h)
    if deliverable == "cover" and h > w:
        return safe_zones.cover_box(w, h)
    return None


def covering_platforms(box, deliverable: str, size: tuple[int, int]) -> list[str]:
    """The platforms whose own interface (safe_zones.PLATFORM_UI) covers part of `box`."""
    return sorted(p for p in safe_zones.DELIVERABLES.get(deliverable, ())
                  if not _inside(box, safe_zones.PLATFORM_UI[p].box(*size)))


# ------------------------------------------------------------------------------------------------ independent text
def tokens(text: str) -> list[str]:
    """The words a field must show: every run of letters or digits, one character or more (QF-136 round 2: a one- or
    two-letter word, a single digit, is truth like any other; punctuation is drawn ink between words)."""
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text or ""))


_WEIGHTS = ("Regular", "Medium", "SemiBold", "Bold", "ExtraBold", "Black")


def _neighbour_weights(face: str, inst: str | None) -> tuple[str, ...]:
    if inst not in _WEIGHTS:
        return ()
    k = _WEIGHTS.index(inst)
    names = {n for f, n in bundled_faces() if f == face}
    return tuple(n for n in (_WEIGHTS[k - 1] if k else None, _WEIGHTS[k + 1] if k + 1 < len(_WEIGHTS) else None)
                 if n in names)


@lru_cache(maxsize=1)
def bundled_faces() -> tuple[tuple[str, str | None], ...]:
    """Every upright face of the bundled font files: (file name, named instance or None)."""
    out = []
    for p in sorted(Path(FONTS_DIR).glob("*.ttf")):
        f = ImageFont.truetype(str(p), 20, layout_engine=ImageFont.Layout.RAQM)
        try:
            names = [n.decode() if isinstance(n, bytes) else str(n) for n in f.get_variation_names()]
        except (OSError, AttributeError):
            names = []
        keep = [n for n in names if n in ("Regular", "Medium", "SemiBold", "Bold", "ExtraBold", "Black")]
        out += [(p.name, n) for n in keep] if keep else [(p.name, None)]
    return tuple(out)


@lru_cache(maxsize=256)
def _font(face: str, inst: str | None, px: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(Path(FONTS_DIR) / face), px, layout_engine=ImageFont.Layout.RAQM)
    if inst:
        f.set_variation_by_name(inst)
    return f


def shaped(word: str, face: str, inst: str | None, px: int, track: float, variant: str) -> np.ndarray | None:
    """The word shaped (raqm/HarfBuzz, kerning kept) and rasterised: a float32 ink mask cropped to its ink, `track`
    em added between letters; variant "upper" sets it in capitals, "smcp" asks for small capitals."""
    r = _shape(word, face, inst, px, track, variant)
    return None if r is None else r[0]


@lru_cache(maxsize=2048)
def _shape(word: str, face: str, inst: str | None, px: int, track: float, variant: str):
    """(ink mask, baseline: px from the mask's top row down to the line's baseline)."""
    if px < 6 or px > 600:
        return None
    font = _font(face, inst, px)
    text = word.upper() if variant == "upper" else word
    feats = ["smcp"] if variant == "smcp" else None
    try:
        adv = [font.getlength(text[:i], features=feats) + i * track * px for i in range(len(text) + 1)]
    except (OSError, ValueError):
        return None
    w, h = int(adv[-1] + px * 2), int(px * 2.2)
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    if track == 0:
        d.text((px // 2, int(px * 1.6)), text, font=font, fill=255, anchor="ls", features=feats)
    else:
        for i, ch in enumerate(text):
            d.text((px // 2 + adv[i], int(px * 1.6)), ch, font=font, fill=255, anchor="ls", features=feats)
    bb = im.getbbox()
    if bb is None or bb[2] - bb[0] < 1 or bb[3] - bb[1] < 3:
        return None
    return np.asarray(im.crop(bb), np.float32) / 255.0, int(px * 1.6) - bb[1]


def _gray(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722).astype(np.float32)


def _match(gray: np.ndarray, tmpl: np.ndarray, win: tuple[int, int, int, int],
           exclude: Iterable[tuple[int, int, int, int]] = ()) -> tuple[float, tuple[int, int]]:
    """Best |NCC| of `tmpl` inside `win` of `gray` (light or dark ink alike) and its top-left in `gray`. Places whose
    centre falls in an `exclude` box (ink another field already holds) are not taken."""
    import cv2
    x0, y0, x1, y1 = win
    reg = gray[max(0, y0):y1, max(0, x0):x1]
    th, tw = tmpl.shape
    if reg.shape[0] < th or reg.shape[1] < tw or float(tmpl.std()) < 1e-3:
        return -1.0, (0, 0)
    r = cv2.matchTemplate(reg, tmpl, cv2.TM_CCOEFF_NORMED)
    r = np.nan_to_num(r, nan=0.0)
    ox, oy = max(0, x0), max(0, y0)
    for b in exclude:            # top-left positions whose template centre lies in b
        ax0, ay0 = max(0, b[0] - ox - tw // 2), max(0, b[1] - oy - th // 2)
        ax1, ay1 = min(r.shape[1], b[2] - ox - tw // 2), min(r.shape[0], b[3] - oy - th // 2)
        if ax1 > ax0 and ay1 > ay0:
            r[ay0:ay1, ax0:ax1] = 0.0
    lo, hi, lo_at, hi_at = cv2.minMaxLoc(r)
    v, at = (hi, hi_at) if hi >= -lo else (-lo, lo_at)
    return float(v), (at[0] + max(0, x0), at[1] + max(0, y0))


def _match_all(gray: np.ndarray, tmpl: np.ndarray, win: tuple[int, int, int, int], exclude=(),
               floor: float = 0.6) -> list[tuple[float, tuple[int, int]]]:
    """Every place in `win` where `tmpl` matches at `floor` or better (local maxima, one per template-sized
    neighbourhood), best first: a word a field repeats elsewhere (a second card's ROOM) is told apart by where it is."""
    import cv2
    x0, y0, x1, y1 = win
    reg = gray[max(0, y0):y1, max(0, x0):x1]
    th, tw = tmpl.shape
    if reg.shape[0] < th or reg.shape[1] < tw or float(tmpl.std()) < 1e-3:
        return []
    r = np.abs(np.nan_to_num(cv2.matchTemplate(reg, tmpl, cv2.TM_CCOEFF_NORMED), nan=0.0))
    ox, oy = max(0, x0), max(0, y0)
    for b in exclude:
        ax0, ay0 = max(0, b[0] - ox - tw // 2), max(0, b[1] - oy - th // 2)
        ax1, ay1 = min(r.shape[1], b[2] - ox - tw // 2), min(r.shape[0], b[3] - oy - th // 2)
        if ax1 > ax0 and ay1 > ay0:
            r[ay0:ay1, ax0:ax1] = 0.0
    peaks = (r >= floor) & (r == cv2.dilate(r, np.ones((max(3, th) | 1, max(3, tw) | 1), np.uint8)))
    ys, xs = np.nonzero(peaks)
    out = sorted(((float(r[yy, xx]), (int(xx) + ox, int(yy) + oy)) for yy, xx in zip(ys, xs)), key=lambda c: (-c[0], c[1]))
    return out[:12]


@dataclass
class Style:
    face: str
    inst: str | None
    px: int
    track: float
    variant: str

    def raster(self, word: str) -> np.ndarray | None:
        return shaped(word, self.face, self.inst, self.px, self.track, self.variant)

    def baseline(self, word: str) -> int:
        r = _shape(word, self.face, self.inst, self.px, self.track, self.variant)
        return 0 if r is None else r[1]

    def family(self) -> str:
        return _family(self.face)

    def label(self) -> str:
        return f"{Path(self.face).stem}{'-' + self.inst if self.inst else ''} {self.px}px" + \
            (f" track {self.track:g}em" if self.track else "") + (f" {self.variant}" if self.variant != "plain" else "")


def _ink_height(word: str, face: str, inst: str | None, variant: str, px: int = 100) -> float:
    m = shaped(word, face, inst, px, 0.0, variant)
    return 0.0 if m is None else m.shape[0] / px


def _families_of(faces) -> dict[str, list[str | None]]:
    out: dict[str, list[str | None]] = {}
    for face, inst in faces:
        out.setdefault(face, []).append(inst)
    return out


def _representative(insts: list[str | None]) -> str | None:
    for want in ("SemiBold", "Bold", "Regular"):
        if want in insts:
            return want
    return insts[0]


def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    import cv2
    return cv2.GaussianBlur(a, (0, 0), sigma) if sigma > 0.3 else a


MIN_TEMPLATE_PX = 14       # a shaped word shorter than this (ink height, px) is too small a template to trust


def calibrate(gray: np.ndarray, word: str, win: tuple[int, int, int, int], height_hint: float,
              faces: Iterable[tuple[str, str | None]] | None = None, hint_face: str = "",
              hint_px: float = 0.0, exclude: tuple = (), fast_only: bool = False) -> tuple[float, Style | None, tuple[int, int]]:
    """The face, size, case and tracking at which `word` is best found inside `win`, coarse to fine. A record's face
    and size, when it gives them, are tried first (a hint: only the match decides). Otherwise every font file at sizes
    8% apart around `height_hint` (how tall a line may be there), plain and in capitals, capitals also tracked,
    matched on blurred pixels so that a near size still finds the word; then the best files' other weights; fine:
    size in 1.5% steps, tracking and small capitals on the sharp pixels."""
    fams = _families_of(tuple(faces or bundled_faces()))
    x0, y0, x1, y1 = win
    reg_cache: dict[float, np.ndarray] = {}
    caps = word.upper() == word

    def blurred(st: Style) -> float:
        t = st.raster(word)
        if t is None or t.shape[0] < MIN_TEMPLATE_PX:
            return -1.0
        import cv2
        sigma = round(max(1.0, st.px / 14.0) * 2) / 2
        f = 0.5 if st.px >= 28 else 1.0                # blurred, half the pixels lose nothing the blur kept
        if sigma not in reg_cache:
            g = _blur(np.ascontiguousarray(gray[max(0, y0):y1, max(0, x0):x1]), sigma)
            reg_cache[sigma] = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else g
        reg = reg_cache[sigma]
        shifted = [tuple(int((v - o) * f) for v, o in zip(b, (max(0, x0), max(0, y0), max(0, x0), max(0, y0))))
                   for b in exclude]
        tp = _blur(np.pad(t, int(2 * sigma)), sigma)   # room for the blur's spread, as the frame has around its ink
        if f < 1:
            tp = cv2.resize(tp, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        if tp.shape[0] > reg.shape[0] or tp.shape[1] > reg.shape[1]:
            return -1.0
        return _match(reg, tp, (0, 0, reg.shape[1], reg.shape[0]), shifted)[0]

    def sharp(st: Style) -> tuple[float, tuple[int, int]]:
        t = st.raster(word)
        return (-1.0, (0, 0)) if t is None or t.shape[0] < MIN_TEMPLATE_PX else _match(gray, t, win, exclude)

    def refine(pool: list[tuple[float, Style]]) -> tuple[float, Style | None, tuple[int, int]]:
        pool.sort(key=lambda c: (-c[0], c[1].face, str(c[1].inst), c[1].px, c[1].track))
        best: tuple[float, Style | None, tuple[int, int]] = (-1.0, None, (0, 0))
        heads: list[Style] = []
        for _v, st in pool:      # the best files and cases, each at up to two sizes apart (a small match in texture
            same = [x for x in heads if (x.face, x.variant) == (st.face, st.variant)]   # must not hide the true size)
            if len(heads) < 6 and len(same) < 2 and all(abs(x.px - st.px) > 0.15 * st.px for x in same):
                heads.append(st)
        for st in heads:                               # each candidate: its size, then its tracking at that size
            local: tuple[float, Style | None, tuple[int, int]] = (-1.0, None, (0, 0))
            for k in range(-10, 11):                   # size in 1.5% steps
                s2 = Style(st.face, st.inst, max(6, round(st.px * (1 + 0.015 * k))), st.track, st.variant)
                v, at = sharp(s2)
                if v > local[0]:
                    local = (v, s2, at)
            if local[1] is None:
                continue
            s1 = local[1]
            for track in (0.0, 0.02, 0.04, 0.06, 0.08, 0.1, 0.12, 0.15, 0.18, 0.22):
                for d in (-2, 0, 2):
                    s2 = Style(s1.face, s1.inst, max(6, s1.px + d), track, s1.variant)
                    v, at = sharp(s2)
                    if v > local[0]:
                        local = (v, s2, at)
            if local[0] > best[0]:
                best = local
        st = best[1]
        if st is None:
            return best
        for inst in fams.get(st.face, [st.inst]):      # the winner's other weights, around its size and tracking
            for d in (-1, 0, 1):
                for dt in (-0.02, 0.0, 0.02):
                    s2 = Style(st.face, inst, max(6, st.px + d), max(0.0, round(st.track + dt, 3)), st.variant)
                    v, at = sharp(s2)
                    if v > best[0]:
                        best = (v, s2, at)
        st = best[1]
        if st is not None and st.variant == "plain" and not caps:
            s2 = Style(st.face, st.inst, st.px, st.track, "smcp")
            v, at = sharp(s2)
            if v > best[0] + 0.01:
                best = (v, s2, at)
        return best

    variants = ("plain",) if caps else ("plain", "upper")
    if hint_face in fams and hint_px > 0:              # the record's face and size first
        pool = [(blurred(Style(hint_face, inst, round(hint_px), track, variant)),
                 Style(hint_face, inst, round(hint_px), track, variant))
                for inst in fams[hint_face] for variant in variants for track in (0.0, 0.05, 0.1, 0.15, 0.2)]
        best = refine(pool)
        if best[0] >= 0.75 or fast_only:
            return best
    if fast_only:
        return -1.0, None, (0, 0)
    cands: list[tuple[float, Style]] = []
    for face, insts in fams.items():
        inst = _representative(insts)
        for variant in variants:
            ink = _ink_height(word, face, inst, variant)
            if ink <= 0:
                continue
            base = height_hint / ink
            sizes = {round(base * 1.08 ** k) for k in range(-14, 2)}
            for lines in (2, 3, 4, 5):                 # a box may hold several lines (a body)
                sizes |= {round(base / (lines * 1.2) * 1.08 ** k) for k in range(-3, 4)}
            if hint_px > 0:                            # a record's size (without its face): sizes around it too
                sizes |= {round(hint_px * f) for f in (0.94, 0.97, 1.0, 1.03, 1.06)}
            for px in sorted(sizes):
                for track in ((0.0, 0.08, 0.16) if (caps or variant == "upper") else (0.0,)):
                    st = Style(face, inst, px, track, variant)
                    cands.append((blurred(st), st))
    if not cands:
        return -1.0, None, (0, 0)
    cands.sort(key=lambda c: (-c[0], c[1].face, c[1].px, c[1].track))
    per_file: dict[tuple[str, str], tuple[float, Style]] = {}
    for c in cands:                                    # each font file's best size and tracking, in each case
        per_file.setdefault((c[1].face, c[1].variant), c)
    files = sorted(per_file.values(), key=lambda c: (-c[0], c[1].face, c[1].variant))[:4]
    pool = list(cands[:8]) + [c for c in files if c not in cands[:8]]
    for _v, st in files:                               # the best files' other weights
        for inst in fams[st.face]:
            if inst != st.inst:
                s2 = Style(st.face, inst, st.px, st.track, st.variant)
                pool.append((blurred(s2), s2))
    return refine(pool)


def _pad(rgb: np.ndarray, pad: int) -> np.ndarray:
    return np.pad(rgb, ((pad, pad), (pad, pad), (0, 0)), mode="edge") if pad else rgb


def _glyph_contrast(rgb: np.ndarray, mask: np.ndarray, at: tuple[int, int], ink: np.ndarray | None = None) -> float | None:
    """typography.ring_contrast's measure (the glyphs' eroded median against the 90th, or for dark ink the 10th,
    percentile of the ring 2-10 px outside them) with the shaped word's own mask at the place it was found; the ink is
    light or dark as the pixels say. A rule struck through the word (a myth card's strike) is a mark of the design,
    not the ground: rows where it runs through the word's own box are left out of the ring."""
    import cv2

    from .typography import _RING_INNER, _RING_OUTER, _lin
    h, w = mask.shape
    x, y = at
    m = 12
    H, W = rgb.shape[:2]
    X0, Y0, X1, Y1 = max(0, x - m), max(0, y - m), min(W, x + w + m), min(H, y + h + m)
    if X1 - X0 <= 2 * m or Y1 - Y0 <= 2 * m:
        return None
    g = np.zeros((Y1 - Y0, X1 - X0), np.uint8)
    sx0, sy0 = x - X0, y - Y0
    tx0, ty0 = max(0, -sx0), max(0, -sy0)
    tx1, ty1 = min(w, g.shape[1] - sx0), min(h, g.shape[0] - sy0)
    if tx1 <= tx0 or ty1 <= ty0:
        return None
    g[sy0 + ty0:sy0 + ty1, sx0 + tx0:sx0 + tx1] = (mask[ty0:ty1, tx0:tx1] > 0.5)
    if g.sum() < 20:
        return None
    lum = _lin(rgb[Y0:Y1, X0:X1])
    ring = cv2.dilate(g, np.ones((_RING_OUTER, _RING_OUTER), np.uint8)) - cv2.dilate(g, np.ones((_RING_INNER, _RING_INNER), np.uint8))
    inner = cv2.erode(g, np.ones((3, 3), np.uint8))
    lt = float(np.median(lum[(inner if inner.sum() > 30 else g) > 0]))
    rest = float(np.median(lum[(g == 0) & (ring > 0)])) if ((g == 0) & (ring > 0)).any() else 0.0
    light = lt >= rest
    # a strike: rows inside the word's box where most pixels between the glyphs are ink-like (a rule across them)
    mid = (lt + rest) / 2
    gap = (g == 0)
    xs = slice(max(0, sx0), min(g.shape[1], sx0 + w))
    strike = []
    for r in range(max(0, sy0), min(g.shape[0], sy0 + h)):
        row_gap = gap[r, xs]
        if row_gap.sum() < 8:
            continue
        inky = (lum[r, xs] > mid) if light else (lum[r, xs] < mid)
        if (inky & row_gap).sum() >= 0.7 * row_gap.sum():
            strike.append(r)
    if strike and len(strike) <= max(4, h // 4):     # a thin rule, not a ground as light as the ink
        for r in strike:
            ring[max(0, r - 2):r + 3, :] = 0
    if ink is not None:          # other words' glyphs (dilated by the anti-aliasing) are not this word's ground
        other = cv2.dilate(ink[Y0:Y1, X0:X1], np.ones((5, 5), np.uint8))
        ring[(other > 0) & (g == 0)] = 0
    bg = lum[ring > 0]
    if len(bg) < 50:
        return None
    lb = float(np.percentile(bg, 90 if light else 10))
    return round((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05), 2)


def verify_fields(rgb: np.ndarray, f: Frame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Each truth field found word by word in the pixels: (findings, rows). Rows carry the style found, every word's
    normalised cross-correlation (ncc) and box, the field's box in the pixels and its measured ratio."""
    h, w = rgb.shape[:2]
    pad = max(40, h // 12)
    gray = _gray(_pad(rgb, pad))
    full = (0, 0, gray.shape[1], gray.shape[0])
    hints = [e for e in f.elements if e.get("box")]
    # the edges a word may be cut at (padded x): the frame's and its zone's (recorded, else the platform box)
    zone = f.zone or platform_box(f.deliverable, (w, h), f.kind)
    _EDGES[:] = [pad, pad + w] + ([pad + zone[0], pad + zone[2]] if zone else [])
    _EDGES_Y[:] = [pad, pad + h] + ([pad + zone[1], pad + zone[3]] if zone else [])
    out: list[dict[str, Any]] = []
    rows_at: dict[int, dict[str, Any]] = {}
    claimed: list[tuple[int, int, int, int]] = []     # ink a field already holds (padded coordinates)
    expect = list(f.expect or [])
    # fields with the longest words first: a long word is the least ambiguous, and ink a field holds is not found
    # again for another (a body must not pass on a title's capitals)
    order_fields = sorted(range(len(expect)), key=lambda i: (-max((len(x) for x in tokens(str(expect[i].get("text") or ""))),
                                                                default=0), i))
    for idx in order_fields:
        fd = expect[idx]
        name, text = str(fd.get("field") or ""), str(fd.get("text") or "")
        _ALLOW_STRIKE[0] = f.kind == "card" and "myth" in name     # only the myth card strikes its words
        if not truth_words(text):
            out.append(finding("coverage", name, "the truth gives this field no words to check", basis="truth"))
        words = truth_words(text)
        row: dict[str, Any] = {"field": name, "truth": fd.get("truth", ""), "text": text, "words": []}
        rows_at[idx] = row
        if not words:
            continue
        # the field's longest words in turn (a dropped glyph in one must not hide the rest), first inside the record
        # boxes (tight), then around them: the first word found sets the face, size, case and tracking of the field
        # letters before figures (a numeral may be drawn digit by digit, and 0 and O are alike in many faces)
        anchors = sorted(dict.fromkeys(words), key=lambda x: (any(c.isdigit() for c in x), -len(x), x))[:3]
        order = sorted(hints, key=lambda e: _pair_rank(name, str(e.get("name") or "")))
        best: tuple[float, Style | None, tuple[int, int], dict | None, tuple] = (-1.0, None, (0, 0), None, full)
        # first, cheaply, each anchor in the face and size its own record boxes give (a hint: the match decides)
        for grow in (0.35, 2.5):     # around the box, then wider (a record box may stand away from its text)
            for anchor in anchors:
                for e in [e for e in order if _pair_rank(name, str(e.get("name") or "")) <= 1 and _face_of(e)[0]
                          and e.get("px")][:3]:
                    b = _box(e["box"])
                    m = max(16, round(max(8, b[3] - b[1]) * grow))
                    win = (b[0] - m + pad, b[1] - m + pad, b[2] + m + pad, b[3] + m + pad)
                    v, st, at = calibrate(gray, anchor, win, float(b[3] - b[1]), hint_face=_face_of(e)[0],
                                          hint_px=float(e["px"]), exclude=tuple(claimed), fast_only=True)
                    if v > best[0]:
                        best = (v, st, at, e, win)
                if best[0] >= 0.75:
                    break
            if best[0] >= 0.75:
                break
        for grow in ((0.35, 1.0) if best[0] < 0.75 else ()):
            for ai, anchor in enumerate(anchors):
                near_boxes = [e for e in order if _pair_rank(name, str(e.get("name") or "")) <= 1][:3]
                if best[3] is not None and best[3] not in near_boxes:
                    near_boxes.append(best[3])
                for e in (order if (ai == 0 and grow < 1) else (near_boxes or order[:2])):
                    b = _box(e["box"])
                    bh = max(8, b[3] - b[1])
                    m = max(16, round(bh * grow))
                    win = (b[0] - m + pad, b[1] - m + pad, b[2] + m + pad, b[3] + m + pad)
                    face = _face_of(e)[0]
                    v, st, at = calibrate(gray, anchor, win, float(bh), hint_face=face, hint_px=float(e.get("px") or 0),
                                          exclude=tuple(claimed))
                    if v > best[0]:
                        best = (v, st, at, e, win)
                    if v >= 0.75:
                        break
                if best[0] >= STYLE_MIN:
                    break
            if best[0] >= STYLE_MIN:
                break
        if best[0] < STYLE_MIN:      # not near any recorded box: the whole frame, sized from the field's own hint
            e0 = order[0] if order else None
            hint = float(max(8, _box(e0["box"])[3] - _box(e0["box"])[1])) if e0 else float(h // 20)
            v, st, at = calibrate(gray, anchors[0], full, hint, hint_face=_face_of(e0)[0] if e0 else "",
                                  hint_px=float((e0 or {}).get("px") or 0), exclude=tuple(claimed))
            if v > best[0]:
                best = (v, st, at, None, full)
        score, style, anchor_at, elem, win = best
        figure_words = {x for k, x in enumerate(words)    # a figure and the words next to it (its unit, "est.")
                        if any(ch.isdigit() for y in words[max(0, k - 1):k + 2] for ch in y)}

        def match_words(style, score, anchor_at, win, taken, words=words, figure_words=figure_words, name=name,
                        text=text):
            """Every word of the field in `style`, in the truth's reading order and once each: (word rows, found, the
            ink they take). The first word is tried at up to three places; the reading that places most words wins."""
            if style is None or score < STYLE_MIN:
                return [{"word": wd, "ncc": None if style is None else round(score, 3)} for wd in words], [], list(taken)
            r0 = style.raster(max(words, key=len))
            lh = r0.shape[0] if r0 is not None else 20
            # the band the field may occupy: around the anchor and every record box named for this field (its lines)
            own = [_box(e["box"]) for e in hints if _pair_rank(name, str(e.get("name") or "")) <= 1]
            y0 = min([win[1]] + [b[1] + pad for b in own])
            y1 = max([win[3]] + [b[3] + pad for b in own])
            near = (0, max(0, y0 - 2 * lh), gray.shape[1], min(gray.shape[0], y1 + 2 * lh))
            def breaks(read):            # the lines a reading takes (fewer is the field's own set of lines)
                bs = [b for _w, b, _t, _a in read[1]]
                return sum(1 for p, q in itertools.pairwise(bs) if q[1] > p[3] - 0.3 * (p[3] - p[1]))

            best_read = None
            for first in range(3):
                read = _read_field(gray, style, words, text, near, full, taken, anchor_at, first, figure_words, hints,
                                   pad, lh)
                if best_read is None or (len(read[1]), -breaks(read)) > (len(best_read[1]), -breaks(best_read)):
                    best_read = read
                if read[3] or (len(read[1]) == len(words) and breaks(read) == 0):
                    break
            return best_read[0], best_read[1], best_read[2]

        words_rows, found, ink_after = match_words(style, score, anchor_at, win, claimed)
        # a word the field's style does not find may itself be the better anchor (the first one found in a box near
        # a look-alike face): the field is matched again in the style that word fixes, and the reading that finds
        # more words is kept. A letter dropped from a word leaves that word missing in both readings.
        for x in [x for x in words_rows if not (x.get("box") and (x["ncc"] or 0) >= WORD_MIN)][:2]:
            if style is None or len(found) == len(words) or 2 * len(found) < len(words) or x["word"] in figure_words:
                break
            r0 = style.raster(x["word"])
            lh0 = r0.shape[0] if r0 is not None else max(8, (win[3] - win[1]) // 2)
            ww = win[2] - win[0]
            band = (max(0, win[0] - ww), max(0, win[1] - 2 * lh0), min(gray.shape[1], win[2] + ww),
                    min(gray.shape[0], win[3] + 2 * lh0))
            v4, st4, at4 = calibrate(gray, x["word"], band, float(lh0), exclude=tuple(claimed))
            if st4 is None or v4 < STYLE_MIN or st4 == style:
                continue
            r2, f2, i2 = match_words(st4, v4, at4, win, claimed)
            if len(f2) > len(found):
                words_rows, found, ink_after, style, score = r2, f2, i2, st4, v4
        claimed[:] = ink_after
        row["style"] = style.label() if style else None
        row["style_ncc"] = round(score, 3)
        row["element"] = elem.get("name") if elem else None
        row["words"] = words_rows
        row["_style"] = style if score >= STYLE_MIN else None
        missing = [x for x in row["words"] if not x.get("placed")]      # a field whose style was never found: all
        fits = sorted(x["ncc"] for x in row["words"] if x.get("placed") and x.get("ncc") is not None)
        unsure = bool(missing) and bool(fits) and fits[len(fits) // 2] < STYLE_CERTIFY
        row["certified"] = not unsure
        if not missing and fits and fits[len(fits) // 2] < STYLE_UNCERTAIN:     # every word placed, but loosely
            row["certified"] = False
            out.append(finding("coverage", name, f"its words are placed at a fit of {fits[len(fits) // 2]:.2f} (needs "
                               f"{STYLE_UNCERTAIN}): the face is not reproduced closely enough to certify them",
                               row.get("box"), fits[len(fits) // 2], STYLE_UNCERTAIN))
        for x in missing:
            if unsure:                   # the field's face is not reproduced closely enough to tell its letters
                out.append(finding("coverage", name, f"{x['word']!r} of the {fd.get('truth') or 'expected'} text cannot be "
                                   f"certified: the field's face is not reproduced here (its words fit "
                                   f"{fits[len(fits) // 2]:.2f}, needs {STYLE_CERTIFY}; "
                                   f"{x.get('why') or 'best match ' + str(x['ncc'])})", x.get("box"), x["ncc"],
                                   STYLE_CERTIFY, word=x["word"], _unsure=True))
                continue
            out.append(finding("text_identity", name, f"{x['word']!r} of the {fd.get('truth') or 'expected'} text is not in "
                               f"the pixels ({x.get('why') or 'best match ' + str(x['ncc'])})", x.get("box"), x["ncc"],
                               WORD_MIN, word=x["word"]))
        for x in row["words"]:
            if x.get("placed") and x.get("faint"):
                out.append(finding("text_identity", name, f"{x['word']!r} is too faint against its ground to tell its "
                                   "letters", x.get("box"), word=x["word"]))
        if elem is not None and _face_of(elem)[0] and style is not None and score >= STYLE_MIN \
                and style.family() != _family(_face_of(elem)[0]):
            out.append(finding("record_mismatch", name, f"the pixels are set in {style.label()}; the record says "
                               f"{_face_of(elem)[0]}", None, basis="pixels+record"))
        if fd.get("conflict"):
            out.append(finding("coverage", name, fd["conflict"], basis="record"))
        if style is not None and style.variant == "upper" and any(c.islower() for c in text) and not (
                fd.get("caps") or name in CAPS_BY_DESIGN):
            out.append(finding("text_identity", name, f"set in capitals; the truth {text[:40]!r} is not, and the design "
                               "does not set this field in capitals", None, basis="pixels+truth"))
        if fd.get("font") and style is not None and score >= STYLE_MIN and not _same_face(style, str(fd["font"]),
                                                                                         fd.get("weight")):
            out.append(finding("text_identity", name, f"the pixels are set in {style.label()}; the truth's face is "
                               f"{fd['font']}" + (f" {fd['weight']}" if fd.get("weight") else ""), None,
                               basis="pixels+truth"))
        intended = fd.get("font") or intended_family(name)
        if intended is None:
            out.append(finding("coverage", name, "no intended face is known for this field (the brand's font roles "
                               "name none): its face cannot be judged", basis="truth"))
        elif style is not None:          # every word of the field in the intended family (a numeral set apart too)
            for x in row["words"]:
                fs = x.get("style")
                if x.get("placed") and fs and _family(fs.split()[0]) != _family(str(intended)):
                    out.append(finding("text_identity", name, f"{x['word']!r} is set in {fs}; the field's face is "
                                       f"{intended}", x.get("box"), basis="pixels+truth", word=x["word"]))
        if found:
            fb = _union([b for _w, b, _t, _a in found])
            row["box"] = list(fb)
            row["_ink"] = [(t, a) for _w, _b, t, a in found]
            inks, grounds = [], []
            for _w, _b, t, a in found:               # the field's own tones: its glyphs and the ground about them
                reg = gray[a[1] + pad:a[1] + pad + t.shape[0], a[0] + pad:a[0] + pad + t.shape[1]]
                if reg.shape == t.shape:
                    inks.append(reg[t > 0.7])
                    grounds.append(reg[t < 0.05])
            if inks and grounds:
                row["_tone"] = (float(np.median(np.concatenate(inks))), float(np.median(np.concatenate(grounds))))
            row["_sure"] = [(t, a) for _w, _b, t, a in found]          # every accepted word's contrast is measured
            for wd, bx, _t, _a in found:
                if not _inside(bx, _grow((0, 0, w, h), PIXEL_TOL)):
                    out.append(finding("text_cut", name, f"{wd!r} {list(bx)} crosses the {w}x{h} frame edge (the edge is "
                                       "inside the word)", bx, mid_word=True))
            if hints:                    # every word inside a box the record gives (a box may hold its plate too)
                grown = [tuple(v + d for v, d in zip(_box(e["box"]), (-RECORD_TOL, -RECORD_TOL, RECORD_TOL, RECORD_TOL)))
                         for e in hints]
                for wd, bx, _t, _a in found:
                    if not any(_inside(bx, g) for g in grown):
                        nb = min(hints, key=lambda e: (-_inter(_box(e["box"]), bx), _dist(_box(e["box"]), bx)))
                        out.append(finding("record_mismatch", name, f"{wd!r} is at {list(bx)} in the pixels; no record box "
                                           f"holds it (nearest: {nb.get('name')} {list(_box(nb['box']))})", bx))
            truth_cf = [x.casefold() for x in words]
            for e in hints:              # record text where this field's words are: a run of the truth's words
                if not e.get("text") or not any(_inside(bx, _grow(_box(e["box"]), RECORD_TOL)) for _w, bx, _t, _a in found):
                    continue
                said = [x.casefold() for x in truth_words(e["text"])]
                if said and not _contiguous(said, truth_cf):
                    out.append(finding("record_mismatch", name, f"the record says {e['text']!r} for {e.get('name')}; the "
                                       f"truth is {text!r}", _box(e["box"]), basis="record"))
    # contrast, once every field's ink is known: a word's ring leaves out the ink of the other words found (a
    # neighbouring line set tight is text, not ground)
    ink = np.zeros((h, w), np.uint8)
    for r in rows_at.values():
        for t, a in r.pop("_ink", []):            # every found word's glyphs (sure or not) are text, not ground
            _stamp(ink, t, a)
    for r in rows_at.values():
        ratios = [v for v in (_glyph_contrast(rgb, t, a, ink) for t, a in r.pop("_sure", [])) if v is not None]
        if ratios:
            r["contrast"] = min(ratios)
    # a field is uncertified only when the frame's faces are not reproduced (its words' median fit across fields):
    # where the other fields certify, an unplaced word is a text_identity finding
    for d in out:                    # round 3 (sweep real runs 5): an uncertified field stays uncertified (coverage)
        d.pop("_unsure", None)
    # the truth's own recorded boxes: the record elements that hold its words. Extra words are looked for there
    # only: ink elsewhere belongs to elements the truth does not cover (a kicker, a share line, the masthead)
    placed = [x["box"] for r in rows_at.values() for x in r.get("words", []) if x.get("placed")]
    regions = [_box(e["box"]) for e in f.elements if e.get("box")
               and any(_inside(b, _grow(_box(e["box"]), RECORD_TOL)) for b in placed)]
    out += _extra_copies(gray, pad, rows_at, expect, claimed, (w, h), regions, anywhere=f.kind == "card")
    out += _stray_ink(gray, pad, rows_at, regions)   # every frame with truth (round 3: graphics too)
    # the fields in the design's order, top to bottom (a heading under its body is not the design)
    tops = [(VISUAL_ORDER.index(r["field"]), r["field"], r["box"][1]) for r in rows_at.values()
            if r.get("box") and r["field"] in VISUAL_ORDER]
    for (_ia, fa_, ya), (_ib, fb_, yb) in itertools.pairwise(sorted(tops)):
        if yb < ya - 4:
            out.append(finding("text_identity", fb_, f"{fb_} stands above {fa_}: the design sets {fa_} first",
                               basis="pixels+truth"))
    _ALLOW_STRIKE[0] = False
    for r in rows_at.values():
        r.pop("_style", None)
        r.pop("_tone", None)
    return out, [rows_at[i] for i in range(len(expect))]


def _stamp(ink: np.ndarray, t: np.ndarray, at: tuple[int, int]) -> None:
    x, y = at
    h, w = t.shape
    x0, y0, x1, y1 = max(0, x), max(0, y), min(ink.shape[1], x + w), min(ink.shape[0], y + h)
    if x1 > x0 and y1 > y0:
        ink[y0:y1, x0:x1] |= (t[y0 - y:y1 - y, x0 - x:x1 - x] > 0.5).astype(np.uint8)


def _digits(gray: np.ndarray, number: str, win: tuple[int, int, int, int], hint: float,
            exclude: tuple) -> tuple[float, tuple[int, int], np.ndarray] | None:
    """A numeral whose digits stand in slots of their own (a count-up's tabular digits): its first digit's style
    found in `win`, then each next digit in that style just right of the one before. (the lowest digit ncc, the
    numeral's top-left, its ink mask) when every digit is found, else None."""
    v0, st, at0 = calibrate(gray, number[0], win, hint, exclude=exclude)
    t0 = st.raster(number[0]) if st is not None else None
    if t0 is None or v0 < STYLE_MIN:
        return None
    boxes, scores, masks = [(at0[0], at0[1], at0[0] + t0.shape[1], at0[1] + t0.shape[0])], [v0], [t0]
    for ch in number[1:]:
        t = st.raster(ch)
        if t is None:
            return None
        p = boxes[-1]
        h = p[3] - p[1]
        v, at = _match(gray, t, (p[2] - h // 3, p[1] - h // 3, p[2] + 2 * h, p[3] + h // 3), exclude)
        if v < WORD_MIN:
            return None
        boxes.append((at[0], at[1], at[0] + t.shape[1], at[1] + t.shape[0]))
        scores.append(v)
        masks.append(t)
    for ch, b, t in zip(number, boxes, masks):        # each slot holds its own digit, not another (321 is not 327)
        k = max(4, (b[3] - b[1]) // 4)
        reg = gray[max(0, b[1] - k):b[3] + k, max(0, b[0] - k):b[2] + k]
        mine = _ncc_best(reg, t)
        for d in _DIGIT:
            alt = st.raster(d) if d != ch else None
            if alt is None or alt.shape[0] > reg.shape[0] or alt.shape[1] > reg.shape[1]:
                continue
            if _ncc_best(reg, alt) > mine - GLYPH_MARGIN:
                return None
    u = _union(boxes)
    m = np.zeros((u[3] - u[1], u[2] - u[0]), np.float32)
    for b, t in zip(boxes, masks):
        m[b[1] - u[1]:b[3] - u[1], b[0] - u[0]:b[2] - u[0]] = np.maximum(m[b[1] - u[1]:b[3] - u[1], b[0] - u[0]:b[2] - u[0]], t)
    return min(scores), (u[0], u[1]), m, st.label()


def _free_sides(text: str, word: str) -> tuple[bool, bool]:
    """Whether the truth sets `word` apart on its left and right (a space or the field's edge each time it occurs):
    a period, comma, hyphen or bracket next to it is drawn ink, not another word's letter."""
    left = right = True
    for m in re.finditer(re.escape(word), unicodedata.normalize("NFC", text or "")):
        a, b = m.start(), m.end()
        left = left and (a == 0 or text[a - 1].isspace())
        right = right and (b == len(text) or text[b].isspace())
    return left, right


def _standalone(gray: np.ndarray, t: np.ndarray, at: tuple[int, int], sides: tuple[bool, bool] = (True, True)) -> bool:
    """A matched word, not part of a longer one: walking out from each side the truth leaves free (a space or the
    field's edge), along the word's middle rows, no ink-toned column comes within a sixth of its height that is then
    followed by its ground again (a letter has an end: a stem, a bowl). Ink-toned columns that run on to the end of
    the walk are a change of ground (a plate's edge onto a dark ground), not a letter."""
    h, w = t.shape
    x, y = at
    word = gray[y:y + h, x:x + w]
    if word.shape != t.shape:
        return True
    m = t > 0.5
    if m.all() or not m.any():
        return True
    ink, ground = float(np.median(word[m])), float(np.median(word[~m]))
    if abs(ink - ground) < 8:
        return True
    rows = slice(y + h // 5, y + h - h // 5)
    span, near, off = max(6, int(0.6 * h)), max(2, h // 6), 1 + round(0.01 * w)
    for check, cols in zip(sides, (range(x - off, x - off - span, -1), range(x + w + off, x + w + off + span))):
        if not check:
            continue
        cols = [c for c in cols if 0 <= c < gray.shape[1]]
        if len(cols) < near:
            continue
        prof = gray[rows, cols].mean(axis=0)
        inkish = np.abs(prof - ink) < np.abs(prof - ground)
        if inkish[:near].any():
            first = int(np.argmax(inkish))
            if (~inkish[first:]).any():
                return False
    return True


def _grow(b, d: int) -> tuple[int, int, int, int]:
    return (b[0] - d, b[1] - d, b[2] + d, b[3] + d)


def _contiguous(part: list[str], whole: list[str]) -> bool:
    n = len(part)
    return any(whole[i:i + n] == part for i in range(len(whole) - n + 1))


def _iou(a, b) -> float:
    i = _inter(a, b)
    return i / max(1, _area(a) + _area(b) - i)


def _dist(a, b) -> int:
    return max(0, a[0] - b[2], b[0] - a[2]) + max(0, a[1] - b[3], b[1] - a[3])


def _pair_rank(field_name: str, element: str) -> int:
    """Where to look first for a field: the element of its own name, then one named like it (line1 / title_1,
    heading / heading_2), then the rest. A hint only: the pixels decide."""
    f, e = field_name.lower(), element.lower()
    if f == e:
        return 0
    fd, ed = re.sub(r"\D", "", f), re.sub(r"\D", "", e)
    if (fd and fd == ed) or e.startswith(f) or f.startswith(e):
        return 1
    return 2


def _face_of(e: dict[str, Any]) -> tuple[str, str | None]:
    face = str(e.get("font") or "")
    return (face, None) if face and (Path(FONTS_DIR) / face).is_file() else ("", None)


def pixel_layout_findings(f: Frame, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Safe zones, overlaps and contrast judged on the boxes the pixels gave each field."""
    out = []
    plat = platform_box(f.deliverable, f.size, f.kind)
    boxed = [r for r in rows if r.get("box")]
    for r in boxed:
        b = tuple(r["box"])
        if not _inside(b, (0, 0, f.size[0], f.size[1])):
            continue                                  # text_cut already
        if f.zone is not None and not _inside(b, _grow(f.zone, PIXEL_TOL)):
            out.append(finding("safe_zone", r["field"], f"{list(b)} leaves the still's zone {list(f.zone)}", b))
        elif plat is not None and not _inside(b, _grow(plat, PIXEL_TOL)):
            who = covering_platforms(b, f.deliverable, f.size)
            out.append(finding("safe_zone", r["field"], f"{list(b)} leaves the {f.deliverable} box {list(plat)}"
                               + (f" (under {', '.join(who)})" if who else ""), b, platforms=who))
        for region in f.protected:
            if _intersects(b, _box(region["box"])):
                out.append(finding("safe_zone", r["field"], f"{list(b)} lies under the platform's {region.get('name')}", b))
        if r.get("contrast") is not None and r["contrast"] < TEXT_CONTRAST:
            out.append(finding("contrast", r["field"], f"measures {r['contrast']:.2f}:1 on the pixels (needs "
                               f"{TEXT_CONTRAST}:1)", b, r["contrast"], TEXT_CONTRAST))
    if f.overlap_rule:
        for i, a in enumerate(boxed):
            for c in boxed[i + 1:]:
                wa = [tuple(x["box"]) for x in a["words"] if x.get("placed")]
                wc = [tuple(x["box"]) for x in c["words"] if x.get("placed")]
                hit = next(((p, q) for p in wa for q in wc if _intersects(p, q) and _iou(p, q) < 0.5), None)
                if hit:
                    out.append(finding("overlap", f"{a['field']}+{c['field']}", f"{a['field']} {list(hit[0])} is over "
                                       f"{c['field']} {list(hit[1])} in the pixels", hit[0]))
        # a word not found whose best place (a garbled match) lies under another field's found ink: the inks cross
        for a in rows:
            for x in a.get("words", []):
                if x.get("placed") or not x.get("box") or x.get("ncc") is None or x["ncc"] < OVERLAP_HINT:
                    continue
                for c in boxed:
                    if c is a:
                        continue
                    under = [tuple(y["box"]) for y in c["words"] if y.get("placed")
                             and _intersects(tuple(y["box"]), tuple(x["box"]))]
                    if under:
                        out.append(finding("overlap", f"{a['field']}+{c['field']}", f"{a['field']}'s {x['word']!r} "
                                           f"{x['box']} is garbled under {c['field']} {list(under[0])} in the pixels",
                                           x["box"], word=x["word"]))
                        break
        # a field with words missing where a record box named for it (not for the other field) lies under another
        # field's found ink
        for a in rows:
            if all(x.get("placed") for x in a.get("words", [])):
                continue
            if any(d["class"] == "overlap" and d["element"].startswith(a["field"] + "+") for d in out):
                continue
            for c in boxed:
                if c is a:
                    continue
                own = [_box(e["box"]) for e in f.elements if e.get("box")
                       and _pair_rank(a["field"], str(e.get("name") or "")) <= 1
                       and _pair_rank(c["field"], str(e.get("name") or "")) > 1]
                inks = [tuple(y["box"]) for y in c["words"] if y.get("placed")]
                hit = next(((o, q) for o in own for q in inks if _intersects(o, q)), None)
                if hit:
                    out.append(finding("overlap", f"{a['field']}+{c['field']}", f"{a['field']}'s words are missing where "
                                       f"its record box {list(hit[0])} lies under {c['field']} {list(hit[1])} in the pixels",
                                       hit[0], basis="pixels+record"))
                    break
    return out


# ------------------------------------------------------------------------------------------------ record cross-checks
def record_findings(f: Frame, verified: set[str]) -> list[dict[str, Any]]:
    """What only the record can say (basis "record"): boxes with no truth text to find (marks, badges), a fallback
    face it recorded, a mark's ratio against its ground, and a recorded text ratio under the gate."""
    out = []
    w, h = f.size
    plat = platform_box(f.deliverable, f.size, f.kind)
    for e in f.elements:
        name = str(e.get("name") or "")
        b = _box(e["box"]) if e.get("box") else None
        if b is not None and name not in verified:
            if not _inside(b, (0, 0, w, h)):
                out.append(finding("text_cut", name, f"the record's box {list(b)} runs past the {w}x{h} frame", b,
                                   basis="record"))
            elif e.get("zone") and not _inside(b, _box(e["zone"])):
                out.append(finding("safe_zone", name, f"the record's box {list(b)} leaves its zone {e['zone']}", b,
                                   basis="record"))
            elif plat is not None and not _inside(b, plat) and f.kind != "still":
                out.append(finding("safe_zone", name, f"the record's box {list(b)} leaves the {f.deliverable} box "
                                   f"{list(plat)}", b, basis="record", platforms=covering_platforms(b, f.deliverable, f.size)))
        if e.get("fallback_for"):
            out.append(finding("glyph_fallback", name, f"recorded in {e.get('font')} standing in for {e['fallback_for']}",
                               b, basis="record"))
        r = e.get("contrast")
        if r is not None and float(r) < TEXT_CONTRAST:
            out.append(finding("contrast", name, f"recorded {float(r):.2f}:1 (needs {TEXT_CONTRAST}:1)", b, round(float(r), 2),
                               TEXT_CONTRAST, basis="record"))
    for n in f.nontext:
        v = n.get("value")
        if v is not None and float(v) < NONTEXT_MIN:
            out.append(finding("mark_contrast", str(n.get("name") or ""), f"{float(v):.2f}:1 against its ground (needs "
                               f"{NONTEXT_MIN}:1)", None, round(float(v), 2), NONTEXT_MIN, basis="record"))
    return out


# ------------------------------------------------------------------------------------------------ faces (GPU)
class FaceDetector:
    """YuNet (the pipeline's tools/yunet.onnx; opencv_zoo face_detection_yunet_2023mar, MIT) through ONNX Runtime: on
    the DirectML adapter onnx_session picks (device "auto" or an adapter index) or on the CPU (device "cpu"). The
    frame is letterboxed into the model's 640 x 640 input; boxes are decoded as OpenCV's FaceDetectorYN decodes them."""

    def __init__(self, model: Path | None = None, device: str = "auto"):
        import onnxruntime as ort
        self.model = Path(model or FACE_MODEL)
        if not self.model.is_file():
            raise FileNotFoundError(f"face model not found: {self.model}")
        if str(device).lower() == "cpu":
            self.session = ort.InferenceSession(str(self.model), providers=["CPUExecutionProvider"])
        else:
            from .onnx_session import create_session
            self.session = create_session(self.model, device=None if device == "auto" else device)
        self.provider = self.session.get_providers()[0]
        self.names = [o.name for o in self.session.get_outputs()]

    def detect(self, rgb: np.ndarray) -> list[dict[str, Any]]:
        import cv2
        h, w = rgb.shape[:2]
        s = FACE_INPUT / max(h, w)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
        small = cv2.resize(np.ascontiguousarray(rgb[..., ::-1]), (nw, nh), interpolation=cv2.INTER_AREA)
        pad = np.zeros((FACE_INPUT, FACE_INPUT, 3), np.uint8)
        pad[:nh, :nw] = small
        blob = pad.transpose(2, 0, 1)[None].astype(np.float32)
        outs = dict(zip(self.names, self.session.run(None, {self.session.get_inputs()[0].name: blob})))
        boxes, scores = [], []
        for stride in (8, 16, 32):
            cols = FACE_INPUT // stride
            score = np.sqrt(np.clip(outs[f"cls_{stride}"][0, :, 0], 0, 1) * np.clip(outs[f"obj_{stride}"][0, :, 0], 0, 1))
            bb = outs[f"bbox_{stride}"][0]
            for k in np.nonzero(score >= FACE_SCORE)[0]:
                r, c = divmod(int(k), cols)
                cx, cy = (c + bb[k, 0]) * stride, (r + bb[k, 1]) * stride
                bw, bh = math.exp(bb[k, 2]) * stride, math.exp(bb[k, 3]) * stride
                boxes.append([float(cx - bw / 2), float(cy - bh / 2), float(bw), float(bh)])
                scores.append(float(score[k]))
        if not boxes:
            return []
        faces = []
        for k in np.array(cv2.dnn.NMSBoxes(boxes, scores, FACE_SCORE, FACE_NMS)).reshape(-1):
            x, y, bw, bh = (v / s for v in boxes[int(k)])
            box = [max(0, round(x)), max(0, round(y)), min(w, round(x + bw)), min(h, round(y + bh))]
            if min(box[2] - box[0], box[3] - box[1]) >= FACE_MIN_PX:
                faces.append({"box": box, "confidence": round(scores[int(k)], 2)})
        return sorted(faces, key=lambda d: (d["box"][1], d["box"][0]))


def face_findings(boxes: Iterable[tuple[str, Any]], faces: list[dict[str, Any]], basis: str = "pixels") -> list[dict[str, Any]]:
    out = []
    for name, b in boxes:
        b = _box(b)
        for fc in faces:
            fb = tuple(fc["box"])
            share = _inter(b, fb) / max(1, _area(fb))
            if share >= FACE_SHARE:
                out.append(finding("face_overlap", name, f"covers {100 * share:.0f}% of a face {list(fb)}", b, round(share, 2),
                                   FACE_SHARE, basis=basis, face=list(fb)))
    return out


# ------------------------------------------------------------------------------------------------ captions (libass)
_TIME = re.compile(r"(\d+):(\d+):(\d+(?:\.\d+)?)")


def _t(ts: str) -> float:
    m = _TIME.match(ts.strip())
    return 0.0 if not m else int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _play_res(text: str) -> tuple[int, int]:
    mx, my = re.search(r"PlayResX:\s*(\d+)", text), re.search(r"PlayResY:\s*(\d+)", text)
    return (int(mx.group(1)) if mx else 1080, int(my.group(1)) if my else 1920)


@dataclass(eq=False)
class AssEvent:
    index: int
    layer: int
    start: float
    end: float
    style: str
    text: str
    raw: str

    @property
    def drawing(self) -> bool:
        return bool(re.search(r"\\p[1-9]", self.text))

    @property
    def halo(self) -> bool:
        return bool(re.search(r"\\1a&HFF&", self.text))

    @property
    def plain(self) -> str:
        return re.sub(r"\{[^}]*\}", "", self.text).replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip()


@dataclass
class AssFile:
    head: list[str]
    fmt: str
    styles: dict[str, str]
    events: list[AssEvent]
    size: tuple[int, int]
    fonts: dict[str, bytes]      # embedded font files ([Fonts]), by file name


def _uudecode_ass(lines: list[str]) -> bytes:
    """ASS [Fonts] encoding: 6-bit groups offset by 33, four characters to three bytes."""
    s = "".join(lines)
    out = bytearray()
    for i in range(0, len(s), 4):
        chunk = [ord(c) - 33 for c in s[i:i + 4]]
        n = len(chunk)
        chunk += [0] * (4 - n)
        v = (chunk[0] << 18) | (chunk[1] << 12) | (chunk[2] << 6) | chunk[3]
        out += bytes(((v >> 16) & 255, (v >> 8) & 255, v & 255))[: {4: 3, 3: 2, 2: 1}.get(n, 0)]
    return bytes(out)


def parse_ass(text: str) -> AssFile:
    lines = text.splitlines()
    k = next((i for i, ln in enumerate(lines) if ln.strip().lower() == "[events]"), len(lines))
    styles = {}
    for ln in lines[:k]:
        if ln.startswith("Style:"):
            parts = [p.strip() for p in ln[6:].split(",")]
            styles[parts[0]] = parts[1]
    fonts: dict[str, bytes] = {}
    cur, buf, in_fonts = None, [], False
    for ln in lines[:k]:
        if ln.strip().startswith("["):
            if cur:
                fonts[cur] = _uudecode_ass(buf)
            cur, buf, in_fonts = None, [], ln.strip().lower() == "[fonts]"
            continue
        if in_fonts:
            if ln.lower().startswith("fontname:"):
                if cur:
                    fonts[cur] = _uudecode_ass(buf)
                cur, buf = ln.split(":", 1)[1].strip(), []
            elif cur and ln.strip():
                buf.append(ln.strip())
    if cur:
        fonts[cur] = _uudecode_ass(buf)
    fmt = next((ln for ln in lines[k:] if ln.startswith("Format:")),
               "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    events = []
    for i, ln in enumerate(lines[k:]):
        if ln.startswith("Dialogue:"):
            p = ln[9:].split(",", 9)
            events.append(AssEvent(i, int(p[0].strip() or 0), _t(p[1]), _t(p[2]), p[3].strip(), p[9], ln))
    return AssFile(lines[:k], fmt, styles, events, _play_res(text), fonts)


def drawing_box(ev: AssEvent) -> tuple[int, int, int, int] | None:
    """A plate's rectangle in frame pixels: its \\pos plus its drawing's extent (video_qa.caption_pills)."""
    pos = re.search(r"\\pos\((-?[\d.]+),\s*(-?[\d.]+)\)", ev.text)
    shape = re.search(r"\{[^}]*\\p[1-9][^}]*\}([^{]*)", ev.text)   # the drawing after its \p block, in any tag order
    if not pos or not shape:
        return None
    nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", shape.group(1))]
    xs, ys = nums[0::2], nums[1::2]
    if not xs or not ys:
        return None
    x, y = float(pos.group(1)), float(pos.group(2))
    return (math.floor(x + min(xs)), math.floor(y + min(ys)), math.ceil(x + max(xs)), math.ceil(y + max(ys)))


def _mask_line(ev: AssEvent, probe: bool = False) -> str:
    """The event as plain white glyph fills: no outline, shadow, blur or halo (video_qa._caption_mask_ass)."""
    t = re.sub(r"\\(?:3c|4c)&H[0-9A-Fa-f]+&|\\(?:bord|shad|blur|be)[\d.]+|\\[34]a&H[0-9A-Fa-f]+&", "", ev.text)
    t = re.sub(r"\\1?c&H[0-9A-Fa-f]{6,8}&", r"\\c&H00FFFFFF&", t)
    if probe:
        t = re.sub(r"\\fn[^\\}]*", lambda _m: "\\fn" + FALLBACK_PROBE, t)
    p = ev.raw[9:].split(",", 9)
    p[9] = "{\\c&H00FFFFFF&\\bord0\\shad0\\blur0}" + t
    return "Dialogue:" + ",".join(p)


def _families(ass: AssFile, ev: AssEvent) -> list[str]:
    fams = [ass.styles.get(ev.style, "")] + [m.strip() for m in re.findall(r"\\fn([^\\}]*)", ev.text)]
    return sorted({f for f in fams if f})


def _ffmpeg_esc(p: Path) -> str:
    return str(Path(p).resolve()).replace("\\", "/").replace(":", "\\:")


def render_ass_mask(ass: AssFile, events: list[AssEvent], t: float, work: Path, fonts_dir: Path,
                    probe_family: str | None = None) -> np.ndarray:
    """libass's glyph mask (H x W uint8) of `events` at time `t`; with `probe_family` that family is renamed (styles
    and \\fn tags) to one that cannot exist, so libass falls back."""
    head = list(ass.head)
    if probe_family is not None:
        pat = re.compile(r"^(Style:\s*[^,]*,)\s*" + re.escape(probe_family) + r"\s*,")
        head = [pat.sub(lambda m: m.group(1) + FALLBACK_PROBE + ",", ln) if ln.startswith("Style:") else ln for ln in head]
    body = ["[Events]", ass.fmt] + [_mask_line(e, probe_family is not None) for e in events]
    work.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=".ass", dir=work)
    f = Path(name)
    with open(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(head + body) + "\n")
    w, h = ass.size
    try:
        res = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=25:d=1",
                              "-vf", f"setpts=PTS+{t:.3f}/TB,subtitles='{_ffmpeg_esc(f)}':fontsdir='{_ffmpeg_esc(fonts_dir)}'",
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             check=True, capture_output=True, timeout=120)
    finally:
        f.unlink(missing_ok=True)
    return np.frombuffer(res.stdout, np.uint8)[: w * h].reshape(h, w)


def _bbox(mask: np.ndarray, level: int = 127) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask > level)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def sample_events(ass: AssFile, n: int = CAPTION_SAMPLES, until: float | None = None) -> list[AssEvent]:
    """Text events to judge: every one, of every style, in file order (round 3: a sample misses the unsafe event
    between samples; `n` is kept for callers and ignored)."""
    del n
    return [e for e in ass.events if not e.drawing and not e.halo and e.plain and (until is None or e.start < until - 0.1)]


def _event_times(ev: AssEvent) -> list[float]:
    """When an event is judged: its 60% point; a moving or transforming event also near its start and its end."""
    t = [_when(ev)]
    if re.search(r"\\(?:move|t)\(", ev.text) and ev.end - ev.start > 0.04:
        t = [round(ev.start + 0.01, 3)] + t + [round(ev.end - 0.01, 3)]
    return t


def truth_words(text: str) -> list[str]:
    """The words a field must show, as drawn units: each run between spaces with its punctuation, digits and
    accents (QF-136 round 2: a dash, an apostrophe or an accent is truth like any letter). Emoji and pictographs are
    left out: the gates remove them from art by a documented rule (typography.drawable, recorded)."""
    t = unicodedata.normalize("NFC", text or "")
    return "".join(c for c in t if not _pictograph(c)).split()


def _pictograph(c: str) -> bool:
    o = ord(c)
    return (o >= 0x1F000 or 0x2600 <= o <= 0x27BF or c in "️‍"
            or (unicodedata.category(c) == "So" and c not in "°©®™"))


def _pos_of(ev: AssEvent) -> str:
    m = re.search(r"\\pos\(([^)]*)\)", ev.text)
    return m.group(1) if m else ""


def _when(ev: AssEvent) -> float:
    return round(ev.start + 0.6 * (ev.end - ev.start), 3)


@lru_cache(maxsize=64)
def _cmap_of(data: bytes) -> tuple[str, frozenset[int]]:
    from fontTools.ttLib import TTFont
    tt = TTFont(io.BytesIO(data), lazy=True)
    fam = tt["name"].getDebugName(1) or ""
    return fam, frozenset(tt.getBestCmap() or {})


def resolved_face(ass: AssFile, family: str, fonts_dir: Path) -> tuple[str, frozenset[int]] | None:
    """The font file that carries `family` (embedded in the caption file, or bundled): (file, its characters)."""
    cands = [(n, d) for n, d in sorted(ass.fonts.items())]
    cands += [(p.name, p.read_bytes()) for p in sorted(Path(fonts_dir).glob("*.ttf"))]
    for n, d in cands:
        try:
            fam, cmap = _cmap_of(d)
        except Exception:  # noqa: BLE001, S112  an unreadable font file is no candidate
            continue
        if fam.strip().lower() == family.strip().lower():
            return n, cmap
    return None


def _norm_words(text: str) -> list[str]:
    return [w.casefold() for w in tokens(text)]


def caption_findings(f: Frame, work: Path, fonts_dir: Path | None = None, samples: int = CAPTION_SAMPLES,
                     shots: list | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Each sampled caption/badge event rendered by libass: glyphs against their plate and the Reel box, a face libass
    never resolved, characters its face lacks; and the caption words against the narration (words.json)."""
    from .typography import ring_contrast
    fonts_dir = Path(fonts_dir or FONTS_DIR)
    ass = parse_ass(Path(f.ass).read_text(encoding="utf-8", errors="replace"))
    out, rows = [], []
    w, h = ass.size
    plat = platform_box("reel", (w, h), "caption") if h > w else None
    probed: dict[tuple[str, str], bool] = {}
    if f.words is not None:          # the narration is the captions' truth (text, order, completeness, timing)
        out += caption_truth_findings(ass, json.loads(Path(f.words).read_text(encoding="utf-8")), f.until)
    else:
        out.append(finding("coverage", "captions", "no narration (words.json) to check the captions against",
                           basis="truth"))
    from .video_composer import RECON_BADGE_LABEL  # the disclosure's signed wording
    for ev in ass.events:
        if ev.style == "ReconBadge" and not ev.drawing and ev.plain and _norm_words(ev.plain) != _norm_words(RECON_BADGE_LABEL):
            out.append(finding("text_identity", f"{ev.style}@{ev.start:.2f}s", f"the badge reads {ev.plain[:60]!r}; its "
                               f"wording is {RECON_BADGE_LABEL!r}", basis="pixels+truth"))
    judged = 0
    for ev in sample_events(ass, samples):
        for d in _slow_fades(ev):
            out.append(finding("text_identity", f"{ev.style}@{ev.start:.2f}s", f"a word fades in over {d:.2f} s (more "
                               f"than {CAPTION_FADE_MAX} s): unreadable while it is said", None, round(d, 2),
                               CAPTION_FADE_MAX))
    for ev, t in [(e, t_) for e in sample_events(ass, samples) for t_ in _event_times(e)]:
        name = f"{ev.style}@{t:.2f}s"
        mask = render_ass_mask(ass, [ev], t, work, fonts_dir)
        gb = _bbox(mask)
        main_time = t == _when(ev)      # the extra times of a moving event judge its geometry only (a fade-out ends it)
        if gb is None and not main_time:
            continue
        if gb is None and ev.end - ev.start > 0.02:     # still fading in at its middle: judged at its last moment
            t = max(ev.start, ev.end - 0.01)
            name = f"{ev.style}@{t:.2f}s"
            mask = render_ass_mask(ass, [ev], t, work, fonts_dir)
            gb = _bbox(mask)
        row: dict[str, Any] = {"event": name, "text": ev.plain[:80], "glyphs": list(gb) if gb else None}
        if gb is None:
            if ev.style == "ReconBadge":   # the disclosure must show whenever it is on
                out.append(finding("text_identity", name, "the reconstruction badge draws nothing (transparent or "
                                   "off the frame)", None))
            elif _event_steady(ass, ev):   # text meant to show that libass does not draw (transparent, empty face)
                out.append(finding("text_identity", name, f"libass draws nothing for {ev.plain[:40]!r}, which is meant "
                                   "to show", None))
            rows.append(row)             # else a line still hidden at this time (its burst's other line lights up)
            continue
        judged += 1
        plates = [(drawing_box(p), p) for p in ass.events if p.drawing and p.start <= t < p.end]
        plates = [(b, p) for b, p in plates if b is not None and _intersects(b, gb)]
        if plates and main_time:         # the glyphs as drawn on their plate: their contrast there (an opaque plate
            # over them, black on black, a half-transparent word)
            rgb = render_ass_frame(Path(f.ass), t, ass.size, fonts_dir)
            ratio = ring_contrast(rgb, mask, (0, 0))
            row["contrast"] = ratio
            if ratio is None or ratio < CAPTION_PLATE_CONTRAST:
                out.append(finding("contrast", name, f"caption glyphs measure {ratio if ratio is None else round(ratio, 2)}"
                                   f":1 on their plate (needs {CAPTION_PLATE_CONTRAST}:1)", gb, ratio,
                                   CAPTION_PLATE_CONTRAST))
            pb = max(plates, key=lambda bp: _inter(bp[0], gb))[0]
            over = max(pb[0] - gb[0], pb[1] - gb[1], gb[2] - pb[2], gb[3] - pb[3])
            row["plate"], row["overrun_px"] = list(pb), over
            if over > PLATE_TOLERANCE:
                out.append(finding("plate_overrun", name, f"glyphs {list(gb)} run {over} px past their plate {list(pb)}",
                                   gb, over, PLATE_TOLERANCE, plate=list(pb)))
        if gb[0] <= 0 or gb[1] <= 0 or gb[2] >= w or gb[3] >= h:
            out.append(finding("text_cut", name, f"glyphs {list(gb)} reach the {w}x{h} frame edge", gb))
        elif plat is not None and not _inside(gb, plat):
            who = covering_platforms(gb, "reel", (w, h))
            out.append(finding("safe_zone", name, f"glyphs {list(gb)} leave the Reel box {list(plat)}"
                               + (f" (under {', '.join(who)})" if who else ""), gb, platforms=who))
        for fam in _families(ass, ev):
            key = (ev.style, fam)
            if key not in probed:
                probe = render_ass_mask(ass, [ev], t, work, fonts_dir, probe_family=fam)
                probed[key] = bool(np.array_equal(probe, mask))
            if probed[key]:
                row.setdefault("unresolved", []).append(fam)
                out.append(finding("glyph_fallback", name, f"libass never resolved the face {fam!r}: the event draws the "
                                   "same pixels as a family that cannot exist", gb, family=fam))
            else:
                rf = resolved_face(ass, fam, fonts_dir)
                if rf is not None:
                    lacking = sorted({c for c in ev.plain if not c.isspace() and ord(c) not in rf[1]})
                    if lacking:
                        out.append(finding("text_identity", name, f"{rf[0]} has no glyph for {''.join(lacking)!r}", gb,
                                           face=rf[0]))
        rows.append(row)
        if shots is not None and len(shots) < SHOTS_PER_FRAME and any(d["element"] == name for d in out):
            shots.append((f"{f.name}#{name}", render_ass_frame(Path(f.ass), t, ass.size, fonts_dir), gb, []))
    if not judged and not any(d["class"] == "text_identity" for d in out):
        out.append(finding("coverage", "captions", "no caption event drew any glyph: nothing was judged", basis="pixels"))
    return out, rows


def render_ass_frame(ass_path: Path, t: float, size: tuple[int, int], fonts_dir: Path) -> np.ndarray:
    """The caption file as libass draws it at `t` over a mid grey (for the contact sheet): read, never written."""
    w, h = size
    res = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x606870:s={w}x{h}:r=25:d=1",
                          "-vf", f"setpts=PTS+{t:.3f}/TB,subtitles='{_ffmpeg_esc(ass_path)}':fontsdir='{_ffmpeg_esc(fonts_dir)}'",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True, timeout=120)
    return np.frombuffer(res.stdout, np.uint8)[: w * h * 3].reshape(h, w, 3)


def _dedupe_bursts(shown: list[tuple[str, list[str]]]) -> list[str]:
    """Caption events repeat a burst's lines while its words light up one by one (a two-line burst's lines take turns):
    each line, keyed by its position and words, once, unless it has not been seen among the last few lines."""
    seq: list[str] = []
    recent: list[tuple[str, tuple[str, ...]]] = []
    for pos, words in shown:
        key = (pos, tuple(words))
        if key in recent:
            continue
        recent = (recent + [key])[-6:]
        seq += words
    return seq


# ------------------------------------------------------------------------------------------------ the master
def decode_frame(video: Path, t: float, size: tuple[int, int]) -> np.ndarray:
    w, h = size
    res = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf",
                          f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True,
                         timeout=120)
    return np.frombuffer(res.stdout, np.uint8)[: w * h * 3].reshape(h, w, 3)


def probe_size(video: Path) -> tuple[int, int] | None:
    try:
        res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True, timeout=60)
        a, b = res.stdout.strip().split(",")[:2]
        return int(a), int(b)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def probe_fps(video: Path) -> float | None:
    try:
        res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                              "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True, timeout=60)
        a, _, b = res.stdout.strip().partition("/")
        return float(a) / float(b or 1)
    except (subprocess.SubprocessError, ValueError, OSError, ZeroDivisionError):
        return None


SHOTS_PER_FRAME = 3           # contact-sheet shots a caption file or master keeps (full frames: memory is bounded by this)
MASTER_BLOB = 12              # px: a connected run of pixels that disagree with the glyphs this large is a difference


def master_identity(rgb: np.ndarray, mask: np.ndarray) -> str:
    """Whether the decoded master shows the captions' own glyphs (libass, the same file, the same time; the master
    was burned with them, so the two agree to the pixel but for compression). The fill colour is the median of the
    glyph cores, the ground the median just around the glyphs (their outline, plate or picture); a pixel is ink when
    it is nearer the fill than the ground. No run of MASTER_BLOB px of the glyphs (coverage > 0.7) may lack ink (a
    letter missing or changed), and none beside them (coverage < 0.3) may carry it (a letter drawn that is not in
    the file: TNE for THE). '' when they agree."""
    import cv2
    a = mask.astype(np.float32) / 255.0
    core = a > 0.9
    if core.sum() < 20:
        return ""
    px = rgb.astype(np.float32)
    fill = np.median(px[core], axis=0)
    near = cv2.dilate((a > 0.02).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    ring = near & (a == 0)
    if ring.sum() < 20:
        return ""
    ground = np.median(px[ring], axis=0)
    gap = float(np.abs(fill - ground).mean())
    if gap < 20:                     # the master shows no caption where its file draws one (or one the ground hides)
        return "missing: the master shows no caption glyphs where its file draws them (fill and ground alike)"
    # each glyph's own fill (a caption lights its spoken word in another colour): a glyph's pixel is ink when nearer
    # its glyph's fill than the ground; a pixel beside the glyphs when nearer any glyph's fill than the ground
    n_c, lab = cv2.connectedComponents(cv2.dilate(core.astype(np.uint8), np.ones((3, 3), np.uint8)))
    dg = np.abs(px - ground).mean(axis=2)
    ink_own = np.zeros(a.shape, bool)
    ink_any = np.zeros(a.shape, bool)
    for k in range(1, n_c):
        sel = lab == k
        csel = sel & core
        if csel.sum() < 4:
            continue
        fk = np.median(px[csel], axis=0)
        if float(np.abs(fk - ground).mean()) < 20:
            continue
        dk = np.abs(px - fk).mean(axis=2) < dg
        ink_own |= sel & dk
        # beside the glyphs only the fill itself counts (an anti-aliased edge sits between fill and ground)
        ink_any |= np.abs(px - fk).mean(axis=2) < 0.35 * float(np.abs(fk - ground).mean())
    ink_near = ink_own | ink_any
    # the master may sit a pixel off the file's own drawing (scaling, encoding): registered first, within a pixel
    best = None
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            sa = np.roll(np.roll(a, dy, axis=0), dx, axis=1)
            maps = ((sa > 0.7) & ~ink_near, near & (sa < 0.3) & ink_any)
            cost = int(maps[0].sum() + maps[1].sum())
            if best is None or cost < best[0]:
                best = (cost, maps)
    for what, bad in zip(("of the glyphs without their ink (a letter missing or changed)",
                          "of ink beside the glyphs where they have none (a letter that is not in the file)"), best[1]):
        n, _lab, st, _c = cv2.connectedComponentsWithStats(bad.astype(np.uint8))
        if n > 1 and int(st[1:, 4].max()) >= MASTER_BLOB:
            k = 1 + int(np.argmax(st[1:, 4]))
            return f"{int(st[k, 4])} px {what} at x {int(st[k, 0])}, y {int(st[k, 1])}"
    return ""


def master_findings(f: Frame, work: Path, faces: FaceDetector | None, fonts_dir: Path | None = None,
                    samples: int = CAPTION_SAMPLES):
    """The captions after composition: master frames at sampled caption times with the caption glyphs (libass, the
    same file) on them: their box against the Reel box, their ratio on the delivered pixels and any face under them."""
    from .typography import ring_contrast
    fonts_dir = Path(fonts_dir or FONTS_DIR)
    ass = parse_ass(Path(f.ass).read_text(encoding="utf-8", errors="replace"))
    out, rows, shots = [], [], []
    size = probe_size(f.video) or ass.size
    if size != ass.size:
        out.append(finding("safe_zone", "master", f"the master is {size[0]}x{size[1]}; its captions were laid out at "
                           f"{ass.size[0]}x{ass.size[1]}"))
    w, h = ass.size
    plat = platform_box("reel", (w, h), "caption") if h > w else None
    on_screen = [e for e in ass.events if not e.drawing and not e.halo and e.plain]
    fps = probe_fps(f.video)
    judged = 0
    for ev in sample_events(ass, samples, f.until):
        t = _when(ev)
        if fps:                          # a frame's own time: the burn drew the captions (and their animation) then
            t = math.floor(t * fps) / fps
        name = f"{ev.style}@{t:.2f}s"
        mask = render_ass_mask(ass, [e for e in on_screen if e.start <= t < e.end], t, work, fonts_dir)
        gb = _bbox(mask)
        if gb is None:
            continue
        rgb = decode_frame(f.video, t + (0.5 / fps if fps else 0.0), (w, h))
        judged += 1
        ratio = ring_contrast(rgb, mask, (0, 0))
        row = {"event": name, "t": t, "glyphs": list(gb), "contrast": ratio}
        why = master_identity(rgb, mask)
        if why:
            row["identity"] = why
            out.append(finding("text_identity", name, f"the master's caption pixels are not its captions' glyphs: {why}",
                               gb, basis="pixels+truth"))
        if ratio is not None and ratio < TEXT_CONTRAST and not why.startswith("missing"):
            out.append(finding("contrast", name, f"captions measure {ratio:.2f}:1 on the master (needs {TEXT_CONTRAST}:1)",
                               gb, ratio, TEXT_CONTRAST))
        if plat is not None and not _inside(gb, plat):
            who = covering_platforms(gb, "reel", (w, h))
            out.append(finding("safe_zone", name, f"caption glyphs {list(gb)} leave the Reel box {list(plat)}"
                               + (f" (under {', '.join(who)})" if who else ""), gb, platforms=who))
        fs = []
        if faces is not None:
            fs = faces.detect(rgb)
            row["faces"] = [d["box"] for d in fs]
            out += face_findings([(name, gb)], fs)
        rows.append(row)
        # the contact sheet keeps a shot only for a sample with a finding, and only a few a frame: every judged event
        # kept would hold a full frame each (round 3: one check reached 48 GB that way)
        if len(shots) < SHOTS_PER_FRAME and any(d["element"] == name for d in out):
            shots.append((f"{f.name}#{name}", rgb, gb, fs))
        del rgb
    if not judged:
        out.append(finding("coverage", "master", "no caption event of the master drew a glyph: nothing was judged",
                           basis="pixels"))
    return out, rows, shots


# ------------------------------------------------------------------------------------------------ the gate
def _load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _sort_findings(fs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(fs, key=lambda d: (CLASSES.index(d["class"]) if d["class"] in CLASSES else 99, d["element"],
                                     d.get("box") or [], d["detail"]))


def judge_frame(f: Frame, work: Path, detector: FaceDetector | None, fonts_dir: Path | None = None,
                samples: int = CAPTION_SAMPLES, timing: dict | None = None) -> tuple[dict[str, Any], list]:
    """One frame's findings and measures: (report entry, contact-sheet shots). A frame the critic cannot read or
    judge (a broken picture, a failed decode) fails with a coverage finding; it never passes."""
    try:
        return _judge_frame(f, work, detector, fonts_dir, samples, timing)
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as e:
        d = finding("coverage", "", f"the frame could not be judged: {type(e).__name__}: {str(e)[:160]}", basis="record")
        return ({"frame": f.name, "kind": f.kind, "size": list(f.size), "deliverable": f.deliverable, "source": f.source,
                 "findings": [d], "failed_classes": ["coverage"], "pass": False}, [])
    finally:
        release_caches()


def _judge_frame(f: Frame, work: Path, detector: FaceDetector | None, fonts_dir: Path | None = None,
                 samples: int = CAPTION_SAMPLES, timing: dict | None = None) -> tuple[dict[str, Any], list]:
    import time
    entry: dict[str, Any] = {"frame": f.name, "kind": f.kind, "size": list(f.size), "deliverable": f.deliverable,
                             "source": f.source}
    found: list[dict[str, Any]] = []
    shots: list = []
    if f.kind == "unreadable":
        found.append(finding("coverage", "", f"unreadable record or picture {f.source}", basis="record"))
    elif f.kind == "caption":
        fs, rows = caption_findings(f, work, fonts_dir, samples, shots)
        found += fs
        entry["events"] = rows
    elif f.kind == "master":
        fs, rows, shots = master_findings(f, work, detector, fonts_dir, samples)
        found += fs
        entry["samples"] = rows
    else:
        verified: set[str] = set()
        if f.image is None:
            found.append(finding("coverage", "", "the record names a picture the set does not hold", basis="record"))
        else:
            rgb = _load_rgb(f.image)
            if (rgb.shape[1], rgb.shape[0]) != tuple(f.size):
                found.append(finding("record_mismatch", "", f"the picture is {rgb.shape[1]}x{rgb.shape[0]}; the record says "
                                     f"{f.size[0]}x{f.size[1]}", basis="record"))
                f.size = (rgb.shape[1], rgb.shape[0])
            expect = f.expect or []
            if not expect:          # nothing the renderer was asked to draw is known: nothing is verified (never a pass)
                found.append(finding("coverage", "", "no truth for this frame: its record has no recipe, or its renderer "
                                     "or graphic kind has no truth reader (the renderer's own record is never truth)",
                                     basis="record"))
            fv = Frame(**{**f.__dict__, "expect": expect})
            fs, rows = verify_fields(rgb, fv)
            found += fs + pixel_layout_findings(fv, rows)
            entry["fields"] = rows
            verified = {str(r.get("element")) for r in rows if r.get("box")} | {str(r["field"]) for r in rows if r.get("box")}
            if detector is not None:
                ft = time.perf_counter()
                faces = detector.detect(rgb)
                if timing is not None:
                    timing["face_seconds"] += time.perf_counter() - ft
                    timing["face_frames"] += 1
                entry["faces"] = faces
                found += face_findings([(r["field"], r["box"]) for r in rows if r.get("box")], faces)
                found += face_findings([(str(e.get("name")), e["box"]) for e in f.elements
                                        if e.get("box") and str(e.get("name")) not in verified], faces, basis="record")
        found += record_findings(f, verified)
    entry["findings"] = _sort_findings(found)
    entry["failed_classes"] = sorted({d["class"] for d in found}, key=CLASSES.index)
    entry["pass"] = not found
    return entry, _pack_shots(shots, entry["findings"])


def _pack_shots(shots: list, findings: list[dict[str, Any]]) -> list[tuple[str, bytes, int]]:
    """Each contact-sheet shot as its marked half-size JPEG (bytes) and finding count: a frame's full pixels are not
    kept past its judgement (memory)."""
    out = []
    for name, rgb, gb, fs in shots:
        own = [d for d in findings if d["element"] == name.split("#", 1)[1]]
        buf = io.BytesIO()
        marked(rgb, own, fs, [gb]).save(buf, "JPEG", quality=80)
        out.append((name, buf.getvalue(), len(own)))
    return out


_WORKER: dict[str, Any] = {}


def _init_worker(device: str, faces: bool, fonts_dir: Path | None) -> None:
    import atexit
    import shutil
    try:
        _WORKER["det"] = FaceDetector(device=device) if faces else None
    except (FileNotFoundError, ImportError):
        _WORKER["det"] = None
    _WORKER["tmp"] = Path(tempfile.mkdtemp(prefix="rr-critic-w-"))
    _WORKER["fonts"] = fonts_dir
    atexit.register(shutil.rmtree, _WORKER["tmp"], True)


def _work(f: Frame, samples: int):
    timing = {"face_seconds": 0.0, "face_frames": 0}
    entry, shots = judge_frame(f, _WORKER["tmp"], _WORKER["det"], _WORKER["fonts"], samples, timing)
    import os
    timing["peak_mb"], timing["pid"] = peak_memory_mb(), os.getpid()
    return entry, shots, timing


def release_caches() -> None:
    """The per-frame drawings (word images, glyphs, shaped words) let go: a frame's words and their variants are its
    own, and kept they hold gigabytes (QF-136 round 2: 12-16 GB a worker before)."""
    for fn in (_word_image, _glyph, _shape):
        fn.cache_clear()


def peak_memory_mb() -> float:
    """This process's peak resident memory (Windows: peak working set; elsewhere: ru_maxrss), in MB."""
    import sys
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return round(c.PeakWorkingSetSize / 2 ** 20, 1)
        return 0.0
    import resource
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


_PICTURE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}


def set_coverage(root: Path, frames: list[Frame], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the set holds that the gate did not judge, and the failures of coverage (findings, class "coverage"): an
    empty set; a render error the set records (render_errors.json); a picture in a folder whose records claim it (a
    still_layout.json or harness records beside it) that no record names; with the lander's freeze manifest
    (freeze_manifest.py MANIFEST.json), any media file it lists that was not judged or is not there. Other pictures and
    videos are listed as not judged (not a pass: see NOT_GATED)."""
    root = Path(root)
    found: list[dict[str, Any]] = []
    judged = {f.name for f in frames} | {_rel(f.image, root) for f in frames if f.image is not None}
    if not frames:
        found.append(finding("coverage", "", "the set holds no frame the gate can judge", basis="record"))
    not_judged = []
    for p in sorted(root.rglob("*")):
        parts = p.relative_to(root).parts
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _SKIP_DIRS & set(parts[:-1]):         # a renderer's intermediates: listed, never a deliverable
            if p.suffix.lower() in _PICTURE_EXT | _VIDEO_EXT:
                not_judged.append(rel + " (intermediate)")
            continue
        if p.name == "render_errors.json":
            try:
                errs = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                errs = ["unreadable"]
            if errs:
                found.append(finding("coverage", "", f"the set records render errors ({rel}: {len(errs)})", basis="record"))
            continue
        ext = p.suffix.lower()
        if ext not in _PICTURE_EXT | _VIDEO_EXT or rel in judged:
            continue
        if p.name in NON_DELIVERABLES:           # a review file, named in the list: listed, never a deliverable
            not_judged.append(rel + " (review file, not a deliverable)")
            continue
        # round 3: every picture or video the set holds that the gate did not judge fails it (an intermediate is in a
        # renderer's own working folder, listed apart)
        found.append(finding("coverage", rel, "a picture or video the set holds that the gate did not judge",
                             basis="record"))
        not_judged.append(rel)
    if manifest is not None:
        for m in manifest.get("images") or []:
            rel = str(m.get("path", "")).replace("\\", "/")
            if not (root / rel).is_file():
                found.append(finding("coverage", rel, "the freeze manifest lists it; the set does not hold it", basis="record"))
            elif rel not in judged:
                found.append(finding("coverage", rel, "the freeze manifest lists it; the gate did not judge it", basis="record"))
    return {"findings": _sort_findings(found), "not_judged": not_judged, "not_gated": list(NOT_GATED),
            "frames_judged": len(frames)}


def check_set(root: Path, out_dir: Path, device: str = "auto", faces: bool = True, samples: int = CAPTION_SAMPLES,
              fonts_dir: Path | None = None, sheet: bool = True, workers: int = 1,
              manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Runs the gate on a render set: report.json (deterministic), timing.json and the contact sheet in `out_dir`,
    which may not lie inside the set. Returns the report. `workers` > 1 judges the pictures in that many processes
    (each with its own face model session); the report is the same, frame for frame."""
    import time
    root, out_dir = Path(root).resolve(), Path(out_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"no render set at {root}")
    if out_dir == root or root in out_dir.parents:
        raise ValueError("the report folder may not lie inside the render set it reads")
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    detector, face_note = None, "checked"
    if faces:
        try:
            detector = FaceDetector(device=device)
        except (FileNotFoundError, ImportError) as e:
            face_note = f"NOT CHECKED: {e}"
    else:
        face_note = "NOT CHECKED: switched off"
    report: dict[str, Any] = {"set": root.name, "advisory": True, "classes": list(CLASSES), "faces": face_note,
                              "thresholds": {"text_contrast": TEXT_CONTRAST, "nontext_contrast": NONTEXT_MIN,
                                             "word_ncc": WORD_MIN, "style_ncc": STYLE_MIN, "identity_missing_max": IDENTITY_MISSING_MAX, "identity_extra_max": IDENTITY_EXTRA_MAX, "record_tolerance_px": RECORD_TOL,
                                             "face_score": FACE_SCORE, "face_share": FACE_SHARE,
                                             "plate_tolerance_px": PLATE_TOLERANCE, "pixel_box_tolerance_px": PIXEL_TOL}, "frames": []}
    timing = {"device": getattr(detector, "provider", "none"), "face_seconds": 0.0, "face_frames": 0}
    peaks: dict[int, float] = {}
    shots_all: list = []
    frames = discover(root)
    entries: dict[int, dict[str, Any]] = {}
    pictures = list(range(len(frames))) if workers > 1 else []   # every frame in the pool (captions, masters too)
    if pictures:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers, initializer=_init_worker,
                                 initargs=(device, detector is not None, fonts_dir)) as pool:
            for k, (entry, shots, tm) in zip(pictures, pool.map(_work, [frames[k] for k in pictures],
                                                                [samples] * len(pictures))):
                entries[k] = entry
                shots_all += shots
                timing["face_seconds"] += tm["face_seconds"]
                timing["face_frames"] += tm["face_frames"]
                peaks[tm["pid"]] = max(peaks.get(tm["pid"], 0.0), tm["peak_mb"])
    with tempfile.TemporaryDirectory(prefix="rr-critic-") as tmp:
        for k, f in enumerate(frames):
            if k not in entries:
                entries[k], shots = judge_frame(f, Path(tmp), detector, fonts_dir, samples, timing)
                shots_all += shots
    report["frames"] = [entries[k] for k in range(len(frames))]
    per_class = {c: {"frames_failed": 0, "findings": 0} for c in CLASSES}
    for e in report["frames"]:
        for c in e["failed_classes"]:
            per_class[c]["frames_failed"] += 1
        for d in e["findings"]:
            per_class[d["class"]]["findings"] += 1
    report["per_class"] = {c: {**v, "pass": v["findings"] == 0} for c, v in per_class.items()}
    report["frame_count"] = len(report["frames"])
    report["frames_failed"] = sum(1 for e in report["frames"] if not e["pass"])
    report["coverage"] = set_coverage(root, frames, manifest)
    per_class["coverage"]["findings"] += len(report["coverage"]["findings"])
    report["per_class"]["coverage"]["findings"] = per_class["coverage"]["findings"]
    report["per_class"]["coverage"]["pass"] = per_class["coverage"]["findings"] == 0
    report["gate"] = ("pass" if report["frames_failed"] == 0 and not report["coverage"]["findings"]
                      and report["frame_count"] > 0 and face_note == "checked" else "fail")
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                         encoding="utf-8", newline="\n")
    if sheet:
        contact_sheet(root, report, out_dir, shots_all)
    timing["seconds"] = round(time.perf_counter() - t0, 2)
    # peak resident memory: each worker's and this process's (the sum bounds what the gate held at once)
    timing["memory_peak_mb"] = {"main": peak_memory_mb(), "workers": sorted(peaks.values(), reverse=True),
                                "sum": round(peak_memory_mb() + sum(peaks.values()), 1)}
    timing["face_seconds"] = round(timing["face_seconds"], 3)
    (out_dir / "timing.json").write_text(json.dumps(timing, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report


# ------------------------------------------------------------------------------------------------ contact sheet
SHEET_MAX_BYTES = 300 * 1024
_COLOURS = {"coverage": (255, 255, 255), "text_identity": (255, 40, 40), "record_mismatch": (255, 120, 120), "text_cut": (230, 40, 40),
            "safe_zone": (255, 140, 0), "overlap": (200, 0, 200), "plate_overrun": (255, 0, 90),
            "glyph_fallback": (0, 170, 255), "contrast": (255, 220, 0), "mark_contrast": (160, 255, 0),
            "face_overlap": (0, 255, 200)}


def save_jpeg(im: Image.Image, path: Path, limit: int = SHEET_MAX_BYTES) -> None:
    """A JPEG at the best quality that stays under `limit` bytes (evidence images: <= 300 KB)."""
    for q in (88, 80, 72, 64, 56, 48, 40):
        im.save(path, "JPEG", quality=q, optimize=True)
        if path.stat().st_size <= limit:
            return
    scale = 0.85
    while path.stat().st_size > limit and scale > 0.2:
        im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS).save(
            path, "JPEG", quality=40, optimize=True)
        scale *= 0.85


def marked(rgb: np.ndarray, findings: list[dict[str, Any]], faces: list | None = None, boxes: list | None = None) -> Image.Image:
    """The frame at half size with each finding's box outlined in its class colour and numbered; faces in blue,
    found text in thin grey."""
    im = Image.fromarray(rgb).convert("RGB")
    im = im.resize((max(1, im.width // 2), max(1, im.height // 2)), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    for b in boxes or []:
        d.rectangle([v // 2 for v in b], outline=(150, 150, 150), width=1)
    for fc in faces or []:
        d.rectangle([v // 2 for v in (fc["box"] if isinstance(fc, dict) else fc)], outline=(0, 120, 255), width=2)
    for k, fd in enumerate(findings, 1):
        if not fd.get("box"):
            continue
        b = [v // 2 for v in fd["box"]]
        col = _COLOURS.get(fd.get("class", ""), (255, 255, 255))
        d.rectangle(b, outline=col, width=3)
        y = max(0, b[1] - 18)
        d.rectangle([max(0, b[0]), y, max(0, b[0]) + 24, y + 17], fill=col)
        d.text((max(0, b[0]) + 4, y + 2), str(k), fill=(0, 0, 0))
    return im


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[-120:]


def contact_sheet(root: Path, report: dict[str, Any], out_dir: Path, shots=()) -> None:
    """sheet/<frame>.jpg (half size, <= 300 KB) for every frame with a finding, sheet/grid.jpg of all pictures with
    their finding counts, and index.html (the per-class gate and every finding, in report order)."""
    from html import escape
    sheet_dir = out_dir / "sheet"
    sheet_dir.mkdir(exist_ok=True)
    tiles, items = [], []
    tw, th, cols = 160, 284, 8

    def tile(im):                        # each frame kept as its grid tile only (memory)
        t = im.copy()
        t.thumbnail((tw - 4, th - 4))
        return t
    for e in report["frames"]:
        img = None
        p = root / e["frame"]
        if e["kind"] in ("still", "graphic", "card") and p.is_file():
            im = marked(_load_rgb(p), e["findings"], e.get("faces"), [r["box"] for r in e.get("fields", []) if r.get("box")])
            tiles.append((tile(im), len(e["findings"])))
            if e["findings"]:
                img = _safe_name(e["frame"]) + ".jpg"
                save_jpeg(im, sheet_dir / img)
            del im
        items.append((e, img))
    for name, jpg, n_own in shots:
        with Image.open(io.BytesIO(jpg)) as im:
            im = im.convert("RGB")
            tiles.append((tile(im), n_own))
            if n_own:
                save_jpeg(im, sheet_dir / (_safe_name(name) + ".jpg"))
    if tiles:
        grid = Image.new("RGB", (cols * tw, math.ceil(len(tiles) / cols) * th), (24, 24, 24))
        gd = ImageDraw.Draw(grid)
        for k, (t, n) in enumerate(tiles):
            x, y = (k % cols) * tw + 2, (k // cols) * th + 2
            grid.paste(t, (x, y))
            if n:
                gd.rectangle([x, y, x + 26, y + 16], fill=(230, 40, 40))
                gd.text((x + 4, y + 2), str(n), fill=(255, 255, 255))
        save_jpeg(grid, sheet_dir / "grid.jpg")
    pc = report["per_class"]
    html = ["<!doctype html><meta charset='utf-8'><title>Visual critic gate</title>",
            ("<style>body{font:14px sans-serif;background:#111;color:#ddd;margin:16px}td,th{padding:4px 8px;"
            "border-bottom:1px solid #333;vertical-align:top}img{max-width:360px}.f{color:#f66}.p{color:#6c6}</style>"),
            (f"<h1>Visual critic gate: {escape(report['set'])} &mdash; "
            f"<span class='{'p' if report['gate'] == 'pass' else 'f'}'>{report['gate'].upper()}</span></h1>"),
            f"<p>{report['frame_count']} frames, {report['frames_failed']} failing. Faces: {escape(str(report['faces']))}</p>",
            "<table><tr><th>class</th><th>gate</th><th>frames failed</th><th>findings</th></tr>"
            + "".join(f"<tr><td>{escape(c)}</td><td class='{'p' if v['pass'] else 'f'}'>{'pass' if v['pass'] else 'FAIL'}"
                      f"</td><td>{v['frames_failed']}</td><td>{v['findings']}</td></tr>" for c, v in pc.items()) + "</table>",
            "<p><img src='sheet/grid.jpg' style='max-width:100%'></p><table>"]
    for e, img in items:
        if not e["findings"]:
            continue
        lis = "".join(f"<li><b>{escape(d['class'])}</b> [{escape(d['basis'])}] {escape(d['element'])}: {escape(d['detail'])}</li>"
                      for d in e["findings"])
        html.append(f"<tr><td>{escape(e['frame'])}<br>{escape(e['kind'])}</td><td>"
                    f"{f'<img src={chr(39)}sheet/{escape(img)}{chr(39)}>' if img else ''}</td><td><ol>{lis}</ol></td></tr>")
    html.append("</table>")
    (out_dir / "index.html").write_text("\n".join(html) + "\n", encoding="utf-8", newline="\n")



def _extra_copies(gray, pad, rows_at, expect, claimed, size, regions, anywhere=False) -> list[dict[str, Any]]:
    """A truth word drawn more often than the truth has it: each field's words of three or more letters searched over
    the whole frame in the field's own style; a clear, standalone copy whose letters agree, not held by any field,
    inside the frame, is an extra copy (a duplicated line, a stale layer): on a card (signed fields: the truth is
    all its text) anywhere for a word of EXTRA_ANYWHERE letters or more, else inside a recorded box that holds the
    truth's words (`regions`: a still's share line, kicker and masthead are not in its truth)."""
    out = []
    want: dict[str, int] = {}
    for fd in expect:
        for x in truth_words(str(fd.get("text") or "")):
            want[x.casefold()] = want.get(x.casefold(), 0) + 1
    full = (0, 0, gray.shape[1], gray.shape[0])
    seen: set[tuple] = set()
    for r in rows_at.values():
        st = r.get("_style")
        if st is None:
            continue
        for wd in dict.fromkeys(x["word"] for x in r["words"] if len(x["word"]) >= 3 and x.get("placed")):
            key = (st.label(), wd.casefold())
            if key in seen:
                continue
            seen.add(key)
            t = st.raster(wd)
            if t is None:
                continue
            # the field's own lines: a copy standing on one of them (an added word) counts wherever it stands
            feet = [x["box"][3] for x in r["words"] if x.get("placed")]

            def on_line(bx, feet=feet, st=st):
                return any(abs(bx[3] - y) <= 0.35 * st.px for y in feet)
            copies = []
            for c in _match_all(gray, t, full, claimed, 0.85):
                bx = (c[1][0] - pad, c[1][1] - pad, c[1][0] - pad + t.shape[1], c[1][1] - pad + t.shape[0])
                if not _inside(bx, (0, 0, size[0], size[1])):
                    continue
                if not ((anywhere and len(wd) >= EXTRA_ANYWHERE) or on_line(bx)
                        or any(_inside(bx, _grow(g, RECORD_TOL)) for g in regions)):
                    continue
                if _standalone(gray, t, c[1]) and _identity(gray, t, c[1])[0]:
                    copies.append(c)
            placed = sum(1 for rr in rows_at.values() for x in rr["words"] if x.get("placed")
                         and x["word"].casefold() == wd.casefold())
            if copies and placed + len(copies) > want.get(wd.casefold(), 0):
                v, at = copies[0]
                bx = [at[0] - pad, at[1] - pad, at[0] - pad + t.shape[1], at[1] - pad + t.shape[0]]
                out.append(finding("text_identity", r["field"], f"an extra copy of {wd!r} at {bx}: the truth has it "
                                   f"{want.get(wd.casefold(), 0)} time(s)", bx, round(v, 3), word=wd))
    return out


def _stray_ink(gray, pad, rows_at, regions) -> list[dict[str, Any]]:
    """Ink in a field's lines that is none of its words: in the gap between two words of a line (a word inserted) and
    in the em and a half after a line's last word (a word added), not under any field's words. The tones are the
    field's own: its words' glyph cores against the ground around them. (Before a line's first word is not judged:
    kickers and bullets put rules there.)"""
    import cv2
    out = []
    taken = np.zeros(gray.shape, np.uint8)
    for r in rows_at.values():
        for x in r.get("words", []):
            if x.get("placed"):
                b = x["box"]
                taken[max(0, b[1] + pad - 3):b[3] + pad + 3, max(0, b[0] + pad - 3):b[2] + pad + 3] = 1
    for r in rows_at.values():
        st = r.get("_style")
        words = [x for x in r.get("words", []) if x.get("placed")]
        if st is None or not words:
            continue
        px = st.px
        if "_tone" not in r:
            continue
        ink, ground = r["_tone"]
        if abs(ink - ground) < 2 * IDENTITY_FAINT:
            continue
        light, thr = ink > ground, (ink + ground) / 2
        lines: list[list[dict]] = []
        for x in sorted(words, key=lambda x: (x["box"][3], x["box"][0])):
            if lines and abs(lines[-1][-1]["box"][3] - x["box"][3]) <= 0.35 * px:
                lines[-1].append(x)
            else:
                lines.append([x])
        for line in lines:
            line.sort(key=lambda x: x["box"][0])
            top, bot = min(x["box"][1] for x in line), max(x["box"][3] for x in line)
            spans = [(a["box"][2] + 3, b["box"][0] - 3, a["word"], "between " + repr(a["word"]) + " and " + repr(b["word"]))
                     for a, b in itertools.pairwise(line)]
            fb0 = line[0]["box"]         # before the line's first word, inside its recorded box: a prefix ("NOT")
            own0 = [g for g in regions if _inside(fb0, _grow(g, RECORD_TOL))]
            if own0:
                spans.append((max(fb0[0] - int(1.5 * px), min(g[0] for g in own0) - RECORD_TOL), fb0[0] - 3,
                              line[0]["word"], "before " + repr(line[0]["word"])))
            lb = line[-1]["box"]
            own = [g for g in regions if _inside(lb, _grow(g, RECORD_TOL))]
            if own:                      # after the line's last word, as far as its recorded box reaches
                spans.append((lb[2] + 3, min(lb[2] + int(1.5 * px), max(g[2] for g in own) + RECORD_TOL),
                              line[-1]["word"], "after " + repr(line[-1]["word"])))
            for x0, x1, _w, where in spans:
                if x1 - x0 < 2:
                    continue
                reg = gray[top + pad:bot + pad, x0 + pad:x1 + pad]
                free = taken[top + pad:bot + pad, x0 + pad:x1 + pad] == 0
                if reg.size == 0:
                    continue
                inkish = (reg > thr) if light else (reg < thr)
                groundish = np.abs(reg.astype(np.float32) - ground) < 0.25 * abs(ink - ground)
                rest = ~inkish & free
                if rest.sum() == 0 or groundish[rest].mean() < 0.8:
                    continue             # not on the field's own ground (a plate's or band's end, the picture)
                inkish = cv2.morphologyEx((inkish & free).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                # rules, plate borders and bullets are the design's marks, not words: thin or long-and-flat blobs
                n_, lab_, st_, _c = cv2.connectedComponentsWithStats(inkish)
                for k_ in range(1, n_):
                    if st_[k_, 3] < 0.4 * px or st_[k_, 2] > 3 * st_[k_, 3]:
                        inkish[lab_ == k_] = 0
                if int(inkish.sum()) > 0.12 * px * px:
                    out.append(finding("text_identity", r["field"], f"ink that is none of the truth's words {where} "
                                       f"({int(inkish.sum())} px)", [x0, top, x1, bot]))
        # the field's own recorded box beyond its lines (a word added on a line of its own), on the field's ground
        fb_ = _union([x["box"] for x in words])
        for g in (g for g in regions if _inside(fb_, _grow(g, RECORD_TOL))):
            for y0, y1 in ((fb_[3] + 3, g[3] + RECORD_TOL), (g[1] - RECORD_TOL, fb_[1] - 3)):
                if y1 - y0 < 0.5 * px:
                    continue
                reg = gray[max(0, y0 + pad):y1 + pad, max(0, g[0] + pad):g[2] + pad]
                free = taken[max(0, y0 + pad):y1 + pad, max(0, g[0] + pad):g[2] + pad] == 0
                if reg.size == 0:
                    continue
                inkish = (reg > thr) if light else (reg < thr)
                groundish = np.abs(reg.astype(np.float32) - ground) < 0.25 * abs(ink - ground)
                rest = ~inkish & free
                if rest.sum() == 0 or groundish[rest].mean() < 0.8:
                    continue
                inkish = cv2.morphologyEx((inkish & free).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                n_, lab_, st_, _c = cv2.connectedComponentsWithStats(inkish)
                for k_ in range(1, n_):          # rules and plate borders are the design's marks
                    if st_[k_, 3] < 0.4 * px or st_[k_, 2] > 3 * st_[k_, 3]:
                        inkish[lab_ == k_] = 0
                if int(inkish.sum()) > 0.12 * px * px:
                    out.append(finding("text_identity", r["field"], f"ink that is none of the truth's words in its "
                                       f"recorded box {'below' if y0 > fb_[1] else 'above'} its lines "
                                       f"({int(inkish.sum())} px)", [g[0], y0, g[2], y1]))
    return out


# Each truth field's font role (brand.BRAND_FONTS: display for titles, headlines, card claims; text for kickers,
# labels, bodies, eyebrows): the intended family is the active brand's for that role (QF-35a)
FIELD_ROLES = {"heading": "display", "title": "display", "line1": "display", "line2": "display", "claim": "display",
               "fact": "display", "myth": "display", "place": "display", "subtitle": "text", "kicker": "text",
               "body": "text", "eyebrow": "text", "tagline": "text", "value": "display", "label": "text"}


def intended_family(field_name: str) -> str | None:
    role = FIELD_ROLES.get(field_name)
    if role is None:
        return None
    from . import brand
    return brand.active().font(role)


def _same_face(style: Style, font: str, weight: str | None = None) -> bool:
    """Whether the face found is the truth's: the same family, and the same file or named weight when the truth
    names one (a static file names its weight: IBMPlexMono-SemiBold.ttf; a variable face takes `weight`)."""
    if style.family() != _family(font):
        return False
    stem = Path(font).stem
    if "-" in stem and not stem.lower().endswith("variable") and Path(style.face).stem != stem:
        return False
    return not weight or (style.inst or "Regular") == weight


def _family(face: str) -> str:
    return Path(str(face)).stem.split("-")[0].lower().replace(" ", "")


_ALLOW_STRIKE = [False]       # the field being read is a myth card's struck claim (verify_fields sets it)
VISUAL_ORDER = ["kicker", "eyebrow", "heading", "title", "line1", "line2", "subtitle", "body", "tagline"]
IDENTITY_MISSING_MAX = 0.15     # the share of a word's glyph core the pixels may lack (JPEG, sub-pixel); letters are told apart by _glyphs_agree
IDENTITY_EXTRA_MAX = 0.15       # the share of other ink inside a word's box (JPEG ringing, a tight neighbour)
IDENTITY_FAINT = 12           # ink and ground closer than this (grey levels): the letters cannot be told
IDENTITY_EDGE_MIN = 0.5       # of each of a word's two outermost ink columns (and rows), the share of its ink present


def _identity(gray: np.ndarray, t: np.ndarray, at: tuple[int, int]) -> tuple[bool, dict[str, Any]]:
    """Whether the pixels at `at` hold exactly the shaped word `t`: its glyphs' cores are ink (no stroke missing, no
    column erased) and no ink stands outside its glyphs inside its box (no other letter: TNE for THE, TBE). NCC finds
    a word; this certifies its letters. A rule struck through the word (a myth's strike) is not counted as extra."""
    import cv2
    h, w = t.shape
    x, y = at
    m = 3
    X0, Y0 = max(0, x - m), max(0, y - m)
    X1, Y1 = min(gray.shape[1], x + w + m), min(gray.shape[0], y + h + m)
    reg = gray[Y0:Y1, X0:X1]
    if reg.size == 0:
        return False, {"why": "outside the frame"}
    M = np.zeros(reg.shape, np.uint8)
    if x < 0 or y < 0:
        return False, {"why": "outside the frame"}
    sub = (t > 0.5).astype(np.uint8)
    M[(y - Y0):(y - Y0) + h, (x - X0):(x - X0) + w] = sub[: M.shape[0] - (y - Y0), : M.shape[1] - (x - X0)]
    if M.sum() < 4:
        return False, {"why": "no glyphs"}
    core = cv2.erode(M, np.ones((3, 3), np.uint8))
    if core.sum() < 0.25 * M.sum():
        core = M
    d = max(1, round(0.035 * h))
    halo = cv2.dilate(M, np.ones((2 * d + 1, 2 * d + 1), np.uint8))
    outside = halo == 0
    if not outside.any():
        return False, {"why": "no ground"}
    ink, ground = float(np.median(reg[core > 0])), float(np.median(reg[outside]))
    if abs(ink - ground) < IDENTITY_FAINT:
        return True, {"faint": True, "missing": None, "extra": None}
    thr = (ink + ground) / 2
    inkish = reg > thr if ink > ground else reg < thr
    missing = float(((core > 0) & ~inkish).sum()) / float(core.sum())
    extra_map = inkish & outside
    rows = extra_map.sum(axis=1)
    if _ALLOW_STRIKE[0]:
        extra_map[rows > 0.6 * w] = False         # a myth card's strike across the whole word (its design)
    extra = float(extra_map.sum()) / float(M.sum())
    ok = missing <= IDENTITY_MISSING_MAX and extra <= IDENTITY_EXTRA_MAX
    info = {"missing": round(missing, 3), "extra": round(extra, 3)}
    # a glyph cut at its outer edge (a column or row of ink erased, clipped by a box): the ink coverage the word's
    # shaped glyphs give each of its outermost columns and rows, against the pixels' own coverage there, with the
    # shaped word moved by up to a pixel in quarter steps to the best fit (anti-aliasing is not a cut)
    E = np.zeros(reg.shape, np.float32)
    E[(y - Y0):(y - Y0) + h, (x - X0):(x - X0) + w] = t[: E.shape[0] - (y - Y0), : E.shape[1] - (x - X0)]
    cov = np.clip((reg - ground) / (ink - ground), 0.0, 1.0).astype(np.float32)
    near_ink = cv2.dilate((E > 0.02).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    cov = np.where(near_ink, cov, 0.0).astype(np.float32)
    shifts = (-1.0, -0.5, 0.0, 0.5, 1.0)

    def edge_ok(axis: int, last: bool) -> float:
        """The best, over shifts of up to a pixel, of the smaller coverage ratio of the two outermost lines."""
        best = 0.0
        pf = cov.sum(axis=axis)
        for dd in shifts:
            mtx = np.float32([[1, 0, dd], [0, 1, 0]]) if axis == 0 else np.float32([[1, 0, 0], [0, 1, dd]])
            Es = cv2.warpAffine(E, mtx, (E.shape[1], E.shape[0]), flags=cv2.INTER_LINEAR)
            pe = Es.sum(axis=axis)
            idx = np.nonzero(pe > 0.15 * pe.max())[0]
            idx = idx[-2:] if last else idx[:2]
            if idx.size:
                best = max(best, float(min(pf[i] / pe[i] for i in idx)))
        return best

    for name, axis, last in (("top", 1, False), ("bottom", 1, True)):   # left and right: per glyph (_glyphs_agree)
        ye = at[1] + (t.shape[0] if last else 0)
        if _EDGES_Y and not any(abs(ye - e) <= max(4, t.shape[0] // 2) for e in _EDGES_Y):
            continue                     # judged where a cut can fall: near the frame's or its zone's top or foot
        r = edge_ok(axis, last)
        if r < IDENTITY_EDGE_MIN:
            ok = False
            info["why"] = f"its {name} edge is cut: {round(100 * (1 - r))}% of an outermost line of its ink is missing"
    return ok, info


def _follows(prev: tuple[int, int, int], at: tuple[int, int], t: np.ndarray, base: int, px: int) -> bool:
    """Reading order: a word stands after the previous one on its line (at most 1.5 em on), or begins a later line
    (its baseline 0.6 to 2.6 em below, the larger of the two words' sizes)."""
    px0, by0, ppx = prev
    by = at[1] + base
    unit = max(px, ppx)
    dy = by - by0
    if abs(dy) <= 0.35 * unit:
        return at[0] >= px0 - 0.1 * unit and at[0] - px0 <= 1.5 * unit
    return 0.6 * unit <= dy <= 2.6 * unit


def _read_field(gray, style, words, text, near, full, taken, anchor_at, first, figure_words, hints, pad, lh):
    """One reading of a field: every word placed in order where the previous word leaves off; the first word at its
    `first`-nearest place to the anchor. (word rows, found, the ink they take, no further first place to try)."""
    rows_w: list[dict[str, Any]] = []
    got: list[tuple] = []
    held_ink = list(taken)
    prev = None
    exhausted = False
    for k, wd in enumerate(words):
        t = style.raster(wd)
        if t is None:
            rows_w.append({"word": wd, "ncc": None, "why": "not drawable in the field's face"})
            continue
        base = style.baseline(wd)
        sides = _free_sides(text, wd)
        cands = [c for c in _match_all(gray, t, near, held_ink, WORD_MIN) if _standalone(gray, t, c[1], sides)]
        if prev is not None:
            cands = [c for c in cands if _follows(prev, c[1], t, base, style.px)]
        else:
            cands.sort(key=lambda c: ((c[1][0] - anchor_at[0]) ** 2 + (c[1][1] - anchor_at[1]) ** 2, -c[0]))
            exhausted = first >= len(cands) - 1
            cands = cands[first:first + 1] + cands[:first] + cands[first + 1:]
            cands = cands[:1] + sorted(cands[1:], key=lambda c: -c[0])
        if prev is not None:             # the next place in reading order first: on the line, then the next line
            cands.sort(key=lambda c: (0, c[1][0]) if abs(c[1][1] + base - prev[1]) <= 0.35 * max(style.px, prev[2])
                       else (1, c[1][1], c[1][0]))
        chosen = None
        tried = None
        for c in cands[:6]:
            ok, info = _identity(gray, t, c[1])
            if ok and not info.get("faint"):
                ok, why = _glyphs_agree(gray, style, wd, c[1], tuple(held_ink))
                if not ok:
                    info = {**info, "why": why}
            tried = tried or (c, info)
            if ok:
                chosen = (c[0], c[1], t, info, base, style.px, style.label())
                break
        if chosen is None and wd in figure_words:       # a figure and its unit may be set in a style of their own
            chosen = _figure_word(gray, wd, near, held_ink, hints, pad, lh, prev)
        if chosen is None:
            if tried is not None:
                (v, at), info = tried
                bx = (at[0] - pad, at[1] - pad, at[0] - pad + t.shape[1], at[1] - pad + t.shape[0])
                why = info.get("why") or (
                    f"the letters differ where it stands: {round((info.get('missing') or 0) * 100)}% of its glyphs "
                    f"missing, {round((info.get('extra') or 0) * 100)}% other ink")
                rows_w.append({"word": wd, "ncc": round(v, 3), "box": list(bx), "why": why})
            else:
                v, at = _match(gray, t, near, held_ink)
                bx = (at[0] - pad, at[1] - pad, at[0] - pad + t.shape[1], at[1] - pad + t.shape[0])
                rows_w.append({"word": wd, "ncc": round(v, 3), "box": list(bx),
                               "why": f"best match {round(v, 3)}" + (", not where the reading order puts it"
                                                                     if prev is not None else "")})
            continue
        v, at, tt, info, b2, wpx, wlab = chosen
        bx = (at[0] - pad, at[1] - pad, at[0] - pad + tt.shape[1], at[1] - pad + tt.shape[0])
        rows_w.append({"word": wd, "ncc": round(v, 3), "box": list(bx), "placed": True, "style": wlab, **{
            kk: vv for kk, vv in info.items() if kk in ("missing", "extra", "faint")}})
        got.append((wd, bx, tt, (at[0] - pad, at[1] - pad)))
        held_ink.append((at[0], at[1], at[0] + tt.shape[1], at[1] + tt.shape[0]))
        prev = (at[0] + tt.shape[1], at[1] + b2, wpx)
    return rows_w, got, held_ink, exhausted


def _figure_word(gray, wd, near, held_ink, hints, pad, lh, prev):
    """A figure word searched on its own in the record boxes nearest the previous word, then in the whole band; in
    reading order and with its letters certified. (ncc, place, mask, identity, baseline, px) or None."""
    last = (prev[0], prev[1]) if prev else (near[0], near[1])
    boxes = sorted((e for e in hints if _intersects(_grow(_box(e["box"]), pad), (near[0] - pad, near[1] - pad,
                                                                               near[2] - pad, near[3] - pad))),
                   key=lambda e: _dist(_grow(_box(e["box"]), pad), (last[0], last[1], last[0], last[1])))
    spots = [((_box(e["box"])[0] - 24 + pad, _box(e["box"])[1] - 24 + pad, _box(e["box"])[2] + 24 + pad,
               _box(e["box"])[3] + 24 + pad), float(max(8, _box(e["box"])[3] - _box(e["box"])[1])),
              _face_of(e)[0], float(e.get("px") or 0)) for e in boxes]
    spots.append((near, float(3 * lh), "", 0.0))
    for sw, sh, sf, sp in spots:
        v3, st3, at3 = calibrate(gray, wd, sw, sh, hint_face=sf, hint_px=sp, exclude=tuple(held_ink))
        t3 = st3.raster(wd) if st3 is not None else None
        if t3 is not None and v3 >= STYLE_MIN:
            b3 = st3.baseline(wd)
            if prev is None or _follows(prev, at3, t3, b3, st3.px):
                ok, info = _identity(gray, t3, at3)
                if ok and _glyphs_agree(gray, st3, wd, at3, tuple(held_ink))[0]:
                    return v3, at3, t3, info, b3, st3.px, st3.label()
        if wd.isdigit() and len(wd) > 1:                # digit by digit (a count-up's slots)
            hit = _digits(gray, wd, sw, sh, tuple(held_ink))
            if hit is not None:
                v, at, m, _lab = hit
                ok, info = _identity(gray, m, at)
                b = m.shape[0]
                if ok and (prev is None or _follows(prev, at, m, b, max(prev[2], m.shape[0]))):
                    return v, at, m, info, b, m.shape[0], hit[3]
    return None


STYLE_UNCERTAIN = 0.85        # a field whose placed words fit their drawing under this (median) is not certified
STYLE_CERTIFY = 0.92          # a field whose placed words fit their drawing less than this (median) is not certified
GLYPH_EXPLAINED = 0.5         # and only when the variant itself leaves no more than this unexplained
GLYPH_UNEXPLAINED = 0.25      # a variant wins only where the truth leaves this much of the difference unexplained
EXTRA_ANYWHERE = 5            # a truth word this long found again anywhere in the frame is an extra copy
EDGE_LOST = 0.08              # ink a word's drawing puts past a cut edge it touches, in its outer columns' ink
_EDGES_Y: list[int] = []      # and its top and foot edges
_EDGES: list[int] = []        # the frame's cut edges (verify_fields sets them per frame; empty: every word is judged)
GLYPH_MARGIN = 0.02           # the truth's letter must match its place better than every other letter by this much
GLYPH_PHASES = (0.25, 0.5, 0.75)   # sub-pixel origins a variant is also drawn at (a line's glyphs are not on whole px)
GLYPH_MIN = 0.5               # and match its place at least this well (a glyph that is not drawn at all)
GLYPH_SAME_PX = 3             # two drawings differing in fewer pixels than this are the same glyph (round 3: a Q's tail
#                               is a difference however alike the rest; correlation never decides identity)
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_DIGIT = "0123456789"


@lru_cache(maxsize=4096)
def _glyph(face: str, inst: str | None, px: int, variant: str, ch: str, ox: float):
    """One letter drawn alone at its place in a word (origin `ox` px from the word image's left, sub-pixel kept):
    (mask, left, top) in the word image's coordinates, or None."""
    font = _font(face, inst, px)
    feats = ["smcp"] if variant == "smcp" else None
    w, h = int(px * 2 + ox + px), int(px * 2.2)
    im = Image.new("L", (w, h), 0)
    ImageDraw.Draw(im).text((px // 2 + ox, int(px * 1.6)), ch, font=font, fill=255, anchor="ls", features=feats)
    bb = im.getbbox()
    if bb is None:
        return None
    return np.asarray(im.crop(bb), np.float32) / 255.0, bb[0], bb[1]


def _ncc_best(reg: np.ndarray, tmpl: np.ndarray) -> float:
    """The best |NCC| of `tmpl` in `reg` (light or dark ink alike)."""
    import cv2
    r = np.nan_to_num(cv2.matchTemplate(reg, tmpl, cv2.TM_CCOEFF_NORMED), nan=0.0)
    return float(max(r.max(), -r.min()))


_PUNCT = ".,;:!?'’‘\"“”-‐‑–—()[]/&%°+×#*@…"
# typographic forms of one sign (a renderer's typesetting may set either): not told apart
_SAME_SIGN = ("'’‘ʼ", "\"“”", "-‐‑‒–—−")


def _base(ch: str) -> str:
    return unicodedata.normalize("NFD", ch)[0]


@lru_cache(maxsize=256)
def _accented(base: str) -> str:
    """Every precomposed Latin letter (U+00C0-U+024F) on `base`."""
    return "".join(c for c in map(chr, range(0xC0, 0x250)) if c.isalpha() and _base(c) == base and c != base)


def _alternatives(ch: str) -> str:
    """The characters a drawn glyph could be instead of `ch`: every letter of its case (with its accented forms, and
    the accented forms of `ch`'s own base letter), the digits, the punctuation signs; an absent glyph is caught by
    GLYPH_MIN."""
    if ch in _DIGIT:
        return _DIGIT + _UPPER
    if ch.isalpha():
        base = _base(ch)
        pool = (_UPPER + _DIGIT) if ch.isupper() else _LOWER
        return "".join(dict.fromkeys(pool + base + _accented(base)))
    if ch in _PUNCT or unicodedata.category(ch).startswith("P"):
        return _PUNCT
    return ""


def _same_sign(a: str, b: str) -> bool:
    return any(a in g and b in g for g in _SAME_SIGN)


@lru_cache(maxsize=256)
def _word_image(text: str, face: str, inst: str | None, px: int, track: float, variant: str, phase: float = 0.0):
    """`text` drawn as the word templates are (same origin, kerning, tracking), uncropped, its origin `phase` px to
    the right (glyphs in a drawn line sit at sub-pixel places): (float image, advances)."""
    font = _font(face, inst, px)
    feats = ["smcp"] if variant == "smcp" else None
    adv = [font.getlength(text[:i], features=feats) + i * track * px for i in range(len(text) + 1)]
    im = Image.new("L", (int(adv[-1] + px * 3), int(px * 2.2)), 0)
    d = ImageDraw.Draw(im)
    if track == 0:
        d.text((px // 2 + phase, int(px * 1.6)), text, font=font, fill=255, anchor="ls", features=feats)
    else:
        for i, ch in enumerate(text):
            d.text((px // 2 + adv[i] + phase, int(px * 1.6)), ch, font=font, fill=255, anchor="ls", features=feats)
    return np.asarray(im, np.float32) / 255.0, tuple(adv)


def _variants(text: str, i: int) -> list[tuple[str, str]]:
    """The one-edit variants of `text` at position `i`: each other character of its kind put there, the character
    dropped, and it swapped with the next one. (label, variant)."""
    ch = text[i]
    out = [(f"{a!r} for {ch!r}", text[:i] + a + text[i + 1:]) for a in _alternatives(ch) if a != ch and not _same_sign(a, ch)]
    out.append((f"{ch!r} dropped", text[:i] + text[i + 1:]))
    if i + 1 < len(text) and text[i + 1] != ch:
        out.append((f"{ch!r} and {text[i + 1]!r} swapped", text[:i] + text[i + 1] + ch + text[i + 2:]))
    return out


def _glyphs_agree(gray: np.ndarray, style: Style, word: str, at: tuple[int, int],
                  others: tuple = ()) -> tuple[bool, str]:
    """The truth's word against each of its one-edit variants (every other character of its kind at each place,
    each character dropped, each pair swapped), all drawn in the same face, size and tracking from the same origin:
    over the stretch around the edited place (its neighbours included, so kerning and spacing count), the pixels
    must fit the truth better than every variant by GLYPH_MARGIN (TNE is not THE, 321 is not 327, KRAKOW is not
    KRAKOW with its accent, WHISPREING is not WHISPERING, 137138 is not 137-138). Each character must also be there
    (GLYPH_MIN over its own box). A variant the face draws like the truth (differing in under GLYPH_SAME_PX pixels: O and 0
    in some faces) and typographic forms of one sign are not told apart. The first and last characters' outer
    edges must be whole."""
    import cv2
    text = word.upper() if style.variant == "upper" else word
    key = (style.face, style.inst, style.px, style.track, style.variant)
    base, adv = _word_image(text, *key)
    ys, xs = np.nonzero(base > 0.02)
    if xs.size == 0:
        return False, "not drawable"
    bx0, by0 = int(xs.min()), int(ys.min())
    # the template's top-left (the cropped ink box) sits at `at` in the frame
    ox, oy = at[0] - bx0, at[1] - by0
    m = 2

    fit = _word_fit(gray, text, key, ox, oy)
    fit_img = _word_image(text, *key, fit[0])[0] if fit is not None else None

    def frame_crop(x0, x1, y0, y1):
        return gray[max(0, oy + y0 - m):oy + y1 + m, max(0, ox + x0 - m):ox + x1 + m]

    H = base.shape[0]
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        own = _glyph(style.face, style.inst, style.px, style.variant, ch, round(adv[i], 2))
        if own is not None:
            gm, gx, gy = own
            reg = frame_crop(gx, gx + gm.shape[1], gy, gy + gm.shape[0])
            if reg.shape[0] < gm.shape[0] + 2 * m or reg.shape[1] < gm.shape[1] + 2 * m:
                return False, f"character {i + 1} ({ch!r}) outside the frame"
            if fit is not None:          # its core carries the word's ink (a dash, an apostrophe, a dot alike)
                # the character as the word draws it (a word shapes in context: a hyphen between capitals is raised)
                c0 = int(style.px // 2 + adv[i] + fit[0])
                c1 = max(c0 + 1, int(style.px // 2 + adv[i + 1] + fit[0]))
                gm = fit_img[:, c0:c1]
                yy, xx = oy + fit[1], ox + fit[2] + c0
                core = gm > 0.7
                here = 0.0
                # a sign may sit higher or lower than this drawing puts it (case-sensitive forms raise a hyphen
                # between capitals): searched a quarter em up and down; a letter or digit where it is drawn
                reach = style.px // 4 if not ch.isalnum() else 0
                for dyy in range(-reach, reach + 1):
                    pr = gray[max(0, yy + dyy):yy + dyy + gm.shape[0], max(0, xx):xx + gm.shape[1]].astype(np.float32)
                    if core.any() and pr.shape == gm.shape:
                        here = max(here, float((np.abs(pr - (fit[3] + fit[4])) < np.abs(pr - fit[3]))[core].mean()))
                if core.any() and here < GLYPH_MIN:
                    return False, f"character {i + 1} ({ch!r}) is not there ({round(100 * here)}% of its core is ink)"
            else:
                here = _ncc_best(reg, gm) if gm.std() > 1e-3 else 1.0
                if here < GLYPH_MIN:
                    return False, f"character {i + 1} ({ch!r}) is not there (it matches {here:.2f})"
        lo = int(adv[max(0, i - 1)]) + style.px // 2 - 2
        for label, var in _variants(text, i):
            vimg, vadv = _word_image(var, *key)
            hi = int(max(adv[min(len(text), i + 2)], vadv[min(len(var), i + 2)])) + style.px // 2 + 2
            w_ = max(base.shape[1], vimg.shape[1])
            a_ = np.zeros((H, w_), np.float32)
            b_ = np.zeros((H, w_), np.float32)
            a_[:, :base.shape[1]] = base
            b_[:, :vimg.shape[1]] = vimg
            a_c, b_c = a_[:, lo:hi], b_[:, lo:hi]
            rows = np.nonzero((a_c + b_c).sum(axis=1) > 0.02)[0]
            if rows.size == 0 or a_c.std() < 1e-3 or b_c.std() < 1e-3:
                continue
            r0, r1 = int(rows.min()), int(rows.max()) + 1
            a_c, b_c = a_c[r0:r1], b_c[r0:r1]
            dy, dx = np.nonzero(np.abs(a_c - b_c) > 0.1)    # judged where the two differ, with some context
            if dx.size == 0:
                continue
            k = max(3, style.px // 10)
            y0, y1 = max(0, int(dy.min()) - k), min(a_c.shape[0], int(dy.max()) + k + 1)
            x0, x1 = max(0, int(dx.min()) - k), min(a_c.shape[1], int(dx.max()) + k + 1)
            da, db = a_c[y0:y1, x0:x1], b_c[y0:y1, x0:x1]
            if da.std() < 1e-3 or db.std() < 1e-3:
                continue
            if int((np.abs(a_c - b_c) > 0.5).sum()) < GLYPH_SAME_PX:
                continue                      # drawn the same in this face (O and 0 in some): no pixel tells them
            del da, db
            if fit is None:
                return False, "its pixels do not fit its drawing"
            ph, fy, fx, fa, fb = fit
            ta = _word_image(text, *key, ph)[0]
            tb = _word_image(var, *key, ph)[0]
            w2 = max(ta.shape[1], tb.shape[1], hi)
            za = np.zeros((H, w2), np.float32)
            zb = np.zeros((H, w2), np.float32)
            za[:, :ta.shape[1]] = ta
            zb[:, :tb.shape[1]] = tb
            sa, sb = za[r0:r1, lo:hi], zb[r0:r1, lo:hi]
            y_, x_ = oy + fy + r0, ox + fx + lo
            if y_ < 0 or x_ < 0 or y_ + sa.shape[0] > gray.shape[0] or x_ + sa.shape[1] > gray.shape[1]:
                continue
            r = gray[y_:y_ + sa.shape[0], x_:x_ + sa.shape[1]].astype(np.float32)
            diff = cv2.dilate((np.abs(sa - sb) > 0.1).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            for b_ in others:            # other words' ink (a line set tight above or below) is not this word's
                yy0, yy1 = max(0, b_[1] - y_), min(diff.shape[0], b_[3] - y_)
                xx0, xx1 = max(0, b_[0] - x_), min(diff.shape[1], b_[2] - x_)
                if yy1 > yy0 and xx1 > xx0:
                    diff[yy0:yy1, xx0:xx1] = False
            if not diff.any():
                continue
            rows_t = np.nonzero((sa > 0.1).any(axis=1))[0]
            if rows_t.size:
                # a mark the variant adds above or below the word's own ink (an accent, a cedilla) is judged only on
                # a free ground there: a line set tight above or below puts its own ink where the mark would be
                outside = np.zeros(diff.shape, bool)
                outside[:rows_t.min()] = True
                outside[rows_t.max() + 1:] = True
                out_d = diff & outside
                if out_d.any():
                    dys = np.nonzero(out_d.any(axis=1))[0]
                    cols = np.nonzero(out_d.any(axis=0))[0]
                    band = r[dys.min():dys.max() + 1]
                    keep = np.ones(band.shape[1], bool)
                    keep[max(0, cols.min() - 2):cols.max() + 3] = False
                    inkish = np.abs(band - (fa + fb)) < np.abs(band - fa)
                    rough = (float(band[:, keep].std()) > 0.05 * abs(fb)          # a plate: plain, and the ground's
                             or abs(float(np.median(band[:, keep])) - fa) > 0.25 * abs(fb)) if keep.any() else True
                    accent = var != text and len(var) == len(text) and all(
                        _base(p_) == _base(q_) for p_, q_ in zip(var, text))       # the two differ by a mark only
                    if rough or (keep.any() and inkish[:, keep].mean() > 0.03):   # not a plain ground there
                        if accent:
                            continue
                        diff = diff & ~outside
                        if not diff.any():
                            continue
                    elif accent:                 # judged on the mark alone (a mark's letter may be spaced apart)
                        diff = out_d
            denom = float(((fb * (sa - sb))[diff] ** 2).sum())
            if denom < 1e-6:
                continue
            mine = float(((r - (fa + fb * sa))[diff] ** 2).sum()) / denom
            theirs = float(((r - (fa + fb * sb))[diff] ** 2).sum()) / denom
            if theirs <= mine or mine >= GLYPH_UNEXPLAINED:   # unsure: each drawing at its own best place here
                def local(word_, w2=w2, r0=r0, r1=r1, lo=lo, hi=hi, y_=y_, x_=x_, diff=diff, denom=denom, fa=fa, fb=fb):
                    best_e = math.inf
                    for ph2 in (0.0,) + GLYPH_PHASES:
                        im2 = _word_image(word_, *key, ph2)[0]
                        z = np.zeros((H, w2), np.float32)
                        z[:, :min(w2, im2.shape[1])] = im2[:, :w2]
                        t2 = z[r0:r1, lo:hi]
                        for ddy in (-1, 0, 1):
                            for ddx in (-3, -2, -1, 0, 1, 2, 3):
                                yy, xx = y_ + ddy, x_ + ddx
                                if yy < 0 or xx < 0 or yy + t2.shape[0] > gray.shape[0] or xx + t2.shape[1] > gray.shape[1]:
                                    continue
                                rr = gray[yy:yy + t2.shape[0], xx:xx + t2.shape[1]].astype(np.float32)
                                best_e = min(best_e, float(((rr - (fa + fb * t2))[diff] ** 2).sum()) / denom)
                    return best_e
                mine, theirs = min(mine, local(text)), local(var)
            if (theirs <= mine and mine >= GLYPH_UNEXPLAINED and theirs <= GLYPH_EXPLAINED
                    and _stretch_prefers(gray, sa, sb, y_, x_)):     # else not told apart
                return False, (f"the pixels read {label}: it leaves {theirs:.2f} of the difference unexplained, the truth "
                               f"{mine:.2f}")
        if i in (0, len(text) - 1) and fit_img is not None:    # the word's outer edges whole (as the word draws them)
            ys2, xs2 = np.nonzero(fit_img > 0.02)
            wy0, wx0 = int(ys2.min()), int(xs2.min())
            crop = fit_img[wy0:int(ys2.max()) + 1, wx0:int(xs2.max()) + 1]
            x_left, x_right = ox + fit[2] + wx0, ox + fit[2] + wx0 + crop.shape[1]
            sides = (("left",) if i == 0 else ()) + (("right",) if i == len(text) - 1 else ())
            # judged where a cut can fall: within an em of the frame's or its zone's edge
            sides = tuple(sd for sd in sides if not _EDGES or any(
                abs((x_left if sd == "left" else x_right) - e) <= style.px for e in _EDGES))
            # ink that runs solid up to a cut edge was cut there (a glyph's own edge fades over its last column)
            rows = gray[oy + fit[1] + wy0:oy + fit[1] + wy0 + crop.shape[0]].astype(np.float32)
            cov = np.clip((rows - fit[3]) / fit[4], 0.0, 1.0).sum(axis=0)
            for side in sides:
                xe = x_left if side == "left" else x_right
                lo_, hi_ = max(0, xe - style.px // 3), min(cov.size, xe + style.px // 3)
                seg = cov[lo_:hi_]
                if seg.size == 0 or seg.max() <= 0:
                    continue
                inked = np.nonzero(seg > 0.15 * seg.max())[0]
                if inked.size == 0:
                    continue
                last = lo_ + (int(inked.max()) if side == "right" else int(inked.min()))
                edge_hit = [e for e in _EDGES if (side == "right" and 0 <= e - (last + 1) <= 1)
                            or (side == "left" and 0 <= last - e <= 1)]
                if edge_hit:             # the drawing puts ink beyond that edge the pixels do not have
                    tcol = np.zeros(cov.size, np.float32)
                    tc_ = fit_img[wy0:wy0 + crop.shape[0]].sum(axis=0)
                    x0t = ox + fit[2]                       # the word image's column 0 in the frame
                    for xx in range(cov.size):
                        if 0 <= xx - x0t < tc_.size:
                            tcol[xx] = tc_[xx - x0t]
                    k_ = style.px // 3
                    span = slice(last + 1, last + 1 + k_) if side == "right" else slice(max(0, last - k_), last)
                    lost = float(np.clip(tcol[span] - cov[span], 0, None).sum())   # drawn ink the pixels lack
                    near_ = tcol[max(0, last - 2):last + 1] if side == "right" else tcol[last:last + 3]
                    tmax = float(near_.max()) if near_.size else 0.0      # the glyph's outer columns' ink
                    if tmax > 0 and lost >= max(2.0, EDGE_LOST * tmax):
                        return False, (f"its {side} edge is cut at x {edge_hit[0] - _EDGES[0]}: {lost:.0f} px of the "
                                       f"glyph's ink past it are not drawn")
            if i == 0 and _EDGES_Y:      # the same at a top or foot edge (a row of ink cut away)
                cols = gray[:, ox + fit[2] + wx0:ox + fit[2] + wx0 + crop.shape[1]].astype(np.float32)
                rcov = np.clip((cols - fit[3]) / fit[4], 0.0, 1.0).sum(axis=1)
                y_top, y_bot = oy + fit[1] + wy0, oy + fit[1] + wy0 + crop.shape[0]
                trow = np.zeros(rcov.size, np.float32)
                tr_ = fit_img[:, wx0:wx0 + crop.shape[1]].sum(axis=1)
                for yy in range(rcov.size):
                    if 0 <= yy - (oy + fit[1]) < tr_.size:
                        trow[yy] = tr_[yy - (oy + fit[1])]
                for vside, ye in (("top", y_top), ("bottom", y_bot)):
                    if not any(abs(ye - e) <= style.px for e in _EDGES_Y):
                        continue
                    lo_, hi_ = max(0, ye - style.px // 3), min(rcov.size, ye + style.px // 3)
                    seg = rcov[lo_:hi_]
                    inked = np.nonzero(seg > 0.15 * seg.max())[0] if seg.size and seg.max() > 0 else np.zeros(0, int)
                    if inked.size == 0:
                        continue
                    last = lo_ + (int(inked.max()) if vside == "bottom" else int(inked.min()))
                    hit = [e for e in _EDGES_Y if (vside == "bottom" and 0 <= e - (last + 1) <= 1)
                           or (vside == "top" and 0 <= last - e <= 1)]
                    if not hit:
                        continue
                    k_ = style.px // 3
                    span = slice(last + 1, last + 1 + k_) if vside == "bottom" else slice(max(0, last - k_), last)
                    lost = float(np.clip(trow[span] - rcov[span], 0, None).sum())
                    near_ = trow[max(0, last - 2):last + 1] if vside == "bottom" else trow[last:last + 3]
                    tmax = float(near_.max()) if near_.size else 0.0
                    if tmax > 0 and lost >= max(2.0, EDGE_LOST * tmax):
                        return False, (f"its {vside} edge is cut at y {hit[0] - _EDGES_Y[0]}: {lost:.0f} px of the "
                                       f"glyphs' ink past it are not drawn")
            for side in sides:           # the outermost character drawn alone, placed on its own
                if own is None:
                    continue
                g3, gx3, gy3 = own
                r = _glyph_edge(gray, g3, ox + fit[2] + gx3, oy + fit[1] + gy3, side)
                # a variable face's neighbouring weights (the style search settles on a named instance; the
                # renderer's weight may lie between two): the edge is whole when one of them finds it whole
                for inst2 in (_neighbour_weights(style.face, style.inst) if r is not None and r < IDENTITY_EDGE_MIN else ()):
                    o2 = _glyph(style.face, inst2, style.px, style.variant, ch, round(adv[i], 2))
                    if o2 is not None:
                        r2 = _glyph_edge(gray, o2[0], ox + fit[2] + o2[1], oy + fit[1] + o2[2], side)
                        r = max(r, r2) if r2 is not None else r
                if r is not None and r < IDENTITY_EDGE_MIN:
                    return False, f"its {side} edge is cut: {round(100 * (1 - r))}% of an outermost column of ink is missing"
    return True, ""


def _stretch_prefers(gray: np.ndarray, sa: np.ndarray, sb: np.ndarray, y_: int, x_: int) -> bool:
    """Whether the variant's stretch (the changed letter and its neighbours) matches the pixels there better than the
    truth's (NCC, each at its best place within 3 px): a letter-level win must hold for the letters about it."""
    m = 3
    reg = gray[max(0, y_ - m):y_ + sa.shape[0] + m, max(0, x_ - m):x_ + sa.shape[1] + m].astype(np.float32)
    if reg.shape[0] < sa.shape[0] or reg.shape[1] < sa.shape[1] or sa.std() < 1e-3 or sb.std() < 1e-3:
        return False
    return _ncc_best(reg, sb.astype(np.float32)) > _ncc_best(reg, sa.astype(np.float32))


def _word_fit(gray: np.ndarray, text: str, key: tuple, ox: int, oy: int):
    """The drawing of `text` that best explains its pixels: its sub-pixel origin (0 or GLYPH_PHASES), whole-pixel
    shift (within 2 px of `ox`, `oy`, the word image's origin in the frame) and the ink and ground tones fitted by
    least squares. (phase, dy, dx, ground, ink - ground) or None."""
    best = (math.inf, None)
    for ph in (0.0,) + GLYPH_PHASES:
        t = _word_image(text, *key, ph)[0]
        ys, xs = np.nonzero(t > 0.02)
        if xs.size == 0:
            continue
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
        tc = t[y0:y1, x0:x1].ravel()
        tm = float(tc.mean())
        tz = tc - tm
        tv = float(tz @ tz)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                yy, xx = oy + dy + y0, ox + dx + x0
                if yy < 0 or xx < 0 or yy + (y1 - y0) > gray.shape[0] or xx + (x1 - x0) > gray.shape[1]:
                    continue
                r = gray[yy:yy + (y1 - y0), xx:xx + (x1 - x0)].astype(np.float32).ravel()
                b = float(tz @ (r - r.mean())) / tv
                a = float(r.mean()) - b * tm
                e = r - (a + b * tc)
                sse = float(e @ e)
                if sse < best[0]:
                    best = (sse, (ph, dy, dx, a, b))
    fit = best[1]
    return fit if fit is not None and abs(fit[4]) > 1e-3 else None


def _glyph_edge(gray: np.ndarray, gm: np.ndarray, x0: int, y0: int, side: str) -> float | None:
    """One letter placed on its own (to the pixel, then by quarter pixels), the ink its two outermost columns on
    `side` should hold against the ink the pixels hold there: the smaller ratio of the two. None when it cannot be
    told (no ground, no contrast)."""
    import cv2
    m = 3
    hh, ww = gm.shape
    reg = gray[max(0, y0 - m):y0 + hh + m, max(0, x0 - m):x0 + ww + m]
    if reg.shape[0] < hh + 2 * m or reg.shape[1] < ww + 2 * m:
        return None
    # placed by its other side only (the side away from the edge judged), so a cut cannot pull the fit over itself
    cut = max(1, int(0.6 * ww))
    part, off = (gm[:, :cut], 0) if side == "right" else (gm[:, ww - cut:], ww - cut)
    if part.std() < 1e-3:
        return None
    r = np.nan_to_num(cv2.matchTemplate(reg, part, cv2.TM_CCOEFF_NORMED), nan=0.0)
    r = np.maximum(r, -r)
    oy, ox = np.unravel_index(int(np.argmax(r)), r.shape)
    ox -= off
    if ox < 0 or ox + ww > reg.shape[1]:
        return None
    E = np.zeros(reg.shape, np.float32)
    E[oy:oy + hh, ox:ox + ww] = gm
    core = cv2.erode((E > 0.5).astype(np.uint8), np.ones((3, 3), np.uint8))
    halo = cv2.dilate((E > 0.02).astype(np.uint8), np.ones((5, 5), np.uint8))
    if core.sum() < 4 or (halo == 0).sum() < 4:
        return None
    ink, ground = float(np.median(reg[core > 0])), float(np.median(reg[halo == 0]))
    if abs(ink - ground) < IDENTITY_FAINT:
        return None
    cov = np.clip((reg - ground) / (ink - ground), 0.0, 1.0).astype(np.float32)
    cov = np.where(halo > 0, cov, 0.0)
    pf = cov.sum(axis=0)
    best = 0.0
    for dd in (-0.5, -0.25, 0.0, 0.25, 0.5):
        Es = cv2.warpAffine(E, np.float32([[1, 0, dd], [0, 1, 0]]), (E.shape[1], E.shape[0]), flags=cv2.INTER_LINEAR)
        pe = Es.sum(axis=0)
        idx = np.nonzero(pe > 0.15 * pe.max())[0]
        idx = idx[-2:] if side == "right" else idx[:2]
        if idx.size:
            best = max(best, float(min(pf[i] / pe[i] for i in idx)))
    return best


CAPTION_TIMING_TOL = 0.30     # s a spoken word may fall outside the time its caption line is on screen


CAPTION_MIN_SHOW = 0.10       # s a caption line must stay on screen (a 10 ms flash is not a caption)
CAPTION_MERGE_GAP = 0.30      # s: a line's repeated events (its words lighting up) join only when this close in time


def _caption_lines(ass: AssFile, styles: set[str]) -> list[dict[str, Any]]:
    """The caption lines of `styles` (every text style but the badge) in the order they first appear: an event
    repeats its line while the line's words light up one by one (a two-line burst's lines take turns), so a line is
    its position and words, its time the span of its events; a repeat joins its line only when it follows within
    CAPTION_MERGE_GAP (round 3: a line said again later is shown again, and is a line again)."""
    lines: list[dict[str, Any]] = []
    for e in sorted((e for e in ass.events if e.style in styles and not e.drawing and not e.halo and e.plain
                     and _event_visible(ass, e)),
                    key=lambda e: (e.start, e.index)):
        key = (e.style, _pos_of(e), tuple(_norm_words(e.plain)))
        gi = next((g for g in range(len(lines) - 1, max(-1, len(lines) - 7), -1) if lines[g]["key"] == key
                   and e.start <= lines[g]["t1"] + CAPTION_MERGE_GAP), None)
        if gi is None:
            lines.append({"key": key, "words": list(key[2]), "t0": e.start, "t1": e.end, "style": e.style})
        else:
            lines[gi]["t0"] = min(lines[gi]["t0"], e.start)
            lines[gi]["t1"] = max(lines[gi]["t1"], e.end)
    return lines


def caption_truth_findings(ass: AssFile, words: list[dict[str, Any]], until: float | None = None) -> list[dict[str, Any]]:
    """The captions against the narration they must show: every spoken word, in order, once (nothing missing, nothing
    added, nothing changed) over every caption style, each on screen while it is spoken (CAPTION_TIMING_TOL) and
    for at least CAPTION_MIN_SHOW. Words spoken after `until` (an end card's start) are not owed a caption, and the
    captions shown after it are not counted."""
    out: list[dict[str, Any]] = []
    spoken = [(tok.casefold(), float(wd.get("start") or 0.0)) for wd in words for tok in tokens(str(wd.get("word", "")))]
    if until is not None:
        spoken = [s for s in spoken if s[1] < until - 0.1]
    styles = {e.style for e in ass.events if not e.drawing and not e.halo and e.plain and e.style != "ReconBadge"}
    if not spoken:
        out.append(finding("coverage", "captions", "the narration has no words to check the captions against",
                           basis="truth"))
        return out
    if not styles:
        out.append(finding("text_identity", "captions", f"no caption text at all for {len(spoken)} spoken words",
                           basis="pixels+truth"))
        return out
    lines = _caption_lines(ass, styles)
    if until is not None:
        lines = [ln for ln in lines if ln["t0"] < until - 0.1]
    style0 = "captions"
    shown = [(wd, ln) for ln in lines for wd in ln["words"]]
    said = [s[0] for s in spoken]
    seen = [s[0] for s in shown]
    if seen != said:
        k = next((i for i in range(min(len(seen), len(said))) if seen[i] != said[i]), min(len(seen), len(said)))
        cap = seen[k] if k < len(seen) else "(nothing)"
        nar = said[k] if k < len(said) else "(nothing)"
        out.append(finding("text_identity", style0, f"the captions differ from the narration at word {k + 1}: the caption "
                           f"shows {cap!r}, the narration says {nar!r} ({len(seen)} caption words, {len(said)} spoken)",
                           basis="pixels+truth", word=nar))
        return out
    short = [ln for ln in lines if ln["t1"] - ln["t0"] < CAPTION_MIN_SHOW]
    if short:
        ln = short[0]
        out.append(finding("text_identity", style0, f"{len(short)} caption line(s) on screen for less than "
                           f"{CAPTION_MIN_SHOW} s: {' '.join(ln['words'])!r} shows {ln['t0']:.2f}-{ln['t1']:.2f} s",
                           basis="pixels+truth", value=round(ln["t1"] - ln["t0"], 3), threshold=CAPTION_MIN_SHOW))
    late = [(said[i], spoken[i][1], ln["t0"], ln["t1"]) for i, (_w, ln) in enumerate(shown)
            if not (ln["t0"] - CAPTION_TIMING_TOL <= spoken[i][1] <= ln["t1"] + CAPTION_TIMING_TOL)]
    if late:
        wd, s, t0, t1 = max(late, key=lambda x: max(x[2] - x[1], x[1] - x[3]))
        out.append(finding("text_identity", style0, f"{len(late)} word(s) not on screen while spoken: {wd!r} is said at "
                           f"{s:.2f} s, its caption shows {t0:.2f}-{t1:.2f} s", basis="pixels+truth", word=wd,
                           value=round(max(t0 - s, s - t1), 2), threshold=CAPTION_TIMING_TOL))
    return out


def _event_steady(ass: AssFile, ev: AssEvent) -> bool:
    """Whether an event has a run of text shown steadily (not hidden, not fading in): such an event must draw."""
    style_line = next((ln for ln in ass.head if ln.startswith("Style:") and ln[6:].split(",")[0].strip() == ev.style), "")
    parts = [p.strip() for p in style_line[6:].split(",")] if style_line else []
    state = "hidden" if len(parts) > 3 and parts[3].upper().startswith("&HFF") else "shown"
    for block, text in re.findall(r"(\{[^}]*\})?([^{]*)", ev.text):
        if block:
            if re.search(r"\\t\([^)]*\\(?:1a|alpha)&H00&", block):
                state = "fading"
            elif re.search(r"\\(?:1a|alpha)&HFF&", block):
                state = "hidden"
            elif re.search(r"\\(?:1a|alpha)&H00&", block):
                state = "shown"
        if text.replace("\\N", "").replace("\\n", "").replace("\\h", "").strip() and state == "shown":
            return True
    return False


CAPTION_PLATE_CONTRAST = 3.0  # WCAG large text: caption glyphs on their plate (their spoken word lit, the rest dimmed by
#                               design: the master's pixels are judged at 4.5:1); a covered or black-on-black word is ~1:1
CAPTION_FADE_MAX = 0.30       # s a word may take to fade in (a slower fade leaves it unreadable while it is said)


def _slow_fades(ev: AssEvent) -> list[float]:
    """The alpha fade-ins of an event longer than CAPTION_FADE_MAX, in seconds."""
    out = []
    for m in re.finditer(r"\\t\((\d+),(\d+)[^)]*\\(?:1a|alpha)&H00&", ev.text):
        d = (int(m.group(2)) - int(m.group(1))) / 1000.0
        if d > CAPTION_FADE_MAX:
            out.append(d)
    return out


def _event_visible(ass: AssFile, ev: AssEvent) -> bool:
    """Whether an event has text meant to show: a run of text not left at full transparency (an \\alpha&HFF& or
    \\1a&HFF& override with no transition back, or a style whose primary colour is fully transparent)."""
    style_line = next((ln for ln in ass.head if ln.startswith("Style:") and ln[6:].split(",")[0].strip() == ev.style), "")
    parts = [p.strip() for p in style_line[6:].split(",")] if style_line else []
    hidden = len(parts) > 3 and parts[3].upper().startswith("&HFF")
    for block, text in re.findall(r"(\{[^}]*\})?([^{]*)", ev.text):
        if block:
            if re.search(r"\\(?:1a|alpha)&HFF&", block) and not re.search(r"\\t\([^)]*\\(?:1a|alpha)&H00&", block):
                hidden = True
            elif re.search(r"\\(?:1a|alpha)&H00&", block):
                hidden = False
        if text.replace("\\N", "").replace("\\n", "").replace("\\h", "").strip() and not hidden:
            return True
    return False
