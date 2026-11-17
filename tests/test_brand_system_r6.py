"""Design-freeze cycle 0, round 6 (2026-11-16): operator ruling 4 and the Codex re-check of a1c7c68a
(queue CYCLE0-adversary-r5.codex.md).

- Ruling 4: the masthead halo cap is 0.16 everywhere; a masthead that reaches the 5.5 gate under no ruled step (scrim
  0.60, halo 0.16, levels, light variant) takes the LEAST halo above it up to 0.24 that does, recorded; a gate pair
  (brand_system.HALO_CAP, HALO_EXCEPTION_CAP) no brand moves.
- r5-1 (S1): every masthead and mark on every surface passes ONE gate (masthead.verify: room, name, mark) and the mark
  is drawn only by masthead.draw_mark: held statically (no other drawer of a diamond in src/) and at run time (every
  masthead name and mark drawn on the carousel, the FB gallery, YouTube, the end card, the end screen, the cover path
  and the hook path was verified first); a palette whose accent or edge misses the mark gate is refused by BrandConfig.
- r5-2 (S2): the fit ladder ends on its exact floor, and a refusal records the true floor.
- r5-3 (S2): the surfaces the type-size observer renders are enumerated from the code's own registries
  (tests/surface_registry.py); the dispatches they mirror are held equal to them here.
"""
from __future__ import annotations

import ast
import itertools
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand, brand_system, masthead

ROOT = Path(__file__).resolve().parent.parent
SIZE = brand_system.type_px("masthead", "carousel")
X, BASE, W, H = 107, 133, 1440, 700
GATE = brand_system.tok("contrast.masthead_gate")


def _mixed(sky: int, dark: int = 40, x0: int = 400) -> Image.Image:
    """A flat bright sky with a dark mass under some of the name's letters: bone needs the sky dark, INK the mass light."""
    a = np.full((H, W, 3), sky, np.uint8)
    a[:, x0:x0 + 200] = dark
    return Image.fromarray(a)


# ------------------------------------------------------------------ ruling 4: the halo exception

def test_the_halo_gate_pair_is_constant_and_no_brand_moves_it():
    assert brand_system.HALO_CAP == brand_system.GATES["halo.masthead_max"] == 0.16
    assert brand_system.HALO_EXCEPTION_CAP == brand_system.GATES["halo.masthead_exception_max"] == 0.24
    assert masthead.HALO_EXCEPTION_CAP == brand_system.HALO_EXCEPTION_CAP
    for key, value in (("halo.masthead_max", 0.3), ("halo.masthead_exception_max", 0.5), ("halo.exception_step", 0.1)):
        with pytest.raises(ValueError, match="gates"):
            brand.DEFAULT_BRAND.derive("moved", system={key: value})


@pytest.mark.parametrize("ground", ["ink", "grey_200", "sky_215_mass"])
def test_no_exception_where_a_ruled_step_reaches_the_gate(ground):
    g = {"ink": Image.new("RGB", (W, H), brand.INK), "grey_200": Image.new("RGB", (W, H), (200, 200, 200)),
         "sky_215_mass": _mixed(215)}[ground]
    _, _, rec = masthead.plan(g, X, BASE, SIZE, reach=BASE + 400)
    assert rec["shown"] and rec["halo_exception"] is False
    assert rec["halo"] <= brand_system.HALO_CAP + 1e-9 and rec["contrast"] >= GATE


def test_the_exception_takes_the_least_halo_that_reaches_the_gate_and_records_it():
    ground, p, rec = masthead.plan(_mixed(230), X, BASE, SIZE, reach=BASE + 400)
    assert rec["shown"] and rec["halo_exception"] is True and rec["halo_exception_cap"] == 0.24
    assert brand_system.HALO_CAP < rec["halo"] <= brand_system.HALO_EXCEPTION_CAP + 1e-9
    assert rec["contrast"] >= GATE and rec["variant"] == "dark" and rec["scrim_peak"] == 0.60
    # the least: one exception step less misses the gate on every ground the exception tries (here, the one it chose)
    from src.typography import plan_text
    less = round(rec["halo"] - brand_system.GATES["halo.exception_step"], 2)
    spec = brand_system.masthead_spec(rec["size_px"])
    q = plan_text(ground, p.xy, p.text, p.font, p.fill, "ls", spec["track"], target=GATE, min_halo=less, max_halo=less)
    assert masthead.judge(ground, q) < GATE
    # drawn, its record says so
    _, _, drec, box = masthead.draw(_mixed(230), X, BASE, SIZE, reach=BASE + 400)
    assert drec["shown"] and drec["halo_exception"] is True and box is not None


