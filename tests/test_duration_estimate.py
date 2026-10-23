"""Spoken length is measured before the real voiceover, so a script that would run long or short is
rewritten in the script stage instead of failing after TTS (the 2026-10-20 Roman concrete run: 109
words, inside the word range, but 53.4 s spoken against a 52 s ceiling). Word count is a poor proxy:
on 16 measured narrations a syllable/pause formula still missed by up to 5.5 s, while the narrator
voice is deterministic (same text, same length), so the stage measures it instead of guessing."""
from src import config
from src.models import ScriptPackage


def _pkg(curated_payload):
    return ScriptPackage.model_validate(curated_payload)


def test_script_that_would_run_long_is_sent_back_with_a_cut(monkeypatch, curated_payload):
    from src import audio_engine, script_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text: 53.6)
    problems = " ".join(script_engine.spoken_length_problems(_pkg(curated_payload)))
    assert "53.6" in problems and "rewrite the narration in at most" in problems


def test_script_that_would_run_short_is_sent_back_with_an_extension(monkeypatch, curated_payload):
    from src import audio_engine, script_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text: 39.0)
    assert "add about" in " ".join(script_engine.spoken_length_problems(_pkg(curated_payload)))


def test_in_range_or_unmeasurable_script_passes(monkeypatch, curated_payload):
    from src import audio_engine, script_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text: 45.2)
    assert script_engine.spoken_length_problems(_pkg(curated_payload)) == []
    # Offline: nothing to measure; the check after the real voiceover still guards the run.
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text: None)
    assert script_engine.spoken_length_problems(_pkg(curated_payload)) == []


def test_verify_claims_rejects_a_script_that_would_run_long(monkeypatch, curated_payload):
    from src import audio_engine
    from src.script_engine import ScriptEngine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text: 55.0)
    ok, _, msg = ScriptEngine().verify_claims(dict(curated_payload), allow_unreviewed_curated=True)
    assert not ok and "spoken" in msg


def test_measurement_speaks_what_the_voiceover_will_speak(monkeypatch, real_measure):
    from src import audio_engine
    seen = {}

    class FakeCommunicate:
        def __init__(self, text, voice, rate=None, pitch=None, **_):
            seen.update(text=text, voice=voice, rate=rate, pitch=pitch)

        async def save(self, path):
            open(path, "wb").write(b"x" * 1000)

    def run(coro):  # the test sandbox blocks the socketpair an event loop needs on Windows
        try:
            coro.send(None)
        except StopIteration as done:
            return done.value

    monkeypatch.setattr(audio_engine.edge_tts, "Communicate", FakeCommunicate)
    monkeypatch.setattr(audio_engine.asyncio, "run", run)
    monkeypatch.setattr(audio_engine.asyncio, "wait_for", lambda aw, timeout: aw)
    monkeypatch.setattr(audio_engine.AudioEngine, "get_duration", staticmethod(lambda p: 47.5))
    raw = "It ran 280 feet (85 m) down."
    assert real_measure(raw) == 47.5
    assert seen["text"] == audio_engine.normalize_speech_text(raw)
    assert (seen["voice"], seen["rate"], seen["pitch"]) == (config.NARRATOR_VOICE, config.NARRATOR_RATE, config.NARRATOR_PITCH)


def test_measurement_failure_returns_none(monkeypatch, real_measure):
    from src import audio_engine

    class Broken:
        def __init__(self, *a, **k):
            raise OSError("offline")

    monkeypatch.setattr(audio_engine.edge_tts, "Communicate", Broken)
    assert real_measure("Some text.") is None


def test_measured_short_voiceover_fails_before_render():
    from src.script_engine import duration_problem
    assert "short" in duration_problem(config.TARGET_DURATION_MIN - 2)
    assert duration_problem(config.HARD_DURATION_CEILING + 1)
    assert duration_problem(45.0) is None


def test_release_range_and_prompt_share_the_config_constants():
    from src.delivery_profiles import PROFILES
    from src.script_engine import SYSTEM_PROMPT
    reel = PROFILES["reel_9x16"]
    assert reel.duration_min == config.TARGET_DURATION_MIN
    assert reel.duration_max >= config.HARD_DURATION_CEILING
    assert f"{config.TARGET_WORD_COUNT_MIN}-{config.TARGET_WORD_COUNT_MAX}" in SYSTEM_PROMPT
    assert "145 wpm" not in SYSTEM_PROMPT  # the voice's measured pace is nothing like a fixed wpm
