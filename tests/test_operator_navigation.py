"""Paging, back-navigation, own-photo uploads, the CC BY-SA switch and Bot API round trips."""
import json

import pytest

from src import config, credits, telegram_bot
from src.control import make_command_handler, pager
from src.image_upload import ImageUpload
from src.telegram_bot import TelegramApprovalBot, format_responsive_keyboard
from tests.test_operator_jobs_upload import image_bytes


def _bot(db, calls):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or {"message_id": 900}
    return bot


def _tap(bot, data, message_id=70, update_id=1, handler=lambda a, t: ""):
    bot._process_update({"update_id": update_id, "callback_query": {
        "id": f"q{update_id}", "data": data, "from": {"id": 555},
        "message": {"message_id": message_id, "chat": {"id": 555}}}}, handler)
    bot.drain()


@pytest.mark.parametrize("page,pages,icons", [(0, 1, []), (0, 2, ["▶"]), (0, 5, ["▶", "⏭"]),
                                              (2, 5, ["⏮", "◀", "▶", "⏭"]), (4, 5, ["⏮", "◀"]), (1, 2, ["◀"])])
def test_pager_shows_only_buttons_that_move(page, pages, icons):
    rows = pager("x:", page, pages)
    assert [b["text"] for row in rows for b in row] == icons
    assert all(b["callback_data"].startswith("x:") for row in rows for b in row)
    assert format_responsive_keyboard(rows) == rows  # a pager row is never split on small screens


def test_waiting_ideas_page_and_keep_place_after_add(db, monkeypatch):
    from src import topic_proposer
    from tests.test_topic_proposer import _proposal
    waiting = {s: _proposal(s, title=f"Idea {s}").model_dump() for s in ("a", "b", "c", "d")}
    monkeypatch.setattr(topic_proposer, "pending", lambda db: dict(waiting))
    monkeypatch.setattr(topic_proposer, "proposal_text", lambda p: p.title)
    monkeypatch.setattr(topic_proposer, "add", lambda db, slug: waiting.pop(slug))
    h = make_command_handler(db, lambda: None, lambda: "")
    text, buttons = h("cprop", "2")
    assert "Waiting idea 3 of 4" in text and "Idea c" in text
    assert [b["text"] for b in buttons[1]] == ["⏮", "◀", "▶"]
    text, _ = h("ptadd", "2:c")                     # the next idea moves into the same place
    assert "Waiting idea 3 of 3" in text and "Idea d" in text
    assert "Idea a" in h("ptskip", "b")[0]          # buttons from the earlier release still work


def test_image_topics_list_full_titles_and_number_buttons(db, monkeypatch):
    long_title = "The Extraordinarily Long Title of a Monument That Never Fits a Button"
    topics = [{"id": f"t{i}", "title": long_title if i == 0 else f"Topic {i}"} for i in range(10)]
    monkeypatch.setattr(db, "list_all_topics", lambda: topics)
    text, buttons = make_command_handler(db, lambda: None, lambda: "")("imagepick", "0")
    assert long_title in text and "page 1 of 2" in text
    assert [b["text"] for b in buttons[0]] == ["1", "2", "3", "4"]
    nav = [b for row in buttons for b in row if b["text"] in {"▶", "⏭"}]
    assert {b["callback_data"] for b in nav} == {"imagepick:8"}


def test_storyboard_decisions_return_to_the_topic(db, tmp_path, monkeypatch):
    topic = tmp_path / "t1"
    topic.mkdir()
    (topic / "credits.json").write_text(json.dumps({"reviewed": False, "images": []}))
    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    text, buttons = h("imgcancel", "t1@digest")
    assert "closed" in text and buttons[0][0]["callback_data"] == "imagehelp:t1"
    text, buttons = h("imgok", "t1@stale")
    assert "changed" in text and buttons[-1][0]["callback_data"] == "cdash:"


def test_covers_from_a_review_card_open_beside_it(db):
    calls = []
    bot = _bot(db, calls)
    _tap(bot, "ccov:r1", handler=lambda action, target: "No rendered run found for this reference.")
    assert not any(m.startswith("editMessage") for m, _ in calls)          # the review card is untouched
    sent = [d for m, d in calls if m == "sendMessage"]
    assert "close:" in sent[-1]["reply_markup"]
    _tap(bot, "close:", message_id=900, update_id=2)
    assert ("deleteMessage", {"chat_id": "555", "message_id": 900}) in [(m, {k: d[k] for k in ("chat_id", "message_id") if k in d}) for m, d in calls]


