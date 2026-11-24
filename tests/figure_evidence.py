"""Lane B c3b: synthetic layout payloads whose drawn figures the run's evidence signs.

Every surface that draws a figure from copy holds it to the run's evidence (visual_engine.gate_drawn). A layout test
that draws made-up copy with figures and no narration or fact sheet would have every figure withheld; `signed` gives
such a payload an inline fact sheet stating its own drawn copy, so the test still measures the layout it is about.
A test about the gate itself never uses this.
"""
from __future__ import annotations

from typing import Any


def _texts(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for slide in list(payload.get("carousel_slides") or []) + list(payload.get("story_slides") or []):
        if isinstance(slide, dict):
            out += [str(v) for k, v in slide.items() if k in ("heading", "body", "line1", "line2") and v]
    poll = payload.get("poll") or {}
    if isinstance(poll, dict) and poll.get("question"):
        out.append(str(poll["question"]))
    th = payload.get("thumbnail") or {}
    out += [str(v) for v in th.values() if isinstance(v, str) and v] if isinstance(th, dict) else []
    out += [str(t) for t in payload.get("photo_labels") or []]
    if payload.get("share_line"):
        out.append(str(payload["share_line"]))
    return out


def signed(payload: dict[str, Any]) -> dict[str, Any]:
    """`payload` with an inline fact sheet whose statements are its own drawn copy (see above)."""
    return dict(payload, fact_sheet={"facts": [{"id": f"copy-{i}", "statement": t}
                                               for i, t in enumerate(_texts(payload), 1)]})
