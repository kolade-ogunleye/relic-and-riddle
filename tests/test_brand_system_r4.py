"""Design-freeze cycle 0, round 4 (2026-11-16): the Codex re-check of 66e5b32a (queue CYCLE0-adversary-r3.codex.md).

1. (S1) The masthead's per-glyph judgement takes each glyph from the SHAPING (font_engine.glyph_masks: HarfBuzz's
   glyphs, positions and advances), never from ink connectivity, so touching letters stay separate glyphs: proved by
   sweeping masthead.track over its whole accepted range (brand_system.TOKEN_RANGES) with the adversary's rectangle
   (one letter's box, padded 0-3 px), stripe and checkerboard probes, each drawn case against an independent check.
2. (S2) The type-size inventory is OBSERVED at run time (tests/size_observer.py: every FreeType font constructed and
   every get_font call, wherever bound, during the renders below) and held equal to its committed declaration
   (tests/fixtures/type_sizes_observed.json): an alias, a font_variant resize or a wrapper making a new size fails it.
   The static scan (tests/type_sites.py) stays as the second check.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, masthead

ROOT = Path(__file__).resolve().parent.parent
OBSERVED = ROOT / "tests" / "fixtures" / "type_sizes_observed.json"
SIZE = brand_system.type_px("masthead", "carousel")
X, BASE, W, H = 107, 133, 1440, 700
GATE = brand_system.tok("contrast.masthead_gate")
LO, HI = brand_system.TOKEN_RANGES["masthead.track"]
TRACKS = sorted({LO, -0.16, -0.14, -0.10, -0.05, 0.0, 0.0625, 0.15, HI})


def _lin(rgb):
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c @ np.array([0.2126, 0.7152, 0.0722])


def _letter_masks(plate, size):
    """This file's own glyph masks, apart from src/masthead.py: each character drawn ALONE at the pen position the
    shaping gives it (font_engine.shape), in its own mask."""
    from src.font_engine import inspect_font, shape
    run = shape(plate.text, plate.font, plate.track, plate.features)
    feats = ["-" + t for t in sorted(inspect_font(str(plate.font.path)).features)]
    out = []
    for g in run.glyphs:
        ch = plate.text[g.cluster]
        if ch.isspace():
            continue
        m = Image.new("L", size, 0)
        ImageDraw.Draw(m).text((plate.xy[0] + g.x, plate.xy[1] - g.y), ch, font=plate.font, fill=255, anchor="ls",
                               features=feats)
        out.append(np.asarray(m) > 127)
    return out


def _independent_letter_min(ground, plate) -> float:
    """Per letter (shaped, drawn alone): the fill against the 95th / 5th percentile of the ground under its own ink,
    and of that plus a 1-4 px surround free of any ink; the worst letter."""
    under = np.asarray(plate.draw_halo(ground.convert("RGB")) if plate.halo > 0 else ground.convert("RGB"))
    lum = _lin(under.reshape(-1, 3)).reshape(under.shape[:2])
    lt = _lin(np.array(plate.fill[:3]))
    masks = _letter_masks(plate, ground.size)
    ink = np.zeros(lum.shape, bool)
    for m in masks:
        ink |= m
    pct = 95 if plate.light else 5
    worst = math.inf
    for m in masks:
        if m.sum() < 8:
            continue
        ring = cv2.dilate(m.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) & ~ink
        for vals in (lum[m], np.concatenate([lum[m], lum[ring]])):
            lb = float(np.percentile(vals, pct))
            worst = min(worst, (max(lt, lb) + 0.05) / (min(lt, lb) + 0.05))
    return worst


def _check(ground, track, text=None):
    b = brand.DEFAULT_BRAND.derive("track probe", system={"masthead.track": track},
                                   text={"masthead": text} if text else None)
    with brand.use(b):
        g2, p, rec = masthead.plan(ground, X, BASE, SIZE, reach=BASE + 400)
        if not rec["shown"]:
            assert "reason" in rec and (rec["best_tried"] is None or rec["best_tried"] < GATE)
            return rec
        assert _independent_letter_min(g2, p) >= GATE - 0.02, (track, rec)
    return rec


def test_glyph_masks_are_the_drawn_text():
    """The shaped per-glyph masks union to the plate's own ink (the text as drawn), at every tracking."""
    from src.font_engine import glyph_masks
    from src.typography import plan_text
    for track in TRACKS:
        with brand.use(brand.DEFAULT_BRAND.derive("t", system={"masthead.track": track})):
            spec = brand_system.masthead_spec(SIZE)
            f = masthead.font_for(SIZE)
            p = plan_text(Image.new("RGB", (W, H), brand.INK), (X + 60, BASE), brand.MASTHEAD, f, brand.BONE, "ls",
                          spec["track"], max_halo=0.0)
            union = np.zeros((H, W), bool)
            masks = glyph_masks((W, H), p.xy, p.text, p.font, p.anchor, p.track, p.features)
            for _, (x0, y0, x1, y1), m in masks:
                union[y0:y1, x0:x1] |= np.asarray(m) > 127
            drawn = np.zeros((H, W), bool)
            ox, oy = p.origin
            gh, gw = p.glyphs.shape
            drawn[oy:oy + gh, ox:ox + gw] = p.glyphs > 127
            iou = (union & drawn).sum() / max(1, (union | drawn).sum())
            assert iou >= 0.98, (track, iou)
            assert len(masks) == sum(not c.isspace() for c in brand.MASTHEAD)   # one mask per letter, always


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("pad", [0, 1, 2, 3])
def test_a_bright_box_over_one_letter_is_caught_at_every_tracking(track, pad):
    """The adversary's probe: 30 letters, touching under negative tracking; a bone rectangle over letter 15's box."""
    from src.font_engine import shape
    text = "I" * 30
    b = brand.DEFAULT_BRAND.derive("touch", system={"masthead.track": track}, text={"masthead": text})
    base = Image.new("RGB", (W, H), brand.INK)
    with brand.use(b):
        _, p, _ = masthead.plan(base, X, BASE, SIZE, reach=BASE + 400)
        g = shape(p.text, p.font, p.track, p.features).glyphs[15]
        m = Image.new("L", base.size)
        ImageDraw.Draw(m).text((p.xy[0] + g.x, p.xy[1] - g.y), "I", font=p.font, fill=255, anchor="ls")
        x0, y0, x1, y1 = m.getbbox()
    ground = base.copy()
    ImageDraw.Draw(ground).rectangle((x0 - pad, y0 - pad, x1 - 1 + pad, y1 - 1 + pad), fill=brand.BONE)
    rec = _check(ground, track, text)
    if rec["shown"]:
        assert rec["contrast"] >= GATE


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("width", [2, 4, 12])
def test_stripes_at_every_tracking(track, width):
    from test_brand_system_r3 import NAME_X, _stripe
    _check(_stripe(width, NAME_X + 17, 0), track)


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("cell", [1, 3, 8])
def test_checkerboards_at_every_tracking(track, cell):
    yy, xx = np.indices((H, W))
    g = np.where((xx // cell + yy // cell) % 2, 255, 0).astype(np.uint8)
    _check(Image.fromarray(np.repeat(g[..., None], 3, axis=2)), track)


def test_token_ranges_cover_every_numeric_runtime_token_and_are_enforced():
    # cycle 0 round 8: a derived token (TOKEN_DERIVED, type.display_cap "measure") is numeric too, its default read
    # from the face; a brand may give a number in its range
    numeric = {k for k, v in brand_system.SYSTEM_TOKENS.items() if isinstance(v, (int, float))}
    numeric |= set(brand_system.TOKEN_DERIVED)
    assert numeric == set(brand_system.TOKEN_RANGES)
    for k, (lo, hi) in brand_system.TOKEN_RANGES.items():
        if k not in brand_system.TOKEN_DERIVED:
            assert lo <= brand_system.SYSTEM_TOKENS[k] <= hi, k
        with pytest.raises(ValueError, match="outside its range"):
            brand.DEFAULT_BRAND.derive("out", system={k: hi + abs(hi) + 1})


# ------------------------------------------------- the runtime-observed type-size inventory (S2)

def render_all(tmp_path) -> list[str]:
    """Every surface variant the code declares (tests/surface_registry.py: enumerated from the renderers' own
    registries, round 6), each rendered once from synthetic inputs (CI has no output/); returns the keys rendered."""
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    import surface_registry
    return surface_registry.render_all(Path(tmp_path))


def observed_modules(seen) -> list[str]:
    return sorted({site.split(":")[0] for site, _ in seen if site != "<outside>"})


def _off_ladder(seen):
    import type_sites
    ladder = type_sites._ladder()
    out: dict[str, list[float]] = {}
    for site, size in seen:
        if site != "<outside>" and round(size, 3) not in ladder:
            out.setdefault(site, set()).add(size)
    return {k: sorted(v) for k, v in sorted(out.items())}


_OBSERVATION: dict = {}


def _observe_in_a_fresh_process(tmp_path, with_modules: bool = False):
    """The observation, run once per test session (both tests read it)."""
    if "result" not in _OBSERVATION:
        _OBSERVATION["result"] = _run_observation(tmp_path)
    observed, modules = _OBSERVATION["result"]
    return (observed, modules) if with_modules else observed


def _run_observation(tmp_path):
    """render_all under the observer in a NEW interpreter, so no font any earlier test cached hides its creation."""
    import os
    import subprocess
    import sys
    env = {**os.environ, "FONT_SIZE": os.environ.get("FONT_SIZE", "80")}
    res = subprocess.run([sys.executable, str(ROOT / "tests" / "observe_render.py"), str(tmp_path)], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=1500, check=False)
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("OBSERVED=")), None)
    assert line is not None, res.stderr[-3000:]
    observed = json.loads(line[len("OBSERVED="):])
    mods = next(ln for ln in res.stdout.splitlines() if ln.startswith("MODULES="))
    for tag in ("SITES", "RENDERED"):   # round 6: the sites seen and the surface keys rendered (test_brand_system_r6)
        got = next(ln for ln in res.stdout.splitlines() if ln.startswith(tag + "="))
        _OBSERVATION[tag.lower()] = json.loads(got[len(tag) + 1:])
    return observed, json.loads(mods[len("MODULES="):])


