"""QF-26: one antialiased path for drawn geometry (src/raster.py, Skia coverage). The land, borders, scale bar,
scale drawing (silhouettes, monolith, strata ground, rulers, brackets) and the stat cards' bars and rules take the
exact share of each pixel they cover; whole-pixel layout stays as ImageDraw drew it, and colours inside a shape are
unchanged."""

import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, geo, graphics


def _raster():
    # imported here, so each test fails (not the module) where the path is missing
    from src import raster
    return raster


def _reference(rings, size, ss=16):
    """Exact-ish coverage: ss x ss point samples a pixel, even-odd over the rings."""
    w, h = size
    ys = (np.arange(h * ss) + 0.5) / ss
    xs = (np.arange(w * ss) + 0.5) / ss
    inside = np.zeros((h * ss, w * ss), bool)
    X, Y = xs[None, :], ys[:, None]
    for ring in rings:
        r = np.asarray(ring, float)
        for (x1, y1), (x2, y2) in zip(r, np.roll(r, -1, axis=0)):
            if y1 != y2:
                inside ^= ((y1 > Y) != (y2 > Y)) & (X < x1 + (Y - y1) * (x2 - x1) / (y2 - y1))
    return inside.reshape(h, ss, w, ss).mean(axis=(1, 3))


LAND = [[(6.25, 9.75), (40.5, 4.25), (58.75, 30.5), (44.25, 57.75), (12.5, 50.25)],
        [(22.25, 22.5), (38.75, 24.25), (31.5, 38.75)]]          # a coast with a lake in it


def test_whole_pixel_layout_is_drawn_exactly_as_imagedraw_drew_it():
    raster = _raster()
    calls = [lambda d: d.rectangle([5, 6, 30, 20], fill=200),
             lambda d: d.rectangle([5, 6, 30, 20], fill=90, outline=255, width=3),
             *[lambda d, w=w: d.line([(4, 20), (36, 20)], fill=255, width=w) for w in (1, 2, 3, 4, 5)],
             *[lambda d, w=w: d.line([(20, 4), (20, 36)], fill=255, width=w) for w in (1, 2, 3, 4, 5)],
             lambda d: d.line([(4, 12), (20, 12), (36, 12)], fill=255, width=3, joint="curve"),
             # review 1: drawn right to left and upwards, an even width's extra row lies on ImageDraw's side
             *[lambda d, w=w, j=j: d.line([(36, 20), (4, 20)], fill=255, width=w, joint=j)
               for w in (2, 3, 4, 6) for j in (None, "curve")],
             *[lambda d, w=w, j=j: d.line([(20, 36), (20, 4)], fill=255, width=w, joint=j)
               for w in (2, 3, 4, 6) for j in (None, "curve")]]
    for call in calls:
        a, b = Image.new("L", (40, 40), 0), Image.new("L", (40, 40), 0)
        call(ImageDraw.Draw(a))
        call(raster.Draw(b))
        assert np.array_equal(np.asarray(a), np.asarray(b))
    # review 1: blended, an outline in the fill's ink is not drawn (ImageDraw skips it; drawn, it blends twice)
    a, b = Image.new("RGB", (40, 40), (14, 22, 34)), Image.new("RGB", (40, 40), (14, 22, 34))
    for im, d in ((a, ImageDraw.Draw(a, "RGBA")), (b, raster.Draw(b, "RGBA"))):
        d.rectangle([5, 6, 30, 20], fill=(201, 162, 39, 128), outline=(201, 162, 39, 128), width=2)
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_fractional_land_takes_its_exact_coverage_and_imagedraw_did_not():
    raster = _raster()
    ref = _reference(LAND, (64, 64))
    edge = (ref > 0.002) & (ref < 0.998)

    def draw(d):
        d.polygon(LAND[0], fill=255)
        d.polygon(LAND[1], fill=0)
    new, old = Image.new("L", (64, 64), 0), Image.new("L", (64, 64), 0)
    draw(raster.Draw(new, grid="exact"))
    draw(ImageDraw.Draw(old))
    new_a, old_a = np.asarray(new, float) / 255, np.asarray(old, float) / 255
    rmse = lambda a: float(np.sqrt(np.mean((a[edge] - ref[edge]) ** 2)))
    assert rmse(new_a) < 0.02 and rmse(old_a) > 0.15
    assert abs(new_a.sum() - ref.sum()) < 1.0 and old_a.sum() - ref.sum() > 10    # no half-pixel dilation
    assert len(np.unique(np.round(new_a[edge] * 255))) > 40                      # tones, not two levels


