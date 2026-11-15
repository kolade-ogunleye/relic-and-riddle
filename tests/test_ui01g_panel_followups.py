"""UI-01g: the notifications panel's follow-ups (operator, 2026-11-14, from the dev bot: "Aren't they supposed to be
updated regularly?") and the UI-01 port review's notes.

A condition item says when it was last checked, never when it was first raised; info leaves the panel after a day
(Notices keeps it, with Restore); "Back online" leaves once Posting or History is opened, or after its time; a page
never holds more items than there are numbers; the carousel preview is drawn inside the review panel, and a screen
asked for from that panel is refused rather than drawn over its video; every face fits the caption; a Redraw that
expires its booking takes the hold's item with it; a carousel notice whose offer is no longer true is written again.
A fake Bot API keeps ids, media and deletions; nothing calls Telegram."""
import itertools
import json
import re
import time
from datetime import timedelta

import pytest
import test_qf73_topic_bank as qf73
import test_qf81_lazy_carousels as qf81
import test_ui01a_notifications_panel as ui01a
from PIL import Image
from test_ui01e_review_panel import a_review, caption, deliver, labels, rows
from test_ui01e_review_panel import make_bot as review_bot
from test_ui01e_review_panel import tap as review_tap
from test_ui01f_repost import make_bot as layout_bot

from src import config, telegram_bot

coord = qf81.coord                   # QF-81's fixture (carousel_desk builds on it)
carousel_desk = ui01a.carousel_desk  # QF-81's coordinator over the fake Bot API
sensitive_db = qf73.sensitive_db     # QF-73's fixtures: a week of carousel slots
carousel_week = qf73.carousel_week
frozen = qf73.frozen

HOUR = 3600.0
T0 = 1_900_000_000.0                 # the simulated clock's start


@pytest.fixture(autouse=True)
def clean_chat(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    monkeypatch.setattr(config, "PUSH_ALERT_SECONDS", 8.0, raising=False)
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10.0, raising=False)
    monkeypatch.setattr(config, "REVIEW_APPROVE_UNDO_LEAD_MINUTES", 15.0, raising=False)
    monkeypatch.setattr(config, "LAYOUT_REPOST_IDLE_HOURS", 30.0, raising=False)
    monkeypatch.setattr(config, "LAYOUT_REPOST_FORCE_HOURS", 40.0, raising=False)
    monkeypatch.setattr(config, "LAYOUT_IDLE_MINUTES", 10.0, raising=False)
    monkeypatch.setattr(telegram_bot, "_video_dimensions", lambda path: {"width": 720, "height": 1280})


def notes():
    from src import notifications
    return notifications


class Clock:
    """The whole process's clock, moved by the test (the panel, its rows and the layout read it)."""

    def __init__(self, monkeypatch, at=T0):
        self.at = at
        monkeypatch.setattr(time, "time", lambda: self.at)

    def __call__(self):
        return self.at


def a_condition(key="cond:empty-queue", section="needs", title="Topic queue is empty", body="Scheduled runs are skipped."):
    return {"key": key, "section": section, "emoji": "📭", "title": title, "subject": "", "body": body, "count": 1,
            "actions": [{"text": "💡 Ideas", "callback_data": "cideas:"}]}


def panel_text(db, chat):
    from src.telegram_layout import slot_message
    return chat.messages[slot_message(db, "notifications")]["text"]


def stamps(text):
    return [int(u) for u in re.findall(r'<tg-time unix="(\d+)"', text)]


def open_keys(db):
    return {i["dedupe_key"] for i in notes().open_items(db)}


def drawn(bot, chat):
    """What the chat shows after the renderer's next round: the pinned panel's text as it reads, and its buttons."""
    from src.telegram_layout import slot_message
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    message = chat.messages[slot_message(bot.db, "notifications")]
    keyboard = json.loads(message.get("reply_markup") or "{}").get("inline_keyboard") or []
    return notes().plain(message["text"]), [b["text"] for row in keyboard for b in row]


def item_page(bot, chat, key):
    """The same, with the newest item of `key` opened on its page (as a tap on its number does)."""
    n = notes()
    item = max((i for i in n.recent(bot.db, limit=200) if i["dedupe_key"] == key), key=lambda i: i["id"])
    n.set_view(bot.db, 1, item["id"], 1, tapped=bot.layout.clock())
    try:
        return drawn(bot, chat)
    finally:
        n.set_view(bot.db, 1, None, 1, tapped=bot.layout.clock())


def quiet_timers(bot):
    bot.layout.timer = lambda *a, **k: type("T", (), {"start": lambda self: None, "daemon": True})()


# ------------------------------------------------------------------ 1. a condition says when it was last checked
def test_a_condition_item_says_when_it_was_last_checked_not_when_it_was_raised(db, monkeypatch):
    """The dev bot's panel showed Thursday on items that were true on Saturday: a condition is derived again every
    20 seconds, and its line now says so."""
    from src import notice_conditions
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [a_condition()])
    bot.layout.tick(force=True)
    raised = int(clock.at)
    clock.at += 50 * HOUR                                  # two days on: the same condition, checked a moment ago
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    n = notes()
    text, _ = n.render(db, clock.at)
    line = next(ln for ln in text.splitlines() if "tg-time" in ln)
    assert "still true · checked" in n.plain(line)
    shown = stamps(line)
    assert len(shown) == 1 and clock.at - 60 <= shown[0] <= clock.at       # the minute of its last check
    assert all(abs(s - raised) > HOUR for s in shown)                       # never the time it was first raised
    item = n.open_items(db)[0]
    n.set_view(db, 1, item["id"], 1, tapped=clock.at)
    page, _ = n.render(db, clock.at)
    assert "still true · checked" in n.plain(page) and all(clock.at - 60 <= s <= clock.at for s in stamps(page))
    assert "still true" in n.plain(panel_text(db, chat))                    # and that is what the chat shows


def test_a_condition_that_could_not_be_checked_claims_nothing(db, monkeypatch):
    """A condition whose check did not run (the conditions could not be derived, or one is still being counted) is
    never called "still true": it says since when it was not checked."""
    from src import notice_conditions
    clock = Clock(monkeypatch)
    bot, _chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [a_condition()])
    bot.layout.tick(force=True)
    checked = int(clock.at)

    def broken(db_):
        raise RuntimeError("the database is busy")
    monkeypatch.setattr(notice_conditions, "current", broken)
    clock.at += 3 * HOUR
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    n = notes()
    text = n.plain(n.render(db, clock.at)[0])
    assert "not checked since" in text and "still true" not in text
    assert [s // 60 for s in stamps(n.render(db, clock.at)[0])] == [checked // 60]
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [{"key": "cond:empty-queue", "keep": True}])
    clock.at += HOUR
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)                             # "keep": still being counted, so not checked either
    assert "not checked since" in n.plain(n.render(db, clock.at)[0])
    assert open_keys(db) == {"cond:empty-queue"}            # and it neither left nor was written again


def test_a_sender_item_keeps_its_own_time(db):
    """Only a condition is re-derived: an item a sender wrote still shows when it was written."""
    n = notes()
    n.post(db, n.NEEDS, "post-failed:1", "Publish failed", "The upload was refused.", emoji="❌", now=T0)
    text, _ = n.render(db, T0 + 2 * HOUR)
    assert stamps(text) == [int(T0)] and "checked" not in n.plain(text)


# ------------------------------------------------------------------ 2. info leaves after about a day
def test_info_leaves_the_panel_after_a_day_and_stays_on_notices_with_restore(db, monkeypatch):
    from src.control import notices_screen
    clock = Clock(monkeypatch)
    bot, _chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    n.post(db, n.INFO, "job:1", "Fetching images done", "12 images.", emoji="🧰")
    n.post(db, n.NEEDS, "post-failed:1", "Publish failed", "The upload was refused.", emoji="❌")
    clock.at += 23 * HOUR
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    assert open_keys(db) == {"job:1", "post-failed:1"}      # not yet
    clock.at += 1.5 * HOUR
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    assert open_keys(db) == {"post-failed:1"}               # the info left; Needs you never leaves by time
    gone = next(i for i in n.recent(db) if i["dedupe_key"] == "job:1")
    assert gone["state"] == "expired"
    text, buttons = notices_screen(db)
    assert "Fetching images done" in text and "left the panel by its time" in text
    restore = [b for row in buttons for b in row if b["text"].startswith("↩️ Restore")]
    assert restore and restore[0]["callback_data"] == f"cnotices:restore {gone['id']}"
    assert n.restore(db, gone["id"]) and "job:1" in open_keys(db)
    clock.at += 23 * HOUR                                   # restored: its day in the panel starts again
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    assert "job:1" in open_keys(db)
    clock.at += 2 * HOUR
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    assert "job:1" not in open_keys(db)


