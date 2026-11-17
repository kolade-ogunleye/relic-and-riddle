"""QF-135: CI runs the full suite as parallel shards with the same coverage. Every shard collects the whole suite and
runs the units a deterministic, duration-balanced plan assigns it (whole files, and the test ids of files too heavy
to keep whole; scripts/ci_shards.py, committed timings in .github/ci_shard_timings.json); the `test` job fails when any shard fails, when the shards' results are not exactly
the full collection (a test missing, run twice or unknown), when the shards collected different suites, or when a
file or shard skipped more tests than its committed baseline."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
SCRIPT = ROOT / "scripts" / "ci_shards.py"
TIMINGS = ROOT / ".github" / "ci_shard_timings.json"


def _cs():
    # Import at the assertion so the base commit (no script) fails each test by assertion.
    assert SCRIPT.is_file(), "scripts/ci_shards.py is missing"
    from scripts import ci_shards
    return ci_shards


def _job(name):
    """The text of one job of the workflow (no YAML library is in the lock: the jobs are read as text)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert m, f"no job {name!r} in the workflow"
    return m.group(1)


def _steps(job_text):
    """Each step's text, split on its "      - " line."""
    body = job_text.split("    steps:\n", 1)[1]
    return [s for s in re.split(r"^(?=      - )", body, flags=re.MULTILINE) if s.strip()]


def _if(step):
    m = re.search(r"^        if: (.+)$", step, re.MULTILINE) or re.search(r"^      - if: (.+)$", step, re.MULTILINE)
    return m.group(1).strip() if m else None


def test_the_suite_runs_as_a_matrix_of_shards_and_one_aggregate_test_check():
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"^env:\n(?:  .*\n)*?  SHARDS: (\d+)$", text, re.MULTILINE)
    assert m, "the workflow sets no SHARDS count"
    shards = int(m.group(1))
    assert shards >= 2
    shard = _job("shard")
    assert re.search(r"^        shard: \[(.*)\]$", shard, re.MULTILINE).group(1) == ", ".join(
        str(i) for i in range(1, shards + 1))
    assert re.search(r"^      fail-fast: false$", shard, re.MULTILINE)   # one failure does not hide another's
    test = _job("test")                             # the check name the lander and the operator read
    assert re.search(r"^    needs: shard$", test, re.MULTILINE) and re.search(r"^    if: always\(\)$", test, re.MULTILINE)
    assert 'test "${{ needs.shard.result }}" = "success"' in test
    verify = [s for s in _steps(test) if "python scripts/ci_shards.py verify" in s]
    assert verify and '--shards "$SHARDS"' in verify[0] and _if(verify[0]) == "always()"



def test_every_shard_has_the_full_environment_and_runs_only_its_planned_units():
    steps = _steps(_job("shard"))
    text = "\n".join(steps)
    for needed in ("libfribidi0", "sha256sum --check --strict", "python scripts/check_ffmpeg.py", "pip install uv==",
                   "uv lock --check", "pip install --require-hashes -r", "python -m pytest --collect-only -q",
                   "python scripts/ci_shards.py plan", "python -m pytest -q -n 4 --junitxml=",
                   '"@$RUNNER_TEMP/shard/files.txt"'):
        assert needed in text, needed
    # The environment, collection and test steps run on every shard; only the shard-independent repository checks
    # run once, on shard 1.
    for step in steps:
        if any(k in step for k in ("check_ffmpeg", "pip install", "uv lock --check", "pytest", "sha256sum")):
            assert _if(step) is None, step
    once = ("python scripts/check_lock.py", "python -m pip_audit --strict", "python -m ruff --version",
            "python scripts/lint_topic_bank.py", "python -m unittest discover -s tests")
    for check in once:
        holding = [s for s in steps if check in s]
        assert len(holding) == 1 and _if(holding[0]) == "matrix.shard == 1", check
    upload = [s for s in steps if "actions/upload-artifact" in s]
    assert len(upload) == 1 and _if(upload[0]) == "always()" and "name: shard-${{ matrix.shard }}" in upload[0]
    # The installed-lock cache is keyed by the lock and by this workflow (a step installing something else is a new key).
    cache = [s for s in steps if "Installed-lock cache" in s]
    assert len(cache) == 1 and "hashFiles('uv.lock', '.github/workflows/tests.yml')" in cache[0]


def _ids(files):
    return [f"{f}::test_{n}" for f, count in files.items() for n in range(count)]


def _timings(files, tests=None, skipped_tests=None):
    return {"files": files, "tests": tests or {}, "skipped_tests": skipped_tests or []}


