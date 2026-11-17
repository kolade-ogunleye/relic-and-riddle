"""Binding gates and graphics, 2026-10-29: the Story poll frame draws no reply instruction and no option boxes
and keeps the poll sticker's room empty; no emoji or missing-glyph box in rendered art; a two-unit figure counts
both units together; figures are drawn in the unit the narration speaks; a number card ends above the caption
band's gap (safe_zones.graphics_box)."""
import re

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import graphics, safe_zones
from src.multi_format import POLL_STICKER_ROOM, MultiFormatGenerator
from src.script_engine import engagement_bait
from src.typography import drawable, fit_text, get_font, spoken_units

RICHAT = ("Visible from space, concentric rings span the sands. Geological field surveys record the feature at "
          "25 miles (40 km) across, though satellite observations describe it as spanning almost 31 miles (50 km).")
DERINKUYU = "The city drops 280 feet (85 metres) beneath the plain."


@pytest.fixture
def gen():
    return MultiFormatGenerator()


def _photo(tmp_path, colour=(90, 90, 90)):
    path = tmp_path / "flat.jpg"
    Image.new("RGB", (1200, 1600), colour).save(path)
    return path


def _spy_text(monkeypatch):
    drawn = []
    real = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **k):
        drawn.append(str(text))
        return real(self, xy, text, *a, **k)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    return drawn


# ------------------------------------------------------------------ 1. the poll frame
POLL = {"question": "Planned city or lucky find?", "options": ["By design", "Pure chance"]}
SLIDE = {"kicker": "YOUR CALL", "line1": "PLANNED", "line2": "OR LUCKY?", "body": "Eighteen levels were cut by hand."}


def test_the_poll_frame_asks_the_question_with_no_reply_ask_and_no_option_boxes(gen, tmp_path, monkeypatch):
    drawn = _spy_text(monkeypatch)
    im, lay = gen._render_story_frame(_photo(tmp_path), SLIDE, 2, 3, POLL)
    names = [e["name"] for e in lay.elements]
    assert not [n for n in names if n.startswith("option_") or n == "weigh_in"], names
    assert not any("REPLY" in t for t in drawn) and not engagement_bait(" ".join(drawn)), drawn
    # no options of our own (read from the placed lines: a tracked kicker is drawn a letter at a time, 2026-10-29)
    lines = [p.text for ps in lay.plates.values() for p in ps]
    assert not [t for t in lines if t in ("A", "B") or "DESIGN" in t or "CHANCE" in t]
    assert not any("DESIGN" in t or "CHANCE" in t for t in drawn)
    # The question, once. Counted from the frame's placed lines: since the overhaul (2026-10-29) each line is drawn
    # several times while its contrast is measured (glyph masks, halo trials), so raw draw calls repeat it.
    placed = " ".join(p.text for ps in lay.plates.values() for p in ps)
    assert placed.count("LUCKY") == 1
    assert lay.problems() == [] and lay.protected[0]["name"] == "poll_sticker_room"


def test_the_poll_sticker_room_is_left_empty(gen, tmp_path):
    # flat grey photo: nothing bright (text, gold) may land in the sticker's room, and no element is recorded there
    im, lay = gen._render_story_frame(_photo(tmp_path), SLIDE, 2, 3, POLL)
    lum = np.asarray(im.convert("L"))
    y0, y1 = POLL_STICKER_ROOM
    assert int((lum[y0:y1] > 160).sum()) == 0
    assert all(e["box"][3] <= y0 or e["box"][1] >= y1 for e in lay.elements), lay.elements


# ------------------------------------------------------------------ 3. emoji and missing glyphs
def test_emoji_and_characters_the_font_cannot_draw_are_removed():
    assert drawable("Send this to a space enthusiast you know 🛰️, and follow.", "Montserrat") == \
        "Send this to a space enthusiast you know, and follow."
    assert drawable("A marvel 🌍.", "Montserrat") == "A marvel."
    assert drawable("✨Wow✨ it works", "Anton") == "Wow it works"
    assert drawable("Café — 1°C → ½", "Montserrat") == "Café — 1°C → ½"           # real glyphs are kept
    assert drawable("Café → ½", "Cinzel") == "Café → ½"     # Cinzel has no arrow: set in a face that has (QF-94)
    assert drawable("Plain ASCII, kept as it is.", "Montserrat") == "Plain ASCII, kept as it is."
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, _, _ = fit_text(d, "Earth from orbit 🚀🌍", "Montserrat", 40, 900, 2, 30)
    assert lines == ["Earth from orbit"]


