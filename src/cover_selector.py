"""Which cover style a Reel goes out in: the fixed style while cover selection is frozen (QF-82, 2026-11-11).

From 2026-11-01 a Thompson-sampling bandit chose among the fitting styles, rewarded by the Reel's 3-second skip
rate, watch ratio and shares/saves. Those rates measure the video, not its cover, so the bandit learned from the
wrong signal; and three variant covers were rendered on every run though the selector never chose from them.
Now:

1. choose() returns config.COVER_FIXED_STYLE (badge) whatever the audience numbers, neighbouring styles or seed.
   Learning resumes only with a cover-specific signal (YouTube long-form thumbnail CTR or a manual Instagram
   test) and a reviewed change to this module; there is deliberately no environment switch.
2. fitting() keeps the fit rules (monument needs a measured figure; the paper card needs a reviewed feature that
   fills its window). The renderer applies them to the delivered cover (VisualEngine._fitted_style and the
   profile-grid repair), and historical redraws keep every style in RENDERABLE_STYLES.
3. reward() and evidence() are kept to describe historical runs; nothing selects with them.
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
RHYTHM = 3                 # how many previous covers the paper rule looks back over (a grid column, round 3)
ENGAGED = 0.03   # shares + saves per account reached that count as a full engagement reward
PAPER_MIN_FILL = 0.35   # round 4: the reviewed feature must fill at least this share of the paper card's window


def fitting(copy: dict[str, Any], figure: dict[str, Any] | None = None, focus: dict[str, Any] | None = None,
            source_size: tuple[int, int] | None = None) -> list[str]:
    """The styles in COVER_STYLES this cover copy can carry, in COVER_STYLES order. "monument" needs a measured
    figure (a number with its unit): a bare year draws, but it is a date, not a scale, and made the weakest hero
    (Derinkuyu's "1963", review 2026-11-01). With the fact sheet's word on that figure (`figure`, from
    cover_design.figure_check; round 3) it also needs the sheet to let the number stand alone: not a range or a
    disputed value, a hedge only with the copy's own qualifier, and the claim's word for what it measures.

    Round 4: with the hero's reviewed focus (`focus`, cover_design.reviewed_focus, and the photo's `source_size`)
    the paper card fits only when that feature fills at least PAPER_MIN_FILL of its window
    (cover_design.sandwich_fill), while another style fits; without a focus record nothing changes."""
    from .cover_design import monument_figure, sandwich_fill

    def measured(c):
        found = monument_figure(c)
        return found is not None and bool(found[1]) and (figure is None or bool(figure.get("monument_ok")))
    out = [s for s in COVER_STYLES if s not in RETIRED and (s != "monument" or measured(copy))]
    if PAPER in out and len(out) > 1 and focus and source_size:
        share = sandwich_fill(copy, focus, source_size, figure)
        if share is not None and share < PAPER_MIN_FILL:
            log.info("Paper card not offered: the reviewed feature fills %.0f%% of its window.", share * 100)
            out = [s for s in out if s != PAPER]
    return out


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
           seed: str = "", recent: list[str | None] | None = None,
           figure: dict[str, Any] | None = None, focus: dict[str, Any] | None = None,
           source_size: tuple[int, int] | None = None) -> tuple[str, str]:
    """Return the fixed style without learning from content metrics or neighbor history.

    The signature remains compatible with callers and historical selection records.
    Render-time copy, focus and profile-grid checks still run on the delivered cover.
    """
    from .config import COVER_FIXED_STYLE
    return COVER_FIXED_STYLE, "fixed: cover bandit frozen pending qualified cover-specific evidence"


def cover_copy(payload: dict[str, Any]) -> dict[str, Any]:
    """The cover copy as VisualEngine.generate_thumbnails draws it (the curiosity badge filled in)."""
    from .visual_engine import extract_curiosity_badge
    copy = dict(payload.get("thumbnail") or {})
    if "badge" not in copy:
        copy["badge"] = extract_curiosity_badge(payload)
    return copy


def for_run(db, run_id: str, payload: dict[str, Any], hero: Any = None) -> tuple[str, str]:
    """choose() for a pipeline run (QF-82): the fixed style. The audience table, the neighbouring runs' styles and
    the hero's focus are no longer read for the choice, since none of them may move it while selection is frozen;
    the renderer still applies the copy, focus and profile-grid fit rules to the delivered cover. `db` and `hero`
    are kept for the caller's signature."""
    style, why = choose(cover_copy(payload), [], seed=run_id)
    log.info("Cover style %s (%s).", style, why)
    return style, why
