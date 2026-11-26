"""What code each board job's pixels came from, observed while it rendered, and the key that says whether that code
is the same at another commit (cycle 5, lane BOARD: the incremental board, board_reuse.py).

Why observed: a case's code dependence cannot be read off this tree statically. Measured at integration 4
(`python board_deps.py <tree> --measure`): the import closure of every render entry (imports inside functions
included) is 161-164 of the 177 src modules, and a name-based symbol reach (every identifier a function mentions,
resolved to every symbol of that name) reaches 7 312 of 9 495 symbols from ANY single root. A module-level or a
static symbol-level map would therefore reuse nothing. So the map is recorded:

RECORD (while rendering; a record only, the pixels are untouched). run_surfaces.py starts a profiler (cProfile: C
callbacks, call edges, no line tracing) before render_surfaces.py loads; render_surfaces.py's job() marks each job's
window. Per job, DEPS (<out>/DEPS.json, "jobs") lists the tree's functions that
  - ran inside the job's window (any thread), or
  - ran outside every job's window in that process before the job ended (import time, the harness's own picks and
    set-up: they can shape any job), or
  - were ever called (in that process, before the job ended) from one of those, transitively: a memoised function
    that returned a cached value in this job is still followed to what computed the value, whichever job did.
A function is the outermost def or method (`Class.method`) holding the code that ran; render_surfaces.py's `main` is
split into its nested functions (each job's closure) and its own body.

KEY (at any commit, from git objects or the worktree; no render). For a job's recorded functions:
  - each function's AST hash (comments and blank lines are not code);
  - every module-level value and class header they mention by name (identifier, attribute, identifier-like string,
    the constant head of a built name), in ANY module, and what those mention, transitively; a function a value
    mentions (a table built at import time) with the values it mentions;
  - the residue of every module (module-level code that is not a def, class, import or plain assignment).
A function that did not run in a job can change without changing the job's key. A changed token, constant, table or
class header changes the key of every job that names it. Everything that is not Python is outside this key and is
handled by board_reuse.py as a change to every job (fonts, assets, data files, the toolchain, the inputs).

Not covered (board_reuse.py re-renders a random sample of the reused jobs for these): state one job leaves for
another through a module global read by a different function; an attribute fetched by a name that appears nowhere in
the recorded code (getattr(x, name) with `name` from a data file); code run through exec/eval.

    python board_deps.py <tree> --measure                      the static maps' sizes (why they are not used)
    python board_deps.py <tree> --keys <DEPS.json> [--commit C] each job's key at the worktree (or a commit)
    python board_deps.py <tree> --what-if <DEPS.json> <base> <head>   the jobs whose key differs between two commits
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
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

FIX = "docs/reviews/freeze-panel-2026-11-04/fixtures"
HERE = "docs/reviews/freeze-system-2026-11-15"
RENDER = "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"
CODE_ROOTS = ("src", "tests", FIX, HERE, "docs/reviews/quality-fixes-2026-11/QF-35a")
# a function whose nested functions are symbols of their own; the rest of it is "<name>.<body>"
SPLIT = {RENDER: ("main",)}
SCHEMA = "board-deps/1"
SELF = f"{HERE}/board_deps.py"
# The harness's dispatchers: they call every job's closure in turn and keep no value of their own, so the calls they
# made for an EARLIER job are not followed into a later one (else every job of a process would inherit the code of
# all the jobs before it, and a board's reuse would depend on how its jobs were sharded). What ran in a job's own
# window is recorded for it all the same.
DISPATCH = (f"{RENDER}::main.job", f"{RENDER}::main.<body>")
OUTSIDE = ""     # the bucket of code run outside every job's window
MODULE = "<module>"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------------ reading a tree
def git_reader(tree: Path, commit: str) -> tuple[Callable[[str], str | None], list[str]]:
    """(read(path) -> text or None, the .py paths) of `commit`, from git objects (never the worktree)."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    names = subprocess.run(["git", "-C", str(tree), "ls-tree", "-r", "--name-only", commit, "--", *CODE_ROOTS],
                           capture_output=True, text=True, check=True, env=env).stdout.splitlines()
    names = [n for n in names if n.endswith(".py")]
    cache: dict[str, str | None] = {}

    def read(n: str) -> str | None:
        if n not in cache:
            r = subprocess.run(["git", "-C", str(tree), "cat-file", "blob", f"{commit}:{n}"], capture_output=True,
                               env=env)
            cache[n] = r.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n") if r.returncode == 0 else None
        return cache[n]
    return read, names


