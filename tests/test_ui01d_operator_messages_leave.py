"""UI-01d: the operator's messages leave once read (design docs/design/telegram-clean-chat-2026-11-11.md §6.6, §6.7;
operator spec: "No operator commands or messages must be permanent").

Plain text that answers nothing, a sticker, a photo nothing asked for and a message sent while the bot was off are
deleted, and the dashboard says what to do; typed commands and the answer to an armed /ask leave too. A fake Bot
API keeps ids and deletions; nothing calls Telegram."""
import time

import pytest

from src import config
from src import notice_conditions as _conditions


@pytest.fixture(autouse=True)
def clean_chat(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)


class FakeTelegram:
    def __init__(self):
        self.next_id, self.calls, self.messages, self.deleted = 100, [], {}, set()

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        self.calls.append((method, data))
        if method == "sendMessage":
            self.next_id += 1
            self.messages[self.next_id] = data
            return {"message_id": self.next_id}
        if method.startswith("editMessage"):
            mid = int(data["message_id"])
            if mid in self.deleted or mid not in self.messages:
                raise RuntimeError("Telegram editMessageText failed: HTTP 400 Bad Request: message to edit not found")
            self.messages[mid] = {**self.messages[mid], **data}
            return {"message_id": mid}
        if method == "deleteMessage":
            self.deleted.add(int(data["message_id"]))
        return True


def make_bot(db, monkeypatch):
    from src import notice_conditions
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    monkeypatch.setattr(bot, "_api", fake)
    monkeypatch.setattr(bot, "_queue_navigation", lambda work: work() or True)
    monkeypatch.setattr(bot, "_delete_soon", bot.delete_message)
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [])
    dash = bot.show_dashboard()
    return bot, fake, dash


def message(mid, sender=7, **content):
    return {"update_id": mid, "message": {"message_id": mid, "chat": {"id": 100}, "from": {"id": sender},
                                          "date": int(time.time()), **content}}


def handler(action, args):
    return f"<b>{action}</b> {args}".strip()


def test_plain_text_that_answers_nothing_is_deleted_and_answered_in_the_dashboard(db, monkeypatch):
    bot, fake, dash = make_bot(db, monkeypatch)
    before = len(fake.calls)
    bot._process_update(message(501, text="hello?"), handler)
    assert 501 in fake.deleted
    assert fake.messages[dash]["text"].startswith("🤔 Type a command, or use the buttons. Help lists every command.")
    assert '"callback_data": "chelp:Everyday"' in fake.messages[dash]["reply_markup"]
    assert not [d for m, d in fake.calls[before:] if m == "sendMessage"]     # nothing new in the chat


@pytest.mark.parametrize("content", [{"sticker": {"file_id": "s"}}, {"photo": [{"file_id": "p", "file_size": 10}]},
                                     {"voice": {"file_id": "v"}}])
def test_a_message_the_bot_has_no_use_for_is_deleted(db, monkeypatch, content):
    bot, fake, dash = make_bot(db, monkeypatch)
    bot._process_update(message(502, **content), handler)
    assert 502 in fake.deleted
    text = fake.messages[dash]["text"]
    assert "Type a command" in text or text.startswith("📎 <b>Photo received</b>")   # a photo: what its caption can do


def test_a_photo_while_text_is_asked_for_says_so(db, monkeypatch):
    bot, fake, dash = make_bot(db, monkeypatch)
    bot._awaiting = ("ask", time.time() + 600)
    bot._process_update(message(503, photo=[{"file_id": "p", "file_size": 10}]), handler)
    assert 503 in fake.deleted and fake.messages[dash]["text"].startswith("That was a photo; type the text instead.")


def test_a_message_sent_while_the_bot_was_off_is_deleted_not_replayed(db, monkeypatch):
    bot, fake, _dash = make_bot(db, monkeypatch)
    old = message(504, text="/queue")
    old["message"]["date"] = int(time.time()) - 3600
    ran = []
    bot._process_update(old, lambda a, b: ran.append(a) or "")
    assert 504 in fake.deleted and ran == []


def test_a_typed_command_and_the_answer_to_an_armed_ask_leave(db, monkeypatch):
    bot, fake, _dash = make_bot(db, monkeypatch)
    monkeypatch.setattr(bot, "_run_command", lambda h, c, a, target=None: None)
    bot._process_update(message(505, text="/queue"), handler)
    bot._awaiting = ("ask", time.time() + 600)
    bot._process_update(message(506, text="why did the Moai run stop?"), handler)
    assert {505, 506} <= fake.deleted


def test_someone_elses_message_is_left_alone(db, monkeypatch):
    bot, fake, _dash = make_bot(db, monkeypatch)
    bot._process_update(message(507, sender=999, text="hi"), handler)
    assert 507 not in fake.deleted


