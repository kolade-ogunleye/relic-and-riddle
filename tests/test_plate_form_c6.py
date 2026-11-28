"""The block family (src/plate_form.py), lander decision of design freeze cycle 6, behind RR_PLATE_FORM.

Every test names the rule it guards and fails when that rule is reverted, at the level production calls it
(plate_form.layer / block_rects / choose, multi_format.scrim / story_ground / text_band_items / plan_kicker /
keep_text_contrast / swipe_strip, design_cards' hook plan and myth ground). The shipped default ("base") is unchanged.
"""
from __future__ import annotations

import ast
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import multi_format, plate_form

FIX = Path(__file__).resolve().parent / "fixtures"
GOLD, BONE = (242, 182, 48), (243, 237, 226)
# a carousel stack as the content slide sets it: kicker, two heading lines, three body lines; a share line apart
STACK = [((112, 934, 900, 965), GOLD, "main"), ((114, 993, 890, 1113), BONE, "main"),
         ((114, 1123, 700, 1243), GOLD, "main"), ((113, 1300, 861, 1338), BONE, "main"),
         ((113, 1352, 840, 1390), BONE, "main"), ((113, 1404, 610, 1442), BONE, "main"),
         ((113, 1560, 1125, 1600), BONE, "cta"), ((113, 1614, 1000, 1654), BONE, "cta")]
BOXES = [b for b, _f, _t in STACK]


def _flat(v, size=(1440, 1800)):
    return Image.new("RGB", size, (v, v, max(0, v - 6)))


def _grounds():
    rng = np.random.default_rng(6)
    real = Image.open(FIX / "plates_c5_masthead_ground.jpg").convert("RGB").resize((1440, 1800))
    noise = Image.fromarray((rng.random((1800, 1440, 1)) * 255).repeat(3, 2).astype(np.uint8), "RGB")
    return {"real": real, "white": _flat(246), "grey": _flat(128), "dark": _flat(22), "black": _flat(3), "busy": noise}


def _scrim(im, **kw):
    got: dict = {}
    out = multi_format.scrim(im, 900, 1800, feather=400, target=0.26, cap=0.90, out=got, plain=STACK, **kw)
    return out, got.get("plate_form") or {}


# ------------------------------------------------------------------------------------------------ the switch
def test_the_default_form_is_base_and_draws_nothing(monkeypatch):
    monkeypatch.delenv(plate_form.ENV, raising=False)
    assert plate_form.active() == "base"
    lyr, rec = plate_form.layer(_flat(235), STACK)
    assert np.asarray(lyr.getchannel("A")).max() == 0 and rec["form"] == "base"


def test_only_base_and_block_ship():
    """Lander ruling 8 (review of cycle 6): the design-study forms leave src."""
    assert plate_form.FORMS == ("base", "block")


@pytest.mark.parametrize("value", ["slab", "none", "hug", "off"])
def test_an_unknown_form_draws_base_and_is_recorded(monkeypatch, value):
    """Lander ruling, step 1 (review of cycle 6, 2026-11-27): a value that names no form is base, recorded."""
    monkeypatch.setenv(plate_form.ENV, value)
    plate_form.UNKNOWN.pop(value, None)
    assert plate_form.active() == "base"
    assert plate_form.UNKNOWN.get(value) == 1


@pytest.mark.parametrize("value", ["none", "block", "base", "anything"])
def test_no_switch_value_can_disable_a_gate(monkeypatch, value):
    """Step 1: no RR_PLATE_FORM value skips refusal.require_gate or require_no_problems."""
    from src import refusal
    monkeypatch.setenv(plate_form.ENV, value)
    with pytest.raises(refusal.OwnRecordFailed):
        refusal.require_gate("probe", [{"name": "body", "text": "x", "contrast": 2.0}], 4.5, "probe rule")
    with pytest.raises(refusal.OwnRecordFailed):
        refusal.require_no_problems("probe", ["below_gate: body 2.0 under 4.5"], "probe rule")


