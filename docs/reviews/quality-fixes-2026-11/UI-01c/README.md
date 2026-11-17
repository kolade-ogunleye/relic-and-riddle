# UI-01c: the progress panel lives exactly as long as its run

Third row of the UI-01 chain ("the chat is always clean"; design `docs/design/telegram-clean-chat-2026-11-11.md`
§6.4 and §7.4, mock-ups 7 and 8). Parent: UI-01b.

## What changed (`src/telegram_bot.py`, `src/telegram_layout.py`, `src/telegram_ui.py`)

- The panel's keyboard is `Cancel run` alone: no Refresh (it redraws itself every minute), no 🏠 (the dashboard is
  just above: deviation D1). The progress bar line is dropped; the stages say it.
- `Cancel run` asks on the keyboard only: `← Keep making` · `Cancel run` (danger), with the toast "Cancel this run?
  It stops at the next step; what was made and paid for is kept, and the topic goes back in the queue." The
  question stands 5 minutes (`STOP_CONFIRM_SECONDS`); then the panel shows `Cancel run` again. Confirmed: the
  cancel is requested and the keyboard is emptied. Callbacks `pstop`, `pkeep`, `pstopok` are in `PANEL_KINDS`.
- The panel is a layout row (`progress:<run>`) and is never reused from an older card.
- The end of the run: success deletes it; a failure deletes it and posts the Needs-you item `❌ <heading> · <title>`
  (Re-render with what it keeps, or Back to the queue when nothing is worth keeping, · Details), which rings once; a
  cancel deletes it and posts the info item `⏹️ Render cancelled · <title>`. No failure card or cancel receipt is
  drawn any more, and the separate failure alert is not sent (the ending was said).
- The reaper (the renderer, every 60 s): a progress panel whose run is no longer being made but whose finisher
  never ran (its process was killed) is deleted; a stopped run gets the Needs-you item `⚠️ Run interrupted`.
- A Re-render that does not start says why in the dashboard, not in a message of its own.

## Round 2 (review 1's blocking items)

- One failure is one Needs-you item, with the real condition items, whichever the renderer sees first (the
  condition skips a run with a `fail:<run>` item; a `fail:<run>` item takes over an open late-failure condition).
- The reaper decides by the owner process: the progress row keeps the pid and creation identity of the process
  making the run. A dead owner (the CLI killed, the run still "producing") is reaped within the reaper's 60 s round:
  the run is marked interrupted with its topic back in the queue, the panel deleted, and "⚠️ Run interrupted"
  posted under `fail:<run>` (Re-render or Back to the queue · Details), the same item as the worker-crash notice and
  the start-up clearing (UI-01a). A run that has just ended is left to its finisher for 30 s; a cancelled run is
  never "Run interrupted". `/progress` keeps the layout row on the panel's new copy.

## Measurement (`measure.py <repo root>`)

Four runs: one succeeds, one fails, one is cancelled, one's process is killed (round 2: its run stays "producing"
and its recorded owner is a process that has ended). Separately, one run that fails with nothing else going on,
synced with the real condition items before and after its finisher.

| | parent (UI-01b) | UI-01c |
|---|---|---|
| progress messages left in the chat | 3 | 0 |
| failure or cancel cards drawn | 2 | 0 |
| items in the pinned panel | none | Render cancelled (info), the failure and Run interrupted (Needs you) |
| Refresh or 🏠 on the panel | yes | no |
| the killed run's status | producing | failed (interrupted) |
| Needs-you items for one failure (real conditions) | 1 | 1 (2 on round 1's commit) |

Not in this row: the full redesign of the panel's body (stage durations "— 4 min", "Started 14:02 · about 12 min
left · review ~14:28", "Episode 2 of 3", the regeneration line). The body keeps today's stage lines and times.
