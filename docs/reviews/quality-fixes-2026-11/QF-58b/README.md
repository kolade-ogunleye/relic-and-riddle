# QF-58b: cascade crops within the hold limit; a refused hold gives way only to a matched photo

Spec: queue/QF-58b.proposed.md, from QF-58 review 6 (queue/QF-58.review-6.md, findings 1-3), decided under the
operator's delegation on 2026-11-10. The coordinator's rounds are in decisions/QF-58b-question.md. The operator's
choice "matched photo only" is in decisions/QF-58b-question-2.md.
- Base: QF-58 (1c4533d8).
- Binding: the gate ruling (decisions/QF-58-gate.md, Option A) and the round-6 scope (decisions/QF-58.md).

## What the row does

1. **Crop source beside its own photograph** (`scene_planner._crop_hold_ok`, Reels only). Both crop tiers of
   `_cascade_fill` use it.
   - A crop of a photograph shown on beat k-1 or k+1 (the photo itself, or another crop of it) is planned only
     while the joined hold stays within `REEL_PHOTO_HOLD_MAX` (6 s). This is the same hold the gate computes.
   - Why: when every photo was in use, the source used to be the pool's first photo, often the opening shot. A
     beat-2 crop of it made a 9 s hold that the gate refused after the voice (dev pools amber_room_heist and
     kailasa_temple_monolith).
   - The next candidate (up to `DETAIL_TRIES`) is used instead. 16:9 is unchanged.
2. **A refused hold gives way only to a matched photo** (in `fit_to_lines`, before QF-58's held-shot steps 4-7;
   helpers `_hold_refused` and `_distinct_pick`; the operator's choice "matched photo only").
   - When it applies: in a Reel, holding the previous shot (or the opening, step 7) on a beat no picture shows
     would be refused by the gate. That means one of:
     - a picture shown again away from its own beat;
     - a hold over 6 s;
     - a picture on two beats in a pool that is not short. `hold_for_short_pool` does not join those, so the gate
       sees a repeat.
   - What it does: the beat keeps its own photo, but only if both hold:
     - the planner matched that photo to the beat's own line;
     - no other beat shows it.
   - What "matched" means: the planner's own record for the beat, `line_beat[photo] == beat`. This is the beat
     image fetched for that very line. main.py builds `line_beat` from the run's beat credits, and the fit check
     uses the same evidence to try a line's own photos first.
   - Otherwise the operator's 2026-10-29 rule stands: the shot is held (a picture held on is never a claim; a photo
     of something else under the words is), and the gate may stop the run after the voice.
   - A kept photo stays listed as a line no picture shows (fallback "kept", `report["kept"]`).
   - A short pool keeps its holds. 16:9 is unchanged.
3. **Comment:** `DETAIL_CROPS_PER_PHOTO = 1` cites the round-6 scope decision (decisions/QF-58.md, item 1), not the
   ruling.

The gate (`reel_photo_coverage`) is QF-58's, unchanged. Crops count toward neither side: a crop beat is neither a
distinct photo nor a photo beat. At most one crop per photograph.

### Item 2 was tried and withdrawn

Review 6's finding 2: with a graphic in the Reel, each crop lowers the distinct-photo requirement by one.

What was tried:
- Rounds 1-3 made crops never lower it. With graphics, the gate required min(9, photo beats + crop beats) distinct
  photos.
- Round 2 added a cascade guard (`_crop_keeps_count`) so the cascade would not plan a crop the gate then refuses.

Why it was withdrawn:
- Real Reels carry a graphic and 8-9 still beats, so the guarded beats had no crop. They needed an illustration, a
  matched photo, or the held shot, and the held shot is refused.
- Measured with the real illustration caps (below; queue/QF-58b.measure-caps.md), the rule raised stops after the
  voice against the parent in every mode short of a 100 % illustration pass rate. Per photo beat in turn, the
  guarded version (04029c66) stopped:

  | illustration | dev | production sample |
  |---|---|---|
  | off | 99 (parent 17) | 110 (parent 9) |
  | on, pass 0.5 | 57 (parent 10) | 65 (parent 5) |

- Without item 2, the same plans stop less often than on the parent in every mode.
- The cost stays where review 6 found it: a crop beat stands in for a photo beat. A Reel with a graphic and a crop
  shows one distinct photo fewer than it has still beats. No historic plan used a crop.

The coordinator withdrew item 2 on 2026-11-10 (decisions/QF-58b-question.md). The gate and the cascade's count
are QF-58's again.

## Measure

