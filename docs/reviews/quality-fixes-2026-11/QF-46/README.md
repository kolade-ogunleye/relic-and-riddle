# QF-46: Cap and ledger illustration spend

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2 (STOP-SHIP). Start commit `cb4eac1`.

## Metric, before -> after

Measured by `measure.py`, which drives the tree's real illustration path (`image_gen.illustrate_beat`,
`generate_raw_linework`, `generate_image`, `media_budget`, the `media_usage` table, `control.spend_lines`) with a fake
`requests.post` answering HTTP 200, every socket blocked and a scratch database:

| Measure | Before (`cb4eac1`) | After |
|---|---|---|
| Image requests sent with `MEDIA_MONTHLY_BUDGET_USD=0` (two beats x four candidates under the per-run cap of 8, plus one legacy linework call) | 9 | **0** |
| `media_usage` rows after one fake 200 (budget $5) | 0 | **1** (`ai-images`, `gemini-3-pro-image`, $0.14) |
| Dashboard spend lines show it as "AI images" | no | **yes** ("AI images this month: $0.14 (same $X media cap)") |
| Acceptance met | false | **true** |

Re-run: `python docs/reviews/quality-fixes-2026-11/QF-46/measure.py <repo root>` prints the JSON and writes
`measurements.json`. `before.json` is that script run on a worktree of `cb4eac1`, `after.json` on the fix. No input
files are needed: the fake response carries a 64x112 PNG made in memory.

## What changed

- `src/image_gen.py`: `generate_image` (the single place an illustration request is sent; `illustrate_beat` and the
  legacy `generate_raw_linework` both go through it) asks `media_budget.allow(db, IMAGE_GEN_COST_USD)` before
  anything else is requested (no model listing, no image request) and records `media_budget.AI_IMAGES` with the model
  id and cost on every HTTP 200, whatever the image turns out to be. A ledger that cannot be opened means no request
  (fail closed). `db` is an optional argument; the pipeline database is the default.
- `src/media_budget.py`: `AI_IMAGES` provider name; `spent(db, provider=None)`.
- `src/db.py`: `media_spend(month, provider=None)`.
- `src/control.py`: `spend_lines` adds "AI images this month: $x (same $cap media cap)"; the "AI motion" line no
  longer counts illustration spend. `spend_summary` (the dashboard line) carries it as "AI images $x".
- `src/config.py`, `.env.example`: `IMAGE_GEN_COST_USD` (default 0.14, minimum 0.01), marked **[UNVERIFIED]**: Google's
  list price for a `gemini-3-pro-image` 1K/2K output image (about $0.134) plus reference-image input, rounded up; not
  read from the project's billing. `MEDIA_MONTHLY_BUDGET_USD` is documented as covering AI motion and illustrations
  together.

## Operator note

The cap is shared with AI motion and defaults to 0, so an environment whose `MEDIA_MONTHLY_BUDGET_USD` is 0 (or unset)
now makes no illustration request at all: that is the row's acceptance. An environment that wants illustrations sets a
budget above 0; each request is then charged at `IMAGE_GEN_COST_USD` until the price is read from billing.

## Tests

- `tests/test_qf46_illustration_spend.py` (new): zero budget means zero requests over a run; a fake 200 inserts one
  `ai-images` row shown in `spend_lines`/`spend_summary`; a failed request is not ledgered; the cap stops requests and
  is shared with motion. Fails on `cb4eac1` (3 failed, 1 passed), passes on the fix.
- `tests/test_illustration_tier_2026_11_02.py::test_the_key_is_sent_in_the_header_and_gemini_takes_aspect_and_no_negative_prompt`
  and `tests/test_illustration_fidelity_2026_11_02.py::test_the_reference_is_sent_as_an_inline_image_part`
  (branch-only, not on `origin/main`): each now funds the cap ($5) on a scratch database before its mocked request;
  their assertions are unchanged.