def test_the_switch_off_leaves_plain_text_as_before(db, monkeypatch):
    bot, fake, _dash = make_bot(db, monkeypatch)
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    bot._process_update(message(508, text="hello?"), handler)
    assert 508 not in fake.deleted


# ------------------------------------------------------------------ round 2: uploads leave once read
REAL_CONDITIONS = _conditions.current   # the real conditions, before make_bot stubs them
PHOTO = [{"file_id": "p", "file_size": 10}]
SET_CAPTION = ("/photos topic-1 | source: https://example.org/p | licence: CC BY 4.0 | "
               "licence_url: https://creativecommons.org/licenses/by/4.0/ | author: A. Photographer")


@pytest.mark.parametrize("staged", [True, False])
def test_a_photos_caption_upload_leaves_and_is_answered_in_the_dashboard(db, monkeypatch, staged):
    """Review blocking 1 (design §6.6: uploads are deleted once staged): the photo leaves, and "🖼 Image saved"
    (or why it was not) is drawn in the dashboard, not sent as a new message at the bottom."""
    from src import telegram_images
    bot, fake, dash = make_bot(db, monkeypatch)
    monkeypatch.setattr(bot, "download_image", lambda item: b"jpeg")

    def add(db_, assets, ref, rights, content, by):
        if not staged:
            raise ValueError("no licence URL")
        return "topic-1"
    monkeypatch.setattr(telegram_images, "add_image", add)
    before = len(fake.calls)
    bot._process_update(message(601, photo=PHOTO, caption=SET_CAPTION), handler)
    assert 601 in fake.deleted
    assert not [d for m, d in fake.calls[before:] if m == "sendMessage"]     # nothing new in the chat
    text = fake.messages[dash]["text"]
    assert text.startswith("🖼 Image saved for <code>topic-1</code>" if staged else "⚠️ Image upload failed: no licence URL")


@pytest.mark.parametrize("caption, size, expected", [
    ("/footage", 10, "🎞 Say which topic the clip is for"),
    ("/footage topic-1", 30 * 1048576, "🎞 That clip is 30 MB"),
])
def test_a_footage_clip_leaves_and_is_answered_in_the_dashboard(db, monkeypatch, caption, size, expected):
    bot, fake, dash = make_bot(db, monkeypatch)
    before = len(fake.calls)
    bot._process_update(message(602, video={"file_id": "v", "file_size": size}, caption=caption), handler)
    assert 602 in fake.deleted
    assert not [d for m, d in fake.calls[before:] if m == "sendMessage"]
    assert fake.messages[dash]["text"].startswith(expected)


def test_a_staged_footage_clip_leaves_and_its_receipt_is_in_the_dashboard(db, monkeypatch, tmp_path):
    import subprocess

    from src import (
        control,
        image_upload,  # noqa: F401  (imported first: it binds topic_for at import, never the stand-in below)
    )
    bot, fake, dash = make_bot(db, monkeypatch)
    (tmp_path / "topic-1").mkdir()
    monkeypatch.setattr(control, "_resolve_topic_dir", lambda ref, assets, db=None: tmp_path / "topic-1")
    monkeypatch.setattr("src.telegram_images.topic_for", lambda db_, name: name)
    monkeypatch.setattr(bot, "download_image", lambda item: b"mp4")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": b"video"})())
    real = fake.__call__

    def api(method, timeout=30, **kw):
        return {"file_path": "videos/clip.mp4"} if method == "getFile" else real(method, timeout, **kw)
    monkeypatch.setattr(bot, "_api", api)
    before = len(fake.calls)
    bot._process_update(message(603, video={"file_id": "v", "file_size": 10},
                                caption="/footage topic-1 | source: https://example.org/v | licence: CC BY 4.0 | "
                                        "licence_url: https://creativecommons.org/licenses/by/4.0/"), handler)
    assert (tmp_path / "topic-1" / "footage" / "curated_footage.mp4").is_file()
    assert 603 in fake.deleted
    assert not [d for m, d in fake.calls[before:] if m == "sendMessage"]
    assert fake.messages[dash]["text"].startswith("✅ Video clip received and saved for <b>topic-1</b>.")


def test_a_sticker_while_text_is_asked_for_says_it_was_not_text(db, monkeypatch):
    """Review non-blocking: "That was a photo" read oddly for a sticker."""
    bot, fake, dash = make_bot(db, monkeypatch)
    bot._awaiting = ("ask", time.time() + 600)
    bot._process_update(message(604, voice={"file_id": "v"}), handler)
    assert 604 in fake.deleted and fake.messages[dash]["text"].startswith("That wasn't text; type it instead.")
    bot._process_update(message(605, photo=PHOTO), handler)
    assert 605 in fake.deleted and fake.messages[dash]["text"].startswith("That was a photo; type the text instead.")


