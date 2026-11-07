"""Round 6 (2026-11-03): the locator's site marker (MAP_MARKER_STYLE pin, dot, leader), its site label, the country's
name inside its fill, and the route's stops as dots under every style."""
import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand, graphics
from tests.test_r3_graphics_2026_11_02 import fake_geo  # noqa: F401  (fixture)
from tests.test_r4_graphics_2026_11_02 import _render_fast, route_geo  # noqa: F401  (fixture)

PLACE = {"lat": 38.3735, "lon": 34.7347, "label": "Derinkuyu", "country": "Turkey"}


def test_style_defaults_to_pin_and_an_invalid_one_raises(monkeypatch) -> None:
    assert graphics.MAP_MARKER_STYLE == "pin" and graphics.marker_style() == "pin"
    monkeypatch.setattr(graphics, "MAP_MARKER_STYLE", "flag")
    with pytest.raises(AssertionError):
        graphics.marker_style()


def test_pin_tip_is_the_site_and_its_geometry() -> None:
    tip = (540.3, 960.7)
    pts = graphics.pin_outline(tip)
    assert pts[0] == tip                                                     # sub-pixel, exactly
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert max(xs) - min(xs) == pytest.approx(graphics.PIN_HEAD_D, abs=0.5)  # head 56
    assert tip[1] - min(ys) == pytest.approx(graphics.PIN_HEIGHT, abs=0.5)    # 84 tall
    im = Image.new("RGB", (1080, 1920), (120, 120, 120))
    graphics._draw_pin(im, tip, 1.0, graphics.pin_motion(graphics.MAP_PIN_AT + 2.0))
    arr = np.asarray(im).astype(int)
    diff = np.abs(arr - 120).max(axis=2) > 30
    ys_, xs_ = np.nonzero(diff[700:961, :])
    assert abs(ys_.max() + 700 - tip[1]) <= 1.5                              # the tip within 1 px of the site
    core = arr[round(tip[1] - graphics.PIN_HEIGHT + graphics.PIN_HEAD_D / 2), round(tip[0])]
    assert np.abs(core - np.array(brand.GOLD)).max() < 40                     # the gold inner disc


def test_pin_motion_falls_squashes_settles_and_ripples_once() -> None:
    t0 = graphics.MAP_PIN_AT
    assert not graphics.pin_motion(t0 - 0.1).visible
    assert graphics.pin_motion(t0).drop == pytest.approx(-graphics.PIN_FALL_PX)
    impact = graphics.pin_motion(t0 + 0.30)
    assert (impact.sx, impact.sy) == graphics.PIN_SQUASH and impact.drop == 0
    settled = graphics.pin_motion(t0 + 0.48)
    assert settled.sx == pytest.approx(1.0) and settled.sy == pytest.approx(1.0)
    assert graphics.pin_motion(t0 + 0.30 + 0.61).ripple_a == 0
    assert graphics.pin_motion(t0 + 0.45).ripple_a > 0
    assert graphics.pin_motion(t0 + 0.15).shadow < 1.0
    a, _rise = graphics.marker_label_motion(2.95, "pin")
    assert a == pytest.approx(1.0, abs=0.01) and graphics.marker_label_motion(t0 + 0.39, "pin")[0] == 0


def test_pin_label_sits_over_the_head_and_flips_near_the_top() -> None:
    lay = graphics.pin_layout((540.0, 960.0), "Derinkuyu Underground City", 1080, 1920)
    head_top = 960 - graphics.PIN_HEIGHT
    last_base = max(xy[1] for _t, xy, _a in lay.rows)
    assert last_base == pytest.approx(head_top - graphics.MARKER_LABEL_GAP) and lay.position == "above"
    assert len(lay.rows) <= graphics.MARKER_LABEL_ROWS and lay.font_px == graphics.MARKER_LABEL_PX
    top = graphics.get_text_box(1080, 1920)[1]
    near = graphics.pin_layout((540.0, top + 120.0), "Derinkuyu Underground City", 1080, 1920)
    assert near.position == "flip" and near.label_box[1] >= top + 120
    wide = graphics.pin_layout((960.0, 540.0), "Derinkuyu", 1920, 1080)
    assert wide.font_px == graphics.MARKER_LABEL_PX_WIDE


