"""The composer's camera on a still photograph or an AI hook clip (QF-22), through the shared camera_motion sampler.

Until QF-22 the composer moved its stills with FFmpeg zoompan: a linear zoom that stopped dead at its cap (a 5 s shot
hit 1.24 at frame 150 and froze), on crop positions rounded to whole pixels of a 2x render (half-pixel ticks on the
delivered frame). Each shot now keeps the extent the old move reached at its last frame (endpoint_for: a short shot
still travels less than a long one) and reaches it on smoothstep (camera_motion.ease), at rest at both ends. Only
the AI hook clip keeps the old rate from its first frame (`lead`, as its zoompan did), and the loop bridge pre-rolls
frame 0 at that rate; `trail` (ending at the rate) is available to a caller that carries a push on.
Frames are sampled from a 2x working copy at fractional positions and written to a lossless FFV1 clip in the
composer's graph format (BT.709 yuv420p, after the still grade's tone curve), so the graph reads exactly the frames
it used to convert itself.
"""

import math
import os
import shutil
import subprocess
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Timer

from PIL import Image, ImageOps

from . import camera_motion
from .encoding import RGB_TO_BT709, source_matrix

# The stills' tone curve (QF-02: no black lift), applied before the graph's BT.709 conversion as it was in the graph.
STILL_CURVE = "curves=master='0/0 0.1/0.11 0.5/0.50 0.9/0.89 1/0.97'"
# Reading a YUV clip into RGB: swscale's default conversion truncates (a hook clip came back 0.77 Y levels darker,
# chroma 0.145 off, after the BT.709 encode); accurate rounding and full chroma interpolation make the round trip
# exact to 0.00 Y / 0.003 UV (QF-22 review 1). The matrix is the clip's own, as to_bt709 reads it.
# A clip tagged full range ("pc") is read as such; anything else as limited range, as the graph's to_bt709 reads it.
DECODE_TO_RGB = "scale=in_color_matrix={matrix}:in_range={range}:flags=bicubic+accurate_rnd+full_chroma_int"
SUPERSAMPLE = 2          # the working copy is at most twice the frame (the old zoompan rendered at 2x)
TIMEOUT_SECONDS = 3600


@dataclass(frozen=True)
class Preset:
    start: float          # zoom at the first frame
    rate: float           # the old move's zoom per frame (negative: a pull)
    cap: float            # where the old move stopped
    pzoom: bool           # the old expression was pzoom+rate (frame k at start + rate*(k+1)), else start + rate*k
    sway: tuple[float, float] = (0.0, 0.0)   # lateral drift in frame px (x, y), from the centre over the shot


# The old zoompan moves, kept as data: the opening and closing shots push (the loop seam), the rest alternate a
# pull and a drift; a macro drift pushes deeper; "splice" is the micro-patch's push and "hook" the AI hook clip's.
# Sway amplitudes are the old sine pans' peaks (32, 28 and 16 px at the 2x render: 16, 14 and 8 frame px).
PRESETS = {
    "push": Preset(1.0, 0.0016, 1.24, True),
    "macro": Preset(1.0, 0.0025, 1.35, False),
    "pull": Preset(1.2, -0.0014, 1.0, False, (16.0, 0.0)),
    "drift": Preset(1.0, 0.0013, 1.18, True, (-14.0, 8.0)),
    "splice": Preset(1.0, 0.0014, 1.20, True),
    "hook": Preset(1.0, 0.0016, 1.24, False),
}


# Transient disk: an FFV1 frame of a real photograph measured up to 2.04 MB at 1080x1920 (0.98 B/px); the check
# reserves 1.25 B/px for every frame of a render's camera clips (all exist while the graph runs) plus DISK_RESERVE.
DISK_BYTES_PER_PIXEL = 1.25
DISK_RESERVE = 2 * 1024 ** 3
# A camera folder older than this was left by a killed render (every encode is bounded by TIMEOUT_SECONDS).
STALE_SECONDS = 6 * 3600


