"""QF-10 real-data run: the release gate's picture checks over every real master, house graphic and design frame.

    python real_data.py <repo root> [--dev <output dir>] [--prod <copy of the production sample's output dir>]
                        [--cards <dir>] [--single-thread <n>]

Reads only: <root>/output/run_* (development masters, graphics and stills), the optional production sample COPY
(never the original) and an optional folder of design-card renders made by
docs/reviews/freeze-panel-2026-11-04/cards/render_evidence.py on real run inputs (its png/ frames, the label band
cut off, and hook_frame.mp4 clips). Stills are encoded as 1.5 s BT.709 clips the way the composer converts RGB
(src.encoding.RGB_TO_BT709, libx264 at VIDEO_CRF) and measured as a Reel. Prints JSON; writes only temporary clips
under the system temp folder. Production results are counts and aggregate timings only (no run, file or per-item
record).

Timings: wall seconds of the whole measurement and CPU seconds of the FFmpeg decode process (Windows process times;
None elsewhere) plus this process's own CPU for the analysers. `--single-thread n` re-measures the first n masters of
each shape with PICTURE_QA_THREADS = 1 (the one-core bound).
"""

import argparse
import ctypes
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
ap.add_argument("--dev", type=Path, help="development output dir (default <root>/output)")
ap.add_argument("--prod", type=Path)
ap.add_argument("--cards", type=Path)
ap.add_argument("--single-thread", type=int, default=0)
args = ap.parse_args()
root = args.root.resolve()
dev_dir = (args.dev or root / "output").resolve()
sys.path.insert(0, str(root))
from src import config, picture_qa
from src.encoding import BT709_FLAGS, RGB_TO_BT709

_popen = picture_qa.subprocess.Popen
_cpu = []


def _process_cpu(handle):
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    c, e, k, u = (wintypes.FILETIME() for _ in range(4))
    ctypes.windll.kernel32.GetProcessTimes(int(handle), *(ctypes.byref(x) for x in (c, e, k, u)))
    return sum(((t.dwHighDateTime << 32) | t.dwLowDateTime) / 1e7 for t in (k, u))


class TimedPopen(_popen):
    def wait(self, timeout=None):
        code = super().wait(timeout)
        if "ffmpeg" in str(self.args[0]):
            _cpu.append(_process_cpu(self._handle))
        return code


picture_qa.subprocess.Popen = TimedPopen


def _plain(exc: Exception) -> str:
    """The error without any absolute path (file names only: no user or host names in committed evidence)."""
    if isinstance(exc, subprocess.TimeoutExpired):
        text = f"{Path(str(exc.cmd[0])).name} timed out after {exc.timeout:.0f} s"
    elif isinstance(exc, subprocess.CalledProcessError):
        text = f"{Path(str(exc.cmd[0])).name} exited {exc.returncode}"
    else:
        text = str(exc)
    text = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s'\"]*[\\/]([^\\/\s'\"]+)", r"\1", text)
    return f"{type(exc).__name__}: {text}"[:300]


def run(path: Path, deep_dive: bool) -> dict:
    _cpu.clear()
    t0, p0 = time.perf_counter(), time.process_time()
    try:
        res = picture_qa.measure(path, deep_dive=deep_dive)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:  # recorded, never a pass
        return {"error": _plain(exc)}
    wall, own = time.perf_counter() - t0, time.process_time() - p0
    decode = _cpu[-1] if _cpu else None
    return {"duration_s": res["duration"], "frames": res["decoded_frames"], "grid": res["sample_grid"],
            "status": {c["name"]: c["status"] for c in res["checks"]},
            "failed": {c["name"]: c.get("intervals") or c["detail"] for c in res["checks"] if c["status"] != "pass"},
            "wall_s": round(wall, 2), "decode_cpu_s": None if decode is None else round(decode, 2),
            "analyser_cpu_s": round(own, 2),
            "cpu_s_per_programme_s": None if decode is None else round((decode + own) / res["duration"], 3)}


def still_clip(image: Path, out: Path, crop_band: bool = False) -> Path:
    from PIL import Image
    with Image.open(image) as im:
        w, h = im.size
    if crop_band:   # render_evidence.py: the card's pixel-exact frame sits above a label band
        h = 1920 if w == 1080 else 1080 if w == 1920 else h
    vf = f"crop={w // 2 * 2}:{h // 2 * 2}:0:0,{RGB_TO_BT709}"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-t", "1.5",
                    "-i", str(image), "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", str(config.VIDEO_CRF),
                    *BT709_FLAGS, "-pix_fmt", "yuv420p", str(out)], check=True, timeout=120)
    return out


def masters(base: Path) -> list:
    return sorted([(p, False) for p in base.glob("run_*/*_master.mp4")] +
                  [(p, True) for p in base.glob("run_*/*_16x9.mp4")])


STILLS = ("thumbnail_cover.jpg", "thumbnail_profile_grid.jpg", "thumbnail_youtube_16x9.jpg",
          "stories/story_*.jpg", "graphics/graphic_*.jpg", "carousel/*.jpg")


def kind_of(path: Path) -> str:
    name = path.name
    if path.parent.name == "stories":
        return "story"
    if path.parent.name == "carousel":
        return "carousel"
    for key in ("map", "numbers", "timeline", "scale", "thennow", "cover", "grid", "youtube"):
        if key in name:
            return {"numbers": "stat_card", "cover": "cover", "grid": "grid_tile", "youtube": "youtube_thumb"}.get(
                key, key)
    return name


