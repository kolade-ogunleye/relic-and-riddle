"""Design-freeze cycle 0, round 7, lane MEASURE: the measurement sweep's counterexamples (queue
CYCLE0-sweep-measure.codex.md), closed by two classes of the lander's:

(b) ONE per-glyph contrast method for ALL text (typography.glyph_contrast / text_contrast, inherited through
    TextPlate.solve and TextPlate.measure, and the masthead's glyph_contrast is that method): every glyph's ground
    under and around its ink, its worse tail taken BY THE RATIO for any fill, all pieces of one shaped glyph judged
    together, the ink read as drawn on delivered pixels, unrounded, never a crash.
(c) A fit is the DRAWN ink: the masthead's group width, a tracked run's ink bounds (combining marks included), the
    per-glyph masks for every anchor, and a fit helper never hands back a font whose ink overflows its room.

Every input is synthetic (stripes, flat grounds, specks); each test names the sweep finding it reproduces.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, masthead
from src import typography as t
from src.font_engine import draw_run, glyph_masks, run_ink_bounds, shape

ROOT = Path(__file__).resolve().parents[1]
SIZE = brand_system.type_px("masthead", "carousel")
GATE = brand_system.tok("contrast.masthead_gate")


def _lum(a):
    a = np.asarray(a, dtype=np.float64) / 255
    return np.where(a <= .04045, a / 12.92, ((a + .055) / 1.055) ** 2.4) @ [0.2126, 0.7152, 0.0722]


def _ratio(a, b):
    return (np.maximum(a, b) + .05) / (np.minimum(a, b) + .05)


def _name_plate(ground, fill=None, halo=0.0, xy=(70, 95)):
    return t.plan_text(ground, xy, brand.MASTHEAD, masthead.font_for(SIZE), brand.BONE if fill is None else fill,
                       "ls", brand_system.tok("masthead.track") * SIZE, min_halo=halo, max_halo=halo)


def _ink(p, size):
    out = np.zeros((size[1], size[0]), np.uint8)
    x, y = p.origin
    h, w = p.glyphs.shape
    out[y:y + h, x:x + w] = p.glyphs
    return out


# ------------------------------------------------------------------ (b) one per-glyph method, every text element

def _striped_body():
    a = np.full((180, 900, 3), brand.INK, np.uint8)
    a[:, np.arange(900) % 32 < 2] = 110          # 2 px stripes every 32 px: the ring stays dark, some glyphs do not
    return Image.fromarray(a)


def test_a_body_line_on_stripes_is_judged_glyph_by_glyph():
    """Sweep #4: the ring passed a striped body ground at 16.60 while the worst glyph read 4.376. TextPlate.solve
    now judges every glyph's ground under its ink, so the element takes a halo, and its record never claims more
    than its worst glyph reaches."""
    g = _striped_body()
    f = t.get_font(brand.TEXT_FONT, 48)
    capped = t.plan_text(g, (40, 100), "The chamber remains.", f, brand.BONE, "ls", max_halo=.16)
    worst, detail = t.glyph_contrast(np.asarray(capped.under(capped._crop(g), capped.halo).convert("RGB")),
                                     capped.glyphs, (0, 0), capped.fill, drawn=False, parts=capped.glyph_parts())
    assert capped.contrast <= worst + 1e-9 and capped.contrast < 6.0, (capped.contrast, worst)
    assert detail["glyphs"] >= 15
    free = t.plan_text(g, (40, 100), "The chamber remains.", f, brand.BONE, "ls")
    assert free.halo > 0 and free.contrast >= brand.PLACE_TARGET
    assert free.measure(free.draw(g)) >= brand.PLACE_TARGET - 0.3      # delivered: the same rule, the ink as drawn


def test_the_ring_alone_still_passes_the_striped_body_line():
    """The old reading for contrast (the counterexample is real): the ring alone reads 16.6 on the same pixels."""
    g = _striped_body()
    p = t.plan_text(g, (40, 100), "The chamber remains.", t.get_font(brand.TEXT_FONT, 48), brand.BONE, "ls",
                    max_halo=0.0)
    assert t.ring_contrast(np.asarray(g), p.glyphs, p.origin, p.fill) > 16.0
    assert p.contrast < 5.0


@pytest.mark.parametrize("grey", [(144, 144, 144), (128, 124, 118)])
def test_a_mid_grey_fill_takes_the_worse_side_of_its_ground(grey):
    """Sweep #3 (and the token sweep's mid-tone BONE/STONE): a grey fill on stripes of its own grey and black reported
    6.58 while half its ink sat at 1:1. The tail is taken by the RATIO, so the worse side of the fill decides."""
    a = np.zeros((180, 900, 3), np.uint8)
    a[:, np.arange(900) % 8 < 4] = grey
    ground = Image.fromarray(a)
    # validate=False: a test of the measure on a palette the configure-time masthead check refuses (PLATES-06)
    with brand.use(brand.DEFAULT_BRAND.derive("grey", palette={"BONE": grey}), validate=False):
        p = _name_plate(ground)
        got = masthead.judge(ground, p)
        assert got < 1.5, got
        rec: dict = {}
        diamond, spec = masthead.mark_geometry(18, 95, SIZE)
        assert masthead.verify(ground, p, diamond, spec["edge"], rec) is None and rec["shown"] is False


def test_the_tail_is_symmetric_in_the_ratio():
    """_tail reads the worst share of the RATIOS: the same ground mirrored about the fill reads the same."""
    lt = 0.2
    lum = np.r_[np.full(50, 0.0), np.full(50, 0.2)]
    assert t._tail(lum, lt, 95) == pytest.approx(1.0)
    assert t._tail(np.r_[np.full(50, 1.0), np.full(50, 0.2)], lt, 95) == pytest.approx(1.0)


def test_the_pieces_of_one_glyph_are_judged_together():
    """Sweep #11: an 8 px detached piece of one shaped glyph was judged as its own glyph and forced the maximum
    treatment. One glyph mask covering a letter and a detached speck makes ONE glyph; a bright patch under the speck
    alone (its 8 px are under 5 % of the glyph) does not decide it."""
    mask = np.zeros((60, 80), np.uint8)
    mask[10:50, 20:30] = 255                      # the stem: 400 px
    mask[10:12, 40:44] = 255                      # its detached speck: 8 px
    glyph = Image.fromarray(mask[5:55, 15:50].copy())
    parts = t.glyph_parts(mask, [(0, (15, 5, 50, 55), glyph)], (0, 0))
    assert len(parts) == 1 and len(parts[0][1]) == 408
    rgb = np.zeros((60, 80, 3), np.uint8)
    rgb[8:14, 38:46] = 200                         # bright ground under and round the speck only
    worst, detail = t.glyph_contrast(rgb, mask, (0, 0), brand.BONE, drawn=False, parts=parts)
    assert detail["glyphs"] == 1 and worst > 10.0
    alone = t.glyph_parts(mask)                    # no shaping known: components, each its own glyph
    assert len(alone) == 2


def test_the_masthead_records_one_glyph_per_letter():
    """Sweep #11's record: RELIC & RIDDLE judged as its 12 shaped glyphs, never 14 pieces."""
    _out, _p, rec, _box = masthead.draw(Image.new("RGB", (900, 180), brand.INK), 30, 95, SIZE)
    assert rec["shown"] and rec["glyphs"] == sum(not c.isspace() for c in brand.MASTHEAD)


