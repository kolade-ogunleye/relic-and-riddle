"""QF-79: every script is classified in scripts/README.md, archived one-offs are referenced by nothing outside the
archive and refuse to start without their override flag, and the scripts the launcher, the release deployer and the
promotion procedure run stay at the paths their consumers name (docs/reviews/project-quality-review-2026-11-05.md,
QF-79)."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
README = SCRIPTS / "README.md"
CLASSES = {"live", "operator", "data", "validation", "eval", "one-off", "archived"}
ROW = re.compile(r"^\| `([^`]+)` \| ([a-z-]+) \|")


def script_files():
    return sorted(p.relative_to(SCRIPTS).as_posix() for p in SCRIPTS.rglob("*")
                  if p.is_file() and p.suffix in {".py", ".ps1"} and "__pycache__" not in p.parts)


def inventory():
    assert README.exists(), "scripts/README.md is missing"
    rows = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            assert m.group(1) not in rows, f"listed twice: {m.group(1)}"
            rows[m.group(1)] = m.group(2)
    return rows


def test_every_script_is_listed_with_a_class():
    rows = inventory()
    missing = [f for f in script_files() if f not in rows]
    assert not missing, f"scripts without a row in scripts/README.md: {missing}"


def test_every_row_names_an_existing_script_and_a_defined_class():
    rows = inventory()
    assert not [f for f in rows if not (SCRIPTS / f).is_file()], "rows for scripts that do not exist"
    assert {f: c for f, c in rows.items() if c not in CLASSES} == {}


def test_archived_means_in_the_archive_folder():
    rows = inventory()
    assert {f for f, c in rows.items() if c == "archived"} == {f for f in rows if f.startswith("archive/")}


def test_no_archived_script_is_referenced_outside_the_archive():
    archived = [Path(f).stem for f, c in inventory().items() if c == "archived"]
    assert archived, "the archive holds the completed one-offs"
    hits = []
    for stem in archived:
        out = subprocess.run(["git", "-C", str(ROOT), "grep", "-lIw", stem, "--", ".", ":!scripts/archive",
                              ":!scripts/README.md", ":!docs/reviews/quality-fixes-2026-11/QF-79"],
                             capture_output=True, text=True, encoding="utf-8", check=False)
        assert out.returncode in (0, 1), out.stderr
        hits += [f"{stem}: {path}" for path in out.stdout.splitlines()]
    assert not hits, "archived scripts still referenced:\n" + "\n".join(hits)


def archived_scripts():
    return sorted(p.name for p in (SCRIPTS / "archive").glob("*.py"))


# What a child process may inherit: enough for Windows to start Python, nothing that names the checkout, a bot,
# a provider or a database.
PASSED_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")


def hermetic_copy(tmp_path):
    """The archive and the package marker, copied into tmp_path/scripts (all the guard needs, and no src beside
    it), and the audited start beside them."""
    copy = tmp_path / "scripts"
    (copy / "archive").mkdir(parents=True)
    shutil.copy2(SCRIPTS / "__init__.py", copy / "__init__.py")
    for p in (SCRIPTS / "archive").glob("*.py"):
        shutil.copy2(p, copy / "archive" / p.name)
    (tmp_path / "hermetic_boot.py").write_text(BOOT, encoding="utf-8", newline="\n")
    home = tmp_path / "home"
    home.mkdir()
    return copy, home


def hermetic_env(home):
    env = {k: os.environ[k] for k in PASSED_ENV if k in os.environ}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home), "LOCALAPPDATA": str(home),
                "TEMP": str(home), "TMP": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                "PYTHONIOENCODING": "utf-8"})
    return env


# The child starts here, then runs the archived script the way python itself does by path or with -m (runpy), under
# an audit hook (PEP 578) that records every write, database open, connection and child process and refuses the
# ones outside tmp_path, so a script whose guard was removed cannot touch the checkout, the network or another
# process. The record goes to stderr at exit through os.write, which raises no audit event.
BOOT = """import atexit, json, os, runpy, sys
TMP = os.path.normcase(os.path.realpath(sys.argv[1]))
HOW, TARGET = sys.argv[2], sys.argv[3]
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
FS = {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace", "os.truncate", "os.symlink", "os.link",
      "os.chmod", "os.utime", "os.chown"}
NET = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.sendto", "socket.bind",
       "urllib.Request", "http.client.connect"}
PROC = {"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile"}
seen, busy = [], []

