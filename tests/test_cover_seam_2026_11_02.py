"""A Reel cover's photograph never ends inside the canvas above its type (review 2026-11-02, lane B cycle 4, freeze
cycle 5).

The history: a full-width photograph that stopped short of the frame's top met a field made of its own edge,
extended and blurred. Round 3 (2026-11-02) removed the hard seam, lane B cycle 4 the blur-to-sharp line; panel 6 still
read the pad (PHOTO-01). Freeze cycle 5 closes the class: there is no join to soften, because the photograph fills
its window with its own pixels (cover_design._filled, src/photo_fitness.py) or the cover takes the contained window
on INK. These tests hold that on the stress grounds the join was tuned on.
"""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand
from src.cover_design import _brand, render_cover
from src.still_layout import Layout

COPY = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
        "tagline": "BUILT WITHOUT RINGS"}


def _hero(kind, size, focus_box):
    """Stress grounds: busy (noise), flat (one tone with a darker subject), bright (near-white)."""
    w, h = size
    if kind == "busy":
        return Image.fromarray(np.random.default_rng(11).integers(30, 230, (h, w, 3), dtype=np.uint8))
    tone = {"flat": (120, 110, 96), "bright": (238, 240, 244)}[kind]
    photo = Image.new("RGB", size, tone)
    fb = focus_box
    ImageDraw.Draw(photo).rectangle((int(fb[0] * w), int(fb[1] * h), int(fb[2] * w), int(fb[3] * h)), fill=(70, 62, 50))
    return photo


@pytest.mark.parametrize("kind", ["busy", "flat", "bright"])
@pytest.mark.parametrize("style,size,focus_box", [
    ("badge", (1600, 1000), (0.2, 0.3, 0.8, 0.8)),          # a wide photograph, room above the feature
    ("lower_third", (1200, 1100), (0.2, 0.3, 0.8, 0.9)),    # a tall feature (the old inset window)
    ("badge", (1200, 1100), (0.2, 0.3, 0.8, 0.9)),
    ("badge", (1100, 2000), (0.2, 0.3, 0.8, 0.6)),          # a tall photograph
])
def test_a_photograph_fills_its_window_or_stands_whole_on_ink(kind, style, size, focus_box) -> None:
    photo = _hero(kind, size, focus_box)
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    im = render_cover(photo.resize((1080, 1920)).convert("RGBA"), COPY, style, lay=lay, source=photo.convert("RGBA"),
                      focus={"focus_box": list(focus_box), "sha256": "x", "reviewed": "test"})
    comp = lay.meta["composition"]
    a = np.asarray(im.convert("RGB"), np.int16)
    ink = np.array(brand.INK, np.int16)
    if comp["mode"] in ("full_bleed", "photo_over_ink"):
        x0, y0, x1, y1 = comp["image_box"]
        assert (x0, y0, x1) == (0, 0, 1080) and comp["source_coverage"] == 1.0   # from the top edge, full width
        assert comp["upscale"] <= 2.0
        px0, py0, px1, py1 = comp["protected_box"]
        assert px0 >= 0 and px1 <= 1080 and py0 >= 0 and py1 <= y1                # the reviewed feature whole
        if kind == "busy":   # the top rows are the photograph's own texture, not a smooth field
            assert np.abs(np.diff(a[4:120, 40:1040, 0], axis=1)).mean() > 20
        if comp["mode"] == "photo_over_ink":
            assert np.abs(a[1900:, :] - ink).max() <= 2                           # INK under it, to the foot
    else:
        assert comp["mode"] == "contain" and lay.meta["photo_fit"]["fit"] is False
        x0, y0, x1, y1 = comp["image_box"]
        assert np.abs(a[:y0 - 2] - ink).max() <= 2 and np.abs(a[1900:] - ink).max() <= 2   # a clean INK field
    assert lay.problems() == []


def test_no_cover_draws_a_masthead_band() -> None:
    # Operator ruling 2 (2026-11-16): no masthead on a Reel cover, so no band is cut or eased for one.
    probe = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    _brand(None, probe, grid_delivery=True)
    assert probe.meta["masthead"]["shown"] is False
