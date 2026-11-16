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
    numeric = {k for k, v in brand_system.SYSTEM_TOKENS.items() if isinstance(v, (int, float))}
    assert numeric == set(brand_system.TOKEN_RANGES)
    for k, (lo, hi) in brand_system.TOKEN_RANGES.items():
        assert lo <= brand_system.SYSTEM_TOKENS[k] <= hi, k
        with pytest.raises(ValueError, match="outside its range"):
            brand.DEFAULT_BRAND.derive("out", system={k: hi + abs(hi) + 1})


# ------------------------------------------------- the runtime-observed type-size inventory (S2)

def render_all(tmp_path) -> None:
    """Every surface renderable from committed or synthetic inputs (CI has no output/): carousel and FB slides of
    every archetype, Story frames, the YouTube thumbnail, the 9:16 end card and 16:9 end screen, hook / chapter / myth
    cards, the 9:16 and 16:9 captions, the five-event timeline and the cross-section."""
    import sys

    from src import end_card, graphics, youtube_thumbnail
    from src.multi_format import MultiFormatGenerator
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1600, 2000), (150, 120, 90)).save(photo)
    gen = MultiFormatGenerator()
    card = {"heading": "IMPACT THEORY DISPROVED", "body": "The theory was disproved in 1969.",
            "stat": "40 KM across", "question": "Impact or erosion?"}
    for arche in ("cover", "immersive", "editorial_split", "comparison", "colossal_stat", "debate"):
        for fb in (False, True):
            gen._render_carousel_slide(arche, photo, photo, card, 0 if arche == "cover" else 1, 7,
                                       {"question": "Which?", "options": ["One", "Two"]}, "Send this to a friend.", fb,
                                       subject="EYE OF THE SAHARA")
    slides = [{"kicker": "DID YOU KNOW?", "line1": "ONE MAN", "line2": "KNOCKED", "body": "Short."},
              {"kicker": "WHAT HE FOUND", "line1": "A CITY", "line2": "UNDER THE FIELDS", "body": "A longer body."}]
    for i, s in enumerate(slides):
        gen._render_story_frame(photo, s, i, 3, {})
    youtube_thumbnail.render(Image.open(photo), {"title": "THE EYE OF THE SAHARA"}, meta={})
    youtube_thumbnail.render(Image.open(photo), {"line1": "THE 25-MILE", "line2": "SAHARA EYE"}, meta={})
    old = (end_card.SECONDS, end_card.END_SCREEN_SECONDS)
    end_card.SECONDS = end_card.END_SCREEN_SECONDS = 0.2
    try:
        list(end_card.frames(Image.new("RGB", (1080, 1920), (60, 60, 60)), "Share this with a friend.", {}))
        list(end_card.frames(Image.new("RGB", (1920, 1080), (60, 60, 60)), "Share this with a friend.", {}))
    finally:
        end_card.SECONDS, end_card.END_SCREEN_SECONDS = old
    sys.path.insert(0, str(ROOT / "tests"))
    import test_freeze_cards_2026_11_04 as fc

    from src import config
    from src import design_cards as dc
    config.FACT_RECORD_KEY = config.FACT_RECORD_KEY or "test-only-fact-record-key-0123456789abcdef"   # the tests' key
    for plan in (fc.chapter_plan(fc.TALL), fc.chapter_plan(fc.WIDE), fc.plan_myth(), fc.plan_myth(size=fc.WIDE)):
        dc.frame_at(plan, plan.frames - 1)
    from src import video_composer as vc
    words = json.loads((ROOT / "docs/reviews/freeze-panel-2026-11-04/fixtures/runs/run_amber_room_heist_1792790550/"
                        "words.json").read_text(encoding="utf-8"))
    vc.VideoComposer().build_subtitles(words, tmp_path / "c.ass")
    vc.VideoComposer().build_subtitles(words, tmp_path / "w.ass", width=1920, height=1080)
    events = [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
              {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
              {"year": 1523, "label": "The knights sail for Malta"},
              {"year": 1912, "label": "Italy takes the Dodecanese"},
              {"year": 1988, "label": "The Medieval City of Rhodes is listed"}]
    saved = graphics._encode

    def encode(frames, out, *a, **k):
        for _ in frames:
            pass
        return Path(out)
    graphics._encode = encode
    try:
        for w, h in ((1080, 1920), (1920, 1080)):
            graphics.render_timeline(events, tmp_path / f"t{w}.mp4", width=w, height=h, photo=photo)
    finally:
        graphics._encode = saved


def _off_ladder(seen):
    import type_sites
    ladder = type_sites._ladder()
    out: dict[str, list[float]] = {}
    for site, size in seen:
        if site != "<outside>" and round(size, 3) not in ladder:
            out.setdefault(site, set()).add(size)
    return {k: sorted(v) for k, v in sorted(out.items())}


def _observe_in_a_fresh_process(tmp_path) -> dict[str, list[float]]:
    """render_all under the observer in a NEW interpreter, so no font any earlier test cached hides its creation."""
    import os
    import subprocess
    import sys
    env = {**os.environ, "FONT_SIZE": os.environ.get("FONT_SIZE", "80")}
    res = subprocess.run([sys.executable, str(ROOT / "tests" / "observe_render.py"), str(tmp_path)], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=1500, check=False)
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("OBSERVED=")), None)
    assert line is not None, res.stderr[-3000:]
    return json.loads(line[len("OBSERVED="):])


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
