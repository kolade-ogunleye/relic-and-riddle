"""SYS-R8-1 (design-freeze cycle 0 round 8, pre-lane system batch): the drawn-figure gate's four false classes and the
one wrongly drawn number of the round-8 regression check (queue CYCLE0.r8-regression-check.md), closed by class:

1. a timeline's gap label is DERIVED from the two signed years it spans: the renderer records its rule and operands,
   the gate re-computes it (a wrong gap still fails);
2. a sentence's lead hedge carries without its comma, and a caption sequence is read with the narration's own
   punctuation where its words are the narration's (a deep dive's narration is its acts');
3. a span whose ends carry different eras parses as one span with per-end eras and matches itself;
4. a converted unit is drawn at the precision that keeps it within the gate's one tolerance.

Invariants, not pinned measurements. Topic-agnostic: the events and texts are the real runs' shapes (runs named by
number only).
"""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
from typing import Any

import pytest

from src import figure_gate, figures, graphics, reference_facts
from omission_ledger import omission_ledger  # noqa: F401  (autouse: the ledger round each test, as make_all)

# ------------------------------------------------------------------------------------------------ fixtures

# the real runs' signed timelines (run 1792790550: three exact years; run 1792949304: two circa BC dates and a year)
EXACT = [{"year": 1941, "label": "Disassembled by troops"}, {"year": 1944, "label": "Documentation ends"},
         {"year": 1997, "label": "Original mosaic recovered"}]
CIRCA_BC = [{"year": -4000, "approximate": True, "label": "Initial prehistoric use"},
            {"year": -2500, "approximate": True, "label": "End of prehistoric use"},
            {"year": 1902, "approximate": False, "label": "Accidentally discovered"}]
# the regression check's dense stand-in (five entries: the date-column layout)
DENSE = [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
         {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
         {"year": 1523, "label": "The knights sail for Malta"},
         {"year": 1912, "label": "Italy takes the Dodecanese"},
         {"year": 1988, "label": "The Medieval City of Rhodes is listed"}]
SECTION = {"subject": "An underground city", "total_depth_ft": 280.0, "approximate": True,
           "levels": [{"depth_ft": 15.0, "label": "Stables, wine presses and storage"},
                      {"depth_ft": 35.0, "label": "Living quarters and kitchens"},
                      {"depth_ft": 140.0, "label": "Cruciform church and school"},
                      {"depth_ft": 280.0, "label": "Deepest wells and shaft base"}]}
TALL, WIDE = (1080, 1920), (1920, 1080)


@pytest.fixture
def plan_only(monkeypatch):
    """Renders composed but never encoded (0.1 s, frames consumed); the contrast record still written."""
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.1)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    monkeypatch.setattr(graphics, "record_nontext", lambda *a, **k: None)

    def encode(frames, out, n, width=None, height=None):
        for _ in frames:
            pass
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"")
        return Path(out)
    monkeypatch.setattr(graphics, "_encode", encode)


def _record(out: Path) -> dict[str, Any]:
    return json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))


def _write(run: Path, rec: dict[str, Any]) -> None:
    (run / "graphics").mkdir(parents=True, exist_ok=True)
    (run / "graphics" / "graphic_timeline.mp4.contrast.json").write_text(json.dumps(rec), encoding="utf-8")


# ------------------------------------------------------------------------------------------------ 1. gap labels

def test_the_gap_label_is_the_one_elapsed_rule():
    """graphics.gap_text draws figures.elapsed, for exact and approximate dates across eras (no year 0)."""
    years = [-9500, -4000, -2500, -700, -1, 1, 43, 780, 1300, 1522, 1523, 1902, 1941, 1944, 1997]
    for a, b in itertools.combinations(years, 2):
        for approx in (False, True):
            ea, eb = {"year": a, "approximate": approx}, {"year": b}
            want = figures.elapsed(a, b, approx)
            text = graphics.gap_text(ea, eb)
            if want is None:
                assert text == ""
                continue
            assert text == f"{'ABOUT ' if want[1] else ''}{want[0]:,} YEARS LATER"
            assert want[1] == approx
            got = figures.parse(text)[0]
            assert got.values == (float(want[0]),) and bool(got.hedge) == approx
    assert figures.years_between(-1, 1) == 1 == graphics.years_between(-1, 1)


