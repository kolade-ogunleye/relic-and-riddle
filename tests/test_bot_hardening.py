"""The Telegram bot tells only measured facts, keeps taps made while it was down, keeps review
cards on /clear, runs slow commands beside the poller, and masks credentials in the chat."""

import time

from src import motion
from src.telegram_bot import TelegramApprovalBot


def _bot(db, monkeypatch, updates):
    calls = []
    bot = TelegramApprovalBot(db=db, bot_token="123:abcdefghijklmnopqrstuvwxyz", chat_id="555", approver_ids={"555"})

    def api(method, **kw):
        calls.append((method, kw.get("data", {})))
        if method == "getUpdates":
            out, updates[:] = list(updates), []
            return out
        return {"message_id": 900}
    monkeypatch.setattr(bot, "_api", api)
    return bot, calls


def test_review_card_shows_the_release_gates_measurements_not_a_fixed_claim():
    report = {"checks": [
        {"name": "duration", "status": "pass", "detail": "Measured 42.267s; expected 42.0-58.0s."},
        {"name": "loudness", "status": "pass", "detail": {"integrated_lufs": -14.2, "true_peak_dbtp": -1.8}},
        {"name": "sync_drift", "status": "pass", "detail": "Measured 6.7ms audio/video duration drift"},
        {"name": "asset_rights", "status": "needs_review", "detail": ["C:/x/motion/motion_01.mp4"]}]}
    card = TelegramApprovalBot.format_card_text({"title": "T"}, "run_x", release=report)
    assert "42.3 s" in card and "-14.2 LUFS" in card and "A/V drift 7 ms" in card
    assert "asset_rights: motion_01.mp4" in card and "C:/x/motion" not in card
    assert "Balanced Sub-Bass" not in card
    assert "not run yet" in TelegramApprovalBot.format_card_text({"title": "T"}, "run_x")


def test_taps_made_while_down_are_applied_and_stale_commands_are_not(db, monkeypatch):
    seen = []
    updates = [
        {"update_id": 7, "message": {"message_id": 1, "text": "/run", "date": int(time.time()) - 3600,
                                     "from": {"id": 555}, "chat": {"id": 555}}},
        {"update_id": 8, "message": {"message_id": 2, "text": "/status", "date": int(time.time()),
                                     "from": {"id": 555}, "chat": {"id": 555}}},
    ]
    bot, calls = _bot(db, monkeypatch, updates)
    bot.poll_once(lambda c, a: seen.append(c) or "ok")
    bot.drain()
    first = next(d for m, d in calls if m == "getUpdates")
    assert "offset" not in first          # unconfirmed updates are fetched, not skipped
    assert seen == ["status"]             # the hour-old /run is not replayed
    assert bot._offset == 9


def test_clear_keeps_review_cards_waiting_for_a_decision(db, monkeypatch):
    db.create_run("run_a", "01_topic")
    db.record_approval("run_a", "01_topic", 590)
    updates = [{"update_id": 1, "message": {"message_id": 600, "text": "/clear", "date": int(time.time()),
                                            "from": {"id": 555}, "chat": {"id": 555}}}]
    bot, calls = _bot(db, monkeypatch, updates)
    bot.poll_once(lambda c, a: "ok")
    bot.drain()
    deleted = [mid for m, d in calls if m == "deleteMessages" for mid in __import__("json").loads(d["message_ids"])]
    assert deleted and 590 not in deleted and 599 in deleted


def test_slow_commands_run_beside_the_poller(db, monkeypatch):
    updates = [{"update_id": 1, "message": {"message_id": 5, "text": "/ask why", "date": int(time.time()),
                                            "from": {"id": 555}, "chat": {"id": 555}}}]
    bot, calls = _bot(db, monkeypatch, updates)
    started = time.monotonic()
    bot.poll_once(lambda c, a: time.sleep(1.0) or "answer")
    assert time.monotonic() - started < 0.8      # the poller is not blocked
    bot._background().shutdown(wait=True)
    assert any(m in {"sendMessage", "editMessageText"} and "answer" in d.get("text", "") for m, d in calls)


def test_chat_messages_never_carry_the_bot_token(db, monkeypatch):
    bot, calls = _bot(db, monkeypatch, [])
    bot.notify("failed: https://api.telegram.org/bot123:abcdefghijklmnopqrstuvwxyz/getFile")
    text = next(d["text"] for m, d in calls if m == "sendMessage")
    assert "abcdefghijklmnopqrstuvwxyz" not in text


