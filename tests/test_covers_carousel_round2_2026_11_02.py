"""Panel round 2 (2026-11-02): Reel covers, the grid's rhythm, the carousel's fixed baselines and diagram slide,
the Facebook gallery, the Story frames and the YouTube thumbnail."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import brand, cover_selector, multi_format, still_layout, youtube_thumbnail
from src.cover_design import (
    EYEBROW_MIN,
    MINIMAL_GAPS,
    TAGLINE_MIN,
    display,
    redundant_tagline,
    render_cover,
)
from src.multi_format import (
    MultiFormatGenerator,
    facebook_pick,
    story_display,
    story_figure,
)

GUMBAZ = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
          "tagline": "BUILT WITHOUT RINGS"}
DERINKUYU = {"eyebrow": "BURIED HISTORY", "line1": "BEHIND ONE WALL...", "line2": "A FORGOTTEN CITY",
             "tagline": "DERINKUYU, 1963"}
NO_FIGURE = {"eyebrow": "LOST CODE", "line1": "THE BOOK", "line2": "NOBODY CAN READ", "tagline": "STILL UNSOLVED"}


def _source(colour=(150, 130, 100)) -> Image.Image:
    import numpy as np
    rng = np.random.default_rng(3)
    base = np.clip(np.array(colour, dtype=np.float32) + rng.normal(0, 30, (1200, 1600, 3)), 0, 255)
    return Image.fromarray(base.astype("uint8")).convert("RGBA")


def _cover(copy, style, tight=False):
    lay = still_layout.Layout("cover", (1080, 1920), (0, 420, 1080, 1500))
    src = _source()
    focus = {"focus_box": [0.3, 0.2, 0.7, 0.8], "sha256": "x", "reviewed": "test"} if style in ("badge", "monument") else None
    im = render_cover(src.resize((1080, 1920)), copy, style, lay=lay, source=src, focus=focus, tight=tight)
    return im, lay


def _el(lay, name):
    return next(e for e in lay.elements if e["name"] == name)


# ---------------------------------------------------------------- covers

@pytest.mark.parametrize("style", ["badge", "monument", "minimal", "depth_sandwich"])
@pytest.mark.parametrize("tight", [False, True])
def test_every_style_sets_its_eyebrow_in_tracked_montserrat_800_at_36_px_or_more(style, tight) -> None:
    _, lay = _cover(GUMBAZ, style, tight)
    eyebrow = _el(lay, "eyebrow")
    assert eyebrow["font"] == "Montserrat-Variable.ttf" and eyebrow["px"] >= EYEBROW_MIN
    plate = lay.meta["plates"]["eyebrow"][0]
    assert plate.track >= 3
    tagline = _el(lay, "tagline")
    assert tagline["px"] >= TAGLINE_MIN
    if style == "depth_sandwich":   # on the paper card: ink, never the mono data face
        assert eyebrow["fill"] == list(brand.INK)
    elif style != "badge":           # badge: ink capitals on the gold tab (existing test)
        assert eyebrow["fill"] == list(brand.GOLD) and tagline["fill"] == list(brand.STONE_LT)
    assert not [p for p in lay.problems() if "overlap" in p or "cut_off" in p]
    for e in lay.elements:   # every line in the profile grid's centre square
        if e["name"] not in ("brand_mark", "dimension", "rule"):
            assert 420 <= e["box"][1] and e["box"][3] <= 1500, e


@pytest.mark.parametrize("style,gold", [("badge", True), ("minimal", True), ("depth_sandwich", False)])
def test_the_second_title_line_is_gold_where_the_two_tone_applies(style, gold) -> None:
    _, lay = _cover(NO_FIGURE, style)
    want = brand.GOLD if gold else brand.INK
    assert _el(lay, "title_2")["fill"] == list(want)


def test_monument_sets_two_remaining_title_lines_bone_then_gold() -> None:
    copy_two = {"eyebrow": "BURIED HISTORY", "line1": "A CITY 280 FT DOWN", "line2": "BEHIND ONE WALL",
                "tagline": "CAPPADOCIA"}
    _, lay = _cover(copy_two, "monument")
    assert _el(lay, "title_1")["fill"] == list(brand.BONE) and _el(lay, "title_2")["fill"] == list(brand.GOLD)
    _, lay = _cover(GUMBAZ, "monument")            # one title line left: bone under the gold figure
    assert _el(lay, "title_2")["fill"] == list(brand.BONE)


def test_minimal_spaces_masthead_eyebrow_and_title_ink_to_ink() -> None:
    _, lay = _cover(GUMBAZ, "minimal")
    brand_box = _el(lay, "brand")["box"]
    eyebrow, title = _el(lay, "eyebrow")["box"], _el(lay, "title_1")["box"]
    assert abs((eyebrow[1] - brand_box[3]) - MINIMAL_GAPS[0]) <= 3
    assert abs((title[1] - eyebrow[3]) - MINIMAL_GAPS[1]) <= 3


def test_display_copy_uses_a_true_ellipsis_and_drops_a_subline_the_headline_already_carries() -> None:
    assert display("BEHIND ONE WALL...") == "BEHIND ONE WALL…"
    assert redundant_tagline({"line1": "THE EYE OF", "line2": "THE SAHARA", "tagline": "SAHARA"})
    assert not redundant_tagline(DERINKUYU)
    _, lay = _cover(DERINKUYU, "minimal")
    assert lay.meta["plates"]["title_1"][0].text.endswith("…")
    _, lay = _cover({"eyebrow": "MAURITANIA", "line1": "THE EYE OF", "line2": "THE SAHARA", "tagline": "SAHARA"},
                    "badge")
    assert "tagline" not in {e["name"] for e in lay.elements}


# ---------------------------------------------------------------- the grid's rhythm and the retired style

def test_cover_history_cannot_rotate_the_fixed_style_while_frozen() -> None:
    for seed in "abcdefghij":
        style, _ = cover_selector.choose(GUMBAZ, [], previous="badge", seed=seed, recent=["badge", "depth_sandwich"])
        assert style == "badge"
        style, _ = cover_selector.choose(GUMBAZ, [], previous="depth_sandwich", seed=seed,
                                         recent=["depth_sandwich", "monument"])
        assert style == "badge"
    # Fit/history variation cannot reactivate experimentation without cover-specific evidence.
    style, _ = cover_selector.choose(NO_FIGURE, [], previous="badge", seed="z", recent=["badge", "depth_sandwich"])
    assert style == "badge"
    assert {cover_selector.choose(GUMBAZ, [], seed=s)[0] for s in "abcdefghijklmnop"} == {"badge"}


def test_minimal_is_retired_for_new_selections_but_still_renders() -> None:
    assert "minimal" not in cover_selector.fitting(GUMBAZ) and "minimal" not in cover_selector.fitting(NO_FIGURE)
    assert {cover_selector.choose(GUMBAZ, [], seed=s)[0] for s in "abcdefghijklmnopqrst"} <= {
        "badge", "monument", "depth_sandwich"}
    im, lay = _cover(GUMBAZ, "minimal")
    assert im.size == (1080, 1920) and lay.elements


def test_for_run_reads_no_audience_or_neighbour_history_while_frozen(db, monkeypatch) -> None:
    # QF-82: neither the audience table nor the previous covers may move the choice, so neither is read.
    for run in ("r1", "r2", "r3"):
        db.create_run(run, "01_topic")

    def forbidden(*args, **kwargs):
        raise AssertionError("the frozen cover choice read history")
    monkeypatch.setattr("src.audience.table", forbidden)
    monkeypatch.setattr("src.audience.cover_style", forbidden)
    monkeypatch.setattr(db, "list_recent_runs", forbidden)
    style, why = cover_selector.for_run(db, "r3", {"thumbnail": GUMBAZ})
    assert style == "badge" and "frozen" in why


# ---------------------------------------------------------------- carousel

ROMAN = {"carousel_slides": [
    {"heading": "THE ROMAN CONCRETE", "body": "Seawalls built two thousand years ago still stand in the waves."},
    {"heading": "SEAWATER REACTION", "body": "Seawater percolating through the concrete grew new minerals in it."},
    {"heading": "SELF-HEALING CRACKS", "body": "Lime clasts dissolve into cracks and seal them again."},
    {"heading": "DEEP LEVELS", "body": "Carved down through soft rock, level after level, below the fields."},
    {"heading": "VOLCANIC ASH", "body": "Ash from the Bay of Naples was mixed with lime and seawater."},
    {"heading": "A MODERN COPY", "body": "Engineers now test mixes inspired by it."},
    {"heading": "WHO KNEW?", "body": "Would you build with seawater?"}],
    "narration_script": "Roman concrete.", "poll": {}}


@pytest.fixture
def photos(tmp_path):
    out = []
    for i, colour in enumerate([(150, 120, 90), (70, 90, 120), (200, 190, 170), (90, 80, 60)]):
        p = tmp_path / f"p{i}.jpg"
        _source(colour).convert("RGB").save(p)
        out.append(p)
    return out


def _records(slides):
    return [still_layout.load(p) for p in slides]


def _box(rec, name):
    return next((e["box"] for e in rec["elements"] if e["name"] == name), None)


@pytest.mark.parametrize("facebook", [False, True])
def test_content_slides_share_their_kicker_and_heading_baselines(photos, tmp_path, facebook) -> None:
    gen = MultiFormatGenerator()
    slides = gen.generate_carousel_sequence(photos * 2, dict(ROMAN, title="Roman concrete"), tmp_path / "run",
                                            facebook=facebook)
    recs = _records(slides)
    content = [r for r in recs[1:-1] if r["recipe"]["archetype"] != "colossal_stat"]
    assert len(content) >= 3 and all(not r["problems"] for r in recs)
    kicker_feet, heading_feet = set(), set()
    for rec in content:
        kicker_feet.add(_box(rec, "kicker")[3])
    assert max(kicker_feet) - min(kicker_feet) <= 2     # the kicker's foot (its baseline) on one line
    # the heading's baseline: from each content slide drawn again from its recipe
    for i, p in enumerate(slides[1:-1], 1):
        rec = still_layout.load(p)
        if rec["recipe"]["archetype"] == "colossal_stat":
            continue
        _, lay = gen._render_carousel_slide(rec["recipe"]["archetype"], Path(rec["recipe"]["photo"]),
                                             Path(rec["recipe"]["photo_next"]), rec["recipe"]["card"], i, len(slides),
                                             {}, "", facebook, subject=rec["recipe"]["subject"])
        heading_feet.add(lay.meta["baselines"]["heading"])
        first = lay.plates["heading"][0]
        assert round(first.xy[1]) == lay.meta["baselines"]["heading"]
    assert len(heading_feet) == 1


def test_body_lines_are_34_px_or_more_hold_34_characters_and_end_on_more_than_one_word(photos, tmp_path) -> None:
    gen = MultiFormatGenerator()
    card = {"heading": "MAGMATIC UPLIFT",
            "body": "Subsurface igneous intrusion deformed overlying sedimentary rock into concentric rings."}
    _, lay = gen._render_carousel_slide("editorial_split", photos[0], photos[1], card, 3, 7, {}, "", False,
                                         subject="THE EYE")
    body = lay.plates["body"]
    px = MultiFormatGenerator._px(MultiFormatGenerator.CAROUSEL_SIZE[0])
    assert all(p.font.size >= px.exact(34) - 0.01 and p.font.size <= px.exact(36) + 0.01 for p in body)
    assert all(len(p.text) <= MultiFormatGenerator.BODY_MEASURE for p in body)
    assert len(body) > 1 and len(body[-1].text.split()) > 1
    step = body[1].xy[1] - body[0].xy[1]
    assert 1.35 <= step / body[0].font.size <= 1.42


def test_facebook_headings_are_set_at_140_px_where_they_fit(photos, tmp_path) -> None:
    gen = MultiFormatGenerator()
    px = MultiFormatGenerator._px(MultiFormatGenerator.CAROUSEL_SIZE[0])
    for heading in ("ANCIENT COOLING", "IMPACT THEORY DISPROVED"):
        card = {"heading": heading, "body": "Initially considered an impact crater, the theory was disproved."}
        _, fb = gen._render_carousel_slide("editorial_split", photos[0], photos[1], card, 2, 7, {}, "", True, subject="S")
        _, ig = gen._render_carousel_slide("editorial_split", photos[0], photos[1], card, 2, 7, {}, "", False, subject="S")
        assert fb.plates["heading"][0].font.size >= px.exact(140) - 0.01
        assert fb.plates["heading"][0].font.size > ig.plates["heading"][0].font.size
        assert not fb.problems()


def _poster(path: Path) -> Path:
    im = Image.new("RGB", (1080, 1920), brand.INK)
    d = ImageDraw.Draw(im)
    d.rectangle((120, 400, 900, 1000), outline=brand.GOLD, width=6)
    d.line((120, 700, 900, 700), fill=brand.BONE, width=4)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def test_a_run_with_an_explanatory_graphic_gets_one_diagram_slide_in_both_copies(photos, tmp_path) -> None:
    run = tmp_path / "run"
    poster = _poster(run / "graphics" / "graphic_section.jpg")
    _poster(run / "graphics" / "graphic_numbers.jpg")              # a photo-backed card: never a diagram slide
    gen = MultiFormatGenerator()
    ig = gen.generate_carousel_sequence(photos * 2, dict(ROMAN, title="Roman concrete"), run)
    fb = gen.generate_carousel_sequence(photos * 2, dict(ROMAN, title="Roman concrete"), run, facebook=True)
    for copy in (ig, fb):
        recs = _records(copy)
        kinds = [r["recipe"]["archetype"] for r in recs]
        assert kinds.count("diagram") == 1 and kinds.index("diagram") == 3   # "DEEP LEVELS" says what it draws
        diagram = recs[3]
        assert diagram["recipe"]["photo"] == str(poster) and not diagram["problems"]
        assert _box(diagram, "kicker") and _box(diagram, "heading") and _box(diagram, "body")
        assert len(copy) == len(ROMAN["carousel_slides"])
    assert multi_format.slide_alt_text(ig[3]).startswith("A diagram from the video.")
    assert multi_format.slide_photos(ig[3]) == [poster]
    assert multi_format.photo_credit(poster) == {}


def test_without_a_graphic_poster_the_carousel_is_unchanged(photos, tmp_path) -> None:
    gen = MultiFormatGenerator()
    slides = gen.generate_carousel_sequence(photos * 2, dict(ROMAN, title="Roman concrete"), tmp_path / "run")
    assert "diagram" not in [r["recipe"]["archetype"] for r in _records(slides)]
    assert multi_format.diagram_poster(tmp_path / "run") is None
    # an explicit list of graphics is read too (the .mp4 names its poster beside it)
    poster = _poster(tmp_path / "elsewhere" / "graphic_defense.jpg")
    assert multi_format.diagram_poster(tmp_path / "run", [poster.with_suffix(".mp4")]) == ("defense", poster)


def test_the_facebook_gallery_takes_five_strongest_in_order(tmp_path) -> None:
    slides = [str(tmp_path / f"carousel_slide_{i:02d}.jpg") for i in range(1, 8)]
    picked = facebook_pick(slides, multi_format.FACEBOOK_GALLERY)
    assert multi_format.FACEBOOK_GALLERY == 5
    assert len(picked) == 5 and picked[0] == slides[0] and picked[-1] == slides[-1] and picked == sorted(picked)
    assert facebook_pick(slides[:5], 5) == slides[:5]


# ---------------------------------------------------------------- Stories

def test_story_display_lines_have_no_tilde_and_short_units() -> None:
    assert story_display("~280 FEET DEEP") == "ABOUT 280 FT DEEP"
    assert story_display("THE 25-MILE EYE") == "THE 25-MILE EYE"
    assert story_figure("AN UNDERGROUND CITY", "ABOUT 280 FT DEEP") == {
        "number": "280", "unit": "FT DEEP", "qualifier": "ABOUT", "title": "AN UNDERGROUND CITY"}
    assert story_figure("A CITY", "UNDER THE FIELDS") is None


def _frame(tmp_path, slide, i, poll=None, colour=(120, 110, 95)):
    photo = tmp_path / f"s{i}.jpg"
    Image.new("RGB", (1080, 1920), colour).save(photo)
    return MultiFormatGenerator()._render_story_frame(photo, slide, i, 3, poll or {})


def test_the_reveal_frame_makes_its_figure_the_monument(tmp_path) -> None:
    slide = {"kicker": "WHAT HE FOUND", "line1": "AN UNDERGROUND CITY", "line2": "~280 FEET DEEP",
             "body": "Stables, wells, chapels and stone doors."}
    _, lay = _frame(tmp_path, slide, 1)
    fig, unit = lay.plates["figure"][0], lay.plates["figure_unit"][0]
    assert fig.text == "280" and fig.fill == brand.GOLD and fig.font.size >= 280
    assert unit.text == "FT DEEP" and unit.box[1] > fig.box[3]
    assert lay.plates["figure_qualifier"][0].text == "ABOUT"
    placed = " ".join(p.text for ps in lay.plates.values() for p in ps)
    assert "~" not in placed
    assert not lay.problems() and lay.meta["hero_figure"] == "280"
    assert all(e["box"][1] >= 250 and e["box"][3] <= 1600 for e in lay.elements)
    # no figure: the two-tone title as before
    _, plain = _frame(tmp_path, dict(slide, line2="UNDER THE FIELDS"), 1)
    assert "figure" not in plain.plates and "title_2" in plain.plates


def test_the_poll_sticker_room_sits_under_the_question_on_an_ink_panel(tmp_path) -> None:
    import numpy as np
    slide = {"kicker": "YOUR TURN", "line1": "WOULD YOU HIDE", "line2": "DOWN THERE?", "body": "Full story in our latest Reel."}
    poll = {"question": "Would you hide underground for weeks?", "options": ["Yes", "No"]}
    im, lay = _frame(tmp_path, slide, 2, poll, colour=(200, 200, 200))
    room = lay.meta["poll_sticker_room"]
    title_foot = max(e["box"][3] for e in lay.elements if e["name"].startswith("title"))
    assert 0 < room[1] - title_foot <= 60 and room[3] - room[1] >= 240 and room[1] < 1000
    assert lay.protected == [{"name": "poll_sticker_room", "box": room}] and not lay.problems()
    inside = np.asarray(im.convert("L"))[room[1] + 4:room[3] - 4, room[0] + 4:room[2] - 4]
    assert inside.max() < 160                                     # an ink panel, nothing drawn in it
    assert all(e["box"][3] <= 1248 for e in lay.elements)       # clear of the reply bar


def test_story_titles_take_three_lines_of_860_px_before_shrinking(tmp_path) -> None:
    slide = {"kicker": "YOUR TURN", "line1": "WOULD YOU HIDE", "line2": "UNDERGROUND FOR WEEKS?", "body": "Short."}
    _, lay = _frame(tmp_path, slide, 0)
    titles = lay.plates["title_1"] + lay.plates["title_2"]
    assert len(titles) <= 3 and all(p.box[2] - multi_format.STORY_MARGIN <= multi_format.STORY_TITLE_W + 8
                                    for p in titles)
    assert titles[0].font.size >= 128
    assert all(p.font.size == lay.plates["body"][0].font.size for p in lay.plates["body"])
    assert multi_format.STORY_BODY_WEIGHT == 600


# ---------------------------------------------------------------- YouTube thumbnail

def test_a_clamped_crop_is_enlarged_until_the_feature_lands_right_and_no_type_covers_it() -> None:
    src = Image.new("RGB", (1600, 1000), (200, 170, 120))
    meta = {}
    _, plates = youtube_thumbnail.render(src, {"line1": "THE 25-MILE", "line2": "SAHARA EYE"},
                                         {"focus_box": [0.4, 0.3, 0.6, 0.7]}, meta=meta)
    assert 1.0 < meta["zoom"] <= youtube_thumbnail.MAX_ZOOM
    assert youtube_thumbnail.FOCUS_LAND[0] - 10 <= meta["focus_x"] <= youtube_thumbnail.FOCUS_LAND[1]
    fb = meta["focus_box"]
    assert meta["text_over_focus"] == []
    for name, p in plates:
        assert not (p.box[0] < fb[2] and fb[0] < p.box[2] and p.box[1] < fb[3] and fb[1] < p.box[3]), name


def test_thumbnail_masthead_figure_and_word_budget() -> None:
    src = Image.new("RGB", (1920, 1080), (90, 80, 70))
    _, plates = youtube_thumbnail.render(src, {"line1": "THE 25-MILE", "line2": "SAHARA EYE OF THE DESERT"})
    names = dict(plates)
    assert names["brand"].fill == brand.GOLD and names["brand"].font.size >= 60
    assert names["figure_unit"].font.size == pytest.approx(names["figure"].font.size * 0.5, abs=2)
    words = ["25", "MILES"] + names["title"].text.split()
    assert len(words) <= youtube_thumbnail.MAX_WORDS
    gap = names["title"].box[1] - names["figure"].box[3]
    assert gap <= 0.12 * names["title"].font.size + 4
    # a year in the tagline is the hero when there is no measured figure: four words in all
    _, plates = youtube_thumbnail.render(src, DERINKUYU)
    texts = {n: p.text for n, p in plates}
    assert texts["figure"] == "1963" and len(texts["figure"].split() + texts["title"].split()) <= 4
    x0, y0, _, _ = youtube_thumbnail.TIMESTAMP_ZONE
    assert all(not (p.box[2] > x0 and p.box[3] > y0) for _, p in plates)


def test_the_left_ink_is_85_percent_over_the_left_42_percent() -> None:
    white = Image.new("RGB", (1920, 1080), (255, 255, 255))
    shaded = youtube_thumbnail._gradient(white).convert("RGB")
    left = shaded.getpixel((400, 900))[0]
    expected = round(255 * 0.15 + brand.INK[0] * 0.85)
    assert abs(left - expected) <= 3
    assert shaded.getpixel((1700, 900))[0] >= 250
