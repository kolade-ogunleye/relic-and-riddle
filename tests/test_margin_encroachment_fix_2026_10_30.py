import numpy as np
from PIL import Image, ImageDraw
from pathlib import Path
from src import still_layout
from src.video_qa import QualityAuditor


def test_story_card_margin_false_positive_eliminated(tmp_path):
    """Production 30 Oct: Antikythera story_01_hook.jpg was blocked by story_margin_encroachment
    because the transparent-model photo had high edge contrast (stddev=69.75 > 65), even though
    still_layout.json proved all text was cleanly positioned within safe bounds (x 84 to 996).
    With layout metadata present, the measured layout is authoritative."""
    story_dir = tmp_path / "stories"
    story_dir.mkdir()

    # Generate a background image with noisy/high-contrast edges (stddev > 75)
    noise = np.random.RandomState(42).randint(0, 255, (1920, 1080, 3), dtype=np.uint8)
    img = Image.fromarray(noise, "RGB")
    card_path = story_dir / "story_01_hook.jpg"
    img.save(card_path, quality=95)

    # 1. With still_layout.json recording a valid layout (no defects)
    lay = still_layout.Layout("story", (1080, 1920), (65, 269, 1015, 1248))
    lay.add("title", (100, 300, 800, 500))
    lay.add("body", (100, 520, 800, 700))
    still_layout.save(card_path, lay, {"renderer": "dummy"})

    auditor = QualityAuditor()
    res = auditor.audit_story_cards(story_dir)
    encroachments = [i for i in res["issues"] if i["type"] == "story_margin_encroachment"]
    assert not encroachments, f"Expected no false-positive encroachment, got {encroachments}"

    # 2. When still_layout records an actual defect (text leaves safe zone)
    bad_lay = still_layout.Layout("story", (1080, 1920), (65, 269, 1015, 1248))
    bad_lay.add("title", (10, 300, 1050, 500))  # x=10 leaves safe zone [65, ...]
    still_layout.save(card_path, bad_lay, {"renderer": "dummy"})

    res_bad = auditor.audit_story_cards(story_dir)
    bad_encroachments = [i for i in res_bad["issues"] if i["type"] == "story_margin_encroachment"]
    assert len(bad_encroachments) >= 1
    assert any("leaves its safe zone" in i["issue"] for i in bad_encroachments)


def test_carousel_slide_margin_false_positive_eliminated(tmp_path):
    """Same verification for carousel slides: layout metadata prevents high-contrast
    background photos from falsely triggering margin_encroachment."""
    carousel_dir = tmp_path / "carousel"
    carousel_dir.mkdir()

    noise = np.random.RandomState(42).randint(0, 255, (1800, 1440, 3), dtype=np.uint8)
    img = Image.fromarray(noise, "RGB")
    slide_path = carousel_dir / "carousel_slide_01.jpg"
    img.save(slide_path, quality=95)

    lay = still_layout.Layout("carousel", (1440, 1800), (80, 200, 1360, 1600))
    lay.add("heading", (120, 250, 1200, 400))
    still_layout.save(slide_path, lay, {"renderer": "dummy"})

    auditor = QualityAuditor()
    res = auditor.audit_carousel_slides(carousel_dir)
    encroachments = [i for i in res["issues"] if i["type"] == "margin_encroachment"]
    assert not encroachments, f"Expected no false-positive encroachment, got {encroachments}"
