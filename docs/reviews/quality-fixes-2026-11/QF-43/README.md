# QF-43: Publishable-field allowlist on the review card

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2 (STOP-SHIP).

The finding: writer-controlled fields reached publication with shape checks only. The YouTube title, description
and tags were posted but never shown on the card, and neither were the carousel alt texts (`src/telegram_bot.py`,
`_card_text`). The card's caption was not the posted one, because the illustration disclosure and the carousel
clean-up only ran after Approve (`src/publisher.py`, `with_disclosure` / `_caption_for`). The card also had no
slot and no closing time.

## Change

- `src/publishable.py` (new) holds what one Approve publishes.
  - `ENVELOPE_FIELDS` is the allowlist. It maps each envelope metadata field to the card lines that show it.
    `RECORDED_ONLY` lists the fields kept for the record that no publisher posts as text (`location`,
    `meta_topic_categories`, and the `is_ai_generated` flag, which comes from config).
  - `envelope_metadata` builds the envelope metadata from the allowlisted payload fields only, with the
    disclosure applied.
  - Hashtags come from the topic's candidate list (`hashtag_candidates`): the topic record's own hashtags (generic
    engagement tags removed), then one tag for each of the topic's names (its title before any ":", place, city,
    region, country, sub-theme; a name tag longer than 25 characters is not made).
    - The writer's tags that are on the list are kept, in the writer's order.
    - If fewer than 3 are kept, the list tops them up. At most 5 are posted.
    - `retag` rebuilds the copy's hashtag line from that list. Any other `#tag` in the copy loses its `#`, so
      no sentence is cut. Credit lines are not changed.
  - `card_copy` returns every string as the platform receives it, through the publishers' own field builders
    (the Reel: Facebook title, Instagram/Facebook caption, first comment; the YouTube Short: title, description,
    tags; the documentary: YouTube title, description, tags).
  - `carousel_base` is the carousel's caption with the same hashtag rule (its own caption, or the Reel caption for
    a script without one). `card_record` adds it for each platform as its request carries it
    (`carousel_captions`, through `publisher._caption_for`), each slide's alt text when the slides are on disk,
    the slot an Approve books (`post_schedule.next_reel_slot` over the booked Reel days) and when the review closes.
  - `check_envelope` raises `ValueError` on a metadata field outside the allowlist, or (when a card was sent) on
    any string the card did not show word for word. A card whose copy could not be worked out records an error and
    nothing to compare with, so the envelope is refused.
  - `carousel_post_base` does the same for the carousel, which is posted by its own path days after Approve: it
    returns the caption base and hashtags, and raises `ValueError` when the card showed another carousel caption
    (or none).
- `src/publisher.py`: `reel_fields`, `youtube_fields` and `youtube_short_fields` are now the only place the request
  text is built; the Upload-Post Reel, YouTube and Short requests use them, and so does the card. `build_envelope`
  builds its metadata through `publishable.envelope_metadata` and calls `check_envelope` against the run's record.
- `main.py`
  - `PipelineCoordinator._carousel_request` gives the carousel post its caption as the card showed it
    (`carousel_post_base`, then the photo credits from `_carousel_post`, which now takes the `hashtags` to keep).
    The scheduled carousel (`_post_photos`), its retry and the dry run's simulated carousel all use it. A refused
    carousel is marked failed, with an alert; a retry says why it was not retried.
  - The coordinator gives the bot its `_approval_window`, so the first card says when the review closes.
- `src/telegram_bot.py`
  - `send_review_card` computes the record, passes it to the card and stores it under `review-copy` before the
    card's buttons can decide. The decision re-render uses the stored record.
  - The card's title, caption, carousel caption and first comment are the posted strings (the caption with the
    disclosure, the carousel caption without a direction cue); its Hashtags line is the posted hashtags.
  - `format_card_text` fits the card exactly as QF-42 does (`_fitted_card`, on the posted strings). Then
    `_with_publish_lines` adds the rest, only in the room the fitted card leaves:
    - the folded "📤 Also posted, word for word" list after the copy's section, in the longest form within the
      card's budget (whole; its first items; its count). It lists the YouTube Short (named as the card's title,
      caption and hashtags when it is those strings; marked and listed last while `YOUTUBE_SHORTS` is off), the
      Facebook Reel, the carousel caption per platform (said as its changed lines when only a few differ), each
      slide's alt text and the written hashtags that are not posted. A documentary lists its YouTube title,
      description and tags.
    - on a card waiting for a decision only: the `🗓 Slot: … · review closes …` line under the title (the closing
      time alone when it is the same day) and, when no form of the list fit, its count on one line
      (`📤 Also posted: N items in the reply`). Both sit in the room `CARD_PAGE_LIMIT` keeps for a decision's
      badge, so the card stays one formatted message under `telegram_pages.PAGE_ABOVE`. The decided card is rebuilt
      with its badge and without them, within `CARD_PAGE_LIMIT` less the badge, as before.
  - The reply under the card ("📋 In full") has the whole list whenever the card does not, and the slot line when
    the card had no room for it (`needs_publish_reply`).

