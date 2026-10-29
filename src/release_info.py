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
    """The commit an annotated tag object names, loose or packed; anything else (a commit) as is."""
    obj = git_dir / "objects" / sha[:2] / sha[2:]
    if not obj.is_file():
        body = _packed_tag(git_dir, sha)
        match = re.match(rb"object ([0-9a-f]{40})\n", body or b"")
        return match.group(1).decode() if match else sha
    try:
        data = zlib.decompress(obj.read_bytes())
    except zlib.error:
        return sha
    if not data.startswith(b"tag "):
        return sha
    match = re.search(rb"\x00object ([0-9a-f]{40})\n", data)
    return match.group(1).decode() if match else sha


def _packed_tag(git_dir: Path, sha: str) -> Optional[bytes]:
    """A tag object's body from a pack (version-2 index). A fetch leaves the tag ref loose and its object
    packed: rc68's dashboard showed the commit, not the tag (2026-10-28). Deltified objects are skipped
    (git stores tag objects whole); the label then falls back as before."""
    want = bytes.fromhex(sha)
    pack_dir = git_dir / "objects" / "pack"
    if not pack_dir.is_dir():
        return None
    for idx in pack_dir.glob("*.idx"):
        try:
            with idx.open("rb") as f:
                if f.read(8) != b"\xfftOc\x00\x00\x00\x02":
                    continue
                fanout = f.read(1024)
                lo = int.from_bytes(fanout[(want[0] - 1) * 4:want[0] * 4], "big") if want[0] else 0
                hi = int.from_bytes(fanout[want[0] * 4:want[0] * 4 + 4], "big")
                total = int.from_bytes(fanout[1020:1024], "big")
                while lo < hi:  # the names are sorted
                    mid = (lo + hi) // 2
                    f.seek(1032 + mid * 20)
                    name = f.read(20)
                    if name == want:
                        break
                    lo, hi = (mid + 1, hi) if name < want else (lo, mid)
                else:
                    continue
                f.seek(1032 + total * 24 + mid * 4)
                offset = int.from_bytes(f.read(4), "big")
                if offset & 0x80000000:
                    f.seek(1032 + total * 28 + (offset & 0x7FFFFFFF) * 8)
                    offset = int.from_bytes(f.read(8), "big")
            with idx.with_suffix(".pack").open("rb") as p:
                p.seek(offset)
                byte = p.read(1)[0]
                kind = (byte >> 4) & 7
                while byte & 0x80:
                    byte = p.read(1)[0]
                if kind != 4:  # not stored as a whole tag object
                    return None
                return zlib.decompressobj().decompress(p.read(64 * 1024))
        except (OSError, IndexError, zlib.error):
            continue
    return None


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
