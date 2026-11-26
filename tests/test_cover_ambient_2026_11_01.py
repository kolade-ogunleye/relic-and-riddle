"""The Reel cover has no ambient field (2026-11-01, retired in freeze cycle 5).

2026-11-01: the margin outside an immersive cover's photograph repeated each edge column, so a textured edge
(Richat's cloud line) became hard vertical streaks; the fix made it a blurred, dimmed field (_ambient). Panel 6 read
that field as what it was, a pad (PHOTO-01). There is no margin now: a photograph fills its window with its own
pixels or stands whole on INK (cover_design._filled, src/photo_fitness.py). These tests hold the retirement on the
source the streaks were measured on.
"""

import numpy as np
from PIL import Image

from src import brand, cover_design
from src.still_layout import Layout

COPY = {"eyebrow": "DESERT ANOMALY", "line1": "SAHARA", "line2": "EYE", "tagline": "CARVED BY NATURE"}


def _banded_edge_source() -> Image.Image:
    """A photo whose top edge is 60 px colour bands: the scale at which the old clamp streaked."""
    rng = np.random.default_rng(3)
    arr = rng.integers(90, 150, (600, 900, 3), dtype=np.uint8)
    arr[:40] = np.repeat(rng.integers(0, 256, (1, 15, 3), dtype=np.uint8), 60, axis=1)
    return Image.fromarray(arr)


def test_no_margin_of_extended_edge_columns_above_a_reviewed_photograph() -> None:
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    src = _banded_edge_source()
    focus = {"focus_box": [0.3, 0.3, 0.7, 0.7], "sha256": "t", "reviewed": "t"}
    im = cover_design.render_cover(Image.new("RGBA", (1080, 1920)), COPY, "badge", lay=lay,
                                   source=src.convert("RGBA"), focus=focus)
    comp = lay.meta["composition"]
    a = np.asarray(im.convert("RGB"), np.int16)
    ink = np.array(brand.INK, np.int16)
    if comp["mode"] == "contain":
        assert np.abs(a[:comp["image_box"][1] - 2] - ink).max() <= 2      # INK above the window, nothing extended
    else:
        assert comp["image_box"][1] == 0 and comp["source_coverage"] == 1.0   # the photograph starts at the top edge
        rows = a[60:260, 40:1040].astype(np.float32).mean(axis=2)
        assert np.abs(np.diff(rows, axis=0)).mean() > 4                    # its own texture down the rows: no
        # column held up the canvas (the clamp repeated one row: zero change from row to row)


def test_the_ambient_field_and_the_feathered_inset_are_gone() -> None:
    for name in ("_ambient", "_immersive", "_edge_fade", "INSET_FEATHER_MAX", "FULL_WIDTH_BLEND"):
        assert not hasattr(cover_design, name), name
