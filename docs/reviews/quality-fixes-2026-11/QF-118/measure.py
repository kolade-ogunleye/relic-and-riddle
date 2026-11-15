"""QF-118 acceptance: the voice and bed mix gives the same samples on every run.

Usage: python measure.py REPO_ROOT [--base-root BASE_ROOT] [--dev-output OUTPUT_DIR]
(prints JSON; writes nothing outside a temporary directory; reads BASE_ROOT and OUTPUT_DIR only)

The mix of tests/test_release_integrity.py::test_real_ffmpeg_mix_preserves_stereo_bed (1 s of silent voice, a 0.1
440 Hz / 880 Hz stereo bed) through the tree's own video_composer._audio_chain, run RUNS times on the local ffmpeg:
without `end` (the test's call) and with the sample-exact end every production mix passes (compose_video and
end_card_level). Per variant: the side RMS (the test's bound is > 0.05) and the number of distinct decoded outputs.
Acceptance: one distinct output per variant, and the open mix equal to the sample-exact one.

--base-root (graphs.json): every audio filter graph production builds, on both trees, with compose_video's call
shapes (calibrate_gain's two passes, the second with the card's level; end_card_level; the delivered chain with
_master_chain and _seam_tail), over a fixed grid of voice lengths, card on and off, card levels, gains and input
indices; LOOP_SECONDS and LOOP_BRIDGE_EDGE are read from each tree. Prints how many are byte-identical.

--dev-output (dev_masters.json): the development masters (OUTPUT_DIR/run_*/*_master.mp4, read only), counts only:
how many, the months they were made, how many are mono (side RMS < 1e-4) and the smallest side RMS, how many end on
more than 5 ms of digital zeros and the longest, and how many have a failing audio check in their release report.
"""

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

RUNS = 35
# compose_video's production calls, over a fixed grid (graphs are strings: no ffmpeg runs)
VOICE_SECONDS = (3.0, 12.345, 47.21, 61.234, 120.0)
CARD_DB = (0.0, 1.5, -2.6)
GAIN_DB = (-3.2, 0.0, 4.75)
FIRST_AUDIO_INPUT = (1, 7, 23)
EMIT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from src import end_card
from src.audio_engine import AudioEngine
from src.encoded_audio import LOWPASS_HZ
from src.video_composer import _audio_chain, _master_chain, _seam_tail
grid = json.loads(sys.argv[2])
graphs = []
for vo in grid["voice"]:
    for card in (False, True):
        card_seconds = end_card.LOOP_SECONDS if card else 0.0
        end = vo + card_seconds
        for db in grid["card_db"]:
            card_at, card_db = (vo, db) if card else (None, 0.0)
            for gain in grid["gain"]:
                # calibrate_gain, first call (no card level), both passes
                base = _audio_chain(0, 1, card_seconds, None, 0.0, end=end)
                graphs += [base + f",lowpass=f={LOWPASS_HZ}:poles=2", base + _master_chain(gain)]
                if card:
                    # end_card_level, then calibrate_gain with the card's level, both passes
                    graphs.append(_audio_chain(0, 1, card_seconds, end=vo + card_seconds) + _master_chain(gain))
                    again = _audio_chain(0, 1, card_seconds, card_at, card_db, end=end)
                    graphs += [again + f",lowpass=f={LOWPASS_HZ}:poles=2", again + _master_chain(gain)]
                seam = _seam_tail(end, AudioEngine.LOOP_BRIDGE_EDGE) if card else ""
                for n in grid["first_input"]:     # the delivered chain inside compose_video's graph
                    graphs.append(_audio_chain(n, n + 1, card_seconds, card_at, card_db, end=end)
                                  + _master_chain(gain) + seam)
