"""
src/card_fields.py
==================
Card fields on a fact sheet (operator decision, 2026-11-03): the design cards (src/design_cards.py) draw only
these, never free text, a headline or words taken out of a passage.

  statements  short card-ready statements (at most 84 characters), each tied to the fact it restates;
  myths       {myth as people state it, verdict "false" or "misleading", refuting fact, correction (at most 84
              characters), source};
  quotes      {exact quotation, structured speaker, work, date or null, source URL, the evidence string it
              appears in verbatim, and that evidence's fact}.

The fact-sheet writer proposes them from its own gathered sources (fact_sheet.build); the deterministic rules
here drop what no evidence holds (from_draft, findings); then every text goes through the project's existing fact
checker, the one the final public copy goes through (trace_check.check, then trace_check.evidence_check), one
text at a time. Each field gets a CardCheck: the verdict derived in code from the checkers' own audits (never
taken from a model's word for it), the runs themselves (text, model, audits), and hashes binding the record to
the field, to the sheet's facts and to itself. Only "supported" is usable: for a myth, the myth contradicted by
both checkers citing its refuting fact AND its correction supported; for a quote, also the exact quotation inside
one pair of the cited evidence's quotation marks, with the speaker in that evidence's attribution.

verify_field is what a card calls: pure (no model, no network), it re-derives the verdict from the stored runs and
refuses a field whose record is missing, stamped without runs, edited after its check, or made against other facts,
and (operator decision A, 2026-11-03) one whose record or runs carry no valid HMAC-SHA256 signature under
FACT_RECORD_KEY, made in check_field as the checkers returned (src/record_signing.py): an old unsigned sheet, a
record edited on disk or signed under another key is untrusted until check_cards runs on it again.
Web pages and model output are data here, never instructions.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from .checkpoints import fingerprint
from .models import (
    CARD_TEXT_MAX,
    CardCheck,
    CardMyth,
    CardQuote,
    CardStatement,
    CheckRun,
    DraftCardFields,
    FactSheetCards,
    Source,
)

log = logging.getLogger(__name__)

KINDS = ("statements", "myths", "quotes")
_MODEL = {"statements": CardStatement, "myths": CardMyth, "quotes": CardQuote}
MYTH_MAX = 160            # characters: a myth longer than this is no card line on any format
QUOTE_MAX = 170
MAX_FIELDS = {"statements": 8, "myths": 3, "quotes": 3}   # per sheet: each is one checker pass per text
TRACE_BAD = ("contradicts", "overstated")
_STOPWORDS = frozenset(("a", "an", "the", "of", "in", "on", "at", "to", "by", "for", "and", "or"))


class FieldRefused(ValueError):
    def __init__(self, reason: str, code: str):
        super().__init__(reason)
        self.reason, self.code = reason, code


# ------------------------------------------------------------------------------------------------ hashing
def facts_hash(sheet: dict[str, Any]) -> str:
    return fingerprint((sheet or {}).get("facts") or [])


def field_hash(field: dict[str, Any]) -> str:
    return fingerprint({k: v for k, v in field.items() if k != "check"})


def _unsigned_as_before(d: dict[str, Any]) -> dict[str, Any]:
    """A record (or run) as hashed before signatures existed: an empty "signature" is left out, so the hash of a
    record written before signing is unchanged (it is still untrusted: verify_field wants a signature)."""
    return {k: v for k, v in d.items() if not (k == "signature" and not v)}


def check_hash(check: dict[str, Any]) -> str:
    rec = _unsigned_as_before({k: v for k, v in check.items() if k != "sha256"})
    if isinstance(rec.get("runs"), list):
        rec["runs"] = [_unsigned_as_before(r) if isinstance(r, dict) else r for r in rec["runs"]]
    return fingerprint(rec)


# ------------------------------------------------------------------------------------------------ signatures
# Operator decision A (2026-11-03): the hashes above can be recomputed by anyone who edits a sheet, so a forged record
# (made-up runs, hashes recomputed) passed. Each checker run is now signed when the checker returns (check_field), and
# the whole record when it is sealed there (record_signing, under FACT_RECORD_KEY); verify_field refuses a record or
# run without a valid signature. A record made any other way (_seal alone, an edit on disk, an old unsigned sheet)
# is untrusted, and card fields are re-checked by check_cards.
def _run_content(run: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in run.items() if k != "signature"}


def _signed_run(run: CheckRun) -> CheckRun:
    from .record_signing import sign
    d = run.model_dump()
    return run.model_copy(update={"signature": sign("check_run", _run_content(d))})


def _card_content(kind: str, field_id: str, check: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "id": field_id, "check": {k: v for k, v in check.items() if k not in ("signature", "sha256")}}


def _sign_check(kind: str, field: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """The sealed record signed over its whole content (and its field's kind and id), then its hash taken again
    over everything, the signature too."""
    from .record_signing import sign
    check["signature"] = sign("card_check", _card_content(kind, str(field.get("id")), check))
    check["sha256"] = check_hash(check)
    return check


def signature_problem(kind: str, field_id: str, check: dict[str, Any]) -> str:
    """"" when the card record and every run in it carry valid signatures under FACT_RECORD_KEY; else why not."""
    from .record_signing import problem
    why = problem("card_check", {**_card_content(kind, field_id, check), "signature": check.get("signature")})
    if why:
        return why
    for run in check.get("runs") or []:
        why = problem("check_run", run if isinstance(run, dict) else {})
        if why:
            return f"a checker run: {why}"
    return ""


# ------------------------------------------------------------------------------------------------ small helpers
def _norm(s: str) -> str:
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("„", '"'), ("–", "-"), ("—", "-")):
        s = str(s or "").replace(a, b)
    return " ".join(s.split())


def _url(u: str) -> str:
    p = urlparse(str(u or "").strip())
    return f"{(p.hostname or '').lower().removeprefix('www.')}{p.path.rstrip('/')}".lower()


def _fact(sheet: dict[str, Any], fid: str) -> dict[str, Any] | None:
    return next((f for f in (sheet or {}).get("facts") or [] if isinstance(f, dict) and f.get("id") == fid), None)


def _nums(s: str) -> list[str]:
    return [m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", str(s or ""))]


def evidence_numbers(sheet: dict[str, Any], fact_id: str) -> set:
    """The 1-based quote numbers evidence_check gives fact `fact_id`'s evidence (its listing: every fact's
    evidence in order, each quote once)."""
    out, seen, n = set(), {}, 0
    for f in (sheet or {}).get("facts") or []:
        for e in f.get("evidence") or []:
            q = e.get("quote")
            if q not in seen:
                n += 1
                seen[q] = n
            if f.get("id") == fact_id:
                out.add(seen[q])
    return out


def texts(kind: str, field: dict[str, Any]) -> dict[str, str]:
    """The texts of a field the checker is given, by part."""
    if kind == "statements":
        return {"text": field["text"]}
    if kind == "myths":
        return {"myth": field["myth"], "correction": field["correction"]}
    sp = field["speaker"]
    cite = ", ".join(x for x in (sp.get("role") or "", field.get("work") or "", field.get("date") or "") if x)
    return {"text": f"“{field['quotation']}” ({sp['name']}{', ' + cite if cite else ''})"}


def _target(kind: str, field: dict[str, Any]) -> str:
    return field["refuting_fact_id"] if kind == "myths" else field["fact_id"]


# ------------------------------------------------------------------------------------------------ deterministic rules
def findings(kind: str, field: dict[str, Any], sheet: dict[str, Any]) -> list[str]:
    """What no evidence on the sheet holds; [] when the field may go to the checker."""
    out: list[str] = []
    fact = _fact(sheet, _target(kind, field))
    if fact is None:
        return [f"unknown fact {_target(kind, field)!r}"]
    if kind == "statements":
        t = " ".join(str(field.get("text") or "").split())
        if not t or len(t) > CARD_TEXT_MAX:
            out.append(f"a statement is 1-{CARD_TEXT_MAX} characters")
        if t.endswith("?"):
            out.append("a statement is not a question")
        out += _source_findings(fact, [t])
    elif kind == "myths":
        m, c = (" ".join(str(field.get(k) or "").split()) for k in ("myth", "correction"))
        if not m or len(m) > MYTH_MAX:
            out.append(f"a myth is 1-{MYTH_MAX} characters")
        if not c or len(c) > CARD_TEXT_MAX:
            out.append(f"a correction is 1-{CARD_TEXT_MAX} characters")
        if m and m.casefold() == c.casefold():
            out.append("the myth and its correction say the same")
        if fact.get("disputed"):
            out.append("a disputed fact refutes nothing")
        out += _source_findings(fact, [c])
        src = (field.get("source") or {}).get("url", "")
        if not any(_url(e.get("url")) == _url(src) and int(e.get("tier", 9)) <= 2 for e in fact.get("evidence") or []):
            out.append("the myth's source is not tier 1-2 evidence of its refuting fact")
    else:
        q = " ".join(str(field.get("quotation") or "").split())
        name = " ".join(str((field.get("speaker") or {}).get("name") or "").split())
        if not q or len(q) > QUOTE_MAX:
            out.append(f"a quotation is 1-{QUOTE_MAX} characters")
        if any(x in q for x in ("…", "...", "[", "]")):
            out.append("a quotation is drawn whole: no elision, no editorial brackets")
        if not name:
            out.append("a quotation needs its speaker")
        if fact.get("disputed"):
            out.append("a disputed fact is not quoted")
        out += _source_findings(fact, None)
        ev = next((e for e in fact.get("evidence") or [] if _norm(e.get("quote")) == _norm(field.get("evidence"))), None)
        if ev is None:
            out.append("the evidence string is not one of its fact's evidence quotes")
        else:
            if _url(ev.get("url")) != _url(field.get("source_url")):
                out.append("the source URL is not that evidence's URL")
            if int(ev.get("tier", 9)) > 2:
                out.append("the quotation's evidence is not tier 1-2")
            if q and name:
                # the same parses the quote card guards with: the speaker from the attribution next to the marks,
                # and the role, work and date tied to that clause (review 2026-11-03, blocking 2), never only
                # found somewhere in the evidence or the fact
                from .design_cards import (
                    quote_attribution_problem,
                    quote_citation_problem,
                )
                why = quote_attribution_problem(q, name, str(ev.get("quote") or ""))
                if why:
                    out.append(why)
                else:
                    why = quote_citation_problem(q, name, (field.get("speaker") or {}).get("role"),
                                                 field.get("work"), field.get("date"), str(ev.get("quote") or ""))
                    if why:
                        out.append(why)
            for label, v in (("role", (field.get("speaker") or {}).get("role")), ("work", field.get("work"))):
                v = _norm(v)
                if v and not set(re.findall(r"[a-z0-9]+", v.casefold())) - _STOPWORDS:
                    out.append(f"the {label} {v!r} names nothing")
            if field.get("date") and not _nums(field["date"]):
                out.append(f"the date {field['date']!r} holds no year")
    return out


_YEARS = re.compile(r"\b(?:1\d{3}|20\d{2})s?\b|\b\d{1,2}(?:st|nd|rd|th)\s+(?:century|millennium)\b", re.IGNORECASE)


_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
          r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DATE_EXPR = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"(?:,?\s+\d{1,4})?\b"     # 30 June 1908
    r"|\b" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?\b(?:,?\s+\d{1,4}\b)?"            # July 29, 1941
    r"|\b\d{1,2}:\d{2}(?:\s*[ap]\.?m\b\.?)?|\b\d{1,2}\.\d{2}\s*[ap]\.?m\b\.?"        # 7:14, 7.14 pm
    r"|\b\d{1,2}\s*[ap]\.?m\b\.?"                                                   # 7 am
    r"|\b(?:\d[\d,]*\s*(?:-|\u2013|to|and)\s*)?\d[\d,]*\s*(?:bce|bc|ce|ad|b\.c\.e?\.?|a\.d\.)(?![a-z])"  # 9600 BC
    r"|\b(?:\d{1,2}(?:st|nd|rd|th)\s*(?:-|\u2013|to|and)\s*)?\d{1,2}(?:st|nd|rd|th)\s+(?:century|millennium)"
    r"(?:\s*(?:bce|bc|ce|ad|b\.c\.e?\.?|a\.d\.)(?![a-z]))?"                       # 8th to 7th century BC
    r"|\b(?:\d[\d,]*(?:\.\d+)?\s*(?:-|\u2013|to|and)\s*)?\d[\d,]*(?:\.\d+)?\s*(?:million\s+|thousand\s+)?years\s+ago\b"
    r"|\b\d{3,4}\s*(?:-|\u2013|to|and)\s*(?:1\d{3}|20\d{2})\b"                   # 780 to 1180
    r"|\b(?:ad|ce|a\.d\.)\s*\d{1,4}\b",                                              # AD 79
    re.IGNORECASE)


def _figures(s: str, date: bool = False) -> list[str]:
    """The figures of a card text: its numbers, less years and centuries (a date may rest on a reference work, as
    the fact-sheet rules allow: fact_sheet.strongly_sourced). On a date fact (`date`) the whole date expression is
    no figure: a day of the month, a BC/AD year of any size, a time of day."""
    s = str(s or "")
    if date:
        s = _DATE_EXPR.sub(" ", s)
    return _nums(_YEARS.sub(" ", s))


def _source_findings(fact: dict[str, Any], card_texts: Sequence[str] | None) -> list[str]:
    """A number or a quotation (card_texts None) on a card needs a tier 1-2 site besides Wikipedia among its fact's
    evidence (QF-40): a Wikipedia article and the pages it links to are one editable source. The evidence's
    provenance (tier, fetch path) is part of the facts the signed record binds (facts_sha256)."""
    if card_texts is not None and fact.get("kind") != "number" and not any(
            _figures(t, fact.get("kind") == "date") for t in card_texts):
        return []
    from .fact_sheet import corroborating_sites
    if corroborating_sites([e for e in fact.get("evidence") or [] if isinstance(e, dict) and e.get("url")]) - {
            "wikipedia.org"}:
        return []
    what = "a quotation" if card_texts is None else "a number"
    return [f"{what} needs a tier 1-2 source besides Wikipedia and the pages it cites"]


# ------------------------------------------------------------------------------------------------ verdicts
def _claims_cite(audits: Sequence[dict], key: str, wanted: set) -> bool:
    return any({str(x) for x in (a.get(key) or [])} & wanted for a in audits)


def text_verdict(trace: CheckRun, evidence: CheckRun, sheet: dict[str, Any], fact_id: str) -> str:
    """A text the card states as true: "supported" only when both checkers passed it, neither audit has a blocking
    claim, the trace cites `fact_id` and the evidence check cites one of that fact's quotes."""
    from .trace_check import _is_empirical_claim
    if not trace.audits or not evidence.audits:
        return "unsupported"
    if any(a.get("verdict") in TRACE_BAD for a in trace.audits) or any(
            a.get("verdict") == "contradicted" for a in evidence.audits):
        return "contradicted"
    t_bad = any(a.get("verdict") != "traced" and _is_empirical_claim(a) for a in trace.audits)
    e_bad = any(a.get("verdict") == "unsupported" and _is_empirical_claim(a) for a in evidence.audits)
    traced = [a for a in trace.audits if a.get("verdict") == "traced"]
    backed = [a for a in evidence.audits if a.get("verdict") in ("supported", "hedged_ok")]
    if (trace.passed and evidence.passed and not t_bad and not e_bad
            and _claims_cite(traced, "fact_ids", {fact_id})
            and _claims_cite(backed, "quote_ids", {str(n) for n in evidence_numbers(sheet, fact_id)})):
        return "supported"
    return "unsupported"


