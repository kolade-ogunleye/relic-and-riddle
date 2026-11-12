# QF-11 Audio QA: two-pass mastering, a full ebur128 decode and a phone check (2026-11-11)

**Finding** (project quality review 2026-11-05, section 3 Audio and section 4). The master's gain was found by a
search that decoded the mix again for every try. The release gate read only integrated loudness and true peak.
It had no loudness range or short-term figures, no silence (dropout) check, no sibilance figure and no
phone-speaker check.

Round 4 replaces the round-3 candidate (7c9d17e0, review 3 NOT OK 6/10). It is rebuilt on a3946226, which already
has QF-10's picture gate and QF-10b.

## Standards and platform targets (checked 2026-11-11)

| Source | What it says | Used for |
|---|---|---|
| ITU-R BS.1770-5 (2023) | K-weighted loudness, 400 ms gating blocks with 75% overlap, an absolute gate and a gate relative to the measured level; true peak from a signal oversampled to at least 192 kHz | Every loudness and true-peak figure here, through FFmpeg `ebur128` (a BS.1770 / EBU Tech 3341 meter) |
| EBU R 128 (2023, v5.0; the cited text is unchanged from 2014) | Programme loudness, loudness range and maximum true peak characterise a programme; production maximum true peak -1 dBTP; measure the whole programme, not only the voice; maximum momentary and short-term loudness may show whether a programme exceeds the upper tolerance; note 2: loudness range is not recommended for programmes shorter than one minute | The recorded figures; LRA is recorded, never judged (`loudness_range_applicable` false under 60 s) |
| EBU Tech 3342 | LRA is the 10th to 95th percentile range of 3 s short-term loudness above a relative gate 20 LU down | The phone check's windows use the same 3 s windows and gate |
| EBU R 128 s2 (2023, "Loudness in Streaming") | Streaming services use higher target levels than broadcast. It names smartphones as the limited-headroom, noisy playback case. True-peak limiting follows EBU Tech 3344 | Why a phone simulation is worth recording |
| YouTube Help, "Recommended upload encoding settings" | AAC-LC (or Opus), 48 kHz, stereo, 384 kbps stereo. **No loudness target is published** | Not changed. The gate's -14 LUFS / -1.5 dBTP limits stay as they are |
| Meta for Developers, Reels publishing (video API) | AAC Low Complexity, 48 kHz, stereo, 128 kbps or more. **No loudness target is published** | Not changed |
| Meta Engineering, "xHE-AAC" (2023-04-11) | Reels and Stories carry loudness metadata, and the player normalises to a target that depends on its context (device speakers among them). Meta publishes no target value | The master's own loudness is kept accurate, which the player then normalises |

The -14 LUFS (+/- 1 LU) and -1.5 dBTP limits stay as they were (`delivery_profiles`). -1.5 dBTP is 0.5 dB stricter
than R 128's production maximum, which leaves room for lossy re-encoding. No platform publishes a figure to
replace them. The commonly quoted "-14 LUFS for YouTube/Instagram" comes from third parties, not the platforms.

## What changed

**Mastering (`src/video_composer.py`).** Two passes: one measurement of the unlimited mix, then the render.
- `calibrate_gain` decodes the programme once: the mix through the master chain's low-pass, before its gain and
  limiter. Every 100 ms frame's momentary loudness and true peak are recorded (`_analyse`). The gain is
  target - measured, applied linearly ahead of the 4x-oversampled limiter. This is what review 3 asked for: the
  limiter only turns the programme down, and only where a peak would cross its ceiling.
- `_limiter_cost_bound` estimates from that same decode how much the limiter can take. Each 400 ms gating block is
  assumed turned down for the whole block by the largest peak excess in it or beside it. Only when that estimate
  is above the 0.5 LU tolerance is the limited mix measured, and then corrected as before (the old loop, now a
  verification).
