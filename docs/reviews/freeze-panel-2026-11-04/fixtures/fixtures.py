"""Real run inputs for the freeze-panel evidence renders (2026-11-04), from fixtures.json beside this file.

    import sys; sys.path.insert(0, "<main checkout>/docs/reviews/freeze-panel-2026-11-04/fixtures")
    import fixtures
    for case in fixtures.load("covers"):
        hero, payload = case["feed"]["hero"], fixtures.json_of(case["feed"]["payload"])

Works from any --code-root render script: it imports nothing from src, and every path is resolved against the
checkout that holds fixtures.json (override with RR_FIXTURE_REPO). In the cases load() returns, every string that
names an existing file or folder of the checkout is a pathlib.Path (absolute); everything else is as recorded.
Designs: covers, captions, social, endcard, thennow, cards (cards has no renderable case: see "verdict").
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("RR_FIXTURE_REPO") or HERE.parents[3])
DESIGNS = ("covers", "captions", "social", "endcard", "thennow", "cards")


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Any]:
    return json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))


def path(p: str | Path) -> Path:
    """A catalogue path (repo-relative) as an absolute path in REPO."""
    q = Path(str(p).replace("\\", "/"))
    return q if q.is_absolute() else REPO / q


def _resolve(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _resolve(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve(x) for x in v]
    if isinstance(v, str) and "/" in v and not v.startswith(("http:", "https:")) and len(v) < 400:
        p = path(v)
        if p.exists():
            return p
    return v


def section(design: str) -> dict[str, Any]:
    """The design's whole catalogue section (cases plus its survey / gaps), paths resolved."""
    if design not in DESIGNS:
        raise KeyError(f"{design!r}: one of {', '.join(DESIGNS)}")
    return _resolve(copy.deepcopy(catalogue()["designs"][design]))


def load(design: str) -> list[dict[str, Any]]:
    """The design's real cases, each with run, topic, why it was picked, its measurements and `feed` (what to pass
    the renderer); paths absolute. A case carrying "label" is new (its label says why) and must be shown as such."""
    return section(design).get("cases") or []


def json_of(p: str | Path) -> Any:
    return json.loads(path(p).read_text(encoding="utf-8"))


def words(case: dict[str, Any]) -> list[dict[str, Any]]:
    """A captions case's real word timings (words.json)."""
    return json_of(case["words"])


def payload(case: dict[str, Any]) -> dict[str, Any]:
    """The run's own verified script (thumbnail, carousel_slides, story_slides, share_line, emphasis_words, ...)."""
    p = (case.get("feed") or {}).get("payload") or case.get("payload")
    return json_of(p)


def frame(mp4: str | Path, t: float | None = None, dst: str | Path | None = None, last: bool = False) -> Path:
    """One frame of a run's clip or master as PNG (cached in the temp folder): at `t` seconds, or the last frame."""
    src = path(mp4)
    tag = "last" if last else f"{float(t or 0):.3f}"
    out = Path(dst) if dst else Path(tempfile.gettempdir()) / "rr_fixture_frames" / src.parent.parent.name / \
        f"{src.stem}_{tag}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-loglevel", "error", "-y", *(["-sseof", "-0.2"] if last else ["-ss", tag]),
               "-i", str(src), "-frames:v", "1", "-update", "1", str(out)]
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
    return out


def background_at(case: dict[str, Any], t: float) -> Path:
    """A caption background at narration time `t` from the run's own timeline: the frame of the caption-free scene
    clip (or the still) that covers t. Starts are estimates (clip lengths less the 0.40 s crossfade per cut); past
    the last recorded start, the remaining scenes share the rest of the narration evenly."""
    tl = case.get("timeline") or []
    if not tl:
        raise ValueError(f"{case.get('run')}: no timeline recorded")
    dur = float(case.get("duration_s") or 0)
    known = [s for s in tl if s.get("start_s_est") is not None]
    rest = tl[len(known):]
    starts = [float(s["start_s_est"]) for s in known]
    if rest:
        t0 = starts[-1] + float(known[-1].get("duration_s") or 0) if known else 0.0
        step = max(dur - t0, 0.0) / len(rest)
        starts += [t0 + i * step for i in range(len(rest))]
    i = max(k for k, s in enumerate(starts) if s <= t) if t >= starts[0] else 0
    clip = path(tl[i]["clip"])
    if clip.suffix.lower() != ".mp4" or not clip.is_file():   # a still, or a clip no longer on disk: its photo
        return clip if clip.is_file() else path(tl[i]["photo"])
    off = max(0.0, t - starts[i])
    if tl[i].get("duration_s"):
        off = min(off, float(tl[i]["duration_s"]) - 0.05)
    return frame(clip, off)


def sheet(topic: str) -> dict[str, Any]:
    """The topic's real fact sheet (data/fact_sheets/<topic>.json, else the script library's)."""
    for p in (REPO / "data" / "fact_sheets" / f"{topic}.json", REPO / "data" / "script_library" / topic / "fact_sheet.json"):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(topic)


if __name__ == "__main__":
    for d in DESIGNS:
        cs = load(d)
        print(f"{d}: {len(cs)} cases")
        for c in cs:
            print(f"  - {Path(str(c.get('run', ''))).name}: {c.get('why')}" + (f"  [{c['label']}]" if c.get("label") else ""))