def test_the_plan_puts_every_test_in_exactly_one_shard_balanced_and_deterministic():
    cs = _cs()
    files = {f"tests/test_{c}.py": 3 for c in "abcdefghij"}
    ids = _ids(files)
    timings = _timings({f"tests/test_{c}.py": {"seconds": s, "skipped": 0}
                        for c, s in zip("abcdefghi", (100, 90, 80, 70, 60, 50, 40, 30, 20))})   # test_j: no row
    plan = cs.plan(ids, 4, timings)
    own = cs.owners(ids, plan)
    assert all(1 <= own[i] <= 4 for i in ids)
    flat = [u for shard in plan for u in shard]
    assert len(flat) == len(set(flat))
    assert plan == cs.plan(list(reversed(ids)), 4, timings) == cs.plan(ids, 4, json.loads(json.dumps(timings)))
    loads = cs.shard_loads(ids, 4, timings)
    assert abs(sum(loads) - (540 + 3 * 540 / 27)) < 1e-6   # the unknown file: 3 tests x the known 20 s per test
    assert max(loads) / (sum(loads) / 4) <= 1.1
    assert cs.plan(ids, 1, timings) == [sorted(files)]


def test_a_file_heavier_than_part_of_a_shard_is_split_by_test_id():
    """No shard is bounded by one file: a file holding most of the suite's time is spread over every shard."""
    cs = _cs()
    ids = _ids({"tests/test_big.py": 40, "tests/test_s1.py": 2, "tests/test_s2.py": 2})
    tests = {f"tests/test_big.py::test_{n}": 50.0 for n in range(10)}            # ten long tests with own rows
    timings = _timings({"tests/test_big.py": {"seconds": 800.0, "skipped": 0},     # the other 30 share 300 s
                        "tests/test_s1.py": {"seconds": 4.0, "skipped": 0},
                        "tests/test_s2.py": {"seconds": 4.0, "skipped": 0}}, tests)
    w = cs.weights_per_test(ids, timings)
    assert w["tests/test_big.py::test_0"] == 50.0 and w["tests/test_big.py::test_39"] == 10.0
    plan = cs.plan(ids, 4, timings)
    assert "tests/test_big.py" not in [u for us in plan for u in us]           # split into its ids
    assert {"tests/test_s1.py", "tests/test_s2.py"} <= {u for us in plan for u in us}   # small files stay whole
    own = cs.owners(ids, plan)
    assert {own[i] for i in ids if cs.file_of(i) == "tests/test_big.py"} == {1, 2, 3, 4}
    loads = cs.shard_loads(ids, 4, timings)
    assert max(loads) / (sum(loads) / 4) <= 1.02
    # As a whole file the same suite could not balance: one shard would carry 800 of 808 s.
    whole = cs.plan(ids, 4, timings, split_fraction=10.0)
    assert "tests/test_big.py" in [u for us in whole for u in us]


def test_the_junit_key_is_pytests_own():
    cs = _cs()
    junitxml = pytest.importorskip("_pytest.junitxml")
    for nodeid in ("tests/test_a.py::test_x", "tests/test_a.py::TestK::test_y[a/b-1::2]", "tests/sub/test_b.py::t[x[y]]"):
        names = junitxml.mangle_test_address(nodeid)
        assert cs.junit_key(nodeid) == (".".join(names[:-1]), names[-1])


def _write_shards(tmp_path, cs, ids, shards, timings, results=None, collected=None, seconds=0.5):
    """Artifacts as the shard jobs upload them; results maps test id -> 'pass' | 'skip' | None (omitted)."""
    plan = cs.plan(ids, shards, timings)
    own = cs.owners(ids, plan)
    results = results or {}
    for i in range(1, shards + 1):
        d = tmp_path / f"shard-{i}"
        d.mkdir(parents=True)
        mine = (collected or {}).get(i, ids)
        (d / "collected.txt").write_text("\n".join(mine) + f"\n\n{len(mine)} tests collected in 1.00s\n", "utf-8")
        (d / "files.txt").write_text("\n".join(plan[i - 1]) + "\n", "utf-8")
        cases = []
        for t in ids:
            if own[t] != i:
                continue
            outcome = results.get(t, "pass")
            if outcome is None:
                continue
            classname, name = cs.junit_key(t)
            body = '<skipped type="pytest.skip" message="no map data">x</skipped>' if outcome == "skip" else ""
            s = seconds(t) if callable(seconds) else seconds
            cases.append(f'<testcase classname="{classname}" name="{name}" time="{s}">{body}</testcase>')
        (d / "junit.xml").write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest">'
                                     + "".join(cases) + "</testsuite></testsuites>", "utf-8")
    return tmp_path


