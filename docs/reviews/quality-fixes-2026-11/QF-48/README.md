# QF-48: maintenance.apply() raises in production

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2. Operator decision: in
production `maintenance.apply()` raises before touching git or pip and no `upd:apply` button is
offered; `check()` stays report-only; updates arrive as a pull request and a signed release tag.
Development keeps `apply()`.

## Change

- `src/maintenance.py`: `apply_allowed()` (false when `RELIC_ENVIRONMENT=production`, which the
  production launcher sets); `apply()` raises `PRODUCTION_REFUSAL` as its first step;
  `apply_buttons(plan)` returns the Apply row only outside production; the weekly report says in
  production that updates arrive as a PR and a signed tag instead of "Approve to apply".
- `main.py`: the weekly check takes its buttons from `apply_buttons()`; the daemon's
  `apply_updates` (an old `upd:apply` button pressed) answers with the refusal and starts no
  thread; `main.py update --apply` prints the refusal and exits 1.

## Metric (`python measure.py <repo root>`)

Real `apply()` with a recording `_run` (nothing is executed), `RELIC_ENVIRONMENT=production`:

| | before (32d1357) | after |
|---|---|---|
| production: git/pip steps run by `apply()` | 9 (branch, pins, pip install, pytest, commit, ff-merge) | 0, raises `RuntimeError` |
| production: `upd:apply` button offered | yes | no |
| development: `apply()` runs, button offered | yes, yes | yes, yes |
| acceptance_met | false | true |

Files: `before.json`/`before.txt`, `after.json`/`after.txt`, `measurements.json` (last run).
Regression test: `tests/test_qf48_apply_refused_in_production.py` (3 failed, 1 passed on the
start commit; 4 passed on the fix).
