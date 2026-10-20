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
    DATA_DIR,
    ELEVENLABS_API_KEY,
    ELEVENLABS_FALLBACK_MODEL,
    ELEVENLABS_MODEL,
    ELEVENLABS_SPEED,
    ELEVENLABS_VOICE_ID,
    VOICE_PREFER,
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


def measure_spoken_seconds(text: str) -> Optional[float]:
    """How long the narration runs when spoken: one edge-tts pass of exactly what the voiceover will
    say (normalized text, narrator voice, rate and pitch), measured like the real voiceover. The
    voice is deterministic, so this is a measurement, not an estimate. None if it can't be made."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="spoken_len_")) / "probe.mp3"
    try:
        c = edge_tts.Communicate(normalize_speech_text(text), NARRATOR_VOICE, rate=NARRATOR_RATE, pitch=NARRATOR_PITCH)
        asyncio.run(asyncio.wait_for(c.save(str(tmp)), timeout=60))  # a stalled service must not hang the stage
        return AudioEngine.get_duration(tmp)
    except Exception as e:  # offline or the service is down: the post-voiceover check still guards
        log.warning("Could not measure the narration's spoken length (%s).", e)
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except OSError:
            pass


PACE_FILE = DATA_DIR / "voice_pace.json"


def voice_key(provider: str, voice: str, model: str = "") -> str:
    return ":".join(p for p in (provider, voice, model) if p)


def production_voice_key() -> str:
    """The voice a real run will use first (VOICE_PREFER), for its measured pace."""
    if VOICE_PREFER == "elevenlabs" and is_configured(ELEVENLABS_API_KEY):
        return voice_key("elevenlabs", ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL)
    return voice_key("azure", NARRATOR_VOICE)


def record_pace(key: str, measured: float, actual: float) -> None:
    """Remembers how a real voice's length compares with the free measurement (last 10)."""
    import json
    if measured <= 0 or actual <= 0:
        return
    try:
        data = json.loads(PACE_FILE.read_text(encoding="utf-8")) if PACE_FILE.exists() else {}
    except (OSError, ValueError):
        data = {}
    data[key] = (data.get(key, []) + [round(actual / measured, 4)])[-10:]
    PACE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


def calibrate_pace(provider: str, voice: str, text: str, actual_seconds: float) -> None:
    """After a real voiceover: record its length against the free measurement of the same text."""
    if provider == "edge-tts":
        return  # the measurement itself
    key = voice_key("elevenlabs", voice, ELEVENLABS_MODEL) if provider == "elevenlabs" else voice_key(provider, voice)
    try:
        measured = measure_spoken_seconds(text)
        if measured:
            record_pace(key, measured, actual_seconds)
    except Exception as e:  # calibration must never fail a run
        log.warning("Could not record the voice pace: %s", e)


def pace_ratio(key: str) -> float:
    """Median real/measured length ratio for this voice; 1.0 until it has been measured."""
    import json
    import statistics
    try:
        ratios = json.loads(PACE_FILE.read_text(encoding="utf-8")).get(key, [])
    except (OSError, ValueError):
        return 1.0
    return statistics.median(ratios) if ratios else 1.0


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


def strip_ssml_tags(text: str) -> str:
    """Strips XML/SSML tags (e.g. <break time="200ms"/>) to obtain clean words for caption timing."""
    return re.sub(r"<[^>]+>", "", text)


def escape_ssml_preserving_tags(text: str) -> str:
    """Escapes XML entities in narration text while preserving valid SSML pacing tags (e.g. <break time="200ms"/>)."""
    import html as _html
    parts = re.split(r"(<[^>]+>)", text)
    out = []
    for part in parts:
        if re.match(r"^<(?:break\s+time=\"\d+m?s\"|/?[a-z]+(?:\s+[a-z]+=\"[^\"]*\")*)\s*/?>$", part, re.IGNORECASE):
            out.append(part)
        else:
            out.append(_html.escape(part))
    return "".join(out)


