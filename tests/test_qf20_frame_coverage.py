"""Coarse viewport sampling must not override native-resolution geography."""

from types import SimpleNamespace

import numpy as np
import pytest

from src import graphics


@pytest.mark.parametrize("home_fraction,context_fraction", [(0.19, 0.20), (0.61, 0.20), (0.40, 0.09)])
def test_coarse_coverage_cannot_accept_invalid_full_resolution(monkeypatch, home_fraction, context_fraction):
    """A passing coarse raster can hide a failing boundary in the final raster."""
    anchor = (40.0, 40.0)
    monkeypatch.setattr(graphics, "get_text_box", lambda *args: (0, 0, 80, 80))
    monkeypatch.setattr(graphics, "PolarProjection", lambda *args: lambda *point: (60.0, 60.0))
    monkeypatch.setattr(graphics, "neighbours_in_view", lambda *args: [("NEIGHBOUR", True)])
    monkeypatch.setattr(graphics.geo, "countries", lambda: [{"display": "Neighbour", "label_at": (1, 1)}])
    monkeypatch.setattr(graphics, "site_part", lambda *args: None)
    marker = SimpleNamespace(label_box=(20, 20, 25, 25), marker_box=(30, 30, 31, 31))
    monkeypatch.setattr(graphics, "leader_marker", lambda *args: marker)
    calls = []

    def names(*args):
        # The original frame cannot label the neighbour, but provisional frames can.
        calls.append(args)
        return [] if len(calls) == 1 else [SimpleNamespace(name="Neighbour")]

    def masks(*args, down=1, **kwargs):
        side = 80 // down
        ground = np.zeros((side, side), dtype=bool)
        land = np.zeros_like(ground)
        home, context = (0.40, 0.20) if down > 1 else (home_fraction, context_fraction)
        ground.flat[:round(ground.size * home)] = True
        land.flat[:round(land.size * (home + context))] = True
        return land, ground

    monkeypatch.setattr(graphics, "close_neighbours", names)
    monkeypatch.setattr(graphics, "view_masks", masks)
    result = graphics.locator_name_frame(0, 0, {"display": "Home"}, 80, 80, 10.0, anchor, "Site", "leader")
    assert result[:2] == (10.0, anchor), "Native-resolution coverage must reject every proposed pan/zoom"
