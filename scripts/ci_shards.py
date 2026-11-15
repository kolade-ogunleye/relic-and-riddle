"""Split the test suite into CI shards and prove the shards ran every collected test exactly once (QF-135).

CI runs the full suite as N parallel jobs. Every job collects the whole suite (`pytest --collect-only -q`), plans
the same deterministic split from that collection and the committed timings, and runs only its units (whole test
files, and single test ids of the files too heavy to keep whole) with `--junitxml`. The aggregate job (the `test`
check) then verifies, from the jobs' artifacts:

- every job collected the same test ids (one deterministic collection),
- every collected test appears exactly once across the shards' results (none missing, none twice, none extra),
- every shard ran exactly the units its plan assigned, and every result came from the shard that owns the test,
- the suite skipped no more tests than its committed baseline, and, once timings are measured (refresh), no test
  file and no shard skipped more tests than its committed baseline.

Sub-commands (standard library only; the aggregate job installs nothing):
  plan    --collected FILE --shard I --shards N [--timings FILE]       print shard I's units, one per line
  verify  --artifacts DIR --shards N [--timings FILE] [--refresh FILE]  check the shards' artifacts (exit 1 on any)
  refresh --artifacts DIR [DIR ...] --out FILE [--label RUN ...]        timings from one or more runs' results

Timings live in .github/ci_shard_timings.json:
  {"suite": {"skipped": k, "runs": [...]},
   "files": {"tests/test_x.py": {"seconds": s, "skipped": k}, ...},   mean CI junit seconds per file, skip baseline
   "tests": {"tests/test_x.py::test_y": s, ...},                      tests of at least TEST_ROW_SECONDS
   "skipped_tests": ["tests/test_x.py::test_z", ...]}                  the measured skipped ids
A test without its own row shares what its file's row leaves; a test file without a row weighs its test count times
the known mean per test (with no rows at all, every test weighs the same) and has a skip baseline of 0 (a new skip is
a visible, committed change). Read-only, no network.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMINGS = ROOT / ".github" / "ci_shard_timings.json"
COLLECTED = "collected.txt"   # per-shard artifact: the full `pytest --collect-only -q` output
JUNIT = "junit.xml"           # per-shard artifact: that shard's `--junitxml` results
FILES = "files.txt"           # per-shard artifact: the units the shard ran (pytest's @file argument list)
XPASS = "xfail-marked test passes unexpectedly"
SPLIT_FRACTION = 0.2          # a file heavier than this part of one shard's load is split into its test ids
TEST_ROW_SECONDS = 2.0        # refresh keeps a row for every test at least this long (mean over the runs)


def read_ids(text: str) -> list[str]:
    """The test ids of `pytest --collect-only -q` output: the id lines before the first blank line (the count and
    any warnings summary follow it; a warnings summary can name test ids too)."""
    ids = []
    for line in text.splitlines():
        if not line.strip():
            break
        if "::" in line and not line.startswith(" "):
            ids.append(line.strip())
    return ids


def file_of(test_id: str) -> str:
    return test_id.split("::", 1)[0]


def junit_key(test_id: str) -> tuple[str, str]:
    """(classname, name) as pytest's junitxml writes them for a test id (_pytest.junitxml.mangle_test_address)."""
    path, bracket, params = test_id.partition("[")
    names = path.split("::")
    names[0] = re.sub(r"\.py$", "", names[0].replace("/", "."))
    names[-1] += bracket + params
    return ".".join(names[:-1]), names[-1]


def load_timings(path: Path) -> dict:
    """The committed timings ({"suite", "files", "tests", "skipped_tests"}), or {} without a file."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_suite_baseline(path: Path) -> int | None:
    """The suite's committed skip baseline (the most tests the whole suite may skip), or None without one."""
    suite = load_timings(path).get("suite", {})
    return int(suite["skipped"]) if "skipped" in suite else None