def test_past_the_exception_the_masthead_still_fails_closed():
    g = _mixed(240)
    out, plate, rec, box = masthead.draw(g, X, BASE, SIZE, reach=BASE + 400)
    assert rec["shown"] is False and out is g and plate is None and box is None
    assert "halo exception up to 0.24" in rec["reason"] and rec["best_tried"] < GATE


def test_the_end_card_takes_the_same_exception():
    """masthead.exception_plate (the end card's form: a ground it cannot scrim) finds the same least halo the planner
    found, on the planner's chosen ground."""
    from src.typography import plan_text
    ground, p, rec = masthead.plan(_mixed(230), X, BASE, SIZE, reach=BASE + 400)
    spec = brand_system.masthead_spec(rec["size_px"])
    found = masthead.exception_plate(ground, lambda h: plan_text(ground, p.xy, p.text, p.font, p.fill, "ls",
                                                                  spec["track"], target=GATE, min_halo=h, max_halo=h))
    assert found is not None and found[0].halo == rec["halo"] and found[1] >= GATE
    assert masthead.exception_plate(_mixed(240), lambda h: plan_text(
        _mixed(240), p.xy, p.text, p.font, p.fill, "ls", spec["track"], target=GATE, min_halo=h, max_halo=h)) is None


# ------------------------------------------------------------------ r5-1: one gate for every masthead and mark

@pytest.mark.parametrize("palette", [{"GOLD": "INK"}, {"GOLD": (42, 44, 48)}, {"INK": (200, 196, 190)}],
                         ids=["gold_is_ink", "low_contrast_accent", "edge_like_bone"])
def test_a_palette_whose_mark_cannot_read_is_refused(palette):
    with pytest.raises(ValueError, match="mark gate"):
        brand.DEFAULT_BRAND.derive("unreadable mark", palette=palette)


def test_the_shipped_and_test_palettes_pass_the_mark_check():
    import sys
    sys.path.insert(0, str(ROOT / "docs/reviews/quality-fixes-2026-11/QF-35a"))
    import render_surfaces
    for b in (brand.DEFAULT_BRAND, render_surfaces.second_brand(brand), render_surfaces.third_brand(brand)):
        assert brand._ratio(b.colour("GOLD"), b.colour("INK")) >= 3.0 and brand._ratio(b.colour("INK"), b.colour("BONE")) >= 3.0


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["end_card", "end_screen"])
def test_the_end_card_fails_closed_when_its_mark_misses_the_gate(size, monkeypatch):
    from src import end_card
    monkeypatch.setattr(end_card, "SECONDS", 0.2)
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 0.2)
    monkeypatch.setattr(masthead, "mark_contrast", lambda *a, **k: (1.38, {"fill": 5, "edge": 0}))
    stats: dict = {}
    frames = list(end_card.frames(Image.new("RGB", size, brand.INK), "Share this with a friend.", stats))
    rec = stats["masthead"]
    assert rec["shown"] is False and "mark gate" in rec["reason"] and rec["mark_contrast"] == 1.38
    lay = end_card.layout(stats["text"], *size)
    cx, cy = lay["mark_centre"]
    last = np.asarray(frames[-1]).astype(int)
    gold = np.abs(last - np.array(brand.GOLD)).sum(axis=2) < 60
    r = int(lay["masthead"]["mark_r"]) + 4
    assert not gold[int(cy) - r:int(cy) + r, int(cx) - r:int(cx) + r].any()   # no mark drawn
    assert "brand" not in stats["contrast"]                                    # and no name


def _instrument(monkeypatch):
    """Record every masthead name and mark drawn, and every one masthead.verify passed."""
    from src import typography
    seen = {"verified_plates": set(), "verified_marks": set(), "names": [], "marks": []}
    real_verify, real_mark, real_draw = masthead.verify, masthead.draw_mark, typography.TextPlate.draw

    def key(diamond):
        return tuple((round(float(x), 2), round(float(y), 2)) for x, y in diamond)

    def verify(ground, plate, diamond, edge, rec, **k):
        out = real_verify(ground, plate, diamond, edge, rec, **k)
        if out is not None:
            seen["verified_plates"].add(id(plate))
            seen["verified_marks"].add(key(diamond))
        return out

    def outside_the_gate():   # src/masthead.py's own draws are its measurements (judge, verify) and verify's output
        return not sys._getframe(2).f_code.co_filename.endswith("masthead.py")

    def draw_mark(d, diamond, edge, *a, **k):
        if outside_the_gate():
            seen["marks"].append(key(diamond))
        return real_mark(d, diamond, edge, *a, **k)

    def draw(self, im, *a, **k):
        if self.text == brand.MASTHEAD and (a[:1] or [k.get("alpha", 1.0)])[0] > 0 and outside_the_gate():
            seen["names"].append(id(self))
        return real_draw(self, im, *a, **k)
    monkeypatch.setattr(masthead, "verify", verify)
    monkeypatch.setattr(masthead, "draw_mark", draw_mark)
    monkeypatch.setattr(typography.TextPlate, "draw", draw)
    return seen


