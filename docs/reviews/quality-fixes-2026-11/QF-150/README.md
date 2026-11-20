# QF-150: the script stage stops narration the captions cannot break well

The captions never end a burst or a line on a never-end word (`video_composer.never_end_end`: OF, IN, THE, WHETHER,
UPON ...) where a valid break exists. Where none exists under the caps and the bonds (a proper name is one unit), the
composer keeps the defect and records it (`reading_rate.never_end`, `reading_rate.line_defect`): "DEPENDING ON
WHETHER |", "DEDICATED IN |", "AT / THE CATHERINE PALACE", "DURING / ARAB-BYZANTINE WARS". Lander ruling: these are
recorded at render, not held at release, and fixed at source, in the script. The voice is never stretched.

## What the script stage does now (`src/script_engine.py`)

- `caption_break_findings` runs the composer's own burst planning (`video_composer.caption_phrases`, with the
  script's emphasis words) on the narration's voice tokens (`caption_tokens`: what `align_words_with_script` gives
  the captions) at nominal timing (`nominal_caption_words`: 0.053 s a letter + 0.09 s a word, +0.47 s after a clause
  mark, +0.55 s after a sentence end, fit on the real runs' words.json). It names each burst end and each 9:16 line
  break on a never-end word that the planner and its repair keep.
- `caption_break_problems` turns them into one finding per sentence ("Caption break: the captions of “...” must
  break at “Dedicated in |” ... Rephrase that clause so a natural break exists ..."), part of every draft's
  `draft_problems`. It never blocks (`blocking_problems`), and a planning failure is advice lost, never a stopped
  draft.
- `ScriptEngine._fix_caption_breaks` (end of `_narrow_edits`): one surgical edit (`_revise_surgically`, a
  `CAPTION:` finding the reviser's rules now describe), asked once per narration and only when nothing else blocks;
  a draft going back for a rewrite carries the finding in its feedback instead (no call added). No call when nothing
  is flagged. The edit is kept only when the sentence count, every other sentence and every figure (digits and
  spelled numbers) are unchanged, no blocking problem is new, and fewer sentences are flagged with none newly
  flagged; else the draft goes on as it was. The kept edit is fact-checked in full like any draft (the signed review
  is made later, on the final copy). The writer's `sentence_trace` follows the reworded sentence.
- The run's record: `payload["caption_lint"]` = `{"checked", "bursts", "lines", "sentences", "edit"}` (made in
  `_finalize`, the edit's outcome added by `generate_script`, and made again by `main._write_and_check_script` on the
  narration as checked). The ledger files the finding as `script.caption_break`.

No topic list: the lint reads the composer's rules only.

## Measured on the 69 real runs (35 dev `output/`, 34 read-only production export)

`measure.py` here; `measurements.json` holds counts and run folder names only.

| | count |
|---|---|
| Recorded by the composer on the real voiced words (truth) | 35 burst ends, 22 lines, in 35 runs |
| Predicted by the lint from the text, nominal timing | 35 burst ends, 21 lines, in 34 runs |
| Defects (kind + word): precision / recall | 55 / 56 = **0.982** / 55 / 57 = **0.965** |
| Sentences: precision / recall | 49 / 50 = **0.980** / 49 / 51 = **0.961** |
| Sentence false-positive rate | 1 / 369 clean sentences = **0.27%** |
| Runs: flagged / with a recorded defect / missed / false alarm | 34 / 35 / 1 / 0 |
| Flag rate | 34 of 69 runs (**49%**; dev 15 of 35, prod 19 of 34), 1.47 sentences per flagged run |

The three disagreements:

- dev `run_gol_gumbaz_whispering_gallery_1793278143`: the same sentence, a different kind (nominal timing ends the
  burst on UPON; the voiced timing puts UPON at a line end). The sentence is flagged either way.
- dev `run_tunguska_event_1908_1792930600`: the defect is predicted; the run's words.json and its later script differ
  by a stray " ." token, so the sentence strings differ (a counting artifact, not a miss).
- prod `run_antikythera_mechanism_1793412278`: "LEADS SOME |" is missed; the voiced pace (a pause the nominal timing
  does not have) changes the repair's dwell term there.

## Cost

Only a flagged draft that nothing else sends back costs a call: one `_revise_surgically` call per flagged narration.
At the measured 49% flag rate, about 0.5 extra writer calls per run (up to one more per fact-check revision whose new
narration is flagged again). The lint itself is local: 0.4-1.6 s (mean 0.95 s, 12 production narrations) of planning per `draft_problems`
call, a few calls per draft.
Whether a writer's rephrase removes the break was not measured (no paid calls); the guard keeps only edits that do.
