"""Design freeze lane F, cycle 1 (hook, chapter and myth cards, callouts, badge): structural invariants.

The hook's plate and wash leave with its type (panel 2 MO, clips\\hook__obelisk__916 f037-f039: an empty INK band
stood across the photo for three frames after the type had gone)."""

from __future__ import annotations

import numpy as np
import pytest

from src import design_cards as dc
from src.brand_system import SPEC
from src.config import VIDEO_FPS

import tests.test_freeze_cards_2026_11_04 as F

FRAMES = range(round(dc.HOOK_SECONDS * VIDEO_FPS) + 1)


def test_hook_motion_equals_the_written_tokens():
    """Until SYS-F-1 makes them runtime tokens, the module values stay equal to the written SPEC ones."""
    assert dc.HOOK_HOLD == SPEC["motion.hook_hold"] and dc.HOOK_FADE == SPEC["motion.hook_fade"]


def test_lane_f_hook_ground_leaves_with_type():
    """On every frame the ground is at least as present as the type it carries, on a frame without type it is at most
    HOOK_EMPTY_MAX, and it is gone by CAPTION_CLEAR."""
    for i in FRAMES:
        t = i / VIDEO_FPS
        a, g = dc._hook_alpha(t), dc._hook_ground_alpha(t)
        assert g >= a - 1e-9, (i, a, g)
        if a == 0.0:
            assert g <= dc.HOOK_EMPTY_MAX, (i, g)
    assert dc._hook_ground_alpha(dc.CAPTION_CLEAR) == 0.0
    # the type still holds whole through frame 32 (H4), and so does its ground
    assert all(dc._hook_ground_alpha(i / VIDEO_FPS) == 1.0 for i in range(33))


@pytest.mark.parametrize("ground", ["sky", "dark", "warm"])
def test_lane_f_no_empty_plate_on_the_drawn_frames(ground, photos):
    """Measured on the pixels: on a frame whose type is gone, the plate band departs from the bare pushed photo by at
    most HOOK_EMPTY_MAX of the full plate's departure (frame 0), so no empty band is ever on screen."""
    plan = F.hook_plan(photos[ground], "Quartzite ridges form the ring", "f_geo")
    if plan.meta["scrim"]["mode"] != "plate":
        pytest.skip("wash ground: no plate band")
    y0, y1 = plan.meta["scrim"]["plate"]
    bg = plan.meta["_r"]["bg"]

    def departure(i):
        shown = np.asarray(dc.frame_at(plan, i).convert("L")).astype(float)[y0:y1]
        bare = np.asarray(dc._pushed_in(bg, 1 + dc.LOOP_PUSH * i).convert("L")).astype(float)[y0:y1]
        return float(np.abs(shown - bare).mean())

    full = departure(0)
    assert full > 0
    empty = [i for i in FRAMES if dc._hook_alpha(i / VIDEO_FPS) == 0.0]
    assert empty
    for i in empty:
        assert departure(i) <= dc.HOOK_EMPTY_MAX * full + 1.0, (i, departure(i), full)


@pytest.fixture(scope="module")
def photos(tmp_path_factory):
    d = tmp_path_factory.mktemp("lane_f_photos")
    return {k: F.T._photo(d / f"{k}.jpg", k) for k in ("sky", "dark", "warm")}


LOADERS = ("get_font", "truetype", "fit_text", "probe_text", "font_variant", "FreeTypeFont", "FeaturedFont")


@pytest.mark.parametrize("module", ["design_cards", "callouts"])
def test_lane_f_faces_come_from_brand_roles(module):
    """Panel 2 lead (second brand: callout labels kept a default face): every font a lane F surface loads names its
    face through a value, never a family literal, so a configured brand's roles reach the hook, chapter, myth and quote
    cards and the callout labels. (The caption and badge roles are read by video_composer, not here.)"""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / f"{module}.py"
    literal = []
    for n in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and n.args:
            name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            if name in LOADERS and isinstance(n.args[0], (ast.Constant, ast.JoinedStr)):
                literal.append((n.lineno, ast.unparse(n)[:80]))
    assert not literal, literal
