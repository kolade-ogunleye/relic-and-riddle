"""A full-width badge photo meets the canvas above it without a hard seam or streaks (review, 2026-11-02)."""

import numpy as np
import pytest
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


def _inset(focus_box=(0.2, 0.1, 0.8, 0.9)):
    # A tall feature that cannot fill the width inside its band: the inset window falls back (Richat). A textured
    # photograph, so its pixels are told from the smooth ambient field around it.
    rng = np.random.default_rng(7)
    photo = Image.fromarray(rng.integers(40, 220, (1100, 1200, 3), dtype=np.uint8))
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    im = render_cover(photo.resize((1080, 1920)).convert("RGBA"), COPY, "badge", lay=lay, source=photo.convert("RGBA"),
                      focus={"focus_box": list(focus_box), "sha256": "x", "reviewed": "test"})
    return np.asarray(im.convert("L"), dtype=np.float32), lay.meta["composition"]


def test_an_inset_window_cuts_no_band_for_a_masthead_the_cover_does_not_draw() -> None:
    # Freeze lane B c4 (panel 5, Richat Reel cover: a blur-to-sharp seam at y ~540): the window's top was cut
    # INSET_CLEAR px under the masthead's foot, leaving 26 px to fade before the feature. Operator ruling 2 draws no
    # masthead on a Reel cover, so the photograph keeps its context above the feature and fades over all of it.
    from src.cover_design import INSET_FEATHER_MAX, _cover_masthead_drawn
    assert not _cover_masthead_drawn()
    grey, comp = _inset()
    assert not comp["full_width"]
    room = comp["protected_box"][1] - comp["image_box"][1]
    assert abs(comp["edge_fade_top"] - min(INSET_FEATHER_MAX, room - 2)) <= 1 and comp["edge_fade_top"] > 1
    x0, x1 = comp["image_box"][0] + 160, comp["image_box"][2] - 160

    def texture(y):
        return np.abs(np.diff(grey[y, x0:x1])).mean()
    feature = texture(comp["protected_box"][1] + 20)
    # The window's top fades in over its room: texture rises row by row, never in one step.
    rise = np.convolve([texture(y) for y in range(max(0, comp["image_box"][1] - 20), comp["protected_box"][1])],
                       np.ones(8) / 8, mode="valid")
    # per 8 rows, no steeper than a smoothstep over the recorded fade (+ noise); the 24 px join was ~0.5
    assert (rise[8:] - rise[:-8]).max() < (1.5 * 8 / comp["edge_fade_top"] + 0.08) * feature


def test_an_inset_window_keeps_its_clear_gap_while_a_cover_draws_a_masthead(monkeypatch) -> None:
    # The r5 rule (2026-11-02: RELIC & RIDDLE sat on the photo's edge) stands whenever a cover draws the masthead.
    from src import cover_design
    from src.cover_design import INSET_CLEAR, _brand_foot
    monkeypatch.setattr(cover_design, "_cover_masthead_drawn", lambda: True)
    grey, comp = _inset()
    foot = _brand_foot()
    assert comp["image_box"][1] >= foot + INSET_CLEAR
    x0, x1 = comp["image_box"][0] + 160, comp["image_box"][2] - 160

    def texture(y):
        return np.abs(np.diff(grey[y, x0:x1])).mean()
    feature = texture(comp["protected_box"][1] + 20)
    assert all(texture(y) < 0.1 * feature for y in range(foot + 4, foot + INSET_CLEAR))   # no photo in the gap


def _hero(kind, size, focus_box):
    """Stress grounds for the join: busy (noise), flat (one tone with a darker subject), bright (near-white)."""
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
    ("badge", (1600, 1000), (0.2, 0.3, 0.8, 0.8)),          # full width, room above the feature
    ("lower_third", (1200, 1100), (0.2, 0.3, 0.8, 0.9)),    # the inset window (Richat's board case)
    ("badge", (1200, 1100), (0.2, 0.3, 0.8, 0.9)),          # a tall feature in the badge: the inset window
])
def test_no_hard_join_where_any_photograph_starts(kind, style, size, focus_box) -> None:
    """Lane B c4, the class: wherever a reviewed photograph's top ends inside the canvas, on any ground, the join to
    the field above it is gradual in tone and texture (no row steps by a large share of the photo's own texture),
    the fade spans its room (edge_fade_top), and no colour band is held up the canvas as a streak."""
    from src.cover_design import INSET_FEATHER_MAX
    photo = _hero(kind, size, focus_box)
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    im = render_cover(photo.resize((1080, 1920)).convert("RGBA"), COPY, style, lay=lay, source=photo.convert("RGBA"),
                      focus={"focus_box": list(focus_box), "sha256": "x", "reviewed": "test"})
    comp = lay.meta["composition"]
    top, ftop = comp["image_box"][1], comp["protected_box"][1]
    assert top > 0, "the case must end the photograph inside the canvas"
    assert comp["edge_fade_top"] >= min(INSET_FEATHER_MAX, ftop - top - 2) - 1   # all its room (px rounding)
    grey = np.asarray(im.convert("L"), dtype=np.float32)
    x0, x1 = max(comp["image_box"][0], 0) + 40, min(comp["image_box"][2], 1080) - 40
    band = grey[max(0, top - 60):ftop - 4, x0:x1]   # the field above, the join, the context down to the feature
    ref = max(float(np.abs(np.diff(grey[ftop + 10, x0:x1])).mean()), 1.0)   # the photograph's own texture

    def per8(v):   # change over 8 rows (single rows are noise on a textured ground)
        v = np.convolve(v, np.ones(8) / 8, mode="valid")
        return v[8:] - v[:-8]
    assert np.abs(per8(band.mean(axis=1))).max() < 12                          # tone: a ramp, never a step
    # texture: no blur-to-sharp line (a 120 px join rises ~0.11 of the photo's texture per 8 rows; the 13c31d27 joins
    # rose 0.20 (64 px, full width) to 0.43 (26 px, the inset window under the old masthead band))
    assert per8(np.abs(np.diff(band, axis=1)).mean(axis=1)).max() < 0.15 * ref + 0.5
    above = grey[max(0, top - 200):max(1, top - 20), 100:980]
    assert np.abs(np.diff(above.mean(axis=0))).mean() < 1.5   # no held colour band (streak) above
