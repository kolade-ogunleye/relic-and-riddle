"""
tests/test_audio_engine_v3.py
=============================
Phase 3 Cinematic Audio & Diegetic Foley Engine Test Suite.
Tests:
1. 4-Band Linkwitz-Riley spectral decomposition and perfect reconstruction.
2. Narrative revelation pattern interrupts (-18 dB rhythm vacuum and swells).
3. Foley category spectral filtering and spatial environment convolution reverb.
4. Neural TTS prosody directing (SSML micro-pauses and cadence ellipses).
5. End-to-end ambient bed generation with adaptive 4-stem scoring.
"""

from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from src.audio_engine import AudioEngine
from src.foley_engine import FoleyEngine, EFFECT_CATEGORIES
from src.stem_composer import StemComposer
from src.voice_prosody import VoiceProsody


@pytest.fixture
def sample_rate():
    return 48000


def test_4band_crossover_perfect_reconstruction(sample_rate):
    """Verifies that decomposing stereo audio into 4 bands and summing them
    reconstructs the original audio with near-zero error across all frequencies."""
    n_samples = sample_rate * 2
    t = np.arange(n_samples) / sample_rate

    # Generate a complex multi-frequency stereo test signal (sub, low-mid, mid-high, treble)
    sig_l = (
        0.3 * np.sin(2 * np.pi * 55.0 * t)     # sub
        + 0.3 * np.sin(2 * np.pi * 440.0 * t)   # rhythm/low-mid
        + 0.2 * np.sin(2 * np.pi * 2000.0 * t)  # melody
        + 0.1 * np.sin(2 * np.pi * 8000.0 * t)  # air
    ).astype(np.float32)
    sig_r = np.roll(sig_l, 100)
    audio = np.column_stack((sig_l, sig_r))

    stems = StemComposer.decompose_stems(audio, sr=sample_rate)

    assert set(stems.keys()) == {"sub_bass", "rhythm", "melody", "air"}
    for band_name, band_data in stems.items():
        assert band_data.shape == audio.shape
        assert np.all(np.isfinite(band_data))

    # Reconstructed sum
    reconstructed = stems["sub_bass"] + stems["rhythm"] + stems["melody"] + stems["air"]

    # In the steady-state region (ignoring tiny filter boundary transients at t=0 and t=end)
    margin = int(0.05 * sample_rate)
    diff = np.abs(audio[margin:-margin] - reconstructed[margin:-margin])
    max_err = np.max(diff)

    # Must be less than 0.05 absolute amplitude difference (magnitude response unity)
    assert max_err < 0.05, f"Reconstruction error too high: {max_err}"


def test_4band_frequency_separation(sample_rate):
    """Verifies that each stem contains the intended spectral content."""
    n_samples = sample_rate * 1
    t = np.arange(n_samples) / sample_rate

    # Pure 60 Hz tone (Sub-bass)
    sub_tone = np.column_stack((np.sin(2 * np.pi * 60.0 * t), np.sin(2 * np.pi * 60.0 * t))).astype(np.float32)
    stems_sub = StemComposer.decompose_stems(sub_tone, sr=sample_rate)
    rms_sub = np.sqrt(np.mean(np.square(stems_sub["sub_bass"])))
    rms_air = np.sqrt(np.mean(np.square(stems_sub["air"])))
    assert rms_sub > 0.5
    assert rms_air < 0.01  # Negligible bleed into air band

    # Pure 10 kHz tone (Air band)
    air_tone = np.column_stack((np.sin(2 * np.pi * 10000.0 * t), np.sin(2 * np.pi * 10000.0 * t))).astype(np.float32)
    stems_air = StemComposer.decompose_stems(air_tone, sr=sample_rate)
    assert np.sqrt(np.mean(np.square(stems_air["air"]))) > 0.5
    assert np.sqrt(np.mean(np.square(stems_air["sub_bass"]))) < 0.01  # Negligible bleed into sub


