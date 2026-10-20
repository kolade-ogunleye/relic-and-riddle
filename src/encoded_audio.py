"""Measure the delivered AAC, including codec overshoot, before certifying a master."""
import os
import subprocess
import tempfile
from pathlib import Path

from .video_qa import measure_program_loudness


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
            return {"measurements": measurements, "attenuation_db": attenuation}
        reduction = measured["true_peak_dbtp"] - target_true_peak + 0.3
        if attempt == 2 or measured["integrated_lufs"] - reduction < target_lufs - 1.0:
            raise ValueError("Encoded peak correction cannot satisfy loudness and peak limits")
        handle, temporary = tempfile.mkstemp(dir=master.parent, suffix=".mp4")
        os.close(handle)
        try:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(master),
                            "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                            "-af", f"volume=-{reduction:.3f}dB", "-c:a", "aac",
                            "-b:a", "192k", "-ar", "48000", "-ac", "2",
                            "-movflags", "+faststart", temporary],
                           check=True, capture_output=True, timeout=180)
            # Measure the candidate before replacing the existing encoded master.
            candidate = measure_program_loudness(Path(temporary))
            if abs(candidate["integrated_lufs"] - target_lufs) > 1.0:
                raise ValueError("Encoded correction exceeded loudness tolerance")
            os.replace(temporary, master)
            attenuation += reduction
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
