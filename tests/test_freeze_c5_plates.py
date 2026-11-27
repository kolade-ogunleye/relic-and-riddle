"""Design-freeze cycle 5, stream PLATES (panel 6 gap ledger, classes PLATES-01, -02, -05, -06, -07, -08 and the Story
pointer): the corpus proofs' own measures (docs/reviews/freeze-system-2026-11-15/corpus/plates_common.py) on committed
and synthetic grounds, so a class cannot silently reopen (CI has no output/).

- PLATES-01: ONE band-edge limit, measured on the ground (band_edge.slope_limit): the field gate on a flat or blurred
  ground, the band-edge token on a sharp photograph, the floating ceiling for a band with the photograph on both
  sides; the myth panel and the hook plate take it.
- PLATES-01 cost (lander ruling 2026-11-26): no ramp past its designed reach; a panel past it keeps its crisp edge.
- PLATES-02: a grade-out holds the ground's hue and saturation (band_edge.hold_hue) to the ledger's bound (composite
  under 20 luma), its luma the plain layer's.
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

def test_a_feather_never_runs_past_the_designed_reach_and_a_panel_edge_goes_crisp_past_it():
    """Lander ruling 2026-11-26 (review of the c5 fix, D1): a ramp is never lengthened past twice the hook's designed
    128 px (a fixed 256 here, not the token: raising the token fails this), nor past a surface's own longer designed
    feather; where the rule's limit needs more (a flat bright ground) reach() says so and the panel keeps its crisp
    edge."""
    flat = band_edge.luma_rows(Image.new("RGB", (1080, 1920), (240, 236, 228)))
    length, met = band_edge.reach(flat, 900, "down", brand.INK, 0.96, 128, 1080, floating=True)
    assert length <= 256 and met is False
    assert band_edge.feather(flat, 900, "down", brand.INK, 0.96, 128, 1080, floating=True) <= 256
    # a surface whose designed feather is longer keeps exactly it: never lengthened
    assert band_edge.feather(flat, 900, "down", brand.INK, 0.96, 400, 1080, floating=True) == 400
    for name, make in GROUNDS.items():
        lum = band_edge.luma_rows(make((1080, 1920)))
        for side in ("up", "down"):
            assert band_edge.feather(lum, 960, side, brand.INK, 1.0, 24, 1080, floating=True) <= 256, (name, side)


def test_a_floating_edge_on_a_flat_ground_is_laid_at_the_field_gate_less_the_headroom():
    """Independent of band_edge's own arithmetic: on a flat ground a floating edge's feather is the smoothstep's
    peak (1.5) x the step over the field gate (0.5 luma / px) x the floating headroom (0.85), while that is inside the
    reach given; changing the headroom, the gate, the peak or the designed ceiling fails this."""
    import math
    lum = band_edge.luma_rows(Image.new("RGB", (1080, 1920), (72, 72, 72)))
    step = 72.0 - float(np.dot(np.asarray(brand.INK, np.float32), band_edge.LUMA))
    length, met = band_edge.reach(lum, 900, "down", brand.INK, 1.0, 24, 1080, floating=True, cap=256)
    assert met and length == math.ceil(1.5 * step / (0.5 * 0.85)), (length, step)
    # by default the designed form's own ramp is the ceiling (the token's 2.0 luma / px): here 1.5 x step / 2.0, so the
    # rule's longer ramp is not taken (a panel goes crisp; never more of the photograph darkened than designed)
    length, met = band_edge.reach(lum, 900, "down", brand.INK, 1.0, 24, 1080, floating=True)
    assert not met and length == math.ceil(1.5 * step / 2.0), (length, step)


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
    """Each soft band's edge is inside its ground's limit, or (lander ruling 2026-11-26) its ramp ran the whole
    designed reach and the rule wanted more (band_edge.reach: not met), the edge never lengthened past it."""
    car = GROUNDS[ground]((1440, 1800))
    out = multi_format.scrim(car, 980, 1800, feather=240, target=0.0, cap=1.0, floor=1.0, colour=brand.INK2)
    edges = pc.band_edges(car, out, brand.INK2, False)
    met = band_edge.reach(band_edge.luma_rows(car), 980, "up", brand.INK2, 1.0, 240, 1440)[1]
    assert edges and (all(e["ok"] for e in edges) or not met), edges
    tall = GROUNDS[ground]((1080, 1920))
    out = multi_format.scrim(tall, 700, 1100, feather=320, target=0.30)
    for e in pc.band_edges(tall, out, brand.INK, True):
        assert e["ok"] or e["rows"][1] - e["rows"][0] <= band_edge.reach_cap(320, 1080) + 8, e


@pytest.mark.parametrize("ground", sorted(GROUNDS))
def test_the_hook_plate_is_within_the_floating_limit_on_every_ground(ground):
    """Panel 6: the bright-ground plate drew a fixed 128 px feather (2.6 luma / px at its foot: a dark bar)."""
    tall = GROUNDS[ground]((1080, 1920))
    y0, y1, st, right = 715, 1055, 0.96, 901
    feathers, side = dc._plate_feathers(tall, y0, y1, st, dc.HOOK_PLATE_FEATHER, right)
    # lander ruling 2026-11-26: an edge is the rule's ramp, at least the designed 128 and at most 256 (2 x 128), or the
    # plate's crisp designed edge (0) where the rule wants more; the ease right of the block its designed 360
    assert all(v == 0 or dc.HOOK_PLATE_FEATHER <= v <= 256 for v in feathers) and side == dc.HOOK_PLATE_SIDE
    comp = Image.alpha_composite(tall.convert("RGBA"), dc._plate_layer(tall.size, y0, y1, st, feathers, right, side))
    left = (0, 0, right, 1920)
    edges = pc.band_edges(tall.crop(left), comp.convert("RGB").crop(left), brand.INK, True, 1080)
    assert len(edges) == sum(1 for v in feathers if v) and all(e["ok"] for e in edges), (edges, feathers)
    if ground == "flat bright":
        assert feathers == (0, 0)          # the panel's case: no ramp reaches the limit inside the reach: crisp


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
    (outside, mask, feathers, rec), panel = caught["parts"]
    edges = pc.band_edges(outside, Image.composite(outside, panel, mask), panel, True)
    # each edge is a ramp inside the floating limit at the rule's own value, or crisp and recorded (lander ruling
    # 2026-11-26: past the designed reach the panel keeps its crisp edge); never a ramp past 256 px
    crisp = rec.get("crisp") or []
    assert len(edges) + len(crisp) == 2 and all(e["ok"] for e in edges), (edges, rec)
    assert all(v == 0 or v <= 256 for v in feathers), feathers
    assert [s_ for s_, v in zip(("up", "down"), feathers) if not v] == sorted(crisp, key=("up", "down").index)


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
    # the luma is the plain layer's at every pixel (every slope and luma contrast measured on it stands), and where
    # the composite is too dark for hue to read (under HOLD_LUMA - HOLD_SPAN) the colour is the layer's own
    assert np.abs(pc.luma(plain) - pc.luma(held)).max() <= 1.5
    dark = pc.luma(plain) < band_edge.HOLD_LUMA - band_edge.HOLD_SPAN - 1
    assert np.abs(np.asarray(plain, np.int16) - np.asarray(held, np.int16))[dark].max(initial=0) <= 1


def _grade_grounds():
    """The dev corpus grounds whose hook grade-out drifted (tests/fixtures/plates_c5_grade_grounds.json: 27 x 48
    colour thumbnails of the 1080 x 1920 cover crop; the c5 review's worst six at ba2750a0 and the six behind it)."""
    d = json.loads((ROOT / "tests" / "fixtures" / "plates_c5_grade_grounds.json").read_text(encoding="utf-8"))
    for g in d["grounds"]:
        w, h = g["size"]
        yield g["source"], Image.frombytes("RGB", (w, h), bytes.fromhex(g["rgb"])).resize((1080, 1920), Image.BICUBIC)


def test_the_hook_grade_out_holds_the_hue_to_the_ledgers_bound_on_the_corpus_grounds():
    """Review of the c5 fix (D3, D4): the grade-out holds the ground's hue to the ledger's own bound (until the
    composite is under 20 luma), not only in alpha 0.1-0.8 of full: on every corpus ground that drifted, wash and plate,
    hue p95 <= 5 degrees and saturation kept >= 0.6 (pixels whose 8-bit chroma can hold a hue: >= 12). The power-law
    hold (HOLD_POWER 6 or 10) drifted 10-176 degrees here."""
    measured = 0
    for source, g in _grade_grounds():
        for name, lay, full in (("plate", dc._plate_layer(g.size, 715, 1055, 0.96, (256, 256), 901, 360), 0.96),
                                ("wash", dc._wash_layer(g.size, 736, 1040, 0.70, 901), 0.70)):
            alpha = np.asarray(lay.getchannel("A"), np.float32) / 255.0
            held = Image.alpha_composite(g.convert("RGBA"), band_edge.hold_hue(g, lay, full)).convert("RGB")
            got = pc.hue_sat(g, held, alpha, full, low=0.05, chroma=12)
            if got is None:
                continue
            measured += 1
            assert got[1] <= 5.0 and got[2] >= 0.6, (source, name, got)
    assert measured >= 16, measured


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


def test_a_real_ground_at_the_gate_floor_takes_the_longer_reach_to_the_margin():
    """Review of the c5 fix (D3): on a real ground the ruled order leaves at the gate floor (the Richat carousel slide
    4 of run 1793474843, its masthead region kept at half size: tests/fixtures/plates_c5_masthead_ground.jpg), the
    margin step takes a longer reach of the same ruled scrim (never a higher peak) and the name clears MARGIN.
    Disabling the margin loop fails this."""
    with Image.open(ROOT / "tests" / "fixtures" / "plates_c5_masthead_ground.jpg") as im:
        half = im.convert("RGB")
    ground = Image.new("RGB", (1440, 1800), (0, 0, 0))
    ground.paste(half.resize((half.width * 2, half.height * 2), Image.BICUBIC), (0, 0))
    x0, baseline, size, reach, room = 107, 131, 160 / 3, 531, 960
    _, _, ruled = masthead.plan_at(ground, x0, baseline, size, reach=reach, max_width=room)
    _, _, rec = masthead.plan(ground, x0, baseline, size, reach=reach, max_width=room)
    assert ruled["shown"] and ruled["contrast"] < masthead.MARGIN, ruled
    assert rec["shown"] and rec.get("margin_step", "").startswith("reach x") and rec["reach"] > reach, rec
    assert rec["contrast"] >= masthead.MARGIN and not rec.get("margin_short"), rec
    assert rec["scrim_peak"] <= brand_system.tok("scrim.masthead_peak") + 1e-9 and not rec["halo_exception"]


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


# ------------------------------------------------------------------------------- second builder: PLATES-03/12/13/14/15
def _worst_ground(tmp_path, name="worst.jpg") -> Path:
    """A synthetic worst case for light type: a near-white ground with brighter specks and a saturated warm cast in
    patches (the bright percentile is white wherever a line falls)."""
    rng = np.random.default_rng(11)
    h, w = 1800, 1440
    base = np.full((h, w, 3), (232, 226, 214), np.float32)
    base[rng.random((h, w)) < 0.04] = (255, 255, 255)
    yy, xx = np.mgrid[0:h, 0:w]
    warm = ((xx // 240 + yy // 240) % 2 == 0)[..., None]
    base = np.where(warm, base * np.array([1.0, 0.86, 0.45]), base)
    p = tmp_path / name
    Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).save(p, quality=95)
    return p


def test_text_on_a_photo_band_reads_at_seven_to_one_on_the_worst_ground(tmp_path):
    """PLATES-15 (and the PHOTO hand-back's Richat closing-slide body, 7.04 -> 6.76 once its window moved): a band
    behind light type over a photograph is as deep as its LINES need on their own ground (band_edge.text_strength),
    so every line reads at contrast.high (7:1) wherever the band's cap allows, on any ground; the record says so.
    The worst-case ground here is near-white with white specks: the old band (solved for the band's own 95th
    percentile pixel) left the lines at the 4.8 placement target."""
    gen = multi_format.MultiFormatGenerator()
    photo = _worst_ground(tmp_path)
    card = {"heading": "Rings of quartzite", "body": "Erosion exposed the dome's rings over millions of years."}
    share = "Send this to someone who loves geology, and follow for more."
    for arch in ("immersive", "cover", "debate"):
        _, lay = gen._render_carousel_slide(arch, photo, photo, card, 0 if arch == "cover" else (6 if arch == "debate"
                                                                                                  else 2), 7, {}, share,
                                            False)
        band = lay.meta.get("text_band")
        assert band and band["target"] == brand_system.tok("contrast.high"), (arch, lay.meta.keys())
        for e in lay.elements:
            if e["name"] in ("kicker", "header_brand") or e.get("masthead") or e.get("contrast") is None:
                continue
            assert e["contrast"] >= 7.0 or (band["short"] and e["contrast"] >= 4.8), (arch, e["name"], e["contrast"])


def test_the_story_band_and_credit_read_at_seven_to_one_and_keep_the_margin(tmp_path):
    """PLATES-15 on the Story (the gold title line 4.92-5.18:1 at the bright percentile) and PLATES-14 (the text
    column 19 px inside the safe box, a plated kicker 5, the photo credit's foot 12): every line reads at 7:1 on the
    worst ground (or its band records the cap), and every text and plate box keeps safe_zones.TEXT_INSET to the Story
    safe box."""
    from src import safe_zones
    gen = multi_format.MultiFormatGenerator()
    photo = _worst_ground(tmp_path)
    slide = {"kicker": "ANCIENT MARVEL", "line1": "THE ACOUSTIC DOME", "line2": "OF GOL GUMBAZ",
             "body": "Sound circles this vast chamber along its curved wall."}
    _, lay = gen._render_story_frame(photo, slide, 0, 2, {})
    sx0, sy0, sx1, sy1 = safe_zones.safe_box("story")
    inset = safe_zones.TEXT_INSET
    for e in lay.elements:
        boxes = [e["box"]] + ([e["plate"]["box"]] if isinstance(e.get("plate"), dict) else [])
        for b in boxes:
            assert min(b[0] - sx0, b[1] - sy0, sx1 - b[2], sy1 - b[3]) >= inset, (e["name"], b)
        if e["name"] not in ("kicker",) and e.get("contrast") is not None:
            band = (lay.ground or {}).get("text_band") or {}
            assert e["contrast"] >= 7.0 or band.get("short"), (e["name"], e["contrast"], band)
    # the column itself: the safe box, the token, and the held label plate's pad
    pad = round(brand_system.tok("plate.label_pad") * brand_system.type_px("kicker", "tall"))
    assert multi_format.STORY_MARGIN == sx0 + inset + pad


def test_a_band_grades_the_photograph_keeping_its_hue_and_saturation():
    """PLATES-03: a text band that never goes opaque grades the photograph (band_edge.graded) where a neutral INK
    alpha left a grey-brown veil: on a saturated warm ground the band's interior keeps the ground's hue (<= 2 degrees)
    and >= 0.95 of its saturation, at the plain alpha's luma; an opaque panel (cap 1) stays its colour."""
    ground = Image.new("RGB", (400, 600), (226, 150, 52))
    graded = multi_format.scrim(ground, 300, 600, feather=100, target=0.26)
    plain = multi_format.scrim(ground, 300, 600, feather=100, target=0.26, grade=False)
    a = np.asarray(ground, np.float32)[500:560, 100:300]
    g = np.asarray(graded, np.float32)[500:560, 100:300]
    p = np.asarray(plain, np.float32)[500:560, 100:300]
    hb, sb = pc._hue_sat(a)
    hg, sg = pc._hue_sat(g)
    hp, sp = pc._hue_sat(p)
    assert np.abs(hg - hb).max() <= 2.0 and (sg / sb).min() >= 0.95
    assert (sp / sb).mean() < (sg / sb).mean()                       # the plain alpha loses saturation
    assert np.abs(pc.luma(Image.fromarray(g.astype(np.uint8))) - pc.luma(Image.fromarray(p.astype(np.uint8)))).max() <= 2
    panel = multi_format.scrim(ground, 300, 600, feather=40, target=0.0, cap=1.0, floor=1.0, colour=brand.INK2)
    assert tuple(np.asarray(panel)[590, 200]) == tuple(brand.INK2)


def test_a_deck_reserves_its_longest_body_so_its_band_stands_at_one_position(tmp_path):
    """PLATES-12 (panel 6: one deck's copy band at three heights; the dev Odessa decks 918 / 973 / 1152): the deck
    frame reserves the copy's longest content body (_deck_body_frame), so a long body no longer lifts its own slide's
    band: two slides of one frame, a two-line heading over a short and over a long body, start at the same cap line."""
    gen = multi_format.MultiFormatGenerator()
    photo = _worst_ground(tmp_path)
    short = {"heading": "Partisan resistance", "body": "Fighters hid in the tunnels."}
    long_ = {"heading": "Partisan resistance", "body": "Fighters hid in the tunnels for months, moving supplies, "
                                                    "printing leaflets and slipping out at night to strike the "
                                                    "occupiers before vanishing back below the city streets."}
    frame = gen._deck_body_frame([short, long_], ["immersive", "immersive"], False)
    assert frame >= 4, frame
    tops = []
    for card in (short, long_):
        _, lay = gen._render_carousel_slide("immersive", photo, photo, card, 2, 7, {}, "", False, heading_frame=2,
                                            body_frame=frame)
        tops.append(next(e["box"][1] for e in lay.elements if e["name"] == "heading"))
    assert abs(tops[0] - tops[1]) <= 3, tops


def test_the_swipe_strip_clears_slide_ones_text_by_the_ledgers_margin():
    """PLATES-13: slide 1's part of the swipe strip clears its text by 24 half px (48 at 1440) or is not drawn."""
    assert multi_format.SWIPE_CLEAR * 1440 / 1080 >= 48
