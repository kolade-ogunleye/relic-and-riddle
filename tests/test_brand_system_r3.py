"""Design-freeze cycle 0, round 3 (2026-11-16): the Codex re-check of 511a5682 (queue CYCLE0-adversary-r2.codex.md),
each finding CLASS closed and proved on the class, not on the one probe:

1. the masthead is judged glyph by glyph (every connected component of its ink against the ground under it and its
   immediate surround), proved on stripes 1-48 px at offsets across the first letters and at angles, checkerboards
   1-8 px, noise, narrow letters; a masthead drawn passes an INDEPENDENT per-glyph check (this file's own code);
2. kicker.weight and kicker.colour drive every caller: complete Story, carousel and Facebook renders change;
3. every type-size site of every surface module is on the ladder or declared (brand_system.SIZE_SITES, scanned);
4. (claim: measurements.md) 5. a fail-closed record carries the true best of every step tried; 6. the end card's
fail-closed record carries its reason; 7. a masthead wider than its room is refused, never clipped.
Invariants only; no pinned measured value.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, masthead

SIZE = brand_system.type_px("masthead", "carousel")
X, BASE = 107, 133
W, H = 1440, 700
GATE = brand_system.tok("contrast.masthead_gate")


def _lin(rgb):
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c @ np.array([0.2126, 0.7152, 0.0722])


def _ratio(a, b):
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _independent_glyph_min(ground, plate) -> float:
    """This file's own per-glyph check, written apart from src/masthead.py: for every 8-connected ink component, the
    fill against the 95th (light text) / 5th (dark text) percentile of the ground under the component's ink, and of
    that ground plus a 1-4 px surround with no other ink; the worst over glyphs."""
    under = np.asarray(plate.draw_halo(ground.convert("RGB")) if plate.halo > 0 else ground.convert("RGB"))
    ox, oy = plate.origin
    h, w = plate.glyphs.shape
    reg = under[oy:oy + h, ox:ox + w]
    ink = plate.glyphs > 127
    lum = _lin(reg.reshape(-1, 3)).reshape(reg.shape[:2])
    lt = _lin(np.array(plate.fill[:3]))
    n, lab = cv2.connectedComponents(ink.astype(np.uint8), connectivity=8)
    worst = math.inf
    pct = 95 if plate.light else 5
    for i in range(1, n):
        comp = lab == i
        if comp.sum() < 8:
            continue
        ring = cv2.dilate(comp.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) & ~ink
        for vals in (lum[comp], np.concatenate([lum[comp], lum[ring]])):
            worst = min(worst, _ratio(lt, float(np.percentile(vals, pct))))
    return worst


def _check(ground, text_brand=None):
    """Draw the masthead on `ground`; shown -> every glyph passes the independent check within the ruled caps;
    not shown -> untouched, with a reason and a best ratio under the gate (or a room refusal)."""
    ctx = brand.use(text_brand) if text_brand is not None else _null()
    with ctx:
        g2, p, rec = masthead.plan(ground, X, BASE, SIZE, reach=BASE + 400)
        out, plate, rec2, _ = masthead.draw(ground, X, BASE, SIZE, reach=BASE + 400)
    assert rec["shown"] == rec2["shown"]
    assert rec["scrim_peak"] <= 0.60 + 1e-9 and rec["halo"] <= 0.16 + 1e-9
    if not rec["shown"]:
        assert out is ground and plate is None and "reason" in rec2
        assert rec2["best_tried"] is None or rec2["best_tried"] < GATE
        return rec2
    assert _independent_glyph_min(g2, p) >= GATE - 0.02, rec
    return rec2


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _stripe(width, x, angle_deg, ground=brand.INK, stripe=brand.BONE):
    im = Image.new("RGB", (W, H), ground)
    d = ImageDraw.Draw(im)
    a = math.radians(angle_deg)
    dx, dy = math.sin(a) * 2000, math.cos(a) * 2000
    cx, cy = x, BASE - 20
    nx, ny = math.cos(a) * width / 2, -math.sin(a) * width / 2
    d.polygon([(cx - dx - nx, cy - dy - ny), (cx - dx + nx, cy - dy + ny), (cx + dx + nx, cy + dy + ny),
               (cx + dx - nx, cy + dy - ny)], fill=stripe)
    return im


NAME_X = round(X + 2 * brand_system.tok("masthead.mark_r") * SIZE + brand_system.tok("masthead.gap") * SIZE)


@pytest.mark.parametrize("width", [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48])
@pytest.mark.parametrize("offset", [2, 9, 17, 41])
def test_vertical_stripes_never_hide_a_drawn_glyph(width, offset):
    _check(_stripe(width, NAME_X + offset, 0))


@pytest.mark.parametrize("width", [2, 4, 12])
@pytest.mark.parametrize("angle", [15, 45, 80])
def test_slanted_stripes_never_hide_a_drawn_glyph(width, angle):
    _check(_stripe(width, NAME_X + 20, angle))


@pytest.mark.parametrize("cell", [1, 2, 3, 4, 6, 8])
def test_checkerboards_fail_closed_or_hold_every_glyph(cell):
    yy, xx = np.indices((H, W))
    g = np.where((xx // cell + yy // cell) % 2, 255, 0).astype(np.uint8)
    _check(Image.fromarray(np.repeat(g[..., None], 3, axis=2)))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_noise_fails_closed_or_holds_every_glyph(seed):
    rng = np.random.default_rng(seed)
    _check(Image.fromarray(rng.integers(0, 256, (H, W, 3), dtype=np.uint8)))


@pytest.mark.parametrize("width", [2, 4, 8])
def test_narrow_letters_are_judged_one_by_one(width):
    """A brand name of narrow letters (I, 1) under a stripe as wide as one of them."""
    narrow = brand.DEFAULT_BRAND.derive("narrow", text={"masthead": "I1 II 1I"})
    with brand.use(narrow):
        x = round(X + 2 * brand_system.tok("masthead.mark_r") * SIZE + brand_system.tok("masthead.gap") * SIZE) + 3
    _check(_stripe(width, x, 0), narrow)
    _check(_stripe(width, x, 10), narrow)   # a slant, as an italic's stems would meet it


def test_the_4px_stripe_that_hid_a_whole_letter_is_caught():
    """r2 finding 1 itself: a 4 px bone stripe over a letter's stem left all its ink on the stripe (recorded 16.6:1)."""
    for off in range(0, 60, 3):
        _check(_stripe(4, NAME_X + off, 0))


