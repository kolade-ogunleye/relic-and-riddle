# QF-118: the voice and bed mix is deterministic

Proposal (queue, from QF-116): `tests/test_release_integrity.py::test_real_ffmpeg_mix_preserves_stereo_bed` is
nondeterministic on ffmpeg 8.1 (side RMS 0.085-0.099 over 35 local runs; 0.0468 once on CI's 6.1), from the
`end=None` input race in the mix it exercises. Make the mix deterministic in the code under test if production
mixes have the same race, else in the test fixture, and keep the 0.05 threshold. Check whether real masters are
affected.

## Cause

`video_composer._audio_chain` feeds the ducked bed through `sidechaincompress` (the bed, keyed by the voice's
speech band). Without `end`, neither the bed nor the key was padded, so both reached the filter with an EOF.
`sidechaincompress` forwards the first EOF it sees and drops what the other input still had queued (the race the
2026-11-04 fix fcb822e5 removed for `end` mixes). Which EOF arrives first depends on thread timing, so the bed's
last samples were in the mix on some runs and missing on others.

## Change

The bed and the key are now padded without end (`apad`) in every mix, not only when `end` is given. No input
reaches `sidechaincompress` with an EOF, so nothing is dropped. The mix ends where it always did: at the voice's
end plus its finite pad (`amix=duration=first`), or by sample count at `end`. The test and its 0.05 threshold are
unchanged.

Production mixes are not affected. Every production call passes `end`: `compose_video` (`vo_dur + card_seconds`),
both calibrate_gain calls, and `end_card_level`, which runs only with a card, so `pad > 0`.

- Analytically: whenever `end` is given, the base already evaluated `bed_pad` to `",apad"` and `key_pad` to
  `f",{'apad,'[:-1]}"` = `",apad"`. Those are exactly the values the change now uses on every path, so only the
  `end=None` graph changes. Only the tests, the defaults and other rows' measure scripts use that graph.
- Measured (`measure.py --base-root`, graphs.json): 585 of 585 production graphs are byte-identical between
  e49616fc and this commit.
  - Every graph compose_video builds was compared: calibrate_gain's two passes, then end_card_level and the second
    calibrate_gain with the card's level, then the delivered chain with `_master_chain` and `_seam_tail`.
  - Grid: voices of 3 to 120 s, card on and off, card_db 0 / 1.5 / -2.6, gain -3.2 / 0 / 4.75, three input
    indices.

## Measurements (`measure.py`, generated inputs; before = e49616fc, after = this commit; ffmpeg 8.1)

The test's own mix (1 s silent voice, a 0.1 440 Hz / 880 Hz stereo bed), 35 runs per variant, on a loaded PC
(3 other heavy runs at the time).

| variant | side RMS, min to max | distinct outputs |
|---|---|---|
| without `end` (the test's call), before | 0.089757 to 0.099194 | 4 |
| without `end`, after | 0.099194 to 0.099194 | 1 |
| sample-exact `end` (production), before and after | 0.099194 to 0.099194 | 1 |

After the change, the open mix's samples equal the sample-exact mix's. `acceptance_met` false -> true. The before
tree's count of distinct outputs varies from pass to pass, as a race's does. The after tree's is 1 on every pass.

## Real masters (development runs, read only; `measure.py --dev-output <main checkout>/output`, dev_masters.json)

These are counts only. There are 29 masters under `output/run_*`, made from 2026-10-22 to 2026-10-31. None was made
on or after 2026-11-04, so none uses the current chain. Every later master uses the unchanged `end` path.

- None is mono: the smallest side RMS is 0.028.
- Their release reports show no failing audio check (channels, sample rate, loudness, true peak, sync drift,
  duration).
- 6 masters end on 9.3 to 36.0 ms of digital zeros. That is the `end` mix's tail defect, which fcb822e5 fixed on
  2026-11-04, after these masters were made. It is not this race.

The figures come from these sources:
- the table above: `measure.py REPO_ROOT` (after.json; before.json on e49616fc);
- 585: `--base-root` (graphs.json);
- 29, 0.028, 6, 9.3 to 36.0 ms, 0 failing: `--dev-output` (dev_masters.json).

## Tests

- `tests/test_qf118_deterministic_mix.py`:
  - Every input of the sidechain is padded without end, with and without a pad.
  - Eight runs of the open mix give identical samples, equal to the sample-exact mix.
  - On e49616fc all 3 fail by assertion: 4 distinct outputs in 8 runs.
- `tests/test_loop_seam_exact_end_2026_11_04.py` (branch-only): it asserted that the open-end chain has no `apad`,
  which is the racy graph itself. That assertion is replaced by the new invariant: no cut and no pad on the voice,
  and the bed and the key padded without end.
- `tests/test_release_integrity.py::test_real_ffmpeg_mix_preserves_stereo_bed` is unchanged and keeps the 0.05
  threshold.
