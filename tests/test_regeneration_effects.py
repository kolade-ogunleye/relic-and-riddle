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


def test_each_revision_carries_every_earlier_rejection(db, tmp_path):
    # Production, 2026-10-23: "built before farming" came back reworded in each draft, since a revision
    # saw only the latest findings; the run failed closed after three drafts.
    seen = []
    findings = iter(["1 blocking claim(s): 'Hunter-gatherers built it long before farming.'",
                     "1 blocking claim(s): 'It was built before farming.'",
                     ""])

    class FakeEngine:
        progress = None

        def generate_script(self, topic, revision_notes="", previous=None):
            seen.append(revision_notes)
            return {"origin": "gemini-free", "title": "T"}

        def verify_claims(self, payload, topic=None, allow_unreviewed_curated=False):
            notes = next(findings)
            return (not notes), payload, notes or "PASSED"

        def _load_curated(self, topic_id):
            return None
    db.create_run("run_x", db.list_all_topics()[0]["id"])
    c = _coordinator(db)
    c.script_engine = FakeEngine()
    c._check_cancel = lambda run_id: None
    c.telegram_bot = SimpleNamespace(update_progress_card=lambda *a, **k: None)
    c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert "long before farming" in seen[2] and "It was built before farming" in seen[2]   # both, not only the last
    assert "Do not restate these claims" in seen[2]
    assert main.with_earlier_rejections("now", []) == "now"


def test_a_regeneration_for_pictures_keeps_the_verified_script_word_for_word(db, tmp_path, monkeypatch):
    # Production 2026-10-24: a "pictures" regeneration wrote a fresh script, so the approved words changed
    # and the voiceover made for them could not carry over.
    import json
    import main
    from types import SimpleNamespace
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    previous = tmp_path / "run_prev"
    previous.mkdir()
    script = {"narration_script": "The words you approved.", "guardrail_passed": True, "title": "T"}
    (previous / "verified_script.json").write_text(json.dumps(script), encoding="utf-8")
    coordinator = SimpleNamespace(_operator_feedback={"reason": "pictures", "from_run": "run_prev"}, db=db)
    coordinator._feedback = lambda: main.PipelineCoordinator._feedback(coordinator)
    coordinator._previous_run_dir = lambda: main.PipelineCoordinator._previous_run_dir(coordinator)
    assert main.PipelineCoordinator._script_from_previous(coordinator, coordinator._feedback()) == script
    for reason in ("facts", "hook", "pacing", "cover"):                     # the words themselves are at issue
        assert main.PipelineCoordinator._script_from_previous(coordinator, {"reason": reason, "from_run": "run_prev"}) is None
    (previous / "verified_script.json").write_text(json.dumps(dict(script, guardrail_passed=False)), encoding="utf-8")
    assert main.PipelineCoordinator._script_from_previous(coordinator, coordinator._feedback()) is None


def test_an_approved_regeneration_returns_its_topic_to_the_queue_not_to_rejected(db, monkeypatch):
    # Production 2026-10-24: after Reject, Make again and Approve (a dry run), the topic read "rejected".
    import main
    from types import SimpleNamespace
    topic = db.list_all_topics()[0]
    db.set_topic_status(topic["id"], "rejected") if hasattr(db, "set_topic_status") else None
    coordinator = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coordinator.db = db
    seen = {}
    coordinator._produce_and_publish = lambda t, run_id, run_dir, auto, dry, previous: seen.update(previous=previous) or {"status": "simulated", "run_id": run_id}
    coordinator.topic_engine = SimpleNamespace(get_topic_by_id=lambda t: dict(topic, status="rejected", times_published=0),
                                               record_status=lambda *a, **k: None, get_next_topic=lambda: None)
    coordinator.health_checker = SimpleNamespace(run_all=lambda require_live=False: {"all_passed": True, "details": {}})
    coordinator.lifecycle_manager = SimpleNamespace(purge_intermediate_assets=lambda: None)
    coordinator.telegram_bot = SimpleNamespace(is_configured=lambda: False, notify=lambda *a, **k: None)
    coordinator._finish_progress = lambda *a, **k: None
    coordinator._collect_findings = lambda *a, **k: None
    coordinator.progress_msg_id = None
    monkeypatch.setattr(main, "OUTPUT_DIR", db.db_path.parent)
    monkeypatch.setattr(main.config, "effective_format", lambda f: f)
    result = coordinator.run_cycle(topic["id"], dry_run=True)
    assert result["status"] == "simulated" and seen["previous"] == "queued"