def test_colour_is_unchanged_inside_and_mixed_only_at_the_edge():
    raster = _raster()
    ground, ink = (14, 22, 34), (201, 162, 39)
    rgb = Image.new("RGB", (64, 64), ground)
    raster.Draw(rgb, "RGBA").polygon(LAND[0], fill=(*ink, 255))
    arr = np.asarray(rgb).astype(int)
    cov = _reference([LAND[0]], (64, 64))
    near = np.zeros_like(cov, bool)                   # within a pixel of any coverage
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            near |= np.roll(np.roll(cov > 0, dy, 0), dx, 1)
    assert (arr[cov > 0.999] == ink).all() and (arr[~near] == ground).all()
    mid = (cov > 0.3) & (cov < 0.7)
    mixed = ground + (np.array(ink) - ground) * cov[mid][:, None]
    assert np.abs(arr[mid] - mixed).max() <= 6
    # on a transparent layer the edge keeps the ink's colour and takes the coverage as alpha (no dark fringe)
    layer = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    raster.Draw(layer).polygon(LAND[0], fill=(*ink, 255))
    la = np.asarray(layer).astype(int)
    part = (la[..., 3] > 20) & (la[..., 3] < 235)
    assert part.sum() > 50 and np.abs(la[part][:, :3] - ink).max() <= 2


def test_the_scale_bar_grows_through_fractional_pixels():
    bar = graphics.scale_bar(41.0, 40.0, "")
    im = Image.new("RGB", (900, 200), brand.INK)
    x, y = 100.0, 100.0
    reach = 0.53 * bar.length
    graphics._draw_scale_bar(im, bar, x, y, 0.53)
    end = x + reach
    arr = np.asarray(im).astype(int)
    col = arr[y.__int__() - 1:int(y) + 2, int(end) - 2:int(end) + 3]
    lum = col.mean(axis=-1)
    line, ink = np.mean(graphics.SCALE_LINE), np.mean(brand.INK)
    assert ((lum > ink + 20) & (lum < line - 20)).any()           # the bar's end lies inside a pixel
    assert (arr[int(y), int(x) + 20] == graphics.SCALE_LINE).all()  # its colour unchanged inside


@pytest.fixture
def blob_world(monkeypatch):
    def blob(cx, cy, rx, ry):
        return [(cx + rx * math.cos(2 * math.pi * i / 60), cy + ry * math.sin(2 * math.pi * i / 60)) for i in range(60)]

    def country(name, cx, cy, rx, ry):
        ring = blob(cx, cy, rx, ry)
        return {"names": {name.lower()}, "sovereign": name.lower(), "polygons": [[ring]], "size": len(ring),
                "display": name, "label_at": None, "label_rank": 9, "type": "Sovereign country",
                "extent": (cx - rx, cy - ry, cx + rx, cy + ry)}
    world = [country("Westland", 30.0, 40.0, 6.0, 3.0), country("Eastland", 38.5, 40.5, 3.0, 2.5)]
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "countries", lambda: world)
    monkeypatch.setattr(geo, "view_polygons", lambda *a, **k: [(c, c["polygons"][0]) for c in world])
    monkeypatch.setattr(graphics, "_relief", lambda: None)
    return world