def myth_verdict(verdict: str, trace: CheckRun, evidence: CheckRun, sheet: dict[str, Any], fact_id: str) -> str:
    """The myth itself: "contradicted" (verified false) only when the WHOLE myth is false: no audited claim of either
    checker is traced or supported (a compound myth with one true clause, "the ring is about 40 km across and aliens
    built it", would strike a fact out: review 2026-11-03, blocking 1), every empirical claim the trace audited is
    "contradicts" (or "overstated", for a misleading myth) and every one the evidence check audited is
    "contradicted", both checkers failed it, and a claim of each cites the refuting fact (the trace) or one of its
    quotes (the evidence check). "supported" when the checkers found it all true; "unsupported" otherwise."""
    from .trace_check import _is_empirical_claim
    if not trace.audits or not evidence.audits:
        return "unsupported"
    bad = ("contradicts", "overstated") if verdict == "misleading" else ("contradicts",)
    true_t = [a for a in trace.audits if a.get("verdict") == "traced"]
    true_e = [a for a in evidence.audits if a.get("verdict") in ("supported", "hedged_ok")]
    if true_t or true_e:
        return "supported" if trace.passed and evidence.passed else "unsupported"
    emp_t = [a for a in trace.audits if _is_empirical_claim(a) or a.get("verdict") in bad]
    emp_e = [a for a in evidence.audits if _is_empirical_claim(a) or a.get("verdict") == "contradicted"]
    if (emp_t and emp_e and not trace.passed and not evidence.passed
            and all(a.get("verdict") in bad for a in emp_t)
            and all(a.get("verdict") == "contradicted" for a in emp_e)
            and _claims_cite(emp_t, "fact_ids", {fact_id})
            and _claims_cite(emp_e, "quote_ids", {str(n) for n in evidence_numbers(sheet, fact_id)})):
        return "contradicted"
    return "unsupported"


