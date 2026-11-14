"""Bind a successful factual review to the exact public copy and research file.

Operator decision A (2026-11-03): every record here is signed with HMAC-SHA256 under FACT_RECORD_KEY
(src/record_signing.py) when the checker runs, and verified wherever it is trusted: each checker's run
(checker_run, "check_run"), the gate's checker record (check_record, "gate_check") and the review stamp
(record_fact_review, "fact_review"). current_fact_review (the release gate, and so publish_run) and
checked_fact_review (the design cards) refuse a record without a valid signature, so an old unsigned record, an
edited one or one signed under another key makes the next run check its copy again.

Operator decision (2026-11-04): the automated checkers run on EVERY script, a curated one a person has marked as
reviewed included. A stamp exists only when they ran and passed: record_fact_review refuses (ValueError) without the
signed check record of their runs on this very copy, and the release gate (fact_review_problem) verifies that record
again. A person's review is its own signed record (human_review_record, "human_review": who and when); the gate
shows it (human_review_shown) and never counts it as a fact check."""

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
    """Binds a passed review to the public copy and the research file. `check` is the checkers' own signed record
    of their runs on this copy (check_record over checker_run's runs, as main._verify_final_copy makes it):
    without one that verifies (both runs signed, passing, for this copy's fingerprint) nothing is stamped and
    ValueError says why (operator decision 2026-11-04: a human review, or a pass with no checker, never is)."""
    sheet_path = Path(sheet_path).resolve()
    digest = sha256_file(sheet_path)
    if not isinstance(payload, dict) or not digest or payload.get("guardrail_passed") is not True:
        raise ValueError("Successful review and a research file are required")
    why = check_record_problem(check, public_fingerprint(payload))
    if why:
        payload.pop("fact_review", None)          # refused: no stamp, not even an old one, is left behind
        raise ValueError(f"No fact review is recorded without the automated checkers' signed, passing record for "
                         f"this copy: {why}")
    payload["fact_review"] = {"public_fingerprint": public_fingerprint(payload),
                              "sheet_path": str(sheet_path), "sheet_sha256": digest,
                              "sheet_lf_sha256": _lf_sha256(sheet_path),
                              "review_rules_fingerprint": review_rules_fingerprint(),
                              "check": check}
    if selected_reference(payload.get("scale_fact")):
        payload["fact_review"].update(reference_path=str(REFERENCE_FILE.resolve()),
                                      reference_sha256=sha256_file(REFERENCE_FILE))
    # signed as the review passes (record_signing "fact_review"), over the check record too
    from .record_signing import signed
    signed("fact_review", payload["fact_review"], exclude=STAMP_UNSIGNED)


def stamp_checked_review(payload, sheet_path, trace_result, evidence_result, models):
    """The stamp after both checkers returned on `payload`'s copy: `trace_result` as trace_check.check returns it
    (passed, confidence, audits, summary), `evidence_result` as trace_check.evidence_check does (status, ...),
    `models` {"trace_check": ..., "evidence_check": ...}. Each run is signed as it came back, then the gate record,
    then the stamp (record_fact_review refuses it when a run did not pass)."""
    t_passed, _, t_audits, t_notes = trace_result
    e_status, _, e_audits, e_notes = evidence_result
    public = public_fingerprint(payload)
    record_fact_review(payload, sheet_path, check=check_record(
        checker_run("trace_check", public, models["trace_check"], t_passed, t_audits, t_notes),
        checker_run("evidence_check", public, models["evidence_check"], e_status == "pass", e_audits, e_notes),
        public))


def unchecked_pass(payload):
    """True when the script stage passed `payload` with no checker run on it: an unreviewed curated script let
    through for a dry run (script_engine.verify_claims / fact_checker.verify, allow_unreviewed_curated). Such a pass
    is never stamped (review 2026-11-03, round 4: a stamp signed under the real key made the release gate treat it
    as checked), so the final copy is checked by the real checkers (main._verify_final_copy) before it is trusted.
    Since 2026-11-04 no script-stage pass is stamped (record_fact_review needs the checkers' record); this names
    the passes that are dry-run only."""
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