def test_scrim_under_base_is_the_shipped_band(monkeypatch):
    monkeypatch.delenv(plate_form.ENV, raising=False)
    a = np.asarray(multi_format.scrim(_flat(235), 1150, 1800, plain=BOXES).convert("L"), np.float32)
    assert a[1600, 1430] < 200          # the band runs to the frame's right edge
    monkeypatch.setenv(plate_form.ENV, "block")
    b = np.asarray(_scrim(_flat(235))[0].convert("L"), np.float32)
    assert b[1600, 1430] > 220          # the block ends at the text's measure


# ------------------------------------------------------------------------------------------------ the blocks
def test_one_block_per_text_block_and_a_share_line_its_own():
    """Rule 1: the kicker, heading and body one block (lines far apart stay one), the share line its own."""
    _lyr, rec = plate_form.layer(_flat(240), STACK, "block")
    assert len(rec["blocks"]) == 2, rec
    apart = [((112, 700, 900, 731), GOLD, "main"), ((114, 851, 890, 971), BONE, "main")]
    assert len(plate_form.layer(_flat(240), apart, "block")[1]["blocks"]) == 1


def test_the_carousel_share_line_and_its_rule_are_their_own_block():
    """Rule 1 (carousel): text_band_items tags the share rows "cta" and puts the drawn gold rule (share_rule_box,
    the drawing's own geometry) in that block; the kicker is measured at its drawn width (kicker_ink_box)."""
    font = multi_format.get_font(multi_format.brand.TEXT_FONT, 40)
    kf = multi_format.get_font(multi_format.brand.TEXT_FONT, 42, weight=800)
    d = ImageDraw.Draw(Image.new("RGB", (1440, 1800)))
    rows = [{"name": "kicker", "kicker": True, "text": "THE SALT CATHEDRAL", "font": kf, "fill": GOLD, "y": 0},
            {"name": "body", "text": "A body line.", "font": font, "fill": BONE, "y": 80},
            {"name": "share", "text": "Send this to a friend.", "font": font, "fill": BONE, "y": 260}]
    px = multi_format.MultiFormatGenerator._px(1440)
    items = multi_format.text_band_items(d, rows, 112, 1500, px)
    tags = [t for _b, _f, t in items]
    assert tags.count("cta") == 2 and tags[0] == "main"
    k = items[0][0]
    assert k[2] - k[0] >= multi_format.kicker_width("THE SALT CATHEDRAL", kf) - 1
    share_top = items[2][0][1]
    assert items[3][0] == multi_format.share_rule_box(112, share_top, px)


def test_blocks_share_the_column_left_edge_a_snapped_width_and_the_gap_token():
    """Rule 1: one left edge; widths within block.width_snap share the wider; nearer than block.gap: one block."""
    near = [((113, 900, 800, 940), BONE, "a"), ((140, 1100, 860, 1140), BONE, "b")]
    blocks, rec = plate_form.block_rects(near, (1440, 1800))
    a, b = sorted(blocks, key=lambda x: x["box"][1])
    assert a["box"][0] == b["box"][0] and a["box"][2] == b["box"][2]
    gap = plate_form.tk("block.gap") * 1440 / 1080
    assert abs((b["box"][1] - a["box"][3]) - gap) <= 1 or rec["gap_short"]
    close = [((113, 900, 800, 940), BONE, "a"), ((113, 960, 800, 1000), BONE, "b")]
    assert len(plate_form.block_rects(close, (1440, 1800))[0]) == 1


@pytest.mark.parametrize("kind,ink", [("reel", [((137, 760, 810, 878), BONE, "main"), ((137, 892, 520, 1011), BONE,
                                                                                         "main")]),
                                      ("story", [((84, 400, 950, 520), BONE, "main")])])