def test_a_failed_search_records_the_best_step_it_tried():
    """r2 finding 5: the reason and best_tried report the best of every step, not the base ratio."""
    yy, xx = np.indices((H, W))
    g = np.where((xx // 2 + yy // 2) % 2, 255, 0).astype(np.uint8)
    _, _, rec = masthead.plan(Image.fromarray(np.repeat(g[..., None], 3, axis=2)), X, BASE, SIZE, reach=BASE + 400)
    assert not rec["shown"]
    assert rec["best_tried"] >= rec["contrast"] and rec["best_step"]
    assert f"{rec['best_tried']:.2f}" in rec["reason"]


def test_a_masthead_wider_than_its_room_is_refused_never_clipped():
    """r2 finding 7: a permitted long name (2065.9 px on 1440) was clipped while recorded as shown."""
    long_brand = brand.DEFAULT_BRAND.derive("long", text={"masthead": "THE VERY LONG NAME OF A BRAND THAT RUNS ON"})
    ground = Image.new("RGB", (W, H), brand.INK)
    with brand.use(long_brand):
        out, plate, rec, _ = masthead.draw(ground, X, BASE, SIZE)
    assert not rec["shown"] and out is ground and plate is None and "room" in rec["reason"]
    assert rec["group_width"] > rec["room"]


def test_the_end_card_fail_closed_record_carries_its_reason():
    """r2 finding 6: an undrawn end-card masthead kept only shown and contrast."""
    from src import end_card
    long_brand = brand.DEFAULT_BRAND.derive("long", text={"masthead": "THE VERY LONG NAME OF A BRAND THAT RUNS ON"})
    stats: dict = {}
    last = Image.new("RGB", (1080, 1920), (60, 60, 60))
    with brand.use(long_brand):
        frames = list(end_card.frames(last, "Share this with a friend.", stats))
    assert frames
    m = stats["masthead"]
    assert m["shown"] is False and "reason" in m and "best_tried" in m


# ------------------------------------------------- tokens drive every caller (r2 finding 2), on complete renders

def _story_render(tmp_path):
    from src.multi_format import MultiFormatGenerator
    photo = tmp_path / "s.jpg"
    Image.new("RGB", (1080, 1920), (120, 110, 95)).save(photo)
    im, lay = MultiFormatGenerator()._render_story_frame(
        photo, {"kicker": "DID YOU KNOW?", "line1": "ONE MAN", "line2": "KNOCKED", "body": "Short."}, 0, 3, {})
    return np.asarray(im.convert("RGB")).tobytes(), lay


def _carousel_render(tmp_path, facebook):
    from src.multi_format import MultiFormatGenerator
    photo = tmp_path / "c.jpg"
    Image.new("RGB", (1600, 2000), (150, 120, 90)).save(photo)
    im, lay = MultiFormatGenerator()._render_carousel_slide(
        "immersive", photo, photo, {"heading": "IMPACT THEORY DISPROVED", "body": "The theory was disproved in 1969."},
        1, 7, {}, "", facebook, subject="EYE OF THE SAHARA")
    return np.asarray(im.convert("RGB")).tobytes(), lay


@pytest.mark.parametrize("surface", ["story", "carousel", "facebook"])
@pytest.mark.parametrize("token,value", [("kicker.weight", 500), ("kicker.colour", "TEAL"), ("kicker.track", 0.3),
                                         ("kicker.rule_thick", 0.4)])
def test_kicker_tokens_change_the_complete_surfaces(tmp_path, surface, token, value):
    def render():
        if surface == "story":
            return _story_render(tmp_path)
        return _carousel_render(tmp_path, surface == "facebook")
    before, lay = render()
    assert any(e["name"] == "kicker" for e in lay.elements), "the surface draws a kicker"
    with brand.use(brand.DEFAULT_BRAND.derive(f"{token} probe", system={token: value})):
        during, lay2 = render()
    assert during != before, (surface, token)
    k = next(e for e in lay2.elements if e["name"] == "kicker")
    if token == "kicker.colour":
        assert tuple(k["fill"]) in (tuple(brand.TEAL), tuple(brand.BONE))   # TEAL, or bone on a clashing ground


# ------------------------------------------------- every type-size site is on the ladder or declared (r2 finding 3)

def test_every_type_size_site_is_declared():
    import type_sites
    found = type_sites.sites()
    declared = dict(brand_system.SIZE_SITES)
    assert set(found) - set(declared) == set(), "undeclared size sites (declare them or port them onto TYPE)"
    assert set(declared) - set(found) == set(), "stale SIZE_SITES entries (ported: remove them)"
    for site, group in declared.items():
        assert group == type_sites.GROUP_OF_MODULE[site.split(":")[0]], site


def test_inline_ladders_declared_in_deviations_exist_where_named():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for surface, (role, where, sizes, group) in brand_system.DEVIATIONS.items():
        head = where.split(":")[0]
        if "." not in head or not head.split(".")[1].isidentifier() or head.split(".")[1].isupper():
            continue
        mod, fn = head.split(".")
        tree = ast.parse((root / "src" / f"{mod}.py").read_text(encoding="utf-8"))
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn)
        tuples = [tuple(c.value for c in t.elts) for t in ast.walk(func) if isinstance(t, ast.Tuple)
                  and t.elts and all(isinstance(c, ast.Constant) for c in t.elts)]
        assert tuple(sizes) in tuples, (surface, where)


# (plate.shade on YouTube is not a pair: the YouTube masthead sits on the thumbnail's own left ink shade and is never
# scrimmed, so the scrim colour has nothing to colour there)
MASTHEAD_TOKEN_CASES = [(s, t, v) for s in ("carousel", "facebook", "youtube")
                        for t, v in (("masthead.track", 0.12), ("masthead.mark_r", 0.4), ("masthead.gap", 0.6),
                                     ("masthead.mark_edge", 0.12), ("masthead.colour_dark", "GOLD_LIGHT"),
                                     ("masthead.weight", 400), ("plate.shade", "CORAL"))
                        if (s, t) != ("youtube", "plate.shade")]


@pytest.mark.parametrize("surface,token,value", MASTHEAD_TOKEN_CASES)
def test_masthead_tokens_change_the_complete_surfaces(tmp_path, surface, token, value):
    from src import youtube_thumbnail

    def render():
        if surface == "youtube":
            sky = Image.new("RGB", (1920, 1080), (120, 170, 225))
            im, _ = youtube_thumbnail.render(sky, {"title": "THE EYE OF THE SAHARA"}, meta={})
            return np.asarray(im.convert("RGB")).tobytes()
        return _carousel_render(tmp_path, surface == "facebook")[0]
    before = render()
    with brand.use(brand.DEFAULT_BRAND.derive(f"{token} probe", system={token: value})):
        during = render()
    assert during != before, (surface, token)


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920)])
def test_the_end_card_masthead_fits_its_real_room_and_is_drawn(monkeypatch, size):
    """The room check must not refuse the brand's own masthead: on 16:9 the room runs to the video slot (round 3 first
    measured it against the group's target width and refused the default masthead), on 9:16 the Reel column."""
    from src import end_card
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 1.0)
    monkeypatch.setattr(end_card, "SECONDS", 1.0)
    stats: dict = {}
    list(end_card.frames(Image.new("RGB", size, (60, 60, 60)), "Share this with a friend.", stats))
    m = stats["masthead"]
    assert m["shown"] is True and m["group_width"] <= m["room"] and m["contrast"] >= GATE
    assert stats["contrast"]["brand"] >= GATE
