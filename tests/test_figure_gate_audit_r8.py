"""The drawn-figure gate's four defects found by the read-only production audit (C:/tmp/qf-handoff/queue/
PROD-FIGURE-AUDIT.md, 2026-11-18), as synthetic cases (run numbers cited only):

1. A descending era span lost its era in Figure.drawn() and re-parsed as two numbers: run 1793034688's correct number
   card "9500–9000 BCE" and 1793433058's caption "APPROXIMATELY 70–60 BC" were held.
2. Caption bursts were judged one card at a time: 1793314157 "AS MANY" | "AS 20,000 PEOPLE" and 1793433058 "ROUGHLY"
   | "205 BC TO 87 BC" were held although spoken and shown together.
3. Fail-open: release_check passed 1792854124, 1793034688 and 1793036660 with 0 elements judged (no layout records).
4. Spelled-out scaled numbers ("two thousand years ago", 1793433058's caption) gave no figure and were never judged.
No renderer's pixels change: the era is not gilded, a spelled-out number is judged but never gilded or drawn as a stat.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from src import figure_gate, figures, heading


# ------------------------------------------------------------------------------------------------- 1. the era
@pytest.mark.parametrize("signed, drawn", [
    ("The site dates to between 9500 and 9000 BCE.", "9500–9000 BCE"),
    ("It was built approximately 70-60 BC.", "APPROXIMATELY 70–60 BC"),
    ("The device dates roughly 205 BC to 87 BC.", "ROUGHLY 205–87 BC"),
    ("Rome invaded in AD 43.", "AD 43"),
    ("Tools here date from roughly 145,000 to 29,000 years ago.", "ROUGHLY 145,000–29,000 YEARS AGO"),
], ids=["bce-span", "bc-hedged", "bc-to-bc", "ad", "years-ago"])
def test_an_era_is_kept_through_drawn_and_the_reparse(signed, drawn):
    fig = figures.parse(signed)[0]
    assert fig.drawn() == drawn
    assert figures.truth_faults(drawn, [signed]) == []
    assert figure_gate.figure_faults(drawn, figures.parse(signed)) == []


def test_the_era_still_distinguishes_and_the_ends_still_fail():
    signed = ["The site dates to between 9500 and 9000 BCE."]
    assert figures.truth_faults("9500 BCE", signed)               # one end of the span
    assert figures.truth_faults("9500–9000 CE", signed)           # the other era


def test_the_era_is_never_gold_and_the_stat_slide_draws_as_before():
    assert figures.gold_runs("9500–9000 BCE") == [("9500–9000", True), (" BCE", False)]
    assert figures.gold_runs("AD 43") == [("AD ", False), ("43", True)]
    fig = figures.stat_figure("The site dates to between 9500 and 9000 BCE.")
    assert fig.drawn(era=False) == "9500–9000" and fig.record()["drawn"] == "9500–9000"


def test_the_number_card_case_passes_the_gate(tmp_path):
    run = tmp_path / "run"
    (run / "graphics").mkdir(parents=True)
    side = {"elements": [{"name": "figure", "text": "9500–9000"}, {"name": "unit", "text": "BCE"},
                         {"name": "label", "text": "WHEN THE PILLARS ROSE"}]}
    (run / "graphics" / "graphic_numbers.mp4.contrast.json").write_text(json.dumps(side), encoding="utf-8")
    payload = {"stat_cards": [{"value": "9500–9000 BCE", "label": "When the pillars rose"}],
               "narration_script": "Its pillars rose between 9500 and 9000 BCE."}
    res = figure_gate.check_run(run, payload)
    assert res["faults"] == [] and res["judged"] >= 1


# ------------------------------------------------------------------------------------------------- 2. captions
def _captions(run: Path, bursts: list[str]) -> None:
    els = []
    for k, t in enumerate(bursts):
        els.append({"name": f"caption {k} bone", "text": t, "drawn": 9.1})
        els.append({"name": f"caption {k} gold", "text": t, "drawn": 7.2})
    (run / "master.contrast.json").write_text(json.dumps({"clip": "captions", "elements": els}), encoding="utf-8")


@pytest.mark.parametrize("bursts, narration", [
    (["It could shelter as many", "as 20,000 people"], "It could shelter as many as 20,000 people."),
    (["The device dates roughly", "205 BC to 87 BC"], "The device dates roughly 205 BC to 87 BC."),
], ids=["as-many-as", "roughly-bc"])
def test_a_hedge_on_the_previous_caption_card_is_read_with_its_figure(tmp_path, bursts, narration):
    run = tmp_path / "run"
    run.mkdir()
    _captions(run, bursts)
    res = figure_gate.check_run(run, {"narration_script": narration})
    assert res["faults"] == [] and res["judged"] == 1
    _captions(run, [bursts[0].rsplit(" ", 1)[0], bursts[1]])           # the hedge really dropped: held
    held = figure_gate.check_run(run, {"narration_script": narration})["faults"]
    assert held and "(captions)" in held[0]


def test_the_caption_sequence_is_the_cue_text_in_order_once_each():
    rec = {"clip": "captions", "elements": [{"name": "caption 1 bone", "text": "as 20,000"},
                                             {"name": "caption 0 bone", "text": "as many"},
                                             {"name": "caption 1 gold", "text": "as 20,000"}]}
    assert figure_gate.caption_sequence(rec) == "as many as 20,000"


# ------------------------------------------------------------------------------------------------- 3. fail-open
def _still(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(path)


@pytest.mark.parametrize("files", [["thumbnail_cover.jpg", "stories/story_01_hook.jpg"],
                                   ["graphics/graphic_numbers.mp4"], ["carousel/carousel_slide_01.jpg"]],
                         ids=["covers-and-story", "graphic", "carousel"])
def test_a_run_with_drawn_surfaces_and_no_records_is_held_never_passed(tmp_path, files):
    run = tmp_path / "run"
    for f in files:
        if f.endswith(".mp4"):
            (run / f).parent.mkdir(parents=True, exist_ok=True)
            (run / f).write_bytes(b"")
        else:
            _still(run / f)
    status, detail = figure_gate.release_check(run, {"narration_script": "Nothing."})
    assert status == "needs_review" and any("no layout record" in d for d in detail)


def test_a_recorded_surface_is_not_held_and_a_run_with_no_surfaces_passes(tmp_path):
    run = tmp_path / "run"
    _still(run / "carousel" / "carousel_slide_01.jpg")
    rec = {"carousel_slide_01.jpg": {"elements": [{"name": "heading", "text": "THE CITY", "box": [0, 0, 1, 1]}]}}
    (run / "carousel" / "still_layout.json").write_text(json.dumps(rec), encoding="utf-8")
    assert figure_gate.release_check(run, {})[0] == "pass"
    _still(run / "carousel" / "carousel_slide_02.jpg")                    # a slide with no record of its own
    status, detail = figure_gate.release_check(run, {})
    assert status == "needs_review" and any("carousel_slide_02.jpg" in d for d in detail)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert figure_gate.release_check(empty, {})[0] == "pass"


# ------------------------------------------------------------------------------------------------- 4. number words
@pytest.mark.parametrize("text, value", [("some two thousand years ago", 2000.0), ("ten thousand people", 10000.0),
                                         ("about 1.2 million tons", 1.2e6)])
def test_spelled_out_scaled_numbers_are_figures(text, value):
    fig = figures.parse(text)[0]
    assert fig.values == (value,)


def test_spelled_out_scaled_numbers_are_judged_but_never_gilded_or_drawn():
    signed = ["Its gears were cut some two thousand years ago."]
    assert figures.truth_faults("SOME 2,000 YEARS AGO", signed) == []
    assert figures.truth_faults("2,000 YEARS AGO", signed)            # the hedge dropped
    assert figures.truth_faults("SOME 3,000 YEARS AGO", signed)       # another number
    assert figures.gold_runs("TWO THOUSAND YEARS AGO") == [("TWO THOUSAND YEARS AGO", False)]
    assert heading.runs(["TWO", "THOUSAND", "YEARS", "AGO"], [], 0)[0] == [("TWO THOUSAND YEARS AGO", heading.ACCENT)]
    assert figures.stat_figure("Its gears were cut some two thousand years ago.") is None


def test_a_spelled_out_caption_figure_is_judged(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    _captions(run, ["Its gears were cut", "some two thousand years ago"])
    narration = "Its gears were cut some two thousand years ago."
    assert figure_gate.check_run(run, {"narration_script": narration})["judged"] == 1
    _captions(run, ["Its gears were cut", "some three thousand years ago"])
    assert figure_gate.check_run(run, {"narration_script": narration})["faults"]


# ------------------------------------------------------------------------------------------------- ruling A: count-up
@pytest.mark.parametrize("value, ok", [("20,000", True), ("UP TO 20,000 PEOPLE", True), ("EST. 144 FT (44 M)", True),
                                       ("5.5 YEARS", True), ("1963", False), ("AD 43", False),
                                       ("9500–9000 BCE", False), ("1404-1438", False), ("94–104 MILLION YEARS AGO", False),
                                       ("2,000 YEARS AGO", False), ("280 FT (85 M) TO 300 FT", False)])
def test_only_a_single_plain_quantity_may_count(value, ok):
    assert figures.counts(value) is ok
    from src import graphics
    if not ok:   # every frame of the count shows the figure whole
        assert all(graphics._shown_value(value, p) == value for p in (0.0, 0.3, 0.7, 1.0))
    else:        # the hedge stays visible; only the number moves
        lead = value[:next(i for i, c in enumerate(value) if c.isdigit())]   # the hedge as written ("EST. ")
        assert all(graphics._shown_value(value, p).startswith(lead) for p in (0.0, 0.5))


@pytest.mark.parametrize("value", ["9500–9000 BCE", "AD 43", "1404–1438", "94–104 MILLION YEARS AGO"])
def test_every_frame_of_a_date_or_span_card_shows_only_the_signed_figure(tmp_path, monkeypatch, value):
    from src import graphics
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 2.0)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    monkeypatch.setattr(graphics, "record_nontext", lambda *a, **k: None)
    buf: list[str] = []
    frames_chars: list[str] = []
    text = graphics._text

    def spy(d, xy, s, *a, **k):
        buf.append(s)
        return text(d, xy, s, *a, **k)
    monkeypatch.setattr(graphics, "_text", spy)

    def encode(frames, out, n, width=None, height=None):
        for _ in frames:
            frames_chars.append("".join(buf))
            buf.clear()
        Path(out).write_bytes(b"")
        return Path(out)
    monkeypatch.setattr(graphics, "_encode", encode)
    graphics.render_stat_cards([{"value": value, "label": "When"}], tmp_path / "n.mp4", width=1080, height=1920)
    parts = graphics.figure_parts(graphics.figure_text(value.upper()))
    whole = parts["num"].replace("–", "").replace("-", "").replace(" ", "")
    drawn = [c for c in frames_chars if c]
    assert frames_chars and all(c == whole for c in drawn), set(drawn)


# ------------------------------------------------------------------------------------------------- ruling B
GOBEKLI = {   # the audit's run 1793034688, its signed copy as synthetic text
    "carousel_slides": [{"heading": "THE FIRST TEMPLES",
                         "body": "Monumental stone enclosures constructed during the Pre-Pottery Neolithic age "
                                 "between 9,600 and 8,200 BCE."}],
    "story_slides": [{"body": "Hunter-gatherers erected monumental stone enclosures in south-eastern Anatolia "
                              "between 9500 and 9000 BCE."}],
    "stat_cards": [{"value": "9500–9000 BCE", "label": "Enclosures raised"}],
    "narration_script": "Hunter-gatherers erected the world's oldest known megaliths between 9500 and 9000 BCE."}


def test_two_signed_values_of_one_quantity_hold_the_run_with_both_named():
    found = figure_gate.contradictions(GOBEKLI)
    assert len(found) == 1
    assert "9,600–8,200 BCE" in found[0] and "9500–9000 BCE" in found[0] and "carousel_slides[1].body" in found[0]


def test_two_different_quantities_are_not_a_contradiction():
    control = {"carousel_slides": [
        {"heading": "BUILT", "body": "The enclosures were raised between 9500 and 9000 BCE."},
        {"heading": "BURIED", "body": "The enclosures were buried between 8200 and 7800 BCE."},
        {"heading": "DEPTH", "body": "The shafts run between 30 and 40 feet deep."},
        {"heading": "WIDTH", "body": "The shafts run between 30 and 45 feet long."}],
        "narration_script": "Hunter-gatherers erected the enclosures between 9500 and 9000 BCE."}
    assert figure_gate.contradictions(control) == []
    assert figure_gate.contradictions({"narration_script": "One span, 9500 to 9000 BCE, stated twice: 9500 to 9000 BCE."}) == []


def test_the_release_report_carries_the_contradiction_check():
    src = (Path(__file__).resolve().parents[1] / "src" / "release.py").read_text(encoding="utf-8")
    assert '"signed_figures_agree"' in src and "contradictions" in src