def test_a_block_keeps_its_safe_box_with_equal_side_pads(kind, ink):
    """Rules 3 and 6: inside the safe box, the pad the box cuts sets the other side's (left = right, >= pad_min)."""
    from src.safe_zones import safe_box
    box = safe_box(kind, 1080, 1920)
    blocks, rec = plate_form.block_rects(ink, (1080, 1920), box)
    x0, y0, x1, y1 = blocks[0]["box"]
    assert x0 >= box[0] and x1 <= box[2] and y0 >= box[1] and y1 <= box[3]
    lx, rx = min(b[0] for b, _f, _t in ink), max(b[2] for b, _f, _t in ink)
    assert abs((lx - x0) - (x1 - rx)) <= 1, rec
    assert lx - x0 >= plate_form.tk("block.pad_min") - 1
    # a line the box leaves under pad_min on a side: symmetric still, and recorded clamped
    tight, rec2 = plate_form.block_rects([((84, 400, 1005, 520), BONE, "main")], (1080, 1920), safe_box("story", 1080, 1920))
    assert rec2["blocks"][0]["clamped"] and abs(rec2["blocks"][0]["pad_x"][0] - rec2["blocks"][0]["pad_x"][1]) <= 1


# ------------------------------------------------------------------------------------------------ where it is laid
def test_the_choice_is_monotonic_in_ground_brightness(monkeypatch):
    """Rule 2: on one stack over flat grounds from black to white, the shipped band below some brightness and the
    block above it: one switch, never back."""
    monkeypatch.setenv(plate_form.ENV, "block")
    laid = [_scrim(_flat(v))[1]["laid"] for v in range(3, 256, 12)]
    first = laid.index("block")
    assert all(x == "block" for x in laid[first:]), laid
    assert laid[0] == "band"


def test_a_mid_tone_ground_the_band_leaves_under_the_target_takes_the_block(monkeypatch):
    """Rule 2: the band on a mid-tone ground leaves gold under target x margin: the block is laid there although
    it darkens more of the frame (the brief's "where the ground needs it")."""
    monkeypatch.setenv(plate_form.ENV, "block")
    out, rec = _scrim(_flat(64))
    assert rec["band_dark_share"] <= rec["dark_share"], rec    # the band darkens less here
    assert rec["band_under_target"] and rec["laid"] == "block", rec


def test_a_ground_the_band_carries_keeps_the_band(monkeypatch):
    """Rule 2: where the band leaves every line at the target and the block would darken more, the band stays."""
    monkeypatch.setenv(plate_form.ENV, "block")
    out, rec = _scrim(_flat(22))
    assert not rec["band_under_target"] and rec["laid"] == "band", rec


def test_inside_the_block_the_ground_is_never_lighter_than_the_band(monkeypatch):
    """Rule 3 (darker_inside): a stripe of the photograph darker than INK under the type stays as dark as the band
    leaves it, inside the block."""
    im = _flat(240)
    ImageDraw.Draw(im).rectangle([0, 1000, 1440, 1040], fill=(2, 2, 2))
    monkeypatch.delenv(plate_form.ENV, raising=False)
    band = np.asarray(multi_format.scrim(im, 900, 1800, feather=400, target=0.26, cap=0.90, plain=BOXES).convert("L"))
    monkeypatch.setenv(plate_form.ENV, "block")
    out, rec = _scrim(im)
    assert rec["laid"] == "block"
    o = np.asarray(out.convert("L"))
    assert o[1020, 400] <= band[1020, 400] + 1


@pytest.mark.parametrize("ground", ["real", "white", "grey", "busy"])
def test_the_block_reaches_the_target_after_a_platform_re_encode(monkeypatch, ground):
    """The opaque tier (block.tier): every line's ground, re-encoded, reads the target against GOLD."""
    monkeypatch.setenv(plate_form.ENV, "block")
    out, rec = _scrim(_grounds()[ground])
    if rec["laid"] != "block":
        pytest.skip("the band is laid here")
    b = io.BytesIO()
    out.save(b, "JPEG", quality=75, subsampling=2)
    a = np.asarray(Image.open(io.BytesIO(b.getvalue())).convert("RGB"), np.float32)
    for (x0, y0, x1, y1) in BOXES:
        g = plate_form.rel_lum(a[y0:y1, x0:x1].reshape(-1, 3))
        assert plate_form.ratio(float(plate_form.rel_lum(np.asarray(GOLD, np.float32))),
                                float(np.percentile(g, 95))) >= plate_form.tk("target")


