# QF-21: fontTools + uharfbuzz + OpenType features

Review finding (project-quality-review-2026-11-05, section 3 "Visual design"; section 9 QF-87 for the features):
tracked text was drawn a letter at a time, each letter at its own advance, so every tracked line (kickers,
eyebrows, country names, timeline labels, the masthead, the end card's follow line, callout labels) lost the
font's kerning, while the width the layouts fitted with was the kerned one; no OpenType feature was used
anywhere; the caption measure summed single letters (the "unkerned-measure workaround"); the font tables were
read by a hand TrueType parser and glyph coverage by comparing masks with the missing-glyph box.

## What changed

- `src/font_engine.py` (new). fontTools reads each bundled file's cmap coverage, GSUB/GPOS features, variation axes,
  Windows vertical metrics and the names libass matches (`inspect_font`, `missing_glyphs`). uharfbuzz shapes a run
  once (`shape`), with the face's kerning, the requested features and the tracking added between clusters (a
  combining mark stays on its letter); `draw_run` draws exactly those glyphs at those positions through Pillow
  (raqm). A glyph without a Unicode mapping (a small capital, a case form) is drawn through an in-memory glyph-id
  cmap; the font files are unchanged. A stroked tracked run strokes every glyph first and fills over it, as Pillow
  draws a stroked run. The API takes the face per call, so QF-94's per-field fallback face passes its own face.
- Features. `typography.get_font` returns a `FeaturedFont`: every measure, mask and `ImageDraw.text` call made
  without explicit features uses `font_engine.default_features(text)`: `kern`, plus `case` when the run has no
  lower-case letter. **Builder decision (recorded here):** the review asks for `["kern","case"]` by default; the
  OpenType registry defines `case` for "all-capital sequences or sets of lining figures", so a mixed-case run
  ("Saint-Malo (AD 700)") keeps its default forms and an all-caps run ("SAINT-MALO (AD 700) · X") gets the raised
  hyphen, brackets and middle dot. Callers may pass `features=`: `SMALL_CAPS` (true `smcp`, accents kept),
  `FIGURE_FEATURES` (`tnum`/`lnum`), through `_draw_tracked`, `tracked_width`, `plan_text`, `place_text`; a plate
  keeps its resolved features for its mask, shadow, pixels and stepped-back twin (`graphics._twin`).
- One measure. `typography.tracked_width` is the shaped run; the private copies that summed single letters
  (`callouts._tracked`, `end_card._tracked_width`, `design_cards._tracked_width`, `graphics._tracked_w`) now call it,
  so what a layout fits is what it draws. `end_card._tracked`, an unused per-letter draw, is removed.
