# UI-01f: the layout is re-posted in order before 48 h and nothing else stays

Last row of the UI-01 chain ("the chat is always clean"; design `docs/design/telegram-clean-chat-2026-11-11.md`
§5.2, §5.3, §5.5, §6.3 path B, §7.2, §7.3, §7.5, §13; mock-up 13). Parent: UI-01e rebased onto the round-2 chain (fc5b7010 on 8cdf0cdc). Behind `TELEGRAM_CLEAN_CHAT`
(off by default since round 2).

## What changed

- `src/layout_repost.py` (new): the ordered rebuild for every cause. Each panel at or below the slot (notifications,
  dashboard, progress panels oldest first, the review panel) is sent again silently, top to bottom, one send per
  1.1 s; each new copy is recorded in its row's `next` before the next is sent; then one transaction swaps every row
  (and the persisted alias table maps each old copy to its new one), the old copies are deleted in one
  `deleteMessages`, and the gap sweep runs. The notifications copy is pinned again; the review copy is sent from its
  file ids (no upload). An always-on panel that does not exist yet is created in its place.
- Interrupted: the next renderer deletes the copies recorded in `next` and posts the layout again (no duplicate).
- Age rule: at the first idle moment after the oldest copy is `LAYOUT_REPOST_IDLE_HOURS` (30) old (no tap or message
  for `LAYOUT_IDLE_MINUTES` (10), no input waiting, no review page opened in the last 5 minutes), forced at
  `LAYOUT_REPOST_FORCE_HOURS` (40).
