"""Design freeze, SYS batch 2: SYS-A-1 (one metre precision per drawing column) and SYS-C-1 (the board's caption line
record reads the composer's break predicate). Invariants, synthetic inputs only."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src import figures

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("feet", [[15, 35, 140, 280], [100, 200], [3, 33, 333, 3333], [7], [1, 2, 4, 1000]])
def test_a_converted_column_has_one_precision_and_every_member_within_tolerance(feet):
    col = figures.converted_numbers(feet, "FT", "M")
    decimals = {len(t.split(".")[1]) if "." in t else 0 for t in col}
    assert len(decimals) == 1
    for text, ft in zip(col, feet):
        assert figures.within_tol(float(text.replace(",", "")), "M", ft, "FT")
    # the least such count: one fewer decimal fails some member
    d = decimals.pop()
    if d:
        trues = [ft * 0.3048 for ft in feet]
        assert not all(figures.within_tol(float(f"{t:.{d - 1}f}"), "M", ft, "FT") for t, ft in zip(trues, feet))


def test_a_one_member_column_is_the_single_conversion():
    for ft in (15, 35, 140, 280, 1073):
        assert figures.converted_numbers([ft], "FT", "M") == [figures.converted_number(ft, "FT", "M")]


def test_the_cross_section_reads_the_set_conversion():
    import inspect

    from src import graphics
    src = inspect.getsource(graphics._render_cross_section)
    assert "converted_numbers(" in src and "converted_number(lv" not in src


def _board_evidence():
    path = ROOT / "docs/reviews/freeze-system-2026-11-15/board_evidence.py"
    spec = importlib.util.spec_from_file_location("board_evidence_sys2", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_board_line_record_reads_the_composers_break_predicate():
    import inspect
    be = _board_evidence()
    src = inspect.getsource(be.caption_rates)
    assert "_line_break_defect(" in src and "composer_line_defect" in src
    from src import video_composer as vc
    # the hedge break the composer accepts is no line defect in the board's record either
    shown = ["MORE", "THAN", "830", "SQUARE", "MILES"]
    assert vc._line_break_defect(shown, 2) is False
    assert vc._line_break_defect(["NEAR", "THE", "PODKAMENNAYA"], 1) is True
