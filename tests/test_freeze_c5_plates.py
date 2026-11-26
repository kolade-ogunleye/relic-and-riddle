"""Design-freeze cycle 5, stream PLATES (panel 6 gap ledger, classes PLATES-01, -02, -05, -06, -07, -08 and the Story
pointer): the corpus proofs' own measures (docs/reviews/freeze-system-2026-11-15/corpus/plates_common.py) on committed
and synthetic grounds, so a class cannot silently reopen (CI has no output/).

- PLATES-01: ONE band-edge limit, measured on the ground (band_edge.slope_limit): the field gate on a flat or blurred
  ground, the band-edge token on a sharp photograph, the floating ceiling for a band with the photograph on both
  sides; the myth panel and the hook plate take it.
- PLATES-02: a grade-out holds the ground's hue and saturation (band_edge.hold_hue), its luma the plain layer's.
- PLATES-07: a masthead the gate withholds is on the run's ledger, the open omission ledger and the still's record.
- PLATES-08: the AI badge never stays over a graphic's drawn elements.
- Story pointer (ruling 2026-11-26): no pointer line without a tap target; recorded; the block laid without it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from src import band_edge, brand, brand_system, masthead, multi_format, refusal, still_layout, video_composer
from src import design_cards as dc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-system-2026-11-15" / "corpus"))
import plates_common as pc  # noqa: E402  (the corpus proofs' measures)

STILL = ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "stills" / \
    "richat_structure_sahara_eye_01_commons_960.jpg"


def _noise(size, mean=170, spread=60, seed=7) -> Image.Image:
    rng = np.random.default_rng(seed)
    a = np.clip(rng.normal(mean, spread, (size[1], size[0], 1)).repeat(3, axis=2), 0, 255).astype(np.uint8)
    return Image.fromarray(a, "RGB")


def _photo(size) -> Image.Image:
    with Image.open(STILL) as im:
        return im.convert("RGB").resize(size, Image.LANCZOS)


GROUNDS = {
    "flat bright": lambda size: Image.new("RGB", size, (228, 222, 210)),
    "blurred photo": lambda size: _photo(size).filter(ImageFilter.GaussianBlur(36)),
    "sharp photo": _photo,
    "busy": _noise,
}


# ------------------------------------------------------------------------------------------------ PLATES-01

def test_the_limit_is_the_field_gate_on_a_flat_ground_and_the_token_on_a_busy_one():
    flat = band_edge.luma_rows(GROUNDS["flat bright"]((1080, 600)))
    busy = band_edge.luma_rows(GROUNDS["busy"]((1080, 600)))
    gate, top, floating = (brand_system.GATES["scrim.slope_max"], brand_system.tok("edge.slope_max"),
                           brand_system.tok("edge.slope_float"))
    assert band_edge.slope_limit(flat, 100, 400) == gate
    assert band_edge.slope_limit(flat, 100, 400, floating=True) == gate
    assert band_edge.slope_limit(busy, 100, 400) == top
    assert band_edge.slope_limit(busy, 100, 400, floating=True) == floating < top
    # between them the limit follows the ground's own detail
    soft = band_edge.luma_rows(_noise((1080, 600), spread=60).filter(ImageFilter.GaussianBlur(4)))
    assert gate < band_edge.slope_limit(soft, 100, 400) < top


def test_the_corpus_measure_states_the_same_limit_as_the_rule():
    """plates_common.texture is the rule written again (not imported): both give the same number."""
    for make in GROUNDS.values():
        lum = band_edge.luma_rows(make((1440, 900)))
        assert pc.texture(lum, 200, 700, 1440) == pytest.approx(band_edge.ground_texture(lum, 200, 699, 1440), rel=0.02)


@pytest.mark.parametrize("ground", sorted(GROUNDS))
def test_every_carousel_and_story_band_edge_is_within_its_grounds_limit(ground):
    car = GROUNDS[ground]((1440, 1800))
    out = multi_format.scrim(car, 980, 1800, feather=240, target=0.0, cap=1.0, floor=1.0, colour=brand.INK2)
    edges = pc.band_edges(car, out, brand.INK2, False)
    assert edges and all(e["ok"] for e in edges), edges
    tall = GROUNDS[ground]((1080, 1920))
    out = multi_format.scrim(tall, 700, 1100, feather=320, target=0.30)
    assert all(e["ok"] for e in pc.band_edges(tall, out, brand.INK, True))


@pytest.mark.parametrize("ground", sorted(GROUNDS))
def test_the_hook_plate_is_within_the_floating_limit_on_every_ground(ground):
    """Panel 6: the bright-ground plate drew a fixed 128 px feather (2.6 luma / px at its foot: a dark bar)."""
    tall = GROUNDS[ground]((1080, 1920))
    y0, y1, st, right = 715, 1055, 0.96, 901
    feathers, side = dc._plate_feathers(tall, y0, y1, st, dc.HOOK_PLATE_FEATHER, right)
    assert min(feathers) >= dc.HOOK_PLATE_FEATHER and side >= dc.HOOK_PLATE_SIDE
    comp = Image.alpha_composite(tall.convert("RGBA"), dc._plate_layer(tall.size, y0, y1, st, feathers, right, side))
    left = (0, 0, right, 1920)
    edges = pc.band_edges(tall.crop(left), comp.convert("RGB").crop(left), brand.INK, True, 1080)
    assert len(edges) == 2 and all(e["ok"] for e in edges), edges
    band = (0, y0, 1080, y1)
    across = pc.band_edges(tall.crop(band), comp.convert("RGB").crop(band), brand.INK, True, 1080, transpose=True)
    assert all(e["ok"] for e in across), across


def test_the_old_fixed_feather_fails_the_measure_on_a_bright_ground():
    """The measure sees the panel's case: the 128 px feather on a bright ground is over the floating limit."""
    tall = GROUNDS["flat bright"]((1080, 1920))
    comp = Image.alpha_composite(tall.convert("RGBA"), dc._plate_layer(tall.size, 715, 1055, 0.96, 128.0))
    edges = pc.band_edges(tall, comp.convert("RGB"), brand.INK, True)
    assert edges and not any(e["ok"] for e in edges)
    assert max(e["slope"] for e in edges) > 2.0