def _blocking(name, audits):
    from .trace_check import _is_empirical_claim
    if name == "trace":
        return any(a.get("verdict") in ("contradicts", "overstated")
                   or (a.get("verdict") != "traced" and _is_empirical_claim(a)) for a in audits)
    return any(a.get("verdict") == "contradicted" or (a.get("verdict") == "unsupported" and _is_empirical_claim(a))
               for a in audits)


def check_record_problem(rec, public):
    """"" when `rec` is the checkers' own record of a passing run on the copy whose fingerprint is `public`: both
    checkers' models named, their audits present and passing again in code, the record's hash intact, and (last,
    decisive) the gate record and both runs signed under FACT_RECORD_KEY. Else why not, in words."""
    if not isinstance(rec, dict):
        return "the fact review carries no record of a check run"
    try:
        if rec.get("sha256") != fingerprint({k: v for k, v in rec.items() if k != "sha256"}):
            return "the check record does not match its hash"
    except (TypeError, ValueError):
        return "the check record cannot be read"
    if not public or rec.get("public_fingerprint") != public:
        return "the check record is for other copy"
    for name in ("trace", "evidence"):
        run = rec.get(name)
        if not isinstance(run, dict) or not str(run.get("model") or "").strip():
            return f"the {name} check names no model"
        if run.get("passed") is not True or not isinstance(run.get("audits"), list) or not run["audits"]:
            return f"the {name} check has no passing audits"
        if not all(isinstance(a, dict) for a in run["audits"]):
            return f"the {name} check's audits cannot be read"
    if _blocking("trace", rec["trace"]["audits"]):
        return "the trace check's audits hold a blocking claim"
    if _blocking("evidence", rec["evidence"]["audits"]):
        return "the evidence check's audits hold a blocking claim"
    # last, and decisive (operator decision A): the gate record and each checker run signed under FACT_RECORD_KEY
    # when the checkers ran; the hashes above can be recomputed by anyone, the signatures cannot
    from .record_signing import problem
    why = (problem("gate_check", rec, exclude=("sha256",))
           or _run_problem("trace_check", public, rec.get("trace"))
           or _run_problem("evidence_check", public, rec.get("evidence")))
    return f"the check record is untrusted: {why}" if why else ""


def _review(payload):
    """The payload's stamp when it is a record; None when there is none; False when it is unreadable."""
    if not isinstance(payload, dict):
        return False
    review = payload.get("fact_review")
    if review is None or review == {}:
        return None
    return review if isinstance(review, dict) else False


def checked_fact_review(payload):
    """(True, "") when the payload's fact review is current AND carries the checkers' own record: both checkers'
    models named, their audits present and passing again in code, and the record's hash intact over the current
    public fingerprint. Else (False, why). A review stamped by record_fact_review with no check run is refused."""
    review = _review(payload)
    try:
        if not review or not _current_unsigned(payload):     # the signatures are checked last, below
            return False, "no current fact review"
        why = check_record_problem(review.get("check"), review.get("public_fingerprint"))
        if why:
            return False, why
        why = stamp_problem(review)
        if why:
            return False, f"the check record is untrusted: {why}"
        if not review.get("sheet_lf_sha256") or review["sheet_lf_sha256"] != _lf_sha256(review.get("sheet_path", "")):
            return False, "the check record is untrusted: its signed research digest does not match the research file"
    except (AttributeError, TypeError, KeyError, ValueError):
        return False, "the fact review cannot be read"
    return True, ""


UNREADABLE = "The factual review record cannot be read; the next run checks the copy again."