@pytest.mark.parametrize("events", [EXACT, CIRCA_BC, DENSE], ids=["exact", "circa_bc", "dense"])
@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_every_drawn_gap_label_records_its_operands_and_passes_the_gate(tmp_path, plan_only, events, size):
    run = tmp_path / "run"
    out = graphics.render_timeline(events, run / "graphics" / "graphic_timeline.mp4", *size)
    assert out is not None
    rec = _record(out)
    gaps = [e for e in rec["elements"] if figure_gate.GAP.match(e["name"])]
    drawn = [e for e in gaps if any(c.isdigit() for c in e["text"])]
    want = {k for k in range(1, len(events)) if graphics.gap_text(events[k - 1], events[k])}
    assert {int(e["name"][3:].split("_")[0]) for e in drawn} == want          # every gap drawn, each recorded
    for e in gaps:                                                              # every row carries its operands
        k = int(e["name"][3:].split("_")[0])
        assert e["derived"] == figures.gap_record(events[k - 1], events[k]), e
    res = figure_gate.check_run(run, {"timeline": events})
    assert res["faults"] == [], res["faults"]
    assert res["derived"] >= len(drawn)


def _timeline_record(events, size=TALL) -> dict[str, Any]:
    rows = []
    for k in range(1, len(events)):
        text = graphics.gap_text(events[k - 1], events[k])
        if text:
            rows.append({"name": f"gap{k}", "text": text, "derived": figures.gap_record(events[k - 1], events[k])})
    return {"clip": "graphic_timeline.mp4", "elements": rows}


@pytest.mark.parametrize("events", [EXACT, CIRCA_BC, DENSE], ids=["exact", "circa_bc", "dense"])
def test_a_wrong_gap_label_still_fails(tmp_path, events):
    good = _timeline_record(events)
    _write(tmp_path / "ok", good)
    assert figure_gate.check_run(tmp_path / "ok", {"timeline": events})["faults"] == []
    for i, row in enumerate(good["elements"]):
        n = figures.parse(row["text"])[0]
        wrong_number = {**row, "text": row["text"].replace(f"{int(n.values[0]):,}", f"{int(n.values[0]) + 7:,}")}
        no_hedge = {**row, "text": row["text"].replace("ABOUT ", "")}
        added_hedge = {**row, "text": row["text"] if n.hedge else "ABOUT " + row["text"]}
        unsigned = {**row, "derived": {**row["derived"], "from": row["derived"]["from"] - 1}}
        precision = {**row, "derived": {**row["derived"], "approximate": not row["derived"]["approximate"]}}
        legacy_wrong = {k: v for k, v in wrong_number.items() if k != "derived"}
        for bad in (wrong_number, no_hedge, added_hedge, unsigned, precision, legacy_wrong):
            if bad == row:
                continue
            rec = {**good, "elements": good["elements"][:i] + [bad] + good["elements"][i + 1:]}
            d = tmp_path / f"bad{i}_{id(bad)}"
            _write(d, rec)
            assert figure_gate.check_run(d, {"timeline": events})["faults"], (bad, row)


@pytest.mark.parametrize("events", [EXACT, CIRCA_BC, DENSE], ids=["exact", "circa_bc", "dense"])
def test_a_legacy_gap_record_without_operands_is_checked_against_the_signed_years(tmp_path, events):
    """A record written before records carried operands (the regression check's captured records): a correct label
    passes when two signed years give it by the rule; a label no pair gives fails."""
    rec = _timeline_record(events)
    for e in rec["elements"]:
        del e["derived"]
    _write(tmp_path, rec)
    assert figure_gate.check_run(tmp_path, {"timeline": events})["faults"] == []
    assert figure_gate.gap_faults("1,234 YEARS LATER", None, {"timeline": events})
    assert figure_gate.gap_faults("LATER", None, {"timeline": events}) == []      # a continuation row


