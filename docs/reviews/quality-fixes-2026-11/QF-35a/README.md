# QF-35a: role tokens, one brand configuration read at call time

Design-freeze ruling Q11 (decisions FREEZE-RESUME, 2026-11-10): before the freeze resumes, the palette, the masthead
text and the font families of the frozen designs come from one brand configuration object (default = today's
values, pixel-identical), so a second-palette render is possible. Full QF-35 (BrandProfile / niche config) comes
after; nothing here loads a brand from settings or adds a niche.

## What changed

- `src/brand.py`: `BrandConfig` (palette roles, font roles, text roles), `DEFAULT_BRAND` holding today's values,
  `active()`, `configure(cfg)`, `use(cfg)` (process-wide, so the graphics' frame threads see it) and `on_change`.
  `brand.GOLD`, `brand.DISPLAY_FONT`, `brand.MASTHEAD` are served by the module `__getattr__` from the active
  configuration on every read. Palette roles are the nine tokens plus the 29 design roles that were literals in the
  design modules (map land, home, sea gradient, globe, scale line, timeline label, earth, rock, plate, glow ink, the
  accent's backdrop and fallback, ...), each with its value unchanged; a role may alias another (SEA is INK,
  GLOBE_LIMB is GOLD, MAP_NAME is STONE_LT), so a palette that moves INK moves the sea. Font roles: display (Anton),
  masthead (Cinzel), text (Montserrat), data (IBM Plex Mono), caption and caption_wide (config.FONT_NAME and
  config.CAPTION_WIDE_FONT, read when used). Text roles: the masthead, the end card's follow line and the 16:9 hand-off.
- The frozen design modules (graphics, design_cards, multi_format, cover_design, youtube_thumbnail, end_card,
  then_now, callouts, video_composer's captions, art_direction's accent) read every role as `brand.<ROLE>` inside the
  function that draws: no `from .brand import GOLD`, no module-level alias (`WHITE = brand.BONE`), no default
  argument (`fill=brand.GOLD` became `fill=None`, resolved in the body), no module-level table of families
  (`_ROLE_FACE` holds role names), no derived colour constant (STRIKE_BACK, SOURCE_FILL, ... are functions).
- The names those modules exported before (`graphics.WHITE`, `end_card.FOLLOW`, `video_composer.COL_EMPHASIS`,
  `cover_design.BRAND_TEXT`, `art_direction.DEFAULT_PALETTE`, ...) are still importable: each module's `__getattr__`
  reads them from the active brand, so a reader never holds a stale copy. No test expected value changed.
- Caches that hold a measure taken in a brand face but are keyed without the family (design_cards `_cap`,
  then_now `year_cap`, video_composer's caption line measures) are cleared through `brand.on_change` when the
  brand changes. `typography.get_font` is keyed by family already.
- The 9:16 caption's small-capital guard compares the caption family's file with the derived face's source
  (font_assets.DERIVED_FAMILIES) instead of the literal "Anton".

What stays a literal (tests/test_qf35a_role_tokens.py `ALLOW`, 30 entries keyed module / function or constant /
literal, each with its reason, and a test that fails on a stale entry): 23 size, step and geometry tuples that happen
to be three or four small integers; the callout verification crop's grey pad and red crosshair and ring
(model-facing, never on a delivered frame); the caption contrast record's white and black worst-case grounds
(analysis); the 9:16 caption's round-2 reference width measure in Anton (an operator-decided measure definition; no
renderer draws it). Pure black (shades, shadows, empty layers) is not a brand colour and is not flagged.

## Measurements

`python measure.py <repo root>` (committed inputs only): the scan, every committed-input surface hashed with the
default brand, and the same surfaces in the test brand. Round 1 ran it on 8ae083c8 and 15c5d178 (the table below,
in git history at 15c5d178); since the port `before.json` is this script on 1599d93b and `after.json` on the port
(the port section at the end).

| | before (8ae083c8) | after |
|---|---|---|
| colour literals on the frozen surfaces outside the configuration | 23 | 0 |
| font-family literals | 225 | 0 |
| brand-string literals (masthead, follow, hand-off) | 7 | 0 |
| role reads bound at import (module-level copies, `from .brand import`, default arguments) | 40 | 0 |
| committed-input surfaces, default brand (8 caption files, 4 end-card / bridge sequences, 10 card sequences, 2 cross-sections; 1,978 frames) | 24 hashed | 24 identical |
| the same surfaces in the test brand | impossible (no configuration) | 18 of 24 drawn, each different from the default; 6 cards refuse for fit and draw in its colours with today's display face kept |
| caption files holding one of today's four caption colours, test brand | | 0 of 8 |
| end-card and cross-section pixels within 6 of today's gold, test brand | | 0 |
| card elements filled in today's gold, test brand | | 0 |
| cards whose display face is the test brand's (Montserrat where Anton was) | | 4 of 4 drawn |

## Pixel identity on real runs (`render_surfaces.py`, `compare.py`)

`python render_surfaces.py <tree> <dev checkout> <out.json> [--brand second] [--keep <dir>]` draws every frozen
surface from real development runs (maps, number cards, timelines, scales and the route from the runs' recorded
graphic inputs; the freeze-panel catalogue's then-and-now pairs, end cards and loop bridges, covers, carousel /
gallery / Story recipes and caption narrations; the card tests' fields on real heroes) and hashes every frame where
it is encoded. `python compare.py base.json head.json [second.json]`. The development runs are gitignored, so these
three scripts take the development checkout as an argument; `real-runs-base.json`, `real-runs-head.json` and
`real-runs-compare.json` are their committed results (base 8ae083c8, head this row's tree; `src_sha256` in each
file is the hash of that tree's `src/*.py`).

**194 surfaces, 16,209 frames: 194 identical, 0 differing, none on one side only, no error on either tree.**

| surface | compared | from |
|---|---|---|
| maps (9:16, and 16:9 for the first three runs) | 19 | 16 runs' recorded place and narration |
| number cards | 15 | 12 runs' stat cards on their own photograph |
| timelines | 9 | 6 runs |
| scale comparisons | 4 | 2 runs |
| route | 2 | the one run with a route (second stop new, labelled) |
| cross-section | 2 | the test spec (no run carries one) |
| then-and-now | 4 | the catalogue's two accepted pairs, both formats |
| loop bridge, with and without FOLLOW | 10 | 5 real endings |
| 9:16 end card, 16:9 end screen | 9 | 9 real endings |
| Reel cover, grid tile, YouTube thumbnail | 15 | 5 heroes |
| carousel, Facebook gallery and Story frames | 68 | 4 runs' kept recipes |
| hook, chapter and myth cards | 27 | the card tests' fields on three real heroes (26 drawn, 1 refused alike on both trees) |
| photo callouts | 1 | the reference pair on a real hero |
| captions | 9 | 5 real narrations as caption files (4 at 9:16, 1 at 16:9; the file carries every colour, face and position libass draws, with the face embedded) and one libass frame of each 9:16 file |

The quote card is held (config.QUOTE_CARDS_ENABLED is false and the renderer refuses, so no run and no committed card
field carries one). `python quote_card.py <tree>` draws it from the design tests' quote field (a test fixture, not
a run's words) on the committed Richat still with the hold lifted in that process only: `quote-card-base.json`
and `quote-card-head.json` hold the same two hashes (9:16 and 16:9, 180 frames each, 360 frames identical), and
the test brand draws both, different from the default, with no element in today's gold.

The grounds include the catalogue's hard ones: the unfinished obelisk's bright quarry (cards, end card, bridge), the
Wieliczka and Odessa dark interiors and the Nazca aerial under the end card and the bridge. The hook card on the
960 px Richat still is refused on both trees ("the photo would be enlarged 3.08x"), recorded as a refusal in both
files and compared as one.

## The second brand

The test brand (tests/test_qf35a_role_tokens.py `_second_brand`): every palette colour with red and blue swapped,
Montserrat in the display role, "SECOND TEST BRAND" as the masthead and other end-card lines. It is passed to
`brand.configure` / `brand.use`; no design module is edited.

Committed inputs (measure.py, `after.json`): 18 of 24 surfaces draw and differ from the default; the eight caption
files carry none of today's four caption colours; the end card's masthead reads SECOND TEST BRAND; no pixel within 6
of today's gold on the end card, the bridge or the cross-section; the chapter card (act 5) and the short myth card
draw their titles in Montserrat at both formats, with no Anton element. The six cards with the card tests' longest
fields (two long chapter titles, the long Richat myth) refuse in Montserrat, which is wider than the condensed
Anton ("the title does not fit two lines at the floor size"): a card fails closed and is never cut. With the same
palette and today's display face kept they draw, with no element in today's gold.

Real runs (`render_surfaces.py --brand second`, `real-runs-second.json`): 194 surfaces, no error: 167 draw, every one different from the
default (none identical); the 27 hook, chapter and myth cards refuse (26 for fit: the card tests' fields are their
longest and Montserrat is wider than Anton; 1, the hook on the 960 px Richat still, for its 3.08x enlargement, as in
the default). With the test brand's palette, masthead and lines and today's display face (`--brand second-colours`,
`real-runs-second-colours.json`) 26 of the 27 cards draw, each different from the default. The photo callouts draw
on the Amber Room hero in both brands. The run was made in two parts on the same tree (the first session ended
between two groups; the second part ran `--only social,cards,callouts,captions`); `real-runs-second.json` joins them
(`parts`, one `src_sha256`), and `real-runs-compare.json` holds the comparison with the default.

Today's colours left on the test brand's frames (`old_colours.py`, exact pixel counts on the last frame of each
surface, `real-runs-old-colours.json`): 162 last frames of the test brand against the same
surfaces' default frames. Mark roles (17 roles with a channel of 64 or more: text, figures, lines, land tints):
14,949,283 px are exactly one of today's colours in the default frames and 3,475 in the test brand's frames, of which
555 px are more than the default frame of the same surface holds (`excess`), every one on a frame over a photograph
(carousel, gallery and Story frames 429 px, then-and-now 64, loop bridges 5, in the per-role lists; the largest is
HOME, a desert brown, 232 px on two Richat carousel slides), against 10,159,354 px in the test brand's own colours.
Today's GOLD: 5,739,356 px in the default frames, 0 in the test brand's; BONE 8,049,488 and 27 (excess 0); GOLD_DIM
85,764 and 7 (excess 0). The ground roles (near-blacks, which a dark photograph or a gradient passes through in any
brand) are listed in the file and not judged. The 26 cards drawn with today's display face kept
(`real-runs-old-colours-cards.json`): marks 3,921,964 px in the default frames, 10 in the test brand's, excess 0.

Boards (`boards/`, half size, default left, test brand right; real run inputs): map (Amber Room run), number card (unfinished obelisk run),
16:9 timeline (Hal Saflieni run), scale (Wieliczka run), then-and-now (Roman concrete pair), end card on the
obelisk's bright quarry, 16:9 end screen on the Wieliczka interior, Reel cover (Richat), YouTube thumbnail
(Wieliczka), carousel slide 1 (Gol Gumbaz run), Story frame 2 (Nazca run), a libass caption frame (Amber Room
narration), the photo callouts, and two cards in the test brand's colours with today's display face (chapter 4 of 5
on the obelisk hero, 9:16; the Richat myth on the Amber Room hero, 16:9). 15 boards, the largest 265 KB.

## Runtime

A role read is a module `__getattr__` and a dictionary lookup (1 to 3 microseconds on this machine under load,
against 0.05 for a module constant). On the renders:

| | base | this row |
|---|---|---|
| 194 real-run surfaces, 16,209 frames (the two trees drawn side by side, busy machine) | 4,817 s | 4,874 s (+1.2%) |
| committed-input surfaces, default brand (measure.py, the two trees one after the other, busy machine) | 835 s | 887 s |
| end card, 9:16, 87 frames (timing.py: the two trees in turn, twice; best) | 71.8 s | 62.9 s |
| cross-section, 9:16, 180 frames | 60.8 s | 62.7 s |
| chapter card, 9:16, 75 frames | 8.08 s | 7.95 s |
| one 9:16 caption file | 0.30 s | 0.33 s |

`timing.json` holds the four runs. The machine was busy: the base tree's own two runs of the cross-section took
60.8 and 83.2 s, so the differences above are inside the spread between two runs of one tree; no surface is
measurably slower.

## Limits (not built here; QF-35)

- One brand per process: `configure` is process-wide (the graphics draw frames on threads), so two brands cannot
  render at once in one process.
- A brand is not part of a stage checkpoint's fingerprint (the graphics' fingerprint holds brand.py's bytes, not the
  active configuration): two brands must not share one run folder's stage cache.
- A display face wider than Anton makes the cards with long checked fields refuse (above); the cards' fit is the
  design's, not the brand's.
- The 9:16 small capitals exist for the Anton-derived face only (a brand naming another 9:16 caption face is
  refused, as before). (Round 1 also listed the 16:9 caption face and the badge's face here; the port closes both,
  below.)
- The stack reads typography.ring_contrast's TypeError (glyphs too thin for an eroded interior, no fill) as its
  thin-text signal in several places (graphics `_plan_thin`, `_ThinPlate`, the base-name and water plates, `_plan`);
  the port routes the timeline spans through that same handling rather than changing the signal. Replacing the
  exception-as-signal with an explicit return is a follow-up row (the lander's), not this commit.
- Outside the frozen surfaces, unchanged: visual_engine's depth-layered thumbnail (no caller in src) still copies
  GOLD and BONE at import (the only module-level copy of a palette role left in src); the operator-facing graphics
  (Telegram cards, the audience chart) are not touched.

## Regression test

`tests/test_qf35a_role_tokens.py` fails on 8ae083c8 by assertion (the scan finds the 255 literals and 40
import-time reads; the brand has no configuration object) and passes here. It also holds: the default brand is
today's values; the old module names follow the active brand; a brand configured after the composer was imported
and had written a caption file reaches the next caption file, 9:16 and 16:9 (none of today's colours left), and the
default returns; a frame thread reads the swapped brand; caches of measures taken in a brand face are dropped.

## Port onto LAND-29 (base 1599d93b: QF-24, QF-25, QF-87 on UI-01g)

The round-1 commit 15c5d178 sat on the pre-port QF-87 8ae083c8; this is the same row replayed onto the LAND-29 port-4
stack tip 1599d93b, with the round-1 review's two coverage gaps closed. The round-1 files above (real-runs-*.json,
quote-card-*.json, timing.json, boards/) are kept as the round-1 record for 8ae083c8; the port's own results are the
`port-*` files, and before.json / after.json are measure.py run again on 1599d93b and on the port.

Resolved by hand (both sides' behaviour kept, every frozen value read from the brand at call time):
- `src/art_direction.py` (2 hunks): the stack's QF-88 accent selection (clear / near lists, `accent_problem`) kept,
  its fallback read through `_default_palette()` and the gold through `brand.GOLD`.
- `src/graphics.py` (7 hunks): the globe's neutral ground (QF-20) and the relief map's neutral fill and dashed
  borders read two new palette roles, NEUTRAL_LAND (57, 61, 64) and NEUTRAL_EDGE (154, 160, 164); the home edge
  ring keeps the stack's `neutral_alpha` with `brand.GOLD_LIGHT`; the country name's size steps, the neighbour-name
  attempts with their compact options, the stack's `_year_px` year size and the floor rows of `_probe_tracked` keep
  the stack's code with `brand.TEXT_FONT` / `brand.DISPLAY_FONT`; one more country-name literal outside the hunks.
- Modules the stack added: `src/timeline_dense.py` (the five-event date column) reads its faces from the brand (7
  literals), its two plan caches are dropped on a brand change, and it is in the scan's FROZEN list (no allow-list
  entry added); `src/colour_vision.py` reads its meaning roles (gold, teal, coral and the map tints) from the brand
  when it checks, so a second brand is checked in its own colours.

The review's gaps:
- The 16:9 caption face libass draws is the caption_wide role's (`video_composer._wide_caption_family`), the face
  the plate is measured in: a role whose bundled file is a variable source draws its derived static 700 instance
  (Montserrat -> RR Sans, today's, unchanged bytes; Cinzel -> RR Serif, a new derived face in
  font_assets.DERIVED_FAMILIES, OFL, no reserved name), a static family draws as itself, and any other is refused
  (ValueError), never drawn in another face.
- The AI-reconstruction badge's face is a brand font role, `badge`, an alias of `data` by default (config.RECON_FONT
  is gone; `video_composer.RECON_FONT` still reads it), so a brand that changes the data face moves the badge.

Found by the third brand's real-run render, fixed here: a timeline span (9:16 tall timeline and the dense timeline)
in a STONE light enough to count as light text, at a size whose glyphs leave typography.ring_contrast no eroded
interior, made plan_text raise TypeError and stopped the graphic (today's STONE is dark text, measured on its fill,
so the default never met it). The spans now go through `graphics._plan_thin` (which gains `track` and plan_text's
own halo default), the stack's existing handling of a face too thin to read its own colour (the map's neighbour
names): it measures such a span against its drawn colour; a span plan_text can read is planned exactly as before.
A first attempt that changed ring_contrast itself moved 12 default maps (the stack reads that TypeError as its
thin-face signal) and was dropped.

Measured on the port:

| | 1599d93b | port |
|---|---|---|
| colour / font-family / brand-string literals on the frozen surfaces (11 modules) | 27 / 233 / 7 | 0 / 0 / 0 |
| role reads bound at import | 40 | 0 |
| real-run surfaces, default brand (`port-real-runs-compare.json`) | 207 drawn | 207 identical, 0 differing, none on one side only, no error (16,575 frames) |
| committed-input surfaces, default brand (before.json / after.json) | 24 hashed | 24 identical (1,978 frames) |
| render seconds, 207 surfaces (busy machine; the port's run is the final tree's, alone) | 6,952 | 5,142 |

The real-run set is round 1's 194 surfaces plus 13: a libass frame of the 16:9 caption, each caption case again with
the reconstruction badge on screen (ASS file and libass frame, both formats), and the dense timeline (no run
recorded five events: the stack's committed five-event test events on the first timeline run's photograph, both
formats, labelled). No surface differs, so the base was not rendered twice. The port was rendered three times: the
first port tree (207 identical), a tree with the first thin-span fix inside ring_contrast (12 maps differed, the cause
above) and the final tree (`port-real-runs-head.json`, src_sha256 3c14988160761c6c: 207 identical, 0 differing).

Configured after import (`probe_after_import.py`, `port-probe-after-import.json`): every frozen module imported
and drawn once in the default brand, then `brand.configure(third)`: 12 of 12 checks hold (end card, its masthead,
captions with none of today's colours in both formats, 16:9 captions in RR Serif embedded, the badge in Cinzel,
the dense timeline's years in Cinzel, the fallback accent, the colour-vision gold, the card measure), and
`configure(None)` gives the first draw back exactly. The third brand (`render_surfaces.py third_brand`) moves every
channel of every palette colour, greys included, and names Cinzel in the display, data and 16:9 caption roles.

The third brand on the real runs (`render_surfaces.py --brand third`, `port-real-runs-third.json`, the final tree;
drawn in three processes on disjoint groups, `parts`, one src_sha256, and the one number card two parts drew hashes
the same in both): 207 surfaces, 0 errors; 180 drawn, every one different from the default (none identical,
`port-real-runs-compare.json`); the 27 hook, chapter and myth cards refuse for fit in Cinzel, wider than Anton (26),
and the hook on the 960 px Richat still for its enlargement (1, as in the default). Captions at 16:9 draw in RR Serif
(Cinzel 700) on plates measured in Cinzel, and the reconstruction badge in Cinzel, in both formats.

Today's colours left on the third brand's frames (`old_colours.py ... third`, `port-real-runs-old-colours-third.json`,
exact pixel counts on the last frame of 170 surfaces drawn in both brands, every palette colour counted, greys
included): mark roles (20) 15,319,867 px in the default frames, 4,207 in the third brand's, of which 460 are more
than the same surface's default frame holds, every one on a frame over a photograph (carousel, gallery and Story
slides, then-and-now, one YouTube cover; the largest GLOBE_LAND, 268 px on Nazca carousel slides); against
10,098,337 px in the third brand's own colours. Today's GOLD: 5,806,818 px in the default frames, 0 in the third
brand's. The ground roles (near-blacks) are listed in the file and not judged, as in round 1.

Boards (`boards/port-third/`, half size, default left, third brand right, the final tree): the 16:9 caption with the
badge (Richat narration), the 9:16 caption with the badge (Amber Room), the dense timeline, a 9:16 timeline (Hal
Saflieni), a map (Amber Room), the end card on the obelisk's quarry, the Reel cover (Richat) and a then-and-now pair.
The 16:9 board also shows the default badge's face: libass finds no bundled face named "IBM Plex Mono" (the files'
names are "IBM Plex Mono SmBld" / "Medm") and draws a system face, which overruns its plate. That is the stack's
(1599d93b's) and the default is kept pixel-identical here; the lander logs it as its own row.

## Round 2 (the port's adversary review)

Two findings on e574f859, both fixed here:
- **Values measured from a role at import.** then_now's `KICKER_PX` (the 16:9 kicker size whose THEN cap reaches 32
  px in the data face) and `BAND_FALL` (the glow's fall, from the display face's year cap) were computed when the
  module was imported. A brand configured afterwards kept the first brand's measure (the third brand's 16:9 kicker
  stayed 46 px where a fresh import under it gives 44), and a module imported under another brand kept that brand's
  after `configure(None)`. Both are now measured when drawn (`then_now.kicker_px()`, `band_fall`, the kicker measure
  cached and dropped on a brand change); the old names read through the module's `__getattr__`. A search of every
  frozen module's module-level values, imported fresh under the default and under a brand that moves every colour and
  face, found no other (the 9:16 caption's REEL_CAP_H is measured from the caption face, which only the Anton-derived
  face can fill while true small capitals are on). The test now imports every frozen module in fresh processes under
  the default, under that brand, and under that brand with the default configured again, and requires the same
  module-level values, and the brand-measured names to follow a brand configured after the import.
