"""The 9:16 timeline hierarchy (2026-11-03 implementation spec): the Anton years the hero at 3.0 x the labels' cap
(2.5 x only logged), the labels Montserrat 700 at 34 px in their row budget, balanced and with no row but the last
ending on a short word, 26 px spans with their own air, one left ink edge for the year group, every label row and
every span, a "c." at half the year's size, the box filled from its top with spare height under the column, and
timeline_copy_fit for upstream callers. The fuzz sets and the property checks are round 4's
(tests/test_r4_graphics_2026_11_02.py), run here at 1080x1920 with this spec's rules on top."""
import itertools
from typing import Any

import pytest

import test_r4_graphics_2026_11_02 as r4
from src import graphics, safe_zones

TALL = (1080, 1920)
E = lambda y, l, a=False: {"year": y, "label": l, "approximate": a}  # noqa: E731
# the comparison renders' sets (scratchpad ba70/r.py)
POMPEII = [E(-600, "Oscans found a town on the Sarnus", True), E(62, "Earthquake damages the forum temples"),
           E(79, "Vesuvius buries the city in ash"), E(1748, "Bourbon engineers begin the excavations")]
BABYLON = [E(-2100, "Ziggurat of the moon god raised", True), E(-605, "Nebuchadnezzar rebuilds the walls"),
           E(-323, "Alexander dies here"), E(1899, "German excavations begin at the Ishtar gate")]
# tight leading alone fits it: three 3-row labels, one span, at 120 px years (1.15 needs 12 px more than the box)
TIGHT_ONLY = [E(1500, "Masons raise the outer walls of the new fortress above the harbour town"),
              E(1501, "Builders finish the great hall and the chapel inside the inner ward"),
              E(1700, "Engineers add the artillery bastions along the landward front of the walls")]
SPARSE3 = [E(802, "Angkor founded", True), E(1150, "Wat completed"), E(1431, "Capital sacked")]
OVER_BUDGET = [E(-700, "Possibly Phrygian tunnels", True),
               E(780, "Expanded against raiders from the south and the east by the Byzantine garrison", True),
               E(1923, "Abandoned"), E(1963, "Rediscovered behind a wall")]
REAL = {"pompeii": POMPEII, "babylon": BABYLON}
SETS: dict[str, list[dict[str, Any]]] = {
    **{f"real/{k}": v for k, v in REAL.items()},
    **{f"named/{k}": v for k, v in r4.NAMED_TL.items()},
    **{f"fz/{k}": v for k, v in r4.FUZZ_TL.items()},
    **{f"{fam}/{k}": v for fam, sets in r4.FUZZ_FAMILIES.items() for k, v in sets.items()},
    "own/tight_only": TIGHT_ONLY, "own/sparse3": SPARSE3, "own/over_budget": OVER_BUDGET,
}
XT = graphics.inset_x0(*TALL) + 16 + 56          # the column's left ink edge (the spine's dots' left edge + 56)


def _render(monkeypatch, caplog, tmp_path, events) -> dict[str, Any]:
    """A 9:16 render with every round-4 property check (r4._render_checked), its plates grouped by entry."""
    got = r4._render_checked(monkeypatch, caplog, tmp_path, events, TALL)
    plates = got["plates"]
    kept = graphics.timeline_events(events)
    ent: list[dict[str, Any]] = [{"labels": [], "year": None, "circa": None, "span": None} for _ in kept]
    for n, p in plates:
        if n.endswith("_circa"):
            ent[int(n[4:-6])]["circa"] = p
        elif n.startswith("year"):
            ent[int(n[4:])]["year"] = p
        elif n.startswith("label"):
            ent[int(n[5:])]["labels"].append(p)
        elif n.startswith("gap"):
            ent[int(n[3:])]["span"] = p
    for en in ent:
        en["labels"].sort(key=lambda p: p.xy[1])
    return {**got, "entries": ent, "kept": kept, "log": caplog.text,
            "warned": [r for r in caplog.records if r.levelno >= 30 and r.name.endswith("graphics")]}


def _cap(p) -> int:
    return graphics.cap_height(p.font)


