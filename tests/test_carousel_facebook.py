"""Facebook shows a multi-photo post as a grid, so its copy of the carousel has no swipe cues."""
from PIL import Image, ImageDraw

from src.multi_format import MultiFormatGenerator


def _render(tmp_path, monkeypatch, facebook):
    drawn = []
    real_text = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text",
                        lambda self, xy, text, *a, **k: drawn.append(str(text)) or real_text(self, xy, text, *a, **k))
    photos = []
    for i in range(3):
        p = tmp_path / f"0{i}.jpg"
        Image.new("RGB", (1200, 1500), (40 * i, 60, 90)).save(p)
        photos.append(p)
    cards = [{"heading": "The dome", "body": "Built in concrete."}, {"heading": "The mix", "body": "Lime clasts."},
             {"heading": "Your view", "body": "Which mattered more?"}]
    payload = {"carousel_slides": cards, "title": "T", "poll": {}}
    slides = MultiFormatGenerator().generate_carousel_sequence(photos, payload, tmp_path, facebook=facebook)
    return slides, drawn


def test_facebook_copy_has_no_swipe_cue_or_counter(tmp_path, monkeypatch):
    slides, drawn = _render(tmp_path, monkeypatch, facebook=True)
    assert len(slides) == 3 and all(s.parent.name == "carousel_fb" for s in slides)
    assert not any("SWIPE" in t or " OF 0" in t or "ARCHIVAL RECORD" in t or "// 0" in t or " DATA" in t for t in drawn)
    assert any("RELIC & RIDDLE" in t for t in drawn)  # the brand mark stays


def test_instagram_copy_keeps_its_swipe_cues(tmp_path, monkeypatch):
    slides, drawn = _render(tmp_path, monkeypatch, facebook=False)
    assert all(s.parent.name == "carousel" for s in slides)
    assert any("SWIPE" in t for t in drawn) and any(" OF 03" in t for t in drawn)
    assert any("ARCHIVAL RECORD // 01 OF 03" in t for t in drawn)
