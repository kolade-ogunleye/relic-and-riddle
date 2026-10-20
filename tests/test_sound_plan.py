"""Sound follows the story: the model marks moments; code decides what is used."""
import numpy as np

from src import sound_plan as sp
from src.audio_engine import AudioEngine

WORDS = [{"word": w, "start": i * 0.5, "dur": 0.4} for i, w in enumerate(
    "Workers carved the doors and hammered stone then divers found the wreck in the sea".split())]


def test_validation_keeps_only_known_effects_spaced_and_timed():
    raw = sp.SoundPlan(moments=[
        sp.SoundMoment(word=1, kind="action", effect="sfx_chisel.wav"),           # t=0.0: too early, dropped
        sp.SoundMoment(word=3, kind="action", effect="sfx_chisel.wav"),           # t=1.0 kept
        sp.SoundMoment(word=6, kind="action", effect="sfx_hammer.wav"),           # t=2.5, 1.5 s after: effect dropped
        sp.SoundMoment(word=10, kind="reveal", effect="sfx_explosion.wav"),       # unknown effect: moment kept, no effect
        sp.SoundMoment(word=99, kind="calm"),                                     # no such word
    ])
    plan = sp.validate(raw, WORDS)
    assert plan["moments"] == [{"t": 1.0, "kind": "action", "effect": "sfx_chisel.wav"},
                               {"t": 2.5, "kind": "action", "effect": None},
                               {"t": 4.5, "kind": "reveal", "effect": None}]


def test_music_swells_on_reveals_and_dips_on_calm():
    sr = 1000
    env = AudioEngine.music_envelope([{"t": 2.0, "kind": "reveal"}, {"t": 6.0, "kind": "calm"}], 10 * sr, sr)
    assert env[int(2.6 * sr)] > 1.2 and abs(env[int(0.5 * sr)] - 1.0) < 1e-6 and env[int(7.25 * sr)] < 0.75
    assert env.max() <= 10 ** (4 / 20) + 1e-6 and env.min() >= 10 ** (-6 / 20) - 1e-6


def test_bed_uses_the_plans_effects_instead_of_word_lists(monkeypatch, tmp_path):
    eng = AudioEngine()
    placed = []
    monkeypatch.setattr(eng, "_effect", lambda name, synth, sr, loop_to=None: placed.append(name) or synth)
    plan = {"moments": [{"t": 1.0, "kind": "action", "effect": "sfx_chisel.wav"}]}
    eng.generate_ambient_bed(6.0, tmp_path / "bed.wav", words=WORDS, sound_plan=plan)
    cue_effects = [n for n in placed if n in AudioEngine.SFX_CUES]
    assert cue_effects == ["sfx_chisel.wav"]  # the word lists would also have fired hammer and water
    assert placed.count("sfx_impact_braam.wav") == 1  # the opening hit only: the plan marks no reveal


def test_no_model_means_word_cue_fallback(monkeypatch):
    monkeypatch.setattr(sp.free_llm, "ready", lambda: False)
    assert sp.plan(WORDS) is None