def derive(kind: str, field: dict[str, Any], runs: Sequence[CheckRun], sheet: dict[str, Any]
           ) -> tuple[str, dict[str, str]]:
    """(verdict, parts) from the stored runs, in code. Missing runs make "needs_check"."""
    parts: dict[str, str] = {}
    for part, text in texts(kind, field).items():
        tr = [r for r in runs if r.checker == "trace_check" and r.text == text and r.model.strip()]
        ev = [r for r in runs if r.checker == "evidence_check" and r.text == text and r.model.strip()]
        if len(tr) != 1 or len(ev) != 1:
            parts[part] = "needs_check"
            continue
        if kind == "myths" and part == "myth":
            parts[part] = myth_verdict(field["verdict"], tr[0], ev[0], sheet, field["refuting_fact_id"])
        else:
            parts[part] = text_verdict(tr[0], ev[0], sheet, _target(kind, field))
    vals = set(parts.values())
    if "needs_check" in vals:
        return "needs_check", parts
    if kind == "myths":
        if parts["myth"] == "contradicted" and parts["correction"] == "supported":
            return "supported", parts
        return ("contradicted" if "supported" == parts["myth"] or parts["correction"] == "contradicted"
                else "unsupported"), parts
    return parts["text"], parts


# ------------------------------------------------------------------------------------------------ the check run
Checker = Callable[[dict[str, Any], dict[str, Any]], tuple[Any, float, list[dict[str, Any]], str]]


