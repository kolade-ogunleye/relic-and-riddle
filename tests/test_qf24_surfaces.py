"""QF-24 build 1b: the surfaces the 2026-11-09 combined-layout decision did not assess, laid out on the approved
shared organic Reel rectangle (113, 288)-(848, 1370) at 1080 x 1920 (operator, 2026-11-13: keep the approved
rectangle, fix the surfaces; decisions QF-24/QF-87 layout).

(a) callouts: the 9:16 label box is the box edge to edge; the operator's reference door/wall pair keeps a layout
    (its labels stepped down together on a finer leader grid where the reviewed grid has none), and a conflict set
    (never drawn) reports plates LABEL_GAP apart where any attempt has them (the committed four-label set);
(b) end card: the group centred in the box (x 480), the masthead at cover scale inside it;
(c) then-and-now: the markers placed against the graphics box (foot 1090), not the Reel box with its caption band;
    the split keeps the reviewed y 768 (QF-24 review 1: the graphics box's middle, 689, set the NOW kicker on
    the old photo's subject), and the NOW block still ends above the graphics foot;
(d) myth, hook and chapter cards start at the box's left edge (x 113), so the reviewed long myth fits its floors,
    and the stress myth LONGEST916 is a declared refusal; the hook's centred (plate) position centres on the box's
    axis (x 480), its masthead inside the box (centred on the frame it ran to x 857);
(e) the timeline stress set five_close is a declared refusal (its best two-row break needs 697 px at the 30 px
    floor; the column holds 646; no three-row stack of its four drawn entries fits the column).
"""
from __future__ import annotations

import math
from itertools import combinations

import pytest

from src import callouts, end_card, graphics, then_now
from src import design_cards as dc
from tests import test_freeze_cards_2026_11_04 as fc
from tests.test_callouts_v4_2026_11_03 import REF_DOOR, REF_WALL

W, H = 1080, 1920
REEL_RECT = (113, 288, 848, 1370)
GRAPHICS_FOOT = 1090
POINTS_4 = [("stone door recess", 600, 900), ("carved tuff wall", 300, 1300), ("ventilation shaft", 1000, 420),
            ("rolling stone door", 70, 700)]


def test_callout_labels_run_to_the_organic_box_edges():
    x0, _y0, x1, y1 = callouts.text_box(W, H)
    assert (x0, x1) == (REEL_RECT[0], REEL_RECT[2]) and y1 == GRAPHICS_FOOT


def test_the_reference_pair_keeps_its_layout_inside_the_organic_box():
    clear = callouts.clear_of_captions([REF_DOOR, REF_WALL], W, H)
    kept = callouts.declutter(clear, W, H)
    assert kept is not None and [p[0] for p in kept] == ["stone door recess", "carved tuff wall"]
    labs = callouts.layout_labels(kept, W, H)
    assert not any(lab["conflict"] for lab in labs)
    sizes = {lab["font"].size for lab in labs}
    assert len(sizes) == 1 and min(sizes) >= math.ceil(0.8 * callouts.LABEL_PX["portrait"])   # one size, the floor
    for lab in labs:
        b = lab["box"]
        assert REEL_RECT[0] <= b[0] and b[2] <= REEL_RECT[2] and b[3] <= GRAPHICS_FOOT, (lab["lines"], b)
    for a, b in combinations(labs, 2):
        assert not callouts._overlaps(tuple(a["box"]), tuple(b["box"]), callouts.LABEL_GAP)


def test_a_conflict_set_never_draws_plates_closer_than_the_label_gap():
    labs = callouts.layout_labels(POINTS_4, W, H)
    for a, b in combinations(labs, 2):
        assert not callouts._overlaps(tuple(a["box"]), tuple(b["box"]), callouts.LABEL_GAP), (a["box"], b["box"])


