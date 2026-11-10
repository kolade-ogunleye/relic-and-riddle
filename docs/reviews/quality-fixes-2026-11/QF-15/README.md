# QF-15: beat restructure (payoff linked to the hook, beat 4 removed)

## What changed

- **Story order.** The writer's system prompt asked for five beats, the fourth being "Civilizational / Modern
  Relevance & High Stakes". It now asks for hook -> stake -> two or three concrete reveals -> payoff -> loop, and
  says never to add a generic modern-relevance paragraph.
  - The hook text (QF-14's rule, its examples and HOOK_FORMS) is unchanged.
  - Debated figures keep their hedge inside a reveal, never as a separate academic-debate paragraph. QF-16 owns the
    hedge rule itself.
  - Both user prompts' word budgets and the reviser's WORD BUDGET say "the stake and 2-3 reveals, the payoff, the
    closing loop sentence" in place of "3-4 fact sentences, the closing sentence".
- **payoff_sentence.** It is a new writer field (`ScriptCore`), required in the writer's JSON schema. It holds the
  narration's penultimate sentence word for word, and that sentence answers the opening.
  - A payload saved before the rule parses with "" (or None). The missing payoff is then a blocking finding; the
    field is never filled in for it.
- **The rule** (`script_engine.payoff_problems`, `src/narrative_payoff.py`). How each finding acts follows the
  operator's ruling of 2026-11-10 (`decisions/QF-15-payoff-rule.md`, option C, "structure blocks, noun repairs";
  until QF-17's semantic judge). Every finding first gets the one repair edit. Then:
  - **blocks**:
    - payoff_sentence missing;
    - payoff_sentence not the penultimate sentence word for word;
    - the payoff says the opening again;
    - the offline model unavailable (fails closed);
  - **ships flagged on the review card**: no noun shared with the narration's own first sentence
    (`PAYOFF_NOUN_PROBLEM`, "Payoff: weak link: ...").
- **The flag** follows QF-14's `hook_signal_note` exactly.
  - `script_engine.payoff_note` builds the card note: ⚠️ <b>Weak payoff:</b> “payoff” names none of the opening's
    nouns ... "Judge whether it answers the opening."
  - The run stores `payoff_note` on the payload after the line-picture check (main.py), so it follows the words the
    run voices.
  - The review card shows it after the hook flag, in the card's note format.
  - `probe` lists it with the other card notes.
  - It is not in `publishable.ENVELOPE_FIELDS` or `PUBLISHABLE_FIELDS`, so it is never posted (tested).
  - `blocking_problems` leaves the noun finding out, and `verify_claims` refuses only blocking payoff faults.
- **One repair edit per payoff sentence**, like QF-14's once per opening. The noun-only edit is keyed on the
  normalised penultimate sentence (`_payoff_key`). A revision that keeps it (a fact-check round, a line-picture
  rewording, the final copy's revision) sends no new payoff edit; a new payoff sentence may get its own.

  A lemma links the two sentences when it is a noun in one and a noun or an adjective in the other. This answers
  review 2, finding 2: the small tagger reads "concrete" in "Modern concrete crumbles" as an adjective. A lemma that
  is only a verb, an adverb, a number or an adjective in both sentences never links them. The model is the
  hash-verified spaCy `en_core_web_sm` 3.8.0 committed in `data/nlp` (approved in round 1). Nothing is downloaded and
  no provider is called.
- **One edit, not a rewrite** (review 2, finding 4). Payoff faults now ride in the same one edit as the opening's
  faults (QF-14).
  - `_opening_faults` takes them, and `_narrow_edits` counts them as narrow.
  - `opening_edit_findings` adds a `PAYOFF:` part. It allows the penultimate sentence as the one more sentence the
    edit may change, or marking payoff_sentence alone (`target: "payoff_sentence"`).
  - Without a payoff fault, the opening edit's text is exactly QF-14's.
  - A draft with both a hook fault and a payoff fault gets one call, not a rewrite. Before, the payoff fault
    disabled the hook edit.
  - When that edit fails, no copy or Story edit is spent (as QF-14 does for the hook).
  - `_length_edit_fixes` counts a payoff fault, so a long draft with a payoff fault takes the length edit, then the
    payoff edit, and no rewrite.
- **Length edits insert before the payoff** (`target: "narration_script"`, a unique match in the narration only).
  A search of the whole draft would also rewrite payoff_sentence.
- **Where it applies:**
  - writer drafts (`draft_problems`);
  - `verify_claims`, before any fact provider (curated and cached scripts);
  - the library path. `main._script_from_library` routes a payoff fault to the one edit, with QF-14's hook. No
    shipped entry has a payoff_sentence, so all 4 are edited and fact-checked again, never used as they are. This
    answers review 2, finding 3.
- **Downstream consumers:**
  - A kept script with a blocking payoff fault is not reworded for its pictures (`main._lines_against_pictures`),
    unless its penultimate sentence is itself an asked line. Every script written before the rule has no
    payoff_sentence, and its rewording would be refused for changing the penultimate sentence. This is the same
    guard as QF-14's for the cap. A kept script whose payoff only names no opening noun is reworded as before; its
    payoff is not edited again.
  - The payoff is a protected line for the line-picture check (`prevoice_lines.protected_sentences`), like the hook
    and the closing sentence.
  - `without_blocked_sentences` never trims the payoff.
  - The copy edit keeps it.
  - `_finalize` tidies it like the narration.
  - `improve` files its findings as `script.payoff`.
  - The hook-form revision (`hook_learning.revision_note`) asks it to keep the noun the payoff names again.

## Measured

Run `python docs/reviews/quality-fixes-2026-11/QF-15/measure.py <repo root> [<runs dir> ...] [--sample <runs dir>]
[--judgments <file>]`. It prints JSON, writes nothing and calls no provider.

- `before.json` is the parent f3abcbe0 and `measurements.json` is this row. Both were run with the same three
  inputs: the repository, the development `output/` and a copy of the production sample's `output/`. They were
  regenerated on 86e3e57e (the same code for this measurement) when the production rows became counts; every
  figure is unchanged except the runs with a composition (17 -> 40: this copy of the sample includes its runs'
  compositions).
- For the development run folder, only counts, run names and hashes are printed. Library sentences are quoted,
  since they are committed data.
- Production sample (decisions/PROD-SAMPLE-counts-only.md): its copy is passed with `--sample` and the builder's
  readings of its scripts (payoff marks and modern-relevance sentences, by hash) with `--judgments`, both kept
  outside the repository; for it measure.py prints counts only (no run name, hash or text). The committed `JUDGED`
  and `MODERN_RELEVANCE` hold the library's and the development runs' readings.
- The parent has no payoff check, so it admits every payoff.

| | parent f3abcbe0 | QF-15 |
|---|---:|---:|
| Story order in the prompt / modern-relevance beat in the prompt | no / yes | yes / no |
| Writer schema requires payoff_sentence | no | yes |
| Synthetic bad payoffs admitted: missing, not penultimate, repeats the hook, verb, adjective or number only | 6 of 6 | 0 of 6 |
| Synthetic controls admitted: same noun, plural, the noun the tagger reads as an adjective in the hook | 3 of 3 | 3 of 3 |
| Synthetic bad payoffs if the one edit fails (missing / not penultimate / repeats the hook / verb / adjective / number only) | all ship clean | blocked / blocked / blocked / flagged / flagged / flagged |
| A structural payoff fault blocks / a noun-only fault blocks | n/a | yes / no (ships flagged) |
| Library entries with a payoff fault and no payoff edit | 4 of 4 | 0 of 4 (4 routed to the one edit) |

### The proxy on the real corpus (review 2, finding 2)

- The corpus is 52 unique real scripts: library 4, development 28, production sample 26, counted once each when
  pooled.
- None was written to the rule, so each script's penultimate sentence is read as the payoff the writer would have
  marked.
- The builder judged each one, by its hash in measure.py: does it answer the opening or resolve its tension?
  - "payoff" means an outcome, a cause, or an explanation of the opening's own subject.
  - "not" means a side detail, a date, a debate, or a modern-relevance aside.
- 19 are judged payoffs and 33 are not.

| Pooled 52 | parent (no check) | round 2 rule (a noun in both) | QF-15 (a noun in one, noun or adjective in the other) |
|---|---:|---:|---:|
| Judged payoffs admitted (recall) | 19/19 | 5/19 (0.26) | **7/19 (0.37)** |
| Judged non-payoffs refused | 0/33 | 24/33 (0.73) | **23/33 (0.70)** |
| Precision of what is admitted | 0.37 | 0.36 | **0.41** |
| False rejects / false accepts | 0 / 33 | 14 / 9 | **12 / 10** |

Stated plainly: **the rule is a weak proxy for "answers the hook".**

- **False rejects (12).** True payoffs that name the subject with another word or a pronoun:
  - the Amber Room's fate told through "the mosaic" or "it";
  - Derinkuyu's "inhabitants sealed sections" after a count of its entrances;
  - a machine named again by a synonym of the opening's noun;
  - "blades" after "steel";
  - Odessa's "partisans" after "detachments".

  Each costs the one edit, which names the opening's noun again. The callback then becomes explicit, which is the
  loop the order asks for.
- **False accepts (10).** A shared noun does not make an answer:
  - "The manuscript is named after Polish book dealer Wilfrid Voynich" shares "book";
  - Memnon's "reconstruction of the statue's upper tiers" shares "statue";
  - "Today, petrographic analysis of ancient concrete ..." shares "concrete" and is itself the retired modern
    beat;
  - a site name shared by the hook and any later sentence always passes.

  A lexical rule cannot see these; a semantic judge (QF-17) is the way past them.
- **The cross-POS change** admits 2 more judged payoffs than round 2: a production-sample payoff whose shared noun
  the tagger reads as an adjective in the hook, and the Lycurgus cup's "green", which the tagger reads as a noun in the
  hook. It adds 1 false accept (the petrographic sentence above).
- **No synonym list.** One was measured on paper and not built. It would admit 2 more payoffs (computer/computing
  machinery, sword/blade) and 2 more non-payoffs (freighter/ship, obelisk/monolith), and a hand-picked word list
  goes against the topic-agnostic rule.
- Because the proxy is this weak, the operator ruled (option C) that a missing shared noun repairs and then flags,
  rather than blocks.

### Outcomes on the 52 real scripts (operator ruling, if each one's one edit changes nothing)

Each script's penultimate sentence is marked as its payoff here, so no structural fault arises: the real scripts
have five or more sentences and a penultimate sentence that is not the opening.

| Pooled 52 | blocked | ships flagged | ships clean |
|---|---:|---:|---:|
| Parent (no payoff check) | 0 | 0 | 52 |
| QF-15, the payoff rule | 0 | 35 | 17 |
| QF-15, judged payoffs (19) / judged non-payoffs (33) | 0 / 0 | 12 / 23 | 7 / 10 |
| With QF-14's hook rule as well: parent / QF-15 | 38 / 38 | 3 / 12 | 11 / 2 |
| Library / dev / production sample (payoff rule) | 0 / 0 / 0 | 1 / 18 / 18 | 3 / 10 / 8 |

- **The payoff rule ends no real run.** All 38 blocks are QF-14's hook cap or label.
- **Flags.** The card would flag 35 of these legacy payoffs: 23 judged non-payoffs (true flags) and 12 judged
  payoffs (false flags). These scripts were not written to the rule; a new draft is asked for the linked payoff.

| Library entry | penultimate shares | judged | QF-15 |
|---|---|---|---|
| odessa_catacombs | city, tunnel | payoff | admitted |
| singing_colossi_of_memnon | statue | not (a date of the repair) | admitted (false accept) |
| unfinished_obelisk_aswan | none (obelisk / monolith) | not (a modern-studies aside) | refused |
| wieliczka_salt_mine | salt | not (ventilation damage) | admitted (false accept) |

All 4 lack payoff_sentence and are routed to the edit anyway.

### Calls and spend (review 2, finding 4)

- The routing replay runs each real script through the tree's own `generate_script`. Its penultimate sentence is
  marked as payoff_sentence.
- The writer is a stand-in that returns the same script every time, which is the worst case: a writer that keeps
  its words.
- Every edit call is counted.

Heavy calls per run, mean (max):

| | parent: edit fixes | QF-15: edit fixes | parent: edit fails | QF-15: edit fails |
|---|---:|---:|---:|---:|
| Library (4) | 2.0 (2) | 2.0 (2) | 4.0 (4), 4 no script | 4.0 (4), 4 no script |
| Development (27 replayable) | 2.0 (6); 23 opening edits | 2.11 (6); 26 one-edits | 3.56 (4); 23 no script | 3.67 (4); 23 no script |
| Production sample (26) | 2.35 (6); 19 opening edits | 2.58 (6); 25 one-edits | 2.96 (4); 16 no script | 3.19 (4); 16 no script |

- **When the one edit works.**
  - A payoff fault adds an edit only to a script whose hook was clean: dev +3 calls, production +6.
  - On every other script it rides in the edit the hook already spent.
  - No rewrite is added. Review 2 found that each payoff fault forced a full redraft and disabled the hook edit.
- **When the edit fails.**
  - Under the ruling, an unlinked payoff ships flagged after its one edit, so **no run ends with no script over
    the payoff**: 23 (dev) and 16 (production) runs end with none on both trees, all of them over QF-14's hook.
  - The cost is the one edit: dev +3 calls, production +6, which raises the mean.
  - The real writer is told the rule in the system prompt, in the finding and in every rewrite.
- **Size of one heavy writer call.** The median real `script_writing` call (development `llm_calls.jsonl`, 100
  calls) was 3,054 tokens in, 216 out and 44 s. The one edit is one such call or smaller.
- **Prompt growth.** These are fixed per-call additions, about 485 characters in the system prompt, of the order of
  250 tokens per writer call plus about 30 tokens out for payoff_sentence:

  | | parent | QF-15 |
  |---|---:|---:|
  | System prompt | 6,381 chars | 6,866 |
  | Verified user template | 7,207 | 7,304 |
  | Reviser | 3,531 | 4,263 |
  | Writer schema | 3,225 | 3,326 |

- **Who pays.**
  - Development: the free Gemini writer while `FREE_TIER_HEAVY` is true, against the free key's daily quota.
  - Production: the billed heavy key, inside the existing `PAID_FALLBACK_HEAVY_MONTHLY_USD` cap.
  - No new provider or spend path. The build and the tests made no live call.

## Beat 4 removed: beats, chapter cards and myth cards (design-freeze scope)

These are measured on the 52 real scripts (`beats` in the JSON). The two trees are identical on them, since they
are the existing scripts.

| | before (real runs) | after |
|---|---:|---:|
| Narration sentences per script (min / median / max) | 5 / 6 / 21 | not measurable in a build: it needs the live writer |
| Composed scenes per run (40 runs with a composition) | 9 / 11 / 12 | same |
| Scripts carrying a modern-relevance sentence (builder-judged, by hash) | 10 of 52 (3 development, 7 production sample) | asked for 0 |
| Spoken time of that sentence (9 timed) | median 7.88 s, max 9.6 s | — |
| Its share of the voiceover | median 17%, max 21% | — |

- **The beat count does not change by rule.**
  - The word window is unchanged, so the number of sentences and scenes stays in the same range.
  - The retired beat's 8 s or so goes to the stake, the reveals and the payoff.
  - The real "after" text needs a live writer, so it is not produced here (no invented copy).
- **Chapter cards** (`design_cards.plan_chapter_card`) take their number, total and title from a deep dive's acts
  (`DeepDivePackage.acts`, the 16:9 deep dive), not from the Reel's beats. A Reel draws no chapter card; the
  composer wires none yet (QF-25).
- **Myth cards** draw only a checked myth field (`card_fields`), never narration.
- **Hook frames** draw their checked claim; the first caption burst reaches them only through the claim guard.
- **Renders.** The freeze panel's `cards/render_evidence.py --only chapter,myth --match _dark` was run on the
  parent and on this row, with the same real run inputs: the Richat deep dive's 5 acts in both formats, and the
  three myth fields.
  - Each tree drew 53 frames and refused 5 cards, with the same reasons.
  - All 53 frames are pixel-identical (`renders/compare.json`).
  - The hook frame is QF-14's; its 47 frames were shown pixel-identical there, and this row changes no renderer.
  - Three half-size frames are in `renders/`: chapter 16:9 act 1, chapter 9:16 act 4, and the Richat myth card
    after its strike.

## Branch-only test changes

- `tests/conftest.py` (origin/main file, fixture input only, no assertion changed). The shared `curated_payload`
  gains `payoff_sentence`: its own penultimate sentence ("Its earliest tunnels ..."), which names the opening's
  "tunnel".

- `tests/test_script_guardrail.py` (origin/main file, fixture input only): the short draft ends on a payoff that
  names its second word.
- Branch-only files, fixture inputs only: each synthetic narration now marks its penultimate sentence as
  payoff_sentence, so the test still isolates what it tests.
  - `test_copy_edit_2026_10_29.py`, `test_length_history_2026_10_29.py`, `test_trace_false_positives_2026_10_29.py`,
    `test_script_rounds.py`, `test_repeat_and_hedge_edits_2026_10_28.py`: the length drafts open on a sentence and
    end on a payoff naming its noun.
  - `test_hook_2026_10_28.py`, `test_qf14_hook_enforcement.py`: a payoff naming every opening's noun.
  - `test_story_motion_colour_2026_10_29.py`: the body's penultimate sentence names Derinkuyu.

No assertion or expected value changed. The covering tests are listed in the build note.

Regression test: `tests/test_qf15_narrative_payoff.py` (35 tests).

## Known limits

- The rule checks a noun and a position. It does not judge whether the sentence answers the opening (QF-17).
- `_split_sentences` splits on ". "; an abbreviation inside the payoff ("St. Peter") would make the position check
  fail. The writer then gets the one edit.
- Kept scripts written before the rule keep their words in a pictures regeneration unless their penultimate line is
  asked.
