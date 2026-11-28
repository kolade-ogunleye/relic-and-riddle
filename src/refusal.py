"""
src/refusal.py
==============
The one refusal type (design-freeze cycle 0 round 8, operator ruling 6): every renderer that cannot draw a surface
as designed, without cutting checked copy, dropping a letter or going under a floor or a gate, raises LayoutError
(or a subclass naming the particular refusal: typography.TextFitError, font_engine.GlyphRefused,
design_cards.CardRefused, multi_format.StoryCreditFitError), and every caller records it for that surface alone
(the stills' hold, main.hold_layout_error; the graphics' make_all; the cards' card_composition; the deep dive's cover
job, per surface), never letting one surface's refusal abort its siblings (the round-7 Tunguska job lost its Reel
cover and grid to the YouTube thumbnail's TextFitError). Its own module so the font engine, which the typography
module imports, can subclass it too.

Freeze cycle 3 (SYS-S1-3, SYS-S1-4; stress baseline 72ac97a8 items 1, 9, 11, 17, 22-25, 28): ONE recorded-decline
class. A surface is drawn whole, or refused (a LayoutError), or drawn with a part left out AND that leaving-out
recorded (`omit`, read by the review card and the release gate through payload["layout_holds"]). Never a None return,
never a part dropped with only a log line, and never a surface delivered against its own failing record:

- `Declined`: a renderer that has nothing it can draw as designed (no reference within range for a scale, fewer than
  two levels for a section). It raises this instead of returning None.
- `OwnRecordFailed`: a surface whose own record fails its own gate (a planned or drawn ratio under its gate, a
  recorded overlap or cut-off) after its one re-lay. It raises this instead of delivering (`require_gate` for rows of
  ratios; still_layout.drawn for the stills' measured layouts).
- `omit` / `collecting`: a part of a drawn surface left out (a timeline event past the column's capacity, a stop
  whose label has no place) is a row on the ledger the caller opened, with the rule that left it out; with no ledger
  open there is nowhere to record it, so it is a refusal (fail closed), never a log line.

Every reason names its rule and its surface (LANES_CYCLE3 "Truth": refusals record a reason that names the rule and
the surface that refused).
"""
from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Mapping, Optional

log = logging.getLogger(__name__)


class LayoutError(ValueError):
    """A card, slide, cover or graphic that cannot be drawn as designed without cutting checked copy (or, in the
    parked freeze work, without dropping a required credit): the surface is refused and held, never delivered
    shortened (QF-23; freeze panel round 2, F1 and F8). The one refusal type: the stills' hold
    (main.hold_layout_error), the graphics' (graphics.make_all) and the review card's "Held" list all catch this
    class, and every more particular refusal subclasses it (TextFitError; the park's BodyFitError; round 8:
    GlyphRefused and CardRefused). `reason`, `code`, `rule` and `surface` are what a caller records."""

    @property
    def reason(self) -> str:
        return getattr(self, "_reason", None) or str(self)

    @reason.setter
    def reason(self, value: str) -> None:
        self._reason = value

    @property
    def code(self) -> str:
        return getattr(self, "_code", None) or type(self).__name__

    @code.setter
    def code(self, value: str) -> None:
        self._code = value

    @property
    def rule(self) -> str:
        """The rule that refused (a gate or floor's name, "scale.reference_range"): "" when the raiser named none."""
        return getattr(self, "_rule", None) or ""

    @rule.setter
    def rule(self, value: str) -> None:
        self._rule = value

    @property
    def surface(self) -> str:
        """The surface that refused ("graphic scale", "carousel_slide_04"): "" when the raiser named none."""
        return getattr(self, "_surface", None) or ""

    @surface.setter
    def surface(self, value: str) -> None:
        self._surface = value


class Declined(LayoutError):
    """A renderer with nothing it can draw as designed for its input (cycle 3, SYS-S1-3: render_scale with no
    reference landmark within range, render_cross_section with fewer than two levels returned None with no record):
    a recorded decline, raised where the renderer used to return None."""


class OwnRecordFailed(LayoutError):
    """A surface whose own record fails its own gate after its re-lay (cycle 3, SYS-S1-4: a carousel heading recorded
    at 1.00 and delivered; a Story "title_1 overlaps title_2" recorded and delivered; timeline gap labels planned at
    4.09 under 4.5; callout labels whose worst open frame records 3.62): refused, never delivered."""


def refuse(surface: str, rule: str, detail: str, kind: type[LayoutError] = LayoutError) -> LayoutError:
    """A refusal of `kind` whose reason names the surface and the rule: "graphic scale: no reference landmark within
    1/3x-3x of 10 m (rule: scale.reference_range)". Raise what it returns."""
    error = kind(f"{surface}: {detail} (rule: {rule})")
    error.rule, error.surface = rule, surface
    return error


def decline(surface: str, rule: str, detail: str) -> Declined:
    """A `Declined` naming its surface and rule (raise it where a renderer returned None)."""
    return refuse(surface, rule, detail, Declined)


