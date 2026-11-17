# QF-65c: no illustration in Stories or carousels; full illustration provenance

Source: row QF-65 of the 2026-11-05 review, less what QF-65a (the AI label derived from the post's media on every
path) and QF-65b (the AI line on decided cards; Facebook older stills fail closed) already landed. Ruling Q6 of the
freeze-resume rulings: the Stories/carousel illustration filter lands as its own row. Base: 11b5e812.

## What changed

1. **One test for an AI-made still** (src/ai_label.py, `is_ai_picture`, `real_stills`, `no_real_photo_reason`).
   - A still is AI-made when QF-65a's own reader (`trace`) says "ai". That covers an image_gen illustration
     (gap_gen_, gen_), a copy whose lineage reaches one (an upscale, a crop, a blur under another name), an AI motion
     clip and anything a record calls AI-generated.
   - A still that cannot be traced is not counted as AI here. The label's own fail-closed rule still covers it.
   - A trace that raises counts as AI, so an unreadable record never lets a picture through as real.
2. **No AI picture in a Story or carousel, by any path.**
   - `image_review.only_strong` (src/image_review.py): AI-made stills are dropped before the strong-photo filter, and
     before its fallback ("none strong, take them all"). With no real photo, it returns an empty list.
   - `scene_planner.strong_shot` (src/scene_planner.py): an illustration, or a copy of one, is never a strong shot.
     It had no credits.json beside it, so it passed as a photo you supplied. So it is never the opening shot, and it
     is never animated (motion.animatable).
   - `generate_story_sequence` (src/multi_format.py): AI-made scene stills are dropped, and the hero fallback is used
     only when the hero is real. With no real photo, `NoRealPhotos` is raised and no frame is drawn.
   - `generate_carousel_sequence` (src/multi_format.py): AI-made stills are dropped. A kept photo match
     (carousel_photos.json) or an earlier copy's recorded match that shows one is never reused: the slides are matched
     again from the real photos. With no real photo, `NoRealPhotos` is raised.
   - QF-81's carousel plan (src/carousel_lazy.py): `plan` records only real stills, and lists the ones it left out
     under `ai_left_out`. `plan_problem` and `draw` hold a plan with no real photo, and say why. A plan written before
     this commit that lists an illustration is drawn from its real photos only.
3. **Held, never padded.**
   - A Story deck whose every picture is AI-made is held the way a QF-23 layout hold is held (main.hold_layout_error):
     frames from an earlier render move to stories/held/, so the posting glob finds none. The hold is recorded in
     `payload["photo_holds"]`.
   - The review card lists it under its own heading: "✋ Held, no real photo to draw on: 1", then "Story frames: no
     real photo to draw the Story frames on: 1 AI-made picture(s) left out (gap_gen_x.jpg); ...". It does not say
     "its text would not fit".
   - A carousel is held at its slot by carousel_lazy, with the reason in the hold alert, as for any other carousel hold.
4. **Full illustration provenance** (src/image_gen.py `illustrate_beat`, src/image_quarantine.py `watch_verdicts`).
   - The lineage record of gap_gen_<key>.png now carries:
     - `prompt`: the whole prompt sent;
     - `raw_sha256`: the SHA-256 of the kept best raw `<key>_best.png`, checked equal to the chosen candidate's raw
       before the record is written;
     - `candidate_index`: the chosen candidate;
     - `judge_verdicts`: each judge's structured answer for that candidate, as the model returned it, or why none
       came. These are the descriptor check (IllustrationVerdict) and the reference comparison (ReferenceComparison),
       each with its model, then the rubric's outcome and score;
     - `candidates`: every candidate's index, raw SHA-256, outcome and score.
   - The verdicts are collected per candidate in a context variable, so candidates scored at the same time in other
     threads do not mix.
   - An illustration whose record cannot be saved is not used (None, with the reason).
   - A saved illustration is reused only when its record has all four fields for the same prompt and the same raw
     file. It then passes the rubric again, and the re-check's answers are kept under `rechecked`.

