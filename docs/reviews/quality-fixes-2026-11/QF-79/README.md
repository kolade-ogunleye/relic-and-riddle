# QF-79: classify and archive scripts; eval harness base

Spec (docs/reviews/project-quality-review-2026-11-05.md, QF-79): most scripts have no test, `eval_models.py` and
`eval_facts_first.py` are stale (they compare Opus and Sonnet while production writes with Gemini),
`eval_prompts.py` is the only eval that measures the production writer, and one-off migrations still sit with the
live tools. Fix: classify, move one-offs to `scripts/archive/`, make `eval_prompts.py` the QF-17 harness base.
Acceptance: `scripts/README.md` lists every script with its class; the archive holds the completed one-offs.

## What changed

- **`scripts/README.md`**: every `.py` and `.ps1` file under `scripts/` (50, the archive included) with its class,
  purpose, how to run it, whether production runs or relies on it, and its safety notes (network, spend, writes).
  Classes: `live` (imported by `main.py`/`src/`, or run by the launcher contract, the release deployer, the
  promotion procedure, the development task or CI), `operator`, `data`, `validation`, `eval`, `one-off` (completed,
  still named by a document, the ledger or a test, so it stays where the reference points) and `archived`.
- **Archive** (`git mv`): `clean_dev_telegram.py`, `generate_topic_bank.py`, `regenerate_queue.py`,
  `rename_topics.py`, `reorder_topic_bank.py`. Each is a completed one-off whose name appears in no tracked file
  outside the archive (checked with `git grep -w` over the repository, tests, docs, launchers, `deployment/` and the
  workflow). The code is the original apart from one added block at the top (and `import sys` in
  `generate_topic_bank.py`): without `--i-know-this-is-archived` the script exits 1 with a message saying what it
  would do, before it imports anything from `src` or writes anything, whether started by path or with `-m` from the
  root. That matters because the move alone did not make them safe: started by path, the four that import `src`
  stop at that import, but `generate_topic_bank.py` ran (exit 0) and wrote a stray
  `scripts/data/topics/topic_bank.json`; started with `-m` from the root, `src` imports and `regenerate_queue.py`
  and `reorder_topic_bank.py` would rewrite the real topic rows (the latter also the bank), and the two Telegram
  ones would act on the configured chat. `scripts/README.md` states, per archived script, what it does if run with
  the flag, what it writes and its network use (none is paid). Their pre-existing ruff findings are left as they
  were, not hidden: archiving is a move, not a refactor.
- **Removed** (`git rm`): `round1_mocks.py`, the poster of a retired Telegram UI round's mocks. Nothing referenced
  it, and it carried development Telegram identifiers, a run id and a local path; git history keeps it.
- **Kept although one-off**, because something names them: `migrate_topic_ids.py` (`.gitignore`, the 2026-10-25
  audit), `production_reset.py` (`AGENTS.md`, `tests/test_production_reset.py`), `migrate_legacy_pd_records.py`
  (`QF-57/README.md`). `relocate.py` stays as an operator tool (it is reusable after any folder move).
- **Eval harness base** (`scripts/eval_harness.py`): the resumable draft loop over topics x variants x runs
  (JSONL, resumes where it stopped, `max_drafts` bound) and the paired-by-topic comparison with eval_prompts'
  weights and margin, generalised to any baseline and candidate. It installs each variant's prompts through
  `eval_prompts.variant`, and a live run passes `eval_prompts.score_draft` bound to the configured engine, so it
  measures the production writer. The harness builds no engine and calls no provider; its CLI only summarises a
  results file. `eval_models.py` and `eval_facts_first.py` are marked stale in the inventory, not changed.
- **No live script changed**: the ten `live` scripts are byte for byte the base's (measure.py `live_unchanged`), and
  `eval_prompts.py`, which `main.py`'s nightly cycle imports, is untouched.

## Measurements (`python measure.py <repo root>`)

| | base fe88d4a6 (`before.json`) | this commit (`after.json`) |
|---|---|---|
| scripts total | 50 | 50 (+ `eval_harness.py`, - `round1_mocks.py`) |
| classified in `scripts/README.md` | 0 | 50 |
| archived | 0 | 5 |
| archived scripts referenced outside the archive | 0 | 0 |
| archived scripts whose first statement is the refusal | 0 | 5 |
| unreferenced stand-alone scripts outside the archive | 12 | 6 |
| live scripts unchanged (of 10) against the parent fe88d4a6 | 10 | 10 |
| acceptance | false | true |

The six unreferenced scripts left (`benchmark_encoders.py`, `download_validation_assets.py`, `generate_sfx_pack.py`,
`motion_pilot.py`, `record_font_evidence.py`, `telegram_acceptance.py`) are reusable tools, now documented in the
inventory; only the README and this folder name them.

## Tests

- `tests/test_qf79_script_inventory.py`: every script has a row with a defined class, every row names an existing
  file, `archived` equals the archive folder, no archived script is named outside the archive, each archived script
  started by path and with `-m` exits 1 with its refusal and writes nothing, and the live scripts, the four deploy
  and launcher scripts (`service_runtime.py`, `release_gate.py`, `runtime_probe.py`, `windows_runtime.ps1`) among
  them, are `live` and sit at the paths their consumers use. Their content is not pinned in the test: that they
  are unchanged is this row's property, measured against the parent by `measure.py`, so a later fix to a deploy
  script needs no expected-value change.
  The refusal cases are hermetic, so a removed guard cannot reach the real database or Telegram: each archived
  script runs from a copy of `scripts/__init__.py` and `scripts/archive/` in pytest's `tmp_path`, by path and as a
  module (`runpy`, as `python` and `-m` do), with `cwd` there and an environment built from scratch (only `PATH`,
  `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `PATHEXT` passed; `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP`,
  `TMP` in `tmp_path`; no `RELIC_APP_ROOT`, `TELEGRAM_*`, provider key, database variable or `PYTHONPATH`). The
  child starts under a Python audit hook (`sys.addaudithook`) that records every write-mode open, file-system
  change, `sqlite3.connect`, socket connection or lookup and child process, and refuses each one outside
  `tmp_path` (connections and processes always) before it happens. Besides exit 1 and the message, the test
  asserts an empty audit record, `tmp_path` unchanged (every path, mtime and size), the checkout's `scripts/`
  unchanged by content (`__pycache__` left out) and no `scripts/data/`. It does not snapshot the checkout's
  `data/` or `cache/`: the running development bot changes the database's WAL files there all the time, and the
  hook proves that nothing outside `tmp_path` is written. A meta-test runs an unguarded probe the same way, both
  ways: it finds no `src`, no such variable, no checkout above it and no `data/pipeline.db` to open; its write and
  database inside `tmp_path` are recorded and allowed; its write, folder and database beside `tmp_path`, its
  connection to 127.0.0.1 and its child process are refused, and nothing appears beside `tmp_path`. With the
  guards of `regenerate_queue`, `clean_dev_telegram` and `generate_topic_bank` removed by hand, their six cases
  failed (`ModuleNotFoundError: src` against the copy, or exit 0 with its writes recorded and allowed under
  `tmp_path`), and `git status --porcelain --ignored` of the checkout was the same before and after.
- `tests/test_qf79_eval_harness.py` (mocked, sockets refused): variants' prompts installed per draft and restored,
  resume without redrafting, the paired comparison equal to `eval_prompts.summarize`'s, the margin tie, the offline
  CLI, which prints `eval_prompts.summarize`'s table (with its current/general paired line) only for that pair and
  labels its JSON with the pair compared.
