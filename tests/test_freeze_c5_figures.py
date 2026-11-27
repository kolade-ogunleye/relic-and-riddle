"""Design freeze cycle 5, FIGURES stream: the class measures of docs/reviews/freeze-system-2026-11-15/corpus run on
committed fixtures, so no class reopens silently (the corpus scripts run the same measures over every dev run).

Invariants, never pinned pixels: one figure rule on every stated quantity (rulings 3 and R8-8); no label on another;
the cross-section's blocks as groups with each leader on its own figure; a derived figure only with its recorded
derivation (lander ruling 2026-11-26); the 9:16 scale drawing's share of the graphics box; the then-and-now divider
drawn hard and its markers' fade over before the divider is near.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src import figure_gate, figures, graphics, then_now

CORPUS = Path(__file__).resolve().parents[1] / "docs" / "reviews" / "freeze-system-2026-11-15" / "corpus"
sys.path.insert(0, str(CORPUS))
import figures_corpus as fc  # noqa: E402
import figures_measures as fm  # noqa: E402

# the three signed scale facts of the dev corpus (a metre depth, a foot length, an exact foot height) and stress facts
FACTS = [
    {"value": 327.0, "unit": "m", "dimension": "depth", "subject": "Wieliczka Salt Mine", "approximate": True},
    {"value": 138.0, "unit": "ft", "dimension": "length", "subject": "Unfinished obelisk", "approximate": True},
    {"value": 60.0, "unit": "ft", "dimension": "height", "subject": "Colossi of Memnon", "approximate": False},
    fc.SCALE_FIXTURES[0], fc.SCALE_FIXTURES[2], fc.SCALE_FIXTURES[8],
]
SECTIONS = [fc.SECTION_FIXTURES[0], fc.SECTION_FIXTURES[2]]
TALL = (1080, 1920)


@pytest.fixture(scope="module")
def records(tmp_path_factory):
    work = tmp_path_factory.mktemp("c5_figures")
    jobs = [{"run": f"fact{k}", "kind": "scale", "input": f, "palette": None, "narration": "", "source": "FIXTURE"}
            for k, f in enumerate(FACTS)]
    jobs += [{"run": f"section{k}", "kind": "section", "input": s, "palette": None, "narration": "",
              "source": "FIXTURE"} for k, s in enumerate(SECTIONS)]
    return [fc.capture_one(job, fmt, work) for job in jobs for fmt in fc.formats(job["kind"])]


def _fails(rows):
    return [r for r in rows if r[4] == "FAIL"]


def test_every_graphic_is_drawn_or_refused_on_the_record(records):
    assert not [r["error"] for r in records if r.get("error")]
    assert all(r.get("drawn") or r.get("refused") for r in records)


def test_stated_quantities_take_the_one_figure_rule(records):
    tok = fm.tokens(graphics, None)
    rows = [row for r in records for row in fm.quantity_rows(r, tok)]
    assert not _fails(rows), _fails(rows)
    # the floor of a depth is the fact itself: drawn as a stated quantity (never on the ruler path) or recorded
    for r in records:
        if r["kind"] == "scale" and r["input"]["dimension"] == "depth" and r.get("drawn"):
            names = {e["name"] for e in r["elements"]}
            dropped = [o for o in r["omissions"] if "floor figure" in str(o.get("omitted"))]
            assert "floor_figure" in names or dropped, (r["run"], r["format"])


def test_no_label_stands_on_another(records):
    rows = [row for r in records for row in fm.collision_rows(r)]
    assert not _fails(rows), _fails(rows)


def test_section_blocks_are_groups_and_leaders_end_on_their_own_figures(records):
    secs = [r for r in records if r["kind"] == "section"]
    rows = [row for r in secs for row in fm.section_rows(r)]
    assert not _fails(rows), _fails(rows)
    for r in secs:
        assert r["layout"] and not graphics.section_layout_faults(r["layout"]), (r["run"], r["format"])
        assert len(r["layout"]["leaders"]) == len(r["input"]["levels"])


def test_derived_figures_carry_their_derivation_or_are_withheld(records):
    rows = [row for r in records for row in fm.derived_rows(r)]
    assert not _fails(rows), _fails(rows)
    for r in records:
        if r["kind"] != "scale" or not r.get("drawn"):
            continue
        lay = r["layout"]
        if lay["derived"] is not None:
            assert not figures.derivation_faults(lay["derived"], lay["labelled"])
            assert not figure_gate.derived_faults("meaning1", "", lay["derived"], {"scale_fact": r["input"]})
        else:
            assert lay["derived_withheld"] and any("derived figure" in str(o.get("omitted")) for o in r["omissions"])


def test_tall_scale_drawing_takes_its_share_of_the_graphics_box(records):
    rows = [row for r in records for row in fm.share_rows(r)]
    assert rows and not _fails(rows), _fails(rows)


def test_derivation_rule():
    a = figures.operand("subject", "Mine", "327 M", 327, "M", True, "scale_fact")
    b = figures.operand("reference", "Pyramid", "146 M", 146, "M", True, "https://example.org/pyramid")
    rec = figures.derivation("difference", [a, b], "181", "ABOUT 181 M")
    assert rec["result"] == 181 and rec["hedged"] and not figures.derivation_faults(rec, ["subject", "reference"])
    assert figures.derivation_faults(dict(rec, text="181 M"))                      # hedged unless exact
    assert figures.derivation_faults(dict(rec, shown="183"))                       # the arithmetic
    assert figures.derivation_faults(rec, ["subject"])                             # an operand not labelled
    assert figures.derivation_faults(dict(rec, rule="product"))                    # difference, ratio or sum only
    assert figures.derivation_faults(dict(rec, operands=[a, dict(b, source="")]))  # every operand sourced
    assert figures.derivation_faults(None)
    exact = figures.derivation("difference", [dict(a, approximate=False), dict(b, approximate=False)], "181", "181 M")
    assert not exact["hedged"] and not figures.derivation_faults(exact)
    with pytest.raises(ValueError):
        figures.derivation("product", [a, b], "1", "x")


def test_a_derived_figure_is_computed_from_the_signed_values_and_rounded_once():
    """TRUTH (lander ruling 2026-11-26): the arithmetic is on the signed source values, rounded once for display;
    never on the operands as rounded for drawing (a 20 m well against a 60 ft landmark drew 'ABOUT 2.0 M' for a true
    1.71 m gap)."""
    well = figures.operand("subject", "Well", "20 M", 20.0, "M", True, "scale_fact")
    mark = figures.operand("reference", "Landmark", "18 M", 60 / 3.28084, "M", True, "https://example.org/landmark")
    good = figures.derivation("difference", [well, mark], "1.7", "ABOUT 1.7 M")
    assert abs(good["result"] - 1.712) < 1e-3 and good["hedged"] and not figures.derivation_faults(good)
    assert figures.derivation_faults(figures.derivation("difference", [well, mark], "2.0", "ABOUT 2.0 M"))
    # an operand recorded at its drawn rounding (18, not 18.288) does not state the source: the gate refuses it
    fact = {"value": 20.0, "unit": "m", "dimension": "depth", "subject": "Well", "approximate": True}
    feet = 20.0 * 3.28084
    ref = graphics.pick_reference(feet)
    st = graphics.scale_statement(fact, feet, ref[0], ref[1], True)
    rec = st["derivation"]
    assert not figure_gate.derived_faults("bracket", st["note"], rec, {"scale_fact": fact})
    true = abs(20.0 - ref[1] / 3.28084)
    assert abs(rec["result"] - true) < 1e-4 and figures.rounded_once(rec["shown"], true)
    assert all(rec["shown"] in t for t in (st["note"], st["lines"][rec and st["derived_line"]]))
    rounded = dict(rec, operands=[rec["operands"][0], dict(rec["operands"][1], value=round(ref[1] / 3.28084))])
    assert figure_gate.derived_faults("bracket", st["note"], rounded, {"scale_fact": fact})
    assert figures.rounded_once("180", 180.39) and not figures.rounded_once("181", 180.39)
    assert not figures.rounded_once("2.0", 1.712) and figures.rounded_once("2", 1.712)
    # the measure the corpus runs (docs/.../corpus/fig_derived.py), on its own arithmetic
    assert fm.truth_fault("difference", "ABOUT 2.0 M", "2.0", {"20", "18"}, 20.0, 18.288)
    assert not fm.truth_fault("difference", "ABOUT 1.7 M", "1.7", {"20", "18"}, 20.0, 18.288)
    assert fm.truth_fault("ratio", "about 3 times as tall", "3", {"138", "305"}, 138.0, 305.0)
    assert not fm.truth_fault("ratio", "about 2 times its length.", "2", {"138", "305"}, 138.0, 305.0)
    assert fm.truth_fault("ratio", "nearly the same height.", "", set(), 100.0, 150.0)


def test_gate_recomputes_a_derived_comparison():
    fact = FACTS[0]
    feet = fact["value"] * 3.28084
    ref = graphics.pick_reference(feet)
    st = graphics.scale_statement(fact, feet, ref[0], ref[1], True)
    rec = st["derivation"]
    assert st["note"] == rec["text"] and rec["rule"] == "difference"
    assert not figure_gate.derived_faults("bracket", st["note"], rec, {"scale_fact": fact})
    assert figure_gate.derived_faults("bracket", st["note"], rec, {"scale_fact": dict(fact, value=300.0)})
    assert figure_gate.derived_faults("bracket", "ABOUT 999 M", rec, {"scale_fact": fact})
    assert figure_gate.derived_faults("bracket", st["note"], None, {"scale_fact": fact})    # fail closed
    bare = graphics.scale_statement(fact, feet, ref[0], ref[1], True, derive=False)
    assert bare["derivation"] is None and not any(str(round(rec["result"])) in ln for ln in bare["lines"])


def test_label_stack_holds_its_gap_or_says_it_cannot():
    assert graphics.label_stack([0.0, 0.0, 0.0], [50.0] * 3, 0.0, 160.0, 20.0, hold=True) is None
    tops = graphics.label_stack([0.0, 0.0, 0.0], [50.0] * 3, 0.0, 200.0, 20.0, hold=True)
    assert tops is not None and all(b - a >= 70.0 - 1e-6 for a, b in zip(tops, tops[1:]))


def test_then_now_divider_is_hard_and_the_fade_is_short_and_clear():
    from PIL import Image
    w, h = 320, 180
    a, b = Image.new("RGB", (w, h), (150, 40, 40)), Image.new("RGB", (w, h), (40, 40, 150))
    out = then_now.join_blurred(a, b, 160.0, -40.0)
    rule = out.getpixel((160, 90))
    from src import brand
    assert max(abs(rule[k] - brand.BONE[k]) for k in range(3)) <= 2      # a hard bone rule at the divider's place
    px = [out.getpixel((x, 90)) for x in range(166, 180)]                # the join behind it blends the two photos
    assert all(p[1] <= 45 for p in px)                                   # never bone or ink mixed over them
    box = (20, 20, 120, 60)
    lead = then_now.GROUP_LEAD * w
    assert not then_now.group_clear(box, "then", 120 + lead - 2, w, h)   # hidden before the divider is that near
    assert then_now.group_clear(box, "then", 120 + lead + 2, w, h)
    sched = then_now.group_alpha([True] * 10 + [False] * 4 + [True] * 10)
    assert all(v == 0 for v in sched[10:14])                             # gone while the divider is within the lead


def test_every_axis_figure_is_drawn_or_recorded_as_yielded(records):
    """RECORDS (freeze cycle 5): an axis figure the ruler implies (every multiple of its step inside the drawing) is
    drawn or recorded with its rule (the 9:16 Wieliczka '300' and the cross-section's tick by its deepest level were
    left out with no record); an axis figure never crosses title-safe (the 16:9 '1,250' past a 1,073 ft floor)."""
    rows = [row for r in records for row in fm.axis_yield_rows(r)]
    assert not _fails(rows), _fails(rows)
    for r in records:
        for y in (r.get("layout") or {}).get("axis_yielded") or []:
            assert y["rule"] == graphics.AXIS_YIELD_RULE and y["reason"] and y["text"]
    sec = [row for r in records if r["kind"] == "section" for row in fm.section_rows(r)]
    assert not [x for x in _fails(sec) if "outside the text box" in x[3]]
    # with a ledger open the yield is on it, named with its rule; without one the drawing is still made
    from src.refusal import collecting
    with collecting() as rows_:
        graphics.yield_axis_figure("scale x.mp4", "300", "it would crowd the floor's stated figure")
    assert rows_ and rows_[0]["omitted"] == "the axis figure 300" and rows_[0]["rule"] == graphics.AXIS_YIELD_RULE
    graphics.yield_axis_figure("scale x.mp4", "300", "no ledger: logged, not refused")


def test_a_region_line_that_yields_is_recorded():
    from src.refusal import collecting
    lay = graphics.MarkerLayout("pin", (540.0, 900.0), (("OUADANE", (540.0, 780.0), "ms"),), 60,
                                (480.0, 730.0, 600.0, 780.0), "above", None, (520.0, 830.0, 560.0, 900.0))
    why: list = []
    out = graphics.with_region_line(lay, "ADRAR PLATEAU, MAURITANIA", *TALL, lay.label_box, why)
    assert out is lay and why and why[-1] == graphics.LAST_REGION_LINE["reason"]
    with collecting() as rows_:
        graphics.yield_part("map m.mp4", "the region line ADRAR PLATEAU, MAURITANIA", graphics.REGION_YIELD_RULE,
                            why[-1])
    assert rows_ and rows_[0]["rule"] == graphics.REGION_YIELD_RULE
    rec = {"run": "r", "kind": "map", "format": "916", "elements": [{"text": "OUADANE"}], "drawn": True,
           "omissions": rows_}
    assert fm.region_line_rows(rec, "ADRAR PLATEAU, MAURITANIA")[0][4] == "RECORDED"
    assert fm.region_line_rows(dict(rec, omissions=[]), "ADRAR PLATEAU, MAURITANIA")[0][4] == "FAIL"


SWEEP_M = [1.75, 2, 3, 4.6, 5, 6, 6.5, 7, 8, 9, 10, 12, 15, 17, 18, 19, 20, 21, 22, 25, 28, 30, 32.6, 35, 40, 42, 45, 50,
           55, 60, 70, 80, 85, 90, 93, 96, 100, 110, 120, 130, 140, 146, 147, 150, 160, 180, 200, 220, 250, 280, 300,
           327, 350, 400, 450]
SWEEP_FT = [6, 8, 10, 11.5, 15, 18, 20, 25, 30, 40, 45, 50, 55, 58, 60, 62, 65, 66, 70, 80, 90, 100, 107, 120, 138, 150,
            180, 200, 250, 280, 300, 305, 310, 330, 350, 400, 455, 470, 481, 500, 600, 700, 800, 1000, 1073, 1200, 1400,
            1500]


def _stated(note: str):
    import re
    m = re.search(r"([\d,]+(?:\.(\d+))?)\s*(FT|M)\b(?:\s+(\d+)\s*IN)?", note or "", re.I)
    if not m:
        return None
    v, step = float(m.group(1).replace(",", "")), 10 ** -len(m.group(2) or "")
    if m.group(4) is not None:
        v, step = v + float(m.group(4)) / 12, 1 / 12
    return v, step


def test_every_derived_figure_over_the_rules_domain_is_true_to_the_signed_values():
    """Review D1 (freeze cycle 5, the reviewer's 594-case sweep): over every metre and foot value x depth / height /
    length x hedged / exact, a depth's drawn gap is within half its last stated digit of the gap between the SIGNED
    values (the fact, the reference file's figure), never 'about 0' for a gap (that is 'nearly level'), and a ratio
    is the comparison rule on the signed values; computed here from the inputs, never from the drawn operands."""
    import re
    false, zero, ratios, n = [], [], [], 0
    for unit, vals in (("m", SWEEP_M), ("ft", SWEEP_FT)):
        for v in vals:
            for dim in ("depth", "height", "length"):
                for approx in (True, False):
                    fact = {"value": float(v), "unit": unit, "dimension": dim, "subject": "Subject",
                            "approximate": approx}
                    feet = v * (3.28084 if unit == "m" else 1.0)
                    ref = graphics.pick_reference(feet)
                    if not ref:
                        continue
                    n += 1
                    k = graphics.per_ft(graphics.lead_unit(fact))
                    st = graphics.scale_statement(fact, feet, ref[0], ref[1], dim == "depth")
                    if dim == "depth":
                        true = abs(ref[1] - feet) * k
                        got = _stated(st["note"])
                        if got is None:
                            assert "LEVEL" in str(st["note"]), (v, unit, st["note"])
                            continue
                        if got[0] == 0:
                            zero.append((v, unit, st["note"]))
                        if abs(got[0] - true) > 0.5 * got[1] + 1e-9:
                            false.append((v, unit, ref[0], st["note"], round(true, 3)))
                    else:
                        big, small = max(ref[1], feet) * k, min(ref[1], feet) * k
                        want = "near" if big / small < 1.1 else figures.comparison_ratio(big, small)
                        text = " ".join(st["lines"])
                        m = re.search(r"about ([\d.]+) times", text, re.I)
                        drawn = "near" if "nearly" in text.lower() else m.group(1) if m else None
                        if drawn != want:
                            ratios.append((v, unit, dim, ref[0], drawn, want))
    assert n == 594
    assert not false and not zero and not ratios, (false[:5], zero[:5], ratios[:5])


def test_the_answer_is_one_step_above_the_drawings_other_figures_and_no_bracket_stands_alone(records):
    """FIG-09: the subject's own figure is larger than the landmark's figure and the bracket's; review D5: no axis
    figure is larger than a stated quantity on the drawing; review D4: a depth's comparison is not withheld for these
    ordinary inputs; review D2: on a video depth drawing the
    landmark's height is not set beside the depth ruler (the line of meaning states it)."""
    rows = [row for r in records for row in fm.hierarchy_rows(r)]
    assert not _fails(rows), _fails(rows)
    for r in records:
        if r["kind"] != "scale" or not r.get("drawn"):
            continue
        names = {e["name"] for e in r["elements"]}
        figs = [x["px"] for e in r["elements"] if e["name"] in fm.SUBJECT_FIGURE + fm.OTHER_FIGURES
                for x in e["runs"] if any(ch.isdigit() for ch in x["text"])]
        axis = [x["px"] for e in r["elements"] if fm.RULER_ROLE.match(e["name"]) for x in e["runs"]]
        if r["format"] != "169":       # D5 is the 9:16 size system (16:9 labels: FIG-06 phase 2, hand-back)
            assert not figs or not axis or max(axis) <= min(figs), (r["run"], r["format"], axis, figs)
        if r["input"]["dimension"] == "depth":
            lay = r["layout"]
            if r["format"] != "slide":      # the video drawings: never withheld for these inputs (review D4)
                assert lay["derived"] is not None, (r["run"], r["format"], r["omissions"])
                assert "ref_label" not in names and "reference" in lay["labelled"]
            elif lay["derived"] is None:     # the slide room (open, hand-back): withheld only on the record, and then
                assert any("derived figure" in str(o.get("omitted")) for o in r["omissions"])   # no orphan figure
                assert "bracket" not in names


MUTATION_CASES = [   # (value, unit, dimension): a depth in metres, a small metre gap, feet, a ratio, a feet-and-inches gap
    (327.0, "m", "depth"), (20.0, "m", "depth"), (1073.0, "ft", "depth"), (28.0, "m", "height"), (470.0, "ft", "depth"),
]


def _case(v, unit, dim):
    fact = {"value": v, "unit": unit, "dimension": dim, "subject": "Subject", "approximate": True}
    feet = v * (3.28084 if unit == "m" else 1.0)
    ref = graphics.pick_reference(feet)
    st = graphics.scale_statement(fact, feet, ref[0], ref[1], dim == "depth")
    u = graphics.lead_unit(fact)
    per = graphics.per_ft(u)
    from src.reference_facts import reference_by_label, stated_figures
    stated = stated_figures(reference_by_label(ref[0]), u)
    fig, metric = graphics.scale_figure(fact, feet, dim == "depth")
    op_drawn = {n for t in (fig, metric, *(c[1] for c in stated)) for n in fm._numbers(t)}
    return fact, feet, ref, st, u, v if (unit == "m") == (u == "M") else feet * per, ref[1] * per, op_drawn


def _bump(text, shown):
    """`text` with its derived figure one last digit off (a feet-and-inches figure: one inch)."""
    import re
    if " IN" in shown.upper():
        m = re.search(r"(\d+) (IN|in)\b", text)
        return text[:m.start(1)] + str(int(m.group(1)) + 1) + text[m.end(1):]
    dp = len(shown.partition(".")[2])
    return text.replace(shown.lower() if shown.lower() in text else shown,
                        f"{float(shown.replace(',', '')) + 10 ** -dp:,.{dp}f}", 1)


@pytest.mark.parametrize("v,unit,dim", MUTATION_CASES)
def test_every_guard_catches_a_derived_figure_one_digit_off(v, unit, dim):
    """Review 2 (N2): a drawn figure changed by one last digit (the bracket, the line of meaning, a feet-and-inches gap,
    a ratio) is caught by the drawn-figure gate and the corpus measure; the record changed with it by
    figures.derivation_faults too. The unmutated drawing passes all three."""
    import copy
    fact, feet, ref, st, u, s_true, r_true, op_drawn = _case(v, unit, dim)
    d, pay = st["derivation"], {"scale_fact": fact}
    line = st["lines"][st["derived_line"]]
    shown = d["shown"]
    texts = [("meaning1", line)] + ([("bracket", st["note"])] if st["note"] else [])
    for name, t in texts:                                       # the control: nothing faults
        assert not figure_gate.derived_faults(name, t, d, pay), (name, t)
        assert not fm.truth_fault(d["rule"], t, shown, op_drawn, s_true, r_true, u), (name, t)
    for name, t in texts:                                       # the drawing one digit off, its record unchanged
        bad = _bump(t, shown if " IN" not in shown.upper() else shown)
        assert bad != t
        assert figure_gate.derived_faults(name, bad, d, pay), (name, bad)
        assert fm.truth_fault(d["rule"], bad, shown, op_drawn, s_true, r_true, u), (name, bad)
    rec = copy.deepcopy(d)                                      # the record off with it
    rec["shown"] = _bump(shown, shown) if " IN" in shown.upper() else _bump(shown, shown)
    assert figures.derivation_faults(rec) and figure_gate.derived_faults("meaning1", line, rec, pay)


@pytest.mark.parametrize("v,unit,dim", MUTATION_CASES)
def test_a_word_form_is_a_derived_claim_with_its_threshold(v, unit, dim):
    """Review 2 (N2): "nearly level" / "nearly the same" state no number but are derived claims: each record carries
    its threshold and all three guards refuse the form where the signed values do not meet it."""
    import copy
    fact, feet, ref, st, u, s_true, r_true, op_drawn = _case(v, unit, dim)
    d, pay = st["derivation"], {"scale_fact": fact}
    word = copy.deepcopy(d)
    word.update(shown="", text="nearly the same" if d["rule"] == "ratio" else "NEARLY LEVEL",
                form_limit=figures.NEAR_RATIO if d["rule"] == "ratio" else graphics.LEVEL_GAP[u])
    said = "is nearly the same height." if d["rule"] == "ratio" else "would reach nearly to the surface."
    assert figures.derivation_faults(word)
    assert figure_gate.derived_faults("meaning1", said, word, pay)
    assert fm.truth_fault(d["rule"], said, "", op_drawn, s_true, r_true, u)
    # and a word form drawn where the record states a number
    assert figure_gate.derived_faults("meaning1", said, d, pay)
    # a word form with no threshold recorded is refused
    assert figures.derivation_faults({k: v_ for k, v_ in word.items() if k != "form_limit"})


def test_the_landmark_is_drawn_as_its_signed_figure_and_the_arithmetic_adds_up():
    """Review 2 (N1, ruling R1): the landmark is drawn as the reference statement's own figure at its precision
    ('146.5 m', never the rounding '146 m'), judged like any drawn number (no meaning / ref_label exemption); a gap
    from a reference its own file calls rounded is hedged (N4); 11.5 in and over carry into the foot (N6)."""
    fact = {"value": 147.0, "unit": "m", "dimension": "depth", "subject": "S", "approximate": True}
    feet = 147 * 3.28084
    ref = graphics.pick_reference(feet)
    st = graphics.scale_statement(fact, feet, ref[0], ref[1], True)
    assert "146.5 m" in st["lines"][0] and "0.5 m" in st["lines"][1]
    assert figure_gate.derived_faults("meaning0", st["lines"][0].replace("146.5", "146"), st["derivation"],
                                      {"scale_fact": fact})
    assert not figure_gate.DERIVED.match("meaning0") and not figure_gate.DERIVED.match("ref_label")
    signed = figures.signed_figures(figure_gate.signed_texts({"scale_fact": fact}))
    assert figure_gate.figure_faults("146 M", signed) and not figure_gate.figure_faults("146.5 M", signed)
    exact = {"value": 200.0, "unit": "ft", "dimension": "depth", "subject": "S", "approximate": False}
    r = graphics.pick_reference(200.0)
    assert graphics.scale_statement(exact, 200.0, r[0], r[1], True)["derivation"]["hedged"]   # Liberty: rounded
    from src.reference_facts import measurement_text
    assert measurement_text(11.97).startswith("12 FT 0 IN") and " 12 IN" not in measurement_text(48.04 - 36)