- **The badge drawn heavier than measured.** The badge style asks bold (700) and its plate is measured at 600; a
  variable badge face (Cinzel, Montserrat) reached libass as its file's default 400 instance, thickened. The badge
  now draws in the face it is measured in (`video_composer._badge_family`): a variable role draws its derived static
  600 instance (RR Serif SemiBold, RR Sans SemiBold, new in font_assets, embedded when the badge is on screen), a
  static face draws as itself, anything else is refused; compose_video checks the badge face as it checks the caption
  face (no face within 150 of 700 is refused). The default badge face IBM Plex Mono is unchanged: libass finds no
  bundled face of that name (1599d93b's, logged as its own row), so for a static face only the weight is checked here.

Round 2 measured on the round-2 tree (src_sha256 3d8a8648d31dca43): the default re-render of every surface the two
fixes reach (then-and-now, the caption files and libass frames with and without the badge) plus 20 jobs drawn at
random from the rest (`port-round2-jobs-default.txt`, seed 35202): 66 surfaces, 3,900 frames, all identical to
1599d93b, 0 errors (`port-round2-default.json`, `port-round2-rerender.json`). The third brand's then-and-now and
caption surfaces again (`port-round2-third.json`): 24 surfaces, 0 errors; with the untouched rest
(`port-real-runs-third.json`) 207 surfaces, 0 errors, 180 drawn, all different from the default. The probe
(`port-probe-after-import.json`) adds the kicker and the glow fall (44 px and 28 px after configure, as a fresh
import under the third brand gives; 46 and 38 in the default) and the badge face: 13 of 13 checks hold.

Round 3 (the port's second adversary review): compose_video checked the badge face even when no reconstruction was
on screen, so a brand whose data face is too light for the badge (Anton, 400) could not compose a photo-only video
that draws no badge. The check now runs only when the badge is drawn (the reconstruction intervals that make
build_subtitles draw and embed it), and build_subtitles names the role's face in the unused badge style when there
is none. The review's probe (photo-only composition, data face default / Anton / Cinzel / Montserrat): all four pass
the font checks with no badge event; a composition with a badge still refuses Anton (test). Re-rendered on the
round-3 tree (`port-round3.json`): the 20 caption surfaces, default brand identical to 1599d93b, third brand
identical to round 2, 0 errors.
