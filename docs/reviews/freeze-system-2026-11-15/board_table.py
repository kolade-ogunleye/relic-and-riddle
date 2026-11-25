"""Row 13's element tables generated from the tree and the board's own records (cycle 3, lane BOARD; AD r2, TY r1,
BC r1/r2, p3 TY/BC row 13, MO r1: the hand-written table said "YouTube: same, 64px" where the renderer draws 48-54,
"brand moment 60.0px" where the record draws 64, and end_card.SECONDS 2.9 beside a delivered 5.2 s card).

Nothing here is written by hand about a surface; every cell is one of:
- drawn(): what this board's renderers recorded when they drew (row 2's masthead records per ground and surface,
  the end sequences' stats and their decoded clips): sizes, variants, contrast, scrim, halo, delivered seconds;
- code_rows(): what the code reaches from each surface's entry point (an AST call graph over src/*.py: whether the
  masthead or the heading engine is reached, the palette colours and the tokens named in the surface's own module);
- weight_rows(): every "<role>.weight" value the system holds, with the src modules that read it (a value no
  renderer reads is written only, and says so) and the weight row 1's specimen draws.
An entry point or a record missing on the tree under review is a cell saying so, never a stale value.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

# surface -> (src module, function qualname): the renderer the pipeline calls for it (as board.py and
# render_surfaces.py call them); a name the tree does not have shows as "not in this tree"
ENTRY = {
    "carousel slide": ("multi_format", "MultiFormatGenerator._render_carousel_slide"),
    "Story frame": ("multi_format", "MultiFormatGenerator._render_story_frame"),
    "Reel cover / grid": ("visual_engine", "VisualEngine.generate_thumbnails"),
    "YouTube thumbnail": ("youtube_thumbnail", "render"),
    "end screen 16:9 / end card 9:16": ("end_card", "frames"),
    "loop bridge": ("end_card", "loop_bridge"),
    "hook frame": ("design_cards", "plan_hook_frame"),
    "chapter card": ("design_cards", "plan_chapter_card"),
    "myth card": ("design_cards", "plan_myth_fact"),
    "number card": ("graphics", "render_stat_cards"),
    "map": ("graphics", "render_map"),
    "timeline": ("graphics", "render_timeline"),
    "photo callout": ("callouts", "render"),
}


class _Module:
    def __init__(self, name: str, path: Path):
        self.name, self.src = name, path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.src)
        self.funcs: dict[str, ast.AST] = {}
        self.aliases: dict[str, str] = {}    # local name -> src module (import ... as)
        self.imported: dict[str, tuple[str, str]] = {}   # local name -> (src module, function)
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[node.name] = node
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.funcs[f"{node.name}.{sub.name}"] = sub
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.level >= 1:
                for a in node.names:
                    if node.module:   # from .x import f
                        self.imported[a.asname or a.name] = (node.module.split(".")[-1], a.name)
                    else:             # from . import x
                        self.aliases[a.asname or a.name] = a.name
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src"):
                for a in node.names:
                    if node.module == "src":
                        self.aliases[a.asname or a.name] = a.name
                    else:
                        self.imported[a.asname or a.name] = (node.module.split(".")[-1], a.name)

    def resolve(self, owner: str, call: ast.Call) -> tuple[str, str] | None:
        """(module, function) a call in `owner` names, when it is a src function this graph can follow."""
        f = call.func
        if isinstance(f, ast.Name):
            if f.id in self.funcs:
                return self.name, f.id
            if f.id in self.imported:
                return self.imported[f.id]
            cls = owner.split(".")[0] if "." in owner else None
            return (self.name, f"{f.id}.__init__") if f"{f.id}.__init__" in self.funcs else (
                (self.name, f"{cls}.{f.id}") if cls and f"{cls}.{f.id}" in self.funcs else None)
        if isinstance(f, ast.Attribute):
            base = f.value
            if isinstance(base, ast.Name) and base.id in self.aliases:
                return self.aliases[base.id], f.attr
            if isinstance(base, ast.Name) and base.id in ("self", "cls") and "." in owner:
                return self.name, f"{owner.split('.')[0]}.{f.attr}"
        return None


def _modules(src: Path) -> dict[str, _Module]:
    return {p.stem: _Module(p.stem, p) for p in sorted(src.glob("*.py"))}


def reach(mods: dict[str, _Module], start: tuple[str, str]) -> set[tuple[str, str]]:
    """Every src function reachable from `start` by name (an over-approximation of what it may call)."""
    seen, todo = set(), [start]
    while todo:
        mod, fn = todo.pop()
        m = mods.get(mod)
        if (mod, fn) in seen or m is None or fn not in m.funcs:
            continue
        seen.add((mod, fn))
        for c in ast.walk(m.funcs[fn]):
            if isinstance(c, ast.Call):
                r = m.resolve(fn, c)
                if r:
                    todo.append(r)
    return seen


def _colour_names() -> list[str]:
    from src import brand
    return sorted(k for k in dir(brand) if k.isupper() and not k.startswith("_")
                  and isinstance(getattr(brand, k), tuple) and len(getattr(brand, k)) == 3
                  and all(isinstance(v, int) for v in getattr(brand, k)))


def code_rows(src: Path = Path("src")) -> list[tuple]:
    """Per surface, from its entry point's reach: whether the masthead is drawn (src/masthead.py reached), whether
    the heading engine is reached (src/heading.py), and, in the surface's own module (the entry and the same-module
    functions it reaches), the palette colours it names (brand.<COLOUR>) and the tokens it reads (tok("...") /
    motion("...") string arguments)."""
    mods = _modules(src)
    colours = _colour_names()
    rows = [("surface (entry point)", "masthead", "heading engine", "colours named in its module",
             "tokens read in its module")]
    for surface, (mod, fn) in ENTRY.items():
        if mod not in mods or fn not in mods[mod].funcs:
            rows.append((f"{surface} ({mod}.{fn})", "not in this tree", "-", "-", "-"))
            continue
        got = reach(mods, (mod, fn))
        mast = sorted(f for m, f in got if m == "masthead")
        head = sorted(f for m, f in got if m == "heading")
        own = [mods[mod].funcs[f] for m, f in got if m == mod]
        named, toks = set(), set()
        for node in own:
            for n in ast.walk(node):
                if isinstance(n, ast.Attribute) and n.attr in colours:
                    named.add(n.attr)
                elif isinstance(n, ast.Name) and n.id in colours:
                    named.add(n.id)
                elif (isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant)
                      and isinstance(n.args[0].value, str)
                      and getattr(n.func, "id", getattr(n.func, "attr", "")) in ("tok", "motion", "colour_of")):
                    toks.add(n.args[0].value)
        rows.append((f"{surface} ({mod}.{fn})",
                     f"drawn (reaches masthead.{', masthead.'.join(mast[:4])})" if mast else "not drawn (src/masthead.py not reached)",
                     f"reached ({', '.join(head[:3])})" if head else "own rule (src/heading.py not reached)",
                     ", ".join(sorted(named)) or "none named (shared helpers only)",
                     ", ".join(sorted(toks)) or "none"))
    return rows


def weight_rows(specimen: dict[str, Any], src: Path = Path("src")) -> list[tuple]:
    """Every "<role>.weight" value (gate, runtime token or written SPEC) beside the src modules that name it and the
    weight row 1's specimen draws for that role."""
    from src import brand_system as bs
    rows = [("weight value", "value", "kind", "read by (src modules naming it)", "row 1 specimen draws")]
    texts = {p.stem: p.read_text(encoding="utf-8") for p in sorted(src.glob("*.py")) if p.stem != "brand_system"}
    for kind, table in (("gate", bs.GATES), ("runtime token", getattr(bs, "SYSTEM_TOKENS", {})), ("SPEC (written)", bs.SPEC)):
        for k, v in table.items():
            if not k.endswith(".weight"):
                continue
            readers = sorted(m for m, t in texts.items() if re.search(rf"[\"']{re.escape(k)}[\"']", t))
            role = k[: -len(".weight")]
            rows.append((k, v, kind, ", ".join(readers) or "no renderer reads it (written only)",
                         specimen.get(role, "role not on the specimen")))
    return rows


