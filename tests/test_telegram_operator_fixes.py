import io
import json
from types import SimpleNamespace

import pytest
import requests
from PIL import Image

from src import accounts, config, control, process_state, telegram_images
from src.telegram_bot import TelegramApprovalBot


def test_upload_post_http_error_is_unknown_not_disconnected(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "test-key")
    monkeypatch.setattr(config, "UPLOAD_POST_USER", "test-user")
    response = SimpleNamespace(raise_for_status=lambda: (_ for _ in ()).throw(requests.HTTPError()), json=lambda: {"error": "temporary"})
    monkeypatch.setattr(accounts.requests, "get", lambda *a, **k: response)
    result = accounts._upload_post()
    assert result["error"] == "HTTPError"
    assert "no instagram" not in " ".join(accounts.problems({"upload_post": result}))


def test_upload_post_actual_profile_and_server_page_selection(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "test-key")
    monkeypatch.setattr(config, "UPLOAD_POST_USER", "test-user")
    replies = iter([{"success": True, "profile": {"social_accounts": {"instagram": {"username": "ig"}, "facebook": {"username": "fb"}, "tiktok": ""}, "facebook_page_id": "page"}}, {"pages": []}])
    monkeypatch.setattr(accounts.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: next(replies)))
    result = accounts._upload_post()
    assert result["connected"] == ["facebook", "instagram"]
    assert accounts.problems({"upload_post": result}) == []


def test_legacy_account_cache_is_refreshed(db, monkeypatch):
    from datetime import datetime, timezone
    db.set_setting(accounts.SETTING, json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(), "problems": ["stale"]}))
    monkeypatch.setattr(accounts, "_upload_post", lambda: {"configured": True, "connected": list(accounts.NEEDED), "facebook_page_configured": True})
    monkeypatch.setattr(accounts, "_meta", lambda: {"configured": False})
    assert accounts.check(db)["problems"] == []


def callback_bot(db, monkeypatch, action, media=False):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    calls = []
    msg = {"message_id": 42, "chat": {"id": 555}}
    if media:
        msg["photo"] = [{"file_id": "original"}]
    def api(method, **kwargs):
        calls.append((method, kwargs.get("data", {})))
        if method == "getUpdates":
            return [{"update_id": 1, "callback_query": {"id": "cq", "from": {"id": 555}, "data": action, "message": msg}}]
        return {"message_id": 43 if method == "sendMessage" else 42, "photo": [{"file_id": "updated"}]}
    monkeypatch.setattr(bot, "_api", api)
    return bot, calls


def test_waiting_ideas_edits_original_panel_without_sending_cards(db, monkeypatch):
    from src import topic_proposer
    monkeypatch.setattr(topic_proposer, "pending", lambda _: {})
    bot, calls = callback_bot(db, monkeypatch, "cprop:")
    handler = control.make_command_handler(db, lambda: None, lambda: "started", send_message=bot.notify)
    bot.poll_once(handler)
    bot.drain()
    if bot._workers is not None:
        bot._workers.shutdown(wait=True)
        bot._workers = None
    edit = [data for method, data in calls if method == "editMessageText"][-1]
    assert edit["message_id"] == 42 and "No topic proposals" in edit["text"]
    assert not any(method == "sendMessage" for method, _ in calls)


def test_cover_photo_replaces_navigation_card_in_place(db, monkeypatch, tmp_path):
    bot, calls = callback_bot(db, monkeypatch, "ccov:")
    photo = tmp_path / "cover.jpg"
    Image.new("RGB", (20, 20)).save(photo)
    def handler(*_):
        bot.send_photo(photo, "Cover preview")
        return ""
    bot.poll_once(handler)
    bot.drain()
    if bot._workers is not None:
        bot._workers.shutdown(wait=True)
        bot._workers = None
    edit = next(data for method, data in calls if method == "editMessageMedia")
    assert edit["message_id"] == 42
    assert not any(method in {"sendPhoto", "sendMessage"} for method, _ in calls)


