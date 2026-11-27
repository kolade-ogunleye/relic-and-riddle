"""Design freeze cycle 6 (PLATEFORM design stream): the candidate ground forms are a switch only.

The shipped design is "base" unless RR_PLATE_FORM names a candidate; under "base" no call site takes the candidate
path (multi_format.scrim lays its own band), and every candidate lays its ground from the type's boxes alone.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src import multi_format, plate_form


def _bright(w=1080, h=1920, v=235):
    return Image.new("RGB", (w, h), (v, v, v - 10))


BOXES = [((137, 1200, 820, 1300), (243, 237, 226)), ((137, 1320, 640, 1420), (242, 182, 48))]


def test_the_default_form_is_base_and_draws_nothing(monkeypatch):
    monkeypatch.delenv(plate_form.ENV, raising=False)
    assert plate_form.active() == "base"
    lyr, rec = plate_form.layer(_bright(), BOXES)
    assert np.asarray(lyr.getchannel("A")).max() == 0 and rec["form"] == "base"


def test_an_unknown_form_is_refused(monkeypatch):
    monkeypatch.setenv(plate_form.ENV, "slab")
    with pytest.raises(ValueError):
        plate_form.active()


def test_scrim_under_base_is_the_shipped_band(monkeypatch):
    """Under base, scrim() with the type's boxes is the integration-5 band (full width), never a candidate form."""
    monkeypatch.delenv(plate_form.ENV, raising=False)
    im = _bright()
    a = np.asarray(multi_format.scrim(im, 1150, 1920, plain=[b for b, _f in BOXES]).convert("L"), np.float32)
    assert a[1600, 1070] < 200          # the band runs to the frame's right edge
    monkeypatch.setenv(plate_form.ENV, "block")
    b = np.asarray(multi_format.scrim(im, 1150, 1920, plain=[b for b, _f in BOXES]).convert("L"), np.float32)
    assert b[1600, 1070] > 220          # the text-fitted block ends at the text's measure


@pytest.mark.parametrize("form", ["hug", "block", "field", "grade", "split"])
def test_each_form_reaches_the_target_under_the_type_on_a_bright_ground(form):
    im = _bright()
    out, rec = plate_form.lay(im, BOXES, form)
    a = np.asarray(out, np.float32)
    for (x0, y0, x1, y1), fill in BOXES:
        g = plate_form.rel_lum(a[y0:y1, x0:x1].reshape(-1, 3))
        ratio = plate_form.ratio(float(plate_form.rel_lum(np.asarray(fill, np.float32))), float(np.percentile(g, 95)))
        assert ratio >= (4.5 if form in ("field", "grade") else 7.0), (form, ratio, rec)


@pytest.mark.parametrize("form", ["hug", "block", "field", "grade"])
def test_the_text_tied_forms_leave_the_frame_edge_and_the_far_photograph_untouched(form):
    im = _bright()
    out, _ = plate_form.lay(im, BOXES, form)
    a = np.asarray(out.convert("L"), np.float32)
    b = np.asarray(im.convert("L"), np.float32)
    assert abs(a[1300, 1075] - b[1300, 1075]) < 2      # no full-width band
    assert abs(a[300, 540] - b[300, 540]) < 2          # the photograph far from the type is itself


# ------------------------------------------------------------------------------------------------ the block family
# Lander decision, freeze cycle 6 (2026-11-27): the block family is the text ground over a photograph. Each test
# below fails when its rule is reverted (the rule named in its docstring).
from pathlib import Path  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
GOLD, BONE = (242, 182, 48), (243, 237, 226)
# a carousel stack as the content slide sets it: kicker, two heading lines, three body lines; a share line apart
STACK = [((112, 934, 900, 965), GOLD, "main"), ((114, 993, 890, 1113), BONE, "main"),
         ((114, 1123, 700, 1243), GOLD, "main"), ((113, 1300, 861, 1338), BONE, "main"),
         ((113, 1352, 840, 1390), BONE, "main"), ((113, 1404, 610, 1442), BONE, "main"),
         ((113, 1560, 1125, 1600), BONE, "cta"), ((113, 1614, 1000, 1654), BONE, "cta")]


