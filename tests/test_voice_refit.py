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
