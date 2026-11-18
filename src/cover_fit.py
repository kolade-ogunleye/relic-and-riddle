"""
src/cover_fit.py
================
The cover copy's fit, checked before render with the renderers' own rule (design-freeze cycle 0 round 8, lander
ruling on the floors): a YouTube or Reel headline that cannot be set at its feed or grid floor is found at the copy
stage, where the script engine's copy edit rewrites the thumbnail lines (script_engine.cover_fit_problems), never
refused for the first time at render after the documentary. One rule, no second threshold: the check calls the very
functions the renderers call.

- YouTube: youtube_thumbnail.render itself, once, on a flat mid-grey ground at the full type column (no reviewed
  feature narrows it), the figure and title fit rules included (_lockup / _title_form / _figure_fonts, held at
  HERO_FLOOR / LINE_FLOOR): a LayoutError is the problem.
- Reel cover: cover_design._lockup, the lockup every cover style sets, at the badge style's measure and size; a
  lockup that only its off-ladder last resort sets is under the profile grid's floor, which the cover's grid proof
  refuses in every style (visual_engine._grid_enforced).

What render can still refuse that this does not see: a reviewed feature that narrows the YouTube column (the type
stops 48 px short of it) and photo-dependent contrast; both are recorded on the review card when they happen.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

COVER_FIT_PROBLEM = "Cover fit: "


def _copy_key(copy: dict[str, Any]) -> tuple:
    return tuple(str(copy.get(k) or "") for k in ("eyebrow", "line1", "line2", "tagline", "badge"))


@lru_cache(maxsize=64)
def _youtube(key: tuple, youtube_title: str) -> str:
    from PIL import Image

    from . import youtube_thumbnail
    from .refusal import LayoutError
    copy = dict(zip(("eyebrow", "line1", "line2", "tagline", "badge"), key))
    try:
        youtube_thumbnail.render(Image.new("RGB", (1920, 1080), (96, 92, 88)), copy, meta={},
                                 youtube_title=youtube_title or None)
    except LayoutError as e:
        return getattr(e, "reason", None) or str(e)
    return ""


@lru_cache(maxsize=64)
def _reel(key: tuple) -> str:
    from PIL import Image, ImageDraw

    from . import cover_design
    from .refusal import LayoutError
    copy = dict(zip(("eyebrow", "line1", "line2", "tagline", "badge"), key))
    try:
        _, rec = cover_design._lockup(ImageDraw.Draw(Image.new("RGB", (8, 8))), copy, 1080 - 2 * 84, 108)
    except LayoutError as e:
        return getattr(e, "reason", None) or str(e)
    if rec.get("off_ladder"):
        return (f"the Reel cover headline {' / '.join(t for t in (rec.get('line1'), rec.get('line2')) if t)!r} sets "
                f"only under the profile grid's 9 px floor")
    return ""


def fit_problems(copy: dict[str, Any], youtube_title: str = "", youtube: bool = True, reel: bool = True) -> list[str]:
    """The cover copy's fit problems ([]: every checked surface sets it at its floors), each prefixed
    COVER_FIT_PROBLEM and saying what to change (fewer or shorter words in line1 / line2)."""
    out = []
    key = _copy_key(copy)
    for name, why in (("YouTube thumbnail", _youtube(key, youtube_title or "") if youtube else ""),
                      ("Reel cover", _reel(key) if reel else "")):
        if why:
            out.append(f"{COVER_FIT_PROBLEM}{name}: {why}. Shorten thumbnail line1 / line2 (fewer or shorter words, "
                       "each line a whole phrase) so it sets at its size floor.")
    return out


def clear() -> None:
    _youtube.cache_clear()
    _reel.cache_clear()


def _register() -> None:
    from . import brand
    brand.on_change(clear)


_register()
