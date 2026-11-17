"""UI-01b measurements: does the dashboard repeat the notifications, and is it ever sent fresh at the bottom?

    python measure.py <repo root>

Runs on the parent (UI-01a) and on the commit, with TELEGRAM_CLEAN_CHAT on. A throwaway database (system temp
folder, no .env: the app root is empty) is seeded with a state that needs the operator: two pending reviews, a run
that failed after its video was made, and queued topics without photo sets. Then:
- the dashboard's text is built and its "Needs you" lines are counted;
- six everyday requests that used to post a fresh dashboard or move it (a typed command's panel, an unknown
  command, /jobs, an issue reply, a screen asked for from an older card, then from a review card) are made against
  a fake Bot API: the messages sent for them are counted, and whether the older card became the dashboard;
- the condition items in the pinned panel are listed by key;
- round 2: the lock probe (thread A rebuilds the layout, blocked in its send, while thread B draws the dashboard
  with the panel missing: the threads still waiting 5 s after A is let go) and the weekly digest (the messages
  with sound it sends: the parent's daemon nudged; notice_conditions.announce_digest where it exists);
- round 3: a typed `/photos <topic> upload` (the documented subcommand that opens the upload wizard): the messages
  sent for it.
Nothing is sent anywhere; nothing in the repository is written.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="ui01b-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
sys.path.insert(0, str(ROOT))

from src import config, control
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot

config.TELEGRAM_CLEAN_CHAT = True
config.TELEGRAM_CARDS_V2 = False


class FakeTelegram:
    def __init__(self):
        self.next_id, self.messages, self.deleted, self.sent = 1000, {}, set(), []

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "sendMessage":
            self.next_id += 1
            self.messages[self.next_id] = data
            self.sent.append(self.next_id)
            return {"message_id": self.next_id}
        if method.startswith("editMessage"):
            mid = int(data["message_id"])
            if mid not in self.messages or mid in self.deleted:
                raise RuntimeError("Telegram editMessageText failed: HTTP 400 Bad Request: message to edit not found")
            self.messages[mid] = {**self.messages[mid], **data}
            return {"message_id": mid}
        if method == "deleteMessage":
            self.deleted.add(int(data["message_id"]))
        return True


def seed(db: PipelineDatabase) -> None:
    topics = db.list_all_topics()
    for k, run in enumerate(("rv1", "rv2")):
        db.create_run(run, topics[k]["id"])
        (TMP / f"{run}.mp4").write_bytes(b"x")
        db.update_run(run, "pending_approval", master_video_path=TMP / f"{run}.mp4",
                      script_payload={"title": f"Review {k + 1}", "topic_id": topics[k]["id"]})
        db.record_approval(run, topics[k]["id"], message_id=None)
    db.create_run("late", topics[2]["id"])
    db.update_run("late", "failed", error="RuntimeError: the voiceover timed out", master_video_path=TMP / "late.mp4")
    control._worth_resuming = lambda r: True


def lock_probe(bank: Path) -> int:
    """Threads still blocked after a layout rebuild and a dashboard draw ran at once (0 = no deadlock)."""
    import threading

    from src.telegram_layout import drop_slot
    db = PipelineDatabase(db_path=TMP / "probe.db", topic_bank_file=bank)
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    entered, go, state = threading.Event(), threading.Event(), {"block": False}

    def api(method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "sendMessage" and str(data.get("text", "")).startswith("🔔") and state["block"]:
            state["block"] = False
            entered.set()
            go.wait(10)
        return fake(method, timeout, **kw)
    bot._api = api
    bot._delete_soon = bot.delete_message
    bot.show_dashboard("🏛️ <b>Dashboard</b>", [[{"text": "x", "callback_data": "cdash:"}]])
    drop_slot(db, "notifications")                                 # a Telegram "Clear history": both gone
    fake.deleted.add(int(db.get_setting("telegram_dashboard_id")))
    state["block"] = True
    a = threading.Thread(target=bot.layout.rebuild, daemon=True)
    b = threading.Thread(target=lambda: bot.show_dashboard("🏛️ again", [[{"text": "x", "callback_data": "cdash:"}]]),
                         daemon=True)
    a.start()
    entered.wait(5)
    b.start()
    b.join(0.5)
    go.set()
    a.join(5)
    b.join(5)
    return int(a.is_alive()) + int(b.is_alive())


def digest_sounds(db: PipelineDatabase) -> int:
    """Messages with sound sent when this week's digest is ready."""
    try:
        from src import notice_conditions
    except ImportError:   # the parent has no condition items
        notice_conditions = None
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    bot._api = fake
    rang = []
    real = fake.__call__

    def api(method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "sendMessage" and data.get("disable_notification") not in ("true", True):
            rang.append(data.get("text"))
        return real(method, timeout, **kw)
    bot._api = api
    control.weekly_digest = lambda db_: {"label": "Week 45"}
    announce = getattr(notice_conditions, "announce_digest", None) if notice_conditions else None
    if announce:
        announce(db, bot)
        bot.layout.tick(force=True)
        rang[:] = [t for t in rang if "digest" in str(t).lower()]
    else:   # the parent's daemon: record the digest, redraw, nudge
        bot.nudge("🔔 Dashboard updated: this week's digest is ready.")
    return len(rang)


def main() -> None:
    bank = TMP / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": f"0{i}_t", "title": f"Topic {i}", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}} for i in range(1, 4)]}),
                    encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / "m.db", topic_bank_file=bank)
    seed(db)
    text, _ = control.dashboard_text_and_buttons(db, "Thu 09:00", assets_dir=TMP)
    lines = text.splitlines()
    needs_lines = 0
    if "<b>Needs you</b>" in lines:
        start = lines.index("<b>Needs you</b>") + 1
        stop = lines.index("<b>Production</b>") if "<b>Production</b>" in lines else len(lines)
        needs_lines = len([ln for ln in lines[start:stop] if ln.strip()])
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    bot._api = fake
    bot._delete_soon = bot.delete_message
    import threading
    threading.Timer.start = lambda self: None
    bot.show_dashboard()
    fake.messages[4242] = {"text": "an older card"}
    before = len(fake.sent)
    bot._typed_panel()
    bot.show_dashboard("🤔 I don't know <code>/x</code>.", [[{"text": "❓ Help", "callback_data": "chelp:Everyday"}]], fresh=True)
    bot.show_dashboard("🧰 <b>Jobs</b>", [[{"text": "x", "callback_data": "jobs:"}]], fresh=True)
    bot.show_dashboard("Thanks, noted.", None, fresh=True)
    bot.show_navigation_panel(4242, "🗓 <b>Posting</b>", [[{"text": "x", "callback_data": "cposts:"}]])
    bot.clear_protected = lambda: {4242}   # a review card: its screens opened a fresh dashboard
    bot.show_navigation_panel(4242, "🎨 <b>Covers</b>", [[{"text": "x", "callback_data": "ccov:"}]])
    sent_for_requests = len(fake.sent) - before
    older_card = db.get_setting("telegram_dashboard_id") == "4242"
    import time
    before = len(fake.sent)
    bot._process_update({"update_id": 9, "message": {"message_id": 4300, "chat": {"id": 100}, "from": {"id": 7},
                                                     "date": int(time.time()), "text": "/photos 01_t upload"}},
                        lambda a, b: "ok")
    sent_for_upload = len(fake.sent) - before
    keys = []
    try:
        from src import notice_conditions, notifications
        notice_conditions.sync(db)
        keys = sorted(i["dedupe_key"].split(":")[1] for i in notifications.open_items(db)
                      if i["dedupe_key"].startswith("cond:"))
    except ImportError:
        pass
    print(json.dumps({
        "dashboard_needs_you_lines": needs_lines,
        "dashboard_says_nothing_needs_you": "Nothing needs you" in text,
        "messages_sent_for_six_requests": sent_for_requests,
        "older_card_became_the_dashboard": older_card,
        "messages_sent_for_a_typed_photos_upload": sent_for_upload,
        "condition_items": keys,
        "lock_probe_threads_stuck": lock_probe(bank),
        "digest_messages_with_sound": digest_sounds(db),
    }, ensure_ascii=False, indent=1, sort_keys=True))


main()
