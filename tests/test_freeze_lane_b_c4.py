"""Design-freeze lane B, cycle 4: Reel cover / grid tile / YouTube thumbnail invariants (panel 5 carry-in)."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand
from src.cover_design import PLATE_SQUARE, render_cover
from src.still_layout import Layout

COPY = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE DOME", "line2": "THAT WHISPERS", "tagline": "BUILT WITHOUT RINGS"}


def _ground(kind):
    """Stress grounds: bright (near-white), dark, busy (noise)."""
    if kind == "busy":
        return Image.fromarray(np.random.default_rng(5).integers(20, 235, (1000, 1500, 3), dtype=np.uint8))
    im = Image.new("RGB", (1500, 1000), {"bright": (240, 238, 232), "dark": (40, 34, 30)}[kind])
    ImageDraw.Draw(im).ellipse((500, 250, 1000, 750), fill=(120, 100, 80))
    return im


@pytest.mark.parametrize("kind", ["bright", "dark", "busy"])
@pytest.mark.parametrize("style", ["badge", "lower_third", "minimal"])
def test_framed_window_bands_end_in_the_plate_value_no_gutter(kind, style) -> None:
    """Panel 5 (CD r1, PL r1/r2): the photo bands above and below the INK plate faded to black, then stepped UP to the
    plate at y 420 and 1500: a dark gutter line, and a lid on bright heroes. Every row of the join is at least the
    plate's own value (one field), whatever the ground."""
    photo = _ground(kind)
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    im = render_cover(photo.resize((1080, 1920)).convert("RGBA"), COPY, style, lay=lay, source=photo.convert("RGBA"))
    rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
    plate = np.array(brand.INK, dtype=np.float32)
    _, top, _, foot = PLATE_SQUARE
    assert np.abs(rgb[top + 2, 4:40] - plate).max() <= 1          # the plate is the INK plate
    for y in list(range(top - 60, top)) + list(range(foot + 1, foot + 61)):
        row = rgb[y].mean(axis=0)
        assert (row >= plate - 1.5).all(), (y, row.tolist())       # never darker than the plate: no gutter
    # and the join is a ramp: no row-to-row step larger than a soft fade's
    lum = rgb.mean(axis=(1, 2))
    assert np.abs(np.diff(lum[top - 60:top + 4])).max() < 6
    assert np.abs(np.diff(lum[foot - 4:foot + 61])).max() < 6