def test_verify_passes_when_every_collected_test_ran_once(tmp_path):
    cs = _cs()
    ids = _ids({f"tests/test_{c}.py": 4 for c in "abcdefg"})
    timings = _timings({"tests/test_a.py": {"seconds": 9, "skipped": 1}}, skipped_tests=["tests/test_a.py::test_0"])
    art = _write_shards(tmp_path, cs, ids, 3, timings, results={"tests/test_a.py::test_0": "skip"})
    assert cs.verify(art, 3, timings) == [] and cs.verify(art, 3, timings, suite_baseline=1) == []
    committed = tmp_path / "timings.json"
    committed.write_text(json.dumps(dict(timings, suite={"skipped": 1})), "utf-8")
    assert cs.main(["verify", "--artifacts", str(art), "--shards", "3", "--timings", str(committed)]) == 0
    # Without a committed suite skip baseline the check cannot pass.
    assert cs.main(["verify", "--artifacts", str(art), "--shards", "3", "--timings", str(tmp_path / "none.json")]) == 1


def test_verify_holds_with_files_split_by_test_id(tmp_path):
    """The exactly-once proof and the skip baselines work when a file's tests run in several shards."""
    cs = _cs()
    ids = _ids({"tests/test_big.py": 40, "tests/test_s1.py": 2})
    skipped = ["tests/test_big.py::test_3", "tests/test_big.py::test_17"]
    timings = _timings({"tests/test_big.py": {"seconds": 400.0, "skipped": 2},
                        "tests/test_s1.py": {"seconds": 2.0, "skipped": 0}}, skipped_tests=skipped)
    plan = cs.plan(ids, 4, timings)
    assert "tests/test_big.py" not in [u for us in plan for u in us]
    results = {t: "skip" for t in skipped}
    art = _write_shards(tmp_path / "ok", cs, ids, 4, timings, results=results)
    assert cs.verify(art, 4, timings, suite_baseline=2) == []
    # The same skip count in the file, but on a test of another shard: the per-shard baseline catches the move.
    own = cs.owners(ids, plan)
    other = next(t for t in ids if cs.file_of(t) == "tests/test_big.py" and own[t] != own[skipped[0]]
                 and own[t] != own[skipped[1]])
    moved = {skipped[0]: "pass", skipped[1]: "skip", other: "skip"}
    problems = cs.verify(_write_shards(tmp_path / "moved", cs, ids, 4, timings, results=moved), 4, timings, 2)
    assert any(re.match(rf"shard {own[other]} skipped 1 tests, its baseline is 0", p) for p in problems), problems
    assert not any("tests/test_big.py skipped" in p for p in problems)
    # A split test run by a shard that does not own it is caught, as is the shard's unit list differing from the plan.
    owner = own["tests/test_big.py::test_0"]
    thief = owner % 4 + 1
    jt = tmp_path / "ok" / f"shard-{thief}" / "junit.xml"
    jo = tmp_path / "ok" / f"shard-{owner}" / "junit.xml"
    case = '<testcase classname="tests.test_big" name="test_0" time="0.5"></testcase>'
    jo.write_text(jo.read_text("utf-8").replace(case, ""), "utf-8")
    jt.write_text(jt.read_text("utf-8").replace("</testsuite>", case + "</testsuite>"), "utf-8")
    problems = cs.verify(tmp_path / "ok", 4, timings, 2)
    assert any(f"shard {thief} ran tests/test_big.py::test_0, which the plan gives shard {owner}" in p for p in problems)


@pytest.mark.parametrize("fault, needle", [
    ("missing", "ran in no shard"),
    ("twice", "ran more than once"),
    ("unknown", "match no collected test"),
    ("collection", "collected a different suite"),
    ("skip", "the suite skipped 1 tests, its baseline is 0"),
    ("artifact", "did not finish"),
    ("files", "but the plan assigns"),
])
def test_verify_fails_on_any_coverage_or_skip_breach(tmp_path, fault, needle):
    cs = _cs()
    ids = _ids({f"tests/test_{c}.py": 4 for c in "abcdefg"})
    timings = {}
    results, collected = {}, None
    if fault == "missing":
        results = {ids[5]: None}
    elif fault == "skip":
        results = {ids[2]: "skip"}
    elif fault == "collection":
        collected = {2: ids[:-1]}
    art = _write_shards(tmp_path, cs, ids, 3, timings, results=results, collected=collected)
    plan = cs.plan(ids, 3, timings)
    own = cs.owners(ids, plan)
    if fault == "twice":   # a test planned for shard 1 also ran in shard 2
        j2 = art / "shard-2" / "junit.xml"
        classname, name = cs.junit_key(next(t for t in ids if own[t] == 1))
        j2.write_text(j2.read_text("utf-8").replace(
            "</testsuite>", f'<testcase classname="{classname}" name="{name}" time="0.1"/></testsuite>'), "utf-8")
    elif fault == "unknown":
        j1 = art / "shard-1" / "junit.xml"
        j1.write_text(j1.read_text("utf-8").replace(
            "</testsuite>", '<testcase classname="tests.test_zz" name="test_new" time="0.1"/></testsuite>'), "utf-8")
    elif fault == "artifact":
        (art / "shard-3" / "junit.xml").unlink()
    elif fault == "files":
        (art / "shard-2" / "files.txt").write_text("\n".join(plan[1][:-1]) + "\n", "utf-8")
    problems = cs.verify(art, 3, timings, suite_baseline=0)
    assert any(needle in p for p in problems), problems


