# QF-99: a text call sends the thinking level its caller asked for

## Defect

decisions/WRITER-TIMEOUT-diagnosis.md, code finding 3. The text path of `gemini.structured` (the Interactions request)
never sent a thinking level; only the vision path did. Every light text task that asks for effort "low" or
"medium" therefore ran at the model's default, which is high. That covers copy edits, shortening, the sound plan,
beat queries, the music mood, trace and evidence checks, and callouts. Those calls were slower and drew more on the
same congested free capacity. The writer asks for "high", which is the default, and is unaffected.

## The class: every path that drops the caller's effort

| Path | Before | After |
|---|---|---|
| gemini.structured, text (Interactions), from free_llm (free, spare and billed keys) | no level | `generation_config.thinking_level` = the effort ("low"/"medium"). A model that answers 400 naming it is asked once more without it, as the vision path already did |
| llm.structured_call when the SDK has no parse (its Gemini fallback) | no level | the call's effort |
| fact_checker, shot_archetypes, topic_proposer: direct Gemini fallbacks of Claude calls that ask "medium"/"low" | no level | the same effort as their Claude path |
| groq.structured (the light fallback, off by default: GROQ_LIGHT_FALLBACK) | no effort | `reasoning_effort` = the effort, resent without it on a 400 that names it |
| "high" or no effort | no level | still none (the default; the writer is unchanged) |

The field names come from Google's Interactions reference ("generation_config: GenerationConfig", "thinking_level:
ThinkingLevel"; the Thinking guide's REST example sends `"generation_config": {"thinking_level": "low"}`;
aistudio.google.com/api-reference/interactions-api, aistudio.google.com/docs/thinking). For Groq the source is
console.groq.com/docs/reasoning (`reasoning_effort`: low, medium or high for openai/gpt-oss-120b).

tests/test_qf99_text_calls_send_thinking.py enforces the class. Every direct `gemini.structured` and
`groq.structured` call in src/ must pass `thinking=` or forward `**kwargs`.

## Measurements

measure.py records the requests from a mocked transport, so no model is called.

| | base 6a7ef303 | this commit |
|---|---|---|
| light requests ("low", "medium") carrying their level, via free_llm and direct | 0 of 2 | 2 of 2 |
| "high" or no effort sends a level | no | no |
| Groq fallback, "low" | none | `reasoning_effort: low` |
| direct Gemini/Groq calls dropping the effort (AST scan of src/) | 5 | 0 |

Before, on real data. The development runs' llm_calls.jsonl (29 runs, real_data.py -> real_data.json) hold the
recorded calls of the tasks that ask a light level on the text path. All of them went out at the default level:

| task | effort asked | ok calls | median s | p90 s | median output tokens |
|---|---|---|---|---|---|
| trace_check | medium | 41 | 28.06 | 60.21 | 2112 |
| evidence_check | medium | 40 | 16.25 | 30.81 | 1054.5 |
| beat_queries | low | 23 | 15.14 | 77.83 | 256 |
| beat_gap_queries | low | 6 | 8.45 | 46.44 | 74.0 |
| music_mood | low | 18 | 6.36 | 8.59 | 11.5 |

That is 128 calls and 4648.9 s in all. Copy edits, shortening and the sound plan are recorded under
script_writing and other labels shared with "high" calls, so they are not separated here.

## Not measured: the after latency and quality

The row asks for latency and quality before and after (edits accepted, fact-check results). The "after" figures need
the same requests sent to the model at the new level. This build is limited to recorded or mocked requests, with no
live provider call, so they are not measured. The call logs keep no request text, so the calls cannot be replayed
as recorded. The proposed measurement, for the operator to approve: run the same light tasks again on the 29
development runs' saved inputs (their scripts, fact sheets and photos) on the free key, at "high" and at the asked
level. That costs no money, only the free tier's quota. Then compare seconds, output tokens, the checkers'
verdicts and the edits accepted. A quality risk to watch is a
trace or evidence check at "medium" that finds fewer problems. The fact gate itself is unchanged: a check that
fails or does not answer still blocks.

## Round 2

Review 1: OK 9/10. One change, at the lander's request. A model that refuses the requested level (Google's table
lists some models with low/high only) is still asked again without the field, and this is now logged as a WARNING
that names the model and the refused level ("... refused thinking_level 'medium' (HTTP 400); sent again without it,
at its default level"), no longer at INFO. The operator's live free-key re-run after landing will show how often
the configured model refuses a level (decisions\PIPE-BATCH-RULINGS-2026-11-16.md).