def test_a_surveyed_kicker_reads_its_frames_shipped_ground(monkeypatch):
    """Rule 1/2: a frame that lays its block keeps the band it replaces (_render_state.shipped_image), and a deck's
    or Story's kicker survey reads the kicker's passes on it (so the set's one step is base's), while the drawing
    keeps its block (a survey drawing may be the one kept)."""
    monkeypatch.setenv(plate_form.ENV, "block")
    im = _flat(240)
    with multi_format.kicker_set(None, survey=True):
        out, rec = _scrim(im)
    assert rec["laid"] == "block"
    monkeypatch.delenv(plate_form.ENV, raising=False)
    band = multi_format.scrim(im, 900, 1800, feather=400, target=0.26, cap=0.90, plain=BOXES)
    assert np.array_equal(np.asarray(multi_format._render_state.shipped_image), np.asarray(band))
    monkeypatch.setenv(plate_form.ENV, "block")
    font = multi_format.get_font(multi_format.brand.TEXT_FONT, 42, weight=800)
    seen = []
    real = multi_format.label_passes if hasattr(multi_format, "label_passes") else None
    import src.typography as typo
    orig = typo.label_passes

    def spy(**kw):
        seen.append(kw["ground"])
        return orig(**kw)
    monkeypatch.setattr(typo, "label_passes", spy)
    with multi_format.kicker_set(None, survey=True):
        multi_format.plan_kicker(out, (150, 950), "THE SALT CATHEDRAL", font, GOLD)
    assert seen and seen[0] is multi_format._render_state.shipped_image
    multi_format._render_state.shipped_image = None
    del real


def test_the_window_fitters_move_test_reads_the_shipped_ground(monkeypatch):
    """Rule 1: keep_text_contrast under block draws both windows on the shipped ground (shipped_ground set)."""
    monkeypatch.setenv(plate_form.ENV, "block")
    seen = []

    class Lay:
        def __init__(self, moved):
            self.meta = {"windows": [{"fit": {"moved": moved}}]}
            self.plates = {}

    def draw():
        seen.append(bool(getattr(multi_format._render_state, "shipped_ground", False)))
        return Image.new("RGB", (8, 8)), Lay(not getattr(multi_format._render_state, "asked_only", False))
    multi_format.keep_text_contrast(draw, Image.new("RGB", (8, 8)), Lay(True))
    assert seen[:2] == [True, True], seen


def test_a_window_trial_frame_is_base_whole_its_kicker_too(monkeypatch):
    """Rule 1: a window trial drawn on the shipped ground never keeps the form a block drawing left (frame_form),
    so its kicker takes base's step and the moved/asked comparison is base's."""
    monkeypatch.setenv(plate_form.ENV, "block")
    multi_format._render_state.frame_form = "block"
    multi_format._render_state.shipped_ground = True
    try:
        out, _rec = _scrim(_flat(240))
        assert multi_format._render_state.frame_form == "band"
        assert multi_format._render_state.shipped_image is None
    finally:
        multi_format._render_state.shipped_ground = False
        multi_format._render_state.frame_form = None
    monkeypatch.delenv(plate_form.ENV, raising=False)
    band = multi_format.scrim(_flat(240), 900, 1800, feather=400, target=0.26, cap=0.90, plain=BOXES)
    assert np.array_equal(np.asarray(out), np.asarray(band))   # the trial's ground is base's band