def test_every_observed_type_size_is_on_the_ladder_or_declared(tmp_path):
    """Every size any font is made at during the renders is on the ladder or declared for its site (the committed
    observation, exactly: nothing new, nothing gone), and every such site is in brand_system.SIZE_SITES."""
    import os
    observed = _observe_in_a_fresh_process(tmp_path)
    if os.environ.get("RR_WRITE_OBSERVED"):
        OBSERVED.write_text(json.dumps(observed, indent=1) + chr(10), encoding="utf-8", newline=chr(10))
    declared = json.loads(OBSERVED.read_text(encoding="utf-8"))
    new = {s: sorted(set(v) - set(declared.get(s, []))) for s, v in observed.items() if set(v) - set(declared.get(s, []))}
    gone = {s: sorted(set(v) - set(observed.get(s, []))) for s, v in declared.items() if set(v) - set(observed.get(s, []))}
    assert new == {}, f"undeclared sizes (declare them in {OBSERVED.name} and SIZE_SITES, or port them): {new}"
    assert gone == {}, f"declared sizes no longer made (remove them): {gone}"
    declared_sites = set(brand_system.SIZE_SITES) | set(brand_system.RUNTIME_SIZE_SITES)
    assert set(observed) <= declared_sites, sorted(set(observed) - declared_sites)
    assert not set(brand_system.RUNTIME_SIZE_SITES) & set(brand_system.SIZE_SITES)   # each declared once


