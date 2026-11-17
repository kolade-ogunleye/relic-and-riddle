# QF-96: tests leave the root logger as they found it

`ops.setup_logging()` (run by `main.main`) adds a `logging.StreamHandler()` bound to the `sys.stderr` of the moment.
Under pytest that is the running test's capture stream, which pytest closes afterwards. The handler stayed on the
root logger, so a later test on the same worker that flushed root handlers failed with "I/O operation on closed
file". In the full suite this hit `test_integrity_gates::test_logs_persist_to_a_masked_rotating_file` twice in a row
on 2026-11-10 (bfb090d), whenever `test_script_library` or `test_regenerate_reason_2026_10_28` shared its worker.

Fix (operator-approved, 2026-11-10): an autouse fixture in `tests/conftest.py` removes and closes every root handler a
test added, and restores the root level and the `_pipeline_configured` flag. No test, assertion or expected value
changes; no application code changes.

Run `python docs/reviews/quality-fixes-2026-11/QF-96/measure.py <repo root>` (each ordering in its own pytest
process; prints JSON, writes nothing). On the parent, copy `tests/test_qf96_root_logging.py` in first.

| run (one process, in this order) | before (5b9f49e) | after |
|---|---|---|
| test_script_library.py, then the integrity test | 1 failed, 20 passed | 21 passed |
| test_regenerate_reason_2026_10_28.py, then the integrity test | 1 failed, 10 passed | 11 passed |
| tests/test_qf96_root_logging.py (configure under capsys, then flush) | 1 failed (ValueError: I/O operation on closed file), 1 passed | 2 passed |
