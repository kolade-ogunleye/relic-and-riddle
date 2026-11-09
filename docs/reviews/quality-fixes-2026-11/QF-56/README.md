# QF-56: technical merit and original depictions

Every new vision verdict requires quality 1-5 and a depiction class: original,
reconstruction, replica, museum display, or drawing. Reviews send at most twelve
photos per call with a 768 px long edge (native smaller images stay smaller), and
process every pool entry instead of truncating the pool at 24. The refill target
remains 24. A partial or unavailable review holds an automatic pool and retains
its complete records; operator decisions still stand.

Usable originals rank ahead of reconstructions within each topic/kept/context
tier, then by quality and relevance. A photograph of an original in a museum is
still original; a reconstruction cannot inherit that status from its location.
Perceptual hashes check duplicates across review batches, including the unchecked
photo screening path. Existing automatic reviews lacking these fields are
refreshed when reviewed again; historical human judgments are not fabricated or
overwritten. Unreviewed records remain unchecked until a model can assess them.

Before visual review, `photo_quality` measures edge sharpness, clipped highlights,
blocked shadows, grey-world channel imbalance, and JPEG quantization. Downloads
save those measurements from the original bytes before the metadata-stripping
JPEG conversion, in a sidecar bound to the resulting pool bytes. Replacing those
bytes invalidates the sidecar. Legacy files explicitly report that their original
encoding is unavailable. These are diagnostic flags, not universal rejection
thresholds: a bright snowfield or warm stone is not automatically a bad photo.
The model must inspect watermarks, HDR halos, artifacts and actual usability from
the image as well. No new dependency or live provider call was used in validation.

Run `python docs/reviews/quality-fixes-2026-11/QF-56/measure.py <repo root>`.
It generates 25 photos through the committed regression fixture and mocks visual
judgment while exercising production batching, source measurements, persistence,
and ranking. Copy that test and the measure to the parent to reproduce before.

| Metric | Parent d5509fa | Candidate |
|---|---:|---:|
| Images per request | 24 | 12, 12, 1 |
| Review long edge | 512 px | 768 px |
| Pool records retained | 24/25 | 25/25 |
| Records with quality and depiction | 0 | 25 |
| Records with technical checks | 0 | 25 |
| Preferred topic photo (same two verdicts) | reconstruction | original |

This measures implementation behavior with generated inputs and mocked judgments;
it does not establish a model's accuracy on real artifacts. Separate regressions
check blur/clipping/cast/compression sensitivity, original quantization evidence,
stale sidecars, required schema fields, cross-batch duplicates and partial-review
failure. All nine fail on the parent. A 206-test covering run passed; after adding
the explicit hold for an existing automatic approval, 68 focused tests passed.
Existing expected assertions are unchanged: model fixture inputs gain the required
fields and return the requested number of verdicts. The QF-53 harness mock follows
the batch contents. The full suite runs at landing.

## Rebased onto QF-53b, QF-54a and QF-55 (2026-11-10)

QF-53b landed between this row's first review and its landing. Its review 3 found that
the cross-batch hash check overrode QF-53b's duplicate rule: `_verdicts` marked any
photo repeating one of an earlier batch as its duplicate, and `review_pool` then
rejected it for good (`rejected_by_review`, never fetched again). On the real
Derinkuyu pool that rejects the two well-lit photos (29, 31) the model chose over
their darker twins (27, 22), because order, not merit, decided. Now:
- `review_pool` asks for verdicts without the cross-batch marking. The whole kept
  pool, sorted by merit, goes through QF-53b's `photo_pool.deduplicated`, which
  already spans batches, reserves the duplicate (`diversity_reserve`, "near
  duplicate") and never reserves a protected photo (operator keep or essential full
  image).
- `screen_unchecked` keeps the cross-batch marking (it drops nothing; the tier
  decides use), but compares only with earlier photos the model accepted, and never
  marks a protected photo.
- Replaying Derinkuyu's committed records and hashes (no model): 29 and 31 are kept;
  on the first rebase all four twins left the pool.
- Five new regression cases (the better later twin, an essential later twin, the twin
  of a refused photo, and both screening cases) fail on the first rebase (0606069).

QF-53b's committed `measure.py` mocked `PhotoVerdict` without the fields QF-56
requires and crashed. Its recorded-verdict mock now gives a verdict without them
quality 3 / original, as QF-56 did for the QF-53 harness. On this commit its only
change from the parent is `unforced_model_calls` 18 -> 26 for one cycle: the eight
pools whose fingerprint still matches (Flannan Isles, Gol Gumbaz, Great Wall of
Gorgan, Odessa, Piri Reis, Colossi of Memnon, Heracleion, Zhang Heng) are sent once
more to get quality and depiction. Ready, kept, approve and refill results are
unchanged.
