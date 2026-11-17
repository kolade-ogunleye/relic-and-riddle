# UI-01g: nothing in the notifications panel outlives its rule

Follow-ups to the clean chat (UI-01a to UI-01f). On the development bot the operator found items in the pinned panel
still dated two days earlier and asked whether they were not supposed to be updated. They were: a condition is
derived again every 20 seconds, but its line showed the time it was first raised. Info items never left by
themselves, and "Back online" stayed until it was dismissed by hand. The UI-01 port review and the QF-73b review
left smaller notes for this row, listed below.

## What changed

1. **A condition says when it was last checked.** A condition item (review waiting, topic queue empty, a provider
   unavailable, the weekly digest ...) shows "still true · checked Sat 14:32": the minute of its last check, with
   the weekday, so a panel nobody could redraw for days cannot pass for today's. When the check did not run (the
   conditions could not be derived, or one is still being counted) it says "not checked since ...", never "still
   true". An item a sender wrote keeps its own time. The conditions are checked every 20 seconds as before; the
   pinned panel is written again for a newer check time alone only once the time it shows is 5 minutes old (at most
   288 edits a day, operator ruling of 2026-11-14), and any other change is drawn at once. The time shown is always
   the minute of a real check, and a check that stops is still said within 2 minutes.
2. **Info leaves after a day.** An info item leaves the panel 24 hours after it was last written. It stays on
   📊 Status › 🗒️ Notices for 90 days as "left the panel by its time", with Restore. An Approved item whose Undo
   still works stays until that Undo ends, and losing the Undo no longer makes the item look new. Needs-you items
   and conditions never leave by time.
3. **"Back online" leaves once it is answered.** Opening 🗓 Posting or 📚 History (a tap or a typed command) closes
   it; the info one also leaves after 12 hours. After an unplanned outage it is a Needs-you item asking for that
   look, and that one is never taken away by time (operator ruling of 2026-11-14). After any start the item either
   says the bot is back (and what to check, while an outage's request is unanswered) or is closed: "Bot stopped"
   never stays in the panel of a running bot. A clean stop does not write over an unanswered request; the item then
   says the bot is stopped and still carries the request, and the next start says "Back online" with it, whether
   it comes 20 seconds or five minutes later. "Bot stopped" itself never leaves by time: only the start takes it
   away, whichever process draws the panel meanwhile.
4. **Numbers.** A page never holds more items than there are numbers (①–⑩), whatever the page size is set to.
5. **The carousel preview is inside the review panel.** 🖼 Preview carousel on Posts draws the sheet (both copies,
   the verdict and every finding) and each slide with its alt text in the review panel's own message, one photo a
   page, drawn as an operator job; Posts and Summary lead back to the video. A screen asked for from the review
   panel when the dashboard cannot be drawn is refused with a line on the panel, never drawn over its video.
6. **Every face fits the caption.** A typed note, a hook or a title far longer than any real one is cut at a word,
   and one last guard holds every face of the review panel within Telegram's 1,024 characters.
7. **A Redraw that expires its booking** takes the hold's item with it (as a carousel's notice already goes when
   it posts or expires); the tap is told why.
8. **A carousel notice whose offer changed is written again.** "Post anyway: it posts at Thursday" is replaced, in
   the same item, once another carousel has taken that slot. A dismissed notice is not brought back, and the
   earlier chat (switch off) is told once, as before.
9. **A "Carousel without a slot" notice is withdrawn when the slot returns.** Once the carousel plan gives the
   waiting carousel a slot again (the one that took it was cancelled, expired or posted elsewhere), its item is
   closed as resolved and the booking forgets the notice, so a later loss raises it again as new.
10. **A held carousel past its window stops offering a Redraw.** At the first pass of the posting loop after the
    window ends, the booking expires (the rule a Redraw tap already applied) and the item, if still open, is
    written again in place: "Carousel not posted ... was not redrawn before its window ended ... This carousel
    will not post", with Details and no Redraw. Inside the window nothing changes; a dismissed item stays dismissed.
11. **Smaller truths.** An info item written again in the instant it was chosen to leave stays. A preview refused
    because the job queue is full says so at once, and one whose drawing was lost with a restart says so at the
    next round, instead of "Drawing both copies ..." for 20 minutes. A tap on a button of an item that left by its
    time is answered with the page's own words (it moved to Notices), not "dealt with".

## Measurements (`measure.py`, simulated clock, fake Bot API)

48 hours of the panel, the renderer's round every ten minutes: two Needs-you items, four info items (one Approved
with an Undo that works for 30 hours), the info "Back online", two conditions that hold throughout, and one more
info item after 30 hours.

