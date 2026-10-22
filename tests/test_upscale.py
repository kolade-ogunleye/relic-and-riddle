from pathlib import Path

from PIL import Image

from src import upscale


def _img(tmp_path, name, size):
    p = tmp_path / name
    Image.new("RGB", size, (90, 80, 70)).save(p)
    return p


def test_vertical_crop_width():
    assert upscale.vertical_crop_width((1280, 960)) == 540      # landscape: heavy crop
    assert upscale.vertical_crop_width((3600, 2400)) == 1350
    assert upscale.vertical_crop_width((1080, 1920)) == 1080    # already 9:16


def test_only_low_res_stills_need_upscaling(tmp_path):
    assert upscale.needs_upscale(_img(tmp_path, "small.jpg", (1280, 960)))
    assert not upscale.needs_upscale(_img(tmp_path, "big.jpg", (4000, 3000)))
    assert not upscale.needs_upscale(tmp_path / "hook_motion.mp4")


def test_missing_tool_falls_back_to_original(tmp_path, monkeypatch):
    monkeypatch.setattr(upscale, "REALESRGAN_BIN", str(tmp_path / "missing.exe"))
    monkeypatch.setattr(upscale, "CACHE_DIR", tmp_path / "cache")
    p = _img(tmp_path, "small.jpg", (1280, 960))
    assert upscale.ensure_resolution(p) == p


def test_upscaled_result_is_cached_and_bounded(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        out = Path(args[args.index("-o") + 1])
        Image.new("RGB", (5120, 3840)).save(out)  # 4x of 1280x960

    binary = tmp_path / "realesrgan.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(upscale, "REALESRGAN_BIN", str(binary))
    monkeypatch.setattr(upscale, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(upscale.subprocess, "run", fake_run)
    p = _img(tmp_path, "small.jpg", (1280, 960))
    out = upscale.ensure_resolution(p)
    assert out != p and out.exists()
    with Image.open(out) as im:
        assert upscale.vertical_crop_width(im.size) == upscale.TARGET_CROP_WIDTH  # trimmed to what the video uses
    assert upscale.ensure_resolution(p) == out and len(calls) == 1               # cached
    paths, low = upscale.ensure_all([p, _img(tmp_path, "big.jpg", (4000, 3000))])
    assert low == 1 and paths[1].name == "big.jpg"


def _fake_4x(tile):
    # Nearest-neighbour 4x: lets the tiling be checked exactly against a whole-image upscale.
    import numpy as np
    return tile.repeat(4, axis=2).repeat(4, axis=3)


def test_tiled_upscale_matches_a_whole_image_upscale_at_awkward_sizes():
    import numpy as np
    rng = np.random.default_rng(1)
    for h, w in ((37, 53), (192, 192), (400, 250)):          # smaller than, equal to and larger than a tile
        rgb = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        out = upscale.upscale_array(rgb, run=_fake_4x)
        assert out.shape == (h * 4, w * 4, 3)
        assert np.array_equal(out, rgb.repeat(4, axis=0).repeat(4, axis=1))   # no seams, no padding leaks


def test_onnx_is_preferred_then_vulkan_then_plain(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"x")
    binary = tmp_path / "realesrgan.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(upscale, "ONNX_MODEL", model)
    monkeypatch.setattr(upscale, "REALESRGAN_BIN", str(binary))
    monkeypatch.setattr(upscale, "CACHE_DIR", tmp_path / "cache")
    used = []
    monkeypatch.setattr(upscale, "_upscale_onnx", lambda p: used.append("onnx") or Image.new("RGB", (5120, 3840)))
    out = upscale.ensure_resolution(_img(tmp_path, "a.jpg", (1280, 960)))
    assert used == ["onnx"] and out.exists()
    assert upscale.backend() == "onnx"

    def broken(p):                                            # e.g. no GPU device: fall through
        raise RuntimeError("no device")

    def fake_run(args, **kw):
        used.append("ncnn")
        Image.new("RGB", (5120, 3840)).save(Path(args[args.index("-o") + 1]))
    monkeypatch.setattr(upscale, "_upscale_onnx", broken)
    monkeypatch.setattr(upscale.subprocess, "run", fake_run)
    out = upscale.ensure_resolution(_img(tmp_path, "b.jpg", (1280, 960)))
    assert used[-1] == "ncnn" and out.exists()
    monkeypatch.setattr(upscale.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("vulkan")))
    p = _img(tmp_path, "c.jpg", (1280, 960))
    assert upscale.ensure_resolution(p) == p                  # nothing works: the original, plainly resized


def test_lineage_records_the_backend(tmp_path, monkeypatch):
    import json
    from src.lineage import sidecar
    model = tmp_path / "model.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(upscale, "ONNX_MODEL", model)
    monkeypatch.setattr(upscale, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(upscale, "_upscale_onnx", lambda p: Image.new("RGB", (5120, 3840)))
    out = upscale.ensure_resolution(_img(tmp_path, "d.jpg", (1280, 960)))
    assert json.loads(sidecar(out).read_text(encoding="utf-8"))["settings"]["backend"] == "onnx"
