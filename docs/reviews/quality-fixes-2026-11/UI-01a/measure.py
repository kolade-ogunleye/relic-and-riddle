"""UI-01a measurements: what a night of notices leaves in the operator's chat.

    python measure.py <repo root>

Runs on the parent (d31604ad) and on the commit. Sends a fixed, invented sequence of 24 notices (one of each kind
the design's §10.2 table maps: failures, posting errors, the publish receipt, proposals, digests, the release
notice, the offline notice ...) through the measured root's TelegramApprovalBot, with TELEGRAM_CLEAN_CHAT on, into a
fake Bot API that keeps message ids, edits, pins and deletions. Nothing is sent anywhere (no network) and nothing in
the repository is written (a throwaway database in the system temp folder; no .env is read: the app root is empty).
Prints JSON: the messages left in the chat after the alerts' lifetime, the pinned panel, the alerts that rang, and
the panel's header line. Round 3: three item actions refused by their handlers (Re-render while another run is
made, Back to the queue for a topic being made, Retry that did not post) and two taps that only acknowledge
(Back to the queue, Resend for review): the Needs-you items left open and the messages the taps sent (null on
the parent, which has no panel). Round 4: Telegram send methods called through the Bot API outside the chat writer
and the panels' renderers (the weekly update report's own message), and a refused 🧰 Apply updates ("A production run
is in progress"): whether its Needs-you item is still open.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="ui01a-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
sys.path.insert(0, str(ROOT))

from src import config
from src.db import PipelineDatabase
from src.telegram_bot import PublishReceipt, TelegramApprovalBot

config.TELEGRAM_CLEAN_CHAT = True
config.PUSH_ALERT_SECONDS = 8.0
config.TELEGRAM_CARDS_V2 = False


class FakeTelegram:
    def __init__(self):
        self.next_id, self.messages, self.deleted, self.pinned, self.rang = 1000, {}, set(), [], []

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "sendMessage":
            self.next_id += 1
            self.messages[self.next_id] = data
            if data.get("disable_notification") != "true":
                self.rang.append(data.get("text", ""))
            return {"message_id": self.next_id}
        if method.startswith("editMessage"):
            mid = int(data["message_id"])
            if mid not in self.messages or mid in self.deleted:
                raise RuntimeError("Telegram editMessageText failed: HTTP 400 Bad Request: message to edit not found")
            self.messages[mid] = {**self.messages[mid], **data}
            return {"message_id": mid}
        if method == "deleteMessage":
            self.deleted.add(int(data["message_id"]))
        if method == "deleteMessages":
            self.deleted.update(json.loads(data["message_ids"]))
        if method == "pinChatMessage":
            self.pinned.append(int(data["message_id"]))
        return True


NOTICES = [
    ("❌ <b>Run failed</b>\n\n<b>Petra</b>\n<blockquote expandable>voiceover timed out</blockquote>\nNothing was posted.", {"group": "needs", "key": "fail:r1"}),
    ("⚠️ Scheduled posting hit an error: Instagram refused the Stories upload.", {"group": "needs", "key": "posting-error"}),
    ("⚠️ Scheduled posting hit an error: Instagram refused the Stories upload.", {"group": "needs", "key": "posting-error"}),
    ("⚠️ Scheduled posting hit an error: Instagram refused the Stories upload.", {"group": "needs", "key": "posting-error"}),
    ("🧭 <b>Improvement proposal</b>\n<b>Rule one</b>", {"group": "needs", "key": "proposals", "replace": True}),
    ("🧭 <b>Improvement proposal</b>\n<b>Rule two</b>", {"group": "needs", "key": "proposals", "replace": True}),
    ("🧭 <b>Improvement proposal</b>\n<b>Rule three</b>", {"group": "needs", "key": "proposals", "replace": True}),
    ("🔁 <b>Voice fallback</b>\n\n<b>Derinkuyu</b>\n\nVoiced by the second provider.", {"section": "info"}),
    ("⏸ Scheduled run skipped (paused). Send /resume to re-enable.", {"group": "runs", "key": "paused-skip"}),
    ("⏸ Scheduled run skipped (paused). Send /resume to re-enable.", {"group": "runs", "key": "paused-skip"}),
    ("📊 <b>Weekly digest</b>\nWeek of 2-8 Nov.", {"group": "info", "key": "digest:45"}),
    ("📦 Installing rc73: back in about 2 min", {"group": "info", "key": "release"}),
    ("📦 rc73 installed", {"group": "info", "key": "release"}),
    ("🔄 <b>Photos turned over</b> in Moai:\n• 4 photos replaced", {"section": "info"}),
    ("⏰ Missed the 18:00 slot for Sigiriya (the daemon was off or paused). Moved to Sat 18:00.", {"section": "needs", "key": "missed:7"}),
    ("🖼 <b>Photo sets not ready</b>\n\nThese upcoming topics will be skipped until their photos are approved.", {"group": "needs"}),
    ("⚖️ <b>Policy watch</b>\nNo changes that affect posting.", {"group": "info", "key": "policy:45"}),
    ("🔴 <b>Production Daemon Offline</b> • Scheduled runs stopped.", {"group": "info", "key": "service-state", "record_alert": False}),
    ("🔑 Now in use, no restart needed: <code>X</code>", {"group": "info", "record_alert": False}),
    ("⌛ <b>Today's run was skipped</b>: every topic left waits for a timed-out review.", {"group": "needs"}),
    ("🧾 Couldn't build the fact sheet for Nazca: source unreachable", {"group": "needs"}),
    ("❌ Updates not applied (rolled back): a test failed", {"group": "needs"}),
]


def needs_without_actions() -> int:
    """Needs-you senders (section or group "needs") in the measured root's main.py, worker_deadline and
    backup_health that offer no way to act: no buttons, no actions (design §10.2, §14.2). Calls in a TypeError
    fallback, and the switch-off branch of `if config.TELEGRAM_CLEAN_CHAT`, repeat or keep the earlier chat."""
    import ast
    missing = 0
    for rel in ("main.py", "src/worker_deadline.py", "src/backup_health.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        skip = {id(sub) for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
                and isinstance(node.type, ast.Name) and node.type.id == "TypeError" for sub in ast.walk(node)}
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and any(isinstance(t, ast.Attribute) and t.attr == "TELEGRAM_CLEAN_CHAT"
                                                for t in ast.walk(node.test)):
                skip |= {id(sub) for branch in node.orelse for sub in ast.walk(branch)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in skip:
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name not in ("_alert", "_notice", "notify", "say"):
                continue
            words = {k.arg: k.value for k in node.keywords}
            needs = any(isinstance(words.get(w), ast.Constant) and words[w].value == "needs" for w in ("section", "group"))
            positional = len(node.args) >= (3 if name in ("say", "_notice") and isinstance(node.func, ast.Name) else 2)
            if needs and not ({"buttons", "actions"} & set(words) or positional):
                missing += 1
    return missing


def item_actions(TMPDIR) -> dict:
    """Round 3 (review 2 of UI-01a): refused item actions keep their Needs-you item; acknowledging taps send nothing."""
    if not hasattr(TelegramApprovalBot, "_notifications_tap"):
        return {"refused_item_actions_that_lost_their_item": None, "item_action_taps_that_sent_a_message": None}
    from src import notifications as notes
    bank = TMPDIR / "bank.json"   # one invented topic
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic 1", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=TMPDIR / "actions.db", topic_bank_file=bank)
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    bot._api = fake
    bot._queue_navigation = lambda work: work() or True
    try:
        from src import notice_conditions
        notice_conditions.current = lambda db_: []
    except ImportError:
        pass
    bot.show_dashboard("🏛️ Dashboard", [[{"text": "x", "callback_data": "cdash:"}]])
    topics = db.list_all_topics()
    topic = topics[0]["id"] if topics else "t1"
    if topics:
        db.record_topic_status(topic, "in_production")
    db.create_run("m1", topic)
    post = db.add_scheduled_post("m1", "story", None, [], status="failed")
    refused = [("rerender:m1", lambda a, b: "🔁 A run is already in progress; Re-render this one when it has finished."),
               (f"requeue:{topic}", lambda a, b: ("This item cannot be requeued from its current state.", [])),
               (f"retrypost:{post}", lambda a, b: "🔁 Not retried: the page refused the upload")]
    lost = 0
    for k, (callback, handler) in enumerate(refused):
        notes.say(bot.notify, "❌ <b>Production failed</b> · Petra\n\nStopped early.", group="needs", key=f"fail:x{k}",
                  run=f"x{k}", replace=True, actions=[{"text": "Act", "callback_data": callback}])
        item = next(i for i in notes.open_items(db) if i["dedupe_key"] == f"fail:x{k}")
        bot._notifications_tap({"id": f"q{k}", "data": f"n:{item['id']}:{notes.action_ref({'callback_data': callback})}",
                                "from": {"id": 7}, "message": {"message_id": 1, "chat": {"id": 100}}}, handler)
        lost += int(not any(i["dedupe_key"] == f"fail:x{k}" for i in notes.open_items(db)))
    sent = 0
    for k, (callback, reply) in enumerate([("requeue:t9", "↩️ Petra is back in the queue."),
                                           ("resend:m1", "📨 Sending Petra for review again…")]):
        notes.say(bot.notify, "⏰ <b>Review timed out</b> · Petra", group="needs", key=f"fail:y{k}", run=f"y{k}",
                  replace=True, actions=[{"text": "Go", "callback_data": callback}])
        item = next(i for i in notes.open_items(db) if i["dedupe_key"] == f"fail:y{k}")
        before = len(fake.messages)
        bot._notifications_tap({"id": f"r{k}", "data": f"n:{item['id']}:{notes.action_ref({'callback_data': callback})}",
                                "from": {"id": 7}, "message": {"message_id": 1, "chat": {"id": 100}}},
                               lambda a, b, reply=reply: reply)
        sent += len(fake.messages) - before
    return {"refused_item_actions_that_lost_their_item": lost, "item_action_taps_that_sent_a_message": sent}


def raw_sends_outside_the_panels() -> int:
    """`_api("send...")` / copy / forward calls in main.py and src/ outside the chat writer and the panels' renderers
    (design §8, §14.2)."""
    import ast
    panels = {"telegram_bot.py", "telegram_layout.py", "review_panel.py", "layout_repost.py"}
    count = 0
    for path in [ROOT / "main.py"] + sorted((ROOT / "src").glob("*.py")):
        if path.parent.name == "src" and path.name in panels:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and (getattr(node.func, "attr", None) or getattr(node.func, "id", None)) == "_api":
                method = node.args[0] if node.args else None
                said = method.value if isinstance(method, ast.Constant) else None
                count += int(not isinstance(said, str) or said.startswith(("send", "copy", "forward")))
    return count


def _from_main(names) -> dict:
    """The measured root's main.py definitions named (main.py itself cannot be imported with an empty app root),
    run in a namespace with what they use."""
    import ast
    import html
    from collections.abc import Callable
    from typing import Any

    from src import maintenance, notifications
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in names for t in n.targets))]
    space = {"html": html, "notifications": notifications, "maintenance": maintenance, "Any": Any,
             "Callable": Callable, "List": list}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(ROOT / "main.py"), "exec"), space)  # noqa: S102 - the measured root's own definitions
    return {k: v for k, v in space.items() if k in names}


def refused_apply_updates() -> dict:
    """Round 4 (review 3 of UI-01a): a refused Apply updates keeps its Needs-you item. The measured root's own reply
    (main.apply_updates_reply) where it has it, else the words its earlier handler replied."""
    key = "refused_apply_updates_that_lost_their_item"
    if not hasattr(TelegramApprovalBot, "_notifications_tap"):
        return {key: None}
    from src import notifications as notes
    from src.control import make_command_handler
    app = _from_main(("apply_updates_reply", "UPDATES_BUSY", "UPDATES_STARTED"))
    db = PipelineDatabase(db_path=TMP / "apply.db", topic_bank_file=TMP / "none.json")
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    bot._api = FakeTelegram()
    bot._queue_navigation = lambda work: work() or True
    try:
        from src import notice_conditions
        notice_conditions.current = lambda db_: []
    except ImportError:
        pass
    bot.show_dashboard("🏛️ Dashboard", [[{"text": "x", "callback_data": "cdash:"}]])
    busy = "A production run is in progress; try again when it finishes."
    apply = (lambda: app["apply_updates_reply"](True, lambda: None)) if "apply_updates_reply" in app else (lambda: busy)
    handler = make_command_handler(db, lambda: "Mon 07:00", lambda: "", apply_updates=apply)
    notes.say(bot.notify, "🧰 <b>Weekly update check</b>\n🔴 requests 2.31.0 -> 2.32.3", group="needs",
              key="maintenance:apply", replace=True, actions=[{"text": "🧰 Apply updates", "callback_data": "upd:apply"}])
    item = next(i for i in notes.open_items(db) if i["dedupe_key"] == "maintenance:apply")
    bot._notifications_tap({"id": "u1", "data": f"n:{item['id']}:{notes.action_ref({'callback_data': 'upd:apply'})}",
                            "from": {"id": 7}, "message": {"message_id": 1, "chat": {"id": 100}}}, handler)
    return {key: int(not any(i["dedupe_key"] == "maintenance:apply" for i in notes.open_items(db)))}


def main() -> None:
    db = PipelineDatabase(db_path=TMP / "measure.db", topic_bank_file=TMP / "none.json")
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    bot._api = fake
    clock = [1_800_000_000.0]
    layout = getattr(bot, "layout", None) if config.TELEGRAM_CLEAN_CHAT and hasattr(type(bot), "layout") else None
    if layout is not None:
        layout.clock = lambda: clock[0]
    import threading
    threading.Timer.start = lambda self: None   # deletions happen on the ticks below, at measured times
    calls = [0]
    real_notify = bot.notify

    def counted(*args, **kw):
        result = real_notify(*args, **kw)   # a call the build refuses (TypeError) is not counted
        calls[0] += 1
        return result
    bot.notify = counted
    for text, kw in NOTICES:
        try:
            bot.notify(text, **kw)
        except TypeError:   # a build whose notify takes no key/section
            bot.notify(text, **{k: v for k, v in kw.items() if k in ("group", "record_alert")})
        clock[0] += 5
    receipt = PublishReceipt(bot, "run_m", "Sigiriya", ["instagram", "facebook"])
    receipt.update_platform("instagram", "live")
    receipt.update_platform("facebook", "live")
    receipt.finalize()
    for _ in range(4):           # the minute after: coalesced alerts ring, the alerts' lifetime passes
        clock[0] += 61
        if layout is not None:
            layout.tick(force=True)
    clock[0] += 61
    if layout is not None:
        layout.delete_due_alerts()
    live = sorted(m for m in fake.messages if m not in fake.deleted)
    panel = None
    if layout is not None:
        from src.telegram_layout import slot_message
        panel = slot_message(db, "notifications")
    header = fake.messages[panel]["text"].splitlines()[0] if panel else None
    print(json.dumps({
        "notices_sent_by_senders": calls[0],
        "messages_left_in_chat": len(live),
        "messages_left_other_than_the_panel": len([m for m in live if m != panel]),
        "pinned_panels": len(set(fake.pinned)),
        "alerts_that_rang": len(fake.rang),
        "alert_lines_over_40_characters": sum(1 for t in fake.rang if len(t) > 40),
        "panel_header": header,
        "needs_you_senders_without_actions": needs_without_actions(),
        **item_actions(TMP),
        "telegram_sends_outside_the_panels": raw_sends_outside_the_panels(),
        **refused_apply_updates(),
    }, ensure_ascii=False, indent=1, sort_keys=True))


main()
