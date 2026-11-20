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


# --------------------------------------------------------------------------- SYS-S1-2 / SYS-A-4: map marks, option C
def test_every_map_mark_pair_clears_the_mark_gate_at_its_worst():
    from src import brand
    from src.brand_system import GATES
    assert brand.map_fill_problems(brand.DEFAULT_BRAND) == []
    ratios = brand.map_mark_ratios(brand.DEFAULT_BRAND)
    assert {"HOME_TINT/LAND_TINT", "HOME_TINT/SEA_TOP", "COAST_LINE/SEA_TOP", "casing INK/HOME_TINT",
            "casing GOLD_LIGHT/LAND_TINT", "globe GLOBE_LAND/INK", "globe GLOBE_HOME/GLOBE_LAND"} <= set(ratios)
    assert all(v >= GATES["contrast.mark"] for v in ratios.values()), ratios
    # home against neighbour over the WHOLE relief range, as the renderer draws it
    from src import graphics
    assert graphics.RELIEF_HOME_FLOOR == brand.MAP_RELIEF_ENVELOPE["HOME_TINT"][0]
    lo, span = graphics.RELIEF_NEIGHBOUR
    assert (lo, lo + span) == brand.MAP_RELIEF_ENVELOPE["LAND_TINT"]


def test_the_round_6_fills_and_a_thin_casing_are_refused_at_configure_time():
    from src import brand
    old = brand.DEFAULT_BRAND.derive("round 6 fills", palette={"HOME_TINT": (168, 137, 81), "LAND_TINT": (88, 92, 100)})
    assert any("HOME_TINT/LAND_TINT" in p for p in brand.map_fill_problems(old))
    gold_globe = brand.DEFAULT_BRAND.derive("gold globe", palette={"GLOBE_HOME": "GOLD", "GLOBE_LAND": (176, 170, 160)})
    assert any("GLOBE_HOME/GLOBE_LAND" in p for p in brand.map_fill_problems(gold_globe))


def test_the_neighbour_fill_is_on_the_warm_palette():
    import colorsys
    from src import brand
    r, g, b = brand.DEFAULT_BRAND.colour("LAND_TINT")
    h, s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    assert s > 0 and (h * 360 <= 60 or h * 360 >= 330)    # a warm grey, never the cool slate


# --------------------------------------------------------------------------- SYS-A-5 / SYS-S2-1: one never-end rule
def test_a_wrapped_label_never_ends_a_line_on_a_never_end_word_where_a_ruled_break_holds():
    from PIL import Image, ImageDraw
    from src import heading, typography
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = typography.get_font("Montserrat", 40, weight=800)
    for text, width in (("AMBER IN COMPLETED ROOM", 480), ("ITALY TAKES THE DODECANESE", 520),
                        ("DISASSEMBLED BY GERMAN TROOPS", 560), ("AMBER IN COMPLETED ROOM", 400)):
        lines = typography.balanced_wrap(d, text, font, width)
        assert " ".join(lines) == text                                    # every word, in order
        assert all(typography.line_width(d, ln, font) <= width for ln in lines)
        assert all(heading.end_ok(ln.split()[-1]) for ln in lines[:-1]), lines
    # a figure is never cut from its unit by the re-break
    lines = typography.balanced_wrap(d, "The tower rises 129 ft over the plain of the river", font, 500)
    assert not any(ln.endswith("129") for ln in lines)


# --------------------------------------------------------------------------- SYS-B-4: a comparator is a hedge; one accessor
def test_a_comparator_is_read_as_its_hedge():
    from src import figures
    assert figures.parse("EST. >13,000 LBS")[0].drawn() == "ESTIMATED MORE THAN 13,000 LB"
    assert figures.parse("~40 people")[0].hedge == "ABOUT"
    assert figures.parse("≥ 5 m")[0].hedge == "AT LEAST"
    signed = figures.signed_figures(["EST. >13,000 LBS"])
    assert figures.truth_faults("13,000 LB", signed)                    # the hedge dropped: a fault the gate sees
    assert figures.truth_faults("UP TO 13,000 LB", signed)
    assert figures.truth_faults("MORE THAN 13,000 LB", signed) == []