- `end_card_level` measures the body and the end card in one decode (asplit), not two.
- The composition record gains `audio_mastering`: each pass's first-pass loudness, LRA and true peak, the gain,
  the estimate, any verification, the card level and the number of decodes.
- loudnorm itself is not used. Its linear mode needs the whole offset to fit under its true-peak target. On real
  mixes it never does (review 3: 34 of 34), so loudnorm falls back to its dynamic mode, a time-varying gain.

**Release gate (`src/audio_qa.py`, `src/release.py`, `src/config.py`).** One decode of the master's sound, split
three ways through the same BS.1770 meter: the programme, a mobile-speaker simulation and a sibilance band.
- `audio_dropout` **blocks**: the sound is below -60 dBFS for 0.2 s or more, more than 0.5 s inside the programme,
  or for 1.0 s or more at either end. The card names the times.
- `audio_phone` is **a note, never blocking**. The speaker simulation is 300 Hz to 8 kHz, two cascaded 2-pole
  sections at each edge (24 dB per octave). The measure is BS.1770 short-term loudness (3 s) through the
  simulation against the full band, over the windows above the relative gate. A window more than 10 LU down (over
  90% of its measured loudness lies where a phone plays nothing) is noted with its times. The integrated loss is
  recorded too.
- `audio_measurement` **fails closed**. A decode error, a timeout (120 s + 5 s per programme second, QF-10's
  picture limit), a short decode (samples per channel against the duration), meters that do not cover the
  programme, or a file that changed during measurement all fail it. When it fails, no sound check is reported.
- Recorded with the measurement: integrated loudness, true peak, LRA (with whether R 128 applies it), maximum
  momentary and short-term loudness, median short-term, peak-to-loudness ratio, the phone figures, the sibilance
  band (4-10 kHz) at its worst against the programme, astats (peak, RMS, flat factor, peak count, noise floor, DC
  offset, samples) and every silence.
- A master measured before is not decoded again. The earlier report's result is reused for the same sha256, format
  and method (`audio_qa.method()`: every `AUDIO_QA_` threshold and `METHOD_VERSION`), like QF-10.
- The integrated loudness and true-peak checks stay the gate's own `loudness` / `true_peak`.

Which checks block, which note and which are only recorded was decided under the operator's delegation, and that
decision includes masters gated before QF-11: `decisions\QF-11-sound-checks.md` (the programme handoff folder, outside the repository). Old masters
get no exemption, which matches the QF-10 ruling. No real master fails a blocking sound check, so no master that
passes today becomes blocked.

**Not added: a de-esser.** The round-3 candidate put `deesser` on every voice without measuring it (review 3
note). Measured here, the sibilance band's loudest momentary window sits 3.1-7.1 LU under the programme on the
development masters and 5.8-8.8 LU under it on the production sample. No master stands out, and changing every
voice without a measured fault is not a quality fix. The figure is recorded so a harsh voice would show.

## Metric

`python measure.py <repo root>` runs the given tree's gate and composer on the generated programmes of
`tests/test_qf11_audio_qa.py`. It also reads the committed real-data files written by `real_data.py`.

| | before (a3946226) | after |
|---|---|---|
| decodes of the mix for the gain, voice over a bed (generated) | 2 | **1** |
| decodes for the end card level (generated) | 2 | **1** |
| voice over loud clicks every 50 ms (the limiter works on every block): decodes, result | 4 (search), -14.5 LUFS | 4 (estimate 0.5 LU+, so verified), **-14.4 LUFS** |
| sound checks on the good master | none | `audio_measurement`, `audio_dropout`, `audio_phone`: pass; 10 of 10 fields |
| 0.8 s dropout / 1.3 s silent end | pass (unchecked) | **blocked** (`audio_dropout`) |
| a real master's 0.13 s silent start | pass | pass |
| 50 Hz drone over 2-6 s | pass (unchecked) | **note**, not blocking (worst window 16.7 LU below) |
| corrupt file | `media_measurement` fail | plus `audio_measurement` **fail**, no sound check reported |

