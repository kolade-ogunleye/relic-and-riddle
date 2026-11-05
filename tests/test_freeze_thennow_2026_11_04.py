"""Freeze panel 2026-11-04, then-and-now (src/then_now.py): the label's halo is cropped wide enough for its whole
tail (no stepped rectangle on a near-white photo), the glow's outline is rounded where the kicker meets the year,
the NOW marker sits off the divider with every line's first ink on one column, the kicker is tightened onto the
year, ink type on paper, a warm dithered glow, the divider reveals the labels at the exact pixel, symmetric
keylines, and a sweep that arrives moving."""
import hashlib
import sys
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand, config, safe_zones, then_now
from src.brand import DATA_FONT, INK
from src.typography import TextPlate, cover_crop, full_halo_pad, get_font

PORTRAIT, LANDSCAPE = (1080, 1920), (1920, 1080)
FPS = config.VIDEO_FPS


# ---------------------------------------------------------------- grounds and a frame grabber
def _flat(path, rgb, size=(1200, 2100)):
    Image.new("RGB", size, rgb).save(path)
    return path


def _gradient(path, lo=214, hi=248, size=(1200, 2100), seed=7):
    """A vertical ramp lo -> hi with 1 LSB TPDF grain, saved losslessly."""
    rng = np.random.default_rng(seed)
    w, h = size
    ramp = np.linspace(lo, hi, h)[:, None, None] * np.ones((1, w, 3))
    grain = rng.random((h, w, 1)) + rng.random((h, w, 1)) - 1.0
    Image.fromarray(np.clip(np.round(ramp + grain), 0, 255).astype(np.uint8), "RGB").save(path)
    return path


