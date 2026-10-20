"""ElevenLabs is used the way the account and the tests need: the current narration model with only
the settings it accepts, never on dry runs (tests mustn't spend credits), and its real pace feeds
the script stage's length check."""
from types import SimpleNamespace

import pytest

from src import audio_engine
from src.audio_engine import AudioEngine


def test_v4_payload_sends_only_supported_settings():
    v4 = AudioEngine.eleven_payload("Hello.", "eleven_v4")
    assert v4["model_id"] == "eleven_v4" and "style" not in v4["voice_settings"]
    assert "use_speaker_boost" not in v4["voice_settings"] and "speed" in v4["voice_settings"]
    v2 = AudioEngine.eleven_payload("Hello.", "eleven_multilingual_v2")
    assert v2["voice_settings"]["style"] == 0.35 and v2["voice_settings"]["use_speaker_boost"] is True


def test_rejected_model_falls_back_without_failing(monkeypatch, tmp_path):
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json["model_id"])
        if json["model_id"] == "eleven_v4":
            return SimpleNamespace(status_code=422, raise_for_status=lambda: None)
        import base64
        data = {"audio_base64": base64.b64encode(b"mp3").decode(),
                "alignment": {"characters": list("Hi"), "character_start_times_seconds": [0.0, 0.1],
                              "character_end_times_seconds": [0.1, 0.2]}}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: data)
    monkeypatch.setattr(audio_engine.requests, "post", post)
    monkeypatch.setattr(audio_engine, "ELEVENLABS_MODEL", "eleven_v4")
    words = AudioEngine(eleven_key="k", eleven_voice="v")._generate_elevenlabs("Hi", tmp_path / "vo.mp3")
    assert sent == ["eleven_v4", "eleven_multilingual_v2"] and words[0]["word"] == "Hi"


def test_dry_runs_never_spend_elevenlabs_credits(monkeypatch, tmp_path):
    eng = AudioEngine(eleven_key="k", azure_key="a", azure_region="eastus")
    monkeypatch.setattr(audio_engine, "VOICE_PREFER", "elevenlabs")
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": "creator", "used": 0, "limit": 100000})
    monkeypatch.setattr(eng, "_generate_elevenlabs", lambda *a: pytest.fail("spent ElevenLabs credits on a test run"))
    monkeypatch.setattr(eng, "_generate_azure", lambda text, path: [{"word": "Test", "start": 0.0, "dur": 0.5}])
    eng.generate_narration("Test", tmp_path / "vo.wav", allow_paid=False)
    assert eng.last_provider == "azure" and eng.fallback_reason == ""  # by design, not a fallback alert


def test_measured_pace_of_the_real_voice_scales_the_length_check(monkeypatch, curated_payload):
    from src import script_engine
    from src.models import ScriptPackage
    key = audio_engine.voice_key("elevenlabs", "v", "eleven_v4")
    monkeypatch.setattr(audio_engine, "production_voice_key", lambda: key)
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text: 48.0)
    pkg = ScriptPackage.model_validate(curated_payload)
    assert script_engine.spoken_length_problems(pkg) == []  # 48 s, uncalibrated
    for actual in (52.8, 52.8, 53.0):  # this voice reads ~10% slower than the measurement
        audio_engine.record_pace(key, 48.0, actual)
    assert "cut about" in " ".join(script_engine.spoken_length_problems(pkg))


def test_calibration_records_real_voices_only(monkeypatch):
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text: 40.0)
    audio_engine.calibrate_pace("edge-tts", "x", "text", 44.0)
    assert not audio_engine.PACE_FILE.exists()
    audio_engine.calibrate_pace("azure", "en-US-BrianMultilingualNeural", "text", 44.0)
    assert audio_engine.pace_ratio(audio_engine.voice_key("azure", "en-US-BrianMultilingualNeural")) == pytest.approx(1.1)


def test_a_dry_run_can_opt_in_to_elevenlabs():
    import main
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.audio_engine = AudioEngine()
    coord.paid_voice_in_dry_run = False
    assert coord.voice_inputs("x", dry_run=True)["allow_paid"] is False
    coord.paid_voice_in_dry_run = True  # `run --dry-run --paid-voice` / `test --paid-voice`
    assert coord.voice_inputs("x", dry_run=True)["allow_paid"] is True
    assert main.PipelineCoordinator.voice_inputs(coord, "x", dry_run=False)["allow_paid"] is True
