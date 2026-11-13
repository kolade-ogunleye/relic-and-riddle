# QF-87: Bebas removed, one Montserrat source, true small capitals, the grid masthead

Row (review section 9.2): Bebas Neue had no caller; Montserrat weight 700 shipped twice; the Cinzel masthead was
about 5 px on a profile-grid tile; libass applies no OpenType feature, so the captions faked small capitals with 82%
`\fscx` capitals. Layout and test patch: decisions QF-24-QF-87-layout (answered 2026-11-09, approve the combined layout
and the v2 main-test patch).

## What changed

- `fonts/`: BebasNeue-Regular.ttf and Montserrat-Bold.ttf removed, with their notices and evidence rows. Every
  remaining file is in `typography.bundled_font_files()` and has one licence record.
- `src/font_assets.py` (new): two caption faces derived locally with fontTools from the bundled files (never
  downloaded or committed), cached under `cache/font_instances/<identity>/` with the source's OFL notice and a
  licence record bound to the source hash (`font_evidence.font_rights` checks it):
  - "RR Caption": Anton with its own `smcp` glyphs as the default forms of its lower case (cmap re-mapped; no outline
    or advance changed);
  - "RR Sans": the static weight-700 instance of Montserrat-Variable.ttf (the 16:9 caption face).
  `build_subtitles` embeds the face in the ASS file (`[Fonts]`), so libass draws it with no installed font.
- `src/video_composer.py`: 9:16 captions keep the narration's case and accents in RR Caption (NFC for display only;
  words, times, SRT and VTT unchanged); no `\fscx` under 100; Bold 0 (Anton has one weight; Bold -1 made libass
  thicken it synthetically, about 10% more ink). The line measure is one shaped run in the caption face with each
  glyph's own ink (`font_engine.run_ink_bounds`), QF-94 face choice kept. The approved bounded two-line layout:
  a complete burst (at most 4 words / 28 characters, bonds kept) too wide for one line at 74 px draws on two lines
  on the 80/74/66 ladder inside the 240 px band; pitch at least 1.30 cap heights and at least 10 px between the two
  lines' measured ink (`CAPTION_TWO_LINE_INK_GAP`); the phrasing pass keeps round-4 sentence boundaries and moves a
  dangling word only within the reading rate.
- `src/cover_design.py`: a grid-delivered cover draws the masthead only at `MASTHEAD_GRID_CAP` (9) px cap height or
  more at 124 px wide; at BRAND_PX it is 5.2 px, so covers have none (`lay.meta["masthead"]` says why). The grid proof
  holds a drawn masthead to 9 px (was 5).
- `src/design_cards.py` (lander ruling 2026-11-13: the rule reaches the Reel's first frame): the hook card draws
  its masthead (name and diamond) only at 9 px at the tile (`hook_masthead_shown`); at 64 px it is 5.2 px, so the
  hook frame has none, its claim block where it was, and the card is never refused for it.
- `scripts/record_font_evidence.py`: one record per bundled file; IBM Plex Mono and the Noto subsets keep their
  recorded evidence (checked before any request); LF output.

## Measurements (`python measure.py <repo root>`; `before.json` on 1a2c3a85, `after.json` on this commit)

| metric | before | after |
|---|---|---|
| font files / unregistered | 9 / 2 (Bebas, Montserrat-Bold) | 7 / 0 |
| Montserrat files | 2 | 1 |
| scaled-capital tags in 7 real 9:16 caption files | 3,860 | 0 |
| narration words drawn in their own case (721) | 35 | 721 |
| bursts / two-line / one-word | 273 / 0 / 8 | 255 / 91 / 3 |
| plates outside the caption band | 0 | 0 |
| "Königsberg" smcp vs default bounds differ, accent drawn (Anton, Montserrat) | yes | yes |
| caption face shapes as Anton + smcp | n/a | yes |
| native libass: precomposed vs decomposed "Königsberg" | 2,548 px differ | identical |
| grid-delivered covers (5 real copies x 3 styles) with a masthead under 9 px | 15 / 15 | 0 (absent) |
| hook frames (3 grounds) with a masthead under 9 px | 3 / 3 | 0 (absent) |

Every word and time is unchanged in all seven runs. The 16:9 documentary captions draw in RR Sans (Bold), embedded.

## Renders (`renders/`, half size; `render_evidence.py`, real dev runs)

- `captions_konigsberg_before_after.jpg`: the Amber Room run's three "Königsberg" bursts over the run's photo.
- `captions_two_line_before_after.jpg`: the first two-line burst of each 9:16 run (same instant on both trees).
- `cover_masthead_before_after.jpg`: the Amber Room cover (badge, minimal) and its 124 px grid tile.
- `targets.json`: the instants drawn.
- `hook_masthead_before_after.jpg` (`render_hook.py`): the hook card's frame 0 on three photos of the Richat dev run
  (the design-card tests' signed Richat claim) and its 124 px tile; `hook_before.json` / `hook_after.json`.
- `captions_16x9_before_after.jpg` (`render_wide.py`): 16:9 captions on the long Richat, Amber Room and Tunguska
  narrations over each run's photo. Every one of nine frames keeps its caption box to the pixel; 217-469 edge pixels
  differ (the RR Sans 700 instance against the former Montserrat-Bold.ttf); `wide_before.json` / `wide_after.json`.

## For the reviewer

- 91 of 255 real bursts draw on two lines (36%). The break is ranked by size first (lander ruling 2026-11-13,
  round 3: a caption never shrinks to keep a name whole), then at that rung by phrase structure: a proper name kept
  whole where a break there allows it (OF / ARMY GROUP NORTH, WHEN / KÖNIGSBERG CASTLE, TO CENTRAL ASIAN /
  TRADITIONS, all at 80), then a line 1 that neither ends on a dangling word nor is one function word alone; NEAR /
  THE PODKAMENNAYA has no other break (THE never ends a line). Production sample (34 Reels, counts only): two-line
  bursts at 66 px 8 (round 2, name first) -> 2; 11 two-line bursts at their top rung split a name where no break
  there keeps it (shape "article + adjective + name | head noun", e.g. THE SECOND WORLD | WAR); none in the dev runs.
- HEAD-regression gate: eight width-forced 9:16 cuts are gone (HEAD's burst draws on two lines); one new cut keeps a
  figure range whole (kind "range": BETWEEN 287 AND 300 KILOMETRES); HEAD's GRAY BASALT MAUSOLEUM is kept (only a
  function-word end now moves a word across a burst boundary). The gate's "width" kind also requires that no
  two-line layout holds HEAD's burst.
- The measure is the shaped outline extent rounded down (it was rounded to nearest): on the ten pinned samples
  libass's ink is 0 to 1 px wider, so REEL_INK_OVER_MEASURE stays (0, 2).
- The covers and the hook frame keep the space where the masthead was (no reflow): a freeze-loop item.
- Not design-freeze acceptance.