def _seal(kind: str, field: dict[str, Any], sheet: dict[str, Any], verdict: str, parts: dict[str, str],
          runs: Sequence[CheckRun], found: Sequence[str]) -> dict[str, Any]:
    check = CardCheck(verdict=verdict, parts=parts, runs=list(runs), findings=list(found),
                      checked_at=datetime.now(timezone.utc).isoformat(), facts_sha256=facts_hash(sheet),
                      field_sha256=field_hash(field)).model_dump()
    check["sha256"] = check_hash(check)
    return check


def _served(got: Sequence[str], configured: str) -> str:
    return " + ".join(dict.fromkeys(got)) if got else configured


def check_field(kind: str, field: dict[str, Any], sheet: dict[str, Any], *, trace: Checker | None = None,
                evidence: Checker | None = None, models: dict[str, str] | None = None) -> dict[str, Any]:
    """The field's CardCheck (as a dict): its deterministic findings, then each of its texts through the existing
    fact checker (trace_check.check and trace_check.evidence_check unless given). A checker that cannot run leaves
    the field "needs_check"; nothing here ever passes a field the checkers did not."""
    from . import trace_check
    field = {k: v for k, v in field.items() if k != "check"}
    found = findings(kind, field, sheet)
    if found:
        return _seal(kind, field, sheet, "unsupported", {}, [], found)
    trace = trace or trace_check.check
    evidence = evidence or trace_check.evidence_check
    models = models or trace_check.checker_models()
    runs: list[CheckRun] = []
    from .llm import watch_served
    for text in texts(kind, field).values():
        payload = {"card_copy": [text]}
        try:
            # the model that ANSWERED each call is recorded (llm.watch_served: a budget swap or a fallback serves
            # another model than configured); the configured one only when no call reported (a mocked checker)
            with watch_served() as t_served:
                passed, _, audits, summary = trace(payload, sheet)
            with watch_served() as e_served:
                status, _, e_audits, e_summary = evidence(payload, sheet)
        except Exception as e:  # noqa: BLE001 - a checker that cannot run (no key, quota, outage) fails closed: needs_check
            log.warning("Card field %s %s: the fact checker could not run (%s).", kind, field.get("id"), e)
            return _seal(kind, field, sheet, "needs_check", {}, [], [f"checker unavailable: {e}"[:300]])
        if status == "unavailable":
            return _seal(kind, field, sheet, "needs_check", {}, [], [f"evidence check unavailable: {e_summary}"[:300]])
        # signed here, as each checker's output comes back (record_signing "check_run")
        runs.append(_signed_run(CheckRun(checker="trace_check", text=text,
                                         model=_served(t_served, models["trace_check"]), passed=bool(passed),
                                         audits=list(audits or []), summary=str(summary))))
        runs.append(_signed_run(CheckRun(checker="evidence_check", text=text,
                                         model=_served(e_served, models["evidence_check"]),
                                         passed=status == "pass", audits=list(e_audits or []),
                                         summary=str(e_summary))))
    verdict, parts = derive(kind, field, runs, sheet)
    return _sign_check(kind, field, _seal(kind, field, sheet, verdict, parts, runs, []))


