"""UI-01g measurements: 48 hours of the notifications panel, round by round.

    python measure.py <repo root>

Runs on the parent and on the commit, with TELEGRAM_CLEAN_CHAT on, against a fake Bot API (no network; a throwaway
database in the system temp folder, no .env). The whole process's clock is simulated and starts at a fixed moment,
so the figures are the same on every run.

The panel starts with two Needs-you items, four info items (one an Approved item whose Undo works for 30 hours), the
info "Back online" of a planned restart, and two conditions that hold for the whole time (one Needs you, one info).
A fifth info item arrives after 30 hours. The renderer then runs its round every ten minutes for 48 hours (and once
more three seconds later, as its one-second tick does, so what the round wrote is drawn). Each round reads what the
chat shows: the time on each condition item against the clock, and how long each info item has been there.

Then the cost of the check time (round 2): six hours with one condition in view and nothing changing, the renderer's
round every 20 seconds, counting the edits of the pinned panel (a day is four times that).

Then "Bot stopped" and "Back online" in the three sequences of review 1 (round 2), read from the panel as the chat
has it: an outage, a clean stop and a start 20 seconds later; the same with the start five minutes later; and a
clean stop of 13 hours during which another process writes a notice and draws the panel.

Then three single checks: 50 items paged with the page size raised to 12 (the most items on one page, against the
ten numbers there are); the review panel's Summary for the committed development run with a 2,700-character note
typed for its new version (the caption's length as Telegram counts it; its limit is 1,024); and where the carousel
preview asked for from the review panel is drawn.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="ui01g-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
sys.path.insert(0, str(ROOT))
(TMP / "data").mkdir()
for name in ("reference_facts.json", "stage_budget.json", "cover_art_direction.json"):   # read at import
    if (ROOT / "data" / name).is_file():
        shutil.copy2(ROOT / "data" / name, TMP / "data" / name)

T0 = 1_900_000_000.0
NOW = [T0]
time.time = lambda: NOW[0]     # the whole process's clock, moved on below

from PIL import Image

from src import config, notice_conditions, release, telegram_bot
from src import notifications as notes
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot

config.TELEGRAM_CLEAN_CHAT = True
config.TELEGRAM_CARDS_V2 = False
telegram_bot._video_dimensions = lambda path: {"width": 720, "height": 1280}
FIXTURE = ROOT / "tests" / "fixtures" / "ui02_review_v2" / "run_gol_gumbaz_whispering_gallery_1793278143"
HOUR = 3600.0
HOURS = 48
STEP = 600
UNDO_HOURS = 30
CONDITIONS = [
    {"key": "cond:empty-queue", "section": "needs", "emoji": "📭", "title": "Topic queue is empty", "subject": "",
     "body": "Scheduled runs are skipped until a topic is added.", "count": 1,
     "actions": [{"text": "💡 Ideas", "callback_data": "cideas:"}]},
    {"key": "cond:digest", "section": "info", "emoji": "📰", "title": "Weekly digest ready", "subject": "",
     "body": "Week 46", "count": 1, "actions": [{"text": "Open digest", "callback_data": "cdigest:"}]},
]


class FakeTelegram:
    def __init__(self):
        self.next_id, self.messages, self.deleted, self.text_edits = 1000, {}, set(), {}

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method in ("sendMessage", "sendVideo", "sendPhoto"):
            self.next_id += 1
            self.messages[self.next_id] = {**data, "kind": method}
            extra = {"video": {"file_id": f"v{self.next_id}", "cover": [{"file_id": f"c{self.next_id}"}]}} \
                if method == "sendVideo" else {"photo": [{"file_id": f"p{self.next_id}"}]} if method == "sendPhoto" else {}
            return {"message_id": self.next_id, **extra}
        if method.startswith("editMessage"):
            mid = int(data["message_id"])
            if mid not in self.messages or mid in self.deleted:
                raise RuntimeError(f"Telegram {method} failed: HTTP 400 Bad Request: message to edit not found")
            if method == "editMessageMedia":
                media = json.loads(data["media"])
                self.messages[mid] = {**self.messages[mid], "caption": media.get("caption"), "media": media}
                return {"message_id": mid, **({"photo": [{"file_id": f"p{mid}"}]} if media["type"] == "photo" else {})}
            self.messages[mid] = {**self.messages[mid], **data}
            self.text_edits[mid] = self.text_edits.get(mid, 0) + 1
            return {"message_id": mid}
        if method in ("deleteMessage", "deleteMessages"):
            ids = json.loads(data["message_ids"]) if method == "deleteMessages" else [data["message_id"]]
            self.deleted.update(map(int, ids))
        return True


def a_bot(name: str):
    bank = TMP / f"{name}.json"
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / f"{name}.db", topic_bank_file=bank)
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    bot._api = fake
    bot._delete_soon = bot.delete_message
    bot._queue_navigation = lambda work: work() or True
    bot.start_progress_ticker = lambda run_id: None
    bot.layout.clock = time.time
    bot.layout.timer = lambda *a, **k: type("T", (), {"start": lambda self: None, "daemon": True})()
    bot.layout.repost.sleep = lambda s: None
    return db, bot, fake


def panel(db, fake) -> str:
    from src.telegram_layout import slot_message
    return fake.messages[slot_message(db, "notifications")]["text"]


def shown_times(text: str, titles: list[str]) -> dict[str, int]:
    """The time each named item's list entry shows (its tg-time), from the panel as the chat has it."""
    out = {}
    for entry in text.split("\n\n"):
        for title in titles:
            stamp = re.search(r'<tg-time unix="(\d+)"', entry)
            if f"<b>{title}</b>" in entry and stamp:
                out[title] = int(stamp[1])
    return out


