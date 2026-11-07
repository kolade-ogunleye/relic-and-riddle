"""The lens vignette is lifted while a graphic is on screen (2026-11-02)."""

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from src.video_composer import graphic_vignette_enable


def test_no_graphics_keeps_the_vignette_everywhere() -> None:
    assert graphic_vignette_enable([Path("motion_01.mp4"), Path("p.jpg")], [2.0, 3.0]) == ""


def test_graphic_spans_follow_the_scene_durations() -> None:
    scenes = [Path("motion_01.mp4"), Path("graphic_timeline.mp4"), Path("p.jpg"), Path("graphic_scale.mp4")]
    assert graphic_vignette_enable(scenes, [2.0, 4.5, 3.0, 2.5]) == (
        r":enable='not(between(t\,2.000\,6.500)+between(t\,9.500\,12.000))'")


def test_ffmpeg_lifts_the_vignette_only_inside_the_span(tmp_path) -> None:
    enable = graphic_vignette_enable([Path("a.jpg"), Path("graphic_x.mp4"), Path("b.jpg")], [1.0, 1.0, 1.0])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:s=160x90:d=3:r=10",
                    "-filter_complex", f"[0:v]format=yuv420p,vignette=PI/4.2:aspect=16/9{enable},format=gray[o]",
                    "-map", "[o]", "-fps_mode", "passthrough", str(tmp_path / "f_%02d.png")], check=True)
    corner = [np.asarray(Image.open(tmp_path / f"f_{i:02d}.png"))[1, 1] for i in range(1, 31)]
    assert all(v > 200 for v in corner[10:20])  # t 1.0-2.0: the graphic, no vignette
    assert all(v < 100 for v in corner[:10] + corner[21:])
