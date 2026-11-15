# QF-137: tests that tick the layout do not race the background photo-set counter

QF-135's CI shard 6 failed `tests/test_ui01b_dashboard_in_place.py::test_the_weekly_digest_arrives_silently` on
every run (2 == 1 sends; the extra send was "🔔 Photo sets not ready"). Root cause (QF-135 review 3): a layout
tick's notice conditions call `control.ready_pools(db, queued, wait=0)` (QF-115). With the sets not yet counted
it answers None and starts a daemon counter. The answers (`_READY_CACHE`), the pending sets, the counter, the
"counting" flag and the listening bot are module state, and they outlived each test on a pytest-xdist worker. A
test passed only when an earlier test on its worker had already counted the same three topics. When it ran first
(shard 6's grouping, or alone), its first tick started the count, the count finished before the second tick, and
the photo item arrived and rang.

Not a product defect. In production the item is kept while the sets are counted. Once the count ends, the next
tick posts "Photo sets not ready" when it is true, and it rings once. The new sibling test checks this, with
three fake sets that all count as not ready. While the count runs, two ticks post no photo item and send nothing.
After the count, one tick posts the item and sends exactly one alert. The next tick sends nothing and leaves no
push pending. So the unchanged item is not rung again, and no second ring is held back by the one-a-minute alert
gap.

Review 1 mutated `sync` so that an unchanged open condition is rung again on every tick. The sibling test fails
on that mutation and passes without it.

## What changed (tests only)

- `tests/conftest.py`: autouse `photo_set_count_reset` for every test (a superset of the tests that tick the
  layout or read `ready_pools`, so a new test is covered too). Before and after each test it joins the count's
  threads (`photo-set-count`, `photo-set-redraw`), clears the answers and the pending sets, and resets the
  counter, the "counting" flag and the listener. A count thread still alive a minute after its test fails that
  test. It depends on `drained_bots`, so it tears down before the bots are drained and before the test's patches
  are undone.
- With every test starting cold, the probe below found three tests that tick or sync more than once and count
  what changed. Each now counts the sets deterministically before its first tick, and asserts more, not less:
  - `test_the_weekly_digest_arrives_silently`: the photo item is there before the digest; no background count
    runs; no push is pending (so the one-a-minute alert gap cannot hide a digest that rang).
  - `test_an_unchanged_condition_writes_nothing_and_a_new_review_rings_again` (ui01b)
  - `test_the_thirty_minute_reminder_alert_starts_with_its_alarm_clock` (ui01e)
  All three also failed alone on a2ed7fe1, in a fresh process.
- New sibling test (ui01b), the other outcome held with a gate:
  `test_a_photo_count_that_finishes_between_ticks_rings_once_and_the_digest_stays_silent`. While counting, both
  ticks keep the photo item absent and the digest sends nothing. After the count is released and joined, the next
  tick posts the item ("Only 0 of 3 queued topics have photos ready.") and sends exactly one alert,
  `🔔 Photo sets not ready`. A later tick sends nothing.
- Regression test: `tests/test_qf137_photo_count_isolation.py`. Each check runs in a new process:
  - the three tests run with the counter forced either to finish the moment it starts or never to run;
  - two ordered tests check that the state one test leaves is gone for the next.

No skip, xfail, quarantine or loosened assertion. No src/ change.

## Measured (`python measure.py <repo root>`, before.json on a2ed7fe1, after.json on this commit)

| case (each test in a new process) | a2ed7fe1 | this commit |
|---|---|---|
| alone (cold count, as on shard 6) | 0 / 3 pass | 3 / 3 |
| count finishes between ticks (forced) | 0 / 3 | 3 / 3 |
| count still running (forced) | 3 / 3 | 3 / 3 |
| state left by an earlier test is gone | 0 / 1 | 1 / 1 |

Regression test: on a2ed7fe1, 2 failed and 1 passed, both failures by assertion. The forced-finish case failed on
the digest test; in one process the first test warms the cache for the other two. The left-behind case failed with
"an earlier test's count was left". On this commit: 3 passed.

Probe (local, not committed): a plugin forced the counter to finish at start or never to run. It ran over the 34
files that tick the layout, sync conditions, draw dashboards or probe (1,020 tests, -n 4). On a2ed7fe1 all modes
passed, because a warm cache masked the race. With the reset alone, the 3 tests above failed (2 natural, 3 forced
finish). With the fixes: 0, except the new sibling test under the forced modes, which replace the real thread it
drives by design.

Shard proof: see the build note (QF-135 shard 6's plan, generated with QF-135's `scripts/ci_shards.py`, run
against this commit).