def _noise(path, lo, hi, size=(1200, 2100), seed=1):
    rng = np.random.default_rng(seed)
    arr = rng.integers(lo, hi, (size[1] // 12, size[0] // 12, 3), dtype=np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


def _map_like(path, size=(1200, 2100), seed=4):
    """A light, busy ground: pale paper crossed by dense dark linework (a sea chart, a street map)."""
    rng = np.random.default_rng(seed)
    w, h = size
    arr = np.full((h, w, 3), 236, np.uint8)
    for _ in range(900):
        x, y = rng.integers(0, w), rng.integers(0, h)
        arr[y:y + 3, max(0, x - 40):x + 40] = 70
        arr[max(0, y - 40):y + 40, x:x + 3] = 70
    Image.fromarray(arr).save(path)
    return path


def _render(monkeypatch, tmp_path, old, new, size, keep=(), stop=None, years=(1925, 2019)):
    """Renders the clip without encoding: {"frames": {index: RGB array}, "last": PIL frame, "rows": plates}."""
    got = {"frames": {}}

    def enc(frames, out, n, **k):
        for i, im in enumerate(frames):
            if i in keep:
                got["frames"][i] = np.asarray(im.convert("RGB")).copy()
            got["last"] = im
            if stop is not None and i >= stop:
                break
        return out
    monkeypatch.setattr(then_now, "_encode", enc)
    monkeypatch.setattr(then_now, "record_contrast", lambda clip, frame, rows: got.update(rows=rows))
    got["clip"] = then_now.render(old, years[0], new, years[1], tmp_path / "x.mp4", width=size[0], height=size[1])
    if "rows" not in got:   # stopped early: the plates are still wanted
        got["rows"] = None
    return got


def _rows(monkeypatch, tmp_path, old, new, size, years=(1925, 2019)):
    got = _render(monkeypatch, tmp_path, old, new, size, years=years)
    return dict(got["rows"]), got


def _box8(lum):
    """An 8 x 8 box mean (the glow's dither is one value per 2 x 2 cell, +/-2 codes: a single pixel pair reads it)."""
    import cv2
    return cv2.blur(np.asarray(lum, np.float64), (8, 8))


def _sources(old, new, size):
    big = (round(size[0] * (1 + then_now.PUSH)), round(size[1] * (1 + then_now.PUSH)))
    return cover_crop(old, big).convert("RGB"), cover_crop(new, big).convert("RGB")


def _lum(a):
    a = np.asarray(a, np.float64)
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _plates_of(monkeypatch, tmp_path, old, new, size, keep, stop):
    """The grabbed frames and the plates (captured from the planner, since the render stops early)."""
    plates = {}
    real = then_now._marker_plate

    def spy(split_frame, alone, xy, text, font, colour, anchor, *a, **k):
        p = real(split_frame, alone, xy, text, font, colour, anchor, *a, **k)
        plates[text] = p                   # the last plan of each line is the one drawn
        return p
    monkeypatch.setattr(then_now, "_marker_plate", spy)
    got = _render(monkeypatch, tmp_path, old, new, size, keep=keep, stop=stop)
    return got, plates


# ---------------------------------------------------------------- S1: the whole halo inside its crop
def test_full_halo_pad_is_the_halo_tail_plus_rounding():
    assert full_halo_pad(36) == 92 and full_halo_pad(31) == 80
    assert full_halo_pad(36) == math.ceil(2.425 * 36) + 4


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
@pytest.mark.parametrize("text,font,halo_px,track", [
    ("THEN", ("mono", 48), 36, 7.2),
    ("2019", ("Anton", 128), 31, 0),
    ("NOW", ("mono", 40), 36, 6.0),
    ("1888", ("Anton", 112), 31, 0),
])
def test_the_halo_has_faded_out_at_its_crop_edge(size, text, font, halo_px, track):
    f = get_font(DATA_FONT, font[1], weight=600) if font[0] == "mono" else get_font("Anton", font[1])
    xy = (size[0] // 2 - 100, size[1] // 2 - 60)
    p = TextPlate(text, f, brand.BONE, xy, "lt", track, 0, INK, halo_px, False, size, halo_pad=full_halo_pad(halo_px))
    h, w = p.glyphs.shape
    assert p.origin[0] > 0 and p.origin[1] > 0 and p.origin[0] + w < size[0] and p.origin[1] + h < size[1]
    a = np.asarray(p.halo_mask)
    assert max(a[0].max(), a[-1].max(), a[:, 0].max(), a[:, -1].max()) <= 1
    # the default crop (halo_px + 16) still cuts it: the defect this fixes
    q = TextPlate(text, f, brand.BONE, xy, "lt", track, 0, INK, halo_px, False, size)
    b = np.asarray(q.halo_mask)
    assert max(b[0].max(), b[:, 0].max()) >= 5


def test_a_plate_without_halo_pad_is_byte_identical_to_before():
    """Recorded from the code before the change (HEAD 5fdb5dc typography.py, loaded side by side: the same origin,
    box, crop, halo mask and composite). Pinned to raqm (operator decision 2026-11-05) per platform: Linux raqm and
    Windows raqm (Pillow 12.3.0, FriBiDi 1.0.18) both draw its ink 1 px wider than BASIC layout did."""
    from PIL import features
    assert features.check("raqm"), "Pillow raqm layout is unavailable (FriBiDi missing): measured pins are raqm's"
    box, shape, sha = HALO_DEFAULT_PINS["win32" if sys.platform == "win32" else "linux"]
    f = get_font("Anton", 128)
    p = TextPlate("1925", f, brand.BONE, (84, 392), "lt", 0, 0, INK, 31, False, PORTRAIT)
    assert p.origin == (38, 345) and p.box == box
    assert p.glyphs.shape == shape
    assert hashlib.sha256(p.halo_mask.tobytes()).hexdigest()[:16] == sha
    # and the halo alpha is still truncated, not rounded, on the default path
    base = Image.new("RGBA", p.halo_mask.size, (250, 250, 250, 255))
    out = np.asarray(p._layer(base, 0.5))[..., 0].astype(int)
    v = np.asarray(p.halo_mask).astype(int)
    want = np.round(250 - (250 - INK[0]) * (v * 0.5).astype(int) / 255).astype(int)
    glyphless = np.asarray(p.full) == 0
    assert np.abs(out - want)[glyphless].max() <= 1


HALO_DEFAULT_SHA = "50a9763159c699fa"   # sha256 prefix of the halo mask under BASIC layout (no longer pinned)
# raqm per platform: (box, glyph crop shape, halo-mask sha256 prefix). Linux raqm (CI with libfribidi0, and the cloud
# container: Pillow 12.3.0, raqm 0.10.5) measures exactly what Windows raqm does; the earlier Linux pin (314 / 323 /
# 50a97631) was BASIC layout's, measured before CI had FriBiDi.
HALO_DEFAULT_PINS = {"linux": ((85, 392, 315, 504), (206, 324), "64873fb3a147fc06"),
                     "win32": ((85, 392, 315, 504), (206, 324), "64873fb3a147fc06")}


GROUNDS = {
    "paper": lambda p: _flat(p, (0xF3, 0xEF, 0xE6)),
    "white": lambda p: _flat(p, (0xFA, 0xFA, 0xFA)),
    "ramp": lambda p: _gradient(p),
}


def _perimeter_contribution(plate, frame):
    """How much drawing `plate` again changes `frame`'s luminance on its crop's outer two rows and columns: the
    step its crop leaves where it ends."""
    before = _lum(np.asarray(frame.convert("RGB")))
    after = _lum(np.asarray(plate.draw(frame).convert("RGB")))
    x0, y0 = plate.origin
    h, w = plate.glyphs.shape
    d = np.abs(after - before)[y0:y0 + h, x0:x0 + w]
    return max(d[:2].max(), d[-2:].max(), d[:, :2].max(), d[:, -2:].max())


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
@pytest.mark.parametrize("ground", list(GROUNDS))
def test_no_stepped_rectangle_round_a_label_on_a_near_white_photo(tmp_path, monkeypatch, size, ground):
    """S1(c): a near-white old photo, a dark recent one (so ink type does not apply: the bone path, with its
    glow): at THEN alone and at the resting split, each plate's crop leaves no step where it ends. Round 2 (T1):
    ink type is now decided per marker, so the near-white THEN marker would take ink; the bone path is forced."""
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)
    old = GROUNDS[ground](tmp_path / "old.png")
    new = _noise(tmp_path / "new.png", 40, 70)
    then_alone, rest = round(0.9 * FPS), round(3.6 * FPS)
    got, plates = _plates_of(monkeypatch, tmp_path, old, new, size, keep=(then_alone, rest), stop=rest)
    assert plates["THEN"].fill != tuple(INK)                              # the bone/gold path, as intended
    for idx in (then_alone, rest):
        frame = Image.fromarray(got["frames"][idx])
        for text in ("THEN", "1925", "NOW", "2019"):
            p = plates[text]
            if idx == then_alone and text in ("NOW", "2019"):
                continue                                                  # not shown yet
            assert _perimeter_contribution(p, frame) <= 1.0, (idx, text)
        # and on the delivered pixels: the THEN kicker crop's top edge and the year crop's bottom edge
        lum = _lum(got["frames"][idx])
        for p, edge in ((plates["THEN"], "top"), (plates["1925"], "bottom")):
            x0, y0 = p.origin
            h, w = p.glyphs.shape
            xs = slice(max(0, x0), min(x0 + w, then_now._split_at(*size) - 8 if size[0] > size[1] else size[0]))
            y = y0 if edge == "top" else y0 + h
            prof = [lum[yy, xs].mean() for yy in (y - 2, y - 1, y, y + 1)]
            assert np.abs(np.diff(prof)).max() <= 2, (idx, edge, prof)


def test_the_panel_measured_points_no_longer_step(tmp_path, monkeypatch):
    """S1(d): the steps the panel measured (9:16 flat paper, x 269 -> 270 on the kicker row; 16:9 sky, y 116 ->
    117 over the THEN kicker), on the bone path."""
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)
    old = _flat(tmp_path / "paper.png", (0xF3, 0xEF, 0xE6))
    got, plates = _plates_of(monkeypatch, tmp_path, old, old, PORTRAIT, keep=(round(3.6 * FPS),), stop=round(3.6 * FPS))
    lum = _lum(got["frames"][round(3.6 * FPS)])
    b = plates["THEN"].box
    row = _box8(lum)[b[1] - 20:b[3] + 20]           # round 2 (T3): 8 x 8 means, so the 2 x 2-cell dither averages
    assert np.abs(row[:, 270] - row[:, 269]).max() <= 2
    sky = _gradient(tmp_path / "sky.png", 196, 246, size=(2100, 1200))
    got, plates = _plates_of(monkeypatch, tmp_path, sky, sky, LANDSCAPE, keep=(round(0.9 * FPS),), stop=round(0.9 * FPS))
    lum = _lum(got["frames"][round(0.9 * FPS)])
    b = plates["THEN"].box
    assert abs(lum[117, b[0]:b[2]].mean() - lum[116, b[0]:b[2]].mean()) <= 2


# ---------------------------------------------------------------- S2: a rounded glow outline
BOXES = [(83, 330, 200, 364), (80, 402, 310, 512)]


def test_the_glow_has_no_crease_where_the_kicker_meets_the_year():
    m = np.asarray(then_now.glow_mask(BOXES, 1080, 1920, 0.6)).astype(int)
    col = m[340:421, 230]                                          # right of the kicker, over the year
    assert np.abs(np.diff(col, 2)).max() <= 3
    ray = [m[364 + s, 200 + s] for s in range(0, 38)]               # 45 degrees out of the inside corner
    assert np.abs(np.diff(ray)).max() <= 2


def _iso_radius(m, level, corner, within=40):
    """The radius of the circle fitted (least squares) to the mask's `level` iso-line points within `within` px
    of `corner`."""
    above = m >= level
    edge = above ^ np.roll(above, 1, axis=0) | above ^ np.roll(above, 1, axis=1)
    ys, xs = np.nonzero(edge)
    keep = np.hypot(xs - corner[0], ys - corner[1]) <= within
    x, y = xs[keep].astype(float), ys[keep].astype(float)
    a = np.column_stack([x, y, np.ones_like(x)])
    c, *_ = np.linalg.lstsq(a, x * x + y * y, rcond=None)
    cx, cy = c[0] / 2, c[1] / 2
    return math.sqrt(c[2] + cx * cx + cy * cy)


def test_the_half_depth_outline_is_rounded_at_the_join(monkeypatch):
    then_now._lines_distance.cache_clear()
    m = np.asarray(then_now.glow_mask(BOXES, 1080, 1920, 0.6)).astype(int)
    half = round(255 * 0.6) / 2
    pad, fall = then_now.BAND_PAD, then_now.BAND_FALL
    mid = pad + fall / 2                                            # the half-depth distance
    corner = (199 + mid, 402 - mid)                                 # where a hard minimum's outline turns
    assert _iso_radius(m, half, corner) >= 24
    monkeypatch.setattr(then_now, "GLOW_SMOOTH_K", 1e-3)            # a hard minimum: a near-square turn
    then_now._lines_distance.cache_clear()
    hard = np.asarray(then_now.glow_mask(BOXES, 1080, 1920, 0.6)).astype(int)
    then_now._lines_distance.cache_clear()
    assert _iso_radius(hard, half, corner) < _iso_radius(m, half, corner)


def test_the_lines_false_shade_is_unchanged():
    boxes = [(83, 330, 170, 350), (80, 372, 310, 484)]
    m = np.asarray(then_now._shade_mask((1080, 1920), boxes, (0, 400), 0.6)).astype(int)
    assert m[400, 0] == round(255 * 0.6) and m[1500, 900] == 0


# ---------------------------------------------------------------- S3 / S4: placement and the lock-up
def _line_boxes(which, year, size):
    w, h = size
    key = "landscape" if w > h else "portrait"
    kf, yf = get_font(DATA_FONT, then_now.KICKER_PX[key], weight=600), get_font("Anton", then_now.YEAR_PX[key])
    word = then_now.kicker_word(which, year)
    kxy, fxy, anchor = then_now.marker_lines(which, year, w, h, word=word)
    k = TextPlate(word, kf, brand.BONE, kxy, anchor, then_now.KICKER_PX[key] * then_now.KICKER_TRACK[key], 0,
                  INK, 0, False, size)
    y = TextPlate(str(year), yf, brand.BONE, fxy, anchor, 0, 0, INK, 0, False, size)
    return k.box, y.box


def test_the_916_now_kicker_sits_96_px_below_the_divider():
    split = then_now._split_at(*PORTRAIT)
    k, _ = _line_boxes("now", 2019, PORTRAIT)
    assert k[1] - split >= 96 and abs(k[1] - (split + then_now.NOW_DROP)) <= 1
    assert then_now.NOW_DROP >= then_now.BAND_PAD + then_now.BAND_FALL     # round 2 (T2): the glow's reach ends short of it


@pytest.mark.parametrize("year", [1908, 2019, 868, 1125])
def test_every_left_set_line_starts_on_one_ink_column(year):
    tk, ty = _line_boxes("then", year, PORTRAIT)
    nk, ny = _line_boxes("now", year, PORTRAIT)
    assert abs(tk[0] - ty[0]) <= 1 and abs(nk[0] - ny[0]) <= 1 and abs(tk[0] - nk[0]) <= 1
    lk, ly = _line_boxes("now", year, LANDSCAPE)                 # 16:9 NOW is left-set too
    assert abs(lk[0] - ly[0]) <= 1
    rk, ry = _line_boxes("then", year, LANDSCAPE)                # 16:9 THEN is set right: it ends on its x
    assert abs(rk[2] - ry[2]) <= 1                               # round 2 (T9c): right-bearing compensation


@pytest.mark.parametrize("size,gap", [(PORTRAIT, (27, 29)), (LANDSCAPE, (23, 25))], ids=["9x16", "16x9"])
def test_the_kicker_sits_tight_on_the_year(tmp_path, monkeypatch, size, gap):
    assert then_now.YEAR_OFFSET == pytest.approx(1.30)
    rows, _ = _rows(monkeypatch, tmp_path, _noise(tmp_path / "a.png", 60, 240, seed=2),
                    _noise(tmp_path / "b.png", 60, 240, seed=3), size, years=(2008, 2019))
    for w in ("then", "now"):
        k, y = rows[f"{w}_kicker"].box, rows[f"{w}_year"].box
        assert gap[0] <= y[1] - k[3] <= gap[1], (w, k, y)
        assert k[3] < y[1]
    if size == PORTRAIT:
        b = rows["then_year"].box                                  # the widest year fits the column, either side
        assert (84 <= b[0] and b[2] <= 440) or (600 <= b[0] and b[2] <= 870)
        x0, y0, x1, y1 = safe_zones.graphics_box(*size)
        for name, p in rows.items():
            assert p.box[0] >= x0 and p.box[1] >= y0 and p.box[2] <= x1 and p.box[3] <= y1, name


def test_the_row_under_the_divider_is_not_shaded(tmp_path, monkeypatch):
    """S3(c): at the split on a near-white pair (bone path), the first photo row below the divider's keyline is
    within 2 codes of the same row with no glow and no halo."""
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)
    old = _flat(tmp_path / "paper.png", (0xF3, 0xEF, 0xE6))
    idx = round(3.6 * FPS)
    got, plates = _plates_of(monkeypatch, tmp_path, old, old, PORTRAIT, keep=(idx,), stop=idx)
    a, b = _sources(old, old, PORTRAIT)
    split = then_now._split_at(*PORTRAIT)
    bare = then_now.compose(then_now.pushed(a, idx / FPS, PORTRAIT), then_now.pushed(b, idx / FPS, PORTRAIT), split)
    row = split + then_now.DIVIDER_W // 2 + then_now.KEYLINE_W + 1
    diff = _lum(got["frames"][idx][row, 40:401]) - _lum(np.asarray(bare)[row, 40:401])
    assert np.abs(diff).max() <= 2


