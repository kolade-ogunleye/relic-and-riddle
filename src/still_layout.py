"""The measured layout of each still (Story frame, Reel cover, carousel slide), checked after it is drawn
(still-layout audit, 2026-10-28: production Antikythera 1 and Richat 2 were rejected after the vision look
reported a Story body cut at "35 meshing", a cover whose tagline "MAURITANIA'S BULLSEYE" was missing, and a
carousel header colliding with what was under it; the look only noted them).

Every renderer records each block of text and each badge it draws as the box PIL measured for it (the real
font metrics, outline stroke included), with the zone it must stay inside (the platform-safe box from
safe_zones). `problems()` then asserts, from those numbers alone, that no box leaves the canvas or its zone,
and that no two boxes intersect. A renderer whose first layout fails draws once more with its tighter fit;
the record, with the recipe to draw the still again, is kept beside the stills in `still_layout.json`, so
the audit (still_vision.findings) can re-check it and, when the vision look reports a cut-off or an overlap,
redraw that still once with the tighter fit."""
from __future__ import annotations

import importlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

Box = Tuple[int, int, int, int]
SIDECAR = "still_layout.json"
_lock = threading.Lock()


def intersects(a: Box, b: Box) -> bool:
    """True when two boxes share any area (touching edges do not count)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def inside(box: Box, within: Box) -> bool:
    return box[0] >= within[0] and box[1] >= within[1] and box[2] <= within[2] and box[3] <= within[3]


def union(boxes: List[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


class Layout:
    """The boxes one still draws, in canvas pixels."""

    def __init__(self, kind: str, size: Tuple[int, int], zone: Box, tight: bool = False,
                 protected: Optional[List[Tuple[str, Box]]] = None):
        self.kind, self.size, self.zone, self.tight = kind, (int(size[0]), int(size[1])), tuple(int(v) for v in zone), tight
        # Regions a platform draws its own controls over (the Instagram carousel's "N/M" counter; operator,
        # 2026-10-28): nothing recorded may intersect one.
        self.protected: List[Dict[str, Any]] = [{"name": n, "box": [int(v) for v in b]} for n, b in (protected or [])]
        self.elements: List[Dict[str, Any]] = []
        self.shortened: List[str] = []
        self.meta: Dict[str, Any] = {}   # a renderer's own record (the Story frame's layout.json entry)

    def add(self, name: str, box, zone: Optional[Box] = None) -> Box:
        box = tuple(int(round(v)) for v in box)
        self.elements.append({"name": name, "box": list(box), **({"zone": list(zone)} if zone else {})})
        return box

    def text(self, draw, xy, text: str, font, name: str, anchor: str = "la", zone: Optional[Box] = None,
             record: bool = True, **kw) -> Box:
        """Draws one line and records the box PIL measures for it (stroke included)."""
        stroke = kw.get("stroke_width", 0)
        draw.text(xy, text, font=font, anchor=anchor, **kw)
        box = draw.textbbox(xy, text, font=font, anchor=anchor, stroke_width=stroke)
        return self.add(name, box, zone) if record else tuple(box)

    def block(self, name: str, boxes: List[Box], zone: Optional[Box] = None) -> Optional[Box]:
        """Several lines recorded as one block (lines of one block never collide with each other)."""
        return self.add(name, union(boxes), zone) if boxes else None

    def problems(self) -> List[str]:
        return problems(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "size": list(self.size), "zone": list(self.zone), "tight": self.tight,
                "elements": self.elements, "shortened": self.shortened, "protected": self.protected}


def problems(record: Dict[str, Any]) -> List[str]:
    """Each measured defect, in words: a box past the canvas or its zone, two boxes over each other, or a box
    under a region the platform draws its own controls over (`protected`)."""
    w, h = record["size"]
    canvas = (0, 0, w, h)
    out: List[str] = []
    elements = record.get("elements") or []
    for e in elements:
        box, zone = tuple(e["box"]), tuple(e.get("zone") or record["zone"])
        if not inside(box, canvas):
            out.append(f"text_cut_off: {e['name']} {list(box)} runs past the {w}x{h} canvas")
        elif not inside(box, zone):
            out.append(f"text_cut_off: {e['name']} {list(box)} leaves its safe zone {list(zone)}")
    for i, a in enumerate(elements):
        for b in elements[i + 1:]:
            if intersects(tuple(a["box"]), tuple(b["box"])):
                out.append(f"layout_overlap: {a['name']} {a['box']} overlaps {b['name']} {b['box']}")
    for region in record.get("protected") or []:
        for e in elements:
            if intersects(tuple(e["box"]), tuple(region["box"])):
                out.append(f"layout_overlap: {e['name']} {e['box']} lies under the platform's {region['name']} {region['box']}")
    return out


def sidecar(still: Path) -> Path:
    return Path(still).parent / SIDECAR


def save(still: Path, layout: Layout, recipe: Dict[str, Any], attempts: int = 1) -> Dict[str, Any]:
    """Keeps a still's measured layout and how to draw it again, beside the still."""
    still = Path(still)
    entry = {**layout.to_dict(), "problems": layout.problems(), "attempts": attempts, "recipe": recipe}
    with _lock:
        path = sidecar(still)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            data = {}
        data[still.name] = entry
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return entry


def load(still: Path) -> Optional[Dict[str, Any]]:
    path = sidecar(still)
    try:
        return (json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}).get(Path(still).name)
    except (OSError, ValueError):
        return None


def check(still: Path) -> Optional[List[str]]:
    """The still's measured defects, re-derived from its record; None when no record was kept (an older run)."""
    entry = load(still)
    return None if entry is None else problems(entry)


def drawn(render, tight_render=None) -> Tuple[Any, Layout, int]:
    """Draws a still, checks it, and draws it once more with the tighter fit if the first layout fails:
    (image, layout, attempts). `render(tight)` returns (image, layout)."""
    im, lay = render(False)
    found = lay.problems()
    if not found:
        return im, lay, 1
    log.warning("%s layout failed its check (%s); drawing it again with the tighter fit.", lay.kind, "; ".join(found[:3]))
    im2, lay2 = (tight_render or render)(True)
    if lay2.problems():
        log.warning("%s layout still fails after the tighter fit: %s", lay2.kind, "; ".join(lay2.problems()[:3]))
    return im2, lay2, 2


def redraw(still: Path) -> Optional[Dict[str, Any]]:
    """Draws a still again with its tighter fit, from its kept recipe; the new record, or None when the still
    has no recipe (or its sources are gone). The renderer named in the recipe saves the image in place."""
    entry = load(still)
    recipe = (entry or {}).get("recipe")
    if not recipe:
        return None
    module_name, _, func = str(recipe.get("renderer", "")).rpartition(".")
    try:
        fn = getattr(importlib.import_module("." + module_name, __package__), func)
        return fn(Path(still), recipe)
    except Exception as e:  # a missing photo or font: the still stays as it is, and the caller says so
        log.warning("Could not redraw %s (%s).", Path(still).name, e)
        return None