def test_the_myth_panel_edges_are_within_the_floating_limit(tmp_path):
    """Panel 6 CD r1: the 9:16 myth panel's foot ramped at up to 2.16 luma / px on its blurred ground (a slab)."""
    photo = tmp_path / "hero.jpg"
    _photo((1620, 2880)).save(photo, quality=92)
    caught = {}
    real = dc._photo_outside

    def spy(*a, **k):
        r = real(*a, **k)
        if r and (k.get("panel") is not None or (len(a) > 4 and a[4] is not None)):
            caught["parts"] = (r, k.get("panel") if "panel" in k else a[4])
        return r
    dc._photo_outside = spy
    try:
        dc._myth_ground(photo, dc._Geo(1080, 1920), 0.0, 1.0, dc.MYTH_PANEL[0], dc.MYTH_PANEL[1])
    finally:
        dc._photo_outside = real
    (outside, mask, feathers), panel = caught["parts"]
    edges = pc.band_edges(outside, Image.composite(outside, panel, mask), panel, True)
    assert edges and all(e["ok"] for e in edges), edges
    assert max(e["slope"] for e in edges) <= brand_system.tok("edge.slope_float") * 1.25 + 0.1
    assert min(feathers) > dc._Geo(1080, 1920).px(dc.MYTH_EDGE_MIN)


# ------------------------------------------------------------------------------------------------ PLATES-02

def _amber(size) -> Image.Image:
    """A saturated warm ground with detail (the Amber Room case's hue: about 36 degrees)."""
    rng = np.random.default_rng(3)
    v = np.clip(rng.normal(0.46, 0.06, (size[1], size[0], 1)), 0.36, 1.0)
    return Image.fromarray((v * np.array([[[232, 168, 70]]])).astype(np.uint8), "RGB")


@pytest.mark.parametrize("layer", ["wash", "plate"])
def test_the_hook_grade_out_holds_the_grounds_hue_and_saturation(layer):
    ground = _amber((1080, 1920))
    if layer == "wash":
        lay, full = dc._wash_layer(ground.size, 736, 1040, 0.70, 901), 0.70
    else:
        lay, full = dc._plate_layer(ground.size, 715, 1055, 0.96, (335.0, 335.0), 901, 360), 0.96
    alpha = np.asarray(lay.getchannel("A"), np.float32) / 255.0
    plain = Image.alpha_composite(ground.convert("RGBA"), lay).convert("RGB")
    held = Image.alpha_composite(ground.convert("RGBA"), band_edge.hold_hue(ground, lay, full)).convert("RGB")
    before, after = pc.hue_sat(ground, plain, alpha, full), pc.hue_sat(ground, held, alpha, full)
    assert before[2] < 0.85                                 # the plain INK layer loses saturation along the path
    # held: hue within 5 degrees, saturation >= 0.6 of the ground's (the ledger's check) and above the plain layer's
    assert after[1] <= 5.0 and after[2] >= max(0.6, before[2] + 0.08), (before, after)
    # the luma is the plain layer's (every slope and luma contrast measured on it stands), and under the block
    # (alpha at the layer's full strength) the colour is the layer's own, value for value
    assert np.abs(pc.luma(plain) - pc.luma(held)).max() <= 1.5
    under = alpha >= full - 1 / 255
    assert under.any()
    assert np.abs(np.asarray(plain, np.int16) - np.asarray(held, np.int16))[under].max() <= 1