def test_every_masthead_name_and_mark_drawn_was_verified_first(tmp_path, monkeypatch):
    """At run time, on every surface that draws a masthead (and on the two paths ruling 2 keeps unreached: the cover's
    and the hook's), each name plate drawn and each diamond drawn passed masthead.verify."""
    from src import cover_design, design_cards, end_card, youtube_thumbnail
    from src.multi_format import MultiFormatGenerator
    seen = _instrument(monkeypatch)
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1600, 2000), (150, 120, 90)).save(photo)
    card = {"heading": "IMPACT THEORY DISPROVED", "body": "The theory was disproved in 1969."}
    for fb in (False, True):
        MultiFormatGenerator()._render_carousel_slide("immersive", photo, photo, card, 1, 7, {}, "", fb, subject="EYE")
    youtube_thumbnail.render(Image.open(photo), {"title": "THE EYE OF THE SAHARA"}, meta={})
    monkeypatch.setattr(end_card, "SECONDS", 0.2)
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 0.2)
    for size in ((1080, 1920), (1920, 1080)):
        list(end_card.frames(Image.new("RGB", size, (60, 60, 60)), "Share this with a friend.", {}))
    from src.still_layout import Layout
    for paper in (False, True):   # the cover's path (unreached while every caller passes grid_delivery)
        lay = Layout("cover", (1080, 1920), (60, 60, 1020, 1860))
        ground = Image.new("RGB", (1080, 1920), brand.BONE if paper else (120, 140, 160))
        cover_design._brand(ground, lay, paper=paper)
    import tests.test_freeze_cards_2026_11_04 as fc
    monkeypatch.setattr(design_cards, "hook_masthead_shown", lambda: True)   # the hook's path, forced
    hook_photo = tmp_path / "h.jpg"
    Image.new("RGB", (1200, 2100), (70, 60, 50)).save(hook_photo)
    try:
        plan = fc.hook_plan(hook_photo)
        design_cards.frame_at(plan, plan.frames - 1)
    except design_cards.CardRefused as e:   # refused for its masthead, or by the grid proof (QF-87, ruling 2)
        assert "masthead" in str(e) or "profile grid: brand" in str(e)
    assert seen["names"] and seen["marks"]
    assert set(seen["names"]) <= seen["verified_plates"], "a masthead name was drawn without masthead.verify"
    assert set(seen["marks"]) <= seen["verified_marks"], "a mark was drawn without masthead.verify"


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def test_no_module_draws_the_mark_or_plans_the_name_outside_the_one_gate():
    """Statically: outside src/masthead.py no polygon is drawn in a colour from a diamond's points (the mark's only
    drawer is masthead.draw_mark), and every module that plans the masthead's name calls masthead.verify."""
    offenders, unverified = [], []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path.name == "masthead.py":
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for call in _calls(tree):
            f = call.func
            if isinstance(f, ast.Attribute) and f.attr == "polygon" and call.args:
                arg = ast.unparse(call.args[0])
                paint = [kw for kw in call.keywords if kw.arg in ("fill", "outline")
                         and not (isinstance(kw.value, ast.Constant) and kw.value.value in (0, 1, 255))]
                if "diamond" in arg and paint:
                    offenders.append(f"{path.name}:{call.lineno}")
        plans_name = any(
            isinstance(c.func, ast.Name) and c.func.id in ("plan_text", "place_text", "TextPlate") and len(c.args) >= 3
            and ast.unparse(c.args[2]) in ("brand.MASTHEAD", "mast['text']", 'mast["text"]')
            for c in _calls(tree))
        if plans_name and "masthead.verify(" not in src:
            unverified.append(path.name)
    assert offenders == [], f"the mark drawn outside masthead.draw_mark: {offenders}"
    assert unverified == [], f"the masthead's name planned without masthead.verify: {unverified}"