def test_each_tap_is_answered_once(db):
    calls = []
    bot = _bot(db, calls)
    bot._answer_callback("q1", "Request received")
    bot._answer_callback("q1", "Working")
    assert [m for m, _ in calls] == ["answerCallbackQuery"]


def test_unchanged_edit_is_success(db, monkeypatch):
    class Reply:
        ok, status_code, headers = False, 400, {"content-type": "application/json"}
        def json(self):
            return {"ok": False, "description": "Bad Request: message is not modified"}
    monkeypatch.setattr(telegram_bot, "_post", lambda url, **kw: Reply())
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    assert bot._api("editMessageText", data={"message_id": 1, "text": "same"})


def test_own_photo_needs_no_urls(db, tmp_path, monkeypatch):
    from src import telegram_images
    monkeypatch.setattr(telegram_images, "write_contact_sheet", lambda *a: None)
    wizard = ImageUpload(db, tmp_path / "staging", tmp_path / "assets")
    state = wizard.begin(555, 111, "1", 42)
    wizard.file(555, 111, image_bytes(), state["id"])
    assert wizard.own_work(555, 111, state["id"])["stage"] == "licence"
    with pytest.raises(ValueError, match="CC0 or CC BY"):
        wizard.answer(555, 111, "Public domain")
    assert wizard.answer(555, 111, "CC BY 4.0")["stage"] == "author"
    result = wizard.answer(555, 111, "Omo")
    record = json.loads((tmp_path / "assets" / result["topic"] / "credits.json").read_text())["images"][0]
    assert record["own_work"] and record["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert record["source_url"].startswith("Own work") and record["artist"] == "Omo"


def test_share_alike_switch_and_release_obligation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "IMAGES_ALLOW_SHARE_ALIKE", False)
    assert not credits.license_allowed("CC BY-SA 4.0")
    monkeypatch.setattr(config, "IMAGES_ALLOW_SHARE_ALIKE", True)
    assert credits.license_allowed("CC BY-SA 4.0") and credits.license_allowed("CC BY-SA 3.0")
    assert not credits.license_allowed("CC BY-SA 1.0")      # 1.0 does not allow a later-version adaptation
    ledger = tmp_path / "provenance.json"
    ledger.write_text(json.dumps({"assets": [{"license": "CC BY-SA 4.0", "attribution_required": True,
                                              "artist": "A", "title": "T", "source_url": "https://s", "license_url": "https://l"}]}))
    payload = credits.add_provenance_attributions({"caption": "Story", "description": "Story"}, ledger)
    assert credits.SHARE_ALIKE_NOTICE in payload["caption"] and credits.SHARE_ALIKE_NOTICE in payload["description"]


def _typed(bot, text, handler, message_id=300):
    import time
    bot._process_update({"update_id": message_id, "message": {"message_id": message_id, "text": text, "date": int(time.time()),
                                                               "from": {"id": 555}, "chat": {"id": 555}}}, handler)
    bot.drain()


def test_typed_answer_is_delivered_even_if_its_screen_was_left(db):
    calls, ids = [], iter(range(900, 950))
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or {"message_id": next(ids)}

    def slow_ask(command, args):
        panel = int(db.get_setting("telegram_dashboard_id"))
        bot._panel_revisions[panel] = bot._panel_revisions.get(panel, 0) + 1   # the operator taps Dashboard meanwhile
        return "the recorded answer"
    _typed(bot, "/ask what failed?", slow_ask)
    sent = [d.get("text", "") for m, d in calls if m == "sendMessage"]
    assert any("the recorded answer" in text for text in sent)                 # opened as the panel, not dropped
    assert ("deleteMessage", 300) in [(m, d.get("message_id")) for m, d in calls]  # the typed command is removed
    job = bot.jobs.recent()[0]
    assert job["delivery"] == "attempted"


def test_a_new_panel_replaces_the_previous_one(db):
    calls, ids = [], iter(range(700, 750))
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or {"message_id": next(ids)}
    first = bot.show_dashboard("one", [[{"text": "x", "callback_data": "cdash:"}]], fresh=True)
    second = bot.show_dashboard("two", [[{"text": "x", "callback_data": "cdash:"}]], fresh=True)
    bot.drain()
    assert first != second and ("deleteMessage", first) in [(m, d.get("message_id")) for m, d in calls]
    _typed(bot, "/next", lambda c, a: "Next topic: X")
    edits = [d.get("text", "") for m, d in calls if m == "editMessageText"]
    assert "Next topic: X" in edits[-1]


