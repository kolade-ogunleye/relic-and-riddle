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
import difflib
import logging
import os
import re
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

COMMON_START_WORDS = {
    "When", "Where", "Why", "How", "Although", "Because", "Before", "After",
    "If", "Then", "So", "And", "But", "Or", "As", "While", "Since", "Until",
    "Unless", "Though", "Whether", "Yesterday", "Today", "Tomorrow", "Now",
    "Soon", "Here", "There", "Perhaps", "Maybe", "Surely", "Actually", "Indeed",
    "Suddenly", "Finally", "Eventually", "Meanwhile", "What", "Who", "Whom",
    "Which", "That", "This", "These", "Those", "Every", "Each", "All", "Both",
    "Either", "Neither", "Once", "Twice", "Still", "Yet", "Already", "Just",
    "Even", "Only", "Almost", "Always", "Never", "Sometimes", "Often", "Well",
    "Yes", "No", "Like", "Unlike", "Plus", "Minus",
}

ROMAN_REGAL_MAP = {
    "I": "the First",
    "II": "the Second",
    "III": "the Third",
    "IV": "the Fourth",
    "V": "the Fifth",
    "VI": "the Sixth",
    "VII": "the Seventh",
    "VIII": "the Eighth",
    "IX": "the Ninth",
    "X": "the Tenth",
    "XI": "the Eleventh",
    "XII": "the Twelfth",
    "XIII": "the Thirteenth",
    "XIV": "the Fourteenth",
    "XV": "the Fifteenth",
    "XVI": "the Sixteenth",
}


def normalize_speech_text(text: str) -> str:
    """Pre-TTS text normalization for natural speech rendering.
    Expands regal Roman numerals (e.g. 'King Krishna I' -> 'King Krishna the First')
    to prevent TTS engines from pronouncing 'I' as the letter / pronoun /aɪ/.
    """
    def _replace_roman(match):
        preceding = match.group(1)
        numeral = match.group(2)
        last_word = preceding.split()[-1]
        if numeral == "I" and last_word in COMMON_START_WORDS:
            return match.group(0)  # pronoun 'I' after conjunction/adverb
        spoken = ROMAN_REGAL_MAP.get(numeral, numeral)
        return f"{preceding} {spoken}"

    # Pre-substitute War/World War Roman numerals before generic regal regex
    pre = re.sub(r"\b(World War|War)\s+I\b", r"\1 One", text)
    pre = re.sub(r"\b(World War|War)\s+II\b", r"\1 Two", pre)
    # Cleanly strip redundant parenthetical unit conversions to avoid awkward TTS cadence pauses
    # e.g. "200 feet (60 meters) deep" -> "200 feet deep"
    # e.g. "85 meters (280 ft)" -> "85 meters"
    pre = re.sub(r"\s*\(\s*(?:or\s+)?\d+(?:\.\d+)?\s*(?:meters?|m|km|kilometers?|feet|ft|miles?|tons?|kg)\s*\)", "", pre, flags=re.IGNORECASE)
    pre = re.sub(r"\s*\([^\)]*\)", "", pre)
    pattern = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI)\b")
    normalized = pattern.sub(_replace_roman, pre)
    return normalized


def split_boundary(text: str, start: float, dur: float) -> Words:
    """Azure can report "In 1963" as one word boundary; captions need single words, so the
    time is shared out by character count."""
    parts = text.split()
    total = sum(len(p) for p in parts) or 1
    out: Words = []
    for p in parts:
        share = dur * len(p) / total
        out.append({"word": p, "start": start, "dur": max(0.05, share)})
        start += share
    return out


