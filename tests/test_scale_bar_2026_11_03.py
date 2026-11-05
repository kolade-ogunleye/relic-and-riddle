"""Round 6 (2026-11-03): the dual-unit scale bar: one cased line, the narration's unit on top (round 7, coordinator
R1: KM without one), the primary a 1-2-5 length and the secondary a tick on the same bar (R2), ends that read apart,
labels on their own ticks, placed off the labels, the home fill and a route's land."""
import logging

import numpy as np
import pytest
from PIL import Image

from src import brand, graphics

KM_PER_DEG = graphics._km_per_deg(16.8)


def _bar(px_per_km: float, narration: str = "", rng=graphics.SCALE_RANGE_TALL, lat: float = 16.8):
    return graphics.scale_bar(lat, px_per_km * graphics._km_per_deg(lat), narration, bar_range=rng)


def test_one_bar_five_px_thick_and_no_alternating_segments() -> None:
    bar = _bar(0.9)
    im = Image.new("RGB", (600, 200), (60, 60, 60))
    graphics._draw_scale_bar(im, bar, 50, 100, 1.0)
    arr = np.asarray(im).astype(int)
    col = arr[:, 50 + round(bar["length"] / 2)]
    touched = np.flatnonzero(np.abs(col - 60).max(axis=1) > 10)
    assert len(touched) == 5 and touched.max() - touched.min() == 4        # 1 + 3 + 1
    core = arr[100, 55:50 + round(bar["length"]) - 5]
    assert (np.abs(core - np.array(graphics.SCALE_LINE)).max(axis=1) <= 2).all()   # one colour along it
    assert graphics.SCALE_BAR_LIFT == 0


# round 7 (deliberate, coordinator R1): KM on top without a narration unit (MI in round 6)
@pytest.mark.parametrize("narration, top", [("", "KM"), ("It is 280 feet deep.", "MI"), ("25 miles away", "MI"),
                                            ("44 metres tall", "KM"), ("about 40 km across", "KM")])
def test_the_primary_unit_is_on_top(narration, top) -> None:
    bar = _bar(0.9, narration)
    assert bar["unit"] == top
    plates = dict(graphics._scale_bar_plates(Image.new("RGB", (900, 300), brand.INK), bar, 50, 150))
    assert top in plates["scale"].text
    if bar["secondary"]:
        assert plates["scale_secondary"].box[1] > plates["scale"].box[3]


SPEC_PRIMARY = (1, 2, 5)          # coordinator R2: the primary's 1-2-5 series (x 10^n), as the spec gives it


def _mantissa(v: float) -> float:
    import math
    return round(v / 10 ** math.floor(math.log10(v) + 1e-9), 6)


@pytest.mark.parametrize("px_per_km", [0.05, 0.3, 0.6, 0.9, 1.7, 4.0, 20.0])
@pytest.mark.parametrize("rng", [graphics.SCALE_RANGE_TALL, graphics.SCALE_RANGE_WIDE])
def test_values_follow_the_spec_series_and_the_bar_is_the_primary(px_per_km, rng) -> None:
    # round 7 (deliberate, coordinator R2; was test_values_are_nice_and_the_longer_bar_is_in_range, which checked the
    # code's own SCALE_STEPS): the primary is 1, 2 or 5 x 10^n, the longest such length no longer than the range;
    # the secondary is a tick on that bar, never past its end, never the same number
    for narration in ("", "44 metres", "280 feet"):
        bar = _bar(px_per_km, narration, rng)
        assert _mantissa(bar.value) in SPEC_PRIMARY, bar.label
        _lo, hi = rng
        assert bar.length == bar.px <= hi + 0.5                            # the bar is the primary's length
        units = {"KM": px_per_km, "MI": px_per_km * 1.609344}
        longer = [m * 10 ** n for m in SPEC_PRIMARY for n in range(-1, 5)
                  if bar.value < m * 10 ** n and m * 10 ** n * units[bar.unit] <= hi + 1e-9]
        assert not longer, (bar.label, longer)                             # the longest 1-2-5 length in range
        if bar.secondary is not None:
            assert bar.secondary.px < bar.px and bar.secondary.value != bar.value
            assert bar.secondary.unit != bar.unit


def test_the_bijapur_case() -> None:
    # round 7 (deliberate, coordinator R2; round 6 expected 250 KM over 100 MI): 241 / 250 px a km on 16:9, the
    # primary 200 KM (193 px; 250 is not a 1-2-5 length), the secondary the nearest round MI tick that reads apart:
    # 80 MI at 124 px (100 MI, 155 px, is within 25 % of the end)
    px_per_km = 241 / 250
    bar = _bar(px_per_km, "44 metres", graphics.SCALE_RANGE_WIDE)
    assert bar["label"] == "200 KM" and bar["px"] == pytest.approx(193, abs=1)
    assert bar["secondary"]["label"] == "80 MI" and bar["secondary"]["px"] == pytest.approx(124, abs=2)
    assert not graphics.scale_ends_apart(193, 155) and graphics.scale_ends_apart(193, 124)
    assert not graphics.scale_ends_apart(241, 233)


