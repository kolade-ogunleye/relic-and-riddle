"""QF-11 real-data run: mastering passes and the release gate's sound checks over every real voice, bed and master.

    python real_data.py <repo root> [--dev <output dir>] [--prod <copy of the production sample's output dir>]
                        [--skip-mastering] [--skip-gate]

Runs the code of <repo root> (on the base too: there is no src.audio_qa there, so the gate part reports that).
Reads only: <root>/output/run_* (development) and an optional COPY of the production sample (never the original).

- Mastering: for every run folder holding its voiceover and ambient bed, the composer's mastering as
  VideoComposer.render calls it (calibrate_gain, end_card_level for a Reel with a loop bridge, calibrate_gain again
  when the card level moves), counting each FFmpeg decode of the mix before the render; then the mastered
  programme (the render's own audio graph, before AAC) measured by the composer's meter.
- Gate: src.audio_qa.measure on every master (*_master.mp4, *_16x9.mp4): check status, the recorded figures, wall
  seconds and the FFmpeg process's CPU seconds (Windows process times; None elsewhere).

Prints JSON; writes nothing. Development rows name the run; production results are counts and aggregate figures
only (no run, file or per-item record: decisions PROD-SAMPLE-counts-only).
"""

import argparse
import ctypes
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
ap.add_argument("--dev", type=Path, help="development output dir (default <root>/output)")
ap.add_argument("--prod", type=Path)
ap.add_argument("--skip-mastering", action="store_true")
ap.add_argument("--skip-gate", action="store_true")
args = ap.parse_args()
root = args.root.resolve()
dev_dir = (args.dev or root / "output").resolve()
sys.path.insert(0, str(root))
import soundfile as sf

from src import video_composer as vc
from src.delivery_profiles import get_delivery_profile
from src.end_card import LOOP_SECONDS

try:
    from src import audio_qa
except ImportError:
    audio_qa = None

_cpu = []
_POPEN = subprocess.Popen


def _process_cpu(handle):
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    c, e, k, u = (wintypes.FILETIME() for _ in range(4))
    ctypes.windll.kernel32.GetProcessTimes(int(handle), *(ctypes.byref(x) for x in (c, e, k, u)))
    return sum(((t.dwHighDateTime << 32) | t.dwLowDateTime) / 1e7 for t in (k, u))


class TimedPopen(_POPEN):
    def wait(self, timeout=None):
        code = super().wait(timeout)
        if "ffmpeg" in str(self.args[0]):
            _cpu.append(_process_cpu(self._handle))
        return code


def _plain(exc: Exception) -> str:
    text = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s'\"]*[\\/]([^\\/\s'\"]+)", r"\1", str(exc))
    return f"{type(exc).__name__}: {text}"[:300]


def master_run(d: Path) -> dict | None:
    """The run's mastering as VideoComposer.render does it, with each decode of the mix counted."""
    vo = next((p for p in (d / "voiceover.wav", d / "voiceover.mp3") if p.is_file()), None)
    bed = d / "ambient_bed.wav"
    if not vo or not bed.is_file():
        return None
    deep = "_long" in d.name or any(d.glob("*_16x9.mp4"))
    prof = get_delivery_profile("deep_dive" if deep else "reel")
    vo_dur = sf.info(str(vo)).duration
    # render's card: a Reel whose bed runs on for the loop bridge (generate_ambient_bed(end_card_at=vo_dur))
    card = not deep and sf.info(str(bed)).duration >= vo_dur + LOOP_SECONDS - 0.02
    pad = LOOP_SECONDS if card else 0.0
    composer = vc.VideoComposer()
    decodes = []
    real_run = vc._run_ffmpeg

    def counted(cmd):
        decodes.append(1)
        return real_run(cmd)
    vc._run_ffmpeg = counted
    t0 = time.perf_counter()
    try:
        gain = composer.calibrate_gain(vo, bed, target_lufs=prof.target_lufs, target_true_peak=prof.target_true_peak,
                                       pad=pad, end=vo_dur + pad)
        records = [getattr(composer, "last_mastering", None)]
        card_db = 0.0
        if card:
            level = composer.end_card_level(vo, bed, vo_dur, pad, gain)
            card_db = level["correction_db"] if abs(level["correction_db"]) >= 0.5 else 0.0
            if card_db:
                gain = composer.calibrate_gain(vo, bed, target_lufs=prof.target_lufs,
                                               target_true_peak=prof.target_true_peak, pad=pad, card_at=vo_dur,
                                               card_db=card_db, end=vo_dur + pad)
                records.append(getattr(composer, "last_mastering", None))
    except Exception as exc:  # noqa: BLE001 - recorded, never a pass
        return {"run": d.name, "error": _plain(exc)}
    finally:
        vc._run_ffmpeg = real_run
    seconds = time.perf_counter() - t0
    chain = vc._audio_chain(0, 1, pad, vo_dur if card else None, card_db, end=vo_dur + pad) + vc._master_chain(gain)
    if card:
        from src.audio_engine import AudioEngine
        chain += vc._seam_tail(vo_dur + pad, AudioEngine.LOOP_BRIDGE_EDGE)
    integrated, peak = vc.VideoComposer._measure(vo, bed, chain)
    bounds = [r.get("limiter_cost_bound_lu") for r in records if r]
    firsts = [r["first_pass"]["integrated_lufs"] for r in records if r and r.get("first_pass")]
    return {"run": d.name, "deep_dive": deep, "loop_bridge": card, "card_db": card_db, "decodes": len(decodes),
            "mastering_s": round(seconds, 1), "gain_db": round(gain, 2),
            "integrated_lufs": integrated, "true_peak_dbtp": peak,
            # the limiter's real cost against the estimate: the last pass's unlimited loudness + gain - result
            "limiter_cost_lu": round(firsts[-1] + gain - integrated, 2) if firsts else None,
            "limiter_cost_bound_lu": bounds[-1] if bounds else None}


