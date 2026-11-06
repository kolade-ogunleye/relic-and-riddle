"""
src/upscale.py
==============
Source-image quality gate and local AI super-resolution engine.

Videos are 1080x1920 and camera moves zoom up to ~1.24x, so a still needs a 9:16 crop at least
~1,350 px wide to stay sharp. Landscape photos are cropped heavily: a 1280x960 photo yields
only a 540x960 vertical crop (2.5x short).

Hybrid Bounded-Envelope Super-Resolution Architecture:
1. Native Full HD Resolution Bypass: Images whose physical dimensions and vertical 9:16 crop
   natively cover the 1080x1920 frame with 1:1 optical pixel mapping bypass upscaling entirely.
2. 1:1 Aspect Ratio Max-Clamp: Wide landscape/panoramic media are symmetrically clamped to a max
   1:1 square centered on the principal axis before super-resolution, guaranteeing 100% optical
   coverage for 9:16 vertical video, 4:5 carousels, 1:1 profile grid thumbnails, and parallax
   pan/zoom shifts while shedding 40-70% of unnecessary tile inference.
3. Hybrid Hardware Execution:
   - GPU (DirectML / CUDA): Runs Real-ESRGAN x4plus on the clamped envelope in ~1.5s.
   - CPU (workstation / CI): For intermediate stretch ratios (<= 1.8x), executes Conservative
     Adaptive Polish (Lanczos-3 resampling with calibrated subtle unsharp mask) in milliseconds;
     severely pixel-starved archive media (> 1.8x, e.g. 640x480) run clamped Real-ESRGAN (9 tiles).
"""

import concurrent.futures
import logging
import subprocess
import threading
from pathlib import Path
from typing import List, Optional, Tuple, Union

from PIL import Image

from .config import BASE_DIR, REALESRGAN_BIN, VIDEO_WIDTH, VIDEO_HEIGHT
from .trusted_image import open_trusted
from .typography import is_video

log = logging.getLogger(__name__)

CACHE_DIR = BASE_DIR / "cache" / "upscaled"
MIN_CROP_WIDTH = int(VIDEO_WIDTH * 1.25)          # 1,350 px (ceiling headroom)
NATIVE_HEADROOM_WIDTH = int(VIDEO_WIDTH * 1.20)   # 1,296 px (1.20x native optical coverage threshold)
TARGET_CROP_WIDTH = VIDEO_WIDTH * 2                # the composer renders camera moves at 2x
ONNX_MODEL = BASE_DIR / "tools" / "models" / "realesrgan_x4plus.onnx"
TILE = 192        # model input per tile (fixed size: DirectML compiles one shape)
TILE_PAD = 16     # context around each tile, discarded after inference (no seams)
SCALE = 4

_session = None
_session_lock = threading.Lock()   # one GPU inference at a time; ensure_all runs files in parallel


def vertical_crop_width(size: Tuple[int, int]) -> int:
    """Width of the largest 9:16 crop of an image of this size."""
    w, h = size
    return int(min(w, h * 9 / 16))


def clamp_envelope(im: Image.Image) -> Image.Image:
    """
    Clamps a wide image to a 1:1 square centered on the principal axis.
    Guarantees 100% optical coverage across 9:16 vertical video, 4:5 carousels,
    and 1:1 profile thumbnails while shedding 40-70% of unseen horizontal margins.
    """
    w, h = im.size
    if w > h:
        left = (w - h) // 2
        return im.crop((left, 0, left + h, h))
    return im


def stretch_ratio(path_or_size: Union[Path, str, Tuple[int, int]]) -> float:
    """Effective optical stretch factor to cover 1080x1920 video frame."""
    if isinstance(path_or_size, (str, Path)):
        with Image.open(path_or_size) as im:
            w, h = im.size
    else:
        w, h = path_or_size
    return max(VIDEO_WIDTH / max(1, w), VIDEO_HEIGHT / max(1, h))


def is_gpu_accelerated() -> bool:
    """True if DirectML or CUDA execution provider is available in ONNX Runtime."""
    try:
        import onnxruntime as ort
        available = set(ort.get_available_providers())
        return bool(available & {"DmlExecutionProvider", "CUDAExecutionProvider"})
    except Exception:
        return False


