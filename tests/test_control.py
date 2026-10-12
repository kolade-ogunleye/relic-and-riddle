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


def _image_set(tmp_path, name="03_kailasa_temple", n=6, reviewed=False):
    from PIL import Image
    from src.credits import save_credits
    d = tmp_path / name
    d.mkdir()
    files = []
    for i in range(n):
        Image.new("RGB", (60, 40)).save(d / f"{i:02d}_commons.jpg")
        files.append({"file": f"{i:02d}_commons.jpg", "title": f"File:Img {i}.jpg", "license": "CC BY 4.0"})
    save_credits(d, {"reviewed": reviewed, "images": files})
    return d


def test_images_review_drop_approve_flow(db, tmp_path):
    from src.credits import load_credits
    d = _image_set(tmp_path)
    _image_set(tmp_path, "29_baltic_sea_anomaly", n=2)
    sent = []
    h = make_command_handler(db, lambda: None, lambda: "", send_photo=lambda p, cap, btns: sent.append((p.name, cap, btns)) or True,
                             assets_dir=tmp_path)
    listing = h("images", "")
    assert "1 to review" in listing and "1 short" in listing and "03 — kailasa temple (6)" in listing

    assert h("review", "3") == "Contact sheet sent above."
    name, caption, buttons = sent[-1]
    assert name == "contact_sheet.jpg" or name.endswith(".jpg")
    assert "1. Img 0.jpg" in caption and buttons[0]["callback_data"] == "imgok:03_kailasa_temple"

    assert "Removed 1; 5 images remain" in h("drop", "03 2")
    assert len(load_credits(d)["images"]) == 5
    assert "Approved 5 images" in h("imgok", "03_kailasa_temple")
    assert load_credits(d)["reviewed"] is True


def test_drop_below_minimum_then_approve_refused(db, tmp_path):
    _image_set(tmp_path, n=5)
    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    h("drop", "3 1")
    with pytest.raises(ValueError, match="need 5"):
        h("imgok", "03_kailasa_temple")


def test_unknown_topic_and_bad_drop_usage(db, tmp_path):
    _image_set(tmp_path)
    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    with pytest.raises(ValueError, match="No fetched image set"):
        h("review", "99")
    assert "Usage" in h("drop", "3 two")


def test_approve_button_routes_through_poller(db, monkeypatch, tmp_path):
    click = {"id": "q", "data": "imgok:03_kailasa_temple", "from": {"id": 555}, "message": {"chat": {"id": 555}}}
    bot, sent = _bot(db, monkeypatch, [{"update_id": 5, "callback_query": click}])
    calls = []
    bot.poll_once(lambda c, a: calls.append((c, a)) or "done")
    assert calls == [("imgok", "03_kailasa_temple")] and sent == ["done"]


def test_quota_lines_warn_when_low():
    from src.control import quota_lines
    assert quota_lines(None) == []
    ok = quota_lines({"tier": "starter", "used": 1000, "limit": 30000, "resets": 1794414283})
    assert ok and not ok[0].startswith("⚠️") and "29,000 of 30,000" in ok[0] and "~38 Reels" in ok[0]
    low = quota_lines({"tier": "free", "used": 9000, "limit": 10000, "resets": None})
    assert low[0].startswith("⚠️") and "edge-tts" in low[0]


def test_status_includes_voice_quota(db):
    h = make_command_handler(db, lambda: None, lambda: "", voice_quota=lambda: {"tier": "free", "used": 0, "limit": 10000})
    assert "ElevenLabs (free): 10,000 of 10,000" in h("status", "")


def test_update_command_requires_daemon_and_delegates(db):
    h = make_command_handler(db, lambda: None, lambda: "")
    assert "only be applied while the daemon" in h("upd", "apply")
    h2 = make_command_handler(db, lambda: None, lambda: "", apply_updates=lambda: "applying")
    assert h2("upd", "apply") == "applying"