def test_dot_is_an_ink_core_in_a_bone_ring() -> None:
    im = Image.new("RGB", (200, 200), (120, 120, 120))
    graphics._draw_dot(im, (100, 100), 1.0)
    arr = np.asarray(im).astype(int)
    assert np.abs(arr[100, 100] - np.array(brand.INK)).max() < 8
    assert np.abs(arr[100, 100 + 9] - np.array(brand.BONE)).max() < 8
    ring = np.abs(arr[100] - 120).max(axis=1) > 20
    # round 6 review: 24 across in all (a 14 core, a 4 px ring, a 1 px keyline inside that), not 26
    assert ring.sum() == pytest.approx(graphics.DOT_D, abs=1) and graphics.DOT_D == 24
    assert graphics.dot_radii(1.0) == (12.0, 11.0, 7.0)


def test_dot_edge_is_antialiased() -> None:
    # round 6 review: supersampled, so its edge takes intermediate values (no stair-steps)
    im = Image.new("RGB", (200, 200), (120, 120, 120))
    graphics._draw_dot(im, (100.3, 100.6), 1.0)
    arr = np.asarray(im).astype(int)[:, :, 0]
    edge = arr[80:120, 80:120]
    mids = ((edge > brand.INK[0] + 10) & (edge < 110)).sum()
    assert mids >= 12


@pytest.mark.parametrize("size, order", [((1080, 1920), ("above", "below", "right", "left")),
                                         ((1920, 1080), ("right", "left", "above", "below"))])
def test_dot_label_takes_the_first_free_imhof_place(size, order) -> None:
    anchor = (size[0] / 2, size[1] / 2)
    blocked: list = []
    for want in order:
        lay = graphics.dot_layout(anchor, "Derinkuyu", *size, obstacles=blocked)
        assert lay.position == want, (blocked, lay.position)
        b = lay.label_box                                                 # force the next one: its far edge
        blocked.append({"above": (b[0], b[1], b[2], b[1] + 4), "below": (b[0], b[3] - 4, b[2], b[3]),
                        "right": (b[2] - 4, b[1], b[2], b[3]), "left": (b[0], b[1], b[0] + 4, b[3])}[want])
        cap = graphics.cap_height(graphics.get_font("Anton", lay.font_px))
        if want in ("right", "left"):                                     # its first row's cap middle on the dot
            assert lay.rows[0][1][1] - cap / 2 == pytest.approx(anchor[1])
        else:
            assert (lay.label_box[0] + lay.label_box[2]) / 2 == pytest.approx(anchor[0], abs=2)
    lay = graphics.dot_layout(anchor, "Derinkuyu", *size, obstacles=blocked)
    assert lay.position == "leader" and lay.leader is not None             # all four blocked


def test_dot_label_rejects_a_coast_straddle() -> None:
    land = np.zeros((1920, 1080), bool)
    land[:, :540] = True                                                  # a coast down the middle
    lay = graphics.dot_layout((540.0, 960.0), "Derinkuyu", 1080, 1920, land=land)
    assert graphics.coast_share(lay.label_box, land) <= graphics.MARKER_COAST_SHARE
    assert lay.position in ("right", "left", "leader")


def test_leader_length_and_ends() -> None:
    assert graphics.MAP_LEADER_MAX == 48
    lay = graphics.dot_layout((540.0, 960.0), "Derinkuyu", 1080, 1920, leader_only=True)
    (ax, ay), (bx, by) = lay.leader
    length = math.dist((ax, ay), (bx, by))
    assert graphics.MAP_LEADER_MIN - 0.5 <= length <= graphics.MAP_LEADER_MAX + 0.5
    ring = graphics.DOT_D / 2                                               # round 6 review: 24 across
    assert math.dist((bx, by), (540, 960)) == pytest.approx(ring + graphics.MARKER_LEAD_RING_GAP, abs=0.5)
    gap = graphics.box_distance((ax, ay), lay.label_box)
    assert gap == pytest.approx(graphics.MARKER_LEAD_LABEL_GAP, abs=0.5)  # to the site label, not the country
    assert lay.style == "leader"


