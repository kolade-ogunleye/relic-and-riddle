"""A full-width badge photo meets the canvas above it without a hard seam or streaks (review, 2026-11-02)."""

import numpy as np
from PIL import Image, ImageDraw

from src.cover_design import render_cover
from src.still_layout import Layout

COPY = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
        "tagline": "BUILT WITHOUT RINGS"}


def _cover(photo: Image.Image) -> tuple[np.ndarray, dict]:
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    im = render_cover(photo.resize((1080, 1920)).convert("RGBA"), COPY, "badge", lay=lay,
                      source=photo.convert("RGBA"),
                      focus={"focus_box": [0.2, 0.25, 0.8, 0.8], "sha256": "x", "reviewed": "test"})
    return np.asarray(im.convert("L"), dtype=np.float32), lay.meta["composition"]


def test_no_hard_seam_where_a_bright_sky_photo_starts() -> None:
    photo = Image.new("RGB", (1600, 1000), (236, 240, 246))                 # a near-white sky (masthead needs help)
    ImageDraw.Draw(photo).rectangle((320, 250, 1280, 800), fill=(150, 140, 110))
    grey, comp = _cover(photo)
    assert comp["full_width"]
    rows = grey[300:comp["protected_box"][1], 20:150].mean(axis=1)   # beside the masthead, down to the feature
    assert np.abs(np.diff(rows)).max() < 10   # a soft ramp (~6 per row), never a hard edge (~16 per row before)


def test_no_vertical_streaks_above_a_textured_top_row() -> None:
    rng = np.random.default_rng(3)
    photo = Image.fromarray(rng.integers(40, 220, (1000, 1600, 3), dtype=np.uint8))
    grey, comp = _cover(photo)
    top = comp["image_box"][1]
    band = grey[max(0, top - 200):max(1, top - 40), 100:980]
    # Column-to-column variation (streaks) stays small compared with the photo's own texture.
    assert np.abs(np.diff(band.mean(axis=0))).mean() < 4


def test_masthead_band_eases_out_over_a_long_ramp_above_a_full_width_photo() -> None:
    # r5 review: the band eased from the masthead's foot over the 40-60 px left above the photo, a dark plateau
    # then a fast ramp: a hard-looking line across the sky. It now eases from the cap centre, with no kink.
    photo = Image.new("RGB", (1600, 1000), (236, 240, 246))
    ImageDraw.Draw(photo).rectangle((320, 250, 1280, 800), fill=(150, 140, 110))
    grey, comp = _cover(photo)
    rows = grey[480:comp["protected_box"][1], 20:150].mean(axis=1)     # the masthead's line down to the feature
    d8 = rows[8:] - rows[:-8]                        # brightening per 8 rows (single rows are 8-bit quantised)
    assert d8.max() < 26                             # was 37: a fast ramp
    assert np.abs(d8[8:] - d8[:-8]).max() < 12       # was 20: the knee where the band met the photo
    # QF-87 (row rule: a profile-grid masthead is 9 px cap height at 124 px or absent): at BRAND_PX it is 5.2 px, so a cover draws none and lay.meta['masthead'] says why: with no masthead there is no band to ease out, so no line across the sky at all (was: the band's ramp
    # spread over 60+ rows)
    assert np.abs(np.diff(rows)).max() < 1


def test_an_inset_window_keeps_a_clear_gap_under_the_masthead() -> None:
    from src.cover_design import INSET_CLEAR, _brand_foot
    # A tall feature that cannot fill the width inside its band: the inset window falls back (Richat). A textured
    # photograph, so its pixels are told from the smooth ambient field around it.
    rng = np.random.default_rng(7)
    photo = Image.fromarray(rng.integers(40, 220, (1100, 1200, 3), dtype=np.uint8))
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    im = render_cover(photo.resize((1080, 1920)).convert("RGBA"), COPY, "badge", lay=lay, source=photo.convert("RGBA"),
                      focus={"focus_box": [0.2, 0.1, 0.8, 0.9], "sha256": "x", "reviewed": "test"})
    comp = lay.meta["composition"]
    assert not comp["full_width"]
    foot = _brand_foot()
    assert comp["image_box"][1] >= foot + INSET_CLEAR
    grey = np.asarray(im.convert("L"), dtype=np.float32)
    x0, x1 = comp["image_box"][0] + 160, comp["image_box"][2] - 160

    def texture(y):
        return np.abs(np.diff(grey[y, x0:x1])).mean()
    feature = texture(comp["protected_box"][1] + 20)
    assert all(texture(y) < 0.1 * feature for y in range(foot + 4, foot + INSET_CLEAR))   # no photo in the gap
    # The window's top fades in over its room: texture rises row by row, not in one step.
    rise = [texture(y) for y in range(comp["image_box"][1], comp["protected_box"][1])]
    assert max(np.diff(rise)) < 0.25 * feature
