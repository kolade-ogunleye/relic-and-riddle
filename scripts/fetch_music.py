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

from src.music import MOODS, MUSIC_DIR, load_library, save_library  # noqa: E402

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


def fetch_music_tracks(per_mood: int = 4) -> int:
    library = load_library()
    have = {t["source_url"] for t in library}
    added = 0
    for mood in MOODS:
        count = sum(1 for t in library if t["mood"] == mood)
        for query in QUERIES[mood]:
            if count >= per_mood:
                break
            for r in search(query):
                if count >= per_mood:
                    break
                dur = r.get("duration") or 0
                title = r.get("title") or ""
                if (r.get("foreign_landing_url") in have or not (MIN_MS <= dur <= MAX_MS)
                        or r.get("filetype") not in ("mp3", "mp32") or SKIP_WORDS.search(title)):
                    continue
                (MUSIC_DIR / mood).mkdir(parents=True, exist_ok=True)
                name = f"{mood}/{re.sub(r'[^a-z0-9]+', '_', title.lower())[:40].strip('_')}_{r['id'][:8]}.mp3"
                try:
                    audio = requests.get(r["url"], headers=HEADERS, timeout=120)
                    audio.raise_for_status()
                    data = audio.content
                    is_mp3 = data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
                    if not is_mp3:
                        continue
                    (MUSIC_DIR / name).write_bytes(audio.content)
                except requests.RequestException:
                    continue
                library.append({
                    "file": name, "title": title, "creator": r.get("creator") or "Unknown",
                    "license": r.get("license", ""), "license_version": r.get("license_version", ""),
                    "license_url": r.get("license_url", ""), "source_url": r.get("foreign_landing_url", ""),
                    "source": r.get("source", ""), "mood": mood, "duration_ms": dur, "reviewed": False, "uses": 0,
                })
                have.add(r.get("foreign_landing_url"))
                count += 1
                added += 1
                time.sleep(0.5)
        save_library(library)
    return added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-mood", type=int, default=4)
    args = parser.parse_args()

    library = load_library()
    have = {t["source_url"] for t in library}
    for mood in MOODS:
        count = sum(1 for t in library if t["mood"] == mood)
        for query in QUERIES[mood]:
            if count >= args.per_mood:
                break
            for r in search(query):
                if count >= args.per_mood:
                    break
                dur = r.get("duration") or 0
                title = r.get("title") or ""
                if (r.get("foreign_landing_url") in have or not (MIN_MS <= dur <= MAX_MS)
                        or r.get("filetype") not in ("mp3", "mp32") or SKIP_WORDS.search(title)):
                    continue
                (MUSIC_DIR / mood).mkdir(parents=True, exist_ok=True)
                name = f"{mood}/{re.sub(r'[^a-z0-9]+', '_', title.lower())[:40].strip('_')}_{r['id'][:8]}.mp3"
                try:
                    audio = requests.get(r["url"], headers=HEADERS, timeout=120)
                    audio.raise_for_status()
                    data = audio.content
                    is_mp3 = data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
                    if not is_mp3:
                        print(f"  skip (not an mp3): {title}")
                        continue
                    (MUSIC_DIR / name).write_bytes(audio.content)
                except requests.RequestException as e:
                    print(f"  skip {title}: {e}")
                    continue
                library.append({
                    "file": name, "title": title, "creator": r.get("creator") or "Unknown",
                    "license": r.get("license", ""), "license_version": r.get("license_version", ""),
                    "license_url": r.get("license_url", ""), "source_url": r.get("foreign_landing_url", ""),
                    "source": r.get("source", ""), "mood": mood, "duration_ms": dur, "reviewed": False, "uses": 0,
                })
                have.add(r.get("foreign_landing_url"))
                count += 1
                print(f"  + {mood}: {title} ({r.get('license')} {r.get('license_version')}, {dur // 1000}s)")
                time.sleep(1)
        save_library(library)
    pending = sum(1 for t in library if not t["reviewed"])
    print(f"Library: {len(library)} tracks, {pending} waiting for review (send /music in Telegram).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
