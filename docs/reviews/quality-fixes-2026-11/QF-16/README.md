# QF-16: hedge rule (the automatic hedge's scope; no disputed fact in the opening, payoff or loop)

Spec (project-quality-review-2026-11-05, finding 11): "restrict auto-hedge to numbers/dates with ranges and forbid it
in hook/close". Built to the ruling of 2026-11-11 (`decisions/QF-16-question.md`, answered under delegation:
1 = B, 2 = P3, 3 = body qualifiers neither blocked nor flagged).

## What changed

- **The automatic hedge's scope** (`fact_sheet._neutral_hedge`, `narrative_hedges.auto_hedge_allowed`). A weakly
  sourced figure still gets "by one estimate, ..." / "by one account, ..." when it is:
  - a date; or
  - a measured quantity, meaning a number followed by a unit of measure (length, area, volume, mass, angle,
    percent, energy, pressure, time).

  The hedge keeps the statement's own words ("by one estimate, <statement>"). **Range statements are deferred to
  proposed QF-112.** Rounds 3 and 4 tried to say a quoted range in place of the figure, and both produced wrong or
  false text: a date or lifespan made a range, a page range taken, "up to" / "more than" kept before the range, a
  hyphenated "85-meter" broken, a range of another dimension adopted. The lander's decision after review 3, round 4
  (end of decisions/QF-16-question.md) turns the rewrite off: `narrative_hedges.with_quoted_range` returns the
  statement unchanged, and the row's tests assert it is never rewritten. A weak count or event figure gets no automatic hedge ("by one estimate, miners carved four chapels", the
  defect the spec cites, and "107 inscriptions"). It is then dropped, unless the sheet's writer authored a hedge.
- **Stated departure from the spec's literal "with ranges".** A single weak measured figure or date is still hedged
  automatically (ruling B). The literal rule would drop 40 of the 47 automatically hedged real facts, including each
  topic's headline measurement. It would also change the outcome of four origin/main tests
  (`test_integrity_gates` x3, `test_unesco_and_upgrades`). Under B, none changes.