def align_words_with_script(script_text: str, timed_words: Words) -> Words:
    """Restores full grammatical punctuation and casing from the script to timed TTS words."""
    if not timed_words or not script_text:
        return timed_words
    norm_text = strip_ssml_tags(normalize_speech_text(script_text))
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

    @staticmethod
    def eleven_payload(text: str, model: str) -> Dict[str, Any]:
        """Request body for one model: style and speaker boost exist only on the v2 models."""
        settings: Dict[str, Any] = {"stability": 0.50, "similarity_boost": 0.85, "speed": ELEVENLABS_SPEED}
        if model.startswith(("eleven_multilingual_v2", "eleven_monolingual")):
            settings.update(style=0.35, use_speaker_boost=True)
        return {"text": text, "model_id": model, "voice_settings": settings}

    def _generate_elevenlabs(self, text: str, output_path: Path) -> Words:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.eleven_voice}/with-timestamps"
        resp = requests.post(url, headers={"xi-api-key": self.eleven_key},
                             json=self.eleven_payload(text, ELEVENLABS_MODEL), timeout=120)
        if resp.status_code in (400, 422) and ELEVENLABS_FALLBACK_MODEL and ELEVENLABS_FALLBACK_MODEL != ELEVENLABS_MODEL:
            # The model may not support timestamped output (or these settings); a rejected request is not billed.
            log.warning("ElevenLabs %s rejected the request (HTTP %d); using %s.", ELEVENLABS_MODEL,
                        resp.status_code, ELEVENLABS_FALLBACK_MODEL)
            resp = requests.post(url, headers={"xi-api-key": self.eleven_key},
                                 json=self.eleven_payload(text, ELEVENLABS_FALLBACK_MODEL), timeout=120)
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
        for attempt in range(1, 6):
            try:
                if output_path.exists():
                    try:
                        output_path.unlink()
                    except Exception:
                        pass
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
                backoff = attempt * 2.0
                log.warning("edge-tts attempt %d failed (%s); retrying in %.1fs with fresh connection...", attempt, e, backoff)
                await asyncio.sleep(backoff)
        raise RuntimeError(f"edge-tts failed after 5 attempts: {last_err}")

    def _generate_azure(self, text: str, output_path: Path) -> Words:
        """Azure Speech via the official SDK; word timings from word-boundary events."""

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
        escaped_text = escape_ssml_preserving_tags(text)
        ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
                f'<voice name="{self.voice}"><prosody rate="{self.rate}" pitch="{self.pitch}">'
                f"{escaped_text}</prosody></voice></speak>")
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

    def generate_narration(self, text: str, output_path: Path, licensed_only: bool = False,
                           allow_paid: bool = True) -> Words:
        """licensed_only (real runs): skip unlicensed voices; raise NoLicensedVoice if none is left.
        allow_paid=False (dry runs): don't spend ElevenLabs credits."""
        orig_text = text
        text = normalize_speech_text(text)
        self.fallback_reason = ""
        reasons: List[str] = []

        def _try_elevenlabs():
            if not is_configured(self.eleven_key):
                return None
            if not allow_paid:  # a test run on the free voice by design, not a fallback
                return None
            quota = self.voice_quota()
            licensed = bool(quota and quota.get("tier") and quota["tier"] != "free")
            has_quota_nums = bool(quota and "limit" in quota and "used" in quota)
            remaining = (quota["limit"] - quota["used"]) if has_quota_nums else None
            if licensed_only and not licensed:
                reasons.append("ElevenLabs is on the Free plan (no commercial licence)")
                return None
            if has_quota_nums and remaining is not None and remaining < len(text):
                reasons.append(f"ElevenLabs balance exhausted ({remaining} chars left; need {len(text)})")
                log.warning("ElevenLabs character balance exhausted (%d chars left; need %d); routing to next voice provider.", remaining, len(text))
                return None
            try:
                log.info("Synthesizing voiceover via ElevenLabs (%s)...", self.eleven_voice)
                words = self._generate_elevenlabs(text, output_path)
                self.last_provider, self.last_licensed = "elevenlabs", licensed
                return align_words_with_script(orig_text, words)
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                reasons.append(f"ElevenLabs failed (HTTP {status})" if status else f"ElevenLabs failed ({type(e).__name__})")
                log.warning("ElevenLabs failed (%s); trying the next voice.", e)
                return None

        def _try_azure():
            if not (is_configured(self.azure_key) and is_configured(self.azure_region)):
                return None
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
                return None

        # Order providers by VOICE_PREFER ("azure" free commercial tier first vs "elevenlabs" paid score first)
        providers_order = [_try_azure, _try_elevenlabs] if VOICE_PREFER == "azure" else [_try_elevenlabs, _try_azure]
        for provider_fn in providers_order:
            res = provider_fn()
            if res is not None:
                return res

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

    # Sound effects play only where the narration says the action or thing they depict (first
    # matching word, whole words only: substrings put an ocean under "research").
    # Tuple format: (words, gain, optional_procedural_synth_name)
    SFX_CUES = {
        "sfx_hammer.wav": (("knock", "knocked", "knocking", "hammer", "hammered", "smash", "smashed"), 0.40, "_synth_chisel_strike"),
        "sfx_stone_door.wav": (("door", "doors"), 0.45, "_synth_stone_grind"),
        "sfx_gear_ratchet.wav": (("gear", "gears", "clockwork", "ratchet", "cogwheel", "cogwheels"), 0.35, "_synth_gear_ratchet"),
        "sfx_water_surge.wav": (("submerged", "sea", "seas", "ocean", "shipwreck", "wreck", "diver", "divers", "underwater", "seabed"), 0.38, "_synth_water_surge"),
        "sfx_chisel.wav": (("chisel", "chiseled", "chiselled", "carve", "carved", "carving", "hewn", "quarried", "quarry"), 0.36, "_synth_chisel_strike"),
    }
    # The cavern-wind layer is for places under the ground; the revelation hit for an actual finding.
    UNDERGROUND_WORDS = {"underground", "subterranean", "cave", "caves", "cavern", "caverns", "tunnel", "tunnels",
                         "shaft", "shafts", "hypogeum", "catacomb", "catacombs", "mine", "mines", "crypt"}
    REVELATION_WORDS = {"discovered", "discovering", "rediscovered", "rediscovering", "uncovered", "revealed",
                        "unearthed", "secret", "impossible"}

    @staticmethod
    def _spoken(w: Dict[str, Any]) -> set:
        return set(re.findall(r"[a-z]+", str(w.get("word", "")).lower()))

    @classmethod
    def sfx_cue_times(cls, words: Optional[Words]) -> Dict[str, float]:
        """Start time of each sound effect: the first word that names what it depicts."""
        cues: Dict[str, float] = {}
        for name, (keywords, *_rest) in cls.SFX_CUES.items():
            t = next((float(w.get("start", 0.0)) for w in words or [] if cls._spoken(w) & set(keywords)), None)
            if t is not None:
                cues[name] = t
        return cues

    @classmethod
    def is_underground(cls, words: Optional[Words]) -> bool:
        return any(cls._spoken(w) & cls.UNDERGROUND_WORDS for w in words or [])

    @classmethod
    def revelation_time(cls, words: Optional[Words]) -> Optional[float]:
        return next((float(w.get("start", 0.0)) for w in words or [] if cls._spoken(w) & cls.REVELATION_WORDS), None)
    MUSIC_RMS = 0.05  # bed level for music (about -26 dBFS); the mix ducks it further under speech

    @staticmethod
    def _synth_gear_ratchet(duration_sec: float = 0.8, sr: int = 48000) -> np.ndarray:
        """Procedural Foley: Clockwork bronze gear ratchet and interlocking wheel clicks."""
        n = int(duration_sec * sr)
        out = np.zeros((n, 2), dtype=np.float32)
        clicks = [0.05, 0.18, 0.32, 0.48, 0.62]
        rng = np.random.default_rng(404)
        for i, c_t in enumerate(clicks):
            c_idx = int(c_t * sr)
            c_dur = int(0.04 * sr)
            if c_idx + c_dur <= n:
                tau = np.arange(c_dur) / sr
                decay = np.exp(-tau / 0.008)
                freq = 2400.0 + (i % 3) * 350.0
                click = (np.sin(2 * np.pi * freq * tau) + 0.4 * rng.standard_normal(c_dur)) * decay * 0.12
                pan_l = 0.4 + 0.1 * i
                pan_r = 1.0 - pan_l
                out[c_idx:c_idx + c_dur, 0] += (click * pan_l).astype(np.float32)
                out[c_idx:c_idx + c_dur, 1] += (click * pan_r).astype(np.float32)
        return out

    @staticmethod
    def _synth_water_surge(duration_sec: float = 2.0, sr: int = 48000) -> np.ndarray:
        """Procedural Foley: Submerged ocean swell and deep undersea water wash."""
        n = int(duration_sec * sr)
        t = np.arange(n) / sr
        rng = np.random.default_rng(505)
        noise_l = rng.standard_normal(n)
        noise_r = rng.standard_normal(n)
        brown_l = np.cumsum(noise_l)
        brown_l -= np.mean(brown_l)
        brown_l /= max(1e-6, np.max(np.abs(brown_l)))
        brown_r = np.cumsum(noise_r)
        brown_r -= np.mean(brown_r)
        brown_r /= max(1e-6, np.max(np.abs(brown_r)))
        env = (np.sin(np.pi * t / duration_sec)) ** 2
        sub = np.sin(2 * np.pi * 55.0 * t) * env * 0.08
        surge_l = (brown_l * 0.10 + sub) * env
        surge_r = (brown_r * 0.10 + sub) * env
        return np.column_stack((surge_l, surge_r)).astype(np.float32)

    @staticmethod
    def _synth_chisel_strike(duration_sec: float = 1.0, sr: int = 48000) -> np.ndarray:
        """Procedural Foley: Acoustic stone chisel strike with resonant masonry ping."""
        n = int(duration_sec * sr)
        t = np.arange(n) / sr
        rng = np.random.default_rng(606)
        noise = rng.standard_normal(n)
        env_transient = np.exp(-t / 0.02) * (1.0 - np.exp(-t / 0.002))
        env_ring = np.exp(-t / 0.25)
        impact = noise * env_transient * 0.15
        ring = np.sin(2 * np.pi * 1450.0 * t) * env_ring * 0.08
        chisel = impact + ring
        return np.column_stack((chisel, chisel)).astype(np.float32)

    @staticmethod
    def _synth_sub_bass_braam(duration_sec: float = 2.0, sr: int = 48000, f0: float = 46.0) -> np.ndarray:
        """Triple-stem foley stem 1: Cinematic revelation sub-bass impact / braam."""
        n = int(duration_sec * sr)
        t = np.arange(n) / sr
        pitch_env = f0 * (1.0 + 0.35 * np.exp(-t / 0.15))
        phase = 2 * np.pi * np.cumsum(pitch_env) / sr
        amp_env = (1.0 - np.exp(-t / 0.04)) * np.exp(-t / 0.8)
        braam = np.tanh(1.8 * np.sin(phase)) * amp_env * 0.16
        shift = int(0.002 * sr)
        left = braam
        right = np.roll(braam, shift)
        return np.column_stack((left, right))

    @staticmethod
    def _synth_cavern_draft(total_samples: int, sr: int = 48000) -> np.ndarray:
        """Triple-stem foley stem 2: Whistling cavern draft winds through subterranean shafts."""
        rng = np.random.default_rng(101)
        white_l = rng.standard_normal(total_samples)
        white_r = rng.standard_normal(total_samples)
        brown_l = np.cumsum(white_l)
        brown_l -= np.mean(brown_l)
        brown_l /= max(1e-6, np.max(np.abs(brown_l)))
        brown_r = np.cumsum(white_r)
        brown_r -= np.mean(brown_r)
        brown_r /= max(1e-6, np.max(np.abs(brown_r)))
        t = np.arange(total_samples) / sr
        lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 0.20 * t)
        draft_l = brown_l * lfo * 0.012
        draft_r = brown_r * (1.0 - lfo) * 0.012
        return np.column_stack((draft_l, draft_r))

    @staticmethod
    def _synth_stone_grind(duration_sec: float = 1.8, sr: int = 48000) -> np.ndarray:
        """Triple-stem foley stem 3: Procedural stone-on-stone friction rumble and scrape."""
        n = int(duration_sec * sr)
        t = np.arange(n) / sr
        rng = np.random.default_rng(202)
        noise = rng.standard_normal(n)
        env = (1.0 - np.exp(-t / 0.08)) * np.exp(-t / 0.65)
        rumble = np.sin(2 * np.pi * 85.0 * t) * (1.0 + 0.5 * noise) * env * 0.10
        return np.column_stack((rumble, rumble))

    @staticmethod
    def _synth_transition_whoosh(duration_sec: float = 0.45, sr: int = 48000) -> np.ndarray:
        """Procedural Foley: Cinematic transition whoosh with resonant downward sweep (600 -> 120 Hz) and dynamic stereo pan."""
        n = int(duration_sec * sr)
        rng = np.random.default_rng(303)
        noise = rng.standard_normal(n)
        freq_sweep = np.linspace(600.0, 120.0, n)
        phase = 2 * np.pi * np.cumsum(freq_sweep) / sr
        sweep = np.sin(phase)
        env = (np.sin(np.pi * np.linspace(0, 1, n))) ** 2
        whoosh = (sweep * 0.45 + noise * 0.55) * env * 0.08
        pan_l = np.linspace(0.8, 0.2, n)
        pan_r = np.linspace(0.2, 0.8, n)
        return np.column_stack((whoosh * pan_l, whoosh * pan_r))

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

        # Triple-stem foley stem 2: continuous cavern draft wind, only for a story told underground
        if self.is_underground(words):
            cavern_draft = self._synth_cavern_draft(total, sr)
            fin_d, fout_d = min(int(1.0 * sr), total // 4), min(int(2.0 * sr), total // 4)
            if fin_d and fout_d:
                cavern_draft[:fin_d] *= np.linspace(0.0, 1.0, fin_d)[:, None]
                cavern_draft[-fout_d:] *= np.linspace(1.0, 0.0, fout_d)[:, None]
            bed += cavern_draft

        # Procedural Foley: Opening hook reveal impact at ~0.15s
        if total > int(0.20 * sr):
            hook_braam = self._synth_sub_bass_braam(duration_sec=1.5, sr=sr, f0=45.0)
            hook_start = int(0.15 * sr)
            n_h = min(len(hook_braam), total - hook_start)
            if n_h > 0:
                bed[hook_start:hook_start + n_h] += hook_braam[:n_h] * 0.70

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
                    # Triple-stem foley stem 1: Cinematic revelation sub-bass braam impact
                    braam = self._synth_sub_bass_braam(duration_sec=2.0, sr=sr)
                    n_b = min(len(braam), total - cue_idx)
                    if n_b > 0:
                        bed[cue_idx:cue_idx + n_b] += braam[:n_b]

        # Procedural Foley: Layer transition whooshes on scene cuts or sentence pauses
        transition_times = list(cue_timestamps) if cue_timestamps else []
        if not transition_times and words:
            last_t = 0.0
            for i in range(len(words) - 1):
                w_curr = words[i]
                w_next = words[i + 1]
                end_curr = w_curr.get("start", 0.0) + w_curr.get("dur", 0.0)
                start_next = w_next.get("start", 0.0)
                gap = start_next - end_curr
                has_punct = any(w_curr.get("word", "").endswith(p) for p in [".", "!", "?", ":", ";"])
                if (gap >= 0.20 or has_punct) and (start_next - last_t >= 3.5):
                    transition_times.append(start_next)
                    last_t = start_next

        for t_trans in transition_times:
            if 0.5 <= t_trans <= duration - 0.5:
                w_start = max(0, int((t_trans - 0.15) * sr))
                whoosh = self._synth_transition_whoosh(duration_sec=0.45, sr=sr)
                n_w = min(len(whoosh), total - w_start)
                if n_w > 0:
                    bed[w_start:w_start + n_w] += whoosh[:n_w]

        # Triple-stem foley: one sub-bass hit on the story's first real revelation
        cue_t = self.revelation_time(words)
        if cue_t is not None and 0 < cue_t < duration - 1.0:
            cue_idx = int(cue_t * sr)
            braam = self._synth_sub_bass_braam(duration_sec=1.8, sr=sr)
            n_b = min(len(braam), total - cue_idx)
            if n_b > 0:
                bed[cue_idx:cue_idx + n_b] += braam[:n_b] * 0.75

        for name, cue in self.sfx_cue_times(words).items():
            cue_spec = self.SFX_CUES[name]
            gain = cue_spec[1]
            synth_func = cue_spec[2] if len(cue_spec) > 2 else "_synth_stone_grind"
            sfx_file = SFX_DIR / name
            if sfx_file.exists():
                s = self._decode(sfx_file, sr)[:, 0]  # resampled to the bed's rate
                i0 = int(cue * sr)
                n = max(0, min(len(s), total - i0))
                bed[i0:i0 + n] += (s[:n] * gain * 0.22)[:, None]
            else:
                synth_method = getattr(self, synth_func, self._synth_stone_grind)
                synth_sfx = synth_method(sr=sr)
                if synth_sfx.ndim == 1:
                    synth_sfx = np.column_stack((synth_sfx, synth_sfx))
                i0 = int(cue * sr)
                n = max(0, min(len(synth_sfx), total - i0))
                bed[i0:i0 + n] += synth_sfx[:n] * (gain * 0.40)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), np.clip(bed, -1.0, 1.0), sr)
        log.info("Ambient bed (%.2fs, %s) -> %s", duration,
                 f"{len(valid_paths)} music movement(s)" if valid_paths else "drone", Path(output_path).name)
        return Path(output_path)
