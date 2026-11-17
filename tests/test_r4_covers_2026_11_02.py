"""Round-4 panel changes to the covers, the profile grid and the YouTube thumbnail (2026-11-02)."""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from src import brand, cover_design, cover_selector, youtube_thumbnail
from src.cover_design import (
    TAGLINE_TRACK,
    display,
    drawn_qualifier,
    figure_check,
    render_cover,
    resolving_tagline,
    sandwich_fill,
    year_event,
)
from src.still_layout import Layout

SHEETS = Path(__file__).resolve().parents[1] / "data" / "fact_sheets"
RICHAT_SHEET = json.loads((SHEETS / "richat_structure_sahara_eye.json").read_text(encoding="utf-8"))
GUMBAZ_SHEET = json.loads((SHEETS / "gol_gumbaz_whispering_gallery.json").read_text(encoding="utf-8"))
DERINKUYU_SHEET = json.loads((SHEETS / "derinkuyu_underground_city.json").read_text(encoding="utf-8"))
NO_LEAD = json.loads(json.dumps(GUMBAZ_SHEET))
for _f in NO_LEAD["facts"]:
    if _f["id"] == "fact-4":
        _f["hedge"] = ""
RICHAT = {"eyebrow": "DESERT ANOMALY", "line1": "THE 25-MILE", "line2": "SAHARA EYE", "tagline": "CARVED BY NATURE"}
GUMBAZ = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
          "tagline": "BUILT WITHOUT RINGS", "badge": "EST. 144 FT (44 M)"}
DERINKUYU = {"eyebrow": "BURIED HISTORY", "line1": "BEHIND ONE WALL...", "line2": "A FORGOTTEN CITY",
             "tagline": "DERINKUYU, 1963"}


def _photo(colour=(120, 110, 95), size=(1600, 1200)) -> Image.Image:
    return Image.new("RGBA", size, colour + (255,))


def _cover(copy, style, figure=None, photo=None, focus=None, reveal=None):
    lay = Layout("cover", (1080, 1920), (60, 420, 1020, 1500))
    photo = photo if photo is not None else _photo()
    out = render_cover(photo.resize((1080, 1920)), copy, style, lay=lay, source=photo, focus=focus, figure=figure,
                       reveal=reveal)
    return out.convert("RGB"), lay


def _plates(lay, name):
    return [p for p in lay.meta.get("plates", {}).get(name, []) if not p.empty]


def _texts(lay, name):
    return [p.text for p in _plates(lay, name)]


# --- the hedge, drawn in full and the same on every style ------------------------------------------------------

def test_est_on_a_dimension_is_drawn_in_the_sheets_or_the_copys_own_words() -> None:
    assert figure_check(GUMBAZ, GUMBAZ_SHEET)["drawn_qualifier"] == "ESTIMATED"   # r7: "by one estimate", one word
    assert figure_check(GUMBAZ, NO_LEAD)["drawn_qualifier"] == "ESTIMATED"             # the copy's word in full
    assert drawn_qualifier("EST.", "FT") == "ESTIMATED" and drawn_qualifier("ABOUT", "FT") == "ABOUT"
    assert drawn_qualifier("EST.", "") == "EST." and drawn_qualifier("C.", "") == "C."   # dates keep theirs
    assert drawn_qualifier("EST.", "M", lead_word="approximately") == "APPROXIMATELY"


def test_the_real_sheets_single_estimate_lets_the_monument_render_again() -> None:
    # r7 (operator default): "by one estimate" is drawn as ESTIMATED, which holds the grid floor on one line.
    fig = figure_check(GUMBAZ, GUMBAZ_SHEET)
    assert fig["monument_ok"] and fig["hedge_fits"] and "monument" in cover_selector.fitting(GUMBAZ, fig)
    _, lay = _cover(GUMBAZ, "monument", fig)
    assert "monument_declined" not in lay.meta and lay.meta["monument"]["number"] == "144"
    q = lay.meta["grid_proof"]["elements"]["figure_qualifier"]
    assert q["text"] == "ESTIMATED" and q["class"] == "hook" and q["cap_124"] >= cover_design.QUALIFIER_GRID_CAP


