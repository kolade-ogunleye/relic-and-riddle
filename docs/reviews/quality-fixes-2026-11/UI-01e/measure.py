"""UI-01e measurements: what three reviews leave in the chat, from delivery to their decisions.

    python measure.py <repo root>

Runs on the parent (UI-01d) and on the commit, with TELEGRAM_CLEAN_CHAT on, against a fake Bot API (no network; a
throwaway database in the system temp folder, no .env). Three copies of the committed development run
(tests/fixtures/ui02_review_v2, a stand-in video and cover, two Story frames; the second copy keeps its two failed
release checks, the others pass) are delivered for review, then decided: the first approved, the second rejected
with a reason, the third left to time out. On the commit the operator also opens every tab of every review.
Prints the review messages sent, the files uploaded, the messages the reviews leave in the chat, what paging and
the tabs sent, and the longest caption a face used.
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
TMP = Path(tempfile.mkdtemp(prefix="ui01e-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
sys.path.insert(0, str(ROOT))
(TMP / "data").mkdir()
for name in ("reference_facts.json", "stage_budget.json", "cover_art_direction.json"):   # read at import
    if (ROOT / "data" / name).is_file():
        shutil.copy2(ROOT / "data" / name, TMP / "data" / name)

from PIL import Image

from src import config, release, telegram_bot
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot

config.TELEGRAM_CLEAN_CHAT = True
config.TELEGRAM_CARDS_V2 = False
telegram_bot._video_dimensions = lambda path: {"width": 720, "height": 1280}
FIXTURE = ROOT / "tests" / "fixtures" / "ui02_review_v2" / "run_gol_gumbaz_whispering_gallery_1793278143"


class FakeTelegram:
    def __init__(self):
        self.next_id, self.messages, self.deleted, self.calls, self.uploads = 1000, {}, set(), [], 0

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        files = dict(kw.get("files") or {})
        self.calls.append(method)
        self.uploads += len(files)
        if method in ("sendMessage", "sendVideo", "sendPhoto", "sendMediaGroup"):
            count = len(json.loads(data["media"])) if method == "sendMediaGroup" else 1
            ids = []
            for _ in range(count):
                self.next_id += 1
                self.messages[self.next_id] = {**data, "kind": method}
                ids.append(self.next_id)
            extra = {"video": {"file_id": f"v{ids[0]}", "cover": [{"file_id": f"c{ids[0]}"}]}} if method == "sendVideo" else {}
            if method == "sendPhoto":
                extra = {"photo": [{"file_id": f"p{ids[0]}"}]}
            return [{"message_id": i} for i in ids] if method == "sendMediaGroup" else {"message_id": ids[0], **extra}
        if method.startswith("editMessage"):
            mid = int(data["message_id"])
            if mid not in self.messages or mid in self.deleted:
                raise RuntimeError(f"Telegram {method} failed: HTTP 400 Bad Request: message to edit not found")
            media = json.loads(data["media"]) if method == "editMessageMedia" else {}
            self.messages[mid] = {**self.messages[mid], **data, **({"caption": media.get("caption")} if media else {})}
            if media and files:
                kind = media["type"]
                return {"message_id": mid, **({"video": {"file_id": f"v{mid}x", "cover": [{"file_id": f"c{mid}x"}]}}
                                               if kind == "video" else {"photo": [{"file_id": f"p{mid}x"}]})}
            return {"message_id": mid}
        if method == "deleteMessage":
            self.deleted.add(int(data["message_id"]))
        if method == "deleteMessages":
            self.deleted.update(json.loads(data["message_ids"]))
        return True


def a_run(db, run_id: str, passing: bool, reports: dict) -> tuple[Path, dict]:
    root = TMP / run_id
    shutil.copytree(FIXTURE, root)
    report = json.loads((root / "release_report.json").read_text(encoding="utf-8"))
    if passing:
        report["checks"] = [{**c, "status": "pass"} for c in report["checks"]]
    (root / "release_report.json").write_text(json.dumps(report), encoding="utf-8")
    reports[str(root)] = report
    (root / "master.mp4").write_bytes(b"offline review video " + run_id.encode())
    Image.new("RGB", (20, 20), "blue").save(root / "thumbnail_cover.jpg")
    (root / "stories").mkdir(exist_ok=True)
    for n in (1, 2):
        Image.new("RGB", (9, 16), "red").save(root / "stories" / f"story_{n:02d}.jpg")
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    topic = db.list_all_topics()[0]["id"]
    payload["topic_id"] = topic
    db.create_run(run_id, topic)
    db.update_run(run_id, "pending_approval", script_payload=payload, master_video_path=root / "master.mp4",
                  thumbnail_cover_path=root / "thumbnail_cover.jpg")
    return root, payload


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
    import threading
    threading.Timer.start = lambda self: None
    from src import notice_conditions
    notice_conditions.current = lambda db_: []
    reports: dict = {}
    release.write_release_report = lambda run, dry_run=False: reports[str(Path(run["master_video_path"]).parent)]
    bot.layout.tick(force=True)                        # the chat as it stands: the pinned panel
    bot.show_dashboard()
    layout = set(fake.messages)
    fake.calls.clear()
    fake.uploads = 0
    runs = [("m_approve", True), ("m_reject", False), ("m_timeout", True)]
    for n, (run_id, passing) in enumerate(runs):
        root, payload = a_run(db, run_id, passing, reports)
        bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, run_id)
        with db._conn() as conn:
            conn.execute("UPDATE approvals SET expires_at=? WHERE run_id=?", (time.time() + 3600 * (n + 1), run_id))
    review_messages = [m for m in fake.messages if m not in layout]
    sent_for_delivery = len(review_messages)
    uploads_for_delivery = fake.uploads
    panel = hasattr(bot, "review") and getattr(bot.review, "message", lambda: None)()
    paging_sent = None
    longest = None
    faces = 0
    if panel:
        from src.review_panel import visible_len
        before = len(fake.messages)

        def tap(data):
            bot.review.tap({"id": "q", "data": data, "from": {"id": 7}, "message": {"message_id": panel, "chat": {"id": 100}}})
        longest = 0
        for run_id, _ in runs:
            key = db.run_key(run_id)
            tap(f"rvp:{key}")
            for tab in ("sum", "scr", "pst", "chk", "pic"):
                tap(f"rvt:{key}:{tab}")
                page = 1
                while True:
                    faces += 1
                    longest = max(longest, visible_len(fake.messages[panel].get("caption") or ""))
                    keyboard = json.loads(fake.messages[panel].get("reply_markup") or '{"inline_keyboard": []}')["inline_keyboard"]
                    if not any(b["text"] == "▶" for row in keyboard for b in row):
                        break
                    page += 1
                    tap(f"rvi:{key}:{page}")
        paging_sent = len(fake.messages) - before
        tap(f"rvp:{db.run_key('m_approve')}")
        tap(f"rva:{db.run_key('m_approve')}")
        tap(f"rvr:{db.run_key('m_reject')}")
        tap(f"rvs:{db.run_key('m_reject')}:hook")
        tap(f"rvd:{db.run_key('m_reject')}")
    else:
        for run_id, decision in (("m_approve", "approve"), ("m_reject", "reject")):
            card = (db.get_approval(run_id) or {}).get("telegram_message_id")
            bot._handle_callback({"id": "q", "data": f"{decision}:{db.run_key(run_id)}", "from": {"id": 7, "first_name": "Op"},
                                  "message": {"message_id": card, "chat": {"id": 100}}})
        bot._handle_callback({"id": "q", "data": f"rsn:{db.run_key('m_reject')}:hook", "from": {"id": 7},
                              "message": {"message_id": (db.get_approval("m_reject") or {}).get("telegram_message_id"),
                                          "chat": {"id": 100}}})
    db.update_approval_decision("m_timeout", "timeout", decided_by="system")
    db.update_run("m_timeout", "timeout")
    bot.dismiss_approval_buttons("m_timeout")
    bot.timeout_notice("m_timeout")
    # a push alert is a line that leaves by itself after PUSH_ALERT_SECONDS (UI-01a): not a review message
    left = [m for m in fake.messages if m not in layout and m not in fake.deleted
            and not str(fake.messages[m].get("text", "")).startswith("🔔 ")]
    print(json.dumps({
        "reviews": len(runs),
        "review_messages_sent_on_delivery": sent_for_delivery,
        "file_parts_uploaded_on_delivery": uploads_for_delivery,
        "review_messages_left_after_the_decisions": len(left),
        "kinds_left": sorted({fake.messages[m]["kind"] for m in left}),
        "in_full_replies_sent": sum(1 for m in review_messages if "In full" in str(fake.messages[m].get("text", ""))),
        "faces_opened": faces,
        "messages_sent_while_paging_and_tabs": paging_sent,
        "longest_face_caption": longest,
        "caption_limit": 1024,
    }, ensure_ascii=False, indent=1, sort_keys=True))


main()