def test_the_statement_at_a_position_is_whole():
    from src import figures
    text = "Egypt's largest: roughly 137-138 ft, cracked"
    f = figures.stated_at(text, text.index("138"))
    assert f is not None and f.drawn() == "ROUGHLY 137–138 FT"
    assert figures.stated_at(text, text.index("roughly")).drawn() == "ROUGHLY 137–138 FT"
    assert figures.stated_at(text, 0) is None


# --------------------------------------------------------------------------- headline figures (lander: "THE 1,200-TON")
def _aswan(**over):
    p = {"narration_script": "Calculations of the finished obelisk's weight range from nearly 1,090 tonnes to 1,168 "
                             "tonnes, about 42 metres long.",
         "title": "Egypt's 1,200-Ton Quarry Monster", "thumbnail": {"line1": "THE 1,200-TON", "line2": "MONSTER"},
         "first_comment": "Could they have moved 1,200 tons?"}
    p.update(over)
    return p


def test_a_headline_figure_must_be_a_body_signed_statement_in_the_runs_unit():
    from src import figure_gate
    found = figure_gate.headline_faults(_aswan())
    assert found and all("rule: headline figure == signed statement" in f for f in found)
    assert any("'1,200 TONNE'" in f for f in found)              # a metric run reads an unqualified ton as a tonne
    # derived copy (a first comment repeating it) is no evidence for the headline
    assert figure_gate.headline_faults(_aswan(title="The Quarry Monster", thumbnail={"line1": "THE MONSTER"})) == []
    # the signed statement, hedge included, passes
    ok = _aswan(title="Nearly 1,090 to 1,168 Tonnes of Granite", thumbnail={"line1": "NEARLY 1,090-1,168 TONNES"})
    assert figure_gate.headline_faults(ok) == []
    # the hedge dropped is a fault
    assert figure_gate.headline_faults(_aswan(title="1,090 to 1,168 Tonnes of Granite", thumbnail={}))
    # one end of the signed span is a fault
    assert figure_gate.headline_faults(_aswan(title="Nearly 1,090 Tonnes of Granite", thumbnail={}))


def test_an_unqualified_ton_in_a_us_customary_run_is_a_short_ton():
    from src import figure_gate, figures
    assert figures.parse("nearly 1,200 short tons")[0].drawn() == "NEARLY 1,200 SHORT TONS"
    run = {"narration_script": "It weighed nearly 1,200 short tons and stood 137 feet tall.",
           "title": "Nearly 1,200-Ton Giant", "thumbnail": {"line1": "NEARLY 1,200 TONS"}}
    assert figure_gate.headline_faults(run) == []
    # in a metric run the same headline is read as tonnes, which the body does not sign
    metric = dict(run, narration_script="It weighed nearly 1,090 tonnes and stood 42 metres tall.")
    assert figure_gate.headline_faults(metric)


# --------------------------------------------------------------------------- SYS-B-6: the no-lone-row rule on the first row
def test_a_one_word_first_row_under_the_balance_minimum_is_a_lone_row():
    from src import heading
    assert not heading.valid(["CARVED", "FROM SOLID SALT"])
    assert heading.valid(["WHY ROMAN CONCRETE", "HEALS ITSELF"])
    assert heading.valid(["SALT", "MINE"])                         # two words: no stack to be lone in
    assert heading.valid(["CARVED", "FROM SOLID SALT"], first_lone_ok=True)
    assert "one-word first row" in heading.broken_rule(["CARVED", "FROM SOLID SALT"])
    assert heading.broken_rule(["WHY ROMAN CONCRETE", "HEALS ITSELF"]) == ""


