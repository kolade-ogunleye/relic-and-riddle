"""QF-58b measurement. Prints JSON and writes nothing in the repository. No network, no model.

    python docs/reviews/quality-fixes-2026-11/QF-58b/measure.py <repo root> [<assets dir> <env name> [<reels file>]]

<repo root> is the code measured (the parent 1c4533d8 or this row), so the same script measures both.

The Reel plans are QF-58/real_reels.json (the development Reels), or the <reels file> given. Production sample
(decisions/PROD-SAMPLE-counts-only.md): pass a COPY of its assets, the env name "production_sample" and the plans
QF-58's extract_reels.py made from a copy of it as <reels file>, all kept outside the repository. Per-item rows
(runs, pools, crop sources) are printed only for the repository's own development data (env name "dev" and no
<reels file>); any other input prints counts only.

1. Generated plans through the Reel plan gate (reel_photo_coverage): crops in a Reel with a graphic (review 6,
   finding 2), and the cascade's detail crop (scene_planner._cascade_fill) beside its own photograph (finding 1).
2. With <assets dir> (a COPY of an environment's assets with its photos) and <env name> ("dev" or
   "production_sample"): every real Reel plan of that environment (QF-58/real_reels.json or <reels file>) whose topic's pool is
   ready, rebuilt from the pool's own files (pool_file; a generated stand-in where the file is gone or the beat was
   a beat image, graphics and film as stand-ins). Each photo beat after the opening is treated in turn as a line no
   photo shows: the cascade's detail-crop tier fills it from the pool (get_scene_sequence, as the planner gets it),
   and the plan with that crop goes through the gate, which runs after the voice. Reported: plans admitted as they
   are, crop plans made, admitted and refused (gate stops after the voice), by reason, and crops beside their own
   photograph. This is a what-if over every photo beat, not a forecast of how often a line goes unshown.
3. With <assets dir>: review 6's probe (probe_cascade.py) on every ready pool: ten stills all in use, 4.5 s beats,
   beats 2, 6 and 9 in turn filled by the cascade's crop (amber_room_heist and kailasa_temple_monolith on dev).
4. With <assets dir>: the planner's own run (planner_runs) on the same real plans and beats: fit_to_lines with
   the cascade and hold_for_short_pool, every look mocked (only the beat's line UNSHOWN, which names nothing any
   record names, is not shown); the paid illustration tier either replaced by a stand-in where
   illustration_allowed admits one, or off (no generation passes). The planner's line_beat is the plan's beat images
   (each found for its beat's line). Which tier fills the beat (QF-58b round 3: the beat's own matched photo kept
   where the held shot would be refused), whether a kept photo repeats elsewhere, and the gate's verdict.
"""

import contextlib
import io
import json
import logging
import sys
import tempfile
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(sys.argv[1]).resolve()
ASSETS = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
ENV = sys.argv[3] if len(sys.argv) > 3 else None
REELS = Path(sys.argv[4]).resolve() if len(sys.argv) > 4 else None
PER_ITEM = (ASSETS is None or ENV == "dev") and REELS is None   # per-item rows only for the repository's dev data
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)
warnings.simplefilter("ignore")
Image.MAX_IMAGE_PIXELS = None
from src import config, picture_coverage, scene_planner
from src.lineage import record_derived

LINE = "Today, bridges crack within decades across modern highways."
UNSHOWN = "Zorvath quillen drabbet morskel vintrope."   # names nothing any photo, film or map record names


def reason(problem: str) -> str:
    if problem.startswith("a photograph on screen over"):
        return "hold over 6 s"
    if "still beats" in problem:
        return "crops below the distinct requirement"
    if "Reel of photos only" in problem:
        return "photos-only floor"
    if problem.startswith("more than one detail crop"):
        return "crop cap"
    if problem.startswith("a photo shown again"):
        return "repeat"
    return problem.split(":")[0]


def noise(path: Path, seed: int, size=(40, 40)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.random.default_rng(seed).integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)).save(path)
    return path


def crop_of(photo: Path, folder: Path, n: int) -> Path:
    out = folder / "detail_crops" / str(n) / photo.name
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(photo) as image:
        image.crop((0, 0, 20, 36)).save(out)
    record_derived(out, [photo], picture_coverage.DETAIL_OPERATION, {"box": [0, 0, 0.5, 0.9]})
    return out


