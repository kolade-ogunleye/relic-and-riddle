# Platform safe-zone evidence, 2026-10-24 (rc39)

Frames of the latest development run (Richat Structure, run_richat_structure_sahara_eye_1792778206) with each
platform's own interface bands drawn over them: red for Meta (Instagram and Facebook Reels and Stories: top 14%,
bottom 35%, 6% each side), blue for YouTube Shorts (top 288 px, bottom 672, left 48, right 192: its icon column
and its title/channel/sound rows), green for the box every platform leaves free, the one the renderers now read
from `src/safe_zones.py`. "Before" is the run's own file; "after" the same content re-rendered with the current
code. Rendered by `scripts/render_safe_zone_evidence.py`; the numbers are in `measurements.txt`.

- captions-before.jpg / captions-after.jpg - the caption pill was centred on the 35% line, so every pill of the
  run (32 of 32) sat half inside Meta's caption and audio row (lowest edge y 1309 against the line at 1248).
  After: the pill's lower edge sits 8 px above the line (lowest 1241), its width unchanged.
- number-card-before.jpg / number-card-after.jpg - the figures ran to x 1000 under Shorts' icon column and the
  labels to y 1113, into the caption band; after, the text is 696 px wide centred (x 192-888) and ends at y 1058.
- timeline-after.jpg, scale-depth-after.jpg (sample data; this run had neither) - the two kinds whose text
  measured past the bounds: the timeline's first year rose into the top band and its last labels into the
  caption band; the scale's reference bar label reached x 920. Both inside now.
- story_01..03-before.jpg / -after.jpg - a Story's text block may now reach y 1248 at most (Meta's 35%),
  where the old rule allowed 1580; the kicker pill, title, body, options and pointer stay within x 65-1015.
- cover-grid-crops.jpg - unchanged: cover text was already inside the centre square (y 420-1500), which both
  the 3:4 profile grid and the older 1:1 crop keep.

Sources: Meta's Instagram Story ad guide (facebook.com/business/ads-guide/video/instagram-story, "leaving
roughly 14% of the top, 35% of the bottom, and 6% on each side"); Google Ads' vertical video guidance
(support.google.com/google-ads/answer/9128498), whose safe-zone figure measures 288/672/48/192 on 1080x1920,
with its caveat that overlays move by format and screen. Meta's figures are the published ones for ads; organic
Stories cover less of the bottom, so the 35% rule is the conservative choice and a one-number lever in the spec.
