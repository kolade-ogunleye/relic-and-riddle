"""
Fetch a free music library (CC0 / CC BY) from Openverse, by mood.

Tracks are saved to data/music/<mood>/ with title, creator, licence and source recorded
in data/music/library.json. They start UNREVIEWED: listen and approve them with /music in
Telegram (bot or daemon running) before they are used.

Usage:
  python scripts/fetch_music.py                 # up to 4 tracks per mood
  python scripts/fetch_music.py --per-mood 6
"""

import argparse
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.music import MOODS, MUSIC_DIR, load_library  # noqa: E402

API = "https://api.openverse.org/v1/audio/"
HEADERS = {"User-Agent": "RelicAndRiddle/1.0 (music library fetcher)"}
QUERIES = {
    "mysterious": ["mysterious ambient", "dark ambient cinematic", "ancient mystery music"],
    "epic": ["epic cinematic orchestral", "cinematic strings", "inspiring orchestral"],
    "melancholic": ["melancholic piano", "sad strings", "reflective piano ambient"],
    "tense": ["suspense cinematic", "tense underscore", "thriller ambient"],
}
MIN_MS, MAX_MS = 50_000, 420_000
SKIP_WORDS = re.compile(r"\b(vocal|vocals|voice|song|rap|remix|loop|sfx|field recording|birds?|rain)\b", re.I)


def search(query: str) -> list:
    params = {"q": query, "license": "cc0,by", "page_size": 20, "mature": "false"}
    r = requests.get(API, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def fetch_music_tracks(per_mood: int = 4, *, mood=None, add_count=None) -> int:
    from src.operator_jobs import checkpoint
    from src.music import register_download
    if mood is not None and mood not in MOODS:
        raise ValueError("Unknown music mood")
    library = load_library(available_only=False)  # never drop records on rewrite
    have = {t["source_url"] for t in library}
    added = 0
    for current_mood in ([mood] if mood else MOODS):
        checkpoint()
        mood = current_mood
        count = sum(1 for t in library if t["mood"] == mood)
        target = count + add_count if add_count is not None else per_mood
        for query in QUERIES[mood]:
            checkpoint()
            if count >= target:
                break
            for r in search(query):
                checkpoint()
                if r.get("license") not in {"cc0", "by"} or not r.get("license_url"):
                    continue
                if count >= target:
                    break
                dur = r.get("duration") or 0
                title = r.get("title") or ""
                if (r.get("foreign_landing_url") in have or not (MIN_MS <= dur <= MAX_MS)
                        or r.get("filetype") not in ("mp3", "mp32") or SKIP_WORDS.search(title)):
                    continue
                (MUSIC_DIR / mood).mkdir(parents=True, exist_ok=True)
                name = f"{mood}/{re.sub(r'[^a-z0-9]+', '_', title.lower())[:40].strip('_')}_{r['id'][:8]}.mp3"
                try:
                    with requests.get(r["url"], headers=HEADERS, timeout=30, stream=True) as audio:
                        audio.raise_for_status()
                        data = bytearray()
                        for chunk in audio.iter_content(65536):
                            checkpoint()
                            data.extend(chunk)
                            if len(data) > 32 * 1024 * 1024:
                                raise ValueError("Music download exceeds 32 MiB")
                    is_mp3 = data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
                    if not is_mp3:
                        continue
                except requests.RequestException:
                    continue
                checkpoint()
                track = {
                    "file": name, "title": title, "creator": r.get("creator") or "Unknown",
                    "license": r.get("license", ""), "license_version": r.get("license_version", ""),
                    "license_url": r.get("license_url", ""), "source_url": r.get("foreign_landing_url", ""),
                    "source": r.get("source", ""), "mood": mood, "duration_ms": dur, "reviewed": False, "uses": 0,
                }
                if not register_download(track, data):
                    continue
                library.append(track)
                have.add(r.get("foreign_landing_url"))
                count += 1
                added += 1
                time.sleep(0.5)
    return added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-mood", type=int, default=4)
    args = parser.parse_args()
    added = fetch_music_tracks(args.per_mood)
    print(f"Downloaded {added} track(s). Review and approve them through /music.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
