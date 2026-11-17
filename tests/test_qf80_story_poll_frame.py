"""QF-80: the Story sequence is hook and reveal; the poll frame is no longer made, booked or posted.

The native poll sticker cannot be posted through the API, so the third frame went out as a question with nothing to
answer it. Ruling (decisions/QF-80-stories-threshold.md, option 1): cut the poll frame; frames 1-2 keep posting with
no audience gate, and each frame's reach stays recorded under its own number (audience <platform>_story<part>).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from PIL import Image

import main
from src import audience, release
from src.multi_format import MultiFormatGenerator
from src.publisher import MetaPublisher, UploadPostPublisher

FRAMES = ["story_01_hook.jpg", "story_02_reveal.jpg"]
POLL_FRAME = "story_03_question.jpg"

PAYLOAD = {
    "title": "The Eye of the Sahara", "narration_script": "Thirty miles across, and nobody built it.",
    "first_comment": "?", "hashtags": ["#x"], "caption": "c",
    "story_slides": [
        {"kicker": "VISIBLE FROM SPACE", "line1": "A BULLSEYE", "line2": "IN THE SAND", "body": "Thirty miles across."},
        {"kicker": "THE REVEAL", "line1": "NOT A CRATER", "line2": "A DOME", "body": "Erosion carved the rings."},
        {"kicker": "YOUR CALL", "line1": "WHAT DO", "line2": "YOU SEE?", "body": "Lost city or geology?"},
    ],
    "poll": {"question": "Lost city or geology?", "options": ["A lost city", "Geology"]},
}


def _legacy_stories(run_dir):
    """A run rendered before QF-80: three frames on disk, the third the poll frame."""
    stories = run_dir / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in FRAMES + [POLL_FRAME]:
        Image.new("RGB", (108, 192), (60, 60, 60)).save(stories / name)
        paths.append(str(stories / name))
    return paths


def test_the_sequence_is_hook_and_reveal_with_no_poll_room(tmp_path):
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1080, 1920), (50, 50, 50)).save(hero)
    (tmp_path / "stories").mkdir()
    Image.new("RGB", (108, 192)).save(tmp_path / "stories" / POLL_FRAME)  # an earlier render's poll frame
    slides = MultiFormatGenerator().generate_story_sequence(hero, PAYLOAD, tmp_path)
    assert [p.name for p in slides] == FRAMES
    assert sorted(p.name for p in (tmp_path / "stories").glob("story_*.jpg")) == FRAMES
    layout = json.loads((tmp_path / "stories" / "layout.json").read_text(encoding="utf-8"))
    assert [e["frame"] for e in layout] == FRAMES and not any(e.get("poll_sticker_room") for e in layout)
    companion = json.loads((tmp_path / "companion_posting.json").read_text(encoding="utf-8"))
    assert companion["story_frames"] == FRAMES and "story_poll_sticker" not in companion
    # the reveal stays the reveal: it is not turned into the poll's question frame
    record = json.loads((tmp_path / "stories" / "still_layout.json").read_text(encoding="utf-8"))
    assert not (record[FRAMES[1]]["recipe"].get("poll") or {})


def test_an_older_runs_poll_frame_is_not_booked_counted_or_credit_checked(db, tmp_path, monkeypatch):
    run_dir = tmp_path / "run_k"
    _legacy_stories(run_dir)
    assert [p.name for p in release.story_frames(run_dir)] == FRAMES
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    db.create_run("run_k", db.list_all_topics()[0]["id"])
    lines = c._book_followers("run_k", run_dir, datetime(2026, 11, 10, 17, 0, tzinfo=UTC))
    booked = [p for p in db.list_scheduled_posts() if p["kind"] == "story"]
    assert [[Path(p).name for p in b["files"]] for b in booked] == [FRAMES]
    assert any(line.startswith("📱 2 Story frames") for line in lines)
    seen = []
    from src import multi_format
    monkeypatch.setattr(multi_format, "story_credit_report", lambda frames: seen.append([f.name for f in frames])
                        or {"credited": [], "problems": []})
    assert release.story_credit_problems(run_dir) == [] and seen == [FRAMES]


def _upload_post(monkeypatch, calls, fail_at=None):
    def fake_post(url, headers, data, files, timeout):
        calls.append((headers["Idempotency-Key"], [f[1][0] for f in files]))
        if fail_at is not None and len(calls) == fail_at:
            return SimpleNamespace(status_code=400, json=lambda: {"success": False, "message": "bad"}, text="")
        return SimpleNamespace(status_code=200, text="",
                               json=lambda: {"success": True, "results": {"instagram": {"success": True, "url": "u"}}})
    monkeypatch.setattr(requests, "post", fake_post)
    return UploadPostPublisher(api_key="k" * 20, user="u", base_url="https://api.upload-post.com/api")


def test_a_booking_holding_the_poll_frame_posts_only_hook_and_reveal(db, tmp_path, monkeypatch):
    db.create_run("r1", "01_topic")
    files = _legacy_stories(tmp_path / "r1")
    calls = []
    up = _upload_post(monkeypatch, calls)
    res = MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "story", files)
    assert res["status"] == "published" and res["posted_parts"] == 2
    assert calls == [("r1-story-1", [FRAMES[0]]), ("r1-story-2", [FRAMES[1]])]
    plan = MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "story", files, dry_run=True)
    assert plan["planned_parts"] == 2
    only_poll = MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "story", files[2:])
    assert only_poll["status"] == "failed" and len(calls) == 2  # nothing sent


def test_a_retried_reveal_keeps_its_number_so_its_reach_is_recorded_as_the_reveals(db, tmp_path, monkeypatch):
    db.create_run("r1", "01_topic")
    files = _legacy_stories(tmp_path / "r1")
    calls = []
    up = _upload_post(monkeypatch, calls)
    res = MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "story", [files[1]],
                                                               platforms=["instagram"], retry_tag="retry1-instagram")
    assert res["status"] == "published" and calls == [("r1-story-2-retry1-instagram", [FRAMES[1]])]
    pub = next(p for p in db.list_publications("r1") if p["channel"] == "upload-post:story")
    receipt = json.loads(pub["response_payload_json"])
    assert receipt["part"] == 2
    targets = audience.targets({"channel": pub["channel"], "response": receipt, "run_id": "r1"}, {})
    assert [t[0] for t in targets] == ["instagram_story2"]


def test_a_failed_reveal_reports_its_frame_number(db, tmp_path, monkeypatch):
    db.create_run("r1", "01_topic")
    files = _legacy_stories(tmp_path / "r1")
    calls = []
    up = _upload_post(monkeypatch, calls, fail_at=2)
    res = MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "story", files)
    assert res["status"] == "unknown" and res["posted_parts"] == 1 and res["part"] == 2


@pytest.fixture
def legacy_booking(db, tmp_path):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_k", topic)
    db.update_run("run_k", "scheduled", master_video_path=tmp_path / "run_k" / "m.mp4", script_payload={"title": "K"})
    files = _legacy_stories(tmp_path / "run_k")
    slot = datetime(2026, 11, 10, 17, 0, tzinfo=UTC) + timedelta(minutes=45)
    return db.add_scheduled_post("run_k", "story", slot.isoformat(), files), files


def _coordinator(db, sent):
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c.publisher = SimpleNamespace(publish_photos=lambda rid, tid, kind, photos, caption="", platforms=None,
                                  dry_run=False, retry_tag="": sent.append((kind, photos, platforms, retry_tag))
                                  or {"status": "published", "post_urls": {}})
    return c


def test_the_poll_frame_of_an_older_booking_is_not_retried(db, legacy_booking):
    story, _ = legacy_booking
    db.update_scheduled_post(story, "unknown", detail={"status": "unknown", "part": 3, "posted_parts": 2,
                                                       "failed": {"instagram": "media error"}})
    sent = []
    assert "poll frame" in _coordinator(db, sent).retry_post(story) and sent == []


def test_a_reveal_retry_sends_the_reveal_even_when_the_hook_is_gone(db, legacy_booking):
    story, files = legacy_booking
    db.update_scheduled_post(story, "unknown", detail={"status": "unknown", "part": 2, "posted_parts": 1,
                                                       "failed": {"instagram": "media error"}})
    Path(files[0]).unlink()  # the hook's file no longer on disk: the reveal is still frame 2
    sent = []
    assert _coordinator(db, sent).retry_post(story).endswith("posted.")
    assert sent == [("story", [files[1]], ["instagram"], "retry1-instagram")]
