# Carousels: no slide chrome, the brand clear of Instagram's counter, no direction cues (2026-10-28)

**Reported by:** the operator, 28 Oct, after the Kailasa carousel was posted:

> Whenever we're using the pointer emoji we need to be sure we are pointing in the right direction... the
> Facebook carousel that we posted... the hand pointer was pointing to the right whereas the caption was up and
> the image gallery was down... likewise Instagram carousel displays up while the caption is down... the design
> we added to the Instagram carousel to make it look like a slide is needless as Instagram has its own look
> making ours look awkward... the RELIC & RIDDLE text also appears in an unsafe UI location showing the N of M of
> the carousel image.

**What we found (production copy, drawn with the real renderers):**

1. **Direction cues in the copy.** The writer was asked for "a short invitation to swipe". Kailasa's carousel
   caption ended in 👉. The same caption went to Instagram's carousel and to Facebook's multi-photo post.
   Facebook shows that post as a grid with the caption above it. The Story drew "FULL STORY IN OUR NEW REEL →"
   and "WEIGH IN BELOW ↓". The closing slide drew "TELL US IN THE COMMENTS ↓". The companion manifest's Facebook
   copy said "in the discussion below".
2. **Slide chrome on the Instagram copy.** Each Instagram slide carried our own furniture:
   - the counter "ARCHIVAL RECORD // NN OF 07"
   - a progress bar and seven dots
   - "SWIPE →" on the cover
   - a slide number on every badge ("ARCHIVAL EVIDENCE // 02")

   Instagram draws its own counter and dots.
3. **The brand under Instagram's counter.** "RELIC & RIDDLE" was drawn at [766, 68, 1002, 93] on every slide of
   both copies. That is under Instagram's "N/M" counter pill, which sits at roughly [930, 39, 1041, 111] on our
   canvas.

## What changed

- **`src/script_engine.py`.**
  - `neutral_cues` removes every direction-dependent cue. That covers:
    - pointing hands and arrow emoji or symbols (with their variation selectors and skin tones), and ASCII
      `->` / `<-` / `=>`
    - "swipe right/left/up/down", which becomes "swipe through"
    - "scroll down", which becomes "scroll"
    - "below" or "above" after a UI noun or a call to act ("in the comments below" becomes "in the comments";
      "drop your guess below" becomes "drop your guess"; "see below" becomes "take a look")

    Facts are left alone: "30 m below the surface", "the carvings above the door", "the images above the altar".
    A removed arrow between two numbers becomes "to" ("1902 → 1910"). An arrow between two sentences becomes a
    full stop.
  - `facebook_photo_caption` applies the same rule and also drops "swipe":
    - "Swipe to see how ..." becomes "See how ..."
    - "Swipe through to trace ..." becomes "Trace ..."
    - "Swipe through the ..." becomes "Explore the ..."
    - anything else becomes "see the photos" or "looking through the photos"
  - `normalize_copy` runs `neutral_cues` on the title, caption, carousel caption, first comment, share line,
    Story slides, carousel slides and thumbnail copy. The narration is never touched.
  - The writer's prompt now asks for "a short invitation to look through the slides ... never say which way".
    It also has a new rule: no direction cues in any text.
- **`src/publisher.py`.** `_caption_for` decides the caption each carousel request carries:
  - Facebook's own request (the `carousel_fb/` copy) gets `facebook_photo_caption`.
  - Every other request gets `neutral_cues`, so an operator's edit is held to the same rule.
  - A dry run now records the caption with each planned request.
- **`src/multi_format.py`.**
  - Both carousel copies lose the dossier counter, the progress bar, the dots, the cover's SWIPE cue and the
    badges' slide numbers. The stat card's "07 DATA" fallback is now the category.
  - The brand moved to the top-left, at (80, 70), at the text margin. See "Placement" below.
  - Every slide's `Layout` declares Instagram's counter region, `_ig_counter`: the right 280 px of the top
    140 px.
  - `_footer_top` is now the zone's bottom less two gaps, at 1288 (it was 1267, above the cues).
  - The cover's long-text limit moved from the SWIPE row (1174) to the footer line.
  - The debate slide's "TELL US IN THE COMMENTS" (now without "↓") and its share line sit on the footer line.
    They were 120 px up, clear of the cues.
  - The stat slide's VERIFIED pill now ends on the footer line.
  - The Facebook copy's slide text goes through `facebook_photo_caption`, so the grid never says "swipe". The
    Instagram copy's text goes through `neutral_cues`.
  - The Story frames now read "FULL STORY IN OUR NEW REEL" and "REPLY WITH A OR B" (the frame asks for replies).
    The companion manifest's Facebook copy says "in the comments".
