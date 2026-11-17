"""The mechanical type-size inventory (design-freeze cycle 0, round 3): every place a surface module sets a type size.

A *site* is a function (module:qualname, "<module>" for module level) of a surface module that calls a font loader
(`get_font`, `fit_text`, `probe_text`, `fit_font`, `fit_line`, `field_font` and the renderers' `_fit_text` /
`_design_wrap`) with a size that is not a literal on the system's ladder (brand_system.TYPE / TYPE_FALLBACK, any canvas).
tests/test_brand_system.py asserts that the set of sites equals brand_system.SIZE_SITES (each with its owner group),
so a new or ported size system cannot appear or vanish without the declaration following it.

    python tests/type_sites.py      # print the current inventory as a SIZE_SITES literal (for review, never pasted blind)
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SURFACE_MODULES = ("graphics", "design_cards", "multi_format", "cover_design", "youtube_thumbnail", "end_card",
                   "then_now", "callouts", "video_composer", "art_direction", "timeline_dense", "masthead")
LOADERS = {"get_font": 1, "fit_text": 3, "probe_text": 3, "fit_font": None, "fit_line": None, "field_font": None,
           "_fit_text": 3, "_design_wrap": 3,   # loader -> the size's positional index (None: always a computed fit)
           "font_variant": None, "truetype": 1, "FreeTypeFont": 1, "FeaturedFont": 1,   # round 4: every constructor
           "font_for": 0,                       # and the system's masthead wrapper (round 4: the adversary's 137 px)
           "figure_font": 0, "data_font": 0, "place_font": 0}   # cycle 0 round 7: the figure / data / place roles


def _ladder() -> set[float]:
    from src import brand_system
    vals = set()
    for table in (brand_system.TYPE, brand_system.TYPE_FALLBACK):
        for roles in table.values():
            vals |= {round(float(v), 3) for v in roles.values() if v is not None}
    vals |= {round(v * 3 / 4, 3) for v in list(vals)}   # the carousel's 1080 design px (drawn x 4/3)
    return vals


def sites() -> dict[str, set[str]]:
    """site -> the size expressions found there (source text), for every off-ladder or computed size."""
    ladder = _ladder()
    found: dict[str, set[str]] = {}
    for mod in SURFACE_MODULES:
        tree = ast.parse((ROOT / "src" / f"{mod}.py").read_text(encoding="utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        aliases = {}   # round 4: a loader bound to another name (gf = get_font) is a loader too
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                v = node.value
                src_name = getattr(v, "id", None) or getattr(v, "attr", None)
                if src_name in LOADERS:
                    aliases[node.targets[0].id] = src_name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            name = aliases.get(name, name)
            if name not in LOADERS:
                continue
            idx = LOADERS[name]
            arg = None
            if idx is not None:
                kw = next((k.value for k in node.keywords if k.arg in ("size", "px")), None)
                arg = kw if kw is not None else (node.args[idx] if len(node.args) > idx else None)
            if arg is not None and isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)) \
                    and round(float(arg.value), 3) in ladder:
                continue
            fn, p = [], parents.get(node)
            while p is not None:
                if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    fn.append(p.name)
                p = parents.get(p)
            site = f"{mod}:{'.'.join(reversed(fn)) or '<module>'}"
            found.setdefault(site, set()).add(ast.unparse(arg) if arg is not None else f"{name}(...)")
    return found


GROUP_OF_MODULE = {"graphics": "A", "timeline_dense": "A", "then_now": "A", "cover_design": "B",
                   "youtube_thumbnail": "B", "video_composer": "C", "multi_format": "D", "end_card": "E",
                   "design_cards": "F", "callouts": "F", "art_direction": "A", "masthead": "system"}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    for site in sorted(sites()):
        print(f'    "{site}": "{GROUP_OF_MODULE[site.split(":")[0]]}",')
