"""
tests/test_release_gate.py
==========================
The release deployer stops production only when nothing is in flight, and the daemon reports
what the deployer did.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from scripts import release_gate

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)  # 12:00 in Edmonton


@pytest.fixture
def prod_db(db):
    db.set_setting("daemon_schedule", json.dumps({"time": "07:00", "timezone": "America/Edmonton"}))
    return db


def _run(db, run_id, status):
    db.create_run(run_id, db.list_all_topics()[0]["id"])
    db.update_run(run_id, status)


def test_an_idle_production_is_idle(prod_db):
    assert release_gate.check(str(prod_db.db_path), NOW) == []


@pytest.mark.parametrize("status", ["producing", "pending_approval", "approved"])
def test_a_run_in_flight_keeps_production_busy(prod_db, status):
    _run(prod_db, "run_a", status)
    assert any("run_a" in r for r in release_gate.check(str(prod_db.db_path), NOW))


@pytest.mark.parametrize("status", ["published", "simulated", "failed", "cancelled"])
def test_finished_runs_do_not(prod_db, status):
    _run(prod_db, "run_a", status)
    assert release_gate.check(str(prod_db.db_path), NOW) == []


def test_an_open_review_keeps_production_busy(prod_db):
    _run(prod_db, "run_a", "simulated")
    prod_db.record_approval("run_a", prod_db.list_all_topics()[0]["id"])
    assert any("review" in r for r in release_gate.check(str(prod_db.db_path), NOW))


def test_a_post_due_within_the_hour_keeps_production_busy(prod_db):
    with prod_db._conn() as conn:
        for due in (NOW + timedelta(minutes=30), NOW + timedelta(hours=5)):
            conn.execute("INSERT INTO scheduled_posts (run_id, kind, due_at, status, created_at, updated_at) "
                         "VALUES ('r', 'reel', ?, 'pending', ?, ?)", (due.isoformat(), NOW.isoformat(), NOW.isoformat()))
    reasons = release_gate.check(str(prod_db.db_path), NOW)
    assert len(reasons) == 1 and "reel" in reasons[0]


@pytest.mark.parametrize("local, busy", [("06:20", True), ("07:10", True), ("06:10", False), ("07:20", False)])
def test_the_daily_run_window_is_kept_clear(prod_db, local, busy):
    from zoneinfo import ZoneInfo
    hour, minute = (int(x) for x in local.split(":"))
    now = datetime(2026, 10, 23, hour, minute, tzinfo=ZoneInfo("America/Edmonton"))
    assert bool(release_gate.check(str(prod_db.db_path), now)) is busy


def test_a_daemon_job_lease_keeps_production_busy_until_released(prod_db):
    with main.busy_lease(prod_db, "the improvement cycle"):
        assert any("improvement cycle" in r for r in release_gate.check(str(prod_db.db_path)))
    assert not any("improvement" in r for r in release_gate.check(str(prod_db.db_path)))


def test_a_lease_left_by_a_dead_process_is_cleared_at_startup(prod_db):
    prod_db.set_setting("busy:the production run", json.dumps({"pid": 1, "started": NOW.isoformat()}))
    main.clear_busy_leases(prod_db)
    assert release_gate.check(str(prod_db.db_path), NOW) == []


def test_unreadable_state_is_never_idle(tmp_path, capsys):
    assert release_gate.main(["--db", str(tmp_path / "missing.db")]) == 2
    assert json.loads(capsys.readouterr().out)["idle"] is False


def test_the_gate_runs_without_the_application_settings(prod_db):
    # It runs under the deployer's account, which cannot read .env: importing src would load it.
    out = subprocess.run([sys.executable, "-I", str(Path(release_gate.__file__)), "--db", str(prod_db.db_path)],
                         capture_output=True, text=True)
    assert out.returncode in (0, 3), out.stderr
    import re
    source = Path(release_gate.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+src\b", source, re.M)


@pytest.mark.parametrize("outcome, expected", [("deployed", "is running"), ("rolled_back", "was not installed")])
def test_the_daemon_reports_the_deployers_outcome_once(db, monkeypatch, tmp_path, outcome, expected):
    receipt = tmp_path / "release.json"
    receipt.write_text(json.dumps({"tag": "v1.0.0-rc10", "outcome": outcome, "previous": "v1.0.0-rc9",
                                   "running": "v1.0.0-rc9", "reason": "the test suite failed", "at": "2026-10-23T18:00:00Z"}),
                       encoding="utf-8")
    monkeypatch.setenv(main.RELEASE_RECEIPT_ENV, str(receipt))
    sent = []
    bot = SimpleNamespace(notify=lambda text, *a, **k: sent.append(text))
    main.announce_release(db, bot)
    main.announce_release(db, bot)  # a restart without a new deployment says nothing again
    assert len(sent) == 1 and expected in sent[0] and "rc10" in sent[0]


def test_no_receipt_means_no_announcement(db, monkeypatch):
    monkeypatch.delenv(main.RELEASE_RECEIPT_ENV, raising=False)
    sent = []
    main.announce_release(db, SimpleNamespace(notify=lambda *a, **k: sent.append(a)))
    assert sent == []


def test_deploy_command_leaves_a_request_for_the_deployer(db, monkeypatch, tmp_path):
    from src import control
    request, status = tmp_path / "deploy.request", tmp_path / "status.json"
    status.write_text(json.dumps({"Status": "Up to date on v1.0.0-rc10."}), encoding="utf-8")
    monkeypatch.setenv(control.DEPLOY_REQUEST_ENV, str(request))
    monkeypatch.setenv(control.DEPLOY_STATUS_ENV, str(status))
    text = control.request_deploy(db)
    assert json.loads(request.read_text(encoding="utf-8"))["at"]
    assert "requested" in text and "Up to date on v1.0.0-rc10" in text


def test_deploy_command_installs_nothing_outside_production(db, monkeypatch):
    from src import control
    monkeypatch.delenv(control.DEPLOY_REQUEST_ENV, raising=False)
    assert "production only" in control.request_deploy(db)
