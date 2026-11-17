"""QF-69: an Undo (or Posting's cancel) of a Reel that has begun to post is refused, on every path.

The V2 card's own Undo is refused by db.undo_review_decision (UI-02). The clean chat's "Undo approval" on a booked
Approve, and Posting's cancel, go through PipelineCoordinator.cancel_post: it read the open bookings and then wrote
"cancelled" over them, unguarded, so a cancel tapped while the Reel was being uploaded called it cancelled and "a
post candidate again", and a posting pass that had read the booking before the cancel posted it anyway. The cancel
is now one transaction with the check that nothing of the run has begun to post, and the upload's claim checks the
booking in its own transaction, so the database serializes them. Round 2: every other write of the poster to a
booking is a compare-and-set on the status it read (a missed-slot reschedule wrote "pending" over a cancelled Reel and
brought it back to be posted), and a cancel during the checks before the claim (the upload lock held, nothing posted
yet) is no longer refused.
"""

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_ui01e_review_panel import (  # noqa: F401 - clean_chat: autouse there
    a_review,
    answers,
    clean_chat,
    deliver,
    items,
    make_bot,
    tap,
)

import main
from src.control import make_command_handler


def _booked(db, tmp_path, run_id="qf69", status="scheduled"):
    topic = db.list_all_topics()[0]["id"]
    db.create_run(run_id, topic)
    (tmp_path / run_id).mkdir(exist_ok=True)
    db.update_run(run_id, status, master_video_path=tmp_path / run_id / "m.mp4",
                  script_payload={"title": "Kailasa", "origin": "gemini-free"})
    slot = datetime.now(UTC) + timedelta(hours=3)
    reel = db.add_scheduled_post(run_id, "reel", slot.isoformat(), [])
    story = db.add_scheduled_post(run_id, "story", (slot + timedelta(minutes=45)).isoformat(), ["s1.jpg"])
    return run_id, reel, story


def _coordinator(db):
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.schedule_posts, c.only_promoted = db, True, False
    c.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    return c


