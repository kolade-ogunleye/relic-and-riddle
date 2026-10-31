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
from typing import Any, Optional, Set, Union

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
    "hook": (0.16, 0.0, 0.0),
}
SEQUENCE = ["drift_left", "rise", "drift_right", "pull", "drift_left", "push", "drift_right", "rise"]
# The opening shot moves from its first frame (Meta's Creator assistant, 2026-10-28: "the opening shot stays
# relatively still for the first three seconds; viewers decide in under two"). Every other shot eases in and
# out (smoothstep), which starts at rest: half a second into the hook's ~4.2 s clip it had done 4% of its move.
# The opening's own move (HOOK_PRESET, a stronger push) eases out only: fastest at frame 0, 22% done by then.
HOOK_PRESET = "hook"


def ease(t: float, lead: bool = False) -> float:
    """Share of the move done at `t` (0-1): smoothstep, or for the opening shot (`lead`) an ease-out that
    starts at full speed (twice the average) and settles at the end."""
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 2 if lead else t * t * (3 - 2 * t)


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


REPEAT_ZOOM = 1.35  # a repeated photo is framed this much tighter, on another part of the picture
REPEAT_FOCUS = [(0.5, 0.36), (0.5, 0.64), (0.36, 0.5), (0.64, 0.5)]  # centres of the tighter frame, by repeat


def repeat_crop(rgb, out_w: int, out_h: int, variant: int):
    """The frame a photo's `variant`-th repeat uses: tighter, centred on another part of it, so a photo the
    story needs twice reads as a new shot (operator, 2026-10-25: beat 8 reused beat 1's frame exactly)."""
    import cv2
    if variant <= 0:
        return _cover_crop(rgb, out_w, out_h)
    big_w, big_h = int(out_w * REPEAT_ZOOM), int(out_h * REPEAT_ZOOM)
    big = _cover_crop(rgb, big_w, big_h)
    fx, fy = REPEAT_FOCUS[(variant - 1) % len(REPEAT_FOCUS)]
    x0 = int(min(max(0, fx * big_w - out_w / 2), big_w - out_w))
    y0 = int(min(max(0, fy * big_h - out_h / 2), big_h - out_h))
    return cv2.resize(big[y0:y0 + out_h, x0:x0 + out_w], (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)


def preset_for(index: int, total: int) -> str:
    """First and last shots push in (matching loop seam); the rest vary."""
    if index == 0 or index == total - 1:
        return "push"
    return SEQUENCE[(index - 1) % len(SEQUENCE)]


from .encoding import bounded_encode


@bounded_encode
def render_clip(image_path: Path, out_path: Path, duration: float, preset: str = "push",
                fps: int = VIDEO_FPS, size=(VIDEO_WIDTH, VIDEO_HEIGHT), variant: int = 0) -> Path:
    """Renders a parallax clip of `duration` seconds from one still."""
    import cv2
    import numpy as np

    out_w, out_h = size
    bw, bh = int(out_w * OVERSCAN), int(out_h * OVERSCAN)
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise OSError(f"Cannot read {image_path}")
    base = repeat_crop(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), bw, bh, variant)
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
            e = ease(t, lead=preset == HOOK_PRESET)
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
    record_derived(out_path, [image_path], "parallax", {"preset": preset, "duration": duration, "fps": fps, "variant": variant})
    return out_path


def render_scenes(scenes, durations, work_dir: Path, transition: float,
                  exempt_indices: Optional[Any] = None) -> Optional[list]:
    """Parallax clip per still (videos, gap reconstructions and macro drifts pass through). Returns new media list, or None if unavailable."""
    if not available():
        log.info("Parallax unavailable (model or libraries missing); using flat camera moves.")
        return None
    from .typography import is_video
    from concurrent.futures import ThreadPoolExecutor

    from .checkpoints import Checkpoints, file_inputs
    from .lineage import sidecar

    exempt_set = set(exempt_indices or [])
    work_dir.mkdir(parents=True, exist_ok=True)
    _session()  # load the model once before the worker threads share it
    renderer = file_inputs([Path(__file__), MODEL_PATH])

    def clip_for(i, scene, duration, preset, variant=0):
        # A clip is reused (QA re-plan, resume) only for the same still, timing, move, size and
        # renderer, and only while it and its lineage record are intact.
        clip = work_dir / f"scene_{i + 1:02d}.mp4"
        inputs = {"image": file_inputs([Path(scene)]), "duration": duration, "preset": preset,
                  "fps": VIDEO_FPS, "size": [VIDEO_WIDTH, VIDEO_HEIGHT], "renderer": renderer, "variant": variant}
        checkpoint = Checkpoints(work_dir / "stage_cache" / clip.stem)
        if checkpoint.reusable("clip", inputs):
            return clip, False
        render_clip(Path(scene), clip, duration, preset, variant=variant)
        try:
            checkpoint.complete("clip", inputs, [clip, sidecar(clip)])
        except (OSError, ValueError) as e:  # caching is an optimisation; the fresh clip stands
            log.debug("Parallax clip %s not cached: %s", clip.name, e)
        return clip, True

    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as pool:  # OpenCV/NumPy release the GIL while warping
        shown: dict = {}   # photo -> how many times it has been shown
        moved: dict = {}   # photo -> the moves it was shown with
        for i, (scene, dur) in enumerate(zip(scenes, durations)):
            p = Path(scene)
            is_reconstruction = p.stem.startswith(("gap_gen_", "gen_"))
            is_macro = i in exempt_set
            if not is_video(p) and not is_reconstruction and not is_macro:
                # A photo shown again gets another frame and a move it has not had, never the same shot twice
                # (Nazca, 2026-10-25: the repeat was reframed but rose as before).
                variant = shown.get(str(scene), 0)
                shown[str(scene)] = variant + 1
                # The opening shot's move starts at frame 0 (HOOK_PRESET); the composer's transitions still
                # follow preset_for, where it is a push.
                preset = HOOK_PRESET if i == 0 else preset_for(i, len(scenes))
                if variant:
                    had = ["push" if m == HOOK_PRESET else m for m in moved.get(str(scene), ())]  # a push too
                    repeats = [m for m in PRESETS if m != HOOK_PRESET]
                    moves = ([m for m in repeats if m not in had and m != preset]
                             or [m for m in repeats if m not in had] or repeats)
                    preset = moves[(variant - 1 + i) % len(moves)]
                moved.setdefault(str(scene), []).append(preset)
                jobs[i] = pool.submit(clip_for, i, scene, dur + 3 * transition, preset, variant)
        results = {i: job.result() for i, job in jobs.items()}
        out = [results[i][0] if i in results else scene for i, scene in enumerate(scenes)]
    rendered = sum(1 for _, fresh in results.values() if fresh)
    log.info("Parallax scene clips: %d rendered, %d reused unchanged.", rendered, len(results) - rendered)
    return out
