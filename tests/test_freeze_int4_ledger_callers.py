"""Freeze integration 4, SYS-A-c4-4: a lane A surface drawn outside graphics.make_all runs with the omission ledger open.

multi_format.current_poster redrew a carousel diagram slide's drawing (the scale drawn for the slide's room, a map, a
route, a cross-section) with no ledger open, so a part the renderer leaves out (refusal.omit: a refused referent note,
a map's scale bar or name) refused the surface (Declined) and the slide fell back to its photo, the drawing lost and
the omission on no card. Invariants:

- a scale whose referent note is refused still yields the slide's drawing, with the ledger row in
  payload["layout_holds"] (named as the carousel diagram slide's drawing), and the row stored beside the drawing;
- a cache hit records the rows again (never dropped), and a cached drawing with no stored record is drawn again;
- a drawing the deck then withholds takes its rows back;
- every caller outside graphics.make_all (src/, scripts/, the board tooling) renders those surfaces inside an open
  ledger (static scan).

Synthetic inputs only (CI has no output/). This module deliberately does NOT import the autouse omission_ledger
fixture: current_poster must open its own ledger, as make_all does.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src import graphics, multi_format as mf, refusal

ROOT = Path(__file__).resolve().parents[1]

# a real run's scale fact (output/run_kailasa_temple_monolith_*/script_metadata.json, as test_freeze_lane_a_c1)
KAILASA = {"value": 32.0, "unit": "m", "dimension": "height", "subject": "Kailasa temple monolith", "approximate": True}
REFUSED_NOTE = "the referent note "


@pytest.fixture
def quick(monkeypatch):
    """Renders composed at a tenth of a second (the poster is the resting frame), never encoded or graded."""
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.1)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)


@pytest.fixture
def refused_note(monkeypatch):
    """The landmark's referent note made longer than any form can hold at the slide's floor, so render_scale refuses
    it and records the refusal on the ledger (graphics.record_drop), drawing the rest of the scale."""
    long = ("measured from the lowest course of the ground level plinth stones up to the very highest point of the "
            "uppermost crowning ornament as surveyed")
    monkeypatch.setattr(graphics, "reference_note", lambda label: long)
    return long


def _slide(tmp_path: Path, payload: dict) -> tuple[Path | None, str]:
    gen = mf.MultiFormatGenerator.__new__(mf.MultiFormatGenerator)
    return mf.current_poster(tmp_path, tmp_path / "graphics" / "graphic_scale.jpg", payload, room=gen.diagram_room(),
                             slide_width=gen.CAROUSEL_SIZE[0])


def _note_rows(holds) -> list[dict]:
    return [h for h in holds or [] if isinstance(h, dict) and str(h.get("omitted", "")).startswith(REFUSED_NOTE)]


def test_a_refused_referent_note_keeps_the_slide_drawing_and_goes_on_the_card(tmp_path, quick, refused_note):
    assert refusal.omissions() == [] and refusal._LEDGER.get() is None, "no ledger open round this test"
    payload = {"scale_fact": dict(KAILASA)}
    got, why = _slide(tmp_path, payload)
    assert got is not None and got.is_file(), why
    assert mf.slide_drawing(got), "the drawing for the slide's room, with its record"
    rows = _note_rows(payload.get("layout_holds"))
    assert rows, payload.get("layout_holds")
    row = rows[0]
    assert row["drawn"] is True and row["rule"] and row["reason"].startswith("drawn without")
    assert row["surface"].startswith(mf.DIAGRAM_SLIDE_SURFACE), row["surface"]
    assert row["drawing"] == str(got)
    # the review card and the release gate read it
    from src.figure_gate import held_note
    from src.telegram_bot import layout_hold_lines
    assert any(line.startswith(mf.DIAGRAM_SLIDE_SURFACE) for line in layout_hold_lines(payload["layout_holds"]))
    assert "Drawn without a part" in held_note(payload)
    # stored beside the drawing
    stored = mf.drawing_omissions(got)
    assert stored and any(str(r.get("omitted", "")).startswith(REFUSED_NOTE) for r in stored)


def test_a_cache_hit_records_the_rows_again(tmp_path, quick, refused_note, monkeypatch):
    first: dict = {"scale_fact": dict(KAILASA)}
    got, _ = _slide(tmp_path, first)
    assert got is not None and _note_rows(first.get("layout_holds"))

    def no_redraw(*a, **k):
        raise AssertionError("a cache hit draws nothing")
    monkeypatch.setattr(graphics, "render_scale", no_redraw)
    second: dict = {"scale_fact": dict(KAILASA)}
    again, why = _slide(tmp_path, second)
    assert again == got and "cached" in why
    assert _note_rows(second.get("layout_holds")) == _note_rows(first.get("layout_holds"))
    # the deck's second copy (same payload) records each row once
    _slide(tmp_path, second)
    assert len(_note_rows(second["layout_holds"])) == len(_note_rows(first["layout_holds"]))


def test_a_cached_drawing_with_no_stored_record_is_drawn_again(tmp_path, quick, refused_note, monkeypatch):
    got, _ = _slide(tmp_path, {"scale_fact": dict(KAILASA)})
    Path(str(got) + mf.OMISSIONS_SIDECAR).unlink()
    calls = []
    real = graphics.render_scale

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(graphics, "render_scale", counting)
    payload: dict = {"scale_fact": dict(KAILASA)}
    again, why = _slide(tmp_path, payload)
    assert calls and again is not None and "cached" not in why
    assert _note_rows(payload.get("layout_holds")) and mf.drawing_omissions(again) is not None


def test_the_rows_reach_an_open_outer_ledger_once(tmp_path, quick, refused_note):
    payload: dict = {"scale_fact": dict(KAILASA)}
    with refusal.collecting() as outer:
        got, _ = _slide(tmp_path, payload)
        _slide(tmp_path, payload)
    assert got is not None
    assert len(_note_rows(outer)) == len(_note_rows(payload["layout_holds"])) >= 1


def test_a_withheld_drawing_takes_its_rows_back():
    poster = Path("x") / "graphic_scale.jpg"
    keep = {"surface": "carousel", "omitted": "slide 3 (A)", "rule": "r", "reason": "drawn without", "drawn": True}
    payload = {"layout_holds": [dict(keep)]}
    mf.record_drawing_omissions(payload, [{"surface": "scale graphic_scale.mp4", "omitted": "the referent note X",
                                           "rule": "r", "reason": "drawn without 'X'", "drawn": True}], None, poster)
    assert len(payload["layout_holds"]) == 2
    assert mf.retract_drawing_omissions(payload, poster) == 1
    assert payload["layout_holds"] == [keep]


# ------------------------------------------------------------------------------------------------ the static guard
SURFACES = ("render_map", "render_route", "render_scale", "render_timeline", "render_cross_section",
            "render_stat_cards")
SCOPES = ("src", "scripts", "docs/reviews/freeze-system-2026-11-15")


def _called(node: ast.Call) -> str | None:
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
    if name in SURFACES:
        return name
    # getattr(graphics, renderer)(...): a renderer named at run time
    if (isinstance(f, ast.Call) and isinstance(f.func, ast.Name) and f.func.id == "getattr" and f.args
            and isinstance(f.args[0], ast.Name) and f.args[0].id == "graphics"):
        return "getattr(graphics, ...)"
    return None


def _opens_ledger(node: ast.With) -> bool:
    for item in node.items:
        c = item.context_expr
        if isinstance(c, ast.Call):
            f = c.func
            if (isinstance(f, ast.Attribute) and f.attr == "collecting") or (
                    isinstance(f, ast.Name) and f.id == "collecting"):
                return True
    return False


def unledgered_calls() -> list[str]:
    out = []
    for scope in SCOPES:
        for path in sorted((ROOT / scope).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))

            def walk(node, chain, ledger):
                for child in ast.iter_child_nodes(node):
                    c_chain = chain + [child.name] if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        else chain
                    c_ledger = ledger or (isinstance(child, ast.With) and _opens_ledger(child))
                    if isinstance(child, ast.Call):
                        name = _called(child)
                        # graphics.make_all opens the ledger round its job pool (its lambdas are built before it)
                        if name and not c_ledger and not (rel == "src/graphics.py" and "make_all" in chain):
                            out.append(f"{rel}:{child.lineno} {'.'.join(chain) or '<module>'} {name}")
                    walk(child, c_chain, c_ledger)
            walk(tree, [], False)
    return out


def test_every_caller_outside_make_all_renders_a_lane_a_surface_inside_an_open_ledger():
    """A new caller that renders a map, route, scale, timeline, cross-section or number card outside make_all with no
    ledger open would refuse the surface on its first omission (SYS-S1-3) and lose it silently: it opens one
    (refusal.collecting) round the render, lexically, and hands the rows on where it records things."""
    found = unledgered_calls()
    assert not found, found


def test_the_guard_sees_the_callers_it_guards():
    """The scan is not vacuous: it finds make_all's jobs and the closed callers (current_poster, the critic, the board)."""
    calls = []
    for scope in SCOPES:
        for path in sorted((ROOT / scope).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and _called(node):
                    calls.append(path.relative_to(ROOT).as_posix())
    for rel in ("src/graphics.py", "src/multi_format.py", "scripts/visual_critic.py",
                "docs/reviews/freeze-system-2026-11-15/board_evidence.py"):
        assert rel in calls, rel
    probe = ast.parse("def f():\n    graphics." + SURFACES[0] + "(p, o)\n")
    assert _called(probe.body[0].body[0].value) == "render_map"
    assert json.dumps(unledgered_calls())   # runs
