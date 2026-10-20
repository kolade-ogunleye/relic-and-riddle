import json
import time
from pathlib import Path
from PIL import Image
import pytest

from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot
from src.control import make_command_handler
from src.topic_proposer import proposal_text
from src.models import TopicProposal, ProposedLocation, Source


def test_db_audit_log_and_media_cache(tmp_path):
    db_file = tmp_path / "test.db"
    db = PipelineDatabase(db_path=db_file)

    # 1. Audit log
    db.record_audit_event("123", username="alice", action="command", payload="/status", status="allowed")
    db.record_audit_event("999", username="intruder", action="command", payload="/pause", status="denied")

    logs = db.list_audit_logs(limit=10)
    assert len(logs) == 2
    assert logs[0]["user_id"] == "999"
    assert logs[0]["status"] == "denied"
    assert logs[1]["user_id"] == "123"
    assert logs[1]["status"] == "allowed"

    # 2. Telegram media cache
    assert db.get_cached_telegram_media("cover:run1:a") is None
    db.set_cached_telegram_media("cover:run1:a", "file_id_abc123", "photo")
    assert db.get_cached_telegram_media("cover:run1:a") == "file_id_abc123"


def test_poll_once_rbac_audit_logging(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    db = PipelineDatabase(db_path=db_file)

    bot = TelegramApprovalBot(db=db, bot_token="token", chat_id="555", approver_ids={"123"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append((method, kw)) or [])

    # 1. Unauthorized command
    unauth_update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 999, "username": "intruder"},
            "chat": {"id": 555},
            "text": "/pause"
        }
    }
    monkeypatch.setattr(bot, "_api", lambda method, **kw: [unauth_update] if method == "getUpdates" else [])
    handled = []
    bot.poll_once(command_handler=lambda c, a: handled.append((c, a)) or "ok", timeout=0)
    assert len(handled) == 0  # Not executed
    logs = db.list_audit_logs()
    assert len(logs) == 1
    assert logs[0]["status"] == "denied"
    assert logs[0]["user_id"] == "999"

    # 2. Authorized command
    auth_update = {
        "update_id": 2,
        "message": {
            "message_id": 11,
            "from": {"id": 123, "username": "admin"},
            "chat": {"id": 555},
            "text": "/status"
        }
    }
    monkeypatch.setattr(bot, "_api", lambda method, **kw: [auth_update] if method == "getUpdates" else [])
    bot.poll_once(command_handler=lambda c, a: handled.append((c, a)) or "ok", timeout=0)
    assert len(handled) == 1
    assert handled[0][0] == "status"
    logs = db.list_audit_logs()
    assert len(logs) == 2
    assert logs[0]["status"] == "allowed"
    assert logs[0]["user_id"] == "123"


def test_queue_readiness_badges_and_render_button(tmp_path):
    bank_file = tmp_path / "topic_bank.json"
    bank_file.write_text(json.dumps({"topics": [
        {"id": "01_ready", "title": "Ready Topic", "hook": "h1", "location": {"name": "L1", "country": "C1"}},
        {"id": "02_review", "title": "Review Topic", "hook": "h2", "location": {"name": "L2", "country": "C2"}},
        {"id": "03_script", "title": "Script Topic", "hook": "h3", "location": {"name": "L3", "country": "C3"}}
    ]}), encoding="utf-8")
    db = PipelineDatabase(db_path=tmp_path / "test.db", topic_bank_file=bank_file)

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()

    # Topic 1: Ready to Render (reviewed=True, >=6 images)
    d1 = assets_dir / "01_ready"
    d1.mkdir()
    from src.credits import save_credits
    imgs1 = [{"file": f"img{i}.jpg", "title": f"T{i}", "license": "CC"} for i in range(6)]
    for i in range(6):
        (d1 / f"img{i}.jpg").write_text("dummy")
    save_credits(d1, {"reviewed": True, "images": imgs1})

    # Topic 2: Needs Image Review (reviewed=False, 6 images)
    d2 = assets_dir / "02_review"
    d2.mkdir()
    for i in range(6):
        (d2 / f"img{i}.jpg").write_text("dummy")
    save_credits(d2, {"reviewed": False, "images": imgs1})

    # Topic 3 has no assets yet (Script Ready)

    rendered = []
    h = make_command_handler(db, lambda: None, lambda: rendered.append("run") or "Started run", assets_dir=assets_dir)

    q_res = h("queue", "")
    assert "🟢 Ready to Render" in q_res
    assert "🟡 Needs Image Review" in q_res
    assert "🔵 Script Ready" in q_res

    # One-tap render #1 button triggers render
    qrender_res = h("qrender", "01_ready")
    assert "Started run" in qrender_res
    assert len(rendered) == 1


