"""Shared encoder detection with a tested CPU fallback."""
import logging
import subprocess
from functools import wraps
from threading import BoundedSemaphore, Lock
from typing import List, Optional
from .config import VIDEO_PRESET, VIDEO_CRF

log = logging.getLogger(__name__)

_DETECTED_ENCODER: Optional[List[str]] = None
_DETECT_LOCK = Lock()  # one probe per process: parallel renderers must agree on the encoder


def _slot_count():
    from . import config
    return config.RENDER_ENCODING_SLOTS


_ENCODING_SLOTS = BoundedSemaphore(_slot_count())


def bounded_encode(function):
    """Bound simultaneous encodes even when renderers use nested thread pools."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _ENCODING_SLOTS:
            return function(*args, **kwargs)
    return wrapped


# BT.709 limited range everywhere (QF-01, 2026-11-05). swscale converts RGB to YUV with BT.601 unless it is told
# otherwise, and the master is tagged BT.709: a pure red graphic measured Y81 U90 V240 in the master (BT.709 red is
# Y63 U102 V240), so players drew every RGB-born picture with the wrong matrix. Every RGB picture is converted with
# the BT.709 matrix where it enters YUV, every clip and frame is tagged BT.709 (ffmpeg 6+ takes the file's colour
# tags from the frames, so setparams marks them; the -color_* flags alone left primaries and transfer "unknown"),
# and a YUV clip is read with the matrix it was made with.
BT709_TAGS = "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
BT709_FLAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
RGB_TO_BT709 = f"scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,{BT709_TAGS}"
BT709_TO_RGB = "scale=in_color_matrix=bt709:in_range=tv"


def source_matrix(colorspace: str | None, height: int = 0) -> str:
    """The YCbCr matrix a YUV clip was made with: its own tag when it has one; untagged, BT.709 for HD (720 lines
    and up, or unknown) and BT.601 below, as players assume."""
    tag = (colorspace or "").strip().lower()
    if tag == "bt709":
        return "bt709"
    if tag in ("bt470bg", "smpte170m", "bt601", "bt470", "fcc"):
        return "bt601"
    if tag.startswith("bt2020"):
        return "bt2020"
    if tag == "smpte240m":
        return "smpte240m"
    return "bt601" if 0 < height < 720 else "bt709"


def to_bt709(colorspace: str | None = None, height: int = 0) -> str:
    """A YUV clip (tag `colorspace`, `height` lines) to BT.709 limited-range yuv420p, tagged; a no-op on BT.709."""
    return (f"scale=in_color_matrix={source_matrix(colorspace, height)}:out_color_matrix=bt709:out_range=tv,"
            f"format=yuv420p,{BT709_TAGS}")


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


def get_intermediate_video_encoder() -> List[str]:
    """Fast, visually-lossless encoder arguments for intermediate scratch renders."""
    enc = get_video_encoder()
    if enc and len(enc) > 1 and enc[1] == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "12"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "10"]


