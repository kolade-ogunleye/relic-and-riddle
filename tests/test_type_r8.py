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
    with brand.use(_second(), validate=False):
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
    surfaces AT THEIR FLOORS is refused at configuration (BrandRefused, every failing line listed), never run by run.
    Lander ruling (round 8): the board's YouTube headlines are in the corpus at the 12 px feed floor; the second
    palette's Montserrat cannot set them there (5.8-9.8 px in round 8's first build), so it is refused."""
    assert brand_system.validate_display(brand.DEFAULT_BRAND) == []
    fails = brand_system.validate_display(_second())
    assert any("youtube: 'THE SECRET WEAPON'" in f for f in fails)
    with pytest.raises(brand.BrandRefused):
        brand.configure(_second())
    assert brand.active() is brand.DEFAULT_BRAND
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
    with brand.use(_second(), validate=False):
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
    SALT) wherever it holds the feed floor. Lane B cycle 1 (r8i AD final): where it does not, the authored whole lines
    are no longer drawn (CARVED FROM / ends on a never-end word): no valid form sets, a recorded refusal that the
    copy stage (cover_fit) sends to the copy edit."""
    from src import heading
    from src import youtube_thumbnail as yt
    try:
        _, _, _, rec = yt._lockup(_draw(), "CARVED FROM", "SOLID SALT", 904)
    except LayoutError:
        pass
    else:
        assert all(heading.end_ok(t.split()[-1]) for t in rec["drawn_rows"][:-1]), rec
    _, _, _, rec = yt._lockup(_draw(), "CARVED FROM", "SOLID SALT", 1400)
    assert (rec["line1"], rec["line2"]) == ("CARVED", "FROM SOLID SALT") and "form_diverges" not in rec


def test_a_headline_no_form_sets_is_a_recorded_refusal_never_drawn_past_its_column():
    """Round 7 drew the lockup's last resort at 96 px whatever its width; now a LayoutError (the one refusal type)."""
    from src import youtube_thumbnail as yt
    with pytest.raises(LayoutError):
        yt._lockup(_draw(), "", "SUPERCALIFRAGILISTICEXPIALIDOCIOUS", 904)


# ------------------------------------------------------------------ ruling 7: the Reel lockup's line 2
def test_the_reel_lockup_never_steps_under_the_grid_floor():
    """Build 7 1b / ruling 7 and the lander's floor ruling: in a wide face line 2 at line 1's size drew 7.0 px of cap at
    the 124 px tile (floor 9). The ratio is a minimum and both lines' ladder sizes are held at the grid floor's size;
    where the copy does not set there (the second palette's "830 SQUARE MILES", one bonded quantity) the lockup can
    only take its recorded off-ladder last resort, which the cover's grid proof then refuses (never delivered)."""
    from src import cover_design
    floor = cover_design.GRID_HOOK["title_2"]
    got, rec = cover_design._lockup(_draw(), {"line1": "OLD", "line2": "SALT"}, 900, 86)   # Anton's fallback tier
    for key in ("line1", "line2"):
        assert cap_height(got[key][0]) * cover_design.GRID_SCALE[0] >= floor
    assert got["line2"][0].size >= got["line1"][0].size
    with brand.use(_second(), validate=False):
        got, rec = cover_design._lockup(_draw(), {"line1": "BRICK DOME", "line2": "FLATTENED FOREST"}, 912, 86)
        assert not rec.get("off_ladder")
        for key in ("line1", "line2"):
            assert cap_height(got[key][0]) * cover_design.GRID_SCALE[0] >= floor
        _, rec = cover_design._lockup(_draw(), {"line1": "830 SQUARE MILES", "line2": "FLATTENED FOREST"}, 912, 86)
        assert rec.get("off_ladder")

