# Brand overhaul, 29 Oct 2026

This is the implementation of the visual critic's round 4 list (`C:\tmp\visual_review\critic_round4\report.md`)
and the operator's decisions of 29 Oct. Every pair below is **before (HEAD d333bc6) | after**, half size, rendered
from the same code path, the same photos and the same copy. The topics are Richat (dev run 1793272675), Gol
Gumbaz (dev run 1793278143) and Derinkuyu (the curated script `data/scripts/derinkuyu_underground_city.json`; no
dev run exists for it). Graphics, captions and end cards are shown on the frame the viewer sees: the master chain
(`video_composer.film_grade_filter`, then noise and vignette), with the captions burned in after it. Nothing was
posted, and no paid or vision API was called; photo matching was fixed to each run's recorded photos.

Files: `story_<topic>_<n>.jpg`, `carousel_<topic>_<n>.jpg`, `numbers_<topic>.jpg`, `map_<topic>.jpg`,
`timeline_<topic>.jpg`, `captions_<topic>.jpg`, `end_<topic>.jpg`, `cover_<topic>.jpg` (47 pairs). The numbers
are in `measurements.json`. The scripts that made them are `render_evidence.py` (run once against a `git archive HEAD src` export and once against the working tree), `caption_style_test.py` and `build_evidence.py`.

## What changed

| Area | Change |
|---|---|
| Tokens | New `src/brand.py`: gold `#F2B630`, bone `#F3EDE2`, ink `#0B0E12`, ink2, stone, stone light, gold dim, teal, coral. It replaces `(255, 215, 0)` in multi_format, graphics, visual_engine and art_direction, and `(212, 175, 55)` in end_card. Captions use the same tokens (`video_composer`). |
| Data font | IBM Plex Mono 2.5.0 Medium and SemiBold, copied unmodified from the IBM/plex release. sha256 matches the critic's `sha256.txt`. OFL 1.1 is in `fonts/licenses/`, and both files are in `fonts/license_evidence.json` (so `font_rights` passes). It is registered as `typography.get_font("IBM Plex Mono", size, weight=500/600)` and is used for bracketed figures, scale bars and Story photo credits. |
| Breaks | `typography.glue`: a figure stays on the same line as its unit, a count word ("20,000 people", "104 million years") or a prefix ("c. 780"). `balanced_wrap`: lines keep the greedy wrap's count, and the widest line is made as narrow as possible. Both are used in every `fit_text` / `fit_lines` fit. |
| Contrast | `typography.place_text` / `plan_text` measure each light text element on the composed pixels. The measure is glyph interior median against the 90th percentile of the ring 2-10 px outside the glyphs. A glyph-shaped ink halo is raised in 8% steps until the ratio reaches 4.8:1 (so JPEG and the grade still leave at least 4.5). Each ratio is recorded on its element in `still_layout.json`, re-measured on the saved JPEG, and in `<clip>.contrast.json` beside each graphic (re-measured on the graded last frame). |
| Stories | Left-aligned column at x 84. Kicker: a gold bar and tracked gold Montserrat 800. Two-tone Anton title (bone, then gold, one size up to 150 px). Montserrat body at 46 px (was 40) in balanced lines. The 35% full-frame shade and fixed vignette are replaced by an adaptive gradient behind the block. Photo credits are kept, now in Plex Mono, left-aligned, on a soft band (no box), above the sticker room. |
| Carousel | Every slide's stack is measured, then bottom-aligned 18 design px (24 px at 1440) above one shared line inside the 1:1 grid crop. Slides alternate between full-bleed photo and photo over ink. The figure slide sets its number in gold Anton with the unit beside it in bone. The brand is Cinzel in tracked bone with a halo, never a pill. Badge pills are gone; "YOUR TAKE" is a kicker. The closing slide has a two-tone heading, the question, a short gold rule and the share-and-follow line. **"TELL US IN THE COMMENTS" is removed** from the slide and its alt text. |
| Number card | Left-aligned over the photo itself (pushing in), with an adaptive gradient behind it. Gold Anton digits sit on fixed cells, right-aligned, so they roll in place. The unit is beside them in bone, and the pair is fitted inside `graphics_box()`. A prefix ("ABOUT", "EST.") is a kicker. A bracketed figure is set in Plex Mono under the label and counts with the main figure. |
| Map | Lighter land (88, 92, 100; home 150, 122, 72). The place label is knockout type on an ink halo (at least 80%), with no pill; a long name takes two lines. There is a scale bar in the narration's length unit (`typography.spoken_system`; km when none is spoken) once the camera rests. `map_label_plan` (the transient neighbour names) is unchanged. |
| Timeline (9:16) | Ink ground and a gold-dim spine. Two events get 180 px years and two-line labels. More than two get 104 px years and one shared label size: labels that fit one line take one, and a longer one takes two at that size. A lift/shrink step keeps the last row at or above `graphics_box()[3]`. |
| End card | `plain_v4`: **no capsule** (no outline, ground or sheen). Bone share ask on two lines where it fits at 54-62 px, else three. FOLLOW FOR THE NEXT RELIC is one line of tracked gold Montserrat 800 that lands with the bell. Each line's contrast is measured on the last frame (`stats["contrast"]`). |
| Captions | Kept as pills (measured below). Spoken words are bone, key words brand gold, and upcoming words stone light (the old grey failed 4.5:1). |
| video_qa | `still_contrast_problems`, `graphic_contrast_problems`, and `caption_contrast`, which measures caption glyphs on the master: frames at caption times, with masks from the caption file redrawn as plain fills. These feed `text_contrast_audit` and are wired into `certify` as `atomic_breakdown["text_contrast"]`. |

