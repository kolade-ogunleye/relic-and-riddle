# Lane B cycle 3: the YouTube headline chain, before and after

Lander ruling (2026-11-19, under the operator's delegation): the YouTube thumbnail's headline is a ranked chain of
the run's signed lines (youtube_headline, thumbnail line1 + line2, the carousel cover heading, the tagline, the
eyebrow, the Story hook line). Each candidate passes the truth, figure and heading gates, reads as a headline and
complements the video title, or the next one is tried. The thumbnail is refused only when the whole chain fails. The
copy stage holds the design's standard 904 px text column.

`youtube_chain_before_after.jpg` and the six `pair*.jpg` files: six real dev runs, each thumbnail at 1280x720.
BEFORE is one copy (the old path) on the same tree; AFTER is the chain. The runs were picked across sources:
thumbnail, carousel cover x2, eyebrow, tagline x2. Every hero is the topic's own photo, and every line is the run's
signed copy.

Measured on dev (31 runs) and the production sample (34 runs, counts only, no images), each run's own hero:

| path | dev refused before / after | production refused before / after | source mix after (dev; production) |
|---|---|---|---|
| Reel copy as the YouTube headline | 15 / 0 | 18 / 0 | thumbnail 16, carousel 11, tagline 4; thumbnail 16, carousel 12, tagline 5, eyebrow 1 |
| documentary title (production's path) | 9 / 0 | 6 / 0 | thumbnail 14, carousel 7, tagline 7, eyebrow 3; thumbnail 23, carousel 5, tagline 3, eyebrow 3 |

Also on the documentary path, before: 19 of the 50 documentary thumbnails that drew carried the code's own label
"DOCUMENTARY" as their headline or kicker. After: 0. Code labels are never chain candidates or kickers.

Scripts are kept outside the tree, in the lane builder's scratchpad: chain_measure.py and make_pairs.py.
