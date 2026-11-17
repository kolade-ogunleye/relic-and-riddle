# QF-07 Ducking

Review: `docs/reviews/project-quality-review-2026-11-05.md`, section 2, row 9. The ambient bed's duck pumped between
words: `sidechaincompress` with a 220 ms release, no hold and the full-band voice as its key let the bed swell back
up inside every short gap between words.

**Metric (acceptance: <= 2 dB, with a band-limited key):** the median bed recovery inside 0.1-0.5 s word gaps.

| | before (start commit) | after |
|---|---|---|
| median recovery in 0.1-0.5 s word gaps (generated inputs, the acceptance) | **7.25 dB** | **0.08 dB** |
| p90 recovery (generated) | 7.52 dB | 0.30 dB |
| median recovery (each run's recorded voiceover and ambient bed) | 3.98 dB | 1.03 dB |
| p90 recovery (recorded) | 6.33 dB | 2.29 dB |
| key | full band (the voice chain's own output) | 200 Hz-4 kHz, held 0.5 s |
| median duck under speech (generated / recorded) | -7.04 / -5.78 dB | -7.06 / -7.01 dB |
| bed back within 1 dB after the voice (max, generated / recorded) | 0.10 / 0.00 s | 0.72 / 0.59 s |
| bed level at the end of the Reel's loop bridge (min / max over the runs) | 0.0 / 0.0 dB | 0.0 / 0.0 dB |

76 gaps of 0.1-0.5 s over the 8 fixture runs. Per run and per gap: `before_after.txt`, `before.json`,
`after.json` (= `measurements.json`), `before_real_audio.json`, `after_real_audio.json`.

## What changed (`src/video_composer.py`)

- The sidechain key is no longer the voice itself. `_duck_key` builds it from the voice's speech band (high-pass
  200 Hz, low-pass 4 kHz, mono), as an RMS envelope (the band squared and low-passed at 12 Hz). It holds each word
  for 0.5 s: the key is the sample-wise max of the envelope and its copies delayed in 50 ms steps up to 0.5 s. The
  key is levelled where it is speech (a `compand` that maps every envelope from -45 dBFS up to -15 dBFS), so a quiet
  word ducks the bed as deep as a loud one. The hold and levelling run at 1 kHz, a rate the 12 Hz envelope allows,
  and the key is then resampled back to 48 kHz for the detector. None of the key reaches the mix.
- `sidechaincompress`: threshold 0.08 -> 0.06, ratio 7 -> 4, release 220 -> 400 ms, attack 15 ms (unchanged). The
  threshold puts the median duck under speech where it was (7 dB).
- The hold ends with the voice. The voice's pad (the end card or loop bridge) now comes after the key's split. That
  pad is applied to the voice before its last equaliser and to the key after its hold, so the loop bridge is the bed
  alone and the bed comes back up as the narration ends, not 0.5 s later. The voice's own filters and their order
  are unchanged. The graph still has the labels `asplit[v_main][v_sc]`, `[bg_carved]` and the exact-cut padding
  that the loop-seam tests re-frame.
- The parameters are named module constants (`DUCK_*`) with the reasons given beside them. They are mixing
  constants, not operator settings, like the rest of the chain.

### Where this departs from the review's suggested fix, and why (measured)

These figures come from parameter sweeps during development. Some were taken on earlier versions of the key
before its final form; each is marked where that applies.

- **Hold 0.5 s, not 0.3 s.** Most word gaps in the fixture runs are about 0.4 s. With a 0.3 s hold (earlier key), the
  median recovery was still 3.5 dB. With 0.5 s, the hold covers the acceptance's whole 0.1-0.5 s gap range.
- **Release 400 ms, not about 700 ms.** The review's first option was a release of about 700 ms and ratio 4 with no
  hold. On its own it measured 6.6 dB. A 4000 ms release was needed to reach 1.3 dB, and the bed then ended the
  loop bridge 1.35 dB down, a level step at the loop seam. With the hold (earlier key, which ran the hold on
  past the voice), a 700 ms release still left the bed 0.76 dB down at the bridge's end on the recorded audio. At 400 ms it is back to 0.0 dB on every run. ffmpeg's
  `release` is about four time constants (coefficient `4000 / (ms * rate)`), so 400 ms means a time constant of
  about 100 ms.
- **Envelope before hold, levelling after.** A hold on the raw rectified voice keeps only the instantaneous samples
  at its tap offsets. That is a comb, not a hold. A levelling `compand` placed before the hold adds onset spikes,
  which the taps then repeat. Both versions were measured and rejected: the duck released partway through a
  falling word.

### Cost

On the longest fixture narration (190 s), the audio graph alone took about 14-16 s to render before the change and
17-19 s after, measured while other agents shared the machine. That is about 3.5 s more for a programme of that
length. Before the envelope was decimated to 1 kHz, a full-rate 11-channel hold took 29 s.

## How it is measured (`measure.py <repo root>`)

`measure.py` runs `<repo root>/src`'s own `_audio_chain` graph through ffmpeg. It uses the fixture runs' words
(`docs/reviews/freeze-panel-2026-11-04/fixtures/runs/*/words.json`) with:

- **generated mode (default, the acceptance):** a narration stand-in generated from each run's word timings (a
  formant-shaped voiced signal at -20.3 LUFS) and a **labelled bed stand-in** (a steady minor chord pad with a
  little noise). No library bed is committed, so these stand in for one. Both are deterministic.
