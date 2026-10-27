"""
tests/test_qa_graphics_2026_10_27.py
====================================
A development test was blocked because the video check called our own number card "an empty dark gradient with
no visual imagery", and its failure card said "Production failed" and not where the finding was (Odessa,
2026-10-27). The check is told which beats are our graphics; a dry run's failure is a test's; a finding says where.
"""

from pathlib import Path

import main
from src import release, video_qa


def test_the_video_check_is_told_which_beats_are_our_graphics(tmp_path, monkeypatch):
    sent = {}
    monkeypatch.setattr(video_qa.free_llm, "enabled", lambda: True)
    monkeypatch.setattr(video_qa.free_llm, "available", lambda: True)
    monkeypatch.setattr(video_qa, "extract_frames", lambda video, times, out: [tmp_path / f"f{i}.jpg" for i in range(len(times))])
    monkeypatch.setattr(video_qa, "image_block", lambda frame, size: {"type": "image", "path": str(frame)})
    monkeypatch.setattr(video_qa.free_llm, "call", lambda system, content, *a, **k: sent.update(system=system, content=content))
    beats = [{"text": "One.", "start": 0, "end": 2}, {"text": "Seventy-three days.", "start": 2, "end": 5}]
    video_qa.review_render(tmp_path / "m.mp4", beats, tmp_path / "c.jpg", tmp_path,
                           [tmp_path / "parallax" / "scene_01.mp4", tmp_path / "graphics" / "graphic_numbers.mp4"])
    texts = [c["text"] for c in sent["content"] if c["type"] == "text"]
    assert texts[0] == 'Beat 1 narration: "One."'
    assert texts[1] == 'Beat 2 [our own graphic: numbers card] narration: "Seventy-three days."'
    assert "not a missing picture" in sent["system"]


def test_a_dry_runs_failure_is_a_test_runs(db):
    from src.control import failed_heading
    db.set_setting("run_mode:run_a", "dry_run")
    db.set_setting("run_mode:run_b", "live")
    assert failed_heading(db, "run_a") == "Test run failed"
    assert failed_heading(db, "run_b") == failed_heading(db, "run_unknown") == "Production failed"


def test_a_video_check_finding_says_where_it_is_and_how_serious():
    beats = [{"start": 0, "end": 4}] * 5 + [{"start": 21.2, "end": 26.0}]
    assert main.beat_where(6, beats) == "beat 6, 0:21-0:26"
    assert main.beat_where(0, beats) == "cover" and main.beat_where(9, beats) == "beat 9"
    check = {"name": "visual_checks", "detail": [{"type": "video_check", "beat": 6, "severity": "minor",
                                                  "issue": "Dark backdrop.", "where": "beat 6, 0:21-0:26"},
                                                 {"issue": "An audit finding."}]}
    assert release.check_problems(check) == ["beat 6, 0:21-0:26 (minor): Dark backdrop.", "An audit finding."]


def test_a_booking_cancelled_and_booked_again_shows_once(db):
    """Göbekli Tepe's Posting list read "Stories cancelled" above "Stories posted" after its retry (2026-10-27)."""
    from src.control import _episode_bookings
    db.create_run("run_g", db.list_all_topics()[0]["id"])
    old_story = db.add_scheduled_post("run_g", "story", "2026-10-26T23:45:00+00:00", ["s.jpg"])
    old_carousel = db.add_scheduled_post("run_g", "carousel", None, ["c.jpg"], status="waiting")
    db.update_scheduled_post(old_story, "cancelled")
    db.update_scheduled_post(old_carousel, "cancelled")
    db.add_scheduled_post("run_g", "story", "2026-10-27T05:00:00+00:00", ["s.jpg"])
    gone = db.add_scheduled_post("run_g", "carousel", "2026-10-29T23:00:00+00:00", ["c.jpg"])
    posts = dict(_episode_bookings(db))["run_g"]
    assert [(p["kind"], p["status"]) for p in posts] == [("story", "pending"), ("carousel", "pending")]
    db.update_scheduled_post(gone, "cancelled")   # a cancellation with no new booking still shows
    assert [(p["kind"], p["status"]) for p in dict(_episode_bookings(db))["run_g"]] == [("story", "pending"), ("carousel", "cancelled")]


def test_a_cancelled_booking_says_why_and_what_can_be_done(db):
    """A bare "cancelled" gave the operator no idea what had happened (Göbekli Tepe, 2026-10-27)."""
    from src.posting_view import posting_episode, posting_panel
    db.create_run("run_c", db.list_all_topics()[0]["id"])
    story = db.add_scheduled_post("run_c", "story", "2026-10-26T23:45:00+00:00", ["s.jpg"])
    db.update_scheduled_post(story, "cancelled", detail={"reason": "the Reel did not post"})
    text, _ = posting_panel(db)
    assert "Stories ⏹ cancelled: its Reel&#x27;s slot failed, so it was not posted" in text
    assert "book it again" not in text
    db.update_run("run_c", "published")
    text, _ = posting_episode(db, "run_c")
    assert "its Reel has posted since: tap 📱 Book Stories and carousel again below" in text
    db.update_scheduled_post(story, "cancelled", detail={"reason": "cancelled by the operator"})
    assert "cancelled: you cancelled it" in posting_panel(db)[0]
