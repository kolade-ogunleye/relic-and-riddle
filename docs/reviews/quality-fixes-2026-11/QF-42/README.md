# QF-42: Sanitised, capped credit lines

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2 (STOP-SHIP). Finding: the Commons
`Artist` field and file title were published verbatim in the caption and description (`src/credits.py`,
`asset_attribution`; `main.py` through `add_provenance_attributions`). The release gate required those lines
(`src/release.py`). The fact and banned-phrase checks skipped them (`src/trace_check.py`, `_is_credit_or_tag`).
The review card hid them when the caption was long. A photographer's e-mail address was stored in a committed
`credits.json`.

## Change

- `src/credits.py`
  - `sanitise_credit_text()` removes e-mail addresses, links, `@handles`, hashtags and the `|` field separator
    from a credit's author, title and licence. A link is a scheme, `www.`, `host/path` such as `t.ly/y`, or a
    bare host with a common top-level domain. The number in "Room #3" stays.
  - `credit_title()` is the sanitised title, capped at `TITLE_MAX = 60` characters. It is cut at a word and
    ends with "…".
  - `author_name()` sanitises the name it finds. An artist field that is only an e-mail address gives "Unknown".
  - `asset_attribution()` prints author | title | licence | source page | licence URL. The two URL slots take
    one http(s) URL each, otherwise "Unknown".
  - `credit_line_problems()` checks a published 📷 line for a link, address, handle or hashtag in its text
    fields, for a URL slot that is not a URL, and for a banned phrase (`script_engine.find_banned_tropes`).
  - Round 2, migration of lines written before this change:
    - `rewrite_credit_line()` rebuilds a 📷 line in the five-field shape from its own fields, sanitised and capped.
      For a line written before this change it gives the same line `asset_attribution()` prints for the record.
    - `rewrite_credit_lines()` / `rewrite_payload_credits()` apply it to the caption, description, carousel
      caption and first comment, and drop a credit line that then repeats an earlier one.
    - `credit_present()` is how the gate finds a record's credit: the new line, or an old line that rewrites to it.
    - `add_provenance_attributions()` (every render, so every regeneration) rewrites the copy's credit lines before
      adding its own, so the lines `hold_attribution()` / `restore_attribution()` carried over are not kept beside
      the new ones. `restore_attribution()` itself is unchanged (its existing tests keep their expected values).
- `src/release.py` runs every 📷 line in the caption, description, carousel caption and first comment through
  `credit_line_problems()`.
  - New check `credit_lines`: fails on a link, address, handle or hashtag. Its detail says that Edit or Regenerate
    rewrites the lines.
  - New check `credit_phrases`: needs review on a banned phrase in a quoted file title.
  - Round 2: `image_attribution` and `description_attribution` use `credit_present()`. A run credited before this
    change keeps the gate it had (so a run waiting for approval at deploy keeps Approve & Post). An old line with a
    link still fails `credit_lines`.
- `main.py` (`edit_copy`, round 2): any edit rewrites the credit lines of the caption, description, carousel caption
  and first comment, so Edit clears an old line that fails `credit_lines`.
- `src/commons.py` sanitises the artist at ingest (`candidates()`). The title is still stored as Commons gives
  it, because it is the file's identifier (duplicate checks and look-ups). It is sanitised and capped where
  it is published.
