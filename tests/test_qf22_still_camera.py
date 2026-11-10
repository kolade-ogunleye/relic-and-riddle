"""QF-22: the composer's still camera (src/still_camera.py) in place of FFmpeg zoompan: fractional positions, no
hard stop at the old cap, each shot's old extent, the loop seam's speed, and a lossless clip of exactly its frames."""

import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from src import end_card, still_camera
from src.encoding import RGB_TO_BT709

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required")


def _ramp(width=640, height=360):
    return Image.fromarray(np.tile(np.arange(width, dtype=np.float32), (height, 1)))


def _trace(preset, frames=181, lead=False, trail=False):
    source = _ramp()
    camera = still_camera.Camera(source.size, source.size, preset,
                                 still_camera.motion_for(preset, frames, lead=lead, trail=trail))
    return np.array([float(np.asarray(still_camera.frame_at(source, camera, i))[180, 100]) for i in range(frames)])


@pytest.mark.parametrize("preset,direction", [("push", 1), ("macro", 1), ("pull", -1), ("drift", 1), ("splice", 1)])
def test_every_preset_moves_between_every_frame_and_settles_without_a_hard_stop(preset, direction):
    steps = np.diff(_trace(preset)) * direction
    assert steps.min() > 0 and max(steps) < 1
    assert steps[0] < max(steps) / 30 and steps[-1] < max(steps) / 30          # at rest at both ends
    assert np.abs(np.diff(steps)).max() < max(steps) / 20                      # no tick, no stop at a cap


def test_the_seam_ends_keep_the_old_push_speed():
    """The opening shot starts at LOOP_PUSH (the loop bridge pre-rolls frame 0 at that speed) and the closing shot
    ends at it (the bridge carries it on), each easing to rest inside the shot."""
    for frames in (60, 150, 300):
        lead = still_camera.motion_for("push", frames, lead=True)
        assert lead.start_velocity == pytest.approx(end_card.LOOP_PUSH) and lead.end_velocity == 0
        trail = still_camera.motion_for("push", frames, trail=True)
        assert trail.end_velocity == pytest.approx(end_card.LOOP_PUSH) and trail.start_velocity == 0
        assert lead.end_zoom == trail.end_zoom == still_camera.endpoint_for("push", frames)
        z = [lead.zoom(lead.progress(i)) for i in range(frames)]
        assert z[1] - z[0] == pytest.approx(end_card.LOOP_PUSH, rel=0.02) and np.diff(z).min() > 0
    long = still_camera.motion_for("push", 900, lead=True)        # a 30 s shot: the speed is limited to stay monotone
    assert 0 < long.start_velocity < end_card.LOOP_PUSH


@pytest.mark.parametrize("preset,frames,old", [
    ("push", 60, 1 + .0016 * 60), ("push", 600, 1.24), ("macro", 60, 1 + .0025 * 59), ("macro", 600, 1.35),
    ("pull", 60, 1.2 - .0014 * 59), ("pull", 600, 1.0), ("drift", 60, 1 + .0013 * 60), ("drift", 600, 1.18),
    ("splice", 60, 1 + .0014 * 60), ("splice", 600, 1.20)])
def test_each_shot_keeps_the_extent_the_old_move_reached(preset, frames, old):
    assert still_camera.endpoint_for(preset, frames) == pytest.approx(old)
    assert still_camera.endpoint_for(preset, 1) == still_camera.PRESETS[preset].start


def test_portrait_asset_tilt_keeps_full_width_and_reaches_both_ends():
    source = Image.fromarray(np.tile(np.arange(320, dtype=np.float32)[:, None], (1, 120)))
    motion = still_camera.motion_for("push", 9)
    down = still_camera.Camera(source.size, (160, 90), "push", motion, tilt="down")
    up = still_camera.Camera(source.size, (160, 90), "push", motion, tilt="up")
    first, last = (np.asarray(still_camera.frame_at(source, down, i)) for i in (0, 8))
    assert np.array_equal(last, np.asarray(still_camera.frame_at(source, up, 0)))
    assert first[45, 80] < 40 and last[45, 80] > 280
    assert down.window(4)[0] == 0 and down.window(4)[2] == 120            # the full width, every frame