def test_delivered_dark_text_is_read_as_drawn():
    """Sweep #13: delivered dark text took the intended fill for its ink: a 5 % fade read 16.60, and so did the
    blank ground. The ink is read as drawn."""
    base = Image.new("RGB", (900, 180), brand.BONE)
    p = _name_plate(base, fill=brand.INK)
    assert p.measure(p.draw(base, alpha=.05)) < 1.5
    assert (p.measure(base) or 1.0) < 1.5
    assert p.measure(p.draw(base)) > 15.0


@pytest.mark.parametrize("size", [10, 12, 14])
def test_thin_glyphs_are_measured_never_a_crash(size):
    """Sweep #9: glyphs with no eroded interior crashed rel_luminance(None) (plan_text and the public masthead draw).
    The ink's fully covered pixels give its colour, else the fill; never an exception."""
    f = t.get_font(brand.TEXT_FONT, size, weight=400)
    g = Image.new("RGB", (400, 80), brand.INK)
    p = t.plan_text(g, (10, 50), "illicit lilt", f, brand.BONE, "ls")
    assert p.contrast is None or p.contrast > 1.0
    assert p.measure(p.draw(g)) is None or p.measure(p.draw(g)) > 1.0
    m = np.zeros((40, 40), np.uint8)
    m[5:35, 19:21] = 200                             # a hairline with no fully covered pixel
    assert t.ring_contrast(np.zeros((40, 40, 3), np.uint8), m) is None   # no ink colour known: None, not a crash
    assert t.ring_contrast(np.zeros((40, 40, 3), np.uint8), m, ink_fallback=brand.BONE) > 10.0


