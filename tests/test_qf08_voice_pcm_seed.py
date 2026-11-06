"""QF-08: lossless voice where supported, bandwidth-preserving fallbacks, seeded requests.

Azure is asked for Riff48Khz16BitMonoPcm and ElevenLabs for output_format=pcm_48000 (ELEVENLABS_OUTPUT_FORMAT) with a
fixed seed and stability 0.65; the voiceover is written as a WAV file (voiceover.wav), its rights record is keyed on
the WAV's bytes, and a punctuation-only timed word ("...", a pacing cue) never reaches a caption burst or cue.
Every provider is mocked: no live call."""
import base64
import hashlib
import json
import re
import subprocess
import sys
import types
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from src import audio_engine, config
from src.audio_engine import AudioEngine

RATE = 48000


def _pcm(seconds: float = 0.6, rate: int = RATE) -> bytes:
    """A 220 Hz tone as raw 16-bit little-endian mono PCM, as ElevenLabs' pcm_48000 returns it."""
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2").tobytes()


def _format_audio(pcm, rate, fmt):
    if fmt.startswith("pcm_"):
        return pcm
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
         "-f", "mp3", "-b:a", fmt.split("_")[2] + "k", "pipe:1"],
        input=pcm, capture_output=True, check=True).stdout


def _assert_high_band(path):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)
    power = np.abs(np.fft.rfft(samples * np.hanning(len(samples)))) ** 2
    freq = np.fft.rfftfreq(len(samples), 1 / rate)
    assert power[(freq >= 13000) & (freq <= 15000)].sum() / power.sum() > 0.1


def _riff(pcm: bytes, path) -> bytes:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm)
    return path.read_bytes()


def _alignment(text: str) -> dict:
    chars = list(text)
    starts = [round(i * 0.04, 3) for i in range(len(chars))]
    return {"characters": chars, "character_start_times_seconds": starts,
            "character_end_times_seconds": [s + 0.04 for s in starts]}


def _is_wav(path, rate: int = RATE) -> bool:
    with wave.open(str(path), "rb") as w:
        return w.getframerate() == rate and w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getnframes() > 0


def _eleven(monkeypatch, sent: list, text_said: str):
    pcm = _pcm()

    def post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "json": json})
        match = re.search(r"output_format=([a-z0-9_]+)", url)
        fmt = match.group(1) if match else "mp3_44100_128"
        rate = int(fmt.split("_")[1])
        audio = _format_audio(_pcm(rate=rate), rate, fmt)
        data = {"audio_base64": base64.b64encode(audio).decode(), "alignment": _alignment(text_said)}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: data)
    monkeypatch.setattr(audio_engine.requests, "post", post)
    return pcm


def test_elevenlabs_is_asked_for_pcm_48000_with_seed_and_stability_and_written_as_wav(monkeypatch, tmp_path):
    sent: list = []
    pcm = _eleven(monkeypatch, sent, "Deep under the plain.")
    vo = audio_engine.voiceover_file(tmp_path) if hasattr(audio_engine, "voiceover_file") else tmp_path / "voiceover.mp3"
    AudioEngine(eleven_key="k" * 20, eleven_voice="v")._generate_elevenlabs("Deep under the plain.", vo)
    request = sent[0]
    assert re.search(r"[?&]output_format=pcm_48000(&|$)", request["url"])
    assert request["json"]["voice_settings"]["stability"] == 0.65
    assert request["json"]["seed"] == getattr(config, "ELEVENLABS_SEED", None) is not None
    assert vo.name == "voiceover.wav" and _is_wav(vo)
    with wave.open(str(vo), "rb") as w:
        assert w.readframes(w.getnframes()) == pcm          # the PCM as it came: no lossy step, no resample


