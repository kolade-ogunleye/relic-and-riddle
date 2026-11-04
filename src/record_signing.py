"""
src/record_signing.py
=====================
Signed fact-check records (operator decision A, 2026-11-03).

Every fact-check record the release path or a design card trusts is signed, at the moment its checker runs, with
HMAC-SHA256 under config.FACT_RECORD_KEY over a canonical serialisation of the record's content (JSON, keys
sorted, no whitespace, UTF-8), prefixed by a domain tag and the record's kind, so a signature made for one kind of
record never verifies as another. The hashes the records carried before (checkpoints.fingerprint) bind a record to
its content but can be recomputed by anyone; the signature cannot be made without the key.

Kinds signed here:
  "check_run"    one checker's output (trace_check.check or trace_check.evidence_check) for one subject: a card
                 field's text (card_fields.check_field) or the public copy's fingerprint (fact_evidence.checker_run)
  "card_check"   a card field's whole CardCheck with the field it is for (card_fields.check_field)
  "gate_check"   the fact gate's checker record (fact_evidence.check_record)
  "fact_review"  the release gate's fact-review stamp (fact_evidence.record_fact_review), which carries the
                 "gate_check" record of the checkers' runs on that copy and is refused without it
  "human_review" a person's review of a curated script, who and when (fact_evidence.human_review_record): shown
                 by the release gate, never counted as a fact check (operator decision, 2026-11-04)

Where the key is unset nothing is signed (sign returns ""), and verify refuses every record. How a missing key is
treated is decided by the environment (config.RELIC_ENVIRONMENT, which the production launcher sets to
"production", the same switch the Telegram card reads for "Production" or "Dry run"):
  production   key_problem() names it; fact_evidence.require_signing_key raises when the script is first written
               (script_engine.generate_script), before the writer's or a checker's call is spent, and again before
               the final checks (main._verify_final_copy); the release gate's "facts" check fails with that
               sentence, so nothing publishes;
  development  every record stays untrusted (cards refuse, the gate blocks), with one warning in the log;
  tests        tests/conftest.py sets a test key (FACT_RECORD_KEY is never read from a developer's .env there).

Key rotation: no earlier key is accepted. Once the key changes, every record signed under the old one fails
verification like an unsigned one: the next run re-checks its public copy (main.py, _verify_final_copy), and a fact
sheet's card fields refuse until they are checked again, which happens the next time the sheet is used
(fact_sheet.ensure runs card_fields.recheck_untrusted on a cached sheet; a key under MIN_KEY_BYTES counts as unset).

The key itself is never logged or returned: messages name only FACT_RECORD_KEY.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

log = logging.getLogger(__name__)

DOMAIN = b"relic-and-riddle/fact-record/v1"
KINDS = frozenset(("check_run", "card_check", "gate_check", "fact_review", "human_review"))
SIGNATURE = "signature"
# A key under MIN_KEY_BYTES, or an .env.example placeholder ("your_..."), is treated as unset (review 2026-11-03,
# round 4): it is never used to sign, so every record stays untrusted and production fails closed. 32 bytes is the
# HMAC-SHA256 output size; generate one with: python -c "import secrets; print(secrets.token_hex(32))"
MIN_KEY_BYTES = 32
_warned: set[str] = set()


def _raw_key() -> bytes:
    from . import config
    return str(getattr(config, "FACT_RECORD_KEY", "") or "").strip().encode("utf-8")


def _key() -> bytes:
    """The signing key, or b"" when it is unset, a placeholder or shorter than MIN_KEY_BYTES."""
    raw = _raw_key()
    if len(raw) < MIN_KEY_BYTES or raw.startswith(b"your_"):
        return b""
    return raw


def is_production() -> bool:
    from . import config
    return str(getattr(config, "RELIC_ENVIRONMENT", "") or "").strip().lower() == "production"


def key_problem() -> str:
    """"" when a key is set; else the sentence that says why no record can be trusted."""
    if _key():
        return ""
    where = "in production" if is_production() else "in this environment"
    if _raw_key():   # set, but weak: said as such (the key itself is never shown)
        return (f"FACT_RECORD_KEY is not set {where} to a usable key (it is a placeholder or shorter than "
                f"{MIN_KEY_BYTES} bytes): fact-check records cannot be signed or verified, so none is trusted and "
                "nothing publishes")
    return (f"FACT_RECORD_KEY is not set {where}: fact-check records cannot be signed or verified, so none is "
            "trusted and nothing publishes")


def canonical(content: Any) -> bytes:
    """The record's content as signed: JSON with sorted keys and no whitespace (tuples read as lists)."""
    return json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _content(record: Mapping[str, Any], exclude: Iterable[str]) -> dict[str, Any]:
    drop = {SIGNATURE, *exclude}
    return {k: v for k, v in record.items() if k not in drop}


def sign(kind: str, record: Mapping[str, Any], exclude: Iterable[str] = ()) -> str:
    """The record's signature (hex), or "" when no key is set (an unsigned record is never trusted)."""
    if kind not in KINDS:
        raise ValueError(f"unknown record kind {kind!r}")
    key = _key()
    if not key:
        warn_once("no_key", key_problem())
        return ""
    msg = DOMAIN + b"\n" + kind.encode("ascii") + b"\n" + canonical(_content(record, exclude))
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def signed(kind: str, record: dict[str, Any], exclude: Iterable[str] = ()) -> dict[str, Any]:
    """`record` with its "signature" set (in place; returned)."""
    record.pop(SIGNATURE, None)
    record[SIGNATURE] = sign(kind, record, exclude)
    return record


def problem(kind: str, record: Any, exclude: Iterable[str] = ()) -> str:
    """"" when `record` carries a valid signature of `kind` under the current key; else why it is untrusted."""
    if not isinstance(record, Mapping):
        return "the record is missing"
    why = key_problem()
    if why:
        return why
    got = record.get(SIGNATURE)
    if not isinstance(got, str) or not got:
        warn_once("unsigned", "Fact-check records without a signature are untrusted (written before records were "
                  "signed, or by something other than the checker); they are checked again on the next run.")
        return "the record is not signed"
    try:
        want = sign(kind, record, exclude)
    except (TypeError, ValueError):
        return "the record cannot be serialised"
    if not hmac.compare_digest(got, want):
        warn_once("bad_signature", "A fact-check record's signature does not match: it was changed after its check "
                  "or signed under another FACT_RECORD_KEY; it is untrusted and checked again on the next run.")
        return "the record's signature does not match (changed after its check, or signed under another key)"
    return ""


def warn_once(topic: str, message: str) -> None:
    """One log line per topic per process (the message never carries the key)."""
    if topic in _warned:
        return
    _warned.add(topic)
    (log.error if topic == "no_key" and is_production() else log.warning)(message)