def test_a_reused_clip_stays_on_its_own_photo(monkeypatch, db, tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    for p in (a, b):
        p.write_bytes(b"0")
    clip = tmp_path / "motion_01.mp4"
    clip.write_bytes(b"0")
    monkeypatch.setattr("src.config.MEDIA_MONTHLY_BUDGET_USD", 5.0)
    monkeypatch.setattr("src.config.MOTION_MAX_CLIPS", 1)
    memo = {str(a): (clip, "kling")}
    scenes, used = motion.apply(db, [b, a], tmp_path / "m", memo=memo)  # the re-plan moved photo a to beat 2
    assert scenes == [b, clip] and used == ["kling"]


def test_progress_of_a_finished_run_is_not_kept_alive(db):
    # The Kailasa dry run ended as "simulated" but its card stayed "in production", and /clear
    # kept re-posting it. A record whose run is no longer being made is stale and cleared.
    db.create_run("run_a", "01_topic")
    db.set_active_progress(run_id="run_a", topic_title="T", step=4, total_steps=5, message_id=696)
    assert db.get_active_progress()["message_id"] == 696      # still producing: kept
    db.update_run("run_a", "simulated")
    assert db.get_active_progress() is None and db.get_setting("active_progress") is None


def test_done_closes_a_card_and_navigation_keeps_its_buttons(db, monkeypatch):
    import json as _json
    from src.control import make_command_handler
    upd = [{"update_id": 1, "callback_query": {"id": "q", "data": "done:", "from": {"id": 555},
                                               "message": {"chat": {"id": 555}, "message_id": 41}}}]
    bot, calls = _bot(db, monkeypatch, upd)
    h = make_command_handler(db, lambda: None, lambda: "started")
    bot.poll_once(h)
    bot.drain()
    assert any(m == "deleteMessage" and d.get("message_id") == 41 for m, d in calls)        # Done closed it
    calls.clear()
    upd.append({"update_id": 2, "callback_query": {"id": "q2", "data": "qrefresh:", "from": {"id": 555},
                                                   "message": {"chat": {"id": 555}, "message_id": 42}}})
    bot.poll_once(h)
    bot.drain()
    edit = next(d for m, d in calls if m == "editMessageText")
    assert edit["message_id"] == 42                                                         # in place...
    buttons = [b["callback_data"] for row in _json.loads(edit["reply_markup"])["inline_keyboard"] for b in row]
    assert "qrefresh:" in buttons and "cdash:" in buttons                                    # ...with its buttons
    assert not any(m == "editMessageReplyMarkup" for m, d in calls)                         # never stripped first


def test_old_history_is_pruned_but_the_audit_trail_is_kept(db):
    old = "2020-01-01T00:00:00+00:00"
    db.record_audit_event(user_id="1", username="u", action="cmd", payload="x", status="allowed")
    db.create_run("run_a", "01_topic")
    with db._conn() as conn:
        conn.execute("UPDATE audit_log SET created_at = ?", (old,))
        conn.execute("UPDATE runs SET created_at = ?", (old,))
    assert db.prune_history()["audit_log"] == 1
    assert db.get_run("run_a") is not None                     # runs are never pruned


def test_cli_reject_records_its_reason_and_closes_the_telegram_card(db, monkeypatch):
    import main
    from src import improve
    db.create_run("run_a", "01_topic")
    db.record_approval("run_a", "01_topic", 9)
    closed = []
    monkeypatch.setattr(main, "PipelineDatabase", lambda *a, **k: db)
    monkeypatch.setattr("src.telegram_bot.TelegramApprovalBot.dismiss_approval_buttons", lambda self, r: closed.append(r))
    assert main.main(["review", "reject", "run_a", "--reason", "pictures", "--note", "wrong temple"]) in (0, None)
    assert improve.operator_feedback(db, "run_a")["reason"] == "pictures" and closed == ["run_a"]


def test_progress_eta_uses_how_long_recent_runs_took(monkeypatch, tmp_path):
    import json as _json
    from src import telegram_bot
    run = tmp_path / "run_x"
    run.mkdir()
    (run / "timings.json").write_text(_json.dumps([
        {"stage": "a", "seconds": 60, "at": "2026-10-20T10:01:00Z"},
        {"stage": "b", "seconds": 600, "at": "2026-10-20T10:15:00Z"},
        {"stage": "review_proxy", "seconds": 20, "at": "2026-10-20T10:20:00Z"}]), encoding="utf-8")
    monkeypatch.setattr("src.config.OUTPUT_DIR", tmp_path)
    assert telegram_bot.typical_run_seconds() == 19 * 60 + 60   # first start to last finish
    # A resumed run (2026-10-24: its stamps spanned two sessions hours apart) and one that stopped before
    # its review are not a run's length.
    resumed, early = tmp_path / "run_resumed", tmp_path / "run_early"
    resumed.mkdir(), early.mkdir()
    (resumed / "timings.json").write_text(_json.dumps([
        {"stage": "a", "seconds": 60, "at": "2026-10-20T09:00:00Z"},
        {"stage": "voiceover", "seconds": 0.1, "at": "2026-10-20T11:28:00Z"},
        {"stage": "review_proxy", "seconds": 20, "at": "2026-10-20T11:45:00Z"}]), encoding="utf-8")
    (early / "timings.json").write_text(_json.dumps([
        {"stage": "script_and_fact_check", "seconds": 240, "at": "2026-10-20T12:04:00Z"}]), encoding="utf-8")
    assert telegram_bot.typical_run_seconds() == 19 * 60 + 60


def test_progress_cards_name_the_work_each_step_does(db, monkeypatch):
    # One list for the first card, live updates and Refresh; the old names were a step behind.
    from src.telegram_bot import PROGRESS_STEPS
    bot, calls = _bot(db, monkeypatch, [])
    db.create_run("run_p", db.list_all_topics()[0]["id"])
    bot.send_progress_card("run_p", "T")
    first = [d["text"] for m, d in calls if m == "sendMessage"][-1]
    assert all(step in first for step in PROGRESS_STEPS) and "Mastering narration" not in first
    assert PROGRESS_STEPS[1].startswith("Music")  # what runs after the voiceover is reported
