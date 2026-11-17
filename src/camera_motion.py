"""The shared camera (QF-22): one fractional sampler and the time curves every push, pull and drift uses.

Every camera move in the renderers (the composer's stills and AI hook clip, the graphics' photo pushes and drifts,
the end card and the loop bridge, then-and-now, the photo callouts) samples its frame here: a floating-point source
window, resampled in one separable Pillow pass (`Image.resize` with a fractional `box`, antialiased when it reduces).
Nothing is rounded to a whole pixel, so a slow push moves a little every frame instead of ticking one pixel every
few frames (the integer resize-and-crop and zoompan copies this replaces: review 2026-11-05, section 2 item 5).
"""

import math

import numpy as np
from PIL import Image

RESAMPLE = Image.Resampling.BICUBIC


def smoothstep(progress: float) -> float:
    """Clamped progress with zero velocity at both ends of a move."""
    value = max(0.0, min(1.0, float(progress)))
    return value * value * (3.0 - 2.0 * value)


def ease(progress: float, start_slope: float = 0.0, end_slope: float = 0.0) -> float:
    """The share of a move done at `progress` (0-1): a cubic Hermite from 0 to 1 whose slopes at the two ends are
    `start_slope` and `end_slope` (in units of the move's mean speed). Both 0 is smoothstep; a non-zero end keeps a
    move's speed across a seam (the loop bridge). The slopes are limited to the circle of radius 3, inside which the
    cubic never overshoots or runs backwards (Fritsch-Carlson)."""
    m0, m1 = max(0.0, float(start_slope)), max(0.0, float(end_slope))
    norm = math.hypot(m0, m1)
    if norm > 3.0:
        m0, m1 = m0 * 3.0 / norm, m1 * 3.0 / norm
    t = max(0.0, min(1.0, float(progress)))
    return (3 * t * t - 2 * t ** 3) + m0 * (t ** 3 - 2 * t * t + t) + m1 * (t ** 3 - t * t)


def slopes(start_slope: float, end_slope: float) -> tuple[float, float]:
    """The slopes ease() really uses for these (after its monotonicity limit)."""
    m0, m1 = max(0.0, float(start_slope)), max(0.0, float(end_slope))
    norm = math.hypot(m0, m1)
    return (m0 * 3.0 / norm, m1 * 3.0 / norm) if norm > 3.0 else (m0, m1)


def camera_window(source_size, size, zoom=1.0, centre=None, pan=(0.0, 0.0), source_box=None):
    """The source window (left, top, right, bottom) a frame shows, never rounded.

    `source_box` is the part of the source that fills the frame at zoom 1 (default: all of it); the zoom is about
    `centre` (default: the box's centre), which keeps its place in the frame; `pan` then moves the window by that
    many source pixels."""
    width, height = source_size
    if min(width, height, *size) <= 0:
        raise ValueError("Camera dimensions must be positive")
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("Camera zoom must be finite and positive")
    x0, y0, x1, y1 = source_box if source_box is not None else (0.0, 0.0, width, height)
    cx, cy = centre if centre is not None else ((x0 + x1) / 2, (y0 + y1) / 2)
    if not all(math.isfinite(value) for value in (cx, cy, *pan, x0, y0, x1, y1)):
        raise ValueError("Camera positions must be finite")
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Camera source window must have positive dimensions")
    left = cx + (x0 - cx) / zoom + pan[0]
    top = cy + (y0 - cy) / zoom + pan[1]
    return left, top, left + (x1 - x0) / zoom, top + (y1 - y0) / zoom


def sample(image: Image.Image, size, window, *, fill=None, resample=RESAMPLE) -> Image.Image:
    """`window` of `image` (fractional source pixels) at `size`, in one resampling pass. A window reaching past the
    image is drawn over a margin of `fill` (a colour), or of the image's own edge mirrored when `fill` is None."""
    size = tuple(int(v) for v in size)
    left, top, right, bottom = (float(v) for v in window)
    w, h = image.size
    if size == (w, h) and (left, top, right, bottom) == (0.0, 0.0, float(w), float(h)):
        return image.copy()
    if left < 0 or top < 0 or right > w or bottom > h:
        # Resampling reads a few pixels past the window (the kernel's support at the reduction), so the margin
        # carries 4 more than the overhang.
        mx = math.ceil(max(0.0, -left, right - w)) + 4 if (left < 0 or right > w) else 0
        my = math.ceil(max(0.0, -top, bottom - h)) + 4 if (top < 0 or bottom > h) else 0
        if fill is None:
            arr = np.asarray(image)
            pad = ((my, my), (mx, mx)) + ((0, 0),) * (arr.ndim - 2)
            image = Image.fromarray(np.pad(arr, pad, mode="symmetric"), image.mode)
        else:
            canvas = Image.new(image.mode, (w + 2 * mx, h + 2 * my), fill)
            canvas.paste(image, (mx, my))
            image = canvas
        left, right, top, bottom = left + mx, right + mx, top + my, bottom + my
    return image.resize(size, resample, box=(left, top, right, bottom))


def kenburns(image: Image.Image, size=None, *, zoom=1.0, centre=None, pan=(0.0, 0.0), source_box=None, fill=None,
             resample=RESAMPLE) -> Image.Image:
    """`image` through the camera: camera_window's window, sampled at `size` (default the image's own) by sample().
    Callers that animate pass the zoom and pan their time curve gives for the frame; a still endpoint is the same
    call, so a held frame and the moving frames next to it cannot disagree."""
    size = size or image.size
    window = camera_window(image.size, size, zoom, centre, pan, source_box)
    return sample(image, size, window, fill=fill, resample=resample)
