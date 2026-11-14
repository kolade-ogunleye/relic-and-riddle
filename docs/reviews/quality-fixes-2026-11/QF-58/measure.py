"""QF-58 measurement. Prints JSON and writes nothing in the repository. No network, no model.

    python docs/reviews/quality-fixes-2026-11/QF-58/measure.py <repo root> [<assets dir> <env name> [<reels file>]]

<repo root> is the code measured (the parent or this row), so the same script measures both. A field the measured
code cannot produce (the parent has no such rule) is "n/a".

Production sample (decisions/PROD-SAMPLE-counts-only.md): its data is never committed. Pass a COPY of its assets as
<assets dir>, any <env name> other than "dev", and the Reel plans extract_reels.py made from it as <reels file>,
both kept outside the repository. Per-item rows (topics, runs, file names, crop boxes) are printed only for the
repository's own development data (no <assets dir>, or <env name> "dev", and no <reels file>); any other input
prints counts and medians only.

1. Generated photos through readiness, refill, holds and the final plan gate (fetch and review mocked).
2. Real pools. Without <assets dir>: the tracked assets/*/credits.json of <repo root>, with the photos' hashes from
   QF-53b's committed real_signatures.json (the photos are not in the repository; empty placeholders stand in).
   With <assets dir> (a COPY of an environment's assets with its photos, e.g. the production sample): its own
   credits and files, read only. Reported per pool: usable photos, ready, refresh due, shot quotas.
3. Real Reels (real_reels.json beside this file, made by extract_reels.py from 28 development Reels, or the
   <reels file> given): each final plan, with generated stand-ins for its photos, through the plan gate and fit_holds;
   with <assets dir> and <env name>, joined to that environment's pools as the measured code sees them.
4. With <assets dir>: the cover photograph and the Stories/carousel photos each ready pool gives (visual_engine),
   and the detail crop (picture_coverage.detail_crop, 9:16) of each ready Reel topic's first two usable photos
   (unchanged by this row since round 7: the saliency change moved to QF-97).
"""

import contextlib
import importlib
import io
import json
import logging
import shutil
import statistics
import sys
import tempfile
import warnings
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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
from src import config, image_review, photo_pool, picture_coverage, scene_planner
from src.credits import load_credits, save_credits

NA = "n/a"