def worktree_reader(tree: Path) -> tuple[Callable[[str], str | None], list[str]]:
    """(read, the .py paths) of the worktree as it is on disk (CRLF read as LF)."""
    names = []
    for top in CODE_ROOTS:
        names += [p.relative_to(tree).as_posix() for p in sorted((tree / top).rglob("*.py"))
                  if "__pycache__" not in p.parts]

    def read(n: str) -> str | None:
        p = tree / n
        return p.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n") if p.is_file() else None
    return read, names


# ------------------------------------------------------------------------------------------------ symbols
class Mentions(ast.NodeVisitor):
    """The names a piece of code can reach another symbol by."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.heads: set[str] = set()

    def visit_Name(self, n: ast.Name) -> None:
        self.names.add(n.id)

    def visit_Attribute(self, n: ast.Attribute) -> None:
        self.names.add(n.attr)
        self.generic_visit(n)

    def visit_Constant(self, n: ast.Constant) -> None:
        if isinstance(n.value, str) and len(n.value) < 80 and IDENT.match(n.value):
            self.names.add(n.value)

    def visit_JoinedStr(self, n: ast.JoinedStr) -> None:
        first = n.values[0] if n.values else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and IDENT.match(first.value):
            self.heads.add(first.value)
        self.generic_visit(n)

    def visit_BinOp(self, n: ast.BinOp) -> None:
        if isinstance(n.op, ast.Add) and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str) \
                and IDENT.match(n.left.value):
            self.heads.add(n.left.value)
        self.generic_visit(n)


class Symbol:
    __slots__ = ("file", "name", "short", "kind", "sha", "names", "heads", "lines")

    def __init__(self, file: str, name: str, kind: str, nodes: list[ast.AST], lines: tuple[int, int] = (0, 0)):
        self.file, self.name, self.kind, self.lines = file, name, kind, lines
        self.short = name.rsplit(".", 1)[-1]
        self.sha = _sha("\n".join(ast.dump(n) for n in nodes))
        m = Mentions()
        for n in nodes:
            m.visit(n)
        self.names, self.heads = m.names, m.heads


def _targets(t: ast.AST) -> Iterable[str]:
    if isinstance(t, ast.Name):
        yield t.id
    elif isinstance(t, (ast.Tuple, ast.List)):
        for e in t.elts:
            yield from _targets(e)
    elif isinstance(t, ast.Starred):
        yield from _targets(t.value)


def _without(node: ast.AST, drop: tuple[type, ...]) -> ast.AST:
    n = copy.copy(node)
    n.body = [s for s in node.body if not isinstance(s, drop)] or [ast.Pass()]
    return n


class _Lift(ast.NodeTransformer):
    """Takes the functions defined anywhere in a function's own statements (not inside another function) out of it."""

    def __init__(self) -> None:
        self.found: list[ast.AST] = []
        self.depth = 0

    def _func(self, node):
        if self.depth == 0:   # the split function itself
            self.depth = 1
            self.generic_visit(node)
            self.depth = 0
            return node
        self.found.append(node)
        return ast.Pass()

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func

    def visit_Lambda(self, node):
        return node   # a lambda is its statement's own code


def _span(node: ast.AST) -> tuple[int, int]:
    first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    return first, node.end_lineno or node.lineno


