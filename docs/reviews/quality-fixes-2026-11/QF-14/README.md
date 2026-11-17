# QF-14: hook enforcement

The narration's first sentence is the sentence the first caption bursts speak. It is now held to two rules. How each
one acts follows the operator's ruling of 2026-11-10 (decisions/QF-14-hook-rule.md):

- the 12-word cap (and a label opening) **blocks**;
- a missing signal in the first 8 tokens gets **one repair edit**. If the opening still has no signal, the script
  **ships with a "Weak hook signal" flag on the review card** for the operator to judge. No run ends without a
  script over the signal check. This holds until a semantic hook judge exists (QF-17).

The two rules:

- **At most 12 spoken words** (`config.HOOK_MAX_WORDS`; it was 14 and advisory). A dash separates words, as in the
  captions.
- **A signal within the first 8 spoken words** (`config.HOOK_SIGNAL_WITHIN`). The signal is a number, a scale, a
  contrast or a question (`script_engine.hook_signal`). Each kind is defined by its grammar, never by a topic's own
  words. Round 4 widened each category to cover its whole grammar (review 3, finding 1):
  - number: a digit, or a number word, with hyphenated parts counting ("thirty-six"). A year counts. A singular
    count counts ("one", "single", "sole", "lone"), and so does "first".
  - scale:
    - a superlative: the -est form of a dimension or age adjective (generated from one list of 37 adjectives),
      an irregular superlative ("best", "worst"), "most" or "least" before a word (but not "most of", which is a
      quantity), "only", or "world's";
    - a comparison: a comparative followed by "than" within the span ("older than", "more than 600"), or a
      comparative verb ("predates", "outlasts");
    - a span of time (decades, centuries, millennia).
  - contrast:
    - a negation: no, never, without, or any "n't";
    - an adversative: yet, but, despite, although...;
    - an earlier-than marker: "before", "until";
    - persistence: "still";
    - a vanishing or surviving verb;
    - the unknown: unknown, unsolved, unread, impossible...
  - question: the sentence starts on its question word.
  - Left out on purpose:
    - "after": it orders events forward and opens scene-setting lead-ins ("After the war, ..."), the date-first
      form QF-73 found on the topic bank's hooks;
    - a comparative with no "than" ("grows stronger"): read lexically, that is no comparison.

  Vague size adjectives ("massive", "immense") do not count, on purpose: "This massive Hindu temple" is the opening
  that Meta's assistant criticised. "giant" and "tiny" were in round 3's scale list and were removed at round 4 for
  the same reason. They state no measure, rank or comparison, only a vague size, as "massive" does. A size counts
  through a figure ("100-foot"), a superlative or a comparison.
- **One repair edit per opening** (round 5, review 4). The signal-only opening edit is keyed on the normalised first
  sentence (`_opening_key`), not on the whole narration. A later revision that keeps the first sentence sends no new
  opening edit. That covers a fact-check round, a line-picture rewording round and the final copy's revision.
  - A new first sentence may get its own one edit.
  - The probe from review 4 is the test `test_a_flagged_opening_gets_no_second_edit_from_a_later_revision`. It makes
    2 surgical calls in all (the edit, then the fact revision), not 3.
  - A rewording never changes the first sentence unasked (`test_a_rewording_never_changes_the_first_sentence_unasked`).
- **Pictures regeneration of a kept script.** The no-rewording guard in `_lines_against_pictures` now covers only the
  blocking hook faults (the cap and a label). A kept script whose only fault is a weak signal is reworded as on the
  parent, and its opening is not edited again.
- The label rule ("This X was...", "Meet the...", "X is a...") is unchanged. It now blocks too; the ruling names only
  the cap and the signal, and a label stays as round 3 built it.
- **How the ruling is built.**
  - A missing signal is a hook finding that does not block: `HOOK_SIGNAL_PROBLEM`, "Hook: weak signal: ...".
    `blocking_problems` leaves it out, as it does the story findings.
  - `_opening_faults` still hands it to the one opening edit (`_edit_story`, asked once per narration, with
    `HOOK_FORMS`). A library script's opening revision counts as that edit and is not asked again.
  - `verify_claims` refuses only blocking hook faults.
  - The flag:
    - The run stores `hook_signal_note` on the payload after the line-picture check (main.py), so it follows the
      words the run voices.
    - The review card shows it after the fact-sheet note, in the card's note format (⚠️ <b>Weak hook signal:</b>
      “opening” ... "Judge whether the opening holds.").
    - `probe` lists it with the other card notes.
    - It is not in `publishable.ENVELOPE_FIELDS` or `PUBLISHABLE_FIELDS` (QF-43), so it is never posted (tested).
