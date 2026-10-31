"""The daily heartbeat and the weekly digest live on the one dashboard panel (operator, 2026-10-25); a nudge
buzzes and leaves."""
import json
from datetime import datetime, timedelta, timezone

from src import control
from src.control import dashboard_text_and_buttons, make_command_handler
from src.telegram_bot import TelegramApprovalBot


def test_the_heartbeat_is_a_dashboard_line_for_a_day(db, tmp_path):
    assert control.heartbeat_line(db) is None
    control.record_heartbeat(db)
    text, _ = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    assert "heartbeat · daemon running" in text
    db.set_setting(control.HEARTBEAT_KEY, (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat())
    assert control.heartbeat_line(db) is None


def test_the_weekly_digest_opens_in_the_panel_for_a_week(db, tmp_path):
    control.record_weekly_digest(db, "📰 <b>Weekly digest</b>\n3 findings")
    text, buttons = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    assert "This week's digest is ready" in text
    assert "cdigest:" in [b["callback_data"] for row in buttons for b in row]
    body, panel = make_command_handler(db, lambda: None, lambda: "")("cdigest", "")
    assert body.endswith("3 findings") and panel[-1][0]["callback_data"] == "cdash:"
    old = json.loads(db.get_setting(control.DIGEST_KEY))
    old["at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    db.set_setting(control.DIGEST_KEY, json.dumps(old))
    text, buttons = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    assert "digest is ready" not in text and "cdigest:" not in [b["callback_data"] for row in buttons for b in row]


def test_a_nudge_buzzes_and_is_removed(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    sent, deleted = [], []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: sent.append((method, kw.get("data", {}))) or {"message_id": 41})
    monkeypatch.setattr(bot, "delete_message", lambda mid: deleted.append(mid) or True)
    monkeypatch.setattr(bot, "NUDGE_SECONDS", 0.05)
    assert bot.nudge("🔔 Dashboard updated")
    method, data = sent[0]
    assert method == "sendMessage" and "disable_notification" not in data              # it buzzes
    import time
    time.sleep(0.3)
    assert deleted == [41]
