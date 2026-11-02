"""Which cover style a Reel goes out in (2026-11-01): fit rules, then what the audience has shown.

Until 2026-11-01 the cover rotated through four styles by run number, so a quarter of the Reels always went out
in whichever style did worst, and one of the four (lower_third) differed from Discovery only in its headline
colour. Now:

1. Fit rules: only the styles the run's copy can carry. "monument" needs a measured figure (a number with its
   unit) for its hero; the others always fit.
2. Neighbours differ: the style the previous run went out in is skipped while another fits (the profile grid
   reads as a set, which is why the rotation existed). The paper card (depth_sandwich) is the one light tile in
   an ink grid, so it is also skipped while either of the two previous covers used it and another style fits
   (grid rhythm, 2026-11-02 panel round 2).
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
from typing import Any

from .visual_engine import COVER_STYLES

log = logging.getLogger(__name__)

MIN_TRIES = 3
# Retired for new selections (operator, 2026-11-02): minimal stays renderable for old runs and redraws.
RETIRED = ("minimal",)
PAPER = "depth_sandwich"   # the light paper card: never two in any three neighbouring grid tiles while avoidable
RHYTHM = 2                 # how many previous covers the paper rule looks back over
ENGAGED = 0.03   # shares + saves per account reached that count as a full engagement reward


def fitting(copy: dict[str, Any]) -> list[str]:
    """The styles in COVER_STYLES this cover copy can carry, in COVER_STYLES order. "monument" needs a measured
    figure (a number with its unit): a bare year draws, but it is a date, not a scale, and made the weakest hero
    (Derinkuyu's "1963", review 2026-11-01)."""
    from .cover_design import monument_figure

    def measured(c):
        found = monument_figure(c)
        return found is not None and bool(found[1])
    return [s for s in COVER_STYLES if s not in RETIRED and (s != "monument" or measured(copy))]


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
    """Each style's rewards, one per run, from audience.table rows on every video platform (learning.per_run_evidence:
    each platform's own level taken out, a Reel measured on three platforms counted once)."""
    from .learning import per_run_evidence
    scored = [(r, v) for r in rows
              if r.get("platform") in VIDEO_PLATFORMS and not r.get("unavailable") and r.get("cover_style")
              for v in [reward(r)] if v is not None]
    return per_run_evidence(scored, "cover_style")


def choose(copy: dict[str, Any], rows: list[dict[str, Any]], previous: str | None = None,
           seed: str = "", recent: list[str | None] | None = None) -> tuple[str, str]:
    """(style, why) for a cover with `copy`, given audience.table `rows`, the previous run's style and, when known,
    the styles of the runs before this one, newest first (`recent`; `previous` is taken as its first when absent)."""
    from . import learning
    options = fitting(copy)
    if previous in options and len(options) > 1:
        options = [s for s in options if s != previous]
    recent = list(recent) if recent is not None else ([previous] if previous else [])
    if PAPER in options and PAPER in recent[:RHYTHM] and len(options) > 1:
        options = [s for s in options if s != PAPER]
    return learning.choose(options, evidence(rows), seed, "cover", MIN_TRIES)


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
    recent: list[str | None] = []
    try:
        for run in db.list_recent_runs(limit=6):
            if run["run_id"] != run_id:
                recent.append(audience.cover_style(db, run["run_id"], run))
                if len(recent) >= RHYTHM:
                    break
    except Exception as e:  # noqa: BLE001 - selection still works without the neighbour rule
        log.warning("Previous cover style unknown (%s).", e)
    previous = recent[0] if recent else None
    style, why = choose(copy, rows, previous, seed=run_id, recent=recent)
    log.info("Cover style %s (%s; previous run: %s).", style, why, previous or "none")
    return style, why
