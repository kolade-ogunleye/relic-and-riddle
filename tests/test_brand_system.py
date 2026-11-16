"""Design-freeze cycle 0 (2026-11-15): the design SYSTEM on QF-35a's role tokens (src/brand_system.py) and the one
masthead treatment (src/masthead.py). Invariants only (gates met, drawn text == the brand's, bounded steps, one
vocabulary), never a pinned measured value.
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand, brand_system, masthead

ROOT = Path(__file__).resolve().parent.parent
RICHAT = ROOT / "docs/reviews/freeze-panel-2026-11-04/fixtures/stills/richat_structure_sahara_eye_01_commons_960.jpg"
DESIGN_MODULES = ("graphics", "design_cards", "multi_format", "cover_design", "youtube_thumbnail", "end_card",
                  "then_now", "callouts", "video_composer", "art_direction", "timeline_dense", "masthead")


def _grounds():
    """Worst-case grounds for the masthead: dark, mid grey, sky, near-white, and a real photograph (Richat)."""
    w, h = 1440, 1800
    yield "dark", Image.new("RGB", (w, h), (24, 28, 34))
    yield "mid", Image.new("RGB", (w, h), (128, 128, 128))
    yield "sky", Image.new("RGB", (w, h), (120, 170, 225))
    yield "near_white", Image.new("RGB", (w, h), (246, 246, 242))
    noise = np.random.default_rng(7).integers(90, 250, (h // 8, w // 8, 3), dtype=np.uint8)
    yield "busy", Image.fromarray(noise).resize((w, h), Image.NEAREST)
    yield "richat", Image.open(RICHAT).convert("RGB").resize((w, h))


# ---------------------------------------------------------------------------------------------- tokens and gates

def test_gates_are_constants_a_brand_cannot_move():
    assert brand_system.tok("contrast.text") == brand.TEXT_CONTRAST
    assert brand_system.tok("contrast.place") == brand.PLACE_TARGET
    assert brand_system.tok("contrast.masthead_gate") <= brand_system.tok("contrast.masthead_target")
    assert brand_system.tok("scrim.masthead_peak") <= 0.60 and brand_system.tok("halo.masthead_max") <= 0.16
    with pytest.raises(ValueError, match="gates"):
        brand.DEFAULT_BRAND.derive("lower gate", system={"contrast.masthead_gate": 3.0})


def test_colour_tokens_name_palette_roles():
    named = [k for k in brand_system.SYSTEM_TOKENS if k.endswith(("colour", "colour_dark", "colour_light", ".shade"))]
    assert named
    for k in named:
        assert brand_system.tok(k) in brand.BRAND_PALETTE, k
        assert brand_system.colour_of(k) == brand.active().colour(brand_system.tok(k))


def test_every_token_is_read_at_call_time_from_the_active_brand():
    size = 64
    before = masthead.group_width(size)
    with brand.use(brand.DEFAULT_BRAND.derive("wide track", system={"masthead.track": 0.2, "masthead.gap": 1.0})):
        assert brand_system.tok("masthead.track") == 0.2
        assert masthead.group_width(size) > before
    assert masthead.group_width(size) == before
    with brand.use(brand.DEFAULT_BRAND.derive("teal kicker", system={"kicker.colour": "TEAL"})):
        assert brand_system.colour_of("kicker.colour") == brand.TEAL


def test_no_design_module_copies_a_token_at_import():
    """A module-level tok(...) / colour_of(...) / masthead_spec(...) call freezes today's brand (GATES are constants
    and may be held)."""
    calls = {"tok", "colour_of", "masthead_spec"}
    bad = []
    for name in DESIGN_MODULES:
        tree = ast.parse((ROOT / "src" / f"{name}.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not isinstance(node, ast.ClassDef):
                    for d in node.args.defaults + node.args.kw_defaults:
                        for sub in ast.walk(d) if d is not None else ():
                            if isinstance(sub, ast.Call) and getattr(sub.func, "id", None) in calls:
                                bad.append(f"{name}:{node.lineno} default argument")
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and getattr(sub.func, "id", getattr(sub.func, "attr", None)) in calls:
                    bad.append(f"{name}:{sub.lineno}")
    assert bad == []


def test_the_type_table_is_one_size_per_role_with_floors_and_one_fallback():
    for canvas, roles in brand_system.TYPE.items():
        assert set(roles) == set(brand_system.FACE)
        for role, fb in brand_system.TYPE_FALLBACK[canvas].items():
            assert fb < roles[role]                        # a fallback is a step down, never a second first size
        for role, floor in brand_system.TYPE_FLOOR[canvas].items():
            assert roles[role] >= floor
            assert brand_system.TYPE_FALLBACK[canvas].get(role, floor) >= floor
        assert roles["display_xl"] > roles["display_l"] >= roles["display_m"] > roles["body"]   # hierarchy
    assert brand_system.TYPE["carousel"]["masthead"] == 40 * 4 / 3   # the 1080 design px, x 4/3 exactly
    for role in brand_system.FACE.values():
        assert role in brand.BRAND_FONTS


def test_one_line_end_vocabulary():
    from src import design_cards, multi_format
    never, last = brand_system.LINE_END_NEVER, brand_system.LINE_END_LAST_RESORT
    assert not never & last
    assert multi_format._DANGLING == never | last
    assert never <= design_cards.W1_STOPS <= never | last     # every surface refuses the never-list; never looser
    assert not brand_system.line_end_ok("of") and not brand_system.line_end_ok("IN")
    assert not brand_system.line_end_ok("and") and brand_system.line_end_ok("and", last_resort=True)
    assert brand_system.line_end_ok("SAHARA") and brand_system.line_end_ok("of,")


def test_named_eases_mean_one_thing_and_are_bounded():
    from src import callouts, end_card, graphics
    ts = np.linspace(0, 1, 101)
    for name, fn in brand_system.EASES.items():
        v = np.array([fn(t) for t in ts])
        assert v[0] == pytest.approx(0, abs=1e-9) and v[-1] == pytest.approx(1, abs=1e-9), name
        if name != "ease_out_back":
            assert np.all(np.diff(v) >= -1e-12), name       # monotonic
    back = max(brand_system.ease_out_back(t) for t in ts)
    assert 1.0 < back <= 1.1                                # one overshoot, about 8 %
    for t in ts:
        assert graphics._ease(t) == end_card.ease(t) == brand_system.ease_out(t)
        assert graphics._ease_io(t) == brand_system.ease_in_out(t)
        assert end_card.ease_io(t) == pytest.approx(brand_system.ease_in_out(t), abs=1e-12)   # same ease, other form
        assert callouts._ease_back(t) == brand_system.ease_out_back(t, callouts.BACK)


# ---------------------------------------------------------------------------------------------- the masthead

@pytest.mark.parametrize("name,ground", list(_grounds()), ids=[n for n, _ in _grounds()])
def test_the_masthead_meets_its_gate_inside_the_ruled_scrim_and_halo(name, ground):
    size = brand_system.type_px("masthead", "carousel")
    x0, base = 107, 133
    out, plate, rec, box = masthead.draw(ground, x0, base, size, reach=base + 400)
    assert plate.text == brand.MASTHEAD                                    # drawn == the brand's
    assert rec["scrim_peak"] <= brand_system.tok("scrim.masthead_peak") + 1e-9
    assert rec["halo"] <= brand_system.tok("halo.masthead_max") + 1e-9
    assert rec["variant"] in ("dark", "light")
    assert tuple(plate.fill) == tuple(brand_system.colour_of(
        "masthead.colour_dark" if rec["variant"] == "dark" else "masthead.colour_light"))
    measured = plate.measure(out)                                          # on the delivered pixels
    assert rec["below_gate"] or measured >= brand_system.tok("contrast.masthead_gate") - 0.05
    assert not rec["below_gate"], f"{name}: {rec}"                         # every one of these grounds is solvable
    # the scrim is a soft gradient: no row step steeper than the field limit, darkening only towards the top
    col = np.asarray(out.convert("L"), dtype=np.float32)[:, 1300]
    src = np.asarray(ground.convert("L"), dtype=np.float32)[:, 1300]
    if rec["variant"] == "dark" and rec["scrim_peak"] > 0 and np.ptp(src) == 0:   # a flat ground shows the shape
        shade = src - col
        assert np.all(np.diff(shade[:600]) <= 1)                           # never more shade going down (rounding)
        assert np.abs(np.diff(col[: base + 400])).max() < 3                  # a gradient, no edge
    assert box[0] <= x0 + 1 and box[2] > box[0]


def test_bone_is_kept_wherever_it_reaches_the_target_and_nothing_is_added_on_a_dark_ground():
    dark = Image.new("RGB", (1440, 1800), (24, 28, 34))
    ground, plate, rec = masthead.plan(dark, 107, 133, 64)
    assert rec == {**rec, "variant": "dark", "scrim_peak": 0.0, "halo": 0.0}
    assert np.array_equal(np.asarray(ground), np.asarray(dark))
    assert plate.contrast >= brand_system.tok("contrast.masthead_target")


def test_every_drawn_masthead_uses_the_one_treatment(tmp_path):
    """Carousel, YouTube thumbnail and end screen: the same face, weight, em track, mark and colour role."""
    from src import end_card, multi_format, youtube_thumbnail
    sky = tmp_path / "sky.jpg"
    Image.new("RGB", (1600, 2000), (120, 170, 225)).save(sky)
    _, lay = multi_format.MultiFormatGenerator()._render_carousel_slide(
        "immersive", sky, sky, {"heading": "A HEADING", "body": "A body line."}, 1, 7, {}, "", False, subject="S")
    el = next(e for e in lay.elements if e["name"] == "header_brand")
    assert el["masthead"]["variant"] in ("dark", "light") and not el["masthead"]["below_gate"]
    assert el["px"] == pytest.approx(brand_system.type_px("masthead", "carousel"), abs=0.1)
    assert el["contrast"] >= brand_system.tok("contrast.masthead_gate")

    meta: dict = {}
    youtube_thumbnail.render(Image.open(sky), {"title": "THE EYE OF THE SAHARA"}, meta=meta)
    assert meta["masthead"]["variant"] in ("dark", "light")
    m = end_card._masthead(740)
    assert m["track"] == pytest.approx(brand_system.tok("masthead.track") * m["size"])
    assert m["mark_r"] == round(brand_system.tok("masthead.mark_r") * m["size"])


def test_the_youtube_masthead_is_never_gold(tmp_path):
    from src import youtube_thumbnail
    meta: dict = {}
    _, plates = youtube_thumbnail.render(Image.new("RGB", (1920, 1080), (60, 60, 60)), {"title": "THE EYE"}, meta=meta)
    fill = dict(plates)["brand"].fill
    assert tuple(fill) != tuple(brand.GOLD)
    assert tuple(fill) in (tuple(brand_system.colour_of("masthead.colour_dark")),
                           tuple(brand_system.colour_of("masthead.colour_light")))


def test_the_end_screen_kicker_is_the_system_kicker():
    from src import end_card, typography
    k = end_card.end_screen_extras()["kicker"]
    assert Path(k["font"].path).name == Path(typography.get_font(brand.TEXT_FONT, 40, weight=800).path).name
    assert k["track"] == pytest.approx(brand_system.tok("kicker.track") * k["font"].size)
    x0, y0, x1, y1 = k["rule"]
    assert x0 == k["xy"][0] < x1 < k["text_xy"][0]                       # the rule leads the words
    assert y0 < k["xy"][1] and y1 >= y0 + 2                               # at mid-cap height, a flat bar


def test_the_masthead_ground_shade_is_ink_never_pure_black():
    white = Image.new("RGB", (64, 400), (255, 255, 255))
    out = masthead.top_scrim(white, 1.0, 400).convert("RGB")
    assert out.getpixel((10, 0)) == tuple(brand.INK)