## Round 2 (review of round 1)

1. The carousel caption on the card was not the posted one. Round 1 showed the carousel caption with the topic's
   hashtags, but the carousel post (`_carousel_post`) still sent the written caption, and the carousel had no check
   against the card. The carousel post now uses the same `carousel_base` the card shows and is refused when it
   differs from the card's record. The reviewer's probe (carousel caption `Swipe the rooms.\n#StoneCity
   #BuyFollowersNow`) is in `measure.py` and in the regression test. The carousel posts the card's caption on both
   platforms, without `#BuyFollowersNow`. On the 29 real carousels, the posted caption is the card's caption plus
   only the photo credit lines in 29 of 29.
2. The card regressed against the QF-42 card. Round 1 laid the slot line and the list out with the card, which cut
   narration, dropped "see Edit" lines and moved captions. The card is now fitted first, exactly as QF-42 fits it,
   and the additions only take room it leaves. On all 30 real cards, the card without the additions is QF-42's
   layout of the posted strings, character for character (the "said once" reply pointer excepted, which can move
   to the list's count). Against the QF-42 card of the written strings, no card shows less: no shorter narration,
   no caption lower (whole > clipped > "see Edit" > absent), no findings list shortened further, no credits list
   lost, and no card longer past PAGE_ABOVE.

## Round 3 (review of round 2)

1. Approve was refused on a real documentary run. `card_record` looked the topic up in the payload alone, and a
   documentary's payload has no `topic_id` (`publish_run` fills it from the run before `build_envelope`). The card
   recorded no topic hashtags, the envelope had them, and `check_envelope` compared the `hashtags` label even for
   the YouTube upload, which posts no hashtags. Resending the card could not fix it. Now `send_review_card` passes
   the run's `topic_id` and `card_record` falls back to it (as `carousel_post_base` does), and `check_envelope`
   leaves the `hashtags` label out of the YouTube check. On the real Richat documentary
   (`run_richat_structure_sahara_eye_1792740961_long`), Approve's envelope is refused on round 2 and built after.
2. The documentary card showed a slot it never books. The slot line now shows a slot only when Approve books one.
   `PipelineCoordinator._approve_posting` is the rule `_after_approval` itself now follows: `"slot"` for a live
   Reel while the coordinator schedules posts, `"now"` otherwise (a documentary, or no scheduled posting, as on the
   command line), `"dry_run"` for a dry run. The coordinator hands it to the bot as it hands it the approval
   window. The card then says `posted as soon as you approve`, or `none, a dry run posts nothing`. A bot without a
   coordinator says the same from the run's mode.

## Metric (`python measure.py <repo root>`)

The real code paths run offline: a temporary database with one topic, `send_review_card` with the Telegram request
captured, `build_envelope`, the Upload-Post Reel and Short requests, and the scheduled carousel post
(`PipelineCoordinator._post_photos`) with `requests.post` captured. The form fields are recorded and nothing is
sent. A string named on the card ("the caption above") counts as shown only if that card element is the posted
string word for word.

| | before (ecf7800) | after |
|---|---|---|
| Posted strings shown on the card word for word (Reel title, IG caption, FB title, FB description, first comment, Short title, description, tags) | 3/8 | 8/8 |
| Card shows `ILLUSTRATION_DISCLOSURE` (the posted caption carries it) | no | yes |
| Card shows the YouTube title / description | no / no | yes / yes |
| Card slot line equals `post_schedule.fmt(next_reel_slot(now, booked))` | no line | yes |
| Card shows when the review closes | no | yes |
| A hashtag not on the topic's list (`#BuyFollowersNow`) is posted with the Reel | yes | no |
| `build_envelope` raises on a title the card did not show | no | yes |
| Carousel caption posted is the one the card shows (Instagram / Facebook) | no / no | yes / yes |
| `#BuyFollowersNow` posted with the carousel | yes | no |
| Carousel post refused when its caption is not the card's | no | yes |
| Documentary without `topic_id` in its payload: Approve's envelope built after its card (round 2: refused) | yes (no check) | yes |
| Slot line true to what Approve does (booked Reel / documentary / no slots / dry run; round 2: 1/4) | 0/4 (no line) | 4/4 |
| acceptance_met | false | true |

`before.json`/`before.txt` and `after.json`/`after.txt` are the script's output on each tree (`measurements.json` is
the after run). The slot and closing times move with the clock; the metric is the equality. Regression test:
`tests/test_qf43_publishable_card.py` (15 tests). All 15 fail on ecf7800 and pass after the fix; the 5 added in
round 3 (the documentary without a `topic_id`, the four slot-line cases) fail on round 2's tree.

