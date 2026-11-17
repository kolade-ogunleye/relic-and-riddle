# QF-41: Gemini roles, fenced evidence and an injection corpus

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2 (STOP-SHIP). Finding: the Gemini text path
sent `f"{system}\n\n{prompt}"` as one string (`src/gemini.py`, `research` and `structured`). No quote was escaped
or fenced, so `</evidence_quotes>` inside a quote reached the model verbatim.

## Change

- `src/gemini.py`: every call sends the system text in its own role. On the Interactions API calls
  (`research`, the `structured` text path) it goes in `system_instruction` (a string), and `input` carries
  only the prompt. On the `generateContent` calls (the research grounding fallback and the vision path) it
  goes in `system_instruction.parts`, and `contents` carries one `role: "user"` turn.
- `src/privacy.py`: the two Gemini face checks send their fixed task as `system_instruction`. `contents` then
  holds only the image, in a user turn.
- `src/prompt_fence.py` (new): `new_token()` makes a random fence mark for each call (`F` plus 16 hex
  characters). `escape()` turns `<`/`>` into `&lt;`/`&gt;`. `fence(label, body, mark)` writes
  `<label fence="mark">`, the escaped body, then `</label fence="mark">`. `fence_note(mark)` is the one line
  saying that text inside the fence is data, never instructions. `safe_caption()` removes markup and command
  sentences, cuts the caption to at most 120 characters and returns it as a JSON string with `<`/`>`
  escaped. A command sentence is one that opens with a generic command verb or a role label (`System:`,
  `Assistant:`), or mentions instructions or the prompt. `check_reply()` raises `FenceEchoed` (an `LLMError`)
  when the model's answer repeats the fence mark.
- Evidence fenced and escaped, with the reply checked:
  - `fact_sheet.build`: topic focus, research notes and evidence quotes, plus the paid shore-up retry.
    A reply that echoes the mark raises.
  - `trace_check.evidence_check`: evidence quotes and package. An echo returns `fail`.
  - `script_engine`: the writer's `raw_evidence_quotes`. An echo raises.
- Commons captions:
  - `image_review._verdicts` and the scene planner (`scene_planner._caption_label`) send each archive caption
    as `safe_caption`, inside `<caption fence="mark">`.
  - The scene planner keeps the pipeline's own crowd note outside the fence.
  - An echoing reply fails that attempt: the photo review tries the next model, and the scene planner makes
    its next attempt or uses its caption fallback.
- `tests/fixtures/injection_corpus.json`: 14 payloads (tag closers, forged quotes, role labels, commands,
  HTML comments). `tests/test_qf41_injection_fence.py` runs them through the real prompt builders.

## Metric (`python measure.py <repo root>`)

The real code paths run with every request mocked (no network). The corpus is the committed fixture.

| | before (58ad475) | after |
|---|---|---|
| Gemini calls with `system_instruction` set (of 7) | 1 | 7 |
| Gemini calls with the system text inside `input`/`contents` | 6 | 0 |
| Prompts with a literal tag taken from quote text (14 payloads × evidence check + fact sheet) | 14 | 0 |
| Captions that reach the model with a command sentence (of 14) | 12 | 0 |
| Captions that reach the model with markup (of 14) | 7 | 0 |
| Captions sent as fenced JSON, ≤120 characters (of 14) | 0 | 14 |
| Checker reply that echoes the fence is rejected | no (no fence) | yes (`fail`) |
| Corpus payloads passing every check | 0 / 14 | 14 / 14 |

Files: `before.json`/`before.txt` (start commit) and `after.json`/`after.txt`/`measurements.json` (fix).

## Limits

- `src/image_gen.py` sends only the picture prompt, in a user turn. It has no system text to separate, so it
  is unchanged. `scripts/review_validation_audio.py` is a development script with a fixed prompt, and is also
  unchanged.
- The command-sentence filter is generic. It can drop a caption sentence that opens with one of its verbs used
  as a noun (for example "Return of the ..."). This drops only part of the caption, which is a hint for the
  model and no fact. Words that commonly open titles ("Set of", "List of", "Mark on") are left out of the
  filter.
- Escaping means the model reads `&lt;` where a quote had `<`. Facts cite quotes by number, and the stored
  quotes are unchanged.
