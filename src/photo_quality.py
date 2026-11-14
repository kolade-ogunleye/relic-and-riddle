"""Byte-bound technical measurements; visual review interprets scene-dependent flags."""

import hashlib
import io
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


def inspect_bytes(data: bytes) -> dict:
    with Image.open(io.BytesIO(data)) as raw:
        tables = getattr(raw, "quantization", None) or {}
        quant = [value for table in tables.values() for value in table]
        image = ImageOps.exif_transpose(raw).convert("RGB")
        dimensions = list(image.size)
        image.thumbnail((768, 768), Image.Resampling.LANCZOS)
        rgb = np.asarray(image, dtype=np.float32)
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    means = rgb.mean(axis=(0, 1))
    cast = float((means.max() - means.min()) / max(1.0, float(means.mean())))
    sharpness = float(cv2.Laplacian(grey, cv2.CV_32F).var())
    highlights = float(np.mean(np.all(rgb >= 250, axis=2)))
    shadows = float(np.mean(np.all(rgb <= 5, axis=2)))
    quant_mean = float(np.mean(quant)) if quant else None
    flags = []
    if sharpness < 25:
        flags.append("low edge sharpness")
    if highlights > 0.10:
        flags.append("clipped highlights")
    if shadows > 0.15:
        flags.append("blocked shadows")
    if cast > 0.30:
        flags.append("possible colour cast")
    if quant_mean is not None and quant_mean > 40:
        flags.append("coarse JPEG quantization")
    return {"version": 1, "source_sha256": hashlib.sha256(data).hexdigest(),
            "dimensions": dimensions, "analysis_dimensions": list(image.size),
            "laplacian_variance": round(sharpness, 4), "clipped_highlights": round(highlights, 6),
            "blocked_shadows": round(shadows, 6), "grey_world_cast": round(cast, 6),
            "jpeg_quantization_mean": round(quant_mean, 4) if quant_mean is not None else None,
            "flags": flags}


def record_source(path: Path, data: bytes) -> None:
    """Preserve source compression evidence before the pool's upright, metadata-stripping derivative."""
    result = inspect_bytes(data)
    result["pool_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    Path(str(path) + ".quality.json").write_text(json.dumps(result), encoding="utf-8")


def inspect_path(path: Path) -> dict:
    data = Path(path).read_bytes()
    try:
        saved = json.loads(Path(str(path) + ".quality.json").read_text(encoding="utf-8"))
        if saved.get("version") == 1 and saved.get("pool_sha256") == hashlib.sha256(data).hexdigest():
            return {**saved, "basis": "downloaded source bytes"}
    except (OSError, ValueError, AttributeError):
        pass
    return {**inspect_bytes(data), "basis": "current pool bytes; original encoding unavailable"}
