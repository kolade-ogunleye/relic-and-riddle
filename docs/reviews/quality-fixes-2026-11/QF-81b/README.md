# QF-81b: a planned carousel's plan is bound to the review card you approved

Source: the QF-81 port review, non-blocking item 1. The gap predates QF-81's exclusion, and no application path
reaches it today: only the make writes the plan, a resume resets the approval, and a new version is reviewed again.
This row is hardening. Round 2 answers review 1's blocking item (what a binding hold tells you and what Redraw does).

## Finding

QF-110's approved review fingerprint (`src/telegram_state.review_fingerprint`) binds the copy, the video, the cover,
the Stories and the carousel slides. QF-81 left a planned carousel's slides out of it, because they are drawn after
review, at the slot. Nothing was bound in their place, and the carousel slot never checked the approval.

The reviewer's probe rewrote `carousel_plan.json` after approval so that it named another photo, with that photo's
correct SHA-256. The slot's own plan checks passed, and the slot drew and posted the substituted photo.

Review 1 (of round 1): the round-1 hold told you to "make a new version and approve it", which cannot be done. The
carousel slot only takes runs whose Reel is posted (`published`), and `prepare_new_version` refuses those. Its alert
also offered Redraw, and `redraw_carousel` did not check the binding again: the redrawn carousel went first at the
next slot, was held again there (a second alert), and a newer ready episode lost that slot.

## Change

- `src/telegram_state.py`: for a planned run, `carousel_plan.json` is added to the fingerprint in place of the
  excluded slides. The plan holds the photo paths, their SHA-256s and their order, the poster SHA-256s and the
  DRAW_FIELDS digest of the carousel copy. The make writes it once, before the card is sent, and nothing writes it
  afterwards (the preview draws into `carousel_draw/`, the slot installs into `carousel/`). The fingerprint therefore
  stays the same through preview, slot install and Redraw. Unplanned runs are unchanged: their slides are still
  bound.
- `main.py`, `_approval_broken`: compares the review card's recorded fingerprint with the current one. It gives the
  reason "its plan or files changed after you approved the review, so it is not the carousel you approved", or,
  when a reviewed file can no longer be read, that reason.
- `main.py`, `_post_carousel_if_due` (round 2): the ready carousels are taken in their usual order (a Redraw first,
  then the newest). A planned one that no longer matches its approval is held **before** the slot is taken, and the
  next ready episode takes the slot the same day. If none is left, the slot is not marked as used.
- `main.py`, `_hold_carousel(..., binding=True)` (round 2): a binding hold is marked `"binding": True` in its booking
  (QF-81's planner, layout and audit holds are not). Its alert is "❌ Carousel not posted for <episode>: <reason>.
  This carousel will not post. Nothing was posted, and its Reel and other posts are not affected." Its only button is
  `📋 Details` (the existing `cpost:` episode screen), plus the 🏠 Dashboard every notice carries, which closes it.
  It offers no Redraw and no instruction that cannot be carried out.
- `main.py`, `redraw_carousel` (round 2): it runs `_approval_broken` first. While the mismatch remains, Redraw is
  refused with the reason ("can't be redrawn: <reason>. It will not post, and nothing was changed."). The booking,
  its drawing and its priority are left as they are. QF-81's own holds, whose binding still matches, redraw as before.
- Unchanged: an approval with no card record (the command line, the auto-approve flag) has no binding, so it posts as
  before. In QF-110, a missing record keeps a grant closed; here, it adds no restriction. Both read "no record" as
  "nothing to bind to". Also unchanged: resume and reset (the approval is reset, so a new card is needed), unplanned
  runs, the plan's own checks at the slot, and the V2 card. The V2 card's "Preview carousel" button is left to the
  UI-01 track.

## Measurement (`measure.py <repo root>`, offline)

The same measure.py run on each root. before.json is 47ae674, after.json is this commit. The b8259cb7 column is
round 1, for this round's comparison (not committed).

| probe | 47ae674 (before.json) | b8259cb7 (round 1) | this commit (after.json) |
|---|---|---|---|
| plan rewritten after approval: fingerprint still matches | yes | no | no |
| plan rewritten after approval: carousel | published, substituted photo drawn | failed (held) | failed (held) |
| its alert asks for a new version | (no hold) | yes | no |
| its alert offers Redraw | (no hold) | yes | no |
| its alert says it will not post | (no hold) | no | yes |
| Redraw tapped, newer r2 ready, next slot: Redraw refused | (r1 posted Tue) | no | yes |
| ... posted at that next slot | r2 | nothing (r2 waiting) | r2 |
| ... hold alerts | 0 | 2 | 1 |
| newest r2 mismatched, older r1 ready, same slot: posted | r2 (substituted) | nothing | r1 |
| unchanged plan | published | published | published |
| preview after approval, then slot | published | published | published |
| command-line approval (no card record) | published | published | published |
| substituted plans posted | 1 | 0 | 0 |
| normal runs held | 0 | 0 | 0 |

## Regression test

`tests/test_qf81b_plan_fingerprint.py`, 11 tests, run on QF-81's `coord` fixture:

- On 47ae6747: 7 fail, all by assertion, and 4 pass. The failures are the 3 round-1 failures (plan rewrite posted;
  plan not in the fingerprint; missing reviewed file posted) and the 4 new round-2 tests (no hold at all there).
- On b8259cb7: 6 fail, all by AssertionError, and 5 pass. The failing tests are:
  - the binding hold says it will not post and offers no Redraw;
  - Redraw is refused while mismatched;
  - a newer ready episode takes the next slot after a refused Redraw;
  - a binding hold never takes the slot from another ready episode;
  - the two round-1 hold tests, now asserting the new wording.
- On this commit: 11 passed. They include QF-81's own planner-unavailable hold on a bound approval: it still offers
  Redraw, the Redraw is accepted, and the carousel posts in the next slot.

The real order: the make writes the plan before the card exists (`_render_approve_publish`: `carousel_lazy.plan`, and
later `telegram_bot` records the card fingerprint). So an approval is always made with the plan in place, and a
normal run is never held. No dev run in `output/run_*` has a plan (QF-81 is newer than every dev run), so the
committed QF-81 fixtures are used.

One effect to know about: a planned run approved on a build between QF-81 and this commit recorded a card fingerprint
without the plan. At its slot it would be a binding hold and its carousel would not post (its Reel is not affected).
QF-81 is not deployed, and no dev run has a plan, so no run is affected.

Not taken here (review 1, non-blocking 2): a plan deleted after approval still fails silently at the slot. That
behaviour is the same on 47ae6747, so it predates this row.
