"""
src/fact_quantities.py
======================
Structured counts and measures on a checked fact (QF-61, operator decision 2026-11-12, answer A: structured counts
only). They are the ONLY numbers an illustration prompt may carry: nothing reads a count or a measure out of prose
for the image generator.

The fact-sheet writer proposes them with each fact (models.DraftFact.counts / .measures); the deterministic fact-sheet
rules (fact_sheet._apply_rules) keep an entry only when check_entry finds nothing wrong, and store the kept entries on
the fact as one record signed under FACT_RECORD_KEY (record_signing kind "fact_quantities"), bound to the fact's id,
statement and evidence:

    fact["quantities"] = {"version": 1, "fact_id", "binds": <sha256 of id, statement, evidence>,
                          "counts":   [{"element", "count", "qualifier", "source"}],
                          "measures": [{"element", "measure", "qualifier"}],
                          "signature"}

A fact without the field is valid and countless. A record of another version, without a valid signature, bound to
other words or evidence, or whose entries no longer pass check_entry, gives nothing (checked). Only an "exact" entry
is ever handed on, and only from a fact that is not disputed, carries no hedge and is strongly sourced as a number:
an approximate, bounded, ranged, per-unit or comparison-set quantity is kept for the record but never drawn, not even
as "roughly N" (a drawing of roughly N shows exactly some number, and nothing can check it). An exact entry is also
refused when its sentence reads as approximate, distributive, a membership or comparison set, a fraction, a hedge, a
plan or a negation, or when the prose reader holds a quantity in it: those checks can only remove an entry.
"""
from __future__ import annotations

import re
from typing import Any

VERSION = 1
KIND = "fact_quantities"
QUALIFIERS = ("exact", "approximate", "minimum", "maximum", "range", "per_unit", "comparison_set")
MAX_COUNT = 100_000
_ELEMENT = re.compile(r"[a-z][a-z'’-]*(?: [a-z][a-z'’-]*){0,3}")
# words that make a stated number anything but an exact inventory of the subject's own parts
_NOT_EXACT = re.compile(
    r"~|≈|%|\bc\.\s*\d|\b(?:about|around|approximately|approx|roughly|nearly|almost|some|estimated?|estimates|circa|ca|over|"
    r"under|more|less|fewer|up\s+to|at\s+least|at\s+most|as\s+many\s+as|upwards|or\s+so|give\s+or\s+take|"
    r"thereabouts|odd|plus|each|every|either|apiece|per|a\s+side|a\s+piece|among|amongst|one\s+of|of\s+the|"
    r"of\s+which|of\s+them|out\s+of|between|percent|per\s+cent|half|halves|thirds?|quarters?|fifths?|tenths?|"
    r"suggests?|suggested|believed?|thought|think|reported|reportedly|according|allegedly|probably|possibly|perhaps|"
    r"likely|may|might|could|would|should|planned|designed|meant|intended|was\s+to|were\s+to|not|no|never|"
    r"without|none|n't|largest|smallest|oldest|tallest|biggest|most|least|only)\b",
    re.IGNORECASE)
_RANGE = re.compile(r"\d\s*(?:-|–|to|or|and)\s*\d|\b(?:to|or)\s+(?:\d|one|two|three|four|five|six|seven|eight|nine|"
                    r"ten|twenty|thirty|forty|fifty|hundred)\b", re.IGNORECASE)


# verbs a count of the subject's parts follows ("had three masts", "holds 82 fragments")
_HAS = {"has", "have", "had", "having", "holds", "hold", "held", "contains", "contain", "contained", "carries",
        "carry", "carried", "bears", "bear", "bore", "features", "featured", "shows", "showed", "includes",
        "included", "comprises", "comprised", "numbers", "numbered", "boasts", "boasted", "houses", "housed", "with"}


def _norm(text: Any) -> str:
    return " ".join(str(text or "").replace("’", "'").split())


