# QF-69: V2 card without mock values; no Undo of a Reel that began to post

Finding (project-quality-review-2026-11-05, 9.2): the `TELEGRAM_CARDS_V2` card showed invented values, and its Undo
paths reset a decision without the fingerprint or any check of what had already happened. Acceptance: `json.dumps`
of the card's blocks contains none of the literals; an Undo of a posted Reel is refused.

## What the base already did (UI-02, d31604ad)

UI-02 wired every V2 value to the run's records (or `V2CardUnavailable` and the live card), and moved the V2 card's
own Undo onto `db.undo_review_decision` (one statement, refused once anything is booked or posted) behind the card's
fingerprint. This row re-measures both and closes what was left of the class.

## The defect class left on the base

Every way an Undo or a cancel takes back a Reel booking ends in `PipelineCoordinator.cancel_post`: the clean chat's
"Undo approval" on a booked Approve (`review_panel._undo_item` → `cancelpost`), Posting's Cancel, and (same
read-then-write shape) "Make a new version" of a blocked booking. It read the open bookings, then wrote "cancelled"
over each, with no check of the run. And the poster wrote to bookings it had read earlier without checking they were
still as read. So:

1. A cancel tapped while the Reel was being uploaded (Post now, or the slot's pass) marked the booking cancelled and
   answered "is a post candidate again"; the upload then posted it, and the item that offered the Undo was closed.
2. A posting pass (or Post now) that had read the booking before the cancel landed uploaded it anyway: the cancel
   had turned the run into `simulated`, which `claim_publication` accepts.
3. (Review 1) A missed-slot reschedule wrote "pending" over a booking the operator had cancelled after the pass read
   it, so the cancelled Reel came back and was posted by the next pass. `_hold_booking`, `_block_booking` and
   `move_post` had the same read-then-write shape.

## Fix

- `db.cancel_reel_bookings`: one `BEGIN IMMEDIATE` transaction that refuses when the run is publishing, published or
  of unknown outcome, or a publication of it is recorded published/unknown; otherwise cancels the open bookings and
  turns `scheduled` into `simulated`. Used by `cancel_post` and by the blocked booking's "Make a new version".
- `db.claim_publication(run_id, booking=)`: the upload's claim requires the booking to be still open, in its own
  transaction. The database serializes the cancel and the claim: either the cancel lands first (the claim is
  refused: "Its booking was cancelled; nothing was posted.") or the claim does (the run is publishing and the cancel
  is refused). The cancel no longer depends on the upload lock, which is also held for the release checks before the
  claim: a cancel then (nothing posted yet) cancels, instead of "Not cancelled" (review 1).
- `db.update_scheduled_post(..., expect=)`: a compare-and-set on the status the caller read, in the same statement;
  returns whether it applied. All writers were audited (`update_scheduled_post(` and `UPDATE scheduled_posts` in
  `src/` and `main.py`: 38 on 6a7ef303, 39 on e5217bce with QF-114's binding refusal). The 28 that move, hold or fail
  a booking before an upload (missed-slot reschedule, hold, block, move, the Stories re-timed after Post now, the photo
  posts' pre-upload failures: files missing, Story credits, a carousel caption that is not the reviewed one (review 2)
  and QF-114's "not the review you approved", every carousel wait/expiry/hold/redraw write) pass the status they read;
  a write that did not apply sends no notice. Left unconditional, on purpose: the cancels
  themselves (cancelled is final), and the writes that record an upload's outcome (published, unknown, the result of
  a post or a retry): an outcome is a fact and is recorded whatever the booking's status says.
- The review panel keeps the "Approved" item when the cancel answers the refusal (decided by the cancel's own answer,
  `posting_view.NOT_CANCELLED`, not by the run's state read afterwards).

Wording (approved by the operator, 2026-11-16): "⏹ Not cancelled: the Reel has started posting or has posted. Posting
shows what happened." and "Its booking was cancelled; nothing was posted."

## Measurements (`measure.py`, committed inputs only)

| Metric | base 6a7ef303 (`before.json`) | this commit (`after.json`) |
|---|---|---|
| invented literals in `json.dumps(blocks)` of the real-run fixture's V2 card | 0 | 0 |
| cancels of a Reel that began to post refused (publishing, published, unknown outcome, unknown publication, upload in progress) | 0/5 | 5/5 |
| cancelled bookings uploaded (a pass, or Post now, read the booking before the cancel) | 2 | 0 |
| cancelled bookings brought back by a later poster write (reschedule, hold, block, move) | 1/4 | 0/4 |
| control: a cancel before posting still cancels the Reel and its Stories | true | true |
| control: a cancel during the checks before the claim (upload lock held, nothing posted) cancels | true | true |

## Real data (read-only; counts only, in `real_data.json`)

- V2 card built (offline, nothing sent) for every real run with a script and a release report: 30 development runs
  (`output/run_*` of the main checkout) and 34 production-sample runs: 64 cards on the base and on this commit, 0
  unavailable, 0 errors, 0 literal hits on either side (UI-02 removed them; this row adds no card change).
- Booking state: the development database holds 0 scheduled posts, so the race cannot be replayed on it. The
  production sample's metrics record 6 published runs and 1 scheduled run (and one Story publication of unknown
  outcome).

## Regression test

`tests/test_qf69_undo_never_cancels_a_posting_reel.py`: 16 tests, three of them with real threads (a cancel during
the checks before the claim; a cancel once the upload began; 12 rounds of the reschedule and hold writes racing a
cancel). On the base they fail by assertion except the two controls.