- **A refusal names what passes** (review 3, finding 2). `script_engine.HOOK_FORMS` lists the accepted forms with a
  short example of each, the 12-word limit and the 8-token span. It appears in:
  - the no-signal finding the writer and every rewrite get;
  - the opening edit's findings, even for a length-only fault, since the cut sentence must keep a signal;
  - the system prompt;
  - the hook-form revision.

  A test checks that every example passes `hook_signal`, and that the vague words it names do not.

These are fixed rules in `src/config.py`, not settings (`.env.example` says so in section 1).

## Where the rule applies

- **Writer drafts.** `blocking_problems` keeps hook faults, and `_opening_faults` hands every one of them to the
  existing one-call opening edit (`_edit_story`), which runs before any rewrite. The edit is asked once per
  narration. When it cannot fix the hook, the draft goes back to the writer. In that case no copy or Story edit is
  spent on the draft first, because it is rewritten anyway.
- **A draft too long with a long hook.** Before this row the hook was advisory, so such a draft took the length edit.
  Counting the hook as blocking would have sent it to a full rewrite instead. Now the length edit runs first and the
  opening edit second, with no rewrite (`_length_edit_fixes`; measured below).
- **Curated and cached scripts.** `verify_claims` refuses a hook fault before any fact provider is called.
- **Library scripts** (`main.py _script_from_library` / `_library_payload`; this answers review 2, finding 1). A
  library script with a hook fault is no longer used as it is. It takes the same path as a library script that runs
  long for the voice: `_write_and_check_script`, with the opening edit's findings, then fact-checked again in full.
  It is never thrown away for a new script.
- **The hook-form revision** (`hook_learning.revision_note`) used to ask for "at most 14 words". It now asks for the
  rule's 12 words and the signal position. Left at 14, its result would fail the draft gate and the call would be
  wasted.
- The writer prompts and the reviser's word budget now say 12. The system prompt's own example was a 14-word sentence
  and is cut to 12.
- **Kept scripts.** A kept script (a regeneration for pictures, sound or a cover, which reuses the paid voiceover word
  for word) and a resumed run keep their words. A new hook there would mean paying for the voice again, and every
  script written after this row already passes the rule.
  - A pictures regeneration rewords the lines no photo shows, through `_write_and_check_script`.
  - For a kept script written before the rule, that path would put the opening through the hook edit. The
    rewording would then change the first sentence, and a pictures regeneration always refuses a change outside
    the asked lines, so every call would be spent on a result that is refused (review 3, N1).
  - So when the kept opening has a blocking fault (the cap or a label) and is not itself an asked line, its words are
    kept, the lines are left as they are, and no call is made (`main.py _lines_against_pictures`).
  - A kept opening with only a weak signal is reworded as on the parent; its opening is not edited again (round 5).
  - Both are tested in tests/test_pictures_every_line_2026_10_29.py.

## Measured

Run `python docs/reviews/quality-fixes-2026-11/QF-14/measure.py <repo root> [<runs dir> ...] [--sample <runs dir>]
[--judgments <file>]`. It prints JSON, writes nothing and calls no provider.

- Committed inputs: the synthetic openings, `data/script_library`, `data/scripts` and the topic bank.
- `<runs dir>` argument: the development `output/`. For it, measure.py prints only counts, run names and sentence
  hashes, never their text.
- Production sample (decisions/PROD-SAMPLE-counts-only.md): a copy of its `output/` is passed with `--sample`, and
  the builder's judgments of its openings (sentence hash to strong/weak) with `--judgments`; both stay outside the
  repository. For a `--sample` folder measure.py prints counts only: no run name, sentence hash or text. The
  judgments committed in measure.py (`JUDGED`) are those of the library and development openings.
- `before.json` is the parent (1ab9aded) and `measurements.json` is this row. Both were run with the same inputs.
- In the JSON, `runs_1` is development and `runs_2` is the production sample copy (counts only).

