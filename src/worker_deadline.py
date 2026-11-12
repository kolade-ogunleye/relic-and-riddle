"""The run deadline (QF-74).

A run's production is bounded by RUN_DEADLINE_MINUTES, counted from when its process claims it (claim_run) and
never while a human decision is awaited. Two layers:

  1. In the worker, at every stage boundary (PipelineCoordinator._check_cancel): past the deadline, a run that is
     still being made stops as timed_out, keeps its files (a re-render continues from them), its topic goes back in
     the queue, its progress card says why, and one Needs-you notice is sent.
  2. In the supervisor that launched the worker (run_in_worker, the bot's monitor): a worker whose production time
     passes the deadline plus RUN_DEADLINE_GRACE_MINUTES without stopping itself (a stage that never reaches a
     boundary) has its own process tree stopped; then its producing run is marked timed_out the same way and an
     upload it interrupted is held as publish_unknown for reconciliation, never retried.

Only the supervisor's own child is ever stopped, identified by its process handle; the runs it may change are the
ones that child claimed under the random attempt id the supervisor gave it (RELIC_WORKER_ATTEMPT).
"""
import html
import json
import logging
import os
import signal
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from . import config
from .operator_jobs import JobCancelled

log = logging.getLogger(__name__)

OWNER_PREFIX = "run_owner:"  # main.RUN_OWNER_PREFIX
STARTED_PREFIX = "run_started:"  # when this run's production last started (claim_run)
NOTICE_PREFIX = "run_deadline_notice:"
WAITING = frozenset({"pending_approval", "timeout"})  # a human decision is awaited: no production time counts

_started: dict[str, float] = {}
_stopped: set[str] = set()  # runs this process stopped at their deadline: every later boundary stops them again
_lock = threading.Lock()


class RunDeadlineExceeded(JobCancelled):
    """A run's production passed its deadline; raised at a stage boundary like an operator's cancel."""


# ------------------------------------------------------------ in the worker
def mark_started(run_id: str) -> None:
    """Starts the run's production clock in this process (each claim, so a resume starts a new one, and a run
    stopped earlier in this process and claimed again is no longer stopped)."""
    with _lock:
        _started[run_id] = monotonic()
        _stopped.discard(run_id)


def production_seconds(run_id: str) -> float | None:
    with _lock:
        started = _started.get(run_id)
    return None if started is None else monotonic() - started


def overdue(run_id: str) -> bool:
    elapsed = production_seconds(run_id)
    return elapsed is not None and elapsed > config.RUN_DEADLINE_MINUTES * 60


def mark_stopped(run_id: str) -> None:
    with _lock:
        _stopped.add(run_id)


def stopped_here(run_id: str) -> bool:
    """Whether this process stopped the run at its deadline (so a stage that swallowed the stop meets it again)."""
    with _lock:
        return run_id in _stopped


def requeue_topic(db, run: dict[str, Any]) -> bool:
    """Puts a timed-out run's topic back in the queue, only while that run is what holds it in production: in a dual
    run the Reel may already be scheduled or posted when its documentary stops, and that topic stays as it is."""
    if not run.get("topic_id"):
        return False
    with db._conn() as conn:
        return bool(conn.execute("UPDATE topics SET status = 'queued', updated_at = ? WHERE id = ? AND "
                                 "status = 'in_production' AND last_run_id = ?",
                                 (datetime.now(UTC).isoformat(), run["topic_id"], run["run_id"])).rowcount)


def forget(db, run_id: str, keep_start: bool = False) -> None:
    """Drops a run's deadline bookkeeping once the run has ended: its notice key, and its production start unless
    `keep_start` (its review is still undecided: the booking that follows reads the start, missed_slot_notice)."""
    keys = (NOTICE_PREFIX + run_id,) if keep_start else (NOTICE_PREFIX + run_id, STARTED_PREFIX + run_id)
    with db._conn() as conn:
        conn.execute(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(keys))})", keys)


def release(db, run_id: str) -> None:
    """At release_run: the start is kept while the run waits for your decision, and dropped otherwise."""
    run = db.get_run(run_id) or {}
    forget(db, run_id, keep_start=run.get("status") in WAITING)


def kept_line(run_id: str, run: dict[str, Any]) -> str:
    from .resume_points import kept_phrase, worth_resuming
    run_dir = config.OUTPUT_DIR / run_id
    try:
        if worth_resuming(run_dir, run):
            return f"Its files are kept: Re-render continues from {kept_phrase(run_dir, run) or 'what it made'}."
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.debug("Could not tell what %s keeps: %s", run_id, e)
    return "It stopped before its script passed the fact check, so the topic is simply made again."


