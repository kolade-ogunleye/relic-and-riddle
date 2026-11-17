# QF-60b: a reconstruction reason must be a real reason

QF-60's review (round 5, non-blocking 1) found that a placeholder reason such as "none", "unknown", "TODO" or
"no reason" still granted the reconstruction exception. Rounds 1-3 tried to read the free-text
`reconstruction_reason`: a placeholder list, then a positive rule (subject word, a cause in the kind's vocabulary,
no hedge, no contradiction, English only). Every review found new prose that passed: in round 3, "no photograph can
show it" appended to a vacuous restatement worked as a pass-phrase for every kind (the VERSION 5 prompt itself
contained those words, and its own photographable example "No photograph can show the statue inside the museum."
was granted), and a bare "camera", "daguerreotype" or "predates" counted as a cause in a reason that said a
photograph exists. The operator chose option A (decisions\QF-60b.md, 2026-11-13): a structured cause, as QF-61 did
for counts. Prose is no longer read to grant anything.

## The grant test (`reconstruction.structured`, inside `approved()`)

A reconstruction verdict grants the exception only when every structured field is valid; anything missing, mistyped
or invalid refuses, and the strict nothing-added rule stays:

- `reconstruction` is exactly `true` and `reconstruction_kind` one of the four kinds (unchanged);
- `reconstruction_cause` is one of a fixed list (`CAUSES`), exact string only, and one the kind allows
  (`COMPATIBLE`, below);
- `reconstruction_subject_words` are words of the verdict's own sentence, in its order, matched as whole words after
  the normalisation QF-60 places a beat in its sentence by (`_norm`: case and punctuation insensitive; a lookalike
  letter, a zero-width split or a part word does not match), at most 8 words, and at least one of them names
  something (three or more characters with a letter, not a stop word: function words, the kinds' own words such as
  "inside", "interior", "original", "construction", and photo words);
- `reconstruction_reason` is present for the reviewer: QF-60's own presence check (a word of three or more letters,
  at most 400 characters), plus a display sanity filter: a reason that is only a placeholder of round 1's list
  ("none", "N/A", "TBD", "no reason given", ...) has nothing to show and refuses. This filter can only refuse; the
  reason's wording is otherwise never tested.

The cause list and what each means to the model (`CAUSES`):

| cause | meaning |
|---|---|
| `lost_or_destroyed` | the thing, or its original state, is lost, destroyed or changed |
| `sealed_or_inaccessible` | it is sealed, buried, flooded or otherwise out of reach |
| `never_photographed` | no photograph of it was ever taken |
| `before_photography` | the building or making happened before photography began in 1826, so no photograph of it can exist (construction only, never a thing that still stands or works) |
| `no_longer_operating` | it no longer works; it is broken, seized or survives only in fragments |

The compatibility table (`COMPATIBLE`): the cause must explain why no photograph can show that kind of view.