def test_a_hedge_that_cannot_hold_the_grid_floor_withholds_the_figure_on_every_style() -> None:
    copy = dict(GUMBAZ, badge="APPROX. 144 FT (44 M)")            # drawn in full: "APPROXIMATELY", too wide
    fig = figure_check(copy, NO_LEAD)
    assert fig["drawn_qualifier"] == "APPROXIMATELY" and not fig["hedge_fits"] and not fig["monument_ok"]
    assert cover_design.must_withhold(fig)
    for style in ("monument", "badge", "depth_sandwich"):
        _, lay = _cover(copy, style, fig)
        drawn = " ".join(p.text for ps in lay.meta["plates"].values() for p in ps if not p.empty)
        assert "144" not in drawn and "APPROXIMATELY" not in drawn, style       # a figure-free headline
        assert "WHISPERING DOME" in drawn and lay.meta["figure_withheld"]["number"] == "144"
    meta = {}
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), copy, meta=meta, figure=fig)
    assert not any("144" in p.text for _, p in plates) and meta["figure_withheld"]


def test_a_hedge_carried_only_by_texture_is_set_at_the_grid_floor() -> None:
    copy = {"eyebrow": "ESTIMATED 144 FT ACROSS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME"}
    fig = {"number": "144", "unit": "FT", "field": "line1", "qualifier": "EST.", "qualifier_from": "eyebrow",
           "drawn_qualifier": "ESTIMATED", "hedged": True, "conflict": False, "hedge_fits": True, "fact_id": "f"}
    assert cover_design.hedge_line(copy, fig) == ("ESTIMATED", "line1")


def test_every_style_sets_the_same_hedge_for_the_same_figure() -> None:
    fig = figure_check(GUMBAZ, NO_LEAD)
    _, mon = _cover(GUMBAZ, "monument", fig)
    assert _texts(mon, "figure_qualifier") == ["ESTIMATED"]
    for style in ("badge", "depth_sandwich"):
        _, lay = _cover(GUMBAZ, style, fig)
        assert _texts(lay, "qualifier") == ["ESTIMATED"], style
        q, t1 = _plates(lay, "qualifier")[0], _plates(lay, "title_1")[0]
        assert q.box[3] < t1.box[1] and t1.text == "THE 144-FOOT"      # on its own line, above the figure's line
        e = lay.meta["grid_proof"]["elements"]["qualifier"]
        # r7: the hedge is a hook at the grid floor (it was texture, ~3 px cap at 124 px), never shrunk to fit.
        assert lay.problems() == [] and e["class"] == "hook" and e["cap_124"] >= cover_design.QUALIFIER_GRID_CAP
        assert not lay.meta["grid_proof"]["hook_fails"], style
    meta = {}
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), GUMBAZ, meta=meta, figure=fig)
    assert [p.text for n, p in plates if n == "figure_qualifier"] == ["ESTIMATED"]


def test_a_flat_stated_undisputed_figure_stays_flat() -> None:
    sheet = json.loads(json.dumps(NO_LEAD))
    for f in sheet["facts"]:
        if f["id"] == "fact-4":
            f["disputed"] = False
    copy = {k: v for k, v in GUMBAZ.items() if k != "badge"}
    fig = figure_check(copy, sheet)
    assert not fig["hedged"]
    for style in ("badge", "depth_sandwich"):
        _, lay = _cover(copy, style, fig)
        assert "qualifier" not in lay.meta["plates"]


# --- the year hero ----------------------------------------------------------------------------------------------

