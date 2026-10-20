"""
Generate the project's licensed sound-effect pack once with ElevenLabs Sound Effects, into
data/sfx/, each file with rights evidence (data/sfx/<name>.rights.json). The ambient bed uses a
recording only while its evidence matches its bytes; otherwise it falls back to the synthesized
effect. Costs about 40 credits per second (the whole pack is roughly 1,000 credits).

Needs a paid ElevenLabs plan (commercial licence). Existing files are skipped unless --force.

Usage:
  python scripts/generate_sfx_pack.py            # dry run: lists effects and the credit estimate
  python scripts/generate_sfx_pack.py --apply
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.audio_engine import AudioEngine  # noqa: E402
from src.audio_provenance import record_sfx  # noqa: E402

URL = "https://api.elevenlabs.io/v1/sound-generation"
CREDITS_PER_SECOND = 40

# name: (prompt, seconds, loop). Names match AudioEngine.SFX_CUES and the bed's layers.
PACK = {
    "sfx_hammer.wav": ("A heavy sledgehammer strikes an old stone wall once, a dull crack and crumbling plaster "
                       "debris, dry close recording, no music", 2.0, False),
    "sfx_stone_door.wav": ("A massive round millstone door rolls slowly along a carved rock groove, deep grinding "
                           "rumble with stone-on-stone friction, small underground tunnel acoustics", 3.5, False),
    "sfx_gear_ratchet.wav": ("Small ancient bronze gears turn and click, a delicate mechanical ratchet, "
                             "close-up and intimate, quiet room", 2.0, False),
    "sfx_water_surge.wav": ("A deep underwater swell rolls past, muffled ocean current heard from below the "
                            "surface, low and calm", 3.0, False),
    "sfx_chisel.wav": ("An iron chisel struck by a wooden mallet carves soft stone, three steady strikes with "
                       "small chips falling, dry close recording", 2.0, False),
    "sfx_cavern_wind.wav": ("A low hollow wind draft moving through deep underground stone tunnels, subtle and "
                            "constant, no voices, no music", 10.0, True),
    # No impact/braam: two generations came back 2.9% and 4.4% clipped, so the clean synthesized
    # one stays (AudioEngine._synth_sub_bass_braam).
    "sfx_whoosh.wav": ("A soft airy cinematic transition whoosh, subtle and smooth, no impact", 1.0, False),
}


MAX_CLIPPED = 0.005  # share of samples at full scale above which a recording sounds distorted


def clipped_fraction(path: Path) -> float:
    import numpy as np
    import soundfile as sf
    audio, _ = sf.read(str(path))
    return float((np.abs(audio) >= 0.999).mean()) if audio.size else 1.0


def generate(name: str, prompt: str, seconds: float, loop: bool, key: str) -> bytes:
    """48 kHz WAV if the plan allows it, else high-bitrate MP3 converted to 48 kHz WAV."""
    body = {"text": prompt, "duration_seconds": seconds, "prompt_influence": 0.5, "loop": loop}
    headers = {"xi-api-key": key}
    for fmt in ("wav_48000", "mp3_44100_192"):
        r = requests.post(URL, params={"output_format": fmt}, json=body, headers=headers, timeout=120)
        if r.status_code in (400, 403, 422) and fmt == "wav_48000":
            print(f"    {name}: WAV not available on this plan ({r.status_code}); using MP3 192 kbps")
            continue
        r.raise_for_status()
        if fmt.startswith("wav"):
            return r.content
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "in.mp3", Path(tmp) / "out.wav"
            src.write_bytes(r.content)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", "48000", "-ac", "2", str(dst)],
                           check=True, timeout=60)
            return dst.read_bytes()
    raise RuntimeError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Generate (spends credits)")
    parser.add_argument("--force", action="store_true", help="Regenerate files that already exist")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="Only these effects (implies --force)")
    args = parser.parse_args()
    todo = {n: spec for n, spec in PACK.items() if (n in args.only if args.only else
            args.force or not (config.SFX_DIR / n).exists() or not (config.SFX_DIR / (n + ".rights.json")).exists())}
    estimate = sum(int(s * CREDITS_PER_SECOND) for _, s, _ in todo.values())
    print(f"{len(todo)} effects, about {estimate} credits: {', '.join(todo) or 'nothing to do'}")
    if not args.apply or not todo:
        return 0
    quota = AudioEngine().voice_quota() or {}
    tier = quota.get("tier")
    if not tier or tier == "free":
        print("A paid ElevenLabs plan is required (the free plan has no commercial licence).")
        return 1
    if quota.get("limit", 0) - quota.get("used", 0) < estimate:
        print("Not enough ElevenLabs credits for the pack.")
        return 1
    config.SFX_DIR.mkdir(parents=True, exist_ok=True)
    for name, (prompt, seconds, loop) in todo.items():
        path = config.SFX_DIR / name
        path.write_bytes(generate(name, prompt, seconds, loop, config.ELEVENLABS_API_KEY))
        clipped = clipped_fraction(path)
        if clipped > MAX_CLIPPED:
            # No rights record: the bed keeps using the clean synthesized effect instead.
            path.unlink()
            Path(str(path) + ".rights.json").unlink(missing_ok=True)
            print(f"  {name}: rejected, {clipped:.1%} of samples clipped (limit {MAX_CLIPPED:.1%}); synthesized effect kept")
            continue
        record_sfx(path, "elevenlabs-sound-effects", prompt, tier, commercial=True)
        print(f"  {name}: {seconds:g}s{' (loop)' if loop else ''}, {clipped:.2%} clipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