### Real data (`real_data.json` new, `real_data_base.json` base; production sample: counts and aggregates only)

Mastering, each run's own voiceover and bed as `VideoComposer.render` masters them (decodes of the mix before the
render; the delivered-AAC measurement after it is unchanged and not counted):

| | dev before | dev after | production sample before | production sample after |
|---|---|---|---|---|
| runs | 34 | 34 | 34 | 34 |
| decodes, no end-card correction (min / median / max) | 2 / 2 / 2 | 1 / 1 / 2 | 2 / 2 / 2 | 1 / 1 / 1 |
| decodes, with the end-card correction | 6 / 6 / 6 | 3 / 3 / 5 | 6 / 6 / 6 | 3 / 3 / 5 |
| mastered loudness, LUFS (min / median / max) | -14.2 / -14.1 / -14.0 | -14.2 / -14.1 / -14.0 | -14.2 / -14.1 / -14.0 | -14.2 / -14.1 / -14.0 |
| mastered true peak, dBTP (max) | -1.9 | -1.9 | -1.9 | -1.9 |
| more than 0.5 LU off target | 0 | 0 | 0 | 0 |
| limiter's real cost / its estimate, LU (max) | - | 0.2 / 0.66 | - | 0.2 / 0.83 |
| runs where the estimate was below the real cost | - | 0 | - | 0 |
| mastering wall seconds (median; shared host, the after run under heavier load) | 29.0 | 13.5 | 32.1 | 20.1 |

Release gate, every master (*_master.mp4, *_16x9.mp4):

