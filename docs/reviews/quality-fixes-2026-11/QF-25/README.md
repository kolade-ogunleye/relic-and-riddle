# QF-25: design_cards wired into the composer

Before this row nothing imported `src/design_cards.py`: the checked hook frame, chapter card and myth card were drawn
by tests only, never composed into a master, and the composer had no way to honour the hook's
`caption_clear_until_s`. `src/card_composition.py` now binds each rendered card to its signed source field, its
design and contrast records and its narration interval, and the normal pipeline composes it.

Ported from the unreviewed old-chain build 9468fdc8 onto QF-24 (f203266f) and reconciled with what landed since:
QF-94 fallback fonts as render sources, QF-10 full-image spans in the manifest, QF-83 loop-follow overlay key,
QF-81 lazy carousels, QF-86 graphics grade bias, `fact_recheck` signing kind. The old chain's
`illustration_scenes` lines and `run_cost.submit` calls are not carried (not on this base).

## What is composed

- **Hook (9:16, frame 0).** Made when the sheet has a checked statement of at most 30 characters and 6 words and
  the hero photo is a strong shot. The master opens on it: the narration gets a silent pre-roll of
  `ceil(caption_clear_until_s x 30) / 30` = 1.40 s (decoded voice samples kept exactly, every word time shifted by
  the same amount), the selected music is re-mixed once through the normal mixer on that clock, and no caption
  starts before 1.40 s. The release gate checks the shifted word clock against the signed hook contract; the
  automated look gets the opening as its own frame.
- **Chapter card (16:9).** One per documentary act, held its full 3.5 s at the act's start; picture cuts and sound
  cues use the same intervals. Each act title gets its own trace/evidence check before the voice is made
  (`check_chapter_fields`): two checker calls per act, through the existing checkers. A title that fails, or a
  chapter card that cannot be drawn, stops the documentary (`require_chapters`); nothing is truncated.
- **Myth card (16:9 and 9:16).** Starts on the spoken myth; its first correction line is fully visible at the
  spoken correction (within half a frame). A too-fast exchange refuses. Ordinary captions keep running.
- **Quote card.** Held: `QUOTE_CARDS_ENABLED` stays false (ruling Q10); a quote card refuses and is never composed.

## Measurements

`python measure.py <repo-root>` renders the native masters through the row's own tests (committed fixtures,
generated photos and audio, offline checkers) and measures the decoded masters.

| | before (f203266f, `before.json`) | after (`after.json`) |
|---|---|---|
| card designs composed into a master | 0 (graphics.make_all returns no card) | 3: hook, chapter, myth (16:9 and 9:16) |
| hook first caption | no hook | 1.4 s; 0 captions before caption_clear_until_s 1.4 s |
| myth correction sync error | no card | 0.0 s (16:9 and 9:16) |
| card in master vs graded card, mean abs RGB (above the caption band) | - | chapter 1.066, myth 1.441 / 1.44 (limit in the tests: 5) |
| caption/graphic overlap records | - | 0 |
| quote card | not composed | held, refused |

The regression test `tests/test_qf25_cards_composed.py` fails on f203266f by assertion (no chapter card from
graphics.make_all; the composer takes no opening card) and passes here; its hook case also runs the release gate's
every-frame picture checks (QF-10) and every-sample sound checks (QF-11) on the hook master: none fail.

Previews (half size, from the decoded masters): [hook](previews/hook.jpg),
[first caption after the hook](previews/hook-then-caption.jpg), [chapter](previews/chapter.jpg),
[myth 16:9](previews/mythfact.jpg), [myth 9:16](previews/mythfact_portrait.jpg). Fixture copy and generated photos,
not publication copy.

## Real runs (`real_runs.py`, counts only)

`python real_runs.py <repo-root> <output-dir> [--fact-sheets <dir>]` is pure (no model, no key, no render).

- Development, 35 runs with metadata (34 Reels, 1 documentary; `real-runs-dev.json`): fact sheet found for 33;
  only 1 sheet has card fields (1 statement, too long for a hook); 0 Reels have a hook-eligible statement, 0 myths
  are spoken in order. The one documentary's 5 chapters fit their 3.5 s intervals (chapter_cuts). Today 29 Reels
  show a caption before 1.4 s (median first caption 0.09 s); with a hook, all 32 Reels with words would start their
  captions at or after 1.4 s.
- Production sample, 34 Reels (`real-runs-prod.json`): no fact sheets in the sample, so 0 card-eligible runs; 33
  show a caption before 1.4 s today; all 34 would clear it with a hook.

So on today's real runs QF-25 composes no hook or myth card: the sheets predate card fields (card_fields.py) and
will get them when rebuilt. Real-run renders of the cards are therefore not possible without inventing copy; the
evidence above uses the committed fixtures.

## For the reviewer

- The hook lengthens a Reel by 1.4 s and re-mixes its bed; release `composition_inputs` and provenance name the
  composed voice and bed.
- `check_chapter_fields` adds checker calls per documentary act and raises (stops the documentary) when a title
  fails or the record key is unset; documentaries are outside the first-release Reel scope.
- The QF-74 `coordinator.db = None` fixture lines of 9468fdc8 are not carried: `StageTimer(run_dir)` takes no
  database on this base and the QF-04/QF-05 tests pass without them (9 passed).

## Round 2 (review 1 findings)

- **Hook handover.** The hook's ground is the run's hero, and in 8 of 18 dev Reels with a parallax opening the
  first picture is that same photo, in another crop and tone: the hard switch at 1.40 s jumped. The hook now
  dissolves into the first picture over its own exit (from `HOOK_HOLD` 1.07 s, when its type starts to leave, to
  the offset) on a smoothstep alpha. `seam_real_runs.py <repo-root> <output-dir>` builds the hook on each of those
  8 real heroes (fixture claim, real scene_01/scene_02 clips and narration) and measures the frame-to-frame MAE
  above the caption band (rows 0-1090): handover step **13.8-37.5 -> 0.29-1.01** (the picture's own motion after it:
  max 1.9-3.2, as before). The dissolve's own steps peak at 8.8-15.8 per frame, a dissolve spread over 10 frames (review 1
  measured the hook's own exit steps at 12.5-14.8 on one of these heroes). `seam-real-runs-before.json` (round-1 commit) and `seam-real-runs-after.json`;
  [handover strip](previews/handover-before-after.jpg) (one real case, frames 36-44, top before, bottom after).
- **Review card clock.** The card's video-check summary now uses the master's beats (`render_beats`), as the
  release record and the look itself do; with a hook its times were 1.4 s early.
- **Documentary sheet.** `check_chapter_fields` reads the sheet the acts were verified on
  (`fact_sheet.path_for`), and returns without effect when there are no acts or no sheet (no chapter cards, as
  before QF-25), so `test_qf84` passes again. Chapter intervals and `require_chapters` apply only when the gate ran.
- Spend: a documentary's chapter gate is 2 checker calls per act (+10 for 5 acts), through the existing checkers and
  caps, cached by identity; documentaries are off by default (`VIDEO_FORMATS=reel`). Without `FACT_RECORD_KEY` a
  documentary with a sheet stops before the voice (production requires the key anyway).