def test_hold_hue_never_brightens_a_ground_darker_than_the_layer():
    ground = Image.new("RGB", (64, 64), (0, 0, 30))
    lay = Image.new("RGBA", (64, 64), brand.INK + (128,))
    held = np.asarray(band_edge.hold_hue(ground, lay, 1.0).convert("RGB"), np.int16)
    assert held.max() <= 30


# ------------------------------------------------------------------------------------------------ PLATES-05 / -06

def _floor_ground() -> Image.Image:
    """A bright busy ground on which the ruled order at the surface's own reach ends short of the target."""
    return masthead.validation_ground(222, 30.0, 1.0)


def test_a_masthead_short_of_the_target_takes_a_longer_reach_never_a_higher_peak():
    w, h, x0, base, size, reach, room = masthead.VALIDATION_FRAME
    ground = _floor_ground()
    _, _, at_reach = masthead.plan_at(ground, x0, base, size, reach=reach, max_width=room)
    _, _, rec = masthead.plan(ground, x0, base, size, reach=reach, max_width=room)
    target = brand_system.tok("contrast.masthead_target")
    if at_reach["shown"] and not at_reach["halo_exception"] and at_reach["contrast"] >= target:
        assert "margin_step" not in rec and rec["reach"] == reach     # nothing moves where the ruled order holds
    else:
        assert rec["shown"] and (rec.get("margin_step") or rec.get("margin_short") or rec["contrast"] >= masthead.MARGIN)
        assert rec["contrast"] >= (at_reach["contrast"] or 0) or not at_reach["shown"]
    assert rec["scrim_peak"] <= brand_system.tok("scrim.masthead_peak") + 1e-9
    assert rec["halo"] <= (brand_system.HALO_EXCEPTION_CAP if rec["halo_exception"] else brand_system.tok("halo.masthead_max"))
    if rec["shown"] and rec["contrast"] < masthead.MARGIN:
        assert rec["margin_short"] is True and "margin" in rec["margin_note"]


def test_the_ruled_order_is_untouched_where_it_reaches_the_target():
    w, h, x0, base, size, reach, room = masthead.VALIDATION_FRAME
    ground = masthead.validation_ground(60, 20.0, 1.5)
    a = masthead.plan_at(ground, x0, base, size, reach=reach, max_width=room)
    b = masthead.plan(ground, x0, base, size, reach=reach, max_width=room)
    assert "margin_step" not in b[2] and b[2]["contrast"] == a[2]["contrast"] >= brand_system.tok("contrast.masthead_target")
    assert np.array_equal(np.asarray(a[0]), np.asarray(b[0]))


def test_the_default_roles_need_no_palette_check_and_a_weak_palette_is_refused_when_configured():
    assert masthead.palette_problems(brand.DEFAULT_BRAND) == []
    assert masthead.palette_problems(brand.DEFAULT_BRAND.derive("same roles", system={"kicker.weight": 700})) == []
    weak = brand.DEFAULT_BRAND.derive("weak pair", palette={"BONE": (200, 200, 200), "INK": (62, 62, 62)})
    fails = masthead.palette_problems(weak)
    assert fails and all("masthead on a" in f and "gate" in f for f in fails)
    with pytest.raises(brand.BrandRefused):
        brand.configure(weak)
    assert brand.active() is not weak if hasattr(brand, "active") else True


# ------------------------------------------------------------------------------------------------ PLATES-07

class _Lay:
    def __init__(self, masthead):
        self.meta = {"masthead": masthead}


WITHHELD = {"shown": False, "contrast": 5.41, "reason": "masthead not drawn: its worst glyph reaches at best 5.41:1"}


