"""
src/stem_composer.py
====================
Pillar II: Adaptive 4-Stem Dynamic Scoring.
Autonomous Linkwitz-Riley 4-band spectral decomposition, dynamic envelope generation,
and the narrative revelation pattern interrupt (-18 dB rhythm vacuum 0.5s before reveals).
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import signal

log = logging.getLogger(__name__)


# Standard Crossover Split Frequencies (Hz)
FREQ_SUB_BASS = 120.0
FREQ_MID_CROSSOVER = 800.0
FREQ_AIR = 4500.0
FREQ_SUBSONIC_HP = 20.0
FREQ_ULTRASONIC_LP = 20000.0


class StemComposer:
    """Decomposes stereo music tracks into 4 phase-linear acoustic frequency bands
    and shapes dynamic gain envelopes based on narrative story arcs."""

    @classmethod
    def decompose_stems(cls, audio: np.ndarray, sr: int = 48000) -> Dict[str, np.ndarray]:
        """Decomposes stereo audio into 4 complementary frequency bands:
        - sub_bass: 20 Hz - 120 Hz
        - rhythm: 120 Hz - 800 Hz
        - melody: 800 Hz - 4500 Hz
        - air: 4500 Hz - 20000 Hz

        Uses tree-structured 2nd-order Butterworth filters in zero-phase sosfiltfilt mode,
        guaranteeing exact Linkwitz-Riley 4th order (LR4) equivalent magnitude matching
        and zero phase distortion across all bands.
        """
        if audio.size == 0 or len(audio) < 32:
            empty = np.zeros_like(audio)
            return {"sub_bass": empty, "rhythm": empty, "melody": empty, "air": empty}

        sig = audio.astype(np.float32)
        nyquist = sr / 2.0

        # Boundary safety: clamp crossover frequencies strictly below Nyquist
        if nyquist <= FREQ_SUB_BASS:
            empty = np.zeros_like(sig)
            return {"sub_bass": sig, "rhythm": empty, "melody": empty, "air": empty}

        c1 = min(FREQ_SUB_BASS, nyquist * 0.90)
        c2 = min(FREQ_MID_CROSSOVER, nyquist * 0.95)
        c3 = min(FREQ_AIR, nyquist * 0.98)
        if c2 <= c1:
            c2 = (c1 + nyquist) / 2.0
        if c3 <= c2:
            c3 = (c2 + nyquist) / 2.0

        # 1. Main split at c2 (Low Half vs High Half)
        sos_lp_mid = signal.butter(2, c2, "lp", fs=sr, output="sos")
        sos_hp_mid = signal.butter(2, c2, "hp", fs=sr, output="sos")
        low_half = signal.sosfiltfilt(sos_lp_mid, sig, axis=0)
        high_half = signal.sosfiltfilt(sos_hp_mid, sig, axis=0)

        # 2. Sub-split low_half at c1 -> Sub-Bass & Rhythm
        sos_lp_sub = signal.butter(2, c1, "lp", fs=sr, output="sos")
        sos_hp_sub = signal.butter(2, c1, "hp", fs=sr, output="sos")
        band_sub = signal.sosfiltfilt(sos_lp_sub, low_half, axis=0)
        band_rhythm = signal.sosfiltfilt(sos_hp_sub, low_half, axis=0)

        # 3. Sub-split high_half at c3 -> Melody & Air
        sos_lp_air = signal.butter(2, c3, "lp", fs=sr, output="sos")
        sos_hp_air = signal.butter(2, c3, "hp", fs=sr, output="sos")
        band_melody = signal.sosfiltfilt(sos_lp_air, high_half, axis=0)
        band_air = signal.sosfiltfilt(sos_hp_air, high_half, axis=0)

        return {
            "sub_bass": band_sub.astype(np.float32),
            "rhythm": band_rhythm.astype(np.float32),
            "melody": band_melody.astype(np.float32),
            "air": band_air.astype(np.float32),
        }

    @staticmethod
    def build_gain_envelopes(moments: List[Dict[str, Any]], total_samples: int, sr: int = 48000) -> Dict[str, np.ndarray]:
        """Constructs per-sample dB gain envelopes for each of the 4 frequency stems:
        - Rhythm Band: -18 dB choke during pre-reveal pattern interrupts
        - Sub-Bass: Punch on revelations, continuous tension foundation
        - Melody: Vocal notch compatibility, post-revelation thematic swell (+3 dB)
        - Air: Smooth dynamic ceiling avoiding high-frequency masking
        """
        db_sub = np.zeros(total_samples, dtype=np.float32)
        db_rhythm = np.zeros(total_samples, dtype=np.float32)
        db_melody = np.zeros(total_samples, dtype=np.float32)
        db_air = np.zeros(total_samples, dtype=np.float32)

        def _span(t0: float, t1: float) -> Tuple[int, int]:
            return max(0, int(t0 * sr)), min(total_samples, int(t1 * sr))

        reveals = sorted(float(m["t"]) for m in moments if m.get("kind") == "reveal")

        for m in moments:
            t = float(m.get("t", 0.0))
            kind = m.get("kind")

            if kind == "reveal":
                # Pattern Interrupt Vacuum:
                # 0.5s before reveal, choke rhythm by -18 dB, melody by -8 dB
                vac_a, vac_b = _span(t - 0.5, t)
                if vac_b > vac_a:
                    vac_env = np.sin(np.linspace(0.0, np.pi / 2.0, vac_b - vac_a)) ** 2
                    db_rhythm[vac_a:vac_b] -= 18.0 * vac_env
                    db_melody[vac_a:vac_b] -= 8.0 * vac_env
                    db_air[vac_a:vac_b] -= 4.0 * vac_env

                # Post-Reveal Impact & Swell:
                # Sub-bass punch: +2.5 dB from t to t + 1.2s
                sub_a, sub_b = _span(t, t + 1.2)
                if sub_b > sub_a:
                    db_sub[sub_a:sub_b] += 2.5 * np.exp(-np.linspace(0.0, 3.0, sub_b - sub_a))

                # Melody & Rhythm Swell:
                # From t to t + 2.5s, melody swells +3.0 dB, rhythm recovers to +1.5 dB
                swell_a, swell_b = _span(t, t + 2.5)
                if swell_b > swell_a:
                    swell_env = np.sin(np.linspace(0.0, np.pi, swell_b - swell_a))
                    db_melody[swell_a:swell_b] += 3.0 * swell_env
                    db_rhythm[swell_a:swell_b] += 1.5 * swell_env

            elif kind == "tension":
                # Suspense build: slow ramp of +2.0 dB until next reveal or 4.0s
                end_t = next((r for r in reveals if r > t), t + 4.0)
                a, b = _span(t, end_t)
                if b > a:
                    ramp = np.linspace(0.0, 2.0, b - a)
                    db_rhythm[a:b] += ramp
                    db_melody[a:b] += 1.0 * ramp
                    db_sub[a:b] += 0.8 * ramp

            elif kind == "calm":
                # Reflective breath: smooth -3.0 dB dip over 2.5s
                a, b = _span(t, t + 2.5)
                if b > a:
                    dip = np.sin(np.linspace(0.0, np.pi, b - a))
                    db_rhythm[a:b] -= 3.0 * dip
                    db_melody[a:b] -= 3.0 * dip
                    db_air[a:b] -= 2.0 * dip

        # Convert dB to linear gain multipliers with bounded dynamics (-24 dB to +4.5 dB)
        return {
            "sub_bass": 10 ** (np.clip(db_sub, -24.0, 4.5) / 20.0),
            "rhythm": 10 ** (np.clip(db_rhythm, -24.0, 4.5) / 20.0),
            "melody": 10 ** (np.clip(db_melody, -24.0, 4.5) / 20.0),
            "air": 10 ** (np.clip(db_air, -24.0, 4.5) / 20.0),
        }

    @classmethod
    def apply_adaptive_scoring(cls, audio: np.ndarray, moments: List[Dict[str, Any]], sr: int = 48000,
                               target_rms: Optional[float] = None) -> np.ndarray:
        """Decomposes stereo track into 4 bands, applies narrative pattern interrupts,
        and re-synthesizes a dynamically sculpted stereo bed."""
        if audio.size == 0 or len(audio) < 32:
            return audio

        was_mono = (audio.ndim == 1)
        audio_2d = np.column_stack((audio, audio)) if was_mono else audio

        total_samples = len(audio_2d)
        orig_rms = float(np.sqrt(np.mean(np.square(audio_2d)))) or 1e-4

        # 1. 4-Band Crossover Decomposition
        stems = cls.decompose_stems(audio_2d, sr=sr)

        # 2. Dynamic Story Arc Envelopes
        envelopes = cls.build_gain_envelopes(moments, total_samples, sr=sr)

        # 3. Apply Multi-Band Gain Shaping
        sculpted = np.zeros_like(audio_2d, dtype=np.float32)
        for band_name in ("sub_bass", "rhythm", "melody", "air"):
            band_audio = stems[band_name]
            band_gain = envelopes[band_name][:, None]
            sculpted += band_audio * band_gain

        # 4. Preserve target RMS level or maintain original gain structure
        desired_rms = target_rms if target_rms is not None else orig_rms
        current_rms = float(np.sqrt(np.mean(np.square(sculpted)))) or 1e-4
        if current_rms > 0:
            sculpted *= (desired_rms / current_rms)

        out = np.clip(sculpted, -1.0, 1.0).astype(np.float32)
        return out[:, 0] if was_mono else out