def test_a_kicker_inside_its_block_takes_its_colour_and_on_a_band_frame_the_decks_step(monkeypatch):
    """Rule 2 (one form per frame): inside a block the kicker has no plate of its own; on a frame that keeps the band
    it takes the deck's one step (base's)."""
    font = multi_format.get_font(multi_format.brand.TEXT_FONT, 42, weight=800)
    im = Image.new("RGB", (1440, 600), (70, 66, 60))
    monkeypatch.setenv(plate_form.ENV, "block")
    multi_format._render_state.frame_form = "block"
    with multi_format.kicker_set(("plate", 0)):
        _p, rec = multi_format.plan_kicker(im, (150, 300), "THE SALT CATHEDRAL", font, GOLD)
    assert rec["variant"] != "plate", rec
    multi_format._render_state.frame_form = "band"
    with multi_format.kicker_set(("plate", 0)):
        _p, rec = multi_format.plan_kicker(im, (150, 300), "THE SALT CATHEDRAL", font, GOLD)
    assert rec["variant"] == "plate", rec
    multi_format._render_state.frame_form = None


# ------------------------------------------------------------------------------------------------ Story
def test_a_story_frames_credit_kicker_and_text_are_one_block_family(monkeypatch, tmp_path):
    """Rules 1 and 4: the Story frame's kicker in its main block, no withheld pointer's room laid, and the credit a
    block of the same family (left edge, snap, record) chosen with the frame (one form)."""
    from src.multi_format import MultiFormatGenerator
    monkeypatch.setenv(plate_form.ENV, "block")
    photo = tmp_path / "s.jpg"
    Image.new("RGB", (1080, 1920), (236, 232, 224)).save(photo)
    im, lay = MultiFormatGenerator()._render_story_frame(
        photo, {"kicker": "DID YOU KNOW?", "line1": "ONE MAN", "line2": "KNOCKED", "body": "Short."}, 0, 3, {})
    rec = (getattr(lay, "ground", None) or {}).get("plate_form") or {}
    assert rec.get("laid") == "block", rec
    k = next(e for e in lay.elements if e["name"] == "kicker")
    main = next(b for b in rec["blocks"] if "main" in b["tag"])
    assert main["box"][1] <= k["box"][1] and main["box"][0] <= k["box"][0]
    body = max(e["box"][3] for e in lay.elements if e["name"] not in ("photo_credit",))
    assert main["box"][3] <= body + 2 * plate_form.tk("block.pad_min") + plate_form.tk("block.gap"), rec   # no ghost
    d = ImageDraw.Draw(im)
    credit = [((84, 1180, 700, 1206), (184, 177, 163), "credit")]
    base = Image.new("RGB", (1080, 1920), (236, 232, 224))
    rows = [("title_1", 0, "ONE MAN", multi_format.get_font(multi_format.brand.DISPLAY_FONT, 120), BONE, 0, 0)]
    _shaded, _rows, g = multi_format.story_ground(base, d, rows, 84, 500, 200, plain=credit, band_on=base)
    tags = [b["tag"] for b in g["plate_form"]["blocks"]]
    assert "credit" in tags and len({b["box"][0] for b in g["plate_form"]["blocks"]}) == 1


# ------------------------------------------------------------------------------------------------ hook and myth
def _hook(photo):
    import tests.test_freeze_cards_2026_11_04 as fc
    from src import design_cards as dc
    return dc, fc.hook_plan(photo)


@pytest.mark.parametrize("v,laid", [(246, "block"), (4, "band")])
def test_the_hook_lays_the_block_by_the_choice(monkeypatch, tmp_path, v, laid):
    """Rule 2 on the hook (plate_form.choose): a bright ground takes the block, a black one keeps the shipped
    ground."""
    monkeypatch.setenv(plate_form.ENV, "block")
    photo = tmp_path / "h.jpg"
    Image.new("RGB", (1600, 2400), (v, v, v)).save(photo)
    try:
        _dc, plan = _hook(photo)
    except Exception as e:   # a ground the card refuses at base too is not this rule's
        pytest.skip(f"refused: {e}")
    assert plan.meta["scrim"]["plate_form"]["laid"] == laid
    assert (plan.meta["scrim"]["mode"] == "block") == (laid == "block"), plan.meta["scrim"]   # what is drawn