def synthetic() -> dict:
    gate = scene_planner.reel_photo_coverage
    out = {}
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        p = [noise(base / "p" / f"{i}.png", i) for i in range(9)]
        graphic = noise(base / "graphic_map.png", 99)
        plans = {
            "graphic_5_photos_4_crops_of_them": [p[0], crop_of(p[0], base, 1), p[1], crop_of(p[1], base, 2), graphic,
                                                 p[2], crop_of(p[2], base, 3), p[3], crop_of(p[3], base, 4), p[4]],
            "graphic_6_photos_3_crops_of_unshown": [p[0], crop_of(p[6], base, 5), p[1], crop_of(p[7], base, 6),
                                                    graphic, p[2], crop_of(p[8], base, 7), p[3], p[4], p[5]],
            "graphic_8_photos_1_crop": p[:4] + [graphic] + p[4:8] + [crop_of(p[1], base, 8)],
            "graphic_9_photos_1_crop": p[:4] + [graphic] + p[4:] + [crop_of(p[2], base, 9)],
            "graphic_8_photos": p[:4] + [graphic] + p[4:8],
            "photos_only_9_photos_1_crop": p + [crop_of(p[3], base, 10)],
            "photos_only_8_photos_1_crop": p[:8] + [crop_of(p[0], base, 11)],
        }
        out["gate_admitted"] = {}
        for name, plan in plans.items():
            verdict = gate(plan, [3.0] * len(plan))
            out["gate_admitted"][name] = {"ok": verdict["ok"], "distinct": verdict["distinct"],
                                          "required": verdict.get("required", "n/a")}
        # The cascade's crop beside its own photograph: 4 approved photos, all in use, beat 2 unshown.
        topic = base / "topic"
        topic.mkdir()
        images = []
        for i in range(4):
            name = f"{i + 1:02d}_commons.jpg"
            noise(topic / name, 10 + i, size=(1400, 2000))
            images.append({"file": name, "title": f"File:{name}", "license": "CC0",
                           "source_url": f"https://example.org/{name}",
                           "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": "it"}})
        (topic / "credits.json").write_text(json.dumps({"reviewed": True, "images": images}), encoding="utf-8")
        scene_planner._folder_credits.cache_clear()
        pool = [topic / i["file"] for i in images]
        out["cascade_beat_2_after_the_opening"] = {}
        for seconds in (4.5, 3.0):
            durations = [seconds] * 4
            got = scene_planner._cascade_fill(1, LINE, list(pool), pool, durations, {}, base / f"run{seconds}", "",
                                              only=(picture_coverage.DETAIL_CROP,))
            plan = list(pool)
            if got:
                plan[1] = got[1]
            verdict = gate(plan, durations)
            out["cascade_beat_2_after_the_opening"][f"{seconds:g}s_beats"] = {
                "crop_of": Path(scene_planner.picture_id(got[1])).name if got else None,
                "beside_its_photograph": bool(got) and scene_planner.picture_id(got[1]) == scene_planner.picture_id(
                    pool[0]),
                "long_holds": verdict["long_holds"]}
    return out


@contextlib.contextmanager
def memo_crops():
    """detail_crop is a pure function of (photo, regions already shown, aspect, width); remembered so a photo's crop
    is computed once per run of this script, not once per beat."""
    real = picture_coverage.detail_crop
    cache: dict = {}

    def remembered(photo, out_dir, used, aspect, min_width=0):
        key = (str(photo), tuple(tuple(u) for u in used), aspect, min_width)
        if key not in cache:
            cache[key] = real(photo, out_dir, used, aspect, min_width=min_width)
        return cache[key]

    from src import photo_pool
    real_signature, signatures = photo_pool.signature, {}

    def signature(path):   # the gate's photo fingerprint: a pure function of the file, which does not change here
        key = str(path)
        if key not in signatures:
            signatures[key] = real_signature(path)
        return dict(signatures[key])

    picture_coverage.detail_crop, photo_pool.signature = remembered, signature
    try:
        yield
    finally:
        picture_coverage.detail_crop, photo_pool.signature = real, real_signature