def test_cover_flip_and_lock(tmp_path, monkeypatch):
    run_dir = Path("output") / "run_test_overhaul_cover"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (100, 100), color="blue").save(run_dir / "thumbnail_variant_a_blueprint.jpg")
        Image.new("RGB", (100, 100), color="red").save(run_dir / "thumbnail_variant_b_intrigue.jpg")
        Image.new("RGB", (100, 100), color="green").save(run_dir / "thumbnail_variant_c_minimal.jpg")

        db = PipelineDatabase(db_path=tmp_path / "test.db")
        bot = TelegramApprovalBot(db=db, bot_token="token", chat_id="555", approver_ids={"123"})

        api_calls = []
        def fake_api(method, **kw):
            api_calls.append((method, kw))
            if method == "editMessageMedia":
                return {"message_id": 42, "photo": [{"file_id": "telegram_fid_variant_a"}]}
            return {}

        monkeypatch.setattr(bot, "_api", fake_api)

        # 1. Flip cover in-place to variant A
        flipped = bot.flip_cover(42, "run_test_overhaul_cover", "a")
        assert flipped is True
        assert api_calls[-1][0] == "editMessageMedia"
        # Media cache saved file_id
        assert db.get_cached_telegram_media("cover:run_test_overhaul_cover:a") == "telegram_fid_variant_a"

        # 2. Next flip to variant A uses cached file_id (<50ms zero-bandwidth)
        api_calls.clear()
        bot.flip_cover(42, "run_test_overhaul_cover", "a")
        media_arg = json.loads(api_calls[-1][1]["data"]["media"])
        assert media_arg["media"] == "telegram_fid_variant_a"

        # 3. Lock variant B
        h = make_command_handler(db, lambda: None, lambda: "")
        lock_res = h("covlock", "run_test_overhaul_cover:b")
        assert "Discovery Intrigue" in lock_res
        assert (run_dir / "thumbnail_cover.jpg").exists()
        assert (run_dir / "thumbnail_profile_grid.jpg").exists()

        # 4. View audit log command
        db.record_audit_event("123", username="admin", action="command", payload="/covers", status="allowed")
        audit_res = h("audit", "")
        assert "AUDIT LOG" in audit_res
    finally:
        import shutil
        if run_dir.exists():
            shutil.rmtree(run_dir)


def test_progress_card_rate_limiter_and_formatting(tmp_path, monkeypatch):
    db = PipelineDatabase(db_path=tmp_path / "test.db")
    bot = TelegramApprovalBot(db=db, bot_token="token", chat_id="555", approver_ids={"123"})

    api_calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: api_calls.append((method, kw)) or {"message_id": 999})

    # Start progress card
    mid = bot.send_progress_card("r_prog", "Derinkuyu Underground City")
    assert mid == 999
    initial_text = api_calls[-1][1]["data"]["text"]
    assert "Derinkuyu Underground City" in initial_text

    # Update step 1 -> step 2 immediately
    api_calls.clear()
    ok = bot.update_progress_card(mid, 2, 5, "Derinkuyu", "r_prog", "Synthesizing voiceover")
    assert ok is True
    assert len(api_calls) == 1
    prog_text = api_calls[-1][1]["data"]["text"]
    assert "Step 2/5" in prog_text
    assert "▰" in prog_text  # Smooth unicode progress bar

    # Rapid update on SAME step within 1.2s debounced by token bucket
    api_calls.clear()
    ok_suppressed = bot.update_progress_card(mid, 2, 5, "Derinkuyu", "r_prog", "Synthesizing voiceover (chunk 3)")
    assert ok_suppressed is True
    assert len(api_calls) == 0  # Suppressed by rate limiter!


