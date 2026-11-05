"""Round 12 (2026-11-04): the review of round 11. An island named by its own 1:10m subunit (Tristan da Cunha,
never SAINT HELENA), found by its sovereign's name too (a United Kingdom site), an atoll's islets drawn together
and an islet too small to read framed with its country's nearest ground (Bikini), Antarctica's ice shelves drawn
as its ground (no ROSS SEA on the Ross Ice Shelf), a small landlocked country framed to hold its name at both
sizes (Schengen), a route's names never stacked into one block (Peru), the 16:9 timeline's spans under the
spine and on as many rows as each other, and the 9:16 spans' separation kept before the years' size."""
import io
import json
import logging
import math
import zipfile

import numpy as np
import pytest

from src import geo, graphics
from tests.test_r4_graphics_2026_11_02 import _render
from tests.test_round11_review_2026_11_04 import _labels_at, _shp_zip

needs_geo = pytest.mark.skipif(not geo.available(), reason="country outlines not fetched")
needs_10m = pytest.mark.skipif(not geo.COUNTRIES_10M_FILE.is_file(), reason="1:10m map subunits not fetched")
needs_ice = pytest.mark.skipif(not geo.ICE_SHELVES_FILE.is_file(), reason="ice shelves not fetched")
needs_marine = pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")

TRISTAN = (-12.3105, -37.0675)
BIKINI = (165.3833, 11.5833)


# ---------------------------------------------------------------------------------------------- 1:10m islands
@needs_geo
@needs_10m
def test_tristan_is_found_under_its_territory_and_named_by_its_own_subunit() -> None:
    # round 15 (coordinator directive 1, deliberate): the entity whose 1:10m ground holds the site
    with geo.detail_view():
        got = geo.site_home(*TRISTAN, hint="Saint Helena")
    assert got.country is not None and got.country["display"] == "Tristan da Cunha" and got.km < 5


@needs_geo
@needs_10m
def test_a_united_kingdom_site_on_tristan_finds_its_island_by_the_sovereign() -> None:
    with geo.detail_view():
        got = geo.site_home(*TRISTAN, hint="United Kingdom")
    assert got.country is not None and got.country["display"] == "Tristan da Cunha"


@needs_geo
@needs_10m
def test_an_atolls_islets_near_the_site_come_together() -> None:
    # round 15 (deliberate): the entity's own 1:10m polygons hold all its islets; those near the site are drawn
    with geo.detail_view():
        home = geo.site_home(*BIKINI).country
    assert home is not None and home["display"] == "Marshall Islands"
    near = [p for p in home["polygons"] if geo.ring_km(p[0], *BIKINI) <= geo.ISLAND_GROUP_KM]
    assert len(near) >= 2                                      # Enyu, 18 km off, as well as Bikini's own islet


@needs_geo
@needs_10m
@pytest.mark.parametrize("country", ["Saint Helena", "United Kingdom"])
def test_tristan_is_drawn_and_named_tristan_da_cunha_never_saint_helena(country, tmp_path, monkeypatch,
                                                                       caplog) -> None:
    place = {"lat": TRISTAN[1], "lon": TRISTAN[0], "country": country, "label": "Edinburgh of the Seven Seas",
             "approximate": False}
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4"), fps=1)
    lay = json.loads((tmp_path / "m.mp4.layout.json").read_text(encoding="utf-8"))
    assert lay["country"] is not None and lay["country"]["text"] == "TRISTAN DA CUNHA"
    assert lay["span_close"] < 1.0                                           # framed on the island itself
    assert lay["globe"] is not None                                          # the globe as its locator


@needs_geo
@needs_10m
def test_bikini_is_framed_with_its_countrys_nearest_ground(tmp_path, monkeypatch, caplog) -> None:
    place = {"lat": BIKINI[1], "lon": BIKINI[0], "country": "Marshall Islands", "label": "Bikini Atoll",
             "approximate": False}
    for w, h in ((1080, 1920), (1920, 1080)):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=graphics.log.name):
            _render(monkeypatch, lambda w=w, h=h: graphics.render_map(place, tmp_path / "m.mp4", width=w, height=h),
                    fps=1)
        lay = json.loads((tmp_path / "m.mp4.layout.json").read_text(encoding="utf-8"))
        assert "too small to read; framed with its country's nearest ground" in caplog.text
        assert 2.0 < lay["span_close"] < 12.0                                  # not one islet, not the ocean
        assert lay["country"] is not None                                       # MARSHALL IS. named
        assert "found no place in any form" not in caplog.text