def survey(base: Path, label: str, anonymise: bool, tmp: Path) -> dict:
    rows = {"masters": [], "graphics": [], "stills": []}
    for i, (path, deep) in enumerate(masters(base)):
        name = f"{label}-{i + 1:02d}" if anonymise else path.relative_to(base).as_posix()
        rows["masters"].append({"master": name, "deep_dive": deep, **run(path, deep)})
        print(f"{label} master {i + 1}: {rows['masters'][-1].get('status') or rows['masters'][-1]}", file=sys.stderr)
    for i, path in enumerate(sorted(base.glob("run_*/graphics/graphic_*.mp4"))):
        rows["graphics"].append({"clip": f"{label}-g{i + 1:03d}" if anonymise else path.relative_to(base).as_posix(),
                                 "kind": kind_of(path), **run(path, False)})
    stills = sorted({p for pattern in STILLS for p in base.glob(f"run_*/{pattern}")})
    for i, path in enumerate(stills):
        clip = still_clip(path, tmp / f"{label}_still_{i}.mp4")
        rows["stills"].append({"still": f"{label}-s{i + 1:03d}" if anonymise else path.relative_to(base).as_posix(),
                               "kind": kind_of(path), **run(clip, False)})
    return rows


def summary(rows: list) -> dict:
    ok = [r for r in rows if "status" in r]
    names = sorted({n for r in ok for n in r["status"]})
    walls = [r["wall_s"] for r in ok]
    rate = [r["cpu_s_per_programme_s"] for r in ok if r.get("cpu_s_per_programme_s") is not None]
    return {"items": len(rows), "measured": len(ok), "errors": len(rows) - len(ok),
            "fail_counts": {n: sum(r["status"][n] == "fail" for r in ok) for n in names},
            "rejected_excluding_colour_tags": sum(any(s == "fail" for n, s in r["status"].items()
                                                      if n != "picture_colour") for r in ok),
            "wall_s": {"min": min(walls), "median": statistics.median(walls), "max": max(walls)} if walls else None,
            "cpu_s_per_programme_s": {"median": statistics.median(rate), "max": max(rate)} if rate else None}


with tempfile.TemporaryDirectory(prefix="qf10-real-") as temp:
    tmp = Path(temp)
    out = {"method": picture_qa.method(), "threads": config.PICTURE_QA_THREADS}
    dev = survey(dev_dir, "dev", False, tmp)
    out["dev"] = {**dev, "summary": {k: summary(v) for k, v in dev.items()}}
    if args.prod:
        prod = survey(args.prod, "prod", True, tmp)
        # Counts only (operator, decisions PROD-SAMPLE-counts-only): no run, file or per-item record is kept.
        out["prod"] = {"summary": {k: summary(v) for k, v in prod.items()},
                       "items_failed_other_than_colour_tags": {
                           k: sum(1 for r in v if any(n != "picture_colour" for n in (r.get("failed") or {})))
                           for k, v in prod.items()},
                       "fail_counts_by_check": {k: summary(v)["fail_counts"] for k, v in prod.items()},
                       "measurement_errors": {k: sum(1 for r in v if r.get("error")) for k, v in prod.items()}}
    if args.cards:
        cards = []
        for i, png in enumerate(sorted((args.cards / "png").glob("*.png"))):
            clip = still_clip(png, tmp / f"card_{i}.mp4", crop_band=True)
            cards.append({"card": png.stem, "kind": png.stem.split("_")[0], **run(clip, False)})
        # The end card as committed in its own review (docs/reviews/end-card-2026-10-25, real runs, half size).
        for i, jpg in enumerate(sorted((root / "docs/reviews/end-card-2026-10-25").glob("after_*.jpg"))):
            clip = still_clip(jpg, tmp / f"end_{i}.mp4")
            cards.append({"card": jpg.stem, "kind": "end_card", **run(clip, False)})
        for clip in sorted(args.cards.rglob("hook_frame.mp4")):
            cards.append({"card": clip.parent.name, "kind": "hook_frame", **run(clip, False)})
        out["cards"] = {"rows": cards, "summary": summary(cards)}
    if args.single_thread:
        config.PICTURE_QA_THREADS = 1
        picked = [p for p, d in masters(dev_dir) if d][:args.single_thread] + \
                 [p for p, d in masters(dev_dir) if not d][:args.single_thread]
        rows = [{"master": p.relative_to(dev_dir).as_posix(), **run(p, "_16x9" in p.name)} for p in picked]
        out["single_thread"] = rows
        rates = [r["cpu_s_per_programme_s"] for r in rows if r.get("cpu_s_per_programme_s")]
        walls = [r["wall_s"] / r["duration_s"] for r in rows if "wall_s" in r]
        # The one-core bound for the longest programme the gate admits (deep_dive_16x9 duration_max).
        from src.delivery_profiles import get_delivery_profile
        longest = get_delivery_profile("deep_dive").duration_max
        out["single_thread_summary"] = {
            "masters": len(rows), "cpu_s_per_programme_s_max": max(rates) if rates else None,
            "wall_s_per_programme_s_max": round(max(walls), 3) if walls else None,
            "longest_programme_s": longest,
            "longest_one_core_wall_s_projected": round(max(walls) * longest, 1) if walls else None,
            "longest_timeout_s": picture_qa.timeout_s(longest)}
sys.stdout.reconfigure(newline="\n")   # LF when redirected to a file on Windows
print(json.dumps(out, indent=1))