def test_revelation_pattern_interrupt_envelopes(sample_rate):
    """Verifies the pre-reveal vacuum (-18 dB rhythm drop) and post-reveal swell."""
    total_samples = sample_rate * 6
    moments = [
        {"t": 3.0, "kind": "reveal"},
        {"t": 5.0, "kind": "calm"},
    ]

    envelopes = StemComposer.build_gain_envelopes(moments, total_samples, sr=sample_rate)

    rhythm_env = envelopes["rhythm"]
    melody_env = envelopes["melody"]
    sub_env = envelopes["sub_bass"]

    # At t = 2.95s (0.05s before reveal), rhythm must be heavily choked (<-14 dB / < 0.20 lin)
    idx_pre = int(2.95 * sample_rate)
    assert rhythm_env[idx_pre] < 0.25, f"Rhythm not choked before reveal: {rhythm_env[idx_pre]}"
    assert melody_env[idx_pre] < 0.60, f"Melody not dipped before reveal: {melody_env[idx_pre]}"

    # At t = 3.05s (immediately after reveal cut), sub-bass punches up (> +1.5 dB / > 1.15 lin)
    idx_post = int(3.05 * sample_rate)
    assert sub_env[idx_post] > 1.15, f"Sub-bass braam impact did not punch: {sub_env[idx_post]}"

    # At t = 4.0s (during post-reveal thematic swell), melody swells above baseline
    idx_swell = int(3.8 * sample_rate)
    assert melody_env[idx_swell] > 1.10, f"Melody did not swell post-reveal: {melody_env[idx_swell]}"

    # At t = 5.5s (during calm moment), rhythm and melody dip by ~3 dB (< 0.85 lin)
    idx_calm = int(5.5 * sample_rate)
    assert rhythm_env[idx_calm] < 0.85, f"Rhythm did not dip on calm: {rhythm_env[idx_calm]}"


def test_foley_category_spectral_filtering(sample_rate):
    """Verifies category spectral filters shape transients correctly."""
    n_samples = sample_rate * 1
    t = np.arange(n_samples) / sample_rate

    # 1. Mechanical: Highpass at 250 Hz suppresses 80 Hz tone
    tone_80hz = np.column_stack((np.sin(2 * np.pi * 80.0 * t), np.sin(2 * np.pi * 80.0 * t))).astype(np.float32)
    filtered_mech = FoleyEngine.apply_spectral_filter(tone_80hz, "mechanical", sr=sample_rate)
    rms_before = np.sqrt(np.mean(np.square(tone_80hz)))
    rms_after = np.sqrt(np.mean(np.square(filtered_mech)))
    assert rms_after < rms_before * 0.15, "Mechanical filter failed to attenuate 80 Hz"

    # 2. Submerged: Lowpass at 800 Hz suppresses 3000 Hz tone
    tone_3khz = np.column_stack((np.sin(2 * np.pi * 3000.0 * t), np.sin(2 * np.pi * 3000.0 * t))).astype(np.float32)
    filtered_sub = FoleyEngine.apply_spectral_filter(tone_3khz, "submerged", sr=sample_rate)
    rms_sub_after = np.sqrt(np.mean(np.square(filtered_sub)))
    assert rms_sub_after < 0.15, "Submerged filter failed to attenuate 3000 Hz"