| kind | allowed causes | why |
|---|---|---|
| original_appearance | lost_or_destroyed | the original look is gone or changed; "before photography" is true of any ancient subject, even one whose look survives unchanged, and "sealed" or "never photographed" says nothing about a look that may still be there |
| unphotographed_interior | sealed_or_inaccessible, never_photographed | the room is out of reach, or was never photographed (the kind's own definition); a lost room is never_photographed, "no longer operating" is no cause for a room |
| mechanism_in_use | lost_or_destroyed, no_longer_operating | it is lost or no longer works; "before photography" is true of a mechanism that still runs today, and "sealed" or "never photographed" does not say it cannot be seen working |
| construction | before_photography | the building process is transient whatever later happens to the building; only a process before photography lacks a photograph for certain (construction photographs of since-destroyed buildings exist), so lost_or_destroyed is not allowed (narrower than the brief's example table; fail closed) |

6 (kind, cause) pairs are allowed, 14 are mismatches.

Round 5 (review 4, blocking 1): round 4 also allowed `before_photography` for original_appearance and
mechanism_in_use, defined as "it happened, or ended, before photography began in 1826". That is true of any ancient
subject, so an honest model could pick it for a look that survives unchanged or a mechanism that still works, and
the structure could not tell those from a genuine case: the cause carried no evidence for two of the four kinds.
It is now allowed for construction only, and its definition is narrowed to construction (the building or making
happened before 1826, so no photograph of it can exist). The genuine cases of the two kinds are covered by
`lost_or_destroyed` (its text includes "or changed": painted colour gone, a look altered before cameras) and
`no_longer_operating` (a mill that stopped long ago, a lost crane). The prompt is written from the table, so it
follows. The prompt stays VERSION 5: round 4 is not on main and no real run has a VERSION 5 record (the 22 are
VERSION 1-2); a round-4 record that used a removed pair is refused at the grant (fail closed), not granted.

`structured` is called only inside `reconstruction.approved()`, the one check behind the planner
(`image_gen.illustration_reason`), the generation gate (`spec_approved` in `illustrate_beat`) and the nothing-added
rule (`spec_approved` in `image_quarantine._nothing_added_rule`). The prose checks of rounds 1-3 (`real_reason`,
`causes`, `is_hedge`, `contradicts`, `is_english`, `copies_sentence` and their vocabularies, about 450 lines) are
removed: they can no longer grant, and kept as a further veto they would bring back the prose fragility that rounds
1-3 measured (genuine terse reasons refused, new bypasses found every round) while adding nothing the structure does
not decide. Only round 1's exact placeholder list stays, as the display filter above.

What the structure cannot do: it checks that the verdict is complete and self-consistent, not that the model's
claim is true. A model that answers the prompt's photographable example with `unphotographed_interior`,
`never_photographed` and "a statue" is granted, as a wrong `kind` was before; the prompt's warning ("Inside the
museum stands a statue" is an ordinary photographic subject), "If uncertain, false" and the review card remain the
control for that. The reason the reviewer reads is the model's own sentence.

## The prompt (`prevoice_lines`, VERSION 5)

The pre-voice SYSTEM prompt now asks, for every sentence, for `reconstruction`, `reconstruction_kind`,
`reconstruction_cause`, `reconstruction_subject_words` and `reconstruction_reason` (defaults false, null, null, "",
""). The cause list, its meanings and the table are written into the prompt by `reconstruction.prompt_fields()` from
`CAUSES`, `COMPATIBLE` and `SUBJECT_MAX_WORDS`, so the prompt and the grant test cannot differ:
"reconstruction_cause: exactly one of: lost_or_destroyed (...); ... Each kind allows only these causes: ... When no
allowed cause is true, the sentence is not a reconstruction (false). reconstruction_subject_words: the words of the
sentence that name the thing reconstructed, copied exactly as they stand in the sentence, at most 8 words.
reconstruction_reason: one short sentence for the human reviewer." The echoable stock phrases are gone: round 3's
"gives the concrete cause no photograph can show it" line is removed, and the kind definitions no longer say "which
no present photo can show" / "in a state no photograph can show" (now "which differs from what survives today" /
"in a state that can no longer be seen"). The answer schema lists the five causes as an enum; a cause outside it is
read as no cause (refused at the grant) instead of failing the whole answer, and subject words that are not text are
read as none. The subject words are kept verbatim (never cleaned like the reason).

Re-asking cost. A record judged without the structured fields cannot grant any more. Such a record is asked again
once, only when it holds a reconstruction verdict (`current_without_reconstruction`: a verdict with reconstruction
true and no `reconstruction_cause` key; every VERSION 5 answer now writes that key, None included, so an answer that
gave no valid cause is never asked in a loop): a VERSION 4 stamp or checkpoint of the same script and photos
(`RECONSTRUCTION_ONLY_VERSIONS = (4,)`, `legacy_key`), or a current VERSION 5 one written by round 3's prompt. The
record must have been judged for the same script and photos (`record_verdicts(run_dir, key)`). The call is the
existing one light, text-only free-tier call (`free_llm.call`, role checker, serial; no paid spend), and only the
reconstruction fields (`RECONSTRUCTION_FIELDS`: reconstruction, kind, cause, subject words, reason) are taken from
the answer (`refresh_reconstruction`): no kind, photo or visual class changes, so no rewording, no picture-side
search and no other line; the record and checkpoint are then current (asked once). A record without a
reconstruction verdict is untouched, and a voiced run is never asked (unchanged). Older versions (1-3) are asked
again as before. Real data: the 22 real runs' records are VERSION 1 or 2, every run is voiced, and 0 of their 137
verdicts has reconstruction true: the change re-asks none of them and costs nothing there.

## Measurements

`measure.py <repo root>`. Part 1 runs the root's code on the lists of `tests/test_qf60b_reconstruction_reason.py`
through the three checks (planner, generation gate with generation off, relaxed nothing-added rule; granted only
when all three agree). Part 2 reads `real_data_base.json` (QF-60, 84c3eeb), `real_data_round3.json` (round 3,
1f7de19) and `real_data.json` (this commit), written by `real_data.py <tree> --dev <runs> --prod <runs>` over
read-only copies of the 22 real runs' records, timed words and voiceovers (4 development, 18 production sample;
counts only). `before.json` is measure.py on 84c3eeb, `round3.json` on 1f7de19, `round4.json` on 1a8a5c4 (round 4)
and `after.json` on this commit. Round 5 re-ran all of them and the three real-data files with this commit's lists
(6 allowed pairs, 14 mismatches, review 4's two probes).

| Part 1, granted in all three places | QF-60 (84c3eeb) | round 3 (1f7de19) | round 4 (1a8a5c4) | this commit |
|---|---|---|---|---|
| prose only: placeholders, paraphrases, lookalikes, other languages (round 1-2) | 103/113 | 0/113 | 0/113 | 0/113 |
| prose only: a cause with no subject of the sentence | 8/8 | 0/8 | 0/8 | 0/8 |
| prose only: genuine reasons, four kinds | 11/11 | 11/11 | 0/11 | 0/11 |
| prose only: 34 reasons written for the VERSION 4 prompt | 34/34 | 25/34 | 0/34 | 0/34 |
| prose only: 20 reasons written as round 3's VERSION 5 asked | 20/20 | 20/20 | 0/20 | 0/20 |
| prose only: depicted action restated, sentence copied | 12/12 | 0/12 | 0/12 | 0/12 |
| prose only: hedges, questions, possibilities | 19/19 | 0/19 | 0/19 | 0/19 |
| prose only: denied cause, a photograph said to exist | 12/12 | 0/12 | 0/12 | 0/12 |
| prose only: other script, small capitals, foreign clause | 12/12 | 0/12 | 0/12 | 0/12 |
| prose only: genuine edge cases (negated remains, dates) | 11/11 | 11/11 | 0/11 | 0/11 |
| **review 3's 14 probes, as written** | 14/14 | 14/14 | 0/14 | 0/14 |
| structured, genuine (10, four kinds): granted | 10/10 | 5/10 | 10/10 | 10/10 |
| every allowed (kind, cause) pair (6): granted | 6/6 | 6/6 | 6/6 | 6/6 |
| every mismatched (kind, cause) pair (14) | 14/14 | 14/14 | 2/14 | 0/14 |
| **review 4's 2 probes: before_photography on a surviving look, on a working mechanism** | 2/2 | 1/2 | 2/2 | 0/2 |
| review 3's probes with their subject words, no cause | 14/14 | 14/14 | 0/14 | 0/14 |
| review 3's probes with each cause their kind does not allow | 47/47 | 47/47 | 10/47 | 0/47 |
| a cause outside the list or mistyped | 20/20 | 20/20 | 0/20 | 0/20 |
| subject words not the sentence's own (order, part words, lookalike, zero-width, stop words, 9 words) | 23/23 | 23/23 | 0/23 | 0/23 |
| subject words of another sentence | 12/12 | 12/12 | 0/12 | 0/12 |
| a bare placeholder as the reviewer's reason | 4/5 | 0/5 | 0/5 | 0/5 |

The round-4 column's 2 mismatched pairs and 10 probe cases are exactly the two pairs this round removes
(original_appearance and mechanism_in_use with before_photography).

Real data (22 runs, 137 sentences, counts only), at the voice length; +1 s and +3 s tails in brackets:

| Metric | QF-60 (84c3eeb) | round 3 (1f7de19) | this commit |
|---|---|---|---|
| prose-only verdicts granted (271 reasons: every list above and the four round-3 templates naming the sentence's word) | 48024/50948 (49851/52845, 49851/52845) | 1286/50948 (1321/52845, 1321/52845) | 0/50948 (0/52845, 0/52845) |
| structured genuine verdicts granted (6 allowed pairs, the sentence's longest word as subject) | 1104/1128 (1146/1170, 1146/1170) | 1032/1128 (1074/1170, 1074/1170) | 1104/1128 (1146/1170, 1146/1170) |
| mismatched (kind, cause) granted | 2576/2632 (2674/2730, 2674/2730) | 2408/2632 (2506/2730, 2506/2730) | 0/2632 (0/2730, 0/2730) |
| subject words of another sentence granted | 1104/1128 (1146/1170, 1146/1170) | 2/1128 (2/1170, 2/1170) | 0/1128 (0/1170, 0/1170) |
| beats granted from another sentence's verdict | 0/1266 (0/1301, 0/1301) | 0/1266 (0/1301, 0/1301) | 0/1266 (0/1301, 0/1301) |
| the 137 real model-written verdict reasons, prose only, granted under any kind | 137/137 | 32/137 | 0/137 |
| the narration sentence copied as the reason, prose only (construction / mechanism / appearance / interior, of 137) | 137 / 137 / 137 / 137 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |

Regression: `tests/test_qf60b_reconstruction_reason.py` (433 tests): every round-3 probe refused as written (no
structure), with its subject words but no cause, and with each cause its kind does not allow; review 4's two probes
(before_photography on a look that survives unchanged, on a mechanism that still works) refused, before_photography
granted for construction alone and the prompt's narrowed definition present; the prompt's statue example granted
only with an interior cause; every prose reason of rounds 1-3 refused on its own; genuine structured verdicts of all
four kinds and every allowed pair granted; the table has 6 allowed pairs and 14 mismatches; every mismatched pair,
every bad cause, every subject that is not the sentence's own (order, part words, lookalikes, zero-width split,
another sentence, stop words only, over 8 words) and every tampered field refused, also when changed after binding;
a terse genuine reviewer reason does not decide; the code's table equals the test's own; the answer schema carries
the enum and a bad cause does not fail the answer; the judge keeps the fields verbatim; the prompt is VERSION 5,
states the list and the table and has no echoable stock phrase; a VERSION 4 record without a reconstruction and a
structured VERSION 5 record are not asked again (stamp and checkpoint; record not rewritten), nor one whose answer
gave no valid cause; a VERSION 4 or round-3 VERSION 5 record with a reconstruction is asked once and only its
reconstruction fields change. On round 4 (1a8a5c4): 17 failed, 416 passed; on round 3 (1f7de19): 154 failed, 279 passed; on QF-60 (84c3eeb): 384 failed, 49 passed;
every failure an AssertionError; on this commit 433 passed.

No genuine grant is lost: the structured genuine verdicts are granted on exactly the beats QF-60 grants (1104/1128
at the voice length; the 24 refused are QF-60's 4 beats without a certain place, times 6 pairs). The pair count
changed in round 5 (8 to 6), so these totals are smaller than round 4's 1472/1504; per allowed pair the count is the
same (184/188). Round 3 granted fewer of them (1032) because its prose rule read the reviewer's template reason.
"acceptance_met" in before.json, round3.json and round4.json is false because part 1 fails there; part 2 always
reads this commit's committed real-data files.
