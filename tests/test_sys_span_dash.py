"""Freeze SYS batch 1 (the lander's item 6): a range's en dash keeps at least tok("figure.dash_gap_em") of
ink gap each side of the figures it joins, in every bundled face and on every draw path (Pillow's layout through
FeaturedFont, font_engine.draw_run, glyph_masks), and its measure agrees with what is drawn. Measured, in Anton
U+2013 is its hyphen and sat 0.04-0.06 em from the figures. Invariants over every digit pair; no pinned px."""
from __future__ import annotations

import itertools

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand_system, font_engine, typography

FACES = sorted(typography.FONT_FILES)
PAIRS = ["".join(p) for p in itertools.product("0123456789", repeat=2)]
DASH = "–"


def _gaps(arr: np.ndarray, dash: tuple[int, int, int, int]) -> tuple[int, int]:
    """Ink gap (px) each side of the dash whose ink box is `dash` (x0, y0, x1, y1), read in the dash's own rows."""
    x0, y0, x1, y1 = dash
    a = arr >= 128
    band = a[y0:y1 + 1].any(0)
    left = np.where(band[:x0])[0]
    right = np.where(band[x1 + 1:])[0]
    return x0 - 1 - left.max(), right.min()


def _dash_box(f, a: str, origin: float) -> tuple[int, int, int, int]:
    """The dash's ink box as the run is shaped: its glyph drawn alone at its shaped position."""
    run = font_engine.shape(a + DASH + "0", f)
    g = run.glyphs[1]
    im = Image.new("L", (int(run.advance) + 4 * f.size, 3 * f.size), 0)
    ImageDraw.Draw(im).text((origin + g.x, 2 * f.size), DASH, font=f, fill=255, anchor="ls")
    ink = np.array(im) >= 128
    cols, rows = np.where(ink.any(0))[0], np.where(ink.any(1))[0]
    return int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())


@pytest.mark.parametrize("size", [120, 300])
@pytest.mark.parametrize("family", FACES)
def test_every_span_dash_keeps_the_token_gap_on_every_draw_path(family, size):
    f = typography.get_font(family, size)
    token = brand_system.tok("figure.dash_gap_em") * font_engine.raster_px(f)
    origin = f.size
    for a, b in PAIRS:
        t = a + DASH + b
        dash = _dash_box(f, a, origin)
        w = int(f.getlength(t)) + 4 * f.size
        pillow = Image.new("L", (w, 3 * f.size), 0)
        ImageDraw.Draw(pillow).text((origin, 2 * f.size), t, font=f, fill=255, anchor="ls")
        run = Image.new("L", (w, 3 * f.size), 0)
        font_engine.draw_run(ImageDraw.Draw(run), (origin, 2 * f.size), t, f, 255, anchor="ls")
        assert np.array_equal(np.array(pillow), np.array(run)), t          # one drawing, either path
        assert min(_gaps(np.array(pillow), dash)) >= token - 1, (t, _gaps(np.array(pillow), dash), token)
        # what is measured is what is drawn
        assert f.getlength(t) == pytest.approx(typography.tracked_width(t, f), abs=0.01)
        ink = np.where((np.array(pillow) >= 128).any(0))[0]
        assert ink.max() + 1 <= origin + f.getlength(t) + f.size * 0.2


@pytest.mark.parametrize("family", FACES)
def test_the_dash_spacing_is_one_per_face_and_both_sides(family):
    f = typography.get_font(family, 200)
    extra = {font_engine.span_extra_px(f, a + DASH + b) for a, b in PAIRS}
    assert len(extra) == 1
    run = font_engine.shape("1" + DASH + "2", f)
    plain = font_engine._shape(str(f.path), font_engine.raster_px(f), getattr(f, "_rr_variations", ()),
                               "1" + DASH + "2", 0.0, font_engine.resolve_features("1" + DASH + "2", None))
    (e,) = extra
    assert run.glyphs[1].x - plain.glyphs[1].x == pytest.approx(e)                  # before the dash
    assert run.glyphs[2].x - plain.glyphs[2].x == pytest.approx(2 * e)              # and after it
    assert run.advance - plain.advance == pytest.approx(2 * e)


@pytest.mark.parametrize("text", ["94-104", "NO SPAN – HERE", "AD 1200", "THE 1,200-TON", "–94", "94–"])
def test_text_without_a_figure_span_is_unchanged(text):
    for family in FACES:
        f = typography.get_font(family, 160)
        assert font_engine.span_extra_px(f, text) == 0.0
        assert f.getlength(text) == pytest.approx(font_engine.shape(text, f).advance, abs=0.01)


def test_libass_measures_keep_libass_spacing():
    """libass draws the captions without the span spacing, so its measure (shape_in_face) has none either."""
    f = typography.get_font(FACES[0], 160)
    t = "1" + DASH + "2"
    assert font_engine.shape_in_face(t, f).advance <= font_engine.shape(t, f).advance