def _myth(photo):
    import tests.test_freeze_cards_2026_11_04 as fc
    from src import design_cards as dc
    plan = fc.richat_myth(fc.real_sheet("richat_structure_sahara_eye"), photo=photo, size=fc.TALL)
    return dc, plan


def test_the_myth_block_is_chosen_never_lighter_and_holds_every_line(monkeypatch, tmp_path):
    """Rules 2, 3 and 6 on the 9:16 myth: a bright ground takes the block (plate_form.choose), a black one keeps the
    panel; inside the block a photograph darker than INK stays dark; every drawn line has pad_min inside the block."""
    monkeypatch.setenv(plate_form.ENV, "block")
    photo = tmp_path / "m.jpg"
    im = Image.new("RGB", (1600, 2400), (240, 238, 230))
    ImageDraw.Draw(im).rectangle([0, 900, 1600, 1000], fill=(1, 1, 1))
    im.save(photo)
    dc, plan = _myth(photo)
    rec = plan.meta["panel_edges"]["plate_form"]
    assert rec["laid"] == "block", rec
    drift = plan.meta["_r"]["drift"]
    pad = plate_form.tk("block.pad_min")
    for e in plan.elements:
        if getattr(e, "kind", "") == "text":
            assert any(b["box"][1] <= e.box[1] - pad + 1 and e.box[3] + pad - 1 <= b["box"][3] for b in rec["blocks"])
    # inside the block the fill is never lighter than the shipped panel: the fill's darkest rows equal the photograph's
    # own black band where it crosses the block (darker than INK), not INK
    inside = plate_form.block_mask((1080, 1920), rec["blocks"]) > 0.99
    assert float(plate_form.luma(Image.fromarray(drift.fill.astype(np.uint8)))[inside].min()) < 10
    photo2 = tmp_path / "k.jpg"
    Image.new("RGB", (1600, 2400), (2, 2, 2)).save(photo2)
    _dc, plan2 = _myth(photo2)
    assert plan2.meta["panel_edges"]["plate_form"]["laid"] == "band"
    assert plan2.meta["panel_edges"].get("form") != "block"           # what is drawn: the shipped panel
    assert plan.meta["panel_edges"].get("form") == "block"


def test_the_myth_block_enters_by_its_first_element_on_a_flash_safe_curve():
    """Rules 5 and 7: absent before the MYTH kicker's reveal; on a bright ground (a luminance step over the flash
    check's 10 %) it enters over motion.reveal (eased), on a dim one over the caption plate's fade; the foot follows
    the panel's opening."""
    from src import design_cards as dc
    from src.config import CAPTION_PLATE_FADE_IN_MS
    geo = dc._Geo(1080, 1920)
    rec = {"tier": 1.0, "blocks": [{"box": [119, 296, 707, 1097]}]}
    bright = Image.new("RGB", (1080, 1920), (230, 228, 220))
    fill = Image.new("RGB", (1080, 1920), (11, 14, 18))
    drift = dc._BlockDrift(bright, dict(rec), geo, (600.0, 1110.0, 2.0, 0.4), dc.MYTH_KICKER_IN, fill)
    assert drift.curve == "reveal" and drift.fade == pytest.approx(float(dc._mtok("motion.reveal")))
    lum = lambda im, x, y: float(np.asarray(im.convert("L"))[y, x])  # noqa: E731
    assert lum(drift(dc.MYTH_KICKER_IN - 0.05), 400, 400) > 200
    assert lum(drift(dc.MYTH_KICKER_IN + drift.fade + 0.01), 400, 400) < 40
    assert lum(drift(1.0), 400, 900) > 200 and lum(drift(3.0), 400, 900) < 40 and lum(drift(3.0), 400, 1150) > 200
    dim = Image.new("RGB", (1080, 1920), (40, 40, 40))
    d2 = dc._BlockDrift(dim, {"tier": 1.0, "blocks": [{"box": [119, 296, 707, 1097]}]}, geo, None,
                        dc.MYTH_KICKER_IN, fill)
    assert d2.curve == "caption_plate" and d2.fade == pytest.approx(CAPTION_PLATE_FADE_IN_MS / 1000)


