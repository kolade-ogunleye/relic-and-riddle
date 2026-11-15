"""QF-108: a photo beat spliced into a master (VideoComposer.splice_single_beat, the micro-patch) takes the master's
photo grade at the master's bias, by the master's own rules (compose_video): a photo still on the camera's moved
frame, a photo clip in 16-bit RGB; an illustration, a graphic, a hook frame or footage keeps its own colour."""
import inspect
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from src import graphics, still_camera
from src import video_composer as vc
from src.delivery_profiles import get_delivery_profile
from src.encoding import BT709_TO_RGB, RGB_TO_BT709

SETTINGS = {"preset": "hook", "duration": 1.0, "fps": 30, "variant": 0}


def lab(rgb):
    """CIE Lab D65 (delta-E 76, as QF-86's evidence)."""
    x = np.asarray(rgb, dtype=float) / 255
    x = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    xyz = x @ np.array([[.4124564, .3575761, .1804375],
                        [.2126729, .7151522, .0721750],
                        [.0193339, .1191920, .9503041]]).T
    xyz /= np.array([.95047, 1, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def decoded(path, t, width=1080, height=1920):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path),
                          "-vf", f"{BT709_TO_RGB},format=rgb24", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3)


def _still(path, colour=(85, 84, 45), size=(64, 96)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def _clip(path, colour=(85, 84, 45), size=(64, 96)):
    path.parent.mkdir(parents=True, exist_ok=True)
    return graphics._encode((Image.new("RGB", size, colour) for _ in range(30)), path, 30, *size)


def _master(tmp_path):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"m")
    return master


def test_the_splice_takes_the_masters_bias():
    assert "grade_bias" in inspect.signature(vc.VideoComposer.splice_single_beat).parameters, \
        "the micro-patch cannot be told the master's grade bias, so a spliced photo cannot match its neighbours"


def _spliced_still_grade(tmp_path, asset, bias):
    grades = []

    def render(_source, output, *_a, **kwargs):
        grades.append(kwargs.get("grade"))
        return Path(output)
    params = inspect.signature(vc.VideoComposer.splice_single_beat).parameters
    extra = {"grade_bias": bias} if "grade_bias" in params else {}
    with patch.object(still_camera, "render_clip", render), patch.object(vc, "_run_ffmpeg", lambda cmd: None):
        vc.VideoComposer.splice_single_beat(_master(tmp_path), 1, asset, [1.0, 1.0, 1.0], tmp_path / "out.mp4",
                                            **extra)
    (grade,) = grades
    return grade


@pytest.mark.parametrize("bias", [0.0, .036, .05, -.05])
def test_a_spliced_photo_still_takes_the_masters_photo_grade(tmp_path, bias):
    photo = _still(tmp_path / "src" / "04_commons.jpg")
    assert _spliced_still_grade(tmp_path, photo, bias) == vc.film_grade_filter(bias)


@pytest.mark.parametrize("name", ["gap_gen_03.png", "gen_04.png", "graphic_numbers.jpg", "hook_frame.jpg",
                                  "footage_01.jpg", "upscaled_illustration", "chain_broken"])
def test_a_spliced_still_that_is_not_a_photograph_keeps_its_colour(tmp_path, name):
    """Never grade illustrations, graphics or footage (QF-86): the master's still rule, failing closed."""
    from src.lineage import record_derived
    if name == "upscaled_illustration":
        art = _still(tmp_path / "src" / "gap_gen_05.png")
        asset = _still(tmp_path / "upscaled" / "up_05.png")
        record_derived(asset, [art], "upscale", {})
    elif name == "chain_broken":
        art = _still(tmp_path / "src" / "06_commons.jpg")
        asset = _still(tmp_path / "upscaled" / "up_06.png")
        record_derived(asset, [art], "upscale", {})
        art.unlink()
    else:
        asset = _still(tmp_path / "src" / name)
    assert _spliced_still_grade(tmp_path, asset, .05) is None


