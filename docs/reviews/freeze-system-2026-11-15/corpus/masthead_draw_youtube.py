"""The YouTube thumbnails the MASTHEAD hook-ratio proof (freeze cycle 6, M1) measures, drawn once per tree and palette:
every dev run that has the surface's inputs (verified_script.json, provenance.json), through the pipeline's own call
(VisualEngine.generate_youtube_thumbnail, the headline chain and the feed gate included) on the run's hero (the scene
still that best matches the run's delivered Reel cover: main draws both from the same hero_img; plates_draw.py's
matcher). The render's own meta and plates are kept (the masthead record, the hook's first row as
youtube_thumbnail.hook_row measures it, every drawn plate's ink box).

    python masthead_draw_youtube.py <tree> <dev checkout> <out> [--brand default|second|second-colours] [--runs s,...]

Writes <out>/<brand>/<run>/thumbnail_youtube_16x9.jpg and yt.json. A refused thumbnail is a result (recorded).
"""
from __future__ import annotations

import argparse
import copy as _copy
import json
import os
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plates_draw as pd  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", choices=("default", "second", "second-colours"), default="default")
    ap.add_argument("--runs", default="")
    a = ap.parse_args()
    tree, dev = a.tree.resolve(), a.dev.resolve()
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))

    def offline(*_a, **_k):
        raise OSError("offline")
    socket.create_connection = offline
    sys.path.insert(0, str(tree))
    from src import brand, youtube_thumbnail
    if a.brand != "default":
        sys.path.insert(0, str(tree / "docs/reviews/quality-fixes-2026-11/QF-35a"))
        import render_surfaces
        brand.configure(render_surfaces.second_brand(brand, "Montserrat" if a.brand == "second" else None),
                        validate=False)
    from src.ai_label import real_stills
    from src.visual_engine import VisualEngine

    runs = pd.corpus_runs(dev)
    if a.runs:
        runs = [r for r in runs if any(s and s in r.name for s in a.runs.split(","))]
    real_render = youtube_thumbnail.render
    for run in runs:
        out = a.out.resolve() / a.brand / run.name
        out.mkdir(parents=True, exist_ok=True)
        payload = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
        stills, _ = real_stills(pd.scene_stills(run))
        rec: dict = {"run": run.name, "commit": pd.commit(tree), "brand": a.brand}
        if not stills:
            rec["error"] = "no real scene still on disk"
            (out / "yt.json").write_text(json.dumps(rec, indent=1), encoding="utf-8", newline="\n")
            continue
        hero = pd.match_delivered([run / "thumbnail_cover.jpg"], stills, (36, 64))[0]
        seen: dict = {}

        def spy(*args, **kw):
            got = real_render(*args, **kw)
            seen["meta"], seen["plates"] = dict(kw.get("meta") or {}), got[1]
            return got
        youtube_thumbnail.render = spy
        t = time.time()
        try:
            VisualEngine().generate_youtube_thumbnail(hero, _copy.deepcopy(payload), out / "thumbnail_youtube_16x9.jpg")
            ok, err = True, None
        except Exception as e:   # a refusal is a result
            ok, err = False, f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            youtube_thumbnail.render = real_render
        plates = seen.get("plates") or []
        meta = seen.get("meta") or {}
        rec.update(ok=ok, error=err, hero=Path(hero).name, seconds=round(time.time() - t, 1),
                   masthead={k: v for k, v in (meta.get("masthead") or {}).items() if k != "box"},
                   hook_row=youtube_thumbnail.hook_row(plates) if ok else None,
                   hook_ratio=meta.get("hook_ratio"), lockup=meta.get("lockup"),
                   plates=[{"name": n, "text": getattr(p, "text", ""), "box": [int(v) for v in p.box],
                            "size": round(float(getattr(p.font, "size", 0)), 2)}
                           for n, p in plates if not getattr(p, "empty", False)] if ok else [])
        (out / "yt.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str), encoding="utf-8",
                                     newline="\n")
        print(f"{run.name[4:48]:44} {'drawn' if ok else 'REFUSED'} {rec['seconds']:5.1f} s "
              f"row {rec['hook_row']} mast {rec['masthead'].get('group_width')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
