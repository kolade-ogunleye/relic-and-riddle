# QF-58: beat-driven readiness, shot-class fetching, re-fetch (saliency crops split to QF-97)

Spec (project-quality-review-2026-11-05.md, QF-58): beat-driven readiness (at least 9 distinct usable photos);
fetch until shot-class quotas are met; a 90-day re-fetch for pools under 12; saliency for detail crops.
Acceptance: no Reel with a repeat or a hold over 6 s; at least 9 distinct photos per Reel, as the operator's gate
ruling reads it (decisions/QF-58-gate.md, Option A: every photo shot a different photo, a Reel of photos only at
least 9, graphics keep their beats). Binding too: the findings of review 4 (queue/QF-58.review-4.md) and the main
test approvals (decisions/QF-58.md).

Round 6 ported only QF-58's own diff (round 5, fa7d8465 on ad650174) onto 1ab9aded (QF-57 on QF-56, QF-55,
QF-96, QF-54a, QF-53b) and answered review 4 on real data. Round 7 answers review 5 under the scope recorded in
decisions/QF-58.md ("Round 6 scope, 2026-11-10"): the gate as ruled, the saliency crop out of this row (open as
QF-97, queue/QF-97.proposed.md), and the refill decision before the voice.

## What the row does

1. **Readiness floor 9** (`MIN_SCENE_IMAGES`), distinct by the files' current hashes: `usable_photos` keeps one
   photo per source (bytes, file name, or a near duplicate by QF-53b's pHash + dHash rule). `pool_ready`,
   `not_ready_reason` and `get_scene_sequence` re-hash the files first (remembered per file size and mtime, so a
   dashboard tap or a run start does not decode every photo again). `required_photos(beats)` asks one photo per
   narration beat, never fewer than 9. The QF-57 rule stands: a rights-held photo is weak and never counts.
2. **Refill** (`asset_pipeline.ensure_pool`): an automatic pool with fewer than 12 usable photos is searched again
   when its last recorded search is 90 days old or none is recorded. Up to 3 fetch/review passes run while the pool
   is short or its shot classes miss the target (at least 3 verified classes, none over half; "unknown" is not a
   class), stopping at the first pass that adds nothing. Quotas are a fetch target only: a pool at the floor stays
   ready without them (ruling, item 4). New this round: every search is recorded (`pool_fetched_at`, also when it
   failed or timed out), and a pool at the floor searched within the period is not searched again for more beats
   or for quotas, so the run-start cost is at most one bounded refill per pool per 90 days. Human decisions stand.
3. **Plan gate** (`scene_planner.reel_photo_coverage`, Reels only, after fitting, holds and the caption re-plan,
   before motion and the render), as the operator ruled it (Option A): every photo beat shows a different photo
   (distinct == photo_beats; the same photograph on a second beat, next to the first or later, by lineage, bytes or
   near-duplicate hash, is a repeat; beats are joined into one only by `hold_for_short_pool`); a Reel of photos
   only (no graphic, film or illustration) shows at least 9 distinct photos; a photograph stays on screen at most
   6 s at a time. A detail crop counts toward neither side: it is never a distinct photo, so it cannot fill the
   floor or a photo beat; at most one crop per photograph in a Reel (`DETAIL_CROPS_PER_PHOTO = 1`: the ruling
   counts photographs, and a crop adds none); a photograph and its crops on consecutive beats are one hold for the
   6 s cap. The cascade (`_cascade_fill`) respects the same one-crop cap in a Reel; it did not check the 6 s hold
   beside the crop's own photograph (review 6, finding 1), which QF-58b adds (QF-58b/README.md). Round 6 admitted 1 photo + 8 crops and 18 s of one photograph through its crops (review 5); both
   are refused now and tested.
4. **Pause fit** (`scene_planner.fit_holds`, new): a beat lasts until the next one starts, so a pause can keep a
   photo past 6 s (production sample: 3 of 341 beats, up to 6.32 s). The cut moves into that pause (earlier at the
   end, else later at the start), never within 0.12 s of a word, and only while the shot on the other side stays
   within 6 s. Durations still add up to the narration. Moves are recorded in the run's `photo_coverage_gate`.
5. **Before the voice**: the pool is refilled for the photo beats the checked script will have
   (`scene_planner.estimated_photo_beats`: words / 9.6, the measured words per beat of the 61 real Reels, within two
   beats of `segment_beats` on every one of them, less one beat for a graphic when the script asks for one). The
   refill is one bounded search, not repeated within the refresh period. A Reel whose pool is no longer ready at
   the floor (photos removed since the run started) stops here with its checked script saved, before any voice.
6. **At the render**: with fewer distinct photos than photo beats (the narration's beats less the graphic beat),
   `ensure_pool` again (after the refill before the voice it only re-reads the pool, as the period guard holds), and
   the pool's sequence again when it gained photos, with the same selection as at run start (turned and
   text-bearing photos out, strong photos re-read for the Stories and carousel, the cover photograph by
   `hero_photo`). Photos added there are vision-reviewed by `review_pool` but do not pass the run start's
   `_upright_photos` step (the turned-photo filter uses the run-start set): a known, low-risk limit.
7. **Detail crops** keep the base's edge-density choice. The saliency change of rounds 1-6 did not prove better
   on real photos (review 5, blocking 2) and moved to QF-97 with its evidence (queue/QF-97-evidence/).
8. Not-ready automatic pools get the same 90-day guard: with a search recorded within the period, one search per
   `ensure_pool` call (as on the parent), not 3 passes. Without one: up to 3 passes while photos arrive.

## Measure

`python docs/reviews/quality-fixes-2026-11/QF-58/measure.py <repo root> [<assets copy> <env name> [<reels file>]]`
prints JSON and writes nothing. Without an assets copy it replays the tracked development credits with QF-53b's
committed photo signatures, and the 28 committed development Reel plans. With a copy of an environment's assets (the
development photos; a copy of the production sample) it reads that pool's files and joins that environment's Reels
to its pools, and also reports the cover photograph, the Stories/carousel photos and the detail crop of each ready
pool. `real_reels.json` comes from `extract_reels.py` and holds the development Reels only (counts, pool file names
and timings; no media, paths, text or database).