def _weight_is(font, weight: int) -> bool:
    ref = graphics.get_font("Montserrat", font.size, weight=weight)
    return all(abs(font.getlength(t) - ref.getlength(t)) < 0.01 for t in ("HAMBURGEFONTSIV", "1923"))


def _year_group(en) -> list[Any]:
    return [p for p in (en["circa"], en["year"]) if p is not None]


def _fallback(st, span) -> bool:
    return f"Timeline span {span.text!r} set at" in st["log"]


def _ink(p) -> int:
    return int((p.glyphs > 127).sum())


def _start_px(count: int) -> int:
    return 128 if count >= 4 else 150 if count == 3 else 190      # round 6 (deliberate): 104 / 120 / 180 before


# ---------------------------------------------------------------------------------------------- E1 and D
@pytest.mark.parametrize("name", sorted(REAL))
def test_pompeii_and_babylon_render_whole_with_no_warning(tmp_path, monkeypatch, caplog, name) -> None:
    st = _render(monkeypatch, caplog, tmp_path, REAL[name])
    # round 7 (deliberate, coordinator R3): the spans back in their own rows cost the column its room; the only
    # warnings are the give-way the spec orders (the spans' air to its 1.5 x minimum, the years to the least ratio)
    allowed = ("Timeline spans set without their full separation", "Timeline: the years set under 4.5")
    assert all(r.getMessage().startswith(allowed) for r in st["warned"]), [r.getMessage() for r in st["warned"]]
    years = [en["year"] for en in st["entries"]]
    labels = [p for en in st["entries"] for p in en["labels"]]
    spans = [en["span"] for en in st["entries"] if en["span"] is not None]
    # round 6 (deliberate): the years about Anton 112 (cap about 96), 84 (80-88) before
    # round 9 (deliberate, review 8): the spans' least air over the next year 14 px of ink (12 before) costs the
    # years one 3 % step, 100 -> 97
    assert len({p.font.size for p in years}) == 1 and years[0].font.size >= 96
    assert years[0].font.size <= 128
    assert min(_cap(p) for p in years) >= graphics.TALL_YEAR_RATIO_MIN * max(_cap(p) for p in labels)   # round 7
    # round 6 (deliberate): 30 px / 600 (34 / 700 before), through the constants
    assert {p.font.size for p in labels} == {graphics.TALL_LABEL_MIN_PX}
    assert all(_weight_is(p.font, graphics.TALL_LABEL_WEIGHT) for p in labels)
    assert all(p.fill == graphics.TALL_LABEL_FILL for p in labels)
    # round 7 (deliberate): the spans in their band at span_font_px(30) = 24 px (26 at 34 px labels before)
    assert len(spans) == 3 and {p.font.size for p in spans} == {graphics.span_font_px(graphics.TALL_LABEL_MIN_PX)}
    for en, e in zip(st["entries"], REAL[name]):                                       # whole, nothing cut
        assert " ".join(p.text for p in en["labels"]) == e["label"].upper()
    if name == "pompeii":
        assert [len(en["labels"]) for en in st["entries"]] == [2, 2, 1, 2]