def test_an_info_item_open_on_its_page_when_it_leaves_says_where_it_went(db, monkeypatch):
    """Through the renderer's own round, 25 hours on: the page in the chat says where the item went, and a tap on a
    button of an earlier keyboard is answered in the same words (review 1, note 4: it said "dealt with")."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    n.post(db, n.INFO, "job:1", "Fetching images done", "12 images.", emoji="🧰")
    item = n.open_items(db)[0]
    clock.at += 25 * HOUR
    n.set_view(db, 1, item["id"], 1, tapped=clock.at)       # its page is open when its day ends
    text, buttons = drawn(bot, chat)
    assert "Fetching images done" in text
    assert "moved to 📊 Status › 🗒️ Notices" in text and "dealt with" not in text
    assert buttons == ["← Back to list"]
    bot._notifications_tap({"id": "late", "from": {"id": 7}, "data": f"n:{item['id']}:x",
                            "message": {"message_id": 101, "chat": {"id": 100}}}, None)
    toast = [d.get("text") for m, d in chat.calls if m == "answerCallbackQuery"][-1]
    assert toast == "This is older info: it moved to 📊 Status › 🗒️ Notices."


def test_needs_you_and_condition_items_never_leave_by_time(db, monkeypatch):
    """A guard (it holds on the parent, where nothing left by time): the new clock takes neither."""
    from src import notice_conditions
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [
        a_condition("cond:digest", "info", "Weekly digest ready", "Week 46")])   # a condition, though info
    n.post(db, n.NEEDS, "post-failed:1", "Publish failed", "x", emoji="❌")
    for hours in (13, 25, 400):
        clock.at = T0 + hours * HOUR
        text, _buttons = drawn(bot, chat)
        assert "Publish failed" in text and "Weekly digest ready" in text, hours
    assert open_keys(db) == {"post-failed:1", "cond:digest"}


def test_an_info_item_written_again_since_it_was_chosen_does_not_leave(db, monkeypatch):
    """Review 1, note 1: expire() chose its rows, then closed them by id alone, so an item a sender wrote again in
    between left the panel with its new text. The update asks again that the row is as old as when it was chosen."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    n.post(db, n.INFO, "job:1", "Fetching images done", "12 images.", emoji="🧰")
    clock.at += 25 * HOUR

    def writes_again(item):                                  # a sender writes it between the choice and the update
        n.post(db, n.INFO, "job:1", "Fetching images done", "14 images.", emoji="🧰")
        return False
    monkeypatch.setattr(bot.layout, "_undo_live", writes_again, raising=False)
    text, _buttons = drawn(bot, chat)
    assert "Fetching images done" in text and "14 images." in text      # written a moment ago: it stays


def test_an_approved_item_stays_while_its_undo_has_time_and_keeps_the_decisions_time(db, monkeypatch):
    """ "Undo until 18:45" is a promise: the Approved item stays past its day while that Undo still works. Losing the
    Undo is not news, so the item's time stays the decision's: it does not start a new day, and leaves at once."""
    clock = Clock(monkeypatch)
    bot, _chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    bot.review.clock = clock
    n = notes()
    until = [T0 + 30 * HOUR]
    monkeypatch.setattr(bot.review, "undo_until", lambda run_id: until[0])
    n.post(db, n.INFO, "posted:r1", "Approved", "Posts Sat 19:00.\nUndo until 18:45.", emoji="✅", subject="Petra",
           actions=[{"text": "Undo approval", "callback_data": "rvua:1"}], run="r1", replace=True)
    decided = n.open_items(db)[0]["updated_at"]
    clock.at += 26 * HOUR
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    assert "posted:r1" in open_keys(db)                     # past its day, but its Undo still works
    clock.at = T0 + 30 * HOUR + 60
    bot.review.tick(clock.at)                               # the Undo's time is up: the button and its line go
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)
    assert "posted:r1" not in open_keys(db)
    item = next(i for i in n.recent(db) if i["dedupe_key"] == "posted:r1")
    assert item["state"] == "expired" and item["actions"] == [] and "Undo until" not in item["body"]
    assert item["updated_at"] == decided                    # it did not look new, nor start a new day


# ------------------------------------------------------------------ 3. "Back online"
def _handler(db):
    from src.control import make_command_handler
    return make_command_handler(db, lambda: "Mon 07:00", lambda: "")


def _back_online(db, bot, clock, planned, down_hours):
    from src import layout_repost
    db.set_setting(layout_repost.STOPPED_KEY, json.dumps({"at": clock.at - down_hours * HOUR, "planned": planned}))
    bot.layout.repost.back(None)
    return next(i for i in notes().open_items(db) if i["dedupe_key"] == "service-state")


@pytest.mark.parametrize("command", ["cposts", "posts", "chistory", "history"])
@pytest.mark.parametrize("planned", [True, False])
def test_back_online_leaves_once_posting_or_history_is_opened(db, monkeypatch, command, planned):
    clock = Clock(monkeypatch)
    bot, _chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    item = _back_online(db, bot, clock, planned, 3)
    assert item["title"] == "Back online" and item["section"] == ("info" if planned else "needs")
    handle = _handler(db)
    handle("cstat", "")                                     # another screen is not that look
    handle("qrefresh", "")
    assert "service-state" in open_keys(db)
    handle(command, "")
    assert "service-state" not in open_keys(db)
    assert notes().get(db, item["id"])["state"] == notes().HANDLED


def test_back_online_info_leaves_after_twelve_hours_and_the_needs_you_one_stays(db, monkeypatch):
    """The info "Back online" leaves the panel by itself after 12 hours (sooner than other info: a night). After an
    unplanned outage it is a Needs-you item asking for a look at Posting and History: that one is never taken away
    by time (operator ruling A, 2026-11-14)."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    _back_online(db, bot, clock, True, 3)
    clock.at += 12 * HOUR - 600
    text, _buttons = drawn(bot, chat)
    assert "Back online" in text and "Nothing needs you · 1 info" in text
    clock.at += 1200                                        # 12 hours and 10 minutes after it was written
    text, _buttons = drawn(bot, chat)
    assert "Back online" not in text and "All clear" in text
    _back_online(db, bot, clock, False, 3)
    clock.at += 72 * HOUR
    text, _buttons = drawn(bot, chat)
    assert "1 needs you" in text and "Back online" in text


def test_with_the_switch_off_opening_posting_closes_nothing(db, monkeypatch):
    n = notes()
    n.post(db, n.INFO, "service-state", "Back online", "Was down 3 min.", emoji="🟢", replace=True)
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    _handler(db)("cposts", "")
    assert "service-state" in open_keys(db)


def test_a_restart_inside_a_minute_takes_bot_stopped_out_of_the_panel(db, monkeypatch):
    """A clean stop writes "Bot stopped"; a start inside a minute says nothing new, and on the parent the running
    bot's panel went on saying it was stopped."""
    clock = Clock(monkeypatch)
    bot, _chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    bot.layout.repost.stopping()
    assert next(i for i in notes().open_items(db) if i["dedupe_key"] == "service-state")["title"] == "Bot stopped"
    clock.at += 20
    bot.layout.repost.back(None)
    assert "service-state" not in open_keys(db)


# ------------------------------------------------------------------ 4. numbers and pages
def test_a_page_holds_no_more_items_than_there_are_numbers():
    n = notes()
    assert n.PAGE_ITEMS <= len(n.CIRCLED)


def test_a_larger_page_size_still_gives_every_item_its_own_number(db, monkeypatch):
    """The guard is in the paging itself: were PAGE_ITEMS raised past the ten numbers, a page still holds ten."""
    n = notes()
    monkeypatch.setattr(n, "PAGE_ITEMS", 12)
    ui01a.post_many(db, needs=20, info=30, t0=T0)
    book = n.pages(n.open_items(db), T0 + 2000)
    assert max(len(page) for page in book) <= len(n.CIRCLED) and sum(len(page) for page in book) == 50
    _text, buttons = n.render(db, T0 + 2000)
    assert len(buttons[0]) == 10 and [b["text"] for b in buttons[0]] == list(n.CIRCLED)


def test_fifty_items_number_from_one_on_every_page_and_every_button_opens_its_own_item(db, monkeypatch):
    clock = Clock(monkeypatch, T0 + 5000)
    bot, fake = ui01a.make_bot(db, monkeypatch)
    n = notes()
    ui01a.post_many(db, needs=20, info=30, t0=T0)
    bot.layout.ensure()
    items = n.open_items(db)
    assert len(items) == 50
    book = n.pages(items, clock.at)
    assert len(book) == 10
    seen = []
    for number, page in enumerate(book, 1):
        ui01a.tap(bot, f"np:{number}")
        text = fake.messages[101]["text"]
        markup = json.loads(fake.messages[101]["reply_markup"])["inline_keyboard"]
        assert f"Page {number} of 10" in text
        entries = [ln for ln in n.plain(text).splitlines() if ln and ln[0] in n.CIRCLED]
        assert [ln[0] for ln in entries] == list(n.CIRCLED[:len(page)])       # ① first, on every page
        assert [b["text"] for b in markup[0]] == list(n.CIRCLED[:len(page)])
        for k, button in enumerate(markup[0]):
            item = page[k]
            assert button["callback_data"] == f"n:{item['id']}"
            assert entries[k].endswith(" " + item["title"])                   # the number beside its own item
            ui01a.tap(bot, button["callback_data"])
            opened = fake.messages[101]["text"]
            assert f"<b>{item['title']}</b>" in opened                        # and its button opens that item
            assert sum(f"<b>{other['title']}</b>" in opened for other in items) == 1
            seen.append(item["id"])
            ui01a.tap(bot, f"np:{number}")
    assert sorted(seen) == sorted(i["id"] for i in items)                     # every item, once