# ---------------------------------------------------------------- S5: ink type on paper
@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
def test_paper_grounds_take_ink_type_with_no_glow_and_no_halo(tmp_path, monkeypatch, size):
    shaded = []
    real = then_now._shade
    monkeypatch.setattr(then_now, "_shade", lambda im, m, a=1.0: shaded.append(m.getbbox()) or real(im, m, a))
    rows, _ = _rows(monkeypatch, tmp_path, _flat(tmp_path / "old.png", (0xF3, 0xEF, 0xE6)),
                    _flat(tmp_path / "new.png", (0xFA, 0xFA, 0xFA)), size)
    assert {p.fill for p in rows.values()} == {tuple(INK)}
    assert all(p.halo == 0 and p.halo_mask is None for p in rows.values())
    assert all(bb is None for bb in shaded)                        # no glow on any frame
    assert all((p.contrast or 0) >= 12 for p in rows.values()), {n: p.contrast for n, p in rows.items()}


def test_one_pale_photo_and_one_dark_one_take_ink_and_bone(tmp_path, monkeypatch):
    """Round 2 (T1): ink type is per marker. The pale photo's marker is ink; the dark one's kicker is bone, not
    gold, so the pair reads as two neutrals."""
    rows, _ = _rows(monkeypatch, tmp_path, _flat(tmp_path / "old.png", (0xF3, 0xEF, 0xE6)),
                    _noise(tmp_path / "new.png", 40, 70), PORTRAIT)
    assert rows["then_kicker"].fill == rows["then_year"].fill == tuple(INK)
    assert rows["now_kicker"].fill == rows["now_year"].fill == tuple(brand.BONE)


def test_a_busy_light_map_stays_on_the_bone_path(tmp_path, monkeypatch):
    chart = _map_like(tmp_path / "chart.png")
    st = then_now.paper_stats(Image.open(chart).convert("RGB"), (0, 0, 1200, 2100))
    assert st["p10"] < then_now.PAPER_P10                          # round 2 (T1): the 10th percentile falls on the linework
    rows, _ = _rows(monkeypatch, tmp_path, chart, chart, PORTRAIT)
    assert all(p.fill != tuple(INK) for p in rows.values())


def test_ink_type_is_a_design_constant_that_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)
    rows, _ = _rows(monkeypatch, tmp_path, _flat(tmp_path / "old.png", (0xF3, 0xEF, 0xE6)),
                    _flat(tmp_path / "new.png", (0xFA, 0xFA, 0xFA)), PORTRAIT)
    assert all(p.fill != tuple(INK) for p in rows.values())


def test_on_paper_reads_lightness_and_chroma():
    paper = Image.new("RGB", (400, 400), (0xF3, 0xEF, 0xE6))
    sky = Image.new("RGB", (400, 400), (150, 200, 250))            # pale but blue
    grey = Image.new("RGB", (400, 400), (170, 170, 170))           # neutral but mid
    box = [(150, 150, 250, 200)]
    assert then_now.on_paper(box, [(paper, (0, 0, 400, 400))])
    assert not then_now.on_paper(box, [(sky, (0, 0, 400, 400))])
    assert not then_now.on_paper(box, [(grey, (0, 0, 400, 400))])
    assert not then_now.on_paper(box, [(paper, (0, 0, 400, 400)), (grey, (0, 0, 400, 400))])   # every frame


# ---------------------------------------------------------------- S6: a warm, dithered glow
def test_the_glow_is_warm_ink_composited_in_float():
    im = Image.new("RGB", (64, 64), (120, 120, 120))
    out = np.asarray(then_now._shade(im, Image.new("L", (64, 64), 255), 0.2)).astype(float)
    want = 0.8 * 120 + 0.2 * np.asarray(then_now.GLOW_INK, float)
    assert np.abs(out - want).max() <= 2.6                         # round 2 (T3): +/-2.04 codes of dither, rounded
    assert out[..., 0].mean() > out[..., 2].mean()                 # warm: R over B
    assert then_now.GLOW_INK == (30, 22, 16)


def test_the_glow_ramp_does_not_terrace_on_a_smooth_sky(tmp_path):
    w, h = 1080, 1920
    ramp = np.linspace(214, 248, h)[:, None, None] * np.ones((1, w, 3))
    rng = np.random.default_rng(3)
    ground = Image.fromarray(np.clip(np.round(ramp + rng.random((h, w, 1)) + rng.random((h, w, 1)) - 1), 0, 255)
                             .astype(np.uint8), "RGB")
    mask = then_now.glow_mask(BOXES, w, h, 0.2)
    out = _lum(np.asarray(then_now._shade(ground, mask)))
    x = 150
    col = np.round(out[512 + then_now.BAND_PAD:512 + then_now.BAND_PAD + then_now.BAND_FALL, x]).astype(int)
    change = np.flatnonzero(np.diff(col) != 0)
    runs = np.diff(np.concatenate(([0], change + 1, [col.size])))
    assert runs.max() <= 6


def test_the_dither_is_static_frame_to_frame():
    im = Image.new("RGB", (1080, 1920), (230, 228, 220))
    mask = then_now.glow_mask(BOXES, 1080, 1920, 0.45)
    a, b = then_now._shade(im, mask), then_now._shade(im.copy(), mask)
    assert a.tobytes() == b.tobytes()
    assert np.unique(np.asarray(a)[530:548, 150, 0]).size > 1      # and dithered where the glow falls (T2: shorter)


# ---------------------------------------------------------------- S7: the divider reveals the labels
def _dark_pair(tmp_path, size):
    s = (2100, 1200) if size[0] > size[1] else (1200, 2100)
    return _flat(tmp_path / "a.png", (52, 48, 44), s), _flat(tmp_path / "b.png", (40, 44, 50), s)


@pytest.mark.parametrize("size,which", [(PORTRAIT, "now"), (LANDSCAPE, "then")], ids=["9x16", "16x9"])
def test_a_label_the_divider_crosses_is_not_drawn(tmp_path, monkeypatch, size, which):
    """Round 2 (T5, replacing S7's exact-pixel reveal): on every frame where the divider crosses a marker, the whole
    group (kicker and year) is off; the frame there is the photo alone."""
    old, new = _dark_pair(tmp_path, size)
    texts = ("NOW", "2019") if which == "now" else ("THEN", "1925")
    got, plates = _plates_of(monkeypatch, tmp_path, old, new, size, keep=tuple(range(36, 100)), stop=99)
    box = [min(plates[x].box[0] for x in texts), min(plates[x].box[1] for x in texts),
           max(plates[x].box[2] for x in texts), max(plates[x].box[3] for x in texts)]
    landscape = size[0] > size[1]
    lo, hi = (box[0], box[2]) if landscape else (box[1], box[3])
    crossing = [i for i in range(36, 100) if lo < then_now.divider_at(i / FPS, *size) < hi]
    assert crossing
    a, b = _sources(old, new, size)
    for i in crossing:
        pos = then_now.divider_at(i / FPS, *size)
        bare = np.asarray(then_now.compose(then_now.pushed(a, i / FPS, size), then_now.pushed(b, i / FPS, size), pos,
                                           divider=False)).astype(int)
        p = round(pos)
        frame = got["frames"][i].astype(int)
        keep = np.zeros(frame.shape[:2], bool)
        keep[box[1]:box[3], box[0]:box[2]] = True
        if landscape:
            keep[:, max(0, p - 60):p + 60] = False              # the divider and its blurred trail
        else:
            keep[max(0, p - 60):p + 60, :] = False
        d = np.abs(frame - bare).max(axis=2)
        if keep.any():
            assert d[keep].max() <= 2, i


def test_then_fades_in_between_0_1_and_0_4_s(tmp_path, monkeypatch):
    old, new = _dark_pair(tmp_path, PORTRAIT)
    early, late = 3, 12                                            # t 0.1 s and 0.4 s
    got, plates = _plates_of(monkeypatch, tmp_path, old, new, PORTRAIT, keep=(early, late), stop=late)
    a, b = _sources(old, new, PORTRAIT)
    for i, want_on in ((early, False), (late, True)):
        bare = then_now.compose(then_now.pushed(a, i / FPS, PORTRAIT), then_now.pushed(b, i / FPS, PORTRAIT), 1920)
        lit = plates["1925"].draw(plates["THEN"].draw(bare))
        target = np.asarray(lit if want_on else bare).astype(int)
        assert np.abs(got["frames"][i] - target).max() <= 2, i


