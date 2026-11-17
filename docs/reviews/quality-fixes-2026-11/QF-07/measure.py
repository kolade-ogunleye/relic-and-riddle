"""QF-07 Ducking: how far the ambient bed comes back up inside the narration's word gaps.

    python measure.py <repo root>

Runs the code of <repo root> (its src/: the composer's own _audio_chain ffmpeg graph, the voice cleanup, the bed
carve and the sidechain duck exactly as VideoComposer.render builds them) on committed or generated inputs only:
- the fixture runs' words (docs/reviews/freeze-panel-2026-11-04/fixtures/runs/<run>/words.json);
- a narration stand-in generated here from the run's own word timings (a voiced, formant-shaped signal inside each
  word, at the -20.3 LUFS every recorded voiceover measures; the same generator as QF-06's measure.py);
- a bed stand-in generated here (LABELLED: no library bed is committed): a steady minor chord pad with a little
  low-passed noise, at about -10 dBFS peak, as long as the narration.
With RR_FIXTURE_REPO=<checkout with output/> each run's own voiceover.mp3 and ambient_bed.wav are used instead (the
"real_audio" mode; every row records which).

The ducked bed is isolated from the graph's own output, with the graph string unchanged: the mix with the bed
(A), the mix with the bed silenced (B: the narration branch alone, bit-identical in both, since the key is the voice)
and the mix with the voice silenced (C: the bed undocked; a silent key does not duck). A - B is the ducked bed, and
its gain against C per 10 ms block is the duck.

Per run, for every gap between two words of 0.1-0.5 s (end of one word's "dur" to the next word's start):
- recovery_db: the bed's highest gain inside the gap minus its median gain over the word before the gap (how far
  the bed swells back between two words; 0 when it holds);
and over the whole run: duck_db, the median gain while a word sounds (the depth of the duck under speech), and the
bed's return after the narration's last word (the graph runs with the composer's own pad, TAIL_PAD s of silence after
the voice, as the Reel's loop bridge does): tail_1db_s, the seconds until the bed stays within 1 dB of its undocked
level, and bridge_end_db, its gain at the end of the Reel's loop bridge (end_card.bed_tail(True) s after the voice;
the median over the last 50 ms, or over the bed's own last 50 ms but 20 where a real bed ends first).
And the key: the graph segments fed by the voice's key branch (key_filters; none on the start commit, whose key is the
voice chain's own output), whether they band-limit it (key_band_limited, key_band_hz) and its hold (key_hold_s).
The acceptance metric is the median recovery_db over every gap of every run (<= 2 dB), with a band-limited key.

Prints JSON and writes measurements.json beside this file (or to the path in RR_MEASURE_OUT).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / "freeze-panel-2026-11-04" / "fixtures" / "runs"
SR = 48000
BLOCK = 480          # 10 ms
VO_LUFS = -20.3      # every recorded voiceover (amber, gol gumbaz, nazca, tunguska: -20.3 LUFS)
GAP_MIN, GAP_MAX = 0.1, 0.5
TAIL_PAD = 2.0      # s of silence after the voice (_audio_chain pad), so the bed's return is measured


def _np():
    import numpy as np
    return np


def k_weight(x):
    """BS.1770 K-weighting at 48 kHz (both stages), per channel."""
    from scipy.signal import lfilter
    b1 = [1.53512485958697, -2.69169618940638, 1.19839281085285]
    a1 = [1.0, -1.69065929318241, 0.73248077421585]
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, -1.99004745483398, 0.99007225036621]
    return lfilter(b2, a2, lfilter(b1, a1, x, axis=0), axis=0)


def integrated(x):
    """BS.1770 integrated loudness (absolute and relative gate)."""
    np = _np()
    z = np.sum(k_weight(x) ** 2, axis=1)
    c = np.concatenate([[0.0], np.cumsum(z)])
    win, hop = int(0.4 * SR), int(0.1 * SR)
    starts = np.arange(0, max(1, len(z) - win + 1), hop)
    m = -0.691 + 10 * np.log10(np.maximum((c[starts + win] - c[starts]) / win, 1e-12))
    m = m[m > -70]
    if not m.size:
        return -70.0
    rel = 10 * np.log10(np.mean(10 ** (m / 10))) - 10
    m = m[m > rel]
    return float(10 * np.log10(np.mean(10 ** (m / 10))))


def word_end(w):
    return float(w.get("start", 0.0)) + float(w.get("dur", (w.get("end") or 0.0) - w.get("start", 0.0)) or 0.3)


def narration_standin(words, seconds, path):
    """A deterministic narration from the words' timing (QF-06's generator): a 118 Hz pulse train with slow drift,
    shaped by three formants, with a little breath noise, inside each word; levelled to VO_LUFS."""
    np = _np()
    import soundfile as sf
    from scipy.signal import butter, sosfilt
    n = int(seconds * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(6)
    f0 = 118.0 * (1 + 0.06 * np.sin(2 * np.pi * 0.31 * t) + 0.03 * np.sin(2 * np.pi * 1.7 * t))
    phase = np.cumsum(f0) / SR
    src = (np.diff(np.floor(phase), prepend=0.0) > 0).astype(float)
    src = sosfilt(butter(1, 900, "lp", fs=SR, output="sos"), src) * 8.0
    voiced = sum(sosfilt(butter(2, [f * 0.85, f * 1.15], "bp", fs=SR, output="sos"), src) * g
                 for f, g in ((550, 1.0), (1500, 0.55), (2600, 0.3)))
    voiced = voiced + 0.02 * sosfilt(butter(2, [2000, 6000], "bp", fs=SR, output="sos"), rng.standard_normal(n))
    env = np.zeros(n)
    for w in words:
        a = int(float(w.get("start", 0.0)) * SR)
        b = min(n, int(word_end(w) * SR))
        if b - a < 8:
            continue
        k = np.arange(b - a) / SR
        ramp = np.minimum(1.0, np.minimum(k / 0.03, (k[-1] - k) / 0.04))
        syll = 0.55 + 0.45 * np.abs(np.sin(np.pi * k * 4.2 + a))
        env[a:b] = np.maximum(env[a:b], ramp * syll)
    x = voiced * env
    x = np.column_stack([x, x])
    x *= 10 ** ((VO_LUFS - integrated(x)) / 20)
    sf.write(str(path), x.astype(np.float32), SR, subtype="FLOAT")
    return path


def bed_standin(seconds, path):
    """A deterministic, steady bed (LABELLED stand-in): a minor chord pad with a little low-passed noise."""
    np = _np()
    import soundfile as sf
    from scipy.signal import butter, sosfilt
    n = int(seconds * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    pad = sum(np.sin(2 * np.pi * f * t + i) * g
              for i, (f, g) in enumerate(((110.0, 1.0), (164.8, 0.7), (220.0, 0.6), (261.6, 0.5), (329.6, 0.4),
                                          (440.0, 0.2))))
    x = pad + sosfilt(butter(2, 1800, "lp", fs=SR, output="sos"), rng.standard_normal(n)) * 0.4
    x = np.column_stack([x, np.roll(x, 240)])
    x *= 0.3 / np.abs(x).max()
    sf.write(str(path), x.astype(np.float32), SR, subtype="FLOAT")
    return path


def silent_like(src, path):
    """A silent file as long as src (the same length and rate, so the graph runs over the same span)."""
    np = _np()
    import soundfile as sf
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                         capture_output=True, text=True, check=True).stdout
    sf.write(str(path), np.zeros((int(float(out) * SR) + SR, 2), dtype=np.float32), SR, subtype="FLOAT")
    return path


def render(chain, vo, bed):
    """The graph's output, stereo float at 48 kHz."""
    np = _np()
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(vo), "-i", str(bed),
                          "-filter_complex", chain, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True, timeout=600).stdout
    return np.frombuffer(out, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def duck_gain_db(chain, vo, bed, tmp, key):
    """The duck's gain (dB) on the bed per 10 ms block, from the graph's own output (A - B against C)."""
    np = _np()
    a = render(chain, vo, bed)
    b = render(chain, vo, silent_like(bed, tmp / f"{key}_nobed.wav"))
    c = render(chain, silent_like(vo, tmp / f"{key}_novo.wav"), bed)
    n = min(len(a), len(b), len(c))
    nb = n // BLOCK
    ducked = (a[:nb * BLOCK] - b[:nb * BLOCK]).reshape(nb, BLOCK, 2)
    dry = c[:nb * BLOCK].reshape(nb, BLOCK, 2)
    ed, ec = np.sum(ducked ** 2, axis=(1, 2)), np.sum(dry ** 2, axis=(1, 2))
    g = np.full(nb, np.nan)
    live = ec > 1e-9
    g[live] = 10 * np.log10(np.maximum(ed[live], 1e-20) / ec[live])
    return g


def gaps_of(words, g):
    """Each 0.1-0.5 s gap: its span and how far the bed rises inside it above its level under the word before."""
    np = _np()
    rows = []
    for w, nxt in pairwise(words):
        t0, t1 = word_end(w), float(nxt.get("start", 0.0))
        if not GAP_MIN <= t1 - t0 <= GAP_MAX:
            continue
        ws, we = int(float(w.get("start", 0.0)) * SR) // BLOCK, int(t0 * SR) // BLOCK
        gs, ge = int(t0 * SR) // BLOCK, -(-int(t1 * SR) // BLOCK)
        under, inside = g[ws:max(we, ws + 1)], g[gs:ge]
        under, inside = under[~np.isnan(under)], inside[~np.isnan(inside)]
        if not under.size or not inside.size:
            continue
        rows.append({"start": round(t0, 3), "gap_s": round(t1 - t0, 3),
                     "recovery_db": round(max(0.0, float(inside.max() - np.median(under))), 2)})
    return rows


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    os.chdir(root)
    np = _np()
    from src import video_composer as vc

    real = os.environ.get("RR_FIXTURE_REPO")
    runs = sorted(p.name for p in FIXTURES.iterdir() if (p / "words.json").is_file())
    only = [r for r in (os.environ.get("RR_MEASURE_RUNS") or "").split(",") if r]
    if only:
        runs = [r for r in runs if any(o in r for o in only)]
    tmp = Path(tempfile.mkdtemp(prefix="qf07_"))
    from src import end_card
    bridge = end_card.bed_tail(True)
    chain = vc._audio_chain(0, 1, TAIL_PAD)
    sc = next((p for p in chain.split(";") if "sidechaincompress" in p), "")

    def one(run):
        words = [w for w in json.loads((FIXTURES / run / "words.json").read_text(encoding="utf-8"))
                 if float(w.get("dur", 0.3) or 0.0) > 0]
        run_dir = Path(real) / "output" / run if real else None
        if run_dir and (run_dir / "voiceover.mp3").is_file() and (run_dir / "ambient_bed.wav").is_file():
            vo, bed, mode = run_dir / "voiceover.mp3", run_dir / "ambient_bed.wav", "real voiceover and ambient bed"
        else:
            seconds = word_end(words[-1]) + 1.5
            vo = narration_standin(words, seconds, tmp / f"{run}_vo.wav")
            bed = bed_standin(seconds + TAIL_PAD + 1.0, tmp / f"{run}_bed.wav")
            mode = "generated narration stand-in and bed stand-in (no library bed is committed)"
        g = duck_gain_db(chain, vo, bed, tmp, run)
        speech = np.zeros(len(g), dtype=bool)
        for w in words:
            speech[int(float(w["start"]) * SR) // BLOCK:int(word_end(w) * SR) // BLOCK] = True
        under = g[speech[:len(g)] & ~np.isnan(g)]
        gaps = gaps_of(words, g)
        vo_end = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                       "csv=p=0", str(vo)], capture_output=True, text=True, check=True).stdout)
        last = int(word_end(words[-1]) * SR) // BLOCK
        valid = np.flatnonzero(~np.isnan(g))
        after = g[last:valid[-1] + 1]                       # up to the bed's end (a real bed ends at the bridge)
        low = np.flatnonzero(after < -1.0)
        # None: still more than 1 dB down where the bed ends
        tail = None if low.size and low[-1] == len(after) - 1 else ((low[-1] + 1) * BLOCK / SR if low.size else 0.0)
        # the bed's level over the last 50 ms of the loop bridge, or of the bed where it ends first; the bed's own
        # last 20 ms are left out (sidechaincompress drops what is queued at the first EOF it sees, so that block
        # depends on how ffmpeg happened to frame the inputs)
        at = min(int(valid[-1]) - 2, int((vo_end + bridge) * SR) // BLOCK - 1)
        end_db = float(np.nanmedian(g[at - 4:at + 1]))
        rec = [r["recovery_db"] for r in gaps]
        return {"run": run, "audio": mode, "gaps": len(gaps),
                "median_recovery_db": round(float(np.median(rec)), 2) if rec else None,
                "p90_recovery_db": round(float(np.percentile(rec, 90)), 2) if rec else None,
                "duck_db": round(float(np.median(under)), 2) if under.size else None,
                "tail_1db_s": None if tail is None else round(float(tail), 2), "bridge_end_db": round(end_db, 2),
                "gap_rows": gaps}

    with ThreadPoolExecutor(max_workers=int(os.environ.get("RR_MEASURE_WORKERS") or 4)) as pool:
        rows = list(pool.map(one, runs))
    rec = [g["recovery_db"] for r in rows for g in r["gap_rows"]]
    ducks = [r["duck_db"] for r in rows if r["duck_db"] is not None]
    # the key's own filters: every graph segment fed by the voice's key branch (none on the start commit, whose key is
    # the voice chain's asplit output itself, full band above its 85 Hz rumble cut)
    key_filters = ";".join(p for p in chain.split(";") if p.startswith(("[v_sc]", "[v_key"))) or None
    result = {
        "metric": "median bed recovery inside 0.1-0.5 s word gaps (dB; acceptance <= 2)",
        "median_recovery_db": round(float(np.median(rec)), 2) if rec else None,
        "p90_recovery_db": round(float(np.percentile(rec, 90)), 2) if rec else None,
        "gaps": len(rec),
        "median_duck_db": round(float(np.median(ducks)), 2) if ducks else None,
        "max_tail_1db_s": max((r["tail_1db_s"] for r in rows if r["tail_1db_s"] is not None), default=None),
        "tail_beyond_bed": sum(r["tail_1db_s"] is None for r in rows),
        "min_bridge_end_db": min(r["bridge_end_db"] for r in rows),
        "max_bridge_end_db": max(r["bridge_end_db"] for r in rows),
        "sidechain": sc,
        "key_filters": key_filters,
        "key_band_limited": bool(key_filters and "highpass" in key_filters and "lowpass" in key_filters),
        "key_band_hz": [int(f) for f in re.findall(r"(?:high|low)pass=f=(\d+)", key_filters or "")[:2]] or None,
        "key_hold_s": max((int(d) for d in re.findall(r"adelay=delays=(\d+)", key_filters or "")),
                          default=0) / 1000,
        "runs": [{k: v for k, v in r.items() if k != "gap_rows"} for r in rows],
        "gap_rows": {r["run"]: r["gap_rows"] for r in rows},
    }
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python measure.py <repo root>")
    out = Path(os.environ.get("RR_MEASURE_OUT") or HERE / "measurements.json").resolve()   # before main's chdir
    res = main(Path(sys.argv[1]).resolve())
    out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in res.items() if k != "gap_rows"}, indent=1))
