# QF-05 Whoosh placement (2026-11-05)

**Finding.** The music bed fired a transition whoosh at every pause of 0.2 s or more, or at a sentence end, with at
least 3.5 s between them, whenever no cue timestamps were passed (`AudioEngine.generate_ambient_bed`). The Reel path
(`main.py`, `_produce_bed_and_render`) passes no cues, so a Reel got 5 to 7 whooshes, most of them where the picture
does not cut, and a 16:9 programme on that path got 30.

**Fix.**
- `AudioEngine.transition_whoosh_times` now decides where whooshes go. It uses two kinds of anchor, in this order:
  the act transitions (`cue_timestamps`), then the reveals. The reveals are the sound plan's. With no plan, it uses
  the narration's first revelation word, and only when the cuts are known. Each anchor takes the scene cut nearest
  it, but only a cut within `TRANSITION_WHOOSH_SNAP` (1.0 s). An anchor with no cut that close gets no whoosh.
- A Reel gets at most `TRANSITION_WHOOSH_MAX` (2) whooshes. A documentary gets one per act transition where it has
  more acts than that. Whooshes are at least 2.5 s apart. None falls within 0.5 s of the start, the end, or the end
  card, which has its own whoosh.
- Each whoosh is mixed `TRANSITION_WHOOSH_DB` (-6 dB) under its old level.
- The pause/punctuation rule is gone.
- `main.py` passes the picture's real cuts at bed time, using the same rules the render plans its scenes with:
  - Reel: the beat boundaries from `segment_beats`, through `caption_cut_times`.
  - Deep dive: the cuts from `documentary_cuts`.
  - Both bed checkpoints now record the cuts.

## Metric (measure.py, the eight fixture runs)

`python measure.py <repo root>` makes each bed the way `main.py` calls `generate_ambient_bed` at that commit. That
means the real engine, the licensed `sfx_whoosh.wav` recording and the run's own words, sound plan and voiceover
length. It makes each bed twice, once as it is and once with the whoosh recording silenced. The difference between
the two is the whoosh layer, so each burst in it is one whoosh. `before.json` is the immediate parent b97dcea;
`after.json` / `measurements.json` is the fix.

| Run | Path | Whooshes before | Whooshes after | On a cut or reveal, after |
|---|---|---|---|---|
| amber_room_heist | Reel | 7 | 1 | 1 / 1 |
| gol_gumbaz_whispering_gallery | Reel | 6 | 1 | 1 / 1 |
| nazca_lines_runway_paradox | Reel | 7 | 1 | 1 / 1 |
| richat_structure_sahara_eye 1793474843 | Reel | 6 | 1 | 1 / 1 |
| tunguska_event_1908 | Reel | 6 | 2 | 2 / 2 |
| unfinished_obelisk_aswan | Reel | 5 | 2 | 2 / 2 |
| voynich_manuscript | Reel | 5 | 1 | 1 / 1 |
| richat_structure_sahara_eye 1792740961_long | 16:9 deep dive (act cues) | 4 | 4 | 4 / 4 (each act start's own cut) |
| same narration | 16:9 render path, no act cues | 30 | 0 | none of its reveals is within 1 s of a cut |

- **Reels.** Whooshes per Reel fell from 5-7 to 1-2, with a maximum of 2. Before, 19 of the 42 Reel whooshes landed
  on neither a scene cut nor a reveal. After, none do.
- **Level.** The mean whoosh RMS went from -36.03 dBFS to -42.03 dBFS, which is -6.00 dB.

Where a Reel has one whoosh, its second reveal either has no cut within 1 s or falls within 0.5 s of the loop
bridge. The Amber Room's 44.07 s reveal is one example. Such a reveal keeps its music swell and its sub-bass hit, and
gets no whoosh.

## Inputs and limits

- `inputs.json` comes from `build_inputs.py`, which is read-only over `output/` and imports no `src`. For each run it
  holds:
  - the voiceover length, from ffprobe;
  - `sound_plan.json`;
  - the delivery profile;
  - a deep dive's chapter starts (its act cues);
  - the master's composition scene count;
  - `picture_hold.merged`.

  The words come from the committed fixture copies.
- **Scene cuts.** No run recorded its final scene durations. The fixture timelines' `start_s_est` values are clip
  lengths less the crossfade, and they run past the narration. So the cuts here are the ones the pipeline plans,
  from `segment_beats` (Reel) and `documentary_cuts` (deep dive).
  - For the Reels whose composition record exists, its scene count matches the beat count: Amber 12, Gol Gumbaz 9,
    Tunguska 10, Voynich 11.
  - Gol Gumbaz and Richat 1793474843 recorded `picture_hold.merged == []`, so their plans cut on every beat
    boundary.
- **Final picture holds and repairs.** The first bed uses the planned beat cuts. Before
  each master render, the final scene durations are passed back to the bed builder.
  If a hold or repair removed a boundary, the bed is remixed locally from the saved
  music selection and sound plan, with its provenance and checkpoint updated before
  the master hashes the audio. An unchanged plan reuses the mix. This makes the
  whooshes follow the final plan even when the pool is short, without another music
  selection or provider request. The new regression covers a hold, a repeated render,
  and a second repaired plan; existing expected values remain unchanged.

## Rebased validation

All four regression tests fail on the immediate parent (52.51 s). Initial audio
coverage: 26 passed (64.84 s). Broader orchestration coverage: 59 passed and one
resume-adapter failure; the positional callback fix leaves that existing test
unchanged. Final regression, resume and identifier checks: 7 passed (51.61 s).
Ruff: no new per-file findings. Fresh before/after measurements reproduce the
table above. Whole-suite validation and independent review remain required.

## Integration fixture correction

The background-preparation test mock now reads the prepared-image future before
the appended final-cut bed callback. The old mock failed with AttributeError on
this row (1 failed in40.06s). All original assertions stay unchanged. The full
concurrency module plus QF05 regressions pass:18passed in44.21s. Application code
and the measured audio output are unchanged by this correction.
