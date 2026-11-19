"""Freeze SYS-D-2 (lane D cycle 1, r8 AD F1): typography.spoken_units converts a whole span when the signed narration
gives that span's pair, both ends or neither, keeping the text's own connective and hedge; the drawn figure is then a
signed figure (figures.truth_faults against the narration). Invariants over generic phrasings, no topic words."""
from __future__ import annotations

import re

import pytest

from src import figures
from src.typography import spoken_units

NARRATIONS = {   # the narration's three ways of signing a span with its conversion
    "span pair": "The length was between roughly 137 and 138 feet (41.75 to 42 m) when complete.",
    "dashed pair": "It ran 137-138 ft (41.75-42 m) end to end.",
    "pair per end": "It ran between 137 feet (41.75 m) and 138 feet (42 m) end to end.",
}
TEXTS = ["Surveys place the length between roughly 41.75 and 42 meters.", "ROUGHLY 41.75–42 M",
         "ABOUT 41.75 TO 42 M LONG", "41.75-42 m"]


def _spans(text):
    return [tuple(float(n.replace(",", "")) for n in m.groups()[:2]) + (m.group(3).lower(),) for m in re.finditer(
        r"(\d[\d,]*(?:\.\d+)?)\s*(?:-|–|to|and)\s*(\d[\d,]*(?:\.\d+)?)\s*(feet|ft|meters|m)\b", text, re.IGNORECASE)]


@pytest.mark.parametrize("kind", NARRATIONS)
@pytest.mark.parametrize("text", TEXTS)
def test_a_span_converts_whole_into_the_lead_unit_with_its_own_connective_and_hedge(kind, text):
    narration = NARRATIONS[kind]
    out = spoken_units(text, narration, "imperial")
    if figures.truth_faults(text.replace("41.75", "137").replace("42", "138").replace("meters", "feet")
                            .replace(" M", " FT").replace(" m", " ft"), [narration]):
        # the lead side is hedged and the text is not: converting would draw a figure_gate fault, so it stays whole
        assert out == text
        return
    assert _spans(out) and all(lo == 137 and hi == 138 and u in ("feet", "ft") for lo, hi, u in _spans(out)), out
    # the text's own words around the span stay: only the two numbers and the unit change
    assert re.sub(r"[\d.,]+|feet|ft|meters|\bm\b", "#", out, flags=re.IGNORECASE) == re.sub(
        r"[\d.,]+|feet|ft|meters|\bm\b", "#", text, flags=re.IGNORECASE)
    assert out.isupper() == text.isupper()
    assert figures.truth_faults(out, [narration]) == []


@pytest.mark.parametrize("kind", NARRATIONS)
def test_the_conversion_runs_both_ways(kind):
    out = spoken_units("ROUGHLY 137–138 FT", NARRATIONS[kind], "metric")
    assert _spans(out) == [(41.75, 42.0, "m")], out
    assert figures.truth_faults(out, [NARRATIONS[kind]]) == []


@pytest.mark.parametrize("kind", NARRATIONS)
@pytest.mark.parametrize("text", ["41.75 and 43 meters", "40–42 M", "between 41.75 and 42 cm"])
def test_a_span_the_narration_does_not_sign_is_never_converted_nor_one_end_of_it(kind, text):
    """Both ends or neither: a span whose pair the narration does not sign stays whole in its own unit."""
    assert spoken_units(text, NARRATIONS[kind], "imperial") == text


@pytest.mark.parametrize("text", TEXTS)
def test_unchanged_when_the_narration_signs_no_pair(text):
    for narration in ("The length was between roughly 41.75 and 42 meters.", "It ran 137 feet end to end.", ""):
        assert spoken_units(text, narration, "imperial") == text


@pytest.mark.parametrize("text", TEXTS)
def test_a_span_already_in_the_lead_unit_is_left_as_written(text):
    for narration in NARRATIONS.values():
        assert spoken_units(text, narration, "metric") == text


@pytest.mark.parametrize("kind", NARRATIONS)
@pytest.mark.parametrize("text", ["Surveyed length: roughly 137–138 ft (41.75 to 42 m).",
                                  "ROUGHLY 41.75–42 M (137–138 FT)"])
def test_a_span_drawn_with_its_own_conversion_is_left_whole(kind, text):
    """Both units already drawn (the production sample's Aswan fact line): neither span is rewritten."""
    for lead in ("imperial", "metric"):
        assert spoken_units(text, NARRATIONS[kind], lead) == text
