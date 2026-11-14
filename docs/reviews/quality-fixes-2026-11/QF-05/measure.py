"""QF-05 whoosh placement: how many transition whooshes each fixture run's music bed carries, where, and how loud.

    python measure.py <repo root>

Runs the code of <repo root> (its src/ and the pipeline's own rules) on committed inputs only: the eight fixture runs'
words (docs/reviews/freeze-panel-2026-11-04/fixtures/runs/<run>/words.json) and the run facts beside this file
(inputs.json: each run's voiceover length, sound plan, delivery profile, act cues; build_inputs.py copied them).

Each bed is made the way main.py makes it (generate_ambient_bed with the arguments its call passes at that commit;
the scene cuts only where that main.py passes them), once as it is and once with the whoosh recording silenced; the
difference is the whoosh layer exactly. Each separate burst in it is one whoosh (its time is the burst start + 0.15 s,
where generate_ambient_bed places it), with its RMS and peak level. A whoosh "on a cut" is within 0.05 s of one of the
run's scene cuts (the beat boundaries the Reel's scene plan cuts on, segment_beats; the documentary's
documentary_cuts); "on a reveal" within 0.05 s of one of its sound plan's reveals.

Prints JSON and writes measurements.json beside this file (or to the path in RR_MEASURE_OUT).
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / "freeze-panel-2026-11-04" / "fixtures" / "runs"
ON = 0.05   # s: a whoosh this close to a cut or a reveal is on it


def _commit(root: Path) -> str:
    """The measured tree's HEAD commit (short sha, noting uncommitted changes): evidence names the code by commit, never by a local path."""
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + (" + uncommitted changes" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    os.chdir(root)   # src.config resolves data/ (the licensed whoosh recording) against the checkout
    import numpy as np
    import soundfile as sf

    from src import end_card
    from src.audio_engine import AudioEngine
    from src.delivery_profiles import get_delivery_profile
    from src.scene_planner import documentary_cuts, segment_beats
    from src.video_composer import caption_cut_times

    takes_cuts = "scene_cuts" in inspect.signature(AudioEngine.generate_ambient_bed).parameters
    main_src = (root / "main.py").read_text(encoding="utf-8")
    main_passes_cuts = "scene_cuts=scene_cuts" in main_src and takes_cuts
    deep_passes_cuts = "scene_cuts=doc_scene_cuts" in main_src and takes_cuts

    class Silent(AudioEngine):
        def _effect(self, name, synth, sr, loop_to=None):
            out = super()._effect(name, synth, sr, loop_to)
            return out * 0.0 if name == "sfx_whoosh.wav" else out

    facts = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="qf05_"))
    rows = []
    for run, f in facts.items():
        words = json.loads((FIXTURES / run / "words.json").read_text(encoding="utf-8"))
        vo = float(f["vo_dur"])
        plan = f["sound_plan"]
        reveals = [float(m["t"]) for m in (plan or {}).get("moments", []) if m.get("kind") == "reveal"]
        cases = []
        if f["deep_dive"]:
            # main.py's deep dive (_run_deep_dive): vo + 0.5 s, act cues, the documentary's own cuts
            cuts = caption_cut_times([c["end"] - c["start"] for c in documentary_cuts(words, vo)])
            kw = {"words": words, "cue_timestamps": f["act_cues"], "sound_plan": plan}
            if deep_passes_cuts:
                kw["scene_cuts"] = cuts
            cases.append(("deep_dive_16x9 (act cues)", vo + 0.5, kw, cuts, None, 48000))
            # the same narration through the Reel/16:9 render path (_produce_bed_and_render), no act cues
            prof = get_delivery_profile(f["delivery_profile"])
            beat_cuts = caption_cut_times([b["end"] - b["start"] for b in segment_beats(words, vo)])
            kw = {"words": words, "sound_plan": plan, "sr": prof.audio_sample_rate, "loop_seamless": prof.loop_seamless}
            if main_passes_cuts:
                kw["scene_cuts"] = beat_cuts
            cases.append(("16x9 render path (no act cues)", vo + end_card.bed_tail(False), kw, beat_cuts, None,
                          prof.audio_sample_rate))
        else:
            prof = get_delivery_profile(f["delivery_profile"])
            card = prof.width < prof.height
            cuts = caption_cut_times([b["end"] - b["start"] for b in segment_beats(words, vo)])
            kw = {"words": words, "sr": prof.audio_sample_rate, "loop_seamless": prof.loop_seamless, "sound_plan": plan,
                      "end_card_at": vo if card else None}
            if main_passes_cuts:
                kw["scene_cuts"] = cuts
            cases.append(("reel_9x16", vo + end_card.bed_tail(card), kw, cuts, vo if card else None,
                          prof.audio_sample_rate))
        for label, seconds, kw, cuts, card_at, sr in cases:
            a = AudioEngine().generate_ambient_bed(seconds, tmp / f"{run}_a.wav", **kw)
            b = Silent().generate_ambient_bed(seconds, tmp / f"{run}_b.wav", **kw)
            x, _ = sf.read(str(a), dtype="float64")
            y, _ = sf.read(str(b), dtype="float64")
            layer = np.abs(x - y).max(axis=1)
            live = np.flatnonzero(layer > 1e-6)
            bursts = []
            if live.size:
                splits = np.flatnonzero(np.diff(live) > int(0.1 * sr)) + 1
                for seg in np.split(live, splits):
                    i0, i1 = int(seg[0]), int(seg[-1]) + 1
                    t = round(i0 / sr + 0.15, 3)
                    if card_at is not None and t >= card_at:
                        continue   # the end card's own whoosh (none on the Reel's loop bridge)
                    part = (x - y)[i0:i1]
                    bursts.append({
                        "t": t,
                        "rms_dbfs": round(20 * float(np.log10(np.sqrt(np.mean(part ** 2)) or 1e-12)), 2),
                        "peak_dbfs": round(20 * float(np.log10(np.abs(part).max() or 1e-12)), 2),
                        "on_cut": any(abs(t - c) <= ON for c in cuts),
                        "on_reveal": any(abs(t - r) <= ON for r in reveals),
                    })
            rows.append({
                "run": run, "path": label, "seconds": round(seconds, 3), "scene_cuts": len(cuts),
                "composition_scenes": f.get("composition_scenes"), "picture_hold_merged": f.get("picture_hold_merged"),
                "scene_cuts_passed": "scene_cuts" in kw, "whooshes": len(bursts),
                "on_cut_or_reveal": sum(1 for w in bursts if w["on_cut"] or w["on_reveal"]),
                "mean_rms_dbfs": round(float(np.mean([w["rms_dbfs"] for w in bursts])), 2) if bursts else None,
                "times": bursts,
            })
    reels = [r for r in rows if r["path"] == "reel_9x16"]
    summary = {
        "reels": len(reels),
        "reel_whooshes": [r["whooshes"] for r in reels],
        "reel_max_whooshes": max(r["whooshes"] for r in reels),
        "reel_whooshes_off_cut_and_reveal": sum(r["whooshes"] - r["on_cut_or_reveal"] for r in reels),
        "reel_mean_whoosh_rms_dbfs": round(float(np.mean([w["rms_dbfs"] for r in reels for w in r["times"]])), 2)
        if any(r["times"] for r in reels) else None,
        "other_16x9": {r["path"]: r["whooshes"] for r in rows if r["path"] != "reel_9x16"},
        "acceptance": "every Reel <= 2 whooshes, each on a scene cut or a sound-plan reveal; level -6 dB vs before",
    }
    result = {"code_commit": _commit(root), "takes_scene_cuts": takes_cuts, "main_passes_scene_cuts": main_passes_cuts,
              "summary": summary, "rows": rows}
    out = Path(os.environ.get("RR_MEASURE_OUT") or HERE / "measurements.json")
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=1))
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: measure.py <repo root>")
    main(Path(sys.argv[1]).resolve())