def test_media_navigation_failure_has_named_retry_on_current_panel(db, monkeypatch):
    bot, calls = callback_bot(db, monkeypatch, "ccov:")
    def handler(*_):
        raise RuntimeError("Could not update the media panel")
    bot.poll_once(handler)
    bot.drain()
    if bot._workers is not None:
        bot._workers.shutdown(wait=True)
        bot._workers = None
    edit = [data for method, data in calls if method == "editMessageText"][-1]
    assert "Covers" in edit["text"] and "failed" in edit["text"]
    assert "ccov:" in edit["reply_markup"] and "Try again" in edit["reply_markup"]
    assert not any(method == "sendMessage" for method, _ in calls)


def test_media_panel_returns_to_dashboard_without_retaining_attachment(db, monkeypatch):
    bot, calls = callback_bot(db, monkeypatch, "cdash:", media=True)
    handler = control.make_command_handler(db, lambda: None, lambda: "started")
    bot.poll_once(handler)
    bot.drain()
    if bot._workers is not None:
        bot._workers.shutdown(wait=True)
        bot._workers = None
    assert any(method == "sendMessage" and "RELIC" in data["text"] for method, data in calls)
    assert any(method == "deleteMessage" and data["message_id"] == 42 for method, data in calls)
    assert bot._current_panel(42) == 43


@pytest.mark.parametrize("observed,state", [(None, "interrupted"), ("different-creation", "interrupted"), ("unknown", "unverified")])
def test_progress_does_not_claim_a_dead_reused_or_unverifiable_worker(db, monkeypatch, observed, state):
    db.create_run("r1", "01_topic")
    db.set_active_progress("r1", "Topic", 2, 5)
    monkeypatch.setattr(process_state, "identity", lambda _: observed)
    assert db.get_active_progress()["worker_state"] == state
    h = control.make_command_handler(db, lambda: None, lambda: "started")
    assert "not confirmation of an active run" in h("progress", "")
    assert db.get_run("r1")["status"] == "producing"  # No invented recovery/failed state.


def test_old_progress_button_does_not_show_another_run(db):
    db.create_run("old", "01_topic")
    db.create_run("new", "02_topic")
    db.set_active_progress("new", "New topic", 2, 5)
    h = control.make_command_handler(db, lambda: None, lambda: "started")
    assert "no longer current" in h("cprog", db.run_key("old"))


def test_generic_card_reply_does_not_silently_choose_latest_run(db, monkeypatch, tmp_path):
    from src import probe, free_llm
    db.create_run("unrelated", "01_topic")
    monkeypatch.setattr(free_llm, "ready", lambda: False)
    seen = []
    original = probe.evidence
    monkeypatch.setattr(probe, "evidence", lambda db, run: seen.append(run) or original(db, run))
    probe.probe_system("Why?\n\n[Context from replied message]: Topic ideas", db, tmp_path)
    assert seen == [None]


def test_upload_rights_and_review_are_preserved(db, tmp_path, monkeypatch):
    ref, rights = telegram_images.parse_caption("1 | source: https://example.org/photo | licence: CC BY 4.0 | licence_url: https://creativecommons.org/licenses/by/4.0/ | author: Photographer")
    payload = io.BytesIO()
    Image.new("RGB", (100, 100)).save(payload, format="PNG")
    monkeypatch.setattr(telegram_images, "write_contact_sheet", lambda *a: None)
    topic_id = telegram_images.add_image(db, tmp_path, ref, rights, payload.getvalue(), "operator")
    credits = json.loads((tmp_path / topic_id / "credits.json").read_text())
    assert credits["reviewed"] is False
    assert credits["images"][0]["source_url"] == rights["source"]
    assert credits["images"][0]["attribution_required"] is True
    with pytest.raises(ValueError):
        telegram_images.parse_caption("1 | licence: All rights reserved")
    db.create_run("busy", topic_id)
    with pytest.raises(ValueError, match="producing"):
        telegram_images.add_image(db, tmp_path, ref, rights, payload.getvalue(), "operator")


