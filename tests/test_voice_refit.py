"""A voiceover that reads over the ceiling is made once more at the narrator's own speed setting, raised by
the measured overrun (production, 2026-10-25: 114 words in 53.7 s against 52); the estimate plans for the
slow end until the narrator has been measured enough."""
import json

from src import audio_engine, config
from src.script_engine import HARD_DURATION_CEILING, refit_speed


def test_an_overrun_is_refit_natively_within_the_cap(monkeypatch):
    monkeypatch.setattr(config, "SPEECH_SPEEDUP_MAX", 1.08)
    assert refit_speed(51.9) is None                                              # fits: no second take
    s = refit_speed(53.7)
    assert s == 1.05 and 53.7 / s < HARD_DURATION_CEILING                        # the production case, now fitting
    assert refit_speed(53.7, speed=1.03) == 1.08                                   # compounds an earlier speed-up
    assert refit_speed(60.0) is None                                              # beyond the cap: still fails


def test_the_pace_plans_for_the_slowest_take_until_it_is_measured_enough(monkeypatch, tmp_path):
    pace = tmp_path / "voice_pace.json"
    monkeypatch.setattr(audio_engine, "PACE_FILE", pace)
    pace.write_text(json.dumps({"elevenlabs:v:m": [1.0184, 1.0748]}), encoding="utf-8")
    assert audio_engine.pace_ratio("elevenlabs:v:m") == 1.0748
    pace.write_text(json.dumps({"elevenlabs:v:m": [1.01, 1.02, 1.03, 1.04, 1.09]}), encoding="utf-8")
    assert audio_engine.pace_ratio("elevenlabs:v:m") == 1.03                      # median once five are known
    assert audio_engine.pace_ratio("unknown") == 1.0


def test_a_take_is_time_stretched_with_its_word_timings(tmp_path):
    # Production 2026-10-25: ElevenLabs read a take asked to be 5% faster 1% slower; a stretch is reliable.
    import subprocess
    from src.audio_engine import AudioEngine
    take = tmp_path / "voiceover.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=4",
                    "-b:a", "192k", str(take)], check=True)
    words = [{"word": "one", "start": 0.0, "dur": 1.0}, {"word": "two", "start": 2.0, "dur": 1.0}]
    out = AudioEngine.stretch_to_fit(take, words, 1.05)
    assert abs(AudioEngine.get_duration(take) - 4 / 1.05) < 0.15
    assert out[1]["start"] == round(2.0 / 1.05, 4) and out[1]["dur"] == round(1.0 / 1.05, 4)
    assert not (tmp_path / "voiceover.fit.mp3").exists()


def test_without_rubberband_a_take_is_never_stretched_at_lower_quality(tmp_path, monkeypatch):
    import pytest
    from src.audio_engine import AudioEngine
    take = tmp_path / "voiceover.mp3"
    take.write_bytes(b"x")
    monkeypatch.setattr(AudioEngine, "rubberband_available", staticmethod(lambda: False))
    with pytest.raises(RuntimeError, match="no rubberband filter"):
        AudioEngine.stretch_to_fit(take, [], 1.05)
    assert take.read_bytes() == b"x"                                             # untouched


def test_the_health_check_reports_rubberband_without_blocking(monkeypatch):
    from src.audio_engine import AudioEngine
    from src.ops import PreFlightHealthChecker as HealthChecker
    monkeypatch.setattr(AudioEngine, "rubberband_available", staticmethod(lambda: False))
    ok, msg = HealthChecker().check_rubberband()
    assert not ok and "no rubberband filter" in msg
