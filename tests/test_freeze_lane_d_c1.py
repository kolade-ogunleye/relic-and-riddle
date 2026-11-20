"""Design-freeze lane D (carousel and Stories), cycle 1: invariants for the carry-in classes
(C:/tmp/qf-handoff/queue/LANE-D-carry-in.md items 1, 4, 5 and QF-145; synthetic inputs, CI has no output/).

1. The Story reveal frame's hero figure takes the figure role's size steps (never a continuous fit), its qualifier
   and unit one matched pair on the tall display ladder, and its title the Story's one title size, never under the
   Story floors (it drew 0.66 x the Story size: 91 at 138, 99 at 150).
2. QF-145: a Story reveal reads the system's one hedge list, so "ESTIMATED" (and its forms) qualifies a figure.
3. The carousel stat slide draws its figure whole with its era (a descending BCE span was withheld for failing its own
   truth check; a BC year was drawn bare); an era-crossing span has no stat layout and is withheld, recorded.
4. A diagram slide holds a drawing only when its labels stay at the carousel's label floor as laid (the 9:16 scale
   drawing shrunk to ~0.4 of the slide drew 19 px labels against 36).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, figure_gate, figures, graphics, multi_format, youtube_thumbnail
from src.brand_system import GATES, label_floor_px
from src.multi_format import MultiFormatGenerator, story_figure, story_qualifier, story_title_px

STORY_FLOOR_ONE, STORY_FLOOR_WRAP = GATES["heading.story_one_line_floor"], GATES["heading.story_floor"]
LADDER = {round(s) for s in brand_system.display_steps("tall", STORY_FLOOR_ONE)}


def _frame(tmp_path: Path, slide: dict, i: int = 1, colour=(110, 100, 90)):
    photo = tmp_path / f"s{i}.jpg"
    Image.new("RGB", (1080, 1920), colour).save(photo)
    return MultiFormatGenerator()._render_story_frame(photo, slide, i, 3, {})


# --------------------------------------------------------------------------------------- 1. the Story reveal
def test_the_reveal_figure_steps_are_the_systems_figure_ladder():
    steps = multi_format.STORY_FIGURE_STEPS
    assert steps == youtube_thumbnail.FIGURE_STEPS                      # one figure ladder, held equal
    assert steps[0] == brand_system.TYPE["tall"]["figure"]             # led by the tall canvas's figure token
    assert list(steps) == sorted(steps, reverse=True)
    assert multi_format.story_figure_pair_px() in {round(s) for s in brand_system.display_steps("tall")}


REVEALS = [
    {"kicker": "WHAT HE FOUND", "line1": "AN UNDERGROUND CITY", "line2": "~280 FEET DEEP", "body": "Stables and wells."},
    {"kicker": "HOW OLD", "line1": "94–104 MILLION YEARS OLD", "line2": "THE RINGS", "body": "Older than the desert."},
    {"kicker": "THE DOME", "line1": "ESTIMATED 44 M", "line2": "ACROSS THE GALLERY", "body": "A vast chamber."},
    {"kicker": "A LONG ONE", "line1": "1,234,567,890 TONNES", "line2": "OF STONE", "body": "A long number."},
    {"kicker": "A LONG TITLE", "line1": "UP TO 20,000 PEOPLE",
     "line2": "SHELTERED UNDER THE CAPPADOCIAN HILLS", "body": "A long title."},
]


@pytest.mark.parametrize("slide", REVEALS, ids=["qualifier", "span", "estimated", "long-number", "long-title"])
def test_the_reveal_frame_sets_its_figure_on_steps_and_its_title_at_the_story_size(tmp_path, slide):
    from src.typography import TextFitError
    try:
        _im, lay = _frame(tmp_path, slide)
    except TextFitError:                # refused whole (QF-23), never drawn under the Story floors
        # only the cases no valid form holds at the floors: a title two lines >= 120 px cannot set, a number that
        # fits no figure step and, as a title, could only break from its unit
        assert slide["kicker"].startswith("A LONG")
        return
    if "figure" not in lay.plates:      # no step fits: the frame is the plain title, at the floors (never smaller)
        sizes = {p.font.size for k in ("title_1", "title_2") for p in lay.plates.get(k, [])}
        assert sizes and min(sizes) >= STORY_FLOOR_ONE
        return
    fig = lay.plates["figure"][0]
    assert fig.font.size in multi_format.STORY_FIGURE_STEPS
    pair = lay.plates.get("figure_unit", []) + lay.plates.get("figure_qualifier", [])
    assert {p.font.size for p in pair} <= {multi_format.story_figure_pair_px()}
    titles = lay.plates.get("title_1", [])
    if titles:
        t = {p.font.size for p in titles}
        assert len(t) == 1 and min(t) in LADDER and min(t) >= STORY_FLOOR_ONE
        if len({p.box[1] for p in titles}) > 1:                   # a wrapped title never under the wrapped floor
            assert min(t) >= STORY_FLOOR_WRAP
        assert all(tuple(p.fill) == tuple(brand.BONE) for p in titles)
    # drawn == signed: the figure's parts read back as the line that states it
    l1, l2 = multi_format.story_fields(slide)
    line = next(x for x in (l1, l2) if any(ch.isdigit() for ch in x))
    drawn = " ".join(p.text for k in ("figure_qualifier", "figure", "figure_unit") for p in lay.plates.get(k, []))
    assert figures.truth_faults(drawn, [line]) == []
    assert not lay.problems()
    assert all(e["box"][1] >= 269 and e["box"][3] <= 1248 for e in lay.elements)    # the Story box


def test_the_reveal_title_is_the_storys_one_size(tmp_path):
    slides = [{"kicker": "A", "line1": "ONE MAN", "line2": "KNOCKED", "body": "Short."}, REVEALS[0],
              {"kicker": "C", "line1": "WHAT LIES", "line2": "BELOW?", "body": "Short."}]
    size = story_title_px(slides)
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1080, 1920), (90, 90, 90)).save(photo)
    _im, lay = MultiFormatGenerator()._render_story_frame(photo, REVEALS[0], 1, 3, {}, title_px=size)
    drawn = {p.font.size for p in lay.plates["title_1"]}
    assert len(drawn) == 1 and lay.meta["story_title_px"] == size
    # at the Story size, else (only when the figure on its least step still leaves no room) one step down, recorded
    assert drawn == {size} or (min(drawn) < size and min(drawn) in LADDER and lay.meta["story_size_off"])
    assert lay.plates["figure"][0].font.size in multi_format.STORY_FIGURE_STEPS


# --------------------------------------------------------------------------------------- 2. QF-145 qualifiers
@pytest.mark.parametrize("prefix, drawn", [("ESTIMATED", "ESTIMATED"), ("AN ESTIMATED", "ESTIMATED"),
                                           ("EST.", "ESTIMATED"), ("AT LEAST", "AT LEAST"), ("CIRCA", "CIRCA"),
                                           ("ABOUT", "ABOUT"), ("UP TO", "UP TO"), ("APPROX", "APPROXIMATELY"),
                                           ("APPROX.", "APPROXIMATELY"), ("ROUGHLY", "ROUGHLY")])
def test_a_story_figure_reads_the_systems_hedges(prefix, drawn):
    assert story_qualifier(prefix) == drawn
    hero = story_figure(f"{prefix} 44 M", "ACROSS THE DOME")
    assert hero and hero["qualifier"] == drawn and hero["number"] == "44"


def test_words_that_are_no_hedge_keep_the_frame_a_title():
    assert story_qualifier("THE") is None
    assert story_figure("THE 144-FOOT DOME", "OF GOL GUMBAZ") is None


# --------------------------------------------------------------------------------------- 3. the stat slide's era
def _stat(tmp_path, body):
    p = tmp_path / "photo.jpg"
    Image.new("RGB", (1200, 1600), (96, 84, 70)).save(p)
    return MultiFormatGenerator()._render_carousel_slide("colossal_stat", p, p, {"heading": "A FIGURE", "body": body},
                                                         3, 7, {}, "", False, subject="SUBJECT")


@pytest.mark.parametrize("body, drawn", [
    ("The site dates to between 9500 and 9000 BCE, before farming.", "9500–9000 BCE"),
    ("The pyramid was finished about 2560 BC by the crews.", "ABOUT 2560 BC"),
])
def test_the_stat_slide_draws_its_figure_whole_with_its_era(tmp_path, body, drawn):
    _im, lay = _stat(tmp_path, body)
    assert lay.meta["stat"] == drawn and lay.meta["stat_figure"]["drawn"] == drawn
    hedge, num, _unit, tail = figures.split_drawn(drawn)
    fills = {" ".join(p.text.split()): tuple(p.fill) for p in lay.plates["stat"]}
    assert fills[num] == tuple(multi_format.figure_colour())                   # the number gold
    era = drawn.split()[-1]
    assert fills[era] == tuple(brand.BONE)                                     # the era bone, never gold
    rec = next(e for e in lay.elements if e["name"] == "stat")
    assert figure_gate.figure_faults(rec["text"], figures.parse(body)) == []   # the gate passes it whole


def test_an_era_crossing_span_is_withheld_and_recorded(tmp_path):
    _im, lay = _stat(tmp_path, "The lines were made between 500 BC and 500 AD by two cultures.")
    assert "stat" not in lay.meta and "stat" not in lay.plates
    assert any("era-crossing" in f for f in lay.meta["stat_figure"]["faults"])


# --------------------------------------------------------------------------------------- 4. diagram legibility
def _poster(path: Path, size, box) -> Path:
    im = Image.new("RGB", size, brand.INK)
    ImageDraw.Draw(im).rectangle(box, outline=brand.GOLD, width=6)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def test_a_drawing_shrunk_under_the_label_floor_is_not_held(tmp_path):
    gen = MultiFormatGenerator()
    # the 9:16 scale drawing's real extent (most of the frame) on the slide: about 0.4 of its size
    big = _poster(tmp_path / "graphic_scale.jpg", (1080, 1920), (100, 300, 980, 1700))
    ok, rec = gen.diagram_legible(big, ((False, 2), (True, 2)))
    assert not ok and rec["label_px_at_least"] < rec["slide_floor_px"] == pytest.approx(label_floor_px(1440, 1800))
    # a drawing whose box the slide lays at >= 4/3 keeps the 9:16 floor's 27 px at >= 36 (lane D cycle 3: on both
    # copies' rooms, the Facebook one shorter under the masthead in the collage square)
    small = _poster(tmp_path / "graphic_section.jpg", (1080, 1920), (330, 760, 730, 1040))
    ok, rec = gen.diagram_legible(small, ((False, 2), (True, 2)))
    assert ok and rec["label_px_at_least"] >= rec["slide_floor_px"]
    # a 16:9 drawing guarantees no floor of its own: never held
    wide = _poster(tmp_path / "graphic_route.jpg", (1920, 1080), (900, 500, 1000, 560))
    assert graphics.label_floor(1920, 1080) == 0 and not gen.diagram_legible(wide, ((False, 2),))[0]


def test_the_rule_is_the_label_floor_at_the_laid_scale():
    """The guard is the floor arithmetic, not a pinned size: poster floor x scale >= the slide's label floor."""
    gen = MultiFormatGenerator()
    for lines in (1, 2):
        for fb in (False, True):
            top, bottom, k = gen._diagram_fit((1080, 1920), (0, 0, 1080, 1920), 1440, fb, lines)
            assert 0 < top < bottom <= 1800 and k < 4 / 3      # a whole 9:16 frame never reaches the floor
