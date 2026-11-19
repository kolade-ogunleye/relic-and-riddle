"""Freeze lane B, cycle 1: covers, grid tile and YouTube thumbnail rules (invariants, synthetic inputs).

- Operator ruling 7 (2026-11-16): a cover tagline emptied by a withheld figure falls back to another already-verified
  line of the run's copy (its carousel slide headings), never invented text; empty only if none fits.
- QF-143 review finding 3: the year that became the YouTube hero is never drawn again in the headline that falls back
  to the line it came from.
"""
from __future__ import annotations

import re

from PIL import Image

from src import cover_design
from src import youtube_thumbnail as yt

HEADINGS = [{"heading": "THE 327-METRE DESCENT"}, {"heading": "FOLLOW FOR MORE"}, {"heading": "SOLID SALT HALLS"},
            {"heading": "MASSIVE LABYRINTH"}, {"heading": "A"}, {"heading": "CARVED FROM"}]


def test_a_fallback_tagline_is_a_verified_run_line_that_stands_alone_and_adds_to_the_headline():
    copy = {"eyebrow": "SUBTERRANEAN MARVEL", "line1": "CARVED FROM", "line2": "SOLID SALT", "tagline": ""}
    alts = cover_design.tagline_alternates({"carousel_slides": HEADINGS})
    got = cover_design.fallback_tagline(copy, alts)
    assert got is not None
    source, text = got
    assert (source, text) in alts                      # a line of the run's copy, never invented
    assert not any(c.isdigit() for c in text)          # no figure (it would need its own check)
    assert not cover_design.withheld_fragment(text) and 2 <= len(text.split()) <= 4
    assert not cover_design.redundant_tagline(dict(copy, tagline=text))
    assert not re.search(r"\b(FOLLOW|SAVE|SHARE|YOUR)\b", text)
    # the fewest words shared with the headline: SOLID SALT HALLS repeats two, MASSIVE LABYRINTH none
    shown = {"CARVED", "FROM", "SOLID", "SALT", "SUBTERRANEAN", "MARVEL"}
    assert not set(text.split()) & shown


def test_no_fitting_line_leaves_the_tagline_empty():
    copy = {"eyebrow": "X", "line1": "CARVED FROM", "line2": "SOLID SALT", "tagline": ""}
    assert cover_design.fallback_tagline(copy, cover_design.tagline_alternates(
        {"carousel_slides": [{"heading": "A 300-FOOT DROP"}, {"heading": "SALT"}, {"heading": "SHARE THIS NOW"}]})) is None
    assert cover_design.fallback_tagline(copy, ()) is None


def test_the_cover_draws_the_fallback_only_where_the_withheld_figure_emptied_the_tagline():
    from src import still_layout
    im = Image.new("RGB", (1080, 1920), (70, 66, 60))
    copy = {"eyebrow": "SUBTERRANEAN MARVEL", "line1": "CARVED FROM", "line2": "SOLID SALT",
            "tagline": "327 METERS DOWN"}
    figure = {"number": "327", "unit": "M", "conflict": True, "reason": "a range"}
    alts = cover_design.tagline_alternates({"carousel_slides": HEADINGS})
    lay = still_layout.Layout("cover", (1080, 1920), (0, 0, 1080, 1920))
    cover_design.render_cover(im.copy(), copy, "badge", lay=lay, figure=figure, alternates=alts)
    fb = lay.meta.get("tagline_fallback")
    assert fb and fb["from"] and (fb["from"], fb["text"]) in alts
    assert any(e.get("text") == fb["text"] for e in lay.to_dict()["elements"] if e.get("name") == "tagline")
    # a tagline the figure did not empty is drawn as written, no fallback
    lay2 = still_layout.Layout("cover", (1080, 1920), (0, 0, 1080, 1920))
    cover_design.render_cover(im.copy(), dict(copy, tagline="NEVER FINISHED"), "badge", lay=lay2, figure=figure,
                              alternates=alts)
    assert "tagline_fallback" not in lay2.meta


def test_the_youtube_year_hero_is_not_drawn_again_in_its_lines_fallback():
    copy = {"eyebrow": "A VERY LONG EYEBROW LINE", "line1": "THE LOST UNDERGROUND CITY",
            "line2": "OF THE ANCIENTS LONG GONE", "tagline": "REDISCOVERED BY DIVERS 1963"}
    meta = {}
    _, plates = yt.render(Image.new("RGB", (1920, 1080), (96, 92, 88)), copy, meta=meta)
    texts = [p.text for n, p in plates if not p.empty and n != "brand"]
    assert sum(t.count("1963") for t in texts) == 1, texts


# ------------------------------------------------------------------ QF-142: a withheld figure in every form
def _no_figure_left(text, value):
    """No form of `value` is left in `text`: no digit run equal to it, no spelled-out number equal to it."""
    from src import figures
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    if any(abs(n - value) < 1e-9 for n in nums):
        return False
    words = re.findall(r"[A-Z]+", text.upper())
    for i in range(len(words)):
        for j in range(i + 1, min(len(words), i + 5) + 1):
            v = cover_design._spelled_value(words[i:j])
            if v is not None and abs(v - value) < 1e-9 and all(w.lower() in figures.NUMBER_WORDS or w in ("HUNDRED",)
                                                               for w in words[i:j]):
                return False
    return True


WITHHELD = [("144", "FT", ["THE MINE (144 FT, 9 LEVELS)", "THE MINE (9 LEVELS, 144 FT)", "130 TO 144 FT DEEP",
                           "144-150 FT DEEP SHAFT", "BETWEEN 130 AND 144 FT OF ROCK", "ONE HUNDRED FORTY-FOUR FEET DOWN",
                           "UP TO 144 FT TALL", "THE 144 FT (WOW) SHAFT"]),
            ("25", "MI", ["25 TO ALMOST 31 MI", "A RING 25 TO ALMOST 31 MI WIDE", "TWENTY-FIVE MILES ACROSS",
                          "THE RING SPANS TWENTY FIVE MILES", "ONE EYE, 25 MILES WIDE"])]


def test_a_withheld_figure_leaves_no_form_of_itself_in_any_field():
    for number, unit, lines in WITHHELD:
        fig = {"number": number, "unit": unit, "conflict": True}
        for line in lines:
            for key in ("eyebrow", "line1", "line2", "tagline", "badge"):
                out = cover_design.withhold_figure({key: line}, fig)[key]
                assert _no_figure_left(out, float(number)), (line, out)
                # what is left is tidy: no empty or opening-mark bracket, no stray separating mark, no dangling word
                assert not re.search(r"\(\s*[,;:]|[,;:]\s*\)|\(\s*\)", out), (line, out)
                assert not out or not cover_design.withheld_fragment(out), (line, out)


def test_other_numbers_and_number_words_stand():
    fig = {"number": "144", "unit": "FT", "conflict": True}
    for line in ("NO ONE KNOWS", "FIVE MILES DOWN", "THE MINE (9 LEVELS)", "THE 1,200-TON MONSTER"):
        assert cover_design.withhold_figure({"line1": line}, fig)["line1"] == line


def test_the_article_before_a_withheld_figure_stays_when_words_follow_it():
    fig = {"number": "144", "unit": "FT", "conflict": True}
    assert cover_design.withhold_figure({"line1": "THE 144 FT (WOW) SHAFT"}, fig)["line1"].startswith("THE ")
    assert cover_design.withhold_figure({"line1": "THE MINE (THE 144 FT)"}, fig)["line1"] == "THE MINE"