def test_at_rest_the_now_label_stays_below_the_divider(tmp_path, monkeypatch):
    old, new = _dark_pair(tmp_path, PORTRAIT)
    got = _render(monkeypatch, tmp_path, old, new, PORTRAIT)
    rows = dict(got["rows"])
    split = then_now._split_at(*PORTRAIT)
    for name in ("now_kicker", "now_year"):
        assert rows[name].origin[1] >= split + 3                   # its crop, halo included, starts below the line
    n = int(then_now.CLIP_SECONDS * FPS)
    a, b = _sources(old, new, PORTRAIT)
    bare = np.asarray(then_now.compose(then_now.pushed(a, (n - 1) / FPS, PORTRAIT),
                                       then_now.pushed(b, (n - 1) / FPS, PORTRAIT), split)).astype(int)
    last = np.asarray(got["last"].convert("RGB")).astype(int)
    assert np.abs(last[split - 4:split + 3] - bare[split - 4:split + 3]).max() == 0   # the divider, untouched


# ---------------------------------------------------------------- S8: symmetric keylines
def _runs_across(line):
    """(bone run length, ink run before, ink run after) across the divider on a 1-D strip of RGB pixels."""
    bone = np.all(line == np.asarray(brand.BONE), axis=1)
    idx = np.flatnonzero(bone)
    ground = line[0]
    darker = np.any(line != ground, axis=1) & ~bone
    lo, hi = idx.min(), idx.max()
    before = 0
    while darker[lo - 1 - before]:
        before += 1
    after = 0
    while darker[hi + 1 + after]:
        after += 1
    return lo, hi, before, after


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
@pytest.mark.parametrize("pos", [400, 768, 960, 1500])
def test_the_divider_is_five_bone_pixels_between_two_equal_keylines(size, pos):
    a = Image.new("RGB", size, (150, 140, 130))
    im = np.asarray(then_now.compose(a, a.copy(), pos))
    line = im[size[1] // 2, :] if size[0] > size[1] else im[:, size[0] // 2]
    lo, hi, before, after = _runs_across(line)
    assert (lo, hi) == (pos - 2, pos + 2) and hi - lo + 1 == 5
    assert before == after == 2
    assert tuple(line[pos - 4]) == tuple(line[pos + 4])            # the same keyline colour on both sides


# ---------------------------------------------------------------- S9: the sweep arrives moving
def test_the_sweep_is_one_landing():
    """Round 2 (T4): one landing, WIPE then / to_far / hold_far / settle, the split holding 2.7 s."""
    W = then_now.WIPE
    assert W == {"then": 1.2, "to_far": 1.0, "hold_far": 0.25, "settle": 0.85}
    assert then_now.CLIP_SECONDS - sum(W.values()) == pytest.approx(2.7)
    w, h = PORTRAIT
    first = then_now.divider_at(36 / FPS, w, h) - then_now.divider_at(37 / FPS, w, h)
    assert 95 <= first <= 105                                      # in at speed from off-frame
    assert then_now.divider_at(3.3, w, h) == pytest.approx(then_now._split_at(w, h))


# ================================================================ freeze panel round 2 (T1-T10)
RUNS = Path(__file__).resolve().parents[1] / "output"
REAL = {   # the catalogue's real inputs (docs/reviews/freeze-panel-2026-11-04/fixtures): photo pairs and their years
    "pantheon": ("run_roman_self_healing_concrete_1793163918", "then_0.jpg", 1934, "now_0_1.jpg", 2018),
    "colossi": ("run_singing_colossi_of_memnon_1792952878", "then_0.jpg", 1876, "now_0_1.jpg", 2011),
    # the labelled stress cases: the brightest real photos (rejected candidates), years from the fact sheets
    "odessa": ("run_odessa_catacombs_1792970549", "then_2.jpg", 1941, "now_0_4.jpg", 1942),
    "golgumbaz": ("run_gol_gumbaz_whispering_gallery_1793278143", "then_2.jpg", 1656, "now_2_0.jpg", 1937),
    # review round 3: the reviewer's line-art stress case (the run's own candidates; years from its fact sheet)
    "aswan": ("run_unfinished_obelisk_aswan_1792985912", "then_2.jpg", 1922, "now_0_0.jpg", 2005),
}
# fix round 1 (framing only, not in the gate table): the reviewer's busy case (review3/busy_aswan), an archive
# negative scan with a black film rebate inside its white print margin (the run's own then_0 / now_0_1; years from
# its fact sheet)
REAL_FRAMING = {"aswan_rebate": ("run_unfinished_obelisk_aswan_1792985912", "then_0.jpg", 1922, "now_0_1.jpg", 2005)}


def _real(name):
    run, old, oy, new, ny = REAL.get(name) or REAL_FRAMING[name]
    g = RUNS / run / "graphics"
    if not (g / old).exists() or not (g / new).exists():
        pytest.skip(f"real run inputs not on this machine: {g}")
    return g / old, oy, g / new, ny


def _plan_only(monkeypatch, tmp_path, old, oy, new, ny, size):
    """then_now.render() up to its first frame (plates and last_meta are planned before any frame)."""
    _render(monkeypatch, tmp_path, old, new, size, keep=(), stop=0, years=(oy, ny))
    return dict(then_now.last_meta)


# ---------------------------------------------------------------- T1: the per-marker paper gate
BOX = [(60, 60, 240, 140)]
FULL = (0, 0, 300, 200)


@pytest.mark.parametrize("v,ok", [(195, True), (194, False)])
def test_paper_gate_mean_boundary(v, ok):
    im = Image.new("RGB", (300, 200), (v, v, v))
    assert then_now.paper_stats(im, FULL)["mean"] == pytest.approx(v, abs=0.01)
    assert then_now.on_paper(BOX, [(im, FULL)]) is ok


@pytest.mark.parametrize("v,ok", [(140, True), (139, False)])
def test_paper_gate_p10_boundary(v, ok):
    a = np.full((200, 300, 3), 250, np.uint8)
    a[36:56] = v                                 # 20 of the padded box's 128 rows at v: its 10th percentile is v
    im = Image.fromarray(a)
    st = then_now.paper_stats(im, (36, 36, 264, 164))
    assert st["p10"] == pytest.approx(v, abs=0.01) and st["mean"] >= 195 and st["edge"] <= 0.2
    assert then_now.on_paper(BOX, [(im, FULL)]) is ok


@pytest.mark.parametrize("delta,ok", [(0.0, True), (-1e-4, False)])
def test_paper_gate_edge_boundary(monkeypatch, delta, ok):
    a = np.full((200, 300, 3), 250, np.uint8)
    a[:, ::9] = 200                              # pale lines: a measured edge density, mean and p10 still paper
    im = Image.fromarray(a)
    st = then_now.paper_stats(im, (36, 36, 264, 164))
    assert st["mean"] >= 195 and st["p10"] >= 140 and 0.05 < st["edge"] < 0.6
    monkeypatch.setattr(then_now, "PAPER_EDGE", st["edge"] + delta)   # the threshold at the measured value
    assert then_now.on_paper(BOX, [(im, FULL)]) is ok


def test_the_gate_constants():
    assert (then_now.PAPER_MEAN, then_now.PAPER_P10, then_now.PAPER_EDGE, then_now.PAPER_PAD) == (195, 140, 0.20, 24)
    assert (then_now.PAPER_DARK_FRAC, then_now.PAPER_DARK) == (0.6, 0.02)       # review round 3: line art
    assert not hasattr(then_now, "PAPER_L10") and not hasattr(then_now, "PAPER_CHROMA")


# ---------------------------------------------------------------- review round 3: no ink type on line art
def _line_art(dark_rows: int):
    """A white diagram ground with `dark_rows` thin black rules across the padded box (36..164 x 36..264): low edge
    density and a bright mean / p10, as Aswan's diagram measured, so only the dark-structure test can tell."""
    a = np.full((200, 300, 3), 250, np.uint8)
    for k in range(dark_rows):
        a[40 + 4 * k, 36:264] = 20
    return Image.fromarray(a)


# Fix round 2: the dark share is read under the lines (PAPER_DARK_PAD), so the line-art tests set their rules under
# a marker whose ink box is the old padded box (36..264 x 36..164); the shares and expected results are unchanged.
LINE_BOX = [(36, 36, 264, 164)]


@pytest.mark.parametrize("delta,ok", [(0.0, True), (-1e-4, False)])
def test_paper_gate_dark_boundary(monkeypatch, delta, ok):
    """PAPER_DARK at the measured dark share of three 1-px black rules (3 of the padded box's 128 rows): at the
    threshold it is paper, a hair under it is not; the spec's three tests pass throughout."""
    im = _line_art(3)
    st = then_now.paper_stats(im, (36, 36, 264, 164))
    assert st["mean"] >= 195 and st["p10"] >= 140 and st["edge"] <= 0.20      # paper by the spec's three tests
    assert st["dark"] == pytest.approx(3 / 128, abs=1e-4)
    monkeypatch.setattr(then_now, "PAPER_DARK", st["dark"] + delta)
    assert then_now.on_paper(LINE_BOX, [(im, FULL)]) is ok


def test_the_shipping_dark_limit_takes_two_rules_and_refuses_three():
    assert then_now.on_paper(LINE_BOX, [(_line_art(2), FULL)])                # 0.0156 <= 0.02
    assert not then_now.on_paper(LINE_BOX, [(_line_art(3), FULL)])            # 0.0234 > 0.02


def test_dark_structure_beside_the_lines_is_not_under_them():
    """Fix round 2 (operator 2026-11-04): dark structure inside the PAPER_PAD box but clear of the lines' own boxes
    (a rock ridge below a year on white sky) leaves the marker on paper; the same structure under the lines does
    not. The mean / p10 / edge tests still read the padded box."""
    a = np.full((200, 300, 3), 250, np.uint8)
    a[142:164, 36:264] = 20                      # a dark band 2..24 px below the ink box (60..240 x 60..140)
    im = Image.fromarray(a)
    ok, st = then_now.paper_gate(BOX, [(im, FULL)])
    assert st[0]["dark_padded"] > then_now.PAPER_DARK and st[0]["dark"] == 0.0
    assert then_now.PAPER_DARK_PAD == 0
    # the band's 22 of the padded box's 128 rows hold its p10 down (20 < PAPER_P10): only the dark test is relaxed
    assert not ok and st[0]["p10"] < then_now.PAPER_P10
    a[142:144, 36:264] = 20
    a[144:164] = 250                             # a 2 px rule beside the lines: paper by every test
    ok, st = then_now.paper_gate(BOX, [(Image.fromarray(a), FULL)])
    assert ok and st[0]["dark"] == 0.0 and st[0]["dark_padded"] == pytest.approx(2 / 128, abs=1e-4)
    a[100:104, 60:240] = 20                      # the same structure under the year: refused (4 / 80 rows)
    ok, st = then_now.paper_gate(BOX, [(Image.fromarray(a), FULL)])
    assert not ok and st[0]["dark"] == pytest.approx(4 / 80, abs=1e-4)


def test_the_real_aswan_then_on_white_sky_is_ink_again(tmp_path, monkeypatch):
    """Fix round 2 blocker: 9:16 Aswan THEN 1922 sits on white sky with the rock ridge below its year inside the
    padded box (dark 0.048 padded). HEAD set it in ink; round 3 drew it bone at halo 1.0 with a 0.20 glow (a grey
    cloud). Under the lines it measures 0.016 / 0.003: ink, no halo, no glow, >= 7:1. NOW on the diagram stays bone."""
    old, oy, new, ny = _real("aswan")
    meta = _plan_only(monkeypatch, tmp_path, old, oy, new, ny, PORTRAIT)
    g = meta["paper_gate"]["then"]
    # Fix round 3: with then_2's 203 px of sky no longer trimmed (test_the_real_aswan_sky_is_not_a_mount) the ridge
    # is out of the padded box too, as at HEAD; it was `dark_padded > PAPER_DARK` (0.048, measured on the trim).
    assert then_now.PAPER_DARK >= max(g["split"]["dark"], g["alone"]["dark"], g["split"]["dark_padded"])
    assert meta["type_mode"] == {"then": "ink", "now": "bone"}
    assert meta["glow_depth"]["then"] is None
    assert min(meta["planned_contrast"]["then_kicker"], meta["planned_contrast"]["then_year"]) >= 7.0


def test_a_line_art_ground_takes_the_bone_path(tmp_path, monkeypatch):
    """Review round 3 (blocking): a pale diagram with black rules and labels passed the mean / p10 / edge gate and
    got black ink type over its own black linework (record_contrast read 13-14:1). It is not paper."""
    a = np.full((2100, 1200, 3), 248, np.uint8)
    a[::40, :] = 25                                  # thin black rules every 40 px, both ways: a diagram, not paper
    a[:, ::40] = 25
    Image.fromarray(a).save(tmp_path / "diagram.png")
    rows, _ = _rows(monkeypatch, tmp_path, tmp_path / "diagram.png", tmp_path / "diagram.png", PORTRAIT)
    meta = then_now.last_meta
    for w in ("then", "now"):
        st = meta["paper_gate"][w]["split"]
        assert st["mean"] >= 195 and st["p10"] >= 140 and st["edge"] <= 0.20 and st["dark"] > then_now.PAPER_DARK
        assert meta["type_mode"][w] != "ink"
    assert all(p.fill != tuple(INK) for p in rows.values())


def test_the_real_aswan_diagram_is_not_paper(tmp_path, monkeypatch):
    """The reviewer's case (review2/aswan_9x16_f165_now_crop.png): Aswan's diagram NOW 2005 drawn bone, not ink."""
    old, oy, new, ny = _real("aswan")
    for size in (PORTRAIT, LANDSCAPE):
        meta = _plan_only(monkeypatch, tmp_path, old, oy, new, ny, size)
        assert meta["type_mode"]["now"] != "ink", meta["paper_gate"]["now"]
        assert meta["paper_gate"]["now"]["split"]["dark"] > then_now.PAPER_DARK


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
def test_ink_markers_on_paper_have_no_glow_and_read_at_7(tmp_path, monkeypatch, size):
    """T1(b, c, d): a pale ground under THEN, a dark busy one under NOW: THEN ink at >= 7:1 planned and no glow;
    NOW's kicker bone, not gold."""
    s = (2100, 1200) if size[0] > size[1] else (1200, 2100)
    old = _flat(tmp_path / "old.png", (0xF3, 0xEF, 0xE6), s)
    new = _noise(tmp_path / "new.png", 30, 90, s)
    rows, _ = _rows(monkeypatch, tmp_path, old, new, size)
    meta = then_now.last_meta
    assert meta["type_mode"] == {"then": "ink", "now": "bone"}
    assert rows["then_kicker"].contrast >= 7.0 and rows["then_year"].contrast >= 7.0
    assert rows["then_kicker"].halo == 0 and rows["then_year"].halo == 0
    assert rows["now_kicker"].fill == tuple(brand.BONE)
    assert meta["glow_depth"]["then"] is None


EXPECTED_GATE = {  # the spec's T1(a) list: where the paper gate fires, with 9:16 Colossi THEN re-measured after T7
    ("pantheon", "9x16"): set(), ("pantheon", "16x9"): set(),
    # Review round 3, re-measured after T7 as the spec asks: the 9:16 shared window (zoom 1.25) brings the far
    # statue's head into THEN's alone-frame padded box (0.027), but none of it is under the lines (0.000): fix round 2
    # (operator 2026-11-04, dark share under the lines) makes 9:16 Colossi THEN ink again (round 3 had set()).
    ("colossi", "9x16"): {"then"}, ("colossi", "16x9"): {"then"},
    ("odessa", "9x16"): {"now"}, ("odessa", "16x9"): set(),
    # Review round 3 (line art): Gol Gumbaz THEN is a printed map (dark share 0.031-0.040 under the marker) and its
    # 16:9 LATER sits on the mausoleum's dark edge (0.068); neither is paper now (was {"then"} and {"then", "now"}).
    ("golgumbaz", "9x16"): set(), ("golgumbaz", "16x9"): set(),
    # Fix round 2 (operator 2026-11-04, no grey cloud worse than HEAD): 9:16 Aswan THEN on white sky is ink as at
    # HEAD (dark under the lines 0.016 / 0.003; round 3 had set()). Its diagram NOW stays refused (0.090).
    ("aswan", "9x16"): {"then"}, ("aswan", "16x9"): set(),
}
# Where ink is drawn: the gate's markers whose ink lines plan >= INK_TARGET (7.0, T1b).
EXPECTED_INK = dict(EXPECTED_GATE)
REAL_CASES = [(n, s) for n in REAL for s in ("9x16", "16x9")]


@pytest.mark.parametrize("name,fmt", REAL_CASES, ids=[f"{n}-{f}" for n, f in REAL_CASES])
def test_the_gate_on_the_real_runs(tmp_path, monkeypatch, name, fmt):
    """T1(a) on the catalogue's real inputs: ink exactly where both of the marker's grounds are paper."""
    size = PORTRAIT if fmt == "9x16" else LANDSCAPE
    old, oy, new, ny = _real(name)
    meta = _plan_only(monkeypatch, tmp_path, old, oy, new, ny, size)
    for w in ("then", "now"):
        g = meta["paper_gate"][w]
        fires = all(st and st["mean"] >= 195 and st["p10"] >= 140 and st["edge"] <= 0.20
                    and st["dark"] <= then_now.PAPER_DARK for st in (g["split"], g["alone"]))
        assert g["ok"] is fires
    assert {w for w in ("then", "now") if meta["paper_gate"][w]["ok"]} == EXPECTED_GATE[(name, fmt)]
    got = {w for w, m in meta["type_mode"].items() if m == "ink"}
    assert got == EXPECTED_INK[(name, fmt)], (meta["paper_gate"], meta["type_mode"])
    for w in got:                                                              # T1(b), planned on the split
        assert min(meta["planned_contrast"][f"{w}_kicker"], meta["planned_contrast"][f"{w}_year"]) >= 7.0
    for w in EXPECTED_GATE[(name, fmt)] - got:
        assert min(meta["paper_gate"][w]["ink_short"]) < 7.0
    if len(got) == 1:
        assert meta["type_mode"][({"then", "now"} - got).pop()] == "bone"     # T1(d)


# ---------------------------------------------------------------- T2: one halo per pair, a tight glow
def test_the_glow_fall_comes_from_the_year_cap():
    for size, key in ((PORTRAIT, "portrait"), (LANDSCAPE, "landscape")):
        assert then_now.band_fall(*size) == round(0.45 * then_now.year_cap(key)) - then_now.BAND_PAD
    assert then_now.band_fall(*PORTRAIT) == 38 and then_now.band_fall(*LANDSCAPE) == 31
    assert then_now.NOW_DROP == 96


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
def test_the_glow_stays_inside_its_reach(size):
    boxes = BOXES if size == PORTRAIT else [(790, 170, 930, 200), (700, 230, 930, 330)]
    key = "portrait" if size == PORTRAIT else "landscape"
    reach = round(0.45 * then_now.year_cap(key)) + 1
    a = then_now.glow_alpha(boxes, *size, 0.45)
    ys, xs = np.nonzero(a)
    assert xs.min() >= min(b[0] for b in boxes) - reach and xs.max() < max(b[2] for b in boxes) + reach
    assert ys.min() >= min(b[1] for b in boxes) - reach and ys.max() < max(b[3] for b in boxes) + reach


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
def test_one_halo_for_the_pair_and_a_capped_glow(tmp_path, monkeypatch, size):
    # Review round 2: on the real catalogue grounds (the Odessa map pair, ink type off so the bone path is planned);
    # synthetic bright linework now holds on the halo alone, so it no longer exercised the glow.
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)
    # Review round 3: on a bright ground the halo goes to 1.0 before a glow, so 16:9 Odessa holds on its halo; 16:9
    # uses the Gol Gumbaz stress pair with the planner's target raised to 5.2 (the harness's labelled forced-glow
    # variant) so the glow path is still exercised (was Odessa on both formats).
    if size == PORTRAIT:
        old, oy, new, ny = _real("odessa")
    else:
        old, oy, new, ny = _real("golgumbaz")
        monkeypatch.setattr(then_now, "PLACE_TARGET", 5.2)
    rows, got = _rows(monkeypatch, tmp_path, old, new, size, years=(oy, ny))
    meta = then_now.last_meta
    halos = [p.halo for n, p in rows.items() if meta["type_mode"][n.split("_")[0]] != "ink"]
    assert halos and max(halos) - min(halos) <= 0.01 and min(halos) >= 0.6      # T2(a)
    assert any(d is not None for d in meta["glow_depth"].values())             # (the glow path is exercised)
    for w in ("then", "now"):
        d, step = meta["glow_depth"][w], meta["glow_step"].get(w, "")
        if d is None:
            continue
        fallback = step.startswith(("glow+halo", "deep"))
        if meta["paper_gate"][w]["split"]["mean"] >= 200 and not fallback:
            assert d <= 0.45                                                   # T2(d)
        if not fallback:   # T2(2): the halo capped with a glow (1.0 on a bright ground first, review round 3)
            cap = 1.0 if meta["paper_gate"][w]["split"]["mean"] >= then_now.CLOUD_Y else 0.80
            assert meta["halo"] <= cap + 1e-9
    for n, p in rows.items():
        assert (p.contrast or 0) >= then_now.PLACE_TARGET - 1e-9, n               # T2(b), planned


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
def test_a_deep_glow_on_a_bright_ground_falls_smoothly(size):
    """Review round 3 (T3b, the handover's "smooth"): the fall lengthens where a short one would step more than
    GLOW_MAX_STEP Y a px; a shallow glow or a darker ground keeps band_fall (T2's short reach)."""
    boxes = BOXES if size == PORTRAIT else [(790, 170, 930, 200), (700, 230, 930, 330)]
    ink_y = 0.299 * then_now.GLOW_INK[0] + 0.587 * then_now.GLOW_INK[1] + 0.114 * then_now.GLOW_INK[2]
    x = (boxes[-1][0] + boxes[-1][2]) // 2
    bottom = max(b[3] for b in boxes)
    for ground in (150.0, 210.0, 254.0):
        for d in then_now.BAND_STEPS:
            fall = then_now.glow_fall(*size, d, ground)
            assert fall >= then_now.band_fall(*size)
            a = then_now.glow_alpha(boxes, *size, d, ground)
            y = ground - a[bottom:bottom + then_now.BAND_PAD + fall + 4, x].astype(np.float64) * (ground - ink_y)
            assert np.abs(np.diff(y)).max() <= then_now.GLOW_MAX_STEP + 0.1, (ground, d, fall)
            assert np.all(np.diff(y) >= -1e-6)                                     # monotone: it only lightens
    assert then_now.glow_fall(*size, 0.2, 150.0) == then_now.band_fall(*size)
    assert then_now.glow_fall(*size, 0.45, 250.0) > then_now.band_fall(*size)


