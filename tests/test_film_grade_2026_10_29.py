"""The master's film grade keeps the print's tone but never the print's colour (29 Oct, Derinkuyu).

The full Kodak 2383 LUT turned a stone door that colour_balance had brought from 70/71/31 to 85/84/45 into
79/76/26 (blue crushed, the cast more than doubled): neon lime in the master. The grade now takes the LUT's grey
response only (docs/reviews/film-grade-2026-10-29)."""

import inspect
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from src import colour_balance
from src import video_composer as vc

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _cast(a):
    n = colour_balance.scene(a)["neutral"]
    assert n is not None
    return float(np.max(np.abs(n.mean() / n - 1)))


def _yellow_wall(path, seed=1):
    """A lamp-lit grey stone wall: textured mid-grey under a yellow-green lamp (Derinkuyu's balanced door)."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:240, 0:320]
    stone = 0.75 + 0.5 * (x / 320) * (y / 240) + rng.normal(0, 0.06, (240, 320))
    a = np.clip(np.stack([85 * stone, 84 * stone, 45 * stone], axis=-1), 0, 255)
    Image.fromarray(a.astype(np.uint8), "RGB").save(path)


def _grade(src, chain, dst):
    vf = f"scale=out_range=tv,format=yuv420p,format=yuv420p,format=gbrp16le,{chain},format=yuv420p"   # QF-02: 16-bit
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", str(dst)], check=True)
    return np.asarray(Image.open(dst).convert("RGB"), dtype=np.float64)


def _old_chain():
    src = str(vc.FILM_PRINT_LUT.resolve()).replace("\\", "/").replace(":", "\\:")
    return f"{vc.FILM_PRE_CURVE},lut3d='{src}'"


def test_the_table_keeps_greys_grey_and_hue(tmp_path):
    lut = vc._read_cube(vc.FILM_PRINT_LUT)
    t = vc.hue_safe_print_table(lut)
    n = t.shape[0]
    diag = np.array([t[i, i, i] for i in range(n)])
    assert np.abs(diag - diag.mean(axis=1, keepdims=True)).max() < 1e-6          # every grey stays grey
    tone = np.array([lut[i, i, i] for i in range(n)]).mean(axis=1)
    assert np.abs(diag[:, 0] - tone).max() < 1e-6                                 # at the print's own contrast
    v = np.linspace(0, 1, n)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    x = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    out = t.reshape(-1, 3)
    hue_in, sat_in = colour_balance._hsv(x * 255)
    hue_out, sat_out = colour_balance._hsv(out * 255)
    coloured = (sat_in > 0.15) & (x.max(axis=1) > 0.15) & (out.max(axis=1) > 0.05)
    dh = np.abs((hue_out - hue_in + 180) % 360 - 180)[coloured]
    assert dh.max() < 0.5                                                         # no hue moved
    assert (sat_out[coloured] / sat_in[coloured]).max() <= vc.FILM_TONE_CHROMA + 0.06   # saturation bounded


@needs_ffmpeg
def test_a_lamp_lit_wall_keeps_its_blue_and_gains_no_cast(tmp_path):
    src = tmp_path / "wall.png"
    _yellow_wall(src)
    ref = _grade(src, "null", tmp_path / "ref.png")
    new = _grade(src, vc.film_grade_filter(0.0, out_dir=tmp_path / "luts"), tmp_path / "new.png")
    old = _grade(src, _old_chain(), tmp_path / "old.png")
    bg = lambda a: a[..., 2].mean() / a[..., 1].mean()
    # the old print crushed blue against green and doubled the cast: what this test must catch
    assert bg(old) < 0.80 * bg(ref) and _cast(old) > 1.5 * _cast(ref)
    # QF-02 (blacks at video black): without the stacked black lifts' neutral veil the dark wall keeps a little less
    # blue against green (0.958 of the reference in the 16-bit chain; 0.97 was measured with the lifts)
    assert bg(new) >= 0.95 * bg(ref)
    assert _cast(new) <= _cast(ref) * 1.04 + 0.01
    hue = lambda a: colour_balance._hue(a.reshape(-1, 3).mean(axis=0))
    assert abs(hue(new) - hue(ref)) < 2.0
    contrast = lambda a: colour_balance._luma(a).std() / colour_balance._luma(a).mean()
    assert contrast(new) >= contrast(ref) and contrast(new) >= 0.95 * contrast(old)   # the print's contrast kept


@needs_ffmpeg
def test_a_neutral_grey_stays_neutral(tmp_path):
    ramp = np.tile(np.linspace(8, 247, 256)[None, :, None], (64, 1, 3)).astype(np.uint8)
    src = tmp_path / "grey.png"
    Image.fromarray(ramp, "RGB").save(src)
    new = _grade(src, vc.film_grade_filter(0.0, out_dir=tmp_path / "luts"), tmp_path / "new.png")
    spread = new.max(axis=-1) - new.min(axis=-1)
    assert spread.max() <= 3                                                      # 8-bit levels, yuv rounding
    old = _grade(src, _old_chain(), tmp_path / "old.png")
    assert (old[..., 0] - old[..., 2]).mean() > 4                                 # the old print warmed greys


def test_the_render_uses_the_hue_safe_grade(tmp_path):
    source = inspect.getsource(vc.VideoComposer.compose_video)
    assert "film_grade = film_grade_filter(grade_bias)" in source
    assert "[video_raw]format=yuv420p,{film_grade},format=yuv420p," in source
    assert "kodak_2383.cube" not in source and "lut3d" not in source             # never the raw print LUT
    chain = vc.film_grade_filter(0.0, out_dir=tmp_path)
    assert chain.startswith(vc.FILM_PRE_CURVE + ",lut3d='") and "_tone_" in chain
    assert "colorbalance" not in chain
    warm = vc.film_grade_filter(0.2, out_dir=tmp_path)                            # the bias stays bounded
    assert warm.endswith(",colorbalance=rs=0.050:bs=-0.050:rm=0.025:bm=-0.025")


def test_the_table_is_cached_with_lf_line_endings(tmp_path):
    a = vc.hue_safe_print_lut(out_dir=tmp_path)
    b = vc.hue_safe_print_lut(out_dir=tmp_path)
    assert a == b and len(list(tmp_path.iterdir())) == 1
    data = a.read_bytes()
    assert b"\r\n" not in data and data.startswith(b'TITLE "kodak_2383 tone only')
    assert vc._read_cube(a).shape == vc._read_cube(vc.FILM_PRINT_LUT).shape


def test_a_missing_print_lut_falls_back_to_a_neutral_contrast(tmp_path):
    chain = vc.film_grade_filter(0.0, source=tmp_path / "missing.cube", out_dir=tmp_path)
    assert "lut3d" not in chain and ":red=" not in chain and ":blue=" not in chain
    assert "saturation=1.03" in chain
