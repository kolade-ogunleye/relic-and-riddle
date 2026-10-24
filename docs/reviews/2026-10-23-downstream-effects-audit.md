# Downstream-effects audit, 2026-10-23

A Regenerate tapped after a restart was recorded and nothing happened: no new run, the old card
stayed, the dashboard and Render button never changed. The operator asked for every action in the
repository to be checked for the same class of fault, and for this check to be part of every change
from now on (see "Standing rule" below).

## Method

1. Inventory every action: about 100 Telegram commands and buttons (about 40 change state), the
   scheduled jobs, the command line and the release deployer.
2. Inventory the state each action writes: run statuses, approvals, topic statuses, settings,
   Telegram cards and albums, files.
3. Test four failure shapes against all of them (the regeneration fault had the first three):

| Shape | Question asked of every action |
|---|---|
| A. Effect bound to a live process | Which pending states act only while some process is alive, and what happens after a restart or a release? |
| B. One action, several paths | Do all paths for the same action (card, CLI, Resend, simulated publish, scheduler, bot vs daemon) have the same downstream effects? |
| C. State nothing consumes | For every status or flag set, does something act on it later? |
| D. Screen disagrees with state | Do dashboard, cards, buttons and lists match the database after each action? |

## Faults found and fixed

| # | Shape | Fault | Fix (commit) |
|---|---|---|---|
| 1 | A | Review buttons acted only while a process waited; after any restart (every release) a decision was recorded and nothing acted | Startup resumes every open review's wait without re-sending the card (`f6706d7`) |
| 2 | B, C | Regenerate through Resend only marked the run `regenerate`; no regeneration ran | Every wait acts on every decision; Regenerate starts `run --regenerate-from` in a worker (`f6706d7`) |
| 3 | D | One decision made three panels (full card + "Why?" message + dashboard); the badge showed a numeric user ID | One receipt card with the reason chips, naming the operator; the regenerated review replaces its predecessor's card and albums (`f6706d7`) |
| 4 | C | Reasons "Music / sound", "Voice" and "Other" changed nothing in the regenerated run, while the card promised "with your feedback" | Sound excludes the previous run's tracks; every reason states its actual effect (`c8a1d8d`) |
| 5 | A | Details replied after a regeneration began were read once at its start and never used, though the reply said "passed to the new version" | Feedback is re-read where each step uses it; the reply says only steps not yet run use it (`c8a1d8d`) |
| 6 | B | A timeout failed the topic on the run's own path and left it in production on the Resend path; the run's path also alerted a second time after the wait's own notice | One `_settle_decision` for every path; no second alert (`c8a1d8d`) |
| 7 | A, C | A decision made from the command line with no wait running was never acted on | A 2-minute sweep resumes decided reviews nothing waits on, never while a production worker (which may be the waiter) is alive, never twice (`c8a1d8d`) |
| 8 | D | `/clear` (and the one-panel logic) kept an open review's card but deleted its albums, the media Approve publishes | One protected list: card and albums of every open review, plus the live progress card (`df4d3e6`) |
| 9 | B | Bot mode only logged a crashed worker; its run stayed "producing" with a live progress card until a restart (the daemon already cleared it) | Bot mode clears it and says so (`df4d3e6`) |
| 10 | D | Re-sent and edited reviews stacked cards and albums | Earlier delivery removed once the new card is in the chat (`1df9d11`) |

Requested at the same time: after an installed or rolled-back release, the daemon clears the chat
with the same routine as `/clear` and opens one dashboard panel led by the release notice
(`df4d3e6`). A release refused while production runs is a notice only; nothing is cleared.

## Checked and found consistent

- Background jobs: a job counts as running only in its own process; after a restart it shows as
  interrupted and Topic ideas offers Research again.
- Cancel run applies to producing runs only; a review is ended with Reject.
- Topic drop applies to queued topics only; requeue refuses a topic in production.
- Approve: booking a slot stamps the card "Approved · scheduled"; publishing (immediate, simulated or
  by the scheduled ticker) goes through one publish function that stamps the receipt; paused holds
  scheduled posts in both bot and daemon.
- Edit: only pending reviews; the whole public copy is re-checked; credits and hashtags kept.
- Interrupted runs: reconciled at startup in both modes and after a daemon or bot worker crash.

## Closed after the audit

- `/ask` and `/issue` without text waited in memory for the next message; the wait is now kept in
  the database with a wall-clock deadline and survives a restart.
- A regeneration started by a resumed wait counted as the first, so the limit of 2 did not hold
  across restarts; each run now records its place in the chain and the limit is enforced.
- Jobs interrupted by a release restart are named in the release notice.

## Remaining risk (recorded, not changed)

- After a review timeout the topic is marked failed and may be produced again by a later run while
  the kept render still waits for Resend (a selection-policy decision for the operator).
- A reason card is not protected by `/clear`; after a clear, details are given by Regenerating again
  or through `/issue`.

## Standing rule (from this audit)

Every new feature or change lists the downstream effects of each action it adds or alters and checks
them against shapes A to D above before it is committed: what state it writes, who consumes that
state, what every other path for the same action does, what survives a restart or a release, and
what the operator sees afterwards. Tests cover the downstream effect, not only the action.
