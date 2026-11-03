"""Bind a successful factual review to the exact public copy and research file.

Operator decision A (2026-11-03): every record here is signed with HMAC-SHA256 under FACT_RECORD_KEY
(src/record_signing.py) when the checker runs, and verified wherever it is trusted: each checker's run
(checker_run, "check_run"), the gate's checker record (check_record, "gate_check") and the review stamp
(record_fact_review, "fact_review"). current_fact_review (the release gate, and so publish_run) and
checked_fact_review (the design cards) refuse a record without a valid signature, so an old unsigned record, an
edited one or one signed under another key makes the next run check its copy again."""

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


_RUN_KEYS = ("model", "passed", "audits", "summary")
# The review stamp's location fields: script_library re-points them at this checkout's copy of the same sheet (and
# reference), so they are not signed; the content they point at is, by its LF-normalised digests (sheet_lf_sha256,
# reference_sha256), which current_fact_review checks against the file at that path.
STAMP_UNSIGNED = ("sheet_path", "sheet_sha256", "reference_path")


def _lf_sha256(path):
    try:
        raw = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        return ""
    import hashlib
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _run_content(name, public, run):
    return {"checker": name, "public_fingerprint": public, **{k: (run or {}).get(k) for k in _RUN_KEYS}}


def checker_run(checker, public, model, passed, audits, summary):
    """One checker's output for the public copy `public` (its fingerprint), signed as it comes back
    (record_signing "check_run"): call it right after trace_check.check or trace_check.evidence_check returns."""
    from .record_signing import sign
    run = {"model": model, "passed": passed, "audits": audits, "summary": summary}
    run["signature"] = sign("check_run", _run_content(checker, public, run))
    return run


def _run_problem(name, public, run):
    from .record_signing import problem
    if not isinstance(run, dict):
        return f"the {name} run is missing"
    why = problem("check_run", {**_run_content(name, public, run), "signature": run.get("signature")})
    return f"the {name} run: {why}" if why else ""


def check_record(trace, evidence, public):
    """The fact gate's own evidence for a review: each checker's model, pass, audits and summary, bound to the public
    copy's fingerprint by a hash over all of it and signed ("gate_check"). `trace` and `evidence` are the signed
    runs checker_run made (main._verify_final_copy, as trace_check.check and trace_check.evidence_check return); a
    run that carries no valid signature for this copy leaves the record unsigned, and so untrusted."""
    import logging

    from .record_signing import sign
    rec = {"public_fingerprint": public,
           "trace": {k: trace.get(k) for k in _RUN_KEYS + ("signature",)},
           "evidence": {k: evidence.get(k) for k in _RUN_KEYS + ("signature",)}}
    why = _run_problem("trace_check", public, rec["trace"]) or _run_problem("evidence_check", public, rec["evidence"])
    if why:
        logging.getLogger(__name__).warning("Fact gate record left unsigned: %s.", why)
        rec["signature"] = ""
    else:
        rec["signature"] = sign("gate_check", rec)
    rec["sha256"] = fingerprint({k: v for k, v in rec.items() if k != "sha256"})   # over the signature too
    return rec


def record_fact_review(payload, sheet_path, check=None):
    """Binds a passed review to the public copy and the research file. `check` is the checkers' own record
    (check_record); a review stamped without one stays current for the release gate but is never a design card's
    gate record (checked_fact_review)."""
    sheet_path = Path(sheet_path).resolve()
    digest = sha256_file(sheet_path)
    if not digest or payload.get("guardrail_passed") is not True:
        raise ValueError("Successful review and a research file are required")
    payload["fact_review"] = {"public_fingerprint": public_fingerprint(payload),
                              "sheet_path": str(sheet_path), "sheet_sha256": digest,
                              "sheet_lf_sha256": _lf_sha256(sheet_path),
                              "review_rules_fingerprint": review_rules_fingerprint()}
    if check is not None:
        payload["fact_review"]["check"] = check
    if selected_reference(payload.get("scale_fact")):
        payload["fact_review"].update(reference_path=str(REFERENCE_FILE.resolve()),
                                      reference_sha256=sha256_file(REFERENCE_FILE))
    # signed as the review passes (record_signing "fact_review"); unsigned when FACT_RECORD_KEY is unset
    from .record_signing import signed
    signed("fact_review", payload["fact_review"], exclude=STAMP_UNSIGNED)


def unchecked_pass(payload):
    """True when the script stage passed `payload` with no checker run on it: an unreviewed curated script let
    through for a dry run (script_engine.verify_claims / fact_checker.verify, allow_unreviewed_curated). Such a pass
    is never stamped (review 2026-11-03, round 4: a stamp signed under the real key made the release gate treat it
    as checked), so the final copy is checked by the real checkers (main._verify_final_copy) before it is trusted."""
    return payload.get("origin") == "curated" and not payload.get("human_reviewed")


