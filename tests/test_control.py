import pytest

from src import control
from src.control import is_paused, make_command_handler, status_text
from src.telegram_bot import TelegramApprovalBot


@pytest.fixture
def handler(db):
    triggered = []
    h = make_command_handler(db, lambda: "Mon 13 Oct 09:00 EDT", lambda: triggered.append(1) or "starting")
    return h, triggered


def test_pause_and_resume(db, handler):
    h, _ = handler
    assert not is_paused(db)
    assert "paused" in h("pause", "")
    assert is_paused(db)
    assert "resumed" in h("resume", "")
    assert not is_paused(db)


def test_status_next_help_and_unknown(db, handler):
    h, _ = handler
    assert "active" in h("status", "") and "Mon 13 Oct" in h("status", "")
    assert "01_topic" in h("next", "")
    assert "/pause" in h("help", "")
    assert "Unknown command" in h("selfdestruct", "")


def test_run_command_triggers(db, handler):
    h, triggered = handler
    assert h("run", "") == "starting" and triggered == [1]


def test_status_escapes_html(db):
    db.create_run("r1", "01_topic")
    db.update_run("r1", "failed", error="<script>")
    assert "<script>" not in status_text(db, "<b>soon</b>")


def _bot(db, monkeypatch, updates):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    sent = []

    def api(method, **kw):
        if method == "getUpdates":
            return updates
        if method == "sendMessage":
            sent.append(kw["data"]["text"])
        return {"message_id": 1}

    monkeypatch.setattr(bot, "_api", api)
    bot._offset = 0
    return bot, sent


def test_poll_routes_commands_from_approver_only(db, monkeypatch):
    updates = [
        {"update_id": 1, "message": {"text": "/status@relicandriddle_bot", "from": {"id": 555}, "chat": {"id": 555}}},
        {"update_id": 2, "message": {"text": "/pause", "from": {"id": 999}, "chat": {"id": 999}}},
        {"update_id": 3, "message": {"text": "hello", "from": {"id": 555}, "chat": {"id": 555}}},
    ]
    bot, sent = _bot(db, monkeypatch, updates)
    calls = []
    bot.poll_once(lambda c, a: calls.append(c) or "ok")
    assert calls == ["status"] and sent == ["ok"] and bot._offset == 4
    assert not is_paused(db)


def test_poll_applies_approval_click_for_any_pending_run(db, monkeypatch):
    db.create_run("r7", "01_topic")
    db.record_approval("r7", "01_topic")
    click = {"id": "q", "data": "reject:r7", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 3}}
    bot, _ = _bot(db, monkeypatch, [{"update_id": 9, "callback_query": click}])
    bot.poll_once(None)
    assert db.get_approval("r7")["decision"] == "rejected"


def test_command_errors_are_reported_not_raised(db, monkeypatch):
    bot, sent = _bot(db, monkeypatch, [{"update_id": 1, "message": {"text": "/next", "from": {"id": 555}, "chat": {"id": 555}}}])
    bot.poll_once(lambda c, a: (_ for _ in ()).throw(RuntimeError("<boom>")))
    assert "failed" in sent[0] and "<boom>" not in sent[0]


def test_notify_never_raises(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "_api", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert bot.notify("hi") is False


def test_healthcheck_ping_is_optional_and_safe(monkeypatch):
    calls = []
    monkeypatch.setattr(control, "HEALTHCHECK_PING_URL", "")
    monkeypatch.setattr(control.requests, "get", lambda url, timeout: calls.append(url))
    control.ping_healthcheck()
    assert calls == []
    monkeypatch.setattr(control, "HEALTHCHECK_PING_URL", "https://hc-ping.com/abc")
    control.ping_healthcheck("/fail")
    assert calls == ["https://hc-ping.com/abc/fail"]
