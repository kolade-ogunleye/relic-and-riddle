"""Design-freeze cycle 0 round 8, integration 2 (SYS-R8-1 truth2 + SYS-R8-2 rules2): the lander's integration rulings,
each held by an invariant over its class.

a. drawn == signed: a signed scale value is drawn at its signed precision (6.5 m drew "6 M"), and the drawn-figure
   gate's conversion tolerance never passes a figure in the signed value's own unit (it passed "6 M" for 6.1 m).
b. the number card draws in the one gold scope (ruling 8): a span with "TO" is the figure whole, a bracketed qualifier
   the bone hedge, every row coloured by figures.gold_runs (Damascus "62 TO 67 (ESTIMATED)").
"""
from __future__ import annotations

import itertools

import pytest

from src import figure_gate, figures, graphics

SIGNED_SIZES = [(v, u) for v, u in itertools.product((2.25, 4.6, 6.1, 6.5, 11.5, 15, 20.5, 21.5, 85.3, 280, 327, 1073),
                                                     ("m", "ft"))]


def _fact(value, unit, approximate=False):
    return {"value": value, "unit": unit, "approximate": approximate, "dimension": "height", "subject": "X height"}


@pytest.mark.parametrize("value,unit", SIGNED_SIZES)
def test_a_scale_lead_is_the_signed_value_and_the_gate_passes_it(value, unit):
    fact = _fact(value, unit)
    feet = value * graphics.FT_PER_M if unit == "m" else value
    lead, conversion = graphics.scale_figure(fact, feet, False)
    want = graphics.lead_unit(fact)
    drawn = [f for f in figures.parse(lead) if f.values]
    assert len(drawn) == 1 and drawn[0].values == (float(value),) and figures._canon(drawn[0].unit) == want, lead
    signed = figures.signed_figures(figure_gate.signed_texts({"scale_fact": fact}))
    assert figure_gate.figure_faults(lead, signed) == []
    assert figure_gate.figure_faults(conversion, signed, need_hedge=False) == []
    # every other place the subject's own size is drawn says the same number (ruler floor, block label, meaning)
    assert graphics._depth_ruler(feet, "", want, fact)[-1][1] == graphics._short(feet, want, fact)
    assert graphics._short(feet, want, fact) in lead
    meaning = " ".join(graphics.scale_meaning_lines(fact, feet, "Statue of Liberty (305 ft)", 305.0, False))
    assert graphics._ft(feet, want, fact) in meaning


def test_a_whole_signed_values_keep_their_drawn_lead():
    """A lead the rounding already draws as the signed number does not change (no pixel change there)."""
    for value, unit in ((280, "ft"), (1073, "ft"), (327, "m"), (85, "m"), (4.6, "m")):
        fact = _fact(value, unit)
        feet = value * graphics.FT_PER_M if unit == "m" else value
        u = graphics.lead_unit(fact)
        assert graphics.measure_text(feet, u, graphics.signed_lead(fact, feet, u)) == graphics.measure_text(feet, u)


@pytest.mark.parametrize("drawn,signed", [("6 M", "6.5 m"), ("6 M", "6.1 m"), ("85 M", "about 85.3 m"),
                                          ("281 FT", "280.5 feet"), ("6.4 M", "6.5 m")])
def test_a_gate_never_passes_a_precision_loss_in_the_signed_unit(drawn, signed):
    assert figure_gate.figure_faults(drawn, figures.parse(signed))


@pytest.mark.parametrize("drawn,signed", [("21 FT", "6.5 m"), ("85 M", "about 280 feet"), ("1,073 FT", "327 m"),
                                          ("4.6 M", "15 ft")])
def test_a_gate_still_passes_a_conversion_to_the_other_unit(drawn, signed):
    assert figure_gate.figure_faults(drawn, figures.parse(signed), need_hedge=False) == []


