"""Freeze cycle 6, THENNOW stream (panel 7; lander rulings 2026-11-27).

T1 (MO-T1 / MO-TN-REVERSE, MO2 8.0 on all 12 then-and-now cases): one eased wipe from the far edge straight to the
split, monotonic, on the motion tokens; both year markers stay shown once in; the old photo holds alone for its
marker's reading time, the split for both. Guarded on the production encode (then_now._encode) of a real render.
T2 (AD-T1 / CD-A1, Memnon 9:16): (a) each year plate in its own panel's calmest documented place; (b) a 9:16
unregistered pair laid out per panel at full width (its central half held, or contained), never a crop that halves
the subject to fill a panel.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand, brand_system, config, then_now

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs/reviews/freeze-system-2026-11-15/corpus"))
FPS = config.VIDEO_FPS
TALL, WIDE = (1080, 1920), (1920, 1080)


def _photo(path: Path, w: int, h: int, blocks=(), sky=(214, 208, 196), seed=0) -> Path:
    """A photograph: a grained sky with dark textured blocks (x0, y0, x1, y1 as shares). No band at an edge (a flat
    or distinct edge band reads as a scan border or a caption strip and is cut before framing)."""
    rng = np.random.default_rng(seed)
    a = np.empty((h, w, 3), np.float32)
    a[:] = sky
    a += rng.normal(0, 7, (h, w, 1))                     # a photographed sky's grain
    for x0, y0, x1, y1 in blocks:
        X0, Y0, X1, Y1 = int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)
        a[Y0:Y1, X0:X1] = (70, 58, 48)
        a[Y0:Y1, X0:X1] += rng.normal(0, 30, (Y1 - Y0, X1 - X0, 1))
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path)
    return path


def _memnon_like(tmp_path: Path) -> tuple[Path, Path]:
    """Two photographs of one place framed apart, of different sizes (not registered), as the dev Memnon pair:
    a 1.29 landscape with two figures, and a 1.22 one whose larger figure stands left and the other far right."""
    old = _photo(tmp_path / "then.png", 1680, 1300, [(0.20, 0.30, 0.38, 0.70), (0.55, 0.38, 0.70, 0.70)], seed=1)
    new = _photo(tmp_path / "now.png", 1460, 1200, [(0.16, 0.20, 0.48, 0.80), (0.62, 0.38, 0.92, 0.76)],
                 sky=(110, 160, 220), seed=2)
    return old, new


# ------------------------------------------------------------------------------------------------ T1: the one wipe
@pytest.mark.parametrize("size", [TALL, WIDE])
def test_the_divider_travels_once_straight_to_the_split(size):
    """Lander ruling 2026-11-27 (T1): from the far edge to the settled split, never back, never past it. The old
    schedule (WIPE to_far 1.0 / hold_far 0.25 / settle 0.85, SWEEP_TO 0.8) went to 0.2 of the edge and back."""
    w, h = size
    edge, split = max(w, h), then_now._split_at(w, h)
    t0, t1 = then_now.wipe_window(w, h, "THEN 1876")
    pos = [then_now.divider_at(i / FPS, w, h, "THEN 1876") for i in range(int(then_now.CLIP_SECONDS * FPS))]
    assert all(b <= a + 1e-9 for a, b in zip(pos, pos[1:]))                     # monotonic
    assert min(pos) == pytest.approx(split) and pos[0] == edge                   # never past the split
    assert all(p == edge for i, p in enumerate(pos) if i / FPS <= t0)            # the old photo whole until read
    assert all(p == pytest.approx(split) for i, p in enumerate(pos) if i / FPS >= t1)
    steps = [a - b for a, b in zip(pos, pos[1:])]
    assert max(steps) <= brand_system.tok("motion.wipe_step") + 1e-6          # the token's peak speed
    assert not any(hasattr(then_now, k) for k in ("WIPE", "SWEEP_TO", "SETTLE_MAX_STEP", "settle_seconds"))


def test_the_wipe_reads_the_motion_tokens():
    """BC: no local constant restates a motion value; a brand that moves the tokens moves the wipe."""
    base = then_now.wipe_seconds(*TALL), then_now.then_dwell("THEN 1876")
    with brand.use(brand.DEFAULT_BRAND.derive("probe wipe", system={"motion.wipe_step": 40.0, "motion.reveal": 0.45})):
        moved = then_now.wipe_seconds(*TALL), then_now.then_dwell("THEN 1876")
    assert moved[0] < base[0] and moved[1] == pytest.approx(base[1] + 0.05, abs=1 / FPS)
    assert then_now.wipe_progress(0.0, *TALL) == 0 and then_now.wipe_progress(then_now.CLIP_SECONDS, *TALL) == 1


@pytest.mark.parametrize("size", [TALL, WIDE])
@pytest.mark.parametrize("years", [(1876, 2011), (1934, 2018), (1890, 1990)])
def test_each_view_is_long_enough_to_read(size, years):
    """The dwell rule: the old photo alone for its marker's entry (motion.reveal) and its text read twice at
    motion.caption_cps (never under the composer's reading need); the split, after the NOW marker's entry, for both
    markers' text read twice."""
    then_text = then_now.then_text(years[0])
    both = f"{then_text} {then_now.kicker_word('now', years[1])} {years[1]}"
    reveal = brand_system.tok("motion.reveal")
    assert then_now.read_seconds(then_text) >= 2 * len(then_text) / brand_system.tok("motion.caption_cps") - 1e-9
    assert then_now.read_seconds(then_text) >= len(then_text) * config.CAPTION_READ_PER_CHAR + config.CAPTION_READ_BASE
    assert then_now.then_dwell(then_text) >= reveal + then_now.read_seconds(then_text) - 1e-9
    assert then_now.hold_seconds(*size, then_text) >= then_now.read_seconds(both)


def _encoded(tmp_path, old, new, size, years=(1876, 2011)):
    from src.art_direction import use_palette
    use_palette(None)
    clip = then_now.render(old, years[0], new, years[1], tmp_path / f"tn_{size[0]}.mp4", size[0], size[1])
    meta = dict(then_now.last_meta)
    w, h = size
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w), meta


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_the_encoded_reveal_is_monotonic_and_the_markers_stay(tmp_path, size):
    """At the encoded-clip level (then_now._encode, the production encoder): the divider found on every decoded frame
    never moves back nor passes the split; each marker, once fully in, stays (its plate's pixels against the last
    frame); no frame steps the mean luma by more than the system's per-frame limit. The corpus script
    (thennow_t1_motion.py) applies the same measure to every dev pair; it FAILS at the cycle-5 base (the divider 384
    px past the split on 9:16, the THEN marker gone 1.4-2 s)."""
    import thennow_t1_motion as t1
    if size == TALL:    # a busy portrait THEN (Roman 1934 is 0.86): the panel cuts its rows, the frame shows its foot
        old = _photo(tmp_path / "then_p.png", 1000, 1160, [(0.1, 0.1, 0.9, 0.95)], seed=13)
        new = _memnon_like(tmp_path)[1]
    else:
        old, new = _memnon_like(tmp_path)
    old, new = _registered(tmp_path, old, new)          # the pair production draws (source() -> register())
    fr, meta = _encoded(tmp_path, old, new, size)
    w, h = size
    axis = 0 if w > h else 1
    edge, split = max(w, h), then_now._split_at(w, h)
    last = fr[-1]
    bone = float(np.median(last[split - 2:split + 3, :] if axis == 1 else last[:, split - 2:split + 3]))
    pos = []
    for f in fr:
        p = t1.find_divider(f, axis, bone)
        pos.append(float(edge) if p is None and not pos else (pos[-1] if p is None else float(p)))
    assert max(b - a for a, b in zip(pos, pos[1:])) <= t1.TOL_BACK
    assert min(pos) >= split - t1.TOL_PAST and abs(pos[-1] - split) <= 2
    plan = [then_now.divider_at(i / FPS, w, h, then_now.then_text(1876)) for i in range(len(fr))]
    for which in ("then", "now"):
        sched = meta["schedule"][which]
        ok, why = t1.monotone_in(sched, 1e-9, 0.999, 0.999)
        assert ok, (which, why)
        zero = [i for i, a in enumerate(sched) if a <= 0]
        box = meta["groups"][which]
        lo = box[0] if axis == 0 else box[1]
        bare = zero[0] if which == "then" else [i for i in zero if plan[i] <= lo][-1]
        pres = t1.presence(fr, box, bare, len(fr) - 1)
        ok, why = t1.monotone_in(pres[bare:], t1.IN_STEP, t1.IN_FULL, t1.IN_KEEP)
        assert ok, (which, why)
    mad = [float(np.abs(fr[i].astype(np.int16) - fr[i - 1].astype(np.int16)).mean()) for i in range(1, len(fr))]
    from src import end_card
    assert max(mad) <= end_card.DIM_STEP_MAX


