"""A finished dry run can be posted for real from Telegram, one video at a time, while the daily runs stay
in dry-run mode: asked to confirm, checked by the live release gate, then booked (or posted). Before, only
`main.py review publish` as the pipeline account could do it (2026-10-24)."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import main
from src import control
from src.control import make_command_handler


def _simulated(db, tmp_path, run_id="run_s"):
    topic = db.list_all_topics()[0]["id"]
    db.create_run(run_id, topic)
    (tmp_path / run_id).mkdir(exist_ok=True)
    db.update_run(run_id, "simulated", master_video_path=tmp_path / run_id / "m.mp4",
                  script_payload={"title": "T", "origin": "gemini-free"})
    return run_id


def _coordinator(db, schedule_posts=True):
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.schedule_posts, c.only_promoted = db, schedule_posts, True
    c.calls = []
    c.schedule_run = lambda run_id: c.calls.append(("schedule", run_id)) or {"status": "scheduled", "scheduled_for": "2026-10-24T23:00:00+00:00"}
    c.publish_run = lambda run_id, dry_run=False, previous_status="queued": c.calls.append(("publish", run_id, dry_run)) or {"status": "published"}
    return c


def test_post_for_real_asks_first_then_books_the_run(db, tmp_path, monkeypatch):
    run_id = _simulated(db, tmp_path)
    monkeypatch.setattr("src.release.require_release", lambda run, dry_run=False: {"status": "pass"})
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    c = _coordinator(db)
    seen = []
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"promote": lambda r: seen.append(r) or c.promote_run(r)})
    text, buttons = h("postreal", db.run_key(run_id))
    assert "Post this for real?" in text and buttons[0][0]["callback_data"] == f"promote:{db.run_key(run_id)}"
    reply = h("promote", db.run_key(run_id))
    assert seen == [run_id] and "booked for real posting" in reply and c.calls == [("schedule", run_id)]
    assert db.get_setting(main.PROMOTED_PREFIX + run_id)


def test_post_for_real_posts_now_without_slots_and_refuses_what_the_live_gate_blocks(db, tmp_path, monkeypatch):
    run_id = _simulated(db, tmp_path)
    monkeypatch.setattr("src.release.require_release", lambda run, dry_run=False: {"status": "pass"})
    c = _coordinator(db, schedule_posts=False)
    assert "is posted" in c.promote_run(run_id) and c.calls == [("publish", run_id, False)]

    def blocked(run, dry_run=False):
        raise ValueError("Release blocked: asset_rights: motion_01.mp4")
    monkeypatch.setattr("src.release.require_release", blocked)
    other = _simulated(db, tmp_path, "run_t")
    reply = c.promote_run(other)
    assert reply.startswith("🚀 Not posted: Release blocked") and not db.get_setting(main.PROMOTED_PREFIX + other)
    db.update_run(other, "producing")
    assert "only a finished dry run" in c.promote_run(other)


def test_a_dry_run_daemon_posts_only_promoted_runs(db, tmp_path):
    promoted, other = _simulated(db, tmp_path, "run_p"), _simulated(db, tmp_path, "run_o")
    due = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    for run_id in (promoted, other):
        db.add_scheduled_post(run_id, "reel", due, [])
    db.set_setting(main.PROMOTED_PREFIX + promoted, "2026-10-24T20:00:00+00:00")
    c = _coordinator(db)
    c._post_carousel_if_due = lambda now: None
    c._post_due()
    assert c.calls == [("publish", promoted, False)]
    assert {p["run_id"]: p["status"] for p in db.list_scheduled_posts(("pending", "published"))} == {other: "pending", promoted: "published"}
    c.only_promoted = False                                      # a live daemon posts every booked run
    c.calls.clear()
    c._post_due()
    assert c.calls == [("publish", other, False)]


def test_dashboard_and_reviews_offer_post_for_real_for_a_finished_dry_run(db, tmp_path):
    run_id = _simulated(db, tmp_path)
    _, buttons = control.dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    found = [b for row in buttons for b in row if b["callback_data"] == f"postreal:{db.run_key(run_id)}"]
    assert found and found[0]["text"].startswith("🚀 Post for real: ")
    h = make_command_handler(db, lambda: None, lambda: "")
    text, panel = h("creviews", "")
    assert "Post candidates" in text and any(b["callback_data"].startswith("postreal:") for row in panel for b in row)


def test_post_now_posts_the_reel_at_once_and_books_its_stories_and_carousel(db, tmp_path, monkeypatch):
    # Operator, 2026-10-24: posting on demand. The Reel goes out now; the Stories 45 minutes later and the
    # carousel in its next slot are booked locally for the daemon to post, exactly as a slot booking does.
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    monkeypatch.setattr(main, "require_release", lambda run, dry_run=False: None, raising=False)
    import src.release
    monkeypatch.setattr(src.release, "require_release", lambda run, dry_run=False: None)
    run_id = _simulated(db, tmp_path)
    (tmp_path / run_id / "stories").mkdir()
    (tmp_path / run_id / "carousel").mkdir()
    for i in (1, 2):
        (tmp_path / run_id / "stories" / f"story_{i}.jpg").write_bytes(b"x")
        (tmp_path / run_id / "carousel" / f"carousel_slide_{i}.jpg").write_bytes(b"x")
    c = _coordinator(db)
    text = c.post_now(run_id)
    assert c.calls == [("publish", run_id, False)] and "is posted" in text and "Story frames" in text
    posts = db.list_scheduled_posts(("pending", "waiting"), run_id=run_id)
    assert sorted((p["kind"], p["status"]) for p in posts) == [("carousel", "waiting"), ("story", "pending")]
    story = next(p for p in posts if p["kind"] == "story")
    minutes = (datetime.fromisoformat(story["due_at"]) - datetime.now(timezone.utc)).total_seconds() / 60
    assert 44 < minutes <= 45 and db.get_setting(main.PROMOTED_PREFIX + run_id)
    # the confirmation card offers both ways when slots are on
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"promote": c.promote_run, "postnow": c.post_now})
    _, buttons = h("post", db.run_key(run_id))
    assert [b["callback_data"].split(":")[0] for row in buttons for b in row][:2] == ["promote", "postnow"]
