"""Design-freeze cycle 0 round 7, lane PLATE (preview panel 1: F8, F9, F15, F17, F19), from the panel's measured
cases on synthetic grounds (CI has no output/):

- F8 / F17: a band or plate edge eases over a feather that grows with its luma step (band_edge, the token
  edge.slope_max): the panel's lid was 217 -> INK 24 in ~40 px (6.1 luma / px) on a bright ground, 1.8 on a dark one.
- F9: no delivered hero window contains a panel seam (a luma step over > 80 % of the width at a gutter) or a printed
  banner row (Richat's "Copernicus Sentinel data" bar, missed on a 9930 x 5190 frame).
- F15: overlay labels take one halo spread and an alpha never above 0.16; FOLLOW takes the light-ground INK variant.
- F19: the 9:16 margin token, the Reel box inset by safe_zones.TEXT_INSET (x 137-824).
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import (
    band_edge,
    brand,
    brand_system,
    end_card,
    hero_window,
    multi_format,
    safe_zones,
)
from src.typography import get_font

ROOT = Path(__file__).resolve().parent.parent


def _cap(width: int) -> float:
    """The band-edge token as luma per canvas px on a canvas `width` wide."""
    return brand_system.tok("edge.slope_max") * 1080 / width


def _steepest_into_band(im: Image.Image, y0: int, y1: int, columns) -> float:
    lum = band_edge.luma_rows(im)
    return max(band_edge.steepest(lum[y0:y1, x]) for x in columns)


# ------------------------------------------------------------------------------------------------ F8: band edges

@pytest.mark.parametrize("ground", [217, 60])
def test_a_band_edge_never_steps_faster_than_the_token(ground):
    """The panel's lid case: a split ground's INK band on a 217 ground (and a dark one) with the comparison's old
    40 design px feather."""
    w, h = 1440, 1800
    im = Image.new("RGB", (w, h), (ground, ground, ground))
    out = multi_format.scrim(im, 1000, h, feather=53, target=0.0, cap=1.0, floor=1.0, colour=brand.INK2)
    assert _steepest_into_band(out, 0, h, [700]) <= _cap(w) + 1.0           # +1: 8-bit rounding
    assert band_edge.luma_rows(out)[1100, 700] < 30                            # the band is still INK


def test_a_dark_ground_keeps_its_designed_feather_value_for_value():
    """Where the designed feather already meets the token, the band is the pre-round-7 one, pixel for pixel."""
    w, h = 1440, 1800
    im = Image.new("RGB", (w, h), (40, 40, 40))
    out = np.asarray(multi_format.scrim(im, 1000, h, feather=240, target=0.0, cap=1.0, floor=1.0,
                                        colour=brand.INK2), np.int16)
    y = np.arange(h, dtype=np.float32)
    v = np.clip(np.where(y >= 1000, 1.0, 1 - (1000 - y) / 240), 0, 1)
    v = v * v * (3 - 2 * v)
    a = (v * 255).astype(np.uint8).astype(np.float32)[:, None, None] / 255
    old = np.asarray(Image.alpha_composite(im.convert("RGBA"), Image.merge("RGBA", (
        *Image.new("RGB", (w, h), brand.INK2).split(),
        Image.fromarray((a[:, 0, 0] * 255).astype(np.uint8).reshape(h, 1)).resize((w, h))))).convert("RGB"),
        np.int16)
    assert np.abs(out - old).max() <= 1


def _photo_file(tmp_path: Path, name: str, colour, size=(1600, 1200), bright_right=False) -> Path:
    im = Image.new("RGB", size, colour)
    if bright_right:   # the Richat s4 case: bright sand at the right margin over a darker field
        ImageDraw.Draw(im).rectangle((int(size[0] * 0.85), 0, size[0], size[1]), fill=(225, 217, 200))
    p = tmp_path / name
    im.save(p, quality=95)
    return p


@pytest.mark.parametrize("two", [False, True])
def test_the_split_and_comparison_grounds_have_no_lid(tmp_path, two):
    """Richat IG s4 / s5: the comparison's lower photo faded into the band over 40 design px (6.1 luma / px at
    the bright right margin, x 1370-1435). Every column now stays under the token."""
    p1 = _photo_file(tmp_path, "a.jpg", (120, 90, 160))
    p2 = _photo_file(tmp_path, "b.jpg", (110, 80, 100), bright_right=True)
    gen = multi_format.MultiFormatGenerator()
    out = gen._split_ground(p1 if two else p2, 1440, 1800, p2 if two else None)
    band_top = gen._px(1440)(multi_format.MultiFormatGenerator.SPLIT_PHOTO_H + 60)
    # from below the comparison's gutter (a deliberate window edge, y ~460-492) into the band
    assert _steepest_into_band(out, band_top - 400, band_top + 4, [300, 1400]) <= _cap(1440) + 1.0


def test_two_photographs_are_two_windows_on_the_gutter_token(tmp_path):
    p1 = _photo_file(tmp_path, "a.jpg", (200, 200, 200))
    p2 = _photo_file(tmp_path, "b.jpg", (200, 200, 200))
    out = multi_format.MultiFormatGenerator()._split_ground(p1, 1440, 1800, p2)
    col = band_edge.luma_rows(out)[:700, 720]
    gutter = int((col < 60).sum())
    assert gutter == multi_format.MultiFormatGenerator._px(1440)(brand_system.tok("plate.gutter"))


@pytest.mark.parametrize("ground", [(243, 237, 226), (255, 255, 255)])
def test_the_end_screen_field_stays_within_the_field_gate(ground):
    """Tokens sweep #4: the 16:9 end screen's dim field stepped 0.558 (bone) / 0.595 (white) luma per px across its
    column ramp, over the gate scrim.slope_max (0.5)."""
    base = Image.new("RGB", (end_card.LANDSCAPE_WIDTH, end_card.LANDSCAPE_HEIGHT), ground)
    shape, _, _ = end_card.fit_dim(base)
    dim = shape * end_card._scrim(base.width)[None, :]
    ink = band_edge.luma_rows(np.asarray(brand.INK, np.float32)[None, None, :])[0, 0]
    lum = band_edge.luma_rows(base)[0, 0] * (1 - dim) + ink * dim
    assert np.abs(np.diff(lum, axis=1)).max() <= brand_system.tok("scrim.slope_max")
    assert np.abs(np.diff(lum, axis=0)).max() <= brand_system.tok("scrim.slope_max")


# ------------------------------------------------------------------------------------------- F17: the myth plate

def test_the_myth_plate_edges_are_feathered_by_the_same_token(tmp_path):
    from src import design_cards as dc
    photo = _photo_file(tmp_path, "bright.jpg", (236, 232, 222), size=(1200, 2000))
    geo = dc._Geo(1080, 1920)
    calls = []
    real = band_edge.feather

    def counted(*a, **k):
        calls.append(a[2])
        return real(*a, **k)
    band_edge.feather = counted
    try:
        _, rest = dc._myth_ground(photo, geo, 0.0, 0.8, 400, 900)
    finally:
        band_edge.feather = real
    assert {"up", "down"} <= set(calls)                                      # both edges go through the rule
    top, foot = geo.y(dc.MYTH_PANEL[0]), geo.y(dc.MYTH_PANEL[1])
    lum = band_edge.luma_rows(rest)
    for x in (200, 540, 900):
        assert band_edge.steepest(lum[top - 400:top + 20, x]) <= _cap(1080) + 1.0
        assert band_edge.steepest(lum[foot - 20:foot + 400, x]) <= _cap(1080) + 1.0
    assert lum[(top + foot) // 2].mean() < 60                                # the panel itself stays dark


# --------------------------------------------------------------------------------------------- F15: label halos

def _bridge(ground):
    im = Image.new("RGB", (1080, 1920), ground)
    stats = {}
    list(end_card.loop_bridge(im, im, "Send this on.", stats, line=False, follow=True))
    return stats


def test_follow_on_a_dark_ground_is_bone_within_the_label_halo():
    stats = _bridge((30, 30, 32))
    assert stats["follow"] and stats["follow_variant"] == "bone"
    assert stats["follow_halo"] <= brand_system.tok("halo.label_max")
    assert stats["contrast"] >= end_card.LOOP_FOLLOW_MIN


def test_follow_on_bright_stone_takes_the_ink_variant_and_no_halo_cloud():
    """The panel's case: the Aswan bridge's FOLLOW on bright stone carried a dark halo cloud at 1x."""
    stats = _bridge((236, 228, 210))
    assert stats["follow"] and stats["follow_variant"] == "ink"
    assert stats["follow_halo"] == 0.0 and stats["contrast"] >= end_card.LOOP_FOLLOW_MIN


def test_the_label_halo_is_one_spread_and_capped():
    spread, cap = brand_system.label_halo(64)
    assert spread == round(brand_system.tok("halo.label_spread") * 64) and cap <= 0.16
    assert brand_system.TOKEN_RANGES["halo.label_max"][1] <= brand_system.HALO_CAP   # never above the ruled cap
    # cycle 0 round 8 (operator ruling 2): both follow labels plan through typography.plan_label, which reads the
    # token (label_halo); updated from "each calls label_halo itself"
    def users(path, name):
        tree = ast.parse((ROOT / "src" / path).read_text(encoding="utf-8"))
        return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == name for c in ast.walk(n))}
    # rules2 R1: the end screen plans its labels as one set (typography.plan_label_set, which calls plan_label)
    assert "_follow_plate" in users("end_card.py", "plan_label") and "frames" in users("end_card.py", "plan_label_set")
    assert "plan_label" in users("typography.py", "label_halo") and "plan_label_set" in users("typography.py", "plan_label")


# ----------------------------------------------------------------------------------------- F9: hero windows

def _textured(w, h, seed=3, base=120):
    rng = np.random.default_rng(seed)
    a = np.clip(base + rng.normal(0, 28, (h // 6, w // 6, 3)), 0, 255).astype(np.uint8)
    return Image.fromarray(a).resize((w, h), Image.BICUBIC)


def test_a_two_panel_picture_keeps_one_panel():
    w, h = 1600, 1200
    im = _textured(w, h, 1, 70)
    im.paste(_textured(w, 560, 2, 190), (0, 640))
    ImageDraw.Draw(im).rectangle((0, 600, w, 640), fill=(250, 250, 250))       # a white gutter between panels
    out, box, bands = hero_window.clean(im)
    assert any(k == "row" for k, _, _ in bands)
    assert box[3] <= 600 or box[1] >= 640                                       # one panel, never the gutter
    assert hero_window.window_problems(out, [hero_window.panel_seams]) == []


def test_a_straight_edge_or_a_printed_line_in_one_picture_is_not_a_seam():
    """Measured on the pool (round 7): a roof's eaves across a drone shot and a chart's double rule."""
    w, h = 1600, 1200
    eaves = _textured(w, h, 4, 90)
    eaves.paste(_textured(w, 600, 5, 200), (0, 600))                            # no gutter: one picture's edge
    rule = Image.new("RGB", (w, h), (245, 245, 240))
    ImageDraw.Draw(rule).rectangle((780, 0, 784, h), fill=(20, 20, 20))         # a thin printed line
    # a clear sky (or a plain ground) meeting the picture is a horizon, not a gutter: the flat run is no thin band
    # (CI, round 7: then-and-now's empty-sky gate and a callout pair's bright sky were cut away)
    sky = np.full((2100, 1200, 3), 70, np.uint8)
    sky[:1000] = 250
    busy = np.full((3000, 4000, 3), 225, np.uint8)
    busy[900:] = np.random.default_rng(3).integers(30, 200, (2100, 4000, 3), dtype=np.uint8)
    for im in (eaves, rule, Image.fromarray(sky), Image.fromarray(busy)):
        assert hero_window.panel_seams(im, *hero_window._small(im)) == []


def test_a_banner_on_a_very_wide_frame_never_reaches_a_delivered_window(tmp_path):
    """Richat Story f1: a 9930 x 5190 frame's 112 px banner was 6.8 analysis rows at 600 px wide and passed."""
    from src.typography import cover_crop
    w, h = 4000, 2090
    im = _textured(w, h, 6, 110)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w, 45), fill=(176, 192, 40))
    d.text((12, 22), "Copernicus Sentinel data [2023], processed by Sentinel Hub" * 3,
           font=get_font("Montserrat", 28), fill=(10, 10, 10), anchor="lm")
    p = tmp_path / "wide.jpg"
    im.save(p, quality=95)
    window = cover_crop(p, (1080, 1920)).convert("RGB")
    lime = np.asarray(window, np.int16)
    banner = (np.abs(lime - np.array([176, 192, 40])).sum(axis=2) < 60).mean(axis=1)
    assert banner.max() < 0.3                                                   # no banner row in the window


