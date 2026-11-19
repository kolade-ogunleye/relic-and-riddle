"""Design freeze, SYS batch 1 (the lander's approved system requests, 2026-11-19): invariants, never pinned
measurements.

- SYS-D-1: one figure-step ladder in the system (brand_system.FIGURE_STEPS), read by every surface's figure ladder.
- The display ladder (TYPE, TYPE_FALLBACK) keeps display_xl > display_l > display_m on every tier.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import pytest

from src import brand_system


# ------------------------------------------------------------------------------------ SYS-D-1: one figure ladder
def test_the_figure_ladder_is_one_descending_ladder_headed_by_the_figure_size():
    steps = brand_system.FIGURE_STEPS
    assert len(steps) > 1 and list(steps) == sorted(set(steps), reverse=True)
    assert steps[0] == brand_system.TYPE["tall"]["figure"]


def test_every_surface_figure_ladder_is_the_systems():
    """Every module-level *FIGURE_STEPS a surface module holds is the system's ladder (YouTube reads it; the Story
    reveal's multi_format.STORY_FIGURE_STEPS, once lane D lands, equals it), and each is declared in DEVIATIONS."""
    import type_sites
    found = {}
    for mod in type_sites.SURFACE_MODULES:
        m = importlib.import_module(f"src.{mod}")
        found |= {f"{mod}.{k}": v for k, v in vars(m).items() if k.endswith("FIGURE_STEPS") and k.isupper()}
    assert "youtube_thumbnail.FIGURE_STEPS" in found
    from src import youtube_thumbnail
    assert youtube_thumbnail.FIGURE_STEPS is brand_system.FIGURE_STEPS
    declared = {where.split(":")[0] for role, where, sizes, _ in brand_system.DEVIATIONS.values()
                if role == "figure" and tuple(sizes) == brand_system.FIGURE_STEPS}
    for name, steps in found.items():
        assert tuple(steps) == brand_system.FIGURE_STEPS, name
        assert name in declared, name


# ------------------------------------------------------------- the display ladder keeps its hierarchy on every tier
@pytest.mark.parametrize("canvas", sorted(brand_system.TYPE))
def test_the_display_ladder_is_ordered_on_every_tier(canvas):
    """SYS batch 1: on each canvas, in TYPE and in TYPE_FALLBACK, display_xl (cover, hook, Story title) > display_l
    (interior heading) > display_m: a role's fallback never drops under the role below's (9:16 had 120 < 124)."""
    for table in (brand_system.TYPE, brand_system.TYPE_FALLBACK):
        xl, l, m = (table[canvas][r] for r in ("display_xl", "display_l", "display_m"))
        assert xl > l > m, (canvas, xl, l, m)


def test_the_tall_display_steps_are_the_ones_the_story_and_the_scan_read():
    """The 9:16 fix reorders roles only: the step set the Story title and the size scan read is unchanged."""
    assert set(brand_system.display_steps("tall")) == {150, 138, 124, 120, 112, 96}
