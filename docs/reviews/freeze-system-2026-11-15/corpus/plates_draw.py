"""The stills the PLATES deck / Story / cover proofs measure (freeze cycle 5), drawn ONCE per tree and palette and kept:
every dev run that has the surface's inputs (verified_script.json, provenance.json), its Instagram and Facebook
carousel, its two Story frames and its Reel cover and grid tile, each through the pipeline's own call
(MultiFormatGenerator.generate_carousel_sequence / generate_story_sequence, VisualEngine.generate_thumbnails), on the
run's own copy and scene stills. Each slide's photo is the scene still that best matches the slide the run delivered
(luma correlation, as the board's Aswan deck: the pipeline's own matcher is a paid vision call; never reached here).

    python plates_draw.py <tree> <dev checkout> <out> [--brand default|second-colours] [--jobs N]
                          [--kinds deck,story,cover] [--runs <substring>,...]

Writes <out>/<brand>/<run>/ (carousel/, carousel_fb/, stories/, thumbnail_cover.jpg, still_layout.json sidecars with
each still's layout meta added under "meta") and draw.json (the tree's commit, the kinds drawn, refusals, seconds).
A run already drawn for the same commit is skipped. This is the expensive step (a deck is drawn about three times by
the pipeline's own kicker survey: minutes per deck); the class scripts (plates_04, 12, 13, 14, 15) only read what it
left: records and image statistics, seconds each.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KINDS = ("deck", "story", "cover")


def args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", choices=("default", "second-colours"), default="default")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--runs", default="")
    ap.add_argument("--one", default="", help=argparse.SUPPRESS)
    return ap.parse_args()


def commit(tree: Path) -> str:
    try:
        sha = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--", "src"], capture_output=True,
                               text=True).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except OSError:
        return "unknown"


def corpus_runs(dev: Path) -> list[Path]:
    return sorted(p for p in (dev / "output").glob("run_*")
                  if (p / "verified_script.json").is_file() and (p / "provenance.json").is_file())


def scene_stills(run: Path) -> list[Path]:
    """The run's scene stills in order: each provenance asset's first source that is a photograph."""
    prov = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    out: list[Path] = []
    for a in prov.get("assets") or []:
        if Path(str(a.get("path") or "")).name.startswith("graphic_"):
            continue
        for s in a.get("source_paths") or []:
            p = Path(s)
            if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.is_file():
                if p not in out:
                    out.append(p)
                break
    return out


def match_delivered(delivered: list[Path], stills: list[Path], shape=(36, 45)) -> list[Path]:
    """Per delivered still, the scene still that best matches it (luma correlation at `shape`); a missing delivered
    still takes the scene order."""
    import numpy as np
    from PIL import Image, ImageOps

    def vec(p):
        with Image.open(p) as im:
            a = np.asarray(ImageOps.fit(im.convert("L"), shape, Image.LANCZOS), np.float32).ravel()
        return (a - a.mean()) / (a.std() + 1e-6)
    cand = [vec(s) for s in stills]
    out = []
    for i, slide in enumerate(delivered):
        if not slide.is_file():
            out.append(stills[i % len(stills)])
            continue
        v = vec(slide)
        out.append(stills[int(np.argmax([float(v @ c) for c in cand]))])
    return out


def one(a) -> int:
    tree, dev = a.tree.resolve(), a.dev.resolve()
    run = dev / "output" / a.one
    out = a.out.resolve() / a.brand / a.one
    out.mkdir(parents=True, exist_ok=True)
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))

    def offline(*_a, **_k):
        raise OSError("offline")
    socket.create_connection = offline
    sys.path.insert(0, str(tree))
    from src import brand, still_layout
    if a.brand != "default":
        sys.path.insert(0, str(tree / "docs/reviews/quality-fixes-2026-11/QF-35a"))
        import render_surfaces
        brand.configure(render_surfaces.second_brand(brand, None), validate=False)
    from src.ai_label import real_stills
    from src.multi_format import CAROUSEL_PHOTOS, MultiFormatGenerator
    from src.visual_engine import VisualEngine

    keep = still_layout.save

    def save(still, layout, recipe, attempts=1):
        entry = keep(still, layout, recipe, attempts)
        meta = getattr(layout, "meta", None)
        if isinstance(meta, dict):
            side = still_layout.sidecar(Path(still))
            data = json.loads(side.read_text(encoding="utf-8"))
            data[Path(still).name]["meta"] = json.loads(json.dumps(meta, default=str))
            side.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        return entry
    still_layout.save = save

    payload = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
    stills, _ = real_stills(scene_stills(run))
    rec: dict = {"run": a.one, "commit": commit(tree), "brand": a.brand, "kinds": {}, "stills": len(stills)}
    kinds = [k for k in a.kinds.split(",") if k in KINDS]
    gen = MultiFormatGenerator()

    def guarded(kind, fn):
        t = time.time()
        try:
            got = fn()
            rec["kinds"][kind] = {"ok": True, "seconds": round(time.time() - t, 1), **(got or {})}
        except Exception as e:   # a refusal is a result (recorded), never a crash of the corpus
            rec["kinds"][kind] = {"ok": False, "seconds": round(time.time() - t, 1),
                                  "error": f"{type(e).__name__}: {str(e)[:300]}"}

    def deck():
        cards = payload["carousel_slides"]
        photos = match_delivered([run / "carousel" / f"carousel_slide_{i + 1:02d}.jpg" for i in range(len(cards))],
                                 stills)
        key = {"cards": [f"{c.get('heading', '')}|{c.get('body', '')}" for c in cards],
               "stills": [str(Path(p)) for p in stills]}
        (out / CAROUSEL_PHOTOS).write_text(json.dumps({"key": key, "photos": [str(p) for p in photos]}, indent=1),
                                           encoding="utf-8", newline="\n")
        graphics = sorted((run / "graphics").glob("graphic_*.mp4"))
        got = {}
        for fb in (False, True):
            p = dict(payload)
            gen.generate_carousel_sequence(stills, p, out, facebook=fb, graphics=graphics)
            got["facebook" if fb else "instagram"] = {
                "layout_holds": p.get("layout_holds"), "omissions": p.get("omissions")}
        return {"photos": [Path(p).name for p in photos], "ledgers": json.loads(json.dumps(got, default=str))}

    def story():
        frames = [run / "stories" / n for n in ("story_01_hook.jpg", "story_02_reveal.jpg")]
        picked = match_delivered(frames, stills, (36, 64))
        MultiFormatGenerator._match_photos = lambda self, cards, st, strict=False: picked[:len(cards)]
        gen.generate_story_sequence(picked[0], dict(payload), out, scene_images=stills)
        return {"photos": [Path(p).name for p in picked]}

    def cover():
        delivered = run / "thumbnail_cover.jpg"
        hero = match_delivered([delivered], stills, (36, 64))[0]
        VisualEngine().generate_thumbnails(hero, dict(payload), out)
        return {"hero": Path(hero).name}

    if not stills:
        rec["error"] = "no real scene still on disk"
    else:
        for kind, fn in (("deck", deck), ("story", story), ("cover", cover)):
            if kind in kinds:
                guarded(kind, fn)
    prior = {}
    f = out / "draw.json"
    if f.is_file():
        prior = json.loads(f.read_text(encoding="utf-8"))
        if prior.get("commit") == rec["commit"]:
            rec["kinds"] = {**(prior.get("kinds") or {}), **rec["kinds"]}
    f.write_text(json.dumps(rec, indent=1), encoding="utf-8", newline="\n")
    return 0


def main() -> int:
    a = args()
    if a.one:
        return one(a)
    tree, dev = a.tree.resolve(), a.dev.resolve()
    sha = commit(tree)
    want = [k for k in a.kinds.split(",") if k in KINDS]
    names = [r.name for r in corpus_runs(dev)]
    if a.runs:
        names = [n for n in names if any(s and s in n for s in a.runs.split(","))]
    todo = []
    for n in names:
        f = a.out / a.brand / n / "draw.json"
        done = {}
        if f.is_file():
            got = json.loads(f.read_text(encoding="utf-8"))
            done = got.get("kinds") or {} if got.get("commit") == sha else {}
        missing = [k for k in want if k not in done]
        if missing:
            todo.append((n, missing))
    print(f"{len(names)} runs, {len(todo)} to draw at {sha} ({a.brand})", flush=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def go(item):
        n, missing = item
        t = time.time()
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(tree), str(dev), str(a.out), "--brand",
                            a.brand, "--kinds", ",".join(missing), "--one", n], capture_output=True, text=True, env=env)
        (a.out / a.brand / n).mkdir(parents=True, exist_ok=True)
        (a.out / a.brand / n / "draw.log").write_text((r.stdout or "") + (r.stderr or ""), encoding="utf-8",
                                                      errors="replace", newline="\n")
        print(f"{n}: exit {r.returncode} in {time.time() - t:.0f} s ({','.join(missing)})", flush=True)
        return r.returncode
    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        codes = list(ex.map(go, todo))
    return 1 if any(codes) else 0


if __name__ == "__main__":
    sys.exit(main())