Production sample (decisions/PROD-SAMPLE-counts-only.md): nothing derived from it is committed. `extract_reels.py`
is run on a copy of the sample with its output redirected to a file outside the repository, and that file is the
`<reels file>` argument, with the assets copy and the env name `production_sample`. With any input other than the
repository's own development data, measure.py prints counts and medians only (no topic, run, file name or crop box).

Measured files (parent = 1ab9aded, the same code as the pushed 1a7d2759 for this measurement; row = this commit):
`measurements_{parent,row}_tracked.json` (no assets argument), `_dev_files.json` (a copy of the development assets
with their photos, `dev`), `_production_sample.json` (counts only: a copy of the production sample,
`production_sample`, and its Reel plans file kept outside the repository). The tracked and production files were
regenerated on 1a7d2759 when the production records left `real_reels.json`; every count is unchanged.

## Results

Generated fixtures (both commits):

| | parent | row |
|---|---|---|
| five-photo pool ready | yes | no (floor 9) |
| nine records of one source count | 9 | 1 |
| 9-photo pool, last search 91 days ago / none recorded: searches | 0 / 0 | 1 / 1 (grows to 12) |
| 9-photo pool searched 3 days ago, 11 beats: searches | n/a | 0 (ready at the floor) |
| plans admitted: valid / photos-only 5 / repeat / 6.1 s / same picture on two beats 3+3 s / 8 photos + graphic | all (no gate) | yes / no / no / no / no / yes |
| crop plans admitted: 1 photo + 8 crops / photo + 2 crops (18 s of one photograph) / 8 photos + a crop / 9 photos + a crop | all | no / no / no / yes |
| longest planned hold, repeated-shot fixture | 9 s | 6 s |
| 6.32 s photo before a graphic (pause after the words) | kept | cut moved, 6.0 s, admitted |
| 5-photo pool searched 3 days ago / no search recorded: searches | 0 / 0 (ready at floor 5) | 1 / 2 (passes stop when ready) |

Real pools (measure.py on copies, parent -> row):

| | development (31 pools) | production sample (16 pools) |
|---|---|---|
| ready | 17 -> 15 | 14 -> 12 |
| no longer ready | nazca_lines_runway_paradox (6 usable), wieliczka_salt_mine (7) | 2 pools |
| ready with 9-11 usable (re-fetched at the next run for 10-11-beat scripts, once per 90 days) | 8 | 5 |
| ready and refresh due on the first run after landing (no search recorded) | 0 -> 7 | 0 -> 5 |
| automatic pools with a refresh due (each one bounded search, then recorded) | 0 -> 23 | 0 -> 7 |
| ready pools meeting the shot-class target | n/a -> 1 | n/a -> 1 |

Not ready reasons are in `pools.by_topic.*.not_ready` (for example "not approved: ... 6 of 9 usable"). The
shot-class target is met by 1 ready pool in each environment: the classes are mostly provisional from metadata,
so every refresh-due pool that enters a refill makes up to 3 passes while new photos arrive. Worst case added at a
run start: 3 x FETCH_TIMEOUT (1,800 s) plus reviews, once per pool per 90 days; typically one search that adds
nothing and stops. A pool that is not ready (Nazca, Wieliczka, Greek fire and Tunguska on dev; 4 pools on the
production sample) is searched at every run start it ranks ahead and in the nightly top-3 check, as on the
parent: one search per call (up to 1,800 s plus a review) once a search is recorded within the period, up to 3
passes only on the first call without one.

Real Reels (61 final plans, 28 development + 33 production sample, the latter as counts only; one production
plan has fewer scenes than beats and is left out of the hold check):

