"""The runtime review of 2026-10-25: one wait per review across processes (a Resend while the first wait ran
acted on one Approve twice), a run booked once only, the stale sweep counting from the newest card and saying
so, and a long review card kept to one formatted message."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import main
from src.publication_lock import publication_lock
from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot, _tg_len


def _waiter(db, calls):
    bot = SimpleNamespace(wait_for_decision=lambda run_id, window: calls.append(run_id) or "approved")
    return SimpleNamespace(db=db, telegram_bot=bot, _approval_window=lambda: 60)


def test_a_second_wait_on_a_review_is_not_started(db):
    calls = []
    me = _waiter(db, calls)
    with publication_lock(db.db_path, "review-wait:run_x"):        # the first wait (another process or thread)
        assert main.PipelineCoordinator._wait_for_review(me, "run_x") is None
    assert calls == []
    assert main.PipelineCoordinator._wait_for_review(me, "run_x") == "approved" and calls == ["run_x"]


def test_a_resend_during_a_wait_only_brings_the_card_down(db, tmp_path):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_y", topic)
    db.update_run("run_y", "pending_approval", master_video_path=str(tmp_path / "m.mp4"), thumbnail_cover_path=str(tmp_path / "c.jpg"))
    db.record_approval("run_y", topic, 5)
    sent, waited = [], []
    bot = SimpleNamespace(send_approval_request=lambda *a: sent.append(a[-1]),
                          wait_for_decision=lambda run_id, window: waited.append(run_id) or "approved")
    me = SimpleNamespace(db=db, telegram_bot=bot, _approval_window=lambda: 60,
                         _wait_for_review=lambda run_id: main.PipelineCoordinator._wait_for_review(me, run_id),
                         _after_approval=lambda *a, **k: {"after": True})
    with publication_lock(db.db_path, "review-wait:run_y"):
        result = main.PipelineCoordinator.request_approval(me, "run_y")
    assert sent == ["run_y"] and waited == [] and result["already_waiting"] and "after" not in result


def test_a_run_is_booked_once(db, tmp_path):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_z", topic)
    db.update_run("run_z", "scheduled", master_video_path=str(tmp_path / "m.mp4"))
    db.add_scheduled_post("run_z", "reel", "2026-10-27T16:00:00+00:00", [])
    me = SimpleNamespace(db=db)
    out = main.PipelineCoordinator.schedule_run(me, "run_z")
    assert out["already_booked"] and len(db.list_scheduled_posts(("pending",), run_id="run_z")) == 1


def test_a_resent_review_is_not_swept_by_its_first_delivery_time(db):
    from src.telegram_state import initialize
    initialize(db)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_s", topic)
    db.record_approval("run_s", topic, 1)
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET created_at=? WHERE run_id='run_s'", (old,))
    db.record_approval("run_s", topic, 2)                               # sent again now
    notices = []
    bot = TelegramApprovalBot.__new__(TelegramApprovalBot)
    bot.db, bot.notify = db, lambda *a, **k: notices.append(a[0]) or 9
    bot.dismiss_approval_buttons = lambda run_id: None
    assert bot.cleanup_stale_approvals() == 0
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET created_at=? WHERE run_id='run_s'", (old,))
    assert bot.cleanup_stale_approvals() == 1 and "No decision" in notices[-1]   # and it says so, with Resend


def test_a_long_review_card_keeps_its_narration_and_stays_one_message():
    narration = " ".join(["word"] * 110)
    findings = [{"severity": "info", "card": "cover_look", "issue": "a long note " * 25} for _ in range(6)]
    payload = {"title": "T", "narration_script": narration, "caption": "Opening line. " + "More. " * 300,
               "carousel_caption": "Swipe through.", "self_healing": {"issues": findings},
               "qa_summary": "🔎 Automated video check: 6 issue(s)\n" + "\n".join("· beat 2: " + "x " * 120 for _ in range(6))}
    card = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert narration in card and "[shortened:" not in card and _tg_len(card) <= CARD_PAGE_LIMIT
    assert "<b>Caption:</b>" in card and "<b>Carousel caption:</b>" in card
    assert "more: reply /ask to this card" in card                        # the folded lists say what was left out
