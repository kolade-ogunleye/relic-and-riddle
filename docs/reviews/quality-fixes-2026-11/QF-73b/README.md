# QF-73b: carousel spacing, one source of truth

Source: QF-73 review 6 (non-blocking 1-4) and the QF-73 port review (stale items, the UI-01a key gap), plus QF-88c.

## What changed

1. **One implementation.** `main._post_carousel_if_due` no longer re-implements the rule. It posts the carousel
   `posting_view.carousel_outlook` names for the slot, the same plan every screen, notice and reply reads. A carousel
   held at its slot (QF-81b/81c) is set aside and the plan is asked again for the next pick. The plan learned the two
   things only the slot loop knew: a carousel whose Reel has not posted yet (late, or held by QF-110) cannot take a
   slot sooner than 48 h from now, and an open slot needs a published run. It also takes the daemon's own run filter
   (`may_post`) and reports its spacing refusals so the slot loop records them on the booking as before.
2. **Spacing re-checked under the lock.** The slot now holds `publication_lock("carousel:<run>")` for drawn and
   undrawn carousels, and checks spacing again after any drawing, just before posting. A sensitive Reel claimed
   meanwhile keeps the carousel waiting (its drawing kept, its reason recorded) and frees the day's slot.
3. **Post anyway replies are true.** Cause: two carousels with priority were ordered newest first, so a later Post
   anyway on a newer carousel took the slot an earlier reply had named and moved the earlier one silently. Now an
   earlier choice goes first (`priority_at`; a choice recorded without a time counts as earliest; a "what if" counts
   as now). `post_anyway_outcome` finds the earlier choice that holds the only slot by asking what would happen if the
   new carousel went first. Redraw records `priority_at` too, and its reply names the plan's slot when there is one.
4. **No silent plain expiry.** A carousel that loses every slot of its window to other carousels (newer ones, as a
   rule) has a wait in the plan. The screens say "waiting: no carousel slot before its deadline", one Needs-you notice
   ("Carousel without a slot", with Post anyway and Let it expire) gives the true reason, and the expiry records it.
   A Post anyway reply for such a carousel does not mention spacing.
5. **Stale items closed.** A carousel's item (`post-failed:<id>`) is closed as resolved when the carousel posts or
   expires (slot loop or watch). Before, it was left open with buttons that no longer applied.
6. **UI-01g / QF-88c.** Outside the panel modules, every Needs-you sender names its item key, and none calls
   `_alert(group=...)` directly. The scan counts were 9 unkeyed senders and 5 raw calls before, 0 and 0 now. New
   keys: `autoapprove-illustration:<run>`, `autoapprove-flash:<run>`, `unconfirmed:<run>`, `unlicensed:<run>`,
   `short:<run>`, `post-held:<booking>` (the blocked Reel replaces its own hold item), `run-skipped`, `queue-empty`.
   With the clean chat off, the messages are sent exactly as before, because `say()` drops the item fields.

## Measurement

`python docs/reviews/quality-fixes-2026-11/QF-73b/measure.py <repo root>` runs 12 three-week scenarios (seeds 0-3;
rolling / every Post anyway tapped / every third Reel held 60 h). Each scenario uses the real
`_post_carousel_if_due` on a fresh synthetic database and a frozen clock, with no network and $0 spend.

| total over 12 scenarios | 981b032 (before.json) | this row (after.json) |
|---|---|---|
| slots where the plan's pick (1 min before, and at the slot) is not what posted | 5 of 180 | 0 of 180 |
| Post anyway replies naming a slot their carousel did not post at | 9 of 32 | 0 of 80 |
| carousels that expired with no notice | 93 of 134 | 0 of 134 |
| the same notice twice for one carousel | 0 | 0 |
| carousels left waiting after every deadline | 0 | 0 |

Posted carousels: 118 before, 118 after. One carousel per three weeks fits the slots, so this row changes who is
told, not how many post. `notices_max` 2 (after) is a carousel whose reason changed: first it had no slot, then a
later sensitive booking held it for spacing. In the clean chat both notices are the one item of its booking's key,
and the second replaces the first. More taps in the after run means more notices offered Post anyway.

The dev database was not replayed. A read-only copy was refused by the session's permissions, so the measurement is
synthetic only.