def test_working_copy_keeps_twice_the_frame_and_never_enlarges():
    big = Image.new("RGB", (9000, 6000))                     # its cover box is 3375 x 6000 for a 9:16 frame
    work, factor = still_camera.working_copy(big, (1080, 1920))
    assert work.height == 3840 and factor == pytest.approx(3840 / 6000, rel=1e-3)
    for size in ((4000, 3000), (800, 600)):                  # under twice the frame already: used as it is
        small = Image.new("RGB", size)
        assert still_camera.working_copy(small, (1080, 1920)) == (small, 1.0)


def _yuv(frames, size, vf):
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
                           "-r", "30", "-i", "-", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                          input=b"".join(f.tobytes() for f in frames), capture_output=True, check=True,
                          timeout=120).stdout


@needs_ffmpeg
def test_the_camera_clip_holds_exactly_the_frames_the_graph_would_have_converted(tmp_path):
    source = tmp_path / "photo.png"
    image = Image.fromarray(np.random.default_rng(22).integers(0, 256, (240, 360, 3), dtype=np.uint8))
    image.save(source)
    size = (96, 64)
    output = tmp_path / "camera.mkv"
    motion = still_camera.render_clip(source, output, .3, size, 30, preset="drift", curve=True)
    assert motion.frames == 9
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "rawvideo", "-pix_fmt", "yuv420p",
                              "-"], capture_output=True, check=True, timeout=60).stdout
    camera = still_camera.Camera(image.size, size, "drift", motion)
    work, factor = still_camera.working_copy(image, size)
    frames = [still_camera.frame_at(work, camera, i, factor=factor) for i in range(9)]
    assert decoded == _yuv(frames, size, f"{still_camera.STILL_CURVE},{RGB_TO_BT709}")     # lossless, in order
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,color_space",
                            "-of", "csv=p=0", str(output)], capture_output=True, text=True, check=True).stdout
    assert probe.strip().startswith("ffv1") and "bt709" in probe


@needs_ffmpeg
def test_an_ai_hook_clip_gets_the_push_on_every_frame_and_holds_its_last_frame(tmp_path):
    clip = tmp_path / "motion_01.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x160:rate=30:duration=0.2",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(clip)], check=True, timeout=60)
    output = tmp_path / "hook.mkv"
    motion = still_camera.render_clip(clip, output, .4, (96, 160), 30, preset="hook", lead=True)
    assert motion.frames == 12 and motion.start_velocity == pytest.approx(end_card.LOOP_PUSH)
    frames = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries", "stream=nb_read_frames",
                             "-of", "csv=p=0", str(output)], capture_output=True, text=True, check=True).stdout
    assert int(frames.strip()) == 12                                     # 6 decoded, the last held 6 more


def test_loop_motion_predicts_the_seam_the_cameras_render(tmp_path):
    """Stills ease to rest at the seam (no push through the bridge, the zoom of their last frame); an AI hook clip
    opening the Reel starts at LOOP_PUSH, which the bridge pre-rolls frame 0 at."""
    from src.video_composer import VIDEO_FPS, loop_motion, seam_from_cameras
    for first in ("a.jpg", "motion_01.mp4"):
        scenes = [tmp_path / first, tmp_path / "b.jpg", tmp_path / "c.jpg"]
        hook = first.endswith(".mp4")
        for vo_dur, durations in ((4.0, [1.0, 1.5, 1.5]), (22.0, [2.0, 2.0, 18.0]), (3.3, [1.1, 1.1, 1.1])):
            boundaries = [round(sum(durations[:k + 1]) * VIDEO_FPS) / VIDEO_FPS for k in range(2)]
            motion_durations = [b - a for a, b in zip([0.0, *boundaries], [*boundaries, vo_dur])]
            got = loop_motion(scenes, motion_durations, vo_dur, set(), closing_transition=0.0,
                              opening_transition=0.0)
            ends = [boundaries[0], np.ceil(vo_dur * VIDEO_FPS) / VIDEO_FPS]
            rendered = {0: still_camera.motion_for("hook" if hook else "push",
                                                   still_camera.frame_count(ends[0], VIDEO_FPS), lead=hook),
                        2: still_camera.motion_for("push", still_camera.frame_count(ends[1] - boundaries[1],
                                                                                    VIDEO_FPS))}
            assert got == seam_from_cameras(rendered, 3)
            assert got["close_push"] == 0.0 and got["close_zoom"] == still_camera.endpoint_for(
                "push", still_camera.frame_count(ends[1] - boundaries[1], VIDEO_FPS))
            assert got["open_push"] == (pytest.approx(end_card.LOOP_PUSH) if hook else 0.0)