- `src/telegram_bot.py`: every credit line from the caption and carousel caption is on the review card, each on its
  own line in a folded "📷 Credits posted with the captions: N" list under the copy. The caption block shows the
  rest of the caption.
  - Round 2, length budget: the list is filled after the narration, before the captions, in the longest form that
    leaves the captions up to `CARD_CAPTION_ROOM` (400) characters: whole lines; without the two URLs; author and
    title cut to 32 characters; author alone. Room for the author-alone form is kept before the narration is fitted.
    A shortened list says so, and the reply under the card ("In full", `detail_messages`) carries every line whole.
  - `format_card_text` shortens the folded findings when even the author-alone form does not fit. Only a card the
    findings fill at every step gets the count alone ("📷 Credits: N: the full list is in the reply under this card").
  - Round 4 (review of round 3: on real runs the main caption read "(too long for this card: see Edit)" on 8 cards
    instead of 2, and the narration was shorter on 5 cards):
    - The captions are scored, not budgeted as one sum. A credit form is accepted only when every caption keeps a
      preview and the captions keep their text up to `CARD_CAPTION_ROOM`; otherwise the form that shows the captions
      best is used (a shorter form before a dropped caption). The count alone is used only when no form fits.
    - The carousel caption, filled first, keeps back the room of the main caption's clipped preview (or, when only
      that fits, of its "see Edit" line), whichever shows the captions best.
    - A clipped caption that does not fit once escaped is clipped again, then falls back to "see Edit"; it was
      dropped.
    - `format_card_text` shortens the findings further when that gives back a caption preview that the card
      without the list (`_card_text(..., credits=False)`, the e1d8466 card) shows.
    - The room is measured on the card as sent: the repeated "the full list is in the reply under this card" is
      said once (`_said_once`), and the narration's "[shortened: …]" note is measured escaped. A card whose notes
      leave no word of narration keeps room for the count alone rather than for a list that cannot fit.
    - The short forms (author and title; author alone) list a repeated line once with its count:
      "📷 Galvm (×5)" instead of "📷 Galvm" five times.
  - Round 5 (review of round 4: on Wieliczka the carousel caption was left off the card, though "see Edit" lines for
    both captions fit): `_card_text`'s caption fill ranks a caption on the card (even only as "see Edit") ahead of
    another caption's preview. The score was (previews, blocks, characters), so one clipped preview with a dropped
    caption (1, 1, 131) beat two "see Edit" lines (0, 2, 0). It is now (blocks, previews, characters), and a credit
    form is accepted only when every caption has a preview. A caption is left out only when even its "see Edit" line
    does not fit, as `format_card_text` promises ("at least its first lines, or where to read it").
- `assets/derinkuyu_underground_city/credits.json`: one rejected photo had an e-mail address as its artist.
  It is now "Unknown".

## Metric (`python measure.py <repo root>`)

`measure.py` runs the real code with deterministic inputs and makes no network connection. It measures:

- the review's example title, as given and on a CC BY record;
- six hostile Commons records;
- a review card whose caption is clipped (300-word narration, six credit lines);
- every committed `credits.json`;
- round 2: twelve review cards crowded with credits (3, 6, 10 or 14 CC BY photos with long titles and two URLs each,
  a 2,000-character caption, 0, 6 or 12 folded findings);
- round 2: `release.write_release_report` on run folders made by the script whose copy carries credit lines in the
  form written before this change, before and after the rewrite that Edit and Regenerate apply;
- round 2: the regeneration path (`hold_attribution`, `restore_attribution`, `add_provenance_attributions`) on that
  copy;
- optional: with `RR_FIXTURE_REPO` set to a checkout that has `output/`, the same card and gate measures on its real
  `run_*` folders (read only; media and checksum checks are not run).

