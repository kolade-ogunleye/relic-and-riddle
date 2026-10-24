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
from .llm import LLMError, structured_call, task as llm_task
from .models import BeatQueries

log = logging.getLogger(__name__)

CREDIT_PREFIX = "📷 Images via Wikimedia Commons:"
MAX_BEATS = 5          # only the most concrete beats get their own search
PER_BEAT = 2           # candidates per beat

SYSTEM = """You plan picture research for a short history documentary. For each narration beat that names a concrete, photographable subject (a specific place, structure, object, artefact or document), write one precise Wikimedia Commons search for it, plus 1-4 lowercase keywords that a relevant file title would contain (proper nouns are best). Skip abstract beats (ideas, numbers, open questions). Prefer the specific site, archaeological drill core, museum specimen, or scientific micrograph over generic scenic landscape look-alikes.
When a beat's subject stands somewhere other than the documentary's own site (a prototype or model it was based on, a museum holding the object, a rival city), set place to that place's name, exactly as a Commons category would spell it; otherwise leave place null."""


def verify_visual_relevance(cand_title: str, query: str = "") -> bool:
    """Rejects modern tourist photos, gift shops, unrelated street scenes, or generic signage."""
    text = (cand_title + " " + query).lower()
    reject_patterns = ("tourist", "souvenir", "ticket", "restaurant", "hotel", "cafe", "bus", "parking", "gift shop", "selfie", "modern street")
    return not any(p in text for p in reject_patterns)


@llm_task("beat_queries")
def plan_queries(beats: List[Dict[str, Any]], topic: Dict[str, Any]) -> BeatQueries:
    lines = "\n".join(f"Beat {i}: {b['text']}" for i, b in enumerate(beats, 1))
    loc = topic.get("location", {})
    prompt = f"Topic: {topic.get('title', '')} ({loc.get('name', '')}, {loc.get('country', '')})\n{lines}\n\nAt most {MAX_BEATS} beats."
    from . import free_llm
    if free_llm.claude_engine():
        return structured_call(SYSTEM, prompt, BeatQueries, effort="low", max_tokens=3000,
                               models=config.LIGHT_TASK_MODELS)
    return free_llm.structured(SYSTEM, prompt, BeatQueries, role="writer", task_class="light")


def source(beats: List[Dict[str, Any]], topic: Dict[str, Any], work_dir: Path, plan=None) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Returns (downloaded image paths, their credit records). Never raises: extra images are optional."""
    if not config.BEAT_IMAGES_ENABLED:
        return [], []
    from . import free_llm
    if not free_llm.ready():
        return [], []
    if plan is None:
        try:
            plan = plan_queries(beats, topic)
        except LLMError as e:
            log.warning("Beat image planning failed: %s", e)
            return [], []
    paths, credits = _download(plan.items[:MAX_BEATS], topic, work_dir, set(), "beat")
    log.info("Beat images: %d candidates for %d beats.", len(paths), len(plan.items))
    return paths, credits


GAP_SYSTEM = """You plan picture research for a short history documentary. The video check found that these beats' pictures do not show what the narration says. For each beat, write one precise Wikimedia Commons search for what that sentence is about, plus 1-4 lowercase keywords that a relevant file title would contain. Stay on the documentary's subject: the thing named, or the same kind of thing from the same civilisation or period (for "Roman aqueducts", a Roman aqueduct; for "modern concrete cracking", a photograph of cracked concrete), never a look-alike from another subject. Prefer a specific named example (a named aqueduct, harbour, specimen or document) over a generic scene.
When the thing named stands somewhere other than the documentary's own site (a prototype it was modelled on, a museum, another city), set place to that place's name as a Commons category would spell it; otherwise leave place null."""


@llm_task("beat_gap_queries")
def plan_gap_queries(beats: List[Dict[str, Any]], gap_beats: List[int], topic: Dict[str, Any], notes: str = "") -> BeatQueries:
    lines = "\n".join(f"Beat {i}: {beats[i - 1]['text']}" for i in gap_beats if 1 <= i <= len(beats))
    loc = topic.get("location", {})
    prompt = (f"Topic: {topic.get('title', '')} ({loc.get('name', '')}, {loc.get('country', '')})\n{lines}"
              + (f"\n\nWhat the video check found:\n{notes}" if notes else "") + "\n\nOne search per beat listed.")
    from . import free_llm
    if free_llm.claude_engine():
        return structured_call(GAP_SYSTEM, prompt, BeatQueries, effort="low", max_tokens=3000,
                               models=config.LIGHT_TASK_MODELS)
    return free_llm.structured(GAP_SYSTEM, prompt, BeatQueries, role="writer", task_class="light")


def source_gaps(beats: List[Dict[str, Any]], gap_beats: List[int], topic: Dict[str, Any], work_dir: Path,
                seen: set, notes: str = "") -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Pictures for the beats the video check found mismatched, when the first pictures did not cover them:
    a search for what each of those sentences says, from Wikimedia Commons, through the same licence,
    title-keyword, relevance and visual filters as the first search. Never raises."""
    if not gap_beats or not config.BEAT_IMAGES_ENABLED:
        return [], []
    from . import free_llm
    if not free_llm.ready():
        return [], []
    try:
        plan = plan_gap_queries(beats, gap_beats, topic, notes)
    except LLMError as e:
        log.warning("Gap picture planning failed: %s", e)
        return [], []
    wanted = set(gap_beats)
    # A repair comes at the end of a long run: its downloads may wait out a rate limit (90 s, not 30).
    paths, credits = _download([i for i in plan.items if i.beat in wanted], topic, work_dir, seen, "gap", wait=90)
    log.info("Gap pictures: %d candidates for beats %s.", len(paths), ", ".join(map(str, sorted(wanted))))
    return paths, credits


