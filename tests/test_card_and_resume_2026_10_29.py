"""29 Oct 2026: (1) the dev bot resumed the review wait of a run a CLI auto-approved dry run was still acting
on, and the dry-run publish ran twice (06:19:00 and 06:19:12, Richat); in production that is a second post.
(2) "there is no text in the message to edit": a process that had no record of a photo/audio panel (a restart,
the run's worker) sent editMessageText to it (dev 28 Oct 22:16, production 29 Oct 11:56)."""
import json
import logging
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import main
from src import process_state
from src import telegram_bot as tb
from src.publication_lock import publication_lock
from src.telegram_bot import TelegramApprovalBot
from src.telegram_state import initialize

NO_TEXT = "Telegram editMessageText failed: HTTP 400 Bad Request: there is no text in the message to edit"


# ---------------------------------------------------------------- 1. a review wait never resumed twice

def _pending(db, run_id, decision="approved"):
    topic = db.list_all_topics()[0]["id"]
    db.create_run(run_id, topic)
    db.update_run(run_id, "pending_approval")
    db.record_approval(run_id, topic)
    if decision != "pending":
        db.update_approval_decision(run_id, decision, decided_by="auto-approve-flag")
    return topic


def _resume(db, decided_only=False):
    followed = []
    main.resume_reviews(SimpleNamespace(db=db), lambda run_id, resend: followed.append(run_id), set(),
                        decided_only=decided_only)
    return followed


@pytest.fixture
def other_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _owned_by(db, run_id, pid, ident):
    db.set_setting(main.RUN_OWNER_PREFIX + run_id, json.dumps({"pid": pid, "identity": ident}))


def test_a_run_a_live_process_is_finishing_is_not_resumed(db, other_process, caplog):
    # The incident: approved by the flag, still pending_approval while the CLI acts on it.
    _pending(db, "run_richat")
    _owned_by(db, "run_richat", other_process.pid, process_state.identity(other_process.pid))
    with caplog.at_level(logging.INFO, logger="pipeline"):
        assert _resume(db, decided_only=True) == []
        assert _resume(db) == []
    assert "process %d owns it" % other_process.pid in caplog.text


def test_a_run_whose_owner_ended_or_whose_pid_was_reused_is_resumed(db, monkeypatch):
    _pending(db, "run_dead")
    _pending(db, "run_reused")
    _pending(db, "run_unowned")
    _owned_by(db, "run_dead", 999001, "111")
    _owned_by(db, "run_reused", 999002, "222")
    seen = {999001: None, 999002: "333"}  # ended; a new process under the same pid
    monkeypatch.setattr(process_state, "identity", lambda pid: seen.get(pid, "x"))
    assert sorted(_resume(db)) == ["run_dead", "run_reused", "run_unowned"]


def test_an_owner_that_cannot_be_checked_counts_as_alive(db, monkeypatch):
    _pending(db, "run_q")
    _owned_by(db, "run_q", 999003, "444")
    monkeypatch.setattr(process_state, "identity", lambda pid: "unknown")
    assert _resume(db) == []


def test_claim_and_release_are_this_process_s_own(db):
    _pending(db, "run_c")
    main.claim_run(db, "run_c")
    record = json.loads(db.get_setting(main.RUN_OWNER_PREFIX + "run_c"))
    assert record["pid"] == os.getpid() and record["identity"] == process_state.identity(os.getpid())
    assert main.run_owned_elsewhere(db, "run_c") is None  # this process's own waits are tracked in `waiting`
    main.release_run(db, "run_c")
    assert not db.get_setting(main.RUN_OWNER_PREFIX + "run_c")
    _owned_by(db, "run_c", os.getpid() + 100000, "555")  # another process's record is never cleared here
    main.release_run(db, "run_c")
    assert json.loads(db.get_setting(main.RUN_OWNER_PREFIX + "run_c"))["pid"] == os.getpid() + 100000


def _coordinator(db):
    bot = SimpleNamespace(wait_for_decision=lambda *a, **k: pytest.fail("a decided review is not waited on"))
    me = SimpleNamespace(db=db, telegram_bot=bot, _approval_window=lambda: 60,
                         REVIEW_ACTED_PREFIX=main.PipelineCoordinator.REVIEW_ACTED_PREFIX,
                         _refused_for_good=main.PipelineCoordinator._refused_for_good)
    return me


def test_an_auto_approve_is_acted_on_once_under_the_review_lock(db):
    # The flag's Approve is acted on under the review lock and marked, so a wait resumed later finds it acted on.
    _pending(db, "run_auto")
    me, acted = _coordinator(db), []

    def act(decision):  # as the dry-run publish does: the run is settled as simulated
        acted.append(decision)
        db.update_run("run_auto", "simulated")
        return {"status": "simulated"}
    assert main.PipelineCoordinator._decide_review(me, "run_auto", act, decided="approved") == ("approved", {"status": "simulated"})
    me.telegram_bot.wait_for_decision = lambda *a, **k: "approved"   # the resumed wait reads the recorded Approve
    assert main.PipelineCoordinator._decide_review(me, "run_auto", act) == ("approved", None)
    assert acted == ["approved"]