`measure.py <repo root> [<assets copy> <env name> [<reels file>]]` prints JSON and writes nothing.
- Without a copy, it runs generated gate and cascade cases.
- With a copy (development assets with photos, `dev`; a copy of the production sample, `production_sample`), it
  runs three things:
  - review 6's probe on every ready pool with at least ten stills;
  - the crop tier alone on the real Reel plans (QF-58/real_reels.json, or the `<reels file>` given);
  - the planner's own run on those plans (`fit_to_lines`, then `hold_for_short_pool`, then the gate). Each photo
    beat after the opening in turn is a line no picture shows, and every look is mocked. The illustration is either
    an uncapped stand-in or off.
- Files: `measurements_{parent,row}_{tracked,dev_files,production_sample}.json`, where parent = 1c4533d8.
- Production sample (decisions/PROD-SAMPLE-counts-only.md): QF-58/real_reels.json holds the development Reels only.
  The production plans are made by QF-58's `extract_reels.py` from a copy of the sample into a file outside the
  repository and passed as `<reels file>` (measure.py) or the fifth argument (caps.py). With any input other than the
  development data, measure.py prints counts only (no run, pool or crop source); `_production_sample.json` is that
  output.

`caps.py <repo root> <assets copy> <env name> <runs dir> [<reels file>]` is the same planner run with the real illustration caps.
It prints JSON and writes nothing in the repository.
- The caps: `illustration_allowed` (MAX_GAP_IMAGES_PER_RUN 2, IMAGE_GEN_MAX_RUNTIME_SHARE 0.15,
  IMAGE_GEN_MIN_SHOT_SECONDS 1.5) and IMAGE_GEN_MAX_CALLS_PER_RUN 8, at IMAGE_GEN_CANDIDATES 4 per attempt.
- Three modes: off (MEDIA_MONTHLY_BUDGET_USD 0, the setting today), and on at an assumed pass rate of 0.5 or 1.0.
  Whether an attempt passes is a fixed hash.
- Two scenarios:
  - "recorded": the beats the real run's own picture check found unshown (`picture_fit.unshown` in the run's
    script_metadata.json), all at once.
  - "each_beat": every photo beat in turn.
- `caps_table.py` prints the table below from `caps_{parent,row}_{dev,prod}.json`.

Data: one copy of the development assets and one of the production sample. The production sample's md5 is
identical before and after (312 files).

## Results

Generated cases: the gate is unchanged, and the same plans are admitted on both commits. The cascade changes as
follows (parent / row):

| case | parent | row |
|---|---|---|
| beat 2 after the opening, every photo in use, 4.5 s beats | crop of 01 (beside it), 9 s hold | crop of 02, no long hold |
| the same at 3 s beats (a 6 s join) | crop of 01, admitted | crop of 01, admitted |

Review 6's probe on every ready pool with ten stills (beats 2, 6 and 9 in turn), parent -> row:

| | development (11 pools) | production sample (9) |
|---|---|---|
| crop plans | 30 -> 29 | 27 -> 26 |
| beside their own photograph | 10 -> 0 | 9 -> 0 |
| stopped after the voice (hold over 6 s) | 10 -> 0 | 9 -> 0 |
| amber_room_heist / kailasa_temple_monolith beat 2 | crop of 01, 9 s, refused -> crop of 02, admitted | (counts only) |

The one plan fewer in development is voynich_manuscript beat 2. The parent cropped the opening photo 01 there
(a 9 s hold), and no other candidate gives a crop, so the beat goes to the next tier. The production sample also
has one plan fewer (counts only).

Real Reel plans on ready pools: dev 20 plans, 17 admitted as they are; production sample 27, 19. These counts are
equal on both commits.
- The crop tier alone, every photo beat in turn: crop plans 110 / 132 on both commits.
  - Admitted: dev 108 -> 110, production sample 131 -> 132.
  - Hold stops: 2 -> 0 and 1 -> 0.
- The planner's own run, every photo beat in turn (parent -> row):

| | development | production sample |
|---|---|---|
| uncapped stand-in illustration: stops after the voice | 2 -> 0 | 1 -> 0 |
| illustration off: filled by | crop 110, held 15 -> crop 110, held 13, matched photo kept 2 | crop 132, held 8 -> crop 132, held 3, matched photo kept 5 |
| illustration off: stops after the voice | 17 -> 13 | 9 -> 3 |
| a kept photo shown on another beat | 0 | 0 |

With the real illustration caps (caps.py), parent -> row:

#### recorded, dev

