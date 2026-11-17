"""QF-58b: the planner on the real Reel plans with the real illustration caps. Prints JSON and writes nothing in
the repository (generated stand-ins and crops go to a temporary folder). No network, no model.

    python docs/reviews/quality-fixes-2026-11/QF-58b/caps.py <repo root> <assets copy> <env name> <runs dir>
        [<reels file>]

<repo root> is the code measured (the parent 1c4533d8 or this row). <assets copy>: a COPY of the environment's
assets with its photos; <env name>: "dev" or "production_sample"; <runs dir>: that environment's output folder
(output/ of the development checkout; the production sample's output/), read only for each run's
script_metadata.json picture_fit record. The plans are QF-58/real_reels.json (committed: the development Reels), or
the <reels file> given. Production sample (decisions/PROD-SAMPLE-counts-only.md): a copy of its assets and output/,
and the plans QF-58's extract_reels.py made from a copy of it as <reels file>, all outside the repository; the
output is counts only for every environment.

Each real Reel plan (QF-58/real_reels.json) of <env> on a ready pool, admitted by the gate as it is, through the
planner's own repair (fit_to_lines with the cascade, then hold_for_short_pool, then reel_photo_coverage), with every
look mocked: a line marked unshown is shown by no picture, every other line by its picture. Two scenarios:
  recorded: the beats the real run's own picture-fit check found unshown (script_metadata.json picture_fit.unshown),
            all at once, one plan per Reel (only runs that recorded a check);
  each_beat: each photo beat after the opening in turn, one at a time.
Illustration modes: off; on with the real caps and pass rate 0.5; on with the real caps and pass rate 1.0. The real
caps: illustration_allowed (MAX_GAP_IMAGES_PER_RUN, IMAGE_GEN_MAX_RUNTIME_SHARE, IMAGE_GEN_MIN_SHOT_SECONDS, not next
to another, never the hook) and IMAGE_GEN_MAX_CALLS_PER_RUN, each attempt taking IMAGE_GEN_CANDIDATES requests
(rejected ones count). Whether an attempt passes: a fixed hash of (run, beat) against the pass rate.
"""

import contextlib
import hashlib
import io
import json
import logging
import statistics
import sys
import tempfile
import warnings
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT, ASSETS, ENV, RUNS = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)
warnings.simplefilter("ignore")
Image.MAX_IMAGE_PIXELS = None
from src import beat_images, config, picture_coverage, scene_planner
from src.visual_engine import MissingAssetsError, VisualEngine

UNSHOWN = "Zorvath quillen drabbet morskel vintrope"
REAL_REELS = Path(sys.argv[5]).resolve() if len(sys.argv) > 5 else HERE.parent / "QF-58" / "real_reels.json"


def noise(path: Path, seed: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.random.default_rng(seed).integers(0, 256, (40, 40, 3), dtype=np.uint8)).save(path)
    return path


def reason(problem: str) -> str:
    if problem.startswith("a photograph on screen over"):
        return "hold over 6 s"
    if "still beats" in problem or "Reel of photos only" in problem:
        return "too few distinct photos"
    if problem.startswith("a photo shown again"):
        return "repeat"
    if problem.startswith("more than one detail crop"):
        return "crop cap"
    return problem.split(":")[0]


def gate(scenes, durations):
    return scene_planner.reel_photo_coverage(scenes, durations)