def inside(p):
    if isinstance(p, int) or p in (":memory:", b":memory:", ""):
        return True
    full = os.path.normcase(os.path.realpath(os.path.join(os.getcwd(), os.fsdecode(p))))
    return full == TMP or full.startswith(TMP + os.sep)

def hook(event, args):
    if busy:
        return
    if event == "open":
        path, mode, flags = args
        if not ((mode and any(c in mode for c in "wax+")) or (not mode and flags & WRITE_FLAGS)):
            return
        kind, paths = "write", [path]
    elif event in FS:
        kind, paths = "write", [a for a in args if isinstance(a, (str, bytes, os.PathLike))]
    elif event == "sqlite3.connect":
        kind, paths = "db", [args[0]]
    elif event in NET:
        kind, paths = "net", []
    elif event in PROC:
        kind, paths = "process", []
    else:
        return
    busy.append(1)
    try:
        ok = kind in ("write", "db") and all(inside(p) for p in paths)
        seen.append([event, kind, [p if isinstance(p, int) else os.fsdecode(p) for p in paths], ok])
    finally:
        busy.pop()
    if not ok:
        raise PermissionError(f"hermetic run: {event} refused")

atexit.register(lambda: os.write(2, ("\\nHERMETIC-AUDIT " + json.dumps(seen) + "\\n").encode()))
sys.addaudithook(hook)
sys.argv = [TARGET]
if HOW == "module":
    runpy.run_module(TARGET, run_name="__main__", alter_sys=True)
else:
    sys.path[0] = os.path.dirname(TARGET)
    runpy.run_path(TARGET, run_name="__main__")
"""


def run_hermetic(tmp_path, copy, home, stem, how):
    target = f"scripts.archive.{stem}" if how == "module" else str(copy / "archive" / f"{stem}.py")
    return subprocess.run([sys.executable, str(tmp_path / "hermetic_boot.py"), str(tmp_path), how, target],
                          cwd=tmp_path, env=hermetic_env(home), capture_output=True, text=True, encoding="utf-8",
                          timeout=60, check=False)


def audit(out):
    """The child's audit record: [event, kind, paths, allowed] per write, database open, connection or process."""
    lines = [ln for ln in out.stderr.splitlines() if ln.startswith("HERMETIC-AUDIT ")]
    assert len(lines) == 1, f"the audit hook did not report: {out.stderr}"
    return json.loads(lines[0].removeprefix("HERMETIC-AUDIT "))


def tree_state(root):
    """Every file and folder under tmp_path with its mtime and size, so a write anywhere below it shows."""
    return {p.as_posix(): (p.stat().st_mtime_ns, p.stat().st_size if p.is_file() else None)
            for p in [root, *root.rglob("*")]}


