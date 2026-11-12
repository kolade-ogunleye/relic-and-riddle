"""UI-01a: one pinned notifications panel that every sender writes into (operator-approved design of 2026-11-12,
docs/design/telegram-clean-chat-2026-11-11.md §6.1, §8, §9).

A fake Bot API keeps message ids, edits, pins and deletions; nothing here calls Telegram. The panel's wording,
order and buttons are the approved mock-ups' (sentence case wins over the mock-ups, 2026-11-11 ruling)."""
import ast
import json
import re
from pathlib import Path

import pytest

from src import config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_chat(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)   # the parent has no switch
    monkeypatch.setattr(config, "PUSH_ALERT_SECONDS", 8.0, raising=False)


def notes():
    from src import notifications
    return notifications


class FakeTelegram:
    """The Bot API as the panel uses it: ids in sending order, edits of a deleted message refused."""

    def __init__(self):
        self.next_id = 100
        self.calls = []
        self.messages = {}
        self.deleted = set()
        self.pinned = []

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
        if method == "pinChatMessage":
            self.pinned.append(int(data["message_id"]))
            return True
        return True

    def sends(self):
        return [d for m, d in self.calls if m == "sendMessage"]


def make_bot(db, monkeypatch, fake=None):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = fake or FakeTelegram()
    monkeypatch.setattr(bot, "_api", fake)
    monkeypatch.setattr(bot, "_queue_navigation", lambda work: work() or True)
    monkeypatch.setattr(bot, "_delete_soon", bot.delete_message)
    try:   # condition items (UI-01b) are their own tests: here the panel holds only what the test posts
        from src import notice_conditions
        monkeypatch.setattr(notice_conditions, "current", lambda db_: [])
    except ImportError:
        pass
    return bot, fake


def post_many(db, needs=0, info=0, t0=1_000_000.0):
    n = notes()
    for k in range(info):
        n.post(db, n.INFO, f"i{k}", f"Info {k}", "A line of info.", emoji="📮", now=t0 + k)
    for k in range(needs):
        n.post(db, n.NEEDS, f"n{k}", f"Needs {k}", "Something to act on.", emoji="❌", now=t0 + 1000 + k)


# ------------------------------------------------------------------ the header and the empty state
@pytest.mark.parametrize("needs, info, expected", [
    (0, 0, "🔔 <b>All clear</b>"),
    (0, 14, "🔔 Nothing needs you · 14 info"),
    (1, 0, "🔔 <b>1 needs you</b>"),
    (2, 14, "🔔 <b>2 need you</b> · 14 info"),
    (1, 22, "🔔 <b>1 needs you</b> · 22 info"),
])
def test_the_header_counts_with_the_verb_agreeing(needs, info, expected):
    assert notes().header(needs, info) == expected


def test_the_empty_state_says_what_will_happen_and_has_no_keyboard(db):
    text, rows = notes().render(db, now=2_000_000.0)
    assert text.startswith("🔔 <b>All clear</b>")
    assert "Nothing needs you, and there is nothing new." in text
    assert "New items arrive here; the ones that need you ring once." in text
    assert rows == []


# ------------------------------------------------------------------ list pages
@pytest.mark.parametrize("needs, info, page_count", [(2, 14, 4), (3, 9, 3), (1, 22, 5), (1, 5, 2), (1, 8, 2)])
def test_pages_hold_five_items_with_needs_you_first_newest_first(db, needs, info, page_count):
    post_many(db, needs, info)
    n = notes()
    book = n.pages(n.open_items(db), 2_000_000.0)
    assert len(book) == page_count
    flat = [i for page in book for i in page]
    assert all(len(page) <= 5 for page in book)
    assert [i["section"] for i in flat] == ["needs"] * needs + ["info"] * info
    assert [i["title"] for i in flat[:needs]] == [f"Needs {k}" for k in reversed(range(needs))]
    text, _ = n.render(db, now=2_000_000.0)
    assert f"Page 1 of {page_count}" in text


def test_a_page_never_passes_3600_characters_and_an_item_is_never_split(db):
    n = notes()
    long_body = "Word " * 60
    for k in range(9):
        n.post(db, n.INFO, f"long{k}", f"Long item {k}", long_body, now=1_000_000.0 + k)
    for p in range(1, 4):
        n.set_view(db, p)
        text, _ = n.render(db, now=2_000_000.0)
        assert n.tg_len(text) <= n.PAGE_CHARS
        assert len(text) <= n.TEXT_LIMIT
    book = n.pages(n.open_items(db), 2_000_000.0)
    assert sum(len(p) for p in book) == 9


@pytest.mark.parametrize("last", range(1, 13))
def test_the_pager_shows_only_the_arrows_that_lead_somewhere_new(last):
    n = notes()
    for page in range(1, last + 1):
        row = [b["text"] for b in n.pager_row(page, last, lambda p: f"np:{p}")]
        expected = ([("⏮")] if page > 2 else []) + (["◀"] if page > 1 else []) + (["▶"] if page < last else []) \
            + (["⏭"] if page < last - 1 else [])
        assert row == expected
        assert not any(ch.isdigit() for t in row for ch in t)   # the counter is in the text, never a button


def test_the_list_keyboard_is_exactly_numbers_dismiss_all_info_and_the_pager(db):
    post_many(db, needs=2, info=14)
    text, rows = notes().render(db, now=2_000_000.0)
    assert [b["text"] for b in rows[0]] == ["①", "②", "③", "④", "⑤"]
    assert all(b["callback_data"].startswith("n:") for b in rows[0])
    assert rows[1] == [{"text": "Dismiss all info", "callback_data": "nall"}]
    assert [b["text"] for b in rows[2]] == ["▶", "⏭"]       # page 1 of 4: each half the row
    assert len(rows) == 3
    assert "<b>Needs you</b>" in text and "<b>Info</b>" in text
    assert text.index("<b>Needs you</b>") < text.index("<b>Info</b>")


def test_needs_you_only_has_no_dismiss_all_info(db):
    post_many(db, needs=3)
    _, rows = notes().render(db, now=2_000_000.0)
    assert rows == [[{"text": c, "callback_data": rows[0][k]["callback_data"]} for k, c in enumerate("①②③")]]


def test_a_list_item_is_its_heading_two_plain_lines_and_its_time(db):
    n = notes()
    n.post(db, n.NEEDS, "fail:r1", "Render failed", "Stopped at the voiceover. Nothing was posted.\n"
           "Re-render keeps the script and voiceover.\nA third line stays on the item page.",
           emoji="❌", subject="Göbekli Tepe", now=1_000_000.0)
    text, _ = n.render(db, now=1_000_060.0)
    assert "① ❌ <b>Render failed</b> · Göbekli Tepe" in text
    entry = text.split("① ", 1)[1]
    assert "A third line" not in entry
    assert '<tg-time unix="1000000" format="r">' in entry


# ------------------------------------------------------------------ item pages
def test_an_item_page_has_its_actions_dismiss_and_back_to_the_list(db):
    n = notes()
    n.post(db, n.NEEDS, "timeout:r", "Review timed out", "Nothing was posted; the render is kept.", emoji="⏰",
           actions=[{"text": "Resend for review", "callback_data": "resend:4"}], now=1_000_000.0)
    item = n.open_items(db)[0]
    n.set_view(db, 1, item["id"], 1, tapped=1_000_000.0)
    text, rows = n.render(db, now=1_000_010.0)
    assert "<i>Needs you · 1 of 1</i>" in text and "⏰ <b>Review timed out</b>" in text
    assert [b["text"] for b in rows[0]] == ["Resend for review", "Dismiss"]
    ref = n.action_ref({"callback_data": "resend:4"})
    assert rows[0][0]["callback_data"] == f"n:{item['id']}:{ref}" and rows[0][1]["callback_data"] == f"n:{item['id']}:x"
    assert rows[-1] == [{"text": "← Back to list", "callback_data": "np:1"}]
    assert not any("🏠" in b["text"] for row in rows for b in row)      # D1: no home on the panel