def notice_once(db, run_id: str, text: str, notify: Callable[..., Any]) -> bool:
    """The one Needs-you notice for a run stopped at its deadline, whichever process stops it first."""
    key = NOTICE_PREFIX + run_id
    if db.get_setting(key):
        return False
    db.set_setting(key, datetime.now(UTC).isoformat())
    notify(text, group="needs")
    return True


def stop_reason(minutes: float) -> str:
    return f"Passed its {minutes:g}-minute production deadline"


def deadline_notice(label: str, minutes: float, kept: str, grace: float | None = None, publishing: bool = False,
                    requeued: bool = True) -> str:
    """The Needs-you notice. `minutes` is the deadline; `grace` is given when the supervisor stopped the worker
    (no stage boundary was reached in the grace minutes after the deadline)."""
    if grace is None:
        how = f"passed its {minutes:g}-minute production deadline and stopped at the next stage"
    else:
        how = (f"passed its {minutes:g}-minute production deadline and no stage finished in the {grace:g} minutes "
               "after it, so its worker was stopped")
    if publishing:
        return (f"⏱ <b>Run stopped at its deadline</b>\n\n<b>{html.escape(label)}</b> {how} during its upload. "
                "It may have posted: check the platform, then answer Posted or Not posted on its card. The topic "
                "stays held until then; nothing is retried.")
    topic = (" The topic is back in the queue." if requeued else
             " Its topic is left as it is: this run was not what held it in production.")
    return (f"⏱ <b>Run stopped at its deadline</b>\n\n<b>{html.escape(label)}</b> {how}. Nothing was posted. "
            f"{kept}{topic}")


# ------------------------------------------------------------ in the supervisor
def owned_runs(db, attempt: str) -> list[dict[str, Any]]:
    """The runs the worker started under `attempt` has claimed (released claims are cleared and not listed)."""
    if db is None or not attempt:
        return []
    with db._conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE ?", (OWNER_PREFIX + "%",)).fetchall()
    runs = []
    for row in rows:
        try:
            owner = json.loads(row["value"] or "{}")
        except ValueError:
            continue
        if isinstance(owner, dict) and owner.get("attempt") == attempt:
            run = db.get_run(row["key"][len(OWNER_PREFIX):])
            if run:
                runs.append(run)
    return runs


def launch_options() -> dict[str, Any]:
    """A worker in its own session on POSIX, so stopping its group never reaches the daemon's."""
    return {} if os.name == "nt" else {"start_new_session": True}


def stop_worker_tree(proc) -> None:
    """Stops this supervisor's own child and every process it started, then reaps the child."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30, check=False)
        if result.returncode and proc.poll() is None:
            raise RuntimeError("Could not stop the timed-out worker's process tree")
    else:
        os.killpg(proc.pid, signal.SIGKILL)  # its own session (launch_options), never the daemon's group
    proc.wait(timeout=30)


def supervise(proc, limit: float, db, attempt: str) -> tuple[Any, Any, bool]:
    """Waits for the worker like communicate(), and stops it once its production time passes `limit` seconds.
    Production time stops while every run it claimed waits for a human decision, and starts again from zero
    after one (what follows a decision is new work: a regeneration, a re-cut or the upload).
    Returns (stdout, stderr, stopped). `db` None: this process's database, opened once the worker outlives a poll."""
    if not 0 < limit < float("inf"):
        raise ValueError("The worker's limit must be a positive number of seconds")
    elapsed, waiting = 0.0, False
    while True:
        tick = config.WORKER_POLL_SECONDS if waiting else max(0.001, min(config.WORKER_POLL_SECONDS, limit - elapsed))
        started = monotonic()
        try:
            stdout, stderr = proc.communicate(timeout=tick)
            return stdout, stderr, False
        except subprocess.TimeoutExpired:
            pass
        if not waiting:
            elapsed += monotonic() - started
        try:
            if db is None:  # opened here, so a failed open is retried at the next poll
                from .db import PipelineDatabase
                db = PipelineDatabase()
            runs = owned_runs(db, attempt)
            now_waiting = bool(runs) and all(r["status"] in WAITING for r in runs)
        except Exception as e:  # noqa: BLE001 - any failure to read is an unknown state, never a reason to stop
            # Unknown state: a worker is never stopped on a guess. Counting carries on as it was, and the stop waits
            # for a poll that can read the runs (a review may have begun meanwhile).
            log.warning("Could not read the worker's runs (%s); it is not stopped on an unknown state.", e)
            continue
        if waiting and not now_waiting:
            elapsed = 0.0  # a decision was made: what follows is new work with its own deadline
        waiting = now_waiting
        if not waiting and elapsed >= limit:
            log.error("Worker PID %s passed its %.0f-minute limit without stopping; stopping its process tree.",
                      proc.pid, limit / 60)
            stop_worker_tree(proc)
            stdout, stderr = proc.communicate(timeout=30)
            return stdout, stderr, True