def weights_per_test(ids: list[str], timings: dict) -> dict[str, float]:
    """Expected CI seconds per collected test: its own row; else an equal part of what its file's row leaves; else,
    for an untimed file, the known mean per test (1.0 with no timings)."""
    files, tests = timings.get("files", {}), timings.get("tests", {})
    by_file: dict[str, list[str]] = {}
    for i in sorted(ids):
        by_file.setdefault(file_of(i), []).append(i)
    known_tests = sum(len(m) for f, m in by_file.items() if f in files)
    known_seconds = sum(float(files[f]["seconds"]) for f in by_file if f in files)
    per_test = known_seconds / known_tests if known_tests else 1.0
    out: dict[str, float] = {}
    for f, members in by_file.items():
        if f not in files:
            out.update((i, per_test) for i in members)
            continue
        listed = {i: float(tests[i]) for i in members if i in tests}
        rest = [i for i in members if i not in listed]
        part = max(0.0, float(files[f]["seconds"]) - sum(listed.values())) / len(rest) if rest else 0.0
        out.update(listed)
        out.update((i, part) for i in rest)
    return out


def plan(ids: list[str], shards: int, timings: dict, split_fraction: float = SPLIT_FRACTION) -> list[list[str]]:
    """The units each shard runs: a whole test file, or one test id of a file heavier than `split_fraction` of one
    shard's share (so no shard is bounded by one file). Longest processing time first: units by weight (ties by
    name) onto the least-loaded shard (ties by index). Deterministic for a given collection and timings; every
    collected test is in exactly one unit of exactly one shard."""
    if shards < 1:
        raise ValueError("shards must be >= 1")
    ids = sorted(ids)   # the collection's order does not change the plan (float sums included)
    w = weights_per_test(ids, timings)
    members: dict[str, list[str]] = {}
    for i in ids:
        members.setdefault(file_of(i), []).append(i)
    file_w = {f: sum(w[i] for i in m) for f, m in members.items()}
    share = sum(file_w.values()) / shards
    units: dict[str, float] = {}
    for f, fw in file_w.items():
        if shards > 1 and fw > split_fraction * share:
            units.update((i, w[i]) for i in members[f])
        else:
            units[f] = fw
    loads = [0.0] * shards
    out: list[list[str]] = [[] for _ in range(shards)]
    for u in sorted(units, key=lambda u: (-units[u], u)):
        k = min(range(shards), key=lambda j: (loads[j], j))
        out[k].append(u)
        loads[k] += units[u]
    return [sorted(us) for us in out]


def owners(ids: list[str], shard_units: list[list[str]]) -> dict[str, int]:
    """Test id -> the shard (1-based) whose units hold it (0: none)."""
    unit_shard = {u: k for k, us in enumerate(shard_units, 1) for u in us}
    return {i: unit_shard.get(i, unit_shard.get(file_of(i), 0)) for i in ids}


def shard_loads(ids: list[str], shards: int, timings: dict) -> list[float]:
    w = weights_per_test(ids, timings)
    own = owners(ids, plan(ids, shards, timings))
    loads = [0.0] * shards
    for i in ids:
        loads[own[i] - 1] += w[i]
    return loads


def read_junit(path: Path) -> list[dict]:
    """Every testcase of a junit file: its key, file, seconds and whether pytest counted it skipped."""
    cases = []
    for case in ET.parse(path).getroot().iter("testcase"):
        skipped = [s for s in case.findall("skipped")
                   if s.get("type") != "pytest.xfail" and s.get("message") != XPASS]
        cases.append({"key": (case.get("classname", ""), case.get("name", "")),
                      "file": case.get("file"), "seconds": float(case.get("time") or 0.0),
                      "skipped": bool(skipped),
                      "reason": skipped[0].get("message", "") if skipped else ""})
    return cases


