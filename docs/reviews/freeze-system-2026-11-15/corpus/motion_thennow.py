"""MOTION-01 / MOTION-02 (FIGURES, cycle 5): the then-and-now clip's markers and divider, on the renderer's own
production-encoded clips (then_now._encode: never NVENC) and its own schedule.

    python motion_thennow.py <tree> <dir> [--render]

--render draws the committed then-and-now cases (docs/reviews/freeze-panel-2026-11-04/fixtures, both formats) with
THIS tree into <dir> (the heavy step, minutes per clip: run it once per tree); without it the clips and their
<clip>.meta.json already in <dir> are measured (cheap: the schedule, and the decoded frames round the divider).

Per clip and marker:
  cut        frames where a marker is on screen (alpha > 0) and the divider is inside its group box       must be 0
  near       frames where a part-transparent marker (0 < alpha < 1) is within LEAD of the divider         must be 0
  ghost      the longest run of part-transparent frames of one fade                              at most RAMP_MAX
Per clip:
  rule       moving frames (step > 8 px) where the divider is a hard bone rule on the decoded frame (its DIVIDER_W
             px within 40 luma of bone), over the moving frames with the divider on screen               must be all
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RAMP_MAX = 4          # frames: the longest part-transparent run of one marker fade
LEAD = 0.015          # of the wipe axis: the divider's least distance from a part-transparent marker


def render(tree: Path, out: Path) -> None:
    sys.path.insert(0, str(tree / "docs/reviews/freeze-panel-2026-11-04/fixtures"))
    import fixtures as fx
    from src import then_now
    from src.art_direction import use_palette
    use_palette(None)
    out.mkdir(parents=True, exist_ok=True)
    for case in fx.load("thennow"):
        for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")):
            clip = out / f"thennow__{case['topic']}__{tag}.mp4"
            then_now.render(fx.path(case["old"]), int(case["old_year"]), fx.path(case["new"]), int(case["new_year"]),
                            clip, w, h)
            meta = dict(then_now.last_meta)
            n = len(meta["schedule"]["then"])
            meta["divider"] = [then_now.divider_at(i / then_now.VIDEO_FPS, w, h) for i in range(n)]
            meta["size"] = [w, h]
            clip.with_suffix(".meta.json").write_text(json.dumps(meta, default=str), encoding="utf-8", newline="\n")
            print("rendered", clip.name, flush=True)


def frames_of(clip: Path, size: tuple[int, int]):
    import numpy as np
    w, h = size
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w)


def measure(clip: Path) -> list[tuple]:
    import numpy as np
    from src import brand, then_now
    meta = json.loads(clip.with_suffix(".meta.json").read_text(encoding="utf-8"))
    w, h = meta.get("size") or ((1080, 1920) if clip.stem.endswith("916") else (1920, 1080))
    axis = 0 if w > h else 1
    edge = (w, h)[axis]
    div = meta["divider"]
    rows = []
    for which, sched in meta["schedule"].items():
        box = meta["groups"][which]
        lo, hi = (box[0], box[2]) if axis == 0 else (box[1], box[3])
        cut = sum(1 for a, p in zip(sched, div) if a > 0 and lo <= p <= hi and 0 < p < edge)
        near = sum(1 for a, p in zip(sched, div)
                   if 0 < a < 1 and 0 < p < edge and min(abs(p - lo), abs(p - hi)) < LEAD * edge
                   and abs(p - div[max(0, div.index(p) - 1)]) > 0)
        run = best = 0
        for i, a in enumerate(sched):
            moving = i > 0 and abs(div[i] - div[i - 1]) > 0.5
            run = run + 1 if 0 < a < 1 and moving else 0
            best = max(best, run)
        ok = cut == 0 and near == 0 and best <= RAMP_MAX
        rows.append((clip.stem, which, f"cut {cut}, near {near}, longest part-transparent run {best} frames",
                     "PASS" if ok else "FAIL"))
    fr = frames_of(clip, (w, h))
    bone = 0.2126 * brand.BONE[0] + 0.7152 * brand.BONE[1] + 0.0722 * brand.BONE[2]
    moving = hard = 0
    for i in range(1, min(len(div), len(fr))):
        p = round(div[i])
        if abs(div[i] - div[i - 1]) <= then_now.BLUR_MIN_STEP or not 4 < p < edge - 4:
            continue
        moving += 1
        band = fr[i][:, p - 1:p + 2] if axis == 0 else fr[i][p - 1:p + 2, :]
        if abs(float(np.median(band)) - bone) <= 40:
            hard += 1
    rows.append((clip.stem, "divider", f"a hard bone rule on {hard} of {moving} moving frames",
                 "PASS" if hard == moving else "FAIL"))
    return rows


if __name__ == "__main__":
    tree, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    sys.path.insert(0, str(tree))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    os.environ.setdefault("RR_FIXTURE_REPO", str(tree))
    if "--render" in sys.argv:
        render(tree, out)
    import figures_corpus as fc
    rows = [r for clip in sorted(out.glob("thennow__*.mp4")) for r in measure(clip)]
    print(fc.table(rows, ("clip", "part", "measure", "verdict")))
    fails = sum(1 for r in rows if r[3] == "FAIL")
    print(f"\nMOTION-01 / MOTION-02: {len(rows)} rows, FAIL {fails}")
    sys.exit(1 if fails else 0)