def test_a_year_takes_the_sheets_own_event_word() -> None:
    assert year_event("1963", DERINKUYU_SHEET) == ("REDISCOVERED", "derinkuyu_discovery_1963")
    assert year_event("1999", DERINKUYU_SHEET) == ("", "")
    fig = figure_check(DERINKUYU, DERINKUYU_SHEET)
    assert fig["kind"] == "year" and fig["event"] == "REDISCOVERED" and not fig["monument_ok"]
    meta = {}
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), DERINKUYU, meta=meta, figure=fig)
    by = {n: p for n, p in plates}
    assert by["figure"].text == "1963" and meta["year_event"] == "REDISCOVERED"
    kicker = next(p for n, p in plates if p.text == "REDISCOVERED")
    assert kicker.box[3] < by["figure"].box[1]
    # r7: the event word is never texture: it holds the feed floor and the contrast gate (Anton when one word is
    # too wide for Montserrat), else the year is not the hero.
    row = meta["downscale"]["figure_qualifier"]
    assert row["cap_168"] >= youtube_thumbnail.LINE_FLOOR and meta["downscale_ok"], meta["downscale_fails"]


def test_a_year_whose_event_word_cannot_hold_the_feed_floor_is_no_hero(monkeypatch) -> None:
    fig = figure_check(DERINKUYU, DERINKUYU_SHEET)
    monkeypatch.setattr(youtube_thumbnail, "_qual_font", lambda text, width: None)
    meta = {}
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), DERINKUYU, meta=meta, figure=fig)
    assert "figure" not in {n for n, _ in plates} and "feed size" in meta["year_declined"]
    assert not any(p.text == "REDISCOVERED" or "1963" in p.text for _, p in plates)
    assert "qualifier" not in youtube_thumbnail._TEXTURE and "figure_qualifier" not in youtube_thumbnail._TEXTURE


def test_a_year_without_an_event_word_is_no_hero() -> None:
    sheet = {"facts": [{"id": "x", "statement": "The city lies under a modern town."}]}
    fig = figure_check(DERINKUYU, sheet)
    assert fig["kind"] == "year" and not fig["event"]
    _, lay = _cover(DERINKUYU, "monument", fig)
    assert lay.meta["monument_declined"] and "monument" not in lay.meta
    meta = {}
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), DERINKUYU, meta=meta, figure=fig)
    assert "figure" not in {n for n, _ in plates} and meta["year_declined"]
    # Without a sheet the old behaviour stands: the year is drawn.
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), DERINKUYU)
    assert any(n == "figure" and p.text == "1963" for n, p in plates)


# --- Discovery's foot, the deck, the typesetting ----------------------------------------------------------------

def test_badge_foot_shows_the_ambient_field_and_the_type_band_holds() -> None:
    photo = _photo((150, 140, 120))
    focus = {"focus_box": [0.3, 0.3, 0.7, 0.7], "sha256": "x", "reviewed": "test"}
    out, lay = _cover(GUMBAZ, "badge", photo=photo, focus=focus)
    foot = np.asarray(out.convert("L"), dtype=np.float32)[1600:1900].mean()
    ink = 0.2126 * brand.INK[0] + 0.7152 * brand.INK[1] + 0.0722 * brand.INK[2]
    assert foot > ink + 12                                   # the field shows faintly, not a dead black band
    plain = cover_design._shade(Image.new("RGBA", (1080, 1920), (150, 140, 120, 255)), 1000, 1920, 0.84, 100)
    assert foot > np.asarray(plain.convert("L"), dtype=np.float32)[1600:1900].mean() + 4
    assert all(e["contrast"] >= 4.5 for e in lay.elements if e.get("contrast") is not None)
    assert lay.problems() == []


def test_the_deck_is_tracked_on_every_style_and_its_comma_stays_on_the_baseline() -> None:
    for style in ("badge", "minimal", "depth_sandwich", "monument"):
        _, lay = _cover(DERINKUYU if style != "monument" else GUMBAZ, style)
        tag = _plates(lay, "tagline")
        assert tag and all(p.track >= 2 for p in tag), style
    _, lay = _cover(DERINKUYU, "badge")
    p = _plates(lay, "tagline")[0]
    assert p.text == "DERINKUYU, 1963" and p.track == TAGLINE_TRACK and p.anchor.endswith("s")