def align_words_with_script(script_text: str, timed_words: Words) -> Words:
    """Restores full grammatical punctuation and casing from the script to timed TTS words."""
    if not timed_words or not script_text:
        return timed_words
    norm_text = normalize_speech_text(script_text)
    tokens = norm_text.split()
    clean_tokens = [re.sub(r"[^\w]", "", t.lower()) for t in tokens]
    clean_words = [re.sub(r"[^\w]", "", w.get("word", "").lower()) for w in timed_words]
    matcher = difflib.SequenceMatcher(None, clean_tokens, clean_words)
    punctuated: Words = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx_t, idx_w in zip(range(i1, i2), range(j1, j2)):
                item = dict(timed_words[idx_w])
                item["word"] = tokens[idx_t]
                punctuated.append(item)
        elif tag == "replace":
            for idx_w in range(j1, j2):
                item = dict(timed_words[idx_w])
                if idx_w == j2 - 1 and i2 > i1:
                    m = re.search(r"[^\w\s]+$", tokens[i2 - 1])
                    if m:
                        punct = m.group(0)
                        if not item["word"].endswith(punct):
                            item["word"] += punct
                punctuated.append(item)
        elif tag == "insert":
            for idx_w in range(j1, j2):
                punctuated.append(dict(timed_words[idx_w]))
    return punctuated if len(punctuated) == len(timed_words) else timed_words


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

        Path(output_path).write_bytes(base64.b64decode(data["audio_base64"]))

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
        last_err = None
        for attempt in range(1, 4):
            try:
                c = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch, boundary="WordBoundary")
                words: Words = []
                with open(output_path, "wb") as f:
                    async for chunk in c.stream():
                        if chunk["type"] == "audio":
                            f.write(chunk["data"])
                        elif chunk["type"] == "WordBoundary":
                            words.extend(split_boundary(
                                chunk["text"],
                                chunk["offset"] / 10_000_000.0,
                                chunk["duration"] / 10_000_000.0,
                            ))
                if words and output_path.exists() and os.path.getsize(output_path) > 500:
                    words.sort(key=lambda w: w["start"])
                    return words
            except Exception as e:
                last_err = e
                log.warning("edge-tts attempt %d failed (%s); retrying in %.1fs...", attempt, e, attempt * 1.5)
                await asyncio.sleep(attempt * 1.5)
        raise RuntimeError(f"edge-tts failed after 3 attempts: {last_err}")

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
                words.extend(split_boundary(evt.text, evt.audio_offset / 10_000_000.0, max(0.05, evt.duration.total_seconds())))

        synth.synthesis_word_boundary.connect(on_boundary)
        ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
                f'<voice name="{self.voice}"><prosody rate="{self.rate}" pitch="{self.pitch}">'
                f"{_html.escape(text)}</prosody></voice></speak>")
        result = synth.speak_ssml_async(ssml).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = getattr(result, "cancellation_details", None)
            raise RuntimeError(f"Azure Speech failed: {result.reason} {getattr(details, 'error_details', '')}")
        Path(output_path).write_bytes(result.audio_data)
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
        orig_text = text
        text = normalize_speech_text(text)
        self.fallback_reason = ""
        reasons: List[str] = []
        if is_configured(self.eleven_key):
            quota = self.voice_quota()
            licensed = bool(quota and quota.get("tier") and quota["tier"] != "free")
            has_quota_nums = bool(quota and "limit" in quota and "used" in quota)
            remaining = (quota["limit"] - quota["used"]) if has_quota_nums else None
            if licensed_only and not licensed:
                reasons.append("ElevenLabs is on the Free plan (no commercial licence)")
            elif has_quota_nums and remaining is not None and remaining < len(text):
                reasons.append(f"ElevenLabs balance exhausted ({remaining} chars left; need {len(text)})")
                log.warning("ElevenLabs character balance exhausted (%d chars left; need %d); routing to next voice provider.", remaining, len(text))
            else:
                try:
                    log.info("Synthesizing voiceover via ElevenLabs (%s)...", self.eleven_voice)
                    words = self._generate_elevenlabs(text, output_path)
                    self.last_provider, self.last_licensed = "elevenlabs", licensed
                    return align_words_with_script(orig_text, words)
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
                return align_words_with_script(orig_text, words)
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
        return align_words_with_script(orig_text, words)

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
        return np.frombuffer(out, dtype=np.float32).reshape(-1, 2).copy()

    @staticmethod
    def _loop_with_crossfade(audio: np.ndarray, target_samples: int, xfade_samples: int = 48000 * 3) -> np.ndarray:
        """Seamlessly loops audio to target_samples with overlap-add crossfade, eliminating hard seams."""
        if len(audio) >= target_samples:
            return audio[:target_samples].copy()
        xfade_samples = min(xfade_samples, max(1, len(audio) // 4))
        fade_out = np.linspace(1.0, 0.0, xfade_samples)[:, None]
        fade_in = np.linspace(0.0, 1.0, xfade_samples)[:, None]
        buf = audio.copy()
        while len(buf) < target_samples:
            tail = buf[-xfade_samples:] * fade_out + audio[:xfade_samples] * fade_in
            buf = np.vstack([buf[:-xfade_samples], tail, audio[xfade_samples:]])
        return buf[:target_samples].copy()

    @classmethod
    def _sequence_movements(cls, tracks_audio: List[np.ndarray], target_samples: int, sr: int, xfade_sec: float = 3.0) -> np.ndarray:
        """Sequences multiple musical movements with smooth 3-second overlap-add crossfades."""
        if not tracks_audio:
            raise ValueError("No audio tracks provided for sequencing")
        if len(tracks_audio) == 1:
            return cls._loop_with_crossfade(tracks_audio[0], target_samples, int(xfade_sec * sr))

        n_tracks = len(tracks_audio)
        xfade_samples = int(xfade_sec * sr)
        seg_samples = target_samples // n_tracks + xfade_samples
        segments = [cls._loop_with_crossfade(a, seg_samples, xfade_samples) for a in tracks_audio]

        fade_out = np.linspace(1.0, 0.0, xfade_samples)[:, None]
        fade_in = np.linspace(0.0, 1.0, xfade_samples)[:, None]

        buf = segments[0]
        for next_seg in segments[1:]:
            actual_xfade = min(xfade_samples, len(buf), len(next_seg))
            fo = fade_out[:actual_xfade]
            fi = fade_in[:actual_xfade]
            tail = buf[-actual_xfade:] * fo + next_seg[:actual_xfade] * fi
            buf = np.vstack([buf[:-actual_xfade], tail, next_seg[actual_xfade:]])

        return buf[:target_samples].copy()

    def generate_ambient_bed(self, duration: float, output_path: Path, words: Optional[Words] = None, sr: int = 48000,
                             music_path: Optional[Path] = None, music_paths: Optional[List[Path]] = None,
                             cue_timestamps: Optional[List[float]] = None, loop_seamless: bool = False) -> Path:
        """Music (if given, with seamless crossfades/movements) or a synthesized drone, plus narration SFX and act boundary risers."""
        total = int(sr * duration)
        valid_paths = [Path(p) for p in (music_paths or []) if Path(p).exists()]
        if not valid_paths and music_path is not None and Path(music_path).exists():
            valid_paths = [Path(music_path)]

        if valid_paths:
            tracks_audio = [self._decode(p, sr) for p in valid_paths]
            bed = self._sequence_movements(tracks_audio, total, sr, xfade_sec=3.0).copy()
            rms = float(np.sqrt(np.mean(bed ** 2))) or 1.0
            bed *= self.MUSIC_RMS / rms
            if loop_seamless:
                # Seamless loop: maintain audio energy without a 2.5s fade to silence at the tail.
                # A 50ms micro-fade prevents boundary clicks while keeping audio momentum continuous.
                micro = min(int(0.05 * sr), total // 4)
                if micro:
                    bed[:micro] *= np.linspace(0.0, 1.0, micro)[:, None]
                    bed[-micro:] *= np.linspace(1.0, 0.0, micro)[:, None]
            else:
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

        if cue_timestamps:
            for cue_t in cue_timestamps:
                if 0 < cue_t < duration - 1.0:
                    cue_idx = int(cue_t * sr)
                    cue_len = int(1.5 * sr)
                    n = min(cue_len, total - cue_idx)
                    if n > 0:
                        freq = np.linspace(40.0, 85.0, n)
                        phase = 2 * np.pi * np.cumsum(freq) / sr
                        env = np.sin(np.pi * np.linspace(0, 1, n)) ** 2
                        riser = 0.08 * np.sin(phase) * env
                        bed[cue_idx:cue_idx + n, 0] += riser
                        bed[cue_idx:cue_idx + n, 1] += riser

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

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), np.clip(bed, -1.0, 1.0), sr)
        log.info("Ambient bed (%.2fs, %s) -> %s", duration,
                 f"{len(valid_paths)} music movement(s)" if valid_paths else "drone", Path(output_path).name)
        return Path(output_path)
