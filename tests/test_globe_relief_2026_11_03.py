"""Round 6 (2026-11-03): the context globe (size, corners, never on the home fill, gone before the pin, a GOLD site
dot), the quiet home relief and its coast, and the locator's framing."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from src import brand, geo, graphics
from tests.test_r4_graphics_2026_11_02 import _render_fast

needs_geo = pytest.mark.skipif(not geo.available() or not geo.RELIEF_FILE.exists(),
                               reason="Natural Earth data not downloaded")
CACHE = Path(__file__).resolve().parent.parent / "data" / "geo_cache.json"
DERINKUYU = ("derinkuyu underground city|turkey", "Derinkuyu Underground City", "Turkey")
BIJAPUR = ("bijapur|india", "Gol Gumbaz, Bijapur", "India")


def _place(case):
    p = dict(json.loads(CACHE.read_text(encoding="utf-8"))[case[0]])
    p["label"] = case[1]
    return p


def test_globe_sizes_and_corners() -> None:
    tr = graphics.globe_box(1080, 1920)
    assert tr[2] - tr[0] == graphics.GLOBE_PX == 200 and tr[2] == 888 and tr[0] == 688
    assert tr[1] == graphics.get_text_box(1080, 1920)[1]                 # on the text box's top (330)
    wide = graphics.globe_box(1920, 1080)
    assert wide[2] - wide[0] == graphics.GLOBE_PX_WIDE == 180 and wide[2] == 1766 and wide[1] == 130
    for size in ((1080, 1920), (1920, 1080)):
        boxes = [graphics.globe_box(*size, c) for c in ("tr", "tl", "br", "bl")]
        assert boxes[1][1] == boxes[0][1] and boxes[2][2] == boxes[0][2] and boxes[3][0] == boxes[1][0]
        assert boxes[2][1] > boxes[0][3]


def test_globe_fades_out_before_the_pin() -> None:
    assert graphics.GLOBE_OUT == pytest.approx(graphics.MAP_PIN_AT - 0.2)
    for t in (2.4, 2.5, 3.0, 5.9):
        assert graphics.globe_alpha(t) == 0
    assert 0 < graphics.globe_alpha(graphics.GLOBE_OUT + 0.15) < 1
    assert graphics.globe_alpha(1.0) == pytest.approx(1.0)


def test_globe_inset_fills_site_dot_and_no_small_footprint(monkeypatch) -> None:
    def blob(cx, cy, rx, ry):
        return [(cx + rx * math.cos(2 * math.pi * i / 48), cy + ry * math.sin(2 * math.pi * i / 48)) for i in range(48)]
    turkey = {"names": {"turkey"}, "polygons": [[blob(35, 39, 9, 3.2)]], "display": "Turkey"}
    egypt = {"names": {"egypt"}, "polygons": [[blob(30, 26.5, 5, 4.5)]], "display": "Egypt"}
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "countries", lambda: [turkey, egypt])
    g = graphics.globe_inset(200, 34.7, 38.4, turkey, (33.0, 37.0, 36.0, 40.0), (34.7, 38.4))
    arr = np.asarray(g.convert("RGBA")).astype(int)
    seen = lambda c, tol=30: ((np.abs(arr[..., :3] - np.array(c)).max(-1) < tol) & (arr[..., 3] > 200)).sum()  # noqa: E731
    assert seen(brand.GOLD) > 100 and seen(graphics.GLOBE_LAND, 40) > 30
    assert seen(brand.GOLD_DIM, 12) < 400                                 # the home is no longer GOLD_DIM
    c = g.width // 2
    assert np.abs(arr[c, c, :3] - np.array(brand.GOLD)).max() < 40          # the site's gold dot
    # round 6 review: a 2 px INK ring GLOBE_SITE_RING across, the home seen between it and the dot (no ink disc)
    r = graphics.GLOBE_SITE_RING / 2
    assert np.abs(arr[c, c + round(r - 1), :3] - np.array(brand.INK)).max() < 50
    assert np.abs(arr[c, c + 8, :3] - np.array(brand.GOLD)).max() < 60      # the home's gold inside the ring
    assert arr[0, 0, 3] == 0 and g.width == 200 + 2 * graphics.GLOBE_SHADOW
    assert graphics.GLOBE_LIMB == brand.GOLD                                # round 6 review: 3:1 on land and sea
    # a footprint 3 degrees across is far under 36 px on a 200 px globe: no box drawn
    ring = (np.abs(arr[..., :3] - np.array(brand.GOLD)).max(-1) < 30) & (arr[..., 3] > 200)
    yy, xx = np.mgrid[0:g.height, 0:g.width]
    ring &= np.hypot(xx - c, yy - c) < 100 - graphics.GLOBE_RING - 4      # inside the GOLD limb (round 6 review)
    ys, xs = np.nonzero(ring)
    assert (xs.max() - xs.min()) < 60                                     # the gold is the home and the dot only


def test_place_globe_never_overlaps_the_home_fill() -> None:
    home = np.zeros((1920, 1080), bool)
    home[300:560, 650:900] = True                                         # under the top-right corner
    gb = graphics.place_globe(1080, 1920, home, [])
    assert gb is not None and gb != graphics.globe_box(1080, 1920)
    disc = np.zeros(home.shape, np.uint8)
    import cv2
    cv2.circle(disc, ((gb[0] + gb[2]) // 2, (gb[1] + gb[3]) // 2), (gb[2] - gb[0]) // 2, 1, -1)
    assert not (disc.astype(bool) & home).any()
    assert graphics.place_globe(1080, 1920, np.ones((1920, 1080), bool), []) is None


def test_relief_multiplier_never_brightens_and_fades_near_the_marker() -> None:
    rng = np.random.default_rng(2)
    hill = np.clip(rng.normal(0.75, 0.15, (400, 400)), 0, 1).astype(np.float32)
    home = np.ones((400, 400), np.float32)
    m = graphics.relief_multiplier(hill, home, None)
    assert m.max() <= 1.0 and m.min() >= graphics.RELIEF_HOME_FLOOR - 1e-6
    ctx = graphics.ReliefContext(base_per_px=1.0, marker=(200.0, 200.0))
    q = graphics.relief_multiplier(hill, home, ctx)
    assert (1 - q[200, 200]) <= graphics.RELIEF_NEAR_AMP * (1 - graphics.RELIEF_HOME_FLOOR) + 1e-6
    assert np.allclose(q[5, 5], m[5, 5])


def _home_mask(place, home, w, h, span, lay=None):
    # QF-20: the close-up is drawn on the site-centred azimuthal projection at every latitude; with the layout,
    # the mask uses that same projection (the frame's anchor and span), so it covers the home the frame shows
    polar = None
    if lay is not None:
        (lon, lat), (ax, ay) = lay["anchor_lonlat"], lay["anchor"]
        polar = graphics.PolarProjection(lon, lat, ax, ay, w / span)
    _land, hm = graphics.view_masks(w, h, place["lon"], place["lat"], span, home, polar=polar)
    return hm


@needs_geo
@pytest.mark.parametrize("case", [DERINKUYU, BIJAPUR], ids=["derinkuyu", "bijapur"])
def test_home_relief_is_quiet_and_its_coast_has_no_teal(tmp_path, monkeypatch, case) -> None:
    place = _place(case)
    st = _render_fast(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=1920, height=1080))
    lay = json.loads(Path(str(tmp_path / "m.mp4") + ".layout.json").read_text(encoding="utf-8"))
    home = geo.find_country(case[2])
    hm = _home_mask(place, home, 1920, 1080, lay["span_close"], lay)
    import cv2
    inner = cv2.erode(hm.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
    arr = np.asarray(st["frames"][-1]).astype(np.float32)
    words = np.zeros(inner.shape, bool)
    for _n, p in st["plates"]:
        x0, y0, x1, y1 = p.box
        words[max(0, y0 - 30):y1 + 30, max(0, x0 - 30):x1 + 30] = True
    pin = lay["anchor"]
    words[int(pin[1]) - 120:int(pin[1]) + 40, int(pin[0]) - 60:int(pin[0]) + 60] = True
    sel = inner & ~words
    lum = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2])[sel]
    assert lum.std() <= 9.0, lum.std()
    ring = cv2.dilate(hm.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) & ~inner
    teal = (np.abs(arr[ring] - np.array(brand.TEAL)).max(axis=1) <= 6).sum()
    assert teal == 0


@needs_geo
@pytest.mark.parametrize("case, size", [(DERINKUYU, (1080, 1920)), (BIJAPUR, (1920, 1080))],
                         ids=["derinkuyu-9x16", "bijapur-16x9"])
def test_framing_shares(case, size) -> None:
    place = _place(case)
    home = geo.find_country(case[2])
    span = graphics.locator_span(place["lat"], place["lon"], home, *size)
    hm = _home_mask(place, home, *size, span)
    share = graphics.home_share(hm, graphics.get_text_box(*size))
    assert 0.20 <= share <= 0.60 and 1 - share >= 0.25, (span, share)
    if case is DERINKUYU:
        assert span < graphics.LOCATOR_SPAN                                # zoomed in
    else:
        assert span > graphics.LOCATOR_SPAN                                # zoomed out


@needs_geo
def test_bijapur_globe_never_on_the_home_fill(tmp_path, monkeypatch) -> None:
    place = _place(BIJAPUR)
    _render_fast(monkeypatch, lambda: graphics.render_map(place, tmp_path / "m.mp4", width=1080, height=1920))
    lay = json.loads(Path(str(tmp_path / "m.mp4") + ".layout.json").read_text(encoding="utf-8"))
    if lay["globe"] is None:
        return
    gb = lay["globe"]
    hm = _home_mask(place, geo.find_country("India"), 1080, 1920, lay["span_close"])
    import cv2
    disc = np.zeros(hm.shape, np.uint8)
    cv2.circle(disc, ((gb[0] + gb[2]) // 2, (gb[1] + gb[3]) // 2), (gb[2] - gb[0]) // 2, 1, -1)
    assert not (disc.astype(bool) & hm).any()
