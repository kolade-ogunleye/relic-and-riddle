"""No rendered text is ever cut mid-sentence (operator, 2026-10-25: a posted Kailasa Story frame and an
Antikythera frame ended on "... and model the", because every renderer kept only its first N lines)."""
import json

import pytest
from PIL import Image, ImageDraw

from src.typography import fit_lines, get_font

D = ImageDraw.Draw(Image.new("RGB", (10, 10)))
LONG = ("The bronze gear train was built to track solar and lunar movements through the zodiac, predict eclipses, "
        "and model the Moon's variable speed across the sky. Its maker remains unknown.")


def test_checked_copy_fits_whole_or_refuses_the_box():
    font = get_font("Montserrat", 40)
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        fit_lines(D, LONG, font, 860, 3)  # keeping only the first sentence loses the qualification
    lines, f = fit_lines(D, LONG, font, 860, 5)
    assert " ".join(lines) == " ".join(LONG.split())
    assert f.size <= 40 and len(lines) <= 5
    short, f2 = fit_lines(D, "Thirty miles across.", font, 860, 3)
    assert short == ["Thirty miles across."] and f2 is font
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        fit_lines(D, LONG.split(". ")[0] * 3, font, 300, 2)  # refuse overflow too


def test_story_frames_draw_their_text_whole(tmp_path):
    from src.multi_format import MultiFormatGenerator
    drawn = []
    real = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **k):
        drawn.append(str(text))
        return real(self, xy, text, *a, **k)
    ImageDraw.ImageDraw.text = spy
    try:
        hero = tmp_path / "hero.jpg"
        Image.new("RGB", (1080, 1920), (50, 50, 50)).save(hero)
        payload = {"title": "T", "narration_script": "x", "first_comment": "?", "hashtags": ["#x"],
                   "story_slides": [{"kicker": "K", "line1": "A", "line2": "B", "body": LONG}] * 3,
                   "poll": {"question": "Which?", "options": ["One", "Two"]}}
        MultiFormatGenerator().generate_story_sequence(hero, payload, tmp_path)
    finally:
        ImageDraw.ImageDraw.text = real
    # The body is drawn at its full size (never shrunk, 2026-10-25), so it may take more lines; what is drawn
    # is whole sentences, never a fragment ending on "the".
    joined = " ".join(drawn)
    assert "across the sky." in joined                                          # the first sentence, whole
    assert not any(t.rstrip().endswith((" the", " and model the")) for t in drawn[-1:])


def test_the_audit_flags_a_story_frame_whose_text_was_shortened(tmp_path):
    from src.video_qa import QualityAuditor
    (tmp_path / "stories").mkdir()
    frame = tmp_path / "stories" / "story_01_hook.jpg"
    im = Image.new("RGB", (1080, 1920), (40, 40, 40)); ImageDraw.Draw(im).text((500, 900), "x", fill=(255, 255, 255)); im.save(frame)
    entry = {"frame": frame.name, "size": [1080, 1920], "text_box": [110, 300, 970, 900], "safe_box": [65, 269, 1015, 1248],
             "body_source": "It tracked the Moon. It predicted eclipses.", "body_drawn": "It tracked the Moon."}
    (tmp_path / "stories" / "layout.json").write_text(json.dumps([entry]), encoding="utf-8")
    found = [i for i in QualityAuditor().audit_story_cards(tmp_path)["issues"] if i["type"] == "story_text_incomplete"]
    assert found and found[0]["severity"] == "major"
    entry["body_drawn"] = entry["body_source"]
    (tmp_path / "stories" / "layout.json").write_text(json.dumps([entry]), encoding="utf-8")
    assert not [i for i in QualityAuditor().audit_story_cards(tmp_path)["issues"] if i["type"] == "story_text_incomplete"]
