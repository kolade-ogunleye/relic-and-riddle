"""Immersive cover backdrop (2026-11-01): the edge-extended margin must not streak.

Regression: the margin outside the photograph repeated each edge column, so a textured edge (Richat's cloud
line) became hard vertical streaks across the top of Discovery / lower-third covers.
"""

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter1d

from src.cover_design import _ambient, _immersive
from src.still_layout import Layout


def _banded_edge_source() -> Image.Image:
    """A photo whose top edge is 60 px colour bands: the scale at which the old clamp streaked."""
    rng = np.random.default_rng(3)
    arr = np.full((600, 900, 3), 120, dtype=np.uint8)
    arr[:40] = np.repeat(rng.integers(0, 256, (1, 15, 3), dtype=np.uint8), 60, axis=1)
    return Image.fromarray(arr)


def test_immersive_margin_has_no_column_streaks() -> None:
    lay = Layout("cover", (1080, 1920), (0, 0, 1080, 1920))
    focus = {"focus_box": [0.3, 0.3, 0.7, 0.7], "sha256": "t", "reviewed": "t"}
    im = _immersive(_banded_edge_source(), (72, 560, 1008, 1100), focus, lay)
    top = lay.meta["composition"]["image_box"][1]
    assert top > 120
    profile = np.asarray(im.convert("L"), dtype=np.float32)[: top - 20].mean(axis=0)
    banding = profile - uniform_filter1d(profile, 400, mode="nearest")
    assert float(banding.std()) < 6.0  # the column clamp measured 15.2 on this source


def test_ambient_keeps_local_colour_and_dims_far_field() -> None:
    w, h = 1080, 1920
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[: h // 2] = (40, 120, 220)   # sky above
    arr[h // 2 :] = (200, 160, 100)  # sand below
    out = np.asarray(_ambient(Image.fromarray(arr), (300, 800, 780, 1120)).convert("RGB"), dtype=np.int32)
    near_sky, far_sky = out[760, 540], out[20, 540]
    assert near_sky[2] > near_sky[0] + 60           # still reads as sky next to the photo
    assert far_sky.sum() < near_sky.sum()           # dims with distance
    assert out[1180, 540][0] > out[1180, 540][2]    # sand stays warm below
