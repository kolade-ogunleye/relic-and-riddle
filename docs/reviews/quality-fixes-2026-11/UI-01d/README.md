# UI-01d: the operator's messages leave once read

Fourth row of the UI-01 chain ("the chat is always clean"; operator spec: "No operator commands or messages must
be permanent"; design `docs/design/telegram-clean-chat-2026-11-11.md` §6.6 and §6.7). Parent: UI-01c.

## What changed (`src/telegram_bot.py`)

- `_leave_unanswered`: a message from the operator that the bot has no use for is deleted once read, and the
  dashboard says "🤔 Type a command, or use the buttons. Help lists every command." with ❓ Help (design §6.6, "Not
  armed"). This covers plain text that answers nothing (it was ignored and stayed), a sticker, a voice note and
  any other message no handler takes. While typed input is asked for, a photo or sticker is answered "That was a
  photo; type the text instead."
- A photo with no usable caption, and a photo with an `/ask` caption, are answered in the dashboard as before and
  now leave the chat.
- A message sent while the bot was off is still not replayed, and is now deleted too.
- Unchanged and checked: typed commands and the answer to an armed /ask were already deleted once read; another
  person's message is never deleted.

## Round 2 (review 1's blocking item)

- Uploads leave once read and are answered in the dashboard (`_upload_reply`): a photo captioned for a topic's set
  ("🖼 Image saved ..." or why not), every `/footage` outcome, and a guided upload's photo that could not be read or
  came at another step. The switch off sends as before.
- Non-blocking items taken: a message sent while the bot was off is said in the dashboard ("⌛ 2 messages sent while
  the bot was off were not acted on; send them again."); a sticker or voice note while text is asked for gets
  "That wasn't text; type it instead." (a photo keeps "That was a photo; type the text instead.").
- A smoke test runs the main operator paths with the switch on and the real condition items (notices from main.py,
  a posting error, the publish receipt, a run's progress to a failure, interrupted runs, a photo upload, a clip,
  plain text) and checks that only the pinned panel, the dashboard and alerts waiting for deletion are left.

## Round 3 (review 2's blocking item)

- A typed `/photos <topic> upload` is deleted once read, like every typed command (design §6.7); its
  wizard opens in the dashboard (UI-01b). A topic it does not know is answered there too, and the command still
  leaves.

## Measurement (`measure.py <repo root>`)

Twelve messages from the operator: plain text, a sticker, a voice note, a photo without a caption, a photo with
"/ask", a message sent while the bot was off, a typed /queue, the answer to an armed /ask, and (round 2) a photo
captioned for a topic's set, a /footage clip with no topic and one over 20 MB, and (round 3) a typed `/photos <topic> upload`.

| | parent (UI-01c) | UI-01d |
|---|---|---|
| operator messages left in the chat | 10 | 0 (3 on round 1's commit; 1 on round 2's, the typed upload) |
| answers sent as new messages at the bottom | 1 | 0 |
| the message sent while the bot was off said in the dashboard | false | true |

Not in this row: typed input asked inside the review panel (Edit text, In my words, a note after a reason): that is
the review panel's row.