def test_a_subject_precision_only_for_the_subjects_own_size():
    fact = _fact(6.5, "m")
    assert graphics.signed_lead(fact, 305.0, "M") is None          # a landmark's size is never the fact's
    assert graphics.signed_lead(fact, 6.5 * graphics.FT_PER_M, "FT") is None   # the fact signs metres
    assert graphics.signed_lead({"value": 10, "unit": "km"}, 32808.4, "FT") is None


# ------------------------------------------------------------------ b. the number card's gold scope (ruling 8)
@pytest.mark.parametrize("value,want", [
    ("62 TO 67 (ESTIMATED)", {"pre": "ESTIMATED", "num": "62–67", "unit": "", "sec": ""}),
    ("ABOUT 62 TO 67 FT (ESTIMATED)", {"pre": "ABOUT", "num": "62–67", "unit": "FT (ESTIMATED)", "sec": ""}),
    ("ABOUT 280 FT (85 M)", {"pre": "ABOUT", "num": "280", "unit": "FT", "sec": "85 M"}),
    ("94–104 MILLION YEARS AGO", {"pre": "", "num": "94–104", "unit": "MILLION YEARS AGO", "sec": ""}),
])
def test_b_figure_parts_keep_a_span_whole_and_a_bracketed_qualifier_a_hedge(value, want):
    assert graphics.figure_parts(value) == want
    parts = graphics.figure_parts(value)
    # the parts state the value's figure whole, with its hedge (the drawn-figure gate's reading)
    drawn = " ".join(x for x in (parts["pre"], parts["num"], parts["unit"]) if x)
    assert figure_gate.figure_faults(drawn, figures.parse(value)) == []


@pytest.mark.parametrize("value", ["62 TO 67 (ESTIMATED)", "ABOUT 62 TO 67 FT (ESTIMATED)", "94–104 MILLION YEARS AGO",
                                   "280 FT DEEP (85 M)", "EST. OVER 13,000 LBS", "1,200 TONS", "25+ MILES"])
def test_b_every_number_card_row_is_coloured_by_the_one_gold_scope(tmp_path, monkeypatch, value):
    """Every word the card draws is gold exactly when figures.gold_runs gilds it in the card's whole value: the number
    and its unit gold, a hedge, a dimension word and a bracketed qualifier bone."""
    from src import brand
    monkeypatch.setattr(graphics, "VIDEO_FPS", 4)
    seen = {}
    real = graphics.record_contrast

    def grab(out, frame, plates, rulers=None):
        seen["plates"] = plates
        return real(out, frame, plates, rulers)
    monkeypatch.setattr(graphics, "record_contrast", grab)
    graphics.render_stat_cards([{"value": value, "label": "Of the blades tested"}], tmp_path / "n.mp4")
    gold_words, bone_words = set(), set()
    for name, plate in seen["plates"]:
        if name == "label":
            continue
        subs = getattr(plate, "plates", None) or [plate]
        if len({tuple(q.fill[:3]) for q in subs}) == 1:   # a figure planned a character at a time: read whole
            subs = [type("Whole", (), {"text": plate.text, "fill": subs[0].fill})()]
        for p in subs:
            words = {w.strip("()") for w in p.text.split()} - {""}
            (gold_words if tuple(p.fill[:3]) == tuple(graphics.figure_fill()) else bone_words).update(words)
            assert tuple(p.fill[:3]) in (tuple(graphics.figure_fill()), tuple(brand.BONE)), (name, p.text, p.fill)
    want_gold = set()
    for run, gold in figures.gold_runs(graphics.figure_text(value)):
        if gold:
            want_gold |= {w.strip("()") for w in run.replace("–", " ").split()}
    drawn_gold = {g for w in gold_words for g in w.replace("–", " ").split()}
    assert drawn_gold == want_gold - {"TO"}, (gold_words, want_gold)
    assert not ({w for w in bone_words if any(c.isdigit() for c in w)}), bone_words
    if "ESTIMATED" in value:
        assert "ESTIMATED" in bone_words and "ESTIMATED" not in gold_words

