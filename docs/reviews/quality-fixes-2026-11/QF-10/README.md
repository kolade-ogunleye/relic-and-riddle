# QF-10: picture checks in the release gate

Before this row the release gate measured the master's size, duration, loudness and A/V drift, but never looked at
its picture. A black, frozen, letterboxed or out-of-range picture, or a master with the wrong colour tags, passed.
Now `src/picture_qa.py` decodes every frame of the master once, and the gate (`src/release.py`) fails each of these
defects as its own check:

| Check | Fails when (all thresholds are constants in `src/config.py`, none is an environment setting) |
|---|---|
| `picture_colour` | the master is not tagged BT.709 primaries, transfer and matrix, limited range, yuv420p |
| `picture_black` | >= 98% of the luma samples are <= 20 (blackdetect's pix_th 0.02 of 16-235, pic_th 0.98) for 0.10 s |
| `picture_freeze` | a picture is held unchanged for longer than the format's longest permitted hold, 6.1 s for a Reel and 12.1 s for a deep dive (freezedetect's rule: compared with the frame the still began on, below -60 dB) |
| `picture_bars` | equal bands of padding lie at both edges of an axis, each >= 2% of the side and the same size throughout, for 1 s. A padding line has no sample above luma 18, and at least 90% of its samples are exactly code 16. Not checked where the composition record says an essential image is shown whole (QF-52) |
| `picture_range` | more than 1% of any plane lies in out-of-range areas for 0.5 s. An area counts when a sample and its 8 grid neighbours are all beyond limited range by more than 2 codes; isolated codec ringing does not count |
| `picture_measurement` | the picture could not be measured (decode error, timeout, a frame-count mismatch, a changed file, or any other error). The picture checks are then absent, never "pass"; a timeout is tried again at the next check |

A failed check's `detail` is a plain sentence that names what was found and what to do ("The picture goes black at
1:36.8-1:36.9: check the cut or transition there and re-render."), so the review card says it. Before, `check_problems` read the interval dict and printed "no detail recorded". The intervals
are kept beside it under `intervals`.

## Review 4, point by point (round 5)

