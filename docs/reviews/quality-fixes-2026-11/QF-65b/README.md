# QF-65b: QF-65a's follow-ups

Source: QF-65a's round 3 review (non-blocking 1 and 2), its port review (non-blocking 1 to 3) and the Facebook
follow-up QF-65a logged as a known limit. Base: the landed tip with QF-65a and the UI-01 chain.

## What changed

1. **The AI line on decided cards where it fits** (src/telegram_bot.py, `TelegramApprovalBot._ai_layout`).
   - Where the line's room would cost what QF-42 keeps, the decided card (rebuilt with its badge) was QF-42's card
     with no AI line. That card also drops the narration's old AI line, because the record replaces it, and the room
     that frees went unused.
   - Now the line, whole if it fits and brief otherwise, is put back under the title wherever that card still fits
     CARD_PAGE_LIMIT less the badge. The line is the only change to that card, so it costs the narration, the
     captions, the caption previews and the publish lines nothing.
   - The insertion is the same one the waiting card uses (`_under_title`, which was the nested `with_ai`).
   - The review panel (UI-01e) already shows the brief line on Summary and the whole line on Posts, from the same
     `_ai_label_line`. Both card paths use the same text, including item 3's "kept off Facebook".
2. **README correction** (QF-65a/README.md, the sentence on decided cards without the line). On those renders the
   card made no AI statement at all, where the base showed "Synthetic media label embedded for Meta CMP compliance".
   The old sentence said the card looked as it did before QF-65a's line.
3. **Facebook and older stills without a record** (src/ai_label.py `assess_photos`, src/publisher.py
   `publish_photos`).
   - Operator decision `decisions\QF-65b.md`, 2026-11-12: fail closed.
   - A Story frame or carousel slide with no record comes from a run made before still records. It is now judged by
     its run's video: the master's scenes from its composition manifest, and the picture check's illustrated beats.
     It goes to Facebook only when every scene traces to real media.
   - AI media, a scene that cannot be traced, or no readable video record keeps the request off Facebook, which has no
     AI label for photo posts. `held_platforms` says why, and so does the posting alert, as for stills showing AI
     media.
   - Instagram's label is set for these stills as before.
   - This is option B from the review, not a blanket hold: a record-less still from a wholly real video still goes to
     Facebook.
   - Every older-run path goes through `MetaPublisher.publish_photos` and is covered: Stories, a carousel drawn with
     the run, its Facebook copy, `retry_post`, and the dry run's plan.
   - The card's record (`ai_label_photos.facebook_hold`) makes the card line say "Stories/carousel kept off Facebook",
     the same as for AI media.
4. **Port-review test notes** (tests/test_qf81_lazy_carousels.py).
   - The vacuous `... or True` assertion now checks something. The recorder keeps each request's file paths, and the
     test asserts that Facebook's request sends carousel_fb/ slides and Instagram's sends carousel/ slides.
   - New test: the real renderer (`MultiFormatGenerator.generate_carousel_sequence`, its own still_layout.json
     records, the real scene planner with only the provider call replaced) draws the carousel at its QF-81 slot, with
     one photo AI-made according to its lineage record. The Instagram request showing it carries `is_ai_generated`,
     and no Facebook request sends a slide showing it.
   - The "COVER_RESULT" placeholder was in the programme's port note, not in any committed file. The note now cites
     the port review's covering run.

## Measurements (measure.py; `measurements.json`, base fe88d4a6 under `_base_fe88d4a6`)

Fixtures: the default output, which check 8 compares.
- Record-less Story frame and carousel slide, live and dry run, for each run video: AI motion clip, illustration
  scene, hook clip with no record, master without a media record, no video.
  - Sent or planned to Facebook: 10/10 -> 0/10 for each of live and dry run.
  - Instagram flagged: 10/10 -> 10/10.
- Record-less stills from a wholly real video: sent to Facebook 2/2 -> 2/2.
- Guard: a slide whose record names a credited photo, in a run whose video shows an AI clip. Before and after it goes
  to Facebook, and Instagram is not flagged.
- Decided card, two fixture shapes x 4 badges (the narration's old line on):
  - AI line on 2/8 -> 8/8;
  - captions lost, narration newly cut, previews lost and publish lines moved 0 -> 0;
  - every card within CARD_PAGE_LIMIT.
- `acceptance_met`: false -> true.

Real data, read only, counts only (`--runs`, `--cards`):
- Photo posts (Story sets, carousels, Facebook's copy):

  | Count | dev output | production sample |
  |---|---|---|
  | photo posts checked | 87 | 102 |
  | with a still without a record | 66 | 45 |
  | kept off Facebook, base -> this commit | 0 -> 24 | 0 -> 45 |
  | of those, a still without a record | 24 | 45 |

  - Dev: the other 42 record-less posts come from runs whose video traces wholly to real media, so they still go to
    Facebook.
  - Production sample: it cannot trace any video, by construction. The pull excluded `parallax/` and `motion/`, and
    its records name the VM's paths. So every record-less post there is held. On the VM the lineage exists, and the
    count there will be lower.
  - No photo post with a record changed.
- Decided cards, 35 dev cards x 2 badges x 2 names = 140 renders:
  - without an AI line 11 -> 8;
  - captions lost, narration newly cut, previews lost and publish lines moved 0 -> 0;
  - over PAGE_ABOVE 0 -> 0;
  - over CARD_PAGE_LIMIT 2 -> 2, which is QF-42's own floor and unchanged;
  - longest 3,914 -> 3,914.
  - The 8 renders still without the line: their bare card is already at or over the badge's room, so adding the line
    would push it over.
- Waiting cards, 35 dev cards, all counts unchanged:
  - AI line 35, saying the label is on 35;
  - lists shortened 5, narration start shown 31, narration characters shown 24,606;
  - caption previews 57, captions named 60;
  - "Also posted" count line 5;
  - longest 4,000, over 4,096 0.

## Tests

- `tests/test_qf65b_ai_label_follow_up.py`, 20 tests. On the base, 17 fail by assertion and 3 pass. The 3 that pass
  are guards: a record-less still from a wholly real video still goes to Facebook (story and carousel), and a slide
  with its own record is not held.
- `tests/test_publisher.py` (on origin/main), changed under `decisions\QF-65b.md`:
  - `test_carousel_is_one_post_with_caption_and_max_ten`: its slides have no record and no video record, so
    Facebook's request (of five) is not sent. The test expects Instagram's ten alone, with the label, and the
    Facebook hold. The ten-and-five split is still asserted, now in the QF-65b test on a wholly real run.
  - The Facebook-copy test and the dry-run plan test test routing, not the label. Their slides now have a record
    (`_recorded`), as every run since still records has. Their assertions are unchanged.
- tests/test_qf81_lazy_carousels.py (branch-only): the recorder records the file paths, the `or True` assertion is
  replaced, and the real-render test is added. These are test-only and pass on the base too.
- tests/test_carousel_chrome_2026_10_28.py (branch-only), `test_facebooks_photo_post_never_says_swipe`: a routing
  test. Its slides now have a record, and its assertions are unchanged.
- tests/test_qf65a_ai_label.py (branch-only): the docstring of the record-less still test now says Facebook is
  judged by the run's video. Its assertions are unchanged.

Known limit, unchanged: a still with a record whose source photo is uncredited or missing is flagged on Instagram
but still goes to Facebook. The decision covers stills without a record only.