5. **Round 2 (review 1, NOT OK 8.5/10; rebased onto 94b09f9a, UI-01g).**
   - A carousel with no real photo is known to be held when the run is made, not at its slot. In the real flow
     that is a run whose every candidate still is an illustration (the hero is the first one). The AI stills that
     only_strong left out now go with the real ones to the Story and to the carousel plan, which never draw one but
     record them (`ai_left_out`), so each hold's reason names them. When the plan left out AI pictures and
     `plan_problem` gives a reason (`no_real_photo`), the coordinator records `{"deck": "carousel", ...}` in `payload["photo_holds"]`:
     the card lists "Instagram carousel: its plan records no real photo ..." under "✋ Held, no real photo to draw
     on".
   - The review card: `publishable.card_record` records `carousel_held` for a carousel drawn at its slot
     (`carousel_lazy.no_real_photo`: the plan's problem, only when the plan left out AI pictures; any other plan
     problem is held at its slot as before, QF-81c). The "Also posted" list then says "Carousel (Instagram and Facebook), its caption and slides: not
     posted, held when you approve: <reason>" instead of promising slides and captions, and 🖼 Preview carousel is
     not offered (card and review panel).
   - Approve books such a carousel held at once (`_book_followers`): final, as its slot would hold it, its booking
     error "held when it was booked: ...", the reply line "🖼 Carousel: held, it won't post (...)" and the "❌ Carousel
     not posted ... This carousel will not post" item in the notifications panel now. A failed booking is never
     planned into a slot (carousel_outlook plans waiting ones only), and UI-01g's `_expire_held_carousels` leaves a
     final hold as it is, so it is never later described as expired or slotless. A Redraw still answers "can't be
     redrawn: <reason>".
   - The hold's reason counts and names each AI picture once (`ai_label.distinct_ai_pictures`, by the illustration
     a copy was made from, `ai_source`): a Story from [gap_gen_x.jpg] with hero gap_gen_x.jpg, or with a prepared
     copy of it as hero, says "1 AI-made picture(s) left out (gap_gen_x.jpg)", not 2.
   - Review notes taken: a copy whose lineage record cannot be read is now AI-made for this filter (fails closed;
     round 1's note said a raising trace counted, but trace returns "unknown" there). hero_photo.pick shares
     only_strong, so it no longer offers an AI still as an alternative hero (harmless, and better).
   - Review note left: still_vision's recipe redraw of an old still is not filtered (real data: 0 such stills).

QF-65a and QF-65b are unchanged. Their label and the Facebook hold stay as the net for stills drawn before this
commit. Their tests pass, and one QF-65b test was changed as listed below.

## Measurements (measure.py; `before.json` at 11b5e812, `after.json`)

Fixtures, made in a temporary folder. The real renderers draw them, and the only fakes are the photo match (a model
call), the image generator and the vision judge.

| Case | 11b5e812 | this commit |
|---|---|---|
| Story from [gap_gen_x.jpg] (the spec's acceptance): frames drawn on an illustration | 2 of 2 | 0 (not made, held with the reason) |
| Story from a prepared copy of an illustration + a real photo, illustrated hero: frames on an illustration | 1 of 2 | 0 of 2 |
| Story from illustrations with a real hero: frames on an illustration | 0 of 2 | 0 of 2 |
| Carousel from [gap_gen_x.jpg]: photo slots on an illustration (photo and next photo of 3 slides) | 6 | 0 (not made, held with the reason) |
| Carousel from an illustration + 2 real photos, with a kept match putting the illustration on slide 1 | 2 slots | 0 slots, 3 slides drawn |
| only_strong fallback (no strong photo): illustrations kept | 2 of 2 | 0 |
| only_strong with nothing known: illustrations kept | 2 of 2 | 0 |
| only_strong with an illustration's stem "strong": kept | 1 | 0 |
| strong_shot of an illustration / of a copy of one / of a photo you supplied | true / true / true | false / false / true |
| QF-81 plan: illustrations recorded | 1 | 0 |
| QF-81 plan from an illustration alone: held with the reason | no | yes |
| A plan made before the filter: illustrations drawn from | 1 | 0 |
| Story held for no real photo: card line "Held, no real photo to draw on", frames left to post | no hold kind | yes, 0 |
| Provenance fields in the record (prompt, raw_sha256, candidate_index, judge_verdicts) | 0 of 4 | 4 of 4 |
| prompt is the one sent / raw_sha256 == sha256(<key>_best.png) / candidate_index is the chosen (2) | no / no / no | yes / yes / yes |
| the chosen candidate's descriptor verdict kept (judge call 3) | no | yes |
| candidates recorded | 0 | 4 |
| a record missing one provenance field reused (of 4) | 4 | 0 |

Round 2 (`round2` in the JSON; `before.json` now at 94b09f9a, the rebased base; round 1 is 1e95bd7c rebased):

| Case | 94b09f9a | round 1 | this commit |
|---|---|---|---|
| Story from [gap_gen_beat02.png] with that hero: AI pictures the hold counts | not held (drawn) | 2 | 1 |
| Card of a run whose carousel plan has no real photo: "Also posted" promises slides | yes | yes | no |
| the same: "Also posted" says the carousel is held, with the reason | no | no | yes |
| the same: 🖼 Preview carousel offered | yes | yes | no |

The rest of the fixture results at 94b09f9a are those of 11b5e812, and this commit's are round 1's.

`acceptance_met`: false -> true.

## Real data (read only, counts only; `real_before.json`, `real_after.json`)

These counts come from `measure.py <root> --runs <output> --pool <assets>`, run with each tree's code. The
development runs are the main checkout's output/run_* and assets/. The production sample is the 2026-11-08 pull,
read only.

| Count | development, 11b5e812 -> this commit | production sample, 11b5e812 -> this commit |
|---|---|---|
| runs | 47 | 34 |
| Story sets / carousels | 31 / 31 | 34 / 34 |
| photos their stills show (records found) | 58 | 187 |
| of those AI-made | 0 | 0 |
| Story sets showing an illustration (as rendered) | 0 | 0 |
| carousels showing an illustration (as rendered) | 0 | 0 |
| only_strong over each deck's candidates (its photos and the topic's pool): stills kept | 714 -> 714 | 967 -> 967 |
| of those AI-made | 0 -> 0 | 0 -> 0 |
| decks left without a still | 0 -> 0 | 0 -> 0 |
| stills and pool photos checked as strong shots | 933 | 695 |
| strong shots | 451 -> 451 | 429 -> 429 |
| of the stills checked, AI-made | 0 | 0 |
| one illustration added to each deck's candidates: decks taking it | 2 -> 0 | 0 -> 0 |
| decks not made then | 0 -> 0 | 0 -> 0 |
| an illustration as a deck's only candidate: decks taking it | 62 -> 0 | 68 -> 0 |
| decks not made then (held, never padded) | 0 -> 62 | 0 -> 68 |

- Round 2: re-run with this commit's measure.py on 94b09f9a (`real_before.json`) and on this commit
  (`real_after.json`): every count is the one round 1 measured on 11b5e812 and 1e95bd7c, in both data sets.
- No real run made so far has an illustration in its pictures. Neither data set holds a gap_gen_ file or a record
  of one. So no real Story or carousel carried an illustration, and none is now not made.
- The selection is unchanged on every real deck: the same stills, the same strong shots.
- The probe shows the change on the real pools. An illustration added beside them was taken by 2 development decks
  before. These are decks whose topic folder names no strong photo (or has no credits), where only_strong's
  fallback took every candidate. Now no
  deck takes it.
- An illustration alone was taken by every deck before. Now the deck is not made.

## Tests

- `tests/test_qf65c_illustration_stills.py`, 16 tests. On 94b09f9a, 15 fail (14 by assertion, 1 by importing
  the row's `is_ai_picture`) and 1 passes. The one that passes is a guard: a real hero still serves a Story whose
  scene stills are all illustrations. On round 1 (1e95bd7c rebased) the 4 round-2 tests fail by assertion: the
  corrupt lineage record, the Story count (2), the carousel hold at make time (only the Story was held) and the
  booking (it waited for its slot).
- `tests/test_qf81_lazy_carousels.py` (branch-only):
  `test_a_carousel_drawn_at_its_slot_by_the_real_renderer_with_an_ai_made_photo_carries_the_label` is now
  `..._never_shows_an_ai_made_photo`. Under QF-65b, the real renderer put the AI-made photo on a slide, and the test
  asserted the Instagram label. Ruling Q6 (this row) keeps that photo off every slide. So the planner's answer is
  "photo 1" for every slide, and the test asserts that no request of either copy sends a slide showing the AI-made
  photo. The label on slides that do show AI media (drawn before this commit) stays covered by the `_draw_showing`
  tests in the same file.
- No test on origin/main changed.
