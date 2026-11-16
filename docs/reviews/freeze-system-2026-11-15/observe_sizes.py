"""Cycle 0 round 4: QF-35a's render_surfaces.py run under the runtime size observer (tests/size_observer.py): every
font made while every reachable surface renders from REAL runs, by site and size.

    python observe_sizes.py <tree> <dev checkout> <out dir> [render_surfaces options, e.g. --only ... --per-kind 1]

Writes <out dir>/rs.json (render_surfaces' hashes: the pixel-identity evidence against the previous commit) and
<out dir>/observed_sizes.json: every off-ladder (site, size) pair seen, and the sites not declared in
brand_system.SIZE_SITES (must be empty).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

tree, dev, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(tree))
sys.path.insert(0, str(tree / "tests"))
sys.path.insert(0, str(tree / "docs/reviews/quality-fixes-2026-11/QF-35a"))
import importlib

import render_surfaces
import size_observer
import type_sites

for _m in ("typography", "font_engine", *type_sites.SURFACE_MODULES):   # bound before the observer patches them
    importlib.import_module(f"src.{_m}")

sys.argv = ["render_surfaces.py", str(tree), str(dev), str(out / "rs.json"), "--keep", str(out / "png"),
            *sys.argv[4:]]
with size_observer.observe() as seen:
    render_surfaces.main()
from src import brand_system

ladder = type_sites._ladder()
off: dict[str, set] = {}
for site, size in seen:
    if site != size_observer.OUTSIDE and round(size, 3) not in ladder:
        off.setdefault(site, set()).add(size)
undeclared = sorted(set(off) - set(brand_system.SIZE_SITES) - set(brand_system.RUNTIME_SIZE_SITES))
(out / "observed_sizes.json").write_text(json.dumps({
    "sites_off_ladder": len(off), "pairs_off_ladder": sum(len(v) for v in off.values()),
    "undeclared_sites": undeclared, "off_ladder": {k: sorted(v) for k, v in sorted(off.items())}}, indent=1),
    encoding="utf-8", newline="\n")
print("off-ladder sites", len(off), "undeclared", undeclared)