- **`src/still_layout.py`.** `Layout(protected=[(name, box)])` stores the regions in the record. `problems()`
  reports any element under one as `layout_overlap: ... lies under the platform's instagram_counter`. The saved
  record re-derives this, so `still_vision` redraws or raises it like any other measured overlap.

## Placement

What each platform draws over the media:

- **Instagram (in the feed):**
  - The "N/M" counter pill at the top-right. It sits about 14 pt in from the corner and is about 40 x 26 pt. On a
    390 pt phone that is about 2.8 canvas px per pt, so x 930-1041 and y 39-111.
  - A tagged-people icon at the bottom-left, on tagged photos only. We post none.
  - The dots, below the media and outside it.
- **Facebook:**
  - The photo grid draws "+N" over the centre of its last tile.
  - The single-photo viewer draws nothing over the photo.

The top-left is clear on both, so the brand sits there, small, on the 80 px text margin.

A square grid tile crops 135 px off the top of a 4:5 slide. The brand (y 68-93, 100 with the tight pill) then
drops out whole instead of being half cut.

The protected region is wider than the measured pill. That leaves room for "10/10" and for narrower phones.

## Evidence

`render_evidence.py <code_root> <label> <out_dir> <repo>` draws the production scripts of Kailasa
(`run_kailasa_temple_monolith_1792854124`, the 👉 caption) and Richat 2 (`run_richat_structure_sahara_eye_1793040996`).
It uses one code version, with photos in /photos sheet order and no model calls. A spy on every ImageDraw text,
line and ellipse call measures before and after the same way. "before" is the working tree before this change,
copied to `C:\tmp\rr_carousel_before`. "after" is this tree.

Each of `before/` and `after/` holds:

- `<topic>/carousel/`: the Instagram copy, at half size
- `<topic>/carousel_fb/`: the Facebook copy, at half size
- `<topic>/instagram_overlay/`: the Instagram copy, with a mock "2/7" pill where Instagram draws it and the
  protected region outlined in red
- `<topic>/story/`: the three Story frames
- `measurements.json`: for each slide, what lies under the counter, what chrome and arrows are drawn and where the
  brand is; for each topic, the caption each platform's request carries (after only)

| | before | after |
|---|---|---|
| Slides with text under Instagram's counter (both copies, 28 slides) | 28 ("RELIC & RIDDLE") | 0 |
| Instagram slides with a dossier counter, a progress bar or dots | 14 of 14 | 0 |
| SWIPE cue on the cover | 2 | 0 |
| Arrows drawn (slides and Stories) | 10 | 0 |
| Brand box | [766, 68, 1002, 93], top-right | [78, 68, 314, 93], top-left |

Captions as sent (after):

- Kailasa, Instagram: "Swipe through the engineering genius behind Ellora's largest rock-cut Hindu temple." (the
  👉 is gone)
- Kailasa, Facebook: "Explore the engineering genius behind Ellora's largest rock-cut Hindu temple."
- Richat, Facebook: "Explore the forensic reality behind Africa's massive desert eye. 🔭 ..."

## Tests

`tests/test_carousel_chrome_2026_10_28.py` checks the following:

- A table of 15 drafts with cues, giving the expected Instagram and Facebook wording. No mark or direction phrase
  survives, and Facebook's wording has no "swip".
- Six facts that are left alone.
- `normalize_copy` over every public field.
- The publisher sends each platform its caption, and a dry run records it.
- Neither copy draws chrome, dots or a progress bar, and the Facebook slides never say swipe.
- Every archetype, normal and tight fit, both copies, passes `problems()` with nothing under the counter, and the
  brand is top-left and out whole in a square crop.
- The protected-region check catches the old brand position.
- The text uses the band the cues reserved.
- Story frames have no arrow or "BELOW".

Updated tests:

- `tests/test_carousel_facebook.py`: the Instagram copy now has no chrome either.
- `tests/test_still_layout_2026_10_28.py`: `header_brand` is the one header element, and REF A clears it.

## Not changed

- The combined request that goes to Instagram and Facebook together is used only when no `carousel_fb/` copy
  exists. Every run makes one (main.py). That request carries the `neutral_cues` caption, which may say "swipe
  through".
- The Telegram operator UI uses ← for navigation. It is not public copy.
- The posted Kailasa and Richat posts are not edited.