# ------------------------------------------------------------------ 5. the carousel preview, inside the review panel
class Now:
    """An executor that runs a job at once (the preview's drawing is an operator job)."""

    def submit(self, work):
        work()


def _a_carousel_review(db, tmp_path, monkeypatch, slides=2):
    from src import carousel_lazy
    from src.control import make_command_handler
    bot, fake = review_bot(db, monkeypatch)
    dash = bot.show_dashboard("🏛️ home", [[{"text": "x", "callback_data": "cdash:"}]])
    root, payload = a_review(db, tmp_path, monkeypatch, "car")
    (root / carousel_lazy.PLAN).write_text("{}", encoding="utf-8")   # planned: drawn at its slot (QF-81)
    mid = deliver(bot, db, root, payload, "car")
    stage = root / carousel_lazy.STAGE
    (stage / "carousel").mkdir(parents=True)
    Image.new("RGB", (20, 20), "green").save(stage / carousel_lazy.SHEET)
    names = [f"carousel_slide_{k:02d}.jpg" for k in range(1, slides + 1)]
    for name in names:
        Image.new("RGB", (20, 20), "green").save(stage / "carousel" / name)
    drawn = []
    record = {"run_dir": str(root), "slides": {"carousel": [{"name": name} for name in names]},
              "alts": {"carousel": [f"Slide {k} shows the gallery." for k in range(1, slides + 1)]}}
    handler = make_command_handler(db, lambda: "Mon 07:00", lambda: "", send_photo=bot.send_photo,
                                   run_actions={"carpreview": lambda run_id: drawn.append(run_id) or dict(record)})
    monkeypatch.setattr(bot, "_background", lambda: Now())
    return bot, fake, mid, dash, handler, drawn, record


def _tap(bot, data, mid, handler):
    return bot.review.tap({"id": "q", "data": data, "from": {"id": 7, "first_name": "Op"},
                           "message": {"message_id": mid, "chat": {"id": 100}}}, handler)


def test_the_carousel_preview_is_drawn_inside_the_review_panel(db, tmp_path, monkeypatch):
    """On the device the preview opened in the dashboard, away from the review it belongs to. It is now the review
    panel's own face: the sheet with the verdict, then each slide with its alt text, one photo a page, from its file
    id after the first time; Posts and Summary lead back to the video. The dashboard is not touched."""
    from src.review_panel import CAPTION_LIMIT, visible_len
    bot, fake, mid, dash, handler, drawn, _record = _a_carousel_review(db, tmp_path, monkeypatch)
    key = db.run_key("car")
    review_tap(bot, f"rvt:{key}:pst", mid)
    button = next(b for row in rows(fake, mid) for b in row if b["text"] == "🖼 Preview carousel")
    assert button["callback_data"] == f"rvx:{key}"
    dashboard_before = dict(fake.messages[dash])
    sends = len([m for m, _ in fake.calls if m.startswith("send")])
    _tap(bot, button["callback_data"], mid, handler)
    assert drawn == ["car"]
    shown = fake.messages[mid]
    assert shown["media"]["type"] == "photo"                                   # in the review panel's own message
    text = caption(fake, mid)
    assert text.startswith("<b>🎬 Review 1 of 1 · Carousel preview 1 of 3</b>")
    assert "Its checks pass" in text and "2 slides" in text and visible_len(text) <= CAPTION_LIMIT
    assert labels(fake, mid) == [["▶", "⏭"], ["Posts", "Summary"]]
    assert ("editMessageMedia", ["photo"]) in fake.uploads
    _tap(bot, f"rvx:{key}:2", mid, handler)
    assert caption(fake, mid).startswith("<b>🎬 Review 1 of 1 · Carousel preview 2 of 3</b>")
    assert "Slide 1 of 2" in caption(fake, mid) and "Alt text (Instagram): Slide 1 shows the gallery." in caption(fake, mid)
    assert drawn == ["car"]                                                    # paging draws nothing again
    uploads = len(fake.uploads)
    _tap(bot, f"rvx:{key}:1", mid, handler)                                    # back to the sheet: from its file id
    assert len(fake.uploads) == uploads and fake.messages[mid]["media"]["media"].startswith("pic-")
    assert fake.messages[dash] == dashboard_before                             # the dashboard is left alone
    assert len([m for m, _ in fake.calls if m.startswith("send")]) == sends    # and nothing is sent
    review_tap(bot, f"rvt:{key}:pst", mid)
    assert fake.messages[mid]["media"]["type"] == "video" and "🖼 Preview carousel" in [b for row in labels(fake, mid) for b in row]


def test_a_preview_that_cannot_be_drawn_says_why_and_keeps_the_video(db, tmp_path, monkeypatch):
    from src.control import make_command_handler
    bot, fake, mid, _dash, _first, _drawn, _record = _a_carousel_review(db, tmp_path, monkeypatch)
    key = db.run_key("car")

    def refuses(run_id):
        raise ValueError("This run has no carousel to preview.")
    handler = make_command_handler(db, lambda: "Mon 07:00", lambda: "", run_actions={"carpreview": refuses})
    review_tap(bot, f"rvt:{key}:pst", mid)
    before = dict(fake.messages[mid])
    _tap(bot, f"rvx:{key}", mid, handler)
    assert "This run has no carousel to preview." in caption(fake, mid)
    assert fake.messages[mid].get("media", before.get("media")) == before.get("media")   # still its video
    assert labels(fake, mid) == [["Posts", "Summary"]]

    def breaks(run_id):
        raise RuntimeError("the renderer stopped")
    handler = make_command_handler(db, lambda: "Mon 07:00", lambda: "", run_actions={"carpreview": breaks})
    _tap(bot, f"rvx:{key}", mid, handler)
    assert "It could not be drawn" in caption(fake, mid) and "try again" in caption(fake, mid)


def test_a_preview_that_never_comes_back_is_given_up_and_a_face_moved_on_is_left(db, tmp_path, monkeypatch):
    from src import review_panel
    bot, fake, mid, _dash, handler, _drawn, _record = _a_carousel_review(db, tmp_path, monkeypatch)
    key = db.run_key("car")
    held = []

    class Later:
        def submit(self, work):
            held.append(work)
    monkeypatch.setattr(bot, "_background", lambda: Later())
    review_tap(bot, f"rvt:{key}:pst", mid)
    _tap(bot, f"rvx:{key}", mid, handler)
    assert "Drawing both copies" in caption(fake, mid) and len(held) == 1
    _tap(bot, f"rvx:{key}", mid, handler)                                      # a second tap starts no second drawing
    assert len(held) == 1
    bot.review.tick(bot.review.clock() + review_panel.PREVIEW_SECONDS + 1)
    assert "was not drawn in time" in caption(fake, mid) and "Drawing both copies" not in caption(fake, mid)
    review_tap(bot, f"rvt:{key}:sum", mid)
    summary = caption(fake, mid)
    held[0]()                                                                  # the drawing ends after the operator left
    assert caption(fake, mid) == summary and fake.messages[mid].get("media", {}).get("type", "video") == "video"


def test_a_screen_asked_for_from_the_review_panel_is_refused_when_the_dashboard_cannot_be_drawn(db, tmp_path,
                                                                                               monkeypatch):
    """The port review's note 1: with no dashboard to draw in (Telegram refused it), an earlier keyboard's Preview
    carousel fell through and drew the preview's photo over the review panel's video."""
    bot, fake, mid, _dash, handler, drawn, _record = _a_carousel_review(db, tmp_path, monkeypatch)
    key = db.run_key("car")
    monkeypatch.setattr(bot, "start_operator_job", lambda title, work, **k: work())
    monkeypatch.setattr(bot, "_typed_panel", lambda: None)                     # the dashboard could not be sent
    bot._process_update({"callback_query": {"id": "cq-car", "from": {"id": 7}, "data": f"ccar:{key}",
                                            "message": {"message_id": mid, "chat": {"id": 100}}}}, handler)
    shown = fake.messages[mid]
    assert shown.get("media", {}).get("type", "video") == "video"              # never the preview's photo
    assert not caption(fake, mid).startswith("🖼 <b>Carousel preview</b>")
    assert caption(fake, mid).startswith("<b>🎬 Review 1 of 1</b>")
    assert "That screen couldn't be opened" in notes().plain(caption(fake, mid))   # and the panel says so
    assert drawn == []


