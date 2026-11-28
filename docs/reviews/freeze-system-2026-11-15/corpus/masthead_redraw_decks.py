"""The MASTHEAD class's decks at a tree (freeze cycle 6, round 2): a copy of plates_draw.py's drawn decks (base) with
EVERY slide of both copies drawn again by the tree under test through production's own calls: the post's masthead form
decided by MultiFormatGenerator._deck_masthead_form (both copies' covers, as generate_carousel_sequence decides it),
then each slide by multi_format.redraw_carousel_slide from its kept recipe with that form (its kicker step, its
heading frame; saved and re-measured on the JPEG as the pipeline saves it). A tree without the form (the base) redraws
each slide as it is. Every slide is drawn by the tree, so the interior comparison is a tip draw, not a copy.

    python masthead_redraw_decks.py <tree> <dev checkout> <base drawn>/<brand> <out>/<brand> [--brand b] [--runs s,...]
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
    ap.add_argument("--stack-shift", type=float, default=None, help="masthead.STACK_SHIFT for a placement experiment")
    a = ap.parse_args()
    tree, dev = a.tree.resolve(), a.dev.resolve()
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))

    def offline(*_a, **_k):
        raise OSError("offline")
    socket.create_connection = offline
    sys.path.insert(0, str(tree))
    from src import brand, multi_format, still_layout
    if a.stack_shift is not None:
        from src import masthead
        masthead.STACK_SHIFT = a.stack_shift
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
    gen = multi_format.MultiFormatGenerator()
    decide = getattr(gen, "_deck_masthead_form", None)
    for run in sorted(p for p in a.base.iterdir() if (p / "draw.json").is_file()):
        if a.runs and not any(s and s in run.name for s in a.runs.split(",")):
            continue
        dst = a.out / run.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(run, dst)
        recs = {}
        for sub in ("carousel", "carousel_fb"):
            f = dst / sub / "still_layout.json"
            if f.is_file():
                recs[sub] = sorted(json.loads(f.read_text(encoding="utf-8")).items())
        form = None
        if decide and recs.get("carousel"):
            rs = [e["recipe"] for _, e in recs["carousel"]]
            r0 = rs[0]
            form = decide([r["card"] for r in rs], [r["archetype"] for r in rs], [Path(r["photo"]) for r in rs],
                          int(r0["total"]), r0.get("poll") or {}, r0.get("share") or "", r0.get("subject") or "",
                          r0.get("lead"))
            multi_format._render_state.masthead_form_note = None
        def redraw(form_):
            for sub, entries in recs.items():
                # slide 2 before slide 1 (the strip meets slide 2's kicker rule), as the sequence draws them
                order = sorted(entries, key=lambda kv: (kv[0] != "carousel_slide_02.jpg", kv[0]))
                for name, e in order:
                    recipe = dict(e["recipe"])
                    if form_ is not None:
                        recipe["masthead_form"] = form_
                    multi_format.redraw_carousel_slide(dst / sub / name, recipe, tight=bool(e.get("tight")))
                print(f"{run.name[4:48]:44} {sub:11} {len(entries)} slides redrawn, form {form_}", flush=True)
        redraw(form)
        fell = None
        if form == "stacked":
            # the post's fallback as generate_carousel_sequence makes it (multi_format.stack_failures): any slide of
            # either copy whose stacked mark failed its check sends the whole post to the one-line form
            failed = []
            for sub in recs:
                for name, e in sorted(json.loads((dst / sub / "still_layout.json").read_text(encoding="utf-8")).items()):
                    chk = (e.get("meta") or {}).get("stack_check") or {}
                    if chk and not chk.get("ok"):
                        failed.append(f"{'Facebook' if sub.endswith('_fb') else 'Instagram'} slide "
                                      f"{int(name[-6:-4])}: {chk.get('why')}")
            if failed:
                fell = failed
                form = "line"
                multi_format._render_state.masthead_form_note = ("the post fell back to the one-line form: "
                                                                  + "; ".join(failed))
                redraw(form)
                multi_format._render_state.masthead_form_note = None
        d = json.loads((dst / "draw.json").read_text(encoding="utf-8"))
        d.update(redrawn_decks_at=str(tree), masthead_form=form, stacked_fallback=fell,
                 stack_shift=a.stack_shift)
        (dst / "draw.json").write_text(json.dumps(d, indent=1), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
