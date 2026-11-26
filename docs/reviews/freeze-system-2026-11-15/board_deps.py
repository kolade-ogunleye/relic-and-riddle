"""What each board job's pixels can depend on, observed while it rendered, and the key that says whether all of it
is the same at another commit (cycle 5, lane BOARD: the incremental board, board_reuse.py).

The rule (review of 2026-11-26, FIX FIRST): a job's key covers EVERYTHING its render can depend on, and where that
cannot be proven the job renders. The first version keyed the functions a profiler saw and the values they named; it
missed tracked data, a tests module's values, module-level bindings and code behind an on-disk cache. This one:

RECORD. One render process per job (board.py), so nothing a job reads, imports or leaves in a module is another
job's. The process records (Recorder; a record only, the pixels are untouched):
  functions   the tree's functions that ran (cProfile, every thread), import time and harness included;
  modules     every tree module loaded (sys.modules) or run, whether or not a function of it ran;
  imports     every module name the import system was asked for (found or not), and the tree's sys.path folders;
  reads       every file opened for reading, handed to a child process (in an argument or inside one, as ffmpeg's
              filter strings carry them) or opened by sqlite, anywhere outside the interpreter's own folders, with
              its sha256; a .py file read as data (not imported) counts as data;
  probes      every path asked for existence or metadata (os.stat and its family), with what was there;
  listed      every folder listed (os.listdir, os.scandir, glob), with its names;
  env         every environment variable looked up by name, with its value's sha256 (a secret-like name: set or not);
  built       every file the process created that outlives it (a cache), with the stack that created it and what
              those functions ever called: its builders.
What the process itself wrote is not its input. A Python child process, a read of an .env file or an input the job
rewrote cannot be recorded: the job is marked "never" (always rendered).

KEY (job_key; at any commit, from git objects only):
  - a module the job ran a function of: the whole module (its AST); render_surfaces.py alone is cut finer, each job
    closure of its `main` being a unit (they are disjoint by construction);
  - every other module the job loaded: its module-level code: everything but function bodies (imports, assignments,
    class bodies with their method names, decorators, defaults). So a re-pointed import, a new method or dunder, a
    tests module's constant change the key although no function of that module ran;
  - every tree .py file an import the job asked for could resolve to (a new module that shadows one);
  - every tracked file that is not Python outside tests/ and docs/ (data/, fonts/, assets/, src/, tools/, the root:
    readers outside Python exist, so a read cannot be ruled out): its blob, in EVERY job's key;
  - every tree path the job read, probed or listed (tests/, docs/ and the rest): blob, presence, names;
  - "whole" jobs (an artefact whose build nobody observed): every module of the code roots, whole.
What is not in git is not in the key and is checked by board_reuse.plan against the disk: files outside the tree,
probes, listings, environment, toolchain, tools/ data, artefacts.

ARTEFACTS (resolve): a file a job read or probed that is neither tracked nor tools/ data (the tree's cache/, the
temp folder's frame cache) was built by code. Its builders join the key of every job that touched it: from the job
that built it in this board, or from the builders' store (<temp>/rr_board_artefacts/<sha256>.json, written when a
build is observed) when it was there before; a stored builder whose module changed since is no proof. No proof: the
job is "whole".

    python board_deps.py <tree> --keys <DEPS.json> [--commit C]            each job's key at a commit (default HEAD)
    python board_deps.py <tree> --what-if <DEPS.json> <base> <head>        the jobs whose key differs, and why
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable

FIX = "docs/reviews/freeze-panel-2026-11-04/fixtures"
HERE = "docs/reviews/freeze-system-2026-11-15"
QF35A = "docs/reviews/quality-fixes-2026-11/QF-35a"
RENDER = f"{QF35A}/render_surfaces.py"
CODE_ROOTS = ("src", FIX, HERE, QF35A)   # a "whole" job's key: every module under these
# a function whose nested functions are units of their own (the rest of it is "<name>.<body>"); only here is a
# file keyed finer than whole
SPLIT = {RENDER: ("main",)}
SCHEMA = "board-deps/2"
SELF = (f"{HERE}/board_deps.py",)   # the recorder itself: its functions are not the render's (its module is keyed)
# the harness's own frames: on every job's stack, so never what ties a job to an artefact's builders
HARNESS = f"{RENDER}::main."


def _harness(fn: str) -> bool:
    return fn.startswith(HARNESS)


# tracked paths whose files count for a job only when it read, probed or listed them; every other tracked file that
# is not Python is in every job's key
RECORDED_DIRS = ("tests", "docs", "scripts", "evals", "deployment", "decisions", ".github")
TOOLS = "tools/"   # untracked tools/ data: hashed whole in board.json reuse_env (board_reuse.tool_data)
STORE = "rr_board_artefacts"
SECRET = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|SESSION", re.I)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
SKIP_PARTS = {".git", ".venv", "__pycache__", "node_modules", "site-packages"}
SKIP_SUFFIXES = {".pyc", ".pyd", ".dll", ".exe"}
# a path inside a child's argument (an ffmpeg filter string): a drive path on Windows, a rooted one elsewhere
ARG_PATH = re.compile(r"[A-Za-z]:[\\/][^'\":;,\[\]|*?<>\r\n]+" if os.name == "nt" else r"/[^'\":;,\[\]|*?<>\s]+")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def path_class(rel: str) -> str:
    """"code" (Python), "recorded" (counts when a job read it) or "global" (in every job's key)."""
    if rel.endswith(".py"):
        return "code"
    top = rel.split("/", 1)[0]
    if ("/" in rel and top in RECORDED_DIRS) or ("/" not in rel and rel.lower().endswith(".md")):
        return "recorded"
    return "global"


# ------------------------------------------------------------------------------------------------ a module's units
class Names(ast.NodeVisitor):
    """The identifiers a piece of code mentions (names, attributes, identifier-like strings)."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, n: ast.Name) -> None:
        self.names.add(n.id)

    def visit_Attribute(self, n: ast.Attribute) -> None:
        self.names.add(n.attr)
        self.generic_visit(n)

    def visit_Constant(self, n: ast.Constant) -> None:
        if isinstance(n.value, str) and len(n.value) < 80 and IDENT.match(n.value):
            self.names.add(n.value)


def _names(nodes: Iterable[ast.AST]) -> set[str]:
    v = Names()
    for n in nodes:
        v.visit(n)
    return v.names


def _span(node: ast.AST) -> tuple[int, int]:
    first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    return first, node.end_lineno or node.lineno


class _Strip(ast.NodeTransformer):
    """Module-level code: every function body (outside another function) emptied; its name, decorators, arguments
    and defaults stay (they run or bind at import time). `units` collects each emptied function: (name, node)."""

    def __init__(self, split: tuple[str, ...] = ()) -> None:
        self.units: list[tuple[str, ast.AST]] = []
        self.split = split
        self.owner: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self.owner.append(node.name)
        self.generic_visit(node)
        self.owner.pop()
        return node

    def _func(self, node):
        name = ".".join(self.owner + [node.name])
        if not self.owner and node.name in self.split:   # its nested functions are units, the rest its <body>
            lift = _Lift()
            rest = lift.visit(copy.deepcopy(node))
            for inner in lift.found:
                self.units.append((f"{node.name}.{inner.name}", inner))
            self.units.append((f"{node.name}.<body>", rest))
        else:
            self.units.append((name, node))
        bare = copy.copy(node)
        bare.body = [ast.Pass()]
        return bare

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func

    def visit_Lambda(self, node):
        return node   # a lambda is its statement's own code


class _Lift(ast.NodeTransformer):
    """Takes the functions defined anywhere in a function's own statements (not inside another function) out of it."""

    def __init__(self) -> None:
        self.found: list[ast.AST] = []
        self.depth = 0

    def _func(self, node):
        if self.depth == 0:
            self.depth = 1
            self.generic_visit(node)
            self.depth = 0
            return node
        self.found.append(node)
        return ast.Pass()

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func

    def visit_Lambda(self, node):
        return node


class Module:
    """One Python file at one state: `full` (its whole AST), `skeleton` (module-level code only), `units`
    ({function or Class.method: sha256}), `spans` ((first line, last line, unit) of each), and the names each part
    mentions."""

    def __init__(self, file: str, text: str):
        self.file = file
        try:
            mod = ast.parse(text)
        except SyntaxError as e:   # unparsable: its text is its only identity
            self.full = self.skeleton = _sha(text + str(e))
            self.units, self.spans, self.unit_names = {}, [], {}
            self.names_full = self.names_skeleton = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
            return
        self.full = _sha(ast.dump(mod))
        self.names_full = _names([mod])
        strip = _Strip(SPLIT.get(file, ()))
        bare = strip.visit(copy.deepcopy(mod))
        self.skeleton = _sha(ast.dump(bare))
        self.names_skeleton = _names([bare])
        self.units, self.unit_names, self.spans = {}, {}, []
        body_spans = []
        for name, node in strip.units:
            sha = _sha(ast.dump(node))
            self.units[name] = _sha(self.units[name] + sha) if name in self.units else sha   # a name defined twice
            self.unit_names[name] = self.unit_names.get(name, set()) | _names([node])
            (body_spans if name.endswith(".<body>") else self.spans).append((*_span(node), name))
        self.spans.sort()
        self.spans += body_spans   # a split function's own body: the last resort for a line inside it


# ------------------------------------------------------------------------------------------------ a tree at a commit
class Tree:
    """A commit's tracked files, from git objects (never the worktree): `blobs` {path: blob id}, text on demand."""

    def __init__(self, repo: Path, commit: str):
        self.repo, self.commit = Path(repo), commit
        self.env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        out = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", commit], capture_output=True, check=True,
                             env=self.env).stdout.decode("utf-8", errors="replace")
        self.blobs: dict[str, str] = {}
        for row in out.split("\0"):
            if row:
                meta, _, name = row.partition("\t")
                self.blobs[name] = meta.split()[2]
        self.lower = {n.lower(): n for n in self.blobs}
        self._text: dict[str, str | None] = {}
        self._mods: dict[str, Module | None] = {}
        self._dirs: set[str] | None = None

    def real(self, rel: str) -> str | None:
        """The tracked path spelled as the tree spells it (records hold lower-cased paths on Windows)."""
        return rel if rel in self.blobs else self.lower.get(rel.lower())

    def fetch(self, names: Iterable[str]) -> None:
        """Read several blobs in one git process."""
        want = [n for n in dict.fromkeys(names) if n in self.blobs and n not in self._text]
        if not want:
            return
        r = subprocess.run(["git", "-C", str(self.repo), "cat-file", "--batch"], capture_output=True, check=True,
                           env=self.env, input="".join(f"{self.blobs[n]}\n" for n in want).encode())
        data, at = r.stdout, 0
        for n in want:
            end = data.index(b"\n", at)
            size = int(data[at:end].split()[2])
            self._text[n] = data[end + 1:end + 1 + size].decode("utf-8", errors="replace").replace("\r\n", "\n")
            at = end + 1 + size + 1

    def text(self, name: str) -> str | None:
        if name not in self._text:
            if name in self.blobs:
                self.fetch([name])
            else:
                self._text[name] = None
        return self._text[name]

    def module(self, name: str) -> Module | None:
        if name not in self._mods:
            t = self.text(name)
            self._mods[name] = Module(name, t) if t is not None else None
        return self._mods[name]

    def dirs(self) -> set[str]:
        if self._dirs is None:
            self._dirs = {""}
            for n in self.blobs:
                parts = n.split("/")[:-1]
                for i in range(1, len(parts) + 1):
                    self._dirs.add("/".join(parts[:i]).lower())
        return self._dirs

    def listing(self, rel: str) -> str:
        """The tracked names directly inside a folder (sha256), or "absent"."""
        d = rel.strip("/").lower()
        if d not in self.dirs():
            return "absent"
        pre = d + "/" if d else ""
        names = sorted({n[len(pre):].split("/", 1)[0] for low, n in ((x.lower(), x) for x in self.blobs)
                        if low.startswith(pre)}, key=str.lower)
        return _sha(json.dumps([x.lower() for x in names]))

    def code(self) -> list[str]:
        """Every module of the code roots (a "whole" job's key)."""
        return sorted(n for n in self.blobs if n.endswith(".py") and any(n.startswith(r + "/") for r in CODE_ROOTS))


