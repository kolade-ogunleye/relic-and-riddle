"""Freeze lane E, cycle 4 (16:9 end screen; panel 5 CD-r9-endscreen-rim, MO-es-keyframe).

- A matte on the end screen's ground (an essential image shown whole on fit_filter's black, a vignette crushed to
  digital black) is found by its geometry and its black; a photographed black ground (an object on black, a dark
  wall) is not a matte.
- Where there is a matte, the end screen extends its INK field over it: the window's edge eases at the band-edge
  slope (edge_feather), in with the dim from the last frame as it is; the record says so. No matte, no record.
- The end screen is encoded as ONE group of pictures by the delivery encoder: no I-frame pulse in its still tail.
Invariants only: never pinned pictures."""
from __future__ import annotations

import shutil
import subprocess
import sys
from itertools import islice
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import end_card
from src.brand_system import tok
from src.image_presentation import fit_image

W, H = end_card.LANDSCAPE_WIDTH, end_card.LANDSCAPE_HEIGHT
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _picture(size, level=210, seed=0):
    """A bright, textured picture (a photograph's stand-in)."""
    rng = np.random.default_rng(seed)
    w, h = size
    im = Image.new("RGB", size, (level, level - 8, level - 24))
    d = ImageDraw.Draw(im)
    for x in range(-h, w, 90):
        d.line([(x, 0), (x + h, h)], fill=(level - 50, level - 60, level - 80), width=5)
    a = np.asarray(im, np.float32) + rng.normal(0, 3, (h, w, 3))
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def _grain(im, sd=4.0, seed=1):
    a = np.asarray(im, np.float32) + np.random.default_rng(seed).normal(0, sd, (im.height, im.width, 3))
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def _pillarbox(grain=False):
    im = fit_image(_picture((900, 1080)), (W, H)).convert("RGB")
    return _grain(im) if grain else im


@pytest.mark.parametrize("make", [
    lambda: _pillarbox(),
    lambda: _pillarbox(grain=True),
    lambda: fit_image(_picture((1920, 700)), (W, H)).convert("RGB"),           # letterbox
    lambda: fit_image(_picture((800, 600)), (W, H)).convert("RGB"),            # a small image at its native size
], ids=["pillarbox", "pillarbox_grain", "letterbox", "small_native"])
def test_an_image_shown_whole_sits_on_a_matte(make):
    found = end_card.matte_window(make())
    assert found is not None
    dist, rec = found
    assert rec["window"] == "rectangle"
    assert dist.shape == (H, W)
    assert float((dist == 0).mean()) == pytest.approx(rec["share"], abs=0.02)


def _ellipse_window(black=0, noise=0.0, seed=2):
    pic = np.asarray(_picture((W, H)), np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    inside = ((xx - W / 2) / (W * 0.52)) ** 2 + ((yy - H / 2) / (H * 0.62)) ** 2 <= 1
    ground = np.full((H, W, 3), float(black), np.float32) + np.random.default_rng(seed).normal(0, noise, (H, W, 3))
    out = np.where(inside[..., None], pic, ground)
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def test_a_vignette_crushed_to_digital_black_is_a_matte():
    found = end_card.matte_window(_ellipse_window(black=0))
    assert found is not None and found[1]["window"] == "hull" and found[1]["spans"]


@pytest.mark.parametrize("make", [
    lambda: _ellipse_window(black=9, noise=2.0),                       # the same window on PHOTOGRAPHED black
    lambda: _picture((W, H)),                                          # no matte at all
    lambda: Image.new("RGB", (W, H), (32, 30, 28)),                    # a dark, flat ground with no window
], ids=["photographed_black", "plain_picture", "dark_flat"])
def test_a_photographed_ground_is_not_a_matte(make):
    assert end_card.matte_window(make()) is None


def test_an_object_on_black_is_not_a_matte():
    im = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(im).ellipse((760, 340, 1160, 740), fill=(180, 150, 90))   # a coin on black: no frame edge
    assert end_card.matte_window(im) is None


def _end_screen(ground, monkeypatch, n=None):
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 2.0)
    stats: dict = {}
    frames = list(islice(end_card.frames(ground, "Send this to a friend.", stats), n) if n else
                  end_card.frames(ground, "Send this to a friend.", stats))
    return frames, stats


