"""Spoken length is measured before the real voiceover, so a script that would run long or short is
rewritten in the script stage instead of failing after TTS (the 2026-10-20 Roman concrete run: 109
words, inside the word range, but 53.4 s spoken against a 52 s ceiling). Word count is a poor proxy:
on 16 measured narrations a syllable/pause formula still missed by up to 5.5 s, while the narrator
voice is deterministic (same text, same length), so the stage measures it instead of guessing."""
from src import config
from src.models import ScriptPackage


def _pkg(curated_payload):
    return ScriptPackage.model_validate(curated_payload)


def test_a_narration_slightly_long_is_read_faster_not_rewritten(monkeypatch, curated_payload):
    from src import audio_engine, script_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 53.6 / speed)
    assert script_engine.spoken_length_problems(_pkg(curated_payload)) == []  # 4% over: read faster
    assert script_engine.fit_speed(_pkg(curated_payload).narration_script) == 1.06


def test_script_that_would_run_long_is_sent_back_with_a_cut(monkeypatch, curated_payload):
    from src import audio_engine, script_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 60.0 / speed)
    problems = " ".join(script_engine.spoken_length_problems(_pkg(curated_payload)))
    assert "60.0" in problems and "rewrite the narration in " in problems and "-" in problems.split("in ")[-1]
    assert script_engine.fit_speed(_pkg(curated_payload).narration_script) is None  # 16% over: too much


def test_script_that_would_run_short_is_sent_back_with_an_extension(monkeypatch, curated_payload):
    from src import audio_engine, script_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 39.0)
    problems = " ".join(script_engine.spoken_length_problems(_pkg(curated_payload)))
    # A word window from this draft's count, as for a long draft ("add about 9 words" did not converge).
    import re
    words = len(_pkg(curated_payload).narration_script.split())
    low, high = (int(x) for x in re.search(r"extend the narration to (\d+)-(\d+) words", problems).groups())
    assert f"This draft is {words} words" in problems and words < low < high


def test_in_range_or_unmeasurable_script_passes(monkeypatch, curated_payload):
    from src import audio_engine, script_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 45.2)
    assert script_engine.spoken_length_problems(_pkg(curated_payload)) == []
    # Offline: nothing to measure; the check after the real voiceover still guards the run.
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: None)
    assert script_engine.spoken_length_problems(_pkg(curated_payload)) == []


def test_verify_claims_rejects_a_script_that_would_run_long(monkeypatch, curated_payload):
    from src import audio_engine
    from src.script_engine import ScriptEngine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 55.0)
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
    expected_text = audio_engine.VoiceProsody.prepare_speech_text(audio_engine.normalize_speech_text(raw), "edge-tts")
    assert seen["text"] == expected_text
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
    assert duration_problem(config.VOICE_DURATION_CEILING + 1)  # a recorded take: its own ceiling (2026-10-28)
    assert duration_problem(45.0) is None


def test_release_range_and_prompt_share_the_config_constants():
    from src.delivery_profiles import PROFILES
    from src.script_engine import SYSTEM_PROMPT
    reel = PROFILES["reel_9x16"]
    assert reel.duration_min == config.TARGET_DURATION_MIN
    assert reel.duration_max >= config.HARD_DURATION_CEILING
    assert f"{config.TARGET_WORD_COUNT_MIN}-{config.TARGET_WORD_COUNT_MAX}" in SYSTEM_PROMPT
    assert "145 wpm" not in SYSTEM_PROMPT  # the voice's measured pace is nothing like a fixed wpm


def test_the_writer_is_asked_for_the_length_that_fits_at_the_measured_pace(monkeypatch, tmp_path):
    # Production 2026-10-23: the fixed 105-115 words ran 53-58 s at 0.5 s a word, past the 51.5 s limit.
    from src import script_engine
    monkeypatch.setattr(script_engine, "WORD_PACE_FILE", tmp_path / "word_pace.json")
    assert script_engine.length_target_note() == ""  # not enough measurements yet
    for spw in (0.50, 0.51, 0.49):
        script_engine.record_word_pace(spw)
    note = script_engine.length_target_note()
    assert "in 85-96 words" in note and "0.50 s a word" in note  # 42-48 s at 0.5 s a word


def test_a_draft_too_long_to_keep_gets_no_graphics_planning(monkeypatch, curated_payload):
    from src import audio_engine, script_engine
    from src.models import ScriptCore
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 60.0 / speed)
    calls = []
    core = ScriptCore.model_validate({k: v for k, v in curated_payload.items() if k in ScriptCore.model_fields})
    monkeypatch.setattr(script_engine, "_structured", lambda system, prompt, schema, **k: calls.append(schema.__name__) or core)
    engine = script_engine.ScriptEngine()
    monkeypatch.setattr(engine, "_sheet", lambda topic: {"facts": [], "sources": []})
    monkeypatch.setattr(script_engine.fact_sheet, "as_prompt", lambda sheet: "")
    engine._generate_with_claude({"id": "t", "title": "T", "location": {}, "key_facts": [], "hook": ""}, "")
    assert calls == ["ScriptCore"]  # the graphics call is skipped for a draft that will be sent back
