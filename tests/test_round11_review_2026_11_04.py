"""Round 11 (2026-11-04): the coordinator's structural directives on the locator map. Water named from Natural
Earth's marine polygons (each body only inside its own), polar views on an azimuthal equidistant projection, a
site's island the 1:50m data leaves out (Pitcairn) taken from the 1:10m data or marked on the open sea, the pin's
cover measured on the island, the country's name kept in a fill that holds it beside the site's name (Bratislava),
the neighbours at a three-country border by the pin, and the scale bar's R10 range."""
import io
import json
import logging
import math
import struct
import zipfile

import numpy as np
import pytest

from src import geo, graphics
from tests.test_r4_graphics_2026_11_02 import _render

needs_marine = pytest.mark.skipif(not geo.MARINE_FILE.is_file(), reason="marine polygons not fetched")
needs_geo = pytest.mark.skipif(not geo.available(), reason="country outlines not fetched")


# ---------------------------------------------------------------------------------------------- shapefiles
def _shp_zip(stem: str, rings: list[list[tuple[float, float]]], names: list[str]) -> bytes:
    """A minimal polygon shapefile (one record per ring list entry) with a one-field .dbf, zipped."""
    recs = b""
    for i, ring in enumerate(rings, start=1):
        pts = b"".join(struct.pack("<2d", x, y) for x, y in ring)
        content = struct.pack("<i4dii", 5, 0, 0, 0, 0, 1, len(ring)) + struct.pack("<i", 0) + pts
        recs += struct.pack(">ii", i, len(content) // 2) + content
    shp = struct.pack(">i", 9994) + b"\0" * 20 + struct.pack(">i", (100 + len(recs)) // 2) \
        + struct.pack("<ii", 1000, 5) + b"\0" * 64 + recs
    field = b"name".ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([20, 0]) + b"\0" * 14
    head_len = 32 + 32 + 1
    dbf = bytes([3, 126, 11, 4]) + struct.pack("<IHH", len(names), head_len, 21) + b"\0" * 20 + field + b"\r"
    dbf += b"".join(b" " + n.encode().ljust(20, b"\0") for n in names) + b"\x1a"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{stem}.shp", shp)
        z.writestr(f"{stem}.dbf", dbf)
        z.writestr(f"{stem}.cpg", "UTF-8")
    return buf.getvalue()


def test_read_shapefile_zip_gives_multipolygons_and_strips_padding() -> None:
    cw = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]          # clockwise: an outer ring
    fc = geo.read_shapefile_zip(_shp_zip("t", [cw], ["Red Sea"]), "t")
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 1
    f = fc["features"][0]
    assert f["properties"]["name"] == "Red Sea"                               # no NUL padding left
    assert f["geometry"]["type"] == "MultiPolygon" and len(f["geometry"]["coordinates"]) == 1


def test_read_shapefile_zip_refuses_what_is_not_a_polygon_shapefile() -> None:
    with pytest.raises(ValueError):
        geo.read_shapefile_zip(_shp_zip("t", [[(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (0.0, 0.0)]], ["a", "b"]), "t")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("t.shp", b"\0" * 120)
        z.writestr("t.dbf", b"\0" * 40)
    with pytest.raises(ValueError):
        geo.read_shapefile_zip(buf.getvalue(), "t")


def test_shp_polygons_put_each_hole_in_its_outer_ring() -> None:
    outer = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)]   # clockwise
    hole = [(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0), (2.0, 2.0)]        # anticlockwise
    far = [(20.0, 0.0), (20.0, 1.0), (21.0, 1.0), (21.0, 0.0), (20.0, 0.0)]
    polys = geo.shp_polygons([outer, hole, far])
    assert len(polys) == 2 and polys[0] == [outer, hole] and polys[1] == [far]


# ---------------------------------------------------------------------------------------------- water names
def _frame(lon0: float, lat0: float, px_per_deg: float, w: int = 1080, h: int = 1920):
    coslat = math.cos(math.radians(lat0))

    def from_frame(x: float, y: float) -> tuple[float, float]:
        return lon0 + (x - w / 2) / (px_per_deg * coslat), lat0 - (y - h / 2) / px_per_deg

    def to_frame(lon: float, lat: float) -> tuple[float, float]:
        return w / 2 + (lon - lon0) * px_per_deg * coslat, h / 2 - (lat - lat0) * px_per_deg
    return from_frame, to_frame


