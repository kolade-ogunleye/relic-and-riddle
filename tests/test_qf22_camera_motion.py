"""QF-22: one shared camera. Every push and drift samples a fractional source window, so a slow move changes every
frame (no integer resize-and-crop staircase), through src/camera_motion.py."""

import numpy as np
import pytest
from PIL import Image


def _ramp(width=640, height=64):
    return Image.fromarray(np.tile(np.arange(width, dtype=np.float32), (height, 1)))


def test_float_ramp_measures_a_continuous_monotone_camera_trace():
    from src.camera_motion import camera_window, kenburns, smoothstep

    source = _ramp()
    trace, expected = [], []
    for frame in range(181):
        zoom = 1 + 0.015 * smoothstep(frame / 180)
        trace.append(float(np.asarray(kenburns(source, zoom=zoom))[32, 100]))
        left, _, right, _ = camera_window(source.size, source.size, zoom)
        expected.append(left + 100.5 * (right - left) / source.width - 0.5)
    assert np.diff(trace).min() > 0
    assert np.max(np.abs(np.array(trace) - expected)) < 0.1     # where an affine camera puts it, independently
    assert max(np.diff(trace)) < 0.1                             # sub-pixel moves, never a pixel jump


def test_identity_preserves_pixels_and_returns_an_independent_frame():
    from src.camera_motion import kenburns

    source = Image.fromarray(np.random.default_rng(22).integers(0, 256, (64, 96, 3), dtype=np.uint8))
    result = kenburns(source)
    assert result.tobytes() == source.tobytes() and result is not source


def test_zoom_out_holds_its_anchor_and_fills_uncovered_edges():
    from src.camera_motion import camera_window, kenburns

    source = Image.new("RGB", (100, 80), (255, 255, 255))
    left, top, right, bottom = camera_window(source.size, source.size, .9, (20, 60))
    assert (20 - left) * 100 / (right - left) == pytest.approx(20)
    assert (60 - top) * 80 / (bottom - top) == pytest.approx(60)
    result = kenburns(source, zoom=.9, centre=(20, 60), fill=(7, 8, 9))
    assert result.getpixel((0, 0)) == (7, 8, 9)
    assert result.getpixel((20, 60)) == (255, 255, 255)
    mirrored = kenburns(source, zoom=.9, centre=(20, 60))        # no fill: the picture's own edge, never black
    assert np.asarray(mirrored).min() == 255


def test_fractional_source_crop_is_sampled_without_an_intermediate_resize():
    from src.camera_motion import camera_window, kenburns

    source = _ramp()
    box = (30.25, 4.5, 510.75, 60.25)
    image = np.asarray(kenburns(source, (320, 40), source_box=box))
    assert camera_window(source.size, (320, 40), source_box=box) == box
    expected = box[0] + (np.arange(320) + .5) * (box[2] - box[0]) / 320 - .5
    assert np.max(np.abs(image[20] - expected)) < .1


def test_ease_is_smoothstep_at_rest_and_keeps_a_seam_speed_when_asked():
    from src.camera_motion import ease, slopes, smoothstep

    u = np.linspace(0, 1, 181)
    rest = np.array([ease(x) for x in u])
    assert np.allclose(rest, [smoothstep(x) for x in u])
    steps = np.diff(rest)
    assert steps.min() > 0 and steps[0] < steps[90] / 50 and steps[-1] < steps[90] / 50
    lead = np.array([ease(x, 1.0, 0.0) for x in u])
    assert np.diff(lead).min() > 0 and np.diff(lead)[0] == pytest.approx(1 / 180, rel=0.02)   # starts at speed 1
    assert np.diff(lead)[-1] < np.diff(lead)[90] / 50                                         # and settles
    trail = np.array([ease(x, 0.0, 1.0) for x in u])
    assert np.diff(trail)[-1] == pytest.approx(1 / 180, rel=0.02)
    for m0, m1 in ((5.0, 0.0), (0.0, 9.0), (2.5, 2.5)):           # past the monotone limit: scaled back to it
        a, b = slopes(m0, m1)
        assert np.hypot(a, b) == pytest.approx(3.0) and np.diff([ease(x, m0, m1) for x in u]).min() >= 0
    assert ease(-1) == 0 and ease(2) == 1


def test_loop_pull_preserves_pixels_at_identity_and_never_exposes_black_padding():
    from src import end_card

    source = Image.new("RGB", (96, 64), (70, 90, 110))
    for zoom in (.97, .9999, 1.0, 1.0001):
        frame = end_card._pulled(source, zoom, *source.size)
        assert np.max(np.abs(frame - np.asarray(source))) <= 1


@pytest.mark.parametrize("path", ["stat_card", "anchored", "anchored_out", "timeline", "end_card", "then_now",
                                  "callouts"])
def test_every_render_path_moves_by_a_fraction_of_a_pixel_every_frame(path):
    """The regression: on the base, the stat card's push, the anchored hold push and the timeline drift tick in
    whole (or half) pixels, holding a position for several frames and then jumping."""
    from src import callouts, end_card, graphics, then_now

    source = _ramp()
    drift = graphics.GroundDrift(source)
    push = callouts.Push(*source.size)
    trace = []
    for frame in range(40, 70):
        progress = frame / 180
        if path == "stat_card":
            output = graphics._pushed_in(source, graphics.push_in(progress * graphics.CLIP_SECONDS,
                                                                  graphics.CLIP_SECONDS))
        elif path == "anchored":
            output = graphics._push_about(source, 1 + .015 * progress, (320, 32))
        elif path == "anchored_out":                                  # below 1: the edge takes the fill
            output = graphics._push_about(source, 1 - .015 * progress, (320, 32), fill=0.0)
        elif path == "timeline":
            output = drift(progress * graphics.CLIP_SECONDS)
        elif path == "end_card":
            output = end_card._pushed(source, end_card.zoom_at(progress * end_card.SECONDS), *source.size)
        elif path == "then_now":
            output = then_now.pushed(source, progress * then_now.CLIP_SECONDS, source.size)
        else:
            output = callouts.camera_sample(source, source.size, push.crop(progress * callouts.CLIP_SECONDS))
        trace.append(float(np.asarray(output)[32, 100]))
    steps = np.diff(trace) * (-1 if path == "anchored_out" else 1)
    assert steps.min() > 0, f"{path} repeated a camera position"
    assert max(steps) < .2, f"{path} jumped rather than sampling a sub-pixel move"
    assert np.abs(np.diff(steps)).max() < .05, f"{path} moved in uneven ticks"


def test_ground_drift_samples_every_moving_frame_and_reuses_only_a_repeat():
    from src import graphics

    ground = Image.fromarray(np.random.default_rng(2).integers(0, 255, (64, 36, 3), dtype=np.uint8))
    drift = graphics.GroundDrift(ground)
    scales = []
    real = graphics._pushed_in
    try:
        graphics._pushed_in = lambda bg, s: scales.append(s) or real(bg, s)
        frames = [drift(i / 30) for i in range(int(graphics.CLIP_SECONDS * 30))]
        again = drift((len(frames) - 1) / 30)
    finally:
        graphics._pushed_in = real
    assert len(scales) == len(frames) - 1 and len(set(scales)) == len(scales)   # frame 0 is the ground itself
    assert again.tobytes() == frames[-1].tobytes()
