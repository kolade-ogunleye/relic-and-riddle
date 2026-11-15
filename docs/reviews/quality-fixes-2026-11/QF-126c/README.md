# QF-126c: Story credits follow TASL

Decision: the operator, 2026-11-14 ("follow the best practice"; queue row QF-126c). Stacks on QF-126 (credit
records) and QF-126b (credits that fit captions); their credit-completeness rules are unchanged.
Trade-off order: truth and compliance > legibility and gates > the design as it is today > speed.

## The problem
A Story frame goes out with no caption, so its photo is credited on the frame itself. The credit was
`Photo: <author> · <licence> · adapted: cropped, with text added` and the licence's address: no title and no
source. On a BY-SA photo it gave only the frame's own BY-SA 4.0 address, not the address of the photo's licence.
CC BY 2.0 and 3.0 s4(b) ask for the title "if supplied" and the URI the licensor specifies for the work. CC BY 4.0
s3(a)(1) asks for a link to the material "to the extent reasonably practicable". Creative Commons' recommended
attribution is TASL: Title, Author, Source, Licence.

## What changed (src/multi_format.py, src/credits.py, main.py)
1. **The frame's credit is TASL** (`story_credit`):
   `Photo: “<title>” by <author> · <address of the photo's page> · <licence> <licence address> · adapted: cropped, with text added`,
   and on a BY-SA photo `This frame: CC BY-SA 4.0 · creativecommons.org/licenses/by-sa/4.0` as before. The photo's
   own licence address is given once. When it is the frame's BY-SA 4.0 address, the frame's line gives it.
   - Title: the file's own title, whole. It is sanitised as every credit's text is (`sanitise_credit_text`). It is
     not capped with "…" as a caption line is (`credit_title`, QF-42), because a frame never carries a cut credit.
     Commons' `File:` namespace and the file type are left out: they name the page, not the work. A record with no
     title gives no title (TASL: "if supplied").
   - Source: a frame has no link, so the source is the page's address as text, without the scheme. The shortest
     official address is the page by its Commons page id, `commons.wikimedia.org/?curid=<id>` (QF-126b's
     `short_source_url`: MediaWiki's own address on Commons' host, never a third party's shortener). It is used
     when the Commons API gave that id for that very page. Otherwise the page's recorded address is used, with
     percent-escapes written as the letters a browser shows.
2. **Page ids looked up for the Story's photos** (`credits.find_page_ids`, the body of `find_source_links` for any
   records). The pipeline passes the same lookup the credits use (`short_link_lookup`, `CREDIT_SHORT_SOURCE_LINKS`)
   to `generate_story_sequence`. The ids go into the run's `source_links.json`, the same file the caption credits
   read. A failed lookup finds none, and the frames keep the full addresses.
3. **Set whole and legibly, or not used** (`story_credit_fit`, `story_credit_fits`). The credit takes the fewest
   lines of the 912 px text column at any size from 26 px down to the 22 px floor, then the largest size that keeps
   that count. It may take at most `STORY_CREDIT_LINES` = 5 lines (it was 3). A complete credit takes 4 or 5 lines
   on the real pools (below). An address wider than the column is broken only at the floor, and only after a "/" or
   before "_", "-", ".", "?". A photo whose credit still does not fit is not used on a frame. If no photo is left,
   `StoryCreditFitError` (a `LayoutError`) holds the Story alone: "Held, its text would not fit whole". The credit is
   never cut and never set smaller than the floor. The probe records no glyph event. A credit that no bundled face
   can draw is still left to the frame's own QF-94 refusal: the frame is refused with the reason, and the operator
   decides.
4. **The release check reads the new form** (`story_credit_report`). A frame's credit is complete in either source
   form (by id, or by recorded address), so a frame drawn before an id was found is not held. A frame whose credit
   takes more than 5 lines, or which was drawn with another credit, is a problem with the redraw advice
   (`hold_advice.STORY_REDRAW`). The stories' packaging cache now also keys on `src/credits.py`.

Not changed: the credit's place (the foot of the Story box), its face (IBM Plex Mono 500), its colour, its backing
and contrast rule (4.5:1), its 22 px floor, the text block above it, the caption and description credits
(QF-126/126b), carousels.

## Measurements (measure.py; before = e49616fc, after = this commit)
Committed photo pools (`assets/*/credits.json`), every photo whose licence asks for a credit (166):

| | before | after |
|---|---|---|
| frame credits naming title, author, source, licence and its address, and the change | 0 | 166 |
| missing the title / the source / the photo's licence address | 166 / 166 / 16 | 0 / 0 / 0 |
| credits that fit 5 lines at 22 px or more, page id known (worst case, a nine-digit id) | n/a | 165 |
| the same with full addresses only (no id known) | n/a | 103 |
| credits drawn with every character (none cut) | n/a | 166 |
| topics whose Story would be held (no photo free of a credit, none whose credit fits), of 27 | n/a | 0 (with ids), 0 (without) |

Setting with page ids: 4 lines at 26 px 54, at 24 px 33, at 22 px 32; 5 lines at 26 px 28, at 24 px 1,
at 22 px 17; does not fit 1 (that photo is not used on a frame).

