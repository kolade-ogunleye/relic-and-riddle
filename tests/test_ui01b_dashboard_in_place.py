"""UI-01b: the dashboard is drawn in place and loses its Needs-you block (design
docs/design/telegram-clean-chat-2026-11-11.md §6.1.2, §6.2; mock-ups 1 and 6).

What needs the operator lives in the pinned notifications panel as condition items that resolve themselves; the
dashboard keeps the state of production and is never sent fresh at the bottom (only the ordered rebuild sends a
new copy). A fake Bot API keeps ids, edits, pins and deletions; nothing calls Telegram."""
import pytest

from src import config


@pytest.fixture(autouse=True)
def clean_chat(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    monkeypatch.setattr(config, "PUSH_ALERT_SECONDS", 8.0, raising=False)


class FakeTelegram:
    def __init__(self):
        self.next_id, self.calls, self.messages, self.deleted, self.pinned = 100, [], {}, set(), []

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
        if method == "pinChatMessage":
            self.pinned.append(int(data["message_id"]))
        return True

    def sends(self):
        return [d for m, d in self.calls if m == "sendMessage"]


def make_bot(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    monkeypatch.setattr(bot, "_api", fake)
    monkeypatch.setattr(bot, "_queue_navigation", lambda work: work() or True)
    monkeypatch.setattr(bot, "_delete_soon", bot.delete_message)
    return bot, fake


def notes():
    from src import notifications
    return notifications


def conditions():
    from src import notice_conditions
    return notice_conditions


def a_pending_review(db, tmp_path, run_id="rv1", expires=None):
    topic = db.list_all_topics()[0]["id"]
    db.create_run(run_id, topic)
    (tmp_path / f"{run_id}.mp4").write_bytes(b"x")
    db.update_run(run_id, "pending_approval", master_video_path=tmp_path / f"{run_id}.mp4",
                  script_payload={"title": f"Title {run_id}", "topic_id": topic})
    db.record_approval(run_id, topic, message_id=None)
    if expires:
        with db._conn() as conn:
            conn.execute("UPDATE approvals SET expires_at = ? WHERE run_id = ?", (expires, run_id))
    return run_id


def open_by_key(db, prefix):
    return [i for i in notes().open_items(db) if i["dedupe_key"].startswith(prefix)]


# ------------------------------------------------------------------ the dashboard loses its Needs-you block
def test_the_dashboard_keeps_production_and_drops_needs_you(db, tmp_path):
    from src.control import dashboard_text_and_buttons
    a_pending_review(db, tmp_path)
    text, buttons = dashboard_text_and_buttons(db, "Thu 09:00", assets_dir=tmp_path)
    assert "Needs you" not in text and "Nothing needs you" not in text and "Last review" not in text
    assert "<b>Production</b>" in text and "<b>This month</b>" in text and "<b>Last 7 days</b>" in text
    assert buttons[0] == [{"text": "🎬 Reviews (1)", "callback_data": "creviews:"}]   # still the way in
    flat = [b["text"] for row in buttons for b in row]
    assert not any(t.startswith(("🧭 Proposals", "🚀 Post for real", "🔁 ", "📰 Weekly")) for t in flat)


def test_the_switch_off_keeps_the_earlier_dashboard(db, tmp_path, monkeypatch):
    from src.control import dashboard_text_and_buttons
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    a_pending_review(db, tmp_path)
    text, _ = dashboard_text_and_buttons(db, "Thu 09:00", assets_dir=tmp_path)
    assert "<b>Needs you</b>" in text


# ------------------------------------------------------------------ condition items
def test_a_pending_review_is_a_needs_you_item_that_resolves_itself(db, tmp_path):
    run = a_pending_review(db, tmp_path, expires=1_900_000_000.0)
    counts = conditions().sync(db)
    assert counts["added"] >= 1
    [item] = open_by_key(db, "cond:reviews")
    assert item["section"] == "needs" and item["title"] == "Review waiting" and item["emoji"] == "🎬"
    assert item["actions"] == [{"text": "🎬 Reviews", "callback_data": "creviews:"}]
    assert "closes first, at" in item["body"]
    assert notes().pending_pushes(db) and notes().pending_pushes(db)[0]["alert"].startswith("Review waiting · ")
    # no Dismiss on a condition: it resolves itself
    notes().set_view(db, 1, item["id"], 1)
    _, rows = notes().render(db)
    assert [b["text"] for b in rows[0]] == ["🎬 Reviews"] and rows[-1][0]["text"] == "← Back to list"
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET decision = 'approved' WHERE run_id = ?", (run,))
    conditions().sync(db)
    assert open_by_key(db, "cond:reviews") == [] and notes().get(db, item["id"])["state"] == "resolved"


def test_an_unchanged_condition_writes_nothing_and_a_new_review_rings_again(db, tmp_path):
    a_pending_review(db, tmp_path, "rv1", expires=1_900_000_000.0)
    conditions().sync(db)
    notes().clear_pushes(db, [i["id"] for i in notes().pending_pushes(db)])
    rev = notes().revision(db)
    conditions().sync(db)
    assert notes().revision(db) == rev                       # nothing changed: no redraw
    a_pending_review(db, tmp_path, "rv2", expires=1_900_000_100.0)
    conditions().sync(db)
    [item] = open_by_key(db, "cond:reviews")
    assert item["title"] == "Reviews waiting" and item["subject"] == "2"
    assert [i["id"] for i in notes().pending_pushes(db)] == [item["id"]]   # the count rose: it rings


def test_the_late_failure_item_carries_re_render_and_details(db, tmp_path, monkeypatch):
    from src import control
    topic = db.list_all_topics()[0]["id"]
    db.create_run("late1", topic)
    db.update_run("late1", "failed", error="RuntimeError: the voiceover timed out", master_video_path=tmp_path / "m.mp4")
    monkeypatch.setattr(control, "_worth_resuming", lambda r: True)
    conditions().sync(db)
    [item] = open_by_key(db, "cond:late:")
    assert item["section"] == "needs" and item["run_id"] == "late1"
    assert [a["text"] for a in item["actions"]] == ["Re-render", "Details"]
    assert item["actions"][0]["callback_data"] == f"rerender:{db.run_key('late1')}"
    assert "voiceover timed out" in item["body"]


def test_an_empty_queue_and_account_problems_are_needs_you_items(db, tmp_path, monkeypatch):
    from src import accounts
    with db._conn() as conn:
        conn.execute("UPDATE topics SET status = 'published'")
    monkeypatch.setattr(accounts, "cached", lambda db_: {"problems": ["Instagram token expired"]})
    conditions().sync(db)
    [queue] = open_by_key(db, "cond:empty-queue")
    assert [a["callback_data"] for a in queue["actions"]] == ["cideas:", "qrefresh:"]
    [acct] = open_by_key(db, "cond:accounts")
    assert "Instagram token expired" in acct["body"] and acct["actions"][0]["callback_data"] == "caccounts:"


def test_a_dismissed_info_condition_stays_dismissed_until_it_changes(db, monkeypatch):
    from src import control
    digest = {"label": "Week of 2-8 Nov"}
    monkeypatch.setattr(control, "weekly_digest", lambda db_: digest)
    conditions().sync(db)
    [item] = open_by_key(db, "cond:digest")
    assert item["section"] == "info" and item["actions"] == [{"text": "Open digest", "callback_data": "cdigest:"}]
    notes().dismiss_all_info(db)
    conditions().sync(db)
    assert open_by_key(db, "cond:digest") == []
    digest["label"] = "Week of 9-15 Nov"
    conditions().sync(db)
    assert len(open_by_key(db, "cond:digest")) == 1


def test_a_run_failure_item_resolves_once_the_run_is_made_again(db):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("f1", topic)
    db.update_run("f1", "failed", error="x")
    notes().post(db, "needs", "fail:f1", "Run failed", run="f1")
    conditions().sync(db)
    assert len(open_by_key(db, "fail:f1")) == 1
    db.update_run("f1", "producing")
    conditions().sync(db)
    assert open_by_key(db, "fail:f1") == []


# ------------------------------------------------------------------ never sent fresh
def test_fresh_dashboard_requests_are_drawn_in_place(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr("src.notice_conditions.current", lambda db_: [])
    first = bot.show_dashboard()
    notes_id = int(db.get_telegram_card("layout:notifications")["message_id"])
    assert notes_id < first                                  # the pinned panel first, the dashboard under it
    fake.calls.clear()
    for text in ("⏳ <b>Working…</b>", "🤔 I don't know <code>/x</code>.", "Upload cancelled."):
        assert bot.show_dashboard(text, [[{"text": "❓ Help", "callback_data": "chelp:Everyday"}]], fresh=True) == first
    assert fake.sends() == [] and int(db.get_setting("telegram_dashboard_id")) == first
    assert bot._typed_panel()[0] == first and fake.sends() == []


def test_start_posts_the_layout_again_in_order_and_deletes_the_old_copies(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr("src.notice_conditions.current", lambda db_: [])
    old_dash = bot.show_dashboard()
    old_notes = int(db.get_telegram_card("layout:notifications")["message_id"])
    bot._process_update({"update_id": 9, "message": {"message_id": 500, "text": "/start", "chat": {"id": 100},
                                                     "from": {"id": 7}, "date": 4_000_000_000}}, lambda *a: "")
    new_notes = int(db.get_telegram_card("layout:notifications")["message_id"])
    new_dash = int(db.get_setting("telegram_dashboard_id"))
    assert old_notes < old_dash < new_notes < new_dash
    assert {old_notes, old_dash, 500} <= fake.deleted and fake.pinned[-1] == new_notes


def test_the_dashboard_goes_home_after_fifteen_idle_minutes(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr("src.notice_conditions.current", lambda db_: [])
    dash = bot.show_dashboard()
    bot._panel_screen[dash] = "cposts:"
    bot._dashboard_touched = 1000.0
    assert not bot.dashboard_idle_tick(1000.0 + 14 * 60)
    assert bot.dashboard_idle_tick(1000.0 + 15 * 60)
    assert bot._panel_screen[dash] == "cdash:" and "RELIC" in fake.messages[dash]["text"]
    bot._panel_screen[dash] = "upload:x"                     # a wizard mid-step waits 30 minutes
    bot._dashboard_touched = 5000.0
    assert not bot.dashboard_idle_tick(5000.0 + 20 * 60) and bot.dashboard_idle_tick(5000.0 + 30 * 60)


def test_a_job_that_ends_after_the_operator_moved_on_is_an_info_item(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr("src.notice_conditions.current", lambda db_: [])
    dash = bot.show_dashboard()
    done = {}

    class Jobs:
        active: dict = {}  # noqa: RUF012 - a stand-in, read only

        def submit(self, pool, title, work, complete, resource, announce):
            job = {"id": "j1", "state": "completed"}
            announce(job)
            bot._panel_revisions[dash] = bot._panel_revisions.get(dash, 0) + 5   # the operator tapped elsewhere
            done["delivered"] = complete(job, ("🖼 <b>Photo set</b>\nPyramids: 11 photos found.",
                                               [[{"text": "🖼 Open set", "callback_data": "imagehelp:pyr"}]]))
    bot.jobs = Jobs()
    monkeypatch.setattr(bot, "_background", lambda: None)
    fake.calls.clear()
    bot.start_operator_job("Photo search", lambda: None, resource="fetchimages:pyr", navigation=(dash, False, 0), late=True)
    assert done["delivered"] is True
    [item] = [i for i in notes().open_items(db) if i["dedupe_key"] == "job:j1"]
    assert item["section"] == "info" and item["title"] == "Photo search done"
    assert {"text": "🖼 Open set", "callback_data": "imagehelp:pyr"} in item["actions"]
    assert not [d for d in fake.sends() if "Pyramids" in d.get("text", "")]   # no message, no fresh dashboard


def test_a_screen_asked_for_from_an_older_card_is_drawn_in_the_dashboard(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr("src.notice_conditions.current", lambda db_: [])
    dash = bot.show_dashboard()
    fake.messages[77] = {"text": "an older card"}
    fake.calls.clear()
    assert bot.show_navigation_panel(77, "🗓 <b>Posting</b>", [[{"text": "x", "callback_data": "cposts:"}]])
    edits = [d for m, d in fake.calls if m == "editMessageText"]
    assert [int(d["message_id"]) for d in edits] == [dash] and fake.sends() == []
    assert int(db.get_setting("telegram_dashboard_id")) == dash


# ------------------------------------------------------------------ round 2: one lock order, the renderer alone sends
class BlockingTelegram(FakeTelegram):
    """The fake Bot API whose first panel send waits until the test lets it go (a slow Telegram)."""

    def __init__(self):
        super().__init__()
        import threading
        self.entered, self.go = threading.Event(), threading.Event()
        self.blocked = False

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "sendMessage" and str(data.get("text", "")).startswith("🔔") and not self.blocked:
            self.blocked = True
            self.entered.set()
            self.go.wait(10)
        return super().__call__(method, timeout, **kw)


def test_a_dashboard_drawn_during_a_layout_rebuild_never_deadlocks(db, monkeypatch):
    """Review blocking 1: thread A rebuilds the layout (blocked in its sendMessage), thread B draws the dashboard
    with the panel missing (a Telegram "Clear history"). Both finish; the panel is sent once and pinned, the
    dashboard sits under it."""
    import threading

    from src.telegram_layout import drop_slot, slot_message
    bot, _ = make_bot(db, monkeypatch)
    fake = BlockingTelegram()
    fake.blocked = True                      # the first layout is posted without waiting
    monkeypatch.setattr(bot, "_api", fake)
    bot.show_dashboard("🏛️ <b>Dashboard</b>", [[{"text": "x", "callback_data": "cdash:"}]])
    drop_slot(db, "notifications")           # the panel is gone, and the dashboard with it
    fake.deleted.add(int(db.get_setting("telegram_dashboard_id")))
    panels_before = sum(1 for d in fake.sends() if d["text"].startswith("🔔"))
    fake.blocked = False
    a = threading.Thread(target=bot.layout.rebuild, daemon=True)
    b = threading.Thread(target=lambda: bot.show_dashboard("🏛️ <b>Dashboard</b> again",
                                                           [[{"text": "x", "callback_data": "cdash:"}]]), daemon=True)
    a.start()
    assert fake.entered.wait(5)
    b.start()
    b.join(0.5)
    fake.go.set()
    a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive(), "the dashboard and the layout waited on each other"
    panel = slot_message(db, "notifications")
    dashboard = int(db.get_setting("telegram_dashboard_id"))
    assert panel and dashboard > panel
    assert sum(1 for d in fake.sends() if d["text"].startswith("🔔")) == panels_before + 1   # one panel, not two
    assert fake.messages[dashboard]["text"].startswith("🏛️ <b>Dashboard</b> again")


def test_the_layout_never_draws_the_dashboard_while_holding_its_own_lock(db, monkeypatch):
    """The lock order, checked where it can break: a tick with the panel missing, an edit that finds it gone,
    and a tap on the panel; show_dashboard is never entered while Layout._lock is held by the caller."""
    from src.telegram_layout import drop_slot, slot_message
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr(conditions(), "current", lambda db_: [])
    held = []
    real = bot.show_dashboard

    def watched(*a, **k):
        held.append(bot.layout._lock._is_owned())
        return real(*a, **k)
    monkeypatch.setattr(bot, "show_dashboard", watched)
    real("🏛️ <b>Dashboard</b>", [[{"text": "x", "callback_data": "cdash:"}]])
    bot.layout.tick(force=True)                         # the panel made, the dashboard re-sent under it
    first = slot_message(db, "notifications")
    fake.deleted.add(first)                             # gone: the next edit finds it missing
    notes().post(db, notes().NEEDS, "k", "Something", now=1.0)
    bot.layout.tick(force=True)
    assert slot_message(db, "notifications") not in (None, first)
    drop_slot(db, "notifications")
    bot.layout.tick(force=True)
    notes().post(db, notes().INFO, "i", "Info", now=2.0)
    fake.deleted.add(slot_message(db, "notifications"))
    bot._notifications_tap({"id": "cq1", "from": {"id": 7}, "message": {"message_id": 1, "chat": {"id": 100}},
                            "data": "np:1"}, None)
    assert held and not any(held), held


def test_only_the_renderer_sends_the_panel(db, monkeypatch):
    """Design §5.4: another process holds the renderer lease, so this one draws its dashboard but never sends a
    notifications panel of its own."""
    from src import telegram_layout
    bot, fake = make_bot(db, monkeypatch)
    assert telegram_layout.acquire(db, owner="the-daemon")
    assert bot.layout.ensure() is None and bot.layout.rebuild() is None
    bot.show_dashboard("🏛️ <b>Dashboard</b>", [[{"text": "x", "callback_data": "cdash:"}]])
    assert telegram_layout.slot_message(db, "notifications") is None
    assert len(fake.sends()) == 1 and "Dashboard" in fake.sends()[0]["text"]


def test_the_weekly_digest_arrives_silently(db, monkeypatch):
    """Review blocking 2: info items arrive silently. The digest is the info item "Weekly digest ready", and
    nothing rings: no nudge, no push alert."""
    bot, fake = make_bot(db, monkeypatch)
    nudged = []
    monkeypatch.setattr(bot, "nudge", lambda text: nudged.append(text))
    from src import control
    monkeypatch.setattr(control, "weekly_digest", lambda db_: {"label": "Week 45"})
    bot.layout.tick(force=True)
    sent_before = len(fake.sends())
    conditions().announce_digest(db, bot)
    bot.layout.tick(force=True)
    digest = [i for i in notes().open_items(db) if i["dedupe_key"] == "cond:digest"]
    assert len(digest) == 1 and digest[0]["section"] == "info" and digest[0]["title"] == "Weekly digest ready"
    assert nudged == [] and len(fake.sends()) == sent_before      # nothing sent: no nudge, no alert
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    conditions().announce_digest(db, bot)
    assert nudged == ["🔔 Dashboard updated: this week's digest is ready."]   # the earlier chat as it was


@pytest.mark.parametrize("condition_first", [True, False])
def test_a_failure_said_by_main_and_the_late_failure_condition_are_one_item(db, tmp_path, monkeypatch, condition_first):
    """Review non-blocking (UI-01c blocking 1 starts here): main.py's "❌ Run failed" item fail:<run> and the
    late-failure condition for the same run are one Needs-you item, with the real conditions and either order."""
    import main
    from src import control
    bot, _fake = make_bot(db, monkeypatch)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("late2", topic)
    db.update_run("late2", "failed", error="RuntimeError: the voiceover timed out")
    monkeypatch.setattr(control, "_worth_resuming", lambda r: True)
    coordinator = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coordinator.db, coordinator.telegram_bot = db, bot
    if condition_first:
        conditions().sync(db)
        assert [i["dedupe_key"] for i in open_by_key(db, "cond:late:")] == ["cond:late:late2"]
    main._notice(coordinator, "❌ <b>Run failed</b>\n\n<b>Late</b>\n<blockquote expandable>timed out</blockquote>",
                 section="needs", key="fail:late2", run="late2", actions=notes().failure_actions(db, "late2"))
    conditions().sync(db)
    conditions().sync(db)
    mine = [i for i in notes().open_items(db) if "late2" in i["dedupe_key"]]
    assert [(i["dedupe_key"], i["section"], i["title"]) for i in mine] == [("fail:late2", "needs", "Run failed")]
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM notifications WHERE dedupe_key LIKE '%late2'").fetchone()[0] == 1
    notes().close_key(db, "fail:late2", notes().DISMISSED)       # dismissed: the condition does not bring it back
    conditions().sync(db)
    assert [i for i in notes().open_items(db) if "late2" in i["dedupe_key"]] == []


# ------------------------------------------------------------------ round 3: a typed wizard opens in the dashboard
@pytest.mark.parametrize("typed", ["/photos {topic} upload", "/photos upload {topic}"])
def test_a_typed_photos_upload_opens_its_wizard_in_the_dashboard(db, monkeypatch, typed):
    """Review 2 (UI-01b blocking 1): the typed subcommand sent "Preparing image upload…" as a new message under the
    dashboard and ran the wizard there. Design §6.2 / §10.1: the upload wizard is drawn in the dashboard."""
    import time
    bot, fake = make_bot(db, monkeypatch)
    dashboard = bot.show_dashboard()
    topic = db.list_all_topics()[0]["id"]
    before = set(fake.messages)
    fake.messages[900] = {"text": "typed"}
    bot._process_update({"update_id": 900, "message": {"message_id": 900, "chat": {"id": 100}, "from": {"id": 7},
                                                       "date": int(time.time()), "text": typed.format(topic=topic)}},
                        lambda a, b: "ok")
    assert sorted(set(fake.messages) - before - {900}) == []          # no message of its own
    state = bot.image_wizard().load(100, 7)
    assert state and state["message_id"] == dashboard and state["stage"] == "file"
    assert "Upload" in fake.messages[dashboard]["text"]
    assert bot._panel_screen.get(dashboard, "").startswith("upload:")  # the wizard's 30-minute return home