def test_the_closing_slide_never_draws_a_missing_glyph_box(gen, tmp_path, monkeypatch):
    # production 29 Oct: "□□" after the closing line, an emoji and its variation selector drawn in Montserrat
    drawn = _spy_text(monkeypatch)
    photo = _photo(tmp_path)
    card = {"heading": "Your take 🤔", "body": "Which view wins? 🛰️"}
    share = "Send this to a space enthusiast you know 🛰️, and follow for the next natural marvel."
    gen._render_carousel_slide("debate", photo, photo, card, 6, 7, {}, share, False)
    font = get_font("Montserrat", 40)
    assert drawn and all(drawable(t, font) == t for t in drawn), drawn
    assert "Send this to a space enthusiast you know, and" in " ".join(drawn)       # the line itself is kept


# ------------------------------------------------------------------ 4. two units count together
def test_both_units_of_a_figure_count_up_together():
    assert graphics._shown_value("280 FT (85 M)", 0.0) == "0 FT (0 M)"
    assert graphics._shown_value("280 FT (85 M)", 0.5) == "140 FT (42 M)"
    assert graphics._shown_value("280 FT (85 M)", 1.0) == "280 FT (85 M)"
    assert graphics._shown_value("EST. 144 FT (44 M)", 0.25) == "EST. 36 FT (11 M)"
    assert graphics._shown_value("1404-1438", 0.3) == "1404-1438"                     # years stay whole
    assert graphics._shown_value("20,000", 0.5) == "10,000"


# ------------------------------------------------------------------ 5. the narration's unit
def test_figures_are_drawn_in_the_unit_the_narration_speaks():
    assert spoken_units("40+ KM", RICHAT) == "25+ MILES"                              # Richat's cover, 29 Oct
    assert spoken_units("THE 40-KM SAHARA EYE", RICHAT) == "THE 25-MILE SAHARA EYE"
    assert spoken_units("50 km across", RICHAT) == "31 miles across"
    assert spoken_units("THE 25-MILE", RICHAT) == "THE 25-MILE"                      # already the narration's
    assert spoken_units("85 M (280 FT)", DERINKUYU) == "280 FT (85 M)"                # both: the spoken one first
    assert spoken_units("280 FT (85 M)", DERINKUYU) == "280 FT (85 M)"                # both, already in order
    assert spoken_units("85 metres deep", DERINKUYU) == "280 feet deep"
    assert spoken_units("85-M SHAFT", DERINKUYU) == "280-FT SHAFT"
    # never a figure the narration does not give: one it gives in one unit only is left as written
    assert spoken_units("60 KM", RICHAT) == "60 KM"
    assert spoken_units("5M VISITORS", RICHAT) == "5M VISITORS"                       # not metres
    assert spoken_units("40 KM", "") == "40 KM"


def test_the_number_card_and_carousel_use_the_narration_unit(gen, tmp_path, monkeypatch):
    # Since 2026-10-29 the card sets the figure on fixed digit cells (a character at a time) and its unit beside it
    # as its own line: the planned lines are read, not the draw calls.
    seen = []
    real = graphics.plan_text
    monkeypatch.setattr(graphics, "plan_text", lambda im, xy, text, *a, **k: seen.append(text) or real(im, xy, text, *a, **k))
    monkeypatch.setattr(graphics, "_encode", lambda frames, out, n, width=1080, height=1920:
                        [f for _, f in zip(range(n), frames)] and out)
    graphics.render_stat_cards([{"value": "40+ km", "label": "Across the rings"}], tmp_path / "n.mp4", narration=RICHAT)
    assert "".join(seen).startswith("25+MILES") and "KM" not in " ".join(seen)
    drawn = _spy_text(monkeypatch)
    photo = _photo(tmp_path)
    gen._render_carousel_slide("immersive", photo, photo, {"heading": spoken_units("40+ km wide", RICHAT),
                                                           "body": "Rings of rock."}, 1, 7, {}, "", False)
    assert "25+ MILES WIDE" in drawn


# ------------------------------------------------------------------ 6. above the caption band's gap
LONG = [{"value": "1,200,000 TONS", "label": "Of volcanic stone removed from the hillside by hand over many centuries of "
                                            "patient work by generations of unnamed masons and labourers"},
        {"value": "94–104M YEARS AGO", "label": "Carbonatite rock cooling age measured across several independent "
                                                "laboratories and published surveys of the formation"}]


FITTING = [{"value": "1,200,000 TONS", "label": "Of volcanic stone removed by hand"},
           {"value": "94–104M YEARS AGO", "label": "Measured carbonatite rock cooling age"}]


