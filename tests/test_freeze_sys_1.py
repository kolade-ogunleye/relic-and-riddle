"""Design freeze, SYS batch 1 (the lander's approved system requests, 2026-11-19): invariants, never pinned
measurements.

- SYS-D-1: one figure-step ladder in the system (brand_system.FIGURE_STEPS), read by every surface's figure ladder.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from src import brand_system  # noqa: E402


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