# ------------------------------------------------------------------ 6. every face fits the caption
@pytest.mark.parametrize("long", ["note", "hook", "title", "all"])
def test_the_summary_fits_the_caption_whatever_the_note_the_hook_and_the_title(db, tmp_path, monkeypatch, long):
    from src.notifications import plain
    from src.review_panel import CAPTION_LIMIT, visible_len
    bot, fake = review_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "long")
    # A licensed voice (QF-127's own record): the fixture's Azure voice without a declared tier is not, and QF-127's
    # lede would then replace the verdict this test is about (port 2 of LAND-32).
    (root / "voice.json").write_text(json.dumps({"provider": "elevenlabs", "tier": "creator", "licensed": True}),
                                     encoding="utf-8")
    words =" ".join(f"word{k}" for k in range(400))                           # about 2,700 characters
    if long in ("hook", "all"):
        payload["hook_sentence"] = "Hook " + words
    if long in ("title", "all"):
        payload["title"] = "Title " + words
    db.update_run("long", "pending_approval", script_payload=payload)
    if long in ("note", "all"):
        db.set_setting("regenerating:long", "Note " + words)
    mid = deliver(bot, db, root, payload, "long")
    key = db.run_key("long")
    faces = [caption(fake, mid)]
    for tab in ("scr", "pst", "chk", "sum"):
        review_tap(bot, f"rvt:{key}:{tab}", mid)
        faces.append(caption(fake, mid))
    review_tap(bot, f"rvr:{key}", mid)                                          # the reason step
    faces.append(caption(fake, mid))
    assert all(0 < visible_len(face) <= CAPTION_LIMIT for face in faces), [visible_len(face) for face in faces]
    summary = plain(faces[0])
    assert summary.startswith("🎬 Review 1 of 1\n")
    assert "Ready to approve" in summary and " words" in summary               # its verdict and facts lines are kept
    assert labels(fake, mid)                                                    # and it still has its buttons


def test_a_real_summary_is_not_cut(db, tmp_path, monkeypatch):
    bot, fake = review_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "real")
    mid = deliver(bot, db, root, payload, "real")
    hook = payload.get("hook_sentence") or payload.get("hook")
    assert payload["title"] in notes().plain(caption(fake, mid)) and hook in notes().plain(caption(fake, mid))


# ------------------------------------------------------------------ 7. a Redraw that expires its booking
def test_a_redraw_that_finds_no_slot_left_takes_the_hold_item_with_its_booking(carousel_desk):
    """The port review's note 2: the booking expired, but its item stayed open, still offering Redraw, until it was
    dismissed by hand."""
    c, bot, fake = carousel_desk
    n = notes()
    c.contrast = 2.1
    c.make("r1", qf81.TUE - timedelta(days=3))
    qf81._slot(c, qf81.TUE)
    held = qf81._carousels(c, "r1")[0]
    item = next(i for i in n.open_items(c.db) if i["dedupe_key"] == f"post-failed:{held['id']}")
    c.contrast = None
    qf81._Frozen.now_value = qf81.TUE + timedelta(days=30)                     # long past its window
    ui01a.tap(bot, f"n:{item['id']}:{n.action_ref(item['actions'][0])}", ui01a._redraw_handler(c.db, c))
    assert qf81._carousels(c, "r1")[0]["status"] == "expired"
    assert n.get(c.db, item["id"])["state"] == n.RESOLVED                      # gone with its booking
    assert not [i for i in n.open_items(c.db) if i["dedupe_key"].startswith("post-failed:")]
    assert "no carousel slot left in its window" in ui01a._toasts(fake)[-1]     # and the tap is told why
    assert not n.view(c.db).get("item")                                        # back on the list


def test_a_carredraw_reply_that_does_not_say_its_outcome_keeps_the_item(db, monkeypatch):
    """The port review's note 4, "carredraw" on its own: a handler that answers a Redraw with plain words (no
    notifications.Outcome) has not said it was done, so the hold's item stays open."""
    from src.telegram_bot import SAY_THEIR_OUTCOME
    bot, _fake = ui01a.make_bot(db, monkeypatch)
    db.set_setting("telegram_dashboard_id", "60")
    bot.layout.ensure()
    assert "carredraw" in SAY_THEIR_OUTCOME
    assert bot._item_action_done("carredraw", "5") is False                    # never "ran without an error"
    assert bot._item_action_done("cancelpost", "5") is True                    # as any other action is
    item = ui01a._failure_item(db, bot, [{"text": "🔁 Redraw and post next slot", "callback_data": "carredraw:5"}],
                               key="post-failed:5")
    n = notes()
    ui01a.tap(bot, f"n:{item['id']}:{n.action_ref(item['actions'][0])}",
              lambda action, args: "🔁 The carousel is drawn again." if action == "carredraw" else "")
    assert n.get(db, item["id"])["state"] == "open"


# ------------------------------------------------------------------ 8. a carousel notice whose offer changed
def _two_without_a_slot(carousel_week, frozen, tmp_path, monkeypatch):
    """QF-73b review 2's probe: plain r2 and sensitive r0 both lose Thursday, their only slot, to the newer r5."""
    from src import post_schedule
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    db, monday = carousel_week.db, carousel_week.monday
    tuesday = post_schedule.reel_time((monday + timedelta(days=1)).date()) + timedelta(minutes=1)
    qf73._another(db, tmp_path, "r5", "2", tuesday)
    carousel_week.__dict__.pop("_alert", None)   # the class's own: notifications.say(telegram_bot.notify, ...)
    bot, fake = ui01a.make_bot(db, monkeypatch)
    bot.layout.ensure()
    carousel_week.telegram_bot = bot
    qf73._passes(carousel_week, frozen, tuesday, 2)
    return db, tuesday, fake


def _booking_item(db, run_id):
    post = qf73._carousel(db, run_id)
    return next((i for i in notes().open_items(db) if i["dedupe_key"] == f"post-failed:{post['id']}"), None)


def test_a_carousel_notice_is_written_again_when_the_slot_it_offered_is_taken(carousel_week, frozen, tmp_path,
                                                                              monkeypatch):
    """Both notices offered "Post anyway: it posts at Thu 09 May". Once r2 took that slot, r0's open notice went on
    offering it (QF-73b review 2, note 1); a tap was refused truthfully, but the panel said something untrue."""
    db, tuesday, fake = _two_without_a_slot(carousel_week, frozen, tmp_path, monkeypatch)
    first = _booking_item(db, "r0")
    assert first and "Carousel without a slot" in first["title"] and "Post anyway: it posts at Thu 09 May" in first["body"]
    assert "Post anyway: it posts at Thu 09 May" in _booking_item(db, "r2")["body"]
    writes = notes().revision(db)
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=1), 2)
    assert notes().revision(db) == writes                                       # unchanged: not written again
    reply = carousel_week.post_carousel_anyway(qf73._carousel(db, "r2")["id"])
    assert reply.done and "Thu 09 May" in reply
    sends = len(fake.sends())
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=2), 1)
    after = _booking_item(db, "r0")
    assert after["id"] == first["id"]                                           # the same item, written again
    assert "it posts at Thu 09 May" not in after["body"] and "Post anyway can't give it a slot" in after["body"]
    assert [a["callback_data"].partition(":")[0] for a in after["actions"]] == ["carexpire"]   # no Post anyway
    assert len(fake.sends()) == sends                                           # in place: no message, no second ring
    writes = notes().revision(db)
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=3), 2)
    assert notes().revision(db) == writes


def test_a_dismissed_carousel_notice_is_not_brought_back_and_the_earlier_chat_is_told_once(carousel_week, frozen,
                                                                                           tmp_path, monkeypatch):
    db, tuesday, _fake = _two_without_a_slot(carousel_week, frozen, tmp_path, monkeypatch)
    n = notes()
    n.close(db, _booking_item(db, "r0")["id"], n.DISMISSED)
    carousel_week.post_carousel_anyway(qf73._carousel(db, "r2")["id"])
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=2), 1)
    assert _booking_item(db, "r0") is None                                      # dismissed stays dismissed
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    carousel_week.alerts = []
    carousel_week._alert = lambda text, buttons=None, group=None, **kw: carousel_week.alerts.append(text)
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=3), 2)
    assert carousel_week.alerts == []                                           # the switch off: said once, as before


