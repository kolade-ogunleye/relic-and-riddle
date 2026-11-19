"""
src/fact_descriptions.py
========================
QF-61b: a number-free description per counted fact, so an illustration is not lost to the number-sentence rule.

QF-61 round 7 drops every sentence that states a number from the illustration prompt (prose is never edited), which
cut real-fact specs 85 -> 54 and real-run specs 55 -> 41: a fact whose only sentence states its count gives no
picture. The fact-sheet writer now proposes, with each fact that states a number, ONE descriptive sentence that states
none (what the thing looks like, its materials, its setting: models.DraftFact.description). The deterministic
fact-sheet rules (fact_sheet._apply_rules) keep it only when check_sentence finds nothing wrong, and store it on the
fact as a record signed under FACT_RECORD_KEY (record_signing kind "fact_description"), bound to the fact's id,
statement and evidence exactly as its quantities are (fact_quantities.binds):

    fact["description"] = {"version": 1, "fact_id", "binds", "sentence", "signature"}

It is checked like any fact, and stricter: one whole sentence, no number of any kind (illustration_elements.has_number),
no attribution to a source, no quote, bracket or backtick, at most DESCRIPTION_MAX characters, and every content word of
it found in the fact's own statement or cited evidence (a description says only what the evidence words: it cannot
introduce a material, a colour or a place). No prose is ever edited: a sentence that fails is dropped whole, with why.
Numbers still come only from the signed structured counts (src/fact_quantities.py). A fact without the field is valid;
a record of another version, unsigned, bound to other words or evidence, or whose sentence no longer passes gives
nothing (sentence_of).
"""
from __future__ import annotations

import re
from typing import Any

VERSION = 1
KIND = "fact_description"
DESCRIPTION_MAX = 180          # characters (image_gen.SUBJECT_MAX: a description fits the generator's subject whole)
_UNSAFE = re.compile(r"[\"“”<>{}\[\]`]")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
# function words a description may use that its evidence need not contain
_FREE = {"also", "very", "still", "once", "now", "then", "here", "there", "today", "made", "make", "built", "stand",
         "stands", "stood", "lies", "lay", "sits", "sat", "seen", "shows", "shown", "look", "looks", "appear",
         "appears", "like"}


def _norm(text: Any) -> str:
    return " ".join(str(text or "").replace("’", "'").split())


def _stems(text: str) -> set[str]:
    from .illustration_elements import canonical
    out: set[str] = set()
    for w in _WORD.findall(_norm(text).lower()):
        out.add(canonical(w))
        for suffix in ("ing", "ed", "es", "s", "en"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                out.add(w[: -len(suffix)])
    return out


def _evidence_text(fact: dict[str, Any]) -> str:
    quotes = [str(e.get("quote") or "") for e in fact.get("evidence") or [] if isinstance(e, dict)]
    return " ".join([str(fact.get("statement") or ""), *quotes])


def check_sentence(fact: dict[str, Any], sentence: Any) -> str:
    """Why `sentence` cannot be `fact`'s description ("" when it can). The fact must state a number (a number-free
    fact describes itself), and the sentence must be one whole number-free sentence of the fact's own evidence."""
    from .fact_sheet import _named_sources
    from .illustration_elements import (
        _STOP,
        has_number,
        number_free_sentences,
        sentences,
    )
    text = _norm(sentence)
    if not text:
        return "empty"
    statement = str(fact.get("statement") or "")
    if not has_number(statement):
        return "the fact states no number: its own sentences describe it"
    if len(sentences(text)) != 1 or not text.endswith((".", "!")):
        return "not one whole sentence"
    if len(text) > DESCRIPTION_MAX:
        return f"longer than {DESCRIPTION_MAX} characters"
    if has_number(text):
        return "it states a number"
    if _UNSAFE.search(text):
        return "it carries a quote, bracket or backtick"
    if _named_sources(text):
        return "it attributes to a source"
    if text in {_norm(s) for s in number_free_sentences(statement)}:
        return "it repeats the statement's own sentence"
    known = _stems(_evidence_text(fact))
    missing = [w for w in _WORD.findall(text.lower()) if len(w) >= 4 and w not in _STOP and w not in _FREE
               and not (_stems(w) & known)]
    if missing:
        return f"words its evidence does not say: {sorted(set(missing))}"
    return ""


def binds(fact: dict[str, Any]) -> str:
    from .fact_quantities import binds as fact_binds
    return fact_binds(fact)


def check(fact: dict[str, Any], sentence: Any) -> tuple[dict[str, Any] | None, str]:
    """The signed description record for `fact` from the writer's proposed `sentence` (None and why when it fails
    check_sentence or none was proposed)."""
    from . import record_signing
    if not _norm(sentence):
        return None, ""
    why = check_sentence(fact, sentence)
    if why:
        return None, why
    record = {"version": VERSION, "fact_id": str(fact.get("id") or ""), "binds": binds(fact),
              "sentence": _norm(sentence)}
    return record_signing.signed(KIND, record), ""


def sentence_of(fact: dict[str, Any]) -> tuple[str, str]:
    """(the fact's checked description, "") or ("", why it gives none): the record must be of this version, validly
    signed, bound to the fact's id, statement and evidence, hold only its fields, and its sentence still pass."""
    from . import record_signing
    record = fact.get("description") if isinstance(fact, dict) else None
    if record is None:
        return "", ""
    if not isinstance(record, dict) or record.get("version") != VERSION:
        return "", "a description record of an unknown version"
    why = record_signing.problem(KIND, record)
    if why:
        return "", why
    if record.get("fact_id") != str(fact.get("id") or "") or record.get("binds") != binds(fact):
        return "", "the description record was made for other words or evidence"
    if set(record) != {"version", "fact_id", "binds", "sentence", record_signing.SIGNATURE}:
        return "", "the description record has unexpected fields"
    why = check_sentence(fact, record.get("sentence"))
    if why:
        return "", f"the description no longer passes: {why}"
    return str(record["sentence"]), ""
