# QF-98: free-writer overload handling

On 2026-11-10 the free tier of the writer model answered HTTP 503 to every request, whatever its size, while the billed
key on the same model wrote the same draft in 78 to 115 s (decisions WRITER-TIMEOUT diagnosis). The cause is vendor
capacity. Three things in `src/free_llm.py` made the outage slower and opaque:

1. The hand-over to the billed key counted attempts, not timeouts: one 503 and then one timeout gave up after a single
   timeout and reported "(2 attempts)".
2. Four 503s in a row were waited through (15 + 30 + 60 s of fixed backoff) before the billed key was asked.
3. `llm_calls.jsonl` kept only a call's final outcome, so which request failed how had to be inferred from timing.

## Fix

- The free-key loop counts consecutive capacity failures (503 or timeout; a dropped connection breaks the run) and the
  totals of each. After `CAPACITY_FAILURES_BEFORE_BILLED` (2) in a row, a long task goes to the capped billed key,
  but only when that key can take it: configured, distinct from the free key, cap above zero and this month's spend
  under the cap. With nothing behind the free key (a cap of 0, as in `dev_run.py`, or a spent cap), the free key keeps
  its retries as before.
- `TIMEOUTS_BEFORE_BILLED` now counts real timeouts. Two timeouts still give up on the free key, with or without a
  billed key, as the operator ruled on 2026-10-25.
- An operator-facing call (`interactive`, its own short timeout) still never bills on a timeout.
- Each retry waits its exponential step (15, 30, 60 s) times a random factor in [0.5, 1.5]. Google's troubleshooting
  guide asks for exponential backoff with jitter on 503.
- The reason given for the billed key says what happened: "stayed overloaded (2 attempts): 2 busy (HTTP 503)" or "did
  not answer in time (2 attempts): 1 busy (HTTP 503), 1 timed out".
- Each `llm_calls.jsonl` line of a free-model call has `attempts`: every request in order, with its key (free, spare or
  billed), status (ok, 503, timeout, 429, 402, dropped, empty, error) and seconds, plus `waited` for the backoff slept
  before it. The other fields are unchanged.

No setting, cap or feature flag changed. The free tier stays first. The Claude fallback and the spare key are untouched.

## Measurements

Run `python docs/reviews/quality-fixes-2026-11/QF-98/measure.py <repo root>`. It runs on the base too, prints JSON and
writes only to a temporary folder. The replays go through the real `free_llm.structured` with a scripted
`gemini.structured` and simulated time. They use the probe times from the diagnosis: a 503 in 4.4 s, a 300 s read
timeout and a billed draft in 91 s. The heavy cap is USD 80 with nothing spent.

| 2026-11-10 sequence | base 1a7d2759 | QF-98 |
|---|---|---|
| runs 1 and 2: one 503, then a 300 s timeout | free, free, billed; 410 s; reason "(2 attempts)"; 0 attempts logged | free, free, billed; 408 s; reason "(2 attempts): 1 busy (HTTP 503), 1 timed out"; 3 of 3 attempts logged |
| run 3: 503 on every request | free x4, billed; 214 s; reason "stayed overloaded"; 0 attempts logged | free x2, billed; 120 s; reason "(2 attempts): 2 busy (HTTP 503)"; 3 of 3 attempts logged |
| one 503, a timeout, then an answer, with no billed key (cap 0) | gives up after the single timeout (FreeQuotaUnavailable) | asks the free key a third time, which answers |

Both replays reach the billed key under the cap, and each billed call is recorded against the heavy cap
(`billed_usage_rows` 2). In run 3 the 503s came back slowly (about 80 s each, by the run's timing), so the real saving
there is larger than the 94 s simulated with 4.4 s 503s.

### Is a shorter request timeout warranted? No.

Source: `call_durations.json`, extracted by `extract_durations.py` from the 29 development runs of 2026-10-25 to 10-31
(1,278 Gemini calls). A call with no free-model retry line in `logs/pipeline.log*` during its window is one request,
and 1,170 calls are like that. The table counts the successful single requests that a shorter timeout would have cut:

| timeout | all tasks | script_writing |
|---|---|---|
| 120 s | 15 | 10 |
| 150 s | 7 | 5 |
| 180 s | 3 | 1 (a 212 s draft) |
| 240 s | 1 (a 281 s trace check) | 0 |

The single-request drafts (87) have a median of 30 s, a p95 of 145 s and a maximum of 212 s. A 180 s timeout would
have cut a good 212 s draft and two other answers, and each cut costs a retry or a billed call. The 300 s timeout
stays. Background mode with polling is not built. The new attempt logs will show whether timeouts, rather than 503s,
dominate future outages, which is the condition the diagnosis set for that work.

### Downstream: what moves to the billed key earlier

In the same records, 13 of 1,278 calls had two capacity failures in a row before anything else.
- 8 of them reached the billed key anyway. They now reach it sooner.
- 5 were later answered by the free key: fact_sheet, music_mood, scene_plan and 2 "other" checker calls. Under QF-98
  they would be billed calls when the cap allows, which is about USD 0.01 per heavy call at the measured draft size.
  That is the cost of not waiting out a short blip. The caps still bound it.

Production per-call evidence is not in the read-only sample (llm_calls.jsonl and run.log were excluded). Counting
production failures needs a read-only pull from the VM, which the operator must approve.

## Known limits

- 503 detection is still by message ("overloaded" or "503"). Diagnosis finding 5 is not part of this row.
- `thinking_level` on text calls (diagnosis finding 3) is a separate row.
- Extracting `call_durations.json` matches log lines to calls by time window and model. A concurrent call on the same
  model can mark a neighbour `retried`, and that errs towards fewer single-request calls.
- Evidence renders: not applicable. The row changes no card, image or video.
