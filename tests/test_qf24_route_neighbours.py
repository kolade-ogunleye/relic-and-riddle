"""QF-24 round 3: a route's neighbour name sits on its own country's ground, never on a neighbour's.

The route is drawn on the 1:50m layer. On the organic Reel box (x 113-848, foot 1090) the Morocco route's only room
for ALGERIA was the box's bottom-right corner, on Algeria's 1:50m fill but 7 km inside Morocco by the 1:10m border
(tests/geo_truth: "neighbour ALGERIA ... off its own ground, on ['Morocco']"). close_neighbours now also requires
the name's place on the country's 1:10m ground (detail_ground_mask) when the route passes its inverse projection.

Round 4 (review 3, ruling 2026-11-14: fail closed): where that ground cannot be read (the 1:10m layer not on disk or
not readable, or no such entity in it) the route does not name the country; round 3 kept the 1:50m place there, the
very fault above on an install without the layer. The synthetic tests below need no fetched map data.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from src import geo, graphics
from tests import geo_truth
from tests.test_r4_graphics_2026_11_02 import _render

needs_data = pytest.mark.skipif(not (geo.available() and geo.COUNTRIES_10M_FILE.is_file() and geo.MARINE_FILE.is_file()),
                                reason="Natural Earth data not fetched")
MOROCCO = {"marrakesh": (31.6295, -7.9811), "fes": (34.0331, -5.0003), "tangier": (35.7595, -5.8340)}


def _morocco_route(tmp_path, monkeypatch, width: int = 1080, height: int = 1920) -> dict:
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
    st = _render(monkeypatch, lambda: graphics.render_route(stops, out, width=width, height=height, narration=""),
                 fps=1)
    lay = json.loads((tmp_path / "r.mp4.layout.json").read_text(encoding="utf-8"))
    lay["omitted"] = st.get("omitted", [])         # freeze lane A cycle 3: the omission ledger's rows
    return lay


@needs_data
def test_the_morocco_route_names_algeria_on_algerian_ground(tmp_path, monkeypatch) -> None:
    lay = _morocco_route(tmp_path, monkeypatch)
    assert not geo_truth.faults(lay), geo_truth.faults(lay)
    nbs = [c for c in lay["name_checks"] if c["kind"] == "neighbour"]
    if "ALGERIA" not in {c["text"] for c in nbs}:
        # freeze lane A cycle 3 (lander ruling): where its own ground holds no place, its omission is recorded on the
        # ledger (refusal.omit), never only logged; it had failed here since before lane A (0fbe1449: no place on
        # Algeria's 1:10m ground inside the box)
        assert any("ALGERIA" in r["omitted"] for r in lay["omitted"]), lay["omitted"]
        return
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


# ---------------------------------------------------------------- round 4: fail closed without the 1:10m ground
@needs_data
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_the_morocco_route_names_no_neighbour_without_the_1_10m_layer(tmp_path, monkeypatch, size) -> None:
    """The reviewer's probe: the layer made unavailable for the render (the path, nothing under tools/geo touched),
    the layout then judged by tests/geo_truth on the real file. Round 3 set ALGERIA on Morocco at 9:16."""
    monkeypatch.setattr(geo, "COUNTRIES_10M_FILE", tmp_path / "absent" / geo.COUNTRIES_10M_FILE.name)
    lay = _morocco_route(tmp_path, monkeypatch, *size)
    assert not geo_truth.faults(lay), geo_truth.faults(lay)
    assert [c["text"] for c in lay["name_checks"] if c["kind"] == "neighbour"] == []     # none on unverified ground
    assert not [c for c in lay["name_checks"] if c["fault"]]


def _square(name: str, x0: float, y0: float, x1: float, y1: float, **more) -> dict:
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return {"display": name, "names": {name.lower()}, "label_rank": 2, "type": "Sovereign country",
            "polygons": [[ring]], "key": f"admin:{name.lower()}", **more}


def _to_frame(lon, lat):
    """The synthetic frame: 100 px a degree, (0, 0) at the frame's top left, latitude growing downwards."""
    return lon * 100, lat * 100


def _from_frame(x, y):
    return x / 100, y / 100