# ---------------------------------------------------------------- T3: a float glow, a coarse dither, its own encode
def test_the_glow_alpha_is_float_end_to_end():
    a = then_now.glow_alpha(BOXES, 1080, 1920, 0.2)
    assert a.dtype == np.float32 and a.max() == pytest.approx(0.2)
    fall = a[512 + then_now.BAND_PAD:512 + then_now.BAND_PAD + then_now.BAND_FALL, 150]
    assert np.unique(fall).size >= then_now.BAND_FALL - 2                      # not 8-bit steps of 0.2 x 255 = 51
    m = then_now.band_mask("now", BOXES, 1080, 1920, 0.2, pos=0, as_float=True)
    assert m.dtype == np.float32 and np.array_equal(m, a)


def test_the_dither_is_2x2_cells_equal_on_three_channels():
    d = then_now._glow_dither(64, 48)
    assert d.shape == (48, 64, 1)
    assert np.array_equal(d[0::2, 0::2], d[1::2, 1::2]) and np.array_equal(d[0::2, 0::2], d[1::2, 0::2])
    peak = 1.75 * 255 / 219
    assert np.abs(d).max() <= peak and np.abs(d).max() > 0.8 * peak
    im = Image.new("RGB", (64, 48), (200, 200, 200))
    out = np.asarray(then_now._shade(im, np.full((48, 64), 0.3, np.float32))).astype(int)
    base = 0.7 * 200 + 0.3 * np.asarray(then_now.GLOW_INK, float)
    assert np.array_equal(np.round(out - base[None, None]).std(axis=2) < 1, np.ones((48, 64), bool))   # same offset
    flat = np.asarray(then_now._shade(im, np.zeros((48, 64), np.float32)))
    assert np.array_equal(flat, np.asarray(im))                                # T3(c): nothing where no glow


