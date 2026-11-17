"""
src/host_state.py
=================
This host's own copies of tracked files the runtime changes (QF-111).

Some tracked files are both a shipped starting point and live state: a photo pool's credits.json (the operator's
review), the music library (approvals, downloads), the fact sheets (rebuilt when old), the geocoding cache, the
topic bank (approved proposals). The runtime used to rewrite them in place, so production's checkout drifted from the
release it runs, and the release deployer refused every later release that also changed one of them ("Production
changed <file> differently from the release").

The runtime never writes a tracked file now. A file is addressed by its tracked location (its logical path, e.g.
assets/<topic>/credits.json); a write goes to the same relative path under data/host/ (gitignored), and a read takes
that copy when it exists, else the tracked file. The first write starts from whatever the tracked file holds on this
host, so a production checkout whose tracked copy was changed in place keeps every decision in it. Once data/host/
holds a file, the tracked copy may be restored to the release's; how the two are read together depends on the kind
(below).

Paths outside the checkout (a test's folder) are read and written in place.

A host copy must not silently hide what a later release ships for the same file (QF-111 review 1, B2). Per kind:
- merged, so the release's entries always arrive: the geocoding cache (this host keeps only its own lookups) and the
  topic bank with its photo searches (src/topic_store.py);
- chosen by content: a fact sheet is read from the usable one of the two, the newer when both are (src/fact_sheet.py);
- merged per entry: a photo pool's credits.json takes the attribution fields of a photo from the shipped record's
  entry for the same photo (the same source_url, never just the same file name: src/credits.py), so a credit-line
  correction arrives;
- whole copies (the rest of a pool's record, its storyboard, the music library): the shipped file's digest is
  recorded when this host's copy is made (<copy>.shipped.json), and shipped_changed() names every one whose shipped
  file changed since; the run start says so (main.warn_shipped_changes).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import config


def _base() -> Path:
    return Path(config.BASE_DIR).resolve()


def host_dir() -> Path:
    return Path(config.HOST_STATE_DIR)


def _relative(path: Path) -> Path | None:
    """The path relative to the checkout, or None when it is outside it or already a host copy. Both sides are
    resolved (a Windows short name such as OMOKOL~1 in one and the long name in the other is the same folder)."""
    full = Path(path).resolve()
    try:
        rel = full.relative_to(_base())
    except ValueError:
        return None
    try:
        full.relative_to(host_dir().resolve())
        return None
    except ValueError:
        return rel


def host_copy(path) -> Path:
    """Where this host keeps its own copy of the file at `path` (the path itself outside the checkout)."""
    path = Path(path)
    rel = _relative(path)
    return path if rel is None else host_dir() / rel


def current(path) -> Path:
    """The file to read for `path`: this host's copy when it has one, else the file itself."""
    copy = host_copy(path)
    return copy if copy.exists() else Path(path)


def exists(path) -> bool:
    return current(path).exists()


def read_text(path, encoding: str = "utf-8") -> str:
    return current(path).read_text(encoding=encoding)


def read_json(path) -> Any:
    return json.loads(read_text(path))




def _replace(target: Path, write) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        with staged.open("wb") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    return target


SHIPPED_SUFFIX = ".shipped.json"
# Kinds whose shipped entries always reach the reader (merged or chosen by content): not reported as masked.
NOT_MASKED = ("data/geo_cache.json", "data/topics/", "data/fact_sheets/")


def _lf_sha256(path) -> str | None:
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return None


def _marker(copy: Path) -> Path:
    return copy.with_name(copy.name + SHIPPED_SUFFIX)


def _record_shipped(path, copy: Path) -> None:
    """The shipped file's digest (line endings aside) as this host's copy is first made: what the copy started from."""
    if copy == Path(path) or copy.exists() or _marker(copy).exists():
        return
    copy.parent.mkdir(parents=True, exist_ok=True)
    _marker(copy).write_text(json.dumps({"sha256": _lf_sha256(path)}), encoding="utf-8")


def write_bytes(path, data: bytes) -> Path:
    """Replaces this host's copy of `path` atomically. Returns the file written."""
    copy = host_copy(path)
    _record_shipped(path, copy)
    return _replace(copy, lambda stream: stream.write(data))


def write_text(path, text: str, encoding: str = "utf-8") -> Path:
    return write_bytes(path, text.encode(encoding))


def write_json(path, value: Any, indent: int | None = 2, ensure_ascii: bool = False, **kwargs: Any) -> Path:
    return write_text(path, json.dumps(value, indent=indent, ensure_ascii=ensure_ascii, **kwargs))


def shipped_changed() -> list[str]:
    """The files (checkout-relative, forward slashes) whose shipped version changed since this host made its own copy,
    so the release's change does not reach this host. A copy with no record (made by hand, or before the record
    existed) takes today's shipped file as its start: reported only when it changes from now on."""
    root = host_dir()
    if not root.is_dir():
        return []
    changed = []
    for copy in sorted(root.rglob("*")):
        name = copy.name
        if not copy.is_file() or name.endswith((SHIPPED_SUFFIX, ".tmp")):
            continue
        rel = copy.relative_to(root).as_posix()
        if any(rel == kind or rel.startswith(kind) for kind in NOT_MASKED):
            continue
        shipped = _base() / rel
        marker = _marker(copy)
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8")).get("sha256")
        except (OSError, ValueError):
            marker.write_text(json.dumps({"sha256": _lf_sha256(shipped)}), encoding="utf-8")
            continue
        if _lf_sha256(shipped) != recorded:
            changed.append(rel)
    return changed
