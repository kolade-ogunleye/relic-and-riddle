"""Freeze lane E, cycle 3 (loop bridge, 16:9 end screen; panel 4 MO-E1, MO-E2, PL-MG-04, MO-T1).

- The loop bridge draws no dim under FOLLOW or with no element (FOLLOW sits on its own held plate): no dip to dark
  before the crossfade. Under the share line (line=True) the dim stays, coming in from rest (ease_io).
- The 16:9 end screen's field (dim and softening) starts from rest: no full-speed first step.
- The 16:9 column scrim is monotone, full over the text column and eases out to LANDSCAPE_RIGHT_DIM.
- The seam gate's measure is recorded with its value (LOOP_SEAM_MEASURE) and is the one seam_problems reads."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import end_card
from src.brand_system import tok

SHARE = "Send this to a space enthusiast you know."


def _bright(size=(1080, 1920), level=225):
    im = Image.new("RGB", size, (level, level - 6, level - 18))
    d = ImageDraw.Draw(im)
    for x in range(0, size[0], 120):   # a little texture so fit_dim measures a real ground
        d.line([(x, 0), (x + 200, size[1])], fill=(level - 40, level - 46, level - 58), width=3)
    return im


def _luma(im, rows=slice(None), cols=slice(None)):
    return float(np.asarray(im.convert("L"), np.float32)[rows, cols].mean())


def _bridge(**kw):
    stats = {}
    last, first = _bright(), _bright(level=200)
    frames = list(end_card.loop_bridge(last, first, SHARE, stats, **kw))
    return frames, stats, first


@pytest.mark.parametrize("kw", [{"line": False, "follow": True}, {"line": False, "follow": False}],
                         ids=["follow", "visual"])
def test_a_bridge_without_the_share_line_never_dips_before_the_crossfade(kw):
    frames, stats, _ = _bridge(**kw)
    assert stats["loop_dim"] == 0.0
    n_cf = round(stats["crossfade"] * end_card.FPS)
    i_out = round(end_card.LOOP_SECONDS * end_card.FPS) - n_cf
    below = slice(1300, None)   # the ground away from the FOLLOW (LOOP_CENTRE)
    ref = _luma(frames[0], below)
    for f in frames[:i_out]:
        assert abs(_luma(f, below) - ref) < 1.0, "the ground darkens before the crossfade"


def test_under_the_share_line_the_dim_comes_in_from_rest():
    frames, stats, _ = _bridge(line=True, follow=False)
    assert stats["loop_dim"] == end_card.LOOP_DIM
    lum = [_luma(f, slice(1300, None)) for f in frames]
    steps = [a - b for a, b in zip(lum, lum[1:])]
    n_in = round(end_card.LOOP_TIMES["in"] * end_card.FPS)
    darkening = steps[:n_in]
    assert max(darkening) > 0
    assert darkening[0] < 0.5 * max(darkening)   # eased from rest, not its largest step first


def test_the_follow_plate_is_planned_on_the_undimmed_grounds():
    frames, stats, _ = _bridge(line=False, follow=True)
    if stats["follow"]:
        assert stats["contrast"] >= end_card.LOOP_FOLLOW_MIN
    else:   # skipped: the record says why
        assert stats["follow_skipped"]


def test_the_seam_gate_records_and_reads_its_own_measure():
    frames, stats, first = _bridge(line=False, follow=True)
    assert stats["mad_measure"] == end_card.LOOP_SEAM_MEASURE
    assert f"{end_card.WIDTH}x{end_card.HEIGHT}" in end_card.LOOP_SEAM_MEASURE and "pre_encode" in end_card.LOOP_SEAM_MEASURE
    own = float(np.abs(np.asarray(frames[-1], np.float32) - np.asarray(first.convert("RGB"), np.float32)).mean())
    assert stats["mad_last"] == pytest.approx(own, abs=0.01)
    assert all("from frame 0" not in p for p in end_card.seam_problems({**stats, "mad_last": end_card.LOOP_MAX_MAD - 0.01}))
    assert any("from frame 0" in p for p in end_card.seam_problems({**stats, "mad_last": end_card.LOOP_MAX_MAD}))


def test_the_end_screen_field_starts_from_rest(monkeypatch):
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 2.0)
    last = _bright((1920, 1080))
    from itertools import islice
    n = round(max(end_card.DIM_IN, end_card.SOFTEN_IN) * end_card.FPS) + 2
    frames = list(islice(end_card.frames(last, SHARE, {}), n))
    # the ground under no type: the lower left, below the text column and clear of the slots and the timestamp
    rows, cols = slice(800, 1080), slice(500, 1000)
    lum = [_luma(f, rows, cols) for f in frames]
    steps = [a - b for a, b in zip(lum, lum[1:])]
    assert max(steps) > 1.0
    assert steps[0] < 0.5 * max(steps)          # from rest: not the largest step first (ease_out's)
    assert lum[-1] < lum[0]                      # and it dims


def test_the_end_screen_scrim_is_monotone_and_full_over_the_text_column():
    s = end_card._scrim(end_card.LANDSCAPE_WIDTH)
    x0, x1 = end_card.LANDSCAPE_SCRIM_X
    assert np.all(np.diff(s) <= 1e-7)                                   # never lifts back
    assert np.allclose(s[:x0 + 1], 1.0)                                 # the text column keeps its full dim
    assert np.allclose(s[x1:], end_card.LANDSCAPE_RIGHT_DIM, atol=1e-6)
    # the ramp is a field: its steepest column step on the worst ground stays inside the gate
    ink_l = float(np.dot(end_card.brand.INK, (0.299, 0.587, 0.114)))
    assert np.abs(np.diff(s)).max() * end_card.DIM_MAX * (255 - ink_l) <= tok("scrim.slope_max")