- Captions (operator decision 2026-11-05: measure without kerning, libass draws the captions unkerned).
  `video_composer.unkerned_length` shapes the whole run with the features libass draws
  (`font_engine.LIBASS_FEATURES`: no `kern`, no `liga`/`clig`, never `case`) instead of summing single letters. Round
  2: libass's own pixels show it draws no fi/fl ligature even at the 16:9 style's Spacing 0 (an fi run and an fl run
  draw the same width, where Montserrat's ligatures would make them differ by about 5 px), so ligatures are off at
  every Spacing (round 1 kept them at Spacing 0 and moved two 16:9 plates by 1 px, away from libass's ink).
  `_font_vmetrics` and `_libass_face` read the tables through fontTools instead of the hand parser;
  `typography._has_glyph` reads the cmap instead of comparing masks.
- Dependencies: `fonttools==4.67.0`, `uharfbuzz==0.57.0` (operator-approved, HANDOFF section 7; installed from
  hash-verified wheels, JOB-QF-21-dependency-window) in pyproject, requirements.txt and uv.lock (`uv lock --check`).

Not in this row: caption small capitals are still the `\fscx82` scaled capitals of `format_word_caption` (faux caps).
The operator's ruling (QF-21/87: true `smcp` or a derived font) is QF-87's caption work: libass takes no feature tags,
so the caption needs a derived face built with fontTools (QF-87 WIP `font_assets`), plus the approved main-test patch
v2 (decisions/QF-24-QF-87-layout.md). QF-21 supplies what that build uses: real `smcp` shaping, coverage and the
feature inventory (Anton `smcp`/`c2sc`/`case`/`cpsp` measured below). Map neighbour names are still spaced with
space characters (`" ".join(name)`, graphics.py relief_base and the neighbour labeller): no letter pair touches, so
there is no kerning to lose; turning them into tracked runs changes the map label geometry and belongs to QF-20's
map port.

## Measurements (`python measure.py <repo root>`; before.json on base 1ab9aded, measurements.json on this commit)

Tracked copy: the 392 unique strings real runs drew (real_strings.json, collected by extract_real_strings.py from
output/run_*: cover eyebrows and taglines, Story kickers, carousel headings, timeline, stat and callout labels, the
scale subject, route stops, map country names, chapter act titles) plus the masthead and the end card's follow line,
each in the font, size and tracking its design sets it in. Reference: Pillow's raqm layout of the whole run, each
letter at its kerned place plus the tracking (kern; case on a run without lower case).

| Design | Lines | Pixel-identical to the kerned reference | Lines drawn wider than kerned | Worst excess |
|---|---:|---:|---:|---:|
| cover eyebrow/tagline | 45 | 5 -> 45 | 39 -> 0 | 10 px -> 0 |
| Story kicker | 66 | 15 -> 66 | 39 -> 0 | 4 -> 0 |
| carousel heading (untracked) | 156 | 152 -> 156 | 0 -> 0 | 0 -> 0 |
| timeline label | 25 | 0 -> 25 | 24 -> 0 | 8 -> 0 |
| stat card label (untracked) | 29 | 29 -> 29 | 0 -> 0 | 0 -> 0 |
| map country name | 28 | 8 -> 28 | 19 -> 0 | 5 -> 0 |
| route stop | 2 | 0 -> 2 | 2 -> 0 | 4 -> 0 |
| callout label | 33 | 10 -> 33 | 20 -> 0 | 5 -> 0 |
| chapter act title (untracked) | 5 | 5 -> 5 | 0 -> 0 | 0 -> 0 |
| scale subject | 3 | 1 -> 3 | 2 -> 0 | 4 -> 0 |
| masthead RELIC & RIDDLE | 1 | 0 -> 1 | 1 -> 0 | 6 -> 0 |
| end card follow line | 1 | 0 -> 1 | 1 -> 0 | 3 -> 0 |
| **all** | **394** | **225 -> 394** | **147 -> 0** | |

The 4 carousel lines that differed before are case forms (an all-caps heading with a hyphen or dash). The measure a
layout fits with now equals the drawn kerned advance to 0.016 px (1.25 px before, where `case` was missing).

Per-font features (fontTools): Anton `smcp c2sc case cpsp kern liga` (no `tnum`); Montserrat `smcp c2sc case tnum
pnum onum lnum kern liga`; Cinzel `kern calt onum` (no `case`, no `smcp`); IBM Plex Mono no `kern` (fixed pitch).
Capitals drawn with `case` by default: Anton and Montserrat false -> true.

Caption width parity: the composer's own `build_subtitles` output for the five committed fixture runs in both formats
(10 ASS files, with a reconstruction badge) is byte-identical to the base (measure.py hashes each file); a decomposed
accent
("Königsberg Kraków") measured 87.25 px wider than the composed spelling at the 16:9 size and now 0.
tests/test_qf21_opentype.py renders it through libass: the plate now fits the ink libass draws; a second libass
test draws fi and fl runs in the 16:9 style and checks that libass forms no ligature and the measure agrees.

## Evidence renders

`render_evidence.py --code-root <tree> --data-root <checkout> --out <dir>` drew every freeze-scope design that a real
run can feed, once from the base tree and once from this one (network blocked); the chapter, myth and hook cards came
from the freeze-panel cards script (docs/reviews/freeze-panel-2026-11-04/cards/render_evidence.py, its own
--code-root). `compare_evidence.py` counted the changed pixels (more than 8/255 in a channel) and drew the boards in
`renders/`: both frames at half size, then each changed text line, before over after, enlarged up to 2x. Every
count is in `evidence_diff.json`.

| Design (real inputs) | Renders | Changed | What changed |
|---|---:|---:|---|
| Reel cover, grid tile, YouTube thumbnail (the 5 catalogue covers) | 15 | 15 | masthead and eyebrow kerned; "6-TON" hyphen in its case form |
| IG carousel, FB gallery (3 runs' kept recipes) | 42 | 42 | the carousel header's tracked masthead; the closing slide's kicker |
| Stories (3 runs' kept recipes) | 9 | 9 | kickers and the masthead kerned |
| Loop bridge 9:16 (5 catalogue cases) | 5 | 0 | nothing tracked or all-caps on the held frame (the share line is mixed case) |
| 16:9 end screen (1 real, 3 catalogue "new" crops of a Reel topic's last scene) | 4 | 4 | masthead kerned |
| Maps (3 runs' recorded places, both formats) | 6 | 3 | tracked home-country names kerned; the other 3 posters show no tracked name |
| Number cards (3 runs, both formats) | 6 | 6 | kickers and labels kerned; the range rule follows the hyphen's case form (raised to the figures' centre) |
| Timelines (3 runs, both formats) | 6 | 6 | event labels and span notes kerned |
| Scale (Wieliczka, both formats) | 2 | 2 | the tracked subject kerned |
| Then and now (2 catalogue pairs, both formats) | 4 | 2 | the 16:9 kicker kerned; the 9:16 posters are unchanged |
| Chapter cards (Richat deep dive acts) | 100 | 100 | the tracked CHAPTER eyebrow |
| Myth-vs-fact cards | 157 | 140 | the MYTH / FACT eyebrows |
| Hook frames (+ title-only) | 47 | 22 | tracked labels on 22 frames; the other 25 unchanged |

Not rendered, and why: the route map (the one run with a route, Amber Room 1792801257, needs its stops geocoded
online: refused offline); the Singing Colossi scale (no lineage kept and its payload's scale needs a reference the run
did not record: the render raised on both trees); cross-sections (no run's payload carries one); photo callouts (no
run recorded callout locations: no callout_locate.json exists, and nothing in the composer calls callouts.render
yet); captions (the 10 fixture ASS files are byte-identical to the base, so libass draws the same pixels; the libass
parity is in the tests). Their copy is still in the tracked-copy measure above (route stops, callout labels).
Downstream, all 15 boards inspected (round 2): no line changes its break or size anywhere; every change is a
narrower kerned line or a case form. Per board:
- covers (Amber Room Reel, Tunguska grid, Wieliczka YouTube): the masthead keeps its left edge and is about 6 px
  narrower, so the group is now centred as measured (it ran about 3 px right of centre before); eyebrow and tagline
  narrower; the "6-TON" hyphen in its case form.
- carousel slide 7 and Story 3: masthead and kicker narrower, left edges and rows unchanged.
- timelines: Amber Room 9:16 labels narrower, positions unchanged; Hal Saflieni 16:9: labels narrower, and the 1902
  column (marker, flag, year, label) sits about 5 px further right. The first two markers do not move: only the
  compressed gap before 1902 (drawn with a break mark, not to scale) widens, because the wide layout search
  (`_render_timeline_wide`) fits the blocks by their measured widths and the kerned labels need less room. Intended
  kerning consequence; the to-scale part of the spine is unchanged.
- number card (Aswan): labels and kickers narrower; the range rule follows the case hyphen to the figures' centre.
- map (Richat 16:9): the water name and the home country name narrower around the same centres; pin, labels and
  scale bar in place.
- scale (Wieliczka 9:16): the tracked subject narrower; drawing and rows unchanged.
- then and now (Colossi 16:9): no visible change; 695 px differ in the halo around NOW and 2011 (the kicker is IBM
  Plex Mono, which has no kerning; the year is untracked Anton), placement unchanged.
- end screen (Wieliczka 16:9): masthead narrower; WATCH NEXT and the share line unchanged.
- chapter, myth, hook cards: the tracked CHAPTER / MYTH / FACT eyebrows and the masthead narrower; titles unchanged.

## Tests

`tests/test_qf21_opentype.py` (28 cases): 26 fail on the base and all pass here. Two pass on both: the
variable-weight instance check, and the libass ligature check (the base's letter-sum measure was already
ligature-free; the check guards the shaped measure against libass's pixels). Kerning pairs: three kerned strings in Anton, Montserrat and Cinzel drawn
pixel-identical to the independent kerned layout; each face's strongest capital pair in the tracked measure and in
the callout, end-card and design-card copies of it; the stroked tracked run. Per-font features: the inventory from
the tables; case on capitals only, on every drawing path; true small caps with accents; a no-op `smcp` on Cinzel;
tabular figures; featured plates and twins. Caption width parity: the shaped unkerned measure, the decomposed
accent on libass's own pixels, and fi against fl runs on libass's own pixels.
