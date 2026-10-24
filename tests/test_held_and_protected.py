"""
tests/test_held_and_protected.py
================================
Item 4a: a timed-out review's topic waits for the decision instead of being made again.
Item 4b: /clear keeps a reason card while a reply to it is still useful.
Item 5a: a running fetch's button says so instead of starting a duplicate.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from src import control
from src.db import QueueEmpty, TIMEOUT_HOLD_DAYS


def _timed_out(db, run_id, topic_id, days_ago=0):
    db.create_run(run_id, topic_id)
    db.update_run(run_id, "timeout")
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with db._conn() as conn:
        conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (when, run_id))


def test_a_timed_out_reviews_topic_is_not_made_again_while_it_waits(db):
    first = db.ranked_topics()[0]["id"]
    _timed_out(db, "run_t", first)
    assert first not in [t["id"] for t in db.ranked_topics()]


def test_after_the_hold_the_topic_returns_to_the_queue(db):
    first = db.ranked_topics()[0]["id"]
    _timed_out(db, "run_t", first, days_ago=TIMEOUT_HOLD_DAYS + 1)
    assert first in [t["id"] for t in db.ranked_topics()]


def test_a_queue_held_only_by_timeouts_says_so_not_that_everything_was_published(db):
    for n, t in enumerate(db.list_all_topics()):
        _timed_out(db, f"run_{n}", t["id"])
    with pytest.raises(QueueEmpty, match="timed-out review"):
        db.ranked_topics()


def test_the_dashboard_and_reviews_offer_the_timed_out_review(db):
    topic = db.ranked_topics()[0]["id"]
    _timed_out(db, "run_t", topic)
    text, buttons = control.dashboard_text_and_buttons(db)
    assert "1 review(s) timed out" in text
    assert any(b["callback_data"] == "creviews:" and "(1)" in b["text"] for row in buttons for b in row)
    reviews, rows = control.make_command_handler(db, lambda: None, lambda: "")("creviews", "")
    assert "Timed out" in reviews and any(b["callback_data"].startswith("resend:") for row in rows for b in row)


def test_clear_keeps_a_recent_reason_card_only(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot, REASON_CARD_HOURS
    topic = db.list_all_topics()[0]["id"]
    for run_id, mid, hours in (("run_new", 501, 0), ("run_old", 502, REASON_CARD_HOURS + 1)):
        db.create_run(run_id, topic)
        db.record_approval(run_id, topic, mid)
        db.update_approval_decision(run_id, "rejected", decided_by="t")
        with db._conn() as conn:
            conn.execute("UPDATE approvals SET decision_timestamp = ? WHERE run_id = ?",
                         ((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(), run_id))
    keep = TelegramApprovalBot(db=db, bot_token="", chat_id="", approver_ids=set()).clear_protected()
    assert 501 in keep and 502 not in keep


def test_a_running_fetch_shows_progress_instead_of_starting_again(db):
    assert control.job_button(db, "fetchimages:t1", "Fetch more") == {"text": "Fetch more", "callback_data": "fetchimages:t1"}
    db.set_setting("operator_job:j1", json.dumps({"id": "j1", "resource": "fetchimages:t1", "state": "running",
                                                  "owner_pid": os.getpid(), "updated_at": datetime.now(timezone.utc).isoformat()}))
    button = control.job_button(db, "fetchimages:t1", "Fetch more")
    assert button["callback_data"] == "jobs:" and button["text"].startswith("⏳ Fetching")
