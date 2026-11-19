"""Design-freeze stress generator: hostile inputs for every surface, every result judged against the binding gates.

    python tools/stress/run.py --tree <worktree> --surface <name|all|a,b,...> --count N --seed S --out <dir>
                               [--quick] [--workers 2] [--scale-by-cost] [--keep-passes]

A bad input must end as a recorded refusal (refusal.LayoutError and its subclasses, a card's recorded refusal, or a
renderer's logged decline), never as a broken drawing. Every case is rendered through the surface's real renderer
from <tree>/src (tools/stress/surfaces.py) and judged by tools/stress/invariants.py. Seeded and deterministic: the
same --seed and --count draw the same cases. No network (sockets refused in every worker), no paid API, no .env.

Output in --out:
- report.json: per surface the cases, passes, refusals (class, code, reason), failures (class, detail, the case as a
  minimal repro: `python tools/stress/run.py --tree <tree> --repro <out>/cases/<id>.json`), notes and seconds;
- cases/<id>.json: every failing case's input, replayable on its own;
- failures.jpg: a contact sheet of the failures only (each delivered frame, labelled with its id and class);
- frames/<id>__<image>.jpg: the failing frames at half size.

--quick: 3 cases per surface, 1 for the surfaces slower than 300 s a case (route, end screen); every lane runs
this before it can freeze. --scale-by-cost: --count is the count of a
1-second surface; slower surfaces (surfaces.COST) run proportionally fewer, at least 3.
At most 2 worker processes (--workers is capped at 2): other agents share the machine.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_TREE: Path | None = None


def _offline(*_a, **_k):
    raise RuntimeError("stress harness: network is off")


def _init(tree: str, out: str) -> None:
    global _TREE
    _TREE = Path(tree)
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_STRESS="1")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    for p in (str(HERE), str(_TREE)):
        if p in sys.path:
            sys.path.remove(p)
    sys.path.insert(0, str(_TREE))
    sys.path.insert(0, str(HERE))
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s",
                        handlers=[logging.FileHandler(Path(out) / f"worker_{os.getpid()}.log", encoding="utf-8")])
    from src import geo
    # a private copy of the place cache: a lookup may write it, never into the dev checkout or the tree
    priv = Path(out) / "_geo_cache.json"
    if not priv.exists():
        src = Path(os.environ.get("RR_STRESS_GEO_CACHE") or
                   r"C:\Users\omokolade\GitHub\relic-and-riddle\data\geo_cache.json")
        priv.write_bytes(src.read_bytes() if src.is_file() else b"{}")
    geo.CACHE_FILE = priv
    import capture
    capture.install()


def _thumb(im, label: str, width: int = 360):
    from PIL import Image, ImageDraw, ImageFont
    im = im.convert("RGB")
    scale = width / im.width
    t = im.resize((width, max(1, round(im.height * scale))), Image.LANCZOS)
    f = ImageFont.load_default(size=14)
    lines = [label[i:i + 44] for i in range(0, min(len(label), 44 * 4), 44)]
    out = Image.new("RGB", (width, t.height + 18 * len(lines) + 6), (24, 24, 24))
    out.paste(t, (0, 0))
    d = ImageDraw.Draw(out)
    for k, ln in enumerate(lines):
        d.text((4, t.height + 3 + 18 * k), ln, font=f, fill=(255, 230, 120))
    return out


def run_case(args) -> dict:
    case, out = args
    import invariants
    import surfaces
    out = Path(out)
    work = out / "_work" / str(os.getpid()) / case["id"]
    t0 = time.perf_counter()
    ctx = surfaces.drive(case, work)
    try:
        fails = invariants.check(ctx)
    except Exception as e:  # noqa: BLE001  a check that cannot run is the harness's failure, said so
        import traceback
        fails = [{"cls": "HARNESS", "detail": f"{type(e).__name__}: {e} {traceback.format_exc()[-600:]}"}]
    status = invariants.classify(ctx, fails)
    res = {"id": case["id"], "surface": case["surface"], "status": status, "seconds": round(time.perf_counter() - t0, 2),
           "refusal": ctx.refusal, "failures": fails, "notes": ctx.notes[:12], "recorded_drops": ctx.recorded_drops[:12],
           "warnings": [w for w in getattr(ctx, "warnings", []) if w.startswith("src.")][:8],
           "classes": {"hostile": case["copy"][case.get("hostile_field", 0)]["cls"],
                       "copy": [c["cls"] for c in case["copy"]], "figure": case["figure"]["cls"],
                       "ground": [g["kind"] for g in case["ground"]][:2],
                       **({"scale": case["scale"]["cls"]} if "scale" in case else {}),
                       **({"map": case["map"]["cls"]} if "map" in case else {})},
           "frames": []}
    if status == "fail":
        (out / "cases").mkdir(parents=True, exist_ok=True)
        (out / "cases" / f"{case['id']}.json").write_text(json.dumps(case, indent=1, ensure_ascii=False),
                                                          encoding="utf-8", newline="\n")
        (out / "frames").mkdir(parents=True, exist_ok=True)
        label = f"{case['id']} {','.join(sorted({f['cls'] for f in fails}))}"
        for im in ctx.images[:4]:
            p = out / "frames" / f"{case['id']}__{im['name']}.jpg"
            _thumb(im["im"], label).save(p, quality=85)
            res["frames"].append(p.name)
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    return res


def contact_sheet(out: Path, results: list[dict]) -> None:
    from PIL import Image
    tiles = []
    for r in results:
        for f in r.get("frames") or []:
            p = out / "frames" / f
            if p.is_file():
                tiles.append(Image.open(p).convert("RGB"))
    if not tiles:
        return
    cols = 8
    w = max(t.width for t in tiles)
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    hs = [max(t.height for t in row) for row in rows]
    sheet = Image.new("RGB", (w * cols, sum(hs)), (12, 12, 12))
    y = 0
    for row, h in zip(rows, hs):
        for k, t in enumerate(row):
            sheet.paste(t, (k * w, y))
        y += h
    sheet.save(out / "failures.jpg", quality=82)


def summarise(results: list[dict], meta: dict) -> dict:
    by: dict[str, dict] = {}
    for r in results:
        s = by.setdefault(r["surface"], {"cases": 0, "passes": 0, "refusals": [], "failures": [], "seconds": 0.0,
                                         "failure_classes": {}, "refusal_classes": {}})
        s["cases"] += 1
        s["seconds"] = round(s["seconds"] + r["seconds"], 2)
        if r["status"] == "pass":
            s["passes"] += 1
        elif r["status"] == "refused":
            ref = r["refusal"] or {}
            key = f"{ref.get('class')}:{ref.get('code') or ''}"
            s["refusal_classes"][key] = s["refusal_classes"].get(key, 0) + 1
            s["refusals"].append({"id": r["id"], **ref, "classes": r["classes"]})
        else:
            for f in r["failures"]:
                s["failure_classes"][f["cls"]] = s["failure_classes"].get(f["cls"], 0) + 1
            s["failures"].append({"id": r["id"], "failures": r["failures"], "classes": r["classes"],
                                  "notes": r["notes"], "recorded_drops": r["recorded_drops"],
                                  "repro": f"cases/{r['id']}.json", "frames": r["frames"]})
    return {**meta, "surfaces": by}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", type=Path, default=HERE.parents[1])
    ap.add_argument("--surface", default="all")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--quick", action="store_true", help="3 cases per surface")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--scale-by-cost", action="store_true")
    ap.add_argument("--budget", type=float, default=0.0,
                    help="seconds per surface: a surface runs min(--count, budget / its measured cost), at least 3")
    ap.add_argument("--repro", type=Path, help="replay one case file (cases/<id>.json) and print its result")
    a = ap.parse_args(argv)
    tree = a.tree.resolve()
    out = (a.out or Path.cwd() / "stress_out").resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE))
    import surfaces
    if a.repro:
        case = json.loads(a.repro.read_text(encoding="utf-8"))
        _init(str(tree), str(out))
        res = run_case((case, str(out)))
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0 if res["status"] != "fail" else 1
    names = list(surfaces.SURFACES) if a.surface == "all" else [s for s in a.surface.split(",") if s]
    unknown = [s for s in names if s not in surfaces.SURFACES]
    if unknown:
        ap.error(f"unknown surface(s) {unknown}; one of {', '.join(surfaces.SURFACES)}")
    cases = []
    for s in names:
        n = (3 if surfaces.COST.get(s, 1) < 300 else 1) if a.quick else a.count
        if a.scale_by_cost and not a.quick:
            n = max(3, round(a.count / max(1, surfaces.COST.get(s, 1))))
        if a.budget and not a.quick:
            n = max(3, min(a.count, int(a.budget / max(0.1, surfaces.COST.get(s, 1)))))
        cases += surfaces.build(s, n, a.seed)
    workers = max(1, min(2, a.workers))
    t0 = time.perf_counter()
    results: list[dict] = []
    # slow surfaces first, so the two workers finish together
    order = sorted(cases, key=lambda c: -surfaces.COST.get(c["surface"], 1))
    meta = {"tree": str(tree), "seed": a.seed, "count": a.count, "quick": a.quick, "workers": workers,
            "cases": len(cases)}
    partial = out / "report.partial.jsonl"
    with partial.open("w", encoding="utf-8", newline="\n") as log, \
            mp.get_context("spawn").Pool(workers, _init, (str(tree), str(out)), maxtasksperchild=25) as pool:
        for r in pool.imap_unordered(run_case, [(c, str(out)) for c in order]):
            results.append(r)
            log.write(json.dumps(r, ensure_ascii=False) + "\n")
            log.flush()
            print(f"[{len(results)}/{len(cases)}] {r['id']} {r['status']} {r['seconds']}s "
                  + ",".join(sorted({f['cls'] for f in r['failures']})), flush=True)
    results.sort(key=lambda r: r["id"])
    rep = summarise(results, {**meta, "wall_seconds": round(time.perf_counter() - t0, 1)})
    (out / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    contact_sheet(out, results)
    bad = sum(len(v["failures"]) for v in rep["surfaces"].values())
    print(f"{len(results)} cases, {bad} failing, {round(time.perf_counter() - t0)} s -> {out / 'report.json'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
