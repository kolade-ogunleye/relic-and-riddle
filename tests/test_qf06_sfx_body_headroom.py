"""QF-06 (2026-11-05): SFX body and headroom.

The impact hits were a 45-46 Hz tone, which a phone speaker cannot play; every effect recording was set to the RMS of
the synthesized stand-in it replaces, so its peak went wherever its crest factor took it (up to +3 dBFS before its cue
gain on the fixture runs); and nothing limited the bed before the mix, so a pile-up of effects reached the master
limiter and turned the narration down with it. Now the impacts have a 90-200 Hz body and a 1-3 kHz transient, every
one-shot effect enters the bed at one peak (SFX_PEAK_DBFS) before its cue gain, and the bed bus is peak-limited on its
own (SFX_BUS_CEILING_DBFS) before the narration is mixed in.

Inputs: the Gol Gumbaz Reel's own word timings (the freeze-panel fixtures), the licensed recordings committed in
data/sfx, and a narration generated from those timings by the QF-06 measure (its stand-in, measure.narration_standin).
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from src import config, end_card
from src.audio_engine import AudioEngine
from src.delivery_profiles import get_delivery_profile
from src.video_composer import VideoComposer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
sys.path.insert(0, str(ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-06"))
import fixtures as _fx
import measure as _m

RUN = Path("output") / "run_gol_gumbaz_whispering_gallery_1793278143"   # its committed copy (fixtures/runs)
SR = 48000
IMPACTS = {"sfx_impact_braam.wav", "sfx_explosion_distant.wav"}
PEAK_DB = getattr(config, "SFX_PEAK_DBFS", -17.0)
CEILING_DB = getattr(config, "SFX_BUS_CEILING_DBFS", -6.0)


def _words():
    return json.loads(_fx.run_file(RUN, "words.json").read_text(encoding="utf-8"))


def _hit_synths(tmp_path):
    """Every synthesized impact a bed asks for, as each of its call sites makes it (opening, act cue, reveal, and
    the explosion's stand-in)."""
    hits = []

    class Spy(AudioEngine):
        def _effect(self, name, synth, sr, loop_to=None):
            if name in IMPACTS:
                hits.append(np.array(synth))
            return synth

    plan = {"moments": [{"t": 6.0, "kind": "reveal", "effect": None},
                        {"t": 10.0, "kind": "action", "effect": "sfx_explosion_distant.wav"}]}
    Spy().generate_ambient_bed(14.0, tmp_path / "bed.wav", words=_words(), sr=SR, sound_plan=plan,
                               cue_timestamps=[3.0])
    return hits


def test_every_synthesized_hit_has_a_body_a_phone_plays_and_a_transient(tmp_path):
    hits = _hit_synths(tmp_path)
    assert len(hits) == 4
    for hit in hits:
        shape = _m.f0_of(hit)
        assert shape["f0_hz"] >= 80.0, shape                 # was 45.3-46.1 Hz
        assert 90.0 <= shape["strongest_hz"] <= 200.0, shape
        assert shape["below_80_pct"] < 5.0, shape            # was 99 % of the energy
        assert shape["body_90_200_pct"] > 80.0, shape        # was 1 %
        assert shape["attack_50ms_1k_3k_pct"] > 2.0, shape   # the crack on the attack (was 0 %)


class _Silent(AudioEngine):
    """The same bed with every one-shot effect silenced (the continuous setting layer stays)."""

    def _effect(self, name, synth, sr, loop_to=None):
        out = super()._effect(name, synth, sr, loop_to)
        return out if loop_to or hasattr(AudioEngine, "_mix_effect") else out * 0.0

    def _mix_effect(self, name, synth, sr, room=None):
        return super()._mix_effect(name, synth, sr, room) * 0.0


def _sfx_layer(tmp_path, seconds, **kw):
    a = AudioEngine().generate_ambient_bed(seconds, tmp_path / "a.wav", **kw)
    b = _Silent().generate_ambient_bed(seconds, tmp_path / "b.wav", **kw)
    x, _ = sf.read(str(a), dtype="float64")
    y, _ = sf.read(str(b), dtype="float64")
    return x, x - y


def test_every_effect_enters_the_bed_at_one_peak_before_its_cue_gain(tmp_path):
    # The opening hit, the reveal hit and two licensed recordings 4 s apart; no scene cut, so no whoosh.
    plan = {"moments": [{"t": 6.0, "kind": "reveal", "effect": None},
                        {"t": 10.0, "kind": "action", "effect": "sfx_hammer.wav"},
                        {"t": 14.0, "kind": "action", "effect": "sfx_chisel.wav"}]}
    _, layer = _sfx_layer(tmp_path, 18.0, words=_words(), sr=SR, sound_plan=plan, scene_cuts=[])
    gains = {0.15: 0.70, 6.0: 0.75, 10.0: AudioEngine.SFX_CUES["sfx_hammer.wav"][1] * 0.40,
             14.0: AudioEngine.SFX_CUES["sfx_chisel.wav"][1] * 0.40}
    for t, gain in gains.items():
        part = layer[int(t * SR):int((t + 3.8) * SR)]
        peak = 20 * np.log10(np.abs(part).max())
        assert abs(peak - (PEAK_DB + 20 * np.log10(gain))) < 0.05, (t, peak)   # before: -16.1 to +3 dBFS x gain


def test_the_bed_bus_is_limited_on_its_own():
    t = np.arange(2 * SR) / SR
    quiet = np.column_stack([0.05 * np.sin(2 * np.pi * 220 * t)] * 2)
    assert AudioEngine.limit_bus(quiet, SR) is quiet          # under the ceiling: untouched
    loud = quiet.copy()
    loud[SR // 2:SR // 2 + 480] += 0.9 * np.sin(2 * np.pi * 1000 * t[:480])[:, None]   # a 10 ms pile-up
    out = AudioEngine.limit_bus(loud, SR)
    ceiling = 10 ** (CEILING_DB / 20)
    assert np.abs(out).max() <= ceiling + 1e-9
    assert np.allclose(out[:SR // 2 - 480], loud[:SR // 2 - 480], rtol=1e-9, atol=1e-12)   # nothing before the look-ahead
    assert np.allclose(out[3 * SR // 2:], loud[3 * SR // 2:], rtol=1e-3)   # recovered (eight release times later)


def test_a_pile_up_of_effects_leaves_the_master_limiter_and_the_narration_alone(tmp_path):
    # Six effects on one spoken word (the narration speaks from 1.9 s): the worst a plan can do to the mix.
    words = [w for w in _words() if float(w["start"]) < 9.0]
    vo_dur = 9.0
    at = next(float(w["start"]) for w in words if float(w["start"]) > 4.0)
    plan = {"moments": [{"t": at, "kind": "reveal", "effect": None}]
            + [{"t": at, "kind": "action", "effect": e} for e in
               ("sfx_hammer.wav", "sfx_chisel.wav", "sfx_sword_clash.wav", "sfx_explosion_distant.wav",
                "sfx_stone_door.wav")]}
    prof = get_delivery_profile("reel_9x16")
    seconds = vo_dur + end_card.bed_tail(True)
    kw = {"words": words, "sr": SR, "loop_seamless": prof.loop_seamless, "sound_plan": plan,
          "end_card_at": vo_dur, "scene_cuts": []}
    a = AudioEngine().generate_ambient_bed(seconds, tmp_path / "a.wav", **kw)
    b = _Silent().generate_ambient_bed(seconds, tmp_path / "b.wav", **kw)
    bed, _ = sf.read(str(a), dtype="float64")
    assert np.abs(bed).max() <= 10 ** (CEILING_DB / 20) + 1e-6
    vo = _m.narration_standin(words, vo_dur, tmp_path / "vo.wav")
    zero = tmp_path / "zero.wav"
    sf.write(str(zero), np.zeros_like(bed, dtype=np.float32), SR)
    composer = VideoComposer()
    with_sfx = _m.Master(composer, vo, a, end_card.LOOP_SECONDS, vo_dur, prof, zero)
    no_sfx = _m.Master(composer, vo, b, end_card.LOOP_SECONDS, vo_dur, prof, zero)
    assert with_sfx.gr(at, at + 2.0) <= 3.0, with_sfx.gr(at, at + 2.0)
    n = min(len(with_sfx.m), len(no_sfx.m))
    speaking = (with_sfx.m_t[:n] >= at - 0.2) & (with_sfx.m_t[:n] + 0.4 <= at + 2.2) & (no_sfx.m[:n] > _m.SPEAKS_LUFS)
    assert speaking.any()
    delta = np.abs(with_sfx.m[:n] - no_sfx.m[:n])[speaking]
    assert delta.max() <= 1.0, delta.max()
