"""Design-freeze cycle 6, stream MASTHEAD (panel 7: PL3 M1, PL1 M2, BC4 M3, TY/BC M4; round 2 by the lander's rulings
of 2026-11-27), at the level production calls each rule, so a class cannot silently reopen:

- M1: ONE hook-ratio rule (masthead.hook_ratio_max) on every hook surface that carries a masthead, measured on the
  surface's OWN drawn first title row. The remedy is the masthead's STACKED lockup form (the name on two lines at the
  same size; the break from the name by rule), never a smaller size; ONE form per post (a deck's covers decide for
  both copies and every slide); the YouTube thumbnail decides for itself. Over the token even stacked: drawn stacked,
  recorded at its ceiling. Interiors drawn alone are not hook surfaces.
- M2: slide 1's part of the swipe strip is drawn only outside the profile grid's 3:4 crop (the grid tile shows none of
  it, the feed still shows one line across the seam); shorter than swipe.part_min: none, and none run in on slide 2.
- M3: a configured palette's masthead pair is asked on the system's bright stress ground, in both forms.
- M4: the rule under the end card's wordmark is a token; the YouTube mark's feed-size capitals are recorded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand, brand_system, masthead, multi_format, safe_zones, still_layout, youtube_thumbnail
from src.multi_format import MultiFormatGenerator, _render_state

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15" / "corpus"
sys.path.insert(0, str(CORPUS))
M = MultiFormatGenerator
CW, CH = M.CAROUSEL_SIZE
CANVAS = brand_system.type_px("masthead", "carousel")
TOKEN = brand_system.tok("masthead.hook_ratio_max")


def _photo(path: Path, colour=(52, 58, 66), size=(1200, 1600)) -> Path:
    rng = np.random.default_rng(3)
    a = np.clip(np.asarray(colour, np.float32) + rng.normal(0, 14, (size[1], size[0], 3)), 0, 255).astype(np.uint8)
    Image.fromarray(a, "RGB").save(path, quality=95)
    return path


def _cover(tmp_path, heading_text: str, facebook: bool = False, form: str | None = None):
    photo = _photo(tmp_path / "cover.jpg")
    card = {"heading": heading_text, "body": "A concentric formation that has long fascinated observers from orbit."}
    prev = getattr(_render_state, "facebook", False), getattr(_render_state, "masthead_form", None)
    _render_state.facebook, _render_state.masthead_form = facebook, None
    try:
        return M()._render_carousel_slide("cover", photo, photo, card, 0, 6, {}, "", facebook, masthead_form=form)
    finally:
        _render_state.facebook, _render_state.masthead_form = prev


# ------------------------------------------------------------------------------------------------------------- M1

def test_the_stacked_lines_come_from_the_name_by_rule():
    assert masthead.stack_lines("RELIC & RIDDLE") == ("RELIC &", "RIDDLE")       # the most even break
    assert masthead.stack_lines("SECOND TEST BRAND") in (("SECOND", "TEST BRAND"), ("SECOND TEST", "BRAND"))
    a, b = masthead.stack_lines("SECOND TEST BRAND")
    others = [("SECOND", "TEST BRAND"), ("SECOND TEST", "BRAND")]
    widths = {o: abs(masthead.group_width(64, text=o[0]) - masthead.group_width(64, text=o[1])) for o in others}
    assert widths[(a, b)] == min(widths.values())
    assert masthead.stack_lines("RELICS") is None                                # one word: no stacked form
    assert masthead.form_width(CANVAS, "stacked") < masthead.form_width(CANVAS, "line")


@pytest.mark.parametrize("facebook", [False, True])
def test_a_cover_with_a_short_first_row_takes_the_stacked_form_at_the_same_size(tmp_path, facebook):
    """Panel 7 PL3 (Richat, 'THE EYE', 536 / 555 px = 0.97): the cover drawn alone decides from its own first row."""
    _, lay = _cover(tmp_path, "THE EYE OF THE SAHARA", facebook)
    m, rec = lay.meta["masthead"], lay.meta["hook_ratio"]
    row = masthead.ink_row(lay.plates["heading"])
    assert lay.meta["heading"]["lines"][0] == "THE EYE"
    assert m["shown"] and m["form"] == "stacked" and m["lines"] == list(masthead.stack_lines())
    assert abs(m["size_px"] - CANVAS) < 1e-6                                     # never a smaller size
    assert rec["form"] == "stacked" and rec["first_row_px"] == round(row, 1) and rec["holds"]
    assert rec["line_ratio"] > TOKEN and m["group_width"] / row <= TOKEN
    el = next(e for e in lay.elements if e["name"] == "header_brand")
    assert el["text"] == brand.MASTHEAD and el["lines"] == list(masthead.stack_lines())   # the text identical


def test_a_cover_whose_first_row_holds_the_ratio_keeps_the_one_line_form(tmp_path):
    _, lay = _cover(tmp_path, "THE UNFINISHED OBELISK")
    assert lay.meta["masthead"]["form"] == "line" and lay.meta["hook_ratio"]["holds"]


def test_a_first_row_even_the_stacked_form_cannot_hold_is_drawn_stacked_and_recorded_at_its_ceiling(tmp_path):
    _, lay = _cover(tmp_path, "EYE OF THE SAHARA")
    m, rec = lay.meta["masthead"], lay.meta["hook_ratio"]
    assert lay.meta["heading"]["lines"][0] == "EYE"
    assert m["form"] == "stacked" and rec["at_ceiling"] and not rec["holds"] and "narrowest form" in rec["reason"]


def test_a_one_word_name_has_no_stacked_form_and_is_recorded_at_its_ceiling(tmp_path):
    with brand.use(brand.DEFAULT_BRAND.derive("one word", text={"masthead": "RELICS"}), validate=False):
        _, lay = _cover(tmp_path, "EYE OF THE SAHARA")
        rec = lay.meta["hook_ratio"]
    assert lay.meta["masthead"]["form"] == "line" and rec["at_ceiling"] and "one word" in rec["reason"]


def test_interiors_drawn_alone_are_not_hook_surfaces(tmp_path):
    """Lander ruling (cycle 6 brief): the ratio binds on covers and thumbnails only."""
    photo = _photo(tmp_path / "i.jpg")
    card = {"heading": "ORBITAL", "body": "Visible from space, the concentric formation serves as a landmark."}
    _render_state.masthead_form = None
    _, lay = M()._render_carousel_slide("immersive", photo, photo, card, 1, 6, {}, "", False)
    assert "hook_ratio" not in lay.meta and lay.meta["masthead"]["form"] == "line"


SHORT_DECK = {"carousel_slides": [
    {"heading": "THE EYE OF THE SAHARA", "body": "A concentric formation that has long fascinated observers."},
    {"heading": "ORBITAL LANDMARK", "body": "Visible from space, the concentric formation serves as a landmark."},
    {"heading": "MAGMATIC UPLIFT", "body": "Subsurface igneous intrusion deformed the rock into rings."},
    {"heading": "YOUR TAKE", "body": "What do you think shaped it?"}], "title": "T", "narration_script": ""}


def test_one_form_per_post_every_slide_of_both_copies_takes_the_covers_form(tmp_path):
    """The deck's covers take the stacked form, so every slide of the Instagram and the Facebook copy draws it (the
    mark never changes form or size inside the post), and a redraw keeps it (the recipe's masthead_form)."""
    photos = [_photo(tmp_path / f"q{i}.jpg", c) for i, c in enumerate([(60, 70, 90), (80, 70, 60), (70, 80, 60)])]
    paths = []
    for fb in (False, True):
        paths += M().generate_carousel_sequence(photos * 2, json.loads(json.dumps(SHORT_DECK)), tmp_path / "run",
                                                facebook=fb)
    forms, sizes = set(), set()
    for p in paths:
        rec = still_layout.load(p)
        assert rec["recipe"]["masthead_form"] == "stacked"
        m = next((e["masthead"] for e in rec["elements"] if e["name"] == "header_brand"), None)
        if m is not None:                         # the Facebook interiors draw none by rule
            forms.add(m["form"])
            sizes.add(round(m["size_px"], 2))
    assert forms == {"stacked"} and sizes == {round(CANVAS, 2)}
    assert getattr(_render_state, "masthead_form", None) is None              # never left for another deck
    multi_format.redraw_carousel_slide(paths[2], still_layout.load(paths[2])["recipe"])
    again = still_layout.load(paths[2])
    assert next(e["masthead"] for e in again["elements"] if e["name"] == "header_brand")["form"] == "stacked"


def test_the_youtube_thumbnail_takes_the_stacked_form_where_its_own_first_row_needs_it():
    """Wieliczka on YouTube (panel 7: 483 / 613 px = 0.79): the thumbnail decides for itself, at its one size."""
    src = Image.new("RGB", (1920, 1080), (60, 66, 74))
    meta: dict = {}
    _, plates = youtube_thumbnail.render(src, {"line1": "THE SALT", "line2": "CATHEDRAL"}, meta=meta)
    rec = meta["hook_ratio"]
    brand_p = next(p for n, p in plates if n == "brand")
    assert rec["surface"] == "YouTube thumbnail" and rec["first_row_px"] == round(youtube_thumbnail.hook_row(plates), 1)
    assert rec["line_ratio"] > TOKEN and rec["form"] == "stacked" and rec["holds"]
    assert meta["masthead"]["size_px"] == youtube_thumbnail.mast_px()
    assert not any(youtube_thumbnail._intersects(p.box, brand_p.box) for n, p in plates if n != "brand")
    wide: dict = {}
    youtube_thumbnail.render(src, {"line1": "GIANT DESERT", "line2": "DRAWINGS"}, meta=wide)
    assert wide["hook_ratio"]["form"] == "line" and wide["hook_ratio"]["holds"]


def test_the_stacked_form_stands_on_the_one_line_forms_share_of_the_ramp_and_holds_the_stress_ground():
    """On the stored bright stress ground the default's stacked form reached 4.52:1 on the one-line reach (its second
    line lower on the ramp): its last baseline takes the one-line baseline's share of the ramp (matched reach)."""
    w, h, x0, base, size, reach, room = masthead.VALIDATION_FRAME
    ground = masthead.stress_ground(*masthead.BRIGHT_STRESS[1:])
    _, _, rec = masthead.plan(ground, x0, base, size, reach=reach, max_width=room, form="stacked")
    assert rec["form"] == "stacked" and rec["reach"] == round(reach * (base + masthead.stack_pitch(size)) / base)
    assert rec["shown"] and rec["contrast"] >= brand_system.tok("contrast.masthead_gate")


def test_a_slide_whose_stacked_mark_reaches_no_gate_draws_the_one_line_form_recorded(tmp_path, monkeypatch):
    real = masthead.draw

    def stacked_refused(*a, **k):
        if k.get("form") == "stacked":
            im, _, rec, _ = real(*a, **k)
            return im, None, dict(rec, shown=False, reason="probe: no gate"), None
        return real(*a, **k)
    monkeypatch.setattr(masthead, "draw", stacked_refused)
    _, lay = _cover(tmp_path, "THE EYE OF THE SAHARA", form="stacked")
    m = lay.meta["masthead"]
    assert m["shown"] and m["form"] == "line" and m["form_fallback"]["stacked"] == "probe: no gate"


def test_the_corpus_measure_on_committed_records():
    """The corpus proof's judge (masthead_01_hook_ratio.judge) on the tip's recorded covers and thumbnails
    (fixtures/masthead_c6_hook_records.json, from the dev corpus): every row holds or is recorded at its ceiling."""
    import masthead_01_hook_ratio as m01
    rows = json.loads((CORPUS / "fixtures" / "masthead_c6_hook_records.json").read_text(encoding="utf-8"))
    assert rows
    for row in rows:
        _, ok, why = m01.judge(row, TOKEN)
        assert ok, (row["run"], row["surface"], why)
    over = dict(rows[0], mast_px=600.0, row_px=500.0, record={})
    assert not m01.judge(over, TOKEN)[1]                                # the judge fails an unrecorded breach


# ------------------------------------------------------------------------------------------------------------- M2

def test_the_profile_grid_crop_and_slide_ones_part_outside_it():
    assert safe_zones.grid_crop(1440, 1800) == (45, 0, 1395, 1800)
    assert multi_format.swipe_part(1440, 1800) == (1395, 1440)
    assert multi_format.swipe_part(1080, 1440) is None                  # a 3:4 slide: the crop is the whole slide


def test_slide_one_draws_its_strip_only_outside_the_grid_crop():
    lay = still_layout.Layout("carousel", (CW, CH), (0, 0, CW, CH))
    im = Image.new("RGB", (CW, CH), (30, 30, 30))
    out = np.asarray(multi_format.swipe_strip(im, lay, {"side": "right", "y0": 924, "y1": 929}))
    assert lay.meta["swipe_strip"]["box"] == [1395, 924, 1440, 929]
    assert out[926, 1400:].max() > 30 and out[:, :1395].max() == 30      # the grid tile (x 45-1395) shows none of it


def test_a_part_shorter_than_the_token_is_not_drawn_and_slide_two_runs_none_in():
    with brand.use(brand.DEFAULT_BRAND.derive("long part", system={"swipe.part_min": 60}), validate=False):
        lay = still_layout.Layout("carousel", (CW, CH), (0, 0, CW, CH))
        out = multi_format.swipe_strip(Image.new("RGB", (CW, CH), (30, 30, 30)), lay,
                                       {"side": "right", "y0": 924, "y1": 929})
        assert "box" not in lay.meta["swipe_strip"] and np.asarray(out).max() == 30
        lay2 = still_layout.Layout("carousel", (CW, CH), (0, 0, CW, CH))
        lay2.meta["kicker_rule"] = [200, 924, 260, 929]
        multi_format.swipe_strip(Image.new("RGB", (CW, CH), (30, 30, 30)), lay2, {"side": "left"})
        assert "box" not in lay2.meta["swipe_strip"] and "part_min" in lay2.meta["swipe_strip"]["skipped"]


# ------------------------------------------------------------------------------------------------------------- M3

def test_the_palette_check_asks_both_lockup_forms(monkeypatch):
    """Round 2 (lander ruling 2026-11-27): the configure-time check plans the name in the one-line AND the stacked form
    on every stored ground (a stacked failure is listed as such)."""
    sys.path.insert(0, str(ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"))
    import render_surfaces
    asked, real = [], masthead.plan

    def spy(*a, **k):
        asked.append(k.get("form", "line"))
        return real(*a, **k)
    monkeypatch.setattr(masthead, "plan", spy)
    monkeypatch.setattr(masthead, "_palette_checked", {})
    masthead.palette_problems(render_surfaces.second_brand(brand, None))
    n = len(masthead.VALIDATION_GROUNDS) + 1
    assert asked.count("line") == n and asked.count("stacked") == n


def test_the_palette_check_refuses_a_pair_under_the_gate_and_accepts_one_over_it():
    """The RULE (lander ruling 2026-11-27, review M3: both shipped refusals sat 0.01 under the gate, a verdict a
    rasteriser can flip): probes far on each side of the 5.5:1 gate on the stored grounds. A grey-on-grey pair (2.4:1 on
    the stress ground) is refused at configure with its value; black and white (6.6:1) is accepted."""
    weak = brand.DEFAULT_BRAND.derive("weak pair", palette={"BONE": (200, 200, 200), "INK": (62, 62, 62)})
    strong = brand.DEFAULT_BRAND.derive("strong pair", palette={"BONE": (255, 255, 255), "INK": (0, 0, 0)})
    fails = masthead.palette_problems(weak)
    assert any("bright stress" in f and ":1" in f for f in fails)
    assert masthead.palette_problems(strong) == []


def test_the_palettes_readings_on_the_stress_ground_are_pinned_as_data():
    """The shipped default and the second colour set measured on the stored stress ground, each held to its dated pin
    (tests/fixtures/masthead_stress_readings.json) within its tolerance: DATA, not a refusal that hangs on 0.01. The
    default reads 0.12 over the gate; the second colours' one-line form 0.01 under it (refused on Windows), its
    stacked form at it. The assertion message carries every reading, so another platform's values are on record."""
    sys.path.insert(0, str(ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"))
    import render_surfaces
    pin = json.loads((ROOT / "tests" / "fixtures" / "masthead_stress_readings.json").read_text(encoding="utf-8"))
    cfgs = {"default": brand.DEFAULT_BRAND, "second-colours": render_surfaces.second_brand(brand, None)}
    w, h, x0, base, size, reach, room = masthead.VALIDATION_FRAME
    ground = masthead.stress_ground(*masthead.BRIGHT_STRESS[1:])
    got = {}
    for key in pin["readings"]:
        name, form = key.split("/")
        with brand.use(cfgs[name], validate=False):
            got[key] = masthead.plan(ground, x0, base, size, reach=reach, max_width=room, form=form)[2]["best_tried"]
    off = {k: (got[k], v) for k, v in pin["readings"].items() if abs(got[k] - v) > pin["tolerance"] + 1e-9}
    assert not off, f"readings {got} against the {pin['date']} pin (tolerance {pin['tolerance']}): {off}"
    assert got["default/line"] >= pin["gate"] and got["default/stacked"] >= pin["gate"], got   # the default draws


# ------------------------------------------------------------------------------------------------------------- M4

def test_the_wordmark_rule_is_the_token():
    from src import end_card
    lay = end_card.layout("Send this to a friend.")
    assert lay["rule"][1] - lay["rule"][0] == brand_system.tok("masthead.rule_len")
    wide = end_card.layout("Send this to a friend.", 1920, 1080)
    assert wide["rule"][1] - wide["rule"][0] == brand_system.tok("masthead.rule_len")
    assert end_card.RULE_HALF == brand_system.tok("masthead.rule_len") / 2
    with brand.use(brand.DEFAULT_BRAND.derive("rule probe", system={"masthead.rule_len": 300}), validate=False):
        for lay_ in (end_card.layout("Send this to a friend."), end_card.layout("Send this to a friend.", 1920, 1080)):
            assert lay_["rule"][1] - lay_["rule"][0] == 300


def test_the_youtube_marks_feed_capitals_are_recorded():
    rule = youtube_thumbnail.mast_size_rule()
    assert set(rule["cap_px_at_feed"]) == {"168", "246"} and 3.0 < rule["cap_px_at_feed"]["246"] < 6.0
