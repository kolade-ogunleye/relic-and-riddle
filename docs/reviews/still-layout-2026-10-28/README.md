# Stills: text that fits its box, measured after drawing, redrawn when the look sees a cut (2026-10-28)

**Found by:** the render audit of 28 Oct (`docs/reviews/render-audit-2026-10-28/README.md`, "Repairing minor
findings"). The still look reported three defects. The operator rejected all three runs and quoted the notes:

- Production Antikythera 1 (`run_antikythera_mechanism_1792933211`), Story frame 2: "text is truncated mid-sentence
  at '35 meshing'".
- Richat 2 (`run_richat_structure_sahara_eye_1793040996`), cover: "'MAURITANIA'S BULLSEYE' has been cut off and
  omitted from the bottom of the frame".
- Richat 2, carousel slide 1: "the header text 'ARCHIVAL RECORD // 01 OF 07' ... collides and overlaps".

`still_vision.findings` gave every finding the severity `info`, and the release gate never acts on `info`. Nothing
redrew the still.

**Causes found while reproducing (the production copy, drawn with the real renderers):**

1. **The Reel cover's tagline was never drawn in two of its four styles.** `lower_third` and `minimal` drew the
   eyebrow and both title lines only. Richat 2 used `lower_third`, so the look was right that "MAURITANIA'S
   BULLSEYE" was missing. Antikythera 1's cover (`minimal`) was missing "35 BRONZE GEARS" in the same way; the look
   did not mention it.
2. **The comparison carousel slide put its REF A badge under the slide's own header.** The badge box
   [60, 35, 415, 73] overlaps the dossier header [77, 68, 407, 91] (Richat slide 4).
3. **`fit_font` returned its floor size even when the text still did not fit.** A long heading ran past the safe
   edge. In the stress set, the debate heading reached x = 1046 (the 3:4 profile-grid crop cuts at 1047), and a
   28-letter cover title at 56 px ran past the canvas edge (x = 1173 on a 1080 canvas).
4. **The Antikythera Story cut was the old `[:3]` line cut.** Production drew that frame before 55d3f03. At HEAD the
   body is already drawn whole, on 5 lines at 40 px. That is still more than the 3-line rule, and `video_qa`
   reports it as `story_text_too_small` (major). Preventing it is the script stage's job (`story_body_lines`).

## What changed

- **`src/typography.py`: `fit_text` and `fit_line`.**
  - They fit text using the font's real metrics (`textbbox`, outline stroke included).
  - The order is: wrap; step the size down by 2 px to a floor; keep whole sentences; then, last of all, shorten at
    a word boundary and end with an ellipsis.
  - The result reports whether the text was shortened.
- **`src/still_layout.py` (new).**
  - `Layout` records every text block and badge a still draws, as the box PIL measured. Each box is checked against
    a zone:
    - Story frames: `safe_zones.STORY`.
    - Covers: `safe_zones.cover_box`, less 60/20 px.
    - Carousel slides: the canvas less 40 px at the sides and 30 px at the top and bottom.
  - `problems()` reports, from the numbers alone, any box past the canvas or its zone, and any two boxes that
    intersect.
  - `drawn()` draws a still, checks it, and draws it once more with the tighter fit if the check fails.
  - `save()` / `check()` / `redraw()` keep each still's record and recipe in `still_layout.json` beside it.
- **`src/multi_format.py`.**
  - Story frames are drawn by `_render_story_frame`. The stack is measured before it is placed. A body over 3 lines
    is first set across the whole safe width, and is never shortened while the box has room for it.
  - Carousel slides are drawn by `_render_carousel_slide`. The header band (dossier and brand) and the swipe-cue
    band are measured and reserved, so badges and text stack between them.
  - Headings take a second line before they shrink past their floor. Bodies are limited to the room left.
  - The tighter fit uses smaller type and puts the header on solid pills, so the header reads over any photo.
  - `redraw_story_frame` and `redraw_carousel_slide` redraw one still in place. The Story redraw also updates
    `layout.json`.
  - The slide-number flag (`_numbered`) is now per thread. The Instagram and Facebook copies are drawn concurrently
    on one generator.
- **`src/visual_engine.py`.**
  - Every cover style draws all four lines.
  - Each style fits them with `_fit_copy`: one title size for both lines, down to 44 px, and only then shortened.
  - The badge and minimal styles keep the whole stack inside the square. Lower-third raises its block when the
    tagline needs the room.
  - `redraw_cover` redraws a cover and cuts the profile-grid copy again.
- **`src/still_vision.py`.**
  - First, the measured check. A defect in a still's record is redrawn once. If it survives the redraw, it is
    **major**. This check runs with `STILL_VISION_CHECK` off too.
  - Text that the fit shortened is **minor**. The Story body is excluded, because `story_text_incomplete` already
    reports it.
  - Then the look. When it reports `text_cut_off` or `layout_overlap`, the still is redrawn once with the tighter
    fit and looked at again:
    - If the defect has gone, the finding stays `info`, marked "redrawn once ... no longer sees it".
    - If it is still seen, or the still has no record to redraw from (a run drawn before this change), or the
      re-check could not run, it is **minor**, so `MINOR_FINDINGS_BLOCK_RELEASE` decides.
  - Every other look finding stays `info`.
  - No change to `release.py` was needed: it already gates by severity (minor follows the setting).

## Measurements (`before/` and `after/measurements.json`, made by `render_evidence.py`)

The same entry points (`generate_story_sequence`, `generate_carousel_sequence`, `generate_thumbnails`) are used for
both sets. Every `ImageDraw.text` and `rounded_rectangle` call is measured by a spy, so before and after are measured
the same way. The cover's blurred halo and drop shadows are excluded. "Before" is HEAD 0c5c32b; "after" is this
change. No model call is made: photo matching is replaced by rotation.

The copy is production's, verbatim, from each run's `verified_script.json`. The cover uses the style that run used.
Photos are the first seven of each topic's `/photos` sheet:

- Antikythera: photo 1, "Mechanism of Antikythera - Kotsanas' version - back side"; photos 2 to 7, "The Antikythera
  Mechanism" (Flickr 3209890679, 3471976134, 3471171927, 3471978200, 3209887483, 3471987204).
- Richat: photo 1, "Richat Structure, Mauritania"; photo 2, "Eye of the Sahara"; photos 3 and 4, the Sentinel-2
  crops; photo 5, "Richat Structure, Mauritania - 6 January 2023"; photos 6 and 7, the ISS views.

The stress set is longer copy than the script stage allows, on the Antikythera photos.

| Still | Before | After |
|---|---|---|
| Antikythera 1 cover (`minimal`) | tagline "35 BRONZE GEARS" not drawn | drawn; all boxes inside the square |
| Richat 2 cover (`lower_third`) | tagline "MAURITANIA'S BULLSEYE" not drawn | drawn at [110, 1426, 484, 1447], under title line 2 (bottom 1377) and inside the square (bottom 1480) |
| Richat carousel slide 4 (comparison) | REF A [60, 35, 415, 73] overlaps the header [77, 68, 407, 91] | REF A at [60, 109, 415, 147], below the header band |
| Antikythera Story frames 1 to 3 | whole (3, 5 and 5 lines; production's older code cut them to 3) | whole, inside the Story box; the tighter fit sets frames 2 and 3 over 930 px (4 and 5 lines) |
| Stress carousel slide 4 (debate) | heading to x = 1046, past the safe edge (1040) | wrapped onto 2 lines, [80, 640, 1000, 778] |
| Stress cover (`lower_third`) | title line 2 to x = 1173 on a 1080 canvas; no tagline | 44 px title, tagline drawn, all inside |
| Stress cover (`minimal`) | no tagline | tagline drawn |
| **All 30 stills** | **6 with a measured defect** | **0** |

`after/redrawn_tight/` holds the tighter fit, as the audit draws it when the look reports a cut-off or an overlap
(`redraw_evidence.py`, from the kept recipes):

- Richat carousel slide 1: header on solid pills.
- Richat cover: smaller type, wider gaps.
- Antikythera Story frames 2 and 3: smaller title; the body over the full safe width.

All four pass the measured check (`measurements.json` there).

Images are half size (540 px wide). File names mirror the run folders: `before/<topic>/story/`,
`before/<topic>/carousel/` and `before/<topic>/cover_<style>.jpg`, and the same under `after/`.

## Tests

`tests/test_still_layout_2026_10_28.py` (7 tests):

- Antikythera's Story text and a very long line stay inside the Story box. Richat's cover draws its tagline in every
  style, inside the square.
- Badges and headers never overlap, in every archetype, in both fits and in both copies.
- The post-draw check catches overlaps and boxes past the zone or canvas. A failing first layout is drawn once more
  with the tighter fit. A tampered record is found again and redrawn by the audit.
- A cut-off the look reports leads to one redraw and one re-check, then minor if it is still seen. If the redraw
  fixes it, it stays info. A still without a record is minor. Story frames and carousel slides go through
  `QualityAuditor` the same way. The measured check runs with the look switched off.

## Notes

- A redraw changes the still's bytes, so the packaging stage cache (`stage_cache/stories`, `carousel`,
  `thumbnails`) will not be reused for that run. A later re-run draws the still normally, and the audit redraws it
  again if it must.
- The tighter fit costs one more light vision call per still that the look flags. The measured check makes no
  model call.
