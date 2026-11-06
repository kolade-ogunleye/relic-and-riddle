"""QF-09: untrusted picture downloads are read under a byte cap, and decoding our own large rasters never lifts
Pillow's process-wide decompression-bomb limit (a Commons photo decoded on another thread in that window went
unchecked). Every input is generated here."""
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFile

from scripts import fetch_relief
from src import commons, config, geo, graphics, upscale, url_guard

ROOT = Path(__file__).resolve().parent.parent

LIMIT = 10_000  # a small global limit for the test: each trusted raster below is over twice it


@pytest.fixture
def seen_limits(monkeypatch):
    """The global limit at every open and decode while the test runs."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", LIMIT)
    seen = []
    real_open, real_load = Image.open, ImageFile.ImageFile.load

    def spy_open(*a, **k):
        seen.append(Image.MAX_IMAGE_PIXELS)
        return real_open(*a, **k)

    def spy_load(self, *a, **k):
        seen.append(Image.MAX_IMAGE_PIXELS)
        return real_load(self, *a, **k)
    monkeypatch.setattr(Image, "open", spy_open)
    monkeypatch.setattr(ImageFile.ImageFile, "load", spy_load)
    return seen


def _relief_tif(path: Path, size=(200, 120)) -> Path:
    w, h = size
    ramp = (np.arange(w, dtype=np.uint16)[None, :] * 255 // (w - 1)).astype(np.uint8)
    Image.fromarray(np.repeat(ramp, h, axis=0), "L").save(path)
    return path


def _jpeg(size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 80, 40)).save(buf, "JPEG")
    return buf.getvalue()


class _Stream:
    """A streamed requests response: records how much of the body was read."""

    def __init__(self, body: bytes, headers=None):
        self.body, self.headers, self.status_code, self.read = body, headers or {}, 200, 0
        self.closed = False

    def raise_for_status(self):
        pass

    def json(self):
        return {}

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.body), chunk_size):
            self.read += len(self.body[i:i + chunk_size])
            yield self.body[i:i + chunk_size]

    @property
    def content(self):
        return b"".join(self.iter_content(1 << 16))

    def close(self):
        self.closed = True


@pytest.fixture
def commons_get(monkeypatch):
    monkeypatch.setattr(commons.time, "sleep", lambda s: None)
    monkeypatch.setattr(url_guard, "_addresses", lambda host: ["1.1.1.1"])
    sent = []

    def install(resp):
        def get(url, params=None, headers=None, timeout=None, **k):
            sent.append(k)
            return resp
        monkeypatch.setattr(commons.requests, "get", get)
        return sent
    return install


def test_the_relief_is_decoded_without_lifting_the_global_limit(tmp_path, monkeypatch, seen_limits):
    tif = _relief_tif(tmp_path / "SR_HR.tif")
    monkeypatch.setattr(geo, "RELIEF_FILE", tif)
    graphics._relief.cache_clear()
    try:
        relief = graphics._relief()
    finally:
        graphics._relief.cache_clear()
    assert relief is not None and relief.size == (200, 120) and relief.mode == "L"
    assert seen_limits and set(seen_limits) == {LIMIT}
    assert Image.MAX_IMAGE_PIXELS == LIMIT


def test_a_4x_upscale_is_decoded_without_lifting_the_global_limit(tmp_path, monkeypatch, seen_limits):
    src = tmp_path / "still.png"
    Image.new("RGB", (40, 50), (10, 20, 30)).save(src)   # 4x: 32,000 px, over twice LIMIT
    binary = tmp_path / "realesrgan-ncnn-vulkan.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(upscale, "REALESRGAN_BIN", str(binary))

    def fake_run(args, **k):
        out = Path(args[args.index("-o") + 1])
        with Image.open(args[args.index("-i") + 1]) as im:
            im.resize((im.width * 4, im.height * 4)).save(out)
    monkeypatch.setattr(upscale.subprocess, "run", fake_run)
    out = upscale._upscale_ncnn(src, tmp_path / "up.png")
    assert out.size == (160, 200)
    assert seen_limits and set(seen_limits) == {LIMIT}
    assert Image.MAX_IMAGE_PIXELS == LIMIT


def test_a_fetched_relief_is_checked_without_lifting_the_global_limit(tmp_path, seen_limits):
    tif = _relief_tif(tmp_path / "SR_50M.tif", (150, 90))
    assert fetch_relief.valid_relief(tif, (150, 90))
    assert seen_limits and set(seen_limits) == {LIMIT}
    assert Image.MAX_IMAGE_PIXELS == LIMIT


def test_a_download_is_decoded_under_the_global_limit(tmp_path, commons_get, seen_limits):
    commons_get(_Stream(_jpeg()))
    commons.download_jpeg("https://upload.wikimedia.org/a.jpg", tmp_path / "a.jpg")
    assert Image.open(tmp_path / "a.jpg").size == (60, 40)
    assert seen_limits and set(seen_limits) == {LIMIT}


def test_a_download_past_the_pixel_limit_is_still_refused(tmp_path, commons_get, seen_limits):
    commons_get(_Stream(_jpeg((200, 120))))                         # 24,000 px: over twice LIMIT
    with pytest.raises(Image.DecompressionBombError):
        commons.download_jpeg("https://upload.wikimedia.org/b.jpg", tmp_path / "b.jpg")
    assert not (tmp_path / "b.jpg").exists()


def test_a_download_is_streamed_and_refused_past_the_byte_cap(tmp_path, monkeypatch, commons_get):
    monkeypatch.setattr(config, "COMMONS_DOWNLOAD_MAX_MB", 1, raising=False)
    cap = 1024 * 1024
    big = _Stream(_jpeg() + b"\0" * (3 * cap))                          # a valid JPEG, padded to over 3 MB
    sent = commons_get(big)
    with pytest.raises(ValueError, match="too large"):
        commons.download_jpeg("https://upload.wikimedia.org/c.jpg", tmp_path / "c.jpg")
    assert sent == [{"allow_redirects": False, "stream": True}]        # each redirect is checked before its request
    assert big.read <= cap + (1 << 16)                                 # reading stopped at the cap
    assert not (tmp_path / "c.jpg").exists()


def test_a_download_declared_past_the_byte_cap_is_not_read(tmp_path, monkeypatch, commons_get):
    monkeypatch.setattr(config, "COMMONS_DOWNLOAD_MAX_MB", 1, raising=False)
    declared = _Stream(_jpeg(), headers={"Content-Length": str(5 * 1024 * 1024)})
    commons_get(declared)
    with pytest.raises(ValueError, match="too large"):
        commons.download_jpeg("https://upload.wikimedia.org/d.jpg", tmp_path / "d.jpg")
    assert declared.read == 0
    assert declared.closed
    assert not (tmp_path / "d.jpg").exists()


def test_a_download_under_the_byte_cap_is_kept(tmp_path, monkeypatch, commons_get):
    monkeypatch.setattr(config, "COMMONS_DOWNLOAD_MAX_MB", 1, raising=False)
    commons_get(_Stream(_jpeg() + b"\0" * 1000))
    commons.download_jpeg("https://upload.wikimedia.org/e.jpg", tmp_path / "e.jpg")
    assert Image.open(tmp_path / "e.jpg").size == (60, 40)


def test_the_byte_cap_is_a_documented_setting():
    assert config.COMMONS_DOWNLOAD_MAX_MB == 50 or "COMMONS_DOWNLOAD_MAX_MB" in __import__("os").environ
    assert "COMMONS_DOWNLOAD_MAX_MB=" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_json_download_bodies_cannot_bypass_the_stream_cap(tmp_path, monkeypatch, commons_get):
    monkeypatch.setattr(config, "COMMONS_DOWNLOAD_MAX_MB", 1)
    response = _Stream(b"x" * (3 * 1024 * 1024), headers={"Content-Type": "application/json"})
    response.json = lambda: pytest.fail("download body read before the cap")
    commons_get(response)
    with pytest.raises(ValueError, match="too large"):
        commons.download_jpeg("https://upload.wikimedia.org/not-an-image.json", tmp_path / "image.jpg")
    assert response.read <= 1024 * 1024 + (1 << 16)
    assert response.closed
