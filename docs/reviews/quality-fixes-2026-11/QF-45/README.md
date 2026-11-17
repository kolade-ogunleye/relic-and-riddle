# QF-45: Learned-rule grammar and provenance

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2. Learned writer rules
(`src/improve.py`, `_draft_rule` / `adopt` / `learned_rules`) were drafted by the free model from
checker text, kept if 10 to 320 characters long, and after one Adopt tap appended to every writer
prompt. The checker examples went into the prompt verbatim (unfenced), so a quoted script, source
or operator reply could steer the draft, and a rule carrying a hashtag, link or name was accepted.
The Adopt card did not say where the rule came from.

## Change

- `src/improve.py`
  - `rule_problem()`: the rule grammar. One sentence, `Never` or `Always` + lowercase wording
    (letters, digits, spaces, `, ; : ' " ( ) / -`, optional final full stop), 4 to 40 words.
    Refused with a reason: `#` (hashtag), a URL (`http`, `www`, `://`, a bare domain such as
    `t.ly`), `@` (handle), any capital after the first word (proper noun / acronym / second sentence).
  - `_draft_rule()`: the examples are sent as data inside a per-call random fence
    (`<examples_<16 hex>>`), each one JSON-encoded, whitespace-folded, clipped to 300 characters and
    with `<`/`>` escaped; the system prompt names the fence and the grammar. A draft that echoes the
    fence or fails `rule_problem()` is discarded (logged with the reason), so no proposal is made.
  - `make_proposals()`: a drafted rule's `change` carries `provenance` (drafted by the free model,
    date, class, findings and runs counted, the finding sources, grammar); `adopt()` keeps it in
    the patch record.
  - `card()`: the Adopt card shows a provenance line under the proposed rule; a proposal made
    before provenance was kept says so.
  - `learned_rules()`: an already adopted rule carrying a hashtag, link or handle is never put
    into the prompt (rules adopted before the grammar are otherwise kept; reverting stays with /improve).
- `tests/test_qf45_rule_grammar.py`: the regression test.
- `tests/test_findings_classifier_2026_10_28.py` (branch-only, not on origin/main): the free-quota
  test's stub draft changed from "Ask an either/or question..." to "Always ask an either/or
  question...", assertion `startswith("Ask")` -> `startswith("Always ask")`, because the row's
  grammar now discards a draft without the Never/Always head.

## Metric (`python measure.py <repo root>`)

| | before (f204a08) | after |
|---|---|---|
| drafts with `#` or `http` kept by `_draft_rule` (acceptance) | 5 of 5 | 0 of 5 |
| drafts breaking the rest of the grammar kept | 6 of 6 | 0 of 6 |
| drafts in the grammar kept | 4 of 4 | 4 of 4 |
| checker examples fenced in the prompt | no | yes |
| a literal `</examples>` tag from an example reaches the prompt | yes | no |
| provenance line on the Adopt card | no | yes |
| acceptance_met | false | true |

Files: `before.json`/`before.txt` (start commit), `after.json`/`after.txt`, `measurements.json`
(last run). The draft date in the card's provenance line is the day the script runs.
Regression test: `tests/test_qf45_rule_grammar.py` (12 failed, 2 passed on the start commit;
14 passed on the fix).
