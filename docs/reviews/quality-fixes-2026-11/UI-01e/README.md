# UI-01e: one paged review panel

Fifth row of the UI-01 chain ("the chat is always clean"; design `docs/design/telegram-clean-chat-2026-11-11.md`
§6.5, §6.5.1, §6.6 for the inputs the review asks for; mock-ups 9, 10 and 11). Parent: UI-01d of the round-2 chain (8cdf0cdc). Behind the existing
`TELEGRAM_CLEAN_CHAT` switch (off by default since round 2); with it off the earlier review card is sent as before.

## What changed

- `src/review_panel.py` (new): the one review panel. One video message (sent silently, with its cover) carries
  every review waiting, soonest-closing window first; the caption's first line is "🎬 Review k of N"; the pager row
  shows only the arrows that apply (`Next ▶` · `⏭` on review 1 of 3). Paging is `editMessageMedia` from the video's
  file_id: each video is uploaded once, the first time it is shown. A new review only raises the count.
- Tabs, each within a 1,024-character caption (a longer text continues on the tab's own pages): Summary (title,
  verdict lede from the release gate, hook, the slot line from the QF-43 record, length / words / pictures / rights,
  notes to check folded), Script (the whole narration), Posts (what Approve posts, word for word: replaces
  "📋 In full"), Checks (a blocking check first, then open, minor and passed checks) and Pictures (each
  illustration beside its reference as one side-by-side picture, then the Story frames and carousel slides; one
  photo a page, "Pictures 8 of 14").
- Summary keyboard: `Approve & post` (success) or a disabled `Blocked by a check`; `Edit text` · `Regenerate`;
  `🎨 Covers` · `Reject` (danger); `Script` · `Posts`; `Checks` · `Pictures · n`; the pager. No 🏠 (D1).
- Approve & post is one tap: the review leaves the pager; the info item `posted:<run>` "✅ Approved" says when it
  posts and carries `↩️ Undo approval` until 15 minutes before its slot (`REVIEW_APPROVE_UNDO_LEAD_MINUTES`). Within
  UI-02's Undo window the Undo reopens the review (db.undo_review_decision, unchanged rules; the worker still waits
  out the window: `v2_card_decision` accepts the panel); after a booking it cancels the booking (Posting's cancel).
  The publish receipt later replaces the same item ("📮 Posted").
- Reject / Regenerate: the reason step (improve.REASONS, "Other" shown as "In my words", ← Keep it, expires after
  5 min). A reason decides; the face then asks for a note (2 min after Reject, 90 s after Regenerate), with Done and
  ↩️ Undo (Reject). Regenerate's face has its time estimate (the recorded typical run; no cost is shown: none is
  recorded) and `Redo without a note`.
- Typed input inside the panel: Edit text (on Posts, "✅ Got it: ‘…’ · Re-checking the release gate…", then the
  copy delivered again or the reason it was not), In my words, the note. One input at a time (a bare /ask takes it
  and the panel says so; the review taking it tells the dashboard); a /command still runs; a photo is refused in
  the panel; the operator's message is deleted once read.
- Endings: a timeout is the run's own Needs-you item `fail:<run>` "⏰ Review timed out" (Resend for review ·
  Details), resolving itself once resent; the timed-out condition names only older ones. The 30-minute reminder is a
  second push alert on the reviews item. The panel is deleted when the pager empties.
- `notice_conditions`: the reviews item's action is `🎬 Show reviews` (turns the panel to the soonest-closing
  review) while the panel is in the chat. `telegram_ui`: `crev` screen, the panel's callbacks in `PANEL_KINDS`.
- Only the renderer draws: a process that is not the lease holder records the review and the renderer draws it.

## Measurement (`measure.py <repo root>`)

Three copies of the committed development run (one blocked by its release gate) delivered for review, then
approved, rejected with a reason and timed out; on this row every face of every review is opened as well.

| | parent (UI-01d) | UI-01e |
|---|---|---|
| review messages sent on delivery | 19 | 1 |
| file parts uploaded on delivery | 12 | 2 |
| "In full" replies | 3 | 0 |
| review messages left after the decisions | 15 | 0 |
| messages sent while paging and opening tabs | n/a | 0 |
| longest face caption (limit 1,024) | n/a | 998 |

Rebased onto the round-2 chain: before.json is measured on 8cdf0cdc. Its "left after the decisions" is 15, not
round 1's recorded 16; measured again today, round 1's parent a1c0b6c7 also gives 15 (the same 15 messages: eight
album parts, three review cards, three "In full" replies, one topic-origin line), so the 16 does not reproduce and
the difference is not a change in behaviour. after.json is unchanged.

Round-2 rules this row follows: the timeout is the run's own Needs-you item `fail:<run>` ("⏰ Review timed out",
`Resend for review` · `Details`), and "⚠️ Review not delivered" (`undelivered:<run>`, `Resend for review`), both
said through `notifications.say` (bot.notify) with replace; only the renderer (the lease holder) sends, edits or
deletes the panel, another process asks it to draw; the panel's lock is innermost in the one lock order
(`_rebuild_lock` -> `_dashboard_lock` -> `Layout._lock` -> the panel's lock), and Posting's cancel (an Undo of a
booked Approve) and the notifications redraw after a tap (`layout.redraw()`) run after it is released.

## Round 2 (round 3 chain)

- The items this row writes carry their actions without emoji, as the design's §6.1 and mock-up 3 show:
  "Resend for review" (the timed-out and not-delivered items, the timed-out condition), "Undo approval" and "Undo"
  (the decided items).
- The 30-minute reminder rings as "⏰ Review closes 16:40 · <title>" (design §9), not "🔔 Review closes …".
- Undo approval from an item closes the item only when the decision was undone (the review panel closes it); an
  Undo refused (its window has passed) leaves it open with the refusal on its page (UI-01a, round 3).
- The measurement is unchanged.
