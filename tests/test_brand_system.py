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
        assert callouts._ease_back(t) == brand_system.ease_out_back(t)


# ---------------------------------------------------------------------------------------------- the masthead

@pytest.mark.parametrize("name,ground", list(_grounds()), ids=[n for n, _ in _grounds()])
def test_the_masthead_meets_its_gate_inside_the_ruled_scrim_and_halo(name, ground):
    size = brand_system.type_px("masthead", "carousel")
    x0, base = 107, 133
    out, plate, rec, box = masthead.draw(ground, x0, base, size, reach=base + 400)
    assert rec["scrim_peak"] <= brand_system.tok("scrim.masthead_peak") + 1e-9
    # ruling 4: above the cap only by the recorded exception, and never above its ceiling
    cap = brand_system.HALO_EXCEPTION_CAP if rec.get("halo_exception") else brand_system.HALO_CAP
    assert rec["halo"] <= cap + 1e-9
    if name != "busy":   # every photographic ground here is solvable; the synthetic noise field may fail closed
        assert rec["shown"], f"{name}: {rec}"
    if not rec["shown"]:
        assert out is ground and plate is None and box is None and "reason" in rec
        return
    assert plate.text == brand.MASTHEAD                                    # drawn == the brand's
    assert tuple(plate.fill) == tuple(brand_system.colour_of(
        "masthead.colour_dark" if rec["variant"] == "dark" else "masthead.colour_light"))
    gate = brand_system.tok("contrast.masthead_gate")
    assert _independent_local_min(out, plate) >= gate - 0.05               # on the delivered pixels, letter by letter
    col = np.asarray(out.convert("L"), dtype=np.float32)[:, 1300]
    src = np.asarray(ground.convert("L"), dtype=np.float32)[:, 1300]
    if rec["variant"] == "dark" and rec["scrim_peak"] > 0 and np.ptp(src) == 0:   # a flat ground shows the shape
        shade = src - col
        assert np.all(np.diff(shade[:600]) <= 1)                           # never more shade going down (rounding)
        assert np.abs(np.diff(col[: base + 400])).max() < 3                  # a gradient, no edge
    assert box[0] <= x0 + 1 and box[2] > box[0]


# ------------------------------------------------- the adversary's grounds (cycle 0 round 2; failed on 7652f083)

def _independent_local_min(out, plate) -> float:
    """The adversary's probe, independent of src/masthead.py: the ring rule per 24 px window of the name."""
    import cv2

    from src.typography import _lin
    ox, oy = plate.origin
    h, w = plate.glyphs.shape
    region = np.asarray(out.convert("RGB"))[oy:oy + h, ox:ox + w]
    g = (plate.glyphs > 127).astype("uint8")
    ring = cv2.dilate(g, np.ones((21, 21), np.uint8)) - cv2.dilate(g, np.ones((5, 5), np.uint8))
    inner = cv2.erode(g, np.ones((3, 3), np.uint8))
    vals = []
    for start in range(0, w, 24):
        rr = region[:, start:start + 24]
        bg, fg = rr[ring[:, start:start + 24] > 0], rr[inner[:, start:start + 24] > 0]
        if len(bg) >= 50 and len(fg) >= 8:
            lb = float(np.percentile(_lin(bg), 90 if plate.light else 10))
            lt = float(np.median(_lin(fg)))
            vals.append((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05))
    return min(vals)


