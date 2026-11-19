"""Design freeze, SYS batch 2 (panel 2 BC F3): a brand whose home country could read as water is refused when it is
configured. Invariants, synthetic brands only."""
from __future__ import annotations

import pytest

from src import brand


def test_the_default_map_fills_pass_and_a_water_home_is_refused():
    assert brand.map_fill_problems(brand.DEFAULT_BRAND) == []
    blue = brand.DEFAULT_BRAND.derive("blue home", palette={"HOME_TINT": (81, 137, 168), "HOME": (42, 74, 92)})
    probs = brand.map_fill_problems(blue)
    assert any("water hue" in p for p in probs)
    with pytest.raises(brand.BrandRefused):
        brand.configure(blue)
    assert brand.active() is brand.DEFAULT_BRAND
    # a home under the mark gate against the sea is refused too, whatever its hue
    dim = brand.DEFAULT_BRAND.derive("dim home", palette={"HOME_TINT": (30, 26, 20)})
    assert any("mark gate" in p for p in brand.map_fill_problems(dim))
