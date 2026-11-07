"""Round-3 panel changes to the covers, the profile grid and the YouTube thumbnail (2026-11-02)."""

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from src import brand, cover_selector, youtube_thumbnail
from src.cover_design import (
    BRAND_TOP,
    figure_check,
    figure_qualifier,
    grid_proof,
    monument_figure,
    render_cover,
    withhold_figure,
)
from src.still_layout import Layout

SHEETS = Path(__file__).resolve().parents[1] / "data" / "fact_sheets"
RICHAT_SHEET = json.loads((SHEETS / "richat_structure_sahara_eye.json").read_text(encoding="utf-8"))
GUMBAZ_SHEET = json.loads((SHEETS / "gol_gumbaz_whispering_gallery.json").read_text(encoding="utf-8"))
# Round 4: the same sheet with fact-4's hedge carrying no lead phrase, so "EST." is drawn spelled out ("ESTIMATED",
# one kicker line). r7: the real sheet's "by one estimate" is drawn as ESTIMATED too, so the monument stands on both.
GUMBAZ_SHEET_NO_LEAD = json.loads(json.dumps(GUMBAZ_SHEET))
for _f in GUMBAZ_SHEET_NO_LEAD["facts"]:
    if _f.get("id") == "fact-4":
        _f["hedge"] = ""
RICHAT = {"eyebrow": "DESERT ANOMALY", "line1": "THE 25-MILE", "line2": "SAHARA EYE", "tagline": "CARVED BY NATURE"}
GUMBAZ = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
          "tagline": "BUILT WITHOUT RINGS", "badge": "EST. 144 FT (44 M)"}
DERINKUYU = {"eyebrow": "BURIED HISTORY", "line1": "BEHIND ONE WALL...", "line2": "A FORGOTTEN CITY",
             "tagline": "DERINKUYU, 1963"}


def _photo(colour=(120, 110, 95), size=(1600, 1200)) -> Image.Image:
    return Image.new("RGBA", size, colour + (255,))


def _cover(copy, style, figure=None, photo=None, focus=None, illustration=False):
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    photo = photo if photo is not None else _photo()
    out = render_cover(photo.resize((1080, 1920)), copy, style, lay=lay, source=photo, focus=focus, figure=figure,
                       illustration=illustration)
    return out.convert("RGB"), lay


def _names(lay):
    return {e["name"]: e for e in lay.elements}


def _drawn(lay):
    return " ".join(p.text for ps in lay.meta.get("plates", {}).values() for p in ps)


# --- the figure check against the fact sheet --------------------------------------------------------------------

def test_a_disputed_range_figure_is_a_conflict_and_never_a_monument_hero() -> None:
    fig = figure_check(RICHAT, RICHAT_SHEET)
    assert fig["conflict"] and not fig["monument_ok"] and fig["dimension"] == "DIAMETER"
    assert "monument" not in cover_selector.fitting(RICHAT, fig)
    assert "monument" in cover_selector.fitting(RICHAT)        # without a sheet: the old rule, unchanged


def test_an_estimate_stands_only_with_the_copys_qualifier_and_its_dimension_word() -> None:
    fig = figure_check(GUMBAZ, GUMBAZ_SHEET)
    assert fig["hedged"] and not fig["conflict"] and fig["qualifier"] == "EST." and fig["qualifier_from"] == "badge"
    assert fig["dimension"] == "EXTERNAL DIAMETER" and fig["drawn_qualifier"] == "ESTIMATED"
    assert fig["monument_ok"]                                     # r7 (operator default): one word, one line
    assert figure_check(GUMBAZ, GUMBAZ_SHEET_NO_LEAD)["monument_ok"]
    bare = {k: v for k, v in GUMBAZ.items() if k != "badge"}
    assert not figure_check(bare, GUMBAZ_SHEET)["monument_ok"]   # a hedged figure with no qualifier
    assert "monument" not in cover_selector.fitting(bare, figure_check(bare, GUMBAZ_SHEET))


