"""UI-01c measurements: what four runs' progress panels leave in the chat.

    python measure.py <repo root>

Runs on the parent (UI-01b) and on the commit, with TELEGRAM_CLEAN_CHAT on. Four runs get a progress panel in a
fake Bot API (no network; a throwaway database in the system temp folder, no .env): one succeeds, one fails after
its fact check, one is cancelled, and one's process is killed (round 2: the run stays "producing" and its recorded
owner is a process that has ended, as when a CLI is killed; its finisher never runs; the reaper's round follows,
where the build has one). Prints the progress messages still in the chat, the failure/cancel cards drawn in them,
the items the pinned panel gained, whether the panel's keyboard carries a Refresh or a 🏠, the killed run's status,
and (round 2) the Needs-you items the one failed run has once the real condition items are synced.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="ui01c-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
sys.path.insert(0, str(ROOT))

from src import config, resume_points
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot

config.TELEGRAM_CLEAN_CHAT = True
config.TELEGRAM_CARDS_V2 = False
resume_points.worth_resuming = lambda d, r=None: True
resume_points.kept_phrase = lambda d, r=None: "the fact-checked script"


class FakeTelegram:
    def __init__(self):
        self.next_id, self.messages, self.deleted, self.cards = 1000, {}, set(), set()

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "sendMessage":
            self.next_id += 1
            self.messages[self.next_id] = data
            return {"message_id": self.next_id}
        if method.startswith("editMessage"):
            mid = int(data["message_id"])
            if mid not in self.messages or mid in self.deleted:
                raise RuntimeError("Telegram editMessageText failed: HTTP 400 Bad Request: message to edit not found")
            self.messages[mid] = {**self.messages[mid], **data}
            if "Run ID" in str(data.get("text", "")):
                self.cards.add(mid)
            return {"message_id": mid}
        if method == "deleteMessage":
            self.deleted.add(int(data["message_id"]))
        return True


def one_failure_items(bank: Path) -> int:
    """A run fails after its fact check with nothing else going on; the real condition items are synced before and
    after its finisher. Returns the Needs-you items that one failure became."""
    from src import notice_conditions, notifications
    db = PipelineDatabase(db_path=TMP / "one.db", topic_bank_file=bank)
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    bot._api = FakeTelegram()
    bot._delete_soon = bot.delete_message
    bot.start_progress_ticker = lambda run_id: None
    topic = db.list_all_topics()[0]["id"]
    db.create_run("once", topic)
    db.update_run("once", "producing", script_payload={"title": "once", "topic_id": topic})
    mid = bot.send_progress_card("once", "once")
    db.update_run("once", "failed", error="RuntimeError: no track long enough")
    notice_conditions.sync(db)
    bot.finish_progress_card(mid, "once", "once", success=False, error="no track long enough")
    notice_conditions.sync(db)
    return sum(1 for i in notifications.open_items(db) if i["section"] == "needs" and i["dedupe_key"].endswith(":once"))


def main() -> None:
    bank = TMP / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": f"0{i}_t", "title": f"Topic {i}", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}} for i in range(1, 5)]}),
                    encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / "m.db", topic_bank_file=bank)
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    bot._api = fake
    bot._delete_soon = bot.delete_message
    bot.start_progress_ticker = lambda run_id: None
    import threading
    threading.Timer.start = lambda self: None
    try:
        from src import notice_conditions
        real_conditions = notice_conditions.current
        notice_conditions.current = lambda db_: []
    except ImportError:
        notice_conditions = None
    topics = db.list_all_topics()
    panels = {}
    for k, run in enumerate(("ok", "failed", "cancelled", "killed")):
        db.create_run(run, topics[k]["id"])
        db.update_run(run, "producing", script_payload={"title": run, "topic_id": topics[k]["id"]})
        panels[run] = bot.send_progress_card(run, run)
    keyboard = json.loads(fake.messages[panels["ok"]]["reply_markup"])["inline_keyboard"]
    texts = [b["text"] for row in keyboard for b in row]
    bot.finish_progress_card(panels["ok"], "ok", "ok", success=True)
    db.update_run("failed", "failed", error="RuntimeError: no track long enough")
    bot.finish_progress_card(panels["failed"], "failed", "failed", success=False, error="no track long enough")
    db.update_run("cancelled", "cancelled")
    bot.finish_progress_card(panels["cancelled"], "cancelled", "cancelled", success=False)
    import subprocess
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    owner = {"owner_pid": ended.pid, "owner_identity": "1"}
    db.set_setting("run_owner:killed", json.dumps({"pid": ended.pid, "identity": "1"}))
    card = db.get_telegram_card("layout:progress:killed") if hasattr(db, "get_telegram_card") else None
    if card:
        db.put_telegram_card("layout:progress:killed", 0, card["message_id"], "layout", {**card["payload"], **owner})
    if hasattr(bot, "reap_progress_panels"):
        bot.reap_progress_panels()
    items = []
    try:
        from src import notifications
        items = sorted(f"{i['section']}:{i['title']}" for i in notifications.open_items(db))
    except ImportError:
        pass
    one_failure = None
    if notice_conditions is not None:
        notice_conditions.current = real_conditions
        one_failure = one_failure_items(bank)
    print(json.dumps({
        "killed_run_status": db.get_run("killed")["status"],
        "needs_you_items_for_the_one_failure": one_failure,
        "progress_messages_left": sum(1 for m in panels.values() if m not in fake.deleted),
        "failure_or_cancel_cards_drawn": len(fake.cards),
        "panel_items": items,
        "keyboard_has_refresh_or_home": any("Refresh" in t or "🏠" in t for t in texts),
    }, ensure_ascii=False, indent=1, sort_keys=True))


main()
