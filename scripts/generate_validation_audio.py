"""Generate checkpointed validation audio without messaging or publication."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.audio_engine import AudioEngine
from src.audio_provenance import inspect_audio_rights, record_bed, record_voice
from src.checkpoints import Checkpoints, file_inputs
from src.delivery_profiles import get_delivery_profile
from src.fact_evidence import current_fact_review
from src.music import MUSIC_DIR, load_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--reuse-existing", action="store_true", help="Retain matching, licensed narration already in this package")
    args = parser.parse_args()
    root = args.package
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    if not current_fact_review(payload):
        raise ValueError("A current successful fact review is required")
    engine = AudioEngine()
    checkpoint = Checkpoints(root / "stage_cache" / "validation_audio")
    voice, words_path = root / "voiceover.mp3", root / "words.json"
    inputs = {"script": payload["narration_script"], "voice": engine.voice, "rate": engine.rate,
              "pitch": engine.pitch, "eleven_voice": engine.eleven_voice}
    matching = inspect_audio_rights(voice, "voice", "", script=payload["narration_script"])[0]
    if not checkpoint.reusable("voice", inputs) and not (args.reuse_existing and matching and words_path.is_file()):
        words = engine.generate_narration(payload["narration_script"], voice, licensed_only=True)
        words_path.write_text(json.dumps(words, indent=2), encoding="utf-8")
        record_voice(voice, engine.last_provider, engine.last_licensed, payload["narration_script"])
    checkpoint.complete("voice", inputs, [voice, words_path, voice.with_suffix(".mp3.rights.json")])
    words = json.loads(words_path.read_text(encoding="utf-8"))
    duration = engine.get_duration(voice)
    profile = get_delivery_profile(payload["delivery_profile"])
    track = next(t for t in load_library() if t.get("reviewed") and t.get("license", "").lower() == "cc0"
                 and t.get("license_url") and t.get("source_url"))
    source = MUSIC_DIR / track["file"]
    music = {"source": "library", "credit": "", "tracks": [{**track, "path": str(source)}]}
    bed = root / "ambient_bed.wav"
    bed_inputs = {"sources": file_inputs([voice, words_path, source, ROOT / "src/audio_engine.py"]),
                  "profile": profile.model_dump()}
    if not checkpoint.reusable("bed", bed_inputs):
        engine.generate_ambient_bed(duration + 0.5, bed, words=words, music_path=source,
                                   sr=profile.audio_sample_rate, loop_seamless=profile.loop_seamless)
        record_bed(bed, music)
        checkpoint.complete("bed", bed_inputs, [bed, bed.with_suffix(".wav.rights.json")])
    print(json.dumps({"duration": duration, "words": len(words), "profile": profile.name}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
