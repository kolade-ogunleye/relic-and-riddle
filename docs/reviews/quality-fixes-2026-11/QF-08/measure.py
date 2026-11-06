"""QF-08 Voice PCM + seed: what each voice provider is asked for, what the voiceover file is, and whether a
punctuation-only timed word reaches the captions.

    python measure.py <repo root>

Runs the code of <repo root> (its src/: AudioEngine._generate_elevenlabs, AudioEngine._generate_azure,
AudioEngine.generate_narration, audio_provenance.record_voice, and video_composer.caption_phrases and
partition_sidecar_cues) with every provider mocked; no live call is made. The mocks answer what was asked for:
- ElevenLabs: the request's output_format query parameter, or ElevenLabs' default mp3_44100_128 when there is none.
  pcm_<rate> is answered with raw 16-bit PCM at that rate; mp3_<rate>_<kbps> with an MP3 that ffmpeg encodes
  here at that rate and bit rate. The alignment is one character every 40 ms of the text the engine sent.
  The mock also models the plan: ElevenLabs' API reference says PCM at 44.1 kHz needs the Pro tier or above, so
  on free/starter/creator a pcm_44100 or pcm_48000 request is answered HTTP 403 (taken as not offered below Pro;
  a rejected request is not billed), and raise_for_status raises as requests does.
- Azure: a stand-in Speech SDK module that records the SpeechSynthesisOutputFormat set on the config. A
  Riff48Khz16BitMonoPcm request gets a 48 kHz 16-bit RIFF file; Audio48Khz192KBitRateMonoMp3 gets an MP3 at
  48 kHz, 192 kb/s.
The audio is a generated stand-in: a 1.5 s harmonic tone plus a 14 kHz component
when the requested rate supports it. Its decoded high-band energy exposes the
12 kHz bandwidth ceiling of pcm_24000; it is not a voice-quality listening score.

The narration is "No one knows why. The city was hidden under the plain for centuries.", a fixed script.
apply_prosody_elevenlabs prefixes "No one knows" with a pacing "...", which ElevenLabs then times as a word.

Reported per provider: the requested format, the seed and stability (ElevenLabs), and the written voiceover's name,
codec and sample rate (ffprobe). Provider MP3 generations and required resampling
are reported separately: decoding MP3 to WAV does not erase its lossy history.
Also reported: whether the rights record's
sha256 is the hash of the written file's bytes, and how many punctuation-only words appear in the timed words, the
9:16 and 16:9 caption bursts and the .srt/.vtt sidecar cues.
By plan (elevenlabs_by_plan): a Creator plan (production's), an unknown plan (quota unreadable) on Creator's
formats, Pro, Free/Starter and an unknown plan on basic formats. Each reports the requests sent (format, model), the provider that made the take (a
stand-in Azure is configured, as in production, so a lost paid voice shows as "azure"), and the voiceover.
Acceptance: Azure Riff48Khz16BitMonoPcm; ElevenLabs pcm_48000 with a seed and stability 0.65 where the plan
allows (Pro); a WAV voiceover; a rights hash that matches the WAV; 0 punctuation-only words in
timed words, bursts and cues; on a Creator or unknown plan the take stays on ElevenLabs, in a plan-safe format
with 192 kbps MP3 decoded once to WAV and preserved 14 kHz energy. Free/Starter
retain 128 kbps MP3. Lower-rate PCM is an explicit setting, not the Creator default.

Prints JSON and writes measurements.json beside this file (or to the path in RR_MEASURE_OUT).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import wave
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
SCRIPT = "No one knows why. The city was hidden under the plain for centuries."
MASTER_RATE = 48000
SECONDS = 1.5


def _tone(rate: int):
    import numpy as np
    t = np.arange(int(rate * SECONDS)) / rate
    low = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((140, 280, 420, 560), start=1)) * 6000
    high = 1800 * np.sin(2 * np.pi * 14000 * t) if rate > 28000 else 0
    return (low + high).astype("<i2")


def _pcm(rate: int) -> bytes:
    return _tone(rate).tobytes()


def _riff(rate: int, work: Path) -> bytes:
    path = work / f"stand_in_{rate}.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(_pcm(rate))
    return path.read_bytes()


def _mp3(rate: int, kbps: int, work: Path) -> bytes:
    src, dst = work / f"stand_in_{rate}.wav", work / f"stand_in_{rate}_{kbps}.mp3"
    _riff(rate, work)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", str(rate), "-b:a", f"{kbps}k", str(dst)],
                   check=True)
    return dst.read_bytes()


def _probe(path: Path) -> dict:
    import numpy as np

    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate:format=format_name",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    stream = (info.get("streams") or [{}])[0]
    codec, rate = stream.get("codec_name", ""), int(stream.get("sample_rate") or 0)
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
                              "-ar", str(rate), "pipe:1"], capture_output=True, check=True).stdout
    samples = np.frombuffer(decoded, dtype=np.float32).astype(float)
    power = np.abs(np.fft.rfft(samples * np.hanning(len(samples)))) ** 2
    freq = np.fft.rfftfreq(len(samples), 1 / rate)
    high_band = float(power[(freq >= 13000) & (freq <= 15000)].sum() / max(float(power.sum()), 1e-20))
    return {"name": path.name, "container": info.get("format", {}).get("format_name", ""), "codec": codec,
            "sample_rate": rate,
            "energy_13_15khz_fraction": round(high_band, 6),
            "stored_lossy_codec": codec.startswith("mp3"), "resample_steps": int(rate != MASTER_RATE)}


def _stand_in_azure(work: Path):
    """Azure configured, as in production: a take that falls back to it is visible as provider "azure"."""
    def generate(text, path):
        Path(path).write_bytes(_riff(MASTER_RATE, work))
        return [{"word": w, "start": i * 0.3, "dur": 0.3} for i, w in enumerate(text.split())]
    return generate


def _by_plan(audio_engine, work: Path, name: str, tier, rejected) -> dict:
    sent: list = []
    _eleven_mock(audio_engine, work, sent, rejected)
    run_dir = work / f"run_plan_{name}"
    run_dir.mkdir()
    eng = audio_engine.AudioEngine(eleven_key="k" * 24, eleven_voice="voice", azure_key="a" * 24,
                                   azure_region="eastus")
    eng.voice_quota = lambda: {"tier": tier, "used": 0, "limit": 100000}
    eng._generate_azure = _stand_in_azure(work)
    vo = _voiceover_path(audio_engine, run_dir)
    eng.generate_narration(SCRIPT, vo, licensed_only=tier not in (None, "free"))
    served = sent[-1]["format_served"] if sent and eng.last_provider == "elevenlabs" else ""
    return {"tier": tier, "formats_not_offered": list(rejected),
            "provider_format": served, "provider_lossy_codec_steps": int(served.startswith("mp3_")),
            "requests": [f"{r['format_served']} {r['json']['model_id']} -> HTTP {r['status']}" for r in sent],
            "provider_used": eng.last_provider, "voiceover": _probe(vo)}


def _alignment(text: str) -> dict:
    chars = list(text)
    starts = [round(i * 0.04, 3) for i in range(len(chars))]
    return {"characters": chars, "character_start_times_seconds": starts,
            "character_end_times_seconds": [round(s + 0.04, 3) for s in starts]}


def _voiceover_path(audio_engine, run_dir: Path) -> Path:
    """Where the root's own pipeline writes a new take (main.py's voice stage)."""
    if hasattr(audio_engine, "VOICEOVER_FILE"):
        return run_dir / audio_engine.VOICEOVER_FILE
    return run_dir / "voiceover.mp3"


BELOW_PRO_REJECTS = ("pcm_44100", "pcm_48000")     # PCM at 44.1 kHz is Pro and above (48 kHz taken as the same)


def _eleven_mock(audio_engine, work: Path, sent: list, rejected=()):
    def post(url, headers=None, json=None, timeout=None, **kwargs):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        query.update({k: str(v) for k, v in (kwargs.get("params") or {}).items()})
        fmt = query.get("output_format") or "mp3_44100_128"     # ElevenLabs' default when none is asked for
        sent.append({"output_format_query": query.get("output_format"), "format_served": fmt, "json": json,
                     "status": 403 if fmt in rejected else 200})
        if fmt in rejected:
            def fail():
                raise audio_engine.requests.HTTPError("403 output format not allowed on this plan",
                                                      response=SimpleNamespace(status_code=403))
            return SimpleNamespace(status_code=403, raise_for_status=fail,
                                   json=lambda: {"detail": {"status": "output_format_not_allowed"}})
        m = re.fullmatch(r"pcm_(\d+)", fmt)
        if m:
            audio = _pcm(int(m.group(1)))
        else:
            rate, kbps = re.fullmatch(r"mp3_(\d+)_(\d+)", fmt).groups()
            audio = _mp3(int(rate), int(kbps), work)
        data = {"audio_base64": base64.b64encode(audio).decode(), "alignment": _alignment(json["text"])}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: data)
    audio_engine.requests.post = post


def _azure_sdk(work: Path, requested: list):
    class Signal:
        def __init__(self):
            self.handlers = []

        def connect(self, fn):
            self.handlers.append(fn)

    class Config:
        def __init__(self, subscription=None, region=None):
            self.fmt = None

        def set_speech_synthesis_output_format(self, fmt):
            self.fmt = fmt
            requested.append(fmt)

        def set_property(self, *a):
            pass

    class Synth:
        def __init__(self, speech_config=None, audio_config=None):
            self.cfg = speech_config
            self.synthesis_word_boundary = Signal()

        def speak_ssml_async(self, ssml):
            spoken = re.sub(r"<[^>]+>", " ", ssml).split()
            for i, word in enumerate(spoken):
                for fn in self.synthesis_word_boundary.handlers:
                    fn(SimpleNamespace(boundary_type="word", text=word, audio_offset=i * 2_500_000,
                                       duration=SimpleNamespace(total_seconds=lambda: 0.22)))
            audio = (_riff(48000, work) if self.cfg.fmt == "Riff48Khz16BitMonoPcm" else _mp3(48000, 192, work))
            return SimpleNamespace(get=lambda: SimpleNamespace(reason="done", audio_data=audio))

    sdk = types.ModuleType("azure.cognitiveservices.speech")
    sdk.SpeechConfig, sdk.SpeechSynthesizer = Config, Synth
    sdk.SpeechSynthesisOutputFormat = SimpleNamespace(Riff48Khz16BitMonoPcm="Riff48Khz16BitMonoPcm",
                                                      Audio48Khz192KBitRateMonoMp3="Audio48Khz192KBitRateMonoMp3")
    sdk.PropertyId = SimpleNamespace(SpeechServiceResponse_RequestWordBoundary="wb")
    sdk.SpeechSynthesisBoundaryType = SimpleNamespace(Word="word")
    sdk.ResultReason = SimpleNamespace(SynthesizingAudioCompleted="done")
    azure = types.ModuleType("azure")
    cs = types.ModuleType("azure.cognitiveservices")
    cs.speech, azure.cognitiveservices = sdk, cs
    sys.modules.update({"azure": azure, "azure.cognitiveservices": cs, "azure.cognitiveservices.speech": sdk})


def _punct_only(token: str) -> bool:
    return not re.search(r"\w", str(token))


def measure(root: Path) -> dict:
    sys.path.insert(0, str(root))
    os.environ.setdefault("FONT_SIZE", "80")
    from src import audio_engine
    from src.audio_provenance import record_voice
    from src.video_composer import caption_phrases, partition_sidecar_cues
    work = Path(tempfile.mkdtemp(prefix="qf08_"))
    result: dict = {}

    # ElevenLabs: the request, the file, the rights record, the timed words.
    sent: list = []
    _eleven_mock(audio_engine, work, sent)
    audio_engine.VOICE_PREFER = "elevenlabs"
    run_dir = work / "run_eleven"
    run_dir.mkdir()
    eng = audio_engine.AudioEngine(eleven_key="k" * 24, eleven_voice="voice", azure_key="", azure_region="")
    eng.voice_quota = lambda: {"tier": "pro", "used": 0, "limit": 100000}   # a plan that offers pcm_48000
    vo = _voiceover_path(audio_engine, run_dir)
    words = eng.generate_narration(SCRIPT, vo, licensed_only=True)
    request = sent[-1]
    settings = request["json"].get("voice_settings", {})
    record = record_voice(vo, eng.last_provider, eng.last_licensed, SCRIPT)
    rights = vo.with_name(vo.name + ".rights.json")
    saved = json.loads(rights.read_text(encoding="utf-8"))
    file_hash = hashlib.sha256(vo.read_bytes()).hexdigest()
    result["elevenlabs"] = {
        "provider_used": eng.last_provider,
        "output_format_requested": request["output_format_query"],
        "output_format_served": request["format_served"],
        "seed": request["json"].get("seed"),
        "stability": settings.get("stability"),
        "voiceover": _probe(vo),
        "rights_record": rights.name,
        "rights_sha256_is_file_hash": record["sha256"] == saved["sha256"] == file_hash,
    }

    # Azure: the requested output format and the file.
    requested: list = []
    _azure_sdk(work, requested)
    run_dir = work / "run_azure"
    run_dir.mkdir()
    az = audio_engine.AudioEngine(eleven_key="", azure_key="a" * 24, azure_region="eastus")
    vo_az = _voiceover_path(audio_engine, run_dir)
    az._generate_azure(SCRIPT, vo_az)
    record_az = record_voice(vo_az, "azure", True, SCRIPT)
    result["azure"] = {"output_format_requested": requested[-1], "voiceover": _probe(vo_az),
                       "rights_sha256_is_file_hash": record_az["sha256"] == hashlib.sha256(vo_az.read_bytes()).hexdigest()}

    # ElevenLabs by plan: production's Creator plan, an unknown plan on Creator's formats, a Pro plan.
    audio_engine.ELEVENLABS_MODEL = "eleven_v4"
    result["elevenlabs_by_plan"] = {
        "creator": _by_plan(audio_engine, work, "creator", "creator", BELOW_PRO_REJECTS),
        "unknown_plan_on_creator_formats": _by_plan(audio_engine, work, "unknown", None, BELOW_PRO_REJECTS),
        "pro": _by_plan(audio_engine, work, "pro", "pro", ()),
        "free": _by_plan(audio_engine, work, "free", "free", (*BELOW_PRO_REJECTS, "mp3_44100_192")),
        "starter": _by_plan(audio_engine, work, "starter", "starter", (*BELOW_PRO_REJECTS, "mp3_44100_192")),
        "unknown_plan_on_basic_formats": _by_plan(audio_engine, work, "unknown_basic", None,
                                                   (*BELOW_PRO_REJECTS, "mp3_44100_192")),
    }

    # Captions from the ElevenLabs take's timed words.
    reel = caption_phrases(words)
    wide = caption_phrases(words, widescreen=True)
    cues = partition_sidecar_cues(words)
    result["captions"] = {
        "sent_to_elevenlabs": request["json"]["text"],
        "timed_words": len(words),
        "punctuation_only_timed_words": [w["word"] for w in words if _punct_only(w["word"])],
        "punctuation_only_bursts_9x16": sum(any(_punct_only(w["word"]) for w in b) for b in reel),
        "punctuation_only_bursts_16x9": sum(any(_punct_only(w["word"]) for w in b) for b in wide),
        "punctuation_only_sidecar_cues": sum(any(_punct_only(t) for t in text.split()) for _s, _e, text in cues),
        "bursts_9x16": [" ".join(w["word"] for w in b) for b in reel],
        "sidecar_cues": [text for _s, _e, text in cues],
    }
    e, a, c = result["elevenlabs"], result["azure"], result["captions"]
    plans = result["elevenlabs_by_plan"]
    below_pro = (plans["creator"], plans["unknown_plan_on_creator_formats"])
    result["acceptance"] = {
        "azure_riff48k_pcm": a["output_format_requested"] == "Riff48Khz16BitMonoPcm",
        "elevenlabs_pcm_48000": e["output_format_requested"] == "pcm_48000",
        "elevenlabs_seed_set": isinstance(e["seed"], int),
        "elevenlabs_stability_0_65": e["stability"] == 0.65,
        "voiceover_is_wav": all(p["voiceover"]["container"] == "wav" and p["voiceover"]["name"].endswith(".wav")
                                for p in (e, a)),
        "provider_mp3_generations_pro_plus_azure": int(e["output_format_served"].startswith("mp3_"))
                                                   + int("Mp3" in a["output_format_requested"]),
        "resample_steps_pro_plus_azure": e["voiceover"]["resample_steps"] + a["voiceover"]["resample_steps"],
        "rights_keyed_on_wav": e["rights_sha256_is_file_hash"] and a["rights_sha256_is_file_hash"]
                               and e["voiceover"]["container"] == "wav",
        "punctuation_only_in_captions": (len(c["punctuation_only_timed_words"]) + c["punctuation_only_bursts_9x16"]
                                         + c["punctuation_only_bursts_16x9"] + c["punctuation_only_sidecar_cues"]),
        "below_pro_plans_keep_elevenlabs": all(p["provider_used"] == "elevenlabs" for p in below_pro),
        "below_pro_provider_lossy_codec_steps": sum(p["provider_lossy_codec_steps"] for p in below_pro),
        "creator_mp3_192": all(p["provider_format"] == "mp3_44100_192" for p in below_pro),
        "creator_bandwidth_preserved": all(p["voiceover"]["sample_rate"] == 44100
                                            and p["voiceover"]["energy_13_15khz_fraction"] >= 0.01 for p in below_pro),
        "basic_plans_keep_format_and_provider": all(
            plans[name]["provider_used"] == "elevenlabs" and plans[name]["provider_format"] == "mp3_44100_128"
            and plans[name]["voiceover"]["container"] == "wav"
            for name in ("free", "starter", "unknown_plan_on_basic_formats")),
        "creator_requests": len(plans["creator"]["requests"]),
        "pro_plan_pcm_48000": plans["pro"]["requests"][:1] == ["pcm_48000 eleven_v4 -> HTTP 200"],
    }
    acc = result["acceptance"]
    acc["met"] = bool(acc["azure_riff48k_pcm"] and acc["elevenlabs_pcm_48000"] and acc["elevenlabs_seed_set"]
                      and acc["elevenlabs_stability_0_65"] and acc["voiceover_is_wav"]
                      and acc["provider_mp3_generations_pro_plus_azure"] == 0
                      and acc["resample_steps_pro_plus_azure"] == 0 and acc["rights_keyed_on_wav"]
                      and acc["punctuation_only_in_captions"] == 0 and acc["below_pro_plans_keep_elevenlabs"]
                      and acc["below_pro_provider_lossy_codec_steps"] == 2 and acc["creator_requests"] == 1
                      and acc["creator_mp3_192"] and acc["creator_bandwidth_preserved"]
                      and acc["basic_plans_keep_format_and_provider"]
                      and acc["pro_plan_pcm_48000"])
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    result = measure(Path(sys.argv[1]).resolve())
    text = json.dumps(result, indent=2, ensure_ascii=False)
    out = Path(os.environ.get("RR_MEASURE_OUT") or HERE / "measurements.json")
    out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
