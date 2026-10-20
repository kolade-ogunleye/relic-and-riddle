"""
src/graphic_match.py
====================
Which narration a graphic belongs to. Every graphic records (in its lineage sidecar) the spec it
was drawn from, so its terms are the numbers, names and labels it actually shows on screen. A beat
matches when it says those terms as whole words: substring checks fired on "air" in "stairs" and
"era" in "several", and their keyword lists were one topic's (Derinkuyu's) vocabulary.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

from .lineage import sidecar
from .unesco import _distinctive, _fold

# Weighted so one shared figure or place name is enough, but one common word is not.
THRESHOLD = 2
NUMBER_WEIGHT, NAME_WEIGHT, WORD_WEIGHT = 2, 2, 1

# Payload field each builder draws from (src/graphics.py make_all).
SPEC_KEYS = {"numbers": "stat_cards", "cross-section": "cross_section", "route": "route", "timeline": "timeline",
             "scale": "scale_fact", "defense": "rolling_stone_defense", "ventilation": "ventilation_airflow",
             "lidar": "lidar_scan", "stratigraphy": "stratigraphy", "isometric": "isometric_cutaway"}
_STEM_KINDS = {"section": "cross-section"}

STOPWORDS = {
    "about", "above", "after", "again", "also", "around", "because", "been", "before", "being", "below",
    "between", "both", "could", "down", "during", "each", "estimated", "every", "from", "have", "here",
    "into", "just", "least", "less", "like", "many", "more", "most", "much", "only", "other", "over",
    "same", "some", "such", "than", "that", "their", "them", "then", "there", "these", "they", "this",
    "those", "through", "under", "until", "very", "were", "what", "when", "where", "which",
    "while", "with", "would", "years", "your", "approximately", "roughly", "nearly", "almost",
    "illustrative", "concept", "detail", "view", "section",  # generic diagram wording
}


def _number(token: str) -> str:
    """'20,000' -> '20000', '85.0' -> '85'."""
    plain = token.replace(",", "")
    return plain[:-2] if plain.endswith(".0") else plain


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def tokens(text: str) -> set:
    """Whole words (lightly singularized) and whole numbers in a piece of narration."""
    out = set()
    for t in re.findall(r"\d[\d,]*(?:\.\d+)?|[a-z][a-z'-]*", _fold(str(text))):
        out.add(_number(t) if t[0].isdigit() else _stem(t.strip("'-")))
    return out


def _leaves(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _leaves(v)
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, float):
        yield f"{obj:g}"
    else:
        yield str(obj)


def kind_of(path: Path) -> str:
    stem = Path(path).stem
    kind = stem[len("graphic_"):] if stem.startswith("graphic_") else ""
    return _STEM_KINDS.get(kind, kind)


def _declaration(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(sidecar(path).read_text(encoding="utf-8"))["settings"]["inputs"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def terms(path: Path) -> Dict[str, int]:
    """Weighted terms the graphic shows; empty when it declares nothing to judge it by."""
    inputs = _declaration(path)
    kind = inputs.get("builder") or kind_of(path)
    weighted: Dict[str, int] = {}
    if kind == "map":
        place = inputs.get("place") or {}
        for word in _distinctive(" ".join(str(place.get(k, "")) for k in ("label", "name", "country"))):
            weighted[_stem(word)] = NAME_WEIGHT
        return weighted
    if kind not in SPEC_KEYS:
        return weighted
    spec = (inputs.get("payload") or {}).get(SPEC_KEYS[kind])
    texts = list(_leaves(spec)) if spec else []
    if kind in ("defense", "ventilation"):  # these diagrams show default wording when the spec gives none
        from .graphics import diagram_labels, diagram_title
        spec = spec if isinstance(spec, dict) else {}
        texts += [kind, diagram_title(kind, spec), *diagram_labels(kind, spec).values()]
    if kind == "scale" and isinstance(spec, dict) and isinstance(spec.get("value"), (int, float)):
        factor = 0.3048 if spec.get("unit") == "ft" else 1 / 0.3048 if spec.get("unit") == "m" else None
        if factor:  # the narration may give the other unit ("280 feet (85 m)")
            texts.append(f"{round(spec['value'] * factor)}")
    for text in texts:
        for t in tokens(text):
            if t[0].isdigit():
                weighted[t] = NUMBER_WEIGHT
            elif len(t) > 3 and t not in STOPWORDS:
                weighted.setdefault(t, WORD_WEIGHT)
    return weighted


def score(path: Path, text: str) -> int:
    said = tokens(text)
    return sum(w for t, w in terms(path).items() if t in said)


def judgeable(path: Path) -> bool:
    return bool(terms(path))
