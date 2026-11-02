"""The 9:16 scale graphic (2026-11-01): the landmark's silhouette to true proportion, one line of meaning, every
line at a measured contrast inside the Reel text box."""

import json
from pathlib import Path

import pytest

from src import graphics

needs_ffmpeg = pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")


def _record(out: Path) -> dict:
    return json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))


@needs_ffmpeg
@pytest.mark.parametrize("fact,first", [
    ({"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu underground city", "approximate": True},
     "THE STATUE OF LIBERTY, 305 FT,"),
    ({"value": 32.0, "unit": "m", "dimension": "height", "subject": "Kailasa temple monolith", "approximate": True},
     "STATUE OF LIBERTY: 305 FT."),
])
def test_scale_says_what_the_comparison_means(tmp_path: Path, monkeypatch, fact, first) -> None:
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 3.5)
    out = graphics.render_scale(fact, tmp_path / "scale.mp4")
    rec = _record(out)
    texts = {e["name"]: e["text"] for e in rec["elements"]}
    assert texts["meaning0"] == first and texts["figure"].startswith("ABOUT ")
    x0, y0, x1, y1 = graphics.get_text_box(1080, 1920)
    for e in rec["elements"]:
        assert e["drawn"] >= 4.5, e
        b = e["box"]
        assert x0 <= b[0] and b[2] <= x1 and y0 <= b[1] and b[3] <= y1, e


def test_depth_wording_when_the_landmark_would_vanish() -> None:
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (1080, 1920)))
    assert graphics._silhouette(d, "Great Pyramid (original)", 540, 1000, 300, (180, 180, 180)) == pytest.approx(471)
    assert graphics._silhouette_width("Statue of Liberty (total)") == 0.36