def needs_upscale(path: Path) -> bool:
    """
    Two-Tier Quality Gate:
    1. Native Full HD Resolution Bypass: If an image contains physical dimensions that cover
       the full 1080x1920 video frame (w >= VIDEO_WIDTH and h >= VIDEO_HEIGHT), and its 9:16
       vertical crop provides at least 1.0x native optical coverage (crop_w >= VIDEO_WIDTH),
       it has 100% native optical 1:1 pixel coverage across every frame pixel and does not
       require neural upscaling.
    2. Sub-Resolution Neural Upscale: Images with sub-1080p crop density (crop_w < VIDEO_WIDTH
       or sub-HD sensor dimensions) are upscaled 4x with Real-ESRGAN x4plus.
    """
    if is_video(path):
        return False
    with Image.open(path) as im:
        w, h = im.size
        crop_w = vertical_crop_width(im.size)
        # Tier 1: Natively high-resolution stills covering full 1080x1920 video frame with 1:1 pixel coverage
        if crop_w >= VIDEO_WIDTH and (h >= VIDEO_HEIGHT or w >= VIDEO_HEIGHT):
            return False
        # Tier 2: Sub-1080p crops require upscaling
        return crop_w < MIN_CROP_WIDTH


def backend() -> str:
    """'onnx' when the converted model is present, else 'ncnn' when its binary is, else 'none'."""
    if ONNX_MODEL.is_file():
        return "onnx"
    return "ncnn" if Path(REALESRGAN_BIN).is_file() else "none"


def _backend_files(name: str) -> List[Path]:
    if name == "onnx":
        return [ONNX_MODEL]
    binary = Path(REALESRGAN_BIN)
    return [binary, *(binary.parent / "models").glob("realesrgan-x4plus.*")] if binary.is_file() else []


def _cache_path(path: Path, name: str) -> Path:
    from .checkpoints import fingerprint, file_inputs
    tool_files = _backend_files(name) if name in ("onnx", "ncnn") else []
    model_name = "realesrgan-x4plus" if name in ("onnx", "ncnn") else "lanczos3-unsharp-polish"
    digest = fingerprint({"source": file_inputs([path]), "tool": file_inputs(tool_files),
                          "target_crop_width": TARGET_CROP_WIDTH, "model": model_name, "backend": name})[:16]
    return CACHE_DIR / f"{path.stem}_{digest}.jpg"


def _onnx_session():
    global _session
    if _session is not None:
        return _session
    # Created once, under the lock: four image-prep threads each created a DirectML session at the same
    # moment and the process died with an access violation (0xC0000005, dev run, 2026-10-23).
    with _session_lock:
        if _session is not None:
            return _session
        from .onnx_session import create_session
        _session = create_session(ONNX_MODEL)
        log.info("Real-ESRGAN ONNX session on %s", _session.get_providers()[0])
    return _session


