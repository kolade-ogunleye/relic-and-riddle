"""Design-freeze board tooling, cycle 5 (docs/reviews/freeze-system-2026-11-15): the review loop's own invariants,
never pinned pictures.

- the incremental board (board_deps.py, board_reuse.py): a job's code key changes exactly when a function that ran
  for it, a value or class header it names, or any import-time residue changes; the recorder follows a memoised
  function to what computed its value, counts code run outside every job for every job, and sees threads; the plan
  reuses a job only when its key and every other input are the same, refuses the whole reuse on anything else, the
  sample aborts on a difference, and the gate's check proves each reused file again from git objects;
- every record key render_surfaces.py writes is one the reuse knows how to split by job;
- the changed-surfaces report (board_diff.py): changed / added / removed / unchanged, the changed box, one pair per
  changed image, a strip pair per changed clip;
- the evidence gaps of panel 6: every row case has its own full-size file, the hook clip is drawn on every ground,
  each caption fixture has its whole run, the board-drawn social frames have masthead records; the expected cases
  follow the names the board writes.
"""
from __future__ import annotations

import ast
import functools
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
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


# ------------------------------------------------------------------------------------------------ code keys
BASE = '''"""doc"""
import os
from functools import lru_cache

SIZE = 40          # a token f names
OTHER = 7          # a token only g names
TABLE = {"a": build}


def build():
    return SIZE * 2


def f(x):
    # a comment
    return helper(x) + SIZE


@lru_cache(maxsize=8)
def helper(x):
    return x + 1


def g(x):
    return x * OTHER


class Card:
    ratio = 2

    def draw(self):
        return self.ratio * SIZE

    def unused(self):
        return 0
'''


def _universe(mods, text: str):
    bd = mods["board_deps"]
    files = {"src/a.py": text}
    return bd.Universe(files.get, list(files))


def _key(mods, text: str, fns):
    return _universe(mods, text).key(fns)["sha256"]


def test_a_code_key_changes_only_with_what_the_job_ran_or_names(mods):
    f = ["src/a.py::f", "src/a.py::helper"]
    base = _key(mods, BASE, f)
    assert _key(mods, BASE.replace("# a comment", "# another comment"), f) == base       # a comment is not code
    assert _key(mods, BASE.replace("return x * OTHER", "return x * OTHER + 1"), f) == base   # g did not run
    assert _key(mods, BASE.replace("OTHER = 7", "OTHER = 8"), f) == base                 # a token only g names
    assert _key(mods, BASE.replace("return 0", "return 1"), f) == base                   # a method that did not run
    assert _key(mods, BASE.replace("helper(x) + SIZE", "helper(x) + SIZE + 1"), f) != base   # f itself
    assert _key(mods, BASE.replace("return x + 1", "return x + 2"), f) != base           # what f's memo computed
    assert _key(mods, BASE.replace("SIZE = 40", "SIZE = 44"), f) != base                 # a token f names
    assert _key(mods, BASE + "\nprint('import time')\n", f) != base                      # residue: every key
    assert _key(mods, BASE + "\nif os.name:\n    SIZE = 1\n", f) != base                 # a second binding of a name


def test_a_method_brings_its_class_header_and_a_table_the_functions_it_names(mods):
    draw = ["src/a.py::Card.draw"]
    base = _key(mods, BASE, draw)
    assert _key(mods, BASE.replace("ratio = 2", "ratio = 3"), draw) != base              # the class header
    assert _key(mods, BASE.replace("return 0", "return 1"), draw) == base
    table = BASE.replace("return helper(x) + SIZE", "return helper(x) + SIZE + len(TABLE)")
    f = ["src/a.py::f", "src/a.py::helper"]
    k = _universe(mods, table).key(f)
    assert ("src/a.py", "build") in {(r[0], r[1]) for r in k["rows"]}                    # named by a value f names
    assert _key(mods, table.replace("return SIZE * 2", "return SIZE * 3"), f) != k["sha256"]


def test_a_recorded_function_that_is_gone_changes_the_key(mods):
    f = ["src/a.py::f", "src/a.py::helper"]
    gone = BASE.replace("@lru_cache(maxsize=8)\ndef helper(x):\n    return x + 1\n", "helper = len\n")
    k = _universe(mods, gone).key(f)
    assert k["absent"] == ["src/a.py::helper"] and k["sha256"] != _key(mods, BASE, f)