def test_the_pin_over_an_atolls_lagoon_covers_none_of_its_ground() -> None:
    # a ring of islets 0.1 degrees round the site, the pin in the lagoon
    islets = [[(math.cos(a) * 0.1 + dx, math.sin(a) * 0.1 + dy) for dx, dy in
               ((0.0, 0.0), (0.004, 0.0), (0.004, 0.004), (0.0, 0.004))] for a in np.linspace(0, 2 * math.pi, 8)]
    assert graphics.pin_island_cover(islets, 0.0, 0.0, 0.5, 1080, 1920) == 0.0
    strip = [[(-0.002, -0.2), (0.002, -0.2), (0.002, 0.2), (-0.002, 0.2)]]   # a thin strip under the pin
    assert graphics.pin_island_cover(strip, 0.0, 0.0, 0.5, 1080, 1920) > 0.0


# ---------------------------------------------------------------------------------------------- ice shelves
@needs_geo
@needs_ice
def test_the_ross_ice_shelf_is_antarcticas_ground() -> None:
    ant = geo.find_country("Antarctica")
    assert geo.in_country(-175.0, -81.0, ant, tolerance_deg=0.0)           # mid-shelf, south of Ross Island
    assert geo.in_country(-162.0, -80.0, ant, tolerance_deg=0.0)           # Roosevelt Island, an ice rise in it
    assert not geo.in_country(-175.0, -75.0, ant, tolerance_deg=0.0)       # the open Ross Sea


def test_without_the_ice_shelves_file_the_shelves_stay_sea(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(geo, "ICE_SHELVES_FILE", tmp_path / "missing.geojson")
    monkeypatch.setattr(geo, "_ICE_MISSING_LOGGED", [])
    assert geo.ice_shelf_polygons() == []


@needs_geo
@needs_ice
@needs_marine
def test_mcmurdo_names_no_water_on_the_ice_shelf() -> None:
    w, h = 1080, 1920
    pv = graphics.PolarProjection(166.6682, -77.8463, w / 2, h / 2, w / 12.0)
    land, _home = graphics.view_masks(w, h, 0.0, 0.0, 1.0, geo.find_country("Antarctica"), polar=pv)
    got = graphics.water_labels(w, h, pv.inverse, land, [], to_frame=pv)
    ant = geo.find_country("Antarctica")
    for wl in got:
        b = wl.box
        for x, y in ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), (b[0], b[1]), (b[2], b[3]):
            assert not geo.in_country(*pv.inverse(x, y), ant, tolerance_deg=0.0), wl.name


# ---------------------------------------------------------------------------------------------- water ranks
@needs_marine
def test_the_smaller_seas_are_loaded_and_ranked_minor() -> None:
    by = {m["name"]: m for m in geo.marine_polygons()}
    assert by["Sea of Marmara"]["scalerank"] >= geo.MARINE_MINOR_RANK
    assert by["Alboran Sea"]["scalerank"] >= geo.MARINE_MINOR_RANK


@needs_marine
@needs_geo
def test_the_water_at_the_site_is_named_first() -> None:
    got, _ff = _labels_at(28.9802, 41.0086, 1080 / 4.0)                    # Istanbul, close
    assert "Sea of Marmara" in got


# ---------------------------------------------------------------------------------------------- shapefiles
def test_an_unknown_cpg_encoding_is_a_value_error() -> None:
    data = _shp_zip("t", [[(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]], ["a"])
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as z:
        for n in src.namelist():
            z.writestr(n, b"no-such-codec" if n.endswith(".cpg") else src.read(n))
    with pytest.raises(ValueError):
        geo.read_shapefile_zip(buf.getvalue(), "t")


# ---------------------------------------------------------------------------------------------- small country
@needs_geo
def test_schengen_16x9_frames_luxembourg_to_hold_its_name(tmp_path, monkeypatch, caplog) -> None:
    place = {"lat": 49.4697, "lon": 6.3622, "country": "Luxembourg", "label": "Schengen", "approximate": False}
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=1920, height=1080), fps=1)
    lay = json.loads((tmp_path / "m.mp4.layout.json").read_text(encoding="utf-8"))
    assert lay["country"] is not None and lay["country"]["inside"]
    assert "framed closer" in caplog.text and "to hold its name in its fill" in caplog.text