def test_dashboard_failure_reason_is_readable():
    from src.control import short_reason
    error = "ScriptUnavailableError: The script writer made 2 attempts and each failed its checks " + "word " * 80
    reason = short_reason(error)
    assert not reason.startswith("ScriptUnavailableError") and reason.endswith("…")
    assert len(reason) <= 281 and not reason[:-1].endswith(("wor", "wo"))   # cut at a word
    assert short_reason("Plain short reason") == "Plain short reason"


def test_ask_about_the_bot_uses_bot_evidence_not_the_last_video(db, monkeypatch):
    from src import free_llm, probe
    db.create_run("r1", "01_topic")
    seen = {}
    monkeypatch.setattr(free_llm, "ready", lambda: True)
    def model(system, prompt, schema, **k):
        seen["system"], seen["prompt"] = system, prompt
        return probe._Answer(answer="Yes, it answered you just now.", records=["recent bot jobs"], recorded=True)
    monkeypatch.setattr(free_llm, "call", model)
    out = probe.probe_system("Is the bot's ask command actively working now?", db=db)
    assert "the bot and studio" in out and "recent bot jobs" in seen["prompt"] and '"run"' not in seen["prompt"]
    assert "plain everyday words" in seen["system"] and "Based on: recent bot jobs" in out
    out = probe.probe_system("why did the last run fail?", db=db)
    assert "About:</b> " in out and "the bot and studio" not in out    # a video question gets the run


def test_ask_says_plainly_when_records_do_not_cover_it(db, monkeypatch):
    from src import free_llm, probe
    monkeypatch.setattr(free_llm, "ready", lambda: True)
    monkeypatch.setattr(free_llm, "call", lambda *a, **k: probe._Answer(answer="I can't tell; try /status.", records=[], recorded=False))
    out = probe.probe_system("is the scheduler healthy?", db=db)
    assert "Not fully covered by the records" in out and "⚪" not in out


def test_start_after_cleared_history_gives_one_visible_panel(db):
    calls, ids = [], iter(range(600, 650))
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or {"message_id": next(ids)}
    db.set_setting("telegram_dashboard_id", "40")            # the old panel, gone from the operator's view
    _typed(bot, "/dashboard", lambda c, a: "", message_id=301)
    _typed(bot, "/start", lambda c, a: "", message_id=302)  # Telegram's START button, a second later
    sends = [d for m, d in calls if m == "sendMessage"]
    assert len(sends) == 1                                    # one new panel at the bottom, not two
    assert not any(m == "editMessageText" and d.get("message_id") == 40 for m, d in calls)  # not an invisible edit
    deleted = {d.get("message_id") for m, d in calls if m == "deleteMessage"}
    assert {40, 301, 302} <= deleted                          # old panel and both commands removed


def test_research_tap_while_research_runs_is_explained_not_failed(db, monkeypatch):
    import os
    started = []
    h = make_command_handler(db, lambda: None, lambda: "", propose_topics=lambda: started.append(1) or "")
    db.set_setting("operator_job:x", json.dumps({"resource": "topic-research", "state": "running", "owner_pid": os.getpid(),
                                                 "updated_at": "2026-10-23T01:17:59+00:00", "title": "Researching topic ideas"}))
    text, buttons = h("cpropose", "")
    assert "already running" in text and not started                       # no duplicate research, no failure
    text, buttons = h("cideas", "")
    assert "Research is running" in text and buttons[0][0]["callback_data"] == "jobs:"
    db.set_setting("operator_job:x", json.dumps({"resource": "topic-research", "state": "running", "owner_pid": -1}))
    assert h("cideas", "")[1][0][0]["callback_data"] == "cpropose:"        # a stopped process's record is not running
    h("cpropose", "")
    assert started == [1]


def test_queue_buttons_pair_render_with_refresh_and_keep_pages_together(db, monkeypatch):
    topics = [{"id": f"t{i}", "title": f"Topic {i}", "status": "queued", "priority": 0} for i in range(1, 30)]
    monkeypatch.setattr(db, "list_all_topics", lambda: topics)
    monkeypatch.setattr(db, "get_next_topic", lambda: topics[4])
    text, rows = make_command_handler(db, lambda: None, lambda: "")("qpage", "2")
    data = [[b["callback_data"].partition(":")[0] for b in row] for row in rows]
    assert data[0] == ["qrender", "qrefresh"] and data[1] == ["qbump", "qdrop"]
    assert data[2] == ["qpage"] * 4 and data[-1] == ["cdash"]          # ⏮ ◀ ▶ ⏭ on one row
    assert format_responsive_keyboard(rows)[2] == rows[2]
