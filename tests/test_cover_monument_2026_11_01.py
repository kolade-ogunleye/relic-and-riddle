"""The "monument number" cover (2026-11-01): one measured figure as the hero, never repeated in the title."""

from PIL import Image, ImageDraw

from src.cover_design import render_cover
from src.still_layout import Layout

GUMBAZ = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
          "tagline": "BUILT WITHOUT RINGS", "badge": "EST. 144 FT (44 M)"}


def _draw(copy, monkeypatch):
    drawn = []
    real = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text",
                        lambda self, xy, text, *a, **k: drawn.append(str(text)) or real(self, xy, text, *a, **k))
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    photo = Image.new("RGBA", (1600, 1200), (120, 110, 95, 255))
    out = render_cover(photo.resize((1080, 1920)), copy, "monument", lay=lay, source=photo)
    return out, lay, " ".join(drawn)


def test_the_figure_is_the_hero_and_not_repeated(monkeypatch) -> None:
    out, lay, drawn = _draw(GUMBAZ, monkeypatch)
    names = {e["name"] for e in lay.elements}
    assert {"figure", "figure_unit", "title_2", "eyebrow", "tagline", "brand"} <= names
    assert "title_1" not in names                  # "THE 144-FOOT" said nothing the figure does not
    assert "144-FOOT" not in drawn and "WHISPERING DOME" in drawn and "FT" in drawn
    assert lay.meta["monument"] == {"number": "144", "unit": "FT", "from": "line1"}
    assert out.size == (1080, 1920) and lay.problems() == []


def test_the_rest_of_a_figure_line_is_kept(monkeypatch) -> None:
    _, lay, drawn = _draw({"eyebrow": "SAHARA ANOMALY", "line1": "THE 30-MILE DOME", "line2": "SEEN FROM SPACE",
                           "tagline": "MAURITANIA'S BULLSEYE"}, monkeypatch)
    # Round 4: the tagline is tracked (drawn a letter at a time) and typeset (a curly apostrophe).
    assert "DOME" in drawn and "SEEN FROM SPACE" in drawn and "MAURITANIA\u2019SBULLSEYE" in drawn.replace(" ", "")
    assert lay.meta["monument"]["unit"] == "MILES"


def test_every_text_element_clears_contrast_and_stays_in_the_square(monkeypatch) -> None:
    _, lay, _ = _draw(GUMBAZ, monkeypatch)
    for e in lay.elements:
        if e.get("contrast") is not None:
            assert e["contrast"] >= 4.5, e
        x0, y0, x1, y1 = e["box"]
        assert 60 <= x0 and x1 <= 1020 and 440 <= y0 and y1 <= 1480, e


def test_without_a_figure_it_falls_back_to_discovery(monkeypatch) -> None:
    _, lay, drawn = _draw({"eyebrow": "LOST CODE", "line1": "THE BOOK", "line2": "NOBODY CAN READ",
                           "tagline": "STILL UNSOLVED"}, monkeypatch)
    assert "monument" not in lay.meta and "NOBODY CAN READ" in drawn


def test_monument_draws_its_dimension_line_under_the_figure(monkeypatch) -> None:
    _, lay, _ = _draw(GUMBAZ, monkeypatch)
    boxes = {e["name"]: e["box"] for e in lay.elements}
    dim, fig = boxes["dimension"], boxes["figure"]
    assert dim[1] > fig[3] and dim[0] <= fig[0] and dim[2] >= boxes["figure_unit"][2]
    assert lay.problems() == []


def test_discovery_eyebrow_sits_on_a_gold_tab() -> None:
    from PIL import Image

    from src import brand
    from src.cover_design import render_cover
    from src.still_layout import Layout
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    photo = Image.new("RGBA", (1600, 1200), (120, 110, 95, 255))
    out = render_cover(photo.resize((1080, 1920)), GUMBAZ, "badge", lay=lay, source=photo).convert("RGB")
    tab = lay.meta["tabs"]["eyebrow"]
    assert out.getpixel((tab[0] + 4, tab[1] + 4)) == brand.GOLD
    eyebrow = next(e for e in lay.elements if e["name"] == "eyebrow")
    assert eyebrow["fill"] == list(brand.INK) and eyebrow["contrast"] >= 4.5
