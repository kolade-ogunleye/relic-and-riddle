import json

import numpy as np
import pytest
import soundfile as sf

from src import music
from src.audio_engine import AudioEngine
from src.control import make_command_handler


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path)
    monkeypatch.setattr(music, "LIBRARY", tmp_path / "library.json")
    tracks = []
    for i, (mood, reviewed, lic) in enumerate([("mysterious", True, "by"), ("epic", True, "cc0"),
                                               ("tense", False, "by"), ("mysterious", False, "cc0")]):
        name = f"{mood}/track_{i}_abcd{i:04d}.wav"
        (tmp_path / mood).mkdir(exist_ok=True)
        sf.write(str(tmp_path / name), np.zeros((48000 * 2, 2), np.float32), 48000)
        tracks.append({"file": name, "title": f"Track {i}", "creator": "Artist", "license": lic,
                       "license_version": "4.0", "source_url": f"https://x/{i}", "mood": mood,
                       "duration_ms": 120_000, "reviewed": reviewed, "uses": 0})
    music.save_library(tracks)
    return tmp_path


def test_only_approved_tracks_are_picked_in_mood_first(library):
    assert music.pick_library_track("mysterious", 45)["title"] == "Track 0"
    assert music.pick_library_track("tense", 45)["title"] in ("Track 0", "Track 1")  # tense track unreviewed
    assert music.pick_library_track("mysterious", 500) is None                     # too short


def test_rotation_and_credit(library):
    music.mark_used(music.pick_library_track("epic", 45)["file"])
    assert music.pick_library_track("melancholic", 45)["title"] == "Track 0"  # least used first
    assert music.credit_line({"license": "cc0"}) == ""
    assert "CC BY 4.0" in music.credit_line({"title": "T", "creator": "A", "license": "by", "license_version": "4.0"})


def test_paid_plan_gate(library, monkeypatch):
    monkeypatch.setattr(music, "ELEVENLABS_API_KEY", "key-1234567890")
    monkeypatch.setattr(music, "choose_mood", lambda p: "mysterious")
    called = []
    monkeypatch.setattr(music, "generate_elevenlabs", lambda *a: called.append(a) or a[2])
    monkeypatch.setattr(music, "MUSIC_PREFER", "elevenlabs")
    res = music.music_for({}, 45, library, {"tier": "free"})
    assert res["source"] == "library" and called == []          # free tier: no commercial licence
    res = music.music_for({}, 45, library, {"tier": "starter"})
    assert res["source"] == "elevenlabs" and called


def test_library_first_by_default_then_paid_score(library, monkeypatch):
    monkeypatch.setattr(music, "ELEVENLABS_API_KEY", "key-1234567890")
    monkeypatch.setattr(music, "choose_mood", lambda p: "mysterious")
    monkeypatch.setattr(music, "MUSIC_PREFER", "library")
    called = []
    monkeypatch.setattr(music, "generate_elevenlabs", lambda *a: called.append(a) or a[2])
    assert music.music_for({}, 45, library, {"tier": "starter"})["source"] == "library" and called == []
    # No approved track long enough: the paid score fills in.
    assert music.music_for({}, 500, library, {"tier": "starter"})["source"] == "elevenlabs"


def test_music_review_flow(library, db):
    sent = []
    h = make_command_handler(db, lambda: None, lambda: "", send_audio=lambda p, cap, b: sent.append((cap, b)) or True)
    assert h("music", "") == "🎵 Track sent above."
    key = sent[-1][1][0]["callback_data"].split(":")[1]
    assert "Kept" in h("mkeep", key)
    key2 = sent[-1][1][0]["callback_data"].split(":")[1]
    assert key2 != key and "Removed" in h("mdel", key2)
    assert "No tracks waiting" in h("music", "")
    lib = json.loads((library / "library.json").read_text(encoding="utf-8"))
    assert len(lib) == 3 and all(t["reviewed"] for t in lib)


def test_bed_uses_music_and_cues_sfx_on_words(tmp_path):
    track = tmp_path / "m.wav"
    sf.write(str(track), (np.random.default_rng(0).standard_normal((48000 * 3, 2)) * 0.2).astype(np.float32), 48000)
    out = AudioEngine().generate_ambient_bed(5.0, tmp_path / "bed.wav", words=[{"word": "quiet", "start": 1.0, "dur": 0.3}],
                                             music_path=track)
    data, sr = sf.read(str(out))
    assert sr == 48000 and data.shape == (240000, 2)
    rms = float(np.sqrt(np.mean(data[48000:-120000] ** 2)))
    assert 0.03 < rms < 0.08  # music normalised to the bed level, looped to length
