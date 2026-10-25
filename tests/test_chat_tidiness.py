"""Cards that have done their job leave the chat (operator, 2026-10-25): "Sending ... for review again" and
"Thanks, noted" stayed as messages, the "No decision" notice stayed after its resend, and a rejected review
(video, frames and reason card) stayed for good. The run, its decision and reason remain in History."""
from src import telegram_bot as tb
from src.telegram_bot import TelegramApprovalBot
from src.telegram_state import get, initialize, put


def _bot(db, monkeypatch, updates):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    initialize(db)
    calls = []

    def api(method, **kw):
        if method == "getUpdates":
            return updates
        calls.append((method, (kw.get("data") or {}).get("text"), (kw.get("data") or {}).get("message_id")))
        return {"message_id": 77} if method == "sendMessage" else True

    monkeypatch.setattr(bot, "_api", api)
    monkeypatch.setattr(bot, "delete_message", lambda mid: calls.append(("delete", None, mid)) or True)
    monkeypatch.setattr(bot, "delete_messages", lambda mids: [calls.append(("delete", None, m)) for m in mids])
    bot._offset = 0
    return bot, calls


def _click(data, message_id):
    return [{"update_id": 1, "callback_query": {"id": "q", "data": data, "from": {"id": 555},
                                                "message": {"chat": {"id": 555}, "message_id": message_id, "text": "x"}}}]


def test_a_resend_removes_its_notice_and_only_nudges(db, monkeypatch):
    db.create_run("run_t", db.list_all_topics()[0]["id"])
    key = db.run_key("run_t")
    bot, calls = _bot(db, monkeypatch, _click(f"resend:{key}", 40))
    nudges = []
    monkeypatch.setattr(bot, "nudge", lambda text: nudges.append(text) or True)
    bot.poll_once(lambda c, a: "📨 Sending it for review again…")
    bot.drain()
    assert ("delete", None, 40) in calls and nudges == ["📨 Sending it for review again…"]
    assert not any(m == "sendMessage" for m, _, _ in calls)                      # nothing that stays


def test_a_resend_from_the_dashboard_keeps_the_dashboard(db, monkeypatch):
    db.create_run("run_t", db.list_all_topics()[0]["id"])
    db.set_setting("telegram_dashboard_id", "40")
    bot, calls = _bot(db, monkeypatch, _click(f"resend:{db.run_key('run_t')}", 40))
    monkeypatch.setattr(bot, "nudge", lambda text: True)
    bot.poll_once(lambda c, a: "📨 Sending…")
    bot.drain()
    assert ("delete", None, 40) not in calls


def test_a_rejected_review_leaves_the_chat(db, monkeypatch):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_r", topic)
    db.record_approval("run_r", topic, 300)
    db.update_approval_decision("run_r", "rejected", decided_by="telegram:555")
    initialize(db)
    put(db, "review-messages", "run_r", [11, 12, 50])
    key = db.run_key("run_r")
    bot, calls = _bot(db, monkeypatch, _click(f"rsn:{key}:other", 50))
    bot.poll_once(lambda c, a: f"❌ Rejected. ✅ Reason: Other. [run {key}]")
    bot.drain()
    assert ("delete", None, 11) in calls and ("delete", None, 12) in calls       # the video and frames at once
    assert ("delete", None, 50) not in calls and get(db, "review-messages", "run_r") == [50]
    later = "2999-01-01T00:00:00+00:00"                                          # it leaves within 30 minutes
    assert [a["message_id"] for a in db.get_expired_alerts(later)] == [50] and not db.get_expired_alerts()
    # Details replied to the reason card: acknowledged by a nudge, and the card goes.
    reply = [{"update_id": 2, "message": {"text": "The pictures were dull", "from": {"id": 555}, "chat": {"id": 555},
                                          "message_id": 60, "reply_to_message": {"message_id": 50,
                                                                                 "text": f"✅ Reason: Other. [run {key}]"}}}]
    bot2, calls2 = _bot(db, monkeypatch, reply)
    nudges = []
    monkeypatch.setattr(bot2, "nudge", lambda text: nudges.append(text) or True)
    seen = []
    bot2.poll_once(lambda c, a: seen.append((c, a)) or "📝 Thanks, noted.")
    bot2.drain()
    assert seen and seen[0][0] == "whytext" and nudges == ["📝 Thanks, noted."]
    assert ("delete", None, 50) in calls2 and not any(m == "sendMessage" for m, _, _ in calls2)


def test_a_regenerated_review_is_not_removed_by_its_reason(db, monkeypatch):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_g", topic)
    db.record_approval("run_g", topic, 300)
    db.update_approval_decision("run_g", "regenerate", decided_by="telegram:555")
    initialize(db)
    put(db, "review-messages", "run_g", [11, 50])
    bot, calls = _bot(db, monkeypatch, _click(f"rsn:{db.run_key('run_g')}:pictures", 50))
    bot.poll_once(lambda c, a: "🔄 Regeneration requested.")
    bot.drain()
    assert not any(m == "delete" for m, _, _ in calls)                          # its new review replaces it


def test_the_reason_card_says_when_it_leaves():
    assert tb.REASON_CARD_MINUTES == 30


def test_the_progress_card_redraws_itself_while_its_run_is_made(db, monkeypatch):
    # Operator, 2026-10-25: the card only changed when a step did, so a 6-minute step looked frozen.
    import time
    db.create_run("run_p", db.list_all_topics()[0]["id"])
    db.update_run("run_p", "producing")
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "PROGRESS_TICK_SECONDS", 0.05)
    monkeypatch.setattr(db, "get_active_progress", lambda: {"run_id": "run_p", "message_id": 9, "step": 2,
                                                            "total_steps": 5, "topic_title": "T", "details": "voice"})
    drawn = []
    monkeypatch.setattr(bot, "update_progress_card", lambda *a, **k: drawn.append(a) or True)
    thread = bot.start_progress_ticker("run_p")
    time.sleep(0.3)
    assert drawn and drawn[0][:6] == (9, 2, 5, "T", "run_p", "voice")
    db.update_run("run_p", "failed")                                              # the run ends: the ticker stops
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_a_screen_asked_for_from_a_protected_card_replaces_the_one_panel(db, monkeypatch):
    # Production 2026-10-25: "← Dashboard" on a decided review card drew the dashboard in that card, and the
    # old dashboard stayed: two panels in the chat.
    db.set_setting("telegram_dashboard_id", "40")
    bot, calls = _bot(db, monkeypatch, [])
    monkeypatch.setattr(bot, "_protected_messages", lambda: {50})
    monkeypatch.setattr(bot, "_delete_soon", lambda mid: calls.append(("delete", None, mid)))
    assert bot.show_navigation_panel(50, "🏛️ Dashboard", [[{"text": "x", "callback_data": "cdash:"}]])
    assert not any(m == "editMessageText" and mid == 50 for m, _, mid in calls)      # the card stays as it is
    assert ("delete", None, 40) in calls and db.get_setting("telegram_dashboard_id") == "77"