def timeline() -> dict:
    db, bot, fake = a_bot("timeline")
    notice_conditions.current = lambda db_: [dict(c) for c in CONDITIONS]
    bot.review.clock = time.time
    bot.review.undo_until = lambda run_id: T0 + UNDO_HOURS * HOUR
    bot.layout.tick(force=True)
    bot.show_dashboard()
    notes.post(db, notes.NEEDS, "post-failed:1", "Publish failed", "The upload was refused.", emoji="❌")
    notes.post(db, notes.NEEDS, "fail:gone", "Run failed", "No track long enough.", emoji="❌")
    notes.post(db, notes.INFO, "job:1", "Fetching images done", "12 images.", emoji="🧰")
    notes.post(db, notes.INFO, "old-messages", "Older messages stay", "Clear history once.", emoji="🧹", replace=True)
    notes.post(db, notes.INFO, "rejected:r9", "Rejected", "Nothing was posted. The topic is set aside.", emoji="❌")
    notes.post(db, notes.INFO, "posted:r1", "Approved", "Posts Sat 19:00.\nUndo until 18:45.", emoji="✅",
               subject="Petra", actions=[{"text": "Undo approval", "callback_data": "rvua:1"}], run="r1", replace=True)
    decided = next(i for i in notes.open_items(db) if i["dedupe_key"] == "posted:r1")["updated_at"]
    from src import layout_repost
    db.set_setting(layout_repost.STOPPED_KEY, json.dumps({"at": T0 - 3 * HOUR, "planned": True}))
    bot.layout.repost.back(None)                              # "Back online" after a planned restart: info
    titles = [c["title"] for c in CONDITIONS]
    needs_at_start = sum(1 for i in notes.open_items(db) if i["section"] == notes.NEEDS)
    stale_minutes, stayed, left, later = 0.0, {}, {}, False
    was = {i["dedupe_key"]: i for i in notes.open_items(db)}
    for k in range(1, HOURS * 3600 // STEP + 1):
        NOW[0] = T0 + k * STEP
        if not later and NOW[0] >= T0 + 30 * HOUR:
            notes.post(db, notes.INFO, "job:2", "Finding music done", "3 tracks.", emoji="🧰")
            later = True
        for at in (NOW[0], NOW[0] + 3):
            NOW[0] = at
            bot.layout._heartbeat = 0.0
            bot.layout.tick()
        items = {i["dedupe_key"]: i for i in notes.open_items(db)}
        for key in set(was) - set(items):
            left[key] = round((NOW[0] - T0) / HOUR, 1)
        was = items
        for key, item in items.items():
            if item["section"] == notes.INFO and not key.startswith("cond:"):
                stayed[key] = max(stayed.get(key, 0.0), (NOW[0] - float(item["created_at"])) / HOUR)
        text = panel(db, fake)
        for stamp in shown_times(text, titles).values():
            stale_minutes = max(stale_minutes, (NOW[0] - stamp) / 60)
    end = {i["dedupe_key"]: i for i in notes.open_items(db)}
    text = panel(db, fake)
    approved = next((i for i in notes.recent(db, limit=50) if i["dedupe_key"] == "posted:r1"), {})
    without_undo = {k: v for k, v in stayed.items() if k != "posted:r1"}
    return {
        "hours": HOURS,
        "needs_you_items_at_the_start": needs_at_start,
        "needs_you_items_at_the_end": sum(1 for i in end.values() if i["section"] == notes.NEEDS),
        "info_items_in_the_panel_at_the_end_written_over_a_day_before": sum(
            1 for k, i in end.items() if i["section"] == notes.INFO and not k.startswith("cond:")
            and NOW[0] - float(i["updated_at"]) > 24 * HOUR),
        "longest_an_info_item_without_a_working_undo_stayed_hours": round(max(without_undo.values()), 1),
        "back_online_stayed_hours": round(stayed["service-state"], 1),
        "approved_item_with_a_30_hour_undo_stayed_hours": round(stayed["posted:r1"], 1),
        "approved_item_time_moved_when_its_undo_ended": float(approved.get("updated_at") or 0) != float(decided),
        "oldest_time_a_condition_item_showed_minutes": round(stale_minutes, 1),
        "condition_items_saying_still_true_checked_at_the_end": notes.plain(text).count("still true · checked"),
        "items_that_left_by_time_kept_on_notices": sum(
            1 for i in notes.recent(db, limit=50) if i["state"] == "expired"),
    }


def check_time_edits() -> dict:
    """How often the pinned panel is edited while nothing changes: one condition that holds, in view, for six hours,
    the renderer's round every 20 seconds (each round checks the conditions, as its 20-second sync does, and may
    draw). The rate is steady, so a day is four times the six hours."""
    from src.telegram_layout import slot_message
    hours, every = 6, 20
    db, bot, fake = a_bot("edits")
    notice_conditions.current = lambda db_: [dict(CONDITIONS[0])]
    start = NOW[0] = NOW[0] + HOUR
    bot.layout.tick(force=True)
    bot.show_dashboard()
    mid = slot_message(db, "notifications")
    before, oldest = fake.text_edits.get(mid, 0), 0.0
    for k in range(1, hours * 3600 // every + 1):
        NOW[0] = start + k * every
        bot.layout.tick()
        for stamp in shown_times(panel(db, fake), [CONDITIONS[0]["title"]]).values():
            oldest = max(oldest, (NOW[0] - stamp) / 60)
    edits = fake.text_edits.get(slot_message(db, "notifications"), 0) - before
    return {"hours": hours, "conditions_checked_every_seconds": every,
            "edits_of_the_pinned_panel_with_nothing_changed": edits,
            "edits_a_day_at_that_rate": edits * 24 // hours,
            "oldest_time_the_condition_item_showed_minutes": round(oldest, 1),
            "says_still_true_at_the_end": "still true · checked" in notes.plain(panel(db, fake))}


def service_state() -> dict:
    """What the panel says about the bot itself, in the three sequences of review 1 (blocking 3a, 3b, 3c)."""
    from src import layout_repost
    request = "Posting and"

    def start(name):
        db, bot, fake = a_bot(name)
        notice_conditions.current = lambda db_: []
        NOW[0] += HOUR
        bot.layout.tick(force=True)
        bot.show_dashboard()
        return db, bot, fake

    def read(db, bot, fake):
        """The list as the chat has it, and the item's page (its full text) as a tap on its number shows it."""
        bot.layout._heartbeat = 0.0
        bot.layout.tick(force=True)
        listed = notes.plain(panel(db, fake))
        item = next((i for i in notes.open_items(db) if i["dedupe_key"] == "service-state"), None)
        page = ""
        if item:
            notes.set_view(db, 1, item["id"], 1, NOW[0])
            bot.layout.tick(force=True)
            page = notes.plain(panel(db, fake))
            notes.set_view(db, 1, None, 1, NOW[0])
        return listed, page, item

    def outage_then_stop(name, back_after):
        db, bot, fake = start(name)
        db.set_setting(layout_repost.STOPPED_KEY, json.dumps({"at": NOW[0] - 3 * HOUR, "planned": False}))
        bot.layout.repost.back(None)                          # an unplanned outage of three hours: Needs you
        NOW[0] += HOUR
        bot.layout.repost.stopping()                          # a clean stop (a release install)
        NOW[0] += back_after
        bot.layout.repost.back(None)
        NOW[0] += 72 * HOUR                                   # the bot has been running for three days
        listed, page, item = read(db, bot, fake)
        return {"panel_of_the_running_bot_says_bot_stopped": "Bot stopped" in listed,
                "panel_says_back_online": "Back online" in listed,
                "item_is_under_needs_you": bool(item) and item["section"] == notes.NEEDS,
                "its_page_still_asks_to_check_posting_and_history": request in page}

    db, bot, fake = start("stopped")
    bot.layout.repost.stopping()
    NOW[0] += 13 * HOUR
    notes.post(db, notes.INFO, "job:9", "Fetching images done", "12 images.", emoji="🧰")   # another process's notice
    listed, _page, _item = read(db, bot, fake)
    return {"outage_clean_stop_start_after_20_seconds": outage_then_stop("quick", 20),
            "outage_clean_stop_start_after_5_minutes": outage_then_stop("later", 300),
            "stopped_13_hours_another_process_draws": {"panel_still_says_bot_stopped": "Bot stopped" in listed}}


def paging() -> dict:
    db, _bot, _fake = a_bot("paging")
    for k in range(30):
        notes.post(db, notes.INFO, f"i{k}", f"Info {k}", "A line of info.", emoji="📮", now=T0 + k)
    for k in range(20):
        notes.post(db, notes.NEEDS, f"n{k}", f"Needs {k}", "Something to act on.", emoji="❌", now=T0 + 1000 + k)
    items = notes.open_items(db)
    book = notes.pages(items, T0 + 2000)
    firsts = 0
    for number in range(1, len(book) + 1):
        notes.set_view(db, number)
        text, rows = notes.render(db, T0 + 2000)
        entries = [ln for ln in notes.plain(text).splitlines() if ln and ln[0] in notes.CIRCLED]
        firsts += int(bool(entries) and entries[0][0] == notes.CIRCLED[0]
                      and [b["callback_data"] for b in rows[0]] == [f"n:{i['id']}" for i in book[number - 1]])
    notes.PAGE_ITEMS = 12
    wide = notes.pages(items, T0 + 2000)
    notes.PAGE_ITEMS = 5
    return {"pages_of_50_items": len(book),
            "pages_numbered_from_1_with_each_button_on_its_own_item": firsts,
            "most_items_on_a_page_with_the_page_size_at_12": max(len(p) for p in wide),
            "numbers_there_are": len(notes.CIRCLED)}


def review() -> dict:
    from src import carousel_lazy
    from src.control import make_command_handler
    from src.review_panel import visible_len
    db, bot, fake = a_bot("review")
    notice_conditions.current = lambda db_: []
    bot.layout.tick(force=True)
    dash = bot.show_dashboard()
    root = TMP / "m_review"
    shutil.copytree(FIXTURE, root)
    report = json.loads((root / "release_report.json").read_text(encoding="utf-8"))
    report["checks"] = [{**c, "status": "pass"} for c in report["checks"]]
    (root / "release_report.json").write_text(json.dumps(report), encoding="utf-8")
    release.write_release_report = lambda run, dry_run=False: report
    (root / "master.mp4").write_bytes(b"offline review video")
    Image.new("RGB", (20, 20), "blue").save(root / "thumbnail_cover.jpg")
    (root / carousel_lazy.PLAN).write_text("{}", encoding="utf-8")
    stage = root / carousel_lazy.STAGE
    (stage / "carousel").mkdir(parents=True)
    Image.new("RGB", (20, 20), "green").save(stage / carousel_lazy.SHEET)
    Image.new("RGB", (20, 20), "green").save(stage / "carousel" / "carousel_slide_01.jpg")
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    topic = db.list_all_topics()[0]["id"]
    payload["topic_id"] = topic
    db.create_run("m_review", topic)
    db.update_run("m_review", "pending_approval", script_payload=payload, master_video_path=root / "master.mp4",
                  thumbnail_cover_path=root / "thumbnail_cover.jpg")
    mid = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, "m_review")
    usual = visible_len(fake.messages[mid].get("caption") or "")
    db.set_setting("regenerating:m_review", "Note " + " ".join(f"word{k}" for k in range(400)))
    bot.review.refresh()
    long_note = visible_len(fake.messages[mid].get("caption") or "")
    db.set_setting("regenerating:m_review", "")
    handler = make_command_handler(db, lambda: "Mon 07:00", lambda: "", send_photo=bot.send_photo, run_actions={
        "carpreview": lambda run_id: {"run_dir": str(root), "slides": {"carousel": [{"name": "carousel_slide_01.jpg"}]},
                                      "alts": {"carousel": ["A slide."]}}})
    bot._background = lambda: type("Now", (), {"submit": lambda self, work: work()})()
    bot.start_operator_job = lambda title, work, **k: work()
    key = db.run_key("m_review")

    def tap(data):
        bot._process_update({"callback_query": {"id": "q" + data, "from": {"id": 7}, "data": data,
                                                "message": {"message_id": mid, "chat": {"id": 100}}}}, handler)
    tap(f"rvt:{key}:pst")
    rows = json.loads(fake.messages[mid]["reply_markup"])["inline_keyboard"]
    button = next(b for row in rows for b in row if "Preview carousel" in b["text"])
    before = {m: dict(fake.messages[m]) for m in (mid, dash)}
    tap(button["callback_data"])
    drawn_in = [name for name, m in (("the review panel", mid), ("the dashboard", dash))
                if (fake.messages[m].get("media") or {}).get("type") == "photo" and fake.messages[m] != before[m]]
    return {"summary_caption_characters": usual,
            "summary_caption_characters_with_a_2700_character_note": long_note,
            "caption_limit": 1024,
            "carousel_preview_from_the_review_panel_is_drawn_in": drawn_in[0] if len(drawn_in) == 1 else drawn_in}


def main() -> None:
    import threading
    threading.Timer.start = lambda self: None
    out = {"timeline": timeline(), "check_time_edits": check_time_edits(),
           "service_state": service_state(), "paging": paging(),
           "review_panel": review()}
    print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))


main()