# ---------------------------------------------------------------------------------------------- the fuzz rules
@pytest.mark.parametrize("case", sorted(SETS))
def test_every_set_keeps_the_hierarchy_rules(tmp_path, monkeypatch, caplog, case) -> None:
    events = SETS[case]
    st = _render(monkeypatch, caplog, tmp_path, events)
    ent, kept, log_text = st["entries"], st["kept"], st["log"]
    years = [en["year"] for en in ent]
    labels = [p for en in ent for p in en["labels"]]
    year_cap, label_cap = min(_cap(p) for p in years), max(_cap(p) for p in labels)
    # E2: the years 3.0 x the labels' cap, or 2.5 x with "years set under" logged
    floor = graphics.TALL_YEAR_RATIO if "years set under" not in log_text else graphics.TALL_YEAR_RATIO_MIN
    if "the last resort" in log_text or "set at the tightest" in log_text:
        floor = graphics.TALL_YEAR_RATIO_LAST          # round 7 (deliberate, R3): the spans' rows' last resort
    assert year_cap >= floor * label_cap, (case, year_cap, label_cap)
    assert min(p.font.size for p in labels) >= graphics.TALL_LABEL_MIN_PX
    assert all(_weight_is(p.font, graphics.TALL_LABEL_WEIGHT) for p in labels)
    many = len(kept) > 2
    if many:
        assert {p.font.size for p in labels} == {graphics.TALL_LABEL_MIN_PX}, case     # 30 fixed at 3-4 entries
    # E3: the spans a tier under the labels
    lab_min = min(p.font.size for p in labels)
    for en in ent:
        if en["span"] is not None:
            # round 7 (deliberate): the labels fell to 30 px (spec F) while the span keeps its 24 px floor
            # (SPAN_MIN_PX), so a span is 6 px and a weight (500 to 600) under them, not 8 px (0.80 x the cap before)
            assert _cap(en["span"]) < label_cap, (case, en["span"].text)
            assert en["span"].font.size <= lab_min - 6, (case, en["span"].text)
    # E5: one left ink edge (a fallback span set beside a plate is excused)
    lefts = [_year_group(en)[0].box[0] for en in ent] + [p.box[0] for p in labels]
    lefts += [en["span"].box[0] for en in ent if en["span"] is not None and not _fallback(st, en["span"])]
    assert max(abs(x - XT) for x in lefts) <= 1, (case, sorted(set(lefts)))
    # E6: no row but the last ends on a short word unless logged; the words are the source's (whole labels)
    for en, e in zip(ent, kept):
        text = graphics.drawable(e["label"].upper(), "Montserrat")
        rows = [p.text for p in en["labels"]]
        if not any("…" in r for r in rows):
            assert " ".join(rows) == " ".join(text.split()), (case, rows)
        ends = [r.split()[-1].strip(",;:.") for r in rows[:-1]]
        if any(w in graphics.TALL_STOP_WORDS for w in ends):
            assert f"Timeline label {text!r}: no break" in log_text, (case, rows)
    # E7: the air inside an entry
    pitches = set()
    for en in ent:
        first = en["labels"][0]
        layout_air = first.xy[1] - _cap(first) - en["year"].xy[1]
        if many:
            assert round(layout_air) == graphics.TALL_YEAR_LABEL_AIR, (case, layout_air)
            ink_air = first.box[1] - max(p.box[3] for p in _year_group(en))
            assert abs(ink_air - graphics.TALL_YEAR_LABEL_AIR) <= 2, (case, ink_air)
        pitches |= {round(b.xy[1] - a.xy[1]) for a, b in itertools.pairwise(en["labels"])}
    allowed = {round(lab_min * graphics.TALL_LEADING), round(lab_min * graphics.TALL_TIGHT_LEADING)}
    assert pitches <= allowed and len(pitches) <= 1, (case, pitches)                   # one leading a column
    if many:
        assert allowed == {34, 32}                       # round 6 (deliberate): 30 px x 1.12 / 1.08 (39 / 37 before)

    # E8: the gap between entries and the spans' air, by ink
    def inner(en) -> list[int]:
        rows = en["labels"]
        return ([rows[0].box[1] - max(p.box[3] for p in _year_group(en))]
                + [b.box[1] - a.box[3] for a, b in itertools.pairwise(rows)])
    for k in range(1, len(ent)):
        above_ink = max(p.box[3] for p in ent[k - 1]["labels"])
        next_top = min(p.box[1] for p in _year_group(ent[k]))
        biggest = max(inner(ent[k - 1]) + inner(ent[k]))
        assert next_top - above_ink >= graphics.TALL_ENTRY_SEPARATION * biggest, (case, k, next_top - above_ink,
                                                                                   biggest)
        sp = ent[k]["span"]
        if sp is not None and not _fallback(st, sp):
            # in layout px, as spec B3 gives them: the label's last baseline (and its ink under it) to the span's
            # cap top, and the span's baseline (and its comma) to the later year's cap top
            last = ent[k - 1]["labels"][-1]
            label_foot = last.xy[1] + graphics.span_descent(last.text, last.font)
            year = ent[k]["year"]
            up = round(sp.xy[1] - _cap(sp) - label_foot)
            down = round(year.xy[1] - _cap(year) - (sp.xy[1] + graphics.span_descent(sp.text, sp.font)))
            assert up >= 23 and down >= graphics.SPAN_AIR_BELOW_MIN and up >= 1.5 * down, (case, k, up, down)
        if sp is None:
            assert next_top - above_ink >= graphics.TALL_NO_SPAN_AIR - 2, (case, k)  # 32 px, ink +-1 each edge
    # E9: a label within its budget is never cut and never over it
    budget = graphics.tall_label_rows_budget(len(kept))
    for en, e in zip(ent, kept):
        text = graphics.drawable(" ".join(e["label"].upper().split()), "Montserrat")
        # round 6: judged by the render's own fit (tall_label_rows_needed), as timeline_copy_fit judges it
        need = graphics.tall_label_rows_needed(text, graphics.tall_label_measure(*TALL) - graphics.TALL_INK_RESERVE)
        if need is None or need > budget:
            assert "copy-fit" in log_text, case
            continue
        assert len(en["labels"]) <= budget, (case, [p.text for p in en["labels"]])
        assert not any("…" in p.text for p in en["labels"]), (case, [p.text for p in en["labels"]])
    # E11: the circa mark
    for en in ent:
        if en["circa"] is not None:
            c, y = en["circa"], en["year"]
            assert c.text == "c." and _weight_is(c.font, 600)
            assert c.font.size == max(graphics.CIRCA_MIN_PX, round(0.5 * y.font.size))
            assert c.xy[1] == y.xy[1] and c.box[2] < y.box[0]
    # E12: the box is filled from its top; the foot: inside it, and on it unless spare height is left under
    box = safe_zones.graphics_box(*TALL)
    assert abs(years[0].xy[1] - _cap(years[0]) - (box[1] + graphics.TALL_TOP_PAD)) <= 1, case
    bottom = max(p.box[3] for _n, p in st["plates"])
    assert bottom <= box[3], case
    gaps = [min(p.box[1] for p in _year_group(ent[k])) - max(p.box[3] for p in ent[k - 1]["labels"])
            for k in range(1, len(ent))]
    # round 6 review (deliberate, spec F give-way 5): a third label row only with the years at the least ratio, so
    # such a column may leave spare height under it with its years below their starting size
    third = many and max(len(en["labels"]) for en in ent) >= 3 and "copy-fit" not in log_text
    if third:
        assert min(_cap(p) for p in years) < graphics.TALL_YEAR_RATIO * label_cap + 1, case
    if bottom < box[3] - 3 and not third:
        assert years[0].font.size == _start_px(len(kept)), (case, bottom)
        assert all(g >= graphics.TALL_GAP_CAP * year_cap - 3 for g in gaps), (case, gaps)


