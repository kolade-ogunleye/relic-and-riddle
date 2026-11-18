"""Cycle 0 round 8, lane TYPE (operator rulings 3-7 of 2026-11-17; official panel consensus 4 and 6).

Built from the panel's measured cases (queue/CYCLE0.panel-{TY,BC,AD}.md), as synthetic inputs: CI has no output/.
- ruling 6: display sizes and floors per typeface by cap height (type.display_cap, brand.DisplayFace), validated at
  configuration against the fit corpus (BrandRefused), one recorded refusal type (typography.LayoutError);
- ruling 7: the YouTube title's two-line tier (it was youtube_thumbnail.py:585, a continuous fit that refused
  "FLATTENED FOREST" where two lines fit), the Reel lockup's line 2 grown to the grid floor (the ratio a minimum);
- ruling 4: the phrase exception (IMPACT THEORY / DISPROVED), no row of function words only (OR, FROM), one form;
- ruling 5: the kickerless YouTube headline one size up.
"""
from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, heading
from src.typography import LayoutError, cap_height, get_font


def _second():
    """The board's second palette (render_surfaces.second_brand): Montserrat in the display role."""
    base = brand.DEFAULT_BRAND
    palette = {k: (v if isinstance(v, str) else (v[2], v[1], v[0])) for k, v in base.palette.items()}
    return base.derive("Second test brand", palette=palette, fonts={"display": "Montserrat"},
                       text={"masthead": "SECOND TEST BRAND", "follow": "FOLLOW FOR THE NEXT ONE",
                             "handoff": "The next one is right here."})


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


# ------------------------------------------------------------------ ruling 6: face-relative display type
def test_the_reference_face_resolves_to_itself_exactly():
    """The default brand's display fonts are exactly as before (scale 1.0, a plain family name, no _rr_px)."""
    assert brand_system.display_px_scale() == 1.0 and brand_system.display_cap_of() == 1.0
    assert type(brand.DISPLAY_FONT) is str and brand.DISPLAY_FONT == "Anton"
    f = get_font(brand.DISPLAY_FONT, 150)
    assert f.size == 150 and not hasattr(f, "_rr_px")


def test_a_wider_display_face_sets_the_reference_measure():
    """Panel BC-2 / AD 5: Montserrat px-for-px set 1.53x wider than Anton, so 35 surfaces of the second palette were
    refused. Face-relative: the brand's display face takes the cap at which it sets the reference ladder's measure,
    and the corpus lines the Story refused ("THE ACOUSTIC DOME", "THE DESERT EYE" at 106) now fit 860 px."""
    d = _draw()
    ref = {t: d.textlength(t, font=get_font("Anton", 106)) for t in ("THE ACOUSTIC DOME", "THE DESERT EYE")}
    with brand.use(_second()):
        face = brand.DISPLAY_FONT
        assert face == "Montserrat" and isinstance(face, brand.DisplayFace)
        assert 0.45 <= brand_system.display_cap_of() <= 0.6
        f = get_font(face, 106)
        assert f.size == 106 and f._rr_px < 106                # asked in reference units, drawn at the brand's px
        assert get_font("Montserrat", 106) is not f            # never the text role's cached font
        for t, w in ref.items():
            got = d.textlength(t, font=f)
            assert abs(got - w) / w < 0.06, (t, got, w)
            assert got <= 860


def test_a_brand_may_write_its_display_cap_and_is_validated_when_configured():
    """Ruling 6: the token is a brand token; a brand whose display face then cannot set the fit corpus on its
    surfaces is refused at configuration (BrandRefused, every failing line listed), never run by run."""
    assert brand_system.validate_display(brand.DEFAULT_BRAND) == []
    assert brand_system.validate_display(_second()) == []
    too_big = _second().derive("Montserrat at Anton's caps", system={"type.display_cap": 1.0})
    with pytest.raises(brand.BrandRefused) as e:
        brand.configure(too_big)
    assert any("THE ACOUSTIC DOME" in f for f in e.value.fails)
    assert brand.active() is brand.DEFAULT_BRAND
    with pytest.raises(ValueError, match="outside its range"):
        brand.DEFAULT_BRAND.derive("tiny", system={"type.display_cap": 0.2})


def test_the_fit_corpus_is_the_boards_titles_and_each_surface_has_a_spec():
    lines = brand_system.fit_corpus()
    assert {"THE ACOUSTIC DOME", "FLATTENED FOREST", "IMPACT THEORY DISPROVED"} <= {x["text"] for x in lines}
    assert {x["surface"] for x in lines} <= set(brand_system.FIT_SPECS)


def test_the_story_title_takes_ladder_steps_only():
    """Panel TY-A-6: the Story title drew 128 px, a size between ladder steps (150/120, 138/124, 112/96)."""
    from src import multi_format
    steps = brand_system.display_steps("tall", brand_system.GATES["heading.story_one_line_floor"])
    assert steps == (150, 138, 124, 120, 112)
    for words in ("THE DESERT EYE", "SEEN FROM SPACE", "THE ACOUSTIC DOME", "ANCIENT DOME"):
        px = multi_format.story_title_px([{"title": words, "line1": words, "line2": "", "kicker": "", "body": ""}])
        assert px in steps, (words, px)


# ------------------------------------------------------------------ ruling 4: phrase exception, function rows
def _m(t, s):
    return _draw().textlength(t, font=get_font("Anton", s))


