"""UI-01c: the progress panel lives exactly as long as its run (design docs/design/telegram-clean-chat-2026-11-11.md
§6.4, §7.4; mock-ups 7 and 8).

Deleted at the end; a failure becomes a Needs-you item (Re-render or Back to the queue · Details · Dismiss) that
rings once, a cancel an info item; Cancel run asks on the keyboard only, for 5 minutes; a panel whose process died
is reaped. A fake Bot API keeps ids, edits and deletions; nothing calls Telegram."""
import json

import pytest

from src import config
from src import notice_conditions as _conditions


@pytest.fixture(autouse=True)
def clean_chat(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    monkeypatch.setattr(config, "PUSH_ALERT_SECONDS", 8.0, raising=False)


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
    monkeypatch.setattr(bot, "start_progress_ticker", lambda run_id: None)
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [])
    bot.layout.timer = lambda *a, **k: type("T", (), {"start": lambda self: None, "daemon": True})()
    return bot, fake


def a_run(db, run_id="pr1", status="producing"):
    topic = db.list_all_topics()[0]["id"]
    db.create_run(run_id, topic)
    db.update_run(run_id, status, script_payload={"title": f"Title {run_id}", "topic_id": topic})
    return run_id


def keyboard(fake, mid):
    markup = fake.messages[mid].get("reply_markup")
    return json.loads(markup)["inline_keyboard"] if markup else []


def items(db, prefix=""):
    from src import notifications
    return [i for i in notifications.open_items(db) if i["dedupe_key"].startswith(prefix)]