def test_fetch_is_topic_specific_and_does_not_widen_licences(db, tmp_path, monkeypatch):
    from scripts import fetch_commons_images
    topics = tmp_path / "topics"
    topics.mkdir()
    (topics / "image_queries.json").write_text(json.dumps({"01_topic": {"keywords": ["topic"], "queries": ["topic"]}}))
    monkeypatch.setattr(config, "TOPICS_DIR", topics)
    calls = []
    monkeypatch.setattr(fetch_commons_images, "fetch_topic", lambda topic, spec, allow_sa, **kw: calls.append((topic["id"], allow_sa, kw["assets_dir"])) or 6)
    assert telegram_images.fetch_images(db, tmp_path / "assets", "1") == ("01_topic", 6)
    assert calls == [("01_topic", False, tmp_path / "assets")]


def test_images_slash_command_has_topic_actions(db, tmp_path):
    h = control.make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path, send_message=lambda *a: True)
    text, buttons = h("images", "")
    actions = [b["callback_data"] for row in buttons for b in row]
    assert "imagepick:0" in actions and "cdash:" in actions


def test_swipe_reply_preserves_run_from_buttons(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_background", lambda: SimpleNamespace(submit=lambda fn, *args: fn(*args)))
    monkeypatch.setattr(bot, "_api", lambda method, **kw: [{"update_id": 1, "message": {
        "text": "Why did this fail?", "from": {"id": 555}, "chat": {"id": 555},
        "reply_to_message": {"text": "Review card", "reply_markup": {"inline_keyboard": [[{"callback_data": "approve:12"}]]}}
    }}] if method == "getUpdates" else {})
    bot.poll_once(lambda command, args: calls.append((command, args)) or "")
    bot.drain()
    if bot._workers is not None:
        bot._workers.shutdown(wait=True)
        bot._workers = None
    assert calls[0][0] == "ask" and calls[0][1].startswith("run 12 ")


def test_unauthorized_image_upload_cannot_download_or_write(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    methods = []
    def api(method, **kwargs):
        methods.append(method)
        return [{"update_id": 1, "message": {"from": {"id": 999}, "chat": {"id": 555}, "photo": [{"file_id": "image"}], "caption": "/image 1"}}] if method == "getUpdates" else {}
    monkeypatch.setattr(bot, "_api", api)
    bot.poll_once(lambda *_: "")
    bot.drain()
    assert "getFile" not in methods


def test_research_completes_on_originating_panel(db, monkeypatch):
    import main
    from src import topic_proposer
    updates = []
    target = (42, False, 1)
    def start(title, work, **kwargs):
        updates.append((title, work(), kwargs))
    bot = SimpleNamespace(start_operator_job=start)
    monkeypatch.setattr(topic_proposer, "propose", lambda _: [object()])
    trigger = main._proposal_trigger(db, bot)
    assert trigger() == ""
    assert len(updates) == 1 and "Research complete" in updates[0][1][0]


def test_background_completion_does_not_overwrite_a_newer_screen(db, monkeypatch):
    bot, calls = callback_bot(db, monkeypatch, "unused:")
    bot._panel_revisions[42] = 2
    bot.update_navigation_target((42, False, 1), "Research finished", [[control.HOME]])
    assert not any(method == "sendMessage" for method, _ in calls)
    assert not any(method == "editMessageText" for method, _ in calls)


def test_missing_explicit_run_is_not_replaced_by_latest(db):
    from src import probe
    db.create_run("latest", "01_topic")
    assert probe.resolve_run(db, "run 999999 why?") is None
    assert "named run was not found" in probe.probe_system("run 999999 why?", db)


def test_general_reply_has_useful_records_without_a_model():
    from src import probe
    text = probe._deterministic({"replied card (quoted text, not independent evidence)": "Dashboard",
                                 "publishing account check": {"checked_at": "2026-10-22", "problems": []}})
    assert "No publishing-account problems" in text
    assert "No run was selected" in text
