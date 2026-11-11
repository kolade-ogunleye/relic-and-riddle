# QF-53b: the QF-53 diversity checks on the real pools

QF-53 (d453b89) made `/photos approve` refuse all 27 real pools with photos, and an automatic review held 16 of the
17 video-ready pools. This row keeps QF-53's checks and fixes how they read real records. The spec is
`JOB-QF53-regression` (items 1-6), reviews 1 and 2 of this row, and the operator's decision on the author cap
(decisions/QF-53b-author-cap.md, 2026-11-09): the author and shot-class quotas govern what the fetcher adds; a
pool's existing photos are never moved out for them by automatic review or refill, and the pool's share is reported.

Run: `python docs/reviews/quality-fixes-2026-11/QF-53b/measure.py <repo root> [<outside pairs file>]`. It prints
JSON and writes nothing. Point it at another commit's worktree to measure that commit's code on the same pools. Its
inputs are committed:
- `real_pairs.json`: every within-pool pair of the development pools with a pHash + dHash distance of 25 or less
  (17 of 1,837 pairs), with hashes only and a label made from side-by-side contact sheets of the files.
- The production sample's labelled pairs are not committed. They are kept in a pairs file of the same format outside
  the repository; passed as `<outside pairs file>`, they are scored as counts only (no pair names).
- `real_signatures.json`: `signature()` of the 278 development pool photos, as hashes only. The replay uses them in
  place of the photos, which are not in the repository.
- the tracked `assets/*/credits.json`, with their recorded vision verdicts (the model is mocked, so no call is made).

It also replays the fetcher's refill path with every search answering nothing (the essential-image check and the
storyboard stubbed, because the placeholders hold no pixels), and reports the photos each pool keeps on every path.

## What changed

1. Unnamed attribution ("Unknown", "Unknown author", "Anonymous, <nationality>, <century>", missing) is unverified:
   it is reported as `unverified_authors` and never counted as one shared author. The 35% share is counted over
   the whole pool, for named authors only. Below three photos no pool can keep each author at 35% or less, so the
   fetch quota starts at three. Round 1's `has_authors` switch is removed.
2. Quotas apply to what the fetcher adds (operator decision): a refill keeps every one of the pool's own photos and
   offers reserves back only within the quotas (`fetch_subset(..., retained=)`); review and the end of a fetch
   remove only near-duplicates (`deduplicated()`, which never reserves a protected photo). `balanced()`, which
   trimmed pools to a quota subset, is removed. `assess()` now separates `holds` (near-duplicates, missing hashes,
   the crop budget: an automatic approval waits) from notices (author share, shot-class balance: reported on the
   review note and the approve message).
