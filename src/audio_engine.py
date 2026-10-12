"""
src/audio_engine.py
===================
Narration and ambient bed.
1. Voice, best first, with word timings from each provider:
     ElevenLabs (character timestamps) -> Azure Speech (word-boundary events, licensed,
     free F0 tier) -> edge-tts (dry runs only: no commercial licence).
   Real (non-dry) runs use only licensed voices: ElevenLabs on a paid plan (the Free plan
   has no commercial licence) or Azure. With neither, the run stops (NoLicensedVoice).
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
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
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


class NoLicensedVoice(RuntimeError):
    """No commercially licensed voice is available for a real run."""


class AudioEngine:
    def __init__(
        self,
        voice: str = NARRATOR_VOICE,
        rate: str = NARRATOR_RATE,
        pitch: str = NARRATOR_PITCH,
        eleven_key: Optional[str] = None,
        eleven_voice: Optional[str] = None,
        azure_key: Optional[str] = None,
        azure_region: Optional[str] = None,
    ):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.eleven_key = eleven_key if eleven_key is not None else ELEVENLABS_API_KEY
        self.eleven_voice = eleven_voice if eleven_voice is not None else ELEVENLABS_VOICE_ID
        self.azure_key = azure_key if azure_key is not None else AZURE_SPEECH_KEY
        self.azure_region = azure_region if azure_region is not None else AZURE_SPEECH_REGION

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

    def _generate_azure(self, text: str, output_path: Path) -> Words:
        """Azure Speech via the official SDK; word timings from word-boundary events."""
        import html as _html

        import azure.cognitiveservices.speech as speechsdk

        cfg = speechsdk.SpeechConfig(subscription=self.azure_key, region=self.azure_region)
        cfg.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio48Khz192KBitRateMonoMp3)
        cfg.set_property(speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary, "true")
        synth = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
        words: Words = []

        def on_boundary(evt):
            if evt.boundary_type == speechsdk.SpeechSynthesisBoundaryType.Word:
                start, dur = evt.audio_offset / 10_000_000.0, max(0.05, evt.duration.total_seconds())
                parts = evt.text.split()  # Azure can report "In 1963" as one boundary; captions need words
                total = sum(len(p) for p in parts) or 1
                for p in parts:
                    share = dur * len(p) / total
                    words.append({"word": p, "start": start, "dur": max(0.05, share)})
                    start += share

        synth.synthesis_word_boundary.connect(on_boundary)
        ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
                f'<voice name="{self.voice}"><prosody rate="{self.rate}" pitch="{self.pitch}">'
                f"{_html.escape(text)}</prosody></voice></speak>")
        result = synth.speak_ssml_async(ssml).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = getattr(result, "cancellation_details", None)
            raise RuntimeError(f"Azure Speech failed: {result.reason} {getattr(details, 'error_details', '')}")
        output_path.write_bytes(result.audio_data)
        words.sort(key=lambda w: w["start"])
        return words

    # Set after each narration: which provider voiced it, whether it is commercially licensed,
    # and why a better provider was skipped.
    last_provider: str = ""
    last_licensed: bool = False
    fallback_reason: str = ""

    def eleven_licensed(self) -> bool:
        """ElevenLabs output is licensed for commercial use only on a paid plan."""
        quota = self.voice_quota()
        return bool(quota and quota.get("tier") and quota["tier"] != "free")

    def generate_narration(self, text: str, output_path: Path, licensed_only: bool = False) -> Words:
        """licensed_only (real runs): skip unlicensed voices; raise NoLicensedVoice if none is left."""
        self.fallback_reason = ""
        reasons: List[str] = []
        if is_configured(self.eleven_key):
            licensed = self.eleven_licensed()
            if licensed_only and not licensed:
                reasons.append("ElevenLabs is on the Free plan (no commercial licence)")
            else:
                try:
                    log.info("Synthesizing voiceover via ElevenLabs (%s)...", self.eleven_voice)
                    words = self._generate_elevenlabs(text, output_path)
                    self.last_provider, self.last_licensed = "elevenlabs", licensed
                    return words
                except Exception as e:
                    status = getattr(getattr(e, "response", None), "status_code", None)
                    reasons.append(f"ElevenLabs failed (HTTP {status})" if status else f"ElevenLabs failed ({type(e).__name__})")
                    log.warning("ElevenLabs failed (%s); trying the next voice.", e)
        if is_configured(self.azure_key) and is_configured(self.azure_region):
            try:
                log.info("Synthesizing voiceover via Azure Speech (%s)...", self.voice)
                words = self._generate_azure(text, output_path)
                if not words:
                    raise RuntimeError("Azure Speech returned no word boundaries")
                self.last_provider, self.last_licensed = "azure", True
                self.fallback_reason = "; ".join(reasons)
                return words
            except Exception as e:
                reasons.append(f"Azure Speech failed ({e})"[:200])
                log.warning("Azure Speech failed (%s); trying the next voice.", e)
        self.fallback_reason = "; ".join(reasons)
        if licensed_only:
            raise NoLicensedVoice("No commercially licensed voice is available: "
                                  + (self.fallback_reason or "set up ElevenLabs (paid plan) or Azure Speech") + ".")

        log.info("Synthesizing voiceover via edge-tts (%s; test runs only)...", self.voice)
        words = asyncio.run(self._generate_edge_tts(text, output_path))
        if not words:
            raise RuntimeError("edge-tts returned no word boundaries; captions cannot be timed.")
        self.last_provider, self.last_licensed = "edge-tts", False
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

    # Sound effects play only where the narration mentions them (first matching word).
    SFX_CUES = {
        "sfx_hammer.wav": (("knock", "hammer", "smash", "broke", "chisel", "wall"), 0.40),
        "sfx_stone_door.wav": (("door", "rolling"), 0.45),
    }
    MUSIC_RMS = 0.05  # bed level for music (about -26 dBFS); the mix ducks it further under speech

    @staticmethod
    def _decode(path: Path, sr: int) -> np.ndarray:
        """Any audio file -> stereo float32 at `sr` (via ffmpeg)."""
        out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
                             capture_output=True, check=True, timeout=120).stdout
        return np.frombuffer(out, dtype=np.float32).reshape(-1, 2)

    def generate_ambient_bed(self, duration: float, output_path: Path, words: Optional[Words] = None, sr: int = 48000,
                             music_path: Optional[Path] = None) -> Path:
        """Music (if given) or a synthesized drone, plus narration-cued sound effects."""
        total = int(sr * duration)
        if music_path is not None:
            music = self._decode(Path(music_path), sr)
            if len(music) < total:  # too short: loop it
                music = np.tile(music, (total // max(1, len(music)) + 1, 1))
            bed = music[:total].copy()
            rms = float(np.sqrt(np.mean(bed ** 2))) or 1.0
            bed *= self.MUSIC_RMS / rms
            fin, fout = min(int(1.0 * sr), total // 4), min(int(2.5 * sr), total // 4)
            bed[:fin] *= np.linspace(0.0, 1.0, fin)[:, None]
            bed[-fout:] *= np.linspace(1.0, 0.0, fout)[:, None]
        else:
            t = np.arange(total) / sr
            drone = 0.24 * np.sin(2 * np.pi * 55.0 * t) + 0.16 * np.sin(2 * np.pi * 82.5 * t)
            fade = min(int(0.05 * sr), total // 2)  # no click at the loop point
            if fade:
                ramp = np.linspace(0.0, 1.0, fade)
                drone[:fade] *= ramp
                drone[-fade:] *= ramp[::-1]
            bed = np.column_stack((drone, drone)) * 0.22

        for name, (keywords, gain) in self.SFX_CUES.items():
            sfx_file = SFX_DIR / name
            if not (sfx_file.exists() and words):
                continue
            cue = next((w["start"] for w in words if any(k in w["word"].lower() for k in keywords)), None)
            if cue is None:
                continue
            s = self._decode(sfx_file, sr)[:, 0]  # resampled to the bed's rate
            i0 = int(cue * sr)
            n = max(0, min(len(s), total - i0))
            bed[i0:i0 + n] += (s[:n] * gain * 0.22)[:, None]

        sf.write(str(output_path), np.clip(bed, -1.0, 1.0), sr)
        log.info("Ambient bed (%.2fs, %s) -> %s", duration, "music" if music_path else "drone", output_path.name)
        return output_path
