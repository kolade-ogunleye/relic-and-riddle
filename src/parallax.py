"""
src/parallax.py
===============
2.5D parallax ("3D photo") camera moves for still images.

Depth Anything V2 Small (Apache-2.0; the Base/Large weights are non-commercial) estimates
per-pixel depth on the local GPU via ONNX Runtime + DirectML. Each frame warps the photo
so near surfaces move more than far ones while the virtual camera pushes in or drifts.
The result is a short clip per scene that the video composer cuts together.

Falls back (available() == False) when the model file or libraries are missing; the
composer then uses its flat zoom-and-pan.
"""

import logging
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .config import BASE_DIR, VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH

log = logging.getLogger(__name__)

MODEL_PATH = BASE_DIR / "tools" / "models" / "depth_anything_v2_small.onnx"
_INFERENCE_LOCK = threading.Lock()
OVERSCAN = 1.18          # source crop is larger than the frame so moves never reveal edges
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)

# (zoom_in, shift_x, shift_y) per preset; shifts are in output pixels at full parallax.
PRESETS = {
    "push": (0.10, 0.0, 0.0),
    "drift_left": (0.04, -70.0, 0.0),
    "drift_right": (0.04, 70.0, 0.0),
    "rise": (0.05, 0.0, -60.0),
    "pull": (-0.08, 0.0, 0.0),
}
SEQUENCE = ["drift_left", "rise", "drift_right", "pull", "drift_left", "push", "drift_right", "rise"]


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return MODEL_PATH.exists()


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort
    providers = [p for p in ("DmlExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
    return ort.InferenceSession(str(MODEL_PATH), providers=providers)


def depth_map(rgb):
    """Relative depth in [0, 1] (1 = nearest) at the image's size, smoothed to avoid tearing."""
    import cv2
    import numpy as np

    h, w = rgb.shape[:2]
    scale = 518 / max(h, w)
    nh, nw = max(14, int(h * scale) // 14 * 14), max(14, int(w * scale) // 14 * 14)
    x = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = (x - np.array(_MEAN, np.float32)) / np.array(_STD, np.float32)
    x = x.transpose(2, 0, 1)[None]
    # DirectML sessions crash (segfault) on concurrent run() calls; inference is quick,
    # so serialise it and keep only the frame warping parallel.
    with _INFERENCE_LOCK:
        out = _session().run(None, {"pixel_values": x})[0][0]
    d = cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC)
    d = (d - d.min()) / max(1e-6, float(d.max() - d.min()))
    return cv2.GaussianBlur(d.astype(np.float32), (0, 0), sigmaX=max(2.0, w / 400))


def _cover_crop(rgb, out_w: int, out_h: int):
    import cv2
    h, w = rgb.shape[:2]
    target = out_w / out_h
    if w / h > target:
        cw, ch = int(h * target), h
    else:
        cw, ch = w, int(w / target)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    return cv2.resize(rgb[y0:y0 + ch, x0:x0 + cw], (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)


def preset_for(index: int, total: int) -> str:
    """First and last shots push in (matching loop seam); the rest vary."""
    if index == 0 or index == total - 1:
        return "push"
    return SEQUENCE[(index - 1) % len(SEQUENCE)]


from .encoding import bounded_encode


@bounded_encode
def render_clip(image_path: Path, out_path: Path, duration: float, preset: str = "push",
                fps: int = VIDEO_FPS, size=(VIDEO_WIDTH, VIDEO_HEIGHT)) -> Path:
    """Renders a parallax clip of `duration` seconds from one still."""
    import cv2
    import numpy as np

    out_w, out_h = size
    bw, bh = int(out_w * OVERSCAN), int(out_h * OVERSCAN)
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise OSError(f"Cannot read {image_path}")
    base = _cover_crop(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), bw, bh)
    depth = depth_map(base)

    zoom_amt, shift_x, shift_y = PRESETS[preset]
    ys, xs = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
    u, v = xs - out_w / 2, ys - out_h / 2
    cx, cy = bw / 2, bh / 2
    s0 = 1.0 / OVERSCAN * (bw / out_w)  # output pixel -> base pixel at rest (fills the frame)
    d = cv2.remap(depth, (cx + u * s0).astype(np.float32), (cy + v * s0).astype(np.float32), cv2.INTER_LINEAR)
    # 2.5D Depth Displacement: Slicing foreground stone doorways from background tunnels for 3D travel with occlusion
    d_norm = np.clip(d, 0.0, 1.0)
    fg_mask = np.clip((d_norm - 0.50) / 0.50, 0.0, 1.0)
    weight = 0.20 + 0.80 * (d_norm ** 1.35) + 0.25 * (fg_mask ** 2)

    frames = max(2, int(round(duration * fps)))
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}",
         "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "14",
         "-pix_fmt", "yuv420p", str(out_path)],
        stdin=subprocess.PIPE)
    try:
        for i in range(frames):
            t = i / (frames - 1)
            e = t * t * (3 - 2 * t)  # smoothstep easing
            z = 1.0 + zoom_amt * (e if zoom_amt > 0 else e - 1) * weight
            scale = s0 / np.maximum(z, 0.5)
            map_x = cx + u * scale + shift_x * (e - 0.5) * weight
            map_y = cy + v * scale + shift_y * (e - 0.5) * weight
            frame = cv2.remap(base, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        proc.wait(timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed encoding {out_path.name}")
    from .lineage import record_derived
    record_derived(out_path, [image_path], "parallax", {"preset": preset, "duration": duration, "fps": fps})
    return out_path


def render_scenes(scenes, durations, work_dir: Path, transition: float) -> Optional[list]:
    """Parallax clip per still (videos pass through). Returns new media list, or None if unavailable."""
    if not available():
        log.info("Parallax unavailable (model or libraries missing); using flat camera moves.")
        return None
    from .typography import is_video
    from concurrent.futures import ThreadPoolExecutor

    work_dir.mkdir(parents=True, exist_ok=True)
    _session()  # load the model once before the worker threads share it
    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as pool:  # OpenCV/NumPy release the GIL while warping
        for i, (scene, dur) in enumerate(zip(scenes, durations)):
            if not is_video(scene):
                clip = work_dir / f"scene_{i + 1:02d}.mp4"
                jobs[i] = pool.submit(render_clip, Path(scene), clip, dur + 3 * transition, preset_for(i, len(scenes)))
        out = [jobs[i].result() if i in jobs else scene for i, scene in enumerate(scenes)]
    log.info("Rendered %d parallax scene clips.", len(jobs))
    return out