# ---------------------------------------------------------------------------------------------- E4
@pytest.mark.parametrize("events", [POMPEII, BABYLON, r4.NAMED_TL["four_d"]], ids=["pompeii", "babylon", "four_d"])
def test_the_years_carry_as_much_ink_as_the_labels(tmp_path, monkeypatch, caplog, events) -> None:
    st = _render(monkeypatch, caplog, tmp_path, events)
    year_ink = sum(_ink(p) for en in st["entries"] for p in _year_group(en))
    label_ink = sum(_ink(p) for en in st["entries"] for p in en["labels"])
    assert year_ink >= 0.8 * label_ink, (year_ink, label_ink)


# ---------------------------------------------------------------------------------------------- E9 (over budget)
def test_a_label_over_its_budget_logs_copy_fit_and_is_drawn_whole(tmp_path, monkeypatch, caplog) -> None:
    st = _render(monkeypatch, caplog, tmp_path, OVER_BUDGET)
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR" and "copy-fit" in r.getMessage()]
    assert len(errors) == 1 and "budget 2" in errors[0], errors
    rows = [p.text for p in st["entries"][1]["labels"]]
    assert len(rows) == 3 and " ".join(rows) == OVER_BUDGET[1]["label"].upper()
    assert all(len(en["labels"]) <= 2 for k, en in enumerate(st["entries"]) if k != 1)