| | development | production sample |
|---|---|---|
| plans showing a photo again (non-consecutive) | 5 | 5 |
| plans holding one photo over 6 s | 4 | 8 |
| of which only a pause (fixed by fit_holds) | 0 | 2 |
| admitted by the gate as planned / after fit_holds | 21 / 21 of 28 | 21 / 23 of 32 |
| Reels whose topic's pool is no longer ready (stop before the script, no spend) | 8 (Nazca 5, Wieliczka, Greek fire, Tunguska) | 5 |
| ready-pool Reels the gate would refuse as planned | 1 (Hal Saflieni, a repeat) | 6 (4 one photo held over consecutive beats, 2 a photo shown again after another picture) |
| of those, pool now has a usable photo per photo shot | 1 of 1 | 6 of 6 |
| median seconds from the run start to the plan (script, voice, bed, images, sourcing) | 696 | 840 |

What this means: every refused ready-pool plan came from the planner's fallback (a shot held over a line no
picture fitted, or the opening photo shown again), not from a short pool. 57 of these 61 runs started before the
fallback cascade landed (2026-11-02), which now tries a detail crop, archive film, the map or a graphic before
holding; the historic plans therefore overstate how often the gate will stop a current run. One refused production plan is
newer than the cascade (a photo shown again). A stop at the gate
comes after the script and the voice (about 12-14 minutes in, by the median above); the run is marked failed with
the reason, and the licensed voiceover already made for that script is kept (`voice_reusable`, the run's
checkpoints) when the failed run is resumed (`main.py resume`, the card's Re-render) after a photo is added; a
re-queued topic starts a new run with a new script. Money spent per stopped run cannot be measured from the records (no cost fields in
the run folders); round 5's "$0.50-0.75" is not a measured figure and is withdrawn.

Covers, Stories and carousels (each ready pool through `get_scene_sequence`, `hero_image`, `only_strong`):

- Cover photograph: unchanged on every pool ready under both (15 development; production sample 12 of 12, by a
  per-pool comparison made outside the repository; committed as counts).
- Stories/carousel photos, development: unchanged except Richat (19 -> 18): `03_commons` and `04_commons` are two
  Sentinel-2 crops of the same scene (File:Richat Structure S2A 432 crop 10 / S2A 1282 crop 10), a near duplicate
  by QF-53b's rule; the second leaves. Production sample: 1 of 12 ready pools loses one photo, the others are
  unchanged (same outside comparison; totals 157 -> 147 over 14 -> 12 ready pools).
- The detail crop does not feed these surfaces: it is made in the scene planner for a Reel beat
  (`_cascade_fill`), after the cover and the Stories/carousel stills are chosen from the pool; the cover's own
  saliency (`visual_engine.calm_center_y`, title placement) is untouched by this row. There is therefore no
  before/after crop to show on a cover, Story or carousel slide.

Detail crops (9:16 at the cascade's 600 px minimum, the first two usable photos of each ready Reel topic): the
boxes equal the parent's on every photo (22 development, 24 production sample), since the saliency change left the row. Round 6's sheets and
the builder's and reviewer's judgement (better 2, worse 3, equal 3, a printed caption pulled into one crop) are
QF-97's starting evidence.

## Tests

`tests/test_qf58_photo_readiness.py`: 25 tests, all fail on the parent (25 failed) and pass on the row. Gate cases: the same
picture on two beats is refused (distinct 9 < photo beats 10) and a cut-back is a repeat; 1 photo + 8 crops,
8 photos + a crop and two crops of one photo are refused, 9 photos + a crop admitted; a photo and two crops for
18 s is one 18 s hold (refused), photo + crop 3 + 3 s admitted and 3 + 3.5 s refused; the 6.32 s pause case moves
its cut and words past the limit do not; the photo-beat estimate before the voice; a pool at the floor searched
this period is not searched again for 11 beats; a pool with no recorded search is searched once and recorded;
"unknown" classes never meet the target. Round 6's two saliency tests left with the change.

origin/main tests: test_image_review, test_credits, test_orchestrator, test_control change exactly the operator-
approved expected values (decisions/QF-58.md, QF-58-proposed-main-tests.patch and
QF-58-control-proposed-main-tests.patch; `test-assertion-audit.json`); test_asset_pipeline changes fixture inputs
and stubs only. These files are byte-identical to round 5 (their parent copies did not change since).
tests/conftest.py (fixture): the `ensure_pool` stub accepts the new `beats` argument.

Branch-only changes: test_photo_readiness_2026_10_28 ("of 9 usable", nine-photo approval, recorded fetch time),
test_shot_pool_2026_10_28 (hold fixtures within 6 s; Nazca refuses a 6.4 s merge), test_plan_review_2026_10_28
and test_carousel_waits_for_graphics_2026_11_02 (the gate stubbed in four-beat/one-beat fixtures), all as
round 5; test_qf53_pool_diversity: `_review_fixture` takes a count (6 by default, so QF-53b's five-reserve
duplicate assertion stands; 9 for the approval in test_changed_pixels_trigger_a_fresh_review). No skip, xfail or
loosened threshold.