@contextlib.contextmanager
def world(cache_dir: Path, mode: str, run_key: list):
    real_crop, crops = picture_coverage.detail_crop, {}

    def crop(photo, out_dir, used, aspect, min_width=0):
        key = (str(photo), tuple(tuple(u) for u in used), aspect, min_width)
        if key not in crops:
            crops[key] = real_crop(photo, out_dir, used, aspect, min_width=min_width)
        return crops[key]

    from src import photo_pool
    real_sig, sigs = photo_pool.signature, {}

    def sig(path):
        if str(path) not in sigs:
            sigs[str(path)] = real_sig(path)
        return dict(sigs[str(path)])

    def look(path, query, size=512, ask=None):
        bad = UNSHOWN in str(query)
        return {"checked": True, "shows": not bad, "burned_in_text": False, "reason": "not that" if bad else "it"}

    rate = {"off": None, "on_p0.5": 0.5, "on_p1.0": 1.0}[mode]
    per_attempt = int(getattr(config, "IMAGE_GEN_CANDIDATES", 4))
    limit = int(getattr(config, "IMAGE_GEN_MAX_CALLS_PER_RUN", 8))

    def illustrate(k, line, out, durations, report, ctx, pick, entry, visual_of, tiered):
        if rate is None:
            return False
        allowed = {"enabled": True, "cap": int(getattr(config, "MAX_GAP_IMAGES_PER_RUN", 2)), "protected": [],
                   "total": float(sum(durations)), "long_form": False}
        if scene_planner.illustration_allowed(k, out, durations, allowed, report.get("stopped") or ""):
            return False
        used = report.setdefault("_calls", 0)
        if used >= limit:
            return False
        report["_calls"] = min(limit, used + per_attempt)
        h = int(hashlib.sha256(f"{run_key[0]}|{k}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if h >= rate:
            return False
        art = noise(cache_dir / f"gap_gen_{hashlib.sha256(run_key[0].encode()).hexdigest()[:12]}_{k}.png", 7000 + k)
        out[k] = art
        entry["fallback"], entry["filled_by"] = "generated_art", art.name
        report.setdefault("generated", []).append({"beat": k + 1, "to": art.name})
        tiered(k, "reconstruction", art)
        report["unshown"].append(entry)
        return True

    patches = [patch.object(beat_images, "picture_look", look),
               patch.object(beat_images, "FIT_CACHE_FILE", cache_dir / "picture_fit.json"),
               patch.object(config, "PICTURE_VISION_CHECK", True),
               patch.object(config, "GENERATIVE_GAP_FILLING", False),
               patch.object(scene_planner, "_illustrate", illustrate),
               patch.object(picture_coverage, "detail_crop", crop), patch.object(photo_pool, "signature", sig)]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


def recorded_unshown(run_name: str) -> list[int] | None:
    try:
        meta = json.loads((RUNS / run_name / "script_metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fit = meta.get("picture_fit") if isinstance(meta, dict) else None
    if not isinstance(fit, dict):
        return None
    return sorted({int(u["beat"]) for u in fit.get("unshown") or [] if isinstance(u.get("beat"), int)})


def main() -> None:
    runs = [r for r in json.loads(REAL_REELS.read_text(encoding="utf-8"))["runs"] if r["env"] == ENV]
    engine = VisualEngine(ASSETS)
    pools, result = {}, {}
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        plans = []
        for index, run in enumerate(runs):
            tid = run["topic"]
            if tid not in pools:
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        pools[tid] = engine.get_scene_sequence({"id": tid})
                except MissingAssetsError:
                    pools[tid] = None
            if pools[tid] is None or len(run["beats"]) != len(run["scenes"]):
                continue
            folder = base / f"r{index}"
            scenes = []
            for k, scene in enumerate(run["scenes"]):
                if scene["kind"] == "photo":
                    real = ASSETS / tid / str(scene.get("pool_file") or "")
                    path = real if scene.get("pool_file") and real.is_file() else \
                        noise(folder / f"stand_in_{scene['picture']}.png", index * 1000 + scene["picture"])
                else:
                    path = folder / (f"graphic_{k}.mp4" if scene["kind"] == "graphic" else f"footage_{k}.mp4")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"")
                scenes.append(path)
            durations = [b["end"] - b["start"] for b in run["beats"]]
            with world(base, "off", [run["run"]]):
                if not gate(scenes, durations)["ok"]:
                    continue
            line_beat = {scenes[k]: k + 1 for k, s in enumerate(run["scenes"]) if s.get("beat_image")}
            candidates = list(dict.fromkeys(list(pools[tid]) + [s for s in scenes if not scene_planner.is_video(s)]))
            plans.append((index, run, folder, scenes, durations, line_beat, candidates))
        for mode in ("off", "on_p0.5", "on_p1.0"):
            for scenario in ("recorded", "each_beat"):
                stops, reasons, distinct, crops, illus, filled = Counter(), Counter(), [], [], [], Counter()
                for index, run, folder, scenes, durations, line_beat, candidates in plans:
                    photo_beats = [k for k in range(1, len(scenes)) if scene_planner._shot_kind(scenes[k]) == "photo"]
                    if scenario == "recorded":
                        marked = recorded_unshown(run["run"])
                        if marked is None:
                            continue
                        cases = [[b - 1 for b in marked if 0 < b - 1 < len(scenes)
                                  and scene_planner._shot_kind(scenes[b - 1]) == "photo"]]
                    else:
                        cases = [[k] for k in photo_beats]
                    for case in cases:
                        print(f"{mode} {scenario} {run['run']} {case}", file=sys.stderr, flush=True)
                        key = [f"{run['run']}|{'-'.join(map(str, case))}"]
                        lines = [f"The {run['topic'].replace('_', ' ')} part {i + 1}." for i in range(len(scenes))]
                        for k in case:
                            lines[k] = f"{UNSHOWN} {k}."
                        beats = [{"start": b["start"], "end": b["end"], "text": t} for b, t in zip(run["beats"], lines)]
                        with world(base, mode, key):
                            out, report = scene_planner.fit_to_lines(
                                beats, list(scenes), candidates, run["topic"], durations=list(durations),
                                max_calls=10_000, line_beat=line_beat,
                                work_dir=folder / f"w{mode}_{scenario}_{'-'.join(map(str, case)) or 'none'}")
                            for k in case:
                                t = next((r["tier"] for r in report.get("tiers") or [] if r.get("beat") == k + 1), None)
                                if t is None:
                                    t = "held" if any(r.get("beat") == k + 1 for r in report.get("held") or []) else (
                                        "own matched photo kept" if any(r.get("beat") == k + 1
                                                                        for r in report.get("kept") or [])
                                        else "pick kept")
                                filled[t] += 1
                            fits = {b - 1: True for b in report.get("fits") or []}
                            fits.update({u["beat"] - 1: False for u in report.get("unshown") or []
                                         if isinstance(u.get("beat"), int)})
                            available = len({scene_planner.picture_id(c) for c in candidates
                                             if not scene_planner.is_video(c)})
                            _, held_scenes, held_durations, _, _ = scene_planner.hold_for_short_pool(
                                beats, out, list(durations), fits=fits,
                                check=lambda picture, line: UNSHOWN not in line, available=available)
                            verdict = gate(held_scenes, held_durations)
                        stops["plans"] += 1
                        stops["unshown_beats"] += len(case)
                        stops["stopped_after_the_voice"] += not verdict["ok"]
                        for r in {reason(p) for p in verdict["problems"]}:
                            reasons[r] += 1
                        distinct.append(verdict["distinct"])
                        crops.append(verdict["detail_crops"])
                        illus.append(sum(scene_planner.is_illustration(Path(p)) for p in held_scenes))
                result[f"{mode}/{scenario}"] = {
                    **dict(stops), "reasons": dict(reasons), "filled_by": dict(filled),
                    "distinct_min": min(distinct) if distinct else None,
                    "distinct_median": statistics.median(distinct) if distinct else None,
                    "crops_per_reel": dict(sorted(Counter(crops).items())),
                    "illustrated_per_reel": dict(sorted(Counter(illus).items()))}
    print(json.dumps({"env": ENV, "results": result}, indent=1))


if __name__ == "__main__":
    main()