def test_a_figure_missing_from_the_sheet_is_not_a_monument_hero() -> None:
    fig = figure_check({"line1": "THE 900-FOOT", "line2": "WHISPERING DOME"}, GUMBAZ_SHEET)
    assert fig and not fig["monument_ok"] and "not in the fact sheet" in fig["reason"]


def test_the_qualifier_is_read_from_the_figures_own_field_first() -> None:
    copy = {"line1": "UP TO 144-FOOT", "line2": "WHISPERING DOME", "badge": "EST. 144 FT"}
    assert figure_qualifier(copy, monument_figure(copy)) == ("UP TO", "line1")


def test_a_conflicted_figure_is_lifted_out_of_every_line_with_no_word_added() -> None:
    fig = figure_check(RICHAT, RICHAT_SHEET)
    out = withhold_figure(dict(RICHAT, badge="ABOUT 25 MILES ACROSS"), fig)
    assert out["line1"] == "" and "25" not in " ".join(out.values())
    words = set(" ".join(RICHAT.values()).split()) | {"ABOUT", "MILES", "ACROSS"}
    assert set(" ".join(out.values()).split()) <= words
    _, lay = _cover(RICHAT, "badge", fig)
    assert "25" not in _drawn(lay) and "SAHARA EYE" in _drawn(lay)
    assert lay.meta["figure_withheld"]["number"] == "25"


def test_monument_falls_back_to_discovery_when_the_sheet_will_not_let_the_figure_stand() -> None:
    fig = figure_check(RICHAT, RICHAT_SHEET)
    _, lay = _cover(RICHAT, "monument", fig)
    assert "monument" not in lay.meta and lay.meta["monument_declined"]
    assert "tabs" in lay.meta and "25" not in _drawn(lay)


# --- the monument hero -------------------------------------------------------------------------------------------

def test_monument_keeps_the_qualifier_attached_and_readable_in_the_grid() -> None:
    copy = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "UP TO 144-FOOT", "line2": "WHISPERING DOME"}
    _, lay = _cover(copy, "monument")
    names = _names(lay)
    assert {"figure_qualifier", "figure", "figure_unit"} <= set(names)
    assert names["figure_qualifier"]["box"][3] < names["figure"]["box"][1]      # directly above the figure
    titles = " ".join(p.text for n, ps in lay.meta["plates"].items() if n.startswith("title") for p in ps)
    assert "UP TO" not in titles                                                # not left behind as a title row
    proof = lay.meta["grid_proof"]["elements"]["figure_qualifier"]
    assert proof["cap_124"] >= 9 and proof["inside_34"]
    assert all(e["contrast"] >= 4.5 for e in lay.elements if e.get("contrast") is not None)
    assert lay.problems() == []


def test_monument_sets_the_claims_dimension_word_under_the_unit() -> None:
    fig = figure_check(GUMBAZ, GUMBAZ_SHEET_NO_LEAD)
    _, lay = _cover(GUMBAZ, "monument", fig)
    names = _names(lay)
    assert lay.meta["monument_figure"] == {"qualifier": "ESTIMATED", "qualifier_from": "badge",
                                           "dimension": "EXTERNAL DIAMETER", "copy_qualifier": "EST."}
    dim, unit, rule = names["figure_dim"], names["figure_unit"], names["dimension"]
    assert dim["box"][1] > unit["box"][3] and abs(dim["box"][0] - unit["box"][0]) <= 6   # unit over dimension
    assert rule["box"][2] >= dim["box"][2] and rule["box"][1] > names["figure"]["box"][3]
    assert lay.meta["monument"] == {"number": "144", "unit": "FT", "from": "line1"}
    assert lay.problems() == []


