"""
src/music.py
============
Background music for each Reel, best source first:

  1. ElevenLabs Music: an instrumental score generated to the Reel's mood and exact
     length. Used only on a paid ElevenLabs plan (the free tier lists no commercial use).
  2. Free library: CC0 / CC BY tracks found via Openverse (scripts/fetch_music.py). They
     start unreviewed and are used only after a person approves them (/music in Telegram).
     CC BY tracks are credited in the caption.
  3. None: the audio engine falls back to its synthesized drone.

The mood comes from a cheap Claude classification of the script (default "mysterious").
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import requests
from pydantic import BaseModel

from .config import DATA_DIR, ELEVENLABS_API_KEY, LIGHT_TASK_MODELS, MUSIC_PREFER, is_configured
from .llm import LLMError, task as llm_task

log = logging.getLogger(__name__)

MUSIC_DIR = DATA_DIR / "music"
LIBRARY = MUSIC_DIR / "library.json"
MOODS = ("mysterious", "epic", "melancholic", "tense")

MOOD_PROMPTS = {
    "mysterious": "slow, mysterious ambient documentary score, low strings and soft pads, subtle pulse, no vocals",
    "epic": "cinematic documentary score building sense of awe and scale, warm strings, gentle percussion, no vocals",
    "melancholic": "melancholic, reflective documentary score, solo piano and soft strings, no vocals",
    "tense": "tense, suspenseful documentary underscore, low drones, ticking percussion, no vocals",
}


class MoodChoice(BaseModel):
    mood: Literal["mysterious", "epic", "melancholic", "tense"]


@llm_task("music_mood")
def choose_mood(payload: Dict[str, Any]) -> str:
    from . import free_llm
    if not free_llm.ready():
        return "mysterious"
    try:
        choice = free_llm.call(
            "Pick the background-music mood that best fits this short history documentary narration.",
            f"Title: {payload.get('title', '')}\nNarration: {payload.get('narration_script', '')}\n"
            f"Moods: {', '.join(MOODS)}.", MoodChoice, task_class="light", effort="low", max_tokens=1000,
            models=LIGHT_TASK_MODELS)
        return choice.mood
    except LLMError as e:
        log.warning("Mood classification failed (%s); using 'mysterious'.", e)
        return "mysterious"


# ------------------------------------------------------------------ library
def load_library(available_only: bool = True) -> List[Dict[str, Any]]:
    """Tracks whose audio is on this machine (for picking). Anything that rewrites the library must
    pass available_only=False: a record is never dropped just because its file is missing here (a
    fresh machine before the audio is copied would otherwise lose the whole library)."""
    if not LIBRARY.exists():
        return []
    tracks = json.loads(LIBRARY.read_text(encoding="utf-8"))
    return [t for t in tracks if (MUSIC_DIR / t["file"]).exists()] if available_only else tracks


def save_library(tracks: List[Dict[str, Any]]) -> None:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY.write_text(json.dumps(tracks, indent=2, ensure_ascii=False), encoding="utf-8")


def pick_library_track(mood: str, min_seconds: float = 0, exclude: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """An approved track in the mood (any approved track as a fallback), long enough if min_seconds > 0."""
    exclude = set(exclude or [])
    tracks = [t for t in load_library() if t.get("reviewed") and t["file"] not in exclude]
    if not tracks:
        return None
    in_mood = [t for t in tracks if t["mood"] == mood]
    pool = in_mood or tracks
    long_enough = [t for t in pool if t["duration_ms"] >= min_seconds * 1000]
    if min_seconds > 0:
        if not long_enough:
            return None
        candidate_pool = long_enough
    else:
        candidate_pool = pool
    return min(candidate_pool, key=lambda t: t.get("uses", 0))  # rotate: least-used first


def pick_documentary_movements(primary_mood: str, num_movements: int = 3, exclude: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Selects 2-3 distinct complementary movements across documentary acts (e.g. Mysterious -> Epic -> Tense)."""
    exclude = set(exclude or [])
    tracks = [t for t in load_library() if t.get("reviewed") and t["file"] not in exclude]
    if not tracks:
        return []
    arc_order = [primary_mood]
    complementary = ["epic", "mysterious", "tense", "melancholic"]
    for c in complementary:
        if c != primary_mood and c not in arc_order:
            arc_order.append(c)

    chosen: List[Dict[str, Any]] = []
    used_files = set(exclude)
    for m in arc_order:
        tr = pick_library_track(m, min_seconds=0, exclude=list(used_files))
        if tr:
            chosen.append(tr)
            used_files.add(tr["file"])
        if len(chosen) >= num_movements:
            break
    return chosen or ([pick_library_track(primary_mood)] if pick_library_track(primary_mood) else [])


def mark_used(file: str) -> None:
    tracks = load_library(available_only=False)
    for t in tracks:
        if t["file"] == file:
            t["uses"] = t.get("uses", 0) + 1
    save_library(tracks)


def license_name(track: Dict[str, Any]) -> str:
    """Openverse reports e.g. "by" / "4.0"; the proper attribution form is "CC BY 4.0"."""
    lic = track.get("license", "").strip().upper()
    if lic and not lic.startswith("CC") and lic not in ("PDM",):
        lic = "CC " + lic
    return f"{lic} {track.get('license_version', '')}".strip()