# ---------------------------------------------------------------------------------------------- E10
def test_a_third_label_row_comes_only_once_the_years_reach_the_least_ratio(tmp_path, monkeypatch, caplog) -> None:
    # round 6 review (deliberate; was test_the_tight_leading_is_taken_before_any_year_step, [3, 3, 3] at 120 px
    # years): spec F's give-way order takes a third label row only after the years step down to
    # TALL_YEAR_RATIO_MIN; three labels that need three rows at 30 px set the years at that floor (logged)
    st = _render(monkeypatch, caplog, tmp_path, TIGHT_ONLY)
    years = [en["year"] for en in st["entries"]]
    labels = [p for en in st["entries"] for p in en["labels"]]
    assert max(len(en["labels"]) for en in st["entries"]) == 3
    ratio = min(_cap(p) for p in years) / max(_cap(p) for p in labels)
    assert graphics.TALL_YEAR_RATIO_MIN <= ratio < graphics.TALL_YEAR_RATIO, ratio
    assert {p.font.size for p in years} != {_start_px(3)} and "Timeline years at" in caplog.text
    pitches = {round(b.xy[1] - a.xy[1]) for en in st["entries"] for a, b in itertools.pairwise(en["labels"])}
    assert pitches <= {round(graphics.TALL_LABEL_MIN_PX * graphics.TALL_LEADING),
                       round(graphics.TALL_LABEL_MIN_PX * graphics.TALL_TIGHT_LEADING)}


def test_two_row_labels_keep_the_years_at_least_at_the_least_ratio(tmp_path, monkeypatch, caplog) -> None:
    # round 6 review: labels that hold on two rows never cost the years their full ratio; round 7 (deliberate, R3:
    # the spans' own rows), never under the least ratio, and only where the full one does not hold (logged)
    st = _render(monkeypatch, caplog, tmp_path, POMPEII)
    years = [en["year"] for en in st["entries"]]
    labels = [p for en in st["entries"] for p in en["labels"]]
    assert all(len(en["labels"]) <= 2 for en in st["entries"])
    ratio = min(_cap(p) for p in years) / max(_cap(p) for p in labels)
    assert ratio >= graphics.TALL_YEAR_RATIO or (ratio >= graphics.TALL_YEAR_RATIO_MIN
                                                and "years set under" in caplog.text), ratio


# ---------------------------------------------------------------------------------------------- E11 / helpers
def test_circa_font_is_half_the_year_with_its_own_floor() -> None:
    assert graphics.circa_font(graphics.get_font("Anton", 84)).size == 42
    assert graphics.circa_font(graphics.get_font("Anton", 40)).size == graphics.CIRCA_MIN_PX == 30
    assert graphics.CIRCA_MIN_PX > graphics.SPAN_MIN_PX == 24


def test_span_font_px_and_the_air_rules() -> None:
    assert [graphics.span_font_px(px) for px in (34, 38, 42, 20)] == [26, 29, 32, 24]
    f = graphics.get_font("Montserrat", 26, weight=600)
    above, below = graphics.span_air_rule(34, f, 15, True)
    assert (above, below) == (30, 16)                                                 # nominal: 2 x 15, the lean
    above, below = graphics.span_air_rule(34, f, 14, False)
    assert below == graphics.SPAN_AIR_BELOW_MIN + graphics.SPAN_OVERSHOOT and above == 23 and above >= 1.5 * below
    assert graphics.TALL_YEAR_STEPS[0] == 1.0 and graphics.TALL_YEAR_STEPS[-1] == 0.6
    assert all(round(a - b, 2) == 0.04 for a, b in itertools.pairwise(graphics.TALL_YEAR_STEPS))
    assert [graphics.tall_label_rows_budget(n) for n in (2, 3, 4)] == [graphics.TALL_LABEL_ROWS, 3, 2]


def test_balanced_rows_avoid_a_short_word_at_a_rows_end_and_keep_the_words() -> None:
    font = graphics.get_font("Montserrat", 34, weight=700)
    width = graphics.tall_label_measure(*TALL)
    text = "OSCANS FOUND A TOWN ON THE SARNUS"
    lines = graphics.tracked_wrap(text, font, width, 2)
    rows, obeyed = graphics.balanced_label_rows(lines, font, width, 2)
    assert obeyed and len(rows) == len(lines) == 2 and " ".join(rows) == text
    assert all(r.split()[-1] not in graphics.TALL_STOP_WORDS for r in rows[:-1])
    assert all(graphics.tracked_width(r, font, 2) <= width for r in rows)
    # failure mode 1: one long word and short words around it, no break avoids the rule: balanced, not cut
    word = "CONSTANTINOPLE-HALICARNASSUS-MAUSOLEUM"
    narrow = graphics.tracked_width(word, font, 2) + 1
    rows, obeyed = graphics.balanced_label_rows(["THE", word, "OF A"], font, narrow, 2)
    assert " ".join(rows) == f"THE {word} OF A" and len(rows) == 3 and not obeyed


