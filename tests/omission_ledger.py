"""The omission ledger round each test, as graphics.make_all opens it round each render (freeze lane A cycle 4).

A lane A renderer (timeline, route, map, scale) records every part it leaves out on the open ledger (refusal.omit);
with none open the surface is refused (SYS-S1-3), so a test that renders one outside make_all must open the ledger
or it tests a refusal production never takes. A test module that calls such a renderer imports the autouse fixture:

    from omission_ledger import omission_ledger  # noqa: F401  (autouse: the ledger round each test)

test_freeze_lane_a_c4.test_every_module_that_renders_a_lane_a_surface_opens_the_ledger holds every module to it. A
test of the no-ledger contract itself uses `no_ledger()`. `assert_timeline_drops_recorded` checks that the events a
timeline leaves out are on the ledger, by name, whether the surface was then drawn or refused.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from src import graphics, refusal

# The renderers that record their drops on the ledger (graphics.record_drop / refusal.omit) and the drop helpers that
# record them when handed a `surface`; make_all opens the ledger round them in production.
LANE_A_RENDERERS = ("render_timeline", "render_route", "render_map", "render_scale")


@pytest.fixture(autouse=True)
def omission_ledger() -> Iterator[list]:
    """The ledger open for the whole test (graphics.make_all's `collecting()`); yields its rows."""
    with refusal.collecting() as rows:
        yield rows


@contextlib.contextmanager
def no_ledger() -> Iterator[None]:
    """No ledger open inside the block (a test of the SYS-S1-3 contract: an omission with none open refuses)."""
    token = refusal._LEDGER.set(None)
    try:
        yield
    finally:
        refusal._LEDGER.reset(token)


def assert_timeline_drops_recorded(rows: Sequence[dict[str, Any]], events: Sequence[dict[str, Any]],
                                   out: Path | str) -> None:
    """Every event timeline_events leaves out of `events` is one ledger row of the timeline drawn to `out` (its year
    and words, upper case), and no event it keeps is: a drop is recorded, never a log line alone (lane A cycle 4)."""
    surface = graphics.surface_of("timeline", out)
    kept = graphics.timeline_events(list(events))            # no surface: the planning call records nothing
    ids = {id(e) for e in kept}
    dropped = [e for e in events if id(e) not in ids]
    got = [r["omitted"] for r in rows if r.get("surface") == surface and str(r.get("omitted", "")).startswith(
        "the event ")]
    assert len(got) == len(dropped), (surface, got, [e.get("label") for e in dropped])

    def named(e: dict[str, Any]) -> str:     # the event as its row names it: the drawn year, then its words
        year = graphics.year_text(int(e["year"]), bool(e.get("approximate")))
        return f"the event {year} {' '.join(str(e.get('label') or '').split()).upper()}"
    assert sorted(got) == sorted(named(e) for e in dropped), (got, [named(e) for e in dropped])