def test_a_withheld_masthead_is_on_the_runs_ledger_and_the_open_omission_ledger():
    payload: dict = {}
    with refusal.collecting() as ledger:
        row = multi_format.record_masthead_withheld(payload, "carousel", 2, _Lay(dict(WITHHELD)))
        multi_format.record_masthead_withheld(payload, "carousel", 2, _Lay(dict(WITHHELD)))   # a second draw
    assert row["surface"] == "carousel slide 2" and row["omitted"] == "the masthead" and row["drawn"] is True
    assert "5.41" in row["reason"] and multi_format.MASTHEAD_RULE in row["reason"]
    assert [h["surface"] for h in payload["layout_holds"]] == ["carousel slide 2"]          # once on the run
    assert any(r["omitted"] == "the masthead" for r in ledger)
    # no ledger open: recorded on the run, never a refusal of the slide
    assert multi_format.record_masthead_withheld({}, "carousel", 3, _Lay(dict(WITHHELD)))["slide"] == 3


@pytest.mark.parametrize("record", [{"shown": True, "contrast": 6.5},
                                    {"shown": False, "fb_interior": True, "reason": "by rule"},
                                    {"shown": False, "by_rule": True}])
def test_a_drawn_masthead_or_one_left_off_by_rule_is_no_omission(record):
    payload: dict = {}
    with refusal.collecting() as ledger:
        assert multi_format.record_masthead_withheld(payload, "Facebook carousel", 4, _Lay(record)) is None
    assert not payload.get("layout_holds") and not ledger


def test_the_stills_record_keeps_the_masthead_and_pointer_decisions(tmp_path):
    lay = still_layout.Layout("carousel", (1440, 1800), (0, 0, 1440, 1800))
    lay.meta["masthead"] = dict(WITHHELD)
    lay.meta["pointer"] = {"shown": False, "by_rule": True}
    path = tmp_path / "carousel_slide_02.jpg"
    Image.new("RGB", (8, 8)).save(path)
    entry = still_layout.save(path, lay, {"renderer": "test"})
    assert entry["masthead"]["shown"] is False and entry["masthead"]["contrast"] == 5.41
    assert still_layout.load(path)["pointer"] == {"shown": False, "by_rule": True}
    assert still_layout.check(path) == []


# ------------------------------------------------------------------------------------------------ PLATES-08

@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_the_badge_yields_over_a_graphic_whose_element_is_in_its_corner(tmp_path, size):
    w, h = size
    box = video_composer.recon_badge_geometry(w, h)["box"]
    clip = tmp_path / "graphic_numbers.mp4"
    clip.write_bytes(b"")
    side = Path(str(clip) + video_composer.CAPTION_CONTRAST_SIDECAR)
    side.write_text(json.dumps({"elements": [{"name": "figure", "text": "36",
                                               "box": [box[0] + 20, box[3] - 6, box[0] + 400, box[3] + 300]}]}))
    scenes, durations = [tmp_path / "gen_01.png", clip, tmp_path / "02_commons.jpg"], [4.0, 5.0, 3.0]
    half = video_composer.TRANS_DUR / 2
    kept, yielded = video_composer.clear_badge_intervals([(0.0, 4.0 + half)], scenes, durations, w, h)
    assert kept == [(0.0, 4.0)]                                   # on for the illustration's own beat, off at the cut
    assert yielded[0]["clip"] == "graphic_numbers.mp4" and "'36'" in yielded[0]["reason"]
    assert yielded[0]["cut"] == [[4.0, round(4.0 + half, 3)]]
    # the same graphic with its elements clear of the corner: the badge stays through the crossfade
    side.write_text(json.dumps({"elements": [{"name": "figure", "text": "36",
                                               "box": [box[0], box[3] + 60, box[0] + 400, box[3] + 300]}]}))
    kept, yielded = video_composer.clear_badge_intervals([(0.0, 4.0 + half)], scenes, durations, w, h)
    assert kept == [(0.0, 4.0 + half)] and not yielded


def test_a_graphic_with_no_record_of_its_corner_counts_as_taken(tmp_path):
    clip = tmp_path / "graphic_map.mp4"
    clip.write_bytes(b"")
    kept, yielded = video_composer.clear_badge_intervals([(1.0, 6.0)], [clip, tmp_path / "gen_02.png"], [2.0, 4.0],
                                                         1080, 1920)
    assert kept == [(2.0, 6.0)] and "no element record" in yielded[0]["reason"]
    # its poster shows the corner: free when flat, taken when a bright figure stands there
    box = video_composer.recon_badge_geometry(1080, 1920)["box"]
    poster = Image.new("RGB", (1080, 1920), brand.INK)
    poster.save(clip.with_suffix(".jpg"))
    assert video_composer.clear_badge_intervals([(1.0, 6.0)], [clip], [2.0], 1080, 1920) == ([(1.0, 6.0)], [])
    poster.paste((242, 182, 48), (box[0] + 10, box[1] + 4, box[0] + 120, box[3] + 80))
    poster.save(clip.with_suffix(".jpg"))
    assert video_composer.clear_badge_intervals([(1.0, 6.0)], [clip], [2.0], 1080, 1920)[0] == [(2.0, 6.0)]


