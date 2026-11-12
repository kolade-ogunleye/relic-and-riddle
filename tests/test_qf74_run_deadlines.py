"""QF-74: a run deadline that stops a late run cleanly with its files kept, a missed-slot notice, stage budgets.

The same file collects on the tree before the fix (the new module is imported softly), so its tests fail there
instead of erroring at collection.
"""
import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import main
from src import config, control, post_schedule, stage_timer

try:
    from src import worker_deadline as deadline
except ImportError:  # the tree before QF-74
    deadline = None


@pytest.fixture(autouse=True)
def fresh_clocks():
    """Each test starts with no production clock and no run stopped in this process."""
    if deadline is not None:
        deadline._started.clear()
        deadline._stopped.clear()
    yield


def own(db, run_id, topic, attempt="attempt-a", state="producing"):
    """A run claimed by the worker started under `attempt`, as claim_run records it."""
    db.create_run(run_id, topic)
    db.record_topic_status(topic, "in_production", run_id)
    db.update_run(run_id, state)
    db.set_setting("run_owner:" + run_id, json.dumps({"attempt": attempt, "pid": 12345}))


class Worker:
    """A worker process that never ends by itself; each communicate() advances a fake clock by its timeout."""

    def __init__(self, clock):
        self.clock, self.dead, self.waits, self.pid, self.returncode = clock, False, [], 4242, None

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        if self.dead:
            return None, ""
        self.waits.append(timeout)
        self.clock[0] += timeout
        raise subprocess.TimeoutExpired("worker", timeout)


def _stoppable(monkeypatch, clock):
    def stop(proc):
        proc.dead, proc.returncode = True, -9
    monkeypatch.setattr(deadline, "stop_worker_tree", stop)
    monkeypatch.setattr(deadline, "monotonic", lambda: clock[0])


# ------------------------------------------------------------ the supervisor
def test_a_worker_that_outruns_its_limit_is_stopped_and_reported_not_left_running(monkeypatch, db):
    clock = [0.0]
    proc = Worker(clock)
    monkeypatch.setattr(main.subprocess, "Popen", lambda *a, **kw: proc)

    def escaped(*a, **kw):  # the old run_in_worker: subprocess.run(timeout=...) raised past the daemon's handler
        raise subprocess.TimeoutExpired("worker", 1)
    monkeypatch.setattr(main.subprocess, "run", escaped)
    if deadline is not None:
        _stoppable(monkeypatch, clock)
    try:
        result = main.run_in_worker("reel", True, False, timeout=90, db=db)
    except (subprocess.TimeoutExpired, TypeError):
        result = None
    assert result and result["status"] == "timed_out" and result["stopped"] is True
    assert proc.dead and clock[0] == 90


def test_the_default_limit_is_the_deadline_plus_its_grace_and_the_attempt_reaches_the_worker(monkeypatch, db):
    seen = {}

    def launch(cmd, **kw):
        seen["env"] = kw.get("env") or {}
        return SimpleNamespace(returncode=0, pid=1, communicate=lambda **k: (None, ""))
    monkeypatch.setattr(main.subprocess, "Popen", launch)
    if deadline is not None:
        real = deadline.supervise

        def spy(proc, limit, database, attempt):
            seen.update(limit=limit, attempt=attempt)
            return real(proc, limit, database, attempt)
        monkeypatch.setattr(deadline, "supervise", spy)
    main.run_in_worker("reel", True, False, db=db)
    assert seen.get("limit") == (config.RUN_DEADLINE_MINUTES + config.RUN_DEADLINE_GRACE_MINUTES) * 60
    assert seen["env"].get("RELIC_WORKER_ATTEMPT") == seen["attempt"] and len(seen["attempt"]) == 32


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_an_invalid_limit_never_starts_a_worker(monkeypatch, seconds):
    launches = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda *a, **kw: launches.append(a))
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **kw: launches.append(a))
    with pytest.raises(ValueError):
        main.run_in_worker("reel", True, False, timeout=seconds)
    assert not launches


