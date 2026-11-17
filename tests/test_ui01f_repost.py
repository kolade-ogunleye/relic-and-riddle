"""UI-01f: the layout is re-posted in order before 48 h and nothing else stays (design
docs/design/telegram-clean-chat-2026-11-11.md §5.2, §5.3, §5.5, §6.3 path B, §7.2, §7.3, §7.5, §13; mock-up 13).

The ordered rebuild posts every panel again top to bottom, silently (the review from its file ids, the
notifications panel pinned again), swaps, then deletes the old copies in one call; an interrupted rebuild leaves no
duplicate; the 30 h idle / 40 h forced age rule; a copy that cannot be deleted is tombstoned once; the gap sweep
deletes everything that is not a panel; the reconcile at start; the migration of the earlier layout; stop and back.
A fake Bot API keeps ids, media, pins, refusals and deletions; nothing calls Telegram."""
import json

import pytest

from src import config, telegram_bot
from tests.test_ui01e_review_panel import FakeTelegram, a_review, deliver


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


class Chat(FakeTelegram):
    """The fake Bot API with bulk deletions, pins and messages Telegram refuses to delete (over 48 h)."""

    def __init__(self):
        super().__init__()
        self.pinned, self.too_old = [], set()

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "deleteMessages":
            self.calls.append((method, data))
            ids = [int(m) for m in json.loads(data["message_ids"])]
            if self.too_old & set(ids):
                raise RuntimeError("Telegram deleteMessages failed: HTTP 400 Bad Request: message can't be deleted")
            self.deleted.update(ids)
            return True
        if method == "deleteMessage" and int(data["message_id"]) in self.too_old:
            self.calls.append((method, data))
            raise RuntimeError("Telegram deleteMessage failed: HTTP 400 Bad Request: message can't be deleted")
        if method == "pinChatMessage":
            self.pinned.append(int(data["message_id"]))
        return super().__call__(method, timeout, **kw)

    def live(self):
        return sorted(m for m in self.messages if m not in self.deleted)


def make_bot(db, monkeypatch):
    from src import notice_conditions
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    chat = Chat()
    monkeypatch.setattr(bot, "_api", chat)
    monkeypatch.setattr(bot, "_queue_navigation", lambda work: work() or True)
    monkeypatch.setattr(bot, "_delete_soon", bot.delete_message)
    monkeypatch.setattr(bot, "start_progress_ticker", lambda run_id: None)
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [])
    bot.layout.timer = lambda *a, **k: type("T", (), {"start": lambda self: None, "daemon": True})()
    bot.layout.repost.sleep = lambda s: None
    bot.layout.tick(force=True)          # the pinned notifications panel
    bot.show_dashboard()                 # the dashboard under it
    return bot, chat


def full_layout(db, tmp_path, monkeypatch):
    """Notifications, dashboard, a progress panel and the review panel, in that order."""
    bot, chat = make_bot(db, monkeypatch)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("prog", topic)
    db.update_run("prog", "producing", script_payload={"title": "Petra", "topic_id": topic})
    bot.send_progress_card("prog", "Petra")
    root, payload = a_review(db, tmp_path, monkeypatch, "rev")
    deliver(bot, db, root, payload, "rev")
    return bot, chat


def slots(db, bot):
    from src.telegram_layout import slot_message
    return [slot_message(db, "notifications"), int(db.get_setting("telegram_dashboard_id")),
            slot_message(db, "progress:prog"), slot_message(db, "review")]


# ------------------------------------------------------------------------------------------------ the rebuild
def test_the_rebuild_posts_every_panel_again_in_order_and_deletes_the_old_copies_in_one_call(db, tmp_path, monkeypatch):
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    assert old == sorted(old)
    chat.calls.clear()
    chat.uploads.clear()
    sent = bot.layout.rebuild()
    new = slots(db, bot)
    assert sent and new == sorted(new) and min(new) > max(old)              # the same order, below the old
    sends = [(m, d) for m, d in chat.calls if m.startswith("send")]
    assert [m for m, _ in sends] == ["sendMessage", "sendMessage", "sendMessage", "sendVideo"]
    assert all(d.get("disable_notification") == "true" for _, d in sends)  # silent
    assert chat.uploads == []                                                # the review from its file ids
    assert sends[-1][1]["video"] == bot.review.record("rev")["video_id"]
    assert chat.pinned[-1] == new[0]                                         # pinned again
    bulk = [d for m, d in chat.calls if m == "deleteMessages"]
    assert json.loads(bulk[0]["message_ids"]) == old                          # the old copies in one call
    assert chat.live() == sorted(new)                                         # nothing else in the chat
    for o, n in zip(old, new):
        assert bot._current_panel(o) == n                                     # a tap on an old copy acts on the new
    assert (db.get_approval("rev") or {})["telegram_message_id"] == new[3]  # the review decides from the new copy
    assert json.loads(db.get_setting("active_progress"))["message_id"] == new[2]


