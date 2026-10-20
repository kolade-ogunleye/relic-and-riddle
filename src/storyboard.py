"""
src/storyboard.py
=================
Script-First Storyboard Generator & Beat Synchronizer.

Bridges the narrative script with visual archival assets:
1. Deconstructs the topic's narration script into 5-8 cinematic beats:
   - Hook (opening visual tension)
   - Landscape / Setting (geographical scale & era)
   - Architectural / Subterranean Interior (structural feats)
   - Macro Artifact / Relic / Material (physical evidence)
   - Engineering Schematic / Survey Map (blueprints/cross-sections)
   - Climax & Debate / Outro
2. Maps each beat to the most relevant high-resolution archival image or footage clip.
3. Generates assets/<topic_id>/storyboard.json and renders a Storyboard Contact Sheet.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ASSETS_DIR, TOPICS_DIR
from .credits import load_credits, write_contact_sheet

log = logging.getLogger(__name__)

STORYBOARD_FILE = "storyboard.json"

DEFAULT_BEAT_ARCHETYPES = [
    {"type": "HOOK", "label": "Opening Hook", "keywords": ["discovery", "entrance", "wall", "found", "shocking", "mystery"]},
    {"type": "LANDSCAPE", "label": "Terrain & Scale", "keywords": ["landscape", "panorama", "aerial", "plateau", "ruins", "exterior", "mountains", "desert"]},
    {"type": "INTERIOR", "label": "Inner Structure", "keywords": ["interior", "chamber", "tunnel", "passage", "vault", "shaft", "hall", "corridor"]},
    {"type": "ARTIFACT", "label": "Physical Evidence", "keywords": ["artifact", "relic", "stone", "tool", "statue", "chisel", "door", "carving", "gear"]},
    {"type": "SCHEMATIC", "label": "Survey / Blueprint", "keywords": ["plan", "map", "blueprint", "diagram", "cross section", "drawing", "survey"]},
    {"type": "CLIMAX", "label": "Anomaly & Debate", "keywords": ["mystery", "anomaly", "depth", "ancient", "unsolved", "theory"]},
]


def load_storyboard(topic_dir: Path) -> Optional[List[Dict[str, Any]]]:
    path = topic_dir / STORYBOARD_FILE
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "beats" in data and isinstance(data["beats"], list):
                return data["beats"]
        except Exception as e:
            log.warning("Could not load %s: %s", path, e)
    return None


def save_storyboard(topic_dir: Path, storyboard: List[Dict[str, Any]]) -> Path:
    path = topic_dir / STORYBOARD_FILE
    path.write_text(json.dumps(storyboard, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _split_narration_sentences(narration: str) -> List[str]:
    """Splits narration into clean complete sentences or clauses."""
    # Split on sentence terminals while keeping text clean
    raw = re.split(r"(?<=[.!?])\s+", narration.strip())
    sentences = [s.strip() for s in raw if len(s.strip()) > 10]
    return sentences


def generate_storyboard(topic_id: str, topic_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Generates a script-first storyboard for a topic by aligning narration beats
    with reviewed or fetched archival assets. `topic_dir` defaults to assets/<topic_id>.
    """
    topic_dir = Path(topic_dir) if topic_dir is not None else ASSETS_DIR / topic_id
    if not topic_dir.is_dir():
        raise FileNotFoundError(f"Topic directory does not exist: {topic_dir}")

    credits = load_credits(topic_dir)
    images = credits.get("images", [])
    if not images:
        raise ValueError(f"No archival images found in {topic_dir.name} to build storyboard.")

    # 1. Attempt to find narration script
    script_file = Path("data") / "scripts" / f"{topic_id}.json"
    narration = ""
    hook = ""
    if script_file.is_file():
        try:
            sdata = json.loads(script_file.read_text(encoding="utf-8"))
            narration = sdata.get("narration_script", "")
            hook = sdata.get("hook_sentence", "")
        except Exception:
            pass

    # 2. Fallback to topic bank hook + key facts if no script file exists yet
    if not narration:
        bank_path = TOPICS_DIR / "topic_bank.json"
        if bank_path.is_file():
            try:
                bdata = json.loads(bank_path.read_text(encoding="utf-8"))
                for t in bdata.get("topics", []):
                    if t.get("id") == topic_id or topic_id.endswith(t.get("id", "")):
                        hook = t.get("hook", "")
                        facts = t.get("key_facts", [])
                        narration = f"{hook} " + " ".join(facts)
                        break
            except Exception:
                pass

    if not narration:
        # Generic beat placeholder if facts not found
        narration = f"The historical investigation into {topic_id.replace('_', ' ').title()}."

    sentences = _split_narration_sentences(narration)
    if not sentences:
        sentences = [narration]

    # 3. Create beats aligned with available images
    num_beats = min(len(images), max(5, len(sentences)))
    storyboard: List[Dict[str, Any]] = []
    used_images = set()

    for beat_idx in range(num_beats):
        archetype_info = DEFAULT_BEAT_ARCHETYPES[min(beat_idx, len(DEFAULT_BEAT_ARCHETYPES) - 1)]
        b_type = archetype_info["type"]
        b_label = archetype_info["label"]

        # Sentence for this beat
        if beat_idx < len(sentences):
            beat_text = sentences[beat_idx]
        else:
            beat_text = sentences[-1]

        # Match best image candidate based on keywords in title
        best_img = None
        best_score = -1

        for img in images:
            if img["file"] in used_images:
                continue
            title_lower = (img.get("title", "") + " " + img.get("file", "")).lower()
            score = 0
            for kw in archetype_info["keywords"]:
                if kw in title_lower:
                    score += 2
            for word in beat_text.lower().split():
                if len(word) > 4 and word in title_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best_img = img

        if not best_img:
            # Fallback to unused image in order
            for img in images:
                if img["file"] not in used_images:
                    best_img = img
                    break

        if not best_img:
            best_img = images[beat_idx % len(images)]

        used_images.add(best_img["file"])

        storyboard.append({
            "beat": beat_idx + 1,
            "type": b_type,
            "label": b_label,
            "text": beat_text,
            "file": best_img["file"],
            "title": best_img.get("title", ""),
            "license": best_img.get("license", "Public Domain"),
        })

    save_storyboard(topic_dir, storyboard)
    write_contact_sheet(topic_dir, images, storyboards=storyboard)
    return storyboard
