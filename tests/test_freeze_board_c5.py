"""Design-freeze board tooling, cycle 5 (docs/reviews/freeze-system-2026-11-15): the review loop's own invariants,
never pinned pictures.

- the incremental board (board_deps.py, board_reuse.py): a job's key covers everything its render can depend on
  and what cannot be proven renders. One planted change per kind of input (PLANTS: tracked data, a tests module's
  value, a module-level import, a new dunder, the builder of an on-disk cache, fonts, fixtures, listings, probes,
  shadowing modules, the harness and the board's own scripts) must render every job that can depend on it; the
  inputs git does not hold are checked on the disk; the recorder is run for real in a process of its own; the gate
  refuses a stale reuse even when the plan's key is made blind, and proves the sample by content hash;
- every record key render_surfaces.py writes is one the reuse knows how to split by job;
- the changed-surfaces report (board_diff.py): changed / added / removed / unchanged, the changed box, one pair per
  changed image, a strip pair per changed clip;
- the evidence gaps of panel 6: every row case has its own full-size file, the hook clip is drawn on every ground,
  each caption fixture has its whole run, the board-drawn social frames have masthead records; the expected cases
  follow the names the board writes.
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15"
QF35A = ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"


@pytest.fixture(scope="module")
def mods():
    for p in (str(BOARD), str(QF35A)):
        if p not in sys.path:
            sys.path.insert(0, p)
    names = ("board_deps", "board_reuse", "board_diff", "board", "board_extras", "board_motion", "board_table",
             "board_inputs", "board_evidence", "exp_cases")
    return {n: importlib.import_module(n) for n in names}


# ------------------------------------------------------------------------------------------------ a module's parts
MODULE = '''"""doc"""
import os
from functools import lru_cache
from .heading import set_heading

SIZE = 40


@dataclass
class Form:
    size: int = 1

    def lines(self):
        return [self.size]


def card(x, pad=SIZE):
    return set_heading(x) + pad


def cover(x):
    return x + 1
'''


def test_module_level_code_is_everything_but_function_bodies(mods):
    """The part of a module a job depends on without running any function of it: a body can change freely; an
    import, a constant, a default, a decorator, a class attribute, a new function or a new method cannot."""
    bd = mods["board_deps"]
    base = bd.Module("src/m.py", MODULE)
    same = {"body": MODULE.replace("return x + 1", "return x + 2"),
            "method body": MODULE.replace("return [self.size]", "return [self.size, 0]"),
            "comment": MODULE + "# a note\n"}
    for what, text in same.items():
        m = bd.Module("src/m.py", text)
        assert m.skeleton == base.skeleton, what
    assert bd.Module("src/m.py", same["body"]).full != base.full
    assert bd.Module("src/m.py", same["comment"]).full == base.full
    other = {
        "an import re-pointed": MODULE.replace("from .heading import set_heading",
                                               "from .heading import set_heading_c5 as set_heading"),
        "a constant": MODULE.replace("SIZE = 40", "SIZE = 41"),
        "a default": MODULE.replace("pad=SIZE", "pad=SIZE + 1"),
        "a decorator": MODULE.replace("@dataclass\n", "@dataclass(frozen=True)\n"),
        "a class attribute": MODULE.replace("size: int = 1", "size: int = 2"),
        "a new dunder": MODULE.replace("    def lines(self):", "    def __post_init__(self):\n        self.size *= 2\n\n"
                                                                 "    def lines(self):"),
        "a new method": MODULE.replace("    def lines(self):", "    def f(self):\n        return 1\n\n    def lines(self):"),
        "a new function": MODULE + "\n\ndef set_heading(x):\n    return 0\n",
        "a module-level statement": MODULE + "\nos.environ.setdefault('X', '1')\n",
    }
    for what, text in other.items():
        assert bd.Module("src/m.py", text).skeleton != base.skeleton, what
    assert set(base.units) == {"Form.lines", "card", "cover"}
    assert bd.Module("src/m.py", "def broken(:\n").full == bd.Module("src/m.py", "def broken(:\n").skeleton


def test_render_surfaces_main_is_split_into_its_job_closures(mods):
    """render_surfaces.py's main holds every job: each nested function is a unit of its own, so a job's key holds
    the closures it ran and main's own body, not the other jobs'."""
    bd = mods["board_deps"]
    m = bd.Module(bd.RENDER, (QF35A / "render_surfaces.py").read_text(encoding="utf-8"))
    names = set(m.units)
    assert {"main.job", "main.<body>", "main.captions"} <= names, sorted(names)[:20]
    assert "main" not in names and sum(n.startswith("main.") for n in names) > 20
    spans = {n: (a, b) for a, b, n in m.spans}
    assert spans["main.<body>"][0] < spans["main.job"][0] < spans["main.job"][1] < spans["main.<body>"][1]


def test_tracked_paths_are_global_unless_their_reads_are_recorded(mods):
    bd = mods["board_deps"]
    for p in ("data/cover_art_direction.json", "data/geo/x.geojson", "fonts/a.ttf", "assets/x.png", "uv.lock",
              "pyproject.toml", "src/table.json", "tools/stress/corpus_stats.json", ".gitattributes"):
        assert bd.path_class(p) == "global", p
    for p in ("tests/fixtures/a.json", "docs/reviews/x/y.png", "README.md", "scripts/x.ps1"):
        assert bd.path_class(p) == "recorded", p
    for p in ("src/a.py", "tests/test_x.py", "tools/stress/corpus.py"):
        assert bd.path_class(p) == "code", p
    assert bd.module_names("src/heading.py", [""]) == {"src.heading"}
    assert bd.module_names("docs/fx/fixtures.py", ["", "docs/fx"]) == {"docs.fx.fixtures", "fixtures"}
    assert bd.module_names("src/pkg/__init__.py", [""]) == {"src.pkg"}


# ------------------------------------------------------------------------------------------------ the key, by plants
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")).stdout.strip()


HARNESS = '''def helper():
    return 0


def main():
    def cards():
        return 1

    def cover():
        return 2

    def job(fn):
        return fn()
    job(cards)
    job(cover)
'''
FILES = {
    "src/__init__.py": "",
    "src/cards.py": "from .shared import set_heading\n\n\ndef card():\n    return set_heading(4)\n",
    "src/cover.py": "from .shared import Form\n\n\ndef cover():\n    return Form().size\n",
    "src/shared.py": "from .heading import set_heading\n\nSIZE = 4\n\n\nclass Form:\n    size = 1\n\n"
                     "    def lines(self):\n        return [self.size]\n\n\ndef unused(x, pad=1):\n    return x\n",
    "src/heading.py": "def set_heading(x):\n    return x\n\n\ndef set_heading_c5(x):\n    return x * 0.8\n",
    "src/font_assets.py": "def derived_font():\n    return _cmap()\n\n\ndef _cmap():\n    return 1\n",
    "src/table.json": "{}",
    "tests/__init__.py": "",
    "tests/spec.py": 'SECTION = {"label": "Living quarters"}\n\n\ndef test_spec():\n    assert SECTION\n',
    "tests/data.json": "{}",
    "tests/unread.json": "{}",
    "tests/sized.json": "{}",
    "tests/there.json": "{}",
    "tests/fixtures/one.json": "{}",
    "data/focus.json": '{"box": [0.2, 0.3]}',
    "data/geo/land.geojson": "{}",
    "fonts/face.txt": "face",
    "assets/mark.txt": "mark",
    "tools/stress/stats.json": "{}",
    "pyproject.toml": "[project]\n",
    "uv.lock": "lock 1\n",
    "docs/notes.md": "notes\n",
    "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py": HARNESS,
    "docs/reviews/freeze-system-2026-11-15/board_speed.py": "def install():\n    return 1\n",
    "docs/reviews/freeze-system-2026-11-15/run_surfaces.py": "import board_speed\nboard_speed.install()\n",
}
RS = "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"
SPEED = "docs/reviews/freeze-system-2026-11-15/board_speed.py"
RUNNER = "docs/reviews/freeze-system-2026-11-15/run_surfaces.py"
COMMON = ["src/__init__.py", "src/cards.py", "src/cover.py", "src/shared.py", "src/heading.py", "src/font_assets.py",
          RS, SPEED, RUNNER]


def _records(dev: Path, br) -> dict:
    """Two jobs' records as the recorder writes them: `cards` ran card() and read tests data and a dev run; `cover
    amber` ran cover() and loaded the tests module whose SECTION it draws. Both loaded every src module."""
    def rec(functions, modules, **more):
        base = {"functions": functions, "modules": sorted(COMMON + modules),
                "imports": ["src", "src.cards", "src.cover", "src.shared", "src.heading", "board_speed", "json"],
                "syspath": ["", "docs/reviews/freeze-system-2026-11-15"], "env": {}, "tree_reads": {}, "reads": {},
                "tree_probes": {}, "probes": {}, "tree_listed": {}, "listed": {}, "built": {}}
        base.update(more)
        return base
    run = dev / "run.json"
    cards = rec(["src/cards.py::card", "src/heading.py::set_heading", f"{RS}::main.cards", f"{RS}::main.job",
                 f"{RS}::main.<body>", f"{SPEED}::install"], [],
                imports=["src", "src.cards", "src.shared", "src.heading", "board_speed", "json", "local_settings"],
                tree_reads={"tests/data.json": "x", "src/font_assets.py": "x"}, tree_probes={"tests/absent.json": "absent", "tests/sized.json": "file:stat", "tests/there.json": "file"},
                tree_listed={"tests/fixtures": "x"}, reads={str(run): br.sha256_file(run)},
                probes={str(dev / "later.json"): "absent"}, listed={str(dev): br.Disk(dev).names(str(dev))},
                env={"RR_BOARD_TEST_FLAG": "unset"})
    cover = rec(["src/cover.py::cover", f"{RS}::main.cover", f"{RS}::main.job", f"{RS}::main.<body>",
                 f"{SPEED}::install"], ["tests/__init__.py", "tests/spec.py"])
    cover["imports"] += ["tests", "tests.spec"]
    return {"schema": "board-deps/2", "jobs": {"cards": cards, "cover amber": cover}}


@pytest.fixture()
def boards(mods, tmp_path, monkeypatch):
    """A repository and a previous board of its first commit, with two jobs' records (see _records)."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    br = mods["board_reuse"]
    monkeypatch.delenv("RR_BOARD_TEST_FLAG", raising=False)
    (tmp_path / "temp").mkdir()      # this test's own temp folder: the dev runs below are not "under the temp folder"
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))
    repo = tmp_path / "repo"
    for name, text in FILES.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text("cache/\n", encoding="utf-8", newline="\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    head = _git(repo, "rev-parse", "HEAD")
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "run.json").write_text("{}", encoding="utf-8")
    env = {"toolchain": {"python": "3"}, "tool_data": {}, "sha256": "env"}
    prev = tmp_path / "prev"
    (prev / "surfaces").mkdir(parents=True)
    surfaces = {"brand": "default", "src_sha256": "x", "seconds": {"cards": 5.0, "cover amber": 2.0}, "errors": {},
                "surfaces": {"card__hook__amber__916": {"sha256": "c", "frames": 3},
                             "cover__amber__reel": {"sha256": "r", "frames": 1}}, "input_files": {}}
    for n in surfaces["surfaces"]:
        (prev / "surfaces" / f"{n}.png").write_bytes(b"png:" + n.encode())
    (prev / "surfaces.json").write_text(json.dumps(surfaces), encoding="utf-8")
    (prev / "board.json").write_text(json.dumps({
        "brand": "default", "tree": {"head": head, "dirty_vs_head": False, "path": str(repo), "untracked": {}},
        "reuse_env": env}), encoding="utf-8")
    (prev / "DEPS.json").write_text(json.dumps(_records(dev, br)), encoding="utf-8")
    (prev / "MANIFEST.json").write_text(json.dumps({
        "repo": {"head": head, "dirty_vs_head": False},
        "images": [{"path": f"surfaces/{n}.png", "sha256": br.sha256_file(prev / "surfaces" / f"{n}.png")}
                   for n in surfaces["surfaces"]]}), encoding="utf-8")

    def commit(changes: dict[str, str | None]) -> dict:
        for path, text in changes.items():
            if text is None:
                (repo / path).unlink()
            else:
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text(text, encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two")
        return {"head": _git(repo, "rev-parse", "HEAD"), "dirty_vs_head": False, "path": str(repo)}
    return {"repo": repo, "prev": prev, "env": env, "commit": commit, "head": head, "dev": dev,
            "offered": ["cards", "cover amber"]}


def _plan(mods, b, stamp, **kw):
    args = dict(sample=0, seed=1, palette="default", offered=b["offered"], env=b["env"])
    args.update(kw)
    return mods["board_reuse"].plan(b["repo"], b["prev"], args["palette"], stamp, args["env"], args["offered"],
                                    args["sample"], args["seed"])


def _rendered(p: dict) -> set[str]:
    return {j for j, r in p["jobs"].items() if r["status"] != "reused"}


BOTH, CARDS, COVER, NONE = {"cards", "cover amber"}, {"cards"}, {"cover amber"}, set()
# Every kind of input a render can read that git holds, planted: {what: (the change, the jobs that must render)}.
# The first four are the review's reproductions (REVIEW-board-c5.md defects 1-4) on this small tree.
PLANTS = {
    "1 tracked data under data/": ({"data/focus.json": '{"box": [0.05, 0.05]}'}, BOTH),
    "1 tracked data, a new file under data/geo": ({"data/geo/sea.geojson": "{}"}, BOTH),
    "2 a value of a tests module no function of which ran": (
        {"tests/spec.py": FILES["tests/spec.py"].replace("Living quarters", "Living quarters and wells")}, COVER),
    "3 a module-level import re-pointed at a new function": (
        {"src/shared.py": FILES["src/shared.py"].replace("from .heading import set_heading",
                                                         "from .heading import set_heading_c5 as set_heading")}, BOTH),
    "3 a new dunder on a class of a module only loaded": (
        {"src/shared.py": FILES["src/shared.py"].replace(
            "    def lines(self):", "    def __post_init__(self):\n        self.size = 2\n\n    def lines(self):")}, BOTH),
    "3 a new method that overrides": (
        {"src/shared.py": FILES["src/shared.py"].replace(
            "    def lines(self):", "    def f(self):\n        return 0\n\n    def lines(self):")}, BOTH),
    "3 a default argument of a function that did not run": (
        {"src/shared.py": FILES["src/shared.py"].replace("pad=1", "pad=2")}, BOTH),
    "4 the builder of an on-disk cache, read as bytes by its cache key": (
        {"src/font_assets.py": FILES["src/font_assets.py"] + "# a note changes the file's hash\n"}, CARDS),
    "a function of a module the job ran code of": (
        {"src/heading.py": FILES["src/heading.py"].replace("return x * 0.8", "return x * 0.7")}, CARDS),
    "a function the job ran": ({"src/cover.py": FILES["src/cover.py"].replace("Form().size", "Form().size + 1")}, COVER),
    "a comment": ({"src/cover.py": FILES["src/cover.py"] + "# a note\n"}, NONE),
    "a function body of a module the jobs only loaded": (
        {"src/shared.py": FILES["src/shared.py"].replace("    return x\n", "    return x + 1\n")}, NONE),
    "a module-level constant of a module only loaded": (
        {"src/shared.py": FILES["src/shared.py"].replace("SIZE = 4", "SIZE = 5")}, BOTH),
    "a font": ({"fonts/face.txt": "another face"}, BOTH),
    "an asset": ({"assets/mark.txt": "another mark"}, BOTH),
    "a data file beside the code": ({"src/table.json": '{"a": 1}'}, BOTH),
    "tracked tools/ data": ({"tools/stress/stats.json": '{"n": 2}'}, BOTH),
    "the lock file": ({"uv.lock": "lock 2\n"}, BOTH),
    "the project file": ({"pyproject.toml": "[project]\nname = 'x'\n"}, BOTH),
    "a tests fixture the job read": ({"tests/data.json": '{"read": 1}'}, CARDS),
    "a tests fixture the job read, deleted": ({"tests/data.json": None}, CARDS),
    "a tests fixture no job read": ({"tests/unread.json": '{"a": 1}'}, NONE),
    "a tests file whose size or time the job asked for": ({"tests/sized.json": '{"longer": 1}'}, CARDS),
    "a tests file the job only asked the presence of, changed": ({"tests/there.json": '{"a": 1}'}, NONE),
    "a tests file the job only asked the presence of, deleted": ({"tests/there.json": None}, CARDS),
    "a new file in a folder the job listed": ({"tests/fixtures/two.json": "{}"}, CARDS),
    "a file the job looked for and did not find": ({"tests/absent.json": "{}"}, CARDS),
    "a new module an import the job asked for can now find": ({"local_settings.py": "X = 1\n"}, CARDS),
    "a new module nothing asked for": ({"src/extra.py": "X = 1\n"}, NONE),
    "a note under docs/": ({"docs/notes.md": "other notes\n"}, NONE),
    "the harness: the closure one job ran": ({RS: HARNESS.replace("return 1", "return 11")}, CARDS),
    "the harness: its own body": ({RS: HARNESS.replace("    job(cover)\n", "    job(cover)\n    job(cards)\n")}, BOTH),
    "the harness: a helper no job ran": ({RS: HARNESS.replace("return 0", "return 5")}, NONE),
    "the board's memo installed in every render process": (
        {SPEED: FILES[SPEED].replace("return 1", "return 2")}, BOTH),
    "the board's runner script (module-level code only)": ({RUNNER: FILES[RUNNER] + "X = 1\n"}, BOTH),
}


@pytest.mark.parametrize("what", sorted(PLANTS))
def test_a_planted_change_renders_every_job_that_can_depend_on_it(mods, boards, what):
    """The inventory of inputs git holds, one planted change each: the jobs named must NOT be reused, and (so the
    key is not simply "everything") the others are."""
    changes, must_render = PLANTS[what]
    p = _plan(mods, boards, boards["commit"](changes))
    assert _rendered(p) == must_render, {j: (r["status"], r.get("changed")) for j, r in p["jobs"].items()}
    for j in must_render:
        assert p["jobs"][j]["why"] == "inputs in git changed" and p["jobs"][j]["changed"], p["jobs"][j]


def test_inputs_outside_git_are_checked_on_the_disk(mods, boards, monkeypatch):
    """A dev run's file, a path the job looked for, a folder it listed, an environment variable it read: each
    change renders the job that recorded it and only that job."""
    stamp = boards["commit"]({"docs/notes.md": "x\n"})
    dev = boards["dev"]

    def why() -> list[str]:
        p = _plan(mods, boards, stamp)
        assert p["jobs"]["cover amber"]["status"] == "reused"
        return p["jobs"]["cards"].get("changed") or []
    assert why() == []
    (dev / "run.json").write_text('{"changed": 1}', encoding="utf-8")
    assert any("other bytes" in w for w in why())
    (dev / "run.json").unlink()
    assert any("now gone" in w for w in why())
    (dev / "run.json").write_text("{}", encoding="utf-8")
    assert why() == []
    (dev / "later.json").write_text("{}", encoding="utf-8")     # looked for and absent then; a new name in the folder
    got = why()
    assert any("probed absent, now file" in w for w in got) and any("listed, now other names" in w for w in got)
    (dev / "later.json").unlink()
    monkeypatch.setenv("RR_BOARD_TEST_FLAG", "1")
    assert any("RR_BOARD_TEST_FLAG" in w for w in why())


def test_what_is_the_same_for_every_job_refuses_the_whole_reuse(mods, boards):
    br = mods["board_reuse"]
    stamp = boards["commit"]({"docs/notes.md": "x\n"})
    assert _rendered(_plan(mods, boards, stamp)) == set()
    with pytest.raises(br.NoReuse, match="palette"):
        _plan(mods, boards, stamp, palette="second")
    with pytest.raises(br.NoReuse, match="job list"):
        _plan(mods, boards, stamp, offered=["cards"])
    with pytest.raises(br.NoReuse, match="toolchain or tool data"):
        _plan(mods, boards, stamp, env={**boards["env"], "sha256": "other"})
    with pytest.raises(br.NoReuse, match="differs from its HEAD"):
        _plan(mods, boards, {**stamp, "dirty_vs_head": True})
    deps = json.loads((boards["prev"] / "DEPS.json").read_text(encoding="utf-8"))
    (boards["prev"] / "DEPS.json").write_text(json.dumps({**deps, "schema": "board-deps/1"}), encoding="utf-8")
    with pytest.raises(br.NoReuse, match="does not cover every input"):
        _plan(mods, boards, stamp)
    (boards["prev"] / "DEPS.json").unlink()
    with pytest.raises(br.NoReuse, match="DEPS.json"):
        _plan(mods, boards, stamp)


def test_a_job_that_could_not_be_recorded_whole_always_renders(mods, boards):
    """`never` (a Python child, an .env file, an input the job rewrote): rendered, whatever changed. `whole` (an
    artefact nobody saw built): keyed on every module of the code roots, so any code change renders it."""
    deps = json.loads((boards["prev"] / "DEPS.json").read_text(encoding="utf-8"))
    deps["jobs"]["cards"]["never"] = ["a Python child process"]
    deps["jobs"]["cover amber"]["whole"] = ["cache/x.ttf: an artefact whose build was not observed"]
    (boards["prev"] / "DEPS.json").write_text(json.dumps(deps), encoding="utf-8")
    p = _plan(mods, boards, boards["commit"]({"docs/notes.md": "x\n"}))
    assert p["jobs"]["cards"]["status"] == "rendered" and "could not all be recorded" in p["jobs"]["cards"]["why"]
    assert p["jobs"]["cover amber"]["status"] == "reused" and p["jobs"]["cover amber"]["whole"]
    # a function body of a module the job only loaded: reused by a recorded job, rendered by a whole one
    p = _plan(mods, boards, boards["commit"](
        {"src/shared.py": FILES["src/shared.py"].replace("    return x\n", "    return x + 1\n")}))
    assert p["jobs"]["cover amber"]["status"] == "rendered"


def test_the_dirty_stamp_sees_the_whole_tree(mods, boards):
    """An uncommitted edit under data/ (or anywhere) and an untracked file make the tree dirty: no reuse."""
    bi = mods["board_inputs"]
    repo = boards["repo"]
    assert bi.tree_stamp(repo, BOARD)["dirty_vs_head"] is False
    assert "data" in bi.DRAWN_PATHS and "data" in bi.TREE_DATA
    (repo / "data" / "focus.json").write_text('{"box": [0, 0]}', encoding="utf-8", newline="\n")
    assert bi.tree_stamp(repo, BOARD)["dirty_vs_head"] is True
    _git(repo, "checkout", "-q", "--", "data/focus.json")
    (repo / "decisions.txt").write_text("x", encoding="utf-8")
    assert bi.tree_stamp(repo, BOARD)["dirty_vs_head"] is True
    (repo / "decisions.txt").unlink()
    (repo / "cache").mkdir()
    (repo / "cache" / "face.ttf").write_bytes(b"built")
    assert bi.tree_stamp(repo, BOARD)["dirty_vs_head"] is False      # ignored: not dirt, an artefact
    assert mods["board_deps"].untracked(repo) == {"cache/face.ttf": mods["board_reuse"].sha256_file(repo / "cache" / "face.ttf")}


# ------------------------------------------------------------------------------------------------ artefacts
def _artefact_jobs() -> dict:
    blank = {"modules": [], "imports": [], "syspath": [""], "env": {}, "tree_reads": {}, "reads": {},
             "tree_probes": {}, "probes": {}, "tree_listed": {}, "listed": {}, "built": {}}
    builder = {**blank, "functions": ["src/cards.py::card", "src/font_assets.py::derived_font",
                                      "src/font_assets.py::_cmap"],
               "built": {"T:cache/face.ttf": {"sha256": "FACE", "stack": [
                   "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py::main.cards", "src/cards.py::card",
                   "src/font_assets.py::derived_font"]}},
               "below": {"src/cards.py::card": ["src/font_assets.py::derived_font", "src/font_assets.py::_cmap",
                                                "src/heading.py::set_heading"],
                         "src/font_assets.py::derived_font": ["src/font_assets.py::_cmap"]}}
    reader = {**blank, "functions": ["src/cover.py::cover", "src/font_assets.py::derived_font"],
              "tree_probes": {"cache/face.ttf": "file"}}
    stranger = {**blank, "functions": ["src/cover.py::cover"], "tree_probes": {"cache/face.ttf": "file"}}
    return {"schema": "board-deps/2", "jobs": {"cards": builder, "cover amber": reader, "cover other": stranger}}


def test_the_builders_of_an_on_disk_cache_join_every_job_that_reads_it(mods, boards, tmp_path):
    """Review defect 4: the job that found the cache on disk never ran the code that built it. The builders are
    attributed to it from the job that did build it (this board), from the store (an earlier board), or the job is
    keyed on the whole code."""
    bd = mods["board_deps"]
    tree = bd.Tree(boards["repo"], "HEAD")
    store = tmp_path / "store"
    deps = _artefact_jobs()
    summary = bd.resolve(deps, tree, {}, store)
    reader, stranger = deps["jobs"]["cover amber"], deps["jobs"]["cover other"]
    assert "src/font_assets.py::_cmap" in reader["functions"] and "src/cards.py::card" not in reader["functions"]
    assert reader["artefacts"]["T:cache/face.ttf"] == {"sha256": "FACE", "builders": "this board"}
    # a job that reached the file through none of the build's functions takes all of them
    assert {"src/cards.py::card", "src/heading.py::set_heading", "src/font_assets.py::_cmap"} <= set(stranger["functions"])
    assert not any(f.endswith("::main.cards") for f in stranger["functions"])
    assert summary["unproven"] == [] and "whole" not in reader
    # so a change inside the builder changes the reader's key
    before = bd.job_key(tree, reader)["sha256"]
    boards["commit"]({"src/font_assets.py": FILES["src/font_assets.py"].replace("return 1", "return 2")})
    assert bd.job_key(bd.Tree(boards["repo"], "HEAD"), reader)["sha256"] != before
    _git(boards["repo"], "reset", "-q", "--hard", boards["head"])
    # a later board: nobody builds, the file is there; the store holds its builders
    later = _artefact_jobs()
    del later["jobs"]["cards"]
    assert bd.resolve(later, tree, {"cache/face.ttf": "FACE"}, store)["from_store"] == ["T:cache/face.ttf"]
    assert "src/font_assets.py::_cmap" in later["jobs"]["cover amber"]["functions"]
    assert later["jobs"]["cover amber"]["artefacts"]["T:cache/face.ttf"]["builders"] == "store"
    # no store, or a builder that changed since the build: no proof, the job is keyed on the whole code
    unknown = _artefact_jobs()
    del unknown["jobs"]["cards"]
    assert bd.resolve(unknown, tree, {"cache/face.ttf": "FACE"}, tmp_path / "empty")["unproven"] == ["T:cache/face.ttf"]
    assert unknown["jobs"]["cover amber"]["whole"]
    boards["commit"]({"src/font_assets.py": FILES["src/font_assets.py"].replace("return 1", "return 3")})
    stale = _artefact_jobs()
    del stale["jobs"]["cards"]
    bd.resolve(stale, bd.Tree(boards["repo"], "HEAD"), {"cache/face.ttf": "FACE"}, store)
    assert stale["jobs"]["cover amber"]["whole"]
    # an untracked file the job read directly, and one in a folder it listed, count the same way
    direct = _artefact_jobs()
    del direct["jobs"]["cards"]
    direct["jobs"]["cover amber"].update(tree_probes={}, tree_reads={"cache/other.bin": "OTHER"})
    direct["jobs"]["cover other"].update(tree_probes={}, tree_listed={"cache": "x"})
    bd.resolve(direct, tree, {"cache/old.bin": "OLD"}, tmp_path / "empty")
    assert "cache/other.bin" in direct["jobs"]["cover amber"]["whole"][0]
    assert "cache/old.bin" in direct["jobs"]["cover other"]["whole"][0]
    # a working checkout (thousands of untracked files): nothing can be proven
    many = _artefact_jobs()
    bd.resolve(many, tree, {"*": "4000 untracked files"}, tmp_path / "empty")
    assert all(r["whole"] for r in many["jobs"].values())


def test_an_artefact_the_job_built_or_read_must_still_be_its_bytes(mods, boards):
    br, bd = mods["board_reuse"], mods["board_deps"]
    repo = boards["repo"]
    tree = bd.Tree(repo, "HEAD")
    (repo / "cache").mkdir()
    face = repo / "cache" / "face.ttf"
    face.write_bytes(b"built")
    sha = br.sha256_file(face)
    rec = {"tree_probes": {"cache/face.ttf": "absent"}, "built": {"T:cache/face.ttf": {"sha256": sha, "stack": []}}}
    assert br.inputs_now(rec, br.Disk(repo), tree, {}) == []        # absent then, built by the job, there now
    reader = {"tree_probes": {"cache/face.ttf": "file"}, "artefacts": {"T:cache/face.ttf": {"sha256": sha}}}
    assert br.inputs_now(reader, br.Disk(repo), tree, {}) == []
    face.write_bytes(b"other bytes")
    assert any("other bytes" in w for w in br.inputs_now(rec, br.Disk(repo), tree, {}))
    assert any("other bytes" in w for w in br.inputs_now(reader, br.Disk(repo), tree, {}))
    face.unlink()                                                   # gone: built again by the code in the key
    assert br.inputs_now(reader, br.Disk(repo), tree, {}) == []
    lister = {"tree_listed": {"cache": "x"}}
    assert br.inputs_now(lister, br.Disk(repo), tree, {}) == []
    (repo / "cache" / "new.bin").write_bytes(b"x")
    assert any("untracked files changed" in w for w in br.inputs_now(lister, br.Disk(repo), tree, {}))


# ------------------------------------------------------------------------------------------------ the recorder
TREE_FILES = {
    "src/__init__.py": "",
    "src/mod.py": '''import json
import os
import threading
from pathlib import Path

from . import builder
from tests import spec

ROOT = Path(__file__).resolve().parent.parent
TABLE = [n * 2 for n in range(3)]
PICK = lambda x: x + 1


def run():
    data = json.loads((ROOT / "data" / "x.json").read_text(encoding="utf-8"))
    (ROOT / "data" / "none.json").exists()
    names = sorted(os.listdir(ROOT / "data"))
    size = (ROOT / "data" / "y.json").stat().st_size + os.path.getsize(ROOT / "data" / "z.json")
    there = (ROOT / "data" / "w.json").is_file() and os.path.exists(ROOT / "data" / "w.json")
    flag = os.environ.get("RR_BOARD_TEST_FLAG")
    os.environ["RR_BOARD_TEST_OWN"] = "1"
    own = os.environ.get("RR_BOARD_TEST_OWN")
    try:
        import local_settings  # noqa: F401
    except ImportError:
        pass
    t = threading.Thread(target=threaded)
    t.start()
    t.join()
    face = builder.derived()
    return data, names, flag, own, face, spec.SECTION, PICK(1), size, there


def threaded():
    return inner()


def inner():
    return 1


def unused():
    return 0
''',
    "src/builder.py": '''import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def derived():
    key = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]
    path = ROOT / "cache" / key / "face.bin"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    _replace(path, _cmap())
    return path


def _cmap():
    return b"glyphs"


def _replace(path, data):
    part = path.with_suffix(".part")
    part.write_bytes(data)
    os.replace(part, path)
''',
    "tests/__init__.py": "",
    "tests/spec.py": 'SECTION = {"label": "Living quarters"}\n',
    "data/x.json": "{}",
    "data/y.json": "{}",
    "data/z.json": "{}",
    "data/w.json": "{}",
}
DRIVER = '''import json, subprocess, sys
from pathlib import Path
board, tree, out, child = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
sys.path.insert(0, board)
sys.path.insert(0, str(tree))
import board_deps
board_deps.install(tree, skip=[out.parent])
board_deps.job_begin("job")
from src import mod
mod.run()
if child == "child":
    subprocess.run([sys.executable, "-c", "pass"], check=True)
board_deps.job_end("job")
board_deps.write(out)
'''


def _record(tmp_path: Path, name: str, child: str = "none") -> dict:
    tree = tmp_path / "tree"
    if not tree.is_dir():
        for rel, text in TREE_FILES.items():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text(text, encoding="utf-8", newline="\n")
    (tmp_path / "driver.py").write_text(DRIVER, encoding="utf-8", newline="\n")
    out = tmp_path / "out" / f"{name}.json"
    out.parent.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("RR_BOARD_TEST_FLAG", "RR_BOARD_TEST_OWN")}
    env["RR_BOARD_TEST_FLAG"] = "on"
    r = subprocess.run([sys.executable, str(tmp_path / "driver.py"), str(BOARD), str(tree), str(out), child],
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(out.read_text(encoding="utf-8"))["jobs"]["job"]


def test_the_recorder_sees_every_kind_of_input_of_its_process(mods, tmp_path):
    """One real process under the recorder: code (threads too), modules loaded without a function run, imports asked
    for and not found, files read (a .py read as data among them), paths probed, folders listed, the environment,
    and the artefact it built with the stack that built it. A second process finds the artefact on disk."""
    bd = mods["board_deps"]
    rec = _record(tmp_path, "first")
    fns = set(rec["functions"])
    assert {"src/mod.py::run", "src/mod.py::threaded", "src/mod.py::inner", "src/builder.py::derived",
            "src/builder.py::_cmap", "src/builder.py::_replace"} <= fns
    assert "src/mod.py::unused" not in fns and not any("<module>" in f or "board_deps.py" in f for f in fns)
    assert {"src/mod.py", "src/builder.py", "tests/spec.py", "src/__init__.py"} <= set(rec["modules"])
    assert "tests/spec.py" not in {f.split("::")[0] for f in fns}        # loaded, no function of it ran
    assert {"src.mod", "tests.spec", "local_settings"} <= set(rec["imports"]) and "" in rec["syspath"]
    assert set(rec["tree_reads"]) == {"data/x.json", "src/builder.py"}, rec["tree_reads"]
    assert rec["tree_probes"].get("data/none.json") == "absent"
    assert rec["tree_probes"].get("data/y.json") == "file:stat" and rec["tree_probes"].get("data/z.json") == "file:stat"
    assert rec["tree_probes"].get("data/w.json") == "file"      # is_file / exists: only its presence was asked
    assert rec["tree_probes"].get("src/mod.py") in ("file", None)   # Path(__file__).resolve() is a presence question
    assert "data" in rec["tree_listed"]
    assert rec["env"] == {"RR_BOARD_TEST_FLAG": bd.env_state("RR_BOARD_TEST_FLAG", "on")}   # not the name it set itself
    built = {k: v for k, v in rec["built"].items() if k.endswith("face.bin")}
    assert len(built) == 1, rec["built"]
    (path, entry), = built.items()
    assert path.startswith("T:cache/") and entry["stack"][-2:] == ["src/builder.py::derived", "src/builder.py::_replace"]
    assert "src/builder.py::_cmap" in rec["below"]["src/builder.py::derived"]
    assert "never" not in rec
    # the second process: the cache is on disk, its builder never runs here
    again = _record(tmp_path, "second")
    assert "src/builder.py::_cmap" not in again["functions"] and "src/builder.py::derived" in again["functions"]
    assert again["tree_probes"].get(path[2:]) == "file" and not again["built"]
    # ... and resolve gives it the builders from the first one's record
    deps = {"schema": bd.SCHEMA, "jobs": {"first": rec, "second": again}}
    _git(tmp_path / "tree", "init", "-q")
    (tmp_path / "tree" / ".gitignore").write_text("cache/\n__pycache__/\n", encoding="utf-8", newline="\n")
    _git(tmp_path / "tree", "add", "-A")
    _git(tmp_path / "tree", "commit", "-q", "-m", "one")
    bd.resolve(deps, bd.Tree(tmp_path / "tree", "HEAD"), {}, tmp_path / "store")
    assert "src/builder.py::_cmap" in deps["jobs"]["second"]["functions"]
    assert deps["jobs"]["second"]["artefacts"][path]["builders"] == "this board"


def test_a_python_child_process_makes_the_job_unrecordable(mods, tmp_path):
    rec = _record(tmp_path, "child", "child")
    assert any("Python child process" in n for n in rec["never"])


def test_a_path_inside_a_childs_argument_is_an_input(mods, tmp_path):
    """ffmpeg is handed files in plain arguments and inside filter strings (colon escaped), and a fonts folder:
    each is the job's input; the file it is asked to write is the job's own."""
    bd = mods["board_deps"]
    for name in ("in.mp4", "cap.ass", "fonts/a.ttf", "fonts/b.ttf"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_bytes(b"x")

    def esc(q: Path) -> str:
        return str(q).replace(chr(92), "/").replace(":", chr(92) + ":")
    argv = ["ffmpeg", "-i", str(tmp_path / "in.mp4"), "-vf",
            f"subtitles='{esc(tmp_path / 'cap.ass')}':fontsdir='{esc(tmp_path / 'fonts')}'", str(tmp_path / "out.png")]
    rec = bd.Recorder(tmp_path / "tree")
    rec._child((None, subprocess.list2cmdline(argv) if os.name == "nt" else argv, None, None))
    norm = lambda q: os.path.normcase(os.path.abspath(q))  # noqa: E731
    assert {norm(tmp_path / n) for n in ("in.mp4", "cap.ass", "fonts/a.ttf", "fonts/b.ttf")} <= rec.reads
    assert norm(tmp_path / "fonts") in rec.listed and norm(tmp_path / "out.png") in rec.wrote
    assert norm(tmp_path / "out.png") not in rec.reads and rec.never == []
    rec._child((sys.executable, [sys.executable, "-c", "pass"], None, None))
    assert rec.never


def test_every_file_under_tools_and_the_whole_toolchain_are_in_the_environment_record(mods, tmp_path):
    br = mods["board_reuse"]
    for name in ("tools/geo/land.bin", "tools/models/net.onnx", "tools/ffmpeg/bin/ffmpeg.bin", "tools/stress/stats.json"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(name.encode())
    data = br.tool_data(tmp_path)
    assert set(data) == {"tools/geo/land.bin", "tools/models/net.onnx", "tools/ffmpeg/bin/ffmpeg.bin",
                         "tools/stress/stats.json"}
    (tmp_path / "tools/ffmpeg/bin/ffmpeg.bin").write_bytes(b"another build")
    assert br.tool_data(tmp_path)["tools/ffmpeg/bin/ffmpeg.bin"] != data["tools/ffmpeg/bin/ffmpeg.bin"]


def test_recording_is_off_unless_asked_and_merges_by_job(mods):
    bd = mods["board_deps"]
    src = (BOARD / "run_surfaces.py").read_text(encoding="utf-8")
    assert 'os.environ.get("BOARD_DEPS") == "1"' in src
    assert src.index("board_deps.install(") < src.index("os.environ.update(") < src.index("import board_speed")
    merged = bd.merge([{"schema": bd.SCHEMA, "jobs": {"a": {"functions": ["x"]}}},
                       {"schema": bd.SCHEMA, "jobs": {"b": {"functions": ["y"]}}}])
    assert set(merged["jobs"]) == {"a", "b"}
    with pytest.raises(RuntimeError):
        bd.merge([{"schema": bd.SCHEMA, "jobs": {"a": {}}}, {"schema": bd.SCHEMA, "jobs": {"a": {}}}])
    with pytest.raises(RuntimeError):
        bd.merge([{"schema": "board-deps/1", "jobs": {"a": ["x"]}}])


def test_the_board_renders_one_process_per_job_when_it_records(mods):
    """A job's record is whole only when its process ran nothing else: board.py starts one per job (run_jobs)."""
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    run = src[src.index("def run_jobs("):src.index("def run_surfaces(")]
    assert '["--jobs", str(tmp / f"job{k}.jobs")]' in run and 'write_text(f"{name}\\n"' in run
    assert "board_deps.resolve(record, board_deps.Tree(tree, \"HEAD\"), before, remember=clean)" in run
    assert "BOARD_DEPS_SKIP" in run
    body = src[src.index("def run_surfaces("):src.index("def main(")]
    assert "if deps:\n        return run_jobs(" in body
    assert 'stamp["untracked"] = board_deps.untracked(tree)' in src


SURFACE_JOBS = {
    "map__amber_room_heist_1792790550__916": "map run_amber_room_heist_1792790550 916",
    "numbers__damascus_steel_nanotubes_1792732472_short__169": "numbers run_damascus_steel_nanotubes_1792732472_short 169",
    "route__amber_room_heist_1792801257_new_stops__916": "route run_amber_room_heist_1792801257 916",
    "timeline_dense__test_events_amber_room_heist_1792790550__169": "timeline_dense 169",
    "cross_section__test_spec__916": "cross-section 916",
    "thennow__singing_colossi_of_memnon__169": "thennow singing_colossi_of_memnon 169",
    "bridge_follow__3_nazca_lines_runway_paradox": "endcard 3 nazca_lines_runway_paradox",
    "endscreen__0_richat_structure_sahara_eye": "endcard 0 richat_structure_sahara_eye",
    "cover__amber_room_heist__youtube": "cover amber_room_heist",
    "social__gol_gumbaz_whispering_gallery_1793278143__story_story_02": "social run_gol_gumbaz_whispering_gallery_1793278143",
    "card__hook__amber__916": "cards",
    "callouts__reference_pair__916": "callouts 916",
    "captions__voynich_manuscript__badge__ass": "captions",
}


def test_every_surface_name_maps_to_its_job(mods):
    br = mods["board_reuse"]
    for stem, job in SURFACE_JOBS.items():
        assert br.job_of(stem) == job, stem
    assert br.job_of("something__else") is None


def _result_keys() -> set[str]:
    tree = ast.parse((QF35A / "render_surfaces.py").read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "result"
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
                and isinstance(node.ctx, ast.Store)):
            keys.add(node.slice.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "result" and node.args
                and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "result" and isinstance(node.value, ast.Dict):
            keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return keys


def test_every_render_surfaces_record_key_is_split_by_the_reuse(mods):
    """A record key the reuse does not know would be dropped from a reused job without a word: a new key fails here."""
    br, b = mods["board_reuse"], mods["board"]
    known = set(br.BY_JOB) | set(br.BY_SURFACE) | set(br.BY_EITHER) | {
        "src_sha256", "brand", "graphic_runs", "brand_validation", "input_files", "count", "copy_set"}
    assert _result_keys() <= known, _result_keys() - known
    assert set(b.SHARD_ONLY_KEYS) == set(br.BY_JOB) | set(br.BY_SURFACE) | set(br.BY_EITHER)
    assert "deps" not in " ".join(_result_keys())      # the code record is a file beside the result, never a key


def test_split_record_keeps_a_jobs_entries_together(mods):
    br = mods["board_reuse"]
    rec = {"seconds": {"cards": 1.0, "captions": 2.0}, "omissions": {"cards": [{"x": 1}]},
           "surfaces": {"card__hook__amber__916": {"sha256": "a"}, "captions__x__ass": {"sha256": "b"}},
           "errors": {"cards": "boom", "captions__x__frame": "bang"},
           "masthead_records": {"social__r_1__fb_carousel_slide_01": {}}, "brand": "default"}
    part = br.split_record(rec, {"cards"})
    assert part["seconds"] == {"cards": 1.0} and list(part["surfaces"]) == ["card__hook__amber__916"]
    assert part["errors"] == {"cards": "boom"} and "brand" not in part
    assert br.unmapped(rec) == ["masthead_records:social__r_1__fb_carousel_slide_01"]


# ------------------------------------------------------------------------------------------------ the plan and its proof
def _fresh(out: Path, names: dict[str, bytes], jobs: list[str]) -> dict:
    (out / "surfaces").mkdir(parents=True, exist_ok=True)
    for n, data in names.items():
        (out / "surfaces" / f"{n}.png").write_bytes(data)
    return {"brand": "default", "seconds": {j: 1.0 for j in jobs}, "errors": {},
            "surfaces": {n: {"sha256": "new", "frames": 1} for n in names}, "input_files": {}}


STEMS = {"cards": "card__hook__amber__916", "cover amber": "cover__amber__reel"}


def _finish(mods, boards, out: Path, p: dict, different: tuple[str, ...] = ()) -> dict:
    """Carry a plan out as board.py does: every job that is not reused is "rendered" (the previous board's bytes
    and record unless named in `different`), then finish."""
    br = mods["board_reuse"]
    prev = json.loads((boards["prev"] / "surfaces.json").read_text(encoding="utf-8"))
    deps = json.loads((boards["prev"] / "DEPS.json").read_text(encoding="utf-8"))
    todo = br.to_render(p)
    names = {STEMS[j]: (b"other pixels" if j in different else (boards["prev"] / "surfaces" / f"{STEMS[j]}.png").read_bytes())
             for j in todo}
    fresh = _fresh(out, names, todo)
    for j in todo:
        if p["jobs"][j]["status"] == "sampled":
            fresh["surfaces"][STEMS[j]] = prev["surfaces"][STEMS[j]]
    merged = br.finish(p, out, fresh if todo else None, {"schema": "board-deps/2", "jobs": {j: deps["jobs"][j] for j in todo}})
    (out / "surfaces.json").write_text(json.dumps(merged), encoding="utf-8")
    (out / "board.json").write_text(json.dumps({"brand": "default", "reuse_env": boards["env"],
                                                "tree": {"dirty_vs_head": False}}), encoding="utf-8")
    return merged


def test_finish_merges_copies_and_the_gate_check_proves_it(mods, boards, tmp_path):
    br = mods["board_reuse"]
    stamp = boards["commit"]({"src/cover.py": FILES["src/cover.py"].replace("Form().size", "Form().size + 1")})
    p = _plan(mods, boards, stamp)
    assert br.to_render(p) == ["cover amber"]
    out = tmp_path / "out"
    merged = _finish(mods, boards, out, p)
    assert set(merged["surfaces"]) == {"card__hook__amber__916", "cover__amber__reel"} and merged["count"] == 2
    assert merged["seconds"] == {"cards": 5.0, "cover amber": 1.0} and merged["reused_seconds"] == {"cards": 5.0}
    src, dst = boards["prev"] / "surfaces" / "card__hook__amber__916.png", out / "surfaces" / "card__hook__amber__916.png"
    assert dst.read_bytes() == src.read_bytes() and int(dst.stat().st_mtime) == int(src.stat().st_mtime)
    rec = json.loads((out / "REUSE.json").read_text(encoding="utf-8"))
    assert rec["files"]["surfaces/card__hook__amber__916.png"]["status"] == "reused"
    assert rec["files"]["surfaces/cover__amber__reel.png"]["status"] == "rendered"
    assert rec["counts"]["reused"] == 1 and rec["counts"]["rendered"] == 1
    assert json.loads((out / "DEPS.json").read_text(encoding="utf-8"))["jobs"].keys() == {"cards", "cover amber"}
    proven, why = br.verify(out, boards["repo"], stamp["head"])
    assert why == [] and proven == {"surfaces/card__hook__amber__916.png"}
    # a reused file that is not the previous board's bytes
    dst.write_bytes(b"edited")
    assert any("not the byte copy" in w for w in br.verify(out, boards["repo"], stamp["head"])[1])
    dst.write_bytes(src.read_bytes())
    assert br.verify(out, boards["repo"], boards["head"])[1]       # the record is for another commit


def test_the_gate_reads_the_diff_itself_and_does_not_take_the_plans_word(mods, boards, tmp_path, monkeypatch):
    """The review's proof that the first gate agreed with the plan by construction: here the plan's key is made
    blind (it calls everything equal) and the gate still refuses each stale reuse, because it reads the diff and
    checks set membership against the job's recorded inputs."""
    br, bd = mods["board_reuse"], mods["board_deps"]
    blind = lambda tree, rec: {"sha256": "same", "rows": {}, "whole": False, "global_data": {}}  # noqa: E731
    for n, (what, expect) in enumerate([("1 tracked data under data/", "tracked data changed"),
                                        ("2 a value of a tests module no function of which ran", "module-level code"),
                                        ("3 a module-level import re-pointed at a new function", "module-level code"),
                                        ("a function the job ran", "a module the job ran code of"),
                                        ("a tests fixture the job read", "read, probed or listed"),
                                        ("a tests file whose size or time the job asked for", "read, probed or listed"),
                                        ("a tests file the job only asked the presence of, deleted", "read, probed or listed"),
                                        ("a new file in a folder the job listed", "read, probed or listed"),
                                        ("a new module an import the job asked for can now find", "came or went"),
                                        ("the harness: the closure one job ran", "harness code the job ran")]):
        _git(boards["repo"], "reset", "-q", "--hard", boards["head"])
        changes, must_render = PLANTS[what]
        stamp = boards["commit"](changes)
        with monkeypatch.context() as m:
            m.setattr(bd, "job_key", blind)
            p = _plan(mods, boards, stamp)
            assert _rendered(p) == set(), what                       # the blind plan reuses everything
            out = tmp_path / f"out{n}"
            _finish(mods, boards, out, p)
            proven, why = br.verify(out, boards["repo"], stamp["head"])
        stale = {f"surfaces/{STEMS[j]}.png" for j in must_render}
        assert not (proven & stale), what
        assert any(expect in w for w in why), (what, why)
        assert proven == {f"surfaces/{STEMS[j]}.png" for j in BOTH - must_render}, what


def test_the_static_net_renders_a_job_whose_code_names_a_changed_function_it_did_not_run(mods, boards, tmp_path):
    """A job recorded as running nothing of a changed module is reused on the record alone, unless the code in its
    key mentions the changed function's name: then the claim is tested by a render (status "sampled")."""
    br = mods["board_reuse"]
    # cover.py mentions `unused` (in a branch that did not run); shared.unused then changes
    base = boards["commit"]({"src/cover.py": FILES["src/cover.py"] + "\n\ndef other():\n    return unused(1)\n"})
    pb = json.loads((boards["prev"] / "board.json").read_text(encoding="utf-8"))
    pb["tree"]["head"] = base["head"]
    (boards["prev"] / "board.json").write_text(json.dumps(pb), encoding="utf-8")
    man = json.loads((boards["prev"] / "MANIFEST.json").read_text(encoding="utf-8"))
    man["repo"]["head"] = base["head"]
    (boards["prev"] / "MANIFEST.json").write_text(json.dumps(man), encoding="utf-8")
    stamp = boards["commit"]({"src/shared.py": FILES["src/shared.py"].replace("    return x\n", "    return x + 1\n")})
    p = _plan(mods, boards, stamp)
    assert p["jobs"]["cover amber"]["status"] == "sampled" and p["jobs"]["cards"]["status"] == "reused"
    assert p["jobs"]["cover amber"]["net"] == ["src/shared.py::unused"]
    assert p["sample"]["static_net"] == ["cover amber"] and p["sample"]["random"] == []
    power = p["sample"]["power"]
    assert power["rendered_for_the_static_net"] == 1 and power["left_on_the_record"] == 1
    assert power["chance_a_given_stale_job_outside_the_net_is_rendered"] == 0.0 and "set membership" in power["statement"]
    # the render agrees: the reuse stands and the gate proves the sample by content hash
    out = tmp_path / "ok"
    _finish(mods, boards, out, json.loads(json.dumps(p)))
    proven, why = br.verify(out, boards["repo"], stamp["head"])
    assert why == [] and proven == {"surfaces/card__hook__amber__916.png"}
    # the gate refuses a board whose net job was not rendered, and one whose sampled file is not the old bytes
    rec = json.loads((out / "REUSE.json").read_text(encoding="utf-8"))
    rec["jobs"]["cover amber"]["status"] = "reused"
    rec["files"]["surfaces/cover__amber__reel.png"].update(status="reused")
    (out / "REUSE.json").write_text(json.dumps(rec), encoding="utf-8")
    assert any("static net names it" in w for w in br.verify(out, boards["repo"], stamp["head"])[1])
    bad = tmp_path / "bad"
    with pytest.raises(SystemExit) as e:          # the render disagrees: the whole reuse is aborted
        _finish(mods, boards, bad, json.loads(json.dumps(p)), different=("cover amber",))
    assert e.value.code == 3 and (bad / "REUSE-ABORTED.json").is_file() and not (bad / "REUSE.json").exists()
    out2 = tmp_path / "tampered"
    _finish(mods, boards, out2, json.loads(json.dumps(p)))
    (out2 / "surfaces" / "cover__amber__reel.png").write_bytes(b"other pixels")
    assert any("sample refuted" in w for w in br.verify(out2, boards["repo"], stamp["head"])[1])


def test_a_random_sample_is_drawn_from_the_jobs_that_loaded_a_changed_module(mods, boards, tmp_path):
    """The sample goes where a stale reuse could be: a job whose closure the diff does not touch has nothing to
    test; the jobs that loaded a changed module and are reused on their record are sampled at random."""
    br = mods["board_reuse"]
    quiet = _plan(mods, boards, boards["commit"]({"docs/notes.md": "x\n"}), sample=2)
    assert quiet["sample"]["jobs"] == [] and quiet["sample"]["power"]["closure_untouched_by_the_diff"] == 2
    stamp = boards["commit"]({"src/shared.py": FILES["src/shared.py"].replace("    return x\n", "    return x + 1\n")})
    p = _plan(mods, boards, stamp, sample=1)
    assert len(p["sample"]["random"]) == 1 and p["sample"]["static_net"] == []
    assert p["sample"]["closure_touched"] == ["cards", "cover amber"]
    assert p["sample"]["power"]["chance_a_given_stale_job_outside_the_net_is_rendered"] == 0.5
    assert _rendered(_plan(mods, boards, stamp, sample=-1)) == BOTH        # -1: every such job is rendered
    job = p["sample"]["random"][0]
    out = tmp_path / "ok"
    _finish(mods, boards, out, json.loads(json.dumps(p)))
    rec = json.loads((out / "REUSE.json").read_text(encoding="utf-8"))
    assert rec["sample"]["passed"] is True and rec["files"][f"surfaces/{STEMS[job]}.png"]["status"] == "sampled"
    assert br.verify(out, boards["repo"], stamp["head"])[1] == []
    rec["sample"]["power"]["left_on_the_record"] = 0                       # a record that claims more than it did
    (out / "REUSE.json").write_text(json.dumps(rec), encoding="utf-8")
    assert any("detection power" in w for w in br.verify(out, boards["repo"], stamp["head"])[1])
    bad = tmp_path / "bad"
    with pytest.raises(SystemExit):
        _finish(mods, boards, bad, json.loads(json.dumps(p)), different=(job,))


def test_composed_rows_are_drawn_from_the_same_bytes_as_a_full_render(mods, boards, tmp_path):
    """The composed rows (1, 2, 12, 13, every sheet) are never reused: they are drawn in every board from
    <out>/surfaces and surfaces.json. composed_inputs is what they are drawn from; an incremental board's equals a
    full render's except for the keys that say how the board was made, which no row draws."""
    br = mods["board_reuse"]
    stamp = boards["commit"]({"docs/notes.md": "x\n"})
    out = tmp_path / "inc"
    _finish(mods, boards, out, _plan(mods, boards, stamp))
    full = tmp_path / "full"
    shutil.copytree(boards["prev"], full)
    assert br.composed_inputs(out) == br.composed_inputs(full)
    inc = json.loads((out / "surfaces.json").read_text(encoding="utf-8"))
    assert set(inc) - set(json.loads((full / "surfaces.json").read_text(encoding="utf-8"))) <= set(br.NOT_DRAWN)
    (out / "surfaces" / "cover__amber__reel.png").write_bytes(b"x")
    assert br.composed_inputs(out) != br.composed_inputs(full)
    # no row reads how the board was made: the reuse's own records are named only where the board writes them
    for name in ("board_evidence.py", "board_extras.py", "board_motion.py", "board_table.py"):
        src = (BOARD / name).read_text(encoding="utf-8")
        for word in ("reused_seconds", "REUSE.json", "REUSE_NAME", "DEPS.json"):
            assert word not in src, (name, word)
    main = (BOARD / "board.py").read_text(encoding="utf-8")
    assert main.count("reused_seconds") == 0
    assert set(br.NOT_DRAWN) == {"seconds", "reused_seconds", "input_files", "src_sha256", "count"}


def test_the_board_wires_reuse_recording_and_the_per_job_input_hash(mods):
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    for needle in ('"--reuse"', '"--reuse-sample"', '"--no-deps"', "board_reuse.plan(", "board_reuse.finish(",
                   'manifest["reuse_env"]', '"code_key": job_keys[job]', 'stamp["path"] = str(tree)',
                   "board_deps.job_key(at_head, r)"):
        assert needle in src, needle
    rs = (QF35A / "render_surfaces.py").read_text(encoding="utf-8")
    assert "board_deps.job_begin(name)" in rs and "board_deps.job_end(name)" in rs and '".deps.json"' in rs
    tc = mods["board_reuse"].toolchain()
    assert {"python", "pillow", "pillow_feature_versions", "numpy", "ffmpeg", "distributions", "machine",
            "native_env"} <= set(tc)
    assert tc["distributions"]["count"] > 5 and "PYTHONHASHSEED" in tc["native_env"]
    assert mods["board_reuse"].TOOL_DATA == ("tools",)


# ------------------------------------------------------------------------------------------------ changed surfaces
def _board(root: Path, files: dict[str, object]) -> None:
    from PIL import Image
    for rel, colour in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(colour, bytes):
            p.write_bytes(colour)
            continue
        im = Image.new("RGB", (200, 120), (10, 10, 10))
        if colour:
            im.paste(colour, (40, 30, 90, 70))
        im.save(p)


def test_the_changed_surfaces_report_lists_and_pairs_what_changed(mods, tmp_path):
    from PIL import Image
    bd = mods["board_diff"]
    a, b, out = tmp_path / "a", tmp_path / "b", tmp_path / "out"
    _board(a, {"surfaces/cover__x__reel.png": None, "surfaces/map__r__916.png": None, "surfaces/card__gone__916.png": None,
               "row03_reel_cover_grid.png": None, "row03_reel_cover_grid_half.jpg": None})
    _board(b, {"surfaces/cover__x__reel.png": (200, 0, 0), "surfaces/map__r__916.png": None,
               "cases/cover__new__reel.png": None, "row03_reel_cover_grid.png": (0, 200, 0),
               "row03_reel_cover_grid_half.jpg": (0, 200, 0)})
    r = bd.report(a, b, out, ("BEFORE one", "AFTER two"))
    assert r["counts"] == {"changed": 3, "added": 1, "removed": 1, "unchanged": 1, "reused": 0}
    row3 = r["rows"]["row 03"]
    cover = next(e for e in row3["changed"] if e["path"] == "surfaces/cover__x__reel.png")
    assert cover["box"] == [40, 30, 90, 70] and cover["kind"] == "surface"
    pair = Image.open(out / cover["pair"])
    assert pair.width == 200 * 2 + bd.GAP and pair.height == bd.BAR + 120                # side by side, 1:1
    sheet = next(e for e in row3["changed"] if e["path"] == "row03_reel_cover_grid.png")
    assert sheet["kind"] == "sheet" and sheet["pair"] is None                           # listed, paired with --sheets
    assert next(e for e in row3["changed"] if e["path"].endswith("_half.jpg"))["kind"] == "half-scale twin"
    assert [e["path"] for e in row3["added"]] == ["cases/cover__new__reel.png"]
    assert [e["path"] for e in r["rows"]["row 08"]["removed"]] == ["surfaces/card__gone__916.png"]
    text = (out / "index.md").read_text(encoding="utf-8")
    assert "## row 03" in text and "changed box [40, 30, 90, 70]" in text and "\r" not in text
    wide = bd.image_pair(Image.new("RGB", (1920, 1080)), Image.new("RGB", (1920, 1080), (9, 9, 9)), ("B", "A"), "s",
                         2400, {"box": [0, 0, 1920, 1080]})
    assert wide.width == 1920 and wide.height == 2 * (bd.BAR + 1080) + bd.GAP           # too wide for a row: stacked 1:1


def test_rows_are_the_boards_own(mods):
    bd = mods["board_diff"]
    clips = {"hook__amber__916.mp4": {"row": "12"}, "endcard_916__1_x.mp4": {"row": "9, 12"}}
    assert bd.row_of("surfaces/social__r_1__story_story_01.png", clips) == "row 06"
    assert bd.row_of("surfaces/map__r__916.png", clips) == "row 10"
    assert bd.row_of("cases/card__hook__amber__916__f00.png", clips) == "row 08"
    assert bd.row_of("clips/hook__amber__916.mp4", clips) == "row 12"
    assert bd.row_of("clips/endcard_916__1_x.mp4", clips) == "row 09"
    assert bd.row_of("row12_motion_hook__amber.png", clips) == "row 12"
    assert bd.row_of("peer_feed_ig_grid.png", clips) == "peer feeds"


def test_a_changed_clip_gets_a_strip_pair(mods, tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg not available")
    bd = mods["board_diff"]
    a, b = tmp_path / "a" / "clips", tmp_path / "b" / "clips"
    for d, colour in ((a, "gray"), (b, "white")):
        d.mkdir(parents=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=64x96:r=30:d=0.5",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "hook__amber__916.mp4")], check=True)
    r = bd.report(a.parent, b.parent, tmp_path / "out", ("B", "A"))
    e = r["rows"]["clips"]["changed"][0]
    assert e["kind"] == "clip" and e["frames_before"] == e["frames_after"] == 15 and e["frames_differing"] == 15
    assert (tmp_path / "out" / e["pair"]).is_file()


# ------------------------------------------------------------------------------------------------ evidence gaps
def test_every_row_case_has_its_own_full_size_file(mods, tmp_path):
    from PIL import Image
    b = mods["board"]
    (tmp_path / "surfaces").mkdir()
    Image.new("RGB", (8, 8)).save(tmp_path / "surfaces" / "cover__a__reel.png")
    manifest: dict = {}
    b.save_case(tmp_path, manifest, "cover__a__reel", Image.new("RGB", (8, 8), (255, 0, 0)))      # render_surfaces' own
    b.save_case(tmp_path, manifest, "card__hook__amber__916__f00", Image.new("RGB", (108, 192), (255, 0, 0)))
    b.case_files(tmp_path, manifest, ["cover__a__reel", "card__hook__amber__916__f00", "card__myth__x__916"])
    assert manifest["case_files"] == {"cover__a__reel": "surfaces/cover__a__reel.png",
                                      "card__hook__amber__916__f00": "cases/card__hook__amber__916__f00.png",
                                      "card__myth__x__916": None}
    assert not (tmp_path / "cases" / "cover__a__reel.png").exists()
    assert Image.open(tmp_path / "cases" / "card__hook__amber__916__f00.png").size == (108, 192)   # never scaled
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    assert 'save_case(out, manifest, item["name"], item["image"])' in src
    assert 'case_files(out, manifest, manifest["rows"][str(r)])' in src


def test_the_hook_clip_is_drawn_on_every_ground(mods):
    bm_src = (BOARD / "board_motion.py").read_text(encoding="utf-8")
    fn = bm_src[bm_src.index("def hook_sequence"):bm_src.index("# ----", bm_src.index("def hook_sequence"))]
    assert "break" not in fn and 'res.setdefault("all", {})[g] = one' in fn
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    assert '"row12_motion_hook" + ("" if n_hook == 0 else f"__{ground}")' in src
    assert 'f"hook__{g}__916.mp4"' in bm_src


def test_board_drawn_social_frames_carry_masthead_records(mods):
    bt = mods["board_table"]
    assert bt.social_part("social__antikythera_mechanism_near_white__carousel_slide_02") == (
        "antikythera_mechanism_near_white", "carousel", 2)
    assert bt.social_part("social__unfinished_obelisk_aswan_1792993374__carousel_carousel_slide_01") == (
        "unfinished_obelisk_aswan_1792993374", "carousel", 1)
    recs = {"social__unfinished_obelisk_aswan_1792993374__carousel_carousel_slide_01":
            {"shown": True, "size": 53.3, "variant": "dark", "contrast": 5.51},
            "social__unfinished_obelisk_aswan_1792993374__carousel_carousel_slide_02":
            {"shown": False, "reason": "no legible variant"}}
    rows = bt.social_masthead_rows({"masthead_records": recs})
    assert any("unfinished_obelisk_aswan_1792993374" in str(r[7]) for r in rows[1:])
    assert rows[0][-3:] == ("reach", "margin step", "margin short") and all(len(r) == len(rows[0]) for r in rows)
    assert any("no legible variant" in str(r[5]) for r in rows[1:])
    src = (BOARD / "board_extras.py").read_text(encoding="utf-8")
    assert "MultiFormatGenerator._render_carousel_slide = real" in src       # the spy is always taken off again
    assert 'manifest.setdefault("masthead_records", {})[item["name"]]' in (BOARD / "board.py").read_text(encoding="utf-8")


def test_the_other_streams_records_reach_the_tile_and_the_table(mods):
    """SYS-plates-c5-1 / SYS-photo-c5-1: a withheld masthead, the Story pointer's decision and a replaced photograph
    are said on the tile; the deck is drawn through the picker only where the tree has one."""
    be, bt = mods["board_evidence"], mods["board_table"]
    assert be.masthead_note({"shown": True}) == "" and be.masthead_note(None) == ""
    assert be.masthead_note({"shown": False, "reason": "rule", "fb_interior": True}) == ""      # by rule: no omission
    assert "RECORDED OMISSION: drawn without the masthead (5.41 under 5.5)" in be.masthead_note(
        {"shown": False, "reason": "5.41 under 5.5"})
    rec = {"masthead_records": {"s": {"shown": False, "reason": "low"}},
           "pointer_records": {"s": {"shown": False, "reason": "no tap target"}},
           "photo_choice": {"s": {"photo": "a/new.jpg", "replaced": "a/old.jpg", "reason": "unfit"},
                            "t": {"photo": "a/x.jpg", "reason": "matched"}}}
    note = be.record_note("s", rec)
    assert "drawn without the masthead" in note and "Story pointer: withheld (no tap target)" in note
    assert "new.jpg for old.jpg (unfit)" in note and be.record_note("t", rec) == ""
    rows = bt.social_masthead_rows({"masthead_records": {
        "social__r_1__carousel_carousel_slide_01": {"shown": True, "size": 40.0, "contrast": 6.0, "reach": 412,
                                                    "margin_step": 2, "margin_short": 0}}})
    assert rows[1][-3:] == ("412", "2", "0")
    rs = (QF35A / "render_surfaces.py").read_text(encoding="utf-8")
    assert 'hasattr(mf, "deck_photo_guard")' in rs and 'run / "carousel_photos.json"' in rs
    assert 'result.setdefault("photo_choice", {})[key]' in rs and 'result.setdefault("pointer_records", {})[key]' in rs
    assert 'r.get("archetype") != "diagram":   # a drawing slide keeps its poster' in rs
    bx = (BOARD / "board_extras.py").read_text(encoding="utf-8")
    assert '(rec.get(p.name) or {}).get("masthead")' in bx and 'payload.get("layout_holds")' in bx
    # TEXT's request: a chapter card is drawn with its video's chapter set, as card_composition.make_all passes it
    assert "dataclasses.replace(spec, set_titles=tuple(fc.RICHAT_ACTS))" in rs
    assert '"chapter_act1": lambda ph, size: chapter(size, 1, ph)' in rs
    sh = (BOARD / "run_palette.sh").read_text(encoding="utf-8")
    assert 'prev=(--timings "$BOARD_PREV/$palette/surfaces.json")' in sh
    # round 3: the reuse base only with the unsafe variable, and then always with the flag (test_freeze_board_c5_fence)
    assert 'prev+=(--reuse "$BOARD_PREV/$palette" --unsafe-reuse-for-iteration)' in sh
    assert '"${BOARD_UNSAFE_REUSE_FOR_ITERATION:-}" = 1' in sh


TIERS = {
    "cover__amber_room_heist__reel": "T1", "cover__amber_room_heist__grid": "T4", "cover__amber_room_heist__youtube": "T3",
    "cover_badge__unfinished_obelisk_aswan__reel": "T2", "thumbnail_badge__unfinished_obelisk_aswan__youtube": "T3",
    "captions__amber_room_heist__frame": "T1", "captions__amber_room_heist__badge__encoded": "T2",
    "captions__richat_structure_sahara_eye__frame": "T3", "captions_run__richat_structure_sahara_eye__169": "T3",
    "captions_run__amber_room_heist__916": "T1",
    "social__r_1__story_story_01_hook": "T1", "social__r_1__fb_carousel_slide_02": "T1",
    "bridge_follow__1_wieliczka_salt_mine": "T1", "endscreen__0_richat_structure_sahara_eye": "T4",
    "endcard__1_wieliczka_salt_mine": "T4", "map__amber_1__916": "T1", "map__amber_1__169": "T3",
    "numbers__amber_1__916": "T1", "timeline__amber_1__916": "T1", "timeline_dense__test_events_x__916": "T4",
    "scale__w_1__916": "T2", "scale__w_1__169": "T3", "route__a_1_new_stops__916": "T2",
    "cross_section__test_spec__916": "T2", "thennow__memnon__916": "T2", "thennow__memnon__169": "T3",
    "callouts__reference_pair__916": "T3", "card__hook__amber__916__f00": "T2", "card__myth_richat__amber__916": "T2",
    "card__myth_richat__amber__169": "T3", "card__chapter_act1__amber__916": "T4", "card__chapter_act1__amber__169": "T3",
    "row13_elements": "T4",
}


def test_every_case_has_a_production_tier(mods):
    be, b = mods["board_evidence"], mods["board"]
    for name, want in TIERS.items():
        assert be.tier(name, "", ("richat_structure_sahara_eye",)) == want, name
    assert be.tier("social__r_1__carousel_carousel_slide_04", "x | diagram slide (recipe)") == "T2"
    assert set(be.TIER_WHAT) == {"T1", "T2", "T3", "T4"}
    assert b.clip_tier_name("endscreen_169__0_richat") == "endscreen__0_richat__169"
    assert be.tier(b.clip_tier_name("bridge_follow_916__1_wieliczka_salt_mine")) == "T1"
    assert be.tier(b.clip_tier_name("endcard_916__1_wieliczka_salt_mine")) == "T4"
    assert be.tier(b.clip_tier_name("hook__amber__916")) == "T2"
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    assert 'manifest.setdefault("case_tiers", {})' in src and 'manifest["tiers"]' in src


def test_each_caption_fixture_has_its_whole_run_in_the_expected_cases(mods):
    ex = mods["exp_cases"]
    base = [{"surface": "surfaces", "case": "map__x", "format": "916"}]
    out, _ = ex.build(base, "default", ROOT)
    keys = {(r["surface"], r["case"], r["format"]) for r in out}
    cat = json.loads((ROOT / ex.FIXTURES).read_text(encoding="utf-8"))
    cases = cat["designs"]["captions"]["cases"]
    assert cases
    for c in cases:
        tag = "169" if c.get("format") == "16:9" else "916"
        assert ("clips", f"captions_run__{c['topic']}", tag) in keys
        assert ("row07_captions_run", c["topic"], tag) in keys
        assert ("row07_captions_run", f"{c['topic']}__{tag}_bursts", "default") in keys
    assert ("clips", "hook__amber", "916") in keys
    assert ("row12_motion_hook", "row12_motion_hook__amber_curves", "default") in keys
    assert all("refused_as" not in r for r in out)
    # the names the board writes
    bx_src = (BOARD / "board_extras.py").read_text(encoding="utf-8")
    assert 'name = f"captions_run__{topic}__{tag}"' in bx_src and 'clips / f"{name}.mp4",' in bx_src
    b_src = (BOARD / "board.py").read_text(encoding="utf-8")
    assert "sname = f\"row07_{res['name']}\"" in b_src and 'f"{sname}_bursts.png"' in b_src
    # a palette that refuses the Amber hook is not asked for its clip; a tree before cycle 5 for none of it
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ref = Path(d) / "refusals.json"
        ref.write_text(json.dumps([{"surface": "card__hook__amber__916 (row 12)", "what": "refused (recorded)",
                                    "reason": "fit"}]), encoding="utf-8")
        out2, dropped = ex.build(base, "second", ROOT, ref)
    assert ("clips", "hook__amber", "916") not in {(r["surface"], r["case"], r["format"]) for r in out2}
    assert "clips/hook__amber__916" in dropped
    assert ex.build(base, "default", ROOT, None, cycle4=False)[0] == base
    assert len(ex.build(base, "default", ROOT, None, cycle5=False)[0]) < len(out)


def test_a_caption_run_tile_is_the_band_at_one_to_one(mods):
    src = (BOARD / "board_extras.py").read_text(encoding="utf-8")
    fn = src[src.index("def caption_run("):src.index("# ----", src.index("def caption_run("))]
    assert "bands[k] = im.crop(box)" in fn and "resize" not in fn          # never scaled
    assert 'b["hide"] - CAPTION_RUN_LEAD' in fn                             # the frame at each burst's END
    assert "encode_caption_clip(" in fn                                    # the production chain and encoder
    assert "sheet(tiles, out / sname, tile_h=None" in (BOARD / "board.py").read_text(encoding="utf-8")


def test_the_tooling_files_are_lf(mods):
    for p in list(BOARD.glob("board*.py")) + [BOARD / "exp_cases.py", BOARD / "run_surfaces.py", BOARD / "run_palette.sh",
                                              QF35A / "render_surfaces.py", Path(__file__)]:
        assert b"\r\n" not in p.read_bytes(), p.name