def test_the_rebuild_never_calls_show_dashboard_and_only_the_renderer_rebuilds(db, tmp_path, monkeypatch):
    from src import telegram_layout
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "show_dashboard", lambda *a, **k: pytest.fail("show_dashboard under Layout._lock"))
    assert bot.layout.rebuild() is not None
    telegram_layout.release(db, bot.layout.owner)
    assert telegram_layout.acquire(db, "the-daemon")
    before = list(chat.calls)
    assert bot.layout.rebuild() is None and chat.calls == before            # not the renderer: nothing is sent


def test_a_rebuild_beside_a_dashboard_tap_and_a_review_page_turn_never_deadlocks(db, tmp_path, monkeypatch):
    """Round 2's lock order (telegram_layout: _rebuild_lock -> _dashboard_lock -> Layout._lock -> the review panel's
    lock), with real threads: thread A re-posts the layout and is held inside the send of the dashboard's copy;
    thread B draws the dashboard (a tap) and thread C turns the review panel's page meanwhile. All three finish,
    one copy per panel stays, and the tap and the page turn land on the new copies, not on the old ones."""
    import threading
    import time
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    inside, release = threading.Event(), threading.Event()
    sends = [0]

    def api(method, timeout=30, **kw):
        if threading.current_thread().name == "rebuild" and method.startswith("send"):
            sends[0] += 1
            if sends[0] == 2:                       # the dashboard's copy: the re-post is held mid-way
                inside.set()
                assert release.wait(10)
        return chat(method, timeout, **kw)
    monkeypatch.setattr(bot, "_api", api)
    key = db.run_key("rev")
    work = {
        "rebuild": lambda: bot.layout.rebuild(),
        "tap": lambda: bot.show_dashboard("🏛️ <b>Dashboard</b> · tapped", [[{"text": "x", "callback_data": "cdash:"}]]),
        "page": lambda: bot.review.tap({"id": "qp", "data": f"rvt:{key}:scr", "from": {"id": 7},
                                        "message": {"message_id": old[3], "chat": {"id": 100}}}),
    }
    threads = {name: threading.Thread(target=fn, name=name, daemon=True) for name, fn in work.items()}
    threads["rebuild"].start()
    assert inside.wait(10)
    threads["tap"].start()
    threads["page"].start()
    time.sleep(0.3)                                 # B and C are waiting on the re-post's locks
    release.set()
    for t in threads.values():
        t.join(10)
    assert not [name for name, t in threads.items() if t.is_alive()]        # no deadlock
    new = slots(db, bot)
    assert new == sorted(new) and not set(new) & set(old) and chat.live() == sorted(new)
    assert "tapped" in chat.messages[new[1]]["text"]                          # the tap drew the new dashboard
    assert bot.review.view()["tab"] == "scr"
    assert chat.messages[new[3]]["caption"].startswith("<b>🎬 Review 1 of 1 · Script</b>")   # the new review copy


def test_a_re_post_is_never_started_under_the_dashboard_lock_or_the_layouts_lock(db, tmp_path, monkeypatch):
    """The first dashboard (placed by a re-post) and path B drawn through show_dashboard (under its lock) start the
    re-post only once the dashboard lock is released; the renderer's age rule runs after Layout._lock is."""
    from src.telegram_layout import Layout
    held = []
    real = Layout.rebuild

    def watched(self, from_slot="notifications"):
        held.append((from_slot, self.bot._dashboard_lock._is_owned(), self._lock._is_owned()))
        return real(self, from_slot)
    monkeypatch.setattr(Layout, "rebuild", watched)
    bot, chat = full_layout(db, tmp_path, monkeypatch)                        # its first dashboard: a re-post
    assert ("dashboard", False, False) in held
    old = slots(db, bot)
    bot._record_kind(old[1], media=True)                                      # the dashboard shows a contact sheet
    held.clear()
    bot.show_dashboard("🏛️ <b>Dashboard</b> · home", [[{"text": "x", "callback_data": "cdash:"}]])
    assert held == [("dashboard", False, False)]                              # path B, after the lock was released
    new = slots(db, bot)
    assert new[0] == old[0] and new[1] > old[3] and new == sorted(new) and chat.live() == sorted(new)
    assert chat.messages[new[1]]["text"].startswith("🏛️ <b>Dashboard</b> · home")
    monkeypatch.setattr(bot.layout.repost, "due", lambda now: True)          # the age rule, on the renderer's tick
    held.clear()
    calls = []
    monkeypatch.setattr(bot.layout.repost, "rebuild", lambda from_slot="notifications": calls.append(
        (bot._dashboard_lock._is_owned(), bot.layout._lock._is_owned())) or {})
    bot.layout.tick(force=True)
    assert calls == [(False, False)]


