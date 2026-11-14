# QF-65a: AI media force the AI label on every platform path

QF-65 split for the first release (decisions `FIRST-RELEASE-scope.md`, priority 2: "QF-65 forced AI label"). The
spec line was "`is_ai_generated` never forced by an illustration (`src/publisher.py:662`)". This row does only that
part, widened to every AI picture or clip and every platform path. Not in this row (the rest of QF-65): the full
illustration provenance record (prompt, raw sha256, candidate index, judge verdicts) and the Story/carousel
illustration filter.

## Before

- The Reel, the Short and the documentary carried one flag, `config.META_IS_AI_GENERATED`. It is a setting, on by
  default, and it is there for the synthetic narration. No picture could change it. With the setting off, a Reel
  showing an AI illustration or an AI motion clip was posted unlabelled on Instagram, Facebook and YouTube. The review
  card's "AI motion ... the Reel carries the AI label" note only held while the setting stayed on.
- The direct Graph fallbacks (`InstagramGraphPublisher`, `FacebookPageReelsPublisher`) sent no AI flag at all.
- Stories and carousels (`publish_photos`) sent no AI flag at all, whatever their stills showed.
- The card's "AI transparency" line was printed for every run (`is_generated_by_ai` is set to True for all runs),
  whatever was actually sent.

## The fix

`src/ai_label.py` derives the label from the records of the files the post publishes:

- `trace(file)` follows the lineage records (`src/lineage.py`) down to the sources. A file is:
  - **ai**: a `gap_gen_*` file (the image_gen tier), a record with `operation: ai_motion` or `settings.ai_generated`,
    or `inputs.origin: ai_illustration`, at any depth. A parallax clip of an illustration is therefore AI too.
  - **real**: a credited photo (its folder's `credits.json`), archive footage with its own record, our own procedural
    graphic, or a crop, blur or upscale of one of these.
  - **unknown**: a missing file, an unreadable record, a record that names no source, a clip with no record (a manual
    `hook_motion.mp4`), or a picture with neither credit nor lineage.
- `assess_video(master, payload, cover)` covers the Reel, the Short and the documentary. It reads the master's
  scenes (`*.composition.json`), the picture check's illustrated beats (`picture_fit.generated`) and the cover (its
  `still_layout.json` hero). The label is on when the setting is on, when any item is AI, or when any item is unknown
  (fails closed). A missing or unreadable manifest is unknown.
- `assess_photos(stills)` covers a Story frame or a carousel: the photos each still's record says it shows. The
  narration setting does not apply here, since a photo post has no narration.

Where it is applied:

- `MetaPublisher.build_envelope`: `metadata.is_ai_generated` comes from `assess_video`. It stays on if the review card
  showed it on, even if a record changed since. The envelope keeps the assessment under `ai_label`. Upload-Post's
  `is_ai_generated` and `facebook_is_ai_generated` (Reel) and `containsSyntheticMedia` (Short and documentary) all
  read that field, as before.
- `InstagramGraphPublisher` sends `is_ai_generated` on the Reel container, and `FacebookPageReelsPublisher` sends it
  on the finish call.
- `MetaPublisher.publish_photos`: a request whose stills are AI or unknown carries `is_ai_generated=true`, which is
  Instagram's label and a documented no-op on Facebook. Facebook has no AI-label field for photo posts or Stories, so a
  request whose stills show AI media is not sent to Facebook. The result names it in `held_platforms`, and the
  posting alert says "posted (with the AI label; not posted to Facebook ...)". A request whose stills only lack a
  record (runs made before still records) still goes to Facebook as before; see the limits below.
- Review card (`publishable.card_record`, with the master and cover from `send_review_card`): the record keeps
  `ai_label` and `ai_label_photos`. The card's line now reads, for example, "🤖 AI transparency: AI label on
  (Instagram, Facebook, YouTube): AI motion clip (motion_01.mp4, kling)". With the setting off and no AI media it
  reads "no AI label: no AI picture or clip in this video, and the AI-content setting is off". When the run's Stories
  or carousel carry AI media, it adds "Stories/carousel: AI label on Instagram; not posted to Facebook ...". A card
  without the record keeps the old narration line, as origin/main's `test_control` expects.
