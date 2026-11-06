# QF-53: deterministic photo-pool diversity

Every fetched photo receives a source SHA-256, pHash and dHash computed from its
actual upright pixels. A distance of at most 10 bits in either perceptual hash
is a duplicate. The existing pHash implementation is reused; dHash is computed
directly, so no extra imagehash dependency is needed.

Fetch admission limits each author to 35% of the target and each known shot
class to half the target. Metadata supplies provisional classes; the pixel
review supplies the final class. Approval requires at least three known classes,
no near duplicates and no author above 35% of the actual selected pool. Missing
authors form one group. Missing hashes hold approval. Replacing photo bytes
invalidates an automatic review fingerprint.

Old full bursts are reduced to a reusable selection so other sources can fill
the gaps. Exclusions remain as recorded reserves, with all downloaded files
retained. Human Keeps and explicit QF-52 essential exceptions are not silently
removed; incompatible protected sets stay held. Essential status does not waive
duplicate or author checks. Rebalancing preserves the QF-52 automatic crop floor
and its 10% low-headroom limit. Existing recorded human pool decisions retain
their existing treatment; new approvals check the current files.

## Reproduce

```text
python docs/reviews/quality-fixes-2026-11/QF-53/measure.py <repository-root>
```

The harness generates 36 distinct/source-duplicate texture fixtures and executes
the actual fetch and review paths with mocked search, download and model calls.
The mock model accepts every image, including duplicates. Three class labels are
fixture truth, not claims about real historical photographs. This is an offline
stress test of deterministic enforcement, not a photographic-merit audit.

| Metric after review | QF-52 parent | Candidate |
|---|---:|---:|
| Selected photos | 24 | 24 |
| Pairs within 10 bits | 10 | 0 |
| Minimum pair distance | 0 | 21 |
| Largest author share | 83.33% | 33.33% |
| Shot classes | 3 | 3 |
| Candidate class counts | — | wide 8, detail 9, interior 7 |
| Recorded reserves | 0 | 12 |
| Downloaded originals retained | 24 | 36 |

`before.json` is measured on QF-52 parent c8de0bd. `measurements.json` identifies
the measured candidate source commit. The measure generates all pixels locally
and requires no ignored images, weights, credentials or external service.

All 15 regression tests fail on the parent, including real old approval paths
for model-approved duplicates, fresh human approval and changed source bytes.
The integrated QF-52/QF-53, review/readiness, ranking and identifier run passes
105 tests in 24.85 seconds; the Telegram control suite passes another50 tests
in24.62 seconds after its approval fixtures received distinct photos/authors/classes.
Ruff has no new findings per changed file. Existing
test assertions remain unchanged; photo fixtures now contain real distinct
pixels, authors and shot classes. Independent review and the landing full suite
remain required.
