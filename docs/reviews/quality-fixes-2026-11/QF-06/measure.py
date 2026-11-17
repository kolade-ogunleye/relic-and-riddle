"""QF-06 SFX body and headroom: what the sound effects do to the master limiter and to the narration's level.

    python measure.py <repo root>

Runs the code of <repo root> (its src/: AudioEngine.generate_ambient_bed, VideoComposer.calibrate_gain and
end_card_level, the composer's _audio_chain/_master_chain/_seam_tail ffmpeg graphs, as VideoComposer.render
assembles them) on committed or generated inputs only:
- the fixture runs' words (docs/reviews/freeze-panel-2026-11-04/fixtures/runs/<run>/words.json) and their recorded
  voiceover length, sound plan and delivery profile (inputs.json beside this file, copied from QF-05's);
- the licensed SFX recordings committed in data/sfx;
- a narration stand-in generated here from the run's own word timings (a voiced, formant-shaped signal in each
  word, at the -20.3 LUFS / about -3 dBFS peak every recorded voiceover measures), and a music stand-in generated
  here (a slow chord pad with a little noise, 70 s), passed to generate_ambient_bed as its music track.
With RR_FIXTURE_REPO=<checkout with output/> the run's own voiceover.mp3 and its music_selection.json track are
used instead (the "real_audio" mode; the result records which).

For each run the bed is made the way main.py makes it (Reel: vo + bed_tail, end_card_at, scene cuts, sound plan;
the deep dive: vo + 0.5 s, its act cues), twice: as it is, and with every one-shot effect silenced (the setting
layer, a continuous texture, stays). The difference between the two beds is the SFX layer; each burst in it is one
cue. Each bed is then mastered exactly as the composer masters it (its own calibrated gain and end card level).

Per cue:
- gr_db: the master limiter's largest gain reduction inside the cue (2 ms blocks, the limited programme against the
  same graph with the master alimiter alone removed, aligned for its lookahead);
- gr_no_sfx_db: the same in the no-SFX master over the same window;
- narration_delta_lu: the narration's momentary loudness (BS.1770 K-weighted, 400 ms) inside the cue in the master,
  against the no-SFX master (the narration branch rendered alone through the same graph and gain, times the
  limiter's measured gain), the largest difference where the narration speaks (momentary above -45 LUFS).
And per synthesized hit (each synth passed for an impact): its f0 (the lowest spectral peak within 12 dB of the
strongest), its energy share below 80 Hz, 90-200 Hz and 1-3 kHz; per effect as mixed (before its cue gain): its peak.

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
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / "freeze-panel-2026-11-04" / "fixtures" / "runs"
SR = 48000
BLOCK = 96          # 2 ms
SPEAKS_LUFS = -45.0
VO_LUFS = -20.3     # every recorded voiceover (amber, gol gumbaz, nazca, tunguska: -20.3 LUFS, -2.8 to -3.5 dBFS)
IMPACTS = {"sfx_impact_braam.wav", "sfx_explosion_distant.wav", "sfx_thunder.wav"}
HIT_SYNTHS = ("_synth_sub_bass_braam", "_synth_chisel_strike")


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


def momentary(x):
    """Momentary loudness (LUFS) every 100 ms over 400 ms windows, and the window starts (s)."""
    np = _np()
    z = np.sum(k_weight(x) ** 2, axis=1)
    c = np.concatenate([[0.0], np.cumsum(z)])
    win, hop = int(0.4 * SR), int(0.1 * SR)
    starts = np.arange(0, max(1, len(z) - win + 1), hop)
    ms = (c[starts + win] - c[starts]) / win
    return starts / SR, -0.691 + 10 * np.log10(np.maximum(ms, 1e-12))


def integrated(x):
    """BS.1770 integrated loudness (absolute and relative gate)."""
    np = _np()
    _, m = momentary(x)
    m = m[m > -70]
    if not m.size:
        return -70.0
    rel = 10 * np.log10(np.mean(10 ** (m / 10))) - 10
    m = m[m > rel]
    return float(10 * np.log10(np.mean(10 ** (m / 10))))


def narration_standin(words, seconds, path):
    """A deterministic narration from the words' timing: a voiced source (a 118 Hz pulse train with slow drift),
    shaped by three formants, with a little breath noise, inside each word; levelled to VO_LUFS."""
    np = _np()
    from scipy.signal import butter, sosfilt
    n = int(seconds * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(6)
    f0 = 118.0 * (1 + 0.06 * np.sin(2 * np.pi * 0.31 * t) + 0.03 * np.sin(2 * np.pi * 1.7 * t))
    phase = np.cumsum(f0) / SR
    src = (np.diff(np.floor(phase), prepend=0.0) > 0).astype(float)          # one pulse per period
    src = sosfilt(butter(1, 900, "lp", fs=SR, output="sos"), src) * 8.0
    voiced = sum(sosfilt(butter(2, [f * 0.85, f * 1.15], "bp", fs=SR, output="sos"), src) * g
                 for f, g in ((550, 1.0), (1500, 0.55), (2600, 0.3)))
    voiced = voiced + 0.02 * sosfilt(butter(2, [2000, 6000], "bp", fs=SR, output="sos"), rng.standard_normal(n))
    env = np.zeros(n)
    for w in words:
        a = int(float(w.get("start", 0.0)) * SR)
        b = min(n, a + int(float(w.get("dur", w.get("end", 0.0) or 0.3)) * SR))
        if b - a < 8:
            continue
        k = np.arange(b - a) / SR
        ramp = np.minimum(1.0, np.minimum(k / 0.03, (k[-1] - k) / 0.04))
        syll = 0.55 + 0.45 * np.abs(np.sin(np.pi * k * 4.2 + a))               # syllables within the word
        env[a:b] = np.maximum(env[a:b], ramp * syll)
    x = voiced * env
    x = np.column_stack([x, x])
    x *= 10 ** ((VO_LUFS - integrated(x)) / 20)
    import soundfile as sf
    sf.write(str(path), x[:, 0].astype(np.float32), SR, subtype="FLOAT")
    return path


def music_standin(path, seconds=70.0):
    """A deterministic music track: a slowly breathing minor chord pad with a little low-passed noise."""
    np = _np()
    from scipy.signal import butter, sosfilt
    n = int(seconds * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    pad = sum(np.sin(2 * np.pi * f * t + i) * g * (0.7 + 0.3 * np.sin(2 * np.pi * (0.05 + 0.02 * i) * t))
              for i, (f, g) in enumerate(((110.0, 1.0), (164.8, 0.7), (220.0, 0.6), (261.6, 0.5), (329.6, 0.4),
                                          (440.0, 0.2))))
    noise = sosfilt(butter(2, 1800, "lp", fs=SR, output="sos"), rng.standard_normal(n)) * 0.4
    x = pad + noise
    x = np.column_stack([x, np.roll(x, 240)])
    x *= 0.3 / np.abs(x).max()
    import soundfile as sf
    sf.write(str(path), x.astype(np.float32), SR, subtype="FLOAT")
    return path


def f0_of(hit):
    """The lowest spectral peak within 12 dB of the strongest, and the energy shares of three bands."""
    np = _np()
    x = np.asarray(hit, dtype=float)
    x = x.mean(axis=1) if x.ndim > 1 else x
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    keep = (f >= 20) & (f <= 8000)
    s, fk = spec[keep], f[keep]
    floor = s.max() * 10 ** (-12 / 20)
    peaks = [i for i in range(1, len(s) - 1) if s[i] >= floor and s[i] >= s[i - 1] and s[i] >= s[i + 1]]
    e = spec ** 2
    tot = float(e.sum()) or 1.0

    def share(a, b):
        return round(100 * float(e[(f >= a) & (f < b)].sum()) / tot, 1)
    head = x[:int(0.05 * SR)]
    hs = np.abs(np.fft.rfft(head * np.hanning(len(head)))) ** 2
    hf = np.fft.rfftfreq(len(head), 1 / SR)
    attack = round(100 * float(hs[(hf >= 1000) & (hf < 3000)].sum()) / (float(hs.sum()) or 1.0), 1)
    return {"f0_hz": round(float(fk[peaks[0]]), 1) if peaks else None,
            "strongest_hz": round(float(fk[int(np.argmax(s))]), 1), "below_80_pct": share(0, 80),
            "body_90_200_pct": share(90, 200), "transient_1k_3k_pct": share(1000, 3000),
            "attack_50ms_1k_3k_pct": attack,
            "peak_dbfs": round(20 * float(np.log10(np.abs(x).max() or 1e-12)), 2)}


def render(chain, vo, bed):
    """The graph's output, stereo float at 48 kHz."""
    np = _np()
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(vo), "-i", str(bed),
                          "-filter_complex", chain, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True, timeout=600).stdout
    return np.frombuffer(out, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def lag_of(pre, post):
    """The limiter's delay (samples): the lag (0-40 ms) that best aligns the limited output with its input."""
    np = _np()
    a, b = pre[SR:6 * SR, 0], post[SR:7 * SR, 0]
    best, lag = -1.0, 0
    for k in range(int(0.04 * SR)):
        n = min(len(a), len(b) - k)
        c = float(np.dot(a[:n], b[k:k + n]))
        if c > best:
            best, lag = c, k
    return lag


def gain_blocks(pre, post, lag):
    """The limiter's gain per 2 ms block (1.0 where it does nothing), aligned to the input's time."""
    np = _np()
    n = min(len(pre), len(post) - lag)
    p, q = pre[:n], post[lag:lag + n]
    nb = n // BLOCK
    ep = np.sum(p[:nb * BLOCK].reshape(nb, BLOCK, 2) ** 2, axis=(1, 2))
    eq = np.sum(q[:nb * BLOCK].reshape(nb, BLOCK, 2) ** 2, axis=(1, 2))
    g = np.ones(nb)
    live = ep > 1e-10
    g[live] = np.minimum(1.0, np.sqrt(eq[live] / ep[live]))
    return g


class Master:
    """One bed mastered the composer's way: its gain, card level, the graph, and the signals measured from it."""

    def __init__(self, composer, vo, bed, card_seconds, vo_dur, prof, zero_bed):
        from src import video_composer as vc
        card = card_seconds > 0
        self.gain = composer.calibrate_gain(vo, bed, target_lufs=prof.target_lufs,
                                            target_true_peak=prof.target_true_peak, pad=card_seconds,
                                            end=vo_dur + card_seconds)
        card_at, card_db = (vo_dur if card else None), 0.0
        if card:
            lvl = composer.end_card_level(vo, bed, vo_dur, card_seconds, self.gain)
            card_db = lvl["correction_db"] if abs(lvl["correction_db"]) >= 0.5 else 0.0
            if card_db:
                self.gain = composer.calibrate_gain(vo, bed, target_lufs=prof.target_lufs,
                                                    target_true_peak=prof.target_true_peak, pad=card_seconds,
                                                    card_at=card_at, card_db=card_db,
                                                    end=vo_dur + card_seconds)
        from src.audio_engine import AudioEngine
        seam = vc._seam_tail(vo_dur + card_seconds, AudioEngine.LOOP_BRIDGE_EDGE) if card else ""
        head = vc._audio_chain(0, 1, card_seconds, card_at, card_db, end=vo_dur + card_seconds)
        master = vc._master_chain(self.gain)
        unlimited = re.sub(r"alimiter=[^,]*,", "anull,", master)
        assert unlimited != master
        self.card_db = card_db
        self.post = render(head + master + seam, vo, bed)
        self.pre = render(head + unlimited + seam, vo, bed)
        self.voice_pre = render(head + unlimited + seam, vo, zero_bed)
        self.lag = lag_of(self.pre, self.post)
        self.g = gain_blocks(self.pre, self.post, self.lag)
        np = _np()
        nb = len(self.g)
        v = self.voice_pre[:nb * BLOCK].reshape(nb, BLOCK, 2) * self.g[:, None, None]
        self.voice = v.reshape(-1, 2)                       # the narration as it plays in the limited master
        self.m_t, self.m = momentary(self.voice)
        self.lufs = integrated(self.post)
        self.peak_dbfs = 20 * float(np.log10(np.abs(self.post).max() or 1e-12))

    def gr(self, t0, t1):
        np = _np()
        a, b = int(t0 * SR) // BLOCK, max(int(t0 * SR) // BLOCK + 1, int(t1 * SR) // BLOCK)
        g = self.g[a:b]
        return round(-20 * float(np.log10(g.min())) if g.size else 0.0, 2)


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    os.chdir(root)   # src.config resolves data/ (the licensed SFX recordings) against the checkout
    import inspect

    import numpy as np
    import soundfile as sf

    from src import end_card
    from src.audio_engine import AudioEngine
    from src.delivery_profiles import get_delivery_profile
    from src.scene_planner import documentary_cuts, segment_beats
    from src.video_composer import VideoComposer, caption_cut_times

    takes_cuts = "scene_cuts" in inspect.signature(AudioEngine.generate_ambient_bed).parameters
    real = os.environ.get("RR_FIXTURE_REPO")
    facts = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))
    only = [r for r in (os.environ.get("RR_MEASURE_RUNS") or "").split(",") if r]
    if only:
        facts = {k: v for k, v in facts.items() if any(o in k for o in only)}
    tmp = Path(tempfile.mkdtemp(prefix="qf06_"))
    standin_music = music_standin(tmp / "music.wav")

    class Probe(AudioEngine):
        """Records every effect as it enters the mix (before its cue gain), and each hit synth passed for it."""

        def __init__(self, silence=False):
            super().__init__()
            self.silence, self.mixed, self.hits, self.bus = silence, [], [], None

        def _effect(self, name, synth, sr, loop_to=None):
            out = super()._effect(name, synth, sr, loop_to)
            if loop_to:
                return out                                   # the continuous setting layer: not a cue
            if name in IMPACTS:
                self.hits.append((name, np.array(synth)))
            if hasattr(AudioEngine, "_mix_effect"):
                return out * 0.0 if self.silence else out   # recorded where it is normalised (_mix_effect)
            self.mixed.append((name, out))
            return out * 0.0 if self.silence else out

        def limit_bus(self, bed, sr, *a, **k):
            out = super().limit_bus(bed, sr, *a, **k)
            lim = np.abs(out).max(axis=1) < np.abs(bed).max(axis=1) * 0.999
            self.bus = {"in_peak_dbfs": round(20 * float(np.log10(np.abs(bed).max() or 1e-12)), 2),
                        "out_peak_dbfs": round(20 * float(np.log10(np.abs(out).max() or 1e-12)), 2),
                        "limited_pct": round(100 * float(lim.mean()), 2)}
            return out

        def _mix_effect(self, name, synth, sr, room=None):
            out = super()._mix_effect(name, synth, sr, room)
            self.mixed.append((name, out))
            return out * 0.0 if self.silence else out

    def one(run, f, label, seconds, kw, card_seconds, vo_dur, prof):
        words = kw["words"]
        key = f"{run}_{label.split()[0]}"
        if real and (Path(real) / "output" / run / "voiceover.mp3").is_file():
            run_dir = Path(real) / "output" / run
            vo = run_dir / "voiceover.mp3"
            if (run_dir / "music_selection.json").is_file():
                sel = json.loads((run_dir / "music_selection.json").read_text(encoding="utf-8"))
                music = Path(real) / "data" / "music" / sel["file"]
            else:   # a deep dive keeps its score beside it
                music = next(iter(sorted(run_dir.glob("music_*.mp3"))), standin_music)
            mode = "real voiceover and music track"
        else:
            vo = narration_standin(words, vo_dur, tmp / f"{key}_vo.wav")
            music = standin_music
            mode = "generated narration and music stand-ins"
        eng, quiet = Probe(), Probe(silence=True)
        bed_a = eng.generate_ambient_bed(seconds, tmp / f"{key}_a.wav", music_path=music, **kw)
        bed_b = quiet.generate_ambient_bed(seconds, tmp / f"{key}_b.wav", music_path=music, **kw)
        x, _ = sf.read(str(bed_a), dtype="float64")
        y, _ = sf.read(str(bed_b), dtype="float64")
        zero = tmp / f"{key}_zero.wav"
        sf.write(str(zero), np.zeros_like(x, dtype=np.float32), prof.audio_sample_rate)
        layer = np.abs(x - y).max(axis=1)
        live = np.flatnonzero(layer > 1e-3)
        bursts = []
        if live.size:
            for seg in np.split(live, np.flatnonzero(np.diff(live) > int(0.5 * SR)) + 1):
                bursts.append((int(seg[0]) / SR, (int(seg[-1]) + 1) / SR))
        composer = VideoComposer()
        with_sfx = Master(composer, vo, bed_a, card_seconds, vo_dur, prof, zero)
        no_sfx = Master(composer, vo, bed_b, card_seconds, vo_dur, prof, zero)
        anchors = [(0.15, "opening hit")]
        for m in (kw.get("sound_plan") or {}).get("moments", []):
            if m.get("kind") == "reveal":
                anchors.append((float(m["t"]), "reveal hit"))
            if m.get("effect"):
                anchors.append((float(m["t"]), m["effect"]))
        anchors += [(float(t) - 0.15, "transition whoosh") for t in getattr(eng, "last_whoosh_times", [])]
        anchors += [(float(t), "act cue riser + hit") for t in kw.get("cue_timestamps") or []]
        if kw.get("end_card_at") is not None:
            anchors.append((float(kw["end_card_at"]), "end card"))
        cues = []
        for t0, t1 in bursts:
            near = min(anchors, key=lambda a: abs(a[0] - t0))
            sel_w = (with_sfx.m_t >= t0 - 0.2) & (with_sfx.m_t + 0.4 <= t1 + 0.2)
            n = min(len(with_sfx.m), len(no_sfx.m))
            sel_w = sel_w[:n] & (no_sfx.m[:n] > SPEAKS_LUFS)
            delta = (with_sfx.m[:n] - no_sfx.m[:n])[sel_w]
            cues.append({
                "t": round(t0, 3), "end": round(t1, 3), "what": near[1] if abs(near[0] - t0) < 0.6 else "effect",
                "gr_db": with_sfx.gr(t0, t1), "gr_no_sfx_db": no_sfx.gr(t0, t1),
                "narration_delta_lu": round(float(delta[np.argmax(np.abs(delta))]), 2) if delta.size else None,
                "bed_peak_dbfs": round(20 * float(np.log10(np.abs(x[int(t0 * SR):int(t1 * SR)]).max() or 1e-12)), 2),
            })
        return {
            "run": run, "path": label, "audio": mode, "seconds": round(seconds, 3),
            "gain_db": round(with_sfx.gain, 2), "gain_no_sfx_db": round(no_sfx.gain, 2),
            "lufs": round(with_sfx.lufs, 2), "peak_dbfs": round(with_sfx.peak_dbfs, 2),
            "limiter_lag_samples": with_sfx.lag,
            "max_gr_db": max((c["gr_db"] for c in cues), default=0.0),
            "max_abs_narration_delta_lu": max((abs(c["narration_delta_lu"]) for c in cues
                                               if c["narration_delta_lu"] is not None), default=0.0),
            "cues": cues,
            "effects_mixed": [{"name": nm, "peak_dbfs": round(20 * float(np.log10(np.abs(a).max() or 1e-12)), 2)}
                              for nm, a in eng.mixed],
            "hits": [{"name": nm, **f0_of(h)} for nm, h in eng.hits],
            "impacts_mixed": [{"name": nm, **f0_of(a)} for nm, a in eng.mixed if nm in IMPACTS],
            "bed_bus": eng.bus,
        }

    jobs = []
    for run, f in facts.items():
        words = json.loads((FIXTURES / run / "words.json").read_text(encoding="utf-8"))
        vo = float(f["vo_dur"])
        plan = f["sound_plan"]
        prof = get_delivery_profile(f["delivery_profile"])
        if f["deep_dive"]:
            cuts = caption_cut_times([c["end"] - c["start"] for c in documentary_cuts(words, vo)])
            kw = {"words": words, "cue_timestamps": f["act_cues"], "sound_plan": plan}
            if takes_cuts:
                kw["scene_cuts"] = cuts
            jobs.append((run, f, "deep_dive_16x9 (act cues)", vo + 0.5, kw, 0.0, vo, prof))
        else:
            card = prof.width < prof.height
            cuts = caption_cut_times([b["end"] - b["start"] for b in segment_beats(words, vo)])
            kw = {"words": words, "sr": prof.audio_sample_rate, "loop_seamless": prof.loop_seamless,
                  "sound_plan": plan, "end_card_at": vo if card else None}
            if takes_cuts:
                kw["scene_cuts"] = cuts
            jobs.append((run, f, "reel_9x16", vo + end_card.bed_tail(card), kw,
                         end_card.LOOP_SECONDS if card else 0.0, vo, prof))
    with ThreadPoolExecutor(max_workers=int(os.environ.get("RR_MEASURE_WORKERS") or 4)) as pool:
        rows = list(pool.map(lambda j: one(*j), jobs))

    # Each synthesized hit synth on its own, as generate_ambient_bed calls it (the opening hit passed f0=45.0 before)
    synths = {}
    for nm in HIT_SYNTHS:
        fn = getattr(AudioEngine, nm)
        synths[nm] = f0_of(fn(sr=SR))
    # Every palette cue as it enters the bed in a dry room, times its cue gain (gain x 0.40), and the whoosh and the
    # hit before their cue gains: the code's own path (_mix_effect; before it existed, _effect through the same
    # FoleyEngine filter and reverb generate_ambient_bed applied).
    from src.foley_engine import FoleyEngine
    eng = AudioEngine()
    levels = {}
    for name, spec in AudioEngine.SFX_CUES.items():
        synth = getattr(eng, spec[2] if len(spec) > 2 else "_synth_stone_grind")(sr=SR)
        synth = np.column_stack((synth, synth)) if synth.ndim == 1 else synth
        if hasattr(eng, "_mix_effect"):
            x = eng._mix_effect(name, synth, SR, room="dry")
        else:
            x = FoleyEngine.apply_environment_reverb(FoleyEngine.apply_spectral_filter(
                eng._effect(name, synth, SR), FoleyEngine.get_category(name), sr=SR), "dry", wet=0.20, sr=SR)
        levels[name] = x * spec[1] * 0.40
    for name, synth in (("sfx_whoosh.wav", AudioEngine._synth_transition_whoosh(0.45, SR)),
                        ("sfx_impact_braam.wav", AudioEngine._synth_sub_bass_braam(duration_sec=1.8, sr=SR))):
        levels[name] = eng._mix_effect(name, synth, SR) if hasattr(eng, "_mix_effect") else eng._effect(name, synth, SR)
    effect_levels = {k: {"rms_dbfs": round(20 * float(np.log10(np.sqrt(np.mean(np.square(v))) or 1e-12)), 1),
                         "peak_dbfs": round(20 * float(np.log10(np.abs(v).max() or 1e-12)), 1)}
                     for k, v in levels.items()}
    peaks = sorted({e["peak_dbfs"] for r in rows for e in r["effects_mixed"]})
    summary = {
        "runs": len(rows),
        "max_gr_db_at_any_cue": max(r["max_gr_db"] for r in rows),
        "max_abs_narration_delta_lu_at_any_cue": max(r["max_abs_narration_delta_lu"] for r in rows),
        "hit_f0_hz_min": min(h["f0_hz"] for r in rows for h in r["hits"]) if any(r["hits"] for r in rows) else None,
        "hit_synth_f0_hz": {k: v["f0_hz"] for k, v in synths.items()},
        "effect_peak_dbfs_range": [peaks[0], peaks[-1]] if peaks else None,
        "acceptance": "no synthesized hit f0 < 80 Hz; every effect peak-normalised; master limiter GR <= 3 dB at "
                      "every SFX cue; narration within 1 LU of its no-SFX level at every cue",
    }
    try:   # the code measured, by its committed src/ tree (never its local path; stable when a commit is amended)
        commit = "src tree " + subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD:src"],
                                              capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "src"], capture_output=True,
                               text=True, check=True, timeout=30).stdout.strip()
        code = commit + (" + uncommitted src changes" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        code = "unknown"
    result = {"code": code, "real_audio": bool(real), "summary": summary, "hit_synths": synths,
              "effect_levels": effect_levels, "rows": rows}
    out = Path(os.environ.get("RR_MEASURE_OUT") or HERE / "measurements.json")
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=1))
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: measure.py <repo root>")
    main(Path(sys.argv[1]).resolve())