3. Duplicates: the pHash + dHash distance must be 15 or less. Calibrated on the labelled pairs (table below).
4. Flat frames (no structure: the dHash is all zeros and the pHash is rounding noise) are duplicates only when
   their bytes are identical. No real pool photo is flat (0 of 278). The stored hash values are unchanged
   (round 1's SHA-256 slices are removed).
5. An undecodable download is recorded with `hash_error`, as `refresh()` already does, so `assess()` reports it
   ("Photos lack verified perceptual hashes"). Round 1 swallowed the error.
6. Kept from round 1, as reviewed: approve_set honours the operator and appends a "Diversity notice"; the
   unchanged-pool path keeps its stored verdict; the legacy comma fingerprint counts as unchanged when every file
   still hashes; the crop budget ignores legacy records with no crop width.

## Duplicate rules on the 17 labelled development pairs

| Rule | Duplicates caught (of 8) | Distinct objects flagged (of 7) | Variants flagged (of 2) |
|---|---|---|---|
| QF-53 landed: either hash <= 10 | 8 | 6 (the Met swords, Lycurgus lit/unlit, Voynich folios) | 1 |
| round 1: pHash <= 6, or both <= 10 | 7 | 1 (Met swords 12/14) | 0 |
| this row: pHash + dHash <= 15 | 7 | 0 | 0 |

Production sample (counts only, from the outside pairs file): 20 labelled pairs, 11 duplicates, 7 distinct, 2
variants. QF-53 landed: 11 caught, 6 distinct and 1 variant flagged; round 1: 10 caught, 1 distinct flagged; this
row: 10 caught, 0 flagged.

The duplicate missed by this row is a limit of the hashes, not of the threshold. Roman concrete 22/27 (two scans of
one Pantheon engraving) sits at 21, and the one duplicate missed in the production sample is likewise beyond the
rule. Different objects sit at 17, 20 and 22 (the Met swords and the Lycurgus cup), so no hash threshold separates
them. The "variants" are Richat satellite scenes in different processing. A pixel-embedding comparison (the
pinned local CLIP model approved for QF-59) is the follow-up that can tell these apart.

## Real pools, development (measure.py on each commit)

| | pre-QF-53 4792d55 | QF-53 d453b89 | round 1 5977ee2 | round 2 31ea1d4 | this row |
|---|---|---|---|---|---|
| video-ready as stored | 17 | 17 | 17 | 17 | 17 |
| ready after an unforced review | 17 | 1 | 5 | 13 | 17 |
| ready after a forced re-review | 17 | 1 | 2 | 11 | 17 |
| ready after a refill that finds nothing | 17 | 17 | 11 | 14 | 17 |
| photos in the ready pools: stored / after a forced review | 231 / 231 | 231 / 231 | 231 / 231 | 231 / 188 | 231 / 230 |
| photos moved to reserve by that refill (all pools) | 0 | 45 | 122 | 73 | 6 |
| pools re-sent to the model unforced | 18 | 26 | 18 | 18 | 18 |
| /photos approve accepted (refused: fewer than 5 photos) | 20 (7) | 0 (27) | 20 (7) | 20 (7) | 20 (7) |

"Ready after" counts the one operator-approved ready pool, which review never touches (`operator decision stands`).
The one photo this row removes is the labelled duplicate Richat 03/04 (one satellite scene, natural and false
colour). Derinkuyu, not stored ready, keeps 11 of 20 photos after review on every commit including pre-QF-53: the
photos without a recorded verdict are dropped by the replay, not by this rule.

The 6 photos this row's refill moves are all labelled near duplicates (Derinkuyu 23, 29 and 31 and two
byte-identical uploads; Richat 04); none is moved for an author or shot-class quota, and the pool's own photos are
otherwise kept by construction (`retained`). tests/test_qf53b_real_pools.py::
test_the_fetchers_refill_on_the_real_pools_moves_only_their_near_duplicates runs the fetcher's own refill
(fetch_topic, searches stubbed to answer nothing) on copies of every real pool with the recorded hashes: no
hash_error, only near duplicates leave, 6 in all. With the refill's `retained` set back to 0 it fails on an
author-quota move (Damascus steel 18), and on d453b89 likewise (Derinkuyu 28).

Correction (review 3): round 3's first measure.py gave the refill no hashes. Its signature lookup took the topic from
the copy's directory name after a prefix, and the refill copy has none, so every photo got `hash_error` and no
duplicate was found (it reported 40 / 40 / 23 / 0 moved). The lookup now maps each copy to its real pool and the
output counts `signature_misses` (0 on every commit). Review 2's 73 moved and 14 ready for round 2 were right.

Author share is now a notice: 21 of the 27 pools with photos have a named author above 35% (Damascus steel 100% The
Met, Gol Gumbaz 100%, Odessa 92%, ...). They stay approved; the share shows on the review note and the approve
message, and new photos fetched for them must fit the quota.

## Backfill

Shot classes are not backfilled by this row. The operator's answer (decisions/QF-53b-backfill.md, delegated
2026-11-09) is a gradual backfill after this row passes review, development pools only.
