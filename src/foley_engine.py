"""
src/foley_engine.py
===================
Pillar I: Beat-Accurate Diegetic Foley Engine.
Sub-second alignment of licensed physical effects, category spectral shaping,
and spatial environment convolution reverb (underground crypts, domes, desert open air).
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import signal

from . import sfx_palette
from .config import SFX_DIR
from .audio_provenance import sfx_rights

log = logging.getLogger(__name__)


# Categorization of palette sound effects for targeted spectral shaping
EFFECT_CATEGORIES = {
    # Mechanical & Precision Clockwork
    "sfx_gear_ratchet.wav": "mechanical",
    # Forensic Craft & Masonry / Metallurgical Strike
    "sfx_chisel.wav": "forensic_craft",
    "sfx_hammer.wav": "forensic_craft",
    "sfx_anvil_forge.wav": "forensic_craft",
    # Maritime & Submerged
    "sfx_water_surge.wav": "submerged",
    "sfx_sailing_ship.wav": "submerged",
    # Documentary Archive & Fragile Antiquity
    "sfx_parchment.wav": "archive",
    # Thermal & Flame
    "sfx_fire_crackle.wav": "fire",
    # Explosive & Shockwave Impacts
    "sfx_impact_braam.wav": "impact",
    "sfx_explosion_distant.wav": "impact",
    "sfx_thunder.wav": "impact",
    # Environmental & Masonry Motion
    "sfx_stone_door.wav": "masonry_friction",
    "sfx_rockfall.wav": "masonry_friction",
    "sfx_footsteps_stone.wav": "masonry_friction",
    # Continuous Atmospheric Layers
    "sfx_cavern_wind.wav": "atmospheric",
    "sfx_desert_wind.wav": "atmospheric",
    "sfx_mountain_wind.wav": "atmospheric",
    "sfx_sea_waves.wav": "atmospheric",
}


class FoleyEngine:
    """Diegetic Foley synthesis, transient alignment, spectral conditioning,
    and spatial acoustic convolution."""

    @staticmethod
    def get_category(effect_name: str) -> str:
        """Returns the acoustic category of a given sound effect."""
        return EFFECT_CATEGORIES.get(effect_name, "general")

    @classmethod
    def apply_spectral_filter(cls, audio: np.ndarray, category: str, sr: int = 48000) -> np.ndarray:
        """Applies zero-phase category spectral filtering to carve vocal pockets
        and optimize transient clarity."""
        if audio.size == 0 or len(audio) < 16:
            return audio

        filtered = audio.copy()
        try:
            if category == "mechanical":
                # Highpass at 250 Hz (prevent low-end rumble clutter); subtle presence lift at 4.5 kHz
                sos_hp = signal.butter(2, 250.0, "hp", fs=sr, output="sos")
                filtered = signal.sosfiltfilt(sos_hp, filtered, axis=0)

            elif category == "forensic_craft":
                # Highpass at 180 Hz to tame excessive sub boom on stone/iron strikes
                sos_hp = signal.butter(2, 180.0, "hp", fs=sr, output="sos")
                filtered = signal.sosfiltfilt(sos_hp, filtered, axis=0)

            elif category == "submerged":
                # Lowpass at 800 Hz to simulate deep undersea water absorption
                sos_lp = signal.butter(2, 800.0, "lp", fs=sr, output="sos")
                filtered = signal.sosfiltfilt(sos_lp, filtered, axis=0)

            elif category == "archive":
                # Notch at 2.2 kHz (Q=2.0) to keep paper rustles from masking speech clarity
                b, a = signal.iirnotch(w0=2200.0, Q=2.0, fs=sr)
                filtered = signal.filtfilt(b, a, filtered, axis=0)

            elif category == "fire":
                # Bandpass 300 Hz - 6000 Hz for contained crackle texture
                sos_bp = signal.butter(2, [300.0, 6000.0], "bp", fs=sr, output="sos")
                filtered = signal.sosfiltfilt(sos_bp, filtered, axis=0)

            elif category == "atmospheric":
                # Highpass at 60 Hz to eliminate subsonic mic rumble
                sos_hp = signal.butter(2, 60.0, "hp", fs=sr, output="sos")
                filtered = signal.sosfiltfilt(sos_hp, filtered, axis=0)

            elif category == "impact":
                # Lowpass at 7000 Hz to retain dark cinematic sub impact
                sos_lp = signal.butter(2, 7000.0, "lp", fs=sr, output="sos")
                filtered = signal.sosfiltfilt(sos_lp, filtered, axis=0)

        except Exception as e:
            log.warning("Spectral filtering failed for category %s (%s); returning original audio.", category, e)
            return audio

        return filtered.astype(np.float32)

    @staticmethod
    def generate_environment_ir(environment: str, duration_sec: float = 1.8, sr: int = 48000) -> np.ndarray:
        """Procedurally synthesizes an acoustic room impulse response (IR) for spatial convolution."""
        n = int(duration_sec * sr)
        t = np.arange(n) / sr
        rng = np.random.default_rng(707)

        env_clean = (environment or "dry").lower().strip()

        if any(k in env_clean for k in ("underground", "crypt", "tunnel", "cavern", "hypogeum", "catacomb", "cave")):
            # Underground stone chamber: RT60 = 1.8s, high-frequency damping
            decay = np.exp(-t / 0.35)
            noise_l = rng.standard_normal(n)
            noise_r = rng.standard_normal(n)
            # Damp high frequencies above 3.5 kHz over time
            damping = np.exp(-t / 0.15)
            damped_l = noise_l * decay * (0.6 + 0.4 * damping)
            damped_r = noise_r * decay * (0.6 + 0.4 * damping)
            # Early reflection spikes
            for delay_ms, amp in [(18, 0.45), (32, 0.35), (48, 0.28), (67, 0.22)]:
                idx = int((delay_ms / 1000.0) * sr)
                if idx < n:
                    damped_l[idx] += amp
                    damped_r[idx] += amp * 0.9
            ir = np.column_stack((damped_l, damped_r))

        elif any(k in env_clean for k in ("dome", "whispering_gallery", "gallery", "stone_hall", "hall", "cathedral")):
            # Grand whispering gallery / dome: RT60 = 2.4s with discrete 80ms echoes
            decay = np.exp(-t / 0.50)
            noise_l = rng.standard_normal(n) * 0.4
            noise_r = rng.standard_normal(n) * 0.4
            ir_l = noise_l * decay
            ir_r = noise_r * decay
            for k in range(1, 8):
                delay_ms = k * 82
                idx = int((delay_ms / 1000.0) * sr)
                if idx < n:
                    amp = 0.55 * (0.75 ** k)
                    ir_l[idx] += amp
                    ir_r[idx] += amp * 0.85
            ir = np.column_stack((ir_l, ir_r))

        elif any(k in env_clean for k in ("desert", "open_air", "plateau", "ridge", "wasteland")):
            # Dry outdoor space: immediate direct arrival with very short ground reflection
            ir = np.zeros((n, 2), dtype=np.float32)
            ir[0, :] = [1.0, 1.0]
            ground_delay = int(0.006 * sr)
            if ground_delay < n:
                ir[ground_delay, :] = [0.12, 0.10]
            return ir

        elif any(k in env_clean for k in ("submerged", "underwater", "ocean", "sea", "wreck")):
            # Submerged acoustic: dense low-frequency resonance
            decay = np.exp(-t / 0.28)
            rumble = np.sin(2 * np.pi * 95.0 * t) * decay * 0.4
            noise = rng.standard_normal(n) * np.exp(-t / 0.08) * 0.2
            combined = rumble + noise
            ir = np.column_stack((combined, combined))

        else:
            # Default / Subtle studio stone room
            decay = np.exp(-t / 0.08)
            noise_l = rng.standard_normal(n) * decay * 0.25
            noise_r = rng.standard_normal(n) * decay * 0.25
            noise_l[0] += 1.0
            noise_r[0] += 1.0
            ir = np.column_stack((noise_l, noise_r))

        # Normalize IR peak
        peak = np.max(np.abs(ir)) or 1.0
        return (ir / peak).astype(np.float32)

    @classmethod
    def apply_environment_reverb(cls, audio: np.ndarray, environment: str, wet: float = 0.20,
                                 sr: int = 48000) -> np.ndarray:
        """Applies spatial environment convolution reverb to `audio` with specified wet/dry mix."""
        if audio.size == 0 or wet <= 0.0:
            return audio

        env_clean = (environment or "dry").lower().strip()
        if any(k in env_clean for k in ("desert", "open_air", "plateau", "dry")):
            return audio  # Open air remains completely dry

        ir = cls.generate_environment_ir(environment, duration_sec=1.5, sr=sr)
        try:
            convolved = signal.fftconvolve(audio, ir, mode="full", axes=0)[:len(audio)]
            # Match RMS scale of wet signal to dry signal
            dry_rms = np.sqrt(np.mean(np.square(audio))) or 1.0
            wet_rms = np.sqrt(np.mean(np.square(convolved))) or 1.0
            convolved_scaled = convolved * (dry_rms / wet_rms)
            blended = (1.0 - wet) * audio + wet * convolved_scaled
            return blended.astype(np.float32)
        except Exception as e:
            log.warning("Convolution reverb failed (%s); retaining dry audio.", e)
            return audio

    @classmethod
    def load_effect_audio(cls, name: str, fallback_synth_fn, sr: int = 48000) -> Tuple[np.ndarray, Optional[Dict[str, Any]]]:
        """Loads licensed effect recording if verified rights exist, else evaluates procedural synth."""
        path = SFX_DIR / name
        rec = sfx_rights(path) if path.exists() else None
        if rec is not None:
            import subprocess
            out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
                                 capture_output=True, check=True, timeout=120).stdout
            audio = np.frombuffer(out, dtype=np.float32).reshape(-1, 2).copy()
            source_info = {"name": name, "path": str(path.resolve()), "sha256": rec["sha256"], "provider": rec.get("provider")}
            return audio, source_info

        # Procedural synth fallback
        synth = fallback_synth_fn()
        if synth.ndim == 1:
            synth = np.column_stack((synth, synth))
        return synth.astype(np.float32), None

    @classmethod
    def synthesize_diegetic_layer(cls, moments: List[Dict[str, Any]], total_duration: float, sr: int = 48000,
                                  environment: Optional[str] = None,
                                  synth_resolver = None) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Renders all scheduled diegetic foley moments into a composite stereo track with
        spectral filtering and room impulse convolution."""
        total_samples = int(total_duration * sr)
        foley_bed = np.zeros((total_samples, 2), dtype=np.float32)
        sources_used: List[Dict[str, Any]] = []

        cues = sfx_palette.cues()

        for m in sorted(moments, key=lambda x: x.get("t", 0.0)):
            effect_name = m.get("effect")
            if not effect_name or effect_name not in cues:
                continue

            t_cue = float(m.get("t", 0.0))
            if t_cue < 0.0 or t_cue >= total_duration - 0.2:
                continue

            cue_spec = cues[effect_name]
            gain = cue_spec[1]
            synth_name = cue_spec[2] if len(cue_spec) > 2 else "_synth_stone_grind"

            def _resolve_synth():
                if synth_resolver and hasattr(synth_resolver, synth_name):
                    return getattr(synth_resolver, synth_name)(sr=sr)
                # Fallback internal default
                t_arr = np.arange(int(1.0 * sr)) / sr
                s = np.sin(2 * np.pi * 120.0 * t_arr) * np.exp(-t_arr / 0.2) * 0.1
                return np.column_stack((s, s))

            audio, src_rec = cls.load_effect_audio(effect_name, _resolve_synth, sr=sr)
            if src_rec and src_rec["name"] not in {s["name"] for s in sources_used}:
                sources_used.append(src_rec)

            category = cls.get_category(effect_name)
            conditioned = cls.apply_spectral_filter(audio, category, sr=sr)
            convolved = cls.apply_environment_reverb(conditioned, environment or "dry", wet=0.22, sr=sr)

            i0 = int(t_cue * sr)
            n_samples = max(0, min(len(convolved), total_samples - i0))
            if n_samples > 0:
                foley_bed[i0:i0 + n_samples] += convolved[:n_samples] * (gain * 0.40)

        return foley_bed, sources_used
