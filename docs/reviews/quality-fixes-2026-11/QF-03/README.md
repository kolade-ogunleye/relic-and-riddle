# QF-03: remove delivery grain and make repeated masters deterministic

The delivery graph and graphics contrast measurement no longer add noise. There
was no corresponding operator setting or `.env.example` entry to remove.

## Acceptance decision (operator, 2026-11-06)

The >=50% bitrate reduction is measured against the original review commit
`581a866`, across all four samples: generated stills and the two Wieliczka photos,
each at 9:16 and 16:9. QF-02 already reduces grain strength by moving the graph
into 16-bit RGB, so its immediate-parent bitrate is not the approved denominator.
Whole-file determinism remains required; video-only equality is insufficient.

## Reproduction

Run `python docs/reviews/quality-fixes-2026-11/QF-03/measure.py <repo root>`.
The root is its only argument. It uses generated narration/stills and the two
committed small JPEGs in `fixtures/`, whose source/attribution/licences are there.
No output directory, ignored photo, provider, network, or environment override
is required. It encodes each sample twice using libx264 slow CRF 18 and records
whole-file hashes, audio/video packet hashes, differing MP4 boxes, noise presence,
and bitrate. The flat-frame check measures temporal noise separately.

Run this candidate's measure script against a clean worktree of `581a866` to
reproduce `baseline-repro.json`. Run it against the candidate to reproduce
`measurements.json`. The baseline and candidate must have matching fixture hashes;
a missing baseline, fewer than four comparisons, or any sample under 50% fails
acceptance. The JPEGs are resized/recompressed copies of the predecessor's photos
(to respect the 300 KB evidence cap), so both sides have been remeasured on those
identical copies. Historical full-resolution numbers cannot be mixed with these.

## Audio determinism repair

The saved grain-removal build compared only the video stream, because concurrent
renders sometimes differed in audio. Its loudness calibration used a finite-input
sidechain: whichever input ended first could affect the mix and the calibrated
gain. Loop-bridge delivery already had sample-bounded mixing; calibration and
ordinary delivery did not consistently use it.

`calibrate_gain` and `_calibrate_gain` now accept the programme endpoint, include
it in the cache key, and build the same padded, sample-trimmed audio chain as
delivery. `compose_video` supplies narration plus any loop tail on every pass and
on every master. No EOF race decides the programme length. Existing public calls
without an endpoint retain their prior behaviour. The limiter, gain target,
ducking parameters and sound design are unchanged.

The regression compares the entire file, includes three independent concurrent
composers, and checks that calibration does not reuse a gain for another endpoint.
No origin/main expectation changes; the branch-only source assertion is documented below. Earlier snapshot JSON/TXT files retained
in this directory describe the saved predecessor build; `baseline-repro.json`
and `measurements.json` are the current four-sample acceptance evidence.

## Validation

Focused run: 18 passed; added concurrent-render test: 1 passed, all three complete
files identical. Final covering tests, fresh four-sample measurements and independent
review are required before landing.
## Current measurements

| Sample | Baseline kb/s | Candidate kb/s | Reduction |
|---|---|---|---|
| generated/reel | 22157.5 | 2392.1 | 89.2% |
| generated/deep_dive | 22718.6 | 9065.4 | 60.1% |
| photos/reel | 20335.3 | 2576.7 | 87.3% |
| photos/deep_dive | 22707.7 | 3400.9 | 85.0% |

Every sample has identical complete files across its two renders. All acceptance fields are true.
The separate three-way concurrent-render regression also passed.

## Branch-only expectation adjustment

`tests/test_loop_seam_exact_end_2026_11_04.py` is absent from origin/main (verified
with git cat-file). Its source assertion previously required the literal conditional
`end=vo_dur + card_seconds if card else None`. The QF-03 whole-file determinism
requirement now needs an endpoint for non-loop masters and calibration as well.
It now requires the unconditional endpoint and rejects the former conditional.
The sample-count, pad and fade assertions and all measured tolerances are unchanged.
First covering run: 69 passed, this one obsolete source assertion failed; rerun required.