# ------------------------------------------------------------------ ruling 6: one recorded refusal type
def test_every_refusal_a_renderer_raises_is_the_one_refusal_type():
    """Ruling 6: TextFitError, GlyphRefused, CardRefused and StoryCreditFitError are all refusal.LayoutError, so a
    caller that records LayoutError records every one (the cards' CardRefused was a bare ValueError)."""
    import ast
    import importlib
    from pathlib import Path

    from src.refusal import LayoutError
    root = Path(__file__).resolve().parents[1] / "src"
    found = []
    for mod in ("typography", "font_engine", "design_cards", "multi_format", "cover_design", "youtube_thumbnail",
                "graphics", "end_card", "then_now", "callouts", "masthead", "still_layout",
                "hero_window", "video_composer", "figures", "figure_gate", "cover_fit"):   # + the r8 lanes' modules
        tree = ast.parse((root / f"{mod}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and (node.name.endswith("Refused") or node.name.endswith("FitError")):
                found.append((mod, node.name))
                cls = getattr(importlib.import_module(f"src.{mod}"), node.name)
                assert issubclass(cls, LayoutError), f"{mod}.{node.name} is not a LayoutError"
    assert {("typography", "TextFitError"), ("font_engine", "GlyphRefused"), ("design_cards", "CardRefused")} <= set(found)


def test_the_cover_job_refuses_per_surface_and_records_it():
    """Panel MG-3: the deep dive's cover job evaluated the Reel cover, grid and YouTube thumbnail as one tuple, so a
    YouTube TextFitError discarded a drawn Reel cover and grid. Each surface is drawn on its own; a refusal is
    recorded on the payload (surface, class, code, reason) and the run stops with that reason."""
    import main
    from src.typography import TextFitError

    def youtube():
        raise TextFitError("FLATTENED FOREST", ["FLATTENED FOREST"], None, 904, 1)
    drawn = []
    results = main.cover_job((("cover", lambda: drawn.append("reel") or ("reel.jpg", "grid.jpg")),
                              ("YouTube thumbnail", youtube)))
    assert drawn == ["reel"] and results[0][1] == ("reel.jpg", "grid.jpg") and results[1][2] is not None
    payload: dict = {}
    # round 8 (lander ruling): the run is held, not stopped: the refused surface is None, its refusal on the card
    assert main.cover_job_results(results, payload) == [("reel.jpg", "grid.jpg"), None]
    assert payload["layout_holds"] == [{"surface": "YouTube thumbnail", "refused": "TextFitError",
                                        "code": "TextFitError", "reason": results[1][2].reason}]
    from src.telegram_bot import layout_hold_lines
    assert layout_hold_lines(payload["layout_holds"]) == [f"YouTube thumbnail: {results[1][2].reason}"]
    assert main.cover_job_results(main.cover_job((("a", lambda: 1), ("b", lambda: 2))), {}) == [1, 2]


def test_the_cover_copy_is_fit_checked_before_render_with_the_renderers_rule():
    """Lander ruling (round 8): a headline that cannot set at its floors is found at the copy stage by the renderers'
    own functions (src/cover_fit.py), a thumbnail-only copy problem for the copy edit, never a draft rewrite."""
    from src import cover_fit, script_engine
    bad = cover_fit.fit_problems({"line1": "BEHIND ONE WALL...", "line2": "A FORGOTTEN CITY"})
    assert bad and bad[0].startswith(cover_fit.COVER_FIT_PROBLEM + "YouTube thumbnail") and "feed floor" in bad[0]
    assert cover_fit.fit_problems({"line1": "ONE WALL...", "line2": "A LOST CITY"}) == []
    assert script_engine.copy_problem_fields(bad[0], None) == ("thumbnail",)
    assert script_engine.blocking_problems(bad) == []


# ------------------------------------------------------------------ ruling 4 on the cards: the lone "RINGS"
def test_no_one_word_middle_row_in_a_card_heading():
    """Ruling 4 (lander ruling, round 8: middle rows too): the 9:16 chapter act 4 set ARCHAEOLOGICAL / EVIDENCE / AND
    REFUTED CLAIMS and the 16:9 struck myth EXTRATERRESTRIALS / CONSTRUCTED / THE CIRCULAR / RINGS (the M2 long-word
    exception). The act sets with the engine's own last resort, a conjunction end (recorded); the stress myth has no
    legal form on the 16:9 column at its floors and is refused (recorded), never drawn with a lone row."""
    from src import design_cards as dc
    from tests import test_freeze_cards_2026_11_04 as fc
    plan = fc.chapter_plan(fc.TALL, 4, 5, fc.RICHAT_ACTS[3])
    assert plan.meta["anchors"]["title_lines"] == ["ARCHAEOLOGICAL", "EVIDENCE AND", "REFUTED CLAIMS"]
    assert plan.meta["anchors"]["conjunction_end_last_resort"] is True
    with pytest.raises(dc.CardRefused):
        fc.plan_myth(fc.LONG_ET, fc.WIDE)
    for m, size in ((fc.LONG_ET, fc.TALL), (fc.SHORT, fc.WIDE), (fc.LONGEST169, fc.WIDE)):
        p = fc.plan_myth(m, size)
        rows = [e.text for e in p.elements if e.name.startswith(("myth_", "fact_")) and e.text
                and e.text not in ("MYTH", "FACT")]
        assert heading.valid(rows[:2]) or len(rows) < 3
    assert dc._wrap("EXTRATERRESTRIALS CONSTRUCTED THE CIRCULAR RINGS", get_font("Anton", 88), 617, 4,
                    even=True) is None


# ------------------------------------------------------------------ lander ruling: nothing drawn under its floor
COPIES = ({"line1": "THE SECRET WEAPON"}, {"line1": "CARVED FROM", "line2": "SOLID SALT"},
          {"line2": "SAHARA EYE", "eyebrow": "DESERT ANOMALY"}, {"line1": "THE 6-TON ROOM"},
          {"line1": "A VERY LONG HEADLINE", "line2": "THAT RUNS ON AND ON"})


@pytest.mark.parametrize("palette", ["default", "second"])
def test_no_youtube_line_is_drawn_under_its_feed_floor(palette):
    """Every YouTube thumbnail either draws every line at or above its feed floor (HERO_FLOOR / LINE_FLOOR at 168 px
    wide: downscale_check records no cap failure) or is a recorded refusal (LayoutError). The second palette's
    headlines that drew 5.8-9.8 px now refuse; its figure unit ("SQ MI" 6.0 px against 7) holds its floor."""
    from src import youtube_thumbnail as yt
    photo = Image.new("RGB", (1920, 1080), (70, 64, 58))
    cfg = _second() if palette == "second" else brand.DEFAULT_BRAND
    drawn = refused = 0
    with brand.use(cfg, validate=False):
        for copy in COPIES:
            meta: dict = {}
            try:
                yt.render(photo, copy, meta=meta)
            except LayoutError:
                refused += 1
                continue
            drawn += 1
            assert not [f for f in meta["downscale_fails"] if "px cap" in f], (copy, meta["downscale_fails"])
        f_num, f_unit, _ = yt._figure_fonts(_draw(), "830", "SQ MI", 904)
        assert cap_height(f_unit) * yt.FEED_WIDTHS[0] / yt.W >= yt.LINE_FLOOR
        assert cap_height(f_num) * yt.FEED_WIDTHS[0] / yt.W >= yt.HERO_FLOOR
        assert yt._kicker_font("A KICKER FAR TOO WIDE FOR ANY COLUMN AT ALL", 600) is None
    assert drawn and (palette == "default" or refused)


def test_a_reel_cover_under_its_grid_floor_in_every_style_is_refused():
    """The profile grid's 9 px floor: a cover whose element stays under it in every fitting style is a recorded
    refusal (it was delivered with grid_hold on its record)."""
    from src import still_layout
    from src.visual_engine import VisualEngine
    fail = ["title_1 7.0 px cap at 124 px (floor 9)"]

    class Lay:
        meta = {"grid_proof": {"hook_fails": fail}}  # noqa: RUF012  (a stub)

        def problems(self):
            return []
    ve = VisualEngine.__new__(VisualEngine)
    ve._renderer = lambda style: (lambda im, copy, lay=None, tight=False: im)
    ve._render_checked = lambda im, copy, style: (im, Lay(), 1)
    ve._hero_fit = lambda: (None, None)
    ve._figure = None
    orig = still_layout.Layout
    try:
        still_layout.Layout = lambda *a, **k: Lay()
        with pytest.raises(LayoutError, match="grid floor|px cap at 124"):
            ve._grid_enforced(Image.new("RGB", (1080, 1920)), {"line1": "X"}, "badge", None, Lay(), 1)
    finally:
        still_layout.Layout = orig


def test_the_reel_lockups_last_resort_never_ships_under_the_grid_floor():
    """Round-8 integration (observed-size review item 4): the Reel lockup's last resort (cover_design._lockup:
    fit_text down to min(64, size), recorded off_ladder) can set under the grid floor's size; the 9:16 cover is
    always drawn with enforce=True (visual_engine.generate_thumbnails -> _grid_enforced), its grid proof flags every
    such line as a hook under its floor ("px cap at 124"), and a cover that stays under it in every fitting style is
    refused (LayoutError), never saved."""
    from src import cover_design, still_layout
    from src.brand_system import px_for_cap
    from src.visual_engine import VisualEngine
    photo = Image.new("RGBA", (1600, 1200), (120, 110, 95, 255))
    copy = {"eyebrow": "X", "line1": "830 SQUARE MILES", "line2": "FLATTENED FOREST", "tagline": "T"}
    with brand.use(_second(), validate=False):
        grid_px = px_for_cap(brand.DISPLAY_FONT, cover_design.GRID_HOOK["title_2"] / cover_design.GRID_SCALE[0])
        lay = still_layout.Layout("cover", (1080, 1920), (60, 420, 1020, 1500))
        cover_design.render_cover(photo.resize((1080, 1920)), copy, "minimal", lay=lay, source=photo)
        assert lay.meta["lockup"].get("off_ladder")
        under = [e["name"] for e in lay.elements if e["name"].startswith("title") and e["px"] < grid_px]
        assert under, "the last resort set no line under the grid floor's size: this test no longer reaches it"
        fails = lay.meta["grid_proof"]["hook_fails"]
        for name in under:
            assert any(f.startswith(f"{name} ") and "px cap at 124" in f for f in fails), (name, fails)
        ve = VisualEngine.__new__(VisualEngine)
        ve._renderer = lambda style: (lambda im, copy, lay=None, tight=False: im)
        ve._render_checked = lambda im, copy, style: (im, lay, 1)
        ve._hero_fit = lambda: (None, None)
        ve._figure = None
        orig = still_layout.Layout
        try:
            still_layout.Layout = lambda *a, **k: lay   # the tighter redraw and every other style: still under it
            with pytest.raises(LayoutError, match="px cap at 124"):
                ve._grid_enforced(Image.new("RGB", (1080, 1920)), copy, "minimal", None, lay, 1)
        finally:
            still_layout.Layout = orig
