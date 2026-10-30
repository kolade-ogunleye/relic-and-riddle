# Story frames credit their photos (2026-10-29)

Development only. Two Story frames drawn by the real renderer (`MultiFormatGenerator._render_story_frame`) from
development photos, before (HEAD `d333bc6`) and after this change, by `render_evidence.py`. The copy on the frames
is evidence copy written for this review, not a run's script. Images are half size (540x960).

| Frame | Photo | Licence | Before | After |
|---|---|---|---|---|
| A | `assets/derinkuyu_underground_city/18_commons.jpg` | CC BY-SA 3.0 | `before_A_derinkuyu_cc_by_sa.jpg` | `after_A_derinkuyu_cc_by_sa.jpg` |
| B | `assets/antikythera_mechanism/15_commons.jpg` | CC0 | `before_B_antikythera_cc0.jpg` | `after_B_antikythera_cc0.jpg` |

## The defect

Stories are posted one frame per request with no caption (`src/publisher.py` `publish_photos`: "Stories take
none"), and the frames drew no credit. A CC BY or BY-SA photo on a Story went out with no attribution anywhere.
CC licences ask for attribution "in any reasonable manner based on the medium"; on a Story that means the frame.

Separately, `credits.asset_attribution` printed the artist field whole. Derinkuyu photo 18's field is 778
characters, nine sentences starting "This Photo was taken by Wolfgang Moroder. Feel free to use my photos...".

## What the frame now says

Frame A (BY-SA), at the foot of the Story box, 26 px Montserrat, light grey with a dark stroke:

    Photo: Wolfgang Moroder · CC BY-SA 3.0
    adapted: cropped, with text added
    This frame: CC BY-SA 4.0 · creativecommons.org/licenses/by-sa/4.0

- It names the author (taken from the free-text field by `credits.author_name`), the licence, and the change made
  (the carousel's wording: "adapted: cropped, with text added").
- A BY-SA photo makes the frame an adaptation under BY-SA 4.0, which the frame states with that licence's address,
  as the video (`SHARE_ALIKE_NOTICE`) and the slides (`CAROUSEL_SHARE_ALIKE`) do. For a CC BY photo the second
  line is the photo's licence address instead (`creativecommons.org/licenses/by/2.0`).
- Frame B (CC0) draws nothing new. Its pixels are identical to a frame drawn with no credits entry (a test checks
  this). Our own graphics and maps have no credits entry, so they get no credit. Their map data is Natural Earth,
  which is public domain.

## Measurements (`after_measurements.json`)

| | A before | A after | B before | B after |
|---|---|---|---|---|
| Credit drawn | none | 3 lines, 26 px | none | none (CC0) |
| Credit box (x0, y0, x1, y1) | | 89, 1134, 990, 1234 | | |
| Story safe box | 65, 269, 1015, 1248 | same | same | same |
| Poll sticker room (y) | 1300 to 1560 | credit ends at 1234, above it | | |
| Credit contrast (90th-percentile background) | | 6.13:1 (no shading needed) | | |
| Layout problems (`still_layout`) | none | none | none | none |
| Body block (y) | 1012 to 1155 | 924 to 1067 (raised to clear the credit) | 1039 to 1136 | unchanged |

- The credit sits inside the Story safe box, which already excludes Meta's 14% top bar and 35% bottom reply band.
  It also stays out of the poll sticker room. It is recorded as the `photo_credit` element in `still_layout.json`,
  so the post-draw check asserts it overlaps nothing and stays in its zone.
- Contrast is measured on the pixels under the line, against their bright end (90th percentile), before any text
  is drawn. When it is under 4.5:1, a feathered ink backing (`ensure_legible`) darkens only as far as needed. On a
  white test photo the frame measured under 4.5:1 unshaded and 4.5:1 or better after shading
  (`tests/test_story_credits_2026_10_29.py`).
- A long name is set smaller, down to 22 px, so the credit never takes more than three lines. A name with a
  character the font cannot draw is recorded `complete: false`, so the release report can stop it.

## Records for the release report

- `stories/layout.json` now carries a `photo_credit` entry for each frame. It holds what was required, the lines
  drawn, their size, box and contrast, and whether the credit is complete.
- `companion_posting.json` now carries `story_credits`, the lines each frame credits.
- `multi_format.story_credit_report(frames)` returns `{"credited": [...], "problems": [...]}`. It recomputes what
  each frame's photo needs from its credits entry and compares that with what the frame recorded. A frame drawn
  before this change has no `photo_credit` record, which counts as a problem.

## Reproduce

    git archive d333bc6 src | tar -x -C C:/tmp/storycred/before
    set RELIC_APP_ROOT=<repo>
    python docs/reviews/story-credits-2026-10-29/render_evidence.py C:/tmp/storycred/before before docs/reviews/story-credits-2026-10-29 <repo>
    python docs/reviews/story-credits-2026-10-29/render_evidence.py <repo> after docs/reviews/story-credits-2026-10-29 <repo>
