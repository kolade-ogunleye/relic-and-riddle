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
