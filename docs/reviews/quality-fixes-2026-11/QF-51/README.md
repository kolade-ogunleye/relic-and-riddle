# QF-51: Config drift: YouTube analytics vars, RELIC_* vars, inventory regex

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2. The three
`YOUTUBE_ANALYTICS_*` credentials were read from `os.environ` inside `src/youtube_analytics.py`
(through its own `_env()` helper) and were missing from the log masker's `SECRET_NAMES`; the
launcher's `RELIC_DEPLOY_REQUEST`, `RELIC_DEPLOY_STATUS` and `RELIC_RELEASE_RECEIPT` were
documented nowhere; `tests/test_settings_inventory.py` matched only a literal `os.getenv("X")`, so
it saw none of it.

## Change

- `src/config.py`: `YOUTUBE_ANALYTICS_CLIENT_ID`, `YOUTUBE_ANALYTICS_CLIENT_SECRET`,
  `YOUTUBE_ANALYTICS_REFRESH_TOKEN` read here (already documented in `.env.example`).
- `src/youtube_analytics.py`: `_env()` (an `os.getenv` wrapper) replaced by `_setting()`, which reads
  the three values from `src.config` at each use; no environment read is left in the module.
- `src/ops.py`: the three names added to `SecretMasker.SECRET_NAMES`, so a log line never shows the
  client secret or refresh token (and `tests/conftest.py` now blanks them in every test).
- `.env.example`: a "Set by the launcher, never in .env" block documents `RELIC_APP_ROOT`,
  `RELIC_DEPLOY_REQUEST`, `RELIC_DEPLOY_STATUS` and `RELIC_RELEASE_RECEIPT` as comments (no
  `KEY=` line, so `env_sync` never writes them into a `.env`).
- `tests/test_settings_inventory.py`: the scan now matches `os.getenv`, `os.environ.get`,
  `os.environ[...]` (reads, not assignments) and `_env(...)`, resolves a module constant
  (`DEPLOY_REQUEST_ENV = "RELIC_DEPLOY_REQUEST"`) to the name it holds, walks `src/` recursively,
  and refuses a read whose name it cannot tell. Launcher-set names may be read where they are used
  but must be documented and must be set by a launcher (`Start-ContentVmWorkload.ps1` or
  `src/service_runtime.py`).

## Metric (`python measure.py <repo root>`)

| | before (712c87c) | after |
|---|---|---|
| settings read outside `src/config.py` (extended scan) | 3 (`YOUTUBE_ANALYTICS_*` in `src/youtube_analytics.py`) | 0 |
| unnamed environment reads outside config | 3 (`_env(n`, `_env(name`, `os.getenv(name`) | 0 |
| names read but undocumented in `.env.example` | 4 (`RELIC_APP_ROOT`, `RELIC_DEPLOY_REQUEST`, `RELIC_DEPLOY_STATUS`, `RELIC_RELEASE_RECEIPT`) | 0 |
| inventory drift total | 8 | 0 |
| YouTube names in `SecretMasker.SECRET_NAMES` | 0 of 3 | 3 of 3 |
| YouTube values shown in a masked log line | 3 of 3 | 0 |
| the tree's own `tests/test_settings_inventory.py` | 2 passed (literal-only scan, drift unseen) | 4 passed |
| the extended inventory test run on the start tree | 3 failed, 1 passed | 4 passed |
| acceptance_met | false | true |

Files: `before.json`/`before.txt`, `after.json`/`after.txt`, `measurements.json` (last run).
Regression test: `tests/test_qf51_config_drift.py` (5 failed on the start commit; 5 passed on the fix).
