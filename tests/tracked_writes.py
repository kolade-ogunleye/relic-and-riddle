"""The tracked-tree guard (QF-111): no code a test runs may write a path the release owns (anything in the checkout
that git does not ignore: a tracked file, or a new file beside them).

A host's runtime once rewrote tracked files in place (data/music/library.json on every pick, the photo pools'
credits.json on every review), so production's checkout drifted from the release it runs and the release deployer
refused every later release that touched one of them. Runtime state lives in gitignored places (src/host_state.py,
the database); this guard keeps it that way for every code path the suite reaches:

- in the test process, an audit hook (sys.addaudithook) refuses any open for writing, rename, replace, truncate or
  delete of such a path and records it; the test that did it fails with the file and the code that wrote it;
- across processes (a script a test starts), the session compares `git status` (tracked and untracked, not ignored)
  at its end with its start and fails the run when such a path changed.

The guard is off when the tests do not run in a git checkout (then there is no tracked tree to protect).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Bound once: a test may stub subprocess.run on the module it tests (it is the same module object).
_run = subprocess.run
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _norm(path) -> str | None:
    if isinstance(path, int):
        return None
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(path)))
    except (TypeError, ValueError):
        return None


def tracked_files(root: Path = ROOT) -> set[str] | None:
    """The tracked files of the checkout at `root`, normalised; None when it is not a git checkout."""
    try:
        out = _run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {_norm(root / name) for name in os.fsdecode(out.stdout).split("\0") if name}


class TrackedWriteRefused(PermissionError):
    """Raised inside the write itself, so the tracked file is never changed."""


def is_write(event: str, args: tuple) -> list:
    """The paths an audit event would change, or [] when it changes none."""
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if isinstance(mode, str):
            return [path] if any(c in mode for c in "wax+") else []
        return [path] if isinstance(flags, int) and flags & WRITE_FLAGS else []
    if event in ("os.rename", "os.replace") and any(fd not in (None, -1) for fd in args[2:4]):
        return []   # relative to an open directory (shutil.rmtree, a TemporaryDirectory's clean-up): not the checkout
    if event in ("os.rename", "os.replace", "shutil.move"):   # the source goes away, the destination is replaced
        return list(args[:2])
    if event == "shutil.copyfile":   # the source is only read
        return list(args[1:2])
    if event == "os.remove" and len(args) > 1 and args[1] not in (None, -1):
        return []   # relative to an open directory, as above
    if event in ("os.remove", "os.truncate"):
        return [args[0]] if args else []
    return []


WATCHED = frozenset({"open", "os.rename", "os.replace", "shutil.copyfile", "shutil.move", "os.remove", "os.truncate"})


def ignored(path: str, root: Path = ROOT) -> bool:
    """Whether git ignores `path` (a file the release never ships, so a host may write it)."""
    try:
        # No timeout: with one, Popen.wait polls with time.sleep, which a test may have stubbed to fail (an edit
        # never waits in place: test_audit_gaps_2026_10_28). check-ignore answers at once.
        out = _run(["git", "-C", str(root), "check-ignore", "-q", "--", path], capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


class Guard:
    """The audit hook's state. A path the release owns is a tracked file or any other path in the checkout git does
    not ignore (a new file there blocks a release that adds the same path: git refuses to overwrite it). Writes
    found are kept in `found` until a test's teardown takes them."""

    def __init__(self, tracked: set[str] | None, root: Path = ROOT):
        self.tracked = tracked or set()
        self.active = tracked is not None
        self.root = _norm(root)
        self.git_dir = _norm(root / ".git")
        self._ignored: dict[str, bool] = {}
        self.found: list[str] = []
        self._lock = threading.Lock()
        self._inside = threading.local()

    def owned(self, path: str) -> bool:
        if path in self.tracked:
            return True
        if not path.startswith(self.root + os.sep) or path == self.git_dir or path.startswith(self.git_dir + os.sep):
            return False
        if path not in self._ignored:
            self._ignored[path] = ignored(path)
        return not self._ignored[path]

    def hook(self, event: str, args: tuple) -> None:
        if event not in WATCHED or not self.active or getattr(self._inside, "busy", False):
            return
        self._inside.busy = True   # git check-ignore and formatting the stack are not watched
        try:
            hit = [p for p in (_norm(a) for a in is_write(event, args)) if p and self.owned(p)]
        finally:
            self._inside.busy = False
        if not hit:
            return
        self._inside.busy = True
        try:
            stack = [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in traceback.extract_stack()[:-2]
                     if not f.filename.endswith("tracked_writes.py") and _norm(f.filename).startswith(_norm(ROOT))]
            rel = [os.path.relpath(p, ROOT) for p in hit]
            with self._lock:
                self.found.append(f"{event} {', '.join(rel)} <- {' <- '.join(reversed(stack[-6:]))}")
        finally:
            self._inside.busy = False
        raise TrackedWriteRefused(f"tests may not write {', '.join(rel)}: the release owns it (QF-111)")

    def take(self) -> list[str]:
        with self._lock:
            found, self.found = self.found, []
        return found


_guard: Guard | None = None


def install() -> Guard:
    """Installs the hook once per process (an audit hook cannot be removed)."""
    global _guard
    if _guard is None:
        _guard = Guard(tracked_files())
        sys.addaudithook(_guard.hook)
    return _guard


def tree_state(root: Path = ROOT) -> dict[str, str] | None:
    """{path the release owns that differs from HEAD (a changed tracked file, or a new file git does not ignore): a
    hash of its bytes now (or "deleted")}; None outside a git checkout."""
    try:
        out = _run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                             capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    state = {}
    entries = os.fsdecode(out.stdout).split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":   # a rename's source follows it
            i += 1
        name = entry[3:]
        path = root / name
        try:
            state[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            state[name] = "deleted"
    return state


def changed_since(before: dict[str, str] | None, after: dict[str, str] | None) -> list[str]:
    if before is None or after is None:
        return []
    return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