def test_the_badge_keeps_its_one_place_and_no_reconstruction_means_no_work():
    assert video_composer.clear_badge_intervals([], [Path("graphic_map.mp4")], [3.0], 1080, 1920) == ([], [])
    tall, wide = (video_composer.recon_badge_geometry(*s) for s in ((1080, 1920), (1920, 1080)))
    assert tall["box"][:2] == (video_composer._REEL_UI.box(1080, 1920)[0] + 19,
                               video_composer._REEL_UI.box(1080, 1920)[1] + 12)
    from src.graphics import WIDE_TITLE_SAFE
    assert wide["box"][:2] == (WIDE_TITLE_SAFE[0], WIDE_TITLE_SAFE[1])


# ------------------------------------------------------------------------------------------------ Story pointer

def _story_frame(tmp_path, i=0):
    photo = tmp_path / "p.jpg"
    _photo((1620, 2880)).save(photo, quality=90)
    slide = {"kicker": "ORBITAL MYSTERY", "line1": "THE DESERT EYE", "line2": "SEEN FROM SPACE",
             "body": "A colossal concentric formation marks the vast Mauritanian desert."}
    return multi_format.MultiFormatGenerator()._render_story_frame(photo, slide, i, 2, {})


def test_no_pointer_line_without_a_tap_target_and_the_frame_says_so(tmp_path):
    assert multi_format.story_tap_target() is False            # the Story API posts no link sticker today
    im, lay = _story_frame(tmp_path)
    names = [e["name"] for e in lay.elements]
    assert "reel_pointer" not in names and "title_1" in names
    rec = lay.meta["pointer"]
    assert rec["shown"] is False and rec["by_rule"] is True and multi_format.STORY_POINTER in rec["reason"]
    assert multi_format.STORY_POINTER_RULE in rec["reason"]
    assert not lay.problems()
    # the block is laid without the row: the first frame's block ends where a later frame's does (no hole kept)
    _, later = _story_frame(tmp_path, i=1)
    foot = max(e["box"][3] for e in lay.elements if e["name"] in ("body", "title_1", "title_2"))
    foot_later = max(e["box"][3] for e in later.elements if e["name"] in ("body", "title_1", "title_2"))
    assert lay.meta["text_box"][3] - foot == later.meta["text_box"][3] - foot_later
    assert "pointer" not in later.meta


def test_with_a_tap_target_the_pointer_is_drawn_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(multi_format, "STORY_TAP_TARGET", True)
    _, lay = _story_frame(tmp_path)
    assert "reel_pointer" in [e["name"] for e in lay.elements]
    assert lay.meta["pointer"] == {"shown": True, "text": multi_format.STORY_POINTER}


# ------------------------------------------------------------------------------------------------ MOTION-05

def test_the_wide_dim_takes_as_long_as_its_luma_drop_needs():
    """Panel 6 MO: on the brightest ground the 16:9 dim fell 111 mean luma in DIM_IN (0.45 s), 12.3-12.5 a frame at
    its steepest. A shallow dim keeps DIM_IN; a deep one takes the frames that hold its steepest frame at
    DIM_STEP_MAX, never past DIM_IN_MAX."""
    from src import end_card
    assert end_card.dim_seconds(30.0) == pytest.approx(round(end_card.DIM_IN * end_card.FPS) / end_card.FPS)
    for drop in (60.0, 111.0, 140.0):
        n = round(end_card.dim_seconds(drop) * end_card.FPS)
        steepest = max(end_card.ease_io((i + 1) / n) - end_card.ease_io(i / n) for i in range(n)) * drop
        assert steepest <= end_card.DIM_STEP_MAX + 1e-6 or n == round(end_card.DIM_IN_MAX * end_card.FPS)
    assert end_card.dim_seconds(111.0) > end_card.DIM_IN
    old = max(end_card.ease_io((i + 1) / 13.5) - end_card.ease_io(i / 13.5) for i in range(13)) * 111.0
    assert old > end_card.DIM_STEP_MAX                      # the measured case fails the rule at DIM_IN
    assert end_card.dim_seconds(500.0) == pytest.approx(end_card.DIM_IN_MAX)
