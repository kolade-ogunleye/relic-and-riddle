"""The 16:9 YouTube thumbnail (2026-11-01): the covers' system, a figure or a two-tone headline, at most four
words, every line at a measured contrast, YouTube's timestamp corner clear."""

from PIL import Image

from src import brand, youtube_thumbnail
from src.visual_engine import VisualEngine


def _photo(colour=(200, 170, 120)) -> Image.Image:
    return Image.new("RGB", (1600, 1200), colour)


def test_a_measured_figure_is_the_hero() -> None:
    im, plates = youtube_thumbnail.render(_photo(), {"line1": "THE 25-MILE", "line2": "SAHARA EYE",
                                                     "eyebrow": "DESERT ANOMALY", "tagline": "CARVED BY NATURE"})
    names = {n: p for n, p in plates}
    assert {"brand", "figure", "figure_unit", "title"} <= set(names)
    assert names["figure"].text == "25" and names["figure_unit"].text == "MILES" and names["title"].text == "SAHARA EYE"
    assert names["figure"].fill == brand.GOLD and names["title"].fill == brand.BONE
    assert all(p.contrast >= 4.5 for _, p in plates)
    assert im.size == (1920, 1080)


def test_without_a_figure_it_is_a_two_tone_headline_of_few_words() -> None:
    _, plates = youtube_thumbnail.render(_photo(), {"line1": "BEHIND ONE WALL... AND MORE WORDS", "line2": "A FORGOTTEN CITY"})
    texts = {n: p.text for n, p in plates}
    assert texts["title_1"] == "BEHIND ONE WALL..." and texts["title_2"] == "A FORGOTTEN CITY"
    fills = {n: p.fill for n, p in plates}
    assert fills["title_1"] == brand.BONE and fills["title_2"] == brand.GOLD


def test_text_stays_left_and_clear_of_the_timestamp_corner() -> None:
    _, plates = youtube_thumbnail.render(_photo((240, 240, 240)), {"line1": "THE 144-FOOT", "line2": "WHISPERING DOME"})
    x0, y0, _, _ = youtube_thumbnail.TIMESTAMP_ZONE
    for _, p in plates:
        assert p.box[2] <= youtube_thumbnail.TEXT_RIGHT + 4 and not (p.box[2] > x0 and p.box[3] > y0)


def test_the_documentary_thumbnail_uses_it(tmp_path) -> None:
    hero = tmp_path / "hero.jpg"
    _photo().save(hero)
    out = VisualEngine().generate_youtube_thumbnail(hero, {"title": "T", "thumbnail": {
        "line1": "280 FEET DOWN", "line2": "A HIDDEN CITY"}}, tmp_path / "yt.jpg")
    assert Image.open(out).size == (1920, 1080)