def test_foley_spatial_impulse_responses(sample_rate):
    """Verifies synthetic impulse responses reflect distinct room acoustics."""
    ir_underground = FoleyEngine.generate_environment_ir("underground", duration_sec=1.0, sr=sample_rate)
    ir_desert = FoleyEngine.generate_environment_ir("desert", duration_sec=1.0, sr=sample_rate)
    ir_dome = FoleyEngine.generate_environment_ir("whispering_gallery", duration_sec=1.0, sr=sample_rate)

    assert ir_underground.shape == (sample_rate, 2)
    assert ir_desert.shape == (sample_rate, 2)
    assert ir_dome.shape == (sample_rate, 2)

    # Desert should be dry: energy after 50ms should be negligible
    late_desert_energy = np.sum(np.abs(ir_desert[int(0.05 * sample_rate):]))
    assert late_desert_energy < 1e-4, "Desert IR contains unexpected late reverb"

    # Underground should have rich reverberant decay extending past 200ms
    late_underground_energy = np.sum(np.abs(ir_underground[int(0.20 * sample_rate):int(0.60 * sample_rate)]))
    assert late_underground_energy > 10.0, "Underground IR lacks reverberant tail"

    # Convolving audio in desert should remain dry
    sig = np.random.randn(sample_rate, 2).astype(np.float32)
    reverb_desert = FoleyEngine.apply_environment_reverb(sig, "desert", wet=0.25, sr=sample_rate)
    assert np.allclose(sig, reverb_desert), "Desert convolution modified dry signal"


def test_voice_prosody_azure_and_elevenlabs():
    """Verifies neural prosody injection for measurements and riddle sentences."""
    script = (
        "Archaeologists found a gear measuring 70 nanometers across. "
        "Could Roman glassmakers control these nanoparticles intentionally?"
    )

    # Azure Speech SSML
    azure_text = VoiceProsody.prepare_speech_text(script, "azure")
    assert '<break time="200ms"/> 70 nanometers' in azure_text
    assert '<break time="350ms"/> Could Roman glassmakers control these nanoparticles intentionally?' in azure_text

    # ElevenLabs ellipsis cadence
    eleven_text = VoiceProsody.prepare_speech_text(script, "elevenlabs")
    assert "... 70 nanometers" in eleven_text
    assert "... Could Roman glassmakers control these nanoparticles intentionally?" in eleven_text

    # Clean stripping for captions and token alignment
    clean = VoiceProsody.strip_prosody(azure_text)
    assert clean == script
    clean_eleven = VoiceProsody.strip_prosody(eleven_text)
    assert clean_eleven == script


def test_end_to_end_ambient_bed_synthesis(tmp_path, sample_rate):
    """Verifies complete ambient bed generation integrating 4-band scoring and diegetic foley."""
    eng = AudioEngine()
    out_bed = tmp_path / "cinematic_bed.wav"

    words = [
        {"word": "Workers", "start": 0.5, "dur": 0.4},
        {"word": "chiseled", "start": 1.0, "dur": 0.4},
        {"word": "deep", "start": 1.5, "dur": 0.3},
        {"word": "underground", "start": 1.8, "dur": 0.5},
        {"word": "tunnels", "start": 2.3, "dur": 0.4},
        {"word": "until", "start": 2.7, "dur": 0.3},
        {"word": "discovering", "start": 3.0, "dur": 0.5},
        {"word": "the", "start": 3.5, "dur": 0.2},
        {"word": "gears.", "start": 3.7, "dur": 0.5},
    ]

    sound_plan = {
        "moments": [
            {"t": 1.0, "kind": "action", "effect": "sfx_chisel.wav"},
            {"t": 3.0, "kind": "reveal", "effect": "sfx_gear_ratchet.wav"},
        ]
    }

    # Generate 5.0 second bed
    result_path = eng.generate_ambient_bed(
        duration=5.0,
        output_path=out_bed,
        words=words,
        sound_plan=sound_plan,
        sr=sample_rate,
    )

    assert result_path.exists()
    audio, sr = sf.read(str(result_path))
    assert sr == sample_rate
    assert audio.shape == (sample_rate * 5, 2)
    assert np.all(np.isfinite(audio))

    # Amplitude must stay bounded within [-1.0, 1.0]
    assert np.max(np.abs(audio)) <= 1.0

    # Overall RMS must be healthy (bed around -26 dBFS, i.e. ~0.04 - 0.08)
    bed_rms = np.sqrt(np.mean(np.square(audio)))
    assert 0.02 <= bed_rms <= 0.15, f"Unexpected bed RMS: {bed_rms}"
