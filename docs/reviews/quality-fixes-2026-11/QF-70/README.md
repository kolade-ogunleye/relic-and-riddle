# QF-70: a stale review stays pending, 12-hour floor, an open review counts as idle

Finding (project-quality-review-2026-11-05, 9.2): a missed review window (floor 1 h, closing 20 minutes before the
slot) failed the run and the topic; an open review blocked release installs. Fix as specified: never auto-fail, keep
the review pending with a stale badge under Needs you; 12-hour floor; the deployer treats an open review as idle.
Acceptance: after the window the topic stays `queued`; `release_gate` reports idle. The four origin/main assertions
this policy changes were approved by the operator on 2026-11-07 (decisions `QF-70.md`,
`QF-70-proposed-main-tests.patch`), applied exactly.

## The defect class

- The wait's timeout (`wait_for_decision`) and the 26-hour sweep (`cleanup_stale_approvals`) recorded the decision
  `timeout`, and `_settle_decision` made the run `timeout` and the topic `failed`.
- `src/config.py`: `APPROVAL_TIMEOUT_SECONDS` defaulted to 3600 and any lower value was taken.
- `scripts/release_gate.py`: every `pending_approval` run and every pending approval was busy, and the worker waiting
  on the review held the daemon's production lease; `publishing` was not busy at all.
- Found on the way, same class (a long review lets its topic be made twice): `get_next_topic` picks an
  `in_production` topic older than `STALE_PRODUCTION_HOURS` (6 h), so a 12-hour window would have let a second run
  of the topic start while its review waited.

## Fix

- `db.mark_review_stale`: one transaction with the check that the review is still undecided; the approval stays
  `pending` (a `timeout` the sweep recorded before this change becomes pending again), the run stays
  `pending_approval`, the topic goes from `in_production` back to `queued` (only while it is still this run's). A
  setting `review-stale:<run>` marks it; a new delivery of the card (`record_approval`) clears it.
- A stale review is listed by `timed_out_reviews` (the existing "Review timed out" Needs-you item and the Reviews
  list's "Timed out" section, with Resend; no new wording) and not by `list_pending_approvals`; its clean-chat item
  is not resolved while it is stale. A wait resumed on a stale review returns at once.
- `get_next_topic` holds the topic of any undecided review (open or stale) for as long as it waits.
- `REVIEW_WINDOW_FLOOR_SECONDS = 12 * 3600`; `APPROVAL_TIMEOUT_SECONDS` is at least that (`.env.example` 43200).
- `release_gate`: `producing`, `approved`, `publishing` are busy; a `pending_approval` run is busy unless its review
  is undecided; the production lease is not counted while its worker only waits on an undecided review
  (`main.review_wait_lease`, written by the supervised worker around the wait). `docs/deployment.md` updated.

### Round 2 (review 1 and the operator's rulings of 2026-11-16)

- A wait keeps its card's own window (review 1, blocking 1). A wait resumed after a restart (which an install during
  an open review now causes) sized a fresh window of at least 12 hours from the restart, while the card closed at its
  own `expires_at` (taps refused after it): in between the review was neither open nor stale and had no Resend. The
  wait now ends when the card closes (and marks it stale with its Resend notice then), and its 30-minute reminder is
  due at the card's delivery + 30 minutes (one already due before the restart is not said again). A Resend during a
  wait also narrows the new card's `expires_at` to the wait's end.
- The daily run is never skipped by an open review (ruling 1). The daemon's production worker gives an undecided
  review's wait to the daemon 15 minutes before the daily run (`main.daily_hand_off_at`: the release gate's own margin;
  only in a worker the daemon supervises, with its production lease and schedule recorded). The worker ends, which
  frees the run lock; the daemon follows the review with the card's own window and acts on the decision, and says in
  Runs: "🎬 <title> still waits for your review; the daily run can start meanwhile." A daily run that still cannot
  start (another run in progress) is said under Needs you: "⚠️ Today's run was skipped: another run was still in
  progress when it was due. Start one from the dashboard when that run ends." (it was a log line only).
- /produce batches (ruling 1 asks for the behaviour, operator-visible). Unchanged rule: the next episode starts after
  the decision on the previous one. When a batch episode's review is handed to the daemon, the batch waits for that
  decision (the daemon's wait continues it, as the worker's end did) and the hand-off notice adds "The batch's next
  episode starts after your decision." A stale review still ends the batch ("Batch production stopped: the last
  episode did not finish cleanly."), as a timeout did.
