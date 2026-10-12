from PIL import Image

from src.visual_engine import COVER_STYLES, VisualEngine

COPY = {"thumbnail": {"eyebrow": "CAPPADOCIA, TURKEY", "line1": "280 FEET DOWN", "line2": "FOUND BEHIND A WALL",
                      "tagline": "Room for up to 20,000 people"}}


def test_every_cover_style_renders_both_formats(tmp_path):
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1600, 2400), (90, 80, 70)).save(hero)
    outputs = set()
    for style in COVER_STYLES:
        c916, c11 = VisualEngine().generate_thumbnails(hero, COPY, tmp_path / style, style=style)
        assert Image.open(c916).size == (1080, 1920) and Image.open(c11).size == (1080, 1080)
        outputs.add(Image.open(c916).tobytes())
        # Text stays out of the top and bottom bands that the 1:1 grid crop removes.
        im = Image.open(c916).convert("L")
        for band in ((0, 0, 1080, 400), (0, 1520, 1080, 1920)):
            assert im.crop(band).getextrema()[1] < 200, style  # no bright text there
    assert len(outputs) == len(COVER_STYLES)  # genuinely different layouts
