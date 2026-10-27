"""Bind a successful factual review to the exact public copy and research file."""

from pathlib import Path

from .checkpoints import fingerprint
from .credits import sha256_file
from .trace_check import surfaces, SYSTEM, EVIDENCE_SYSTEM
from .reference_facts import selected_reference, REFERENCE_FILE


def _digests(path):
    """A file's SHA-256 as it is and with Windows line endings made Unix ones: the same research or reference in a
    checkout made before the repository kept LF (production's reference_facts.json, written 2026-10-21) hashed
    otherwise, and a review of it never counted as current (rc57 and rc58 rolled back, 2026-10-27)."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return set()
    import hashlib
    lf = raw.replace(b"\r\n", b"\n")
    return {hashlib.sha256(v).hexdigest() for v in (raw, lf, lf.replace(b"\n", b"\r\n"))}  # either way round


def review_rules_fingerprint():
    return fingerprint({"trace": SYSTEM, "evidence": EVIDENCE_SYSTEM, "citation_validation": 1})


def public_fingerprint(payload):
    return fingerprint([(where, " ".join(text.split())) for where, text in surfaces(payload)])


def record_fact_review(payload, sheet_path):
    sheet_path = Path(sheet_path).resolve()
    digest = sha256_file(sheet_path)
    if not digest or payload.get("guardrail_passed") is not True:
        raise ValueError("Successful review and a research file are required")
    payload["fact_review"] = {"public_fingerprint": public_fingerprint(payload),
                              "sheet_path": str(sheet_path), "sheet_sha256": digest,
                              "review_rules_fingerprint": review_rules_fingerprint()}
    if selected_reference(payload.get("scale_fact")):
        payload["fact_review"].update(reference_path=str(REFERENCE_FILE.resolve()),
                                      reference_sha256=sha256_file(REFERENCE_FILE))


def current_fact_review(payload):
    review = payload.get("fact_review") or {}
    reference_current = (not selected_reference(payload.get("scale_fact")) or
                         review.get("reference_sha256") in _digests(REFERENCE_FILE))
    return bool(reference_current and review.get("review_rules_fingerprint") == review_rules_fingerprint() and
                payload.get("guardrail_passed") is True and review.get("sheet_sha256") and
                review.get("public_fingerprint") == public_fingerprint(payload) and
                review["sheet_sha256"] in _digests(review.get("sheet_path", "")))