# ------------------------------------------------------------------ 9. 48 hours of the panel
def test_over_48_hours_nothing_stays_in_the_panel_past_its_rule(db, monkeypatch):
    """The panel's whole clock: two days of the renderer's rounds, ten minutes apart. A condition always says it was
    checked within the last two minutes; no info item is older than a day (the info "Back online" half a day) unless
    its Undo still works; every Needs-you item is still there at the end; what left is on Notices."""
    from src import notice_conditions
    from src.telegram_layout import slot_message
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    bot.review.clock = clock
    n = notes()
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [
        a_condition(), a_condition("cond:digest", "info", "Weekly digest ready", "Week 46")])
    undo_ends = T0 + 30 * HOUR
    monkeypatch.setattr(bot.review, "undo_until", lambda run_id: undo_ends)
    n.post(db, n.NEEDS, "post-failed:1", "Publish failed", "The upload was refused.", emoji="❌")
    n.post(db, n.NEEDS, "fail:gone", "Run failed", "No track long enough.", emoji="❌")
    n.post(db, n.INFO, "job:1", "Fetching images done", "12 images.", emoji="🧰")
    n.post(db, n.INFO, "old-messages", "Older messages stay", "Clear history once.", emoji="🧹", replace=True)
    n.post(db, n.INFO, "rejected:r9", "Rejected", "Nothing was posted. The topic is set aside.", emoji="❌", run="r9")
    n.post(db, n.INFO, "posted:r1", "Approved", "Posts Sat 19:00.\nUndo until 18:45.", emoji="✅", subject="Petra",
           actions=[{"text": "Undo approval", "callback_data": "rvua:1"}], run="r1", replace=True)
    _back_online(db, bot, clock, True, 3)
    step, later_posted, left_at = 600, False, {}
    was_open = open_keys(db)
    for k in range(1, 48 * 6 + 1):
        clock.at = T0 + k * step
        if not later_posted and clock.at >= T0 + 30 * HOUR:
            n.post(db, n.INFO, "job:2", "Finding music done", "3 tracks.", emoji="🧰")
            later_posted = True
        bot.layout._heartbeat = 0.0
        bot.layout.tick()
        clock.at += 3                                       # the renderer's next rounds (it ticks every second):
        bot.layout.tick()                                   # what the round wrote is drawn after the 2 s debounce
        now_open = open_keys(db)
        for key in was_open - now_open:
            left_at[key] = (clock.at - T0) / HOUR
        was_open = now_open
        for item in n.open_items(db):
            key, age = item["dedupe_key"], clock.at - item["updated_at"]
            if item["section"] == n.NEEDS or key.startswith("cond:"):
                continue
            if key == "posted:r1" and clock.at < undo_ends + 60 + step:
                continue                                    # its Undo still works (and one round to take it away)
            limit = (12 if key == "service-state" else 24) * HOUR
            assert age < limit + step, (key, age / HOUR)
        drawn = chat.messages[slot_message(db, "notifications")]["text"]
        checked = [ln for ln in drawn.splitlines() if "checked" in n.plain(ln)]
        assert len(checked) == 2 and all("still true · checked" in n.plain(ln) for ln in checked), (k, checked)
        assert all(clock.at - 120 <= s <= clock.at for ln in checked for s in stamps(ln)), (k, checked)
    assert open_keys(db) == {"post-failed:1", "fail:gone", "cond:empty-queue", "cond:digest", "job:2"}
    assert left_at["service-state"] == pytest.approx(12, abs=0.2)
    for key in ("job:1", "old-messages", "rejected:r9"):
        assert left_at[key] == pytest.approx(24, abs=0.2), key
    assert left_at["posted:r1"] == pytest.approx(30, abs=0.4)                   # with its Undo, not before
    states = {i["dedupe_key"]: i["state"] for i in n.recent(db, limit=50)}
    assert all(states[key] == "expired" for key in left_at)                     # each is on Notices, with Restore
    clock.at = T0 + 54.2 * HOUR
    bot.layout._heartbeat = 0.0
    bot.layout.tick()
    assert "job:2" not in open_keys(db)                                         # the later one leaves a day after it came


# ====================================================================== round 2 (review 1): a condition that ended
# ------------------------------------------------------------------ 10. "Bot stopped" and "Back online", in sequence
REQUEST = "🗓️ Posting and 📜 History"
STILL = "Still to check after the earlier outage"


def test_an_outage_a_clean_stop_and_a_start_inside_a_minute_says_back_online_and_what_to_check(db, monkeypatch):
    """Review 1, blocking 3a: an unplanned outage left the Needs-you "Back online: check Posting and History"; a clean
    stop (a release install) wrote "Bot stopped" over it, and a start inside a minute left that in the running bot's
    panel, under Needs you, for good. Now the stop keeps the request, and the start says the bot is back with it."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    _back_online(db, bot, clock, False, 3)
    text, buttons = item_page(bot, chat, "service-state")
    assert "1 needs you" in text and "Back online" in text and REQUEST in text and "🗓️ Posting" in buttons
    clock.at += HOUR
    bot.layout.repost.stopping()
    assert n.pending_pushes(db) == []                       # a clean stop rings nothing
    text, buttons = item_page(bot, chat, "service-state")
    assert "Bot stopped" in text and "Buttons work again when it is back" in text
    assert STILL in text and REQUEST in text                # the unanswered request is not written over
    assert "1 needs you" in text and "🗓️ Posting" in buttons
    clock.at += 20
    bot.layout.repost.back(None)
    assert n.pending_pushes(db) == []
    for hours in (0, 1, 72):
        clock.at += hours * HOUR
        text, buttons = item_page(bot, chat, "service-state")
        assert "Bot stopped" not in text, hours             # never in the panel of a bot that is running
        assert "Back online" in text and STILL in text and REQUEST in text, hours
        assert "1 needs you" in text and "🗓️ Posting" in buttons, hours
    _handler(db)("cposts", "")                              # the look it asked for
    text, _buttons = drawn(bot, chat)
    assert "All clear" in text and "Back online" not in text


def test_an_outage_a_clean_stop_and_a_later_start_keeps_what_to_check(db, monkeypatch):
    """Review 1, blocking 3b: the same, back after five minutes. The item said "Back online · Was down 5 min." under
    Needs you: the request was gone, and an item that asks for nothing never left."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    _back_online(db, bot, clock, False, 3)
    drawn(bot, chat)                                        # the outage's own ring goes
    clock.at += HOUR
    bot.layout.repost.stopping()
    clock.at += 300
    bot.layout.repost.back(None)
    assert n.pending_pushes(db) == []                       # a planned stop and its start ring nothing
    for hours in (0, 13, 72):
        clock.at += hours * HOUR
        text, buttons = item_page(bot, chat, "service-state")
        assert "Back online" in text and "Was down 5 min." in text, hours
        assert STILL in text and REQUEST in text, hours
        assert "1 needs you" in text and "🗓️ Posting" in buttons, hours
    _handler(db)("chistory", "")
    text, _buttons = drawn(bot, chat)
    assert "All clear" in text


def test_bot_stopped_stays_until_the_bot_is_back_whoever_draws_the_panel(db, monkeypatch):
    """Review 1, blocking 3c: with the bot stopped for over 12 hours, another process that drew the panel (a CLI run
    posting a notice) expired "Bot stopped": the one line saying the buttons do not work left while they still did
    not. It leaves only when the bot is back; the 12-hour rule is the info "Back online"'s alone."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    bot.layout.repost.stopping()
    for hours in (13, 30):
        clock.at = T0 + hours * HOUR
        n.post(db, n.INFO, f"job:{hours}", "Fetching images done", "12 images.", emoji="🧰")   # another process
        text, _buttons = drawn(bot, chat)
        assert "Bot stopped" in text and "Buttons work again when it is back" in text, hours
    bot.layout.repost.back(None)
    text, _buttons = drawn(bot, chat)
    assert "Bot stopped" not in text and "Back online" in text and "Was down 30 h 0 min." in text
    clock.at += 12 * HOUR + 600
    text, _buttons = drawn(bot, chat)
    assert "Back online" not in text and "Bot stopped" not in text


@pytest.mark.parametrize("planned, minutes, needs, asks", [(True, 5, False, False), (True, 600, False, False),
                                                          (False, 5, False, True), (False, 30, True, True)])
def test_a_plain_stop_or_outage_and_its_start_say_back_online_as_before(db, monkeypatch, planned, minutes, needs, asks):
    """A guard for the plain sequences (they hold on the parent): a clean stop and its start are info and ask for
    nothing; an outage says what to check, as info up to 15 minutes and as a Needs-you item with its button beyond."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    last_poll = clock.at
    if planned:
        bot.layout.repost.stopping()
        text, _buttons = drawn(bot, chat)
        assert "Bot stopped" in text and "Nothing needs you · 1 info" in text
    clock.at += minutes * 60
    bot.layout.repost.back(None if planned else last_poll)
    text, buttons = item_page(bot, chat, "service-state")
    said = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
    assert "Bot stopped" not in text and "Back online" in text and f"Was down {said}." in text
    assert (f"check {REQUEST}" in text) == asks and STILL not in text
    assert ("1 needs you" in text) == needs and ("🗓️ Posting" in buttons) == needs


def test_a_first_start_and_a_restart_with_nothing_said_leave_the_panel_clear(db, monkeypatch):
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    bot.layout.repost.back(None)                            # the first start ever: nothing to say
    bot.layout.repost.back(clock.at - 20)                   # a crash and a start inside a minute: nothing either
    text, _buttons = drawn(bot, chat)
    assert "All clear" in text