def module_names(rel: str, syspath: Iterable[str]) -> set[str]:
    """The names an import could find the tree file `rel` under, given the tree's sys.path folders ("" the root)."""
    out = set()
    low = rel.lower()
    for d in syspath:
        d = d.strip("/").lower()
        pre = d + "/" if d else ""
        if not low.startswith(pre):
            continue
        parts = rel[len(pre):-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and all(IDENT.match(p) for p in parts):
            out.add(".".join(parts))
    return out


def job_key(tree: Tree, rec: dict[str, Any]) -> dict[str, Any]:
    """The key of the job recorded as `rec` at the tree's commit: {"sha256", "rows": {(what, path): identity}}."""
    ran: dict[str, set[str]] = {}
    for fn in rec.get("functions") or ():
        f, _, name = fn.partition("::")
        ran.setdefault(f, set()).add(name)
    whole = bool(rec.get("whole"))
    files = set(rec.get("modules") or ()) | set(ran)
    if whole:
        files |= set(tree.code())
    tree.fetch(sorted(files))
    rows: dict[str, str] = {}
    for f in sorted(files):
        m = tree.module(tree.real(f) or f)
        if m is None:
            rows[f"code:{f}"] = "absent"
        elif f in SPLIT and not whole:   # the harness: its module-level code and the units this job ran
            rows[f"module-level:{f}"] = m.skeleton
            for name in sorted(ran.get(f, ())):
                rows[f"unit:{f}::{name}"] = m.units.get(name, "absent")
        elif whole or f in ran:
            rows[f"code:{f}"] = m.full
        else:
            rows[f"module-level:{f}"] = m.skeleton
    asked = set(rec.get("imports") or ())
    syspath = rec.get("syspath") or [""]
    have = {f.lower() for f in files}
    for n, blob in tree.blobs.items():
        if n.endswith(".py") and n.lower() not in have and module_names(n, syspath) & asked:
            rows[f"import-candidate:{n}"] = blob
    data = sorted((n, b) for n, b in tree.blobs.items() if path_class(n) == "global")
    rows["tracked-data:*"] = _sha(json.dumps(data))
    for rel in rec.get("tree_reads") or {}:
        real = tree.real(rel)
        rows[f"read:{rel}"] = tree.blobs[real] if real else "absent"
    for rel in rec.get("tree_probes") or {}:
        real = tree.real(rel)
        rows[f"probe:{rel}"] = "file" if real else "dir" if rel.strip("/").lower() in tree.dirs() else "absent"
    for rel in rec.get("tree_listed") or {}:
        rows[f"listing:{rel}"] = tree.listing(rel)
    listed = sorted(rows.items())
    return {"sha256": _sha(json.dumps(listed)), "rows": rows, "whole": whole,
            "global_data": {n: b for n, b in data}}


def changed_rows(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """What differs between two keys of one job (the row names; a changed tracked data file is named)."""
    ra, rb = a["rows"], b["rows"]
    out = sorted(k for k in set(ra) | set(rb) if ra.get(k) != rb.get(k) and k != "tracked-data:*")
    ga, gb = a.get("global_data") or {}, b.get("global_data") or {}
    out += sorted(f"tracked-data:{n}" for n in set(ga) | set(gb) if ga.get(n) != gb.get(n))
    return out


def kept_names(tree: Tree, rec: dict[str, Any]) -> set[str]:
    """Every identifier the code a job's key holds mentions (the gate's static net: board_reuse.touched_by)."""
    ran: dict[str, set[str]] = {}
    for fn in rec.get("functions") or ():
        f, _, name = fn.partition("::")
        ran.setdefault(f, set()).add(name)
    out: set[str] = set()
    for f in set(rec.get("modules") or ()) | set(ran):
        m = tree.module(tree.real(f) or f)
        if m is None:
            continue
        if f in SPLIT:   # the harness: its dispatcher (<body>) names every job's closure and calls one; left out
            out |= m.names_skeleton
            for name in ran.get(f, ()):
                if not name.endswith(".<body>"):
                    out |= m.unit_names.get(name, set())
        elif f in ran:
            out |= m.names_full
        else:
            out |= m.names_skeleton
    return out


# ------------------------------------------------------------------------------------------------ recording
def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def env_name(key: str) -> str:
    return key.upper() if os.name == "nt" else key


def env_state(key: str, value: str | None) -> str:
    """An environment variable as recorded: "unset", "set" (a secret-like name: its value is never hashed) or the
    sha256 of its value."""
    if value is None:
        return "unset"
    return "set" if SECRET.search(key) else _sha(value)


class Recorder:
    """Everything this process's render can depend on (see the module docstring). One job per process."""

    def __init__(self, tree: Path, skip: Iterable[str | Path] = ()):
        import cProfile
        self.tree = _norm(str(tree)) + os.sep
        self.temp = _norm(tempfile.gettempdir()) + os.sep
        self.skip = tuple(_norm(str(s)) for s in skip)
        self.py = tuple(sorted({_norm(p) + os.sep for p in (sys.prefix, sys.base_prefix, sys.exec_prefix)}))
        self.prof = cProfile.Profile(subcalls=True, builtins=False)
        self._mods: dict[str, Module | None] = {}
        self._code: dict[Any, tuple[str, str] | None] = {}
        self._thread_seen: set[tuple[Any, Any]] = set()
        self._paths: dict[str, str | None] = {}
        self.reads: set[str] = set()
        self.probes: dict[str, str] = {}
        self.listed: dict[str, str] = {}
        self.wrote: dict[str, list[str]] = {}     # path -> the stack that first wrote it
        self.made: set[str] = set()               # folders this process created
        self.missing: dict[str, list[str]] = {}   # a path first seen missing -> the stack that asked
        self.patterns: list[re.Pattern] = []      # a child's numbered outputs (frame_%05d.png)
        self.env: dict[str, str] = {}
        self.env_own: set[str] = set()            # names this process set itself: not its inputs
        self.imports: set[str] = set()
        self.never: list[str] = []
        self.busy = threading.local()
        self.on = False
        self.jobs: dict[str, dict[str, Any]] = {}
        self.current: str | None = None

    # -- code object -> (tree file, unit or None for module-level code)
    def _module(self, rel: str) -> Module | None:
        if rel not in self._mods:
            was = getattr(self.busy, "on", False)
            try:
                self.busy.on = True
                text = Path(self.tree, rel).read_bytes().decode("utf-8", errors="replace")
                self._mods[rel] = Module(rel, text)
            except OSError:
                self._mods[rel] = None
            finally:
                self.busy.on = was
        return self._mods[rel]

    def _real(self, rel: str) -> str:
        if not hasattr(self, "_reals"):
            self._reals: dict[str, str] = {}
            for dirpath, dirnames, filenames in os.walk(self.tree):
                dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS and d not in ("output", "cache", "logs")]
                for fn in filenames:
                    if fn.endswith(".py"):
                        r = os.path.relpath(os.path.join(dirpath, fn), self.tree).replace(os.sep, "/")
                        self._reals[r.lower()] = r
        return self._reals.get(rel.lower(), rel)

    def symbol(self, code) -> tuple[str, str | None] | None:
        if code not in self._code:
            out = None
            fn = getattr(code, "co_filename", None)
            if isinstance(fn, str) and fn.endswith(".py"):
                p = _norm(fn)
                if p.startswith(self.tree) and not (set(Path(p).parts) & SKIP_PARTS):
                    real = self._real(p[len(self.tree):].replace(os.sep, "/"))
                    if real not in SELF:   # the recorder is not render code
                        m = self._module(real)
                        if m is not None:   # a file of the tree (a library compiled with a relative name is not)
                            line = code.co_firstlineno
                            out = (real, next((n for a, b, n in m.spans if a <= line <= b), None))
            self._code[code] = out
        return self._code[code]

    def _stack(self) -> list[str]:
        out: list[str] = []
        f = sys._getframe(1)
        while f is not None:
            s = self.symbol(f.f_code)
            if s and s[1]:
                name = f"{s[0]}::{s[1]}"
                if name not in out:
                    out.append(name)
            f = f.f_back
        return out[::-1]

    # -- paths
    def _path(self, path: Any) -> str | None:
        """The path as recorded, or None for one that is no input (the interpreter's, code caches, the skip roots)."""
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        elif isinstance(path, os.PathLike):
            path = os.fspath(path)
        if not isinstance(path, str) or not path:
            return None
        if path not in self._paths:
            out = None
            try:
                p = _norm(path)
                if not ((p + os.sep).startswith(self.py) or (self.skip and p.startswith(self.skip))
                        or set(Path(p).parts) & SKIP_PARTS or os.path.splitext(p)[1] in SKIP_SUFFIXES
                        or p.startswith("\\\\.\\") or os.path.basename(p) in ("nul", "con")):
                    out = p
            except (OSError, ValueError):
                out = None
            if len(self._paths) > 200000:
                self._paths.clear()
            self._paths[path] = out
        return self._paths[path]

    def _own(self, p: str) -> bool:
        """Did this process make `p` (wrote it, found it missing first, or made a folder above it)?"""
        if p in self.wrote or p in self.missing or any(r.match(p) for r in self.patterns):
            return True
        d = os.path.dirname(p)
        while d and len(d) > 3:
            if d in self.made:
                return True
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
        return False

    def _read(self, p: str) -> None:
        if not self._own(p):
            if os.path.isdir(p):
                self._list(p, deep=True)
            elif os.path.exists(p):
                self.reads.add(p)
            else:
                self.missing.setdefault(p, self._stack())
                self.probes.setdefault(p, "absent")

    def _write(self, p: str) -> None:
        if p in self.reads and p not in self.wrote:
            self.never.append(f"an input the job then wrote: {p}")
        self.wrote.setdefault(p, self._stack())

    def _list(self, p: str, deep: bool = False) -> None:
        if p in self.listed or self._own(p):
            return
        try:
            names = sorted(os.listdir(p))
        except OSError:
            self.missing.setdefault(p, self._stack())
            return
        self.listed[p] = _sha(json.dumps([n.lower() for n in names]))
        if deep:   # a folder handed to a child process: every file under it is its input
            n = 0
            for dirpath, _d, filenames in os.walk(p):
                for fn in filenames:
                    q = self._path(os.path.join(dirpath, fn))
                    if q and not self._own(q):
                        self.reads.add(q)
                        n += 1
                if n > 5000:
                    self.never.append(f"a folder of more than 5000 files handed to a child process: {p}")
                    break

    def _probe(self, path: Any, found: Any) -> None:
        p = self._path(path)
        if p is None or p in self.probes or self._own(p):
            return
        if found is None:
            self.missing.setdefault(p, self._stack())
            self.probes[p] = "absent"
        else:
            import stat as _stat
            self.probes[p] = "dir" if _stat.S_ISDIR(found.st_mode) else "file"

    # -- the hooks
    def _audit(self, event: str, args: tuple) -> None:
        if not self.on or getattr(self.busy, "on", False):
            return
        if event not in ("open", "import", "os.listdir", "os.scandir", "glob.glob", "os.rename", "os.mkdir",
                         "subprocess.Popen", "sqlite3.connect", "os.remove"):
            return
        self.busy.on = True
        try:
            if event == "import":
                if isinstance(args[0], str):
                    self.imports.add(args[0])
            elif event == "open":
                path, mode, flags = (tuple(args) + (None, None, None))[:3]
                p = self._path(path)
                if p is None:
                    return
                writing = (any(c in mode for c in "wax+") if isinstance(mode, str)
                           else bool(isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)))
                if writing:
                    self._write(p)
                elif p.endswith(".py"):
                    # a module the import system loads is code (the modules record); a .py opened by anything
                    # else is read as data (font_assets keys its cache on its own file's bytes). The harness's
                    # own hash of src/ (render_surfaces.py's src_sha256, a record field) and this recorder's
                    # parse are not the render's reads.
                    f, who = sys._getframe(1), ""
                    for _ in range(8):   # past the standard library's own wrappers (pathlib, io) to who asked
                        if f is None:
                            break
                        who = f.f_code.co_filename
                        if os.path.basename(who) not in ("pathlib.py", "io.py", "_pyio.py", "codecs.py",
                                                         "genericpath.py", "shutil.py"):
                            break
                        f = f.f_back
                    if who.startswith("<frozen") or os.path.basename(who) in (
                            "linecache.py", "tokenize.py", "render_surfaces.py", "board_deps.py", "runpy.py",
                            "pkgutil.py", "traceback.py", "inspect.py"):
                        return
                    self._read(p)
                else:
                    self._read(p)
            elif event in ("os.listdir", "os.scandir"):
                p = self._path(args[0] if args and args[0] is not None else ".")
                if p:
                    self._list(p)
            elif event == "glob.glob":
                p = self._path(os.path.dirname(str(args[0]).split("*")[0].split("?")[0].split("[")[0]) or ".")
                if p:
                    self._list(p)
            elif event == "os.rename":
                p = self._path(args[1])
                if p:
                    self._write(p)
            elif event == "os.mkdir":
                p = self._path(args[0])
                if p and not os.path.exists(p):
                    self.made.add(p)
                    self.missing.setdefault(p, self._stack())
            elif event == "sqlite3.connect":
                p = self._path(args[0])
                if p:
                    self._read(p)
            elif event == "subprocess.Popen":
                self._child(args)
        except Exception as e:  # noqa: BLE001  a record that fails says so; the render goes on
            self.never.append(f"recorder error on {event}: {type(e).__name__}: {e}"[:300])
        finally:
            self.busy.on = False

    def _child(self, args: tuple) -> None:
        exe, argv = (tuple(args) + (None, None))[:2]
        if isinstance(argv, str):   # Windows: the command line, already joined
            items = [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', argv)]
        else:
            items = [argv] if isinstance(argv, (bytes, os.PathLike)) else list(argv or [])
        strs = [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else a for a in items if a is not None]
        strs = [s for s in strs if isinstance(s, str)]
        first = os.path.basename(str(exe or (strs[0] if strs else ""))).lower()
        if first.startswith("python") or first.startswith("pythonw") or first in ("py.exe", "py"):
            self.never.append(f"a Python child process (its code and reads are not recorded): {' '.join(strs)[:160]}")
        for s in strs[1:] if strs else []:
            cands = []
            if len(s) < 600 and not s.startswith("-") and os.path.isabs(s):
                cands.append(s)
            else:   # a path inside an argument (an ffmpeg filter string escapes its colon)
                cands += [m.group(0).rstrip(" ") for m in ARG_PATH.finditer(s.replace("\\:", ":").replace("\\\\", "\\"))]
            for c in cands:
                p = self._path(c)
                if p is None:
                    continue
                if os.path.exists(p):
                    self._read(p)
                elif p.startswith((self.tree, self.temp)) or os.path.isdir(os.path.dirname(p)):
                    self.wrote.setdefault(p, self._stack())   # the child's output (a pattern or a file to come)
                    if "%" in p:
                        self.patterns.append(re.compile("".join(
                            r"\d+" if re.fullmatch(r"%0?\d*d", t) else re.escape(t)
                            for t in re.split(r"(%0?\d*d)", p)) + "$"))

    def _thread_hook(self, frame, event, _arg):   # a thread the profiler does not see: its calls, plainly
        if event == "call":
            self._thread_seen.add((frame.f_back.f_code if frame.f_back else None, frame.f_code))

    def _patch(self) -> None:
        rec = self

        def wrap(mod, name):
            orig = getattr(mod, name)

            def probe(path, *a, **k):
                try:
                    r = orig(path, *a, **k)
                except OSError:
                    if rec.on and not getattr(rec.busy, "on", False) and not k.get("dir_fd"):
                        rec.busy.on = True
                        try:
                            rec._probe(path, None)
                        finally:
                            rec.busy.on = False
                    raise
                if rec.on and not getattr(rec.busy, "on", False) and not isinstance(path, int) \
                        and not k.get("dir_fd"):
                    rec.busy.on = True
                    try:
                        if hasattr(r, "st_mode"):
                            rec._probe(path, r)
                        else:   # a yes / no answer (os.access, a C isdir): ask what is there
                            try:
                                rec._probe(path, orig_stat(path))
                            except OSError:
                                rec._probe(path, None)
                    finally:
                        rec.busy.on = False
                return r
            probe.__name__ = getattr(orig, "__name__", name)
            setattr(mod, name, probe)
        orig_stat = os.stat
        for name in ("stat", "lstat", "access"):
            wrap(os, name)
        for name in ("exists", "isfile", "isdir", "lexists", "islink"):
            if type(getattr(os.path, name)).__name__ == "builtin_function_or_method":   # a C fast path: no os.stat
                wrap(os.path, name)
        env_cls = type(os.environ)
        orig_get, orig_iter, orig_set = env_cls.__getitem__, env_cls.__iter__, env_cls.__setitem__
        bulk = threading.local()

        def setitem(self_, key, value):
            if isinstance(key, str):
                rec.env_own.add(env_name(key))
            return orig_set(self_, key, value)

        def getitem(self_, key):
            copying = getattr(bulk, "key", None) == key   # dict(os.environ): a copy for a child, not a read
            mine = not rec.on or not isinstance(key, str) or copying or env_name(key) in rec.env_own
            try:
                v = orig_get(self_, key)
            except KeyError:
                if not mine:
                    rec.env.setdefault(env_name(key), "unset")
                raise
            if not mine:
                rec.env.setdefault(env_name(key), env_state(key, v))
            return v

        def iterate(self_):
            for k in orig_iter(self_):
                bulk.key = k
                yield k
            bulk.key = None
        env_cls.__getitem__, env_cls.__iter__, env_cls.__setitem__ = getitem, iterate, setitem

    def start(self) -> None:
        if not self.on:
            self._patch()
            sys.addaudithook(self._audit)
            threading.setprofile(self._thread_hook)
            self.on = True
            self.prof.enable()

    def begin(self, job: str) -> None:
        if self.on and self.current is None and not self.jobs:
            self.current = job
        elif self.on:
            self.never.append(f"a second job ({job}) in one process: its record is not its own")

    def end(self, job: str) -> None:
        """Close the process's record as `job`'s."""
        if not self.on:
            return
        self.prof.disable()
        self.on = False
        ran: set[str] = set()
        files: set[str] = set()
        edges: dict[str, set[str]] = {}
        pairs = list(self._thread_seen)
        for e in self.prof.getstats():
            if isinstance(e.code, str):
                continue
            pairs.append((None, e.code))
            for sub in e.calls or ():
                if not isinstance(sub.code, str):
                    pairs.append((e.code, sub.code))
        for caller, callee in pairs:
            s = self.symbol(callee)
            if not s:
                continue
            files.add(s[0])
            if s[1]:
                name = f"{s[0]}::{s[1]}"
                ran.add(name)
                c = self.symbol(caller) if caller is not None else None
                if c and c[1] and f"{c[0]}::{c[1]}" != name:
                    edges.setdefault(f"{c[0]}::{c[1]}", set()).add(name)
        for m in list(sys.modules.values()):
            fn = getattr(m, "__file__", None)
            if isinstance(fn, str) and fn.endswith(".py"):
                p = _norm(fn)
                if p.startswith(self.tree) and not (set(Path(p).parts) & SKIP_PARTS):
                    files.add(self._real(p[len(self.tree):].replace(os.sep, "/")))
        syspath = sorted({_norm(p)[len(self.tree):].replace(os.sep, "/") if _norm(p) != self.tree.rstrip(os.sep)
                          else "" for p in sys.path if p and (_norm(p) + os.sep).startswith(self.tree)})
        try:
            import board_inputs
            sha_of = board_inputs.sha256_file
        except ImportError:
            sha_of = _sha_file
        rec: dict[str, Any] = {"functions": sorted(ran), "modules": sorted(files),
                               "imports": sorted(self.imports), "syspath": syspath, "env": dict(sorted(self.env.items())),
                               "tree_reads": {}, "reads": {}, "tree_probes": {}, "probes": {}, "tree_listed": {},
                               "listed": {}, "built": {}}
        never = list(self.never)

        def split(p: str) -> tuple[str, str]:
            if p + os.sep == self.tree:
                return "tree_", ""
            return ("tree_", p[len(self.tree):].replace(os.sep, "/")) if p.startswith(self.tree) else ("", p)
        for p in sorted(self.reads):
            if os.path.basename(p).lower().startswith(".env"):
                never.append(f"an .env file was read (never hashed, so never keyed): {p}")
                continue
            kind, name = split(p)
            sha = sha_of(p)
            if sha is None:
                if not self._own(p):
                    never.append(f"an input that is gone at the end of the job: {p}")
                continue
            rec[kind + "reads"][name] = sha
        for p, state in sorted(self.probes.items()):
            kind, name = split(p)
            if name not in rec[kind + "reads"]:
                rec[kind + "probes"][name] = state
        for p, sha in sorted(self.listed.items()):
            kind, name = split(p)
            rec[kind + "listed"][name] = sha

        def below(fn: str) -> list[str]:
            seen, todo = set(), [fn]
            while todo:
                for c in edges.get(todo.pop(), ()):
                    if c not in seen:
                        seen.add(c)
                        todo.append(c)
            return sorted(seen)
        made = {**self.missing, **self.wrote}
        for p, stack in sorted(made.items()):   # what this process created and left behind: an artefact it built
            if not os.path.isfile(p) or not (p.startswith(self.tree) or p.startswith(self.temp)):
                continue
            sha = sha_of(p)
            if sha:
                kind, name = split(p)
                stack = [g for g in stack if not _harness(g)]
                rec["built"][("T:" + name) if kind else name] = {"sha256": sha, "stack": stack}
                for g in stack:
                    if g not in rec.setdefault("below", {}):
                        rec["below"][g] = below(g)
        if never:
            rec["never"] = never
        self.jobs[job] = rec
        self.current = None

    def record(self) -> dict[str, Any]:
        """The process's record, read when it is about to end: a file it made and has removed since (its scratch
        folder) is not an artefact it built."""
        for rec in self.jobs.values():
            gone = [p for p in rec["built"]
                    if not os.path.isfile(self.tree + p[2:].replace("/", os.sep) if p.startswith("T:") else p)]
            for p in gone:
                del rec["built"][p]
            used = {g for e in rec["built"].values() for g in e["stack"]}
            if "below" in rec:
                rec["below"] = {g: v for g, v in rec["below"].items() if g in used}
        return {"schema": SCHEMA, "jobs": dict(self.jobs)}


def _sha_file(p: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


_recorder: Recorder | None = None


def install(tree: Path, skip: Iterable[str | Path] = ()) -> Recorder:
    """Start recording in this process (idempotent). Called by run_surfaces.py before render_surfaces.py loads."""
    global _recorder
    if _recorder is None:
        _recorder = Recorder(tree, skip)
        _recorder.start()
    return _recorder


def job_begin(name: str) -> None:
    if _recorder is not None:
        _recorder.begin(name)


def job_end(name: str) -> None:
    if _recorder is not None:
        _recorder.end(name)


def write(path: Path) -> bool:
    """This process's record to `path` (False: not recording)."""
    if _recorder is None:
        return False
    Path(path).write_text(json.dumps(_recorder.record(), indent=0, sort_keys=True), encoding="utf-8", newline="\n")
    return True


def merge(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """One DEPS record from the processes' (a job is one process's)."""
    jobs: dict[str, Any] = {}
    for p in parts:
        if p.get("schema") != SCHEMA:
            raise RuntimeError(f"board deps: a record of schema {p.get('schema')!r}, not {SCHEMA}")
        for j, rec in (p.get("jobs") or {}).items():
            if j in jobs:
                raise RuntimeError(f"board deps: job {j!r} recorded by two processes")
            jobs[j] = rec
    return {"schema": SCHEMA, "jobs": jobs}


# ------------------------------------------------------------------------------------------------ artefacts
def store_dir() -> Path:
    return Path(tempfile.gettempdir()) / STORE


def untracked(repo: Path) -> dict[str, str]:
    """{tree path (lower-cased): sha256} of the worktree's files git does not track, tools/ data and code caches
    left out: what a render could read that no commit names (an on-disk cache). An .env file is named, never read."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--others"], capture_output=True, check=True,
                         env=env).stdout.decode("utf-8", errors="replace")
    found: dict[str, str] = {}
    names = [n for n in out.split("\0") if n and not n.startswith(TOOLS) and not set(n.split("/")) & SKIP_PARTS
             and os.path.splitext(n)[1] not in SKIP_SUFFIXES]
    if len(names) > 3000:   # a working checkout (runs, logs, a database): not a tree a reuse can be proven on
        return {"*": f"{len(names)} untracked files"}
    for n in names:
        if os.path.basename(n).lower().startswith(".env"):
            found[n.lower()] = "unread"
            continue
        sha = _sha_file(str(Path(repo) / n))
        if sha:
            found[n.lower()] = sha
    return found


def _inherit(functions: set[str], src: dict[str, Any]) -> set[str]:
    """The builders of an artefact a job touched: the build's functions that the job also ran and what they ever
    called; the whole build stack and what it called when the job ran none of them (it reached the file some other
    way)."""
    below = src.get("below") or {}
    stack = [g for g in (src.get("stack") or ()) if not _harness(g)]
    shared = functions & set(stack)
    use = shared or set(stack)
    return set().union(*[set(below.get(g) or ()) for g in use], use)


def resolve(deps: dict[str, Any], tree: Tree, before: dict[str, str] | None = None, store: Path | None = None,
            temp: str | None = None, remember: bool = True) -> dict[str, Any]:
    """Tie every artefact a job touched to its builders (in place; returns a summary). `before`: the tree's
    untracked files when the board started (untracked()). Writes the builders of every observed build to the store
    (`remember`: only for a worktree that is its commit, whose module hashes the store entry then states)."""
    store = Path(store) if store else store_dir()
    temp = _norm(temp or tempfile.gettempdir()) + os.sep
    before = before or {}
    jobs = deps.get("jobs") or {}
    built: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for j, r in jobs.items():
        for p, e in (r.get("built") or {}).items():
            built.setdefault(p, []).append((j, {**e, "below": r.get("below") or {}}))
    summary: dict[str, Any] = {"observed": sorted(built), "from_store": [], "unproven": []}
    if "*" in before:
        for r in jobs.values():
            r.setdefault("whole", []).append(f"the tree holds {before['*']}: what a render read of them is not known")
        summary["unproven"].append(f"tree: {before['*']}")
        before = {}

    def stored(sha: str) -> dict[str, Any] | None:
        f = store / f"{sha}.json"
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
        except (OSError, ValueError):
            return None

    def disk(rel: str) -> str | None:
        return _sha_file(str(tree.repo / rel))
    for j, r in jobs.items():
        touched: dict[str, str | None] = {}
        mine = r.get("built") or {}
        for rel, sha in (r.get("tree_reads") or {}).items():
            if tree.real(rel) is None and not rel.startswith(TOOLS):
                touched["T:" + rel] = sha
        for rel, state in (r.get("tree_probes") or {}).items():
            if state == "file" and tree.real(rel) is None and not rel.startswith(TOOLS):
                first = next((e["sha256"] for _k, e in built.get("T:" + rel, [])), None)
                touched["T:" + rel] = before.get(rel) or first or disk(rel)
        for d in r.get("tree_listed") or {}:   # a listed folder: every untracked file directly in it
            pre = d.strip("/") + "/" if d.strip("/") else ""
            for rel, sha in before.items():
                if rel.startswith(pre) and "/" not in rel[len(pre):] and tree.real(rel) is None:
                    touched.setdefault("T:" + rel, sha)
        for p, sha in (r.get("reads") or {}).items():
            if p.startswith(temp):
                touched[p] = sha
        fns = set(r.get("functions") or ())
        for p, sha in sorted(touched.items()):
            if p in mine:
                continue
            if sha == "unread":
                r.setdefault("never", []).append(f"an .env file in the tree was touched: {p}")
                continue
            src = next((e for k, e in built.get(p, []) if k != j and e["sha256"] == sha), None)
            origin, add = "this board", None
            if src is not None:
                add = _inherit(fns, src)
            else:
                src = stored(sha) if sha else None
                if src is not None:   # a stored builder whose module changed since the build is no proof
                    add = _inherit(fns, src)
                    files = sorted({n.partition("::")[0] for n in add})
                    tree.fetch(files)
                    if any(tree.module(f) is None or tree.module(f).full != (src.get("files") or {}).get(f)
                           for f in files):
                        add = None
                    else:
                        origin = "store"
                        if p not in summary["from_store"]:
                            summary["from_store"].append(p)
            if add is None:
                r.setdefault("whole", []).append(f"{p}: an artefact whose build was not observed")
                if p not in summary["unproven"]:
                    summary["unproven"].append(p)
                continue
            fns |= add
            r.setdefault("artefacts", {})[p] = {"sha256": sha, "builders": origin}
        r["functions"] = sorted(fns)
    if not remember:
        return summary
    try:
        store.mkdir(parents=True, exist_ok=True)
    except OSError:
        return summary
    for p, entries in built.items():
        for _j, e in entries:
            stack = [g for g in (e.get("stack") or ()) if not _harness(g)]
            below = {g: (e.get("below") or {}).get(g) or [] for g in stack}
            names = set(stack).union(*[set(v) for v in below.values()])
            files = sorted({n.partition("::")[0] for n in names})
            tree.fetch(files)
            rec = {"path": p, "sha256": e["sha256"], "stack": stack, "below": below, "commit": tree.commit,
                   "files": {f: tree.module(f).full for f in files if tree.module(f)}}
            part = store / f"{e['sha256']}.{os.getpid()}.part"
            try:
                part.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8", newline="\n")
                os.replace(part, store / f"{e['sha256']}.json")
            except OSError:
                pass
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree", type=Path)
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--keys", type=Path)
    ap.add_argument("--what-if", nargs=3, metavar=("DEPS", "BASE", "HEAD"))
    a = ap.parse_args(argv)
    repo = a.tree.resolve()
    if a.keys:
        deps = json.loads(a.keys.read_text(encoding="utf-8"))
        t = Tree(repo, a.commit)
        for job, rec in sorted(deps["jobs"].items()):
            k = job_key(t, rec)
            print(f"{k['sha256'][:16]}  {len(rec.get('functions') or ()):5} functions, {len(k['rows']):5} rows"
                  f"{'  WHOLE' if k['whole'] else ''}  {job}")
        return 0
    if a.what_if:
        deps = json.loads(Path(a.what_if[0]).read_text(encoding="utf-8"))
        tb, th = Tree(repo, a.what_if[1]), Tree(repo, a.what_if[2])
        n = 0
        for job, rec in sorted(deps["jobs"].items()):
            kb, kh = job_key(tb, rec), job_key(th, rec)
            if kb["sha256"] != kh["sha256"]:
                n += 1
                diff = changed_rows(kb, kh)
                print(f"CHANGED {job}: " + "; ".join(diff[:6]) + (f" ... ({len(diff)})" if len(diff) > 6 else ""))
        print(f"{n} of {len(deps['jobs'])} jobs change")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