def test_the_phrase_exception_sets_impact_theory_disproved():
    """TY-A-3: IMPACT / THEORY DISPROVED (0.40, a split compound noun) gives way to the ruled IMPACT THEORY /
    DISPROVED (0.69): a complete phrase whose break balance is >= 0.55 may keep a short last row."""
    form = heading.set_heading("IMPACT THEORY DISPROVED", _m, 1216, (184,))
    assert form.lines == ("IMPACT THEORY", "DISPROVED") and form.record["phrase_exception"] == "DISPROVED"
    assert form.record["balance"] >= 0.55


def test_a_noun_left_alone_is_still_a_lone_row():
    assert not heading.valid(["THE DESERT", "EYE"])
    assert heading.set_heading("THE SECRET WEAPON", _m, 904, (190,), max_lines=2) is None
    form = heading.set_heading("THE SECRET WEAPON", _m, 904, (190,), max_lines=2, lockup=True)
    assert form.lines == ("THE SECRET", "WEAPON")   # a lockup's payoff run is a phrase of its own


def test_a_row_of_function_words_only_is_never_a_row():
    """AD S2: the second palette's Story set SACRED PATHS / OR / ASTRONOMY (its field "OR ASTRONOMY" broken after
    OR); the YouTube two-line tier must never set CARVED / FROM."""
    assert not heading.valid(["OR", "ASTRONOMY"])
    assert not heading.valid(["CARVED", "FROM"])
    assert heading.set_field("OR ASTRONOMY", _m, 300, 150, max_lines=2) is None


# ------------------------------------------------------------------ rulings 4, 5, 7: the YouTube headline
def test_the_youtube_figure_title_has_a_two_line_tier():
    """AD MG-2 / BC: "FLATTENED FOREST" did not fit one line of 904 px at 90 px in the second palette (a TextFitError
    that aborted the Tunguska cover job). It now takes the two-line tier on the ladder, at its feed floor."""
    from src import youtube_thumbnail as yt
    with brand.use(_second()):
        lines, f, rec = yt._title_form(_draw(), "FLATTENED FOREST", 904, 600)
        assert lines == ["FLATTENED", "FOREST"] and rec["tier"] is not None
        assert cap_height(f) * yt.FEED_WIDTHS[0] / yt.W >= yt.LINE_FLOOR
    lines, f, _ = yt._title_form(_draw(), "FLATTENED FOREST", 904, 600)
    assert lines == ["FLATTENED FOREST"] and f.size in brand_system.display_steps("wide")
    with pytest.raises(LayoutError):
        yt._title_form(_draw(), "SUPERCALIFRAGILISTICEXPIALIDOCIOUSNESS", 904, 600)


def test_the_kickerless_headline_takes_the_larger_size_on_two_lines():
    """Ruling 5: Greek fire's THE SECRET WEAPON drew 120 px (9.0 px cap at the 168 px feed, under its 12 px floor)
    with the fill kicker dropped; kickerless, it takes the ladder's size on two lines."""
    from src import youtube_thumbnail as yt
    _, f, _, rec = yt._lockup(_draw(), "THE SECRET WEAPON", "", 904, kickerless=True)
    assert rec["sizes"] == [190, 190] and rec["rows"] == 2 and rec["kickerless"]
    assert cap_height(f) * yt.FEED_WIDTHS[0] / yt.W >= yt.HERO_FLOOR


def test_one_form_per_run_and_a_recorded_divergence():
    """Ruling 4 / panel BC-8: the YouTube lockup sets the run's ruled form (the Reel cover's CARVED / FROM SOLID
    SALT) wherever it holds the feed floor; Wieliczka's does not at Anton's widths, so the authored whole lines are
    drawn and the record says why (never a silent second form)."""
    from src import youtube_thumbnail as yt
    _, _, _, rec = yt._lockup(_draw(), "CARVED FROM", "SOLID SALT", 904)
    assert rec["form_diverges"]["ruled"] == ["CARVED", "FROM SOLID SALT"]
    _, _, _, rec = yt._lockup(_draw(), "CARVED FROM", "SOLID SALT", 1400)
    assert (rec["line1"], rec["line2"]) == ("CARVED", "FROM SOLID SALT") and "form_diverges" not in rec


def test_a_headline_no_form_sets_is_a_recorded_refusal_never_drawn_past_its_column():
    """Round 7 drew the lockup's last resort at 96 px whatever its width; now a LayoutError (the one refusal type)."""
    from src import youtube_thumbnail as yt
    with pytest.raises(LayoutError):
        yt._lockup(_draw(), "", "SUPERCALIFRAGILISTICEXPIALIDOCIOUS", 904)


# ------------------------------------------------------------------ ruling 7: the Reel lockup's line 2
def test_the_reel_lockup_line_2_grows_to_the_grid_floor():
    """Build 7 1b / ruling 7: in a wide face line 2 at line 1's size drew 7.0 px of cap at the 124 px tile (floor 9);
    the ratio is a minimum and line 2 grows until the tile clears its floor, where it sets (recorded)."""
    from src import cover_design
    with brand.use(_second()):
        got, rec = cover_design._lockup(_draw(), {"line1": "830 SQUARE MILES", "line2": "FLATTENED FOREST"}, 912, 86)
        cap2 = cap_height(got["line2"][0]) * cover_design.GRID_SCALE[0]
        assert rec.get("line2_grown") and cap2 >= cover_design.GRID_HOOK["title_2"]
        assert got["line2"][0].size >= got["line1"][0].size
    got, rec = cover_design._lockup(_draw(), {"line1": "OLD", "line2": "SALT"}, 900, 108)
    assert "line2_grown" not in rec and got["line2"][0].size == 108   # Anton at 108 already clears it