def _sentence_of(statement: str, part: str) -> str:
    """The sentence of `statement` that holds `part` (the whole statement when it cannot be told)."""
    at = statement.find(part)
    if at < 0:
        return statement
    start = max(statement.rfind(c, 0, at) for c in ".;!?") + 1
    ends = [e for e in (statement.find(c, at + len(part)) for c in ".;!?") if e >= 0]
    return statement[start:min(ends) + 1 if ends else len(statement)]


def binds(fact: dict[str, Any]) -> str:
    """What the record is bound to: the fact's id, statement and evidence (its hedge may change on load)."""
    from .checkpoints import fingerprint
    return fingerprint({"id": str(fact.get("id") or ""), "statement": str(fact.get("statement") or ""),
                        "evidence": fact.get("evidence") or []})


def _quotes(fact: dict[str, Any]) -> list[str]:
    return [str(e.get("quote") or "") for e in fact.get("evidence") or [] if isinstance(e, dict)]


def _strong(fact: dict[str, Any]) -> bool:
    from .fact_sheet import strongly_sourced
    evidence = [e for e in fact.get("evidence") or [] if isinstance(e, dict) and e.get("url")]
    return bool(evidence) and strongly_sourced("number", evidence)


def check_entry(kind: str, entry: Any, fact: dict[str, Any]) -> str:
    """"" when `entry` (a "counts" or "measures" entry) is what the fact's statement and evidence state; else why not.
    An "exact" entry must also be exact as stated (see the module note)."""
    from .fact_sheet import _number_supported, numbers_in
    from .illustration_elements import (
        _PHYSICAL_UNITS,
        _STOP,
        canonical,
        extract,
        values_in,
    )
    fields = ("element", "count", "qualifier", "source") if kind == "counts" else ("element", "measure", "qualifier")
    if not isinstance(entry, dict) or set(entry) != set(fields):
        return f"a {kind[:-1]} needs exactly the fields {', '.join(fields)}"
    if entry["qualifier"] not in QUALIFIERS:
        return f"unknown qualifier {entry['qualifier']!r}"
    element = entry["element"]
    if not isinstance(element, str) or not _ELEMENT.fullmatch(element):
        return "the element is not a plain noun phrase of at most four lower-case words"
    statement = _norm(fact.get("statement"))
    words = {canonical(w) for w in re.findall(r"[a-z][a-z'-]*", statement.lower())}
    if any(canonical(w) not in words for w in element.split()):
        return f"the statement does not name {element!r}"
    text = entry["source"] if kind == "counts" else entry["measure"]
    if not isinstance(text, str) or not (0 < len(_norm(text)) <= 120) or _norm(text) not in statement:
        return "its words are not the statement's own"
    text = _norm(text)
    if kind == "counts":
        count = entry["count"]
        if type(count) is not int or not 1 <= count <= MAX_COUNT:
            return "the count is not a whole number from 1 to 100,000"
        if values_in(text) != {float(count)}:
            return "its words do not state exactly that one number"
        if canonical(element.split()[-1]) not in {canonical(w) for w in re.findall(r"[a-z][a-z'-]*", text.lower())}:
            return "its words do not hold the counted noun"
        if not any(float(count) in values_in(q) for q in _quotes(fact)):
            return "no evidence quote states that number"
        if re.search(r"(?:ed|ing)$", element.split()[-1]):
            return "the counted element is not a noun"
    else:
        found = numbers_in(text)
        if len(values_in(text)) != 1 or len(found) != 1:
            return "a measure states exactly one number"
        lower = text.lower()
        if not any(re.search(rf"\d\s*{re.escape(u)}\b|\b{re.escape(u)}\b", lower) for u in _PHYSICAL_UNITS):
            return "a measure needs a unit of size, distance, area, volume, weight or angle"
        quoted = [n for q in _quotes(fact) for n in numbers_in(q)]
        if not _number_supported(found[0], quoted):
            return "no evidence quote states that number"
    if entry["qualifier"] != "exact":
        return ""
    if fact.get("disputed") or _norm(fact.get("hedge")):
        return "a disputed or hedged fact gives no exact quantity"
    if not _strong(fact):
        return "an exact quantity needs a tier-1 source or two corroborating sites"
    sentence = _sentence_of(statement, text)
    if _NOT_EXACT.search(sentence) or _RANGE.search(sentence):
        return "its sentence does not state it as an exact inventory"
    if kind == "counts":
        before = re.findall(r"[A-Za-z][A-Za-z'-]*|\d", statement[:statement.find(text)])
        prev = before[-1] if before else ""
        if prev and (prev[:1].isupper() or prev.isdigit() or (prev.lower() not in _STOP and prev.lower() not in _HAS)):
            return "the number follows a name or a noun (a label such as 'Cave 16'), not a verb or a function word"
        head = canonical(element.split()[-1])
        if sum(canonical(w) == head for w in re.findall(r"[a-z][a-z'-]*", statement.lower())) > 1:
            return "the statement names the counted element more than once"
    if extract(sentence)["unresolved_quantities"]:
        return "its sentence holds a quantity that is not exact and clear"
    return ""


