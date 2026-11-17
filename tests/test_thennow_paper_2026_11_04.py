"""Then-and-now, freeze panel 2026-11-04 step 1: ink type on paper.

A THEN / NOW marker whose ground is plain paper (near-white, flat, no dark structure) is set in brand INK with no
halo and no glow; every other ground keeps the bone/gold type, halo and glow exactly as before.
"""
import hashlib

import numpy as np
from PIL import Image, ImageDraw

from src import then_now
from src.brand import INK, PLACE_TARGET
from src.typography import get_font


def _flat(path, size=(1200, 2100), base=236, spread=10):
    """A near-white paper ground: a gentle diagonal tone, no structure."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    v = base + spread * (xx / w + yy / h) / 2
    arr = np.repeat(np.clip(v, 0, 255).astype(np.uint8)[..., None], 3, axis=2)
    Image.fromarray(arr).save(path)
    return path


def _busy(path, size=(1200, 2100), seed=4):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (size[1] // 6, size[0] // 6, 3), dtype=np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


def _capture(monkeypatch):
    got = {"frames": []}

    def enc(frames, out, n, **k):
        h = hashlib.sha256()
        for im in frames:
            h.update(im.convert("RGB").tobytes())
            got["last"] = im
        got["sha"] = h.hexdigest()
        return out
    monkeypatch.setattr(then_now, "_encode", enc)
    monkeypatch.setattr(then_now, "record_contrast", lambda clip, frame, rows: got.update(rows=rows))
    return got


BOX = [(100, 100, 260, 140), (96, 172, 420, 300)]       # a kicker over a wider year, as the markers sit


# ---------------------------------------------------------------- the gate
def test_paper_gate_passes_a_flat_near_white_ground():
    w, h = 600, 500
    arr = np.full((h, w, 3), 238, np.uint8)
    arr += (np.arange(w, dtype=np.uint8)[None, :, None] // 100)
    im = Image.fromarray(arr)
    ok, st = then_now.paper_gate(BOX, [(im, (0, 0, w, h)), (im, (0, 0, w, h))])
    assert ok, st
    assert st[0]["mean"] >= then_now.PAPER_MEAN and st[0]["dark"] == 0 and st[0]["edge"] <= then_now.PAPER_EDGE


def test_paper_gate_fails_a_busy_photo(tmp_path):
    im = Image.open(_busy(tmp_path / "b.jpg", size=(600, 500)))
    ok, st = then_now.paper_gate(BOX, [(im, (0, 0, 600, 500))])
    assert not ok, st


def test_paper_gate_fails_white_line_art_with_black_rules_and_labels():
    # a diagram: white paper, thin black rules and its own black labels; bright and fairly flat on average
    w, h = 600, 500
    im = Image.new("RGB", (w, h), (246, 246, 244))
    d = ImageDraw.Draw(im)
    for x in range(40, w, 140):
        d.line([(x, 0), (x, h)], fill=(10, 10, 10), width=2)
    d.line([(0, 230), (w, 230)], fill=(10, 10, 10), width=2)
    font = get_font("Anton", 40)
    d.text((120, 120), "A-A 41.8 m", font=font, fill=(0, 0, 0))
    d.text((150, 250), "SECTION", font=font, fill=(0, 0, 0))
    ok, st = then_now.paper_gate(BOX, [(im, (0, 0, w, h))])
    assert st[0]["mean"] >= then_now.PAPER_MEAN          # bright enough to pass the luma tests alone
    assert st[0]["dark"] > then_now.PAPER_DARK           # its own black linework stops it
    assert not ok


# ---------------------------------------------------------------- the render
def test_paper_markers_are_ink_with_no_halo_and_no_glow(tmp_path, monkeypatch):
    got = _capture(monkeypatch)
    shade_calls, band_calls = [], []
    real_shade, real_band = then_now._shade, then_now.band_mask
    monkeypatch.setattr(then_now, "_shade", lambda *a, **k: shade_calls.append(1) or real_shade(*a, **k))
    monkeypatch.setattr(then_now, "band_mask", lambda *a, **k: band_calls.append(1) or real_band(*a, **k))
    old = _flat(tmp_path / "old.png", base=232)
    new = _flat(tmp_path / "new.png", base=238)
    then_now.render(old, 1941, new, 1942, tmp_path / "x.mp4")
    assert {w: then_now.LAST_PAPER[w]["mode"] for w in ("then", "now")} == {"then": "ink", "now": "ink"}
    assert not shade_calls and not band_calls                # no glow anywhere
    rows = dict(got["rows"])
    assert set(rows) == {"then_kicker", "then_year", "now_kicker", "now_year"}
    for name, p in rows.items():
        assert p.fill == tuple(INK), name
        assert p.halo == 0 and p.halo_mask is None, name
        assert p.contrast >= PLACE_TARGET, (name, p.contrast)
        assert p.measure(got["last"]) >= PLACE_TARGET, (name, p.measure(got["last"]))
    # Operator decision 2026-11-04: the recent photo's kicker reads LATER when that photo is itself historical
    # (1942, more than NOW_YEARS before the current UTC year), NOW otherwise; it was "NOW" here.
    assert rows["then_kicker"].text == "THEN" and rows["now_kicker"].text == "LATER"


def test_one_paper_marker_makes_the_other_kicker_bone(tmp_path, monkeypatch):
    got = _capture(monkeypatch)
    old = _flat(tmp_path / "old.png", base=234)
    new = _busy(tmp_path / "new.jpg")
    then_now.render(old, 1941, new, 1942, tmp_path / "x.mp4")
    assert then_now.LAST_PAPER["then"]["mode"] == "ink" and then_now.LAST_PAPER["now"]["mode"] == "bone/gold"
    rows = dict(got["rows"])
    assert rows["now_kicker"].fill == tuple(then_now.WHITE)
    assert rows["now_year"].fill == tuple(then_now.WHITE)


def test_a_non_paper_render_plans_as_before(tmp_path, monkeypatch):
    """Off paper the rule does nothing: the render is byte for byte the one with the rule switched off (the HEAD
    path), plates and every frame."""
    a = _busy(tmp_path / "a.jpg", seed=2)
    b = _busy(tmp_path / "b.jpg", seed=3)
    runs = []
    for flag in (True, False):
        got = _capture(monkeypatch)
        monkeypatch.setattr(then_now, "INK_ON_PAPER", flag)
        then_now.render(a, 1925, b, 2019, tmp_path / f"{flag}.mp4")
        runs.append((got["sha"], [(n, p.fill, p.halo, p.contrast, p.box) for n, p in got["rows"]],
                     {w: then_now.LAST_PAPER[w]["mode"] for w in ("then", "now")}))
    assert runs[0][2] == {"then": "bone/gold", "now": "bone/gold"}
    assert runs[0][0] == runs[1][0]
    assert runs[0][1] == runs[1][1]