def verify(artifacts: Path, shards: int, timings: dict, suite_baseline: int | None = None) -> list[str]:
    """Every problem found in the shards' artifacts (artifacts/shard-<i>/{collected.txt,junit.xml,files.txt})."""
    problems: list[str] = []
    dirs = {i: artifacts / f"shard-{i}" for i in range(1, shards + 1)}
    extra = sorted(p.name for p in artifacts.glob("shard-*") if p.name not in {d.name for d in dirs.values()})
    if extra:
        problems.append(f"artifacts from shards outside 1..{shards}: {extra}")
    collections: dict[int, list[str]] = {}
    for i, d in dirs.items():
        for name in (COLLECTED, JUNIT, FILES):
            if not (d / name).is_file():
                problems.append(f"shard {i}: no {name} (the shard did not finish)")
        if (d / COLLECTED).is_file():
            collections[i] = read_ids((d / COLLECTED).read_text(encoding="utf-8"))
    if problems:
        return problems
    full = collections[1]
    if not full:
        return ["shard 1 collected no tests"]
    duplicates = sorted(i for i, n in Counter(full).items() if n > 1)
    if duplicates:
        problems.append(f"the collection holds duplicate ids: {duplicates[:10]}")
    for i, ids in collections.items():
        if sorted(ids) != sorted(full):
            only_i, only_1 = sorted(set(ids) - set(full)), sorted(set(full) - set(ids))
            problems.append(f"shard {i} collected a different suite from shard 1: "
                            f"{len(only_i)} only in shard {i} {only_i[:5]}, {len(only_1)} only in shard 1 {only_1[:5]}")
    expected_units = plan(full, shards, timings)
    own = owners(full, expected_units)
    expected = Counter(junit_key(i) for i in full)
    key_id = {junit_key(i): i for i in full}
    seen: Counter = Counter()
    per_file_skips: Counter = Counter()
    per_shard_skips: Counter = Counter()
    skip_reasons: dict[str, list[str]] = {}
    for i, d in dirs.items():
        ran = [line.strip() for line in (d / FILES).read_text(encoding="utf-8").splitlines() if line.strip()]
        if ran != expected_units[i - 1]:
            problems.append(f"shard {i} ran units {ran[:3]}... ({len(ran)}) but the plan assigns "
                            f"{expected_units[i - 1][:3]}... ({len(expected_units[i - 1])})")
        for case in read_junit(d / JUNIT):
            seen[case["key"]] += 1
            test_id = key_id.get(case["key"])
            if test_id is None:
                continue
            if own[test_id] != i:
                problems.append(f"shard {i} ran {test_id}, which the plan gives shard {own[test_id]}")
            if case["skipped"]:
                f = file_of(test_id)
                per_file_skips[f] += 1
                per_shard_skips[i] += 1
                skip_reasons.setdefault(f, []).append(f"{case['key'][1]}: {case['reason'][:120]}")
    missing = sorted(k for k in expected if seen[k] < expected[k])
    twice = sorted(k for k in seen if k in expected and seen[k] > expected[k])
    unknown = sorted(k for k in seen if k not in expected)
    if missing:
        problems.append(f"{len(missing)} collected tests ran in no shard: {missing[:10]}")
    if twice:
        problems.append(f"{len(twice)} tests ran more than once: {twice[:10]}")
    if unknown:
        problems.append(f"{len(unknown)} results match no collected test: {unknown[:10]}")
    total = sum(per_file_skips.values())
    if suite_baseline is not None and total > suite_baseline:
        worst = [f"{f} {n}" for f, n in sorted(per_file_skips.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
        problems.append(f"the suite skipped {total} tests, its baseline is {suite_baseline} (most: {', '.join(worst)})")
    files = timings.get("files", {})
    if not files:
        return problems   # no per-file baseline measured yet: the suite total above is the whole skip check
    for f in sorted(per_file_skips):
        baseline = int(files.get(f, {}).get("skipped", 0))
        if per_file_skips[f] > baseline:
            problems.append(f"{f} skipped {per_file_skips[f]} tests, its baseline is {baseline}: "
                            + "; ".join(skip_reasons[f][:5]))
    skipped_ids = set(timings.get("skipped_tests", []))
    for i in range(1, shards + 1):
        mine = [t for t in full if own[t] == i]
        if skipped_ids:   # the measured skipped tests this shard holds
            allowed = sum(1 for t in mine if t in skipped_ids)
        else:             # its files' baselines (a split file's whole baseline in each shard holding part of it)
            allowed = sum(int(files.get(f, {}).get("skipped", 0)) for f in {file_of(t) for t in mine})
        if per_shard_skips[i] > allowed:
            problems.append(f"shard {i} skipped {per_shard_skips[i]} tests, its baseline is {allowed}")
    return problems


def run_results(artifacts: Path) -> tuple[dict[str, float], set[str]]:
    """One run's junit seconds per test id and its skipped ids, from its shard artifacts."""
    seconds: dict[str, float] = {}
    skipped: set[str] = set()
    for d in sorted(artifacts.glob("shard-*")):
        if not (d / COLLECTED).is_file() or not (d / JUNIT).is_file():
            continue
        key_id = {junit_key(i): i for i in read_ids((d / COLLECTED).read_text(encoding="utf-8"))}
        for case in read_junit(d / JUNIT):
            test_id = key_id.get(case["key"])
            if test_id is None:
                continue
            seconds[test_id] = seconds.get(test_id, 0.0) + case["seconds"]
            if case["skipped"]:
                skipped.add(test_id)
    return seconds, skipped


def refresh(runs: list[Path], labels: list[str] | None = None) -> dict:
    """Timings from one or more runs' shard artifacts: each test's mean junit seconds over the runs that ran it,
    summed per file; a row for every test of at least TEST_ROW_SECONDS; per-file skips (the most any run skipped)
    and the skipped ids of every run."""
    results = [run_results(r) for r in runs]
    total: dict[str, float] = {}
    count: Counter = Counter()
    skipped_ids: set[str] = set()
    for seconds, skipped in results:
        for t, s in seconds.items():
            total[t] = total.get(t, 0.0) + s
            count[t] += 1
        skipped_ids |= skipped
    mean = {t: total[t] / count[t] for t in total}
    files: dict[str, dict] = {}
    for t, s in mean.items():
        files.setdefault(file_of(t), {"seconds": 0.0, "skipped": 0})["seconds"] += s
    for f, row in files.items():
        row["seconds"] = round(row["seconds"], 2)
        row["skipped"] = max(sum(1 for t in skipped if file_of(t) == f) for _, skipped in results)
    suite: dict = {"skipped": sum(row["skipped"] for row in files.values())}
    if labels:
        suite["runs"] = list(labels)
    elif os.environ.get("GITHUB_RUN_ID"):
        suite["runs"] = [os.environ["GITHUB_RUN_ID"]]
    return {"suite": suite, "files": dict(sorted(files.items())),
            "tests": {t: round(s, 2) for t, s in sorted(mean.items()) if s >= TEST_ROW_SECONDS},
            "skipped_tests": sorted(skipped_ids)}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--collected", type=Path, required=True)
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, required=True)
    p.add_argument("--timings", type=Path, default=TIMINGS)
    v = sub.add_parser("verify")
    v.add_argument("--artifacts", type=Path, required=True)
    v.add_argument("--shards", type=int, required=True)
    v.add_argument("--timings", type=Path, default=TIMINGS)
    v.add_argument("--refresh", type=Path, help="also write the timings these results measure to this file")
    r = sub.add_parser("refresh")
    r.add_argument("--artifacts", type=Path, nargs="+", required=True, help="one shard-artifact folder per run")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--label", nargs="*", help="the runs' ids, recorded in the file")
    args = parser.parse_args(argv)
    if args.command == "refresh":
        write_json(args.out, refresh(args.artifacts, args.label))
        return 0
    timings = load_timings(args.timings)
    if args.command == "plan":
        if not 1 <= args.shard <= args.shards:
            parser.error("--shard must be within 1..--shards")
        ids = read_ids(args.collected.read_text(encoding="utf-8"))
        if not ids:
            print("no test ids in the collection", file=sys.stderr)
            return 1
        print("\n".join(plan(ids, args.shards, timings)[args.shard - 1]))
        return 0
    if args.refresh:
        write_json(args.refresh, refresh([args.artifacts]))
    baseline = load_suite_baseline(args.timings)
    problems = verify(args.artifacts, args.shards, timings, baseline)
    if baseline is None:
        problems.append(f"{args.timings} holds no suite skip baseline (\"suite\": {{\"skipped\": n}})")
    full = read_ids((args.artifacts / "shard-1" / COLLECTED).read_text(encoding="utf-8")) \
        if (args.artifacts / "shard-1" / COLLECTED).is_file() else []
    if full:
        loads = shard_loads(full, args.shards, timings)
        print(f"{len(full)} collected tests in {len({file_of(i) for i in full})} files; planned seconds per shard: "
              + ", ".join(f"{x:.0f}" for x in loads))
    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        return 1
    print(f"OK: every collected test ran exactly once across {args.shards} shards; no file or shard skipped over "
          "its baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
