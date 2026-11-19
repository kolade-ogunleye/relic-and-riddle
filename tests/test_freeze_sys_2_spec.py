"""Design freeze, SYS batch 2 item 7: heading.cover_over_interior holds on the carousel ladder at both tiers (its
declared scope), and the two SPEC values the batch declares stay written (not runtime tokens)."""
from __future__ import annotations

from src import brand_system


def test_cover_over_interior_holds_on_the_carousel_ladder_at_both_tiers():
    ratio = brand_system.SPEC["heading.cover_over_interior"]
    t, f = brand_system.TYPE["carousel"], brand_system.TYPE_FALLBACK["carousel"]
    assert t["display_xl"] >= ratio * t["display_l"] - 1e-9          # the deck at its design size
    assert t["display_xl"] >= ratio * f["display_l"] - 1e-9          # the deck at its fallback tier (SYS-D-5)


def test_the_declared_values_stay_spec():
    for key in ("heading.cover_over_interior", "figure.dash_centre_em"):
        assert key in brand_system.SPEC and key not in brand_system.SYSTEM_TOKENS