def _clip_case(tmp_path, kind):
    from src.lineage import record_derived
    photo = _still(tmp_path / "src" / "04_commons.jpg")
    if kind == "parallax_of_photo":
        clip = _clip(tmp_path / "parallax" / "scene_01.mp4")
        record_derived(clip, [photo], "parallax", SETTINGS)
        return clip, True
    if kind == "ai_motion_of_photo":
        anchor = _still(tmp_path / "motion" / "motion_01_anchor.jpg")
        record_derived(anchor, [photo], "motion_anchor_frame", {})
        clip = _clip(tmp_path / "motion" / "motion_01.mp4")
        record_derived(clip, [anchor], "ai_motion", {"provider": "fixture", "ai_generated": True})
        return clip, True
    if kind == "parallax_of_illustration":
        art = _still(tmp_path / "src" / "gap_gen_03.png")
        clip = _clip(tmp_path / "parallax" / "scene_03.mp4")
        record_derived(clip, [art], "parallax", SETTINGS)
        return clip, False
    if kind == "graphic_clip":
        return _clip(tmp_path / "graphics" / "graphic_numbers.mp4"), False
    if kind == "footage_clip":
        return _clip(tmp_path / "footage" / "footage_01.mp4"), False
    if kind == "no_lineage":
        return _clip(tmp_path / "parallax" / "scene_09.mp4"), False
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["parallax_of_photo", "ai_motion_of_photo", "parallax_of_illustration",
                                  "graphic_clip", "footage_clip", "no_lineage"])
def test_a_spliced_clip_is_graded_only_when_it_is_a_photo_clip(tmp_path, kind):
    clip, photo = _clip_case(tmp_path, kind)
    bias = .05
    commands = []
    params = inspect.signature(vc.VideoComposer.splice_single_beat).parameters
    extra = {"grade_bias": bias} if "grade_bias" in params else {}
    with patch.object(vc, "_run_ffmpeg", lambda cmd: commands.append(list(cmd))):
        vc.VideoComposer.splice_single_beat(_master(tmp_path), 1, clip, [1.0, 1.0, 1.0], tmp_path / "out.mp4", **extra)
    (command,) = commands
    graph = command[command.index("-filter_complex") + 1]
    graded = f"format=gbrp16le,{vc.film_grade_filter(bias)},{RGB_TO_BT709}[v_mid]" in graph
    assert graded == photo, graph
    assert graph.count("curves=") == int(photo), graph


@pytest.mark.parametrize("bias", [.05, -.05])
def test_a_spliced_photo_beat_matches_the_beat_the_master_drew(tmp_path, bias):
    """Actual pixels: a master of one photo on three beats at `bias` (lens off: the grade alone), its middle beat
    spliced with the same photo. With both paths on the same camera move, the spliced beat matches the master's own
    beat to the delivery encode's noise (median CIE76 about 1.3; the untouched neighbours, re-encoded, about
    0.8-1.1). Ungraded (e49616fc) it was CIE76 4.7 away, its red-minus-blue 11 codes off at +0.05."""
    assert "grade_bias" in inspect.signature(vc.VideoComposer.splice_single_beat).parameters
    rng = np.random.default_rng(7)
    arr = (rng.random((96, 54, 3)) * np.array([230, 190, 150]) + 15).astype(np.uint8)
    photo = tmp_path / "src" / "04_commons.png"
    photo.parent.mkdir()
    Image.fromarray(arr).resize((1080, 1920), Image.BICUBIC).save(photo)
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                    "-t", "3", str(voice)], check=True, timeout=60)
    real = still_camera.render_clip

    def same_move(source, output, duration, size, fps, **kwargs):
        # the master's scenes on the splice's own move, so the two beats compare pixel for pixel
        kwargs.update(preset="splice", focal=(0.5, 0.5), tilt=None, lead=False, trail=False)
        return real(source, output, duration, size, fps, **kwargs)
    master = tmp_path / "master.mp4"
    with patch.object(vc, "picture_vignette", lambda width, height: "null"), \
            patch.object(still_camera, "render_clip", same_move):
        vc.VideoComposer().compose_video([photo, photo, photo], voice, voice, [], 3, master, durations=[1, 1, 1],
                                         grade_bias=bias, delivery_profile=get_delivery_profile("reel"))
    patched = vc.VideoComposer.splice_single_beat(master, 1, photo, [1.0, 1.0, 1.0], tmp_path / "patched.mp4",
                                                  grade_bias=bias)
    for t in (1.2, 1.5, 1.9):
        a, b = decoded(master, t).astype(np.float64), decoded(patched, t).astype(np.float64)
        gap = np.linalg.norm(lab(a[::4, ::4]) - lab(b[::4, ::4]), axis=-1)
        warmth = float((a[..., 0] - a[..., 2]).mean() - (b[..., 0] - b[..., 2]).mean())
        assert float(np.median(gap)) <= 2.0 and float(np.percentile(gap, 95)) <= 4.0, (t, bias, np.median(gap))
        assert abs(warmth) <= 1.0, (t, bias, warmth)