def record(error: BaseException, surface: str) -> dict:
    """A refusal as a caller records it: the surface, the refusal's class and code, its reason, and its rule where the
    raiser named one (the record's shape is unchanged for a refusal that names none)."""
    rec = {"surface": surface or getattr(error, "surface", "") or "", "refused": type(error).__name__,
           "code": getattr(error, "code", "") or "", "reason": getattr(error, "reason", None) or str(error)}
    if getattr(error, "rule", ""):
        rec["rule"] = error.rule
    return rec


# ------------------------------------------------------------------------------------------- a surface's own gate
def failing_rows(rows: Iterable[Mapping[str, Any]], gate: float) -> list[str]:
    """One line per row of a renderer's own record whose ratio is under `gate` (a row's own "gate" wins), and per row
    whose PLANNED ratio is under it even where the drawn one clears it (a plan that accepts a sub-gate value is the
    fault: stress item 12, timeline gap labels planned at 4.09): [] when every row holds."""
    out = []
    for row in rows or []:
        g = row.get("gate")
        g = float(g) if isinstance(g, (int, float)) and not isinstance(g, bool) else float(gate)
        values = {k: float(row[k]) for k in ("planned", "contrast_planned", "drawn", "graded", "contrast")
                  if isinstance(row.get(k), (int, float)) and not isinstance(row.get(k), bool)}
        low = {k: v for k, v in values.items() if v + 1e-9 < g}
        if low:
            how = ", ".join(f"{k} {v:.2f}" for k, v in low.items())
            out.append(f"{row.get('name') or '?'} {str(row.get('text') or '')[:60]!r} {how} under its {g:g}:1 gate")
    return out


def require_gate(surface: str, rows: Iterable[Mapping[str, Any]], gate: float, rule: str) -> None:
    """Raises OwnRecordFailed when any row of the surface's own record is under its gate (failing_rows): the surface
    is refused (re-lay first, then call this), never delivered against its own record (SYS-S1-4)."""
    bad = failing_rows(rows, gate)
    if bad:
        raise refuse(surface, rule, "its own record fails its gate: " + "; ".join(bad[:4])
                     + (f"; and {len(bad) - 4} more" if len(bad) > 4 else ""), OwnRecordFailed)


def require_no_problems(surface: str, problems: Iterable[str], rule: str) -> None:
    """Raises OwnRecordFailed when the surface recorded its own layout problems (an overlap, a cut-off, a ratio under
    its gate) and is about to deliver anyway (SYS-S1-4)."""
    found = [str(p) for p in problems or []]
    if found:
        raise refuse(surface, rule, "its own layout record fails: " + "; ".join(found[:4])
                     + (f"; and {len(found) - 4} more" if len(found) > 4 else ""), OwnRecordFailed)


# ------------------------------------------------------------------------------- recorded omissions (the ledger)
_LEDGER: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar("refusal_omissions", default=None)


@contextmanager
def collecting() -> Iterator[list]:
    """A ledger for the omissions recorded inside the block (`omit`); yields the list of rows. A thread pool that runs
    its jobs in contextvars.copy_context() (graphics.make_all) shares the ledger, which is one list. The caller hands
    the rows on (`hold_rows` into payload["layout_holds"]) so the review card and the release gate read them."""
    rows: list = []
    token = _LEDGER.set(rows)
    try:
        yield rows
    finally:
        _LEDGER.reset(token)


def omit(surface: str, element: str, rule: str, reason: str) -> dict:
    """Records that `surface` is drawn without `element` (the words left out) under `rule`, on the open ledger, and
    returns the row. With no ledger open the omission cannot be recorded, so the surface is refused (raises
    Declined): a part is never left out with only a log line (SYS-S1-3)."""
    row = {"surface": surface, "omitted": element, "rule": rule, "reason": f"drawn without {element!r}: {reason} "
                                                                           f"(rule: {rule})", "drawn": True}
    rows = _LEDGER.get()
    if rows is None:
        raise refuse(surface, rule, f"would be drawn without {element!r} ({reason}) and no ledger is open to record "
                                    "it", Declined)
    rows.append(row)
    log.warning("%s: %s", surface, row["reason"])
    return row


def ledger_open() -> bool:
    """Whether an omission ledger is open (collecting): an omit() here would be recorded, not refused."""
    return _LEDGER.get() is not None


def omissions() -> list:
    """The rows on the open ledger (a copy; [] when none is open)."""
    return list(_LEDGER.get() or [])


def hold_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Ledger rows as payload["layout_holds"] entries: {"surface", "reason", "omitted", "rule", "drawn": True}. The
    review card lists "surface: reason" (telegram_bot.layout_hold_lines); the release gate names them as drawn
    without a part (figure_gate.held_note), apart from the surfaces held whole."""
    return [dict(r) for r in rows or [] if isinstance(r, Mapping) and r.get("surface")]