def test_the_fallback_format_is_a_setting_and_still_written_as_wav(monkeypatch, tmp_path):
    sent: list = []
    _eleven(monkeypatch, sent, "Hi")
    monkeypatch.setattr(audio_engine, "ELEVENLABS_OUTPUT_FORMAT", "pcm_24000", raising=False)
    vo = tmp_path / "voiceover.wav"
    AudioEngine(eleven_key="k" * 20, eleven_voice="v")._generate_elevenlabs("Hi", vo)
    assert "output_format=pcm_24000" in sent[0]["url"] and _is_wav(vo, rate=24000)


def _rejecting(monkeypatch, sent: list, rejected: dict, text_said: str = "Hi"):
    """ElevenLabs mocked: a format in `rejected` gets that HTTP status (how a plan without the format answers);
    any other format returns valid bytes in that format. The signal includes 14 kHz
    so a bandwidth-losing fallback cannot pass just by writing a WAV header."""
    def post(url, headers=None, json=None, timeout=None):
        fmt = re.search(r"output_format=([a-z0-9_]+)", url).group(1)
        sent.append((fmt, json["model_id"]))
        if fmt in rejected:
            def fail(status=rejected[fmt]):
                raise audio_engine.requests.HTTPError(f"{status}", response=SimpleNamespace(status_code=status))
            return SimpleNamespace(status_code=rejected[fmt], raise_for_status=fail,
                                   json=lambda: {"detail": {"status": "output_format_not_allowed"}})
        rate = int(fmt.split("_")[1])
        t = np.arange(int(rate * 0.3)) / rate
        pcm = ((np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 14000 * t)) * 4000).astype("<i2").tobytes()
        data = {"audio_base64": base64.b64encode(_format_audio(pcm, rate, fmt)).decode(),
                "alignment": _alignment(text_said)}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: data)
    monkeypatch.setattr(audio_engine.requests, "post", post)
    monkeypatch.setattr(audio_engine, "ELEVENLABS_MODEL", "eleven_v4")


def test_a_plan_without_48k_pcm_is_asked_for_the_plan_safe_format_straight_away(monkeypatch, tmp_path):
    """Production's Creator plan: ElevenLabs gates 44.1 kHz PCM (and so 48 kHz) to Pro and above."""
    sent: list = []
    _rejecting(monkeypatch, sent, {"pcm_48000": 403, "pcm_44100": 403})
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v", azure_key="a" * 20, azure_region="eastus")
    monkeypatch.setattr(audio_engine, "VOICE_PREFER", "elevenlabs")
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": "creator", "used": 0, "limit": 100000})
    monkeypatch.setattr(eng, "_generate_azure", lambda *a: pytest.fail("the paid voice was lost to Azure"))
    vo = tmp_path / "voiceover.wav"
    words = eng.generate_narration("Hi", vo, licensed_only=True)
    assert sent == [("mp3_44100_192", "eleven_v4")] and config.ELEVENLABS_FALLBACK_FORMAT == "mp3_44100_192"
    assert eng.last_provider == "elevenlabs" and eng.last_output_format == "mp3_44100_192" and words
    assert _is_wav(vo, rate=44100) and eng.fallback_reason == ""
    _assert_high_band(vo)


@pytest.mark.parametrize("status", [403, 400, 422])
def test_a_rejected_format_is_retried_once_with_the_plan_safe_format(monkeypatch, tmp_path, status):
    """An unknown plan (quota unreadable) is asked for pcm_48000; a rejection of the format, whatever its status,
    is retried with the fallback format, and the take stays on ElevenLabs instead of moving to Azure."""
    sent: list = []
    _rejecting(monkeypatch, sent, {"pcm_48000": status})
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v", azure_key="a" * 20, azure_region="eastus")
    monkeypatch.setattr(audio_engine, "VOICE_PREFER", "elevenlabs")
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": None, "used": 0, "limit": 100000})
    monkeypatch.setattr(eng, "_generate_azure", lambda *a: pytest.fail("the paid voice was lost to Azure"))
    vo = tmp_path / "voiceover.wav"
    words = eng.generate_narration("Hi", vo)
    tried = [("pcm_48000", "eleven_v4")] + ([("pcm_48000", "eleven_multilingual_v2")] if status != 403 else [])
    assert sent == tried + [("mp3_44100_192", "eleven_v4")]
    assert eng.last_provider == "elevenlabs" and eng.last_output_format == "mp3_44100_192" and words
    assert _is_wav(vo, rate=44100)
    _assert_high_band(vo)