def test_the_clip_is_x264_grain_tuned_bt709_limited(tmp_path):
    import inspect
    import json as _json
    import subprocess
    frames = [Image.new("RGB", (64, 64), (200, 180, 160)) for _ in range(3)]
    out = then_now._encode(iter(frames), tmp_path / "c.mp4", 3, width=64, height=64)
    probe = _json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                       capture_output=True, text=True, check=True).stdout)["streams"][0]
    assert probe["codec_name"] == "h264" and probe["pix_fmt"] == "yuv420p"
    assert probe.get("color_space") == "bt709" and probe.get("color_primaries") == "bt709"
    assert probe.get("color_transfer") == "bt709" and probe.get("color_range") == "tv"
    assert (tmp_path / "c.jpg").exists()
    assert '"-preset", "slow", "-crf", "16", "-tune", "grain"' in inspect.getsource(then_now._encode)


# ---------------------------------------------------------------- T4: one landing and a linear push
@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
def test_the_divider_lands_once(size):
    w, h = size
    edge, split = max(size), then_now._split_at(w, h)
    assert all(then_now.divider_at(t, w, h) == edge for t in np.arange(0, 1.2, 0.01))
    for t in np.arange(2.2, 2.4501, 0.01):
        assert then_now.divider_at(t, w, h) == pytest.approx(384, abs=0.01)
    landed = 2.45 + then_now.settle_seconds(w, h)            # review round 3 (T4d): 3.3 s on 9:16, 3.4167 s on 16:9
    assert landed == pytest.approx(3.3 if size == PORTRAIT else 3.4167, abs=1e-3)
    assert then_now.divider_at(landed, w, h) == pytest.approx(split) and then_now.divider_at(5.9, w, h) == split
    pos = [then_now.divider_at(i / FPS, w, h) for i in range(180)]
    steps = np.diff(pos)
    signs = [np.sign(s) for s in steps if abs(s) > 1e-9]
    assert sum(1 for a, b in zip(signs, signs[1:]) if a != b) == 1           # T4(b): one change of direction
    hold = [s for i, s in enumerate(steps, 1) if 2.2 + 1e-9 < i / FPS <= 2.45 + 1e-9]
    assert hold and all(abs(s) < 1e-9 for s in hold)
    first_up = next(i for i, s in enumerate(steps, 1) if s > 1e-9)
    assert 2.45 < first_up / FPS <= 2.45 + 1.5 / FPS                          # ... at the start of the settle
    assert np.abs(steps).max() <= 105                                          # T4(c)
    settle = [abs(s) for i, s in enumerate(steps, 1) if 2.45 < i / FPS <= landed + 1e-9]
    # T4(d): <= 30 px on both formats; 16:9's settle is lengthened to 0.9667 s (was a 34 px allowance there)
    assert max(settle) <= 30


def test_the_push_is_linear():
    assert then_now.push_at(1.2) - 1 >= 0.0079 and then_now.push_at(then_now.CLIP_SECONDS) == pytest.approx(1.04)
    z = [then_now.push_at(i / FPS) for i in range(180)]
    d = np.diff(z)
    assert np.abs(d - d[0]).max() <= 1e-9


