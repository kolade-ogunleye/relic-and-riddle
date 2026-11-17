"""QF-82 on real runs: what the run folders hold, which styles went out, and a re-render of each recorded cover.

Usage: measure_real.py REPO_ROOT OUTPUT_DIR [--assets ASSETS_DIR] [--against OTHER_REPO_ROOT]

OUTPUT_DIR holds run_* folders (the development output, or a copy of the production sample taken outside the
repository). Prints counts and medians only: no run names, file names, hashes or copy, so it is safe on the
production sample (decisions: counts only). Writes nothing outside a temporary directory.

For every run whose delivered cover has a recorded recipe (still_layout.json) and a verified script, the cover is
drawn again with the code at REPO_ROOT, in the recorded style, from the recorded hero (else the topic's pool photo
of the same name under ASSETS_DIR). With --against, the same renders are made with the code at OTHER_REPO_ROOT
and the delivered cover and profile-grid tile are compared byte for byte (only the number identical is printed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VARIANTS = ("thumbnail_variant_a_blueprint.jpg", "thumbnail_variant_b_intrigue.jpg", "thumbnail_variant_c_minimal.jpg")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def layout(run: Path) -> dict:
    try:
        return json.loads((run / "still_layout.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def hero_for(run: Path, recipe: dict, assets: Path | None) -> Path | None:
    recorded = Path(str(recipe.get("hero") or "").replace("\\", "/"))
    if recorded.name and recorded.is_file():
        return recorded
    if assets is None or not recorded.name:
        return None
    topic = re.sub(r"^run_|_\d+$", "", run.name)
    stem = re.sub(r"_[0-9a-f]{16}$", "", recorded.stem)
    found = sorted((assets / topic).glob(f"{stem}.*")) if (assets / topic).is_dir() else []
    return found[0] if found else None


def jobs(output: Path, assets: Path | None) -> tuple[list[dict], dict]:
    runs = sorted(p for p in output.glob("run_*") if p.is_dir())
    counts = {"runs": len(runs), "runs_with_cover": 0, "runs_with_variants": 0, "variant_files": 0,
              "variant_bytes_per_run": [], "delivered_style": {}, "cover_identical_to_a_variant": 0,
              "cover_identical_to_a_variant_of_another_style": 0, "rerender_skipped_no_recipe_or_script": 0,
              "rerender_skipped_no_hero": 0}
    todo = []
    for run in runs:
        cover = run / "thumbnail_cover.jpg"
        if not cover.is_file():
            continue
        counts["runs_with_cover"] += 1
        present = [run / name for name in VARIANTS if (run / name).is_file()]
        if present:
            counts["runs_with_variants"] += 1
            counts["variant_files"] += len(present)
            counts["variant_bytes_per_run"].append(sum(p.stat().st_size for p in present))
        records = layout(run)
        recipe = (records.get(cover.name) or {}).get("recipe") or {}
        style = recipe.get("style") or "no record"
        counts["delivered_style"][style] = counts["delivered_style"].get(style, 0) + 1
        digest = sha(cover)
        same = [p for p in present if sha(p) == digest]
        if same:
            counts["cover_identical_to_a_variant"] += 1
            variant_style = ((records.get(same[0].name) or {}).get("recipe") or {}).get("style")
            if recipe and variant_style and variant_style != recipe.get("style"):
                counts["cover_identical_to_a_variant_of_another_style"] += 1
        script = run / "verified_script.json"
        if not recipe or not script.is_file():
            counts["rerender_skipped_no_recipe_or_script"] += 1
            continue
        hero = hero_for(run, recipe, assets)
        if hero is None:
            counts["rerender_skipped_no_hero"] += 1
            continue
        todo.append({"hero": str(hero), "script": str(script), "style": recipe.get("style") or "badge"})
    sizes = counts.pop("variant_bytes_per_run")
    counts["median_variant_bytes_per_run"] = int(statistics.median(sizes)) if sizes else 0
    counts["variant_bytes_total"] = sum(sizes)
    return todo, counts


def worker(root: Path, todo: list[dict]) -> list[dict]:
    """Renders each job with the code at `root` (run in its own process, so each commit's modules load alone)."""
    sys.path.insert(0, str(root))
    import logging
    logging.disable(logging.CRITICAL)
    from src.visual_engine import VisualEngine
    out = []
    for job in todo:
        payload = json.loads(Path(job["script"]).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="qf82-real-") as directory:
            run = Path(directory) / "run"
            t0 = time.perf_counter()
            try:
                cover, grid = VisualEngine().generate_thumbnails(Path(job["hero"]), payload, run, style=job["style"])
            except Exception as e:  # noqa: BLE001 - one unreadable input must not hide the others
                out.append({"error": type(e).__name__})
                continue
            seconds = time.perf_counter() - t0
            names = sorted(p.name for p in run.glob("*.jpg"))
            recorded = (layout(run).get("thumbnail_cover.jpg") or {}).get("recipe") or {}
            out.append({"seconds": seconds, "jpgs": len(names),
                        "variants": sum(name.startswith("thumbnail_variant_") for name in names),
                        "style": recorded.get("style"), "cover": sha(cover), "grid": sha(grid)})
    return out


def render(root: Path, todo: list[dict]) -> list[dict]:
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(root)],
                          input=json.dumps(todo), capture_output=True, text=True, cwd=str(root), check=True)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def summary(results: list[dict]) -> dict:
    ok = [r for r in results if "error" not in r]
    return {"rendered": len(ok), "errors": len(results) - len(ok),
            "jpgs_per_run": sorted({r["jpgs"] for r in ok}),
            "variant_files_written": sum(r["variants"] for r in ok),
            "median_seconds": round(statistics.median(r["seconds"] for r in ok), 2) if ok else None}


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--worker":
        print(json.dumps(worker(Path(sys.argv[2]).resolve(), json.loads(sys.stdin.read()))))
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("output")
    ap.add_argument("--assets")
    ap.add_argument("--against")
    args = ap.parse_args()
    todo, counts = jobs(Path(args.output), Path(args.assets) if args.assets else None)
    report = {"folders": counts, "rerender_jobs": len(todo)}
    mine = render(Path(args.root).resolve(), todo)
    report["this_code"] = summary(mine)
    if args.against:
        other = render(Path(args.against).resolve(), todo)
        report["other_code"] = summary(other)
        pairs = [(a, b) for a, b in zip(mine, other) if "error" not in a and "error" not in b]
        report["compared"] = len(pairs)
        report["delivered_cover_identical"] = sum(a["cover"] == b["cover"] for a, b in pairs)
        report["profile_grid_identical"] = sum(a["grid"] == b["grid"] for a, b in pairs)
        report["delivered_style_identical"] = sum(a["style"] == b["style"] for a, b in pairs)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