def test_monument_scrim_is_confined_to_the_type_stack() -> None:
    photo = _photo((180, 170, 150))
    out, lay = _cover({"line1": "THE 144-FOOT", "line2": "DOME"}, "monument", photo=photo)
    _, y0, x1, y1 = lay.meta["scrim"]["box"]
    assert x1 < 1080 - 120
    right = np.asarray(out.crop((1062, (y0 + y1) // 2, 1078, (y0 + y1) // 2 + 16))).mean(axis=(0, 1))
    assert np.max(np.abs(right - (180, 170, 150))) < 6               # beside the stack: the photo, ungraded
    above = np.asarray(out.crop((900, y0 - 200, 916, y0 - 184))).mean(axis=(0, 1))
    assert np.max(np.abs(above - (180, 170, 150))) < 6


# --- Discovery and the masthead ----------------------------------------------------------------------------------

def test_discovery_reviewed_photo_spans_the_full_width() -> None:
    photo = _photo((60, 90, 120), (1600, 900))
    ImageDraw.Draw(photo).rectangle((560, 300, 1040, 600), fill=(190, 120, 50, 255))
    focus = {"focus_box": [0.35, 0.33, 0.65, 0.67], "sha256": "x", "reviewed": "test"}
    out, lay = _cover(GUMBAZ, "badge", photo=photo, focus=focus)
    comp = lay.meta["composition"]
    assert comp["full_width"] and comp["image_box"][0] == 0 and comp["image_box"][2] == 1080
    x0, y0, x1, y1 = comp["protected_box"]
    assert 420 <= y0 < y1 <= 1500
    for e in lay.elements:
        assert e["name"] == "brand_mark" or not (e["box"][0] < x1 and x0 < e["box"][2]
                                                 and e["box"][1] < y1 and y0 < e["box"][3]), e
    edge = np.asarray(out.crop((2, (y0 + y1) // 2, 10, (y0 + y1) // 2 + 8))).mean(axis=(0, 1))
    assert np.max(np.abs(edge - (60, 90, 120))) < 10                 # photograph at the canvas edge, not ink


def test_masthead_over_a_bright_sky_uses_a_band_scrim_not_a_boxy_halo() -> None:
    sky = _photo((120, 170, 225))
    _, lay = _cover(GUMBAZ, "badge", photo=sky)
    brand_el = _names(lay)["brand"]
    assert lay.meta["mast_scrim"]["strength"] <= 0.75
    assert brand_el["halo"] <= 0.3 and brand_el["contrast"] >= 4.5 and brand_el["fill"] == list(brand.BONE)


def test_masthead_line_is_locked_on_every_selectable_style() -> None:
    tops, centres = set(), set()
    for style in ("badge", "monument", "depth_sandwich"):
        _, lay = _cover(GUMBAZ, style)
        b, m = _names(lay)["brand"]["box"], _names(lay)["brand_mark"]["box"]
        tops.add(b[1])
        centres.add(round((m[0] + b[2]) / 2))
        assert BRAND_TOP <= b[1] < BRAND_TOP + 20 and 240 <= b[1] and b[3] <= 1680   # inside the 3:4 crop too
    assert len(tops) == 1 and all(abs(c - 540) <= 2 for c in centres)


# --- the paper card ----------------------------------------------------------------------------------------------

def test_paper_card_sits_on_flat_ink_with_one_grid_and_a_gold_kicker_tab() -> None:
    out, lay = _cover(GUMBAZ, "depth_sandwich", photo=_photo((200, 60, 60), (1500, 1000)))
    for y in (120, 300, 1600, 1800):
        assert out.getpixel((540, y)) == brand.INK
    names = _names(lay)
    assert "rule" not in names
    tab = lay.meta["tabs"]["eyebrow"]
    assert out.getpixel((tab[0] + 4, tab[1] + 4)) == brand.GOLD and names["eyebrow"]["fill"] == list(brand.INK)
    x0, y0, x1, y1 = lay.meta["composition"]["image_box"]
    # A 3:2 photograph: half the card's height now (47% before), inside the card's one grid, x 96-984.
    ap = lay.meta["sandwich"]["aperture"]
    assert ap[0] == 96 and ap[2] == 984 and 96 <= x0 and x1 <= 984 and (y1 - y0) >= 0.5 * (1479 - 440)
    assert y1 == ap[3]                                               # foot of the window on the aperture's foot
    kicker_gap = names["title_1"]["box"][1] - tab[3]
    assert abs((tab[1] - y1) - 2 * kicker_gap) <= 24
    assert lay.meta["composition"]["mode"] == "contain" and lay.problems() == []


def test_paper_rule_looks_back_over_a_grid_column() -> None:
    assert cover_selector.RHYTHM == 3
    for seed in ("a", "b", "c", "d"):
        style, _ = cover_selector.choose(GUMBAZ, [], previous="badge", seed=seed,
                                         recent=["badge", "monument", "depth_sandwich"])
        assert style != "depth_sandwich"


# --- the profile-grid proof and the disclosure label ------------------------------------------------------------

def test_grid_proof_flags_a_date_carried_only_by_texture() -> None:
    out, lay = _cover(DERINKUYU, "badge")
    proof = lay.meta["grid_proof"]
    assert not proof["ok"] and any("1963" in f and "tagline" in f for f in proof["fails"])
    assert proof["elements"]["title_2"]["class"] == "hook" and proof["elements"]["tagline"]["class"] == "texture"
    assert 0 <= proof["mean_lstar"] <= 100
    _, ok = _cover(GUMBAZ, "badge")
    assert ok.meta["grid_proof"]["ok"], ok.meta["grid_proof"]["fails"]
    assert grid_proof(out, lay)["fails"] == proof["fails"]


def test_an_illustrated_hero_carries_the_existing_disclosure_label() -> None:
    from src.video_composer import RECON_BADGE_LABEL
    _, lay = _cover(GUMBAZ, "depth_sandwich", illustration=True)
    tag = lay.meta["provenance"]
    assert tag["label"] == RECON_BADGE_LABEL and tag["contrast"] >= 4.5
    x0, y0, _, y1 = tag["plate"]
    photo = lay.meta["composition"]["image_box"]
    assert 60 <= x0 and y1 <= 1480 and photo[1] <= y0 and y1 <= photo[3]    # on the window it labels
    assert lay.problems() == []
    _, plain = _cover(GUMBAZ, "depth_sandwich")
    assert "provenance" not in plain.meta


# --- the YouTube thumbnail ---------------------------------------------------------------------------------------

def test_thumbnail_never_draws_a_conflicted_figure() -> None:
    meta = {}
    _, plates = youtube_thumbnail.render(_photo(), RICHAT, meta=meta, figure=figure_check(RICHAT, RICHAT_SHEET))
    names = {n for n, _ in plates}
    assert "figure" not in names and not any("25" in p.text for _, p in plates)
    assert meta["figure_withheld"]["number"] == "25"


def test_thumbnail_figure_keeps_its_qualifier_and_dimension_outside_the_word_budget() -> None:
    meta = {}
    _, plates = youtube_thumbnail.render(_photo(), GUMBAZ, meta=meta, figure=figure_check(GUMBAZ, GUMBAZ_SHEET))
    texts = {n: [p.text for k, p in plates if k == n] for n, _ in plates}
    assert texts["figure_qualifier"] == ["ESTIMATED"]                  # r7: the sheet's single estimate, one word
    assert texts["figure"] == ["144"] and texts["figure_unit"] == ["FT"]
    assert " ".join(texts["figure_dim"]) == "EXTERNAL DIAMETER" and texts["title"] == ["WHISPERING DOME"]
    assert all(p.contrast >= 4.5 for _, p in plates)
    assert meta["downscale"]["figure_qualifier"]["cap_168"] >= youtube_thumbnail.LINE_FLOOR


def test_thumbnail_hedged_figure_without_a_qualifier_is_not_the_hero() -> None:
    bare = {k: v for k, v in GUMBAZ.items() if k != "badge"}
    meta = {}
    _, plates = youtube_thumbnail.render(_photo(), bare, meta=meta, figure=figure_check(bare, GUMBAZ_SHEET))
    assert "figure" not in {n for n, _ in plates} and meta["figure_declined"]


def test_thumbnail_subject_line_gives_way_when_it_repeats_the_video_title() -> None:
    meta = {}
    _, plates = youtube_thumbnail.render(_photo(), dict(GUMBAZ, tagline=""), meta=meta,
                                         youtube_title="The Whispering Dome of Gol Gumbaz")
    title = [p.text for n, p in plates if n == "title"]
    assert title == ["ANCIENT ACOUSTICS"] and meta["tension_from"] == "eyebrow"
    meta = {}
    youtube_thumbnail.render(_photo(), {"line1": "THE 144-FOOT", "line2": "WHISPERING DOME"}, meta=meta,
                             youtube_title="The Whispering Dome of Gol Gumbaz")
    assert meta["title_overlap"] is True


def test_thumbnail_is_measured_at_feed_size() -> None:
    meta = {}
    youtube_thumbnail.render(_photo(), {"line1": "BEHIND ONE WALL...", "line2": "A FORGOTTEN CITY"}, meta=meta)
    assert set(meta["downscale"]["title_1"]) >= {"cap_168", "cap_246", "contrast_168", "contrast_246"}
    assert not meta["downscale_ok"] and any("title_1" in f and "floor 12" in f for f in meta["downscale_fails"])
    meta = {}
    youtube_thumbnail.render(_photo(), {"line1": "THE 25-MILE", "line2": "SAHARA EYE"}, meta=meta)
    assert meta["downscale_ok"], meta["downscale_fails"]


def test_thumbnail_ink_feather_ends_short_of_the_subject() -> None:
    # r7 (operator default): the fade is a soft gradient from the type's edge, so it now ends by the subject's centre
    # (meta["focus_x"]) rather than at x 1040, which made the hard vertical edge; the type still ends before 1040.
    assert youtube_thumbnail.SHADE_END == 1040
    meta = {}
    im, plates = youtube_thumbnail.render(_photo((200, 170, 120)), {"line1": "THE 25-MILE", "line2": "SAHARA EYE"},
                                          meta=meta)
    x = meta["focus_x"]
    assert np.max(np.abs(np.asarray(im.crop((x, 700, x + 20, 720))).mean(axis=(0, 1)) - (200, 170, 120))) < 4
    assert max(p.box[2] for _, p in plates) <= youtube_thumbnail.SHADE_END


# --- the engine wiring -------------------------------------------------------------------------------------------

def test_engine_checks_the_figure_against_the_runs_sheet_and_keeps_it_in_the_recipe(tmp_path) -> None:
    from src import still_layout
    from src.visual_engine import VisualEngine, cover_figure
    sheet = tmp_path / "sheet.json"
    sheet.write_text(json.dumps(RICHAT_SHEET), encoding="utf-8")
    payload = {"thumbnail": dict(RICHAT), "fact_review": {"sheet_path": str(sheet)}, "title": "The Sahara Eye"}
    assert cover_figure(RICHAT, payload)["conflict"]
    hero = tmp_path / "hero.jpg"
    _photo().convert("RGB").save(hero)
    cover, _ = VisualEngine().generate_thumbnails(hero, payload, tmp_path / "out", style="monument", variants=False)
    record = still_layout.load(cover)
    assert record["recipe"]["figure"]["conflict"] and "grid_proof" in record["recipe"]
    assert not record["problems"]
    out = VisualEngine().generate_youtube_thumbnail(hero, payload, tmp_path / "yt.jpg")
    qa = json.loads(out.with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert qa["figure_withheld"]["number"] == "25" and "downscale_ok" in qa


# --- r5 review fixes (2026-11-02) --------------------------------------------------------------------------------

def test_qualifier_is_set_in_the_eyebrow_face_with_a_clear_gap_under_the_eyebrow() -> None:
    from src.cover_design import EYEBROW_QUALIFIER_GAP, QUALIFIER_FONT
    fig = figure_check(GUMBAZ, GUMBAZ_SHEET_NO_LEAD)
    _, lay = _cover(GUMBAZ, "monument", fig)
    q = lay.meta["plates"]["figure_qualifier"][0]
    assert QUALIFIER_FONT in Path(q.font.path).name and "Mono" not in Path(q.font.path).name
    # Kerned: the period sits close to the T (in a monospace cell it stood a full advance off).
    t_right = q.font.getbbox("EST", anchor="ls")[2]
    dot_left = q.font.getbbox("EST.", anchor="ls")[2] - (q.font.getbbox(".", anchor="ls")[2]
                                                         - q.font.getbbox(".", anchor="ls")[0])
    assert dot_left - t_right < 0.12 * q.font.size
    names = _names(lay)
    assert names["figure_qualifier"]["box"][1] - names["eyebrow"]["box"][3] >= EYEBROW_QUALIFIER_GAP - 4
    assert lay.problems() == []


def test_monument_scrim_has_no_visible_edge_over_a_bright_wall() -> None:
    photo = _photo((215, 210, 200))
    out, lay = _cover({"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME"},
                      "monument", photo=photo)
    _, y0, x1, y1 = lay.meta["scrim"]["box"]
    row = np.asarray(out.convert("L"), dtype=np.float32)[(y0 + y1) // 2 - 4:(y0 + y1) // 2 + 4, x1:1080].mean(axis=0)
    # Right of the stack: a Gaussian falloff, never a ramp with a kink (per 16 px: was 22 levels, kink 11).
    s16 = row[16:] - row[:-16]
    assert np.abs(s16).max() < 16 and np.abs(s16[16:] - s16[:-16]).max() < 6
    assert all(e["contrast"] >= 4.5 for e in lay.elements if e.get("contrast") is not None)


def test_grid_proof_counts_a_qualifier_only_when_it_is_attached_to_a_figure() -> None:
    copy = dict(GUMBAZ, tagline="LOOKING OVER THE CITY", eyebrow="SOME SAY ABOUT IT")
    _, lay = _cover(copy, "badge")
    assert lay.meta["grid_proof"]["ok"], lay.meta["grid_proof"]["fails"]
    _, lay = _cover(dict(GUMBAZ, tagline="OVER 400 YEARS OLD"), "badge")
    assert any("OVER" in f and "tagline" in f for f in lay.meta["grid_proof"]["fails"])


def test_thumbnail_qualifier_is_kerned_and_the_title_clears_the_dimension_word() -> None:
    meta = {}
    _, plates = youtube_thumbnail.render(_photo(), GUMBAZ, meta=meta, figure=figure_check(GUMBAZ, GUMBAZ_SHEET))
    by = {}
    for n, p in plates:
        by.setdefault(n, []).append(p)
    assert "Mono" not in Path(by["figure_qualifier"][0].font.path).name
    assert by["title"][0].box[1] - max(p.box[3] for p in by["figure_dim"]) >= youtube_thumbnail.DIM_TITLE_GAP - 8


def test_thumbnail_raises_a_lines_target_until_it_passes_the_feed_gate(monkeypatch) -> None:
    # A busy bright photo: the full-size plan alone measures under 4.5 at 246 px; the loop re-plans that line.
    rng = np.random.default_rng(5)
    noise = Image.fromarray(rng.integers(100, 230, (1080, 1920, 3), dtype=np.uint8))
    monkeypatch.setattr(youtube_thumbnail, "SHADE_ALPHA", 0.0)
    meta = {}
    youtube_thumbnail.render(noise, {"line1": "THE 25-MILE", "line2": "SAHARA EYE"}, meta=meta)
    assert meta["downscale_ok"], meta["downscale_fails"]
    assert meta.get("feed_targets"), "the first plan already passed: the test photo is not hard enough"
    assert all(t <= youtube_thumbnail.FEED_TARGET_MAX for t in meta["feed_targets"].values())


def test_a_gol_gumbaz_like_thumbnail_passes_its_feed_gate() -> None:
    # The r5 sample (title 4.43 at 246 px) in miniature: a bright sky and a sunlit dome behind the type's fade.
    photo = Image.new("RGB", (1600, 900), (120, 170, 225))                 # sky ...
    ImageDraw.Draw(photo).ellipse((500, 150, 1400, 1100), fill=(140, 135, 100))   # ... and a sunlit dome
    meta = {}
    youtube_thumbnail.render(photo, GUMBAZ, {"focus_box": [0.3, 0.1, 0.7, 0.7]}, meta=meta,
                             figure=figure_check(GUMBAZ, GUMBAZ_SHEET))
    assert meta["downscale_ok"], meta["downscale_fails"]
