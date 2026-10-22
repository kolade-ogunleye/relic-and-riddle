"""Everything one Approve publishes is in front of the reviewer, protected from forwarding; text can be
edited (and re-checked) from the card; the review window follows the posting slot; the account check
reports what really blocks publishing; the dashboard leads with what needs you."""

import json
from datetime import datetime, timezone

from PIL import Image

from src import accounts
from src.control import dashboard_text_and_buttons, make_command_handler
from src.telegram_bot import TelegramApprovalBot


def _bot(db, monkeypatch):
    calls = []
    bot = TelegramApprovalBot(db=db, bot_token="123:abcdefghijklmnopqrstuvwxyz", chat_id="555", approver_ids={"555"})

    def api(method, **kw):
        calls.append((method, kw.get("data", {}), sorted((kw.get("files") or {}).keys())))
        return {"message_id": 70 + len(calls)}
    monkeypatch.setattr(bot, "_api", api)
    return bot, calls


def test_review_shows_cover_strip_video_and_every_story_and_slide(db, monkeypatch, tmp_path):
    run_dir = tmp_path / "run_x"
    (run_dir / "stories").mkdir(parents=True)
    (run_dir / "carousel").mkdir()
    for i in range(1, 4):
        Image.new("RGB", (108, 192)).save(run_dir / "stories" / f"story_0{i}.jpg")
    for i in range(1, 8):
        Image.new("RGB", (108, 135)).save(run_dir / "carousel" / f"carousel_slide_0{i}.jpg")
    cover, video = run_dir / "cover.jpg", run_dir / "review_proxy.mp4"
    Image.new("RGB", (108, 192)).save(cover)
    video.write_bytes(b"0" * 1000)
    monkeypatch.setattr("src.telegram_bot.scene_strip", lambda v, out: Image.new("RGB", (60, 40)).save(out) or out)
    db.create_run("run_x", "01_topic")
    db.update_run("run_x", "pending_approval", master_video_path=video, thumbnail_cover_path=cover, script_payload={"title": "T", "topic_id": "01_topic"})
    bot, calls = _bot(db, monkeypatch)
    bot.send_approval_request(video, cover, {"title": "T", "topic_id": "01_topic"}, "run_x")
    albums = [c for c in calls if c[0] == "sendMediaGroup"]
    assert len(albums) == 2
    first, second = (json.loads(c[1]["media"]) for c in albums)
    assert [m["type"] for m in first] == ["photo", "photo", "video"]          # cover, scene strip, video
    assert len(second) == 10 and "3 Story frame(s)" in second[0]["caption"]  # 3 stories + 7 slides
    assert all(c[1].get("protect_content") == "true" for c in albums)
    card = [c for c in calls if c[0] == "sendMessage"][-1][1]
    buttons = [b["callback_data"] for row in json.loads(card["reply_markup"])["inline_keyboard"] for b in row]
    assert any(b.startswith("edit:") for b in buttons) and any(b.startswith("approve:") for b in buttons)


def test_edit_prompt_and_reply_reach_the_edit_action(db):
    db.create_run("run_x", "01_topic")
    key = db.run_key("run_x")
    seen = []
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"edit": lambda r, f, t: seen.append((r, f, t)) or "ok"})
    assert f"[edit run {key}]" in h("edit", key)
    h("edittext", f"{key} title: Carved From the Top Down")
    h("edittext", f"{key} A new caption.\nSecond line.")
    assert seen == [("run_x", "title", "Carved From the Top Down"), ("run_x", "caption", "A new caption.\nSecond line.")]


def test_account_problems_are_plain_and_specific():
    res = {"upload_post": {"configured": True, "connected": ["tiktok"], "reauth": [], "facebook_page_configured": False},
           "meta": {"configured": False}}
    probs = accounts.problems(res)
    assert probs == ["Upload-Post profile has no instagram or facebook account connected: connect it in the Upload-Post dashboard."]
    res["upload_post"]["connected"] = ["facebook", "instagram"]
    assert accounts.problems(res) == ["UPLOAD_POST_FACEBOOK_PAGE_ID is not set (Facebook posts and analytics need the Page ID)."]


def test_dashboard_leads_with_what_needs_you(db):
    db.create_run("run_x", "01_topic")
    db.record_approval("run_x", "01_topic", 9)
    db.set_setting(accounts.SETTING, json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(),
                                                 "schema_version": accounts.SCHEMA_VERSION, "configuration": accounts.configuration_fingerprint(),
                                                 "upload_post": {"configured": True, "connected": []}}))
    text, buttons = dashboard_text_and_buttons(db)
    assert text.index("Needs you") < text.index("Schedule")
    assert "1 review(s) waiting" in text and "no instagram or facebook account" in text
    assert buttons[0][0]["callback_data"] == "creviews:"
    assert "COCKPIT" not in text and "Zero-Trust" not in text


def test_review_window_ends_before_the_slot_and_the_next_daily_run(monkeypatch):
    import main
    from datetime import timedelta
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    monkeypatch.setattr(c, "_booked_reel_days", lambda: set(), raising=False)
    monkeypatch.setattr(main.post_schedule, "next_reel_slot", lambda n, b: now + timedelta(hours=5))
    w = c._approval_window()
    assert 3600 <= w <= 5 * 3600 - 20 * 60 + 5
    monkeypatch.setattr(main, "POST_SCHEDULING", False)
    from src.config import APPROVAL_TIMEOUT_SECONDS
    assert c._approval_window() == APPROVAL_TIMEOUT_SECONDS
