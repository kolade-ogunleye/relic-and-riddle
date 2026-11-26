"""Design-freeze cycle 0, round 7 (2026-11-16): the preview design panel's findings (queue CYCLE0.preview-panel-1.md)
built by the cycle-0 builder itself; the lanes' findings have their own test files.

- F7: the light-ground variant (the name in INK) sits on its own least bone LIFT under the dark scrim's cap; it is taken
  where bone stays short of the target under the full scrim and its halo and the INK name reaches it, and before the
  halo exception wherever bone misses the gate. Never on a dark ground; never above the cap.
- Ruling 2 made structural: the Reel cover and the hook frame have no masthead path (tests in
  test_cover_brand_legibility_2026_11_01.py and test_brand_system_r6.py).
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src import brand, brand_system, masthead

SIZE = brand_system.type_px("masthead", "carousel")
X, BASE, W, H = 107, 133, 1440, 700
GATE = brand_system.tok("contrast.masthead_gate")
TARGET = brand_system.tok("contrast.masthead_target")
PEAK = brand_system.tok("scrim.masthead_peak")


def _sky(v: int, mass: int | None = None) -> Image.Image:
    a = np.full((H, W, 3), v, np.uint8)
    if mass is not None:
        a[:, 400:600] = mass
    return Image.fromarray(a)


@pytest.mark.parametrize("v", [205, 230, 245, 255])
def test_on_a_near_white_ground_the_ink_variant_is_drawn(v):
    _out, plate, rec, _box = masthead.draw(_sky(v), X, BASE, SIZE, reach=BASE + 400)
    assert rec["shown"] and rec["variant"] == "light" and plate.fill == tuple(brand.INK)
    assert rec["contrast"] >= GATE and rec["light_lift"] <= PEAK + 1e-9 and rec["mark_contrast"] >= 3.0


def test_a_bright_sky_with_a_dark_mass_takes_a_lift_not_the_halo_exception(monkeypatch):
    """The round-6 exception case (a 230 sky with a dark mass under some letters): INK on a lift now reads, so the
    exception is not needed; the lift is the least that reaches the target (or the gate)."""
    monkeypatch.setattr(masthead, "MARGIN_REACH", ())   # freeze cycle 5: the ruled order at the surface's own reach
    _ground, p, rec = masthead.plan(_sky(230, 40), X, BASE, SIZE, reach=BASE + 400)
    assert rec["shown"] and rec["variant"] == "light" and rec["halo_exception"] is False
    assert 0.0 < rec["light_lift"] <= PEAK + 1e-9 and rec["contrast"] >= GATE
    goal = TARGET if rec["contrast"] >= TARGET else GATE
    less = masthead.top_scrim(_sky(230, 40), max(0.0, rec["light_lift"] - 0.02), BASE + 400, brand.BONE)
    from src.typography import plan_text
    q = plan_text(less, p.xy, p.text, p.font, p.fill, "ls", p.track, target=TARGET, max_halo=0.0)
    assert masthead.judge(less, q) < goal + 0.05   # a visibly smaller lift does not reach it (bisection resolution)


@pytest.mark.parametrize("v", [11, 40, 90])
def test_a_dark_ground_keeps_bone(v):
    _, _p, rec = masthead.plan(_sky(v), X, BASE, SIZE, reach=BASE + 400)
    assert rec["shown"] and rec["variant"] == "dark" and "light_lift" not in rec


def test_bone_that_reaches_the_target_is_never_replaced():
    """Grey 150: bone reaches the target with the dark scrim (no halo needed); the INK variant is not tried there."""
    _, _, rec = masthead.plan(_sky(150), X, BASE, SIZE, reach=BASE + 400)
    if rec["contrast"] >= TARGET and rec["variant"] == "dark":
        assert "light_lift" not in rec
