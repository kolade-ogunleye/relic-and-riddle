from src.audio_provenance import inspect_audio_rights, record_bed, record_voice


def test_voice_evidence_is_bound_to_audio(tmp_path):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"voice")
    record_voice(voice, "edge-tts", False, "script")
    assert not inspect_audio_rights(voice, "voice", "")[0]
    record_voice(voice, "azure", True, "script")
    assert inspect_audio_rights(voice, "voice", "")[0]
    assert not inspect_audio_rights(voice, "voice", "", script="changed script")[0]
    voice.write_bytes(b"replacement")
    assert not inspect_audio_rights(voice, "voice", "")[0]


def test_library_music_requires_attribution_and_intact_sources(tmp_path):
    source, bed = tmp_path / "track.wav", tmp_path / "bed.wav"
    source.write_bytes(b"track")
    bed.write_bytes(b"bed")
    music = {"source": "library", "credit": "Music: Artist (CC BY 4.0)", "tracks": [{
        "path": str(source), "license": "by", "license_version": "4.0", "reviewed": True,
        "source_url": "https://example.org/source", "license_url": "https://example.org/license"}]}
    record_bed(bed, music)
    assert not inspect_audio_rights(bed, "music", "No attribution")[0]
    assert inspect_audio_rights(bed, "music", music["credit"])[0]
    source.write_bytes(b"changed")
    assert not inspect_audio_rights(bed, "music", music["credit"])[0]


def test_provider_music_cannot_inherit_voice_license(tmp_path):
    bed = tmp_path / "bed.wav"
    bed.write_bytes(b"bed")
    record_bed(bed, {"source": "elevenlabs", "credit": "", "voice_plan": "paid"})
    assert not inspect_audio_rights(bed, "music", "")[0]
    record_bed(bed, {"source": "none"})
    assert inspect_audio_rights(bed, "music", "")[0]
