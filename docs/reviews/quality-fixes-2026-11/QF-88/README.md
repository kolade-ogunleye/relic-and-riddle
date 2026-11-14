# QF-88: photosensitivity, CPS and CVD gates

Spec (project-quality-review-2026-11-05, QF-88): no photosensitivity check (archive film, AI hook clips and operator
footage unmeasured); colour-blind legibility of published graphics never measured; `CAPTION_MAX_CPS=17` logged, not
enforced. Acceptance: a 10-Hz strobe scene is blocked with a named reason; a map passes the CVD gate; a 20-cps burst
fails the build. How the CPS and CVD gates act on real data was decided under delegation
(decisions/QF-88-question.md: CPS option B, CVD option C).

## Round 7 (review 1 round 6, NOT OK 5/10; operator answer after round 6, decisions/QF-88.md)

- **Blocking 1, a fail beside a held stretch became a clearable hold.** `measure` kept `hold` and `held` on a
  failing picture_flash check, `release._flash_hold` made it `needs_review`, and `review clear-flash` then passed
  the hazard (a 4 Hz full-frame strobe followed by a held second; fixtures sb_b22_c08 and ip_b3_k6).
  - A flash check with any failing interval is a fail and nothing else. `Flash.check` holds a stretch only when
    nothing fails, and `measure` writes `hold` / `held` only then.
  - `release._flash_hold` acts only on a pure hold. A recorded report holding a fail with a held stretch (as round 6
    wrote it) is put back to the fail it is: its clearance and sheets are dropped and its sentence restored.
  - `release.held_flash` returns nothing for a check with intervals. `release.clear_flash_hold` refuses a failing
    check: "The flash check fails on this master (it flashes more than 3 times in a second somewhere), so it cannot
    be cleared: re-render it without that flashing." `review clear-flash` says so first (`release.flash_failure`).
    The card's "held: possible flash" line needs `needs_review` and no intervals; otherwise it says "blocked".
  - A `flash_review.json` written for the same bytes never passes a failing check.
- **Blocking 2, 0.44-degree checkers over 52-60% of a field reversing 8 times a second passed.** The fine stage's
  0.1-degree boxes that straddle the squares' edges average half rising and half falling, so about 21% of the area
  read as unchanged (chk52_8, chk56_8, chk60_8, chk52_red, sb_b18_c16).
  - The reviewer's one-sample dilation fails all five, but measured here it is not a drop-in. On the dev masters it
    turned both Richat holds and the Roman pass into rejects (3 new rejects of 30; their frames are whips into a
    card, as `roman_held_stretch.jpg` shows). A reach from every changing box (tried in this round with the
    narrower rules below) also failed a 0.04-degree checkerboard, which WCAG exempts and a must-pass test checks: the
    boxes' moire still swings 12% of the field, and the reach spread that to 49%.
  - **The second reading (`_FlashStage(dilate=True)`).** The fine stage now reads the boxes twice. The first reading
    is the boxes as they are (round 6). The second also counts a sample as rising (falling) where solid change, at
    least 2 x 2 boxes, rises (falls) within one step of it (`_solid`, a binary opening; `_reach`).
  - A square over 0.1 degree has such a solid inside, so it is counted whole. A pattern under 0.1 degree is not
    reached from, because its few swinging boxes are scattered. Squares of exactly 0.1 degree leave only single
    boxes changing; they stay the first reading's, which fails them over a whole field as before.
  - The reach corrects only what the edge loss hides:
    - It can make a frame counter-phase, never a single-direction transition. That still needs a quarter of the
      boxes themselves: reached in every direction, whips into a card read as 7 single-direction transitions.
    - The coarse test stays on the boxes themselves.
    - A sample's own transitions are its box's own or, if more, the most that solid change within a step made. It
      is the most, never the sum, so an edge moving over detail is not one box flashing in place.
  - **Either reading's fail fails, and either one's hold holds** (`Flash.frame`). So the second reading never lets
    pass what round 6 failed. That matters: a frame the reach makes counter-phase loses the single-direction count
    the first reading gives it. Measured with a full one-step reach (and the coarse test on the boxes),
    fine_rot_plus_band became a hold alone; combined it stays a fail.
  - On a frame where the first reading fails, the second is not asked. It takes the first's per-sample transitions
    and field levels.
- **F2 with few in-place frames is held (review 1 round 6, non-blocking 1: production-sample master P1).** With the
  second reading P1 still failed, by F2: 6 transitions that always count plus one or two fine counter-phase frames.
  - Its frames (a contact sheet in scratch; production sample, not committed) show a 4-frame zoom-whip through
    gilded panels with white streaks (4.70-4.77 s), one full-frame cream card (4.80 s) and a 5-frame fade to a dark
    map. That is one bright flash and a whip; no area flashes more than 3 times in a second.
  - The independent analyser says the same: 6 / 6 / 7 transitions at 1, 0.5 and 0.2 degree, and 1-3.3% of the field
    over 6. No frame shows a 2.3.1 hazard, so under decision A it is held, not rejected.
  - The rule: F2 now fails only with at least `PICTURE_QA_FLASH_IN_PLACE` (6) such frames, the same bar as the
    in-place remainder; with 1 to 5 it holds (`_held`).
  - sub_band_checker_24 and _c10 have the same counts ((5, 2), (5, 3), (6, 2)), so they are held with it. The round-6
    review allowed that ("a hazard may be held"). Every other F2 fixture still fails: sub_band_checker_20, _red,
    _10hz, sub_band_burst, sub_band_vstrip, sb_b18_c16, sb_b22_c08, sb_b24_c04 and cuts_fine_partner.
