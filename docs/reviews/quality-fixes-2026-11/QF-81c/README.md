# QF-81c: a booked carousel never fails silently at its slot (with QF-81d)

Source: the QF-81b review, round 2, non-blocking items 1 and 2. Both predate QF-81b. No application path deletes or
rewrites a carousel plan after approval today (only the make writes it, and a new make resets the approval), so this
row is hardening against out-of-band changes, under QF-110's rule that posting never blocks silently.

Round 2 adds the case an application path does reach (QF-81c review, round 1, blocking 1): a plan with no photo, and
folds in QF-81d (the same review's non-blocking 1, a separate row folded in by the lander): a drawn booking with only
some of its slides left.

## Finding

On the QF-81b commit (d9d623f), at the carousel slot:

- A planned carousel whose `carousel_plan.json` was deleted took the slot (`carousel_posted_on` set) and then failed
  in `_post_photos` with "image files are missing". No alert was sent, and the next ready episode lost that day's
  slot. A carousel drawn with its run (before QF-81) whose booked slides were deleted failed the same way.
- A plan that could not be read (not UTF-8 text) or was corrupt (cut-off JSON, no photo list) also took the slot. It
  was held with an alert, but that alert offered Redraw, which cannot help: a redraw draws from the same plan.
- The approval binding (QF-81b) was checked before `publication_lock(..., wait=900)`. A plan rewritten while the slot
  waited for that lock was drawn and posted with the substituted photo. A plan deleted in that wait took the slot and
  offered a Redraw that cannot help.
- A plan with an empty photo list, which `carousel_lazy.plan` itself writes when a run's scene media are all video or
  none is on disk, was read without error. The carousel took the slot, was held ("its plan records no photo of the
  topic") with a Redraw, and the Redraw booked it first for the next slot, where it was held again: two slots lost
  and two alerts in the reviewer's two-slot probe. A plan listing only video clips did the same (the renderer draws
  stills only, so its photo match fails).
- QF-81d: a drawn booking with some of its slides gone posted the rest, a deck you never approved, and the alert said
  the carousel was posted. With a card approval, a drawn slide whose bytes changed was posted too: the binding was
  checked only for undrawn bookings.

## Change

- `src/carousel_lazy.py`: `plan_problem(run_dir)` gives the plain reason an undrawn carousel cannot be drawn at all:
  its plan is missing, or cannot be read or is corrupt (the same parse `_checked_stills` uses, now shared through
  `_read_plan`, so the two cannot disagree), or it records fewer stills than a deck needs (`MIN_STILLS`, one: the
  photo match cycles the photos it has) once the entries the renderer drops are left out (a video, an empty path):
  "its plan records no photo of the topic". Otherwise None. `_checked_stills` uses the same rule.
- `main.py`, `_post_carousel_if_due`: before the slot is taken, a drawn booking whose slides are all gone, and an
  undrawn booking whose plan has a `plan_problem`, are held with one alert each, and the next ready episode takes the
  slot the same day (the rule QF-81b uses for a binding hold). The plan is checked before the binding, so the alert
  names the missing plan rather than a vague change.
- `main.py`, the same loop (QF-81d): a drawn booking posts exactly the slides it booked, or nothing. With any of
  them gone it is a final hold ("1 of its 3 slides in carousel/ are no longer on disk (carousel_slide_01.jpg), so
  the rest is not the carousel you approved"); with all of them there, the binding is checked as for an undrawn one
  (a card approval binds the drawn slides' bytes), and a binding hold sets it aside. Either way the next ready
  episode takes the slot.
- `main.py`, the draw: after the publication lock is taken and before drawing, `plan_problem` and `_approval_broken`
  run again. A carousel held there is held with the same alert (a binding hold when its approval no longer matches),
  nothing is drawn or posted, and the slot is left free (`carousel_posted_on` put back), so the daemon's next pass in
  the same slot posts the next ready episode.
- `main.py`, `_hold_carousel(..., final=True)`: the booking is marked `"final": True` (QF-81b's binding holds keep
  `"binding": True`). Its alert is the binding hold's: "❌ Carousel not posted for <episode>: <what is missing>. This
  carousel will not post. Nothing was posted, and its Reel and other posts are not affected." Details only, no
  Redraw.
- `main.py`, `redraw_carousel`: refused, with the reason, while the plan has a `plan_problem`, and for a drawn
  booking's final hold (a new drawing would not be the deck you approved); nothing is changed.

Unchanged: QF-81's own holds (a changed photo, poster or carousel card, the audit, the photo match, the copy fit, a
file error) still take the slot and offer Redraw; QF-81b's binding holds; normal runs; approvals without a card
record; the release gate; settings (none added).

## Metric

`measure.py <repo root>`: fourteen probes, each with an older ready episode r1 and the newest r2, whose carousel is
damaged as the probe says (see the module docstring). The slot runs twice (its pass, then the daemon's next pass); the
two-slot probe adds an oldest ready episode r0, taps the hold alert's Redraw after Tuesday and runs Wednesday's slot.
The renderer double draws from stills only, as the real renderer does.

| | d9d623f (before.json) | this commit (after.json) |
|---|---|---|
| silent failures (r2 failed, no alert) | 3 (plan deleted, plan deleted with a card approval, slides gone) | 0 |
| slots lost (nothing posted in a slot), 14 slots | 11 | 0 |
| damaged carousels posted | 3 (plan rewritten inside the lock; a partial deck; a changed drawn slide) | 0 |
| partial or changed drawn decks posted (QF-81d) | 2 | 0 |
| holds offering a Redraw that cannot help | 7 (unreadable, corrupt, no photo list, deleted inside the lock, no photo, video entries, two-slot) | 0 |
| Redraws accepted that cannot help (two-slot probe) | 1 | 0 |
| normal runs held | 0 | 0 |

Round 1 of this row (before round 2) still lost 4 slots, posted 2 partial or changed decks, offered 3 Redraws that
cannot help and accepted 1. After: every damaged probe holds r2 with exactly one alert that says nothing was posted
and that its Reel and other posts are unaffected, offers no Redraw, and r1 posts in the same slot; in the two-slot
probe r1 posts Tuesday and r0 Wednesday.

## Regression

`tests/test_qf81c_no_silent_slot.py` (22): deleted plan (with and without a card approval), five unreadable or
corrupt plans, Redraw refused, a drawn booking's slides gone, a plan changed and a plan deleted inside the lock; a
plan with no photo (the make's own empty plan, and a plan of video entries), with its Redraw refused; a plan below
the minimum; the two-slot probe; QF-81d: a partial deck (cover slide gone, two of three gone) with its Redraw
refused, and a drawn slide changed after a card approval. Four must hold on both commits: a normal run posts; QF-81's
photo hold still takes the slot and offers Redraw; QF-81b's binding hold is unchanged; a whole drawn deck still posts.
On d9d623f: 18 failed by assertion, 4 passed. Here: 22 passed.