def finalize_stopped_worker(db, attempt: str, notify: Callable[..., Any], minutes: float, grace: float,
                            finish_card: Callable[..., Any] | None = None) -> list[str]:
    """After supervise() stopped a worker: its producing run becomes timed_out (topic queued, files kept) and an
    upload it interrupted becomes publish_unknown (topic held); its progress card ends with the reason; one notice
    per run. Runs in any other state (a review, a finished post, another worker's runs) are left as they are."""
    try:
        progress = json.loads(db.get_setting("active_progress") or "{}")
    except ValueError:
        progress = {}
    changed: list[str] = []
    now = datetime.now(UTC).isoformat()
    for run in owned_runs(db, attempt):
        run_id, old = run["run_id"], run["status"]
        if old not in ("producing", "publishing"):
            continue
        new = "timed_out" if old == "producing" else "publish_unknown"
        error = (stop_reason(minutes) + " and was stopped by its supervisor." if old == "producing" else
                 "Stopped during its upload by the run deadline; reconcile the platform before retrying.")
        with db._conn() as conn:
            if not conn.execute("UPDATE runs SET status = ?, error = ?, updated_at = ? WHERE run_id = ? AND status = ?",
                                (new, error, now, run_id, old)).rowcount:
                continue
        requeued = False
        if old == "producing":
            requeued = requeue_topic(db, run)
        else:
            try:
                db.record_topic_status(run["topic_id"], "publish_unknown", run_id=run_id)
            except KeyError:
                log.warning("Run %s has no topic to hold.", run_id)
        if finish_card and progress.get("run_id") == run_id and progress.get("message_id"):
            try:
                finish_card(progress["message_id"], progress.get("topic_title") or run_id, run_id,
                            success=False, error=stop_reason(minutes))
            except (OSError, RuntimeError, ValueError, TypeError) as e:  # a courtesy; the run's state is already right
                log.debug("Could not finish the progress card of %s: %s", run_id, e)
        db.clear_active_progress(run_id)
        from .stage_timer import file_findings
        file_findings(db, run_id, config.OUTPUT_DIR / run_id)  # its process cannot file them any more
        label = db.run_label(run_id) if hasattr(db, "run_label") else run_id
        notice_once(db, run_id, deadline_notice(label, minutes, kept_line(run_id, run), grace=grace,
                                                publishing=old == "publishing", requeued=requeued), notify)
        forget(db, run_id)  # its process cannot release it any more
        log.warning("Run %s was stopped at its deadline: %s -> %s.", run_id, old, new)
        changed.append(run_id)
    return changed


# ------------------------------------------------------------ the slot a late run missed
def missed_slot_notice(db, run: dict[str, Any], booked, now: datetime, taken_days, notify: Callable[..., Any]) -> bool:
    """Says so when an approved Reel is booked later than the slot it was made for: the first free slot after its
    production started (run_started, else the run's creation). Never moves anything; True when it said so."""
    from . import post_schedule
    try:  # a notice never fails the booking it describes
        raw = db.get_setting(STARTED_PREFIX + run["run_id"]) or run.get("created_at")
        started = datetime.fromisoformat(str(raw))
        started = started if started.tzinfo else started.replace(tzinfo=UTC)
        intended = post_schedule.next_reel_slot(started, taken_days)
        if booked <= intended:
            return False
        today = intended.astimezone(post_schedule.tz()).date() == now.astimezone(post_schedule.tz()).date()
        label = db.run_label(run["run_id"])
    except (TypeError, ValueError, KeyError, AttributeError) as e:
        log.warning("Could not tell whether %s missed its slot: %s", run.get("run_id"), e)
        return False
    heading = "Missed today's slot" if today else "Missed its first slot"
    notify(f"⏰ <b>{heading}</b> ({html.escape(post_schedule.fmt(intended))}): {html.escape(label)} was approved "
           f"too late for it, so it is booked for {html.escape(post_schedule.fmt(booked))}.")  # the studio stream, as _reschedule's missed-slot notice
    return True
