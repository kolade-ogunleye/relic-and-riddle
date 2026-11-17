# UI-01h: hold advice names only actions the surface offers

Source: LAND-33 port review 1 (non-blocking notes 1-4), QF-65e item 1. Spec: ui\UI-01h.proposed.md.

## What was wrong (dbcabf6)

1. A run the release gate failed (auto-approve, the CLI) was told "re-send the review or save any Edit", or "Shorten
   the text (Edit, for a caption), or Regenerate": its item offers Details only, and its Details Make again or the
   queue.
2. A booking held at its slot was told "Edit" and "re-send": it offers Make a new version and Cancel booking.
3. The review panel was told "re-send the review": it offers Edit text, Regenerate and Reject (no Resend).
4. QF-127's voice hold said "Re-render" when its item offered Back to the queue (a run that cannot be resumed).
5. The held booking's item page said "Not posted: <label> … Its Reel was held at its slot because:" twice.
6. Credit titles cut at credits.TITLE_MAX made two photos read alike in one reason ("Interior of Catacombs - Partisan
   Resistance Base…" twice).
7. QF-65e item 1: an AI motion clip of a run with no provenance.json was said to be "in provenance.json".

## The fix

- `src/hold_advice.py`: the one table of hold advice. The producers (credits.py, release.py) write their advice
  from its constants (same words as before: a log, the CLI and a stored report keep them). `for_surface(text,
  surface)` says each advice in the words of the surface it is read on: `review` names Edit text / Regenerate;
  `booked` and `failed` say what is needed with no verb ("A new version is written with them", "The text must be
  shorter for them to fit, or the video use fewer photos that need a credit: both are done in the review of a new
  version"), and their own next-step line names their buttons (BLOCKED_NEXT; the gate item's "Its Details say whether
  and how it can be made again").
- `release.check_problems / release_problems / plain_block_reasons` take `surface`; callers pass theirs: the review
  panel and the earlier review card (review), main._block_reasons (booked), main._blocked_at_gate,
  control._run_findings, the history and dashboard lines, the late-failure item (failed). Details reads stored
  reasons through `for_surface` too, so a reason stored before this commit is said in the surface's words.
- The voice hold names the button its item has (Re-render, or Back to the queue: "the topic is made again from the
  start"). `voice_licence.recovery(resend=False)` for the YouTube Short reply on Posting (no Resend button there).
  A Story post's credit problem no longer says "redraw the Stories" beside a Retry button.
- The blocked booking's notice is `⛔ <b>Not posted</b> · <label>` (label as subject, or the first body line), and
  `notifications._item_body` quotes only the lines its opening does not already show.
- `credits._credit_subjects`: a photo on its topic's /photos sheet is "photo N, “title” by author" (sheet_order,
  photos-by-sheet-number); titles cut alike keep the words that tell them apart ("File:Interior of Catacombs -
  Partisan… (26862878592).jpg"); two photos with the same whole title, author and no number are counted.
- `ai_label._ledger_scene`: "AI motion clip (motion_01.mp4, by the video's manifest; provenance.json: the run has
  none)", or "... has no record of it"; "in provenance.json" only when the ledger holds it.

Button set and layout unchanged; no label added or changed.

## Round 2 (review 1, NOT OK 8/10)

- The gate's picture, sound and colour holds said "... then re-render" on surfaces with no Re-render button
  (picture_qa black, freeze, bars, range, flash, held flash, colour tags; audio_qa dropout; release graphic colour
  vision; the grandfathered colour note). Their remedies are hold_advice constants now, the found facts and the
  diagnosis word for word; per surface: review "... in a new render: Regenerate makes one", booked "... (a new version
  is one)", failed "... in a new render".
- The guard test is case-insensitive and scans every module whose text becomes a check's detail (release, credits,
  picture_qa, audio_qa, video_qa, colour_vision); the one text in them that is not a detail (the answer to "review
  clear-flash" on a master that fails) is named in the test. The nine holds are in HOLDS, each written by its
  producer from what a decode found.
- Review notes: the gate item says "Its Details say whether and how it can be made again"; the review's Story-credit
  advice names Regenerate; the sheet-number test also has a backslash (Windows ledger) path. The ".; " join and the
  900-character cut are unchanged (pre-existing, not in this row).

## Measurements (measure.py, dbcabf6 -> this commit)

before.json / after.json (committed inputs only), real_before.json / real_after.json (with the development output
and the production sample, read only; counts only).

| Measure | dbcabf6 | this commit |
|---|---|---|
| Surfaces (18 hold kinds x 6 surfaces: review Summary/Checks, booked item/Details, failed item/Details) naming a remedy no button there offers (remedies matched whatever their case) | 95 of 108 | 0 of 108 |
| ... of which: booked item, booked Details, failed item, failed Details, review Summary, review Checks | 18, 18, 18, 18, 10, 13 | 0 each |
| Surfaces that lose what was found | 0 | 0 |
| Voice hold whose run cannot be resumed: remedies named that its item lacks | Re-render | none |
| Sentences said twice on the held booking's item page | 2 | 0 |
| Reasons with two credits that read alike, committed development runs (55 reasons, 98 credits named) | 4 | 0 |
| Same, development output (30 runs, 55 reasons) / production sample (34 runs, 62 reasons) | 4 / 3 | 0 / 0 |
| Credits named by their /photos sheet number, development output / production sample | 0 / 0 | 77 of 98 / 90 of 114 |
| Real holds (each kind's longest) drawn on every surface naming a remedy not offered: committed dev runs / dev output / production sample | 17 of 18 / 17 of 18 / 12 of 12 | 0 / 0 / 0 |
| Real holds' item pages with a sentence said twice: dev / prod | 4 / 3 | 0 / 0 |
| Longest real item page (characters), all under the 1,200 quote limit | 1,227 | 1,117 |
| An AI motion clip of a run with no provenance.json said "in provenance.json" | yes | no ("provenance.json: the run has none") |

renders_before.md / renders_after.md: each kind of real hold of the committed development runs (the stored copy
through the gate's credit check) drawn by the real code on every surface (fake Bot API, nothing sent): the booked
item and Details, the failed item and Details, and the review panel for the run whose whole record is committed.
The same for the development output and the production sample was drawn outside the repository (not committed).

## Known limits

- Reasons stored in a run's error or a booking before this commit are rewritten when read (exact canonical phrases).
  A report stored by an older wording (before QF-126b) keeps its words.
- A sheet number is the one at the time of the check; a photo dropped later renumbers the sheet. The title is always
  given with it.
- The "lacks ...; Reel caption" join ("asks for.; Reel") is QF-110's "; " join, unchanged (not in this row's spec).
