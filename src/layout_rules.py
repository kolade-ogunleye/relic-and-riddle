"""
src/layout_rules.py
===================
Persistent layout rules and constraints ledger for the Shift-Left Layout Engine.
Tracks calibrated shelf gaps, text clearances, and learned adjustments across
aspect ratios (16:9 widescreen, 9:16 vertical) and topic runs.
"""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .config import DATA_DIR

log = logging.getLogger(__name__)

LAYOUT_RULES_PATH = DATA_DIR / "layout_rules.json"

DEFAULT_RULES: Dict[str, Any] = {
    "version": "1.0.0",
    "shelf_anchor": {
        "min_gap": 10,
        "sub_offset": 14,
        "title_offset": 10,
    },
    "constraints": {
        "min_text_gap": 24,
        "caption_margin_v_16x9": 1100,
        "caption_margin_v_9x16": 1380,
        "edge_safe_margin": 60,
        "max_healing_iterations": 3,
    },
    "aspect_ratio_rules": {
        "16:9": {
            "shelf_gap": 10,
            "min_padding": 24,
            "title_font_size": 32,
            "sub_font_size": 22,
        },
        "9:16": {
            "shelf_gap": 12,
            "min_padding": 28,
            "title_font_size": 36,
            "sub_font_size": 24,
        },
    },
    "learned_adjustments": {},
}


def load_layout_rules() -> Dict[str, Any]:
    """Loads persistent layout rules, creating the defaults if not found."""
    if not LAYOUT_RULES_PATH.exists():
        save_layout_rules(DEFAULT_RULES)
        return copy.deepcopy(DEFAULT_RULES)
    try:
        data = json.loads(LAYOUT_RULES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return copy.deepcopy(DEFAULT_RULES)
        return data
    except Exception as e:
        log.warning("Could not load layout rules from %s: %s; using defaults.", LAYOUT_RULES_PATH, e)
        return copy.deepcopy(DEFAULT_RULES)


def save_layout_rules(rules: Dict[str, Any]) -> None:
    """Persists layout rules to disk safely."""
    try:
        LAYOUT_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAYOUT_RULES_PATH.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.error("Failed writing layout rules to %s: %s", LAYOUT_RULES_PATH, e)


def get_layout_rule(key: str, aspect_ratio: Optional[str] = None, default: Any = None) -> Any:
    """Retrieves a rule with aspect ratio specific override if available."""
    rules = load_layout_rules()
    if aspect_ratio and aspect_ratio in rules.get("aspect_ratio_rules", {}):
        if key in rules["aspect_ratio_rules"][aspect_ratio]:
            return rules["aspect_ratio_rules"][aspect_ratio][key]
    if key in rules.get("constraints", {}):
        return rules["constraints"][key]
    if key in rules.get("shelf_anchor", {}):
        return rules["shelf_anchor"][key]
    return rules.get(key, default)


def record_learned_adjustment(topic_id: str, element_key: str, adjustment: Dict[str, Any]) -> None:
    """Records an auto-remediation calibration adjustment into the persistent ledger."""
    rules = load_layout_rules()
    adjustments = rules.setdefault("learned_adjustments", {})
    topic_entry = adjustments.setdefault(topic_id, {})
    topic_entry[element_key] = adjustment
    save_layout_rules(rules)
    log.info("Recorded learned layout adjustment for [%s:%s] -> %s", topic_id, element_key, adjustment)
