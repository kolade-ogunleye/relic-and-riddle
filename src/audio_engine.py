"""
src/audio_engine.py
===================
Narration and ambient bed.
1. Voice: ElevenLabs (with character timestamps) when configured, else edge-tts
   (ChristopherNeural) with word-boundary events.
2. Word timings for the kinetic karaoke captions.
3. Ambient bed: 55 Hz + 82.5 Hz drone with diegetic hammer (t=0) and stone-door SFX.
Final mixing, ducking and loudness mastering happen in src/video_composer.py.
"""

import asyncio
import base64
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

import edge_tts
import numpy as np
import requests
import soundfile as sf

from .config import (
    NARRATOR_VOICE,
    NARRATOR_RATE,
    NARRATOR_PITCH,
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    SFX_DIR,
    is_configured,
)

log = logging.getLogger(__name__)

Words = List[Dict[str, Any]]


class AudioEngine:
    def __init__(
        self,
        voice: str = NARRATOR_VOICE,
        rate: str = NARRATOR_RATE,
        pitch: str = NARRATOR_PITCH,
        eleven_key: Optional[str] = None,
        eleven_voice: Optional[str] = None,
    ):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.eleven_key = eleven_key if eleven_key is not None else ELEVENLABS_API_KEY
        self.eleven_voice = eleven_voice if eleven_voice is not None else ELEVENLABS_VOICE_ID

    def _generate_elevenlabs(self, text: str, output_path: Path) -> Words:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.eleven_voice}/with-timestamps"
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.50, "similarity_boost": 0.85, "style": 0.35, "use_speaker_boost": True},
        }
        resp = requests.post(url, headers={"xi-api-key": self.eleven_key}, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        alignment = data.get("alignment") or {}
        chars = alignment.get("characters") or []
        starts = alignment.get("character_start_times_seconds") or []
        ends = alignment.get("character_end_times_seconds") or []
        if not chars or len(chars) != len(starts) or len(chars) != len(ends):
            raise ValueError("ElevenLabs response is missing usable character alignment.")

        output_path.write_bytes(base64.b64decode(data["audio_base64"]))

        words: Words = []
        cur: List[str] = []
        w_start = 0.0
        for i, ch in enumerate(chars + [" "]):
            if ch.strip():
                if not cur:
                    w_start = starts[i]
                cur.append(ch)
            elif cur:
                words.append({"word": "".join(cur), "start": w_start, "dur": max(0.05, ends[i - 1] - w_start)})
                cur = []
        return words

    async def _generate_edge_tts(self, text: str, output_path: Path) -> Words:
        c = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch, boundary="WordBoundary")
        words: Words = []
        with open(output_path, "wb") as f:
            async for chunk in c.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append({
                        "word": chunk["text"],
                        "start": chunk["offset"] / 10_000_000.0,
                        "dur": chunk["duration"] / 10_000_000.0,
                    })
        words.sort(key=lambda w: w["start"])
        return words

    # Set after each narration: which provider voiced it, and why the primary was skipped.
    last_provider: str = ""
    fallback_reason: str = ""

    def generate_narration(self, text: str, output_path: Path) -> Words:
        self.fallback_reason = ""
        if is_configured(self.eleven_key):
            try:
                log.info("Synthesizing voiceover via ElevenLabs (%s)...", self.eleven_voice)
                words = self._generate_elevenlabs(text, output_path)
                self.last_provider = "elevenlabs"
                return words
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                self.fallback_reason = f"HTTP {status}" if status else type(e).__name__
                log.warning("ElevenLabs failed (%s); falling back to edge-tts.", e)
        self.last_provider = "edge-tts"

        log.info("Synthesizing voiceover via edge-tts (%s)...", self.voice)
        words = asyncio.run(self._generate_edge_tts(text, output_path))
        if not words:
            raise RuntimeError("edge-tts returned no word boundaries; captions cannot be timed.")
        return words

    def voice_quota(self) -> Optional[Dict[str, Any]]:
        """ElevenLabs character quota for this billing period, or None if unavailable."""
        if not is_configured(self.eleven_key):
            return None
        try:
            r = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                             headers={"xi-api-key": self.eleven_key}, timeout=20)
            r.raise_for_status()
            j = r.json()
            return {"tier": j.get("tier"), "used": int(j.get("character_count") or 0),
                    "limit": int(j.get("character_limit") or 0), "resets": j.get("next_character_count_reset_unix")}
        except Exception as e:
            log.warning("Could not read ElevenLabs quota: %s", e)
            return None

    @staticmethod
    def get_duration(audio_path: Path) -> float:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)]
        return float(subprocess.check_output(cmd, timeout=60).decode().strip())

    @staticmethod
    def _load_mono(path: Path) -> np.ndarray:
        data, _ = sf.read(str(path))
        return data[:, 0] if data.ndim > 1 else data

    def generate_ambient_bed(self, duration: float, output_path: Path, words: Optional[Words] = None, sr: int = 48000) -> Path:
        total = int(sr * duration)
        t = np.arange(total) / sr
        bed = 0.24 * np.sin(2 * np.pi * 55.0 * t) + 0.16 * np.sin(2 * np.pi * 82.5 * t)
        # Short drone fades so the loop point (end -> start) doesn't click; SFX are added after.
        fade = min(int(0.05 * sr), total // 2)
        if fade:
            ramp = np.linspace(0.0, 1.0, fade)
            bed[:fade] *= ramp
            bed[-fade:] *= ramp[::-1]

        hammer = SFX_DIR / "sfx_hammer.wav"
        if hammer.exists():
            h = self._load_mono(hammer)[:total]
            bed[:len(h)] += h * 0.40

        door = SFX_DIR / "sfx_stone_door.wav"
        if door.exists() and words:
            cue = next((w["start"] for w in words if any(k in w["word"].lower() for k in ("door", "stone", "rolling"))), None)
            if cue is not None:
                s = self._load_mono(door)
                i0 = int(cue * sr)
                n = max(0, min(len(s), total - i0))
                bed[i0:i0 + n] += s[:n] * 0.45

        audio = np.clip(bed * 0.22, -1.0, 1.0)
        sf.write(str(output_path), np.column_stack((audio, audio)), sr)
        log.info("Ambient bed (%.2fs) -> %s", duration, output_path.name)
        return output_path
