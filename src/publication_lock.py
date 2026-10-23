"""OS-owned publication locks: process exit releases ownership, never a timer."""
import hashlib
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path


@contextmanager
def exclusive(database_path, name):
    """Yields True while this process alone holds the named lock, or False (at once) if another
    process holds it. For work that one process should do and others simply skip."""
    with ExitStack() as stack:
        try:
            stack.enter_context(publication_lock(database_path, name))
        except ValueError:  # only the lock's own refusal; errors from the caller's work propagate
            yield False
            return
        yield True


@contextmanager
def publication_lock(database_path, run_id, wait: float = 0):
    """Holds the named OS lock. wait > 0 retries for that many seconds before refusing: the approval tap
    holds a run's lock for a moment while it records the decision, and the worker that publishes right
    after must wait for it, not fail (a real concurrent upload holds it for minutes and is still refused)."""
    import time
    deadline = time.monotonic() + max(0, wait)
    with ExitStack() as stack:
        while True:  # only acquiring is retried; the protected work runs once, after the loop
            try:
                stack.enter_context(_publication_lock(database_path, run_id))
                break
            except _Held:
                if time.monotonic() >= deadline:
                    raise ValueError("Publication is in progress; its owner still holds the upload lock.") from None
                time.sleep(0.5)
        yield


class _Held(Exception):
    pass


@contextmanager
def _publication_lock(database_path, run_id):
    directory = Path(database_path).resolve().parent / "publication_locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(run_id.encode()).hexdigest() + ".lock")
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise _Held() from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
