"""
src/photo_grade.py
==================
The photo grade for photo layers drawn inside graphics (QF-86).

The master grades every bare photograph (a still or a parallax clip) with video_composer.film_grade_filter(bias):
one 16-bit curve and the run's bounded warm/cool bias, run before the scenes are mixed. A graphic clip is not
graded, so the photo a graphic draws (the stat card's backdrop, the then-and-now pair, the callout photo, the hook
frame's ground, a chapter or myth card's ground) takes the same grade here, before any token, type or plate is
drawn on it: a photo looks the same bare and inside a graphic, and the drawn marks keep their own colour.

`grade(im)` maps each channel through a table made by running the master's own filter on all 256 input codes in
gbrp16le (ffmpeg, once per bias), so it is the master's curve, not an imitation. The bias is the one the master
will use for the same run: renderers read `current()`, which `using(bias)` sets for the calling thread (graphics
.make_all, callouts.make and then_now.make set it from the run; anything else, e.g. a direct render in a test,
gets the neutral curve at bias 0).
"""
from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Iterator
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from PIL import Image

_BIAS: ContextVar[float] = ContextVar("photo_grade_bias", default=0.0)


@contextlib.contextmanager
def using(bias: float | None) -> Iterator[None]:
    """Photo layers drawn in this thread inside the block take the master's grade at `bias`."""
    token = _BIAS.set(float(bias or 0.0))
    try:
        yield
    finally:
        _BIAS.reset(token)


def current() -> float:
    return _BIAS.get()


@lru_cache(maxsize=32)
def _table(bias: float) -> tuple[int, ...]:
    """The master's photo grade at `bias` as an Image.point table (R, G, B; 256 entries each)."""
    import numpy as np

    from .video_composer import film_grade_filter
    ramp = np.tile((np.arange(256, dtype=np.uint16) * 257)[None, None, :], (3, 1, 1))
    out = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-s", "256x1", "-i", "-",
                          "-vf", f"format=gbrp16le,{film_grade_filter(bias)},format=gbrp16le", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-"],
                         input=ramp.tobytes(), capture_output=True, check=True, timeout=60).stdout
    g, b, r = np.frombuffer(out, dtype="<u2").reshape(3, 256)          # gbrp: planes G, B, R
    return tuple(_to8(r) + _to8(g) + _to8(b))


def _to8(plane) -> list[int]:
    import numpy as np
    return np.clip(np.round(plane.astype(np.float64) / 257.0), 0, 255).astype(int).tolist()


def grade(im: Image.Image, bias: float | None = None) -> Image.Image:
    """`im` (a photograph) through the master's photo grade at `bias` (default: current())."""
    value = round(float(current() if bias is None else bias), 3)
    return im.convert("RGB").point(list(_table(value)))


def is_photograph(path: Path) -> bool:
    """The master's still rule (video_composer.is_photo_still): False for a generated illustration, a drawn graphic,
    a hook frame or footage, by name or anywhere in its lineage, and for a still whose lineage chain is broken or
    stale (fails closed); those keep their own colour, as the master leaves them."""
    from .video_composer import is_photo_still
    return is_photo_still(path)


def cover(photo: Path, size: tuple[int, int], bias: float | None = None) -> Image.Image:
    """typography.cover_crop(photo, size) as RGB, graded as the master grades a bare photo (an illustration or a
    graphic is returned ungraded)."""
    from .typography import cover_crop
    im = cover_crop(Path(photo), size).convert("RGB")
    return grade(im, bias) if is_photograph(Path(photo)) else im
