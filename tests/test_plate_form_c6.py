"""Design freeze cycle 6 (PLATEFORM design stream): the candidate ground forms are a switch only.

The shipped design is "base" unless RR_PLATE_FORM names a candidate; under "base" no call site takes the candidate
path (multi_format.scrim lays its own band), and every candidate lays its ground from the type's boxes alone.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src import multi_format, plate_form


def _bright(w=1080, h=1920, v=235):
    return Image.new("RGB", (w, h), (v, v, v - 10))


BOXES = [((137, 1200, 820, 1300), (243, 237, 226)), ((137, 1320, 640, 1420), (242, 182, 48))]


def test_the_default_form_is_base_and_draws_nothing(monkeypatch):
    monkeypatch.delenv(plate_form.ENV, raising=False)
    assert plate_form.active() == "base"
    lyr, rec = plate_form.layer(_bright(), BOXES)
    assert np.asarray(lyr.getchannel("A")).max() == 0 and rec["form"] == "base"


def test_an_unknown_form_is_refused(monkeypatch):
    monkeypatch.setenv(plate_form.ENV, "slab")
    with pytest.raises(ValueError):
        plate_form.active()


def test_scrim_under_base_is_the_shipped_band(monkeypatch):
    """Under base, scrim() with the type's boxes is the integration-5 band (full width), never a candidate form."""
    monkeypatch.delenv(plate_form.ENV, raising=False)
    im = _bright()
    a = np.asarray(multi_format.scrim(im, 1150, 1920, plain=[b for b, _f in BOXES]).convert("L"), np.float32)
    assert a[1600, 1070] < 200          # the band runs to the frame's right edge
    monkeypatch.setenv(plate_form.ENV, "block")
    b = np.asarray(multi_format.scrim(im, 1150, 1920, plain=[b for b, _f in BOXES]).convert("L"), np.float32)
    assert b[1600, 1070] > 220          # the text-fitted block ends at the text's measure


@pytest.mark.parametrize("form", ["hug", "block", "field", "grade", "split"])
def test_each_form_reaches_the_target_under_the_type_on_a_bright_ground(form):
    im = _bright()
    out, rec = plate_form.lay(im, BOXES, form)
    a = np.asarray(out, np.float32)
    for (x0, y0, x1, y1), fill in BOXES:
        g = plate_form.rel_lum(a[y0:y1, x0:x1].reshape(-1, 3))
        ratio = plate_form.ratio(float(plate_form.rel_lum(np.asarray(fill, np.float32))), float(np.percentile(g, 95)))
        assert ratio >= (4.5 if form in ("field", "grade") else 7.0), (form, ratio, rec)


@pytest.mark.parametrize("form", ["hug", "block", "field", "grade"])
def test_the_text_tied_forms_leave_the_frame_edge_and_the_far_photograph_untouched(form):
    im = _bright()
    out, _ = plate_form.lay(im, BOXES, form)
    a = np.asarray(out.convert("L"), np.float32)
    b = np.asarray(im.convert("L"), np.float32)
    assert abs(a[1300, 1075] - b[1300, 1075]) < 2      # no full-width band
    assert abs(a[300, 540] - b[300, 540]) < 2          # the photograph far from the type is itself