def _map(monkeypatch, tmp_path, style, place=PLACE, size=(1080, 1920)):
    monkeypatch.setattr(graphics, "MAP_MARKER_STYLE", style)
    out = tmp_path / f"m_{style}.mp4"
    st = _render_fast(monkeypatch, lambda: graphics.render_map(place, out, width=size[0], height=size[1]))
    st["layout"] = json.loads(Path(str(out) + ".layout.json").read_text(encoding="utf-8"))
    return st


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("style", graphics.MAP_MARKER_STYLES)
def test_every_style_names_the_site_nearest_and_the_country_away(tmp_path, monkeypatch, fake_geo, style,  # noqa: F811
                                                                  size) -> None:
    st = _map(monkeypatch, tmp_path, style, size=size)
    anchor = tuple(st["layout"]["anchor"])
    words = [(n, p) for n, p in st["plates"] if not n.startswith(("scale", "neighbour", "water"))]
    nearest = min(words, key=lambda np_: graphics.box_distance(anchor, np_[1].box))
    assert nearest[0] == "place" and "DERINKUYU" in nearest[1].text
    assert not any(p.text == "TURKEY" and n == "place" for n, p in st["plates"])     # no floating title either
    assert sum(n == "place" for n, _p in st["plates"]) <= graphics.MARKER_LABEL_ROWS
    country = dict(st["plates"]).get("country")
    # round 7 (review: `if country is not None` let a missing name pass, Great Zimbabwe 9:16): always named
    assert country is not None and country.text == "TURKEY", st["layout"]["country"]
    c = ((country.box[0] + country.box[2]) / 2, (country.box[1] + country.box[3]) / 2)
    k = min(size) / 1080
    assert math.dist(c, anchor) >= graphics.COUNTRY_ANCHOR_AIR * k - 1
    assert all(e["drawn"] >= brand.TEXT_CONTRAST for e in st["record"]["elements"] if e["name"] == "country")
    assert st["layout"]["style"] == style
    if style == "leader":
        assert st["layout"]["leader"] is not None


@pytest.mark.parametrize("style", graphics.MAP_MARKER_STYLES)
def test_an_approximate_place_draws_no_marker(tmp_path, monkeypatch, fake_geo, style) -> None:  # noqa: F811
    place = {"lat": 38.6, "lon": 34.8, "label": "Cappadocia", "country": "Turkey", "approximate": True,
             "matched": "region"}
    st = _map(monkeypatch, tmp_path, style, place)
    assert dict(st["plates"])["approx"].text == "APPROX. LOCATION"
    arr = np.asarray(st["frames"][-1]).astype(int)
    patch = arr[960 - 6:960 + 7, 540 - 6:540 + 7]
    assert not (np.abs(patch - np.array(brand.INK)).max(axis=2) < 12).any()     # no pin body or dot core
    assert not (np.abs(patch - np.array(brand.BONE)).max(axis=2) < 12).any()
    assert st["layout"]["style"] == "area"


def test_routes_draw_dots_only_under_the_pin_style(tmp_path, monkeypatch, route_geo) -> None:  # noqa: F811
    from tests.test_r4_graphics_2026_11_02 import ROUTES
    monkeypatch.setattr(graphics, "MAP_MARKER_STYLE", "pin")
    pins: list = []
    monkeypatch.setattr(graphics, "_draw_pin", lambda im, *a, **k: pins.append(a) or im)
    dots: list = []
    real = graphics._draw_dot
    monkeypatch.setattr(graphics, "_draw_dot", lambda d, xy, *a, **k: dots.append(xy) or real(d, xy, *a, **k))
    _render_fast(monkeypatch, lambda: graphics.render_route(ROUTES["five"], tmp_path / "r.mp4"))
    assert not pins and len({tuple(round(v) for v in xy) for xy in dots}) == len(ROUTES["five"])
    assert graphics.ROUTE_STOP_R == graphics.DOT_D / 2
