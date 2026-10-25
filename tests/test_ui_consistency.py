"""
tests/test_ui_consistency.py
============================
The 2026-10-23 UI review: one heading and label style, line breaks where text was a wall, one name per
action, words never cut mid-word, and every published text visible on the review card.
"""

import json

from src import control
from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot, _tg_len
from src.telegram_presentation import format_html


def test_a_leading_emoji_stays_outside_the_bold_heading():
    assert format_html("ℹ️ Nothing to undo.") == "ℹ️ <b>Nothing to undo.</b>"
    assert format_html("<b>🗂 Topic origin</b>") == "🗂 <b>Topic origin</b>"
    assert format_html("🎯 <b>Next:</b> 22. Tunguska") == "🎯 <b>Next:</b> 22. Tunguska"
    assert format_html("<b>RELIC &amp; RIDDLE</b> · DEV").startswith("<b>Relic &amp; Riddle</b>")


def test_the_edit_card_is_a_heading_and_short_lines():
    text = control.edit_prompt({"title": "T", "caption": "C"}, "1")
    text = text[0] if isinstance(text, tuple) else text
    intro = text.split("<pre>")[0]
    assert intro.startswith("✏️ <b>Edit text</b>\n\n") and intro.count("\n• ") >= 5


def test_the_dashboard_puts_this_months_spend_on_one_line(db):
    summary = control.spend_summary(db)
    assert summary.startswith("💳 This month · ") and "\n" not in summary and "/$" in summary
    text, _ = control.dashboard_text_and_buttons(db)
    assert summary in text and "AI motion this month:" not in text


def test_one_name_for_rendering_the_next_topic(db):
    _, buttons = control.dashboard_text_and_buttons(db)
    labels = [b["text"] for row in buttons for b in row]
    assert any(l.startswith("⚡ Render #") and l.endswith(" now") for l in labels)
    assert not any(l.startswith("⚡ Make #") for l in labels)


def test_text_is_cut_at_a_word_with_an_ellipsis():
    assert control.clip_words("short", 10) == "short"
    cut = control.clip_words("Still waiting for your review of The Lost Amber Room (Catherine Palace)", 50)
    assert cut.endswith("…") and len(cut) <= 50 and not cut[:-1].endswith(("Cathe", "("))


def test_the_audit_log_names_what_was_done():
    assert control.audit_action_name("rsn:16:other") == "Reason"
    assert control.audit_action_name("cdash:") == "Dashboard"
    assert control.audit_action_name("/start") == ""


def test_every_run_status_has_an_icon(db):
    db.create_run("run_r", db.list_all_topics()[0]["id"])
    db.update_run("run_r", "regenerate")
    assert "🔄 Regenerated" in control.status_text(db, None, None)


def test_the_review_card_shows_the_captions_without_costing_the_narration():
    narration = " ".join(["word"] * 140)
    payload = {"title": "T", "narration_script": narration, "first_comment": "Q?",
               "caption": "Opening line. " + "More caption text. " * 150, "carousel_caption": "Swipe."}
    card = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert narration in card and "shortened:" not in card          # the narration is whole
    assert "<b>Caption:</b>" in card and "(full text: Edit)" in card
    assert _tg_len(card) <= CARD_PAGE_LIMIT


def test_a_short_caption_is_shown_whole():
    card = TelegramApprovalBot.format_card_text({"title": "T", "narration_script": "x", "caption": "A short caption.",
                                                 "carousel_caption": "Swipe."}, "run_x")
    assert "A short caption." in card and "Swipe." in card and "(full text: Edit)" not in card


def test_a_failed_run_is_named_on_the_dashboard_with_its_whole_reason_folded(db):
    # The reason was cut mid-sentence ("…or add a curated script at…") in the Needs-you line.
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_f", topic)
    reason = ("The script writer (gemini-free) made 3 attempts and each failed its checks (last: Narration runs "
              "35.8s when spoken). Run it again, or add a curated script at data/scripts/" + topic + ".json.")
    db.update_run("run_f", "failed", error="ScriptUnavailableError: " + reason)
    text, _ = control.dashboard_text_and_buttons(db)
    assert "❌ <b>Last run failed</b>: " in text
    assert f"<blockquote expandable>{reason}</blockquote>" in text      # whole, without the exception name


def test_the_card_caption_leaves_the_hashtags_to_their_own_line():
    # Operator, 2026-10-25: hashtags showed twice, in the caption and on their own line.
    from src.telegram_bot import without_hashtags
    caption = "Gears in a shipwreck.\nWhat else was lost?\n\n#history #antikythera #archaeology\nPhoto: Wikimedia (CC BY)"
    assert without_hashtags(caption) == "Gears in a shipwreck.\nWhat else was lost?\n\nPhoto: Wikimedia (CC BY)"
    assert without_hashtags("Lost at sea. #history #greece") == "Lost at sea."
    assert without_hashtags("The #1 mystery of the sea.") == "The #1 mystery of the sea."       # inside a sentence
    card = TelegramApprovalBot.format_card_text({"title": "T", "narration_script": "x", "caption": "A line. #one #two",
                                                 "hashtags": ["#one", "#two"], "carousel_caption": "Swipe. #one"}, "run_x")
    assert card.count("#one") == 1 and "<b>Hashtags:</b>" in card


def test_a_run_on_a_button_is_named_whole(db):
    # Operator, 2026-10-25: 'Re-render: "The Antikythera…' was cut off on the dashboard button.
    topic = db.list_all_topics()[0]
    db.create_run("run_b", topic["id"])
    name = control.button_run_name(db, "run_b")
    assert len(name) <= 26 and '"' not in name
    assert name.rsplit(" ", 1)[-1].count(":") == 1                                 # its time tells two runs apart


def test_story_body_text_is_never_shrunk_and_the_script_keeps_it_to_three_lines(curated_payload):
    # Operator, 2026-10-25: an Antikythera Story body was shrunk to 30 px over four lines, about 11 pt on a phone.
    from src import multi_format, script_engine
    from src.models import ScriptPackage
    long_body = ("The bronze gear train was built to track solar and lunar movements through the zodiac, predict "
                 "eclipses, and model the Moon's variable speed between apogee and perigee.")
    assert multi_format.story_body_lines(long_body) > multi_format.STORY_BODY_LINES
    pkg = ScriptPackage.model_validate(curated_payload)
    pkg.story_slides[1].body = long_body
    problems = " ".join(script_engine.validate_structure(pkg))
    assert "Story slide 2's body takes" in problems and "rewrite it in about" in problems
    pkg.story_slides[1].body = "Its gears tracked the Sun and Moon and predicted eclipses."
    assert "Story slide 2" not in " ".join(script_engine.validate_structure(pkg))


def test_the_dashboard_offers_one_action_per_topic(db, tmp_path, monkeypatch):
    # Operator, 2026-10-25: Re-render for an Antikythera run sat beside Render #4 now for the same topic.
    monkeypatch.setattr(control, "_worth_resuming", lambda run: True)
    nxt = db.get_next_topic()
    db.create_run("run_late", nxt["id"])
    db.update_run("run_late", "failed", error="Voiceover is 41.1s; too short for the 42s minimum.")
    db.record_topic_status(nxt["id"], "queued")
    db.create_run("run_other", db.list_all_topics()[-1]["id"])
    db.update_run("run_other", "published")
    _, buttons = control.dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    flat = [b["callback_data"] for row in buttons for b in row]
    assert any(c.startswith("rerender:") for c in flat) and not any(c.startswith("qrender:") for c in flat)
