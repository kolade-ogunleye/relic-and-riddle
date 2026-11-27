"""The board's provenance (cycle 3, lane BOARD; AD r1 AD-EV-manifest, FREEZE-PLAYBOOK 0 "Evidence manifest"): which
code drew the evidence and which inputs it read, so a reviewer can tie every image to both.

- tree_stamp(): the tree's HEAD, whether its drawn paths differ from HEAD, the src hash (full) and the board tools'
  own hash (this folder's *.py and QF-35a's render_surfaces.py, CRLF read as LF).
- install() / files(): every data file a process reads (sys.addaudithook: a read-mode "open", and the file
  arguments of every subprocess it starts, so ffmpeg's inputs count) under the given roots, with its sha256. Code is
  left out (*.py, *.pyc, .git, .venv, __pycache__): the tree stamp and freeze_manifest.py's tree ids cover it.
  A file read once through a process cache (the geo cache, the fixtures catalogue) is read by whichever job comes
  first in its process, so the record is the run's union, never a per-job split that depends on the shard layout.
- input_hashes(): <out>/INPUT_HASHES.json, read by freeze_manifest.py: per media file of the board, sha256 over its
  path, the brand, the tree stamp and the union of the inputs (identical however the board is sharded).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SKIP_PARTS = {".git", ".venv", "__pycache__", "node_modules"}
SKIP_SUFFIXES = {".py", ".pyc", ".pyd", ".dll", ".exe"}
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mkv"}
FRAME_CACHE = Path(tempfile.gettempdir()) / "rr_fixture_frames"   # fixtures.frame's cache (read as an input)

_state: dict[str, Any] = {"roots": (), "skip": (), "seen": set(), "installed": False}
_sha_cache: dict[tuple[str, int, int], str] = {}


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _wanted(path: str) -> str | None:
    p = _norm(path)
    if not any(p.startswith(r) for r in _state["roots"]) or any(p.startswith(s) for s in _state["skip"]):
        return None
    parts = set(Path(p).parts)
    if parts & SKIP_PARTS or Path(p).suffix.lower() in SKIP_SUFFIXES:
        return None
    return p


def _hook(event: str, args: tuple) -> None:
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if not isinstance(path, (str, os.PathLike)):
            return
        if isinstance(mode, str):
            if any(c in mode for c in "wax+"):
                return
        elif isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            return
        p = _wanted(os.fspath(path))
        if p:
            _state["seen"].add(p)
    elif event == "subprocess.Popen":
        argv = args[1] if len(args) > 1 else None
        for a in ([argv] if isinstance(argv, (str, os.PathLike)) else list(argv or [])):
            s = os.fspath(a) if isinstance(a, (str, os.PathLike)) else None
            if s and len(s) < 400 and not s.startswith("-"):
                p = _wanted(s)
                if p and os.path.isfile(p):
                    _state["seen"].add(p)


def install(roots, skip=()) -> None:
    """Start recording reads under `roots` (minus `skip`) in this process; idempotent (the roots are widened)."""
    _state["roots"] = tuple(sorted({*_state["roots"], *(_norm(str(r)) for r in roots)}))
    _state["skip"] = tuple(sorted({*_state["skip"], *(_norm(str(s)) for s in skip)}))
    if not _state["installed"]:
        sys.addaudithook(_hook)
        _state["installed"] = True


# tree paths whose data reads are recorded (cycle 5, fix 1: data/ too: the art-direction boxes, the word lists,
# the fit corpus, data/geo are read by the renders)
TREE_DATA = ("tests", "docs/reviews/freeze-panel-2026-11-04/fixtures", "data")


def tree_roots(tree) -> list[str]:
    """The tree's folders whose data files (never code) a render can read and that are recorded as inputs (cycle 5):
    a changed tests fixture or catalogue file then counts for the incremental board only when a render read it.
    fonts/ and assets/ are left out: FreeType opens a face outside Python, so their reads are not all seen."""
    return [str(Path(tree) / p) for p in TREE_DATA]


def seen() -> set[str]:
    return set(_state["seen"])


def sha256_file(p: str) -> str | None:
    try:
        st = os.stat(p)
    except OSError:
        return None
    key = (p, st.st_size, st.st_mtime_ns)
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(p, "rb") as f:   # opened for hashing: a read under the roots, already recorded
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        _sha_cache[key] = h.hexdigest()
    return _sha_cache[key]


def files(paths=None) -> dict[str, str]:
    """{path (as recorded): sha256} for `paths` (default: every read recorded in this process), files that exist."""
    out = {}
    for p in sorted(paths if paths is not None else seen()):
        if os.path.isfile(p):
            sha = sha256_file(p)
            if sha:
                out[p] = sha
    return out


def merge(into: dict[str, str], more: dict[str, str]) -> dict[str, str]:
    """A union of two input records; a file recorded with two contents is an error (an input changed mid-run)."""
    for p, sha in (more or {}).items():
        if into.get(p, sha) != sha:
            raise RuntimeError(f"board inputs: {p} changed during the run ({into[p][:12]} / {sha[:12]})")
        into[p] = sha
    return into


def union_sha256(inputs: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(inputs.items()), separators=(",", ":")).encode()).hexdigest()


def _lf_sha(paths) -> str:
    h = hashlib.sha256()
    for f in sorted(paths, key=lambda p: Path(p).name):
        h.update(Path(f).name.encode() + Path(f).read_bytes().replace(bytes([13, 10]), bytes([10])))
    return h.hexdigest()


def _git(tree: Path, *args: str) -> str | None:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")   # never refresh (write) the tree's index
    try:
        r = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


DRAWN_PATHS = ("src", "fonts", "assets", "data", "tools", "uv.lock", "pyproject.toml", "tests",
               "docs/reviews/freeze-panel-2026-11-04/fixtures",
               "docs/reviews/freeze-system-2026-11-15", "docs/reviews/quality-fixes-2026-11/QF-35a")


def tree_stamp(tree: Path, here: Path) -> dict[str, Any]:
    """The code that drew the board: HEAD, the tree ids of the drawn paths at HEAD, whether the drawn paths differ
    from HEAD on disk (and which), the full src hash and the board tools' hash."""
    tree, here = Path(tree), Path(here)
    head = _git(tree, "rev-parse", "HEAD")
    ids = {p: _git(tree, "rev-parse", f"HEAD:{p}") for p in DRAWN_PATHS} if head else {}
    # cycle 5, fix 1: the WHOLE tree (a render reads data/, tools/ and more than the paths above); files git
    # ignores (tools/ data, cache/) are not in this list: board_reuse.tool_data and board_deps.untracked hold them
    dirty = _git(tree, "status", "--porcelain", "--untracked-files=all")
    tools = sorted(here.glob("*.py")) + [tree / "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"]
    return {"head": head, "head_tree_ids": ids, "dirty_vs_head": bool(dirty) if dirty is not None else None,
            "dirty_paths": (dirty or "").splitlines()[:50],
            "src_sha256": _lf_sha((tree / "src").glob("*.py")),
            "board_tools_sha256": _lf_sha([p for p in tools if p.is_file()]),
            "board_tools": [p.name for p in tools if p.is_file()]}


def media(out: Path) -> list[str]:
    """The board's media files (as freeze_manifest.py lists them: every media file under <out>), relative posix."""
    out = Path(out)
    found = []
    for dirpath, dirnames, filenames in os.walk(out):
        dirnames[:] = sorted(d for d in dirnames if d not in {"_ab", "__pycache__", ".git"})
        for fn in sorted(filenames):
            if Path(fn).suffix.lower() in MEDIA_SUFFIXES:
                found.append((Path(dirpath) / fn).relative_to(out).as_posix())
    return found


def input_hashes(out: Path, stamp: dict[str, Any], brand: str, inputs: dict[str, str]) -> dict[str, str]:
    """{media path: "sha256:..."} over (the path, the brand, the tree stamp's code ids, the inputs' union)."""
    code = {k: stamp.get(k) for k in ("head", "head_tree_ids", "dirty_paths", "src_sha256", "board_tools_sha256")}
    base = {"brand": brand, "code": code, "inputs_sha256": union_sha256(inputs)}
    return {rel: "sha256:" + hashlib.sha256(json.dumps({**base, "output": rel}, sort_keys=True,
                                                       separators=(",", ":")).encode()).hexdigest()
            for rel in media(out)}