- The card lays the line out under the title, in its own room.
  - Brief form first: "AI label on: <first reason>; more in the reply".
  - The whole line once everything else is placed, if room is left. Otherwise the reply under the card carries it
    whole (`needs_publish_reply`).
  - The line does not change what the slot line and the "Also posted" list or count show; the reference is the card
    without the record, with its old narration line (`TelegramApprovalBot._ai_layout`):
    - When the QF-42 fit leaves the line room with those lines unchanged, the fit is untouched.
    - Otherwise the fit gives up the line's room (`card_fit_limit`), exactly that and no more: the narration,
      captions and findings lose the line's own room (`test_what_else_is_posted_costs_the_card_nothing` pins it).
    - If a fit step frees more than that, the publish lines keep the room they had.
  - What QF-42 keeps comes first (round 2): the narration whole, every caption named (its text or its "see Edit"
    line), the credits' own lines and the caption previews. When giving up the line's room would cost one of them,
    the fit takes the room still left under the page cap, with the publish lines unchanged. When no fit does, the
    card keeps QF-42's fit, and the slot line or the "Also posted" count that no longer fits goes to the reply
    under the card, which has Approve's lines whole. A caption that keeps only its "see Edit" line (still named)
    is the line's accepted cost. `test_the_ai_line_never_costs_a_caption_or_the_whole_narration` covers it.
  - So the "Also posted" count shows exactly where it showed before, e.g. on the 900-word card of
    `test_what_else_is_posted_costs_the_card_nothing`.
  - The first reason on the brief line is the decisive one: AI media, then the setting, then what cannot be traced
    (round 1 named a missing cover record on 28 of 35 real cards whose label the setting decided). The whole line
    and the record keep that order.

## Platform documentation (checked 2026-11-11)

