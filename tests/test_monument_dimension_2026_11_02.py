"""The monument cover's dimension line marks a measurement only, never a year (operator, 2026-11-02)."""

from PIL import Image

from src.cover_design import monument_figure, render_cover
from src.still_layout import Layout

YEAR = {"eyebrow": "BURIED HISTORY", "line1": "BEHIND ONE WALL...", "line2": "A FORGOTTEN CITY",
        "tagline": "DERINKUYU, 1963"}
MEASURED = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
            "tagline": "BUILT WITHOUT RINGS", "badge": "EST. 144 FT (44 M)"}


def _layout(copy: dict) -> Layout:
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    photo = Image.new("RGBA", (1600, 1200), (120, 110, 95, 255))
    render_cover(photo.resize((1080, 1920)), copy, "monument", lay=lay, source=photo)
    return lay


def test_a_year_hero_has_no_dimension_line() -> None:
    found = monument_figure(YEAR)
    assert found and not found[1]  # a figure, and no unit: a year
    lay = _layout(YEAR)
    names = {e["name"] for e in lay.elements}
    assert "figure" in names and "dimension" not in names
    assert lay.problems() == []


def test_a_measured_hero_keeps_its_dimension_line() -> None:
    names = {e["name"] for e in _layout(MEASURED).elements}
    assert "dimension" in names