## Caption style decision (measured)

Each topic's caption phrase was rendered by `build_subtitles` and burned onto two graded grounds: the beat
photo and the brightest photo of the topic. It was rendered in brand colours in both styles: today's pill,
and the critic's stroke-and-halo (7 px ink outline plus a blurred ink halo layer at 65%). Every colour class
was measured with the ring method at full size, and at phone size (frame and masks at 360 px wide, ring 1-4 px).
Other words' glyphs were kept out of each ring.

| Minimum ratio (full / phone) | pill | stroke-and-halo |
|---|---|---|
| Richat, beat | **5.30 / 5.31** | 2.54 / 2.39 |
| Richat, bright desert | **5.27 / 5.27** | 2.18 / 2.02 |
| Gol Gumbaz, beat | 6.71 / 6.72 | 6.89 / 6.69 |
| Gol Gumbaz, bright | **5.71 / 5.87** | 4.31 / 4.46 |
| Derinkuyu, beat | **5.77 / 5.78** | 3.88 / 3.70 |
| Derinkuyu, bright | **6.32 / 6.33** | 5.06 / 4.86 |
| Spoken bone, worst | 9.12 | 4.07 |
| Key gold, worst | 6.01 | 2.56 |
| Caption band covered | 14-28% | 13-33% |

**Decision: keep the pill.** It holds every class above 4.5:1 on every ground at both sizes. Stroke-and-halo
drops to 2.0:1 (upcoming words) and 2.6:1 (gold) on the bright Richat desert. It also covers no less of the
picture, because the halo spreads past the glyphs. With the old grey upcoming colour (#909090), the pill itself
measured 3.70:1, so upcoming words are now stone light. Those runs are kept in measurements.json as
`captions_pill_vs_stroke_grey909090`. The new `video_qa.caption_contrast` measured 4.82:1 at its lowest on the
existing Richat dev master, which still has the old colours.

## Measurements (after)

| Surface | Lowest ratio on delivered pixels | Elements | Layout problems |
|---|---|---|---|
| Stories (9 frames) | 4.80 | kicker, titles, body, pointer, photo credit | 0 |
| Carousel (21 slides) | 4.77 | brand, kicker, stat, heading, body, share | 0 |
| Number cards (3) | 6.54 graded | prefix, figure (per digit), unit, label, secondary | right edge 868 of 888 at most, bottom 992 of 1058 |
| Maps (3) | 5.35 graded | place, country, scale | bottom 1049 of 1058 at most |
| Timelines (2) | 8.80 graded | years, labels | bottom 1058 of 1058 at most |
| End cards (3) | 8.56 (brand) / 15.1 (share) / 9.93 (follow) | | follow line inside the Reel's centred width |
| Captions | 5.27 (pill, graded, worst ground) | | pills inside the Reel box (unchanged geometry) |

Scale bars: 250 MI in all three topics, because all three narrations speak imperial lengths first. The bar is
217.5-218.1 px at 60 px per degree of latitude.

## Gate checks

- **No slide chrome or fake UI:** no pills, badges, counters, arrows or capsules. The IG counter region (1067-1440 x 0-187 at 1440) is empty on all 21 slides (still_layout `protected`, 0 problems).
- **Poll sticker room:** y 1300-1560 is empty on the three poll frames (`protected`). The Story credit's lowest box is at y 1236, inside the safe box (bottom 1248) and above the room.
- **Safe zones:** every Story box is inside the safe box (65, 269, 1015, 1248). Graphics text is above y 1058 and left of x 888 (Shorts' rail). End-card text is inside the Reel's centred width.
- **Engagement bait:** `script_engine.engagement_bait` was run on all rendered copy (Story fields, slide headings and bodies, share lines, pointer, follow line, kickers). Nothing was flagged.
- **Share-and-follow line:** present on the three end cards and the three closing slides.
- **Figures:** every figure is drawn as the script gives it, in the narration's unit (`spoken_units`). Count-ups end on the written value, emoji are dropped (`drawable`), and a figure and its unit stay together.
- **Story credits:** they are kept, and every credited frame records its lines, box and measured ratio in layout.json.

## Not done here (outside this change's files)

- The Story evidence plate (a ring sized to the subject) needs a subject box from the vision review. No photo record has one (`image_review`), so it was not built.
- Derinkuyu's curated closing card body says "Tell us in the comments and follow for more history." That is the script's copy, not the template's, so it needs a script or curated-script change.
- The covers changed only in their gold. The cover redesign (critic round 5) and the hue-keeping still grade belong to the still-grade work.