| | parent 1ab9aded | QF-14 |
|---|---:|---:|
| Limit / signal position / the cap blocks | 14 / none / no | 12 / 8 / yes |
| Synthetic bad openings when the one edit fails: label / 13 words / number at word 9 / vague size only | all ship | blocked / blocked / ships flagged / ships flagged |
| Synthetic concise controls (number, question, negation, time span, hyphenated number) | 5 of 5 clean | 5 of 5 clean |
| Library scripts used as they are with a hook fault | 4 of 4 | 0 of 4 (4 routed to the opening edit) |
| Real unique scripts blocked (library + dev + production sample, 52; cap or label) | 0 | 38 |
| Real unique scripts that ship flagged if their one edit fails (signal only) | n/a (no flag) | 3 |
| Real unique scripts that ship clean | 23 (29 with an advisory finding) | 11 |
| Blocked: library / dev / production sample | 0/4, 0/28, 0/26 | 4/4, 24/28, 16/26 |
| Refusal reasons (one script can have several): over 12 words / no early signal / label | n/a | 38 / 28 / 4 |
| Curated Derinkuyu (unreviewed, dry runs only) passes `verify_claims` | yes | no (17-word opening) |
| Topic bank suggested hooks that fail the rule (writer input only; QF-73 rewrites them) | 0 of 31 | 31 of 31 |

### Precision on the real corpus

Each real opening was read by the builder and marked "strong" (fact or riddle first, worth keeping) or "weak". The
marks are an input, listed in measure.py by sentence hash; the rule never reads them.

