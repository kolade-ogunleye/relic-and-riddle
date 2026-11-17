# QF-61: count and number fidelity for illustrations

Row (project-quality-review-2026-11-05.md, 9.2): the illustration prompt carries the fact's counts and sizes, but
nothing checked that the picture came back with them. Acceptance: a count mismatch is rejected; an element that is
not in the facts is rejected.

Round 5 follows the operator's decision after review 4 (decisions\QF-61.md, answer A, 2026-11-12: structured counts
only). Rounds 3 and 4 read counts out of the fact's prose and told the generator to draw them exactly; each review
found new ways the prose reader invented a number (label numbers before a verb, per-unit counts as totals, merged
groups, comparison sets such as Kailasa's "among the 34 monuments", hedges and plans made exact). Round 5 stops
reading numbers from prose for the generator altogether.

Round 6 removed the generator's "Draw only" list (option B, below) and took numbers out of the prose phrase by
phrase. Review 6 found that phrase removal still broke English and, in three real facts, reversed the meaning ("five
to ten percent of all cranial remains" became "all cranial remains"; the dev Richat run's sentence 3 lost its verb).

Round 7 follows the lander's rule after review 6 (decisions\QF-61.md, last section): prose is never edited. A
sentence that states any number is left out of the generator prompt whole; every other sentence is passed exactly as
the fact writes it. Fewer sentences, never an altered meaning. Everything else of rounds 5 and 6 stays: signing, the
structured quantities (the only source of a number in the prompt), the list-free prompt and the closed inventory in
the vision check.

## Round 7: prose is never edited

`illustration_elements.number_free_sentences(text)` cuts the text into sentences (`sentences`) and keeps those for
which `number_reasons` is empty, each exactly as written. The phrase-removal code of round 6 (`without_numbers`,
`sentence_problems` and their tables) is deleted; nothing in the generator path rewrites prose.

A sentence states a number when it has any of these (`number_reasons`):
- a character with a Unicode numeric value: digits of any script, superscripts ("²"), vulgar fractions ("½"), roman
  numeral signs ("Ⅻ"), circled digits; or a number symbol: % ‰ ‱ ° ′ ″ # №;
- a cardinal number word, hyphenated or not ("three", "thirty-five", "one-third"), with its plurals ("hundreds",
  "thousands", "dozens", "tens", "scores", "twenties");
- an ordinal ("first", "second", "nineteenth", "twenty-first", "firstly"). Decision: an ordinal is a number even when
  it is a position ("the second floor"), because it says how many come before; such sentences are dropped (review 6
  called this borderline; dropping is the safe side);
- a fraction or a share: "half", "halves", "halved", "quarter(s)", "thirds" ..., "percent", "percentage", "cent";
- a collective or multiplicative word (review 6, blocking 2): pair, couple, twin, trio, triple, triplet, quartet,
  quintet, sextet, double, quadruple, duo, dual, triad, "both", "single", "twice", "thrice", "dozen", "score",
  "fourscore" and every "-score" or "-fold" compound of a number word;
- "a" or "an" (or "half") before decade, century, millennium or fortnight ("a century later");
- a roman numeral: any upper-case token of two or more letters that is a valid numeral ("XII", "XVI", "IV", "DC"), a
  lone "V" or "X", and "I" after a capitalised word ("Edward I");
- an era marker: AD, BC, BCE, CE, or A.D./B.C./B.C.E./C.E. with stops;
- "one", except in four idioms that count nothing: "no one"; "one another"; "one of the/its/their/his/her/our/<name>'s"
  followed by a superlative ("one of the largest temples", "one of the foremost achievements", "one of humanity's
  earliest"; a superlative is most, least, best, worst, foremost, utmost or a word in -est that is not on a list of
  non-superlatives such as forest, west, interest); the pronoun after a determiner and before a relative, a
  preposition or the end ("the one that fell", "this one"); and the generic pronoun before a modal ("one can see").
  Every other "one" is a count, so "Only one of the towers survives", "Just one of the bronze gears remains" and "One
  of the stone towers collapsed" are dropped.

Not numbers (kept): "several", "many", "few", "numerous", "multiple", "only", "sole", "lone", "once". They say no
amount a drawing could be held to.

Sentences (`sentences`) end at ".", "!" or "?" (with a closing quote or bracket) before white space and a capital
letter, a digit, an opening quote or bracket, or the end. A stop after an initial ("Derek J."), after a word with
inner stops ("A.D.", "B.C.", "e.g.") or after a common abbreviation ("St.", "c.", "Mt.") does not end a sentence. When
unsure, two sentences stay one, so they are dropped together when either states a number; nothing is ever cut inside
a sentence. Semicolons do not split.

`image_gen.spec_from_facts` builds the subject (`_subject`) from the supporting facts' number-free sentences, whole
and in order: as many as fit in 180 characters, and a first sentence of up to 300 characters is taken whole rather
than clipped. Runs of white space become one space (no real statement has any). A sentence with a character the
prompt never takes from fact text (straight or curly double quotes, brackets, braces, backticks) is left out whole
instead of losing the character; no real sentence has one. With no sentence left, the fact gives no picture ("the
facts cannot be described without their numbers: every sentence of them states one"). The topic title and the place
are left out whole when they state a number. PROMPT_VERSION becomes "engraving-2026-11-13", so no cached picture made
from an older prompt is reused (review 6, non-blocking).

### Byte comparison (sentences altered = 0, by construction and by measurement)

`measure.py` and `real_data.py` check every spec with their own oracle, not the code's splitter: the spec's subject
must be a sequence of whole source sentences of the supporting facts' statements, byte for byte, joined by single
spaces (`subject_tiles`); the prompt must contain "Subject: <the spec's subject>". On this commit every subject tiles:

| Subject sentences not byte-identical to a source sentence | base | round 4 | round 5 | round 6 | after |
|---|---|---|---|---|---|
| real facts (sentences checked: 89 / 87 / 87 / 85 / 54) | 4 | 4 | 31 | 28 | 0 |
| 22 real runs (subjects: 58 / 55 / 55 / 55 / 41) | 69 | 65 | 73 | 73 | 0 |
| prompts without "Subject: <spec subject>" (facts / runs) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

The base and round 4 subjects were clipped at 180 characters or carried two facts' text; rounds 5 and 6 edited the
numbers out.

### Pictures lost (round 6 -> round 7)

Real facts (each a single sentence): 85 -> 54 specs. 33 physical facts now give no picture because every sentence
states a number (2 of them were already refused in round 6); 2 more are still refused for an unclear quantity. The 31
facts that had a picture in round 6 and have none now:
- amber_room_heist: amber-room-destruction-investigation-2004, amber-room-expansion-rastrelli,
  amber-room-origins-design
- antikythera_mechanism: mechanism-retrieval
- damascus_steel_nanotubes: damascus-03, damascus-05, damascus-07
- derinkuyu_underground_city: derinkuyu_airflow_engineering, derinkuyu_barrel_vaulted_room,
  derinkuyu_central_ventilation_shaft, derinkuyu_discovery_1963, derinkuyu_max_depth
- gobekli_tepe_enclosure_d: gobekli_tepe_pillar_43_calendar
- hal_saflieni_hypogeum: three_superimposed_levels
- kailasa_temple_monolith: kailasa-cave16-scale, kailasa-textual-framework
- lycurgus_cup_nanotech: fact-7, fact-8, fact-13
- nazca_lines_runway_paradox: nazca-dating-phases
- odessa_catacombs: fact_5
- richat_structure_sahara_eye: fact-11
- roman_self_healing_concrete: fact-1, fact-2
- singing_colossi_of_memnon: fact_1, fact_2, fact_9
- tunguska_event_1908: tunguska-treerings-13
- wieliczka_salt_mine: fact-4, fact-8, fact-9

No fact gained a picture. The lost facts include the ones round 6 rewrote: the Colossi statues (fact_1, "two
monumental stone statues"), Derinkuyu's depth and shaft, the Kailasa scale sentence and the Pantheon (Roman concrete
fact-2). Kailasa's relief fact ("considered one of the foremost achievements") states no number and keeps its picture.

22 real runs (counts only, read-only): 55 -> 41 specs. 4 development runs: 9 -> 7 (the Richat run's sentence 3,
facts fact-8 and fact-11, review 6's reversed sentence; and one sentence supported by Richat fact-9). 18
production-sample runs: 46 -> 34 (counts only, by the operator's counts-only rule). All 14 refusals give "the facts
cannot be described without their numbers".

Review-4 probes (52 sentences, each one sentence with a number): 46 -> 1 spec. The one left is the structured ship
fact, which now carries a second, number-free sentence.

Structured counts: a fact with a signed count always states that number in its statement, so its counted sentence is
never in the subject. A fact made only of that sentence gives no picture; a fact (or a pair of supporting facts) with
another, number-free sentence gives a picture whose only numbers are the signed counts. The probes for this (the
three-mast ship, the six non-exact qualifiers, the Kailasa offer) carry one number-free sentence so that they still
produce a spec and are not vacuous (`non_exact_specs` 6 of 6, `kailasa_spec` true). No real fact sheet carries a
structured record yet, so this changes no real picture today. Whether the fact-sheet writer should be asked for one
number-free descriptive sentence beside a counted one is a decision for the lander or operator; this round does not
change the writer.

## Round 6: why no list in the generator prompt (option B)

Two options were compared on the real specs' prompts, with no generation (`measure.py`, part 4 and the inventory
counters; `after.json` "inventory_options"):
- Option A: keep the list, built as the union of the forms, the materials and every word of the subject that the
  vision check allows. There is no part-of-speech tagger, so "the subject's nouns" are its allowed words. On round
  6's 85 real specs (`before_round6.json`) the list has a median of 12 words and up to 20. 221 of its words name no
  object (participles such as "considered" and "carved", adverbs, abstract nouns such as "achievement" and
  "investigation"), in 75 of 85 lists; on this round's 54 specs, a median of 13 words and 139 such words in 48 lists. A
  generator told to "draw only" those words is asked to draw words that are not things, and any noun picker that
  misses a word brings back review 5's contradiction.
- Option B (chosen): no list in the generator prompt. Contradictions are 0 by construction. The prompt stays short
  and names the subject once. Nothing is loosened: the vision check rejects every drawn item the facts do not name,
  exactly as before (the cannon and the 7 smuggled items are still rejected).

| Inventory sentence in the generator prompt (real facts) | base | round 4 | round 5 | rounds 6 and 7 |
|---|---|---|---|---|
| prompts with a "Draw only" list | 0 | 39 | 39 | 0 |
| lists that leave out an "It clearly shows" form | 0 | 10 | 10 | 0 |
| lists that leave out a subject word the vision check allows | 0 | 39 | 39 | 0 |
| real runs: lists that leave out a form / a subject word | 0 / 0 | 8 / 28 | 8 / 28 | 0 / 0 |

## The design

- A number reaches the image generator only from a fact's structured quantities record, `fact["quantities"]`
  (new module `src/fact_quantities.py`): `{"version": 1, "fact_id", "binds", "counts": [{"element", "count",
  "qualifier", "source"}], "measures": [{"element", "measure", "qualifier"}], "signature"}`.
  - The fact-sheet writer proposes `counts` and `measures` with each fact (`models.DraftFact`, new optional
    `DraftCount` / `DraftMeasure` lists; the writer's prompt says when each qualifier applies).
  - The deterministic fact-sheet rules (`fact_sheet._apply_rules`) keep an entry only when `check_entry` passes: the
    element's words are in the statement, the source/measure words are the statement's own, they state exactly
    that one number, an evidence quote states it, and a measure has a physical unit. An element given twice is
    dropped.
  - An `exact` entry must also be exact as stated: the fact is not disputed, has no hedge and is strongly sourced as
    a number (tier 1 or two corroborating sites); its sentence has no approximation, bound, distributive, membership
    or comparison set, fraction, percentage, hedge, plan, negation or range; the number does not follow a name or a
    noun (a label such as "Cave 16"); the counted noun appears once in the statement; and the round-4 prose reader
    holds nothing in that sentence. These checks can only remove an entry.
  - The kept entries are signed with the fact under FACT_RECORD_KEY (new record kind `fact_quantities` in
    `src/record_signing.py`), bound to the fact's id, statement and evidence. The fact gate and record signing are
    unchanged; the signature is an addition. `fact_sheet.FACT_SCHEMA_VERSION = 2` is stamped on saved sheets.
    RULES_VERSION is unchanged, so no sheet is rebuilt. A fact without the field is valid and countless.
  - At use (`fact_quantities.checked`), a record of another version, an unsigned record, one signed under another
    key, one bound to other words or evidence, or one whose entries no longer pass gives nothing.
- Only `exact` entries are passed, as "Exact counts, checked against the sources: N element. Draw exactly these
  numbers." (`image_gen.counts_sentence`), plus "Draw no specific number of any other repeated element.". An
  approximate count is not passed, not even as "roughly N, do not depict an exact number". A drawing of roughly N
  still shows some exact number, which then reads as the fact's figure, and the vision check cannot verify "roughly".
  So omitting it is the safer choice. Range, minimum/maximum, per-unit and comparison-set entries are never a
  subject count.
- No structured count means no number. The prompt says `image_gen.NO_COUNT` ("No count of anything here has been
  checked: draw no specific number of any repeated element, and do not lay out repeated parts so that they can be
  counted."). The spec has no counts, so the vision check's count step has nothing to verify and invents no target.
- Prose is never edited (round 7, above). The subject is the facts' number-free sentences, whole and unaltered; a
  topic title or place that states a number is left out whole. Measures come only from structured exact entries and
  are quoted verbatim.
- Kept from round 4: the closed inventory rule (every content word of a depicted item is a word of the facts) and
  the refusal of an unclear prose quantity (`extract()`'s `unresolved_quantities`; approximations, partitives,
  ranges, vague and negated quantities still refuse the picture). `extract()` no longer returns counts or measures.
  It is used only to refuse and to build the inventory. Two facts whose checked counts of one element differ also
  refuse the picture.
- Existing fact sheets: no structured count was invented, and no signed sheet was changed. All 271 facts stay
  countless. Adding a record to a sheet would change the facts its signed card records bind (`facts_sha256`) and
  would need re-signing, so none was added.
- The vision rubric asks for `elements` only when the spec has counts.

## Measurements

`measure.py <repo root>`: the inputs are committed. They are the 271 tracked facts and the probe sentences. The vision
answer is mocked. Quantities records are signed in memory under a throwaway key. The oracle is the script's own.
"Prose numbers" are the figures and number words left in the generator prompt once the fixed template text (the
module's constant strings, era tables and viewpoint slot) and the structured counts sentence and measures are
removed; "one of the ..." is not counted. `before.json` is the base (84c3eeb), `before_round4.json` round 4
(24be1831), `before_round5.json` round 5 (f3791555), `before_round6.json` round 6 (6436f298) and `after.json` this
commit, all printed by this commit's measure.py (re-run on every tree for round 7: the structured probes gained their
number-free sentence and the byte comparison and fact list were added).

| Metric | base | round 4 | round 5 | round 6 | after |
|---|---|---|---|---|---|
| 158 physical real facts: specs | 89 | 87 | 87 | 85 | 54 |
| refused for a quantity / refused, every sentence states a number | 0 / 0 | 2 / 0 | 2 / 0 | 2 / 2 | 2 / 33 |
| real-fact prompts with prose numbers / prose numbers | 30 / 52 | 28 / 52 | 0 / 0 | 0 / 0 | 0 / 0 |
| real-fact spec counts + measures not from a structured field | 5 | 10 | 0 | 0 | 0 |
| real-fact subject sentences not byte-identical to a source sentence | 4 | 4 | 31 | 28 | 0 |
| real-fact subjects with a broken sentence (oracle BROKEN) | 1 | 1 | 18 | 0 | 0 |
| real-fact prompts with the no-number instruction | 0 | 0 | 87 / 87 | 85 / 85 | 54 / 54 |
| review-4 probes (52): specs | 52 | 52 | 52 | 46 | 1 |
| review-4 probes: prompts with prose numbers | 52 | 52 | 0 | 0 | 0 |
| review-4 probes: prose numbers / spec counts | 83 / 0 | 133 / 51 | 0 / 0 | 0 / 0 | 0 / 0 |
| structured exact ship count (3 masts, 2 sails, 34 cm) drawn exactly | n/a | n/a | yes | yes | yes |
| structured non-exact qualifiers (6) that became exact (specs made) | n/a | n/a | 0 (6) | 0 (6) | 0 (6) |
| Kailasa "34 monuments" offered as exact: kept / spec counts | n/a | n/a | no / 0 | no / 0 | no / 0 |
| countless ship fact: counts the vision check holds it to | 0 | 2 | 0 | 0 | 0 |
| count mismatch (2 masts for 3) accepted | yes | no | no | no | no |
| cannon absent from the facts accepted | yes | no | no | no | no |
| review-3 items the facts do not name accepted | 7/7 | 0/7 | 0/7 | 0/7 | 0/7 |
| faithful control accepted | yes | yes | yes | yes | yes |
| "Draw only" lists / leaving out a "clearly shows" form | 0 / 0 | 39 / 10 | 39 / 10 | 0 / 0 | 0 / 0 |

acceptance_met is true on this commit only.

Real runs: `real_data.py <root> --dev <output> --prod <production-sample copy>` reads the 22 runs QF-60 measured
(4 development, 18 production-sample copy). It is read-only, counts only and writes nothing; it lists supporting fact
ids for the development runs only. Each of the 137 narration sentences goes through `spec_from_facts` with the run's
payload and fact sheet. `real_data_base.json`, `real_data_round4.json`, `real_data_round5.json`,
`real_data_round6.json` and `real_data.json` hold the five results.

| Real runs | base | round 4 | round 5 | round 6 | after |
|---|---|---|---|---|---|
| specs (development / production sample) | 58 (9 / 49) | 55 (9 / 46) | 55 (9 / 46) | 55 (9 / 46) | 41 (7 / 34) |
| refused for a held quantity / every sentence states a number | 0 / 0 | 3 / 0 | 3 / 0 | 3 / 0 | 3 / 14 |
| prompts with prose numbers / prose numbers | 19 / 27 | 16 / 20 | 0 / 0 | 0 / 0 | 0 / 0 |
| spec counts + measures not from a structured field | 2 | 1 | 0 | 0 | 0 |
| subject sentences / not byte-identical to a source sentence | 91 / 69 | 86 / 65 | 86 / 73 | 86 / 73 | 41 / 0 |
| subjects with a broken sentence | 13 | 12 | 22 | 0 | 0 |
| prompts with the no-number instruction | 0 | 0 | 55 | 55 | 41 |

The 3 held-quantity refusals are approximate counts ("more than 600 separate entrances" twice, "up to 20,000
people").

## What is not measured

No real vision inventory exists. There is no record with `depicted_elements` or `count_seen`, and no AI illustration
in any development run or in the production sample. The inventory rule is therefore measured only on mocked answers,
as a boundary. No false-reject rate is claimed; calibration belongs to QF-67. No fact sheet yet carries a structured
count, so every real illustration is currently countless. Structured counts start with the next fact sheet built
(the writer's prompt asks for them). That build is paid research, not run here. How the 14 lost run pictures are
covered instead (a photo, a map or no picture) follows the planner's existing fallbacks and is not measured here.

## Regression

`tests/test_qf61_illustration_counts.py` covers the following:
- the review-4 probes give no number in the generator prompt;
- the Kailasa real sheet gives no count, and the Kailasa set offered as exact is not kept;
- a structured exact count yields exactly that number;
- the six non-exact qualifiers never become exact;
- 17 exact offers the statement does not state as exact are refused;
- disputed, hedged, weakly sourced and unquoted facts give no exact count;
- edited, rebound, future-version and unsigned records give no number;
- conflicting checked counts refuse the picture;
- `_apply_rules` writes and signs the record, and a fact without one is countless;
- countless facts get the no-number instruction, and the count check has nothing to verify;
- the round-4 inventory and vision tests still hold, and unclear prose quantities still refuse the picture;
- the 271 real facts give no prose number;
- round 6: no "Draw only" list leaves out an "It clearly shows" item; the closed inventory stays in the vision check;
  a clipped title never ends on a dangling word;
- round 7:
  - the phrase-removal functions are gone;
  - the 7 real facts review 6 named (the dev Richat run's sentence 3, trepanation fact-3, Wieliczka fact-5, the
    Antikythera gap and imaging facts, the Kailasa and Hal Saflieni dating facts) give no sentence and no picture;
  - review 6's synthetic and real-shaped examples give no sentence, or only the untouched number-free sentence beside
    them ("The king died in 1350, leaving ...", "The tower collapsed in 1902, crushing ...", "The roof burned in
    AD 64, ...");
  - 40 sentences with a number word or form are dropped whole: every form of review 6's blocking 2 and the lander's
    list (pair, twin, trio, couple, triple, double, half-dozen, dozen, fourscore, score, "XII", "½", "²", "Cave XVI",
    "Only one of the stone towers survives", "One of the stone towers collapsed"), ordinals, fractions, symbols,
    "both", "single", "a century", regnal numerals and era markers;
  - 12 idioms and number-free sentences pass verbatim ("one of the largest temples", "one of the foremost
    achievements", "No one", "one another", "the one that", "one can see", "Derek J. de Solla Price");
  - the sentence cutter keeps initials, eras and abbreviations inside a sentence and returns the text's own
    characters;
  - every real prompt subject tiles with whole source sentences, byte for byte (the test's own splitter);
  - a mixed fact (and a pair of facts) keeps only its number-free sentences, unaltered;
  - a subject is never a clipped sentence; a title or place with a number is left out whole.

Tests both ways (each run -n 4): base 84c3eeb4 200 failed / 8 passed; round 6 6436f298 75 failed / 133 passed; this
commit 208 passed. Every failure is an AssertionError (the new functions are read through asserting helpers).

Branch-only test changes (none of these files is on origin/main):
- `tests/test_illustration_tier_2026_11_02.py`,
  test_the_prompt_is_house_style_from_the_fact_sheet_with_no_named_artist, asserted `"34 cm" in prompt`, a number read
  from the prose "about 34 cm wide". Since round 5 it asserts `"34" not in prompt and image_gen.NO_COUNT in prompt`
  (decisions\QF-61.md, builder under the 2026-11-12 overnight delegation, for the reviewer and operator to confirm).
- Round 7 fixture inputs, no assertion changed: the shared fact F1 of `tests/test_illustration_tier_2026_11_02.py`
  and `tests/test_illustration_fidelity_2026_11_02.py` ("... a wooden case about 34 cm wide.") gains a second,
  number-free sentence ("The mechanism held its bronze gears in a wooden case."). Its only sentence states a number,
  so under the round-7 rule it gave no picture and the tiers and fidelity tests had nothing to draw.
- Round 7, in this row's own file: the ship, gear and Kailasa structured probes and the countless-fact probes gain a
  number-free sentence (same reason); the real-spec floor in `real_specs` is 54 (was 80): the facts whose only
  sentence states a number no longer give a picture, which is the rule. The round-6 phrase-removal tests are replaced
  by the round-7 tests above.

Downstream:
- `prevoice_lines.has_descriptors` follows `spec_from_facts`: the 14 run sentences with no number-free supporting
  sentence lose the generated-picture option; nothing else in the planner changes.
- The fact-sheet writer's schema gains two optional lists (round 5); `record_signing.KINDS` gains `fact_quantities`.
- PROMPT_VERSION is "engraving-2026-11-13" (cache keys change; an older cached picture is not reused).
- No new setting, dependency, spend or live call.