def masters(base: Path) -> list:
    return sorted([(p, False) for p in base.glob("run_*/*_master.mp4")] +
                  [(p, True) for p in base.glob("run_*/*_16x9.mp4")])


def gate_one(path: Path, deep: bool) -> dict:
    if audio_qa is None:
        return {"error": "no src.audio_qa in this tree (no sound checks in the gate)"}
    _cpu.clear()
    audio_qa.subprocess.Popen = TimedPopen
    t0 = time.perf_counter()
    try:
        res = audio_qa.measure(path, deep_dive=deep)
    except Exception as exc:  # noqa: BLE001 - recorded, never a pass
        return {"error": _plain(exc)}
    finally:
        audio_qa.subprocess.Popen = _POPEN
    wall = time.perf_counter() - t0
    cpu = sum(c for c in _cpu if c is not None) if _cpu and all(c is not None for c in _cpu) else None
    return {"duration_s": res["duration"], "status": {c["name"]: c["status"] for c in res["checks"]},
            "noted": [c["name"] for c in res["checks"] if c.get("minor")],
            "integrated_lufs": res["integrated_lufs"], "true_peak_dbtp": res["true_peak_dbtp"],
            "loudness_range_lu": res["loudness_range_lu"], "max_short_term_lufs": res["max_short_term_lufs"],
            "max_momentary_lufs": res["max_momentary_lufs"], "phone_loss_lu": res["phone"]["loss_lu"],
            "phone_worst_window_lu": res["phone"]["worst_window_loss_lu"],
            "sibilance_worst_rel_lu": res["sibilance"]["worst_momentary_rel_lu"],
            "longest_silence_s": round(max((s["end"] - s["start"] for s in res["silence_intervals"]), default=0.0), 2),
            "flat_factor": res["astats"]["flat_factor"],
            "wall_s": round(wall, 2), "cpu_s": None if cpu is None else round(cpu, 2),
            "wall_s_per_programme_s": round(wall / res["duration"], 4),
            "cpu_s_per_programme_s": None if cpu is None else round(cpu / res["duration"], 4)}


def _spread(values) -> dict | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"min": min(values), "median": round(statistics.median(values), 3), "max": max(values)}


def summarise_mastering(rows: list) -> dict:
    ok = [r for r in rows if "error" not in r]
    return {"runs": len(rows), "errors": len(rows) - len(ok),
            "decodes": _spread([r["decodes"] for r in ok]),
            "decodes_with_card_level": _spread([r["decodes"] for r in ok if r["card_db"]]),
            "decodes_without_card_level": _spread([r["decodes"] for r in ok if not r["card_db"]]),
            "mastering_s": _spread([r["mastering_s"] for r in ok]),
            "integrated_lufs": _spread([r["integrated_lufs"] for r in ok]),
            "true_peak_dbtp": _spread([r["true_peak_dbtp"] for r in ok]),
            "off_target_over_0_5_lu": sum(1 for r in ok if abs(r["integrated_lufs"] + 14.0) > 0.5),
            "limiter_cost_lu": _spread([r["limiter_cost_lu"] for r in ok]),
            "limiter_cost_bound_lu": _spread([r["limiter_cost_bound_lu"] for r in ok]),
            "bound_below_real_cost": sum(1 for r in ok if r["limiter_cost_bound_lu"] is not None
                                         and r["limiter_cost_lu"] is not None
                                         and r["limiter_cost_bound_lu"] + 0.05 < r["limiter_cost_lu"])}


def summarise_gate(rows: list) -> dict:
    ok = [r for r in rows if "error" not in r]
    names = sorted({n for r in ok for n in r["status"]})
    return {"masters": len(rows), "measurement_failed": len(rows) - len(ok),
            "failed": {n: sum(1 for r in ok if r["status"][n] == "fail") for n in names},
            "noted": {n: sum(1 for r in ok if n in r["noted"]) for n in names},
            **{k: _spread([r[k] for r in ok]) for k in (
                "loudness_range_lu", "max_short_term_lufs", "max_momentary_lufs", "phone_loss_lu",
                "phone_worst_window_lu", "sibilance_worst_rel_lu", "longest_silence_s", "flat_factor",
                "wall_s_per_programme_s", "cpu_s_per_programme_s")}}


out = {"root_has_audio_qa": audio_qa is not None}
for label, base, anonymise in (("dev", dev_dir, False), ("prod", args.prod, True)):
    if base is None:
        continue
    base = base.resolve()
    section = {}
    if not args.skip_mastering:
        rows = [r for r in (master_run(d) for d in sorted(base.glob("run_*"))) if r]
        section["mastering_summary"] = summarise_mastering(rows)
        if not anonymise:
            section["mastering"] = rows
    if not args.skip_gate:
        rows = [{"master": p.relative_to(base).as_posix(), "deep_dive": deep, **gate_one(p, deep)}
                for p, deep in masters(base)]
        section["gate_summary"] = summarise_gate(rows)
        if not anonymise:
            section["gate"] = rows
        if audio_qa is not None:
            longest = get_delivery_profile("deep_dive").duration_max
            rates = [r["wall_s_per_programme_s"] for r in rows if "error" not in r]
            section["time_limit"] = {"longest_programme_s": longest,
                                     "longest_wall_s_projected": round(max(rates) * longest, 1) if rates else None,
                                     "audio_limit_s": audio_qa.timeout_s(longest)}
    out[label] = section
sys.stdout.reconfigure(newline="\n")
print(json.dumps(out, indent=1))
