"""
tests/test_regeneration_effects.py
==================================
A Regenerate's reason must change what it says it changes (2026-10-23 audit: "Music / sound" and
details added after the run began did nothing, while the card promised "with your feedback").
"""

import json
from types import SimpleNamespace

import main
from src import improve


def _coordinator(db):
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    return c


def test_every_reason_says_what_its_regeneration_does():
    assert set(improve.REGENERATE_EFFECT) == set(improve.REASONS)
    assert "different music track" in improve.REGENERATE_EFFECT["sound"]
    assert "not changed automatically" in improve.REGENERATE_EFFECT["voice"]   # no false promise


def test_a_sound_regeneration_avoids_the_previous_tracks(db, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    (tmp_path / "run_old").mkdir()
    (tmp_path / "run_old" / "music_selection.json").write_text(json.dumps(
        {"file": "a.mp3", "tracks": [{"file": "a.mp3"}, {"file": "b.mp3"}]}), encoding="utf-8")
    c = _coordinator(db)
    assert c._previous_tracks({"reason": "sound", "from_run": "run_old"}) == ["a.mp3", "b.mp3"]
    assert c._previous_tracks({"reason": "facts", "from_run": "run_old"}) == []    # only for music


def test_the_picker_honours_the_exclusion(monkeypatch):
    from src import music
    library = [{"file": f, "mood": "mysterious", "reviewed": True, "duration_ms": 90000, "uses": uses, "title": f}
               for f, uses in (("a.mp3", 0), ("b.mp3", 5))]
    monkeypatch.setattr(music, "load_library", lambda: library)
    assert music.pick_library_track("mysterious", 30)["file"] == "a.mp3"
    assert music.pick_library_track("mysterious", 30, exclude=["a.mp3"])["file"] == "b.mp3"


def test_details_added_after_the_run_began_reach_its_later_steps(db):
    db.create_run("run_old", db.list_all_topics()[0]["id"])
    c = _coordinator(db)
    c._operator_feedback = {"reason": "sound", "text": "", "from_run": "run_old"}
    improve.record_reason(db, "run_old", "sound", "regenerate", "Too loud under the voice")
    assert c._feedback()["text"] == "Too loud under the voice"


def test_the_reason_card_states_the_effect(db):
    from src.control import make_command_handler
    db.create_run("run_x", db.list_all_topics()[0]["id"])
    db.record_approval("run_x", db.list_all_topics()[0]["id"], 300)
    db.update_approval_decision("run_x", "regenerate", decided_by="telegram:555")
    text = make_command_handler(db, lambda: None, lambda: "")("rsn", f"{db.run_key('run_x')}:sound")
    text = text[0] if isinstance(text, tuple) else text
    assert "a different music track is used" in text