def _span(vals) -> str:
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return "-"
    lo, hi = min(vals), max(vals)
    return f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"


def drawn(out: Path, end_seqs: list[tuple[str, str, Any]]) -> list[tuple]:
    """What the masthead and the end sequences measured as drawn on this board, per surface across its grounds."""
    rows = [("surface (records)", "masthead size px", "variant", "contrast (worst letter)", "scrim peak / halo",
             "shown / not drawn", "grounds")]
    p = out / "row02_masthead.json"
    rec = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    by: dict[str, list] = {}
    for key, r in rec.items():
        ground, surface = key.split(" | ", 1)
        by.setdefault(re.sub(r" \(.*", "", surface), []).append((ground, r))
    for _topic, name, seq in end_seqs:
        st = getattr(seq, "stats", {}) or {}
        if st.get("masthead"):
            by.setdefault(f"{name} (end sequence, rows 9/12)", []).append((seq.topic, st["masthead"]))
    for surface, items in sorted(by.items()):
        rs = [r for _g, r in items if "refused" not in r]
        rows.append((surface, _span([r.get("size") for r in rs]),
                     ", ".join(sorted({str(r.get("variant")) for r in rs if r.get("variant")})) or "-",
                     _span([r.get("contrast") for r in rs]),
                     f"{_span([r.get('scrim_peak') for r in rs])} / {_span([r.get('halo') for r in rs])}",
                     f"{sum(r.get('shown', True) is not False for r in rs)} / "
                     f"{sum(r.get('shown') is False for r in rs)}"
                     + (f" ({len(items) - len(rs)} refused)" if len(items) > len(rs) else ""),
                     ", ".join(g for g, _r in items)))
    if not rec:
        rows.append(("row 2 not drawn on this board", "-", "-", "-", "-", "-", "-"))
    return rows


