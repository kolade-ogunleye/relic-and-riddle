"""Freeze cycle 5, TEXT stream, after the independent review (queue REVIEW-text-c5: D1, D2, D8).

D1: the line policy holds in the path production draws: MultiFormatGenerator._body (the carousel body setter, with
    its character measure), not only in typography.fit_text + even_rag at the full width; even_rag never trades a
    range seam for a number parted from its unit.
D2: the records are real: "line_faults" and "type_fallbacks" are written beside the still (still_layout.json), for
    the lines the still DREW only (no trial fit, no other surface's trace).
D8: a proper name is not parted to avoid a function-word end; no line more than the body needs; a line over the
    character measure is recorded.

The bodies are real run copy (tests/fixtures/text_c5/carousel_bodies.json: text and the line count its slide gives
it; no paths): every carousel body of the development runs that broke a rule at the base or moved in this cycle. The
corpus proof (docs/reviews/freeze-system-2026-11-15/corpus/text_lines.py) runs the same measure over every run.
Invariants, never pinned breaks (three named panel cases excepted: the review's own examples).
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, heading, line_policy, still_layout, typography
from src import multi_format as mf

ROOT = Path(__file__).resolve().parents[1]
BODIES = json.loads((ROOT / "tests" / "fixtures" / "text_c5" / "carousel_bodies.json").read_text(encoding="utf-8"))
GEN = mf.MultiFormatGenerator
MEASURE = GEN.BODY_MEASURE
ASWAN = "Weight calculations range from nearly 1,090 tonnes to 1,168 tonnes."
GOL_GUMBAZ = "Work ceased in 1656 upon Mohammad Adil Shah's death, leaving elements unfinished."


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


def _body(text: str, max_lines: int, cw: int = 1440):
    """The body as a slide of canvas `cw` sets it: the renderer's own call (multi_format._stack)."""
    g = object.__new__(GEN)
    px = GEN._px(cw)
    lines, font, short = g._body(_draw(), px, text, px(1080 - 2 * GEN.MARGIN), max_lines, GEN.BODY_SPEC[0],
                                 GEN.BODY_SPEC[0], trial=True)
    return lines, font, short


def _design_measure():
    font = typography.get_font(brand.TEXT_FONT, GEN.BODY_SPEC[0])
    d = _draw()
    return lambda t: d.textlength(t, font=font)


# --------------------------------------------------------------------------------------------- D1: the real setter
@pytest.mark.parametrize("row", BODIES, ids=[f"{i:02d}" for i in range(len(BODIES))])
def test_the_renderers_body_setter_holds_the_policy_on_real_run_copy(row):
    text, max_lines = row["text"], row["max_lines"]
    lines, _, short = _body(text, max_lines)
    if short:                                   # the slide then gives it one more line (multi_format._stack)
        lines, _, short = _body(text, max_lines + 1)
        max_lines += 1
    assert not short and " ".join(lines).split() == text.split()            # truth: every word, in order
    faults = line_policy.violations(lines)
    # never a number parted from its unit, a line ending on a never word, or a proper name parted
    assert not [f for f in faults if f["fault"] in ("never_end", "name_split")
                or (f["fault"] == "unit_split" and not f.get("seam"))], (lines, faults)
    # and whatever stays (a last-resort end, a range seam, a line over the measure) has no legal alternative
    width = 1080 - 2 * GEN.MARGIN
    assert line_policy.legal_alternative(text, _design_measure(), width, max_lines, lines, MEASURE) is None, lines
    assert all(_design_measure()(ln) <= width for ln in lines)
    # the record names every fault and every line over the measure, with the line as drawn
    record = line_policy.drawn_record(lines, MEASURE)
    assert {f["fault"] for f in faults} <= {r["fault"] for r in record}
    assert [r["text"] for r in record if r["fault"] == "measure_over"] == [ln for ln in lines if len(ln) > MEASURE]


@pytest.mark.parametrize("cw", (1440, 1080))
def test_the_aswan_weight_range_keeps_each_figure_with_its_unit(cw):
    # panel 6's lowest-scored stem of the class (Aswan deck slide 5): "... nearly 1,090 / tonnes to 1,168 tonnes."
    lines, _, _ = _body(ASWAN, 4, cw)
    assert not line_policy.violations(lines), lines
    assert any("1,090 tonnes to 1,168 tonnes." in ln for ln in lines), lines
    # the legal break passes the 34-character measure: recorded, never silent
    over = [r for r in line_policy.drawn_record(lines, MEASURE) if r["fault"] == "measure_over"]
    assert over and all(r["chars"] <= round(MEASURE * line_policy.MEASURE_STRETCH) for r in over), (lines, over)


