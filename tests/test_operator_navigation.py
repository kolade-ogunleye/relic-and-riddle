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