def test_review_waits_never_count_and_a_decision_starts_a_new_budget(monkeypatch, db):
    clock = [0.0]
    _stoppable(monkeypatch, clock)
    monkeypatch.setattr(config, "WORKER_POLL_SECONDS", 30)
    own(db, "r1", "01_topic")
    script = iter(["producing", "pending_approval", "pending_approval", "pending_approval", "approved",
                   "approved", "approved", "approved"])

    class Run(Worker):
        def communicate(self, timeout=None):
            if not self.dead:  # the run moves on while the supervisor waits
                db.update_run("r1", next(script))
            return super().communicate(timeout)
    proc = Run(clock)
    _, _, stopped = deadline.supervise(proc, 90, db, "attempt-a")
    # 60 s of production (the poll in which the review began counts), three polls waiting for you that never
    # count, then the decision: 90 s of new work from zero, and the worker is stopped
    assert stopped and proc.waits == [30] * 8 and clock[0] == 240


def test_a_busy_database_never_stops_a_worker_on_an_unknown_state(monkeypatch, db):
    import sqlite3
    clock = [0.0]
    _stoppable(monkeypatch, clock)
    monkeypatch.setattr(config, "WORKER_POLL_SECONDS", 30)
    reads = []

    def busy(*a):  # locked for five polls, then readable: the worker is still producing
        reads.append(clock[0])
        if len(reads) <= 5:
            raise sqlite3.OperationalError("database is locked")
        return []
    monkeypatch.setattr(deadline, "owned_runs", busy)
    proc = Worker(clock)
    assert deadline.supervise(proc, 60, db, "attempt-a")[2]
    assert len(reads) == 6 and proc.dead  # stopped only at the first poll that could read its state


def test_a_failed_database_open_is_retried_and_never_stops_a_healthy_worker(monkeypatch, db):
    import sqlite3

    import src.db
    clock = [0.0]
    _stoppable(monkeypatch, clock)
    monkeypatch.setattr(config, "WORKER_POLL_SECONDS", 30)
    own(db, "r1", "01_topic", attempt="attempt-a", state="pending_approval")  # waiting for you
    opens = []

    def open_db(*a, **kw):
        opens.append(clock[0])
        if len(opens) <= 3:
            raise sqlite3.OperationalError("database is locked")
        return db
    monkeypatch.setattr(src.db, "PipelineDatabase", open_db)

    class Reviewed(Worker):
        def communicate(self, timeout=None):
            if len(opens) >= 4 and len(self.waits) >= 8:
                return "done", ""  # the worker finished by itself after its review
            return super().communicate(timeout)
    proc = Reviewed(clock)
    # the limit passes while the database cannot be opened; the run was in review all along: never stopped
    assert deadline.supervise(proc, 60, None, "attempt-a") == ("done", "", False) and not proc.dead
    assert len(opens) == 4  # opened once it could be, then kept


def test_run_in_worker_never_stops_a_worker_when_its_database_cannot_be_opened(monkeypatch):
    import sqlite3

    import src.db
    clock = [0.0]
    _stoppable(monkeypatch, clock)
    monkeypatch.setattr(config, "WORKER_POLL_SECONDS", 30)

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(src.db, "PipelineDatabase", locked)

    class Finishes(Worker):
        def communicate(self, timeout=None):
            if len(self.waits) >= 6:
                return None, ""
            return super().communicate(timeout)
    proc = Finishes(clock)
    monkeypatch.setattr(main.subprocess, "Popen", lambda *a, **kw: proc)
    result = main.run_in_worker("reel", True, False, timeout=60)
    assert result["status"] == "crashed" and not proc.dead  # its own end, never a kill on an unknown state


def test_any_error_reading_the_state_is_unknown_never_a_kill(monkeypatch, db):
    clock = [0.0]
    _stoppable(monkeypatch, clock)
    monkeypatch.setattr(config, "WORKER_POLL_SECONDS", 30)
    own(db, "r1", "01_topic", state="pending_approval")
    real, reads = deadline.owned_runs, []

    def flaky(*a):  # a malformed record or a library bug, not a database error
        reads.append(1)
        if len(reads) <= 3:
            raise RuntimeError("unexpected")
        return real(*a)
    monkeypatch.setattr(deadline, "owned_runs", flaky)

    class Reviewed(Worker):
        def communicate(self, timeout=None):
            if len(self.waits) >= 6:
                return "done", ""
            return super().communicate(timeout)
    proc = Reviewed(clock)
    assert deadline.supervise(proc, 60, db, "attempt-a") == ("done", "", False) and not proc.dead


