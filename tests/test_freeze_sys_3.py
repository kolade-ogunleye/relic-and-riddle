"""Freeze cycle 3, SYS batch 3: the system's shared rules (invariants, never pinned measurements)."""
from __future__ import annotations

import contextvars
import threading

import pytest

from src import refusal, still_layout


# --------------------------------------------------------------------------- SYS-S1-4: never delivered against its record
def _lay(elements, tight=False):
    lay = still_layout.Layout("story", (1080, 1920), (0, 0, 1080, 1920), tight=tight)
    for e in elements:
        lay.elements.append(dict(e))
    return lay


def test_a_still_element_under_its_gate_is_a_layout_problem():
    low = {"name": "title_2", "box": [80, 440, 940, 580], "text": "THE WALLS OF KRAKOW", "contrast": 1.03}
    found = _lay([low]).problems()
    assert any(p.startswith("below_gate: title_2") for p in found)
    # its own recorded gate wins over the text gate (a kicker's 5.5)
    kicker = {"name": "kicker", "box": [80, 300, 400, 340], "text": "EGYPT", "contrast": 5.0, "gate": 5.5}
    assert any("kicker" in p for p in _lay([kicker]).problems())
    ok = {"name": "body", "box": [80, 700, 940, 800], "text": "A line", "contrast": 7.2}
    assert _lay([ok]).problems() == []
    # a planned ratio under the gate fails even where the delivered one clears it (the plan accepted a sub-gate value)
    planned_low = {"name": "body", "box": [80, 700, 940, 800], "text": "A line", "contrast": 4.6, "contrast_planned": 4.1}
    assert _lay([planned_low]).problems()


def test_a_still_whose_tighter_fit_still_fails_its_record_is_refused_never_delivered():
    bad = [{"name": "title_1", "box": [88, 356, 932, 461]}, {"name": "title_2", "box": [85, 444, 938, 577]}]
    calls = []

    def render(tight):
        calls.append(tight)
        return "image", _lay(bad, tight)
    with pytest.raises(refusal.OwnRecordFailed) as e:
        still_layout.drawn(render)
    assert calls == [False, True]
    assert isinstance(e.value, refusal.LayoutError)          # every caller's hold catches it
    assert e.value.rule == "still_layout.own_record" and e.value.surface == "story still"
    assert "title_1" in e.value.reason and "rule: still_layout.own_record" in e.value.reason

    def fixed(tight):
        return "image", _lay(bad if not tight else bad[:1], tight)
    im, lay, attempts = still_layout.drawn(fixed)
    assert attempts == 2 and lay.problems() == []


def test_the_text_fit_retry_is_checked_too():
    from src.typography import TextFitError

    def render(tight):
        if not tight:
            raise TextFitError("A long line", where="the design size")
        return "image", _lay([{"name": "t", "box": [0, 0, 10, 10], "text": "x", "contrast": 2.0}], tight)
    with pytest.raises(refusal.OwnRecordFailed):
        still_layout.drawn(render)


def test_require_gate_reads_planned_drawn_and_graded():
    rows = [{"name": "gap1", "text": "ABOUT 120 YEARS LATER", "planned": 4.09, "drawn": 4.07, "graded": None},
            {"name": "year0", "text": "1652", "planned": 9.0, "drawn": 8.8, "graded": 8.7}]
    with pytest.raises(refusal.OwnRecordFailed) as e:
        refusal.require_gate("graphic timeline", rows, 4.5, "contrast.text")
    assert "gap1" in e.value.reason and "year0" not in e.value.reason
    assert e.value.surface == "graphic timeline" and e.value.rule == "contrast.text"
    refusal.require_gate("graphic timeline", rows[1:], 4.5, "contrast.text")
    # a mark row carries its own 3.0 gate
    refusal.require_gate("graphic map", [{"name": "marker", "drawn": 3.2, "gate": 3.0}], 4.5, "contrast.mark")
    with pytest.raises(refusal.OwnRecordFailed):
        refusal.require_gate("graphic map", [{"name": "marker", "drawn": 1.24, "gate": 3.0}], 4.5, "contrast.mark")


# --------------------------------------------------------------------------- SYS-S1-3: one recorded-decline class
def test_a_decline_names_its_surface_and_rule_and_is_the_one_refusal_type():
    e = refusal.decline("graphic scale", "scale.reference_range", "no reference landmark within 1/3x-3x of 10 m")
    assert isinstance(e, refusal.Declined) and isinstance(e, refusal.LayoutError)
    assert "graphic scale" in e.reason and "scale.reference_range" in e.reason
    rec = refusal.record(e, "")
    assert rec["surface"] == "graphic scale" and rec["rule"] == "scale.reference_range"
    assert rec["refused"] == "Declined"


def test_an_omission_with_no_ledger_open_is_a_refusal_never_a_log_line():
    with pytest.raises(refusal.Declined) as e:
        refusal.omit("graphic timeline", "1652", "timeline.capacity", "6 events, 5 rows")
    assert "timeline.capacity" in e.value.reason and "1652" in e.value.reason


def test_an_omission_is_recorded_on_the_open_ledger_and_reaches_the_holds():
    with refusal.collecting() as rows:
        row = refusal.omit("graphic timeline", "1652", "timeline.capacity", "6 events, 5 rows")
        assert refusal.omissions() == [row]
    assert refusal.omissions() == []                     # closed with the block
    holds = refusal.hold_rows(rows)
    assert holds[0]["surface"] == "graphic timeline" and holds[0]["drawn"] is True
    assert "1652" in holds[0]["reason"] and "timeline.capacity" in holds[0]["reason"]