| Measure | Before (e1d8466) | After |
|---|---|---|
| Example title field (`File:x t.ly/y #tag`, CC BY) | `File:x t.ly/y #tag` | `File:x` |
| Example has a link or hashtag | true | false |
| Hostile corpus lines with link/address/handle/hashtag | 5 of 6 | 0 of 6 |
| Longest title in a credit (corpus / committed records) | 217 / 202 | 59 / 59 |
| Committed records with a title over 60 characters | 100 of 278 | 0 of 278 |
| Credit lines on a clipped card (`_card_text` / `format_card_text`) | 1 of 6 / 1 of 6 | 6 of 6 / 6 of 6 |
| `credits.json` files holding an e-mail address | 1 | 0 |
| Crowded cards: over `CARD_PAGE_LIMIT` (3,880) / over 4,096 | 0 / 0 | 0 / 0 |
| Crowded cards missing a photo's 📷 line | 12 of 12 | 0 of 12 |
| Old safe lines (90-character title): `image_attribution` | pass | pass |
| Old line with a link: `credit_lines`, then after Edit's rewrite | no check / old line kept | fail, then pass |
| Regenerated copy: photo lines (2 photos) / titles over 60 / with link or hashtag (caption and description) | 2 / 1 / 1 | 2 / 0 / 0 |
| Near-full cards (regression test's, source note 42 / 45): caption, carousel caption, narration, every photographer | clipped 468, full 317, whole, no / clipped 331, full 317, whole, no | clipped 275, full 317, whole, yes / clipped 139, full 317, whole, yes |
| Round 5, "see Edit" card (regression test's, source note 52): caption, carousel caption, narration, every photographer | absent, full 317, whole, no | see Edit, see Edit, whole, yes |
| Acceptance met | false | true |

Real runs (`RR_FIXTURE_REPO`, 30 runs with a review card, 28 with photo credits; recorded in
`before.json` / `after.json`):

| Measure | e1d8466 | Round 1 (review) | Round 3 (review) | Round 4 (review) | After |
|---|---|---|---|---|---|
| Cards over 3,880 / over 4,096 | 2 / 2 | 9 / 6 | 2 / 2 | 2 / 2 | 2 / 2 |
| Cards with a photo credit missing | 28 | 1 | 4 | 4 | 4 |
| Cards with a folded findings list shortened | 9 | 17 | 13 | 10 | 10 |
| `image_attribution` not passing | 2 of 28 | 16 of 28 | 2 of 28 | 2 of 28 | 2 of 28 |
| `credit_lines` failing | (no check) | 0 | 0 | 0 | 0 |
| Main caption "too long for this card: see Edit" | 2 | | 8 | 1 | 2 |
| Carousel caption "too long for this card: see Edit" | 1 | | | 2 | 3 |
| Main / carousel caption left off the card | 7 / 6 | | | 7 / 7 | 7 / 6 |
| Cards with a main caption preview / a carousel caption preview | 21 / 23 | | | 22 / 21 | 21 / 21 |
| Cards that lost a caption preview / narration length / a caption on the card, versus e1d8466 | | | 13 / 5 / - | 2 / 3 / 1 | 2 / 3 / 0 (forced, listed below) |
| Cards whose shortened findings lists show only their count where e1d8466 showed their first items | 0 | | | 1 | 1 (forced, listed below) |

The round-1 column is `measure.py` run on the round-1 commit (8ee3faf), whose review raised these items. The
reviewer counted 35 runs; this checkout has 30 with a release report, so the counts differ slightly from the review's.
On the crowded synthetic cards the round-1 commit had 1 of 12 over 3,880; the fix has 0.

- The two cards over 4,096 are the same two Nazca runs before and after, 22 characters longer than at e1d8466.
  Their "Pictures checked against their lines" note is about 2,000 characters and `format_card_text` cannot shorten
  it, so every step leaves the card over the limit (narration cut to its note, both captions gone). That note is a
  separate, older defect. These two cards, and one other Nazca run that the findings also fill, show the credit
  count and the reply link instead of the list. The fourth card with a credit missing is Wieliczka: one of its old
  lines splits a multi-line artist field across two lines, so it does not have the five-field shape.
- One more card than at e1d8466 shortens a findings list (Gol Gumbaz: the release gate's list, "…and 2 more"):
  that gives back both caption previews, which the card without the list showed. The shortened items are in the
  reply under the card. Round 3 had four such cards and still lost the previews.
- The 2 runs whose `image_attribution` does not pass are the same 2 as at e1d8466 (photos missing from their copy).

### Rounds 4 and 5: every real card, e1d8466 -> after

`format_card_text` on the 30 real runs (`RR_FIXTURE_REPO`, read only), from `before.json` / `after.json`
(`real_runs.per_card`). Caption columns: how the card shows it (full, clipped, `too_long` = only "see Edit",
`absent` = not on the card) and the characters of its text shown, credit lines not counted. A shorter caption
preview is the room the credit lines now take (before, a long caption carried them and they were cut with it).
Findings lists (round 5): of the card's shortened folded lists, how many show their first items then "…and N more",
and how many show only their count ("4 items").

| Run | Caption | Carousel caption | Narration (chars) | Findings lists: first items / count only | Photo authors missing |
|---|---|---|---|---|---|
| amber_room_heist_1792790550 | clipped 695 -> clipped 260 | full 276 -> full 276 | 715 -> 715 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| amber_room_heist_1792801257 | clipped 503 -> full 413 | full 255 -> full 255 | 705 -> 705 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| amber_room_heist_1792805842 | clipped 499 -> clipped 182 | full 292 -> full 292 | 714 -> 714 | 0 / 0 -> 0 / 0 | 1 -> 0 |
| damascus_steel_nanotubes_1792732472_short | full 574 -> full 574 | full 228 -> full 228 | 769 -> 769 | 0 / 0 -> 0 / 0 | 0 -> 0 |
| gol_gumbaz_whispering_gallery_1793278143 | clipped 137 -> clipped 124 | full 320 -> clipped 248 | 697 -> 697 | 0 / 0 -> 1 / 0 | 2 -> 0 |
| greek_fire_siphon_1792735284_short | clipped 311 -> clipped 131 | full 251 -> clipped 164 | 764 -> 764 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| hal_saflieni_hypogeum_1792949304 | clipped 534 -> clipped 154 | full 307 -> full 307 | 760 -> 760 | 0 / 0 -> 0 / 0 | 1 -> 0 |
| kailasa_temple_monolith_1792524470 | clipped 618 -> clipped 163 | full 346 -> full 346 | 666 -> 666 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| nazca_lines_runway_paradox_1792961797 | clipped 462 -> clipped 341 | full 289 -> full 289 | 719 -> 719 | 0 / 2 -> 0 / 2 | 4 -> 0 |
| nazca_lines_runway_paradox_1793247464 | absent -> absent | absent -> absent | 67 -> 67 | 0 / 1 -> 0 / 1 | 4 -> 4 |
| nazca_lines_runway_paradox_1793251266 | absent -> absent | absent -> absent | 67 -> 67 | 0 / 1 -> 0 / 1 | 4 -> 4 |
| nazca_lines_runway_paradox_1793262388 | absent -> absent | absent -> absent | 143 -> 188 | 0 / 2 -> 0 / 2 | 4 -> 4 |
| nazca_lines_runway_paradox_1793267092 | absent -> absent | absent -> absent | 499 -> 278 | 0 / 2 -> 0 / 2 | 4 -> 0 |
| odessa_catacombs_1792970549 | clipped 237 -> clipped 136 | full 334 -> clipped 122 | 666 -> 666 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| odessa_catacombs_1793098390 | clipped 409 -> clipped 132 | full 334 -> clipped 270 | 666 -> 666 | 1 / 0 -> 1 / 0 | 2 -> 0 |
| odessa_catacombs_1793100750 | clipped 642 -> clipped 253 | full 334 -> full 334 | 666 -> 666 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| richat_structure_sahara_eye_1792737039_short | clipped 537 -> clipped 195 | full 302 -> full 302 | 706 -> 706 | 0 / 0 -> 0 / 0 | 1 -> 0 |
| richat_structure_sahara_eye_1792740961_long | absent -> absent | absent -> absent | 2597 -> 2420 | 1 / 0 -> 1 / 0 | 2 -> 0 |
| richat_structure_sahara_eye_1792778206 | clipped 488 -> clipped 131 | full 392 -> clipped 300 | 674 -> 674 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| richat_structure_sahara_eye_1793272675 | too_long -> too_long | full 331 -> too_long | 718 -> 718 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| richat_structure_sahara_eye_1793474843 | clipped 181 -> clipped 122 | full 406 -> clipped 223 | 669 -> 669 | 0 / 0 -> 0 / 0 | 1 -> 0 |
| roman_self_healing_concrete_1793163918 | absent -> absent | absent -> absent | 724 -> 368 | 0 / 0 -> 0 / 0 | 3 -> 0 |
| singing_colossi_of_memnon_1792952878 | clipped 350 -> clipped 133 | full 339 -> clipped 206 | 694 -> 694 | 0 / 0 -> 0 / 0 | 3 -> 0 |
| trepanation_miracle_1792995462 | clipped 280 -> clipped 134 | full 419 -> clipped 356 | 776 -> 776 | 1 / 2 -> 1 / 2 | 1 -> 0 |
| tunguska_event_1908_1792930600 | clipped 361 -> clipped 127 | full 359 -> clipped 265 | 661 -> 661 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| unfinished_obelisk_aswan_1792985912 | absent -> absent | clipped 354 -> too_long | 703 -> 703 | 0 / 0 -> 0 / 0 | 2 -> 0 |
| unfinished_obelisk_aswan_1792993374 | clipped 588 -> clipped 466 | full 385 -> full 385 | 683 -> 683 | 0 / 0 -> 0 / 0 | 1 -> 0 |
| unfinished_obelisk_aswan_1793056896 | clipped 713 -> clipped 129 | full 447 -> clipped 283 | 696 -> 696 | 0 / 0 -> 0 / 0 | 1 -> 0 |
| voynich_manuscript_1792965308 | full 524 -> full 524 | full 389 -> full 389 | 734 -> 734 | 0 / 0 -> 0 / 0 | 0 -> 0 |
| wieliczka_salt_mine_1792974672 | too_long -> too_long | too_long -> too_long | 722 -> 722 | 3 / 0 -> 1 / 2 | 5 -> 1 |

No card lost a caption preview, narration length, a caption on the card or a findings list's first items except
these six. For each, `real_runs.per_card.<run>.every_findings_cap` records the card at every findings cap with the
list and without it (the narration at each cap, and the caps that fit with the narration whole and every caption on
the card): no cap gives the preview, the narration or the first items back with the list, and the list is in its
shortest form (author alone, repeated authors counted) on every one of those cards. Every credit line shown is what
takes the room:

- `nazca_lines_runway_paradox_1793267092`: narration 499 -> 278 characters (8 credit lines; both captions absent
  before and after; the card's notes fill it).
- `richat_structure_sahara_eye_1792740961_long`: narration 2,597 -> 2,420 characters of a documentary narration
  already shortened at e1d8466 (5 credit lines).
- `roman_self_healing_concrete_1793163918`: narration 724 -> 368 characters (5 credit lines; both captions absent
  before and after).
- `richat_structure_sahara_eye_1793272675`: the carousel caption (331 characters, whole) is now "see Edit"; the
  main caption was "see Edit" before and after. The list in its shortest form takes 331 characters and leaves 166,
  10 short of a clipped preview.
- `unfinished_obelisk_aswan_1792985912`: the carousel caption (clipped at 354) is now "see Edit"; the main caption
  is absent before and after. 65 characters short of a clipped preview.
- `wieliczka_salt_mine_1792974672` (round 5): both captions read "see Edit", as at e1d8466 (round 4 showed a
  131-character clip of the main caption and left the carousel caption off the card: the review's finding). Its two
  shortened findings lists (outside photos, automated video check) show their count ("4 items", "3 items") instead of
  their first item and "…and N more" (findings cap 0 instead of 1; the release gate's list keeps its first items).
  With the list in its shortest form (8 credit lines) the narration is 504 of 722 characters at cap 1 and whole only
  at cap 0 (`narration_chars_at_caps_none_4_3_2_1_0`: 67, 67, 67, 416, 504, 722); without the list it is whole at
  cap 1. The narration comes first, so the lists give way. Every item is in the reply under the card.

Round 5 versus round 4: Wieliczka's main caption is "see Edit" again instead of a 131-character clip, so the main
caption reads "see Edit" on 2 cards (as at e1d8466; round 4 had 1), and its carousel caption is on the card again.
That is the only real card the change moves. The near-full regression cards (source note 42 / 45) are unchanged. On
`nazca_lines_runway_paradox_1793262388` the narration is 188 characters instead of 143 (the room is measured as
sent). Cards whose carousel caption was whole and is now clipped keep its first 122 to
356 characters: the main caption's preview room is kept before the carousel caption is filled.

`asset_attribution({"title": "File:x t.ly/y #tag"})` with no licence returns "" before and after, because no
licence asks for a credit. The CC BY record is the case that prints a line.

Files: `before.json` / `before.txt` (start commit), `after.json` / `after.txt` and `measurements.json` (fix).
Regression tests: `tests/test_qf42_credit_lines.py` (rounds 1-3) and `tests/test_qf42_caption_room.py` (rounds 4
and 5; round 5: `test_a_caption_with_room_only_for_see_edit_keeps_that_line_rather_than_being_dropped`).

## Notes

- Existing tests keep their expected values. The credit line keeps its five fields and its shape
  (`_CREDIT_SHAPE`).
- The gate accepts an old line only when it rewrites to the record's current line, so its author, licence and URLs
  match and its title only differs by the sanitising and the cap. Any link, address, handle or hashtag in an old
  line still fails `credit_lines`. The gate is no weaker than at e1d8466.