def test_the_observed_renders_cover_every_surface_module(tmp_path):
    """The render list is complete: every module that declares a size site (SIZE_SITES, RUNTIME_SIZE_SITES) makes at
    least one font during render_all (round 5: then-and-now had been left out, so its sizes went unobserved)."""
    _, modules = _observe_in_a_fresh_process(tmp_path, with_modules=True)
    declared = {site.split(":")[0] for site in list(brand_system.SIZE_SITES) + list(brand_system.RUNTIME_SIZE_SITES)}
    assert declared <= set(modules), sorted(declared - set(modules))


def test_the_observer_renders_every_registered_surface(tmp_path):
    """Round 6 (adversary r5-3: a hand list left out the monument cover): the observed renders are exactly the surface
    variants the code declares (tests/surface_registry.py: cover styles, carousel archetypes in both copies, card
    planners and graphic renderers on each canvas they draw, ...), each rendered."""
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    import surface_registry
    _observe_in_a_fresh_process(tmp_path)
    assert _OBSERVATION["rendered"] == surface_registry.registry()
    assert "cover.monument:tall" in _OBSERVATION["rendered"]
    assert "graphics.render_timeline[events=2]:tall" in _OBSERVATION["rendered"]   # round 7: branches, not just kinds
    assert any(site.startswith("cover_design:_monument") for site in _OBSERVATION["sites"])


