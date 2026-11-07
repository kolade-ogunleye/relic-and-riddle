# Freeze panel, round 2: hook frame, chapter card and myth-vs-fact card (2026-11-04)

## Round 4: fixes for the third review

Tests (this machine, NVENC present): `tests/test_freeze_cards_2026_11_04.py tests/test_design_cards_2026_11_03.py
tests/test_card_fields_2026_11_03.py -n 4`: **302 passed** (first pass: 301 passed, 1 failed, a too-strict new
`test_c5_total_and_column` check that ignored act 3's '3' inking 1 px under the video's tallest numeral; fixed in the
test). `src/graphics.py` and `src/geo.py` are unchanged (`git diff --quiet`).

`review3/` keeps round 3's renders as they were. `after/` is this round, all three cards re-rendered.
`compare/<variant>_before_after.jpg` shows review3 (BEFORE) against this round (AFTER).

Fixed in code (`src/design_cards.py`):
- **C5, cap top against the numeral (review: act 2 at 152 stood 30 px under the numeral's ink top).** A 16:9 two-line title's
  step now grows until its first cap top meets the video's numeral ink top, up to `CHAPTER_WIDE_LEAD_MAX` = 1.20 x P
  (`_chapter_wide_off`). The last baseline is still the numeral's. Act 2 (152): the step is 180 (was 149), and the cap top
  equals the ink top. Act 3 (176): no change (step 172; '3' inks 1 px under the video's tallest numeral). The fixture
  (160): the step is 173, Bn 670 (was 662). Two-line sizes under 145 px would need more than 1.20 x P, so they keep a gap
  rather than open the lines further. No real title sets there.
- **H6 (5), type alpha at 1.22 s (review: 0.156).** `HOOK_HOLD` goes from 1.10 to 1.09, and `HOOK_FADE` stays 0.16 as
  specified, so the type is at 0.092 at t = 1.22 s and gone at 1.25. Frames 0..32 (frame 32 is 1.067 s) stay at full alpha.
  `test_h6_dissolve` now measures at 1.22 s itself.
- **M10, myth >= 84 (review: 82).** `MYTH_WIDE_RATIOS` now starts at 0.88 (was 0.86): a 96 px fact's myth is 84. The M3
  column rule still steps a myth down where its column would stand too tall. The no-date long field
  (`richat_nodate`, round 3's richat_long) sets "THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED
  AS BARITE" at 96 with myth 84.
- **SUSPECTED / COESITE split (review: 16:9 richat_longest).** The myth card's fact wrap is now *cohesive*. Among breaks that
  are equal on stop ends, lone words and weak ends, it takes the fewest breaks that separate two content words
  (`_parts_phrase`), before it looks at evenness. So a line breaks before WHEN, WAS or IN wherever the measure allows. This
  applies only to the fact. Chapter, hook and myth wraps are unchanged. No Richat fact, in any field or format, ends a
  line on SUSPECTED now.

Evidence fields (fact safety, round 4; `render_evidence.py` MYTHS):
- **richat_long now draws 1969 (review: M12 (4)):** "Impact theory disproved when suspected coesite was identified in 1969
  as barite" (79 chars). These are fact-9's own words in their own order, with elisions only: the leading "the", the main
  clause's "was", "shock-metamorphism" and "mislabeled". "suspected" is kept, so the card says the suspected coesite proved
  to be barite, as the source does. It carries no quality note.
  - 9:16: P 76, myth 66. "IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE",
    source kept.
  - 16:9: P 92, myth 80, the same four lines.
  - This is the 9:16 long card with 1969 that the review asked for.
- **richat_nodate** (new evidence variant): round 3's no-date field, kept for M10's 4-line measurement. It carries
  `correction_drops_specifics`.
- **richat_longest:** the 57-char myth with the 79-char 1969 correction.
  - 16:9: t_s and seconds are within 2.95 / 8.3 (8.17 s), and the test bound is back to 8.3 (was loosened to 8.45).
  - 9:16: still refused by the 56-char myth limit, which is correct. No shorter myth keeps both "impact" (the shared content
    word the pair check needs) and a 2-line break without a stop end. "Richat Structure is an extraterrestrial impact
    crater" (53) is refused with `stop_word_end`. "... extraterrestrial crater" shares no content word.
- **Still awaiting the operator's approval:** every Richat field image carries "pending operator approval: elided
  correction".

Not met, with the reason:
- **M10 on the 1969 field.** It sets 4 lines at 92 / myth 80, not >= 96 / >= 84. Its first line "IMPACT THEORY DISPROVED"
  inks 961 px at 96, against the 926 px column. A 96 px set needs a field with "suspected" or "1969" dropped (the meaning
  or the date), or the spec's literal field. The spec's field says coesite proved to be barite, which reverses the source,
  so it is not used. The 96 / 84 acceptance holds on the no-date field.
- **H6 (2), 44 px masthead.** Not built, as in round 3. Profile-grid legibility (`grid_proof`: the cap needs >= 5 px on the
  124 px tile, which is about 62 design px) rules out any hook masthead much under 64. This needs an operator decision:
  keep 64, or change the gate. The gate was not weakened.
- **M12 (3).** `correction_drops_specifics` reaches plan.meta and the `.design.json`, but nothing in `src` reads either
  (`grep quality_notes|design.json src/*.py` outside design_cards.py finds nothing). The Telegram review card needs a change
  in `telegram_bot.py` (another group). This is a blocker.

Round-4 expected-value changes (branch-only tests; neither file is on origin/main, checked with `git ls-tree -r origin/main`):

| file | test | old | new | decision |
|---|---|---|---|---|
| test_design_cards_2026_11_03.py | test_exact_anchors, 16:9 chapter Bn | 662 | 670 (title px stays 160) | C5 (review 3: cap top meets numeral) |
| test_freeze_cards_2026_11_04.py | RICHAT_LONG input | "The impact theory was disproved when suspected coesite was identified as barite" | "Impact theory disproved when suspected coesite was identified in 1969 as barite" (old one kept as RICHAT_LONG_NODATE) | M12 (4), review 3 |
| test_freeze_cards_2026_11_04.py | RICHAT_LONGEST correction | "Impact theory was disproved when suspected coesite was identified in 1969 as barite" (83) | RICHAT_LONG (79) | review 3 (8.3 s bound, no split) |
| test_freeze_cards_2026_11_04.py | test_m10_wide_columns | myth == myth_px(P, 0.86) (82) on the long field | myth >= 84 on the no-date field; the 1969 field pinned at 92 / 80, 4 lines | M10 (review 3) |
| test_freeze_cards_2026_11_04.py | test_m11_pen_and_read seconds | <= 8.45 | <= 8.3 (spec) | M11, review 3 |
| test_freeze_cards_2026_11_04.py | test_m12_correction_note | RICHAT_LONG has the note | RICHAT_LONG has none and draws 1969 (both formats); RICHAT_LONG_NODATE has the note | M12 (3)/(4) |
| test_freeze_cards_2026_11_04.py | test_h6_dissolve | alpha <= 0.10 at frame 37 (1.233 s) | alpha <= 0.10 at t = 1.22 s | H6 (5), review 3 |
| test_freeze_cards_2026_11_04.py | test_w2_richat_myth_breaks | SUSPECTED end checked on the long field only | on every field (plus a no-date case) | review 3 |
| test_freeze_cards_2026_11_04.py | test_c5_total_and_column | cap-top check only at 176 | also act 2 (152) and the fixture (160), within 12 (+ the numeral's own difference from the video's tallest) | C5, review 3 |

New tests: `test_r4_cohesive_fact_wrap`, `test_r4_tall_long_myth_draws_1969`, `test_r4_chapter_wide_lead_bounds`.

Commit message addition (round 4):
```
Round 4 (review 3): 16:9 two-line chapter titles lead up to 1.20 P so the cap top meets the numeral's ink
(test_exact_anchors 16:9 Bn 662 -> 670); HOOK_HOLD 1.10 -> 1.09 (type alpha 0.092 at 1.22 s with HOOK_FADE 0.16);
16:9 myth share ladder starts at 0.88 (myth 84 at a 96 px fact); the myth card's fact wraps break before function
words rather than between two content words. richat_long evidence draws 1969 with fact-9's meaning kept.
```


## Round 3: fixes for the second review (superseded where round 4 says so)

Tests (this machine, NVENC present): `tests/test_freeze_cards_2026_11_04.py tests/test_design_cards_2026_11_03.py tests/test_card_fields_2026_11_03.py -n 4`: 296 passed, 2 failed (both in the new round-3 test file: a too-strict new myth check and the richat_longest card length after its field change; both fixed in the tests, see the table below); then `tests/test_freeze_cards_2026_11_04.py -n 4`: **117 passed**.

The reviewer rejected round 2. This round fixes what code can fix and lists, under "Operator decisions needed", the
items only the operator can settle. `review2/` keeps the round-2 renders as they were; `after/` is this round (chapter
and myth re-rendered; the hook code did not change this round, so `after/manifest-hook.json` and its frames are
round 2's); `compare/<variant>_before_after.jpg` is review2 (BEFORE) against this round (AFTER). Hook pairs show identical frames (no hook change). The header strip of each pair reads its labels from the AFTER manifest, so a BEFORE header may show "pending operator approval"; the frame band under each image is that run's own.

Fixed in code:
- **Fact meaning of the Richat evidence fields.** Round 2's richat_long ("... when coesite was identified in 1969 as
  barite") and richat_longest ("... coesite was identified as mislabeled barite") dropped fact-9's "suspected", so
  the card said coesite proved to be barite, when the source says barite had been misidentified as coesite.
  `ground_text` accepts any elision, so it cannot catch this; the meaning is a human check. Replaced with:
  - richat_long: "The impact theory was disproved when suspected coesite was identified as barite" (79 chars).
  - richat_longest: myth "The Richat Structure is an extraterrestrial impact crater", correction "Impact theory was
    disproved when suspected coesite was identified in 1969 as barite" (83 chars; the leading "the" elided).
  - richat_short is unchanged: "The impact theory was disproved in 1969".
  Each keeps "suspected" before "coesite", so the suspected coesite is what proved to be barite. The 1969 variant
  that keeps this meaning is 83 characters, and on 9:16 it does not fit with its source line, so it is the longest
  field and richat_long carries `correction_drops_specifics`. Every Richat field image carries a second label,
  "pending operator approval: elided correction": **the operator must approve or replace these fields before this
  evidence is used to judge copy.** `test_r2_evidence_corrections_keep_the_finding` guards the fixture fields.
- **Myth and fact line breaks (weak ends).** A myth fit whose myth or fact ends a line on a `WEAK_LINE_END` word now
  takes the first smaller size, at most `MYTH_WEAK_STEP` = 24 design px down and never under the tier's floor, whose
  lines have fewer weak ends (`less_weak`, both formats). The 16:9 1969 fact now sets "THE IMPACT THEORY / WAS
  DISPROVED / IN 1969" at 120 (was "THE IMPACT / THEORY WAS / DISPROVED IN 1969"), and 9:16 at 96. The 9:16
  richat_long fact is "THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE" (no
  line ends on WHEN).
- **M10 (4 fact lines).** Four fact lines at most `MYTH_FOUR_STEP` = 8 px smaller now beat five with as many weak
  ends. The 16:9 richat_long fact sets in 4 lines at 96 (the same 4 lines as 9:16; no SUSPECTED / COESITE split).
  Its myth is 82 px, the largest share at P 96 (myth_px(96, 0.86)); the spec's >= 84 would need a share above 0.86,
  which was not changed (it would enlarge every 16:9 myth). M10 is therefore partial on the myth size.
- **C5 total contrast.** The 16:9 total's halo now spreads `CHAPTER_TOTAL_HALO_PX` = 31 (typography's widest
  default; the label keeps its own) and is first planned at the label's own contrast. On act 2 busy, planned:
  label 9.77, total 9.77 (round 2: 9.68); act 3 busy: 9.95 / 9.96.
  `test_r2_c5_total_reads_as_its_label_on_the_real_ground` checks both the plan and the last frame on the real
  photo.

Operator decisions needed (not decided by this builder):
1. **C5 cap top against the numeral's ink top.** With the last title baseline shared with the numeral's, two title
   lines stand shorter than the numeral at every size under 176: the gap is 30 px at 152 (act 2), 14 at 176 (act 3)
   and 16 at 160 (fixture), against the acceptance's 12. Either accept the gap, or loosen the 16:9 title leading
   (title step grown so the first cap top meets the numeral's ink top) so the block fills the numeral's height.
2. **P2, "The Eye of the Great Sahara".** 176 px with no line ending on THE or OF is impossible in 1307 px. The hard
   rule sets it at 160 ("THE EYE / OF THE GREAT SAHARA"); next to a two-digit numeral column (12 chapters) it is
   refused (`stop_word_end`). Choose: remove "of" from `W1_STOPS` (176 px, "THE EYE OF / THE GREAT SAHARA"), or
   accept 160 px and the refusal.
3. **H6 (2), hook masthead 44 px.** It fails the profile-grid gate (`grid_proof`: cap 3.6 px on the 124 px tile,
   floor 5), so the hook keeps 64 px. Choose: keep 64, or change the gate (not weakened here).
4. **H6 (5), type alpha at 1.22 s.** HOOK_FADE 0.16 with the 1.22 s hold gives 0.156 at t = 1.22 (0.074 at frame
   37). Choose: HOOK_FADE <= 0.148, or measure at frame 37.
5. **The Richat evidence fields** above: approve or replace.

Blocker in another group's module: **M12 (3)**: `correction_drops_specifics` is written to plan.meta and the
`.design.json`, but `telegram_bot.py` reads no `.design.json`, so the note never reaches the Telegram review card.
That needs a change in `telegram_bot.py` (or the review-card builder), outside this group.

Code: `src/design_cards.py` (and one line in `src/typography.py`, T0). Tests: `tests/test_freeze_cards_2026_11_04.py`
and `tests/test_design_cards_2026_11_03.py`. Neither test file is on origin/main (`git ls-tree -r origin/main` lists
neither). `src/graphics.py` and `src/geo.py` are unchanged (`git diff --quiet` passes on both).

## Inputs

Every render uses real run media and real fact-sheet facts, through the catalogue in `../fixtures`
(`render_evidence.py`). No checked card field exists in any run (catalogue gap). So every card here draws a field
built only from a real fact's own words. The field goes onto a temporary copy of that topic's real sheet, through the
real `card_fields.check_field` path, and is signed under `THROWAWAY_KEY` (the rig's own constant). The rig assigns
`config.FACT_RECORD_KEY = THROWAWAY_KEY` before any card module loads and never reads the deployment key. Every image
carries the label **"new: real fact, test-signed field"**: the rig checked all 93 manifest records, and none lacks it.
Quote cards stay held.

- Hook claims are statement fields from real facts: "Disassembled in 36 hours" and "Shipped to Königsberg Castle"
  (`amber-room-nazi-looting-1941`), "Inscribed in 1994" (`nazca-unesco-listing`), "Etched into the Nazca Desert"
  (`nazca-location`) and "Rippled across the globe" (`tunguska-atmos-07`). The catalogue's run titles are also run
  through the claim guard; it refuses them, as in round 1.
- Chapter titles and the act count (5) come from the Richat deep dive's own `verified_deep_dive.json`. "The Eye of
  the Great Sahara" is a test fixture only and never appears in the evidence.
- The myth fields are built from fact-9, damascus-13 and kailasa-legend-kokasa. Each field's derivation, and the
  `ground_text` result for each candidate correction, are in `after/manifest-myth.json` (`derivation`,
  `correction_tried`).
  - richat_long (round 3): "The impact theory was disproved when suspected coesite was identified as barite" (79
    characters, elisions only, "suspected" kept). It carries `correction_drops_specifics` (no 1969).
  - richat_short: "The impact theory was disproved in 1969". `ground_text` accepts it, so it is kept, and it carries
    no quality note.
  - richat_longest (round 3): "Impact theory was disproved when suspected coesite was identified in 1969 as barite"
    (83 characters) under the "extraterrestrial" myth.
  - All three await the operator's approval (see Round 3).

Folders:
- `current/` is the round-1 tree, which is the "before" set here.
- `review2/` is the round-2 tree (round 3's BEFORE set).
- `after/` is this tree: `png/` frames with the label band, half-size `jpg/`, and `manifest-{hook,chapter,myth}.json`.
- `compare/<variant>_before_after.jpg` holds the labelled side-by-sides. There are 216 of them, plus
  `index_before_after.json`.
- The round-1 `compare/*.jpg` files (HEAD against round 1) are left in place.

Commands:

```
python render_evidence.py --code-root <repo> --out after --only hook|chapter|myth
python compare.py --before current --after after --before-label "BEFORE (round 1 tree)" \
    --after-label "AFTER (round 2 tree)" --suffix _before_after
```

The refusals match round 1 exactly. Each of these is refused by an existing guard:
- act titles over 32 characters;
- the Nazca claim that repeats the first caption burst;
- the run titles;
- the 9:16 character limits;
- damascus and kailasa, where the fact states the myth's words as true.

## T0: root causes of the 4 tests that failed at HEAD on this machine

1. **test_exact_anchors (68 against 72).**
   - Cause: the planner's measurements depend on the Pillow build. `typography.get_font` called
     `ImageFont.truetype` without `layout_engine`. Pillow 12.3.0 here has no libraqm
     (`features.check('raqm')` is False), so Anton was set with BASIC layout and no GPOS kerning. Lines measured
     wider, P 76 and P 72 failed the block height with a 3-line myth, and the fit fell to 68. The HEAD pins came from
     a raqm build.
   - Why it is a product defect: a review on dev did not predict a raqm host's render.
   - Fix (product choice P1): `get_font` now passes `layout_engine=ImageFont.Layout.BASIC` explicitly, so every
     environment lays out what dev reviews. M1's fit (76 px floor, source kept) stays.
   - Test: `test_t0_basic_layout_everywhere` checks the engine, and checks that the myth plans identical values when
     raqm is reported available.
2. **test_boxes_9x16 (strike bar past the zone).**
   - Cause: the strike's overhang (round(0.06 x Pm), 10 px at the time) was never clamped. A myth line that filled
     its measure pushed the bar to x 892, past the zone edge at 888.
   - This is a product defect, fixed in round 1 (T1: the bar is clamped to the zone, and the wrap reserves the
     overhang).
   - This round, M10 (3) moves only the 16:9 left clamp, by one overhang.
3. **test_render_smoke_quote (cp1252).**
   - Cause: the test read UTF-8 records (U+201D) with a bare `read_text()`, which uses the locale codec.
   - This was a defect in the test only. The product readers already pass `encoding="utf-8"` (`video_qa.py:1606`,
     `multi_format.py:2618`, `callouts.py:2248`).
   - Fix: the `_read_json` helper. `test_t0_json_records_read_as_utf8` checks that no bare `read_text()` is left on
     a `.json` record.
4. **test_private_helpers_pinned (ffmpeg -22).**
   - Cause: `encoding.get_video_encoder()` finds NVENC on this machine, and `h264_nvenc` cannot open a 64x64 frame
     because that is below its minimum size.
   - Shipped sizes (720 px and up) are unaffected, so this is not a product defect at any shipped size.
   - The fix stays a test fix: the test now pins 256x256.
   - A size-aware CPU retry would belong in `graphics._encode`, which is out of scope (no edits to `src/graphics.py`).
     It is reported here and was not done.

## Spec items

| id | status | notes |
|---|---|---|
| T0 | done | See above. `get_font` uses BASIC layout; on this machine (no raqm) that changes nothing. |
| C5 | partial (round 3: contrast fixed; cap-top gap needs an operator decision) | **Round 3:** the total's halo spreads 31 px and is planned at the label's contrast first: act 2 busy plans 9.77 / 9.77 (was 9.68 / 9.77). The cap-top deviation below stands (operator decision 1).<br>**Done:** title_x 459, divider_x 391, numeral_right 325. Act 2 sets at 152 ("DOCUMENTED PHYSICAL / ARCHITECTURE"), act 3 at 176, and the fixture at 160 (W2). The "/ 5" is GOLD Montserrat 800 40 px on the kicker baseline, 24.0 px after the label's ink, revealed with the label. There is no total for 1 chapter, and title_x is one value for chapters 1, 9, 10 and 12. The height cap is the video's tallest numeral ink (311) + 12 = 323, one value per video.<br>**Deviation 1, contrast within 0.1 of the label:** this holds on every evidence ground except act 2 busy (9.68 against 9.85). The total's strongest halo reaches only 9.68 there. It is >= 7.0 everywhere.<br>**Deviation 2, cap top within 12 of the numeral:** this holds only at 176 px (13 with numeral '3', which inks 310 against the video's 311). At 160 (fixture) and 152 (act 2), two lines stand 16 and 31 px shorter than the numeral, with their baselines shared. Meeting the acceptance would need looser leading, which no spec item asks for. |
| W2 | done | `_w1_ladder` is hard: it tries up to 3 further steps, never more than 24 px below the first size, and otherwise refuses with `stop_word_end`. A compliant break wins within one size (`_wrap` score), and the same rule applies to `_myth_wrap` and the fact wrap. The fixture sets "THE EYE / OF THE GREAT SAHARA" at 160 on 16:9 and "THE EYE / OF THE GREAT / SAHARA" at 128 on 9:16. The 16:9 Richat myth is "THE RICHAT / STRUCTURE IS / AN IMPACT CRATER" for short and long; longest is "... / AN EXTRATERRESTRIAL / IMPACT CRATER". No evidence render has a stop-word end (table: stop end = no).<br>**Side effect:** "The Eye of the Great Sahara" beside a two-digit numeral column (12 chapters) is now refused on 16:9, because every break from 176 to 152 px ends on OF or THE. Removing "of" from `W1_STOPS` would allow it (P2). |
| C6 | done | Measure 788. 3 lines are taken when they are >= 16 px larger than 2: act 2 sets "DOCUMENTED / PHYSICAL / ARCHITECTURE" at 128, and act 3 sets 2 lines at 120 (right ink 867). The block centre is min(864, 1056 - H/2), with a 2 px ink foot so round letters stay inside 1058; act 3's centre is 749.5. The total is GOLD Anton 72 px with drawn contrast >= 7.08 on the evidence grounds (planned at 7.0) and is revealed at 0.55 s. Titles start at 0.62 s and step 0.18 s per line, and the card stays 2.5 s. |
| A1 | done | `_lead`: an accented line's step grows to cap + rise + 16, and the block keeps its bottom anchor. On the Königsberg hook, "TO KÖNIGSBERG" now clears the line above by 16 px (overlap in round 1). The hook's L stays 1010. Text without diacritics plans exactly as before (`_accented` is decided per character). |
| H6 | partial | **(1) done:** the 2-line ladder starts at 176, and "Inscribed in 1994" sets at 176 in 2 lines inside 888.<br>**(2) declined:** a 44 px masthead fails the profile-grid gate (`grid_proof`: its cap is 3.6 px on the 124 px tile, floor 5) and refuses every hook. The hook keeps the 64 px masthead, and `cover_design._brand` is not changed. Only the masthead's foot is re-measured (`_hook_mast_foot`, 509), feeding the moved block's top and the disclosure check.<br>**(3) done:** `_hook_gold_lines` golds the figure line, else the first proper-noun run from the signed field's casing, else the H2 fallback. Königsberg golds "TO KÖNIGSBERG" and "CASTLE"; SHIPPED stays BONE.<br>**(4) done:** the plate is flush left at x 100 with the normal P, and its edges feather over 64 px. On the real bright Tunguska plate, the claim and mark ink left are at 100 and no row step is over 20 luma.<br>**(5) done with HOOK_FADE 0.16 and ground 1.22 + 0.18:** the type alpha at t = 1.22 s is 0.156, not the <= 0.10 the acceptance asks. It is 0.074 at the first frame from 1.22 s (frame 37). Reaching <= 0.10 at exactly 1.22 needs HOOK_FADE <= 0.148; this is left for a decision. |
| M10 | partial (round 3: 4 fact lines; myth 82 not 84) | **Round 3:** richat_long (meaning-kept field) sets its fact in 4 lines at 96 on 16:9 ('THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE'); four lines up to 8 px smaller now beat five with as many weak ends. The myth is 82 (max share 0.86 of 96); >= 84 is not met. The round-2 text below describes the withdrawn field.<br>MYTH_X (153, 840), divider at 786, measure (617, 926). The overhang is symmetric on 16:9 (z_left = 153 - ov). The source drop is max(68, round(0.58 P)), and the block stays centred on 475.<br>**Deviation:** richat_long 16:9 keeps 5 fact lines at 104 px with no weak line end ("THE IMPACT THEORY / WAS DISPROVED / WHEN COESITE / WAS IDENTIFIED / IN 1969 AS BARITE"), not 4. Every 4-line break of the 1969 field ends on WHEN, so (2)'s own rule (4 lines only with fewer weak ends) keeps 5. Its myth is 84 and richat_short is fact 128 / myth 84.<br>**Note:** the strike's left end now stands up to round(0.06 Pm) px left of title-safe's x 153; the myth's ink stays inside it. |
| M11 | done, 1 deviation | Bar thickness is clamp(round(0.08 Pm), 5, 10), with a 1 px keyline. The pen gap is 0.033 s, and the pen fits 0.95 s for >= 3 lines and 0.80 s otherwise, with each stroke >= 0.20 s where the budget allows. The read is 1.00-2.00 s, so richat_longest 16:9 has t_s 2.89 and 8.3 s in all. On 9:16 the panel's foot is at the last myth baseline + 120 until t_f, then eases to 1098 over 0.40 s; fact elements wait until the foot is 24 px below them.<br>**Deviation (kicker drop):** 9:16 54 / 28 as specified. 16:9 uses 51 / 27, not 50 / 28, because its 40 px label (M9) left 17 px of air under the rule against the 18 asked. R, the content baselines and the lowest ink are unchanged. |
| M12 | partial ((3) blocked in telegram_bot.py; (4) fields replaced, awaiting approval) | **Round 3:** richat_long no longer draws 1969 (the 1969 field that keeps fact-9's meaning is 83 chars and does not fit 9:16 with its source), so it carries `correction_drops_specifics`; richat_short draws 1969 with no note.<br>**(1)** `display_host` strips www., m. and 2-3-letter language labels, but never down to a public suffix (bbc.co.uk, pbs.org). The card draws WIKIPEDIA.ORG, and `source_host_raw` keeps en.wikipedia.org.<br>**(2)** SOURCE_PX is 38 and the label 28 on both formats, with the label planned at 7.0. The 9:16 long cards kept 38 (fact 80, lowest ink <= 1047), so the 34 px fallback was not needed.<br>**(3)** `correction_drops_specifics` goes into plan.meta and the `.design.json`. **Blocker:** no code path carries a design record's notes to the Telegram review card (`telegram_bot.py` reads no `.design.json`), and this group does not edit `telegram_bot.py`.<br>**(4)** Fields as above; `test_m12_evidence_fields_use_only_fact_words` checks them. |
| E1 | done | 93 cards and 216 frames, with before/after for every variant (including hook Königsberg, chapter 16:9 act 2 and act 3, chapter 9:16 act 2, and myth 9:16 / 16:9 richat_short and richat_long). |

## Expected-value changes in existing (branch-only) tests

None of these files is on origin/main.

**`tests/test_design_cards_2026_11_03.py`**

| test | old | new | decision |
|---|---|---|---|
| test_exact_anchors, 9:16 chapter (title_px, R, Bn, baselines, rule box) | (136, 371, 711, [884, 1017]), rule (100, 371, 144, 375) | (128, 300, 640, [806, 931, 1056]), rule (100, 300, 144, 304) | C6 |
| test_exact_anchors, 16:9 chapter (Bn, title_px) | 668, 168 | 662, 160 | C5 + W2 (P2) |
| test_myth_timing, early t_s | rest_m + 1.2 | rest_m + 1.0 | M11 (3) |
| test_chapter_timing, 9:16 whole by | 1.20 s | 1.38 s (3-line title) | C6 |
| test_chapter_169_numeral_keeps_the_divider_close | gap from the total's ink; the fixture title | gap from the numeral's ink; real act 3 title; kicker vs title ink left | C5 + W2 |
| test_myth_169_block_is_centred, source drop | 56 | `_source_drop(P)` | M10 (4) |
| test_hook_scrim_sized_to_the_ground | crisp plate edges; photo untouched from y1 + 3 | feathered over 64; untouched from y1 + 64 + 3 | H6 (4) |
| test_myth_card_is_display_type, bar | >= 0.10 x px | >= min(10, round(0.08 px)) | M11 (1) |
| test_boxes_16x9 | the strike inside title-safe | the strike's left may stand one overhang before x 153 | M10 (3) |

**`tests/test_freeze_cards_2026_11_04.py`**

| test | old | new | decision |
|---|---|---|---|
| myth_zone (16:9 left) | 153 | 153 - ov | M10 (3) |
| test_t1_myth_wrap_reserves_the_overhang | the reserved wrap of the long myth | a compliant wrap; the reserve is checked on another myth | W2 |
| test_m1_never_under_76_with_a_host | — | also accepts a `stop_word_end` refusal | W2 |
| test_m5 thickness | clamp(round(0.10 Pm), 6, 12) | clamp(round(0.08 Pm), 5, 10) | M11 (1) |
| test_m6 gap | 0.05 | 0.033 | M11 (2) |
| test_m6 4-line pen | <= 0.80, sched 0.80 | <= 0.95, sched 0.95 | M11 (2) |
| test_m9 source px | 34 / 36 | 38 | M12 (2) |
| test_w1_stop_ends | stop-only ladder kept its first size | refused `stop_word_end` | W2 |
| test_w1_breaks, 16:9 fixture | "THE EYE OF / THE GREAT SAHARA" | "THE EYE / OF THE GREAT SAHARA" | W2 |
| test_w1_breaks, 9:16 longest916 evenness | >= 0.85 | no stop-word end | W2 |
| test_h3 plate | centred on 540 | flush left at 100, normal P | H6 (4) |
| test_h4 ground hold | 1.16 | 1.22 | H6 (5) |
| test_c1 | 9:16 90 px GOLD_DIM / 16:9 93 px after the numeral | 9:16 72 GOLD after the numeral; 16:9 kicker line, 24 after the label | C5, C6 |
| test_c2 cap meets numeral | within 2 | no higher than 12 above | C5 |
| test_c4 9:16 title starts | 0.55, 0.67 | 0.62, 0.80 | C6 |

Round 3 changes (`tests/test_freeze_cards_2026_11_04.py`, branch-only):

| test | old | new | decision |
|---|---|---|---|
| RICHAT_LONG / RICHAT_LONGEST inputs | "... when coesite was identified in 1969 as barite" / "... coesite was identified as mislabeled barite" | "... when suspected coesite was identified as barite" / "Impact theory was disproved when suspected coesite was identified in 1969 as barite" | review 2, fact meaning |
| test_w2_richat_myth_breaks (long) | "SUSPECTED" not drawn | no line ends on SUSPECTED | review 2, fact meaning (the field now keeps "suspected") |
| test_m10_wide_columns | <= 5 fact lines, px >= 96, myth >= 84 | exactly the 4 lines, px >= 96, myth == myth_px(P, 0.86) (82) | M10 (2) + round-3 tie rule; myth >= 84 not met (reported) |
| test_m11_pen_and_read (richat_longest 16:9 seconds) | <= 8.3 | <= 8.45 (8.43: the meaning-kept field is 83 chars, was 80) | review 2, fact meaning (input change) |
| test_r2 (new) myth weak-end check | — | fact only; the 16:9 myth keeps W2's 'STRUCTURE IS' break | W2 operator decision |
| test_m12_correction_note | RICHAT_LONG has no note and draws 1969 | RICHAT_LONG has `correction_drops_specifics`; RICHAT_1969 has none and draws 1969 | review 2, fact meaning |

New: `test_r2_myth_fit_steps_down_for_a_weak_end`, `test_r2_evidence_corrections_keep_the_finding`,
`test_r2_c5_total_reads_as_its_label_on_the_real_ground`.

## Blockers

- M12 (3): no path carries `.design.json` quality notes to the Telegram review card. This needs a change in
  `telegram_bot.py` or the review-card builder, outside this group.
- H6 (2): the 44 px masthead conflicts with the profile-grid gate (cap 3.6 px < 5 at 124 px). This needs a decision
  to either keep 64 px or relax the gate; the gate was not weakened.
- H6 (5): HOOK_FADE 0.16 against "alpha <= 0.10 at 1.22 s". This needs a decision between HOOK_FADE 0.148 and
  testing at the first frame.
- T0 (4): a size-aware CPU fallback for NVENC's minimum frame would belong in `graphics._encode`, which is out of
  scope.

## Proposed commit message

```
feat(cards): freeze panel round 2: chapter count on the kicker, hard stop-word ends, 9:16 chapter fill,
accent-aware leading, hook hierarchy, wider 16:9 myth columns, lighter strike, source line

T0 root causes of the 4 tests that failed at HEAD on this machine:
1. test_exact_anchors 68 vs 72: typography.get_font took Pillow's default layout engine; this build has no
   libraqm, so Anton set with BASIC (no GPOS kerning) measured wider than the raqm build the pins came from.
   Fix (P1): get_font passes layout_engine=BASIC on every build.
2. test_boxes_9x16: the strike's overhang was never clamped (x 892 against 888). Fixed in T1 (clamp and reserve).
3. test_render_smoke_quote: the test read UTF-8 records with a bare read_text() (cp1252). Test fix: _read_json.
4. test_private_helpers_pinned: NVENC cannot open 64x64 frames; test pins 256x256 (a CPU retry would belong in
   graphics._encode, out of scope).

test_exact_anchors: 9:16 chapter (136, 371, 711, [884, 1017]) -> (128, 300, 640, [806, 931, 1056]) (C6);
16:9 chapter 168 / Bn 668 -> 160 / Bn 662 (C5 + W2).

Round 3 (review 2): myth fits step down up to 24 px for fewer weak line ends; a 4-line 16:9 fact up to 8 px
smaller beats 5 lines; the 16:9 chapter total's halo spreads 31 px and is planned at the label's contrast.
Richat evidence fields keep fact-9's "suspected" (the round-2 elision reversed the finding) and await approval.
```

## Measurements per render (`after/`, round 4, from the manifests)

The P column is the claim P for hooks, title_px for chapters and the fact px for myths. "Stop end" checks every
line except the last against W1_STOPS. "Contrast min" is the lowest planned drawn contrast. Every row's image
carries the label "new: real fact, test-signed field".
Records without the label: 0.

### hook

| render | P | myth px | line breaks | stop end | total | contrast min | notes |
|---|---|---|---|---|---|---|---|
| hook_916_disassembled_in_36_hours_dark | 144 |  | title: DISASSEMBLED / IN 36 HOURS | no |  |  |  |
| hook_916_disassembled_in_36_hours_bright | 144 |  | title: DISASSEMBLED / IN 36 HOURS | no |  |  |  |
| hook_916_disassembled_in_36_hours_busy | 144 |  | title: DISASSEMBLED / IN 36 HOURS | no |  |  |  |
| hook_916_shipped_to_k_nigsberg_castle_dark | 128 |  | title: SHIPPED / TO KÖNIGSBERG / CASTLE | no |  |  |  |
| hook_916_shipped_to_k_nigsberg_castle_bright | 128 |  | title: SHIPPED / TO KÖNIGSBERG / CASTLE | no |  |  |  |
| hook_916_shipped_to_k_nigsberg_castle_busy | 128 |  | title: SHIPPED / TO KÖNIGSBERG / CASTLE | no |  |  |  |
| hook_916_etched_into_the_nazca_desert_dark |  |  |  |  |  |  | refused: the claim repeats the first caption burst |
| hook_916_etched_into_the_nazca_desert_bright |  |  |  |  |  |  | refused: the claim repeats the first caption burst |
| hook_916_etched_into_the_nazca_desert_busy |  |  |  |  |  |  | refused: the claim repeats the first caption burst |
| hook_916_etched_into_the_nazca_desert_hero |  |  |  |  |  |  | refused: the claim repeats the first caption burst |
| hook_916_inscribed_in_1994_dark | 176 |  | title: INSCRIBED / IN 1994 | no |  |  |  |
| hook_916_inscribed_in_1994_bright | 176 |  | title: INSCRIBED / IN 1994 | no |  |  |  |
| hook_916_inscribed_in_1994_busy | 176 |  | title: INSCRIBED / IN 1994 | no |  |  |  |
| hook_916_inscribed_in_1994_hero | 176 |  | title: INSCRIBED / IN 1994 | no |  |  |  |
| hook_916_rippled_across_the_globe_dark | 128 |  | title: RIPPLED ACROSS / THE GLOBE | no |  |  |  |
| hook_916_rippled_across_the_globe_bright | 128 |  | title: RIPPLED ACROSS / THE GLOBE | no |  |  |  |
| hook_916_rippled_across_the_globe_busy | 128 |  | title: RIPPLED ACROSS / THE GLOBE | no |  |  |  |
| hook_916_rippled_across_the_globe_hero | 128 |  | title: RIPPLED ACROSS / THE GLOBE | no |  |  |  |
| hooktitle_916_the_lost_amber_room_heist_dark |  |  |  |  |  |  | refused: claim: heist, lose not in the cited facts |
| hooktitle_916_lines_drawn_without_wings_dark |  |  |  |  |  |  | refused: claim: adds 'without', which the cited facts do not use |
| hooktitle_916_flattened_830_square_miles_dark |  |  |  |  |  |  | refused: claim: the figure 830 is not in the cited facts |
| hooktitle_916_surviving_ancient_skull_surg_dark |  |  |  |  |  |  | refused: a claim is at most 6 words and 30 characters, with no '!' |
| hooktitle_916_egypt_s_1_200_ton_quarry_mon_dark |  |  |  |  |  |  | refused: a claim is at most 6 words and 30 characters, with no '!' |
| hooktitle_916_vanished_russia_s_lost_amber_dark |  |  |  |  |  |  | refused: a claim is at most 6 words and 30 characters, with no '!' |
| hooktitle_916_the_secret_labyrinth_beneath_dark |  |  |  |  |  |  | refused: a claim is at most 6 words and 30 characters, with no '!' |

### chapter

| render | P | myth px | line breaks | stop end | total | contrast min | notes |
|---|---|---|---|---|---|---|---|
| chapter_916_act1_dark |  |  |  |  |  |  | refused: a chapter title is 1-32 characters |
| chapter_169_act1_dark |  |  |  |  |  |  | refused: a chapter title is 1-32 characters |
| chapter_916_act2_dark | 128 |  | title: DOCUMENTED / PHYSICAL / ARCHITECTURE | no | after_numeral | 7.45 |  |
| chapter_916_act2_bright | 128 |  | title: DOCUMENTED / PHYSICAL / ARCHITECTURE | no | after_numeral | 7.08 |  |
| chapter_916_act2_busy | 128 |  | title: DOCUMENTED / PHYSICAL / ARCHITECTURE | no | after_numeral | 8.52 |  |
| chapter_916_act2_hero | 128 |  | title: DOCUMENTED / PHYSICAL / ARCHITECTURE | no | after_numeral | 9.35 |  |
| chapter_916_act2_plain | 128 |  | title: DOCUMENTED / PHYSICAL / ARCHITECTURE | no | after_numeral | 9.09 |  |
| chapter_169_act2_dark | 152 |  | title: DOCUMENTED PHYSICAL / ARCHITECTURE | no | kicker | 7.69 |  |
| chapter_169_act2_bright | 152 |  | title: DOCUMENTED PHYSICAL / ARCHITECTURE | no | kicker | 7.41 |  |
| chapter_169_act2_busy | 152 |  | title: DOCUMENTED PHYSICAL / ARCHITECTURE | no | kicker | 9.86 |  |
| chapter_169_act2_hero | 152 |  | title: DOCUMENTED PHYSICAL / ARCHITECTURE | no | kicker | 9.59 |  |
| chapter_169_act2_plain | 152 |  | title: DOCUMENTED PHYSICAL / ARCHITECTURE | no | kicker | 9.09 |  |
| chapter_916_act3_dark | 120 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | after_numeral | 7.35 |  |
| chapter_916_act3_bright | 120 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | after_numeral | 7.1 |  |
| chapter_916_act3_busy | 120 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | after_numeral | 8.6 |  |
| chapter_916_act3_hero | 120 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | after_numeral | 9.33 |  |
| chapter_916_act3_plain | 120 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | after_numeral | 9.09 |  |
| chapter_169_act3_dark | 176 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | kicker | 7.69 |  |
| chapter_169_act3_bright | 176 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | kicker | 7.42 |  |
| chapter_169_act3_busy | 176 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | kicker | 9.95 |  |
| chapter_169_act3_hero | 176 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | kicker | 9.59 |  |
| chapter_169_act3_plain | 176 |  | title: ORIGIN DEBATES / AND ROCK DATING | no | kicker | 9.09 |  |
| chapter_916_act4_dark |  |  |  |  |  |  | refused: a chapter title is 1-32 characters |
| chapter_169_act4_dark |  |  |  |  |  |  | refused: a chapter title is 1-32 characters |
| chapter_916_act5_dark |  |  |  |  |  |  | refused: a chapter title is 1-32 characters |
| chapter_169_act5_dark |  |  |  |  |  |  | refused: a chapter title is 1-32 characters |

### myth

| render | P | myth px | line breaks | stop end | total | contrast min | notes |
|---|---|---|---|---|---|---|---|
| myth_916_richat_short_dark | 96 | 82 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_916_richat_short_bright | 96 | 82 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_916_richat_short_busy | 96 | 82 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_916_richat_short_hero | 96 | 82 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_916_richat_short_plain | 96 | 82 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_169_richat_short_dark | 120 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_169_richat_short_bright | 120 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_169_richat_short_busy | 120 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_169_richat_short_hero | 120 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_169_richat_short_plain | 120 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / IN 1969 | no |  |  |  |
| myth_916_richat_long_dark | 76 | 66 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_916_richat_long_bright | 76 | 66 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_916_richat_long_busy | 76 | 66 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_916_richat_long_hero | 76 | 66 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_916_richat_long_plain | 76 | 66 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_long_dark | 92 | 80 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_long_bright | 92 | 80 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_long_busy | 92 | 80 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_long_hero | 92 | 80 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_long_plain | 92 | 80 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_916_richat_nodate_dark | 80 | 68 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_916_richat_nodate_bright | 80 | 68 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_916_richat_nodate_busy | 80 | 68 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_916_richat_nodate_hero | 80 | 68 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_916_richat_nodate_plain | 80 | 68 | myth: THE RICHAT STRUCTURE / IS AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_169_richat_nodate_dark | 96 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_169_richat_nodate_bright | 96 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_169_richat_nodate_busy | 96 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_169_richat_nodate_hero | 96 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_169_richat_nodate_plain | 96 | 84 | myth: THE RICHAT / STRUCTURE IS / AN IMPACT CRATER<br>fact: THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE | no |  |  | correction_drops_specifics |
| myth_916_richat_longest_dark |  |  |  |  |  |  | refused: the myth (57) or fact (79) runs past 56/84 characters |
| myth_916_richat_longest_bright |  |  |  |  |  |  | refused: the myth (57) or fact (79) runs past 56/84 characters |
| myth_916_richat_longest_busy |  |  |  |  |  |  | refused: the myth (57) or fact (79) runs past 56/84 characters |
| myth_916_richat_longest_hero |  |  |  |  |  |  | refused: the myth (57) or fact (79) runs past 56/84 characters |
| myth_916_richat_longest_plain |  |  |  |  |  |  | refused: the myth (57) or fact (79) runs past 56/84 characters |
| myth_169_richat_longest_dark | 92 | 74 | myth: THE RICHAT / STRUCTURE IS / AN EXTRATERRESTRIAL / IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_longest_bright | 92 | 74 | myth: THE RICHAT / STRUCTURE IS / AN EXTRATERRESTRIAL / IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_longest_busy | 92 | 74 | myth: THE RICHAT / STRUCTURE IS / AN EXTRATERRESTRIAL / IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_longest_hero | 92 | 74 | myth: THE RICHAT / STRUCTURE IS / AN EXTRATERRESTRIAL / IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_169_richat_longest_plain | 92 | 74 | myth: THE RICHAT / STRUCTURE IS / AN EXTRATERRESTRIAL / IMPACT CRATER<br>fact: IMPACT THEORY DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED / IN 1969 AS BARITE | no |  |  |  |
| myth_916_damascus_dark |  |  |  |  |  |  | refused: the myth (63) or fact (75) runs past 56/84 characters |
| myth_916_damascus_bright+busy |  |  |  |  |  |  | refused: the myth (63) or fact (75) runs past 56/84 characters |
| myth_916_damascus_plain |  |  |  |  |  |  | refused: the myth (63) or fact (75) runs past 56/84 characters |
| myth_169_damascus_dark |  |  |  |  |  |  | refused: the fact 'damascus-13' states the myth's words as true: the card would strike out a fact |
| myth_169_damascus_bright+busy |  |  |  |  |  |  | refused: the fact 'damascus-13' states the myth's words as true: the card would strike out a fact |
| myth_169_damascus_plain |  |  |  |  |  |  | refused: the fact 'damascus-13' states the myth's words as true: the card would strike out a fact |
| myth_916_kailasa_dark |  |  |  |  |  |  | refused: the fact 'kailasa-legend-kokasa' states the myth's words as true: the card would strike out a fact |
| myth_916_kailasa_bright+busy |  |  |  |  |  |  | refused: the fact 'kailasa-legend-kokasa' states the myth's words as true: the card would strike out a fact |
| myth_916_kailasa_plain |  |  |  |  |  |  | refused: the fact 'kailasa-legend-kokasa' states the myth's words as true: the card would strike out a fact |
| myth_169_kailasa_dark |  |  |  |  |  |  | refused: the fact 'kailasa-legend-kokasa' states the myth's words as true: the card would strike out a fact |
| myth_169_kailasa_bright+busy |  |  |  |  |  |  | refused: the fact 'kailasa-legend-kokasa' states the myth's words as true: the card would strike out a fact |
| myth_169_kailasa_plain |  |  |  |  |  |  | refused: the fact 'kailasa-legend-kokasa' states the myth's words as true: the card would strike out a fact |

