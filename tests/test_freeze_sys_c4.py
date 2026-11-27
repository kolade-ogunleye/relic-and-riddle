"""Design-freeze cycle 4, the SYSTEM lane: invariants only (drawn words == the signed words, the engine's line rule
held, the kicker's colour held before it gives way, every SPEC value read or declared), never a pinned measure.

- item 2: one line rule for labels and lines (heading.set_lines / label_ok / label_fault), read by the end card's
  share line, the callouts' names, the scale's figure and referent note and graphics.tracked_wrap;
- item 3: one kicker colour rule (typography.label_ladder colour_held);
- item 4: credit.weight read (a runtime token), every other SPEC value read or declared (SPEC_DECLARED);
- item 5: a count word and its count noun across its modifiers ("three superimposed underground levels").
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, figures, heading

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------- item 5: count word + count noun

@pytest.mark.parametrize("text", [
    "The hypogeum has three superimposed underground levels.",
    "THREE SUPERIMPOSED UNDERGROUND LEVELS",
    "a monument carved across three underground levels",
    "three levels",
])
def test_a_count_word_signs_its_count_noun_across_its_modifiers(text):
    figs = figures.parse(text)
    assert [f.drawn() for f in figs] == ["3 LEVELS"]
    # ruling 8: the number and the noun gold, the modifiers bone
    gold = "".join(t for t, g in figures.gold_runs(text) if g).upper()
    assert "LEVEL" in gold and "SUPERIMPOSED" not in gold and "UNDERGROUND" not in gold


@pytest.mark.parametrize("text", [
    "three of the levels", "two more years", "one fine day", "In 2005 new levels were dug",
    "two massive stone doors", "three very old underground levels",
])
def test_a_count_word_reads_no_figure_through_function_words_digits_or_long_runs(text):
    assert not [f for f in figures.parse(text) if f.unit in ("LEVELS", "YEARS", "DAY")]


def test_the_drawn_count_is_supported_by_the_signed_sentence():
    signed = ["Prehistoric builders carved it across three superimposed underground levels."]
    assert figures.truth_faults("THREE LEVELS", signed) == []
    assert figures.truth_faults("FOUR LEVELS", signed)


# ------------------------------------------------------------------------- item 2: one line rule for labels

def _measure(font_px=30):
    from src.typography import get_font
    f = get_font(brand.TEXT_FONT, font_px, weight=600)
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    return lambda t: d.textlength(t, font=f)


LABELS = ["GROUND TO TORCH TIP", "TEMPLE OF ARTEMIS AT EPHESUS", "ABOUT 280 FT DEEP", "THE NEXT ONE IS WAITING",
          "Send this to a friend who loves ancient engineering, and follow for the next marvel.",
          "Would you descend into an ancient underground necropolis carved entirely by hand?"]


@pytest.mark.parametrize("text", LABELS)
@pytest.mark.parametrize("width", [120, 200, 320, 520, 900])
def test_set_lines_keeps_every_word_and_only_ever_returns_a_valid_fitting_form(text, width):
    m = _measure()
    for n in (1, 2, 3, 4):
        got = heading.set_lines(text, m, width, n)
        if got is None:
            continue
        assert " ".join(got).split() == text.split()            # the words as written, commas kept
        assert len(got) <= n and all(m(t) <= width + 1e-6 for t in got)
        assert heading.label_ok(got, [m(t) for t in got]) and not heading.label_fault(got)


def test_set_lines_takes_the_fewest_rows_and_refuses_rather_than_break_a_rule():
    m = _measure()
    w = m("TO TORCH TIP") + 1
    assert heading.set_lines("GROUND TO TORCH TIP", m, w, 3) == ("GROUND", "TO TORCH TIP")
    # narrower than every valid form: None (the caller steps), never "... / TIP"
    narrow = m("TO TORCH") + 1
    assert heading.set_lines("GROUND TO TORCH TIP", m, narrow, 3) is None
    assert "no-lone-row" in heading.label_fault(["GROUND", "TO TORCH", "TIP"])


def test_end_card_share_line_breaks_by_the_line_rule():
    from src import end_card
    from src.typography import get_font
    font = get_font(brand.TEXT_FONT, 56, weight=end_card.SHARE_WEIGHT)
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for text in LABELS[4:]:
        words = text.split()
        for width in (500, 700, 900):
            got = end_card._balanced(words, font, width, 4)
            if got is None:
                continue
            lines = [" ".join(w) for w in got]
            assert sum(got, []) == words
            assert heading.label_ok(lines, [d.textlength(t, font=font) for t in lines])


def test_callout_names_break_by_the_line_rule():
    from src import callouts
    from src.typography import get_font
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    font = get_font(brand.TEXT_FONT, 40, weight=700)
    for text in ("TEMPLE OF ARTEMIS", "CHURCH OF THE HOLY SEPULCHRE", "GATE OF ALL NATIONS"):
        for max_w in (260, 380, 520):
            for lines in callouts._fitting(d, text, font, 1.6, max_w):
                assert " ".join(lines) == text and heading.label_ok(lines)


def test_tracked_wrap_takes_a_form_without_a_lone_last_row_where_one_fits():
    from src import graphics
    from src.typography import get_font
    f = get_font(brand.TEXT_FONT, 30, weight=600)
    w = graphics._tracked_w("TO TORCH TIP", f, 1.0) + 1
    got = graphics.tracked_wrap("GROUND TO TORCH TIP", f, w, 1.0)
    assert got and heading.label_ok(got) and " ".join(got) == "GROUND TO TORCH TIP"


def test_scale_figure_lines_never_splits_a_hedge_or_a_unit():
    from src import graphics
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for figure in ("ABOUT 280 FT DEEP", "UP TO 1,073 FT DEEP", "305 FT TALL"):
        for width in (220, 300, 420):
            lines, _ = graphics.scale_figure_lines(d, figure, 96, 72, 64, width)
            assert " ".join(lines) == figure
            if len(lines) > 1:
                assert heading._bonded(figure.split()).isdisjoint({len(lines[0].split())})


# ------------------------------------------------------------------------------- item 3: one kicker colour rule

def test_the_kicker_ladder_holds_its_colour_on_the_plate_before_it_gives_way():
    from src.typography import label_ladder
    held = label_ladder((0, 1), colour_held=True)
    # the colour holds on the plate before it gives way to another colour; INK type on a bright ground (the binding
    # ink rule, 2026-11-17) comes before any plate
    assert held == [("halo", 0), ("ink", -1), ("plate", 0), ("halo", 1), ("plate", 1)]
    assert label_ladder((0, 1), ink=False, colour_held=True) == [("halo", 0), ("plate", 0), ("halo", 1), ("plate", 1)]
    assert label_ladder((0, 1)) == [("halo", 0), ("halo", 1), ("ink", -1), ("plate", 0), ("plate", 1)]   # others


@pytest.mark.parametrize("grey", [24, 70, 95, 120, 160, 230])
def test_a_kicker_is_drawn_in_its_colour_wherever_any_backing_holds_it(grey):
    from src import multi_format
    from src.brand_system import colour_of
    from src.typography import get_font, label_passes
    im = Image.new("RGB", (1080, 400), (grey, grey, grey))
    font = get_font(brand.TEXT_FONT, 40, weight=brand_system.tok("kicker.weight"))
    gold = colour_of("kicker.colour")
    plate, rec = multi_format.plan_kicker(im, (100, 200), "THE UNFINISHED OBELISK", font, gold, "ls", track=6)
    member = multi_format.kicker_label(im, (100, 200), "THE UNFINISHED OBELISK", font, gold, "ls", 6, None,
                                       brand_system.tok("contrast.kicker"))
    gold_holds = any(k == 0 for _, k in label_passes(**member))
    assert rec["shown"]
    if gold_holds:   # never bone while gold holds; INK type only where the ink rule takes it (a bright ground)
        assert rec["step"][1] != 1, rec
        if rec["step"][0] != "ink":
            assert tuple(plate.fill[:3]) == tuple(gold) and rec["step"][1] == 0, rec
    assert rec["contrast"] >= brand_system.tok("contrast.kicker") - 1e-9


# ------------------------------------------------------------------------ item 4: tokens read or declared

def _src_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").glob("*.py") if p.name != "brand_system.py")


def test_every_spec_value_is_read_by_a_renderer_or_declared():
    src = _src_text()
    for key in brand_system.SPEC:
        read = re.search(r"[\"']" + re.escape(key) + r"[\"']", src) is not None
        assert read or key in brand_system.SPEC_DECLARED, f"SPEC {key} is neither read nor declared"
        if key in brand_system.SPEC_DECLARED and brand_system.SPEC_DECLARED[key].startswith("not read"):
            assert not read, f"SPEC {key} is declared 'not read' but a renderer names it"
    assert set(brand_system.SPEC_DECLARED) <= set(brand_system.SPEC)
    # freeze cycle 5 (ledger BOARD-05, one source): no value is written that nothing reads; a "declared" entry is a
    # written invariant a test reads (tests/test_freeze_sys_2_spec.py), never a "not read" token
    assert not [k for k, why in brand_system.SPEC_DECLARED.items() if why.startswith("not read")]
    for key in ("badge.weight", "lead.display", "lead.body", "lead.label"):
        assert re.search(r"[\"']" + re.escape(key) + r"[\"']", src), key
    assert not set(brand_system.SPEC) & set(brand_system.SYSTEM_TOKENS)


def test_the_credit_weight_is_read_where_credits_are_drawn():
    src = {n: (ROOT / "src" / f"{n}.py").read_text(encoding="utf-8") for n in ("end_card", "multi_format",
                                                                               "design_cards")}
    for name, text in src.items():
        assert "credit.weight" in text, name
    assert "credit.weight" in brand_system.SYSTEM_TOKENS and "credit.weight" in brand_system.TOKEN_RANGES


# ------------------------------------------------------------------ SYS-E-c4-1: one overshoot curve, eased dissolve

def test_no_module_keeps_its_own_overshoot_curve():
    for p in (ROOT / "src").glob("*.py"):
        if p.name == "brand_system.py":
            continue
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"def\s+_?ease_out_back\s*\(", text), p.name
        assert "PIN_OVERSHOOT" not in text, p.name
    assert "motion.pop_scale" not in brand_system.SPEC and "motion.pop_scale" not in brand_system.SYSTEM_TOKENS


def test_the_pin_settles_and_the_dot_pops_on_the_system_curve():
    from src import graphics
    peak = max(graphics.dot_motion(i / 300 * graphics.DOT_POP_S, 0.0)[0] for i in range(1, 300))
    want = max(brand_system.ease_out_back(i / 1000, brand_system.tok("motion.back_mark")) for i in range(1001))
    assert abs(peak - want) < 0.01
    pose = [graphics.pin_motion(graphics.MAP_PIN_AT + graphics.PIN_FALL_S + k / 1000) for k in range(0, 400)]
    assert all(min(p.sx, p.sy) > 0 for p in pose) and pose[-1].sx == pose[-1].sy == 1.0


@pytest.mark.parametrize("ease", sorted(brand_system.XFADE_EASES))
def test_each_crossfade_ease_is_its_eases_curve(ease):
    weight = brand_system.XFADE_EASES[ease]
    curve = brand_system.EASES[ease]
    for k in range(11):
        p = 1 - k / 10
        got = (1 - p) if weight is None else eval(weight, {"P": p})   # noqa: S307 (a fixed table of arithmetic)
        assert abs(got - curve(k / 10)) < 1e-9
    assert brand_system.xfade_transition(ease).startswith("transition=")
    with pytest.raises(ValueError):
        brand_system.xfade_transition("ease_out_back")


def test_the_act_dissolve_runs_eased_and_monotonic_on_the_bundled_encoder(tmp_path):
    import subprocess

    import numpy as np

    from src import video_composer
    import shutil
    ffmpeg = shutil.which("ffmpeg")   # the encoder the composer runs (PATH): the pinned 8.1 on CI, the bundled one here
    if ffmpeg is None:
        pytest.skip("no ffmpeg on PATH")
    out = tmp_path / "x.raw"
    graph = f"[0][1]xfade={video_composer.act_dissolve_transition()}:duration=0.5:offset=0.25,format=gray"
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=black:s=64x64:r=30:d=1", "-f", "lavfi", "-i", "color=white:s=64x64:r=30:d=1",
                    "-filter_complex", graph, "-f", "rawvideo", "-pix_fmt", "gray", str(out)], check=True, timeout=60)
    luma = [int(f[32, 32]) for f in np.fromfile(out, dtype=np.uint8).reshape(-1, 64, 64)]
    steps = [b - a for a, b in zip(luma, luma[1:])]
    assert all(s >= 0 for s in steps) and luma[0] == 0 and luma[-1] == 255
    moving = [s for s in steps if s > 0]
    assert moving[0] < moving[len(moving) // 2] and moving[-1] < moving[len(moving) // 2]   # eased in and out
    assert max(moving) <= 1.6 * (255 / 15)


# --------------------------------------------- SYS-lane-c-c4-1: one evidence set for a headline figure (release gate)

def test_a_headline_figure_only_a_slide_or_a_story_states_is_refused_by_the_release_gate():
    from src import figure_gate, visual_engine
    run = {"narration_script": "Its door weighed roughly 12 tonnes.", "title": "The 12-Tonne Door",
           "carousel_slides": [{"heading": "THE DOOR", "body": "Its door weighed 12 tonnes."}],
           "story_slides": [{"text": "A 12-tonne door."}]}
    assert any("ROUGHLY" in f for f in figure_gate.headline_faults(run))
    # the release gate reads no field the draw gate calls derived copy
    assert {"carousel_slides", "story_slides"} <= set(figure_gate.DERIVED_COPY_KEYS)
    assert not set(visual_engine.EVIDENCE_KEYS) & set(figure_gate.DERIVED_COPY_KEYS)
    # the body's own words still sign it
    run["narration_script"] = "Its door weighed 12 tonnes."
    assert figure_gate.headline_faults(run) == []


# ------------------------------------------------- SYS-A-c4-1 / -2: the unit's thin space, label rows on the line rule

def test_the_unit_gap_is_a_thin_space_of_the_data_face_cap():
    cap = brand_system.face_metrics(str(brand.DEFAULT_BRAND.family("data")))[0]
    assert 0.3 <= brand_system.tok("data.unit_gap") / cap <= 0.4


def test_ruled_rows_keeps_the_row_count_and_takes_the_line_rule_where_a_form_holds():
    from src import graphics
    from src.typography import get_font
    f = get_font(brand.TEXT_FONT, 30, weight=600)
    w = graphics._tracked_w("IN COMPLETED ROOM", f, 0.0) + 2
    got = graphics.ruled_rows(["AMBER IN COMPLETED", "ROOM"], f, w, 0.0)
    assert len(got) == 2 and " ".join(got) == "AMBER IN COMPLETED ROOM" and heading.label_ok(got)