def test_every_declared_size_site_is_observed(tmp_path):
    """Cycle 0 round 7 (sweep-paths #4: the registry enumerated KINDS, so branches no declaration ever saw were never
    drawn): every declared size site (brand_system.SIZE_SITES and RUNTIME_SIZE_SITES) makes a font during the
    observed renders, so each one's sizes are held by the committed observation. A site no render reaches needs the
    input that reaches it (a branch in tests/surface_registry.py), or it is dead code."""
    _observe_in_a_fresh_process(tmp_path)
    declared = set(brand_system.SIZE_SITES) | set(brand_system.RUNTIME_SIZE_SITES)
    unobserved = sorted(declared - set(_OBSERVATION["sites"]))
    assert unobserved == [], f"declared size sites no observed render reaches: {unobserved}"


def test_the_observer_sees_an_alias_a_variant_and_a_wrapper(tmp_path):
    """The adversary's three mutations, each creating a 137 px font the static scan did not see."""
    import size_observer

    from src import typography
    def alias():
        gf = typography.get_font                                             # an alias, bound inside the block
        gf(brand.TEXT_FONT, 137)

    def variant():
        typography.get_font(brand.TEXT_FONT, 40).font_variant(size=137)        # a cached font resized

    def wrapper():
        masthead.font_for(137)                                               # the system wrapper
    for make in (alias, variant, wrapper):
        with size_observer.observe() as seen:
            make()
        assert 137.0 in {size for _, size in seen}, make.__name__


# ------------------------------------------------- bright, mixed and mid grounds (the lander's board finding, round 4)

def _grounds_bright():
    """A pure #F5F0E8 wall; a mid grey; a bright sky (luma ~235) with a dark mass (~60) under the name's last letters
    (the board's Aswan crop: sky p50 212-235, trees p5 50-104); a mid-grey glass case (~140, the board's Antikythera)."""
    out = {"bone_wall_F5F0E8": Image.new("RGB", (W, H), (0xF5, 0xF0, 0xE8)),
           "mid_grey_128": Image.new("RGB", (W, H), (128, 128, 128)),
           "mid_grey_140": Image.new("RGB", (W, H), (140, 140, 142))}
    sky = Image.new("RGB", (W, H), (236, 236, 232))
    ImageDraw.Draw(sky).rectangle((560, 60, W, 260), fill=(60, 64, 58))
    out["bright_sky_dark_mass"] = sky
    return out


@pytest.mark.parametrize("name", sorted(_grounds_bright()))
def test_the_masthead_is_drawn_wherever_either_variant_reaches_the_gate(name):
    """Drawn whenever bone (with the ruled scrim, halo, levels) or INK (on the untouched ground) reaches 5.5 on every
    glyph; refused only when the record shows BOTH variants under the gate, and then the independent per-letter
    check of INK on the untouched ground agrees."""
    from src.typography import plan_text
    ground = _grounds_bright()[name]
    g2, p, rec = masthead.plan(ground, X, BASE, SIZE, reach=BASE + 400)
    if rec["shown"]:
        assert _independent_letter_min(g2, p) >= GATE - 0.02, rec
    else:
        assert rec["best_dark"] < GATE and rec["best_light"] < GATE, rec
        spec = brand_system.masthead_spec(SIZE)
        ink = plan_text(ground, (X + 2 * spec["mark_r"] + spec["gap"], BASE), brand.MASTHEAD, masthead.font_for(SIZE),
                        brand.INK, "ls", spec["track"], max_halo=0.0)
        assert _independent_letter_min(ground, ink) < GATE + 0.02
    if name == "bone_wall_F5F0E8":   # INK on a near-white wall: drawn, in the light variant, far above the gate
        assert rec["shown"] and rec["variant"] == "light" and rec["contrast"] >= 10, rec
    if name == "mid_grey_128":       # bone with the scrim on a mid grey
        assert rec["shown"] and rec["variant"] == "dark", rec


