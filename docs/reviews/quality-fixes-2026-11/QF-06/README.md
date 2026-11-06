# QF-06 SFX body and headroom (2026-11-05)

**Finding** (project quality review 2026-11-05, item 8). The synthesized impact hits sit at 45-46 Hz, which phones
cannot play. The review also said they drive the master limiter about 9 dB and duck the narrator. SFX are set to the
RMS of a synthesized stand-in, not by their peak.

**Fix.**
- **Impacts.** `AudioEngine._synth_sub_bass_braam` now makes a body at `IMPACT_BODY_HZ` (110 Hz). The body falls to
  110 Hz from 1.35 x over the first 0.1 s, so it stays within 90-200 Hz. A 1-3 kHz noise transient
  (`IMPACT_TRANSIENT_HZ`) sits on the attack. The opening hit no longer passes `f0=45.0`.
- **Licensed braam.** The recording `sfx_impact_braam.wav` has all of its energy below 80 Hz (measured, 61.5 Hz
  strongest). It is kept as the hit's sub-bass weight, `IMPACT_SUB_DB` (-15 dB) under the body (`_impact`), so a
  phone still plays the body.
- **Every effect is peak-normalised.** `_mix_effect` handles every one-shot effect that enters the bed: the opening,
  act-cue and reveal hits, the transition whooshes, the palette cues (after their category filter and room reverb)
  and the end card's whoosh and bell. Each one enters at `SFX_PEAK_DBFS` (-17 dBFS, `src/config.py`) before its cue
  gain.
  - `_effect` keeps its contract: the recording at the stand-in's RMS, which `tests/test_sfx_rights.py` checks.
  - The end card's whoosh and bell keep their levels. `END_CARD_WHOOSH_DB` / `END_CARD_BELL_DB` are now peak offsets
    (7.5 / 12.3 dB), giving the same -17.0 / -12.2 dBFS peaks as before.
- **Bed bus limiter.** `AudioEngine.limit_bus` limits the bed bus (music, setting layer and effects together) at
  `SFX_BUS_CEILING_DBFS` (-6 dBFS) before the narration is mixed in. It is a look-ahead peak limiter: 5 ms look-ahead,
  120 ms release, 1 ms gain blocks. A bed below the ceiling passes through untouched.

## Metric (measure.py, the eight fixture runs)

`python measure.py <repo root>` makes each run's bed the way `main.py` does, then masters it the way
`VideoComposer.render` does:
- `generate_ambient_bed`;
- `calibrate_gain` and `end_card_level`;
- the `_audio_chain` + `_master_chain` + `_seam_tail` ffmpeg graph.

It does this twice, once as is and once with every one-shot effect silenced, so the difference is the SFX layer.
For each SFX cue it reports:
- the master limiter's largest gain reduction (GR), and the same window with no SFX;
- how far the narration's momentary loudness moves against the no-SFX master.

`before.json` is the start commit 96e8db4 (src tree fb491fc); `measurements.json` is the fix (src tree c3429b5). Each file records the src tree it measured in `code`. Both use committed inputs only: the words
from the fixtures, generated narration and music stand-ins, and `data/sfx`. `before_real_audio.json` /
`after_real_audio.json` are the same runs with `RR_FIXTURE_REPO` set, which uses each run's own voiceover and music
track. `cues_before_after.txt` is the per-cue table, written by `python table.py`.

| Acceptance | Before (96e8db4) | After |
|---|---|---|
| Lowest f0 of a synthesized hit | **45.3 Hz** (46.0 at the default; 99 % of the energy under 80 Hz) | **110.0 Hz** (0 % under 80 Hz, 99 % at 90-200 Hz) |
| Licensed hit as mixed | 61.5 Hz strongest, 99.9 % under 80 Hz | 110 Hz strongest, 58-60 % at 90-200 Hz, 39-41 % sub-bass under it |
| Effect peaks as they enter the bed (before cue gain) | -17.3 to **+2.95** dBFS (the fire crackle) | **-17.0** dBFS, every one |
| Max master-limiter GR at any SFX cue: stand-ins | 0.89 dB (whoosh at 77.5 s; 0.08 dB without SFX) | **0.13 dB** |
| Max master-limiter GR at any SFX cue: real audio | 2.64 dB (2.61 dB without SFX) | **2.59 dB** (2.61 dB without SFX) |
| Max narration change at any cue: stand-ins / real | 0.16 / 0.25 LU | **0.01 / 0.08 LU** |
| Six effects piled on one spoken word (regression test) | GR 0.11 dB, narration 0.10 LU | GR 0.11 dB, narration 0.0005 LU |

**The "~9 dB" limiter claim does not reproduce.** On the real pipeline at the start commit, no SFX cue in any of the
eight runs reached 3 dB of master-limiter gain reduction, with stand-in or real audio. Even a six-effect pile-up on a
spoken word stayed at 0.11 dB.
- With the real voiceovers, the 1-2.6 dB at the cues is the narration's own peaks: the no-SFX master shows the same
  gain reduction in the same window.
- The largest change the effects themselves caused was 0.8 dB (a whoosh in the deep dive) on the stand-ins, and
  0.46 dB (the deep dive's opening hit) on real audio.
- The bed peaks at about -12 dBFS. The master gain is +3.2 to +4.7 dB and the ceiling is -1.9 dBFS.
- The GR and narration criteria were therefore already met at the start. The fix keeps them met and removes the
  SFX share of both.
- At -6 dBFS the bus limiter did not act on any of the eight real beds, which peak at -7.8 to -12.2 dBFS. It is a
  guard against a pile-up, not a level change. In an exploratory run at -9 dBFS it limited 0.2-5.6 % of the samples of four real beds, which
  was the music's own swells.

## Level changes (an item to check by listening)

Peak normalisation changes each recording's loudness by its crest factor. `cues_before_after.txt` has the table:
- The whoosh does not change (-36.0 dBFS RMS), and the end-card whoosh and bell keep their peaks.
- The hit is 7.0 dB lower in RMS at the same peak. Its energy is now at 110 Hz, where the ear and a phone hear it,
  instead of at 61 Hz.
- Palette cues change by -16 to +6 dB RMS. Spiky recordings drop most: fire crackle -16.1, horse hooves -14.8,
  footsteps -13.9. Dense ones rise: desert wind +5.8.
- Before the fix, every palette cue already sat 15-35 dB under the music bed (-41 to -62 dBFS RMS against the bed's
  -26). After the fix they sit 19-46 dB under it. That they are buried was true before this change, so their level
  is a separate listening item. The cue gains in `src/sfx_palette.py` are unchanged.

## Not changed

- The deep dive's act-cue riser is still a 40-85 Hz sweep. It is not a hit and was not among the cited lines. The
  bus limiter covers its peaks.
- The review also suggested a `highpass=300` phone check in QA. It is not part of this finding's acceptance and is
  not added here.

## Rebased validation

Current acceptance uses the default generated fixtures. Recorded-audio JSONs and
comparison text are historical observations from the original build, not current
acceptance; their ignored inputs are unnecessary for the default reproduction.
The measurement's calibration and audio graphs use QF-03's sample-exact endpoint.
Fresh immediate-parent and candidate runs reproduce the generated metrics above.
Regression on the parent: 3 failed, 1 passed (96.42 s); the original master-headroom
criterion was already met. Candidate audio coverage: 29 passed (101.97 s).
The descendant QF-07 identifier checks passed all 8 tests (51.08 s), covering these
files too. Ruff has no new per-file findings. Existing tests/expectations unchanged.