def test_a_thread_pool_running_in_copied_contexts_shares_the_ledger():
    import concurrent.futures
    with refusal.collecting() as rows:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contextvars.copy_context().run, refusal.omit, f"graphic {i}", "x", "r", "why")
                       for i in range(3)]
            [f.result() for f in futures]
    assert sorted(r["surface"] for r in rows) == ["graphic 0", "graphic 1", "graphic 2"]
    # a bare thread (no copied context) has no ledger: it refuses rather than logging
    errors = []
    with refusal.collecting():
        t = threading.Thread(target=lambda: errors.append(_try_omit()))
        t.start()
        t.join()
    assert errors == ["Declined"]


def _try_omit():
    try:
        refusal.omit("graphic route", "stop", "route.stop_label", "no place")
    except refusal.Declined:
        return "Declined"
    return "recorded"


def test_the_release_gate_names_omissions_apart_from_held_surfaces():
    from src.figure_gate import held_note
    payload = {"layout_holds": [{"deck": "graphics/scale", "error": "refused", "moved": []},
                                *refusal.hold_rows([{"surface": "graphic timeline", "omitted": "1652",
                                                     "rule": "timeline.capacity", "reason": "drawn without '1652'",
                                                     "drawn": True}])]}
    note = held_note(payload)
    assert "graphics/scale" in note.split("Drawn without a part")[0]
    assert "graphic timeline" in note.split("Drawn without a part")[1]
    assert held_note({}) == ""


def test_the_review_card_lists_an_omission():
    from src.telegram_bot import layout_hold_lines
    lines = layout_hold_lines(refusal.hold_rows([{"surface": "graphic timeline", "omitted": "1652",
                                                  "rule": "timeline.capacity",
                                                  "reason": "drawn without '1652': 6 events (rule: timeline.capacity)",
                                                  "drawn": True}]))
    assert lines and "graphic timeline" in lines[0] and "1652" in lines[0]


# --------------------------------------------------------------------------- stress realism item 2 (Richat)
def test_a_glued_scale_letter_before_a_count_unit_is_its_scale_word():
    from src import figures
    (f,) = figures.parse("94-104M years")
    assert f.drawn() == "94–104 MILLION YEARS" and f.values == (94e6, 104e6)
    assert figures.parse("94M-104M years")[0].drawn() == "94–104 MILLION YEARS"
    assert figures.parse("3.5bn years")[0].drawn() == "3.5 BILLION YEARS"
    # never a metre, never a bare letter: no count unit follows
    for text in ("5M visitors", "40m tall", "a 2m wall", "5m"):
        assert all(not g.scale for g in figures.parse(text)), text
    # drawn whole from the glued sign, the gate passes it; one end or the scale dropped still fails
    signed = figures.signed_figures(["94-104M years"])
    assert figures.truth_faults("94–104 MILLION YEARS", signed) == []
    assert figures.truth_faults("94–104 YEARS", signed)
    assert figures.truth_faults("104 MILLION YEARS", signed)


class _P:
    def __init__(self, text, box):
        self.text, self.box, self.empty = text, box, False


def test_a_stacked_unit_beside_its_figure_reads_top_to_bottom():
    from src.typography import drawn_text
    plates = [_P("94–104", (40, 230, 220, 300)), _P("MILLION", (232, 240, 300, 262)),
              _P("YEARS", (232, 268, 290, 292)), _P("AGO", (292, 268, 322, 292))]
    assert drawn_text(plates) == "94–104 MILLION YEARS AGO"
    # runs on one line still read left to right; lines top to bottom
    assert drawn_text([_P("129 FT", (200, 0, 300, 40)), _P("MORE THAN", (0, 0, 190, 40)),
                       _P("DEEP", (0, 60, 80, 100))]) == "MORE THAN 129 FT DEEP"


# --------------------------------------------------------------------------- a drawn hedge that bounds the other way
def test_a_drawn_hedge_that_bounds_the_other_way_is_a_fault():
    from src import figures
    signed = figures.signed_figures(["It rises more than 129 feet.", "It holds up to 40 people."])
    assert figures.truth_faults("UP TO 129 FT", signed)
    assert figures.truth_faults("MORE THAN 40 PEOPLE", signed)
    assert figures.truth_faults("OVER 129 FT", signed) == []
    assert figures.truth_faults("NEARLY 40 PEOPLE", signed) == []


# --------------------------------------------------------------------------- SYS-F-3: a hedge carried over a break
def test_the_gate_reads_a_hedge_carried_to_the_line_before():
    from src import figure_gate, figures
    signed = figures.signed_figures(["It rises more than 129 feet over the plain."])
    f = figures.parse("THAN 129 FT")[0]
    assert figure_gate._hedge_on_line_before("MORE", "THAN 129 FT", f, signed)
    f = figures.parse("129 FT")[0]
    assert figure_gate._hedge_on_line_before("MORE THAN", "129 FT", f, signed)
    assert not figure_gate._hedge_on_line_before("THE TOWER", "129 FT", f, signed)
    # a hedge that bounds the other way is never the signed one
    assert not figure_gate._hedge_on_line_before("UP TO", "129 FT", f, signed)


# --------------------------------------------------------------------------- SYS-S1-5: drawn == signed precision
def test_a_signed_decimal_is_drawn_at_its_signed_precision():
    from src import figures
    assert figures.signed_number(171.5) == "171.5"
    assert figures.signed_number(1447.9) == "1,447.9"
    assert figures.signed_number(1073) == "1,073"
    assert figures.signed_number(171.5, "171.50") == "171.50"
    signed = figures.signed_figures(["The shaft reaches 1,447.9 ft."])
    assert figures.truth_faults(figures.signed_number(1447.9) + " FT", signed) == []
    assert figures.truth_faults("1,448 FT", signed)     # rounding stays a fault: the gate does not accept it