def parse_module(file: str, text: str) -> list[Symbol]:
    """The module's symbols: functions (kind "code"), methods ("code", named Class.method), class headers ("class"),
    module-level assigned names ("value") and its residue ("residue", named <residue>)."""
    try:
        mod = ast.parse(text)
    except SyntaxError as e:   # unparsable: all residue, so any change to it changes every key
        return [Symbol(file, "<residue>", "residue", [ast.Constant(value=_sha(text) + str(e))])]
    out: list[Symbol] = []
    residue: list[ast.AST] = []
    split = SPLIT.get(file, ())

    def level(stmts: list[ast.stmt]) -> None:
        for s in stmts:
            if isinstance(s, FUNC):
                if s.name in split:   # its nested functions (in a loop or a block too) are symbols of their own
                    body = _Lift()
                    rest = body.visit(copy.deepcopy(s))
                    for inner in body.found:
                        out.append(Symbol(file, f"{s.name}.{inner.name}", "code", [inner], _span(inner)))
                    out.append(Symbol(file, f"{s.name}.<body>", "code", [rest], _span(s)))
                else:
                    out.append(Symbol(file, s.name, "code", [s], _span(s)))
            elif isinstance(s, ast.ClassDef):
                out.append(Symbol(file, s.name, "class", [_without(s, FUNC)], _span(s)))
                for inner in s.body:
                    if isinstance(inner, FUNC):
                        out.append(Symbol(file, f"{s.name}.{inner.name}", "code", [inner], _span(inner)))
            elif isinstance(s, (ast.Import, ast.ImportFrom)):
                continue   # an import is no code of its own: what it brings is reached by name
            elif isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                names = [n for t in (s.targets if isinstance(s, ast.Assign) else [s.target]) for n in _targets(t)]
                for n in names:
                    out.append(Symbol(file, n, "value", [s]))
                if not names:   # an attribute or item assignment at import time (a patch of another module)
                    residue.append(s)
            elif isinstance(s, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                head = copy.copy(s)   # a guarded block: its header is residue, its statements module-level ones
                bodies = []
                for field in ("body", "orelse", "finalbody"):
                    part = getattr(s, field, None)
                    if part:
                        bodies.append(part)
                        setattr(head, field, [ast.Pass()])
                if isinstance(s, ast.Try):
                    head.handlers = []
                    bodies += [h.body for h in s.handlers]
                residue.append(head)
                for b in bodies:
                    level(b)
            elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
                continue   # a docstring
            else:
                residue.append(s)
    level(mod.body)
    if residue:
        out.append(Symbol(file, "<residue>", "residue", residue))
    return out


class Universe:
    """The symbols of a tree's Python at one state (a commit's git objects, or the worktree). Files are parsed when
    first asked for; values and class headers are looked up by name over src/, the fixtures, the board tools and
    every tests module a recorded function lives in."""

    def __init__(self, read: Callable[[str], str | None], names: list[str], extra: Iterable[str] = ()):
        self.read = read
        self.all_names = set(names)
        self.base = sorted({n for n in names if n.startswith("src/") or n.startswith(FIX + "/")
                            or n.startswith(HERE + "/") or n == RENDER} | (set(extra) & set(names)))
        self._files: dict[str, dict[str, Symbol]] = {}
        self._named: dict[str, list[Symbol]] | None = None

    def file(self, f: str) -> dict[str, Symbol]:
        if f not in self._files:
            text = self.read(f)
            syms: dict[str, Symbol] = {}
            for s in (parse_module(f, text) if text is not None else []):
                if s.name in syms:   # a name bound twice in a module: one symbol holding both
                    old = syms[s.name]
                    old.sha = _sha(old.sha + s.sha)
                    old.names |= s.names
                    old.heads |= s.heads
                else:
                    syms[s.name] = s
            self._files[f] = syms
        return self._files[f]

    def named(self) -> dict[str, list[Symbol]]:
        """short name -> the values and class headers of that name (any base file)."""
        if self._named is None:
            self._named = {}
            for f in self.base:
                for s in self.file(f).values():
                    if s.kind in ("value", "class"):
                        self._named.setdefault(s.short, []).append(s)
        return self._named

    def code_named(self, short: str) -> list[Symbol]:
        if not hasattr(self, "_code"):
            self._code: dict[str, list[Symbol]] = {}
            for f in self.base:
                for s in self.file(f).values():
                    if s.kind == "code":
                        self._code.setdefault(s.short, []).append(s)
        return self._code.get(short, [])

    def residue(self) -> list[tuple[str, str]]:
        return sorted((f, s.sha) for f in self.base for s in self.file(f).values() if s.kind == "residue")

    def key(self, functions: Iterable[str]) -> dict[str, Any]:
        """The code key of a job whose recorded functions are `functions` ("file::name")."""
        rows: dict[tuple[str, str], str] = {}
        todo: list[Symbol] = []
        missing = []
        for fn in sorted(set(functions)):
            f, _, name = fn.partition("::")
            s = self.file(f).get(name)
            if s is None or s.kind != "code":
                rows[(f, name)] = "absent"
                missing.append(fn)
                continue
            rows[(f, name)] = s.sha
            todo.append(s)
            owner = name.rsplit(".", 1)[0] if "." in name else None
            c = self.file(f).get(owner) if owner else None
            if c is not None and c.kind == "class" and (f, owner) not in rows:   # a method's own class header
                rows[(f, owner)] = c.sha
                todo.append(c)
        named = self.named()
        via_value: set[tuple[str, str]] = set()
        while todo:
            s = todo.pop()
            hits = [t for n in s.names for t in named.get(n, ())]
            for h in s.heads:
                hits += [t for n, ts in named.items() if n.startswith(h) for t in ts]
            if s.kind in ("value", "class"):   # a table built at import time: the functions it names, and their values
                for n in s.names:
                    for t in self.code_named(n):
                        if (t.file, t.name) not in rows and (t.file, t.name) not in via_value:
                            via_value.add((t.file, t.name))
                            rows[(t.file, t.name)] = t.sha
                            todo.append(t)
            for t in hits:
                if (t.file, t.name) not in rows:
                    rows[(t.file, t.name)] = t.sha
                    todo.append(t)
        listed = sorted((f, n, sha) for (f, n), sha in rows.items())
        res = self.residue()
        return {"sha256": _sha(json.dumps(listed) + json.dumps(res)), "symbols": len(listed),
                "files": len({r[0] for r in listed}), "functions": len(set(functions)), "absent": missing,
                "rows": listed, "residue": res}


def universe(tree: Path, commit: str | None = None, deps: dict | None = None) -> Universe:
    read, names = git_reader(tree, commit) if commit else worktree_reader(tree)
    extra = {fn.split("::")[0] for fns in ((deps or {}).get("jobs") or {}).values() for fn in fns}
    return Universe(read, names, extra)


def changed_rows(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """The symbols (and residue files) whose hash differs between two key records."""
    ra = {(f, n): s for f, n, s in a["rows"]} | {(f, "<residue>"): s for f, s in a["residue"]}
    rb = {(f, n): s for f, n, s in b["rows"]} | {(f, "<residue>"): s for f, s in b["residue"]}
    return sorted(f"{f}::{n}" for f, n in set(ra) | set(rb) if ra.get((f, n)) != rb.get((f, n)))


# ------------------------------------------------------------------------------------------------ recording
class Recorder:
    """The functions of `tree` that run in this process, per job window (see the module docstring)."""

    def __init__(self, tree: Path):
        import cProfile
        self.tree = os.path.normcase(os.path.abspath(tree)) + os.sep
        self.prof = cProfile.Profile(subcalls=True, builtins=False)
        self.edges: dict[str, set[str]] = {}
        self.dispatch: set[str] = set(DISPATCH)
        self.ran: dict[str, set[str]] = {OUTSIDE: set()}
        self.jobs: dict[str, list[str]] = {}
        self.current = OUTSIDE
        self._spans: dict[str, list[tuple[int, int, str]]] = {}
        self._code: dict[Any, str | None] = {}
        self._thread_seen: set[tuple[str | None, str | None]] = set()
        self.on = False

    # -- code object -> "file::outermost def"
    def _file_spans(self, rel: str) -> list[tuple[int, int, str]]:
        if rel not in self._spans:
            try:
                text = Path(self.tree, rel).read_bytes().decode("utf-8", errors="replace")
                self._spans[rel] = sorted((s.lines[0], s.lines[1], s.name) for s in parse_module(rel, text)
                                          if s.kind == "code" and not s.name.endswith(".<body>"))
                self._spans[rel] += [(s.lines[0], s.lines[1], s.name) for s in parse_module(rel, text)
                                     if s.name.endswith(".<body>")]   # the split function's own body: last resort
            except OSError:
                self._spans[rel] = []
        return self._spans[rel]

    def symbol(self, code) -> str | None:
        if code not in self._code:
            out = None
            fn = getattr(code, "co_filename", None)
            if isinstance(fn, str):
                p = os.path.normcase(os.path.abspath(fn))
                if p.startswith(self.tree) and ".venv" not in p:
                    rel = Path(p[len(self.tree):]).as_posix()
                    # normcase lower-cases on Windows: take the path's real spelling from the tree
                    real = self._real(rel)
                    line = code.co_firstlineno
                    name = next((n for a, b, n in self._file_spans(real) if a <= line <= b), MODULE)
                    if real != SELF:   # the recorder is not render code
                        out = f"{real}::{name}"
            self._code[code] = out
        return self._code[code]

    def _real(self, rel: str) -> str:
        if not hasattr(self, "_reals"):
            self._reals: dict[str, str] = {}
            for top in CODE_ROOTS:
                for p in Path(self.tree, top).rglob("*.py"):
                    r = p.relative_to(self.tree).as_posix()
                    self._reals[os.path.normcase(r).replace("\\", "/")] = r
        return self._reals.get(rel, rel)

    # -- windows
    def _thread_hook(self, frame, event, _arg):   # a thread the profiler does not see: its calls, plainly
        if event == "call":
            self._thread_seen.add((self.symbol(frame.f_back.f_code) if frame.f_back else None,
                                   self.symbol(frame.f_code)))

    def start(self) -> None:
        if not self.on:
            threading.setprofile(self._thread_hook)
            self.prof.enable()
            self.on = True

    def _harvest(self) -> None:
        self.prof.disable()
        bucket = self.ran.setdefault(self.current, set())
        pairs = list(self._thread_seen)
        self._thread_seen.clear()
        for e in self.prof.getstats():
            callee = self.symbol(e.code) if not isinstance(e.code, str) else None
            if callee:
                bucket.add(callee)
            for sub in e.calls or ():
                pairs.append((callee, self.symbol(sub.code) if not isinstance(sub.code, str) else None))
        for caller, callee in pairs:
            if callee:
                bucket.add(callee)
                if caller and caller != callee:
                    self.edges.setdefault(caller, set()).add(callee)
        self.prof.clear()
        self.prof.enable()

    def begin(self, job: str) -> None:
        if self.on:
            self._harvest()
            self.current = job

    def end(self, job: str) -> None:
        """Close `job`'s window: its functions, the outside bucket so far, and everything those ever called."""
        if not self.on:
            return
        self._harvest()
        self.current = OUTSIDE
        seen = set(self.ran.get(job, ())) | self.ran[OUTSIDE]
        todo = [s for s in seen if s not in self.dispatch]
        while todo:
            for c in self.edges.get(todo.pop(), ()):
                if c in self.dispatch:
                    seen.add(c)
                    continue
                if c not in seen:
                    seen.add(c)
                    todo.append(c)
        self.jobs[job] = sorted(s for s in seen if not s.endswith("::" + MODULE))

    def record(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "jobs": dict(self.jobs)}


_recorder: Recorder | None = None


def install(tree: Path) -> Recorder:
    """Start recording in this process (idempotent). Called by run_surfaces.py before render_surfaces.py loads."""
    global _recorder
    if _recorder is None:
        _recorder = Recorder(tree)
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
    """One DEPS record from the shards' (a job is one shard's)."""
    jobs: dict[str, list[str]] = {}
    for p in parts:
        for j, fns in (p.get("jobs") or {}).items():
            if j in jobs:
                raise RuntimeError(f"board deps: job {j!r} recorded by two shards")
            jobs[j] = fns
    return {"schema": SCHEMA, "jobs": jobs}


# ------------------------------------------------------------------------------------------------ measures
def import_closure(tree: Path, commit: str | None, entry: str) -> tuple[int, int]:
    """(src modules reached from src/<entry>.py through every import statement, src modules in all)."""
    read, names = git_reader(tree, commit) if commit else worktree_reader(tree)
    src = {n[4:-3]: n for n in names if n.startswith("src/") and n.count("/") == 1}
    seen: set[str] = set()
    todo = [entry]
    while todo:
        m = todo.pop()
        if m in seen or m not in src:
            continue
        seen.add(m)
        try:
            mod = ast.parse(read(src[m]) or "")
        except SyntaxError:
            continue
        for node in ast.walk(mod):
            if isinstance(node, ast.ImportFrom):
                if node.level == 1 and node.module:
                    todo.append(node.module.split(".")[0])
                elif node.level == 1:
                    todo += [a.name for a in node.names]
                elif node.module and node.module.startswith("src"):
                    todo += [a.name for a in node.names] + node.module.split(".")[1:2]
            elif isinstance(node, ast.Import):
                todo += [a.name.split(".")[1] for a in node.names if a.name.startswith("src.")]
    return len(seen), len(src)


def name_reach(tree: Path, commit: str | None, root: tuple[str, str]) -> tuple[int, int]:
    """(symbols a name-based reach gets to from one function, symbols in all): every name a reached symbol mentions
    resolved to every symbol of that name. The static symbol-level map; measured to show why it is not used."""
    read, names = git_reader(tree, commit) if commit else worktree_reader(tree)
    u = Universe(read, names)
    by: dict[str, list[Symbol]] = {}
    total = 0
    for f in u.base:
        for s in u.file(f).values():
            by.setdefault(s.short, []).append(s)
            total += 1
    start = u.file(root[0]).get(root[1])
    seen = {(start.file, start.name)} if start else set()
    todo = [start] if start else []
    while todo:
        s = todo.pop()
        for n in s.names:
            for t in by.get(n, ()):
                if (t.file, t.name) not in seen:
                    seen.add((t.file, t.name))
                    todo.append(t)
    return len(seen), total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree", type=Path)
    ap.add_argument("--commit")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--keys", type=Path)
    ap.add_argument("--what-if", nargs=3, metavar=("DEPS", "BASE", "HEAD"))
    a = ap.parse_args(argv)
    tree = a.tree.resolve()
    if a.measure:
        for entry in ("graphics", "multi_format", "end_card", "visual_engine", "design_cards", "then_now"):
            n, total = import_closure(tree, a.commit, entry)
            print(f"import closure of src/{entry}.py: {n} of {total} src modules")
        for root in (("src/then_now.py", "render"), ("src/graphics.py", "render_scale"), ("src/end_card.py", "frames")):
            n, total = name_reach(tree, a.commit, root)
            print(f"name-based reach from {root[0]}::{root[1]}: {n} of {total} symbols")
        return 0
    if a.keys:
        deps = json.loads(a.keys.read_text(encoding="utf-8"))
        u = universe(tree, a.commit, deps)
        for job, fns in sorted(deps["jobs"].items()):
            k = u.key(fns)
            print(f"{k['sha256'][:16]}  {k['functions']:5} functions, {k['symbols']:5} symbols, {k['files']:3} files  {job}")
        return 0
    if a.what_if:
        deps = json.loads(Path(a.what_if[0]).read_text(encoding="utf-8"))
        ub, uh = universe(tree, a.what_if[1], deps), universe(tree, a.what_if[2], deps)
        n = 0
        for job, fns in sorted(deps["jobs"].items()):
            kb, kh = ub.key(fns), uh.key(fns)
            if kb["sha256"] != kh["sha256"]:
                n += 1
                diff = changed_rows(kb, kh)
                print(f"CHANGED {job}: " + "; ".join(d.rsplit("/", 1)[-1] for d in diff[:6])
                      + (f" ... ({len(diff)})" if len(diff) > 6 else ""))
        print(f"{n} of {len(deps['jobs'])} jobs change")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