def test_drawn_strings_are_typeset() -> None:
    assert display("BEHIND ONE WALL...") == "BEHIND ONE WALL\u2026"
    assert display("94-104 MILLION YEARS") == "94\u2013104 MILLION YEARS" and display("THE 25-MILE") == "THE 25-MILE"
    assert display("EARTH'S EYE") == "EARTH\u2019S EYE"
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), {"line1": "EARTH'S EYE", "line2": "A RIDDLE"})
    assert any(p.text == "EARTH\u2019S EYE" for _, p in plates)


# --- the monument: gold on gold, the scrim ----------------------------------------------------------------------

def test_gold_on_gold_ground_is_calmed_behind_the_numeral_only() -> None:
    copy = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME"}
    out, lay = _cover(copy, "monument", photo=_photo((240, 175, 30)))     # sunlit sandstone
    assert lay.meta["gold_ground"]["applied"]
    assert all(e["contrast"] >= 4.5 for e in lay.elements if e.get("contrast") is not None)
    corner = np.asarray(out.crop((1040, 200, 1076, 236))).mean(axis=(0, 1))
    assert np.max(np.abs(corner - (240, 175, 30))) < 6                    # the photo elsewhere: untouched
    _, grey = _cover(copy, "monument", photo=_photo((120, 120, 120)))
    assert not grey.meta["gold_ground"]["applied"]


def test_monument_scrim_runs_one_deck_cap_past_the_last_line() -> None:
    _, lay = _cover(dict(GUMBAZ), "monument")
    foot = lay.meta["scrim"]["box"][3]
    last = max(p.box[3] for ps in lay.meta["plates"].values() for p in ps if not p.empty)
    assert foot >= last + 20


# --- the tagline that answers the mystery -----------------------------------------------------------------------

def test_a_resolving_tagline_is_dropped_only_with_the_scripts_reveal() -> None:
    assert resolving_tagline(RICHAT, "Wind and water carved it; nature made the eye.")
    assert resolving_tagline(RICHAT, None) == ""
    assert resolving_tagline(RICHAT, "") == "" and resolving_tagline(RICHAT, "   ") == ""   # r7: no reveal line
    # r7: the reveal must state the cause itself; "eroded" is not the tagline's own maker.
    assert resolving_tagline(RICHAT, "The eroded bullseye endures.") == ""
    assert resolving_tagline({"tagline": "FORGOTTEN BENEATH HOMES"}, "A city lay forgotten beneath ordinary homes.")
    assert resolving_tagline(DERINKUYU, "A city lay forgotten beneath ordinary homes.") == ""
    _, lay = _cover(RICHAT, "badge", reveal="Nature carved the eye over millions of years.")
    assert "tagline" not in lay.meta["plates"] and lay.meta["tagline_dropped"]["tagline"] == "CARVED BY NATURE"
    _, kept = _cover(RICHAT, "badge")
    assert _texts(kept, "tagline") == ["CARVED BY NATURE"]
    _, kept = _cover(RICHAT, "badge", reveal="")
    assert _texts(kept, "tagline") == ["CARVED BY NATURE"] and "tagline_dropped" not in kept.meta


def test_a_mystery_hook_the_reveal_does_not_answer_is_kept() -> None:
    for tag in ("GUARDED BY MONKS", "HAUNTED BY ECHOES"):
        copy = {"line1": "THE LOST", "line2": "LIBRARY", "tagline": tag}
        assert resolving_tagline(copy, "The gate is still guarded and the halls still haunted today.") == "", tag
    assert resolving_tagline({"tagline": "GUARDED BY MONKS"}, "For centuries the monks kept the gate.")


def test_the_engine_passes_no_reveal_when_the_payload_has_none() -> None:
    from src.visual_engine import reveal_line
    assert reveal_line({}) is None and reveal_line({"closing_sentence": "  "}) is None
    assert reveal_line({"closing_sentence": "It endures."}) == "It endures."