## Real cards (RR_FIXTURE_REPO set; 30 development runs with a release report)

`format_card_text` with the card's record (fixed clock, one-hour window), against the QF-42 card. The QF-42 card
measured on this tree without a record equals the card at ecf7800 for all 30 runs.

| | QF-42 card (ecf7800) | round 1 | after |
|---|---|---|---|
| cards with a caption lower / narration cut further / longer past PAGE_ABOVE | - | 6 / 4 / 2 | 0 / 0 / 0 |
| cards with any regression against the QF-42 card (`regressions_vs_qf42_card`) | - | - | 0 |
| fitted part = QF-42 layout of the posted strings | - | - | 30/30 |
| narration shortened | 6 | 6 (4 cut further) | 6 (none cut further) |
| caption "see Edit" or absent (main / carousel) | 9 / 9 | 10 / 10 | 9 / 9 |
| caption characters shown (both captions, all cards) | 10664 | 10161 | 10948 |
| credits list on the card | 25 | 25 | 25 |
| over PAGE_ABOVE (4000) / over 4096; longest | 2 / 2; 4211 | 2 / 2; 4269 | 2 / 2; 4199 |
| with the slot line | 0 | 30 | 28 (the two Nazca cards over 4096: in the reply) |
| "Also posted" list on the card: whole / first items / count / in the reply only | - | 21 on the card | 1 / 1 / 26 / 2 |
| Approve's envelope (as `publish_run` builds it) refused | - | - | 0 (round 2: 1, the Richat documentary) |
| slot line (card or reply) true to what Approve does | - | - | 30 (round 2: 29, the documentary showed a slot) |

The pending card is now between `CARD_PAGE_LIMIT` (3880) and PAGE_ABOVE (4000) on 27 more cards (`over_card_page_limit`
2 -> 29). That is the badge room; the decided card is still within `CARD_PAGE_LIMIT` less its badge.

Per caption, against the QF-42 card (`before.txt`, `after.txt`): 17 previews are longer, 1 caption gains its "see
Edit" line (unfinished_obelisk_1792985912), and 4 show fewer characters. None of the 4 is a layout loss:

- kailasa_1792524470 carousel 346 -> 343, nazca_1792961797 carousel 289 -> 288, richat_1792737039_short carousel
  302 -> 300: whole on both cards. The posted carousel caption is shorter, because the direction cue (`➡️`, `👉`)
  comes out of every carousel request (`_caption_for`). The card now shows that string.
- odessa_1792970549: caption 136 -> 124 and carousel 122 -> 132, both clipped before and after (258 -> 256
  characters in all). The posted hashtags line is one character shorter, and QF-42's word-boundary clipping
  splits the room differently. The fitted card is QF-42's own layout of the posted strings.

## Consequences and what is left

- Hashtags: on the 34 development runs with a topic record, 49 of the writer's 149 tags are on their topic's
  candidate list. The rest are left out, and the card lists them. The writer prompt does not yet receive the
  candidate list; giving it the list is the natural follow-up and needs its own writer evaluation.
- A run whose topic record cannot be read posts no writer hashtag (fail closed).
- If the topic's hashtag list changes between the card and the post, the copy no longer matches the card, so
  `build_envelope` and the carousel post refuse and the card must be resent.
- Without a card record (command-line approval, Telegram not configured, or the `TELEGRAM_CARDS_V2` rich card,
  off by default, which does not show the copy), `build_envelope` and the carousel still enforce the allowlist and
  the candidate list; they have no card strings to compare against.
- On a full card the "Also posted" strings are in the reply under the card, and the card says how many there are.
  A card with no room left under PAGE_ABOVE (2 of 30) has them, and its slot line, in the reply only.
- The carousel's photo credit lines are added by the post from the slides' signed records (QF-42); the card shows
  the caption before them and says that they follow.
- Cost per run is QF-68, not this row.