def test_the_engine_never_shrinks_a_heading_only_to_move_a_word():
    from src import heading

    def measure(t, size):
        return len(t) * size * 0.5
    # a balanced re-break at the size is taken over a lone first row
    form = heading.set_heading("HOT MIXING SECRET DISCOVERY", measure, 1400, (124,))
    assert form is not None and form.size == 124 and "first_row_lone" not in form.record
    # where no ladder form avoids it, the lone first row stands at the DESIGN size, recorded (never the next tier)
    form = heading.set_heading("HOT MIXING DISCOVERY", measure, 1200, (138, 124))
    assert form is not None and form.size == 138 and form.record.get("first_row_lone") == "HOT"


def test_one_heading_sets_two_fields_as_one_lockup():
    from src import heading

    def measure(t, size):
        return len(t) * size * 0.5
    form = heading.one_heading("CARVED FROM", "SOLID SALT", measure, 2000, (92,))
    assert form is not None and form.lockup and " ".join(form.lines) == "CARVED FROM SOLID SALT"


# --------------------------------------------------------------------------- SYS-B-4 part 3: the YouTube figure row
def test_the_gate_reads_the_youtube_figure_row_as_one_statement(tmp_path):
    import json
    from src import figure_gate
    qa = {"elements": [{"name": "figure", "text": "137–138", "box": [96, 300, 600, 500]},
                       {"name": "figure_unit", "text": "FT", "box": [620, 420, 700, 500]},
                       {"name": "figure_qualifier", "text": "ROUGHLY", "box": [96, 520, 400, 560]},
                       {"name": "title_1", "text": "EGYPT'S LARGEST", "box": [96, 600, 900, 700]}]}
    (tmp_path / "thumbnail_youtube.qa.json").write_text(json.dumps(qa), encoding="utf-8")
    payload = {"narration_script": "Surveys place its length at roughly 137-138 ft."}
    rows = [(n, e["text"]) for _s, n, e in figure_gate._elements(tmp_path)]
    assert ("figure", "ROUGHLY 137–138 FT") in rows
    assert not [f for f in figure_gate.check_run(tmp_path, payload)["faults"] if "thumbnail_youtube" in f]
    qa["elements"] = qa["elements"][:2]          # the hedge dropped from the drawing
    (tmp_path / "thumbnail_youtube.qa.json").write_text(json.dumps(qa), encoding="utf-8")
    assert [f for f in figure_gate.check_run(tmp_path, payload)["faults"] if "thumbnail_youtube" in f]


def test_a_lone_first_row_is_never_traded_for_a_form_under_the_balance_minimum():
    from src import heading

    def measure(t, size):
        return len(t) * size * 0.5
    # NO / METEORITE IMPACT: the only other form, NO METEORITE / IMPACT, is under the minimum and splits a compound
    form = heading.set_heading("NO METEORITE IMPACT", measure, 1100, (124,))
    assert form is not None and form.lines == ("NO", "METEORITE IMPACT") and form.record.get("first_row_lone") == "NO"


def test_headline_evidence_is_any_signed_field_never_derived_copy():
    from src import figure_gate
    run = {"narration_script": "Orbital surveys describe the dome as almost 30 miles across.",
           "title": "The 30-Mile Dome", "caption": "A 30-mile dome seen from space.",
           "fact_audits": [{"claim": "A 30-mile dome.", "where": "[title]", "note": "titles exempt"}]}
    # the caption and an audit OF the title are derived: the signed body says "almost"
    assert any("ALMOST" in f for f in figure_gate.headline_faults(run))
    # a fact audit of the body (or the second check) is signed evidence
    run["fact_audits"].append({"claim": "The dome is 30 miles across.", "where": "[narration]"})
    assert figure_gate.headline_faults(run) == []
    run["fact_audits"].pop()
    run["second_check"] = {"audits": [{"claim": "Surveys give 30 miles across.", "note": ""}]}
    assert figure_gate.headline_faults(run) == []


