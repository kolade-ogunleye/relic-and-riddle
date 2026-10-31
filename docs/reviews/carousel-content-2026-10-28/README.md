# Carousels: no claim the script did not make, alt text, Facebook's four photos, every credit (2026-10-28)

**Reported by:** the platform audit, 28 Oct.

**What we found (the production scripts, drawn with the real renderer):**

1. **Labels the script never stated.** Each slide's badge and photo labels were fixed strings or keyword matches.
   None was a fact from the script:
   - every comparison slide drew "REF A // SURFACE CONTEXT" and "REF B // SUBTERRANEAN DEPTH". Roman concrete's
     slide 2 put "SUBTERRANEAN DEPTH" on the Pantheon's dome. Nazca's slide 6 put it on a drone-survey slide.
   - every stat slide drew "VERIFIED BY ARCHEOLOGICAL RECORD" (misspelt). Nazca's slide 5 drew it under
     "By one estimate, the etched condor spans 134 meters". That claim is hedged, and nothing on the slide was
     checked against an archaeological record.
   - keyword badges: Roman concrete's marine slide was a "DISCOVERY RECORD" because "seawalls" contains "wall".
     Nazca's surveying slide was a "DISCOVERY RECORD" because it says "found". Other badges were "FIELD
     INVESTIGATION", "ARCHAEOLOGICAL METRIC", "COMPARATIVE EVIDENCE" and "FORENSIC DEBATE".

   The 28 slides of the two topics (both copies) drew 34 such labels (`before/measurements.json`,
   `labels_the_card_does_not_say`).
2. **No alt text.** The carousel request sent only `title` and `description`.
3. **Facebook hid the closing slide.** Facebook got all seven slides. Its grid shows four tiles and puts "+3"
   over the fourth, so the closing share-and-follow slide was never seen in the feed. Upload-Post
   (docs.upload-post.com/api/upload-photo/) also says a Facebook multi-photo post's caption is applied only to
   the first photo.
4. **The two copies could show different photos.** `_match_photos` ran once for each copy, in parallel threads.
5. **Credits.** The carousel caption appended only the first credit line of the Reel caption, and only when the
   Reel caption had one. That line summarises the whole topic pool. It is not a credit for each photo the slides
   show.

## What changed

- **`src/multi_format.py`.**
  - `_get_card_category`: fact slides carry no badge, because a card has only a heading and a body and neither
    says what kind of evidence it is. The closing slide is labelled "YOUR TAKE" only when its card asks a
    question.
  - Each archetype draws its badge only when it has one. Without a badge, the heading moves up into the
    badge's place. The immersive slide's heading stays exactly where it was.
  - The comparison slide no longer draws REF A or REF B. Its photos are matched to the slide's words, not to a
    role, so nothing is said about them.
  - The stat slide no longer draws the VERIFIED pill. The fact review traces the package's claims, not a source
    for each slide, so no slide can say what verified it. The stat line is the card's own figure. A card
    without a figure gets no stat line (it used to show the category label instead). The text panel now runs
    down to the footer line.
  - `_carousel_photos`: the photos are matched once per run and kept in `carousel_photos.json`. Both copies use
    them. A lock makes the second copy wait for the first. A redraw reuses the record while the cards and the
    photo pool are unchanged. A run drawn before the record existed reuses the other copy's recorded photos.
  - New helpers read each slide's record (`still_layout.json`):
    - `slide_photos`: the slide's photo, plus the second photo on a comparison slide.
    - `photo_credit`: the photo's `credits.json` entry. It follows lineage for derived copies.
    - `carousel_credits`: an `asset_attribution` line for each photo that needs credit, once each, in slide
      order. If any shown photo is CC BY-SA, it adds "📷 These slides are licensed CC BY-SA 4.0: ..." first,
      as the video does with its own notice.
    - `slide_alt_text`: the photo's archive description (first sentence, camera file names removed), then
      "Text on the slide:", the heading and the body as written. The closing slide adds its share line and the
      comments call. At most 1,000 characters.
    - `facebook_pick`: see the rule below.