def _labels_at(lon0: float, lat0: float, px_per_deg: float, w: int = 1080, h: int = 1920):
    from_frame, to_frame = _frame(lon0, lat0, px_per_deg, w, h)
    land, _home = graphics.view_masks(w, h, lon0, lat0, w / px_per_deg, None)
    got = graphics.water_labels(w, h, from_frame, land, [], to_frame=to_frame)
    for wl in got:                    # every name wholly on its own body's water: the centre and the corners
        b = wl.box
        for x, y in ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), (b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3]):
            m = geo.marine_at(*from_frame(x, y))
            assert m is not None and m["name"] == wl.name, (wl.name, m and m["name"])
        x0, y0, x1, y1 = (int(v) for v in b)
        assert not land[y0:y1, x0:x1].any()
    return {wl.name: wl for wl in got}, from_frame


@needs_marine
@needs_geo
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_portobelo_names_the_caribbean_north_and_the_gulf_of_panama_south(size) -> None:
    w, h = size
    got, _ff = _labels_at(-79.6553, 9.5547, w / 8.0, w, h)
    assert "Caribbean Sea" in got
    assert got["Caribbean Sea"].box[3] < h / 2                                 # north of the isthmus
    assert not any("Pacific" in n for n in got if got[n].box[1] < h / 2 - 100)  # the Pacific never on the north
    if "Gulf of Panama" in got:
        assert got["Gulf of Panama"].box[1] > h / 2                            # south


@needs_marine
@needs_geo
def test_lalibela_names_the_red_sea_and_the_gulf_of_aden_each_on_its_side() -> None:
    got, ff = _labels_at(43.0, 13.5, 90.0)
    assert got, "no water named"
    for name, wl in got.items():
        lon, lat = ff((wl.box[0] + wl.box[2]) / 2, (wl.box[1] + wl.box[3]) / 2)
        if name == "Red Sea":
            assert lat > 12.6 and lon < 43.5
        if name == "Gulf of Aden":
            assert lon > 43.3 and lat < 13.0


@needs_marine
@needs_geo
def test_narva_names_the_gulf_of_finland() -> None:
    got, _ff = _labels_at(28.2018, 59.3772, 1080 / 6.0)
    assert "Gulf of Finland" in got


@needs_marine
@needs_geo
def test_ushuaia_water_is_named_only_inside_its_own_polygons() -> None:
    got, _ff = _labels_at(-68.303, -54.8019, 1080 / 10.0)       # every name checked on its own polygon (_labels_at)
    assert all(n in {m["name"] for m in geo.marine_polygons()} for n in got)


@needs_marine
@needs_geo
def test_a_mid_ocean_site_names_its_ocean() -> None:
    got, _ff = _labels_at(-35.0, 30.0, 1080 / 18.0)
    assert list(got) == ["North Atlantic Ocean"]


def test_without_the_marine_file_no_water_is_named(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setattr(geo, "MARINE_FILE", tmp_path / "missing.geojson")
    monkeypatch.setattr(geo, "_MARINE_MISSING_LOGGED", [])
    geo.marine_polygons.cache_clear()
    try:
        w, h = 1080, 1920
        ff, tf = _frame(18.0, 34.0, 100.0)
        with caplog.at_level(logging.WARNING, logger=geo.log.name):
            assert graphics.water_labels(w, h, ff, np.zeros((h, w), bool), [], to_frame=tf) == []
        assert "no water is named" in caplog.text
    finally:
        geo.marine_polygons.cache_clear()


def test_a_body_is_named_only_where_its_name_fits_its_own_water(monkeypatch, tmp_path) -> None:
    sea = [(10.0, 30.0), (10.0, 40.0), (20.0, 40.0), (20.0, 30.0), (10.0, 30.0)]
    gulf = [(20.0, 30.0), (20.0, 40.0), (20.2, 40.0), (20.2, 30.0), (20.0, 30.0)]   # a sliver: no room
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": n, "name_en": n, "featurecla": "sea", "scalerank": "1"},
         "geometry": {"type": "Polygon", "coordinates": [r]}} for n, r in (("Test Sea", sea), ("Thin Gulf", gulf))]}
    path = tmp_path / "m.geojson"
    path.write_text(json.dumps(fc), encoding="utf-8")
    monkeypatch.setattr(geo, "MARINE_FILE", path)
    geo.marine_polygons.cache_clear()
    try:
        w, h = 1080, 1920
        ff, tf = _frame(17.0, 35.0, 100.0)
        got = graphics.water_labels(w, h, ff, np.zeros((h, w), bool), [], to_frame=tf)
        assert [g.name for g in got] == ["Test Sea"]
        b = got[0].box
        assert ff(b[2], b[1])[0] <= 20.0                                   # wholly west of the sea's edge
        # every place taken: none named
        assert graphics.water_labels(w, h, ff, np.zeros((h, w), bool), [(0, 0, w, h)], to_frame=tf) == []
    finally:
        geo.marine_polygons.cache_clear()


