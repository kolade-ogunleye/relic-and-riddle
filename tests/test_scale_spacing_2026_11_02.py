"""9:16 scale: clear air between the drawing and its words; the subject's block is labelled (2026-11-02)."""

import json
import shutil

import numpy as np
import pytest
from PIL import Image

from src import graphics

FFMPEG = shutil.which("ffmpeg")
DEPTH = {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu underground city depth",
         "approximate": True}
HEIGHT = {"value": 105.0, "unit": "ft", "dimension": "height", "subject": "Kailasa Temple monolith height",
          "approximate": True}


def _render(fact, tmp_path):
    clip = graphics.render_scale(fact, tmp_path / "graphic_scale.mp4")
    record = json.loads((tmp_path / "graphic_scale.mp4.contrast.json").read_text(encoding="utf-8"))
    last = np.asarray(Image.open(clip.with_suffix(".jpg")).convert("RGB"), dtype=np.int32)
    return record, last


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg renders the clip")
@pytest.mark.parametrize("fact", [DEPTH, HEIGHT])
def test_nothing_is_drawn_just_above_the_line_of_meaning(fact, tmp_path) -> None:
    record, last = _render(fact, tmp_path)
    meaning = next(e for e in record["elements"] if e["name"] == "meaning0")
    y0 = meaning["box"][1]
    band = last[y0 - 36:y0 - 4, 70:880]   # the 32 px of air above the words, across the Reel box
    sea = np.array(graphics.SEA)
    assert np.abs(band - sea).max() < 40   # background only: no ground section, ground line or drawing


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg renders the clip")
def test_the_height_block_carries_its_hedged_figure(tmp_path) -> None:
    record, _ = _render(HEIGHT, tmp_path)
    label = next(e for e in record["elements"] if e["name"] == "block_label")
    assert label["text"] == "ABOUT 105 FT" and label["drawn"] >= 4.5