def test_messages_sent_while_the_bot_was_off_are_deleted_and_said(db, monkeypatch):
    """Review non-blocking: a deleted message from an outage must not look handled."""
    bot, fake, dash = make_bot(db, monkeypatch)
    for mid in (606, 607):
        old = message(mid, text="/queue")
        old["message"]["date"] = int(time.time()) - 3600
        bot._process_update(old, handler)
    assert {606, 607} <= fake.deleted
    assert fake.messages[dash]["text"].startswith(
        "⌛ 2 messages sent while the bot was off were not acted on; send them again.")


# ------------------------------------------------------------------ the chat stays clean across the main paths
def test_the_main_operator_paths_leave_only_the_layout_in_the_chat(db, monkeypatch, tmp_path):
    """The reviewer's smoke test, with the switch on and the REAL condition items: notices from main.py, a
    posting error, the publish receipt, a run's progress from start to a failure, interrupted runs, a photo
    upload, a clip and plain text. Afterwards the chat holds the pinned panel and the dashboard, and nothing
    else but push alerts waiting for their deletion."""
    import json

    import main
    from src import notice_conditions, telegram_images, telegram_layout
    from src.telegram_bot import PublishReceipt
    bot, fake, _dash = make_bot(db, monkeypatch)
    monkeypatch.setattr(notice_conditions, "current", REAL_CONDITIONS)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(bot, "start_progress_ticker", lambda run_id: None, raising=False)
    monkeypatch.setattr(bot, "download_image", lambda item: b"jpeg")
    monkeypatch.setattr(telegram_images, "add_image", lambda *a: "topic-1")
    bot.layout.timer = lambda *a, **k: type("T", (), {"start": lambda self: None, "daemon": True})()
    bot.layout.tick(force=True)

    coordinator = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coordinator.db, coordinator.telegram_bot = db, bot
    topic = db.list_all_topics()[0]["id"]
    db.create_run("sm1", topic)
    db.update_run("sm1", "producing", script_payload={"title": "Smoke", "topic_id": topic})
    main._notice(coordinator, "🩺 <b>Run not started: a pre-flight check failed</b>\n\n• ffprobe: missing",
                 section="needs", key="preflight", actions=[{"text": "Details", "callback_data": "cstat:"}])
    main.say_posting_error(db, bot, RuntimeError("Graph is down"))
    receipt = PublishReceipt(bot, "sm1", "Smoke", ["facebook", "instagram"])
    receipt.update_platform("facebook", "live", url="https://example.org/fb")
    receipt.update_platform("instagram", "live", url="https://example.org/ig")
    mid = bot.send_progress_card("sm1", "Smoke")
    db.update_run("sm1", "failed", error="RuntimeError: boom")
    bot.finish_progress_card(mid, "Smoke", "sm1", success=False, error="RuntimeError: boom")
    main.say_interrupted(db, bot, ["sm1"])
    bot._process_update(message(701, photo=PHOTO, caption=SET_CAPTION), handler)
    bot._process_update(message(702, video={"file_id": "v", "file_size": 10}, caption="/footage"), handler)
    bot._process_update(message(703, text="hello?"), handler)
    notice_conditions.sync(db)
    bot.layout.tick(force=True)

    alive = {m for m in fake.messages if m not in fake.deleted}
    panel = telegram_layout.slot_message(db, "notifications")
    dashboard = int(db.get_setting("telegram_dashboard_id"))
    waiting_alerts = {int(a["id"]) for a in json.loads(db.get_setting(telegram_layout.ALERTS_KEY) or "[]")}
    assert alive - {panel, dashboard} <= waiting_alerts, [fake.messages[m]["text"][:60] for m in alive - {panel, dashboard}]
    assert panel < dashboard
    fails = [i for i in notice_conditions.notes.open_items(db) if "sm1" in i["dedupe_key"]]
    assert [i["dedupe_key"] for i in fails if i["section"] == "needs"] == ["fail:sm1"]


# ------------------------------------------------------------------ round 3: a typed /photos upload leaves too
@pytest.mark.parametrize("typed", ["/photos {topic} upload", "/photos upload {topic}", "/photos nowhere upload"])
def test_a_typed_photos_upload_leaves_once_read(db, monkeypatch, typed):
    """Review 2 (UI-01d blocking 1): the upload subcommand returned before the common deletion, so the typed command
    stayed for good. Design §6.7: "Every typed /command is deleted once read" (a refused topic too)."""
    bot, fake, dash = make_bot(db, monkeypatch)
    before = set(fake.messages)
    fake.messages[707] = {"text": "typed"}
    bot._process_update(message(707, text=typed.format(topic=db.list_all_topics()[0]["id"])), handler)
    assert 707 in fake.deleted
    assert sorted(set(fake.messages) - before - {707}) == []           # the answer is in the dashboard
    assert dash not in fake.deleted