def fact_review_problem(payload):
    """"" when the payload's fact review is current, signed and carries the automated checkers' own signed record
    of a passing run on this copy; else why not, in words (the release gate's detail). A garbled stamp (not a
    record, or fields of the wrong type) is refused with UNREADABLE, never raised. A person's review
    (human_review) plays no part here."""
    from .record_signing import key_problem
    if key_problem():
        return key_problem()
    review = _review(payload)
    if review is False:
        return UNREADABLE
    try:
        if review is None or not _current_unsigned(payload):
            return "Factual review must match the public copy and current research file."
        why = stamp_problem(review)
        if why:
            return f"The factual review is untrusted ({why}); the next run checks the copy again."
        why = check_record_problem(review.get("check"), review.get("public_fingerprint"))
        if why:
            return (f"The factual review has no passing automated check behind it ({why}); the next run checks the "
                    "copy again.")
        if not review.get("sheet_lf_sha256") or review["sheet_lf_sha256"] != _lf_sha256(review.get("sheet_path", "")):
            return "The factual review's signed research digest does not match the research file."
    except (AttributeError, TypeError, KeyError, ValueError):
        return UNREADABLE
    return ""


def fact_review_shown(payload):
    """The release gate's "facts" detail when it passes: the checkers that ran, never the person's review."""
    rec = (payload.get("fact_review") or {}).get("check") or {}
    return ("Automated fact check passed on this copy and is signed: trace check ("
            f"{rec.get('trace', {}).get('model')}) and evidence check ({rec.get('evidence', {}).get('model')}).")


def human_review_record(topic_id, reviewer, reviewed_at, script_sha256):
    """A person's review of a curated script: who and when, for which topic and which script file (its LF
    SHA-256), signed ("human_review"). It is shown by the release gate and never counts as a fact check."""
    from .record_signing import signed
    return signed("human_review", {"topic_id": str(topic_id), "reviewer": str(reviewer or ""),
                                   "reviewed_at": str(reviewed_at or ""), "script_sha256": str(script_sha256 or ""),
                                   "counts_as_fact_check": False})


def human_review_shown(payload):
    """What the release gate shows of a person's review: None when the script records none; else who, when and
    whether its signature holds. counts_as_fact_check is always False."""
    if not isinstance(payload, dict):
        return None
    rec = payload.get("human_review")
    if rec is None and not payload.get("human_reviewed"):
        return None
    from .record_signing import problem
    why = problem("human_review", rec) if isinstance(rec, dict) else "no signed record of who reviewed it or when"
    rec = rec if isinstance(rec, dict) else {}
    return {"reviewer": str(rec.get("reviewer") or ""), "reviewed_at": str(rec.get("reviewed_at") or ""),
            "verified": not why, "problem": why, "counts_as_fact_check": False}


def human_review_text(shown):
    """The shown human review in one sentence (the release report's facts detail)."""
    if not shown:
        return ""
    who = f"by {shown['reviewer'] or 'an unnamed reviewer'}"
    if shown["reviewed_at"]:
        who += f" at {shown['reviewed_at']}"
    state = "signed" if shown["verified"] else f"not verified: {shown['problem']}"
    return f"Human review {who} ({state}); recorded separately, it is not a fact check."


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


# ---------------------------------------------------------------- QF-110: a review made before a code update
# A stamp is bound to its copy and research file by the code's own fingerprints (review_rules_fingerprint: the
# checkers' prompts; public_fingerprint: what surfaces() reads), so every code update that changed either made each
# review approved before it stale, and an approved booking could never post (production, 2026-11-02 to 11-12). The
# gate is now content-bound: when only those code fingerprints moved, the copy is checked again under the current
# rules, and the result is kept beside the run (RECHECK_FILE, signed "fact_recheck"). Only a passing re-check of the
# very content the operator approved (main.PipelineCoordinator._approved_fingerprint) lets it post. A changed or
# missing fact sheet, a changed reference file, an unsigned or altered stamp, or changed copy still blocks.
RECHECK_FILE = "fact_recheck.json"