SOCIAL_KINDS = {"carousel": "Instagram carousel", "fb": "Facebook multi-photo post", "story": "Story"}


def social_part(stem: str) -> tuple[str, str, int] | None:
    """(deck, kind, frame index) of a render_surfaces.py social surface (social__<run>__<kind>_<frame>), else None."""
    m = re.match(r"social__(.+?)__(carousel|fb|story)_(?:carousel_slide|story)_(\d+)", stem)
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def masthead_class(stem: str) -> str | None:
    """A social surface's masthead class: its kind's cover (frame 1) or an interior / later frame."""
    part = social_part(stem)
    if part is None:
        return None
    _deck, kind, k = part
    return f"{SOCIAL_KINDS[kind]} {'cover (frame 1)' if k == 1 else 'interior (frame 2+)'}"


def social_masthead_rows(surfaces: dict[str, Any] | None) -> list[tuple]:
    """Cycle 4 (panel 5 BC, carry-in 3): the masthead each social frame drew on this board, from its renderer's own
    layout record (render_surfaces.py "masthead_records"): per kind's cover and interiors, the sizes, variants and
    contrast drawn, the Facebook cover's matched reach where it took it, and every reason a frame draws none (a
    Facebook interior carries no masthead by rule: the collage's cover tile is branded once)."""
    rows = [("surface (records)", "masthead size px", "variant", "contrast (worst letter)", "shown / not drawn",
             "not drawn because", "Facebook cover reach matched", "decks")]
    recs = (surfaces or {}).get("masthead_records") or {}
    if not recs:
        rows.append(("no frame masthead records on this board (render_surfaces.py before cycle 4, or no social "
                     "job)", "-", "-", "-", "-", "-", "-", "-"))
        return rows
    by: dict[str, list] = {}
    for stem, r in sorted(recs.items()):
        cls = masthead_class(stem)
        if cls:
            by.setdefault(cls, []).append((stem, r))
    for cls, items in sorted(by.items()):
        rs = [r for _s, r in items]
        shown = [r for r in rs if r.get("shown", True) is not False]
        reasons = sorted({str(r.get("reason")) for r in rs if r.get("shown") is False})
        rows.append((cls, _span([r.get("size") for r in shown]),
                     ", ".join(sorted({str(r.get("variant")) for r in shown if r.get("variant")})) or "-",
                     _span([r.get("contrast") for r in shown]), f"{len(shown)} / {len(rs) - len(shown)}",
                     "; ".join(reasons) or "-", _span([r.get("reach_matched") for r in rs]),
                     ", ".join(sorted({social_part(s)[0] for s, _r in items}))))
    return rows


def delivered_motion(end_seqs: list[tuple[str, str, Any]]) -> list[tuple]:
    """(name, value, kind, what): each end sequence's delivered length (decoded frames / fps) beside its module
    constant and its stats' own seconds, so a constant that no longer times the card shows as such."""
    from src import end_card as ec
    rows = []
    for topic, name, seq in end_seqs:
        st = getattr(seq, "stats", {}) or {}
        const = ("END_SCREEN_SECONDS" if name.startswith("endscreen") else
                 "LOOP_SECONDS" if name.startswith("bridge") else "SECONDS")
        rows.append((f"delivered {name} ({topic})", f"{seq.n} frames = {seq.n / ec.FPS:.3f} s",
                     "decoded clip (drives now)",
                     f"end_card.{const} = {getattr(ec, const, 'not in this tree')}; the renderer's own seconds "
                     f"{st.get('seconds', '-')}" + (f"; {seq.n - 1} drawn + the Reel's frame 0" if name.startswith("bridge")
                                                    else "")))
    return rows
