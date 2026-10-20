"""Shared encoder detection with a tested CPU fallback."""
import logging
import os
import subprocess
from functools import wraps
from threading import BoundedSemaphore, Lock
from typing import List, Optional
from .config import VIDEO_PRESET, VIDEO_CRF

log = logging.getLogger(__name__)

_DETECTED_ENCODER: Optional[List[str]] = None
_DETECT_LOCK = Lock()  # one probe per process: parallel renderers must agree on the encoder


def _slot_count():
    try:
        return max(1, min(8, int(os.getenv("RENDER_ENCODING_SLOTS", "2"))))
    except ValueError:
        return 2


_ENCODING_SLOTS = BoundedSemaphore(_slot_count())


def bounded_encode(function):
    """Bound simultaneous encodes even when renderers use nested thread pools."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _ENCODING_SLOTS:
            return function(*args, **kwargs)
    return wrapped


def get_video_encoder() -> List[str]:
    """Detect if h264_nvenc hardware acceleration is available, falling back to libx264."""
    global _DETECTED_ENCODER
    if _DETECTED_ENCODER is not None:
        return _DETECTED_ENCODER
    with _DETECT_LOCK:
        if _DETECTED_ENCODER is not None:
            return _DETECTED_ENCODER
        try:
            res = subprocess.run(
                ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21", "-f", "null", "-"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            if res.returncode == 0:
                log.info("Hardware acceleration enabled: h264_nvenc (NVIDIA GPU)")
                _DETECTED_ENCODER = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21"]
                return _DETECTED_ENCODER
        except Exception:
            pass
        log.info("Hardware acceleration fallback: libx264 (CPU)")
        _DETECTED_ENCODER = ["-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF)]
        return _DETECTED_ENCODER