# ---------------------------------------------------------------------------------------------- route names
def test_stacks_clear_flags_a_name_set_over_or_under_another() -> None:
    named = [(100.0, 100.0, 300.0, 130.0)]
    cands = np.array([[150.0, 150.0, 350.0, 180.0],          # 20 px under it, sharing most of its width
                      [150.0, 220.0, 350.0, 250.0],          # 90 px under it
                      [320.0, 150.0, 500.0, 180.0]])         # beside it, sharing none of its width
    assert graphics.stacks_clear(cands, named).tolist() == [False, True, True]
    assert graphics.stacks_clear(cands, []).all()


ROUTE_COORDS = {"lima": (-12.0464, -77.0428), "nazca": (-14.7390, -75.1300), "arequipa": (-16.3989, -71.5370),
                "cusco": (-13.5163, -71.9785), "machu picchu": (-13.1631, -72.5450)}


@needs_geo
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_the_peru_route_stacks_no_name_on_another(size, tmp_path, monkeypatch, caplog) -> None:
    def fake_locate(topic):
        n = topic["location"]["name"].lower()
        la, lo = ROUTE_COORDS[n]
        return {"lat": la, "lon": lo, "country": "Peru", "label": topic["location"]["name"], "approximate": False,
                "qid": "x", "matched": "name"}
    monkeypatch.setattr(geo, "locate", fake_locate)
    if hasattr(graphics, "locate"):
        monkeypatch.setattr(graphics, "locate", fake_locate)
    stops = [{"place": n, "country": "Peru", "label": n.title()} for n in ROUTE_COORDS]
    w, h = size
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        _render(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=w, height=h), fps=1)
    assert "is stacked on another stop's name" not in caplog.text
    assert "drawn unlabelled" not in caplog.text


# ---------------------------------------------------------------------------------------------- timelines
def test_the_two_row_count_break_keeps_years_later_together() -> None:
    assert graphics.wide_span_lines("ABOUT 4,400 YEARS LATER", 2) == ["ABOUT 4,400", "YEARS LATER"]
    # round 15 (ruling R11, deliberate): an exact count breaks before "YEARS", never before "LATER"
    assert graphics.wide_span_lines("40 YEARS LATER", 2) == ["40", "YEARS LATER"]


E = lambda y, lab, a=False: {"year": y, "label": lab, "approximate": a}
STONEHENGE = [E(-3000, "A circular ditch and bank are dug", True), E(-2500, "The sarsen stones are raised", True),
              E(1918, "Cecil Chubb gives it to the nation"), E(1986, "Named a World Heritage Site")]
MACHU = [E(1450, "Pachacuti builds the estate", True), E(1572, "The last Inca stronghold falls"),
         E(1911, "Hiram Bingham reaches the ruins"), E(1983, "Named a World Heritage Site")]
COLOSSEUM = [E(72, "Vespasian begins the amphitheatre"), E(80, "Titus opens it with 100 days of games"),
             E(523, "The last recorded animal hunt"), E(1349, "An earthquake brings down the south side"),
             E(1980, "Named a World Heritage Site")]


def test_stonehenge_16x9_keeps_every_span_under_the_spine(tmp_path, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        _render(monkeypatch, lambda: graphics.render_timeline(STONEHENGE, tmp_path / "t.mp4", width=1920,
                                                              height=1080), fps=1)
    # round 13 (coordinator directive 4): a label never splits a name or keeps a one-word middle row; the only
    # clean break of NAMED A WORLD HERITAGE SITE is wider, so a span may take spec G's logged last resort over the
    # spine (it was "NAMED A / WORLD / HERITAGE SITE" with every span under it)
    assert "hard failure" not in caplog.text
    assert "set over the spine" not in caplog.text or "spec G's last resort" in caplog.text


@pytest.mark.parametrize("events", [MACHU, COLOSSEUM], ids=["machu", "colosseum"])
def test_16x9_spans_share_their_rows_and_the_heritage_label_breaks_whole(events, tmp_path, monkeypatch,
                                                                       caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", width=1920, height=1080),
                fps=1)
    assert "keep a weak middle row" not in caplog.text
    # round 15 (ruling R11, deliberate): with "LATER" never alone on a row, a span that no spacing holds under the
    # spine on one row, or on two as the count over its unit, takes spec G's logged last resort over it
    assert "set over the spine" not in caplog.text or "spec G's last resort" in caplog.text
    assert "unclean layout" not in caplog.text and "hard failure" not in caplog.text


def test_colosseum_9x16_keeps_the_spans_separation(tmp_path, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        _render(monkeypatch, lambda: graphics.render_timeline(COLOSSEUM, tmp_path / "t.mp4"), fps=1)
    assert "without their full separation" not in caplog.text
