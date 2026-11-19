"""QF-61b: a number-free description per counted fact (src/fact_descriptions.py), so an illustration is not lost to
the number-sentence rule (QF-61 round 7 dropped every number-bearing sentence: real-fact specs 85 -> 54).

Invariants: a kept description is one whole sentence of no number, said in its fact's own evidence words, signed with
the fact and bound to it; anything else is dropped whole with why (prose is never edited); the generator's subject
uses it only through its valid record. Plus the review-7 notes on the sentence rule (missing number words, the
accented-name and correlative over-drops). Synthetic inputs only.
"""

from __future__ import annotations

import pytest

from src import fact_descriptions, fact_sheet, illustration_elements, image_gen
from src.models import DraftFact

SHIP = "The wooden ship had three tall masts and two sails."
QUOTE = ("The wooden ship had three tall masts and two sails. Its hull of oak planks was painted black and lay "
         "half buried in the harbour mud.")
GOOD = "Its hull of oak planks was painted black."
QUOTES = [{"url": "https://www.britannica.com/topic/ship", "quote": QUOTE}]


def _fact(**kw):
    draft = DraftFact(id="F1", statement=SHIP, kind="number", quote_ids=[1], disputed=False, hedge="", **kw)
    facts, dropped = fact_sheet._apply_rules([draft], QUOTES)
    assert not dropped
    return facts[0]


def test_a_counted_fact_keeps_its_checked_description_signed_with_it():
    fact = _fact(description=GOOD)
    rec = fact.get("description")
    assert rec and rec["signature"] and rec["sentence"] == GOOD and rec["fact_id"] == "F1"
    assert fact_descriptions.sentence_of(fact) == (GOOD, "")
    # the generator's subject: the statement states numbers (dropped whole), so the description is the picture
    assert image_gen._subject([fact]) == GOOD
    assert "description" not in _fact()                                  # a fact without one stays valid


@pytest.mark.parametrize("sentence,why", [
    ("Its two hulls of oak planks were painted black.", "number"),
    ("Its hull of oak planks was painted black. It lay in the mud.", "one whole sentence"),
    ("Its hull of oak planks was painted bright crimson.", "evidence does not say"),
    ("Its hull, according to Britannica, was painted black.", "source"),
    ('Its hull was "painted black".', "quote"),
    ("Its hull of oak planks was painted black", "one whole sentence"),
    ("The wooden ship had " + "black oak planks and " * 12 + "mud.", "longer"),
])
def test_a_description_that_fails_any_check_is_dropped_whole(sentence, why):
    fact = _fact(description=sentence)
    assert "description" not in fact
    assert why in fact_descriptions.check_sentence(fact, sentence)


def test_a_number_free_fact_needs_no_description():
    plain = DraftFact(id="F2", statement="The hull of the ship was built of oak planks.", kind="other", quote_ids=[1],
                      disputed=False, hedge="", description=GOOD)
    facts, _ = fact_sheet._apply_rules([plain], [{"url": "https://www.britannica.com/x",
                                                   "quote": "The hull of the ship was built of oak planks. " + QUOTE}])
    assert "description" not in facts[0]


def test_a_tampered_or_rebound_record_gives_nothing():
    fact = _fact(description=GOOD)
    forged = dict(fact, description=dict(fact["description"], sentence="Its hull was painted black."))
    assert fact_descriptions.sentence_of(forged)[0] == ""
    moved = dict(fact, statement="The wooden ship had four tall masts and two sails.")
    assert fact_descriptions.sentence_of(moved) == ("", "the description record was made for other words or evidence")
    assert image_gen._subject([forged]) == ""


@pytest.mark.parametrize("sentence,number", [
    ("The treasury counted a lakh of coins.", True),
    ("A brace of pheasants hung by the door.", True),
    ("The pilgrims rested at Vézelay abbey.", False),
    ("The walls were made of both stone and timber.", False),
    ("Both towers fell in the storm.", True),
    ("The bunker was used during World War II.", False),
    ("Edward I built the castle.", True),
])
def test_the_sentence_rule_review_7_notes(sentence, number):
    assert illustration_elements.has_number(sentence) is number, illustration_elements.number_reasons(sentence)