- **Cached sheets are re-checked in place** (`fact_sheet.load`). RULES_VERSION stays 2 (review 2, finding 3: a bump
  would research all 20 topics again).
  - A stored automatic hedge (recognised exactly by `_is_auto_hedge`) on a count or event figure drops that fact.
  - On a measured figure or a date, the stored hedge stays as stored (the statement's own words).
  - Authored hedges are untouched.
- **The opening, payoff and final loop** (`script_engine.hedge_problems`, ruling P3). Each finding first gets the one
  repair edit that QF-14's opening and QF-15's payoff already share (`_opening_faults`, `HEDGE:` in
  `opening_edit_findings`; the loop sentence joins its scope, plus at most one body sentence to take the claim).
  Then:
  - **blocks** (`HEDGE_PROBLEM`): a disputed fact of the run's own sheet in those sentences. That is either the
    automatic lead ("by one estimate" / "by one account"), or a figure that only a disputed fact states, in its
    statement or its hedge. Metric/imperial conversions and spelled-out numbers count. Small whole counts (under 10)
    are not used to tie a sentence to a fact.
  - **ships flagged on the review card** (`HEDGE_WORDING_PROBLEM`, `hedge_note`, "⚠️ Hedged payoff: ... Judge
    whether it should stand on a firmer fact."): any other qualifier there, for example "roughly", "most likely" or
    "suggests" or "maybe". An approximation word counts only before a quantity, so spatial "around the city" and "some still
    stand" are not qualifiers.
  - **exempt**: a question.
- **Body qualifiers** are the writer's. Neither the draft check nor `verify_claims` reads them (round 2 refused 20 of
  49 real scripts over them).
- **One quantity matcher** (`narrative_hedges`: digits, spelled numbers, multiples such as "twice", "dozens",
  "one-third") serves every marker. "By one estimate, thirty-five gears" therefore reads as quantitative (review 2,
  finding 4).
- **Where the placement rule applies**:
  - writer drafts (`draft_problems`, against the draft's sheet);
  - `verify_claims`, before any fact provider (curated and cached scripts, against the topic's sheet);
  - library scripts, which get the one edit before use (main.py);
  - the line-picture check, where a kept script whose close states a disputed fact stands unless that sentence is
    an asked line, as for QF-14 and QF-15.
- The flag is stored after the line-picture check, beside the QF-14 and QF-15 flags. It is in `probe.NOTE_KEYS` and
  shown on the card. It is in neither publishable list (tested).
- One repair edit per close sentence, as QF-14 and QF-15 do: a revision that keeps a flagged sentence sends no new
  edit.
- Prompts: the writer, the reviser and the MANDATORY HEDGES note each say that hedged claims stay in the body.
- Findings file under `script.hedge` (improve).

## Before / after (`measure.py`; measurements.json on this tree, before.json on a3946226)

| | before | after |
|---|---:|---:|
| Synthetic close cases as ruled (4 block, 3 flag, 5 controls clean) | 5/12 | 12/12 |
| Quantitative qualifiers read as quantitative (spelled, multiples) | — | 6/6 |
| Automatic-hedge cases as ruled (2 counts dropped, 5 kept in their own words) | 5/7 | 7/7 |
| Real sheets: facts loaded (271 stored, 20 sheets) | 241 | 228 |
| Automatic hedges loaded (47 stored) | 48 | 35 |
| Facts dropped on load: weak count/event figures | 0 | 13 |
| Sheets researched again on next use (all built under older rules before this row) | 2 | 2 |
| Smallest loaded sheet (minimum 6) | 11 | 11 |
| 52 real scripts, hedge rule blocked / flagged / clean (if the one edit changes nothing) | 0/0/52 | 2/11/39 |
| — library 4 / dev 28 / production-sample copy 26 (unique per source) | 0/0/4, 0/0/28, 0/0/26 | 0/1/3, 2/4/22, 0/7/19 |
| Builder's reading of the 13 hits: real qualifier or disputed claim / false hit | — | 13 / 0 |
| With QF-14's and QF-15's rules too, pooled blocked / flagged / clean | 38/12/2 | 38/12/2 |
| Body sentences with a qualifier (not read by the rule) / refused | — / 0 | 53 (45 quantitative) / 0 |

Notes on the table:
- **Both blocks are dev scripts.** Each says the automatic lead "By one estimate" in its payoff or loop. Both scripts
  are blocked by QF-14's hook rule as well.
- **The 11 flags.** Most are approximations on a figure in the opening or payoff. Four are uncertain claims in the
  close.
- **Combined outcome.** No script's combined outcome changes: every script the hedge rule touches already had an
  opening edit or a block.

**Spend and calls** (routing replay, heavy calls per run, worst-case writer that keeps its words): unchanged from
the parent.

| | dev: when the edit works | dev: when it fails | production sample: when it works | production sample: when it fails |
|---|---:|---:|---:|---:|
| Heavy calls per run | 2.11 | 3.67 | 2.58 | 3.19 |
| Runs ending with no script | 1 | 23 | 4 | 16 |

Library: 2.0 / 4.0 calls per run, and 0 / 4 runs with no script. The hedge findings ride in the one edit that the
hook and payoff faults of the same scripts already get.

**Rebuild cost** (review 2, finding 3): none. No sheet is researched again because of this row. A rebuilt sheet
would lose the same 13 weak count/event facts that load now drops (all `number`, none below the minimum).

**Narration pace** (decisions/QF-88-question.md): unchanged, because nothing is voiced again and the edit keeps the
length.

| | bursts | over 17 cps | over 20 cps | median | max |
|---|---:|---:|---:|---:|---:|
| dev | 772 | 277 | 58 | 15.7 cps | 37.5 cps |
| production sample | 851 | 271 | 72 | 15.62 cps | 29.17 cps |

The touched close sentences are spoken at a median of 17.21 cps (dev, 2 timed) and 14.49 cps (production sample,
6 timed).

## Inputs and reproduction

```
python docs/reviews/quality-fixes-2026-11/QF-16/measure.py <repo root> <dev output dir> \
    --sample <copy of the production sample's output dir> --judgments <readings file outside the repo>
```

Committed inputs: data/fact_sheets, data/script_library and the synthetic cases. The production sample and its
readings stay outside the repository; only counts are printed for them. The production sample is read against the
committed sheets, which production ships (`script_library.seed_fact_sheet`). No network, model or provider call.

## Known limits

- A lexical floor, not a judge of truth.
  - A disputed fact said with no figure and no automatic lead in the close is not seen.
  - A qualifier the marker list lacks is not flagged.
  - The fact checks still decide what is supported, and QF-17's semantic judge is the way past this.
- The unit list is generic grammar. A measured figure written with an unusual unit counts as a count, and its weak
  fact is dropped rather than hedged.
- The real writer's compliance with the edit is unmeasured: no live call is allowed in a build.
- No pixel surface changes (the card note is text), so there are no renders.