def _valid_rules():
    return {review_rules_fingerprint(), "79415fa9c90d9a5027f19d13e77f998cf14dfdf848dc59042819e993438c455d"}


def recheck_sheet(payload):
    """The research file a re-check is made against: a signed stamp's own (its path as script_library re-points it);
    for a stamp made before records were signed, only the topic's own fact sheet (DATA_DIR/fact_sheets/<topic>.json),
    never a path the unsigned record names. None when there is none."""
    review = _review(payload) or {}
    if review.get("signature"):
        return Path(review.get("sheet_path") or "") if review.get("sheet_path") else None
    from . import config
    topic = str(payload.get("topic_id") or "").strip()
    if not topic or Path(topic).name != topic:
        return None
    return config.DATA_DIR / "fact_sheets" / f"{topic}.json"


def drift_only(payload) -> bool:
    """True when the payload's stamp is not current only because of code, so a re-check of the same copy against
    the same research may stand in for it (QF-110):
      - a signed stamp whose signatures hold (its own and its checker record's, for the fingerprint it was stamped
        with) and whose rules or public-copy fingerprint differs from the current code's;
      - a stamp made before records were signed (2026-11-03; production's code until then wrote no signature, no
        checker record and no LF digest): operator decision A has the copy checked again, which the re-check is.
    Either way the stamp's research file must be unchanged (its sha256, and its LF digest where recorded), the
    reference file too when the script uses one, and the guardrail must have passed. A stamp carrying signed fields
    but no signature, a signature that does not hold, or a changed sheet is never re-checked: it blocks."""
    from .record_signing import key_problem, problem
    if key_problem():
        return False
    review = _review(payload)
    if not review:
        return False
    try:
        if payload.get("guardrail_passed") is not True:
            return False
        sheet = recheck_sheet(payload)
        if sheet is None or not review.get("sheet_sha256") or review["sheet_sha256"] not in _digests(sheet):
            return False
        if selected_reference(payload.get("scale_fact")) and review.get("reference_sha256") not in _digests(REFERENCE_FILE):
            return False
        if not review.get("signature"):
            # made before signing: none of the fields signing added may be present without the signature
            return not any(review.get(k) for k in ("check", "sheet_lf_sha256"))
        if not review.get("sheet_lf_sha256") or review["sheet_lf_sha256"] != _lf_sha256(sheet):
            return False
        if stamp_problem(review):
            return False
        rec = review.get("check")
        if not isinstance(rec, dict) or rec.get("public_fingerprint") != review.get("public_fingerprint"):
            return False
        if problem("gate_check", rec, exclude=("sha256",)):
            return False
        current = (review.get("review_rules_fingerprint") in _valid_rules()
                   and review.get("public_fingerprint") == public_fingerprint(payload))
        return not current
    except (AttributeError, TypeError, KeyError, ValueError):
        return False


def recheck_subject(payload, approved):
    """What a re-check is for: the original stamp (its signature, "" for one made before signing), the content the
    operator approved (`approved`, the run's review fingerprint), this copy under the current code, the current rules
    and the research file as it is now (its LF digest)."""
    review = payload.get("fact_review") or {}
    sheet = recheck_sheet(payload)
    return {"stamp_signature": str(review.get("signature") or ""), "approved_fingerprint": str(approved or ""),
            "stamp_sheet_sha256": str(review.get("sheet_sha256") or ""),
            "public_fingerprint": public_fingerprint(payload), "review_rules_fingerprint": review_rules_fingerprint(),
            "sheet_lf_sha256": _lf_sha256(sheet) if sheet else "",
            "reference_sha256": str(review.get("reference_sha256") or "")}


def recheck_record(payload, approved, passed, check=None, notes=""):
    """The re-check's result, signed ("fact_recheck"); a failed one is kept too, so it is not paid for again."""
    from datetime import UTC, datetime

    from .record_signing import signed
    return signed("fact_recheck", {**recheck_subject(payload, approved), "passed": bool(passed), "check": check,
                                   "notes": str(notes or "")[:1000],
                                   "checked_at": datetime.now(UTC).isoformat(timespec="seconds")})