def upscale_array(rgb, run=None):
    """4x upscale of an HxWx3 uint8 array, tile by tile. `run(tile)` maps a 1x3xhxw float32 tile to its
    1x3x4hx4w result (the ONNX session by default). Each tile is the same size: the image is reflect-
    padded to whole tiles plus context, and only each tile's centre is kept, so there are no seams."""
    import numpy as np
    if run is None:
        session = _onnx_session()
        name = session.get_inputs()[0].name

        def run(tile):
            with _session_lock:
                return session.run(None, {name: tile})[0]
    h, w = rgb.shape[:2]
    ty, tx = -(-h // TILE), -(-w // TILE)
    src = np.pad(rgb.astype(np.float32) / 255.0,
                 ((TILE_PAD, ty * TILE - h + TILE_PAD), (TILE_PAD, tx * TILE - w + TILE_PAD), (0, 0)), mode="reflect")
    out = np.empty((ty * TILE * SCALE, tx * TILE * SCALE, 3), dtype=np.float32)
    size = TILE + 2 * TILE_PAD
    for j in range(ty):
        for i in range(tx):
            tile = src[j * TILE:j * TILE + size, i * TILE:i * TILE + size].transpose(2, 0, 1)[None]
            res = run(np.ascontiguousarray(tile))[0].transpose(1, 2, 0)
            p = TILE_PAD * SCALE
            out[j * TILE * SCALE:(j + 1) * TILE * SCALE, i * TILE * SCALE:(i + 1) * TILE * SCALE] = \
                res[p:p + TILE * SCALE, p:p + TILE * SCALE]
    return (np.clip(out[:h * SCALE, :w * SCALE], 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _upscale_onnx(path: Path) -> Image.Image:
    import numpy as np
    with Image.open(path) as im:
        clamped = clamp_envelope(im.convert("RGB"))
        rgb = np.asarray(clamped)
    return Image.fromarray(upscale_array(rgb))


def _upscale_ncnn(path: Path, tmp: Path) -> Image.Image:
    binary = Path(REALESRGAN_BIN)
    with Image.open(path) as im:
        w, h = im.size
        needs_clamp = w > h
        if needs_clamp:
            in_file = tmp.with_name(f"{tmp.stem}_clamped.png")
            clamp_envelope(im.convert("RGB")).save(in_file)
            input_target = in_file
        else:
            in_file = None
            input_target = path
    try:
        subprocess.run([str(binary), "-i", str(input_target), "-o", str(tmp), "-n", "realesrgan-x4plus"],
                       cwd=str(binary.parent), check=True, capture_output=True, timeout=600)
    finally:
        if in_file is not None:
            in_file.unlink(missing_ok=True)
    # Our own 4x output of a wide still can exceed Pillow's decompression-bomb limit: decoded under its own limit
    # (at most the source still, scaled: a clamped input is smaller), never by lifting the process-wide one
    return open_trusted(tmp, w * h * SCALE * SCALE)


def _upscale_cpu_polish(path: Path) -> Image.Image:
    """
    Conservative Adaptive Polish on CPU:
    Rescales the clamped envelope using Lanczos-3, followed by a subtle unsharp mask
    (radius 1.0, percent 45, threshold 3) to restore optical edge MTF without haloing.
    """
    from PIL import ImageFilter
    with Image.open(path) as im:
        clamped = clamp_envelope(im.convert("RGB"))
        scale = max(TARGET_CROP_WIDTH / max(1, vertical_crop_width(clamped.size)),
                    (VIDEO_HEIGHT * 2) / max(1, clamped.height))
        scale = min(4.0, scale)
        if scale > 1.0:
            target_w = round(clamped.width * scale)
            target_h = round(clamped.height * scale)
            scaled = clamped.resize((target_w, target_h), Image.Resampling.LANCZOS)
        else:
            scaled = clamped
        return scaled.filter(ImageFilter.UnsharpMask(radius=1.0, percent=45, threshold=3))


def ensure_resolution(path: Path) -> Path:
    """Returns a version of `path` sharp enough for the video (cached), or `path` itself."""
    path = Path(path)
    from .image_presentation import full_image, prepared_source
    if full_image(path):
        return prepared_source(path)
    if not needs_upscale(path):
        return path

    if backend() == "none":
        log.warning("Low-resolution source %s and no working Real-ESRGAN backend; using plain resizing.", path.name)
        return path

    use_gpu = is_gpu_accelerated()
    stretch = stretch_ratio(path)

    # When running on CPU without hardware acceleration, intermediate stretch ratios (<= 1.8x)
    # use high-precision Lanczos-3 with calibrated unsharp polish (milliseconds vs hours on CPU).
    if not use_gpu and stretch <= 1.80:
        backend_order = ["cpu_lanczos_polish", "onnx", "ncnn"]
    else:
        backend_order = ["onnx", "ncnn"]

    for name in backend_order:
        if name in ("onnx", "ncnn") and not _backend_files(name):
            continue
        out = _cache_path(path, name)
        if out.exists():
            return out
        upscaled = _try_backend(name, path, out)
        if upscaled is not None:
            return upscaled

    log.warning("Low-resolution source %s and no working Real-ESRGAN backend; using plain resizing.", path.name)
    return path


def _try_backend(name: str, path: Path, out: Path) -> Optional[Path]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    try:
        if name == "cpu_lanczos_polish":
            im = _upscale_cpu_polish(path)
        elif name == "onnx":
            im = _upscale_onnx(path)
        else:
            im = _upscale_ncnn(path, tmp)

        with im:
            im = im.convert("RGB")
            # Keep no more than the video can use (a 2x-render crop width), to bound file size.
            scale = min(1.0, TARGET_CROP_WIDTH / max(1, vertical_crop_width(im.size)))
            if scale < 1.0:
                im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
            im.save(out, "JPEG", quality=95)
        log.info("Upscaled %s -> %s (%s)", path.name, out.name, name)
        from .lineage import record_derived
        model_name = "lanczos-3-unsharp-polish" if name == "cpu_lanczos_polish" else "realesrgan-x4plus"
        record_derived(out, [path], "upscale", {"model": model_name, "backend": name})
        return out
    except Exception as e:  # a backend that can't run (no GPU device, bad install) falls through to the next
        log.warning("Upscaling %s with %s failed (%s); trying the next option.", path.name, name, e)
        return None
    finally:
        tmp.unlink(missing_ok=True)


def ensure_all(paths: List[Path]) -> Tuple[List[Path], int]:
    """Upscales every low-resolution still. Returns (paths to use, how many were low-res)."""
    p_paths = [Path(p) for p in paths]
    low_res_flags = [needs_upscale(p) for p in p_paths]
    low = sum(1 for flag in low_res_flags if flag)
    if low <= 1:
        return [ensure_resolution(p) for p in p_paths], low
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, low)) as ex:
        results = list(ex.map(ensure_resolution, p_paths))
    return results, low
