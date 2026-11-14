# UI-02: truthful publish receipt; V2 review card cannot be enabled with placeholder values

Telegram UI track, 2026-11-11. Defects from the style audit (items 1 and 2) and the behaviour audit (B1, B2, B3,
B10, the V2 receipt and "Review waiting" truncation). Kept minimal so UI-01 (the clean-chat redesign) can rebuild on it.

`python measure.py <repo root>` prints the numbers below (`before.json`: the base 2debdf8b, `after.json`: this
commit). It builds the V2 card from the committed real development run `tests/fixtures/ui02_review_v2/` (made by
`build_fixture.py` from `output/run_gol_gumbaz_whispering_gallery_1793278143`: its script, release report, voice and
music records, local paths made relative) and renders the receipt for four outcomes with a stub bot. Nothing is
sent to Telegram.

## Publish receipt

| Outcome | Header before | Header after | Runs log line before | after |
|---|---|---|---|---|
| all ok | ✅ Title | ✅ Posted · Title | 📮 Title posted · Reel & Short | 📮 Title posted · Instagram Reel, Facebook Reel and YouTube Short |
| Instagram failed | ✅ Title | ⚠️ Posted with problems · Title | 📮 Title posted · Reel & Short | ⚠️ Title posted with problems · Facebook Reel and YouTube Short; not Instagram Reel |
| all failed | ✅ Title | ❌ Not posted · Title | 📮 Title posted · Reel & Short | ❌ Title not posted |
| Short skipped | ✅ Title | ✅ Posted · Title | 📮 Title posted · Reel & Short | 📮 Title posted · Instagram Reel and Facebook Reel |

- A failure reads `❌ Instagram Reel: <plain reason>` (was `• Instagram Reel: ⚠️ failed (...)`); a skipped Short reads
  `◽ YouTube Short: not attempted (<reason>)` (was left "queued" for good); an unconfirmed one says to check the account.
- main.py marked Instagram and Facebook "live" whatever the result said: the receipt now starts from the result
  (`platform_errors`, a new per-platform reason from `MetaPublisher.publish_reel`), is made only once the Reel
  posted (it was sent "uploading…" before the outcome was known, and stayed so when the Reel failed), and lists the
  Short only when YOUTUBE_SHORTS is on. The follow-up line names the Stories and carousel only when they are booked.
- With the flag off: the "✅ Published" alert becomes "⚠️ Posted with problems" with `❌ <platform>: <reason>` lines
  when a platform was missed; the review card's stamp "✅ Approved · posted" becomes "⚠️ Approved · posted with
  problems" and names a failed Short.
- "Post now?" names only what will be attempted: Facebook and Instagram where a configured publisher covers them,
  "then as a YouTube Short" only with YOUTUBE_SHORTS on and Upload-Post set up (`publisher.attempted_platforms`);
  with no account set up it says so and offers no post button. It promised YouTube whatever the setting.
  It names the Stories and the carousel only when they will post: booked for a booked run, on disk for one Post
  now books after the Reel, and never without Upload-Post (the only publisher of Stories and carousels).

## V2 review card