def ensure_disk(directory: Path, frames: int, size) -> None:
    """Refuse a render whose camera clips would not fit: RuntimeError naming what is needed and what is free."""
    directory = Path(directory)
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    need = int(frames * size[0] * size[1] * DISK_BYTES_PER_PIXEL) + DISK_RESERVE
    if free < need:
        raise RuntimeError(f"The still camera needs {need / 1024 ** 3:.1f} GB free in {directory.name} for "
                           f"{frames} frames ({free / 1024 ** 3:.1f} GB free)")


def sweep_stale(directory: Path, now: float | None = None) -> list[Path]:
    """Remove camera folders and clips (`*.camera-*`) a killed render left in `directory` over STALE_SECONDS ago."""
    import time
    now = time.time() if now is None else now
    removed = []
    for path in Path(directory).glob("*.camera-*"):
        try:
            if now - path.stat().st_mtime < STALE_SECONDS:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed.append(path)
        except OSError:
            continue
    return removed


def preset_for(index: int, total: int, macro: bool = False) -> str:
    if macro:
        return "macro"
    if index == 0 or index == total - 1:
        return "push"
    return "pull" if index % 2 else "drift"


def frame_count(duration: float, fps: float) -> int:
    """Frames a clip of `duration` s covers (the graph trims it to the duration)."""
    if not math.isfinite(duration) or duration <= 0 or fps <= 0:
        raise ValueError("Camera duration and frame rate must be positive")
    return max(1, math.ceil(duration * fps - 1e-6))


def endpoint_for(preset: str, frames: int) -> float:
    """The zoom the old move had reached at the shot's last frame: each shot keeps its travel."""
    if frames < 1:
        raise ValueError("Camera frame count must be positive")
    p = PRESETS[preset]
    if frames == 1:
        return p.start                     # a single frame has no move
    steps = frames if p.pzoom else frames - 1
    end = p.start + p.rate * steps
    return min(end, p.cap) if p.rate > 0 else max(end, p.cap)


@dataclass(frozen=True)
class Motion:
    """A shot's zoom over its frames: start and end zoom, the ease's slopes, and the zoom per frame at each end."""
    frames: int
    start_zoom: float
    end_zoom: float
    start_slope: float
    end_slope: float

    def zoom(self, progress: float) -> float:
        e = camera_motion.ease(progress, self.start_slope, self.end_slope)
        return self.start_zoom + (self.end_zoom - self.start_zoom) * e

    def progress(self, index: int) -> float:
        return index / (self.frames - 1) if self.frames > 1 else 0.0

    @property
    def start_velocity(self) -> float:
        return self._velocity(self.start_slope)

    @property
    def end_velocity(self) -> float:
        return self._velocity(self.end_slope)

    def _velocity(self, slope: float) -> float:
        return abs(self.end_zoom - self.start_zoom) * slope / (self.frames - 1) if self.frames > 1 else 0.0


def motion_for(preset: str, frames: int, lead: bool = False, trail: bool = False) -> Motion:
    """The shot's Motion: the old extent (endpoint_for), at rest at both ends except a seam end (`lead` the first
    frame, `trail` the last), which moves at the old rate (a push only)."""
    p = PRESETS[preset]
    start, end = p.start, endpoint_for(preset, frames)
    travel = abs(end - start)
    if frames < 2 or travel <= 0 or p.rate <= 0:
        return Motion(frames, start, end if frames > 1 else start, 0.0, 0.0)
    slope = p.rate * (frames - 1) / travel     # the old rate, in units of this shot's mean speed
    m0, m1 = camera_motion.slopes(slope if lead else 0.0, slope if trail else 0.0)
    return Motion(frames, start, end, m0, m1)