def test_the_end_card_group_is_centred_in_the_box_at_cover_scale():
    lay = end_card.layout("Send this to the codebreaker you know.")
    m = lay["masthead"]
    left = lay["masthead_xy"][0] - 2 * m["mark_r"] - m["gap"]
    assert m["size"] >= 64 and REEL_RECT[0] <= left and left + m["width"] <= REEL_RECT[2]
    assert abs(left + m["width"] / 2 - 480) <= 2 and lay["centre_x"] == 480
    assert REEL_RECT[0] <= lay["box"][0] and lay["box"][2] <= REEL_RECT[2]


def test_then_now_is_placed_against_the_graphics_box():
    split = then_now._split_at(W, H)
    assert split == 768 and then_now._split_at(1920, 1080) == 960
    font = graphics.get_font(then_now.DATA_FONT, then_now.KICKER_PX["portrait"], weight=600)
    (_kx, ky), (_yx, yy), _ = then_now.marker_lines("now", 2018, W, H)
    year = graphics.get_font("Anton", then_now.YEAR_PX["portrait"])
    assert yy + year.getbbox("2018", anchor="lt")[3] <= GRAPHICS_FOOT
    assert ky + font.getbbox("NOW", anchor="lt")[3] <= GRAPHICS_FOOT
    assert then_now.marker_xy("then", W, H)[0] == REEL_RECT[0] + 18


@pytest.fixture
def signed(monkeypatch):
    from src import config
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "test-only-fact-record-key-0123456789abcdef")


def test_the_cards_start_at_the_box_edge_and_the_reviewed_long_myth_fits(signed):
    assert tuple(getattr(dc, k, None) for k in ("TALL_LEFT", "TALL_RIGHT", "TALL_FOOT")) == (113, 848, GRAPHICS_FOOT)
    plan = fc.plan_myth(fc.LONG_ET)
    a = plan.meta["anchors"]
    assert a["px"] >= dc.MYTH_FLOOR_SOURCE and a["myth_px"] >= 58 and plan.meta["source_omitted"] is None
    assert min(e.box[0] for e in plan.elements) >= REEL_RECT[0] - 2
    assert max(e.box[2] for e in plan.elements) <= REEL_RECT[2]
    assert max(e.box[3] for e in plan.elements) <= GRAPHICS_FOOT


def test_the_hook_plate_centres_on_the_box_axis_with_its_masthead_inside(signed, tmp_path):
    from tests import test_design_cards_2026_11_03 as T
    plan = fc.hook_plan(T._photo(tmp_path / "sky.jpg", "sky"), eyebrow="The Richat ring")
    assert plan.meta["anchors"]["position"] == "plate"
    e = {x.name: x for x in plan.elements}
    # QF-87 (lander ruling 2026-11-13: masthead >= 9 px at the 124 px tile or absent, the hook frame too): no masthead
    # at 5.2 px; the claim's lines centre on the box axis inside the box (was: the masthead centred on it)
    assert "brand" not in e and "brand_mark" not in e
    for x in plan.elements:
        assert REEL_RECT[0] <= x.box[0] and x.box[2] <= REEL_RECT[2], (x.name, x.box)
    titles = [x.box for n, x in e.items() if n.startswith("title_")]
    assert titles and all(abs((b[0] + b[2]) / 2 - 480) <= 3 for b in titles), titles


def test_the_stress_myth_longest916_is_a_declared_refusal(signed):
    with pytest.raises(dc.CardRefused):
        fc.plan_myth(fc.LONGEST916)


def test_the_five_close_stress_timeline_is_a_declared_refusal(tmp_path, monkeypatch):
    from tests.test_r4_graphics_2026_11_02 import FIVE_CLOSE
    monkeypatch.setattr(graphics, "VIDEO_FPS", 1)
    monkeypatch.setattr(graphics, "_encode", lambda *_a, **_k: pytest.fail("a refused timeline was encoded"))
    with pytest.raises(graphics.TextFitError, match="Checked copy does not fit the "):
        graphics.render_timeline(FIVE_CLOSE, tmp_path / "t.mp4")
