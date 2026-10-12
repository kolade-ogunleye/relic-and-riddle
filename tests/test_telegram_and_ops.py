import logging

from src.ops import SecretMasker, _MaskingFilter
from src.telegram_bot import TelegramApprovalBot


def test_wait_never_auto_approves_when_unconfigured(db):
    db.create_run("r1", "01_topic")
    db.record_approval("r1", "01_topic")
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    assert bot.wait_for_decision("r1", timeout_seconds=0.3, poll_interval=0.05) == "timeout"
    assert db.get_approval("r1")["decision"] == "timeout"


def test_wait_picks_up_cli_decision(db):
    db.create_run("r1", "01_topic")
    db.record_approval("r1", "01_topic")
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    assert bot.record_local_decision("r1", "approved")
    assert bot.wait_for_decision("r1", timeout_seconds=5) == "approved"


def test_unauthorized_click_is_ignored(db, monkeypatch):
    db.create_run("r1", "01_topic")
    db.record_approval("r1", "01_topic")
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append(method) or {})
    cq = {"id": "q", "data": "approve:r1", "from": {"id": 999}, "message": {"chat": {"id": 555}, "message_id": 1}}
    assert bot._handle_callback(cq, "r1") is None
    assert db.get_approval("r1")["decision"] == "pending"
    cq["from"]["id"] = 555
    assert bot._handle_callback(cq, "r1") == "approved"


def test_card_escapes_html():
    card = TelegramApprovalBot.format_card_text({"title": "<b>x</b> & <script>", "narration_script": "a<b"}, "r1")
    assert "<script>" not in card and "&lt;script&gt;" in card


def test_masker_hides_telegram_token_in_urls():
    msg = "HTTPSConnectionPool: Max retries exceeded with url: /bot123456:AAH-abcdefghijklmnopqrstuvwxyz_12/sendPhoto"
    assert "abcdefghij" not in SecretMasker().mask(msg)


def test_logging_filter_masks_configured_secret(monkeypatch):
    monkeypatch.setattr("src.config.UPLOAD_POST_API_KEY", "sk_live_supersecretvalue123")
    record = logging.LogRecord("t", logging.INFO, "", 0, "key=%s", ("sk_live_supersecretvalue123",), None)
    _MaskingFilter(SecretMasker()).filter(record)
    assert "supersecret" not in record.getMessage()


def test_placeholder_chat_id_is_not_an_approver():
    from src.config import _approver_ids
    assert _approver_ids("your_telegram_chat_id_here") == set()
    assert _approver_ids("123, 456 ,abc") == {"123", "456"}


def test_health_flags_chat_id_equal_to_bot_id(monkeypatch):
    from src import config
    from src.ops import PreFlightHealthChecker
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-real")
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "k"); monkeypatch.setattr(config, "UPLOAD_POST_USER", "u")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123456")
    ok, msg = PreFlightHealthChecker().check_live_credentials()
    assert not ok and "bot's own ID" in msg
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "987654")
    monkeypatch.setattr(config, "AZURE_SPEECH_KEY", "a" * 20)
    monkeypatch.setattr(config, "AZURE_SPEECH_REGION", "eastus")
    assert PreFlightHealthChecker().check_live_credentials()[0]


def test_health_requires_a_licensed_voice(monkeypatch):
    from src import config
    from src.audio_engine import AudioEngine
    from src.ops import PreFlightHealthChecker
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-real")
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "k"); monkeypatch.setattr(config, "UPLOAD_POST_USER", "u")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "987654")
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k" * 20)
    monkeypatch.setattr(AudioEngine, "voice_quota", lambda self: {"tier": "free"})
    ok, msg = PreFlightHealthChecker().check_live_credentials()
    assert not ok and "licensed voice" in msg
    monkeypatch.setattr(AudioEngine, "voice_quota", lambda self: {"tier": "starter"})
    assert PreFlightHealthChecker().check_live_credentials()[0]


def test_timed_out_approval_can_be_reopened(db):
    db.create_run("r1", "01_topic")
    db.record_approval("r1", "01_topic")
    db.update_approval_decision("r1", "timeout", decided_by="system")
    assert db.reopen_approval("r1")
    assert db.get_approval("r1")["decision"] == "pending"
    db.update_approval_decision("r1", "approved", decided_by="cli")
    assert not db.reopen_approval("r1")  # only timeouts can be reopened
