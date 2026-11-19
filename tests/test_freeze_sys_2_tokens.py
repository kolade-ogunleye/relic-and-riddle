"""Design freeze, SYS batch 2: the YouTube lockup's tokens (SYS-B-2 masthead.hook_ratio_max, SYS-B-3
lockup.line_gap). Invariants: each token is the value the surface reads now, and a configured value moves it."""
from __future__ import annotations

import pytest

from src import brand, brand_system
from src import youtube_thumbnail as yt


def test_the_mark_ratio_is_the_runtime_token_and_moves_the_room():
    assert yt.MARK_RATIO_MAX == brand_system.tok("masthead.hook_ratio_max")
    lo, hi = brand_system.TOKEN_RANGES["masthead.hook_ratio_max"]
    assert lo <= yt.MARK_RATIO_MAX <= hi
    with brand.use(brand.DEFAULT_BRAND.derive("ratio probe", system={"masthead.hook_ratio_max": 0.62}), validate=False):
        assert yt.MARK_RATIO_MAX == 0.62
    assert "MARK_RATIO_MAX" not in vars(yt)                        # never a module copy (read when drawn)


def test_the_lockup_line_gap_is_the_runtime_token_and_its_pitch():
    assert yt.TITLE_LINE_GAP == brand_system.tok("lockup.line_gap")
    assert yt.line_pitch() == pytest.approx(1 + yt.TITLE_LINE_GAP)
    assert all(int(ch * yt.line_pitch()) == int(ch * 1.18) for ch in range(1, 2000))   # today's pitch, no pixel change
    with brand.use(brand.DEFAULT_BRAND.derive("gap probe", system={"lockup.line_gap": 0.25}), validate=False):
        assert yt.line_pitch() == pytest.approx(1.25)
    assert "TITLE_LINE_GAP" not in vars(yt)


def test_no_lockup_pitch_literal_is_left_in_the_youtube_renderer():
    import inspect
    src = inspect.getsource(yt)
    assert "* 1.18)" not in src
