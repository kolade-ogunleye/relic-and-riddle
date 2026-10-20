import subprocess

import pytest

from src.encoded_audio import finalize_encoded_audio
from src.video_qa import measure_program_loudness


def test_encoded_peak_correction_preserves_video_and_checks_final_aac(tmp_path):
    master = tmp_path / "master.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:r=10:d=2", "-f", "lavfi", "-i",
                    "sine=frequency=997:sample_rate=48000:duration=2", "-af", "volume=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-b:a", "192k", "-ac", "2", "-shortest", str(master)],
                   check=True, capture_output=True, timeout=30)

    def video_hash():
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(master),
                               "-map", "0:v:0", "-c", "copy", "-f", "hash", "-"],
                              check=True, capture_output=True, timeout=30).stdout

    original_video = video_hash()
    before = measure_program_loudness(master)
    target_peak = before["true_peak_dbtp"] - 0.4
    report = finalize_encoded_audio(master, before["integrated_lufs"], target_peak)
    after = measure_program_loudness(master)
    assert report["attenuation_db"] > 0
    assert after["true_peak_dbtp"] <= target_peak
    assert abs(after["integrated_lufs"] - before["integrated_lufs"]) <= 1.0
    assert video_hash() == original_video


def test_out_of_tolerance_encoded_loudness_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr("src.encoded_audio.measure_program_loudness",
                        lambda _: {"integrated_lufs": -18.0, "true_peak_dbtp": -2.0})
    with pytest.raises(ValueError, match="loudness"):
        finalize_encoded_audio(tmp_path / "unused.mp4", -14.0, -1.5)