def test_the_end_screen_takes_the_field_only_ground_over_a_matte(monkeypatch):
    # freeze cycle 5 (PHOTO-02): lane E cycle 4 extended an INK field over the matte with a feathered edge, and the
    # edge still showed (the Richat oval). A last frame inside a frame of its own is no ground: the whole frame eases
    # to INK with the dim (the field-only ground), recorded with its reason.
    ground = _pillarbox()
    frames, stats = _end_screen(ground, monkeypatch)
    rec = stats["matte"]
    assert rec and rec["window"] == "rectangle" and rec["ground"] == "field only" and "rule:" in rec["reason"]
    # frame 0 is the narration's last frame as it is: the field comes in with the dim
    assert float(np.abs(np.asarray(frames[0], np.float32) - np.asarray(ground, np.float32)).mean()) < 0.5
    rest = np.asarray(frames[-1], np.float32)
    ink = np.asarray(end_card.brand.INK, np.float32)
    # at rest the right side (under no type) is INK edge to edge: no window, no edge of the source
    assert np.abs(rest[:, 1500:] - ink).max() < 6
    from src.band_edge import steepest
    assert steepest((rest @ LUMA)[100:250, 1300:].mean(axis=0)) <= tok("edge.slope_max") * 1080 / W * 1.25


def test_a_ground_without_a_matte_records_none(monkeypatch):
    frames, stats = _end_screen(_picture((W, H)), monkeypatch, n=2)
    assert "matte" in stats and stats["matte"] is None


def test_the_end_screen_encoder_is_the_delivery_encoder_as_one_gop():
    from src.encoding import delivery_video_encoder
    args = end_card.end_screen_encoder()
    base = delivery_video_encoder()
    assert args[:len(base)] == base
    g = int(args[args.index("-g") + 1])
    assert g >= round(end_card.END_SCREEN_SECONDS * end_card.FPS)
    assert args[args.index("-sc_threshold") + 1] == "0"
    assert "-tune" not in args and "h264_nvenc" not in args


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_a_still_end_screen_encodes_with_one_i_frame(tmp_path):
    n = round(end_card.END_SCREEN_SECONDS * end_card.FPS)
    out = tmp_path / "es.mp4"
    frame = np.full((36, 64, 3), 40, np.uint8).tobytes()
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x36", "-r",
                          str(end_card.FPS), "-i", "-", *end_card.end_screen_encoder(), "-pix_fmt", "yuv420p",
                          str(out)], stdin=subprocess.PIPE)
    p.stdin.write(frame * n)
    p.stdin.close()
    assert p.wait(timeout=120) == 0
    types = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pict_type",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True, timeout=60).stdout.split()
    assert len(types) == n
    assert [i for i, t in enumerate(types) if t.startswith("I")] == [0]


def test_the_board_encodes_the_end_screen_with_its_own_encoder(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parent.parent / "docs" / "reviews" / "freeze-system-2026-11-15"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import board_evidence as be
    import board_motion as bm
    seen = {}
    monkeypatch.setattr(be, "guarded", lambda surface, fn, *a, **k: seen.setdefault(a[1].name, k.get("codec")))
    bm._encode_end("es", lambda st: iter(()), {}, tmp_path / "endscreen_169__0_x.mp4", (W, H))
    bm._encode_end("ec", lambda st: iter(()), {}, tmp_path / "endcard_916__0_x.mp4", (1080, 1920))
    assert seen["endscreen_169__0_x.mp4"] == end_card.end_screen_encoder()
    assert seen["endcard_916__0_x.mp4"] is None   # every other sequence: the delivery encoder, unchanged