def reel_runs() -> list[dict]:
    """The real Reel plans of ENV: QF-58's committed development plans, or the <reels file> given."""
    source = REELS or HERE.parent / "QF-58" / "real_reels.json"
    return [r for r in json.loads(source.read_text(encoding="utf-8"))["runs"] if r["env"] == ENV]


def progress(text: str, run: dict) -> None:
    print(f"{text} {run['run']}" if PER_ITEM else text, file=sys.stderr, flush=True)


def real_plans() -> dict:
    from src.visual_engine import MissingAssetsError, VisualEngine
    gate = scene_planner.reel_photo_coverage
    runs = reel_runs()
    engine = VisualEngine(ASSETS)
    pools, rows = {}, []
    totals = Counter()
    reasons, adjacent_reasons = Counter(), Counter()
    with tempfile.TemporaryDirectory() as temporary, memo_crops():
        base = Path(temporary)
        for index, run in enumerate(runs):
            tid = run["topic"]
            progress(f"{index + 1}/{len(runs)}", run)
            if tid not in pools:
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        pools[tid] = engine.get_scene_sequence({"id": tid})
                except MissingAssetsError:
                    pools[tid] = None
            pool = pools[tid]
            if pool is None or len(run["beats"]) != len(run["scenes"]):
                totals["plans_skipped_pool_not_ready_or_beats_differ"] += 1
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
            own = gate(scenes, durations)
            totals["plans"] += 1
            totals["plans_admitted_as_they_are"] += own["ok"]
            row = {"run": run["run"], "topic": tid, "beats": len(scenes), "admitted": own["ok"], "crops": []}
            if not own["ok"]:
                rows.append(row)
                continue
            for k in range(1, len(scenes)):
                if scene_planner._shot_kind(scenes[k]) != "photo":
                    continue
                got = scene_planner._cascade_fill(k, LINE, list(scenes), list(pool), durations, {}, folder / "run",
                                                  "", only=(picture_coverage.DETAIL_CROP,))
                totals["photo_beats_tried"] += 1
                if got is None:
                    totals["no_crop_planned"] += 1
                    continue
                source = scene_planner.picture_id(got[1])
                beside = any(0 <= j < len(scenes) and scene_planner._shot_key(scenes[j]) == source
                             for j in (k - 1, k + 1))
                plan = list(scenes)
                plan[k] = got[1]
                verdict = gate(plan, durations)
                totals["crop_plans"] += 1
                totals["crop_plans_beside_own_photograph"] += beside
                if verdict["ok"]:
                    totals["crop_plans_admitted"] += 1
                else:
                    totals["crop_plans_refused_after_the_voice"] += 1
                    for r in {reason(p) for p in verdict["problems"]}:
                        reasons[r] += 1
                        if beside:
                            adjacent_reasons[r] += 1
                row["crops"].append({"beat": k + 1, "crop_of": Path(source).name, "beside": beside,
                                     "ok": verdict["ok"], "problems": verdict["problems"]})
            rows.append(row)
    return {"totals": dict(totals), "refusal_reasons": dict(reasons),
            "refusal_reasons_beside_own_photograph": dict(adjacent_reasons),
            "pools_ready": sorted(t for t, p in pools.items() if p is not None),
            "pools_not_ready": sorted(t for t, p in pools.items() if p is None), "runs": rows}