def test_the_compact_and_wrapped_gap_forms_carry_the_same_figure():
    for text in ("ABOUT 1,500 YEARS LATER", "~1,500 YRS LATER", "ABOUT 1,500 YEARS"):
        assert figure_gate.gap_faults(text, figures.gap_record(*CIRCA_BC[:2]), {"timeline": CIRCA_BC}) == []
    assert graphics.compact_gap("ABOUT 1,500 YEARS LATER") == "~1,500 YRS LATER"


# ------------------------------------------------------------------------------------------------ 2. lead hedges

LEADS = ("By one estimate", "By some estimates", "According to one estimate", "According to current estimates",
         "On current estimates", "By one approximation", "Estimates suggest", "It is estimated")


@pytest.mark.parametrize("lead", LEADS)
@pytest.mark.parametrize("punct", [", ", " ", " - ", " – ", ": ", " — "], ids=["comma", "none", "hyphen", "en", "colon",
                                                                               "em"])
def test_a_lead_hedge_carries_whatever_punctuation_follows_it(lead, punct):
    text = f"{lead}{punct}the text uses an alphabet of 20 to 25 characters."
    figs = figures.parse(text)
    assert [f.drawn() for f in figs] == [f"{'APPROXIMATELY' if 'approx' in lead.lower() else 'ESTIMATED'} 20–25"], text


def test_a_lead_hedge_governs_its_own_sentence_only():
    figs = figures.parse("It was dated between 1404 and 1438. By one estimate the text has 20 to 25 characters. "
                         "Beside it are 30 plants.")
    assert [f.drawn() for f in figs] == ["1404–1438", "ESTIMATED 20–25", "30"]
    assert [f.drawn() for f in figures.parse("Estimates of 20 vary.")] == ["20"]    # not a lead phrase


# a real narration's shape (run 1792965308): the hedge is the sentence's lead, the caption words carry no punctuation
NARRATION = ("Radiocarbon dating established that the parchment dates between 1404 and 1438. By one estimate, the "
             "text is written from left to right in a script where an alphabet of 20 to 25 characters accounts for "
             "the entire work. Beside botanical diagrams sit 30 unidentified plants.")


def _captions(words: str, run: Path) -> None:
    bursts = [words.split()[i:i + 3] for i in range(0, len(words.split()), 3)]
    els = []
    for n, b in enumerate(bursts):
        els += [{"name": f"caption {n} bone", "text": " ".join(b)}, {"name": f"caption {n} gold", "text": " ".join(b)}]
    run.mkdir(parents=True, exist_ok=True)
    (run / "cap.contrast.json").write_text(json.dumps({"clip": "captions", "elements": els}), encoding="utf-8")


def _spoken(text: str) -> str:
    return " ".join(w.strip(".,;:") for w in text.split())


def test_a_caption_sequence_is_read_with_the_narrations_punctuation(tmp_path):
    payload = {"narration_script": NARRATION}
    _captions(_spoken(NARRATION), tmp_path / "ok")
    assert figure_gate.check_run(tmp_path / "ok", payload)["faults"] == []
    seq = figure_gate.caption_text(_spoken(NARRATION), NARRATION)
    assert seq == NARRATION                                      # the words are the narration's: its text is read


@pytest.mark.parametrize("edit", [("20 to 25", "21 to 25"), ("By one estimate ", ""), ("30", "31"),
                                  ("1404 and 1438", "1404 and 1439")], ids=["figure", "hedge", "count", "year"])
def test_a_caption_that_differs_from_the_narration_is_judged_as_drawn(tmp_path, edit):
    payload = {"narration_script": NARRATION}
    words = _spoken(NARRATION).replace(*edit)
    _captions(words, tmp_path)
    assert figure_gate.check_run(tmp_path, payload)["faults"], edit