def test_a_thin_masthead_draws_or_refuses_with_a_record():
    """Sweep #9 through the public entry point: masthead weight 400, a one-letter name."""
    cfg = brand.DEFAULT_BRAND.derive("I", text={"masthead": "I"}, system={"masthead.weight": 400})
    with brand.use(cfg):
        room = masthead.group_width(40.0)
        _out, _p, rec, _box = masthead.draw(Image.new("RGB", (900, 180), brand.INK), 30, 95, SIZE, max_width=room)
    assert "shown" in rec and (rec["shown"] or rec.get("reason"))


def test_contrast_is_unrounded():
    """Class (a): a gate decides on the value the pixels give. ring_contrast and the glyph method return it whole."""
    rgb = np.full((60, 200, 3), 60, np.uint8)
    rgb[:, ::3] = 75
    f = t.get_font(brand.TEXT_FONT, 40)
    p = t.plan_text(Image.fromarray(rgb), (10, 45), "Hollow", f, brand.BONE, "ls", max_halo=0.0)
    ring = t.ring_contrast(rgb, p.glyphs, p.origin, p.fill)
    assert ring != round(ring, 2), ring
    assert p.contrast_unaided <= ring + 1e-9
    assert p.contrast == p.judged(p._crop(Image.fromarray(rgb)), 0.0) and p.contrast != round(p.contrast, 2)
    assert masthead.judge(Image.fromarray(rgb), p) != round(masthead.judge(Image.fromarray(rgb), p), 2)


@pytest.mark.parametrize("ground", [(92, 100, 0), (15, 91, 192), (99, 96, 64)])
def test_a_ratio_just_under_the_gate_stays_under_it(ground):
    """Class (a) on the real-run range (12 Nazca slides drew at a true 5.4981-5.4985 and were recorded 5.50): on a
    flat ground whose true ratio to BONE is 5.4982-5.4986, every measure the gate reads returns it unrounded."""
    g = Image.new("RGB", (900, 180), ground)
    p = _name_plate(g)
    true = float(_ratio(_lum(brand.BONE), _lum(ground)))
    assert 5.498 < true < GATE
    for got in (masthead.judge(g, p), masthead.glyph_contrast(g, p)[0], masthead.window_contrast(p.draw(g), p),
                p.measure(p.draw(g)), p.contrast):
        assert got == pytest.approx(true, abs=2e-5) and got < GATE, got


def test_the_gate_reads_every_measure_and_says_which_binds():
    """Real-runs sweep (b): the record did not say which measure bound. judge_detail gives every reading; the gate is
    their minimum, the windowed ring kept in it (it reads ground 5-10 px from the ink the glyph surround does not)."""
    a = np.full((180, 900, 3), brand.INK, np.uint8)
    a[:, np.arange(900) % 16 < 3] = 90
    g = Image.fromarray(a)
    p = _name_plate(g)
    d = masthead.judge_detail(g, p)
    assert set(d) == {"glyph", "drawn", "window"} and all(v is not None for v in d.values())
    assert masthead.judge(g, p) == min(d.values())


