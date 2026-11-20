"""Design-freeze lane D (carousel and Stories), cycle 2: invariants for the lander's rulings of 2026-11-19 ~05:20
(decisions/OVERNIGHT-DELEGATION-2026-11-12.md) and the verified panel-2 leads. Synthetic inputs (CI has no output/);
the heading list is the real decks' content headings whose IG and FB forms differed in cycle 1 (51 of 218).

1. One deck, one form: the Facebook copy sets every content heading in the Instagram form, on one frame (SYS-D-4).
2. A scale diagram slide is the drawing drawn for the slide's room (graphics.render_scale drawing_floor, lane A),
   laid 1:1, every label at the carousel floor (cycle 3: the slide keeps its own signed words; see
   test_freeze_lane_d_c3).
3. A Story frame's window keeps a foreign object out of its text column where the picture allows (deck window rule).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import graphics, multi_format
from src.multi_format import MultiFormatGenerator, _render_state, current_poster, slide_drawing

# the real content headings whose IG and FB forms differed before the FB tier was retired (cycle 1 scan)
REAL_HEADINGS = [
    "VANISHED IN 1944", "SURVIVING TRACES", "SURFACED MOSAIC", "THE DAMASCUS FORGE", "ACOUSTIC GUIDANCE",
    "ARTILLERY ENGINES", "MODERN ANCESTRY", "CENTURIES OF USE", "CAVE 16 SANCTUARY", "QUARRY EFFICIENCY",
    "SIMPLE SURVEYING", "TWO ANCIENT ERAS", "SURVEYING STAKES", "MAGMATIC UPLIFT", "DESERT LANDMARK",
    "ERODED ROCK DOME", "ORBITAL LANDMARK", "MAGMA AND UPLIFT", "IMPACT DISPROVED", "SACRED GUARDIANS",
    "RADIAL FLATTENING", "FIRE AND BEDROCK", "CRACKED IN PLACE", "EXTRACTION PUZZLE", "STRUCTURAL LIMITS",
    "PARCHMENT ORIGIN", "PRESERVED AT YALE", "UNBROKEN CIPHER", "35 BRONZE GEARS", "PLANETARY CYCLES",
    "HISTORICAL HIATUS", "35 MESHING GEARS", "HIDDEN GEARWORK", "DEFENSIVE DESIGN", "TACTICAL FORTRESS",
    "ABANDONED IN 1923", "IMPACT THEORY DISPROVED", "EARTH'S HIDDEN MARVELS",
]


def _form(heading: str, facebook: bool):
    g = MultiFormatGenerator()
    cw = g.CAROUSEL_SIZE[0]
    px = g._px(cw)
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    prev = getattr(_render_state, "facebook", False)
    _render_state.facebook = facebook
    try:
        form, font = g._set_heading(d, px, heading, cw - 2 * px(g.MARGIN), "wrap", 1.0, (112, 60), True)
    finally:
        _render_state.facebook = prev
    return form.lines, form.size, font.size


# --------------------------------------------------------------------------------------- 1. one form per heading
def test_the_facebook_tier_is_retired():
    assert not hasattr(MultiFormatGenerator, "CONTENT_HEADING_FB")


@pytest.mark.parametrize("heading", REAL_HEADINGS)
def test_every_real_heading_sets_one_form_on_both_copies(heading):
    assert _form(heading, True) == _form(heading, False)


def test_both_copies_share_one_frame():
    g = MultiFormatGenerator()
    px = g._px(g.CAROUSEL_SIZE[0])
    for lines in (1, 2):
        assert g._content_frame(px, True, lines) == g._content_frame(px, False, lines)
    cards = [{"heading": h, "body": "A body line of ordinary length for the slide."} for h in
             ["THE COVER", *REAL_HEADINGS[:5], "YOUR TAKE"]]
    arche = g.sequence_archetypes(cards)
    assert g._deck_heading_frame(cards, arche, True) == g._deck_heading_frame(cards, arche, False)


# --------------------------------------------------------------------------------------- 2. the scale diagram
FACT = {"value": 138.0, "unit": "ft", "dimension": "length", "subject": "Unfinished obelisk", "approximate": True}


def test_a_scale_diagram_is_drawn_for_the_slides_room(tmp_path):
    g = MultiFormatGenerator()
    room = g.diagram_room()
    stored = tmp_path / "graphics" / "graphic_scale.jpg"
    stored.parent.mkdir(parents=True)
    Image.new("RGB", (1080, 1920), (20, 20, 20)).save(stored)                  # a stale 9:16 poster
    got, why = current_poster(tmp_path, stored, {"scale_fact": FACT}, room=room, slide_width=g.CAROUSEL_SIZE[0])
    assert got is not None and got != stored and "slide's room" in why
    rec = slide_drawing(got)
    with Image.open(got) as im:
        assert im.size == room == tuple(rec["size"])
    assert rec["drawing_floor"] == pytest.approx(graphics.diagram_label_floor(g.CAROUSEL_SIZE[0]))
    assert "heading" not in rec and "body" not in rec            # cycle 3: the drawing brings no slide words
    # the gate holds it at 1:1 on both copies' frames; the slide lays it whole, inside the room, unscaled
    ok, why_ok = g.diagram_legible(got, ((False, 2), (True, 2), (False, 1), (True, 1)))
    assert ok and why_ok["label_px_at_least"] >= why_ok["slide_floor_px"]
    card = {"heading": "ANCIENT WORK MARKS", "body": "Crews burned and wedged the rock, leaving tool marks."}
    for fb in (False, True):
        _im, lay = g._render_carousel_slide("diagram", got, got, card, 2, 7, {}, "", fb, subject="S")
        x0, y0, x1, y1 = lay.meta["diagram"]["image_box"]
        assert (x1 - x0, y1 - y0) == room and not lay.problems()
    # a second call reuses the drawing (cached on the room and the floor)
    again, why2 = current_poster(tmp_path, stored, {"scale_fact": FACT}, room=room, slide_width=g.CAROUSEL_SIZE[0])
    assert again == got and "cached" in why2


def test_a_drawing_that_overflows_a_frames_room_is_not_held(tmp_path):
    g = MultiFormatGenerator()
    w, h = g.diagram_room()
    big = tmp_path / "graphic_scale.jpg"
    Image.new("RGB", (w, h + 40), (20, 20, 20)).save(big)
    Path(str(big) + multi_format.DRAWING_SIDECAR).write_text(json.dumps({"drawing_floor": 36, "size": [w, h + 40]}),
                                                          encoding="utf-8")
    assert not g.diagram_legible(big, ((False, 2),))[0]


# --------------------------------------------------------------------------------------- 3. the Story window
def _photo_with_object(path: Path, at_x: int | None) -> Path:
    im = Image.new("RGB", (3000, 1500), (120, 116, 110))
    if at_x is not None:   # a saturated, straight-edged foreign object (a sign, a panel) in the text column's rows
        ImageDraw.Draw(im).rectangle((at_x, 380, at_x + 320, 720), fill=(220, 30, 30))
    im.save(path)
    return path


def test_a_story_window_keeps_a_foreign_object_out_of_its_text_column(tmp_path):
    g = MultiFormatGenerator()
    slide = {"kicker": "THE SITE", "line1": "A STONE RING", "line2": "IN THE DESERT", "body": "A short body."}
    photo = _photo_with_object(tmp_path / "obj.jpg", 1150)
    _im, lay = g._render_story_frame(photo, slide, 1, 3, {})
    win = lay.meta["window"]
    assert win["centering"][0] != 0.5 and win["text_clear"] is True
    assert 0.0 <= win["centering"][0] <= 1.0 and not lay.problems()
    plain = _photo_with_object(tmp_path / "plain.jpg", None)
    _im, lay = g._render_story_frame(plain, slide, 1, 3, {})
    assert lay.meta["window"]["centering"] == [0.5, 0.5]                      # centred, as before


# --------------------------------------------------------------------------------------- 4. Story floors are gates
# Real Story decks whose frames the old last resort drew wrapped at 106 (cycle 2 scan: 9 of 144 frames, 7 decks)
FLOOR_DECKS = [
    [{"kicker": "THE HEIST", "line1": "STRIPPED FROM WALLS", "line2": "PACKED INTO CRATES",
      "body": "In 1941, invading soldiers dismantled the entire chamber at the Catherine Palace."},
     {"kicker": "THE EVIDENCE", "line1": "A SURVIVING FRAGMENT", "line2": "FOUND IN GERMANY",
      "body": "In 1997, an original stone mosaic titled Feel and Touch was recovered in Germany."},
     {"kicker": "THE POLL", "line1": "WHAT WAS THE ROOM'S", "line2": "TRUE FATE?",
      "body": "Official tracking ended in August 1944 when heavy fire-bombing struck the castle."}],
    [{"kicker": "DESERT ENIGMA", "line1": "ETCHED ACROSS PERU", "line2": "TWO THOUSAND YEARS",
      "body": "Giant geoglyphs etched into desert soil have survived wind and weather across southern Peru."},
     {"kicker": "SURFACE SCIENCE", "line1": "SCRAPING IRON OXIDE", "line2": "PROTECTED BY CLAY",
      "body": "Morning mist hardens the exposed clay, creating a natural shield that stops wind erosion."},
     {"kicker": "ANCIENT PURPOSE", "line1": "WALKWAYS OR SKY SIGNS", "line2": "CHOOSE YOUR THEORY",
      "body": "Archaeological surveys reveal hundreds of giant figures etched across the dry Peruvian plateau."}],
]


@pytest.mark.parametrize("deck", range(len(FLOOR_DECKS)))
def test_no_story_title_is_drawn_under_its_floor(tmp_path, deck):
    from src.brand_system import GATES
    from src.typography import LayoutError
    slides = FLOOR_DECKS[deck]
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1080, 1920), (120, 110, 95)).save(photo)
    size = multi_format.story_title_px(slides)
    for i, s in enumerate(slides):
        try:
            _im, lay = MultiFormatGenerator()._render_story_frame(photo, s, i, len(slides), {}, title_px=size)
        except LayoutError:
            continue                                   # refused whole: never drawn under a floor
        for name in ("title_1", "title_2"):
            ps = lay.plates.get(name, [])
            if not ps:
                continue
            assert min(p.font.size for p in ps) >= GATES["heading.story_one_line_floor"]
            if len({p.xy[1] for p in ps}) > 1:         # wrapped: never under the wrapped floor
                assert min(p.font.size for p in ps) >= GATES["heading.story_floor"]