def _download(items, topic: Dict[str, Any], work_dir: Path, seen: set, prefix: str,
              wait: int = 30) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Downloads the best openly licensed, on-subject Commons results for each planned search; records them
    in the folder's credits.json (added to, so a later search keeps the earlier records)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    loc = topic.get("location") or {}
    place = ({"lat": loc["latitude"], "lon": loc["longitude"]}
             if loc.get("latitude") and loc.get("longitude") else None)
    paths, credits = [], []
    for item in items:
        try:
            found = 0
            # A beat about a place other than the topic's site (a prototype, a museum) is ranked by that
            # place's name, not by distance from the site: the right photo of the Virupaksha Temple at
            # Pattadakal, 430 km from Ellora, scored 3/10 for being where Pattadakal is (2026-10-24).
            keywords, near = item.keywords, place
            elsewhere = (getattr(item, "place", None) or "").strip()
            if elsewhere and elsewhere.lower() not in (loc.get("name") or "").lower():
                keywords = list(item.keywords) + [w for w in commons.normalize(elsewhere).split() if len(w) > 3]
                near = None
            # Short wait cap: a rate-limited Commons shouldn't stall a production run. Results are
            # ranked by what their metadata says they show (commons.relevance), best first.
            ranked = commons.best_first(list(commons.candidates(commons.search(item.query, limit=30, max_wait=60),
                                                                keywords, config.BEAT_IMAGES_ALLOW_SHARE_ALIKE)),
                                        keywords, near)
            for cand in ranked:
                if cand["title"] in seen or cand["relevance"] <= 0:
                    continue
                if not verify_visual_relevance(cand["title"], item.query):
                    continue
                dest = work_dir / f"{prefix}{item.beat:02d}_{found + 1}.jpg"
                commons.download_jpeg(cand["download_url"], dest, max_wait=wait)
                seen.add(cand["title"])
                cand.update(file=str(dest), beat=item.beat)
                paths.append(dest)
                credits.append(cand)
                found += 1
                if found >= PER_BEAT:
                    break
        except (requests.RequestException, OSError, Image.DecompressionBombError) as e:
            log.warning("Beat %d image search failed (%s); continuing.", item.beat, e)
    if credits:  # the same record a topic folder keeps, so the scene planner can caption these too
        import json
        record = work_dir / "credits.json"
        try:
            earlier = json.loads(record.read_text(encoding="utf-8")).get("images", []) if record.exists() else []
        except (OSError, ValueError):
            earlier = []
        record.write_text(json.dumps(
            {"reviewed": False, "images": earlier + [{**c, "file": Path(c["file"]).name} for c in credits]},
            indent=2, ensure_ascii=False), encoding="utf-8")
        from .scene_planner import _folder_credits
        _folder_credits.cache_clear()  # the planner reads captions through a cache: show it the new ones
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


# "visual"/"display": Roman concrete's beat 9 ("the visual displays the interior dome") matched none of the
# older words, so the mismatch was never repaired.
# Word stems: "shows", "showing" and "show" all count (a finding that said "the video continues to show
# the Kailasa Temple ... rather than the prototype temple mentioned" matched nothing and went unrepaired,
# production 2026-10-24).
PICTURE_WORDS = ("image", "picture", "photo", "show", "depict", "match", "contradict", "unrelated", "irrelevant",
                 "visual", "display", "footage", "scene", "video", "frame", "b-roll", "clip")
CAPTION_ONLY = ("caption", "subtitle")  # a finding about the text on screen, unless it is also about the picture


# A minor finding counts only when it says the picture contradicts what is said (Roman concrete's beat 8:
# "...the Pantheon while the narration describes modern concrete cracking"), not a quality note ("soft").
MISMATCH_WORDS = ("narration", "voiceover", "contradict", "unrelated", "irrelevant", "instead of", "does not show")


def _picture_findings(report) -> list:
    """QA findings about what a beat shows (not its captions or text): major/critical ones, and minor ones
    that report a mismatch with the narration."""
    if report is None:
        return []
    out = []
    for i in report.issues:
        text = i.issue.lower()
        if i.beat <= 0 or not any(w in text for w in PICTURE_WORDS):
            continue
        if any(w in text for w in CAPTION_ONLY) and not any(w in text for w in ("video", "footage", "frame", "image", "photo", "picture", "show")):
            continue
        if i.severity in ("major", "critical") or any(w in text for w in MISMATCH_WORDS):
            out.append(i)
    return out


def picture_issues(report) -> str:
    """QA findings about what a beat shows, as planner feedback. Minor ones count too: a picture that does
    not show what is said is a mismatch whatever its rating (beat 8 of Roman concrete was "minor")."""
    return "\n".join(f"Beat {i.beat}: {i.issue}" for i in _picture_findings(report))


def mismatched_beats(report) -> List[int]:
    """The beats whose picture does not show what is said, for a targeted picture search."""
    return sorted({i.beat for i in _picture_findings(report)})