def test_render_surfaces_main_is_split_into_its_job_closures(mods):
    bd = mods["board_deps"]
    text = (QF35A / "render_surfaces.py").read_text(encoding="utf-8")
    names = {s.name for s in bd.parse_module(bd.RENDER, text) if s.kind == "code"}
    for n in ("main.<body>", "main.job", "main.graphic", "main.endcard", "main.cover", "main.social", "main.cards",
              "main.captions", "main.callout", "main.thennow"):
        assert n in names, n
    assert "main" not in names


# ------------------------------------------------------------------------------------------------ the recorder
TREE_MOD = '''from functools import lru_cache

CALLS = []


def outside():
    return 1


def entry(x):
    return memo(x)


@lru_cache(maxsize=8)
def memo(x):
    return deep(x)


def deep(x):
    return x + 1


def dispatch(fn):
    return fn()


def other():
    return inner()


def inner():
    return 2


def threaded():
    return 3
'''


def test_the_recorder_follows_a_memo_counts_outside_code_and_sees_threads(mods, tmp_path):
    bd = mods["board_deps"]
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "recmod.py").write_text(TREE_MOD, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location("recmod_c5", tmp_path / "src" / "recmod.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rec = bd.Recorder(tmp_path)
    rec.start()
    try:
        mod.outside()                      # outside every job's window
        rec.begin("A")
        mod.entry(1)                       # memo miss: deep runs
        rec.end("A")
        rec.begin("B")
        mod.entry(1)                       # memo hit: deep does not run in B's window
        t = threading.Thread(target=mod.threaded)
        t.start()
        t.join()
        rec.end("B")
        rec.begin("C")
        mod.other()
        rec.end("C")
        # a dispatcher that called an earlier job's code does not hand it to a later job
        rec.dispatch.add("src/recmod.py::dispatch")
        rec.begin("D")
        mod.dispatch(mod.other)
        rec.end("D")
        rec.begin("E")
        mod.dispatch(mod.threaded)
        rec.end("E")
        rec.dispatch.clear()
        rec.begin("F")
        mod.dispatch(mod.threaded)
        rec.end("F")
    finally:
        rec.prof.disable()
        threading.setprofile(None)
    got = {j: {f.split("::")[1] for f in fns} for j, fns in rec.record()["jobs"].items()}
    assert {"dispatch", "other", "inner"} <= got["D"]
    assert {"dispatch", "threaded"} <= got["E"] and "other" not in got["E"] and "inner" not in got["E"]
    assert {"other", "inner"} <= got["F"]          # without the rule a later job inherits the earlier one's code
    assert {"entry", "memo", "deep", "outside"} <= got["A"]
    assert {"entry", "deep", "outside", "threaded"} <= got["B"]     # deep through A's recorded call edge
    assert got["C"] >= {"other", "inner", "outside"} and "deep" not in got["C"] and "entry" not in got["C"]
    assert all(f.startswith("src/recmod.py::") for fns in rec.record()["jobs"].values() for f in fns)


def test_recording_is_off_unless_asked_and_merges_by_job(mods):
    bd = mods["board_deps"]
    src = (BOARD / "run_surfaces.py").read_text(encoding="utf-8")
    assert 'os.environ.get("BOARD_DEPS") == "1"' in src
    merged = bd.merge([{"jobs": {"a": ["x"]}}, {"jobs": {"b": ["y"]}}])
    assert merged["jobs"] == {"a": ["x"], "b": ["y"]}
    with pytest.raises(RuntimeError):
        bd.merge([{"jobs": {"a": ["x"]}}, {"jobs": {"a": ["y"]}}])


# ------------------------------------------------------------------------------------------------ names and records
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
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")).stdout.strip()


SRC_A = "SIZE = 4\n\n\ndef card():\n    return SIZE\n\n\ndef cover():\n    return 1\n"


@pytest.fixture()
def boards(mods, tmp_path):
    """A two-commit repository and a previous board of the first commit: job `cards` ran card(), job
    `cover amber` ran cover()."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    br = mods["board_reuse"]
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "fonts").mkdir()
    (repo / "src" / "a.py").write_text(SRC_A, encoding="utf-8", newline="\n")
    (repo / "tests" / "data.json").write_text("{}", encoding="utf-8", newline="\n")
    (repo / "tests" / "unread.json").write_text("{}", encoding="utf-8", newline="\n")
    (repo / "fonts" / "face.txt").write_text("face", encoding="utf-8", newline="\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    head = _git(repo, "rev-parse", "HEAD")
    data = tmp_path / "dev" / "run.json"
    data.parent.mkdir()
    data.write_text("{}", encoding="utf-8")
    env = {"toolchain": {"python": "3"}, "tool_data": {}, "sha256": "env"}
    prev = tmp_path / "prev"
    (prev / "surfaces").mkdir(parents=True)
    surfaces = {"brand": "default", "src_sha256": "x", "seconds": {"cards": 5.0, "cover amber": 2.0}, "errors": {},
                "surfaces": {"card__hook__amber__916": {"sha256": "c", "frames": 3},
                             "cover__amber__reel": {"sha256": "r", "frames": 1}},
                "input_files": {str(data): br.sha256_file(data)}}
    for n in surfaces["surfaces"]:
        (prev / "surfaces" / f"{n}.png").write_bytes(b"png:" + n.encode())
    read = str(repo / "tests" / "data.json")
    (prev / "surfaces.json").write_text(json.dumps(surfaces), encoding="utf-8")
    (prev / "board.json").write_text(json.dumps({
        "brand": "default", "tree": {"head": head, "dirty_vs_head": False, "path": str(repo)}, "reuse_env": env,
        "inputs": {"files": {str(data): br.sha256_file(data), read: br.sha256_file(Path(read))}}}), encoding="utf-8")
    (prev / "DEPS.json").write_text(json.dumps({"schema": "board-deps/1", "jobs": {
        "cards": ["src/a.py::card"], "cover amber": ["src/a.py::cover"]}}), encoding="utf-8")
    (prev / "MANIFEST.json").write_text(json.dumps({
        "repo": {"head": head, "dirty_vs_head": False},
        "images": [{"path": f"surfaces/{n}.png", "sha256": br.sha256_file(prev / "surfaces" / f"{n}.png")}
                   for n in surfaces["surfaces"]]}), encoding="utf-8")

    def commit(path: str, text: str) -> dict:
        (repo / path).write_text(text, encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two")
        return {"head": _git(repo, "rev-parse", "HEAD"), "dirty_vs_head": False, "path": str(repo)}
    return {"repo": repo, "prev": prev, "env": env, "commit": commit, "head": head, "data": data,
            "offered": ["cards", "cover amber"], "cache": str(tmp_path / "frames")}


def _plan(mods, b, stamp, **kw):
    args = dict(sample=0, seed=1)
    args.update(kw)
    return mods["board_reuse"].plan(b["repo"], b["prev"], kw.pop("palette", "default"), stamp, b["env"],
                                    kw.pop("offered", b["offered"]), b["cache"], args["sample"], args["seed"])


def test_only_the_job_whose_code_changed_renders(mods, boards):
    stamp = boards["commit"]("src/a.py", SRC_A.replace("return 1", "return 2"))      # cover() changed
    p = _plan(mods, boards, stamp)
    assert p["jobs"]["cards"]["status"] == "reused" and p["jobs"]["cover amber"]["status"] == "rendered"
    assert p["jobs"]["cover amber"]["changed"] == ["src/a.py::cover"]
    assert mods["board_reuse"].to_render(p) == ["cover amber"]


def test_a_token_reaches_every_job_that_names_it_and_a_comment_none(mods, boards):
    p = _plan(mods, boards, boards["commit"]("src/a.py", SRC_A.replace("SIZE = 4", "SIZE = 5")))
    assert [p["jobs"][j]["status"] for j in ("cards", "cover amber")] == ["rendered", "reused"]
    p = _plan(mods, boards, boards["commit"]("src/a.py", SRC_A.replace("SIZE = 5", "SIZE = 4") + "# note\n"))
    assert {r["status"] for r in p["jobs"].values()} == {"reused"}


def test_anything_that_is_not_python_refuses_the_whole_reuse(mods, boards):
    br = mods["board_reuse"]
    stamp = boards["commit"]("tests/unread.json", '{"a": 1}')            # tests data no render read: no trigger
    assert {r["status"] for r in _plan(mods, boards, stamp)["jobs"].values()} == {"reused"}
    with pytest.raises(br.NoReuse, match="palette"):
        br.plan(boards["repo"], boards["prev"], "second", stamp, boards["env"], boards["offered"], boards["cache"])
    with pytest.raises(br.NoReuse, match="job list"):
        br.plan(boards["repo"], boards["prev"], "default", stamp, boards["env"], ["cards"], boards["cache"])
    with pytest.raises(br.NoReuse, match="toolchain or tool data"):
        br.plan(boards["repo"], boards["prev"], "default", stamp, {**boards["env"], "sha256": "other"},
                boards["offered"], boards["cache"])
    with pytest.raises(br.NoReuse, match="differs from its HEAD"):
        br.plan(boards["repo"], boards["prev"], "default", {**stamp, "dirty_vs_head": True}, boards["env"],
                boards["offered"], boards["cache"])
    boards["data"].write_text('{"changed": 1}', encoding="utf-8")
    with pytest.raises(br.NoReuse, match="data inputs changed"):
        _plan(mods, boards, stamp)
    boards["data"].write_text("{}", encoding="utf-8")
    with pytest.raises(br.NoReuse, match="non-Python inputs"):
        _plan(mods, boards, boards["commit"]("tests/data.json", '{"read": 1}'))       # tests data a render read
    with pytest.raises(br.NoReuse, match="non-Python inputs"):
        _plan(mods, boards, boards["commit"]("fonts/face.txt", "another face"))


def _fresh(out: Path, names: dict[str, bytes], jobs: list[str]) -> dict:
    (out / "surfaces").mkdir(parents=True, exist_ok=True)
    for n, data in names.items():
        (out / "surfaces" / f"{n}.png").write_bytes(data)
    return {"brand": "default", "seconds": {j: 1.0 for j in jobs}, "errors": {},
            "surfaces": {n: {"sha256": "new", "frames": 1} for n in names}, "input_files": {}}


def test_finish_merges_copies_and_the_gate_check_proves_it(mods, boards, tmp_path):
    br = mods["board_reuse"]
    stamp = boards["commit"]("src/a.py", SRC_A.replace("return 1", "return 2"))
    p = _plan(mods, boards, stamp)
    out = tmp_path / "out"
    fresh = _fresh(out, {"cover__amber__reel": b"png:new"}, ["cover amber"])
    merged = br.finish(p, out, fresh, {"jobs": {"cover amber": ["src/a.py::cover"]}})
    assert set(merged["surfaces"]) == {"card__hook__amber__916", "cover__amber__reel"} and merged["count"] == 2
    assert merged["seconds"] == {"cards": 5.0, "cover amber": 1.0} and merged["reused_seconds"] == {"cards": 5.0}
    src, dst = boards["prev"] / "surfaces" / "card__hook__amber__916.png", out / "surfaces" / "card__hook__amber__916.png"
    assert dst.read_bytes() == src.read_bytes() and int(dst.stat().st_mtime) == int(src.stat().st_mtime)
    rec = json.loads((out / "REUSE.json").read_text(encoding="utf-8"))
    assert rec["files"]["surfaces/card__hook__amber__916.png"]["status"] == "reused"
    assert rec["files"]["surfaces/cover__amber__reel.png"]["status"] == "rendered"
    assert rec["counts"]["reused"] == 1 and rec["counts"]["rendered"] == 1
    assert json.loads((out / "DEPS.json").read_text(encoding="utf-8"))["jobs"].keys() == {"cards", "cover amber"}
    (out / "board.json").write_text(json.dumps({"brand": "default", "reuse_env": boards["env"]}), encoding="utf-8")
    proven, why = br.verify(out, boards["repo"], stamp["head"])
    assert why == [] and proven == {"surfaces/card__hook__amber__916.png"}
    # a reused file that is not the previous board's bytes, and a reuse claimed for the job that changed
    dst.write_bytes(b"edited")
    assert any("not the byte copy" in w for w in br.verify(out, boards["repo"], stamp["head"])[1])
    dst.write_bytes(src.read_bytes())
    rec["files"]["surfaces/cover__amber__reel.png"] = {"status": "reused", "job": "cover amber"}
    rec["jobs"]["cover amber"]["status"] = "reused"
    (out / "REUSE.json").write_text(json.dumps(rec), encoding="utf-8")
    proven, why = br.verify(out, boards["repo"], stamp["head"])
    assert "surfaces/cover__amber__reel.png" not in proven and any("code differs" in w for w in why)
    assert br.verify(out, boards["repo"], boards["head"])[1]       # the record is for another commit


def test_a_sample_that_differs_aborts_the_reuse(mods, boards, tmp_path):
    br = mods["board_reuse"]
    stamp = boards["commit"]("src/a.py", SRC_A + "# note\n")
    p = _plan(mods, boards, stamp, sample=1)
    job = p["sample"]["jobs"][0]
    stem = {"cards": "card__hook__amber__916", "cover amber": "cover__amber__reel"}[job]
    same = (boards["prev"] / "surfaces" / f"{stem}.png").read_bytes()
    prev_rec = json.loads((boards["prev"] / "surfaces.json").read_text(encoding="utf-8"))["surfaces"][stem]
    out = tmp_path / "ok"
    fresh = _fresh(out, {stem: same}, [job])
    fresh["surfaces"][stem] = prev_rec
    br.finish(json.loads(json.dumps(p)), out, fresh, {"jobs": {job: ["src/a.py::card"]}})
    assert json.loads((out / "REUSE.json").read_text(encoding="utf-8"))["sample"]["passed"] is True
    bad = tmp_path / "bad"
    fresh = _fresh(bad, {stem: b"other pixels"}, [job])
    fresh["surfaces"][stem] = prev_rec
    with pytest.raises(SystemExit) as e:
        br.finish(json.loads(json.dumps(p)), bad, fresh, {"jobs": {}})
    assert e.value.code == 3 and (bad / "REUSE-ABORTED.json").is_file() and not (bad / "REUSE.json").exists()


def test_a_board_without_the_code_record_cannot_be_reused(mods, boards):
    br = mods["board_reuse"]
    (boards["prev"] / "DEPS.json").unlink()
    with pytest.raises(br.NoReuse, match="DEPS.json"):
        _plan(mods, boards, boards["commit"]("src/a.py", SRC_A + "# note\n"))


def test_the_board_wires_reuse_recording_and_the_per_job_input_hash(mods):
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    for needle in ('"--reuse"', '"--reuse-sample"', '"--no-deps"', "board_reuse.plan(", "board_reuse.finish(",
                   'manifest["reuse_env"]', '"code_key": job_keys[job]', 'stamp["path"] = str(tree)'):
        assert needle in src, needle
    rs = (QF35A / "render_surfaces.py").read_text(encoding="utf-8")
    assert "board_deps.job_begin(name)" in rs and "board_deps.job_end(name)" in rs and '".deps.json"' in rs
    env_keys = set(mods["board_reuse"].toolchain())
    assert {"python", "pillow", "pillow_feature_versions", "numpy", "ffmpeg"} <= env_keys


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


TIERS = {
    "cover__amber_room_heist__reel": "T1", "cover__amber_room_heist__grid": "T4", "cover__amber_room_heist__youtube": "T3",
    "cover_badge__unfinished_obelisk_aswan__reel": "T2", "thumbnail_badge__unfinished_obelisk_aswan__youtube": "T3",
    "captions__amber_room_heist__frame": "T1", "captions__amber_room_heist__badge__encoded": "T2",
    "captions__richat_structure_sahara_eye__frame": "T3", "captions_run__richat_structure_sahara_eye__169": "T3",
    "captions_run__amber_room_heist__916": "T1",
    "social__r_1__story_story_01_hook": "T1", "social__r_1__fb_carousel_slide_02": "T1",
    "bridge_follow__1_wieliczka_salt_mine": "T1", "endscreen__0_richat_structure_sahara_eye": "T3",
    "endcard__1_wieliczka_salt_mine": "T4", "map__amber_1__916": "T1", "map__amber_1__169": "T3",
    "numbers__amber_1__916": "T1", "timeline__amber_1__916": "T1", "timeline_dense__test_events_x__916": "T4",
    "scale__w_1__916": "T2", "scale__w_1__169": "T3", "route__a_1_new_stops__916": "T2",
    "cross_section__test_spec__916": "T2", "thennow__memnon__916": "T2", "thennow__memnon__169": "T3",
    "callouts__reference_pair__916": "T2", "card__hook__amber__916__f00": "T2", "card__myth_richat__amber__916": "T2",
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