def _yuv_frame0(path, size):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "yuv420p", "-"], capture_output=True, check=True, timeout=60).stdout
    w, h = size
    y = np.frombuffer(raw[:w * h], np.uint8).astype(float)
    u = np.frombuffer(raw[w * h:w * h * 5 // 4], np.uint8).astype(float)
    v = np.frombuffer(raw[w * h * 5 // 4:w * h * 3 // 2], np.uint8).astype(float)
    return np.array([y.mean(), u.mean(), v.mean()])


@needs_ffmpeg
@pytest.mark.parametrize("tags", [[], ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                                       "-color_range", "tv"]])
def test_the_hook_clip_keeps_its_colour_through_the_camera(tmp_path, tags):
    """Review 1: the RGB read truncated (frame 0 came back 0.77 Y levels darker, chroma 0.145 off). Frame 0 (zoom
    1, the clip's own size) must keep the source's mean Y, U and V."""
    from PIL import ImageFilter
    size = (192, 320)
    rng = np.random.default_rng(22)          # a photograph-like frame: soft colour fields, in gamut
    still = Image.fromarray(rng.integers(40, 216, (size[1] // 8, size[0] // 8, 3), dtype=np.uint8)).resize(
        size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(2))
    still.save(tmp_path / "frame.png")
    clip = tmp_path / "motion_01.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(tmp_path / "frame.png"), "-t", "0.2",
                    "-r", "30", "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", *tags, str(clip)], check=True, timeout=60)
    output = tmp_path / "hook.mkv"
    still_camera.render_clip(clip, output, .2, size, 30, preset="hook", lead=True,
                             colorspace="bt709" if tags else "")
    assert np.abs(_yuv_frame0(output, size) - _yuv_frame0(clip, size)).max() < 0.2


def test_a_render_that_would_not_fit_on_the_disk_is_refused(tmp_path, monkeypatch):
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(still_camera.shutil, "disk_usage", lambda _p: usage(10 ** 12, 0, 3 * 1024 ** 3))
    still_camera.ensure_disk(tmp_path / "x.camera-1", 100, (1080, 1920))       # 0.24 GB + reserve fits in 3 GB
    with pytest.raises(RuntimeError, match="GB free"):
        still_camera.ensure_disk(tmp_path / "x.camera-1", 1800, (1080, 1920))  # a 60 s Reel of stills: 4.3 GB


def test_a_killed_renders_camera_folders_are_swept_only_when_stale(tmp_path):
    import os
    import time
    old_dir, old_clip, fresh = tmp_path / "m.camera-aa", tmp_path / "m.camera-3.mkv", tmp_path / "m.camera-bb"
    old_dir.mkdir()
    (old_dir / "0.mkv").write_bytes(b"x")
    old_clip.write_bytes(b"x")
    fresh.mkdir()
    keep = tmp_path / "m_master.mp4"
    keep.write_bytes(b"x")
    stale = time.time() - still_camera.STALE_SECONDS - 60
    for p in (old_dir, old_clip, keep):
        os.utime(p, (stale, stale))
    removed = still_camera.sweep_stale(tmp_path)
    assert set(removed) == {old_dir, old_clip} and fresh.exists() and keep.exists() and not old_dir.exists()


def test_a_long_pull_keeps_its_sway_where_the_photograph_is_wider_than_the_frame():
    """Review 1: the sway was held by the zoom's margin alone, so a pull ending at 1.0 (>= 143 frames) lost it.
    It is now held by the picture the window can really use: a landscape photograph in a 9:16 frame keeps it."""
    motion = still_camera.motion_for("pull", 300)
    assert motion.end_zoom == 1.0
    wide = still_camera.Camera((3000, 2000), (1080, 1920), "pull", motion)
    assert wide.sway[0] == pytest.approx(16.0)
    x0, _, x1, _ = wide.window(299)
    assert 0 <= x0 and x1 <= 3000 and x0 > wide.box[0]                      # moved sideways, inside the picture
    exact = still_camera.Camera((1080, 1920), (1080, 1920), "pull", motion)   # the frame's own shape: no room
    assert exact.sway == (0.0, 0.0)