def test_a_name_is_not_parted_to_avoid_a_function_word_end_and_no_line_is_added():
    # review D8 (Gol Gumbaz slide 6): "upon Mohammad Adil / Shah's death, leaving / elements unfinished." on four lines
    lines, _, _ = _body(GOL_GUMBAZ, 4)
    assert len(lines) == 3 and not line_policy.violations(lines), lines
    assert any("Mohammad Adil Shah's" in ln for ln in lines), lines
    words = GOL_GUMBAZ.split()
    names = line_policy.name_bonds(words)
    assert {words.index("Adil"), words.index("Shah's")} <= names and words.index("ceased") not in names
    assert line_policy.name_bonds("guarding where Amenhotep III was worshipped".split()) == {3}     # a regnal numeral
    # capitals and title case name nothing by shape: headings, kickers and captions are untouched
    assert not line_policy.name_bonds("THE WHISPERING VAULT OF MOHAMMAD ADIL SHAH".split())
    assert not line_policy.name_bonds("The Whispering Vault Of Bijapur".split())
    assert {"fault": "name_split", "line": 0, "word": "Adil | Shah's"} in line_policy.violations(
        ["Work ceased upon Mohammad Adil", "Shah's death, leaving it unfinished."])


def test_even_rag_never_trades_a_range_seam_for_a_number_parted_from_its_unit():
    # review D1: the seam and the number|unit cut cost the same, and the rag chose "nearly 1,090 / tonnes"
    d = _draw()
    font = typography.get_font(brand.TEXT_FONT, 36)
    seam = ["Weight calculations range", "from nearly 1,090 tonnes", "to 1,168 tonnes."]
    got = mf.even_rag(d, seam, font, 912, MEASURE)
    assert not [f for f in line_policy.violations(got) if f["fault"] == "unit_split" and not f.get("seam")], got
    words = " ".join(seam).split()
    unit_cut, seam_cut = words.index("tonnes"), words.index("to")
    assert line_policy.line_cost(words, [unit_cut])[0] == 1 and line_policy.line_cost(words, [seam_cut])[0] == 0
    assert line_policy.line_cost(words, [seam_cut])[3] == 1                  # the seam: a last resort, recorded
    assert line_policy.line_cost(words, [unit_cut]) > line_policy.line_cost(words, [seam_cut])


def test_set_lines_order_and_refusal():
    m = lambda s: 10.0 * len(s)   # noqa: E731
    text = "The obelisk would have weighed 1,090 tonnes when it was finished"
    for width in (200, 260, 300, 360, 420):
        lines = line_policy.set_lines(text, m, width, 8, chars=30)
        assert lines is not None and " ".join(lines).split() == text.split()
        assert not [f for f in line_policy.violations(lines) if f["fault"] == "unit_split"], lines
    assert line_policy.set_lines(text, m, 200, 1) is None                    # it refuses; it never truncates
    # a line over the measure is taken before a figure is parted from its unit, and before a function-word end
    lines = line_policy.set_lines("It weighs from nearly 1,090 tonnes to 1,168 tonnes.", m, 900, 3, chars=34)
    assert not line_policy.violations(lines) and max(map(len, lines)) > 34, lines
    # but never further than the stretch while a last-resort break exists: the range then takes its seam
    lines = line_policy.set_lines("It weighs somewhere between 1,090 tonnes and 1,168 tonnes of granite.", m, 900, 4,
                                  chars=24)
    assert max(map(len, lines)) <= 29, lines                                # 24 x 1.2 (MEASURE_STRETCH)
    assert all(f.get("seam") for f in line_policy.violations(lines)), lines