def scripts_content():
    """The checkout's scripts/ by content, __pycache__ left out (other test workers write bytecode there)."""
    return {p.relative_to(SCRIPTS).as_posix(): p.read_bytes() for p in SCRIPTS.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


@pytest.mark.parametrize("how", ["path", "module"])
@pytest.mark.parametrize("name", archived_scripts())
def test_archived_script_refuses_to_start_without_the_flag(name, how, tmp_path):
    """Started by path or as a module, an archived one-off exits 1 with its refusal before it imports src or writes
    anything. It runs from a copy in tmp_path with an environment built from scratch and an audit hook that refuses
    any write, database open, connection or child process outside tmp_path, so a removed guard fails against the
    copy (no src to import, no app root, bot token or provider key) and never reaches the checkout's database or
    Telegram chat."""
    copy, home = hermetic_copy(tmp_path)
    copied = tree_state(tmp_path)
    scripts = scripts_content()
    out = run_hermetic(tmp_path, copy, home, Path(name).stem, how)
    assert out.returncode == 1, out.stderr
    assert f"scripts/archive/{name} is an archived one-off" in out.stderr, out.stderr
    assert "--i-know-this-is-archived" in out.stderr
    assert "Traceback" not in out.stderr
    assert audit(out) == [], "the refusal wrote, opened a database, connected or started a process"
    assert tree_state(tmp_path) == copied, "the refusal wrote under tmp_path"
    assert scripts_content() == scripts, "the refusal changed the checkout's scripts"
    assert not (SCRIPTS / "data").exists()


# An unguarded stand-in for an archived script whose guard was removed: it looks for the checkout the way the
# archived scripts and src/config.py do (src on sys.path, RELIC_APP_ROOT, its own parents) and for anything that
# would let it reach a bot, a provider or a database, then writes a file and opens a database inside tmp_path
# (allowed) and tries a write, a folder and a database next to tmp_path, a connection and a child process (each
# refused by the hook before it happens).
DUMMY = """import importlib.util, json, os, socket, sqlite3, subprocess, sys
from pathlib import Path
here = Path(__file__).resolve()
tmp = Path(os.getcwd())
outside = str(tmp.parent / (tmp.name + "-outside"))
roots = [Path(v) for k, v in os.environ.items() if k == "RELIC_APP_ROOT"] + list(here.parents)
def refused(fn):
    try:
        fn()
    except PermissionError:
        return True
    return False
(tmp / "inside.txt").write_text("x")
sqlite3.connect(tmp / "inside.db").close()
print(json.dumps({
    "src": importlib.util.find_spec("src") is not None,
    "env": sorted(k for k in os.environ if k.startswith(("RELIC", "TELEGRAM", "DATABASE", "DB_", "PYTHONPATH"))
                  or any(w in k for w in ("KEY", "TOKEN", "SECRET"))),
    "checkout": [str(p) for p in roots if (p / "src" / "config.py").exists() or (p / ".env").exists()],
    "db": [str(p) for p in roots if (p / "data" / "pipeline.db").exists()],
    "refused": [refused(lambda: Path(outside + ".txt").write_text("x")),
                refused(lambda: os.mkdir(outside + "-dir")),
                refused(lambda: sqlite3.connect(outside + ".db")),
                refused(lambda: socket.create_connection(("127.0.0.1", 9), timeout=1)),
                refused(lambda: subprocess.run([sys.executable, "-c", "pass"]))],
}))
"""


@pytest.mark.parametrize("how", ["path", "module"])
def test_the_refusal_harness_cannot_reach_the_checkout(how, tmp_path):
    """Run the way the refusal test runs the archive, a script with no guard at all finds no src to import, no app
    root, bot token, provider key or database variable, no checkout above it and no database to open; its write and
    database inside tmp_path are recorded and allowed, and its write, folder, database, connection and child process
    outside are refused before they happen: so the refusal test is safe even when the guard it checks is gone."""
    copy, home = hermetic_copy(tmp_path)
    (copy / "archive" / "unguarded_probe.py").write_text(DUMMY, encoding="utf-8", newline="\n")
    out = run_hermetic(tmp_path, copy, home, "unguarded_probe", how)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"src": False, "env": [], "checkout": [], "db": [], "refused": [True] * 5}
    record = audit(out)
    allowed = [p for _, _, paths, ok in record if ok for p in paths]
    assert allowed and all(Path(p).resolve().is_relative_to(tmp_path.resolve()) for p in allowed), allowed
    assert {kind for _, kind, _, ok in record if ok} == {"write", "db"}
    assert {kind for _, kind, _, ok in record if not ok} == {"write", "db", "net", "process"}
    assert (tmp_path / "inside.txt").is_file() and (tmp_path / "inside.db").is_file()
    assert not [p.name for p in tmp_path.parent.iterdir() if p.name.startswith(f"{tmp_path.name}-outside")]
    env = hermetic_env(home)
    assert set(env) <= set(PASSED_ENV) | {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
                                          "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTHONIOENCODING"}
    assert not [k for k, v in env.items() if k != "PATH" and str(ROOT).lower() in v.lower()]


def test_live_scripts_stay_at_the_paths_their_consumers_use():
    rows = inventory()
    live = {f for f, c in rows.items() if c == "live"}
    for consumer, name in (("deployment/windows/Invoke-ContentReleaseDeployer.ps1", "release_gate.py"),
                           ("deployment/windows/Promote-ContentRelease.ps1", "runtime_probe.py"),
                           ("main.py", "eval_prompts.py"), ("src/control.py", "fetch_music.py"),
                           ("src/telegram_images.py", "fetch_commons_images.py"),
                           (".github/workflows/tests.yml", "check_lock.py")):
        assert Path(name).stem in (ROOT / consumer).read_text(encoding="utf-8"), consumer
        assert name in live and (SCRIPTS / name).is_file(), name
    for name in ("service_runtime.py", "release_gate.py", "runtime_probe.py", "windows_runtime.ps1"):
        assert name in live and (SCRIPTS / name).is_file(), name
