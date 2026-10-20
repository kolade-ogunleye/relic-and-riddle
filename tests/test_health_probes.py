"""
tests/test_health_probes.py
===========================
Live pre-flight: credentials must work, not merely be present.
"""

from types import SimpleNamespace

import pytest

from src import config
from src.ops import PreFlightHealthChecker


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "SCRIPT_ENGINE", "free")
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "AIza-free")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-paid")
    monkeypatch.setattr(config, "AZURE_SPEECH_KEY", "a" * 32)
    monkeypatch.setattr(config, "AZURE_SPEECH_REGION", "eastus")
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    calls = []

    def reply(status):
        return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: {})
    state = {"azure": 200, "gemini": 200}

    def post(url, headers=None, timeout=None, **kw):
        calls.append(url)
        return reply(state["azure"])

    def get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return reply(state["gemini"])
    monkeypatch.setattr("src.ops.requests.post", post)
    monkeypatch.setattr("src.ops.requests.get", get)
    return state, calls


def test_working_credentials_pass(configured):
    ok, msg = PreFlightHealthChecker().probe_live_services()
    assert ok, msg


def test_a_rejected_azure_key_fails_before_any_spend(configured):
    state, _ = configured
    state["azure"] = 401
    ok, msg = PreFlightHealthChecker().probe_live_services()
    assert not ok and "Azure Speech" in msg and "401" in msg


def test_elevenlabs_without_enough_characters_is_not_a_licensed_voice(configured, monkeypatch):
    from src.audio_engine import AudioEngine
    state, _ = configured
    state["azure"] = 401
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k" * 20)
    monkeypatch.setattr(AudioEngine, "voice_quota", lambda self: {"tier": "starter", "used": 29_763, "limit": 30_000})
    ok, msg = PreFlightHealthChecker().probe_live_services()
    assert not ok and "237 characters left" in msg


def test_a_shared_free_and_paid_gemini_key_is_flagged(configured, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-free")
    ok, msg = PreFlightHealthChecker().probe_live_services()
    assert not ok and "same key" in msg


def test_free_engine_does_not_require_an_anthropic_key(monkeypatch):
    monkeypatch.setattr(config, "SCRIPT_ENGINE", "free")
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "AIza-free")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "k"); monkeypatch.setattr(config, "UPLOAD_POST_USER", "u")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABCDEF"); monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "987654")
    monkeypatch.setattr(config, "AZURE_SPEECH_KEY", "a" * 20); monkeypatch.setattr(config, "AZURE_SPEECH_REGION", "eastus")
    ok, msg = PreFlightHealthChecker().check_live_credentials()
    assert ok, msg
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "")
    ok, msg = PreFlightHealthChecker().check_live_credentials()
    assert not ok and "GEMINI_FREE_API_KEY" in msg

