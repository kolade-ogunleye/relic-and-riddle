"""Design-freeze cycle 6, stream MASTHEAD (panel 7: PL3 M1, PL1 M2, BC4 M3, TY/BC M4), at the level production calls
each rule, so a class cannot silently reopen:

- M1: ONE hook-ratio rule (masthead.hook_ratio_max) on every hook surface that carries a masthead, measured on the
  surface's OWN drawn first title row: the carousel covers (both copies) draw the mark at its canvas size where the
  ratio holds, else at its fit ladder's floor (never under it); the title side (a legal re-break, the cover's size is
  locked; on YouTube the lockup's other forms on its ladder); else recorded at its ceiling. Interiors are not bound.
- M2: slide 1 draws no decorative part outside the profile grid's 3:4 crop (the seam strip's stub).
- M3: a configured palette's masthead pair is asked on the system's bright stress ground (refused with the value).
- M4: the rule under the end card's wordmark is a token; the YouTube mark's feed-size capitals are recorded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, heading, masthead, multi_format, safe_zones, still_layout, youtube_thumbnail
from src.multi_format import MultiFormatGenerator, _render_state
from src.typography import get_font

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15" / "corpus"
sys.path.insert(0, str(CORPUS))
M = MultiFormatGenerator
CW, CH = M.CAROUSEL_SIZE


def _photo(path: Path, colour=(52, 58, 66), size=(1200, 1600)) -> Path:
    rng = np.random.default_rng(3)
    a = np.clip(np.asarray(colour, np.float32) + rng.normal(0, 14, (size[1], size[0], 3)), 0, 255).astype(np.uint8)
    Image.fromarray(a, "RGB").save(path, quality=95)
    return path


def _cover(tmp_path, heading_text: str, facebook: bool = False):
    photo = _photo(tmp_path / "cover.jpg")
    card = {"heading": heading_text, "body": "A concentric formation that has long fascinated observers from orbit."}
    prev = getattr(_render_state, "facebook", False)
    _render_state.facebook = facebook
    try:
        return M()._render_carousel_slide("cover", photo, photo, card, 0, 6, {}, "", facebook)
    finally:
        _render_state.facebook = prev


# ------------------------------------------------------------------------------------------------------------- M1

@pytest.mark.parametrize("facebook", [False, True])
def test_a_cover_with_a_short_first_row_steps_its_mark_down_until_the_ratio_holds(tmp_path, facebook, monkeypatch):
    """Panel 7 PL3 (Richat, 'THE EYE', 536 / 555 px = 0.97 at a fixed 53.3 px): the cover's mark steps down its ladder
    (the platform re-encode guard held neutral here: it has its own test below)."""
    monkeypatch.setattr(masthead, "platform_contrast", lambda drawn: 7.0 if drawn[2].get("shown") else None)
    _, lay = _cover(tmp_path, "THE EYE OF THE SAHARA", facebook)
    m, rec = lay.meta["masthead"], lay.meta["hook_ratio"]
    canvas = brand_system.type_px("masthead", "carousel")
    floor = masthead.fit_ladder(canvas)[-1]
    assert lay.meta["heading"]["lines"][0] == "THE EYE"
    row = masthead.ink_row(lay.plates["heading"])
    ladder = masthead.fit_ladder(canvas)
    # the least step down that holds the ratio (never under the floor); the step above it does not hold
    assert m["shown"] and floor - 1e-6 <= m["size_px"] < canvas and m["size_px"] in ladder
    above = ladder[ladder.index(m["size_px"]) - 1]
    assert masthead.group_width(above) / row > brand_system.tok("masthead.hook_ratio_max")
    assert rec["first_row_px"] == round(row, 1) and rec["holds"] and not rec.get("at_ceiling")
    assert m["group_width"] / row <= brand_system.tok("masthead.hook_ratio_max")
    assert rec["surface"] == ("Facebook carousel cover" if facebook else "carousel cover")
    # the cap line stays on the header line (the mark is smaller, not lower)
    box = next(e["box"] for e in lay.elements if e["name"] == "header_brand")
    assert abs(box[1] - M._px(CW)(M()._header_y(facebook, "cover"))) <= 0.3 * m["size_px"]


def test_the_step_is_not_taken_where_it_reads_lower_after_the_platform_re_encode(tmp_path, monkeypatch):
    """Review of the first draft (2026-11-27): a smaller mark read 0.1-0.5 lower after the platform re-encode on 11
    of 16 stepped dev covers. Legibility outranks hierarchy: the canvas size stays, recorded at its ceiling."""
    real = masthead.platform_contrast
    monkeypatch.setattr(masthead, "platform_contrast",
                        lambda drawn: None if real(drawn) is None else (9.0 if drawn[2]["size_px"] > 50 else 5.0))
    _, lay = _cover(tmp_path, "THE EYE OF THE SAHARA")
    m, rec = lay.meta["masthead"], lay.meta["hook_ratio"]
    assert abs(m["size_px"] - brand_system.type_px("masthead", "carousel")) < 1e-6
    assert rec["at_ceiling"] and rec["size_kept"]["platform_contrast"] == [9.0, 5.0] and "re-encode" in rec["reason"]


def test_a_stronger_ground_treatment_is_named():
    assert masthead.treats_more({"light_lift": 0.22, "reach": 671}, {"light_lift": 0.12, "reach": 671}) ==         "a stronger light lift"
    assert masthead.treats_more({"reach": 671, "reach_matched": 900}, {"reach": 671}) == "a longer scrim"
    assert masthead.treats_more({"scrim_peak": 0.6, "reach": 500}, {"scrim_peak": 0.6, "reach": 500}) == ""


def test_a_cover_whose_first_row_holds_the_ratio_keeps_the_canvas_size(tmp_path):
    _, lay = _cover(tmp_path, "THE UNFINISHED OBELISK")
    m, rec = lay.meta["masthead"], lay.meta["hook_ratio"]
    assert abs(m["size_px"] - brand_system.type_px("masthead", "carousel")) < 1e-6
    assert rec["holds"] and rec["ratio"] <= brand_system.tok("masthead.hook_ratio_max")


def test_a_first_row_no_size_or_rebreak_can_hold_is_drawn_at_the_floor_and_recorded_at_its_ceiling(tmp_path):
    """LINES / IN THE SAND: no legal re-break with a longer first row at the locked cover size (display_xl, F2)."""
    _, lay = _cover(tmp_path, "LINES IN THE SAND")
    m, rec = lay.meta["masthead"], lay.meta["hook_ratio"]
    assert lay.meta["heading"]["lines"] == ["LINES", "IN THE SAND"]          # text and break as the engine set them
    assert m["shown"] and abs(m["size_px"] - masthead.fit_ladder(brand_system.type_px("masthead", "carousel"))[-1]) < 1e-6
    assert rec["at_ceiling"] and not rec["holds"] and "ceiling" in rec["reason"]
    assert rec["title_remedy"]["kind"] == "none" and "locked" in rec["title_remedy"]["reason"]


def test_interiors_are_not_hook_surfaces(tmp_path):
    """Lander ruling (cycle 6 brief): the ratio binds on covers and thumbnails only."""
    photo = _photo(tmp_path / "i.jpg")
    card = {"heading": "ORBITAL", "body": "Visible from space, the concentric formation serves as a landmark."}
    _, lay = M()._render_carousel_slide("immersive", photo, photo, card, 1, 6, {}, "", False)
    assert "hook_ratio" not in lay.meta
    assert abs(lay.meta["masthead"]["size_px"] - brand_system.type_px("masthead", "carousel")) < 1e-6


def test_the_title_side_rebreaks_to_a_longer_legal_first_row():
    """heading.longer_first_row: the same words, the same size, the same gold run, a longer first row, no rule broken
    (the engine's valid() and the one line policy)."""
    words = tuple("GIANT STONE RINGS OF THE SAHARA".split())
    form = heading.Form(words, (1,), 150, 0, 3, True)                    # GIANT / STONE RINGS OF THE SAHARA
    got = heading.longer_first_row(form, lambda t: 40.0 * len(t), 800, lambda t: 40.0 * len(t), 300)
    assert got is not None and got.lines == ("GIANT STONE RINGS", "OF THE SAHARA")
    assert got.size == form.size and got.seam == form.seam and got.record["hook_rebreak"]["from"] == list(form.lines)
    assert [r for line in got.runs() for r in line if r[1] in heading.GOLD_ROLES] == \
        [r for line in form.runs() for r in line if r[1] in heading.GOLD_ROLES]
    # never a re-break that ends a row on a never-end word (THE EYE OF / THE SAHARA)
    eye = heading.Form(tuple("THE EYE OF THE SAHARA".split()), (2,), 150, 0, 4, True)
    assert heading.longer_first_row(eye, lambda t: 40.0 * len(t), 700, lambda t: 40.0 * len(t), 400) is None


def test_the_cover_takes_the_rebreak_where_the_mark_at_its_floor_would_compete(tmp_path):
    px = M._px(CW)
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def measure(t, s):
        return d.textlength(t, font=get_font(brand.DISPLAY_FONT, s))
    words = tuple("GIANT STONE RINGS OF THE SAHARA".split())
    form = heading.Form(words, (1,), 150, 0, 3, True)
    _render_state.hook_remedy = None
    got = M()._hook_heading(form, px, measure, measure("GIANT STONE RINGS", 150) + 40)   # one line does not fit
    assert got.lines[0] == "GIANT STONE RINGS" and _render_state.hook_remedy["kind"] == "re-break"
    _render_state.hook_remedy = None


def _yt_measure():
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


def test_the_youtube_lockup_takes_a_form_with_a_longer_first_row_where_the_mark_would_compete():
    """Wieliczka on YouTube (panel 7: 483 / 613 px = 0.79): the kickerless lockup tries two lines at the larger size
    before one at the smaller; with the mark's least row given, the first form whose first row holds it is taken."""
    measure = _yt_measure()
    one_160 = measure.textlength("THE SALT CATHEDRAL", font=get_font(brand.DISPLAY_FONT, 160))
    one_190 = measure.textlength("THE SALT CATHEDRAL", font=get_font(brand.DISPLAY_FONT, 190))
    width = (one_160 + one_190) / 2
    _, _, _, plain = youtube_thumbnail._lockup(measure, "THE SALT", "CATHEDRAL", width, kickerless=True)
    assert plain["drawn_rows"] == ["THE SALT", "CATHEDRAL"] and "hook_remedy" not in plain
    need = masthead.group_width(youtube_thumbnail.mast_px(), youtube_thumbnail.LEFT) / brand_system.tok(
        "masthead.hook_ratio_max")
    _, _, _, rec = youtube_thumbnail._lockup(measure, "THE SALT", "CATHEDRAL", width, kickerless=True, min_row=need)
    assert rec["drawn_rows"] == ["THE SALT CATHEDRAL"] and rec["hook_remedy"]["kind"] == "title size"
    # no such form: the first form stands, recorded
    _, _, _, held = youtube_thumbnail._lockup(measure, "THE SALT", "CATHEDRAL", one_160 - 10, kickerless=True,
                                              min_row=need)
    assert held["drawn_rows"] == ["THE SALT", "CATHEDRAL"] and held["hook_remedy"]["kind"] == "none"


def test_the_youtube_thumbnail_records_its_own_first_row(tmp_path):
    src = Image.new("RGB", (1920, 1080), (60, 66, 74))
    meta: dict = {}
    _, plates = youtube_thumbnail.render(src, {"line1": "THE SALT", "line2": "CATHEDRAL"}, meta=meta)
    rec = meta["hook_ratio"]
    assert rec["surface"] == "YouTube thumbnail" and rec["first_row_px"] == round(youtube_thumbnail.hook_row(plates), 1)
    assert rec["masthead_px"] == meta["masthead"]["group_width"]
    assert rec["holds"] or rec["at_ceiling"]


def test_the_corpus_measure_on_committed_records():
    """The corpus proof's judge (masthead_01_hook_ratio.judge) on the tip's recorded covers and thumbnails
    (fixtures/masthead_c6_hook_records.json, from the dev corpus): every row holds or is recorded at its ceiling."""
    import masthead_01_hook_ratio as m01
    rows = json.loads((CORPUS / "fixtures" / "masthead_c6_hook_records.json").read_text(encoding="utf-8"))
    assert rows
    for row in rows:
        _, ok, why = m01.judge(row, brand_system.tok("masthead.hook_ratio_max"))
        assert ok, (row["run"], row["surface"], why)
    over = dict(rows[0], mast_px=600.0, row_px=500.0, record={})
    assert not m01.judge(over, 0.78)[1]                                 # the judge fails an unrecorded breach


# ------------------------------------------------------------------------------------------------------------- M2

def test_the_profile_grid_crop_of_the_carousel_cover():
    assert safe_zones.grid_crop(1440, 1800) == (45, 0, 1395, 1800)
    assert safe_zones.grid_crop(1080, 1440) == (0, 0, 1080, 1440)


def test_slide_one_draws_no_strip_part_the_grid_crop_would_cut():
    lay = still_layout.Layout("carousel", (CW, CH), (0, 0, CW, CH))
    im = Image.new("RGB", (CW, CH), (30, 30, 30))
    out = multi_format.swipe_strip(im, lay, {"side": "right", "y0": 924, "y1": 929})
    rec = lay.meta["swipe_strip"]
    assert "box" not in rec and "grid crop" in rec["skipped"] and np.asarray(out).max() == 30


# ------------------------------------------------------------------------------------------------------------- M3

def test_the_second_colour_set_is_refused_on_the_bright_stress_ground_and_the_default_is_not():
    """Panel 7 BC (BC7-R2-01): the second colours' masthead withheld at 5.41:1 on the board's bright Aswan cover; the
    four uniform grounds accepted the palette. The split stress ground refuses it at configure, with the value."""
    sys.path.insert(0, str(ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"))
    import render_surfaces
    cfg = render_surfaces.second_brand(brand, None)
    fails = masthead.palette_problems(cfg)
    assert fails and all("bright stress" in f for f in fails) and all(":1" in f for f in fails)
    with pytest.raises(brand.BrandRefused):   # (its map fills are refused first; palette_problems runs after them)
        brand.configure(cfg)
    w, h, x0, base, size, reach, room = masthead.VALIDATION_FRAME
    _, _, rec = masthead.plan(masthead.stress_ground(*masthead.BRIGHT_STRESS[1:]), x0, base, size, reach=reach,
                              max_width=room)
    assert rec["shown"] and rec["contrast"] >= brand_system.tok("contrast.masthead_gate")   # the default draws there
    with brand.use(cfg, validate=False):                                   # the board draws it unvalidated: no break
        assert brand.active() is cfg


# ------------------------------------------------------------------------------------------------------------- M4

def test_the_wordmark_rule_is_the_token():
    from src import end_card
    lay = end_card.layout("Send this to a friend.")
    assert lay["rule"][1] - lay["rule"][0] == brand_system.tok("masthead.rule_len")
    wide = end_card.layout("Send this to a friend.", 1920, 1080)
    assert wide["rule"][1] - wide["rule"][0] == brand_system.tok("masthead.rule_len")
    assert end_card.RULE_HALF == brand_system.tok("masthead.rule_len") / 2


def test_the_youtube_marks_feed_capitals_are_recorded():
    rule = youtube_thumbnail.mast_size_rule()
    assert set(rule["cap_px_at_feed"]) == {"168", "246"} and 3.0 < rule["cap_px_at_feed"]["246"] < 6.0