| variant | illustration | stops after the voice (by reason) | distinct photos per Reel min / median | Reels by crops | Reels by illustrated beats | unshown beats filled by |
|---|---|---|---|---|---|---|
| parent 1c4533d8 | off | 0 of 2 (hold 0, repeat 0, too few distinct 0) | 6 / 7.0 | 0: 1, 1: 1 | 0: 2 | detail_crop: 1 |
| parent 1c4533d8 | on, caps, pass 0.5 | 0 of 2 (hold 0, repeat 0, too few distinct 0) | 6 / 7.0 | 0: 1, 1: 1 | 0: 2 | detail_crop: 1 |
| parent 1c4533d8 | on, caps, pass 1.0 | 0 of 2 (hold 0, repeat 0, too few distinct 0) | 6 / 7.0 | 0: 1, 1: 1 | 0: 2 | detail_crop: 1 |
| this row | off | 0 of 2 (hold 0, repeat 0, too few distinct 0) | 6 / 7.0 | 0: 1, 1: 1 | 0: 2 | detail_crop: 1 |
| this row | on, caps, pass 0.5 | 0 of 2 (hold 0, repeat 0, too few distinct 0) | 6 / 7.0 | 0: 1, 1: 1 | 0: 2 | detail_crop: 1 |
| this row | on, caps, pass 1.0 | 0 of 2 (hold 0, repeat 0, too few distinct 0) | 6 / 7.0 | 0: 1, 1: 1 | 0: 2 | detail_crop: 1 |

#### recorded, production sample

| variant | illustration | stops after the voice (by reason) | distinct photos per Reel min / median | Reels by crops | Reels by illustrated beats | unshown beats filled by |
|---|---|---|---|---|---|---|
| parent 1c4533d8 | off | 0 of 11 (hold 0, repeat 0, too few distinct 0) | 5 / 8 | 0: 7, 1: 1, 2: 3 | 0: 11 | detail_crop: 7 |
| parent 1c4533d8 | on, caps, pass 0.5 | 0 of 11 (hold 0, repeat 0, too few distinct 0) | 5 / 8 | 0: 7, 1: 1, 2: 3 | 0: 11 | detail_crop: 7 |
| parent 1c4533d8 | on, caps, pass 1.0 | 0 of 11 (hold 0, repeat 0, too few distinct 0) | 5 / 8 | 0: 7, 1: 1, 2: 3 | 0: 11 | detail_crop: 7 |
| this row | off | 0 of 11 (hold 0, repeat 0, too few distinct 0) | 5 / 8 | 0: 7, 1: 1, 2: 3 | 0: 11 | detail_crop: 7 |
| this row | on, caps, pass 0.5 | 0 of 11 (hold 0, repeat 0, too few distinct 0) | 5 / 8 | 0: 7, 1: 1, 2: 3 | 0: 11 | detail_crop: 7 |
| this row | on, caps, pass 1.0 | 0 of 11 (hold 0, repeat 0, too few distinct 0) | 5 / 8 | 0: 7, 1: 1, 2: 3 | 0: 11 | detail_crop: 7 |

#### each_beat, dev

| variant | illustration | stops after the voice (by reason) | distinct photos per Reel min / median | Reels by crops | Reels by illustrated beats | unshown beats filled by |
|---|---|---|---|---|---|---|
| parent 1c4533d8 | off | 17 of 125 (hold 14, repeat 15, too few distinct 0) | 6 / 7 | 0: 15, 1: 110 | 0: 125 | detail_crop: 110, held: 15 |
| parent 1c4533d8 | on, caps, pass 0.5 | 10 of 125 (hold 9, repeat 8, too few distinct 0) | 6 / 7 | 0: 15, 1: 110 | 0: 118, 1: 7 | detail_crop: 110, reconstruction: 7, held: 8 |
| parent 1c4533d8 | on, caps, pass 1.0 | 2 of 125 (hold 2, repeat 0, too few distinct 0) | 6 / 7 | 0: 15, 1: 110 | 0: 110, 1: 15 | detail_crop: 110, reconstruction: 15 |
| this row | off | 13 of 125 (hold 10, repeat 13, too few distinct 0) | 6 / 7 | 0: 15, 1: 110 | 0: 125 | detail_crop: 110, held: 13, own matched photo kept: 2 |
| this row | on, caps, pass 0.5 | 8 of 125 (hold 7, repeat 8, too few distinct 0) | 6 / 7 | 0: 15, 1: 110 | 0: 118, 1: 7 | detail_crop: 110, reconstruction: 7, held: 8 |
| this row | on, caps, pass 1.0 | 0 of 125 (hold 0, repeat 0, too few distinct 0) | 6 / 7 | 0: 15, 1: 110 | 0: 110, 1: 15 | detail_crop: 110, reconstruction: 15 |

#### each_beat, production sample

