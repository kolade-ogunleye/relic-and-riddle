# QF-135: CI runs the full suite in parallel shards

Operator-approved plan `FREEZE-EFFICIENCY-2026-11-15`: the GitHub Actions full-suite wall time ~48-50 min -> ~15 min
with the same coverage, the same environment and the same strictness.

## Before (dbcabf65)

One job `tests / test`: set-up 58 s (checkout, Python, apt libraries, the pinned FFmpeg 8.1 with its SHA-256 and
`check_ffmpeg.py`, uv, `uv lock --check`, the hashed lock, `check_lock.py`, pip-audit, ruff, the topic-bank lint),
then `pytest -q -n 4` (9,363 passed, 387 skipped in 2,797 s; run 43731546225, job 47 min 41 s; run 43720222472,
49 min 41 s), then the Windows launcher adapter tests. CI fetches no map data (the map tests skip, part of the 387)
and sets no FONT_SIZE; both stay so.

## After

`.github/workflows/tests.yml`:

- `shard` (matrix `shard: [1 .. 8]`, `SHARDS: 8`, `fail-fast: false`, check names `tests / shard 1` ..
  `tests / shard 8`): every shard has today's whole environment (apt libraries with FriBiDi, the pinned FFmpeg 8.1
  verified by SHA-256 and `check_ffmpeg.py`, uv 0.13.8, `uv lock --check`, the hashed lock). The shard-independent
  repository checks (`check_lock.py`, pip-audit `--strict`, ruff, the topic-bank lint, the Windows launcher adapter
  tests) run once, on shard 1. Each shard collects the full suite (`pytest --collect-only -q`), plans its units with
  `scripts/ci_shards.py plan` (whole test files, and single test ids of the files too heavy to keep whole) and runs
  them with the same command as before plus a result file, the units passed as pytest's `@file` argument list:
  `pytest -q -n 4 --junitxml=... @files.txt`. It uploads `collected.txt`, `files.txt` and `junit.xml` even when it
  fails.
- `test` (the existing check name, `tests / test`; `needs: shard`, `if: always()`): fails unless every shard
  succeeded, then `scripts/ci_shards.py verify` fails when the shards collected different suites, when any collected
  test is missing from the results, ran twice or matches no collected test, when a shard ran other units than the
  plan computed from the committed timings or a result came from a shard that does not own the test, or when the
  suite, any test file or any shard skipped more tests than its committed baseline. It uploads the timings the run measured (`ci-shard-timings`) for refreshing the committed file.
- Caching: the pip cache (setup-python, as before), the FFmpeg archive (actions/cache keyed by its SHA-256; the
  hash is still checked on every run) and, from round 2, the installed lock (the Python install directory, keyed by
  runner image, Python release and the hashes of `uv.lock` and the workflow file (round 3); `pip install --require-hashes -r` still runs and keeps only
  the exact pinned versions). There is no map data to cache: CI never fetched it.

Split: `scripts/ci_shards.py` (standard library only, no new dependency) plans units longest-processing-time first
onto the least-loaded shard, from `.github/ci_shard_timings.json`: per-file mean CI seconds and skip baseline, a row
for every test of at least 2 s, and the measured skipped test ids. A file heavier than 0.2 of one shard's share is
split into its test ids (7 files at 8 shards); every other file stays whole. A test without its own row shares what
its file's row leaves; a file without a row is weighted by its test count times the known mean per test, with a skip
baseline of 0. Deterministic: the plan is a function of the collected id set (sorted first) and the committed file,
the same on every shard and in the aggregate.

A new test file has no row, so its skip baseline is 0: a row that adds a file with skipping tests (or more skips in
a file) must refresh `.github/ci_shard_timings.json` in the same commit (from its CI run's `ci-shard-timings`
artifact, or by raising that file's `skipped` and the suite's), or CI goes red naming the file and the skip reasons.

Branch protection: `remediation/baseline` has none (GitHub API 404 "Branch not protected", no rulesets), so no
required-check setting changes. The workflow is still `tests` and the green/red check is still `test`; the eight
shard checks are new and informational. Nine jobs per run, within the concurrent-job limit.

## Measurements

`python measure.py <repo root>` (before.json: dbcabf65, after.json: this commit). It runs only `pytest --collect-only`.

