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
    assert "asset_rights: needs_review (motion_01.mp4)" in card
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
    assert any(m == "sendMessage" and "answer" in d.get("text", "") for m, d in calls)


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
