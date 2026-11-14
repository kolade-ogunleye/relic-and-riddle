"""QF-24 round 3: a route's neighbour name sits on its own country's ground, never on a neighbour's.

The route is drawn on the 1:50m layer. On the organic Reel box (x 113-848, foot 1090) the Morocco route's only room
for ALGERIA was the box's bottom-right corner, on Algeria's 1:50m fill but 7 km inside Morocco by the 1:10m border
(tests/geo_truth: "neighbour ALGERIA ... off its own ground, on ['Morocco']"). close_neighbours now also requires
the name's place on the country's 1:10m ground (detail_ground_mask) when the route passes its inverse projection.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from src import geo, graphics
from tests import geo_truth
from tests.test_r4_graphics_2026_11_02 import _render

needs_data = pytest.mark.skipif(not (geo.available() and geo.COUNTRIES_10M_FILE.is_file() and geo.MARINE_FILE.is_file()),
                                reason="Natural Earth data not fetched")
MOROCCO = {"marrakesh": (31.6295, -7.9811), "fes": (34.0331, -5.0003), "tangier": (35.7595, -5.8340)}


def _morocco_route(tmp_path, monkeypatch) -> dict:
    real = geo.locate

    def fake(topic):
        n = topic["location"]["name"].lower()
        if n in MOROCCO:
            la, lo = MOROCCO[n]
            return {"lat": la, "lon": lo, "country": "Morocco", "label": topic["location"]["name"],
                    "approximate": False, "qid": "x", "matched": "name"}
        return real(topic)
    monkeypatch.setattr(geo, "locate", fake)
    stops = [{"place": n, "country": "Morocco", "label": n} for n in ("Marrakesh", "Fes", "Tangier")]
    out = tmp_path / "r.mp4"
    _render(monkeypatch, lambda: graphics.render_route(stops, out, width=1080, height=1920, narration=""), fps=1)
    return json.loads((tmp_path / "r.mp4.layout.json").read_text(encoding="utf-8"))


@needs_data
def test_the_morocco_route_names_algeria_on_algerian_ground(tmp_path, monkeypatch) -> None:
    lay = _morocco_route(tmp_path, monkeypatch)
    assert not geo_truth.faults(lay), geo_truth.faults(lay)
    nbs = [c for c in lay["name_checks"] if c["kind"] == "neighbour"]
    assert "ALGERIA" in {c["text"] for c in nbs}          # named, not dropped
    algeria = next(c for c in nbs if c["text"] == "ALGERIA")
    for lo, la in algeria["lonlat"]:                        # every sampled point of its box on its own 1:10m ground
        assert geo_truth._near(geo_truth.features_named("ALGERIA"), lo, la, geo_truth.TOL_KM), (lo, la)
        assert not any(f.props.get("NAME") == "Morocco" for f in geo_truth.land_at(lo, la)), (lo, la)


@needs_data
def test_the_detail_ground_mask_follows_the_1_10m_border() -> None:
    algeria = geo.find_country("Algeria")
    # a plain equirectangular frame over the Morocco-Algeria border (1 px = 0.01 degree)
    lon0, lat0, w, h = -6.0, 31.5, 400, 300

    def from_frame(x, y):
        return lon0 + np.asarray(x) * 0.01, lat0 - np.asarray(y) * 0.01
    mask = graphics.detail_ground_mask(algeria, from_frame, w, h)
    assert mask is not None and mask.shape == (h, w) and mask.any() and not mask.all()
    # (-4.446, 30.532): on Algeria's 1:50m fill, 7 km inside Morocco by the 1:10m border; (-3.0, 30.5) is Algeria
    assert not mask[round((lat0 - 30.532) / 0.01), round((-4.446 - lon0) / 0.01)]
    assert mask[round((lat0 - 30.5) / 0.01), round((-3.0 - lon0) / 0.01)]