def test_capped_spacing_keeps_each_gap_under_its_cap_and_leaves_the_rest_below() -> None:
    events = [{"year": 0 + 1}, {"year": 400}, {"year": 900}]
    sp = graphics.TimelineSpacing([0.0, 400.0, 1000.0], (), 1.0)
    got = graphics.capped_spacing(sp, events, [300.0, 300.0])
    steps = [b - a for a, b in itertools.pairwise(got.positions)]
    assert steps == [300.0, 300.0] and got.positions[-1] < sp.positions[-1]        # the rest under the column
    got = graphics.capped_spacing(sp, events, [500.0, 450.0])                        # one capped, its excess shared
    steps = [b - a for a, b in itertools.pairwise(got.positions)]
    assert steps[1] == 450.0 and steps[0] == pytest.approx(500.0) and got.breaks == (2,)
    assert graphics.capped_spacing(sp, events, [900.0, 900.0]) is sp                 # nothing capped: unchanged


# ---------------------------------------------------------------------------------------------- E12
def test_a_sparse_three_entry_column_never_stretches_a_gap_past_its_cap(tmp_path, monkeypatch, caplog) -> None:
    # round 6 (deliberate; was ..._leaves_its_spare_height_below): the 150 px years fill the column to its foot
    # (at 120 px it left spare height under it); no gap between entries grows past TALL_GAP_CAP x the year cap
    st = _render(monkeypatch, caplog, tmp_path, SPARSE3)
    ent = st["entries"]
    box = safe_zones.graphics_box(*TALL)
    years = [en["year"] for en in ent]
    assert {p.font.size for p in years} == {_start_px(3)}                             # round 6: 150 (120)
    cap = _cap(years[0])
    bottom = max(p.box[3] for _n, p in st["plates"])
    assert bottom <= box[3]                                                           # no stretching past it
    for k in range(1, len(ent)):
        gap = min(p.box[1] for p in _year_group(ent[k])) - max(p.box[3] for p in ent[k - 1]["labels"])
        assert gap <= graphics.TALL_GAP_CAP * cap + 2, (k, gap, cap)
        if bottom < box[3] - 3:                                                       # spare height: each at its cap
            assert gap >= graphics.TALL_GAP_CAP * cap - 3, (k, gap, cap)


# ---------------------------------------------------------------------------------------------- E13
def test_timeline_copy_fit_flags_over_budget_labels_and_too_many_events() -> None:
    assert graphics.timeline_copy_fit(POMPEII) == []
    assert graphics.timeline_copy_fit(BABYLON) == []
    got = graphics.timeline_copy_fit(OVER_BUDGET)
    assert len(got) == 1 and "needs 3 rows" in got[0] and "budget 2" in got[0]
    five = POMPEII + [E(1997, "Listed as a world heritage site")]
    got = graphics.timeline_copy_fit(five)
    assert any("5 events" in g for g in got)
    three = [OVER_BUDGET[1], POMPEII[1], POMPEII[2]]                                  # 3 entries: 3 rows hold it
    assert graphics.timeline_copy_fit(three) == []


# ---------------------------------------------------------------------------------------------- review 15
EASTER = [E(1200, "Polynesian voyagers reach Rapa Nui", True), E(1500, "Moai carving at Rano Raraku peaks", True),
          E(1722, "Jacob Roggeveen lands on Easter Sunday"), E(1995, "UNESCO lists Rapa Nui National Park")]
COLOSSEUM = [E(72, "Vespasian begins the amphitheatre"), E(80, "Titus opens it with a hundred days of games"),
             E(1349, "An earthquake brings down the south wall"), E(2000, "Restoration of the hypogeum begins")]


@pytest.mark.parametrize("source", ["UNESCO lists Rapa Nui National Park", "Moai carving at Rano Raraku peaks",
                                    "Titus opens it with a hundred days of games"])
