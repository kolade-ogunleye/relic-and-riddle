"""QF-103: /posts says how many Story frames a booking posts, and how many went out, retries included.

The count was cut from QF-80 (operator: "land it without the line") because it could not see a frame finished by a
retry in a record written before c497369c. A production episode (2026-10-24/25): frames 1 and 2 published, frame 3
reached one platform, and a retry finished it. That retry was recorded as part 1, and its booking holds no `part`.
The tests use the record shapes the code of each period wrote.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import requests
from PIL import Image

import main
from src.publisher import MetaPublisher, UploadPostPublisher

FRAMES = ["story_01_hook.jpg", "story_02_reveal.jpg"]
POLL_FRAME = "story_03_question.jpg"


def _stories(run_dir, names):
    stories = run_dir / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        Image.new("RGB", (108, 192), (60, 60, 60)).save(stories / name)
        paths.append(str(stories / name))
    return paths


@pytest.fixture
def booking(db, tmp_path):
    """A run's Story booking; `frames` picks the files it holds (three for a run rendered before QF-80)."""
    def make(names=tuple(FRAMES)):
        topic = db.list_all_topics()[0]["id"]
        db.create_run("run_k", topic)
        db.update_run("run_k", "scheduled", master_video_path=tmp_path / "run_k" / "m.mp4", script_payload={"title": "K"})
        files = _stories(tmp_path / "run_k", list(names))
        slot = datetime(2026, 11, 10, 17, 0, tzinfo=UTC) + timedelta(minutes=45)
        return db.add_scheduled_post("run_k", "story", slot.isoformat(), files), topic, files
    return make


def _block(db):
    from src import control
    text, _ = control.posting_episode(db, "run_k")
    return text.split("<b>Stories</b>")[1].split("\n\n")[0]


def test_a_pending_booking_says_the_frames_it_will_post(db, booking):
    booking()
    assert "  Frames: 2" in _block(db)


def test_a_booking_made_before_qf80_with_the_poll_frame_will_post_two(db, booking):
    booking(FRAMES + [POLL_FRAME])
    assert "  Frames: 2" in _block(db)   # the poll frame is never posted (QF-80)


def test_a_production_frame_finished_by_a_retry_before_c497369c_counts(db, booking):
    """The real shape (QF-80 review 5): 2a90c5d6's publish_photos returned {status unknown, error, posted_parts} and
    no part or failed; c497369c's retry_post kept the frame it sent local (part = posted_parts + 1) and stored
    dict(detail, retries, last_retry) with failed = {}; its receipt was numbered part 1."""
    story, topic, _ = booking(FRAMES + [POLL_FRAME])
    for status, part in (("published", 1), ("published", 2), ("unknown", 3), ("published", 1)):
        db.record_publication(topic, "run_k", "upload-post:story", status, {"part": part})
    first = {"status": "unknown", "error": "Upload-Post partial: failed=['instagram']", "posted_parts": 2}
    db.update_scheduled_post(story, "unknown", detail=first)
    assert "  Frames confirmed: 2" in _block(db)
    after = dict(first, retries=1, last_retry={"status": "published", "error": None, "post_urls": {}})
    after["failed"] = {}
    db.update_scheduled_post(story, "published", detail=after)
    block = _block(db)
    assert "  Frames posted: 3" in block and "  Frames: " not in block


def test_a_reveal_finished_by_todays_retry_counts_two(db, booking, monkeypatch):
    """A two-frame booking whose reveal reached one platform, finished by the current retry_post."""
    story, topic, _ = booking()
    db.record_publication(topic, "run_k", "upload-post:story", "published", {"part": 1})
    db.record_publication(topic, "run_k", "upload-post:story", "unknown", {"part": 2})
    db.update_scheduled_post(story, "unknown", detail={"status": "unknown", "posted_parts": 1, "part": 2,
                                                       "failed": {"instagram": "media error"}})
    assert "  Frames confirmed: 1" in _block(db)
    calls = []

    def fake_post(url, headers, data, files, timeout):
        calls.append(headers["Idempotency-Key"])
        return SimpleNamespace(status_code=200, text="",
                               json=lambda: {"success": True, "results": {"instagram": {"success": True, "url": "u"}}})
    monkeypatch.setattr(requests, "post", fake_post)
    up = UploadPostPublisher(api_key="k" * 20, user="u", base_url="https://api.upload-post.com/api")
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.publisher = db, MetaPublisher(db=db, publishers=[up])
    assert c.retry_post(story).endswith("posted.")
    assert calls == ["run_k-story-2-retry1-instagram"]
    assert "  Frames posted: 2" in _block(db)


def test_a_retry_without_posted_parts_counts_the_frame_retry_post_chose(db, booking):
    story, _, _ = booking()
    db.update_scheduled_post(story, "published", detail={"status": "unknown", "retries": 1,
                                                         "last_retry": {"status": "published"}})
    assert "  Frames posted: 1" in _block(db)


@pytest.mark.parametrize(("status", "detail", "line"), [
    ("published", {"status": "published", "posted_parts": 3}, "  Frames posted: 3"),   # posted before QF-80
    ("published", {"status": "published", "posted_parts": 2}, "  Frames posted: 2"),
    ("unknown", {"status": "unknown", "posted_parts": 1, "part": 2}, "  Frames confirmed: 1"),
    ("failed", {"status": "failed", "posted_parts": 1, "part": 2}, "  Frames confirmed: 1"),
    ("failed", {"error": "image files are missing", "posted_parts": 0}, "  Frames: 2"),   # nothing went out
    ("waiting", {}, "  Frames: 2"),
])
def test_each_state_reads_what_went_out_or_what_will(db, booking, status, detail, line):
    story, _, _ = booking()
    db.update_scheduled_post(story, status, detail=detail)
    block = _block(db)
    assert line in block
    assert block.count("Frames") == 1


@pytest.mark.parametrize(("status", "detail"), [
    ("unknown", {"status": "unknown", "posted_parts": 0, "part": 1}),   # nothing confirmed
    ("published", {"status": "published"}),                             # a receipt with no count
    ("cancelled", {"reason": "cancelled by the operator"}),
])
def test_no_count_when_nothing_is_known_or_it_never_posts(db, booking, status, detail):
    story, _, _ = booking()
    db.update_scheduled_post(story, status, detail=detail)
    assert "Frames" not in _block(db)


def test_a_retry_that_failed_adds_no_frame(db, booking):
    story, _, _ = booking()
    db.update_scheduled_post(story, "unknown", detail={"status": "unknown", "posted_parts": 1, "part": 2, "retries": 1,
                                                       "last_retry": {"status": "failed"}})
    assert "  Frames confirmed: 1" in _block(db)
