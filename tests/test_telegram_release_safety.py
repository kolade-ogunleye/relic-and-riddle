"""Negative, restart and concurrency cases from the October Telegram audit."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src import accounts, control
from src.credits import load_credits, save_credits
from src.telegram_bot import TelegramApprovalBot
from src.telegram_state import decode, encode, get, put, review_fingerprint


def ready_review(db, root, run_id="review", mid=202):
    from src.telegram_state import initialize
    initialize(db)
    topic = db.list_all_topics()[0]["id"]
    directory = root / run_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "master.mp4").write_bytes(b"offline review video")
    Image.new("RGB", (20, 20)).save(directory / "thumbnail_cover.jpg")
    db.create_run(run_id, topic)
    db.update_run(run_id, "pending_approval", script_payload={"caption": "Original"},
                  master_video_path=directory / "master.mp4")
    db.record_approval(run_id, topic, mid)
    fingerprint = review_fingerprint(db, run_id)
    put(db, "review-card", f"{run_id}:{mid}", fingerprint)
    put(db, "review-media", run_id, fingerprint)
    return directory


def offline_bot(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append((method, kw)) or {"message_id": 300})
    return bot, calls


def click(mid=202):
    return {"id": "query", "data": "approve:review", "from": {"id": 555},
            "message": {"message_id": mid, "chat": {"id": 555}}}


def test_current_delivered_review_only_and_first_wins(db, tmp_path, monkeypatch):
    bot, calls = offline_bot(db, monkeypatch)
    ready_review(db, tmp_path)
    put(db, "review-card", "review:101", review_fingerprint(db, "review"))
    bot._handle_callback(click(101))
    assert db.get_approval("review")["decision"] == "pending"
    assert not any(method == "sendMessage" for method, _ in calls)
    assert bot._handle_callback(click()) == "approved"
    assert bot._handle_callback(click()) == "approved"
    assert db.get_approval("review")["decision"] == "approved"


@pytest.mark.parametrize("change", ["video", "copy", "expiry", "undelivered"])
def test_changed_or_unverified_review_cannot_approve(db, tmp_path, monkeypatch, change):
    bot, _ = offline_bot(db, monkeypatch)
    root = ready_review(db, tmp_path)
    if change == "video":
        (root / "master.mp4").write_bytes(b"different edit")
    elif change == "copy":
        db.update_run("review", "pending_approval", script_payload={"caption": "Changed"})
    elif change == "expiry":
        with db._conn() as conn:
            conn.execute("UPDATE approvals SET expires_at=0")
    else:
        put(db, "review-card", "review:202", None)
    bot._handle_callback(click())
    assert db.get_approval("review")["decision"] == "pending"


def test_copy_verification_racing_decision_does_not_commit(db, tmp_path):
    from main import PipelineCoordinator
    ready_review(db, tmp_path)
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
    coordinator.db = db
    coordinator.topic_engine = SimpleNamespace(get_topic_by_id=db.get_topic_by_id)
    coordinator._verify_final_copy = lambda *args: db.update_approval_decision("review", "approved")
    result = coordinator.edit_copy("review", "caption", "Changed")
    assert "Nothing was changed" in result
    assert db.get_run("review")["script_payload"]["caption"] == "Original"


def test_preview_failure_never_offers_approval(db, tmp_path, monkeypatch):
    bot, calls = offline_bot(db, monkeypatch)
    root = ready_review(db, tmp_path)
    monkeypatch.setattr(bot, "_send_review_media", lambda *args: (_ for _ in ()).throw(RuntimeError("upload failed")))
    assert bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", {}, "review") is None
    assert db.get_approval("review")["telegram_message_id"] is None
    assert not any("approve:" in str(kw) for _, kw in calls)


def test_restart_does_not_replay_run_and_bad_update_does_not_abort_batch(db, monkeypatch):
    updates = [{"update_id": 1, "message": "malformed"}, {"update_id": 2, "message": {
        "message_id": 44, "date": int(time.time()), "text": "/run", "from": {"id": 555}, "chat": {"id": 555}}}]
    calls = []
    for _ in range(2):
        bot, _ = offline_bot(db, monkeypatch)
        monkeypatch.setattr(bot, "_api", lambda method, **kw: updates if method == "getUpdates" else {"message_id": 300})
        bot.poll_once(lambda command, args: calls.append(command) or "")
        assert bot._offset == 3
    assert calls == ["run"]


def test_topic_paths_and_numeric_missing_assets_fail_closed(db, tmp_path):
    assets = tmp_path / "assets"
    outside = tmp_path / "outside"
    outside.mkdir()
    save_credits(outside, {"images": []})
    assets.mkdir()
    for ref in ("../outside", "..\\outside", str(outside), "C:outside", "\\\\host\\share"):
        with pytest.raises(ValueError):
            control._resolve_topic_dir(ref, assets, db)
    unrelated = assets / "01_unrelated"
    unrelated.mkdir()
    save_credits(unrelated, {"images": []})
    with pytest.raises(ValueError):
        control._resolve_topic_dir("1", assets, db)


def test_changed_image_snapshot_is_not_approved(db, tmp_path, monkeypatch):
    topic = db.list_all_topics()[0]["id"]
    directory = tmp_path / topic
    directory.mkdir()
    images = []
    for i in range(control.MIN_SCENE_IMAGES):
        Image.new("RGB", (20, 20)).save(directory / f"{i}.jpg")
        images.append({"file": f"{i}.jpg", "license": "PD", "title": str(i)})
    save_credits(directory, {"images": images, "reviewed": False})
    shown = []
    monkeypatch.setattr(control, "review_caption", lambda *args, **kw: "Review")
    handler = control.make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path,
                                           send_photo=lambda *args: shown.append(args) or True)
    handler("review", topic)
    target = shown[-1][2][0][0]["callback_data"].partition(":")[2]
    Image.new("RGB", (20, 20), "red").save(directory / "0.jpg")
    assert "changed" in handler("imgok", target)[0]
    assert not load_credits(directory)["reviewed"]


def test_queue_and_progress_stale_actions_are_fenced(db, monkeypatch):
    topic = db.list_all_topics()[0]["id"]
    db.record_topic_status(topic, "in_production")
    assert not db.drop_topic(topic)
    assert not db.bump_topic(topic)
    assert not db.requeue_topic(topic)
    db.create_run("old", topic)
    db.create_run("new", topic)
    db.set_active_progress("new", "New", 2, 5, message_id=77)
    bot, calls = offline_bot(db, monkeypatch)
    bot.finish_progress_card(66, "Old", "old", success=False)
    assert db.get_active_progress()["run_id"] == "new"
    monkeypatch.setattr(bot, "edit_message_text", lambda *args, **kw: False)
    bot.update_progress_card(77, 3, 5, "New", "new")
    assert not any(method == "sendMessage" for method, _ in calls)


def test_compact_callbacks_survive_restart(db):
    from src.telegram_state import initialize, prepare
    initialize(db)
    target = "cov:" + "long_topic_" * 20 + ":b"
    compact = encode(db, target)
    assert len(compact.encode()) <= 64
    assert decode(db, compact) == target
    result = prepare(db, {"reply_markup": json.dumps({"inline_keyboard": [[{"text": "Cover", "callback_data": target}]]})})
    assert json.loads(result["reply_markup"])["inline_keyboard"][0][0]["callback_data"] == compact


def test_published_and_missing_cover_never_report_success(db, tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    root = ready_review(db, tmp_path)
    Image.new("RGB", (20, 20), "red").save(root / "thumbnail_variant_a_blueprint.jpg")
    before = (root / "thumbnail_cover.jpg").read_bytes()
    db.update_run("review", "published")
    handler = control.make_command_handler(db, lambda: None, lambda: "")
    assert "cannot be changed" in handler("covlock", "review:a")
    assert (root / "thumbnail_cover.jpg").read_bytes() == before
    assert not handler("covlock", "missing:b").startswith("✅")


def test_health_unknown_stale_and_incomplete_integrity_never_pass(db):
    assert accounts.problems({"meta": {"configured": True, "valid": None, "error": "Timeout"}})
    assert "100%" not in TelegramApprovalBot.format_card_text({}, "unknown")
    assert "verification not recorded" in TelegramApprovalBot.format_card_text({}, "unknown")
    db.set_setting(accounts.SETTING, json.dumps({"checked_at": "2020-01-01T00:00:00+00:00", "problems": []}))
    assert "ready" not in accounts.status_line(db)


def test_album_uses_shared_formatter_and_caption_limit():
    from src.telegram_presentation import format_payload
    result = format_payload("sendMediaGroup", {"media": json.dumps([
        {"type": "photo", "media": "x", "parse_mode": "HTML", "caption": "<b><u>REVIEW</u></b>\n" + "🧱" * 2000}])})
    caption = json.loads(result["media"])[0]["caption"]
    assert "<u>" not in caption
    assert len(caption) <= 1024


def test_music_keys_do_not_collide_on_old_suffix():
    from src.music import track_key
    assert track_key({"file": "first/track_12345678.mp3"}) != track_key({"file": "second/other_12345678.mp3"})


def test_dry_run_progress_is_explicit(db, monkeypatch):
    bot, _ = offline_bot(db, monkeypatch)
    db.set_setting("run_mode:test", "dry_run")
    assert "publishing/scheduling skipped" in bot.progress_mode("test")
    assert "API costs" in bot.progress_mode("test")
    db.set_setting("run_mode:test", "live")
    assert "Live production" in bot.progress_mode("test")


def test_approval_is_handled_while_slow_navigation_runs(db, tmp_path, monkeypatch):
    # F12: one lightweight consumer. A slow panel (here 1.5 s) runs beside the poller, so an Approve
    # tap in the same batch is decided before the slow work finishes.
    bot, _ = offline_bot(db, monkeypatch)
    ready_review(db, tmp_path)
    updates = [{"update_id": 1, "callback_query": {"id": "a", "data": "cstat:", "from": {"id": 555},
                                                   "message": {"message_id": 50, "chat": {"id": 555}}}},
               {"update_id": 2, "callback_query": click()}]
    monkeypatch.setattr(bot, "_api", lambda method, **kw: updates if method == "getUpdates" else {"message_id": 300})
    started = time.monotonic()
    bot.poll_once(lambda command, args: time.sleep(1.5) or "status")
    assert time.monotonic() - started < 1.0
    assert db.get_approval("review")["decision"] == "approved"
    bot.drain()


def test_failed_job_keeps_a_masked_reason_and_history_is_bounded(db):
    # F16: an actionable, sanitized failure detail; finished-job history does not grow without limit.
    import concurrent.futures
    from src.operator_jobs import Jobs
    jobs = Jobs(db)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        jobs.submit(pool, "Fetch", lambda: (_ for _ in ()).throw(RuntimeError(
            "GET https://api.telegram.org/bot123456:abcdefghijklmnopqrstuvwxyz/getFile failed")), lambda job, r: True)
    failed = jobs.recent()[0]
    assert failed["state"] == "failed" and failed["error_type"] == "RuntimeError"
    assert "abcdefghijklmnopqrstuvwxyz" not in failed["error_detail"] and "getFile failed" in failed["error_detail"]
    for i in range(205):
        jobs._save({"id": f"done{i}", "state": "completed", "title": "t", "resource": ""})
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'operator_job:%'").fetchone()[0] <= 201


def test_reply_to_a_decided_card_keeps_its_run(db, tmp_path, monkeypatch):
    # F17: the card's run is stored with the message, so a reply still names the run after the
    # buttons are gone (decided, expired or edited cards).
    bot, _ = offline_bot(db, monkeypatch)
    ready_review(db, tmp_path)
    put(db, "message-run", "555:202", "review")
    seen = []
    reply = {"update_id": 9, "message": {"message_id": 210, "text": "why was this held?", "date": int(time.time()),
                                         "from": {"id": 555}, "chat": {"id": 555},
                                         "reply_to_message": {"message_id": 202, "text": "Master review (decided)"}}}
    monkeypatch.setattr(bot, "_api", lambda method, **kw: [reply] if method == "getUpdates" else {"message_id": 300})
    bot.poll_once(lambda command, args: seen.append((command, args)) or "answer")
    bot.drain()
    assert seen and seen[0][0] == "ask" and seen[0][1].startswith(f"run {db.run_key('review')} ")


def test_an_edit_resends_the_review_copy_not_the_master(db, tmp_path, monkeypatch):
    # The edited card failed with "A deliverable review video is required": the master (78 MB) was sent.
    from main import PipelineCoordinator
    root = ready_review(db, tmp_path)
    (root / "review_proxy.mp4").write_bytes(b"small")
    sent = []
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
    coordinator.db = db
    coordinator.topic_engine = SimpleNamespace(get_topic_by_id=db.get_topic_by_id)
    coordinator._verify_final_copy = lambda *args: None
    coordinator.telegram_bot = SimpleNamespace(_strip_buttons=lambda mid: None,
                                               send_approval_request=lambda video, thumb, payload, rid: sent.append(Path(video).name))
    monkeypatch.setattr("src.release.write_release_report", lambda run: {"checks": []})
    result = coordinator.edit_copy("review", {"title": "New title", "first_comment": "Your view?"})
    assert sent == ["review_proxy.mp4"] and "Title and first comment updated" in result
    assert db.get_run("review")["script_payload"]["first_comment"] == "Your view?"


def test_an_edited_caption_keeps_its_credits(db, tmp_path, monkeypatch):
    # A caption edit replaced the credit lines too; the release gate would then block Approve & Post.
    from main import PipelineCoordinator
    from src.credits import SHARE_ALIKE_NOTICE
    root = ready_review(db, tmp_path)
    (root / "review_proxy.mp4").write_bytes(b"small")
    credits = ["🎵 Music: “Theme” by Someone (CC BY 4.0)", SHARE_ALIKE_NOTICE, "📷 Someone | File:Room.jpg | CC BY 4.0"]
    run = db.get_run("review")
    db.update_run("review", run["status"], script_payload={**run["script_payload"],
                                                           "caption": "Old wording.\n\n" + "\n".join(credits)})
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
    coordinator.db = db
    coordinator.topic_engine = SimpleNamespace(get_topic_by_id=db.get_topic_by_id)
    checked = []
    coordinator._verify_final_copy = lambda payload, topic: checked.append(payload["caption"])
    coordinator.telegram_bot = SimpleNamespace(_strip_buttons=lambda mid: None,
                                               send_approval_request=lambda *a: None)
    monkeypatch.setattr("src.release.write_release_report", lambda run: {"checks": []})
    coordinator.edit_copy("review", {"caption": "New wording, my own."})
    caption = db.get_run("review")["script_payload"]["caption"]
    assert caption.startswith("New wording, my own.") and "Old wording" not in caption
    assert all(line in caption for line in credits)
    assert checked == [caption]  # the copy that is checked is the copy that is kept


def test_credits_already_in_the_new_caption_are_not_doubled():
    from src.credits import keep_attribution
    old = "Old.\n📷 A | File:x.jpg | CC BY 4.0"
    assert keep_attribution(old, "New.\n📷 A | File:x.jpg | CC BY 4.0").count("📷") == 1
    assert keep_attribution("Old, no credits.", "New.") == "New."


def _numbered_bot(db, monkeypatch, fail_card=False):
    """An offline bot whose sends get increasing message ids; records deletions."""
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    state = {"next": 400, "deleted": []}

    def api(method, **kw):
        data = kw.get("data") or {}
        if method == "deleteMessages":
            state["deleted"] += json.loads(data["message_ids"])
            return True
        if method == "sendMessage" and fail_card and "reply_markup" in data:
            raise RuntimeError("card send failed")
        if method == "sendMediaGroup":
            count = len(json.loads(data["media"]))
            ids = list(range(state["next"], state["next"] + count))
            state["next"] += count
            return [{"message_id": m} for m in ids]
        state["next"] += 1
        return {"message_id": state["next"] - 1}
    monkeypatch.setattr(bot, "_api", api)
    monkeypatch.setattr("src.telegram_bot.scene_strip", lambda video, out: None)
    return bot, state


def test_a_resent_review_replaces_the_previous_card_and_albums(db, tmp_path, monkeypatch):
    # Resend and Edit left every earlier card and its albums in the chat (dev, 2026-10-23).
    root = ready_review(db, tmp_path)
    video, thumb = root / "master.mp4", root / "thumbnail_cover.jpg"
    bot, state = _numbered_bot(db, monkeypatch)
    first = bot.send_approval_request(video, thumb, {"caption": "One", "title": "T"}, "review")
    first_messages = get(db, "review-messages", "review")
    assert first in first_messages and len(first_messages) >= 2
    second = bot.send_approval_request(video, thumb, {"caption": "Two", "title": "T"}, "review")
    assert second != first
    assert set(first_messages) | {202} <= set(state["deleted"])      # old card (and the original) gone
    assert not set(get(db, "review-messages", "review")) & set(state["deleted"])


def test_a_failed_resend_keeps_the_previous_review(db, tmp_path, monkeypatch):
    root = ready_review(db, tmp_path)
    video, thumb = root / "master.mp4", root / "thumbnail_cover.jpg"
    bot, _ = _numbered_bot(db, monkeypatch)
    bot.send_approval_request(video, thumb, {"caption": "One", "title": "T"}, "review")
    bot, state = _numbered_bot(db, monkeypatch, fail_card=True)
    assert bot.send_approval_request(video, thumb, {"caption": "Two", "title": "T"}, "review") is None
    assert state["deleted"] == []   # nothing removed before a new card is in the chat


@pytest.mark.parametrize("resource, parent", [("topic-research", "cideas:"), ("fetchimages:amber_room_heist", "cimages:"),
                                              ("mfetch:", "cmusic:"), ("uadd:123::0", "cdisc:"), ("jobs-unknown", None)])
def test_a_job_card_offers_the_screen_it_came_from(db, monkeypatch, resource, parent):
    # "Researching topic ideas" offered Cancel, Jobs and Dashboard, but no way back to Topic ideas.
    import threading
    bot, _ = offline_bot(db, monkeypatch)
    cards, done = [], threading.Event()
    monkeypatch.setattr(bot, "navigation_target", lambda: None)
    monkeypatch.setattr(bot, "notify", lambda text, buttons=None, **k: cards.append(buttons) or 900)
    monkeypatch.setattr(bot, "update_navigation_target", lambda target, text, buttons: cards.append(buttons) or done.set() or True)
    bot.start_operator_job("Job", lambda: (_ for _ in ()).throw(RuntimeError("stop")), resource=resource)
    assert done.wait(5)
    for buttons in cards:  # the running card, then the failed one
        flat = [b.get("callback_data") for row in buttons for b in row]
        assert (parent in flat) if parent else not any(c in flat for c in ("cideas:", "cimages:", "cmusic:", "cdisc:"))
        assert flat[-1] == "cdash:"   # the parent sits just above the dashboard
