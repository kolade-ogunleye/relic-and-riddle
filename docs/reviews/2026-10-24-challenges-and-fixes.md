# 2026-10-24: what was holding the project back, and what changed

Evidence: production's log and database (since 21 Oct), development's database, stage timings of the
8 complete development runs, the findings ledger, spend, and the backlog in
`deployment-reconciliation.md`.

## Findings

| # | Challenge | Evidence |
|---|---|---|
| 1 | The script writer wasted rounds: it drifted past the word window and past the facts it was given; each miss cost a 1–3 min round. | Script stage median 6 min (max 14) of a 20-min run. Ledger: `script.facts` 63 (dev) / 23 (prod); `script.length` 10 across 4 prod runs. 4 of 6 failed prod runs: "3 attempts, each failed its checks". |
| 2 | Production rarely reached review. | 18 run starts, 8 failures, 3 review cards; 1 of 7 recorded runs reached a decision. |
| 3 | The nightly improvement cycle was dead on production. | `Access is denied: …\evals\prompts` (the app tree is read-only to `rr-pipeline$`); a log line only. |
| 4 | Every Claude call failed for a day, silently falling back to billed Gemini. | `You will regain access on 2026-11-01` (23 Oct). |
| 5 | Rules contradicted rules: the planner's lock put a graphic named in the hook on beat 2; the audit flagged that as 2.4 s late. Any *minor* finding decertifies the master. | Kailasa: two renders, same finding. |
| 6 | Cards were not checked against the record. | Approve offered on a release-blocked version; "(by -)"; a reason cut mid-word; step labels a step behind; "~79 min" for a 20-min run. |
| 7 | Log noise hid real errors. | Telegram timeouts as tracebacks (18 prod, hundreds dev); "using the billed key" ×66; "message to edit not found" ×28 (a deliberate failing edit on every Refresh). |
| 8 | Source photos are small. | Kailasa: 8 of 16 below video resolution, upscaled. |
| 9 | Hygiene: no `.gitattributes` (CRLF/LF churn), `main.py` 3,000+ lines. | rc18's diff bloat. |

Costs were not a problem (prod $7.73 this month; dev $8.16). Runway was fine (28 queued topics,
17 photo pools ready, 10 fact sheets).

## What changed (rc20 onwards)

