"""
src/upscale.py
==============
Source-image quality gate and local AI upscaling.

Videos are 1080x1920 and the camera moves zoom in up to ~1.24x, so a still needs a 9:16
crop at least ~1,350 px wide to stay sharp. Landscape photos are cropped heavily: a
1280x960 photo yields only a 540x960 vertical crop (2.5x short).

Images below the bar are upscaled 4x with Real-ESRGAN (realesrgan-ncnn-vulkan, runs on
the local GPU), cached under cache/upscaled/, and downscaled to what the video needs.
Real-ESRGAN cleans edges and compression artefacts but cannot recover detail that was
never captured, so higher-resolution sources remain the real fix. Without the tool,
the original image is used (plain resizing) and a warning is logged.
"""

import concurrent.futures
import logging
import subprocess
from pathlib import Path
from typing import List, Tuple

from PIL import Image

from .config import BASE_DIR, REALESRGAN_BIN, VIDEO_WIDTH
from .typography import is_video

log = logging.getLogger(__name__)

CACHE_DIR = BASE_DIR / "cache" / "upscaled"
MIN_CROP_WIDTH = int(VIDEO_WIDTH * 1.25)   # zoom headroom
TARGET_CROP_WIDTH = VIDEO_WIDTH * 2         # the composer renders camera moves at 2x


def vertical_crop_width(size: Tuple[int, int]) -> int:
    """Width of the largest 9:16 crop of an image of this size."""
    w, h = size
    return int(min(w, h * 9 / 16))


def needs_upscale(path: Path) -> bool:
    if is_video(path):
        return False
    with Image.open(path) as im:
        return vertical_crop_width(im.size) < MIN_CROP_WIDTH


def _cache_path(path: Path) -> Path:
    from .checkpoints import fingerprint, file_inputs
    binary = Path(REALESRGAN_BIN)
    model_files = list((binary.parent / "models").glob("realesrgan-x4plus.*")) if binary.is_file() else []
    digest = fingerprint({"source": file_inputs([path]), "tool": file_inputs([binary, *model_files]),
                          "target_crop_width": TARGET_CROP_WIDTH, "model": "realesrgan-x4plus"})[:16]
    return CACHE_DIR / f"{path.stem}_{digest}.jpg"


def ensure_resolution(path: Path) -> Path:
    """Returns a version of `path` sharp enough for the video (cached), or `path` itself."""
    path = Path(path)
    if not needs_upscale(path):
        return path
    out = _cache_path(path)
    if out.exists():
        return out
    binary = Path(REALESRGAN_BIN)
    if not binary.exists():
        log.warning("Low-resolution source %s and no Real-ESRGAN at %s; using plain resizing.", path.name, binary)
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    try:
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
        with im:
            im = im.convert("RGB")
            # Keep no more than the video can use (a 2x-render crop width), to bound file size.
            scale = min(1.0, TARGET_CROP_WIDTH / max(1, vertical_crop_width(im.size)))
            if scale < 1.0:
                im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
            im.save(out, "JPEG", quality=95)
        log.info("Upscaled %s -> %s", path.name, out.name)
        from .lineage import record_derived
        record_derived(out, [path], "upscale", {"model": "realesrgan-x4plus"})
        return out
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("Upscaling %s failed (%s); using the original.", path.name, e)
        return path
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