def test_the_map_base_draws_coasts_and_borders_antialiased(blob_world):
    lat0, lon0, bw, bh = 40.0, 33.0, 800, 600
    kx, coslat = bw / 24.0, math.cos(math.radians(lat0))
    project = lambda lon, lat: (bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx)
    base = graphics.relief_base(lat0, lon0, bw, bh, kx, coslat, None, project, (0, 0, bw, bh))
    arr = np.asarray(base).astype(int)
    land = np.asarray(graphics.LAND_TINT, float) * (graphics.RELIEF_NEIGHBOUR[0] + graphics.RELIEF_NEIGHBOUR[1] * 0.75)
    # along Westland's west coast (a sea to land edge with no border on it), the pixels the coast crosses
    cx, cy = project(24.0, 40.0)
    row = arr[int(cy), int(cx) - 6:int(cx) + 7]
    lum = row.mean(axis=-1)
    sea_l, land_l = lum.min(), float(np.mean(land))
    between = (lum > sea_l + 6) & (lum < land_l - 6)
    assert between.any(), lum
    # the border between the two countries is a 3 px line centred on the shared coast: mixed tones beside its core
    bx, by = project(36.0, 40.0)
    seg = arr[int(by) - 40:int(by) + 40, int(bx) - 30:int(bx) + 30]
    border = np.abs(seg - np.array(graphics.MAP_BORDER)).max(axis=-1) <= 2
    assert border.any()
    tones = {tuple(p) for p in seg.reshape(-1, 3)}
    assert len(tones) > 12


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_the_scale_drawing_is_drawn_on_the_antialiased_path(tmp_path, monkeypatch, size):
    """Both formats: the strata ground, rulers, shaft, silhouette, monolith and brackets are drawn through
    src/raster.py (ImageDraw before), the ground keeps its EARTH colour, and the silhouette's edges take tones."""
    frames, used = [], []

    def fake_encode(frames_, path, n, width=1080, height=1920):
        frames.extend(f.convert("RGB") for f in frames_)
        return path
    for name in ("_silhouette", "_bracket", "_monolith"):
        real = getattr(graphics, name)

        def spy(d, *a, _real=real, _name=name, **k):
            used.append((_name, type(d).__module__))
            return _real(d, *a, **k)
        monkeypatch.setattr(graphics, name, spy)
    monkeypatch.setattr(graphics, "_encode", fake_encode)
    monkeypatch.setattr(graphics, "VIDEO_FPS", 4)
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 3.5)
    monkeypatch.setattr(graphics, "graded_frame", lambda frame, work, vignette=False: None)
    fact = {"value": 327.0, "unit": "m", "dimension": "depth", "subject": "Test shaft", "approximate": True}
    assert graphics.render_scale(fact, tmp_path / "s.mp4", width=size[0], height=size[1]) is not None
    assert {n for n, _ in used} >= {"_silhouette", "_bracket"}
    assert {m for _, m in used} == {"src.raster"}, used[:3]
    arr = np.asarray(frames[-1]).astype(int)
    assert np.all(arr == graphics.EARTH, axis=-1).sum() > 2000      # the strata ground, its colour unchanged


def test_the_stat_cards_bars_and_rules_are_drawn_on_the_antialiased_path(tmp_path, monkeypatch):
    raster = _raster()
    boxes = []
    real = raster.Draw.rectangle

    def spy(self, xy, *a, **k):
        boxes.append(tuple(float(v) for v in xy))
        return real(self, xy, *a, **k)
    monkeypatch.setattr(raster.Draw, "rectangle", spy)
    monkeypatch.setattr(graphics, "VIDEO_FPS", 8)
    monkeypatch.setattr(graphics, "graded_frame", lambda frame, work, vignette=False: None)
    monkeypatch.setattr(graphics, "_encode", lambda frames, path, n, width=1080, height=1920: [*frames] and path)
    graphics.render_stat_cards([{"value": "about 30-40 m", "label": "below the surface"}], tmp_path / "n.mp4")
    assert boxes                                                   # the kicker's rule and the range's dash
    assert any(v != round(v) for b in boxes for v in b)            # the settling dash between whole pixels


LAT0, LON0, BW, BH = 40.0, 30.0, 900, 700
KX, COSLAT = BW / 14.0, math.cos(math.radians(LAT0))


def _project(lon, lat):
    return BW / 2 + (lon - LON0) * KX * COSLAT, BH / 2 - (lat - LAT0) * KX


def _home_base(bpp: float, holes=()) -> np.ndarray:
    """relief_base on a synthetic home blob (with `holes`, lon/lat rings) at base scale `bpp`."""
    ring = [(30.0 + 5.0 * math.cos(2 * math.pi * i / 90 + 0.37), 40.0 + 3.0 * math.sin(2 * math.pi * i / 90 + 0.37))
            for i in range(90)]
    home = {"names": {"homeland"}, "sovereign": "homeland", "polygons": [[ring, *holes]], "size": 90,
            "display": "Homeland", "label_at": None, "label_rank": 9, "type": "Sovereign country",
            "extent": (25.0, 37.0, 35.0, 43.0)}
    token = graphics._RELIEF_CTX.set(graphics.ReliefContext(base_per_px=bpp, wide=False))
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(geo, "available", lambda: True)
            mp.setattr(geo, "countries", lambda: [home])
            mp.setattr(geo, "view_polygons", lambda *a, **k: [(home, home["polygons"][0])])
            mp.setattr(graphics, "_relief", lambda: None)
            return np.asarray(graphics.relief_base(LAT0, LON0, BW, BH, KX, COSLAT, home, _project,
                                                   (0, 0, BW, BH))).astype(float)
    finally:
        graphics._RELIEF_CTX.reset(token)


