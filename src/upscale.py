"""
src/upscale.py
==============
Source-image quality gate and local AI upscaling.

Videos are 1080x1920 and the camera moves zoom in up to ~1.24x, so a still needs a 9:16
crop at least ~1,350 px wide to stay sharp. Landscape photos are cropped heavily: a
1280x960 photo yields only a 540x960 vertical crop (2.5x short).

Images below the bar are upscaled 4x with Real-ESRGAN x4plus, cached under cache/upscaled/,
and downscaled to what the video needs. Backends, in order:
1. ONNX Runtime with the model converted from the official weights
   (tools/models/realesrgan_x4plus.onnx; see docs/upscaler.md): DirectML on the GPU when the
   provider is available, else CPU. Works in the production VM, which exposes DirectML but not
   Vulkan to the guest.
2. realesrgan-ncnn-vulkan (tools/realesrgan), where Vulkan is available.
3. The original image (plain resizing), with a warning.
Real-ESRGAN cleans edges and compression artefacts but cannot recover detail that was never
captured, so higher-resolution sources remain the real fix.
"""

import concurrent.futures
import logging
import subprocess
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image

from .config import BASE_DIR, REALESRGAN_BIN, VIDEO_WIDTH
from .typography import is_video

log = logging.getLogger(__name__)

CACHE_DIR = BASE_DIR / "cache" / "upscaled"
MIN_CROP_WIDTH = int(VIDEO_WIDTH * 1.25)   # zoom headroom
TARGET_CROP_WIDTH = VIDEO_WIDTH * 2         # the composer renders camera moves at 2x
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


def needs_upscale(path: Path) -> bool:
    if is_video(path):
        return False
    with Image.open(path) as im:
        return vertical_crop_width(im.size) < MIN_CROP_WIDTH


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
    digest = fingerprint({"source": file_inputs([path]), "tool": file_inputs(_backend_files(name)),
                          "target_crop_width": TARGET_CROP_WIDTH, "model": "realesrgan-x4plus", "backend": name})[:16]
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
        import onnxruntime as ort
        available = ort.get_available_providers()
        providers = [p for p in ("DmlExecutionProvider", "CPUExecutionProvider") if p in available]
        options = ort.SessionOptions()
        options.log_severity_level = 3
        _session = ort.InferenceSession(str(ONNX_MODEL), sess_options=options, providers=providers)
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
        rgb = np.asarray(im.convert("RGB"))
    return Image.fromarray(upscale_array(rgb))


def _upscale_ncnn(path: Path, tmp: Path) -> Image.Image:
    binary = Path(REALESRGAN_BIN)
    subprocess.run([str(binary), "-i", str(path), "-o", str(tmp), "-n", "realesrgan-x4plus"],
                   cwd=str(binary.parent), check=True, capture_output=True, timeout=600)
    # Our own 4x output of a wide still can exceed Pillow's decompression-bomb limit
    # (a 3840x2160 photo becomes 133 MP); the file is trusted, so lift the limit for it.
    limit, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None
    try:
        im = Image.open(tmp)
        im.load()
    finally:
        Image.MAX_IMAGE_PIXELS = limit
    return im


def ensure_resolution(path: Path) -> Path:
    """Returns a version of `path` sharp enough for the video (cached), or `path` itself."""
    path = Path(path)
    if not needs_upscale(path):
        return path
    for name in [b for b in ("onnx", "ncnn") if _backend_files(b)]:
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
        im = _upscale_onnx(path) if name == "onnx" else _upscale_ncnn(path, tmp)
        with im:
            im = im.convert("RGB")
            # Keep no more than the video can use (a 2x-render crop width), to bound file size.
            scale = min(1.0, TARGET_CROP_WIDTH / max(1, vertical_crop_width(im.size)))
            if scale < 1.0:
                im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
            im.save(out, "JPEG", quality=95)
        log.info("Upscaled %s -> %s (%s)", path.name, out.name, name)
        from .lineage import record_derived
        record_derived(out, [path], "upscale", {"model": "realesrgan-x4plus", "backend": name})
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
