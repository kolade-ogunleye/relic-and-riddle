"""QF-88 round 2 (review 1, blocking 3): real narration through the composer's real caption path with random cuts.

    python cps_cuts.py <repo root> --set <label>=<output dir> [--set ...]

Every run_*/words.json under each output dir goes through the real VideoComposer.build_subtitles (9:16, first frame
whole, as compose_video calls it) once with no cuts and with 20 random cut sets (scenes 1.8-4.5 s, on frames),
seeded by the run's folder name, so the result is repeatable. A render the CPS rule fails is counted. A second pass
puts the same narration through a timing that hides each burst 0.3 s early, to show that real defects are still
caught. Counts only (a production-sample copy may be given: decisions/PROD-SAMPLE-counts-only). Prints JSON (LF);
writes only temporary caption files. Adapted from the review-1 reviewer's probe.
"""

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
ap.add_argument("--set", action="append", default=[])
args = ap.parse_args()
sys.path.insert(0, str(args.root.resolve()))
from src import video_composer as vc
from src.config import CAPTION_FIRST_FRAME_WHOLE_S


def build(words, path, cuts):
    try:
        vc.VideoComposer().build_subtitles(words, path, first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S, cuts=cuts)
        return "built"
    except vc.CaptionReadingRateError:
        return "failed"


out = {}
for spec in args.set:
    label, base = spec.split("=", 1)
    tot = {"runs": 0, "builds": 0, "failed": 0, "runs_failing": 0, "no_cut_failed": 0, "errors": 0,
           "early_hide_builds": 0, "early_hide_caught": 0}
    with tempfile.TemporaryDirectory(prefix="qf88-cps-") as temp:
        for wj in sorted(Path(base).glob("run_*/words.json")):
            try:
                data = json.loads(wj.read_text(encoding="utf-8"))
                words = data["words"] if isinstance(data, dict) else data
                end = max(float(w["start"]) + float(w.get("dur") or 0.3) for w in words)
            except (OSError, ValueError, KeyError, TypeError):
                tot["errors"] += 1
                continue
            tot["runs"] += 1
            rng = random.Random(wj.parent.name)
            failed_here = False
            for k in range(21):
                cuts = None
                if k:
                    cuts, x = [], rng.uniform(1.5, 4.0)
                    while x < end:
                        cuts.append(round(x * 30) / 30)
                        x += rng.uniform(1.8, 4.5)
                tot["builds"] += 1
                try:
                    result = build(words, Path(temp) / "c.ass", cuts)
                except Exception:  # noqa: BLE001 - any other error is counted apart
                    tot["errors"] += 1
                    continue
                if result == "failed":
                    tot["failed"] += 1
                    failed_here = True
                    tot["no_cut_failed"] += cuts is None
            tot["runs_failing"] += failed_here
            real = vc.burst_timings
            vc.burst_timings = lambda p, real=real, **kw: [(s, max(s + 0.05, h - 0.3), r) for s, h, r in real(p, **kw)]
            try:
                tot["early_hide_builds"] += 1
                tot["early_hide_caught"] += build(words, Path(temp) / "e.ass", None) == "failed"
            finally:
                vc.burst_timings = real
    out[label] = tot
sys.stdout.reconfigure(newline="\n")
print(json.dumps(out, indent=1))