# ---------------------------------------------------------------- T5: the lock-up is never cut
def test_group_alpha_ramps_over_six_frames():
    clear = [True] * 20 + [False] * 5 + [True] * 20
    a = then_now.group_alpha(clear)
    assert all(a[i] == 0 for i in range(20, 25))
    assert a[13] == 1 and a[31] == 1 and all(0 < a[i] < 1 for i in list(range(14, 20)) + list(range(25, 31)))
    assert all(x >= y for x, y in zip(a[13:20], a[14:21])) and all(x <= y for x, y in zip(a[24:31], a[25:32]))


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE], ids=["9x16", "16x9"])
def test_the_group_schedule_on_a_render(tmp_path, monkeypatch, size):
    old, new = _dark_pair(tmp_path, size)
    meta = _plan_only(monkeypatch, tmp_path, old, 1925, new, 2019, size)
    for w in ("then", "now"):
        al, g = meta["schedule"][w], tuple(meta["groups"][w])
        for i in range(180):
            if not then_now.group_clear(g, w, then_now.divider_at(i / FPS, *size), *size):
                assert al[i] <= 0.02, (w, i)                                   # T5(a)
        assert all(v == 1.0 for v in al[105:])                                 # T5(c)
        runs, cur = [], 0
        for i in range(1, 180):
            if 0 < al[i] < 1:
                cur += 1
            elif cur:
                runs.append(cur)
                cur = 0
        assert all(r >= 6 for r in runs), (w, runs)                            # T5(d): no blink


# ---------------------------------------------------------------- T6: a 180-degree shutter on the seam
def test_the_seam_ramp_and_the_divider_energy():
    for pos, dpos in ((900.0, -100.0), (700.0, 22.0), (500.0, -9.0)):
        m = then_now.seam_weights(1920, pos, dpos).astype(float)
        xs = np.arange(1920) + 0.5
        order = np.argsort(m, kind="stable")
        width = abs(np.interp(0.9, m[order], xs[order]) - np.interp(0.1, m[order], xs[order]))
        assert width >= 0.4 * abs(dpos) - 0.5
        fb, fk = then_now.divider_profile(1920, pos, dpos)
        energy = fb.sum() + fk.sum() * then_now.KEYLINE_ALPHA / 255
        static = then_now.DIVIDER_W + 2 * then_now.KEYLINE_W * then_now.KEYLINE_ALPHA / 255
        assert energy == pytest.approx(static, rel=0.05)


def test_slow_frames_are_byte_identical_with_the_blur_off(tmp_path, monkeypatch):
    old, new = _noise(tmp_path / "a.png", 40, 220, seed=11), _noise(tmp_path / "b.png", 40, 220, seed=12)
    keep = tuple(range(110))
    on = _render(monkeypatch, tmp_path, old, new, PORTRAIT, keep=keep, stop=109)["frames"]
    monkeypatch.setattr(then_now, "MOTION_BLUR", False)
    off = _render(monkeypatch, tmp_path, old, new, PORTRAIT, keep=keep, stop=109)["frames"]
    pos = [then_now.divider_at(i / FPS, *PORTRAIT) for i in range(110)]
    fast = [i for i in range(1, 110) if abs(pos[i] - pos[i - 1]) > then_now.BLUR_MIN_STEP]
    assert fast
    for i in range(110):
        if i not in fast:
            assert np.array_equal(on[i], off[i]), i
    assert any(not np.array_equal(on[i], off[i]) for i in fast)


# ---------------------------------------------------------------- T7: scan borders and the 9:16 window
def test_scan_trims_take_a_mount_and_keep_a_sky():
    rng = np.random.default_rng(2)
    a = rng.integers(40, 200, (1000, 1400, 3), dtype=np.uint8)
    a[:, :30] = 230                               # a flat cream mount down the left edge
    a[:120, 30:] = 120                            # a flat sky filling the top band: the picture, kept
    t = then_now.scan_trims(Image.fromarray(a))
    assert t == (30, 0, 0, 0)


def test_a_registered_pair_keeps_one_window(tmp_path):
    rng = np.random.default_rng(3)
    a = np.full((3000, 4000, 3), 225, np.uint8)
    a[900:] = rng.integers(30, 200, (2100, 4000, 3), dtype=np.uint8)         # an empty sky over a busy lower 70%
    Image.fromarray(a).save(tmp_path / "o.png")
    Image.fromarray(a).save(tmp_path / "n.png")
    x, y, info = then_now.frame_pair(tmp_path / "o.png", tmp_path / "n.png", *PORTRAIT)
    assert info["gate"] == "THEN pane empty"
    assert np.array_equal(np.asarray(x), np.asarray(y))                        # one window, both photos (T7b)
    assert info["window"] == "shared" and 1.0 < info["zoom"] <= then_now.FRAME_Z_MAX
    assert info["source_px_per_frame_px"] >= 0.8                                # T7(c)
    assert 0.12 <= info["subject_row_share"] <= 0.15
    assert info["then_pane_edge_after"] >= 0.06 and info["now_pane_edge_after"] >= 0.06


def test_the_real_colossi_framing(tmp_path):
    old, oy, new, ny = _real("colossi")
    a, b, info = then_now.frame_pair(old, new, *LANDSCAPE)
    assert info["trims_px"]["old"][0] > 0 and info["trims_px"]["old"][1] > 0
    frame = np.asarray(then_now.pushed(a, then_now.CLIP_SECONDS, LANDSCAPE))
    y = _lum(frame)
    interior = y[:, 200:1700].mean()
    strip = y[:, 0:16]
    assert strip.std() >= 6 or abs(strip.mean() - interior) <= 12             # T7(d): no flat mount strip
    _, _, p = then_now.frame_pair(old, new, *PORTRAIT)
    assert p["gate"] == "THEN pane empty" and p["subject_top_share"] == pytest.approx(0.23, abs=0.02)
    # Review round 3 (T7a): the soft 1876 print never reaches edge density 0.06, so the subject-top window with
    # the most THEN structure is taken (it was the centred crop, the pane two-thirds sky).
    assert p["window"].startswith("shared (most THEN structure") and 1.0 < p["zoom"] <= p["z_max"]
    assert 0.12 <= p["subject_row_share"] <= 0.15
    assert p["then_pane_edge_after"] >= then_now.FRAME_BEST_GAIN * p["then_pane_edge"]
    assert p["now_pane_edge_after"] >= then_now.FRAME_EDGE_MIN and p["source_px_per_frame_px"] >= 0.8   # T7(c)


@pytest.mark.parametrize("name", ["pantheon", "odessa"])
def test_framings_without_border_or_empty_pane_are_unchanged(name):
    """T7(e). Review round 3: Odessa now_0_4 (a map on white) lost 126-169 px of its own white margin to the trim;
    a border the colour of the picture's own ground is kept now, so its framing is the plain cover crop again."""
    old, oy, new, ny = _real(name)
    for size in (PORTRAIT, LANDSCAPE):
        a, b, info = then_now.frame_pair(old, new, *size)
        big = (round(size[0] * 1.04), round(size[1] * 1.04))
        assert info["trim_share"] == [0, 0, 0, 0] and "gate" not in info
        assert np.array_equal(np.asarray(a), np.asarray(cover_crop(old, big).convert("RGB")))
        assert np.array_equal(np.asarray(b), np.asarray(cover_crop(new, big).convert("RGB")))


def test_a_white_margin_that_is_the_pictures_ground_is_kept():
    rng = np.random.default_rng(5)
    a = np.full((1000, 1400, 3), 252, np.uint8)            # a map on white: the margin is the sheet's own ground
    a[300:700, 400:1000] = rng.integers(40, 200, (400, 600, 3), dtype=np.uint8)
    assert then_now.scan_trims(Image.fromarray(a)) == (0, 0, 0, 0)


def test_the_gol_gumbaz_map_loses_only_its_paper_margin():
    """Review round 3 (T7e against T7(1)): Gol Gumbaz then_2's 17 px left and top are blank paper outside the
    map's printed frame, a print margin unlike the map inside it (4-21 % of the interior matches it), so T7(1)
    trims it; the builder keeps that and records it (T7(e) assumed no trim here)."""
    old, oy, new, ny = _real("golgumbaz")
    assert then_now.scan_trims(then_now._load_clean(old)) == (17, 17, 0, 0)


# ---------------------------------------------------------------- T7 fix round 1: a border nested in a border
def _rebate_lines(y, n=40):
    """Per edge (top, bottom, left, right): how many of the frame's outer `n` lines are flat black film rebate
    (std under 1 Y, mean under 14 Y: the reviewer's 'Y about 11, std 0.4' rows)."""
    def flat(lines):
        return sum(1 for v in lines if v.std() < 1.0 and v.mean() < 14)
    return flat(y[:n]), flat(y[-n:]), flat(y[:, :n].T), flat(y[:, -n:].T)


def test_the_real_aswan_rebate_comes_off(monkeypatch):
    """Review round 3 blocker: Aswan then_0 (a 5168 x 3866 negative scan) lost its white margin (147, 54, 130, 59)
    but kept the black film rebate inside it, with its '8075' handwriting down the left. Measured here: the rebate's
    inner edges are at 369 / 190 / 442 / 210 px (the picture starts at a step of 60+ Y left and top, the propeller's
    black on the right, the shadow's Y 17 against the rebate's 12 at the bottom). The nested pass takes it all, to
    within 12 px, and leaves no flat black line at any edge of either format's frame, at rest or pushed."""
    old, oy, new, ny = _real("aswan_rebate")
    im = then_now._load_clean(old)
    assert then_now._trim_pass(im)[0] == [147, 54, 130, 59]                    # the outer margin, as before
    t = then_now.scan_trims(im)
    for got, edge in zip(t, (369, 190, 442, 210)):
        assert edge - 12 <= got <= edge + 12, (t, edge)
    for size in (PORTRAIT, LANDSCAPE):
        a, b, info = then_now.frame_pair(old, new, *size)
        assert info["trims_px"]["old"] == list(t) and info["trims_px"]["new"] == [0, 0, 0, 0]
        for when in (0.0, then_now.CLIP_SECONDS):
            y = _lum(np.asarray(then_now.pushed(a, when, size)))
            assert _rebate_lines(y) == (0, 0, 0, 0), (size, when)
    monkeypatch.setattr(then_now, "TRIM_PASSES", 1)                            # one pass: the defect the review saw
    a, b, info = then_now.frame_pair(old, new, *PORTRAIT)
    y = _lum(np.asarray(then_now.pushed(a, 0.0, PORTRAIT)))
    assert _rebate_lines(y)[0] > 0 and _rebate_lines(y)[1] > 0


