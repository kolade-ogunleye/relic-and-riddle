# QF-53b: Backfilling Vision Shot Classes for Existing Photo Pools

Written by Antigravity (builder), 2026-11-08, for operator decision.

## Context
Landed `QF-53` (`d453b89`) introduced deterministic photo diversity checks (`photo_pool.assess()`)
requiring three distinct shot classes (`wide`, `interior`, `detail`, `people-scale`, `archival`)
and capping any single shot class at half the pool. However, across the 27 real topic pools
(`assets/*/credits.json`), 0 carry a vision `shot_class`, because existing pools were reviewed
before shot class extraction was introduced. Under `d453b89`, this caused 27 pools to fail with
"Fewer than three verified shot classes" and 23 pools to fail on half-pool quota ("unknown" count > 50%).

In `QF-53b`, missing or "unknown" shot classes are treated as "unverified": reported in
`diversity["unverified_shot_classes"]`, but never refused on. This immediately unblocks operator
approval and restores existing video-ready pools.

## Quota Impact of Backfilling
Populating verified vision `shot_class` attributes across the existing repository would require
running a forced vision review (`image_review.review_pool(topic, topic_dir, force=True)`) on each topic:
- 27 topics containing ~350 total images.
- Each image sends full visual context to the vision model (`free_llm.call` with `PoolReview` schema).
- Running this backfill en masse would consume significant free vision quota (Gemini 2.5 Flash / Flash-Lite),
  risking 429 rate limits or exhausting the quota needed for unattended pipeline runs.

## Question for the Operator
How should verified shot classes be backfilled for existing topic pools?
1. **No forced backfill (Recommended)**: Leave existing pools with unverified shot classes. As new
   photos are fetched or pools are naturally refilled/re-reviewed, shot classes will populate organically.
2. **Gradual scheduled backfill**: Backfill 1–2 topics per day during off-peak hours until complete.
3. **One-shot forced backfill**: Execute immediate forced re-review across all topics, accepting the quota burn.

answer: superseded (see below)

answer: delegated by the operator on 2026-11-09 ("I'll follow your recommendation as long as it meets the project's highest-quality standard"); chosen by Claude: option 2, gradual backfill, with conditions. Verified shot classes are what the diversity rule
needs, so waiting for refills alone leaves most pools unverified for months. Conditions: (a) only after QF-53b passes
review, so that a forced re-review can no longer drop a ready pool (review 1 item 6: 17 ready -> 1); (b) development
pools only, free vision tier only, at most 2 topics per day, off-peak, never while a pipeline run holds run_lock, stop
for the day on the first 429; (c) every backfilled pool is compared before/after (ready state, approved set) and a
pool that would lose readiness is reported, not changed; (d) production pools populate through their normal reviews,
nothing is copied from development. No paid model, no cap change.