# --- the paper card ----------------------------------------------------------------------------------------------

def test_paper_card_fills_its_window_with_a_reviewed_photo() -> None:
    focus = {"focus_box": [0.3, 0.25, 0.7, 0.75], "sha256": "x", "reviewed": "test"}
    _, lay = _cover(GUMBAZ, "depth_sandwich", photo=_photo((200, 60, 60), (1500, 1000)), focus=focus)
    comp = lay.meta["composition"]
    ap = lay.meta["sandwich"]["aperture"]
    assert comp["mode"] == "reviewed_window" and comp["image_box"][0] == 96 and comp["image_box"][2] == 984
    x0, y0, x1, y1 = comp["protected_box"]
    ib = comp["image_box"]
    assert ib[0] <= x0 < x1 <= ib[2] and ib[1] <= y0 < y1 <= ib[3] and ib[3] == ap[3]
    assert lay.problems() == []
    _, plain = _cover(GUMBAZ, "depth_sandwich", photo=_photo((200, 60, 60), (1500, 1000)))
    assert plain.meta["composition"]["mode"] == "contain"       # unreviewed: the complete photograph


def test_paper_card_margin_is_even_around_the_masthead() -> None:
    _, lay = _cover(GUMBAZ, "depth_sandwich")
    card = lay.meta["sandwich"]["card"]
    # QF-87 (row rule: a profile-grid masthead is 9 px cap height at 124 px or absent): at BRAND_PX it is 5.2 px, so a cover draws none and lay.meta['masthead'] says why; the card keeps its top (was: the masthead 36 px under the card's top edge)
    assert not any(e["name"] == "brand" for e in lay.elements) and lay.meta["masthead"]["shown"] is False
    assert 420 <= card[1]
    deck = _plates(lay, "tagline")[0]
    assert card[3] - deck.box[3] >= 32


def test_a_small_reviewed_subject_takes_the_paper_card_out_while_another_style_fits() -> None:
    small = {"focus_box": [0.45, 0.45, 0.55, 0.52], "sha256": "x", "reviewed": "t"}
    big = {"focus_box": [0.1, 0.1, 0.9, 0.9], "sha256": "x", "reviewed": "t"}
    assert sandwich_fill(GUMBAZ, small, (1600, 1200)) < cover_selector.PAPER_MIN_FILL
    assert sandwich_fill(GUMBAZ, big, (1600, 1200)) >= cover_selector.PAPER_MIN_FILL
    assert "depth_sandwich" not in cover_selector.fitting(GUMBAZ, None, small, (1600, 1200))
    assert "depth_sandwich" in cover_selector.fitting(GUMBAZ, None, big, (1600, 1200))
    assert cover_selector.fitting(GUMBAZ) == cover_selector.fitting(GUMBAZ, None, None, None)
    style, _ = cover_selector.choose(GUMBAZ, [], previous="badge", seed="s", focus=small, source_size=(1600, 1200))
    assert style != "depth_sandwich"


# --- the engine: the fact sheet fails closed, the grid proof acts ----------------------------------------------

def test_an_unreadable_review_path_falls_back_to_the_topics_sheet(monkeypatch) -> None:
    from src import fact_sheet
    from src.visual_engine import cover_figure
    monkeypatch.setattr(fact_sheet, "load", lambda topic, max_age_days=0: RICHAT_SHEET if topic == "richat" else None)
    payload = {"fact_review": {"sheet_path": "C:\\\\nowhere\\\\richat.json"}, "topic_id": "richat"}
    fig = cover_figure(RICHAT, payload)
    assert fig["conflict"]
    meta = {}
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), RICHAT, meta=meta, figure=fig)
    assert not any("25" in p.text for _, p in plates)