def test_a_headline_floor_only_for_a_signed_floor_drawn_as_the_same_hedge():
    from src import figure_gate
    body = "It weighed over 1,000 tonnes when complete."
    assert figure_gate.headline_faults({"narration_script": body, "title": "Over 1,000 Tonnes"}) == []
    assert figure_gate.headline_faults({"narration_script": body, "title": "1,000+ Tonnes"}) == []
    assert figure_gate.headline_faults({"narration_script": body, "title": "About 1,000 Tonnes"})
    exact = "It weighed an estimated 1,000 tonnes."
    assert figure_gate.headline_faults({"narration_script": exact, "title": "1,000+ Tonnes"})
    assert figure_gate.headline_faults({"narration_script": exact, "title": "Over 1,000 Tonnes"})


# --------------------------------------------------------------------------- one headline rule: writer, edit, checker
def test_the_writer_the_copy_edit_and_the_checker_are_told_the_one_headline_rule():
    """Fixture test, no API call: the prompts carry the release gate's headline rule verbatim, and no prompt exempts
    titles or covers from hedges any more."""
    import string

    from src import script_engine, trace_check
    from src.figure_gate import HEADLINE_FIGURE_RULE
    prompts = {"writer": script_engine.USER_PROMPT_TEMPLATE, "writer (verified)": script_engine.VERIFIED_USER_PROMPT_TEMPLATE,
               "copy edit": script_engine.COPY_EDIT_SYSTEM, "fact check": trace_check.SYSTEM}
    for name, text in prompts.items():
        assert HEADLINE_FIGURE_RULE in text, name
        assert "EXEMPT from mandatory statistical hedging" not in text and "covers are exempt" not in text, name
    assert "{" not in HEADLINE_FIGURE_RULE and "}" not in HEADLINE_FIGURE_RULE      # the templates are .format()-ed
    fields = {f for _t, f, _s, _c in string.Formatter().parse(script_engine.USER_PROMPT_TEMPLATE) if f}
    script_engine.USER_PROMPT_TEMPLATE.format(**{f: "x" for f in fields})
    note = script_engine.mandatory_hedges_note({"facts": [{"statement": "It weighs 1,090 tonnes.", "disputed": True,
                                                           "hedge": "an estimated 1,090 tonnes"}]})
    assert "covers are exempt" not in note and "headline rule" in note


def test_the_writer_check_and_the_release_gate_agree_on_a_headline(curated_payload=None):
    from types import SimpleNamespace

    from src import figure_gate, script_engine
    sheet = {"facts": [{"statement": "Calculations give its weight as 1,090 to 1,168 tonnes.", "disputed": True,
                        "hedge": "nearly 1,090 tonnes to 1,168 tonnes"}]}
    for title, ok in (("The 1,200-Ton Monster", False), ("Nearly 1,090-1,168 Tonnes", True),
                      ("1,090-1,168 Tonnes", False), ("1,000+ Tonnes", False)):
        pkg = SimpleNamespace(title=title, caption="", narration_script="It weighs nearly 1,090 to 1,168 tonnes.",
                              thumbnail=SimpleNamespace(eyebrow="", line1="", line2="", tagline=""))
        writer = script_engine.headline_problems(pkg, sheet)
        gate = figure_gate.headline_faults({"title": title, "fact_sheet": sheet,
                                            "narration_script": "It weighs nearly 1,090 to 1,168 tonnes."})
        assert (not writer) == ok and (not gate) == ok, (title, writer, gate)


# --------------------------------------------------------------------------- SYS-E-3: the hand-off points at no place
def test_no_brands_hand_off_line_points_at_a_place():
    from src import brand
    assert brand.DEFAULT_BRAND.text["handoff"] == "The next riddle is waiting."
    assert brand.handoff_problems(brand.DEFAULT_BRAND) == []
    for line in ("The next riddle is right here.", "Watch the next one below.", "Tap here for more."):
        cfg = brand.DEFAULT_BRAND.derive("place", text={"handoff": line})
        assert brand.handoff_problems(cfg), line
        with pytest.raises(brand.BrandRefused):
            brand.configure(cfg)
    assert brand.handoff_problems(brand.DEFAULT_BRAND.derive("ok", text={"handoff": "There's always another riddle."})) == []
    assert brand.handoff_problems(brand.DEFAULT_BRAND.derive("ok", text={"handoff": "Another riddle is waiting."})) == []