def _status(db, post_id):
    with db._conn() as conn:
        return conn.execute("SELECT status FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone()["status"]


@pytest.mark.parametrize("posting", ["publishing", "published", "publish_unknown"])
def test_cancelling_a_reel_that_began_to_post_is_refused_and_changes_nothing(db, tmp_path, posting):
    run_id, reel, story = _booked(db, tmp_path)
    db.update_run(run_id, posting)                 # the upload has begun (or ended) since the booking was read
    reply = _coordinator(db).cancel_post(reel)
    assert reply.startswith("⏹ Not cancelled")
    assert _status(db, reel) == "pending" and _status(db, story) == "pending"
    assert db.get_run(run_id)["status"] == posting


def test_a_recorded_live_publication_refuses_the_cancel(db, tmp_path):
    run_id, reel, _ = _booked(db, tmp_path)
    db.record_publication(db.get_run(run_id)["topic_id"], run_id, "upload-post:reel", "unknown", {})
    assert _coordinator(db).cancel_post(reel).startswith("⏹ Not cancelled")
    assert _status(db, reel) == "pending" and db.get_run(run_id)["status"] == "scheduled"


def test_a_cancel_before_posting_cancels_the_reel_and_its_followers(db, tmp_path):
    run_id, reel, story = _booked(db, tmp_path)
    reply = _coordinator(db).cancel_post(reel)
    assert reply.startswith("⏹ Booking cancelled")
    assert _status(db, reel) == "cancelled" and _status(db, story) == "cancelled"
    assert db.get_run(run_id)["status"] == "simulated"


def test_a_booking_cancelled_after_the_pass_read_it_is_never_posted(db, tmp_path, monkeypatch):
    """The posting pass read the booking, the operator's cancel landed, then the pass uploaded: the run was a
    'simulated' post candidate by then, which claim_publication accepts, so the cancelled Reel was posted."""
    run_id, reel, _ = _booked(db, tmp_path)
    c = _coordinator(db)
    uploaded = []
    monkeypatch.setattr(c, "_publish_run_locked", lambda rid, *a, **k: uploaded.append(rid) or {"status": "published"},
                        raising=False)
    monkeypatch.setattr(c, "_may_post", lambda rid: True, raising=False)
    post = next(p for p in db.list_scheduled_posts(("pending",), run_id=run_id) if p["kind"] == "reel")
    assert c.cancel_post(reel).startswith("⏹ Booking cancelled")
    post["due_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError):
        c._post_one_due(post, datetime.now(UTC))
    assert uploaded == [] and _status(db, reel) == "cancelled"


def test_post_now_of_a_booking_cancelled_meanwhile_uploads_nothing(db, tmp_path, monkeypatch):
    run_id, _, _ = _booked(db, tmp_path)
    c = _coordinator(db)
    uploaded = []
    monkeypatch.setattr(c, "_require_release", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(c, "_publish_run_locked", lambda rid, *a, **k: uploaded.append(rid) or {"status": "published"},
                        raising=False)
    real = db.list_scheduled_posts

    def cancelled_between(statuses=("pending", "waiting"), run_id=None):   # the cancel lands after Post now read it
        rows = real(statuses, run_id=run_id)
        if statuses == ("pending", "blocked") and rows:
            assert c.cancel_post(rows[0]["id"]).startswith("⏹ Booking cancelled")
        return rows
    monkeypatch.setattr(db, "list_scheduled_posts", cancelled_between)
    reply = c.post_now(run_id)
    assert uploaded == [] and "Not posted" in reply


def test_the_review_panels_undo_of_a_booked_approve_being_posted_keeps_the_booking_and_its_item(db, tmp_path,
                                                                                                monkeypatch):
    from src import telegram_bot
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "qf69p")
    mid = deliver(bot, db, root, payload, "qf69p")
    tap(bot, f"rva:{db.run_key('qf69p')}", mid)
    reel = db.add_scheduled_post("qf69p", "reel", (datetime.now(UTC) + timedelta(hours=3)).isoformat(), [])
    db.update_run("qf69p", "publishing")          # Post now is uploading it
    monkeypatch.setattr(telegram_bot, "undo_minutes", lambda: 0.0)   # past UI-02's window: the booking's cancel
    c = _coordinator(db)
    handler = make_command_handler(db, lambda: None, lambda: "", run_actions={"cancelpost": c.cancel_post})
    bot.review.tap({"id": "u", "data": f"rvua:{db.run_key('qf69p')}", "from": {"id": 7},
                    "message": {"message_id": mid, "chat": {"id": 100}}}, handler)
    assert _status(db, reel) == "pending" and db.get_run("qf69p")["status"] == "publishing"
    assert answers(fake)[-1].startswith("⏹ Not cancelled")
    assert items(db, "posted:qf69p")                  # the Approved item stays: nothing was undone


# ---------------------------------------------------------------------------------------------- round 2
class _Uploaded(BaseException):
    """The upload began (the claim held): raised from the stand-in upload, past publish_run's own handlers."""


def _uploadable(db, tmp_path, c, run_id="qf69"):
    """The real publish_run up to the upload: the release checks and the envelope are stand-ins; the claim is real."""
    run_dir = tmp_path / run_id
    for name in ("m.mp4", "cover.jpg", "grid.jpg"):
        (run_dir / name).write_bytes(b"x")
    db.update_run(run_id, db.get_run(run_id)["status"], thumbnail_cover_path=run_dir / "cover.jpg",
                  thumbnail_grid_path=run_dir / "grid.jpg")
    c.publisher = SimpleNamespace(build_envelope=lambda *a, **k: {"platforms": ["instagram"]})
    c._check_cancel = lambda rid: None
    c.uploads = []

    def upload(run, work):
        c.uploads.append(run["run_id"])
        raise _Uploaded()
    c._timed_upload = upload
    return c


def _poster(c, reel, run_id, outcome):
    def run():
        try:
            c._publish_booking(reel, run_id)
        except BaseException as e:  # noqa: BLE001 - the outcome is asserted by the test
            outcome["error"] = e
    return threading.Thread(target=run)


def test_threads_a_cancel_during_the_checks_before_the_claim_cancels_and_nothing_is_uploaded(db, tmp_path):
    """Real threads: the poster holds the run's upload lock through its release checks; the operator's cancel lands
    then. It cancels (nothing has posted), and the poster's claim refuses the cancelled booking."""
    run_id, reel, story = _booked(db, tmp_path)
    c = _uploadable(db, tmp_path, _coordinator(db))
    checking, go, outcome = threading.Event(), threading.Event(), {}

    def checks(run, dry_run=False, **k):
        checking.set()
        assert go.wait(10)
    c._require_release = checks
    worker = _poster(c, reel, run_id, outcome)
    worker.start()
    assert checking.wait(10)
    reply = c.cancel_post(reel)
    go.set()
    worker.join(10)
    assert reply.startswith("⏹ Booking cancelled")
    assert isinstance(outcome.get("error"), ValueError) and "booking was cancelled" in str(outcome["error"])
    assert c.uploads == [] and _status(db, reel) == "cancelled" and _status(db, story) == "cancelled"
    assert db.get_run(run_id)["status"] == "simulated"


def test_threads_a_cancel_once_the_upload_began_is_refused_and_the_booking_stays(db, tmp_path):
    from src.posting_view import NOT_CANCELLED
    run_id, reel, _ = _booked(db, tmp_path)
    c = _uploadable(db, tmp_path, _coordinator(db))
    c._require_release = lambda *a, **k: None
    uploading, go, outcome = threading.Event(), threading.Event(), {}

    def upload(run, work):
        c.uploads.append(run["run_id"])
        uploading.set()
        assert go.wait(10)
        raise _Uploaded()
    c._timed_upload = upload
    worker = _poster(c, reel, run_id, outcome)
    worker.start()
    assert uploading.wait(10)
    reply = c.cancel_post(reel)
    go.set()
    worker.join(10)
    assert reply == NOT_CANCELLED and isinstance(outcome.get("error"), _Uploaded)
    assert c.uploads == [run_id] and _status(db, reel) == "pending" and db.get_run(run_id)["status"] == "publishing"


def _read_then_cancel(db, tmp_path, c):
    run_id, reel, story = _booked(db, tmp_path)
    post = next(p for p in db.list_scheduled_posts(("pending",), run_id=run_id) if p["kind"] == "reel")
    assert c.cancel_post(reel).startswith("⏹ Booking cancelled")
    return run_id, reel, story, post


def test_a_missed_slot_reschedule_never_brings_back_a_booking_cancelled_after_the_pass_read_it(db, tmp_path,
                                                                                                monkeypatch):
    c = _coordinator(db)
    monkeypatch.setattr(c, "_may_post", lambda rid: True, raising=False)
    monkeypatch.setattr(c, "_reel_slot", lambda rid, after, days: datetime.now(UTC) + timedelta(days=1), raising=False)
    notices = []
    monkeypatch.setattr(main, "_notice", lambda owner, text, **k: notices.append(text))
    _, reel, story, post = _read_then_cancel(db, tmp_path, c)
    post["due_at"] = (datetime.now(UTC) - main.MISSED_SLOT_GRACE - timedelta(hours=1)).isoformat()
    c._post_one_due(post, datetime.now(UTC))
    assert _status(db, reel) == "cancelled" and _status(db, story) == "cancelled"
    assert not db.booking_open(reel) and notices == []


@pytest.mark.parametrize("writer", ["hold", "block", "move"])
def test_no_other_poster_write_revives_a_cancelled_booking_or_speaks_for_it(db, tmp_path, monkeypatch, writer):
    c = _coordinator(db)
    notices = []
    monkeypatch.setattr(main, "_notice", lambda owner, text, **k: notices.append(text))
    monkeypatch.setattr(c, "_block_reasons", lambda rid, exc: ["x"], raising=False)
    monkeypatch.setattr(c, "_reel_slot", lambda rid, after, days: datetime.now(UTC) + timedelta(days=2), raising=False)
    monkeypatch.setattr(c, "_episode_action", lambda rid: [], raising=False)
    _, reel, _, post = _read_then_cancel(db, tmp_path, c)
    if writer == "hold":
        c._hold_booking(post, ConnectionError("network down"))
    elif writer == "block":
        c._block_booking(post, ValueError("Release blocked: x"), datetime.now(UTC))
    else:
        assert c.move_post(reel).startswith("⏭ That Reel booking is no longer pending")
    assert _status(db, reel) == "cancelled" and notices == []


def test_threads_writers_racing_a_cancel_never_leave_a_cancelled_booking_open(db, tmp_path, monkeypatch):
    """Real threads, many rounds: the poster's reschedule and hold writes race the operator's cancel; whatever the
    order, a booking the cancel answered "cancelled" stays cancelled."""
    c = _coordinator(db)
    monkeypatch.setattr(c, "_reel_slot", lambda rid, after, days: datetime.now(UTC) + timedelta(days=1), raising=False)
    monkeypatch.setattr(main, "_notice", lambda owner, text, **k: None)
    monkeypatch.setattr(c, "_episode_action", lambda rid: [], raising=False)
    for n in range(12):
        run_id, reel, _ = _booked(db, tmp_path, run_id=f"race{n}")
        post = next(p for p in db.list_scheduled_posts(("pending",), run_id=run_id) if p["kind"] == "reel")
        start, replies = threading.Barrier(2), {}

        def cancel(reel=reel, start=start, replies=replies):
            start.wait()
            replies["cancel"] = c.cancel_post(reel)

        def poster(post=post, run_id=run_id, start=start):
            start.wait()
            c._reschedule(dict(post), db.get_run(run_id), datetime.now(UTC))
            c._hold_booking(dict(post, detail={}), ConnectionError("x"))
        threads = [threading.Thread(target=cancel), threading.Thread(target=poster)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(10)
        assert replies["cancel"].startswith("⏹ Booking cancelled")
        assert _status(db, reel) == "cancelled", n


def test_the_panel_closes_the_approved_item_only_when_the_cancel_did_not_refuse(db, tmp_path, monkeypatch):
    from src import telegram_bot
    from src.posting_view import NOT_CANCELLED
    bot, _fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "qf69q")
    mid = deliver(bot, db, root, payload, "qf69q")
    tap(bot, f"rva:{db.run_key('qf69q')}", mid)
    db.add_scheduled_post("qf69q", "reel", (datetime.now(UTC) + timedelta(hours=3)).isoformat(), [])
    monkeypatch.setattr(telegram_bot, "undo_minutes", lambda: 0.0)
    bot.review.tap({"id": "u", "data": f"rvua:{db.run_key('qf69q')}", "from": {"id": 7},
                    "message": {"message_id": mid, "chat": {"id": 100}}}, lambda command, args: NOT_CANCELLED)
    assert items(db, "posted:qf69q")             # refused, whatever the run's state says: the item stays
