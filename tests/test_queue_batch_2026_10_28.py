"""
tests/test_queue_batch_2026_10_28.py
====================================
/queue drop, requeue and bump take several topics at once (operator, 2026-10-28: "why am I not able to drop
multiple topics at a time? like /queue drop 7 8 11"), typed to the real bot. Every number is resolved before
anything changes, and one /queue undo reverses a whole drop.
"""

import time

from src.control import make_command_handler, topic_refs


def test_references_are_numbers_ids_commas_and_ranges():
    assert topic_refs("7 8 11") == ["7", "8", "11"]
    assert topic_refs("7,8, 11") == ["7", "8", "11"]
    assert topic_refs("7-9 12") == ["7", "8", "9", "12"]
    assert topic_refs("kailasa_temple_monolith") == ["kailasa_temple_monolith"]
    assert topic_refs("1-200") == ["1-200"]                      # an absurd range is one (unknown) reference


def _bot(db, calls):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or {"message_id": 900}
    return bot


def _typed(bot, text, handler, message_id):
    bot._process_update({"update_id": message_id, "message": {"message_id": message_id, "text": text, "date": int(time.time()),
                                                               "from": {"id": 555}, "chat": {"id": 555}}}, handler)
    bot.drain()


def _queued(db):
    return [t["id"] for t in db.list_all_topics() if t.get("status") == "queued"]


def test_typed_queue_drop_of_several_topics_and_one_undo(db, tmp_path):
    topics = db.list_all_topics()
    picked = [t["id"] for t in topics[:3]]
    seen = []
    real = make_command_handler(db, lambda: None, lambda *a, **k: "", assets_dir=tmp_path)

    def handler(command, args):
        seen.append((command, args))
        return real(command, args)
    calls = []
    bot = _bot(db, calls)
    _typed(bot, "/queue drop 1 2,3 999", handler, 401)
    assert ("drop_topic", "1 2,3 999") in seen
    assert not set(picked) & set(_queued(db))                     # all three archived
    said = " ".join(str(d.get("text") or "") for _, d in calls)
    assert "Archived 3 topic(s)" in said and "Not found: 999" in said and "/queue undo" in said
    _typed(bot, "/queue undo", handler, 402)
    assert set(picked) <= set(_queued(db))                        # one undo brings back all three


def test_requeue_and_bump_take_several_and_bump_keeps_the_order_given(db, tmp_path):
    handle = make_command_handler(db, lambda: None, lambda *a, **k: "", assets_dir=tmp_path)
    topics = db.list_all_topics()
    a, b = topics[1]["id"], topics[2]["id"]
    handle("drop_topic", "2 3")
    out = handle("requeue", "2-3")
    assert "Restored 2 topic(s)" in (out if isinstance(out, str) else out[0])
    assert {a, b} <= set(_queued(db))
    out = handle("bump", "3 2")
    text = out if isinstance(out, str) else out[0]
    assert "Moved 2 topic(s) to the front" in text
    assert db.get_next_topic()["id"] == b                         # the first one named is next
