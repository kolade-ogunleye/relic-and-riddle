# UI-01b: the dashboard is drawn in place and loses its Needs-you block

Second row of the UI-01 chain ("the chat is always clean"; design `docs/design/telegram-clean-chat-2026-11-11.md`
§6.1.2 and §6.2, mock-ups 1 and 6). Parent: UI-01a.

## What changed

- `src/notice_conditions.py` (new): the panel's condition items, derived from the state the dashboard's "Needs you"
  block read: pending reviews (one item, "Moai closes first, at 16:40", 🎬 Reviews; the alert names the newest
  review), timed-out reviews, ready proposals (one coalesced item), account problems (🔗 Accounts), an unavailable
  provider, photo sets not ready (🖼 Pictures), a re-renderable late failure (Re-render or Make again · Details), a
  post candidate (Post for real), an empty queue (💡 Ideas · 📋 Queue) and the weekly digest (info, Open digest).
  `sync()` adds what holds (a Needs-you one rings), updates a changed one in place (it rings again only when its
  count rose), resolves what cleared, leaves a dismissed info condition dismissed until it changes, and resolves a
  run's `fail:<run>` item once the run is no longer stopped. A condition's page has no Dismiss. The renderer syncs
  every 20 s.
- `src/control.py`: in the clean chat the dashboard keeps the masthead, Production, This month, Last 7 days, the
  heartbeat and the section grid; it drops the Needs-you block, the "Last review" receipt, the post-candidate line,
  the digest line and the Proposals / Post for real / Re-render / digest buttons. `🎬 Reviews (n)` still heads the
  first row while reviews wait.
- `src/telegram_bot.py`: the dashboard is never sent fresh (typed commands, unknown commands, /jobs, issue replies,
  screens from a review card or an older card): it is drawn in place, and only the ordered rebuild sends a copy.
  /start, /dashboard and /clear post the layout again in order (notifications pinned, then the dashboard) and
  delete the old copies. The dashboard returns home after 15 minutes without a tap (30 while typed input or a
  wizard is mid-step). A job whose screen was left while it ran becomes an info item "🧰 <job> done" with its
  screen's buttons. When the pinned panel is missing, it is sent before the dashboard.
- `main.py`: the proposals item is the condition item. `tests/test_ui01a_notifications_panel.py` (branch-only):
  its helper keeps condition items out of its panel, the proposals test uses the real conditions, and the rebuild
  now passes `rebuild=True` (listed in the commit body).

## Round 2 (review 1's blocking items)

- One lock order, written at the top of `src/telegram_layout.py`: `Layout._rebuild_lock` -> the bot's
  `_dashboard_lock` -> `Layout._lock`. Nothing holding `Layout._lock` draws the dashboard (a panel found gone is
  re-posted once it is released); `show_dashboard` and the typed panel make sure the pinned panel exists before they
  take `_dashboard_lock`. `ensure()` and `rebuild()` send only in the renderer (§5.4). Tests: two threads and a fake
  Bot API after a "Clear history" (both finish, one panel sent), show_dashboard never entered under the layout lock
  (tick, a gone panel, a tap), another process's lease (no panel sent).
- The weekly digest arrives silently: `notice_conditions.announce_digest` (the info item, no nudge).
- One failure is one item: the late-failure condition skips a run with a `fail:<run>` item, and a `fail:<run>`
  item posted while the condition shows the run takes that item over (it rings once).

## Round 3 (review 2's blocking item)

- A typed `/photos <topic> upload` (or `/photos upload <topic>`) opens the upload wizard inside the dashboard, as
  its button does (design §6.2, §10.1 "Upload wizard step sent fresh -> D"): no "Preparing image upload…"
  message of its own. The dashboard's screen is the wizard, so it waits 30 minutes before going home.

## Measurement (`measure.py <repo root>`)

Seeded state: two pending reviews, a late failure with its video, queued topics without photo sets.

| | parent (UI-01a) | UI-01b |
|---|---|---|
| "Needs you" lines on the dashboard | 8 | 0 |
| messages sent for six everyday requests | 4 | 0 |
| an older card became the dashboard | true | false |
| condition items in the pinned panel | none | late, photos, reviews |
| digest messages with sound | 1 | 0 |
| messages sent for a typed `/photos <topic> upload` | 1 | 0 |
| lock probe: threads still waiting after a rebuild and a dashboard draw at once | 0 | 0 (2 on round 1's commit) |