- Upload-Post, upload video (https://docs.upload-post.com/api/upload-video/):
  - `is_ai_generated` is Instagram's "AI info" self-disclosure. It cannot be added or removed after publishing.
  - `facebook_is_ai_generated` is the "AI-generated label on Reels".
  - `containsSyntheticMedia` is YouTube's "realistic altered or synthetic (AI-generated) content" declaration.
- Upload-Post, upload photo (https://docs.upload-post.com/api/upload-photo/): `is_ai_generated` is documented for
  Instagram, and on a carousel it covers the whole post. No Facebook AI field is listed.
- Upload-Post, AI content labelling guide (https://docs.upload-post.com/guides/ai-content-labeling):
  - Instagram covers Reels, photos and carousels.
  - Facebook takes the flag on Reels only. For other Page posts it is "a documented no-op"; Meta labels those from
    C2PA/IPTC metadata or an in-app disclosure.
- Meta, Instagram Platform changelog, 22 June 2026
  (https://developers.facebook.com/documentation/instagram-platform/changelog): the Content Publishing API takes
  `is_ai_generated` at publish time. For carousels it goes on the carousel container only.
- Meta, Facebook Reels publishing (https://developers.facebook.com/docs/video-api/guides/reels-publishing): the
  `upload_phase=finish` call takes `is_ai_generated` ("created or edited using AI", default false).
- YouTube Help, altered or synthetic content (https://support.google.com/youtube/answer/14328491):
  - Disclosure is required for realistic content.
  - Unrealistic or animated content, and minor edits (colour, upscaling, effects), are not required.
  - "Disclosing AI content alone won't limit a video's audience or impact its eligibility to earn money."
- Meta Help, AI info labels (https://www.meta.com/help/artificial-intelligence/1783222608822690/):
  - A label is required for photorealistic video or realistic audio, including a realistic AI voiceover.
  - Images are not covered by that requirement.

Reading of the rules: the platforms require disclosure for realistic AI media and allow it for anything else. YouTube
states that disclosure costs no reach or monetisation. The operator's rule, label any AI picture or clip, is therefore
always allowed. Over-labelling a non-realistic illustration has no compliance risk, so no operator question was needed.

The one gap no API closes is Facebook photo posts and Stories. Not sending AI media there is the only way to keep
"never unlabelled", and it is the fail-closed choice. Today it affects no real run (below). The QF-65 filter will keep
illustrations out of Stories and carousels altogether.

Not labelled, by design: the parallax camera move (a depth-estimated push on a real photo) and the face blur and
upscale copies. These are camera effects and minor edits on real photos ("minor edits" in YouTube's list). They are
not image_gen or motion output.

## Measurements (`measure.py`, the narration setting OFF so only the media decide)

Fixture (the measured code's own builders, requests recorded locally). Base 5c7840e9 -> this commit:

| Case | Instagram Reel | Facebook Reel | YouTube Short | YouTube documentary |
|---|---|---|---|---|
| real photos, graphic, credited cover | off -> off | off -> off | off -> off | off -> off |
| AI motion clip | off -> on | off -> on | off -> on | off -> on |
| illustration in a scene | off -> on | off -> on | off -> on | off -> on |
| illustrated beat in the picture check | off -> on | off -> on | off -> on | off -> on |
| hook clip with no record | off -> on | off -> on | off -> on | off -> on |
| no media record (fail closed) | off -> on | off -> on | off -> on | off -> on |

- Story frame drawn from an illustration: Instagram flagged no -> yes; sent to Facebook unlabelled yes -> no.
- Carousel slide drawn from an illustration: Instagram flagged no -> yes; sent to Facebook unlabelled yes -> no.
- Carousel of a credited photo: sent to Facebook and Instagram, unflagged, before and after.
- Direct Graph fallbacks: Instagram container flag no -> yes; Facebook finish flag no -> yes.
- Review card says the label is on, and why: no -> yes. `acceptance_met`: false -> true.

Real runs (counts only; dev output read in place; the production sample from a copy outside the repository):

| Count (narration setting off) | dev output | production-sample copy |
|---|---|---|
| runs with a master manifest | 29 | 33 |
| runs whose video uses AI media (all AI motion clips; 0 illustrations, 0 hook clips) | 4 | 16 |
| AI-media runs labelled, base -> this commit | 0 -> 4 | 0 -> 16 |
| runs labelled, base -> this commit | 0 -> 26 | 0 -> 33 |
| of which for AI media recognised from its records | 4 | 0 |
| of which only for media that cannot be traced | 22 | 33 |
| photo posts (Story set, carousel, Facebook carousel) checked | 81 | 99 |
| photo posts showing AI media | 0 | 0 |
| photo posts labelled on Instagram, base -> this commit (all untraceable) | 0 -> 60 | 0 -> 99 |
| photo posts with a still that has no record | 60 | 42 |
| photo posts kept off Facebook | 0 | 0 |

What the counts mean:

- With the setting on, which is the default and is on in the shipped `.env.example`, every Reel was labelled before and
  is now. Nothing changes for those runs. The change matters when the setting is off, and for every Story and carousel.
- Dev runs with all their files have no false positive. The 3 of 29 runs with intact scene clips and a recorded cover
  stay unlabelled with the setting off. The 22 untraceable dev runs are older runs: their parallax clips were deleted
  (7 runs), or their cover predates still records (22).
- The production-sample copy cannot trace anything, by construction. The pull excluded the `parallax/` and `motion/`
  folders, and the stills' records name the VM's own paths, which do not exist here. So the "AI media" count there
  comes from the manifests' file names (16 runs open on an AI motion clip). Those 16 are labelled either way; on the
  VM their lineage records exist and are read as AI motion. On the copy, 42 of its 99 photo posts have a still
  without a record. Those 42 cannot be traced on the VM either, so they are labelled there too.
- The production metrics hold 16 Story and 8 carousel publications, sent with no AI flag. None of them came from a
  run whose stills show AI media (0 illustrations in the sample).

Review cards: 35 real dev cards rendered by the measured code (`--cards`, setting on as by default), base -> this
commit. Every number below is in `measurements.json`.

- AI line on the card: 29 -> 35. Every line now says whether the label is on, and why. Its first reason is the
  decisive one: on the 28 cards whose label the setting decides it reads "AI label on: synthetic narration
  (setting); more in the reply" (round 1 named the missing cover record there).
- Narration shortened ("[shortened:" note): 5 -> 5. Narration start shown: 31 -> 31.
- Captions named (their text or the "see Edit" line): 60 -> 60. Caption previews: 57 -> 57. Captions moved to
  "see Edit": 3 -> 3, on 2 -> 2 cards. List items left to the reply: 37 -> 37.
- "Also posted" count line: 5 -> 5. What the cards show of the slot line and the "Also posted" section is unchanged
  on all 35 cards (`cards_publish_lines_changed` 0).
- Longest card: 4,000 -> 4,000 characters, so none exceeds Telegram's 4,096.

What the line costs, card by card against the base (`measure.py <base> --cards <dev output>` saved to a file, then
`measure.py <this commit> --cards <dev output> --parent-cards <that file>`; `cards_against_parent`). The brief AI line
costs the narration and the captions its own room on a full card, and nothing else:

- The folded copy section (narration, first comment, captions, hashtags) is shorter on 22 of 35 cards, longer on 3:
  445 characters in all, median 22.5 where shorter, worst 133. The body without the AI and slot lines is unchanged
  on 10 cards.
- The narration itself is shorter on 3 of 35 cards (220 characters in all, median 44, worst 133); the other 32 show
  the same narration.
- No card names fewer captions, shows fewer caption previews, moves another caption to "see Edit", shortens a
  narration it showed whole, or leaves more list items to the reply.
- Round 1 (40e6e735), measured the same way (`_round1_40e6e735_cards_against_parent`): copy section shorter on 23
  cards, 1,154 characters, median 46, worst 403; one card lost a caption's "see Edit" line altogether and one a
  caption preview. Round 2 halves the cost (the decisive, shorter first reason) and keeps every caption.

The decided card (round 3). On a decision the card is rebuilt with its badge (`format_card_text(...,
reserve=<badge>) + badge`). It has no slot line or "Also posted" count that could give way, and QF-43 keeps it in
CARD_PAGE_LIMIT less the badge (`tests/test_qf43_publishable_card.py`, base 5c7840e9: `_tg_len(decided) <=
CARD_PAGE_LIMIT - 60`). So where the brief AI line's room would cost what QF-42 keeps, the decided card is QF-42's
card without the line, exactly as the card without the record lays it out. The same applies where QF-42's fit cannot
shrink into the page (its floor). The review's suggested cap (PAGE_ABOVE less the badge) was measured and not used: it
makes that QF-43 assertion fail (3,890 and 3,875 > 3,820), and changing it would loosen a base expected value. This is
the review's own alternative. Only the operator's Telegram text changes. The label on every post is decided from the
record and the media at publish time, never from the card's text
(`test_a_decided_card_without_the_line_changes_no_post_s_ai_label`).

`measure.py <root> --cards <dev output> --sweep`; each render is compared with the same render without the AI label.
The badges are Approved and Rejected, with a 3- and a 64-character name (Telegram's longest first name). The review's
reserves 44, 75 and 140 are badges of that length. Round 2 (6afba7e9) -> this commit (`card_sweep`,
`_round2_6afba7e9_card_sweep`):

| 860 fixture shapes | captions lost | narration newly cut | previews lost | publish lines moved | decided cards with no AI line |
|---|---|---|---|---|---|
| waiting (reserve 0) | 0 -> 0 | 0 -> 0 | 56 -> 56 | 40 -> 40 | 0 -> 0 |
| Approved, name 3 | 30 -> 0 | 31 -> 0 | 47 -> 0 | 0 -> 0 | 0 -> 113 |
| Approved, name 64 | 27 -> 0 | 20 -> 0 | 44 -> 0 | 1 -> 0 | 0 -> 106 |
| Rejected, name 3 | 29 -> 0 | 20 -> 0 | 41 -> 0 | 1 -> 0 | 0 -> 108 |
| Rejected, name 64 | 32 -> 0 | 20 -> 0 | 49 -> 0 | 1 -> 0 | 0 -> 99 |
| reserve 44 | 24 -> 0 | 20 -> 0 | 46 -> 0 | 4 -> 0 | 0 -> 114 |
| reserve 75 | 33 -> 0 | 15 -> 0 | 53 -> 0 | 0 -> 0 | 0 -> 106 |
| reserve 140 | 28 -> 0 | 20 -> 0 | 40 -> 0 | 1 -> 0 | 0 -> 104 |

- The shapes are findings 0/2/5/9/14 x caption and carousel caption x3/10/18/30 x narration 120-1,100 words in steps
  of 23.
- Every decided render stays in CARD_PAGE_LIMIT with its badge (0 over, longest 3,880) and under PAGE_ABOVE.
- The waiting card is unchanged: every fixture count and every real-card count (`cards`) is the same as round 2.
- Real decided cards (`cards.folder_1.decided`): 35 dev cards x 2 badges x 2 names = 140 renders. Captions lost
  0 -> 0, narration newly cut 0 -> 0, previews lost 1 -> 0, publish lines moved 0 -> 0. Renders with no AI line
  0 -> 11. Over PAGE_ABOVE 2 -> 0: these are the Approved badge with a 64-character name on 2 cards whose fit cannot
  shrink into the page (3,882 + 128 and 3,887 + 128 in round 2). Over CARD_PAGE_LIMIT 8 -> 2: those same 2, which is
  QF-42's own floor (the card without any record is 3,880 and 3,885 there). Longest decided render 4,015 -> 3,914.
- What the operator sees on a decided card without the line: QF-42's card without any AI line, plus the badge. The
  narration's old line is dropped too, because the record replaces it, so on those 11 renders the decided card makes
  no AI statement where the base (5c7840e9) showed "Synthetic media label embedded for Meta CMP compliance"
  (corrected in QF-65b, which puts the line back on 3 of the 11 where it fits). The waiting card that was tapped
  showed the line. Where it showed only the brief line, the reply under it
  has the whole line. The posts carry the label regardless.

## Tests

`tests/test_qf65a_ai_label.py`. It fails on the base and passes on this commit. Round 3 adds three tests for the
decided card. All 8 cases fail on round 2 (6afba7e9):
- `test_the_decided_card_never_gives_a_caption_or_the_narration_to_the_ai_line`, with the Approved and Rejected badges;
- `test_the_decided_card_keeps_one_message_when_the_fit_cannot_shrink`;
- `test_a_decided_card_without_the_line_changes_no_post_s_ai_label`.

The covering tests are listed in the build note.

## Known limits

- The upscale is an AI model (Real-ESRGAN x4plus, `src/upscale.py`), and the parallax move uses a depth-estimation
  model. Neither sets the label: they enlarge or move the real photo without changing what it shows, which is the
  "minor edits" and camera-effect case above (YouTube Help 14328491), not image_gen or motion output.
- Facebook photo posts and Stories showing AI media are not posted to Facebook, because no API label exists there.
  The Instagram post still goes out, with the label.
- A Story frame or slide with no still record (older runs only) cannot be traced. Instagram labels it (fail closed),
  but Facebook still gets it, as before. Holding Facebook there too would change origin/main's expected requests
  (`tests/test_publisher.py`, record-less fixture photos), which this row may not do. Every still drawn since the
  still records exist has one, and none of the real photo posts measured shows AI media. Accepted for the first
  release (review round 2) on one condition, logged as a follow-up: land option B (judge a record-less still by the
  run's master scenes), or option A, with QF-65 before any older (pre-still-record) run's Stories or carousel is
  approved or re-queued.
- A run's label reads the files on disk at card and post time. A run whose scene clips were deleted is labelled
  (fail closed), and its card says why.
- Older runs made before still records existed have covers, Stories and carousels without records, so they are
  labelled. Their bookings are past the carousel's 7-day window.