| variant | illustration | stops after the voice (by reason) | distinct photos per Reel min / median | Reels by crops | Reels by illustrated beats | unshown beats filled by |
|---|---|---|---|---|---|---|
| parent 1c4533d8 | off | 9 of 140 (hold 7, repeat 8, too few distinct 0) | 6 / 8.0 | 0: 8, 1: 132 | 0: 140 | detail_crop: 132, held: 8 |
| parent 1c4533d8 | on, caps, pass 0.5 | 5 of 140 (hold 4, repeat 4, too few distinct 0) | 6 / 8.0 | 0: 8, 1: 132 | 0: 136, 1: 4 | detail_crop: 132, reconstruction: 4, held: 4 |
| parent 1c4533d8 | on, caps, pass 1.0 | 1 of 140 (hold 1, repeat 0, too few distinct 0) | 6 / 8.0 | 0: 8, 1: 132 | 0: 132, 1: 8 | detail_crop: 132, reconstruction: 8 |
| this row | off | 3 of 140 (hold 2, repeat 3, too few distinct 0) | 6 / 8.0 | 0: 8, 1: 132 | 0: 140 | detail_crop: 132, held: 3, own matched photo kept: 5 |
| this row | on, caps, pass 0.5 | 0 of 140 (hold 0, repeat 0, too few distinct 0) | 6 / 8.0 | 0: 8, 1: 132 | 0: 136, 1: 4 | detail_crop: 132, reconstruction: 4, own matched photo kept: 4 |
| this row | on, caps, pass 1.0 | 0 of 140 (hold 0, repeat 0, too few distinct 0) | 6 / 8.0 | 0: 8, 1: 132 | 0: 132, 1: 8 | detail_crop: 132, reconstruction: 8 |

Summary of stops after the voice, each_beat (parent -> row), dev: off 17 -> 13, on 0.5 10 -> 8, on 1.0 2 -> 0;
production sample: off 9 -> 3, on 0.5 5 -> 0, on 1.0 1 -> 0; recorded: 0 -> 0 everywhere. Distinct photos, crops
and illustrated beats per Reel are equal on both commits.

What this means, plainly:
- Item 1 removes every stop after the voice for a crop held beside its own photograph.
- The matched-photo step turns some refused held shots into the beat's own matched photo: dev 2 and production
  sample 5 per-beat cases with the illustration off.
- With the gate as QF-58 landed it, no variant raises stops after the voice against the parent, in any mode.
- What remains is the operator's 2026-10-29 rule: a held shot on a beat with neither a crop, an illustration nor a
  matched photo is refused.
- Distinct photos per Reel are the same on both commits.

## Tests

`tests/test_qf58b_crop_holds.py` has 12 tests. On the parent 8 fail and 4 pass. The 4 are guards: a 6 s join keeps
its crop of the photo beside it, 16:9 keeps its crop choice, a photo found for another line is held, and a pick
shown on another beat is never kept.

Cases:
- Item 1: the beat-2 crop after the opening at 4.5 s beats; the next beat's photograph and a crop of the photograph
  count for the join; no crop when every source is beside itself; `fit_to_lines` plans a crop with no long hold;
  16:9.
- Item 2 (matched photo), with the illustration off and no crop possible:
  - a refused hold gives way to the beat's own matched photo, nothing repeats, and after `hold_for_short_pool` the
    gate admits the plan;
  - a photo found for another beat's line is held (the 2026-10-29 rule);
  - a pick shown on another beat is held;
  - `_hold_refused` cases: 9 s; a 6 s hold in a pool that is not short; a cut-back to the opening; not a 6 s hold
    in a short pool; 16:9.
- Item 3: the crop cap's comment.

The item-2 (count rule) tests of rounds 1-3 left with the rule.

No existing test changed. The five tests that pin the 2026-10-29 rule pass unchanged:
- test_pictures_every_line_2026_10_29 (two tests)
- test_story_motion_colour_2026_10_29::test_with_neither_the_shot_is_held_as_before
- test_gap_engine::test_tier3_relic_macro_drift
- test_illustration_followups_2026_11_02::test_a_named_subject_never_takes_an_unmatched_graphic_after_the_map

## Known limits

- Review 6's finding 2 stands, by decision: in a Reel with a graphic, a crop beat lowers the distinct-photo count
  one for one (at most one crop per photograph).
- The what-ifs mock every look.
  - The illustration is a stand-in that passes at the assumed rate, and its fact-sheet descriptors are assumed.
  - The plans' graphics are stand-ins with no declared content, so the graphic filler cannot fill a beat.
  - "recorded" has few Reels (dev 2, production sample 11), so "each_beat" is the stress case.
- `_crop_hold_ok` and `_hold_refused` see the plan's durations at fit time. `hold_for_short_pool` and `fit_holds`
  may move cuts later. The gate stays the final check.