def test_at_most_three_actions_a_row(db):
    n = notes()
    acts = [{"text": f"Act {k}", "callback_data": f"cpost:{k}"} for k in range(4)]
    n.post(db, n.NEEDS, "k", "Four things", "", actions=acts, now=1.0)
    item = n.open_items(db)[0]
    n.set_view(db, 1, item["id"], 1, tapped=1.0)
    _, rows = n.render(db, now=2.0)
    assert [len(r) for r in rows] == [3, 2, 1]


def test_a_long_item_folds_into_one_expandable_quote(db):
    n = notes()
    body = "Not approved: one picture's licence can't be confirmed. " + "The source page now says otherwise. " * 20
    n.post(db, n.NEEDS, "gate:r", "Blocked at the release gate", body, emoji="⛔", now=1.0)
    item = n.open_items(db)[0]
    n.set_view(db, 1, item["id"], 1, tapped=1.0)
    text, _ = n.render(db, now=2.0)
    assert text.count("<blockquote expandable>") == 1 and "<blockquote><blockquote" not in text
    assert len(text) <= n.TEXT_LIMIT


def test_an_item_dealt_with_while_open_says_so_and_offers_only_back(db):
    n = notes()
    n.post(db, n.NEEDS, "k", "Posting error", "x", now=1.0)
    item = n.open_items(db)[0]
    n.set_view(db, 2, item["id"], 2, tapped=1.0)
    n.close_key(db, "k")
    text, rows = n.render(db, now=2.0)
    assert "✅ This has been dealt with." in text
    assert rows == [[{"text": "← Back to list", "callback_data": "np:2"}]]


# ------------------------------------------------------------------ de-duplication, pushes, retention
def test_the_same_key_is_one_item_counted_and_rings_once_in_six_hours(db):
    n = notes()
    first = n.post(db, n.NEEDS, "posting-error", "Posting error", "a", now=1000.0)
    second = n.post(db, n.NEEDS, "posting-error", "Posting error", "b", now=2000.0)
    third = n.post(db, n.NEEDS, "posting-error", "Posting error", "c", now=1000.0 + 6 * 3600)
    assert first["push"] and not second["push"] and third["push"]
    items = n.open_items(db)
    assert len(items) == 1 and items[0]["count"] == 3 and items[0]["body"] == "c"
    text, _ = n.render(db, now=1000.0 + 6 * 3600 + 5)
    assert "×3" in text


def test_info_rings_nothing_and_escalation_to_needs_you_rings(db):
    n = notes()
    assert not n.post(db, n.INFO, "k", "Heads up", now=1.0)["push"]
    escalated = n.post(db, n.NEEDS, "k", "Now it needs you", now=2.0)
    assert escalated["push"] and n.open_items(db)[0]["section"] == "needs"


def test_a_key_handled_before_reopens_as_new_and_rings(db):
    n = notes()
    one = n.post(db, n.NEEDS, "fail:r", "Render failed", now=1.0)
    n.close(db, one["id"], n.HANDLED)
    again = n.post(db, n.NEEDS, "fail:r", "Render failed", now=2.0)
    assert again["push"] and again["id"] != one["id"] and n.open_items(db)[0]["count"] == 1


def test_replaceable_keys_replace_the_older_text_without_counting(db):
    n = notes()
    n.post(db, n.INFO, "release", "Installing rc73", "back in about 2 min", now=1.0)
    n.post(db, n.INFO, "release", "rc73 installed", "", now=2.0)
    items = n.open_items(db)
    assert len(items) == 1 and items[0]["title"] == "rc73 installed" and items[0]["count"] == 1


def test_the_panel_keeps_fifty_needs_you_never_dropped_and_ninety_days_of_rows(db):
    n = notes()
    post_many(db, needs=3, info=60)
    items = n.open_items(db)
    assert len(items) == 50 and sum(i["section"] == "needs" for i in items) == 3
    assert {i["title"] for i in items if i["section"] == "info"} == {f"Info {k}" for k in range(13, 60)}
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM notifications WHERE state = 'dropped'").fetchone()[0] == 13
    # 90 days later the closed rows go; open Needs-you items stay.
    n.post(db, n.INFO, "late", "Later", now=1_000_000.0 + 91 * 86400)
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM notifications WHERE state = 'dropped'").fetchone()[0] == 1
    assert sum(i["section"] == "needs" for i in n.open_items(db)) == 3


def test_dismiss_all_info_clears_every_page_keeps_needs_you_and_undoes_for_thirty_seconds(db):
    n = notes()
    post_many(db, needs=1, info=22)
    ids = n.dismiss_all_info(db, now=5000.0)
    assert len(ids) == 22 and [i["section"] for i in n.open_items(db)] == ["needs"]
    text, rows = n.render(db, now=5010.0)
    assert text.startswith("🔔 <b>1 needs you</b> · 22 info dismissed")
    assert rows[1] == [{"text": "↩️ Undo", "callback_data": "nundo"}]
    assert n.undo_dismiss_all(db, now=5020.0) == 22 and len(n.open_items(db)) == 23
    n.dismiss_all_info(db, now=6000.0)
    assert n.undo_dismiss_all(db, now=6031.0) == 0 and len(n.open_items(db)) == 1