# ------------------------------------------------------------------ 11. the check time, every five minutes
def test_the_check_time_alone_is_drawn_every_five_minutes_and_any_change_at_once(db, monkeypatch):
    """Operator ruling C (2026-11-14): the conditions are still checked every 20 seconds, but the pinned panel is
    written again for the check time alone only once the time it shows is 5 minutes old: 7 edits in half an hour,
    not 30. The time shown is always a real check's; anything else that changes is drawn at once; and a check that
    stops is said within the 2 minutes a check counts for, with the time of the last real check."""
    from src import notice_conditions
    from src.telegram_layout import slot_message
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    held, checks, working = [a_condition()], [], [True]

    def current(db_):
        if not working[0]:
            raise RuntimeError("the database is busy")
        checks.append(clock.at)
        return list(held)
    monkeypatch.setattr(notice_conditions, "current", current)
    bot.layout.tick(force=True)
    mid = slot_message(db, "notifications")

    def edits():
        return [d for m, d in chat.calls if m == "editMessageText" and int(d["message_id"]) == mid]

    def rounds(seconds, every=1):
        """The renderer's rounds (one a second; one every 4 seconds over the half hour, to keep the test short);
        the panel in the chat is read after each."""
        for _ in range(seconds // every):
            clock.at += every
            bot.layout.tick()
            yield chat.messages[mid]["text"]
    before = len(edits())
    for text in rounds(30 * 60, every=4):
        line = next((ln for ln in text.splitlines() if "checked" in n.plain(ln)), "")
        assert "still true · checked" in n.plain(line), n.plain(text)
        shown = stamps(line)[0]
        assert shown in {int(c) // 60 * 60 for c in checks}        # the minute of a real check, never a made-up one
        assert clock.at - shown < 300 + 4                           # never older than five minutes (and a round)
        assert clock.at - checks[-1] <= 20                          # "still true": it was checked a moment ago
    made = edits()[before:]
    assert 5 <= len(made) <= 7, len(made)                           # 06ab669f made 30
    assert len(checks) >= 30 * 60 // 20                             # the checks themselves are as often as before
    times = [stamps(d["text"])[0] for d in made]
    assert all(b - a >= 300 for a, b in itertools.pairwise(times))
    # a real change inside the five minutes is drawn at once (after the 2-second debounce)
    list(rounds(30))
    before = len(edits())
    n.post(db, n.NEEDS, "post-failed:1", "Publish failed", "The upload was refused.", emoji="❌")
    assert "Publish failed" in n.plain(list(rounds(3))[-1]) and len(edits()) == before + 1
    held[0] = dict(a_condition(), title="Topic queue is empty again", count=2)
    assert "Topic queue is empty again" in n.plain(list(rounds(23))[-1])    # within one check round (20 s)
    # a check that stops: "still true" goes within the two minutes a check counts for, with the last real check
    last = checks[-1]
    working[0] = False
    said = [n.plain(text) for text in rounds(123)]
    assert "still true" not in said[-1] and "not checked since" in said[-1]
    assert int(last) // 60 * 60 in stamps(chat.messages[mid]["text"])
    assert all("still true" not in text for text in said[121:])


def test_the_check_time_stays_a_real_checks_when_telegram_refuses_the_edit(db, monkeypatch):
    """The five-minute edit refused by Telegram: rate-limited (the panel keeps the time it had, a real check's, and is
    not tried again before Telegram's own retry_after), answered "message is not modified" (taken as drawn: no edit
    every second after it), and the message gone (the panel is posted again once, with a current check)."""
    from src import notice_conditions
    from src.telegram_layout import slot_message
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    checks = []

    def current(db_):
        checks.append(clock.at)
        return [a_condition()]
    monkeypatch.setattr(notice_conditions, "current", current)
    bot.layout.tick(force=True)
    mid = slot_message(db, "notifications")
    real, refuse, tried = bot._api, [None], []

    def api(method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "editMessageText" and int(data["message_id"]) == mid and refuse[0]:
            tried.append(clock.at)
            if refuse[0] == "limit":
                raise telegram_bot.TelegramRateLimited(method, 600, "Too Many Requests: retry after 600")
            if refuse[0] == "same":
                return {"message_not_modified": True}       # what TelegramApprovalBot._api returns for that answer
            raise RuntimeError("Telegram editMessageText failed: HTTP 400 Bad Request: message to edit not found")
        return real(method, timeout, **kw)
    monkeypatch.setattr(bot, "_api", api)

    def rounds(seconds):
        for _ in range(seconds // 5):
            clock.at += 5
            bot.layout.tick()

    def shown(message):
        line = next((ln for ln in chat.messages[message]["text"].splitlines() if "checked" in n.plain(ln)), "")
        assert "still true · checked" in n.plain(line), n.plain(chat.messages[message]["text"])
        return stamps(line)[0]
    first = shown(mid)
    refuse[0] = "limit"
    rounds(16 * 60)
    assert len(tried) == 2 and tried[1] - tried[0] >= 600   # at five minutes, then not before Telegram's retry_after
    assert shown(mid) == first                              # what the chat has is still the time of a real check
    refuse[0] = None
    rounds(10 * 60)
    assert clock.at - shown(mid) < 300 + 5 and shown(mid) in {int(c) // 60 * 60 for c in checks}
    tried.clear()
    refuse[0] = "same"
    rounds(11 * 60)
    assert 1 <= len(tried) <= 3                             # taken as drawn: one try a five minutes, not one a second
    tried.clear()
    refuse[0] = "gone"
    chat.deleted.add(mid)                                   # the message is no longer in the chat
    rounds(6 * 60)
    again = slot_message(db, "notifications")
    assert len(tried) == 1 and again != mid                 # posted again once, in order (UI-01f)
    assert clock.at - shown(again) < 300 + 5
    assert len([m for m in chat.live() if "checked" in n.plain(chat.messages[m].get("text") or "")]) == 1


# ------------------------------------------------------------------ 12. a carousel notice whose condition ended
def test_a_no_slot_notice_is_withdrawn_when_the_plan_gives_the_slot_back_and_returns_if_it_is_lost_again(
        carousel_week, frozen, tmp_path, monkeypatch):
    """Review 1, blocking 1: r0 and r2 lose Thursday to the newer r5 and say so. r5's carousel is cancelled, and the
    plan gives r0 Thursday; its Needs-you item went on saying it has no slot, that r5 takes it, that it expires if
    nothing is done, and offering Let it expire for a carousel about to post. It is withdrawn; a later loss raises
    it again."""
    from src.posting_view import carousel_outlook
    db, tuesday, fake = _two_without_a_slot(carousel_week, frozen, tmp_path, monkeypatch)
    bot = carousel_week.telegram_bot
    quiet_timers(bot)
    n = notes()
    r0 = qf73._carousel(db, "r0")
    first = _booking_item(db, "r0")
    text, buttons = item_page(bot, fake, f"post-failed:{r0['id']}")
    assert "Carousel without a slot" in text and "Episode r5" in text and "Let it expire" in buttons
    db.update_scheduled_post(qf73._carousel(db, "r5")["id"], "cancelled")
    later = tuesday + timedelta(hours=1)
    qf73._passes(carousel_week, frozen, later, 1)
    assert r0["id"] in carousel_outlook(db, later)["plan"]                      # the plan gives it a slot again
    text, buttons = item_page(bot, fake, f"post-failed:{r0['id']}")
    assert "This has been dealt with." in text                                  # withdrawn, as resolved
    assert "has no slot" not in text and "Let it expire" not in " ".join(buttons) and buttons == ["← Back to list"]
    assert n.get(db, first["id"])["state"] == n.RESOLVED
    text, _buttons = drawn(bot, fake)
    assert text.count("Carousel without a slot") == 1 and "1 needs you" in text  # r2's alone, which still has none
    kept = qf73._carousel(db, "r0")["detail"]
    assert not {"slot_alert", "spacing_alert", "notice_offer"} & set(kept)
    writes = n.revision(db)
    qf73._passes(carousel_week, frozen, later + timedelta(hours=1), 2)
    assert n.revision(db) == writes and _booking_item(db, "r0") is None         # and it stays withdrawn
    # the slot is lost again (a newer episode's carousel takes Thursday): the notice comes back, as new
    qf73._another(db, tmp_path, "r6", "2", tuesday + timedelta(minutes=2))
    qf73._passes(carousel_week, frozen, later + timedelta(hours=3), 1)
    again = _booking_item(db, "r0")
    assert again and again["id"] != first["id"]
    text, buttons = item_page(bot, fake, f"post-failed:{r0['id']}")
    assert "Carousel without a slot" in text and "Episode r6" in text and "Let it expire" in buttons


def test_with_the_switch_off_a_slot_given_back_changes_nothing_and_nothing_new_is_sent(carousel_week, frozen,
                                                                                         tmp_path, monkeypatch):
    """The earlier chat's path is as before: its notice is said once, a slot given back withdraws nothing, and a
    second loss says nothing again."""
    from src import post_schedule
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    db, monday = carousel_week.db, carousel_week.monday
    tuesday = post_schedule.reel_time((monday + timedelta(days=1)).date()) + timedelta(minutes=1)
    qf73._another(db, tmp_path, "r5", "2", tuesday)
    carousel_week.alerts = []
    carousel_week._alert = lambda text, buttons=None, group=None, **kw: carousel_week.alerts.append(text)
    qf73._passes(carousel_week, frozen, tuesday, 2)
    assert len(carousel_week.alerts) == 2
    raised = qf73._carousel(db, "r0")["detail"].get("slot_alert")
    assert raised
    db.update_scheduled_post(qf73._carousel(db, "r5")["id"], "cancelled")
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=1), 2)
    assert qf73._carousel(db, "r0")["detail"].get("slot_alert") == raised       # nothing withdrawn
    qf73._another(db, tmp_path, "r6", "2", tuesday + timedelta(minutes=2))
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=3), 2)
    assert len(carousel_week.alerts) == 2                                       # said once, as before
    assert notes().open_items(db) == []


def _pass(c, when):
    qf81._Frozen.now_value = when
    c._post_carousel_if_due(when)


def test_a_held_carousel_whose_window_has_passed_no_longer_offers_a_redraw(carousel_desk):
    """Review 1, blocking 2: held at its slot, never redrawn. 7, 14 and 30 days later its item still said "Redraw it
    ...; it then posts in the next carousel slot" with the button, which could only answer "no carousel slot left in
    its window". At the first pass of the posting loop with no slot left in its window the booking expires (the rule
    a Redraw applies) and the item is written again in place: what happened, that it will not post, and no Redraw."""
    c, bot, fake = carousel_desk
    quiet_timers(bot)
    n = notes()
    c.contrast = 2.1
    c.make("r1", qf81.TUE - timedelta(days=3))
    qf81._slot(c, qf81.TUE)
    held = qf81._carousels(c, "r1")[0]
    key = f"post-failed:{held['id']}"
    first = next(i for i in n.open_items(c.db) if i["dedupe_key"] == key)
    writes = n.revision(c.db)
    for hours in (2, 26, 50):                                                   # inside its window: nothing changes
        _pass(c, qf81.TUE + timedelta(hours=hours))
        assert qf81._carousels(c, "r1")[0]["status"] == "failed" and n.revision(c.db) == writes
    text, buttons = item_page(bot, fake, key)
    assert "Carousel held" in text and "Redraw it once the cause is dealt with" in text
    assert "Redraw and post next slot" in buttons
    sends = len(fake.sends())
    from src import post_schedule
    ends = post_schedule.carousel_deadline(post_schedule.carousel_reel(c.db, held)[1])
    assert qf81.TUE + timedelta(days=3.5, hours=1) < ends                       # its last slot passes before its window ends
    for days in (3.5, 7, 14, 30):
        _pass(c, qf81.TUE + timedelta(days=days, hours=1))
        assert qf81._carousels(c, "r1")[0]["status"] == "expired", days
        text, buttons = item_page(bot, fake, key)
        assert "Redraw" not in text and "Redraw" not in " ".join(buttons), days
        assert "next carousel slot" not in text, days
        assert "Carousel not posted" in text and "no carousel slot is left in its window (until " in text, days
        assert f"(until {post_schedule.fmt(ends)})" in text and "ended" not in text, days   # true before that time too
        assert "This carousel will not post." in text and "1 needs you" in text, days
        assert [b.removeprefix("📋 ") for b in buttons] == ["Details", "Dismiss", "← Back to list"], days
        if days == 3.5:
            writes = n.revision(c.db)
    assert n.revision(c.db) == writes                                           # written once, then left
    item = next(i for i in n.open_items(c.db) if i["dedupe_key"] == key)
    assert item["id"] == first["id"]                                            # the same item, in place
    assert len([s for s in fake.sends() if not str(s.get("text", "")).startswith("🔔")]) <= sends   # no message of its own
    # the stale keyboard's Redraw is answered truthfully, and changes nothing
    reply = c.redraw_carousel(str(held["id"]))
    assert "not held any more" in reply and not reply.done


def test_a_held_carousel_dismissed_before_its_window_ends_is_not_brought_back_and_the_switch_off_is_as_before(
        carousel_desk, monkeypatch):
    c, bot, _fake = carousel_desk
    quiet_timers(bot)
    n = notes()
    c.contrast = 2.1
    c.make("r1", qf81.TUE - timedelta(days=3))
    c.make("r2", qf81.TUE - timedelta(days=3))
    qf81._slot(c, qf81.TUE)
    qf81._slot(c, qf81.TUE + timedelta(days=1))
    held = {p["run_id"]: p for p in qf81._carousels(c) if p["status"] == "failed"}
    assert set(held) == {"r1", "r2"}
    for item in n.open_items(c.db):
        if item["dedupe_key"] == f"post-failed:{held['r1']['id']}":
            n.close(c.db, item["id"], n.DISMISSED)
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    _pass(c, qf81.TUE + timedelta(days=14, hours=1))
    assert {p["status"] for p in qf81._carousels(c)} == {"failed"}              # the switch off: exactly as before
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    _pass(c, qf81.TUE + timedelta(days=14, hours=2))
    assert {p["status"] for p in qf81._carousels(c)} == {"expired"}
    open_now = {i["dedupe_key"]: i for i in n.open_items(c.db)}
    assert f"post-failed:{held['r1']['id']}" not in open_now                    # dismissed stays dismissed
    assert open_now[f"post-failed:{held['r2']['id']}"]["title"] == "Carousel not posted"


def _posting_words(c, run_id):
    """What 🗓 Posting's list and the episode's 📋 Details (cpost) say of its posts."""
    from src.posting_view import posting_episode, posting_panel
    return posting_panel(c.db)[0], posting_episode(c.db, run_id)[0]


@pytest.mark.parametrize("path", ["the posting loop", "a Redraw tap"])
def test_a_held_carousel_expired_before_seven_days_says_why_and_until_when_on_posting_and_details(
        carousel_desk, path):
    """Review 2, blocking 1: the Carousel not posted item's 📋 Details opened "🖼 Carousel · expired (it waited over
    7 days)", and Posting's list said the same, of a carousel held at its slot that expired 3.5 days after its hold
    (its Reel 6 d 13 h before), because no carousel slot was left in its window. Whether the posting loop or a Redraw
    tap finds that, the expiry records why and until when, and every screen says that."""
    from src import post_schedule
    c, bot, fake = carousel_desk
    quiet_timers(bot)
    c.contrast = 2.1
    c.make("r1", qf81.TUE - timedelta(days=3))
    qf81._slot(c, qf81.TUE)
    held = qf81._carousels(c, "r1")[0]
    reel_at = post_schedule.carousel_reel(c.db, held)[1]
    until = f"(until {post_schedule.fmt(post_schedule.carousel_deadline(reel_at))})"
    when = qf81.TUE + timedelta(days=3.5, hours=1)
    assert when - reel_at < timedelta(days=7)                                   # it did not wait 7 days
    c.contrast = None
    if path == "the posting loop":
        _pass(c, when)
        why = "held at its slot, and no carousel slot was left in its window"
        text, buttons = item_page(bot, fake, f"post-failed:{held['id']}")
        assert f"no carousel slot is left in its window {until}" in text and "📋 Details" in buttons
    else:
        qf81._Frozen.now_value = when
        reply = c.redraw_carousel(str(held["id"]))
        assert "no carousel slot left in its window" in reply and not reply.done
        why = "held at its slot, and a Redraw found no carousel slot left in its window"
    assert qf81._carousels(c, "r1")[0]["status"] == "expired"
    listed, details = _posting_words(c, "r1")
    for screen in (listed, details):
        assert "7 days" not in screen
        assert f"🖼 Carousel • expired: {why} {until}" in screen or f"🖼 <b>Carousel</b> · expired: {why} {until}" in screen
    assert "cancelled with the failed slot" not in details                      # nothing was cancelled
    assert details.count(why) == 1                                              # said once, in its status


def test_a_carousel_that_waited_past_its_deadline_keeps_its_words(carousel_desk):
    """The expiry that records no reason (a waiting carousel past its deadline at a slot) says what it always said."""
    from src.posting_view import status_words
    c, _bot, _fake = carousel_desk
    c.make("r1", qf81.TUE - timedelta(days=3))
    post = qf81._carousels(c, "r1")[0]
    c.db.update_scheduled_post(post["id"], "expired", detail=c._spacing_expiry(post, qf81.TUE))   # main's own record
    post = qf81._carousels(c, "r1")[0]
    assert status_words(c.db, post) == "expired (it waited over 7 days)"
    assert "🖼 Carousel • expired (it waited over 7 days)" in _posting_words(c, "r1")[0]


_UNREAD = ("the credit record of 1 of its photos (credits.json beside the photo) cannot be read, and the episode's "
           "provenance record does not hold that photo with a licence, so nothing says what credit its licence asks")
_LACKS = ("the credit of a photo it shows (" + " ".join(f"Monument{k} of the old city walls" for k in range(30))
          + ") lacks its author, which its licence asks for")
# what a redraw does about an unread credit record, as QF-126's _carousel_request says it (a literal: the test also
# runs on the commit before this one)
_RESTORE = ("Restore or correct the photo's credit record (credits.json beside the photo) first; a redraw then credits "
            "the photo from it")


@pytest.mark.parametrize("reason", [_UNREAD, _LACKS], ids=["unread record", "a reason longer than the item"])
def test_a_credit_hold_that_expires_says_its_reason_whole_or_at_a_word_and_no_redraw_advice(carousel_desk, reason):
    """Port review 1 of LAND-32, blocking 3: a QF-126 credit hold expired through _expire_held_carousels was quoted
    with why[:300], cut mid-word ("... first; a re"), and its item kept the hold's Redraw/restore advice beside "This
    carousel will not post" with no Redraw button. Its reason is now said whole when it fits the item, else cut at a
    word with an ellipsis saying the whole is on Details; the advice is said nowhere, Details' note included."""
    import main
    from src import post_schedule
    c, bot, fake = carousel_desk
    quiet_timers(bot)
    c.contrast = 2.1
    c.make("r1", qf81.TUE - timedelta(days=3))
    qf81._slot(c, qf81.TUE)
    held = qf81._carousels(c, "r1")[0]
    # held as _post_photos holds a credit hold a redraw can help (QF-126): its reason, then what a redraw does
    c._hold_carousel(held, main.CarouselCreditHold(f"{reason}. {_RESTORE}"))
    key = f"post-failed:{held['id']}"
    text, buttons = item_page(bot, fake, key)
    assert "Carousel held" in text and "Restore or correct" in text and "Redraw and post next slot" in buttons
    c.contrast = None
    _pass(c, qf81.TUE + timedelta(days=3.5, hours=1))
    assert qf81._carousels(c, "r1")[0]["status"] == "expired"
    text, buttons = item_page(bot, fake, key)
    page = " ".join(text.split())
    assert "Carousel not posted" in page and "This carousel will not post." in page
    assert [b.removeprefix("📋 ") for b in buttons] == ["Details", "Dismiss", "← Back to list"]
    for advice in ("Restore or correct", "a redraw", "A redraw", "Redraw"):   # no advice for a button it lacks
        assert advice not in page, advice
    _listed, details = _posting_words(c, "r1")
    details = notes().plain(details)
    assert reason in " ".join(details.split())                                  # Details has the whole reason
    assert "Restore or correct" not in details and "redraw" not in details.lower()
    folded = page.rsplit("it was held at its slot (", 1)[1]                    # the item's folded text, whole
    said = folded.split("), and no carousel slot is left", 1)[0]
    if reason == _UNREAD:
        assert said == reason                                                   # whole, never "... first; a re"
    else:
        cut, where = said.split("…; ", 1)
        assert where == "the whole reason is on Details"
        assert reason.startswith(cut) and reason[len(cut)] == " "               # it ends at a word, never mid-word
        assert "its Reel and other posts are not affected." in page            # the rest of the item is whole
    ends = post_schedule.carousel_deadline(post_schedule.carousel_reel(c.db, held)[1])
    assert f"(until {post_schedule.fmt(ends)})" in page


# ------------------------------------------------------------------ 12b. the database refuses: nothing is claimed
def _locked(*a, **k):
    import sqlite3
    raise sqlite3.OperationalError("database is locked")


def test_an_undo_that_cannot_be_looked_up_keeps_its_item_and_the_next_round_asks_again(db, monkeypatch, caplog):
    """The lookup of an Approved item's Undo fails (the database is locked): whether the Undo still works is not
    known, so the item stays in the panel with its promise, the failure is logged, and the next round decides."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    n.post(db, n.INFO, "posted:r1", "Approved", "Posts Sat 19:00.\nUndo until 18:45.", emoji="✅", subject="Petra",
           actions=[{"text": "Undo approval", "callback_data": "rvua:1"}], run="r1", replace=True)
    clock.at += 25 * HOUR
    monkeypatch.setattr(bot.layout, "_undo_live", _locked, raising=False)
    with caplog.at_level("WARNING"):
        text, _buttons = drawn(bot, chat)
    assert "Approved" in text and "posted:r1" in open_keys(db)
    assert "Could not look up the Undo" in caplog.text and "database is locked" in caplog.text
    monkeypatch.setattr(bot.layout, "_undo_live", lambda item: False, raising=False)   # read again: its Undo has ended
    text, _buttons = drawn(bot, chat)
    assert "Approved" not in text and "posted:r1" not in open_keys(db)


def test_back_online_that_cannot_be_closed_stays_and_the_screen_is_still_drawn(db, monkeypatch, caplog):
    """Posting is opened while the item cannot be closed (the database is locked): the screen the operator asked for
    is drawn, the item stays open (it is not said to be dealt with), and the next opening closes it."""
    clock = Clock(monkeypatch)
    bot, chat = layout_bot(db, monkeypatch)
    bot.layout.clock = clock
    n = notes()
    _back_online(db, bot, clock, False, 3)
    handle = _handler(db)
    with monkeypatch.context() as m, caplog.at_level("WARNING"):
        m.setattr(n, "service_seen", _locked, raising=False)
        screen = handle("cposts", "")
    assert screen                                                                    # Posting, drawn all the same
    assert "Could not close the Back online item" in caplog.text
    text, _buttons = drawn(bot, chat)
    assert "1 needs you" in text and "Back online" in text
    handle("cposts", "")
    assert "service-state" not in open_keys(db)


def test_a_withdrawal_the_database_refuses_keeps_the_booking_s_record_and_is_done_at_the_next_pass(
        carousel_week, frozen, tmp_path, monkeypatch, caplog):
    """The slot is given back but the item cannot be closed (the database is locked): the booking still says the
    notice was raised (so nothing is forgotten and nothing is raised twice), and the next pass withdraws it."""
    db, tuesday, fake = _two_without_a_slot(carousel_week, frozen, tmp_path, monkeypatch)
    bot = carousel_week.telegram_bot
    quiet_timers(bot)
    n = notes()
    r0 = qf73._carousel(db, "r0")
    db.update_scheduled_post(qf73._carousel(db, "r5")["id"], "cancelled")
    later = tuesday + timedelta(hours=1)
    with monkeypatch.context() as m, caplog.at_level("WARNING"):
        m.setattr(n, "close_key", _locked)
        qf73._passes(carousel_week, frozen, later, 1)
    assert f"Could not withdraw the notice of booking {r0['id']}" in caplog.text
    assert _booking_item(db, "r0") is not None                                  # still open: it could not be closed
    assert {"slot_alert", "notice_offer"} <= set(qf73._carousel(db, "r0")["detail"])   # and the booking still says so
    qf73._passes(carousel_week, frozen, later + timedelta(minutes=1), 1)
    assert _booking_item(db, "r0") is None
    assert not {"slot_alert", "spacing_alert", "notice_offer"} & set(qf73._carousel(db, "r0")["detail"])
    text, _buttons = drawn(bot, fake)
    assert text.count("Carousel without a slot") == 1                           # r2's alone


def test_a_notice_that_cannot_be_read_is_left_as_it_is(carousel_week, frozen, tmp_path, monkeypatch, caplog):
    """Whether a booking's item is still open cannot be read (the database is locked): the pass writes nothing over
    it and brings nothing back; it is logged, and the item in the panel is the one that was there."""
    db, tuesday, _fake = _two_without_a_slot(carousel_week, frozen, tmp_path, monkeypatch)
    bot = carousel_week.telegram_bot
    quiet_timers(bot)
    n = notes()
    before = _booking_item(db, "r0")
    writes = n.revision(db)
    with monkeypatch.context() as m, caplog.at_level("WARNING"):
        m.setattr(n, "has_open", _locked, raising=False)
        qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=1), 1)
    assert "Could not read the notice of booking" in caplog.text
    assert n.revision(db) == writes
    after = _booking_item(db, "r0")
    assert after and (after["id"], after["body"], after["updated_at"]) == (before["id"], before["body"], before["updated_at"])


# ------------------------------------------------------------------ 13. a preview nothing is drawing
def test_a_preview_refused_because_the_job_queue_is_full_says_so_at_once(db, tmp_path, monkeypatch):
    """Review 1, note 2: with the job queue full the drawing was never started, and the face said "Drawing both
    copies ..." for 20 minutes with nothing drawing."""
    bot, fake, mid, _dash, handler, done, _record = _a_carousel_review(db, tmp_path, monkeypatch)
    key = db.run_key("car")
    for k in range(12):                                                         # the queue's limit
        bot.jobs.active[f"job{k}"] = ({"id": f"job{k}", "resource": f"other:{k}", "state": "running"}, None)
    review_tap(bot, f"rvt:{key}:pst", mid)
    _tap(bot, f"rvx:{key}", mid, handler)
    text = notes().plain(caption(fake, mid))
    assert "Drawing both copies" not in text and done == []
    assert "It was not drawn: the job queue is full." in text and "tap 🖼 Preview carousel again" in text
    assert fake.messages[mid].get("media", {}).get("type", "video") == "video"
    assert labels(fake, mid) == [["Posts", "Summary"]]


def test_a_preview_whose_drawing_was_lost_with_a_restart_says_so_at_the_next_round(db, tmp_path, monkeypatch):
    """Review 1, note 3: the job is gone with the process that ran it; the face went on saying "Drawing both copies
    ..." for up to 20 minutes after the restart."""
    from src import telegram_layout
    bot, fake, mid, _dash, handler, _done, _record = _a_carousel_review(db, tmp_path, monkeypatch)
    key = db.run_key("car")
    held = []

    class Later:
        def submit(self, work):
            held.append(work)
    monkeypatch.setattr(bot, "_background", lambda: Later())
    review_tap(bot, f"rvt:{key}:pst", mid)
    _tap(bot, f"rvx:{key}", mid, handler)
    bot.review.tick(bot.review.clock() + 1)
    assert "Drawing both copies" in caption(fake, mid)                          # this process is drawing it
    monkeypatch.setattr(telegram_layout, "PROCESS_IDENTITY", "the-bot-started-again")
    bot.review.tick(bot.review.clock() + 2)
    text = notes().plain(caption(fake, mid))
    assert "Drawing both copies" not in text
    assert "The carousel preview was interrupted: the bot stopped while drawing it." in text
    assert "🖼 Preview carousel" in [b for row in labels(fake, mid) for b in row]