def test_an_interrupted_rebuild_leaves_one_copy_per_panel(db, tmp_path, monkeypatch):
    from src.layout_repost import Repost
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    real = Repost._copy
    count = [0]

    def dies(self, row):
        count[0] += 1
        if count[0] == 3:
            raise KeyboardInterrupt        # the process is killed between two sends
        return real(self, row)
    monkeypatch.setattr(Repost, "_copy", dies)
    with pytest.raises(KeyboardInterrupt):
        bot.layout.rebuild()
    assert slots(db, bot) == old                                              # the rows still point at the old copies
    assert len(chat.live()) == 6                                              # two half-sent copies are in the chat
    monkeypatch.setattr(Repost, "_copy", real)
    bot.layout._repost = None                                                 # the next renderer
    assert bot.layout.repost.reconcile() == "notifications"
    new = slots(db, bot)
    assert chat.live() == sorted(new) and len(new) == 4 and not set(new) & set(old)


# ------------------------------------------------------------------------------------------------ the age rule
def test_thirty_hours_waits_for_an_idle_moment_and_forty_hours_forces_it(db, tmp_path, monkeypatch):
    bot, _chat = full_layout(db, tmp_path, monkeypatch)
    repost = bot.layout.repost
    sent = repost.oldest()
    hour = 3600
    bot._last_activity = sent + 29 * hour
    assert not repost.due(sent + 29 * hour)                                   # not yet
    bot._last_activity = sent + 31 * hour - 60                                # a tap a minute ago
    assert not repost.due(sent + 31 * hour)
    bot._last_activity = sent + 31 * hour - 11 * 60                           # idle 11 minutes
    assert repost.due(sent + 31 * hour)
    bot._awaiting = ("rvin", sent + 32 * hour)                                # but an input waits
    assert not repost.due(sent + 31 * hour)
    view = bot.review.view()
    bot._awaiting = None
    bot.review._save({**view, "opened": sent + 31 * hour - 60})               # a review page opened a minute ago
    assert not repost.due(sent + 31 * hour)
    assert repost.due(sent + 40 * hour)                                       # forced at 40 h, whatever happens
    monkeypatch.setattr(bot.layout, "clock", lambda: sent + 40 * hour)
    old = slots(db, bot)
    assert repost.tick(sent + 40 * hour)
    assert not set(slots(db, bot)) & set(old) and repost.oldest() >= sent + 40 * hour


def test_a_copy_that_can_no_longer_be_deleted_is_tombstoned_once(db, tmp_path, monkeypatch):
    from src.layout_repost import STUCK_TEXT
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    chat.too_old.add(old[1])                                                  # the dashboard's copy is over 48 h
    bot.layout.rebuild()
    assert set(old) - {old[1]} <= chat.deleted and old[1] not in chat.deleted
    assert chat.messages[old[1]]["text"] == STUCK_TEXT and json.loads(chat.messages[old[1]]["reply_markup"]) == {"inline_keyboard": []}
    chat.calls.clear()
    bot.layout.rebuild()
    assert not [d for m, d in chat.calls if m.startswith("edit") and int(d["message_id"]) == old[1]]


# ------------------------------------------------------------------------------------------------ the gap sweep
def test_the_gap_sweep_deletes_everything_that_is_not_a_panel(db, tmp_path, monkeypatch):
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    panels = slots(db, bot)
    orphan = chat("sendMessage", data={"chat_id": "100", "text": "a crashed CLI's card"})["message_id"]
    reading = orphan + 1
    bot._note_update({"update_id": 1, "message": {"message_id": reading, "text": "typing…", "from": {"id": 7}}})
    removed = bot.layout.repost.sweep(force=True)
    bulk = [d for m, d in chat.calls if m == "deleteMessages"]
    assert removed >= 1 and orphan in chat.deleted
    assert not set(panels) & chat.deleted and reading not in chat.deleted   # panels and a message being read stay
    assert bulk


def test_reconcile_at_start_reposts_from_the_first_missing_panel(db, tmp_path, monkeypatch):
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    chat.deleted.add(old[1])                                                  # the operator deleted the dashboard
    bot.layout._repost = None
    assert bot.layout.repost.reconcile() == "dashboard"
    new = slots(db, bot)
    assert new[0] == old[0] and new == sorted(new) and chat.live() == sorted(new)