| | parent 38f6f5d | this commit |
|---|---|---|
| oldest time a condition item showed, against the clock (a round every ten minutes) | 2880.1 min | 0.7 min |
| condition items saying "still true · checked" at the end | 0 | 2 |
| info items in the panel at the end, written over a day before | 4 | 0 |
| longest an info item without a working Undo stayed | 48.0 h | 23.8 h |
| "Back online" stayed | 48.0 h | 11.8 h |
| Approved item with a 30-hour Undo stayed | 48.0 h | 29.8 h |
| its time moved when the Undo ended | true | false |
| items that left by time, kept on Notices | 0 | 5 |
| Needs-you items, start and end | 3 and 3 | 3 and 3 |
| most items on a page with the page size at 12 (10 numbers) | 12 | 10 |
| pages of 50 items numbered from ① with each button on its own item | 10 of 10 | 10 of 10 |
| Summary caption with a 2700-character note (limit 1024) | 3491 | 997 |
| Summary caption of the development run as it is | 748 | 894 |
| carousel preview asked for from the review panel is drawn in | the dashboard | the review panel |

The stays are read at the last round before the item left, so they are one round short of 24, 12 and 30 hours.

Port (LAND-32, onto QF-126 11b5e81): the two Summary figures are measured on the port. The development run is
voiced by Azure with no declared tier, so QF-127's licence flag and lede now share the Summary caption: on the
port's own parent 11b5e81 the same run measures 894, and 3516 with the note (`parent-11b5e81.json`, over the 1,024
Telegram takes); the port keeps 894 and fits the note's case in 997. Every other figure is the same.

Six hours with one condition in view and nothing changing, the renderer's round every 20 seconds:

| | parent 38f6f5d | this commit |
|---|---|---|
| edits of the pinned panel | 0 | 72 |
| the same rate over a day | 0 | 288 |
| oldest time the condition item showed | 360.0 min | 4.7 min |
| says "still true · checked" at the end | false | true |

The parent never edited because it showed the time the item was raised, which never changes. Round 1 of this row
edited once a minute (`round1.json`, this `measure.py` on 06ab669: 360 edits in the six hours, 1440 a day);
`tests/test_ui01g_panel_followups.py` holds it to 5 to 7 in half an hour.

"Bot stopped" and "Back online", read from the panel as the chat has it (`service_state` in the JSON):

| | parent 38f6f5d | this commit |
|---|---|---|
| outage, clean stop, start 20 seconds later; three days on, the running bot's panel says "Bot stopped" | true | false |
| ... and says "Back online" with the request to check Posting and History | false | true |
| the same with the start 5 minutes later: the item's page still asks for that check | false | true |
| stopped 13 hours, another process draws the panel: it still says "Bot stopped" | true | true |

The last line held on the parent (nothing left by time there); round 1's 12-hour rule broke it, and round 2 holds
it again, with a test.

## Tests

`tests/test_ui01g_panel_followups.py` (the 48-hour clock is `test_over_48_hours_nothing_stays_in_the_panel_past_its_rule`).
Every test of a changed rule fails on the parent by assertion, none by an error. Guards that hold on the parent
as well: `PAGE_ITEMS <= len(CIRCLED)` with the 50-item paging test, the "carredraw" entry on its own, Needs-you and
condition items never leaving by time, the plain stop and start sequences, and the switch-off paths.

## Real-state check (round 2)

The panel was also rendered from a copy of the development database (5 open items: two conditions, a Needs-you
"Back online" after an unplanned outage, two info items two days old) with the Bot API faked: the stored items, the
conditions derived again by `notice_conditions`, the three stop and start sequences over the real "Back online"
item, a Needs-you item for each of its 46 runs, and 48 hours on. 126 panel texts: longest label 16 characters,
longest callback 10 bytes, longest text 718 characters, no duplicate Needs-you item, no info item left past its
rule. The development database has no carousel booking, so items 9 and 10 are shown by the tests' fixtures only.

## Known limits

- With a condition in view the pinned panel is edited at most once every five minutes for the check time alone, so
  the time shown can be up to five minutes behind the last check (it is still a real check's time).
- The held carousel's expiry and the withdrawal of a no-slot notice happen at the posting loop's pass (once a
  minute while the bot runs), not at the instant the window ends or the slot returns.
- A no-slot notice the operator dismissed is not raised again for the same loss; it is raised again, as new, only
  after the slot returned and was lost once more.
- The info limits are fixed in `src/notifications.py` (`INFO_SECONDS`, `BACK_ONLINE_SECONDS`,
  `CHECK_SHOWN_SECONDS`), like the panel's other constants.
