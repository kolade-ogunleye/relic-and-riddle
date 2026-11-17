"""QF-118: the voice and bed mix is deterministic with or without a sample-exact `end`. Without `end`, the duck key's
EOF raced the bed's queued samples inside sidechaincompress, so the bed's level in the same 1 s mix varied from run
to run (side RMS 0.085-0.099 over 35 runs on ffmpeg 8.1; 0.047 once on CI, under the 0.05 the stereo test needs)."""

import hashlib
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from src.video_composer import _audio_chain

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required")


@pytest.mark.parametrize("pad", [0.0, 2.0])
def test_no_input_of_the_sidechain_can_reach_it_with_an_eof(pad):
    chain = _audio_chain(0, 1, pad)
    assert ",apad[bg_carved]" in chain            # the bed runs on without end
    assert ",apad[v_key]" in chain                # and so does the key: the voice's own (finite) pad ends the mix
    assert "amix=inputs=2:duration=first" in chain


def _mix(tmp_path, chain, name):
    out = tmp_path / f"{name}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp_path / "vo.wav"), "-i", str(tmp_path / "bed.wav"),
                    "-filter_complex", chain + "[out]", "-map", "[out]", str(out)],
                   check=True, capture_output=True, timeout=60)
    return out


@needs_ffmpeg
def test_the_same_mix_gives_the_same_samples_every_run(tmp_path):
    sr = 48000
    t = np.arange(sr) / sr
    sf.write(tmp_path / "vo.wav", np.zeros(sr, dtype=np.float32), sr)
    sf.write(tmp_path / "bed.wav", np.column_stack([0.1 * np.sin(2 * np.pi * 440 * t),
                                                    0.1 * np.sin(2 * np.pi * 880 * t)]), sr)
    runs = [_mix(tmp_path, _audio_chain(0, 1), f"open_{k}") for k in range(8)]
    digests = {hashlib.sha256(sf.read(p)[0].tobytes()).hexdigest() for p in runs}
    assert len(digests) == 1
    exact, _ = sf.read(_mix(tmp_path, _audio_chain(0, 1, end=1.0), "exact"))
    mixed, _ = sf.read(runs[0])
    assert mixed.shape == exact.shape == (sr, 2)
    assert np.array_equal(mixed, exact)           # the whole bed, as the sample-exact production mix has it