1. **QF-52's essential "full image" was rejected as bars.** `image_presentation.fit_filter` shows an
   operator-designated essential image whole, padded with black. That padding is the design. The composer now
   records where it does this. `image_presentation.full_image_spans` gives the scenes' (start, end) from the
   composer's own `starts`/`ends`, plus the loop bridge when an essential image opens or closes the Reel. The
   composer writes them into the master's composition record as `full_image_spans`, every time, as an empty list
   when there is none. The gate reads the spans only from a composition record bound to the master's sha256, and
   leaves those intervals out of the bar check. The sentence says so ("not checked over 0:00.0-0:01.5, where an
   essential photo is shown whole: its black border is by design"). Nothing is inferred from pixels. The spans are
   part of the reuse key. Fixture, through the real chain: a real landscape image under `fit_filter` padding, then
   `RGB_TO_BT709`, `picture_chain(film_grade_filter())` (print LUT and lens vignette) and CRF 18, 1.5 s, followed by
   1.5 s of true letterboxing. With the record, bars fail only from 1.5 s. Without it, they fail from 0 s. Exempting
   both scenes passes.
2. **Colour tags on masters made before the colour fix.** The operator ruled "Grandfather old masters"
   (decisions\QF-10-ssim.md). Such a master gets a note, not a block: "Made before the colour fix: the video's
   colour tags are incomplete (its picture is BT.709). It can still be posted; re-render it to tag it." It is
   `status: pass, minor, grandfathered`, so the card lists it as not blocking. The condition is read from stored
   records:
   - its tags are exactly the pre-QF-01 set (matrix bt709, range tv, yuv420p, primaries and transfer unset);
   - its composition record is bound to its sha256 and was written before QF-10 (no `full_image_spans` key, which
     the QF-10 composer always writes);
   - the run's release report was written by the gate before QF-10. Such a report has no picture measurement and
     schema 1.0; the QF-10 gate writes schema 1.1. A later check accepts the gate's own record of grandfathering
     these same bytes.

   A new render, a master never gated, a master first gated by QF-10, and other wrong tags all still fail. Black,
   freeze, bars and range block every master. Every approve and post path reads the same report:
   `require_release` (`publish_run`, `post_now`, the CLI `review approve`/`review publish`) and the card's
   `release_lines` and Approve button (`blocking_checks`). Tests cover the note, the re-check, four cases that do
   not qualify, and a grandfathered master that is still blocked by picture_black through `require_release`.
- Non-blocking, done: plain card sentences for every picture check. A timeout that strikes inside a frame now says
  it timed out ("tried again at the next check"). Any exception while measuring is recorded as
  `picture_measurement: fail` (test). The wall-clock limit stays generous (120 s + 5 s per programme second,
  against a measured one-core worst case of 1.2 s per programme second), and a timeout is retried at the next check.
- Production-sample evidence is counts only (decisions\PROD-SAMPLE-counts-only.md). `real_data_prod.json` holds
  summaries and counts, and this README cites counts.

## Review 3, point by point

1. **SSIM was a no-op reported as a pass.** The gate holds only the master, so there is no reference to compare it
   with. The `picture_ssim` line is gone. Fidelity SSIM against the composer's own pre-encode picture moves to QF-12,
   which owns the delivery encode. That decision is delegated, recorded in `decisions\QF-10-ssim.md` (option 3) with
   QF-12's conditions. The acceptance count drops SSIM: 5 of 5 checks fail their broken master, plus two defects
   made from the real Wieliczka graphic (7/7 fixtures). `measure.py` fails acceptance if any picture_ssim line appears, and the
   regression test asserts the exact set of picture checks reported.
2. **Run time.** One decode of the full master, sampled every 4th pixel of every 4th line (a 1080-line master is
   measured on 270 x 480 samples). The sampling is nearest neighbour, so the sampled values are the decoded values.
   The decode feeds four numpy analysers, replacing round 3's five full-resolution FFmpeg filters
   (blackdetect, freezedetect, cropdetect, lutyuv, signalstats). The decode itself is the floor: every frame is still
   decoded. The time limit grows with the programme (120 s + 5 s per programme second: 356 s for a 47 s Reel,
   1,920 s for the longest 360 s deep dive). Measured numbers are in the table below. The one-core bound is given so
   the VM's core count is not needed. A master already measured is not decoded again. The review card re-runs the
   gate every time it is shown (`telegram_bot.py:1789`), and so does the copy-edit path (`main.py:1232`). The gate
   reuses the earlier report's picture result only for the same sha256, the same format and the same method
   (`picture_qa.method()`: every threshold plus `METHOD_VERSION`). The cost of a re-check is one sha256.
3. **picture_bars rejected a house graphic** (Wieliczka's depth graphic, 23.93-28.17 s). Padding is flat code-16
   black: an encoded pad is all 16 away from the picture's edge. A house graphic's dark ground is not. Its grain and
   vignette spread every line over several codes (here luma 16-27, mean 19, only 4% of samples at 16). Bars are now
   measured as edge bands of padding lines, not as cropdetect's line means. On that frame, the padding lines are
   top 0 and bottom 0 (round 3 found 448 and 448). A real pad is measured exactly: 50/50 rows on the letterboxed
   fixture, and 52/51 of 52.5 on a lossy x264 CRF 23 pad, where ringing at the picture edge costs a row. The frame is committed at the gate's own grid
   (`tests/fixtures/qf10/wieliczka_depth_graphic_25s.png`). The regression test passes it and fails the same picture
   shrunk into flat padding. Results over every real master, house graphic and design frame are below.
4. **measure.py's real-data block was typed in.** Every number is computed now. `real_data.py` (committed) measures
   the real masters, graphics, stills and design-card renders and prints JSON. Its output is committed as derived
   data (`real_data.json` dev, `real_data_prod.json` production-sample counts, `real_data_cards.json`,
   `real_data_single_thread.json`), and `measure.py` reads those
   files and runs the fixtures live. The Richat deep dive's black intervals are measured, not assumed: 96.83-96.93 s
   and 142.17-142.27 s, which match the review's figures.
5. **A threshold was an environment override.** No `PICTURE_QA_*` value reads the environment (the SSIM threshold is
   gone, and the threads setting is a constant too). `.env.example` is unchanged because there is no setting to
   document.
- Note, photosensitivity: QF-88 owns it, recorded in `decisions\QF-10-ssim.md` (question 2) and in the ledger row.
  QF-88 adds its analyser to `picture_qa.analysers()` on the same decode and bumps `METHOD_VERSION`.
- Note, the synchronous re-run in `main.py:1232`: it reuses the measurement unless the master changed (point 2).

## Measurements

`python docs/reviews/quality-fixes-2026-11/QF-10/measure.py <repo root>`:

| | base 1a7d2759 | this commit |
|---|---|---|
| broken fixtures caught (black, freeze, bars, range, colour; the real Wieliczka graphic letterboxed in flat padding; the same graphic in full range tagged limited) | 0/7 (no picture checks) | 7/7 |
| clean pictures passing every picture check (moving testsrc2, the real Wieliczka graphic, a halftone that rings beyond range) | 0/3 (none reported) | 3/3 |
| an SSIM verdict printed without a reference | no line (no checks) | no line |
| essential image shown whole (real fit_filter chain) then true letterboxing: padding not reported, letterbox caught | no (no checks) | yes |
| pre-QF-01 master gated before QF-10: colour noted, not blocked | no (no checks) | yes |

### Real data (this commit; `real_data.py`, committed output in `real_data*.json`)

The base has no picture checks, so it rejects nothing and detects nothing. Round 3 (aaa37264), as measured in
review 3, failed picture_bars on 1 of 29 development Reels (the Wieliczka house graphic). During this round, the
first version of the range check (which counted every sample beyond 2 codes) failed 1 of 33 production-sample
Reels on codec ringing. The area rule clears it. A synthetic halftone that rings at about 25% of its samples is the
fixture in the test and in `measure.py`. (Production-sample evidence is counts only:
decisions/PROD-SAMPLE-counts-only.)

| Set | Items | Failed, other than colour tags | Measurement errors | picture_colour fail |
|---|---|---|---|---|
| Dev masters (29 Reels + 1 deep dive) | 30 | 1: Richat deep dive, picture_black 96.83-96.93 s and 142.17-142.27 s (a true finding, see evidence) | 0 | 30 (pre-QF-01 tags) |
| Dev house-graphic clips (map 32, stat card 20, timeline 9, then-and-now 2, scale 2) | 65 | 0 | 1: Odessa graphic_map.mp4 is truncated (no moov atom), refused, not passed | 64 |
| Dev stills (carousel 217, story 90, cover 32, grid tile 32, graphic stills 64, YouTube thumbnail 1) | 436 | 0 | 0 | 0 |
| Production-sample masters (copy, 33 Reels) | 33 | 0 | 0 | 33 |
| Production-sample graphic clips | 86 | 0 | 0 | 86 |
| Production-sample stills (stories, carousel, covers, grid tiles, graphic stills) | 494 | 0 | 0 | 0 |
| Design-card renders on real run inputs (chapter 100, hook 40, hook title 7, myth 28) + end card 3 | 178 | 0 | 0 | 0 |

False rejects of house graphics and frozen-design frames: **0** of 1,258 (covers, grid tiles, stories,
carousel, maps, stat cards, timelines, scale, then-and-now, chapter, hook and myth cards, end card), and 0 of 62
real Reels.

### Run time (the host was shared and loaded throughout; CPU seconds are the load-independent figure)

| | Development (30) | Production sample (33) |
|---|---|---|
| wall per master, all threads: min / median / max | 13.3 / 19.0 / 44.0 s | 24.7 / 36.4 / 40.9 s |
| CPU seconds per programme second (decode + analysers): median / max | 1.20 / 1.33 | 1.22 / 1.38 |

One core (`PICTURE_QA_THREADS = 1`, `real_data_single_thread.json`): the Richat deep dive (196.4 s, 5,891 frames)
takes 272.9 s wall and 157.7 CPU s (134.5 decode + 23.3 analysers). Round 3 spent 1,473 CPU s on the same master
(review 3), so this is 9.3 times less. The worst rate on one core is 1.48 wall seconds per programme second, which
projects the longest admitted programme (360 s) to 534 s against a limit of 1,920 s. On all threads the deep dive
takes 62.0 s and the three Reels 13.7-19.7 s. All of this was measured while the host ran other sessions' test
suites. The limit holds on any core count, so the
VM's vCPU count is not needed.

To reproduce: `real_data.py <root> --dev <main checkout>/output`; `--dev <empty dir> --prod <copy of the
production sample's output>`;
`--dev <empty dir> --cards <dir>` after `render_evidence.py --code-root <root> --out <dir>`
(`RR_FIXTURE_REPO=<main checkout>`); `--dev <dir with copies of 3 Reels and the deep dive> --single-thread 3`.

## Downstream effects

- **Masters made before QF-01 (all 30 dev, all 33 in the production sample: primaries and transfer unset).**
  Under the operator's ruling, one that the gate checked before QF-10 shows a not-blocking note on its card. Its
  Approve, `review approve`, `review publish`, and the scheduled `publish_run`/`post_now` at its slot all stay as
  they were: an approved, booked post still goes out. One that was never gated before QF-10 fails picture_colour
  until it is re-rendered (no paid calls). So does any master rendered after this commit with those tags. A real
  colour fault on a new render is never hidden.
- **Release-gate time.** Measuring the picture adds one full decode the first time a master is checked (wall times
  in the table). Every later check of the same bytes reuses it, for the cost of a sha256. The review card re-runs
  the gate each time it is shown, so this matters. On a copy of the Wieliczka master, the first gate took 48.5 s
  and the re-check 19.8 s. The re-check's time is the existing loudness and media measurements; its picture result
  carried `reused_from`.
- **Consumers of `release_report.json`.** The review card (`release_lines`), `release_problems`/`check_problems`
  (which now print the sentence), `control.py`'s gate line, `improve.py` (which files a failed picture check as
  `release.picture_*`), `card_ledger`, `audience` (which reads only `duration`), `probe` and the backup all read the
  checks generically. No consumer keys on the new names. Each report gains six entries (about 2 KB).
- **Production and dev data.** Nothing outside a temporary folder is written by `measure.py` or `real_data.py`.
  The production sample was measured from a copy, and only counts and aggregate timings are committed.
- **Composition record.** It gains `full_image_spans`, a list that is empty unless an essential image is shown
  whole. `release.composition_inputs`, `end_card_record` and `video_qa` read other keys and are unaffected.
- **QF-88 hooks.** `picture_qa.analysers()` is the single list of per-frame gates on the one decode. A record made
  under another `METHOD_VERSION` or other thresholds is measured again, so adding QF-88's analyser needs no
  migration.

## Evidence

- `wieliczka_bars_before_after.jpg`: the Wieliczka house graphic at 0:25.0. On the left, the round-3 rule's
  448 px bands, which fail it. On the right, the padding-line rule's 0 and 0, which pass it (half size).
- `richat_act_dip_black.jpg`: the Richat deep dive from 96.70 s, every second frame. The old fade-through-black
  transition (removed since by QF-04) is black from 96.83 to 96.93 s: a true picture_black finding (half size).

## Known limits

- SSIM: not in this row (decision above). Until QF-12 lands, nothing compares the master with what it was encoded
  from.
- Photosensitivity: QF-88. Archive film, AI clips and operator footage are not yet flash-checked.
- Bars must be symmetric: a picture pushed to one edge with black on the other side only is not reported. A
  letterbox whose padding is not flat black (a blurred fill, a colour) is a design choice and is not reported either.
- The sampled grid sees a defect only when it covers more than one sample (4 x 4 px at 1080 lines). The checks
  measure fractions of the picture (98%, 1%, 2% of a side), so this does not change their verdicts.
