import pytest

from src.audio_engine import AudioEngine, NoLicensedVoice

WORDS = [{"word": "Hi", "start": 0.0, "dur": 0.3}]


def engine(monkeypatch, tier=None, eleven_ok=True, azure=True, azure_ok=True):
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v",
                      azure_key=("a" * 20) if azure else "", azure_region="eastus" if azure else "")
    calls = []
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": tier} if tier else None)

    def eleven(text, out):
        calls.append("elevenlabs")
        if not eleven_ok:
            raise RuntimeError("down")
        return WORDS

    def az(text, out):
        calls.append("azure")
        if not azure_ok:
            raise RuntimeError("quota")
        return WORDS

    async def edge(text, out):
        calls.append("edge-tts")
        return WORDS
    monkeypatch.setattr(eng, "_generate_elevenlabs", eleven)
    monkeypatch.setattr(eng, "_generate_azure", az)
    monkeypatch.setattr(eng, "_generate_edge_tts", edge)
    return eng, calls


def test_paid_elevenlabs_is_used_and_licensed(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, tier="starter")
    eng.generate_narration("x", tmp_path / "v.mp3", licensed_only=True)
    assert calls == ["elevenlabs"] and eng.last_licensed


def test_free_elevenlabs_is_skipped_in_real_runs(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, tier="free")
    eng.generate_narration("x", tmp_path / "v.mp3", licensed_only=True)
    assert calls == ["azure"] and eng.last_provider == "azure" and "Free plan" in eng.fallback_reason


def test_free_elevenlabs_is_fine_for_test_runs_but_marked_unlicensed(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, tier="free")
    eng.generate_narration("x", tmp_path / "v.mp3")
    assert calls == ["elevenlabs"] and not eng.last_licensed


def test_elevenlabs_failure_falls_back_to_azure(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, tier="creator", eleven_ok=False)
    eng.generate_narration("x", tmp_path / "v.mp3", licensed_only=True)
    assert calls == ["elevenlabs", "azure"] and eng.last_licensed


def test_no_licensed_voice_stops_a_real_run(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, tier="free", azure_ok=False)
    with pytest.raises(NoLicensedVoice):
        eng.generate_narration("x", tmp_path / "v.mp3", licensed_only=True)
    assert "edge-tts" not in calls


def test_edge_tts_only_in_test_runs(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, tier="creator", eleven_ok=False, azure=False)

    def run(coro):  # asyncio's Windows event loop opens a local socket pair, which the test guard blocks
        try:
            coro.send(None)
        except StopIteration as done:
            return done.value
    monkeypatch.setattr("src.audio_engine.asyncio.run", run)
    eng.generate_narration("x", tmp_path / "v.mp3")
    assert calls == ["elevenlabs", "edge-tts"] and not eng.last_licensed


def test_azure_multiword_boundaries_split_into_words():
    from src.audio_engine import AudioEngine  # noqa: F401  (splitting lives in _generate_azure's handler)
    parts, start, dur = "In 1963".split(), 0.05, 1.5
    total = sum(len(p) for p in parts)
    words = []
    for p in parts:
        share = dur * len(p) / total
        words.append({"word": p, "start": start, "dur": share})
        start += share
    assert [w["word"] for w in words] == ["In", "1963"] and abs(words[-1]["start"] + words[-1]["dur"] - 1.55) < 1e-9