# ------------------------------------------------------------------------------------------------ D2: the records
def _photo(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / "p.jpg"
    im = Image.new("RGB", (2000, 2500), (120, 110, 100))
    ImageDraw.Draw(im).rectangle([200, 300, 1700, 1100], fill=(200, 190, 170))
    im.save(p, quality=92)
    return p


POLL = {"question": "Quarry or monument?", "options": ["The quarry", "The monument"]}
SHARE = "Send this to a friend who loves granite."


def _slide(tmp_path: Path, name: str, card: dict, archetype: str = "immersive", index: int = 4) -> dict:
    """One real slide drawn and saved with its record, as generate_carousel_sequence saves it; the sidecar entry."""
    gen = GEN()
    photo = _photo(tmp_path / "photos")
    im, lay = gen._render_carousel_slide(archetype, photo, photo, card, index, 7, POLL, SHARE, False)
    path = tmp_path / name
    im.convert("RGB").save(path, quality=90)
    still_layout.save(path, lay, {"renderer": "test"})
    return json.loads((tmp_path / still_layout.SIDECAR).read_text(encoding="utf-8"))[name]


def test_line_faults_are_written_beside_the_still_for_the_lines_it_drew(tmp_path):
    entry = _slide(tmp_path, "s05.jpg", {"heading": "UNMATCHED WEIGHT", "body": ASWAN})
    faults = entry.get("line_faults")
    assert faults, entry.keys()                                              # in still_layout.json, not in memory only
    drawn = next(e["text"] for e in entry["elements"] if e["name"] == "body")
    assert all(f["role"] == "body" and f["text"] in drawn for f in faults), (faults, drawn)
    assert [f["fault"] for f in faults] == ["measure_over"] and faults[0]["text"].endswith("1,168 tonnes.")
    # the heading engine's form (its recorded last resorts with it) is beside the still too, as drawn
    assert " ".join(entry["heading"]["lines"]) == "UNMATCHED WEIGHT" and "conjunction_end" in entry["heading"]
    # every recorded line is a line of the drawn body, once: no trial fit's lines beside them
    lines, _, _ = _body(ASWAN, 4)
    assert sorted({f["text"] for f in faults}) == sorted({ln for ln in lines if len(ln) > MEASURE})


def test_a_clean_slide_has_no_record_and_takes_none_from_another_surface(tmp_path):
    # another surface in this thread leaves a wrap with a violation no break removes (the breaker's trace) ...
    d = _draw()
    typography.fit_text(d, "length between roughly 41.75 metres and 42 metres, with calculations of its weight",
                        brand.TEXT_FONT, 36, 400, 9, 36, quiet=True)
    # ... and the next slide, whose own body is clean, records nothing (review D2: the trace leaked into it)
    entry = _slide(tmp_path, "s02.jpg", {"heading": "CARVED FROM BEDROCK",
                                         "body": "Workers carved it into bedrock, leaving its underside attached."})
    assert "line_faults" not in entry and "type_fallbacks" not in entry, entry.keys()
    json.dumps(entry)                                                        # the whole entry is a plain record
    line_policy.take_notes()


def test_the_trace_is_bounded_and_is_not_the_record():
    line_policy.take_notes()
    for i in range(line_policy.NOTE_LIMIT * 3):
        line_policy.note([f"a {i}", "b"], [{"fault": "never_end", "line": 0, "word": "a"}])
    notes = line_policy.take_notes()
    assert len(notes) == line_policy.NOTE_LIMIT and notes[-1]["lines"][0] == f"a {line_policy.NOTE_LIMIT * 3 - 1}"
    assert line_policy.take_notes() == []


def test_type_fallbacks_and_line_faults_reach_the_sidecar_and_only_when_present(tmp_path):
    lay = still_layout.Layout("carousel", (1440, 1800), (53, 40, 1387, 1760))
    path = tmp_path / "a.jpg"
    assert "type_fallbacks" not in still_layout.save(path, lay, {}) and "line_faults" not in still_layout.load(path)
    lay.meta["type_fallbacks"] = [{"role": "body", "px": 45.33, "design_px": 48.0}]
    lay.meta["line_faults"] = line_policy.drawn_record(["It ends on the", "wrong word."], role="body")
    lay.meta["kickers"] = {"kicker": {"x": 1}}                               # a renderer's other notes stay in memory
    still_layout.save(path, lay, {})
    entry = still_layout.load(path)
    assert entry["type_fallbacks"] == lay.meta["type_fallbacks"] and "kickers" not in entry
    assert [(f["fault"], f["word"], f["text"]) for f in entry["line_faults"]] == [("never_end", "the", "It ends on the")]


def test_a_cover_body_on_its_fallback_tier_is_recorded_once_beside_the_still(tmp_path):
    # ledger TEXT-10 (the IG cover body at 45.3 px against 48): the tier is declared; its use is now on the record
    card = {"heading": "THE UNFINISHED OBELISK OF THE NORTHERN QUARRY",
            "body": "The largest obelisk ever attempted by ancient Egypt still sits trapped in Aswan granite."}
    entry = _slide(tmp_path, "s01.jpg", card, archetype="cover", index=0)
    body_px = GEN._px(1440).exact(GEN.BODY_SPEC[0])
    rows = entry.get("type_fallbacks") or []
    assert len(rows) <= 1
    if rows:                                                                 # the stack took the tier
        assert rows[0]["role"] == "body" and rows[0]["px"] < rows[0]["design_px"] == round(body_px, 2)
        assert rows[0]["px"] == round(GEN._px(1440).exact(GEN.BODY_SPEC[1]), 2)


# --------------------------------------------------------------- the guards the review could not revert-test
def test_a_heading_holds_the_policy_bonds_and_falls_back_recorded_never_refused():
    measure = lambda t, size: len(t) * size * 0.5   # noqa: E731
    # the policy's bonds (a date, an era, a multi-word hedge) hold in a heading as in body copy
    for text, pair in (("BUILT IN AUGUST 1944 BY HAND", ("AUGUST", "1944")), ("STANDING SINCE 1350 BC IN THEBES",
                       ("1350", "BC")), ("MORE THAN 590 SQUARE FEET OF AMBER", ("THAN", "590"))):
        words = text.split()
        assert words.index(pair[1]) not in heading.break_points(words), text
        form = heading.set_heading(text, measure, 16 * 100 * 0.5, (100, 90), max_lines=3)
        assert form is not None and any(f"{pair[0]} {pair[1]}" in ln for ln in form.lines), (text, form.lines)
    # where no form holds every policy bond the heading sets on the round-7 bonds, RECORDED, never newly refused: the
    # real cover lockup of a development run at its locked size (display_xl, the Reel cover's measure)
    d = _draw()
    anton = lambda t, size: d.textlength(t, font=typography.get_font(brand.DISPLAY_FONT, size))   # noqa: E731
    text = "ESTIMATED 6 TONNES GONE IN 36 HOURS"
    xl = brand_system.type_px("display_xl", "tall")
    form = heading.set_heading(text, anton, 1080 - 2 * 84, (xl,), max_lines=2, lockup=True, locked_lines=3)
    assert form is not None and form.record.get("keep_together_last_resort") is True, getattr(form, "lines", None)
    assert " ".join(form.lines).split() == text.split() and form.size == xl
    assert any("6 TONNES" in ln for ln in form.lines) and any("36 HOURS" in ln for ln in form.lines)   # round-7 bonds
    assert heading._set_heading_ruled(text, anton, 1080 - 2 * 84, (xl,), max_lines=2, lockup=True,
                                      locked_lines=3) is None                # the policy's bonds alone refuse it
    easy = heading.set_heading("THE UNFINISHED OBELISK", anton, 1080 - 2 * 84, (xl,), max_lines=2, lockup=True,
                               locked_lines=3)
    assert easy is not None and not easy.record.get("keep_together_last_resort"), easy.lines


def test_the_board_specimen_fitter_never_drops_a_word_inside_a_sentence():
    sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-system-2026-11-15"))
    try:
        board = importlib.import_module("board")
    finally:
        sys.path.pop(0)
    font = typography.get_font(brand.TEXT_FONT, 36)
    lines = ["A concentric formation in Mauritania that has", "long fascinated observers from orbit."]
    text = " ".join(lines)
    wide = max(font.getlength(ln) for ln in lines)
    assert board.fit_sample_lines(lines, font, 0.0, wide + 1) == lines       # lines that fit are drawn as they are
    for room in (wide * 0.95, wide * 0.8, wide * 0.6, wide * 0.4):
        got = board.fit_sample_lines(lines, font, 0.0, room)
        assert len(got) <= len(lines) and all(font.getlength(ln) <= room + 0.5 for ln in got), (room, got)
        assert line_policy.ellipsis_drop(" ".join(got), text) == [], (room, got)
        kept = " ".join(got).replace("…", "").replace("...", "").split()
        assert kept == text.split()[:len(kept)] or kept[:-1] == text.split()[:len(kept) - 1], (room, got)