class Camera:
    """Where each frame of one shot looks: `source_size` framed to `size` (a cover crop at `focal`), moved by
    `motion`, swaying per its preset; `tilt` ("down"/"up") is the 16:9 full-width reveal of a tall picture."""

    def __init__(self, source_size, size, preset="push", motion: Motion | None = None, focal=(0.5, 0.5),
                 tilt: str | None = None):
        width, height = source_size
        out_w, out_h = size
        if min(width, height, out_w, out_h) <= 0:
            raise ValueError("Camera dimensions must be positive")
        if preset not in PRESETS:
            raise ValueError(f"Unknown still camera preset: {preset}")
        if tilt not in (None, "down", "up"):
            raise ValueError(f"Unknown tilt: {tilt}")
        self.source_size, self.size, self.preset, self.tilt = (width, height), (out_w, out_h), preset, tilt
        self.motion = motion or motion_for(preset, 1)
        if tilt:
            crop_h = min(height, width * out_h / out_w)
            self.box = (0.0, 0.0, float(width), crop_h)
        else:
            ratio = min(width / out_w, height / out_h)
            crop_w, crop_h = out_w * ratio, out_h * ratio
            fx, fy = (max(0.0, min(1.0, float(v))) for v in focal)
            left, top = (width - crop_w) * fx, (height - crop_h) * fy
            self.box = (left, top, left + crop_w, top + crop_h)
        self.sway = self._sway()

    def _sway(self) -> tuple[float, float]:
        """The preset's sway (frame px), held to half the picture the window can use on its side at every point of
        the move: the zoom's own margin inside the cover box plus the photograph beyond it. A pull ending at zoom 1
        keeps its sway wherever the photograph is wider (or taller) than the frame's shape, which is the usual case;
        only a picture of exactly the frame's shape has no room left at the end, and then no sway."""
        sway = PRESETS[self.preset].sway
        if not any(sway) or self.tilt:
            return 0.0, 0.0
        width, height = self.source_size
        x0, y0, x1, y1 = self.box
        m = self.motion
        out = []
        for axis, amount in enumerate(sway):
            if not amount:
                out.append(0.0)
                continue
            span, frame = ((x1 - x0), self.size[0]) if axis == 0 else ((y1 - y0), self.size[1])
            beyond = (width - x1 if amount > 0 else x0) if axis == 0 else (height - y1 if amount > 0 else y0)
            limit = abs(amount)
            for k in range(1, 21):
                e = k / 20
                z = m.start_zoom + (m.end_zoom - m.start_zoom) * e
                margin = beyond + span * (1 - 1 / z) / 2          # source px free on the sway's side
                limit = min(limit, 0.5 * margin / (e * span / frame / z))
            out.append(math.copysign(max(0.0, limit), amount))
        return out[0], out[1]

    def window(self, index: int) -> tuple[float, float, float, float]:
        width, height = self.source_size
        u = self.motion.progress(index)
        x0, y0, x1, y1 = self.box
        if self.tilt:
            e = camera_motion.smoothstep(u)
            top = (height - (y1 - y0)) * (e if self.tilt == "down" else 1 - e)
            return x0, top, x1, top + (y1 - y0)
        m = self.motion
        zoom = m.zoom(u)
        e = (zoom - m.start_zoom) / (m.end_zoom - m.start_zoom) if m.end_zoom != m.start_zoom else 0.5
        # The sway travels one way from the centre, on the zoom's own curve: a frame position is then linear in
        # (zoom, sway), both monotone, so no point ever turns back (the old sine pan went out and back). _sway()
        # holds it inside the picture, so it never needs the picture's edge.
        sway_x, sway_y = self.sway
        scale = (x1 - x0) / self.size[0] / zoom          # source px per frame px at this zoom
        left, top, right, bottom = camera_motion.camera_window(self.source_size, self.size, zoom, source_box=self.box,
                                                              pan=(e * sway_x * scale, e * sway_y * scale))
        # Never past the picture: the window slides back inside it (the sway stays well inside its margin).
        dx = max(0.0, -left) - max(0.0, right - width)
        dy = max(0.0, -top) - max(0.0, bottom - height)
        return left + dx, top + dy, right + dx, bottom + dy


def working_copy(image: Image.Image, size) -> tuple[Image.Image, float]:
    """`image` reduced once so the frame's source box is at most SUPERSAMPLE x the frame (never enlarged), and the
    factor: every frame is then sampled from this copy at the scaled window, which keeps the 2x detail the old
    render had at a fraction of the cost of reading the full photograph each frame."""
    width, height = image.size
    ratio = min(width / size[0], height / size[1])
    factor = min(1.0, SUPERSAMPLE / ratio)
    if factor >= 1.0:
        return image, 1.0
    reduced = image.resize((max(1, round(width * factor)), max(1, round(height * factor))), Image.Resampling.LANCZOS)
    return reduced, reduced.width / width


