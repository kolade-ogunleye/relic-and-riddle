# 2026-10-25: audit of the earlier fixes, the day's releases and the day's runs

Evidence: the code at HEAD, the 2026-10-19 assessment, `2026-10-24-challenges-and-fixes.md`, the diff of
rc49-rc51, development's `logs/pipeline.log`, run folders, findings ledger and usage for the day, and
production's log, runs, findings, alerts and operator actions (read-only).

## The 2026-10-19 assessment, re-checked

| Section | Result |
|---|---|
| B (broken) | 9 of 10 fixed. B5 (Veo) was removed, not repaired: Kling is the only motion provider, parallax the fallback. |
| C (false positives) | 7 fixed, 4 mostly, 3 partial. Partial: model-written URLs are gone but any annotation with a URL still counts (C4); the same-key guard refuses the paid fallback and reports under `health --live`, not at load (C10); `docs/completion-audit.json` and `implementation-history.md` still cite deleted `output/validation` evidence (C13). |
| D (enhance) | 17 done, 6 partial. Not done: prompt caching of the fact-sheet prefix (D6); a cover on the Graph fallback (D15). Partial: Etruscan and Marie Jackson strings in prompts, two Derinkuyu diagram titles (D4); motion thresholds uncalibrated, and the "megastructure" loosening is `max(req, 0.22)` against a 0.25 floor, so it never applies (D10). |
| E (purge) | Done, with residue: a `youtube` credit branch in `footage.py`, Pexels/Pixabay in `docs/sources.md`, `scripts/relocate.py` and `migrate_topic_ids.py` kept. |

## The 2026-10-24 document, re-checked

Every "built" claim is in the code, with three corrections: the still-look findings (info severity) were
kept but shown nowhere when the gate passed (fixed today: a "Look at the stills and cover" section on the
card); the "Produce 3 / 5 / 7" buttons were removed on 2026-10-25 at the operator's request (`/produce N`
stays); the YouTube Short thumbnail follow-up was already built (`set_youtube_thumbnail`, at Upload-Post's
`/uploadposts/youtube/thumbnail`). Still open from that document: motion calibration (needs ~5 paid
episodes), a cover title moved automatically, title-over-subject placement on stills, the carousel photo
match, the Fusion spike, and `main.py` at 3,800 lines (deferred by design).

## Where the implementation went past the plan

- The plan asked to rename the no-op "self-healing" honestly. The code now has a real repair pass: a
  picture the look finds wrong is withdrawn, a new one searched for its line, the scene re-planned once.
- The plan asked for a live credential probe. The health check probes Azure, ElevenLabs and both Gemini
  keys, reports a prepaid-depleted key (HTTP 402) on the dashboard and shuts it until a call succeeds.
- Not in the plan at all: the card ledger and self-audit of every card against the run's record; platform
  safe zones measured into every renderer and the video check; a licensed SFX palette; art direction from
  the hero photograph; per-platform retries that cannot repeat themselves; photo tiers with screening of
  every photo before use; the release gate re-run at publish time.

## The day's releases (rc49-rc51), reviewed as a diff

Two confirmed defects, fixed in rc52: a vision re-review dropped photos the operator had kept with
`/keep` and marked them rejected; a Regenerate decided on a re-rendered run started the new version
without the live preflight. Also fixed: Facebook's "locale exists" error was matched by substring (a video
id containing 482 would have triggered the replace path); the progress ticker could overwrite a finished
card; a typed `/requeue` refusal was silent; the repair pass judged a plan the caption-overflow re-plan
had replaced; a dead block in the planner; a recursion path in the one-panel rule (unreachable today).

## The day's runs

Development: no errors; 10 free-tier 429 fallbacks to the billed key ($1.02 for the day); the writer's
word target drifted because every re-check of a draft recorded another pace sample (five copies of one
topic's pace), which is why two drafts came out under 42 s (fixed: one sample per distinct draft); a
major picture finding (a car under "ancient surveyors") sat beside "all checks pass" because the look's
findings never reached the gate (fixed: they gate with the audit, info notes excepted); the year "1404"
was drawn as "1,404" and would have counted up from zero (fixed: a value is written as the writer wrote
it, a year shown whole); a space before a full stop was voiced (fixed: tidied before the checks); the
record said "1 round" for runs whose log showed rejected drafts (fixed: every draft counts). Production:
five Antikythera failures (three voice lengths, one fact check, one too short), each explained and fixed
in rc48-rc50; the operator's `/drop` of Derinkuyu at 16:42 UTC was their own tap; the Regenerate at
19:44 UTC recorded the reason and started nothing (fixed in rc51).

## New in rc52: share and follow

Meta's creator tips asked for a share prompt and a follow prompt in the content. One line per episode
closes the video, both captions and the carousel: `docs/reviews/share-follow-2026-10-25/`.

## Verdict

Not 10/10, and no software is. What stands between this and the ceiling is listed above and in the open
items; the biggest are judgement calls the code cannot make alone (motion thresholds, a title over its
subject) and two integrity gaps (prompt caching for cost; a cover on the Graph fallback). Everything a
run claims on a card is now checked against its record, and every finding has a place to land.