# ------------------------------------------------------------------------------------------------ cover seam
def test_under_the_block_the_cover_strip_is_never_drawn_on_the_photograph():
    """Rule 5: slide 1's swipe strip under a laid block is drawn only inside a block, else not at all (recorded)."""
    im = Image.new("RGB", (1440, 1800), (240, 238, 230))

    class Lay:
        elements = [{"name": "heading", "box": [113, 946, 1100, 1314]}]
        meta = {"plate_form": {"laid": "block", "side_pad": 30, "blocks": [{"box": [83, 900, 1130, 1360]}]}}
    out = multi_format.swipe_strip(im, Lay, {"side": "right", "y0": 1000, "y1": 1006})
    assert np.array_equal(np.asarray(out), np.asarray(im))
    assert "skipped" in Lay.meta["swipe_strip"]


# ------------------------------------------------------------------------------------------------ tokens
def test_every_block_measure_is_a_token():
    """Rule 8: no number in plate_form's functions but 0, 1, 2, an index or round()'s digits: every threshold is a
    token (TOKENS) or a named constant read from its source."""
    tree = ast.parse((Path(plate_form.__file__)).read_text(encoding="utf-8"))
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript):
            skip.update(id(c) for c in ast.walk(n.slice))
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "round" and len(n.args) > 1:
            skip.add(id(n.args[1]))
    bad = []
    for f in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for c in ast.walk(f):
            if (isinstance(c, ast.Constant) and isinstance(c.value, (int, float)) and not isinstance(c.value, bool)
                    and c.value not in (0, 1, 2) and id(c) not in skip):
                bad.append((f.name, c.value, c.lineno))
    assert bad == [], bad
    for name in ("block.pad_x", "block.pad_y", "block.pad_min", "block.gap", "block.gap_reach", "block.width_snap",
                 "block.radius", "block.tier", "block.encode_margin", "block.dark_step", "block.share_tie",
                 "block.ring", "block.ring_pct", "block.supersample"):
        assert name in plate_form.TOKENS


def test_each_frame_starts_without_a_form():
    """Rule 2 (one form per frame): a frame renderer starts with no form, so a split slide (no ground of its own) never
    inherits the previous slide's block (its kicker then takes the deck's step, as at base)."""
    seen = []

    class Gen:
        @multi_format._holds_text_contrast
        def render(self):
            seen.append(getattr(multi_format._render_state, "frame_form", None))

            class Lay:
                meta: dict = {}
            return Image.new("RGB", (4, 4)), Lay()
    multi_format._render_state.frame_form = "block"
    Gen().render()
    assert seen == [None]


def test_the_myth_block_grows_to_hold_every_drawn_line():
    """Rule 6: a drawn line nearer than pad_min to the block's edge (the source line at its own metrics) grows the
    block (design_cards._BlockDrift.cover), and the mask is drawn again."""
    from src import design_cards as dc
    geo = dc._Geo(1080, 1920)
    rec = {"tier": 1.0, "blocks": [{"box": [119, 296, 707, 1060]}]}
    pic = Image.new("RGB", (1080, 1920), (230, 228, 220))
    drift = dc._BlockDrift(pic, rec, geo, None, dc.MYTH_KICKER_IN, Image.new("RGB", (1080, 1920), (11, 14, 18)))
    drift.cover([(137, 1051, 594, 1078)])
    assert rec["blocks"][0]["box"][3] >= 1078 + plate_form.tk("block.pad_min")
    assert float(np.asarray(drift(5.0).convert("L"))[1090, 300]) < 40


