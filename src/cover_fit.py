"""
src/cover_fit.py
================
The cover copy's fit, checked before render with the renderers' own rule (design-freeze cycle 0 round 8, lander
ruling on the floors): a YouTube or Reel headline that cannot be set at its feed or grid floor is found at the copy
stage, where the script engine's copy edit rewrites the thumbnail lines (script_engine.cover_fit_problems), never
refused for the first time at render after the documentary. One rule, no second threshold: the check calls the very
functions the renderers call.

- YouTube: youtube_thumbnail.render itself with the render's own inputs (visual_engine.youtube_render_inputs: the copy
  with its badge, the fact sheet's figure check, the video title, the reveal line), the figure and title fit rules
  included (_lockup / _title_form / _figure_fonts, held at HERO_FLOOR / LINE_FLOOR): a LayoutError is the problem.
  Freeze lane B cycle 1 (board fix 72ac97a8 finding): the check rendered without the figure check and the badge, at the
  full column only, so it passed copy the real thumbnail refuses (production sample, default brand: 3 of 34). The
  check and the render are now one decision: the same function on the same inputs, at every column the render can
  set the type in. With the hero (`hero`): on the hero itself (its reviewed feature's column), and at the full
  column (a prepared copy of the hero, colour-balanced or de-identified, carries no reviewed focus). Without it:
  lane B cycle 3 (lander ruling): at the design's standard text column only; a hero that narrows it is met at render
  by the headline chain (youtube_chain_problems / visual_engine.youtube_headline_chain).
- Reel cover: cover_design._lockup, the lockup every cover style sets, at the badge style's measure and size; a
  lockup that only its off-ladder last resort sets is under the profile grid's floor, which the cover's grid proof
  refuses in every style (visual_engine._grid_enforced).

What render can still refuse that this does not see: nothing on YouTube's fit (photo-dependent contrast is planned,
never a refusal); recorded on the review card when it happens.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

COVER_FIT_PROBLEM = "Cover fit: "


def _copy_key(copy: dict[str, Any]) -> tuple:
    return tuple(str(copy.get(k) or "") for k in ("eyebrow", "line1", "line2", "tagline", "badge"))


_YT_CACHE: dict = {}


def _youtube(copy: dict[str, Any], youtube_title: str, payload: dict[str, Any] | None, hero,
             need_headline: bool = False) -> str:
    import json
    from pathlib import Path

    from PIL import Image

    from . import youtube_thumbnail
    from .refusal import LayoutError
    from .visual_engine import youtube_render_inputs
    ctx = dict(payload or {})
    if youtube_title:
        ctx["youtube_title"] = youtube_title
    inputs = youtube_render_inputs(ctx, None, copy=copy)
    hero_key = ""
    if hero is not None:
        try:
            hero_key = f"{Path(hero)}|{Path(hero).stat().st_mtime_ns}"
        except OSError:
            hero_key = str(hero)
    key = json.dumps([_copy_key(inputs["copy"]), inputs["youtube_title"], inputs["figure"], inputs["reveal"],
                      hero_key, need_headline], sort_keys=True, default=str)
    if key in _YT_CACHE:
        return _YT_CACHE[key]
    flat = dict(inputs, source=Image.new("RGB", (1920, 1080), (96, 92, 88)))
    why = ""
    if hero is not None:
        try:   # the hero itself: the column its reviewed feature leaves, the render's very call
            why = _headline_why(youtube_thumbnail.render(meta={}, **youtube_render_inputs(ctx, Path(hero), copy=copy)),
                                need_headline)
        except LayoutError as e:
            why = getattr(e, "reason", None) or str(e)
        if not why:   # the hero's prepared copy (balanced, de-identified) carries no reviewed focus: the full column
            try:
                why = _headline_why(youtube_thumbnail.render(meta={}, **flat), need_headline)
            except LayoutError as e:
                why = getattr(e, "reason", None) or str(e)
    else:
        # lane B cycle 3 (lander ruling, 2026-11-19): the copy stage holds the design's standard text column (the
        # thumbnail controls it; the hero is placed to leave it, youtube_thumbnail._photo), not the narrowest column a
        # hero could force; a hero that narrows it at render is met by the headline chain (youtube_headline_chain)
        try:
            why = _headline_why(youtube_thumbnail.render(
                meta={}, column=float(youtube_thumbnail.TEXT_RIGHT - youtube_thumbnail.LEFT), **flat), need_headline)
        except LayoutError as e:
            why = getattr(e, "reason", None) or str(e)
    _YT_CACHE[key] = why
    return why


def _headline_why(rendered, need: bool) -> str:
    """"" unless `need` and the render drew no headline (no title row and no figure): a chain candidate the word
    budget left out whole is not a thumbnail headline (lane B cycle 3)."""
    from . import youtube_thumbnail
    if need and not youtube_thumbnail.drew_headline(rendered[1]):
        return "no headline drawn (the line is longer than the thumbnail's word budget)"
    return ""


def youtube_chain_problems(payload: dict[str, Any], hero=None) -> list[str]:
    """Lane B cycle 3: the YouTube thumbnail's copy-stage check over its ranked headline chain
    (visual_engine.youtube_headline_chain): [] when any candidate sets (the render's own rule, at the standard column
    or on the hero); else one problem naming every source's reason, for the copy edit."""
    from .visual_engine import youtube_headline_chain
    reasons = []
    for cand in youtube_headline_chain(dict(payload or {})):
        if cand["copy"] is None:
            reasons.append(f"{cand['source']}: {cand['skipped']}")
            continue
        why = _youtube(cand["copy"], str((payload or {}).get("youtube_title") or ""), payload, hero,
                       need_headline=True)
        if not why:
            return []
        reasons.append(f"{cand['source']}: {why}")
    return [f"{COVER_FIT_PROBLEM}YouTube thumbnail: no line of the headline chain sets ({'; '.join(reasons)}). "
            "Write a youtube_headline that sets at the size floor (fewer or shorter words, each line a whole phrase)."]


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


def fit_problems(copy: dict[str, Any], youtube_title: str = "", youtube: bool = True, reel: bool = True,
                 payload: dict[str, Any] | None = None, hero=None) -> list[str]:
    """The cover copy's fit problems ([]: every checked surface sets it at its floors), each prefixed
    COVER_FIT_PROBLEM and saying what to change (fewer or shorter words in line1 / line2). `payload`: the run's script
    payload as the render will read it (the fact sheet's figure check, the badge, the reveal line); `hero`: the
    thumbnail's photograph when it is known (else every column a hero can leave is checked)."""
    out = []
    key = _copy_key(copy)
    for name, why in (("YouTube thumbnail", _youtube(copy, youtube_title or "", payload, hero) if youtube else ""),
                      ("Reel cover", _reel(key) if reel else "")):
        if why:
            out.append(f"{COVER_FIT_PROBLEM}{name}: {why}. Shorten thumbnail line1 / line2 (fewer or shorter words, "
                       "each line a whole phrase) so it sets at its size floor.")
    return out


def clear() -> None:
    _YT_CACHE.clear()
    _reel.cache_clear()


def _register() -> None:
    from . import brand
    brand.on_change(clear)


_register()
