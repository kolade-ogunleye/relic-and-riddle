"""The MASTHEAD class's covers at a tree, cheaply: a copy of plates_draw.py's drawn decks (base) with slide 1 of each
copy drawn again by the tree under test through production's own redraw (multi_format.redraw_carousel_slide: the
slide's kept recipe, its kicker step, its heading frame, saved and re-measured on the JPEG as the pipeline saves it).
The class changes slide 1 only (the hook ratio and the grid-crop strip part); masthead_vs_base.py proves every other
slide pixel-identical on full draws.

    python masthead_redraw_covers.py <tree> <dev checkout> <base drawn>/<brand> <out>/<brand> [--brand b] [--runs s,...]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("base", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", default="default")
    ap.add_argument("--runs", default="")
    a = ap.parse_args()
    tree, dev = a.tree.resolve(), a.dev.resolve()
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))

    def offline(*_a, **_k):
        raise OSError("offline")
    socket.create_connection = offline
    sys.path.insert(0, str(tree))
    from src import brand, multi_format, still_layout
    if a.brand != "default":
        sys.path.insert(0, str(tree / "docs/reviews/quality-fixes-2026-11/QF-35a"))
        import render_surfaces
        brand.configure(render_surfaces.second_brand(brand, "Montserrat" if a.brand == "second" else None),
                        validate=False)
    keep = still_layout.save

    def save(still, layout, recipe, attempts=1):   # plates_draw.py's sidecar: the layout meta kept with the record
        entry = keep(still, layout, recipe, attempts)
        meta = getattr(layout, "meta", None)
        if isinstance(meta, dict):
            side = still_layout.sidecar(Path(still))
            data = json.loads(side.read_text(encoding="utf-8"))
            data[Path(still).name]["meta"] = json.loads(json.dumps(meta, default=str))
            side.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        return entry
    still_layout.save = save
    for run in sorted(p for p in a.base.iterdir() if (p / "draw.json").is_file()):
        if a.runs and not any(s and s in run.name for s in a.runs.split(",")):
            continue
        dst = a.out / run.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(run, dst)
        for sub in ("carousel", "carousel_fb"):
            path = dst / sub / "carousel_slide_01.jpg"
            rec = still_layout.load(path)
            if not rec:
                continue
            multi_format.redraw_carousel_slide(path, rec["recipe"], tight=bool(rec.get("tight")))
            print(f"{run.name[4:48]:44} {sub:11} redrawn", flush=True)
        d = json.loads((dst / "draw.json").read_text(encoding="utf-8"))
        d["redrawn_covers_at"] = str(tree)
        (dst / "draw.json").write_text(json.dumps(d, indent=1), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