# ------------------------------------------------------------------ the bot: senders write items, never messages
def test_a_sender_notice_is_an_item_and_the_panel_is_sent_pinned_and_silent(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    assert bot.notify("❌ <b>Run failed</b>\n\n<b>Petra</b>\nNothing was posted.", group="needs") is True
    sends = fake.sends()
    panel = [d for d in sends if "reply_markup" in d]      # the panel has a keyboard; the alert has none
    assert len(panel) == 1 and panel[0]["disable_notification"] == "true" and panel[0]["text"].startswith("🔔")
    assert fake.pinned == [101]
    from src.telegram_layout import slot_message
    assert slot_message(db, "notifications") == 101
    # The alert is the only other send: one line, with sound, and no buttons.
    alerts = [d for d in sends if d is not panel[0]]
    assert len(alerts) == 1 and alerts[0]["text"] == "🔔 Run failed" and "disable_notification" not in alerts[0]
    assert "reply_markup" not in alerts[0]
    # A second notice edits the panel; nothing is sent for it.
    fake.calls.clear()
    bot.notify("📮 <b>Posted</b> · Sigiriya", group="runs")
    assert fake.sends() == []


def test_the_panel_goes_above_an_existing_dashboard(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    db.set_setting("telegram_dashboard_id", "55")
    fake.messages[55] = {"text": "dashboard"}
    shown = []
    monkeypatch.setattr(bot, "show_dashboard", lambda **kw: shown.append(kw) or db.set_setting("telegram_dashboard_id", "999"))
    bot.notify("⚠️ <b>Publishing accounts</b>\n• Instagram token expired", group="needs")
    assert shown == [{"fresh": True, "rebuild": True}]   # the dashboard is sent again under the panel


def test_the_pin_service_line_is_deleted_when_it_arrives(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    updates = [{"update_id": 1, "message": {"message_id": 102, "chat": {"id": 100}, "from": {"id": 1, "is_bot": True},
                                            "pinned_message": {"message_id": 101}, "date": 4_000_000_000}}]
    real = fake.__call__

    def api(method, timeout=30, **kw):
        return updates if method == "getUpdates" else real(method, timeout, **kw)
    monkeypatch.setattr(bot, "_api", api)
    monkeypatch.setattr(bot.layout, "start", lambda: None)
    bot.poll_once(lambda *a: "")
    assert 102 in fake.deleted


def test_an_alert_is_deleted_after_its_seconds_even_by_another_process(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    from src.telegram_layout import Layout
    clock = [10_000.0]
    bot.layout.clock = lambda: clock[0]
    monkeypatch.setattr("threading.Timer.start", lambda self: None)   # the timer's job is done by the next tick
    bot.notify("⏰ <b>Review timed out</b> · Petra", group="needs")
    alert = max(fake.messages)
    assert alert not in fake.deleted
    clock[0] += 7.9
    bot.layout.delete_due_alerts()
    assert alert not in fake.deleted
    later = Layout(bot)                     # a restart: the deletion was recorded, not kept in memory
    later.clock = lambda: clock[0] + 0.2
    later.delete_due_alerts()
    assert alert in fake.deleted


def test_alerts_coalesce_within_a_minute_and_info_never_rings(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    clock = [20_000.0]
    bot.layout.clock = lambda: clock[0]
    monkeypatch.setattr("threading.Timer.start", lambda self: None)
    bot.notify("📮 <b>Posted</b> · Sigiriya", group="runs")
    assert [d for d in fake.sends() if "reply_markup" not in d] == []      # info rings nothing
    bot.notify("❌ <b>Render failed</b>", group="needs")
    clock[0] += 10
    bot.notify("🧾 <b>Fact sheet not built</b>", group="needs")
    bot.notify("🎬 <b>Review waiting</b>", group="needs")
    alerts = [d["text"] for d in fake.sends() if "reply_markup" not in d]
    assert alerts == ["🔔 Render failed"]
    clock[0] += 60
    bot.layout.tick(force=True)
    alerts = [d["text"] for d in fake.sends() if "reply_markup" not in d]
    assert alerts[1] == "🔔 3 need you · newest: Review waiting" and len(alerts[1]) <= 40


def test_alert_lines_fit_a_lock_screen():
    from src.telegram_layout import alert_text
    one = alert_text([{"id": 1, "title": "Render failed", "subject": "Petra's Treasury: The Tomb With No Name",
                       "updated_at": 1.0}], 1)
    assert one.startswith("🔔 Render failed · Petra") and len(one) <= 40


def test_a_second_process_only_writes_rows_while_the_renderer_holds_the_lease(db, monkeypatch):
    from src import telegram_layout
    assert telegram_layout.acquire(db, "daemon-1")
    bot, fake = make_bot(db, monkeypatch)
    bot.notify("❌ <b>Render failed</b>", group="needs")
    assert fake.calls == []                     # the worker wrote the row; the daemon draws it
    assert len(notes().open_items(db)) == 1
    telegram_layout.release(db, "daemon-1")
    bot.notify("⚠️ <b>Posting error</b>", group="needs")
    assert any(m == "sendMessage" for m, _ in fake.calls)   # nobody holds it: this process draws


def test_the_studio_screens_a_command_draws_still_go_to_the_dashboard(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    bot.notify("🏛️ <b>Dashboard</b>", buttons=[[{"text": "📋 Queue", "callback_data": "qrefresh:"}]],
               record_alert=False, group="studio")
    assert [d["text"] for d in fake.sends()] == ["🏛️ <b>Dashboard</b>"]
    assert notes().open_items(db) == []


# ------------------------------------------------------------------ taps on the panel
TAPS = iter(range(1, 10_000))


def tap(bot, data, handler=None):
    bot._notifications_tap({"id": f"cq{next(TAPS)}", "from": {"id": 7},
                            "message": {"message_id": 101, "chat": {"id": 100}}, "data": data}, handler)


def test_taps_open_an_item_dismiss_it_turn_pages_and_undo_dismiss_all(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    post_many(db, needs=1, info=9)
    bot.layout.ensure()
    n = notes()
    first = n.open_items(db)[0]
    tap(bot, f"n:{first['id']}")
    assert "Needs you · 1 of 1" in fake.messages[101]["text"]
    tap(bot, f"n:{first['id']}:x")
    assert n.get(db, first["id"])["state"] == "dismissed"
    answers = [d["text"] for m, d in fake.calls if m == "answerCallbackQuery"]
    assert "Dismissed" in answers
    tap(bot, "np:2")
    assert "Page 2 of 2" in fake.messages[101]["text"]
    tap(bot, "nall")
    assert "9 info dismissed" in fake.messages[101]["text"].splitlines()[0]
    tap(bot, "nundo")
    assert len(n.open_items(db)) == 9


def test_an_action_runs_in_the_dashboard_a_screen_keeps_the_item_page(db, monkeypatch):
    from src.telegram_layout import slot_message
    bot, _fake = make_bot(db, monkeypatch)
    db.set_setting("telegram_dashboard_id", "60")
    n = notes()
    n.post(db, n.NEEDS, "missed:1", "Missed the slot", "Moved to Fri 18:00.",
           actions=[{"text": "🗓 Posting", "callback_data": "cposts:"},
                    {"text": "Retry", "callback_data": "postsdo:retry 4"}])
    bot.layout.ensure()                         # the panel above, the dashboard sent again under it
    dashboard = int(db.get_setting("telegram_dashboard_id"))
    assert dashboard > slot_message(db, "notifications")
    item = n.open_items(db)[0]
    ran = []
    monkeypatch.setattr(bot, "_process_update", lambda update, handler: ran.append(update["callback_query"]))
    tap(bot, f"n:{item['id']}")
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': 'cposts:'})}")
    assert ran[-1]["data"] == "cposts:" and ran[-1]["message"]["message_id"] == dashboard
    assert n.get(db, item["id"])["state"] == "open" and n.view(db)["item"] == item["id"]
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': 'postsdo:retry 4'})}")
    assert ran[-1]["data"] == "postsdo:retry 4" and ran[-1]["message"]["message_id"] == dashboard
    assert n.get(db, item["id"])["state"] == "handled" and not n.view(db).get("item")


def test_the_panel_returns_to_page_one_after_two_minutes_and_when_needs_you_arrives(db, monkeypatch):
    bot, _fake = make_bot(db, monkeypatch)
    clock = [50_000.0]
    bot.layout.clock = lambda: clock[0]
    n = notes()
    post_many(db, info=12, t0=40_000.0)
    bot.layout.ensure()
    n.set_view(db, 3, tapped=clock[0])
    n.post(db, n.NEEDS, "new", "Review waiting", now=clock[0])
    clock[0] += 5
    bot.layout.draw_notifications(force=True)
    assert n.view(db)["page"] == 1                     # a list page goes to page 1 at once
    item = n.open_items(db)[1]
    n.set_view(db, 1, item["id"], 1, tapped=clock[0])
    n.post(db, n.NEEDS, "new2", "Render failed", now=clock[0])
    bot.layout.draw_notifications(force=True)
    assert n.view(db)["item"] == item["id"]           # an open item page is not moved
    clock[0] += 121
    bot.layout.draw_notifications(force=True)
    assert not n.view(db).get("item") and n.view(db)["page"] == 1


def test_ten_writes_in_a_second_are_one_edit(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    from src import telegram_layout
    clock = [70_000.0]
    bot.layout.clock = lambda: clock[0]
    bot.layout.ensure()
    assert telegram_layout.acquire(db, bot.layout.owner, now=clock[0])
    fake.calls.clear()
    for k in range(10):
        notes().post(db, "info", f"burst{k}", f"Burst {k}", now=clock[0])
        bot.layout.draw_notifications()
    assert not [m for m, _ in fake.calls if m == "editMessageText"]
    clock[0] += 2.5
    bot.layout.draw_notifications()
    bot.layout.draw_notifications()
    assert len([m for m, _ in fake.calls if m == "editMessageText"]) == 1


def test_a_missing_panel_is_posted_again_in_order(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    bot.layout.ensure()
    fake.deleted.add(101)                     # the operator deleted it
    notes().post(db, "info", "k", "Something")
    bot.layout.redraw()   # UI-01b: drawn under the layout lock, the missing panel re-posted once it is released
    from src.telegram_layout import slot_message
    assert slot_message(db, "notifications") not in (None, 101) and fake.pinned[-1] == slot_message(db, "notifications")


# ------------------------------------------------------------------ the senders
def test_the_publish_receipt_is_one_item_updated_in_place(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    from src.telegram_bot import PublishReceipt
    r = PublishReceipt(bot, "run_x", "Sigiriya", ["instagram", "facebook"])
    r.update_platform("instagram", "live", url="https://example.org/i")
    r.update_platform("facebook", "live")
    r.finalize()
    items = notes().open_items(db)
    assert len(items) == 1 and items[0]["dedupe_key"] == "posted:run_x"
    assert (items[0]["emoji"], items[0]["title"], items[0]["subject"]) == ("✅", "Posted", "Sigiriya")
    assert items[0]["section"] == "info" and items[0]["count"] == 1
    assert [d for d in fake.sends() if "Sigiriya" in d["text"] and not d["text"].startswith("🔔")] == []


@pytest.mark.parametrize("text, expected", [
    ("❌ <b>Run failed</b>\n\n<b>Petra</b>\nNothing was posted.", ("❌", "Run failed", "", "<b>Petra</b>\nNothing was posted.")),
    ("📮 <b>Posted</b> · <b>Sigiriya</b>\n✅ Instagram Reel: live", ("📮", "Posted", "Sigiriya", "✅ Instagram Reel: live")),
    ("⚠️ Scheduled posting hit an error: Instagram refused the upload.",
     ("⚠️", "Scheduled posting hit an error", "", "Instagram refused the upload.")),
    ("⛔ <b>Blocked at the release gate</b>, not approved (by you)\n\nNothing was posted.",
     ("⛔", "Blocked at the release gate", "", "not approved (by you)\nNothing was posted.")),
    ("⏰ Missed the 18:00 slot for Sigiriya (the daemon was off or paused). Moved to Sat 18:00.",
     ("⏰", "Missed the 18:00 slot for Sigiriya…", "",
      "Missed the 18:00 slot for Sigiriya (the daemon was off or paused). Moved to Sat 18:00.")),
])
def test_a_senders_message_becomes_title_subject_and_body_with_no_word_lost(text, expected):
    emoji, title, subject, body = notes().parse(text)
    assert (emoji, title, subject, body) == expected
    assert len(title) <= 40


def test_every_main_sender_names_its_section():
    """Design §14.2: an AST scan fails any notice without a section (or a group that maps to one)."""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    handlers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) and node.type.id == "TypeError":
            for sub in ast.walk(node):
                handlers.add(id(sub))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in handlers:
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in ("_alert", "_notice", "notify"):
            continue
        words = {k.arg for k in node.keywords}
        if not words & {"section", "group"} and not (name == "notify" and "buttons" in words and len(node.args) == 1):
            missing.append(node.lineno)
    assert missing == [], missing


def test_proposals_of_one_night_are_one_item(db, monkeypatch):
    from src import notice_conditions
    real = notice_conditions.current
    bot, _fake = make_bot(db, monkeypatch)
    monkeypatch.setattr(notice_conditions, "current", real)   # proposals are a condition item (UI-01b)
    import main
    from src import improve
    monkeypatch.setattr(improve, "proposals", lambda db_, status=None: [{"id": f"p{k}", "title": f"Rule {k}", "status": "ready"}
                                                                         for k in range(3)])
    for k in range(3):
        main.proposal_notice(bot, db, {"id": f"p{k}", "title": f"Rule {k}", "status": "ready"})
    items = [i for i in notes().open_items(db) if "proposal" in i["dedupe_key"]]
    assert len(items) == 1 and items[0]["title"] == "3 improvement proposals"
    assert items[0]["actions"] == [{"text": "🧭 Improvements", "callback_data": "cimprove:"}]


# ------------------------------------------------------------------ Notices, conventions, housekeeping
def test_notices_lists_ninety_days_with_restore_and_alerts_clear(db):
    import time

    from src.control import make_command_handler
    n = notes()
    post_many(db, needs=1, info=2, t0=time.time() - 3600)
    h = make_command_handler(db, lambda: "Mon 13 Oct 07:00", lambda: "")
    text, rows = h("alerts", "clear")
    assert text.startswith("🗒️ <b>Notices</b>") and "2 info dismissed" in text
    restore = [b for row in rows for b in row if b["text"].startswith("↩️ Restore")]
    assert len(restore) == 2 and restore[0]["callback_data"].startswith("cnotices:restore ")
    item_id = int(restore[0]["callback_data"].rsplit(" ", 1)[1])
    text, _ = h("cnotices", f"restore {item_id}")
    assert "Back in the panel" in text and n.get(db, item_id)["state"] == "open"
    _status_text, status_rows = h("cstat", "")
    assert any(b["callback_data"] == "cnotices:" for row in status_rows for b in row)


def test_every_panel_callback_is_registered_and_short(db):
    from src.telegram_ui import PANEL_KINDS, SCREENS, long_labels
    n = notes()
    post_many(db, needs=2, info=14)
    pages_and_items = [n.render(db, now=2_000_000.0)]
    for item in n.open_items(db)[:3]:
        n.set_view(db, 1, item["id"], 1, tapped=2_000_000.0)
        pages_and_items.append(n.render(db, now=2_000_000.0))
    for _, rows in pages_and_items:
        assert long_labels(rows) == []
        for b in (b for row in rows for b in row):
            prefix = b["callback_data"].partition(":")[0]
            assert prefix in PANEL_KINDS or prefix in SCREENS
            assert len(b["callback_data"].encode()) <= 20
    assert "cnotices" in SCREENS and SCREENS["cnote"][2] is None


def test_the_layout_rows_survive_the_card_sweep_and_a_clear(db, monkeypatch):
    bot, _fake = make_bot(db, monkeypatch)
    bot.layout.ensure()
    with db._conn() as conn:
        conn.execute("UPDATE telegram_cards SET created_at = '2000-01-01T00:00:00+00:00'")
    db.sweep_expired_telegram_cards()
    from src.telegram_layout import slot_message
    assert slot_message(db, "notifications") == 101
    assert 101 in bot.clear_protected()


def test_the_switch_restores_the_earlier_chat(db, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    bot, fake = make_bot(db, monkeypatch)
    bot.notify("❌ <b>Run failed</b>", group="needs")
    assert [d["text"] for d in fake.sends()] == ["❌ <b>Run failed</b>"]
    assert notes().open_items(db) == []


def test_settings_are_documented():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^TELEGRAM_CLEAN_CHAT=false$", example, re.MULTILINE)   # off until the on-device acceptance
    assert re.search(r"^PUSH_ALERT_SECONDS=8$", example, re.MULTILINE)
    assert json.dumps(config.TELEGRAM_CLEAN_CHAT) in ("true", "false")


# ------------------------------------------------------------------ round 2: every Needs-you item can be acted on
NEEDS_SENDERS = ("main.py", "src/worker_deadline.py", "src/backup_health.py")


def _needs_calls_without_actions(path):
    """Design §14.2: a Needs-you sender (section or group "needs") that offers no way to act on it. A call inside
    a TypeError fallback repeats the call above it, and one in the switch-off branch of `if
    config.TELEGRAM_CLEAN_CHAT` is the earlier chat's message, kept as it was: neither is counted."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    fallback = {id(sub) for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
                and isinstance(node.type, ast.Name) and node.type.id == "TypeError" for sub in ast.walk(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and any(isinstance(t, ast.Attribute) and t.attr == "TELEGRAM_CLEAN_CHAT"
                                            for t in ast.walk(node.test)):
            fallback |= {id(sub) for branch in node.orelse for sub in ast.walk(branch)}
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in fallback:
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in ("_alert", "_notice", "notify", "say"):
            continue
        words = {k.arg: k.value for k in node.keywords}
        needs = any(isinstance(words.get(w), ast.Constant) and words[w].value == "needs" for w in ("section", "group"))
        positional = len(node.args) >= (3 if name in ("say", "_notice") and isinstance(node.func, ast.Name) else 2)
        if needs and not ({"buttons", "actions"} & set(words) or positional):
            missing.append(f"{path}:{node.lineno}")
    return missing


def test_every_needs_you_sender_offers_its_actions():
    """Operator spec: a notification that needs an action comes with just enough buttons to act on it (§10.2)."""
    assert [line for path in NEEDS_SENDERS for line in _needs_calls_without_actions(path)] == []


def test_a_stand_in_alert_gets_only_what_it_takes(db, monkeypatch):
    """A test's `_alert = alerts.append` (origin/main's QF-43 and illustration tests) still gets the text."""
    from src import notifications
    got = []
    notifications.say(got.append, "❌ Carousel not posted", buttons=None, group="needs", section="needs",
                      key="post-failed:1", actions=[{"text": "Retry", "callback_data": "retrypost:1"}])
    assert got == ["❌ Carousel not posted"]
    seen = []
    notifications.say(lambda text, buttons=None: seen.append((text, buttons)), "⚠️ x",
                      buttons=[{"text": "A", "callback_data": "a:"}], section="needs")
    assert seen == [("⚠️ x", [{"text": "A", "callback_data": "a:"}])]

    def broken(text, buttons=None):
        raise TypeError("a bug inside the sender")
    with pytest.raises(TypeError, match="a bug inside"):   # never mistaken for a signature mismatch
        notifications.say(broken, "x", buttons=None)


def test_a_posting_error_is_one_needs_you_item_whose_button_opens_posting(db, monkeypatch):
    """Flow with the switch on: the post ticker's error -> one item with 🗓️ Posting, drawn in the dashboard."""
    import main
    bot, fake = make_bot(db, monkeypatch)
    bot.show_dashboard("🏛️ home", [[{"text": "x", "callback_data": "cdash:"}]])
    for _ in range(2):   # the same error said again: still one item
        main.say_posting_error(db, bot, RuntimeError("Graph is down"))
    main.say_posting_error(db, bot, RuntimeError("Graph is down again"))
    n = notes()
    item = [i for i in n.open_items(db) if i["dedupe_key"] == "posting-error"]
    assert len(item) == 1 and item[0]["section"] == "needs"
    assert item[0]["actions"] == [{"text": "🗓️ Posting", "callback_data": "cposts:"}]
    assert all(d["text"] != item[0]["title"] for d in fake.sends())       # no message of its own
    ran = []
    monkeypatch.setattr(bot, "_process_update", lambda update, handler: ran.append(update["callback_query"]))
    tap(bot, f"n:{item[0]['id']}")
    tap(bot, f"n:{item[0]['id']}:{n.action_ref({'callback_data': 'cposts:'})}")
    assert ran[-1]["data"] == "cposts:"
    assert ran[-1]["message"]["message_id"] == int(db.get_setting("telegram_dashboard_id"))


def test_a_run_failure_offers_re_render_or_back_to_the_queue_and_details(db, monkeypatch, tmp_path):
    import main
    from src import config as cfg
    bot, _fake = make_bot(db, monkeypatch)
    monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("rf1", topic)
    db.update_run("rf1", "failed", error="boom")
    coordinator = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coordinator.db, coordinator.telegram_bot = db, bot
    main._notice(coordinator, "❌ <b>Run failed</b>\n\nx", section="needs", key="fail:rf1", run="rf1",
                 actions=notes().failure_actions(db, "rf1"))
    item = next(i for i in notes().open_items(db) if i["dedupe_key"] == "fail:rf1")
    key = db.run_key("rf1")
    assert item["actions"] == [{"text": "Back to the queue", "callback_data": f"requeue:{topic}"},
                               {"text": "Details", "callback_data": f"chist:{key}"}]
    (tmp_path / "rf1").mkdir()
    monkeypatch.setattr("src.resume_points.reached", lambda run_dir, run=None: ["script"])
    assert notes().failure_actions(db, "rf1")[0] == {"text": "Re-render", "callback_data": f"rerender:{key}"}


def test_interrupted_runs_are_each_the_runs_own_failure_item(db, monkeypatch):
    import main
    bot, _fake = make_bot(db, monkeypatch)
    topic = db.list_all_topics()[0]["id"]
    for r in ("ri1", "ri2"):
        db.create_run(r, topic)
        db.update_run(r, "failed", error="Interrupted")
    main.say_interrupted(db, bot, ["ri1", "ri2"], "Its worker process crashed (exit 3).")
    main.say_interrupted(db, bot, ["ri1"])      # the start-up clearing says it again: still one item
    got = sorted((i["dedupe_key"], i["title"], i["count"]) for i in notes().open_items(db))
    assert got == [("fail:ri1", "Run interrupted", 1), ("fail:ri2", "Run interrupted", 1)]


def test_an_action_is_addressed_by_what_it_runs_not_its_place(db, monkeypatch):
    """Review non-blocking: a sender that updates an item's actions while its page is open never makes a tap run
    another action than the one shown."""
    bot, _fake = make_bot(db, monkeypatch)
    n = notes()
    n.post(db, n.NEEDS, "fail:r", "Run interrupted", actions=[{"text": "Details", "callback_data": "chist:r"}])
    item = n.open_items(db)[0]
    shown = n.action_ref({"callback_data": "chist:r"})
    n.post(db, n.NEEDS, "fail:r", "Run failed", replace=True,
           actions=[{"text": "Re-render", "callback_data": "rerender:r"}, {"text": "Details", "callback_data": "chist:r"}])
    ran = []
    monkeypatch.setattr(bot, "_dispatch_in_dashboard", lambda cq, callback, handler: ran.append(callback))
    tap(bot, f"n:{item['id']}:{shown}")
    assert ran == ["chist:r"]
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': 'gone:'})}")
    assert ran == ["chist:r"]


def test_shutdown_draws_the_panel_once_more_and_gives_up_the_lease(db, monkeypatch):
    from src import telegram_layout
    bot, fake = make_bot(db, monkeypatch)
    bot.layout.tick(force=True)
    assert telegram_layout.holder(db) == bot.layout.owner
    bot.notify("🔴 <b>Production Daemon Offline</b> • Scheduled runs stopped.", record_alert=False, group="info",
               key="service-state")
    bot.close_layout()
    assert "Production Daemon Offline" in fake.messages[telegram_layout.slot_message(db, "notifications")]["text"]
    assert telegram_layout.holder(db) is None


# ------------------------------------------------------------------ round 3: an item closes only when its action is done
def _failure_item(db, bot, actions, key="fail:r1"):
    n = notes()
    n.say(bot.notify, "❌ <b>Production failed</b> · Petra\n\nNo track long enough.", group="needs", key=key,
          run="r1", replace=True, actions=actions)
    return next(i for i in n.open_items(db) if i["dedupe_key"] == key)


def _toasts(fake):
    return [d.get("text") for m, d in fake.calls if m == "answerCallbackQuery"]


def _a_topic(db, status):
    topic = db.list_all_topics()[0]["id"]
    db.record_topic_status(topic, status)
    return topic


def _a_booking(db, status):
    db.create_run("r1", db.list_all_topics()[0]["id"])
    db.update_run("r1", "failed")   # the failed run its item is about
    return db.add_scheduled_post("r1", "story", None, [], status=status)


def _outcome(text, done):
    from src.notifications import Outcome
    return Outcome(text, done)


def _raises(error):
    def handler(action, args):
        raise error
    return handler


HOME_ROW = [[{"text": "🏠 Dashboard", "callback_data": "cdash:"}]]
REFUSED = [
    ("Re-render", "rerender:r1", lambda db: {},
     lambda db: (lambda a, b: _outcome("🔁 A run is already in progress; Re-render this one when it has finished.",
                                       False)), "A run is already in progress"),
    ("Back to the queue", "requeue:{topic}", lambda db: {"topic": _a_topic(db, "in_production")},
     lambda db: (lambda a, b: ("This item cannot be requeued from its current state.", HOME_ROW)),
     "cannot be requeued"),
    ("Retry", "retrypost:{post}", lambda db: {"post": _a_booking(db, "failed")},
     lambda db: (lambda a, b: "🔁 Not retried: the page refused the upload"), "Not retried"),
    ("Resend for review", "resend:r1", lambda db: {},
     lambda db: _raises(RuntimeError("the video file is missing")), "it failed"),
    ("It posted", "pubok:r1", lambda db: {},
     lambda db: _raises(ValueError("no such run")), "it failed"),
]


@pytest.mark.parametrize("label, callback, setup, handler, why", REFUSED, ids=[r[0] for r in REFUSED])
def test_a_refused_or_failed_action_keeps_its_item_open_saying_why(db, monkeypatch, label, callback, setup, handler,
                                                                     why):
    """Review 2 (UI-01a blocking 1): every action kind's refused path. The item is never lost: it stays open, its
    page says why in the handler's own words, the toast says it, and the dashboard is not redrawn over it."""
    n = notes()
    bot, fake = make_bot(db, monkeypatch)
    dashboard = bot.show_dashboard("🏛️ before the tap", [[{"text": "x", "callback_data": "cdash:"}]])
    callback = callback.format(**setup(db))
    item = _failure_item(db, bot, [{"text": label, "callback_data": callback},
                                   {"text": "Details", "callback_data": "chist:r1"}])
    sends = len(fake.sends())
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': callback})}", handler(db))
    after = n.get(db, item["id"])
    assert after["state"] == "open" and why.lower() in (after["note"] or "").lower()
    assert n.view(db)["item"] == item["id"]                              # its page stays open, saying why
    text, rows = n.render(db)
    assert "Not done: " + n.plain(after["note"])[:30] in n.plain(text)
    assert rows[0][0]["text"] == label
    assert why.lower() in (_toasts(fake)[-1] or "").lower() and len(_toasts(fake)) == 1
    assert len(fake.sends()) == sends                                    # no message of its own
    shown = fake.messages[dashboard]["text"]
    assert shown.startswith("🏛️ before the tap") or why.lower() in shown.lower()   # never drawn over by home


DONE = [
    ("Re-render", "rerender:r1", lambda db: {},
     lambda db: (lambda a, b: _outcome("🔁 Re-rendering Petra from its voiceover.", True)), "Re-render started"),
    ("Back to the queue", "requeue:{topic}", lambda db: {"topic": _a_topic(db, "failed")},
     lambda db: (lambda a, b: "🔄 Topic <b>Petra</b> restored to the queue." if db.requeue_topic(b) else "x"),
     "restored to the queue"),
    ("Retry", "retrypost:{post}", lambda db: {"post": _a_booking(db, "failed")},
     lambda db: (lambda a, b: (db.update_scheduled_post(int(b), "published") or "🔁 Stories retried for Petra: posted.",
                               HOME_ROW)), "retried for Petra"),
]


@pytest.mark.parametrize("label, callback, setup, handler, toast", DONE, ids=[r[0] for r in DONE])
def test_an_action_that_is_done_closes_its_item_with_its_own_toast(db, monkeypatch, label, callback, setup, handler,
                                                                    toast):
    n = notes()
    bot, fake = make_bot(db, monkeypatch)
    bot.show_dashboard("🏛️ home", [[{"text": "x", "callback_data": "cdash:"}]])
    callback = callback.format(**setup(db))
    item = _failure_item(db, bot, [{"text": label, "callback_data": callback}])
    sends = len(fake.sends())
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': callback})}", handler(db))
    assert n.get(db, item["id"])["state"] == "handled" and not n.view(db).get("item")
    assert toast in _toasts(fake)[-1] and len(_toasts(fake)) == 1
    assert len(fake.sends()) == sends


@pytest.mark.parametrize("callback, reply", [("requeue:t1", "↩️ Petra is back in the queue."),
                                             ("resend:r1", "📨 Sending Petra for review again…")])
def test_back_to_the_queue_and_resend_answer_with_a_toast_never_a_message(db, monkeypatch, callback, reply):
    """Review 2 (UI-01a blocking 2): the nudge path sent a separate ringing message for 60 s. The clean chat answers
    a tap with its toast (design §6.1, §9: "No other ring")."""
    n = notes()
    bot, fake = make_bot(db, monkeypatch)
    bot.show_dashboard("🏛️ home", [[{"text": "x", "callback_data": "cdash:"}]])
    item = _failure_item(db, bot, [{"text": "Go", "callback_data": callback}])
    sends = len(fake.sends())
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': callback})}", lambda a, b: reply)
    assert fake.sends()[sends:] == []
    assert _toasts(fake)[-1] == n.plain(reply)


def test_a_long_reply_to_a_tap_is_a_dashboard_line_not_a_message(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    dashboard = bot.show_dashboard("🏛️ home", [[{"text": "x", "callback_data": "cdash:"}]])
    long = "📝 Thanks, noted. " + "The new version reads these words at its script and picture steps. " * 4
    sends = len(fake.sends())
    cq = {"id": "cq-long", "from": {"id": 7}, "data": "whytext:r1 more",
          "message": {"message_id": dashboard, "chat": {"id": 100}}}
    bot._process_update({"callback_query": cq}, lambda a, b: long)
    assert fake.sends()[sends:] == []
    assert fake.messages[dashboard]["text"].startswith("📝 Thanks, noted.")


def test_restore_takes_a_handled_item_back(db):
    n = notes()
    n.post(db, n.NEEDS, "fail:r9", "Render failed", "x", now=1.0)
    item = n.open_items(db)[0]
    n.close(db, item["id"], n.HANDLED)
    assert n.restore(db, item["id"]) and n.get(db, item["id"])["state"] == "open"


def test_action_buttons_carry_no_emoji_and_screen_buttons_keep_theirs(db):
    """Design §6.1: "Action buttons carry no emoji"; a button that opens a screen carries its screen's emoji."""
    n = notes()
    n.post(db, n.NEEDS, "fail:r", "Review timed out", "x", now=1.0,
           actions=[{"text": "📨 Resend for review", "callback_data": "resend:4"},
                    {"text": "🗓️ Posting", "callback_data": "cposts:"},
                    {"text": "✅ It posted", "callback_data": "pubok:4"}])
    item = n.open_items(db)[0]
    n.set_view(db, 1, item["id"], 1, tapped=1.0)
    _, rows = n.render(db, now=2.0)
    assert [b["text"] for b in rows[0]] == ["Resend for review", "🗓️ Posting", "It posted"]


def test_a_sender_writing_the_item_again_clears_the_not_done_line(db):
    n = notes()
    n.post(db, n.NEEDS, "fail:r", "Render failed", "x", now=1.0)
    item = n.open_items(db)[0]
    n.note(db, item["id"], "🔁 A run is already in progress")
    assert n.get(db, item["id"])["note"]
    n.post(db, n.NEEDS, "fail:r", "Render failed", "y", replace=True, now=2.0)
    assert not n.get(db, item["id"])["note"]


# ------------------------------------------------------------------ round 4: nothing outside the panels sends a message
PANEL_SENDERS = {"src/telegram_bot.py", "src/telegram_layout.py", "src/review_panel.py", "src/layout_repost.py"}
SEND_METHODS = ("send", "copy", "forward")   # sendMessage, sendPhoto, sendMediaGroup, copyMessage, forwardMessage ...


def _raw_sends(path):
    """Design §14.2: a Telegram send method called through the Bot API directly (`bot._api("sendMessage", ...)`)."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name != "_api":
            continue
        method = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "method"), None)
        said = method.value if isinstance(method, ast.Constant) and isinstance(method.value, str) else None
        if said is None or said.startswith(SEND_METHODS):   # a method it cannot read is counted too
            found.append(f"{path}:{node.lineno} {said}")
    return found


def test_no_sender_outside_the_panels_calls_a_telegram_send_method():
    """Review 3 (UI-01a blocking 1): the weekly update report sent its own ringing message through bot._api. Only the
    chat writer and the panels' renderers send (design §8, §14.2); every other module writes an item."""
    files = ["main.py"] + sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "src").glob("*.py"))
    assert [hit for path in files if path not in PANEL_SENDERS for hit in _raw_sends(path)] == []


def _plan(applicable):
    from src.maintenance import Update, UpdatePlan
    return UpdatePlan([Update("requests", "2.31.0", "2.32.3", "security", ["CVE-x"])] if applicable else [])


def test_the_weekly_update_report_is_a_needs_you_item_with_apply_updates(db, monkeypatch):
    """Review 3 (UI-01a blocking 1): with something to apply, the report is a Needs-you item whose action is Apply
    updates, never a message of its own; next week's with nothing to apply resolves it and is info."""
    import main
    from src import maintenance
    monkeypatch.setattr(maintenance, "apply_allowed", lambda: True)
    n = notes()
    bot, fake = make_bot(db, monkeypatch)
    bot.layout.ensure()
    sends = len(fake.sends())
    main.say_update_report(bot, _plan(True))
    item = next(i for i in n.open_items(db) if i["dedupe_key"] == main.UPDATES_ITEM_KEY)
    assert item["section"] == "needs" and item["title"].startswith("Weekly update check")
    assert item["actions"] == [{"text": "🧰 Apply updates", "callback_data": "upd:apply"}]
    # no message of its own: only the item's one ring (design §9), which carries no buttons and is deleted
    assert [(d["text"], d.get("reply_markup")) for d in fake.sends()[sends:]] == [("🔔 Weekly update check", None)]
    main.say_update_report(bot, _plan(False))
    assert n.get(db, item["id"])["state"] == "resolved"
    assert [i["section"] for i in n.open_items(db) if i["dedupe_key"] == "maintenance:weekly"] == ["info"]


def test_the_earlier_chat_keeps_its_weekly_update_message_with_apply(db, monkeypatch):
    import main
    from src import maintenance
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    monkeypatch.setattr(maintenance, "apply_allowed", lambda: True)
    bot, fake = make_bot(db, monkeypatch)
    main.say_update_report(bot, _plan(True))
    sent = [d for d in fake.sends() if "Weekly update check" in d["text"]]
    assert len(sent) == 1 and "upd:apply" in sent[0]["reply_markup"]


@pytest.mark.parametrize("production", [False, True])
def test_a_refused_apply_updates_keeps_its_item_open_saying_why(db, monkeypatch, production):
    """Review 3 (UI-01a blocking 1): "A production run is in progress" (or production itself) is not done: the item
    stays open with "Not done: <reason>", the toast says it, nothing is sent and nothing starts."""
    import main
    from src import maintenance
    from src.control import make_command_handler
    monkeypatch.setattr(maintenance, "apply_allowed", lambda: not production)
    n = notes()
    bot, fake = make_bot(db, monkeypatch)
    dashboard = bot.show_dashboard("🏛️ before the tap", [[{"text": "x", "callback_data": "cdash:"}]])
    item = _failure_item(db, bot, [{"text": "🧰 Apply updates", "callback_data": "upd:apply"}], key="maintenance:apply")
    started = []

    def handler_for(busy):
        return make_command_handler(db, lambda: "Mon 07:00", lambda: "",
                                    apply_updates=lambda: main.apply_updates_reply(busy, lambda: started.append(1)))
    sends = len(fake.sends())
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': 'upd:apply'})}", handler_for(True))
    why = "not applied in production" if production else "A production run is in progress"
    after = n.get(db, item["id"])
    assert after["state"] == "open" and why.lower() in after["note"].lower() and started == []
    assert "⚠️ <i>Not done: " in n.render(db)[0] and why.lower() in n.plain(n.render(db)[0]).lower()
    assert why.lower() in _toasts(fake)[-1].lower() and len(fake.sends()) == sends
    assert fake.messages[dashboard]["text"].startswith("🏛️ before the tap")   # not drawn over
    if production:
        return
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': 'upd:apply'})}", handler_for(False))
    assert n.get(db, item["id"])["state"] == "handled" and started == [1]
    assert _toasts(fake)[-1].startswith("🧰 Applying updates") and len(fake.sends()) == sends


@pytest.mark.parametrize("reply, done", [
    ("🔁 <b>Notre-Dame de Paris</b> is being re-rendered, keeping its voiceover. Its progress shows here.", True),
    ("🔁 <b>Nottingham Castle</b> is being re-rendered, keeping its voiceover. Its progress shows here.", True),
    ("🔁 Notre-Dame de Paris: that run's files are no longer kept; Render the topic again instead.", False),
    ("🔁 A run is already in progress; Re-render this one when it has finished.", False),
])
def test_re_render_is_judged_by_its_handlers_outcome_never_by_its_words(db, monkeypatch, reply, done):
    """Review 3 (UI-01a non-blocking 1): a started re-render of a topic called "Notre-Dame..." was read as refused
    ("Not" in its first 12 characters). The outcome is the handler's (notifications.Outcome)."""
    n = notes()
    bot, fake = make_bot(db, monkeypatch)
    item = _failure_item(db, bot, [{"text": "Re-render", "callback_data": "rerender:r1"}])
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': 'rerender:r1'})}", lambda a, b: _outcome(reply, done))
    assert (n.get(db, item["id"])["state"] == "handled") is done
    assert _toasts(fake)[-1] == ("Re-render started" if done else n.clip(n.plain(reply), 190))


def test_a_re_render_reply_that_does_not_say_its_outcome_keeps_the_item(db, monkeypatch):
    """A handler that does not say (no daemon: control's own reply; plain words) never closes the item."""
    from src.control import make_command_handler
    n = notes()
    bot, _fake = make_bot(db, monkeypatch)
    item = _failure_item(db, bot, [{"text": "Re-render", "callback_data": "rerender:r1"}])
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': 'rerender:r1'})}",
        make_command_handler(db, lambda: "Mon 07:00", lambda: ""))
    assert n.get(db, item["id"])["state"] == "open" and "daemon is running" in n.get(db, item["id"])["note"]
    item = _failure_item(db, bot, [{"text": "Re-render", "callback_data": "rerender:r2"}], key="fail:r2")
    tap(bot, f"n:{item['id']}:{n.action_ref({'callback_data': 'rerender:r2'})}",
        lambda a, b: "🔁 <b>Petra</b> is being re-rendered.")
    assert n.get(db, item["id"])["state"] == "open"


def test_the_re_render_handlers_say_their_outcome(db, monkeypatch):
    import main

    class Coordinator:
        def __init__(self, refusal=None):
            self.db, self.refusal = db, refusal

        def rerender_refusal(self, run_id):
            return self.refusal

        def remake_args(self, run_id):
            return []
    monkeypatch.setattr(main, "rerender_started", lambda db_, run_id: "🔁 <b>Notre-Dame de Paris</b> is being re-rendered.")
    started = main.rerender_reply(Coordinator(), "r1", lambda extra: None)
    assert started.done is True and started.startswith("🔁 <b>Notre-Dame")
    busy = main.rerender_reply(Coordinator(), "r1", lambda extra: _outcome("🔁 A run is already in progress.", False))
    assert busy.done is False and "already in progress" in busy
    refused = main.rerender_reply(Coordinator("That run is no longer recorded."), "r1", lambda extra: None)
    assert refused.done is False and refused == "🔁 That run is no longer recorded."


def test_a_dashboard_re_render_of_notre_dame_reads_as_started(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    dashboard = bot.show_dashboard("🏛️ home", [[{"text": "x", "callback_data": "cdash:"}]])
    cq = {"id": "cq-nd", "from": {"id": 7}, "data": "rerender:r1",
          "message": {"message_id": dashboard, "chat": {"id": 100}}}
    bot._process_update({"callback_query": cq},
                        lambda a, b: _outcome("🔁 <b>Notre-Dame de Paris</b> is being re-rendered.", True))
    assert "Notre-Dame" not in fake.messages[dashboard]["text"]   # started: home again (a refusal stays drawn)


# ------------------------------------------------------------------ port onto 84c3eeb4: the carousel rows' notices
# QF-81 (holds with Redraw), QF-81b (binding holds) and QF-81c (final holds) added carousel notices, and QF-65a the AI
# label on a photo post's notice. Each is one item of the panel under its booking's key, with its actions.
import test_qf81_lazy_carousels as qf81
from test_qf81_lazy_carousels import TUE, _carousels, _slot

coord = qf81.coord   # QF-81's fixture: a coordinator that makes planned runs and runs the carousel slot


@pytest.fixture
def carousel_desk(coord, db, monkeypatch):
    """QF-81's coordinator with its real _alert writing to a bot over the fake Bot API (no stand-in alert)."""
    bot, fake = make_bot(db, monkeypatch)
    del coord._alert   # the class's own: notifications.say(telegram_bot.notify, ...)
    bot.stamp_review_outcome = lambda run_id, text: True
    coord.telegram_bot = bot
    bot.layout.ensure()
    return coord, bot, fake


def _carousel_items(db):
    return [i for i in notes().open_items(db) if i["dedupe_key"].startswith("post-failed:")]


def _redraw_handler(db, coordinator=None):
    from src.control import make_command_handler
    return make_command_handler(db, lambda: "Mon 07:00", lambda: "",
                                run_actions={"carredraw": coordinator.redraw_carousel} if coordinator else None)


def test_a_held_carousel_is_one_needs_you_item_whose_redraw_closes_it_only_once_done(carousel_desk):

    c, bot, fake = carousel_desk
    n = notes()
    c.contrast = 2.1   # its delivered text contrast fails: held at its slot with Redraw (QF-81)
    c.make("r1", TUE - qf81.timedelta(days=3))
    sends = len(fake.sends())
    _slot(c, TUE)
    _slot(c, TUE)   # a second pass in the same slot says nothing more
    held = _carousels(c, "r1")[0]
    items = _carousel_items(c.db)
    assert held["status"] == "failed" and len(items) == 1
    item = items[0]
    assert item["section"] == "needs" and item["dedupe_key"] == f"post-failed:{held['id']}"
    assert item["title"].startswith("Carousel held") and "Nothing was posted" in item["body"]
    assert item["actions"] == [{"text": "🔁 Redraw and post next slot", "callback_data": f"carredraw:{held['id']}"}]
    # no message of its own: only the item's one ring, which carries no buttons
    assert all("reply_markup" not in d and "Nothing was posted" not in d["text"] for d in fake.sends()[sends:])
    ref = n.action_ref(item["actions"][0])
    tap(bot, f"n:{item['id']}:{ref}", _redraw_handler(c.db))   # no daemon: refused, the item stays open saying why
    after = n.get(c.db, item["id"])
    assert after["state"] == "open" and "only works while the daemon is running" in after["note"]
    c.contrast = None   # the cause is dealt with
    qf81._Frozen.now_value = qf81.ps.carousel_time(TUE.date()) + qf81.timedelta(hours=2)
    tap(bot, f"n:{item['id']}:{ref}", _redraw_handler(c.db, c))
    assert n.get(c.db, item["id"])["state"] == "handled"
    again = _carousels(c, "r1")[0]
    assert again["status"] == "waiting" and again["detail"].get("priority")
    assert "is drawn again and posts" in _toasts(fake)[-1]
    assert all("Nothing was posted" not in d["text"] for d in fake.sends()[sends:])


def test_a_redraw_the_coordinator_refuses_keeps_the_hold_item_open(carousel_desk):
    """QF-81c: once a held carousel's plan is gone, its Redraw is refused by redraw_carousel (notifications.Outcome,
    done False): the item stays open, its page saying why, and the booking is unchanged."""
    c, bot, _fake = carousel_desk
    n = notes()
    c.contrast = 2.1
    run_dir, _ = c.make("r1", TUE - qf81.timedelta(days=3))
    _slot(c, TUE)
    item = _carousel_items(c.db)[0]
    (run_dir / "carousel_plan.json").unlink()
    tap(bot, f"n:{item['id']}:{n.action_ref(item['actions'][0])}", _redraw_handler(c.db, c))
    after = n.get(c.db, item["id"])
    assert after["state"] == "open" and "can't be redrawn" in after["note"] and "carousel_plan.json" in after["note"]
    assert _carousels(c, "r1")[0]["status"] == "failed"


@pytest.mark.parametrize("cause", ["plan-gone", "binding"])
def test_a_carousel_that_can_never_post_is_one_needs_you_item_with_details(carousel_desk, cause):
    """QF-81b's binding hold and QF-81c's final hold: one item each, its Details opening the episode's Posting page in
    the dashboard (the item page stays)."""
    import test_qf81b_plan_fingerprint as qf81b
    c, bot, _fake = carousel_desk
    n = notes()
    run_dir, _ = c.make("r1", TUE - qf81.timedelta(days=3))
    if cause == "plan-gone":
        (run_dir / "carousel_plan.json").unlink()
    else:
        qf81b._approve_on_card(c, "r1")
        qf81b._substitute_photo(run_dir)
    _slot(c, TUE)
    _slot(c, TUE)
    held = _carousels(c, "r1")[0]
    items = _carousel_items(c.db)
    assert held["status"] == "failed" and held["detail"].get("binding" if cause == "binding" else "final")
    assert len(items) == 1 and items[0]["section"] == "needs" and items[0]["title"].startswith("Carousel not posted")
    details = {"text": "📋 Details", "callback_data": f"cpost:{c.db.run_key('r1')}"}
    assert items[0]["actions"] == [details] and n.opens_a_screen(details["callback_data"])
    tap(bot, f"n:{items[0]['id']}:{n.action_ref(details)}", _redraw_handler(c.db, c))
    assert n.get(c.db, items[0]["id"])["state"] == "open"   # a screen: drawn in the dashboard, the item stays


def test_with_the_switch_off_a_carousel_hold_is_sent_as_on_the_base(carousel_desk, monkeypatch):
    """TELEGRAM_CLEAN_CHAT=false: the hold reaches notify exactly as 84c3eeb4 sent it (text, its buttons, studio)."""
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    c, bot, _fake = carousel_desk
    calls = []
    monkeypatch.setattr(bot, "notify", lambda text, **kw: calls.append((text, kw)) or True)
    c.contrast = 2.1
    c.make("r1", TUE - qf81.timedelta(days=3))
    _slot(c, TUE)
    held = _carousels(c, "r1")[0]
    hold = [(t, kw) for t, kw in calls if "Carousel held" in t]
    assert len(hold) == 1 and hold[0][1] == {
        "buttons": [[{"text": "🔁 Redraw and post next slot", "callback_data": f"carredraw:{held['id']}"}]],
        "group": "studio"}
    assert notes().open_items(c.db) == []


def test_a_photo_post_whose_facebook_copy_was_held_for_ai_media_is_one_info_item(carousel_desk, monkeypatch):
    """QF-65a: the posted notice names the Facebook hold; it is info (nothing to do), never a message."""
    c, _bot, fake = carousel_desk
    monkeypatch.setattr(c.publisher, "publish_photos", lambda run_id, tid, kind, files, caption="", **k: {
        "status": "published", "held_platforms": {"facebook": "not posted to Facebook: gap_gen_beat02.jpg is AI"}})
    c.make("r1", TUE - qf81.timedelta(days=3))
    sends = len(fake.sends())
    _slot(c, TUE)
    posted = [i for i in notes().open_items(c.db) if i["title"].startswith("Carousel posted")]
    assert len(posted) == 1 and posted[0]["section"] == "info"
    said = posted[0]["title"] + posted[0]["subject"] + posted[0]["body"]
    assert "with the AI label" in said and "gap_gen_beat02.jpg" in said
    assert all("gap_gen_beat02" not in d["text"] for d in fake.sends()[sends:])