| | before | after |
|---|---|---|
| placeholder literals on the card built from the real run | 13 (12 values; the cost appears twice) | 0 |
| Length shown | 52.0 s (a default; the run records no `duration`) | 47.7 s (the release gate's measurement) |
| run whose report has no measured length | V2 card sent with the default | live card instead, logged with the reason |

Every value, and where it comes from now:

| Value | Before | After |
|---|---|---|
| Lede count | "1 check failed", "2 notes to check" (fixed) | counted from the release report's blocking or open checks |
| Length | `payload.get("duration", 52.0)` | the gate's measured duration; missing: live card |
| Release gate | "✅ All checks passed" when not blocked | blocked / needs review / all checks pass, from the report |
| Pictures "9 distinct / 10 beats" | invented | removed (no cheap, correct per-run count) |
| Rights "✅ Cleared" | invented | removed (no single rights verdict; the gate row covers it) |
| Cost "~$0.41", "$0.41" | invented | removed (no per-run cost record) |
| Written by "Gemini 2.5 Pro" | invented | `origin_label(script origin)`, as the live card says it |
| Voice "ElevenLabs · Archivist" | invented | voice.json provider and licence ("Azure Speech · licensed") |
| Music "Ancient Winds · licensed" | invented | music_selection.json credit (title, author, licence) |
| Footer "rc70 · made just now", "🧪 Dry run" off production | invented | removed |
| Findings lines (two) | invented | the gate's own findings (as the live card words them) and the run's notes; none: section left out |
| Caption fallback, first comment | stock text | the copy Approve posts (QF-43 card record), saved before the buttons can decide |
| Script | first 4 sentences under a summary implying all | the whole narration up to V2_SCRIPT_ROOM characters, then where the rest is |
| Bold | literal `**` (not rich text) | rich-text bold |

The guard: `_v2_review_plan` raises `V2CardUnavailable` when a value the card needs is missing (title, measured
length, narration, release report, the posted copy, the caption); `send_approval_request` then sends the live
card and logs `V2 review card not used for <run>: <reason>`. Optional rows (voice, music, who wrote it) are left
out when their record is missing, never filled in.

## V2 behaviour (behaviour audit)

- B1 Undo: `db.undo_review_decision` reopens a review only while the decision is still the one undone, taken on
  this card, at most 10 minutes ago, the run still `pending_approval`, nothing booked or posted, and the worker has
  not claimed it (`review-claim:<run>` holds the decision's time, set by `main.undone_before_acting` before acting; the
  worker reads the decision again after claiming, so exactly one of the two wins). A refused Undo says why and
  removes its button. Undo of a published, regenerated or settled run: reopened before, refused now.
- Undo window (review 1): the worker claims a decision tapped on a V2 card only once REVIEW_UNDO_MINUTES (config and
  .env.example, default 10) has passed since it, reading the decision again every 2 s meanwhile, so the Undo works for
  the whole window it offers (it lasted one 5 s poll). On the claim the Undo button is removed. The approval stamp says
  when: "posts after HH:MM unless you undo" (post now), "books its slot after HH:MM unless you undo" (slot mode,
  where the Reel still waits for its slot), "dry run after HH:MM ..."; the rejection stamp "Undo until HH:MM; then
  the topic is set aside". With the window set to 0 no Undo is offered.
- Review 2: an Undo gives the review back the time it had left when it was decided, and at least
  REVIEW_UNDO_MINUTES from now (approvals.expires_at); the wait follows it, so an Undo after the review's own window
  had closed no longer restores an Approve that refuses. Its toast says "The review is open until HH:MM <zone>".
  The slot is picked from the decision's time (main.slot_clock, taken before the run-folder backup), so the Undo wait
  and the backup never push a last-moment approval to the next day. Stamp times carry the time zone; with the window
  at 0 a stale Undo says "Undo is turned off."
- Review 3: a new delivery of the card after an Undo (Resend, Edit text's fresh card, the V2 pair: each calls
  `db.record_approval`, a 24 h placeholder expiry) clears the Undo's mark (`decided_by` "undone"), so the next wait
  narrows the expiry to its own window again and a wait already running keeps only the time the Undo gave back
  and the new card's window. Before: the wait followed the placeholder, up to 24 h with the review-wait and
  production locks held; after: a 120 s test window ends at 120 s (a wait already running: at the Undo's 10 min).
  What an Undo gives back is at most the review window (the coordinator's approval window, else
  APPROVAL_TIMEOUT_SECONDS) and at least the Undo window: a decision before any wait narrowed the placeholder gave
  back about 24 h. REVIEW_UNDO_MINUTES is held between 0 and 20 (the approval window's margin before the slot).
- The Undo wait keeps the review-wait lock: releasing it would let a Resend's wait claim and act on the same
  approval (the lock is what makes "two waits never act on one Approve" hold across processes), so a Resend during
  the window answers that a wait is already running, as during any review wait.
- B2 sendRichMessage: a real Bot API method (added with rich messages, per the captured official documentation of 2026-10-29;
  not re-checked live). On failure the review media is already recorded, so the live card is delivered under the
  video, the video's own buttons are removed and the live card decides; the video is deleted only when no card
  could be sent.
- B3 flag off: Keep it, Undo approval and Undo rejection answer "switched off, nothing changed" and are never a
  decision; the command line accepts only real decisions.
- B10: the expired-card sweep runs (it was given the time as `max_age_hours` and overflowed); an archived card is
  not reaped again every 6 hours.
- Reject reasons: improve.REASONS (8) on the V2 card too, recorded with improve.record_reason; a chip of the
  retired 7-reason set is not a decision. "Keep it" and Undo offer no Approve on a release-blocked version.
- "Review waiting · <run>" is cut at a word with an ellipsis; the V2 stamps are plain text (no "&amp;").
- Sentence case on the touched labels ("🚀 Approve & post", operator ruling of 2026-11-11); no setting names on the
  cards: "not posted: YouTube Shorts are turned off" (was "YOUTUBE_SHORTS is off", shared with the live card) and a
  skipped Short's "no account that can upload Shorts is set up" (its error named the Upload-Post settings).

Regression test: `tests/test_ui02_receipt_and_v2_card.py` (fails on the base: the receipt's new API, the guard
and the V2 fixes do not exist there).