# ------------------------------------------------------------------------------------------------ path B
def test_leaving_a_media_screen_reposts_the_dashboard_and_the_panels_below_in_order(db, tmp_path, monkeypatch):
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    bot._record_kind(old[1], media=True)                                      # the dashboard shows a contact sheet
    assert bot.show_navigation_panel(old[1], "🖼 <b>Pictures</b>", [[{"text": "🏠 Dashboard", "callback_data": "cdash:"}]])
    new = slots(db, bot)
    assert new[0] == old[0] and new[1] > old[3] and new == sorted(new)        # dashboard, progress, review again
    assert chat.messages[new[1]]["text"].startswith("🖼 <b>Pictures</b>") and chat.live() == sorted(new)


# ------------------------------------------------------------------------------------------------ migration
def test_the_first_start_cleans_the_earlier_layout_and_moves_reviews_into_the_panel(db, tmp_path, monkeypatch):
    from src import notifications
    from src.telegram_state import put
    bot, chat = make_bot(db, monkeypatch)
    legacy = [chat("sendMessage", data={"chat_id": "100", "text": f"old {n}"})["message_id"] for n in range(4)]
    db.record_alert("⌛ No decision on …", message_id=legacy[0], kind="persistent")
    put(db, "timeout-notice", "gone", legacy[1])
    put(db, "review-messages", "gone", [legacy[2]])
    db.put_telegram_card("review:gone", 4908, legacy[3], "review_pair", {"video_mid": legacy[3]})
    _root, payload = a_review(db, tmp_path, monkeypatch, "waiting")
    db.record_approval("waiting", payload["topic_id"], legacy[2])           # a review on a card of the earlier chat
    chat.too_old.add(legacy[0])
    result = bot.layout.repost.migrate()
    assert result["posted"] >= 2 and bot.review.pager() == ["waiting"]
    assert set(legacy[1:]) <= chat.deleted
    item = [i for i in notifications.open_items(db) if i["dedupe_key"] == "old-messages"]
    assert item and "Clear history" in item[0]["body"]                         # the one it could not remove
    live = set(chat.live()) - {legacy[0]}
    assert live == set(slots_or_none(db))
    assert bot.layout.repost.migrate() is None                                # once


def slots_or_none(db):
    from src.telegram_layout import panel_ids
    return panel_ids(db) | {int(db.get_setting("telegram_dashboard_id"))}


# ------------------------------------------------------------------------------------------------ stop and back
def test_a_clean_stop_says_so_and_the_start_says_back_online(db, tmp_path, monkeypatch):
    from src import notifications
    bot, chat = make_bot(db, monkeypatch)
    dash = int(db.get_setting("telegram_dashboard_id"))
    clock = [bot.layout.clock()]
    monkeypatch.setattr(bot.layout, "clock", lambda: clock[0])
    bot.service_stopped()
    assert "⏸️ Bot stopped" in chat.messages[dash]["text"] and "buttons work again when it is back" in chat.messages[dash]["text"]
    item = next(i for i in notifications.open_items(db) if i["dedupe_key"] == "service-state")
    assert item["section"] == "info" and item["title"] == "Bot stopped"
    clock[0] += 2 * 3600 + 14 * 60
    bot.layout.repost.back(None)
    item = next(i for i in notifications.open_items(db) if i["dedupe_key"] == "service-state")
    assert item["section"] == "info" and item["title"] == "Back online" and item["body"].startswith("Was down 2 h 14 min")
    clock[0] += 3600
    bot.layout.repost.back(clock[0] - 40 * 60)                                # unplanned, 40 minutes
    item = next(i for i in notifications.open_items(db) if i["dedupe_key"] == "service-state")
    assert item["section"] == "needs" and item["actions"] == [{"text": "🗓️ Posting", "callback_data": "cposts:"}]


def test_with_the_switch_off_the_rebuild_is_the_earlier_one(db, monkeypatch):
    bot, _chat = make_bot(db, monkeypatch)
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    shown = []
    monkeypatch.setattr(bot, "show_dashboard", lambda **kw: shown.append(kw))
    bot.layout.rebuild()
    assert shown == [{"fresh": True, "rebuild": True}]


# ------------------------------------------------------------------------------------------------ round 2
def second_bot(db, chat, monkeypatch, owner):
    """Another process on the same database and chat (the run's worker)."""
    from src.telegram_bot import TelegramApprovalBot
    from src.telegram_layout import Layout
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    monkeypatch.setattr(bot, "_api", chat)
    monkeypatch.setattr(bot, "_queue_navigation", lambda work: work() or True)
    monkeypatch.setattr(bot, "_delete_soon", bot.delete_message)
    bot._layout = Layout(bot, owner=owner)
    bot._layout.timer = lambda *a, **k: type("T", (), {"start": lambda self: None, "daemon": True})()
    bot._layout.repost.sleep = lambda s: None
    return bot


