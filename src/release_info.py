"""
src/release_info.py
===================
Which release this checkout is running, for the dashboard ("PRODUCTION (v1.0.0-rc11)").

Production's daemon runs as an account that may not run git on a checkout another account owns, so
the release is read from the .git files themselves: the commit HEAD names (the release deployer and
the promotion procedure check releases out detached) and a release tag that points at it, whether
packed, loose and lightweight, or loose and annotated. Where none is found, git describe is tried
(development), then the short commit.
"""

from __future__ import annotations

import re
import subprocess
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from .config import BASE_DIR

RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+(?:-rc\d+)?$")
SHA = re.compile(r"^[0-9a-f]{40}$")


def _head_commit(git_dir: Path) -> Optional[str]:
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if SHA.match(head):
        return head
    if head.startswith("ref: "):
        return _resolve_ref(git_dir, head[5:].strip())
    return None


def _packed_refs(git_dir: Path) -> Dict[str, str]:
    """ref -> commit, using the peeled (^) line for annotated tags."""
    refs: Dict[str, str] = {}
    path = git_dir / "packed-refs"
    if not path.is_file():
        return refs
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("^") and last:
            refs[last] = line[1:].strip()
        elif line and not line.startswith("#"):
            sha, _, ref = line.partition(" ")
            refs[ref.strip()] = sha.strip()
            last = ref.strip()
    return refs


def _resolve_ref(git_dir: Path, ref: str) -> Optional[str]:
    loose = git_dir / ref
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip()
    return _packed_refs(git_dir).get(ref)


def _peel(git_dir: Path, sha: str) -> str:
    """The commit an annotated tag object names; anything else (a commit, a packed object) as is."""
    obj = git_dir / "objects" / sha[:2] / sha[2:]
    if not obj.is_file():
        return sha
    try:
        data = zlib.decompress(obj.read_bytes())
    except zlib.error:
        return sha
    if not data.startswith(b"tag "):
        return sha
    match = re.search(rb"\x00object ([0-9a-f]{40})\n", data)
    return match.group(1).decode() if match else sha


def _tag_for(git_dir: Path, commit: str) -> Optional[str]:
    names = []
    for ref, target in _packed_refs(git_dir).items():
        if ref.startswith("refs/tags/") and target == commit:
            names.append(ref[len("refs/tags/"):])
    tags_dir = git_dir / "refs" / "tags"
    if tags_dir.is_dir():
        for path in tags_dir.iterdir():
            if path.is_file() and _peel(git_dir, path.read_text(encoding="utf-8").strip()) == commit:
                names.append(path.name)
    releases = sorted({n for n in names if RELEASE_TAG.match(n)}, key=_version)
    return releases[-1] if releases else None


def _version(tag: str) -> tuple:
    """v1.2.3-rcN orders by number, a final release after its candidates (as the deployer orders them)."""
    major, minor, patch, rc = re.match(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$", tag).groups()
    return int(major), int(minor), int(patch), int(rc) if rc else float("inf")


def _describe(root: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "describe",
                              "--tags", "--always"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


@lru_cache(maxsize=4)
def _cached(root: str, head: str) -> Optional[str]:
    path = Path(root)
    git_dir = path / ".git"
    tag = _tag_for(git_dir, head) if SHA.match(head) else None
    return tag or _describe(path) or (head[:7] if SHA.match(head) else None)


def release_label(root: Path = BASE_DIR) -> Optional[str]:
    """The running release: its tag, git describe, or the short commit (None if not a checkout)."""
    git_dir = root / ".git"
    try:
        head = _head_commit(git_dir) or ""
    except OSError:
        return None
    return _cached(str(root), head)  # recomputed only when HEAD moves (a new release)
