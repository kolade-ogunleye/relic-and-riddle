# QF-60: structured reconstruction verdict replaces the regex

Ordinary words such as `worked`, `inside` and `turned` no longer grant permission to reconstruct a named subject.
The pre-voice check (src/prevoice_lines.py, VERSION 4: older answers are asked again) emits a strict boolean, one of
four allowed kinds (original appearance, an interior no photograph exists of, a mechanism in use, construction) and
a reason. An exception needs all of them, a generable or unshown sentence and a non-abstract visual class; a
missing, malformed, contradictory or stale record grants none. The planner, the generation gate and the reference
comparison each check it; `reason="reconstruction"` alone is refused. A valid exception still lets the drawing add
only what the narrated line and the fact sheet's descriptors describe. Fact, reference, rights and spend gates are
unchanged.

## Round 4: the verdict reaches the beats of its sentence

Beats (`scene_planner.segment_beats`) are fragments of a sentence, so the verdict is bound to the sentence, not to
the beat text: `reconstruction.approved()` requires the normalised beat to lie inside the verdict's sentence as
whole words, and `bind()` stores the verdict and its own sentence in the illustration spec; the generation gate and
the nothing-added rule (`spec_approved`) require the stored sentence to equal the verdict's exactly. Both are kept in
the image's lineage record and in `report["generated"]`.

## Round 5: the beat's own sentence, found by word position

Review 4 found that the verdict reaching a beat came from `picture_coverage.beat_verdicts`, which takes the FIRST
sentence containing the beat's words. A short beat of an ordinary sentence ("the sands.") could then inherit an
earlier reconstruction sentence's verdict, and containment cannot tell the two apart. This happened on real runs once
the voice ran a second past its last word (the short final beat is then no longer folded into the one before). Now:

- `segment_beats` records each beat's `word_span`: its [first, end) offsets in the voiceover's word timeline.
- `picture_coverage.beat_sentences(run_dir, beats)` places each beat in its sentence by position. The timeline's
  tokens (`words.json`) are aligned with the tokens of the pre-voice record's sentences (`prevoice_lines.sentences`,
  in narration order, the `beat_verdicts` normalisation): the identity when the two streams are equal, otherwise only
  matching blocks whose tokens occur once in each stream. The beat's text must be the timeline's words at its span,
  every token must have a certain place, the places must be consecutive and they must lie in ONE sentence.
  Otherwise the beat is unplaced (None). Without the record, the timeline or a valid span, every beat is unplaced.
- `picture_coverage.placed_verdicts` hands on a copy of that sentence's verdict with a placement
  (`{"by": "word_position", "sentence_index", "beat"}`). `reconstruction.approved()` grants nothing without a
  placement made by word position for this very beat. A verdict from the text lookup, a placement for another beat,
  and a placement written into the record itself (dropped by both lookups) all grant nothing.
- `scene_planner` takes the reconstruction verdict only from `placed_verdicts`. The planner (`illustration_reason`),
  the gate (`spec_approved` in `illustrate_beat`) and the nothing-added rule therefore all see the same placement.
  `beat_verdicts` still gives the visual class and the protected flag, which are unchanged.
- A reason needs a word of three or more letters or digits: an invisible or one-character reason is refused (review
  4, non-blocking 1).

## Measurements

`measure.py <repo root>`. Part 1 mocks the provider. It measures the permission boundary, not live model accuracy:
the 25 committed ordinary sentences, the structured answer through the real pre-voice schema, and whether an
unsupported reconstruction can relax the reference rule. Part 2 reads the committed `real_data*.json`. They were
written by `real_data.py <tree> --dev <output> --prod <production-sample copy>` on the base (d9d623fb), round 3
(6cc8273), round 4 (ff0e92c3) and this commit. Real data is counts only: 22 runs with the pre-voice record, the
word timeline and the voiceover (4 development, 18 production-sample copy), with 137 pre-voice sentences. In 5 runs,
the timeline lacks words the script has (numbers and units the voice timing left out). Each run is measured at the
pipeline's own voice length (ffprobe of the voiceover, as `audio_engine.get_duration`) and with 0, 1 and 3 s of tail
silence after the last word. For every sentence, only that sentence is given a valid verdict, and every beat goes
through all three checks. The beat's own sentence comes from the script's own alignment oracle, not from the code
under test.

| Metric | before | after |
|---|---|---|
| ordinary lines flagged (acceptance <= 1) | 20/25 (base) | 0/25 |
| the three review examples return "" | no | yes |
| structured verdict retained | no | yes |
| unsupported reconstruction keeps the strict reference rule | no | yes |
| real pre-voice sentences flagged without a verdict | 7/137 (base regex) | 0/137 |
| beats inside one sentence granted, voice length (232 beats) | 27/188 (round 3), 184/188 (round 4) | 184/188 |
| the same at +1 s and +3 s tail (239 beats) | 20/195 (round 3), 188/195 (round 4) | 191/195 |
| of them, beats of multi-beat sentences, voice length / +1 s / +3 s | 111/113, 122/127, 122/127 (round 4) | 111/113, 125/127, 125/127 |
| beats granted from another sentence's verdict, voice length | 0/1266 (round 4) | 0/1266 |
| the same at +1 s and +3 s tail | 3/1301 (round 4) | 0/1301 |
| another sentence's valid verdict handed to the beat directly, +1 s / +3 s | 7/1301 (round 4) | 0/1301 |
| own verdict made invalid, granted, voice length | 184/1302 (round 4: the invisible reason) | 0/1288 |

The tail-0 s figures equal the voice-length figures on every tree. The 4 refused inside beats at every voice length
are unplaced: in runs whose timeline lacks a word of the script, their words run across the missing word, or lie in
an alignment block that also occurs elsewhere (fail closed). Every other beat inside one sentence is granted. Every beat spanning two sentences is refused (44 at
each voice length). Round 4's 184 invalid grants are one per granted beat, from the zero-width-space reason. The
beat count (232 at the pipeline's voice length) is the pipeline's own split. Review 3 reconstructed the beats in its
own way and counted fewer.

Regression: `tests/test_qf60_reconstruction_verdict.py`. The reviewer's mill probe runs through the real
`segment_beats`, the placement and the three checks (end to end through `fit_to_lines`) at 0, 1 and 3 s of tail. The
file also covers repeated words across sentences, a beat spanning two sentences, unplaceable beats (no timeline, an
uncertain alignment block, a beat across a timeline gap, a missing/malformed/stale span), direct `approved()` calls
without or with a foreign placement, a placement forged in the record, and the earlier real-split tests.