def frame_at(image: Image.Image, camera: Camera, index: int, *, factor: float = 1.0) -> Image.Image:
    """Frame `index` of `camera` from `image` (the source, or its working_copy at `factor`)."""
    window = tuple(v * factor for v in camera.window(index))
    return camera_motion.sample(image, camera.size, window)


def _workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) // 2))


def _video_probe(source: Path) -> tuple[tuple[int, int], str]:
    """The clip's (width, height) and its range for swscale ("pc" when tagged full range, else "tv")."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,color_range", "-of", "default=noprint_wrappers=1", str(source)],
                           capture_output=True, text=True, timeout=60, check=True)
    fields = dict(line.split("=", 1) for line in probe.stdout.strip().splitlines() if "=" in line)
    full = fields.get("color_range", "").strip().lower() in ("pc", "jpeg", "full")
    return (int(fields["width"]), int(fields["height"])), "pc" if full else "tv"


def _video_frames(source: Path, size: tuple[int, int], duration: float, fps: float, frames: int,
                  colorspace: str | None, colour_range: str = "tv"):
    """The clip's RGB frames (`size`, read with its own matrix, QF-01) at `fps`, its last frame held to `frames`."""
    width, height = size
    process = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-t", f"{duration:.3f}", "-i", str(source), "-an", "-vf",
         f"fps={fps},{DECODE_TO_RGB.format(matrix=source_matrix(colorspace), range=colour_range)},format=rgb24",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size, last, count = width * height * 3, None, 0
    try:
        while count < frames:
            data = process.stdout.read(size)
            if len(data) < size:
                break
            last = Image.frombytes("RGB", (width, height), data)
            count += 1
            yield last
    finally:
        process.stdout.close()
        process.kill()
        process.wait()
    if last is None:
        raise RuntimeError(f"No frames decoded from {source.name}")
    for _ in range(frames - count):     # the old graph's tpad=stop_mode=clone
        yield last


def render_clip(source, output, duration, size, fps, *, preset="push", focal=(0.5, 0.5), lead=False, trail=False,
                tilt: str | None = None, curve: bool = False, colorspace: str | None = None) -> Motion:
    """Render one shot to `output` (FFV1, BT.709 yuv420p): a still photograph, or (a video `source`) an AI hook clip
    whose every frame gets the move. `curve` applies STILL_CURVE first. Returns the shot's Motion, from which the
    loop bridge takes the seam's zoom and speed."""
    from .typography import is_video
    source, output = Path(source), Path(output)
    frames = frame_count(duration, fps)
    motion = motion_for(preset, frames, lead=lead, trail=trail)
    video = is_video(source)
    if video:
        source_size, colour_range = _video_probe(source)
        camera = Camera(source_size, size, preset, motion, focal)
        images = ((frame, 1.0) for frame in _video_frames(source, source_size, duration, fps, frames, colorspace,
                                                          colour_range))
    else:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        camera = Camera(image.size, size, preset, motion, focal, tilt)
        work, factor = working_copy(image, size)
        images = ((work, factor) for _ in range(frames))
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=output.parent, suffix=".mkv")
    os.close(handle)
    vf = f"{STILL_CURVE},{RGB_TO_BT709}" if curve else RGB_TO_BT709
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
                 "-r", str(fps), "-i", "-", "-an", "-vf", vf, "-c:v", "ffv1", "-level", "3", "-slices", "16",
                 "-threads", "4", temporary], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
            timer = Timer(TIMEOUT_SECONDS, process.kill)
            timer.daemon = True
            timer.start()
            try:
                with ThreadPoolExecutor(max_workers=_workers()) as pool:
                    pending: deque = deque()
                    for index, (picture, scale) in enumerate(images):
                        pending.append(pool.submit(frame_at, picture, camera, index, factor=scale))
                        if len(pending) >= 2 * _workers():
                            process.stdin.write(pending.popleft().result().tobytes())
                    while pending:
                        process.stdin.write(pending.popleft().result().tobytes())
                process.stdin.close()
                code = process.wait(timeout=TIMEOUT_SECONDS)
            except BaseException:
                process.kill()
                process.wait()
                raise
            finally:
                timer.cancel()
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            if code:
                errors.seek(0)
                raise RuntimeError("Still camera encode failed: " + errors.read().decode("utf-8", "replace")[-1000:])
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return motion