def _adversary_grounds():
    from PIL import ImageDraw
    W, H = 1440, 700
    size = brand_system.type_px("masthead", "carousel")
    x, baseline = 107, 133
    spec = brand_system.masthead_spec(size)
    name_x = round(x + 2 * spec["mark_r"] + spec["gap"])
    yy, xx = np.indices((H, W))
    out = {"dark": Image.new("RGB", (W, H), brand.INK), "near_white": Image.new("RGB", (W, H), (254, 254, 252)),
           "yellow": Image.new("RGB", (W, H), (255, 255, 0)), "gold": Image.new("RGB", (W, H), brand.GOLD),
           "checker_2px": Image.fromarray(np.repeat(np.where((xx // 2 + yy // 2) % 2, 255, 0)[..., None], 3,
                                                    axis=2).astype("uint8")),
           "horizontal_gradient": Image.fromarray(np.repeat(np.broadcast_to(np.linspace(0, 255, W)[None, :], (H, W))
                                                            [..., None], 3, axis=2).astype("uint8"))}
    for width in (12, 24, 36):
        im = Image.new("RGB", (W, H), brand.INK)
        ImageDraw.Draw(im).rectangle((name_x - 3, 0, name_x + width, H), fill=brand.BONE)
        out[f"bright_stripe_{width}"] = im
    im = Image.new("RGB", (W, H), brand.INK)
    ImageDraw.Draw(im).rectangle((x - 5, 0, round(x + 2 * spec["mark_r"] + 5), H), fill=brand.GOLD)
    out["gold_band_under_mark"] = im
    im = Image.new("RGB", (W, H), brand.INK)
    ImageDraw.Draw(im).rectangle((0, round(baseline - 20), W, round(baseline - 15)), fill=brand.BONE)
    out["bright_horizontal_band"] = im
    return out


ADVERSARY = _adversary_grounds()


@pytest.mark.parametrize("label", sorted(ADVERSARY))
def test_the_masthead_fails_closed_and_is_judged_letter_by_letter(label):
    """Findings 1 and 2: no drawn masthead under the gate on ANY window of its name (a checkerboard drew 3.74:1; a
    bright stripe erased the first letter at 1:1 while the record said 16.6:1); below it nothing is drawn and the
    record says why."""
    ground = ADVERSARY[label]
    size, x, baseline = brand_system.type_px("masthead", "carousel"), 107, 133
    out, plate, rec, _ = masthead.draw(ground, x, baseline, size, reach=baseline + 400)
    gate = brand_system.tok("contrast.masthead_gate")
    if not rec["shown"]:
        assert out is ground and plate is None and rec["contrast"] < gate and "reason" in rec
        return
    assert _independent_local_min(out, plate) >= gate - 0.05, rec
    assert rec["contrast"] >= gate and rec["scrim_peak"] <= 0.60 + 1e-9 and rec["halo"] <= (0.24 if rec.get("halo_exception") else 0.16) + 1e-9   # ruling 4


@pytest.mark.parametrize("label", sorted(ADVERSARY))
def test_the_mark_keeps_its_ink_edge_and_its_contrast(label):
    """Finding 3: the INK edge survives the fill (it was overwritten everywhere; on a gold band the mark vanished), and
    the mark meets the non-text gate on every ground the masthead is drawn on."""
    from PIL import ImageDraw
    ground = ADVERSARY[label]
    size, x, baseline = brand_system.type_px("masthead", "carousel"), 107, 133
    out, _, rec, _ = masthead.draw(ground, x, baseline, size, reach=baseline + 400)
    if not rec["shown"]:
        return
    diamond, _ = masthead.mark_geometry(x, baseline, size)
    filled = out.copy()
    ImageDraw.Draw(filled).polygon(diamond, fill=brand.GOLD)
    assert not np.array_equal(np.asarray(out), np.asarray(filled))         # the edge is there
    mask = Image.new("L", out.size)
    ImageDraw.Draw(mask).polygon(diamond, fill=255)
    inside = np.asarray(mask) > 0
    assert np.count_nonzero(np.any(np.asarray(out) != np.asarray(ground), axis=2) & inside) > 0   # the mark is drawn
    assert rec["mark_contrast"] >= brand_system.tok("contrast.mark")


def test_a_surface_records_an_undrawn_masthead(tmp_path, monkeypatch):
    """Fail closed end to end: the carousel and the YouTube thumbnail keep the record of a masthead not drawn."""
    from src import multi_format, youtube_thumbnail
    calls = []

    def refuse(im, *a, **k):
        calls.append(1)
        return im, None, {"shown": False, "reason": "test: under the gate", "contrast": 1.0}, None
    monkeypatch.setattr(masthead, "draw", refuse)
    sky = tmp_path / "sky.jpg"
    Image.new("RGB", (1600, 2000), (120, 170, 225)).save(sky)
    _, lay = multi_format.MultiFormatGenerator()._render_carousel_slide(
        "immersive", sky, sky, {"heading": "A HEADING", "body": "A body line."}, 1, 7, {}, "", False, subject="S")
    assert not any(e["name"] == "header_brand" for e in lay.elements)
    assert lay.meta["masthead"]["shown"] is False and "reason" in lay.meta["masthead"]
    meta: dict = {}
    _, plates = youtube_thumbnail.render(Image.open(sky), {"title": "THE EYE"}, meta=meta)
    assert "brand" not in dict(plates) and meta["masthead"]["shown"] is False
    assert len(calls) >= 2


# ------------------------------------------------- every runtime token drives its renderer (finding 4)

def _kicker_width():
    from src import multi_format, typography
    return multi_format.kicker_width("THE RIDDLE", typography.get_font(brand.TEXT_FONT, 45, weight=800))


def _carousel_kicker_pixels():
    from src import multi_format, typography
    im = Image.new("RGB", (900, 200), (20, 20, 20))
    out, _ = multi_format.draw_kicker(im, 40, 120, "THE RIDDLE", typography.get_font(brand.TEXT_FONT, 45, weight=800))
    return np.asarray(out).tobytes()


def _end_screen_kicker():
    from src import end_card
    k = end_card.end_screen_extras()["kicker"]
    return (round(k["font"].getlength(k["text"]), 2), round(k["track"], 3), k["rule"], k["text_xy"])


def _masthead_pixels(ground=(120, 170, 225), size=53.3):
    out, _, rec, _ = masthead.draw(Image.new("RGB", (900, 400), ground), 40, 100, size, reach=500)
    return np.asarray(out).tobytes(), rec.get("halo"), rec.get("variant")


def _callout_ease():
    from src import callouts
    return callouts._ease_back(0.5)


def _end_screen_rise():
    from src import end_card
    return end_card.kicker_rise(0.25)


def _graphics():
    from src import graphics
    return graphics


def _ruler_zero_box():
    # cycle 0 round 7 (F25): a ruler's "0 FT" as graphics.ruler_label sets it (number and unit joined)
    g = _graphics()
    return g.ruler_label(Image.new("RGB", (600, 200), (14, 18, 24)), 400, 100, "0 FT", g.data_font(34),
                         brand.STONE_LT).box


def _place_plate(ground=(108, 108, 108)):
    # cycle 0 round 7 (F14): a place label on a mid ground that needs a halo (its fill, halo and crop)
    g = _graphics()
    p = g.place_plate(Image.new("RGB", (900, 300), ground), (100, 150), "CATHERINE PALACE", g.place_font(34), "ls")
    return p.fill, p.halo, p.glyphs.shape, getattr(p, "halo_exception", False)


def _held_label():
    # cycle 0 round 8 (operator ruling 2): a place label on a busy ground (black and white stripes, 3 px) that neither
    # bone (halo <= 0.16) nor INK type carries takes the held INK plate: its pixels as drawn
    g = _graphics()
    arr = np.zeros((300, 900, 3), np.uint8)
    arr[::6] = arr[1::6] = arr[2::6] = 255
    im = Image.fromarray(arr)
    p = g.place_plate(im, (100, 150), "CATHERINE PALACE", g.place_font(34), "ls")
    return p.variant, np.asarray(p.draw(im)).tobytes()


def _band_edge_profile():
    from src import multi_format
    im = Image.new("RGB", (400, 1800), (217, 217, 217))
    out = multi_format.scrim(im, 1000, 1800, feather=53, target=0.0, cap=1.0, floor=1.0)
    return np.asarray(out)[:, 200].tobytes()


def _follow_label():
    """The end card's follow line (an overlay label) planned on a mid ground: its halo's spread and alpha."""
    from src import end_card
    from src.brand_system import label_halo
    from src.typography import get_font, plan_text
    font = get_font(brand.TEXT_FONT, 64, weight=800)
    spread, cap = label_halo(64)
    p = plan_text(Image.new("RGB", (1080, 400), (150, 150, 150)), (540, 200), "FOLLOW", font, brand.BONE, "ms",
                  64 * end_card.FOLLOW_TRACK, halo_px=spread, max_halo=cap)
    return p.halo, np.asarray(p.halo_mask).shape, int(np.asarray(p.halo_mask).sum())


def _comparison_ground():
    import tempfile

    from src import multi_format
    with tempfile.TemporaryDirectory() as d:
        photo = Path(d) / "p.png"
        Image.new("RGB", (800, 600), (200, 200, 200)).save(photo)
        out = multi_format.MultiFormatGenerator()._split_ground(photo, 1440, 1800, photo)
    return np.asarray(out)[:, 720].tobytes()


def _body_gap_carousel():
    from src.multi_format import MultiFormatGenerator
    g = MultiFormatGenerator()
    return g._content_frame(g._px(1440), False, 2)["heading_last"]


def _body_gap_story():
    import tempfile

    from src.multi_format import MultiFormatGenerator
    photo = Path(tempfile.gettempdir()) / "rr_probe_story_ground.png"
    if not photo.exists():
        Image.new("RGB", (1080, 1920), (40, 40, 44)).save(photo)
    _, lay = MultiFormatGenerator()._render_story_frame(
        photo, {"kicker": "K", "line1": "Old rooms", "line2": "under salt", "body": "A short body."}, 0, 2, {})
    return min(p.xy[1] for p in lay.plates["body"]) - max(p.xy[1] for p in lay.plates["title_2"])


def _lockup_ratio():
    from PIL import ImageDraw

    from src import cover_design
    got, _ = cover_design._lockup(ImageDraw.Draw(Image.new("RGB", (8, 8))), {"line1": "OLD", "line2": "SALT"}, 900, 100)
    return got["line2"][0].size


def _display_raster():
    """Cycle 0 round 8 (ruling 6): the px the display face is rasterised at for the reference size 150."""
    from src.font_engine import raster_px
    from src.typography import get_font
    return raster_px(get_font(brand.DISPLAY_FONT, 150))


def _kickerless_headline():
    """Ruling 5: a kickerless one-field headline's size and rows (the larger size on two lines first)."""
    from PIL import ImageDraw

    from src import youtube_thumbnail
    _, _, _, rec = youtube_thumbnail._lockup(ImageDraw.Draw(Image.new("RGB", (8, 8))), "", "STONE GIANTS",
                                             904, kickerless=True)
    return rec["sizes"], rec["rows"]


def _card_centre():
    from src import design_cards
    return design_cards._chapter_tall_block(120, ["A B"])[2]


def _end_card_frame(k: int):
    """Frame k of the 9:16 end card over a mid ground (SYS-E-1: the reveal's tokens move it)."""
    from src import end_card
    for i, f in enumerate(end_card.frames(Image.new("RGB", (1080, 1920), (90, 96, 104)), "Send this to a friend.")):
        if i == k:
            return np.asarray(f).tobytes()
    return None


def _bridge_crossfade(line: bool):
    from src import end_card
    ground = Image.new("RGB", (1080, 1920), (90, 96, 104))
    st: dict = {}
    for _ in end_card.loop_bridge(ground, ground, "Send this to a friend.", st, line=line):
        pass
    return st["crossfade"]


def _map_base_sum():
    """Cycle 3 (SYS-A-4): relief_base on a synthetic home and a neighbour island (its coast takes the coast mark)."""
    import math

    import numpy as np

    from src import geo, graphics

    def blob(lon, lat, a, b):
        return [(lon + a * math.cos(2 * math.pi * i / 72), lat + b * math.sin(2 * math.pi * i / 72)) for i in range(72)]

    def country(name, ring):
        return {"names": {name}, "sovereign": name, "polygons": [[ring]], "size": 90, "display": name.title(),
                "label_at": None, "label_rank": 9, "type": "Sovereign country", "extent": (20.0, 35.0, 40.0, 45.0)}
    home, other = country("homeland", blob(28.0, 40.0, 3.0, 2.0)), country("other", blob(34.0, 40.0, 2.0, 2.0))
    bw, bh, kx = 600, 400, 600 / 16.0
    coslat = math.cos(math.radians(40.0))

    def project(lon, lat):
        return bw / 2 + (lon - 31.0) * kx * coslat, bh / 2 - (lat - 40.0) * kx
    token = graphics._RELIEF_CTX.set(graphics.ReliefContext(base_per_px=1.0, wide=False))
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(geo, "available", lambda: True)
            mp.setattr(geo, "countries", lambda: [home, other])
            mp.setattr(geo, "view_polygons", lambda *a, **k: [(c, c["polygons"][0]) for c in (home, other)])
            mp.setattr(graphics, "_relief", lambda: None)
            arr = np.asarray(graphics.relief_base(40.0, 31.0, bw, bh, kx, coslat, home, project, (0, 0, bw, bh)))
    finally:
        graphics._RELIEF_CTX.reset(token)
    return int(arr.astype(np.int64).sum())


def _globe_sum():
    import numpy as np

    from src import graphics
    return int(np.asarray(graphics.globe_inset(160, 20.0, 45.0, None, (15.0, 40.0, 25.0, 50.0))).astype(np.int64).sum())


def _youtube_mark_room():
    """SYS-B-2: the YouTube masthead's room over a wide hook row (the renderer's _mark_room)."""
    from types import SimpleNamespace

    from src import youtube_thumbnail
    from src.typography import get_font
    p = SimpleNamespace(empty=False, font=get_font(brand.DISPLAY_FONT, 190), text="THE STONE GIANTS", anchor="ls",
                        track=0, xy=(96, 600))
    return youtube_thumbnail._mark_room([("title_1", p)])


TOKEN_PROBES = {   # token -> (an override value, a probe of the renderer that reads it)
    "edge.slope_max": (0.6, _band_edge_profile),
    "halo.label_max": (0.04, lambda: _follow_label()[0]),
    "halo.label_spread": (0.5, lambda: _follow_label()[1:]),
    "plate.gutter": (48, _comparison_ground),
    "halo.step": (0.05, lambda: _masthead_pixels()[1]),
    "plate.shade": ("CORAL", lambda: _masthead_pixels()[0]),
    "masthead.weight": (400, lambda: masthead.group_width(64)),
    "masthead.track": (0.2, lambda: masthead.group_width(64)),
    "masthead.mark_r": (0.5, lambda: masthead.group_width(64)),
    "masthead.gap": (1.0, lambda: masthead.group_width(64)),
    "masthead.mark_edge": (0.10, lambda: _masthead_pixels((24, 28, 34))[0]),   # <= 0.35 x mark_r (TOKEN_JOINT)
    "masthead.colour_dark": ("GOLD_LIGHT", lambda: _masthead_pixels((24, 28, 34))[0]),
    "masthead.colour_light": ("CORAL", lambda: _masthead_pixels((250, 250, 248))[0]),
    "masthead.fit_floor": (1.0, lambda: masthead.fit_size(64, 0.9 * masthead.group_width(64))),
    "kicker.weight": (600, _end_screen_kicker),
    "kicker.track": (0.3, _kicker_width),
    "kicker.rule": (2.0, _kicker_width),
    "kicker.rule_gap": (1.5, _kicker_width),
    "kicker.rule_thick": (0.4, _carousel_kicker_pixels),
    "kicker.colour": ("TEAL", _carousel_kicker_pixels),
    "kicker.gap": (1.2, lambda: __import__("src.multi_format", fromlist=["x"]).kicker_gap(40)),
    "band.body_gap": (2.6, _body_gap_carousel),
    "band.body_gap_tall": (2.4, _body_gap_story),
    "lockup.line_ratio": (1.4, _lockup_ratio),
    "card.tall_centre": (0.32, _card_centre),
    "motion.back": (4.0, _callout_ease),
    "motion.rise_s": (20, _end_screen_rise),
    # cycle 0 round 7 (lane TRUTH): figures, the unit joiner and the place-label role
    "figure.face": ("data", lambda: _graphics().figure_font(40).getname()),
    "figure.colour": ("TEAL", lambda: _graphics().figure_fill()),
    "figure.conversion": (0.3, lambda: _graphics().conversion_px(76)),
    "data.unit_gap": (0.5, _ruler_zero_box),
    "figure.dash_gap_em": (0.12, lambda: __import__("src.typography", fromlist=["x"]).get_font(
        brand.DISPLAY_FONT, 200).getlength("94\u2013104")),
    "place.weight": (500, lambda: _graphics().place_font(34).getlength("CATHERINE PALACE")),
    "place.px": (40, lambda: _graphics().place_steps(1080, 1920)),
    "place.halo_px": (12, _place_plate),
    "place.halo_max": (0.08, _place_plate),
    # cycle 0 round 8 (lane TYPE): the display face's cap share (ruling 6) and the kickerless headline (ruling 5)
    "type.display_cap": (0.8, _display_raster),
    "lockup.kickerless_up": (0, _kickerless_headline),
    # cycle 0 round 8 (lane RULES): the held plate behind an overlay label, and the site's own place-label step
    "plate.held": (0.95, _held_label),
    "plate.radius": (30, _held_label),
    "plate.label_pad": (0.6, _held_label),
    "place.site_step": (1.2, lambda: _graphics().site_steps(1080, 1920)),
    "motion.act_dissolve": (0.8, lambda: __import__("src.video_composer", fromlist=["x"]).act_dissolve_s()),
    # SYS batch 2 (SYS-E-1 / SYS-F-1): the reveal, the bridge, the push and the hook, live
    "motion.reveal": (0.7, lambda: _end_card_frame(6)),                 # the masthead's fade at 0.2 s
    "motion.word_stagger": (0.1, lambda: __import__("src.end_card", fromlist=["x"]).word_at(3)),
    "motion.word_in": (0.5, lambda: __import__("src.end_card", fromlist=["x"]).share_done(4)),
    "motion.line_in": (0.5, lambda: _end_card_frame(51)),               # the follow line rising into the bell
    "motion.rise_l": (30, lambda: _end_card_frame(16)),                 # the share words rising
    "motion.rise_m": (24, lambda: _end_card_frame(52)),                 # the follow line rising
    "motion.loop_crossfade": (0.6, lambda: _bridge_crossfade(True)),
    "motion.loop_crossfade_visual": (0.8, lambda: _bridge_crossfade(False)),
    "motion.push_per_frame": (0.002, lambda: __import__("src.still_camera", fromlist=["x"]).motion_for(
        "push", 150, lead=True).start_velocity),
    "motion.push_cap": (1.1, lambda: __import__("src.still_camera", fromlist=["x"]).endpoint_for("push", 300)),
    "motion.hook_hold": (0.9, lambda: __import__("src.design_cards", fromlist=["x"])._hook_ground_alpha(1.0)),
    "motion.hook_fade": (0.25, lambda: __import__("src.design_cards", fromlist=["x"])._hook_alpha(1.15)),
    "masthead.hook_ratio_max": (0.62, _youtube_mark_room),
    "map.home_line_w": (3.0, _map_base_sum),
    "map.home_edge_w": (2.5, _map_base_sum),
    "map.home_edge_alpha": (0.9, _map_base_sum),
    "map.coast_w": (3.0, _map_base_sum),
    "globe.limb_dark": (0.05, _globe_sum),
    "lockup.line_gap": (0.25, lambda: __import__("src.youtube_thumbnail", fromlist=["x"]).line_pitch()),
}


def test_every_runtime_token_has_a_probe():
    assert set(TOKEN_PROBES) == set(brand_system.SYSTEM_TOKENS)
    assert not set(brand_system.SPEC) & set(brand_system.SYSTEM_TOKENS)
    with pytest.raises(ValueError, match="not runtime tokens"):
        brand.DEFAULT_BRAND.derive("spec override", system={"plate.panel": 0.5})


@pytest.mark.parametrize("token", sorted(TOKEN_PROBES))
def test_each_runtime_token_changes_what_its_renderer_draws(token):
    value, probe = TOKEN_PROBES[token]
    before = probe()
    with brand.use(brand.DEFAULT_BRAND.derive(f"probe {token}", system={token: value})):
        during = probe()
    assert during != before, token
    assert probe() == before                                               # and back with the default brand


# ------------------------------------------------- one size per role, or a declared deviation (finding 5)

def test_every_surface_size_system_outside_the_ladder_is_declared():
    import importlib

    from src import design_cards
    for surface, (role, where, sizes, group) in brand_system.DEVIATIONS.items():
        assert role in brand_system.FACE and group in "ABCDEF", surface
        mod, _, const = where.partition(".")
        if const.isidentifier() and const.isupper():                      # an importable constant: it must match
            held = getattr(importlib.import_module(f"src.{mod}"), const)
            held = tuple(held.values()) if isinstance(held, dict) else tuple(held)
            assert held == sizes, (surface, held)
    ladder = set(brand_system.TYPE["tall"].values()) | set(brand_system.TYPE_FALLBACK["tall"].values())
    declared = set()
    for _, _, sizes, _ in brand_system.DEVIATIONS.values():
        for t in (sizes if isinstance(sizes[0], tuple) else (sizes,)):
            declared |= set(t)
    for sizes in design_cards.HOOK_SIZES.values():                        # the adversary's 144 px hook: declared
        assert all(s in ladder or s in declared for s in sizes)
    assert all(s in declared for s in design_cards.CHAPTER_WIDE_ONE + design_cards.CHAPTER_WIDE_TWO)


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
