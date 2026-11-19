"""Design freeze, SYS batch 2, SYS-A-3: hero_window.subject_box (the reviewed focus, then registered subject
detectors, else None) and the then-and-now markers placed against it. Invariants, synthetic inputs only."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent



# ------------------------------------------------------------------ SYS-A-3: the subject box
def test_subject_box_is_none_without_a_reviewed_focus_or_a_detector(tmp_path):
    from src import hero_window
    p = tmp_path / "plain.png"
    Image.new("RGB", (400, 300), (120, 110, 90)).save(p)
    assert hero_window.SUBJECT_DETECTORS == [] or all(callable(d) for d in hero_window.SUBJECT_DETECTORS)
    saved = list(hero_window.SUBJECT_DETECTORS)
    hero_window.SUBJECT_DETECTORS.clear()
    try:
        assert hero_window.subject_box(Image.open(p), p) is None
        assert hero_window.subject_box(Image.open(p)) is None
    finally:
        hero_window.SUBJECT_DETECTORS[:] = saved


def test_subject_box_takes_the_reviewed_focus_before_a_detector(tmp_path, monkeypatch):
    from src import cover_design, hero_window
    p = tmp_path / "photo.png"
    Image.new("RGB", (400, 200), (120, 110, 90)).save(p)
    monkeypatch.setattr(cover_design, "reviewed_focus",
                        lambda path, kept=None: {"focus_box": [0.25, 0.5, 0.75, 1.0]} if path == p else None)
    det = lambda im: (0, 0, 10, 10)
    hero_window.register_subject(det)
    try:
        assert hero_window.subject_box(Image.open(p), p) == (100, 100, 300, 200)
        assert hero_window.subject_box(Image.open(p)) == (0, 0, 10, 10)          # no path: the detector
        bad = lambda im: (500, 500, 900, 900)   # outside the image: no box
        hero_window.unregister_subject(det)
        hero_window.register_subject(bad)
        assert hero_window.subject_box(Image.open(p)) is None
        hero_window.unregister_subject(bad)
    finally:
        hero_window.unregister_subject(det)


def test_hero_window_holds_no_topic_list():
    import ast
    tree = ast.parse((ROOT / "src" / "hero_window.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "subject_box")
    words = {c.value.lower() for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    assert not words & {"memnon", "colossi", "richat", "nazca", "aswan", "roman"}


@pytest.mark.parametrize("where", ["left", "middle", "right"])
def test_then_now_markers_keep_off_a_known_subject(where):
    from src import then_now
    sides = ("left", "centre:300", "centre:540", "centre:780", "right")
    pos = {"left": 0, "middle": 2, "right": 4}[where]
    shares = {s: (0.9 if i == pos else 0.0) for i, s in enumerate(sides)}
    sums = {s: 1.0 for s in sides}
    side = then_now.subject_side(shares, sums)
    assert shares[side] == 0.0
    if where != "left":
        assert side == "left"                 # left stays the default on a tie
    # the edge sum second: among the subject-free sides the quietest (by the margin) wins
    sums2 = dict(sums, right=0.1)
    if where != "right":
        assert then_now.subject_side(shares, sums2) == "right"


def test_the_subject_mask_follows_the_photo_through_frame_pair(tmp_path):
    """A registered detector marks a red block; after the trims, the window and the fit, the mask lies on the red."""
    import numpy as np

    from src import hero_window, then_now
    old, new = tmp_path / "old.png", tmp_path / "new.png"
    rng = np.random.default_rng(3)
    for p, x in ((old, 120), (new, 700)):
        a = (rng.random((900, 1000, 3)) * 160).astype(np.uint8)
        a[300:600, x:x + 180] = (230, 20, 20)
        Image.fromarray(a).save(p)

    def red(im):
        arr = np.asarray(im.convert("RGB")).astype(int)
        ys, xs = np.nonzero((arr[..., 0] > 200) & (arr[..., 1] < 60))
        return (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) if xs.size else None
    hero_window.register_subject(red)
    try:
        subjects: list = []
        a, b, _ = then_now.frame_pair(old, new, 540, 960, subjects=subjects)
    finally:
        hero_window.unregister_subject(red)
    for im, m in zip((a, b), subjects):
        assert m is not None and m.size == im.size
        arr = np.asarray(im.convert("RGB")).astype(int)
        is_red = (arr[..., 0] > 200) & (arr[..., 1] < 60)
        inside = np.asarray(m) > 127
        assert inside.any() and is_red[inside].mean() > 0.9
