"""Freeze pass 2026-11-04: ffmpeg's vignette `aspect` is the pixel aspect (SAR), not the frame's. 16/9 on a
square-pixel 1920x1080 frame crushed the 16:9 corners to black; 9:16 keeps its approved chain byte for byte."""
import shutil
import subprocess

import numpy as np
import pytest

from src.video_composer import picture_vignette


def test_chains():
    assert picture_vignette(1080, 1920) == "vignette=PI/4.2:aspect=9/16"
    assert picture_vignette(1920, 1080) == "vignette=PI/6.4:aspect=1"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_16x9_corners_keep_their_picture():
    w, h = 1920, 1080
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x808080:s={w}x{h}:d=0.04",
                          "-vf", f"{picture_vignette(w, h)},format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    y = np.frombuffer(raw, np.uint8).reshape(h, w).astype(float)
    centre = y[h // 2 - 32:h // 2 + 32, w // 2 - 32:w // 2 + 32].mean()
    for cy, cx in ((0, 0), (0, w - 64), (h - 64, 0), (h - 64, w - 64)):
        assert y[cy:cy + 64, cx:cx + 64].mean() >= 0.5 * centre      # was 0 (black) with aspect=16/9