def test_expandable_blockquotes_present():
    prop = TopicProposal(
        slug="test_wonder",
        title="The Sunken Temple",
        hook="Beneath the clear Mediterranean waters...",
        why_it_works="Visual clarity and deep historical intrigue.",
        sub_theme="subterranean",
        location=ProposedLocation(name="Thonis-Heracleion", city="", region="", country="Egypt"),
        key_facts=["Discovered in 2000", "Submerged for 1,200 years", "Contained 64 shipwrecks"],
        hashtags=["#archaeology"],
        image_queries=["temple"],
        image_keywords=["thonis"],
        sources=[Source(url="https://example.com/temple", title="Research")]
    )
    txt = proposal_text(prop)
    assert "<blockquote expandable>" in txt
    assert "</blockquote>" in txt
    assert "Key Verified Facts:" in txt


def test_startup_dashboard_card_and_cockpit(tmp_path):
    from src.control import startup_dashboard_card, dashboard_text_and_buttons
    bank_file = tmp_path / "topic_bank.json"
    bank_file.write_text(json.dumps({"topics": [
        {"id": "01_monolith", "title": "Kailasa Monolith", "hook": "h1", "location": {"name": "L1", "country": "C1"}}
    ]}), encoding="utf-8")
    db = PipelineDatabase(db_path=tmp_path / "test.db", topic_bank_file=bank_file)

    # 1. Startup dashboard card in bot-only mode
    card_txt, card_btns = startup_dashboard_card(db, mode="bot")
    assert "Studio Control Center Online" in card_txt
    assert "Interactive Control Center (Bot-only)" in card_txt
    assert len(card_btns) >= 2

    # 2. Startup dashboard card in daemon mode
    daemon_txt, _ = startup_dashboard_card(db, mode="daemon", time_str="09:00", timezone_str="America/Edmonton")
    assert "Full Autonomous Daemon" in daemon_txt
    assert "09:00 America/Edmonton" in daemon_txt

    # 3. Touch-first Executive Cockpit (/dashboard)
    sent_msgs = []
    h = make_command_handler(db, lambda: "Wed 21 Oct 09:00 MDT", lambda: "Render triggered",
                             send_message=lambda t, b=None: sent_msgs.append((t, b)) or 42)

    dash_reply = h("dashboard", "")
    assert dash_reply == ""
    assert len(sent_msgs) == 1
    d_txt, d_btns = sent_msgs[-1]
    assert "EXECUTIVE COCKPIT" in d_txt
    assert "Kailasa Monolith" in d_txt
    assert any("qrefresh:" in b["callback_data"] for row in d_btns for b in row)
    assert any("ccov:" in b["callback_data"] for row in d_btns for b in row)

    # 4. Cockpit pause and resume toggle
    pause_reply = h("cpause", "")
    assert "Scheduler Paused!" in pause_reply
    assert "PAUSED" in pause_reply

    resume_reply = h("cresume", "")
    assert "Scheduler Resumed!" in resume_reply
    assert "ACTIVE" in resume_reply

    # 5. /help includes cockpit pointer
    help_reply = h("help", "")
    assert "/pause" in help_reply
    assert "/dashboard" in help_reply


def test_silent_notification_flag(tmp_path, monkeypatch):
    db = PipelineDatabase(db_path=tmp_path / "test.db")
    bot = TelegramApprovalBot(db=db, bot_token="token", chat_id="555", approver_ids={"123"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda m, **kw: calls.append((m, kw)) or {"message_id": 101})

    # Standard notification
    bot.notify("Actionable review required")
    assert "disable_notification" not in calls[-1][1]["data"]

    # Silent notification (for background heartbeats/telemetry)
    bot.notify("Silent heartbeat", silent=True)
    assert calls[-1][1]["data"]["disable_notification"] == "true"