# ------------------------------------------------- one body size per deck, both palettes (the lander's board finding)

DECK = [("immersive", {"heading": "IMPACT THEORY DISPROVED",
                       "body": "Initially considered an impact crater, the theory was disproved in 1969."}),
        ("editorial_split", {"heading": "RINGS OF HARD ROCK",
                             "body": "Erosion exposed rings of harder and softer rock over a hundred million years."}),
        ("comparison", {"heading": "EYE OF THE SAHARA",
                        "body": "Seen from orbit, the structure looks like a target painted on the desert."}),
        ("immersive", {"heading": "WHO NAMED IT",
                       "body": "Astronauts on early missions used it as a landmark."})]


def _second_brand():
    import sys
    sys.path.insert(0, str(ROOT / "docs/reviews/quality-fixes-2026-11/QF-35a"))
    import render_surfaces
    return render_surfaces.second_brand(brand)


@pytest.mark.parametrize("palette", ["default", "second"])
@pytest.mark.parametrize("facebook", [False, True])
def test_a_content_body_keeps_one_size_through_the_deck(tmp_path, palette, facebook):
    """Within one deck the content slides' body is one size in both palettes: the stack's fit gives way in the heading
    (a declared deviation), never in the body (the second brand's wider display face had shrunk single bodies to
    27-33 design px). The cover archetype's dek is the cover's own (rubric CD3: cover, stat and CTA excepted)."""
    from src.multi_format import MultiFormatGenerator
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1600, 2000), (150, 120, 90)).save(photo)
    cfg = _second_brand() if palette == "second" else brand.DEFAULT_BRAND
    sizes = set()
    with brand.use(cfg, validate=False):   # the second palette (round 8: refused by the fit corpus)
        for i, (arche, card) in enumerate(DECK, start=1):
            _, lay = MultiFormatGenerator()._render_carousel_slide(arche, photo, photo, card, i, 7, {}, "", facebook,
                                                                  subject="EYE OF THE SAHARA")
            sizes |= {e["px"] for e in lay.elements if e["name"] == "body"}
    assert sizes == {round(brand_system.TYPE["carousel"]["body"], 1)}, sizes


# ------------------------------------------------- the mark measured on its drawn pixels; joint token limits (round 5)

def test_interacting_tokens_are_limited_jointly():
    """The adversary's pair (mark_r 0.15 with mark_edge 0.20, each inside its own range) erased the diamond: refused."""
    with pytest.raises(ValueError, match="interact"):
        brand.DEFAULT_BRAND.derive("erased mark", system={"masthead.mark_r": 0.15, "masthead.mark_edge": 0.20})
    for key, factor, other in brand_system.TOKEN_JOINT:
        assert brand_system.SYSTEM_TOKENS[key] <= factor * brand_system.SYSTEM_TOKENS[other]


