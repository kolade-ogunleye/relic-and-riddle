# QF-47 Docs say dry-run, code posts live

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2. Production has run `daemon` live since the
operator activated it on 2026-10-25 (`src/service_runtime.py`, `deployment/windows/runtime/content_runtime.py`),
but four docs said it ran `daemon --dry-run`, and CI tested the obsolete
`deployment/windows/handoff/tools/content_runtime.py` copy, which still started `--dry-run`: CI was green on a
launcher production never installs.

Operator decision (binding): fix the four docs; the dashboard masthead shows the mode (LIVE posting / dry run);
delete the `deployment/windows/handoff/` launcher copies (tools and tests); CI points at `runtime/`.

## What changed

- `docs/deployment.md` (3 places), `docs/runtime-contract.md`, `docs/unattended-startup.md`,
  `docs/production-probe-handoff.md`: production posts live (approval still decides); development's bot-started
  runs are dry runs (`BOT_RUNS_DRY`); the masthead names the mode. `docs/deployment-reconciliation.md` and
  `docs/unattended-startup.md` now point at `deployment/windows/runtime` instead of the handoff copies.
- `deployment/windows/handoff/tools/*` and `handoff/tests/*` deleted. `handoff/README.md` and `provenance.json`
  stay as the record of what the lab handed back (git history keeps the bytes).
- The adapter's Python tests and the `ContentWorkloadProcess` Pester tests moved to
  `deployment/windows/runtime/tests/`, now against the installed copy; the dry-run assertion became
  `test_pipeline_runs_the_daemon_live` (`["daemon"]`). The other handoff tests covered only the deleted tools or
  lab-owned files and went with them.
- `.github/workflows/tests.yml`: the adapter step runs `python -m unittest discover -s tests -p "test_*.py"` in
  `deployment/windows/runtime`.
- `src/control.py`: `record_run_mode` / `run_mode_label`; `main.py bot` records `BOT_RUNS_DRY`, `main.py daemon`
  records its `--dry-run` flag; the dashboard masthead ends with `📡 LIVE posting` or `🧪 dry run` (nothing before
  a process recorded its mode).

## Metric (before = start commit 0ec79ea, after = this fix)

| Measure | Before | After |
| --- | --- | --- |
| Dry-run production claims in the four docs | 6 | 0 |
| Launcher command production installs | `daemon` | `daemon` |
| Files under `handoff/tools` + `handoff/tests` | 16 | 0 |
| CI adapter unittest directory | `deployment/windows/handoff` | `deployment/windows/runtime` |
| Masthead names the mode (live / dry / unrecorded) | no / no / - | `📡 LIVE posting` / `🧪 dry run` / neither |
| Acceptance met | false | true |

On the start commit the old CI command (`unittest discover -s tests/python` in `handoff`) passes 18/18 while
asserting `["daemon", "--dry-run"]`: it tested the copy, not the launcher.

## Re-run

```
python docs/reviews/quality-fixes-2026-11/QF-47/measure.py <repo root>
```

No inputs beyond the repository: the dashboard is driven on a scratch database in a temporary folder with every
socket blocked. `before.json` / `after.json` are its output on the two trees; `measurements.json` is the last run.
Regression test: `tests/test_qf47_dry_run_docs.py` (fails on the start commit, passes here).
