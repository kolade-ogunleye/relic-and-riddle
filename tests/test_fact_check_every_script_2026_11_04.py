"""Operator decision (2026-11-04): the automated fact checker runs on EVERY script, a curated one a person has marked
as reviewed included. A human review is recorded separately (who and when) and never stands in for the check. A
signed fact-review stamp exists only when the checkers ran and passed, and the release gate requires the stamp to
carry their signed check record and verifies it. Checkers are mocked; records are signed under the test key."""
import copy
import json
from types import SimpleNamespace

import pytest

from src import config, record_signing
from src.fact_evidence import (
    check_record,
    checked_fact_review,
    checker_run,
    current_fact_review,
    fact_review_problem,
    human_review_shown,
    public_fingerprint,
    record_fact_review,
)

AUDIT = {"claim": "T", "where": "title", "fact_ids": [], "claim_type": "rhetorical_framing", "verdict": "traced",
         "note": ""}


def signed_check(payload):
    pub = public_fingerprint(payload)
    return check_record(checker_run("trace_check", pub, "m1", True, [AUDIT], "ok"),
                        checker_run("evidence_check", pub, "m2", True, [dict(AUDIT, verdict="supported",
                                                                             quote_ids=[])], "ok"), pub)


def sheet_file(tmp_path):
    sp = tmp_path / "facts.json"
    sp.write_text('{"facts": []}', encoding="utf-8")
    return sp


def facts_check(tmp_path, payload):
    from src.release import write_release_report
    rep = write_release_report({"master_video_path": str(tmp_path / "none.mp4"), "script_payload": payload},
                               dry_run=True)
    return next(c for c in rep["checks"] if c["name"] == "facts")


def hand_stamp(payload, sheet, check=None):
    """A stamp built and signed under the real key without record_fact_review (an insider's or an old one)."""
    from src.credits import sha256_file
    from src.fact_evidence import STAMP_UNSIGNED, _lf_sha256, review_rules_fingerprint
    review = {"public_fingerprint": public_fingerprint(payload), "sheet_path": str(sheet),
              "sheet_sha256": sha256_file(sheet), "sheet_lf_sha256": _lf_sha256(sheet),
              "review_rules_fingerprint": review_rules_fingerprint()}
    if check is not None:
        review["check"] = check
    payload["fact_review"] = record_signing.signed("fact_review", review, exclude=STAMP_UNSIGNED)
    return payload