def test_ends_read_apart_or_the_secondary_is_dropped(caplog) -> None:
    for px_per_km in np.linspace(0.05, 6, 120):
        bar = _bar(float(px_per_km))
        assert isinstance(bar, graphics.ScaleBar)                         # a frozen dataclass (lang-python)
        if bar.secondary:
            assert graphics.scale_ends_apart(bar.px, bar.secondary.px)
            assert bar.secondary.value != bar.value                       # round 6 review: never "50 KM / 50 MI"
            assert min(bar.px, bar.secondary.px) >= graphics.SCALE_SHORT_MIN_PX
    assert graphics.scale_ends_apart(200, 150) and not graphics.scale_ends_apart(200, 170)
    assert not graphics.scale_ends_apart(100, 70)               # 30 px apart
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        bar = graphics.scale_bar(16.8, 1.0, "", max_px=30, bar_range=(150, 230))   # too short for two
    assert bar["secondary"] is None and "drawn in one unit" in caplog.text


def test_labels_are_right_aligned_to_their_ticks_and_there_is_no_zero() -> None:
    for px_per_km in (0.3, 0.9, 2.2):
        bar = _bar(px_per_km)
        rows = dict(graphics._scale_bar_plates(Image.new("RGB", (900, 300), brand.INK), bar, 80, 150))
        assert abs(rows["scale"].box[2] - (80 + bar.px)) <= 1
        if bar.secondary:
            # right-aligned on its tick, or (round 6 review) where wider than its run, starting at the zero end
            sec = rows["scale_secondary"].box
            assert abs(sec[2] - (80 + bar.secondary.px)) <= 1 or abs(sec[0] - 80) <= 1
        assert all(p.box[0] >= 80 - 1 for p in rows.values())               # never left of the zero end
        assert all(p.text.split()[0] != "0" for p in rows.values())
        assert all(len(p.text.split()) == 2 for p in rows.values())        # number plus unit


def test_place_scale_bar_shifts_off_labels_and_the_home_fill() -> None:
    bar = _bar(0.9)
    home = np.zeros((1920, 1080), np.uint8)
    home[900:1100, 0:400] = 255                                           # the fill under the bottom-left
    lay = graphics.place_scale_bar(bar, 1080, 1920, [], home=home)
    box = lay.box
    sub = home[int(box[1]):int(box[3]), int(box[0]):int(box[2])] > 127
    assert sub.mean() <= graphics.SCALE_HOME_MAX
    assert lay.x > graphics.SCALE_ORIGIN_TALL[0] and (lay.x - graphics.SCALE_ORIGIN_TALL[0]) % 40 == 0
    label = (60, 990, 380, 1050)
    lay2 = graphics.place_scale_bar(bar, 1080, 1920, [label])
    assert not graphics.LayoutConstraintSolver.boxes_intersect(lay2.box, label)


@pytest.mark.skipif(not graphics.geo.available(), reason="Natural Earth data not downloaded")
def test_the_greece_route_bar_keeps_off_crete_and_the_legs(tmp_path, monkeypatch) -> None:
    from tests.test_r4_graphics_2026_11_02 import _render_fast
    cap: dict = {}
    real_place, real_with = graphics.place_scale_bar, graphics._with_bar

    def spy_place(bar, width, height, labels, home=None, obstacles=None, origin=None, box_x=None, prefer="l"):
        cap["obstacles"] = obstacles
        return real_place(bar, width, height, labels, home, obstacles, origin, box_x, prefer)

    def spy_with(rest, bar, x, y):
        cap["bar"] = (bar, x, y)
        return real_with(rest, bar, x, y)
    monkeypatch.setattr(graphics, "place_scale_bar", spy_place)
    monkeypatch.setattr(graphics, "_with_bar", spy_with)
    stops = [{"place": "Athens", "country": "Greece", "label": "Athens"},
             {"place": "Antikythera", "country": "Greece", "label": "Antikythera wreck"},
             {"place": "Rhodes", "country": "Greece", "label": "Rhodes"}]
    _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=1080, height=1920))
    bar, x, y = cap["bar"]
    box = graphics.scale_bar_box(bar, x, y, graphics.scale_label_font(1080, 1920))
    import cv2
    r = int(graphics.SCALE_COAST_AIR)
    obs = cap["obstacles"].astype(np.uint8)
    grown = cv2.dilate(obs, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    x0, y0, x1, y1 = (int(v) for v in box)
    assert not grown[y0:y1, x0:x1].any()                  # off the land (Crete) and 16 px from the legs


def test_route_scale_bar_motion_draws_then_shows_its_labels() -> None:
    assert graphics.scale_bar_motion(1.0, 1.0) == (0.0, 0.0)
    ab, al = graphics.scale_bar_motion(1.0 + graphics.SCALE_DRAW_S, 1.0)
    assert ab == pytest.approx(1.0) and al == pytest.approx(1.0)
    ab, al = graphics.scale_bar_motion(1.2, 1.0)
    assert 0 < ab < 1 and al == 0.0
