"""Shared by the PLATES records proofs (freeze cycle 5: PLATES-04, 12, 13, 14, 15, 16): they read only what
plates_draw.py left for one tree and palette (<drawn>/<brand>/<run>/: carousel/, carousel_fb/ and stories/ with their
still_layout.json, the Reel cover's still_layout.json, stories/layout.json, draw.json), so each runs in seconds over
the whole dev corpus; the drawing (the pipeline's own calls, once per tree) is plates_draw.py's.

    python plates_<class>.py <drawn>/<brand> [--json out.json]

Exit 1 on a violation that is neither fixed nor recorded.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DECKS = ("carousel", "carousel_fb")


def setup(doc: str, extra=None):
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drawn", type=Path, help="plates_draw.py's <out>/<brand> folder")
    ap.add_argument("--json", type=Path)
    if extra:
        extra(ap)
    return ap.parse_args()


def runs(root: Path) -> list[Path]:
    return sorted(p for p in Path(root).iterdir() if (p / "draw.json").is_file())


def draw_record(run: Path) -> dict:
    return json.loads((run / "draw.json").read_text(encoding="utf-8"))


def frames(run: Path, sub: str) -> list[tuple[str, dict]]:
    """(still name, its still_layout entry) for one folder of a drawn run ("" is the run's own folder: the Reel
    cover and grid tile), in name order; [] when the folder was not drawn (a refused deck)."""
    f = (run / sub if sub else run) / "still_layout.json"
    if not f.is_file():
        return []
    return sorted(json.loads(f.read_text(encoding="utf-8")).items())


def story_layout(run: Path) -> dict[str, dict]:
    """stories/layout.json by frame name (the frame's ground and credit records)."""
    f = run / "stories" / "layout.json"
    if not f.is_file():
        return {}
    return {e.get("frame"): e for e in json.loads(f.read_text(encoding="utf-8")) if isinstance(e, dict)}


def archetype(entry: dict) -> str:
    return str((entry.get("recipe") or {}).get("archetype") or entry.get("kind") or "")


def finish(a, name: str, rows: list[dict], bad: list[dict], summary: dict) -> None:
    print(f"\n{name}: {len(rows)} rows, {len(bad)} violations")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if a.json:
        a.json.write_text(json.dumps({"class": name, "summary": summary, "violations": len(bad), "rows": rows},
                                     indent=1, default=str), encoding="utf-8", newline="\n")
    sys.exit(1 if bad else 0)