def test_an_auto_approve_is_not_acted_on_while_another_wait_holds_the_review(db):
    _pending(db, "run_held")
    me, acted = _coordinator(db), []
    with publication_lock(db.db_path, "review-wait:run_held"):
        assert main.PipelineCoordinator._decide_review(me, "run_held", acted.append, decided="approved") == (None, None)
    assert acted == []


# ---------------------------------------------------------------- 2. a media card is never sent editMessageText

def _bot(db, monkeypatch, no_text=()):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    initialize(db)
    calls = []

    def api(method, **kw):
        data = kw.get("data") or {}
        calls.append((method, data.get("message_id")))
        if method == "editMessageText" and data.get("message_id") in no_text:
            raise RuntimeError(NO_TEXT)
        return {"message_id": 77} if method == "sendMessage" else True

    monkeypatch.setattr(bot, "_api", api)
    monkeypatch.setattr(bot, "delete_message", lambda mid: calls.append(("delete", mid)) or True)
    monkeypatch.setattr(bot, "_protected_messages", lambda: set())
    monkeypatch.setattr(bot, "_delete_soon", lambda mid: calls.append(("delete_soon", mid)))
    return bot, calls


def test_a_photo_panel_unknown_to_this_process_is_replaced_not_warned_about(db, monkeypatch, caplog):
    db.set_setting("telegram_dashboard_id", "1238")
    bot, calls = _bot(db, monkeypatch, no_text={1238})
    with caplog.at_level(logging.INFO, logger="src.telegram_bot"):
        assert bot.show_navigation_panel(1238, "🏛️ Dashboard", [[{"text": "x", "callback_data": "cdash:"}]])
    assert ("sendMessage", None) in calls and ("delete", 1238) in calls
    assert db.get_setting("telegram_dashboard_id") == "77"
    assert "Could not edit message text" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    # A new process (a restart, the run's worker) knows the kind from the database: no text edit is tried.
    fresh, again = _bot(db, monkeypatch, no_text={1238})
    assert fresh._is_media(1238)
    assert fresh.show_navigation_panel(1238, "🏛️ Dashboard", [[{"text": "x", "callback_data": "cdash:"}]])
    assert ("editMessageText", 1238) not in again and ("sendMessage", None) in again


def test_a_text_edit_of_a_media_card_edits_its_caption_or_leaves_it_to_a_fresh_card(db, monkeypatch, caplog):
    bot, calls = _bot(db, monkeypatch, no_text={1624})
    with caplog.at_level(logging.INFO, logger="src.telegram_bot"):
        # The first answer teaches the kind: False, so the caller sends a fresh card (as after any failed edit)
        assert not bot.edit_message_text(1624, "❌ <b>Production failed</b>")
        assert bot.edit_message_text(1624, "❌ <b>Production failed</b>", [[{"text": "Done", "callback_data": "done:"}]])
    assert calls == [("editMessageText", 1624), ("editMessageCaption", 1624)]
    calls.clear()
    assert not bot.edit_message_text(1624, "x" * 1500)   # too long for a caption: the caller sends a fresh card
    assert calls == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_text_card_stays_a_text_edit(db, monkeypatch):
    bot, calls = _bot(db, monkeypatch)
    assert bot.edit_message_text(500, "⏳ step 2")
    assert calls == [("editMessageText", 500)]


def test_every_sent_card_records_its_kind(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    initialize(db)
    answers = {"sendPhoto": {"message_id": 41, "photo": [{}]}, "sendMessage": {"message_id": 42, "text": "t"},
               "editMessageMedia": {"message_id": 43, "audio": {}}}

    def post(url, **kw):
        method = url.rsplit("/", 1)[1]
        return SimpleNamespace(ok=True, status_code=200, headers={"content-type": "application/json"},
                               json=lambda: {"ok": True, "result": answers[method]})

    monkeypatch.setattr(tb, "_post", post)
    bot._api("sendPhoto", data={"chat_id": "555", "caption": "c"})
    bot._api("sendMessage", data={"chat_id": "555", "text": "t"})
    bot._api("editMessageMedia", data={"chat_id": "555", "message_id": 43})
    reloaded = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    assert reloaded._is_media(41) and reloaded._is_media(43) and not reloaded._is_media(42)
    bot._record_kind(41, media=False)  # a panel that became text again is edited as text
    assert not TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})._is_media(41)