def test_a_worker_never_sends_a_dashboard_it_asks_the_renderer_to_draw_it(db, tmp_path, monkeypatch):
    """Review 1 (UI-01f blocking 1): the operator has a picture screen open (a media dashboard) and a run's worker
    redraws the dashboard as its run ends. It sent a new dashboard at the bottom, under the progress and review
    panels. Only the renderer sends layout messages (design §5.4): the worker asks, the renderer re-posts in order."""
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    bot._record_kind(old[1], media=True)
    worker = second_bot(db, chat, monkeypatch, "worker")
    sends = len([1 for m, _ in chat.calls if m.startswith("send")])
    worker.show_dashboard("🏛️ <b>After the run</b>", [[{"text": "x", "callback_data": "cdash:"}]])
    assert len([1 for m, _ in chat.calls if m.startswith("send")]) == sends    # the worker sent nothing
    assert slots(db, bot) == old
    bot.layout.tick()                                                         # the renderer's next round
    new = slots(db, bot)
    assert new == sorted(new) and chat.live() == sorted(new)                  # in order, nothing else left
    assert chat.messages[new[1]]["text"].startswith("🏛️ <b>After the run</b>")
    assert db.get_setting(bot.DASHBOARD_ASK_KEY) in ("", None)
    # a worker whose dashboard edit finds the message gone does not send one either
    chat.deleted.add(new[1])
    worker.show_dashboard("🏛️ again", [[{"text": "x", "callback_data": "cdash:"}]])
    assert len([1 for m, _ in chat.calls if m.startswith("send")]) == sends + 3   # only the renderer's re-post


def test_a_renderer_whose_media_re_post_fails_keeps_the_dashboard_where_it_is(db, tmp_path, monkeypatch):
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    bot._record_kind(old[1], media=True)
    monkeypatch.setattr(bot.layout, "rebuild", lambda *a, **k: None)          # the re-post cannot run
    sends = len([1 for m, _ in chat.calls if m.startswith("send")])
    assert bot.show_navigation_panel(old[1], "🏛️ home", [[{"text": "x", "callback_data": "cdash:"}]])
    bot._repost_if_asked()
    assert len([1 for m, _ in chat.calls if m.startswith("send")]) == sends and slots(db, bot) == old