@pytest.fixture
def one_neighbour(tmp_path, monkeypatch):
    """A frame whose only country is a 500 x 300 px square (x 300-800, y 500-800) inside the text box of both
    formats; the 1:10m layer's path points at a file the test controls, and the once-only log is fresh."""
    coarse = _square("Borduria", 3, 5, 8, 8)
    monkeypatch.setattr(graphics, "_view_polygons", lambda *_a: [(coarse, coarse["polygons"][0])])
    monkeypatch.setattr(graphics, "_UNVERIFIED_GROUND_LOGGED", set(), raising=False)
    monkeypatch.setattr(geo, "available", lambda: True)
    layer = tmp_path / "geo" / "ne_10m_admin_0_map_subunits.geojson"
    monkeypatch.setattr(geo, "COUNTRIES_10M_FILE", layer)
    return coarse, layer


def _present(layer) -> None:
    layer.parent.mkdir(parents=True, exist_ok=True)
    layer.write_bytes(b"{}")


SIZES = [(1080, 1920), (1920, 1080)]


@pytest.mark.parametrize("size", SIZES)
def test_a_route_names_no_neighbour_when_the_1_10m_layer_is_absent(one_neighbour, monkeypatch, caplog, size) -> None:
    w, h = size
    monkeypatch.setattr(geo, "countries_detail", lambda: pytest.fail("an absent layer is not read"))
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        assert graphics.close_neighbours(w, h, None, _to_frame, [], set(), from_frame=_from_frame) == []
        assert graphics.close_neighbours(w, h, None, _to_frame, [], set(), from_frame=_from_frame) == []
    said = [r for r in caplog.records if "ne_10m_admin_0_map_subunits.geojson" in r.getMessage()]
    assert len(said) == 1 and said[0].levelno == logging.WARNING                    # said once, with the command
    assert geo.FETCH_COUNTRIES_10M_CMD in said[0].getMessage()
    # a caller that passes no inverse projection (the locator's close-up) places the name as before
    assert [n.name for n in graphics.close_neighbours(w, h, None, _to_frame, [], set())] == ["Borduria"]


