"""QF-86: neutral photo curves retain colour, local contrast and black-zero endpoints."""

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
    vf = f"format=gbrp16le,{chain},format=rgb24"  # QF-86: real photo branch, before YUV conversion
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", str(dst)], check=True)
    return np.asarray(Image.open(dst).convert("RGB"), dtype=np.float64)


def test_the_photo_curve_keeps_greys_grey_and_endpoints(tmp_path):
    ramp = np.tile(np.arange(256)[None, :, None], (64, 1, 3)).astype(np.uint8)
    src = tmp_path / "ramp.png"
    Image.fromarray(ramp).save(src)
    out = _grade(src, vc.film_grade_filter(), tmp_path / "out.png")
    assert (out.max(axis=-1) - out.min(axis=-1)).max() <= 3
    assert out[:, 0].max() <= 1
    assert out[:, -1].min() >= 254


@needs_ffmpeg
def test_a_lamp_lit_wall_keeps_its_blue_and_gains_no_cast(tmp_path):
    src = tmp_path / "wall.png"
    _yellow_wall(src)
    ref = _grade(src, "null", tmp_path / "ref.png")
    new = _grade(src, vc.film_grade_filter(0.0, out_dir=tmp_path / "luts"), tmp_path / "new.png")
    bg = lambda a: a[..., 2].mean() / a[..., 1].mean()
    # QF-02 (blacks at video black): without the stacked black lifts' neutral veil the dark wall keeps a little less
    # blue against green (0.958 of the reference in the 16-bit chain; 0.97 was measured with the lifts)
    assert bg(new) >= 0.95 * bg(ref)
    assert _cast(new) <= _cast(ref) * 1.04 + 0.01
    hue = lambda a: colour_balance._hue(a.reshape(-1, 3).mean(axis=0))
    assert abs(hue(new) - hue(ref)) < 2.0
    contrast = lambda a: colour_balance._luma(a).std() / colour_balance._luma(a).mean()
    assert contrast(new) >= contrast(ref)  # retain local photographic contrast


@needs_ffmpeg
def test_a_neutral_grey_stays_neutral(tmp_path):
    ramp = np.tile(np.linspace(8, 247, 256)[None, :, None], (64, 1, 3)).astype(np.uint8)
    src = tmp_path / "grey.png"
    Image.fromarray(ramp, "RGB").save(src)
    new = _grade(src, vc.film_grade_filter(0.0, out_dir=tmp_path / "luts"), tmp_path / "new.png")
    spread = new.max(axis=-1) - new.min(axis=-1)
    assert spread.max() <= 3                                                      # 8-bit levels, yuv rounding


def test_the_render_uses_the_hue_safe_grade(tmp_path):
    source = inspect.getsource(vc.VideoComposer.compose_video)
    assert "photo_grade = film_grade_filter(grade_bias)" in source
    assert "[video_raw]format=yuv420p,{film_grade},format=yuv420p," in source
    assert "kodak_2383.cube" not in source and "lut3d" not in source             # never the raw print LUT
    chain = vc.film_grade_filter(0.0, out_dir=tmp_path)
    assert chain == vc.PHOTO_CURVE and "lut3d" not in chain
    assert "colorbalance" not in chain
    warm = vc.film_grade_filter(0.2, out_dir=tmp_path)                            # the bias stays bounded
    assert warm.endswith(":red='0/0 0.5/0.525 1/1':blue='0/0 0.5/0.475 1/1'")


def test_the_curve_is_deterministic_without_a_cached_table(tmp_path):
    a = vc.film_grade_filter(out_dir=tmp_path)
    b = vc.film_grade_filter(out_dir=tmp_path)
    assert a == b and not list(tmp_path.iterdir())


def test_a_missing_print_lut_does_not_change_the_photo_curve(tmp_path):
    chain = vc.film_grade_filter(0.0, source=tmp_path / "missing.cube", out_dir=tmp_path)
    assert "lut3d" not in chain and ":red=" not in chain and ":blue=" not in chain
    assert chain == vc.PHOTO_CURVE