Real development runs (`RR_REAL_OUTPUT` = the development output, read only; page ids from
`tests/fixtures/qf126b/dev_page_ids.json`, the Commons API's own values). Every Story frame with a recorded recipe was
drawn again: 7 runs, 14 frames, 12 with a credit (`real_dev_before.json`, `real_dev_after.json`):
complete TASL credits 0 -> 12 of 12; credits that fit 12 of 12; frames with no layout problem 12 -> 12; frame
text above the credit 12 -> 12.

Production sample (counts only, `real_prod_sample_counts.json`; frames drawn again from their recorded recipes,
photos read from the sample's own pools): 19 runs, 38 frames, 34 with a credit. Complete TASL credits 0 -> 34. Fit
with no page id known: 25 of 34 (the photos of the other 9 would no longer be chosen; the Story uses another). Fit
with a page id for every page: 34 of 34. No layout problem: 34 -> 34. Text above the credit: 34 -> 34. Pools: 176
photos ask for a credit; 169 fit with page ids and 110 with full addresses. Topics whose Story would be held: 0 of
16 either way.

## Evidence renders (half-size, real runs, before | after)
`pair_1` Gol Gumbaz hook (the longest title: 4 lines), `pair_2` Gol Gumbaz reveal, `pair_3` Nazca reveal,
`pair_4` Richat hook (bright desert). Each pair is the frame from its recorded recipe: the photo, the copy and the
place in the sequence, as the run drew them. No development run's Story shows a BY-SA photo. The BY-SA case is
covered by the tests (a white photo, the full address and the frame's own licence line:
`test_the_complete_credit_sits_in_the_story_box_under_the_text_on_a_white_photo`) and by the production sample
counts above.

## Downstream effects
- Frames drawn before this change carry the old credit. The release check `story_credits` reports them ("the frame
  credits ..., its photo needs ... (drawn with another credit: redraw the Stories)"), so a run waiting for approval
  with a credited Story frame fails the check until it is drawn again: a new render, or Regenerate. A booked Story
  is not posted, with the same reason. This is deliberate (CC BY 2.0/3.0 ask for the title and the source), and
  the lander should know before deploying it.
- The packaging cache of the stories keys on `src/multi_format.py` and now `src/credits.py`, so a resumed run
  draws its frames again.
- The `companion_posting.json` `story_credits` lines take the new form.

## Round 2 (review 1 NOT OK 7/10; lander's rulings)
1. **A short thin credit line crashed its frame.** At the 22 px floor a line holding only a name ("by Pinakpani")
   of IBM Plex Mono 500 has strokes about 2 px wide, so it has no eroded interior. `plan_text` raised `TypeError` in
   `typography.ring_contrast`, and the Story and the run's packaging failed. `typography` is not changed (the
   lander's ruling: its `TypeError` is the stack's thin-text signal, and row QF-138 replaces that contract). Each
   credit line is now planned as the graphics stack plans a light face too thin to read its own colour
   (`graphics._plan_thin`, through `multi_format.story_credit_plates`): it is measured against its drawn colour, and
   its halo is raised until it reaches the target. Its plates (`_ThinPlate`) also re-measure the delivered JPEG the
   same way. `story_credit_fit` plans the setting it chooses with that same function, so a credit judged to fit is
   one the frame can draw: the probe and the drawing agree.
   - Regression tests: a short thin line at 22 px on white, grey and ink, and the two committed development records
     the review found (kailasa_temple_monolith 03, tunguska_event_1908 15). Both now draw their frames at 4.5:1 or
     more, with no layout problem.
   - Pool-wide test: every credit judged to fit is drawn without error, with and without page ids, in the committed
     pools and, with `RR_PROD_SAMPLE` set, the production sample's pools. It passed on both.
   - Real frames drawn again, with no error: development with ids (12 of 12 fit) and without ids (9 of 12 fit; the
     other 3 are drawn whole and reported); the production sample with and without ids.
2. **A booked Story's failure notice is cut before its reason.** The problem now leads with its cause and what is
   needed: "Story frame 1: drawn with another credit than its photo needs (an older form: the Stories must be drawn
   again); it credits ..., its photo needs ...". The notice quotes the reason whole when it fits an item's folded
   text (`notifications.QUOTE_LIMIT`). Otherwise it ends at a word with "…" and says where the rest is. Details
   keeps the reason whole up to `posting_view.HELD_NOTE`, as LAND-32 did for a held carousel. The notice offers
   Details (where Make a new version and Cancel booking are) instead of Retry, which would fail the same way.
   - On the development Richat runs: the frame with the older credit gives a 454-character reason, quoted whole
     with its advice first; the other three Story runs give a 242-character "no record" reason, also whole.
3. **Notes.** A credit whose record gives no address for the photo's page or its licence is not used on a frame.
   There are 0 such records in either pool. The probe measures the line before the emoji gate (`drawable`), which
   it cannot call without recording an event. A credit's text is sanitised, so this differs only for an emoji in a
   name, and that frame then draws a shorter line.