# ------------------------------------------------------------------ a human-reviewed script gets the automated check
def test_a_human_reviewed_curated_script_is_not_stamped_until_the_checkers_pass(db, tmp_path, curated_payload,
                                                                               monkeypatch):
    import main
    from src import trace_check

    class Engine:
        progress = None

        def generate_script(self, topic, revision_notes="", previous=None):
            return dict(curated_payload, human_reviewed=True)

        def verify_claims(self, p, topic=None, allow_unreviewed_curated=False):
            return True, p, "Curated script (human reviewed); the automated fact check runs on its final copy."

        def _load_curated(self, topic_id):
            return None
    data = tmp_path / "data"
    (data / "fact_sheets").mkdir(parents=True)
    tid = db.list_all_topics()[0]["id"]
    (data / "fact_sheets" / f"{tid}.json").write_text('{"facts": []}', encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", data)
    for dry_run in (True, False):
        run_id = f"run_{int(dry_run)}"
        db.create_run(run_id, tid)
        c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
        c.db, c.script_engine = db, Engine()
        c._check_cancel = lambda run_id: None
        c.telegram_bot = SimpleNamespace(update_progress_card=lambda *a, **k: None)
        out = c._write_and_check_script({"id": tid, "title": "T"}, run_id, tmp_path, dry_run=dry_run)
        assert out["guardrail_passed"] and out["human_reviewed"]
        assert "fact_review" not in out and not current_fact_review(out)      # the review is no fact check
        calls = []
        monkeypatch.setattr(trace_check, "check", lambda p, s: calls.append("trace") or (True, 0, [AUDIT], "ok"))
        monkeypatch.setattr(trace_check, "evidence_check", lambda p, s: calls.append("evidence") or (
            "pass", 0, [dict(AUDIT, verdict="supported", quote_ids=[])], "ok"))
        main.PipelineCoordinator._verify_final_copy(None, out, {"id": tid})   # the same path as every script
        assert calls == ["trace", "evidence"]
        assert current_fact_review(out) and checked_fact_review(out) == (True, "")
        assert out["fact_review"]["check"]["trace"]["model"]


def test_a_human_reviewed_script_failing_the_checkers_is_refused(tmp_path, curated_payload, monkeypatch):
    import main
    from src import trace_check
    (tmp_path / "fact_sheets").mkdir()
    (tmp_path / "fact_sheets" / "x.json").write_text('{"facts": []}', encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    p = dict(curated_payload, human_reviewed=True, guardrail_passed=True)
    monkeypatch.setattr(trace_check, "check", lambda *a: (False, 0, [dict(AUDIT, verdict="contradicts")], "wrong"))
    with pytest.raises(ValueError, match="failed fact verification"):
        main.PipelineCoordinator._verify_final_copy(None, p, {"id": "x"})
    assert not current_fact_review(p) and "fact_review" not in p


def test_the_offline_checker_claims_no_confidence_for_a_human_review(curated_payload):
    from src.fact_checker import ClaimFactChecker
    passed, confidence, audits, summary = ClaimFactChecker().verify(dict(curated_payload, human_reviewed=True))
    assert passed and confidence == 0.0 and audits == [] and "automated fact check runs" in summary


# ------------------------------------------------------------------ a stamp without a check record is refused
def test_record_fact_review_refuses_without_a_check_record(tmp_path):
    sp = sheet_file(tmp_path)
    for origin, reviewed in (("curated", False), ("curated", True), ("claude", None)):
        p = {"guardrail_passed": True, "title": "T", "origin": origin, "human_reviewed": reviewed,
             "fact_review": {"stale": True}}
        with pytest.raises(ValueError, match="no record of a check run"):
            record_fact_review(p, sp)
        assert "fact_review" not in p and not current_fact_review(p)
    unsigned = {"guardrail_passed": True, "title": "T"}     # check_record handed plain dicts: no checker ran
    pub = public_fingerprint(unsigned)
    rec = check_record({"model": "m1", "passed": True, "audits": [AUDIT], "summary": ""},
                       {"model": "m2", "passed": True, "audits": [dict(AUDIT, verdict="supported")],
                        "summary": ""}, pub)
    with pytest.raises(ValueError, match="not signed"):
        record_fact_review(unsigned, sp, check=rec)
    failed = {"guardrail_passed": True, "title": "T"}
    pub = public_fingerprint(failed)
    rec = check_record(checker_run("trace_check", pub, "m1", False, [dict(AUDIT, verdict="contradicts")], ""),
                       checker_run("evidence_check", pub, "m2", True, [dict(AUDIT, verdict="supported")], ""), pub)
    with pytest.raises(ValueError, match="no passing audits"):
        record_fact_review(failed, sp, check=rec)


def test_the_release_gate_refuses_a_signed_stamp_with_no_check_record(tmp_path):
    p = hand_stamp({"guardrail_passed": True, "title": "T", "origin": "curated", "human_reviewed": True},
                   sheet_file(tmp_path))
    assert record_signing.problem("fact_review", p["fact_review"], exclude=("sheet_path", "sheet_sha256",
                                                                            "reference_path")) == ""
    why = fact_review_problem(p)
    assert "no passing automated check" in why and "no record of a check run" in why
    assert not current_fact_review(p) and not checked_fact_review(p)[0]
    assert facts_check(tmp_path, p)["status"] == "fail"


# ------------------------------------------------------------------ a check record of another payload is refused
def test_a_check_record_of_another_payload_is_refused(tmp_path):
    sp = sheet_file(tmp_path)
    a = {"guardrail_passed": True, "title": "The Eye of the Sahara"}
    b = {"guardrail_passed": True, "title": "Aliens Built the Ring"}
    rec_a = signed_check(a)
    with pytest.raises(ValueError, match="other copy"):
        record_fact_review(b, sp, check=rec_a)
    assert "fact_review" not in b
    hand_stamp(b, sp, check=copy.deepcopy(rec_a))          # signed stamp around A's genuine record
    why = fact_review_problem(b)
    assert "other copy" in why and not current_fact_review(b)
    assert facts_check(tmp_path, b)["status"] == "fail"
    record_fact_review(a, sp, check=rec_a)                  # A's own record still stamps A
    assert current_fact_review(a) and fact_review_problem(a) == ""
    c = copy.deepcopy(b)                                    # A's whole stamp moved onto B
    c["fact_review"] = copy.deepcopy(a["fact_review"])
    assert not current_fact_review(c)


# ------------------------------------------------------------------ a garbled stamp is refused, not crashed on
@pytest.mark.parametrize("garbled", [
    "garbage", 5, ["a"], True,
    {"public_fingerprint": None, "sheet_sha256": 5},
    {"public_fingerprint": None, "sheet_sha256": ["x"], "sheet_path": 7, "review_rules_fingerprint": {}},
])
def test_a_garbled_stamp_is_refused_not_crashed_on(tmp_path, garbled):
    p = {"guardrail_passed": True, "title": "x", "fact_review": garbled}
    why = fact_review_problem(p)
    assert isinstance(why, str) and why
    assert current_fact_review(p) is False and checked_fact_review(p)[0] is False
    assert facts_check(tmp_path, p)["status"] == "fail"


def test_a_garbled_check_record_inside_a_current_stamp_is_refused(tmp_path):
    p = {"guardrail_passed": True, "title": "T"}
    record_fact_review(p, sheet_file(tmp_path), check=signed_check(p))
    for check in ("x", 3, [], {"trace": "x", "evidence": None}, {"sha256": object()}):
        q = copy.deepcopy({k: v for k, v in p.items() if k != "fact_review"})
        q["fact_review"] = dict(p["fact_review"], check=check)
        why = fact_review_problem(q)
        assert isinstance(why, str) and why and not current_fact_review(q)
    assert fact_review_problem("not a payload") and not current_fact_review(None)


# ------------------------------------------------------------------ the human review is recorded and shown
def test_the_human_review_is_recorded_signed_and_shown_but_never_counted(tmp_path, monkeypatch, checked_stamp):
    from src import script_engine
    curated = tmp_path / "scripts"
    curated.mkdir()
    (curated / "t1.json").write_text(json.dumps({"_reviewed_by_human": True, "_reviewed_by": "Operator A",
                                                 "_reviewed_at": "2026-11-04T09:30:00Z", "title": "T"}),
                                     encoding="utf-8")
    (curated / "t2.json").write_text(json.dumps({"_reviewed_by_human": False, "title": "T"}), encoding="utf-8")
    monkeypatch.setattr(script_engine, "CURATED_SCRIPTS_DIR", curated)
    rec = script_engine.curated_human_review("t1")
    assert rec["reviewer"] == "Operator A" and rec["reviewed_at"] == "2026-11-04T09:30:00Z"
    assert rec["topic_id"] == "t1" and rec["counts_as_fact_check"] is False and len(rec["signature"]) == 64
    assert script_engine.curated_human_review("t2") is None and script_engine.curated_human_review("none") is None

    p = {"guardrail_passed": True, "title": "T", "origin": "curated", "human_reviewed": True, "human_review": rec}
    unchecked = facts_check(tmp_path, p)                     # reviewed, never checked: the gate fails
    assert unchecked["status"] == "fail" and "Operator A" in unchecked["detail"]
    assert unchecked["human_review"]["verified"] is True
    checked_stamp(p, sheet_file(tmp_path))
    shown = facts_check(tmp_path, p)
    assert shown["status"] == "pass"
    assert "Automated fact check passed" in shown["detail"] and "mock-trace" in shown["detail"]
    assert "Human review by Operator A at 2026-11-04T09:30:00Z (signed)" in shown["detail"]
    assert "not a fact check" in shown["detail"]
    assert shown["human_review"] == {"reviewer": "Operator A", "reviewed_at": "2026-11-04T09:30:00Z",
                                     "verified": True, "problem": "", "counts_as_fact_check": False}
    # the person's review plays no part in the verdict: the same stamp passes with no review, or an edited one
    assert fact_review_problem({k: v for k, v in p.items() if k not in ("human_review", "human_reviewed")}) == ""
    edited = copy.deepcopy(p)
    edited["human_review"]["reviewer"] = "Someone Else"
    assert human_review_shown(edited)["verified"] is False
    shown_edited = facts_check(tmp_path, edited)
    assert shown_edited["status"] == "pass" and "not verified" in shown_edited["detail"]
    bare = dict(p)
    bare.pop("human_review")                                 # flagged reviewed with no record of who or when
    assert human_review_shown(bare)["verified"] is False and "no signed record" in human_review_shown(bare)["problem"]
    assert human_review_shown({"title": "T"}) is None


def test_the_release_status_is_unchanged_by_a_human_review(tmp_path, checked_stamp):
    """No new manual step: the human review adds no needs_review check; the facts check is pass or fail only."""
    from src.release import write_release_report
    from src.fact_evidence import human_review_record
    p = checked_stamp({"guardrail_passed": True, "title": "T"}, sheet_file(tmp_path))
    run = {"master_video_path": str(tmp_path / "none.mp4"), "script_payload": p}
    before = [(c["name"], c["status"]) for c in write_release_report(run, dry_run=True)["checks"]]
    p["human_reviewed"], p["human_review"] = True, human_review_record("t", "A", "2026-11-04", "0" * 64)
    after = [(c["name"], c["status"]) for c in write_release_report(run, dry_run=True)["checks"]]
    assert before == after
