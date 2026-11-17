"""UI-01d measurements: which of the operator's messages stay in the chat after the bot has read them.

    python measure.py <repo root>

Runs on the parent (UI-01c) and on the commit, with TELEGRAM_CLEAN_CHAT on. Eight messages from the operator go
through the measured root's bot into a fake Bot API (no network; a throwaway database in the system temp folder,
no .env): plain text that answers nothing, a sticker, a voice note, a photo without a caption, a photo with an
"/ask" caption, a message sent while the bot was off, a typed /queue, and the answer to an armed /ask; round 2
adds three uploads: a photo captioned for a topic's set (/photos <topic> | source ... ; staging stubbed to succeed),
a /footage clip with no topic and one over Telegram's 20 MB; round 3 adds a typed `/photos <topic> upload` (the
subcommand that opens the upload wizard). Prints the messages the bot left in the chat, the
answers it sent as new messages at the bottom instead of drawing them in the dashboard, and whether the dashboard
said that the message sent while the bot was off was not acted on.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="ui01d-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
sys.path.insert(0, str(ROOT))

from src import config
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot

config.TELEGRAM_CLEAN_CHAT = True
config.TELEGRAM_CARDS_V2 = False


class FakeTelegram:
    def __init__(self):
        self.next_id, self.messages, self.deleted = 1000, {}, set()

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
            return {"message_id": mid}
        if method == "deleteMessage":
            self.deleted.add(int(data["message_id"]))
        return True


def main() -> None:
    bank = TMP / "bank.json"   # one invented topic (round 3: the typed upload names it)
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic 1", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / "m.db", topic_bank_file=bank)
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    bot._api = fake
    bot._delete_soon = bot.delete_message
    bot._queue_navigation = lambda work: work() or True
    bot._run_command = lambda handler, command, args, target=None: None
    import threading
    threading.Timer.start = lambda self: None
    try:
        from src import notice_conditions
        notice_conditions.current = lambda db_: []
    except ImportError:
        pass
    bot.show_dashboard()
    now = int(time.time())
    operator = [
        ("plain text", {"text": "hello?"}),
        ("sticker", {"sticker": {"file_id": "s"}}),
        ("voice note", {"voice": {"file_id": "v"}}),
        ("photo, no caption", {"photo": [{"file_id": "p", "file_size": 10}]}),
        ("photo with /ask", {"photo": [{"file_id": "p", "file_size": 10}], "caption": "/ask what is this?"}),
        ("sent while the bot was off", {"text": "/queue", "date": now - 3600}),
        ("typed /queue", {"text": "/queue"}),
        ("photo for a topic's set", {"photo": [{"file_id": "p", "file_size": 10}],
                                     "caption": "/photos topic-1 | source: https://example.org/p | licence: CC BY 4.0 | "
                                                "licence_url: https://creativecommons.org/licenses/by/4.0/ | author: A"}),
        ("clip with no topic", {"video": {"file_id": "v", "file_size": 10}, "caption": "/footage"}),
        ("clip over 20 MB", {"video": {"file_id": "v", "file_size": 30 * 1048576}, "caption": "/footage topic-1"}),
        ("typed /photos <topic> upload", {"text": "/photos 01_topic upload"}),
    ]
    from src import telegram_images
    telegram_images.add_image = lambda *a, **k: "topic-1"
    bot.download_image = lambda item: b"jpeg"
    sent_before = sum(1 for m in fake.messages if m > 1000)
    outage_said = False
    left = []
    for n, (name, content) in enumerate(operator, start=1):
        mid = 500 + n
        bot._process_update({"update_id": mid, "message": {"message_id": mid, "chat": {"id": 100}, "from": {"id": 7},
                                                         "date": now, **content}}, lambda a, b: "")
        if mid not in fake.deleted:
            left.append(name)
        if name == "sent while the bot was off":
            stored = db.get_setting("telegram_dashboard_id")
            outage_said = bool(stored) and "not acted on" in str(fake.messages.get(int(stored), {}).get("text", ""))
    bot._awaiting = ("ask", time.time() + 600)
    bot._process_update({"update_id": 600, "message": {"message_id": 600, "chat": {"id": 100}, "from": {"id": 7},
                                                       "date": now, "text": "why did it stop?"}}, lambda a, b: "")
    if 600 not in fake.deleted:
        left.append("answer to an armed /ask")
    print(json.dumps({
        "operator_messages_sent": len(operator) + 1,
        "operator_messages_left_in_chat": len(left),
        "left": left,
        "answers_sent_as_new_messages": sum(1 for m in fake.messages if m > 1000) - sent_before,
        "outage_message_said": outage_said,
    }, ensure_ascii=False, indent=1, sort_keys=True))


main()
