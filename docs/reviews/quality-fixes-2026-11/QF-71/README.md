# QF-71: silent notifications except Needs you and the review card

Finding (project-quality-review-2026-11-05, 9.2): every notification rang in V1. Fix: `disable_notification` unless
`group == "needs"` or the review card. Acceptance: `notify("x", group="info")` sends `disable_notification=true`.

## The defect class

- `notify`: only V2 silenced anything; in the earlier layout every grouped notice rang.
- The run's progress card (and its redraw) and, around the earlier layout's review card, its albums and "In full"
  detail messages rang too: one review rang up to four times.
- The other half of the class, a Needs-you notice that does not ring: the coordinator's `_notice(..., section="needs")`
  with no group (19 call sites: a failed run, Auto-approve refused, Blocked at the release gate, a missed slot, a
  carousel or Reel not posted...) went out as group "studio" (`_alert`'s default; `say()` drops the section when the
  clean chat is off), so V2 sent them silent, to the studio topic.

## Fix

- `notify`: silent unless the group or the section is "needs", in both layouts; a call naming neither is silent too
  (operator, 2026-11-16: one rule; their written approval changes only the expectation of origin/main's
  `tests/test_ui_overhaul.py::test_silent_notification_flag`).
- `_notice`: a notice whose section is "needs" is sent in the needs group (it rings, and V2 files it under Needs you).
- Progress card, its redraw, the review card's albums and detail messages: `disable_notification`. The review card
  itself rings, once (V2's pair already rang once, from its Needs-you line).
- The clean chat (UI-01) has its own push rules and is unchanged.

### Round 2 (review 1 and the operator's rulings of 2026-11-16)

- The three Needs-you notices that carried an explicit `silent=True` ring: "⚠️ Publishing accounts" (posting will
  fail), "🖼 Photo sets not ready" (topics will be skipped until photos are approved) and the earlier layout's
  improvement proposal card (it asks for a decision). `measure.py` now counts an explicit `silent=True` on a Needs-you
  site as a silent Needs-you site.
- "Batch production stopped: the last episode did not finish cleanly." is Needs you: production the operator started
  stopped on its own and only they can start it again; it rings, offers 📊 Status, and its mark is ⚠️ (a Needs-you
  mark, `layout_repost.NEEDS_MARKS`; it was ⏹). "Batch stopped" for an expired batch stays in Runs (the operator's own
  old batch, nothing failed).
- A late approval's missed-slot notice (`worker_deadline.missed_slot_notice`) goes in the needs group, as
  `_reschedule`'s does (it went out as "studio").
- The 19 section-needs notices stay in the Needs-you topic and ring (ruling 3).

## Measurements (`measure.py`: every notice call site found by syntax, each kind sent once through the code)

111 call sites at the base (on e5217bce, with QF-70: the pipeline batch and QF-70 added sites), 112 here (the late approval's missed-slot notice is now a `_notice` call in main); one kind decided at run time (`"needs" if failed else "info"`) is in neither class.

| Metric | base a0a7d95a (QF-70 on e5217bce, `before.json`) | this commit (`after.json`) |
|---|---|---|
| V1: routine call sites that ring | 47 of 51 | 0 of 49 |
| V1: Needs-you call sites that are silent (explicit `silent=True` counted) | 3 of 53 | 0 of 56 |
| V2: Needs-you call sites that are silent | 24 of 53 | 0 of 56 |
| a notice naming no group and no section rings (V1, V2) | yes, no | no, no |
| progress card rings (V1, V2) | 1, 1 | 0, 0 |
| messages of one review that ring, of 4 sent (V1, V2) | 4, 2 | 1, 1 |

## Real data (read-only, counts in `real_data.json`)

The development database's 131 recorded alerts, each matched to its call site by the literal start of the message:
84 matched (47 start with a run-time value and are not attributed). Of the 84, 82 are Needs-you notices (none is silent
before or after; 3 of them are section-only Needs notices that V2 sent silent on the base) and 2 are routine (both rang
in V1 on the base; 0 ring now).

## Test changes

- origin/main, with the operator's written approval (decisions `TG-BATCH-OPERATOR-RULINGS-2026-11-16.md`, ruling 2):
  `tests/test_ui_overhaul.py::test_silent_notification_flag`, only the expectation for a notice naming no group
  (it rang; it is silent).
- Branch-only: `tests/test_ui01a_notifications_panel.py::test_with_the_switch_off_a_carousel_hold_is_sent_as_on_the_base`:
  the carousel hold (a `section="needs"` notice) reaches `notify` with group "needs" instead of "studio".

## Regression test

`tests/test_qf71_quiet_notifications.py`: 22 tests (V1 and V2); round 2 adds the bare notice, a scan that no
Needs-you call site is sent `silent=True`, and the batch-stopped and missed-slot notices as Needs you.