def needs_check(kind: str, field: dict[str, Any], sheet: dict[str, Any], why: str) -> dict[str, Any]:
    """A record that says the field was never checked (no checker available): unusable until check_cards runs."""
    field = {k: v for k, v in field.items() if k != "check"}
    return _seal(kind, field, sheet, "needs_check", {}, [], findings(kind, field, sheet) + [why])


def check_cards(sheet: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """Every card field of `sheet` checked (check_field), the sheet's "cards" replaced; returns the sheet."""
    cards = FactSheetCards(**(sheet.get("cards") or {})).model_dump()
    for kind in KINDS:
        for f in cards[kind]:
            f["check"] = check_field(kind, f, sheet, **kw)
    sheet["cards"] = FactSheetCards(**cards).model_dump()
    return sheet


def recheck_untrusted(sheet: dict[str, Any], **kw: Any) -> int:
    """Every card field of `sheet` whose recorded check holds except for its signature (an old unsigned record, or
    one signed under an earlier FACT_RECORD_KEY) checked again (check_field), in place; returns how many. Nothing is
    re-checked while no usable key is set (nothing could be signed: they would stay untrusted)."""
    from .record_signing import key_problem
    if key_problem():
        return 0
    cards = (sheet or {}).get("cards")
    if not isinstance(cards, dict):
        return 0
    n = 0
    for kind in KINDS:
        for f in cards.get(kind) or []:
            if not isinstance(f, dict):
                continue
            try:
                verify_field(sheet, kind, str(f.get("id")))
            except FieldRefused as e:
                if e.code != "unsigned_check":
                    continue
                log.info("Card field %s %s: its check record is untrusted (%s); checking it again.", kind,
                         f.get("id"), e.reason[:160])
                f["check"] = check_field(kind, f, sheet, **kw)
                n += 1
    return n


# ------------------------------------------------------------------------------------------------ from the writer
def from_draft(draft: DraftCardFields | None, facts: Sequence[dict[str, Any]], quotes: Sequence[dict[str, Any]]
               ) -> dict[str, Any]:
    """The writer's proposed card fields on the kept facts: evidence strings and URLs filled in from the quotes it
    cited (never from its own words), fields on a dropped fact or with an uncited quote dropped, with why."""
    out = FactSheetCards()
    if draft is None:
        return out.model_dump()
    by_id = {f["id"]: f for f in facts}

    def quote(n: int) -> dict[str, Any] | None:
        return quotes[n - 1] if isinstance(n, int) and 1 <= n <= len(quotes) else None

    def held(fid: str, q: dict[str, Any] | None) -> dict[str, Any] | None:
        if q is None or fid not in by_id:
            return None
        return next((e for e in by_id[fid]["evidence"] if e["quote"] == q["quote"][:600] and e["url"] == q["url"]), None)

    for s in draft.statements[: MAX_FIELDS["statements"]]:
        if s.fact_id not in by_id:
            out.dropped.append({"id": s.id, "reason": f"statement on a dropped fact {s.fact_id!r}"})
        elif len(s.text) > CARD_TEXT_MAX:
            out.dropped.append({"id": s.id, "reason": f"statement over {CARD_TEXT_MAX} characters"})
        else:
            out.statements.append(CardStatement(id=s.id, text=" ".join(s.text.split()), fact_id=s.fact_id))
    for m in draft.myths[: MAX_FIELDS["myths"]]:
        e = held(m.refuting_fact_id, quote(m.quote_id))
        if e is None:
            out.dropped.append({"id": m.id, "reason": "myth source is not evidence of its refuting fact"})
        elif len(m.correction) > CARD_TEXT_MAX:
            out.dropped.append({"id": m.id, "reason": f"correction over {CARD_TEXT_MAX} characters"})
        else:
            out.myths.append(CardMyth(id=m.id, myth=" ".join(m.myth.split()), verdict=m.verdict,
                                      refuting_fact_id=m.refuting_fact_id, correction=" ".join(m.correction.split()),
                                      source=Source(title=urlparse(e["url"]).hostname or e["url"], url=e["url"])))
    for q in draft.quotes[: MAX_FIELDS["quotes"]]:
        e = held(q.fact_id, quote(q.quote_id))
        if e is None:
            out.dropped.append({"id": q.id, "reason": "quotation's evidence is not cited by its fact"})
        else:
            out.quotes.append(CardQuote(id=q.id, quotation=" ".join(q.quotation.split()), speaker=q.speaker,
                                        work=q.work, date=q.date, source_url=e["url"], evidence=e["quote"],
                                        fact_id=q.fact_id))
    return out.model_dump()


# ------------------------------------------------------------------------------------------------ what a card reads
def verify_field(sheet: dict[str, Any], kind: str, field_id: str) -> dict[str, Any]:
    """The card field `field_id` of `kind`, only when its recorded check holds now: FieldRefused otherwise. Pure: it
    re-reads the record, its hashes and the deterministic rules, and re-derives the verdict from the stored runs."""
    if kind not in KINDS:
        raise FieldRefused(f"unknown card field kind {kind!r}", "bad_field")
    raw = ((sheet or {}).get("cards") or {}).get(kind) or []
    found = [f for f in raw if isinstance(f, dict) and f.get("id") == field_id] if isinstance(raw, list) else []
    if len(found) != 1:
        raise FieldRefused(f"no single {kind[:-1]} {field_id!r} on the fact sheet", "no_field")
    try:       # the one field, typed (a field over its length limit is refused here, never trimmed)
        obj = _MODEL[kind](**found[0])
    except (ValidationError, TypeError) as e:
        raise FieldRefused(f"{kind[:-1]} {field_id!r} does not validate ({e.__class__.__name__})", "bad_field") from e
    field = obj.model_dump()
    check = field.pop("check")
    if not check:
        raise FieldRefused(f"{kind[:-1]} {field_id!r} has no check record", "unchecked")
    if check["verdict"] != "supported":
        # the deterministic finding, when there is one, is the reason (it says what no evidence holds)
        raise FieldRefused("; ".join(check["findings"]) if check["findings"] else
                           f"{kind[:-1]} {field_id!r}'s verdict is {check['verdict']!r}", "unsupported_field")
    if check["sha256"] != check_hash(check):
        raise FieldRefused(f"{kind[:-1]} {field_id!r}: the check record does not match its hash", "forged_check")
    if check["field_sha256"] != field_hash(field):
        raise FieldRefused(f"{kind[:-1]} {field_id!r} changed after its check", "stale_check")
    if check["facts_sha256"] != facts_hash(sheet):
        raise FieldRefused(f"{kind[:-1]} {field_id!r} was checked against other facts", "stale_check")
    if check["findings"] or findings(kind, field, sheet):
        raise FieldRefused("; ".join(check["findings"] or findings(kind, field, sheet)), "unsupported_field")
    verdict, parts = derive(kind, field, obj.check.runs, sheet)
    if verdict != "supported" or parts != check["parts"]:
        raise FieldRefused(f"{kind[:-1]} {field_id!r}: its checker runs give {verdict!r}, not the recorded "
                           "'supported'", "forged_check")
    # last, and decisive: everything above can be recomputed by whoever edits the sheet; the signatures cannot
    # (operator decision A): a record or run not signed under FACT_RECORD_KEY when its checker ran is untrusted
    why = signature_problem(kind, field_id, check)
    if why:
        raise FieldRefused(f"{kind[:-1]} {field_id!r}'s check record is untrusted: {why}", "unsigned_check")
    return field