def stamp_problem(review):
    """"" when the review stamp's signature holds (record_signing "fact_review"); else why it is untrusted."""
    from .record_signing import problem
    return problem("fact_review", review, exclude=STAMP_UNSIGNED)


def require_signing_key():
    """Production with no FACT_RECORD_KEY stops here, before a checker call is spent: no record could be signed,
    so nothing would publish (RuntimeError, saying so). Elsewhere a missing key is only logged (record_signing)."""
    from .record_signing import is_production, key_problem, warn_once
    why = key_problem()
    if why and is_production():
        raise RuntimeError(why + ". Set FACT_RECORD_KEY in this environment's .env and restart.")
    if why:
        warn_once("no_key", why)


def checked_fact_review(payload):
    """(True, "") when the payload's fact review is current AND carries the checkers' own record: both checkers'
    models named, their audits present and passing again in code, and the record's hash intact over the current
    public fingerprint. Else (False, why). A review stamped by record_fact_review with no check run is refused."""
    from .trace_check import _is_empirical_claim
    if not _current_unsigned(payload):     # the signatures are checked last, below
        return False, "no current fact review"
    review = payload.get("fact_review") or {}
    rec = review.get("check")
    if not isinstance(rec, dict):
        return False, "the fact review carries no record of a check run"
    if rec.get("sha256") != fingerprint({k: v for k, v in rec.items() if k != "sha256"}):
        return False, "the check record does not match its hash"
    if rec.get("public_fingerprint") != review.get("public_fingerprint"):
        return False, "the check record is for other copy"
    for name in ("trace", "evidence"):
        run = rec.get(name)
        if not isinstance(run, dict) or not str(run.get("model") or "").strip():
            return False, f"the {name} check names no model"
        if run.get("passed") is not True or not isinstance(run.get("audits"), list) or not run["audits"]:
            return False, f"the {name} check has no passing audits"
    if any(a.get("verdict") in ("contradicts", "overstated") or (a.get("verdict") != "traced" and _is_empirical_claim(a))
           for a in rec["trace"]["audits"]):
        return False, "the trace check's audits hold a blocking claim"
    if any(a.get("verdict") == "contradicted" or (a.get("verdict") == "unsupported" and _is_empirical_claim(a))
           for a in rec["evidence"]["audits"]):
        return False, "the evidence check's audits hold a blocking claim"
    # last, and decisive (operator decision A): the gate record, each checker run and the review stamp signed under
    # FACT_RECORD_KEY when the checkers ran; the hashes above can be recomputed by anyone, the signatures cannot
    from .record_signing import problem
    why = (problem("gate_check", rec, exclude=("sha256",))
           or _run_problem("trace_check", rec.get("public_fingerprint"), rec.get("trace"))
           or _run_problem("evidence_check", rec.get("public_fingerprint"), rec.get("evidence"))
           or stamp_problem(review))
    if why:
        return False, f"the check record is untrusted: {why}"
    if not review.get("sheet_lf_sha256") or review["sheet_lf_sha256"] != _lf_sha256(review.get("sheet_path", "")):
        return False, "the check record is untrusted: its signed research digest does not match the research file"
    return True, ""


def fact_review_problem(payload):
    """"" when the payload's fact review is current and signed; else why not, in words (the release gate's detail)."""
    from .record_signing import key_problem
    if key_problem():
        return key_problem()
    if not _current_unsigned(payload):
        return "Factual review must match the public copy and current research file."
    review = payload.get("fact_review") or {}
    why = stamp_problem(review)
    if why:
        return f"The factual review is untrusted ({why}); the next run checks the copy again."
    if not review.get("sheet_lf_sha256") or review["sheet_lf_sha256"] != _lf_sha256(review.get("sheet_path", "")):
        return "The factual review's signed research digest does not match the research file."
    return ""


def current_fact_review(payload):
    """True when the payload's fact review matches its public copy, research file and rules, AND its stamp carries
    a valid signature under FACT_RECORD_KEY (an old unsigned review, an edited one or one signed under another key
    is not current: the pipeline checks the copy again)."""
    try:
        return not fact_review_problem(payload)
    except (AttributeError, TypeError, KeyError):
        return False


def _current_unsigned(payload):
    review = payload.get("fact_review") or {}
    reference_current = (not selected_reference(payload.get("scale_fact")) or
                         review.get("reference_sha256") in _digests(REFERENCE_FILE))
    valid_rules = {review_rules_fingerprint(), "79415fa9c90d9a5027f19d13e77f998cf14dfdf848dc59042819e993438c455d"}
    return bool(reference_current and review.get("review_rules_fingerprint") in valid_rules and
                payload.get("guardrail_passed") is True and review.get("sheet_sha256") and
                review.get("public_fingerprint") == public_fingerprint(payload) and
                review["sheet_sha256"] in _digests(review.get("sheet_path", "")))
