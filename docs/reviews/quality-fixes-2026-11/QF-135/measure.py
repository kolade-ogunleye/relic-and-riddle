"""QF-135 acceptance: CI runs the full suite as parallel shards with the same coverage.

    python measure.py <repo root>

Reads `.github/workflows/tests.yml` at <repo root> and prints JSON: how many jobs run the suite; and, when the commit
has scripts/ci_shards.py, the collection-equality proof run locally (`pytest --collect-only -q` of the full suite
versus the union of each shard's own `pytest --collect-only -q @<its units file>`, exactly as CI passes them:
identical id multisets, every test in exactly one shard), the planned CI seconds per shard from
.github/ci_shard_timings.json and the expected CI wall time and Actions minutes.

Model (from this row's CI runs): a shard's pytest wall time is its planned junit CPU-seconds over 3.9 (the
CPU-seconds per wall second every shard achieved with -n 4 on a 4-vCPU runner, runs 43750859188 and 43754490252);
each shard adds ~95 s of set-up and collection, the `test` job ~20 s. The committed timings are the mean of those two
runs, which differed by 32 % in total (12,004 and 15,877 CPU-s); "slow run" scales the plan by the slower run's
1.14 x the mean. Without timings (the base), the single-job run 43731546225: 58 s set-up and a 2,797 s suite.
"""
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

BASE_SUITE_S = 2797.0      # run 43731546225 "9363 passed, 387 skipped ... in 2797.08s"
BASE_SETUP_S = 58.0        # same run: set-up steps before pytest (checkout .. topic-bank lint)
SHARD_OVERHEAD_S = 95.0    # runs 43750859188 / 43754490252: checkout .. collection, per shard
AGGREGATE_S = 20.0         # the `test` job: checkout, setup-python, download artifacts, verify
CPU_PER_WALL = 3.9         # junit CPU-seconds per wall second per shard (-n 4, 4 vCPU), both runs
SLOW_RUN = 15877.0 / ((12004.0 + 15877.0) / 2)   # the slower measured run over the committed mean


def collect(root: Path, args: list[str]) -> list[str]:
    out = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q", *args],
                         cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
    if out.returncode != 0:
        raise SystemExit(f"collection failed ({out.returncode}): {out.stdout[-2000:]}{out.stderr[-2000:]}")
    ids = []
    for line in out.stdout.splitlines():
        if not line.strip():
            break
        if "::" in line:
            ids.append(line.strip())
    return ids


def main(root: Path) -> dict:
    text = (root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    m = re.search(r"^  SHARDS: (\d+)$", text, re.MULTILINE)
    shards = int(m.group(1)) if m else 1
    result = {"jobs_running_the_suite": shards}
    if not (root / "scripts" / "ci_shards.py").is_file():
        result["expected_wall_s"] = round(BASE_SETUP_S + BASE_SUITE_S)
        result["expected_actions_minutes"] = round((BASE_SETUP_S + BASE_SUITE_S) / 60, 1)
        return result
    sys.path.insert(0, str(root))
    from scripts import ci_shards as cs
    timings = cs.load_timings(root / ".github" / "ci_shard_timings.json")
    full = collect(root, [])
    plan = cs.plan(full, shards, timings)
    union: Counter = Counter()
    per_shard = []
    with tempfile.TemporaryDirectory() as tmp:
        for k, units in enumerate(plan, 1):
            args_file = Path(tmp) / f"units-{k}.txt"
            args_file.write_text("\n".join(units) + "\n", encoding="utf-8", newline="\n")
            ids = collect(root, [f"@{args_file}"])
            union.update(ids)
            per_shard.append(len(ids))
    full_c = Counter(full)
    loads = cs.shard_loads(full, shards, timings)
    mean = sum(loads) / shards
    suite_s = [x / CPU_PER_WALL for x in loads]
    wall = SHARD_OVERHEAD_S + max(suite_s) + AGGREGATE_S
    slow_wall = SHARD_OVERHEAD_S + max(suite_s) * SLOW_RUN + AGGREGATE_S
    minutes = (shards * SHARD_OVERHEAD_S + sum(suite_s) + AGGREGATE_S) / 60
    files_split = sorted({cs.file_of(u) for us in plan for u in us if "::" in u})
    result.update({
        "full_collection": len(full),
        "full_collection_files": len({cs.file_of(i) for i in full}),
        "shard_collections": per_shard,
        "union_of_shard_collections": sum(union.values()),
        "union_equals_full_collection": union == full_c,
        "ids_in_no_shard": sorted(full_c - union)[:10],
        "ids_in_more_than_one_shard": sorted(k for k, n in union.items() if n > 1)[:10],
        "units_per_shard": [len(us) for us in plan],
        "files_split_by_test_id": files_split,
        "timed_files": sum(1 for f in {cs.file_of(i) for i in full} if f in timings.get("files", {})),
        "planned_cpu_seconds_per_shard": [round(x) for x in loads],
        "imbalance_max_over_mean": round(max(loads) / mean, 3),
        "expected_pytest_s_per_shard_on_ci": [round(x) for x in suite_s],
        "expected_wall_s": round(wall),
        "expected_wall_s_slow_run": round(slow_wall),
        "expected_actions_minutes": round(minutes, 1),
        "expected_actions_minutes_slow_run": round((shards * SHARD_OVERHEAD_S + sum(suite_s) * SLOW_RUN
                                                    + AGGREGATE_S) / 60, 1),
        "plan_is_deterministic": plan == cs.plan(list(reversed(full)), shards, timings),
    })
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=1))