@pytest.mark.parametrize("cards", [LONG, LONG[:1], LONG[1:]], ids=["two", "one", "second"])
def test_oversized_number_card_copy_is_refused_whole(cards, tmp_path, monkeypatch):
    monkeypatch.setattr(graphics, "_encode", lambda *_a, **_k: pytest.fail("Truncated facts must never be encoded"))
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        graphics.render_stat_cards(cards, tmp_path / "n.mp4")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("cards", [FITTING, FITTING[:1], FITTING[1:]], ids=["two", "one", "second"])
def test_a_number_cards_text_ends_above_the_caption_gap(cards, tmp_path, monkeypatch):
    bottom = safe_zones.graphics_box()[3]
    # QF-24 (decision QF-24/QF-87 layout, deliberate change): the organic graphics box ends at y 1090 (1370 less the
    # 240 px caption band and the 40 px gap; the ads box's foot was 1058)
    assert bottom == graphics.TEXT_BOTTOM == 1090
    boxes = []
    real = graphics.plan_text

    def spy(im, xy, text, *a, **k):  # each planned line and its box (2026-10-29: the card plans its lines first)
        plate = real(im, xy, text, *a, **k)
        if text and not plate.empty:
            boxes.append((text, plate.box))
        return plate
    monkeypatch.setattr(graphics, "plan_text", spy)
    monkeypatch.setattr(graphics, "_encode", lambda frames, out, n, width=1080, height=1920:
                        [f for _, f in zip(range(n), frames)] and out)
    graphics.render_stat_cards(cards, tmp_path / "n.mp4")
    assert boxes
    low = max(boxes, key=lambda b: b[1][3])
    assert low[1][3] <= bottom, low
    top = graphics.get_text_bounds(1080, 1920)[0]
    assert min(b[1][1] for b in boxes) >= top - 2
    # the two cards never overlap: the first card's lowest line ends above the second figure's top
    if len(cards) == 2:
        # the second card's figure is drawn a digit at a time; its first characters are the second card's first boxes
        first_count = sum(1 for t, _ in boxes if len(t) > 1 and t in cards[0]["label"].upper())
        first_lines = [b for t, b in boxes if len(t) > 1 and t in cards[0]["label"].upper()]
        # round 4: a range's dash is a drawn rule, not a planned character
        second_num = graphics.figure_parts(cards[1]["value"].upper())["num"].replace("\u2013", "").replace("-", "")
        start = next(i for i in range(len(boxes)) if "".join(t for t, _ in boxes[i:i + len(second_num)]) == second_num)
        second = [b for _, b in boxes[start:start + len(second_num)]]
        assert first_count and max(b[3] for b in first_lines) < min(b[1] for b in second)


def test_a_two_unit_card_counts_both_units_on_the_frame(tmp_path, monkeypatch):
    # Since 2026-10-29 the card draws "280 FT (85 M)" as the figure (digit by digit, on fixed cells), its unit, and
    # the bracketed figure under the label in the data face; both figures count on every frame.
    # Round 4 (2026-11-02): no count-up, so every string the render draws (ImageDraw.text, the lowest draw call,
    # across all its frames) that is a figure is one of the final figure's.
    # Round 4 review 4 (2026-11-02): read back from what the render drew and recorded, not from the literal (the
    # previous last line asserted on "280 FT (85 M)" itself): the last frame's measured plates name the same depth
    # in both units.
    drawn = _spy_text(monkeypatch)
    recorded = {}
    real_record = graphics.record_contrast

    def spy_record(path, frame, plates):
        recorded.update({name: pl.text for name, pl in plates if pl is not None and not pl.empty})
        return real_record(path, frame, plates)
    monkeypatch.setattr(graphics, "record_contrast", spy_record)
    monkeypatch.setattr(graphics, "_encode", lambda frames, out, n, width=1080, height=1920:
                        [f for _, f in zip(range(n), frames)] and out)
    graphics.render_stat_cards([{"value": "280 FT (85 M)", "label": "Depth"}], tmp_path / "n.mp4", narration=DERINKUYU)
    figures = [t for t in drawn if re.fullmatch(r"[\d,.]+( ?[A-Z]+)?", t)]
    assert figures and set(figures) <= {"2", "8", "0", "85 M"}, sorted(set(figures))
    assert {"2", "8", "0", "85 M"} <= set(figures)                   # every one of them drawn
    assert (recorded["figure"], recorded["unit"], recorded["secondary"]) == ("280", "FT", "85 M")
    ft, m = int(recorded["figure"]), int(re.match(r"\d+", recorded["secondary"]).group())
    assert abs(ft * 0.3048 - m) <= 1.5, (ft, m)                     # the two drawn units: the same depth
