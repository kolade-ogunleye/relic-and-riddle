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
  column (a prepared copy of the hero, colour-balanced or de-identified, carries no reviewed focus). Without it: at
  every column a hero can leave (_probe_flat, youtube_thumbnail.type_column: COLUMN_MIN to the full column).
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


def _signature(value) -> Any:
    """A width gate's result reduced to what decides the layout (fonts by face and size)."""
    if isinstance(value, (list, tuple)):
        return tuple(_signature(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _signature(v)) for k, v in value.items()))
    if hasattr(value, "getname") and hasattr(value, "size"):
        return (getattr(value, "path", None) or value.getname(), value.size)
    return value


def _changes(fn, bound, widths) -> set:
    """The widths (ascending range `widths`) at which the gate's result differs from the next narrower width: the
    left end of every interval where the render's choice is constant. A refusal is a result too."""
    from .refusal import LayoutError
    out, prev = set(), object()
    for w in widths:
        args = bound.arguments | {"width": float(w)}
        try:
            sig = _signature(fn(**args))
        except LayoutError:
            sig = "refused"
        if sig != prev:
            out.add(w)
        prev = sig
    return out


def _probe_flat(render, inputs) -> str:
    """The copy set by the render on a flat ground at every width a hero can leave its type column (COLUMN_MIN up to
    the full column, youtube_thumbnail.type_column): the reason of the first refusal, else "". Within an interval where
    no width gate (youtube_thumbnail._WIDTH_GATES: the qualifier, kicker and figure forms) changes its choice, every
    remaining fit is monotonic in the width, so the copy is set at each interval's narrowest width (exact, not a
    sample); the gates are found from the render's own calls and re-scanned until no new one appears."""
    from . import youtube_thumbnail as yt
    from .refusal import LayoutError
    full = yt.TEXT_RIGHT - yt.LEFT
    widths = range(yt.COLUMN_MIN, full + 1)
    todo, done, seen = {yt.COLUMN_MIN, full}, set(), set()
    calls: list = []

    def hook(gated, fn, bound):
        calls.append((fn, bound))
    while todo - done:
        w = min(todo - done)
        done.add(w)
        calls.clear()
        yt._WIDTH_GATES.append(hook)
        try:
            render(meta={}, column=float(w), **inputs)
            why = ""
        except LayoutError as e:
            why = getattr(e, "reason", None) or str(e)
        finally:
            yt._WIDTH_GATES.remove(hook)
        if why:
            return why
        for fn, bound in calls:
            key = (fn.__name__, tuple((k, repr(v)) for k, v in bound.arguments.items() if k not in ("width", "measure")))
            if key not in seen:
                seen.add(key)
                todo |= _changes(fn, bound, widths)
    return ""


def _youtube(copy: dict[str, Any], youtube_title: str, payload: dict[str, Any] | None, hero) -> str:
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
                      hero_key], sort_keys=True, default=str)
    if key in _YT_CACHE:
        return _YT_CACHE[key]
    flat = dict(inputs, source=Image.new("RGB", (1920, 1080), (96, 92, 88)))
    why = ""
    if hero is not None:
        try:   # the hero itself: the column its reviewed feature leaves, the render's very call
            youtube_thumbnail.render(meta={}, **youtube_render_inputs(ctx, Path(hero), copy=copy))
        except LayoutError as e:
            why = getattr(e, "reason", None) or str(e)
        if not why:   # the hero's prepared copy (balanced, de-identified) carries no reviewed focus: the full column
            try:
                youtube_thumbnail.render(meta={}, **flat)
            except LayoutError as e:
                why = getattr(e, "reason", None) or str(e)
    else:
        why = _probe_flat(youtube_thumbnail.render, flat)
    _YT_CACHE[key] = why
    return why


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
