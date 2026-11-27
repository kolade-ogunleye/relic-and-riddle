"""Freeze cycle 6, THENNOW stream (panel 7; lander rulings 2026-11-27).

T1 (MO-T1 / MO-TN-REVERSE, MO2 8.0 on all 12 then-and-now cases): one eased wipe from the far edge straight to the
split, monotonic, on the motion tokens; both year markers stay shown once in; the old photo holds alone for its
marker's reading time, the split for both. Guarded on the production encode (then_now._encode) of a real render.
T2 (AD-T1 / CD-A1, Memnon 9:16): (a) each year plate in its own panel's calmest documented place; (b) a 9:16
unregistered pair laid out per panel at full width (its central half held, or contained), never a crop that halves
the subject to fill a panel.
"""
from __future__ import annotations

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
    with brand.use(brand.DEFAULT_BRAND.derive("probe wipe", system={"motion.wipe_step": 20.0, "motion.reveal": 0.6})):
        moved = then_now.wipe_seconds(*TALL), then_now.then_dwell("THEN 1876")
    assert moved[0] > base[0] and moved[1] == pytest.approx(base[1] + 0.2, abs=1 / FPS)
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


# --------------------------------------------------------------------------------- T2b: the 9:16 panels' crops
def test_an_unregistered_916_pair_is_laid_out_per_panel_at_full_width(tmp_path):
    """Lander ruling 2026-11-27 (T2b): each panel keeps its photograph's central region, at least the share of its
    height the 16:9 frame shows, and here its whole width (the NOW photo's larger figure stands at 16-48 % of its
    width and the other at 62-92 %: a narrower crop cuts one). The shared window this replaced showed the NOW
    photo's foot only (the Memnon base, panel 7 CD-A1)."""
    old, new = _memnon_like(tmp_path)
    a, b, info = then_now.frame_pair(old, new, *TALL)
    assert info["registered"] is False and info["layout"] == "panels"
    _, _, wide = then_now.frame_pair(old, new, *WIDE)
    for which, path in (("then", old), ("now", new)):
        shown = info["panels"][which]["shown"]
        assert shown["centre_half"] and shown["w"] >= 0.96 - 1e-3                   # across: the whole width
        with Image.open(path) as im:
            share16 = min(1.0, (im.width / im.height) / (16 / 9)) / (1 + then_now.PUSH)
        assert shown["h"] >= share16 - 0.005                                       # at least 16:9's share
    # the NOW photograph's figures are both whole in its panel at rest (dark blocks against the sky)
    rest = then_now.compose(then_now.pushed(a, then_now.CLIP_SECONDS, TALL),
                            then_now.pushed(b, then_now.CLIP_SECONDS, TALL), then_now._split_at(*TALL))
    now = np.asarray(rest.convert("L"))[then_now._split_at(*TALL) + 10:, :]
    figure = (now > 35) & (now < 95)                                                # the figures (INK is darker)
    cols = np.flatnonzero(figure.mean(axis=0) > 0.15)
    runs = np.split(cols, np.flatnonzero(np.diff(cols) > 1) + 1)
    assert len(runs) == 2 and runs[0][0] > 20 and runs[-1][-1] < 1060               # both whole, neither cut
    assert "panels" not in wide                                                     # 16:9: the reference, unchanged


def test_each_photograph_keeps_its_panel_place_through_the_clip(tmp_path):
    """Only the shared push moves a panel's photograph: the old one opens where it rests (a builder's trial that slid
    it from whole to its panel with the wipe stepped the mean luma 13-15 a frame on Roman 9:16, over the 8.0 limit)."""
    old = _photo(tmp_path / "then_tall.png", 1000, 1160, [(0.3, 0.2, 0.7, 0.9)], seed=3)     # a 0.86 portrait
    new = _photo(tmp_path / "now_w.png", 1500, 1000, [(0.3, 0.3, 0.6, 0.8)], seed=4)
    a, b, info = then_now.frame_pair(old, new, *TALL)
    g = info["panels"]["then"]
    assert g["mode"] == "rows" and g["y"] < 0 and set(g) >= {"x", "y", "w", "h", "shown"} and "slide_px" not in g
    assert a.size == b.size == (round(1080 * (1 + then_now.PUSH)), round(1920 * (1 + then_now.PUSH)))


def test_a_photograph_too_tall_for_its_panel_is_contained(tmp_path):
    """Where a panel cannot hold the photograph's central half (a 0.45 portrait in the 9:16 THEN panel), it stands
    whole in its panel, centred, the INK field beside it: the designed alternative, recorded."""
    old = _photo(tmp_path / "then_pillar.png", 900, 2000, [(0.3, 0.1, 0.7, 0.95)], seed=5)
    new = _photo(tmp_path / "now.png", 1400, 1000, seed=6)
    a, b, info = then_now.frame_pair(old, new, *TALL)
    g = info["panels"]["then"]
    assert g["mode"] == "contained" and g["w"] < a.width and g["x"] > 0
    assert g["shown"]["centre_half"]


def test_a_registered_916_pair_keeps_the_shared_window(tmp_path):
    old = _photo(tmp_path / "o.png", 1600, 1200, [(0.3, 0.3, 0.6, 0.8)], seed=7)
    new = _photo(tmp_path / "n.png", 1600, 1200, [(0.3, 0.3, 0.6, 0.8)], sky=(110, 160, 220), seed=8)
    _, _, info = then_now.frame_pair(old, new, *TALL)
    assert info["registered"] is True and "panels" not in info


# -------------------------------------------------------------------------- T2a: each plate in its calmest place
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