- Cost:
  - `_solid` and `_reach` are numpy shifts (scipy's binary opening took 53 s of a 196-s profile).
  - The F2 frame count is kept per frame for both the fail and the hold, and it stops at 6.
  - The second reading skips its own hold search where the first already holds.
- No threshold changed: 25%, 3 a second, a third of each side, 0.10/0.80, 0.2/0.8, coarse 0.1 and
  `PICTURE_QA_FLASH_IN_PLACE` 6.
- `METHOD_VERSION` stays 2. Version 2 has never been released (this row is not landed), so no stored measurement
  outside builders' scratch copies holds it.

### Fixtures (the reviewer's probe6r.py, 91 fixtures, 270 x 480 at 30 fps; f8c23fc2 = round 6)

One decode per fixture with both analysers side by side (scratch probe_cmp.py). 84 of 91 keep their verdict. The
three marked "clearable" kept their Flash verdict but failed only at the release level on f8c23fc2: the hold
sneak, now fixed. The full table is in the build note.

| Fixture | ruling | f8c23fc2 | this round | flash analyser s, f8c23fc2 / this round |
|---|---|---|---|---|
| sub_band_checker_24 | fail | fail (4) | **hold (3)** | 41.5 / 53.9 |
| sub_band_checker_c10 | fail | fail (4) | **hold (3)** | 44.8 / 55.5 |
| ip_b3_k6 | fail | fail (6), held stretch beside it: clearable | **fail (6)** | 19.5 / 23.9 |
| chk52_8 | fail | pass (3) | **fail (4)** | 17.1 / 28.1 |
| chk56_8 | fail | pass (3) | **fail (4)** | 16.8 / 26.2 |
| chk52_red | fail | pass (3) | **fail (4)** | 23.1 / 34.0 |
| chk60_8 | fail | pass (3) | **fail (4)** | 17.0 / 25.1 |
| sb_b18_c16 | fail | pass (3) | **fail (4)** | 21.0 / 86.9 |
| sb_b22_c08 | fail | fail (4), held stretch beside it: clearable | **fail (4)** | 40.8 / 51.6 |
| fail_then_hold | fail | fail (3.5), held stretch beside it: clearable | **fail (3.5)** | 40.2 / 51.6 |

- Nothing that failed or was held on f8c23fc2 passes.
- The five blocking-2 hazards fail.
- Every pass fixture still passes: full_3hz, halves_3hz, cuts_6_per_s, the zooms, pans, cuts_photos_8, ip_b2_k2,
  ip_b0_k6, chk48_8, chk48_15, blockpair24_8, sb_b20_c08, sb_b14_c14 and scattered_spread.
- The held ones stay held: coarse3_plus_fine_reversal and ip_b3_k1 to _k5, with the 8 Hz and 8-px/2-px variants.
- The test's 0.028- and 0.04-degree checkerboards pass.

### Real masters (one decode per master, both analysers; per master pass / hold / reject; f8c23fc2 vs this round)

All other masters pass on both: 26 of 30 dev and 28 of 33 production-sample masters. The copy was hash-checked
against the manifest (2,713 files, 0 mismatches) and read in place, read-only. Counts only: the production-sample
run ids are in the build note, not here.

| Master | f8c23fc2 | this round | stretch (s) | flash analyser s, f8c23fc2 / this round |
|---|---|---|---|---|
| dev Amber Room 1792790550 | reject | **reject** | 3.70-5.43 | 63.4 / 74.9 |
| dev Richat 16x9 1792740961 | hold | **hold** | 14.20-15.87 | 95.8 / 106.7 |
| dev Richat 1793272675 | hold | **hold** | 2.70-4.37 | 49.6 / 61.5 |
| dev Roman concrete 1793163918 | pass | **hold** | 4.30-5.97 | 57.1 / 77.4 |
| production-sample master P1 | reject | **hold** | 3.93-5.60 | 73.9 / 83.9 |
| production-sample master P2 | hold | **hold** | 4.60-6.27 | 55.5 / 73.5 |
| production-sample master P3 | hold | **hold** | 2.37-4.00 | 56.6 / 79.9 |
| production-sample master P4 | reject | **reject** | 4.47-6.17 | 99.3 / 115.7 |
| production-sample master P5 | pass | **hold** | 2.13-3.80 | 42.6 / 63.9 |

- Totals, pass / hold / reject: dev 27 / 2 / 1 -> **26 / 3 / 1**; production-sample 29 / 2 / 2 -> **28 / 4 / 1**.
- **No new reject.** Both true hazards stay rejects: the dev Amber Room and P4.
- P1 is held (above).
- Two passes became holds, not rejects; they wait for a look and are never auto-rejected:
  - dev Roman concrete: `roman_held_stretch.jpg`, the contact sheet the release report carries (4.30-5.97 s, 51
    frames, 300,210 bytes). A zoom-whip through the rough concrete (5.0-5.2 s) into a flat amber card, then a fade
    to a dark timeline.
  - P5: a zoom-whip through a fine-line engraving into a dark card, then a fade up to a seascape (frames in scratch,
    not committed).
  - Both are the class of the held Richat masters. F2 reaches 7 there with 1 or 2 fine frames, now only by the
    second reading.

### Time

One decode per master with both analysers side by side (each analyser's own time, so both saw the same host load;
the host was shared and fully loaded):
- flash analyser total: dev 514.1 s -> 565.5 s (+10.0%), production-sample 590.1 s -> 675.2 s (+14.4%);
- 59 of 63 masters within +25%. The other 4 are held masters where the fine stage runs (+32% to +50%). The slowest is P4 at
  115.7 s. The longest whole decode with both analysers was 221 s, on P4, against its 352-s limit (QF-10: 120 s +
  5 s per programme second). Alone on the real path (`picture_qa.measure`, one analyser) P4 took 123.5 s on the
  same loaded host.
- Fixtures: 1,916 s -> 2,413 s in all (+26%). The slowest is sb_b18_c16 (21.0 -> 86.9 s): it passes the first
  reading, so the second decides it with its full F2 work.
- The F2 early stop at 6 frames came after these runs. It changes no result: it returns as soon as some field has the
  6 frames the fail asks for, and no call asks for more. With it, on the same loaded host and alone on the real path,
  P4 took 97.6 s (limit 352 s) and sb_b18_c16 64.6 s (limit 135 s; the whole test file, 79 tests at -n 4, passes).

## Round 6 (review 1 round 5, NOT OK 6/10; operator decision A after round 5, decisions/QF-88.md)

- **Blocking 1, a sub-25% coarse change beside a sub-25% fine pattern flashing in place passed.** At every cut 30%
  of the field rose and 30% fell (a band jumping 8 times a second plus a 0.44-degree checker inverting in place), and
  the gate reported "3 flashes" (sub_band_checker_20, sub_band_vstrip, sub_band_burst).
- **The rule (the reviewer's candidate F2, built incrementally).** A fine counter-phase frame also counts for a
  field where, on that frame, more than 25% of the field rose (or fell) coarsely or by samples that themselves made
  n transitions in the second (flashing in place at the field's rate). Each fine counter-phase frame's rise and fall
  masks, their ~1-degree coarse boxes and each field's coarse share are kept when the frame arrives; a count asks
  only fields that could reach it this way, and a frame only where its coarse share plus the field's share of
  samples that made n could exceed 25%. The other conditions are found by bisection (each holds for n - 1 where it
  holds for n); this one is asked once a frame, upwards from the limit, and only when the others leave the field at
  or under it.
- **Round 5's addition, narrowed (not dropped).** Dropping it outright turned masked_rolling_fine from a fail into a
  hold, so a
  remainder of fine counter-phase transitions still fails where more than 25% of the field's samples made at least
  `PICTURE_QA_FLASH_IN_PLACE` = 6 of them in place (twice `PICTURE_QA_FLASH_PER_S`: that area alone flashed at the
  limit). masked_rolling_fine (6 each) fails by it again.
- **Held, not passed and not rejected (decision A).** The same remainder with 1 to 5 in-place transitions is the
  count the gate cannot settle without a motion test: one fine frame after 6 that always count is what both a fine
  pattern reversing in place (coarse3_plus_fine_reversal, 3.5 flashes) and a whip over detail into a flat card give.
  - picture_flash is then `needs_review` with `hold: true` and the stretch in `held`. Its sentence says "Possible
    flash; needs your look: <stretch> (<seconds> s)", the count if it is a flash, and what to do.
  - The release report carries a JPEG contact sheet of each held stretch from the master's own frames
    (`picture_qa.hold_sheet`: every frame of the stretch, up to 60, labelled with its time; at most 300 KB;
    `PICTURE_HOLD_SHEET_*` in src/config.py, documented in .env.example). It sits beside the master.
  - A held master never posts by itself: `needs_review` blocks every live post (`release.blocking_checks`), the
    auto-approve flag sends it to review instead of approving it (`main._held_for_look`, with an alert), and the
    card says "held: possible flash; needs your look" rather than "blocked".
  - The operator clears it after looking: `python main.py review clear-flash <run> --looked` writes
    `flash_review.json` for these exact bytes and stretches; the check then reads as a note ("you looked at it and
    cleared it"). A re-render or another stretch is held again. Approval then works as usual.
  - Nothing round 5 failed passes now: each is a fail or a hold. Both real true hazards and every listed must-fail
    fixture stay fails, except coarse3_plus_fine_reversal, which is held (below).
- **A replay artefact removed.** The fine stage starts fresh when the screen stage finds a field over the limit. It
  replayed only 1.5 s, so a field's first transition in the replay counted whatever came before it: a rise after an
  earlier rise counted as a 7th transition. That was the dev Roman concrete reject (with the full history the field
  makes 6). The stage now replays `FINE_HISTORY_S` = 5 s, state only (no counting; frames that leave the window
  before the stage counts update the field directions only), and stays on for 1.5 s as before. Strict before; on the
  Roman master 5 s gives the full-history count (6; 3 s still gave 7).
- No threshold changed: 25%, 3 flashes a second, the 10-degree field, 0.10/0.80, 0.2/0.8, the ~1-degree coarse box.
  The motion test is proposed as a follow-up row (QF-88b).

### Fixtures (the reviewer's probe5r.py fixtures, 270 x 480 at 30 fps; 49dd857a = round 5)

| Fixture | want | 49dd857a | round 6 |
|---|---|---|---|
| full_3hz | pass | pass (3) | pass (3) |
| full_3.5hz | fail | fail (3.5) | fail (3.5) |
| full_4hz | fail | fail (4) | fail (4) |
| red_10hz_on_black | fail | fail (10) | fail (10) |
| red_4hz_on_black | fail | fail (4) | fail (4) |
| rolling_bands_white | fail | fail (9) | fail (9) |
| rolling_bands_red | fail | fail (9) | fail (9) |
| rolling_blocks | fail | fail (9) | fail (9) |
| rolling_cols4 | fail | fail (9) | fail (9) |
| rolling_bands_2each | fail | fail (6) | fail (6) |
| moving_band_3px | fail | fail (15) | fail (15) |
| moving_band_6px | fail | fail (12) | fail (12) |
| moving_band_10px | fail | fail (7) | fail (7) |
| moving_box_4px | fail | fail (6) | fail (6) |
| moving_box_8px | fail | pass (3) | pass (3) |
| scattered_spread | pass | pass (0) | pass (0) |
| scattered_clustered | fail | fail (10) | fail (10) |
| straddle_10hz | fail | fail (10) | fail (10) |
| whip_fine | pass | fail (10) | fail (10) |
| cuts_8_per_s | fail | fail (4) | fail (4) |
| whip_plus_rolling | fail | fail (6) | fail (6) |
| masked_rolling | fail | fail (6) | fail (6) |
| rolling_ab_control | fail | fail (6) | fail (6) |
| halves_5hz | fail | fail (5) | fail (5) |
| halves_3.5hz | fail | fail (3.5) | fail (3.5) |
| halves_3hz | pass | pass (3) | pass (3) |
| cuts_9_per_s | fail | fail (4.5) | fail (4.5) |
| cuts_8_band40 | fail | fail (4) | fail (4) |
| cuts_6_per_s | pass | pass (3) | pass (3) |
| cuts_12_4class | fail | pass (0) | pass (0) |
| zoom_slow_photo | pass | pass (0) | pass (0) |
| zoom_fast_photo | pass | pass (0) | pass (0) |
| zoom_slow_checker | pass | pass (0) | pass (0) |
| pan_photo_25px | pass | pass (0) | pass (0) |
| pan_photo_60px | pass | pass (3) | pass (3) |
| cuts_photos_8 | pass? | pass (3) | pass (3) |
| masked_rolling_red | fail | fail (6) | fail (6) |
| masked_rolling_fine | fail | fail (6) | fail (6) |
| cuts_12_per_s | fail | fail (6) | fail (6) |
| red_cuts_8 | fail | fail (4) | fail (4) |
| cuts_fine_partner | fail | fail (4) | fail (4) |
| sub_band_checker_24 | fail | fail (4) | fail (4) |
| sub_band_checker_20 | fail | pass (3) | fail (4) |
| sub_band_checker_red | fail | fail (4) | fail (4) |
| sub_band_checker_10hz | fail | fail (5) | fail (4.5) |
| sub_band_checker_c10 | fail? | fail (4) | fail (4) |
| band24_sprinkle | fail? | pass (2) | pass (2) |
| band24_alone | pass | pass (0) | pass (0) |
| alt_coarse_fine | fail | fail (6) | fail (6) |
| coarse_rise_fine_fall | fail | fail (6.5) | fail (6.5) |
| fine_rot_plus_band | pass? | fail (6) | fail (5) |
| sub_band_burst | fail | pass (3) | fail (4) |
| sub_band_vstrip | fail | pass (2) | fail (4) |
| two_patch_rotation | fail? | pass (2.5) | pass (2.5) |
| coarse3_plus_fine_reversal | fail | fail (3.5) | hold (3.5) |

- Changed: sub_band_checker_20, sub_band_vstrip and sub_band_burst now fail (blocking 1; must-fail tests);
  coarse3_plus_fine_reversal is now held, not failed (below). Every other fixture keeps its result.
  sub_band_checker_10hz reports 4.5 instead of 5: a failing frame reports the count the cheaper conditions give (the
  in-place condition is asked only where they leave the field at or under the limit).
- coarse3_plus_fine_reversal (a 30% band flashing 3 times, then a 0.44-degree checker over 70% reversing once) is the
  case the round-5 review names as needing a motion test: its count, 6 that always count plus one fine counter-phase
  frame, is the one the whips into flat cards give. Failing it fails those masters; passing it is not allowed. So it is
  held: never posted without the operator's clearance, never passed. It is a hold case in the regression test and in
  measure.py.
- Known-limit rows (moving_box_8px, two_patch_rotation, band24_sprinkle pass; whip_fine fails, strict) are as in round
  5. fine_rot_plus_band (the reviewer's "pass?") still fails.

### Real masters (one decode per master; per master pass / hold / reject; 49dd857a vs this commit)

All other masters pass on both: 26 of 30 dev and 28 of 33 production-sample masters (the copy hash-checked against the
manifest, 2,713 files, 0 mismatches; read in place, read-only; counts only, the production-sample run ids are in the
build note, not here).

| Master | 49dd857a | round 6 | stretch (s) | flash analyser s, 49dd857a / round 6 | reviewer's round-5 verdict |
|---|---|---|---|---|---|
| dev Amber Room 1792790550 | reject | **reject** | 3.70-5.43 | 20.0 / 30.6 | true hazard |
| dev Richat 16x9 1792740961 | reject | **hold** | 14.20-15.87 | 43.0 / 55.1 | false reject |
| dev Richat 1793272675 | reject | **hold** | 2.70-4.37 | 17.8 / 23.8 | false reject |
| dev Roman concrete 1793163918 | reject | **pass** | 4.23-5.97 | 20.8 / 31.4 | false reject |
| production-sample master P1 | reject | **reject** | 3.90-5.60 | 24.5 / 37.9 | false reject |
| production-sample master P2 | reject | **hold** | 4.60-6.27 | 21.6 / 34.7 | false reject |
| production-sample master P3 | reject | **hold** | 2.37-4.00 | 24.3 / 33.8 | false reject |
| production-sample master P4 | reject | **reject** | 4.47-6.17 | 44.1 / 65.9 | true hazard |
| production-sample master P5 | reject | **pass** | 2.10-3.80 | 22.5 / 26.6 | false reject |

- Totals, pass / hold / reject: dev 26 / 0 / 4 -> **27 / 2 / 1**; production-sample 28 / 0 / 5 -> **29 / 2 / 2**.
- The two true hazards stay rejects (the dev Amber Room and P4, the whip across black-and-white bands into a white
  card).
- Of the 7 false rejects, 2 pass (dev Roman concrete; P5), 4 are held (both Richat masters, P2, P3) and **1 stays a
  reject (P1)**:
  - Roman concrete: the 7th transition was the fine stage's replay artefact (below); with the field's full history it
    makes 6.
  - The held ones: 6 (or 5) transitions that always count plus 1 to 4 fine counter-phase frames whose samples changed in
    place 1 to 4 times. `richat_held_stretch.jpg` is the contact sheet the release report carries for dev Richat
    1793272675 (2.70-4.37 s, 51 frames, 289 KB): a zoom-whip through the crater at 3.4-3.6 s into a flat card, then a
    fade.
  - P1 fails by the in-place condition itself (F2, the reviewer's candidate): at the fine stage a field makes 6 that
    always count (or 5) and 1 (or 2) fine counter-phase frames on which coarse change plus samples that flipped 7 times
    in the second take it over 25%. sub_band_checker_24, a ruled hazard, gives the same counts (s, fine frames) = (5,
    2), (5, 3), (6, 2), so no count separates them; holding them would hold that hazard. Its frames (looked at in
    scratch, not committed: production sample) show the retired QF-04 zoom-whip through high-contrast gilded panels with
    white streaks (4.70-4.77 s), a full-frame cream flash at 4.80 s and a fade to a dark map, the same transition as the
    dev Amber Room true hazard. It stays a reject; the reviewer's round-5 count for it was 6 / 6 / 7 at 1, 0.5 and 0.2
    degree. A re-render cuts there (QF-04).

### Time

Measured on one decode per master with both analysers side by side (scratch compare6.py; each analyser's own time,
so both saw the same host load):
- flash analyser total: dev 296.1 s -> 345.9 s (+16.8%), production-sample 368.8 s -> 431.6 s (+17.0%);
- 55 of 63 masters within +25% of round 5. The other 8 are exactly round 5's rejects, where the fine stage runs:
  +28% to +61%, from the 5-s state replay. The slowest is P4 at 65.9 s (round 5: 44.1 s); the longest whole decode
  with both analysers was 116 s, against QF-10's limit of 120 s + 5 s per programme second (370 s for a 50-s Reel).
- The decode that timed out in review round 5 (`test_the_same_parts_kept_flashing_fail`, 135 s with the F2 sketch)
  now has 13.3 window sums per decoded frame; `test_the_flash_count_stays_incremental` holds it and
  sub_band_checker_20 to a measured bound.
- Fixtures (55, one after another, not paired, on a shared host): 352 s on 49dd857a; 395 s for this round's
  build before its last two rule changes (the 3 newly failing sub_band fixtures 64 s of it, 4 s when they passed);
  704 s for the final run in the table above, on a busier host. The paired master figures above are the measure.

## Round 5 (review 1 round 4, NOT OK 7/10; operator decision A, decisions/QF-88.md)

- **Blocking 1, a rolling strobe given a counter-phase partner passed.** Round 4 excused every counter-phase frame
  (more than 25% of a field rising and more than 25% falling) unless more than 25% of the field's samples made the
  whole count themselves. Band A flashed 3 times, then band B 3 times, each a contiguous 27% of the field, with 4-px
  columns lit whenever A was not (masked_rolling). It passed as "3 flashes".
- **Blocking 2, fast cuts passed.** The fixture is 8 or 9 hard cuts a second between pictures whose bright third
  differs. At each cut one band goes dark and another lights: a general flash over a third of the field, 8 or 9 a
  second. The review ruled it a WCAG 2.3.1 hazard: cuts are "rapidly changing image sequences", and balance exempts
  only patterns under 0.1 degree. It passed, and so did 9 cuts a second and the 40-px-band variant.
- **The rule (the reviewer's candidate, `picture_qa._FlashStage`).** A counter-phase frame is excused as motion over
  fine detail (the whip) only when neither its rise nor its fall is coarse. Coarse means that more than 25% of the
  field changes by majority over boxes a tenth of the field's shorter side, about 1 degree
  (`PICTURE_QA_FLASH_COARSE` = 0.1, src/config.py). A coarse counter-phase frame always counts, like a
  single-direction one.
- **A further hole, closed by the builder.** masked_rolling made of 4-px columns still passed the candidate, because
  its counter-phase frames are fine. In masked_rolling_fine, A, B and the partner X are column classes; A and X flash
  3 times in place, then B 3 times. It is the same rolling hazard as blocking 1.
  - So a field's fine counter-phase transitions now also stand as far as more than 25% of its samples made them on
    fine counter-phase frames, on top of the transitions that always count.
  - As a count: s (the transitions that always count) + K (the fine level that more than a quarter of the samples
    reached), at most the field's own count.
  - The round-4 condition (more than 25% of the samples made all n) is kept as well. So the rule is stricter than the
    candidate everywhere, and never looser.
  - The whip, in_turn(9), still passes: 9 fine counter-phase frames, with each part changing on 6 of them. The whip
    followed by one flash fails.
- No threshold changed: 25%, 3 flashes a second, the 10-degree field, 0.10/0.80 and 0.2/0.8.

### Fixtures (270 x 480, 30 fps, as the regression test's `clip()`; 53fb572e = round 4)

| Fixture | want | 53fb572e | round 5 |
|---|---|---|---|
| masked_rolling (review, round 4) | fail | **pass** (3) | fail (6) |
| masked_rolling, red | fail | **pass** | fail (6, red) |
| masked_rolling_fine (4-px columns, builder) | fail | **pass** (3) | fail (6) |
| cuts_8_per_s / cuts_9_per_s | fail | **pass** (3 / 3) | fail (4 / 4.5) |
| cuts_8_band40 | fail | **pass** | fail (4) |
| cuts_8, red | fail | **pass** | fail (4, red) |
| cuts_8 with a fine 4-px partner on every other cut | fail | **pass** | fail (4) |
| 12 cuts a second between 4 bands of 26% of a field | fail | **pass** | fail (6) |
| the in-turn whip, then one full-frame flash | fail | fail | fail (6) |
| red halves at 10 Hz (counter-phase) | fail | fail | fail (10, red) |
| 12 cuts a second between 4 bands of exactly 25% | pass ("no more than 25%") | pass | pass (0) |
| cuts_6_per_s | pass | pass (2) | pass (3) |
| full-frame 3 Hz; halves at 3 Hz | pass | pass | pass (3) |
| the whip, in_turn(9) | pass | pass (3) | pass (3) |
| rolling bands white, red; blocks; 4-px columns; 2 each | fail | fail | fail (9, 9, 9, 9, 6) |
| full-frame 3.5 / 4 Hz; red 10 / 4 Hz; straddle; clustered | fail | fail | fail |
| moving band 3, 6, 10 px a frame; moving box 4 px | fail | fail | fail (15, 12, 7, 6) |
| moving box 8 px a frame | Known limits | pass (3) | pass (3) |
| 8 cuts a second between 3 pictures whose bright part is a class of 4-px columns | Known limits | pass (3) | pass (3) |
| one white bar crossing the frame at 48 px a frame | pass | pass (1) | pass (2) |
| three crops of a real dev photo, cut 8 a second | fail (the review's realistic cuts_8) | **pass** | fail (4) |
| a 3 s pan over a real dev photo at 25 / 60 px a frame | strict (review non-blocking 1) | fail | fail |

The regression test `test_a_coarse_counter_phase_change_is_never_excused_as_a_whip` (also in measure.py) holds the
first eight rows and masked_rolling_fine as must-fail cases. 9 of its 10 cases fail on 53fb572e by assertion, and all
10 pass here. The tenth, the whip followed by a flash, already failed on 53fb572e.

### The Amber Room second (dev run 1792790550, 3.7-5.4 s): a true hazard, and the gate is right

`amber_room_worst_field.jpg` shows the master's frames (extracted by FFmpeg) with the worst 10-degree field outlined.
`amber_room_worst_field.json` holds the numbers. Both come from `amber_room_trace.py`, `amber_room_boxes.py` and
`amber_room_sheet.py`, on the gate's own fine-stage decode.
- From 4.467 s the picture whips through the gilded panels for 5 frames (QF-04's retired zoom transition). It flashes
  full-frame yellow at 4.633 s and falls to the dark stat card at 4.700 s.
- In those 8 frames the worst field makes 7 transitions: up, down, up, counter-phase, counter-phase, up (the
  yellow), down. Each covers more than 25% of the field (26-69%). Both counter-phase frames are coarse: 38% rising
  and 37% falling by majority over 1-degree boxes, then 29% and 24%.
- The changes are large and in place, not fine texture sliding by:
  - frame to frame, 23-55% of the field's 1-degree boxes rise by 0.10 or more, and 17-37% fall;
  - within 3.7-4.7 s, 75% of the field's 1-degree boxes make 4 or more transitions of their own, 29% make 5 and 6%
    make 6;
  - only the field's mean luminance makes just 2.
- So under the reading this row has used since round 4, it is 3.5 flashes in a second: a WCAG 2.3.1 general-flash
  failure. That reading is "the combined area of flashes occurring concurrently", whichever parts of the field make
  them, with no balance exemption above 0.1 degree. Only the per-location reading passes it, at exactly 3, and rounds
  3 and 4 rejected that reading.
- **The master must be re-cut before it is posted.** The gate is not changed for it. The current composer makes a
  hard cut there (QF-04), so a re-render removes the transition.

### Real masters (one decode per master: round 4 / the candidate alone / round 5)

| | dev, 30 masters (output/run_*, read-only) | production-sample copy, 33 masters (hash-checked, counts only) |
|---|---|---|
| picture_flash fails | 0 / 1 / 4 | 0 / 0 / 5 |
| masters at 6 transitions, the limit (round 4 / round 5) | 5 / 3 | 7 / 4 |
| red, at most (round 5) | 2 | 0 |

- The Amber Room is the candidate's one failure (above). Round 6: it stays a reject; of the masters below, 2 pass, 4 are
  held for the operator's look and 1 stays a reject (round 6, above).
- The other 3 dev and 5 production-sample failures come from the builder's addition. Each is at 7 transitions (3.5
  flashes):
  - in each worst field, 6 transitions count anyway (single-direction or coarse counter-phase), and one fine
    counter-phase frame has more than 25% of the field changing at once;
  - this was checked with each field's own frames, not the shared approximation (scratch `exact.py`). For example,
    s = 6 plus one fine frame made by more than a quarter of the samples gives 7.
- By the field reading these are 3.5 flashes in a second. Only the per-location reading passes them, and it is also
  the only reading that passes masked_rolling_fine. Under decision A ("no reading that lets a 2.3.1 hazard pass,
  whatever the false-reject cost") they are counted.
- This is the round's cost, stated for the operator: with the candidate alone, 1 dev master and 0 production-sample
  masters fail, but masked_rolling_fine passes.

## Round 4 (review 1 round 3, NOT OK 6/10)

- **Blocking 1, flashes made in turn by different parts of a field passed.** Round 3 counted a field's transitions
  only as far as more than 25% of its samples made them each on their own. Three bands (or blocks, or 4-px columns),
  each a third of a field, flashing 3 times each in turn passed as "3 flashes", although the field shows 9 flashes in
  0.6 s, each over a third of it; so did a 15 Hz band moving 10 px a frame. WCAG 2.3.1 counts the flashes of the
  field and adds up "the combined area of flashes occurring concurrently", whichever pixels make them; the per-area
  reading is a second check, never a gate on its own.
- **The fix (the reviewer's measured fix, `picture_qa._FlashStage`).** Each field also counts its single-direction
  transitions (more than 25% rising, or falling, not both) over the same second, and those always count. The sample
  condition now decides only a count that needs the field's counter-phase frames (more than 25% rising and more than
  25% falling at once): it stands where more than 25% of the field made it itself, a pattern flashing in place, and
  not where parts of the field rose and fell in turn, the whip over detail. No threshold changed: 25%, 3 flashes a
  second, the 10-degree field, and the luminance and red rules are as before.
- **New must-fail regression tests** (`test_parts_of_a_field_flashing_in_turn_fail`, and in measure.py): rolling
  bands white and red, rolling blocks, rolling 4-px columns (each 3 x 3 flashes), rolling bands 2 flashes each
  (6 a second), and the 15 Hz band moving 10 px a frame. All six fail on round 3 (60e3ca75) and pass here.
- **Real masters:** 0 of 30 dev and 0 of 33 production-sample masters fail (round 3: 0 and 0). The worst count on a
  real master rises to 6 transitions (3 flashes, the limit) on 5 dev and 7 production-sample masters; none is over.

## Round 3 (review 1 round 2, NOT OK 7/10)

- **Blocking 1a, same-direction events.** Each 10-degree field keeps the direction of its last transition again. A
  rise after a rise (two areas going dark a frame apart, a still frame between) adds none: WCAG note 2 counts
  transitions between adjacent peaks and valleys. Round 2 forgot the direction on a still frame.
- **Blocking 1b, counter-phase counted twice.** A frame where more than 25% of a field rises and more than 25% falls
  is one transition, not two. Counter-phase checkerboards (12 across) and halves at 2 and 3 Hz now pass; at 10 Hz
  they still fail.
- **What the specified fix alone still did.** Built exactly as specified, it failed 4 of the 30 dev masters (round 2:
  1), in the same retired whips: motion over a detailed picture keeps a field a third rising and a third falling on
  consecutive frames, so every frame is one more transition. Behind them, the samples made 3 to 5 transitions each.
  In the Amber Room 1792790550's worst field, 0.7% of the samples made more than 6.
- **The rule kept** (decision: builder under the 2026-11-12 overnight delegation). A field's count over a second is
  the most transitions n that the field made, and that more than 25% of its samples also made themselves, on frames
  where some field rose or fell over 25%. This is the per-area reading of "the combined area of flashes occurring
  concurrently": a field's transitions count only as far as one area of it made them. Fine detail flickering
  between such frames is not part of a flash. No threshold changed: 25%, 3 flashes, the 10-degree field, and the
  luminance and red rules are as before.
- **Fixtures** (in the regression test and in measure.py):
  - pass: counter-phase at 2 and 3 Hz; the late fall after 3 flashes; parts of a field changing in turn for
    9 frames (each part flashes 3 times; the field-only count fails it);
  - fail: the same in-turn pattern held for a second (round 2 passed it), and a 10 Hz band drifting 3 px a frame
    (the sample condition does not excuse moving flashing);
  - still fail: every round-1 and round-2 strobe, counter-phase, off-grid and 0.1-degree fixture.
- **The reported count** is now the count the decision rests on. Round 2 said "the most anywhere: 3.5 flashes" in
  a pass sentence, which was the strict screen stage's count, where the fine stage had exempted it. A passing
  picture now never reports more than 3.
- **Non-blocking 1:** the config comment now describes the field-area rule.

## Round 2 (review 1, NOT OK 6/10)

- **Blocking 1, counter-phase flashing passed.** The rising and the falling area of a field are now counted apart
  (`picture_qa._FlashStage`). A field makes a transition on a frame where more than 25% of it rises, and another
  where more than 25% of it falls. A counter-phase checkerboard (squares 1/12, 1/6 and 1/4 of the width) and two
  halves alternating at 10 Hz now fail.
- **Blocking 2, the field sat on a fixed grid.** The share of a field is now an exact sliding window over integral
  images: every position on the grid, not a 6 x 6 cell grid. A strip of 30% or 32% of a field a quarter-cell off the
  old grid, and a 30% square off it, now fail; a 22% strip still passes.
- **The 0.1-degree rule.** WCAG exempts fine, balanced patterns with squares under 0.1 degree. A second, fine stage
  on the same decode settles it. FFmpeg's one process also writes the frame as 600 x 600 area means (0.05 degree
  each), and a 0.1-degree box (2 x 2 of them, at 0.05-degree steps) is what the fine stage counts. The fine stage runs
  only where the screen stage (the chroma grid, as before, which errs strict) finds a field failing. It replays the
  last 1.5 s it buffered. Counter-phase checkerboards at 1080 x 1920:
  - 0.1 degree (phases 0, 1/4, 1/2), 0.15, 0.2 and 1 degree fail;
  - 0.028, 0.04 and 0.05 degree pass.

  Squares between 0.05 and 0.1 degree are still counted: stricter than WCAG, never looser.
- **Blocking 3, the CPS rule failed renders the composer timed itself.** Option (a): `snap_to_cuts._valid` now
  refuses a move that would make a burst a reading-rate defect it was not (`video_composer.rate_defect`, shared
  with `reading_rates`, with 1 us of float slack). This keeps the timing code responsible for CPS (decision B); an
  allowance widened to the snap's 0.2 s would instead let a cut take 0.2 s off any fast burst. The raise stays as a
  backstop. The 0.10 s slack covers only the two-frame lead and the programme-start clamp, as before.
  - Reproduced with `cps_cuts.py`, adapted from the reviewer's probe: every real words.json through the real
    `build_subtitles`, once without cuts and with 20 random cut sets each. Failed builds: dev 0 of 735 (35 runs),
    production-sample copy 0 of 714 (34 runs); before the fix the reviewer measured failures in both.
  - True defects are still caught: the same narration with every burst hidden 0.3 s early fails 35/35 and 34/34.
- **Non-blocking, done.**
  - An ingest clip that could not be measured is recorded on its record (`picture_check: "not measured (...)"`)
    and shown on the card as an `ingest_picture` note.
  - A run without palette.json says on its CVD line that the gold-against-accent pair was not judged.
  - The area test now passes at exactly 25%, as WCAG says ("no more than"), and fails above it.
- **A rule tried in this round and dropped.** Counting each sample's own transitions failed 4 production-copy
  masters and 1 graphic clip: a fast count-up of large digits, and a slow pan over a halftone print. A moving
  texture changes each point at a different frame and never a quarter of a field at once; the field rule counts
  that correctly (fixture: a panning stripe pattern passes).

## What changed

| Gate | Where | Standard and thresholds (constants in `src/config.py` / `src/colour_vision.py`) | Blocks |
|---|---|---|---|
| `picture_flash` | `picture_qa.Flash`, on QF-10's single decode (`analysers()`; `METHOD_VERSION` 1 -> 2, so every stored measurement is redone once; the decode also writes the 600 x 600 flash grid) | W3C WCAG 2.2 SC 2.3.1, the normative "general flash and red flash thresholds". General transition: relative luminance changes >= 0.10 with the darker state < 0.80 (the same as ITU-R BT.1702's 20 cd/m2 below 160 cd/m2 on its 200 cd/m2 display). Red transition (note 3): > 0.2 in CIE 1976 u'v' to or from a saturated red, R/(R+G+B) >= 0.8. A 10-degree field (WCAG note 1's estimate, 341 x 256 on 1024 x 768: one third of each side) at any position makes a transition when more than 25% of it rises, or falls, at once; more than 3 flashes (6 transitions) of one field in any second fail. A counter-phase frame (more than 25% rising and more than 25% falling) always counts when its rise or fall is coarse (more than 25% of the field by majority over ~1-degree boxes, `PICTURE_QA_FLASH_COARSE`); a fine one counts where samples that made the count in place, alone or with coarse change on the same frame, take more than 25% of the field; a count only a motion test could settle is held (`needs_review`, round 6), and a fail is never held (round 7). The fine stage reads its 0.1-degree boxes twice, as they are and with solid change reaching one step, so squares over 0.1 degree count whole (round 7). Patterns finer than 0.05 degree are exempt (WCAG's fine-pattern exception; round 2). | yes, like every picture check; on old masters too (only picture_colour is grandfathered) |
| ingest check | `picture_qa.ingest_problems`, called by `footage.get_curated_footage` after `make_segment` (operator footage and approved archive film) and by `motion._gate` (AI clips; `GATE_VERSION` 2 -> 3) | black, freeze (Reel hold) and flash, one decode | the clip is not used; a clip that cannot be measured is used, recorded (`picture_check`) and noted on the card (`ingest_picture`), because the master's gate decode fails closed |
| `caption_rate` (CPS, option B) | `video_composer.reading_rates`, enforced in `build_subtitles`; recorded as `reading_rate` in the composition record; read by `release._reading_rate_checks` | A burst is a timing defect when it is shown faster than 17 cps (characters, spaces included, over its time on screen) AND for less time than its words hold the narration (first word to the next burst's first word, or its last word's end before a pause), by more than `CAPTION_CPS_TIMING_SLACK_S` (0.10 s: the two-frame lead, which the programme start clamps, plus a frame). `snap_to_cuts` never makes one (round 2). | a timing defect fails the render (`CaptionReadingRateError`) and the gate; the narration's own pace over 17 cps is a card note ("N of M bursts go by faster than 17 characters a second because the narration is that fast ..."), never a block |
| `graphic_colour_vision` (CVD, option C) | `colour_vision.check` on every rendered graphic still (`graphics/graphic_*.jpg`, the clip's drawn last frame) with the run's accent (`palette.json`); `release._colour_vision_checks` | Machado, Oliveira & Fernandes 2009 (IEEE TVCG 15(6)), severity 1.0, protanopia, deuteranopia and tritanopia, on linear RGB (matrices checked against the authors' table). A role (figure gold, the run's accent, chart teal and coral, the map's home fill and neighbour land) is present when >= 0.02% of the picture lies within 12 CIELAB units of its nominal colour (lightness down-weighted: the relief shades the home fill); its measured colour is the median of those pixels. Every present meaning pair must stay >= 10 CIEDE2000 apart in every simulation. **10 dE00 is the project's own margin, not a standard's** (about four times the large-patch just-noticeable difference, under the 14 of the closest token pair). The map pair is judged on maps and routes only (`PAIR_KINDS`). | yes |
| accent at the source | `art_direction.palette_from` | an accent `colour_vision.accent_problem` finds under 10 dE00 from the figure gold in any simulation is passed over for the next sampled hue, then the warm hue's complement, then the default TEAL | n/a |
| `graphic_contrast` (note) | `release._colour_vision_checks` | WCAG 2.1 SC 1.4.11, 3:1, each mark role against the band 3-9 px around it, in normal vision and each simulation | never: a minor note on the card. The shortfalls go to the freeze loop as a design criterion (FREEZE-RESUME-rulings) |

The card (`telegram_bot.release_lines`) lists a pass-with-note check as "(note, not blocking)" also when another check
blocks, as QF-10b does for the old-master note.

## Measurements

`python docs/reviews/quality-fixes-2026-11/QF-88/measure.py <repo root>` (base 5c7840e9 / this commit; this
commit's output is `after.json`, `acceptance_met: true`):

| | base | this commit |
|---|---|---|
| 10-Hz white strobe blocked by the release gate with a named reason ("flashes up to 10 times in a second ... WCAG 2.3.1") | no (no check) | yes |
| flash fixtures as WCAG says: fail 10 Hz red on black, 4 Hz, 10 Hz over 6% of the frame; pass 3 Hz, dim 10 Hz, 10 Hz over 2% | 0/6 | 6/6 |
| round 2: counter-phase checkerboards (12 and 4 across) and halves fail; a 32% strip off the old grid fails, a 22% strip passes; a 0.1-degree checkerboard at half a square's phase fails, a 0.04-degree one passes | 0/7 | 7/7 |
| round 3: pass counter-phase checkerboard (12 across) at 3 Hz, halves at 2 Hz, the late fall after 3 flashes, parts of a field changing in turn for 9 frames; fail the in-turn pattern held 1 s and a 10 Hz band drifting 3 px a frame | 0/6 | 6/6 |
| round 4: fail rolling bands (white, red), rolling blocks, rolling 4-px columns, rolling bands 2 flashes each, a 15 Hz band moving 10 px a frame | 0/6 | 6/6 |
| round 5: fail masked_rolling (white, red, 4-px columns), cuts 8 and 9 a second, 40-px bands, red cuts, cuts with a fine partner, 12 cuts a second between 26% bands, the whip followed by a flash; pass 6 cuts a second | 0/11 | 11/11 |
| round 6: fail sub_band_checker_20, sub_band_vstrip, sub_band_burst; hold coarse3_plus_fine_reversal | 0/4 | 4/4 |
| round 6: a held master is needs_review, says why, blocks a live post, and its report carries a JPEG contact sheet within 300 KB | - | yes |
| round 7: fail chk52_8, chk56_8, chk60_8, chk52_red, sb_b18_c16 (0.44-degree checkers over 52-60% of a field), sb_b22_c08, ip_b3_k6 and a 4 Hz strobe followed by a held second | 0/8 | 8/8 |
| round 7: a 4 Hz strobe followed by a held second is a fail, not held, blocks, and `clear_flash_hold` refuses it | - | yes |
| round 2: a cut 1-6 frames before a fast burst's boundary never makes a CPS defect | - | yes |
| a 20-cps burst the timing cuts short fails the build; the same words timed right build | no / - | yes / yes |
| a burst spoken at 20 cps and shown as long as it is spoken: noted, not failed | - | yes |
| the committed real map renders (brand-overhaul-2026-10-29, 3) pass the CVD gate | - | 3/3 |
| an accent a protanope sees as gold fails the gate; art_direction does not pick it | - | yes / yes |
| QF-10b's old-master note kept through the METHOD_VERSION 2 re-measure | - | yes |
| acceptance | false | true |

The regression test (tests/test_qf88_accessibility_gates.py, 79 tests in round 7, 70 in round 6, 64 in round 5; round 3 added the counter-phase
2-3 Hz, late-fall, in-turn, drifting-band and passing-count tests, round 4 the six rolling and moving-band ones, round
5 the ten masked-rolling and cuts ones, round 6 the three sub-band ones, the hold and the window-sum bound, round 7 the five checkers, the three fails beside a held stretch and the recorded round-6 report) adds the reviewer's
counter-phase, off-grid and 0.1-degree fixtures, the snap case, a panning texture that must pass, the ingest note and
the missing-palette sentence to the red flash on a grey of equal luminance
(caught only by the chromaticity rule), a fast fade (one transition, not many), the footage and AI-clip ingest, the
card note beside a block, a real `render_map` through the CVD gate and the map pair's kind scope.

### Real data (`real_data.py`; committed output `real_data_dev.json`, `real_data_prod.json` counts only)

Production sample: a fresh scratch copy of the production sample's output, deleted after; counts only
(decisions/PROD-SAMPLE-counts-only). Round 7 measured the masters, graphic clips and ingest clips again
(`--parts masters,graphics,ingest`); the caption and CVD rows are round 2's run (that code is unchanged).

| | dev | production-sample copy |
|---|---|---|
| masters, release gate twice (as QF-10b, then this row) | 30 | 33 |
| picture_flash, round 7: fail / held (round 6: 1 / 2 and 2 / 2; round 5: fail 4 and 5; round 4: 0 and 0) | 1 / 3 (the Amber Room, a true hazard / both Richat masters, Roman concrete) | 1 / 4 (one true hazard / P1, P2, P3, P5: Round 7 above) |
| held masters' contact sheets, largest | 300,210 bytes | 288,793 bytes |
| other picture blocks | 1 (Richat deep dive picture_black, the known true finding) | 0 |
| old masters noted by QF-10b and still noted after the re-measure | 29/29 (the 30th was never gated: no report, blocks as before) | 33/33 |
| graphic clips, picture_flash fails (round 7) | 0 of 64 (1 truncated Odessa clip refused, as in QF-10; 65 found) | 0 of 86 |
| AI motion / footage segments rejected at ingest (round 7) | 0/7, 0/2 | none in the sample |
| operator film (assets), cut as ingest cuts it, rejected | 0/1 | - |
| caption bursts (delivered `*_captions.ass` against words.json) | 1,014 in 29 masters | 1,115 in 33 masters |
| bursts over 17 cps / over 20 cps | 350 / 84 | 337 / 96 |
| caption timing defects (would fail the build) | 0 | 0 |
| real narration through the real build_subtitles, 20 random cut sets a run (`cps_cuts.json`), builds failed | 0 of 735 | 0 of 714 |
| the same narration timed 0.3 s short, caught | 35 of 35 | 34 of 34 |
| masters noted for the narration's pace | 29/29 | 33/33 |
| graphics through the CVD gate, failed | 0 of 64 | 0 of 86 |
| closest meaning pair on a real graphic (min over simulations, dE00) | 20.8 | 20.8 |
| graphics with a 1.4.11 note | 2 | 3 |
| run accents rejected at the source | 0 of 10 | 0 of 11 |
| stills (stories, carousel, covers, grid tiles) as a stress set, failed | 0 of 371 | 0 of 408 |

Round 2's three "true findings" (the dev Amber Room 1792790550 and 2 production-sample masters, 7 transitions in a
second) were false rejects of round 2's counter (review 1, round 2): same-direction events and counter-phase frames
counted twice. Rounds 3 and 4 passed all three, as they passed every master. Round 5 counts each transition once and
fails the Amber Room again for a different reason: its coarse counter-phase frames now count (inspected frame by
frame above, a true hazard). Before round 5:
- the field-only count with the review's two fixes would still fail 4 of 30 dev masters (whips; the samples behind the
  transitions made 3-5 each), and the sample condition alone, without the "concurrently" gating, 1 production-sample
  master (fine detail flickering between the field's transitions took a field to 26.2% against 25%);
- the Richat deep dive's old fade-through-black (QF-10) is the one remaining picture block.

False rejects found and fixed while building, before any threshold was kept:
- The first flash rule counted each frame of a fast fade as a transition; 6 of the first 9 dev masters failed
  (the Amber Room photo -> yellow -> stat card whip, among others). A field's run of frames in one direction is now
  one transition (round 1), with rising and falling counted apart (round 2).
- The first red rule used the Harding-style (R-G-B) x 320 metric. WCAG 2.2's normative text uses u'v' chromaticity,
  so the rule was replaced.
- The first CPS rule (shown faster than spoken by 0.5 cps) failed every back-to-back burst by its two-frame lead,
  and 11 existing caption tests. The comparison now uses the time the words hold the narration. On the production
  copy, 17 "defects" in 3 masters were the survey's own word matching, thrown off by "..." tokens; matching skips
  them now, and there are 0.
- The map pair (home fill and land) matched photo colours on 4 dev and 3 production stills and fell to 8.0-9.8 dE00.
  It is now judged on maps and routes only. Stat cards with photo grounds had come to 12.1.

Run time:
- A one-off timing (not in measure.py's output): round 2 measures a 49.3 s dev Reel in 14.0 s on all threads and
  47.3 s on one core (0.96 wall s per programme second), with 5.7 CPU s in the flash analyser. The fine stage costs
  about 170 ms a frame, but it runs only around
  a failing field; a calm frame costs 7 ms.
- The full release gate (twice per master in `real_data.py`), the second check: round 5 32.4 s median and 146.3 s
  max (the deep dive) per dev master, 27.6 s and 48.5 s on the production-sample copy; round 6 24.5 s and 141.7 s,
  25.3 s and 63.2 s (with the hold's contact sheet), on a shared host.
- QF-10's limit is 120 s + 5 s per programme second. Before this row the one-core worst case was 1.48 wall s per
  programme second, so a 360 s programme projects to about 534-605 s against the 1,920 s limit.
- The CVD check takes about 0.5 s per graphic, and it is cached by the graphic's sha256 for the card's re-checks.

## Downstream effects

- Every master is measured again once (METHOD_VERSION 2): one decode the first time each card is shown after
  deploy, and the cost of a sha256 after that.
- Reports gain `picture_flash`, `caption_rate` (new renders only), `graphic_colour_vision` (runs with graphics) and,
  when there is a shortfall, `graphic_contrast`. Readers (card, `release_problems`, control, improve, card_ledger,
  audience, probe, backup) read checks generically.
- The composition record gains `reading_rate`. AI motion clips accepted under GATE_VERSION 2 are measured again
  before reuse; all checks are local, with no paid call.
- `art_direction` picks the same accent as before for every real run: all 21 real accents clear 10 dE00, the closest
  being the dev coral at 15.7 under tritanopia. Its order now tries every sampled hue before falling back, where
  before it took the single best one.
- Round 6, the hold: picture_flash may now be `needs_review` (`hold: true`, `held`, `contact_sheets`). Readers that
  treat `needs_review` as blocking a live post (`release.blocking_checks`, the card, `card_ledger`, `control`) hold it
  without change; a dry run lets it through as before (nothing is posted). The card's gate line reads "held: possible
  flash; needs your look" when a hold is all that blocks. `main.PipelineCoordinator._held_for_look` sends a held run
  to review instead of letting the auto-approve flag decide it (an alert says why). The contact sheet
  (`<master stem>.flash_hold_<n>.jpg`) and the operator's clearance (`flash_review.json`) sit in the run folder and
  are backed up with it. Ingest (footage, AI clips) treats a hold as no problem: the master's own check holds it.
- Round 6: `review clear-flash <run> [--looked]` (main.py) prints the held stretch and the contact sheet, and with
  `--looked` writes the clearance for these bytes. It changes no Telegram screen; a card control is for a design
  under the two-card rules (QF-88b).
- Branch-only test change: tests/test_qf10_picture_gate.py `PICTURE` gains `picture_flash`. The exact set of picture
  checks is asserted there, and this row adds one.

## Evidence

- `map_cvd_simulations.jpg`: a real dev locator map (Amber Room, Catherine Palace) in normal vision, protanopia,
  deuteranopia and tritanopia (quarter size). The home fill against the neighbours stays 21.3-23.2 dE00 apart.
- `amber_room_worst_field.jpg` (round 5; it replaces round 2's `amber_room_worst_second_flash.jpg`): the Amber Room
  master (1792790550), every frame from 4.400 to 4.767 s, extracted by FFmpeg from the master. The worst 10-degree
  field of the gate's fine stage is outlined. Under each frame are the transition it makes and the shares rising and
  falling (coarse share in brackets). Below are the field's luminance curves: the mean, and its 100 one-degree boxes.
  `amber_room_worst_field.json` holds the figures, made by `amber_room_trace.py` (the gate's own decode, recorded),
  `amber_room_boxes.py` and `amber_room_sheet.py`. Round 5 counts 7 transitions there (3.5 flashes) and fails it;
  the master must be re-cut.
- `roman_held_stretch.jpg` (round 7): the contact sheet for the dev Roman concrete master 1793163918, held by
  this round (4.30-5.97 s, 51 frames, 300,210 bytes): a zoom-whip through rough concrete into a flat amber card.
- `richat_held_stretch.jpg` (round 6): the contact sheet the release report carries for a held stretch, made by
  `picture_qa.hold_sheet` from the dev Richat master 1793272675, 2.70-4.37 s (every frame, 51, labelled with its time
  and index; 289 KB).

## Known limits

- The 10-degree field is WCAG's desktop estimate, one third of each side. On a phone the true field is a larger share
  of the screen, so the rule is stricter than WCAG there, never looser.
- Single-direction and coarse counter-phase transitions always count, whichever parts of the field make them. Only
  fine counter-phase frames are limited to what more than a quarter of the field made sample by sample (round 5),
  and they add to the transitions that always count. So a counter-phase pattern finer than about 1 degree that
  changes in turn passes while no part of it flashes more than 3 times. An example is 8 cuts a second between
  pictures whose bright part is a class of 4-px columns: each point flashes about 2.7 times a second. This is the
  whip excuse, and a count of every field transition would fail it.
- The coarse test is a majority over boxes a tenth of the field's shorter side, about 1 degree. Detail between about
  0.1 degree and 1 degree is "fine" here, so it is judged sample by sample rather than as one area.
- One to five fine counter-phase transitions in place beside the transitions that always count are held, not failed
  (round 6), and so is the in-place condition (F2) reached with one to five fine counter-phase frames (round 7,
  sub_band_checker_24 and _c10 among them): the gate cannot tell a fine pattern reversing in place from a whip over detail without a motion test
  (QF-88b, proposed). A held master waits for the operator's look and never posts without it.
- The in-place condition (F2) fails a field where coarse change plus samples that flipped n times in the second
  take it over 25% on at least 6 of its fine counter-phase frames; with fewer it holds (round 7).
- The second reading reaches out only from solid change (2 x 2 boxes). Squares of exactly 0.1 degree over part of
  a field (single boxes changing) keep round 6's edge loss: they count where the boxes themselves take a quarter of
  the field (a whole field of them fails), not their whole area. Reaching out from single boxes failed the
  WCAG-exempt 0.04-degree checkerboard by its moire.
- A failing frame on which the first reading fails is not asked of the second, so a fail's wording can omit
  "saturated red" that only the second reading found; it fails either way.
- A failing frame reports the count the cheaper conditions give, so a fail's "up to N" can be half a flash under
  the in-place condition's own count (sub_band_checker_10hz: 4.5, not 5).
- The fields are fixed on the screen. A small strobe moving fast enough passes, although a viewer tracking it sees it
  flash at full rate. The fixture is a 10 Hz square, 0.36 of a field, moving 8 px a frame on the 270 x 480 frame; the
  8-px box passes at 3 flashes under every screen-fixed reading. Review 1, round 4 ruled that this is not a 2.3.1
  failure as written (WCAG fixes the field "anywhere on the displayed screen"); a pursuit-compensated field would go
  beyond the standard. Harding-type tools share this limit.
- A pattern moving across the frame (a pan over stripes or a halftone) is not counted while under a quarter of a
  field changes per frame. A faster pan, where more than a quarter changes at once, counts as flashing. Harding's
  separate spatial-pattern test is not implemented.
- Ofcom's extended-flash rule (sustained flashing at 2-3 a second over 5 s) is not implemented; WCAG 2.3.1 has no
  such rule.
- CVD roles are found by colour. A role drawn far from its token (a heavily graded fill) is absent rather than
  measured, so the pair is not judged. TEAL and CORAL series are not drawn by any published renderer today.
- CPS on masters rendered before this row: no `reading_rate` record, so no claim is made. Their captions were measured
  from the delivered files for this README only.
