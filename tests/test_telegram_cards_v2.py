"""Tests for Telegram Bot Chat Upgrade v2:
- Durable SQLite card store with atomic CAS
- Two-Card Review Pair in topic 4908 (Reviews) & alert in 4907 (Needs you)
- 1-tap Approve & Post with Undo window
- 2-tap Reject with reason chips & 10m Undo
- Consolidated single-card publication receipt in topic 4909 (Runs)
- 6-hour archival reaper & expired card sweeper
- V1 fallback when TELEGRAM_CARDS_V2=False
"""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src import config
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot, PublishReceipt
from src.telegram_state import initialize, put, get, review_fingerprint


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ui02_review_v2" / "run_gol_gumbaz_whispering_gallery_1793278143"


def ready_v2_review(db, root, run_id="v2_run", mid=500, monkeypatch=None):
    """A real development run's records (UI-02: the V2 card shows only values the run records, so an invented
    payload now gets the live card), with a stand-in video and cover."""
    import shutil
    initialize(db)
    topic = db.list_all_topics()[0]["id"]
    directory = root / run_id
    shutil.copytree(FIXTURE, directory)
    (directory / "master.mp4").write_bytes(b"v2 master video preview data")
    Image.new("RGB", (30, 30), color="blue").save(directory / "thumbnail_cover.jpg")
    payload = json.loads((directory / "script_metadata.json").read_text(encoding="utf-8"))
    payload["topic_id"] = topic
    db.create_run(run_id, topic)
    db.update_run(run_id, "pending_approval", script_payload=payload, master_video_path=directory / "master.mp4")
    if monkeypatch is not None:   # the review's gate re-check returns the run's own report (a stand-in video)
        from src import release
        saved = json.loads((directory / "release_report.json").read_text(encoding="utf-8"))
        monkeypatch.setattr(release, "write_release_report", lambda run, dry_run=False: saved)
    return directory