# ---------------------------------------------------------------------------------------------- polar
def test_azimuthal_round_trips_and_keeps_true_distance() -> None:
    for lon0, lat0 in ((106.8, -78.5), (0.0, -89.99), (-62.3, 82.5), (15.5, 78.2)):
        for lon, lat in ((lon0 + 30, lat0 + 5), (lon0 - 170, max(-89.0, min(89.0, lat0))), (lon0, lat0)):
            u, v = geo.azimuthal(lon, lat, lon0, lat0)
            back = geo.azimuthal_inverse(u, v, lon0, lat0)
            assert math.isclose((back[0] - lon + 540) % 360 - 180, 0, abs_tol=1e-6) or abs(abs(lat) - 90) < 1e-6
            assert math.isclose(back[1], lat, abs_tol=1e-6)
    u, v = geo.azimuthal(0.0, -80.0, 0.0, -90.0)
    # QF-20: PROJ's azimuthal equidistant view on the WGS84 ellipsoid keeps the true geodesic distance from the
    # centre (in arc degrees of the mean sphere): 10 degrees of latitude from the pole are 10.044 such degrees
    from src import map_geometry
    true_deg = map_geometry.GEODESIC.inv(0.0, -90.0, 0.0, -80.0)[2] / map_geometry.METRES_PER_ARC_DEGREE
    assert math.isclose(math.hypot(u, v), true_deg, abs_tol=1e-9)


def test_inverse_grid_matches_the_scalar_inverse() -> None:
    pv = graphics.PolarProjection(106.8, -78.5, 540.0, 700.0, 30.0)
    xs, ys = np.array([0.0, 540.0, 1079.0]), np.array([0.0, 700.0, 1919.0])
    lon, lat = pv.inverse_grid(xs, ys)
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            lo, la = pv.inverse(float(x), float(y))
            assert math.isclose(lat[j, i], la, abs_tol=1e-6)
            assert math.isclose((lon[j, i] - lo + 540) % 360 - 180, 0, abs_tol=1e-6)


POLAR_SITES = {"vostok": (-78.4645, 106.8373, "Antarctica"), "southpole": (-89.99, 0.0, "Antarctica"),
               "alert": (82.5018, -62.3481, "Canada"), "svalbard": (78.2357, 15.4913, "Norway")}


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
@pytest.mark.parametrize("site", sorted(POLAR_SITES))
def test_polar_sites_take_the_polar_view(site, size) -> None:
    lat, _lon, _c = POLAR_SITES[site]
    assert graphics.is_polar_view(lat, *size, 8.0, 40.0)
    assert not graphics.is_polar_view(41.9, *size, 8.0, 40.0)                 # Rome stays plate carree
    assert graphics.is_polar_view(64.1, 1080, 1920, 8.0, 40.0)                # Reykjavik's wide view passes the pole


@needs_geo
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
@pytest.mark.parametrize("site", sorted(POLAR_SITES))
def test_the_polar_view_has_no_void_no_mirror_and_no_seam(site, size) -> None:
    w, h = size
    lat, lon, country = POLAR_SITES[site]
    home = geo.find_country(country)
    pv = graphics.PolarProjection(lon, lat, w / 2, h / 2 - graphics.polar_lift(w, h), w / 20.0)
    land, home_m = graphics.view_masks(w, h, 0.0, 0.0, 1.0, home, polar=pv)
    # no mirror: every land pixel sampled is on a country's ground at its own (lon, lat) (a 1 px coast tolerance)
    ys, xs = np.nonzero(land[::23, ::23])
    for y, x in list(zip(ys * 23, xs * 23))[::7]:
        lo, la = pv.inverse(float(x), float(y))
        assert any(geo.in_country(lo, la, c, tolerance_deg=0.3) for c in geo.countries()
                   if c["extent"][1] - 1 <= la <= c["extent"][3] + 1), (x, y, lo, la)
    if country == "Antarctica":
        # no void past the pole, no slit along the antimeridian cut: the ground round the pole is solid
        px, py = pv(0.0, -90.0)
        if 0 <= px < w and 0 <= py < h:
            r = 40
            patch = home_m[max(0, int(py) - r):int(py) + r, max(0, int(px) - r):int(px) + r]
            assert patch.all()
        assert home_m[int(h / 2 - graphics.polar_lift(w, h)), int(w / 2)]           # the site on its ground