def full_pools() -> dict:
    """Review 6's probe (probe_cascade.py) on every ready pool with at least ten stills: its first ten, the pool the
    cascade crops from and all in use, one per beat at 4.5 s; beats 2, 6 and 9 in turn are a line no photo shows and
    take the cascade's crop (nine distinct photos stay after a crop, so the hold rule is what is
    measured)."""
    from src.visual_engine import MissingAssetsError, VisualEngine
    gate = scene_planner.reel_photo_coverage
    engine = VisualEngine(ASSETS)
    rows, totals, reasons = {}, Counter(), Counter()
    with tempfile.TemporaryDirectory() as temporary, memo_crops():
        for credits in sorted(ASSETS.glob("*/credits.json")):
            tid = credits.parent.name
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    media = engine.get_scene_sequence({"id": tid})
            except MissingAssetsError:
                continue
            use = [Path(m) for m in media if not scene_planner.is_video(Path(m))][:10]
            if len(use) < 10:
                totals["pools_under_ten_stills"] += 1
                continue
            totals["ready_pools"] += 1
            rows[tid] = []
            durations = [4.5] * 10
            for k in (1, 5, 8):
                out = list(use)
                got = scene_planner._cascade_fill(k, LINE, out, list(use), durations, {}, Path(temporary) / tid, "",
                                                  only=(picture_coverage.DETAIL_CROP,))
                totals["beats_tried"] += 1
                if got is None:
                    rows[tid].append({"beat": k + 1, "crop_of": None})
                    continue
                source = scene_planner.picture_id(got[1])
                plan = list(out)
                plan[k] = got[1]
                verdict = gate(plan, durations)
                beside = any(scene_planner._shot_key(plan[j]) == source for j in (k - 1, k + 1))
                totals["crop_plans"] += 1
                totals["crop_plans_beside_own_photograph"] += beside
                totals["crop_plans_admitted"] += verdict["ok"]
                totals["crop_plans_refused_after_the_voice"] += not verdict["ok"]
                for r in {reason(p) for p in verdict["problems"]}:
                    reasons[r] += 1
                rows[tid].append({"beat": k + 1, "crop_of": Path(source).name, "beside": beside,
                                  "ok": verdict["ok"], "problems": verdict["problems"]})
    return {"totals": dict(totals), "refusal_reasons": dict(reasons), "pools": rows}


@contextlib.contextmanager
def planner_world(cache_dir: Path, illustration: bool):
    """The planner's own run with no model: every look (beat_images.picture_look) says the picture shows its line,
    except on the line LINE; the illustration tier (a paid generation) is replaced by a stand-in labelled
    illustration wherever illustration_allowed admits one, recorded as "reconstruction (stand-in)"."""
    from unittest.mock import patch

    from src import beat_images

    def look(path, query, size=512, ask=None):
        bad = UNSHOWN in str(query)
        return {"checked": True, "shows": not bad, "burned_in_text": False, "reason": "not that" if bad else "it"}

    def illustrate(k, line, out, durations, report, ctx, pick, entry, visual_of, tiered):
        if not illustration:
            return False
        allowed = {"enabled": True, "cap": int(getattr(config, "MAX_GAP_IMAGES_PER_RUN", 2)), "protected": [],
                   "total": float(sum(durations)), "long_form": False}
        if scene_planner.illustration_allowed(k, out, durations, allowed, report.get("stopped") or ""):
            return False
        art = noise(cache_dir / f"gap_gen_{len(list(cache_dir.glob('gap_gen_*')))}.png", 7000 + k)
        out[k] = art
        entry["fallback"], entry["filled_by"] = "generated_art", art.name
        report.setdefault("generated", []).append({"beat": k + 1, "to": art.name})
        tiered(k, "reconstruction", art)
        report["unshown"].append(entry)
        return True

    with patch.object(beat_images, "picture_look", look), \
            patch.object(beat_images, "FIT_CACHE_FILE", cache_dir / "picture_fit.json"), \
            patch.object(config, "PICTURE_VISION_CHECK", True), \
            patch.object(config, "GENERATIVE_GAP_FILLING", False), \
            patch.object(scene_planner, "_illustrate", illustrate):
        yield


