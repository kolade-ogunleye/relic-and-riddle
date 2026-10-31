"""Measure the delivered AAC, including codec overshoot, before certifying a master."""
import logging
import math
import os
import subprocess
import tempfile
from pathlib import Path

from .video_qa import measure_program_loudness

log = logging.getLogger(__name__)

MASTER_LIMIT = 0.80   # the master chain's limiter ceiling, linear (about -1.9 dBFS, limited at 4x oversampling)
LOWPASS_HZ = 16000    # AAC at 192 kb/s drops the band above this: removed before limiting, it can't ring over the ceiling
# When turning the encode down would leave it too quiet, the limiter's ceiling is lowered once instead, by the
# measured excess and a margin, within these bounds: deeper than that squashes the programme audibly.
CEILING_STEP_MIN_DB = 0.5
CEILING_STEP_MAX_DB = 1.5
_MARGIN_DB = 0.3
_SAMPLE_RATE = 48000


def _encode(master: Path, audio_filter: str) -> str:
    """The master with its audio re-encoded through `audio_filter` (the video copied), as a temporary file beside
    it, to be measured before anything is replaced. Returns its path; the caller replaces or removes it."""
    handle, temporary = tempfile.mkstemp(dir=master.parent, suffix=".mp4")
    os.close(handle)
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(master),
                        "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                        "-af", audio_filter, "-c:a", "aac",
                        "-b:a", "192k", "-ar", str(_SAMPLE_RATE), "-ac", "2",
                        "-movflags", "+faststart", temporary],
                       check=True, capture_output=True, timeout=180)
    except BaseException:
        os.unlink(temporary)
        raise
    return temporary


def _lower_ceiling_once(master: Path, measured, measurements, attenuation: float,
                        target_lufs: float, target_true_peak: float):
    """One re-encode through the master's limiter with its ceiling a small step lower, re-measured; the limits
    are still required of the result (Roman concrete, 2026-10-28: an overshoot that a volume cut could only fix
    by leaving the master too quiet failed the run outright)."""
    excess = measured["true_peak_dbtp"] - target_true_peak
    step = min(CEILING_STEP_MAX_DB, max(CEILING_STEP_MIN_DB, excess + _MARGIN_DB))
    ceiling_db = 20 * math.log10(MASTER_LIMIT) - attenuation - step
    audio_filter = (f"lowpass=f={LOWPASS_HZ}:poles=2,aresample={_SAMPLE_RATE * 4},"
                    f"alimiter=limit={10 ** (ceiling_db / 20):.4f}:attack=2.5:release=80:level=disabled:asc=1,"
                    f"aresample={_SAMPLE_RATE}")
    log.info("Encoded audio peaks at %.2f dBTP (limit %.2f); limiting once more at %.2f dBFS.",
             measured["true_peak_dbtp"], target_true_peak, ceiling_db)
    temporary = _encode(master, audio_filter)
    try:
        candidate = measure_program_loudness(Path(temporary))
        measurements.append(candidate)
        if (abs(candidate["integrated_lufs"] - target_lufs) > 1.0
                or candidate["true_peak_dbtp"] > target_true_peak):
            raise ValueError("Encoded peak correction cannot satisfy loudness and peak limits "
                             f"(lowered limiter: {candidate['integrated_lufs']:.1f} LUFS, "
                             f"{candidate['true_peak_dbtp']:.1f} dBTP)")
        os.replace(temporary, master)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"measurements": measurements, "attenuation_db": attenuation, "limiter_ceiling_db": round(ceiling_db, 2)}


def finalize_encoded_audio(master, target_lufs, target_true_peak):
    master = Path(master)
    measurements = []
    attenuation = 0.0
    for attempt in range(3):
        measured = measure_program_loudness(master)
        measurements.append(measured)
        if abs(measured["integrated_lufs"] - target_lufs) > 1.0:
            raise ValueError("Encoded loudness exceeds delivery tolerance")
        if measured["true_peak_dbtp"] <= target_true_peak:
            return {"measurements": measurements, "attenuation_db": attenuation, "limiter_ceiling_db": None}
        reduction = measured["true_peak_dbtp"] - target_true_peak + _MARGIN_DB
        if attempt == 2 or measured["integrated_lufs"] - reduction < target_lufs - 1.0:
            return _lower_ceiling_once(master, measured, measurements, attenuation, target_lufs, target_true_peak)
        temporary = _encode(master, f"volume=-{reduction:.3f}dB")
        try:
            # Measure the candidate before replacing the existing encoded master.
            candidate = measure_program_loudness(Path(temporary))
            if abs(candidate["integrated_lufs"] - target_lufs) > 1.0:
                raise ValueError("Encoded correction exceeded loudness tolerance")
            os.replace(temporary, master)
            attenuation += reduction
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
