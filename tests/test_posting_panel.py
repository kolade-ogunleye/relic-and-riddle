"""🗓 Posting (operator, 2026-10-24): every booking with its slot and status, the links once posted, and
the actions on a booking - post it now, move it to the next slot, cancel it. Bookings are kept here, not
on the platforms; the daemon posts each when its slot comes."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import main
from src import control
from src.control import make_command_handler


def _booked(db, tmp_path, run_id="run_k"):
    topic = db.list_all_topics()[0]["id"]
    db.create_run(run_id, topic)
    (tmp_path / run_id).mkdir(exist_ok=True)
    db.update_run(run_id, "scheduled", master_video_path=tmp_path / run_id / "m.mp4",
                  script_payload={"title": "Kailasa", "origin": "gemini-free"})
    db.set_setting(main.PROMOTED_PREFIX + run_id, "2026-10-24T21:03:36")
    slot = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)
    reel = db.add_scheduled_post(run_id, "reel", slot.isoformat(), [])
    db.add_scheduled_post(run_id, "story", (slot + timedelta(minutes=45)).isoformat(), ["s1.jpg"])
    db.add_scheduled_post(run_id, "carousel", None, ["c1.jpg"], status="waiting")
    return run_id, reel, slot


def _coordinator(db):
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.schedule_posts, c.only_promoted = db, True, True
    c.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    c.published = []
    c.publish_run = lambda run_id, dry_run=False, previous_status="queued": c.published.append(run_id) or {"status": "published", "publish_result": {}}
    return c


def test_the_panel_lists_bookings_with_their_actions_and_the_dashboard_offers_it(db, tmp_path):
    run_id, reel, slot = _booked(db, tmp_path)
    text, buttons = control.posting_panel(db)
    assert "🗓 Reel ·" in text and "🗓 Stories ·" in text and "⏳ Carousel · next carousel slot" in text
    callbacks = [b["callback_data"] for row in buttons for b in row]
    assert callbacks == [f"postnow:{db.run_key(run_id)}", f"movepost:{reel}", f"cancelpost:{reel}"]
    _, dash = control.dashboard_text_and_buttons(db)
    assert any(b["callback_data"] == "cposts:" and b["text"] == "🗓 Posting (3)" for row in dash for b in row)
    h = make_command_handler(db, lambda: None, lambda: "")
    text, buttons = h("posts", "")
    assert text.startswith("🗓 <b>Posting</b>") and buttons[-2][0]["callback_data"] == "cstat:"


def test_a_published_booking_shows_its_links_from_the_receipts(db, tmp_path):
    run_id, reel, slot = _booked(db, tmp_path)
    db.update_scheduled_post(reel, "published", detail={"posted_at": slot.isoformat()})
    db.record_publication(db.get_run(run_id)["topic_id"], run_id, "upload-post:reel", "published",
                          {"post_urls": {"instagram": "https://instagram.com/p/1", "facebook": "https://facebook.com/2"}})
    text, _ = control.posting_panel(db)
    recent = text.split("<b>Recent</b>")[1]
    assert "✅ Reel" in recent and 'href="https://instagram.com/p/1">instagram</a>' in recent and "facebook</a>" in recent


def test_move_and_cancel_act_on_the_booking_and_its_followers(db, tmp_path):
    run_id, reel, slot = _booked(db, tmp_path)
    c = _coordinator(db)
    assert "moved to" in c.move_post(reel)
    posts = {p["kind"]: p for p in db.list_scheduled_posts(("pending",), run_id=run_id)}
    moved = datetime.fromisoformat(posts["reel"]["due_at"])
    assert moved > slot and datetime.fromisoformat(posts["story"]["due_at"]) == moved + timedelta(minutes=45)
    assert "post candidate again" in c.cancel_post(reel)
    assert db.list_scheduled_posts(("pending", "waiting"), run_id=run_id) == []
    assert db.get_run(run_id)["status"] == "simulated" and not db.get_setting(main.PROMOTED_PREFIX + run_id)
    assert c.cancel_post(reel) == "⏹ That booking is no longer pending."


def test_post_now_on_a_booking_posts_the_reel_and_retimes_its_stories(db, tmp_path, monkeypatch):
    import src.release
    monkeypatch.setattr(src.release, "require_release", lambda run, dry_run=False: None)
    run_id, reel, slot = _booked(db, tmp_path)
    c = _coordinator(db)
    text = c.post_now(run_id)
    assert c.published == [run_id] and "is posted" in text
    reel_row = next(p for p in db.list_scheduled_posts(("published",), run_id=run_id))
    assert reel_row["id"] == reel and json.loads(reel_row.get("detail_json") or "{}").get("posted_at") or reel_row.get("detail")
    story = next(p for p in db.list_scheduled_posts(("pending",), run_id=run_id) if p["kind"] == "story")
    minutes = (datetime.fromisoformat(story["due_at"]) - datetime.now(timezone.utc)).total_seconds() / 60
    assert 44 < minutes <= 45
    # the handler routes the booking actions to the coordinator
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"cancelpost": c.cancel_post, "movepost": c.move_post})
    text, _ = h("cancelpost", "999999")
    assert text.startswith("⏹ That booking is no longer pending.")
    assert h("movepost", "abc") == "That booking id is not valid."


def test_a_partial_story_publish_can_be_retried_on_the_failed_platform_only(db, tmp_path):
    # The first Stories (2026-10-24): frame 3 reached Facebook, not Instagram; the booking stayed "unknown"
    # with nothing to do. Retry re-sends that frame to Instagram alone, under a fresh idempotency key.
    run_id, reel, slot = _booked(db, tmp_path)
    frames = []
    for i in (1, 2, 3):
        f = tmp_path / run_id / f"story_{i}.jpg"
        f.write_bytes(b"x")
        frames.append(str(f))
    story = db.add_scheduled_post(run_id, "story", (slot + timedelta(minutes=45)).isoformat(), frames)
    db.update_scheduled_post(story, "unknown", detail={"status": "unknown", "part": 3, "posted_parts": 2,
                                                       "error": "Partial publish: ok=['facebook'] failed=['instagram'] (instagram: media error)",
                                                       "failed": {"instagram": "media error"}})
    text, buttons = control.posting_panel(db)
    assert any(b["callback_data"] == f"retrypost:{story}" for row in buttons for b in row)
    c = _coordinator(db)
    sent = []
    c.publisher = SimpleNamespace(publish_photos=lambda rid, tid, kind, photos, caption="", platforms=None, dry_run=False, retry_tag="":
                                  sent.append((kind, photos, platforms, retry_tag)) or {"status": "published", "post_urls": {"instagram": "https://instagram.com/stories/x"}})
    assert c.retry_post(story).endswith("posted.")
    assert sent == [("story", [frames[2]], ["instagram"], "retry1-instagram")]
    row = next(p for p in db.list_scheduled_posts(("published",), run_id=run_id) if p["id"] == story)
    assert row["detail"]["retries"] == 1 and row["detail"]["failed"] == {}
    # an older record names the platform only in its text
    older = db.add_scheduled_post(run_id, "story", (slot + timedelta(minutes=45)).isoformat(), frames)
    db.update_scheduled_post(older, "unknown", detail={"error": "Partial publish: ok=['facebook'] failed=['instagram']", "posted_parts": 2})
    c.retry_post(older)
    assert sent[-1] == ("story", [frames[2]], ["instagram"], "retry1-instagram")
    assert c.retry_post(999999) == "🔁 Nothing to retry for that booking."