| | development (30) | production sample (33) |
|---|---|---|
| `audio_measurement` failed | 0 | 0 |
| `audio_dropout` failed (false rejects) | 0 | 0 |
| `audio_phone` noted | 0 | 0 |
| worst phone window, LU below (median / max; note above 10) | 6.8 / 7.9 | 4.1 / 4.9 |
| integrated phone loss, LU (median / max) | 4.8 / 5.2 | 2.8 / 3.3 |
| longest silence anywhere, s (max) | 0.36 (a deep dive's last 0.36 s) | 0.0 |
| loudness range, LU (min / max) | 1.1 / 2.4 | 1.2 / 2.1 |
| loudest 3 s, LUFS (max) | -12.4 | -12.7 |
| flat factor (clipping), max | 0 | 0 |
| CPU seconds per programme second (median / max) | 0.127 / 0.174 | 0.131 / 0.143 |
| wall seconds per programme second, max (loaded host) | 0.230 | 0.179 |

The thresholds were set before these runs and were not moved after: 0.2 s at -60 dBFS (no real master has
an interior silence at 0.1 s), and 10 LU for the phone note (2.1 LU above the worst real window).

### Run time against QF-10's limit

The sound decode costs at most 0.174 CPU seconds per programme second. QF-10's picture decode costs 1.38, so the
gate's decode work grows by about 13%. At the worst measured wall rate (0.230 s per programme second, on a host loaded by other suites), the longest
programme the gate admits (360 s) takes about 83 s, against the 1,920 s limit (120 s + 5 s per second, the
picture's formula). A re-check of the same bytes costs one sha256. A real Wieliczka copy took 63.8 s for its first
full gate and 14.4 s to re-check, with both decodes reused, on a host loaded by other sessions' suites.

## Evidence render

`phone_check_real_and_drone.jpg` (`render.py <root> <dev master> <out>`, half size). Top: the real Wieliczka Reel's
short-term loudness, full band (blue) against the phone simulation (orange), worst window 7.8 LU, no note.
Bottom: the generated 50 Hz drone, with the noted windows shaded.

## Review 3 findings

1. *loudnorm's dynamic fallback on every real mix.* Fixed as asked. The measured gain is applied statically ahead
   of the limiter, and loudnorm is not used (see above). On 34 dev and 34 production-sample mixes the result is
   -14.0 to -14.2 LUFS and -1.9 dBTP, and the limiter took at most 0.2 LU.
2. *No real-data check.* `real_data.py` sweeps every real voice/bed pair and master. Its results are committed
   (`real_data.json`, `real_data_base.json`): per run for dev (first pass, decodes, loudness, true peak, the
   estimate, time), counts and aggregates for the production sample.
3. *The retired search left in place.* `calibrate_gain` is the new mastering, and the render calls it. The old loop
   survives only as the verification, used when the estimate exceeds the tolerance. Its origin/main test
   (`tests/test_concurrency.py::test_calibrated_gain_reused_only_for_identical_audio`) is unchanged and passes.
- Notes: the card now shows a dropout as a blocking finding with its times. The de-esser is not added (measured
  above).

## CI fix (2026-11-12): the silence detector across FFmpeg versions

CI (Ubuntu, FFmpeg 8.0.1) passed the dropout and silent-end masters: `audio_dropout` read no silence. The cause
is FFmpeg's source, not the thresholds or the encoder. `af_silencedetect.c` logs through its private context up to
n8.0.1 (`av_log(s, ...)`, so the line reads `[silencedetect @ 0x...]`, as in n6.1). From n8.1 it logs through the
filter context (`av_log(ctx, ...)`, `[silencedetect@gap @ 0x...]`). The parser matched only the 8.1 form, and an
unread detector looked like "no silence". `ebur128` and `astats` log through the filter context in every version
from n6.1 on, so their named instances read the same everywhere. The 6.1 form was reproduced with a static 6.1.2
build: same decode, same silence times, different prefix.

- **Read every form.** `silencedetect`, `silencedetect@gap` and `Parsed_silencedetect_N` all parse. The graph has
  one detector, so the form is unambiguous.
- **Fail closed against the meter.** The full-band meter's per-frame true peak (FTPK) is the cross-check. Three
  100 ms frames in a row whose true peak is below -60 dBFS hold 0.3 s of samples below it, since the true peak is
  never below the sample peak. The detector (0.2 s) must report that span. A frame wholly inside a reported
  silence must not peak above -59 dBFS. Missing frame peaks, an unreported quiet span or a contradicted silence
  fail `audio_measurement` and report no sound check. That is a fail, never a pass.
- **Thresholds unchanged.** `METHOD_VERSION` 1 -> 2, so a result recorded under the old reading is measured again.
- **Recorded logs.** `tests/fixtures/qf11/dropout_ffmpeg-8.1.log` was recorded with 8.1.
  `dropout_ffmpeg-6.1-8.0.log` is the same decode with the detector lines in the 6.1-8.0 form, taken as a 6.1.2
  build printed them. The rest of that log is unchanged: the ebur128 and astats line formats are the same in n6.1
  (`LOG_FMT`, `PRINT_PEAKS`). The test reads the dropout from both. With the detector lines removed, or the frame
  peaks removed, the measurement fails, including through the release gate. A misread silence start also fails.
- **Real data after the fix:** 0 measurement failures, 0 false rejects and 0 notes on 30 dev and 33
  production-sample masters (counts). No real master's meter contradicts its detector.

## Known limits

- The gate sees only the mixed master. A voice that drops out while the bed plays on is not a dropout to it.
  Neither is a word the bed masks.
- The phone band is a simulation chosen for this check (300 Hz is the review's own phone check). No standard or
  platform defines a phone-speaker response, which is why the check is a note.
- The limiter-cost estimate is an estimate, not a proof. It covers one 100 ms frame of release either side of a
  peak. On none of the 68 real mixes was it below the real cost, and the delivered AAC is still
  measured and held to +/- 1 LU after the encode.
- An end-card correction still costs a second gain measurement, because the card's level changes the programme's
  loudness.