# ---------------------------------------------------------------------- T2: what production draws (review round 1)
def _registered(tmp_path: Path, old: Path, new: Path, name: str = "reg") -> tuple[Path, Path]:
    """A registered pair as production's source() returns it: through then_now.register (here the identity matrix,
    the recent photo cover-fitted to the old one's frame: no real registered pair exists in dev)."""
    from PIL import ImageOps
    o = Image.open(old).convert("RGB")
    fit = tmp_path / f"{name}_fit.png"
    ImageOps.fit(Image.open(new).convert("RGB"), o.size, Image.LANCZOS).save(fit)
    pair = then_now.register(old, fit, np.array([[1.0, 0, 0], [0, 1.0, 0]]), tmp_path / name)
    assert pair is not None
    return pair


def test_an_unregistered_pair_is_refused_by_production_and_recorded(tmp_path, monkeypatch):
    """Review round 1 (lander ruling 2026-11-27): an unregistered pair is not a then-and-now. make() draws nothing
    when source() finds no registered pair and writes why beside the run; render() of an unregistered pair (a
    board stand-in) is drawn as at base, recorded "unregistered" (the 9:16 'panels' layout is withdrawn)."""
    monkeypatch.setattr(then_now, "source", lambda topic, wd: then_now.LAST_REFUSAL.append(
        {"old": 1, "reason": "not registered: the pair does not share a viewpoint"}) or None)
    clip, credits = then_now.make({"location": {"name": "x"}}, tmp_path)
    assert clip is None and credits == []
    rec = json.loads((tmp_path / then_now.REFUSAL_SIDECAR).read_text(encoding="utf-8"))
    assert rec["drawn"] is False and "not registered" in rec["refused"][0]["reason"]
    old, new = _memnon_like(tmp_path)
    for size in (TALL, WIDE):
        a, b, info = then_now.frame_pair(old, new, *size)
        assert info["registered"] is False and "not registered" in info["unregistered"]
        assert "panels" not in info and info.get("layout") is None
        assert not hasattr(then_now, "panel_geometry")


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_a_registered_window_keeps_the_pictures_central_region(tmp_path, size):
    """Review round 1 (ruling 2026-11-27): a registered pair is one picture on one shared window. The window keeps
    the picture's central region: here a 4:3 picture whose top is empty sky, which the T7 search would zoom and lift
    (base: the window went up to 1.6x round the subject's top, holding under 0.3 of the picture's width at 9:16)."""
    rng = np.random.default_rng(21)
    a = np.full((1500, 2000, 3), 220, np.float32) + rng.normal(0, 2, (1500, 2000, 1))   # an empty sky
    a[700:] = rng.integers(30, 200, (800, 2000, 1))                                     # a busy lower half
    old = tmp_path / "old.png"
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(old)
    reg = _registered(tmp_path, old, old)
    _, _, info = then_now.frame_pair(reg[0], reg[1], *size)
    assert info["registered"] is True
    big = (round(size[0] * (1 + then_now.PUSH)), round(size[1] * (1 + then_now.PUSH)))
    with Image.open(reg[0]) as im:
        psize = im.size
    win = then_now._cover_window(psize, big, info.get("zoom", 1.0), info.get("v_centre", 0.5), info.get("u_centre", 0.5))
    assert then_now.keeps_centre(win, psize, big), info
    r = then_now.central_region(psize, big)
    if size == TALL:
        assert r[2] - r[0] < 0.5 and info.get("zoom", 1.0) == 1.0     # a 4:3 picture at 9:16: centred, unzoomed


