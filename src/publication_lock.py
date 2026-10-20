"""OS-owned publication locks: process exit releases ownership, never a timer."""
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def publication_lock(database_path, run_id):
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
            raise ValueError("Publication is in progress; its owner still holds the upload lock.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