def _refusing(chat, real):
    def api(method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "sendMessage" and data.get("disable_notification") == "true" and "RELIC" in data.get("text", ""):
            raise RuntimeError("Telegram sendMessage failed: HTTP 400 Bad Request: BUTTON_DATA_INVALID")
        return real(method, timeout, **kw)
    return api


def test_a_failing_re_post_backs_off_and_says_so_once(db, tmp_path, monkeypatch):
    """Review 1 (UI-01f blocking 2): a re-post whose dashboard copy is refused was tried on every renderer tick (a
    copy of the notifications panel sent, pinned and deleted each second). Now: one try, then 1, 2, 4 ... 15 min;
    no extra copy or pin in between; after 3 failures one Needs-you item, closed when a re-post works."""
    from src import notifications
    from src.layout_repost import RETRY_ITEM_KEY
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    real = bot._api
    monkeypatch.setattr(bot, "_api", _refusing(chat, real))
    monkeypatch.setattr(bot.layout.repost, "due", lambda now: True)
    t0 = bot.layout.clock()

    def counts():
        return (len([1 for m, d in chat.calls if m == "sendMessage" and d.get("disable_notification") == "true"]),
                len([1 for m, _ in chat.calls if m == "pinChatMessage"]))
    before = counts()
    for k in range(10):                                   # ten renderer ticks, one a second
        bot.layout.clock = lambda k=k: t0 + k
        bot.layout.tick()
    copies, pins = (a - b for a, b in zip(counts(), before))
    assert (copies, pins) == (1, 1)                       # the first try only (its copy deleted again)
    assert chat.live() == sorted(slots(db, bot))
    assert not [i for i in notifications.open_items(db) if i["dedupe_key"] == RETRY_ITEM_KEY]
    for at, tries in ((59, 1), (60, 2), (179, 2), (180, 3)):   # the next tries wait 1, then 2 minutes
        bot.layout.clock = lambda at=at: t0 + at
        bot.layout.tick()
        assert counts()[0] - before[0] == tries
    assert not [i for i in notifications.open_items(db) if i["dedupe_key"] == RETRY_ITEM_KEY]
    bot.layout.clock = lambda: t0 + 181
    bot.layout.tick()                                     # three failures: one item says so
    item = [i for i in notifications.open_items(db) if i["dedupe_key"] == RETRY_ITEM_KEY]
    assert len(item) == 1 and item[0]["section"] == "needs" and item[0]["actions"]
    monkeypatch.setattr(bot, "_api", real)                # Telegram takes the copy again: the item closes
    bot.layout.clock = lambda: t0 + 420
    bot.layout.tick()
    assert slots(db, bot) == sorted(slots(db, bot)) and chat.live() == sorted(slots(db, bot))
    assert not [i for i in notifications.open_items(db) if i["dedupe_key"] == RETRY_ITEM_KEY]


def test_a_rate_limited_re_post_waits_for_telegrams_retry_after_even_at_start(db, tmp_path, monkeypatch):
    bot, _chat = full_layout(db, tmp_path, monkeypatch)
    real = bot._api

    def api(method, timeout=30, **kw):
        if method == "sendMessage" and "RELIC" in dict(kw.get("data") or {}).get("text", ""):
            raise telegram_bot.TelegramRateLimited(method, 600, "Too Many Requests: retry after 600")
        return real(method, timeout, **kw)
    monkeypatch.setattr(bot, "_api", api)
    assert bot.layout.rebuild() is None
    monkeypatch.setattr(bot, "_api", real)
    t0 = bot.layout.clock()
    bot.layout.clock = lambda: t0 + 300
    assert bot.layout.rebuild(urgent=True) is None        # /start too waits for Telegram's own retry_after
    bot.layout.clock = lambda: t0 + 601
    assert bot.layout.rebuild(urgent=True) is not None


def test_a_review_panel_with_an_empty_pager_is_closed_not_copied(db, tmp_path, monkeypatch):
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    old = slots(db, bot)
    db.update_run("rev", "timeout")
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET decision='timeout' WHERE run_id='rev'")
    assert bot.review.pager() == []
    sent = bot.layout.repost.rebuild()
    assert sent is not None and "review" not in sent
    from src.telegram_layout import slot_message
    assert slot_message(db, "review") is None and old[3] in chat.deleted
    live = [slot_message(db, "notifications"), int(db.get_setting("telegram_dashboard_id")),
            slot_message(db, "progress:prog")]
    assert chat.live() == sorted(live)


def test_a_review_delivered_during_a_re_post_shows_on_the_new_copy(db, tmp_path, monkeypatch):
    """Review 1 (UI-01f non-blocking 1): the new copy said "Review 1 of 1" with no Next while two were waiting."""
    import threading
    bot, chat = full_layout(db, tmp_path, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "rev2")
    real = bot.layout.repost._swap

    def swap(plan, sent):
        t = threading.Thread(target=lambda: deliver(bot, db, root, payload, "rev2"), daemon=True)
        t.start()
        t.join(10)
        return real(plan, sent)
    monkeypatch.setattr(bot.layout.repost, "_swap", swap)
    bot.layout.rebuild()
    from src.telegram_layout import slot_message
    mid = slot_message(db, "review")
    keyboard = json.loads(chat.messages[mid].get("reply_markup") or "{}").get("inline_keyboard", [])
    assert (chat.messages[mid].get("caption") or "").startswith("<b>🎬 Review 1 of 2</b>")
    assert any("▶" in b["text"] for row in keyboard for b in row)


def test_the_migration_turns_the_earlier_chats_notices_into_items_before_deleting_them(db, tmp_path, monkeypatch):
    """Review 1 (UI-01f non-blocking 3): Needs you is never lost: a failure notice with buttons becomes a Needs-you
    item (its run's Details), an info notice an info item, silently; then the old messages go."""
    from src import notifications
    from src.telegram_state import put
    bot, chat = make_bot(db, monkeypatch)
    fail = chat("sendMessage", data={"chat_id": "100", "text": "x"})["message_id"]
    info = chat("sendMessage", data={"chat_id": "100", "text": "y"})["message_id"]
    old = chat("sendMessage", data={"chat_id": "100", "text": "z"})["message_id"]
    topic = db.list_all_topics()[0]["id"]
    db.create_run("r7", topic)
    db.record_alert("❌ <b>Production failed</b> · Petra\n\nStopped at the voiceover.", message_id=fail, kind="persistent")
    put(db, "message-run", f"100:{fail}", "r7")
    db.record_alert("📊 <b>Weekly digest</b>\nWeek of 2-8 Nov.", message_id=info, kind="persistent")
    db.record_alert("⚠️ <b>Old warning</b>", message_id=old, kind="persistent")
    with db._conn() as conn:
        conn.execute("UPDATE alerts SET created_at = '2020-01-01T00:00:00+00:00' WHERE message_id = ?", (old,))
    rang = len([1 for m, d in chat.calls if m == "sendMessage" and d.get("disable_notification") != "true"])
    bot.layout.repost.migrate()
    items = {i["title"]: i for i in notifications.open_items(db)}
    assert items["Production failed"]["section"] == "needs" and items["Production failed"]["subject"] == "Petra"
    assert items["Production failed"]["actions"] == [{"text": "Details", "callback_data": f"chist:{db.run_key('r7')}"}]
    assert items["Weekly digest"]["section"] == "info"
    assert "Old warning" not in items                                         # older than 48 h: it stays in the chat
    assert {fail, info} <= chat.deleted
    assert len([1 for m, d in chat.calls if m == "sendMessage" and d.get("disable_notification") != "true"]) == rang


def test_the_hourly_sweep_never_deletes_a_wizard_in_use(db, tmp_path, monkeypatch):
    """Review 1 of UI-01d (round 2) on this row: a typed /photos upload opened its wizard in a message the hourly sweep
    later deleted mid-step. The wizard is the dashboard now (UI-01b): the sweep keeps it."""
    import time
    bot, chat = make_bot(db, monkeypatch)
    topic = db.list_all_topics()[0]["id"]
    chat.messages[900] = {"text": "typed"}
    bot._process_update({"update_id": 900, "message": {"message_id": 900, "chat": {"id": 100}, "from": {"id": 7},
                                                       "date": int(time.time()), "text": f"/photos {topic} upload"}},
                        lambda a, b: "ok")
    wizard = bot.image_wizard().load(100, 7)["message_id"]
    from src.layout_repost import seen
    seen(db, 950)
    bot.layout.repost.sweep(force=True)
    assert wizard not in chat.deleted and wizard == int(db.get_setting("telegram_dashboard_id"))


# ------------------------------------------------------------------------------------------------ round 3: migration
EARLIER_NEEDS = [
    "🛑 <b>Blocked: unlicensed voice</b>\n\n<b>Petra</b> was not published.",
    "⏱ <b>Run stopped at its deadline</b>\n\n<b>Petra</b> may have posted: answer Posted or Not posted.",
    "🗄 <b>Backups need attention</b>: no backup in 3 days",
    "🧾 Couldn't build the fact sheet for Petra: timeout",
    "🔁 <b>Re-render not started</b>: the run is approved",
    "⚠️ <b>Publish unconfirmed</b>\n\n<b>Petra</b>",
    "⚠️ Scheduled posting hit an error: Instagram refused the Stories upload.",
    "❌ <b>Publish failed</b>\n\n<b>Petra</b>",
    "💥 <b>The run's worker process ended without a result</b>",
    "⚠️ <b>Interrupted run(s) cleared</b>: the worker had stopped.",
]


def _earlier_alert(db, chat, text, kind, hours_left=5.0):
    from datetime import UTC, datetime, timedelta
    mid = chat("sendMessage", data={"chat_id": "100", "text": "old"})["message_id"]
    expires = (datetime.now(UTC) + timedelta(hours=hours_left)).isoformat() if kind == "transient" else None
    db.record_alert(text, message_id=mid, kind=kind, expires_at=expires)
    return mid


def _earlier_items(db):
    from src import notifications
    return [i for i in notifications.open_items(db) if i["dedupe_key"].startswith("earlier:")]


@pytest.mark.parametrize("kind", ["persistent", "transient"])
@pytest.mark.parametrize("text", EARLIER_NEEDS)
def test_the_migration_never_loses_or_silences_a_needs_you_notice(db, monkeypatch, text, kind):
    """Review 2 (UI-01f blocking 1): the first start deleted the earlier chat's Needs-you notices without an item
    (every transient one: a posting error, Publish failed, a worker that ended, ...) or made them silent info (🛑 ⏱ 🗄
    🧾 🔁). Each unexpired notice becomes a Needs-you item, with a way to act, before its message is deleted."""
    bot, chat = make_bot(db, monkeypatch)
    mid = _earlier_alert(db, chat, text, kind)
    rang = len([1 for m, d in chat.calls if m == "sendMessage" and d.get("disable_notification") != "true"])
    bot.layout.repost.migrate()
    items = _earlier_items(db)
    assert mid in chat.deleted
    assert [i["section"] for i in items] == ["needs"] and items[0]["actions"]
    assert len([1 for m, d in chat.calls if m == "sendMessage" and d.get("disable_notification") != "true"]) == rang


@pytest.mark.parametrize("text, kind", [
    ("📊 <b>Weekly digest</b>\nWeek of 2-8 Nov.", "persistent"),
    ("🗓 <b>Post scheduled</b>\n\n<b>Petra</b>\n\nSat 18:00", "persistent"),
    ("✅ Stories posted for Petra", "transient"),
    ("🧰 <b>Weekly update check</b>: all pinned packages are current, no known vulnerabilities.", "transient"),
    ("🌙 Quiet night: nothing ran.", "transient"),
])
def test_the_migration_makes_only_informational_notices_info(db, monkeypatch, text, kind):
    bot, chat = make_bot(db, monkeypatch)
    mid = _earlier_alert(db, chat, text, kind)
    bot.layout.repost.migrate()
    assert [(i["section"], i["actions"]) for i in _earlier_items(db)] == [("info", [])] and mid in chat.deleted


def test_an_unknown_mark_is_needs_you_when_the_notice_asked_for_an_action():
    from src.layout_repost import needs_you
    assert needs_you("🌙", persistent=True) and not needs_you("🌙", persistent=False)
    assert needs_you("", persistent=True) and needs_you("⏱", persistent=False)
    assert not needs_you("📊", persistent=True)


def test_an_expired_transient_notice_is_not_brought_back(db, monkeypatch):
    bot, chat = make_bot(db, monkeypatch)
    _earlier_alert(db, chat, "⚠️ Scheduled posting hit an error: Graph is down.", "transient", hours_left=-1.0)
    bot.layout.repost.migrate()
    assert _earlier_items(db) == []


def test_a_notice_recorded_by_this_build_keeps_its_own_actions(db, monkeypatch):
    """The earlier chat run by this build (the switch off) keeps each notice's buttons by its message id: at the
    clean chat's first start a blocked notice's item offers them (🧪 Simulate publish, 📨 Resend), not only Status."""
    bot, chat = make_bot(db, monkeypatch)
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    buttons = [{"text": "🧪 Simulate Publish Only", "callback_data": "simpub:r7"},
               {"text": "📨 Resend review card", "callback_data": "resend:r7"}]
    mid = bot.notify("🛑 <b>Blocked: unlicensed voice</b>\n\nTopic: <b>Petra</b>", buttons, group="needs")
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True)
    bot.layout.repost.migrate()
    items = _earlier_items(db)
    assert mid in chat.deleted
    assert [(i["section"], i["title"]) for i in items] == [("needs", "Blocked: unlicensed voice")]
    assert items[0]["actions"] == buttons