def test_a_fact_checked_payload_without_a_sheet_lets_no_figure_be_the_hero(monkeypatch) -> None:
    from src import fact_sheet
    from src.visual_engine import cover_figure
    monkeypatch.setattr(fact_sheet, "load", lambda topic, max_age_days=0: None)
    fig = cover_figure(GUMBAZ, {"fact_review": {"sheet_path": "/nowhere.json"}, "topic_id": "x"})
    assert fig["sheet_missing"] and not fig["monument_ok"]
    assert "monument" not in cover_selector.fitting(GUMBAZ, fig)
    meta = {}
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), GUMBAZ, meta=meta, figure=fig)
    assert "figure" not in {n for n, _ in plates} and "did not load" in meta["figure_declined"]
    assert cover_figure(GUMBAZ, {"title": "no fact check"}) is None   # never claimed: the old rule


def test_a_hook_failure_at_grid_size_redraws_in_another_fitting_style(tmp_path, monkeypatch) -> None:
    from src import still_layout
    from src.visual_engine import VisualEngine
    real = cover_design.grid_proof

    def proof(im, lay):
        out = real(im, lay)
        if "monument" not in lay.meta:          # every style but the monument "fails" its hook check
            out["hook_fails"] = ["title_1 outside the 3:4 crop"]
            out["fails"] = out["fails"] + out["hook_fails"]
            out["ok"] = False
        return out
    monkeypatch.setattr(cover_design, "grid_proof", proof)
    hero = tmp_path / "hero.jpg"
    _photo().convert("RGB").save(hero)
    cover, _ = VisualEngine().generate_thumbnails(hero, {"thumbnail": dict(GUMBAZ), "title": "Dome"},
                                                  tmp_path / "out", style="badge", variants=False)
    recipe = still_layout.load(cover)["recipe"]
    assert recipe["style"] == "monument" and recipe["grid_fallback"]["from"] == "badge"


# --- the YouTube thumbnail ---------------------------------------------------------------------------------------

def test_a_withheld_figure_fills_the_headline_from_the_verified_eyebrow() -> None:
    meta = {}
    _, plates = youtube_thumbnail.render(_photo().convert("RGB"), RICHAT, meta=meta,
                                         figure=figure_check(RICHAT, RICHAT_SHEET))
    by = {n: p for n, p in plates}
    assert by["kicker"].text == "DESERT ANOMALY" and by["title_1"].text == "SAHARA EYE"
    assert by["kicker"].box[3] < by["title_1"].box[1] and by["kicker"].font.size < by["title_1"].font.size
    assert meta["headline_fill_from"] == "eyebrow" and meta["downscale_ok"], meta["downscale_fails"]


def test_the_ink_feather_follows_the_types_right_edge() -> None:
    meta = {}
    _, plates = youtube_thumbnail.render(_photo((230, 230, 230)).convert("RGB"),
                                          {"line1": "THE 25-MILE", "line2": "EYE"}, meta=meta)
    edge = meta["ink_edge"]
    assert edge == max(p.box[2] for _, p in plates)
    white = Image.new("RGB", (1920, 1080), (255, 255, 255))
    fitted = youtube_thumbnail._gradient(white, edge).convert("L")
    default = youtube_thumbnail._gradient(white).convert("L")
    assert fitted.getpixel((min(edge + 30, 1000), 900)) <= default.getpixel((min(edge + 30, 1000), 900)) + 2


def test_the_ink_fade_is_soft_and_the_type_sits_in_solid_ink() -> None:
    # r7: the round-4 ease (240 px, capped at x 1040) read as a hard vertical edge, and a long title ran into it.
    white = Image.new("RGB", (1920, 1080), (255, 255, 255))
    for edge, stop in ((905.0, 1340.0), (700.0, None), (1000.0, 1250.0)):
        row = np.asarray(youtube_thumbnail._gradient(white, edge, stop).convert("L"), dtype=np.float32)[540]
        slope = np.abs(row[20:] - row[:-20]) / 20                          # per px, over 20 px (8-bit steps)
        assert slope.max() <= youtube_thumbnail.FEATHER_SLOPE_MAX
        assert abs(row[int(edge)] - row[0]) <= 1                            # the type's edge: full ink
        end = edge + youtube_thumbnail.FEATHER_PAD + youtube_thumbnail.FEATHER_EASE
        if stop is not None:
            end = max(edge + youtube_thumbnail.FEATHER_PAD + youtube_thumbnail.FEATHER_MIN_EASE, min(end, stop))
        assert row[int(end) + 2] >= 254                                      # gone by the subject's centre
    meta = {}
    _, plates = youtube_thumbnail.render(_photo((200, 170, 120)).convert("RGB"),
                                          {"line1": "BEHIND ONE WALL", "line2": "A FORGOTTEN CITY"}, meta=meta)
    title_right = max(p.box[2] for n, p in plates if n.startswith("title"))
    assert meta["ink_edge"] >= title_right


