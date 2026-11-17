"""Round-2 panel: spans between approximate dates are never more precise than the dates (2026-11-02)."""

from src import graphics


def circa(year: int) -> dict:
    return {"year": year, "approximate": True}


def test_spans_between_circa_dates_are_rounded() -> None:
    assert graphics.gap_text(circa(-700), {"year": 780}) == "ABOUT 1,500 YEARS LATER"
    assert graphics.gap_text({"year": 780}, circa(1923)) == "ABOUT 1,100 YEARS LATER"
    assert graphics.gap_text(circa(1900), {"year": 1973}) == "ABOUT 70 YEARS LATER"
    assert graphics.gap_text(circa(1900), {"year": 1903}) == "ABOUT 3 YEARS LATER"
    assert graphics.gap_text({"year": 1923}, {"year": 1963}) == "40 YEARS LATER"
    assert graphics.gap_text({"year": -700}, {"year": 780}) == "1,479 YEARS LATER"