def _oracle_worst(under, mask, origin, fill, surround=4, min_px=8):
    """An independent per-glyph check for light text on a dark ground (after the real-runs sweep's indep.py, written
    from WCAG alone, nothing from src): 8-connected components; the fill against the 95th percentile of (a) the
    ground under the ink and (b) under plus a 1..4 px surround, every pixel of ink excluded; the lower of the two."""
    import cv2
    ox, oy = origin
    h, w = mask.shape
    U = _lum(under[oy:oy + h, ox:ox + w].reshape(-1, 3)).reshape(h, w)
    lf = float(_lum(fill))
    n, lab, stats, _ = cv2.connectedComponentsWithStats((mask > 127).astype(np.uint8), connectivity=8)
    k = np.ones((2 * surround + 1, 2 * surround + 1), np.uint8)
    worst = None
    for j in range(1, n):
        if stats[j, cv2.CC_STAT_AREA] < min_px:
            continue
        comp = lab == j
        ring = (cv2.dilate(comp.astype(np.uint8), k) > 0) & ~(mask > 0)
        u = U[comp]
        both = np.concatenate([u, U[ring]])
        s = min(float(_ratio(lf, np.percentile(u, 95))), float(_ratio(lf, np.percentile(both, 95))))
        worst = s if worst is None else min(worst, s)
    return worst


@pytest.mark.parametrize("period,width,level", [(8, 2, 120), (12, 3, 160), (32, 2, 110), (6, 1, 200)])
def test_the_glyph_method_agrees_with_an_independent_oracle(period, width, level):
    """A second check: for separated letters of one piece each (no detached parts, so shaped glyphs and components
    coincide) on a dark striped ground, the planned per-glyph ratio equals the independent oracle's."""
    a = np.full((180, 900, 3), brand.INK, np.uint8)
    a[:, np.arange(900) % period < width] = level
    g = Image.fromarray(a)
    p = t.plan_text(g, (40, 110), "HOLLOW MARK", t.get_font(brand.TEXT_FONT, 56, weight=700), brand.BONE, "ls",
                    track=6, max_halo=0.0)
    ours, detail = t.glyph_contrast(a, p.glyphs, p.origin, p.fill, drawn=False, parts=p.glyph_parts())
    assert detail["glyphs"] == len(t.glyph_parts(p.glyphs)) == 10
    assert ours == pytest.approx(_oracle_worst(a, p.glyphs, p.origin, p.fill), rel=1e-6)


# ------------------------------------------------------------------ (c) fit = the drawn ink

TEXTS = ["RELIC & RIDDLE", "AVAVAV", "Á í A̧", "WAVY fj", "Jojo q̃"]


@pytest.mark.parametrize("family", ["Cinzel", "Montserrat"])
@pytest.mark.parametrize("track", [-0.2, 0.0, 0.0625, 0.3])
def test_glyph_masks_are_the_drawn_ink_for_every_anchor(family, track):
    """Sweep #14: zero-tracking centre and right masks missed drawn ink (402 px in an ordinary name); combining
    marks were masked as a lone mark (a dotted circle). The masks union to the drawn ink, pixel for pixel."""
    f = t.get_font(family, 53.333333)
    W, H = 1400, 240
    for text in TEXTS:
        for anchor in ("ls", "ms", "rs", "mm", "ra", "lt"):
            im = Image.new("L", (W, H))
            draw_run(ImageDraw.Draw(im), (700.4, 120.3), text, f, 255, anchor, track * f.size)
            union = np.zeros((H, W), np.uint8)
            for _, (x0, y0, x1, y1), gm in glyph_masks((W, H), (700.4, 120.3), text, f, anchor, track * f.size):
                union[y0:y1, x0:x1] = np.maximum(union[y0:y1, x0:x1], np.asarray(gm))
            assert np.array_equal(union > 0, np.asarray(im) > 0), (text, anchor)


