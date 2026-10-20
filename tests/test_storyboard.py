import json
from pathlib import Path
import pytest
from PIL import Image

from src import storyboard
from src.credits import write_contact_sheet


def test_storyboard_generation_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(storyboard, "ASSETS_DIR", tmp_path)
    topic_dir = tmp_path / "sample_topic"
    topic_dir.mkdir(parents=True)

    # Create dummy images
    images = []
    for i in range(1, 6):
        im_name = f"0{i}_commons.jpg"
        im = Image.new("RGB", (300, 200), color=(i * 40, i * 30, i * 20))
        im.save(topic_dir / im_name, "JPEG")
        images.append({
            "file": im_name,
            "title": f"Archival shot of topic part {i}",
            "license": "CC BY 4.0",
        })

    credits_data = {"reviewed": False, "images": images}
    (topic_dir / "credits.json").write_text(json.dumps(credits_data), encoding="utf-8")

    # Generate storyboard
    sb = storyboard.generate_storyboard("sample_topic")
    assert len(sb) >= 5
    assert sb[0]["type"] == "HOOK"
    assert (topic_dir / "storyboard.json").is_file()

    # Verify contact sheet rendered
    assert (topic_dir / "contact_sheet.jpg").is_file()
    with Image.open(topic_dir / "contact_sheet.jpg") as cs:
        assert cs.width > 0 and cs.height > 0

    # Load storyboard
    loaded = storyboard.load_storyboard(topic_dir)
    assert loaded is not None
    assert len(loaded) == len(sb)
    assert loaded[0]["beat"] == 1