| | before | after (round 3) |
|---|---|---|
| jobs running the suite | 1 | 8 shards + the `test` aggregate |
| full collection (local, `--collect-only -q`) | 9,750 (= CI's 9,363 passed + 387 skipped) | 9,768 (+18: this row's test file) in 388 files |
| union of the 8 shards' own collections (`--collect-only -q @<units file>`, as CI passes them) | - | 9,768: equal to the full collection as a multiset; 0 ids in no shard, 0 in two |
| units per shard / tests per shard | - | 177-185 units; 850 / 1,356 / 1,389 / 1,124 / 946 / 1,046 / 2,092 / 965 tests |
| files split by test id | - | 7 (listed in after.json) |
| planned CI CPU-seconds per shard (mean of two runs) | - | 1,743 each (max/mean 1.000) |
| plan deterministic (reversed collection, same plan) | - | yes (also shuffled, checked once) |
| expected CI wall time | 2,855 s (47.6 min; real: 47 min 41 s and 49 min 41 s) | ~562 s (9.4 min): ~95 s set-up and collection + 447 s pytest (1,743 / 3.9) + ~20 s; ~624 s (10.4 min) at the slower measured run |
| expected Actions minutes | 47.6 | ~72.6 (80.8 at the slower run's speed); public repository, minutes free |

## Round 2

Round 1 (count-balanced, no per-file rows) ran on CI as run 43750859188: green, every collected test exactly once
(9,379 passed + 387 skipped = 9,766; the base's 9,363 + 387 plus this row's 16), but 23.2 min wall: pytest per shard
583 / 1,290 / 657 / 796 s, because one file, `tests/test_r4_graphics_2026_11_02.py`, holds 2,893 of the suite's
12,004 junit CPU-seconds and the count split put it beside other heavy files. Actions minutes 61.7 (47.7 base).

Round 2 commits that run's own `ci-shard-timings` artifact as `.github/ci_shard_timings.json` (388 files, measured
CI seconds and skips; suite skipped 387, run 43750859188). The plan on this commit's collection is 3,001 / 3,001 /
3,001 / 3,001 junit seconds (max/mean 1.000); at round 1's 3.9 CPU-seconds per wall second that is ~770 s of pytest
per shard, ~14.6 min wall. With rows present, the per-file and per-shard skip baselines are on (sum of rows 387 =
the suite's). `verify` on round 1's downloaded artifacts with the timings applied to coverage: no problem.

Actions minutes: round 1 overhead per shard was ~95 s (checkout 9-13 s, apt 7-9 s, FFmpeg 3-5 s, uv 2 s, the hashed
install 28-38 s, collection 18-23 s); the rest of the rise was the shards' uneven pytest time (3,326 s against the
base's 2,797 s). Round 2: balanced shards (~12,004 / 3.9 = ~3,080 s of pytest in total) and the installed-lock cache
(on a hit the install step only confirms the pins, saving most of its ~30 s per shard; the first run after any
`uv.lock` change misses). Estimate: 4 x ~75-95 s + ~3,080 s + ~15 s = ~56-58 min (base 47.7, +17-22 %): the
per-shard collection (the in-CI coverage proof) and set-up cannot be shared across runners. Measured on the lander's
CI run of this commit.

## Round 3

Round 2 (whole files, round 1's timings) ran as 43754490252: green, coverage exact, skips 387, but 24.6 min wall.
Pytest per shard 1,034 / 1,268 / 824 / 1,035 s (max/mean 1.22): the same 9,766 tests took 15,877 junit CPU-s against
round 1's 12,004 (+32 %), with single files moving most (test_timeline_hierarchy 802 -> 1,913 s), and one file,
`tests/test_r4_graphics_2026_11_02.py` (2,900-3,900 CPU-s), bounds any whole-file shard at ~750-1,000 s.

Round 3 changes the planner and the timings (the coverage proof and the skip checks are the same, now by test id):
- Files heavier than 0.2 of a shard's share are split by test id (test_r4_graphics, test_timeline_hierarchy,
  test_freeze_thennow, test_freeze_cards, test_qf25_card_composition, test_r4_callouts, test_callouts_v3 at 8
  shards). The ids go to pytest through an `@file` list (ids hold spaces and brackets). Checked locally: a sample of
  split ids with brackets ran under `-n 2` from an `@file`, and every junit key matched its id.
- Timings: the mean of both real runs per test (`ci_shards.py refresh --artifacts <run 1> <run 2>`): 388 files,
  926 tests of at least 2 s with their own rows, 387 skipped ids (= the per-file skips = the suite's 387).
- verify owns each test by shard: a result from a shard that does not own the test fails; the per-shard skip
  baseline is the measured skipped ids that shard owns.
- SHARDS 4 -> 8 for margin against the run-to-run variance. Replaying the round-3 plan on both real runs' measured
  per-test seconds: 8 shards max/mean 1.054 and 1.060, max shard 1,582 and 2,103 CPU-s = ~406 and ~539 s of pytest
  (6 shards: 1.047 and 1.034, ~537 and ~702 s). With ~95 s of set-up and collection and ~20 s for `test`: ~8.7 min
  at the faster run's speed, ~10.9 min at the slower (6 shards: ~10.9 and ~13.6 min). The replay is in-sample (the
  timings are those runs' mean); a run ~50 % slower than the slower one still fits ~15 min at 8 shards.
- Actions minutes (public repository: free; reported as they are): 8 x ~95 s overhead + the suite's ~3,080-4,070 s
  of pytest + ~20 s = ~64-82 min against the base's 47.7 (+35-70 %); measure.py's model gives 72.6 (80.8 at the slower run). The
  per-shard set-up and collection (the in-CI coverage proof) are what grows with the shard count.
- The installed-lock cache key adds the workflow file's hash (a workflow step that installs something else is a new
  key; pip never uninstalls extras).

Why the round-1 timings were missing: CI's log has no per-test names or durations (`pytest -q` with xdist prints
dots), and a local Windows full run was stopped at 69 % at the lander's request.