@needs_geo
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
@pytest.mark.parametrize("site", sorted(POLAR_SITES))
def test_a_polar_render_is_drawn_polar_and_never_fades_past_a_pole(site, size, tmp_path, monkeypatch,
                                                                   caplog) -> None:
    lat, lon, country = POLAR_SITES[site]
    w, h = size
    place = {"lat": lat, "lon": lon, "country": country, "label": site.title(), "approximate": False}
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=w, height=h), fps=1)
    assert "polar (azimuthal equidistant)" in caplog.text
    assert "runs past the" not in caplog.text                                  # pole_fade never runs
    last = np.asarray(st["frames"][-1]).astype(int)
    # no void: no band of rows all sea-dark across the middle of the frame where ground should be
    assert (np.abs(np.diff(last[h // 4: 3 * h // 4].mean(axis=(1, 2)))) < 40).all()


def test_polar_polygons_hold_antarctica_whole_at_the_pole() -> None:
    if not geo.available():
        pytest.skip("country outlines not fetched")
    got = geo.polar_polygons(0.0, -89.99, 10.0)
    assert any(c["display"] == "Antarctica" for c, _p in got)
    assert all(c["display"] != "Canada" for c, _p in got)


# ---------------------------------------------------------------------------------------------- Pitcairn
@needs_geo
def test_pitcairn_takes_its_island_from_the_10m_data_for_that_view_only(monkeypatch, tmp_path, caplog) -> None:
    if not geo.COUNTRIES_10M_FILE.is_file():
        pytest.skip("1:10m countries not fetched")
    # round 15 (coordinator directive 1, deliberate): the close-up draws the 1:10m entities throughout
    # (geo.detail_view), so Pitcairn's own island is the ground that holds the site, not an outline added for one view
    home = geo.find_country("Pitcairn Islands")
    n0 = len(home["polygons"])
    with geo.detail_view():
        got = geo.site_home(-130.1015, -25.066)
    assert got.country is not None and got.country["display"] == "Pitcairn Islands" and got.km == 0.0
    place = {"lat": -25.066, "lon": -130.1015, "country": "Pitcairn Islands", "label": "Adamstown",
             "approximate": False}
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4"), fps=1)
    assert len(home["polygons"]) == n0                                        # the 1:50m data untouched
    lay = json.loads((tmp_path / "m.mp4.layout.json").read_text(encoding="utf-8"))
    assert lay["span_close"] < 1.0                                            # framed on Pitcairn itself
    assert st["frames"]


@needs_geo
def test_without_the_10m_data_pitcairn_is_marked_on_the_open_sea(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setattr(geo, "COUNTRIES_10M_FILE", tmp_path / "missing.geojson")
    geo.countries_10m.cache_clear()
    try:
        place = {"lat": -25.066, "lon": -130.1015, "country": "Pitcairn Islands", "label": "Adamstown",
                 "approximate": False}
        with caplog.at_level(logging.INFO, logger=graphics.log.name):
            _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=1920, height=1080),
                    fps=1)
        assert "marked on the open sea" in caplog.text
        lay = json.loads((tmp_path / "m.mp4.layout.json").read_text(encoding="utf-8"))
        # round 15 (coordinator directive 1, deliberate): with no ground within 50 km in the data the map names no
        # home (the topic's country is never a name looked up), and says why
        assert lay["country"] is None and lay["home_drop"].startswith("the open sea")
    finally:
        geo.countries_10m.cache_clear()


# ---------------------------------------------------------------------------------------------- thin atolls
def test_the_pin_and_its_name_cover_little_of_a_thin_island(caplog) -> None:
    w, h = 1080, 1920
    anchor = (540.0, 900.0)
    home = np.zeros((h, w), bool)
    home[400:1400, 515:575] = True                       # a thin north-south atoll, 60 px wide, under the pin
    plain = graphics.pin_layout(anchor, "Funafuti Atoll", w, h)
    before = graphics.island_cover([plain.label_box, plain.marker_box], home)
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        lay = graphics.pin_layout(anchor, "Funafuti Atoll", w, h, home=home)
    after = graphics.island_cover([lay.label_box, lay.marker_box], home)
    assert after < before and after <= graphics.PIN_ISLAND_COVER_MAX + 0.05
    assert "of the island" in caplog.text
    # a large island the label barely touches is left as it is
    big = np.zeros((h, w), bool)
    big[700:1600, 100:1000] = True
    assert graphics.pin_layout(anchor, "Funafuti Atoll", w, h, home=big) == plain


def test_island_cover_is_the_islands_share_not_the_boxs() -> None:
    home = np.zeros((100, 100), bool)
    home[:, 45:55] = True                                                      # 1,000 px of island
    assert graphics.island_cover([(0, 0, 100, 10)], home) == pytest.approx(0.1)
    assert graphics.island_cover([(0, 0, 100, 10), (0, 5, 100, 20)], home) == pytest.approx(0.2)   # the union
    assert graphics.island_cover([(0, 0, 10, 10)], None) == 0.0


# ---------------------------------------------------------------------------------------------- R5 Bratislava
def test_a_fill_beside_the_site_name_holds_the_country_name(caplog) -> None:
    w, h = 1080, 1920
    # a fill only to the right of the site's name, level with it: 300 x 90 px (the castle's name to its left)
    ring = [(600.0, 700.0), (800.0, 700.0), (800.0, 790.0), (600.0, 790.0)]
    label = (300.0, 720.0, 560.0, 770.0)
    anchor = (430.0, 860.0)
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        got = graphics.country_name_place("SLOVAKIA", [ring], anchor, label, [], w, h, px=20)
    assert got is not None
    (_x, _y), b = got
    assert b[0] - label[2] >= graphics.COUNTRY_LEVEL_MIN_REACH - 1             # a clear gap off the site's name
    assert "level with the site's name" in caplog.text


def test_the_log_says_when_the_words_keep_the_name_out_of_a_fill(caplog) -> None:
    w, h = 1080, 1920
    ring = [(300.0, 700.0), (700.0, 700.0), (700.0, 800.0), (300.0, 800.0)]
    label = (280.0, 690.0, 720.0, 810.0)                                       # the site's name over all of it
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        graphics.country_spot("SLOVAKIA", [ring], np.zeros((h, w), bool), np.zeros((h, w), bool), (500.0, 900.0),
                              label, [], [], w, h)
    assert "would hold its name" in caplog.text and "fits its fill at no size" not in caplog.text


# ---------------------------------------------------------------------------------------------- neighbours
def test_an_adjacent_country_by_the_pin_ranks_first() -> None:
    w, h = 400, 400
    home = np.zeros((h, w), bool)
    home[100:300, 100:200] = True
    long_ = np.zeros((h, w), np.uint8)
    long_[100:300, 200:260] = 1                                  # the longest shared border, far from the pin
    near = np.zeros((h, w), np.uint8)
    near[96:100, 100:140] = 1                                    # a short border by the pin
    a, b = {"display": "LONG"}, {"display": "NEAR"}
    plain = graphics.neighbour_rank([(a, long_), (b, near)], home)
    assert plain[id(a)] < plain[id(b)]
    ranked = graphics.neighbour_rank([(a, long_), (b, near)], home, anchor=(120.0, 104.0))
    assert ranked[id(b)] < ranked[id(a)] and ranked[id(b)][0] == 0.0


# ---------------------------------------------------------------------------------------------- R10 scale bar
def test_the_scale_bar_ranges_follow_ruling_r10() -> None:
    assert graphics.SCALE_RANGE_TALL == (80.0, 240.0)
    assert graphics.SCALE_RANGE_WIDE == (100.0, 300.0)
    for d in np.linspace(5, 3000, 120):
        for rng in (graphics.SCALE_RANGE_TALL, graphics.SCALE_RANGE_WIDE):
            bar = graphics.scale_bar(30.0, float(d), "", bar_range=rng)
            assert rng[0] - 0.5 <= bar.px <= rng[1] + 0.5, (d, bar.label, bar.px)


def test_where_no_length_is_in_range_the_nearest_is_taken_and_logged(caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        bar = graphics.scale_bar(30.0, 1.0, "", max_px=30, bar_range=(150, 230))
    assert bar.px <= 30 + 0.5 and "the nearest" in caplog.text
