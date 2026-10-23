"""Everything one Approve publishes is in front of the reviewer, protected from forwarding; text can be
edited (and re-checked) from the card; the review window follows the posting slot; the account check
reports what really blocks publishing; the dashboard leads with what needs you."""

import json
import re

import pytest
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
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"edit": lambda r, changes: seen.append((r, changes)) or "ok"})
    assert f"[edit run {key}]" in h("edit", key)  # no copy: plain text (an empty run has no fields)
    h("edittext", f"{key} title: Carved From the Top Down")
    h("edittext", f"{key} A new caption.\nSecond line.")
    # Several fields in one reply (this one set the title to the whole text before).
    h("edittext", f"{key} title: 25-Mile Sahara Eye; first comment: What do you think, guys?")
    assert seen == [("run_x", {"title": "Carved From the Top Down"}),
                    ("run_x", {"caption": "A new caption.\nSecond line."}),
                    ("run_x", {"title": "25-Mile Sahara Eye", "first_comment": "What do you think, guys?"})]


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


def _review_with_copy(db):
    from src.credits import SHARE_ALIKE_NOTICE
    db.create_run("run_y", "01_topic")
    db.update_run("run_y", "pending_approval", script_payload={
        "title": "6-Ton Amber Room Stolen in 36 Hours", "first_comment": "What do you think?",
        "caption": "The room vanished.\nWhere did it go?\n#history\n\n📷 Someone | File:Room.jpg | CC BY 4.0\n" + SHARE_ALIKE_NOTICE,
        "carousel_caption": "Swipe through the clues."})
    return db.run_key("run_y")


def test_the_edit_card_offers_its_current_copy_to_copy(db):
    from src.control import edit_prompt
    key = _review_with_copy(db)
    text, buttons = edit_prompt(db.get_run("run_y")["script_payload"], str(key))
    block = re.search(r"<pre><code[^>]*>(.*?)</code></pre>", text, re.S).group(1)
    assert "title: 6-Ton Amber Room Stolen in 36 Hours" in block and "first comment: What do you think?" in block
    assert "caption: The room vanished.\nWhere did it go?" in block
    assert "#history" not in block                               # hashtags are kept automatically, not copied
    assert "📷" not in block and "CC BY-SA" not in block         # credits are kept automatically, not copied
    assert [b[0]["copy_text"]["text"] for b in buttons] == ["6-Ton Amber Room Stolen in 36 Hours", "What do you think?"]
    assert f"[edit run {key}]" in text


def test_the_copied_template_sent_back_parses_to_exactly_the_current_fields(db):
    import html as html_mod
    from src.control import edit_prompt
    key = _review_with_copy(db)
    text, _ = edit_prompt(db.get_run("run_y")["script_payload"], str(key))
    block = html_mod.unescape(re.search(r"<pre><code[^>]*>(.*?)</code></pre>", text, re.S).group(1))
    seen = []
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"edit": lambda r, changes: seen.append(changes) or "ok"})
    h("edittext", f"{key} {block}")
    assert seen == [{"title": "6-Ton Amber Room Stolen in 36 Hours", "first_comment": "What do you think?",
                     "carousel_caption": "Swipe through the clues.",
                     "caption": "The room vanished.\nWhere did it go?"}]


def test_a_blank_field_is_ignored_not_emptied(db):
    key = _review_with_copy(db)
    seen = []
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"edit": lambda r, changes: seen.append(changes) or "ok"})
    h("edittext", f"{key} title: \nfirst comment: A new question?")
    assert seen == [{"first_comment": "A new question?"}]


def _pasted(bot, text, seen):
    update = {"update_id": 77, "message": {"message_id": 501, "text": text, "date": int(__import__("time").time()),
                                           "from": {"id": 555}, "chat": {"id": 555}}}
    bot._process_update(update, lambda command, args: seen.append((command, args)) or "ok")


@pytest.fixture
def paste_bot(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "_api", lambda method, **kw: {"message_id": 900})
    return bot


def test_a_pasted_template_goes_to_the_review_whose_edit_card_was_opened(db, paste_bot):
    # Pasted into the chat, not sent as a reply: it used to be ignored.
    key = _review_with_copy(db)
    make_command_handler(db, lambda: None, lambda: "")("edit", str(key))
    seen = []
    _pasted(paste_bot, "title: A New Title\n\nfirst comment: Your view?", seen)
    paste_bot.drain()
    assert seen and seen[0][0] == "edittext" and seen[0][1].startswith(f"{key} title: A New Title")


def test_a_paste_long_after_the_edit_card_or_without_a_label_is_not_an_edit(db, paste_bot):
    import time as clock
    from src.control import EDIT_OPEN_KEY, EDIT_OPEN_SECONDS
    key = _review_with_copy(db)
    seen = []
    db.set_setting(EDIT_OPEN_KEY, json.dumps({"run": str(key), "at": clock.time() - EDIT_OPEN_SECONDS - 5}))
    _pasted(paste_bot, "title: Too late", seen)
    db.set_setting(EDIT_OPEN_KEY, json.dumps({"run": str(key), "at": clock.time()}))
    _pasted(paste_bot, "Just a note about the title: nothing to change", seen)
    paste_bot.drain()
    assert not any(command == "edittext" for command, _ in seen)


def test_the_template_separates_fields_and_still_parses_back(db):
    import html as html_mod
    from src.control import edit_prompt
    key = _review_with_copy(db)
    text, _ = edit_prompt(db.get_run("run_y")["script_payload"], str(key))
    block = html_mod.unescape(re.search(r"<pre><code[^>]*>(.*?)</code></pre>", text, re.S).group(1))
    assert "\n\nfirst comment:" in block and "\n\ncaption:" in block
    edited = block.replace("title: 6-Ton Amber Room Stolen in 36 Hours", "title: ").replace("What do you think?", "Thoughts?")
    seen = []
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"edit": lambda r, changes: seen.append(changes) or "ok"})
    h("edittext", f"{key} {edited}")
    assert "title" not in seen[0] and seen[0]["first_comment"] == "Thoughts?"   # blank title ignored
    assert seen[0]["caption"] == "The room vanished.\nWhere did it go?"


def test_hashtags_stay_unless_the_new_caption_brings_its_own():
    from src.credits import keep_hashtags
    old = "Old words.\n\n#AmberRoom #WWIIHistory"
    assert keep_hashtags(old, "New words.") == "New words.\n\n#AmberRoom #WWIIHistory"
    assert keep_hashtags(old, "New words.\n#Mystery") == "New words.\n#Mystery"   # replaced on purpose
    assert keep_hashtags("No tags.", "New words.") == "New words."