@pytest.mark.parametrize("name", ["pantheon", "colossi", "odessa", "golgumbaz", "aswan"])
def test_the_nested_pass_leaves_single_borders_alone(name):
    """The nested pass reads only inside a side the first pass trimmed, only lines flatter than film base, and only
    a level unlike the outer border's: on every other real input the trims are the single pass's (on the 182 run
    photos on this machine, only Aswan then_0 changed)."""
    old, oy, new, ny = _real(name)
    for path in (old, new):
        im = then_now._load_clean(path)
        assert list(then_now.scan_trims(im)) == then_now._trim_pass(im)[0], path


def test_a_rebate_with_handwriting_inside_a_white_margin_comes_off():
    rng = np.random.default_rng(8)
    a = rng.integers(60, 200, (1200, 1600, 3), dtype=np.uint8)              # the picture
    a[:, :110] = 12                                                           # a black film rebate down the left ...
    a[300:700, 30:80] = np.where(rng.random((400, 50, 1)) < 0.3, 230, 12)    # ... with white handwriting on it
    a[:, :40] = 252                                                           # inside a white print margin
    t = then_now.scan_trims(Image.fromarray(a))
    assert then_now._trim_pass(Image.fromarray(a))[0][0] == 40               # one pass: the margin only
    assert t[0] == 110 and t[1:] == (0, 0, 0)


def test_more_of_the_outer_border_level_is_not_a_nested_border():
    """A flat band the colour of the mount just inside it is the picture (a pale sky under a cream mount): the
    nested pass takes only a level unlike the outer border's (more than TRIM_GAP from it). The same band in black,
    a film rebate's level, does come off."""
    rng = np.random.default_rng(9)

    def ground(level):
        a = rng.integers(40, 200, (1000, 1400, 3), dtype=np.uint8)
        a[:30] = 236                                # a cream mount along the top
        a[46:76] = level                            # a flat band inside it, after 16 textured rows
        return Image.fromarray(a)
    pale = ground(234)
    assert then_now._trim_pass(pale)[0][1] == 30 and then_now.scan_trims(pale)[1] == 30
    black = ground(12)
    assert then_now._trim_pass(black)[0][1] == 30 and then_now.scan_trims(black)[1] == 76


# ---------------------------------------------------------------- T7 fix round 3: a sky split by noisy lines
def test_the_real_aswan_sky_is_not_a_mount(monkeypatch):
    """Fix round 3 blocker (step2 review2/aswan916_then_crop_before_left_after_right.png): Aswan then_2's top band
    is white sky (249.6-251.4 Y, std 1.3-6.2) read as four flat runs split by single noisy lines; the trim took the
    first three as a 'mount' and cut 203 px of real sky, dropping 9:16 THEN 1922 onto the rock ridge (19.0 -> 9.1:1).
    Runs of one level split by a line or two are one field now; the scanner edges right and bottom still come off."""
    old, oy, new, ny = _real("aswan")
    im = then_now._load_clean(old)
    assert then_now.scan_trims(im) == (0, 0, 19, 11)
    monkeypatch.setattr(then_now, "TRIM_JOIN_GAP", -1)                        # the round-2 read: the defect
    assert then_now.scan_trims(im) == (0, 203, 19, 11)


def test_a_real_mount_edge_into_a_sky_still_comes_off():
    """The join is only for a line or two inside one level: Colossi then_0's top mount (229.7 Y) meets its
    albumen sky (225.1 Y) over a 7-line edge, so its 132 px still come off (T7(d)), as do the other sides."""
    old, oy, new, ny = _real("colossi")
    assert then_now.scan_trims(then_now._load_clean(old)) == (96, 132, 167, 138)


def test_the_916_aswan_then_year_is_back_on_clean_sky(tmp_path, monkeypatch):
    """The regression gate against HEAD: 9:16 Aswan THEN 1922 planned 19.01:1 in ink at HEAD (no trim); with the
    sky kept it plans the same again (it was 9.12 with the 203 px trim), its kicker unchanged."""
    old, oy, new, ny = _real("aswan")
    meta = _plan_only(monkeypatch, tmp_path, old, oy, new, ny, PORTRAIT)
    assert meta["type_mode"]["then"] == "ink"
    assert meta["planned_contrast"]["then_year"] >= 19.0 and meta["planned_contrast"]["then_kicker"] >= 19.0


# ---------------------------------------------------------------- T8: LATER for an older "recent" photo
def test_the_recent_kicker_reads_now_or_later(monkeypatch):
    monkeypatch.setattr(then_now, "_utc_year", lambda: 2026)
    assert then_now.kicker_word("now", 2011) == "NOW" and then_now.kicker_word("now", 2001) == "NOW"
    assert then_now.kicker_word("now", 2000) == "LATER" and then_now.kicker_word("now", 1942) == "LATER"
    assert then_now.kicker_word("then", 1876) == "THEN"


@pytest.mark.parametrize("years,word", [((1876, 2011), "NOW"), ((1656, 1937), "LATER"), ((1941, 1942), "LATER")])
def test_render_draws_now_or_later(tmp_path, monkeypatch, years, word):
    monkeypatch.setattr(then_now, "_utc_year", lambda: 2026)
    old, new = _dark_pair(tmp_path, LANDSCAPE)
    got, plates = _plates_of(monkeypatch, tmp_path, old, new, LANDSCAPE, keep=(), stop=0)
    meta = _plan_only(monkeypatch, tmp_path, old, years[0], new, years[1], LANDSCAPE)
    assert meta["kicker"] == {"then": "THEN", "now": word}


def test_the_16x9_later_kicker_lines_up_with_its_year():
    k, y = _line_boxes("now", 1937, LANDSCAPE)
    assert abs(k[0] - y[0]) <= 1
    assert int(LANDSCAPE[0] * 0.92) - k[2] >= 64                               # clear of title-safe's right edge


# ---------------------------------------------------------------- T9: the 16:9 kicker
def _drawn_cap(text, font, track=0.0):
    from PIL import ImageDraw

    from src.typography import _draw_tracked
    im = Image.new("L", (900, 300), 0)
    _draw_tracked(ImageDraw.Draw(im), (40, 200), text, font, 255, "ls", track)
    return 200 - im.getbbox()[1]


def test_the_16x9_kicker_cap_and_the_gaps():
    kf = get_font(DATA_FONT, then_now.KICKER_PX["landscape"], weight=600)
    cap = _drawn_cap("THEN", kf, kf.size * then_now.KICKER_TRACK["landscape"])
    assert abs(cap - 32) <= 1 and cap / then_now.year_cap("landscape") >= 0.32
    assert _drawn_cap("THEN", get_font(DATA_FONT, then_now.KICKER_PX["portrait"], weight=600)) >= 34
    for size, gap in ((LANDSCAPE, 24), (PORTRAIT, 28)):
        for which, year in (("then", 1876), ("now", 2011), ("now", 1937)):
            k, y = _line_boxes(which, year, size)
            assert abs((y[1] - k[3]) - gap) <= 1, (size, which, k, y)
    k, y = _line_boxes("then", 1876, LANDSCAPE)
    assert abs(k[2] - y[2]) <= 1 and abs(k[2] - 896) <= 1                      # T9(c): both end on the marker's x


# ---------------------------------------------------------------- T10: the 9:16 markers' side
def _half_busy(path, busy_left, size=(1200, 2100), seed=9):
    rng = np.random.default_rng(seed)
    w, h = size
    a = np.full((h, w, 3), 70, np.uint8)
    noise = rng.integers(0, 255, (h, w // 2, 3), dtype=np.uint8)
    if busy_left:
        a[:, :w // 2] = noise
    else:
        a[:, w // 2:] = noise
    Image.fromarray(a).save(path)
    return path


@pytest.mark.parametrize("busy_left,side", [(True, "right"), (False, "left")])
def test_the_916_markers_take_the_quieter_side(tmp_path, monkeypatch, busy_left, side):
    old = _half_busy(tmp_path / "o.png", busy_left)
    new = _half_busy(tmp_path / "n.png", busy_left, seed=10)
    got, plates = _plates_of(monkeypatch, tmp_path, old, new, PORTRAIT, keep=(), stop=0)
    meta = then_now.last_meta
    assert meta["side"]["side"] == side
    sums = meta["side"]["edge_sum"]
    assert sums[side] <= sums["left" if side == "right" else "right"]
    x0, y0, x1, y1 = safe_zones.safe_box("reel", *PORTRAIT)
    for t in ("THEN", "1925", meta["kicker"]["now"], "2019"):
        b = plates[t].box
        assert x0 <= b[0] and b[2] <= x1 and y0 <= b[1] and b[3] <= y1, t
    if side == "right":
        assert abs(plates["THEN"].box[2] - plates["1925"].box[2]) <= 1
        assert abs(plates[meta["kicker"]["now"]].box[2] - plates["2019"].box[2]) <= 1