def test_before_per_file_timings_exist_the_suite_total_is_the_skip_check(tmp_path):
    cs = _cs()
    ids = _ids({f"tests/test_{c}.py": 4 for c in "abcd"})
    art = _write_shards(tmp_path, cs, ids, 2, {}, results={"tests/test_b.py::test_0": "skip",
                                                           "tests/test_c.py::test_1": "skip"})
    assert cs.verify(art, 2, {}, suite_baseline=2) == []
    problems = cs.verify(art, 2, {}, suite_baseline=1)
    assert problems == ["the suite skipped 2 tests, its baseline is 1 (most: tests/test_b.py 1, tests/test_c.py 1)"]


def test_a_shard_over_its_skip_baseline_fails_even_when_the_suite_total_is_unchanged(tmp_path):
    """Skips moving from one file to another keep the total but break the per-file and per-shard baselines."""
    cs = _cs()
    ids = _ids({f"tests/test_{c}.py": 4 for c in "abcd"})
    for skipped_tests in (["tests/test_a.py::test_0"], []):   # measured skipped ids, or file rows only
        timings = _timings({f"tests/test_{c}.py": {"seconds": 10, "skipped": 1 if c == "a" else 0} for c in "abcd"},
                           skipped_tests=skipped_tests)
        own = cs.owners(ids, cs.plan(ids, 4, timings))
        moved = next(t for t in ids if cs.file_of(t) == "tests/test_b.py" and own[t] != own["tests/test_a.py::test_0"])
        art = _write_shards(tmp_path / str(len(skipped_tests)), cs, ids, 4, timings, results={moved: "skip"})
        problems = cs.verify(art, 4, timings, suite_baseline=1)
        assert not any(p.startswith("the suite skipped") for p in problems)
        assert any("tests/test_b.py skipped 1" in p for p in problems)
        if skipped_tests:   # file rows alone cannot apportion a split file's baseline; the per-file check holds
            assert any(re.match(r"shard \d skipped 1 tests, its baseline is 0", p) for p in problems)


def test_refresh_averages_runs_per_test_and_keeps_long_tests_and_skips(tmp_path, monkeypatch):
    cs = _cs()
    ids = _ids({"tests/test_a.py": 2, "tests/test_b.py": 3})
    slow = {"tests/test_b.py::test_2": 9.0}
    run1 = _write_shards(tmp_path / "1", cs, ids, 2, {}, results={"tests/test_b.py::test_1": "skip"},
                         seconds=lambda t: slow.get(t, 0.5))
    run2 = _write_shards(tmp_path / "2", cs, ids, 2, {}, results={"tests/test_b.py::test_1": "skip"},
                         seconds=lambda t: 2 * slow.get(t, 0.5))
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    got = cs.refresh([run1, run2], ["r1", "r2"])
    assert got == {"suite": {"skipped": 1, "runs": ["r1", "r2"]},
                   "files": {"tests/test_a.py": {"seconds": 1.5, "skipped": 0},
                             "tests/test_b.py": {"seconds": 15.0, "skipped": 1}},
                   "tests": {"tests/test_b.py::test_2": 13.5},
                   "skipped_tests": ["tests/test_b.py::test_1"]}
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    assert cs.refresh([run1])["suite"] == {"skipped": 1, "runs": ["123"]}


def test_the_committed_timings_cover_the_suite_and_its_skip_baseline():
    cs = _cs()
    assert TIMINGS.is_file()
    data = cs.load_timings(TIMINGS)
    # The suite's skip count on the single-job baseline run (43731546225: 9,363 passed, 387 skipped).
    assert cs.load_suite_baseline(TIMINGS) == 387
    files = data["files"]
    assert len(files) >= 380 and all(f.startswith("tests/") and f.endswith(".py") for f in files)
    # Measured on CI (the mean of this row's runs): the per-file baselines and the skipped ids add up to the
    # suite's, so the per-file and per-shard skip checks are on.
    assert len(data["suite"]["runs"]) >= 2
    assert sum(row["skipped"] for row in files.values()) == 387 == len(data["skipped_tests"])
    assert all(row["seconds"] >= 0 and row["skipped"] >= 0 for row in files.values())
    assert all(cs.file_of(t) in files for t in list(data["tests"]) + data["skipped_tests"])
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")}
    assert set(files) <= on_disk     # no row for a removed file