def _grounds():
    """Real and limiting grounds: the committed Richat slide (cycle 5 fixture) at the carousel's size, near-white,
    mid grey, near-black, and a busy high-contrast noise."""
    rng = np.random.default_rng(6)
    real = Image.open(FIX / "plates_c5_masthead_ground.jpg").convert("RGB").resize((1440, 1800))
    noise = Image.fromarray((rng.random((1800, 1440, 1)) * 255).repeat(3, 2).astype(np.uint8), "RGB")
    return {"real": real, "white": Image.new("RGB", (1440, 1800), (246, 244, 238)),
            "grey": Image.new("RGB", (1440, 1800), (128, 126, 120)), "dark": Image.new("RGB", (1440, 1800), (22, 20, 18)),
            "dim": Image.new("RGB", (1440, 1800), (64, 62, 58)),
            "busy": noise}


def test_the_kicker_heading_and_body_are_one_block_and_a_share_line_its_own():
    """Rule 1 (one plate per text block): the kicker joins its group's block (no plate of its own inside it)."""
    blocks, rec = plate_form.block_rects(STACK, (1440, 1800))
    assert len(blocks) == 2, rec
    main = next(b for b in blocks if b["tag"] == "main")
    assert main["box"][1] < 934 and main["box"][3] > 1442 and main["lines"] == 6
    # a text block stays ONE plate even where its own lines stand far apart (a kicker 120 px above its heading)
    apart = [((112, 700, 900, 731), GOLD, "main"), ((114, 851, 890, 971), BONE, "main")]
    assert len(plate_form.block_rects(apart, (1440, 1800))[0]) == 1


def test_blocks_share_the_column_left_edge_a_snapped_width_and_the_gap_token():
    """Rule 1: every block on the text column's left edge; widths within block.width_snap share the wider; the gap
    between two blocks is the block.gap token where the facing edges can reach it."""
    near = [((113, 900, 800, 940), BONE, "a"), ((140, 1100, 860, 1140), BONE, "b")]
    blocks, rec = plate_form.block_rects(near, (1440, 1800))
    a, b = sorted(blocks, key=lambda x: x["box"][1])
    assert a["box"][0] == b["box"][0]
    assert a["box"][2] == b["box"][2]          # 687 vs 720 wide: inside the 115 px snap at 1440
    gap = plate_form.tk("block.gap") * 1440 / 1080
    assert abs((b["box"][1] - a["box"][3]) - gap) <= 1 or rec["gap_short"]
    close = [((113, 900, 800, 940), BONE, "a"), ((113, 960, 800, 1000), BONE, "b")]
    assert len(plate_form.block_rects(close, (1440, 1800))[0]) == 1      # nearer than the token: one block


def test_a_block_never_crosses_its_safe_box():
    """Rule 3 (a block never clips at the safe box): the Story column at x 84 against the safe box's 65."""
    from src.safe_zones import safe_box
    box = safe_box("story", 1080, 1920)
    blocks, _ = plate_form.block_rects([((84, 400, 1000, 520), BONE, "main")], (1080, 1920), box)
    x0, y0, x1, y1 = blocks[0]["box"]
    assert x0 >= box[0] and x1 <= box[2] and y0 >= box[1] and y1 <= box[3]


@pytest.mark.parametrize("ground", ["real", "white", "grey", "dark", "dim", "busy"])
def test_scrim_lays_the_block_only_where_it_darkens_no_more_than_the_shipped_band(monkeypatch, ground):
    """Rule 3 (darkened share never above integration 5), at the level production calls it (multi_format.scrim with
    the carousel's tagged items): the block is laid, or the band where the block would darken more."""
    im = _grounds()[ground]
    monkeypatch.delenv(plate_form.ENV, raising=False)
    band = multi_format.scrim(im, 900, 1800, feather=400, target=0.26, cap=0.90, plain=[b for b, _f, _t in STACK])
    monkeypatch.setenv(plate_form.ENV, "block")
    got: dict = {}
    out = multi_format.scrim(im, 900, 1800, feather=400, target=0.26, cap=0.90, out=got, plain=STACK)
    assert plate_form.dark_share(im, out) <= plate_form.dark_share(im, band) + 1e-4, got["plate_form"]
    if ground in ("white", "busy", "real"):
        assert got["plate_form"]["laid"] == "block"
    if ground == "dim":   # the band darkens under 20 luma here, an INK block 50: the band stays
        assert got["plate_form"]["laid"] == "band"