# --- r7: the paper card's fill rule reaches the pipeline ----------------------------------------------------------

def test_the_engine_refits_the_paper_card_when_the_hero_fills_too_little(tmp_path, monkeypatch) -> None:
    from src import still_layout, visual_engine
    from src.visual_engine import VisualEngine
    small = {"focus_box": [0.45, 0.45, 0.55, 0.52], "sha256": "x", "reviewed": "t"}
    monkeypatch.setattr(cover_design, "reviewed_focus", lambda path, kept=None: small)
    import src.photo_hygiene as hygiene
    monkeypatch.setattr(hygiene, "clean_hero", lambda im, focus: (im, focus))
    seen = []
    real = cover_selector.fitting

    def spy(copy, figure=None, focus=None, source_size=None):
        seen.append((focus, source_size))
        return real(copy, figure, focus, source_size)
    monkeypatch.setattr(cover_selector, "fitting", spy)
    hero = tmp_path / "hero.jpg"
    _photo(size=(1600, 1200)).convert("RGB").save(hero)
    cover, _ = VisualEngine().generate_thumbnails(hero, {"thumbnail": dict(GUMBAZ), "title": "Dome"},
                                                  tmp_path / "out", style="depth_sandwich", variants=False)
    recipe = still_layout.load(cover)["recipe"]
    assert recipe["style"] != "depth_sandwich" and recipe["style_refit"]["from"] == "depth_sandwich"
    assert seen and seen[0] == (small, (1600, 1200))
    assert visual_engine.COVER_STYLES   # the module the engine reads


def test_the_grid_fallback_passes_the_heros_focus_to_fitting(tmp_path, monkeypatch) -> None:
    from src.visual_engine import VisualEngine
    small = {"focus_box": [0.45, 0.45, 0.55, 0.52], "sha256": "x", "reviewed": "t"}
    engine = VisualEngine()
    engine._hero_focus, engine._hero_source = small, _photo(size=(1600, 1200))
    engine._figure = None
    got = []
    monkeypatch.setattr(cover_selector, "fitting",
                        lambda copy, figure=None, focus=None, source_size=None: got.append((focus, source_size)) or [])
    lay = Layout("cover", (1080, 1920), (60, 420, 1020, 1500))
    lay.meta["grid_proof"] = {"hook_fails": ["title_1 outside the 3:4 crop"]}
    im = _photo().resize((1080, 1920))
    engine._grid_enforced(im, dict(GUMBAZ), "monument", im, lay, 1)
    assert got == [(small, (1600, 1200))]


# --- r7: grid variety --------------------------------------------------------------------------------------------

def test_a_measurable_figure_keeps_a_monument_tile_in_the_grid() -> None:
    # With the real sheets, every topic whose hero is a measurable figure the sheet lets stand keeps the monument
    # among its fitting styles: no style rule may leave the 12-tile grid without one.
    fig = figure_check(GUMBAZ, GUMBAZ_SHEET)
    assert "monument" in cover_selector.fitting(GUMBAZ, fig)
    rich = figure_check(RICHAT, RICHAT_SHEET)
    assert rich["conflict"] and "monument" not in cover_selector.fitting(RICHAT, rich)   # a range: rightly none
