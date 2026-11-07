"""Carousel rhythm (2026-11-01): neighbouring slides never repeat a photo treatment; figure slides keep theirs."""

import json
from itertools import pairwise
from pathlib import Path

from src.multi_format import MultiFormatGenerator as M


def test_neighbours_do_not_repeat_a_treatment() -> None:
    cards = [{"heading": "Cover", "body": "x"},
             {"heading": "Then and now", "body": "Compared with the modern town"},
             {"heading": "Unlike any other", "body": "In contrast with its surface"},
             {"heading": "Closing", "body": "What do you think?"}]
    assert [M.route_carousel_archetype(c, i, 4) for i, c in enumerate(cards)][1:3] == ["comparison", "comparison"]
    seq = M.sequence_archetypes(cards)
    assert seq[0] == "cover" and seq[-1] == "debate" and seq[1] == "comparison"
    assert all(a != b for a, b in pairwise(seq))


def test_figure_slides_keep_their_archetype() -> None:
    cards = [{"heading": "Cover", "body": "x"}, {"heading": "Deep", "body": "280 feet deep, 18 levels"},
             {"heading": "People", "body": "Room for 20,000 people"}, {"heading": "End", "body": "Why?"}]
    assert M.sequence_archetypes(cards)[1:3] == ["colossal_stat", "colossal_stat"]


def test_the_review_payloads_alternate() -> None:
    inputs = json.loads(Path("docs/reviews/cover-refinement-2026-11-01/inputs.json").read_text(encoding="utf-8"))
    for name, item in inputs.items():
        seq = M.sequence_archetypes(item["payload"]["carousel_slides"])
        assert all(a != b or a in M.REPEATABLE for a, b in pairwise(seq)), (name, seq)
