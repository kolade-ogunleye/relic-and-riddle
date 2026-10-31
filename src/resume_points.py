"""When a failed run is worth making again in place, and what that keeps (operator, 2026-10-25: "establish when
it is worth it to be able to re-render and not ... from one point, between, and the end of a rendered video").

A run passes these points, each saved as it completes (checkpoints.json and the run folder). The later the
failure, the more a resume keeps:

  1. script      the fact-checked script: the writer and the fact check, 5-15 minutes and the run's AI spend,
                 and a script that already passed. The first point worth resuming from.
  2. voiceover   the paid narration (ElevenLabs); kept only if it fits the length limit, otherwise made again.
  3. sound       music and the ambient bed with its licensed effects.
  4. pictures    photos found and checked for the beats, graphics, paid motion clips, parallax.
  5. video       the rendered master (a failure at the quality check or the release gate).

Before point 1 nothing paid or slow is kept (the topic's fact sheet is cached per topic and every new run
reuses it anyway), so the topic is simply rendered again. Every point is fingerprinted with its inputs, so a
resume re-makes any stage whose inputs changed since (new rules, an edited fact sheet) rather than reusing it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

POINTS = (
    ("script", "the fact-checked script"),
    ("voiceover", "the voiceover"),
    ("sound", "the music and sound"),
    ("pictures", "the photos, graphics and motion clips"),
    ("video", "the rendered video"),
)


def _has_files(directory: Path, pattern: str = "*") -> bool:
    return directory.is_dir() and any(p.is_file() for p in directory.glob(pattern))


def reached(run_dir: Path, run: Optional[Dict[str, Any]] = None) -> List[str]:
    """The points this run completed, in order, from its own records."""
    try:
        stages = json.loads((Path(run_dir) / "checkpoints.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stages = {}
    run_dir = Path(run_dir)
    done = {
        "script": "script" in stages and (run_dir / "verified_script.json").is_file(),
        "voiceover": "voice" in stages and (run_dir / "voiceover.mp3").is_file(),
        "sound": "bed" in stages and (run_dir / "ambient_bed.wav").is_file(),
        "pictures": _has_files(run_dir / "motion", "*.mp4") or _has_files(run_dir / "graphics", "*.mp4")
                    or _has_files(run_dir / "parallax", "*.mp4"),
        "video": bool((run or {}).get("master_video_path")) and Path(str((run or {}).get("master_video_path"))).is_file(),
    }
    return [name for name, _ in POINTS if done[name]]


def worth_resuming(run_dir: Path, run: Optional[Dict[str, Any]] = None) -> bool:
    """A failed run is made again in place once it has a fact-checked script; before that, render the topic."""
    return Path(run_dir).is_dir() and "script" in reached(run_dir, run)


def kept_phrase(run_dir: Path, run: Optional[Dict[str, Any]] = None) -> str:
    """What a resume keeps, for the failure card: "the fact-checked script and the voiceover"."""
    labels = dict(POINTS)
    names = [labels[p] for p in reached(run_dir, run)]
    if not names:
        return ""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
