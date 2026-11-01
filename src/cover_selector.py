"""Which cover style a Reel goes out in (2026-11-01): fit rules, then what the audience has shown.

Until 2026-11-01 the cover rotated through four styles by run number, so a quarter of the Reels always went out
in whichever style did worst, and one of the four (lower_third) differed from Discovery only in its headline
colour. Now:

1. Fit rules: only the styles the run's copy can carry. "monument" needs a figure for its hero (a measured
   figure, or a year); the others always fit.
2. Neighbours differ: the style the previous run went out in is skipped while another fits (the profile grid
   reads as a set, which is why the rotation existed).
3. Every fitting style is tried MIN_TRIES times (measured Reels, on any platform) before the numbers are used,
   least-tried first.
4. Then Thompson sampling: each style's reward is a Beta posterior over its Reels' rewards, and the style with
   the highest draw wins, so better styles go out more often while the others keep being tried now and then.

Measured on every platform the Reel went to (Instagram, Facebook, YouTube Shorts), from the topic-independent
rates audience.table derives: 1 - the 3-second skip rate where Instagram reports it, the watch ratio, and shares
plus saves per account reached (else likes per view). Each platform's level is taken out and each run counts
once (evidence). Reach itself is left out: it follows the topic, not the cover. The draw is seeded by the run,
so a resumed run picks the same style from the same numbers.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from .visual_engine import COVER_STYLES

log = logging.getLogger(__name__)

MIN_TRIES = 3
ENGAGED = 0.03   # shares + saves per account reached that count as a full engagement reward


def fitting(copy: dict[str, Any]) -> list[str]:
    """The styles in COVER_STYLES this cover copy can carry, in COVER_STYLES order."""
    from .cover_design import monument_figure
    return [s for s in COVER_STYLES if s != "monument" or monument_figure(copy) is not None]


VIDEO_PLATFORMS = ("instagram", "facebook", "youtube")
LIKED = 0.05     # likes per view counted as a full engagement reward where a platform reports nothing richer


def reward(row: dict[str, Any]) -> float | None:
    """One measured video's reward in [0, 1] from the rates its platform reports, or None when it has none:
    1 - the 3-second skip rate (Instagram), the watch ratio (capped at 1: loops count once), and shares plus
    saves per account reached against ENGAGED (else likes per view against LIKED, YouTube's usual answer)."""
    def num(key):
        v = row.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    parts = []
    if num("skip_rate") is not None:
        parts.append(min(1.0, max(0.0, 1 - num("skip_rate") / 100)))
    if num("watch_ratio") is not None:
        parts.append(min(1.0, max(0.0, num("watch_ratio"))))
    if num("share_rate") is not None or num("save_rate") is not None:
        parts.append(min(1.0, ((num("share_rate") or 0.0) + (num("save_rate") or 0.0)) / ENGAGED))
    elif num("likes") is not None and num("views"):
        parts.append(min(1.0, num("likes") / num("views") / LIKED))
    return sum(parts) / len(parts) if parts else None


def evidence(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Each style's rewards, one per run, from audience.table rows on every video platform.

    A platform's own level is taken out first (each reward against that platform's median, around 0.5), since
    YouTube, Facebook and Instagram report different rates on different scales; then each run counts once, the
    mean of its platforms, so a Reel measured on three platforms is not three votes."""
    from statistics import median
    usable = [(r, reward(r)) for r in rows
              if r.get("platform") in VIDEO_PLATFORMS and not r.get("unavailable") and r.get("cover_style")]
    usable = [(r, v) for r, v in usable if v is not None]
    level = {p: median([v for r, v in usable if r["platform"] == p])
             for p in {r["platform"] for r, _ in usable}}
    per_run: dict[str, tuple[str, list[float]]] = {}
    for r, v in usable:
        adjusted = min(1.0, max(0.0, 0.5 + v - level[r["platform"]]))
        per_run.setdefault(r.get("run_id") or id(r), (r["cover_style"], []))[1].append(adjusted)
    out: dict[str, list[float]] = {}
    for style, values in per_run.values():
        out.setdefault(style, []).append(sum(values) / len(values))
    return out


def choose(copy: dict[str, Any], rows: list[dict[str, Any]], previous: str | None = None,
           seed: str = "") -> tuple[str, str]:
    """(style, why) for a cover with `copy`, given audience.table `rows` and the previous run's style."""
    options = fitting(copy)
    if previous in options and len(options) > 1:
        options = [s for s in options if s != previous]
    seen = evidence(rows)
    rng = random.Random(f"cover:{seed}")
    tries = {s: len(seen.get(s, [])) for s in options}
    fewest = min(tries.values())
    if fewest < MIN_TRIES:
        pool = [s for s in options if tries[s] == fewest]
        style = rng.choice(pool)
        return style, f"exploring: {style} has {fewest} measured Reel(s) of {MIN_TRIES} before the numbers count"
    draws = {}
    for s in options:
        gains = sum(seen[s])
        draws[s] = rng.betavariate(1 + gains, 1 + len(seen[s]) - gains)
    style = max(options, key=lambda s: draws[s])
    means = ", ".join(f"{s} {sum(seen[s]) / len(seen[s]):.2f} ({len(seen[s])})" for s in options)
    return style, f"learned: {style} drew highest; mean reward {means}"


def cover_copy(payload: dict[str, Any]) -> dict[str, Any]:
    """The cover copy as VisualEngine.generate_thumbnails draws it (the curiosity badge filled in)."""
    from .visual_engine import extract_curiosity_badge
    copy = dict(payload.get("thumbnail") or {})
    if "badge" not in copy:
        copy["badge"] = extract_curiosity_badge(payload)
    return copy


def for_run(db, run_id: str, payload: dict[str, Any]) -> tuple[str, str]:
    """choose() for a pipeline run's script payload: its audience evidence, the style of the run before it,
    seeded by the run."""
    from . import audience
    copy = cover_copy(payload)
    try:
        rows = audience.table(db)
    except Exception as e:  # noqa: BLE001 - a broken metrics table must never stop a render
        log.warning("Cover selection without audience numbers (%s).", e)
        rows = []
    previous = None
    try:
        for run in db.list_recent_runs(limit=6):
            if run["run_id"] != run_id:
                previous = audience.cover_style(db, run["run_id"], run)
                break
    except Exception as e:  # noqa: BLE001 - selection still works without the neighbour rule
        log.warning("Previous cover style unknown (%s).", e)
    style, why = choose(copy, rows, previous, seed=run_id)
    log.info("Cover style %s (%s; previous run: %s).", style, why, previous or "none")
    return style, why