def _needs_headings(path):
    """Heading marks of the Needs-you senders (section or group "needs") of a module: a literal text, or a local
    name the text was assigned in the same function. Texts made elsewhere (a function's result) are not read."""
    import ast
    from pathlib import Path

    from src import notifications
    root = Path(__file__).resolve().parent.parent

    def lead(node):
        while isinstance(node, ast.BinOp):
            node = node.left
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
            return node.values[0].value
        return None
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    found = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        names = {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        names.setdefault(t.id, []).append(n.value)
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if name not in ("notify", "_alert", "_notice", "say"):
                continue
            words = {k.arg: k.value for k in n.keywords}
            if not any(isinstance(words.get(w), ast.Constant) and words[w].value == "needs" for w in ("section", "group")):
                continue
            args = n.args[1:] if name == "say" or (name == "_notice" and isinstance(n.func, ast.Name)) else n.args
            if not args:
                continue
            for value in (names.get(args[0].id, []) if isinstance(args[0], ast.Name) else [args[0]]):
                text = lead(value)
                if text is not None:
                    found[f"{path}:{n.lineno}"] = notifications.parse(text)[0]
    return found


def test_every_needs_you_senders_heading_mark_is_a_needs_mark():
    """Review 2 (UI-01f blocking 1): the migration knows a Needs-you notice by its heading. Every Needs-you sender's
    heading in main.py and src/ carries a mark of NEEDS_MARKS, and no informational mark is also a Needs-you one."""
    from pathlib import Path

    from src import worker_deadline
    from src.layout_repost import INFO_MARKS, NEEDS_MARKS
    root = Path(__file__).resolve().parent.parent
    files = ["main.py"] + sorted(f"src/{p.name}" for p in (root / "src").glob("*.py"))
    marks = {where: mark for path in files for where, mark in _needs_headings(path).items()}
    assert len(marks) >= 40                                                  # the scan reads the senders
    assert {where: mark for where, mark in marks.items() if not mark.startswith(NEEDS_MARKS)} == {}
    deadline = worker_deadline.deadline_notice("Petra", 90, "Its files are kept.")
    assert deadline.startswith(NEEDS_MARKS)
    assert [m for m in INFO_MARKS if m.startswith(NEEDS_MARKS) or any(n.startswith(m) for n in NEEDS_MARKS)] == []