def credit_line(track: Dict[str, Any]) -> str:
    if track.get("license", "").lower().startswith("cc0"):
        return ""
    return f"🎵 Music: “{track['title']}” by {track['creator']} ({license_name(track)})"


# ------------------------------------------------------------------ ElevenLabs
def elevenlabs_paid(voice_quota: Optional[dict]) -> bool:
    return bool(voice_quota) and str(voice_quota.get("tier", "free")).lower() not in ("free", "")


def generate_elevenlabs(mood: str, seconds: float, out_path: Path) -> Path:
    resp = requests.post(
        "https://api.elevenlabs.io/v1/music",
        params={"output_format": "mp3_44100_192"},
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={"prompt": MOOD_PROMPTS[mood], "music_length_ms": int(min(600, max(10, seconds)) * 1000),
              "force_instrumental": True},
        timeout=300,
    )
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    return out_path


def music_for(payload: Dict[str, Any], seconds: float, work_dir: Path, voice_quota: Optional[dict],
              is_documentary: bool = False) -> Dict[str, Any]:
    """
    Returns {"path": Path|None, "paths": List[Path]|None, "source": "elevenlabs"|"library"|"none", "mood": str, "credit": str, "file": str}.
    """
    mood = choose_mood(payload)

    def library():
        can_eleven = bool(is_configured(ELEVENLABS_API_KEY) and elevenlabs_paid(voice_quota))
        if is_documentary and not can_eleven:
            movements = pick_documentary_movements(mood, num_movements=3)
            if movements:
                paths = [MUSIC_DIR / t["file"] for t in movements]
                credits = [credit_line(t) for t in movements if credit_line(t)]
                for t in movements:
                    mark_used(t["file"])
                log.info("Music: documentary multi-movement suite (%d tracks: %s).",
                         len(movements), ", ".join(t["mood"] for t in movements))
                return {"path": paths[0], "paths": paths, "source": "library", "mood": mood,
                        "credit": " \n".join(credits), "file": movements[0]["file"],
                        "tracks": [{**t, "path": str(MUSIC_DIR / t["file"])} for t in movements]}
        track = pick_library_track(mood, seconds)
        if not track and not can_eleven:
            track = pick_library_track(mood, 0)
        if track:
            log.info("Music: library track '%s' (%s).", track["title"], track["mood"])
            mark_used(track["file"])
            return {"path": MUSIC_DIR / track["file"], "paths": [MUSIC_DIR / track["file"]], "source": "library", "mood": mood,
                    "credit": credit_line(track), "file": track["file"],
                    "tracks": [{**track, "path": str(MUSIC_DIR / track["file"])}]}
        return None

    def elevenlabs():
        if not (is_configured(ELEVENLABS_API_KEY) and elevenlabs_paid(voice_quota)):
            return None
        try:
            path = generate_elevenlabs(mood, seconds + 1, work_dir / "music_elevenlabs.mp3")
            log.info("Music: ElevenLabs score (%s).", mood)
            return {"path": path, "paths": [path], "source": "elevenlabs", "mood": mood, "credit": "", "file": ""}
        except (requests.RequestException, OSError) as e:
            log.warning("ElevenLabs Music failed (%s).", e)
            return None

    order = (elevenlabs, library) if MUSIC_PREFER == "elevenlabs" else (library, elevenlabs)
    for option in order:
        result = option()
        if result:
            return result
    log.info("Music: no paid plan or approved library track; using the synthesized drone.")
    return {"path": None, "paths": None, "source": "none", "mood": mood, "credit": "", "file": ""}


# ------------------------------------------------------------------ review
def track_key(track: Dict[str, Any]) -> str:
    """Short stable key for Telegram buttons: the 8-character id at the end of the file name."""
    return Path(track["file"]).stem.rsplit("_", 1)[-1]


def pending_tracks() -> List[Dict[str, Any]]:
    return [t for t in load_library() if not t.get("reviewed")]


def _find(key: str) -> Dict[str, Any]:
    for t in load_library():
        if track_key(t) == key:
            return t
    raise ValueError("That track is no longer in the library.")


def approve_track(key: str) -> str:
    tracks = load_library(available_only=False)
    for t in tracks:
        if track_key(t) == key:
            t["reviewed"] = True
            save_library(tracks)
            return f"Kept “{t['title']}” ({t['mood']})."
    raise ValueError("That track is no longer in the library.")


def remove_track(key: str) -> str:
    track = _find(key)
    (MUSIC_DIR / track["file"]).unlink(missing_ok=True)
    save_library([t for t in load_library(available_only=False) if t["file"] != track["file"]])
    return f"Removed “{track['title']}”."


def review_caption(track: Dict[str, Any], remaining: int) -> str:
    import html
    lic = license_name(track)
    return (f"🎵 <b>{html.escape(track['title'])}</b> by {html.escape(track['creator'])}\n"
            f"Mood: {html.escape(track['mood'])} · {track['duration_ms'] // 1000}s · {html.escape(lic)}\n"
            f"{html.escape(track.get('source_url', ''))}\n"
            f"{remaining} track(s) left to review. Keep it if it would sit well under a documentary voice.")