# ------------------------------------------------------------------ r5-2: the ladder's exact floor

def test_the_fit_ladder_ends_on_its_exact_floor():
    for size in (SIZE, 64.0, 72.0, 53.0):
        ladder = masthead.fit_ladder(size)
        assert ladder[0] == size and ladder[-1] == pytest.approx(brand_system.tok("masthead.fit_floor") * size)
        assert all(a > b for a, b in itertools.pairwise(ladder))


def test_a_name_that_fits_at_the_floor_is_drawn_and_a_refusal_names_the_true_floor(tmp_path):
    from src.multi_format import MultiFormatGenerator
    photo = tmp_path / "ink.png"
    Image.new("RGB", (1600, 2000), brand.INK).save(photo)
    floor = brand_system.tok("masthead.fit_floor") * SIZE
    for name, fits in (("W" * 22, True), ("W" * 23, False)):
        with brand.use(brand.DEFAULT_BRAND.derive("wide name", text={"masthead": name})):
            assert (masthead.group_width(floor) <= 960) is fits
            for fb in (False, True):
                _, lay = MultiFormatGenerator()._render_carousel_slide(
                    "immersive", photo, photo, {"heading": "OLD ROOMS", "body": "A short body."}, 1, 3, {}, "", fb,
                    subject="TEST SITE")
                rec = lay.meta["masthead"]
                if fits:
                    assert rec["shown"] and rec["size_px"] == pytest.approx(floor), rec
                else:
                    assert not rec["shown"] and rec["fit_floor_px"] == pytest.approx(floor, abs=1e-3)
                    assert f"floor ({floor:.1f} px" in rec["reason"] and rec["floor_width"] > rec["room"]


# ------------------------------------------------------------------ r5-3: the observer's surfaces come from the code

def test_the_carousel_registry_is_its_dispatch():
    from src.multi_format import MultiFormatGenerator
    tree = ast.parse((ROOT / "src" / "multi_format.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_render_carousel_slide")
    named = {c.comparators[0].value for c in ast.walk(fn) if isinstance(c, ast.Compare)
             and ast.unparse(c.left) == "archetype" and isinstance(c.ops[0], ast.Eq)
             and isinstance(c.comparators[0], ast.Constant)}
    assert set(MultiFormatGenerator.CAROUSEL_ARCHETYPES) == named | {"editorial_split"}   # the else draws the split


def test_the_cover_registry_is_its_dispatch():
    from src.visual_engine import RENDERABLE_STYLES
    tree = ast.parse((ROOT / "src" / "visual_engine.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "redraw_cover")
    keys = {k.value for d in ast.walk(fn) if isinstance(d, ast.Dict) for k, v in zip(d.keys, d.values)
            if isinstance(k, ast.Constant) and isinstance(v, ast.Attribute) and v.attr.startswith("_cover_")}
    assert set(RENDERABLE_STYLES) == keys | {"badge"}   # the .get default draws badge
    cover = (ROOT / "src" / "cover_design.py").read_text(encoding="utf-8")
    tree = ast.parse(cover)
    styles = {c.comparators[0].value for c in ast.walk(tree) if isinstance(c, ast.Compare)
              and ast.unparse(c.left) == "style" and isinstance(c.comparators[0], ast.Constant)}
    styles |= {e.value for c in ast.walk(tree) if isinstance(c, ast.Compare) and ast.unparse(c.left) == "style"
               and isinstance(c.comparators[0], ast.Tuple) for e in c.comparators[0].elts}
    assert styles <= set(RENDERABLE_STYLES), sorted(styles - set(RENDERABLE_STYLES))


def test_the_registry_has_an_input_for_every_kind_and_names_both_canvases():
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    import surface_registry as sr
    keys = sr.registry()
    assert sr.kinds(keys) == set(sr.inputs()), sorted(sr.kinds(keys) ^ set(sr.inputs()))
    for k in ("cover.monument:tall", "cover.lower_third:tall", "carousel.blueprint.facebook:carousel",
              "carousel.diagram.instagram:carousel", "design_cards.plan_quote_card:wide",
              "design_cards.plan_hook_frame:tall", "graphics.render_map:wide", "graphics.render_route:tall",
              "callouts.render:wide", "end_card.frames:wide"):
        assert k in keys, k
    assert "design_cards.plan_hook_frame:wide" not in keys   # PLAN_SIZES: the hook draws 9:16 only