- Gap sweep (after each rebuild and every hour): every id between the newest seen and the oldest panel (or the old
  layout's oldest copy after a rebuild) that is not a panel, an alert still due, a tombstone or a message received
  in the last 30 s; at most 1,000 ids, in calls of 100.
- A copy Telegram refuses to delete is edited once to a quiet line with no buttons and recorded as stuck.
- Reconcile at start: finish an interrupted rebuild, check each panel with a no-op keyboard edit, post again from
  the first missing one (or from the top past 40 h), reap, sweep. Migration once: reviews waiting on cards of the
  earlier chat are moved into the review panel, the layout is posted, every known old message is deleted and
  1,000 ids below the new layout are swept; an info item says how to clear what the bot could not.
- Stop and back (§7.3): a clean stop writes the dashboard footer and the `service-state` item and releases the
  lease; the start says "Back online · was down …" (a Needs-you item with 🗓️ Posting after an unplanned outage of
  more than 15 minutes).
- Path B (§6.3): the dashboard leaving a media screen is posted again in order with the panels below it.
- Lock order (round 2's one documented order, `_rebuild_lock` -> `_dashboard_lock` -> `Layout._lock` -> the review
  panel's lock): only the renderer (the lease holder) sends layout messages. The re-post takes the three layout
  locks in that order and holds them until its old copies are deleted, so a dashboard tap or a review page turn
  made meanwhile waits and lands on the new copy; it sends the dashboard's copy itself and never calls
  `show_dashboard`. Nothing starts a re-post while holding `_dashboard_lock` or `Layout._lock`: the first
  dashboard is placed before the dashboard lock is taken, path B drawn under that lock asks for the re-post and it
  runs once the lock is released, and the renderer's tick runs the age rule after releasing `Layout._lock`. Every
  review-panel entry that draws takes `Layout._lock` first (delivery, which may upload, does not).
- The reconcile keeps round 2's reaper (by owner, with its grace) and its one item per failure; the migration's,
  stop's and back's items are said through `notifications.say` (bot.notify) like every sender's.

## Round 2 (review 1's blocking items)

- Only the renderer sends a dashboard (design §5.4). A process that is not the renderer (a run's worker ending its
  run while a picture or music screen is open) records the dashboard it would draw (`dashboard-redraw`) and the
  renderer's next round draws it: in place, or posted again in order from the dashboard down when it leaves a media
  screen. A worker whose dashboard edit finds the message gone does not send one either. In the renderer, a media
  dashboard whose re-post cannot run stays as it is (never a new dashboard at the bottom), and a gone dashboard with
  panels below it is posted again in its place, in order.
- A re-post that fails backs off: the copies it sent are deleted, the old layout stays, and the next automatic try
  waits Telegram's retry_after, else 1, 2, 4, 8, then every 15 minutes (`layout-repost-retry`). It is logged once;
  after 3 failures in a row one Needs-you item "⚠️ Panels not posted again" (📊 Status) says so, and it closes when
  a re-post works. /start and /clear skip the wait, never Telegram's retry_after. A review panel whose pager is
  empty is closed instead of aborting the re-post.
- Non-blocking items taken: a review delivered during a re-post is drawn on the new copy at once ("Review 1 of 2",
  Next ▶); the migration turns the earlier chat's notices that carried buttons and are young enough to be deleted
  into items first, in their senders' own words and silently (a failure or a warning under Needs you, with its
  run's Details or 📊 Status; anything else as info). The hourly sweep keeps a wizard in use (it is the dashboard
  since UI-01b, round 3): a test says so.

## Round 3 (review 2's blocking item)

- The first start's migration (§13) never loses or silences a Needs-you notice of the earlier chat. Every notice
  still in the chat and young enough to be deleted becomes an item before its message is deleted: the persistent
  ones, and now the transient ones whose lifetime has not ended (the earlier chat gave every Needs-you notice
  without buttons a 6 h life: a posting error, Publish failed, a worker that ended, a deadline, backups).
- Needs you or info (`layout_repost.needs_you`): Needs you when the heading carries a mark of `NEEDS_MARKS` (now with
  🛑 ⏱ 🗄 🧾 🔁 📭 🏛 🖼, every Needs-you sender's heading: a test scans main.py and src/ for them); info when its mark is
  an informational sender's (`INFO_MARKS`: ✅ 📮 📊 📦 ⚖ 🔄 🔎 ▶ 🗓 📜 🔑 🧹 ⏸ ⏹ 🎬 🟢 📝 ⚡); any other mark is Needs you
  when the notice was persistent (it carried buttons: it asked for an action) and info when it was transient.
- A Needs-you item keeps the notice's own buttons when they were recorded: this build keeps each notice's buttons by
  its message id while the switch is off (`alert-actions`), so "🛑 Blocked: unlicensed voice" keeps 🧪 Simulate
  publish and 📨 Resend. Otherwise it offers the run's Details or 📊 Status.
- Non-blocking taken: the "Panels not posted again" item says it keeps trying (up to every 15 minutes).
- Known limit: a notice an earlier build sent has no recorded buttons (that build's alerts table keeps only the
  text), so its item offers Details or 📊 Status, one tap from the run's or the posting's actions.

## Measurement (`measure.py <repo root>`)

The layout (notifications, dashboard, a progress panel, the review panel) with three stray messages among it;
the clock moves on 41 hours with the operator idle and the renderer runs its round.

| | parent (UI-01e) | UI-01f |
|---|---|---|
| oldest panel copy (hours) | 41.0 | 0.0 |
| panel copies older than the 40-hour cap | 4 | 0 |
| stray messages left | 3 | 0 |
| layout in order | true | true |
| review video uploads after the first | 0 | 0 |
| dashboards a run's worker sent at the bottom (a picture screen open) | 1 | 0 |
| layout in order after the worker's redraw and the renderer's next round | false | true |
| panel copies sent over ten renderer rounds while a copy is refused | - (no re-post) | 1 (10 on round 1's commit) |
| pins over those ten rounds | - | 1 (10 on round 1's commit) |
| Needs-you notices of the earlier chat deleted without an item (10 kinds, persistent and transient) | - (no migration) | 0 (10 on round 2's commit) |
| those made info | - | 0 (5 on round 2's commit) |