- Manual work never dropped (review 2, blocking 1). The decision usually comes while the daily run holds the run lock;
  the batch's next episode (and a Regenerate, a Re-render) found it held and returned with a log line, after the
  operator had been told "queued" / "Regenerating". A manual run that finds the lock held now waits behind the run in
  progress: `main.queue_manual_run` persists it (setting `manual-runs-queued`), the daemon starts the first waiting run
  as soon as the run in progress ends (`drain_manual_queue` when the lock is released), and at startup for any queued
  before a restart. So the true behaviour: after your decision the batch's next episode starts at once if nothing is
  running, otherwise as soon as the run in progress (usually the daily run, including its own review wait up to its
  hand-off) ends.
- Release gate vs the worker stop (review 1, blocking 3). The deployer checks the gate once and then writes the hold;
  an Approve tapped before the launcher stops the worker could be acted on and killed mid-act. The launcher now asks
  `scripts/release_gate.py` again immediately before it stops the worker tree (`Test-ContentReleaseGate`, every
  15 s while held); while busy it declines (`Hold.json` state `declined`, with the gate's reasons), and the deployer,
  which waits 6 minutes for a confirmation, clears the hold and tries later ("the launcher did not confirm the hold";
  nothing changed). A gate that cannot run or answer counts as busy. Why an Approve is then either fully acted on or
  not started: a decision the gate saw is busy ("a review decision awaiting action") until it is acted on, so the hold
  is declined. A decision recorded after the last check is not acted on while the hold exists
  (`main.wait_while_release_held`, before every act in `_decide_review`; the hold is `control/Pipeline.hold`, found
  beside the launcher's RELIC_RELEASE_RECEIPT): it stays recorded, and after the stop the daemon acts on it at its
  restart (resume_reviews), or at once if the hold is declined or cleared. (Round 2 relied on the Undo window, which
  only V2 and clean-chat decisions wait out; a V1 card or a command-line decision is acted on at the next poll. The
  hold check covers every kind.) The only window left is an act that had already begun before the deployer wrote the
  hold, which the gate saw as busy. Pester tests:
  `deployment/windows/tests/ContentReleaseHold.Tests.ps1` (13 passed locally; CI does not run Pester).

## Measurements (`measure.py`, generated inputs)

| Metric | base 0bc5a49d (QF-69, `before.json`) | this commit (`after.json`) |
|---|---|---|
| after the window: decision / run / topic | timeout / timeout / failed | pending / pending_approval / queued |
| topic made again 7 h into an open review (other topics done) | true | false |
| topic made again 8 days after a missed window | false (it was failed: back only by hand) | false (held while it waits) |
| `APPROVAL_TIMEOUT_SECONDS=3600` gives | 3600 s | 43200 s |
| window with the next slot 2 h away | 6000 s | 43200 s |
| release gate, open undecided review | busy (2 reasons) | idle |
| release gate, production worker waiting on that review | busy (3 reasons) | idle |
| controls: decided review not acted on / publishing | busy / busy | busy / busy |
| resumed wait (card closes 1 s in): ends after the card closed | 3 s, not stale | 0 s, stale with its Resend |
| daemon's worker waiting when the daily run nears: wait ends, review stays open | no | yes |

## Real data (read-only, counts in `real_data.json`)

- Development database (backup-API copy): 8 reviews recorded `timeout`, every one by the wait (`system`); their runs
  are `timeout` and 6 of their 8 topics are `failed` today (needing Back to the queue by hand). 3 of the 8 windows
  were under 12 hours (1.0, 1.0 and 11.72 h). No review is pending now, so the gate is idle on it either way.
  Existing `timeout` runs are history and are not migrated: they keep the old Resend path, and their failed topics
  age out of TIMEOUT_HOLD_DAYS as before (or go back to the queue by hand).
- Production sample: 4 of 45 runs ended `timeout`.

## Branch-only test changes (not on origin/main)

- `tests/test_release_gate.py`: `test_an_open_review_keeps_production_busy` becomes
  `test_an_open_review_alone_keeps_production_idle` (this row's acceptance reverses it).
- `tests/test_runtime_fixes_2026_10_25.py`: the stale-sweep fixture's run is made `pending_approval` (a rendered run
  awaiting review), as the approved `test_control` patch does; its assertions are unchanged.

## Regression test

`tests/test_qf70_stale_review.py`: 25 tests (round 3 adds a batch episode and a Regenerate decided while the
daily run holds the lock, which wait and then start; the queue surviving a restart; no act while a release hold
exists. Round 2 adds the resumed wait ending at the card's close, its reminder
time, the hand-off time and outcome, the worker returning a handed-over review without acting, the daemon taking it
on, and a daily run that cannot start being said under Needs you). Pester: `ContentReleaseHold.Tests.ps1`.