- **recorded mode (`RR_FIXTURE_REPO=<checkout with output/>`):** each run's own `voiceover.mp3` and
  `ambient_bed.wav`. Every row records which mode produced it.

The ducked bed is isolated from the graph's own output with the graph unchanged. The mix with the bed, minus the
mix with the bed silenced, is the ducked bed (the voice branch is identical in both). Its energy per 10 ms block,
against the mix with the voice silenced (the bed undocked), is the duck's gain. For each 0.1-0.5 s gap between two
words, `recovery_db` is the highest gain inside the gap minus the median gain under the word before it.

Re-run: `python docs/reviews/quality-fixes-2026-11/QF-07/measure.py .` (about a minute). For the before figures,
use a worktree of the start commit as the root: `RR_MEASURE_OUT=<file> python <this measure.py> <start worktree>`.

## Regression test

`tests/test_qf07_ducking.py` runs the composer's chain through ffmpeg on inputs it makes itself: voiced 0.5 s words
with a quiet second syllable, separated by 0.15-0.45 s gaps, and a steady chord bed. It asserts that:

- the median recovery in the gaps is <= 2 dB, and no gap exceeds 3 dB;
- the duck holds one depth under quiet and loud syllables (10-90 % spread <= 3 dB);
- the bed is back within 1 dB by the end of the loop bridge and undocked before the first word;
- the key is band-limited (200 Hz / 4 kHz) and drives `sidechaincompress`.

On the start commit, 3 tests fail: a median recovery of 4.7 dB, a depth spread of 6.6 dB and no band-limited key.
After the fix, all 3 pass.

## Rebased validation

Current acceptance uses the generated fixtures only. Recorded-audio JSONs and
comparison text are historical results from the original build; their ignored
recordings are not needed for the default measurement or current acceptance.
The saved regression's ffmpeg skip marker was removed: ffmpeg is required, and
its absence must fail validation. Existing tests and expected values are unchanged.
Rebased regression, whole-file determinism, encoded-audio and loop/exact-end
coverage: 59 passed in 155.33 s. Fresh generated metrics: median recovery 0.08 dB,
p90 0.30 dB, band 200-4000 Hz, hold 0.5 s, end-of-bridge gain 0.0 dB on all runs.

Fresh immediate-parent f1d805d measurements reproduce 7.25 dB median recovery;
all three regression tests fail on that parent (48.43 s). before.json and
after.json now hold the current generated runs. No existing expectation changed.