- **`src/publisher.py`.**
  - `UploadPostPublisher.publish_photos` sends a carousel's `instagram_alt_text` and `facebook_alt_text` as JSON
    lists, one entry per photo in order. This is the documented per-photo form. It sends nothing when any slide
    lacks alt text, and never for Stories. A carousel's `first_comment` goes as `<platform>_first_comment` for
    each platform in the request.
  - `MetaPublisher.publish_photos` always gives Facebook its own request. That request uses the
    `carousel_fb/` copy when it exists, limited to four photos by `facebook_pick`. A Facebook-only retry now
    uses that copy too, under the same idempotency key as before. Alt text is built for each request from the
    slides it sends. A dry run records the alt text and the first comment.
  - `_caption_for` leaves credit lines exactly as they are. A photo's own title, quoted in its credit, is never
    rewritten by the direction-cue rules.
- **`main.py`.**
  - `_carousel_post(payload, slides)` returns the caption and the first comment. The caption is the carousel's
    own text, then the credit lines from `carousel_credits`, skipping any the text already has. If the result
    would pass 2,200 characters, the credits go in the first comment.
  - If a slide has no record (it was drawn before records were kept), the Reel caption's summary credit line is
    added, as before.
  - `_carousel_caption` is kept, returning the caption only.
  - `_post_photos`, the retry and the dry run pass the slides and the first comment.

## Facebook's four photos

The four photos are:

- the cover
- the closing slide
- the two strongest content slides, in slide order

Facebook's grid shows four tiles: the first photo large, then three more. With four photos nothing is hidden
behind "+N". The cover comes first because Upload-Post puts the caption on it.

"Strongest" means, chosen one at a time:

1. no layout problem
2. nothing shortened
3. a photo the post does not already show
4. the earliest slide, since the script puts its reveal first

For Roman concrete that is slides 1, 2, 4 and 7. Slide 3 is passed over because slide 2 already shows the
Pantheon photo. For Nazca it is slides 1, 2, 3 and 7.

## Not changed: 1440 x 1800

The slides are still drawn at 1080 x 1350. Moving to 1440 x 1800 is not a clean rescale. The renderers place
about 60 absolute pixel values:

- the 80 px margins
- the header at y=70
- the photo heights 380, 520, 560, 620 and 760
- badge offsets, font sizes and floors
- the gradient spans

`_ig_counter` is measured in 1080-wide canvas pixels. The still_layout zones and several tests assert boxes in
those coordinates.

The change needs:

- one scale factor `S = cw / 1080`, applied to every constant and font size in the seven archetypes, the brand
  band, `_zone`, `_ig_counter` and `_footer_top`
- the tests' box assertions expressed through `S`
- new evidence renders

Upscaling the finished 1080 slides would not add detail.

## Evidence

`render_evidence.py` draws both topics with the real renderer. It makes no model calls. Photos are in /photos
sheet order and are rotated the same way for both versions. `before/` is HEAD (1f01aee) and `after/` is this
change. Every drawn text is recorded.

| | before | after |
|---|---|---|
| labels a card does not say (both topics, both copies, 28 slides) | 34 | 0, plus "YOUR TAKE" on the 4 closing slides, which ask a question |
| VERIFIED / REF A / REF B drawn | 2 / 4 / 4 | 0 |
| alt text sent | none | every carousel photo (e.g. "Photo: Nazca Lines from 5m in height. Text on the slide: Massive Animal Shapes. By one estimate, ...") |
| Facebook photos | 7 | 4: cover, two content slides, closing slide |
| credit lines in the carousel caption | 1 summary line from the Reel caption | Roman concrete 4, Nazca 6: one for each credited photo shown (captions 1,153 and 1,537 characters) |

- `overview_before_top_after_bottom.jpg`: four slides at quarter size (from the half-size renders), before on top and after below:
  - Roman concrete 2, the comparison slide
  - Roman concrete 4, the marine slide
  - Nazca 5, the stat slide
  - Nazca 7, the closing slide
- `before/<topic>/carousel*/` and `after/<topic>/carousel*/`: every slide at half size.
- `*/measurements.json`: what each slide drew that its card does not say. The after version also has every
  slide's alt text, the credit lines, the caption, and Facebook's four photos.

Re-run it with:

    python render_evidence.py <code_root> before|after <this folder> <repo root>

## Tests

`tests/test_carousel_content_2026_10_28.py` covers each finding with the production cards: Roman concrete's
seven, and Nazca's stat and surveying slides.

`tests/test_publisher.py` and `tests/test_still_layout_2026_10_28.py` were updated for the new rule. Facebook
now gets its own request of four photos, and REF A / REF B are gone.
