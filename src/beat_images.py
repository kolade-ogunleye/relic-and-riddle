"""
src/beat_images.py
==================
Per-beat image sourcing, so the picture matches what the narrator is saying.

For beats that name a concrete, photographable subject ("ventilation shafts", "rolling
stone doors"), Claude writes a precise Wikimedia Commons search; openly licensed,
title-matched, high-resolution results are downloaded into the run folder. The scene
planner then picks between the topic's reviewed images and these candidates, and the
reviewer sees them in the video before approving. CC BY images are credited.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from PIL import Image

from . import commons
from . import config
from .llm import LLMError, claude_available, structured_call, task as llm_task
from .models import BeatQueries

log = logging.getLogger(__name__)

CREDIT_PREFIX = "📷 Images via Wikimedia Commons:"
MAX_BEATS = 5          # only the most concrete beats get their own search
PER_BEAT = 2           # candidates per beat

SYSTEM = """You plan picture research for a short history documentary. For each narration beat that names a concrete, photographable subject (a specific place, structure, object, artefact or document), write one precise Wikimedia Commons search for it, plus 1-4 lowercase keywords that a relevant file title would contain (proper nouns are best). Skip abstract beats (ideas, numbers, open questions). Prefer the specific site over generic look-alikes."""


@llm_task("beat_queries")
def plan_queries(beats: List[Dict[str, Any]], topic: Dict[str, Any]) -> BeatQueries:
    lines = "\n".join(f"Beat {i}: {b['text']}" for i, b in enumerate(beats, 1))
    loc = topic.get("location", {})
    return structured_call(SYSTEM, f"Topic: {topic.get('title', '')} ({loc.get('name', '')}, {loc.get('country', '')})\n"
                                   f"{lines}\n\nAt most {MAX_BEATS} beats.", BeatQueries, effort="low", max_tokens=3000,
                           models=config.LIGHT_TASK_MODELS)


def source(beats: List[Dict[str, Any]], topic: Dict[str, Any], work_dir: Path, plan=None) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Returns (downloaded image paths, their credit records). Never raises: extra images are optional."""
    if not config.BEAT_IMAGES_ENABLED or not claude_available():
        return [], []
    if plan is None:
        try:
            plan = plan_queries(beats, topic)
        except LLMError as e:
            log.warning("Beat image planning failed: %s", e)
            return [], []
    work_dir.mkdir(parents=True, exist_ok=True)
    paths, credits, seen = [], [], set()
    for item in plan.items[:MAX_BEATS]:
        try:
            found = 0
            # Short wait cap: a rate-limited Commons shouldn't stall a production run.
            for cand in commons.candidates(commons.search(item.query, limit=30, max_wait=60), item.keywords,
                                            config.BEAT_IMAGES_ALLOW_SHARE_ALIKE):
                if cand["title"] in seen:
                    continue
                dest = work_dir / f"beat{item.beat:02d}_{found + 1}.jpg"
                commons.download_jpeg(cand["download_url"], dest)
                seen.add(cand["title"])
                cand.update(file=str(dest), beat=item.beat)
                paths.append(dest)
                credits.append(cand)
                found += 1
                if found >= PER_BEAT:
                    break
        except (requests.RequestException, OSError, Image.DecompressionBombError) as e:
            log.warning("Beat %d image search failed (%s); continuing.", item.beat, e)
    log.info("Beat images: %d candidates for %d beats.", len(paths), len(plan.items))
    return paths, credits


def used_credits(scenes: List[Path], credits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Credit records for the beat images the scene plan actually put in the video."""
    chosen = {Path(p) for p in scenes}
    return [c for c in credits if Path(c["file"]) in chosen or Path(c.get("upscaled") or c["file"]) in chosen]


def credit_caption(caption: str, topic_credits: Dict[str, Any], extra: List[Dict[str, Any]]) -> str:
    """Replaces the caption's image credit line with one covering the topic and beat images used.
    Idempotent, so a resumed run doesn't stack credit lines."""
    from .credits import caption_credit_line
    new = caption_credit_line({"images": topic_credits.get("images", []) + extra})
    kept = [ln for ln in caption.split("\n") if not ln.startswith(CREDIT_PREFIX)]
    body = "\n".join(kept).rstrip()
    return f"{body}\n\n{new}" if new else body


PICTURE_WORDS = ("image", "picture", "photo", "shows", "depict", "match", "contradict", "unrelated", "irrelevant")


def picture_issues(report) -> str:
    """Major/critical QA findings about what a beat shows (not captions), as planner feedback."""
    if report is None:
        return ""
    found = [i for i in report.issues
             if i.beat > 0 and i.severity in ("major", "critical") and any(w in i.issue.lower() for w in PICTURE_WORDS)]
    return "\n".join(f"Beat {i.beat}: {i.issue}" for i in found)