def test_a_detector_plugs_in_and_every_crop_honours_it(tmp_path):
    """QF-144's seam: a registered detector's bands are cut from every hero crop with no surface change."""
    from src.typography import cover_crop
    p = tmp_path / "p.jpg"
    _textured(1600, 1200, 8, 120).save(p, quality=95)

    def top_third(im, small, scale):
        return [("row", 0, im.size[1] // 3)]
    plain = cover_crop(p, (400, 300))
    hero_window.register(top_third)
    try:
        cut = cover_crop(p, (400, 300))
    finally:
        hero_window.unregister(top_third)
    assert np.asarray(cut).tobytes() != np.asarray(plain).tobytes()
    assert np.asarray(cover_crop(p, (400, 300))).tobytes() == np.asarray(plain).tobytes()


# Every ImageOps.fit / hand-rolled cover crop in src, and why its input is already a clean window (or no photograph):
# a new crop site fails here until it is routed through photo_hygiene.without_caption_strip (the hero-window rule)
# or declared with its reason.
CROP_SITES = {
    "typography:cover_crop": "crops the hero window's clean region (without_caption_strip)",
    "then_now:frame_pair": "its sources come from then_now._load_clean (without_caption_strip)",
    "end_card:_cover": "a rendered video frame (the narration's graded last frame), not a source photograph",
    "visual_engine:redraw_cover": "engine._hero_source, cleaned by photo_hygiene.clean_hero",
    "visual_engine:VisualEngine.generate_thumbnails": "self._hero_source, cleaned by photo_hygiene.clean_hero",
}


def _fit_sites():
    found = set()
    for path in sorted((ROOT / "src").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, prefix, path=path):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visit(child, f"{prefix}.{child.name}" if prefix else child.name, path)
                elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and \
                        child.func.attr == "fit" and getattr(child.func.value, "id", "") == "ImageOps":
                    found.add(f"{path.stem}:{prefix}")
                    visit(child, prefix)
                else:
                    visit(child, prefix)
        visit(tree, "")
    return found


def test_no_hero_crop_bypasses_the_window_rule():
    assert _fit_sites() == set(CROP_SITES)


def test_the_shared_crops_go_through_the_window_rule(tmp_path, monkeypatch):
    from src import photo_grade, typography
    seen = []
    real = hero_window.clean
    monkeypatch.setattr(hero_window, "clean", lambda im: seen.append(im.size) or real(im))
    p = tmp_path / "p.jpg"
    _textured(800, 600).save(p)
    typography.cover_crop(p, (300, 400))
    photo_grade.cover(p, (300, 400))
    assert len(seen) == 2


# ----------------------------------------------------------------------------------------------- F19: margins

def test_the_9_16_margin_token_is_the_reel_box_inset():
    from src import design_cards as dc
    x0, _, x1, _ = safe_zones.safe_box("reel")
    assert safe_zones.text_box("reel")[0::2] == (x0 + 24, x1 - 24) == (137, 824)
    assert (dc.TALL_LEFT, dc.TALL_RIGHT) == (137, 824) and dc.HOOK_MEASURE == 687
    assert end_card.reel_column() == (480, 687)


def test_a_myth_card_sets_on_the_margin_column_and_falls_back_to_the_box_only_when_it_must(monkeypatch):
    from src import config
    from src import design_cards as dc
    from tests import test_freeze_cards_2026_11_04 as fc
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "test-only-fact-record-key-0123456789abcdef")
    plan = fc.plan_myth(fc.SHORT)
    assert plan.meta["anchors"]["margin"] == "token"
    text = [e for e in plan.elements if e.name.startswith(("myth_", "fact_")) and "strike" not in e.name]
    assert min(e.box[0] for e in text) >= dc.TALL_LEFT - 2 and max(e.box[2] for e in text) <= dc.TALL_RIGHT


def test_the_end_card_text_keeps_the_margin():
    lay = end_card.layout("Send this to the friend who still thinks the pyramids were built by slaves in a hurry.")
    x0, _, x1, _ = safe_zones.text_box("reel")
    assert x0 <= lay["box"][0] and lay["box"][2] <= x1
    assert all(w <= lay["max_width"] for w in lay["share_widths"])
