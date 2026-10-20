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


def test_title_moves_away_from_busy_detail():
    import numpy as np
    from PIL import Image
    from src.visual_engine import calm_center_y
    rng = np.random.default_rng(1)
    busy = (rng.random((960, 1080)) * 255).astype("uint8")
    flat = np.full((960, 1080), 128, dtype="uint8")
    top_busy = Image.fromarray(np.vstack([busy, flat])).convert("RGB")
    bottom_busy = Image.fromarray(np.vstack([flat, busy])).convert("RGB")
    assert calm_center_y(top_busy) == 1160
    assert calm_center_y(bottom_busy) == 760


def test_story_kicker_alignment(tmp_path):
    from src.multi_format import MultiFormatGenerator
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1080, 1920), (50, 50, 50)).save(hero)
    payload = {
        "title": "Ancient Engineering Marvel",
        "narration_script": "Carvers started at the top and sculpted 200,000 tons of rock downward.",
        "first_comment": "How do you think this was engineered?",
        "hashtags": ["#Archaeology", "#AncientHistory"],
        "story_slides": [
            {"kicker": "ANCIENT MARVELS", "line1": "NOT BUILT FROM", "line2": "THE GROUND UP", "body": "Carvers started at the top."}
        ],
        "poll": {"options": ["Hand Tools", "Lost Technology"]}
    }
    gen = MultiFormatGenerator()
    slides = gen.generate_story_sequence(hero, payload, tmp_path)
    assert len(slides) == 1
    assert Image.open(slides[0]).size == (1080, 1920)

    # Verify multi-platform companion_posting.json
    import json
    comp_file = tmp_path / "companion_posting.json"
    assert comp_file.exists()
    comp_data = json.loads(comp_file.read_text(encoding="utf-8"))
    assert "platforms" in comp_data
    assert "instagram_reels" in comp_data["platforms"]
    assert "facebook_reels" in comp_data["platforms"]
    assert "youtube_shorts" in comp_data["platforms"]
    assert comp_data["platforms"]["youtube_shorts"]["title"] == "Ancient Engineering Marvel"
    assert comp_data["platforms"]["instagram_reels"]["hashtags"] == ["#Archaeology", "#AncientHistory"]


def test_3_variant_thumbnail_suite_generation(tmp_path):
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1080, 1920), (70, 60, 50)).save(hero)
    payload = {
        "title": "The Antikythera Mechanism",
        "thumbnail": {
            "eyebrow": "ANCIENT MARVEL",
            "line1": "2,000-YEAR-OLD",
            "line2": "ANALOG COMPUTER",
            "tagline": "BRONZE GEAR REVELATION",
        },
        "stat_cards": [
            {"value": "35 GEARS", "label": "Mechanism Complexity"}
        ]
    }
    engine = VisualEngine()
    cover_916, grid_1x1 = engine.generate_thumbnails(hero, payload, tmp_path)
    assert cover_916.exists() and Image.open(cover_916).size == (1080, 1920)
    assert grid_1x1.exists() and Image.open(grid_1x1).size == (1080, 1080)

    var_a = tmp_path / "thumbnail_variant_a_blueprint.jpg"
    var_b = tmp_path / "thumbnail_variant_b_intrigue.jpg"
    var_c = tmp_path / "thumbnail_variant_c_minimal.jpg"
    assert var_a.exists() and Image.open(var_a).size == (1080, 1920)
    assert var_b.exists() and Image.open(var_b).size == (1080, 1920)
    assert var_c.exists() and Image.open(var_c).size == (1080, 1920)



def test_ensure_legible_reaches_wcag_contrast_on_bright_photos_and_leaves_dark_ones():
    import numpy as np
    from src.multi_format import _rel_luminance, ensure_legible

    def ratio(text, px):
        return (_rel_luminance(text) + 0.05) / (_rel_luminance(px) + 0.05)

    for text in ((235, 235, 235), (212, 175, 55)):
        for shade in (250, 180, 120):
            out = ensure_legible(Image.new("RGB", (500, 400), (shade,) * 3), (50, 50, 350, 250), text_rgb=text, feather=30)
            a = np.asarray(out)
            assert ratio(text, a[150, 200]) >= 4.5 and ratio(text, a[52, 52]) >= 4.5  # centre and edge of the box
            assert tuple(a[-1, -1]) == (shade,) * 3                                    # far outside is untouched
    dark = Image.new("RGB", (400, 300), (30, 30, 30))
    assert np.array_equal(np.asarray(ensure_legible(dark, (50, 50, 350, 250))), np.asarray(dark))


def test_split_balanced_keeps_words_whole():
    from src.multi_format import split_balanced
    assert split_balanced("WHICH TOOK MORE WORK AT KAILASA?") == ("WHICH TOOK MORE", "WORK AT KAILASA?")
    assert split_balanced("WHY?") == ("WHY?", "")