def recheck_matches(payload, rec, approved) -> bool:
    """True when `rec` is a signed re-check made for this exact subject (pass or fail)."""
    from .record_signing import problem
    if not isinstance(rec, dict) or not approved or problem("fact_recheck", rec):
        return False
    return all(rec.get(k) == v for k, v in recheck_subject(payload, approved).items())


def recheck_problem(payload, rec, approved) -> str:
    """"" when `rec` is a signed, passing re-check of this copy under the current rules for the content the
    operator approved (`approved`: the run's review fingerprint as it is now); else why not, in words."""
    if not approved:
        return ("its approval cannot be matched to this copy (the review card's record is missing or the copy, "
                "video or pictures changed since)")
    if not isinstance(rec, dict):
        return "it has not been checked again yet"
    if not recheck_matches(payload, rec, approved):
        return "the re-check on record is for other copy, rules or research, or is not signed"
    if rec.get("passed") is not True:
        return "checked again under the current rules, it did not pass" + (f": {rec['notes']}" if rec.get("notes") else "")
    why = check_record_problem(rec.get("check"), recheck_subject(payload, approved)["public_fingerprint"])
    return f"the re-check has no passing automated check behind it ({why})" if why else ""


class RecheckUnavailable(RuntimeError):
    """A checker gave no verdict on the copy (quota, rate limit, outage, an empty or refused reply): nothing is
    recorded, and the re-check is tried again later (QF-110 review 1: an outage stored as a signed failure blocked the
    booking for good)."""


def run_recheck(payload, approved):
    """Runs both automated checkers again on a copy of `payload` (the stored payload is never changed, so the
    approval's binding to it holds) against the fact sheet the stamp was made with, and returns the signed result.
    A verdict, pass or fail, is returned; a checker that gives none raises RecheckUnavailable (as do its own
    errors: the caller retries later, bounded). A verdict needs reviewed claims: an empty reply is not one."""
    import copy
    import json

    from . import trace_check
    from .llm import watch_served
    sheet = json.loads(Path(recheck_sheet(payload)).read_text(encoding="utf-8"))
    work = copy.deepcopy(payload)
    work.pop("fact_review", None)
    try:
        with watch_served() as t_served:
            t_passed, _, t_audits, t_notes = trace_check.check(work, sheet)
    except Exception as e:  # quota, rate limit, network: no verdict on the copy
        raise RecheckUnavailable(f"trace check unavailable: {type(e).__name__}") from e
    if not t_audits:
        raise RecheckUnavailable(f"trace check gave no verdict: {t_notes}")
    if not t_passed:
        return recheck_record(payload, approved, False, notes=f"trace check: {t_notes}")
    try:
        with watch_served() as e_served:
            e_status, _, e_audits, e_notes = trace_check.evidence_check(work, sheet)
    except Exception as e:
        raise RecheckUnavailable(f"evidence check unavailable: {type(e).__name__}") from e
    if e_status not in ("pass", "fail") or not e_audits:
        raise RecheckUnavailable(f"evidence check gave no verdict: {e_notes}")
    if e_status != "pass":
        return recheck_record(payload, approved, False, notes=f"evidence check: {e_notes}")
    models = trace_check.checker_models()
    served = {"trace_check": t_served, "evidence_check": e_served}
    models = {k: " + ".join(dict.fromkeys(served[k])) if served[k] else v for k, v in models.items()}
    public = public_fingerprint(payload)
    check = check_record(checker_run("trace_check", public, models["trace_check"], t_passed, t_audits, t_notes),
                         checker_run("evidence_check", public, models["evidence_check"], True, e_audits, e_notes),
                         public)
    why = check_record_problem(check, public)
    return recheck_record(payload, approved, not why, check=check, notes=why)
