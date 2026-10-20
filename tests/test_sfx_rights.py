"""A sound effect file enters a mix only with rights evidence bound to its bytes; otherwise the
synthesized effect plays. Files used are listed in the bed's rights record."""
import json

import numpy as np
import soundfile as sf

from src import audio_engine, audio_provenance
from src.audio_engine import AudioEngine


def _wav(path, seconds=1.0, amp=0.5, sr=48000):
    t = np.arange(int(seconds * sr)) / sr
    sf.write(str(path), np.column_stack([amp * np.sin(2 * np.pi * 220 * t)] * 2), sr)
    return path


def test_unrecorded_file_is_never_mixed(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_engine, "SFX_DIR", tmp_path)
    _wav(tmp_path / "sfx_whoosh.wav")
    eng, synth = AudioEngine(), np.full((100, 2), 0.1, dtype=np.float32)
    assert eng._effect("sfx_whoosh.wav", synth, 48000) is synth and eng.last_sfx_sources == []


def test_licensed_file_is_used_at_the_synth_loudness(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_engine, "SFX_DIR", tmp_path)
    path = _wav(tmp_path / "sfx_whoosh.wav", amp=0.9)
    audio_provenance.record_sfx(path, "elevenlabs-sound-effects", "soft whoosh", "creator", True)
    eng, synth = AudioEngine(), np.full((48000, 2), 0.1, dtype=np.float32)
    out = eng._effect("sfx_whoosh.wav", synth, 48000)
    assert out is not synth and abs(np.sqrt(np.mean(out ** 2)) - 0.1) < 1e-3
    assert eng.last_sfx_sources[0]["name"] == "sfx_whoosh.wav"


def test_bed_record_covers_its_effects_and_catches_tampering(tmp_path):
    fx = _wav(tmp_path / "sfx_whoosh.wav")
    audio_provenance.record_sfx(fx, "elevenlabs-sound-effects", "whoosh", "creator", True)
    bed = _wav(tmp_path / "bed.wav")
    used = [{"name": "sfx_whoosh.wav", "path": str(fx), "sha256": audio_provenance.sha256_file(fx)}]
    record = audio_provenance.record_bed(bed, {"source": "none"}, sfx=used)
    assert record["rights_status"] == "verified" and record["sound_effects"][0]["rights_status"] == "verified"
    _wav(fx, amp=0.2)  # the effect file changes after its evidence was recorded
    assert audio_provenance.record_bed(bed, {"source": "none"}, sfx=used)["rights_status"] == "unverified"
    assert json.loads((tmp_path / "bed.wav.rights.json").read_text())["rights_status"] == "unverified"