def check(fact: dict[str, Any], counts: list[Any], measures: list[Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """The signed quantities record for `fact` (a fact dict as the sheet stores it) from the writer's proposed
    entries, keeping only entries check_entry passes (an element counted more than once is dropped whole); None when
    none is kept. Returns (record, [why each dropped entry was dropped])."""
    from . import record_signing
    dropped, kept = [], {"counts": [], "measures": []}
    for kind, proposed in (("counts", counts), ("measures", measures)):
        for e in proposed or []:
            entry = e.model_dump() if hasattr(e, "model_dump") else dict(e) if isinstance(e, dict) else e
            if isinstance(entry, dict):
                entry = {k: (_norm(v) if isinstance(v, str) and k != "element" else v) for k, v in entry.items()}
                if isinstance(entry.get("element"), str):
                    entry["element"] = _norm(entry["element"]).lower()
            why = check_entry(kind, entry, fact)
            if why:
                dropped.append(f"{kind[:-1]} {entry!r}: {why}")
            else:
                kept[kind].append(entry)
    for kind in ("counts", "measures"):
        seen: dict[str, int] = {}
        for e in kept[kind]:
            seen[e["element"]] = seen.get(e["element"], 0) + 1
        for e in [e for e in kept[kind] if seen[e["element"]] > 1]:
            dropped.append(f"{kind[:-1]} {e!r}: the element is given more than once")
        kept[kind] = [e for e in kept[kind] if seen[e["element"]] == 1]
    if not kept["counts"] and not kept["measures"]:
        return None, dropped
    record = {"version": VERSION, "fact_id": str(fact.get("id") or ""), "binds": binds(fact), **kept}
    return record_signing.signed(KIND, record), dropped


def checked(fact: dict[str, Any]) -> dict[str, Any]:
    """The exact counts and measures a fact's signed record gives ({"counts": [...], "measures": [...]}, each
    entry as stored), and "problem": why the record gives nothing ("" when it is valid or absent)."""
    from . import record_signing
    out: dict[str, Any] = {"counts": [], "measures": [], "problem": ""}
    record = fact.get("quantities") if isinstance(fact, dict) else None
    if record is None:
        return out
    if not isinstance(record, dict) or record.get("version") != VERSION:
        out["problem"] = "a quantities record of an unknown version"
        return out
    why = record_signing.problem(KIND, record)
    if why:
        out["problem"] = why
        return out
    if record.get("fact_id") != str(fact.get("id") or "") or record.get("binds") != binds(fact):
        out["problem"] = "the quantities record was made for other words or evidence"
        return out
    if set(record) != {"version", "fact_id", "binds", "counts", "measures", record_signing.SIGNATURE}:
        out["problem"] = "the quantities record has unexpected fields"
        return out
    for kind in ("counts", "measures"):
        entries = record.get(kind)
        if not isinstance(entries, list):
            out["problem"] = f"the quantities record's {kind} is not a list"
            return {"counts": [], "measures": [], "problem": out["problem"]}
        for e in entries:
            if isinstance(e, dict) and e.get("qualifier") == "exact" and not check_entry(kind, e, fact):
                out[kind].append(dict(e))
    return out