def test_balanced_rows_keep_a_name_or_a_count_whole_where_a_break_allows(source) -> None:
    font = graphics.get_font("Montserrat", 34, weight=700)
    width = graphics.tall_label_measure(*TALL)
    text = graphics.drawable(source.upper(), "Montserrat")
    lines = graphics.tracked_wrap(text, font, width, 2)
    rows, obeyed = graphics.balanced_label_rows(lines, font, width, 2, source=source)
    assert obeyed and len(rows) == len(lines) == 2 and " ".join(rows) == text
    assert all(graphics.tracked_width(r, font, 2) <= width for r in rows)
    ends = {r.split()[-1] + " " + n.split()[0] for r, n in itertools.pairwise(rows)}
    assert not ends & {"RAPA NUI", "NUI NATIONAL", "NATIONAL PARK", "RANO RARAKU", "HUNDRED DAYS"}, rows
    # without the writer's case (or set in capitals) only the count is held: the break stays balanced
    plain, _ = graphics.balanced_label_rows(lines, font, width, 2)
    assert " ".join(plain) == text
    assert graphics.balanced_label_rows(lines, font, width, 2, source=source.upper())[0] == plain


def test_a_held_name_gives_way_where_no_other_break_fits() -> None:
    font = graphics.get_font("Montserrat", 34, weight=700)
    words = ["Jacob", "Roggeveen"]
    narrow = max(graphics.tracked_width(w.upper(), font, 2) for w in words) + 1
    rows, obeyed = graphics.balanced_label_rows(["JACOB", "ROGGEVEEN"], font, narrow, 2, source="Jacob Roggeveen")
    assert rows == ["JACOB", "ROGGEVEEN"] and obeyed


def test_the_rendered_rows_keep_names_whole(tmp_path, monkeypatch, caplog) -> None:
    for events in (EASTER, COLOSSEUM):
        st = _render(monkeypatch, caplog, tmp_path, events)
        for en, e in zip(st["entries"], st["kept"]):
            rows = [p.text for p in en["labels"]]
            assert " ".join(rows) == graphics.drawable(e["label"].upper(), "Montserrat")
            pairs = {a.split()[-1] + " " + b.split()[0] for a, b in itertools.pairwise(rows)}
            assert not pairs & {"RAPA NUI", "NUI NATIONAL", "RANO RARAKU", "JACOB ROGGEVEEN", "HUNDRED DAYS",
                                "EASTER SUNDAY"}, rows


def test_copy_fit_judges_a_label_as_the_render_fits_it() -> None:
    # round 6 (deliberate): 30 px / 600 at 0.05 em on the measure less TALL_INK_RESERVE (34 / 700 / +2 before)
    width = graphics.tall_label_measure(*TALL) - graphics.TALL_INK_RESERVE
    px, wt, tr = graphics.TALL_LABEL_MIN_PX, graphics.TALL_LABEL_WEIGHT, graphics.tall_track(graphics.TALL_LABEL_MIN_PX)
    for events in (POMPEII, BABYLON, OVER_BUDGET, EASTER, COLOSSEUM,
                   *r4.NAMED_TL.values(), *r4.FUZZ_TL.values()):
        for e in events:
            text = graphics.drawable(" ".join(e["label"].upper().split()), "Montserrat")
            got = graphics.tall_label_rows_needed(text, width)
            font = graphics.get_font("Montserrat", px, weight=wt)
            lines = graphics.fit_tracked(None, text, "Montserrat", px, width, got or 6, px, tr,
                                         sentences=(got or 6) > 1, weight=wt, quiet=True)[0]
            if got is not None:
                assert len(lines) == got and " ".join(lines) == text
                assert got == 1 or graphics.tracked_wrap(text, font, width, tr, lines=got - 1) is None
    # a sentence break in the label: the render's fit, sentences whole, and the copy-fit agree
    two = [E(1100, "Walls rise. Gates follow."), E(1200, "Abandoned"), E(1300, "Found"), E(1400, "Listed")]
    assert graphics.timeline_copy_fit(two) == []
    assert graphics.tall_label_rows_needed("WALLS RISE. GATES FOLLOW.", width) == 1