# a deep dive (run 1792740961_long): no narration_script; each act's narration_text is the narration
DEEP = {"title": "A desert landmark", "acts": [
    {"act_number": 2, "act_title": "Architecture", "narration_text": "By one estimate, the outer layers dip outward at "
                                                                         "angles between 10 and 20 degrees"},
    {"act_number": 1, "act_title": "Introduction", "narration_text": "Surveys record it at 40 kilometres across."}]}


def test_a_deep_dives_narration_is_its_acts_in_order(tmp_path):
    assert figure_gate.narration(DEEP) == ("Surveys record it at 40 kilometres across. By one estimate, the outer "
                                           "layers dip outward at angles between 10 and 20 degrees.")
    assert figure_gate.narration(DEEP) in figure_gate.signed_texts(DEEP)
    _captions(_spoken(figure_gate.narration(DEEP)), tmp_path / "ok")
    assert figure_gate.check_run(tmp_path / "ok", DEEP)["faults"] == []
    _captions(_spoken(figure_gate.narration(DEEP)).replace("By one estimate ", ""), tmp_path / "bad")
    assert figure_gate.check_run(tmp_path / "bad", DEEP)["faults"]          # the hedge dropped from the captions


# ------------------------------------------------------------------------------------------------ 3. era spans

ERA_SPANS = [("between 500 BC and 500 AD", ("BC", "AD"), (-500, 500)),
             ("from 200 BC to AD 500", ("BC", "AD"), (-200, 500)),
             ("Nazca (200 BC to 500 AD)", ("BC", "AD"), (-200, 500)),
             ("between 300 BCE and 200 CE", ("BC", "AD"), (-300, 200)),
             ("500 BC–AD 500", ("BC", "AD"), (-500, 500)),
             ("between AD 500 and 500 BC", ("AD", "BC"), (500, -500)),
             ("9500–9000 BCE", ("BC", "BC"), (-9500, -9000)),
             ("205 BC to 87 BC", ("BC", "BC"), (-205, -87)),
             ("400 to 200 BC", ("BC", "BC"), (-400, -200)),
             ("AD 43 to 410", ("AD", "AD"), (43, 410)),
             ("145,000 to 29,000 years ago", ("", ""), (145000, 29000))]


@pytest.mark.parametrize("text,eras,years", ERA_SPANS, ids=[t for t, _e, _y in ERA_SPANS])
def test_a_span_keeps_each_ends_era_and_matches_itself(text, eras, years):
    figs = figures.parse(text)
    assert len(figs) == 1, [f.drawn() for f in figs]
    f = figs[0]
    assert f.eras == eras and f.years == tuple(float(y) for y in years)
    again = figures.parse(f.drawn())
    assert len(again) == 1 and again[0].eras == f.eras and again[0].years == f.years, f.drawn()   # drawn whole
    assert figures.truth_faults(f.drawn(), [text]) == []                                          # matches itself
    for a, b in f.gold:                      # an era is never gold
        assert not any(w.upper() in ("BC", "BCE", "AD", "CE") for w in text[a:b].replace("–", " ").split()), text


def test_a_crossing_span_never_matches_a_one_era_span():
    assert figures.truth_faults("AD 500–500", ["between 500 BC and 500 AD"])
    assert figures.truth_faults("500 BC–AD 500", ["AD 500 to 500"])
    assert figures.truth_faults("500 BC–AD 500", ["500 to 500 BC"])


def test_the_stat_slide_still_withholds_a_crossing_span_it_cannot_draw_with_its_eras():
    """The stat slide draws figures without eras (round 8: drawing an era there is lane D's design change): a
    crossing span without its eras is not the figure, so its own truth check withholds it, as before."""
    body = "Created between 500 BC and 500 AD, spanning two phases."
    fig = figures.stat_figure(body)
    assert fig is not None and figures.truth_faults(fig.drawn(era=False), [body])