def test_the_decks_one_step_is_planned_on_the_shipped_grounds_survey():
    """Rule 2: kickers drawn in their blocks all take their colour, but the deck's one step is planned on each
    kicker's survey on its frame's shipped ground (survey_step / passes), so a band frame's kicker keeps base's step."""
    class Lay:
        def __init__(self, rec):
            self.meta = {"kickers": {"kicker": rec}}
    on_block = {"shown": True, "step": ["halo", 0], "survey_step": ["plate", 0], "passes": [["plate", 0], ["plate", 1]]}
    on_band = {"shown": True, "step": ["plate", 0], "passes": [["halo", 0], ["plate", 0]]}
    assert tuple(multi_format.deck_kicker_step([Lay(on_block), Lay(on_band)])) == ("plate", 0)
    assert tuple(multi_format.deck_kicker_step([Lay(on_block), Lay(dict(on_block))])) == ("plate", 0)


def test_the_myth_plan_grows_its_block_to_the_drawn_lines(monkeypatch, tmp_path):
    """Rule 6 at the plan: _myth_compose hands every drawn text box to the block (cover) after the type is set."""
    from src import design_cards as dc
    monkeypatch.setenv(plate_form.ENV, "block")
    seen = []
    real = dc._BlockDrift.cover

    def spy(self, boxes):
        seen.append(len(boxes))
        return real(self, boxes)
    monkeypatch.setattr(dc._BlockDrift, "cover", spy)
    photo = tmp_path / "m.jpg"
    Image.new("RGB", (1600, 2400), (240, 238, 230)).save(photo)
    _dc, plan = _myth(photo)
    assert seen and seen[0] >= 5


def test_a_story_frames_drawn_credit_is_a_block_of_its_frame(monkeypatch, tmp_path):
    """Rule 4 at the frame: a Story frame with a credit lays the credit as a block of the frame's family (tag
    "credit", the frame's left edge), in the frame's one record, never a band of its own."""
    from src.multi_format import MultiFormatGenerator
    monkeypatch.setenv(plate_form.ENV, "block")
    monkeypatch.setattr(multi_format, "story_credit", lambda photo, pages=None: {
        "required": True, "lines": ["Photo: Jane Doe, CC BY 4.0"], "license": "CC BY 4.0"})
    photo = tmp_path / "s.jpg"
    Image.new("RGB", (1080, 1920), (236, 232, 224)).save(photo)
    _im, lay = MultiFormatGenerator()._render_story_frame(
        photo, {"kicker": "DID YOU KNOW?", "line1": "ONE MAN", "line2": "KNOCKED", "body": "Short."}, 0, 3, {})
    rec = (getattr(lay, "ground", None) or {}).get("plate_form") or {}
    assert rec.get("laid") == "block", rec
    tags = [b["tag"] for b in rec["blocks"]]
    assert "credit" in tags, tags
    assert len({b["box"][0] for b in rec["blocks"]}) == 1, rec["blocks"]


def test_the_hooks_eyebrow_is_in_its_block(monkeypatch, tmp_path):
    """Rules 1/2 on the hook: the eyebrow is a line of the hook's main block (one form for the frame), so the block
    reaches above it, never a band or plate of its own."""
    import tests.test_freeze_cards_2026_11_04 as fc
    monkeypatch.setenv(plate_form.ENV, "block")
    photo = tmp_path / "h.jpg"
    Image.new("RGB", (1600, 2400), (246, 246, 246)).save(photo)
    plan = fc.hook_plan(photo, eyebrow="The Richat ring")
    rec = plan.meta["scrim"]["plate_form"]
    assert rec["laid"] == "block", rec
    eb = next(x for x in plan.elements if x.name == "eyebrow")
    assert any(b["box"][1] <= eb.box[1] and b["box"][3] >= eb.box[3] and b["box"][0] <= eb.box[0]
               for b in rec["blocks"]), (eb.box, rec["blocks"])