print(json.dumps(graphs))
'''


def production_graphs(root: Path) -> list[str]:
    grid = {"voice": VOICE_SECONDS, "card_db": CARD_DB, "gain": GAIN_DB, "first_input": FIRST_AUDIO_INPUT}
    out = subprocess.run([sys.executable, "-I", "-c", EMIT, str(root.resolve()), json.dumps(grid)],
                         capture_output=True, text=True, check=True, timeout=300)
    return json.loads(out.stdout.strip().splitlines()[-1])


def compare_graphs(head: Path, base: Path) -> dict:
    new, old = production_graphs(head), production_graphs(base)
    return {"grid": {"voice_seconds": list(VOICE_SECONDS), "card_db": list(CARD_DB), "gain_db": list(GAIN_DB),
                     "first_audio_input": list(FIRST_AUDIO_INPUT)},
            "production_graphs": len(new), "identical": sum(a == b for a, b in zip(new, old)),
            "same_count": len(new) == len(old)}


def dev_masters(output: Path) -> dict:
    """Counts only (no run names) for every development master under `output`, read only."""
    made, mono, tails, failing, sides = [], 0, [], 0, []
    for master in sorted(output.glob("run_*/*_master.mp4")):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(master), "-map", "0:a:0", "-f", "f32le", "-ac", "2",
                              "-ar", "48000", "-"], check=False, capture_output=True, timeout=600).stdout
        a = np.frombuffer(raw, np.float32).reshape(-1, 2)
        if not len(a):
            continue
        made.append(datetime.date.fromtimestamp(master.stat().st_mtime).isoformat())
        side = float(np.sqrt(np.mean((a[:, 0] - a[:, 1]) ** 2)))
        sides.append(side)
        mono += side < 1e-4
        loud = np.flatnonzero(np.abs(a).max(axis=1) > 0)
        tail_ms = (len(a) - 1 - loud[-1]) / 48 if len(loud) else len(a) / 48
        if tail_ms > 5:
            tails.append(tail_ms)
        report = master.parent / "release_report.json"
        if report.is_file():
            checks = json.loads(report.read_text(encoding="utf-8")).get("checks", [])
            failing += any(c.get("status") != "pass" for c in checks if c.get("name") in
                           ("channels", "sample_rate", "loudness", "true_peak", "sync_drift", "duration"))
    return {"masters": len(made), "made_first": min(made, default=None), "made_last": max(made, default=None),
            "made_on_or_after_2026_11_04": sum(d >= "2026-11-04" for d in made), "mono": mono,
            "side_rms_min": round(min(sides), 3) if sides else None,
            "tail_zero_over_5ms": len(tails), "tail_zero_ms_min": round(min(tails), 1) if tails else None,
            "tail_zero_ms_max": round(max(tails), 1) if tails else None, "failing_audio_check": failing}


def main(root: Path, base: Path | None = None, dev_output: Path | None = None) -> None:
    if base is not None or dev_output is not None:
        result = {"schema_version": 1}
        if base is not None:
            result["graphs"] = compare_graphs(root, base)
        if dev_output is not None:
            result["dev_masters"] = dev_masters(dev_output)
        print(json.dumps(result, indent=2))
        return
    sys.path.insert(0, str(root.resolve()))
    from src.video_composer import _audio_chain
    sr = 48000
    t = np.arange(sr) / sr
    version = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True).stdout.split()[2]
    result = {"schema_version": 1, "ffmpeg": version, "runs": RUNS, "variants": {}}
    digests = {}
    with tempfile.TemporaryDirectory(prefix="qf118-measure-") as directory:
        work = Path(directory)
        sf.write(work / "vo.wav", np.zeros(sr, dtype=np.float32), sr)
        sf.write(work / "bed.wav", np.column_stack([0.1 * np.sin(2 * np.pi * 440 * t),
                                                    0.1 * np.sin(2 * np.pi * 880 * t)]), sr)
        for name, chain in (("open_end", _audio_chain(0, 1)), ("sample_exact_end", _audio_chain(0, 1, end=1.0))):
            rms, seen = [], set()
            for _ in range(RUNS):
                out = work / f"{name}.wav"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(work / "vo.wav"), "-i", str(work / "bed.wav"),
                                "-filter_complex", chain + "[out]", "-map", "[out]", str(out)],
                               check=True, capture_output=True, timeout=60)
                mixed, _ = sf.read(out)
                rms.append(float(np.sqrt(np.mean((mixed[:, 0] - mixed[:, 1]) ** 2))))
                seen.add(hashlib.sha256(mixed.tobytes()).hexdigest())
            digests[name] = seen
            result["variants"][name] = {"side_rms_min": round(min(rms), 6), "side_rms_max": round(max(rms), 6),
                                        "distinct_outputs": len(seen), "below_test_bound_0_05": sum(r <= 0.05 for r in rms)}
    result["open_equals_sample_exact"] = digests["open_end"] == digests["sample_exact_end"]
    result["acceptance_met"] = (all(v["distinct_outputs"] == 1 for v in result["variants"].values())
                                and result["open_equals_sample_exact"])
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--base-root", type=Path)
    parser.add_argument("--dev-output", type=Path)
    args = parser.parse_args()
    main(args.root, args.base_root, args.dev_output)