def generated(folder: Path, count: int, start: int = 0) -> list[dict]:
    images = []
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(start, start + count):
        name = f"{index}.jpg"
        pixels = np.random.default_rng(index).integers(0, 256, (96, 128, 3), dtype=np.uint8)
        Image.fromarray(pixels).save(folder / name)
        images.append({"file": name, "shot_class": ("wide", "interior", "detail")[index % 3],
                       "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}})
    return images


def refill(ap, folder: Path, fetched_days_ago: int | None, beats: int | None, count: int = 9) -> dict:
    """An automatic pool of `count` photos through ensure_pool; each fetch adds three photos, the review approves."""
    import inspect
    credits = {"images": generated(folder, count), "reviewed": True, "reviewed_by": "vision"}
    if fetched_days_ago is not None:
        credits["pool_fetched_at"] = (datetime.now(UTC) - timedelta(days=fetched_days_ago)).isoformat()
    save_credits(folder, credits)
    fetched = []

    def fetch(*args, **kwargs):
        fetched.append(True)
        current = load_credits(folder)
        current["images"] = current["images"] + generated(folder, 3, start=len(current["images"]))
        save_credits(folder, current)
        return True

    kwargs = {"beats": [{}] * beats} if beats and "beats" in inspect.signature(ap.ensure_pool).parameters else {}
    if beats and not kwargs:
        return {"ready": NA, "fetches": NA, "photos_after": NA}
    with patch.object(ap, "_fetch", fetch),             patch.object(image_review, "review_pool", lambda *a, **k: {"status": "approved", "kept": 0}):
        ready = ap.ensure_pool({"id": folder.name}, folder.parent, **kwargs)
    return {"ready": ready, "fetches": len(fetched), "photos_after": len(load_credits(folder)["images"])}


def synthetic() -> dict:
    from src import asset_pipeline
    ap = importlib.reload(asset_pipeline)
    gate = getattr(scene_planner, "reel_photo_coverage", None)
    fit = getattr(scene_planner, "fit_holds", None)
    out = {}
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        folder = base / "topic"
        images = generated(folder, 9)
        five = image_review.readiness(images[:5])
        out.update(five_photo_pool_ready=five["ready"], required_floor=five["need"],
                   same_source_nine_records_usable=image_review.readiness(
                       [dict(i, source_sha256="a" * 64) for i in images])["usable"])
        out["refill"] = {name[0]: refill(ap, base / name[0], *name[1:])
                         for name in (("nine_photos_fetched_91_days_ago", 91, None),
                                                               ("nine_photos_no_fetch_recorded", None, None),
                                                               ("nine_photos_fetched_3_days_ago_11_beats", 3, 11),
                                                               ("nine_photos_fetched_3_days_ago", 3, None),
                                                               ("five_photos_fetched_3_days_ago", 3, None, 5),
                                                               ("five_photos_no_fetch_recorded", None, None, 5))}
        paths = [folder / f"{i}.jpg" for i in range(9)]
        graphic = folder / "graphic_numbers.mp4"
        graphic.write_bytes(b"")
        plans = {"valid": (paths, [4.0] * 9), "short_photos_only": (paths[:5], [4.0] * 5),
                 "repeat": (paths + paths[:1], [4.0] * 10), "long": (paths, [6.1] + [4.0] * 8),
                 "held_two_lines_6s": (paths[:3] + paths[2:], [4.0, 4.0, 3.0, 3.0] + [4.0] * 6),
                 "held_two_lines_6.5s": (paths[:3] + paths[2:], [4.0, 4.0, 3.5, 3.0] + [4.0] * 6),
                 "eight_photos_and_a_graphic": (paths[:8] + [graphic], [4.0] * 9)}
        out["plan_admitted"] = {name: (gate(s, d)["ok"] if gate else True) for name, (s, d) in plans.items()}
        beats = [{"start": i * 3.0, "end": (i + 1) * 3.0, "text": f"Line {i}."} for i in range(5)]
        held = scene_planner.hold_for_short_pool(beats, [paths[i] for i in (0, 1, 2, 0, 1)], [3.0] * 5,
                                                fits={i: True for i in range(1, 5)},
                                                check=lambda path, line: True, available=3)
        out["planned_hold_max_seconds"] = max(held[2])
        # A pause after the words keeps a photo 6.32 s on screen (the longest such hold in the production sample)
        durations = [4.0] * 5 + [6.32, 4.0, 4.0, 4.0]
        plan = paths[:6] + [graphic] + paths[6:8]
        starts = [sum(durations[:k]) for k in range(len(durations))]
        pause_beats = [{"start": s, "end": s + d} for s, d in zip(starts, durations)]
        words = [{"word": "w", "start": s, "dur": (4.6 if k == 5 else 3.0)} for k, s in enumerate(starts)]
        if fit:
            _, fitted, moves = fit(pause_beats, plan, durations, words)
            out["pause_hold"] = {"before_s": 6.32, "after_s": round(fitted[5], 2), "moves": len(moves),
                                 "admitted": gate(plan, fitted)["ok"]}
        else:
            out["pause_hold"] = {"before_s": 6.32, "after_s": 6.32, "moves": NA, "admitted": bool(not gate)}
        # Detail crops against the ruling (review 5): crops count toward neither side, one per photograph, and a
        # photograph with its consecutive crops is one hold.
        from src.lineage import record_derived

        def crop(photo: Path, n: int) -> Path:
            out_path = folder / "detail_crops" / str(n) / photo.name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(photo) as im:
                im.crop((0, 0, 40, 72)).save(out_path)
            record_derived(out_path, [photo], picture_coverage.DETAIL_OPERATION, {"box": [0, 0, 0.3, 0.75]})
            return out_path

        probes = {"one_photo_eight_crops": ([paths[0]] + [crop(paths[0], n) for n in range(1, 9)], [4.0] * 9),
                  "photo_then_two_crops_18s": ([paths[0], crop(paths[0], 11), crop(paths[0], 12)] + paths[1:],
                                               [6.0, 6.0, 6.0] + [4.0] * 8),
                  "eight_photos_and_a_crop": (paths[:8] + [crop(paths[0], 13)], [4.0] * 9),
                  "nine_photos_and_a_crop": (paths + [crop(paths[3], 14)], [4.0] * 10)}
        out["crop_plans_admitted"] = {name: (gate(s, d)["ok"] if gate else True) for name, (s, d) in probes.items()}
    out["coverage_gate_present"] = gate is not None
    return out


# ------------------------------------------------------------------ real pools
SIGNATURES = json.loads((HERE.parent / "QF-53b" / "real_signatures.json").read_text(encoding="utf-8"))["signatures"]


@contextlib.contextmanager
def pools_dir():
    """The assets folder to read: the given copy, or the tracked credits with empty placeholders for the photos
    and their recorded signatures."""
    if ASSETS is not None:
        yield ASSETS
        return
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        topic_of = {}
        for credits in sorted((ROOT / "assets").glob("*/credits.json")):
            folder = base / credits.parent.name
            folder.mkdir()
            shutil.copy2(credits, folder / "credits.json")
            for record in json.loads(credits.read_text(encoding="utf-8")).get("images", []):
                (folder / record["file"]).write_bytes(b"")
            topic_of[str(folder.resolve())] = credits.parent.name
        real = photo_pool.signature

        def recorded(path):
            path = Path(path)
            key = f"{topic_of.get(str(path.parent.resolve()), path.parent.name)}/{path.name}"
            if key in SIGNATURES:
                return dict(SIGNATURES[key])
            return real(path)

        with patch.object(photo_pool, "signature", recorded):
            yield base


def pools() -> dict:
    from src import asset_pipeline
    ap = importlib.reload(asset_pipeline)
    rows = {}
    with pools_dir() as assets:
        for credits_path in sorted(assets.glob("*/credits.json")):
            tid = credits_path.parent.name
            credits = load_credits(credits_path.parent)
            images = credits.get("images", [])
            if ASSETS is not None or hasattr(ap, "_load_pool"):
                photo_pool.refresh(credits_path.parent, images)   # the files as they are (the row's own reading)
            r = image_review.readiness(images, bool(credits.get("reviewed")))
            ready = ap.pool_ready(tid, assets)
            row = {"by": credits.get("reviewed_by"), "photos": len(images), "usable": r["usable"], "ready": ready,
                   "reserve": len(image_review.reserve_photos(images)), "weak": r["weak"],
                   "refresh_due": ap.refresh_due(credits) if hasattr(ap, "refresh_due") else NA,
                   "shot_quotas_met": ap.shot_quotas_met(images) if hasattr(ap, "shot_quotas_met") else NA}
            if not ready and images:
                row["not_ready"] = ap.not_ready_reason(tid, assets)
            rows[tid] = row
    ready = [t for t, r in rows.items() if r["ready"]]
    due = [t for t, r in rows.items() if r["refresh_due"] is True]
    return {"pools": len(rows), "ready": len(ready), "ready_topics": ready,
            "ready_usable_9_to_11": sorted(t for t in ready if 9 <= rows[t]["usable"] <= 11),
            "ready_and_refresh_due": sorted(t for t in ready if t in due),
            "refresh_due_automatic": len(due),
            "ready_meeting_shot_quotas": sum(rows[t]["shot_quotas_met"] is True for t in ready)
            if hasattr(ap, "shot_quotas_met") else NA,
            "by_topic": rows}


# ------------------------------------------------------------------ real Reels
def plan_facts(scenes: list[dict], durations: list[float]) -> tuple[int, float]:
    """(photos cut back in after another picture, the longest time one photo stays on screen) of a real plan."""
    seen, repeats, longest, run, last = set(), 0, 0.0, 0.0, None
    for scene, seconds in zip(scenes, durations):
        picture = scene.get("picture") if scene["kind"] == "photo" else None
        run = run + seconds if picture is not None and picture == last else seconds
        if picture is not None:
            longest = max(longest, run)
            if picture != last and picture in seen:
                repeats += 1
            seen.add(picture)
        last = picture
    return repeats, round(longest, 2)


def reels(pool_rows: dict | None) -> dict:
    gate = getattr(scene_planner, "reel_photo_coverage", None)
    fit = getattr(scene_planner, "fit_holds", None)
    runs = json.loads((REELS or HERE / "real_reels.json").read_text(encoding="utf-8"))["runs"]
    if ENV:
        runs = [r for r in runs if r["env"] == ENV]
    out = {"reels": len(runs), "by_env": dict(Counter(r["env"] for r in runs))}
    results, problems = [], Counter()
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        for index, run in enumerate(runs):
            folder = base / f"r{index}"
            folder.mkdir()
            scenes = []
            for k, scene in enumerate(run["scenes"]):
                if scene["kind"] == "photo":
                    path = folder / f"p{scene['picture']}.jpg"
                    if not path.exists():
                        rng = np.random.default_rng(index * 1000 + scene["picture"])
                        Image.fromarray(rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)).save(path)
                else:
                    path = folder / (f"graphic_{k}.mp4" if scene["kind"] == "graphic" else f"footage_{k}.mp4")
                    path.write_bytes(b"")
                scenes.append(path)
            beats = run["beats"]
            matched = len(beats) == len(scenes)
            durations = [b["end"] - b["start"] for b in beats] if matched else None
            row = {"env": run["env"], "run": run["run"], "topic": run["topic"], "beats": len(beats),
                   "photo_scenes": sum(s["kind"] == "photo" for s in run["scenes"]),
                   "distinct_photos": len({s["picture"] for s in run["scenes"] if s["kind"] == "photo"}),
                   "beats_match_scenes": matched,
                   "seconds_before_plan": round(sum(run["stage_seconds_before_plan"].values()), 1)}
            if matched:   # what the plan itself shows, whatever the measured code would decide
                row["plan_repeats"], row["plan_longest_photo_hold_s"] = plan_facts(run["scenes"], durations)
            if gate and matched:
                verdict = gate(scenes, durations)
                row["historic_plan_admitted"] = verdict["ok"]
                row["repeats"], row["long_holds"] = len(verdict["repeats"]), len(verdict["long_holds"])
                if fit:
                    words = [{"word": "w", "start": b["speech_start"], "dur": b["speech_end"] - b["speech_start"]}
                             for b in beats if b["speech_start"] is not None]
                    _, fitted, moves = fit([dict(b) for b in beats], scenes, durations, words)
                    after = gate(scenes, fitted)
                    row["cuts_moved"] = len(moves)
                    row["admitted_after_fit_holds"] = after["ok"]
                    row["long_holds_after_fit"] = len(after["long_holds"])
                    for p in after["problems"]:
                        problems[p.split(":")[0]] += 1
            elif gate:
                row["historic_plan_admitted"] = NA
            else:
                row["historic_plan_admitted"] = True   # no gate: every plan rendered
            if pool_rows is not None:
                pool = pool_rows.get(run["topic"])
                row["pool_ready_now"] = bool(pool and pool["ready"])
                row["pool_usable_now"] = pool["usable"] if pool else 0
                row["pool_reserve_now"] = pool["reserve"] if pool else 0
            results.append(row)
    out["plans_showing_a_photo_again"] = sum(r.get("plan_repeats", 0) > 0 for r in results)
    out["plans_holding_a_photo_over_6s"] = sum(r.get("plan_longest_photo_hold_s", 0) > 6.0 + 1e-6 for r in results)
    gated = [r for r in results if r.get("historic_plan_admitted") not in (NA,)]
    out["historic_plans_admitted"] = sum(r["historic_plan_admitted"] is True for r in gated)
    out["historic_plans_with_repeats"] = sum(r.get("repeats", 0) > 0 for r in results)
    out["historic_plans_with_long_holds"] = sum(r.get("long_holds", 0) > 0 for r in results)
    out["historic_plans_beats_scenes_differ"] = sum(not r["beats_match_scenes"] for r in results)
    if fit and gate:
        out["admitted_after_fit_holds"] = sum(r.get("admitted_after_fit_holds") is True for r in results)
        out["long_holds_fixed_by_fit_holds"] = sum(r.get("long_holds", 0) > 0 and r.get("long_holds_after_fit") == 0
                                                   for r in results)
        out["refusal_reasons_after_fit"] = dict(problems)
    else:
        out["admitted_after_fit_holds"] = NA
    out["photo_scenes_per_reel"] = dict(sorted(Counter(r["photo_scenes"] for r in results).items()))
    out["beats_per_reel"] = dict(sorted(Counter(r["beats"] for r in results).items()))
    seconds = [r["seconds_before_plan"] for r in results if r["seconds_before_plan"]]
    out["seconds_before_plan_median"] = round(statistics.median(seconds), 1) if seconds else NA
    if pool_rows is not None:
        out["pool_not_ready_now"] = sorted({r["topic"] for r in results if not r["pool_ready_now"]})
        out["reels_whose_pool_is_not_ready_now"] = sum(not r["pool_ready_now"] for r in results)
        refused = [r for r in results if r["pool_ready_now"] and r.get("admitted_after_fit_holds") is False]
        out["ready_pool_reels_refused_as_planned"] = len(refused)
        # A repeat the pool could have avoided: as many distinct usable (+ reserve) photos as the plan has photo shots.
        out["of_which_pool_has_a_photo_per_shot"] = sum(
            r["pool_usable_now"] + r["pool_reserve_now"] >= r["photo_scenes"] for r in refused)
        out["refused_runs"] = [{k: r[k] for k in ("env", "run", "photo_scenes", "distinct_photos", "pool_usable_now",
                                                  "pool_reserve_now", "repeats", "long_holds_after_fit")}
                               for r in refused]
    out["runs"] = results
    return out


# ------------------------------------------------------------------ covers, Stories/carousel photos, detail crops
def surfaces(pool_rows: dict, reel_topics: set) -> dict:
    from src.image_review import only_strong, strong_stems, usable_photos
    from src.visual_engine import VisualEngine
    engine = VisualEngine(ASSETS)
    covers, crops = {}, {}
    aspect = config.VIDEO_WIDTH / config.VIDEO_HEIGHT if config.VIDEO_HEIGHT > config.VIDEO_WIDTH else 9 / 16
    with tempfile.TemporaryDirectory() as temporary:
        for tid, row in sorted(pool_rows.items()):
            if not row["ready"]:
                continue
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    media = engine.get_scene_sequence({"id": tid})
                    hero = engine.hero_image(media)
            except Exception as e:  # noqa: BLE001 - reported, not hidden
                covers[tid] = {"error": type(e).__name__}
                continue
            strong = only_strong(media, strong_stems(ASSETS / tid))
            covers[tid] = {"cover_photo": Path(hero).name, "stills": len(media),
                           "stories_carousel_photos": [Path(p).name for p in strong]}
            if tid not in reel_topics:
                continue
            credits = load_credits(ASSETS / tid)
            for record in usable_photos(credits.get("images", []))[:2]:
                photo = ASSETS / tid / record["file"]
                got = picture_coverage.detail_crop(photo, Path(temporary) / tid, [], aspect,
                                                   min_width=int(getattr(config, "DETAIL_CROP_MIN_WIDTH", 600)))
                crops[f"{tid}/{record['file']}"] = [round(v, 4) for v in got[1]] if got else None
    return {"covers": covers, "detail_crop_boxes": crops}


def median(values: list) -> float | str:
    return round(statistics.median(values), 2) if values else NA


def counts_only(result: dict) -> dict:
    """The same measurement with every per-item row (topic, run, file name, box) replaced by counts and medians."""
    pools_in = result["pools"]
    rows = pools_in["by_topic"].values()
    pools_out = {k: (len(v) if isinstance(v, list) else v) for k, v in pools_in.items()
                 if k not in ("by_topic", "ready_topics")}
    pools_out.update(not_ready=sum(not r["ready"] for r in rows), usable_median=median([r["usable"] for r in rows]))
    reels_in = result["reels"]
    reels_out = {k: (len(v) if isinstance(v, list) else v) for k, v in reels_in.items()
                 if k not in ("runs", "refused_runs")}
    if "refused_runs" in reels_in:   # the same runs as refused_runs, by what the plan itself shows
        refused = [r for r in reels_in["runs"] if r.get("pool_ready_now") and r.get("admitted_after_fit_holds") is False]
        reels_out["refused_as_planned_by_kind"] = dict(Counter(
            "a photo shown again after another picture" if r.get("plan_repeats")
            else "one photo held over consecutive beats" if r.get("repeats")
            else "a hold over 6 s" if r.get("long_holds_after_fit") else "other" for r in refused))
    out = dict(result, pools=pools_out, reels=reels_out, per_item="omitted: counts only")
    if "surfaces" in result:
        covers = result["surfaces"]["covers"].values()
        found = [c for c in covers if "error" not in c]
        boxes = result["surfaces"]["detail_crop_boxes"].values()
        out["surfaces"] = {"ready_pools": len(covers), "errors": len(covers) - len(found),
                           "stills_total": sum(c["stills"] for c in found),
                           "stories_carousel_photos_total": sum(len(c["stories_carousel_photos"]) for c in found),
                           "stories_carousel_photos_median": median([len(c["stories_carousel_photos"])
                                                                     for c in found]),
                           "detail_crop_photos": len(boxes), "detail_crops_found": sum(b is not None for b in boxes)}
    return out


def main() -> None:
    result = {"code": "the commit at <repo root>", "assets": "tracked credits + QF-53b signatures" if ASSETS is None
              else f"{ENV or 'given'} assets copy", "synthetic": synthetic()}
    pool_result = pools()
    result["pools"] = pool_result
    joined = ASSETS is not None or ENV == "dev"   # the tracked pools are the development ones
    reel_result = reels(pool_result["by_topic"] if joined else None)
    result["reels"] = reel_result
    if ASSETS is not None:
        result["surfaces"] = surfaces(pool_result["by_topic"], {r["topic"] for r in reel_result["runs"]})
    if not PER_ITEM:
        result = counts_only(result)
    print(json.dumps(result, indent=1, sort_keys=False))


if __name__ == "__main__":
    main()
