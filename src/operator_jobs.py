"""Cooperative operator jobs. Cancellation never claims a remote request was refunded."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
import threading
from uuid import uuid4

_CANCEL = ContextVar("operator_cancel", default=None)


class JobCancelled(Exception):
    pass


def live_job_count(connection):
    """Read-only reload guard; ambiguous process access must also block stopping."""
    from .process_state import identity
    count = 0
    for row in connection.execute("SELECT value FROM settings WHERE key LIKE 'operator_job:%'"):
        job = json.loads(row[0])
        if job["state"] in {"queued", "running", "cancelling"}:
            observed = identity(job.get("owner_pid", 0))
            if observed is not None and observed in {job.get("owner_identity"), "unknown"}:
                count += 1
    return count


def checkpoint():
    event = _CANCEL.get()
    if event is not None and event.is_set():
        raise JobCancelled("Cancelled by operator")


@contextmanager
def cancellable(event):
    token = _CANCEL.set(event)
    try:
        checkpoint()
        yield
        checkpoint()
    finally:
        _CANCEL.reset(token)


class Jobs:
    def __init__(self, db):
        self.db = db
        self.active = {}
        self.lock = threading.RLock()

    def _save(self, job):
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.db.set_setting("operator_job:" + job["id"], json.dumps(job))

    def submit(self, executor, title, work, complete, resource="", announce=None):
        from .process_state import identity
        with self.lock:
            if resource and any(j[0]["resource"] == resource for j in self.active.values()):
                raise ValueError("A job for this item is already running; open /jobs")
            job = {"id": uuid4().hex[:12], "title": title, "resource": resource,
                   "state": "queued", "owner_pid": os.getpid(), "owner_identity": identity(os.getpid())}
            event = threading.Event()
            self.active[job["id"]] = (job, event)
            self._save(job)

        def run():
            result = None
            try:
                with cancellable(event):
                    with self.lock:
                        job["state"] = "running"
                        self._save(job)
                    result = work()
                state = "completed"
            except JobCancelled:
                state = "cancelled"
            except Exception as exc:
                state = "failed"
                job["error_type"] = type(exc).__name__
            with self.lock:
                job["state"] = "cancelled" if state == "completed" and event.is_set() else state
                self._save(job)
                self.active.pop(job["id"], None)
            complete(job, result)

        try:
            if announce:
                announce(job)
            executor.submit(run)
        except Exception:
            with self.lock:
                self.active.pop(job["id"], None)
                job["state"] = "failed"
                self._save(job)
            raise
        return job["id"]

    def cancel(self, key):
        with self.lock:
            if key not in self.active:
                return False
            job, event = self.active[key]
            event.set()
            job["state"] = "cancelling"
            self._save(job)
            return True

    def recent(self):
        with self.db._conn() as conn:
            rows = conn.execute("SELECT value FROM settings WHERE key LIKE 'operator_job:%'").fetchall()
        jobs = [json.loads(row[0]) for row in rows]
        for job in jobs:
            if job["state"] in {"queued", "running", "cancelling"} and job["id"] not in self.active:
                from .process_state import identity
                observed = identity(job.get("owner_pid", 0))
                job["state"] = "interrupted" if observed is None or observed not in {job.get("owner_identity"), "unknown"} else "unverified"
        return sorted(jobs, key=lambda j: j["updated_at"], reverse=True)[:8]