| Pooled 52 unique scripts | QF-14 |
|---|---:|
| Refused, weak opening (justified) | 25 |
| Refused, strong opening over the 12-word cap (justified by the acceptance's cap; trimmed by the opening edit) | 16 |
| **False rejects** (strong, within 12 words, refused) | **0** |
| Admitted strong | 10 |
| **False accepts** (weak, admitted) | **1** |

- The one false accept is a production opening that starts on a date span.
- The year is the only signal it has. A year still counts as a number because three strong real openings depend
  on it.
- The rule is a lexical floor on form and position. It does not judge how interesting the fact is; that is QF-17's
  judge.
- **The 0 is in-sample.** The contrast and time-span words were added after review 2 found two false rejects in this
  same corpus (contrast openings, now admitted). The round-4 widening changed none of the 52 verdicts: no real
  opening has a widened form in its first 8 words. The held-out figures below are the ones that say how the rule behaves on new hooks.

### Held-out openings (review 3, finding 1)

Two lists were written apart from the 52-script corpus. They are committed in `tests/data/` and scored by
`measure.py heldout`.

- **Part A** (`qf14_heldout_reviewer.json`): the round-3 reviewer's 30 strong and 20 weak or adversarial probes,
  copied verbatim.
- **Part B** (`qf14_heldout_builder.json`): 30 strong and 20 weak openings the builder wrote from the topic bank's
  key facts, at 2026-11-10T17:10Z, on code 282523a8. They were written before any round-4 list change and never
  edited after.

Part B was scored once before the change, as a baseline: strong 18 of 30 admitted, weak 0 of 20. The widening
followed the categories the review named. One of its additions coincides with a part-B miss: "best", an irregular
superlative, in "Nazca's figures are best seen from the air, yet were drawn on foot." That opening is still refused,
because it is 13 words.

| Held-out | parent 1ab9aded (nothing blocks) | QF-14 round 3 (282523a8) | QF-14 round 4 |
|---|---:|---:|---:|
| A (reviewer): strong admitted (recall) | 30 of 30 | 12 of 30 | 18 of 30 (0.60) |
| A (reviewer): weak or adversarial refused | 0 of 20 | 8 of 20 | 8 of 20 (0.40) |
| A (reviewer): precision of what is admitted | 0.60 | 0.50 | 0.60 |
| B (builder): strong admitted (recall) | 30 of 30 | 18 of 30 | 19 of 30 (0.63) |
| B (builder): weak refused | 0 of 20 | 20 of 20 | 20 of 20 (1.00) |
| B (builder): precision of what is admitted | 0.60 | 1.00 | 1.00 |

- **Stated plainly: about 4 in 10 strong new openings are still refused.**
  - A few are refused by the position rule as specified: the signal arrives at word 9 or later, as in "Kailasa was
    carved from the top down, with no room for error."
  - Most carry their surprise in meaning, not in any marker a lexical rule can read: "Gold nanoparticles make a
    Roman cup glow red.", "Roman concrete heals its own cracks when seawater gets in."
  - Each such refusal costs the opening edit. The edit is now told the accepted forms, so it can comply in one
    call, for example by bringing a figure, a contrast or a question forward.
  - Under the operator's ruling, an opening the edit leaves without a signal ships with the card's flag. It no longer
    ends a run.
  - A semantic hook judge (QF-17) is the way past it.
- **The floor is gameable** (review 3, N2). Any sentence with a listed word early passes, whatever it says:
  - 12 of part A's 20 weak or adversarial openings are admitted, for example "There is no place quite like this
    ancient city.", "In 1950, a team of archaeologists visited the site again.", "Two archaeologists wrote a book
    about the city." and "The first thing you notice is the colour of the stone.";
  - and the corpus's own date-span false accept.

  The rule checks form and position only. It catches the openings writers actually produce (part B's natural weak
  openings: 20 of 20 refused), not a writer trying to game it.
- `tests/test_qf14_hook_enforcement.py::test_held_out_openings_do_not_regress` pins these round-4 figures as floors.
  They were measured once and not tuned against.
- **The cost of the cap.** 16 of the 41 refusals are strong openings over 12 words. Seven of them are 13 or 14
  words. These go to the one opening edit, not a rewrite.

Every refusal, with its run, word count, reasons and mark, is in `measurements.json` under `corpus.*.refusals`. Only
the library's sentences are quoted there, since they are committed data.

Refused development openings (quoted; development output is our own test data):

| Run | Words | Reasons | Mark |
|---|---:|---|---|
| amber_room_heist_1792790550 "In 1941, German soldiers of Army Group North disassembled the Amber Room in 36 hours under expert supervision." | 18 | long | strong (over cap) |
| amber_room_heist_1792805842 "Inside Russia's Catherine Palace, an entire royal chamber was crafted from fossilized resin." | 13 | long, signal | strong (over cap) |
| damascus_steel x2 "Damascus steel is a high-carbon crucible steel forged in the Near East, ..." | 21 | long, signal | weak |
| gol_gumbaz "Can a quiet whisper travel across an estimated 144-foot (44 m) masonry dome?" | 13 | long | strong (over cap; the bracketed conversion is itself against the prompt) |
| greek_fire "In the tenth century, Byzantine forces defeated Kievan Rus' longships in the Golden Horn using Greek fire." | 17 | long, signal | weak |
| hal_saflieni "Beneath the town of Paola, Malta, prehistoric builders engineered ..." | 23 | long, signal | weak |
| kailasa "This massive temple was never built from the ground up; ..." | 21 | label, long | weak |
| nazca_1792961797 "Across southern Peru's Nazca Desert, immense figures etched into open soil have defied erosion for two millennia." | 17 | long, signal | strong (over cap) |
| odessa "Beneath Odesa, a major seaport city located in Ukraine ..." | 26 | long, signal | weak |
| richat_1792737039 "Seen from orbit, a bullseye across the Sahara desert looks like a massive planetary scar, yet its heart holds no impact crater." | 22 | long, signal | strong (over cap) |
| richat x4 (situated near Ouadane / visible from space x2 / a massive circular formation) | 13-26 | long, signal | weak |
| roman_concrete_1793161347 "Modern concrete structures can begin to crumble just a few decades after construction, yet Rome's Pantheon ..." | 30 | long, signal | strong (over cap) |
| singing_colossi "In 27 BC, a major earthquake shattered the northern colossus, ..." | 22 | long | weak |
| trepanation x2 "Trepanation is the oldest documented surgical procedure ..." | 18 | label, long | weak |
| tunguska "On the morning of 30 June 1908 at around 7:14 local time, ..." | 23 | long | weak |
| aswan_1792985912 "Deep in Egypt's Aswan quarries lies a granite monolith that would have been nearly one-third larger than any obelisk ever erected." | 21 | long, signal | strong (over cap) |
| aswan_1792993374 "Deep inside an ancient Aswan granite quarry, Egyptian builders carved a colossal monolith ..." | 25 | long, signal | weak |
| aswan_1793056896 "Deep inside an ancient quarry in Aswan, the largest obelisk ever attempted remains trapped in solid bedrock." | 17 | long, signal | strong (over cap) |
| wieliczka "Near Kraków in southern Poland, the mine's rock salt naturally displays various shades of grey, ..." | 23 | long, signal | weak |

### Opening timing in the real voiceovers (67 voiced runs, dev + production sample)

Each time below is when the word ends in the run's own words.json.

| | |
|---|---:|
| First sentence ends (median / max) | 6.80 s / 13.35 s |
| The 12th word ends (median), where a 12-word hook would end at that run's pace | 4.88 s |
| Runs whose first sentence runs past its 12th word | 49 of 67 |

### Calls and spend (review 2, finding 3)

The routing replay runs every real script through the tree's own `generate_script`. Its writer is a stand-in that
returns the same script every time, which is the worst case: a writer that keeps its opening. Every edit call is
counted. There are two modes: the opening edit fixes the hook, or it fails. Heavy calls per run:

| | parent: mean (max) | QF-14, edit fixes: mean (max) | QF-14, edit fails: mean (max) |
|---|---:|---:|---:|
| Library, replayed through `generate_script` (4) | 2.0 (2) | 2.0 (2) | 4.0 (4), 4 of 4 end without a script |
| **Library, the real path** (`library_path`) | **0**: used as is, no call, no fact check | **at least 1 edit each (4 of 4), then the full fact checks** | |
| Dev (27 replayable) | 2.0 (10) | 2.0 (6) | 3.56 (4), 23 of 27 end without a script |
| Production sample (26) | 2.69 (10) | 2.35 (6) | 2.96 (4), 16 of 26 end without a script (the 3 signal-only ship flagged) |
| A long draft with a 13-word hook | writer + length edit (hook kept at 13) | writer + length edit + opening edit, no rewrite | |

- **The library's real path.** On the parent, a shipped library script that fit the narrator cost no heavy call: it
  was used as is. Now each of the 4 costs at least one writer edit, then the full fact checks (review 3, N3).
- **The parent already spent this call.** Its `_edit_story` already sent a first sentence over 14 words to the
  opening edit, so when the edit works the mean does not rise. The QF-14 max falls from 10 to 6 because no copy
  edit is spent on a draft whose hook is still failing.
- **Where the cost is.** It is in the failure mode, not the call count.
  - If the writer and the edit both keep an opening over the cap (or a label), the run now ends with no script
    (ScriptUnavailableError, or the curated fallback where one exists), where before it shipped that opening.
  - A signal-only opening ships flagged instead (operator ruling).
  - The stand-in is a worst case: the real writer is told the fault and the accepted forms on every rewrite.
  - The one allowed development run (below) did not reach a draft, so the writer's real compliance is still
    unmeasured.
- **Size of one heavy writer call.** The median real `script_writing` call in development `llm_calls.jsonl` (100
  calls) was 3,054 tokens in, 216 tokens out and 44 s. An opening edit is one such call or smaller.
- **Who pays.**
  - Development: on the free Gemini writer while `FREE_TIER_HEAVY` is true. Each edit is one more request against
    the free key's shared daily quota.
  - Production: on the billed heavy key, inside the existing `PAID_FALLBACK_HEAVY_MONTHLY_USD` cap.
  - No new spend path or provider. The row's own build and tests made no call.

## Design-freeze scope: the first frame, the hook frame and the first caption burst

- **No renderer changes in QF-14.** What changes is the text: the first sentence, and so the first caption bursts
  and the voice over the first frame.
  - **Hook text length.** The 52 real openings had a median of 17 words before (10-30). After, an admitted opening
    is 12 words at most. The refused ones get new words from the writer's opening edit, which needs a live model.
    So the "after" text of a refused run cannot be produced in a build without inventing copy.
  - **Opening renders**, in `renders/` (half-size JPEGs of real dev masters). Each shows the frame at 1.5 s and the
    frame where the first sentence ends, with the sentence, its length and the verdict. They are three pairs from
    the same topics: a refused opening (Amber Room 18 words, ends 7.97 s; Nazca 17 words, 7.07 s; Odessa 26 words,
    10.55 s) and an admitted one (Amber Room 12 words, 6.11 s; Nazca question 11 words, 3.36 s; Voynich 11 words,
    5.64 s).
  - **Hook frame** (`design_cards.render_hook_frame`). It draws its claim (at most 6 words, from a checked field),
    not the narration. The narration's first caption burst reaches it only through the claim guard ("the claim
    repeats the first caption burst"), so a changed opening can change only that guard's verdict.
- **Hook frame renders on real runs.** The freeze panel's `render_evidence.py --only hook` was run on the parent and
  on this row, with real run photos and the real first bursts from words.json.
  - Each tree produced 25 hook cards: 11 rendered and 14 refused, with the same refusal reasons.
  - All 47 frames are pixel-identical between the trees, and so are the manifests apart from render times.
  - Three half-size frames are in `renders/hook_frame/`, from Amber Room 1792801257 (first burst "In 1941,"), Nazca
    1793247464 ("How do you etch massive desert") and Tunguska 1792930600 ("On the morning of 30 June": that run's
    23-word opening is refused here).
  - No hook frame is wired into the composed video yet (QF-25).

## Branch-only test changes (none of these files' expected values exist on origin/main)

- `tests/test_hook_2026_10_28.py` (branch-only file).
  - Two GOOD_HOOKS openings were 13-14 words. They were cut to 12 with the same facts.
  - "a weak opening alone costs no rewrite" became "requires a corrected draft", because a hook fault blocks.
  - The kept-script assertion became "structure validation is separate from the hook rule".
- `tests/test_script_rounds.py` (branch-only).
  - The length fixtures opened on a 108-word "sentence", which drew an opening edit.
  - They now open on a valid 8-word sentence, so the test isolates the length edit. The expected edit list is
    therefore ["LENGTH"].
- `tests/test_story_motion_colour_2026_10_29.py` (branch-only).
  - "an opening edit that fixes nothing keeps the draft" became "cannot keep the draft". With no curated script,
    the run now ends after three drafts and the one edit.
- origin/main files, fixture inputs only, no assertion changed:
  - `tests/conftest.py`: the shared `curated_payload` opens on an 11-word sentence with the same facts. The
    repository's curated file is untouched and is refused by `verify_claims` (above).
  - `tests/test_script_guardrail.py`: the short synthetic draft gets an 8-word first sentence with a figure, and
    alphabetic unique tokens.

- `tests/test_pictures_every_line_2026_10_29.py` (branch-only).
  - Its fixture opening "Deep under the plain lies a city carved into soft rock." has no signal, so it would now
    take the pre-rule branch.
  - It became "Under the plain, a city descends eight levels into soft rock." (fixture input; no assertion changed).
  - That same pre-rule opening is the new test of the kept-script branch.

Regression test: `tests/test_qf14_hook_enforcement.py` (47 tests): 43 fail on the parent and 47 pass here.

## Lost fallback (review 3, N4)

The repository's only curated script, `data/scripts/derinkuyu_underground_city.json` (unreviewed, dry runs only),
opens on 17 words. It is now refused by `verify_claims` in both curated fallbacks. So a Derinkuyu dry run whose
writer fails ends without it. The file is a human-review record and is left untouched; whether to re-edit and
re-review it is the operator's call.

## Operator ruling: outcomes (measure.py `outcomes`, if each opening's one edit changes nothing)

| | blocked | ships flagged | ships clean |
|---|---:|---:|---:|
| 52 real scripts, pooled | 38 | 3 | 11 |
| strong (26) / weak (26) | 16 / 22 | 0 / 3 | 10 / 1 |
| library / dev / production sample | 4 / 24 / 16 | 0 / 0 / 3 | 0 / 4 / 7 |
| Held-out A (reviewer), strong 30 / weak 20 | 0 / 3 | 12 / 5 | 18 / 12 |
| Held-out B (builder), strong 30 / weak 20 | 1 / 7 | 10 / 13 | 19 / 0 |

- **On the parent nothing blocks.** Its "ships flagged" column is its advisory hook findings, which no card showed.
- **Every refusal in the corpus is over the cap.** The cap is what still blocks: 16 strong openings over 12 words
  (7 at 13-14), trimmed by the one opening edit.
- **Strong held-out openings without a signal now ship flagged** (12 and 10) instead of ending runs. The one strong
  held-out opening still blocked is 13 words.

## The one development run (operator, 2026-11-10)

`dev_run.py` ran with `RELIC_APP_ROOT` pointing at the development checkout. That is the launcher's own variable:
the app read its .env as usual, and the script never read, printed or copied it. The result is in `dev_run.json`
(no key, path or identifier).

- **Route, confirmed before any call by `--check`:** SCRIPT_ENGINE free, FREE_TIER_HEAVY true, the free key
  configured, writer gemini-3.8-flash.
- **In this process only:** paid fallbacks capped at 0, Claude last resort off, spare key off, and the calls held
  to the free quota (`free_llm.free_only`).
- **Dry:** only `ScriptEngine.generate_script`. No fact check, voice, render, Telegram or post. The cached fact sheet
  was used as loaded (no research). The word-pace history was not written.
- **Topic:** wieliczka_salt_mine. Its current opening, the library entry's, is 23 words with no early signal.
- **Outcome: no script, inconclusive.**
  - The free writer model did not answer in time: 1 heavy call, which free_llm tried twice; 323.8 s; 0 drafts;
    0 edits.
  - The run ended with "could not be reached ... run it again later", the existing provider-timeout path, not a
    hook refusal.
  - So whether the real writer reaches a passing opening in one edit is still unmeasured.
  - The operator allowed one run, so it was not repeated. A second run needs the operator's word.
