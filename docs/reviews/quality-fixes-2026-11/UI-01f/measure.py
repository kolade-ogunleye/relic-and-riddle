"""UI-01f measurements: the layout 41 hours later, and what else is left in the chat.

    python measure.py <repo root>

Runs on the parent (UI-01e) and on the commit, with TELEGRAM_CLEAN_CHAT on, against a fake Bot API that records
when each message was sent (no network; a throwaway database in the system temp folder, no .env). The layout is
built (the pinned notifications panel, the dashboard, a progress panel for a run being made, the review panel for
the committed development run), and three stray messages are sent among them: an orphan card, a legacy notice and
an earlier build's review card. The clock then moves on 41 hours (past the 40-hour cap; Telegram refuses to delete
a message older than 48 hours) with the operator idle, and the renderer runs its round. Prints the age of the oldest
panel copy, how many panel copies are older than the 40-hour cap, whether the order holds, the stray
messages left, and whether the review video was uploaded again. Round 2: a run's worker (another process) redraws
the dashboard while it shows a picture screen (the messages the worker sent, and whether the order still holds
after the renderer's next round), then the renderer's re-post is due while Telegram refuses the dashboard's copy
for ten rounds a second apart (the panel copies sent and the pins: null on the parent, which has no re-post).
Round 3: the first start's migration of the earlier chat, with ten Needs-you notices of the earlier build's kinds
recorded once as persistent and once as transient (not yet expired): how many were deleted without an item, and
how many became info (null on the parent, which has no migration).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="ui01f-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
sys.path.insert(0, str(ROOT))
(TMP / "data").mkdir()
for name in ("reference_facts.json", "stage_budget.json", "cover_art_direction.json"):   # read at import
    if (ROOT / "data" / name).is_file():
        shutil.copy2(ROOT / "data" / name, TMP / "data" / name)

REAL_TIME = time.time
OFFSET = [0.0]
time.time = lambda: REAL_TIME() + OFFSET[0]     # the whole process's clock, moved on below

from PIL import Image

from src import config, release, telegram_bot
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot

config.TELEGRAM_CLEAN_CHAT = True
config.TELEGRAM_CARDS_V2 = False
telegram_bot._video_dimensions = lambda path: {"width": 720, "height": 1280}
FIXTURE = ROOT / "tests" / "fixtures" / "ui02_review_v2" / "run_gol_gumbaz_whispering_gallery_1793278143"
HOURS = 41


class FakeTelegram:
    def __init__(self):
        self.next_id, self.messages, self.sent_at, self.deleted, self.uploads = 1000, {}, {}, set(), 0

    def _new(self, data, kind):
        self.next_id += 1
        self.messages[self.next_id] = {**data, "kind": kind}
        self.sent_at[self.next_id] = time.time()
        return self.next_id

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        files = dict(kw.get("files") or {})
        self.uploads += sum(1 for name in files if name == "video")
        if method in ("sendMessage", "sendVideo", "sendPhoto"):
            mid = self._new(data, method)
            extra = {"video": {"file_id": f"v{mid}", "cover": [{"file_id": f"c{mid}"}]}} if method == "sendVideo" else {}
            return {"message_id": mid, **extra}
        if method.startswith("editMessage"):
            mid = int(data["message_id"])
            if mid not in self.messages or mid in self.deleted:
                raise RuntimeError(f"Telegram {method} failed: HTTP 400 Bad Request: message to edit not found")
            self.messages[mid] = {**self.messages[mid], **data}
            return {"message_id": mid}
        if method in ("deleteMessage", "deleteMessages"):
            ids = json.loads(data["message_ids"]) if method == "deleteMessages" else [data["message_id"]]
            for mid in map(int, ids):
                if mid in self.messages and time.time() - self.sent_at[mid] > 48 * 3600:
                    raise RuntimeError(f"Telegram {method} failed: HTTP 400 Bad Request: message can't be deleted")
            self.deleted.update(map(int, ids))
        return True


def round_two(bot, db, fake) -> dict:
    """Review 1 of UI-01f: a worker's dashboard redraw, and a re-post that keeps failing."""
    from src.telegram_layout import Layout, slot_message
    stored = int(db.get_setting("telegram_dashboard_id"))
    bot._record_kind(stored, media=True)                  # a contact sheet is open in the dashboard
    worker = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    worker._api = fake
    worker._delete_soon = worker.delete_message
    worker._layout = Layout(worker, owner="worker")
    before = fake.next_id
    worker.show_dashboard()                               # its run ended (finish_progress_card -> show_dashboard)
    by_worker = fake.next_id - before
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)                           # the renderer's next round
    order = [slot_message(db, "notifications"), int(db.get_setting("telegram_dashboard_id")),
             slot_message(db, "progress:m_prog"), slot_message(db, "review")]
    out = {"dashboards_sent_by_a_worker": by_worker,
           "layout_in_order_after_a_worker_redraw": None not in order and order == sorted(order)}
    repost = getattr(bot.layout, "repost", None)
    if repost is None:
        out.update(copies_sent_over_ten_failing_rounds=None, pins_over_ten_failing_rounds=None)
        return out
    real = bot._api
    pins = [0]

    def refusing(method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "pinChatMessage":
            pins[0] += 1
        if method == "sendMessage" and data.get("disable_notification") == "true" and "RELIC" in data.get("text", ""):
            raise RuntimeError("Telegram sendMessage failed: HTTP 400 Bad Request: BUTTON_DATA_INVALID")
        return real(method, timeout, **kw)
    bot._api = refusing
    repost.due = lambda now: True
    before = fake.next_id
    t0 = time.time()
    for k in range(10):
        bot.layout.clock = lambda k=k: t0 + k
        bot.layout.tick()
    bot._api = real
    out.update(copies_sent_over_ten_failing_rounds=fake.next_id - before, pins_over_ten_failing_rounds=pins[0])
    return out


EARLIER_NEEDS = ("🛑 <b>Blocked: unlicensed voice</b>", "⏱ <b>Run stopped at its deadline</b>",
                 "🗄 <b>Backups need attention</b>: no backup in 3 days", "🧾 Couldn't build the fact sheet: timeout",
                 "🔁 <b>Re-render not started</b>", "⚠️ <b>Publish unconfirmed</b>",
                 "⚠️ Scheduled posting hit an error: the upload was refused.", "❌ <b>Publish failed</b>",
                 "💥 <b>The run's worker process ended without a result</b>", "⚠️ <b>Interrupted run(s) cleared</b>")


def round_three() -> dict:
    """Review 2 of UI-01f: no Needs-you notice of the earlier chat is lost or made info by the migration."""
    keys = ("needs_you_notices_lost_at_migration", "needs_you_notices_made_info_at_migration")
    db = PipelineDatabase(db_path=TMP / "migration.db", topic_bank_file=TMP / "none.json")
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    if not hasattr(getattr(bot, "layout", None), "repost"):
        return dict.fromkeys(keys)
    from datetime import UTC, datetime, timedelta

    from src import notifications
    fake = FakeTelegram()
    bot._api = fake
    bot._delete_soon = bot.delete_message
    bot._queue_navigation = lambda work: work() or True
    bot.layout.clock = time.time
    bot.layout.repost.sleep = lambda s: None
    later = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    for kind in ("persistent", "transient"):
        for text in EARLIER_NEEDS:
            mid = fake._new({"text": text}, "sendMessage")
            fake.sent_at[mid] = time.time() - 3600        # an hour old: the bot can still delete it
            db.record_alert(text, message_id=mid, kind=kind, expires_at=later if kind == "transient" else None)
    bot.layout.repost.migrate()
    alerts = {r["id"]: r["message_id"] for r in db.list_recent_alerts(limit=100)}
    items = {int(i["dedupe_key"].split(":", 1)[1]): i["section"] for i in notifications.open_items(db)
             if i["dedupe_key"].startswith("earlier:")}
    return {keys[0]: sum(1 for a, m in alerts.items() if m in fake.deleted and a not in items),
            keys[1]: sum(1 for section in items.values() if section != "needs")}


def main() -> None:
    bank = TMP / "bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / "m.db", topic_bank_file=bank)
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    bot._api = fake
    bot._delete_soon = bot.delete_message
    bot._queue_navigation = lambda work: work() or True
    bot.start_progress_ticker = lambda run_id: None
    import threading
    threading.Timer.start = lambda self: None
    from src import notice_conditions
    notice_conditions.current = lambda db_: []
    bot.layout.clock = time.time
    if hasattr(bot.layout, "repost"):
        bot.layout.repost.sleep = lambda s: None
    bot.layout.tick(force=True)
    bot.show_dashboard()
    stray = [fake._new({"text": "a crashed CLI's card"}, "sendMessage")]
    topic = db.list_all_topics()[0]["id"]
    db.create_run("m_prog", topic)
    db.update_run("m_prog", "producing", script_payload={"title": "Petra", "topic_id": topic})
    bot.send_progress_card("m_prog", "Petra")
    stray.append(fake._new({"text": "⌛ No decision on … (an earlier build's notice)"}, "sendMessage"))
    root = TMP / "m_review"
    shutil.copytree(FIXTURE, root)
    report = json.loads((root / "release_report.json").read_text(encoding="utf-8"))
    report["checks"] = [{**c, "status": "pass"} for c in report["checks"]]
    (root / "release_report.json").write_text(json.dumps(report), encoding="utf-8")
    release.write_release_report = lambda run, dry_run=False: report
    (root / "master.mp4").write_bytes(b"offline review video")
    Image.new("RGB", (20, 20), "blue").save(root / "thumbnail_cover.jpg")
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    payload["topic_id"] = topic
    db.create_run("m_review", topic)
    db.update_run("m_review", "pending_approval", script_payload=payload, master_video_path=root / "master.mp4",
                  thumbnail_cover_path=root / "thumbnail_cover.jpg")
    bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, "m_review")
    stray.append(fake._new({"text": "🎬 Master review (an earlier build's card)"}, "sendMessage"))
    db.set_setting("layout-newest-id", str(stray[-1]))   # the newest id the chat has seen (as poll_once records it)
    uploads_before = fake.uploads
    OFFSET[0] = HOURS * 3600.0
    bot._last_activity = 0.0
    bot.layout._heartbeat = 0.0
    bot.layout.tick(force=True)                           # the renderer's round, 41 hours on
    from src.telegram_layout import panel_ids
    panels = sorted(panel_ids(db) | {int(db.get_setting("telegram_dashboard_id"))})
    live = [m for m in panels if m not in fake.deleted]
    ages = [(time.time() - fake.sent_at[m]) / 3600 for m in live]
    rows = {c["card_id"]: c["message_id"] for c in db.list_telegram_cards(topic_id=0, card_kind="layout")}
    in_order = [rows.get("layout:notifications"), int(db.get_setting("telegram_dashboard_id")),
                rows.get("layout:progress:m_prog"), rows.get("layout:review")]
    second = round_two(bot, db, fake)
    second.update(round_three())
    print(json.dumps({
        **second,
        "hours_later": HOURS,
        "panels": len(live),
        "oldest_panel_copy_hours": round(max(ages), 2),
        "panel_copies_older_than_40h": sum(1 for a in ages if a >= 40),
        "layout_in_order": in_order == sorted(in_order) and None not in in_order,
        "stray_messages_left": sum(1 for m in stray if m not in fake.deleted),
        "review_video_uploads_after_the_first": fake.uploads - uploads_before,
    }, ensure_ascii=False, indent=1, sort_keys=True))


main()