def test_a_pro_plan_keeps_pcm_48000_and_the_fallback_format_is_a_setting(monkeypatch, tmp_path):
    sent: list = []
    _rejecting(monkeypatch, sent, {})
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v")
    eng.eleven_tier = "pro"
    eng._generate_elevenlabs("Hi", tmp_path / "a.wav")
    assert sent == [("pcm_48000", "eleven_v4")] and _is_wav(tmp_path / "a.wav", rate=48000)
    monkeypatch.setattr(audio_engine, "ELEVENLABS_FALLBACK_FORMAT", "mp3_44100_192")
    eng.eleven_tier = "creator"
    assert eng.eleven_formats() == ["mp3_44100_192"]
    eng.eleven_tier = None
    assert eng.eleven_formats() == ["pcm_48000", "mp3_44100_192", "mp3_44100_128"]


@pytest.mark.parametrize("tier", ["free", "starter", None])
def test_basic_plans_keep_their_bandwidth_and_provider(monkeypatch, tmp_path, tier):
    sent = []
    _rejecting(monkeypatch, sent, {"pcm_48000": 403, "mp3_44100_192": 403})
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v")
    eng.eleven_tier = tier
    vo = tmp_path / "voiceover.wav"
    eng._generate_elevenlabs("Hi", vo)
    expected = ["mp3_44100_128"] if tier else ["pcm_48000", "mp3_44100_192", "mp3_44100_128"]
    assert [fmt for fmt, _ in sent] == expected
    assert _is_wav(vo, rate=44100)
    _assert_high_band(vo)


def test_azure_is_asked_for_riff_48k_pcm_and_written_as_wav(monkeypatch, tmp_path):
    requested = []
    riff = _riff(_pcm(), tmp_path / "riff.wav")

    class Signal:
        def __init__(self):
            self.handlers = []

        def connect(self, fn):
            self.handlers.append(fn)

    class Synth:
        def __init__(self, speech_config=None, audio_config=None):
            self.synthesis_word_boundary = Signal()

        def speak_ssml_async(self, ssml):
            for i, word in enumerate(("Deep", "under")):
                for fn in self.synthesis_word_boundary.handlers:
                    fn(SimpleNamespace(boundary_type="word", text=word, audio_offset=i * 3_000_000,
                                       duration=SimpleNamespace(total_seconds=lambda: 0.3)))
            return SimpleNamespace(get=lambda: SimpleNamespace(reason="done", audio_data=riff))

    class Config:
        def __init__(self, subscription=None, region=None):
            pass

        def set_speech_synthesis_output_format(self, fmt):
            requested.append(fmt)

        def set_property(self, *a):
            pass

    sdk = types.ModuleType("azure.cognitiveservices.speech")
    sdk.SpeechConfig, sdk.SpeechSynthesizer = Config, Synth
    sdk.SpeechSynthesisOutputFormat = SimpleNamespace(Riff48Khz16BitMonoPcm="Riff48Khz16BitMonoPcm",
                                                      Audio48Khz192KBitRateMonoMp3="Audio48Khz192KBitRateMonoMp3")
    sdk.PropertyId = SimpleNamespace(SpeechServiceResponse_RequestWordBoundary="wb")
    sdk.SpeechSynthesisBoundaryType = SimpleNamespace(Word="word")
    sdk.ResultReason = SimpleNamespace(SynthesizingAudioCompleted="done")
    for name in ("azure", "azure.cognitiveservices"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "azure.cognitiveservices.speech", sdk)
    monkeypatch.setattr(sys.modules["azure.cognitiveservices"], "speech", sdk, raising=False)
    vo = tmp_path / "voiceover.wav"
    words = AudioEngine(azure_key="a" * 20, azure_region="eastus")._generate_azure("Deep under", vo)
    assert requested == ["Riff48Khz16BitMonoPcm"]
    assert [w["word"] for w in words] == ["Deep", "under"] and vo.read_bytes() == riff and _is_wav(vo)