def test_a_worker_that_ends_by_itself_is_never_stopped(monkeypatch, db):
    stopped = []
    monkeypatch.setattr(deadline, "stop_worker_tree", stopped.append)
    proc = SimpleNamespace(pid=1, communicate=lambda timeout=None: ("out", "err"))
    assert deadline.supervise(proc, 60, db, "attempt-a") == ("out", "err", False) and not stopped


def test_only_the_supervised_process_tree_is_stopped(monkeypatch):
    calls = []
    proc = SimpleNamespace(pid=54321, poll=lambda: None, wait=lambda timeout: calls.append(("wait", timeout)))
    if deadline.os.name == "nt":
        monkeypatch.setattr(deadline.subprocess, "run", lambda cmd, **kw: calls.append(tuple(cmd))
                            or SimpleNamespace(returncode=0))
        expected = ("taskkill", "/PID", "54321", "/T", "/F")
    else:
        monkeypatch.setattr(deadline.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
        expected = (54321, deadline.signal.SIGKILL)
    deadline.stop_worker_tree(proc)
    assert calls == [expected, ("wait", 30)]


def test_an_exited_worker_is_not_signalled_again(monkeypatch):
    calls = []
    monkeypatch.setattr(deadline.subprocess, "run", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(deadline.os, "killpg", lambda *a: calls.append(a), raising=False)
    deadline.stop_worker_tree(SimpleNamespace(poll=lambda: 0))
    assert not calls


def test_a_stopped_worker_times_out_only_its_own_run_keeps_its_files_and_says_so_once(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    (tmp_path / "late").mkdir()
    (tmp_path / "late" / "voiceover.mp3").write_bytes(b"made")
    own(db, "late", "01_topic")
    own(db, "other", "02_topic", attempt="attempt-b")
    own(db, "review", "03_topic", state="pending_approval")
    db.set_setting("active_progress", json.dumps({"run_id": "late", "message_id": 77, "topic_title": "Topic 1"}))
    notes, cards = [], []
    notify = lambda text, **kw: notes.append((text, kw))
    finish = lambda *a, **kw: cards.append((a, kw))
    assert deadline.finalize_stopped_worker(db, "attempt-a", notify, 180, 60, finish) == ["late"]
    assert deadline.finalize_stopped_worker(db, "attempt-a", notify, 180, 60, finish) == []
    assert db.get_run("late")["status"] == "timed_out" and db.get_topic_by_id("01_topic")["status"] == "queued"
    assert (tmp_path / "late" / "voiceover.mp3").read_bytes() == b"made"  # its files are kept
    assert db.get_run("other")["status"] == "producing"  # another worker's run
    assert db.get_run("review")["status"] == "pending_approval"  # a review waiting for you
    assert len(notes) == 1 and notes[0][1] == {"group": "needs"}
    assert "180-minute production deadline" in notes[0][0] and "60 minutes after it" in notes[0][0]
    assert cards == [((77, "Topic 1", "late"), {"success": False, "error": "Passed its 180-minute production deadline"})]
    assert "180-minute" in db.get_run("late")["error"] and "240" not in db.get_run("late")["error"]
    assert not db.get_setting("active_progress")
    assert not db.get_setting("run_deadline_notice:late") and not db.get_setting("run_started:late")


def test_an_interrupted_upload_is_held_for_reconciliation_never_requeued(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    own(db, "upload", "01_topic", state="publishing")
    notes = []
    deadline.finalize_stopped_worker(db, "attempt-a", lambda text, **kw: notes.append(text), 180, 60)
    assert db.get_run("upload")["status"] == "publish_unknown"
    assert db.get_topic_by_id("01_topic")["status"] == "publish_unknown"
    assert "check the platform" in notes[0]


# ------------------------------------------------------------ in the worker: a clean stop at a stage boundary
@pytest.fixture
def coordinator(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    coord = object.__new__(main.PipelineCoordinator)
    coord.db = db
    coord.topic_engine = SimpleNamespace(record_status=lambda tid, status, run_id=None:
                                         db.record_topic_status(tid, status, run_id))
    coord.alerts, coord.cards = [], []
    coord._alert = lambda text, buttons=None, group=None: coord.alerts.append((text, group))
    coord.telegram_bot = SimpleNamespace(finish_progress_card=lambda *a, **kw: coord.cards.append(kw) or True)
    coord.progress_msg_id = 9
    return coord


def _late(coordinator, monkeypatch, run_id="late", state="producing"):
    db = coordinator.db
    db.create_run(run_id, "01_topic")
    db.update_run(run_id, state)
    db.record_topic_status("01_topic", "in_production", run_id)
    (config.OUTPUT_DIR / run_id).mkdir()
    (config.OUTPUT_DIR / run_id / "voiceover.mp3").write_bytes(b"made")
    main.claim_run(db, run_id)
    if deadline is not None:  # its production started a second more than the deadline ago
        deadline._started[run_id] -= config.RUN_DEADLINE_MINUTES * 60 + 1
    return db


def test_a_run_past_its_deadline_stops_at_the_next_stage_boundary_and_keeps_its_files(coordinator, monkeypatch):
    db = _late(coordinator, monkeypatch)
    with pytest.raises(main.JobCancelled) as stop:  # a stage boundary stops it as an operator's cancel does
        coordinator._check_cancel("late")
    assert type(stop.value).__name__ == "RunDeadlineExceeded"
    assert db.get_run("late")["status"] == "timed_out" and db.get_topic_by_id("01_topic")["status"] == "queued"
    assert (config.OUTPUT_DIR / "late" / "voiceover.mp3").read_bytes() == b"made"
    assert coordinator.cards and coordinator.cards[0]["success"] is False  # the progress card says why
    assert [group for _, group in coordinator.alerts] == ["needs"]
    with pytest.raises(main.JobCancelled):  # a stage that swallowed the stop meets it again at the next boundary
        coordinator._check_cancel("late")
    assert len(coordinator.alerts) == 1 and db.get_run("late")["status"] == "timed_out"  # no second notice


def test_the_deadline_bookkeeping_ends_with_the_run(db):
    db.create_run("r1", "01_topic")
    main.claim_run(db, "r1")
    db.set_setting("run_deadline_notice:r1", "sent")
    assert db.get_setting("run_started:r1")
    main.release_run(db, "r1")
    with db._conn() as conn:
        left = conn.execute("SELECT key FROM settings WHERE key IN ('run_started:r1', 'run_deadline_notice:r1')").fetchall()
    assert not left


def _documentary(coordinator, monkeypatch):
    monkeypatch.setattr(config, "documentary_enabled", lambda: True)
    monkeypatch.setattr(deadline, "overdue", lambda run_id: run_id == "doc1")
    coordinator.progress_msg_id = None  # the Reel's card has ended; the documentary has none
    coordinator.telegram_bot = SimpleNamespace(send_progress_card=lambda *a, **kw: None,
                                               finish_progress_card=lambda *a, **kw: False)
    return coordinator.produce_deep_dive({"id": "01_topic", "title": "Topic 1"}, parent_run_id="reel1",
                                         run_id="doc1")


def test_a_documentary_stopped_without_a_card_sends_one_message(coordinator, monkeypatch):
    db = coordinator.db
    db.record_topic_status("01_topic", "in_production", "doc1")
    result = _documentary(coordinator, monkeypatch)
    assert result["status"] == "timed_out" and db.get_run("doc1")["status"] == "timed_out"
    assert len(coordinator.alerts) == 1 and coordinator.alerts[0][1] == "needs"
    assert "Documentary failed" not in coordinator.alerts[0][0]


def test_a_dual_runs_documentary_stop_never_requeues_a_topic_whose_reel_is_booked(coordinator, monkeypatch):
    db = coordinator.db
    db.create_run("reel1", "01_topic")
    db.update_run("reel1", "scheduled")
    db.record_topic_status("01_topic", "scheduled", "reel1")
    result = _documentary(coordinator, monkeypatch)
    assert result["status"] == "timed_out"
    assert db.get_topic_by_id("01_topic")["status"] == "scheduled"  # the Reel's booking stands
    assert len(coordinator.alerts) == 1 and "left as it is" in coordinator.alerts[0][0]


def test_a_run_claimed_again_in_the_same_process_gets_a_new_deadline(coordinator, monkeypatch):
    db = _late(coordinator, monkeypatch)
    with pytest.raises(main.JobCancelled):
        coordinator._check_cancel("late")
    db.update_run("late", "producing")  # resumed in this process
    main.claim_run(db, "late")
    coordinator._check_cancel("late")  # a fresh clock: not stopped again
    assert db.get_run("late")["status"] == "producing"


def test_an_interrupted_run_leaves_no_deadline_bookkeeping(db):
    db.create_run("crashed", "01_topic")
    db.set_setting("run_started:crashed", "2026-11-11T09:00:00+00:00")
    db.set_setting("run_deadline_notice:crashed", "sent")
    with db._conn() as conn:  # older than the startup grace, no live process record
        conn.execute("UPDATE runs SET updated_at = '2026-11-01T00:00:00+00:00' WHERE run_id = 'crashed'")
    assert main.reconcile_interrupted_runs(db) == ["crashed"]
    with db._conn() as conn:
        assert not conn.execute("SELECT 1 FROM settings WHERE key LIKE '%:crashed' AND key LIKE 'run_%' "
                                "AND key NOT LIKE 'run_owner:%' AND key NOT LIKE 'run_mode:%'").fetchall()


def test_a_review_waiting_at_release_keeps_its_start_for_the_booking(db, tmp_path, curated_payload, monkeypatch, eastern):
    """A review resumed after its worker released the run: the booking still reads the run's production start,
    not its creation days earlier, so an approval in time is not told it missed a slot; then the start is dropped."""
    monkeypatch.setattr("src.release.require_release", lambda *a, **k: {"status": "pass"})
    c = main.PipelineCoordinator(db, schedule_posts=True)
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "m.mp4").write_bytes(b"0")
    curated_payload.update(topic_id="01_topic", human_reviewed=True)
    db.create_run("r1", "01_topic")
    with db._conn() as conn:  # made days ago, re-rendered this morning
        conn.execute("UPDATE runs SET created_at = '2026-11-08T14:00:00+00:00' WHERE run_id = 'r1'")
    main.claim_run(db, "r1")
    db.set_setting("run_started:r1", datetime(2026, 11, 11, 9, 0, tzinfo=eastern).isoformat())
    db.update_run("r1", "pending_approval", script_payload=curated_payload, master_video_path=run_dir / "m.mp4",
                  thumbnail_cover_path=run_dir / "m.mp4", thumbnail_grid_path=run_dir / "m.mp4")
    main.release_run(db, "r1")  # the worker hands the waiting review on
    assert db.get_setting("run_started:r1")
    alerts = []
    c._alert = lambda text, buttons=None, group=None: alerts.append(text)
    c.telegram_bot = SimpleNamespace(stamp_review_outcome=lambda *a: True)
    c.schedule_run("r1", now=datetime(2026, 11, 11, 10, 0, tzinfo=eastern))
    assert not [a for a in alerts if "Missed" in a]
    assert not db.get_setting("run_started:r1")


def test_a_review_or_an_upload_past_the_deadline_is_never_stopped(coordinator, monkeypatch):
    db = _late(coordinator, monkeypatch, state="pending_approval")
    coordinator._check_cancel("late")
    assert db.get_run("late")["status"] == "pending_approval" and not coordinator.alerts


def test_a_run_inside_its_deadline_carries_on(coordinator, monkeypatch, db):
    db.create_run("fresh", "01_topic")
    main.claim_run(db, "fresh")
    coordinator._check_cancel("fresh")
    assert db.get_run("fresh")["status"] == "producing" and not coordinator.alerts


def test_a_timed_out_run_is_made_again_from_its_files(coordinator, monkeypatch):
    db = _late(coordinator, monkeypatch)
    with pytest.raises(main.JobCancelled):
        coordinator._check_cancel("late")
    monkeypatch.setattr("src.resume_points.worth_resuming", lambda *a, **kw: True)
    assert coordinator.rerender_refusal("late") is None and coordinator.remake_args("late") == []
    assert control.remakeable(db.get_run("late"))
    assert control.STATUS_WORDS["timed_out"] == "stopped at its deadline"


def test_the_claim_carries_the_supervisors_attempt_and_the_production_start(db, monkeypatch):
    monkeypatch.setattr(config, "WORKER_ATTEMPT_ID", "attempt-z")
    db.create_run("r1", "01_topic")
    main.claim_run(db, "r1")
    assert json.loads(db.get_setting("run_owner:r1"))["attempt"] == "attempt-z"
    assert [r["run_id"] for r in deadline.owned_runs(db, "attempt-z")] == ["r1"]
    assert datetime.fromisoformat(db.get_setting("run_started:r1")).tzinfo is not None


# ------------------------------------------------------------ settings
def test_deadline_settings_are_validated_and_read_from_the_workers_environment():
    assert config.run_deadline_limits({"RUN_DEADLINE_MINUTES": "100", "RUN_DEADLINE_GRACE_MINUTES": "20"}) == (6000, 7200)
    for bad in ("0", "-5", "nan", "inf"):
        with pytest.raises(ValueError):
            config.run_deadline_limits({"RUN_DEADLINE_MINUTES": bad})
    assert config.run_deadline_limits({}) == (config.RUN_DEADLINE_MINUTES * 60,
                                              (config.RUN_DEADLINE_MINUTES + config.RUN_DEADLINE_GRACE_MINUTES) * 60)


# ------------------------------------------------------------ the missed slot
@pytest.fixture
def eastern(monkeypatch):
    monkeypatch.setattr(config, "POST_TIMEZONE", "America/New_York")
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    monkeypatch.setattr(config, "REEL_POST_TIMES", ["12:00", "19:00"])
    return post_schedule.tz()


def test_a_run_approved_after_its_slot_says_which_slot_it_missed(db, eastern):
    started = datetime(2026, 11, 11, 9, 0, tzinfo=eastern)  # Wednesday: its slot is 12:00 or 19:00 that day
    now = datetime(2026, 11, 11, 21, 0, tzinfo=eastern)
    db.create_run("late", "01_topic")
    db.set_setting("run_started:late", started.isoformat())
    booked = post_schedule.next_reel_slot(now)
    notes = []
    assert deadline.missed_slot_notice(db, db.get_run("late"), booked, now, set(), lambda t, **k: notes.append(t))
    intended = post_schedule.next_reel_slot(started)
    assert len(notes) == 1 and "Missed today's slot" in notes[0]
    assert post_schedule.fmt(intended) in notes[0] and post_schedule.fmt(booked) in notes[0]


def test_a_run_that_makes_its_slot_or_starts_after_it_is_not_told_it_missed_one(db, eastern):
    db.create_run("ok", "01_topic")
    started = datetime(2026, 11, 11, 9, 0, tzinfo=eastern)
    db.set_setting("run_started:ok", started.isoformat())
    in_time = datetime(2026, 11, 11, 10, 0, tzinfo=eastern)
    notes = []
    say = lambda t, **k: notes.append(t)
    assert not deadline.missed_slot_notice(db, db.get_run("ok"), post_schedule.next_reel_slot(in_time), in_time, set(), say)
    # started after the day's slot: tomorrow's is the one it was made for
    db.set_setting("run_started:ok", datetime(2026, 11, 11, 20, 0, tzinfo=eastern).isoformat())
    late = datetime(2026, 11, 11, 23, 0, tzinfo=eastern)
    assert not deadline.missed_slot_notice(db, db.get_run("ok"), post_schedule.next_reel_slot(late), late, set(), say)
    assert not notes


def test_booking_a_late_approval_sends_the_missed_slot_notice(db, tmp_path, curated_payload, monkeypatch, eastern):
    monkeypatch.setattr("src.release.require_release", lambda *a, **k: {"status": "pass"})
    c = main.PipelineCoordinator(db, schedule_posts=True)
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "m.mp4").write_bytes(b"0")
    curated_payload.update(topic_id="01_topic", human_reviewed=True)
    db.create_run("r1", "01_topic")
    db.update_run("r1", "pending_approval", script_payload=curated_payload, master_video_path=run_dir / "m.mp4",
                  thumbnail_cover_path=run_dir / "m.mp4", thumbnail_grid_path=run_dir / "m.mp4")
    db.set_setting("run_started:r1", datetime(2026, 11, 11, 9, 0, tzinfo=eastern).isoformat())
    alerts = []
    c._alert = lambda text, buttons=None, group=None: alerts.append(text)
    c.telegram_bot = SimpleNamespace(stamp_review_outcome=lambda *a: True)
    c.schedule_run("r1", now=datetime(2026, 11, 11, 21, 0, tzinfo=eastern))
    assert [a for a in alerts if "Missed today's slot" in a]


# ------------------------------------------------------------ stage budgets
def test_a_stage_over_150_percent_of_its_budget_warns_once_and_files_one_finding(db, tmp_path, caplog):
    db.create_run("measured", "01_topic")
    timer = stage_timer.StageTimer(tmp_path / "measured")
    timer.record("master_render_attempt1", 900)
    timer.record("master_render_attempt2", 451, ok=False)  # 1351 s > 1.5 x 15 min: a failed attempt counts
    timer.record("master_render_attempt3", 5)
    assert caplog.text.count("Stage budget") == 1 and "150%" in caplog.text
    assert stage_timer.file_findings(db, "measured", tmp_path / "measured") == 1
    assert stage_timer.file_findings(db, "measured", tmp_path / "measured") == 0
    with db._conn() as conn:
        rows = conn.execute("SELECT klass, detail FROM findings WHERE run_id = 'measured'").fetchall()
    assert [r["klass"] for r in rows] == ["operations.stage_budget.render"] and "15-minute budget" in rows[0]["detail"]


def test_exactly_150_percent_is_within_budget_and_unbudgeted_steps_never_count(db, tmp_path, caplog):
    db.create_run("edge", "01_topic")
    timer = stage_timer.StageTimer(tmp_path / "edge")
    timer.record("voiceover", 720)  # 8 min x 1.5
    timer.record("music_and_bed", 9000)
    timer.record("image_prep_wait", 9000)
    assert "Stage budget" not in caplog.text and stage_timer.file_findings(db, "edge", tmp_path / "edge") == 0


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_an_impossible_duration_is_not_recorded_and_never_fails_the_run(tmp_path, value):
    stage_timer.StageTimer(tmp_path).record("voiceover", value)
    assert not (tmp_path / "timings.json").exists()


def test_the_weekly_digest_gives_each_stage_p50_p90_and_deadline_stops(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    for run_id, seconds in (("a", 60), ("b", 180), ("c", 120)):
        db.create_run(run_id, "01_topic")
        stage_timer.StageTimer(tmp_path / run_id).record("voiceover", seconds)
    stage_timer.StageTimer(tmp_path / "b").record("voiceover", 60)  # a retry adds to its run's total
    db.update_run("c", "timed_out")
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    text = "\n".join(stage_timer.weekly_lines(db, since))
    assert "Voice: 2.0 / 3.6 of 8 (3 runs)" in text and "run deadline: 1" in text
    from src import improve
    assert "Voice: 2.0 / 3.6 of 8 (3 runs)" in improve.digest_text(db)


def test_the_committed_budgets_are_the_specified_minutes():
    policy = stage_timer.budgets()
    assert policy["minutes"] == {"script": 20, "voice": 8, "pictures": 25, "render": 15, "qa": 5, "upload": 5}
    assert policy["warning_multiplier"] == 1.5
    assert stage_timer.category("qa_attempt3", policy) == "qa" and stage_timer.category("review_proxy", policy) is None