Local proof of the checks: `tests/test_qf135_ci_shards.py` (18 tests: 18 fail on dbcabf65 by assertion, 18 pass
here) builds shard artifacts and shows `verify` failing on a test missing, run twice or unknown, on differing
collections, a missing artifact, a shard running other units, a split test run by a shard that does not own it, a
suite or per-file or per-shard skip excess (also a skip moving between shards inside one split file); the planner
splitting a file that holds most of the suite over every shard and keeping small files whole; refresh averaging
runs per test; that the
junit key is pytest's own `mangle_test_address`; and the workflow's shape (matrix, `fail-fast: false`, the
`test` job's `needs`/`if: always()`, the environment steps on every shard, the repository checks once).

## Ways it could fail (and what holds)

- A shard fails or is cancelled: `fail-fast: false` lets the others finish; `test` runs `if: always()` and fails on
  `needs.shard.result != success` (a cancelled run still produces a red `test`, never a skipped one).
- A shard's selection drops or repeats a test: the plan comes from the collection itself (every collected test is in
  exactly one unit of one shard), and the aggregate compares the junit results with the full collection (missing / twice /
  unknown), so a dropped test fails CI, not only a unit test.
- Passing explicit files or test ids collects differently from `testpaths` discovery (an explicit path bypassing an
  ignore, a conftest difference, an id that does not match back): the result-vs-collection equality catches extras
  and absences; measure.py shows the 8 `@file` collections add up to the full one locally.
- A split file's module-scoped fixtures now run in more than one shard: xdist already splits a module's tests across
  its 4 workers, so module fixtures already ran several times per run; no new sharing assumption.
- Shards see different suites (a nondeterministic parametrization, an environment difference): every shard uploads
  its full collection and the aggregate requires them identical.
- Plans diverge between shards (a different timings file, an unstable sort): the plan is a pure function of the
  collection and the committed file with total ordering on ties; the aggregate recomputes it and compares every
  shard's `files.txt`.
- junit key collisions or a key format change in pytest: keys are compared as a multiset; the regression test
  checks `junit_key` against pytest's own `_pytest.junitxml.mangle_test_address`.
- xfail counted as a skip: junit `skipped` of type `pytest.xfail` and unexpected passes are excluded, as pytest's
  summary excludes them.
- A test depending on another file's side effects (order dependence): xdist already ran tests in varying order and
  worker split; a shard now holds fewer files per worker. A failure would be a real defect surfaced, not hidden.
- Skips spread so the total stays at 387 while one shard skips more: caught by the per-file and per-shard baselines
  (on from round 2; the regression test shows it).
- A skip that varies by environment (another runner image) would turn CI red with the file and reasons named: all
  shards share one image; refresh the timings when the image changes the skip set.
- Timings go stale as files change, or a run is much slower: only balance or wall time degrades, never coverage;
  refresh from runs' shard artifacts (`refresh` averages several runs).
- A renamed skipping test: its id is not in `skipped_tests`, so its shard's skip baseline counts it as new and CI
  goes red naming it; refresh the timings in that commit.
- The installed-lock cache is stale or wrong: its key is the runner image, the Python release and the hashes of
  `uv.lock` and the workflow file, and `pip install --require-hashes -r` still runs, replacing any version that is not the exact pin.
- Collection costs once per shard (~25 s on CI, 50 s on the busy dev PC): accepted, it is what lets each shard plan
  independently and prove the same suite; it is in the minute estimate.
- FFmpeg from the cache is a different file: the SHA-256 is checked after every restore or download, before unpacking.
- The ruff/pip-audit/lint/lock/launcher checks run on shard 1 only: they read the repository, not the shard; a
  failure fails shard 1 and so `test`.
- Required-check name: no branch protection or ruleset exists; `tests / test` keeps its name and meaning.