@pytest.mark.parametrize("error", [OSError("cannot open"), json.JSONDecodeError("cut short", "{", 1),
                                   UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte"), KeyError("coordinates")])
def test_a_route_names_no_neighbour_when_the_1_10m_layer_cannot_be_read(one_neighbour, monkeypatch, caplog,
                                                                        error) -> None:
    _coarse, layer = one_neighbour
    _present(layer)

    def unreadable():
        raise error
    monkeypatch.setattr(geo, "countries_detail", unreadable)
    with caplog.at_level(logging.WARNING, logger=graphics.log.name):
        assert graphics.close_neighbours(1080, 1920, None, _to_frame, [], set(), from_frame=_from_frame) == []
    said = [r.getMessage() for r in caplog.records if "cannot be read" in r.getMessage()]
    assert len(said) == 1 and geo.FETCH_COUNTRIES_10M_CMD in said[0]


@pytest.mark.parametrize("size", SIZES)
def test_a_route_names_no_neighbour_the_1_10m_layer_has_no_entity_for(one_neighbour, monkeypatch, size) -> None:
    _coarse, layer = one_neighbour
    _present(layer)
    w, h = size
    for entities in ([], [_square("Syldavia", 3, 5, 8, 8)],
                     [{**_square("Borduria", 0, 0, 1, 1), "polygons": []}]):
        monkeypatch.setattr(geo, "countries_detail", lambda entities=entities: entities)
        assert graphics.close_neighbours(w, h, None, _to_frame, [], set(), from_frame=_from_frame) == []


@pytest.mark.parametrize("size", SIZES)
def test_a_route_names_a_neighbour_only_on_its_1_10m_ground(one_neighbour, monkeypatch, size) -> None:
    """The layer present: the round 3 rule, on synthetic ground. The 1:10m border gives the square's left 200 px to
    another country, so the name is set on the 300 px that are the neighbour's on both layers."""
    _coarse, layer = one_neighbour
    _present(layer)
    w, h = size
    monkeypatch.setattr(geo, "countries_detail", lambda: [_square("Borduria", 5, 5, 8, 8)])
    got = graphics.close_neighbours(w, h, None, _to_frame, [], set(), from_frame=_from_frame)
    assert [n.name for n in got] == ["Borduria"]
    x0, y0, x1, y1 = got[0].box
    assert 500 <= x0 < x1 <= 800 and 500 <= y0 < y1 <= 800, got[0].box
    # the 1:10m ground wholly off the 1:50m fill: no place carries the name on both, so it is not set
    monkeypatch.setattr(geo, "countries_detail", lambda: [_square("Borduria", 8.2, 5, 10, 8)])
    assert graphics.close_neighbours(w, h, None, _to_frame, [], set(), from_frame=_from_frame) == []


# ---------------------------------------------------------------- round 4b: a layer that cannot be read is missing
# Ruling B covers ground that "cannot be read": a 1:10m file on disk that is cut short or not JSON fails closed
# exactly as a missing one (the neighbour that needs the 1:10m ground is not named, one WARNING, the route renders).
# On the round-4 commit (fdbed65b) such a file raised JSONDecodeError from the route's water names (geo.on_detail_land).
CUT_SHORT = b'{"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"NAME": "Mor'
NOT_JSON = b"\xff\xfe<html>404 Not Found</html>"


@pytest.fixture
def fresh_10m(monkeypatch):
    """The 1:10m readers' caches emptied before and after (the layer's path is monkeypatched by the test), and the
    once-only logs fresh."""
    def clear() -> None:
        for f in (geo.countries_10m, geo.countries_detail, geo._detail_land_index):
            f.cache_clear()
    monkeypatch.setattr(graphics, "_UNVERIFIED_GROUND_LOGGED", set(), raising=False)
    clear()
    yield
    clear()


def _corrupt(tmp_path, monkeypatch, content: bytes):
    bad = tmp_path / "corrupt" / geo.COUNTRIES_10M_FILE.name
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(content)
    monkeypatch.setattr(geo, "COUNTRIES_10M_FILE", bad)
    return bad


def _said_about(caplog, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if name in r.getMessage()]


@needs_data
@pytest.mark.parametrize("content, size", [(CUT_SHORT, (1080, 1920)), (NOT_JSON, (1920, 1080))],
                         ids=["cut-short-9x16", "not-json-16x9"])
def test_a_route_renders_and_names_no_neighbour_of_its_own_when_the_1_10m_layer_is_corrupt(
        tmp_path, monkeypatch, caplog, fresh_10m, content, size) -> None:
    bad = _corrupt(tmp_path, monkeypatch, content)
    with caplog.at_level(logging.INFO):
        lay = _morocco_route(tmp_path, monkeypatch, *size)          # renders: never raises
    assert not geo_truth.faults(lay), geo_truth.faults(lay)
    # ALGERIA, which the route sets only on its 1:10m ground (test above), is not named
    assert [c["text"] for c in lay["name_checks"] if c["kind"] == "neighbour"] == []
    assert not [c for c in lay["name_checks"] if c["fault"]]
    said = _said_about(caplog, bad.name)
    assert len(said) == 1 and said[0].levelno == logging.WARNING, [r.getMessage() for r in said]
    assert "cannot be read" in said[0].getMessage() and geo.FETCH_COUNTRIES_10M_CMD in said[0].getMessage()


@pytest.mark.parametrize("content", [CUT_SHORT, NOT_JSON, b'{"features": [{"geometry": {"type": "Polygon"}}]}',
                                     b"[1, 2, 3]"], ids=["cut-short", "not-json", "no-coordinates", "a-list"])
@pytest.mark.parametrize("size", SIZES)
def test_a_corrupt_1_10m_layer_is_read_as_missing(one_neighbour, tmp_path, monkeypatch, caplog, fresh_10m, content,
                                                   size) -> None:
    """No fetched map data needed (runs on CI): the real geo readers on a corrupt file, close_neighbours on the
    synthetic frame. The layer is read as missing (no 1:10m entity), the neighbour is not named, and the file is
    named in one WARNING with its command; no per-country line follows."""
    w, h = size
    bad = _corrupt(tmp_path, monkeypatch, content)
    monkeypatch.setattr(geo, "countries_50m", list)   # the fixture's geo.available() has no 1:50m file behind it
    with caplog.at_level(logging.INFO):
        assert graphics.close_neighbours(w, h, None, _to_frame, [], set(), from_frame=_from_frame) == []
        assert graphics.close_neighbours(w, h, None, _to_frame, [], set(), from_frame=_from_frame) == []
        assert geo.countries_10m() == () and geo.countries_detail() == [] and not geo.on_detail_land(5.0, 6.0)
    assert geo.countries_10m_unreadable() in {"JSONDecodeError", "UnicodeDecodeError", "KeyError", "AttributeError"}
    said = _said_about(caplog, bad.name)
    assert len(said) == 1 and said[0].levelno == logging.WARNING, [r.getMessage() for r in said]
    assert "cannot be read" in said[0].getMessage() and geo.FETCH_COUNTRIES_10M_CMD in said[0].getMessage()
    assert not [r for r in caplog.records if "has no 1:10m ground" in r.getMessage()]
    # the locator's close-up (no inverse projection) places the name as before
    assert [n.name for n in graphics.close_neighbours(w, h, None, _to_frame, [], set())] == ["Borduria"]
