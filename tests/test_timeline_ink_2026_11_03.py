"""Round 6 (2026-11-03): the 9:16 timeline's years outweigh their labels (cap ratio, ink, brightness) with the spans
(round 7, R3) back in their own rows; the 16:9 spans under the axis on one row, the labels in 420 px columns."""
import logging
import re

import pytest

from src import brand, graphics
from src.typography import rel_luminance
from tests.test_r4_graphics_2026_11_02 import _render

E = lambda y, l, a=False: {"year": y, "label": l, "approximate": a}
POMPEII = [E(-600, "Oscans found a town on the Sarnus", True), E(62, "Earthquake damages the forum temples"),
           E(79, "Vesuvius buries the city in ash"), E(1748, "Bourbon engineers begin the excavations")]
BABYLON = [E(-2100, "Ziggurat of the moon god raised", True), E(-605, "Nebuchadnezzar rebuilds the walls"),
           E(-323, "Alexander dies here"), E(1899, "German excavations begin at the Ishtar gate")]
SPAN = re.compile(r"^(ABOUT )?\d{1,3}(,\d{3})* YEARS LATER$")   # round 7 (deliberate, R3): the full form


def _ink(p) -> int:
    return int((p.glyphs > 127).sum())


@pytest.mark.parametrize("events", [POMPEII, BABYLON], ids=["pompeii", "babylon"])
def test_tall_years_outweigh_their_labels(tmp_path, monkeypatch, caplog, events) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4"), fps=2)
    years = [p for n, p in st["plates"] if re.fullmatch(r"year\d", n)]
    labels = [p for n, p in st["plates"] if n.startswith("label")]
    ratio = graphics.cap_height(years[0].font) / graphics.cap_height(labels[0].font)
    assert ratio >= graphics.TALL_YEAR_RATIO or (ratio >= graphics.TALL_YEAR_RATIO_MIN
                                                and "under 4.5" in caplog.text), ratio
    all_years = [p for n, p in st["plates"] if n.startswith("year")]
    assert sum(map(_ink, all_years)) >= graphics.TALL_INK_RATIO * sum(map(_ink, labels))
    assert rel_luminance(graphics.TALL_LABEL_FILL) < rel_luminance(brand.GOLD)
    assert all(tuple(p.fill) == graphics.TALL_LABEL_FILL for p in labels)
    assert min(p.font.size for p in labels) >= graphics.TALL_LABEL_MIN_PX == 30
    assert graphics.TALL_LABEL_WEIGHT == 600 and graphics.TALL_LEADING == 1.12


@pytest.mark.parametrize("events", [POMPEII, BABYLON], ids=["pompeii", "babylon"])
def test_tall_spans_sit_in_their_own_row_between_the_entries(tmp_path, monkeypatch, events) -> None:
    # round 7 (deliberate, coordinator R3; was test_tall_spans_sit_inline_beside_the_later_year): back between the
    # entries in the full form, never level with a year ("+17 YRS" by "AD 79" read as part of the date)
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4"), fps=2)
    gaps = {int(n[3:]): p for n, p in st["plates"] if re.fullmatch(r"gap\d", n)}
    assert set(gaps) == {1, 2, 3}
    for k, p in gaps.items():
        above = max(q.box[3] for n, q in st["plates"] if n == f"label{k - 1}")
        below = min(q.box[1] for n, q in st["plates"] if n in (f"year{k}", f"year{k}_circa"))
        assert above < p.box[1] and p.box[3] < below, (k, p.text)
        assert SPAN.match(p.text), p.text
        assert tuple(p.fill) == brand.STONE and p.box[2] <= graphics.TEXT_RIGHT


def test_a_span_no_band_holds_is_never_dropped(tmp_path, monkeypatch, caplog) -> None:
    # round 7 (deliberate, R3; was test_a_span_that_does_not_fit_is_dropped_and_logged): as before round 6, a span
    # no band holds is set at the floor in the clear band (logged), never dropped
    monkeypatch.setattr(graphics, "span_fit", lambda text, px, fits: None)
    with caplog.at_level(logging.WARNING, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(POMPEII, tmp_path / "t.mp4"), fps=2)
    assert {int(n[3:]) for n, _p in st["plates"] if re.fullmatch(r"gap\d", n)} == {1, 2, 3}
    assert "without its full air" in caplog.text


@pytest.mark.parametrize("events", [POMPEII, BABYLON], ids=["pompeii", "babylon"])
def test_wide_spans_below_the_axis_on_one_row_and_420_px_columns(tmp_path, monkeypatch, caplog, events) -> None:
    seen = {}
    real = graphics.spine_marks

    def spy(size, along_x, fixed, *a, **k):
        seen["spine"] = fixed
        return real(size, along_x, fixed, *a, **k)
    monkeypatch.setattr(graphics, "spine_marks", spy)
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", width=1920,
                                                                   height=1080), fps=2)
    gaps = [(n, p) for n, p in st["plates"] if n.startswith("gap")]
    # round 7 (deliberate, R3): the full form, on one row where it fits (broken before "LATER" where not), under the
    # axis unless logged as set over it
    rows: dict[str, list] = {}
    for n, p in gaps:
        rows.setdefault(n.split("_")[0], []).append(p)
    assert rows and all(len(ps) <= 2 for ps in rows.values())
    for ps in rows.values():
        text = " ".join(p.text for p in ps)
        assert SPAN.match(text), text
        if f"the span {text!r} set over the spine" not in caplog.text:
            assert all(p.box[1] > seen["spine"] for p in ps), text
    by_label: dict[str, list] = {}
    for n, p in st["plates"]:
        if n.startswith("label"):
            by_label.setdefault(n, []).append(p)
    for ps in by_label.values():
        assert len(ps) <= graphics.WIDE_LABEL_ROWS
        text = " ".join(p.text for p in sorted(ps, key=lambda p: p.box[1]))
        if f"wider than its {graphics.WIDE_LABEL_COL:.0f} px column to stay whole: {text}" not in caplog.text:
            assert max(p.box[2] - p.box[0] for p in ps) <= graphics.WIDE_LABEL_COL + 8
