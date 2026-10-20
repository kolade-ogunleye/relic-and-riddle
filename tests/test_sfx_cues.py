"""Sound effects fire on the words that mean them, matched as whole words: substring matching put
an ocean surge under "research", and topic-agnostic layers (a cavern wind, a revelation hit on
"never") played on every topic."""
from src.audio_engine import AudioEngine


def words(text):
    return [{"word": w, "start": float(i), "dur": 0.4} for i, w in enumerate(text.split())]


def test_cues_match_whole_words_only():
    cues = AudioEngine.sfx_cue_times(words("New research shows the outdoor dialogue was recorded."))
    assert cues == {}
    cues = AudioEngine.sfx_cue_times(words("Sponge divers found the wreck under the sea."))
    assert set(cues) == {"sfx_water_surge.wav"} and cues["sfx_water_surge.wav"] == 1.0  # "divers"


def test_cues_do_not_misdescribe():
    # An underground city's depth is not water; a bronze statue is not clockwork; a wall is not a hammer blow.
    assert AudioEngine.sfx_cue_times(words("Its depth and bronze walls amazed visitors.")) == {}
    cues = AudioEngine.sfx_cue_times(words("He knocked through a wall and found doors carved from rock."))
    assert set(cues) == {"sfx_hammer.wav", "sfx_stone_door.wav", "sfx_chisel.wav"}


def test_cavern_wind_only_underground():
    assert AudioEngine.is_underground(words("Tunnels and ventilation shafts ran under the city."))
    assert not AudioEngine.is_underground(words("Roman concrete harbours still stand today."))


def test_revelation_hit_is_a_real_revelation():
    assert AudioEngine.revelation_time(words("It would never be the same.")) is None
    assert AudioEngine.revelation_time(words("Then divers discovered a corroded lump.")) == 2.0