def _mark_grounds():
    photo = Image.fromarray(np.random.default_rng(5).integers(40, 220, (H // 10, W // 10, 3), dtype=np.uint8)).resize(
        (W, H), Image.BILINEAR)
    return {"ink": Image.new("RGB", (W, H), brand.INK), "bone": Image.new("RGB", (W, H), brand.BONE),
            "gold": Image.new("RGB", (W, H), brand.GOLD), "photo": photo}


MARK_LIMITS = []
for _r in brand_system.TOKEN_RANGES["masthead.mark_r"]:
    _edge_hi = min(brand_system.TOKEN_RANGES["masthead.mark_edge"][1], 0.35 * _r)
    for _e in (brand_system.TOKEN_RANGES["masthead.mark_edge"][0], _edge_hi):
        MARK_LIMITS.append((_r, _e))


@pytest.mark.parametrize("mark_r,mark_edge", MARK_LIMITS)
@pytest.mark.parametrize("ground_name", ["ink", "bone", "gold", "photo"])
def test_the_mark_is_visible_at_every_token_limit_or_the_masthead_is_refused(mark_r, mark_edge, ground_name):
    """Every pair of mark tokens at its limits, on INK, bone, gold and a photo: a drawn masthead's mark has visible
    pixels that meet 3:1 against the ground around it (measured here independently: the pixels the mark changed, gold
    against the ring's 10th percentile or INK against its 90th); otherwise the masthead is refused with the reason."""
    import cv2
    ground = _mark_grounds()[ground_name]
    b = brand.DEFAULT_BRAND.derive("mark limits", system={"masthead.mark_r": mark_r, "masthead.mark_edge": mark_edge})
    with brand.use(b):
        out, _, rec, _ = masthead.draw(ground, X, BASE, SIZE, reach=BASE + 400)
        if not rec["shown"]:
            assert "reason" in rec
            return
        diamond, spec = masthead.mark_geometry(X, BASE, SIZE)
        g2, p2, _ = masthead.plan(ground, X, BASE, SIZE, reach=BASE + 400)
    before = np.asarray(p2.draw(g2.convert("RGB"))).astype(np.int16)
    after = np.asarray(out.convert("RGB")).astype(np.int16)
    m = Image.new("L", (W, H))
    ImageDraw.Draw(m).polygon(diamond, fill=255)
    gm = (np.asarray(m) > 0).astype(np.uint8)
    region = cv2.dilate(gm, np.ones((2 * spec["edge"] + 3,) * 2, np.uint8)) > 0
    changed = region & (np.abs(after - before).max(axis=2) > 8)
    ring = (cv2.dilate(region.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0) & ~(
        cv2.dilate(region.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    lum_bg = _lin(before[ring].astype(np.uint8))
    lo, hi = float(np.percentile(lum_bg, 10)), float(np.percentile(lum_bg, 90))
    lum = _lin(after[changed].astype(np.uint8))
    best = 1.0
    px_changed = after[changed]
    gold_like = (np.abs(px_changed - np.array(brand.GOLD)).sum(axis=1)
                 < np.abs(px_changed - np.array(brand.INK)).sum(axis=1))
    if gold_like.sum() >= 12:
        lf = float(np.median(lum[gold_like]))
        best = max(best, (max(lf, lo) + 0.05) / (min(lf, lo) + 0.05))
    if (~gold_like).sum() >= 12:
        le = float(np.median(lum[~gold_like]))
        best = max(best, (max(le, hi) + 0.05) / (min(le, hi) + 0.05))
    assert best >= brand_system.tok("contrast.mark") - 0.05, (rec, best)


# ------------------------------------------------- one body size in GENERATED decks (round 5)

LONG = " ".join(["The ancient monument contains hidden rooms beneath the hill"] * 3) + "."
GENERATED_DECKS = [
    [("immersive", {"heading": "WIDESPREAD MYSTERIOUS DISCOVERIES", "body": "A short body."}),
     ("immersive", {"heading": "WIDESPREAD MYSTERIOUS DISCOVERIES", "body": LONG})],
    [("editorial_split", {"heading": "A RING", "body": LONG}),
     ("comparison", {"heading": "THE EYE OF THE GREAT SAHARA DESERT", "body": "Short."}),
     ("immersive", {"heading": "WHO", "body": " ".join(["Astronauts used it as a landmark on early missions"] * 2) + "."})],
    [("immersive", {"heading": "THE EYE", "body": "Short."}),
     ("immersive", {"heading": "THE EYE", "body": LONG})],
]


# deck 0's 33-letter heading is refused outright by the second brand's wider face (QF-23: refused, never shrunk), so
# that pair would draw nothing; deck 2 forces the body fit in the second palette instead
DECK_CASES = [(d, pal, fb) for d in range(len(GENERATED_DECKS)) for pal in ("default", "second") for fb in (False, True)
              if (d, pal) != (0, "second")]


@pytest.mark.parametrize("deck,palette,facebook", DECK_CASES)
def test_generated_decks_keep_one_body_size(tmp_path, palette, facebook, deck):
    """Decks whose headings and bodies force the fit (the adversary's two-slide deck drew 48 and 45.3): every content
    body is drawn at the one carousel body size; a body that does not set in its lines takes a line, not a size."""
    from src.multi_format import MultiFormatGenerator, _render_state
    from src.typography import TextFitError
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1600, 2000), (150, 120, 90)).save(photo)
    cfg = _second_brand() if palette == "second" else brand.DEFAULT_BRAND
    cards = [c for _, c in GENERATED_DECKS[deck]]
    archetypes = [a for a, _ in GENERATED_DECKS[deck]]
    sizes = set()
    with brand.use(cfg, validate=False):   # the second palette (round 8: refused by the fit corpus)
        gen = MultiFormatGenerator()
        frame = gen._deck_heading_frame(cards, archetypes, facebook)
        _render_state.heading_frame = frame
        drawn = 0
        for i, (arche, card) in enumerate(GENERATED_DECKS[deck], start=1):
            try:
                _, lay = gen._render_carousel_slide(arche, photo, photo, card, i, 7, {}, "", facebook,
                                                    subject="TEST SITE", heading_frame=frame)
            except TextFitError:   # a heading the brand's face cannot set is refused (QF-23), never shrunk
                continue
            drawn += 1
            sizes |= {e["px"] for e in lay.elements if e["name"] == "body"}
    assert drawn >= 1
    assert sizes == {round(brand_system.TYPE["carousel"]["body"], 1)}, sizes


# ------------------------------------------------- the masthead's declared fit ladder (round 5, the lander's addition)

def test_the_fit_ladder_is_declared_and_bounded():
    floor = brand_system.tok("masthead.fit_floor")
    lo, hi = brand_system.TOKEN_RANGES["masthead.fit_floor"]
    assert lo <= floor <= hi
    ladder = masthead.fit_ladder(64)
    assert ladder[0] == 64 and min(ladder) >= floor * 64 - 1e-9
    assert all(a - b == pytest.approx(masthead.FIT_STEP) for a, b in __import__("itertools").pairwise(ladder))


@pytest.mark.parametrize("name,expect", [("SECOND TEST BRAND", "fit"), ("A MUCH LONGER BRAND NAME THAN EVER FITS", "refused")])
def test_a_longer_brand_name_steps_down_the_ladder_on_every_surface(name, expect):
    """One rule on every surface that draws the masthead: the largest ladder size whose group fits the surface's room,
    never under the floor; below it, refused with the reason. The default name keeps its canvas size (unchanged)."""
    from src import end_card
    b = brand.DEFAULT_BRAND.derive("long name", text={"masthead": name})
    ground = Image.new("RGB", (W, H), brand.INK)
    with brand.use(b):
        # "fit": a room narrower than the name at its canvas size; "refused": the carousel's room, far too narrow
        room = 0.92 * masthead.group_width(SIZE) if expect == "fit" else 0.62 * W
        _, _, rec = masthead.plan(ground, X, BASE, SIZE, max_width=room)
        floor = brand_system.tok("masthead.fit_floor") * SIZE
        if expect == "fit":
            assert rec["shown"] and floor - 1e-6 <= rec["size_px"] < SIZE and rec["group_width"] <= room
        else:
            assert not rec["shown"] and "floor" in rec["reason"]
        stats: dict = {}
        old = end_card.SECONDS
        end_card.SECONDS = 0.2
        try:
            list(end_card.frames(Image.new("RGB", (1080, 1920), (60, 60, 60)), "Share this with a friend.", stats))
        finally:
            end_card.SECONDS = old
        m = stats["masthead"]
        start = brand_system.type_px("masthead", "tall")
        if expect == "fit":
            assert m["shown"] and brand_system.tok("masthead.fit_floor") * start - 1e-6 <= m["size"] and m["group_width"] <= m["room"]
        else:
            assert not m["shown"] and "reason" in m
    _, _, rec = masthead.plan(ground, X, BASE, SIZE, max_width=0.62 * W)
    assert rec["shown"] and rec["size_px"] == SIZE                    # the default name: its canvas size
