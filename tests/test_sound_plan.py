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
    layers = AudioEngine.SETTING_LAYERS
    cue_effects = [n for n in placed if n in AudioEngine.SFX_CUES and n not in layers]
    assert cue_effects == ["sfx_chisel.wav"]  # the word lists would also have fired hammer and the wreck
    assert placed.count("sfx_impact_braam.wav") == 1  # the opening hit only: the plan marks no reveal
    assert [n for n in placed if n in layers] in ([], ["sfx_sea_waves.wav"])  # "the sea": its layer, when the recording exists


def test_no_model_means_word_cue_fallback(monkeypatch):
    monkeypatch.setattr(sp.free_llm, "ready", lambda: False)
    assert sp.plan(WORDS) is None


def test_one_palette_feeds_the_plan_the_bed_and_the_generator():
    # Quality step 4 (2026-10-25): three episodes planned moments and chose no effect from the five on offer.
    from src import sfx_palette
    assert set(sp.EFFECTS) == set(AudioEngine.SFX_CUES) and len(sp.EFFECTS) >= 20
    assert set(AudioEngine.SETTING_LAYERS) >= {"sfx_cavern_wind.wav", "sfx_desert_wind.wav", "sfx_sea_waves.wav", "sfx_mountain_wind.wav"}
    assert list(AudioEngine.SETTING_LAYERS)[0] == "sfx_cavern_wind.wav"           # underground keeps precedence
    owners = {}
    for name, effect in sfx_palette.PALETTE.items():                                # a spoken word names one effect only
        for word in effect.keywords + effect.layer_words:
            assert word == word.lower() and word.isalpha(), (name, word)
            assert word not in owners, (word, owners.get(word), name)
            owners[word] = name
        assert hasattr(AudioEngine, effect.synth), (name, effect.synth)
    assert "sfx_impact_braam.wav" in sfx_palette.PALETTE                           # the hit in every episode is in the pack
    assert 2000 <= sfx_palette.credit_estimate() <= 4000                            # the whole pack is a small spend


def test_setting_layer_follows_the_narration():
    words = lambda text: [{"word": w, "start": i * 0.5, "dur": 0.4} for i, w in enumerate(text.split())]  # noqa: E731
    assert AudioEngine.setting_layer(words("A ring of rock in the western desert, seen from orbit")) == "sfx_desert_wind.wav"
    assert AudioEngine.setting_layer(words("Three keepers vanished from the lighthouse on the isle")) == "sfx_sea_waves.wav"
    assert AudioEngine.setting_layer(words("Carved from the mountain cliff, then dug underground into caves and tunnels")) == "sfx_cavern_wind.wav"
    assert AudioEngine.setting_layer(words("Fire on the sea, then sand on the shore")) == "sfx_sea_waves.wav"  # the setting named most
    assert AudioEngine.setting_layer(words("Greek fire burned ships; sand put it out")) is None            # one passing mention is no setting
    assert AudioEngine.setting_layer(words("The mechanism counted the months and the games")) is None
    assert AudioEngine.setting_layer(words("Desertion was punished; sandy soil")) is None                # whole words only
    assert AudioEngine.sfx_cue_times(words("Their swords rang on the anvil before the war"))["sfx_sword_clash.wav"] == 0.5


def test_a_setting_layer_sits_under_the_music_not_in_silence(tmp_path):
    import soundfile as sf
    eng = AudioEngine()
    sr = 48000
    words = [{"word": w, "start": i * 0.5, "dur": 0.4} for i, w in enumerate("deep underground tunnels were dug".split())]
    bed_path = eng.generate_ambient_bed(8.0, tmp_path / "bed.wav", words=words, sr=sr)
    quiet = eng.generate_ambient_bed(8.0, tmp_path / "quiet.wav", words=[{"word": "nothing", "start": 0.5, "dur": 0.4}], sr=sr)
    with_layer, _ = sf.read(str(bed_path))
    without, _ = sf.read(str(quiet))
    layer = with_layer[3 * sr:5 * sr] - without[3 * sr:5 * sr]                  # the middle: past the fades, no hits
    level_db = 20 * np.log10(np.sqrt(np.mean(layer ** 2)) / eng.MUSIC_RMS)
    assert -17 < level_db < -11, level_db                                        # about 14 dB under the music bed