@pytest.mark.parametrize("family", ["Cinzel", "Montserrat", "IBM Plex Mono"])
@pytest.mark.parametrize("track", [-0.2, 0.0625, 0.3])
def test_tracked_combining_marks_stay_inside_the_ink_bounds(family, track):
    """Sweep #19: tracked Montserrat strings with combining marks drew 30-31 px past run_ink_bounds (a lone mark
    rasterised through its Unicode character is set on a dotted circle). Marks draw by glyph id."""
    f = t.get_font(family, 53.333333)
    for text in TEXTS:
        im = Image.new("L", (1400, 220))
        draw_run(ImageDraw.Draw(im), (100, 120), text, f, 255, "ls", track * f.size)
        bb = im.getbbox()
        b = run_ink_bounds(shape(text, f, track * f.size), f)
        box = (math.floor(b[0] + 100), math.floor(b[1] + 120), math.ceil(b[2] + 100), math.ceil(b[3] + 120))
        assert box[0] <= bb[0] and box[1] <= bb[1] and box[2] >= bb[2] and box[3] >= bb[3], (text, box, bb)


@pytest.mark.parametrize("name", [brand.MASTHEAD, "AVAVAV", "W" * 10, "ÁVA"])
@pytest.mark.parametrize("track", [-0.2, 0.0, 0.0625, 0.3])
@pytest.mark.parametrize("x0", [30, 30.4])
def test_the_masthead_group_fits_its_room_to_the_drawn_pixel(name, track, x0):
    """Sweep #6: group_width omitted the last glyph's overhang ("AVAVAV" drew 1.805 px past its accepted room). The
    group's width is its drawn extent, so a room of exactly that width holds every pixel drawn."""
    with brand.use(brand.DEFAULT_BRAND.derive("fit", text={"masthead": name}, system={"masthead.track": track})):
        width = masthead.group_width(SIZE, x0)
        _r, p, rec, box = masthead.draw(Image.new("RGB", (2500, 180), brand.INK), x0, 95, SIZE, max_width=width)
    assert rec["shown"], rec.get("reason")
    assert box[2] <= x0 + width + 1e-9 and p.box[2] <= x0 + width + 1e-9, (box, x0 + width)


def test_a_fit_never_returns_a_font_that_overflows():
    """Sweep #8: fit_font returned its floor size even when the drawn ink overflowed (896 px of ink in a 546 px room,
    a label outside the Reel safe zone). The floor is tried, and past it the fit refuses."""
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    title = "THE ANCIENT CEREMONIAL UNDERGROUND CHAMBER COMPLEX"
    with pytest.raises(t.TextFitError):
        t.fit_font(d, title, brand.DISPLAY_FONT, 66, 546, 40)
    f = t.fit_font(d, "DERINKUYU", brand.DISPLAY_FONT, 66, 546, 40)
    assert t.text_size(d, "DERINKUYU", f)[0] <= 546
    f = t.fit_font(d, "W" * 9, brand.DISPLAY_FONT, 67, t.text_size(d, "W" * 9, t.get_font(brand.DISPLAY_FONT, 40))[0], 40)
    assert f.size == 40                              # an odd step to the floor still tries the floor itself
    assert issubclass(t.TextFitError, t.LayoutError)  # the surface is refused and held, never drawn overflowing


# ------------------------------------------------------------------ one method: no renderer bypasses it

def test_no_renderer_measures_text_contrast_its_own_way():
    """The ring is read only inside typography (TextPlate, text_contrast); masthead.glyph_contrast is the shared
    method, with no percentile of its own; every other measure in src calls text_contrast, a TextPlate, or (the
    design cards' multi-plate line) glyph_contrast beside its ring."""
    calls = {}
    for path in (ROOT / "src").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if name in ("ring_contrast", "text_contrast", "glyph_contrast", "typography_glyph_contrast"):
                    calls.setdefault(name, set()).add(path.name)
    # QF-136: the advisory visual critic reads ring_contrast on glyph masks it finds in the delivered pixels itself, so
    # its measure stays independent of what any renderer recorded (the method is typography's; the masks are its own)
    assert calls["ring_contrast"] <= {"typography.py", "visual_critic.py"}, calls["ring_contrast"]
    assert {"graphics.py", "video_qa.py"} <= calls["text_contrast"]
    assert {"masthead.py", "design_cards.py"} <= calls["glyph_contrast"] | calls.get("typography_glyph_contrast", set())
    src = (ROOT / "src" / "masthead.py").read_text(encoding="utf-8")
    fn = src[src.index("def glyph_contrast("):src.index("def window_contrast(")]
    assert "percentile" not in fn and "typography_glyph_contrast(" in fn
