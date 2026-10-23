"""Continuous improvement: findings are filed per run, recurring ones become proposals, a writing
rule is only put to you after it wins on the eval, adopting and reverting work, your reasons after
Reject/Regenerate are kept, /issue and /ask work from the run's records, and audience maths."""

import json

import pytest

from src import audience, improve
from src.control import make_command_handler


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(improve, "PATCHES_FILE", tmp_path / "prompt_patches.json")
    monkeypatch.setattr(improve, "QUEUE_FILE", tmp_path / "queue.md")
    monkeypatch.setattr("src.free_llm.ready", lambda: False)  # no model: deterministic paths


def _run(db, run_id="run_x", topic="01_topic"):
    db.create_run(run_id, topic)
    return run_id


def test_a_run_files_its_findings_from_notes_and_records(db, tmp_path):
    run_id = _run(db)
    (tmp_path / "release_report.json").write_text(json.dumps({"checks": [
        {"name": "duration", "status": "pass"}, {"name": "asset_rights", "status": "needs_review", "detail": ["m.mp4"]}]}))
    improve.note_problems(["The poll asks a yes/no question"], "structure")
    n = improve.collect_run(db, run_id, tmp_path, {"second_check": {"status": "unavailable"}}, topic_id="01_topic")
    assert n == 3
    classes = {r["klass"] for r in improve.recurring(db, min_count=1)}
    assert {"script.poll", "release.asset_rights", "check.second_unavailable"} <= classes


def test_recurring_script_problem_becomes_a_tested_rule_and_others_a_code_card(db, monkeypatch):
    for i in range(3):
        rid = _run(db, f"run_{i}")
        improve.record(db, "script.poll", f"yes/no poll {i}", "structure", run_id=rid)
        improve.record(db, "release.asset_rights", f"clip {i}", "release", run_id=rid)
    monkeypatch.setattr(improve, "_draft_rule", lambda k, ex: "Ask either/or poll questions whose two options answer them.")
    made = {p["klass"]: p for p in improve.make_proposals(db)}
    assert made["script.poll"]["status"] == "testing" and made["script.poll"]["tier"] == "prompt"
    # protected class: never a writing rule, only a card for a code fix
    assert made["release.asset_rights"]["tier"] == "code" and made["release.asset_rights"]["status"] == "ready"
    assert improve.make_proposals(db) == []  # one open proposal per class


def test_a_rule_is_put_to_you_only_after_it_wins_without_losing_traceability(db, monkeypatch):
    monkeypatch.setattr(improve, "_draft_rule", lambda k, ex: "A rule.")
    for i in range(3):
        improve.record(db, "script.hook", "weak hook", "structure", run_id=_run(db, f"run_{i}"))
    pid = improve.make_proposals(db)[0]["id"]
    cand = f"candidate:{pid}"
    row = lambda topic, v, traced, structure: {"topic": topic, "variant": v, "traced": traced, "structure": structure,  # noqa: E731
                                               "tropes": 0, "length_ok": True}
    rows = [row(t, "current", 0.9, 1) for t in "abc"] + [row(t, cand, 0.9, 0) for t in "abc"]
    assert improve.update_gates(db, rows) == []  # 3 topics: not enough evidence yet
    rows += [row("d", "current", 0.9, 1), row("d", cand, 0.9, 0)]
    assert improve.update_gates(db, rows)[0]["status"] == "ready"
    # a candidate that loses traceability is never ready, however it scores otherwise
    improve._set_status(db, pid, "testing")
    worse = [row(t, cand, 0.7, 0) for t in "abcd"] + [row(t, "current", 0.9, 0) for t in "abcd"]
    assert improve.update_gates(db, worse)[0]["status"] == "not_better"


def test_adopt_and_revert_a_rule(db, monkeypatch):
    monkeypatch.setattr(improve, "_draft_rule", lambda k, ex: "Keep poll options under 24 characters.")
    for i in range(3):
        improve.record(db, "script.poll", "long option", "structure", run_id=_run(db, f"run_{i}"))
    pid = improve.make_proposals(db)[0]["id"]
    h = make_command_handler(db, lambda: None, lambda: "")
    assert "Adopted" in h("impok", pid)
    assert "Keep poll options under 24 characters." in improve.learned_rules()
    assert "Reverted" in h("imprev", pid)
    assert improve.learned_rules() == ""


def test_reason_chip_and_reply_reach_the_regenerated_run(db):
    run_id = _run(db)
    db.record_approval(run_id, "01_topic", 10)
    db.update_approval_decision(run_id, "regenerate", decided_by="t")
    key = db.run_key(run_id)
    h = make_command_handler(db, lambda: None, lambda: "")
    assert f"[run {key}]" in h("rsn", f"{key}:pictures")
    h("whytext", f"{key} the hook shows the wrong temple")
    fb = improve.operator_feedback(db, run_id)
    assert fb == {"reason": "pictures", "text": "the hook shows the wrong temple", "decision": "regenerate"}
    assert any(r["klass"] == "review.pictures" for r in improve.recurring(db, min_count=1))


def test_issue_is_filed_with_its_run_and_class(db):
    run_id = _run(db)
    key = db.run_key(run_id)
    res = make_command_handler(db, lambda: None, lambda: "")("issue", f"run {key} the map label covers the pin")
    assert "Report saved" in res
    issue = db.list_open_issues()[0]
    assert issue["run_id"] == run_id and "map label" in issue["critique"]


def test_ask_answers_from_the_runs_records_without_inventing(db):
    run_id = _run(db)
    db.update_run(run_id, "failed", error="ReleaseBlocked: asset_rights")
    res = make_command_handler(db, lambda: None, lambda: "")("ask", "why did the last run fail?")
    assert "ReleaseBlocked" in res                     # the recorded error, shown as recorded
    assert "AGY" not in res and "nominal" not in res and "LUFS" not in res


def test_audience_rates_use_the_measured_length():
    d = audience.derived({"views": 1000, "reach": 800, "ig_reels_avg_watch_time": 21000, "shares": 8, "saved": 4,
                          "reels_skip_rate": 35.0}, 42.0)
    assert d == {"views": 1000.0, "reach": 800.0, "watch_ratio": 0.5, "skip_rate": 35.0,
                 "share_rate": 0.01, "save_rate": 0.005, "likes": None, "comments": None}


def test_proposals_need_separate_runs_and_open_reports(db, monkeypatch, tmp_path):
    from src import config, improve
    monkeypatch.setattr(improve, "_draft_rule", lambda klass, examples: None)
    for i in range(5):  # one run's drafts rejected five times: one problem, not a recurring one
        improve.record(db, "script.facts", f"claim {i}", "guardrail", run_id="r1")
    for n in range(3):
        db.record_issue(f"iss_{n}", "the bot panel is cut off")
        improve.record(db, "issue.bot", "panel cut off", f"issue:iss_{n}")
    assert [p["klass"] for p in improve.make_proposals(db)] == ["issue.bot"]   # three open linked reports
    monkeypatch.setattr(config, "ISSUES_DIR", tmp_path)
    for n in range(3):
        (tmp_path / f"iss_{n}.md").write_text("- Status: fixed — done\n", encoding="utf-8")
    improve.sync_issue_files(db)
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM findings WHERE klass = 'issue.bot'").fetchone()[0] == 0
