"""The runtime review's medium and low items (2026-10-25): taps queued during an outage, /footage saying every
outcome, a stranger's photo, an expired button, the Cancel job confirmation, heading tags, upright photos,
and error text cut before it is escaped."""
import io
import time

from PIL import Image

from src.telegram_bot import TelegramApprovalBot
from src.telegram_presentation import format_html


def _bot(db, calls):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or (
        [] if method == "getUpdates" else {"message_id": 900})
    return bot


def test_a_tap_that_starts_work_is_not_replayed_after_an_outage(db):
    bot = _bot(db, [])
    db.set_setting(bot.LAST_POLL_KEY, f"{time.time() - 2 * 86400:.0f}")      # down for two days
    assert bot._offline_gap()
    assert bot._stale_tap("qrender:4") and bot._stale_tap("fetchimages:x") and bot._stale_tap("postnow:3")
    assert not bot._stale_tap("approve:4") and not bot._stale_tap("cstat:") and not bot._stale_tap("chistory:1")
    assert not bot._offline_gap()                                               # it has polled since


def test_a_queued_render_tap_is_answered_not_run(db):
    calls, ran = [], []
    bot = _bot(db, calls)
    db.set_setting(bot.LAST_POLL_KEY, f"{time.time() - 86400:.0f}")
    old = {"update_id": 5, "callback_query": {"id": "q5", "data": "qrender:4", "from": {"id": 555},
                                              "message": {"message_id": 70, "chat": {"id": 555}}}}
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or (
        [old] if method == "getUpdates" else {"message_id": 900})
    bot.poll_once(lambda a, t: ran.append(a) or "")
    bot.drain()
    assert ran == [] and any(m == "answerCallbackQuery" and "offline" in d.get("text", "") for m, d in calls)


def test_footage_says_why_nothing_was_saved(db):
    calls = []
    bot = _bot(db, calls)
    shown = []
    bot.notify = lambda text, **kw: shown.append(text) or 1
    bot._process_update({"update_id": 7, "message": {"message_id": 3, "date": int(time.time()), "caption": "/footage",
                                                     "video": {"file_id": "v"}, "from": {"id": 555}, "chat": {"id": 555}}}, None)
    bot._process_update({"update_id": 8, "message": {"message_id": 4, "date": int(time.time()), "caption": "/footage 3",
                                                     "video": {"file_id": "v", "file_size": 60 * 1048576},
                                                     "from": {"id": 555}, "chat": {"id": 555}}}, None)
    assert "Say which topic" in shown[0] and "20 MB" in shown[1]


def test_a_strangers_photo_does_not_answer_the_operators_issue(db):
    bot = _bot(db, [])
    bot._awaiting = ("issue", time.time() + 600)
    bot._process_update({"update_id": 9, "message": {"message_id": 5, "date": int(time.time()), "photo": [{"file_id": "p"}],
                                                     "from": {"id": 777}, "chat": {"id": 777}}}, lambda a, t: "")
    assert bot._awaiting and bot._awaiting[0] == "issue"


def test_an_expired_button_opens_the_dashboard_not_an_unknown_command(db):
    calls, ran = [], []
    bot = _bot(db, calls)
    bot._process_update({"update_id": 10, "callback_query": {"id": "q10", "data": "expired:", "from": {"id": 555},
                                                             "message": {"message_id": 70, "chat": {"id": 555}}}},
                        lambda a, t: ran.append(a) or "Unknown command /expired. Send /help.")
    bot.drain()
    sent = [d.get("text", "") for m, d in calls if m in ("sendMessage", "editMessageText")]
    assert ran == [] and not any("Unknown command" in t for t in sent)
    assert any(m == "editMessageText" and d.get("message_id") == 70 for m, d in calls)   # the Dashboard, in the panel


def test_a_heading_whose_tags_close_later_is_not_crossed():
    assert format_html("<i>a\nb</i>") == "<i>a\n\nb</i>"


def test_a_photo_with_a_rotate_flag_is_saved_upright(tmp_path, monkeypatch):
    from src import commons
    im = Image.new("RGB", (400, 300), (200, 30, 30))
    exif = im.getexif()
    exif[274] = 6                                                                # "rotate 90 to show it"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    monkeypatch.setattr(commons, "_get", lambda *a, **k: type("R", (), {"content": buf.getvalue()})())
    dest = tmp_path / "x.jpg"
    commons.download_jpeg("https://example.org/x.jpg", dest)
    assert Image.open(dest).size == (300, 400)                                  # portrait, as it is meant to be seen