def test_a_registered_916_pair_keeps_the_shared_window(tmp_path):
    old = _photo(tmp_path / "o.png", 1600, 1200, [(0.3, 0.3, 0.6, 0.8)], seed=7)
    new = _photo(tmp_path / "n.png", 1600, 1200, [(0.3, 0.3, 0.6, 0.8)], sky=(110, 160, 220), seed=8)
    _, _, info = then_now.frame_pair(old, new, *TALL)
    assert info["registered"] is True and "panels" not in info


def _plan_only(monkeypatch, tmp_path, old, new, size, years=(1876, 2011)):
    monkeypatch.setattr(then_now, "_encode", lambda frames, out, n, **k: out)
    then_now.render(old, years[0], new, years[1], tmp_path / "x.mp4", size[0], size[1])
    return dict(then_now.last_meta)


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_each_year_plate_takes_its_own_panels_calmest_place(monkeypatch, tmp_path, size):
    """Lander ruling 2026-11-27 (T2a): a year plate sits in its panel's calmest documented place, the same rule on
    both formats. Here the THEN photograph is busy where the NOW one is calm and the reverse: T10's one side for
    both markers (cycle-5 base) put one of them on the busy ground."""
    w, h = size
    if w > h:     # THEN busy by the divider (its right), NOW busy at its outer edge (its right)
        old = _photo(tmp_path / "t.png", 1920, 1080, [(0.25, 0.1, 0.5, 0.6)], seed=11)
        new = _photo(tmp_path / "n.png", 1700, 1080, [(0.72, 0.1, 0.97, 0.6)], seed=12)
    else:         # THEN busy at its left, NOW busy at its right
        old = _photo(tmp_path / "t.png", 1500, 1100, [(0.04, 0.1, 0.5, 0.9)], seed=11)
        new = _photo(tmp_path / "n.png", 1300, 1100, [(0.5, 0.1, 0.96, 0.9)], seed=12)
    meta = _plan_only(monkeypatch, tmp_path, old, new, size)
    side = meta["side"]
    for which in ("then", "now"):
        d = side[which]["detail"]
        got = d[side[which]["side"]]
        assert got <= min(d.values()) / (1 - then_now.SIDE_MARGIN) + 1e-6, (which, side[which])
        default = side["candidates"][0]
        assert side[which]["side"] == default or got <= (1 - then_now.SIDE_MARGIN) * d[default] + 1e-6
    if w > h:
        assert side["then"]["side"] == "outer" and side["now"]["side"] == "inner"
    else:
        assert side["then"]["side"] != "left" and side["now"]["side"] == "left"


