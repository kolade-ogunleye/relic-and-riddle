"""Design-freeze cycle 0 round 8, integration 2 (SYS-R8-1 truth2 + SYS-R8-2 rules2): the lander's integration rulings,
each held by an invariant over its class.

a. drawn == signed: a signed scale value is drawn at its signed precision (6.5 m drew "6 M"), and the drawn-figure
   gate's conversion tolerance never passes a figure in the signed value's own unit (it passed "6 M" for 6.1 m).
b. the number card draws in the one gold scope (ruling 8): a span with "TO" is the figure whole, a bracketed qualifier
   the bone hedge, every row coloured by figures.gold_runs (Damascus "62 TO 67 (ESTIMATED)").
c. a deck's Instagram and Facebook copies are the same slides: one kicker set, its step planned once for both.
d. a map's place labels (graphics.place_plates) are one set: typography.plan_label_set, one treatment.
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


# ------------------------------------------------------------------ c. one kicker set across a deck's two copies
def _deck(tmp_path):
    from PIL import Image
    photos = []
    for i, colour in enumerate(((250, 250, 246), (20, 22, 26), (200, 170, 60), (20, 22, 26))):
        p = tmp_path / f"0{i}.jpg"
        Image.new("RGB", (1200, 1500), colour).save(p)
        photos.append(p)
    cards = [{"heading": "The dome", "body": "Built in concrete."}, {"heading": "The mix", "body": "Lime clasts."},
             {"heading": "The pour", "body": "Layer by layer."}, {"heading": "Your view", "body": "Which mattered more?"}]
    return photos, {"carousel_slides": cards, "title": "T", "poll": {}}


def _kickers(tmp_path, copy):
    import json
    rec = json.loads((tmp_path / copy / "still_layout.json").read_text(encoding="utf-8"))
    kickers = [e for s in sorted(rec) for e in rec[s]["elements"] if e["name"] == "kicker"]
    steps = {tuple(rec[s]["recipe"].get("kicker_step") or ()) for s in rec}
    return kickers, steps


@pytest.mark.parametrize("first", [False, True], ids=["instagram_first", "facebook_first"])
def test_c_both_copies_of_a_deck_take_one_kicker_step_planned_once(tmp_path, monkeypatch, first):
    from src import multi_format
    multi_format.clear_deck_steps()
    calls = []
    real = multi_format.deck_kicker_step
    monkeypatch.setattr(multi_format, "deck_kicker_step", lambda lays: calls.append(len(lays)) or real(lays))
    photos, payload = _deck(tmp_path)
    gen = multi_format.MultiFormatGenerator()
    for fb in (first, not first):
        gen.generate_carousel_sequence(photos, payload, tmp_path, facebook=fb)
    assert len(calls) == 1                        # planned once, over both copies' kickers
    ig, ig_steps = _kickers(tmp_path, "carousel")
    fb, fb_steps = _kickers(tmp_path, "carousel_fb")
    assert ig and fb
    assert ig_steps == fb_steps and len(ig_steps) == 1
    assert len({(k["variant"], tuple(k["step"]), tuple(k["fill"])) for k in ig + fb}) == 1, ig + fb


def test_c_a_changed_deck_is_planned_again(tmp_path):
    from src import multi_format
    photos, payload = _deck(tmp_path)
    key = multi_format.deck_set_key(tmp_path, payload["carousel_slides"], ["a"], photos, None)
    changed = multi_format.deck_set_key(tmp_path, payload["carousel_slides"][:3], ["a"], photos, None)
    assert key != changed
    multi_format.keep_deck_set_step(key, ("plate", 0))
    assert multi_format.deck_set_step(key) == (True, ("plate", 0))
    assert multi_format.deck_set_step(changed) == (False, None)
    multi_format.clear_deck_steps()
    assert multi_format.deck_set_step(key) == (False, None)


# ------------------------------------------------------------------ d. a map's place labels are one set
def _split_ground():
    from PIL import Image
    im = Image.new("RGB", (1200, 400), (18, 20, 24))
    im.paste((236, 232, 222), (600, 0, 1200, 400))   # one name on dark sea, one on bright sand
    return im


def test_d_a_maps_place_labels_take_one_treatment_that_holds_on_every_ground():
    from src.brand_system import GATES
    ground = _split_ground()
    font = graphics.place_font(34)
    labels = [[((80, 200), "ALEXANDRIA", font, "ls")], [((680, 200), "SIWA OASIS", font, "ls")]]
    alone = [graphics.place_plates(ground, rows) for rows in labels]
    assert len({p[0].variant for p in alone}) == 2          # each alone takes its own step (the case the set closes)
    sets = graphics.place_plate_set(ground, labels)
    assert len({(tuple(p.step), p.variant) for pls in sets for p in pls}) == 1
    for pls in sets:
        for p in pls:
            assert not p.empty and p.measure(p.draw(ground)) >= GATES["contrast.place"] - 1e-6


def test_d_one_label_is_a_set_of_one():
    ground = _split_ground()
    rows = [((80, 200), "ALEXANDRIA", graphics.place_font(34), "ls")]
    a, b = graphics.place_plates(ground, rows)[0], graphics.place_plate_set(ground, [rows])[0][0]
    assert (a.variant, a.step, a.box) == (b.variant, b.step, b.box)


def test_d_every_map_renderer_plans_its_place_labels_as_one_set():
    import ast
    import inspect
    import textwrap
    for fn in (graphics.render_route, graphics.label_plates_for, graphics.place_plates):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        calls = {ast.unparse(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
        assert "place_plate_set" in calls or "place_plates" in calls, fn.__name__
        assert "plan_label" not in calls, fn.__name__
    src = inspect.getsource(graphics.render_route)
    assert "place_plate_set(" in src          # the route's several names: one set
