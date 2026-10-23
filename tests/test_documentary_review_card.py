"""Documentary review: the card stays one collapsible page, shows the place, and the map gets a beat."""

from PIL import Image

from src import scene_planner
from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot, _tg_len


def _payload(words: int) -> dict:
    return {"title": "The Richat Structure", "narration_script": " ".join(["sediment"] * words),
            "first_comment": "What remains to be studied?", "tags": ["geology", "Sahara"],
            "location": {"name": "Ouadane", "country": "Mauritania"}, "delivery_profile": "deep_dive_16x9"}


def test_long_narration_is_shortened_inside_the_collapsible_quote():
    text = TelegramApprovalBot.format_card_text(_payload(900), "run_x_long")
    assert _tg_len(text) <= CARD_PAGE_LIMIT  # never paged as plain text
    assert "<blockquote expandable>" in text and "all 900 words" in text
    assert "📍 <i>Ouadane, Mauritania</i>" in text and "<b>Tags:</b> geology, Sahara" in text


def test_short_narration_is_shown_whole_and_a_missing_place_leaves_no_stray_comma():
    payload = _payload(40)
    payload["location"] = {}
    text = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert "shortened" not in text and "📍" not in text


def test_a_graphic_only_the_hook_names_follows_the_hook(tmp_path, monkeypatch):
    graphic = tmp_path / "graphic_map.mp4"
    graphic.write_bytes(b"x")
    Image.new("RGB", (8, 8)).save(graphic.with_suffix(".jpg"))
    beats = [{"text": "The Richat Structure lies near Ouadane in Mauritania."},
             {"text": "It is visible from space."}, {"text": "Its origin was debated."}]
    monkeypatch.setattr(scene_planner.graphic_match, "score", lambda g, text: 4 if "Mauritania" in text else 0)
    assert scene_planner.lock_narrative_graphics(beats, [graphic]) == {1: graphic}