def test_ground_detail_reads_structure_not_tone():
    flat_dark = Image.new("RGB", (300, 200), (30, 30, 30))
    flat_light = Image.new("RGB", (300, 200), (230, 230, 230))
    rng = np.random.default_rng(0)
    busy = Image.fromarray(rng.integers(0, 255, (200, 300, 3), dtype=np.uint8))
    box = (20, 20, 280, 180)
    assert then_now.ground_detail(flat_dark, box) == 0 == then_now.ground_detail(flat_light, box)
    assert then_now.ground_detail(busy, box) > 5


# The real dev pairs (Memnon, Roman, every then_k x now_k_j, both formats) are measured by the corpus scripts
# docs/reviews/freeze-system-2026-11-15/corpus/thennow_t1_motion.py and thennow_t2_layout.py (CI has no dev runs).


# ------------------------------------------------------------------------ review round 1: B2, B3, D5, D6
PALETTES = ("default", "second", "second-colours")


def _palette(name: str):
    sys.path.insert(0, str(ROOT / "docs/reviews/freeze-system-2026-11-15"))
    import board
    board.configure_palette(ROOT, name)


@pytest.fixture
def default_brand_after():
    yield
    brand.configure(None)


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_the_place_is_the_same_in_every_palette_and_every_plate_holds_contrast_high(
        monkeypatch, tmp_path, size, default_brand_after):
    """B3: the place is chosen on the photographs' own pixels (no divider, INK or plate in a candidate box), so it is
    the same in the three palettes. B2: each plate's text holds contrast.high (7.0) through its tiers (held, then
    opaque INK); the second palettes' held plates read 5.99-6.25 at the cycle-6 first tip."""
    old, new = _registered(tmp_path, *_memnon_like(tmp_path))
    sides, worst = {}, {}
    for pal in PALETTES:
        _palette(pal)
        meta = _plan_only(monkeypatch, tmp_path, old, new, size)
        sides[pal] = (meta["side"]["then"]["side"], meta["side"]["now"]["side"])
        for w in ("then", "now"):
            t = meta["side"][w]["tier"]
            assert t["contrast"] is not None and t["contrast"] >= brand_system.tok("contrast.high") - 1e-9, (pal, w, t)
            worst[pal, w] = t
    assert len(set(sides.values())) == 1, sides


