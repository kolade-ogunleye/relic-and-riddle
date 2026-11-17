# UI-01a: one pinned notifications panel that every sender writes into

Row of the Telegram UI track, first of the UI-01 chain ("the chat is always clean", operator spec of 2026-11-11,
design approved 2026-11-12: `docs/design/telegram-clean-chat-2026-11-11.md` §4-§9 and its mock-ups 1-5, 8, 12).
Parent: d31604ad (UI-02).

## What changed

- `src/notifications.py` (new): the panel's items. `post(section, key, title, body, ...)` writes a row and bumps the
  panel's revision; it never sends. Needs you above Info, newest first; the same open key is one item (latest text,
  ×n); replaceable keys (`digest:`, `maintenance:`, `policy:`, `service-state`, `release`) replace the text; a key
  handled before reopens as new. 50 items shown (the oldest info first leaves; Needs you is never dropped), every row
  kept 90 days. Drawing: the header (the pin strip's line, with the verb agreeing), list pages of at most 5 items and
  3,600 characters (HTML counted), the list keyboard of exactly the number row, Dismiss all info (only when there is
  info) and the arrows that apply; the item page (place, heading, the body or its folded quote, time, the sender's
  actions + Dismiss, ≤ 3 a row, ← Back to list); "✅ This has been dealt with." for an item closed while open; the
  empty state with no keyboard; Dismiss all info with ↩️ Undo for 30 s.
- `src/telegram_layout.py` (new): the layout table (`telegram_cards`, topic 0, kind "layout"), the renderer lease
  (90 s, renewed every 30 s), the panel sent silently and pinned (above an existing dashboard, which is sent again
  under it), edited with a 2 s debounce and a 3 s gap, re-posted when it is missing, back to page 1 after 2 minutes
  or when a Needs-you item arrives on a list page; push alerts (one line ≤ 40 with sound, at most one a minute,
  later items coalesced, deleted after `PUSH_ALERT_SECONDS`, the deletion recorded).
- `src/telegram_bot.py`: `notify()` posts an item (switch on) instead of sending; only the screens a command draws
  (studio, unrecorded) still send, for UI-01b to move into the dashboard. Taps `n:` / `np:` / `nall` / `nundo` act
  in place with a toast; an item's action runs as a tap on the dashboard. The pin's service line is deleted. The
  publish receipt is one `📮 Posted` item per episode. `clear_protected` keeps the panel.
- `main.py`: every sender names its section (and a key where it coalesces) per the design's §10.2 table; the night's
  proposals are one item, "🧭 3 improvement proposals" with 🧭 Improvements.
- `src/control.py`, `src/telegram_ui.py`: 📊 Status › 🗒️ Notices (`cnotices`; `/alerts`, `/alerts clear`, Restore);
  `SCREENS` gains `cnote` and `cnotices`, `PANEL_KINDS` names the panel's callbacks.
- `src/db.py`: the 40 h card sweep leaves layout rows alone. `src/config.py`, `.env.example`: `TELEGRAM_CLEAN_CHAT`
  (default false until the whole chain has passed the on-device acceptance; development turns it on),
  `PUSH_ALERT_SECONDS` (8). `tests/conftest.py`: the switch off for the
  existing tests (as LOOP_FOLLOW_OVERLAY), on in `tests/test_ui01a_notifications_panel.py`.

## Round 2 (review 1's blocking items)

- Every Needs-you sender carries the design's §10.2 actions (`actions=`, existing callbacks): the pre-flight check,
  the fact sheet, updates not applied, backups -> Details (📊 Status); run failures, a run stopped at its deadline
  and "Re-render not started" -> key `fail:<run>` with Re-render or Back to the queue · Details
  (`notifications.failure_actions`, the failure card's own choice); a worker that ended or crashed and the start-up
  "Interrupted run(s) cleared" -> one "⚠️ Run interrupted" item per run under `fail:<run>`; a posting error ->
  🗓️ Posting; a missed or held slot, "Not sure the <part> posted", a blocked Stories/Carousel, a YouTube Short
  unconfirmed and "Posted with problems" -> the episode on 🗓️ Posting; "<part> not posted" -> Retry
  (`retrypost:<booking>`); auto-approve refused -> 🎬 Reviews; release gate and publish failed -> Details (the run's
  history); a review that could not be re-sent -> Resend for review; photo sets -> 🖼 Pictures; accounts -> 🔗
  Accounts. An AST test fails any Needs-you sender with no action.
- `notifications.say()` calls a sender with only the keywords it takes, and with the switch off without the item
  fields at all: the three origin/main tests that replaced `_alert` with a list's append pass again unchanged.
- Sound non-blocking items taken: an item action is addressed by a hash of what it runs (an updated item can never
  run another action than the one shown); shutdown draws the panel once more and gives up the lease; the switch
  defaults off.

## Round 3 (review 2's blocking items)

- An item closes only when its action was done (design §6.1; spec: Needs you stays until handled). The bot runs an
  item's action as a tap on the dashboard and reads its outcome: the Re-render branch's own "started", the recorded
  state for Back to the queue (the topic is queued) and Retry (the booking posted), an error for any other action.
  A refused or failed action leaves the item open; its page says "⚠️ Not done: <the handler's own words>" (design
  §10.1: "If not started: the item's body says why") and the toast says it too. The refusal is never drawn over by
  the home screen. A sender writing the item again clears the line. Restore (🗒️ Notices) takes handled items back.
- A tap's acknowledgement (Back to the queue, Resend for review, a reason's words) is the callback's toast when it
  fits, else a line in the dashboard: never a separate message (design §6.1, §9 "No other ring"). A typed reply of
  that kind is a dashboard line. One toast per tap: the action's answers are collected until its outcome is known.
- Action buttons on an item page carry no emoji ("Resend for review", "Undo approval", "It posted"); a button that
  opens a screen keeps its screen's emoji ("🗓️ Posting"), as the design's §6.1 says.

## Round 4 (review 3's blocking item, scoped)

- Nothing outside the panels sends a message: the weekly update report with something to apply (outside production)
  sent its own ringing message through `bot._api("sendMessage")`. It is now a Needs-you item
  (`main.say_update_report`, through `notifications.say`, key `maintenance:apply`) whose action is 🧰 Apply updates;
  next week's report with nothing to apply resolves it and is an info item. With the switch off it is one message
  with Apply, as before (sent by `notify`). A test scans main.py and src/ for any Telegram send method called through
  the Bot API outside the chat writer and the panels' renderers (design §14.2).
- An item's action is judged by its handler's outcome where it gives one (`notifications.Outcome`, a str that says
  `done`): a refused 🧰 Apply updates ("A production run is in progress", or production) keeps its item open with
  "⚠️ Not done: <reason>" and the dashboard is not drawn over; started, the item closes. Apply updates and Re-render
  are judged only that way: a reply that does not say is not done.
- Non-blocking taken: Re-render's outcome is its handler's (`main.rerender_reply`: refused, a run already in
  progress, or started), not its words, so "Notre-Dame de Paris is being re-rendered" is started. A handler that is
  not running ("That button only works while the daemon is running") says not done.

## Measurement (`measure.py <repo root>`)

24 invented notices of the §10.2 kinds plus a publish receipt, through the measured root's bot into a fake Bot API,
then the alerts' lifetime passes. `before.json` = parent, `after.json` = this commit.

| | parent | UI-01a |
|---|---|---|
| messages left in the chat | 24 | 1 (the pinned panel) |
| messages other than the panel | 24 | 0 |
| pinned panels | 0 | 1 |
| alerts that rang | 22 | 3 |
| alert lines over 40 characters | 19 | 0 |
| Needs-you senders without actions | 14 | 0 (29 on round 1's commit) |
| refused item actions that lost their item (3 refused: Re-render, Back to the queue, Retry) | - (no panel) | 0 (3 on round 2's commit) |
| messages sent by acknowledging item taps (Back to the queue, Resend) | - (no panel) | 0 (2 on round 2's commit) |
| Telegram send methods called through the Bot API outside the panels | 1 | 0 (1 on round 3's commit) |
| a refused Apply updates that lost its item | - (no panel) | 0 (1 on round 3's commit) |

## Not in this row (the next rows of the chain, ui\UI-01.plan.md)

The dashboard drawn in place and its Needs-you block moved to condition items (UI-01b), the progress panel's end
(UI-01c), the paged review panel (UI-01d), typed input and deleting the operator's messages (UI-01e), the 30/40 h
ordered re-post of every panel, the gap sweep and the migration (UI-01f).