def planner_runs(illustration: bool) -> dict:
    """Every real Reel plan admitted as it is (on a ready pool, beats matching scenes): each photo beat after the
    opening in turn says LINE, which no photo shows. The planner's own repair runs (fit_to_lines with the cascade,
    then hold_for_short_pool as main.py calls it, with the same look), and the gate judges the result. Reported per
    beat: the tier that filled it (report["tiers"], "held" or "kept": the pick stays) and the gate's verdict."""
    from src.visual_engine import MissingAssetsError, VisualEngine
    gate = scene_planner.reel_photo_coverage
    runs = reel_runs()
    engine = VisualEngine(ASSETS)
    pools = {}
    tiers, outcomes, reasons, by_tier = Counter(), Counter(), Counter(), {}
    outcomes["own_photo_kept_repeated_elsewhere"] = 0
    with tempfile.TemporaryDirectory() as temporary, memo_crops():
        base = Path(temporary)
        with planner_world(base, illustration):
            for index, run in enumerate(runs):
                tid = run["topic"]
                progress(f"planner {index + 1}/{len(runs)}", run)
                if tid not in pools:
                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            pools[tid] = engine.get_scene_sequence({"id": tid})
                    except MissingAssetsError:
                        pools[tid] = None
                pool = pools[tid]
                if pool is None or len(run["beats"]) != len(run["scenes"]):
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
                if not gate(scenes, durations)["ok"]:
                    continue
                # The planner's own record of a photo matched to its line: a beat image, found for that beat's line
                # (main.py passes these as line_beat).
                line_beat = {scenes[k]: k + 1 for k, scene in enumerate(run["scenes"]) if scene.get("beat_image")}
                candidates = list(dict.fromkeys(list(pool) + [s for s in scenes if not scene_planner.is_video(s)]))
                for k in range(1, len(scenes)):
                    if scene_planner._shot_kind(scenes[k]) != "photo":
                        continue
                    lines = [f"The {tid.replace('_', ' ')} part {i + 1}." for i in range(len(scenes))]
                    lines[k] = UNSHOWN
                    beats = [{"start": b["start"], "end": b["end"], "text": t} for b, t in zip(run["beats"], lines)]
                    out, report = scene_planner.fit_to_lines(beats, list(scenes), candidates, tid,
                                                             durations=list(durations), max_calls=10_000,
                                                             line_beat=line_beat, work_dir=folder / f"w{k}")
                    filled = next((r["tier"] for r in report.get("tiers") or [] if r.get("beat") == k + 1), None)
                    if filled is None:
                        if any(r.get("beat") == k + 1 for r in report.get("held") or []):
                            filled = "held"
                        elif any(r.get("beat") == k + 1 for r in report.get("kept") or []):
                            filled = "own matched photo kept (held shot refused)"
                        else:
                            filled = "pick kept"
                    outcomes["beats_whose_photo_is_matched_to_their_line"] += line_beat.get(scenes[k]) == k + 1
                    if filled.startswith("own matched photo"):
                        source = scene_planner.picture_id(out[k])
                        outcomes["own_photo_kept_repeated_elsewhere"] += sum(
                            scene_planner.picture_id(p) == source for j, p in enumerate(out) if j != k)
                    fits = {b - 1: True for b in report.get("fits") or []}
                    fits.update({u["beat"] - 1: False for u in report.get("unshown") or []
                                 if isinstance(u.get("beat"), int)})
                    available = len({scene_planner.picture_id(c) for c in candidates if not scene_planner.is_video(c)})
                    _, held_scenes, held_durations, _, _ = scene_planner.hold_for_short_pool(
                        beats, out, list(durations), fits=fits, check=lambda picture, line: line != UNSHOWN,
                        available=available)
                    verdict = gate(held_scenes, held_durations)
                    tiers[filled] += 1
                    outcomes["plans"] += 1
                    outcomes["admitted" if verdict["ok"] else "stopped_after_the_voice"] += 1
                    row = by_tier.setdefault(filled, Counter())
                    row["admitted" if verdict["ok"] else "stopped"] += 1
                    for r in {reason(p) for p in verdict["problems"]}:
                        reasons[r] += 1
                        row[r] += 1
    return {"outcomes": dict(outcomes), "filled_by": dict(tiers), "refusal_reasons": dict(reasons),
            "by_tier": {t: dict(c) for t, c in sorted(by_tier.items())}}


def main() -> None:
    result = {"code": "the commit at <repo root>", "video": f"{config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}",
              "synthetic": synthetic()}
    if ASSETS is not None:
        result["assets"] = f"{ENV} assets copy"
        result["full_pools"] = full_pools()
        result["real_plans"] = real_plans()
        result["planner_with_illustration"] = planner_runs(True)
        result["planner_without_illustration"] = planner_runs(False)
        if not PER_ITEM:   # counts only: no pool, run or crop source
            result["full_pools"]["pools"] = len(result["full_pools"]["pools"])
            plans = result["real_plans"]
            plans.update(pools_ready=len(plans["pools_ready"]), pools_not_ready=len(plans["pools_not_ready"]),
                         runs=len(plans["runs"]))
            result["per_item"] = "omitted: counts only"
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