def _gold_rim_share(bpp: float) -> float:
    """The home coast's GOLD_LIGHT inner edge on a synthetic home blob: the mean, over rows and columns across
    its coast, of the largest share of GOLD_LIGHT over the home fill."""
    arr = _home_base(bpp)
    bw, bh = BW, BH
    gl, inside = np.array(graphics.GOLD_LIGHT, float), arr[bh // 2, bw // 2]
    t = ((arr - inside) @ (gl - inside)) / ((gl - inside) @ (gl - inside))
    cy, cx = bh // 2, bw // 2
    shares = [t[y, :cx].max() for y in range(cy - 150, cy + 151)] + [t[y, cx:].max() for y in range(cy - 150, cy + 151)]
    shares += [t[:cy, x].max() for x in range(cx - 200, cx + 201)] + [t[cy:, x].max() for x in range(cx - 200, cx + 201)]
    return float(np.mean(shares))


@pytest.mark.parametrize("bpp,base_share", [(1.0, 0.467), (1.6, 0.593), (2.4, 0.729)])
def test_the_home_coasts_gold_edge_keeps_its_strength(bpp, base_share):
    """Review 1 (blocking): the home edge's ring and INK line were cut from the antialiased mask and then blurred,
    so the coast was softened twice (peak share 0.366 / 0.530 / 0.710 at these scales). The shares the base
    (a3946226) draws are the floor, within 2 %."""
    assert _gold_rim_share(bpp) >= 0.98 * base_share


@pytest.mark.parametrize("bpp,cx,cy,side,drawn", [(1.0, 560.3, 330.3, 0.3, False), (2.4, 560.3, 330.3, 0.3, False),
                                                   (1.0, 560.0, 330.0, 1.2, True)])
def test_a_loch_smaller_than_a_pixel_keeps_its_ink_and_gold(bpp, cx, cy, side, drawn):
    """Round 2: the home edge is cut where the home covers half a pixel; a loch whose coverage reaches half of no
    pixel would vanish from the INK line and the gold (Flannan's lochs did). It keeps the pixels it touches, as the
    base drew it: an INK dot ringed with gold. Port onto QF-20: a hole with no one-pixel-wide interior at the
    drawing scale is not drawn at all (QF-20's map_geometry.resolvable_hole: sub-pixel slivers between source
    layers made false coasts), so the 0.3 px loch leaves the home fill whole at both scales; a 1.2 px loch on a
    pixel corner is drawn and still reaches half of no pixel, and keeps its INK dot and gold."""
    sq = [(cx - side / 2, cy - side / 2), (cx + side / 2, cy - side / 2), (cx + side / 2, cy + side / 2),
          (cx - side / 2, cy + side / 2)]
    assert geo.map_geometry.resolvable_hole(sq, bpp) is drawn
    assert not drawn or graphics._closed_at_half(sq)        # the pinhole case: no pixel reaches half
    loch = [(LON0 + (x - BW / 2) / (KX * COSLAT), LAT0 + (BH / 2 - y) / KX) for x, y in sq]
    arr = _home_base(bpp, holes=[loch])
    lum = arr @ np.array([0.299, 0.587, 0.114])
    gl, inside = np.array(graphics.GOLD_LIGHT, float), arr[BH // 2, BW // 2]
    t = ((arr - inside) @ (gl - inside)) / ((gl - inside) @ (gl - inside))
    near = (slice(int(cy) - 2, int(cy) + 3), slice(int(cx) - 2, int(cx) + 3))
    ring = (slice(int(cy) - 6, int(cy) + 7), slice(int(cx) - 6, int(cx) + 7))
    if drawn:
        assert lum[near].min() < 60                         # the INK dot
        assert t[ring].max() > 0.3                          # its gold
    else:
        whole = np.asarray(_home_base(bpp)) @ np.array([0.299, 0.587, 0.114])
        assert np.array_equal(lum[ring], whole[ring])       # not drawn: the fill as with no loch