def test_contradictions_read_crossing_spans_on_one_time_line():
    same = {"caption": "Made between 500 BC and 500 AD by the people.", "description": "Made by the people between "
                                                                                       "400 BC and 500 AD."}
    assert figure_gate.contradictions(same)
    agree = {"caption": "Made between 500 BC and 500 AD by the people.", "description": "Made by the people between "
                                                                                        "500 BC and 500 AD."}
    assert figure_gate.contradictions(agree) == []


# ------------------------------------------------------------------------------------------------ 4. conversions

VALUES = sorted({round(10 ** (k / 40), 3) for k in range(-40, 201)} | {15.0, 35.0, 140.0, 280.0, 4.57, 10.67, 6.5,
                                                                       1.75, 25.5, 327.0, 1073.0})


def test_the_gate_and_the_renderers_share_one_tolerance():
    assert figure_gate.CONVERT_TOL is figures.CONVERT_TOL
    assert figure_gate._FT_PER is figures.LENGTH_PER_FT and graphics.FT_PER_M == figures.LENGTH_PER_FT["M"]


@pytest.mark.parametrize("pair", [("FT", "M"), ("M", "FT"), ("KM", "MILES"), ("MILES", "KM"), ("KG", "LB"),
                                  ("LB", "KG")], ids=lambda p: f"{p[0]}-{p[1]}")
def test_every_converted_value_is_within_the_gates_tolerance_at_the_fewest_decimals(pair):
    src, dst = pair
    words = {"FT": "ft", "M": "m", "KM": "km", "MILES": "miles", "KG": "kg", "LB": "lb"}
    for v in VALUES:
        text = figures.converted_number(v, src, dst)
        drawn = float(text.replace(",", ""))
        assert figures.within_tol(drawn, dst, v, src), (v, text)
        signed = figures.parse(f"{v:g} {words[src]}")
        assert figure_gate.figure_faults(f"{text} {words[dst]}", signed) == [], (v, src, text)   # the gate's own test
        dp = len(text.split(".")[1]) if "." in text else 0
        if dp:                                                            # never more precise than the rule needs
            shorter = float(f"{v * figures.in_base(1, src)[1] / figures.in_base(1, dst)[1]:.{dp - 1}f}")
            assert not figures.within_tol(shorter, dst, v, src), (v, text)


def test_the_scale_and_reference_conversions_follow_the_rule():
    for feet in VALUES:
        _lead, conv = graphics.measure_text(feet, "FT")
        m = float(conv.split()[0].replace(",", ""))
        assert figures.within_tol(m, "M", feet, "FT"), (feet, conv)
        metres = feet / graphics.FT_PER_M
        _lead_m, conv_ft = graphics.measure_text(feet, "M")
        assert figures.within_tol(float(conv_ft.split()[0].replace(",", "")), "FT", metres, "M"), (feet, conv_ft)
        assert f"({conv})" in reference_facts.measurement_text(feet)


@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_the_cross_sections_metres_pass_the_gate(tmp_path, plan_only, size):
    run = tmp_path / "run"
    out = graphics.render_cross_section(SECTION, run / "graphics" / "graphic_section.mp4", *size)
    assert out is not None
    rec = _record(out)
    metres = {e["text"] for e in rec["elements"] if e["name"].endswith("_metres")}
    # SYS-A-1: the level column is converted as one set (one precision, each member within the gate's tolerance)
    column = figures.converted_numbers([lv["depth_ft"] for lv in SECTION["levels"]], "FT", "M")
    assert metres == {f"{m} M" for m in column}
    res = figure_gate.check_run(run, {"cross_section": SECTION})
    assert res["faults"] == [], res["faults"]


def test_whole_metres_would_have_failed_the_gate():
    """The regression's case: 15 ft as "5 M" and 35 ft as "11 M" are outside the tolerance; the rule's are inside."""
    signed = figures.parse("15 ft") + figures.parse("35 ft")
    assert figure_gate.figure_faults("5 M", signed) and figure_gate.figure_faults("11 M", signed)
    assert not math.isclose(float(figures.converted_number(15, "FT", "M")), 5.0)
