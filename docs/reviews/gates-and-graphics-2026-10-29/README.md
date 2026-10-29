# Binding gates and graphics (2026-10-29)

Five defects from the 29 Oct production review: the Story poll frame told viewers to "REPLY WITH A OR B" and drew fake
option boxes; an emoji was drawn as "□□" on the carousel; a two-unit number card counted one unit while the other sat
still; Richat's cover said "40+ KM" while the narration said "25 miles"; graphics text could run below the caption
band's gap (y 1058).

A sixth item, banning share asks ("Send this to …") in rendered art, was built and then reverted the same day. The
operator's 2026-10-25 decision stands (`docs/reviews/share-follow-2026-10-25`, from Meta's creator-dashboard tip "ask
your community to share your content"). The detector keeps its original rule: an ask to share with someone in
particular and a genuine question are not bait; shares to a number of people, tags, votes, reactions and set replies
are. The end card and the carousel still show the writer's share-and-follow line, now with any emoji stripped.

The binding gates (Meta/YouTube policy): no engagement bait (likes, votes, shares to a number of people, tags, set replies), no direction
cues, no fake platform UI or buttons, no slide counters, no emoji in rendered art.

## What changed

| # | Defect | Fix | Where |
|---|---|---|---|
| 1 | Poll frame drew A/B option boxes and "REPLY WITH A OR B" | The frame asks the poll question and draws nothing else for it. `POLL_STICKER_ROOM` (y 1300-1560) is a protected region in the frame's layout record, so still_layout reports anything drawn there | `src/multi_format.py` (`POLL_STICKER_ROOM`, `_render_story_frame`) |
| 3 | "□□" on the carousel | `typography.drawable` strips emoji, variation selectors, the zero-width joiner and flags. It also strips any character the font has no glyph for: the character's mask is compared with the font's .notdef box (fontTools is not installed). It runs in `fit_text` and `fit_lines`, in graphics `_text`, in the carousel's `_text` and in the end card | `src/typography.py`, `src/graphics.py`, `src/multi_format.py`, `src/end_card.py` |
| 4 | "280 FT (85 M)" counted the feet only | Every number in the value counts up together. Years still show whole from the first frame | `src/graphics.py` (`_shown_value`) |
| 5 | "40+ KM" against "25 miles" in the narration | `typography.spoken_units`: a figure written in the other system is redrawn in the narration's unit, using the narration's own pair ("25 miles (40 km)"). A figure the narration gives in one unit only is left as written, so nothing is drawn that the fact review did not see. When both units are drawn, the narration's unit goes first | `src/typography.py`; used by number cards (`render_stat_cards(narration=)`), Story frames and carousel slides |
| 6 | Text below y 1058 | Number cards are measured before drawing (`_stat_plan`). A card that would end below `graphics_box()[3]` is lifted, and the card above it moves too. If the two cards still do not fit, the labels are set smaller one step at a time and never cut. The rule under the value now sits below the value's measured ink | `src/graphics.py` |

## Measurements

- **Number cards, dev runs** (31 runs; text bottoms measured on every frame): before the fix, the lowest text ended at
  y 1000. A card with long labels (the synthetic case in `number_card_long_labels_*`) ended at y 1132 before the fix
  and at y 1058 after it. The two cards no longer overlap.
- **Map, every frame of the zoom** (Richat, Bijapur, Derinkuyu, Odessa, Hal Saflieni): the lowest neighbour label
  ended at y 721-875 and the place label's pill at y 816. The map already kept its text inside the limit, because
  `relief_base` bounds its labels by the final view's 1058 line, so it is unchanged.
- **Timeline** (another agent owns it; not edited): on the dev runs its lowest label ends at y 1000-1042, within
  1058. A label kept whole on more lines has no lift or shrink step like the number card's.

## Evidence (half-size JPEGs, before = HEAD 47d6168, after = this change; Richat dev assets)

| Files | What they show |
|---|---|
| `story_poll_frame_before/after.jpg` | Option boxes and "REPLY WITH A OR B" removed; the question stays; the sticker's room is empty |
| `carousel_closing_slide_before/after.jpg` | Production's line with its emoji: "… you know □□, and follow …" becomes "… you know, and follow …"; the share-and-follow line itself is kept |
| `number_card_mid_count_before/after.jpg` | Frame 18: "187 FT (85 M)" (metres fixed) becomes "187 FT (57 M)" (both counting) |
| `number_card_long_labels_before/after.jpg` | Red line y 1058 (graphics text bottom), blue line y 1098 (caption band). Before: overlapping cards and text below both lines. After: separated, above 1058 |
| `number_card_unit_before/after.jpg` | "40+ KM" becomes "25+ MILES" against Richat's narration; the rule no longer cuts the figure |

## Tests

`tests/test_bait_emoji_units_2026_10_29.py` (11 tests): the poll frame (no REPLY line, no option boxes, the sticker's
room empty), emoji and missing-glyph stripping, the two-unit count-up, the narration's units, and the long-label
number card. Against HEAD with stand-ins for the new names, the poll-frame, stripping, count-up, units and long-label
tests fail. The only existing test changed is `test_carousel_chrome_2026_10_28.py`, whose assertion that the poll
frame draws "REPLY WITH A OR B" now asserts that it draws no REPLY line.

## Not done here (outside this change's files)

- The Reel cover (`src/visual_engine.py`) does not yet call `typography.spoken_units`. It needs to, with the run's
  narration, for its eyebrow, lines and tagline.
- `_POLL_BODY_SLACK` in `script_engine.py` still budgets for the poll frame's option boxes, which are gone; the Story
  body there could take two more words.