def test_a_held_plate_under_the_margin_goes_opaque(monkeypatch, tmp_path, default_brand_after):
    """B2's tier: on a bright ground the held plate (plate.held 0.875) lets light through; under contrast.high plus
    the re-encode margin it is drawn opaque INK, and its text then reads its fill against INK."""
    _palette("second-colours")     # its gold on the held plate read 6.19-6.25 over these grounds at the first tip
    old, new = _registered(tmp_path, *_memnon_like(tmp_path))
    meta = _plan_only(monkeypatch, tmp_path, old, new, TALL)
    tiers = {w: meta["side"][w]["tier"] for w in ("then", "now")}
    assert any(t["tier"] == "opaque" for t in tiers.values()), tiers
    for t in tiers.values():
        assert t["contrast"] >= brand_system.tok("contrast.high") - 1e-9
        if t["tier"] == "opaque":
            assert t["held_contrast"] < brand_system.tok("contrast.high") + then_now.HIGH_MARGIN


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_the_now_marker_enters_on_the_tokens_after_the_landing(monkeypatch, tmp_path, size):
    """D5: the NOW marker enters over motion.reveal on ease_out, never before the divider has landed, on every
    placement (16:9 "outer" showed at 2.3 s while the divider landed at 3.07 s)."""
    if size == WIDE:   # NOW busy against the divider, calm at its outer edge: it takes "outer"
        old = _photo(tmp_path / "t.png", 1920, 1080, [(0.05, 0.1, 0.3, 0.6)], seed=41)
        new = _photo(tmp_path / "n.png", 1920, 1080, [(0.5, 0.1, 0.72, 0.6)], seed=42)
    else:
        old, new = _memnon_like(tmp_path)
    old, new = _registered(tmp_path, old, new)
    meta = _plan_only(monkeypatch, tmp_path, old, new, size)
    if size == WIDE:
        assert meta["side"]["now"]["side"] == "outer"
    t1 = then_now.wipe_window(*size, then_now.then_text(1876))[1]
    reveal = brand_system.tok("motion.reveal")
    from src.brand_system import ease_out
    for i, a in enumerate(meta["schedule"]["now"]):
        t = i / FPS
        assert a == pytest.approx(0.0 if t < t1 - 1e-9 else ease_out((t - t1) / reveal), abs=1e-4), (i, a)


def test_the_split_reads_both_markers_at_both_ends_of_the_wipe_range():
    """D6: the split's hold after the NOW marker's entry covers both markers' reading time (the longest pair,
    'THEN 0000 LATER 0000') at both ends of motion.wipe_step's range; a brand breaking it is refused at configure."""
    lo, hi = brand_system.TOKEN_RANGES["motion.wipe_step"]
    assert then_now.timing_problems(brand.DEFAULT_BRAND.system) == []
    for step in (lo, hi):
        cfg = brand.DEFAULT_BRAND.derive(f"wipe {step}", system={"motion.wipe_step": step})
        assert then_now.timing_problems(cfg.system) == []
        for size in (TALL, WIDE):
            t = then_now.timing(brand_system.tok("motion.reveal"), step, brand_system.tok("motion.caption_cps"), *size)
            assert t["hold"] >= t["need"]
    with pytest.raises(ValueError, match="then-and-now"):
        brand.DEFAULT_BRAND.derive("slow reveal", system={"motion.reveal": 0.8})