def test_under_the_block_family_a_kicker_stands_on_its_held_plate_where_no_block_is(monkeypatch):
    """Rule 3 (contrast never lower than base; ruling 1, the kicker in its block): a kicker on a frame that keeps the
    band takes its held plate (the family's smallest block), whatever the deck's one step would be."""
    from src import brand
    font = multi_format.get_font(brand.TEXT_FONT, 42, weight=800)
    im = Image.new("RGB", (1440, 600), (70, 66, 60))
    monkeypatch.setenv(plate_form.ENV, "block")
    with multi_format.kicker_set(("halo", 0)):
        _plate, rec = multi_format.plan_kicker(im, (150, 300), "THE SALT CATHEDRAL", font, GOLD)
    assert rec["variant"] == "plate", rec
    monkeypatch.delenv(plate_form.ENV, raising=False)
    with multi_format.kicker_set(("halo", 0)):
        _plate, rec = multi_format.plan_kicker(im, (150, 300), "THE SALT CATHEDRAL", font, GOLD)
    assert rec["variant"] != "plate", rec


@pytest.mark.parametrize("ground", ["real", "white", "grey", "busy"])
def test_the_block_reaches_the_target_after_a_platform_re_encode(monkeypatch, ground):
    """Rule 3 (contrast per element at or above base and its gate): on a laid block every line's ground, re-encoded
    (JPEG q75 4:2:0), reads at least the 7:1 target against GOLD (the darker text fill) at the 95th percentile."""
    import io
    im = _grounds()[ground]
    monkeypatch.setenv(plate_form.ENV, "block")
    got: dict = {}
    out = multi_format.scrim(im, 900, 1800, out=got, plain=STACK)
    if got["plate_form"]["laid"] != "block":
        pytest.skip("the band is laid here (it darkens less)")
    b = io.BytesIO()
    out.save(b, "JPEG", quality=75, subsampling=2)
    a = np.asarray(Image.open(io.BytesIO(b.getvalue())).convert("RGB"), np.float32)
    for (x0, y0, x1, y1), _f, _t in STACK:
        g = plate_form.rel_lum(a[y0:y1, x0:x1].reshape(-1, 3))
        assert plate_form.ratio(float(plate_form.rel_lum(np.asarray(GOLD, np.float32))),
                                float(np.percentile(g, 95))) >= 7.0


def test_the_myth_block_enters_with_its_first_element_and_its_foot_follows_the_panel():
    """Rule 5 (motion): the 9:16 myth block is absent before its first element (MYTH_BLOCK_IN), fully laid after
    the caption plate's fade-in, and its foot stands where the panel's opening stands (no fact line on bare photo)."""
    from src import design_cards as dc
    from src.config import CAPTION_PLATE_FADE_IN_MS
    pic = Image.new("RGB", (1080, 1920), (230, 228, 220))
    rec = {"tier": 1.0, "blocks": [{"box": [119, 296, 707, 1097]}]}
    geo = dc._Geo(1080, 1920)
    drift = dc._BlockDrift(pic, rec, geo, (600.0, 1110.0, 2.0, 0.4), dc.MYTH_BLOCK_IN)
    lum = lambda im, x, y: float(np.asarray(im.convert("L"))[y, x])  # noqa: E731
    assert lum(drift(dc.MYTH_BLOCK_IN - 0.05), 400, 400) > 200
    assert lum(drift(dc.MYTH_BLOCK_IN + CAPTION_PLATE_FADE_IN_MS / 1000 + 0.01), 400, 400) < 40
    assert lum(drift(1.0), 400, 900) > 200          # under the opening foot (600) the block has not opened yet
    assert lum(drift(3.0), 400, 900) < 40           # after t0 + dur the block reaches its own foot
    assert lum(drift(3.0), 400, 1150) > 200         # and never past it


def test_every_block_measure_is_a_token():
    """Rule 3 (radius, pads, tiers all tokens): the block reads every length from plate_form.TOKENS."""
    for name in ("block.pad_x", "block.pad_y", "block.pad_min", "block.gap", "block.gap_reach", "block.width_snap",
                 "block.radius", "block.tiers", "block.encode_margin"):
        assert name in plate_form.TOKENS
    assert plate_form.tk("block.radius") == plate_form.tk("hug.radius")     # the system's plate.radius