def test_the_rights_record_is_keyed_on_the_wav_bytes(monkeypatch, tmp_path):
    from src.audio_provenance import inspect_audio_rights, record_voice
    _eleven(monkeypatch, [], "Hi")
    vo = audio_engine.voiceover_file(tmp_path) if hasattr(audio_engine, "voiceover_file") else tmp_path / "voiceover.mp3"
    AudioEngine(eleven_key="k" * 20, eleven_voice="v")._generate_elevenlabs("Hi", vo)
    record = record_voice(vo, "elevenlabs", True, "Hi")
    wav_hash = hashlib.sha256(vo.read_bytes()).hexdigest()
    saved = json.loads((tmp_path / "voiceover.wav.rights.json").read_text(encoding="utf-8"))
    assert vo.read_bytes()[:4] == b"RIFF" and record["sha256"] == saved["sha256"] == wav_hash
    assert inspect_audio_rights(vo, "voice", "", script="Hi")[0]


def test_a_take_made_before_qf08_is_still_found_and_a_new_one_is_the_wav(tmp_path):
    assert audio_engine.voiceover_file(tmp_path).name == "voiceover.wav"
    (tmp_path / "voiceover.mp3").write_bytes(b"a paid take from before")
    assert audio_engine.voiceover_file(tmp_path).name == "voiceover.mp3"       # never thrown away or re-bought
    (tmp_path / "voiceover.wav").write_bytes(b"RIFF")
    assert audio_engine.voiceover_file(tmp_path).name == "voiceover.wav"


def test_a_punctuation_only_timed_word_makes_no_caption_burst_or_cue(monkeypatch, tmp_path):
    from src.video_composer import caption_phrases, partition_sidecar_cues
    script = "No one knows why. The city was hidden under the plain for centuries."
    said = audio_engine.VoiceProsody.apply_prosody_elevenlabs(audio_engine.normalize_speech_text(script))
    assert "..." in said.split()                     # the pacing cue ElevenLabs is sent: a timed word of its own
    _eleven(monkeypatch, [], said)
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v")
    monkeypatch.setattr(audio_engine, "VOICE_PREFER", "elevenlabs")
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": "creator", "used": 0, "limit": 100000})
    words = eng.generate_narration(script, tmp_path / "voiceover.wav", licensed_only=True)
    assert eng.last_provider == "elevenlabs" and words
    assert all(re.search(r"\w", w["word"]) for w in words), [w["word"] for w in words]
    for burst in caption_phrases(words) + caption_phrases(words, widescreen=True):
        assert all(re.search(r"\w", w["word"]) for w in burst)
    for _start, _end, text in partition_sidecar_cues(words):
        assert all(re.search(r"\w", token) for token in text.split())
    assert " ".join(w["word"] for w in words) == script


def test_edge_tts_dry_run_take_is_decoded_once_to_wav(monkeypatch, tmp_path):
    import subprocess
    mp3 = tmp_path / "stream.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1",
                    "-ar", "24000", "-ac", "1", "-b:a", "48k", str(mp3)], check=True)

    class Communicate:
        def __init__(self, *a, **k):
            pass

        async def stream(self):
            yield {"type": "WordBoundary", "text": "Hi", "offset": 0, "duration": 3_000_000}
            yield {"type": "audio", "data": mp3.read_bytes()}
    monkeypatch.setattr(audio_engine.edge_tts, "Communicate", Communicate)
    vo = tmp_path / "voiceover.wav"
    take = AudioEngine()._generate_edge_tts("Hi", vo)
    with pytest.raises(StopIteration) as done:   # the mocked stream never waits: run without an event loop (no socket)
        take.send(None)
    words = done.value.value
    assert words[0]["word"] == "Hi" and _is_wav(vo, rate=24000)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stream.mp3", "voiceover.wav"]   # no stream left behind


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