def test_the_panel_has_cancel_run_alone_no_refresh_no_home_no_bar(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    rows = keyboard(fake, mid)
    assert rows == [[{"text": "Cancel run", "callback_data": f"pstop:{db.run_key(run)}"}]]
    assert "Progress: <code>[" not in fake.messages[mid]["text"]
    assert db.get_telegram_card(f"layout:progress:{run}")["message_id"] == mid
    from src.telegram_ui import PANEL_KINDS
    assert {"pstop", "pkeep", "pstopok"} <= PANEL_KINDS


def test_cancel_run_asks_on_the_keyboard_and_the_question_expires(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    key = db.run_key(run)

    taps = iter(range(1, 100))

    def tap(data):
        bot._progress_tap({"id": f"c{next(taps)}", "data": data, "message": {"message_id": mid}})
    tap(f"pstop:{key}")
    edits = [d for m, d in fake.calls if m == "editMessageReplyMarkup"]
    confirm = json.loads(edits[-1]["reply_markup"])["inline_keyboard"]
    assert [[b["text"] for b in row] for row in confirm] == [["← Keep making", "Cancel run"]]
    assert confirm[0][1]["style"] == "danger" and confirm[0][1]["callback_data"] == f"pstopok:{key}"
    answers = [d["text"] for m, d in fake.calls if m == "answerCallbackQuery"]
    assert answers[-1].startswith("Cancel this run? It stops at the next step")
    tap(f"pkeep:{key}")
    assert json.loads([d for m, d in fake.calls if m == "editMessageReplyMarkup"][-1]["reply_markup"])["inline_keyboard"] \
        == [[{"text": "Cancel run", "callback_data": f"pstop:{key}"}]]
    bot._stop_asked[run] = 0.0                                         # asked long ago: the question has expired
    assert bot._progress_buttons(run) == [[{"text": "Cancel run", "callback_data": f"pstop:{key}"}]]
    tap(f"pstop:{key}")
    tap(f"pstopok:{key}")
    assert db.get_setting("cancel_run:" + run) == "requested"
    assert json.loads([d for m, d in fake.calls if m == "editMessageReplyMarkup"][-1]["reply_markup"])["inline_keyboard"] == []


def test_success_deletes_the_panel(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    assert bot.finish_progress_card(mid, "Petra", run, success=True)
    assert mid in fake.deleted and db.get_telegram_card(f"layout:progress:{run}") is None
    assert items(db) == []


def test_a_failure_deletes_the_panel_and_becomes_a_needs_you_item(db, monkeypatch, tmp_path):
    bot, fake = make_bot(db, monkeypatch)
    from src import resume_points
    monkeypatch.setattr(resume_points, "worth_resuming", lambda d, r=None: True)
    monkeypatch.setattr(resume_points, "kept_phrase", lambda d, r=None: "the fact-checked script and the voiceover")
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    db.update_run(run, "failed", error="RuntimeError: the music library had no track long enough")
    assert bot.finish_progress_card(mid, "Petra", run, success=False, error="RuntimeError: no track long enough")
    assert mid in fake.deleted
    [item] = items(db, "fail:")
    assert item["section"] == "needs" and item["emoji"] == "❌" and item["subject"] == db.run_label(run)
    assert [a["text"] for a in item["actions"]] == ["Re-render", "Details"]
    assert item["actions"][0]["callback_data"] == f"rerender:{db.run_key(run)}"
    assert item["actions"][1]["callback_data"] == f"chist:{db.run_key(run)}"
    assert "keeping the fact-checked script and the voiceover" in item["body"] and "no track long enough" in item["body"]
    alerts = [d["text"] for m, d in fake.calls if m == "sendMessage" and "reply_markup" not in d]
    assert len(alerts) == 1 and alerts[0].startswith("🔔 ")
    # no failure card is drawn: the panel's message is gone, not edited into a card
    assert not [d for m, d in fake.calls if m == "editMessageText" and int(d["message_id"]) == mid]


def test_a_failure_before_the_fact_check_offers_back_to_the_queue(db, monkeypatch):
    bot, _fake = make_bot(db, monkeypatch)
    from src import resume_points
    monkeypatch.setattr(resume_points, "worth_resuming", lambda d, r=None: False)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    db.update_run(run, "failed", error="x")
    bot.finish_progress_card(mid, "Petra", run, success=False, error="the fact sheet could not be built")
    [item] = items(db, "fail:")
    assert [a["text"] for a in item["actions"]] == ["Back to the queue", "Details"]
    assert item["actions"][0]["callback_data"].startswith("requeue:")


def test_a_cancel_deletes_the_panel_and_is_an_info_item(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    db.update_run(run, "cancelled")
    bot.finish_progress_card(mid, "Petra", run, success=False)
    assert mid in fake.deleted
    [item] = items(db, "cancel:")
    assert item["section"] == "info" and item["title"] == "Render cancelled" and item["emoji"] == "⏹️"


def test_the_reaper_deletes_a_panel_whose_finisher_never_ran(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    live, dead, done = a_run(db, "live"), a_run(db, "dead"), a_run(db, "done")
    ids = {r: bot.send_progress_card(r, r) for r in (live, dead, done)}
    db.update_run(dead, "failed", error="worker killed")
    db.update_run(done, "pending_approval")
    import time
    assert bot.reap_progress_panels() == 0              # just ended: left to their finisher (round 2)
    assert bot.reap_progress_panels(now=time.time() + 31) == 2
    assert ids[live] not in fake.deleted and {ids[dead], ids[done]} <= fake.deleted
    [item] = items(db, "fail:")
    assert item["run_id"] == dead and item["title"] == "Run interrupted" and item["emoji"] == "⚠️"
    assert db.get_telegram_card("layout:progress:live") and not db.get_telegram_card("layout:progress:dead")


def test_the_switch_off_keeps_the_failure_card(db, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    db.update_run(run, "failed", error="x")
    bot.finish_progress_card(mid, "Petra", run, success=False, error="x")
    assert mid not in fake.deleted and "Run ID" in fake.messages[mid]["text"]


# ------------------------------------------------------------------ round 2: one failure, one item; reaped by owner
REAL_CONDITIONS = _conditions.current   # the real conditions, before make_bot stubs them


def a_late_failure(db, monkeypatch, tmp_path, run_id="pr1"):
    """A run that failed after its fact-checked script: the real condition "🔁 Failed after its fact check"
    holds for it (control.rerenderable, src/resume_points.py), as on a real failure."""
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    run = a_run(db, run_id)
    (tmp_path / run).mkdir()
    (tmp_path / run / "checkpoints.json").write_text(json.dumps({"script": {"done": True}}), encoding="utf-8")
    (tmp_path / run / "verified_script.json").write_text("{}", encoding="utf-8")
    return run


@pytest.mark.parametrize("sync_first", [True, False])
def test_one_failure_is_one_needs_you_item_with_the_real_conditions(db, monkeypatch, tmp_path, sync_first):
    """Review blocking 1 (mock-up 8: "🔔 1 needs you"): the progress finisher's failure item and the late-failure
    condition are one item for one failure, whichever the renderer sees first, and it rings once."""
    from src import notifications
    bot, _fake = make_bot(db, monkeypatch)
    monkeypatch.setattr(_conditions, "current", REAL_CONDITIONS)
    run = a_late_failure(db, monkeypatch, tmp_path)
    mid = bot.send_progress_card(run, "Petra")
    db.update_run(run, "failed", error="RuntimeError: the music library had no track long enough")
    if sync_first:   # the renderer's 20-s sync came between the failure and the finisher
        _conditions.sync(db)
        assert [i["dedupe_key"] for i in items(db) if i.get("run_id") == run] == [f"cond:late:{run}"]
    assert bot.finish_progress_card(mid, "Petra", run, success=False, error="RuntimeError: no track long enough")
    _conditions.sync(db)
    _conditions.sync(db)
    mine = [i for i in items(db) if i.get("run_id") == run or run in i["dedupe_key"]]
    assert [(i["dedupe_key"], i["section"]) for i in mine] == [(f"fail:{run}", "needs")]
    assert [a["text"] for a in mine[0]["actions"]] == ["Re-render", "Details"]
    assert "no track long enough" in mine[0]["body"]
    with db._conn() as conn:   # it rang once: one push for the run, whichever item was first
        rang = conn.execute("SELECT COUNT(*) FROM notifications WHERE pushed_at IS NOT NULL AND "
                            "(run_id = ? OR dedupe_key LIKE ?)", (run, f"%{run}")).fetchone()[0]
    assert rang == 1
    assert notifications.has_open(db, f"fail:{run}") and not notifications.has_open(db, f"cond:late:{run}")


def a_dead_pid():
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_the_reaper_takes_a_dead_processes_panel_and_marks_the_run_interrupted(db, monkeypatch):
    """Review blocking 2 (design §5.6, mock-up 7): the CLI making a run is killed. Its run stays "producing"; the
    renderer's next reaper pass (every 60 s) sees its process gone, deletes the panel, marks the run interrupted
    with its topic back in the queue, and says "⚠️ Run interrupted" once."""
    import main
    from src.telegram_layout import put_slot
    bot, fake = make_bot(db, monkeypatch)
    run = a_run(db, "killed")
    alive = a_run(db, "alive")
    db.record_topic_status(db.get_run(run)["topic_id"], "producing", run_id=run)
    mid = bot.send_progress_card(run, "Petra")
    kept = bot.send_progress_card(alive, "Ani")
    put_slot(db, f"progress:{run}", mid, owner_pid=a_dead_pid(), owner_identity="133000000000000000")
    clock = [1_000_000.0]
    bot.layout.clock = lambda: clock[0]
    bot.layout.tick()                                  # the renderer's pass: the reaper runs in it
    assert mid in fake.deleted and kept not in fake.deleted
    record = db.get_run(run)
    assert record["status"] == "failed" and record["error"].startswith("Interrupted:")
    topic = next(t for t in db.list_all_topics() if t["id"] == record["topic_id"])
    assert topic["status"] == "queued"
    [item] = items(db, "fail:")
    assert (item["dedupe_key"], item["title"], item["emoji"]) == (f"fail:{run}", "Run interrupted", "⚠️")
    assert [a["text"] for a in item["actions"]][-1] == "Details"
    assert db.get_run(alive)["status"] == "producing" and db.get_telegram_card(f"layout:progress:{alive}")
    # The worker-crash notice and the start-up clearing for the same run are the same item, not two more.
    main.say_interrupted(db, bot, [run], "Its worker process crashed (exit 3).")
    main.say_interrupted(db, bot, [run])
    assert [i["dedupe_key"] for i in items(db, "fail:")] == [f"fail:{run}"]
    assert bot.reap_progress_panels() == 0


def test_the_reaper_reaps_within_a_minute_of_the_owner_dying(db, monkeypatch):
    from src.telegram_layout import REAP_SECONDS, put_slot
    bot, fake = make_bot(db, monkeypatch)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    clock = [2_000_000.0]
    bot.layout.clock = lambda: clock[0]
    bot.layout.tick()                                  # a pass with the owner alive: nothing reaped
    assert mid not in fake.deleted
    put_slot(db, f"progress:{run}", mid, owner_pid=a_dead_pid(), owner_identity="1")   # killed now
    clock[0] += REAP_SECONDS - 1
    bot.layout.tick()
    assert mid not in fake.deleted
    clock[0] += 1
    bot.layout.tick()                                  # 60 s after the last pass
    assert REAP_SECONDS <= 60 and mid in fake.deleted


def test_an_owner_that_cannot_be_checked_is_never_declared_dead(db, monkeypatch):
    from src import process_state
    from src.telegram_layout import put_slot
    bot, _fake = make_bot(db, monkeypatch)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    put_slot(db, f"progress:{run}", mid, owner_pid=4242, owner_identity="1")
    monkeypatch.setattr(process_state, "identity", lambda pid: "unknown")
    assert bot.reap_progress_panels() == 0 and db.get_run(run)["status"] == "producing"


def test_a_run_that_just_ended_is_left_to_its_finisher_and_a_cancel_is_never_interrupted(db, monkeypatch):
    """Review non-blocking: the reaper never beats the run's own finisher (a 30 s grace), and a cancelled run whose
    finisher did not run is the info item "Render cancelled", not a Needs-you "Run interrupted"."""
    import time

    from src.telegram_bot import REAP_GRACE_SECONDS
    bot, fake = make_bot(db, monkeypatch)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    db.update_run(run, "cancelled")
    assert bot.reap_progress_panels(now=time.time()) == 0 and mid not in fake.deleted
    assert bot.reap_progress_panels(now=time.time() + REAP_GRACE_SECONDS + 1) == 1 and mid in fake.deleted
    assert items(db, "fail:") == []
    [item] = items(db, "cancel:")
    assert item["section"] == "info" and item["title"] == "Render cancelled"


def test_progress_brought_down_keeps_its_layout_row(db, monkeypatch):
    """Review non-blocking: /progress re-sends the panel; its layout row follows, so the finisher and the reaper
    delete the copy that is in the chat."""
    bot, fake = make_bot(db, monkeypatch)
    run = a_run(db)
    mid = bot.send_progress_card(run, "Petra")
    record = json.loads(db.get_setting("active_progress"))
    record["worker_state"] = "running"
    monkeypatch.setattr(db, "get_active_progress", lambda: record)
    owner = db.get_telegram_card(f"layout:progress:{run}")["payload"]["owner_pid"]
    assert bot.bring_progress_down()
    card = db.get_telegram_card(f"layout:progress:{run}")
    assert mid in fake.deleted and card["message_id"] not in (mid, None) and card["payload"]["owner_pid"] == owner