Script stage, same gates (#1, #2):
- A draft whose only fault is its spoken length is edited to the word window with verified facts,
  then given its graphics, instead of a full rewrite (`ScriptEngine._fit_length_by_editing`).
- A draft the first checker rejects is also shown to the second checker; one revision fixes both.
- A free second check that is unavailable is tried again twice, a minute apart, before the run is lost.
- `script.rounds` is a ledger finding when a script took more than two drafts.

Continuous audit (#3, #6, #7):
- Every card a run sends is kept with the run (`cards.jsonl`) and checked at run end against the
  release report and timings (`src/card_ledger.py`): Approve on a blocked version, placeholder
  names, text cut with room to spare, the first estimate against the real length, a stale step.
  Findings file under `ui.*` with source `self_audit`; recurring ones become proposals.
- The improvement cycle writes under `data/`; its failure is a Telegram card.
- Transport failures on the long poll are one line with a growing wait; Refresh Progress redraws
  the card instead of editing the deleted one; the billed-key notice is said once per reason.
- The dashboard has a health line (runs, reached review, failed, median length, self-audit count)
  and says when a provider is shut by a limit or cap and until when (`src/provider_status.py`).

Rules that agree (#5):
- The audit accepts a graphic named in the hook on the next cut; a placement-only finding is
  repaired by moving the graphic to its words without a second planning call
  (`scene_planner.repair_placement`).
- Open decision: whether a minor finding should block release or be shown and repaired.

Re-rendering (rc19): a run that failed after its video was made is re-rendered from Telegram, reusing
its paid voice and accepted motion clips; a re-rendered run waits for a new decision.

## Later the same day (rc21-rc26)

Followed three Kailasa runs end to end with the per-run evidence (`run.log`, `llm_calls.jsonl`,
`cards.jsonl`). Found and fixed: Re-render and regenerations paying again for the voice, the Kling
clip and the music bed; a pictures regeneration rewriting the approved script; a regeneration
starting the next queued topic; a resumed run skipping its self-audit; an approved run leaving its
topic "rejected"; a checker finding worded "the video shows X" never reaching the repair pass; a
Pattadakal photo penalised for being 430 km from Ellora; the scene plan spending 20,000 output tokens
thinking; the deployer re-testing a waiting release every cycle and a 45-minute pre-run margin; no
warning before an install's quiet period; a "Failed" label for a rejected run, mid-word cuts, a Run ID
auto-linked as a phone number, a stale "Installing" notice and an 89-minute median. Added "Post for
real": a finished dry run posted one video at a time while the daemon stays in dry-run. The test
suite runs on 4 workers (85 s).

## Where each remaining item stands

| Item | State |
|---|---|
| Minor findings: block or show-and-repair | A setting: `MINOR_FINDINGS_BLOCK_RELEASE` (default true, unchanged behaviour; false shows minor-only findings on the card instead of blocking). The operator picks. |
| Picture candidates and sharpness | Done: three candidates per beat; ≥1080-px files rank higher; upscaling and privacy checks were already cached across runs. |
| Writer-model trial | Run in development on five topics (script stage only, nothing rendered), cost per model from the usage ledger. Gemini (the production engine): passed 4/5 in 1-3 rounds (2.4 average) and 2.8-6.8 min a topic at $0.02-0.11 on the billed fallback ($0 on the free quota), each pass with the Gemini re-check; Göbekli Tepe was rejected three rounds running, each revision fixing the last finding and reaching for a new unsupported framing (a number not in the sheet, "thousands of years before pottery", "one of humanity's oldest monumental complexes"), which is the gate doing its work on a writer that over-reaches on that topic. Opus 5.5: passed the trace check on all five topics in 1-3 rounds (2.2 average), $0.26-0.53 of writer+trace a topic, 1.7-3.5 min without the web second opinion; its one failure was that opinion disputing a Damascus-steel claim the sheet supports. Sonnet 5.5: never passed the trace check on the Amber Room (overstated the sheet's facts three rounds running), passed the other four in 1-3 rounds at $0.20-0.34 a topic. The web-searching Fable second opinion cost $4.2-10.4 a topic (two requests a round; the 180-300k-token search request was unlogged until rc27) and is now a Gemini evidence re-check whenever a verified sheet exists; the last six Claude rows ran with the second check off (credit depleted) and measure the writer only. Reading: keep Gemini as the production writer; if a Claude writer is wanted, Opus, not Sonnet, at about $0.45 a topic with the Gemini re-check. Prose quality was not scored here; the 2026-10-12 eval had Opus over Sonnet 6/6. |
| Gemini billed key: prepaid credit depleted | Found 2026-10-24 16:33 UTC in development: HTTP 402 "prepayment credits are depleted" from the billed key, surfacing as `AttributeError` because the error body is a list. The app now reads such bodies, shuts the billed key with a dashboard notice until a billed call goes through, and treats it as a wait rather than a failed run. The top-up itself is the operator's, in Google AI Studio; production's key (same project?) is next in line, with `FREE_TIER_HEAVY=false` sending every heavy task to it. |
| Deployer: once-per-release test, 4-worker suite | In the repository (rc23, rc26). Reaches production only through the elevated install, run by the operator on the VM with the pipeline stopped through its stop file; the exact sequence is now in `docs/deployment.md`, "Updating the deployer or launcher". Until it runs, production's deployer is the rc11 copy and every release waits for the full single-worker suite. |
| Split `main.py`'s daemon wiring | Deferred by design: a refactor of working code with no defect behind it; done piecemeal as that code is touched. |
