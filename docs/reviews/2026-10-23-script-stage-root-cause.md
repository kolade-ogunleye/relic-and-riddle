# Script stage (step 1): root-cause review, 2026-10-23

Production ran three Reel dry runs of *The Mystery of Roman Self-Healing Concrete*. All three failed in
step 1, the script and fact-check stage. The longest took 23 minutes before failing.

## Evidence

| Run (UTC) | Release | Outcome | Time in step 1 |
|---|---|---|---|
| 13:00 | rc3 | the free model gave no answer within 300 s; the run failed on that single attempt | 5 min |
| 13:40 | rc5 | 3 drafts, all rejected for length (106 / 100 / 111 words) | 10 min |
| 14:22 | rc6 | 3 drafts, all rejected for length (109 / 73 / 108 words) | 23 min |

- **Shared key:** dev and production use the same free Gemini key (identical key fingerprints). Dev's
  overnight runs used up its daily quota, so production's 07:00 run found every free request refused.
- **Spare key:** the spare free key, wrongly switched on in production, never answered within its 300 s
  limit. It cost 5 minutes on every call.
- **Call times:** a writer call on the billed key took 30-90 s, and each draft was two calls (script,
  then graphics).
- **Measured pace:** 0.50-0.51 s a word for this topic (Pantheon, pozzolanic, Vitruvius).

## Root causes

1. **Length was set in words, but the limit is in seconds.** The prompt's fixed 105-115 words runs
   53-58 s at 0.5 s a word, past the 51.5 s limit. The writer can't hear its own draft. It overshot
   both ways and drifted back to the prompt's number on each full rewrite.
2. **The checks contradicted each other.** The word check (105-115) and the time limit left no valid
   draft (fixed in rc6). The advice "at most 95 words" had no lower bound, so the next draft was 73.
3. **A spare key that never answered was tried on every call**, at 5 minutes each.
4. **Production's free quota is used up by dev**, so every heavy call is refused first.
5. **A rejected draft still paid for graphics planning.**
6. **Nothing showed the drafts:** three drafts all sat under "step 1".

## Fixes

| Cause | Fix | Where |
|---|---|---|
| 1 | Up to 8% over the limit is read faster, confirmed by measuring at that speed (`SPEECH_SPEEDUP_MAX`) | `fit_speed`, `748a28c` |
| 1 | The writer is asked for the word range that fits at the measured pace (85-96 words at 0.5 s a word) | `length_target_note`, `284dcb3` |
| 2 | Word counts are sanity bounds only; the measured time decides; advice is a window ("88-95 words"), and a following short draft is reminded of it | rc6 + `748a28c` |
| 3 | The spare key is switched off in production (the compliance policy); a failed spare is skipped for an hour | `.env`, `0f2d87f` |
| 4 | `FREE_TIER_HEAVY=false` sends production's heavy tasks straight to the capped billed key | config, this commit |
| 5 | A draft too long to keep returns before graphics planning | `284dcb3` |
| 6 | The progress card shows each draft and why it was rejected | `748a28c` |

**Expected step 1 with these fixes:** one or two drafts of about a minute each, plus fact checks. That
is about 3-6 minutes, not 10-23.

**Not done, deliberately:** a narration-only rewrite (instead of regenerating the whole package). With
the speed fit and the measured target, a rewrite should be rare. This is revisited if the audit ledger
shows `script.length` recurring.