def offline_v2_bot(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline_v2", chat_id="555", approver_ids={"555"})
    calls = []
    mid_counter = [1000]

    def mock_api(method, **kw):
        calls.append((method, kw))
        data = kw.get("data") or {}
        if method == "deleteMessages":
            return True
        if method == "deleteMessage":
            return True
        if method in ("sendVideo", "sendRichMessage", "sendMessage"):
            mid_counter[0] += 1
            return {"message_id": mid_counter[0]}
        if method in ("editMessageCaption", "editMessageReplyMarkup", "editMessageText"):
            return True
        return {"ok": True}

    monkeypatch.setattr(bot, "_api", mock_api)
    return bot, calls


def test_telegram_card_store_cas(tmp_path):
    db_file = tmp_path / "test_cas.db"
    db = PipelineDatabase(db_file)

    # 1. Put card
    card = db.put_telegram_card("review:run1", 4908, 1001, "review_pair", {"status": "waiting"})
    assert card["revision"] == 1
    assert card["payload"]["status"] == "waiting"

    # 2. Get card
    retrieved = db.get_telegram_card("review:run1")
    assert retrieved is not None
    assert retrieved["revision"] == 1

    # 3. Successful CAS update
    success = db.cas_update_telegram_card("review:run1", 1, {"status": "approved"})
    assert success is True
    updated = db.get_telegram_card("review:run1")
    assert updated["revision"] == 2
    assert updated["payload"]["status"] == "approved"

    # 4. Failed CAS update with stale revision
    fail_success = db.cas_update_telegram_card("review:run1", 1, {"status": "rejected"})
    assert fail_success is False
    fail_updated = db.get_telegram_card("review:run1")
    assert fail_updated["revision"] == 2
    assert fail_updated["payload"]["status"] == "approved"

    # 5. List cards by topic
    cards = db.list_telegram_cards(topic_id=4908)
    assert len(cards) == 1
    assert cards[0]["card_id"] == "review:run1"

    # 6. Expiry sweeping
    from datetime import datetime, timezone, timedelta
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    db.put_telegram_card("temp:chip", 4906, 1002, "chip", {"label": "test"}, expires_at=past)
    assert len(db.list_telegram_cards()) == 2
    swept = db.sweep_expired_telegram_cards()
    assert len(swept) == 1
    assert len(db.list_telegram_cards()) == 1


def test_send_review_pair_v2(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_v2_bot(db, monkeypatch)
    root = ready_v2_review(db, tmp_path, monkeypatch=monkeypatch)

    run = db.get_run("v2_run")
    video = root / "master.mp4"
    thumb = root / "thumbnail_cover.jpg"

    vid_mid = bot.send_approval_request(video, thumb, run["script_payload"], "v2_run")
    assert vid_mid is not None

    # Check API calls
    methods = [c[0] for c in calls]
    assert "sendVideo" in methods
    assert "sendRichMessage" in methods
    assert "sendMessage" in methods

    # Verify sendVideo targeted topic Reviews (4908)
    vid_call = next(c for c in calls if c[0] == "sendVideo")[1]
    assert vid_call["data"]["message_thread_id"] == 4908
    assert vid_call["data"]["disable_notification"] is True
    assert "reply_markup" in vid_call["data"]

    # Verify sendRichMessage targeted topic Reviews (4908)
    rich_call = next(c for c in calls if c[0] == "sendRichMessage")[1]
    assert rich_call["data"]["message_thread_id"] == 4908
    assert rich_call["data"]["disable_notification"] is True

    # Verify alert line targeted topic Needs you (4907)
    alert_call = next(c for c in calls if c[0] == "sendMessage" and c[1]["data"].get("message_thread_id") == 4907)[1]
    assert "Review waiting" in alert_call["data"]["text"]

    # Verify stored card in DB
    card = db.get_telegram_card("review:v2_run")
    assert card is not None
    assert card["card_kind"] == "review_pair"
    assert card["payload"]["status"] == "waiting"


def test_v2_one_tap_approve_and_undo(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_v2_bot(db, monkeypatch)
    root = ready_v2_review(db, tmp_path, monkeypatch=monkeypatch)
    run = db.get_run("v2_run")

    vid_mid = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", run["script_payload"], "v2_run")

    # 1. Tap Approve
    cq = {
        "id": "q1",
        "data": f"approve:{db.run_key('v2_run')}",
        "from": {"id": 555, "first_name": "Kolade"},
        "message": {"message_id": vid_mid, "chat": {"id": 555}},
    }
    decision = bot._handle_callback(cq, "v2_run")
    assert decision == "approved"
    assert db.get_approval("v2_run")["decision"] == "approved"

    # Card record in SQLite updated to approved
    card = db.get_telegram_card("review:v2_run")
    assert card["payload"]["status"] == "approved"

    # 2. Tap Undo approval
    undo_cq = {
        "id": "q2",
        "data": f"undoappr:{db.run_key('v2_run')}",
        "from": {"id": 555, "first_name": "Kolade"},
        "message": {"message_id": vid_mid, "chat": {"id": 555}},
    }
    bot._handle_callback(undo_cq, "v2_run")
    assert db.get_approval("v2_run")["decision"] == "pending"

    card = db.get_telegram_card("review:v2_run")
    assert card["payload"]["status"] == "waiting"


def test_v2_two_tap_reject_with_reasons_and_undo(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_v2_bot(db, monkeypatch)
    root = ready_v2_review(db, tmp_path, monkeypatch=monkeypatch)
    run = db.get_run("v2_run")

    vid_mid = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", run["script_payload"], "v2_run")

    # 1. Tap Reject (first tap)
    reject_cq = {
        "id": "q1",
        "data": f"reject:{db.run_key('v2_run')}",
        "from": {"id": 555, "first_name": "Kolade"},
        "message": {"message_id": vid_mid, "chat": {"id": 555}},
    }
    bot._handle_callback(reject_cq, "v2_run")
    assert db.get_approval("v2_run")["decision"] == "pending"

    # 2. Tap Keep it (cancels rejection flow and restores original buttons)
    keep_cq = {
        "id": "q2",
        "data": f"keepit:{db.run_key('v2_run')}",
        "from": {"id": 555, "first_name": "Kolade"},
        "message": {"message_id": vid_mid, "chat": {"id": 555}},
    }
    bot._handle_callback(keep_cq, "v2_run")
    assert db.get_approval("v2_run")["decision"] == "pending"

    # 3. Tap Reject again, then pick reason "facts" (improve.REASONS: UI-02)
    bot._handle_callback(reject_cq, "v2_run")
    reason_cq = {
        "id": "q3",
        "data": f"rejres:{db.run_key('v2_run')}:facts",
        "from": {"id": 555, "first_name": "Kolade"},
        "message": {"message_id": vid_mid, "chat": {"id": 555}},
    }
    decision = bot._handle_callback(reason_cq, "v2_run")
    assert decision == "rejected"
    assert db.get_approval("v2_run")["decision"] == "rejected"

    card = db.get_telegram_card("review:v2_run")
    assert card["payload"]["status"] == "rejected"
    assert card["payload"]["reason"] == "facts"

    # 4. Undo rejection
    undo_rej_cq = {
        "id": "q4",
        "data": f"undorej:{db.run_key('v2_run')}",
        "from": {"id": 555, "first_name": "Kolade"},
        "message": {"message_id": vid_mid, "chat": {"id": 555}},
    }
    bot._handle_callback(undo_rej_cq, "v2_run")
    assert db.get_approval("v2_run")["decision"] == "pending"


def test_v2_consolidated_publish_receipt(db, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_v2_bot(db, monkeypatch)

    receipt = bot.create_publish_receipt("run_receipt_1", "The Pyramids of Dahshur",
                                         platforms=["instagram", "facebook", "youtube"])
    assert receipt.message_id is not None

    receipt.update_platform("instagram", "uploading")
    receipt.update_platform("instagram", "live", "https://instagram.com/reel/123")
    receipt.update_platform("youtube", "live", "https://youtube.com/shorts/456")
    receipt.update_platform("facebook", "live", "https://facebook.com/reel/789")
    receipt.finalize(follow_up="◽ Its Stories and carousel follow as booked (see Posting).")

    # Verify that edits occurred on the single in-place message
    edits = [c for c in calls if c[0] == "editMessageText"]
    assert len(edits) >= 3


def test_v2_reap_decided_reviews(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_v2_bot(db, monkeypatch)
    root = ready_v2_review(db, tmp_path, monkeypatch=monkeypatch)

    # Store card with status 'approved' and updated_at 7 hours ago
    seven_hours_ago = time.time() - (7 * 3600)
    db.put_telegram_card(
        card_id="review:v2_run",
        topic_id=4908,
        message_id=1001,
        card_kind="review_pair",
        payload={"status": "approved", "video_mid": 1001, "card_mid": 1002},
    )
    with db._conn() as conn:
        conn.execute("UPDATE telegram_cards SET updated_at = ? WHERE card_id = 'review:v2_run'", (seven_hours_ago,))

    reaped = bot.reap_decided_reviews(max_hours=6.0)
    assert reaped == 1

    card = db.get_telegram_card("review:v2_run")
    assert card["payload"]["status"] == "archived"


def test_v1_fallback_when_disabled(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", False)
    bot, calls = offline_v2_bot(db, monkeypatch)
    root = ready_v2_review(db, tmp_path, monkeypatch=monkeypatch)
    run = db.get_run("v2_run")

    mid = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", run["script_payload"], "v2_run")
    assert mid is not None

    # In V1 fallback, sendRichMessage is NOT called
    methods = [c[0] for c in calls]
    assert "sendRichMessage" not in methods
